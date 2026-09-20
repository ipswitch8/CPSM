# -*- coding: utf-8 -*-
"""Tests for child-process environment sanitising.

Regression cover for a bug that made CPSM unable to open a terminal when run
from a PyInstaller bundle.

PyInstaller's bootloader points LD_LIBRARY_PATH and QT_PLUGIN_PATH at the
bundle's ``_internal`` directory. Every CPSM spawn site inherited os.environ
verbatim, so terminals, tmux and ssh resolved libraries out of CPSM's bundle
instead of the system's. With system Qt 6.6.2 and a bundle carrying Qt 6.11.0,
konsole did not merely misbehave -- it refused to start:

    konsole: symbol lookup error: /lib/x86_64-linux-gnu/libQt6Multimedia.so.6:
             undefined symbol: _ZN14QObjectPrivateC2Ei, version Qt_6_PRIVATE_API

This was previously masked in the AppImage by appimage-builder's AppRun shim,
which scrubbed child environments. That shim leaked ~413 MB/day and was removed
(docs/MEMORY-LEAK-INVESTIGATION.md), which exposed a bug that had always been
present in non-AppImage runs.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import ClassVar

import pytest

from cpsm.platform.child_env import child_env
from cpsm.platform.process_runner import ProcessRunner

BUNDLE = "/tmp/.mount_CPSM-xxxx/usr/bin/_internal"


@pytest.fixture()
def frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend CPSM is running from a PyInstaller bundle.

    Restoration of ``<NAME>_ORIG`` only applies when frozen. A test that
    exercises it without declaring frozen state is asserting behaviour on the
    DEVELOPMENT path, where a stale ``_ORIG`` inherited from an ancestor
    AppImage session would be restored into children that never had the
    variable set — the inverse of what this module is for. See the early
    return in child_env().
    """
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", BUNDLE, raising=False)


