# -*- coding: utf-8 -*-
"""Tests for cpsm.app — first-run welcome dispatch wiring in run_gui().

Covers the dispatch logic: how `run_gui()` and `_run_first_run_flow()` route
based on the user's WelcomeDialog choice. The dialog widget itself is covered
in tests/ui/test_welcome.py.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# Ensure offscreen Qt platform before any Qt imports
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from cpsm.data.schema import CpsmDocument, Settings
from cpsm.ui.dialogs.welcome import WelcomeDialog


@pytest.fixture
def fake_services():
    """A SimpleNamespace whose members are MagicMocks."""
    return SimpleNamespace(
        config=MagicMock(),
        session=MagicMock(),
        layout=MagicMock(),
        templates=MagicMock(),
        repository=MagicMock(),
    )


@pytest.fixture
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


def _stub_dialog_exec(monkeypatch, choice: WelcomeDialog.Choice, source_path=None):
    """Patch WelcomeDialog.exec so the dispatch sees the requested choice
    without spinning a real event loop. Uses a real WelcomeDialog instance so
    the `is`-comparison against the real Choice enum works.
    """

    def fake_exec(self):
        self._choice = choice
        self._source_path = source_path
        return 1 if choice is not WelcomeDialog.Choice.CANCEL else 0

    monkeypatch.setattr(WelcomeDialog, "exec", fake_exec)


# ---------------------------------------------------------------------------
# _run_first_run_flow — branch on each Choice
# ---------------------------------------------------------------------------


class TestFirstRunFlowBranches:
    def test_cancel_returns_none(self, qapp, fake_services, tmp_path, monkeypatch) -> None:
        from cpsm.app import _run_first_run_flow

        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        _stub_dialog_exec(monkeypatch, WelcomeDialog.Choice.CANCEL)

        result = _run_first_run_flow(fake_services, None)
        assert result is None

    def test_empty_writes_minimal_doc(self, qapp, fake_services, tmp_path, monkeypatch) -> None:
        from cpsm.app import _run_first_run_flow

        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        _stub_dialog_exec(monkeypatch, WelcomeDialog.Choice.EMPTY)

        result = _run_first_run_flow(fake_services, None)

        assert result is not None
        assert fake_services.repository.save.called, "Empty branch must save the new doc"
        saved_doc = fake_services.repository.save.call_args.args[0]
        assert isinstance(saved_doc, CpsmDocument)
        assert saved_doc.connections == []
        assert saved_doc.groups == []

    def test_open_returns_user_picked_path(
        self, qapp, fake_services, tmp_path, monkeypatch
    ) -> None:
        from cpsm.app import _run_first_run_flow

        monkeypatch.setenv("HOME", str(tmp_path))
        picked = tmp_path / "my-config.yaml"
        _stub_dialog_exec(monkeypatch, WelcomeDialog.Choice.OPEN, source_path=picked)

        result = _run_first_run_flow(fake_services, None)
        assert result == picked
        assert not fake_services.repository.save.called

    def test_import_runs_import_service(self, qapp, fake_services, tmp_path, monkeypatch) -> None:
        from cpsm.app import _run_first_run_flow

        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        legacy = tmp_path / "legacy.yaml"
        legacy.write_text("projects: []\n")
        _stub_dialog_exec(monkeypatch, WelcomeDialog.Choice.IMPORT, source_path=legacy)

        with patch("cpsm.services.import_service.ImportService") as mock_import_class:
            result = _run_first_run_flow(fake_services, None)

        assert result is not None
        assert mock_import_class.return_value.import_legacy_to.called
        call_args = mock_import_class.return_value.import_legacy_to.call_args
        assert call_args.args[0] == legacy

    def test_import_with_no_source_returns_none(
        self, qapp, fake_services, tmp_path, monkeypatch
    ) -> None:
        """IMPORT but source_path is None — exits gracefully."""
        from cpsm.app import _run_first_run_flow

        monkeypatch.setenv("HOME", str(tmp_path))
        _stub_dialog_exec(monkeypatch, WelcomeDialog.Choice.IMPORT, source_path=None)

        result = _run_first_run_flow(fake_services, None)
        assert result is None


# ---------------------------------------------------------------------------
# run_gui — fast path when config exists, vs. welcome path when it doesn't
# ---------------------------------------------------------------------------


class TestRunGuiDispatch:
    def test_existing_config_skips_welcome(self, qapp, fake_services, tmp_path, empty_doc) -> None:
        """If the resolved config exists, run_gui loads it directly."""
        config_path = tmp_path / ".cpsm.yaml"
        config_path.write_text("schema_version: 1\nsettings: {}\n", encoding="utf-8")

        with (
            patch("cpsm.app._make_services", return_value=fake_services),
            patch("cpsm.app._run_first_run_flow") as mock_flow,
            patch("cpsm.ui.main_window.MainWindow") as mock_window_class,
        ):
            fake_services.config.load.return_value = empty_doc
            # qapp is a real QApplication; stub its exec()
            with patch.object(qapp, "exec", return_value=0):
                from cpsm.app import run_gui

                rc = run_gui(config_path=config_path)

        assert rc == 0
        assert not mock_flow.called, "welcome flow must not run when config exists"
        mock_window_class.assert_called_once()

    def test_missing_config_runs_welcome(self, qapp, fake_services, tmp_path, empty_doc) -> None:
        """If the resolved config doesn't exist, run_gui invokes the welcome flow."""
        config_path = tmp_path / "nope.yaml"
        chosen_path = tmp_path / "chosen.yaml"
        chosen_path.write_text("schema_version: 1\nsettings: {}\n")

        with (
            patch("cpsm.app._make_services", return_value=fake_services),
            patch("cpsm.app._run_first_run_flow", return_value=chosen_path) as mock_flow,
            patch("cpsm.ui.main_window.MainWindow") as mock_window_class,
        ):
            fake_services.config.load.return_value = empty_doc
            with patch.object(qapp, "exec", return_value=0):
                from cpsm.app import run_gui

                rc = run_gui(config_path=config_path)

        assert rc == 0
        mock_flow.assert_called_once()
        mock_window_class.assert_called_once()

    def test_welcome_cancel_exits_without_main_window(self, qapp, fake_services, tmp_path) -> None:
        """If welcome flow returns None (Cancel), main window is not shown."""
        config_path = tmp_path / "nope.yaml"

        with (
            patch("cpsm.app._make_services", return_value=fake_services),
            patch("cpsm.app._run_first_run_flow", return_value=None),
            patch("cpsm.ui.main_window.MainWindow") as mock_window_class,
        ):
            from cpsm.app import run_gui

            rc = run_gui(config_path=config_path)

        assert rc == 0
        assert not mock_window_class.called, "main window must not show when user cancels"


