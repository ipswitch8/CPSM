# -*- coding: utf-8 -*-
"""
Tests for Bug 2: wiring ConnectionEditorDialog to the connections tree.

Covers:
- Double-click in connections tree → ConnectionEditorDialog opens.
- On dialog accept, the connection in self._document is updated.
- New Connection action opens the dialog with empty data.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


import pytest
from PySide6.QtWidgets import QDialog

from cpsm.data.schema import (
    ClaudeRemoteConnection,
    CpsmDocument,
    SshKey,
)
from cpsm.ui.main_window import MainWindow

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def ssh_key():
    return SshKey(
        id="key-edit",
        name="Edit Key",
        type="ed25519",
        private_path="~/.ssh/id_ed25519_edit",
        public_path="~/.ssh/id_ed25519_edit.pub",
    )


@pytest.fixture()
def remote_conn(ssh_key):
    return ClaudeRemoteConnection(
        id="conn-edit",
        name="Edit Me",
        launch_profile="claude-remote",
        host="edit.example.com",
        port=22,
        user="ubuntu",
        identity_file_ref="key-edit",
        project_folder="/opt/edit",
        claude_options="--resume",
    )


@pytest.fixture()
def doc(ssh_key, remote_conn):
    return CpsmDocument(ssh_keys=[ssh_key], connections=[remote_conn])


@pytest.fixture()
def win(qtbot, doc):
    w = MainWindow(document=doc)
    qtbot.addWidget(w)
    w.show()
    return w


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_double_click_connections_tree_opens_editor(qtbot, win: MainWindow) -> None:
    """Double-clicking a row in the connections tree opens ConnectionEditorDialog."""
    opened: list[object] = []

    from cpsm.ui.dialogs.connection_editor import ConnectionEditorDialog

    original_exec = ConnectionEditorDialog.exec

    def _fake_exec(self_dlg: ConnectionEditorDialog) -> int:
        opened.append(self_dlg)
        return QDialog.DialogCode.Rejected  # Cancel — no update

    ConnectionEditorDialog.exec = _fake_exec  # type: ignore[method-assign]
    try:
        index = win._connections_model.index(0, 0)
        win._on_connection_double_clicked(index)

        assert len(opened) == 1, "Dialog should have been opened once"
    finally:
        ConnectionEditorDialog.exec = original_exec  # type: ignore[method-assign]


def test_double_click_out_of_range_does_not_crash(qtbot, win: MainWindow) -> None:
    """Double-clicking an out-of-range row must not crash."""

    class _FakeIndex:
        def row(self) -> int:
            return 99

    # Should not raise
    win._on_connection_double_clicked(_FakeIndex())  # type: ignore[arg-type]


def test_on_dialog_accept_updates_connection(qtbot, win: MainWindow, remote_conn) -> None:
    """On dialog accept, the connection data in self._document is updated."""
    from cpsm.ui.dialogs.connection_editor import ConnectionEditorDialog

    original_exec = ConnectionEditorDialog.exec
    original_get_data = ConnectionEditorDialog.get_connection_data

    # Prepare updated data
    updated_data = remote_conn.model_dump(mode="python")
    updated_data["name"] = "Updated Name"

    def _fake_exec(self_dlg: ConnectionEditorDialog) -> int:
        return QDialog.DialogCode.Accepted

    def _fake_get_data(self_dlg: ConnectionEditorDialog) -> dict:
        return updated_data

    ConnectionEditorDialog.exec = _fake_exec  # type: ignore[method-assign]
    ConnectionEditorDialog.get_connection_data = _fake_get_data  # type: ignore[method-assign]
    try:
        index = win._connections_model.index(0, 0)
        win._on_connection_double_clicked(index)

        assert win._document.connections[0].name == "Updated Name"
    finally:
        ConnectionEditorDialog.exec = original_exec  # type: ignore[method-assign]
        ConnectionEditorDialog.get_connection_data = original_get_data  # type: ignore[method-assign]


def test_new_connection_action_opens_editor_empty(qtbot, win: MainWindow) -> None:
    """Triggering action_new_connection opens ConnectionEditorDialog with is_new=True."""
    opened: list[object] = []

    from cpsm.ui.dialogs.connection_editor import ConnectionEditorDialog

    original_init = ConnectionEditorDialog.__init__
    original_exec = ConnectionEditorDialog.exec

    init_kwargs: list[dict] = []

    def _capture_init(self_dlg, parent=None, **kwargs):
        init_kwargs.append(kwargs)
        original_init(self_dlg, parent=parent, **kwargs)

    def _fake_exec(self_dlg: ConnectionEditorDialog) -> int:
        opened.append(self_dlg)
        return QDialog.DialogCode.Rejected

    ConnectionEditorDialog.__init__ = _capture_init  # type: ignore[method-assign]
    ConnectionEditorDialog.exec = _fake_exec  # type: ignore[method-assign]
    try:
        win.action_new_connection.trigger()

        assert len(opened) == 1, "Dialog opened once"
        assert init_kwargs[0].get("is_new") is True
    finally:
        ConnectionEditorDialog.__init__ = original_init  # type: ignore[method-assign]
        ConnectionEditorDialog.exec = original_exec  # type: ignore[method-assign]
