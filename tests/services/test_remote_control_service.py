# -*- coding: utf-8 -*-
"""Tests for RemoteControlService — preflight + bootstrap helpers for
Claude Code Remote Control (--remote-control flag).

The service shells out via an injectable probe callable so these tests
never touch a real host.
"""

from __future__ import annotations

import pytest

from cpsm.services.remote_control_service import (
    MIN_CLAUDE_VERSION,
    RemoteControlService,
    parse_claude_version,
)


class FakeProbe:
    """Records every probe call; returns canned responses by command."""

    def __init__(self, responses: dict[str, tuple[str, str, int]]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str, int, str, str]] = []

    def __call__(
        self,
        host: str,
        user: str,
        port: int,
        key_path: str,
        remote_command: str,
    ) -> tuple[str, str, int]:
        self.calls.append((host, user, port, key_path, remote_command))
        # Match by command prefix so callers can express ``uname -s`` once.
        for prefix, response in self.responses.items():
            if remote_command.startswith(prefix):
                return response
        return ("", f"no canned response for {remote_command!r}", 1)


# ---------------------------------------------------------------------------
# parse_claude_version
# ---------------------------------------------------------------------------


class TestParseClaudeVersion:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("2.1.51", (2, 1, 51)),
            ("claude-code 2.1.51 (build 12345)", (2, 1, 51)),
            ("Claude Code v3.0.0", (3, 0, 0)),
            ("v0.99.999", (0, 99, 999)),
        ],
    )
    def test_extracts_triplet(self, text: str, expected: tuple[int, int, int]) -> None:
        assert parse_claude_version(text) == expected

    @pytest.mark.parametrize("text", ["", "  ", "no version here", "v2"])
    def test_returns_none_when_no_match(self, text: str) -> None:
        assert parse_claude_version(text) is None


# ---------------------------------------------------------------------------
# check_preflight
# ---------------------------------------------------------------------------


class TestPreflightHappyPath:
    def test_linux_modern_claude_no_api_key_is_ok(self) -> None:
        probe = FakeProbe(
            {
                "uname -s": ("Linux\n", "", 0),
                "claude --version": ("claude-code 2.1.51\n", "", 0),
                "printenv ANTHROPIC_API_KEY": ("", "", 0),
            }
        )
        svc = RemoteControlService(ssh_probe=probe)
        result = svc.check_preflight(host="h", user="u", port=22)
        assert result.ok is True
        assert result.os_kernel == "Linux"
        assert result.claude_version == (2, 1, 51)
        assert result.api_key_set is False
        assert result.errors == []
        assert result.warnings == []

    def test_api_key_set_warns_but_still_ok(self) -> None:
        probe = FakeProbe(
            {
                "uname -s": ("Linux\n", "", 0),
                "claude --version": ("2.2.0\n", "", 0),
                "printenv ANTHROPIC_API_KEY": ("sk-...\n", "", 0),
            }
        )
        svc = RemoteControlService(ssh_probe=probe)
        result = svc.check_preflight(host="h", user="u")
        assert result.ok is True
        assert result.api_key_set is True
        assert len(result.warnings) == 1
        assert "ANTHROPIC_API_KEY" in result.warnings[0]


class TestPreflightHardFails:
    def test_macos_target_blocked(self) -> None:
        probe = FakeProbe(
            {
                "uname -s": ("Darwin\n", "", 0),
                "claude --version": ("2.1.51\n", "", 0),
                "printenv ANTHROPIC_API_KEY": ("", "", 0),
            }
        )
        svc = RemoteControlService(ssh_probe=probe)
        result = svc.check_preflight(host="h", user="u")
        assert result.ok is False
        assert any("macOS" in e for e in result.errors), result.errors

    def test_too_old_claude_blocks(self) -> None:
        probe = FakeProbe(
            {
                "uname -s": ("Linux\n", "", 0),
                "claude --version": ("2.1.50\n", "", 0),
                "printenv ANTHROPIC_API_KEY": ("", "", 0),
            }
        )
        svc = RemoteControlService(ssh_probe=probe)
        result = svc.check_preflight(host="h", user="u")
        assert result.ok is False
        assert any("too old" in e for e in result.errors), result.errors

    def test_missing_claude_blocks(self) -> None:
        probe = FakeProbe(
            {
                "uname -s": ("Linux\n", "", 0),
                "claude --version": (
                    "bash: claude: command not found\n",
                    "",
                    0,
                ),
                "printenv ANTHROPIC_API_KEY": ("", "", 0),
            }
        )
        svc = RemoteControlService(ssh_probe=probe)
        result = svc.check_preflight(host="h", user="u")
        assert result.ok is False
        assert any("PATH" in e or "find" in e for e in result.errors)

    def test_ssh_unreachable_short_circuits(self) -> None:
        probe = FakeProbe(
            {
                "uname -s": (
                    "",
                    "ssh: connect to host nope.example port 22: refused",
                    255,
                ),
            }
        )
        svc = RemoteControlService(ssh_probe=probe)
        result = svc.check_preflight(host="nope.example", user="u")
        assert result.ok is False
        # Doesn't bother running the other probes when SSH itself fails.
        assert len(probe.calls) == 1
        assert "connect" in result.errors[0] or "Could not" in result.errors[0]


# ---------------------------------------------------------------------------
# credentials_present
# ---------------------------------------------------------------------------


