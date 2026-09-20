# -*- coding: utf-8 -*-
"""Visual smoke tests for dialogs — real-render via xcb."""

from __future__ import annotations

import os

from PySide6.QtWidgets import QApplication

from cpsm.data.schema import Settings
from cpsm.ui.dialogs.about import AboutDialog
from cpsm.ui.dialogs.connection_editor import ConnectionEditorDialog
from cpsm.ui.dialogs.group_editor import GroupEditorDialog
from cpsm.ui.dialogs.settings import SettingsDialog
from cpsm.ui.dialogs.welcome import WelcomeDialog
from cpsm.ui.widgets.group_panel import ConnectionEntry

from .conftest import assert_pixmap_nonblank


def _save_dialog_screenshot(dialog, screenshot_dir, name: str, qtbot, threshold: int = 2000):
    """Show, paint, screenshot, close — no exec()."""
    dialog.show()
    dialog.raise_()
    qtbot.waitExposed(dialog, timeout=5000)
    QApplication.processEvents()
    qtbot.wait(150)
    QApplication.processEvents()

    pixmap = dialog.grab()
    assert_pixmap_nonblank(pixmap, threshold=threshold)

    path = os.path.join(screenshot_dir, f"{name}.png")
    pixmap.save(path)
    assert os.path.getsize(path) > 500

    dialog.close()
    QApplication.processEvents()


class TestDialogsVisual:
    def test_welcome_dialog_paints(self, qtbot, qapp, screenshot_dir):
        dialog = WelcomeDialog()
        qtbot.addWidget(dialog)
        _save_dialog_screenshot(dialog, screenshot_dir, "welcome", qtbot)

    def test_about_dialog_paints(self, qtbot, qapp, screenshot_dir):
        dialog = AboutDialog()
        qtbot.addWidget(dialog)
        _save_dialog_screenshot(dialog, screenshot_dir, "about", qtbot)

    def test_settings_dialog_paints(self, qtbot, qapp, screenshot_dir):
        dialog = SettingsDialog(settings=Settings())
        qtbot.addWidget(dialog)
        _save_dialog_screenshot(dialog, screenshot_dir, "settings", qtbot)

    def test_connection_editor_remote_paints(self, qtbot, qapp, screenshot_dir):
        connection_data = {
            "id": "web01",
            "name": "Web Server 01",
            "launch_profile": "claude-remote",
            "host": "example.com",
            "port": 22,
            "user": "deploy",
            "identity_file_ref": "default-key",
            "project_folder": "/srv/web",
            "claude_options": "--resume",
        }
        dialog = ConnectionEditorDialog(connection_data=connection_data, groups=[])
        qtbot.addWidget(dialog)
        _save_dialog_screenshot(dialog, screenshot_dir, "connection_editor_remote", qtbot)

    def test_connection_editor_local_paints(self, qtbot, qapp, screenshot_dir):
        connection_data = {
            "id": "dotfiles",
            "name": "Local Dotfiles",
            "launch_profile": "claude-local",
            "project_folder": "/home/user/dotfiles",
            "claude_options": "--resume",
        }
        dialog = ConnectionEditorDialog(connection_data=connection_data, groups=[])
        qtbot.addWidget(dialog)
        _save_dialog_screenshot(dialog, screenshot_dir, "connection_editor_local", qtbot)

    def test_group_editor_paints(self, qtbot, qapp, screenshot_dir):
        group_data = {
            "id": "prod",
            "name": "Production",
            "members": ["web01"],
            "color": "#3b82f6",
            "launch_order": "sequential",
            "launch_delay_ms": 250,
            "isolation": "shared",
            "layout_conflict": "move",
            "auto_attach": True,
        }
        all_connections = [
            ConnectionEntry(
                conn_id="web01", name="Web Server 01", profile="claude-remote", other_groups=[]
            ),
            ConnectionEntry(
                conn_id="dotfiles", name="Local Dotfiles", profile="claude-local", other_groups=[]
            ),
        ]
        dialog = GroupEditorDialog(
            group_data=group_data,
            all_connections=all_connections,
            available_layout_ids=[],
            is_new=False,
        )
        qtbot.addWidget(dialog)
        _save_dialog_screenshot(dialog, screenshot_dir, "group_editor", qtbot)
