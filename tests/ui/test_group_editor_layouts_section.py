# -*- coding: utf-8 -*-
"""
tests/ui/test_group_editor_layouts_section.py

Change 3 — GroupEditorDialog lists associated layouts with Delete.

Covers:
- Group editor lists the group's layouts.
- Delete button removes the layout from doc.screen_layouts.
- Delete clears default_layout_id if it matched.
- Layouts NOT associated with the group are not shown.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QListWidget, QMessageBox  # noqa: I001

from cpsm.data.schema import (
    ClaudeLocalConnection,
    CpsmDocument,
    GeometryPct,
    Group,
    Monitor,
    ScreenLayout,
    Viewport,
)
from cpsm.ui.dialogs.group_editor import GroupEditorDialog
from cpsm.ui.widgets.group_panel import ConnectionEntry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_vp(vid: str) -> Viewport:
    return Viewport(id=vid, geometry_pct=GeometryPct(x=0, y=0, w=100, h=100))


def _make_layout(lid: str, name: str) -> ScreenLayout:
    return ScreenLayout(id=lid, name=name, monitors=[Monitor(viewports=[_make_vp(f"vp-{lid}")])])


def _make_doc() -> CpsmDocument:
    conn = ClaudeLocalConnection(
        id="conn-x",
        name="Conn X",
        launch_profile="claude-local",
        project_folder="~/x",
        claude_options="--resume",
    )
    grp = Group(
        id="test-grp",
        name="Test Group",
        members=["conn-x"],
        default_layout_id="test-grp-default-layout",
    )
    layouts = [
        _make_layout("test-grp-default-layout", "Test Group default"),
        _make_layout("test-grp-extra", "Test Group Extra"),
        _make_layout("other-layout", "Other Layout"),  # not associated
    ]
    return CpsmDocument(connections=[conn], groups=[grp], screen_layouts=layouts)


def _make_dlg(qtbot, doc: CpsmDocument, group: Group) -> GroupEditorDialog:
    entries = [
        ConnectionEntry(
            conn_id=c.id, name=c.name or c.id, profile=c.launch_profile, other_groups=[]
        )
        for c in doc.connections
    ]
    layout_ids = [sl.id for sl in doc.screen_layouts]
    dlg = GroupEditorDialog(
        group_data=group.model_dump(mode="python"),
        all_connections=entries,
        available_layout_ids=layout_ids,
        is_new=False,
        doc=doc,
    )
    qtbot.addWidget(dlg)
    dlg.show()
    return dlg


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_layouts_list_widget_exists(qtbot) -> None:
    """GroupEditorDialog must contain a layouts list widget."""
    doc = _make_doc()
    grp = doc.groups[0]
    dlg = _make_dlg(qtbot, doc, grp)

    lst = dlg.findChild(QListWidget, "list_group_layouts")
    assert lst is not None


def test_group_editor_lists_associated_layouts(qtbot) -> None:
    """The layouts list must show layouts associated with the group."""
    doc = _make_doc()
    grp = doc.groups[0]
    dlg = _make_dlg(qtbot, doc, grp)

    lst = dlg.findChild(QListWidget, "list_group_layouts")
    assert lst is not None

    # Should show test-grp-default-layout and test-grp-extra but NOT other-layout
    ids_shown = []
    for i in range(lst.count()):
        item = lst.item(i)
        if item:
            ids_shown.append(item.data(0x0100))  # Qt.ItemDataRole.UserRole

    assert "test-grp-default-layout" in ids_shown
    assert "test-grp-extra" in ids_shown
    assert "other-layout" not in ids_shown


def test_delete_layout_removes_from_doc(qtbot, monkeypatch) -> None:
    """Clicking Delete (after confirming) removes the layout from doc.screen_layouts."""
    doc = _make_doc()
    grp = doc.groups[0]
    dlg = _make_dlg(qtbot, doc, grp)

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.Yes)

    initial_count = len(doc.screen_layouts)
    dlg._on_delete_layout("test-grp-extra")

    assert len(doc.screen_layouts) == initial_count - 1
    assert all(sl.id != "test-grp-extra" for sl in doc.screen_layouts)


def test_delete_default_layout_clears_default_layout_id(qtbot, monkeypatch) -> None:
    """Deleting the default layout clears default_layout_id in the combo."""
    doc = _make_doc()
    grp = doc.groups[0]
    dlg = _make_dlg(qtbot, doc, grp)

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.Yes)

    # Before: combo should have the default layout selected
    combo = dlg._combo_default_layout
    idx = combo.findData("test-grp-default-layout")
    assert idx >= 0
    combo.setCurrentIndex(idx)

    dlg._on_delete_layout("test-grp-default-layout")

    # After: combo should not have the deleted layout as current data
    current_data = combo.currentData() or ""
    assert current_data != "test-grp-default-layout"


def test_delete_cancelled_keeps_layout(qtbot, monkeypatch) -> None:
    """Cancelling the delete confirmation keeps the layout in the document."""
    doc = _make_doc()
    grp = doc.groups[0]
    dlg = _make_dlg(qtbot, doc, grp)

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.No)

    initial_count = len(doc.screen_layouts)
    dlg._on_delete_layout("test-grp-extra")

    assert len(doc.screen_layouts) == initial_count
