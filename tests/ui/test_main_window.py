# -*- coding: utf-8 -*-
"""
pytest-qt smoke tests for cpsm.ui.main_window.MainWindow.

Spec section: §3.1, §3.3

All tests run with QT_QPA_PLATFORM=offscreen (set in conftest.py).
"""

from __future__ import annotations

import os

# QT_QPA_PLATFORM must be set before PySide6 is imported — keep this block last.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: I001  (imports after env setup are intentional)
from PySide6.QtWidgets import (
    QDialog,
    QDockWidget,
    QTabWidget,
    QTreeView,
)

from cpsm.data.schema import (
    ClaudeLocalConnection,
    ClaudeRemoteConnection,
    CpsmDocument,
    Group,
    SshKey,
)
from cpsm.ui.main_window import MainWindow


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def window(qtbot):  # type: ignore[no-untyped-def]
    """Instantiate a MainWindow with an empty document and show it."""
    win = MainWindow()
    qtbot.addWidget(win)
    win.show()
    return win


@pytest.fixture()
def small_doc() -> CpsmDocument:
    """Return a small CpsmDocument with two connections and one group."""
    # Include the ssh_key so FK validation passes for identity_file_ref
    key = SshKey(
        id="key-prod-2026",
        name="Production ed25519",
        type="ed25519",
        private_path="~/.ssh/id_ed25519_prod",
        public_path="~/.ssh/id_ed25519_prod.pub",
    )
    conn1 = ClaudeRemoteConnection(
        id="web01",
        name="WebApp Frontend",
        launch_profile="claude-remote",
        host="dev.example.com",
        user="ubuntu",
        identity_file_ref="key-prod-2026",
        project_folder="/opt/webapp",
        claude_options="--resume",
    )
    conn2 = ClaudeLocalConnection(
        id="dotfiles",
        name="Dotfiles",
        launch_profile="claude-local",
        project_folder="~/projects/dotfiles",
        claude_options="--resume",
    )
    grp = Group(id="project-1", name="Project 1", members=["web01", "dotfiles"])
    return CpsmDocument(ssh_keys=[key], connections=[conn1, conn2], groups=[grp])


@pytest.fixture()
def populated_window(qtbot, small_doc: CpsmDocument):  # type: ignore[no-untyped-def]
    """MainWindow loaded with small_doc."""
    win = MainWindow(document=small_doc)
    qtbot.addWidget(win)
    win.show()
    return win


# ---------------------------------------------------------------------------
# Basic smoke tests
# ---------------------------------------------------------------------------


def test_main_window_opens(window: MainWindow) -> None:
    """MainWindow must be visible after show()."""
    assert window.isVisible()


def test_main_window_closes_cleanly(qtbot) -> None:  # type: ignore[no-untyped-def]
    """Closing MainWindow must not raise any exception."""
    win = MainWindow()
    qtbot.addWidget(win)
    win.show()
    win.close()
    assert not win.isVisible()


# ---------------------------------------------------------------------------
# Layout elements
# ---------------------------------------------------------------------------


def test_sidebar_tree_present(window: MainWindow) -> None:
    """findChild(QTreeView, 'sidebar_tree') must return the sidebar tree."""
    tree = window.findChild(QTreeView, "sidebar_tree")
    assert tree is not None


def test_main_tabwidget_has_one_tab(window: MainWindow) -> None:
    """Round B reduced the tab widget to a single Screens tab; Connections,
    Groups, and Active Sessions tabs were removed in favour of the left
    sidebar driving everything."""
    tabs = window.findChild(QTabWidget, "tabwidget_main")
    assert tabs is not None
    assert tabs.count() == 1


def test_tab_bar_hidden_for_single_tab(window: MainWindow) -> None:
    """With only one tab, the tab bar must be hidden so it doesn't take up
    a row of unused chrome above the canvas."""
    tabs = window.findChild(QTabWidget, "tabwidget_main")
    assert tabs is not None
    assert tabs.tabBar().isHidden()


