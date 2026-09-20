# -*- coding: utf-8 -*-
"""Tests for LayoutController instantiation and ScreenMapWidget drop signal handling."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

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
def mock_services() -> SimpleNamespace:
    svc = SimpleNamespace(
        config=MagicMock(),
        session=MagicMock(),
        layout=MagicMock(),
        templates=MagicMock(),
        repository=MagicMock(),
        key_service=MagicMock(),
        config_path=Path("/tmp/layout-ctrl-test.cpsm.yaml"),
        status_poller=MagicMock(),
        monitor_service=None,
    )
    # Provide _backend for LayoutController instantiation
    svc.session._backend = MagicMock()
    return svc


# ---------------------------------------------------------------------------
# LayoutController instantiation
# ---------------------------------------------------------------------------


def test_layout_controller_is_created_when_services_present(
    qtbot, empty_doc: CpsmDocument, mock_services: SimpleNamespace
) -> None:
    """LayoutController should be instantiated when services are provided."""
    w = MainWindow(services=mock_services, document=empty_doc)
    qtbot.addWidget(w)
    w.show()
    assert w._layout_controller is not None


def test_layout_controller_is_none_without_services(qtbot, empty_doc: CpsmDocument) -> None:
    """LayoutController should be None when no services are provided."""
    w = MainWindow(services=None, document=empty_doc)
    qtbot.addWidget(w)
    w.show()
    assert w._layout_controller is None


def test_layout_controller_none_on_import_error(
    qtbot, empty_doc: CpsmDocument, mock_services: SimpleNamespace, monkeypatch
) -> None:
    """If LayoutController cannot be imported, _layout_controller stays None."""
    monkeypatch.setattr(
        "cpsm.ui.main_window.MainWindow._layout_controller",
        None,
        raising=False,
    )
    # Patch to raise on import
    with patch(
        "cpsm.controllers.layout_controller.LayoutController.__init__",
        side_effect=RuntimeError("import fail"),
    ):
        w = MainWindow(services=mock_services, document=empty_doc)
        qtbot.addWidget(w)
        w.show()
    assert w._layout_controller is None


# ---------------------------------------------------------------------------
# Drop signal handlers
# ---------------------------------------------------------------------------


def test_on_screen_map_drop_connection_calls_controller(
    qtbot, empty_doc: CpsmDocument, mock_services: SimpleNamespace
) -> None:
    w = MainWindow(services=mock_services, document=empty_doc)
    qtbot.addWidget(w)
    w.show()

    if w._layout_controller is None:
        pytest.skip("LayoutController not instantiated in this environment")

    # The drop handler only delegates to LayoutController in Live mode now;
    # in Preview mode it mutates the document directly. Force Live for this
    # contract test.
    w._radio_screens_live.setChecked(True)

    # Replace controller with a mock to track calls
    mock_ctrl = MagicMock()
    w._layout_controller = mock_ctrl

    w._on_screen_map_drop_connection("conn-1", "pane-1", "right", 0)
    mock_ctrl.on_drop_connection.assert_called_with("conn-1", "pane-1", "right", 0)


def test_on_screen_map_drop_pane_calls_controller(
    qtbot, empty_doc: CpsmDocument, mock_services: SimpleNamespace
) -> None:
    w = MainWindow(services=mock_services, document=empty_doc)
    qtbot.addWidget(w)
    w.show()

    if w._layout_controller is None:
        pytest.skip("LayoutController not instantiated in this environment")

    # Live mode required for the controller path (see test above).
    w._radio_screens_live.setChecked(True)

    mock_ctrl = MagicMock()
    w._layout_controller = mock_ctrl

    w._on_screen_map_drop_pane("src-pane", "dst-pane", "bottom", 0)
    mock_ctrl.on_drop_pane.assert_called_with("src-pane", "dst-pane", "bottom", 0)


def test_on_screen_map_drop_connection_no_controller_is_safe(
    qtbot, empty_doc: CpsmDocument
) -> None:
    """Handler is a no-op when _layout_controller is None."""
    w = MainWindow(services=None, document=empty_doc)
    qtbot.addWidget(w)
    w.show()
    # Should not raise
    w._on_screen_map_drop_connection("conn-1", "pane-1", "right", 0)


def test_on_screen_map_drop_connection_handles_exception(
    qtbot, empty_doc: CpsmDocument, mock_services: SimpleNamespace, monkeypatch
) -> None:
    w = MainWindow(services=mock_services, document=empty_doc)
    qtbot.addWidget(w)
    w.show()

    # Live mode is the only path that delegates to LayoutController; force it
    # so the exception-handling assertion has a route.
    w._radio_screens_live.setChecked(True)

    mock_ctrl = MagicMock()
    mock_ctrl.on_drop_connection.side_effect = RuntimeError("backend error")
    w._layout_controller = mock_ctrl

    shown: list[str] = []
    monkeypatch.setattr(
        "cpsm.ui.main_window.QMessageBox.warning",
        lambda *a, **kw: shown.append(str(a)) or 0,
    )
    w._on_screen_map_drop_connection("conn-1", "pane-1", "right", 0)
    assert shown, "QMessageBox.warning should have been shown on error"
