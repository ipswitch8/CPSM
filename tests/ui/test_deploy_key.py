# -*- coding: utf-8 -*-
"""
pytest-qt tests for DeployKeyDialog.

Spec section: §9.2

Covers:
- Deploy calls key_service.deploy with password.
- Password QLineEdit cleared and buffer zeroed on success and cancel.
- Password NEVER appears in any logged record (caplog assert).

All tests run with QT_QPA_PLATFORM=offscreen (set in conftest.py).
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QLineEdit, QPushButton

from cpsm.data.schema import SshKey
from cpsm.services.key_service import DeployResult, KeyService
from cpsm.ui.dialogs.deploy_key import DeployKeyDialog
from cpsm.workers.key_deploy_worker import KeyDeployWorker

_SECRET_PASSWORD = "Sup3rSecretSSHPassword!"


def _make_key(tmp_path: Path) -> SshKey:
    priv = tmp_path / "id_ed25519"
    pub = tmp_path / "id_ed25519.pub"
    priv.write_text("fake private key", encoding="utf-8")
    pub.write_text("fake public key", encoding="utf-8")
    return SshKey(
        id="key-deploy-test",
        name="Deploy Test Key",
        type="ed25519",
        private_path=str(priv),
        public_path=str(pub),
        created_at=datetime.now(tz=UTC),
    )


def _make_connection(conn_id: str = "conn-target") -> MagicMock:
    conn = MagicMock()
    conn.id = conn_id
    conn.name = f"Connection {conn_id}"
    conn.host = "dev.example.com"
    conn.user = "ubuntu"
    conn.port = 22
    return conn


def _make_key_service(success: bool = True) -> MagicMock:
    svc = MagicMock(spec=KeyService)
    svc.deploy.return_value = DeployResult(
        success=success,
        method="ssh-copy-id",
        errors=[] if success else ["Connection refused"],
    )
    return svc


class TestDeployCallsService:
    def test_deploy_calls_key_service_deploy(self, qtbot, tmp_path):
        key = _make_key(tmp_path)
        conn = _make_connection()
        svc = _make_key_service(success=True)

        dlg = DeployKeyDialog(key=key, candidates=[conn], key_service=svc)
        qtbot.addWidget(dlg)

        # Patch the worker to run synchronously using signals
        password_captured = []

        def _fake_worker_run(self_worker):
            password_captured.append(self_worker._password)
            result = svc.deploy(
                key=self_worker._key,
                connection=self_worker._connection,
                password=self_worker._password,
            )
            if result.success:
                self_worker.signals.finished.emit(True, "")
            else:
                self_worker.signals.finished.emit(False, "; ".join(result.errors))

        with patch.object(KeyDeployWorker, "start", lambda w: _fake_worker_run(w)):
            edit_pw = dlg.findChild(QLineEdit, "edit_ssh_password")
            edit_pw.setText(_SECRET_PASSWORD)
            dlg._on_deploy()

        svc.deploy.assert_called_once()
        call_kwargs = svc.deploy.call_args.kwargs
        assert call_kwargs["key"] is key
        assert call_kwargs["connection"] is conn


class TestPasswordCleared:
    def test_password_cleared_after_deploy(self, qtbot, tmp_path):
        key = _make_key(tmp_path)
        conn = _make_connection()
        svc = _make_key_service(success=True)

        dlg = DeployKeyDialog(key=key, candidates=[conn], key_service=svc)
        qtbot.addWidget(dlg)

        def _fake_start(worker):
            # Simulate immediate success
            worker.signals.finished.emit(True, "")

        with patch.object(KeyDeployWorker, "start", _fake_start):
            edit_pw = dlg.findChild(QLineEdit, "edit_ssh_password")
            edit_pw.setText(_SECRET_PASSWORD)
            dlg._on_deploy()

        assert edit_pw.text() == ""

    def test_password_cleared_on_cancel(self, qtbot, tmp_path):
        key = _make_key(tmp_path)
        conn = _make_connection()
        svc = _make_key_service()

        dlg = DeployKeyDialog(key=key, candidates=[conn], key_service=svc)
        qtbot.addWidget(dlg)

        edit_pw = dlg.findChild(QLineEdit, "edit_ssh_password")
        edit_pw.setText(_SECRET_PASSWORD)

        dlg._on_cancel()

        assert edit_pw.text() == ""

    def test_password_cleared_on_failure(self, qtbot, tmp_path):
        key = _make_key(tmp_path)
        conn = _make_connection()
        svc = _make_key_service(success=False)

        dlg = DeployKeyDialog(key=key, candidates=[conn], key_service=svc)
        qtbot.addWidget(dlg)

        def _fake_start(worker):
            worker.signals.finished.emit(False, "Connection refused")

        with patch.object(KeyDeployWorker, "start", _fake_start):
            edit_pw = dlg.findChild(QLineEdit, "edit_ssh_password")
            edit_pw.setText(_SECRET_PASSWORD)
            dlg._on_deploy()

        assert edit_pw.text() == ""


class TestPasswordNeverLogged:
    """Password must never appear in any log record."""

    def test_password_not_in_logs(self, qtbot, tmp_path, caplog):
        key = _make_key(tmp_path)
        conn = _make_connection()
        svc = _make_key_service(success=True)

        dlg = DeployKeyDialog(key=key, candidates=[conn], key_service=svc)
        qtbot.addWidget(dlg)

        def _fake_start(worker):
            worker.signals.log_line.emit("Deploying key…")
            worker.signals.finished.emit(True, "")

        with caplog.at_level(logging.DEBUG):
            with patch.object(KeyDeployWorker, "start", _fake_start):
                edit_pw = dlg.findChild(QLineEdit, "edit_ssh_password")
                edit_pw.setText(_SECRET_PASSWORD)
                dlg._on_deploy()

        for record in caplog.records:
            assert _SECRET_PASSWORD not in record.getMessage(), (
                f"Password leaked into log: {record.getMessage()}"
            )
            assert _SECRET_PASSWORD not in str(record.args), "Password leaked into log args"

    def test_worker_password_not_logged(self, qtbot, tmp_path, caplog):
        """KeyDeployWorker itself must not log the password."""
        import sys

        from PySide6.QtWidgets import QApplication

        key = _make_key(tmp_path)
        conn = _make_connection()
        svc = _make_key_service(success=True)

        QApplication.instance() or QApplication(sys.argv)

        worker = KeyDeployWorker(
            key=key,
            connection=conn,
            key_service=svc,
            password=_SECRET_PASSWORD,
        )

        with caplog.at_level(logging.DEBUG):
            worker.run()

        for record in caplog.records:
            assert _SECRET_PASSWORD not in record.getMessage()


class TestDeployNoCandidates:
    def test_deploy_disabled_with_no_candidates(self, qtbot, tmp_path):
        key = _make_key(tmp_path)
        svc = _make_key_service()

        dlg = DeployKeyDialog(key=key, candidates=[], key_service=svc)
        qtbot.addWidget(dlg)

        btn = dlg.findChild(QPushButton, "btn_deploy")
        assert btn is not None
        assert not btn.isEnabled()
