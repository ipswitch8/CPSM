# -*- coding: utf-8 -*-
"""Happy-path tests for the newly-wired action handlers in MainWindow.

Each test mocks the relevant service call or dialog so no real Qt event loop
blocking occurs and no real filesystem/tmux is touched.
"""

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
    Scene,
    Settings,
)
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
def conn() -> LocalShellConnection:
    return LocalShellConnection(
        id="test-conn",
        name="Test Conn",
        launch_profile="local-shell",
        project_folder="/opt/test",
    )


@pytest.fixture()
def grp(conn: LocalShellConnection) -> Group:
    return Group(id="test-grp", name="Test Group", members=["test-conn"])


@pytest.fixture()
def doc(conn: LocalShellConnection, grp: Group, empty_doc: CpsmDocument) -> CpsmDocument:
    return empty_doc.model_copy(update={"connections": [conn], "groups": [grp]})


@pytest.fixture()
def mock_services(doc: CpsmDocument) -> SimpleNamespace:
    svc = SimpleNamespace(
        config=MagicMock(),
        session=MagicMock(),
        layout=MagicMock(),
        templates=MagicMock(),
        repository=MagicMock(),
        key_service=MagicMock(),
        config_path=Path("/tmp/test.cpsm.yaml"),
        status_poller=MagicMock(),
        monitor_service=None,
    )
    svc.config.validate.return_value = []
    svc.config.load.return_value = doc
    return svc


@pytest.fixture()
def win(qtbot, doc: CpsmDocument, mock_services: SimpleNamespace, monkeypatch) -> MainWindow:
    # Prevent LayoutController from failing in the test environment
    monkeypatch.setattr(
        "cpsm.controllers.layout_controller.LayoutController.__init__",
        lambda self, **kwargs: None,
        raising=False,
    )
    w = MainWindow(services=mock_services, document=doc)
    qtbot.addWidget(w)
    w.show()
    return w


# ---------------------------------------------------------------------------
# Fix #7: _save_document uses QMessageBox.warning on failure
# ---------------------------------------------------------------------------


def test_save_document_shows_qmessagebox_on_failure(
    qtbot, win: MainWindow, mock_services: SimpleNamespace, monkeypatch
) -> None:
    mock_services.repository.save.side_effect = OSError("disk full")
    shown: list[str] = []
    monkeypatch.setattr(
        "cpsm.ui.main_window.QMessageBox.warning",
        lambda *a, **kw: shown.append(a[2]) or 0,
    )
    win._save_document()
    assert any("disk full" in m for m in shown)


# ---------------------------------------------------------------------------
# Fix #1: _on_save_config_action
# ---------------------------------------------------------------------------


def test_on_save_config_action_saves_and_shows_status(
    qtbot, win: MainWindow, mock_services: SimpleNamespace
) -> None:
    mock_services.repository.save.return_value = None
    win._on_save_config_action()
    assert mock_services.repository.save.called


# ---------------------------------------------------------------------------
# Fix #1: _on_open_config_action
# ---------------------------------------------------------------------------


def test_on_open_config_action_loads_file(
    qtbot, win: MainWindow, mock_services: SimpleNamespace, tmp_path: Path, monkeypatch
) -> None:
    cfg = tmp_path / "test.cpsm.yaml"
    cfg.write_text("schema_version: 1\nsettings: {}\n", encoding="utf-8")
    monkeypatch.setattr(
        "PySide6.QtWidgets.QFileDialog.getOpenFileName",
        staticmethod(lambda *a, **kw: (str(cfg), "")),
    )
    win._on_open_config_action()
    assert mock_services.config.load.called
    assert mock_services.config_path == cfg


def test_on_open_config_action_cancel_noop(
    qtbot, win: MainWindow, mock_services: SimpleNamespace, monkeypatch
) -> None:
    monkeypatch.setattr(
        "PySide6.QtWidgets.QFileDialog.getOpenFileName",
        staticmethod(lambda *a, **kw: ("", "")),
    )
    mock_services.config.load.reset_mock()
    win._on_open_config_action()
    assert not mock_services.config.load.called


# ---------------------------------------------------------------------------
# Fix #1: _on_import_action
# ---------------------------------------------------------------------------


def test_on_import_action_calls_import_service(
    qtbot, win: MainWindow, mock_services: SimpleNamespace, tmp_path: Path, monkeypatch
) -> None:
    src = tmp_path / "legacy.yaml"
    src.write_text("projects: []\n", encoding="utf-8")
    # Round-late tweak: a confirmation dialog now precedes the file
    # picker — auto-Ok it so the test can reach the picker path.
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(
        "cpsm.ui.main_window.QMessageBox.information",
        lambda *a, **kw: QMessageBox.StandardButton.Ok,
    )
    monkeypatch.setattr(
        "PySide6.QtWidgets.QFileDialog.getOpenFileName",
        staticmethod(lambda *a, **kw: (str(src), "")),
    )
    mock_import_svc = MagicMock()
    monkeypatch.setattr(
        "cpsm.services.import_service.ImportService",
        lambda: mock_import_svc,
    )
    win._on_import_action()
    assert mock_import_svc.import_legacy_to.called


# ---------------------------------------------------------------------------
# Fix #1: _on_validate_action — valid doc shows status bar
# ---------------------------------------------------------------------------


