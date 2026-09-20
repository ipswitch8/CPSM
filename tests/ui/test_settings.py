# -*- coding: utf-8 -*-
"""
pytest-qt tests for SettingsDialog.

Spec section: §4.7

Covers:
- Every Settings field rendered with the correct widget type.
- OK accepts and writes model; Cancel discards changes.
- collect_data() round-trips all field values.

All tests run with QT_QPA_PLATFORM=offscreen (set in conftest.py).
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QRadioButton,
    QSpinBox,
)

from cpsm.data.schema import Settings
from cpsm.ui.dialogs.settings import SettingsDialog

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings(**overrides) -> Settings:
    s = Settings()
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSettingsWidgetTypes:
    """Each Settings field must be bound to a type-correct widget."""

    def test_multiplexer_radios_present(self, qtbot):
        s = _make_settings()
        dlg = SettingsDialog(s)
        qtbot.addWidget(dlg)
        for opt in ("tmux", "itmux", "psmux", "auto"):
            rb = dlg.findChild(QRadioButton, f"radio_mux_{opt}")
            assert rb is not None, f"Missing radio for multiplexer={opt}"

    def test_terminal_combo_present(self, qtbot):
        s = _make_settings()
        dlg = SettingsDialog(s)
        qtbot.addWidget(dlg)
        combo = dlg.findChild(QComboBox, "combo_terminal")
        assert combo is not None
        items = [combo.itemText(i) for i in range(combo.count())]
        assert "auto" in items
        assert "wt" in items
        assert "gnome-terminal" in items

    def test_ssh_binary_radios_present(self, qtbot):
        s = _make_settings()
        dlg = SettingsDialog(s)
        qtbot.addWidget(dlg)
        for opt in ("auto", "openssh", "plink"):
            rb = dlg.findChild(QRadioButton, f"radio_ssh_{opt}")
            assert rb is not None

    def test_status_poll_is_spinbox(self, qtbot):
        s = _make_settings()
        dlg = SettingsDialog(s)
        qtbot.addWidget(dlg)
        spin = dlg.findChild(QSpinBox, "spin_status_poll_interval_ms")
        assert spin is not None
        assert spin.minimum() == 1000
        assert spin.maximum() == 30000

    def test_known_hosts_is_checkbox(self, qtbot):
        s = _make_settings()
        dlg = SettingsDialog(s)
        qtbot.addWidget(dlg)
        chk = dlg.findChild(QCheckBox, "chk_known_hosts_strict")
        assert chk is not None

    def test_layout_conflict_combo(self, qtbot):
        s = _make_settings()
        dlg = SettingsDialog(s)
        qtbot.addWidget(dlg)
        combo = dlg.findChild(QComboBox, "combo_layout_conflict_default")
        assert combo is not None
        texts = [combo.itemText(i) for i in range(combo.count())]
        assert "move" in texts
        assert "keep" in texts
        assert "error" in texts

    def test_preserve_on_remove_checkbox(self, qtbot):
        s = _make_settings()
        dlg = SettingsDialog(s)
        qtbot.addWidget(dlg)
        chk = dlg.findChild(QCheckBox, "chk_layout_preserve_on_remove")
        assert chk is not None

    def test_log_level_combo(self, qtbot):
        s = _make_settings()
        dlg = SettingsDialog(s)
        qtbot.addWidget(dlg)
        combo = dlg.findChild(QComboBox, "combo_log_level")
        assert combo is not None
        texts = [combo.itemText(i) for i in range(combo.count())]
        for level in ("DEBUG", "INFO", "WARNING", "ERROR"):
            assert level in texts


class TestSettingsLoad:
    """Dialog populates widgets from the Settings model."""

    def test_multiplexer_preselected(self, qtbot):
        s = _make_settings(default_multiplexer="psmux")
        dlg = SettingsDialog(s)
        qtbot.addWidget(dlg)
        rb = dlg.findChild(QRadioButton, "radio_mux_psmux")
        assert rb is not None and rb.isChecked()

    def test_terminal_preselected(self, qtbot):
        s = _make_settings(default_terminal="kitty")
        dlg = SettingsDialog(s)
        qtbot.addWidget(dlg)
        combo = dlg.findChild(QComboBox, "combo_terminal")
        assert combo.currentText() == "kitty"

    def test_ssh_binary_preselected(self, qtbot):
        s = _make_settings(ssh_binary="plink")
        dlg = SettingsDialog(s)
        qtbot.addWidget(dlg)
        rb = dlg.findChild(QRadioButton, "radio_ssh_plink")
        assert rb is not None and rb.isChecked()

    def test_known_hosts_false(self, qtbot):
        s = _make_settings(known_hosts_strict=False)
        dlg = SettingsDialog(s)
        qtbot.addWidget(dlg)
        chk = dlg.findChild(QCheckBox, "chk_known_hosts_strict")
        assert chk is not None and not chk.isChecked()

    def test_poll_interval(self, qtbot):
        s = _make_settings(status_poll_interval_ms=5000)
        dlg = SettingsDialog(s)
        qtbot.addWidget(dlg)
        spin = dlg.findChild(QSpinBox, "spin_status_poll_interval_ms")
        assert spin.value() == 5000

    def test_log_level(self, qtbot):
        s = _make_settings(log_level="DEBUG")
        dlg = SettingsDialog(s)
        qtbot.addWidget(dlg)
        combo = dlg.findChild(QComboBox, "combo_log_level")
        assert combo.currentText() == "DEBUG"


class TestSettingsOkCancel:
    """OK writes model; Cancel discards changes."""

    def test_ok_writes_model(self, qtbot):
        s = _make_settings(default_multiplexer="tmux", log_level="INFO")
        dlg = SettingsDialog(s)
        qtbot.addWidget(dlg)

        # Change multiplexer to itmux
        rb = dlg.findChild(QRadioButton, "radio_mux_itmux")
        rb.setChecked(True)
        # Change log level
        combo = dlg.findChild(QComboBox, "combo_log_level")
        combo.setCurrentText("ERROR")

        dlg._on_accept()
        assert s.default_multiplexer == "itmux"
        assert s.log_level == "ERROR"

    def test_cancel_does_not_write_model(self, qtbot):
        s = _make_settings(default_multiplexer="tmux")
        dlg = SettingsDialog(s)
        qtbot.addWidget(dlg)

        rb = dlg.findChild(QRadioButton, "radio_mux_psmux")
        rb.setChecked(True)
        dlg.reject()

        assert s.default_multiplexer == "tmux"  # unchanged


class TestCollectDataRoundTrip:
    """collect_data() returns all field values matching widget state."""

    def test_round_trip_all_fields(self, qtbot):
        s = _make_settings(
            default_multiplexer="itmux",
            default_terminal="alacritty",
            ssh_binary="openssh",
            default_claude_options="--dangerously-skip-permissions",
            default_ssh_options="-o ConnectTimeout=5",
            known_hosts_strict=False,
            status_poll_interval_ms=8000,
            layout_conflict_default="keep",
            layout_preserve_on_remove=False,
            log_level="WARNING",
        )
        dlg = SettingsDialog(s)
        qtbot.addWidget(dlg)
        data = dlg.collect_data()

        assert data["default_multiplexer"] == "itmux"
        assert data["default_terminal"] == "alacritty"
        assert data["ssh_binary"] == "openssh"
        assert data["default_claude_options"] == "--dangerously-skip-permissions"
        assert data["default_ssh_options"] == "-o ConnectTimeout=5"
        assert data["known_hosts_strict"] is False
        assert data["status_poll_interval_ms"] == 8000
        assert data["layout_conflict_default"] == "keep"
        assert data["layout_preserve_on_remove"] is False
        assert data["log_level"] == "WARNING"

    def test_ok_round_trips(self, qtbot):
        s = _make_settings()
        dlg = SettingsDialog(s)
        qtbot.addWidget(dlg)

        spin = dlg.findChild(QSpinBox, "spin_status_poll_interval_ms")
        spin.setValue(12000)

        dlg._on_accept()
        assert s.status_poll_interval_ms == 12000
