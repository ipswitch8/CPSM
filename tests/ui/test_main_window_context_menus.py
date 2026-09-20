# -*- coding: utf-8 -*-
"""Tests for right-click context menus on the sidebar and central tabs.

Implementation note: PySide6's `QMenu.exec` is a C++ method that cannot be
replaced via Python class assignment, so we instead patch
`MainWindow._exec_menu` (the Python-level helper that wraps `menu.exec(pos)`).
This lets us inspect the constructed menu without spinning a real event loop.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QMenu, QMessageBox

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
        id="key-ctx",
        name="CTX Key",
        type="ed25519",
        private_path="~/.ssh/id_ed25519_ctx",
        public_path="~/.ssh/id_ed25519_ctx.pub",
    )


@pytest.fixture()
def remote_conn(ssh_key: SshKey) -> ClaudeRemoteConnection:
    return ClaudeRemoteConnection(
        id="conn-ctx",
        name="CTX Conn",
        launch_profile="claude-remote",
        host="ctx.example.com",
        port=22,
        user="ubuntu",
        identity_file_ref="key-ctx",
        project_folder="/opt/ctx",
        claude_options="--resume",
    )


@pytest.fixture()
def local_conn() -> ClaudeLocalConnection:
    return ClaudeLocalConnection(
        id="conn-local2",
        name="Local Conn2",
        launch_profile="claude-local",
        project_folder="~/local2",
        claude_options="--resume",
    )


@pytest.fixture()
def grp(remote_conn: ClaudeRemoteConnection) -> Group:
    return Group(id="grp-ctx", name="CTX Group", members=["conn-ctx"])


@pytest.fixture()
def layout() -> ScreenLayout:
    return ScreenLayout(
        id="lay-ctx",
        name="CTX Layout",
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
def win(qtbot, doc: CpsmDocument, monkeypatch) -> MainWindow:
    # Stub _exec_menu so any context-menu-show path captures the menu instead
    # of spawning a blocking real popup.
    captured: list[QMenu] = []

    def fake_exec_menu(self, menu, _pos):
        captured.append(menu)

    monkeypatch.setattr(MainWindow, "_exec_menu", fake_exec_menu)

    w = MainWindow(document=doc)
    w._captured_menus = captured  # type: ignore[attr-defined]
    qtbot.addWidget(w)
    w.show()
    return w


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_sidebar_item(win: MainWindow, category_data: str, item_id: str):
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


def _action_texts(menu: QMenu) -> list[str]:
    return [a.text() for a in menu.actions() if not a.isSeparator()]


# ---------------------------------------------------------------------------
# Sidebar context menu — connection
# ---------------------------------------------------------------------------


def test_sidebar_connection_context_menu_has_expected_actions(
    win: MainWindow, remote_conn: ClaudeRemoteConnection
) -> None:
    """Right-click on sidebar connection child → menu contains Edit, Launch, Delete."""
    item = _find_sidebar_item(win, "category_cat_connections", "conn-ctx")
    assert item is not None, "Sidebar connection item not found"

    win._show_sidebar_item_menu(item, QPoint(0, 0))

    captured = win._captured_menus  # type: ignore[attr-defined]
    assert len(captured) == 1
    texts = _action_texts(captured[0])
    assert "Edit…" in texts
    assert "Launch" in texts
    assert "Delete" in texts


def test_sidebar_group_context_menu_has_expected_actions(win: MainWindow, grp: Group) -> None:
    """Right-click on sidebar group child → menu contains Edit, Launch group, Delete."""
    item = _find_sidebar_item(win, "category_cat_groups", "grp-ctx")
    assert item is not None, "Sidebar group item not found"

    win._show_sidebar_item_menu(item, QPoint(0, 0))

    captured = win._captured_menus  # type: ignore[attr-defined]
    assert len(captured) == 1
    texts = _action_texts(captured[0])
    assert "Edit…" in texts
    assert "Launch group" in texts
    assert "Delete" in texts


def test_connections_tab_context_menu_opens(
    win: MainWindow, remote_conn: ClaudeRemoteConnection
) -> None:
    """Connection menu builder produces an Edit action."""
    conn = win._document.connections[0]
    menu = win._build_connection_menu(conn, "menu_tab_connection")
    texts = _action_texts(menu)
    assert "Edit…" in texts


def test_groups_tab_context_menu_opens(win: MainWindow, grp: Group) -> None:
    """Group menu builder produces Edit + Launch group actions."""
    menu = win._build_group_menu(grp, "menu_tab_group")
    texts = _action_texts(menu)
    assert "Edit…" in texts
    assert "Launch group" in texts


def test_layouts_tab_context_menu_opens(win: MainWindow, layout: ScreenLayout) -> None:
    """Layout menu builder produces Edit + Delete actions."""
    menu = win._build_layout_menu(layout, "menu_tab_layout")
    texts = _action_texts(menu)
    assert "Edit…" in texts
    assert "Delete" in texts


# ---------------------------------------------------------------------------
# Delete connection — removes from doc
# ---------------------------------------------------------------------------


def test_delete_connection_removes_from_doc(
    win: MainWindow, remote_conn: ClaudeRemoteConnection, monkeypatch
) -> None:
    """Selecting Delete on a connection (after confirming) removes it from doc."""
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.Yes)
    assert any(c.id == "conn-ctx" for c in win._document.connections)
    win._delete_connection(remote_conn)
    assert not any(c.id == "conn-ctx" for c in win._document.connections)


def test_delete_connection_cancelled_does_not_remove(
    win: MainWindow, remote_conn: ClaudeRemoteConnection, monkeypatch
) -> None:
    """Cancelling Delete keeps the connection in the doc."""
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.No)
    win._delete_connection(remote_conn)
    assert any(c.id == "conn-ctx" for c in win._document.connections)


# ---------------------------------------------------------------------------
# Duplicate connection — appends copy with new id
# ---------------------------------------------------------------------------


def test_duplicate_connection_appends_copy(
    win: MainWindow, remote_conn: ClaudeRemoteConnection, monkeypatch
) -> None:
    """Selecting Duplicate on a connection appends a copy with a new id and
    opens the editor on the new copy. Stub the editor to a no-op so the
    modal dialog doesn't hang the test."""
    opened: list = []

    def _stub_editor(conn) -> None:
        opened.append(conn)

    monkeypatch.setattr(win, "_open_connection_editor", _stub_editor)
    original_count = len(win._document.connections)
    win._duplicate_connection(remote_conn)
    assert len(win._document.connections) == original_count + 1
    copy = win._document.connections[-1]
    assert copy.id != remote_conn.id
    assert "copy" in (copy.name or "").lower()
    # Editor was opened on the new copy, not the original.
    assert opened == [copy]


# ---------------------------------------------------------------------------
# Delete / Duplicate group + layout
# ---------------------------------------------------------------------------


def test_delete_group_removes_from_doc(win: MainWindow, grp: Group, monkeypatch) -> None:
    """Delete group (confirmed) removes it from doc."""
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.Yes)
    assert any(g.id == "grp-ctx" for g in win._document.groups)
    win._delete_group(grp)
    assert not any(g.id == "grp-ctx" for g in win._document.groups)


def test_duplicate_group_appends_copy(win: MainWindow, grp: Group) -> None:
    """Duplicate group appends a copy with a new id."""
    original_count = len(win._document.groups)
    win._duplicate_group(grp)
    assert len(win._document.groups) == original_count + 1
    copy = win._document.groups[-1]
    assert copy.id != grp.id
    assert "copy" in copy.name.lower()


def test_delete_layout_removes_from_doc(win: MainWindow, layout: ScreenLayout, monkeypatch) -> None:
    """Delete layout (confirmed) removes it from doc."""
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.Yes)
    assert any(la.id == "lay-ctx" for la in win._document.screen_layouts)
    win._delete_layout(layout)
    assert not any(la.id == "lay-ctx" for la in win._document.screen_layouts)
