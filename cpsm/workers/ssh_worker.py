# -*- coding: utf-8 -*-
"""
SshWorker — QRunnable wrappers for short SSH-related operations run from the
UI thread pool (e.g. "Test Connection" button in the Connection Editor).

Spec: §1.5, §5.7
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from cpsm.platform.process_runner import ProcessRunner
from cpsm.platform.ssh_binary import SshBinary
from cpsm.services.key_discovery import KeyCandidate

# ---------------------------------------------------------------------------
# Signal carrier (QObject required for signals on QRunnable)
# ---------------------------------------------------------------------------


class SshTestSignals(QObject):
    """Signals emitted by :class:`SshTestConnectionTask`.

    Attributes:
        finished: ``(success, error_message)`` — ``success`` is True when the
            SSH test command exited with code 0.  ``error_message`` is empty on
            success, or a human-readable description of the failure.
    """

    finished: Signal = Signal(bool, str)


# ---------------------------------------------------------------------------
# SshTestConnectionTask
# ---------------------------------------------------------------------------


class SshTestConnectionTask(QRunnable):
    """QRunnable that tests an SSH connection in a thread-pool thread.

    Runs ``ssh -o BatchMode=yes -o ConnectTimeout=5 user@host true`` (or the
    equivalent plink command) via :class:`ProcessRunner` and emits
    ``signals.finished(success, error_message)`` when done.

    This is used by the Connection Editor's "Test Connection" button.

    Args:
        ssh_binary:     Detected :class:`SshBinary` instance.
        host:           Remote hostname or IP.
        user:           Remote username.
        port:           SSH port (default 22).
        identity_file:  Path to private key (optional).
        runner:         Custom :class:`ProcessRunner` for testing; a fresh
                        instance is created when ``None``.
    """

    def __init__(
        self,
        ssh_binary: SshBinary,
        host: str,
        user: str,
        port: int = 22,
        identity_file: Path | None = None,
        runner: ProcessRunner | None = None,
    ) -> None:
        super().__init__()
        self._ssh_binary = ssh_binary
        self._host = host
        self._user = user
        self._port = port
        self._identity_file = identity_file
        self._runner = runner or ProcessRunner()
        self.signals = SshTestSignals()
        # Allow garbage collection after the task finishes.
        self.setAutoDelete(True)

    @Slot()
    def run(self) -> None:
        """Execute the SSH test and emit ``signals.finished``."""
        # Build argv:  ssh -o BatchMode=yes -o ConnectTimeout=5 … true
        argv = self._ssh_binary.build_argv(
            host=self._host,
            user=self._user,
            port=self._port,
            identity_file=self._identity_file,
            ssh_options=["BatchMode=yes", "ConnectTimeout=5"],
            remote_command=["true"],
            force_tty=False,
        )

        try:
            result = self._runner.run(argv, timeout=15.0, check=False)
        except subprocess.TimeoutExpired:
            self.signals.finished.emit(False, "Connection timed out")
            return
        except OSError as exc:
            self.signals.finished.emit(False, f"Failed to launch SSH: {exc}")
            return
        except Exception as exc:
            self.signals.finished.emit(False, str(exc))
            return

        if result.returncode == 0:
            self.signals.finished.emit(True, "")
        else:
            error = (result.stderr or result.stdout or "").strip()
            if not error:
                error = f"SSH exited with code {result.returncode}"
            self.signals.finished.emit(False, error)


# ---------------------------------------------------------------------------
# KeyDiscoveryProbeTask
# ---------------------------------------------------------------------------


class KeyDiscoveryProbeSignals(QObject):
    """Signals emitted by :class:`KeyDiscoveryProbeTask`.

    Attributes:
        finished: ``(candidate,)`` — the first :class:`KeyCandidate` (from
            ``cpsm.services.key_discovery``) that authenticated, probed in the
            order supplied, or ``None`` if none did. Declared as ``object``
            (not ``KeyCandidate``) because PySide6 signals cannot carry an
            arbitrary dataclass type directly, and ``None`` must also be a
            valid payload.
    """

    finished: Signal = Signal(object)


class KeyDiscoveryProbeTask(QRunnable):
    """QRunnable that probes ranked :class:`KeyCandidate` entries in order.

    Each candidate is tested with the same read-only command
    :class:`SshTestConnectionTask` uses for "Test Connection" —
    ``ssh -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=5 -i <key>
    user@host true`` — via :meth:`SshBinary.build_argv`, which is what
    actually adds ``IdentitiesOnly=yes`` (see the long comment at
    ``cpsm/platform/ssh_binary.py:205``). ``BatchMode=yes`` is essential here:
    without it a key requiring an unknown passphrase would make ssh prompt
    interactively and hang this task's thread indefinitely.

    Probing stops at the first candidate that authenticates (returncode 0);
    later candidates are never invoked. If no candidate authenticates,
    ``signals.finished`` is emitted with ``None``.

    This performs BLOCKING network I/O and must run via ``QThreadPool``,
    never on the GUI thread — the same discipline as
    :class:`SshTestConnectionTask`.

    Args:
        ssh_binary:  Detected :class:`SshBinary` instance.
        host:        Remote hostname or IP.
        user:        Remote username.
        port:        SSH port (default 22).
        candidates:  Ranked candidates, best first (see
                     ``KeyDiscoveryService.discover``).
        runner:      Custom :class:`ProcessRunner` for testing; a fresh
                     instance is created when ``None``. Tests MUST inject
                     this — the default constructs a real subprocess runner
                     capable of reaching the network.
    """

    def __init__(
        self,
        *,
        ssh_binary: SshBinary,
        host: str,
        user: str,
        port: int = 22,
        candidates: Sequence[KeyCandidate],
        runner: ProcessRunner | None = None,
    ) -> None:
        super().__init__()
        self._ssh_binary = ssh_binary
        self._host = host
        self._user = user
        self._port = port
        self._candidates = list(candidates)
        self._runner = runner or ProcessRunner()
        self.signals = KeyDiscoveryProbeSignals()
        # Allow garbage collection after the task finishes.
        self.setAutoDelete(True)

    @Slot()
    def run(self) -> None:
        """Probe each candidate in order; emit the first that authenticates."""
        for candidate in self._candidates:
            argv = self._ssh_binary.build_argv(
                host=self._host,
                user=self._user,
                port=self._port,
                identity_file=candidate.private_path,
                ssh_options=["BatchMode=yes", "ConnectTimeout=5"],
                remote_command=["true"],
                force_tty=False,
            )
            try:
                result = self._runner.run(argv, timeout=15.0, check=False)
            except subprocess.TimeoutExpired:
                continue
            except OSError:
                continue
            except Exception:
                # A candidate that errors out (e.g. permissions) is simply
                # not the right key; keep trying the rest.
                continue

            if result.returncode == 0:
                self.signals.finished.emit(candidate)
                return

        self.signals.finished.emit(None)
