# -*- coding: utf-8 -*-
"""Tests for CorrelationService — disambiguating ambiguous SSH discovered
sessions via remote probing.

We never run real SSH in tests. The probe runner is injected as a fake.
The local /proc walker is exercised via a fake ``proc_root`` directory
populated with synthetic ``fd`` symlinks and a ``net/tcp`` table.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from cpsm.data.schema import (
    ClaudeRemoteConnection,
    CpsmDocument,
    SshKey,
    SshShellConnection,
)
from cpsm.services.correlation_service import (
    CorrelationResult,
    CorrelationService,
    RemoteSession,
    _PROBE_SCRIPT,
    _paths_equal,
    get_local_source_ports,
    parse_probe_home,
    parse_probe_output,
)
from cpsm.services.discovery_service import DiscoveredSession


# ---------------------------------------------------------------------------
# parse_probe_output
# ---------------------------------------------------------------------------


class TestParseProbeOutput:
    def test_typical_two_session_output(self) -> None:
        text = (
            "CPSM_PROBE_START\n"
            "CPSM_PROBE|1234|54321|2345|/opt/foo|claude --resume\n"
            "CPSM_PROBE|1235|54322|2346|/opt/bar|bash\n"
            "CPSM_PROBE_END\n"
        )
        result = parse_probe_output(text)
        assert len(result) == 2
        assert result[0] == RemoteSession(
            sshd_pid=1234, source_port=54321, fg_pid=2345,
            cwd="/opt/foo", cmd="claude --resume",
        )
        assert result[1].source_port == 54322
        assert result[1].cwd == "/opt/bar"

    def test_ignores_login_banner_noise(self) -> None:
        text = (
            "Welcome to Ubuntu 22.04 LTS\n"
            "Last login: ...\n"
            "CPSM_PROBE_START\n"
            "CPSM_PROBE|99|12345|100|/x|claude\n"
            "CPSM_PROBE_END\n"
            "Connection to host closed.\n"
        )
        result = parse_probe_output(text)
        assert len(result) == 1
        assert result[0].source_port == 12345

    def test_malformed_lines_dropped(self) -> None:
        text = (
            "CPSM_PROBE|not-a-pid|54321|2345|/x|claude\n"
            "CPSM_PROBE|1234|abc|2345|/x|claude\n"
            "CPSM_PROBE|1234|54321\n"
            "CPSM_PROBE|1234|54321|2345|/x|claude\n"
        )
        result = parse_probe_output(text)
        assert len(result) == 1
        assert result[0].source_port == 54321

    def test_empty_input(self) -> None:
        assert parse_probe_output("") == []
        assert parse_probe_output("\n\n") == []

    def test_cwd_with_pipe_in_cmd(self) -> None:
        """The cmd field is the last split component, so any extra '|' chars
        are preserved in cmd rather than truncating fields."""
        text = "CPSM_PROBE|1|2|3|/x|bash -c 'a|b'\n"
        result = parse_probe_output(text)
        assert len(result) == 1
        assert result[0].cmd == "bash -c 'a|b'"


class TestParseProbeHome:
    def test_extracts_home(self) -> None:
        text = "CPSM_PROBE_START\nCPSM_PROBE_HOME|/root\nCPSM_PROBE|1|2|3|/x|sh\n"
        assert parse_probe_home(text) == "/root"

    def test_missing_home_returns_empty(self) -> None:
        text = "CPSM_PROBE_START\nCPSM_PROBE|1|2|3|/x|sh\n"
        assert parse_probe_home(text) == ""

    def test_handles_home_with_slashes(self) -> None:
        assert parse_probe_home("CPSM_PROBE_HOME|/var/lib/postgres") == "/var/lib/postgres"


# ---------------------------------------------------------------------------
# get_local_source_ports — fake /proc tree
# ---------------------------------------------------------------------------


def _make_fake_proc(
    tmp: Path,
    *,
    pid_to_inode: dict[int, int],
    inode_to_local_port: dict[int, int],
    state_by_inode: dict[int, str] | None = None,
) -> str:
    """Build a fake /proc tree at *tmp* and return its path.

    Each pid gets a ``fd/0`` symlink to ``socket:[<inode>]``.  ``net/tcp``
    contains one row per inode with the requested local-port and an
    ESTABLISHED state by default.
    """
    state_by_inode = state_by_inode or {}
    proc = tmp
    for pid, inode in pid_to_inode.items():
        fd_dir = proc / str(pid) / "fd"
        fd_dir.mkdir(parents=True, exist_ok=True)
        # symlink target doesn't have to exist
        os.symlink(f"socket:[{inode}]", fd_dir / "0")
    net_dir = proc / "net"
    net_dir.mkdir(parents=True, exist_ok=True)
    rows = ["  sl  local_address rem_address  st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"]
    for inode, port in inode_to_local_port.items():
        state = state_by_inode.get(inode, "01")
        port_hex = f"{port:04X}"
        # local_address: 0100007F:<port_hex>
        # rem_address: anything; we don't check
        rows.append(
            f"  0: 0100007F:{port_hex} 0100007F:0050 {state} 00000000:00000000 "
            f"00:00000000 00000000  1000        0 {inode} 1\n"
        )
    (net_dir / "tcp").write_text("".join(rows))
    return str(proc)


class TestGetLocalSourcePorts:
    def test_returns_pid_to_port_map(self, tmp_path: Path) -> None:
        proc_root = _make_fake_proc(
            tmp_path,
            pid_to_inode={1001: 5001, 1002: 5002},
            inode_to_local_port={5001: 54321, 5002: 54322},
        )
        result = get_local_source_ports([1001, 1002], proc_root=proc_root)
        assert result == {1001: 54321, 1002: 54322}

    def test_skips_pid_not_in_proc(self, tmp_path: Path) -> None:
        proc_root = _make_fake_proc(
            tmp_path,
            pid_to_inode={1001: 5001},
            inode_to_local_port={5001: 54321},
        )
        result = get_local_source_ports([1001, 9999], proc_root=proc_root)
        assert result == {1001: 54321}
        assert 9999 not in result

    def test_skips_non_established_state(self, tmp_path: Path) -> None:
        proc_root = _make_fake_proc(
            tmp_path,
            pid_to_inode={1001: 5001},
            inode_to_local_port={5001: 54321},
            state_by_inode={5001: "0A"},  # LISTEN
        )
        result = get_local_source_ports([1001], proc_root=proc_root)
        assert result == {}

    def test_empty_pid_list(self) -> None:
        assert get_local_source_ports([]) == {}

    def test_missing_proc_root(self, tmp_path: Path) -> None:
        bogus = str(tmp_path / "does-not-exist")
        assert get_local_source_ports([1, 2], proc_root=bogus) == {}


# ---------------------------------------------------------------------------
# CorrelationService — end-to-end with a fake prober and fake /proc
# ---------------------------------------------------------------------------


def _key() -> SshKey:
    return SshKey(
        id="key-prod",
        name="prod",
        type="ed25519",
        private_path="~/.ssh/id_ed25519_prod",
        public_path="~/.ssh/id_ed25519_prod.pub",
    )


def _ssh_conn(cid: str, host: str, *, project_folder: str = "/opt") -> SshShellConnection:
    return SshShellConnection(
        id=cid, name=cid,
        launch_profile="ssh-shell",
        host=host, user="root",
        identity_file_ref="key-prod",
        project_folder=project_folder,
    )


def _disc_ssh(pid: int, host: str, user: str = "root") -> DiscoveredSession:
    return DiscoveredSession(
        pid=pid, kind="ssh-shell",
        cmdline=f"ssh {user}@{host}",
        cwd="", host=host, user=user,
        tty="/dev/pts/0",
        suggested_connection_id="",  # ambiguous from local matching
    )


class TestCorrelateService:
    def test_cwds_by_pid_populated_for_diagnostic_tooltip(
        self, tmp_path: Path
    ) -> None:
        """The cwd map is reported for ALL pids the probe paired (even ones
        that didn't match any Connection.project_folder), so the UI can
        surface the remote cwd in tooltips and the user can see WHY the
        correlator couldn't pick a parent for them."""
        proc_root = _make_fake_proc(
            tmp_path,
            pid_to_inode={2001: 7001, 2002: 7002},
            inode_to_local_port={7001: 50001, 7002: 50002},
        )
        # Two Connections with project_folders that won't match the probe's
        # reported cwds — so by_pid stays empty, but cwds_by_pid is full.
        conn_a = _ssh_conn("conn-a", "10.0.0.1", project_folder="/opt/foo")
        conn_b = _ssh_conn("conn-b", "10.0.0.1", project_folder="/opt/bar")
        doc = CpsmDocument(ssh_keys=[_key()], connections=[conn_a, conn_b])
        sessions = [_disc_ssh(2001, "10.0.0.1"), _disc_ssh(2002, "10.0.0.1")]

        def fake(*_a: Any, **_kw: Any) -> str:
            return (
                "CPSM_PROBE|1|50001|2|/var/log|tail\n"
                "CPSM_PROBE|3|50002|4|/etc|cat\n"
            )
        svc = CorrelationService(ssh_prober=fake, proc_root=proc_root)
        result = svc.correlate(doc, sessions)

        # No Connection-level matches.
        assert result.by_pid == {}
        # But the cwd map is reported so the UI can show "Remote cwd: /var/log".
        assert result.cwds_by_pid == {2001: "/var/log", 2002: "/etc"}

    def test_two_connections_disambiguated_by_cwd(self, tmp_path: Path) -> None:
        """Two Connections to the same host with different project_folders;
        two discovered sessions; correlator pairs each pid to the correct
        connection_id by matching local source-port → remote cwd."""
        # Two local ssh PIDs with distinct source ports.
        proc_root = _make_fake_proc(
            tmp_path,
            pid_to_inode={2001: 7001, 2002: 7002},
            inode_to_local_port={7001: 50001, 7002: 50002},
        )

        # Two Connections to 10.0.0.1, distinct project_folders.
        conn_a = _ssh_conn("conn-a", "10.0.0.1", project_folder="/opt/foo")
        conn_b = _ssh_conn("conn-b", "10.0.0.1", project_folder="/opt/bar")
        doc = CpsmDocument(ssh_keys=[_key()], connections=[conn_a, conn_b])
        sessions = [_disc_ssh(2001, "10.0.0.1"), _disc_ssh(2002, "10.0.0.1")]

        # Fake probe: pid 2001 (port 50001) is /opt/foo; pid 2002 → /opt/bar.
        def fake_prober(host: str, user: str, key: str, port: int) -> str:
            assert host == "10.0.0.1"
            return (
                "CPSM_PROBE|111|50001|222|/opt/foo|claude\n"
                "CPSM_PROBE|112|50002|223|/opt/bar|claude\n"
            )

        svc = CorrelationService(ssh_prober=fake_prober, proc_root=proc_root)
        result = svc.correlate(doc, sessions)

        assert result.by_pid == {2001: "conn-a", 2002: "conn-b"}

    def test_swapped_session_pairing(self, tmp_path: Path) -> None:
        """Same as above but ports map the other way — confirms the
        correlator doesn't accidentally rely on input order."""
        proc_root = _make_fake_proc(
            tmp_path,
            pid_to_inode={2001: 7001, 2002: 7002},
            inode_to_local_port={7001: 50002, 7002: 50001},
        )
        conn_a = _ssh_conn("conn-a", "10.0.0.1", project_folder="/opt/foo")
        conn_b = _ssh_conn("conn-b", "10.0.0.1", project_folder="/opt/bar")
        doc = CpsmDocument(ssh_keys=[_key()], connections=[conn_a, conn_b])
        sessions = [_disc_ssh(2001, "10.0.0.1"), _disc_ssh(2002, "10.0.0.1")]

        def fake_prober(host: str, user: str, key: str, port: int) -> str:
            return (
                "CPSM_PROBE|111|50001|222|/opt/foo|claude\n"
                "CPSM_PROBE|112|50002|223|/opt/bar|claude\n"
            )

        svc = CorrelationService(ssh_prober=fake_prober, proc_root=proc_root)
        result = svc.correlate(doc, sessions)
        # pid 2001 has source-port 50002 (per fake fd) → cwd /opt/bar → conn-b
        # pid 2002 has source-port 50001 → cwd /opt/foo → conn-a
        assert result.by_pid == {2001: "conn-b", 2002: "conn-a"}

    def test_skips_host_with_only_one_connection(self, tmp_path: Path) -> None:
        """No ambiguity → skip probe entirely (saves a network round-trip)."""
        proc_root = _make_fake_proc(
            tmp_path,
            pid_to_inode={2001: 7001},
            inode_to_local_port={7001: 50001},
        )
        conn = _ssh_conn("only", "10.0.0.1", project_folder="/x")
        doc = CpsmDocument(ssh_keys=[_key()], connections=[conn])
        sessions = [_disc_ssh(2001, "10.0.0.1")]

        prober_calls: list[Any] = []
        def fake_prober(*args: Any, **_kw: Any) -> str:
            prober_calls.append(args)
            return ""
        svc = CorrelationService(ssh_prober=fake_prober, proc_root=proc_root)
        result = svc.correlate(doc, sessions)
        assert result.by_pid == {}
        assert prober_calls == []

    def test_probe_failure_yields_empty_mapping(self, tmp_path: Path) -> None:
        proc_root = _make_fake_proc(
            tmp_path,
            pid_to_inode={2001: 7001, 2002: 7002},
            inode_to_local_port={7001: 50001, 7002: 50002},
        )
        conn_a = _ssh_conn("conn-a", "10.0.0.1", project_folder="/opt/foo")
        conn_b = _ssh_conn("conn-b", "10.0.0.1", project_folder="/opt/bar")
        doc = CpsmDocument(ssh_keys=[_key()], connections=[conn_a, conn_b])
        sessions = [_disc_ssh(2001, "10.0.0.1"), _disc_ssh(2002, "10.0.0.1")]

        def boom(*_a: Any, **_kw: Any) -> str:
            raise RuntimeError("ssh failed")
        svc = CorrelationService(ssh_prober=boom, proc_root=proc_root)
        result = svc.correlate(doc, sessions)
        assert result.by_pid == {}

    def test_probe_with_no_matching_cwds_yields_empty(self, tmp_path: Path) -> None:
        proc_root = _make_fake_proc(
            tmp_path,
            pid_to_inode={2001: 7001},
            inode_to_local_port={7001: 50001},
        )
        # Two Connections, but the probe reports a cwd that matches neither.
        conn_a = _ssh_conn("conn-a", "10.0.0.1", project_folder="/opt/foo")
        conn_b = _ssh_conn("conn-b", "10.0.0.1", project_folder="/opt/bar")
        doc = CpsmDocument(ssh_keys=[_key()], connections=[conn_a, conn_b])
        sessions = [_disc_ssh(2001, "10.0.0.1")]

        def fake(*_a: Any, **_kw: Any) -> str:
            return "CPSM_PROBE|1|50001|2|/etc|cat\n"
        svc = CorrelationService(ssh_prober=fake, proc_root=proc_root)
        result = svc.correlate(doc, sessions)
        # No project_folder matched → no mapping for that pid.
        assert result.by_pid == {}

    def test_probe_home_threaded_to_path_compare(
        self, tmp_path: Path
    ) -> None:
        """End-to-end: the probe emits CPSM_PROBE_HOME, the correlator
        captures it, and the path comparison expands ``~/`` against that
        home for a deterministic match — not the loose suffix heuristic."""
        proc_root = _make_fake_proc(
            tmp_path,
            pid_to_inode={2001: 7001},
            inode_to_local_port={7001: 50001},
        )
        # Two Connections to the same host. project_folders use ``~/``
        # against the remote user's home, which the probe will resolve.
        conn_a = _ssh_conn("conn-a", "10.0.0.1", project_folder="~/work/foo")
        conn_b = _ssh_conn("conn-b", "10.0.0.1", project_folder="~/work/bar")
        doc = CpsmDocument(ssh_keys=[_key()], connections=[conn_a, conn_b])
        sessions = [_disc_ssh(2001, "10.0.0.1")]

        # Probe emits HOME=/root; the remote shell sits in /root/work/bar.
        # That path is decidedly NOT under any common /home/<user> location,
        # so this only resolves correctly because we deterministically
        # expand against the captured remote $HOME.
        def fake(*_a: Any, **_kw: Any) -> str:
            return (
                "CPSM_PROBE_START\n"
                "CPSM_PROBE_HOME|/root\n"
                "CPSM_PROBE|1|50001|2|/root/work/bar|claude\n"
                "CPSM_PROBE_END\n"
            )
        svc = CorrelationService(ssh_prober=fake, proc_root=proc_root)
        result = svc.correlate(doc, sessions)
        assert result.by_pid == {2001: "conn-b"}

    def test_correlates_tilde_project_to_remote_root_home(
        self, tmp_path: Path
    ) -> None:
        """Real-world adoption case: Connection.project_folder is
        ``~/projects/foo`` (interpreted on the remote), and the probe
        reports the remote shell's cwd as ``/root/projects/foo``. The
        correlator must still pair them via tilde-suffix matching."""
        proc_root = _make_fake_proc(
            tmp_path,
            pid_to_inode={2001: 7001, 2002: 7002},
            inode_to_local_port={7001: 50001, 7002: 50002},
        )
        conn_a = _ssh_conn("conn-a", "10.0.0.1",
                            project_folder="~/projects/foo")
        conn_b = _ssh_conn("conn-b", "10.0.0.1",
                            project_folder="~/projects/bar")
        doc = CpsmDocument(ssh_keys=[_key()], connections=[conn_a, conn_b])
        sessions = [_disc_ssh(2001, "10.0.0.1"), _disc_ssh(2002, "10.0.0.1")]

        def fake(*_a: Any, **_kw: Any) -> str:
            return (
                "CPSM_PROBE|1|50001|2|/root/projects/foo|claude\n"
                "CPSM_PROBE|3|50002|4|/root/projects/bar|claude\n"
            )
        svc = CorrelationService(ssh_prober=fake, proc_root=proc_root)
        result = svc.correlate(doc, sessions)
        assert result.by_pid == {2001: "conn-a", 2002: "conn-b"}

    def test_handles_tilde_in_project_folder(self, tmp_path: Path) -> None:
        """Connections store paths with ``~`` ("~/projects/x"); probe sees
        the real path. _paths_equal must reconcile via expanduser."""
        # Use a real path under tmp_path so realpath works.
        target = tmp_path / "real"
        target.mkdir()
        proc_root = _make_fake_proc(
            tmp_path / "proc",
            pid_to_inode={2001: 7001, 2002: 7002},
            inode_to_local_port={7001: 50001, 7002: 50002},
        )
        # Connection's project_folder uses an absolute path; probe sees same.
        conn_a = _ssh_conn("conn-a", "10.0.0.1", project_folder=str(target))
        conn_b = _ssh_conn("conn-b", "10.0.0.1", project_folder=str(tmp_path))
        doc = CpsmDocument(ssh_keys=[_key()], connections=[conn_a, conn_b])
        sessions = [_disc_ssh(2001, "10.0.0.1"), _disc_ssh(2002, "10.0.0.1")]

        def fake(*_a: Any, **_kw: Any) -> str:
            return (
                f"CPSM_PROBE|1|50001|2|{target}|claude\n"
                f"CPSM_PROBE|3|50002|4|{tmp_path}|bash\n"
            )
        svc = CorrelationService(ssh_prober=fake, proc_root=proc_root)
        result = svc.correlate(doc, sessions)
        assert result.by_pid == {2001: "conn-a", 2002: "conn-b"}


# ---------------------------------------------------------------------------
# _paths_equal
# ---------------------------------------------------------------------------


class TestPathsEqual:
    def test_exact_match(self) -> None:
        assert _paths_equal("/a/b", "/a/b")

    def test_trailing_slash_differs(self, tmp_path: Path) -> None:
        # realpath normalises trailing slashes only when the path exists,
        # so use tmp_path which does.
        assert _paths_equal(str(tmp_path), str(tmp_path) + "/")

    def test_empty_either_side(self) -> None:
        assert not _paths_equal("", "/x")
        assert not _paths_equal("/x", "")
        assert not _paths_equal("", "")

    def test_distinct_paths(self) -> None:
        assert not _paths_equal("/a/b", "/a/c")

    def test_tilde_local_matches_remote_root_home_via_explicit_home(self) -> None:
        """When the probe captured ``$HOME=/root``, ``~/projects/foo`` is
        compared after deterministic expansion against ``/root``."""
        assert _paths_equal(
            "~/projects/foo", "/root/projects/foo", remote_home="/root",
        )
        assert _paths_equal(
            "/root/projects/foo", "~/projects/foo", remote_home="/root",
        )

    def test_tilde_matches_arbitrary_home_via_explicit_home(self) -> None:
        assert _paths_equal("~/work", "/home/ubuntu/work", remote_home="/home/ubuntu")
        assert _paths_equal("~/work", "/var/lib/somebody/work", remote_home="/var/lib/somebody")

    def test_explicit_home_rejects_overmatch(self) -> None:
        """With the remote $HOME explicit, ``~/work`` no longer matches
        ``/usr/local/work`` because expansion produces a different path."""
        assert not _paths_equal(
            "~/work", "/usr/local/work", remote_home="/root",
        )

    def test_fallback_tilde_match_requires_home_shaped_prefix(self) -> None:
        """Without a remote_home, the comparator falls back to a heuristic
        that requires the suffix to sit immediately after a home-like
        prefix (``/root/...``, ``/home/<x>/...``, etc.) — so ``~/work``
        matches ``/root/work`` but NOT ``/usr/local/work``."""
        # Allowed (home-shaped):
        assert _paths_equal("~/projects/foo", "/root/projects/foo")
        assert _paths_equal("~/projects/foo", "/home/ubuntu/projects/foo")
        assert _paths_equal("~/projects/foo", "/var/lib/postgres/projects/foo")
        assert _paths_equal("~/work", "/Users/me/work")
        # Rejected (not home-shaped — was a false positive in the previous
        # naive endswith-only implementation):
        assert not _paths_equal("~/work", "/usr/local/work")
        assert not _paths_equal("~/work", "/var/log/work")
        assert not _paths_equal("~/work", "/opt/something/work")

    def test_tilde_does_not_overmatch_unrelated_paths(self) -> None:
        # A trailing ``foo`` mid-path should not match ``~/foo``.
        assert not _paths_equal("~/foo", "/some/path/foobar")
        assert not _paths_equal("~/projects/foo", "/some/projects/bar")

    def test_distinct_absolute_paths_still_reject(self) -> None:
        assert not _paths_equal("/opt/foo", "/opt/bar")


# ---------------------------------------------------------------------------
# Probe script sanity (just confirm the constant has the markers)
# ---------------------------------------------------------------------------


def test_probe_script_contains_required_markers() -> None:
    assert "CPSM_PROBE_START" in _PROBE_SCRIPT
    assert "CPSM_PROBE_END" in _PROBE_SCRIPT
    assert "CPSM_PROBE|" in _PROBE_SCRIPT
    # No bashisms — POSIX sh only
    assert "[[ " not in _PROBE_SCRIPT


class TestProbeArgvPinsIdentity:
    """The correlation probe must pin the identity it was given.

    This call site (``_run_probe_via_subprocess``) previously had NO test
    exercising its identity path at all, so a regression that defeated the pin
    here would have been invisible. The builder's own unit tests cannot see a
    caller that puts ``IdentitiesOnly=no`` into its own ssh_options, or that
    bypasses SshBinary entirely — which is precisely the defect this feature
    exists to prevent, so the assertion has to be made against the argv this
    function actually spawns.
    """

    @staticmethod
    def _capture(monkeypatch, key_path: str) -> list[str]:
        import cpsm.services.correlation_service as cs

        captured: list[list[str]] = []

        class _Result:
            stdout = ""
            stderr = ""
            returncode = 0

        def _fake_run(argv, **kwargs):
            captured.append(list(argv))
            return _Result()

        monkeypatch.setattr(cs.subprocess, "run", _fake_run)
        cs._run_probe_via_subprocess(
            host="h", user="u", key_path=key_path, port=22
        )
        # Exercise guard: with no recorded argv every assertion below would be
        # vacuously true.
        assert captured, "the probe never spawned a subprocess"
        return captured[0]

    def test_probe_pins_identity_when_key_given(self, monkeypatch) -> None:
        argv = self._capture(monkeypatch, "/tmp/key")
        assert "-i" in argv, argv
        assert "/tmp/key" in argv, argv
        assert "IdentitiesOnly=yes" in argv, argv
        assert "IdentitiesOnly=no" not in argv, argv

    def test_probe_does_not_pin_without_a_key(self, monkeypatch) -> None:
        """No key means no -i, so the pin must be absent.

        IdentitiesOnly=yes with no -i restricts ssh to the default identity
        files and disables agent-held keys.
        """
        argv = self._capture(monkeypatch, "")
        assert "-i" not in argv, argv
        assert not any("IdentitiesOnly" in a for a in argv), argv
