# -*- coding: utf-8 -*-
"""
pytest-qt tests for SshKeyManagerDialog, GenerateKeyDialog, DeployKeyDialog.

Spec sections: §4.8, §9.2

Covers:
- Empty key list shows empty table.
- Two keys shown with correct name/type/path.
- Permissions check: file with mode 0o644 shows warning row (Linux only).
- "Generate New Key" button opens GenerateKeyDialog (mock).
- "Delete" removes from doc.ssh_keys[].
- test_generate_key:
    - Confirm-passphrase mismatch disables Generate button.
    - Generate calls key_service.generate_ed25519 with matching args.
    - Passphrase never appears in any logged record.
    - Passphrase QLineEdit cleared after Generate.
- test_deploy_key:
    - Deploy calls key_service.deploy with password.
    - Password QLineEdit cleared and buffer zeroed on success and cancel.
    - Password never appears in any logged record.

All tests run with QT_QPA_PLATFORM=offscreen (set in conftest.py).
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QDialog, QMessageBox, QPushButton, QTableWidget

from cpsm.data.schema import CpsmDocument, SshKey
from cpsm.services.key_service import KeyService
from cpsm.ui.dialogs.ssh_key_manager import _COL_TYPE, SshKeyManagerDialog

# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_system_key_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent ``SshKeyManagerDialog._discover_system_keys`` from scanning
    the developer's real ``~/.ssh/`` (which would inject extra rows and
    break row-count assertions). Tests that specifically need to exercise
    discovery override this fixture locally."""
    monkeypatch.setattr(
        SshKeyManagerDialog,
        "_discover_system_keys",
        staticmethod(lambda existing: []),
    )


def _make_doc(*keys: SshKey) -> CpsmDocument:
    doc = CpsmDocument()
    doc.ssh_keys = list(keys)
    return doc


def _make_key(key_id: str, *, private_path: str = "~/.ssh/id_ed25519") -> SshKey:
    return SshKey(
        id=key_id,
        name=f"Key {key_id}",
        type="ed25519",
        private_path=private_path,
        public_path=private_path + ".pub",
        created_at=datetime.now(tz=UTC),
    )


def _make_key_service() -> MagicMock:
    svc = MagicMock(spec=KeyService)
    return svc


# ---------------------------------------------------------------------------
# SshKeyManagerDialog tests
# ---------------------------------------------------------------------------


class TestSshKeyManagerEmpty:
    def test_empty_table(self, qtbot):
        doc = _make_doc()
        svc = _make_key_service()
        dlg = SshKeyManagerDialog(doc, svc)
        qtbot.addWidget(dlg)

        table: QTableWidget = dlg.findChild(QTableWidget, "table_keys")
        assert table is not None
        assert table.rowCount() == 0


class TestSshKeyManagerTwoKeys:
    def test_two_keys_shown(self, qtbot):
        k1 = _make_key("key-alpha")
        k2 = _make_key("key-beta", private_path="~/.ssh/id_ed25519_beta")
        doc = _make_doc(k1, k2)
        svc = _make_key_service()
        dlg = SshKeyManagerDialog(doc, svc)
        qtbot.addWidget(dlg)

        table: QTableWidget = dlg.findChild(QTableWidget, "table_keys")
        assert table.rowCount() == 2

        # Row 0 name
        item0 = table.item(0, 0)
        assert item0 is not None
        assert "alpha" in item0.text().lower() or "key-alpha" in item0.text().lower()

        # Row 1 name
        item1 = table.item(1, 0)
        assert item1 is not None
        assert "beta" in item1.text().lower() or "key-beta" in item1.text().lower()

    def test_type_column(self, qtbot):
        k1 = _make_key("key-alpha")
        doc = _make_doc(k1)
        svc = _make_key_service()
        dlg = SshKeyManagerDialog(doc, svc)
        qtbot.addWidget(dlg)

        table: QTableWidget = dlg.findChild(QTableWidget, "table_keys")
        type_item = table.item(0, _COL_TYPE)
        assert type_item is not None
        assert type_item.text() == "ed25519"

    def test_path_column(self, qtbot):
        k1 = _make_key("key-alpha", private_path="~/.ssh/id_special")
        doc = _make_doc(k1)
        svc = _make_key_service()
        dlg = SshKeyManagerDialog(doc, svc)
        qtbot.addWidget(dlg)

        table: QTableWidget = dlg.findChild(QTableWidget, "table_keys")
        path_item = table.item(0, 2)
        assert path_item is not None
        assert "id_special" in path_item.text()


