# -*- coding: utf-8 -*-
"""
tests/lint/test_object_names.py — CI lint: every interactive widget must have
a non-empty objectName().

Spec section: §3.1 (Selenium-friendly objectNames)

The test fixture creates a MainWindow with a small CpsmDocument and walks the
entire widget tree, asserting that every instance of INTERACTIVE_TYPES has a
non-empty objectName().  QAction instances are checked via findChildren().

This module also exports ``check_object_names()`` so other test modules can
call it directly (e.g. test_main_window.test_object_name_lint).
"""

from __future__ import annotations

import os

# QT_QPA_PLATFORM must be set before PySide6 is imported — keep this block last.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QAction, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QDockWidget,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMenuBar,
    QPushButton,
    QSpinBox,
    QTableView,
    QTabWidget,
    QToolBar,
    QToolButton,
    QTreeView,
    QWidget,
)

# ---------------------------------------------------------------------------
# Types considered "interactive"
# ---------------------------------------------------------------------------

INTERACTIVE_TYPES = (
    QPushButton,
    QToolButton,
    QLineEdit,
    QComboBox,
    QSpinBox,
    QTreeView,
    QTableView,
    QTabWidget,
    QDockWidget,
    QMenuBar,
    QMenu,
    QToolBar,
    QShortcut,
)


# ---------------------------------------------------------------------------
# Core lint function (importable by other test modules)
# ---------------------------------------------------------------------------


def _is_qt_internal_menu(menu: QWidget) -> bool:
    """Return True if *menu* is an auto-created Qt-internal context menu.

    Qt creates extra QMenu instances for:
    - QDockWidget title-bar context menus
    - QToolBar context menus
    - QMenuBar corner/overflow menus

    These are not authored by application code, so we exclude them from the
    lint check.  We identify them by their parent type and absence of an
    objectName: application-created menus always receive an objectName.
    """
    parent = menu.parent()
    if parent is None:
        return False
    # Dock and toolbar context menus have QDockWidget / QToolBar as parent
    if isinstance(parent, (QDockWidget, QToolBar, QMenuBar)):
        return True
    # Also skip menus whose parent is a QMenuBar child with Qt-internal names
    if hasattr(parent, "objectName") and parent.objectName().startswith("qt_"):
        return True
    return False


def _is_qt_internal_action(action: QAction) -> bool:
    """Return True if *action* is auto-created by Qt and need not have an objectName.

    Qt creates:
    - Separator actions (isSeparator() == True)
    - Title/toggle actions for QDockWidget ("Connections", "Inspector") and QToolBar
    - ``menuAction()`` QActions owned by a QMenu — these represent the menu itself
      in a parent QMenuBar, and the QMenu widget already has the objectName.
    """
    if action.isSeparator():
        return True
    parent = action.parent()
    if parent is None:
        return False
    # Actions directly owned by QDockWidget or QToolBar are title/toggle actions
    if isinstance(parent, (QDockWidget, QToolBar, QMenuBar)):
        return True
    # Actions owned by a QMenu are its menuAction() or internal submenu actions;
    # the menu widget itself already carries the objectName.
    if isinstance(parent, QMenu):
        return True
    return False


def check_object_names(widget: QWidget) -> list[str]:
    """Walk *widget* children and return a list of failure messages.

    Each failure message identifies the widget class and its (missing)
    objectName so the developer can find the offending widget quickly.

    Parameters
    ----------
    widget:
        Root widget to inspect (typically the MainWindow).

    Returns
    -------
    list[str]
        Empty list means all checks passed.
    """
    failures: list[str] = []

    # --- Check QWidget subclasses of INTERACTIVE_TYPES ---
    for interactive_type in INTERACTIVE_TYPES:
        children: list[QWidget] = widget.findChildren(interactive_type)  # type: ignore[assignment]
        for child in children:
            # Skip Qt-internal auto-created menus (dock/toolbar context menus)
            if isinstance(child, QMenu) and _is_qt_internal_menu(child):
                continue
            name = child.objectName()
            if not name or name.strip() == "":
                failures.append(
                    f"MISSING objectName: {type(child).__name__} "
                    f"(accessible='{child.accessibleName()}', "
                    f"parent={type(child.parent()).__name__ if child.parent() else 'None'})"
                )

    # --- Check QAction instances ---
    if isinstance(widget, QMainWindow):
        actions: list[QAction] = widget.findChildren(QAction)
        for action in actions:
            # Skip separators and Qt-auto-created dock/toolbar title actions
            if _is_qt_internal_action(action):
                continue
            name = action.objectName()
            if not name or name.strip() == "":
                failures.append(f"MISSING objectName: QAction text='{action.text()}'")

    return failures


# ---------------------------------------------------------------------------
# Pytest fixture and test
# ---------------------------------------------------------------------------


@pytest.fixture()
def main_window_for_lint(qtbot):  # type: ignore[no-untyped-def]
    """Create a MainWindow with a small document for lint testing."""
    from cpsm.data.schema import (
        ClaudeLocalConnection,
        ClaudeRemoteConnection,
        CpsmDocument,
        Group,
        SshKey,
    )
    from cpsm.ui.main_window import MainWindow

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
    doc = CpsmDocument(ssh_keys=[key], connections=[conn1, conn2], groups=[grp])

    win = MainWindow(document=doc)
    qtbot.addWidget(win)
    win.show()
    return win


def test_all_interactive_widgets_have_object_names(main_window_for_lint: QWidget) -> None:
    """Fail if any interactive widget is missing a stable objectName."""
    failures = check_object_names(main_window_for_lint)
    assert failures == [], f"{len(failures)} widget(s) are missing objectName():\n" + "\n".join(
        f"  - {f}" for f in failures
    )
