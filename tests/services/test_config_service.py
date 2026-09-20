# -*- coding: utf-8 -*-
"""Tests for ConfigService.

Coverage targets: ≥ 90% of cpsm/services/config_service.py
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cpsm.data.repository import CpsmRepository
from cpsm.data.schema import (
    ClaudeLocalConnection,
    ClaudeRemoteConnection,
    CpsmDocument,
    CustomConnection,
    Group,
    LaunchTemplate,
    Scene,
    ScreenLayout,
    SshKey,
)
from cpsm.services.config_service import ConfigService, ValidationIssue

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def repo() -> MagicMock:
    mock_repo = MagicMock(spec=CpsmRepository)
    return mock_repo


@pytest.fixture
def service(repo: MagicMock) -> ConfigService:
    return ConfigService(repo)


@pytest.fixture
def minimal_doc() -> CpsmDocument:
    """A minimal valid document."""
    return CpsmDocument()


@pytest.fixture
def doc_with_connection() -> CpsmDocument:
    """Document with one claude-local connection."""
    conn = ClaudeLocalConnection(
        id="dotfiles",
        launch_profile="claude-local",
        project_folder="~/projects/dotfiles",
        claude_options="--resume",
    )
    return CpsmDocument(connections=[conn])


@pytest.fixture
def doc_with_all(tmp_path: Path) -> CpsmDocument:
    """Document with connections, group, scene, layout, key, template."""
    key = SshKey(
        id="key-test",
        name="Test key",
        type="ed25519",
        private_path=str(tmp_path / "id_ed25519"),
        public_path=str(tmp_path / "id_ed25519.pub"),
    )
    conn = ClaudeRemoteConnection(
        id="web01",
        launch_profile="claude-remote",
        host="example.com",
        port=22,
        user="ubuntu",
        identity_file_ref="key-test",
        project_folder="/opt/app",
        claude_options="--resume",
    )
    local_conn = ClaudeLocalConnection(
        id="dotfiles",
        launch_profile="claude-local",
        project_folder="~/projects/dotfiles",
        claude_options="--resume",
    )
    grp = Group(
        id="grp-1",
        name="Group 1",
        members=["web01", "dotfiles"],
    )
    scene = Scene(id="sc-1", groups=["grp-1"], on_conflict="error")
    layout = ScreenLayout(id="ly-1", name="Layout 1")
    template = LaunchTemplate(id="tpl-1", bash="echo hello")
    return CpsmDocument(
        ssh_keys=[key],
        connections=[conn, local_conn],
        groups=[grp],
        scenes=[scene],
        screen_layouts=[layout],
        launch_templates=[template],
    )


# ---------------------------------------------------------------------------
# load()
# ---------------------------------------------------------------------------


class TestLoad:
    def test_load_calls_repo_load_or_create(self, service: ConfigService, repo: MagicMock) -> None:
        repo.load_or_create.return_value = CpsmDocument()
        doc = service.load(Path("/some/path.yaml"))
        repo.load_or_create.assert_called_once()
        assert isinstance(doc, CpsmDocument)

    def test_load_without_path_uses_resolved_default(
        self, service: ConfigService, repo: MagicMock
    ) -> None:
        repo.load_or_create.return_value = CpsmDocument()
        with patch("cpsm.services.config_service.resolve_config_path") as mock_res:
            mock_res.return_value = Path("/resolved/default.yaml")
            service.load(None)
            mock_res.assert_called_once_with(None)

    def test_load_stores_path(self, service: ConfigService, repo: MagicMock) -> None:
        repo.load_or_create.return_value = CpsmDocument()
        service.load(Path("/some/path.yaml"))
        # Path is stored internally
        assert service._path is not None


# ---------------------------------------------------------------------------
# save()
# ---------------------------------------------------------------------------


class TestSave:
    def test_save_delegates_to_repo(
        self, service: ConfigService, repo: MagicMock, minimal_doc: CpsmDocument
    ) -> None:
        service.save(minimal_doc, Path("/out.yaml"))
        repo.save.assert_called_once_with(minimal_doc, Path("/out.yaml"))

    def test_save_uses_stored_path_when_no_path_given(
        self, service: ConfigService, repo: MagicMock, minimal_doc: CpsmDocument
    ) -> None:
        repo.load_or_create.return_value = CpsmDocument()
        with patch("cpsm.services.config_service.resolve_config_path") as mock_res:
            mock_res.return_value = Path("/loaded.yaml")
            service.load(Path("/loaded.yaml"))
        service.save(minimal_doc)
        # Service passes the stored path (resolved from load) to the repo
        repo.save.assert_called_once_with(minimal_doc, Path("/loaded.yaml"))


# ---------------------------------------------------------------------------
# validate()
# ---------------------------------------------------------------------------


class TestValidate:
    def test_validate_returns_empty_on_valid_doc(
        self, service: ConfigService, minimal_doc: CpsmDocument
    ) -> None:
        issues = service.validate(minimal_doc)
        assert issues == []

    def test_validate_returns_issues_on_invalid_doc(self, service: ConfigService) -> None:
        """A real dangling group member must surface as an error-severity
        ValidationIssue — no mock, exercises the shared FK walker end-to-end.
        """
        doc = CpsmDocument(
            groups=[Group(id="grp-broken", name="Broken", members=["nonexistent"])],
        )
        issues = service.validate(doc)
        assert len(issues) > 0
        assert all(isinstance(i, ValidationIssue) for i in issues)
        assert all(i.severity == "error" for i in issues)
        assert any("nonexistent" in i.message for i in issues)

    def test_validate_issue_has_location_and_message(self, service: ConfigService) -> None:
        """Each ValidationIssue from a real dangling FK has both a
        location and a message with the offending id in it.
        """
        doc = CpsmDocument(
            groups=[Group(id="grp-broken", name="Broken", members=["ghost-conn"])],
        )
        issues = service.validate(doc)
        assert len(issues) > 0
        for issue in issues:
            assert issue.location
            assert issue.message
        target = next(i for i in issues if "ghost-conn" in i.message)
        assert target.location == "groups.grp-broken.members"

    def test_validate_covers_all_seven_fk_cases(
        self, service: ConfigService, tmp_path: Path
    ) -> None:
        """Regression coverage: every FK check ConfigService is responsible
        for must produce a real ValidationIssue when broken. Otherwise a
        broken .cpsm.yaml would report "valid" to the CLI ``cpsm validate``
        command and the GUI Validate action — the exact false-green
        regression that shipped after Round C was extended.
        """
        # CustomConnection is the profile that accepts all three of
        # jump_host, identity_file_ref, and (required) custom_template_id.
        conn_with_bad_refs = CustomConnection(
            id="broken",
            launch_profile="custom",
            custom_template_id="ghost-tpl",
            host="example.com",
            port=22,
            user="ubuntu",
            identity_file_ref="ghost-key",
            jump_host="ghost-bastion",
        )
        # Layout with a pane referencing a missing connection AND
        # inherits_from a missing parent.
        from cpsm.data.schema import GeometryPct, Monitor, Pane, Viewport

        broken_layout = ScreenLayout(
            id="ly-broken",
            name="Broken Layout",
            inherits_from="ghost-parent",
            monitors=[
                Monitor(
                    viewports=[
                        Viewport(
                            id="vp1",
                            geometry_pct=GeometryPct(x=0, y=0, w=100, h=100),
                            panes=[Pane(connection_id="ghost-pane-conn")],
                        )
                    ]
                )
            ],
        )
        broken_group = Group(
            id="grp-broken",
            name="Broken group",
            members=["ghost-member"],
            default_layout_id="ghost-layout",
        )
        broken_scene = Scene(id="sc-broken", groups=["ghost-group"], on_conflict="error")

        doc = CpsmDocument(
            connections=[conn_with_bad_refs],
            groups=[broken_group],
            screen_layouts=[broken_layout],
            scenes=[broken_scene],
        )
        issues = service.validate(doc)
        messages = [i.message for i in issues]

        assert any("jump_host" in m for m in messages), "jump_host FK not surfaced"
        assert any("identity_file_ref" in m for m in messages), (
            "identity_file_ref FK not surfaced"
        )
        assert any("custom_template_id" in m for m in messages), (
            "custom_template_id FK not surfaced"
        )
        assert any(
            "member" in m and "ghost-member" in m for m in messages
        ), "group members FK not surfaced"
        assert any(
            "default_layout_id" in m for m in messages
        ), "default_layout_id FK not surfaced"
        assert any("inherits_from" in m for m in messages), "inherits_from FK not surfaced"
        assert any(
            "pane connection_id" in m and "ghost-pane-conn" in m for m in messages
        ), "pane connection_id FK not surfaced"
        assert any(
            "group" in m and "ghost-group" in m for m in messages
        ), "scene groups FK not surfaced"

    def test_validate_structural_error_short_circuits(
        self, service: ConfigService
    ) -> None:
        """A structural pydantic ValidationError (e.g. bad enum) is
        returned without also running the FK walker — trying to walk an
        object that failed structural parsing is meaningless.
        """
        import pydantic

        valid_doc = CpsmDocument()
        ve = pydantic.ValidationError.from_exception_data(
            title="CpsmDocument",
            input_type="python",
            line_errors=[
                {
                    "type": "value_error",
                    "loc": ("connections", 0, "id"),
                    "msg": "Structural failure",
                    "input": "bad-value",
                    "ctx": {"error": ValueError("Structural failure")},
                }
            ],
        )
        with patch("cpsm.services.config_service.CpsmDocument.model_validate", side_effect=ve):
            issues = service.validate(valid_doc)
        assert len(issues) == 1
        assert issues[0].location == "connections.0.id"
        assert issues[0].severity == "error"

    def test_validate_complex_valid_doc(
        self, service: ConfigService, doc_with_all: CpsmDocument
    ) -> None:
        issues = service.validate(doc_with_all)
        assert issues == []


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------


class TestFindConnection:
    def test_find_existing_connection(
        self, service: ConfigService, doc_with_connection: CpsmDocument
    ) -> None:
        conn = service.find_connection(doc_with_connection, "dotfiles")
        assert conn is not None
        assert conn.id == "dotfiles"

    def test_find_missing_connection_returns_none(
        self, service: ConfigService, doc_with_connection: CpsmDocument
    ) -> None:
        conn = service.find_connection(doc_with_connection, "missing")
        assert conn is None


class TestFindGroup:
    def test_find_existing_group(self, service: ConfigService, doc_with_all: CpsmDocument) -> None:
        grp = service.find_group(doc_with_all, "grp-1")
        assert grp is not None
        assert grp.id == "grp-1"

    def test_find_missing_group_returns_none(
        self, service: ConfigService, doc_with_all: CpsmDocument
    ) -> None:
        grp = service.find_group(doc_with_all, "nope")
        assert grp is None


class TestFindScene:
    def test_find_existing_scene(self, service: ConfigService, doc_with_all: CpsmDocument) -> None:
        scene = service.find_scene(doc_with_all, "sc-1")
        assert scene is not None
        assert scene.id == "sc-1"

    def test_find_missing_scene_returns_none(
        self, service: ConfigService, doc_with_all: CpsmDocument
    ) -> None:
        scene = service.find_scene(doc_with_all, "nope")
        assert scene is None


class TestFindLayout:
    def test_find_existing_layout(self, service: ConfigService, doc_with_all: CpsmDocument) -> None:
        layout = service.find_layout(doc_with_all, "ly-1")
        assert layout is not None
        assert layout.id == "ly-1"

    def test_find_missing_layout_returns_none(
        self, service: ConfigService, doc_with_all: CpsmDocument
    ) -> None:
        layout = service.find_layout(doc_with_all, "nope")
        assert layout is None


class TestFindKey:
    def test_find_existing_key(self, service: ConfigService, doc_with_all: CpsmDocument) -> None:
        key = service.find_key(doc_with_all, "key-test")
        assert key is not None
        assert key.id == "key-test"

    def test_find_missing_key_returns_none(
        self, service: ConfigService, doc_with_all: CpsmDocument
    ) -> None:
        key = service.find_key(doc_with_all, "nope")
        assert key is None


# ---------------------------------------------------------------------------
# ValidationIssue dataclass
# ---------------------------------------------------------------------------


class TestValidationIssue:
    def test_defaults(self) -> None:
        issue = ValidationIssue(location="field.x", message="bad value")
        assert issue.severity == "error"

    def test_warning_severity(self) -> None:
        issue = ValidationIssue(location="x", message="y", severity="warning")
        assert issue.severity == "warning"


# ---------------------------------------------------------------------------
# cpsm-connection-key-ux phase-4: a newly pinned ssh_keys entry must survive
# a REAL save -> load round trip, through ConfigService's real API backed by
# CpsmRepository — not the MagicMock repo used throughout this file, which
# proves nothing about serialization.
#
# This is the criterion that most directly protects the user's original
# complaint: 21 saved connections referenced a key id that did not exist in
# ssh_keys. A key that resolves correctly in memory (as connection_editor's
# _resolve_identity_path / _pin_discovered_key already do, see
# tests/ui/test_connection_editor.py) but silently drops or mangles its
# ssh_keys entry on save+reload would recreate exactly that bug one layer
# down, where no in-memory test could ever see it.
#
# Hard constraint: every path here is tmp_path — never ~/.cpsm.yaml.
# ---------------------------------------------------------------------------


class TestPinnedKeyRoundTripsThroughRealSaveLoad:
    def _service(self) -> ConfigService:
        """A ConfigService backed by the REAL CpsmRepository (ruamel.yaml
        atomic write + pydantic re-validation on load), never the MagicMock
        `repo` fixture used elsewhere in this file."""
        return ConfigService(CpsmRepository())

    def test_newly_pinned_key_survives_save_and_reload(self, tmp_path: Path) -> None:
        config_path = tmp_path / "roundtrip.cpsm.yaml"
        service = self._service()

        # Mirrors what connection_editor._pin_discovered_key /
        # _on_new_key_requested hand back via new_ssh_keys: a freshly
        # created SshKey plus a connection whose identity_file_ref names it.
        new_key = SshKey(
            id="freshly-pinned",
            name="Freshly pinned key",
            type="ed25519",
            private_path=str(tmp_path / "id_ed25519_fresh"),
            public_path=str(tmp_path / "id_ed25519_fresh.pub"),
        )
        conn = ClaudeRemoteConnection(
            id="pinned-conn",
            launch_profile="claude-remote",
            host="no-such-host.invalid",
            port=22,
            user="ubuntu",
            identity_file_ref="freshly-pinned",
            project_folder="/opt/app",
            claude_options="--resume",
        )
        doc = CpsmDocument(ssh_keys=[new_key], connections=[conn])

        service.save(doc, config_path)
        assert config_path.exists(), "save() must have written the config file"

        # Fresh service + fresh repository instance: proves the round trip
        # goes through serialized YAML on disk, not a cached in-memory model.
        reloaded = self._service().load(config_path)

        assert [k.id for k in reloaded.ssh_keys] == ["freshly-pinned"]
        reloaded_key = reloaded.ssh_keys[0]
        assert reloaded_key.type == "ed25519"
        assert reloaded_key.private_path == str(tmp_path / "id_ed25519_fresh")
        assert reloaded_key.public_path == str(tmp_path / "id_ed25519_fresh.pub")

        reloaded_conn = reloaded.connections[0]
        assert reloaded_conn.identity_file_ref == "freshly-pinned"

        # The specific failure this test exists to catch: the FK the
        # connection names must actually resolve inside the reloaded
        # document's ssh_keys, not merely be present as a string.
        issues = service.validate(reloaded)
        dangling = [i for i in issues if "ssh_key" in i.location or "identity" in i.message.lower()]
        assert not dangling, f"pinned key did not round-trip as a resolvable FK: {issues}"

    def test_key_missing_from_ssh_keys_is_flagged_by_validate(self, tmp_path: Path) -> None:
        """Negative control for the test above: validate() must actually be
        capable of catching a dangling identity_file_ref, or the assertion
        `not dangling` above would pass vacuously regardless of whether the
        round trip worked."""
        config_path = tmp_path / "dangling.cpsm.yaml"
        service = self._service()

        conn = ClaudeRemoteConnection(
            id="dangling-conn",
            launch_profile="claude-remote",
            host="no-such-host.invalid",
            port=22,
            user="ubuntu",
            identity_file_ref="does-not-exist",
            project_folder="/opt/app",
            claude_options="--resume",
        )
        doc = CpsmDocument(ssh_keys=[], connections=[conn])
        service.save(doc, config_path)
        reloaded = self._service().load(config_path)

        issues = service.validate(reloaded)
        assert any(
            "does-not-exist" in i.message or "does-not-exist" in i.location
            for i in issues
        ), f"validate() failed to flag a dangling identity_file_ref: {issues}"

    def test_save_and_load_never_touch_real_cpsm_yaml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Belt-and-braces: even resolve_config_path's ~/.cpsm.yaml fallback
        is neutralized by pointing HOME at tmp_path, so a test bug that
        forgets to pass an explicit path fails safely instead of touching
        the developer's real config."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("CPSM_CONFIG", raising=False)
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)

        real_home_config = Path.home() / ".cpsm.yaml"
        assert real_home_config == tmp_path / ".cpsm.yaml"

        service = self._service()
        service.save(CpsmDocument(), tmp_path / "explicit.cpsm.yaml")
        assert not real_home_config.exists()
