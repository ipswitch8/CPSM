# -*- coding: utf-8 -*-
"""Tests for SessionService — single, group, and scene launch flows.

Key scenarios:
  - Single launch: claude-remote, claude-local, ssh-shell, local-shell, custom.
  - Local-profile guard: blocks ssh/scp/plink in rendered template.
  - Group launch: sequential vs parallel, null-id placeholder panes.
  - Scene launch: on_conflict=error/first-wins/last-wins.
  - session_name() isolation modes.

Coverage target: ≥ 90%
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from cpsm.data.schema import (
    ClaudeLocalConnection,
    ClaudeRemoteConnection,
    CpsmDocument,
    CustomConnection,
    GeometryPct,
    Group,
    LaunchTemplate,
    LocalShellConnection,
    Monitor,
    Pane,
    Scene,
    ScreenLayout,
    SshKey,
    SshShellConnection,
    Viewport,
)
from cpsm.platform.base import MultiplexerBackend
from cpsm.services.config_service import ConfigService
from cpsm.services.layout_service import LayoutService
from cpsm.services.session_service import (
    LayoutConflictError,
    LocalProfileLeakError,
    SessionService,
    _check_local_profile_guard,
)
from cpsm.services.template_service import TemplateService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_backend() -> MagicMock:
    backend = MagicMock(spec=MultiplexerBackend)
    backend.list_sessions.return_value = []
    backend.new_session.return_value = MagicMock(name="session", id="$0")
    backend.capture_layout.return_value = "abc123,220x50,0,0"
    return backend


def _mock_templates(rendered: str = "#!/bin/bash\necho hello\n") -> MagicMock:
    tpl = MagicMock(spec=TemplateService)
    tpl.render.return_value = rendered
    tpl.render_placeholder.return_value = "#!/bin/bash\necho placeholder\n"
    return tpl


def _mock_config_service(doc: CpsmDocument) -> MagicMock:
    svc = MagicMock(spec=ConfigService)
    svc.find_connection.side_effect = lambda d, cid: next(
        (c for c in d.connections if c.id == cid), None
    )
    svc.find_group.side_effect = lambda d, gid: next((g for g in d.groups if g.id == gid), None)
    svc.find_scene.side_effect = lambda d, sid: next((s for s in d.scenes if s.id == sid), None)
    svc.find_layout.side_effect = lambda d, lid: next(
        (ly for ly in d.screen_layouts if ly.id == lid), None
    )
    return svc


def _mock_layout_service() -> MagicMock:
    return MagicMock(spec=LayoutService)


def _make_service(
    doc: CpsmDocument,
    backend: MagicMock | None = None,
    templates: MagicMock | None = None,
    tmp_dir: Path | None = None,
) -> SessionService:
    config = _mock_config_service(doc)
    backend = backend or _mock_backend()
    templates = templates or _mock_templates()
    layout = _mock_layout_service()
    return SessionService(config, backend, templates, layout, launcher_tmp_dir=tmp_dir)


# ---------------------------------------------------------------------------
# Document fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def remote_conn() -> ClaudeRemoteConnection:
    return ClaudeRemoteConnection(
        id="web01",
        launch_profile="claude-remote",
        host="example.com",
        port=22,
        user="ubuntu",
        identity_file_ref="key-test",
        project_folder="/opt/app",
        claude_options="--resume",
    )


@pytest.fixture
def local_conn() -> ClaudeLocalConnection:
    return ClaudeLocalConnection(
        id="dotfiles",
        launch_profile="claude-local",
        project_folder="~/projects/dotfiles",
        claude_options="--resume",
    )


@pytest.fixture
def ssh_shell_conn() -> SshShellConnection:
    return SshShellConnection(
        id="bastion",
        launch_profile="ssh-shell",
        host="bastion.example.com",
        user="admin",
        identity_file_ref="key-test",
    )


@pytest.fixture
def local_shell_conn() -> LocalShellConnection:
    return LocalShellConnection(
        id="scratch",
        launch_profile="local-shell",
        project_folder="~/scratch",
    )


@pytest.fixture
def custom_conn() -> CustomConnection:
    return CustomConnection(
        id="nspawn-dev",
        launch_profile="custom",
        custom_template_id="tpl-nspawn",
        project_folder="/var/lib/machines/dev",
    )


@pytest.fixture
def doc_remote(remote_conn: ClaudeRemoteConnection) -> CpsmDocument:
    key = SshKey(
        id="key-test",
        name="Test",
        type="ed25519",
        private_path="/home/user/.ssh/id_ed25519",
        public_path="/home/user/.ssh/id_ed25519.pub",
    )
    return CpsmDocument(ssh_keys=[key], connections=[remote_conn])


@pytest.fixture
def doc_local(local_conn: ClaudeLocalConnection) -> CpsmDocument:
    return CpsmDocument(connections=[local_conn])


@pytest.fixture
def doc_all(
    remote_conn: ClaudeRemoteConnection,
    local_conn: ClaudeLocalConnection,
    ssh_shell_conn: SshShellConnection,
    local_shell_conn: LocalShellConnection,
    custom_conn: CustomConnection,
) -> CpsmDocument:
    key = SshKey(
        id="key-test",
        name="Test",
        type="ed25519",
        private_path="/home/user/.ssh/id_ed25519",
        public_path="/home/user/.ssh/id_ed25519.pub",
    )
    tpl = LaunchTemplate(id="tpl-nspawn", bash="echo hello")
    return CpsmDocument(
        ssh_keys=[key],
        connections=[remote_conn, local_conn, ssh_shell_conn, local_shell_conn, custom_conn],
        launch_templates=[tpl],
    )


# ---------------------------------------------------------------------------
# session_name()
# ---------------------------------------------------------------------------


class TestSessionName:
    def test_shared_session_name(self, doc_remote: CpsmDocument, tmp_path: Path) -> None:
        svc = _make_service(doc_remote, tmp_dir=tmp_path)
        assert svc.session_name("web01") == "cpsm-web01"

    def test_per_group_session_name(self, doc_remote: CpsmDocument, tmp_path: Path) -> None:
        svc = _make_service(doc_remote, tmp_dir=tmp_path)
        assert svc.session_name("web01", "grp-1") == "cpsm-grp-1-web01"


# ---------------------------------------------------------------------------
# launch() — single connection
# ---------------------------------------------------------------------------


class TestLaunchSingleRemote:
    def test_launch_remote_creates_session(self, doc_remote: CpsmDocument, tmp_path: Path) -> None:
        backend = _mock_backend()
        templates = _mock_templates()
        svc = _make_service(doc_remote, backend=backend, templates=templates, tmp_dir=tmp_path)
        result = svc.launch(doc_remote, "web01")
        assert result.success
        assert result.session_name == "cpsm-web01"

    def test_launch_remote_calls_render(self, doc_remote: CpsmDocument, tmp_path: Path) -> None:
        templates = _mock_templates()
        svc = _make_service(doc_remote, templates=templates, tmp_dir=tmp_path)
        svc.launch(doc_remote, "web01")
        templates.render.assert_called_once()

    def test_launch_remote_calls_respawn_pane(
        self, doc_remote: CpsmDocument, tmp_path: Path
    ) -> None:
        backend = _mock_backend()
        svc = _make_service(doc_remote, backend=backend, tmp_dir=tmp_path)
        svc.launch(doc_remote, "web01")
        backend.respawn_pane.assert_called_once()
        _pane_target, cmd = backend.respawn_pane.call_args.args
        assert "bash" in cmd


class TestLaunchSingleLocal:
    def test_launch_local_succeeds(self, doc_local: CpsmDocument, tmp_path: Path) -> None:
        svc = _make_service(doc_local, tmp_dir=tmp_path)
        result = svc.launch(doc_local, "dotfiles")
        assert result.success

    def test_launch_local_calls_render_with_claude_local_profile(
        self, doc_local: CpsmDocument, tmp_path: Path
    ) -> None:
        templates = _mock_templates()
        svc = _make_service(doc_local, templates=templates, tmp_dir=tmp_path)
        svc.launch(doc_local, "dotfiles")
        call_args = templates.render.call_args
        assert call_args.args[0] == "claude-local"


class TestLaunchSingleSshShell:
    def test_launch_ssh_shell_succeeds(self, doc_all: CpsmDocument, tmp_path: Path) -> None:
        svc = _make_service(doc_all, tmp_dir=tmp_path)
        result = svc.launch(doc_all, "bastion")
        assert result.success


class TestLaunchSingleLocalShell:
    def test_launch_local_shell_succeeds(self, doc_all: CpsmDocument, tmp_path: Path) -> None:
        svc = _make_service(doc_all, tmp_dir=tmp_path)
        result = svc.launch(doc_all, "scratch")
        assert result.success


class TestLaunchSingleCustom:
    def test_launch_custom_succeeds(self, doc_all: CpsmDocument, tmp_path: Path) -> None:
        svc = _make_service(doc_all, tmp_dir=tmp_path)
        result = svc.launch(doc_all, "nspawn-dev")
        assert result.success


class TestLaunchMissingConnection:
    def test_launch_missing_connection_returns_failure(
        self, doc_local: CpsmDocument, tmp_path: Path
    ) -> None:
        svc = _make_service(doc_local, tmp_dir=tmp_path)
        result = svc.launch(doc_local, "nonexistent")
        assert not result.success
        assert result.errors


# ---------------------------------------------------------------------------
# Local-profile guard (§9.2)
# ---------------------------------------------------------------------------


class TestLocalProfileGuard:
    def test_claude_local_with_clean_template_passes(
        self, doc_local: CpsmDocument, tmp_path: Path
    ) -> None:
        """Clean local template (no ssh/scp/plink) must succeed."""
        templates = _mock_templates("#!/bin/bash\ncd ~/projects && claude --resume\n")
        svc = _make_service(doc_local, templates=templates, tmp_dir=tmp_path)
        result = svc.launch(doc_local, "dotfiles")
        assert result.success

    def test_claude_local_with_ssh_in_template_raises(
        self, doc_local: CpsmDocument, tmp_path: Path
    ) -> None:
        """If a malicious template renders ssh for claude-local, raise LocalProfileLeakError."""
        malicious = "#!/bin/bash\nssh user@host 'claude --resume'\n"
        templates = _mock_templates(malicious)
        svc = _make_service(doc_local, templates=templates, tmp_dir=tmp_path)
        with pytest.raises(LocalProfileLeakError):
            svc.launch(doc_local, "dotfiles")

    def test_local_shell_with_scp_in_template_raises(
        self,
        doc_all: CpsmDocument,
        tmp_path: Path,
        local_shell_conn: LocalShellConnection,
    ) -> None:
        malicious = "#!/bin/bash\nscp file.txt user@host:/path/\n"
        templates = _mock_templates(malicious)
        svc = _make_service(doc_all, templates=templates, tmp_dir=tmp_path)
        with pytest.raises(LocalProfileLeakError):
            svc.launch(doc_all, "scratch")

    def test_local_shell_with_plink_in_template_raises(
        self,
        doc_all: CpsmDocument,
        tmp_path: Path,
    ) -> None:
        malicious = "#!/bin/bash\nplink user@host\n"
        templates = _mock_templates(malicious)
        svc = _make_service(doc_all, templates=templates, tmp_dir=tmp_path)
        with pytest.raises(LocalProfileLeakError):
            svc.launch(doc_all, "scratch")

    def test_remote_profile_with_ssh_in_template_is_fine(
        self, doc_remote: CpsmDocument, tmp_path: Path
    ) -> None:
        """claude-remote is allowed to contain ssh (it IS an ssh profile)."""
        ssh_content = "#!/bin/bash\nssh -tt ubuntu@example.com 'bash /tmp/launcher.sh'\n"
        templates = _mock_templates(ssh_content)
        svc = _make_service(doc_remote, templates=templates, tmp_dir=tmp_path)
        result = svc.launch(doc_remote, "web01")
        assert result.success

    def test_guard_function_directly_with_inline_ssh(self) -> None:
        """Direct guard function test for all forbidden patterns."""
        _check_local_profile_guard("#!/bin/bash\ncd /foo && claude", "claude-local", "x")

    def test_guard_rejects_ssh_at_line_start(self) -> None:
        with pytest.raises(LocalProfileLeakError):
            _check_local_profile_guard("ssh user@host", "claude-local", "x")

    def test_guard_rejects_ssh_after_semicolon(self) -> None:
        with pytest.raises(LocalProfileLeakError):
            _check_local_profile_guard("cd /foo; ssh user@host", "claude-local", "x")

    def test_guard_rejects_scp(self) -> None:
        with pytest.raises(LocalProfileLeakError):
            _check_local_profile_guard("scp file user@host:/path", "local-shell", "x")

    def test_guard_allows_sshd(self) -> None:
        """'sshd' should not trigger the guard (not a client binary)."""
        # 'sshd' does not match '\bssh\b' at a spawn boundary
        # Actually our regex matches 'ssh' followed by word boundary, so
        # 'sshd' = 'ssh' + 'd' — 'd' means no word boundary after 'ssh' before 'd'
        # The regex: (?:^|[\s;|&`(])(?:ssh|scp|plink)\b
        # 'sshd' → 'ssh' followed by 'd' → \b between 'h' and 'd' is NOT a boundary
        # (both are word chars). So this should not match.
        _check_local_profile_guard("sshd -t", "claude-local", "x")  # should not raise


# ---------------------------------------------------------------------------
# launch_group() — sequential
# ---------------------------------------------------------------------------


@pytest.fixture
def doc_group(
    remote_conn: ClaudeRemoteConnection,
    local_conn: ClaudeLocalConnection,
) -> CpsmDocument:
    key = SshKey(
        id="key-test",
        name="Test",
        type="ed25519",
        private_path="/home/user/.ssh/id_ed25519",
        public_path="/home/user/.ssh/id_ed25519.pub",
    )
    grp = Group(
        id="grp-1",
        name="Group 1",
        members=["web01", "dotfiles"],
        launch_order="sequential",
        launch_delay_ms=0,
    )
    return CpsmDocument(
        ssh_keys=[key],
        connections=[remote_conn, local_conn],
        groups=[grp],
    )


@pytest.fixture
def doc_group_parallel(
    remote_conn: ClaudeRemoteConnection,
    local_conn: ClaudeLocalConnection,
) -> CpsmDocument:
    key = SshKey(
        id="key-test",
        name="Test",
        type="ed25519",
        private_path="/home/user/.ssh/id_ed25519",
        public_path="/home/user/.ssh/id_ed25519.pub",
    )
    grp = Group(
        id="grp-parallel",
        name="Parallel Group",
        members=["web01", "dotfiles"],
        launch_order="parallel",
        launch_delay_ms=0,
    )
    return CpsmDocument(
        ssh_keys=[key],
        connections=[remote_conn, local_conn],
        groups=[grp],
    )


class TestLaunchGroupSequential:
    def test_launch_group_sequential_success(self, doc_group: CpsmDocument, tmp_path: Path) -> None:
        svc = _make_service(doc_group, tmp_dir=tmp_path)
        result = svc.launch_group(doc_group, "grp-1")
        assert result.success
        assert result.group_id == "grp-1"
        assert len(result.member_results) == 2

    def test_launch_group_all_members_launched(
        self, doc_group: CpsmDocument, tmp_path: Path
    ) -> None:
        svc = _make_service(doc_group, tmp_dir=tmp_path)
        result = svc.launch_group(doc_group, "grp-1")
        launched_ids = {r.connection_id for r in result.member_results}
        assert launched_ids == {"web01", "dotfiles"}

    def test_launch_group_missing_group_returns_failure(
        self, doc_group: CpsmDocument, tmp_path: Path
    ) -> None:
        svc = _make_service(doc_group, tmp_dir=tmp_path)
        result = svc.launch_group(doc_group, "nope")
        assert not result.success
        assert result.errors


class TestLaunchGroupParallel:
    def test_launch_group_parallel_all_members_launched(
        self, doc_group_parallel: CpsmDocument, tmp_path: Path
    ) -> None:
        svc = _make_service(doc_group_parallel, tmp_dir=tmp_path)
        result = svc.launch_group(doc_group_parallel, "grp-parallel")
        assert result.success
        assert len(result.member_results) == 2


class TestLaunchGroupDelay:
    def test_launch_group_sequential_with_delay(
        self,
        remote_conn: ClaudeRemoteConnection,
        local_conn: ClaudeLocalConnection,
        tmp_path: Path,
    ) -> None:
        """Verify launch_delay_ms causes delay between members."""
        key = SshKey(
            id="key-test",
            name="Test",
            type="ed25519",
            private_path="/home/user/.ssh/id_ed25519",
            public_path="/home/user/.ssh/id_ed25519.pub",
        )
        grp = Group(
            id="grp-delay",
            name="Delay Group",
            members=["web01", "dotfiles"],
            launch_order="sequential",
            launch_delay_ms=50,
        )
        doc = CpsmDocument(
            ssh_keys=[key],
            connections=[remote_conn, local_conn],
            groups=[grp],
        )
        start = time.monotonic()
        svc = _make_service(doc, tmp_dir=tmp_path)
        result = svc.launch_group(doc, "grp-delay")
        elapsed = time.monotonic() - start
        assert result.success
        # Should have slept at least 50ms between the two members
        assert elapsed >= 0.04


class TestLaunchGroupNullPanes:
    def test_launch_group_with_null_pane_in_layout(
        self,
        remote_conn: ClaudeRemoteConnection,
        local_conn: ClaudeLocalConnection,
        tmp_path: Path,
    ) -> None:
        """Null-id panes are handled — placeholder is respawned into the
        pane. The launch succeeds, with one LaunchResult per non-null pane
        (the null pane uses a placeholder, no LaunchResult).

        The Phase A layout-aware path actually performs the placeholder
        respawn now (instead of deferring with a warning), so the previous
        'warnings populated' assertion is replaced.
        """
        key = SshKey(
            id="key-test",
            name="Test",
            type="ed25519",
            private_path="/home/user/.ssh/id_ed25519",
            public_path="/home/user/.ssh/id_ed25519.pub",
        )
        vp = Viewport(
            id="vp-1",
            geometry_pct=GeometryPct(x=0, y=0, w=100, h=100),
            panes=[Pane(connection_id="web01"), Pane(connection_id=None)],  # null pane
        )
        monitor = Monitor(viewports=[vp])
        layout = ScreenLayout(id="ly-1", name="Layout", monitors=[monitor])
        grp = Group(
            id="grp-1",
            name="G",
            members=["web01"],
            default_layout_id="ly-1",
        )
        doc = CpsmDocument(
            ssh_keys=[key],
            connections=[remote_conn, local_conn],
            groups=[grp],
            screen_layouts=[layout],
        )
        svc = _make_service(doc, tmp_dir=tmp_path)
        result = svc.launch_group(doc, "grp-1")
        assert result.success
        # Exactly one LaunchResult for the non-null pane
        assert len(result.member_results) == 1
        assert result.member_results[0].connection_id == "web01"


# ---------------------------------------------------------------------------
# launch_scene() — on_conflict modes
# ---------------------------------------------------------------------------


@pytest.fixture
def doc_scene() -> CpsmDocument:
    """Document with two groups sharing a viewport id in separate layouts."""
    key = SshKey(
        id="key-test",
        name="Test",
        type="ed25519",
        private_path="/home/user/.ssh/id_ed25519",
        public_path="/home/user/.ssh/id_ed25519.pub",
    )
    conn1 = ClaudeRemoteConnection(
        id="web01",
        launch_profile="claude-remote",
        host="a.com",
        port=22,
        user="ubuntu",
        identity_file_ref="key-test",
        project_folder="/opt/app",
        claude_options="--resume",
    )
    conn2 = ClaudeLocalConnection(
        id="dotfiles",
        launch_profile="claude-local",
        project_folder="~/projects/dotfiles",
        claude_options="--resume",
    )
    # Two layouts that share the same (monitor identifier, viewport id)
    vp1 = Viewport(id="shared-vp", geometry_pct=GeometryPct(x=0, y=0, w=100, h=100))
    vp2 = Viewport(id="shared-vp", geometry_pct=GeometryPct(x=0, y=0, w=100, h=100))
    mon1 = Monitor(identifier="monitor-1", viewports=[vp1])
    mon2 = Monitor(identifier="monitor-1", viewports=[vp2])
    layout1 = ScreenLayout(id="ly-1", name="L1", monitors=[mon1])
    layout2 = ScreenLayout(id="ly-2", name="L2", monitors=[mon2])
    grp1 = Group(id="grp-1", name="G1", members=["web01"], default_layout_id="ly-1")
    grp2 = Group(id="grp-2", name="G2", members=["dotfiles"], default_layout_id="ly-2")
    return CpsmDocument(
        ssh_keys=[key],
        connections=[conn1, conn2],
        groups=[grp1, grp2],
        screen_layouts=[layout1, layout2],
    )


class TestLaunchSceneConflictError:
    def test_raises_layout_conflict_error(self, doc_scene: CpsmDocument, tmp_path: Path) -> None:
        scene = Scene(id="sc-1", groups=["grp-1", "grp-2"], on_conflict="error")
        doc = doc_scene.model_copy(update={"scenes": [scene]})
        svc = _make_service(doc, tmp_dir=tmp_path)
        with pytest.raises(LayoutConflictError):
            svc.launch_scene(doc, "sc-1")


class TestLaunchSceneFirstWins:
    def test_first_wins_skips_later_conflicting_group(
        self, doc_scene: CpsmDocument, tmp_path: Path
    ) -> None:
        scene = Scene(id="sc-2", groups=["grp-1", "grp-2"], on_conflict="first-wins")
        doc = doc_scene.model_copy(update={"scenes": [scene]})
        svc = _make_service(doc, tmp_dir=tmp_path)
        result = svc.launch_scene(doc, "sc-2")
        assert result.success
        # Only 1 group should have been launched (grp-2 was skipped)
        assert len(result.group_results) == 1
        assert result.group_results[0].group_id == "grp-1"
        assert any("first-wins" in w.lower() for w in result.warnings)

    def test_first_wins_produces_warning(self, doc_scene: CpsmDocument, tmp_path: Path) -> None:
        scene = Scene(id="sc-2", groups=["grp-1", "grp-2"], on_conflict="first-wins")
        doc = doc_scene.model_copy(update={"scenes": [scene]})
        svc = _make_service(doc, tmp_dir=tmp_path)
        result = svc.launch_scene(doc, "sc-2")
        assert result.warnings


class TestLaunchSceneLastWins:
    def test_last_wins_launches_all_groups(self, doc_scene: CpsmDocument, tmp_path: Path) -> None:
        scene = Scene(id="sc-3", groups=["grp-1", "grp-2"], on_conflict="last-wins")
        doc = doc_scene.model_copy(update={"scenes": [scene]})
        svc = _make_service(doc, tmp_dir=tmp_path)
        result = svc.launch_scene(doc, "sc-3")
        assert result.success
        assert len(result.group_results) == 2

    def test_last_wins_produces_warning(self, doc_scene: CpsmDocument, tmp_path: Path) -> None:
        scene = Scene(id="sc-3", groups=["grp-1", "grp-2"], on_conflict="last-wins")
        doc = doc_scene.model_copy(update={"scenes": [scene]})
        svc = _make_service(doc, tmp_dir=tmp_path)
        result = svc.launch_scene(doc, "sc-3")
        assert any("last-wins" in w.lower() for w in result.warnings)


class TestLaunchSceneNoConflict:
    def test_scene_without_conflict_launches_all_groups(
        self,
        tmp_path: Path,
    ) -> None:
        key = SshKey(
            id="key-test",
            name="Test",
            type="ed25519",
            private_path="/home/user/.ssh/id_ed25519",
            public_path="/home/user/.ssh/id_ed25519.pub",
        )
        conn1 = ClaudeRemoteConnection(
            id="web01",
            launch_profile="claude-remote",
            host="a.com",
            port=22,
            user="ubuntu",
            identity_file_ref="key-test",
            project_folder="/opt/app",
            claude_options="--resume",
        )
        conn2 = ClaudeLocalConnection(
            id="dotfiles",
            launch_profile="claude-local",
            project_folder="~/projects/dotfiles",
            claude_options="--resume",
        )
        grp1 = Group(id="grp-1", name="G1", members=["web01"])
        grp2 = Group(id="grp-2", name="G2", members=["dotfiles"])
        scene = Scene(id="sc-ok", groups=["grp-1", "grp-2"], on_conflict="error")
        doc = CpsmDocument(
            ssh_keys=[key],
            connections=[conn1, conn2],
            groups=[grp1, grp2],
            scenes=[scene],
        )
        svc = _make_service(doc, tmp_dir=tmp_path)
        result = svc.launch_scene(doc, "sc-ok")
        assert result.success
        assert len(result.group_results) == 2


class TestLaunchSceneMissing:
    def test_launch_missing_scene_returns_failure(self, tmp_path: Path) -> None:
        doc = CpsmDocument()
        svc = _make_service(doc, tmp_dir=tmp_path)
        result = svc.launch_scene(doc, "nope")
        assert not result.success
        assert result.errors


# ---------------------------------------------------------------------------
# kill_session / close
# ---------------------------------------------------------------------------


class TestKillSession:
    def test_kill_session_calls_backend(self, doc_local: CpsmDocument, tmp_path: Path) -> None:
        backend = _mock_backend()
        svc = _make_service(doc_local, backend=backend, tmp_dir=tmp_path)
        svc.kill_session("cpsm-dotfiles")
        backend.kill_session.assert_called_once_with("cpsm-dotfiles")

    def test_kill_session_logs_warning_on_error(
        self, doc_local: CpsmDocument, tmp_path: Path
    ) -> None:
        backend = _mock_backend()
        backend.kill_session.side_effect = RuntimeError("session not found")
        svc = _make_service(doc_local, backend=backend, tmp_dir=tmp_path)
        # Should not raise
        svc.kill_session("nonexistent")

    def test_close_kills_shared_session(self, doc_local: CpsmDocument, tmp_path: Path) -> None:
        backend = _mock_backend()
        svc = _make_service(doc_local, backend=backend, tmp_dir=tmp_path)
        svc.close(doc_local, "dotfiles")
        backend.kill_session.assert_called_once_with("cpsm-dotfiles")


# ---------------------------------------------------------------------------
# Launcher tmpfile
# ---------------------------------------------------------------------------


class TestLauncherTmpfile:
    def test_tmpfile_written_to_tmp_dir(self, doc_remote: CpsmDocument, tmp_path: Path) -> None:
        backend = _mock_backend()
        svc = _make_service(doc_remote, backend=backend, tmp_dir=tmp_path)
        svc.launch(doc_remote, "web01")
        # respawn_pane should have been called with a path inside tmp_path
        call_args = backend.respawn_pane.call_args
        cmd = call_args.args[1]
        assert str(tmp_path) in cmd

    def test_tmpfile_mode_0700_on_linux(self, doc_remote: CpsmDocument, tmp_path: Path) -> None:
        import os
        import sys

        if sys.platform == "win32":
            pytest.skip("Linux-only permission test")
        backend = _mock_backend()
        svc = _make_service(doc_remote, backend=backend, tmp_dir=tmp_path)
        svc.launch(doc_remote, "web01")
        call_args = backend.respawn_pane.call_args
        cmd = call_args.args[1]
        # Extract path from "bash /path/to/file.sh"
        tmpfile_path = cmd.split("bash ", 1)[1]
        mode = os.stat(tmpfile_path).st_mode & 0o777
        assert mode == 0o700, f"Expected 0700, got {oct(mode)}"

    def test_tmpfile_written_without_tmp_dir(self, doc_remote: CpsmDocument) -> None:
        """Test the fallback when no tmp_dir is given (uses system temp)."""
        backend = _mock_backend()
        # No tmp_dir — uses system tempdir
        svc = _make_service(doc_remote, backend=backend, tmp_dir=None)
        result = svc.launch(doc_remote, "web01")
        assert result.success
        backend.respawn_pane.assert_called_once()


# ---------------------------------------------------------------------------
# Additional coverage: per-group isolation, existing_pane, error paths
# ---------------------------------------------------------------------------


class TestLaunchWithExistingPane:
    def test_launch_with_existing_pane_respawns(
        self, doc_remote: CpsmDocument, tmp_path: Path
    ) -> None:
        """When existing_pane is provided, respawn into it instead of creating session."""
        backend = _mock_backend()
        svc = _make_service(doc_remote, backend=backend, tmp_dir=tmp_path)
        result = svc.launch(doc_remote, "web01", existing_pane="session:0.3")
        assert result.success
        # Should NOT create a new session
        backend.new_session.assert_not_called()
        # Should respawn into the given pane
        backend.respawn_pane.assert_called_once()
        pane_arg = backend.respawn_pane.call_args.args[0]
        assert pane_arg == "session:0.3"


class TestLaunchPerGroupIsolation:
    def test_launch_with_per_group_isolation(
        self,
        remote_conn: ClaudeRemoteConnection,
        tmp_path: Path,
    ) -> None:
        """When group has isolation=per-group, session name includes group id."""
        key = SshKey(
            id="key-test",
            name="T",
            type="ed25519",
            private_path="/home/user/.ssh/id_ed25519",
            public_path="/home/user/.ssh/id_ed25519.pub",
        )
        grp = Group(id="grp-pg", name="Per-Group", members=["web01"], isolation="per-group")
        doc = CpsmDocument(ssh_keys=[key], connections=[remote_conn], groups=[grp])
        backend = _mock_backend()
        svc = _make_service(doc, backend=backend, tmp_dir=tmp_path)
        result = svc.launch(doc, "web01", group_id="grp-pg")
        assert result.success
        assert "grp-pg" in result.session_name

    def test_launch_with_shared_isolation_no_group_in_name(
        self,
        remote_conn: ClaudeRemoteConnection,
        tmp_path: Path,
    ) -> None:
        """When group has isolation=shared (default), session name omits group id."""
        key = SshKey(
            id="key-test",
            name="T",
            type="ed25519",
            private_path="/home/user/.ssh/id_ed25519",
            public_path="/home/user/.ssh/id_ed25519.pub",
        )
        grp = Group(id="grp-sh", name="Shared", members=["web01"], isolation="shared")
        doc = CpsmDocument(ssh_keys=[key], connections=[remote_conn], groups=[grp])
        backend = _mock_backend()
        svc = _make_service(doc, backend=backend, tmp_dir=tmp_path)
        result = svc.launch(doc, "web01", group_id="grp-sh")
        assert result.success
        assert "grp-sh" not in result.session_name


class TestLaunchExceptionPath:
    def test_launch_catches_backend_exception(
        self, doc_remote: CpsmDocument, tmp_path: Path
    ) -> None:
        """Exceptions in respawn_pane are caught and returned as errors."""
        backend = _mock_backend()
        backend.respawn_pane.side_effect = RuntimeError("tmux died")
        svc = _make_service(doc_remote, backend=backend, tmp_dir=tmp_path)
        result = svc.launch(doc_remote, "web01")
        assert not result.success
        assert "tmux died" in result.errors[0]


class TestSessionReuse:
    def test_launch_reuses_existing_session(self, doc_remote: CpsmDocument, tmp_path: Path) -> None:
        """When session already exists, new_session is NOT called."""
        backend = _mock_backend()
        # Simulate existing session
        existing_session = MagicMock()
        existing_session.name = "cpsm-web01"
        backend.list_sessions.return_value = [existing_session]
        svc = _make_service(doc_remote, backend=backend, tmp_dir=tmp_path)
        result = svc.launch(doc_remote, "web01")
        assert result.success
        backend.new_session.assert_not_called()


class TestSceneMissingGroups:
    def test_launch_scene_missing_group_returns_failure(self, tmp_path: Path) -> None:
        """Scene referencing a non-existent group returns failure without launching.

        We build a valid document (so pydantic passes) then mock find_group to
        return None, simulating a group that was deleted after the scene was built.
        """
        key = SshKey(
            id="key-test",
            name="T",
            type="ed25519",
            private_path="/home/user/.ssh/id_ed25519",
            public_path="/home/user/.ssh/id_ed25519.pub",
        )
        conn = ClaudeLocalConnection(
            id="dotfiles",
            launch_profile="claude-local",
            project_folder="~/projects",
            claude_options="--resume",
        )
        grp = Group(id="grp-valid", name="G", members=["dotfiles"])
        scene = Scene(id="sc-bad", groups=["grp-valid"], on_conflict="error")
        doc = CpsmDocument(
            ssh_keys=[key],
            connections=[conn],
            groups=[grp],
            scenes=[scene],
        )
        svc = _make_service(doc, tmp_dir=tmp_path)
        # Force find_group to return None (as if the group doesn't exist at runtime)
        svc._config.find_group.side_effect = lambda d, gid: None
        result = svc.launch_scene(doc, "sc-bad")
        assert not result.success
        assert result.errors


class TestGroupPlaceholderFailure:
    def test_launch_group_placeholder_failure_becomes_warning(
        self,
        remote_conn: ClaudeRemoteConnection,
        tmp_path: Path,
    ) -> None:
        """If placeholder tmpfile creation fails, it becomes a warning, not an exception."""
        key = SshKey(
            id="key-test",
            name="T",
            type="ed25519",
            private_path="/home/user/.ssh/id_ed25519",
            public_path="/home/user/.ssh/id_ed25519.pub",
        )
        vp = Viewport(
            id="vp-1",
            geometry_pct=GeometryPct(x=0, y=0, w=100, h=100),
            panes=[Pane(connection_id=None)],
        )
        monitor = Monitor(viewports=[vp])
        layout = ScreenLayout(id="ly-1", name="L", monitors=[monitor])
        grp = Group(id="grp-1", name="G", members=["web01"], default_layout_id="ly-1")
        doc = CpsmDocument(
            ssh_keys=[key],
            connections=[remote_conn],
            groups=[grp],
            screen_layouts=[layout],
        )
        # Make render_placeholder raise
        templates = _mock_templates()
        templates.render_placeholder.side_effect = RuntimeError("disk full")
        svc = _make_service(doc, templates=templates, tmp_dir=tmp_path)
        result = svc.launch_group(doc, "grp-1")
        # Should still succeed for the main members; placeholder failure is a warning
        assert any("failed" in w.lower() for w in result.warnings)


# ---------------------------------------------------------------------------
# Round 3 — tmux custom layout-string generation from split tree
# ---------------------------------------------------------------------------


class TestTmuxLayoutString:
    """The custom-layout-string generator must build correctly-formed
    layout strings (with checksum prefix) for arbitrary split trees."""

    def test_checksum_known_value(self) -> None:
        from cpsm.services.session_service import _tmux_layout_checksum

        # Empty body → checksum is 0 (4 hex zeros)
        assert _tmux_layout_checksum("") == "0000"
        # Known: "100x50,0,0,0" → tmux's algo gives a specific value;
        # we just assert it's 4 lowercase hex chars (round-trip property).
        out = _tmux_layout_checksum("100x50,0,0,0")
        assert len(out) == 4
        assert all(c in "0123456789abcdef" for c in out)

    def test_single_pane_layout_string(self) -> None:
        from cpsm.data.schema import Pane
        from cpsm.services.session_service import _tmux_layout_string_from_tree

        leaf = Pane(connection_id="aa")
        out = _tmux_layout_string_from_tree(leaf, 200, 50)
        # Format: "<csum>,200x50,0,0,0"
        assert "," in out
        csum, body = out.split(",", 1)
        assert len(csum) == 4
        assert body == "200x50,0,0,0"

    def test_horizontal_split_layout_string(self) -> None:
        from cpsm.data.schema import Pane, Split
        from cpsm.services.session_service import _tmux_layout_string_from_tree

        tree = Split(
            direction="h",
            children=[Pane(connection_id="aa"), Pane(connection_id="bb")],
        )
        out = _tmux_layout_string_from_tree(tree, 200, 50)
        _csum, body = out.split(",", 1)
        # h split → curly braces, halved widths (100x50 each)
        assert body == "200x50,0,0{100x50,0,0,0,100x50,100,0,1}"

    def test_vertical_split_layout_string(self) -> None:
        from cpsm.data.schema import Pane, Split
        from cpsm.services.session_service import _tmux_layout_string_from_tree

        tree = Split(
            direction="v",
            children=[Pane(connection_id="aa"), Pane(connection_id="bb")],
        )
        out = _tmux_layout_string_from_tree(tree, 200, 50)
        _csum, body = out.split(",", 1)
        # v split → square brackets, children at half height (200x25 each)
        assert body == "200x50,0,0[200x25,0,0,0,200x25,0,25,1]"

    def test_quadrant_layout_string(self) -> None:
        """Mixed-orientation layout: an h-split of two v-splits = quadrants.
        Each child v-split has half the width; each leaf has half the height
        of its column."""
        from cpsm.data.schema import Pane, Split
        from cpsm.services.session_service import _tmux_layout_string_from_tree

        tree = Split(
            direction="h",
            children=[
                Split(
                    direction="v",
                    children=[Pane(connection_id="tl"), Pane(connection_id="bl")],
                ),
                Split(
                    direction="v",
                    children=[Pane(connection_id="tr"), Pane(connection_id="br")],
                ),
            ],
        )
        out = _tmux_layout_string_from_tree(tree, 200, 50)
        _csum, body = out.split(",", 1)
        # Top-level h-split (curly), each child is a v-split (square)
        assert body.startswith("200x50,0,0{")
        assert body.endswith("}")
        # Each v-split occupies width 100, height 50; its children are
        # 100x25 (half-height of the column).
        assert "100x50,0,0[100x25,0,0,0,100x25,0,25,1]" in body
        assert "100x50,100,0[100x25,100,0,2,100x25,100,25,3]" in body


class TestReconcileByConnectionId:
    """Reconcile uses connection_id-keyed diff, not positional matching.

    Regression suite for the bugs where editing a layout while a group
    session was running produced duplicated panes, lost connections, or
    wrongly-coloured status dots — all caused by ``vp.panes[i]`` being
    matched to ``existing_panes[i]`` regardless of whether their
    connection_ids actually corresponded.
    """

    @staticmethod
    def _make_pane(
        pane_id: str,
        pane_index: int,
        conn_id: str | None,
        *,
        dead: bool = False,
        window_index: int = 0,
    ) -> MagicMock:
        from cpsm.platform.base import Pane as PlatformPane

        if conn_id is None:
            sc = "bash /tmp/cpsm-launcher-placeholder-vp-1-AbCdEf.sh"
        else:
            sc = f"bash /tmp/cpsm-launcher-{conn_id}-AbCdEf.sh"
        return PlatformPane(
            id=pane_id,
            session="cpsm-group-grp-x-mon-0",
            window_index=window_index,
            pane_index=pane_index,
            pid=12345,
            dead=dead,
            current_command="ssh",
            width=80,
            height=24,
            start_command=sc,
        )

    def _make_three_alive_panes(self) -> list[Any]:
        return [
            self._make_pane("%1", 0, "alpha"),
            self._make_pane("%2", 1, "beta"),
            self._make_pane("%3", 2, "gamma"),
        ]

    def _make_layout(self, conn_ids: list[str | None]) -> ScreenLayout:
        vp = Viewport(
            id="vp-1",
            geometry_pct=GeometryPct(x=0, y=0, w=100, h=100),
            panes=[Pane(connection_id=cid) for cid in conn_ids],
        )
        return ScreenLayout(
            id="ly-1",
            name="L",
            monitors=[Monitor(viewports=[vp])],
        )

    def _make_doc_with_conns(
        self,
        conn_ids: list[str],
        remote_conn: ClaudeRemoteConnection,
    ) -> CpsmDocument:
        key = SshKey(
            id="key-test",
            name="Test",
            type="ed25519",
            private_path="/k",
            public_path="/k.pub",
        )
        connections = [
            ClaudeRemoteConnection(
                id=cid,
                launch_profile="claude-remote",
                host="example.com",
                port=22,
                user="ubuntu",
                identity_file_ref="key-test",
                project_folder="/opt",
                claude_options="",
            )
            for cid in conn_ids
        ]
        return CpsmDocument(ssh_keys=[key], connections=connections)

    def _reconcile(
        self,
        existing: list[Any],
        layout: ScreenLayout,
        doc: CpsmDocument,
        tmp_path: Path,
    ) -> tuple[MagicMock, list]:
        backend = _mock_backend()
        backend.list_panes.return_value = existing
        # split_pane returns a fresh pane object each time
        next_pid = [4]

        def _split(target: str, direction: str) -> Any:
            new_id = f"%{next_pid[0]}"
            new_idx = len(existing) + (next_pid[0] - 4)
            next_pid[0] += 1
            from cpsm.platform.base import Pane as PlatformPane

            return PlatformPane(
                id=new_id,
                session="cpsm-group-grp-x-mon-0",
                window_index=0,
                pane_index=new_idx,
                pid=99,
                dead=False,
                current_command="bash",
                width=80,
                height=24,
                start_command="",
            )

        backend.split_pane.side_effect = _split
        svc = _make_service(doc, backend=backend, tmp_dir=tmp_path)
        warnings: list[str] = []
        results: list = []
        svc._reconcile_session_with_monitor(
            doc=doc,
            mon_session="cpsm-group-grp-x-mon-0",
            monitor=layout.monitors[0],
            existing_panes=existing,
            member_results=results,
            warnings=warnings,
        )
        return backend, warnings

    def test_insert_in_middle_does_not_duplicate(
        self,
        remote_conn: ClaudeRemoteConnection,
        tmp_path: Path,
    ) -> None:
        """Layout [alpha, NEW, beta, gamma] vs running [alpha, beta, gamma]:
        only NEW is split-respawned; alpha/beta/gamma are left alone (no
        respawn) regardless of where NEW was inserted."""
        existing = self._make_three_alive_panes()
        layout = self._make_layout(["alpha", "new", "beta", "gamma"])
        doc = self._make_doc_with_conns(
            ["alpha", "beta", "gamma", "new"],
            remote_conn,
        )
        backend, warnings = self._reconcile(existing, layout, doc, tmp_path)

        # Exactly one split-pane (for the new connection)
        assert backend.split_pane.call_count == 1, warnings
        # Exactly one respawn (the freshly-split pane). The three alive
        # panes are NOT respawned — their live processes are preserved.
        respawned_targets = [c.args[0] for c in backend.respawn_pane.call_args_list]
        assert len(respawned_targets) == 1, respawned_targets
        # No kills — every existing pane's connection is still in the layout.
        assert backend.kill_pane.call_count == 0

    def test_delete_from_middle_kills_only_the_removed(
        self,
        remote_conn: ClaudeRemoteConnection,
        tmp_path: Path,
    ) -> None:
        """Layout [alpha, gamma] vs running [alpha, beta, gamma]: only beta
        is killed; alpha and gamma are left alone (no respawn, no shift)."""
        existing = self._make_three_alive_panes()
        layout = self._make_layout(["alpha", "gamma"])
        doc = self._make_doc_with_conns(["alpha", "beta", "gamma"], remote_conn)
        backend, warnings = self._reconcile(existing, layout, doc, tmp_path)

        # Beta's pane id (%2) is killed
        kill_targets = [c.args[0] for c in backend.kill_pane.call_args_list]
        assert kill_targets == ["%2"], (kill_targets, warnings)
        # Nothing else respawned or split
        assert backend.split_pane.call_count == 0
        assert backend.respawn_pane.call_count == 0

    def test_reorder_uses_swap_pane_not_respawn(
        self,
        remote_conn: ClaudeRemoteConnection,
        tmp_path: Path,
    ) -> None:
        """Layout [gamma, alpha, beta] vs running [alpha, beta, gamma]: no
        kills, no splits, no respawns — just swap-panes to fix order."""
        existing = self._make_three_alive_panes()
        layout = self._make_layout(["gamma", "alpha", "beta"])
        doc = self._make_doc_with_conns(["alpha", "beta", "gamma"], remote_conn)
        backend, warnings = self._reconcile(existing, layout, doc, tmp_path)

        assert backend.split_pane.call_count == 0
        assert backend.respawn_pane.call_count == 0
        assert backend.kill_pane.call_count == 0
        # At least one swap to put gamma at position 0
        assert backend.swap_panes.call_count >= 1, warnings

    def test_dead_pane_with_matching_layout_conn_is_respawned(
        self,
        remote_conn: ClaudeRemoteConnection,
        tmp_path: Path,
    ) -> None:
        """Dead pane whose connection_id is still in the layout: respawn
        it with that connection's launcher (in place — no split, no kill)."""
        panes = self._make_three_alive_panes()
        # Mark beta as dead
        panes[1] = self._make_pane("%2", 1, "beta", dead=True)
        layout = self._make_layout(["alpha", "beta", "gamma"])
        doc = self._make_doc_with_conns(["alpha", "beta", "gamma"], remote_conn)
        backend, warnings = self._reconcile(panes, layout, doc, tmp_path)

        respawned_targets = [c.args[0] for c in backend.respawn_pane.call_args_list]
        assert respawned_targets == ["%2"], (respawned_targets, warnings)
        assert backend.split_pane.call_count == 0
        assert backend.kill_pane.call_count == 0


