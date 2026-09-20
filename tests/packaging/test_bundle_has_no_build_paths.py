# -*- coding: utf-8 -*-
"""
tests/packaging/test_bundle_has_no_build_paths.py — the packaged bundle must
not carry the build machine's filesystem layout.

Why this exists
---------------
`tests/lint/test_no_real_infrastructure.py` keeps developer home paths out of
the *source tree*. It scans tracked text files, which means it is blind to a
compiled artefact — and a compiled artefact is exactly where such a path can
reappear without anyone touching a source file.

That is not hypothetical. The first published AppImage contained two
`__pycache__/*.pyc` files, swept in by `packaging/cpsm.spec`'s directory-wide
`datas` copy, each embedding the absolute path it was compiled from. Nothing
in the source tree had changed; the leak arrived purely through the packaging
step, and only a post-publication audit of the extracted binary found it.

`cpsm.spec` now filters `__pycache__` out of `a.datas`. This test is the
control for that fix.

Scope
-----
Runs only when `dist/cpsm/` exists — i.e. after a real PyInstaller build. It
is skipped in a plain checkout, which is the common case, so it does not force
every contributor to build before testing. CI builds the bundle during the
release job, which is where it matters.

The check is deliberately generic: it looks for *any* absolute home directory,
not one specific developer's, so it protects whoever packages the next
release rather than only the person who caused the original problem.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DIST = REPO_ROOT / "dist" / "cpsm"

# Any absolute home directory, on Linux or macOS.
_HOME_RE = re.compile(rb"/(?:home|Users)/[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-/]*")

# Third-party libraries ship with their own build-host paths baked in and we
# cannot do anything about those. Only CPSM's own bundled files are in scope.
_OURS = "cpsm"

pytestmark = pytest.mark.skipif(
    not DIST.is_dir(),
    reason="no dist/cpsm — run `pyinstaller --noconfirm packaging/cpsm.spec` first",
)


def _our_bundled_files() -> list[Path]:
    internal = DIST / "_internal" / _OURS
    root = internal if internal.is_dir() else DIST / _OURS
    if not root.is_dir():
        return []
    return [p for p in root.rglob("*") if p.is_file()]


def test_bundle_ships_no_pycache() -> None:
    """__pycache__ must not reach the bundle at all.

    This is the direct control on the cpsm.spec filter. It is stricter than
    the path check below — no .pyc should be there regardless of content —
    because the next stray .pyc will embed whatever path built it.
    """
    files = _our_bundled_files()
    assert files, "found no bundled cpsm files; the layout assumption is wrong"

    offenders = [
        str(p.relative_to(DIST))
        for p in files
        if "__pycache__" in p.parts or p.suffix in {".pyc", ".pyo"}
    ]
    assert not offenders, (
        "The packaged bundle contains compiled-bytecode files:\n  "
        + "\n  ".join(offenders)
        + "\n\npackaging/cpsm.spec filters these out of a.datas. If they are "
        "back, that filter was removed or bypassed — they embed the absolute "
        "path of the machine that built them."
    )


def test_bundle_contains_no_absolute_home_path() -> None:
    """No file CPSM ships may contain a developer's home directory."""
    files = _our_bundled_files()
    assert files, "found no bundled cpsm files; the layout assumption is wrong"

    hits: list[str] = []
    for path in files:
        try:
            blob = path.read_bytes()
        except OSError:  # pragma: no cover - unreadable file in a build tree
            continue
        for match in _HOME_RE.findall(blob):
            hits.append(f"{path.relative_to(DIST)}: {match.decode('utf-8', 'replace')}")

    assert not hits, (
        f"The packaged bundle embeds {len(hits)} absolute home path(s):\n  "
        + "\n  ".join(hits[:20])
        + "\n\nThis ships the build machine's filesystem layout to every user. "
        "Usually it means a build artefact was swept into the bundle by a "
        "directory-wide copy in packaging/cpsm.spec."
    )
