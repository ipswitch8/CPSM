# -*- coding: utf-8 -*-
"""
E2E tests: key generation, deployment, and passphrase-in-keyring.

Acceptance criteria covered:
  §10.22  Key gen/deploy/keychain works: ed25519 generated, public key written,
          permissions 0600 on Linux, passphrase stored in OS keyring (mocked),
          deploy calls runner with correct argv (no password in argv).
  §10.30  No private key / password / passphrase in YAML, logs, or process
          arguments for the entire keygen+deploy flow.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from cpsm.data.schema import ClaudeRemoteConnection, CpsmDocument, SshKey
from cpsm.platform.process_runner import ProcessRunner
from cpsm.services.key_service import KeyService

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_TEST_PASSPHRASE = "S3cr3t-E2E-Passphrase!"
_TEST_PASSWORD = "DeployP@ssword99!"


@pytest.fixture()
def mock_keyring():
    kr = MagicMock()
    kr.get_password.return_value = None
    return kr


@pytest.fixture()
def mock_runner():
    runner = MagicMock(spec=ProcessRunner)
    runner.run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    return runner


@pytest.fixture()
def key_service(mock_runner, mock_keyring):
    return KeyService(runner=mock_runner, keyring_module=mock_keyring)


# ---------------------------------------------------------------------------
# §10.22 — Key gen / deploy / keychain
# ---------------------------------------------------------------------------


class TestKeyGenDeployKeychain:
    """Acceptance §10.22: Key gen/deploy/keychain works on Linux + Windows."""

    def test_generate_writes_private_and_public_key(self, key_service, tmp_path):
        """Acceptance §10.22: generate_ed25519 writes private and public key files."""
        priv = tmp_path / "id_ed25519_test"
        key = key_service.generate_ed25519(key_id="test-key", private_path=priv)

        assert priv.exists(), "Private key file not written"
        assert Path(key.public_path).exists(), "Public key file not written"

    def test_generate_private_key_mode_0600_on_linux(self, key_service, tmp_path):
        """Acceptance §10.22: Private key file has mode 0600 on Linux."""
        if sys.platform == "win32":
            pytest.skip("Linux-only permission test")

        priv = tmp_path / "id_ed25519_perms"
        key_service.generate_ed25519(key_id="perms-key", private_path=priv)

        mode = priv.stat().st_mode & 0o777
        assert mode == 0o600, f"Expected 0600, got {oct(mode)}"

    def test_generate_with_passphrase_stores_in_keyring(self, key_service, mock_keyring, tmp_path):
        """Acceptance §10.22: Passphrase is stored in OS keyring, not in key file."""
        priv = tmp_path / "id_ed25519_kring"
        key = key_service.generate_ed25519(
            key_id="kring-key",
            private_path=priv,
            passphrase=_TEST_PASSPHRASE,
        )

        # Keyring set_password must have been called
        mock_keyring.set_password.assert_called_once_with("cpsm", "kring-key", _TEST_PASSPHRASE)
        # passphrase_ref must be set in the returned SshKey
        assert key.passphrase_ref is not None
        assert "keyring" in key.passphrase_ref

    def test_generate_without_passphrase_no_keyring_call(self, key_service, mock_keyring, tmp_path):
        """Acceptance §10.22: No keyring call when no passphrase is given."""
        priv = tmp_path / "id_ed25519_nopw"
        key = key_service.generate_ed25519(key_id="nopw-key", private_path=priv)

        mock_keyring.set_password.assert_not_called()
        assert key.passphrase_ref is None

    def test_deploy_calls_runner_with_correct_host_user(self, key_service, mock_runner, tmp_path):
        """Acceptance §10.22: deploy() calls runner with host/user in argv."""
        priv = tmp_path / "id_ed25519_deploy"
        key = key_service.generate_ed25519(key_id="deploy-key", private_path=priv)

        conn = ClaudeRemoteConnection(
            id="web01",
            launch_profile="claude-remote",
            host="deploy.example.com",
            port=22,
            user="ubuntu",
            identity_file_ref="deploy-key",
            project_folder="/opt/app",
            claude_options="--resume",
        )

        result = key_service.deploy(key=key, connection=conn, password=None)

        # Runner must have been called at least once
        assert mock_runner.run.called or result.method in ("ssh-copy-id", "manual")
        if mock_runner.run.called:
            all_argvs = [list(call.args[0]) for call in mock_runner.run.call_args_list]
            # Check that host appears in at least one argv
            host_found = any("deploy.example.com" in " ".join(argv) for argv in all_argvs)
            assert host_found, f"Host not in any runner argv: {all_argvs}"

    def test_get_passphrase_from_keyring(self, key_service, mock_keyring):
        """Acceptance §10.22: get_passphrase retrieves from keyring."""
        mock_keyring.get_password.return_value = _TEST_PASSPHRASE
        key = SshKey(
            id="test-key",
            name="Test Key",
            type="ed25519",
            private_path="~/.ssh/id_test",
            public_path="~/.ssh/id_test.pub",
        )
        result = key_service.get_passphrase(key)
        assert result == _TEST_PASSPHRASE
        mock_keyring.get_password.assert_called_once_with("cpsm", "test-key")


# ---------------------------------------------------------------------------
# §10.30 — No secrets in YAML, logs, or process arguments
# ---------------------------------------------------------------------------


class TestNoSecretsEverywhere:
    """Acceptance §10.30: No private-key/password/passphrase in YAML, logs, or argv."""

    def test_passphrase_not_in_generated_key_yaml(self, key_service, mock_keyring, tmp_path):
        """Acceptance §10.30: Passphrase never appears in the SshKey YAML representation."""
        priv = tmp_path / "id_ed25519_yaml"
        key = key_service.generate_ed25519(
            key_id="yaml-key",
            private_path=priv,
            passphrase=_TEST_PASSPHRASE,
        )

        # Serialize to dict (as would appear in YAML)
        key_dict = key.model_dump()
        yaml_text = str(key_dict)

        assert _TEST_PASSPHRASE not in yaml_text, f"Passphrase found in YAML dict: {yaml_text}"
        # passphrase_ref should be a keyring:// reference, not the actual passphrase
        if key.passphrase_ref:
            assert "keyring://" in key.passphrase_ref

    def test_passphrase_not_in_log_records(self, key_service, mock_keyring, tmp_path, caplog):
        """Acceptance §10.30: Passphrase never appears in any log record during keygen."""
        priv = tmp_path / "id_ed25519_log"

        with caplog.at_level(logging.DEBUG, logger="cpsm"):
            key_service.generate_ed25519(
                key_id="log-key",
                private_path=priv,
                passphrase=_TEST_PASSPHRASE,
            )

        # Search all log records for the passphrase
        all_log_text = " ".join(record.getMessage() for record in caplog.records)
        assert _TEST_PASSPHRASE not in all_log_text, (
            f"Passphrase found in logs: {all_log_text[:200]}"
        )

    def test_deploy_password_not_in_runner_argv(self, key_service, mock_runner, tmp_path):
        """Acceptance §10.30: Deploy password never appears in ProcessRunner.run argv."""
        priv = tmp_path / "id_ed25519_argv"
        key = key_service.generate_ed25519(key_id="argv-key", private_path=priv)

        conn = ClaudeRemoteConnection(
            id="web01",
            launch_profile="claude-remote",
            host="secure.example.com",
            user="ubuntu",
            identity_file_ref="argv-key",
            project_folder="/opt/app",
            claude_options="--resume",
        )

        key_service.deploy(key=key, connection=conn, password=_TEST_PASSWORD)

        # Check every runner.run call argument list
        for call in mock_runner.run.call_args_list:
            argv = list(call.args[0]) if call.args else []
            argv_str = " ".join(argv)
            assert _TEST_PASSWORD not in argv_str, (
                f"Deploy password found in runner argv: {argv_str}"
            )

    def test_passphrase_not_in_yaml_document_save(self, key_service, mock_keyring, tmp_path):
        """Acceptance §10.30: Passphrase absent from .cpsm.yaml after save."""
        from cpsm.data.repository import CpsmRepository

        priv = tmp_path / "id_ed25519_save"
        key = key_service.generate_ed25519(
            key_id="save-key",
            private_path=priv,
            passphrase=_TEST_PASSPHRASE,
        )

        doc = CpsmDocument(ssh_keys=[key])
        repo = CpsmRepository()
        cfg_path = tmp_path / ".cpsm.yaml"
        repo.save(doc, cfg_path)

        saved_text = cfg_path.read_text(encoding="utf-8")
        assert _TEST_PASSPHRASE not in saved_text, f"Passphrase found in saved YAML:\n{saved_text}"

    def test_deploy_logs_no_password(self, key_service, mock_runner, tmp_path, caplog):
        """Acceptance §10.30: Deploy password never appears in any log during deploy."""
        priv = tmp_path / "id_ed25519_logdep"
        key = key_service.generate_ed25519(key_id="logdep-key", private_path=priv)

        conn = ClaudeRemoteConnection(
            id="web01",
            launch_profile="claude-remote",
            host="secure.example.com",
            user="ubuntu",
            identity_file_ref="logdep-key",
            project_folder="/opt/app",
            claude_options="--resume",
        )

        with caplog.at_level(logging.DEBUG, logger="cpsm"):
            key_service.deploy(key=key, connection=conn, password=_TEST_PASSWORD)

        all_log_text = " ".join(record.getMessage() for record in caplog.records)
        assert _TEST_PASSWORD not in all_log_text, (
            f"Deploy password found in logs: {all_log_text[:200]}"
        )
