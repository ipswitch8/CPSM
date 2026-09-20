# -*- coding: utf-8 -*-
"""
tests/ui/test_layout_editor_add_monitor.py — Gap C: adding monitors from the
system via right-click on the empty canvas.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QMenu

from cpsm.data.schema import CpsmDocument, ScreenLayout
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


def _make_monitor_service(*monitors: MonitorInfo) -> MagicMock:
    svc = MagicMock()
    svc.snapshot.return_value = list(monitors)
    return svc


def _blank_layout() -> ScreenLayout:
    return ScreenLayout(id="blank-layout", name="Blank Layout", monitors=[])


def _open_dlg(
    qtbot,
    layout: ScreenLayout | None = None,
    monitor_service=None,
    doc: CpsmDocument | None = None,
) -> LayoutEditorDialog:
    if layout is None:
        layout = _blank_layout()
    if doc is None:
        doc = CpsmDocument(screen_layouts=[layout])

    # Patch _exec_menu so menus don't block
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
    monkeypatch_exec(dlg, fake_exec)
    qtbot.addWidget(dlg)
    return dlg


def monkeypatch_exec(dlg: LayoutEditorDialog, fake_exec) -> None:
    """Replace _exec_menu on a specific instance."""
    import types

    dlg._exec_menu = types.MethodType(fake_exec, dlg)  # type: ignore[method-assign]


# ---------------------------------------------------------------------------
# Tests — empty canvas right-click shows monitor submenu
# ---------------------------------------------------------------------------


def test_empty_canvas_menu_built_when_no_monitors_in_layout(qtbot) -> None:
    """Right-click on empty canvas (no monitors in layout) builds the canvas menu."""
    mon = _make_monitor_info("mon-x", "VGA-1", 0)
    svc = _make_monitor_service(mon)
    dlg = _open_dlg(qtbot, monitor_service=svc)

    # Invoke context menu handler with a scene position that hits no monitor
    dlg._on_screen_map_context_menu(QPoint(5, 5))

    captured = dlg._captured_menus  # type: ignore[attr-defined]
    assert len(captured) >= 1, "No menu was captured"
    menu = captured[0]
    assert menu.objectName() == "menu_layout_editor_canvas"


def test_empty_canvas_menu_has_add_monitor_submenu(qtbot) -> None:
    """The canvas menu includes an 'Add monitor from system' submenu."""
    mon = _make_monitor_info("mon-x", "VGA-1", 0)
    svc = _make_monitor_service(mon)
    dlg = _open_dlg(qtbot, monitor_service=svc)

    dlg._on_screen_map_context_menu(QPoint(5, 5))

    captured = dlg._captured_menus  # type: ignore[attr-defined]
    assert captured, "No menu was captured"
    menu = captured[0]

    submenu_names = [a.text() for a in menu.actions()]
    # The submenu action text should mention "Add monitor from system"
    assert any("Add monitor from system" in t for t in submenu_names), (
        f"Expected 'Add monitor from system' in menu actions, got: {submenu_names}"
    )


def test_empty_canvas_add_monitor_submenu_lists_available_monitors(qtbot) -> None:
    """The submenu lists monitors from the service not yet in the layout."""
    mon1 = _make_monitor_info("mon-1", "HDMI-1", 0)
    mon2 = _make_monitor_info("mon-2", "DP-1", 1)
    svc = _make_monitor_service(mon1, mon2)
    dlg = _open_dlg(qtbot, monitor_service=svc)

    # Build menu directly
    menu = dlg._build_empty_canvas_menu()

    # The menu has one submenu action; get the submenu
    submenu = None
    for action in menu.actions():
        if action.menu() is not None:
            submenu = action.menu()
            break
    assert submenu is not None, "No submenu found"

    sub_texts = [a.text() for a in submenu.actions() if not a.isSeparator()]
    assert any("HDMI-1" in t for t in sub_texts), f"mon1 not in submenu: {sub_texts}"
    assert any("DP-1" in t for t in sub_texts), f"mon2 not in submenu: {sub_texts}"


def test_add_monitor_appends_to_layout_monitors(qtbot) -> None:
    """Selecting a monitor from the submenu appends a Monitor to layout.monitors."""
    mon = _make_monitor_info("mon-sel", "HDMI-1", 0)
    svc = _make_monitor_service(mon)
    dlg = _open_dlg(qtbot, monitor_service=svc)

    assert len(dlg._layout.monitors) == 0

    dlg._add_monitor_from_info(mon)

    assert len(dlg._layout.monitors) == 1
    added = dlg._layout.monitors[0]
    assert added.identifier == "mon-sel"


def test_add_monitor_sets_monitor_index_hint(qtbot) -> None:
    """Added Monitor carries the qt_index as monitor_index_hint."""
    mon = _make_monitor_info("mon-idx", "eDP-1", 3)
    svc = _make_monitor_service(mon)
    dlg = _open_dlg(qtbot, monitor_service=svc)

    dlg._add_monitor_from_info(mon)

    added = dlg._layout.monitors[0]
    assert added.monitor_index_hint == 3


def test_add_monitor_already_in_layout_not_listed_in_submenu(qtbot) -> None:
    """Monitors already in the layout are excluded from the add-monitor submenu."""
    from cpsm.data.schema import Monitor as SchemaMonitor

    mon = _make_monitor_info("mon-used", "HDMI-1", 0)
    # Layout already has this monitor
    existing_mon = SchemaMonitor(identifier="mon-used", viewports=[])
    layout = ScreenLayout(id="test", name="Test", monitors=[existing_mon])
    svc = _make_monitor_service(mon)
    dlg = _open_dlg(qtbot, layout=layout, monitor_service=svc)

    menu = dlg._build_empty_canvas_menu()
    submenu = None
    for action in menu.actions():
        if action.menu() is not None:
            submenu = action.menu()
            break
    assert submenu is not None

    sub_texts = [a.text() for a in submenu.actions() if not a.isSeparator()]
    # Should not list mon-used since it's already in the layout
    assert not any("mon-used" in t for t in sub_texts), (
        f"Already-added monitor appeared in submenu: {sub_texts}"
    )


def test_add_monitor_marks_dirty(qtbot) -> None:
    """After adding a monitor, the dialog is marked dirty."""
    mon = _make_monitor_info("mon-dirty", "HDMI-1", 0)
    svc = _make_monitor_service(mon)
    dlg = _open_dlg(qtbot, monitor_service=svc)

    assert not dlg._dirty
    dlg._add_monitor_from_info(mon)
    assert dlg._dirty
