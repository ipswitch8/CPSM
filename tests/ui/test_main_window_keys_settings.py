# -*- coding: utf-8 -*-
"""Tests for Manage Keys and Settings action handlers in MainWindow."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QDialog

from cpsm.data.schema import CpsmDocument, Settings
from cpsm.ui.main_window import MainWindow

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def empty_doc() -> CpsmDocument:
    return CpsmDocument(
        schema_version=1,
        settings=Settings(),
        ssh_keys=[],
        connections=[],
        groups=[],
        screen_layouts=[],
        scenes=[],
        launch_templates=[],
    )


@pytest.fixture()
def mock_services(empty_doc: CpsmDocument) -> SimpleNamespace:
    svc = SimpleNamespace(
        config=MagicMock(),
        session=MagicMock(),
        layout=MagicMock(),
        templates=MagicMock(),
        repository=MagicMock(),
        key_service=MagicMock(),
        config_path=Path("/tmp/keys-settings-test.cpsm.yaml"),
        status_poller=MagicMock(),
        monitor_service=None,
    )
    svc.session._backend = MagicMock()
    svc.config.load.return_value = empty_doc
    return svc


@pytest.fixture()
def win(qtbot, empty_doc: CpsmDocument, mock_services: SimpleNamespace) -> MainWindow:
    w = MainWindow(services=mock_services, document=empty_doc)
    qtbot.addWidget(w)
    w.show()
    return w


# ---------------------------------------------------------------------------
# action_manage_keys — opens SshKeyManagerDialog
# ---------------------------------------------------------------------------


def test_manage_keys_action_exists(win: MainWindow) -> None:
    """action_manage_keys must be registered as an attribute."""
    assert hasattr(win, "action_manage_keys")


def test_on_manage_keys_action_opens_dialog(
    win: MainWindow, mock_services: SimpleNamespace, monkeypatch
) -> None:
    exec_calls: list[int] = []
    monkeypatch.setattr(
        "cpsm.ui.dialogs.ssh_key_manager.SshKeyManagerDialog.exec",
        lambda self: exec_calls.append(1) or 0,
    )
    win._on_manage_keys_action()
    assert exec_calls, "SshKeyManagerDialog.exec should have been called"


def test_on_manage_keys_action_saves_after_dialog(
    win: MainWindow, mock_services: SimpleNamespace, monkeypatch
) -> None:
    monkeypatch.setattr(
        "cpsm.ui.dialogs.ssh_key_manager.SshKeyManagerDialog.exec",
        lambda self: 0,
    )
    mock_services.repository.save.reset_mock()
    win._on_manage_keys_action()
    assert mock_services.repository.save.called


def test_on_manage_keys_action_exception_shows_warning(
    win: MainWindow, mock_services: SimpleNamespace, monkeypatch
) -> None:
    monkeypatch.setattr(
        "cpsm.ui.dialogs.ssh_key_manager.SshKeyManagerDialog.__init__",
        lambda self, *a, **kw: (_ for _ in ()).throw(RuntimeError("import error")),
    )
    shown: list[str] = []
    monkeypatch.setattr(
        "cpsm.ui.main_window.QMessageBox.warning",
        lambda *a, **kw: shown.append(str(a)) or 0,
    )
    win._on_manage_keys_action()
    assert shown


# ---------------------------------------------------------------------------
# action_settings — opens SettingsDialog
# ---------------------------------------------------------------------------


def test_settings_action_exists(win: MainWindow) -> None:
    """action_settings must be registered as an attribute."""
    assert hasattr(win, "action_settings")


def test_on_settings_action_opens_dialog(win: MainWindow, monkeypatch) -> None:
    exec_calls: list[int] = []
    monkeypatch.setattr(
        "cpsm.ui.dialogs.settings.SettingsDialog.exec",
        lambda self: exec_calls.append(1) or QDialog.DialogCode.Rejected,
    )
    win._on_settings_action()
    assert exec_calls, "SettingsDialog.exec should have been called"


def test_on_settings_accepted_saves_document(
    win: MainWindow, mock_services: SimpleNamespace, monkeypatch
) -> None:
    monkeypatch.setattr(
        "cpsm.ui.dialogs.settings.SettingsDialog.exec",
        lambda self: QDialog.DialogCode.Accepted,
    )
    monkeypatch.setattr(
        "cpsm.ui.dialogs.settings.SettingsDialog.collect_data",
        lambda self: {
            "default_multiplexer": "tmux",
            "default_terminal": "xterm",
            "ssh_binary": "auto",
            "default_claude_options": "",
            "default_ssh_options": "",
            "known_hosts_strict": False,
            "status_poll_interval_ms": 3000,
            "layout_conflict_default": "error",
            "layout_preserve_on_remove": True,
            "log_level": "WARNING",
        },
    )
    mock_services.repository.save.reset_mock()
    win._on_settings_action()
    assert mock_services.repository.save.called


def test_on_settings_action_exception_shows_warning(win: MainWindow, monkeypatch) -> None:
    monkeypatch.setattr(
        "cpsm.ui.dialogs.settings.SettingsDialog.__init__",
        lambda self, *a, **kw: (_ for _ in ()).throw(RuntimeError("settings crash")),
    )
    shown: list[str] = []
    monkeypatch.setattr(
        "cpsm.ui.main_window.QMessageBox.warning",
        lambda *a, **kw: shown.append(str(a)) or 0,
    )
    win._on_settings_action()
    assert shown
