# -*- coding: utf-8 -*-
"""
pytest-qt tests for GenerateKeyDialog.

Spec section: §9.2

Covers:
- Confirm-passphrase mismatch disables Generate button.
- Generate calls key_service.generate_ed25519 with matching args.
- Passphrase NEVER appears in any logged record (caplog assert).
- Passphrase QLineEdit cleared after Generate.

All tests run with QT_QPA_PLATFORM=offscreen (set in conftest.py).
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QCheckBox, QLineEdit, QPushButton

from cpsm.data.schema import SshKey
from cpsm.services.key_service import KeyService
from cpsm.ui.dialogs.generate_key import GenerateKeyDialog

_SECRET_PASSPHRASE = "S3cr3tPassphrase!"


def _make_key_service(tmp_path: Path) -> MagicMock:
    svc = MagicMock(spec=KeyService)

    def _gen(*, key_id, private_path, comment="", passphrase=None):
        private_path.write_text("fake", encoding="utf-8")
        return SshKey(
            id=key_id,
            name=comment or key_id,
            type="ed25519",
            private_path=str(private_path),
            public_path=str(private_path) + ".pub",
            passphrase_ref=f"keyring://cpsm/{key_id}" if passphrase else None,
            created_at=datetime.now(tz=UTC),
        )

    svc.generate_ed25519.side_effect = _gen
    return svc


class TestPassphraseMismatch:
    def test_mismatch_disables_generate(self, qtbot):
        svc = MagicMock(spec=KeyService)
        dlg = GenerateKeyDialog(svc, Path("/tmp"))
        qtbot.addWidget(dlg)

        # Enable passphrase
        chk = dlg.findChild(QCheckBox, "chk_use_passphrase")
        chk.setChecked(True)

        pp = dlg.findChild(QLineEdit, "edit_passphrase")
        cf = dlg.findChild(QLineEdit, "edit_confirm_passphrase")
        pp.setText("abc123XY")
        cf.setText("different!")

        btn = dlg.findChild(QPushButton, "btn_generate")
        assert not btn.isEnabled()

    def test_match_enables_generate(self, qtbot):
        svc = MagicMock(spec=KeyService)
        dlg = GenerateKeyDialog(svc, Path("/tmp"))
        qtbot.addWidget(dlg)

        chk = dlg.findChild(QCheckBox, "chk_use_passphrase")
        chk.setChecked(True)

        pp = dlg.findChild(QLineEdit, "edit_passphrase")
        cf = dlg.findChild(QLineEdit, "edit_confirm_passphrase")
        pp.setText("abc123XY")
        cf.setText("abc123XY")

        btn = dlg.findChild(QPushButton, "btn_generate")
        assert btn.isEnabled()

    def test_no_passphrase_generate_enabled(self, qtbot):
        svc = MagicMock(spec=KeyService)
        dlg = GenerateKeyDialog(svc, Path("/tmp"))
        qtbot.addWidget(dlg)

        chk = dlg.findChild(QCheckBox, "chk_use_passphrase")
        assert not chk.isChecked()

        btn = dlg.findChild(QPushButton, "btn_generate")
        assert btn.isEnabled()


class TestGenerateCallsService:
    def test_calls_generate_ed25519(self, qtbot, tmp_path):
        svc = _make_key_service(tmp_path)
        dlg = GenerateKeyDialog(svc, tmp_path)
        qtbot.addWidget(dlg)

        name_edit = dlg.findChild(QLineEdit, "edit_filename")
        name_edit.setText("id_test_key")
        comment_edit = dlg.findChild(QLineEdit, "edit_comment")
        comment_edit.setText("test comment")

        dlg._on_generate()

        svc.generate_ed25519.assert_called_once()
        call_kwargs = svc.generate_ed25519.call_args.kwargs
        assert call_kwargs["comment"] == "test comment"
        assert call_kwargs["passphrase"] is None
        assert "id_test_key" in str(call_kwargs["private_path"])

    def test_calls_with_passphrase(self, qtbot, tmp_path):
        svc = _make_key_service(tmp_path)
        dlg = GenerateKeyDialog(svc, tmp_path)
        qtbot.addWidget(dlg)

        name_edit = dlg.findChild(QLineEdit, "edit_filename")
        name_edit.setText("id_pp_key")

        chk = dlg.findChild(QCheckBox, "chk_use_passphrase")
        chk.setChecked(True)

        pp = dlg.findChild(QLineEdit, "edit_passphrase")
        cf = dlg.findChild(QLineEdit, "edit_confirm_passphrase")
        pp.setText(_SECRET_PASSPHRASE)
        cf.setText(_SECRET_PASSPHRASE)

        dlg._on_generate()

        call_kwargs = svc.generate_ed25519.call_args.kwargs
        assert call_kwargs["passphrase"] == _SECRET_PASSPHRASE

    def test_created_key_set_on_accept(self, qtbot, tmp_path):
        svc = _make_key_service(tmp_path)
        dlg = GenerateKeyDialog(svc, tmp_path)
        qtbot.addWidget(dlg)

        name_edit = dlg.findChild(QLineEdit, "edit_filename")
        name_edit.setText("id_result_key")

        dlg._on_generate()
        assert dlg.created_key is not None
        assert isinstance(dlg.created_key, SshKey)


class TestPassphraseNeverLogged:
    """Passphrase must never appear in any log record."""

    def test_passphrase_not_in_logs(self, qtbot, tmp_path, caplog):
        svc = _make_key_service(tmp_path)
        dlg = GenerateKeyDialog(svc, tmp_path)
        qtbot.addWidget(dlg)

        name_edit = dlg.findChild(QLineEdit, "edit_filename")
        name_edit.setText("id_secret_key")

        chk = dlg.findChild(QCheckBox, "chk_use_passphrase")
        chk.setChecked(True)
        pp = dlg.findChild(QLineEdit, "edit_passphrase")
        cf = dlg.findChild(QLineEdit, "edit_confirm_passphrase")
        pp.setText(_SECRET_PASSPHRASE)
        cf.setText(_SECRET_PASSPHRASE)

        with caplog.at_level(logging.DEBUG):
            dlg._on_generate()

        for record in caplog.records:
            assert _SECRET_PASSPHRASE not in record.getMessage(), (
                f"Passphrase leaked into log: {record.getMessage()}"
            )
            assert _SECRET_PASSPHRASE not in str(record.args), "Passphrase leaked into log args"


class TestPassphraseFieldCleared:
    def test_passphrase_edit_cleared_after_generate(self, qtbot, tmp_path):
        svc = _make_key_service(tmp_path)
        dlg = GenerateKeyDialog(svc, tmp_path)
        qtbot.addWidget(dlg)

        name_edit = dlg.findChild(QLineEdit, "edit_filename")
        name_edit.setText("id_clear_test")

        chk = dlg.findChild(QCheckBox, "chk_use_passphrase")
        chk.setChecked(True)
        pp = dlg.findChild(QLineEdit, "edit_passphrase")
        cf = dlg.findChild(QLineEdit, "edit_confirm_passphrase")
        pp.setText(_SECRET_PASSPHRASE)
        cf.setText(_SECRET_PASSPHRASE)

        dlg._on_generate()

        # Both fields must be empty after generation
        assert pp.text() == ""
        assert cf.text() == ""

    def test_passphrase_cleared_on_failure(self, qtbot, tmp_path):
        """Even when generation fails, passphrase fields are cleared."""
        svc = MagicMock(spec=KeyService)
        svc.generate_ed25519.side_effect = RuntimeError("disk full")

        dlg = GenerateKeyDialog(svc, tmp_path)
        qtbot.addWidget(dlg)

        name_edit = dlg.findChild(QLineEdit, "edit_filename")
        name_edit.setText("id_fail_key")

        chk = dlg.findChild(QCheckBox, "chk_use_passphrase")
        chk.setChecked(True)
        pp = dlg.findChild(QLineEdit, "edit_passphrase")
        cf = dlg.findChild(QLineEdit, "edit_confirm_passphrase")
        pp.setText(_SECRET_PASSPHRASE)
        cf.setText(_SECRET_PASSPHRASE)

        dlg._on_generate()

        assert pp.text() == ""
        assert cf.text() == ""
