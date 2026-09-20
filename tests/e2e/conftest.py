# -*- coding: utf-8 -*-
"""
Shared pytest fixtures for the cpsm E2E test package.

Sets QT_QPA_PLATFORM=offscreen so every test in this directory runs without
a real display server.

Provides:
  - qtbot          (from pytest-qt, re-exported via autouse)
  - tmp_config     tmp_path/.cpsm.yaml (empty or seeded)
  - tmp_legacy     tmp_path/.claude-projects.yaml (minimal legacy format)
  - mock_backend   MagicMock(spec=MultiplexerBackend) — never requires tmux
  - services_ns    SimpleNamespace of mocked services ready for MainWindow
  - main_window    fully-constructed MainWindow(services=services_ns)
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

# Ensure offscreen before any Qt import
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from cpsm.data.schema import CpsmDocument
from cpsm.platform.base import MultiplexerBackend
from cpsm.platform.process_runner import ProcessRunner
from cpsm.services.config_service import ConfigService
from cpsm.services.import_service import ImportService
from cpsm.services.key_service import KeyService
from cpsm.services.layout_service import LayoutService
from cpsm.services.monitor_service import MonitorService
from cpsm.services.session_service import SessionService
from cpsm.services.template_service import TemplateService

# ---------------------------------------------------------------------------
# Offscreen guard (session-scoped so Qt is only initialised once)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True, scope="session")
def _offscreen_platform() -> None:
    """Ensure Qt uses the offscreen platform during all E2E tests."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


# ---------------------------------------------------------------------------
# Config/legacy file fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_config(tmp_path):
    """Return a Path to a minimal .cpsm.yaml in a temp directory."""
    cfg = tmp_path / ".cpsm.yaml"
    cfg.write_text("schema_version: 1\n", encoding="utf-8")
    return cfg


@pytest.fixture()
def tmp_legacy(tmp_path):
    """Return a Path to a minimal .claude-projects.yaml (legacy format)."""
    legacy = tmp_path / ".claude-projects.yaml"
    legacy.write_text(
        "projects:\n"
        "  - name: web-frontend\n"
        "    host: dev.example.com\n"
        "    user: ubuntu\n"
        "    project_folder: /opt/webapp/frontend\n",
        encoding="utf-8",
    )
    return legacy


# ---------------------------------------------------------------------------
# Mocked backend
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_backend():
    """Return a MagicMock that satisfies MultiplexerBackend's interface."""
    import datetime

    from cpsm.platform.base import Session

    backend = MagicMock(spec=MultiplexerBackend)
    backend.list_sessions.return_value = []
    backend.new_session.return_value = Session(
        id="$0",
        name="cpsm-test",
        attached=False,
        created_at=datetime.datetime.now(),
    )
    backend.respawn_pane.return_value = None
    split_pane_result = MagicMock()
    split_pane_result.id = "%99"
    backend.split_pane.return_value = split_pane_result
    backend.swap_panes.return_value = None
    backend.kill_pane.return_value = None
    backend.kill_session.return_value = None
    backend.capture_pane.return_value = ""
    backend.capture_layout.return_value = "tiled"
    backend.set_window_option.return_value = None
    backend.select_layout.return_value = None
    backend.list_panes.return_value = []
    backend.list_windows.return_value = []
    backend.attach_session.return_value = None
    backend.send_keys.return_value = None
    backend.select_pane.return_value = None
    backend.kill_window.return_value = None
    backend.new_window.return_value = MagicMock()
    backend.break_pane.return_value = MagicMock()
    backend.move_pane.return_value = None
    backend.resize_pane.return_value = None
    return backend


@pytest.fixture()
def mock_runner():
    """Return a MagicMock ProcessRunner that records calls but does nothing."""
    runner = MagicMock(spec=ProcessRunner)
    runner.run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    return runner


@pytest.fixture()
def mock_keyring():
    """Return a MagicMock keyring module."""
    kr = MagicMock()
    kr.get_password.return_value = None
    return kr


# ---------------------------------------------------------------------------
# Services namespace
# ---------------------------------------------------------------------------


@pytest.fixture()
def services_ns(tmp_config, mock_backend, mock_runner, mock_keyring):
    """Return a SimpleNamespace of mocked services suitable for MainWindow."""
    from cpsm.data.repository import CpsmRepository

    repo = CpsmRepository()
    config_svc = ConfigService(repository=repo)
    template_svc = TemplateService()
    layout_svc = MagicMock(spec=LayoutService)
    layout_svc.apply_change.return_value = None

    session_svc = SessionService(
        config=config_svc,
        backend=mock_backend,
        templates=template_svc,
        layout=layout_svc,
    )
    key_svc = KeyService(runner=mock_runner, keyring_module=mock_keyring)
    import_svc = ImportService()
    monitor_svc = MagicMock(spec=MonitorService)
    monitor_svc.current_monitors.return_value = []

    return SimpleNamespace(
        config=config_svc,
        session=session_svc,
        key=key_svc,
        import_svc=import_svc,
        layout=layout_svc,
        template=template_svc,
        monitor=monitor_svc,
        backend=mock_backend,
    )


# ---------------------------------------------------------------------------
# Main window fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def main_window(qtbot, services_ns):
    """Construct a fully-built MainWindow backed by mocked services."""
    from cpsm.ui.main_window import MainWindow

    doc = CpsmDocument()
    win = MainWindow(services=services_ns, document=doc)
    qtbot.addWidget(win)
    win.show()
    return win