class TestLaunchGroupOrphanSourceMove:
    """Group launch picks up live panes from single-launch sessions.

    Regression: launching a connection without a group put it in
    ``cpsm-<conn_id>``. Adding it to a group's layout and launching the
    group used to spawn a SECOND copy in ``cpsm-group-<gid>-mon-0``
    instead of moving the live pane.
    """

    @staticmethod
    def _platform_pane(
        pane_id: str,
        session: str,
        conn_id: str,
        *,
        pane_index: int = 0,
        window_index: int = 0,
        dead: bool = False,
    ) -> Any:
        from cpsm.platform.base import Pane as PlatformPane

        sc = f"bash /tmp/cpsm-launcher-{conn_id}-AbCdEf.sh"
        return PlatformPane(
            id=pane_id,
            session=session,
            window_index=window_index,
            pane_index=pane_index,
            pid=12345,
            dead=dead,
            current_command="bash",
            width=80,
            height=24,
            start_command=sc,
        )

    def test_single_launch_pane_is_joined_into_group_session(
        self,
        local_conn: ClaudeLocalConnection,
        tmp_path: Path,
    ) -> None:
        """A pane in ``cpsm-dotfiles`` (single launch) gets ``join-pane``'d
        into ``cpsm-group-grp-x-mon-0`` rather than duplicated."""
        from cpsm.data.schema import (
            CpsmDocument,
            GeometryPct,
            Group,
            ScreenLayout,
            SshKey,
            Viewport,
        )
        from cpsm.data.schema import (
            Monitor as _Monitor,
        )
        from cpsm.data.schema import (
            Pane as _Pane,
        )

        key = SshKey(
            id="key-test",
            name="Test",
            type="ed25519",
            private_path="/k",
            public_path="/k.pub",
        )
        vp = Viewport(
            id="vp-1",
            geometry_pct=GeometryPct(x=0, y=0, w=100, h=100),
            panes=[_Pane(connection_id="dotfiles")],
        )
        layout = ScreenLayout(
            id="ly-1",
            name="L",
            monitors=[_Monitor(viewports=[vp])],
        )
        grp = Group(
            id="grp-x",
            name="X",
            members=["dotfiles"],
            default_layout_id="ly-1",
        )
        doc = CpsmDocument(
            ssh_keys=[key],
            connections=[local_conn],
            groups=[grp],
            screen_layouts=[layout],
        )

        # Backend state: a live single-launch session exists for dotfiles.
        # No cpsm-group-grp-x-mon-* sessions yet.
        from types import SimpleNamespace

        single_pane = self._platform_pane("%1", "cpsm-dotfiles", "dotfiles")
        joined_pane = self._platform_pane(
            "%1",
            "cpsm-group-grp-x-mon-0",
            "dotfiles",
        )
        # State that mutates as the launch progresses. ``moved`` flips after
        # the join-pane call so subsequent list_panes queries reflect the
        # post-move world: the source is empty, the destination has the
        # relocated pane.
        state = {"moved": False}

        def _list_panes(target=None):
            if state["moved"]:
                if target is None:
                    return [joined_pane]
                if target == "cpsm-dotfiles":
                    return []
                if target.startswith("cpsm-group-grp-x-mon-0"):
                    return [joined_pane]
                return []
            # Pre-move state
            if target is None:
                return [single_pane]
            if target == "cpsm-dotfiles":
                return [single_pane]
            return []

        def _join_pane(*_args, **_kw):
            state["moved"] = True

        backend = _mock_backend()
        backend.list_sessions.return_value = [SimpleNamespace(name="cpsm-dotfiles", attached=True)]
        backend.list_panes.side_effect = _list_panes
        backend.join_pane.side_effect = _join_pane
        backend.new_session.return_value = SimpleNamespace(
            name="cpsm-group-grp-x-mon-0",
            id="$1",
        )

        svc = _make_service(doc, backend=backend, tmp_dir=tmp_path)
        result = svc.launch_group(doc, "grp-x")

        # Sanity: the launch reported success.
        assert result.success or result.member_results, result

        # The crucial assertion: a join-pane call was made from the
        # single-launch pane into the destination group session — not a
        # respawn-pane that would duplicate the connection.
        join_calls = backend.join_pane.call_args_list
        assert len(join_calls) >= 1, (
            f"expected join-pane to relocate single-launch pane; got 0 calls. "
            f"respawn_pane={backend.respawn_pane.call_args_list}"
        )
        first_join = join_calls[0]
        assert first_join.args[0] == "%1"  # source pane id
        assert "cpsm-group-grp-x-mon-0" in first_join.args[1]

        # Source session was empty after move and got explicitly killed.
        kill_session_targets = [c.args[0] for c in backend.kill_session.call_args_list]
        assert "cpsm-dotfiles" in kill_session_targets, kill_session_targets


