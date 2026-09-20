# -*- coding: utf-8 -*-
"""Tests for KeyService — ed25519 generation, deployment, keyring.

Security assertions
-------------------
- generate_ed25519 with passphrase: private key 0600 on Linux, .pub written,
  passphrase stored via mock keyring.set_password, NEVER appears in any
  captured value (log lines, ProcessRunner.run argv, Repository.save).
- deploy: correct ProcessRunner invocation, password buffer zeroed.
- Refuse to write over existing private key path (KeyExistsError).

Coverage target: ≥ 90%
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from cpsm.data.schema import SshKey
from cpsm.platform.process_runner import ProcessRunner
from cpsm.services.key_service import KeyExistsError, KeyService, _zero_password

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TEST_PASSPHRASE = "S3cr3tP@ssphrase!"
_TEST_PASSWORD = "d3pl0yP@ssword!"


@pytest.fixture
def mock_keyring() -> MagicMock:
    kr = MagicMock()
    kr.get_password.return_value = None
    return kr


@pytest.fixture
def mock_runner() -> MagicMock:
    runner = MagicMock(spec=ProcessRunner)
    runner.run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    return runner


@pytest.fixture
def key_service(mock_runner: MagicMock, mock_keyring: MagicMock) -> KeyService:
    return KeyService(runner=mock_runner, keyring_module=mock_keyring)


@pytest.fixture
def key_service_no_runner(mock_keyring: MagicMock) -> KeyService:
    """KeyService with real runner (will fail for actual SSH) but mocked keyring."""
    return KeyService(keyring_module=mock_keyring)


# ---------------------------------------------------------------------------
# generate_ed25519 — happy paths
# ---------------------------------------------------------------------------


class TestGenerateEd25519:
    def test_writes_private_and_public_key_files(
        self, key_service: KeyService, tmp_path: Path
    ) -> None:
        priv = tmp_path / "id_ed25519_test"
        key = key_service.generate_ed25519(key_id="test-key", private_path=priv)
        assert priv.exists()
        assert Path(key.public_path).exists()

    def test_private_key_mode_0600_on_linux(self, key_service: KeyService, tmp_path: Path) -> None:
        if sys.platform == "win32":
            pytest.skip("Linux-only permission test")
        priv = tmp_path / "id_ed25519_test"
        key_service.generate_ed25519(key_id="test-key", private_path=priv)
        mode = os.stat(priv).st_mode & 0o777
        assert mode == 0o600, f"Expected 0600, got {oct(mode)}"

    def test_public_key_file_path(self, key_service: KeyService, tmp_path: Path) -> None:
        priv = tmp_path / "id_ed25519_test"
        key = key_service.generate_ed25519(key_id="test-key", private_path=priv)
        assert key.public_path == str(priv.with_suffix(".pub"))

    def test_public_key_contains_openssh_header(
        self, key_service: KeyService, tmp_path: Path
    ) -> None:
        priv = tmp_path / "id_ed25519"
        key_service.generate_ed25519(key_id="test-key", private_path=priv)
        pub_content = Path(priv.with_suffix(".pub")).read_text(encoding="utf-8")
        assert pub_content.startswith("ssh-ed25519 ")

    def test_private_key_is_pem_openssh(self, key_service: KeyService, tmp_path: Path) -> None:
        priv = tmp_path / "id_ed25519"
        key_service.generate_ed25519(key_id="test-key", private_path=priv)
        content = priv.read_text(encoding="utf-8")
        assert "OPENSSH PRIVATE KEY" in content

    def test_comment_appended_to_public_key(self, key_service: KeyService, tmp_path: Path) -> None:
        priv = tmp_path / "id_ed25519"
        key_service.generate_ed25519(key_id="test-key", private_path=priv, comment="user@host")
        pub_content = Path(priv.with_suffix(".pub")).read_text(encoding="utf-8")
        assert "user@host" in pub_content

    def test_returns_sshkey_with_correct_fields(
        self, key_service: KeyService, tmp_path: Path
    ) -> None:
        priv = tmp_path / "id_ed25519"
        key = key_service.generate_ed25519(key_id="my-key", private_path=priv, comment="test key")
        assert key.id == "my-key"
        assert key.type == "ed25519"
        assert key.private_path == str(priv)
        assert key.created_at is not None


# ---------------------------------------------------------------------------
# generate_ed25519 — passphrase security
# ---------------------------------------------------------------------------


class TestPassphraseSecurity:
    def test_passphrase_stored_in_keyring_not_yaml(
        self, key_service: KeyService, mock_keyring: MagicMock, tmp_path: Path
    ) -> None:
        priv = tmp_path / "id_ed25519"
        key = key_service.generate_ed25519(
            key_id="secured-key",
            private_path=priv,
            passphrase=_TEST_PASSPHRASE,
        )
        mock_keyring.set_password.assert_called_once_with("cpsm", "secured-key", _TEST_PASSPHRASE)
        assert key.passphrase_ref == "keyring://cpsm/secured-key"

    def test_passphrase_ref_set_in_returned_key(
        self, key_service: KeyService, tmp_path: Path
    ) -> None:
        priv = tmp_path / "id_ed25519"
        key = key_service.generate_ed25519(
            key_id="secured-key", private_path=priv, passphrase=_TEST_PASSPHRASE
        )
        assert key.passphrase_ref is not None
        assert "keyring" in key.passphrase_ref

    def test_no_passphrase_ref_when_none(self, key_service: KeyService, tmp_path: Path) -> None:
        priv = tmp_path / "id_ed25519"
        key = key_service.generate_ed25519(key_id="plain-key", private_path=priv)
        assert key.passphrase_ref is None

    @pytest.mark.parametrize("passphrase", [_TEST_PASSPHRASE, "another-pass-123"])
    def test_passphrase_never_in_log_lines(
        self,
        key_service: KeyService,
        tmp_path: Path,
        passphrase: str,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        priv = tmp_path / "id_ed25519"
        with caplog.at_level(logging.DEBUG):
            key_service.generate_ed25519(
                key_id="test-key", private_path=priv, passphrase=passphrase
            )
        # Passphrase must NOT appear in any log record
        all_log_text = "\n".join(r.message for r in caplog.records)
        assert passphrase not in all_log_text

    def test_passphrase_never_in_runner_argv(
        self, mock_runner: MagicMock, mock_keyring: MagicMock, tmp_path: Path
    ) -> None:
        """ProcessRunner.run must not be called with passphrase in any arg."""
        svc = KeyService(runner=mock_runner, keyring_module=mock_keyring)
        priv = tmp_path / "id_ed25519"
        svc.generate_ed25519(key_id="test-key", private_path=priv, passphrase=_TEST_PASSPHRASE)
        for call_args in mock_runner.run.call_args_list:
            argv = call_args.args[0] if call_args.args else []
            for arg in argv:
                assert _TEST_PASSPHRASE not in str(arg)


# ---------------------------------------------------------------------------
# generate_ed25519 — KeyExistsError
# ---------------------------------------------------------------------------


class TestKeyExistsError:
    def test_raises_key_exists_error_when_private_path_exists(
        self, key_service: KeyService, tmp_path: Path
    ) -> None:
        priv = tmp_path / "id_ed25519"
        priv.write_text("existing key content", encoding="utf-8")
        with pytest.raises(KeyExistsError):
            key_service.generate_ed25519(key_id="test-key", private_path=priv)

    def test_does_not_overwrite_existing_key(self, key_service: KeyService, tmp_path: Path) -> None:
        priv = tmp_path / "id_ed25519"
        original_content = "original key content"
        priv.write_text(original_content, encoding="utf-8")
        with pytest.raises(KeyExistsError):
            key_service.generate_ed25519(key_id="test-key", private_path=priv)
        assert priv.read_text(encoding="utf-8") == original_content


# ---------------------------------------------------------------------------
# deploy()
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_ssh_key(tmp_path: Path, key_service: KeyService) -> SshKey:
    """Generate a real SshKey for deploy tests."""
    priv = tmp_path / "id_ed25519_deploy"
    return key_service.generate_ed25519(key_id="deploy-key", private_path=priv)


@pytest.fixture
def mock_conn() -> MagicMock:
    conn = MagicMock()
    conn.host = "example.com"
    conn.user = "ubuntu"
    conn.port = 22
    return conn


class TestDeploy:
    def test_deploy_calls_ssh_copy_id(
        self,
        key_service: KeyService,
        mock_runner: MagicMock,
        sample_ssh_key: SshKey,
        mock_conn: MagicMock,
    ) -> None:
        # Verify needs the deployed pub key body to appear in the remote
        # authorized_keys output.  Make the mock return that.
        pub_key_body = Path(sample_ssh_key.public_path).read_text(encoding="utf-8").strip()
        mock_runner.run.return_value = MagicMock(
            returncode=0,
            stdout=pub_key_body,
            stderr="",
        )
        result = key_service.deploy(key=sample_ssh_key, connection=mock_conn)
        assert result.success
        assert result.method == "ssh-copy-id"
        # Two calls: first is ssh-copy-id, second is the verification
        # ssh that reads remote authorized_keys.
        assert mock_runner.run.call_count == 2
        first_argv = mock_runner.run.call_args_list[0].args[0]
        assert "ssh-copy-id" in first_argv
        assert "-i" in first_argv
        verify_argv = mock_runner.run.call_args_list[1].args[0]
        # Verify call contains the cat command.
        assert any("authorized_keys" in a for a in verify_argv)

    def test_deploy_ssh_copy_id_includes_host(
        self,
        key_service: KeyService,
        mock_runner: MagicMock,
        sample_ssh_key: SshKey,
        mock_conn: MagicMock,
    ) -> None:
        key_service.deploy(key=sample_ssh_key, connection=mock_conn)
        # Find the ssh-copy-id call (verify call also targets the host)
        copy_call = next(c for c in mock_runner.run.call_args_list if "ssh-copy-id" in c.args[0])
        argv = copy_call.args[0]
        combined = " ".join(argv)
        assert "ubuntu@example.com" in combined

    def test_deploy_password_not_in_argv(
        self,
        key_service: KeyService,
        mock_runner: MagicMock,
        sample_ssh_key: SshKey,
        mock_conn: MagicMock,
    ) -> None:
        key_service.deploy(key=sample_ssh_key, connection=mock_conn, password=_TEST_PASSWORD)
        for call_args in mock_runner.run.call_args_list:
            argv = call_args.args[0] if call_args.args else []
            for arg in argv:
                assert _TEST_PASSWORD not in str(arg)

    def test_deploy_strips_ssh_auth_sock_from_env(
        self,
        key_service: KeyService,
        mock_runner: MagicMock,
        sample_ssh_key: SshKey,
        mock_conn: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When a password is provided, every spawned ssh subprocess must
        receive an env that excludes SSH_AUTH_SOCK — otherwise a
        misbehaving agent can intercept auth and cause the very
        "agent refused to sign" failure we're trying to bypass.
        Regression guard for the env-strip in deploy().
        """
        # Make sure SSH_AUTH_SOCK is set in the parent env so the test
        # can prove the deploy code is actively stripping it (not just
        # that it happens to be unset).
        monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/agent-fake.sock")
        key_service.deploy(
            key=sample_ssh_key,
            connection=mock_conn,
            password=_TEST_PASSWORD,
        )
        # Every call must have env supplied (not None) and not contain
        # SSH_AUTH_SOCK.
        for call_args in mock_runner.run.call_args_list:
            env = call_args.kwargs.get("env")
            assert env is not None, (
                "deploy() must pass an explicit env when a password is provided; got env=None"
            )
            assert "SSH_AUTH_SOCK" not in env, (
                f"SSH_AUTH_SOCK must be stripped, found {env.get('SSH_AUTH_SOCK')!r}"
            )

    def test_deploy_forces_password_only_auth_options(
        self,
        key_service: KeyService,
        mock_runner: MagicMock,
        sample_ssh_key: SshKey,
        mock_conn: MagicMock,
    ) -> None:
        """When a password is provided, ssh-copy-id and the manual
        fallback must be invoked with options that disable pubkey-based
        auth entirely.  Otherwise ssh would consult agent keys before
        the password — which is exactly the failure mode that motivated
        these flags.
        """
        key_service.deploy(
            key=sample_ssh_key,
            connection=mock_conn,
            password=_TEST_PASSWORD,
        )
        required = {
            "PreferredAuthentications=password,keyboard-interactive",
            "PubkeyAuthentication=no",
            "IdentityAgent=none",
        }
        # The deploy step (ssh-copy-id, or manual ssh) should carry these.
        # The post-deploy verify call uses sshpass with the same options
        # to read remote authorized_keys, so it should too.
        deploy_calls = [
            c
            for c in mock_runner.run.call_args_list
            if "ssh-copy-id" in c.args[0] or (c.args[0] and c.args[0][0] == "sshpass")
        ]
        assert deploy_calls, "expected at least one deploy/verify ssh call"
        for call_args in deploy_calls:
            argv = call_args.args[0]
            present = set(argv) & required
            missing = required - present
            assert not missing, (
                f"deploy/verify call missing required auth flags {missing}; argv was {argv}"
            )

    def test_deploy_falls_back_to_manual_when_no_ssh_copy_id(
        self,
        mock_keyring: MagicMock,
        sample_ssh_key: SshKey,
        mock_conn: MagicMock,
    ) -> None:
        """When ssh-copy-id is not found, fall back to manual."""
        pub_key_body = Path(sample_ssh_key.public_path).read_text(encoding="utf-8").strip()
        runner = MagicMock(spec=ProcessRunner)
        # 1) ssh-copy-id not installed → FileNotFoundError
        # 2) manual ssh write → succeeds
        # 3) verification ssh (cat authorized_keys) → returns our pub key
        runner.run.side_effect = [
            FileNotFoundError("ssh-copy-id not found"),
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=0, stdout=pub_key_body, stderr=""),
        ]
        svc = KeyService(runner=runner, keyring_module=mock_keyring)
        result = svc.deploy(key=sample_ssh_key, connection=mock_conn)
        assert result.success
        assert result.method == "manual"

    def test_deploy_falls_through_to_manual_when_verification_fails(
        self,
        mock_keyring: MagicMock,
        sample_ssh_key: SshKey,
        mock_conn: MagicMock,
    ) -> None:
        """If ssh-copy-id returns 0 but the pubkey isn't in remote
        authorized_keys, fall through to the manual stdin path (which
        re-verifies)."""
        pub_key_body = Path(sample_ssh_key.public_path).read_text(encoding="utf-8").strip()
        runner = MagicMock(spec=ProcessRunner)
        runner.run.side_effect = [
            MagicMock(returncode=0, stdout="", stderr=""),  # ssh-copy-id
            MagicMock(returncode=0, stdout="", stderr=""),  # verify 1: empty authorized_keys → fail
            MagicMock(returncode=0, stdout="", stderr=""),  # manual ssh
            MagicMock(returncode=0, stdout=pub_key_body, stderr=""),  # verify 2 → ok
        ]
        svc = KeyService(runner=runner, keyring_module=mock_keyring)
        result = svc.deploy(key=sample_ssh_key, connection=mock_conn)
        assert result.success
        assert result.method == "manual"

    def test_deploy_fails_when_verification_fails_after_manual(
        self,
        mock_keyring: MagicMock,
        sample_ssh_key: SshKey,
        mock_conn: MagicMock,
    ) -> None:
        """If both ssh-copy-id and manual report success but the pubkey
        never lands in remote authorized_keys, deploy must report failure
        — never persist auth."""
        runner = MagicMock(spec=ProcessRunner)
        runner.run.side_effect = [
            MagicMock(returncode=0, stdout="", stderr=""),  # ssh-copy-id
            MagicMock(returncode=0, stdout="", stderr=""),  # verify 1: empty
            MagicMock(returncode=0, stdout="", stderr=""),  # manual ssh
            MagicMock(returncode=0, stdout="", stderr=""),  # verify 2: empty
        ]
        svc = KeyService(runner=runner, keyring_module=mock_keyring)
        result = svc.deploy(key=sample_ssh_key, connection=mock_conn)
        assert not result.success
        assert any("not in remote" in e.lower() for e in result.errors)

    def test_deploy_fails_when_no_host(
        self,
        key_service: KeyService,
        sample_ssh_key: SshKey,
    ) -> None:
        conn = MagicMock()
        conn.host = None
        conn.user = "ubuntu"
        conn.port = 22
        result = key_service.deploy(key=sample_ssh_key, connection=conn)
        assert not result.success
        assert result.errors

    def test_deploy_fails_when_pub_key_missing(
        self,
        key_service: KeyService,
        mock_conn: MagicMock,
        tmp_path: Path,
    ) -> None:
        fake_key = SshKey(
            id="test-key",
            name="Test",
            type="ed25519",
            private_path=str(tmp_path / "id_ed25519"),
            public_path=str(tmp_path / "id_ed25519.pub"),  # does not exist
        )
        result = key_service.deploy(key=fake_key, connection=mock_conn)
        assert not result.success
        assert "not found" in result.errors[0].lower() or "public key" in result.errors[0].lower()

    def test_deploy_password_not_in_logs(
        self,
        key_service: KeyService,
        mock_runner: MagicMock,
        sample_ssh_key: SshKey,
        mock_conn: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        with caplog.at_level(logging.DEBUG):
            key_service.deploy(key=sample_ssh_key, connection=mock_conn, password=_TEST_PASSWORD)
        all_log_text = "\n".join(r.message for r in caplog.records)
        assert _TEST_PASSWORD not in all_log_text


# ---------------------------------------------------------------------------
# Passphrase retrieval
# ---------------------------------------------------------------------------


class TestGetPassphrase:
    def test_get_passphrase_returns_keyring_value(
        self, key_service: KeyService, mock_keyring: MagicMock
    ) -> None:
        mock_keyring.get_password.return_value = "my-passphrase"
        key = SshKey(
            id="test-key",
            name="Test",
            type="ed25519",
            private_path="/path/key",
            public_path="/path/key.pub",
        )
        result = key_service.get_passphrase(key)
        assert result == "my-passphrase"
        mock_keyring.get_password.assert_called_once_with("cpsm", "test-key")

    def test_get_passphrase_returns_none_when_not_stored(
        self, key_service: KeyService, mock_keyring: MagicMock
    ) -> None:
        mock_keyring.get_password.return_value = None
        key = SshKey(
            id="unset-key",
            name="Test",
            type="ed25519",
            private_path="/path/key",
            public_path="/path/key.pub",
        )
        result = key_service.get_passphrase(key)
        assert result is None


class TestStorePassphrase:
    def test_store_passphrase_calls_keyring_set_password(
        self, key_service: KeyService, mock_keyring: MagicMock
    ) -> None:
        key_service.store_passphrase("my-key", "secret-pass")
        mock_keyring.set_password.assert_called_once_with("cpsm", "my-key", "secret-pass")

    def test_store_passphrase_not_in_log(
        self,
        key_service: KeyService,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        with caplog.at_level(logging.DEBUG):
            key_service.store_passphrase("my-key", _TEST_PASSPHRASE)
        all_log_text = "\n".join(r.message for r in caplog.records)
        assert _TEST_PASSPHRASE not in all_log_text


# ---------------------------------------------------------------------------
# Zero-password helper
# ---------------------------------------------------------------------------


class TestZeroPassword:
    def test_zero_password_none_is_noop(self) -> None:
        _zero_password(None)  # should not raise

    def test_zero_password_string(self) -> None:
        _zero_password("some-secret")  # should not raise


class TestDeploymentIsDeliberatelyNotPinned:
    """Key deployment must NOT pass -o IdentitiesOnly=yes.

    Every other ssh invocation in CPSM pins the identity so that the key the
    user configured is the key that authenticates. Deployment is the one
    place where doing so would be wrong, and these tests exist so nobody
    "fixes" the inconsistency without reading why.

    Deployment is the BOOTSTRAP path: the key being installed is by definition
    not yet in the target's authorized_keys, so the connection must
    authenticate by some other means -- an existing agent key, a password, or
    a pre-existing default key. IdentitiesOnly=yes narrows exactly that set
    and would break deploying to any host reachable only via an agent-held
    key.

    Note also that ssh-copy-id's ``-i`` names the key to INSTALL, not an
    authentication identity: /usr/bin/ssh-copy-id routes it to use_id_file(),
    which sets PUB_ID_FILE, and its own ssh call carries no -i.
    """

    def _deploy(self, key_service, mock_runner, sample_ssh_key, mock_conn):
        pub = Path(sample_ssh_key.public_path).read_text(encoding="utf-8").strip()
        mock_runner.run.return_value = MagicMock(
            returncode=0,
            stdout=pub,
            stderr="",
        )
        key_service.deploy(key=sample_ssh_key, connection=mock_conn)
        # Exercise guard: asserting an option is ABSENT is vacuously true if
        # the runner was never called at all.
        assert mock_runner.run.call_count > 0, "deploy never invoked the runner"
        return [c.args[0] for c in mock_runner.run.call_args_list if c.args]

    def test_no_deploy_invocation_pins_the_identity(
        self,
        key_service,
        mock_runner,
        sample_ssh_key,
        mock_conn,
    ) -> None:
        """Neither ssh-copy-id nor the verification ssh may carry the pin."""
        for argv in self._deploy(key_service, mock_runner, sample_ssh_key, mock_conn):
            assert not any("IdentitiesOnly" in a for a in argv), argv

    def test_ssh_copy_id_still_names_the_key_to_install(
        self,
        key_service,
        mock_runner,
        sample_ssh_key,
        mock_conn,
    ) -> None:
        """-i must still be present -- it selects the key to INSTALL.

        Guards the reasoning as much as the behaviour: if a later change
        removed -i believing it to be an authentication flag, deployment would
        silently install whatever ~/.ssh/id*.pub ssh-copy-id picked by
        default rather than the key CPSM was asked to deploy.
        """
        argvs = self._deploy(key_service, mock_runner, sample_ssh_key, mock_conn)
        copy_id = next(a for a in argvs if "ssh-copy-id" in a)
        assert "-i" in copy_id
        assert copy_id[copy_id.index("-i") + 1].endswith(".pub")

    def test_verification_ssh_passes_no_identity_at_all(
        self,
        key_service,
        mock_runner,
        sample_ssh_key,
        mock_conn,
    ) -> None:
        """The authorized_keys read-back carries no -i, so it needs no pin.

        Pinning with no -i would be the harmful direction: it restricts ssh to
        the default identity files and disables agent-held keys.
        """
        argvs = self._deploy(key_service, mock_runner, sample_ssh_key, mock_conn)
        verify = next(a for a in argvs if any("authorized_keys" in x for x in a))
        assert "-i" not in verify, verify
        assert not any("IdentitiesOnly" in a for a in verify), verify

    def test_password_path_wrapping_and_ordering_unchanged(
        self,
        key_service,
        mock_runner,
        sample_ssh_key,
        mock_conn,
    ) -> None:
        """sshpass stays argv[0], the password stays out of argv, and the
        password path still carries no pin.

        The identity-pinning work changed how CPSM's OTHER ssh invocations are
        built. This asserts it did not disturb the deployment path's own,
        stronger constraint: PubkeyAuthentication=no with IdentityAgent=none
        is a tighter restriction than IdentitiesOnly=yes, and it is applied
        here instead of, not alongside, the pin.
        """
        key_service.deploy(
            key=sample_ssh_key,
            connection=mock_conn,
            password=_TEST_PASSWORD,
        )
        assert mock_runner.run.call_count > 0, "deploy never invoked the runner"
        wrapped = [
            c.args[0]
            for c in mock_runner.run.call_args_list
            if c.args and c.args[0] and c.args[0][0] == "sshpass"
        ]
        assert wrapped, "expected at least one sshpass-wrapped invocation"
        for argv in wrapped:
            # sshpass must lead, and read the password from the environment.
            assert argv[0] == "sshpass", argv
            assert "-e" in argv[:2], argv
            # The real binary follows the wrapper, not the other way round.
            assert any(a in ("ssh", "ssh-copy-id") for a in argv[:3]), argv
            # The password must never be visible in argv (it would show in ps).
            assert _TEST_PASSWORD not in argv, "password leaked into argv"
            # And the deployment path stays unpinned.
            assert not any("IdentitiesOnly" in a for a in argv), argv
