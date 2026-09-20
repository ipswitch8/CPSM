# -*- coding: utf-8 -*-
"""
Tests for cpsm.data.repository.

Covers:
- Load / save round-trip preserving comments and key order
- Atomic write (temp file used, then renamed)
- 0600 permissions on Linux
- Path resolution priority order
- Null connection_id preserved as 'null' (not omitted or 'None')
- load_or_create on missing file
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from cpsm.data.repository import CpsmRepository, resolve_config_path
from cpsm.data.schema import CpsmDocument

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FIXTURES = Path(__file__).parent / "fixtures"
VALID_CPSM = FIXTURES / "valid-cpsm.yaml"


def _read_bytes(path: Path) -> bytes:
    return path.read_bytes()


# ---------------------------------------------------------------------------
# Path resolution tests
# ---------------------------------------------------------------------------


class TestResolveConfigPath:
    def test_explicit_path_takes_priority(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CPSM_CONFIG", str(tmp_path / "env.yaml"))
        explicit = tmp_path / "explicit.yaml"
        result = resolve_config_path(explicit)
        assert result == explicit.resolve()

    def test_cpsm_config_env_second_priority(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env_path = tmp_path / "env-config.yaml"
        monkeypatch.setenv("CPSM_CONFIG", str(env_path))
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        result = resolve_config_path(None)
        assert result == env_path.resolve()

    def test_xdg_config_home_third_priority(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("CPSM_CONFIG", raising=False)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
        if sys.platform != "win32":
            result = resolve_config_path(None)
            assert "cpsm" in str(result)
            assert result.name == ".cpsm.yaml"

    def test_home_fallback(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """On Linux with no XDG_CONFIG_HOME, falls through to ~/.cpsm.yaml per §2.1."""
        monkeypatch.delenv("CPSM_CONFIG", raising=False)
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        if sys.platform != "win32":
            result = resolve_config_path(None)
            assert result == Path.home() / ".cpsm.yaml"

    def test_home_fallback_when_appdata_unset_on_windows(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """On Windows with no APPDATA, falls through to ~/.cpsm.yaml per §2.1."""
        monkeypatch.delenv("CPSM_CONFIG", raising=False)
        monkeypatch.delenv("APPDATA", raising=False)
        if sys.platform == "win32":
            result = resolve_config_path(None)
            assert result == Path.home() / ".cpsm.yaml"

    def test_env_priority_over_xdg(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        env_path = tmp_path / "via-env.yaml"
        monkeypatch.setenv("CPSM_CONFIG", str(env_path))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "via-xdg"))
        result = resolve_config_path(None)
        assert result == env_path.resolve()


# ---------------------------------------------------------------------------
# Repository: load and save round-trip
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_load_valid_fixture(self) -> None:
        repo = CpsmRepository()
        doc = repo.load(VALID_CPSM)
        assert isinstance(doc, CpsmDocument)
        assert doc.schema_version == 1
        assert len(doc.connections) > 0

    def test_save_and_reload_preserves_structure(self, tmp_path: Path) -> None:
        """Load fixture, save to temp, reload — document is structurally identical."""
        repo = CpsmRepository()
        doc = repo.load(VALID_CPSM)

        out_path = tmp_path / ".cpsm.yaml"
        repo.save(doc, out_path)

        repo2 = CpsmRepository()
        doc2 = repo2.load(out_path)

        # Key structural checks
        assert doc2.schema_version == doc.schema_version
        assert len(doc2.connections) == len(doc.connections)
        assert len(doc2.groups) == len(doc.groups)
        assert len(doc2.screen_layouts) == len(doc.screen_layouts)
        assert len(doc2.scenes) == len(doc.scenes)

    def test_save_round_trip_byte_identity(self, tmp_path: Path) -> None:
        """Load fixture, save, reload and resave — second save is byte-identical to first.

        We cannot guarantee the first save is byte-identical to the original fixture
        (ruamel may reformat marginally), but a load→save→load→save cycle MUST be
        stable.  This is the practical definition of 'round-trip preserving comments
        and key order'.
        """
        import difflib

        repo1 = CpsmRepository()
        doc1 = repo1.load(VALID_CPSM)
        save1_path = tmp_path / "save1.yaml"
        repo1.save(doc1, save1_path)

        # Second load+save from the already-saved file
        repo2 = CpsmRepository()
        doc2 = repo2.load(save1_path)
        save2_path = tmp_path / "save2.yaml"
        repo2.save(doc2, save2_path)

        text1 = save1_path.read_text(encoding="utf-8")
        text2 = save2_path.read_text(encoding="utf-8")

        if text1 != text2:
            diff = list(
                difflib.unified_diff(
                    text1.splitlines(keepends=True),
                    text2.splitlines(keepends=True),
                    fromfile="save1.yaml",
                    tofile="save2.yaml",
                )
            )
            pytest.fail("Round-trip is not stable:\n" + "".join(diff))

    def test_comments_survive_round_trip(self, tmp_path: Path) -> None:
        """Comments written into a file must survive load+save."""
        commented_yaml = (
            "# Top-level comment\n"
            "schema_version: 1\n"
            "# Settings comment\n"
            "settings:\n"
            "  default_multiplexer: tmux  # inline comment\n"
            "ssh_keys: []\n"
            "connections: []\n"
            "groups: []\n"
            "screen_layouts: []\n"
            "scenes: []\n"
            "launch_templates: []\n"
        )
        source = tmp_path / "commented.yaml"
        source.write_text(commented_yaml, encoding="utf-8")

        repo = CpsmRepository()
        doc = repo.load(source)
        out = tmp_path / "out.yaml"
        repo.save(doc, out)

        saved = out.read_text(encoding="utf-8")
        assert "# Top-level comment" in saved
        assert "# Settings comment" in saved
        assert "inline comment" in saved

    def test_null_connection_id_preserved_as_null(self, tmp_path: Path) -> None:
        """A pane with connection_id: null must survive as YAML null, not 'None' or omitted."""
        yaml_content = (
            "schema_version: 1\n"
            "settings: {}\n"
            "ssh_keys:\n"
            "  - id: key-prod\n"
            "    name: Key\n"
            "    type: ed25519\n"
            "    private_path: ~/.ssh/id_ed25519\n"
            "    public_path: ~/.ssh/id_ed25519.pub\n"
            "connections:\n"
            "  - id: web01\n"
            "    name: WebApp\n"
            "    launch_profile: claude-remote\n"
            "    host: dev.example.com\n"
            "    port: 22\n"
            "    user: ubuntu\n"
            "    identity_file_ref: key-prod\n"
            "    project_folder: /opt/app\n"
            "    claude_options: '--resume'\n"
            "groups: []\n"
            "screen_layouts:\n"
            "  - id: layout01\n"
            "    name: Layout\n"
            "    monitors:\n"
            "      - viewports:\n"
            "          - id: vp01\n"
            "            geometry_pct: {x: 0, y: 0, w: 100, h: 100}\n"
            "            panes:\n"
            "              - connection_id: null\n"
            "              - connection_id: web01\n"
            "scenes: []\n"
            "launch_templates: []\n"
        )
        source = tmp_path / "null-test.yaml"
        source.write_text(yaml_content, encoding="utf-8")

        repo = CpsmRepository()
        doc = repo.load(source)

        # Verify the model has the null pane
        panes = doc.screen_layouts[0].monitors[0].viewports[0].panes
        assert panes[0].connection_id is None
        assert panes[1].connection_id == "web01"

        out = tmp_path / "null-out.yaml"
        repo.save(doc, out)

        saved = out.read_text(encoding="utf-8")
        # The null pane must appear as a connection_id key (either as empty scalar
        # 'connection_id:' or explicit 'connection_id: null' — both are valid YAML null).
        # It must NOT be omitted entirely and must NOT contain Python's "None" string.
        assert "connection_id:" in saved
        assert "connection_id: None" not in saved

        # Most importantly: reloading the saved file must still produce a None pane
        repo2 = CpsmRepository()
        doc2 = repo2.load(out)
        panes2 = doc2.screen_layouts[0].monitors[0].viewports[0].panes
        assert panes2[0].connection_id is None
        assert panes2[1].connection_id == "web01"


# ---------------------------------------------------------------------------
# Repository: atomic write
# ---------------------------------------------------------------------------


class TestAtomicWrite:
    def test_atomic_write_uses_tmp_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify that save() writes through a .tmp file (not directly to target)."""
        target = tmp_path / ".cpsm.yaml"
        observed_renames: list[tuple[str, str]] = []

        original_replace = os.replace

        def spy_replace(src: str, dst: str) -> None:
            observed_renames.append((src, dst))
            original_replace(src, dst)

        monkeypatch.setattr(os, "replace", spy_replace)

        repo = CpsmRepository()
        doc = repo.load(VALID_CPSM)
        repo.save(doc, target)

        assert len(observed_renames) == 1
        src, dst = observed_renames[0]
        assert str(src).endswith(".tmp")
        assert Path(dst) == target

    def test_tmp_file_cleaned_up_on_success(self, tmp_path: Path) -> None:
        """After a successful save, no .tmp file should remain."""
        target = tmp_path / ".cpsm.yaml"
        repo = CpsmRepository()
        doc = repo.load(VALID_CPSM)
        repo.save(doc, target)

        tmp_file = target.with_suffix(".yaml.tmp")
        assert not tmp_file.exists()

    def test_no_path_raises(self) -> None:
        repo = CpsmRepository()
        doc = CpsmDocument()
        with pytest.raises(RuntimeError, match="No path supplied"):
            repo.save(doc)


