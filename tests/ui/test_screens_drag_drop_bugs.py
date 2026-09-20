# -*- coding: utf-8 -*-
"""
tests/ui/test_screens_drag_drop_bugs.py

Tests for the three Screens-tab drag-drop bugs:
  Bug 1: "New Layout" creates panes with connection_id=None
  Bug 2: Drag-drop on Screens tab does nothing in Preview mode
  Bug 3: Drop on empty viewport (no panes) silently fails

Tests required per spec:
  - test_screens_new_layout_prefills_connection_ids
  - test_screens_drop_connection_on_pane_replaces_in_preview
  - test_screens_drop_connection_on_empty_pane_assigns_in_preview
  - test_screen_map_widget_emits_viewport_drop_signal_for_empty_viewport
  - test_screens_drop_on_empty_viewport_appends_pane_in_preview
"""

from __future__ import annotations

import os
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QRadioButton

from cpsm.data.schema import (
    ClaudeLocalConnection,
    CpsmDocument,
    GeometryPct,
    Group,
    Monitor,
    Pane,
    ScreenLayout,
    Viewport,
)
from cpsm.services.monitor_service import MonitorInfo
from cpsm.ui.main_window import MainWindow
from cpsm.ui.widgets.screen_map import ScreenMapWidget

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_conn(cid: str) -> ClaudeLocalConnection:
    return ClaudeLocalConnection(
        id=cid,
        name=cid,
        launch_profile="claude-local",
        project_folder=f"~/{cid}",
        claude_options="--resume",
    )


def _make_monitor_info(
    identifier: str = "test-mon",
    x: int = 0,
    y: int = 0,
    w: int = 1920,
    h: int = 1080,
    qt_index: int = 0,
) -> MonitorInfo:
    return MonitorInfo(
        identifier=identifier,
        name=identifier,
        geometry=(x, y, w, h),
        available_geometry=(x, y, w, h),
        physical_size_mm=(527.0, 296.0),
        device_pixel_ratio=1.0,
        orientation="landscape",
        manufacturer="",
        model="",
        serial="",
        qt_index=qt_index,
    )


def _make_layout_with_pane(lid: str, conn_id: str | None, vp_id: str = "vp-test") -> ScreenLayout:
    """One monitor, one viewport, one pane with the given connection_id."""
    pane = Pane(connection_id=conn_id)
    vp = Viewport(
        id=vp_id,
        geometry_pct=GeometryPct(x=0, y=0, w=100, h=100),
        tmux_layout="tiled",
        panes=[pane],
    )
    return ScreenLayout(id=lid, name=lid, monitors=[Monitor(viewports=[vp])])


def _make_layout_with_empty_viewport(
    lid: str, vp_id: str = "vp-empty"
) -> ScreenLayout:
    """One monitor, one viewport, zero panes."""
    vp = Viewport(
        id=vp_id,
        geometry_pct=GeometryPct(x=0, y=0, w=100, h=100),
        tmux_layout="tiled",
        panes=[],
    )
    return ScreenLayout(id=lid, name=lid, monitors=[Monitor(viewports=[vp])])


def _open_win(qtbot: Any, doc: CpsmDocument) -> MainWindow:
    win = MainWindow(document=doc)
    qtbot.addWidget(win)
    win.show()
    QApplication.processEvents()
    return win


def _switch_to_preview(win: MainWindow) -> None:
    radio = win.findChild(QRadioButton, "radio_screens_preview")
    assert radio is not None, "radio_screens_preview not found"
    radio.setChecked(True)
    QApplication.processEvents()


# ---------------------------------------------------------------------------
# Stub MonitorService returning exactly one monitor
# ---------------------------------------------------------------------------


class _StubMonitorService:
    """Minimal monitor service stub returning a single monitor."""

    def __init__(self, monitors: list[MonitorInfo]) -> None:
        self._monitors = monitors

    def snapshot(self) -> list[MonitorInfo]:
        return list(self._monitors)

    # Dummy signals so ScreenMapWidget.connect(...) doesn't raise
    class _NullSignal:
        def connect(self, *a: Any, **kw: Any) -> None:
            pass

    monitor_added = _NullSignal()
    monitor_removed = _NullSignal()