@pytest.mark.skipif(sys.platform == "win32", reason="Permissions check is Linux-only")
class TestPermissionsWarning:
    def test_broad_perms_shows_warning(self, qtbot, tmp_path):
        key_file = tmp_path / "id_test"
        key_file.write_bytes(b"fake key data")
        os.chmod(key_file, 0o644)

        k = SshKey(
            id="key-bad-perms",
            name="Bad Perms Key",
            type="ed25519",
            private_path=str(key_file),
            public_path=str(key_file) + ".pub",
        )
        doc = _make_doc(k)
        svc = _make_key_service()
        dlg = SshKeyManagerDialog(doc, svc)
        qtbot.addWidget(dlg)

        table: QTableWidget = dlg.findChild(QTableWidget, "table_keys")
        assert table.rowCount() == 1
        warn_item = table.item(0, _COL_TYPE)
        assert warn_item is not None
        text = warn_item.text()
        assert "⚠" in text or "permissions" in text.lower()

    def test_correct_perms_no_warning(self, qtbot, tmp_path):
        key_file = tmp_path / "id_ok"
        key_file.write_bytes(b"fake key data")
        os.chmod(key_file, 0o600)

        k = SshKey(
            id="key-ok-perms",
            name="OK Perms Key",
            type="ed25519",
            private_path=str(key_file),
            public_path=str(key_file) + ".pub",
        )
        doc = _make_doc(k)
        svc = _make_key_service()
        dlg = SshKeyManagerDialog(doc, svc)
        qtbot.addWidget(dlg)

        table: QTableWidget = dlg.findChild(QTableWidget, "table_keys")
        assert table.rowCount() == 1
        type_item = table.item(0, _COL_TYPE)
        assert type_item is not None
        assert type_item.text() == "ed25519"  # normal row


class TestSshKeyManagerGenerateButton:
    def test_generate_opens_dialog(self, qtbot, monkeypatch):
        doc = _make_doc()
        svc = _make_key_service()
        dlg = SshKeyManagerDialog(doc, svc)
        qtbot.addWidget(dlg)

        new_key = _make_key("key-new")
        mock_dlg = MagicMock()
        mock_dlg.exec.return_value = QDialog.DialogCode.Accepted
        mock_dlg.created_key = new_key

        with patch("cpsm.ui.dialogs.ssh_key_manager.GenerateKeyDialog") as MockGen:
            MockGen.return_value = mock_dlg
            btn = dlg.findChild(QPushButton, "btn_generate_new_key")
            assert btn is not None
            btn.click()
            MockGen.assert_called_once()

        # Key appended
        assert any(k.id == "key-new" for k in doc.ssh_keys)