# ---------------------------------------------------------------------------
# Repository: file permissions (Linux only)
# ---------------------------------------------------------------------------


class TestFilePermissions:
    @pytest.mark.skipif(sys.platform == "win32", reason="0600 perms test is Linux-only")
    def test_saved_file_is_0600(self, tmp_path: Path) -> None:
        target = tmp_path / ".cpsm.yaml"
        repo = CpsmRepository()
        doc = repo.load(VALID_CPSM)
        repo.save(doc, target)

        mode = oct(os.stat(target).st_mode)[-3:]
        assert mode == "600", f"Expected 0600, got {mode}"


class TestEdgeCases:
    def test_load_empty_yaml_file(self, tmp_path: Path) -> None:
        """Loading a completely empty YAML file should produce a default document."""
        empty = tmp_path / "empty.yaml"
        empty.write_text("", encoding="utf-8")
        repo = CpsmRepository()
        doc = repo.load(empty)
        assert isinstance(doc, CpsmDocument)
        assert doc.schema_version == 1

    def test_atomic_write_cleanup_on_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If os.replace fails, the .tmp file should be cleaned up."""
        target = tmp_path / ".cpsm.yaml"

        def fail_replace(src: object, dst: object) -> None:
            raise OSError("simulated failure")

        monkeypatch.setattr(os, "replace", fail_replace)

        repo = CpsmRepository()
        doc = repo.load(VALID_CPSM)
        with pytest.raises(OSError, match="simulated"):
            repo.save(doc, target)

        # .tmp file should be gone
        tmp_file = target.with_suffix(".yaml.tmp")
        assert not tmp_file.exists()


# ---------------------------------------------------------------------------
# Repository: load_or_create
# ---------------------------------------------------------------------------


class TestLoadOrCreate:
    def test_load_existing_file(self, tmp_path: Path) -> None:
        import shutil

        dest = tmp_path / ".cpsm.yaml"
        shutil.copy(VALID_CPSM, dest)
        repo = CpsmRepository()
        doc = repo.load_or_create(dest)
        assert doc.schema_version == 1

    def test_create_when_missing(self, tmp_path: Path) -> None:
        path = tmp_path / ".cpsm.yaml"
        assert not path.exists()
        repo = CpsmRepository()
        doc = repo.load_or_create(path)
        # Should return a default document
        assert isinstance(doc, CpsmDocument)
        assert doc.schema_version == 1
        assert doc.connections == []


# ---------------------------------------------------------------------------
# Repository: serialize_to_string
# ---------------------------------------------------------------------------


class TestSerializeToString:
    def test_serializes_document(self) -> None:
        repo = CpsmRepository()
        repo.load(VALID_CPSM)
        text = repo.serialize_to_string()
        assert "schema_version" in text
        assert "connections" in text

    def test_serializes_explicit_document(self) -> None:
        repo = CpsmRepository()
        doc = CpsmDocument()
        text = repo.serialize_to_string(doc)
        assert "schema_version" in text

    def test_no_document_raises(self) -> None:
        repo = CpsmRepository()
        with pytest.raises(RuntimeError, match="No document"):
            repo.serialize_to_string()


# ---------------------------------------------------------------------------
# Repository: properties
# ---------------------------------------------------------------------------


class TestRepositoryProperties:
    def test_document_property_none_initially(self) -> None:
        repo = CpsmRepository()
        assert repo.document is None

    def test_raw_property_none_initially(self) -> None:
        repo = CpsmRepository()
        assert repo.raw is None

    def test_document_property_after_load(self) -> None:
        repo = CpsmRepository()
        doc = repo.load(VALID_CPSM)
        assert repo.document is doc

    def test_raw_property_after_load(self) -> None:
        repo = CpsmRepository()
        repo.load(VALID_CPSM)
        assert repo.raw is not None

    def test_save_without_prior_raw_uses_fresh_tree(self, tmp_path: Path) -> None:
        """save() on a repo that has no cached raw tree must still work."""
        repo = CpsmRepository()
        doc = CpsmDocument()
        # Set internal _path directly to simulate a repo that never loaded
        out = tmp_path / ".cpsm.yaml"
        repo.save(doc, out)
        assert out.exists()


# ---------------------------------------------------------------------------
# Migrations tests
# ---------------------------------------------------------------------------


class TestMigrations:
    def test_empty_migrations_list(self) -> None:
        from cpsm.data.migrations import MIGRATIONS

        assert MIGRATIONS == []

    def test_run_migrations_noop_on_current_version(self) -> None:
        from cpsm.data.migrations import run_migrations

        doc = {"schema_version": 1, "connections": []}
        result = run_migrations(doc, target_version=1)
        assert result["schema_version"] == 1

    def test_run_migrations_returns_same_doc(self) -> None:
        from cpsm.data.migrations import run_migrations

        doc = {"schema_version": 1}
        result = run_migrations(doc)
        assert result is doc

    def test_migration_abc_cannot_instantiate(self) -> None:
        from cpsm.data.migrations import Migration

        with pytest.raises(TypeError):
            Migration()  # type: ignore[abstract]

    def test_migration_subclass_apply(self) -> None:
        from typing import Any

        from cpsm.data.migrations import Migration, run_migrations

        class FakeV1Migration(Migration):
            from_version = 1

            def apply(self, doc: dict[str, Any]) -> None:
                doc["migrated"] = True

        from cpsm.data import migrations as mig_module

        original = mig_module.MIGRATIONS[:]
        mig_module.MIGRATIONS.append(FakeV1Migration())
        try:
            doc = {"schema_version": 1}
            run_migrations(doc, target_version=2)
            assert doc.get("migrated") is True
            assert doc["schema_version"] == 2
        finally:
            mig_module.MIGRATIONS[:] = original