# ---------------------------------------------------------------------------
# Bug 1: New Layout prefills connection_ids
# ---------------------------------------------------------------------------


class TestAutoLayoutPrefillsConnectionIds:
    """Round C: auto-layout (one per Group) must wire connection_ids in the
    live-monitor path (not just the headless fallback). The auto-layout
    runs at document load time."""

    def test_auto_layout_prefills_connection_ids(self, qtbot: Any) -> None:
        """With 1 monitor and 3 members, the auto-generated layout's
        viewport must have 3 panes whose connection_ids match the
        members."""
        conn_a = _make_conn("conn-a")
        conn_b = _make_conn("conn-b")
        conn_c = _make_conn("conn-c")
        grp = Group(id="grp-n", name="Group N", members=["conn-a", "conn-b", "conn-c"])
        doc = CpsmDocument(
            connections=[conn_a, conn_b, conn_c],
            groups=[grp],
            screen_layouts=[],
        )

        # Pre-stub _query_live_monitors via a subclass so auto-layout
        # exercises the live-monitor path even before the window is shown.
        single_monitor = _make_monitor_info(identifier="test-mon")
        from cpsm.ui.main_window import MainWindow as _MW

        class _StubbedMainWindow(_MW):
            def _query_live_monitors(self_inner):  # type: ignore[no-untyped-def]
                return [single_monitor]

            def _save_document(self_inner):  # type: ignore[no-untyped-def]
                pass  # avoid accidental disk writes

        win = _StubbedMainWindow(document=doc)
        qtbot.addWidget(win)
        win.show()
        QApplication.processEvents()

        # Auto-layout should have created exactly one layout for the group
        assert len(win._document.screen_layouts) == 1, (
            f"Expected exactly one auto-created layout, got {len(win._document.screen_layouts)}"
        )
        new_layout = win._document.screen_layouts[0]
        assert win._document.groups[0].default_layout_id == new_layout.id

        # Gather all pane connection_ids across all monitors/viewports
        all_pane_ids = []
        for mon in new_layout.monitors:
            for vp in mon.viewports:
                for pane in vp.panes:
                    all_pane_ids.append(pane.connection_id)

        assert len(all_pane_ids) == 3, (
            f"Expected 3 panes, got {len(all_pane_ids)}: {all_pane_ids}"
        )
        assert None not in all_pane_ids, (
            f"Some panes still have connection_id=None: {all_pane_ids}"
        )
        assert set(all_pane_ids) == {"conn-a", "conn-b", "conn-c"}, (
            f"Unexpected connection_ids: {all_pane_ids}"
        )


# ---------------------------------------------------------------------------
# Bug 2: Drop on pane replaces connection_id in Preview mode
# ---------------------------------------------------------------------------


