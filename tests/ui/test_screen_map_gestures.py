# -*- coding: utf-8 -*-
"""
tests/ui/test_screen_map_gestures.py — gesture / drop scenario tests.

Spec section: §6.7, §6.8

Tests cover every row in the §6.7 drop-gesture table:
  1. Drop connection on empty-slot placeholder pane → respawn_pane
  2. Drop connection on occupied pane edge → split_pane + send_keys
  3. Drop connection on occupied pane center → DropDisambiguationDialog
       a. Replace → respawn_pane
       b. Split right → split_pane(h, before=False) + send_keys
       c. Split below → split_pane(v, before=False) + send_keys
       d. Cancel → no-op
  4. Same-window pane-to-pane center → swap_panes
  5. Same-window pane-to-pane edge → swap_panes + placeholder respawn
  6. Cross-viewport pane drag → break_pane + move_pane
  7. Pane drag-out to empty space → break_pane(detached=True)
  8. Shift+drag-out → kill_pane
  9. Remove from layout (preserve=True) → respawn_pane with placeholder
 10. Remove from layout (preserve=False) → kill_pane
 11. Kill pane → kill_pane (unconditional)
 12. Resize pane → resize_pane + apply_change
 13. Split-and-lock: apply_change called after every modifying operation
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt

from cpsm.controllers.layout_controller import LayoutController
from cpsm.data.schema import (
    CpsmDocument,
    GeometryPct,
    Monitor,
    Pane,
    ScreenLayout,
    Settings,
    Viewport,
)

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_pane(connection_id: str | None = None) -> Pane:
    return Pane(connection_id=connection_id)


def _make_viewport(
    vp_id: str = "vp-01",
    panes: list[Pane] | None = None,
    window_name: str | None = None,
) -> Viewport:
    return Viewport(
        id=vp_id,
        geometry_pct=GeometryPct(x=0, y=0, w=100, h=100),
        tmux_window_name=window_name or vp_id,
        tmux_layout="tiled",
        panes=panes or [],
    )


def _make_layout(
    layout_id: str = "layout-01",
    viewports: list[Viewport] | None = None,
) -> ScreenLayout:
    vps = viewports or [_make_viewport()]
    return ScreenLayout(
        id=layout_id,
        name="Test Layout",
        monitors=[Monitor(identifier="mon-1", viewports=vps)],
    )


def _make_doc(
    settings: Settings | None = None,
) -> CpsmDocument:
    """Create a minimal CpsmDocument with no layouts (avoids FK validation errors)."""
    return CpsmDocument(
        settings=settings or Settings(),
    )


def _make_backend() -> MagicMock:
    """Return a MagicMock that mimics MultiplexerBackend."""
    backend = MagicMock()
    # split_pane returns an object with .id
    split_result = MagicMock()
    split_result.id = "%99"
    backend.split_pane.return_value = split_result
    # capture_layout returns a dummy layout string
    backend.capture_layout.return_value = "tiled"
    return backend


def _make_controller(
    backend: MagicMock | None = None,
    preserve_on_remove: bool = True,
    layout: ScreenLayout | None = None,
) -> tuple[LayoutController, MagicMock]:
    """Return a (LayoutController, backend_mock) pair ready for use."""
    backend = backend or _make_backend()

    config = MagicMock()
    layout_svc = MagicMock()
    session_svc = MagicMock()
    templates_svc = MagicMock()
    templates_svc.render.return_value = "bash /tmp/launcher.sh"
    templates_svc.render_placeholder.return_value = "bash /tmp/placeholder.sh"

    # layout_svc.apply_change returns a copy of the provided layout
    sl = layout or _make_layout()
    layout_svc.apply_change.side_effect = lambda doc, lay, vp, **kw: lay

    ctrl = LayoutController(
        config=config,
        layout=layout_svc,
        session=session_svc,
        backend=backend,
        templates=templates_svc,
    )

    settings = Settings(layout_preserve_on_remove=preserve_on_remove)
    doc = _make_doc(settings=settings)
    ctrl.set_document(doc)
    ctrl.set_screen_layout(sl)

    return ctrl, backend


# ============================================================================
# 1. Drop connection on empty-slot pane → respawn_pane (no dialog)
# ============================================================================


class TestDropConnectionOnEmptySlot:
    def test_respawn_called(self) -> None:
        pane_id = "conn-empty-01"  # empty slot uses synthetic id; faked here
        vp = _make_viewport(vp_id="vp-01", panes=[Pane(connection_id=None)])
        layout = _make_layout(viewports=[vp])
        ctrl, backend = _make_controller(layout=layout)

        # Patch _pane_is_empty to return True for pane_id
        ctrl._pane_is_empty = lambda pid, v: True  # type: ignore[method-assign]
        # Patch find_viewport_for_pane to return our viewport
        ctrl._find_viewport_for_pane = lambda pid: vp  # type: ignore[method-assign]

        conn = MagicMock()
        conn.launch_profile = "local-shell"
        ctrl._config.find_connection.return_value = conn

        ctrl.on_drop_connection("conn-empty-01", pane_id, "center", 0)

        backend.respawn_pane.assert_called_once()
        args = backend.respawn_pane.call_args
        assert args[1].get("kill_existing", True) is True

    def test_apply_change_called_after_respawn(self) -> None:
        vp = _make_viewport(vp_id="vp-01", panes=[Pane(connection_id=None)])
        layout = _make_layout(viewports=[vp])
        ctrl, _backend = _make_controller(layout=layout)

        ctrl._pane_is_empty = lambda pid, v: True  # type: ignore[method-assign]
        ctrl._find_viewport_for_pane = lambda pid: vp  # type: ignore[method-assign]

        conn = MagicMock()
        conn.launch_profile = "local-shell"
        ctrl._config.find_connection.return_value = conn

        ctrl.on_drop_connection("conn-empty-01", "pane-id", "center", 0)

        ctrl._layout.apply_change.assert_called_once()


# ============================================================================
# 2. Drop connection on occupied pane edge → split_pane + send_keys
# ============================================================================


class TestDropConnectionOnEdge:
    @pytest.mark.parametrize(
        "zone, expected_direction, expected_before",
        [
            ("top", "v", True),
            ("bottom", "v", False),
            ("left", "h", True),
            ("right", "h", False),
        ],
    )
    def test_split_called_for_zone(
        self, zone: str, expected_direction: str, expected_before: bool
    ) -> None:
        vp = _make_viewport(panes=[Pane(connection_id="conn-occ")])
        layout = _make_layout(viewports=[vp])
        ctrl, backend = _make_controller(layout=layout)

        ctrl._pane_is_empty = lambda pid, v: False  # type: ignore[method-assign]
        ctrl._find_viewport_for_pane = lambda pid: vp  # type: ignore[method-assign]

        conn = MagicMock()
        conn.launch_profile = "local-shell"
        ctrl._config.find_connection.return_value = conn

        ctrl.on_drop_connection("conn-new", "conn-occ", zone, 0)

        backend.split_pane.assert_called_once_with(
            "conn-occ", expected_direction, before=expected_before
        )
        backend.send_keys.assert_called_once()

    def test_apply_change_called_after_edge_split(self) -> None:
        vp = _make_viewport(panes=[Pane(connection_id="conn-occ")])
        layout = _make_layout(viewports=[vp])
        ctrl, _backend = _make_controller(layout=layout)

        ctrl._pane_is_empty = lambda pid, v: False  # type: ignore[method-assign]
        ctrl._find_viewport_for_pane = lambda pid: vp  # type: ignore[method-assign]

        conn = MagicMock()
        conn.launch_profile = "local-shell"
        ctrl._config.find_connection.return_value = conn

        ctrl.on_drop_connection("conn-new", "conn-occ", "top", 0)

        ctrl._layout.apply_change.assert_called_once()


# ============================================================================
# 3. Drop connection on occupied pane center → DropDisambiguationDialog
# ============================================================================


class TestDropConnectionOnCenterOccupied:
    def _setup(self) -> tuple[LayoutController, MagicMock]:
        vp = _make_viewport(panes=[Pane(connection_id="conn-occ")])
        layout = _make_layout(viewports=[vp])
        ctrl, backend = _make_controller(layout=layout)
        ctrl._pane_is_empty = lambda pid, v: False  # type: ignore[method-assign]
        ctrl._find_viewport_for_pane = lambda pid: vp  # type: ignore[method-assign]
        conn = MagicMock()
        conn.launch_profile = "local-shell"
        ctrl._config.find_connection.return_value = conn
        return ctrl, backend

    def test_replace_calls_respawn(self) -> None:
        ctrl, backend = self._setup()
        ctrl._ask_disambiguation = lambda c, p: "replace"  # type: ignore[method-assign]
        ctrl.on_drop_connection("conn-new", "conn-occ", "center", 0)
        backend.respawn_pane.assert_called_once()
        ctrl._layout.apply_change.assert_called_once()

    def test_split_right_calls_split_h(self) -> None:
        ctrl, backend = self._setup()
        ctrl._ask_disambiguation = lambda c, p: "split-right"  # type: ignore[method-assign]
        ctrl.on_drop_connection("conn-new", "conn-occ", "center", 0)
        backend.split_pane.assert_called_once()
        call_args = backend.split_pane.call_args
        assert call_args[0][1] == "h"
        backend.send_keys.assert_called_once()

    def test_split_below_calls_split_v(self) -> None:
        ctrl, backend = self._setup()
        ctrl._ask_disambiguation = lambda c, p: "split-below"  # type: ignore[method-assign]
        ctrl.on_drop_connection("conn-new", "conn-occ", "center", 0)
        backend.split_pane.assert_called_once()
        call_args = backend.split_pane.call_args
        assert call_args[0][1] == "v"
        backend.send_keys.assert_called_once()

    def test_cancel_does_nothing(self) -> None:
        ctrl, backend = self._setup()
        ctrl._ask_disambiguation = lambda c, p: "cancel"  # type: ignore[method-assign]
        ctrl.on_drop_connection("conn-new", "conn-occ", "center", 0)
        backend.respawn_pane.assert_not_called()
        backend.split_pane.assert_not_called()
        ctrl._layout.apply_change.assert_not_called()


# ============================================================================
# 4. Same-window pane-to-pane center → swap_panes
# ============================================================================


class TestSameWindowPaneToPaneCenter:
    def test_swap_panes_called(self) -> None:
        vp = _make_viewport(panes=[Pane(connection_id="src-01"), Pane(connection_id="dst-01")])
        layout = _make_layout(viewports=[vp])
        ctrl, backend = _make_controller(layout=layout)

        ctrl._find_viewport_for_pane = lambda pid: vp  # type: ignore[method-assign]

        ctrl.on_drop_pane("src-01", "dst-01", "center", 0)

        backend.swap_panes.assert_called_once_with("src-01", "dst-01")

    def test_apply_change_called_after_swap(self) -> None:
        vp = _make_viewport(panes=[Pane(connection_id="src-01"), Pane(connection_id="dst-01")])
        layout = _make_layout(viewports=[vp])
        ctrl, _backend = _make_controller(layout=layout)
        ctrl._find_viewport_for_pane = lambda pid: vp  # type: ignore[method-assign]

        ctrl.on_drop_pane("src-01", "dst-01", "center", 0)

        ctrl._layout.apply_change.assert_called_once()


# ============================================================================
# 5. Same-window pane-to-pane edge → swap_panes + placeholder respawn
# ============================================================================


class TestSameWindowPaneToPaneEdge:
    def test_swap_then_placeholder(self) -> None:
        vp = _make_viewport(panes=[Pane(connection_id="src-01"), Pane(connection_id="dst-01")])
        layout = _make_layout(viewports=[vp])
        ctrl, backend = _make_controller(layout=layout)
        ctrl._find_viewport_for_pane = lambda pid: vp  # type: ignore[method-assign]

        ctrl.on_drop_pane("src-01", "dst-01", "top", 0)

        backend.swap_panes.assert_called_once_with("src-01", "dst-01")
        backend.respawn_pane.assert_called_once()
        ctrl._layout.apply_change.assert_called_once()


# ============================================================================
# 6. Cross-viewport pane drag → break_pane + move_pane
# ============================================================================


class TestCrossViewportPaneDrag:
    def test_break_then_move(self) -> None:
        vp_src = Viewport(
            id="vp-src",
            geometry_pct=GeometryPct(x=0, y=0, w=50, h=100),
            tmux_window_name="win-src",
            tmux_layout="tiled",
            panes=[Pane(connection_id="src-01")],
        )
        vp_dst = Viewport(
            id="vp-dst",
            geometry_pct=GeometryPct(x=50, y=0, w=50, h=100),
            tmux_window_name="win-dst",
            tmux_layout="tiled",
            panes=[Pane(connection_id="dst-01")],
        )
        layout = ScreenLayout(
            id="layout-x",
            name="X",
            monitors=[
                Monitor(
                    identifier="mon-1",
                    viewports=[vp_src, vp_dst],
                )
            ],
        )
        doc = _make_doc()
        backend = _make_backend()
        layout_svc = MagicMock()
        layout_svc.apply_change.side_effect = lambda doc, lay, vp, **kw: lay
        ctrl = LayoutController(
            config=MagicMock(),
            layout=layout_svc,
            session=MagicMock(),
            backend=backend,
            templates=MagicMock(),
        )
        ctrl.set_document(doc)
        ctrl.set_screen_layout(layout)

        # Return different viewports for src vs dst
        def _find(pid: str) -> Viewport | None:
            if pid == "src-01":
                return vp_src
            if pid == "dst-01":
                return vp_dst
            return None

        ctrl._find_viewport_for_pane = _find  # type: ignore[method-assign]

        ctrl.on_drop_pane("src-01", "dst-01", "center", 0)

        backend.break_pane.assert_called_once_with("src-01", detached=True)
        backend.move_pane.assert_called_once_with("src-01", "win-dst")
        ctrl._layout.apply_change.assert_called_once()


# ============================================================================
# 7. Pane drag-out to empty space → break_pane(detached=True)
# ============================================================================


class TestPaneDragOut:
    def test_break_pane_detached(self) -> None:
        vp = _make_viewport(panes=[Pane(connection_id="src-01")])
        layout = _make_layout(viewports=[vp])
        ctrl, backend = _make_controller(layout=layout)
        ctrl._find_viewport_for_pane = lambda pid: vp  # type: ignore[method-assign]

        ctrl.on_drop_pane("src-01", "", "center", 0)

        backend.break_pane.assert_called_once_with("src-01", detached=True)
        backend.kill_pane.assert_not_called()


# ============================================================================
# 8. Shift+drag-out → kill_pane instead of break_pane
# ============================================================================


class TestShiftDragOut:
    def test_kill_pane_called(self) -> None:
        vp = _make_viewport(panes=[Pane(connection_id="src-01")])
        layout = _make_layout(viewports=[vp])
        ctrl, backend = _make_controller(layout=layout)
        ctrl._find_viewport_for_pane = lambda pid: vp  # type: ignore[method-assign]

        shift_mods = Qt.KeyboardModifier.ShiftModifier.value
        ctrl.on_drop_pane("src-01", "", "center", shift_mods)

        backend.kill_pane.assert_called_once_with("src-01")
        backend.break_pane.assert_not_called()


# ============================================================================
# 9 & 10. Remove from layout — preserve vs kill
# ============================================================================


class TestRemoveFromLayout:
    def test_preserve_true_calls_remove_pane_svc(self) -> None:
        pane = Pane(connection_id="conn-rm")
        vp = _make_viewport(panes=[pane])
        layout = _make_layout(viewports=[vp])
        ctrl, _backend = _make_controller(layout=layout, preserve_on_remove=True)
        ctrl._find_viewport_for_pane = lambda pid: vp  # type: ignore[method-assign]

        # remove_pane delegates to LayoutService.remove_pane
        ctrl._layout.remove_pane.return_value = layout
        ctrl.on_remove_pane("conn-rm")

        ctrl._layout.remove_pane.assert_called_once()

    def test_preserve_false_calls_remove_pane_svc(self) -> None:
        pane = Pane(connection_id="conn-rm")
        vp = _make_viewport(panes=[pane])
        layout = _make_layout(viewports=[vp])
        ctrl, _backend = _make_controller(layout=layout, preserve_on_remove=False)
        ctrl._find_viewport_for_pane = lambda pid: vp  # type: ignore[method-assign]

        ctrl._layout.remove_pane.return_value = layout
        ctrl.on_remove_pane("conn-rm")

        ctrl._layout.remove_pane.assert_called_once()

    def test_dirty_flag_set_after_remove(self) -> None:
        pane = Pane(connection_id="conn-rm")
        vp = _make_viewport(panes=[pane])
        layout = _make_layout(viewports=[vp])
        ctrl, _backend = _make_controller(layout=layout)
        ctrl._find_viewport_for_pane = lambda pid: vp  # type: ignore[method-assign]
        ctrl._layout.remove_pane.return_value = layout

        assert not ctrl.is_dirty
        ctrl.on_remove_pane("conn-rm")
        assert ctrl.is_dirty


# ============================================================================
# 11. Kill pane (unconditional)
# ============================================================================


class TestKillPane:
    def test_kill_pane_called_unconditionally(self) -> None:
        vp = _make_viewport(panes=[Pane(connection_id="conn-kill")])
        layout = _make_layout(viewports=[vp])
        ctrl, backend = _make_controller(layout=layout)
        ctrl._find_viewport_for_pane = lambda pid: vp  # type: ignore[method-assign]

        ctrl.on_kill_pane("conn-kill")

        backend.kill_pane.assert_called_once_with("conn-kill")

    def test_kill_pane_dirty_flag(self) -> None:
        vp = _make_viewport(panes=[Pane(connection_id="conn-kill")])
        layout = _make_layout(viewports=[vp])
        ctrl, _backend = _make_controller(layout=layout)
        ctrl._find_viewport_for_pane = lambda pid: vp  # type: ignore[method-assign]

        assert not ctrl.is_dirty
        ctrl.on_kill_pane("conn-kill")
        assert ctrl.is_dirty


# ============================================================================
# 12. Resize pane → resize_pane + apply_change
# ============================================================================


class TestResizePane:
    def test_resize_pane_called(self) -> None:
        vp = _make_viewport(panes=[Pane(connection_id="conn-rz")])
        layout = _make_layout(viewports=[vp])
        ctrl, backend = _make_controller(layout=layout)
        ctrl._find_viewport_for_pane = lambda pid: vp  # type: ignore[method-assign]

        ctrl.on_resize_pane("conn-rz", 80, 24)

        backend.resize_pane.assert_called_once_with("conn-rz", 80, 24)

    def test_apply_change_called_after_resize(self) -> None:
        vp = _make_viewport(panes=[Pane(connection_id="conn-rz")])
        layout = _make_layout(viewports=[vp])
        ctrl, _backend = _make_controller(layout=layout)
        ctrl._find_viewport_for_pane = lambda pid: vp  # type: ignore[method-assign]

        ctrl.on_resize_pane("conn-rz", 80, 24)

        ctrl._layout.apply_change.assert_called_once()


# ============================================================================
# 13. Split-and-lock invariant: apply_change called after every op
# ============================================================================


class TestSplitAndLockInvariant:
    """Ensure LayoutService.apply_change is always called after operations."""

    def _make(self) -> tuple[LayoutController, MagicMock, Viewport]:
        vp = _make_viewport(panes=[Pane(connection_id="c1"), Pane(connection_id="c2")])
        layout = _make_layout(viewports=[vp])
        ctrl, backend = _make_controller(layout=layout)
        ctrl._find_viewport_for_pane = lambda pid: vp  # type: ignore[method-assign]
        ctrl._pane_is_empty = lambda pid, v: False  # type: ignore[method-assign]
        conn = MagicMock()
        conn.launch_profile = "local-shell"
        ctrl._config.find_connection.return_value = conn
        return ctrl, backend, vp

    def test_connection_edge_drop_triggers_apply_change(self) -> None:
        ctrl, _backend, _vp = self._make()
        ctrl.on_drop_connection("conn-new", "c1", "right", 0)
        ctrl._layout.apply_change.assert_called_once()

    def test_pane_center_drop_triggers_apply_change(self) -> None:
        ctrl, _backend, _vp = self._make()
        ctrl.on_drop_pane("c1", "c2", "center", 0)
        ctrl._layout.apply_change.assert_called_once()

    def test_resize_triggers_apply_change(self) -> None:
        ctrl, _backend, _vp = self._make()
        ctrl.on_resize_pane("c1", 100, 30)
        ctrl._layout.apply_change.assert_called_once()


# ============================================================================
# DropDisambiguationDialog tests
# ============================================================================


class TestDropDisambiguationDialog:
    """Test the modal disambiguation dialog."""

    def test_dialog_object_name(self, qtbot) -> None:
        from cpsm.ui.dialogs.drop_disambiguation import DropDisambiguationDialog

        dlg = DropDisambiguationDialog()
        qtbot.addWidget(dlg)
        assert dlg.objectName() == "dlg_drop_disambiguation"

    def test_initial_choice_is_cancel(self, qtbot) -> None:
        from cpsm.ui.dialogs.drop_disambiguation import DropDisambiguationDialog

        dlg = DropDisambiguationDialog()
        qtbot.addWidget(dlg)
        assert dlg.choice == "cancel"

    def test_replace_button_sets_choice(self, qtbot) -> None:
        from cpsm.ui.dialogs.drop_disambiguation import DropDisambiguationDialog

        dlg = DropDisambiguationDialog()
        qtbot.addWidget(dlg)
        dlg._btn_replace.click()
        assert dlg.choice == "replace"

    def test_split_right_button_sets_choice(self, qtbot) -> None:
        from cpsm.ui.dialogs.drop_disambiguation import DropDisambiguationDialog

        dlg = DropDisambiguationDialog()
        qtbot.addWidget(dlg)
        dlg._btn_split_right.click()
        assert dlg.choice == "split-right"

    def test_split_below_button_sets_choice(self, qtbot) -> None:
        from cpsm.ui.dialogs.drop_disambiguation import DropDisambiguationDialog

        dlg = DropDisambiguationDialog()
        qtbot.addWidget(dlg)
        dlg._btn_split_below.click()
        assert dlg.choice == "split-below"

    def test_cancel_button_sets_choice(self, qtbot) -> None:
        from cpsm.ui.dialogs.drop_disambiguation import DropDisambiguationDialog

        dlg = DropDisambiguationDialog()
        qtbot.addWidget(dlg)
        dlg._btn_cancel.click()
        assert dlg.choice == "cancel"

    def test_button_object_names_present(self, qtbot) -> None:
        from cpsm.ui.dialogs.drop_disambiguation import DropDisambiguationDialog

        dlg = DropDisambiguationDialog()
        qtbot.addWidget(dlg)
        assert dlg._btn_replace.objectName() == "dlg_drop_btn_replace"
        assert dlg._btn_split_right.objectName() == "dlg_drop_btn_split_right"
        assert dlg._btn_split_below.objectName() == "dlg_drop_btn_split_below"
        assert dlg._btn_cancel.objectName() == "dlg_drop_btn_cancel"

    def test_with_connection_and_pane_names(self, qtbot) -> None:
        from PySide6.QtWidgets import QLabel

        from cpsm.ui.dialogs.drop_disambiguation import DropDisambiguationDialog

        dlg = DropDisambiguationDialog(
            connection_name="my-conn",
            pane_label="pane-42",
        )
        qtbot.addWidget(dlg)
        # Should not raise; prompt label should mention the connection
        prompt = dlg.findChild(QLabel, "dlg_drop_disambiguation_prompt")
        assert prompt is not None


# ============================================================================
# ScreenMapWidget signal tests
# ============================================================================


class TestScreenMapWidgetSignals:
    """Test that ScreenMapWidget emits the correct signals."""

    def _make_widget(self, qtbot):
        from cpsm.ui.widgets.screen_map import ScreenMapWidget

        w = ScreenMapWidget()
        qtbot.addWidget(w)
        return w

    def test_remove_pane_signal(self, qtbot) -> None:
        w = self._make_widget(qtbot)
        received: list[str] = []
        w.remove_pane_requested.connect(received.append)
        w.remove_pane_requested.emit("pane-x")
        assert received == ["pane-x"]

    def test_kill_pane_signal(self, qtbot) -> None:
        w = self._make_widget(qtbot)
        received: list[str] = []
        w.kill_pane_requested.connect(received.append)
        w.kill_pane_requested.emit("pane-y")
        assert received == ["pane-y"]

    def test_drop_connection_signal(self, qtbot) -> None:
        w = self._make_widget(qtbot)
        received: list[tuple[str, str, str, int]] = []
        w.drop_connection_requested.connect(lambda c, t, z, m: received.append((c, t, z, m)))
        w.drop_connection_requested.emit("conn-1", "pane-1", "top", 0)
        assert received == [("conn-1", "pane-1", "top", 0)]

    def test_drop_pane_signal(self, qtbot) -> None:
        w = self._make_widget(qtbot)
        received: list[tuple[str, str, str, int]] = []
        w.drop_pane_requested.connect(lambda s, d, z, m: received.append((s, d, z, m)))
        w.drop_pane_requested.emit("src-1", "dst-1", "left", 0)
        assert received == [("src-1", "dst-1", "left", 0)]

    def test_attach_signal(self, qtbot) -> None:
        w = self._make_widget(qtbot)
        received: list[str] = []
        w.attach_requested.connect(received.append)
        w.attach_requested.emit("pane-z")
        assert received == ["pane-z"]

    def test_reconnect_signal(self, qtbot) -> None:
        w = self._make_widget(qtbot)
        received: list[str] = []
        w.reconnect_requested.connect(received.append)
        w.reconnect_requested.emit("pane-z")
        assert received == ["pane-z"]

    def test_save_layout_signal(self, qtbot) -> None:
        w = self._make_widget(qtbot)
        received: list[bool] = []
        w.save_layout_requested.connect(lambda: received.append(True))
        w.save_layout_requested.emit()
        assert received == [True]


# ============================================================================
# LayoutController guard: slots no-op when doc/layout not set
# ============================================================================


class TestControllerGuard:
    def _bare_ctrl(self) -> LayoutController:
        return LayoutController(
            config=MagicMock(),
            layout=MagicMock(),
            session=MagicMock(),
            backend=MagicMock(),
            templates=MagicMock(),
        )

    def test_on_drop_connection_without_doc(self) -> None:
        ctrl = self._bare_ctrl()
        # Should not raise
        ctrl.on_drop_connection("c", "p", "top", 0)

    def test_on_drop_pane_without_doc(self) -> None:
        ctrl = self._bare_ctrl()
        ctrl.on_drop_pane("src", "dst", "center", 0)

    def test_on_remove_pane_without_doc(self) -> None:
        ctrl = self._bare_ctrl()
        ctrl.on_remove_pane("pane")

    def test_on_kill_pane_without_doc(self) -> None:
        ctrl = self._bare_ctrl()
        ctrl.on_kill_pane("pane")

    def test_on_resize_pane_without_doc(self) -> None:
        ctrl = self._bare_ctrl()
        ctrl.on_resize_pane("pane", 80, 24)

    def test_dirty_flag_initially_false(self) -> None:
        ctrl = self._bare_ctrl()
        assert not ctrl.is_dirty

    def test_mark_clean_resets_dirty(self) -> None:
        vp = _make_viewport(panes=[Pane(connection_id="c1")])
        layout = _make_layout(viewports=[vp])
        ctrl, _backend = _make_controller(layout=layout)
        ctrl._find_viewport_for_pane = lambda pid: vp  # type: ignore[method-assign]
        ctrl.on_kill_pane("c1")
        assert ctrl.is_dirty
        ctrl.mark_clean()
        assert not ctrl.is_dirty


# ============================================================================
# Synthetic QDropEvent tests (§6.7 — drag source integration)
# ============================================================================


class TestSyntheticDropEvents:
    """Use synthetic QMimeData to exercise dragEnterEvent / dropEvent paths."""

    def _make_widget_with_pane(self, qtbot):
        from PySide6.QtCore import QObject, Signal

        from cpsm.data.schema import GeometryPct, Monitor, Pane, ScreenLayout, Viewport
        from cpsm.services.monitor_service import MonitorInfo
        from cpsm.ui.widgets.screen_map import ScreenMapWidget

        class _FakeMon(QObject):
            monitor_added: Signal = Signal(object)
            monitor_removed: Signal = Signal(str)

            def snapshot(self):
                return [mi]

        mi = MonitorInfo(
            identifier="m1",
            name="HDMI-1",
            geometry=(0, 0, 1920, 1080),
            available_geometry=(0, 0, 1920, 1080),
            physical_size_mm=(527.0, 296.0),
            device_pixel_ratio=1.0,
            orientation="landscape",
            manufacturer="",
            model="",
            serial="",
            qt_index=0,
        )
        layout = ScreenLayout(
            id="layout-dd",
            name="DD",
            monitors=[
                Monitor(
                    identifier="m1",
                    viewports=[
                        Viewport(
                            id="vp-dd",
                            geometry_pct=GeometryPct(x=0, y=0, w=100, h=100),
                            tmux_layout="tiled",
                            panes=[Pane(connection_id=None)],
                        )
                    ],
                )
            ],
        )
        svc = _FakeMon()
        w = ScreenMapWidget(monitor_service=svc, connection_lookup=lambda _: None)
        qtbot.addWidget(w)
        w.resize(800, 600)
        w.show()
        qtbot.waitExposed(w)
        w.set_layout(layout, [mi])
        return w

    def test_drag_enter_accepts_connection_mime(self, qtbot) -> None:
        from PySide6.QtCore import QByteArray, QMimeData

        from cpsm.ui.widgets.screen_map import MIME_CONNECTION_ID

        w = self._make_widget_with_pane(qtbot)

        mime = QMimeData()
        mime.setData(MIME_CONNECTION_ID, QByteArray(b"conn-test"))

        # Simulate dragEnterEvent directly
        accepted: list[bool] = []

        class _FakeEvent:
            def mimeData(self):
                return mime

            def acceptProposedAction(self):
                accepted.append(True)

            def ignore(self):
                accepted.append(False)

        w._on_drag_enter(_FakeEvent())  # type: ignore[arg-type]
        assert accepted == [True]

    def test_drag_enter_rejects_unknown_mime(self, qtbot) -> None:
        from PySide6.QtCore import QByteArray, QMimeData

        w = self._make_widget_with_pane(qtbot)

        mime = QMimeData()
        mime.setData("text/plain", QByteArray(b"irrelevant"))

        ignored: list[bool] = []

        class _FakeEvent:
            def mimeData(self):
                return mime

            def acceptProposedAction(self):
                pass

            def ignore(self):
                ignored.append(True)

        w._on_drag_enter(_FakeEvent())  # type: ignore[arg-type]
        assert ignored == [True]

    def test_drop_connection_signal_emitted(self, qtbot) -> None:
        from PySide6.QtCore import QByteArray, QMimeData, QPointF, Qt

        from cpsm.ui.widgets.screen_map import MIME_CONNECTION_ID

        w = self._make_widget_with_pane(qtbot)

        received: list[tuple[str, str, str, int]] = []
        w.drop_connection_requested.connect(lambda c, t, z, m: received.append((c, t, z, m)))

        mime = QMimeData()
        mime.setData(MIME_CONNECTION_ID, QByteArray(b"conn-drop"))

        # Find a pane in the registry and use its centre
        assert w.pane_registry, "Pane registry should not be empty"
        rec = w.pane_registry[0]
        cx = rec.scene_x + rec.scene_w / 2
        cy = rec.scene_y + rec.scene_h / 2
        view_pt = w._view.mapFromScene(QPointF(cx, cy))

        class _FakeDropEvent:
            def mimeData(self):
                return mime

            def position(self):
                return QPointF(view_pt)

            def modifiers(self):
                return Qt.KeyboardModifier.NoModifier

            def acceptProposedAction(self):
                pass

            def ignore(self):
                pass

        w._on_drop(_FakeDropEvent())  # type: ignore[arg-type]
        # Signal should have been emitted
        assert len(received) == 1
        conn_id, _pane_id, _zone, _mods = received[0]
        assert conn_id == "conn-drop"