def test_status_bar_present(window: MainWindow) -> None:
    """statusBar() must be non-null and the four labelled sub-widgets must exist."""
    bar = window.statusBar()
    assert bar is not None
    assert window.findChild(type(window._statusbar_backend), "statusbar_backend") is not None
    assert window.findChild(type(window._statusbar_active), "statusbar_active") is not None
    assert window.findChild(type(window._statusbar_valid), "statusbar_valid") is not None
    assert window.findChild(type(window._statusbar_conflicts), "statusbar_conflicts") is not None


def test_inspector_dock_present(window: MainWindow) -> None:
    """Inspector dock must exist with the correct objectName."""
    dock = window.findChild(QDockWidget, "dock_inspector")
    assert dock is not None


# ---------------------------------------------------------------------------
# F4 inspector toggle
# ---------------------------------------------------------------------------


def test_inspector_dock_toggle_f4(qtbot, window: MainWindow) -> None:
    """Pressing F4 hides the inspector; pressing again shows it."""
    dock = window.findChild(QDockWidget, "dock_inspector")
    assert dock is not None

    initial_visible = dock.isVisible()

    # Simulate F4 key press via the action
    window.action_toggle_inspector.trigger()
    assert dock.isVisible() != initial_visible

    window.action_toggle_inspector.trigger()
    assert dock.isVisible() == initial_visible


# ---------------------------------------------------------------------------
# Ctrl+1..5 tab switching
# ---------------------------------------------------------------------------


def test_tab_switch_shortcuts(qtbot, window: MainWindow) -> None:
    """Ctrl+1..4 shortcuts still exist for backward compatibility but only
    Ctrl+1 has a real target now (the sole Screens tab). The others are
    no-ops that don't error."""
    tabs = window.findChild(QTabWidget, "tabwidget_main")
    assert tabs is not None

    for idx in range(4):
        sc = window._tab_shortcuts[idx]
        sc.activated.emit()
    # The currentIndex never moves past 0 because there's only one tab
    assert tabs.currentIndex() == 0


# ---------------------------------------------------------------------------
# About dialog
# ---------------------------------------------------------------------------


def test_about_dialog_opens(qtbot, window: MainWindow) -> None:
    """Help → About must open the AboutDialog."""
    # Trigger the action; dialog runs exec() but in offscreen mode returns immediately
    # We monkey-patch exec to avoid blocking.
    from cpsm.ui.dialogs.about import AboutDialog

    opened: list[AboutDialog] = []

    original_exec = AboutDialog.exec

    def _fake_exec(self: AboutDialog) -> int:  # type: ignore[override]
        opened.append(self)
        return QDialog.DialogCode.Accepted

    AboutDialog.exec = _fake_exec  # type: ignore[method-assign]
    try:
        window.action_about.trigger()
        assert len(opened) == 1
        assert opened[0].objectName() == "dlg_about"
    finally:
        AboutDialog.exec = original_exec  # type: ignore[method-assign]


# ---------------------------------------------------------------------------
# Document population
# ---------------------------------------------------------------------------


def test_load_document_populates_sidebar(
    qtbot, populated_window: MainWindow, small_doc: CpsmDocument
) -> None:
    """Sidebar tree must have the right item count after load_document()."""
    from cpsm.ui.widgets.session_list import SessionListWidget

    widget = populated_window.findChild(SessionListWidget, "widget_session_list")
    assert widget is not None
    tree = widget.tree

    # Two connections under the Connections category
    cat_connections = tree.topLevelItem(0)
    assert cat_connections is not None
    assert cat_connections.childCount() == 2

    # One group under the Groups category
    cat_groups = tree.topLevelItem(1)
    assert cat_groups is not None
    assert cat_groups.childCount() == 1


def test_load_document_populates_connections_tab(
    qtbot, populated_window: MainWindow, small_doc: CpsmDocument
) -> None:
    """Connections tree in the main tab must show the two connections."""
    model = populated_window._connections_model
    assert model.rowCount() == 2


# ---------------------------------------------------------------------------
# Object-name lint (imported from tests/lint)
# ---------------------------------------------------------------------------


def test_object_name_lint(qtbot, window: MainWindow) -> None:
    """All interactive widgets must have non-empty objectNames (lint gate)."""
    # Import and run the lint check directly
    from tests.lint.test_object_names import check_object_names

    failures = check_object_names(window)
    assert failures == [], "\n".join(failures)
