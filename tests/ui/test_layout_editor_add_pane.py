# -*- coding: utf-8 -*-
"""
tests/ui/test_layout_editor_add_pane.py — Gap C: adding panes to a viewport
via right-click, and the connection picker dialog.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import types

from PySide6.QtWidgets import QMenu

from cpsm.data.schema import (
    CpsmDocument,
    GeometryPct,
    LocalShellConnection,
    Pane,
    ScreenLayout,
    Viewport,
)
from cpsm.data.schema import (
    Monitor as SchemaMonitor,
)
from cpsm.ui.dialogs.layout_editor import LayoutEditorDialog, _ConnectionPickerDialog

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_local_conn(conn_id: str = "conn-a", name: str = "Shell A") -> LocalShellConnection:
    return LocalShellConnection(
        id=conn_id,
        name=name,
        launch_profile="local-shell",
        project_folder="~/test",
    )


def _make_viewport(vp_id: str = "vp-test") -> Viewport:
    return Viewport(
        id=vp_id,
        geometry_pct=GeometryPct(x=0, y=0, w=100, h=100),
        tmux_window_name=None,
        tmux_layout="tiled",
        panes=[],
    )


def _make_schema_mon(vp: Viewport, identifier: str = "mon-1") -> SchemaMonitor:
    return SchemaMonitor(identifier=identifier, viewports=[vp])


def _make_layout_with_viewport(
    vp: Viewport | None = None,
) -> tuple[ScreenLayout, SchemaMonitor, Viewport]:
    if vp is None:
        vp = _make_viewport()
    mon = _make_schema_mon(vp)
    layout = ScreenLayout(id="test-layout", name="Test", monitors=[mon])
    return layout, mon, vp


def _open_dlg(
    qtbot,
    layout: ScreenLayout,
    doc: CpsmDocument | None = None,
    monitor_service=None,
) -> LayoutEditorDialog:
    if doc is None:
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
# Tests — viewport menu
# ---------------------------------------------------------------------------


def test_viewport_menu_has_add_pane_connection(qtbot) -> None:
    """Viewport right-click menu includes 'Add pane (connection)…'."""
    layout, _, __ = _make_layout_with_viewport()
    dlg = _open_dlg(qtbot, layout)

    schema_mon = dlg._layout.monitors[0]
    vp_obj = schema_mon.viewports[0]
    menu = dlg._build_viewport_menu(schema_mon, vp_obj)
    texts = [a.text() for a in menu.actions() if not a.isSeparator()]
    assert "Add pane (connection)…" in texts


def test_viewport_menu_has_add_empty_pane(qtbot) -> None:
    """Viewport right-click menu includes 'Add empty pane'."""
    layout, _, __ = _make_layout_with_viewport()
    dlg = _open_dlg(qtbot, layout)

    schema_mon = dlg._layout.monitors[0]
    vp_obj = schema_mon.viewports[0]
    menu = dlg._build_viewport_menu(schema_mon, vp_obj)
    texts = [a.text() for a in menu.actions() if not a.isSeparator()]
    assert "Add empty pane" in texts


def test_viewport_menu_has_remove_viewport(qtbot) -> None:
    """Viewport right-click menu includes 'Remove viewport'."""
    layout, _, __ = _make_layout_with_viewport()
    dlg = _open_dlg(qtbot, layout)

    schema_mon = dlg._layout.monitors[0]
    vp_obj = schema_mon.viewports[0]
    menu = dlg._build_viewport_menu(schema_mon, vp_obj)
    texts = [a.text() for a in menu.actions() if not a.isSeparator()]
    assert "Remove viewport" in texts


# ---------------------------------------------------------------------------
# Tests — _add_empty_pane
# ---------------------------------------------------------------------------


def test_add_empty_pane_appends_pane(qtbot) -> None:
    """_add_empty_pane appends a Pane(connection_id=None) to the viewport."""
    layout, _mon, _vp = _make_layout_with_viewport()
    dlg = _open_dlg(qtbot, layout)

    schema_mon = dlg._layout.monitors[0]
    vp_obj = schema_mon.viewports[0]

    assert len(vp_obj.panes) == 0
    dlg._add_empty_pane(schema_mon, vp_obj)

    new_vp = dlg._layout.monitors[0].viewports[0]
    assert len(new_vp.panes) == 1
    assert new_vp.panes[0].connection_id is None


def test_add_empty_pane_marks_dirty(qtbot) -> None:
    layout, _mon, _vp = _make_layout_with_viewport()
    dlg = _open_dlg(qtbot, layout)

    schema_mon = dlg._layout.monitors[0]
    vp_obj = schema_mon.viewports[0]

    assert not dlg._dirty
    dlg._add_empty_pane(schema_mon, vp_obj)
    assert dlg._dirty


# ---------------------------------------------------------------------------
# Tests — pane with connection via picker
# ---------------------------------------------------------------------------


def test_add_pane_with_picker_opens_connection_picker(qtbot, monkeypatch) -> None:
    """_add_pane_with_picker opens _ConnectionPickerDialog."""
    conn = _make_local_conn("conn-a", "Shell A")
    layout, _mon, _vp = _make_layout_with_viewport()
    doc = CpsmDocument(connections=[conn], screen_layouts=[layout])
    dlg = _open_dlg(qtbot, layout, doc=doc)

    picker_shown: list[bool] = []

    def fake_pick(self) -> str | None:
        picker_shown.append(True)
        return "conn-a"

    dlg._pick_connection = types.MethodType(fake_pick, dlg)  # type: ignore[method-assign]

    schema_mon = dlg._layout.monitors[0]
    vp_obj = schema_mon.viewports[0]
    dlg._add_pane_with_picker(schema_mon, vp_obj)

    assert picker_shown, "_pick_connection was never called"


def test_add_pane_with_picker_appends_pane_with_connection(qtbot) -> None:
    """Selecting a connection in the picker appends a Pane with that connection_id."""
    conn = _make_local_conn("conn-b", "Shell B")
    layout, _mon, _vp = _make_layout_with_viewport()
    doc = CpsmDocument(connections=[conn], screen_layouts=[layout])
    dlg = _open_dlg(qtbot, layout, doc=doc)

    def fake_pick(self) -> str | None:
        return "conn-b"

    dlg._pick_connection = types.MethodType(fake_pick, dlg)  # type: ignore[method-assign]

    schema_mon = dlg._layout.monitors[0]
    vp_obj = schema_mon.viewports[0]
    dlg._add_pane_with_picker(schema_mon, vp_obj)

    new_vp = dlg._layout.monitors[0].viewports[0]
    assert len(new_vp.panes) == 1
    assert new_vp.panes[0].connection_id == "conn-b"


def test_add_pane_picker_cancel_does_not_append(qtbot) -> None:
    """Cancelling the picker does not append any pane."""
    conn = _make_local_conn("conn-c", "Shell C")
    layout, _mon, _vp = _make_layout_with_viewport()
    doc = CpsmDocument(connections=[conn], screen_layouts=[layout])
    dlg = _open_dlg(qtbot, layout, doc=doc)

    def fake_pick(self) -> str | None:
        return None  # user cancelled

    dlg._pick_connection = types.MethodType(fake_pick, dlg)  # type: ignore[method-assign]

    schema_mon = dlg._layout.monitors[0]
    vp_obj = schema_mon.viewports[0]
    dlg._add_pane_with_picker(schema_mon, vp_obj)

    new_vp = dlg._layout.monitors[0].viewports[0]
    assert len(new_vp.panes) == 0


# ---------------------------------------------------------------------------
# Tests — pane right-click menu
# ---------------------------------------------------------------------------


def _layout_with_pane(conn_id: str | None = "conn-a") -> tuple:
    pane = Pane(connection_id=conn_id)
    vp = Viewport(
        id="vp-with-pane",
        geometry_pct=GeometryPct(x=0, y=0, w=100, h=100),
        tmux_window_name=None,
        tmux_layout="tiled",
        panes=[pane],
    )
    mon = SchemaMonitor(identifier="mon-with-pane", viewports=[vp])
    layout = ScreenLayout(id="test-with-pane", name="Test With Pane", monitors=[mon])
    return layout, mon, vp, 0  # pane_idx = 0


def _doc_for_pane_layout(layout: ScreenLayout, conn_id: str | None) -> CpsmDocument:
    """Build a CpsmDocument that satisfies FK constraint for pane connection_id."""
    if conn_id is not None:
        conn = _make_local_conn(conn_id, f"Conn {conn_id}")
        return CpsmDocument(connections=[conn], screen_layouts=[layout])
    return CpsmDocument(screen_layouts=[layout])


def test_pane_menu_has_change_connection_when_assigned(qtbot) -> None:
    """Pane with connection_id gets 'Change connection…' in its menu."""
    layout, _mon, _vp, pane_idx = _layout_with_pane("conn-a")
    doc = _doc_for_pane_layout(layout, "conn-a")
    dlg = _open_dlg(qtbot, layout, doc=doc)

    schema_mon = dlg._layout.monitors[0]
    vp_obj = schema_mon.viewports[0]
    menu = dlg._build_pane_menu(schema_mon, vp_obj, pane_idx)

    texts = [a.text() for a in menu.actions() if not a.isSeparator()]
    assert "Change connection…" in texts


def test_pane_menu_has_clear_pane_when_assigned(qtbot) -> None:
    """Pane with connection_id gets 'Clear pane (make empty)' in its menu."""
    layout, _mon, _vp, pane_idx = _layout_with_pane("conn-a")
    doc = _doc_for_pane_layout(layout, "conn-a")
    dlg = _open_dlg(qtbot, layout, doc=doc)

    schema_mon = dlg._layout.monitors[0]
    vp_obj = schema_mon.viewports[0]
    menu = dlg._build_pane_menu(schema_mon, vp_obj, pane_idx)

    texts = [a.text() for a in menu.actions() if not a.isSeparator()]
    assert "Clear pane (make empty)" in texts


def test_pane_menu_has_set_connection_when_empty(qtbot) -> None:
    """Empty pane (connection_id=None) gets 'Set connection…' in its menu."""
    layout, _mon, _vp, pane_idx = _layout_with_pane(None)
    doc = _doc_for_pane_layout(layout, None)
    dlg = _open_dlg(qtbot, layout, doc=doc)

    schema_mon = dlg._layout.monitors[0]
    vp_obj = schema_mon.viewports[0]
    menu = dlg._build_pane_menu(schema_mon, vp_obj, pane_idx)

    texts = [a.text() for a in menu.actions() if not a.isSeparator()]
    assert "Set connection…" in texts


def test_pane_menu_has_remove_pane(qtbot) -> None:
    """All pane menus include 'Remove pane'."""
    layout, _mon, _vp, pane_idx = _layout_with_pane("conn-a")
    doc = _doc_for_pane_layout(layout, "conn-a")
    dlg = _open_dlg(qtbot, layout, doc=doc)

    schema_mon = dlg._layout.monitors[0]
    vp_obj = schema_mon.viewports[0]
    menu = dlg._build_pane_menu(schema_mon, vp_obj, pane_idx)

    texts = [a.text() for a in menu.actions() if not a.isSeparator()]
    assert "Remove pane" in texts


def test_clear_pane_sets_connection_id_to_none(qtbot) -> None:
    """_clear_pane sets pane.connection_id to None."""
    layout, _mon, _vp, pane_idx = _layout_with_pane("conn-a")
    doc = _doc_for_pane_layout(layout, "conn-a")
    dlg = _open_dlg(qtbot, layout, doc=doc)

    schema_mon = dlg._layout.monitors[0]
    vp_obj = schema_mon.viewports[0]

    assert vp_obj.panes[0].connection_id == "conn-a"
    dlg._clear_pane(schema_mon, vp_obj, pane_idx)

    new_vp = dlg._layout.monitors[0].viewports[0]
    assert new_vp.panes[0].connection_id is None


def test_remove_pane_removes_it_from_viewport(qtbot) -> None:
    """_remove_pane removes the pane from the viewport."""
    layout, _mon, _vp, pane_idx = _layout_with_pane("conn-a")
    doc = _doc_for_pane_layout(layout, "conn-a")
    dlg = _open_dlg(qtbot, layout, doc=doc)

    schema_mon = dlg._layout.monitors[0]
    vp_obj = schema_mon.viewports[0]

    assert len(vp_obj.panes) == 1
    dlg._remove_pane(schema_mon, vp_obj, pane_idx)

    new_vp = dlg._layout.monitors[0].viewports[0]
    assert len(new_vp.panes) == 0


# ---------------------------------------------------------------------------
# Tests — _ConnectionPickerDialog
# ---------------------------------------------------------------------------


def test_connection_picker_has_correct_object_name(qtbot) -> None:
    """_ConnectionPickerDialog has objectName 'dlg_pick_connection'."""
    conn = _make_local_conn()
    doc = CpsmDocument(connections=[conn])
    dlg = _ConnectionPickerDialog(document=doc)
    qtbot.addWidget(dlg)
    assert dlg.objectName() == "dlg_pick_connection"


def test_connection_picker_lists_all_connections(qtbot) -> None:
    """Connection picker lists all connections in the document."""
    conn_a = _make_local_conn("conn-a", "Shell A")
    conn_b = _make_local_conn("conn-b", "Shell B")
    doc = CpsmDocument(connections=[conn_a, conn_b])
    dlg = _ConnectionPickerDialog(document=doc)
    qtbot.addWidget(dlg)

    assert dlg._list.count() == 2
    ids = [dlg._list.item(i).data(256) for i in range(dlg._list.count())]
    assert "conn-a" in ids
    assert "conn-b" in ids


def test_connection_picker_returns_selected_id(qtbot) -> None:
    """Clicking OK with a selection returns the selected connection_id."""
    conn = _make_local_conn("conn-pick", "Shell Pick")
    doc = CpsmDocument(connections=[conn])
    dlg = _ConnectionPickerDialog(document=doc)
    qtbot.addWidget(dlg)

    dlg._list.setCurrentRow(0)
    dlg._on_ok()

    assert dlg.selected_connection_id() == "conn-pick"


def test_connection_picker_returns_none_when_nothing_selected(qtbot) -> None:
    """Without selecting an item, selected_connection_id() returns None."""
    conn = _make_local_conn()
    doc = CpsmDocument(connections=[conn])
    dlg = _ConnectionPickerDialog(document=doc)
    qtbot.addWidget(dlg)

    # Do not select any item; call _on_ok
    dlg._list.clearSelection()
    dlg._on_ok()

    assert dlg.selected_connection_id() is None
