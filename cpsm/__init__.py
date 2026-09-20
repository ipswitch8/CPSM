# -*- coding: utf-8 -*-
"""CPSM — Cross-Platform Session Manager."""

from __future__ import annotations

import subprocess
from pathlib import Path

__version__ = "0.2.1"
__all__ = ["__version__", "build_id", "version_string"]


def _source_root() -> Path:
    """Directory that would contain .git when running from a checkout."""
    return Path(__file__).resolve().parent.parent


def _read_stamp() -> str:
    """The commit recorded at package time, or "" if there is no stamp."""
    try:
        from cpsm import _build_stamp  # type: ignore[attr-defined]

        return str(getattr(_build_stamp, "COMMIT", "") or "")
    except Exception:
        return ""


def _git_describe(root: Path) -> str:
    """Current commit of *root*, ``-dirty`` when the tree has changes."""
    try:
        # Imported here, not at module scope: cpsm.platform pulls in the rest
        # of the package, and this module is the package root.
        from cpsm.platform.child_env import child_env

        # A frozen build points LD_LIBRARY_PATH at its own bundled libraries,
        # which can stop a system binary starting at all.  git is a system
        # binary like any other, so it gets the same sanitised environment
        # every other spawn site uses.
        env = child_env()

        def _git(*args: str) -> tuple[int, str]:
            r = subprocess.run(
                ["git", *args],
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=5,
                env=env,
            )
            return r.returncode, r.stdout.strip()

        rc, commit = _git("rev-parse", "--short", "HEAD")
        if rc != 0 or not commit:
            return ""
        rc_d, dirty = _git("status", "--porcelain")
        if rc_d == 0 and dirty:
            commit += "-dirty"
        return commit
    except Exception:
        return ""


def build_id() -> str:
    """Identify the code actually running, so a stale binary can be spotted.

    ``__version__`` alone cannot answer "am I running the build with the fix
    in it": it moves only on release, so every build between two releases
    reports the same string.  This returns the commit instead.

    WHICH SOURCE WINS, AND WHY IT IS NOT THE STAMP

    Packaging writes ``cpsm/_build_stamp.py`` INTO the source tree, and this
    project builds in place.  So after any build the tree permanently contains
    a stamp naming the commit that build was made from.  Preferring it meant
    every later source run reported that frozen commit no matter how far the
    working tree had moved -- silently, and with no ``-dirty`` marker, because
    the stamp records a build that was clean at the time.  A stamp that lies
    is worse than no stamp at all: the whole point is to be able to trust it.

    So the question asked first is "is this a checkout?", not "is there a
    stamp?".  A checkout has ``.git`` and git is authoritative there.  A frozen
    or installed artifact has no ``.git``, cannot consult git, and its stamp
    cannot be stale because it was written by the build that produced it.

    In a checkout where git cannot be run, the answer is ``"unknown"`` rather
    than the stamp: without git there is no way to tell whether that stamp
    still describes the tree, and reporting a maybe-stale commit as fact is
    the exact failure this avoids.

    Never raises -- a version string is not worth crashing a launch over.
    """
    try:
        root = _source_root()
        if (root / ".git").exists():
            # Source checkout: git decides, and says so when the tree is dirty.
            return _git_describe(root) or "unknown"
        # Packaged artifact: the stamp is the only thing that can know.
        return _read_stamp() or "unknown"
    except Exception:
        return "unknown"


def version_string() -> str:
    """``0.2.0 (build 1a2b3c4)`` — what --version and the About dialog show."""
    return f"{__version__} (build {build_id()})"
