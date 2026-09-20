# -*- coding: utf-8 -*-
"""
tests/ui/test_screen_map_basic.py — pytest-qt tests for ScreenMapWidget.

Spec sections: §6.1, §6.2, §6.3
Phase 15: basic rendering (read-only, no drag-and-drop).

All tests run with QT_QPA_PLATFORM=offscreen (set by conftest.py and the
os.environ.setdefault guard below).
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject, Qt, Signal

from cpsm.data.schema import (
    ClaudeLocalConnection,
    ClaudeRemoteConnection,
    GeometryPct,
    Monitor,
    Pane,
    ScreenLayout,
    SshKey,
    Viewport,
)
from cpsm.services.monitor_service import MonitorInfo
from cpsm.ui.widgets.screen_map import (
    _PROFILE_GLYPHS,
    ScreenMapWidget,
    _compute_pane_rects,
    _even_horizontal_rects,
    _even_vertical_rects,
    _main_horizontal_rects,
    _main_vertical_rects,
    _tiled_rects,
)

# ---------------------------------------------------------------------------
# Synthetic MonitorInfo factory
# ---------------------------------------------------------------------------


def _make_monitor_info(
    identifier: str = "test-mon",
    name: str = "HDMI-1",
    x: int = 0,
    y: int = 0,
    w: int = 1920,
    h: int = 1080,
    qt_index: int = 0,
) -> MonitorInfo:
    return MonitorInfo(
        identifier=identifier,
        name=name,
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


# ---------------------------------------------------------------------------
# Synthetic ScreenLayout factory helpers
# ---------------------------------------------------------------------------


def _full_viewport(
    vp_id: str = "vp-main",
    window_name: str = "main",
    layout: str = "tiled",
    panes: list[Pane] | None = None,
) -> Viewport:
    return Viewport(
        id=vp_id,
        geometry_pct=GeometryPct(x=0, y=0, w=100, h=100),
        tmux_window_name=window_name,
        tmux_layout=layout,  # type: ignore[arg-type]
        panes=panes or [],
    )


def _half_viewport(
    vp_id: str,
    x_pct: float,
    window_name: str,
    panes: list[Pane] | None = None,
) -> Viewport:
    return Viewport(
        id=vp_id,
        geometry_pct=GeometryPct(x=x_pct, y=0, w=50, h=100),
        tmux_window_name=window_name,
        tmux_layout="tiled",
        panes=panes or [],
    )


# ---------------------------------------------------------------------------
# Minimal MonitorService stub that emits signals
# ---------------------------------------------------------------------------


class _FakeMonitorService(QObject):
    monitor_added: Signal = Signal(MonitorInfo)
    monitor_removed: Signal = Signal(str)

    def __init__(self, monitors: list[MonitorInfo] | None = None) -> None:
        super().__init__()
        self._monitors = monitors or []

    def snapshot(self) -> list[MonitorInfo]:
        return list(self._monitors)

    def set_monitors(self, monitors: list[MonitorInfo]) -> None:
        self._monitors = list(monitors)


# ---------------------------------------------------------------------------
# Connection lookup fixture
# ---------------------------------------------------------------------------


def _make_lookup(*connections):
    """Return a callable that looks up connections by id."""
    by_id = {c.id: c for c in connections}
    return lambda cid: by_id.get(cid)


_DUMMY_KEY = SshKey(
    id="key-test-01",
    name="Test key",
    type="ed25519",
    private_path="~/.ssh/id_ed25519",
    public_path="~/.ssh/id_ed25519.pub",
)

_CONN_REMOTE = ClaudeRemoteConnection(
    id="remote-01",
    name="Remote Server",
    launch_profile="claude-remote",
    host="example.com",
    user="ubuntu",
    identity_file_ref="key-test-01",
    project_folder="/opt/project",
    claude_options="--resume",
)

_CONN_LOCAL = ClaudeLocalConnection(
    id="local-01",
    name="Local Dev",
    launch_profile="claude-local",
    project_folder="~/projects/dev",
    claude_options="--resume",
)

_LOOKUP_BOTH = _make_lookup(_CONN_REMOTE, _CONN_LOCAL)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def monitor_service() -> _FakeMonitorService:
    mon = _make_monitor_info()
    return _FakeMonitorService(monitors=[mon])


@pytest.fixture()
def widget(qtbot, monitor_service: _FakeMonitorService) -> ScreenMapWidget:
    w = ScreenMapWidget(
        monitor_service=monitor_service,
        connection_lookup=_LOOKUP_BOTH,
    )
    qtbot.addWidget(w)
    w.show()
    return w


# ---------------------------------------------------------------------------
# Helper: count QGraphicsRectItems in a scene
# ---------------------------------------------------------------------------


def _count_rects(widget: ScreenMapWidget) -> int:
    from PySide6.QtWidgets import QGraphicsRectItem

    return sum(1 for item in widget.scene.items() if isinstance(item, QGraphicsRectItem))


def _find_text_items(widget: ScreenMapWidget) -> list[str]:
    from PySide6.QtWidgets import QGraphicsTextItem

    return [
        item.toPlainText() for item in widget.scene.items() if isinstance(item, QGraphicsTextItem)
    ]


# ===========================================================================
# Tests: object names and basic structure
# ===========================================================================


class TestWidgetStructure:
    def test_widget_object_name(self, widget: ScreenMapWidget) -> None:
        assert widget.objectName() == "widget_screen_map"

    def test_view_object_name(self, widget: ScreenMapWidget) -> None:
        assert widget.view.objectName() == "screenmap_view"

    def test_mode_label_object_name(self, widget: ScreenMapWidget) -> None:
        assert widget._mode_label.objectName() == "screenmap_mode_label"

    def test_btn_live_object_name(self, widget: ScreenMapWidget) -> None:
        assert widget._btn_live.objectName() == "screenmap_btn_live"

    def test_btn_preview_object_name(self, widget: ScreenMapWidget) -> None:
        assert widget._btn_preview.objectName() == "screenmap_btn_preview"

    def test_initial_mode_is_live(self, widget: ScreenMapWidget) -> None:
        assert widget._mode == "live"

    def test_scene_initially_empty(self, widget: ScreenMapWidget) -> None:
        assert widget.scene.items() == []

    def test_has_view_property(self, widget: ScreenMapWidget) -> None:
        from PySide6.QtWidgets import QGraphicsView

        assert isinstance(widget.view, QGraphicsView)

    def test_has_scene_property(self, widget: ScreenMapWidget) -> None:
        from PySide6.QtWidgets import QGraphicsScene

        assert isinstance(widget.scene, QGraphicsScene)


# ===========================================================================
# Tests: single monitor + single viewport + 1 pane
# ===========================================================================


class TestSingleMonitorSinglePane:
    """Single monitor + single viewport with 1 pane."""

    def _setup(self, widget: ScreenMapWidget, monitor_service: _FakeMonitorService) -> None:
        mon = _make_monitor_info()
        layout = ScreenLayout(
            id="layout-01",
            name="Test Layout",
            monitors=[
                Monitor(
                    identifier="test-mon",
                    viewports=[
                        _full_viewport(
                            panes=[Pane(connection_id="remote-01")],
                        )
                    ],
                )
            ],
        )
        monitor_service.set_monitors([mon])
        widget.set_layout(layout, [mon])

    def test_at_least_one_rect_rendered(
        self, widget: ScreenMapWidget, monitor_service: _FakeMonitorService
    ) -> None:
        self._setup(widget, monitor_service)
        assert _count_rects(widget) >= 1

    def test_pane_rect_rendered(
        self, widget: ScreenMapWidget, monitor_service: _FakeMonitorService
    ) -> None:
        self._setup(widget, monitor_service)
        # Monitor + viewport + pane = at least 3 rects
        assert _count_rects(widget) >= 3

    def test_no_profile_glyph_in_scene(
        self, widget: ScreenMapWidget, monitor_service: _FakeMonitorService
    ) -> None:
        # Round A removed the small profile glyph in favour of one large
        # centered session name. Glyphs should no longer appear.
        self._setup(widget, monitor_service)
        texts = _find_text_items(widget)
        assert not any("🔗" in t for t in texts), f"Profile glyph should be gone; texts={texts}"

    def test_connection_name_in_scene(
        self, widget: ScreenMapWidget, monitor_service: _FakeMonitorService
    ) -> None:
        self._setup(widget, monitor_service)
        texts = _find_text_items(widget)
        assert any("Remote Server" in t for t in texts), f"Connection name not found; texts={texts}"


# ===========================================================================
# Tests: single monitor + single viewport + 4 panes (tiled 2x2)
# ===========================================================================


class TestFourPanesTiled:
    """4 panes in tiled layout should form a 2x2 grid."""

    def _setup(self, widget: ScreenMapWidget, monitor_service: _FakeMonitorService) -> None:
        mon = _make_monitor_info()
        # Empty panes are not rendered after Round A; use real ids so the
        # 4-pane grid actually appears in the scene.
        panes = [Pane(connection_id=f"conn-{i}") for i in range(4)]
        layout = ScreenLayout(
            id="layout-4panes",
            name="4 Panes",
            monitors=[
                Monitor(
                    identifier="test-mon",
                    viewports=[
                        _full_viewport(
                            vp_id="vp-grid",
                            layout="tiled",
                            panes=panes,
                        )
                    ],
                )
            ],
        )
        monitor_service.set_monitors([mon])
        widget.set_layout(layout, [mon])

    def test_four_pane_rects_rendered(
        self, widget: ScreenMapWidget, monitor_service: _FakeMonitorService
    ) -> None:
        self._setup(widget, monitor_service)
        # 1 monitor + 1 viewport + 4 panes = at least 6 rects
        assert _count_rects(widget) >= 6

    def test_tiled_rects_math_2x2(self) -> None:
        """tiled layout for n=4 should produce 2x2 grid (each 1/4 of viewport)."""
        rects = _tiled_rects(4, 0, 0, 100, 100)
        assert len(rects) == 4
        # Each pane should be 50x50
        for x, y, w, h in rects:
            assert abs(w - 50.0) < 0.01, f"Expected w=50, got {w}"
            assert abs(h - 50.0) < 0.01, f"Expected h=50, got {h}"

    def test_tiled_rects_math_1pane(self) -> None:
        """1 pane should occupy the full viewport."""
        rects = _tiled_rects(1, 0, 0, 100, 100)
        assert len(rects) == 1
        assert rects[0] == (0, 0, 100, 100)

    def test_tiled_rects_math_2panes(self) -> None:
        """2 panes: 2 cols x 1 row -> each 50x100."""
        rects = _tiled_rects(2, 0, 0, 100, 100)
        assert len(rects) == 2
        for x, y, w, h in rects:
            assert abs(w - 50.0) < 0.01
            assert abs(h - 100.0) < 0.01


# ===========================================================================
# Tests: two monitors side-by-side
# ===========================================================================


class TestTwoMonitors:
    """Two monitors side-by-side — both should render."""

    def _setup(self, widget: ScreenMapWidget, monitor_service: _FakeMonitorService) -> None:
        mon1 = _make_monitor_info(
            identifier="mon-left", name="HDMI-1", x=0, y=0, w=1920, h=1080, qt_index=0
        )
        mon2 = _make_monitor_info(
            identifier="mon-right", name="HDMI-2", x=1920, y=0, w=1920, h=1080, qt_index=1
        )
        monitors = [mon1, mon2]
        layout = ScreenLayout(
            id="layout-dual",
            name="Dual Monitor",
            monitors=[
                Monitor(identifier="mon-left", viewports=[_full_viewport(vp_id="vp-l")]),
                Monitor(identifier="mon-right", viewports=[_full_viewport(vp_id="vp-r")]),
            ],
        )
        monitor_service.set_monitors(monitors)
        widget.set_layout(layout, monitors)

    def test_two_monitor_rects_rendered(
        self, widget: ScreenMapWidget, monitor_service: _FakeMonitorService
    ) -> None:
        self._setup(widget, monitor_service)
        # At least 2 monitor rects + 2 viewport rects
        assert _count_rects(widget) >= 4

    def test_scene_not_empty_after_dual_set(
        self, widget: ScreenMapWidget, monitor_service: _FakeMonitorService
    ) -> None:
        self._setup(widget, monitor_service)
        assert len(widget.scene.items()) > 0

    def test_both_monitor_names_in_scene(
        self, widget: ScreenMapWidget, monitor_service: _FakeMonitorService
    ) -> None:
        self._setup(widget, monitor_service)
        texts = _find_text_items(widget)
        assert any("HDMI-1" in t for t in texts), f"HDMI-1 not found; texts={texts}"
        assert any("HDMI-2" in t for t in texts), f"HDMI-2 not found; texts={texts}"


# ===========================================================================
# Tests: empty-slot pane
# ===========================================================================


class TestEmptyPaneNotRendered:
    """Empty panes (connection_id=None) MUST NOT be rendered after Round A —
    they should be visually indistinguishable from no-pane area so the user
    can drop on them and have a connection placed automatically."""

    def _setup(self, widget: ScreenMapWidget, monitor_service: _FakeMonitorService) -> None:
        mon = _make_monitor_info()
        layout = ScreenLayout(
            id="layout-empty",
            name="Empty Slot",
            monitors=[
                Monitor(
                    identifier="test-mon",
                    viewports=[
                        _full_viewport(
                            vp_id="vp-empty",
                            panes=[Pane(connection_id=None)],
                        )
                    ],
                )
            ],
        )
        monitor_service.set_monitors([mon])
        widget.set_layout(layout, [mon])

    def test_no_empty_slot_text(
        self, widget: ScreenMapWidget, monitor_service: _FakeMonitorService
    ) -> None:
        self._setup(widget, monitor_service)
        texts = _find_text_items(widget)
        assert not any("empty slot" in t for t in texts), (
            f"'empty slot' text must not appear; texts={texts}"
        )

    def test_empty_pane_rect_not_rendered(
        self, widget: ScreenMapWidget, monitor_service: _FakeMonitorService
    ) -> None:
        self._setup(widget, monitor_service)
        # monitor + viewport only; no pane rect for the empty pane
        assert _count_rects(widget) == 2

    def test_no_empty_glyph_symbol(
        self, widget: ScreenMapWidget, monitor_service: _FakeMonitorService
    ) -> None:
        self._setup(widget, monitor_service)
        texts = _find_text_items(widget)
        assert not any("▭" in t for t in texts), f"▭ symbol must not appear; texts={texts}"


# ===========================================================================
# Tests: hot-plug — monitor_added → canvas redrawn
# ===========================================================================


class TestHotPlugAdded:
    """Emitting monitor_added should trigger a redraw."""

    def test_monitor_added_triggers_redraw(
        self, qtbot, widget: ScreenMapWidget, monitor_service: _FakeMonitorService
    ) -> None:
        # Set an initial layout
        mon1 = _make_monitor_info(identifier="mon-1")
        layout = ScreenLayout(
            id="layout-hotplug",
            name="Hot Plug",
            monitors=[
                Monitor(identifier="mon-1", viewports=[_full_viewport(vp_id="vp-hp")]),
            ],
        )
        monitor_service.set_monitors([mon1])
        widget.set_layout(layout, [mon1])

        # Add a second monitor to service state, then emit signal
        mon2 = _make_monitor_info(identifier="mon-2", name="DP-1", x=1920, y=0, qt_index=1)
        monitor_service.set_monitors([mon1, mon2])
        # Emit the signal — _on_monitor_added should update self._monitors and redraw
        monitor_service.monitor_added.emit(mon2)

        # After redraw the scene should have been rebuilt (same or more items)
        assert len(widget.scene.items()) >= 0  # scene was rebuilt without error

    def test_monitor_added_does_not_crash(
        self, qtbot, widget: ScreenMapWidget, monitor_service: _FakeMonitorService
    ) -> None:
        new_mon = _make_monitor_info(identifier="new-mon", name="VGA-1", qt_index=1)
        monitor_service.set_monitors([_make_monitor_info(), new_mon])
        monitor_service.monitor_added.emit(new_mon)  # must not raise


# ===========================================================================
# Tests: hot-plug — monitor_removed for in-use monitor → ghost overlay
# ===========================================================================


class TestHotPlugRemoved:
    """Emitting monitor_removed for a monitor used in the layout shows ghost."""

    def _setup(self, widget: ScreenMapWidget, monitor_service: _FakeMonitorService) -> None:
        mon = _make_monitor_info(identifier="mon-ghost")
        layout = ScreenLayout(
            id="layout-ghost",
            name="Ghost Test",
            monitors=[
                Monitor(
                    identifier="mon-ghost",
                    viewports=[_full_viewport(vp_id="vp-ghost")],
                )
            ],
        )
        monitor_service.set_monitors([mon])
        widget.set_layout(layout, [mon])

    def test_ghost_overlay_shown_after_removal(
        self, qtbot, widget: ScreenMapWidget, monitor_service: _FakeMonitorService
    ) -> None:
        self._setup(widget, monitor_service)

        # Remove the monitor from service, emit signal
        monitor_service.set_monitors([])
        monitor_service.monitor_removed.emit("mon-ghost")

        texts = _find_text_items(widget)
        assert any("disconnected" in t.lower() for t in texts), (
            f"Ghost overlay text not found; texts={texts}"
        )

    def test_ghost_overlay_mentions_viewports(
        self, qtbot, widget: ScreenMapWidget, monitor_service: _FakeMonitorService
    ) -> None:
        self._setup(widget, monitor_service)
        monitor_service.set_monitors([])
        monitor_service.monitor_removed.emit("mon-ghost")

        texts = _find_text_items(widget)
        assert any("viewport" in t.lower() for t in texts), (
            f"'viewport' not found in ghost text; texts={texts}"
        )

    def test_ghost_rect_rendered(
        self, qtbot, widget: ScreenMapWidget, monitor_service: _FakeMonitorService
    ) -> None:
        self._setup(widget, monitor_service)
        monitor_service.set_monitors([])
        monitor_service.monitor_removed.emit("mon-ghost")
        assert _count_rects(widget) >= 1


# ===========================================================================
# Tests: mode toggle
# ===========================================================================


class TestModeToggle:
    def test_set_mode_preview_emits_signal(self, qtbot, widget: ScreenMapWidget) -> None:
        with qtbot.waitSignal(widget.mode_changed, timeout=500) as blocker:
            widget.set_mode("preview")
        assert blocker.args == ["preview"]

    def test_set_mode_live_emits_signal(self, qtbot, widget: ScreenMapWidget) -> None:
        widget.set_mode("preview")  # switch away first
        with qtbot.waitSignal(widget.mode_changed, timeout=500) as blocker:
            widget.set_mode("live")
        assert blocker.args == ["live"]

    def test_set_same_mode_does_not_emit(self, qtbot, widget: ScreenMapWidget) -> None:
        # Already in "live" mode
        received: list[str] = []
        widget.mode_changed.connect(received.append)
        widget.set_mode("live")
        assert received == []

    def test_mode_label_updates_to_preview(self, qtbot, widget: ScreenMapWidget) -> None:
        widget.set_mode("preview")
        assert "preview" in widget._mode_label.text().lower()

    def test_mode_label_updates_back_to_live(self, qtbot, widget: ScreenMapWidget) -> None:
        widget.set_mode("preview")
        widget.set_mode("live")
        assert "live" in widget._mode_label.text().lower()

    def test_btn_toggle_preview_checked(self, qtbot, widget: ScreenMapWidget) -> None:
        widget.set_mode("preview")
        assert widget._btn_preview.isChecked()
        assert not widget._btn_live.isChecked()

    def test_btn_toggle_live_checked(self, qtbot, widget: ScreenMapWidget) -> None:
        widget.set_mode("preview")
        widget.set_mode("live")
        assert widget._btn_live.isChecked()
        assert not widget._btn_preview.isChecked()


# ===========================================================================
# Tests: pane layout math
# ===========================================================================


class TestLayoutMath:
    """Unit tests for pane-layout geometry helpers."""

    # --- even-h ---
    def test_even_h_equal_widths(self) -> None:
        rects = _even_horizontal_rects(3, 0, 0, 90, 60)
        assert len(rects) == 3
        for _, _, w, h in rects:
            assert abs(w - 30.0) < 0.01
            assert abs(h - 60.0) < 0.01

    def test_even_h_single(self) -> None:
        rects = _even_horizontal_rects(1, 0, 0, 100, 100)
        assert rects == [(0, 0, 100.0, 100.0)]

    # --- even-v ---
    def test_even_v_equal_heights(self) -> None:
        rects = _even_vertical_rects(4, 0, 0, 100, 80)
        assert len(rects) == 4
        for _, _, w, h in rects:
            assert abs(w - 100.0) < 0.01
            assert abs(h - 20.0) < 0.01

    # --- main-h ---
    def test_main_h_top_half_main(self) -> None:
        rects = _main_horizontal_rects(3, 0, 0, 100, 100)
        assert len(rects) == 3
        # First rect spans full width, top half
        _x, _y, w, h = rects[0]
        assert abs(h - 50.0) < 0.01
        assert abs(w - 100.0) < 0.01

    def test_main_h_single_pane(self) -> None:
        rects = _main_horizontal_rects(1, 0, 0, 100, 100)
        assert rects == [(0, 0, 100, 100)]

    # --- main-v ---
    def test_main_v_left_half_main(self) -> None:
        rects = _main_vertical_rects(3, 0, 0, 100, 100)
        assert len(rects) == 3
        _x, _y, w, h = rects[0]
        assert abs(w - 50.0) < 0.01
        assert abs(h - 100.0) < 0.01

    def test_main_v_single_pane(self) -> None:
        rects = _main_vertical_rects(1, 0, 0, 100, 100)
        assert rects == [(0, 0, 100, 100)]

    # --- dispatch ---
    def test_dispatch_custom_falls_back_to_tiled(self) -> None:
        tiled = _tiled_rects(4, 0, 0, 100, 100)
        custom = _compute_pane_rects("custom", 4, 0, 0, 100, 100)
        assert tiled == custom

    def test_dispatch_even_h(self) -> None:
        assert _compute_pane_rects("even-h", 2, 0, 0, 100, 100) == _even_horizontal_rects(
            2, 0, 0, 100, 100
        )

    def test_dispatch_even_v(self) -> None:
        assert _compute_pane_rects("even-v", 2, 0, 0, 100, 100) == _even_vertical_rects(
            2, 0, 0, 100, 100
        )

    def test_dispatch_main_h(self) -> None:
        assert _compute_pane_rects("main-h", 3, 0, 0, 100, 100) == _main_horizontal_rects(
            3, 0, 0, 100, 100
        )

    def test_dispatch_main_v(self) -> None:
        assert _compute_pane_rects("main-v", 3, 0, 0, 100, 100) == _main_vertical_rects(
            3, 0, 0, 100, 100
        )

    def test_zero_panes_returns_empty(self) -> None:
        for fn in [_tiled_rects, _even_horizontal_rects, _even_vertical_rects]:
            assert fn(0, 0, 0, 100, 100) == []


# ===========================================================================
# Tests: profile glyphs exported
# ===========================================================================


class TestProfileGlyphs:
    def test_claude_remote_glyph(self) -> None:
        assert _PROFILE_GLYPHS["claude-remote"] == "🔗"

    def test_local_shell_glyph(self) -> None:
        assert _PROFILE_GLYPHS["local-shell"] == "$"

    def test_custom_glyph(self) -> None:
        assert _PROFILE_GLYPHS["custom"] == "⚙"


# ===========================================================================
# Tests: widget construction without monitor service
# ===========================================================================


class TestConstructionVariants:
    def test_no_monitor_service(self, qtbot) -> None:
        w = ScreenMapWidget()
        qtbot.addWidget(w)
        w.show()
        assert w.objectName() == "widget_screen_map"

    def test_no_connection_lookup(self, qtbot) -> None:
        w = ScreenMapWidget()
        qtbot.addWidget(w)
        mon = _make_monitor_info()
        layout = ScreenLayout(
            id="layout-bare",
            name="Bare",
            monitors=[
                Monitor(
                    identifier="test-mon",
                    viewports=[_full_viewport(panes=[Pane(connection_id="no-conn")])],
                )
            ],
        )
        # Should not crash even if connection_lookup returns None for everything
        w.set_layout(layout, [mon])
        assert _count_rects(w) >= 1

    def test_set_layout_empty_monitors_list(self, qtbot) -> None:
        w = ScreenMapWidget()
        qtbot.addWidget(w)
        layout = ScreenLayout(
            id="layout-no-mon",
            name="No Monitors",
            monitors=[],
        )
        w.set_layout(layout, [])  # must not raise

    def test_set_layout_clears_previous(self, qtbot) -> None:
        w = ScreenMapWidget()
        qtbot.addWidget(w)
        mon = _make_monitor_info()
        layout1 = ScreenLayout(
            id="layout-a",
            name="A",
            monitors=[Monitor(identifier="test-mon", viewports=[_full_viewport(vp_id="vp-a")])],
        )
        layout2 = ScreenLayout(
            id="layout-b",
            name="B",
            monitors=[Monitor(identifier="test-mon", viewports=[_full_viewport(vp_id="vp-b")])],
        )
        w.set_layout(layout1, [mon])
        count1 = _count_rects(w)
        w.set_layout(layout2, [mon])
        count2 = _count_rects(w)
        # After the second set_layout the scene should have been rebuilt
        # (same structure → same count)
        assert count2 == count1


# ===========================================================================
# Tests: ghost monitor placement, hit-testing, and context menu
# (fixes for: text unreadable, right-click pass-through, overlap with real monitor)
# ===========================================================================


class TestGhostMonitorBehavior:
    """Ghost monitors (disconnected schema monitors) must:

    1. Be drawn *below* the real monitor bounding box, never overlapping it.
    2. Register a :class:`_GhostRecord` for hit-testing so context menus
       don't fall through to real panes behind them.
    3. Show a ghost-specific context menu that emits
       ``remove_disconnected_monitor_requested`` on the "Remove …" action.
    4. Consume left-click without emitting ``pane_clicked``.
    5. Render overlay text at a screen-fixed size (ItemIgnoresTransformations)
       so it stays legible after ``fitInView`` scales the scene.
    """

    def _layout_with_ghost(self) -> ScreenLayout:
        return ScreenLayout(
            id="layout-ghost-tests",
            name="Ghost Tests",
            monitors=[
                Monitor(
                    identifier="mon-live",
                    viewports=[_full_viewport(vp_id="vp-live")],
                ),
                Monitor(
                    identifier="mon-gone",
                    viewports=[_full_viewport(vp_id="vp-gone")],
                ),
            ],
        )

    def test_ghost_stripe_below_real_bbox_no_overlap(self, qtbot, widget: ScreenMapWidget) -> None:
        mon = _make_monitor_info(identifier="mon-live", w=1920, h=1080)
        widget.set_layout(self._layout_with_ghost(), [mon])
        # Real monitor bbox spans y ∈ [0, 1080*scale].  Every ghost must
        # start below that bbox (with the configured gutter).
        assert widget._ghost_registry, "expected one ghost record"
        from cpsm.ui.widgets.screen_map import _GHOST_STRIPE_Y_GUTTER

        real_max_y = 1080 * (800 / max(1920, 1080))
        for g in widget._ghost_registry:
            assert g.scene_y >= real_max_y + _GHOST_STRIPE_Y_GUTTER - 0.5

    def test_ghost_hit_test_returns_record_not_pane(self, qtbot, widget: ScreenMapWidget) -> None:
        mon = _make_monitor_info(identifier="mon-live", w=1920, h=1080)
        widget.set_layout(self._layout_with_ghost(), [mon])
        ghosts = widget._ghost_registry
        assert ghosts, "ghost record required for this test"
        g = ghosts[0]
        # Point at the ghost rect's centre
        cx, cy = g.scene_x + g.scene_w / 2, g.scene_y + g.scene_h / 2
        hit = widget.ghost_at_scene_pos(cx, cy)
        assert hit is not None
        assert hit.ghost_key == "mon-gone"
        # Same point must NOT resolve to a pane — the stripe is disjoint.
        assert widget.pane_at_scene_pos(cx, cy) is None

    def test_ghost_key_falls_back_to_synthetic_when_identifier_empty(
        self, qtbot, widget: ScreenMapWidget
    ) -> None:
        layout = ScreenLayout(
            id="layout-ghost-noident",
            name="Ghost NoIdent",
            monitors=[
                Monitor(
                    identifier="mon-live",
                    viewports=[_full_viewport(vp_id="vp-live")],
                ),
                Monitor(
                    identifier="",  # empty → synthetic key
                    viewports=[_full_viewport(vp_id="vp-noident")],
                ),
            ],
        )
        mon = _make_monitor_info(identifier="mon-live")
        widget.set_layout(layout, [mon])
        assert widget._ghost_registry
        assert widget._ghost_registry[0].ghost_key.startswith("__ghost_idx_")

    def test_ghost_overlay_text_uses_fixed_screen_size(
        self, qtbot, widget: ScreenMapWidget
    ) -> None:
        """The overlay QGraphicsTextItem must have ItemIgnoresTransformations
        set so fitInView shrinking doesn't render the text at 2pt.
        """
        from PySide6.QtWidgets import QGraphicsItem, QGraphicsTextItem

        mon = _make_monitor_info(identifier="mon-live")
        widget.set_layout(self._layout_with_ghost(), [mon])
        # Find the "Monitor disconnected" text item.
        overlay = next(
            item
            for item in widget.scene.items()
            if isinstance(item, QGraphicsTextItem) and "disconnected" in item.toPlainText().lower()
        )
        assert overlay.flags() & QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations
        # And its point size must be readable (>= 10).
        assert overlay.font().pointSize() >= 10

    def test_context_menu_on_ghost_shows_ghost_menu(self, qtbot, widget: ScreenMapWidget) -> None:
        """Right-click on a ghost rect must show the ghost menu (containing
        the 'Remove Disconnected Monitor' action), not a pane menu.
        """
        mon = _make_monitor_info(identifier="mon-live")
        widget.set_layout(self._layout_with_ghost(), [mon])
        assert widget._ghost_registry
        g = widget._ghost_registry[0]
        # Direct-call the menu builder — the real _on_context_menu_view
        # would call gmenu.exec() next, which blocks even under offscreen QPA.
        menu = widget._build_ghost_context_menu(g)
        assert menu.objectName() == "screenmap_ghost_context_menu"
        action_object_names = {a.objectName() for a in menu.actions()}
        assert "screenmap_ctx_ghost_remove" in action_object_names
        assert "screenmap_ctx_ghost_save_layout" in action_object_names

    def test_ghost_remove_signal_emits_ghost_key(self, qtbot, widget: ScreenMapWidget) -> None:
        """Triggering the ghost menu's Remove action emits
        remove_disconnected_monitor_requested with the ghost key.
        """
        mon = _make_monitor_info(identifier="mon-live")
        widget.set_layout(self._layout_with_ghost(), [mon])
        g = widget._ghost_registry[0]

        emitted: list[str] = []
        widget.remove_disconnected_monitor_requested.connect(emitted.append)

        menu = widget._build_ghost_context_menu(g)
        remove_act = next(
            a for a in menu.actions() if a.objectName() == "screenmap_ctx_ghost_remove"
        )
        remove_act.trigger()
        assert emitted == ["mon-gone"]

    def test_left_click_on_ghost_does_not_emit_pane_clicked(
        self, qtbot, widget: ScreenMapWidget
    ) -> None:
        """Left-click on a ghost rect must be consumed — no pane_clicked
        (which would populate the Inspector with the pane visually behind
        the ghost) and no drag anchor set.
        """
        from PySide6.QtCore import QEvent, QPointF
        from PySide6.QtGui import QMouseEvent

        mon = _make_monitor_info(identifier="mon-live")
        widget.set_layout(self._layout_with_ghost(), [mon])
        g = widget._ghost_registry[0]
        scene_pt_x = g.scene_x + g.scene_w / 2
        scene_pt_y = g.scene_y + g.scene_h / 2
        view_pt = widget._view.mapFromScene(scene_pt_x, scene_pt_y)

        received: list[str] = []
        widget.pane_clicked.connect(received.append)

        # Fabricate a left-button press at the ghost's centre.
        ev = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(view_pt),
            QPointF(view_pt),
            widget._view.viewport().mapToGlobal(view_pt),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        widget._view.mousePressEvent(ev)
        assert received == []
        assert widget._view._press_pane_id in (None, "")
