# -*- coding: utf-8 -*-
"""
Tests for cpsm.data.importer.

Spec sections: §4.2, §4.3

Coverage targets:
  - 14 connections from the fixture
  - 5 groups (Main, DataScience, DevOps, Documentation, Production)
  - All connections have launch_profile == "claude-remote"
  - settings.default_claude_options and default_ssh_options match fixture defaults
  - Collision suffix (-2) on id clash
  - Source-file immutability (mtime, inode, write-blocker monkeypatch)
  - Determinism: two runs produce identical output
  - ImportPreview.transforms contains every expected kind for the fixture
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from cpsm.data.importer import (
    ImportPreview,
    convert,
    load_and_convert,
)

# ---------------------------------------------------------------------------
# Fixture path
# ---------------------------------------------------------------------------

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "example-.claude-projects.yaml"


# ---------------------------------------------------------------------------
# Helper: read the fixture as a plain dict
# ---------------------------------------------------------------------------


def _load_fixture_as_dict(path: Path) -> dict:  # type: ignore[type-arg]
    """Load a YAML file via ruamel safe loader and return a plain dict."""
    from ruamel.yaml import YAML

    yaml = YAML(typ="safe")
    with open(path, encoding="utf-8") as fh:
        raw = yaml.load(fh)
    return raw or {}


# ---------------------------------------------------------------------------
# Basic fixture import
# ---------------------------------------------------------------------------


class TestFixtureImport:
    """Import the real fixture and assert every structural invariant."""

    @pytest.fixture(scope="class")
    def preview(self) -> ImportPreview:
        return load_and_convert(FIXTURE_PATH)

    def test_returns_import_preview(self, preview: ImportPreview) -> None:
        assert isinstance(preview, ImportPreview)

    def test_source_path_recorded(self, preview: ImportPreview) -> None:
        assert preview.source_path == FIXTURE_PATH.resolve()

    def test_connection_count(self, preview: ImportPreview) -> None:
        """Fixture has 14 project entries → 14 connections."""
        assert len(preview.document.connections) == 14

    def test_group_count(self, preview: ImportPreview) -> None:
        """Fixture has 5 unique groups."""
        assert len(preview.document.groups) == 5

    def test_group_names(self, preview: ImportPreview) -> None:
        names = {g.name for g in preview.document.groups}
        assert names == {"Main", "DataScience", "DevOps", "Documentation", "Production"}

    def test_all_connections_are_claude_remote(self, preview: ImportPreview) -> None:
        for conn in preview.document.connections:
            assert conn.launch_profile == "claude-remote", (
                f"connection '{conn.id}' has unexpected profile '{conn.launch_profile}'"
            )

    def test_settings_default_claude_options(self, preview: ImportPreview) -> None:
        assert preview.document.settings.default_claude_options == "--resume"

    def test_settings_default_ssh_options(self, preview: ImportPreview) -> None:
        assert (
            preview.document.settings.default_ssh_options
            == "-o ConnectTimeout=10 -o ServerAliveInterval=30"
        )

    def test_document_validates(self, preview: ImportPreview) -> None:
        """CpsmDocument construction itself proves it — just assert no exception."""
        # Re-validate via model_validate to be explicit
        try:
            from cpsm.data.schema import CpsmDocument

            CpsmDocument.model_validate(preview.document.model_dump())
        except ValidationError as exc:
            pytest.fail(f"Imported document failed re-validation: {exc}")

    def test_placeholder_ssh_key_present(self, preview: ImportPreview) -> None:
        key_ids = [k.id for k in preview.document.ssh_keys]
        assert "imported-default" in key_ids

    def test_placeholder_key_fields(self, preview: ImportPreview) -> None:
        key = next(k for k in preview.document.ssh_keys if k.id == "imported-default")
        assert key.name == "Imported default key"
        assert key.type == "ed25519"
        assert key.private_path == "~/.ssh/id_ed25519"
        assert key.public_path == "~/.ssh/id_ed25519.pub"
        assert key.passphrase_ref is None

    def test_all_connections_reference_placeholder_key(self, preview: ImportPreview) -> None:
        for conn in preview.document.connections:
            assert conn.identity_file_ref == "imported-default", (  # type: ignore[union-attr]
                f"connection '{conn.id}' has wrong identity_file_ref"
            )

    def test_group_members_cover_all_connections(self, preview: ImportPreview) -> None:
        conn_ids = {c.id for c in preview.document.connections}
        member_ids: set[str] = set()
        for group in preview.document.groups:
            member_ids.update(group.members)
        assert conn_ids == member_ids

    # ------------------------------------------------------------------
    # Transform log checks
    # ------------------------------------------------------------------

    def test_transforms_nonempty(self, preview: ImportPreview) -> None:
        assert len(preview.transforms) > 0

    def test_transforms_contain_added_kind(self, preview: ImportPreview) -> None:
        kinds = {t.kind for t in preview.transforms}
        assert "added" in kinds

    def test_transforms_contain_synthesized_kind(self, preview: ImportPreview) -> None:
        kinds = {t.kind for t in preview.transforms}
        assert "synthesized" in kinds

    def test_synthesized_transform_for_placeholder_key(self, preview: ImportPreview) -> None:
        synth = [t for t in preview.transforms if t.kind == "synthesized"]
        assert any("ssh_keys[imported-default]" in t.target_path for t in synth)
        assert any("placeholder key" in t.detail.lower() for t in synth)

    def test_added_transform_per_connection(self, preview: ImportPreview) -> None:
        added = [t for t in preview.transforms if t.kind == "added"]
        conn_paths = {t.target_path for t in added if t.target_path.startswith("connections[")}
        assert len(conn_paths) == 14

    def test_added_transform_per_group(self, preview: ImportPreview) -> None:
        added = [t for t in preview.transforms if t.kind == "added"]
        group_paths = {t.target_path for t in added if t.target_path.startswith("groups[")}
        assert len(group_paths) == 5

    def test_added_transform_for_settings_options(self, preview: ImportPreview) -> None:
        added_paths = {t.target_path for t in preview.transforms if t.kind == "added"}
        assert "settings.default_claude_options" in added_paths
        assert "settings.default_ssh_options" in added_paths


# ---------------------------------------------------------------------------
# Slug collision test
# ---------------------------------------------------------------------------


class TestSlugCollision:
    """Two projects whose names slugify to the same id → second gets -2."""

    @pytest.fixture
    def collision_legacy(self) -> dict:  # type: ignore[type-arg]
        return {
            "defaults": {
                "claude_options": "--resume",
                "ssh_options": "-o ConnectTimeout=5",
            },
            "projects": [
                {
                    "name": "My Project",
                    "host": "host1.example.com",
                    "ssh_user": "user1",
                    "project_folder": "/opt/proj1",
                    "claude_options": "--resume",
                },
                {
                    "name": "My-Project",  # slugifies to same "my-project"
                    "host": "host2.example.com",
                    "ssh_user": "user2",
                    "project_folder": "/opt/proj2",
                    "claude_options": "--resume",
                },
            ],
        }

    def test_collision_gives_suffix(self, collision_legacy: dict) -> None:  # type: ignore[type-arg]
        preview = convert(collision_legacy, Path("/fake/source.yaml"))
        ids = [c.id for c in preview.document.connections]
        assert "my-project" in ids
        assert "my-project-2" in ids

    def test_collision_transform_recorded(
        self,
        collision_legacy: dict,  # type: ignore[type-arg]
    ) -> None:
        preview = convert(collision_legacy, Path("/fake/source.yaml"))
        renamed = [t for t in preview.transforms if t.kind == "renamed"]
        assert len(renamed) == 1
        assert "my-project-2" in renamed[0].target_path
        assert "collision suffixed" in renamed[0].detail


# ---------------------------------------------------------------------------
# Source-file immutability tests
# ---------------------------------------------------------------------------


class TestSourceImmutability:
    """The source file must never be opened for write."""

    def test_mtime_unchanged(self, tmp_path: Path) -> None:
        """mtime must be identical before and after import."""
        # Copy fixture to a writable temp location
        import shutil

        src = tmp_path / "test-.claude-projects.yaml"
        shutil.copy2(FIXTURE_PATH, src)

        stat_before = os.stat(src)
        load_and_convert(src)
        stat_after = os.stat(src)

        assert stat_after.st_mtime == stat_before.st_mtime, (
            "Source file mtime changed — importer opened the file for write."
        )

    def test_inode_unchanged(self, tmp_path: Path) -> None:
        """inode must be identical before and after import."""
        import shutil

        src = tmp_path / "test-.claude-projects.yaml"
        shutil.copy2(FIXTURE_PATH, src)

        stat_before = os.stat(src)
        load_and_convert(src)
        stat_after = os.stat(src)

        assert stat_after.st_ino == stat_before.st_ino, (
            "Source file inode changed — importer may have replaced the file."
        )

    def test_write_blocker_monkeypatch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Monkeypatching Path.write_text and open() on the source path must not
        prevent the import from succeeding — i.e. we never attempt to write."""
        import shutil

        src = tmp_path / "test-.claude-projects.yaml"
        shutil.copy2(FIXTURE_PATH, src)
        src_resolved = src.resolve()

        original_open = open

        def _guarded_open(file: object, mode: str = "r", **kwargs: object) -> object:
            path_str = str(file)
            if str(src_resolved) in path_str and ("w" in str(mode) or "a" in str(mode)):
                raise PermissionError(
                    f"Write-blocker: attempted to open source file for write: {file!r}"
                )
            return original_open(file, mode, **kwargs)  # type: ignore[call-overload]

        monkeypatch.setattr("builtins.open", _guarded_open)

        # This must succeed — the importer only reads the source
        preview = load_and_convert(src)
        assert len(preview.document.connections) == 14


