# -*- coding: utf-8 -*-
"""
tests/ui/test_layout_editor_population.py — Gap B: ScreenMapWidget population.

Verifies that LayoutEditorDialog always calls set_layout() on its embedded
ScreenMapWidget after construction, both when the layout is non-empty and
when it is blank (no monitors).
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


from cpsm.data.schema import (
    CpsmDocument,
    GeometryPct,
    ScreenLayout,
    Viewport,
)
from cpsm.data.schema import (
    Monitor as SchemaMonitor,
)
from cpsm.ui.dialogs.layout_editor import LayoutEditorDialog

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_monitor_service(monitors: list) -> MagicMock:
    svc = MagicMock()
    svc.snapshot.return_value = monitors
    return svc


def _make_non_empty_layout() -> ScreenLayout:
    """Return a ScreenLayout that contains a monitor and a viewport."""
    vp = Viewport(
        id="vp-test",
        geometry_pct=GeometryPct(x=0, y=0, w=100, h=100),
        tmux_window_name="main",
        tmux_layout="tiled",
        panes=[],
    )
    mon = SchemaMonitor(identifier="test-monitor", viewports=[vp])
    return ScreenLayout(id="test-layout", name="Test Layout", monitors=[mon])


def _make_blank_layout() -> ScreenLayout:
    """Return a ScreenLayout with no monitors."""
    return ScreenLayout(id="blank-layout", name="Blank Layout", monitors=[])


def _open_dlg(qtbot, layout: ScreenLayout, monitor_service=None) -> LayoutEditorDialog:
    doc = CpsmDocument(screen_layouts=[layout])
    dlg = LayoutEditorDialog(
        document=doc,
        layout=layout,
        is_new=False,
        monitor_service=monitor_service,
    )
    qtbot.addWidget(dlg)
    return dlg


# ---------------------------------------------------------------------------
# Tests — set_layout always called
# ---------------------------------------------------------------------------


def test_non_empty_layout_calls_set_layout(qtbot) -> None:
    """LayoutEditorDialog with a non-empty layout calls set_layout on its screen map."""
    layout = _make_non_empty_layout()
    svc = _make_monitor_service([])

    set_layout_calls: list = []

    with patch.object(
        LayoutEditorDialog,
        "_redraw_screen_map",
        lambda self: set_layout_calls.append(self._layout),
    ):
        # The initial call happens in _populate via _screen_map.set_layout directly
        pass

    # Instead, spy on the screen_map.set_layout after construction
    dlg = _open_dlg(qtbot, layout, monitor_service=svc)

    # _populate was already called; verify the screen map has layout data
    assert dlg._screen_map._layout_data is not None, (
        "set_layout was never called on the embedded ScreenMapWidget"
    )
    assert dlg._screen_map._layout_data.id == "test-layout"


def test_non_empty_layout_screen_map_receives_correct_layout(qtbot) -> None:
    """The embedded screen map's _layout_data matches the dialog's layout."""
    layout = _make_non_empty_layout()
    dlg = _open_dlg(qtbot, layout)

    assert dlg._screen_map._layout_data is not None
    assert dlg._screen_map._layout_data.id == layout.id


def test_blank_layout_still_calls_set_layout(qtbot) -> None:
    """LayoutEditorDialog with an empty layout still calls set_layout (not skipped)."""
    layout = _make_blank_layout()
    svc = _make_monitor_service([])

    dlg = _open_dlg(qtbot, layout, monitor_service=svc)

    # Even a blank layout must result in _layout_data being set (not None)
    assert dlg._screen_map._layout_data is not None, (
        "set_layout was not called for a blank (no-monitor) layout"
    )
    assert dlg._screen_map._layout_data.id == "blank-layout"


def test_blank_layout_monitors_come_from_service(qtbot) -> None:
    """When a monitor service is provided, its snapshot() is passed to set_layout."""
    from cpsm.services.monitor_service import MonitorInfo

    fake_monitor = MonitorInfo(
        identifier="fake-monitor",
        name="FAKE",
        geometry=(0, 0, 1920, 1080),
        available_geometry=(0, 0, 1920, 1040),
        physical_size_mm=(527.0, 296.0),
        device_pixel_ratio=1.0,
        orientation="landscape",
        manufacturer="ACME",
        model="X1",
        serial="SN001",
        qt_index=0,
    )
    layout = _make_blank_layout()
    svc = _make_monitor_service([fake_monitor])

    dlg = _open_dlg(qtbot, layout, monitor_service=svc)

    # Monitors were passed through; screen map has them cached
    assert dlg._screen_map._monitors == [fake_monitor]


def test_no_monitor_service_passes_empty_monitors(qtbot) -> None:
    """Without a monitor service, monitors list is empty but set_layout is still called."""
    layout = _make_blank_layout()
    dlg = _open_dlg(qtbot, layout, monitor_service=None)

    assert dlg._screen_map._layout_data is not None
    assert dlg._screen_map._monitors == []