class TestDropConnectionOnPanePreview:
    """Bug 2: drop_connection_requested in Preview mode must mutate the
    document (replace pane.connection_id) and call save."""

    def test_screens_drop_connection_on_pane_replaces_in_preview(
        self, qtbot: Any
    ) -> None:
        """Emit drop_connection_requested('b', 'a', 'center', 0) from the
        Screens-tab widget; the pane with connection_id='a' must be updated
        to 'b' and save must be called."""
        layout = _make_layout_with_pane("test-layout", "conn-a", vp_id="vp-test")
        conn_a = _make_conn("conn-a")
        conn_b = _make_conn("conn-b")
        grp = Group(
            id="grp-t",
            name="Group T",
            members=["conn-a", "conn-b"],
            default_layout_id="test-layout",
        )
        doc = CpsmDocument(
            connections=[conn_a, conn_b],
            groups=[grp],
            screen_layouts=[layout],
        )

        win = _open_win(qtbot, doc)
        _switch_to_preview(win)
        QApplication.processEvents()

        save_calls: list[int] = []
        win._save_document = lambda: save_calls.append(1)  # type: ignore[method-assign]

        # Emit from the Screens-tab widget
        win._screen_map_widget.drop_connection_requested.emit("conn-b", "conn-a", "center", 0)
        QApplication.processEvents()

        # Verify document mutation
        updated_layout = win._document.screen_layouts[0]
        panes = updated_layout.monitors[0].viewports[0].panes
        assert len(panes) == 1
        assert panes[0].connection_id == "conn-b", (
            f"Expected connection_id='conn-b', got {panes[0].connection_id!r}"
        )
        assert len(save_calls) >= 1, "Expected _save_document to be called at least once"

    def test_screens_drop_connection_on_empty_pane_assigns_in_preview(
        self, qtbot: Any
    ) -> None:
        """Pane with connection_id=None is rendered as __empty_0.
        Dropping 'conn-x' on '__empty_0' must assign connection_id='conn-x'."""
        layout = _make_layout_with_pane("test-empty-layout", None, vp_id="vp-empty")
        conn_x = _make_conn("conn-x")
        grp = Group(
            id="grp-e",
            name="Group E",
            members=["conn-x"],
            default_layout_id="test-empty-layout",
        )
        doc = CpsmDocument(
            connections=[conn_x],
            groups=[grp],
            screen_layouts=[layout],
        )

        win = _open_win(qtbot, doc)
        _switch_to_preview(win)
        QApplication.processEvents()

        save_calls: list[int] = []
        win._save_document = lambda: save_calls.append(1)  # type: ignore[method-assign]

        # The pane has connection_id=None, so pane_serial=0 → pane_id="__empty_0"
        win._screen_map_widget.drop_connection_requested.emit(
            "conn-x", "__empty_0", "center", 0
        )
        QApplication.processEvents()

        updated_layout = win._document.screen_layouts[0]
        panes = updated_layout.monitors[0].viewports[0].panes
        assert len(panes) == 1
        assert panes[0].connection_id == "conn-x", (
            f"Expected connection_id='conn-x', got {panes[0].connection_id!r}"
        )
        assert len(save_calls) >= 1, "Expected _save_document to be called"


# ---------------------------------------------------------------------------
# Bug 3: Drop on empty viewport emits signal and appends pane
# ---------------------------------------------------------------------------