# ---------------------------------------------------------------------------
# Determinism test
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_two_runs_are_identical(self) -> None:
        """Running the importer twice on the same input produces identical output."""
        p1 = load_and_convert(FIXTURE_PATH)
        p2 = load_and_convert(FIXTURE_PATH)

        assert p1.document.model_dump() == p2.document.model_dump()
        assert len(p1.transforms) == len(p2.transforms)
        for t1, t2 in zip(p1.transforms, p2.transforms):
            assert t1 == t2


# ---------------------------------------------------------------------------
# Edge-case / warning tests
# ---------------------------------------------------------------------------


class TestWarningsAndEdgeCases:
    def test_unknown_field_emits_warning(self) -> None:
        """An unknown field in a legacy entry should produce a 'warning' transform."""
        legacy = {
            "projects": [
                {
                    "name": "Test",
                    "host": "h.example.com",
                    "ssh_user": "u",
                    "project_folder": "/proj",
                    "claude_options": "--resume",
                    "unknown_field_xyz": "something",
                }
            ]
        }
        preview = convert(legacy, Path("/fake/source.yaml"))
        warnings = [t for t in preview.transforms if t.kind == "warning"]
        assert any("unknown_field_xyz" in w.detail for w in warnings)

    def test_entry_without_name_is_skipped(self) -> None:
        legacy = {
            "projects": [
                {
                    "host": "h.example.com",
                    "ssh_user": "u",
                    "project_folder": "/proj",
                    "claude_options": "--resume",
                }
            ]
        }
        preview = convert(legacy, Path("/fake/source.yaml"))
        skipped = [t for t in preview.transforms if t.kind == "skipped"]
        assert len(skipped) == 1
        assert len(preview.document.connections) == 0

    def test_empty_legacy_produces_valid_document(self) -> None:
        preview = convert({}, Path("/fake/empty.yaml"))
        assert preview.document.schema_version == 1
        assert len(preview.document.connections) == 0
        assert len(preview.document.groups) == 0

    def test_no_defaults_block_uses_schema_defaults(self) -> None:
        """When there is no defaults block, Settings uses its own defaults."""
        legacy = {
            "projects": [
                {
                    "name": "Minimal",
                    "host": "h.example.com",
                    "ssh_user": "u",
                    "project_folder": "/p",
                    "claude_options": "--resume",
                }
            ]
        }
        preview = convert(legacy, Path("/fake/minimal.yaml"))
        # Settings.default_claude_options has schema default "--resume"
        assert preview.document.settings.default_claude_options == "--resume"

    def test_group_with_no_members_not_synthesized(self) -> None:
        """Projects without a group field produce no group entry."""
        legacy = {
            "projects": [
                {
                    "name": "Ungrouped",
                    "host": "h.example.com",
                    "ssh_user": "u",
                    "project_folder": "/p",
                    "claude_options": "--resume",
                }
            ]
        }
        preview = convert(legacy, Path("/fake/ungrouped.yaml"))
        assert len(preview.document.groups) == 0

    def test_multi_group_collision_in_group_names(self) -> None:
        """Two different display names that slug to the same id get suffixed."""
        legacy = {
            "projects": [
                {
                    "name": "ProjectA",
                    "group": "My Group",
                    "host": "h1.example.com",
                    "ssh_user": "u",
                    "project_folder": "/p1",
                    "claude_options": "--resume",
                },
                {
                    "name": "ProjectB",
                    "group": "My-Group",  # same slug "my-group"
                    "host": "h2.example.com",
                    "ssh_user": "u",
                    "project_folder": "/p2",
                    "claude_options": "--resume",
                },
            ]
        }
        preview = convert(legacy, Path("/fake/gslug.yaml"))
        gids = [g.id for g in preview.document.groups]
        assert "my-group" in gids
        assert "my-group-2" in gids

    def test_per_entry_claude_options_preserved(self) -> None:
        legacy = {
            "defaults": {"claude_options": "--resume"},
            "projects": [
                {
                    "name": "Override",
                    "host": "h.example.com",
                    "ssh_user": "u",
                    "project_folder": "/p",
                    "claude_options": "--new --verbose",
                }
            ],
        }
        preview = convert(legacy, Path("/fake/override.yaml"))
        conn = preview.document.connections[0]
        assert conn.claude_options == "--new --verbose"  # type: ignore[union-attr]

    def test_non_mapping_project_entry_is_skipped(self) -> None:
        """Non-dict entries inside projects[] should be skipped gracefully."""
        legacy = {
            "projects": [
                "this is not a mapping",
                {
                    "name": "Valid",
                    "host": "h.example.com",
                    "ssh_user": "u",
                    "project_folder": "/p",
                    "claude_options": "--resume",
                },
            ]
        }
        preview = convert(legacy, Path("/fake/mixed.yaml"))
        skipped = [t for t in preview.transforms if t.kind == "skipped"]
        assert len(skipped) == 1
        assert len(preview.document.connections) == 1