class TestCredentialsPresent:
    def test_returns_true_when_file_exists(self) -> None:
        probe = FakeProbe({"test -f": ("OK\n", "", 0)})
        svc = RemoteControlService(ssh_probe=probe)
        assert svc.credentials_present(host="h", user="u") is True

    def test_returns_false_when_file_missing(self) -> None:
        probe = FakeProbe({"test -f": ("NO\n", "", 0)})
        svc = RemoteControlService(ssh_probe=probe)
        assert svc.credentials_present(host="h", user="u") is False

    def test_returns_false_on_ssh_failure(self) -> None:
        probe = FakeProbe({"test -f": ("", "ssh failed", 255)})
        svc = RemoteControlService(ssh_probe=probe)
        assert svc.credentials_present(host="h", user="u") is False


# ---------------------------------------------------------------------------
# build_auth_ssh_argv
# ---------------------------------------------------------------------------


class TestBuildAuthSshArgv:
    def test_includes_forward_port(self) -> None:
        svc = RemoteControlService()
        argv = svc.build_auth_ssh_argv(
            host="example.com",
            user="ubuntu",
            port=22,
            forward_port=8080,
        )
        # The `-o LocalForward=...` is what enables the callback tunnel.
        joined = " ".join(argv)
        assert "LocalForward=8080 localhost:8080" in joined

    def test_includes_user_at_host(self) -> None:
        svc = RemoteControlService()
        argv = svc.build_auth_ssh_argv(
            host="example.com",
            user="ubuntu",
        )
        assert "ubuntu@example.com" in argv

    def test_non_default_port_is_passed(self) -> None:
        svc = RemoteControlService()
        argv = svc.build_auth_ssh_argv(
            host="example.com",
            user="ubuntu",
            port=2222,
        )
        # Either OpenSSH (-p 2222) or plink (-P 2222) format.
        idx = next(
            (i for i, a in enumerate(argv) if a in ("-p", "-P")),
            -1,
        )
        assert idx >= 0
        assert argv[idx + 1] == "2222"

    def test_identity_file_passed_when_provided(self) -> None:
        svc = RemoteControlService()
        argv = svc.build_auth_ssh_argv(
            host="h",
            user="u",
            key_path="/tmp/key",
        )
        assert "-i" in argv
        assert "/tmp/key" in argv

    def test_identity_is_pinned_at_this_call_site(self) -> None:
        """The auth-wizard argv must pin the identity, not merely name it.

        Asserted here rather than only on SshBinary.build_argv, because the
        builder's own tests cannot see a caller that defeats the pin — by
        putting IdentitiesOnly=no into its own ssh_options, or by bypassing
        SshBinary altogether. A call site silently re-introducing the defect
        is exactly what this feature exists to prevent.
        """
        svc = RemoteControlService()
        argv = svc.build_auth_ssh_argv(
            host="h",
            user="u",
            key_path="/tmp/key",
        )
        assert "IdentitiesOnly=yes" in argv, argv
        assert "IdentitiesOnly=no" not in argv, argv

    def test_no_identity_means_no_pin_at_this_call_site(self) -> None:
        """With no key_path there is no -i, so there must be no pin.

        IdentitiesOnly=yes without -i restricts ssh to the default identity
        files and disables agent-held keys.
        """
        svc = RemoteControlService()
        argv = svc.build_auth_ssh_argv(host="h", user="u")
        assert "-i" not in argv, argv
        assert not any("IdentitiesOnly" in a for a in argv), argv

    def test_default_probe_argv_is_pinned(self, monkeypatch) -> None:
        """The preflight probe (_default_probe) must pin too.

        This call site had no test at all: nothing exercised its identity
        path, so a regression there would have been invisible. Capture the
        argv it hands to ProcessRunner rather than letting it spawn ssh.
        """
        import cpsm.services.remote_control_service as rcs

        captured: list[list[str]] = []

        class _FakeResult:
            stdout = ""
            stderr = ""
            returncode = 0

        class _FakeRunner:
            def run(self, argv, **kwargs):
                captured.append(list(argv))
                return _FakeResult()

        monkeypatch.setattr(rcs, "ProcessRunner", _FakeRunner)
        rcs._default_probe("h", "u", 22, "/tmp/key", "uname -s")

        assert captured, "the probe never invoked the runner"
        argv = captured[0]
        assert "-i" in argv, argv
        assert "/tmp/key" in argv, argv
        assert "IdentitiesOnly=yes" in argv, argv

    def test_default_probe_without_key_is_not_pinned(self, monkeypatch) -> None:
        """No key_path means no -i, therefore no pin."""
        import cpsm.services.remote_control_service as rcs

        captured: list[list[str]] = []

        class _FakeResult:
            stdout = ""
            stderr = ""
            returncode = 0

        class _FakeRunner:
            def run(self, argv, **kwargs):
                captured.append(list(argv))
                return _FakeResult()

        monkeypatch.setattr(rcs, "ProcessRunner", _FakeRunner)
        rcs._default_probe("h", "u", 22, "", "uname -s")

        assert captured, "the probe never invoked the runner"
        argv = captured[0]
        assert "-i" not in argv, argv
        assert not any("IdentitiesOnly" in a for a in argv), argv

    def test_min_claude_version_constant_is_2_1_51(self) -> None:
        # Documented v1 baseline — if you change this, update the README.
        assert MIN_CLAUDE_VERSION == (2, 1, 51)
