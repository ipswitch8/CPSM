# -*- coding: utf-8 -*-
"""
Tests for cpsm.workers.ssh_worker.

Covers SshTestConnectionTask via mocked ProcessRunner.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from cpsm.platform.process_runner import ProcessRunner
from cpsm.platform.ssh_binary import SshBinary
from cpsm.services.key_discovery import KeyCandidate
from cpsm.workers.ssh_worker import (
    KeyDiscoveryProbeSignals,
    KeyDiscoveryProbeTask,
    SshTestConnectionTask,
    SshTestSignals,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ssh_binary(flavor: str = "openssh") -> SshBinary:
    return SshBinary(binary="/usr/bin/ssh", flavor=flavor)  # type: ignore[arg-type]


def _make_runner(returncode: int = 0, stdout: str = "", stderr: str = "") -> ProcessRunner:
    """Return a mock ProcessRunner that returns a CompletedProcess."""
    runner = MagicMock(spec=ProcessRunner)
    runner.run.return_value = subprocess.CompletedProcess(
        args=[],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )
    return runner


def _make_task(
    runner: ProcessRunner,
    host: str = "myhost.example.com",
    user: str = "admin",
    port: int = 22,
    identity_file: Path | None = None,
) -> SshTestConnectionTask:
    return SshTestConnectionTask(
        _make_ssh_binary(),
        host,
        user,
        port=port,
        identity_file=identity_file,
        runner=runner,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSshTestConnectionTaskUnit:
    def test_has_signals_attribute(self) -> None:
        runner = _make_runner()
        task = _make_task(runner)
        assert isinstance(task.signals, SshTestSignals)

    def test_auto_delete_enabled(self) -> None:
        runner = _make_runner()
        task = _make_task(runner)
        assert task.autoDelete() is True

    def test_success_path(self) -> None:
        """returncode=0 → finished(True, '')."""
        runner = _make_runner(returncode=0)
        task = _make_task(runner)

        results: list[tuple[bool, str]] = []
        task.signals.finished.connect(lambda ok, err: results.append((ok, err)))
        task.run()

        assert results == [(True, "")]

    def test_failure_with_stderr(self) -> None:
        """Non-zero returncode → finished(False, stderr)."""
        runner = _make_runner(returncode=255, stderr="Connection refused")
        task = _make_task(runner)

        results: list[tuple[bool, str]] = []
        task.signals.finished.connect(lambda ok, err: results.append((ok, err)))
        task.run()

        assert results[0][0] is False
        assert "Connection refused" in results[0][1]

    def test_failure_uses_stdout_when_stderr_empty(self) -> None:
        """When stderr is empty, stdout provides the error message."""
        runner = _make_runner(returncode=1, stdout="Permission denied", stderr="")
        task = _make_task(runner)

        results: list[tuple[bool, str]] = []
        task.signals.finished.connect(lambda ok, err: results.append((ok, err)))
        task.run()

        assert results[0][0] is False
        assert "Permission denied" in results[0][1]

    def test_failure_fallback_exit_code_message(self) -> None:
        """When both stderr and stdout are empty, a generic exit-code message is used."""
        runner = _make_runner(returncode=127, stdout="", stderr="")
        task = _make_task(runner)

        results: list[tuple[bool, str]] = []
        task.signals.finished.connect(lambda ok, err: results.append((ok, err)))
        task.run()

        assert results[0][0] is False
        assert "127" in results[0][1]

    def test_timeout_expired(self) -> None:
        """subprocess.TimeoutExpired → finished(False, 'timed out')."""
        runner = MagicMock(spec=ProcessRunner)
        runner.run.side_effect = subprocess.TimeoutExpired(cmd=["ssh"], timeout=15)
        task = _make_task(runner)

        results: list[tuple[bool, str]] = []
        task.signals.finished.connect(lambda ok, err: results.append((ok, err)))
        task.run()

        assert results[0][0] is False
        assert "timed out" in results[0][1].lower()

    def test_oserror_on_launch(self) -> None:
        """OSError (binary not found) → finished(False, error_message)."""
        runner = MagicMock(spec=ProcessRunner)
        runner.run.side_effect = OSError("No such file or directory")
        task = _make_task(runner)

        results: list[tuple[bool, str]] = []
        task.signals.finished.connect(lambda ok, err: results.append((ok, err)))
        task.run()

        assert results[0][0] is False
        assert "No such file" in results[0][1]

    def test_generic_exception(self) -> None:
        """Any other exception → finished(False, str(exc))."""
        runner = MagicMock(spec=ProcessRunner)
        runner.run.side_effect = ValueError("unexpected")
        task = _make_task(runner)

        results: list[tuple[bool, str]] = []
        task.signals.finished.connect(lambda ok, err: results.append((ok, err)))
        task.run()

        assert results[0][0] is False
        assert "unexpected" in results[0][1]

    def test_argv_includes_batch_mode(self) -> None:
        """The argv passed to runner.run must contain BatchMode=yes."""
        runner = _make_runner(returncode=0)
        task = _make_task(runner)
        task.run()

        call_argv = runner.run.call_args[0][0]
        argv_str = " ".join(call_argv)
        assert "BatchMode=yes" in argv_str

    def test_argv_includes_connect_timeout(self) -> None:
        """The argv must contain ConnectTimeout=5."""
        runner = _make_runner(returncode=0)
        task = _make_task(runner)
        task.run()

        call_argv = runner.run.call_args[0][0]
        argv_str = " ".join(call_argv)
        assert "ConnectTimeout=5" in argv_str

    def test_non_default_port_included(self) -> None:
        """Port != 22 must appear in the argv."""
        runner = _make_runner(returncode=0)
        task = _make_task(runner, port=2222)
        task.run()

        call_argv = runner.run.call_args[0][0]
        assert "2222" in call_argv

    def test_identity_file_included(self) -> None:
        """Identity file path must appear in the argv when provided."""
        runner = _make_runner(returncode=0)
        task = _make_task(runner, identity_file=Path("/home/user/.ssh/id_ed25519"))
        task.run()

        call_argv = runner.run.call_args[0][0]
        assert "/home/user/.ssh/id_ed25519" in call_argv

    def test_identity_file_is_pinned_at_this_call_site(self) -> None:
        """The produced argv must pin the identity, not merely name it.

        Asserted at the CALL SITE rather than only on SshBinary.build_argv,
        because the builder's own unit tests cannot see a caller that defeats
        the pin — by putting IdentitiesOnly=no in its own ssh_options, or by
        bypassing SshBinary entirely. That is precisely the defect this
        feature exists to prevent, so it needs a test that watches the argv
        this worker actually hands to the runner.
        """
        runner = _make_runner(returncode=0)
        task = _make_task(runner, identity_file=Path("/home/user/.ssh/id_ed25519"))
        task.run()

        call_argv = runner.run.call_args[0][0]
        assert "-i" in call_argv, call_argv
        assert "IdentitiesOnly=yes" in call_argv, call_argv
        assert "IdentitiesOnly=no" not in call_argv, call_argv

    def test_no_identity_file_is_not_pinned_at_this_call_site(self) -> None:
        """With no identity, the pin must be absent.

        IdentitiesOnly=yes without -i restricts ssh to the default identity
        files and disables agent-held keys, so a connection with no configured
        key must not receive it.
        """
        runner = _make_runner(returncode=0)
        task = _make_task(runner)
        task.run()

        call_argv = runner.run.call_args[0][0]
        assert "-i" not in call_argv, call_argv
        assert not any("IdentitiesOnly" in a for a in call_argv), call_argv

    def test_default_port_omitted(self) -> None:
        """Port 22 (the default) should not add an extra flag."""
        runner = _make_runner(returncode=0)
        task = _make_task(runner, port=22)
        task.run()

        call_argv = runner.run.call_args[0][0]
        # "-p" should not be in the argv for default port
        assert "-p" not in call_argv

    def test_no_force_tty(self) -> None:
        """Test connection must NOT request TTY (no -tt or -t in argv)."""
        runner = _make_runner(returncode=0)
        task = _make_task(runner)
        task.run()

        call_argv = runner.run.call_args[0][0]
        assert "-tt" not in call_argv
        assert "-t" not in call_argv


@pytest.mark.ui
class TestSshTestConnectionTaskIntegration:
    def test_signal_emitted_via_thread_pool(self, qtbot: object) -> None:
        """Task runs correctly when submitted to QThreadPool."""
        from PySide6.QtCore import QThreadPool

        runner = _make_runner(returncode=0)
        task = _make_task(runner)

        results: list[tuple[bool, str]] = []
        task.signals.finished.connect(lambda ok, err: results.append((ok, err)))

        QThreadPool.globalInstance().start(task)
        qtbot.waitUntil(lambda: len(results) >= 1, timeout=2000)  # type: ignore[attr-defined]

        assert results == [(True, "")]

    def test_failure_signal_via_thread_pool(self, qtbot: object) -> None:
        """Non-zero exit emits finished(False, ...) from thread pool."""
        from PySide6.QtCore import QThreadPool

        runner = _make_runner(returncode=255, stderr="Host unreachable")
        # Note: for thread-pool tasks autoDelete=True means we can't reuse
        # the task object; create a new one.
        task = SshTestConnectionTask(
            _make_ssh_binary(),
            "myhost.example.com",
            "admin",
            runner=runner,
        )

        results: list[tuple[bool, str]] = []
        task.signals.finished.connect(lambda ok, err: results.append((ok, err)))

        QThreadPool.globalInstance().start(task)
        qtbot.waitUntil(lambda: len(results) >= 1, timeout=2000)  # type: ignore[attr-defined]

        assert results[0][0] is False
        assert "Host unreachable" in results[0][1]


# ---------------------------------------------------------------------------
# KeyDiscoveryProbeTask (cpsm-connection-key-ux phase-3)
#
# All tests here inject a mock ProcessRunner (spec=ProcessRunner) — none
# launch a real ssh subprocess or reach the network.
# ---------------------------------------------------------------------------


def _make_candidate(name: str, *, score: int = 60) -> KeyCandidate:
    priv = Path("/home/user/.ssh") / name
    return KeyCandidate(
        private_path=priv,
        public_path=priv.with_name(priv.name + ".pub"),
        comment="",
        source="pub_comment_host",
        score=score,
    )


def _make_probe_task(
    runner: ProcessRunner,
    candidates: list[KeyCandidate],
    host: str = "myhost.example.com",
    user: str = "admin",
    port: int = 22,
) -> KeyDiscoveryProbeTask:
    return KeyDiscoveryProbeTask(
        ssh_binary=_make_ssh_binary(),
        host=host,
        user=user,
        port=port,
        candidates=candidates,
        runner=runner,
    )


class TestKeyDiscoveryProbeTaskUnit:
    def test_has_signals_attribute(self) -> None:
        runner = _make_runner()
        task = _make_probe_task(runner, [_make_candidate("k1")])
        assert isinstance(task.signals, KeyDiscoveryProbeSignals)

    def test_auto_delete_enabled(self) -> None:
        runner = _make_runner()
        task = _make_probe_task(runner, [_make_candidate("k1")])
        assert task.autoDelete() is True

    def test_argv_pins_identity_and_batch_mode_for_first_candidate(self) -> None:
        """The probe argv is the entire point of this feature — assert on it
        directly, not on a proxy."""
        candidate = _make_candidate("id_ed25519_utility")
        runner = _make_runner(returncode=0)
        task = _make_probe_task(runner, [candidate])
        task.run()

        call_argv = runner.run.call_args[0][0]
        assert "-i" in call_argv, call_argv
        i_index = call_argv.index("-i")
        assert call_argv[i_index + 1] == str(candidate.private_path)
        assert "IdentitiesOnly=yes" in call_argv, call_argv
        assert "BatchMode=yes" in call_argv, call_argv

    def test_first_authenticating_candidate_wins_and_later_ones_not_probed(self) -> None:
        c1 = _make_candidate("first", score=100)
        c2 = _make_candidate("second", score=60)
        runner = MagicMock(spec=ProcessRunner)
        runner.run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        task = _make_probe_task(runner, [c1, c2])

        results: list[KeyCandidate | None] = []
        task.signals.finished.connect(results.append)
        task.run()

        assert results == [c1]
        assert runner.run.call_count == 1

    def test_first_candidate_fails_second_succeeds(self) -> None:
        c1 = _make_candidate("first")
        c2 = _make_candidate("second")
        runner = MagicMock(spec=ProcessRunner)
        runner.run.side_effect = [
            subprocess.CompletedProcess(args=[], returncode=255, stdout="", stderr="denied"),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
        ]
        task = _make_probe_task(runner, [c1, c2])

        results: list[KeyCandidate | None] = []
        task.signals.finished.connect(results.append)
        task.run()

        assert results == [c2]
        assert runner.run.call_count == 2

    def test_no_candidate_authenticates_emits_none(self) -> None:
        c1 = _make_candidate("first")
        c2 = _make_candidate("second")
        runner = MagicMock(spec=ProcessRunner)
        runner.run.return_value = subprocess.CompletedProcess(
            args=[], returncode=255, stdout="", stderr="denied"
        )
        task = _make_probe_task(runner, [c1, c2])

        results: list[KeyCandidate | None] = []
        task.signals.finished.connect(results.append)
        task.run()

        assert results == [None]
        assert runner.run.call_count == 2

    def test_empty_candidate_list_emits_none_without_probing(self) -> None:
        runner = MagicMock(spec=ProcessRunner)
        task = _make_probe_task(runner, [])

        results: list[KeyCandidate | None] = []
        task.signals.finished.connect(results.append)
        task.run()

        assert results == [None]
        runner.run.assert_not_called()

    def test_timeout_on_one_candidate_tries_the_next(self) -> None:
        c1 = _make_candidate("first")
        c2 = _make_candidate("second")
        runner = MagicMock(spec=ProcessRunner)
        runner.run.side_effect = [
            subprocess.TimeoutExpired(cmd=["ssh"], timeout=15),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
        ]
        task = _make_probe_task(runner, [c1, c2])

        results: list[KeyCandidate | None] = []
        task.signals.finished.connect(results.append)
        task.run()

        assert results == [c2]

    def test_oserror_on_one_candidate_tries_the_next(self) -> None:
        c1 = _make_candidate("first")
        c2 = _make_candidate("second")
        runner = MagicMock(spec=ProcessRunner)
        runner.run.side_effect = [
            OSError("no such file"),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
        ]
        task = _make_probe_task(runner, [c1, c2])

        results: list[KeyCandidate | None] = []
        task.signals.finished.connect(results.append)
        task.run()

        assert results == [c2]

    def test_non_default_port_included(self) -> None:
        runner = _make_runner(returncode=0)
        task = _make_probe_task(runner, [_make_candidate("k1")], port=2222)
        task.run()

        call_argv = runner.run.call_args[0][0]
        assert "2222" in call_argv

    def test_no_force_tty(self) -> None:
        runner = _make_runner(returncode=0)
        task = _make_probe_task(runner, [_make_candidate("k1")])
        task.run()

        call_argv = runner.run.call_args[0][0]
        assert "-tt" not in call_argv
        assert "-t" not in call_argv


@pytest.mark.ui
class TestKeyDiscoveryProbeTaskIntegration:
    def test_signal_emitted_via_thread_pool(self, qtbot: object) -> None:
        from PySide6.QtCore import QThreadPool

        runner = _make_runner(returncode=0)
        task = _make_probe_task(runner, [_make_candidate("k1")])

        results: list[KeyCandidate | None] = []
        task.signals.finished.connect(results.append)

        QThreadPool.globalInstance().start(task)
        qtbot.waitUntil(lambda: len(results) >= 1, timeout=2000)  # type: ignore[attr-defined]

        assert len(results) == 1
        assert results[0] is not None