class TestDropOnEmptyViewport:
    """Bug 3: dropping on a viewport with no panes must emit
    drop_connection_on_viewport_requested and append a new Pane."""

    def _make_widget_with_empty_viewport(
        self, vp_id: str = "vp-empty"
    ) -> ScreenMapWidget:
        """Build a standalone ScreenMapWidget with one monitor and one empty viewport."""
        widget = ScreenMapWidget()
        monitor = _make_monitor_info(identifier="test-mon", qt_index=0)
        vp = Viewport(
            id=vp_id,
            geometry_pct=GeometryPct(x=0, y=0, w=100, h=100),
            tmux_layout="tiled",
            panes=[],
        )
        layout = ScreenLayout(
            id="test-layout",
            name="Test",
            monitors=[Monitor(identifier="test-mon", monitor_index_hint=0, viewports=[vp])],
        )
        widget.set_layout(layout, [monitor])
        return widget

    def test_screen_map_widget_emits_viewport_drop_signal_for_empty_viewport(
        self, qtbot: Any
    ) -> None:
        """Unit test on ScreenMapWidget alone: layout with one empty viewport;
        simulate drop at a scene position inside the viewport and verify that
        drop_connection_on_viewport_requested fires with the correct viewport_id."""
        widget = self._make_widget_with_empty_viewport("vp-empty")
        qtbot.addWidget(widget)
        widget.show()
        QApplication.processEvents()

        emitted: list[tuple[str, str, int]] = []
        widget.drop_connection_on_viewport_requested.connect(
            lambda cid, vid, mods: emitted.append((cid, vid, mods))
        )

        # Find a scene point that falls inside the viewport.
        # The viewport covers 100% of the monitor at scale ≈0.5 with 1920x1080 monitor.
        # We just need any interior point. Use the centre of the scene bounding rect.
        scene_rect = widget.scene.itemsBoundingRect()
        if scene_rect.isNull() or scene_rect.isEmpty():
            # Fallback: compute centre manually for 1920x1080 @ scale≈min(800/1920,800/1080)
            scale = min(800 / 1920, 800 / 1080)
            cx = 1920 * scale / 2
            cy = 1080 * scale / 2
        else:
            cx = scene_rect.center().x()
            cy = scene_rect.center().y()

        # Verify viewport_at_scene_pos returns the expected id
        vp_id_hit = widget.viewport_at_scene_pos(cx, cy)
        assert vp_id_hit == "vp-empty", (
            f"Expected viewport 'vp-empty' at ({cx:.1f}, {cy:.1f}), got {vp_id_hit!r}"
        )

        # Simulate the drop by calling _on_drop directly via the widget method
        # We do this by directly calling the internal method with a fake MIME object.
        from PySide6.QtCore import QByteArray, QMimeData, QPointF
        from PySide6.QtGui import QDropEvent

        from cpsm.ui.widgets.screen_map import MIME_CONNECTION_ID

        mime = QMimeData()
        ba = QByteArray()
        ba.append(b"conn-dropped")
        mime.setData(MIME_CONNECTION_ID, ba)

        # Map scene point back to view coords
        view_pt = widget.view.mapFromScene(QPointF(cx, cy))

        drop_event = QDropEvent(
            view_pt.toPointF(),
            # type: ignore[call-arg]
            __import__("PySide6.QtCore", fromlist=["Qt"]).Qt.DropAction.CopyAction,
            mime,
            __import__("PySide6.QtCore", fromlist=["Qt"]).Qt.MouseButton.LeftButton,
            __import__("PySide6.QtCore", fromlist=["Qt"]).Qt.KeyboardModifier.NoModifier,
        )

        widget._on_drop(drop_event)
        QApplication.processEvents()

        assert len(emitted) == 1, (
            f"Expected drop_connection_on_viewport_requested to be emitted once, "
            f"got {len(emitted)} times"
        )
        dropped_conn_id, dropped_vp_id, _mods = emitted[0]
        assert dropped_conn_id == "conn-dropped", f"Wrong conn_id: {dropped_conn_id!r}"
        assert dropped_vp_id == "vp-empty", f"Wrong viewport_id: {dropped_vp_id!r}"

    def test_screens_drop_on_empty_viewport_appends_pane_in_preview(
        self, qtbot: Any
    ) -> None:
        """Integration: emit drop_connection_on_viewport_requested on Screens tab;
        the layout's viewport must gain a new pane with the dropped connection_id
        and the document must be saved."""
        layout = _make_layout_with_empty_viewport("empty-vp-layout", vp_id="vp-empty")
        conn_a = _make_conn("conn-a")
        conn_b = _make_conn("conn-b")
        grp = Group(
            id="grp-ev",
            name="Group EV",
            members=["conn-a", "conn-b"],
            default_layout_id="empty-vp-layout",
        )
        doc = CpsmDocument(
            connections=[conn_a, conn_b],
            groups=[grp],
            screen_layouts=[layout],
        )

        win = _open_win(qtbot, doc)
        _switch_to_preview(win)
        QApplication.processEvents()

        save_calls: list[int] = []
        win._save_document = lambda: save_calls.append(1)  # type: ignore[method-assign]

        # Emit the viewport drop signal directly
        win._screen_map_widget.drop_connection_on_viewport_requested.emit(
            "conn-b", "vp-empty", 0
        )
        QApplication.processEvents()

        updated_layout = win._document.screen_layouts[0]
        panes = updated_layout.monitors[0].viewports[0].panes
        assert len(panes) == 1, f"Expected 1 pane appended, got {len(panes)}"
        assert panes[0].connection_id == "conn-b", (
            f"Expected connection_id='conn-b', got {panes[0].connection_id!r}"
        )
        assert len(save_calls) >= 1, "Expected _save_document to be called"