class TestCleanupDeadPanes:
    """Reaping of dead cpsm-* panes after the user exits a connection.

    Regression: ``remain-on-exit on`` made dead panes (from clean exits or
    Ctrl+C) linger in their tmux session, leaving the connection stuck on
    red/blue forever.  cleanup_dead_panes() reaps them so the next poll
    cycle finds no pane → connection resolves to "disconnected".
    """

    def _pane_status(
        self,
        *,
        pane_id: str,
        session: str,
        state_value: str,
    ) -> Any:
        from datetime import UTC, datetime

        from cpsm.workers.status_poller import PaneState, PaneStatus

        return PaneStatus(
            pane_id=pane_id,
            session=session,
            state=PaneState(state_value),
            last_seen=datetime.now(tz=UTC),
            exit_code=0 if state_value == "disconnected_clean" else 130,
            last_output_tail=None,
            window_index=0,
            pane_index=0,
            current_command="",
            attached=False,
            start_command="bash /tmp/cpsm-launcher-conn-AbCdEf.sh",
        )

    def test_reaps_dead_pane_in_cpsm_session(self) -> None:
        from cpsm.data.schema import CpsmDocument

        doc = CpsmDocument()
        backend = _mock_backend()
        svc = _make_service(doc, backend=backend)

        snap = [
            self._pane_status(pane_id="%5", session="cpsm-cc-multi", state_value="error"),
        ]
        killed = svc.cleanup_dead_panes(snap)

        assert killed == 1
        kill_pane_targets = [c.args[0] for c in backend.kill_pane.call_args_list]
        assert kill_pane_targets == ["%5"]

    def test_reaps_clean_exit_pane(self) -> None:
        """DISCONNECTED_CLEAN (exit 0) is also reaped, not just ERROR."""
        from cpsm.data.schema import CpsmDocument

        doc = CpsmDocument()
        backend = _mock_backend()
        svc = _make_service(doc, backend=backend)

        snap = [
            self._pane_status(
                pane_id="%7",
                session="cpsm-cc-multi",
                state_value="disconnected_clean",
            ),
        ]
        killed = svc.cleanup_dead_panes(snap)

        assert killed == 1
        assert backend.kill_pane.call_args_list[0].args[0] == "%7"

    def test_does_not_touch_live_panes(self) -> None:
        from cpsm.data.schema import CpsmDocument

        doc = CpsmDocument()
        backend = _mock_backend()
        svc = _make_service(doc, backend=backend)

        snap = [
            self._pane_status(pane_id="%1", session="cpsm-cc-multi", state_value="connected"),
            self._pane_status(pane_id="%2", session="cpsm-group-foo-mon-0", state_value="stale"),
            self._pane_status(pane_id="%3", session="cpsm-empty", state_value="empty_slot"),
        ]
        killed = svc.cleanup_dead_panes(snap)

        assert killed == 0
        backend.kill_pane.assert_not_called()

    def test_does_not_touch_non_cpsm_sessions(self) -> None:
        """User's own tmux sessions must not be reaped even if they have
        dead panes — only cpsm-* are managed by us."""
        from cpsm.data.schema import CpsmDocument

        doc = CpsmDocument()
        backend = _mock_backend()
        svc = _make_service(doc, backend=backend)

        snap = [
            self._pane_status(pane_id="%9", session="user-work", state_value="error"),
        ]
        killed = svc.cleanup_dead_panes(snap)

        assert killed == 0
        backend.kill_pane.assert_not_called()

    def test_kill_pane_failure_is_swallowed(self) -> None:
        """A backend failure on one pane must not stop reaping the rest."""
        from cpsm.data.schema import CpsmDocument

        doc = CpsmDocument()
        backend = _mock_backend()
        backend.kill_pane.side_effect = [Exception("boom"), None]
        svc = _make_service(doc, backend=backend)

        snap = [
            self._pane_status(pane_id="%1", session="cpsm-a", state_value="error"),
            self._pane_status(pane_id="%2", session="cpsm-b", state_value="error"),
        ]
        killed = svc.cleanup_dead_panes(snap)

        # First raised, second succeeded → killed counter is 1.
        assert killed == 1
        assert backend.kill_pane.call_count == 2

    def test_empty_snapshot_is_a_noop(self) -> None:
        from cpsm.data.schema import CpsmDocument

        doc = CpsmDocument()
        backend = _mock_backend()
        svc = _make_service(doc, backend=backend)

        assert svc.cleanup_dead_panes([]) == 0
        assert svc.cleanup_dead_panes(None) == 0
        backend.kill_pane.assert_not_called()


