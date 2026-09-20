# -*- coding: utf-8 -*-
"""
Tests for the inspector dock extended field list in MainWindow.

Covers Bug 1: sudo_user, port, claude_options, identity_file_ref, jump_host
were missing from the inspector dock.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QLabel

from cpsm.data.schema import (
    ClaudeLocalConnection,
    ClaudeRemoteConnection,
    CpsmDocument,
    SshKey,
    SshShellConnection,
)
from cpsm.ui.main_window import MainWindow

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def ssh_key():
    return SshKey(
        id="key-test",
        name="Test Key",
        type="ed25519",
        private_path="~/.ssh/id_ed25519_test",
        public_path="~/.ssh/id_ed25519_test.pub",
    )


@pytest.fixture()
def jump_conn(ssh_key):
    """A bastion/jump connection — used as jump_host reference."""
    return SshShellConnection(
        id="jump-host-01",
        name="Jump Host",
        launch_profile="ssh-shell",
        host="bastion.example.com",
        user="ops",
        identity_file_ref="key-test",
    )


@pytest.fixture()
def remote_conn_with_sudo(ssh_key, jump_conn):
    return ClaudeRemoteConnection(
        id="remote-01",
        name="Remote One",
        launch_profile="claude-remote",
        host="remote.example.com",
        port=2222,
        user="ubuntu",
        sudo_user="root",
        identity_file_ref="key-test",
        project_folder="/opt/proj",
        claude_options="--verbose",
        jump_host=jump_conn.id,  # Must be a connection ID
    )


@pytest.fixture()
def doc_with_remote(ssh_key, jump_conn, remote_conn_with_sudo):
    return CpsmDocument(ssh_keys=[ssh_key], connections=[jump_conn, remote_conn_with_sudo])


@pytest.fixture()
def window_remote(qtbot, doc_with_remote):
    win = MainWindow(document=doc_with_remote)
    qtbot.addWidget(win)
    win.show()
    return win


@pytest.fixture()
def local_conn():
    return ClaudeLocalConnection(
        id="local-01",
        name="Local One",
        launch_profile="claude-local",
        project_folder="~/projects/local",
        claude_options="--resume",
    )


@pytest.fixture()
def doc_with_local(local_conn):
    return CpsmDocument(connections=[local_conn])


@pytest.fixture()
def window_local(qtbot, doc_with_local):
    win = MainWindow(document=doc_with_local)
    qtbot.addWidget(win)
    win.show()
    return win


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _select_connection_at(win: MainWindow, row: int = 0) -> None:
    """Select the given row in the connections tree to populate the inspector."""
    model = win._connections_model
    if model.rowCount() <= row:
        return
    index = model.index(row, 0)
    win._connections_tree.selectionModel().select(
        index,
        win._connections_tree.selectionModel().SelectionFlag.ClearAndSelect
        | win._connections_tree.selectionModel().SelectionFlag.Rows,
    )


def _select_first_connection(win: MainWindow) -> None:
    """Select the first row in the connections tree."""
    _select_connection_at(win, 0)


def _inspector_field(win: MainWindow, field_name: str) -> QLabel | None:
    return win._inspector_fields.get(field_name)


# ---------------------------------------------------------------------------
# Tests: sudo_user present on claude-remote
# ---------------------------------------------------------------------------


def test_inspector_shows_sudo_user_when_set(qtbot, window_remote: MainWindow) -> None:
    """Inspector must display sudo_user when the connection has one."""
    # The remote connection with sudo is at row 1 (row 0 is the jump host)
    _select_connection_at(window_remote, 1)

    lbl = _inspector_field(window_remote, "sudo_user")
    assert lbl is not None, "inspector_fields must contain 'sudo_user'"
    assert lbl.isVisible(), "sudo_user label should be visible when set"
    assert lbl.text() == "root"


def test_inspector_shows_port_for_remote(qtbot, window_remote: MainWindow) -> None:
    """Inspector must display port for claude-remote connections."""
    _select_connection_at(window_remote, 1)

    lbl = _inspector_field(window_remote, "port")
    assert lbl is not None
    assert lbl.isVisible()
    assert lbl.text() == "2222"


def test_inspector_shows_claude_options(qtbot, window_remote: MainWindow) -> None:
    """Inspector must display claude_options for claude-remote connections."""
    _select_connection_at(window_remote, 1)

    lbl = _inspector_field(window_remote, "claude_options")
    assert lbl is not None
    assert lbl.isVisible()
    assert lbl.text() == "--verbose"


def test_inspector_shows_identity_file_ref(qtbot, window_remote: MainWindow) -> None:
    """Inspector must display identity_file_ref for claude-remote connections."""
    _select_connection_at(window_remote, 1)

    lbl = _inspector_field(window_remote, "identity_file_ref")
    assert lbl is not None
    assert lbl.isVisible()
    assert lbl.text() == "key-test"


def test_inspector_shows_jump_host(qtbot, window_remote: MainWindow) -> None:
    """Inspector must display jump_host when set on a claude-remote connection."""
    _select_connection_at(window_remote, 1)

    lbl = _inspector_field(window_remote, "jump_host")
    assert lbl is not None
    assert lbl.isVisible()
    # jump_host is stored as the connection ID
    assert lbl.text() == "jump-host-01"


# ---------------------------------------------------------------------------
# Tests: fields hidden when None / not applicable
# ---------------------------------------------------------------------------


def test_inspector_hides_host_for_local_connection(qtbot, window_local: MainWindow) -> None:
    """Inspector must hide 'host' for a claude-local connection (no host attr)."""
    _select_first_connection(window_local)

    lbl = _inspector_field(window_local, "host")
    assert lbl is not None
    # claude-local has no 'host'; should be hidden
    assert not lbl.isVisible(), "host should be hidden for claude-local"


def test_inspector_hides_sudo_user_when_none_v2(qtbot) -> None:
    """Inspector hides sudo_user when the field is None (no sudo_user set)."""
    conn = ClaudeLocalConnection(
        id="local-02",
        name="Local Two",
        launch_profile="claude-local",
        project_folder="~/projects/l2",
        claude_options="--resume",
        # sudo_user is None by default
    )
    doc = CpsmDocument(connections=[conn])
    win = MainWindow(document=doc)
    qtbot.addWidget(win)
    win.show()

    _select_first_connection(win)

    lbl = _inspector_field(win, "sudo_user")
    assert lbl is not None
    assert not lbl.isVisible(), "sudo_user label should be hidden when None"


def test_inspector_hides_jump_host_when_none(qtbot, ssh_key) -> None:
    """Inspector hides jump_host when not set on an ssh-shell connection."""
    conn = SshShellConnection(
        id="ssh-01",
        name="SSH One",
        launch_profile="ssh-shell",
        host="ssh.example.com",
        user="ops",
        identity_file_ref="key-test",
        # jump_host is None by default
    )
    doc = CpsmDocument(ssh_keys=[ssh_key], connections=[conn])
    win = MainWindow(document=doc)
    qtbot.addWidget(win)
    win.show()

    _select_first_connection(win)

    lbl = _inspector_field(win, "jump_host")
    assert lbl is not None
    assert not lbl.isVisible(), "jump_host should be hidden when None"


def test_inspector_placeholder_shown_when_no_selection(qtbot) -> None:
    """Inspector placeholder label is shown when nothing is selected."""
    win = MainWindow()
    qtbot.addWidget(win)
    win.show()

    assert win._inspector_placeholder.isVisible()