# ---------------------------------------------------------------------------
# _make_services — basic shape
# ---------------------------------------------------------------------------


def test_make_services_returns_full_stack() -> None:
    """_make_services should produce a SimpleNamespace with all expected attrs."""
    from cpsm.app import _make_services

    services = _make_services()
    for name in ("config", "session", "layout", "templates", "repository"):
        assert hasattr(services, name), f"missing attr: {name}"


# ---------------------------------------------------------------------------
# Fix #8 — config_path persistence
# ---------------------------------------------------------------------------


class TestConfigPathPersistence:
    """run_gui sets services.config_path = target after loading the config."""

    def test_existing_config_sets_config_path(
        self, qapp, fake_services, tmp_path, empty_doc
    ) -> None:
        """When config exists, services.config_path must be set to that path."""
        config_path = tmp_path / ".cpsm.yaml"
        config_path.write_text("schema_version: 1\nsettings: {}\n", encoding="utf-8")

        with (
            patch("cpsm.app._make_services", return_value=fake_services),
            patch("cpsm.app._run_first_run_flow"),
            patch("cpsm.ui.main_window.MainWindow"),
        ):
            fake_services.config.load.return_value = empty_doc
            with patch.object(qapp, "exec", return_value=0):
                from cpsm.app import run_gui

                run_gui(config_path=config_path)

        assert fake_services.config_path == config_path

    def test_welcome_flow_sets_config_path(self, qapp, fake_services, tmp_path, empty_doc) -> None:
        """When welcome flow picks a path, services.config_path must be that path."""
        config_path = tmp_path / "nope.yaml"
        chosen_path = tmp_path / "chosen.yaml"
        chosen_path.write_text("schema_version: 1\nsettings: {}\n", encoding="utf-8")

        with (
            patch("cpsm.app._make_services", return_value=fake_services),
            patch("cpsm.app._run_first_run_flow", return_value=chosen_path),
            patch("cpsm.ui.main_window.MainWindow"),
        ):
            fake_services.config.load.return_value = empty_doc
            with patch.object(qapp, "exec", return_value=0):
                from cpsm.app import run_gui

                run_gui(config_path=config_path)

        assert fake_services.config_path == chosen_path


# ---------------------------------------------------------------------------
# Fix #2 — status_poller started in run_gui
# ---------------------------------------------------------------------------


class TestStatusPollerStarted:
    def test_status_poller_started_when_present(
        self, qapp, fake_services, tmp_path, empty_doc
    ) -> None:
        """run_gui calls status_poller.start() when the attribute is present."""
        config_path = tmp_path / ".cpsm.yaml"
        config_path.write_text("schema_version: 1\nsettings: {}\n", encoding="utf-8")

        fake_services.status_poller = MagicMock()
        fake_services.config_path = None

        with (
            patch("cpsm.app._make_services", return_value=fake_services),
            patch("cpsm.app._run_first_run_flow"),
            patch("cpsm.ui.main_window.MainWindow"),
        ):
            fake_services.config.load.return_value = empty_doc
            with patch.object(qapp, "exec", return_value=0):
                from cpsm.app import run_gui

                run_gui(config_path=config_path)

        fake_services.status_poller.start.assert_called_once()

    def test_make_services_includes_status_poller(self) -> None:
        """_make_services now includes a status_poller attribute."""
        from cpsm.app import _make_services

        services = _make_services()
        assert hasattr(services, "status_poller"), "status_poller missing from services"

    def test_make_services_includes_config_path(self) -> None:
        """_make_services now includes config_path=None."""
        from cpsm.app import _make_services

        services = _make_services()
        assert hasattr(services, "config_path"), "config_path missing from services"
        assert services.config_path is None

    def test_make_services_includes_key_service(self) -> None:
        """_make_services now includes key_service."""
        from cpsm.app import _make_services

        services = _make_services()
        assert hasattr(services, "key_service"), "key_service missing from services"