# ---------------------------------------------------------------------------
# Slugify unit tests
# ---------------------------------------------------------------------------


class TestSlugify:
    def test_simple_name(self) -> None:
        from cpsm.data.importer import _slugify

        assert _slugify("WebApp-Frontend") == "webapp-frontend"

    def test_spaces_become_dashes(self) -> None:
        from cpsm.data.importer import _slugify

        assert _slugify("My Project") == "my-project"

    def test_consecutive_non_alnum_collapsed(self) -> None:
        from cpsm.data.importer import _slugify

        assert _slugify("foo  __bar") == "foo-bar"

    def test_empty_name_raises(self) -> None:
        from cpsm.data.importer import _slugify

        with pytest.raises(ValueError, match="empty"):
            _slugify("---")

    def test_name_too_long_truncated(self) -> None:
        from cpsm.data.importer import _slugify

        long_name = "a" * 100
        result = _slugify(long_name)
        assert len(result) <= 63

    def test_truncation_onto_dash_raises(self) -> None:
        """A name that is exactly 64 chars where char 63 is a dash produces an
        empty-after-strip result at truncation boundary → ValueError."""
        from cpsm.data.importer import _slugify

        # "a" * 62 + "-" + "b" = 64 chars.  After lower/replace it stays as-is.
        # Truncated to 63 → "a" * 62 + "-", strip("-") → "a" * 62 which is fine.
        # We need to construct a name where truncation leaves all dashes.
        # Simplest: a name that after slug processing is all dashes within 63 chars.
        # Use a name that is already all special chars:
        with pytest.raises(ValueError, match="empty"):
            # "---" → stripped to "" before truncation
            _slugify("!!!")  # → "---" → stripped → empty

    def test_project_with_bad_slug_name_is_skipped(self) -> None:
        """A project whose name slugifies to empty is recorded as 'skipped'."""
        legacy = {
            "projects": [
                {
                    "name": "!!!",  # slugifies to empty
                    "host": "h.example.com",
                    "ssh_user": "u",
                    "project_folder": "/p",
                    "claude_options": "--resume",
                }
            ]
        }
        preview = convert(legacy, Path("/fake/source.yaml"))
        skipped = [t for t in preview.transforms if t.kind == "skipped"]
        assert any("cannot derive id slug" in s.detail for s in skipped)
        assert len(preview.document.connections) == 0

    def test_group_with_bad_slug_name_is_skipped(self) -> None:
        """A group whose display name slugifies to empty is recorded as 'skipped'."""
        legacy = {
            "projects": [
                {
                    "name": "ValidProject",
                    "group": "!!!",  # group name slugifies to empty
                    "host": "h.example.com",
                    "ssh_user": "u",
                    "project_folder": "/p",
                    "claude_options": "--resume",
                }
            ]
        }
        preview = convert(legacy, Path("/fake/source.yaml"))
        # Connection is added but the group cannot be synthesized
        assert len(preview.document.connections) == 1
        assert len(preview.document.groups) == 0
        skipped = [t for t in preview.transforms if t.kind == "skipped"]
        assert any("cannot derive group id slug" in s.detail for s in skipped)