class TestExtractCpsmConnId:
    """``_extract_cpsm_conn_id`` parses the connection_id back out of the
    pane_start_command tmux records for a CPSM-launched pane. The whole
    status-lookup fallback (and reconcile-by-conn-id flow) depends on it,
    so the regex must accept every legal mkstemp suffix."""

    def test_simple_alphanum_suffix(self) -> None:
        from cpsm.services.session_service import _extract_cpsm_conn_id

        assert (
            _extract_cpsm_conn_id("bash /tmp/cpsm-launcher-radar-dash-AbCdEfGh.sh") == "radar-dash"
        )

    def test_suffix_with_underscore(self) -> None:
        """Regression: mkstemp draws from ``ascii_letters + digits + '_'``,
        so suffixes like ``mb_97z2x`` are legitimate. Earlier the regex
        was ``[A-Za-z0-9]`` (no underscore) and silently failed to match."""
        from cpsm.services.session_service import _extract_cpsm_conn_id

        assert (
            _extract_cpsm_conn_id("bash /tmp/cpsm-launcher-radar-dash-mb_97z2x.sh") == "radar-dash"
        )

    def test_suffix_all_underscores_alphanumeric(self) -> None:
        from cpsm.services.session_service import _extract_cpsm_conn_id

        # 8-char suffix with mix of underscore + digits + letters
        assert _extract_cpsm_conn_id("bash /tmp/cpsm-launcher-c1-_a1b2c3_.sh") == "c1"

    def test_placeholder_id_returns_none(self) -> None:
        from cpsm.services.session_service import _extract_cpsm_conn_id

        # Placeholder launchers (empty-slot panes) are tagged "placeholder-<vp>"
        # and explicitly rejected by the helper.
        assert _extract_cpsm_conn_id("bash /tmp/cpsm-launcher-placeholder-vp1-AbCdEfGh.sh") is None

    def test_non_launcher_path_returns_none(self) -> None:
        from cpsm.services.session_service import _extract_cpsm_conn_id

        assert _extract_cpsm_conn_id("ssh user@host") is None
        assert _extract_cpsm_conn_id("") is None
        assert _extract_cpsm_conn_id("/bin/bash") is None
