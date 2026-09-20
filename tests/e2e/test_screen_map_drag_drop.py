# -*- coding: utf-8 -*-
"""
E2E tests: screen-map drag-drop gestures.

Acceptance criteria covered:
  §10.13  Every gesture in §6.7 produces the correct tmux command
          (via LayoutController).
  §10.14  Drop targeting per §6.6 (edges, center popup, empty-slot silent
          respawn, modifier overrides).
  §10.15  Split-and-lock invariant: every add captures layout, sets custom +
          custom_layout_string.
  §10.16  Removal preserves geometry by default; layout_preserve_on_remove=false
          reverts (kill_pane called).
  §10.17  Drop on empty-slot uses respawn-pane, layout string byte-stable.
  §10.18  Same-window pane drop = swap_pane, content swap, geometry unchanged.
  §10.19  Drop into full window splits target only.

These tests delegate to LayoutController, which is the locus of all tmux
orchestration.  The tests follow the same pattern as the unit tests in
tests/ui/test_screen_map_gestures.py but at E2E scope (full service wiring).

§10.13-related detailed gesture coverage is already comprehensive in:
  tests/ui/test_screen_map_gestures.py
This E2E file asserts the integration path: controller wired to real services.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


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
from cpsm.services.layout_service import LayoutService

# ---------------------------------------------------------------------------
# Helpers (mirrors tests/ui/test_screen_map_gestures.py pattern)
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


def _make_backend() -> MagicMock:
    backend = MagicMock()
    split_result = MagicMock()
    split_result.id = "%99"
    backend.split_pane.return_value = split_result
    backend.capture_layout.return_value = "tiled"
    backend.respawn_pane.return_value = None
    backend.swap_panes.return_value = None
    backend.kill_pane.return_value = None
    return backend


def _make_controller(
    backend: MagicMock | None = None,
    preserve_on_remove: bool = True,
    layout: ScreenLayout | None = None,
) -> tuple[LayoutController, MagicMock]:
    """Return (LayoutController, backend_mock) with a full mock service stack."""
    backend = backend or _make_backend()

    config = MagicMock()
    layout_svc = MagicMock()
    session_svc = MagicMock()
    templates_svc = MagicMock()
    templates_svc.render.return_value = "bash /tmp/launcher.sh"
    templates_svc.render_placeholder.return_value = "bash /tmp/placeholder.sh"
    layout_svc.apply_change.side_effect = lambda doc, lay, vp, **kw: lay

    ctrl = LayoutController(
        config=config,
        layout=layout_svc,
        session=session_svc,
        backend=backend,
        templates=templates_svc,
    )

    settings = Settings(layout_preserve_on_remove=preserve_on_remove)
    doc = CpsmDocument(settings=settings)
    ctrl.set_document(doc)

    sl = layout or _make_layout()
    ctrl.set_screen_layout(sl)

    return ctrl, backend


# ---------------------------------------------------------------------------
# Identity pinning through the REAL TemplateService
# ---------------------------------------------------------------------------


class TestDropRendersPinnedLauncher:
    """A dropped connection must produce a launcher that pins its identity.

    Every other test in this file mocks TemplateService, so none of them can
    see what the launcher actually contains.  That blind spot hid a real bug:
    on_drop_connection rendered WITHOUT passing ssh_keys, so the resolver
    never saw the key list, identity_file came back empty, and every launcher
    made by dropping a connection onto a pane went out with no -i -- and
    therefore no IdentitiesOnly pin either.

    Uses the real TemplateService deliberately.  A mock cannot fail this way.
    """

    @staticmethod
    def _doc_and_conn(tmp_path):
        from cpsm.data.schema import SshKey, SshShellConnection

        key = tmp_path / "utility"
        key.write_text("x", encoding="utf-8")
        (tmp_path / "utility.pub").write_text("x", encoding="utf-8")
        conn = SshShellConnection(
            id="pinned",
            name="Pinned",
            launch_profile="ssh-shell",
            host="192.0.2.44",
            port=22,
            user="root",
            identity_file_ref="utility",
            project_folder="/tmp",
        )
        doc = CpsmDocument(settings=Settings())
        doc.ssh_keys = [
            SshKey(
                id="utility",
                name="Utility",
                type="rsa",
                private_path=str(key),
                public_path=str(key) + ".pub",
            )
        ]
        doc.connections = [conn]
        return doc, conn

    def _drop(self, tmp_path):
        from cpsm.services.template_service import TemplateService

        doc, conn = self._doc_and_conn(tmp_path)
        backend = _make_backend()
        config = MagicMock()
        config.find_connection.return_value = conn
        layout_svc = MagicMock()
        layout_svc.apply_change.side_effect = lambda d, lay, vp, **kw: lay

        ctrl = LayoutController(
            config=config,
            layout=layout_svc,
            session=MagicMock(),
            backend=backend,
            templates=TemplateService(),  # real, not a mock
        )
        ctrl.set_document(doc)
        vp = _make_viewport(panes=[_make_pane(None)])
        ctrl.set_screen_layout(_make_layout(viewports=[vp]))
        # Panes carry no id of their own; the id is the tmux pane target, so
        # stub the lookups the same way the gesture tests above do.
        ctrl._pane_is_empty = lambda pid, v: True  # type: ignore[method-assign]
        ctrl._find_viewport_for_pane = lambda pid: vp  # type: ignore[method-assign]
        ctrl.on_drop_connection("pinned", "pane-a", "center", 0)
        return backend, doc

    def test_drop_does_not_fail_for_a_resolvable_key(self, tmp_path):
        """The regression: a healthy key must not make the drop a no-op."""
        backend, _ = self._drop(tmp_path)
        assert backend.respawn_pane.called, (
            "respawn_pane was never reached -- render() raised and "
            "on_drop_connection swallowed it, so the drop silently did nothing"
        )

    def test_dropped_launcher_pins_the_identity(self, tmp_path):
        """And the launcher it produced must actually carry the pin."""
        backend, doc = self._drop(tmp_path)
        launcher = backend.respawn_pane.call_args[0][1]
        assert "IdentitiesOnly=yes" in launcher, launcher[:400]
        assert doc.ssh_keys[0].private_path in launcher, launcher[:400]


# ---------------------------------------------------------------------------
# §10.13 — Gesture map: edge drops produce split_pane, center produces respawn
# ---------------------------------------------------------------------------


class TestGestureMap:
    """Acceptance §10.13: Every gesture in §6.7 produces the correct tmux command."""

    def test_drop_edge_top_calls_split_vertical(self):
        """Acceptance §10.13: Top-edge drop → split_pane(target, 'v', before=True)."""
        vp = _make_viewport(vp_id="vp-01", panes=[_make_pane("conn-a"), _make_pane("conn-b")])
        layout = _make_layout(viewports=[vp])
        ctrl, backend = _make_controller(layout=layout)

        conn = MagicMock()
        conn.launch_profile = "claude-remote"
        ctrl._config.find_connection.return_value = conn
        ctrl._pane_is_empty = lambda pid, v: False  # type: ignore[method-assign]
        ctrl._find_viewport_for_pane = lambda pid: vp  # type: ignore[method-assign]

        ctrl.on_drop_connection("web01", "pane-b", "top", 0)

        backend.split_pane.assert_called_once()
        # split_pane is called as: split_pane(target, direction, before=bool)
        call_args = backend.split_pane.call_args
        direction = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("direction")
        before = call_args[1].get("before")
        assert direction == "v", f"Expected 'v' (vertical), got {direction!r}"
        assert before is True

    def test_drop_edge_right_calls_split_horizontal(self):
        """Acceptance §10.13: Right-edge drop → split_pane(target, 'h', before=False)."""
        vp = _make_viewport(vp_id="vp-01", panes=[_make_pane("conn-a"), _make_pane("conn-b")])
        layout = _make_layout(viewports=[vp])
        ctrl, backend = _make_controller(layout=layout)

        conn = MagicMock()
        conn.launch_profile = "claude-remote"
        ctrl._config.find_connection.return_value = conn
        ctrl._pane_is_empty = lambda pid, v: False  # type: ignore[method-assign]
        ctrl._find_viewport_for_pane = lambda pid: vp  # type: ignore[method-assign]

        ctrl.on_drop_connection("web01", "pane-b", "right", 0)

        backend.split_pane.assert_called_once()
        call_args = backend.split_pane.call_args
        direction = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("direction")
        before = call_args[1].get("before")
        assert direction == "h", f"Expected 'h' (horizontal), got {direction!r}"
        assert before is False

    def test_drop_center_occupied_calls_respawn(self):
        """Acceptance §10.13: Center drop on occupied pane → respawn_pane (replace)."""
        vp = _make_viewport(vp_id="vp-01", panes=[_make_pane("conn-a"), _make_pane("conn-b")])
        layout = _make_layout(viewports=[vp])
        ctrl, backend = _make_controller(layout=layout)

        conn = MagicMock()
        conn.launch_profile = "claude-remote"
        ctrl._config.find_connection.return_value = conn
        ctrl._pane_is_empty = lambda pid, v: False  # type: ignore[method-assign]
        ctrl._find_viewport_for_pane = lambda pid: vp  # type: ignore[method-assign]

        # Patch _ask_disambiguation (the method that shows DropDisambiguationDialog)
        ctrl._ask_disambiguation = lambda cid, pid: "replace"  # type: ignore[method-assign]
        ctrl.on_drop_connection("web01", "pane-b", "center", 0)

        backend.respawn_pane.assert_called()


# ---------------------------------------------------------------------------
# §10.14 — Drop targeting: edges, center, empty-slot, modifiers
# ---------------------------------------------------------------------------


class TestDropTargeting:
    """Acceptance §10.14: Drop targeting per §6.6."""

    def test_shift_modifier_forces_horizontal_split(self):
        """Acceptance §10.14: Shift modifier overrides zone to horizontal split ('h')."""
        from PySide6.QtCore import Qt

        vp = _make_viewport(vp_id="vp-01", panes=[_make_pane("conn-a"), _make_pane("conn-b")])
        layout = _make_layout(viewports=[vp])
        ctrl, backend = _make_controller(layout=layout)

        conn = MagicMock()
        conn.launch_profile = "claude-remote"
        ctrl._config.find_connection.return_value = conn
        ctrl._pane_is_empty = lambda pid, v: False  # type: ignore[method-assign]
        ctrl._find_viewport_for_pane = lambda pid: vp  # type: ignore[method-assign]

        shift = Qt.KeyboardModifier.ShiftModifier.value
        # Drop with Shift on top zone → must be treated as horizontal split (right zone → "h")
        ctrl.on_drop_connection("web01", "pane-b", "top", shift)

        backend.split_pane.assert_called_once()
        call_args = backend.split_pane.call_args
        direction = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("direction")
        # Shift forces horizontal split
        assert direction == "h", f"Expected 'h' (horizontal), got {direction!r}"

    def test_ctrl_modifier_forces_vertical_split(self):
        """Acceptance §10.14: Ctrl modifier overrides zone to vertical split ('v')."""
        from PySide6.QtCore import Qt

        vp = _make_viewport(vp_id="vp-01", panes=[_make_pane("conn-a"), _make_pane("conn-b")])
        layout = _make_layout(viewports=[vp])
        ctrl, backend = _make_controller(layout=layout)

        conn = MagicMock()
        conn.launch_profile = "claude-remote"
        ctrl._config.find_connection.return_value = conn
        ctrl._pane_is_empty = lambda pid, v: False  # type: ignore[method-assign]
        ctrl._find_viewport_for_pane = lambda pid: vp  # type: ignore[method-assign]

        ctrl_mod = Qt.KeyboardModifier.ControlModifier.value
        # Drop with Ctrl on left zone → treated as vertical split (bottom zone → "v")
        ctrl.on_drop_connection("web01", "pane-b", "left", ctrl_mod)

        backend.split_pane.assert_called_once()
        call_args = backend.split_pane.call_args
        direction = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("direction")
        assert direction == "v", f"Expected 'v' (vertical), got {direction!r}"

    def test_empty_slot_drop_is_silent_respawn(self):
        """Acceptance §10.14: Drop on empty-slot pane silently calls respawn_pane."""
        vp = _make_viewport(vp_id="vp-01", panes=[_make_pane(None)])
        layout = _make_layout(viewports=[vp])
        ctrl, backend = _make_controller(layout=layout)

        conn = MagicMock()
        conn.launch_profile = "local-shell"
        ctrl._config.find_connection.return_value = conn
        ctrl._pane_is_empty = lambda pid, v: True  # type: ignore[method-assign]
        ctrl._find_viewport_for_pane = lambda pid: vp  # type: ignore[method-assign]

        ctrl.on_drop_connection("scratch", "pane-empty", "center", 0)

        # No disambiguation dialog — direct respawn
        backend.respawn_pane.assert_called_once()


# ---------------------------------------------------------------------------
# §10.15 — Split-and-lock invariant
# ---------------------------------------------------------------------------


class TestSplitAndLockInvariant:
    """Acceptance §10.15: Every add captures layout, sets custom + custom_layout_string."""

    def test_apply_change_called_after_split(self):
        """Acceptance §10.15: layout_svc.apply_change called after every split."""
        vp = _make_viewport(vp_id="vp-01", panes=[_make_pane("conn-a"), _make_pane("conn-b")])
        layout = _make_layout(viewports=[vp])
        backend = _make_backend()
        config = MagicMock()
        layout_svc = MagicMock()
        layout_svc.apply_change.side_effect = lambda doc, lay, vp_, **kw: lay
        session_svc = MagicMock()
        templates_svc = MagicMock()
        templates_svc.render.return_value = "bash /tmp/launcher.sh"
        templates_svc.render_placeholder.return_value = "bash /tmp/placeholder.sh"

        ctrl = LayoutController(
            config=config,
            layout=layout_svc,
            session=session_svc,
            backend=backend,
            templates=templates_svc,
        )
        doc = CpsmDocument()
        ctrl.set_document(doc)
        ctrl.set_screen_layout(layout)

        conn = MagicMock()
        conn.launch_profile = "claude-remote"
        config.find_connection.return_value = conn
        ctrl._pane_is_empty = lambda pid, v: False  # type: ignore[method-assign]
        ctrl._find_viewport_for_pane = lambda pid: vp  # type: ignore[method-assign]

        ctrl.on_drop_connection("web01", "pane-b", "right", 0)

        layout_svc.apply_change.assert_called_once()
        call_kwargs = layout_svc.apply_change.call_args[1]
        assert call_kwargs.get("change_kind") == "split"

    def test_backend_capture_layout_called(self):
        """Acceptance §10.15: backend.capture_layout is called during apply_change."""

        backend = _make_backend()
        layout_svc_real = LayoutService(backend=backend)

        vp = _make_viewport(vp_id="vp-01", panes=[_make_pane("conn-a")])
        layout = _make_layout(viewports=[vp])
        doc = CpsmDocument()

        result_layout = layout_svc_real.apply_change(
            doc,
            layout,
            vp,
            change_kind="split",
            window_target="cpsm-web01:0",
        )

        backend.capture_layout.assert_called_once_with("cpsm-web01:0")
        # Viewport's tmux_layout must be set to "custom"
        updated_vp = result_layout.monitors[0].viewports[0]
        assert updated_vp.tmux_layout == "custom"


# ---------------------------------------------------------------------------
# §10.16 — Removal preserves geometry / reverts when preserve=False
# ---------------------------------------------------------------------------


class TestRemovalPreservesGeometry:
    """Acceptance §10.16: Removal preserves geometry by default; reverts when False."""

    def test_remove_with_preserve_true_calls_layout_service_remove_pane(self):
        """Acceptance §10.16: layout_preserve_on_remove=True → layout_svc.remove_pane called."""
        vp = _make_viewport(vp_id="vp-01", panes=[_make_pane("conn-a")])
        layout = _make_layout(viewports=[vp])
        backend = _make_backend()
        config = MagicMock()
        layout_svc = MagicMock()
        layout_svc.remove_pane.return_value = layout  # return unchanged layout
        session_svc = MagicMock()
        templates_svc = MagicMock()
        templates_svc.render_placeholder.return_value = "bash /tmp/placeholder.sh"

        ctrl = LayoutController(
            config=config,
            layout=layout_svc,
            session=session_svc,
            backend=backend,
            templates=templates_svc,
        )
        doc = CpsmDocument(settings=Settings(layout_preserve_on_remove=True))
        ctrl.set_document(doc)
        ctrl.set_screen_layout(layout)
        ctrl._find_viewport_for_pane = lambda pid: vp  # type: ignore[method-assign]

        ctrl.on_remove_pane("conn-a")

        # layout_svc.remove_pane must have been called (it calls backend.respawn_pane internally)
        layout_svc.remove_pane.assert_called_once()

    def test_remove_with_preserve_false_calls_layout_service_remove_pane(self):
        """Acceptance §10.16: layout_preserve_on_remove=False → layout_svc.remove_pane called."""
        vp = _make_viewport(vp_id="vp-01", panes=[_make_pane("conn-a")])
        layout = _make_layout(viewports=[vp])
        backend = _make_backend()
        config = MagicMock()
        layout_svc = MagicMock()
        layout_svc.remove_pane.return_value = layout
        session_svc = MagicMock()
        templates_svc = MagicMock()
        templates_svc.render_placeholder.return_value = "bash /tmp/placeholder.sh"

        ctrl = LayoutController(
            config=config,
            layout=layout_svc,
            session=session_svc,
            backend=backend,
            templates=templates_svc,
        )
        doc = CpsmDocument(settings=Settings(layout_preserve_on_remove=False))
        ctrl.set_document(doc)
        ctrl.set_screen_layout(layout)
        ctrl._find_viewport_for_pane = lambda pid: vp  # type: ignore[method-assign]

        ctrl.on_remove_pane("conn-a")

        layout_svc.remove_pane.assert_called_once()

    def test_layout_service_remove_preserve_true_calls_backend_respawn(self):
        """Acceptance §10.16: LayoutService.remove_pane with preserve=True calls respawn_pane."""
        from cpsm.services.layout_service import LayoutService

        backend = _make_backend()
        backend.capture_layout.return_value = "tiled"
        layout_svc = LayoutService(backend=backend)

        vp = _make_viewport(vp_id="vp-01", panes=[_make_pane("conn-a")])
        layout = _make_layout(viewports=[vp])
        doc = CpsmDocument(settings=Settings(layout_preserve_on_remove=True))

        layout_svc.remove_pane(
            doc,
            layout,
            vp,
            pane_index=0,
            window_target="cpsm-test:0",
            pane_target="conn-a",
            placeholder_command="bash /tmp/placeholder.sh",
        )

        backend.respawn_pane.assert_called_once()

    def test_layout_service_remove_preserve_false_calls_backend_kill(self):
        """Acceptance §10.16: LayoutService.remove_pane with preserve=False calls kill_pane."""
        from cpsm.services.layout_service import LayoutService

        backend = _make_backend()
        backend.capture_layout.return_value = "tiled"
        layout_svc = LayoutService(backend=backend)

        vp = _make_viewport(vp_id="vp-01", panes=[_make_pane("conn-a")])
        layout = _make_layout(viewports=[vp])
        doc = CpsmDocument(settings=Settings(layout_preserve_on_remove=False))

        layout_svc.remove_pane(
            doc,
            layout,
            vp,
            pane_index=0,
            window_target="cpsm-test:0",
            pane_target="conn-a",
            placeholder_command="bash /tmp/placeholder.sh",
        )

        backend.kill_pane.assert_called_once()


# ---------------------------------------------------------------------------
# §10.17 — Drop on empty-slot uses respawn-pane
# ---------------------------------------------------------------------------


class TestDropOnEmptySlot:
    """Acceptance §10.17: Drop on empty-slot uses respawn-pane, layout byte-stable."""

    def test_empty_slot_respawn_uses_placeholder_text_stable(self):
        """Acceptance §10.17: respawn_pane command is byte-stable (same placeholder script)."""
        vp = _make_viewport(vp_id="vp-01", panes=[_make_pane(None)])
        layout = _make_layout(viewports=[vp])
        ctrl, backend = _make_controller(layout=layout)

        conn = MagicMock()
        conn.launch_profile = "local-shell"
        ctrl._config.find_connection.return_value = conn
        ctrl._pane_is_empty = lambda pid, v: True  # type: ignore[method-assign]
        ctrl._find_viewport_for_pane = lambda pid: vp  # type: ignore[method-assign]

        ctrl.on_drop_connection("scratch", "empty-pane", "center", 0)

        assert backend.respawn_pane.call_count == 1
        first_cmd = backend.respawn_pane.call_args[0]

        # Second call (simulate same drop again)
        backend.respawn_pane.reset_mock()
        ctrl.on_drop_connection("scratch", "empty-pane", "center", 0)

        assert backend.respawn_pane.call_count == 1
        second_cmd = backend.respawn_pane.call_args[0]

        # Commands must be identical (layout string byte-stable)
        assert first_cmd == second_cmd


# ---------------------------------------------------------------------------
# §10.18 — Same-window pane-to-pane swap
# ---------------------------------------------------------------------------


class TestSameWindowSwap:
    """Acceptance §10.18: Same-window pane drop = swap_pane."""

    def test_intrawindow_drop_calls_swap_panes(self):
        """Acceptance §10.18: Dropping pane onto another within the same window calls swap_panes."""
        vp = _make_viewport(vp_id="vp-01", panes=[_make_pane("conn-a"), _make_pane("conn-b")])
        layout = _make_layout(viewports=[vp])
        ctrl, backend = _make_controller(layout=layout)
        ctrl._find_viewport_for_pane = lambda pid: vp  # type: ignore[method-assign]

        # on_drop_pane with same-window center zone → swap_panes
        ctrl.on_drop_pane("conn-a", "conn-b", "center", 0)

        backend.swap_panes.assert_called_once()


# ---------------------------------------------------------------------------
# §10.19 — Drop into full window splits target only
# ---------------------------------------------------------------------------


class TestDropIntoFullWindow:
    """Acceptance §10.19: Drop into full window splits target only."""

    def test_drop_into_occupied_splits_only_target_pane(self):
        """Acceptance §10.19: Dropping on an occupied pane splits that pane only."""
        pane_a = _make_pane("conn-a")
        pane_b = _make_pane("conn-b")
        vp = _make_viewport(vp_id="vp-01", panes=[pane_a, pane_b])
        layout = _make_layout(viewports=[vp])
        ctrl, backend = _make_controller(layout=layout)

        conn = MagicMock()
        conn.launch_profile = "claude-remote"
        ctrl._config.find_connection.return_value = conn
        ctrl._pane_is_empty = lambda pid, v: False  # type: ignore[method-assign]
        ctrl._find_viewport_for_pane = lambda pid: vp  # type: ignore[method-assign]

        ctrl.on_drop_connection("web01", "pane-b", "right", 0)

        # split_pane must be called exactly once — only the target is split
        assert backend.split_pane.call_count == 1
        # respawn_pane should NOT be called for the other pane
        # (respawn may be called for the new pane's launcher)
        # The key invariant: only one split call, not two
