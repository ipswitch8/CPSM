# -*- coding: utf-8 -*-
"""Tests for DiscoveryService — finding outside-CPSM sessions for adoption.

Focus areas:
  - Linux /proc parsing: comm/cmdline/cwd/tty extraction with edge cases.
  - Tmux ancestor exclusion (anything inside tmux is already managed).
  - Classification: claude-local vs claude-remote vs ssh-shell.
  - Matching: claude-local by exact cwd, ssh-based by host+user.
  - Platform fallback: macOS/Windows return [] without explosion.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from cpsm.data.schema import (
    ClaudeLocalConnection,
    ClaudeRemoteConnection,
    CpsmDocument,
    SshKey,
    SshShellConnection,
)
from cpsm.services.discovery_service import (
    DiscoveredSession,
    DiscoveryService,
    ProcInfo,
    _has_tmux_ancestor,
    _parse_ssh_args,
    _parse_stat,
    _ssh_args_invoke_claude,
    append_continue_flag,
    is_pid_alive,
    send_sigterm,
    wait_for_pid_exit,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeProcSource:
    def __init__(self, procs: list[ProcInfo]) -> None:
        self._procs = procs

    def list_processes(self) -> list[ProcInfo]:
        return list(self._procs)


def _proc(
    pid: int,
    *,
    ppid: int = 1,
    comm: str = "",
    cmdline: tuple[str, ...] = (),
    cwd: str = "",
    tty: str = "",
) -> ProcInfo:
    return ProcInfo(pid=pid, ppid=ppid, comm=comm, cmdline=cmdline, cwd=cwd, tty=tty)


def _empty_doc() -> CpsmDocument:
    return CpsmDocument()


# ---------------------------------------------------------------------------
# /proc/<pid>/stat parsing
# ---------------------------------------------------------------------------


class TestParseStat:
    def test_simple(self) -> None:
        comm, ppid = _parse_stat("1234 (claude) S 999 ...")
        assert comm == "claude"
        assert ppid == 999

    def test_comm_with_parens(self) -> None:
        # Linux truncates comm to 16 chars; embedded parens are legal.
        comm, ppid = _parse_stat("42 (tmux: server) S 1 ...")
        assert comm == "tmux: server"
        assert ppid == 1

    def test_comm_with_inner_close_paren(self) -> None:
        comm, ppid = _parse_stat("99 (a)b) S 7 ...")
        # We use rfind(')') so the inner ')' becomes part of comm.
        assert comm == "a)b"
        assert ppid == 7

    def test_unparseable_returns_none(self) -> None:
        assert _parse_stat("garbage") == (None, 0)
        assert _parse_stat("") == (None, 0)

    def test_missing_ppid_returns_zero(self) -> None:
        comm, ppid = _parse_stat("1 (init) S")
        assert comm == "init"
        assert ppid == 0


# ---------------------------------------------------------------------------
# ssh argv parsing
# ---------------------------------------------------------------------------


class TestParseSshArgs:
    def test_user_at_host(self) -> None:
        host, user = _parse_ssh_args(("ssh", "ubuntu@example.com"))
        assert host == "example.com"
        assert user == "ubuntu"

    def test_dash_l_user(self) -> None:
        host, user = _parse_ssh_args(("ssh", "-l", "ubuntu", "example.com"))
        assert host == "example.com"
        assert user == "ubuntu"

    def test_bare_host(self) -> None:
        host, user = _parse_ssh_args(("ssh", "example.com"))
        assert host == "example.com"
        assert user == ""

    def test_skip_options_with_values(self) -> None:
        host, user = _parse_ssh_args(
            (
                "ssh",
                "-i",
                "/key",
                "-p",
                "2222",
                "-o",
                "StrictHostKeyChecking=no",
                "ubuntu@example.com",
            )
        )
        assert host == "example.com"
        assert user == "ubuntu"

    def test_no_host_returns_empty(self) -> None:
        host, user = _parse_ssh_args(("ssh", "-V"))
        assert host == ""
        assert user == ""

    def test_scp_remote_path_does_not_corrupt_host(self) -> None:
        """scp argv embeds remote paths as ``user@host:/path/file``; the host
        field must not include the ``:/path`` suffix."""
        host, user = _parse_ssh_args(("scp", "ubuntu@dev.example.com:/tmp/file", "./local"))
        assert host == "dev.example.com"
        assert user == "ubuntu"

    def test_scp_bare_host_with_remote_path(self) -> None:
        host, user = _parse_ssh_args(("scp", "dev.example.com:/etc/hosts", "./hosts"))
        assert host == "dev.example.com"
        assert user == ""


class TestSshArgsInvokeClaude:
    def test_remote_command_is_claude(self) -> None:
        assert _ssh_args_invoke_claude(("ssh", "host", "claude", "--resume"))

    def test_no_remote_command_is_shell(self) -> None:
        assert not _ssh_args_invoke_claude(("ssh", "user@host"))

    def test_remote_bash_with_claude(self) -> None:
        # Real-world: ssh ... bash -ic 'cd /x && claude'
        assert _ssh_args_invoke_claude(("ssh", "host", "bash", "-ic", "cd /x && claude --resume"))


# ---------------------------------------------------------------------------
# Tmux ancestor walk
# ---------------------------------------------------------------------------


class TestHasTmuxAncestor:
    def test_direct_tmux_parent(self) -> None:
        tmux = _proc(100, ppid=1, comm="tmux: server")
        claude = _proc(200, ppid=100, comm="claude")
        assert _has_tmux_ancestor(claude, {100: tmux, 200: claude})

    def test_grandparent_tmux(self) -> None:
        tmux = _proc(100, ppid=1, comm="tmux: server")
        bash = _proc(150, ppid=100, comm="bash")
        claude = _proc(200, ppid=150, comm="claude")
        assert _has_tmux_ancestor(claude, {100: tmux, 150: bash, 200: claude})

    def test_no_tmux_ancestor(self) -> None:
        gnome = _proc(100, ppid=1, comm="gnome-terminal-")
        bash = _proc(150, ppid=100, comm="bash")
        claude = _proc(200, ppid=150, comm="claude")
        assert not _has_tmux_ancestor(claude, {100: gnome, 150: bash, 200: claude})

    def test_orphan_chain_terminates(self) -> None:
        """If the parent map is missing entries (e.g. parent already exited),
        the walk returns False rather than looping forever."""
        claude = _proc(200, ppid=999, comm="claude")  # 999 not in map
        assert not _has_tmux_ancestor(claude, {200: claude})


# ---------------------------------------------------------------------------
# End-to-end DiscoveryService behavior with FakeProcSource
# ---------------------------------------------------------------------------


class TestFindOutsideSessions:
    def test_finds_orphan_claude_outside_tmux(self) -> None:
        gnome = _proc(100, ppid=1, comm="gnome-terminal-")
        bash = _proc(150, ppid=100, comm="bash")
        claude = _proc(
            200,
            ppid=150,
            comm="claude",
            cmdline=("claude", "--resume"),
            cwd="/home/user/projects/dotfiles",
            tty="/dev/pts/3",
        )
        svc = DiscoveryService(proc_source=_FakeProcSource([gnome, bash, claude]))
        sessions = svc.find_outside_sessions(_empty_doc())

        assert len(sessions) == 1
        s = sessions[0]
        assert s.pid == 200
        assert s.kind == "claude-local"
        assert s.cwd == "/home/user/projects/dotfiles"
        assert s.tty == "/dev/pts/3"
        assert s.suggested_connection_id == ""  # no doc match

    def test_excludes_claude_inside_tmux(self) -> None:
        tmux = _proc(100, ppid=1, comm="tmux: server")
        bash = _proc(150, ppid=100, comm="bash")
        claude = _proc(200, ppid=150, comm="claude", cmdline=("claude",))
        svc = DiscoveryService(proc_source=_FakeProcSource([tmux, bash, claude]))
        sessions = svc.find_outside_sessions(_empty_doc())
        assert sessions == []

    def test_classifies_ssh_with_remote_claude_as_claude_remote(self) -> None:
        gnome = _proc(100, ppid=1, comm="gnome-terminal-")
        bash = _proc(150, ppid=100, comm="bash")
        ssh = _proc(
            200,
            ppid=150,
            comm="ssh",
            cmdline=("ssh", "ubuntu@dev.example.com", "claude", "--resume"),
        )
        svc = DiscoveryService(proc_source=_FakeProcSource([gnome, bash, ssh]))
        sessions = svc.find_outside_sessions(_empty_doc())

        assert len(sessions) == 1
        s = sessions[0]
        assert s.kind == "claude-remote"
        assert s.host == "dev.example.com"
        assert s.user == "ubuntu"

    def test_classifies_ssh_without_remote_claude_as_ssh_shell(self) -> None:
        gnome = _proc(100, ppid=1, comm="gnome-terminal-")
        ssh = _proc(
            200,
            ppid=100,
            comm="ssh",
            cmdline=("ssh", "ubuntu@dev.example.com"),
        )
        svc = DiscoveryService(proc_source=_FakeProcSource([gnome, ssh]))
        sessions = svc.find_outside_sessions(_empty_doc())

        assert len(sessions) == 1
        assert sessions[0].kind == "ssh-shell"

    def test_does_not_match_claude_prefixed_binaries(self) -> None:
        """``claude-tools`` / ``claude-helper`` etc. are unrelated programs
        and must not be classified as Claude (would cause false-positive
        adoption candidates)."""
        gnome = _proc(100, ppid=1, comm="gnome-terminal-")
        bogus = _proc(
            201,
            ppid=100,
            comm="claude-test",
            cmdline=("claude-test", "--flag"),
            cwd="/tmp",
        )
        also_bogus = _proc(
            202,
            ppid=100,
            comm="claude-helper",
            cmdline=("claude-helper",),
            cwd="/tmp",
        )
        svc = DiscoveryService(proc_source=_FakeProcSource([gnome, bogus, also_bogus]))
        assert svc.find_outside_sessions(_empty_doc()) == []

    def test_skips_kernel_threads_and_unrelated_processes(self) -> None:
        kthreadd = _proc(2, ppid=0, comm="kthreadd", cmdline=())
        firefox = _proc(300, ppid=1, comm="firefox", cmdline=("firefox", "--profile"))
        svc = DiscoveryService(proc_source=_FakeProcSource([kthreadd, firefox]))
        assert svc.find_outside_sessions(_empty_doc()) == []

    def test_results_are_sorted_deterministically(self) -> None:
        c2 = _proc(500, ppid=1, comm="claude", cmdline=("claude",), cwd="/b")
        c1 = _proc(300, ppid=1, comm="claude", cmdline=("claude",), cwd="/a")
        svc = DiscoveryService(proc_source=_FakeProcSource([c2, c1]))
        sessions = svc.find_outside_sessions(_empty_doc())
        # Sort key is (kind, pid) — both claude-local, so by pid ascending.
        assert [s.pid for s in sessions] == [300, 500]


# ---------------------------------------------------------------------------
# Connection matching
# ---------------------------------------------------------------------------


def _doc_with_local_conn(project_folder: str) -> CpsmDocument:
    conn = ClaudeLocalConnection(
        id="dotfiles",
        name="Dotfiles",
        launch_profile="claude-local",
        project_folder=project_folder,
        claude_options="--resume",
    )
    return CpsmDocument(connections=[conn])


def _doc_with_remote_conn(host: str, user: str, *, profile: str = "claude-remote") -> CpsmDocument:
    key = SshKey(
        id="key-prod",
        name="prod",
        type="ed25519",
        private_path="~/.ssh/id_ed25519_prod",
        public_path="~/.ssh/id_ed25519_prod.pub",
    )
    if profile == "claude-remote":
        conn = ClaudeRemoteConnection(
            id="web01",
            name="WebApp",
            launch_profile="claude-remote",
            host=host,
            user=user,
            identity_file_ref="key-prod",
            project_folder="/opt/app",
            claude_options="",
        )
    else:
        conn = SshShellConnection(
            id="web01",
            name="WebApp",
            launch_profile="ssh-shell",
            host=host,
            user=user,
            identity_file_ref="key-prod",
        )
    return CpsmDocument(ssh_keys=[key], connections=[conn])


class TestMatching:
    def test_claude_local_matched_by_exact_cwd(self, tmp_path: Path) -> None:
        # Use a real path so realpath() works.
        target = tmp_path / "project"
        target.mkdir()
        doc = _doc_with_local_conn(str(target))

        gnome = _proc(100, ppid=1, comm="gnome-terminal-")
        claude = _proc(
            200,
            ppid=100,
            comm="claude",
            cmdline=("claude",),
            cwd=str(target),
        )
        svc = DiscoveryService(proc_source=_FakeProcSource([gnome, claude]))
        sessions = svc.find_outside_sessions(doc)

        assert len(sessions) == 1
        assert sessions[0].suggested_connection_id == "dotfiles"

    def test_claude_local_no_match_for_different_cwd(self, tmp_path: Path) -> None:
        proj_a = tmp_path / "a"
        proj_a.mkdir()
        proj_b = tmp_path / "b"
        proj_b.mkdir()
        doc = _doc_with_local_conn(str(proj_a))

        gnome = _proc(100, ppid=1, comm="gnome-terminal-")
        claude = _proc(
            200,
            ppid=100,
            comm="claude",
            cmdline=("claude",),
            cwd=str(proj_b),  # different folder
        )
        svc = DiscoveryService(proc_source=_FakeProcSource([gnome, claude]))
        sessions = svc.find_outside_sessions(doc)

        assert sessions[0].suggested_connection_id == ""

    def test_claude_remote_matched_by_host_and_user(self) -> None:
        doc = _doc_with_remote_conn("dev.example.com", "ubuntu")
        gnome = _proc(100, ppid=1, comm="gnome-terminal-")
        ssh = _proc(
            200,
            ppid=100,
            comm="ssh",
            cmdline=("ssh", "ubuntu@dev.example.com", "claude"),
        )
        svc = DiscoveryService(proc_source=_FakeProcSource([gnome, ssh]))
        sessions = svc.find_outside_sessions(doc)

        assert sessions[0].suggested_connection_id == "web01"

    def test_ssh_shell_matched_by_host_user_with_correct_profile(self) -> None:
        doc = _doc_with_remote_conn("dev.example.com", "ubuntu", profile="ssh-shell")
        gnome = _proc(100, ppid=1, comm="gnome-terminal-")
        ssh = _proc(
            200,
            ppid=100,
            comm="ssh",
            cmdline=("ssh", "ubuntu@dev.example.com"),  # no remote claude
        )
        svc = DiscoveryService(proc_source=_FakeProcSource([gnome, ssh]))
        sessions = svc.find_outside_sessions(doc)

        assert sessions[0].kind == "ssh-shell"
        assert sessions[0].suggested_connection_id == "web01"

    def test_remote_no_match_when_host_differs(self) -> None:
        doc = _doc_with_remote_conn("prod.example.com", "ubuntu")
        gnome = _proc(100, ppid=1, comm="gnome-terminal-")
        ssh = _proc(
            200,
            ppid=100,
            comm="ssh",
            cmdline=("ssh", "ubuntu@dev.example.com", "claude"),
        )
        svc = DiscoveryService(proc_source=_FakeProcSource([gnome, ssh]))
        sessions = svc.find_outside_sessions(doc)
        assert sessions[0].suggested_connection_id == ""

    def test_ssh_shell_session_matches_claude_remote_connection(self) -> None:
        """Cross-profile match: a manually-opened plain ssh (kind=ssh-shell)
        should match a claude-remote Connection by host+user, since the
        user might just have an interactive SSH terminal to a host they've
        configured for claude-remote launches.
        """
        doc = _doc_with_remote_conn("dev.example.com", "ubuntu", profile="claude-remote")
        gnome = _proc(100, ppid=1, comm="gnome-terminal-")
        ssh = _proc(
            200,
            ppid=100,
            comm="ssh",
            cmdline=("ssh", "ubuntu@dev.example.com"),  # no claude in argv
        )
        svc = DiscoveryService(proc_source=_FakeProcSource([gnome, ssh]))
        sessions = svc.find_outside_sessions(doc)

        assert len(sessions) == 1
        # Classified as ssh-shell (no claude in argv) — but matched to the
        # claude-remote Connection because we cross-match SSH-based profiles.
        assert sessions[0].kind == "ssh-shell"
        assert sessions[0].suggested_connection_id == "web01"

    def test_match_prefers_same_profile_when_both_exist(self) -> None:
        """When both a claude-remote and ssh-shell Connection match
        host+user, we should prefer the same-profile one."""
        # Build two connections to the same host: one of each profile.
        key = SshKey(
            id="key-prod",
            name="prod",
            type="ed25519",
            private_path="~/.ssh/id_ed25519_prod",
            public_path="~/.ssh/id_ed25519_prod.pub",
        )
        claude_remote_conn = ClaudeRemoteConnection(
            id="web-claude",
            name="WebApp Claude",
            launch_profile="claude-remote",
            host="dev.example.com",
            user="ubuntu",
            identity_file_ref="key-prod",
            project_folder="/opt/app",
            claude_options="",
        )
        ssh_shell_conn = SshShellConnection(
            id="web-shell",
            name="WebApp Shell",
            launch_profile="ssh-shell",
            host="dev.example.com",
            user="ubuntu",
            identity_file_ref="key-prod",
        )
        doc = CpsmDocument(ssh_keys=[key], connections=[claude_remote_conn, ssh_shell_conn])

        # Plain ssh argv → kind=ssh-shell. Should match ssh-shell Connection.
        gnome = _proc(100, ppid=1, comm="gnome-terminal-")
        ssh = _proc(200, ppid=100, comm="ssh", cmdline=("ssh", "ubuntu@dev.example.com"))
        svc = DiscoveryService(proc_source=_FakeProcSource([gnome, ssh]))
        sessions = svc.find_outside_sessions(doc)
        assert sessions[0].kind == "ssh-shell"
        assert sessions[0].suggested_connection_id == "web-shell"

    def test_remote_bare_host_matches_any_user(self) -> None:
        """ssh argv without an explicit user → match any user on the host."""
        doc = _doc_with_remote_conn("dev.example.com", "ubuntu")
        gnome = _proc(100, ppid=1, comm="gnome-terminal-")
        ssh = _proc(
            200,
            ppid=100,
            comm="ssh",
            cmdline=("ssh", "dev.example.com", "claude"),  # no user
        )
        svc = DiscoveryService(proc_source=_FakeProcSource([gnome, ssh]))
        sessions = svc.find_outside_sessions(doc)
        assert sessions[0].suggested_connection_id == "web01"

    def test_ambiguous_host_user_returns_empty_for_correlation(self) -> None:
        """When ≥2 Connections share host+user (different project_folders),
        local matching cannot pick correctly — leave the suggested id empty
        and let CorrelationService disambiguate via remote probe.
        Regression: previously returned the first match, mis-tagging every
        session whose actual project differed from candidate #1.
        """
        key = SshKey(
            id="key-prod",
            name="prod",
            type="ed25519",
            private_path="~/.ssh/id_ed25519_prod",
            public_path="~/.ssh/id_ed25519_prod.pub",
        )
        conn_a = ClaudeRemoteConnection(
            id="email-engine",
            name="Email Engine",
            launch_profile="claude-remote",
            host="192.0.2.44",
            user="root",
            identity_file_ref="key-prod",
            project_folder="/opt/email_engine",
            claude_options="",
        )
        conn_b = ClaudeRemoteConnection(
            id="rmm-server",
            name="RMM Server",
            launch_profile="claude-remote",
            host="192.0.2.44",
            user="root",
            identity_file_ref="key-prod",
            project_folder="/opt/rmm",
            claude_options="",
        )
        conn_c = ClaudeRemoteConnection(
            id="other-app",
            name="Other App",
            launch_profile="claude-remote",
            host="192.0.2.44",
            user="root",
            identity_file_ref="key-prod",
            project_folder="/opt/other",
            claude_options="",
        )
        doc = CpsmDocument(ssh_keys=[key], connections=[conn_a, conn_b, conn_c])
        gnome = _proc(100, ppid=1, comm="gnome-terminal-")
        ssh = _proc(200, ppid=100, comm="ssh", cmdline=("ssh", "root@192.0.2.44", "claude"))
        svc = DiscoveryService(proc_source=_FakeProcSource([gnome, ssh]))
        sessions = svc.find_outside_sessions(doc)
        assert len(sessions) == 1
        # Ambiguous: 3 candidates, all match host+user — defer to correlation.
        assert sessions[0].suggested_connection_id == ""


# ---------------------------------------------------------------------------
# find_for_connection helper (used by launch interceptor)
# ---------------------------------------------------------------------------


class TestFindForConnection:
    def test_returns_matching_session(self, tmp_path: Path) -> None:
        target = tmp_path / "project"
        target.mkdir()
        doc = _doc_with_local_conn(str(target))
        gnome = _proc(100, ppid=1, comm="gnome-terminal-")
        claude = _proc(
            200,
            ppid=100,
            comm="claude",
            cmdline=("claude",),
            cwd=str(target),
        )
        svc = DiscoveryService(proc_source=_FakeProcSource([gnome, claude]))

        result = svc.find_for_connection(doc, "dotfiles")
        assert result is not None
        assert result.pid == 200

    def test_returns_none_when_no_match(self, tmp_path: Path) -> None:
        doc = _doc_with_local_conn(str(tmp_path / "nope"))
        svc = DiscoveryService(proc_source=_FakeProcSource([]))
        assert svc.find_for_connection(doc, "dotfiles") is None

    def test_empty_connection_id_returns_none(self) -> None:
        svc = DiscoveryService(proc_source=_FakeProcSource([]))
        assert svc.find_for_connection(_empty_doc(), "") is None


# ---------------------------------------------------------------------------
# Platform fallback: non-Linux returns []
# ---------------------------------------------------------------------------


class TestPlatformFallback:
    def test_non_linux_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from cpsm.services import discovery_service as ds

        # Force the production constructor path to take the non-Linux branch.
        monkeypatch.setattr(ds, "_is_linux", lambda: False)
        svc = DiscoveryService()
        assert svc.find_outside_sessions(_empty_doc()) == []

    def test_proc_source_exception_swallowed(self) -> None:
        class _BoomSource:
            def list_processes(self) -> list[ProcInfo]:
                raise RuntimeError("kaboom")

        svc = DiscoveryService(proc_source=_BoomSource())
        # Must not propagate the exception to the caller (UI thread).
        assert svc.find_outside_sessions(_empty_doc()) == []


# ---------------------------------------------------------------------------
# Linux /proc reader smoke test (skipped on non-Linux)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not __import__("sys").platform.startswith("linux"),
    reason="Linux-only: /proc-based smoke test",
)
class TestLinuxProcSourceSmoke:
    def test_reads_real_proc_without_error(self) -> None:
        """Drive the real /proc reader against the test process itself.
        We don't assert on contents; we just want to confirm no exceptions
        propagate from a typical process table."""
        svc = DiscoveryService()
        # Should return without raising. Result may be empty or contain
        # whatever ssh/claude is actually running on the dev box.
        sessions = svc.find_outside_sessions(_empty_doc())
        assert isinstance(sessions, list)
        for s in sessions:
            assert isinstance(s, DiscoveredSession)


# ---------------------------------------------------------------------------
# Adoption helpers (D2): is_pid_alive / send_sigterm / wait_for_pid_exit /
# append_continue_flag
# ---------------------------------------------------------------------------


class TestIsPidAlive:
    def test_self_pid_is_alive(self) -> None:
        assert is_pid_alive(os.getpid())

    def test_definitely_dead_pid(self) -> None:
        # pid -1 / 0 are invalid signal targets; the helper rejects those.
        assert not is_pid_alive(0)
        assert not is_pid_alive(-1)

    def test_obvious_zombie_pid_returns_false(self) -> None:
        # 2**31 - 1 is well above any real pid on Linux.
        assert not is_pid_alive(2_147_483_640)


class TestSendSigterm:
    def test_invalid_pid_returns_false(self) -> None:
        assert not send_sigterm(0)
        assert not send_sigterm(-1)

    def test_dead_pid_returns_false(self) -> None:
        assert not send_sigterm(2_147_483_640)


class TestWaitForPidExit:
    def test_already_dead_returns_immediately(self) -> None:
        # A bogus pid is "already dead" — the helper should bail in 0 polls.
        sleep_calls: list[float] = []
        ok = wait_for_pid_exit(
            2_147_483_640,
            timeout_s=1.0,
            poll_interval_s=0.1,
            sleep=sleep_calls.append,
        )
        assert ok is True
        # is_pid_alive returned False on first check, so no sleep ever ran.
        assert sleep_calls == []

    def test_timeout_when_still_alive(self) -> None:
        sleep_calls: list[float] = []
        # Use the test process itself — it stays alive for the duration.
        ok = wait_for_pid_exit(
            os.getpid(),
            timeout_s=0.4,
            poll_interval_s=0.1,
            sleep=sleep_calls.append,
        )
        assert ok is False
        # ~4 polls expected (4 * 0.1 = 0.4s).
        assert 3 <= len(sleep_calls) <= 5

    def test_invalid_pid_returns_true(self) -> None:
        # pid 0 / negative isn't a real process; treat as already-gone.
        assert wait_for_pid_exit(0, timeout_s=0.1) is True
        assert wait_for_pid_exit(-1, timeout_s=0.1) is True


class TestAppendContinueFlag:
    def test_appends_when_missing(self) -> None:
        assert append_continue_flag("--resume") == "--resume --continue"

    def test_no_op_when_already_present(self) -> None:
        assert append_continue_flag("--continue --verbose") == "--continue --verbose"
        assert append_continue_flag("--resume --continue") == "--resume --continue"

    def test_empty_input(self) -> None:
        assert append_continue_flag("") == "--continue"
        assert append_continue_flag(None) == "--continue"  # type: ignore[arg-type]

    def test_continue_in_middle(self) -> None:
        assert append_continue_flag("--a --continue --b") == "--a --continue --b"

    def test_substring_does_not_count_as_match(self) -> None:
        """A flag like --continue-on-error must NOT be treated as --continue."""
        out = append_continue_flag("--continue-on-error")
        assert out == "--continue-on-error --continue"