class TestSshKeyManagerDeleteInUse:
    """Deleting a key that connections reference must not orphan them silently.

    importer.py creates a placeholder key ``imported-default`` and instructs
    the user to replace it via this dialog.  Following that instruction used to
    silently orphan every connection referencing it -- each kept an
    identity_file_ref resolving to nothing, so no -i reached ssh and the
    IdentitiesOnly pin had nothing to attach to.
    """

    @staticmethod
    def _doc_with_refs(key_id, n=2):
        from cpsm.data.schema import SshShellConnection

        doc = _make_doc(_make_key(key_id))
        doc.connections = [
            SshShellConnection(
                id="c%d" % i,
                name="Conn %d" % i,
                launch_profile="ssh-shell",
                host="10.0.0.%d" % (i + 1),
                user="root",
                identity_file_ref=key_id,
            )
            for i in range(n)
        ]
        return doc

    def test_referenced_key_prompts_and_cancel_keeps_it(self, qtbot, monkeypatch):
        doc = self._doc_with_refs("in-use")
        dlg = SshKeyManagerDialog(doc, _make_key_service())
        qtbot.addWidget(dlg)

        seen = {}

        class _FakeBox:
            Icon = QMessageBox.Icon
            StandardButton = QMessageBox.StandardButton

            def __init__(self, *a, **kw):
                pass

            def setObjectName(self, n):
                seen["name"] = n

            def setIcon(self, *a):
                pass

            def setWindowTitle(self, *a):
                pass

            def setText(self, t):
                seen["text"] = t

            def setInformativeText(self, t):
                seen["info"] = t

            def setStandardButtons(self, *a):
                pass

            def setDefaultButton(self, b):
                seen["default"] = b

            def exec(self):
                return QMessageBox.StandardButton.No

        monkeypatch.setattr(
            "cpsm.ui.dialogs.ssh_key_manager.QMessageBox", _FakeBox
        )
        dlg._on_delete(doc.ssh_keys[0])

        # Cancelled -> key survives.
        assert [k.id for k in doc.ssh_keys] == ["in-use"]
        # The warning must name what breaks, not just say "are you sure".
        assert "Conn 0" in seen["info"] and "Conn 1" in seen["info"]
        assert "2 connection(s)" in seen["text"]
        # Defaults to No so a reflexive Enter does not orphan anything.
        assert seen["default"] == QMessageBox.StandardButton.No
        assert seen["name"] == "msg_delete_key_in_use"

    def test_referenced_key_confirm_deletes(self, qtbot, monkeypatch):
        doc = self._doc_with_refs("in-use")
        dlg = SshKeyManagerDialog(doc, _make_key_service())
        qtbot.addWidget(dlg)

        class _YesBox:
            Icon = QMessageBox.Icon
            StandardButton = QMessageBox.StandardButton

            def __init__(self, *a, **kw):
                pass

            def __getattr__(self, _n):
                return lambda *a, **kw: None

            def exec(self):
                return QMessageBox.StandardButton.Yes

        monkeypatch.setattr(
            "cpsm.ui.dialogs.ssh_key_manager.QMessageBox", _YesBox
        )
        dlg._on_delete(doc.ssh_keys[0])
        assert doc.ssh_keys == []

    def test_unreferenced_key_deletes_without_prompting(self, qtbot, monkeypatch):
        """No prompt when nothing breaks -- otherwise people learn to dismiss it."""
        doc = _make_doc(_make_key("orphan-none"))
        doc.connections = []
        dlg = SshKeyManagerDialog(doc, _make_key_service())
        qtbot.addWidget(dlg)

        def _boom(*a, **kw):
            raise AssertionError("prompted for an unreferenced key")

        monkeypatch.setattr("cpsm.ui.dialogs.ssh_key_manager.QMessageBox", _boom)
        dlg._on_delete(doc.ssh_keys[0])
        assert doc.ssh_keys == []


class TestSshKeyManagerDelete:
    def test_delete_removes_key(self, qtbot):
        k1 = _make_key("key-del")
        k2 = _make_key("key-keep")
        doc = _make_doc(k1, k2)
        svc = _make_key_service()
        dlg = SshKeyManagerDialog(doc, svc)
        qtbot.addWidget(dlg)

        assert len(doc.ssh_keys) == 2
        dlg._on_delete(k1)
        assert len(doc.ssh_keys) == 1
        assert doc.ssh_keys[0].id == "key-keep"

    def test_delete_refreshes_table(self, qtbot):
        k1 = _make_key("key-one")
        doc = _make_doc(k1)
        svc = _make_key_service()
        dlg = SshKeyManagerDialog(doc, svc)
        qtbot.addWidget(dlg)

        table: QTableWidget = dlg.findChild(QTableWidget, "table_keys")
        assert table.rowCount() == 1
        dlg._on_delete(k1)
        assert table.rowCount() == 0
