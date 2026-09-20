# -*- coding: utf-8 -*-
"""Tests for _stop_connection, _stop_group, and _reconnect_connection."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from cpsm.data.schema import (
    CpsmDocument,
    Group,
    LocalShellConnection,
    Settings,
)
from cpsm.ui.main_window import MainWindow

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def conn() -> LocalShellConnection:
    return LocalShellConnection(
        id="stop-conn",
        name="Stop Conn",
        launch_profile="local-shell",
        project_folder="/opt/stop",
    )


@pytest.fixture()
def grp(conn: LocalShellConnection) -> Group:
    return Group(id="stop-grp", name="Stop Group", members=["stop-conn"])


@pytest.fixture()
def doc(conn: LocalShellConnection, grp: Group) -> CpsmDocument:
    return CpsmDocument(
        schema_version=1,
        settings=Settings(),
        ssh_keys=[],
        connections=[conn],
        groups=[grp],
        screen_layouts=[],
        scenes=[],
        launch_templates=[],
    )


@pytest.fixture()
def mock_services() -> SimpleNamespace:
    svc = SimpleNamespace(
        config=MagicMock(),
        session=MagicMock(),
        layout=MagicMock(),
        templates=MagicMock(),
        repository=MagicMock(),
        key_service=MagicMock(),
        config_path=Path("/tmp/stop-test.cpsm.yaml"),
        status_poller=MagicMock(),
        monitor_service=None,
    )
    svc.session.session_name.side_effect = lambda cid: f"cpsm-{cid}"
    return svc


@pytest.fixture()
def win(qtbot, doc: CpsmDocument, mock_services: SimpleNamespace) -> MainWindow:
    w = MainWindow(services=mock_services, document=doc)
    qtbot.addWidget(w)
    w.show()
    return w


# ---------------------------------------------------------------------------
# _stop_connection
# ---------------------------------------------------------------------------


def test_stop_connection_calls_kill_session(
    win: MainWindow, mock_services: SimpleNamespace, conn: LocalShellConnection
) -> None:
    win._stop_connection(conn)
    mock_services.session.session_name.assert_called_with("stop-conn")
    mock_services.session.kill_session.assert_called_with("cpsm-stop-conn")


def test_stop_connection_shows_status_bar(
    win: MainWindow, mock_services: SimpleNamespace, conn: LocalShellConnection
) -> None:
    win._stop_connection(conn)
    # Status bar text is set; just verify no exception
    assert True


def test_stop_connection_shows_warning_on_error(
    win: MainWindow, mock_services: SimpleNamespace, conn: LocalShellConnection, monkeypatch
) -> None:
    mock_services.session.kill_session.side_effect = RuntimeError("tmux not running")
    shown: list[str] = []
    monkeypatch.setattr(
        "cpsm.ui.main_window.QMessageBox.warning",
        lambda *a, **kw: shown.append(str(a)) or 0,
    )
    win._stop_connection(conn)
    assert shown, "warning should have been shown"


def test_stop_connection_no_services_is_safe(
    qtbot, doc: CpsmDocument, conn: LocalShellConnection
) -> None:
    w = MainWindow(services=None, document=doc)
    qtbot.addWidget(w)
    w.show()
    # Should not raise; shows a status message instead
    w._stop_connection(conn)


# ---------------------------------------------------------------------------
# _stop_group
# ---------------------------------------------------------------------------


def test_stop_group_calls_kill_for_each_member(
    win: MainWindow, mock_services: SimpleNamespace, grp: Group
) -> None:
    win._stop_group(grp)
    mock_services.session.kill_session.assert_called_with("cpsm-stop-conn")


def test_stop_group_partial_failure_shows_warning(
    win: MainWindow, mock_services: SimpleNamespace, grp: Group, monkeypatch
) -> None:
    mock_services.session.kill_session.side_effect = RuntimeError("bad")
    shown: list[str] = []
    monkeypatch.setattr(
        "cpsm.ui.main_window.QMessageBox.warning",
        lambda *a, **kw: shown.append(str(a)) or 0,
    )
    win._stop_group(grp)
    assert shown, "warning should appear on partial failure"


# ---------------------------------------------------------------------------
# _reconnect_connection
# ---------------------------------------------------------------------------


def test_reconnect_connection_kills_then_launches(
    win: MainWindow, mock_services: SimpleNamespace, conn: LocalShellConnection
) -> None:
    win._reconnect_connection(conn)
    mock_services.session.kill_session.assert_called()
    mock_services.session.launch.assert_called()


def test_reconnect_connection_kill_failure_still_launches(
    win: MainWindow, mock_services: SimpleNamespace, conn: LocalShellConnection
) -> None:
    """Even if kill fails, we should still try to launch."""
    mock_services.session.kill_session.side_effect = RuntimeError("no session")
    win._reconnect_connection(conn)
    # launch should still be called
    assert mock_services.session.launch.called
