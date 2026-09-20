# -*- coding: utf-8 -*-
"""
RemoteControlService — preflight + bootstrap helpers for ``claude --remote-control``.

The actual /login OAuth flow is interactive and runs inside a terminal the
user drives manually. CPSM's job is to:

  1. Validate the target box is capable (OS, Claude Code version, env).
  2. Spawn an SSH session with the OAuth callback port forwarded so the
     user can paste the printed URL into a local browser.
  3. Poll the remote for ``~/.claude/.credentials.json`` so the wizard
     knows when /login succeeded.

All SSH interaction happens via injectable callables so this service is
fully testable without touching real hosts. The default callable wraps
:class:`cpsm.platform.process_runner.ProcessRunner` against
:class:`cpsm.platform.ssh_binary.SshBinary`.

Spec note: the feature is **Linux remotes only** — macOS targets store
their OAuth credentials in the Keychain, which is inaccessible from
non-GUI SSH sessions. ``check_preflight`` returns a hard ``failed`` for
macOS targets and the wizard refuses to continue.
"""

from __future__ import annotations

import logging
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from cpsm.platform.process_runner import ProcessRunner
from cpsm.platform.ssh_binary import SshBinary

__all__ = [
    "MIN_CLAUDE_VERSION",
    "PreflightResult",
    "RemoteControlService",
    "SshProbeFn",
    "parse_claude_version",
]

logger = logging.getLogger(__name__)


# Minimum Claude Code CLI version that supports ``--remote-control``.
MIN_CLAUDE_VERSION: tuple[int, int, int] = (2, 1, 51)


# Probe function signature: (host, user, port, key_path, remote_command) →
# (stdout, stderr, returncode). Tests inject fakes.
SshProbeFn = Callable[[str, str, int, str, str], tuple[str, str, int]]


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PreflightResult:
    """Outcome of the three preflight probes.

    ``ok`` is True only when every hard requirement passes. ``warnings``
    holds soft issues (e.g. ANTHROPIC_API_KEY present) that the user
    should see but that don't block the flow.

    For a local-profile connection (host=""), the probes run against the
    local box via the same probe function — caller is expected to inject
    a local-runner variant.
    """

    ok: bool
    os_kernel: str  # 'Linux', 'Darwin', etc.
    claude_version: tuple[int, int, int] | None
    api_key_set: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Version parsing
# ---------------------------------------------------------------------------


_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")


def parse_claude_version(text: str) -> tuple[int, int, int] | None:
    """Extract ``(major, minor, patch)`` from ``claude --version`` output.

    Accepts ``"2.1.51"``, ``"claude-code 2.1.51 (build ...)"``, etc.
    Returns ``None`` if no version-like tuple is found.
    """
    m = _VERSION_RE.search(text or "")
    if m is None:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


# ---------------------------------------------------------------------------
# Default SSH probe runner
# ---------------------------------------------------------------------------


