# -*- coding: utf-8 -*-
"""
Tests for cpsm.data.schema.

Covers:
- All 5 discriminated-union launch_profile variants
- Every §2.5 validator (positive + negative)
- FK integrity checks
- Null connection_id round-trip semantics
- Multi-group membership (same connection_id in two groups must pass)
- jump_host cycle detection
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from cpsm.data.schema import (
    ClaudeLocalConnection,
    ClaudeRemoteConnection,
    CpsmDocument,
    CustomConnection,
    GeometryPct,
    LocalShellConnection,
    Monitor,
    Pane,
    Settings,
    SshShellConnection,
    Viewport,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minimal_doc(**overrides: object) -> dict:
    """Return a minimal valid CpsmDocument dict."""
    base: dict = {
        "schema_version": 1,
        "settings": {},
        "ssh_keys": [],
        "connections": [],
        "groups": [],
        "screen_layouts": [],
        "scenes": [],
        "launch_templates": [],
    }
    base.update(overrides)
    return base


def _remote_conn(**overrides: object) -> dict:
    base: dict = {
        "id": "web01",
        "launch_profile": "claude-remote",
        "host": "dev.example.com",
        "port": 22,
        "user": "ubuntu",
        "identity_file_ref": "key-prod",
        "project_folder": "/opt/app",
        "claude_options": "--resume",
    }
    base.update(overrides)
    return base


def _local_conn(**overrides: object) -> dict:
    base: dict = {
        "id": "local01",
        "launch_profile": "claude-local",
        "project_folder": "~/projects/app",
        "claude_options": "--resume",
    }
    base.update(overrides)
    return base


def _ssh_shell_conn(**overrides: object) -> dict:
    base: dict = {
        "id": "shell01",
        "launch_profile": "ssh-shell",
        "host": "bastion.example.com",
        "port": 22,
        "user": "admin",
        "identity_file_ref": "key-prod",
    }
    base.update(overrides)
    return base


def _local_shell_conn(**overrides: object) -> dict:
    base: dict = {
        "id": "scratch01",
        "launch_profile": "local-shell",
        "project_folder": "~/scratch",
    }
    base.update(overrides)
    return base


def _custom_conn(**overrides: object) -> dict:
    base: dict = {
        "id": "container01",
        "launch_profile": "custom",
        "custom_template_id": "tpl-nspawn",
    }
    base.update(overrides)
    return base


def _key() -> dict:
    return {
        "id": "key-prod",
        "name": "Production key",
        "type": "ed25519",
        "private_path": "~/.ssh/id_ed25519",
        "public_path": "~/.ssh/id_ed25519.pub",
    }


def _template() -> dict:
    return {
        "id": "tpl-nspawn",
        "bash": "machinectl shell root@dev /bin/bash",
    }


# ===========================================================================
# Settings
# ===========================================================================


class TestSettings:
    def test_defaults(self) -> None:
        s = Settings()
        assert s.default_multiplexer == "auto"
        assert s.log_level == "INFO"

    def test_custom_values(self) -> None:
        s = Settings(default_multiplexer="tmux", log_level="DEBUG")
        assert s.default_multiplexer == "tmux"

    def test_extra_field_forbidden(self) -> None:
        with pytest.raises(ValidationError, match="extra_field"):
            Settings.model_validate({"extra_field": "x"})  # type: ignore[call-arg]


# ===========================================================================
# ID Slug Validation
# ===========================================================================


class TestIdSlug:
    @pytest.mark.parametrize(
        "slug",
        [
            "web01",
            "a1",
            "my-connection-id",
            "0abc",
            "abc-123-xyz",
            "a" + "b" * 62,  # max length = 63 chars
        ],
    )
    def test_valid_slugs(self, slug: str) -> None:
        conn = ClaudeLocalConnection(
            id=slug, launch_profile="claude-local", project_folder="/tmp", claude_options=""
        )
        assert conn.id == slug

    @pytest.mark.parametrize(
        "slug",
        [
            "",  # empty
            "A",  # uppercase start
            "-abc",  # leading dash
            "ab cd",  # space
            "ab_cd",  # underscore
            "a",  # too short (need at least 2 chars: [a-z0-9][a-z0-9-]{1,62})
            "A" * 64,  # too long
        ],
    )
    def test_invalid_slugs(self, slug: str) -> None:
        with pytest.raises(ValidationError):
            ClaudeLocalConnection(
                id=slug, launch_profile="claude-local", project_folder="/tmp", claude_options=""
            )


# ===========================================================================
# GeometryPct
# ===========================================================================


class TestGeometryPct:
    def test_valid_geometry(self) -> None:
        g = GeometryPct(x=0, y=0, w=50, h=50)
        assert g.x == 0

    def test_full_screen(self) -> None:
        g = GeometryPct(x=0, y=0, w=100, h=100)
        assert g.w == 100

    def test_x_plus_w_exactly_100(self) -> None:
        g = GeometryPct(x=50, y=0, w=50, h=100)
        assert g.x + g.w == 100

    def test_x_plus_w_exceeds_100(self) -> None:
        with pytest.raises(ValidationError, match="exceeds 100"):
            GeometryPct(x=60, y=0, w=50, h=100)

    def test_y_plus_h_exceeds_100(self) -> None:
        with pytest.raises(ValidationError, match="exceeds 100"):
            GeometryPct(x=0, y=60, w=100, h=50)

    def test_zero_width_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            GeometryPct(x=0, y=0, w=0, h=50)

    def test_negative_x_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            GeometryPct(x=-1, y=0, w=50, h=50)

    def test_x_equals_100_forbidden(self) -> None:
        # x must be <= 100 AND w > 0, so x=100, w=1 would exceed; x=100 alone is ok per
        # field constraint (le=100) but combined validator will fail
        with pytest.raises(ValidationError):
            GeometryPct(x=100, y=0, w=1, h=100)


# ===========================================================================
# Pane — null connection_id
# ===========================================================================


class TestPane:
    def test_null_connection_id(self) -> None:
        p = Pane(connection_id=None)
        assert p.connection_id is None

    def test_valid_connection_id(self) -> None:
        p = Pane(connection_id="web01")
        assert p.connection_id == "web01"

    def test_invalid_connection_id_slug(self) -> None:
        with pytest.raises(ValidationError):
            Pane(connection_id="INVALID_ID")


# ===========================================================================
# Viewport — uniqueness and overlap
# ===========================================================================


class TestViewport:
    def test_duplicate_pane_connection_id_raises(self) -> None:
        with pytest.raises(ValidationError, match="appears more than once"):
            Viewport(
                id="vp01",
                geometry_pct={"x": 0, "y": 0, "w": 100, "h": 100},
                panes=[{"connection_id": "web01"}, {"connection_id": "web01"}],
            )

    def test_multiple_null_panes_allowed(self) -> None:
        vp = Viewport(
            id="vp01",
            geometry_pct={"x": 0, "y": 0, "w": 100, "h": 100},
            panes=[
                {"connection_id": None},
                {"connection_id": None},
                {"connection_id": "web01"},
            ],
        )
        assert len(vp.panes) == 3


class TestSplitTreeMigration:
    """Round C — schema migration: Viewport.split_tree is auto-built from
    flat panes + tmux_layout when not provided, and panes are auto-derived
    from a tree when only the tree is provided."""

    def test_flat_panes_migrate_to_split_tree_horizontal(self) -> None:
        from cpsm.data.schema import Split

        vp = Viewport(
            id="vp01",
            geometry_pct={"x": 0, "y": 0, "w": 100, "h": 100},
            tmux_layout="even-h",
            panes=[
                {"connection_id": "aa"},
                {"connection_id": "bb"},
                {"connection_id": "cc"},
            ],
        )
        assert isinstance(vp.split_tree, Split)
        assert vp.split_tree.direction == "h"
        assert len(vp.split_tree.children) == 3

    def test_flat_panes_migrate_to_split_tree_vertical(self) -> None:
        from cpsm.data.schema import Split

        vp = Viewport(
            id="vp01",
            geometry_pct={"x": 0, "y": 0, "w": 100, "h": 100},
            tmux_layout="even-v",
            panes=[{"connection_id": "aa"}, {"connection_id": "bb"}],
        )
        assert isinstance(vp.split_tree, Split)
        assert vp.split_tree.direction == "v"

    def test_single_pane_migrates_to_leaf(self) -> None:
        from cpsm.data.schema import Pane as PaneModel

        vp = Viewport(
            id="vp01",
            geometry_pct={"x": 0, "y": 0, "w": 100, "h": 100},
            panes=[{"connection_id": "only"}],
        )
        assert isinstance(vp.split_tree, PaneModel)
        assert vp.split_tree.connection_id == "only"

    def test_empty_viewport_has_no_split_tree(self) -> None:
        vp = Viewport(
            id="vp01",
            geometry_pct={"x": 0, "y": 0, "w": 100, "h": 100},
            panes=[],
        )
        assert vp.split_tree is None

    def test_quadrant_tree_round_trips(self) -> None:
        """A quadrant layout — two horizontally-split columns, each split
        vertically — round-trips through Pydantic validation and yields a
        flat panes list of 4 leaves (in left-column-top, left-column-bottom,
        right-column-top, right-column-bottom order)."""
        from cpsm.data.schema import Split

        tree = Split(
            direction="h",
            children=[
                Split(
                    direction="v",
                    children=[{"connection_id": "tl"}, {"connection_id": "bl"}],
                ),
                Split(
                    direction="v",
                    children=[{"connection_id": "tr"}, {"connection_id": "br"}],
                ),
            ],
        )
        vp = Viewport(
            id="vp01",
            geometry_pct={"x": 0, "y": 0, "w": 100, "h": 100},
            split_tree=tree,
        )
        assert [p.connection_id for p in vp.panes] == ["tl", "bl", "tr", "br"]

    def test_split_with_one_child_rejected(self) -> None:
        from cpsm.data.schema import Split

        with pytest.raises(ValidationError, match="at least 2 children"):
            Split(direction="h", children=[{"connection_id": "lone"}])

    def test_split_ratios_length_must_match(self) -> None:
        from cpsm.data.schema import Split

        with pytest.raises(ValidationError, match="ratios length"):
            Split(
                direction="h",
                children=[{"connection_id": "aa"}, {"connection_id": "bb"}],
                ratios=[0.3, 0.4],  # too many
            )

    def test_split_ratios_must_sum_under_one(self) -> None:
        from cpsm.data.schema import Split

        with pytest.raises(ValidationError, match="sum to less than 1"):
            Split(
                direction="h",
                children=[
                    {"connection_id": "aa"},
                    {"connection_id": "bb"},
                    {"connection_id": "cc"},
                ],
                ratios=[0.5, 0.6],  # sum exceeds 1
            )


class TestMonitorOverlap:
    def test_non_overlapping_viewports_ok(self) -> None:
        monitor = Monitor(
            viewports=[
                {
                    "id": "vp1",
                    "geometry_pct": {"x": 0, "y": 0, "w": 50, "h": 100},
                    "panes": [],
                },
                {
                    "id": "vp2",
                    "geometry_pct": {"x": 50, "y": 0, "w": 50, "h": 100},
                    "panes": [],
                },
            ]
        )
        assert len(monitor.viewports) == 2

    def test_touching_viewports_ok(self) -> None:
        """Viewports that share an edge (0% overlap) are valid."""
        monitor = Monitor(
            viewports=[
                {
                    "id": "vp1",
                    "geometry_pct": {"x": 0, "y": 0, "w": 50, "h": 100},
                    "panes": [],
                },
                {
                    "id": "vp2",
                    "geometry_pct": {"x": 50, "y": 0, "w": 50, "h": 100},
                    "panes": [],
                },
            ]
        )
        assert len(monitor.viewports) == 2

    def test_overlapping_viewports_raises(self) -> None:
        with pytest.raises(ValidationError, match="overlap"):
            Monitor(
                viewports=[
                    {
                        "id": "vp1",
                        "geometry_pct": {"x": 0, "y": 0, "w": 60, "h": 100},
                        "panes": [],
                    },
                    {
                        "id": "vp2",
                        "geometry_pct": {"x": 50, "y": 0, "w": 50, "h": 100},
                        "panes": [],
                    },
                ]
            )

    def test_sub_one_percent_overlap_ok(self) -> None:
        """An overlap of 0.5% of monitor area is allowed (spec: max 1%)."""
        # 10% * 5% = 0.5% of monitor -> permitted
        monitor = Monitor(
            viewports=[
                {
                    "id": "vp1",
                    "geometry_pct": {"x": 0, "y": 0, "w": 60, "h": 5},
                    "panes": [],
                },
                {
                    "id": "vp2",
                    "geometry_pct": {"x": 50, "y": 0, "w": 50, "h": 5},
                    "panes": [],
                },
            ]
        )
        assert len(monitor.viewports) == 2

    def test_just_over_one_percent_overlap_raises(self) -> None:
        """An overlap of 1.05% of monitor area is rejected (spec: max 1%)."""
        # 10.5% * 10% = 1.05% of monitor -> rejected
        with pytest.raises(ValidationError, match="overlap"):
            Monitor(
                viewports=[
                    {
                        "id": "vp1",
                        "geometry_pct": {"x": 0, "y": 0, "w": 60.5, "h": 10},
                        "panes": [],
                    },
                    {
                        "id": "vp2",
                        "geometry_pct": {"x": 50, "y": 0, "w": 50, "h": 10},
                        "panes": [],
                    },
                ]
            )

    def test_duplicate_viewport_id_raises(self) -> None:
        with pytest.raises(ValidationError, match="not unique"):
            Monitor(
                viewports=[
                    {
                        "id": "vp01",
                        "geometry_pct": {"x": 0, "y": 0, "w": 50, "h": 100},
                        "panes": [],
                    },
                    {
                        "id": "vp01",
                        "geometry_pct": {"x": 50, "y": 0, "w": 50, "h": 100},
                        "panes": [],
                    },
                ]
            )


# ===========================================================================
# ClaudeRemoteConnection
# ===========================================================================


class TestClaudeRemoteConnection:
    def test_valid(self) -> None:
        conn = ClaudeRemoteConnection(**_remote_conn())
        assert conn.launch_profile == "claude-remote"
        assert conn.host == "dev.example.com"

    def test_missing_host_raises(self) -> None:
        data = _remote_conn()
        del data["host"]
        with pytest.raises(ValidationError):
            ClaudeRemoteConnection(**data)

    def test_missing_user_raises(self) -> None:
        data = _remote_conn()
        del data["user"]
        with pytest.raises(ValidationError):
            ClaudeRemoteConnection(**data)

    def test_identity_file_ref_optional(self) -> None:
        """Round C: identity_file_ref is now optional. Missing or None
        means no key configured yet — auth_method='ask' will prompt the
        user on first launch."""
        data = _remote_conn()
        del data["identity_file_ref"]
        conn = ClaudeRemoteConnection(**data)
        assert conn.identity_file_ref is None
        # Default auth_method is "ask"
        assert conn.auth_method == "ask"
        assert conn.key_deployed is False

    def test_missing_project_folder_raises(self) -> None:
        data = _remote_conn()
        del data["project_folder"]
        with pytest.raises(ValidationError):
            ClaudeRemoteConnection(**data)

    def test_missing_claude_options_raises(self) -> None:
        data = _remote_conn()
        del data["claude_options"]
        with pytest.raises(ValidationError):
            ClaudeRemoteConnection(**data)

    def test_extra_forbidden_field_raises(self) -> None:
        data = _remote_conn(custom_template_id="tpl-foo")
        with pytest.raises(ValidationError):
            ClaudeRemoteConnection(**data)

    def test_jump_host_optional(self) -> None:
        conn = ClaudeRemoteConnection(**_remote_conn(jump_host="bastion"))
        assert conn.jump_host == "bastion"

    # ── Remote Control fields ────────────────────────────────────────────

    def test_remote_control_defaults_off(self) -> None:
        conn = ClaudeRemoteConnection(**_remote_conn())
        assert conn.remote_control_enabled is False
        assert conn.remote_control_name is None

    def test_remote_control_enabled_with_explicit_name(self) -> None:
        conn = ClaudeRemoteConnection(
            **_remote_conn(
                remote_control_enabled=True,
                remote_control_name="web-server-1",
            )
        )
        assert conn.remote_control_enabled is True
        assert conn.remote_control_name == "web-server-1"

    @pytest.mark.parametrize(
        "good_name",
        ["web-server-1", "build.box", "DB_Migration", "x", "a.b-c_d", "A1"],
    )
    def test_remote_control_name_accepts_safe_chars(self, good_name: str) -> None:
        conn = ClaudeRemoteConnection(
            **_remote_conn(remote_control_name=good_name)
        )
        assert conn.remote_control_name == good_name

    @pytest.mark.parametrize(
        "bad_name",
        ["with space", "tab\there", "newline\nhere", "semi;colon", "$dollar", ""],
    )
    def test_remote_control_name_rejects_unsafe_chars(self, bad_name: str) -> None:
        """Whitespace and shell metacharacters would survive single-layer
        shlex.quote'ing as a unit only — but the rendered string is then
        word-split by bash when expanded as ``$_CLAUDE_OPTIONS``. Reject
        them at schema parse-time so the failure is loud and early."""
        if bad_name == "":
            # Empty string is normalised to None (not an error)
            conn = ClaudeRemoteConnection(
                **_remote_conn(remote_control_name=bad_name)
            )
            assert conn.remote_control_name is None
            return
        with pytest.raises(ValidationError):
            ClaudeRemoteConnection(**_remote_conn(remote_control_name=bad_name))


# ===========================================================================
# ClaudeLocalConnection
# ===========================================================================


class TestClaudeLocalConnection:
    def test_valid(self) -> None:
        conn = ClaudeLocalConnection(**_local_conn())
        assert conn.launch_profile == "claude-local"

    def test_missing_project_folder_raises(self) -> None:
        data = _local_conn()
        del data["project_folder"]
        with pytest.raises(ValidationError):
            ClaudeLocalConnection(**data)

    def test_missing_claude_options_raises(self) -> None:
        data = _local_conn()
        del data["claude_options"]
        with pytest.raises(ValidationError):
            ClaudeLocalConnection(**data)

    @pytest.mark.parametrize(
        "forbidden_field", ["host", "port", "user", "identity_file_ref", "jump_host"]
    )
    def test_forbidden_field_raises(self, forbidden_field: str) -> None:
        data = _local_conn()
        # Assign dummy value appropriate to each field type
        value: object
        if forbidden_field == "port":
            value = 22
        else:
            value = "some-value"
        data[forbidden_field] = value
        with pytest.raises(ValidationError):
            ClaudeLocalConnection(**data)

    def test_remote_control_defaults_off(self) -> None:
        conn = ClaudeLocalConnection(**_local_conn())
        assert conn.remote_control_enabled is False
        assert conn.remote_control_name is None

    def test_remote_control_enabled_with_name(self) -> None:
        conn = ClaudeLocalConnection(
            **_local_conn(
                remote_control_enabled=True,
                remote_control_name="dev.local",
            )
        )
        assert conn.remote_control_enabled is True
        assert conn.remote_control_name == "dev.local"

    def test_remote_control_name_with_whitespace_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ClaudeLocalConnection(
                **_local_conn(remote_control_name="bad name"),
            )


# ===========================================================================
# SshShellConnection
# ===========================================================================


class TestSshShellConnection:
    def test_valid(self) -> None:
        conn = SshShellConnection(**_ssh_shell_conn())
        assert conn.launch_profile == "ssh-shell"

    def test_missing_host_raises(self) -> None:
        data = _ssh_shell_conn()
        del data["host"]
        with pytest.raises(ValidationError):
            SshShellConnection(**data)

    def test_missing_user_raises(self) -> None:
        data = _ssh_shell_conn()
        del data["user"]
        with pytest.raises(ValidationError):
            SshShellConnection(**data)

    def test_identity_file_ref_optional(self) -> None:
        """Round C: identity_file_ref is now optional on ssh-shell too."""
        data = _ssh_shell_conn()
        del data["identity_file_ref"]
        conn = SshShellConnection(**data)
        assert conn.identity_file_ref is None
        assert conn.auth_method == "ask"
        assert conn.key_deployed is False

    def test_project_folder_optional(self) -> None:
        conn = SshShellConnection(**_ssh_shell_conn())
        assert conn.project_folder is None

    def test_claude_options_forbidden(self) -> None:
        data = _ssh_shell_conn(claude_options="--resume")
        with pytest.raises(ValidationError):
            SshShellConnection(**data)

    def test_jump_host_forbidden_not_applicable(self) -> None:
        """jump_host is optional (not forbidden) for ssh-shell per §2.3."""
        conn = SshShellConnection(**_ssh_shell_conn(jump_host="bastion"))
        assert conn.jump_host == "bastion"


# ===========================================================================
# LocalShellConnection
# ===========================================================================


class TestLocalShellConnection:
    def test_valid(self) -> None:
        conn = LocalShellConnection(**_local_shell_conn())
        assert conn.launch_profile == "local-shell"

    def test_missing_project_folder_raises(self) -> None:
        data = _local_shell_conn()
        del data["project_folder"]
        with pytest.raises(ValidationError):
            LocalShellConnection(**data)

    @pytest.mark.parametrize(
        "forbidden_field",
        ["host", "port", "user", "identity_file_ref", "jump_host", "claude_options"],
    )
    def test_forbidden_field_raises(self, forbidden_field: str) -> None:
        data = _local_shell_conn()
        value: object
        if forbidden_field == "port":
            value = 22
        else:
            value = "some-value"
        data[forbidden_field] = value
        with pytest.raises(ValidationError):
            LocalShellConnection(**data)


# ===========================================================================
# CustomConnection
# ===========================================================================


class TestCustomConnection:
    def test_valid_minimal(self) -> None:
        conn = CustomConnection(**_custom_conn())
        assert conn.launch_profile == "custom"
        assert conn.custom_template_id == "tpl-nspawn"

    def test_missing_custom_template_id_raises(self) -> None:
        data = _custom_conn()
        del data["custom_template_id"]
        with pytest.raises(ValidationError):
            CustomConnection(**data)

    def test_optional_fields_accepted(self) -> None:
        conn = CustomConnection(
            **_custom_conn(
                host="dev.example.com",
                port=22,
                user="ubuntu",
                project_folder="/opt/app",
                claude_options="--resume",
            )
        )
        assert conn.host == "dev.example.com"

    def test_invalid_custom_template_id_slug(self) -> None:
        with pytest.raises(ValidationError):
            CustomConnection(
                id="my-conn",
                launch_profile="custom",
                custom_template_id="INVALID_SLUG",
            )


# ===========================================================================
# Discriminated union dispatch via CpsmDocument
# ===========================================================================


class TestDiscriminatedUnion:
    @pytest.mark.parametrize(
        "conn_data,expected_type",
        [
            (_remote_conn(), ClaudeRemoteConnection),
            (_local_conn(), ClaudeLocalConnection),
            (_ssh_shell_conn(), SshShellConnection),
            (_local_shell_conn(), LocalShellConnection),
            (_custom_conn(), CustomConnection),
        ],
    )
    def test_discriminated_dispatch(self, conn_data: dict, expected_type: type) -> None:
        doc = CpsmDocument.model_validate(
            _minimal_doc(
                ssh_keys=[_key()] if "identity_file_ref" in conn_data else [],
                connections=[conn_data],
                launch_templates=[_template()]
                if conn_data.get("launch_profile") == "custom"
                else [],
            )
        )
        assert isinstance(doc.connections[0], expected_type)

    def test_unknown_launch_profile_raises(self) -> None:
        data = {
            "id": "bad01",
            "launch_profile": "unknown-profile",
            "project_folder": "/tmp",
        }
        with pytest.raises(ValidationError):
            CpsmDocument.model_validate(_minimal_doc(connections=[data]))


# ===========================================================================
# FK Integrity
# ===========================================================================


class TestFKIntegrity:
    def _doc_with_remote_conn(self, extra_conns: list | None = None) -> dict:
        return _minimal_doc(
            ssh_keys=[_key()],
            connections=[_remote_conn()] + (extra_conns or []),
        )

    def test_jump_host_fk_valid(self) -> None:
        bastion = _ssh_shell_conn(id="bastion")
        conn = _remote_conn(jump_host="bastion")
        doc = _minimal_doc(
            ssh_keys=[_key()],
            connections=[conn, bastion],
        )
        parsed = CpsmDocument.model_validate(doc)
        assert parsed.connections[0].jump_host == "bastion"  # type: ignore[union-attr]

    def test_jump_host_missing_loads_with_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Round C (extended): a dangling jump_host loads with a warning
        rather than blocking the whole config."""
        conn = _remote_conn(jump_host="nonexistent")
        with caplog.at_level("WARNING"):
            doc = CpsmDocument.model_validate(
                _minimal_doc(ssh_keys=[_key()], connections=[conn])
            )
        assert any("jump_host" in r.message for r in caplog.records), (
            "Expected a WARNING about the dangling jump_host"
        )
        assert doc.connections[0].jump_host == "nonexistent"  # type: ignore[union-attr]

    def test_identity_file_ref_fk_valid(self) -> None:
        doc = _minimal_doc(ssh_keys=[_key()], connections=[_remote_conn()])
        CpsmDocument.model_validate(doc)

    def test_identity_file_ref_missing_loads_with_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Round C: a dangling identity_file_ref reference is no longer
        fatal — it logs a warning and the document loads. The connection's
        auth_method will recover at launch time."""
        conn = _remote_conn()  # references "key-prod" but no ssh_keys entry
        with caplog.at_level("WARNING"):
            doc = CpsmDocument.model_validate(_minimal_doc(connections=[conn]))
        assert any("identity_file_ref" in r.message for r in caplog.records), (
            "Expected a WARNING about the dangling identity_file_ref"
        )
        # Connection still loads with the dangling reference intact
        assert doc.connections[0].identity_file_ref == "key-prod"

    def test_custom_template_id_fk_valid(self) -> None:
        doc = _minimal_doc(
            connections=[_custom_conn()],
            launch_templates=[_template()],
        )
        CpsmDocument.model_validate(doc)

    def test_custom_template_id_missing_loads_with_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Round C (extended): a dangling custom_template_id loads with a
        warning; runtime falls back to the default template."""
        with caplog.at_level("WARNING"):
            doc = CpsmDocument.model_validate(_minimal_doc(connections=[_custom_conn()]))
        assert any("custom_template_id" in r.message for r in caplog.records), (
            "Expected a WARNING about the dangling custom_template_id"
        )
        assert doc.connections[0].custom_template_id is not None  # type: ignore[union-attr]

    def test_group_member_fk_valid(self) -> None:
        doc = _minimal_doc(
            ssh_keys=[_key()],
            connections=[_remote_conn()],
            groups=[{"id": "grp1", "name": "Group 1", "members": ["web01"]}],
        )
        CpsmDocument.model_validate(doc)

    def test_group_member_missing_loads_with_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Round C (extended): a dangling group member loads with a warning;
        the missing member is skipped at group launch."""
        doc = _minimal_doc(
            groups=[{"id": "grp1", "name": "Group 1", "members": ["nonexistent"]}],
        )
        with caplog.at_level("WARNING"):
            parsed = CpsmDocument.model_validate(doc)
        assert any(
            "member" in r.message and "nonexistent" in r.message for r in caplog.records
        ), "Expected a WARNING naming the dangling group member"
        assert parsed.groups[0].members == ["nonexistent"]

    def test_group_default_layout_id_fk_valid(self) -> None:
        layout = {
            "id": "layout01",
            "name": "Layout",
            "monitors": [],
        }
        group = {
            "id": "grp1",
            "name": "Group 1",
            "members": ["web01"],
            "default_layout_id": "layout01",
        }
        doc = _minimal_doc(
            ssh_keys=[_key()],
            connections=[_remote_conn()],
            groups=[group],
            screen_layouts=[layout],
        )
        CpsmDocument.model_validate(doc)

    def test_group_default_layout_id_missing_loads_with_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Round C (extended): a dangling default_layout_id loads with a
        warning; group loads without a default layout."""
        group = {
            "id": "grp1",
            "name": "Group 1",
            "members": [],
            "default_layout_id": "nonexistent-layout",
        }
        with caplog.at_level("WARNING"):
            parsed = CpsmDocument.model_validate(_minimal_doc(groups=[group]))
        assert any("default_layout_id" in r.message for r in caplog.records), (
            "Expected a WARNING about the dangling default_layout_id"
        )
        assert parsed.groups[0].default_layout_id == "nonexistent-layout"

    def test_pane_connection_id_fk_valid(self) -> None:
        layout = {
            "id": "layout01",
            "name": "Layout",
            "monitors": [
                {
                    "viewports": [
                        {
                            "id": "vp01",
                            "geometry_pct": {"x": 0, "y": 0, "w": 100, "h": 100},
                            "panes": [{"connection_id": "web01"}],
                        }
                    ]
                }
            ],
        }
        doc = _minimal_doc(
            ssh_keys=[_key()],
            connections=[_remote_conn()],
            screen_layouts=[layout],
        )
        CpsmDocument.model_validate(doc)

    def test_pane_null_connection_id_skips_fk_check(self) -> None:
        """A pane with connection_id: null must not trigger FK validation (§2.5)."""
        layout = {
            "id": "layout01",
            "name": "Layout",
            "monitors": [
                {
                    "viewports": [
                        {
                            "id": "vp01",
                            "geometry_pct": {"x": 0, "y": 0, "w": 100, "h": 100},
                            "panes": [{"connection_id": None}],
                        }
                    ]
                }
            ],
        }
        doc = _minimal_doc(screen_layouts=[layout])
        parsed = CpsmDocument.model_validate(doc)
        pane = parsed.screen_layouts[0].monitors[0].viewports[0].panes[0]
        assert pane.connection_id is None

    def test_pane_connection_id_missing_loads_with_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Round C (extended): a dangling pane connection_id loads with a
        warning; the pane opens empty until the reference is repaired."""
        layout = {
            "id": "layout01",
            "name": "Layout",
            "monitors": [
                {
                    "viewports": [
                        {
                            "id": "vp01",
                            "geometry_pct": {"x": 0, "y": 0, "w": 100, "h": 100},
                            "panes": [{"connection_id": "ghost"}],
                        }
                    ]
                }
            ],
        }
        with caplog.at_level("WARNING"):
            parsed = CpsmDocument.model_validate(_minimal_doc(screen_layouts=[layout]))
        assert any(
            "connection_id" in r.message and "ghost" in r.message for r in caplog.records
        ), "Expected a WARNING naming the dangling pane connection_id"
        pane = parsed.screen_layouts[0].monitors[0].viewports[0].panes[0]
        assert pane.connection_id == "ghost"

    def test_scene_group_fk_valid(self) -> None:
        doc = _minimal_doc(
            ssh_keys=[_key()],
            connections=[_remote_conn()],
            groups=[{"id": "grp1", "name": "G", "members": ["web01"]}],
            scenes=[{"id": "scene1", "groups": ["grp1"]}],
        )
        CpsmDocument.model_validate(doc)

    def test_scene_group_missing_loads_with_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Round C (extended): a dangling scene group ref loads with a
        warning; the missing group is skipped at scene launch."""
        doc = _minimal_doc(
            scenes=[{"id": "scene1", "groups": ["nonexistent-group"]}],
        )
        with caplog.at_level("WARNING"):
            parsed = CpsmDocument.model_validate(doc)
        assert any(
            "scene" in r.message and "nonexistent-group" in r.message
            for r in caplog.records
        ), "Expected a WARNING naming the dangling scene group ref"
        assert parsed.scenes[0].groups == ["nonexistent-group"]

    def test_inherits_from_fk_valid(self) -> None:
        layouts = [
            {"id": "layout-base", "name": "Base", "monitors": []},
            {"id": "layout-child", "name": "Child", "inherits_from": "layout-base", "monitors": []},
        ]
        doc = _minimal_doc(screen_layouts=layouts)
        CpsmDocument.model_validate(doc)

    def test_inherits_from_missing_loads_with_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Round C (extended): a dangling inherits_from loads with a warning;
        the layout loads without inheritance until the ref is repaired."""
        layout = {
            "id": "layout01",
            "name": "Layout",
            "inherits_from": "nonexistent-layout",
            "monitors": [],
        }
        with caplog.at_level("WARNING"):
            parsed = CpsmDocument.model_validate(_minimal_doc(screen_layouts=[layout]))
        assert any("inherits_from" in r.message for r in caplog.records), (
            "Expected a WARNING about the dangling inherits_from"
        )
        assert parsed.screen_layouts[0].inherits_from == "nonexistent-layout"


# ===========================================================================
# Multi-group membership (same connection_id in two groups — must NOT raise)
# ===========================================================================


class TestMultiGroupMembership:
    def test_same_connection_in_two_groups_is_valid(self) -> None:
        """§2.5: Same non-null connection_id may appear across multiple groups."""
        doc = _minimal_doc(
            ssh_keys=[_key()],
            connections=[_remote_conn()],
            groups=[
                {"id": "grp-alpha", "name": "Alpha", "members": ["web01"]},
                {"id": "grp-beta", "name": "Beta", "members": ["web01"]},
            ],
        )
        parsed = CpsmDocument.model_validate(doc)
        assert len(parsed.groups) == 2
        assert "web01" in parsed.groups[0].members
        assert "web01" in parsed.groups[1].members

    def test_same_connection_in_two_viewports_is_valid(self) -> None:
        """§2.5: Cross-viewport connection_id duplication is allowed."""
        layout = {
            "id": "layout01",
            "name": "Layout",
            "monitors": [
                {
                    "viewports": [
                        {
                            "id": "vp01",
                            "geometry_pct": {"x": 0, "y": 0, "w": 50, "h": 100},
                            "panes": [{"connection_id": "web01"}],
                        },
                        {
                            "id": "vp02",
                            "geometry_pct": {"x": 50, "y": 0, "w": 50, "h": 100},
                            "panes": [{"connection_id": "web01"}],
                        },
                    ]
                }
            ],
        }
        doc = _minimal_doc(
            ssh_keys=[_key()],
            connections=[_remote_conn()],
            screen_layouts=[layout],
        )
        parsed = CpsmDocument.model_validate(doc)
        vps = parsed.screen_layouts[0].monitors[0].viewports
        assert vps[0].panes[0].connection_id == "web01"
        assert vps[1].panes[0].connection_id == "web01"


# ===========================================================================
# jump_host cycle detection
# ===========================================================================


class TestJumpHostChain:
    def test_linear_chain_ok(self) -> None:
        """A → B (no cycle, depth 1) should be valid."""
        bastion = _ssh_shell_conn(id="bastion")
        conn = _remote_conn(jump_host="bastion")
        doc = _minimal_doc(
            ssh_keys=[_key()],
            connections=[conn, bastion],
        )
        CpsmDocument.model_validate(doc)

    def test_cycle_raises(self) -> None:
        """A → B → A is a cycle and must be rejected."""
        conn_a = {
            "id": "conn-a",
            "launch_profile": "ssh-shell",
            "host": "a.example.com",
            "port": 22,
            "user": "admin",
            "identity_file_ref": "key-prod",
            "jump_host": "conn-b",
        }
        conn_b = {
            "id": "conn-b",
            "launch_profile": "ssh-shell",
            "host": "b.example.com",
            "port": 22,
            "user": "admin",
            "identity_file_ref": "key-prod",
            "jump_host": "conn-a",
        }
        doc = _minimal_doc(
            ssh_keys=[_key()],
            connections=[conn_a, conn_b],
        )
        with pytest.raises(ValidationError, match="cycle"):
            CpsmDocument.model_validate(doc)

    def test_self_cycle_raises(self) -> None:
        """A → A is a trivial cycle."""
        conn = {
            "id": "conn-a",
            "launch_profile": "ssh-shell",
            "host": "a.example.com",
            "port": 22,
            "user": "admin",
            "identity_file_ref": "key-prod",
            "jump_host": "conn-a",
        }
        doc = _minimal_doc(ssh_keys=[_key()], connections=[conn])
        with pytest.raises(ValidationError, match="cycle"):
            CpsmDocument.model_validate(doc)

    def test_max_depth_4_ok(self) -> None:
        """Chains up to depth 4 are allowed."""
        conns = []
        for i in range(1, 5):
            conn: dict = {
                "id": f"hop{i:02d}",
                "launch_profile": "ssh-shell",
                "host": f"h{i}.example.com",
                "port": 22,
                "user": "admin",
                "identity_file_ref": "key-prod",
            }
            if i < 4:
                conn["jump_host"] = f"hop{i + 1:02d}"
            conns.append(conn)
        doc = _minimal_doc(ssh_keys=[_key()], connections=conns)
        CpsmDocument.model_validate(doc)

    def test_depth_5_raises(self) -> None:
        """Chains exceeding depth 4 must be rejected."""
        conns = []
        for i in range(1, 6):
            conn: dict = {
                "id": f"hop{i:02d}",
                "launch_profile": "ssh-shell",
                "host": f"h{i}.example.com",
                "port": 22,
                "user": "admin",
                "identity_file_ref": "key-prod",
            }
            if i < 5:
                conn["jump_host"] = f"hop{i + 1:02d}"
            conns.append(conn)
        doc = _minimal_doc(ssh_keys=[_key()], connections=conns)
        with pytest.raises(ValidationError, match="depth"):
            CpsmDocument.model_validate(doc)


# ===========================================================================
# Full document parse from fixture
# ===========================================================================


class TestFullDocumentFixture:
    def test_valid_cpsm_yaml_parses(self, valid_cpsm_doc: CpsmDocument) -> None:
        assert valid_cpsm_doc.schema_version == 1
        assert len(valid_cpsm_doc.connections) == 6
        assert len(valid_cpsm_doc.groups) == 2
        assert len(valid_cpsm_doc.scenes) == 1
        assert len(valid_cpsm_doc.launch_templates) == 1

    def test_all_five_profiles_present(self, valid_cpsm_doc: CpsmDocument) -> None:
        profiles = {c.launch_profile for c in valid_cpsm_doc.connections}
        assert profiles == {"claude-remote", "claude-local", "ssh-shell", "local-shell", "custom"}

    def test_null_pane_present(self, valid_cpsm_doc: CpsmDocument) -> None:
        panes = [
            pane
            for layout in valid_cpsm_doc.screen_layouts
            for monitor in layout.monitors
            for vp in monitor.viewports
            for pane in vp.panes
        ]
        null_panes = [p for p in panes if p.connection_id is None]
        assert len(null_panes) >= 1

    def test_multi_group_membership(self, valid_cpsm_doc: CpsmDocument) -> None:
        """web01 appears in both project-1 and project-2."""
        groups_containing_web01 = [g.id for g in valid_cpsm_doc.groups if "web01" in g.members]
        assert len(groups_containing_web01) == 2

    def test_inherits_from_null(self, valid_cpsm_doc: CpsmDocument) -> None:
        layout = next(sl for sl in valid_cpsm_doc.screen_layouts if sl.id == "layout-project-1")
        assert layout.inherits_from is None


# ---------------------------------------------------------------------------
# conftest-style fixtures (declared here since conftest.py may not exist yet)
# ---------------------------------------------------------------------------


@pytest.fixture
def valid_cpsm_doc() -> CpsmDocument:
    """Load valid-cpsm.yaml and return parsed CpsmDocument."""
    from pathlib import Path

    from ruamel.yaml import YAML

    fixture_path = Path(__file__).parent / "fixtures" / "valid-cpsm.yaml"
    yaml = YAML(typ="rt")
    with open(fixture_path, encoding="utf-8") as fh:
        raw = yaml.load(fh)

    from cpsm.data.repository import _commented_map_to_plain

    plain = _commented_map_to_plain(raw)
    return CpsmDocument.model_validate(plain)


class TestFkIssuesBlankPrivatePath:
    """`cpsm validate` must agree with what the launcher will actually do.

    Checking only that the id exists let a key entry with an empty
    private_path pass validation, then fail at launch with
    IdentityKeyNotFoundError -- "validate is clean" and "this will launch"
    disagreed for exactly the shape the launch guard was added to catch.
    """

    def test_blank_private_path_is_reported(self):
        from cpsm.data.schema import (
            CpsmDocument,
            SshKey,
            SshShellConnection,
            collect_fk_issues,
        )

        doc = CpsmDocument()
        doc.ssh_keys = [
            SshKey(id="blanked", name="Blanked", type="rsa",
                   private_path="", public_path="")
        ]
        doc.connections = [
            SshShellConnection(
                id="c1", name="C1", launch_profile="ssh-shell",
                host="h", user="u", identity_file_ref="blanked",
            )
        ]
        issues = collect_fk_issues(doc)
        assert any("empty private_path" in m for _, m in issues), issues

    def test_usable_key_is_not_reported(self):
        from cpsm.data.schema import (
            CpsmDocument,
            SshKey,
            SshShellConnection,
            collect_fk_issues,
        )

        doc = CpsmDocument()
        doc.ssh_keys = [
            SshKey(id="ok", name="OK", type="rsa",
                   private_path="~/.ssh/utility", public_path="~/.ssh/utility.pub")
        ]
        doc.connections = [
            SshShellConnection(
                id="c1", name="C1", launch_profile="ssh-shell",
                host="h", user="u", identity_file_ref="ok",
            )
        ]
        assert collect_fk_issues(doc) == []