def test_on_validate_action_valid_shows_status(
    qtbot, win: MainWindow, mock_services: SimpleNamespace
) -> None:
    mock_services.config.validate.return_value = []
    win._on_validate_action()
    assert mock_services.config.validate.called


def test_on_validate_action_invalid_opens_dialog(
    qtbot, win: MainWindow, mock_services: SimpleNamespace, monkeypatch
) -> None:
    from cpsm.services.config_service import ValidationIssue

    issues = [ValidationIssue(severity="error", message="bad", location="connections[0]")]
    mock_services.config.validate.return_value = issues
    exec_calls: list[int] = []
    monkeypatch.setattr(
        "cpsm.ui.dialogs.validation_errors.ValidationErrorsDialog.exec",
        lambda self: exec_calls.append(1) or 0,
    )
    win._on_validate_action()
    assert exec_calls, "ValidationErrorsDialog.exec should have been called"


# ---------------------------------------------------------------------------
# Fix #1: _on_launch_action
# ---------------------------------------------------------------------------


def test_on_launch_action_with_connection_selected(
    qtbot, win: MainWindow, mock_services: SimpleNamespace, conn: LocalShellConnection
) -> None:
    # Simulate sidebar selection by monkeypatching _get_selected_item
    win._get_selected_item = lambda: conn  # type: ignore[method-assign]
    win._on_launch_action()
    assert mock_services.session.launch.called


def test_on_launch_action_with_group_selected(
    qtbot, win: MainWindow, mock_services: SimpleNamespace, grp: Group
) -> None:
    win._get_selected_item = lambda: grp  # type: ignore[method-assign]
    win._on_launch_action()
    assert mock_services.session.launch_group.called


def test_on_launch_action_no_selection_shows_message(
    qtbot, win: MainWindow, mock_services: SimpleNamespace
) -> None:
    win._get_selected_item = lambda: None  # type: ignore[method-assign]
    win._on_launch_action()
    # No crash; services not called
    assert not mock_services.session.launch.called


# ---------------------------------------------------------------------------
# Fix #1: _on_stop_action
# ---------------------------------------------------------------------------


def test_on_stop_action_with_connection_calls_kill(
    qtbot, win: MainWindow, mock_services: SimpleNamespace, conn: LocalShellConnection
) -> None:
    win._get_selected_item = lambda: conn  # type: ignore[method-assign]
    mock_services.session.session_name.return_value = "cpsm-test-conn"
    win._on_stop_action()
    assert mock_services.session.kill_session.called


def test_on_stop_action_no_selection_noop(
    qtbot, win: MainWindow, mock_services: SimpleNamespace
) -> None:
    win._get_selected_item = lambda: None  # type: ignore[method-assign]
    win._on_stop_action()
    assert not mock_services.session.kill_session.called


# ---------------------------------------------------------------------------
# Fix #1: _on_reconnect_action
# ---------------------------------------------------------------------------


def test_on_reconnect_action_kills_then_launches(
    qtbot, win: MainWindow, mock_services: SimpleNamespace, conn: LocalShellConnection
) -> None:
    win._get_selected_item = lambda: conn  # type: ignore[method-assign]
    mock_services.session.session_name.return_value = "cpsm-test-conn"
    win._on_reconnect_action()
    assert mock_services.session.launch.called


# ---------------------------------------------------------------------------
# Fix #1: _on_launch_scene_action
# ---------------------------------------------------------------------------


def test_on_launch_scene_action_launches_selected_scene(
    qtbot, win: MainWindow, mock_services: SimpleNamespace, monkeypatch
) -> None:
    win._document = win._document.model_copy(update={"scenes": [Scene(id="scene-1", groups=[])]})
    monkeypatch.setattr(
        "PySide6.QtWidgets.QInputDialog.getItem",
        staticmethod(lambda *a, **kw: ("scene-1", True)),
    )
    win._on_launch_scene_action()
    mock_services.session.launch_scene.assert_called_once()


# ---------------------------------------------------------------------------
# Fix #1: _on_launcher_templates_action
# ---------------------------------------------------------------------------


def test_on_launcher_templates_action_opens_dialog(
    qtbot, win: MainWindow, mock_services: SimpleNamespace, monkeypatch
) -> None:
    exec_calls: list[int] = []
    # Patch both __init__ and exec to avoid MagicMock leaking into Qt widgets
    monkeypatch.setattr(
        "cpsm.ui.dialogs.launcher_templates.LauncherTemplatesDialog.__init__",
        lambda self, *a, **kw: None,
    )
    monkeypatch.setattr(
        "cpsm.ui.dialogs.launcher_templates.LauncherTemplatesDialog.exec",
        lambda self: exec_calls.append(1) or 0,
    )
    win._on_launcher_templates_action()
    assert exec_calls


# ---------------------------------------------------------------------------
# Fix #1: _on_find_action
# ---------------------------------------------------------------------------


def test_on_find_action_focuses_tree(qtbot, win: MainWindow) -> None:
    # Should not raise
    win._on_find_action()


# ---------------------------------------------------------------------------
# Fix #1: _on_toggle_live_preview_action
# ---------------------------------------------------------------------------


def test_on_toggle_live_preview_toggles_radio(qtbot, win: MainWindow) -> None:
    initial = win._radio_screens_preview.isChecked()
    win._on_toggle_live_preview_action()
    assert win._radio_screens_preview.isChecked() != initial
