# -*- coding: utf-8 -*-
"""
Tests for cpsm.services.import_service.

Covers:
  - import_legacy (dry_run=True and False)
  - import_legacy_to (writes output, never touches source)
  - ImportService class wrappers
  - Source-file immutability: mtime, inode, write-blocker monkeypatch
  - FileNotFoundError when source is missing
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from cpsm.data.importer import ImportPreview
from cpsm.services.import_service import ImportService, import_legacy, import_legacy_to

# ---------------------------------------------------------------------------
# Fixture path
# ---------------------------------------------------------------------------

FIXTURE_PATH = Path(__file__).parent.parent / "data" / "fixtures" / "example-.claude-projects.yaml"


# ---------------------------------------------------------------------------
# import_legacy — dry_run=True (default)
# ---------------------------------------------------------------------------


class TestImportLegacyDryRun:
    def test_returns_import_preview(self, tmp_path: Path) -> None:
        src = tmp_path / "legacy.yaml"
        shutil.copy2(FIXTURE_PATH, src)
        preview = import_legacy(src, dry_run=True)
        assert isinstance(preview, ImportPreview)

    def test_document_has_connections(self, tmp_path: Path) -> None:
        src = tmp_path / "legacy.yaml"
        shutil.copy2(FIXTURE_PATH, src)
        preview = import_legacy(src)
        assert len(preview.document.connections) == 14

    def test_source_not_modified_mtime(self, tmp_path: Path) -> None:
        src = tmp_path / "legacy.yaml"
        shutil.copy2(FIXTURE_PATH, src)
        stat_before = os.stat(src)
        import_legacy(src, dry_run=True)
        stat_after = os.stat(src)
        assert stat_after.st_mtime == stat_before.st_mtime

    def test_source_not_modified_inode(self, tmp_path: Path) -> None:
        src = tmp_path / "legacy.yaml"
        shutil.copy2(FIXTURE_PATH, src)
        stat_before = os.stat(src)
        import_legacy(src, dry_run=True)
        stat_after = os.stat(src)
        assert stat_after.st_ino == stat_before.st_ino

    def test_source_write_blocker(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        src = tmp_path / "legacy.yaml"
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
        # Must succeed without triggering the write blocker
        preview = import_legacy(src)
        assert len(preview.document.connections) == 14

    def test_missing_source_raises_file_not_found(self, tmp_path: Path) -> None:
        missing = tmp_path / "nonexistent.yaml"
        with pytest.raises(FileNotFoundError):
            import_legacy(missing)

    def test_document_validates(self, tmp_path: Path) -> None:
        src = tmp_path / "legacy.yaml"
        shutil.copy2(FIXTURE_PATH, src)
        preview = import_legacy(src)
        try:
            from cpsm.data.schema import CpsmDocument

            CpsmDocument.model_validate(preview.document.model_dump())
        except ValidationError as exc:
            pytest.fail(f"Document failed validation: {exc}")


# ---------------------------------------------------------------------------
# import_legacy_to — writes output
# ---------------------------------------------------------------------------


class TestImportLegacyTo:
    def test_creates_output_file(self, tmp_path: Path) -> None:
        src = tmp_path / "legacy.yaml"
        shutil.copy2(FIXTURE_PATH, src)
        target = tmp_path / "out.cpsm.yaml"

        import_legacy_to(src, target)
        assert target.exists()

    def test_output_is_valid_yaml(self, tmp_path: Path) -> None:
        src = tmp_path / "legacy.yaml"
        shutil.copy2(FIXTURE_PATH, src)
        target = tmp_path / "out.cpsm.yaml"

        import_legacy_to(src, target)

        from ruamel.yaml import YAML

        yaml = YAML(typ="safe")
        with open(target, encoding="utf-8") as fh:
            data = yaml.load(fh)
        assert data is not None
        assert "connections" in data

    def test_source_mtime_unchanged_after_write(self, tmp_path: Path) -> None:
        src = tmp_path / "legacy.yaml"
        shutil.copy2(FIXTURE_PATH, src)
        target = tmp_path / "out.cpsm.yaml"

        stat_before = os.stat(src)
        import_legacy_to(src, target)
        stat_after = os.stat(src)

        assert stat_after.st_mtime == stat_before.st_mtime

    def test_source_inode_unchanged_after_write(self, tmp_path: Path) -> None:
        src = tmp_path / "legacy.yaml"
        shutil.copy2(FIXTURE_PATH, src)
        target = tmp_path / "out.cpsm.yaml"

        stat_before = os.stat(src)
        import_legacy_to(src, target)
        stat_after = os.stat(src)

        assert stat_after.st_ino == stat_before.st_ino

    def test_source_write_blocker_with_output(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Even when writing to a different target, the source must not be written."""
        src = tmp_path / "legacy.yaml"
        shutil.copy2(FIXTURE_PATH, src)
        src_resolved = src.resolve()
        target = tmp_path / "out.cpsm.yaml"

        original_open = open

        def _guarded_open(file: object, mode: str = "r", **kwargs: object) -> object:
            path_str = str(file)
            if str(src_resolved) in path_str and ("w" in str(mode) or "a" in str(mode)):
                raise PermissionError(f"Write-blocker: write to source file blocked: {file!r}")
            return original_open(file, mode, **kwargs)  # type: ignore[call-overload]

        monkeypatch.setattr("builtins.open", _guarded_open)
        preview = import_legacy_to(src, target)
        assert target.exists()
        assert len(preview.document.connections) == 14

    def test_returns_preview(self, tmp_path: Path) -> None:
        src = tmp_path / "legacy.yaml"
        shutil.copy2(FIXTURE_PATH, src)
        target = tmp_path / "out.cpsm.yaml"

        preview = import_legacy_to(src, target)
        assert isinstance(preview, ImportPreview)
        assert len(preview.document.connections) == 14

    def test_missing_source_raises(self, tmp_path: Path) -> None:
        missing = tmp_path / "nope.yaml"
        target = tmp_path / "out.cpsm.yaml"
        with pytest.raises(FileNotFoundError):
            import_legacy_to(missing, target)

    def test_output_file_permissions_0600(self, tmp_path: Path) -> None:
        """On Linux the output file must have mode 0600 (set by Repository.save)."""
        import sys

        if sys.platform == "win32":
            pytest.skip("Permission check is Linux-only")

        src = tmp_path / "legacy.yaml"
        shutil.copy2(FIXTURE_PATH, src)
        target = tmp_path / "out.cpsm.yaml"

        import_legacy_to(src, target)
        mode = oct(os.stat(target).st_mode & 0o777)
        assert mode == oct(0o600), f"Expected 0600, got {mode}"

    def test_roundtrip_via_repository_load(self, tmp_path: Path) -> None:
        """The saved file must be loadable by CpsmRepository and yield the same
        document structure."""
        src = tmp_path / "legacy.yaml"
        shutil.copy2(FIXTURE_PATH, src)
        target = tmp_path / "out.cpsm.yaml"

        preview = import_legacy_to(src, target)

        from cpsm.data.repository import CpsmRepository

        repo = CpsmRepository()
        loaded = repo.load(target)

        assert len(loaded.connections) == len(preview.document.connections)
        assert len(loaded.groups) == len(preview.document.groups)


