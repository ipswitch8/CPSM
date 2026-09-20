# -*- coding: utf-8 -*-
"""Regression test for the AppImage memory leak.

CPSM's AppImage leaked ~413 MB/day because appimage-builder's AppRun
LD_PRELOADs `libapprun_hooks.so`, a shim that intercepts open/openat/dlopen to
rewrite AppDir-relative paths. Controlled A/B (scripts/diagnose_appimage_leak.sh):

    hooks INTACT     +1176 KB / 240s  ->  413.4 MB/day
    hooks STUBBED       +0 KB / 240s  ->    0.0 MB/day

The fix is to build with stock appimagetool instead
(scripts/build_appimage_plain.sh), whose AppRun execs the binary directly.
See docs/MEMORY-LEAK-INVESTIGATION.md.

These tests guard the PACKAGING property, because that is where the defect
lives — no CPSM source change can reintroduce or prevent it. The leak itself
cannot be asserted in pytest: it is a continuous background rate requiring a
live GUI process over minutes, which is what the diagnose script is for.

What IS assertable, cheaply and deterministically:
  * the build script must not reintroduce the shim or an LD_PRELOAD
  * a built AppImage, if one is present, must not contain the shim

The second is skipped when no AppImage has been built, so the suite stays
runnable on a clean checkout.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BUILD_SCRIPT = REPO / "scripts" / "build_appimage_plain.sh"

# The shim whose presence IS the bug.
HOOK_LIB = "libapprun_hooks.so"


def _built_appimages() -> list[Path]:
    return sorted(REPO.glob("CPSM-*-x86_64.AppImage"))


class TestBuildScriptDoesNotReintroduceTheShim:
    """Guards the fix at its source: the build recipe itself."""

    def test_build_script_exists(self) -> None:
        assert BUILD_SCRIPT.is_file(), (
            f"{BUILD_SCRIPT.relative_to(REPO)} is missing — the AppImage fix has "
            f"been removed. See docs/MEMORY-LEAK-INVESTIGATION.md."
        )

    def test_apprun_template_has_no_preload_or_shim(self) -> None:
        """The AppRun this script writes must exec the binary directly."""
        text = BUILD_SCRIPT.read_text(encoding="utf-8")

        # Isolate the heredoc that produces AppRun, so prose in the file's
        # explanatory comments (which necessarily MENTION LD_PRELOAD and the
        # shim) cannot make this test pass or fail spuriously.
        start = text.index("<<'APPRUN'")
        end = text.index("APPRUN", start + len("<<'APPRUN'"))
        apprun = text[start:end]

        assert "LD_PRELOAD" not in apprun, (
            "the generated AppRun sets LD_PRELOAD — this is how the leaking "
            "shim gets loaded"
        )
        assert HOOK_LIB not in apprun, (
            f"the generated AppRun references {HOOK_LIB}, the library that leaks "
            f"~413 MB/day"
        )
        assert "exec " in apprun, "the generated AppRun must exec the binary directly"

    def test_build_script_uses_appimagetool_not_appimage_builder(self) -> None:
        text = BUILD_SCRIPT.read_text(encoding="utf-8")
        assert "appimagetool" in text
        # appimage-builder is what ships the shim. It is fine for the script to
        # mention it in comments explaining WHY it is not used; what must not
        # happen is invoking it.
        assert "appimage-builder --recipe" not in text, (
            "the plain build script invokes appimage-builder, which reintroduces "
            "the leaking AppRun shim"
        )


@pytest.mark.skipif(
    not _built_appimages(),
    reason="no CPSM-*-x86_64.AppImage built; run scripts/build_appimage_plain.sh",
)
class TestBuiltAppImageIsFreeOfTheShim:
    """Guards the actual artifact, when one exists to check."""

    @pytest.fixture(scope="class")
    def extracted(self, tmp_path_factory: pytest.TempPathFactory) -> Path:
        appimage = _built_appimages()[-1]
        workdir = tmp_path_factory.mktemp("appimage")
        result = subprocess.run(
            [str(appimage), "--appimage-extract"],
            cwd=workdir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
        )
        if result.returncode != 0:
            pytest.skip(f"--appimage-extract failed: {result.stderr[:200]}")
        root = workdir / "squashfs-root"
        if not root.is_dir():
            pytest.skip("extraction produced no squashfs-root")
        return root

    def test_no_hook_shim_in_bundle(self, extracted: Path) -> None:
        found = list(extracted.rglob(HOOK_LIB))
        assert not found, (
            f"{HOOK_LIB} is present at {[str(p.relative_to(extracted)) for p in found]} "
            f"— this AppImage was built with appimage-builder and will leak "
            f"~413 MB/day. Build with scripts/build_appimage_plain.sh instead."
        )

    def test_apprun_does_not_preload_anything(self, extracted: Path) -> None:
        apprun = extracted / "AppRun"
        if not apprun.exists():
            pytest.skip("no AppRun in the extracted AppDir")
        # AppRun may be a binary (appimage-builder) or a script (plain build);
        # read bytes and look for the marker either way.
        blob = apprun.read_bytes()
        assert b"LD_PRELOAD" not in blob, (
            "AppRun sets LD_PRELOAD — appimage-builder's shim machinery is back"
        )

    def test_no_bundled_compat_glibc(self, extracted: Path) -> None:
        """appimage-builder ships runtime/compat with its own loader and libc.

        Not the cause of the leak (a 2.35 -> 2.39 rebuild changed nothing), but
        its presence is a reliable marker that appimage-builder produced this
        artifact.
        """
        assert not (extracted / "runtime" / "compat").is_dir(), (
            "runtime/compat is present — this AppImage was built with "
            "appimage-builder"
        )
