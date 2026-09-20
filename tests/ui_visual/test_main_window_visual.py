# -*- coding: utf-8 -*-
"""Visual smoke tests for the main window — real-render via xcb."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QTreeView, QTreeWidget

from cpsm.data.schema import (
    ClaudeLocalConnection,
    ClaudeRemoteConnection,
    CpsmDocument,
    Group,
    Settings,
    SshKey,
)
from cpsm.ui.main_window import MainWindow

from .conftest import assert_pixmap_nonblank


def _make_doc() -> CpsmDocument:
    return CpsmDocument(
        schema_version=1,
        settings=Settings(),
        ssh_keys=[
            SshKey(
                id="default-key",
                name="Default ed25519",
                type="ed25519",
                private_path=str(Path("~/.ssh/id_ed25519").expanduser()),
                public_path=str(Path("~/.ssh/id_ed25519.pub").expanduser()),
            )
        ],
        connections=[
            ClaudeRemoteConnection(
                id="web01",
                name="Web Server 01",
                launch_profile="claude-remote",
                host="web01.example.com",
                port=22,
                user="deploy",
                identity_file_ref="default-key",
                project_folder="/srv/web01",
                claude_options="--resume",
            ),
            ClaudeLocalConnection(
                id="dotfiles",
                name="Local Dotfiles",
                launch_profile="claude-local",
                project_folder="/home/user/dotfiles",
                claude_options="--resume",
            ),
        ],
        groups=[
            Group(id="prod", name="Production", members=["web01"], isolation="shared"),
            Group(id="local", name="Local Work", members=["dotfiles"], isolation="shared"),
        ],
        screen_layouts=[],
        scenes=[],
        launch_templates=[],
    )


@pytest.fixture
def main_window(qtbot, qapp):
    """Construct, populate, and show the main window."""
    doc = _make_doc()
    window = MainWindow(document=doc)
    if hasattr(window, "load_document"):
        window.load_document(doc)
    qtbot.addWidget(window)
    window.show()
    window.raise_()
    qtbot.waitExposed(window, timeout=5000)
    QApplication.processEvents()
    qtbot.wait(150)
    QApplication.processEvents()
    return window


class TestMainWindowVisual:
    def test_main_window_paints(self, main_window, screenshot_dir, qtbot):
        pixmap = main_window.grab()
        assert_pixmap_nonblank(pixmap, threshold=10000)
        path = os.path.join(screenshot_dir, "main_window.png")
        pixmap.save(path)
        assert os.path.getsize(path) > 1000

    def test_each_tab_paints(self, main_window, screenshot_dir, qtbot):
        from PySide6.QtWidgets import QTabWidget

        tab_widget = main_window.findChild(QTabWidget, "tab_main")
        if tab_widget is None:
            tab_widget = main_window.findChild(QTabWidget)
        assert tab_widget is not None
        # Round B reduced the tab widget to a single Screens tab.
        assert tab_widget.count() == 1, f"expected 1 tab, got {tab_widget.count()}"

        for i in range(tab_widget.count()):
            tab_widget.setCurrentIndex(i)
            QApplication.processEvents()
            qtbot.wait(100)
            QApplication.processEvents()

            tab_name = tab_widget.tabText(i).lower().replace(" ", "_")
            pixmap = main_window.grab()
            assert_pixmap_nonblank(pixmap, threshold=10000)
            pixmap.save(os.path.join(screenshot_dir, f"main_window_tab_{i}_{tab_name}.png"))

    def test_sidebar_tree_paints(self, main_window, screenshot_dir, qtbot):
        sidebar = main_window.findChild(QTreeWidget, "sidebar_tree")
        if sidebar is None:
            sidebar = main_window.findChild(QTreeView, "sidebar_tree")
        assert sidebar is not None, "sidebar_tree not found"
        pixmap = sidebar.grab()
        assert_pixmap_nonblank(pixmap, threshold=100)
        pixmap.save(os.path.join(screenshot_dir, "sidebar_tree.png"))

    def test_status_bar_paints(self, main_window, screenshot_dir, qtbot):
        status = main_window.statusBar()
        assert status is not None
        pixmap = status.grab()
        assert_pixmap_nonblank(pixmap, threshold=50)
        pixmap.save(os.path.join(screenshot_dir, "status_bar.png"))

    def test_keyboard_shortcut_f4_toggles_inspector(self, main_window, qtbot):
        from PySide6.QtWidgets import QDockWidget

        inspector = main_window.findChild(QDockWidget, "dock_inspector")
        if inspector is None:
            pytest.skip("dock_inspector not present in this build")

        initial = inspector.isVisible()
        qtbot.keyClick(main_window, Qt.Key_F4)
        QApplication.processEvents()
        qtbot.wait(150)
        toggled = inspector.isVisible()
        qtbot.keyClick(main_window, Qt.Key_F4)
        QApplication.processEvents()
        qtbot.wait(150)
        final = inspector.isVisible()

        assert toggled != initial
        assert final == initial