class TestRestoresSavedOriginals:
    """PyInstaller saves the pre-launch value as <NAME>_ORIG."""

    def test_restores_original_value(self, frozen: None, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LD_LIBRARY_PATH", BUNDLE)
        monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/opt/custom/lib")

        env = child_env()

        assert env["LD_LIBRARY_PATH"] == "/opt/custom/lib"
        assert "LD_LIBRARY_PATH_ORIG" not in env

    def test_empty_original_means_it_was_unset(
        self, frozen: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An empty saved value means the user had it unset.

        Restoring it as "" would NOT be equivalent: an empty LD_LIBRARY_PATH is
        interpreted by the loader as "search the current directory", which is
        both wrong and a security footgun.
        """
        monkeypatch.setenv("LD_LIBRARY_PATH", BUNDLE)
        monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "")

        env = child_env()

        assert "LD_LIBRARY_PATH" not in env
        assert "LD_LIBRARY_PATH_ORIG" not in env

    def test_qt_plugin_path_restored_too(
        self, frozen: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("QT_PLUGIN_PATH", f"{BUNDLE}/PySide6/Qt/plugins")
        monkeypatch.setenv("QT_PLUGIN_PATH_ORIG", "/usr/lib/qt6/plugins")

        env = child_env()

        assert env["QT_PLUGIN_PATH"] == "/usr/lib/qt6/plugins"


class TestDropsBundlePathsWithoutSavedOriginal:
    """No <NAME>_ORIG, but the value points into our own bundle."""

    def test_drops_value_pointing_into_bundle(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "_MEIPASS", BUNDLE, raising=False)
        monkeypatch.setenv("LD_LIBRARY_PATH", BUNDLE)
        monkeypatch.delenv("LD_LIBRARY_PATH_ORIG", raising=False)

        env = child_env()

        assert "LD_LIBRARY_PATH" not in env

    def test_drops_when_bundle_is_one_entry_among_several(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "_MEIPASS", BUNDLE, raising=False)
        monkeypatch.setenv("LD_LIBRARY_PATH", f"/usr/local/lib:{BUNDLE}")
        monkeypatch.delenv("LD_LIBRARY_PATH_ORIG", raising=False)

        env = child_env()

        # Conservative: the whole value is ours to drop, since we cannot know
        # which entries the user set. A saved _ORIG is the reliable signal, and
        # PyInstaller writes one whenever there was a previous value -- so
        # reaching here means there wasn't.
        assert "LD_LIBRARY_PATH" not in env

    def test_keeps_user_value_not_pointing_into_bundle(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A path unrelated to the bundle is the user's, and is left alone."""
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "_MEIPASS", BUNDLE, raising=False)
        monkeypatch.setenv("LD_LIBRARY_PATH", "/opt/mylib")
        monkeypatch.delenv("LD_LIBRARY_PATH_ORIG", raising=False)

        env = child_env()

        assert env["LD_LIBRARY_PATH"] == "/opt/mylib"


class TestNotFrozenIsPassThrough:
    """Development runs override nothing, so there is nothing to undo."""

    def test_unfrozen_keeps_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delattr(sys, "frozen", raising=False)
        monkeypatch.setenv("LD_LIBRARY_PATH", "/opt/mylib")
        monkeypatch.delenv("LD_LIBRARY_PATH_ORIG", raising=False)

        env = child_env()

        assert env["LD_LIBRARY_PATH"] == "/opt/mylib"

    def test_does_not_mutate_the_source_mapping(self) -> None:
        source = {"LD_LIBRARY_PATH": BUNDLE, "LD_LIBRARY_PATH_ORIG": "/opt/lib"}
        snapshot = dict(source)

        child_env(source)

        assert source == snapshot


class TestProcessRunnerSanitises:
    """The sanitiser must actually be wired into the spawn path."""

    def test_run_strips_bundle_paths_from_child(
        self, frozen: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """End-to-end: a real child process must not see the bundle path."""
        monkeypatch.setenv("LD_LIBRARY_PATH", BUNDLE)
        monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "")

        result = ProcessRunner().run(
            [
                sys.executable,
                "-c",
                "import os; print(os.environ.get('LD_LIBRARY_PATH', '<unset>'))",
            ],
            timeout=60,
        )

        assert result.stdout.strip() == "<unset>", (
            "the child inherited CPSM's bundle library path — a spawned "
            "terminal would resolve Qt out of our bundle and may fail to start"
        )

    def test_explicit_env_is_passed_through_untouched(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An explicit env is the caller's business; do not second-guess it."""
        monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "")

        result = ProcessRunner().run(
            [sys.executable, "-c", "import os; print(os.environ.get('CPSM_MARKER', '<unset>'))"],
            env={"CPSM_MARKER": "explicit", "PATH": os.environ.get("PATH", "")},
            timeout=60,
        )

        assert result.stdout.strip() == "explicit"


class TestTerminalLaunchersSanitise:
    """Every Popen site in terminal_launcher.py must pass a sanitised env."""

    def test_no_unsanitised_popen_remains(self) -> None:
        source = Path(__file__).resolve().parents[2] / "cpsm" / "platform" / "terminal_launcher.py"
        text = source.read_text(encoding="utf-8")

        popen_count = text.count("subprocess.Popen(")
        sanitised_count = text.count("env=child_env()")

        assert popen_count > 0, "no Popen calls found — test needs updating"
        assert sanitised_count >= popen_count, (
            f"{popen_count} Popen call(s) but only {sanitised_count} pass "
            f"env=child_env(); an unsanitised spawn hands the child CPSM's "
            f"bundle library paths"
        )


class TestEverySpawnSiteIsSanitised:
    """Repo-wide guard.

    The per-file check above only covers terminal_launcher.py. Spawn sites
    exist in several modules, and an unsanitised one silently hands a system
    binary CPSM's bundle library paths — which is how konsole came to fail.
    This sweeps the whole package so a NEW spawn site cannot be added without
    either sanitising it or explicitly exempting it here.
    """

    # Files whose direct subprocess calls are exempt, with the reason.
    _EXEMPT: ClassVar[set[str]] = {
        # ProcessRunner IS the sanitiser for everything that goes through it
        # (tmux, ssh via SshBinary, launcher scripts). It applies child_env()
        # itself when the caller passes env=None.
        "cpsm/platform/process_runner.py",
    }

    def test_all_spawn_sites_pass_a_sanitised_env(self) -> None:
        import re

        repo = Path(__file__).resolve().parents[2]
        offenders: list[str] = []

        for path in sorted((repo / "cpsm").rglob("*.py")):
            rel = path.relative_to(repo).as_posix()
            if rel in self._EXEMPT:
                continue
            text = path.read_text(encoding="utf-8")
            # Each spawn call, with the following ~12 lines of its argument
            # list, must mention an env= that resolves to child_env.
            for match in re.finditer(r"(?:subprocess|_subprocess)\.(?:run|Popen)\(", text):
                tail = text[match.start() : match.start() + 600]
                # Stop at the closing of this call, approximated by the next
                # line that is a dedented statement; 600 chars is generous.
                if "env=child_env()" in tail or "env=_child_env()" in tail:
                    continue
                if "env=env" in tail or "env=run_env" in tail:
                    continue
                line = text[: match.start()].count("\n") + 1
                offenders.append(f"{rel}:{line}")

        assert not offenders, (
            "spawn sites without a sanitised env: "
            + ", ".join(offenders)
            + " — each hands the child CPSM's PyInstaller library paths, which "
            "can make a system binary fail to start (konsole does). Pass "
            "env=child_env(), route through ProcessRunner, or add an "
            "explicit exemption with a reason."
        )


class TestDevRunDoesNotResurrectStaleOrig:
    """Regression: a development run must never restore a stale <NAME>_ORIG.

    A shell descended from an AppImage session keeps LD_LIBRARY_PATH_ORIG long
    after the mount is gone. Before the frozen-state gate, child_env() restored
    it, handing every child a dead AppDir path the parent did not even have set
    — the exact inverse of this module's purpose. Reproduced on a real machine
    whose shell was launched from a previous CPSM AppImage.
    """

    def test_stale_orig_is_not_restored_when_not_frozen(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delattr(sys, "frozen", raising=False)
        monkeypatch.delenv("LD_LIBRARY_PATH", raising=False)
        monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/tmp/.mount_cpsm.DEAD/usr/bin/_internal")

        env = child_env()

        assert "LD_LIBRARY_PATH" not in env, (
            "a development run resurrected a stale LD_LIBRARY_PATH_ORIG into "
            "LD_LIBRARY_PATH — children would inherit a dead AppDir path the "
            "parent never had set"
        )

    def test_frozen_run_still_restores(self, frozen: None, monkeypatch: pytest.MonkeyPatch) -> None:
        """The gate must not disable restoration where it IS wanted."""
        monkeypatch.setenv("LD_LIBRARY_PATH", BUNDLE)
        monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/opt/real/lib")

        env = child_env()

        assert env["LD_LIBRARY_PATH"] == "/opt/real/lib"
