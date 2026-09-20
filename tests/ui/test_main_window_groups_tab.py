# -*- coding: utf-8 -*-
"""
Tests for Bug 3: Groups tab no longer shows a placeholder.

Covers:
- Groups tab now lists groups.
- Double-click → GroupEditorDialog opens.
- New Group action opens the dialog for a new group.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QDialog, QLabel, QListView

from cpsm.data.schema import (
    ClaudeLocalConnection,
    CpsmDocument,
    Group,
)
from cpsm.ui.main_window import MainWindow

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def two_groups_doc():
    conn1 = ClaudeLocalConnection(
        id="conn-a",
        name="Conn A",
        launch_profile="claude-local",
        project_folder="~/a",
        claude_options="--resume",
    )
    conn2 = ClaudeLocalConnection(
        id="conn-b",
        name="Conn B",
        launch_profile="claude-local",
        project_folder="~/b",
        claude_options="--resume",
    )
    grp1 = Group(id="grp-one", name="Group One", members=["conn-a"])
    grp2 = Group(id="grp-two", name="Group Two", members=["conn-a", "conn-b"])
    return CpsmDocument(connections=[conn1, conn2], groups=[grp1, grp2])


@pytest.fixture()
def win(qtbot, two_groups_doc):
    w = MainWindow(document=two_groups_doc)
    qtbot.addWidget(w)
    w.show()
    return w


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_groups_tab_has_listview(win: MainWindow) -> None:
    """Groups tab must contain a QListView (not just a placeholder label)."""
    view = win.findChild(QListView, "listview_groups")
    assert view is not None, "listview_groups not found"


def test_groups_tab_lists_groups(win: MainWindow, two_groups_doc) -> None:
    """Groups list must show one row per group."""
    model = win._groups_model
    assert model.rowCount() == len(two_groups_doc.groups)


def test_groups_tab_no_placeholder_text(win: MainWindow) -> None:
    """Groups tab must not contain 'Coming in Phase 12' text."""
    # Placeholder label from old implementation should not exist
    lbl = win.findChild(QLabel, "label_placeholder_groups")
    # Either absent or not containing placeholder text
    if lbl is not None:
        assert "Coming in Phase 12" not in lbl.text()


def test_groups_tab_row_text_contains_id_and_name(win: MainWindow, two_groups_doc) -> None:
    """Each row in the groups list should mention the group id and name."""
    model = win._groups_model
    for i, grp in enumerate(two_groups_doc.groups):
        text = model.item(i).text()
        assert grp.id in text
        assert grp.name in text


def test_groups_tab_row_text_contains_member_count(win: MainWindow, two_groups_doc) -> None:
    """Each row should mention member count."""
    model = win._groups_model
    # grp-one has 1 member
    text_0 = model.item(0).text()
    assert "1" in text_0
    # grp-two has 2 members
    text_1 = model.item(1).text()
    assert "2" in text_1


def test_groups_double_click_opens_group_editor(qtbot, win: MainWindow) -> None:
    """Double-clicking a group row opens GroupEditorDialog."""
    from cpsm.ui.dialogs.group_editor import GroupEditorDialog

    opened: list[GroupEditorDialog] = []
    original_exec = GroupEditorDialog.exec

    def _fake_exec(self_dlg: GroupEditorDialog) -> int:
        opened.append(self_dlg)
        return QDialog.DialogCode.Rejected

    GroupEditorDialog.exec = _fake_exec  # type: ignore[method-assign]
    try:
        index = win._groups_model.index(0, 0)
        win._on_group_double_clicked(index)

        assert len(opened) == 1
    finally:
        GroupEditorDialog.exec = original_exec  # type: ignore[method-assign]


def test_groups_double_click_accept_adds_to_document(
    qtbot, win: MainWindow, two_groups_doc
) -> None:
    """On dialog accept, the group in self._document is updated."""
    from cpsm.ui.dialogs.group_editor import GroupEditorDialog

    original_exec = GroupEditorDialog.exec
    original_get_data = GroupEditorDialog.get_group_data

    updated_data = two_groups_doc.groups[0].model_dump(mode="python")
    updated_data["name"] = "Updated Group Name"

    def _fake_exec(self_dlg: GroupEditorDialog) -> int:
        return QDialog.DialogCode.Accepted

    def _fake_get_data(self_dlg: GroupEditorDialog) -> dict:
        return updated_data

    GroupEditorDialog.exec = _fake_exec  # type: ignore[method-assign]
    GroupEditorDialog.get_group_data = _fake_get_data  # type: ignore[method-assign]
    try:
        index = win._groups_model.index(0, 0)
        win._on_group_double_clicked(index)

        assert win._document.groups[0].name == "Updated Group Name"
    finally:
        GroupEditorDialog.exec = original_exec  # type: ignore[method-assign]
        GroupEditorDialog.get_group_data = original_get_data  # type: ignore[method-assign]


def test_new_group_action_opens_group_editor_empty(qtbot, win: MainWindow) -> None:
    """Triggering action_new_group opens GroupEditorDialog with is_new=True."""
    from cpsm.ui.dialogs.group_editor import GroupEditorDialog

    opened: list[GroupEditorDialog] = []
    init_kwargs: list[dict] = []
    original_init = GroupEditorDialog.__init__
    original_exec = GroupEditorDialog.exec

    def _capture_init(self_dlg, parent=None, **kwargs):
        init_kwargs.append(kwargs)
        original_init(self_dlg, parent=parent, **kwargs)

    def _fake_exec(self_dlg: GroupEditorDialog) -> int:
        opened.append(self_dlg)
        return QDialog.DialogCode.Rejected

    GroupEditorDialog.__init__ = _capture_init  # type: ignore[method-assign]
    GroupEditorDialog.exec = _fake_exec  # type: ignore[method-assign]
    try:
        win.action_new_group.trigger()

        assert len(opened) == 1
        assert init_kwargs[0].get("is_new") is True
    finally:
        GroupEditorDialog.__init__ = original_init  # type: ignore[method-assign]
        GroupEditorDialog.exec = original_exec  # type: ignore[method-assign]