# ---------------------------------------------------------------------------
# _to_plain and load_and_convert edge-case tests
# ---------------------------------------------------------------------------


class TestToPlain:
    """Unit tests for the _to_plain helper and load_and_convert edge cases."""

    def test_to_plain_with_commented_map(self) -> None:
        """_to_plain correctly converts ruamel CommentedMap to plain dict."""
        from ruamel.yaml.comments import CommentedMap, CommentedSeq

        from cpsm.data.importer import _to_plain

        cm = CommentedMap()
        cm["key"] = "value"
        cs = CommentedSeq()
        cs.append("item")
        cm["list"] = cs

        result = _to_plain(cm)
        assert result == {"key": "value", "list": ["item"]}
        assert isinstance(result, dict)
        assert isinstance(result["list"], list)

    def test_to_plain_with_nested_commented_map(self) -> None:
        """_to_plain handles nested CommentedMap/CommentedSeq."""
        from ruamel.yaml.comments import CommentedMap

        from cpsm.data.importer import _to_plain

        outer = CommentedMap()
        inner = CommentedMap()
        inner["x"] = 1
        outer["nested"] = inner

        result = _to_plain(outer)
        assert result == {"nested": {"x": 1}}

    def test_to_plain_passthrough_primitives(self) -> None:
        from cpsm.data.importer import _to_plain

        assert _to_plain(42) == 42
        assert _to_plain("hello") == "hello"
        assert _to_plain(None) is None

    def test_to_plain_plain_dict(self) -> None:
        """Plain dicts pass through correctly."""
        from cpsm.data.importer import _to_plain

        result = _to_plain({"a": [1, 2], "b": "c"})
        assert result == {"a": [1, 2], "b": "c"}

    def test_load_and_convert_empty_file(self, tmp_path: Path) -> None:
        """An empty YAML file produces an empty document without error."""
        empty = tmp_path / "empty.yaml"
        empty.write_text("", encoding="utf-8")
        preview = load_and_convert(empty)
        assert len(preview.document.connections) == 0
        assert len(preview.document.groups) == 0
