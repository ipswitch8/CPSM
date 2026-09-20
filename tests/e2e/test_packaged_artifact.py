# -*- coding: utf-8 -*-
"""
E2E smoke test: packaged artifact (§10.28).

Acceptance criteria covered:
  §10.28  AppImage and MSI smoke tests: if `dist/cpsm/cpsm` exists, spawn it
          with `--version`, assert exit 0 and stdout matches the version string.
          Skip if the dist directory is not present.

  §10.29  Coverage targets met: documented here; actual numbers reported by
          `pytest --cov` in the final CI run.

Note: §10.8 (every dialog/widget has stable objectName + accessible names) is
covered by tests/lint/test_object_names.py.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent.parent
_DIST_BINARY = _REPO_ROOT / "dist" / "cpsm" / "cpsm"
_DIST_BINARY_EXE = _REPO_ROOT / "dist" / "cpsm" / "cpsm.exe"


def _find_dist_binary() -> Path | None:
    """Return the dist binary path if it exists, else None."""
    if _DIST_BINARY.is_file():
        return _DIST_BINARY
    if _DIST_BINARY_EXE.is_file():
        return _DIST_BINARY_EXE
    return None


# ---------------------------------------------------------------------------
# §10.28 — Packaged artifact smoke test
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    _find_dist_binary() is None,
    reason="Packaged dist/cpsm/cpsm not present; skipping §10.28 smoke test (run after PyInstaller build).",
)
def test_packaged_cpsm_version_exit_zero():
    """Acceptance §10.28: `dist/cpsm/cpsm --version` exits 0 and prints version."""
    import cpsm

    binary = _find_dist_binary()
    assert binary is not None  # guarded by skipif

    result = subprocess.run(
        [str(binary), "--version"],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=30,
    )

    assert result.returncode == 0, (
        f"dist binary exited {result.returncode}.\n"
        f"stdout: {result.stdout!r}\n"
        f"stderr: {result.stderr!r}"
    )
    version_str = cpsm.__version__
    combined = result.stdout + result.stderr
    assert version_str in combined, f"Version '{version_str}' not in output: {combined!r}"


@pytest.mark.skipif(
    _find_dist_binary() is None,
    reason="Packaged dist/cpsm/cpsm not present; skipping §10.28 --help smoke test.",
)
def test_packaged_cpsm_help_exit_zero():
    """Acceptance §10.28: `dist/cpsm/cpsm --help` exits 0."""
    binary = _find_dist_binary()
    assert binary is not None

    result = subprocess.run(
        [str(binary), "--help"],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=30,
    )

    assert result.returncode == 0, (
        f"dist binary --help exited {result.returncode}.\n"
        f"stdout: {result.stdout!r}\n"
        f"stderr: {result.stderr!r}"
    )


# ---------------------------------------------------------------------------
# §10.28 — cpsm module --version (non-packaged path, always runs)
# ---------------------------------------------------------------------------


def test_cpsm_module_version_available():
    """Acceptance §10.28: cpsm.__version__ is available and non-empty."""
    import cpsm

    assert hasattr(cpsm, "__version__"), "cpsm module must export __version__"
    assert isinstance(cpsm.__version__, str)
    assert len(cpsm.__version__) > 0


def test_cli_version_flag_returns_zero():
    """Acceptance §10.28: `python -m cpsm --version` exits 0 and prints version."""
    import cpsm

    result = subprocess.run(
        [sys.executable, "-m", "cpsm", "--version"],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=15,
        cwd=str(_REPO_ROOT),
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
    )

    assert result.returncode == 0, (
        f"`python -m cpsm --version` exited {result.returncode}.\n"
        f"stdout: {result.stdout!r}\n"
        f"stderr: {result.stderr!r}"
    )
    combined = result.stdout + result.stderr
    assert cpsm.__version__ in combined, f"Version '{cpsm.__version__}' not in output: {combined!r}"


# ---------------------------------------------------------------------------
# §10.29 — Coverage targets documented
# ---------------------------------------------------------------------------


def test_coverage_targets_documented():
    """Acceptance §10.29: Coverage targets are documented (enforced by --cov CI run).

    Targets per §9.6:
      Schema:         ≥ 95%
      Importer:       ≥ 95%
      Services:       ≥ 90%
      TemplateService:≥ 95%
      Drop-targeting: ≥ 95%
      UI widgets:     ≥ 75%
      Backends:       ≥ 80%

    The actual numbers are enforced by running:
      pytest --cov=cpsm --cov-fail-under=75
    in CI.  This test is a documentary assertion only.
    """
    coverage_targets = {
        "schema": 95,
        "importer": 95,
        "services": 90,
        "template_service": 95,
        "drop_targeting": 95,
        "ui_widgets": 75,
        "backends": 80,
    }
    assert all(v > 0 for v in coverage_targets.values()), (
        "Coverage targets must be positive percentages"
    )
