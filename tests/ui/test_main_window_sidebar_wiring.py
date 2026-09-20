# -*- coding: utf-8 -*-
"""
Tests for sidebar tree double-click wiring.

Covers:
- Double-click on a connection child → ConnectionEditorDialog opens.
- Double-click on a group child → GroupEditorDialog opens.
- Double-click on a layout child → LayoutEditorDialog opens.
- Double-click on a category root → no editor opens.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog

from cpsm.data.schema import (
    ClaudeLocalConnection,
    ClaudeRemoteConnection,
    CpsmDocument,
    Group,
    Monitor,
    ScreenLayout,
    SshKey,
)
from cpsm.ui.main_window import MainWindow

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def ssh_key() -> SshKey:
    return SshKey(
        id="key-sb",
        name="Sidebar Key",
        type="ed25519",
        private_path="~/.ssh/id_ed25519_sb",
        public_path="~/.ssh/id_ed25519_sb.pub",
    )


@pytest.fixture()
def remote_conn(ssh_key: SshKey) -> ClaudeRemoteConnection:
    return ClaudeRemoteConnection(
        id="conn-sb",
        name="Sidebar Conn",
        launch_profile="claude-remote",
        host="sb.example.com",
        port=22,
        user="ubuntu",
        identity_file_ref="key-sb",
        project_folder="/opt/sb",
        claude_options="--resume",
    )


@pytest.fixture()
def local_conn() -> ClaudeLocalConnection:
    return ClaudeLocalConnection(
        id="conn-local",
        name="Local Conn",
        launch_profile="claude-local",
        project_folder="~/local",
        claude_options="--resume",
    )


@pytest.fixture()
def grp(remote_conn: ClaudeRemoteConnection) -> Group:
    return Group(id="grp-sb", name="Sidebar Group", members=["conn-sb"])


@pytest.fixture()
def layout() -> ScreenLayout:
    return ScreenLayout(
        id="lay-sb",
        name="Sidebar Layout",
        monitors=[Monitor(viewports=[])],
    )


@pytest.fixture()
def doc(
    ssh_key: SshKey,
    remote_conn: ClaudeRemoteConnection,
    local_conn: ClaudeLocalConnection,
    grp: Group,
    layout: ScreenLayout,
) -> CpsmDocument:
    return CpsmDocument(
        ssh_keys=[ssh_key],
        connections=[remote_conn, local_conn],
        groups=[grp],
        screen_layouts=[layout],
    )


@pytest.fixture()
def win(qtbot, doc: CpsmDocument) -> MainWindow:
    w = MainWindow(document=doc)
    qtbot.addWidget(w)
    w.show()
    return w


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_sidebar_item(win: MainWindow, category_data: str, item_id: str):
    """Return the QTreeWidgetItem with *item_id* under the category whose UserRole == category_data."""
    tree = win._session_list.tree
    root = tree.invisibleRootItem()
    for ci in range(root.childCount()):
        cat = root.child(ci)
        if cat.data(0, Qt.ItemDataRole.UserRole) == category_data:
            for ii in range(cat.childCount()):
                child = cat.child(ii)
                if child.data(0, Qt.ItemDataRole.UserRole) == item_id:
                    return child
    return None


def _find_category_item(win: MainWindow, category_data: str):
    """Return the top-level category QTreeWidgetItem."""
    tree = win._session_list.tree
    root = tree.invisibleRootItem()
    for ci in range(root.childCount()):
        cat = root.child(ci)
        if cat.data(0, Qt.ItemDataRole.UserRole) == category_data:
            return cat
    return None


# ---------------------------------------------------------------------------
# Tests — connection
# ---------------------------------------------------------------------------


def test_sidebar_double_click_connection_opens_editor(
    qtbot, win: MainWindow, remote_conn: ClaudeRemoteConnection
) -> None:
    """Double-clicking a connection in the sidebar opens ConnectionEditorDialog."""
    from cpsm.ui.dialogs.connection_editor import ConnectionEditorDialog

    opened: list[object] = []
    original_exec = ConnectionEditorDialog.exec

    def _fake_exec(self_dlg):
        opened.append(self_dlg)
        return QDialog.DialogCode.Rejected

    ConnectionEditorDialog.exec = _fake_exec  # type: ignore[method-assign]
    try:
        item = _find_sidebar_item(win, "category_cat_connections", "conn-sb")
        assert item is not None, "Connection sidebar item not found"
        win._on_sidebar_double_clicked(item, 0)
        assert len(opened) == 1, "ConnectionEditorDialog should have been opened once"
    finally:
        ConnectionEditorDialog.exec = original_exec  # type: ignore[method-assign]


# ---------------------------------------------------------------------------
# Tests — group
# ---------------------------------------------------------------------------


def test_sidebar_double_click_group_opens_editor(qtbot, win: MainWindow, grp: Group) -> None:
    """Double-clicking a group in the sidebar opens GroupEditorDialog."""
    from cpsm.ui.dialogs.group_editor import GroupEditorDialog

    opened: list[object] = []
    original_exec = GroupEditorDialog.exec

    def _fake_exec(self_dlg):
        opened.append(self_dlg)
        return QDialog.DialogCode.Rejected

    GroupEditorDialog.exec = _fake_exec  # type: ignore[method-assign]
    try:
        item = _find_sidebar_item(win, "category_cat_groups", "grp-sb")
        assert item is not None, "Group sidebar item not found"
        win._on_sidebar_double_clicked(item, 0)
        assert len(opened) == 1, "GroupEditorDialog should have been opened once"
    finally:
        GroupEditorDialog.exec = original_exec  # type: ignore[method-assign]


# ---------------------------------------------------------------------------
# Tests — layout
# ---------------------------------------------------------------------------


def test_sidebar_layout_category_not_in_visible_tree(
    qtbot, win: MainWindow, layout: ScreenLayout
) -> None:
    """Round C removed the Layouts category from the visible sidebar.
    Layouts are now an implementation detail of Groups (one auto-managed
    layout per group), not a separately-listed entity."""
    tree = win._session_list.tree
    # The Layouts category is constructed but not added to the tree
    for i in range(tree.topLevelItemCount()):
        cat = tree.topLevelItem(i)
        cat_id = cat.data(0, Qt.ItemDataRole.UserRole) or ""
        assert cat_id != "category_cat_layouts", (
            f"Layouts category should not appear in visible tree; saw {cat_id}"
        )


# ---------------------------------------------------------------------------
# Tests — category root (no-op)
# ---------------------------------------------------------------------------


def test_sidebar_double_click_category_root_does_not_open_editor(qtbot, win: MainWindow) -> None:
    """Double-clicking a category root item must not open any editor."""
    from cpsm.ui.dialogs.connection_editor import ConnectionEditorDialog
    from cpsm.ui.dialogs.group_editor import GroupEditorDialog
    from cpsm.ui.dialogs.layout_editor import LayoutEditorDialog

    opened: list[object] = []

    orig_ce = ConnectionEditorDialog.exec
    orig_ge = GroupEditorDialog.exec
    orig_le = LayoutEditorDialog.exec

    def _fake_exec(self_dlg):
        opened.append(self_dlg)
        return QDialog.DialogCode.Rejected

    ConnectionEditorDialog.exec = _fake_exec  # type: ignore[method-assign]
    GroupEditorDialog.exec = _fake_exec  # type: ignore[method-assign]
    LayoutEditorDialog.exec = _fake_exec  # type: ignore[method-assign]
    try:
        cat = _find_category_item(win, "category_cat_connections")
        assert cat is not None
        win._on_sidebar_double_clicked(cat, 0)
        assert len(opened) == 0, "No editor should open for a category root"
    finally:
        ConnectionEditorDialog.exec = orig_ce  # type: ignore[method-assign]
        GroupEditorDialog.exec = orig_ge  # type: ignore[method-assign]
        LayoutEditorDialog.exec = orig_le  # type: ignore[method-assign]
