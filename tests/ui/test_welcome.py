# -*- coding: utf-8 -*-
"""
pytest-qt tests for cpsm.ui.dialogs.welcome.WelcomeDialog.

Spec section: §4.2

All tests run with QT_QPA_PLATFORM=offscreen (set in tests/ui/conftest.py).
"""

from __future__ import annotations

import os
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: I001

from cpsm.ui.dialogs.welcome import WelcomeDialog


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def dlg(qtbot):
    """Create and show a WelcomeDialog."""
    dialog = WelcomeDialog()
    qtbot.addWidget(dialog)
    dialog.show()
    return dialog


# ---------------------------------------------------------------------------
# Construction tests
# ---------------------------------------------------------------------------


def test_dialog_constructs_cleanly(qtbot):
    """WelcomeDialog should construct without raising."""
    dlg = WelcomeDialog()
    qtbot.addWidget(dlg)
    assert dlg is not None


def test_dialog_object_name(dlg):
    """The dialog must carry the stable objectName 'dlg_welcome'."""
    assert dlg.objectName() == "dlg_welcome"


def test_title(dlg):
    """Window title should be 'Welcome to CPSM'."""
    assert dlg.windowTitle() == "Welcome to CPSM"


# ---------------------------------------------------------------------------
# Stable objectName tests
# ---------------------------------------------------------------------------


def test_lbl_title_present(dlg):
    lbl = dlg.findChild(object, "lbl_title")
    assert lbl is not None


def test_lbl_description_present(dlg):
    lbl = dlg.findChild(object, "lbl_description")
    assert lbl is not None


def test_btn_import_present(dlg):
    btn = dlg.findChild(object, "btn_import")
    assert btn is not None


def test_btn_empty_present(dlg):
    btn = dlg.findChild(object, "btn_empty")
    assert btn is not None


def test_btn_open_present(dlg):
    btn = dlg.findChild(object, "btn_open")
    assert btn is not None


def test_btn_cancel_present(dlg):
    btn = dlg.findChild(object, "btn_cancel")
    assert btn is not None


# ---------------------------------------------------------------------------
# Choice behaviour — btn_empty
# ---------------------------------------------------------------------------


def test_empty_button_sets_choice(qtbot, dlg):
    """Clicking 'Start with an empty config' should set choice=EMPTY and accept."""
    from PySide6.QtWidgets import QPushButton

    btn = dlg.findChild(QPushButton, "btn_empty")
    assert btn is not None

    with qtbot.waitSignal(dlg.accepted, timeout=2000):
        btn.click()

    assert dlg.choice == WelcomeDialog.Choice.EMPTY
    assert dlg.source_path is None


# ---------------------------------------------------------------------------
# Choice behaviour — btn_import
# ---------------------------------------------------------------------------


def test_import_button_opens_file_dialog(qtbot, dlg, tmp_path):
    """Clicking Import should open a file picker; on accept sets IMPORT choice."""
    from PySide6.QtWidgets import QPushButton

    fake_file = tmp_path / "fake-.claude-projects.yaml"
    fake_file.write_text("projects: []", encoding="utf-8")

    btn = dlg.findChild(QPushButton, "btn_import")
    assert btn is not None

    with patch(
        "cpsm.ui.dialogs.welcome.QFileDialog.getOpenFileName",
        return_value=(str(fake_file), "YAML files (*.yaml *.yml);;All files (*)"),
    ):
        with qtbot.waitSignal(dlg.accepted, timeout=2000):
            btn.click()

    assert dlg.choice == WelcomeDialog.Choice.IMPORT
    assert dlg.source_path == fake_file


def test_import_button_cancel_keeps_cancel_choice(qtbot, dlg):
    """If the file picker is dismissed without selection, dialog stays open."""
    from PySide6.QtWidgets import QPushButton

    btn = dlg.findChild(QPushButton, "btn_import")
    assert btn is not None

    with patch(
        "cpsm.ui.dialogs.welcome.QFileDialog.getOpenFileName",
        return_value=("", ""),
    ):
        btn.click()  # no signal expected — dialog remains open

    assert dlg.choice == WelcomeDialog.Choice.CANCEL
    assert dlg.source_path is None


# ---------------------------------------------------------------------------
# Choice behaviour — btn_open
# ---------------------------------------------------------------------------


def test_open_button_sets_choice(qtbot, dlg, tmp_path):
    """Clicking Open should open a file picker; on accept sets OPEN choice."""
    from PySide6.QtWidgets import QPushButton

    cpsm_file = tmp_path / "my.cpsm.yaml"
    cpsm_file.write_text("schema_version: 1\n", encoding="utf-8")

    btn = dlg.findChild(QPushButton, "btn_open")
    assert btn is not None

    with patch(
        "cpsm.ui.dialogs.welcome.QFileDialog.getOpenFileName",
        return_value=(str(cpsm_file), "CPSM config (*.yaml *.yml);;All files (*)"),
    ):
        with qtbot.waitSignal(dlg.accepted, timeout=2000):
            btn.click()

    assert dlg.choice == WelcomeDialog.Choice.OPEN
    assert dlg.source_path == cpsm_file


def test_open_button_cancel_keeps_cancel_choice(qtbot, dlg):
    """If the open file picker is dismissed without selection, dialog stays open."""
    from PySide6.QtWidgets import QPushButton

    btn = dlg.findChild(QPushButton, "btn_open")
    assert btn is not None

    with patch(
        "cpsm.ui.dialogs.welcome.QFileDialog.getOpenFileName",
        return_value=("", ""),
    ):
        btn.click()

    assert dlg.choice == WelcomeDialog.Choice.CANCEL


# ---------------------------------------------------------------------------
# Choice behaviour — btn_cancel / Esc
# ---------------------------------------------------------------------------


def test_cancel_button_sets_cancel_choice(qtbot, dlg):
    """Clicking Cancel should set choice=CANCEL and reject."""
    from PySide6.QtWidgets import QPushButton

    btn = dlg.findChild(QPushButton, "btn_cancel")
    assert btn is not None

    with qtbot.waitSignal(dlg.rejected, timeout=2000):
        btn.click()

    assert dlg.choice == WelcomeDialog.Choice.CANCEL


def test_default_choice_is_cancel(qtbot):
    """Before any interaction the choice defaults to CANCEL."""
    dlg = WelcomeDialog()
    qtbot.addWidget(dlg)
    assert dlg.choice == WelcomeDialog.Choice.CANCEL
    assert dlg.source_path is None


# ---------------------------------------------------------------------------
# Choice enum values
# ---------------------------------------------------------------------------


def test_choice_enum_values():
    """Choice enum members carry expected string values."""
    assert WelcomeDialog.Choice.IMPORT.value == "import"
    assert WelcomeDialog.Choice.EMPTY.value == "empty"
    assert WelcomeDialog.Choice.OPEN.value == "open"
    assert WelcomeDialog.Choice.CANCEL.value == "cancel"