# ---------------------------------------------------------------------------
# ImportService class wrappers
# ---------------------------------------------------------------------------


class TestImportServiceClass:
    def test_import_legacy_delegates_correctly(self, tmp_path: Path) -> None:
        src = tmp_path / "legacy.yaml"
        shutil.copy2(FIXTURE_PATH, src)
        svc = ImportService()
        preview = svc.import_legacy(src, dry_run=True)
        assert isinstance(preview, ImportPreview)
        assert len(preview.document.connections) == 14

    def test_import_legacy_to_delegates_correctly(self, tmp_path: Path) -> None:
        src = tmp_path / "legacy.yaml"
        shutil.copy2(FIXTURE_PATH, src)
        target = tmp_path / "out.cpsm.yaml"
        svc = ImportService()
        preview = svc.import_legacy_to(src, target)
        assert target.exists()
        assert len(preview.document.connections) == 14

    def test_service_source_immutability(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        src = tmp_path / "legacy.yaml"
        shutil.copy2(FIXTURE_PATH, src)
        src_resolved = src.resolve()

        original_open = open

        def _guarded_open(file: object, mode: str = "r", **kwargs: object) -> object:
            path_str = str(file)
            if str(src_resolved) in path_str and ("w" in str(mode) or "a" in str(mode)):
                raise PermissionError(f"Write-blocker fired: {file!r}")
            return original_open(file, mode, **kwargs)  # type: ignore[call-overload]

        monkeypatch.setattr("builtins.open", _guarded_open)
        svc = ImportService()
        preview = svc.import_legacy(src)
        assert len(preview.document.connections) == 14


# ---------------------------------------------------------------------------
# Determinism through the service layer
# ---------------------------------------------------------------------------


class TestServiceDeterminism:
    def test_two_dry_run_calls_identical(self, tmp_path: Path) -> None:
        src = tmp_path / "legacy.yaml"
        shutil.copy2(FIXTURE_PATH, src)
        p1 = import_legacy(src)
        p2 = import_legacy(src)
        assert p1.document.model_dump() == p2.document.model_dump()
        assert len(p1.transforms) == len(p2.transforms)
        for t1, t2 in zip(p1.transforms, p2.transforms):
            assert t1 == t2

    def test_dry_run_and_write_produce_same_document(self, tmp_path: Path) -> None:
        src = tmp_path / "legacy.yaml"
        shutil.copy2(FIXTURE_PATH, src)
        target = tmp_path / "out.cpsm.yaml"

        p_dry = import_legacy(src)
        p_write = import_legacy_to(src, target)

        assert p_dry.document.model_dump() == p_write.document.model_dump()
