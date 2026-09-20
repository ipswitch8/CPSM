# -*- coding: utf-8 -*-
"""
tests/ui/test_layout_editor_add_viewport.py — Gap C: adding viewports to a
monitor via right-click on the monitor rect.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import types

from PySide6.QtWidgets import QMenu

from cpsm.data.schema import (
    CpsmDocument,
    ScreenLayout,
)
from cpsm.data.schema import (
    Monitor as SchemaMonitor,
)
from cpsm.services.monitor_service import MonitorInfo
from cpsm.ui.dialogs.layout_editor import LayoutEditorDialog

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_monitor_info(
    identifier: str = "mon-1",
    name: str = "HDMI-1",
    qt_index: int = 0,
) -> MonitorInfo:
    return MonitorInfo(
        identifier=identifier,
        name=name,
        geometry=(0, 0, 1920, 1080),
        available_geometry=(0, 0, 1920, 1040),
        physical_size_mm=(527.0, 296.0),
        device_pixel_ratio=1.0,
        orientation="landscape",
        manufacturer="ACME",
        model="X1",
        serial="SN001",
        qt_index=qt_index,
    )


def _make_svc(*monitors: MonitorInfo) -> MagicMock:
    svc = MagicMock()
    svc.snapshot.return_value = list(monitors)
    return svc


def _layout_with_monitor(mon_info: MonitorInfo) -> ScreenLayout:
    """Return a layout containing one SchemaMonitor matching *mon_info*."""
    schema_mon = SchemaMonitor(
        identifier=mon_info.identifier,
        monitor_index_hint=mon_info.qt_index,
        viewports=[],
    )
    return ScreenLayout(id="test-layout", name="Test", monitors=[schema_mon])


def _open_dlg(
    qtbot,
    layout: ScreenLayout,
    monitor_service=None,
) -> LayoutEditorDialog:
    doc = CpsmDocument(screen_layouts=[layout])
    captured: list[QMenu] = []

    def fake_exec(self, menu, pos):
        captured.append(menu)

    dlg = LayoutEditorDialog(
        document=doc,
        layout=layout,
        is_new=False,
        monitor_service=monitor_service,
    )
    dlg._captured_menus = captured  # type: ignore[attr-defined]
    dlg._exec_menu = types.MethodType(fake_exec, dlg)  # type: ignore[method-assign]
    qtbot.addWidget(dlg)
    return dlg


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_monitor_menu_built(qtbot) -> None:
    """_build_monitor_menu returns a non-empty menu."""
    mon_info = _make_monitor_info()
    layout = _layout_with_monitor(mon_info)
    dlg = _open_dlg(qtbot, layout, monitor_service=_make_svc(mon_info))

    schema_mon = dlg._layout.monitors[0]
    menu = dlg._build_monitor_menu(schema_mon)

    assert not menu.isEmpty()
    assert menu.objectName() == "menu_layout_editor_monitor"


def test_monitor_menu_has_add_full_viewport(qtbot) -> None:
    """Monitor right-click menu includes 'Add full-screen viewport'."""
    mon_info = _make_monitor_info()
    layout = _layout_with_monitor(mon_info)
    dlg = _open_dlg(qtbot, layout, monitor_service=_make_svc(mon_info))

    schema_mon = dlg._layout.monitors[0]
    menu = dlg._build_monitor_menu(schema_mon)

    texts = [a.text() for a in menu.actions() if not a.isSeparator()]
    assert "Add full-screen viewport" in texts


def test_monitor_menu_has_half_left_and_right(qtbot) -> None:
    """Monitor menu includes half-screen left and right viewport options."""
    mon_info = _make_monitor_info()
    layout = _layout_with_monitor(mon_info)
    dlg = _open_dlg(qtbot, layout, monitor_service=_make_svc(mon_info))

    schema_mon = dlg._layout.monitors[0]
    menu = dlg._build_monitor_menu(schema_mon)

    texts = [a.text() for a in menu.actions() if not a.isSeparator()]
    assert "Add half-screen viewport (left)" in texts
    assert "Add half-screen viewport (right)" in texts


def test_add_full_screen_viewport_appends_viewport(qtbot) -> None:
    """'Add full-screen viewport' appends a viewport with {x:0,y:0,w:100,h:100}."""
    mon_info = _make_monitor_info()
    layout = _layout_with_monitor(mon_info)
    dlg = _open_dlg(qtbot, layout, monitor_service=_make_svc(mon_info))

    schema_mon = dlg._layout.monitors[0]
    assert len(schema_mon.viewports) == 0

    dlg._add_viewport(schema_mon, 0, 0, 100, 100)

    assert len(dlg._layout.monitors) == 1
    new_mon = dlg._layout.monitors[0]
    assert len(new_mon.viewports) == 1
    gp = new_mon.viewports[0].geometry_pct
    assert gp.x == 0
    assert gp.y == 0
    assert gp.w == 100
    assert gp.h == 100


def test_add_half_left_viewport_geometry(qtbot) -> None:
    """'Add half-screen viewport (left)' gives geometry {x:0,y:0,w:50,h:100}."""
    mon_info = _make_monitor_info()
    layout = _layout_with_monitor(mon_info)
    dlg = _open_dlg(qtbot, layout, monitor_service=_make_svc(mon_info))

    schema_mon = dlg._layout.monitors[0]
    dlg._add_viewport(schema_mon, 0, 0, 50, 100)

    new_mon = dlg._layout.monitors[0]
    gp = new_mon.viewports[0].geometry_pct
    assert gp.x == 0
    assert gp.w == 50


def test_add_half_right_viewport_geometry(qtbot) -> None:
    """'Add half-screen viewport (right)' gives geometry {x:50,y:0,w:50,h:100}."""
    mon_info = _make_monitor_info()
    layout = _layout_with_monitor(mon_info)
    dlg = _open_dlg(qtbot, layout, monitor_service=_make_svc(mon_info))

    schema_mon = dlg._layout.monitors[0]
    dlg._add_viewport(schema_mon, 50, 0, 50, 100)

    new_mon = dlg._layout.monitors[0]
    gp = new_mon.viewports[0].geometry_pct
    assert gp.x == 50
    assert gp.w == 50


def test_add_viewport_marks_dirty(qtbot) -> None:
    """Adding a viewport sets the dirty flag."""
    mon_info = _make_monitor_info()
    layout = _layout_with_monitor(mon_info)
    dlg = _open_dlg(qtbot, layout, monitor_service=_make_svc(mon_info))

    schema_mon = dlg._layout.monitors[0]
    assert not dlg._dirty
    dlg._add_viewport(schema_mon, 0, 0, 100, 100)
    assert dlg._dirty


def test_add_viewport_new_layout_is_immutable_copy(qtbot) -> None:
    """After _add_viewport, _layout is a new object (immutable copy)."""
    mon_info = _make_monitor_info()
    layout = _layout_with_monitor(mon_info)
    dlg = _open_dlg(qtbot, layout, monitor_service=_make_svc(mon_info))

    original_layout = dlg._layout
    schema_mon = dlg._layout.monitors[0]
    dlg._add_viewport(schema_mon, 0, 0, 100, 100)

    # Pydantic model_copy creates a new instance
    assert dlg._layout is not original_layout


def test_monitor_menu_has_remove_monitor(qtbot) -> None:
    """Monitor context menu includes 'Remove monitor' action."""
    mon_info = _make_monitor_info()
    layout = _layout_with_monitor(mon_info)
    dlg = _open_dlg(qtbot, layout, monitor_service=_make_svc(mon_info))

    schema_mon = dlg._layout.monitors[0]
    menu = dlg._build_monitor_menu(schema_mon)

    texts = [a.text() for a in menu.actions() if not a.isSeparator()]
    assert "Remove monitor" in texts


def test_remove_monitor_confirmed_removes(qtbot, monkeypatch) -> None:
    """Confirming 'Remove monitor' removes it from layout.monitors."""
    from PySide6.QtWidgets import QMessageBox

    mon_info = _make_monitor_info()
    layout = _layout_with_monitor(mon_info)
    dlg = _open_dlg(qtbot, layout, monitor_service=_make_svc(mon_info))

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *a, **kw: QMessageBox.StandardButton.Yes,
    )

    schema_mon = dlg._layout.monitors[0]
    assert len(dlg._layout.monitors) == 1
    dlg._remove_monitor(schema_mon)
    assert len(dlg._layout.monitors) == 0