def _default_probe(
    host: str, user: str, port: int, key_path: str, remote_command: str
) -> tuple[str, str, int]:
    """Run *remote_command* via ssh in BatchMode. Returns (stdout, stderr, rc).

    Connection errors return ``("", <err>, -1)`` rather than raising — the
    service-layer caller summarises this into a PreflightResult.
    """
    binary = SshBinary.detect()
    argv = binary.build_argv(
        host=host,
        user=user,
        port=port,
        identity_file=Path(key_path) if key_path else None,
        ssh_options=[
            "BatchMode=yes",
            "ConnectTimeout=5",
            "StrictHostKeyChecking=accept-new",
        ],
        remote_command=["sh", "-c", remote_command],
        force_tty=False,
    )
    runner = ProcessRunner()
    try:
        result = runner.run(argv, timeout=15.0, check=False)
    except subprocess.TimeoutExpired:
        return ("", "ssh probe timed out", -1)
    except OSError as exc:
        return ("", f"ssh launch failed: {exc}", -1)
    return (result.stdout or "", result.stderr or "", result.returncode)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class RemoteControlService:
    """SSH probes + auth-flow helpers for Claude Code Remote Control.

    All side-effecting SSH is funnelled through ``ssh_probe`` so tests can
    swap it out. The default probes a real host via ``ssh`` in BatchMode.
    """

    def __init__(self, ssh_probe: SshProbeFn | None = None) -> None:
        self._probe: SshProbeFn = ssh_probe or _default_probe

    # ------------------------------------------------------------------
    # Preflight
    # ------------------------------------------------------------------

    def check_preflight(
        self,
        *,
        host: str,
        user: str,
        port: int = 22,
        key_path: str = "",
    ) -> PreflightResult:
        """Run the three preflight probes and return a structured result.

        Errors are hard-fails (``ok=False``):
          - macOS target (Keychain inaccessible from SSH)
          - claude version < MIN_CLAUDE_VERSION
          - claude not installed on PATH

        Warnings (``ok`` can still be True):
          - ``ANTHROPIC_API_KEY`` set on the remote (short-circuits OAuth)
        """
        errors: list[str] = []
        warnings: list[str] = []

        # 1. uname -s
        os_kernel = ""
        stdout, stderr, rc = self._probe(host, user, port, key_path, "uname -s")
        if rc != 0:
            errors.append(
                f"Could not connect to {user}@{host}: {stderr.strip() or 'unknown error'}"
            )
            return PreflightResult(
                ok=False,
                os_kernel="",
                claude_version=None,
                api_key_set=False,
                errors=errors,
            )
        os_kernel = stdout.strip()
        if os_kernel == "Darwin":
            errors.append(
                "macOS targets cannot be authenticated over SSH — Claude's "
                "OAuth credentials live in the macOS Keychain, which is "
                "inaccessible from non-GUI SSH sessions. Run /login locally "
                "on the Mac instead."
            )

        # 2. claude --version
        claude_version: tuple[int, int, int] | None = None
        stdout, stderr, rc = self._probe(
            host, user, port, key_path, "claude --version 2>&1 || true"
        )
        # The ``|| true`` keeps rc=0; we judge on parse success.
        claude_version = parse_claude_version(stdout)
        if claude_version is None:
            errors.append(
                f"Could not find Claude Code on {user}@{host}'s PATH. "
                "Install or PATH-fix it before retrying."
            )
        elif claude_version < MIN_CLAUDE_VERSION:
            errors.append(
                f"Claude Code {'.'.join(str(v) for v in claude_version)} is too "
                f"old for Remote Control. Need ≥ "
                f"{'.'.join(str(v) for v in MIN_CLAUDE_VERSION)}."
            )

        # 3. ANTHROPIC_API_KEY
        stdout, stderr, rc = self._probe(
            host,
            user,
            port,
            key_path,
            "printenv ANTHROPIC_API_KEY 2>/dev/null || true",
        )
        api_key_set = bool(stdout.strip())
        if api_key_set:
            warnings.append(
                "ANTHROPIC_API_KEY is set in the remote shell's environment. "
                "It must be unset for the /login flow to use OAuth — the "
                "wizard will run claude with ``env -u ANTHROPIC_API_KEY``."
            )

        ok = not errors
        return PreflightResult(
            ok=ok,
            os_kernel=os_kernel,
            claude_version=claude_version,
            api_key_set=api_key_set,
            errors=errors,
            warnings=warnings,
        )

    # ------------------------------------------------------------------
    # Credentials polling
    # ------------------------------------------------------------------

    def credentials_present(
        self,
        *,
        host: str,
        user: str,
        port: int = 22,
        key_path: str = "",
    ) -> bool:
        """Return True if ``~/.claude/.credentials.json`` exists on the remote.

        Used by the wizard to detect when /login has completed.  Uses
        ``test -f`` so the probe returns 0/1 cleanly.
        """
        stdout, _stderr, rc = self._probe(
            host,
            user,
            port,
            key_path,
            'test -f "$HOME/.claude/.credentials.json" && echo OK || echo NO',
        )
        return rc == 0 and stdout.strip() == "OK"

    # ------------------------------------------------------------------
    # Auth terminal
    # ------------------------------------------------------------------

    def build_auth_ssh_argv(
        self,
        *,
        host: str,
        user: str,
        port: int = 22,
        key_path: str = "",
        forward_port: int = 8080,
    ) -> list[str]:
        """Build the SSH argv used by the auth wizard to spawn an
        interactive shell with the OAuth callback port forwarded.

        Tests assert on the produced argv; the wizard hands it to a
        terminal launcher.
        """
        binary = SshBinary.detect()
        argv = binary.build_argv(
            host=host,
            user=user,
            port=port,
            identity_file=Path(key_path) if key_path else None,
            ssh_options=[
                f"LocalForward={forward_port} localhost:{forward_port}",
                "StrictHostKeyChecking=accept-new",
            ],
            # Interactive shell, no remote_command. The terminal will be
            # driven manually by the user (``unset ANTHROPIC_API_KEY``,
            # ``claude``, then ``/login``).
            remote_command=None,
            force_tty=True,
        )
        return argv
