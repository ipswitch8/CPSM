# -*- coding: utf-8 -*-
"""install.sh and packaging/install.sh must not drift apart.

Two copies of the installer is one too many.  They had already diverged once,
silently and in the dangerous direction: packaging/install.sh passed
``--executable`` to ``cpsm install-desktop`` (pinning the .desktop Exec= line
to the persistent symlink) while the repo-root copy did not.  The root copy is
the one the README tells people to run, so the fix lived in the copy nobody
invoked.

A user running the root copy therefore got a launcher whose Exec= was resolved
from PATH, which is how a successful install can leave the desktop entry
pointing at an older install.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _extract_function(name: str) -> str:
    """Return the source text of shell function *name* from install.sh."""
    import re

    text = (ROOT / "install.sh").read_text(encoding="utf-8")
    m = re.search(rf"^{name}\(\) \{{$", text, re.M)
    assert m, f"{name}() not found in install.sh"
    start = m.start()
    end = text.index("\n}\n", start) + len("\n}\n")
    return text[start:end]


def _digest(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


class TestInstallerCopiesAgree:
    def test_both_installers_exist(self):
        assert (ROOT / "install.sh").is_file()
        assert (ROOT / "packaging" / "install.sh").is_file()

    def test_installers_are_identical(self):
        a = ROOT / "install.sh"
        b = ROOT / "packaging" / "install.sh"
        assert _digest(a) == _digest(b), (
            "install.sh and packaging/install.sh have drifted. They are "
            "distributed as the same script; a fix applied to one and not the "
            "other means the copy a user actually runs may be the stale one. "
            "Copy whichever is correct over the other."
        )

    def test_desktop_registration_pins_the_executable(self):
        """Exec= must not be resolved from PATH.

        Inside the running AppImage, install-desktop's shutil.which("cpsm")
        returns a transient FUSE mount that disappears on exit; under sudo it
        returns root's PATH rather than the user's.  Both produce a launcher
        pointing somewhere wrong.
        """
        text = (ROOT / "install.sh").read_text(encoding="utf-8")
        assert "install-desktop" in text
        assert "--executable" in text, (
            "install-desktop is invoked without --executable, so Exec= is resolved from PATH"
        )

    def test_system_install_registers_for_every_user(self):
        """A system-wide install must not target one user's home.

        This test previously asserted only `"SUDO_USER" in text`, over the
        WHOLE file.  That was vacuous by the time the code moved on: the
        `sudo -u "$SUDO_USER"` dance in register_desktop was deleted in favour
        of `install-desktop --system`, but SUDO_USER still appears elsewhere
        (resolving the invoking user's home for the cleanup), so the
        assertion kept passing with the regression reintroduced.  Verified:
        reverting register_desktop to the single-user form left this green.

        It now inspects register_desktop's own body, which is the code the
        docstring is actually about.
        """
        body = _extract_function("register_desktop")
        assert "--system" in body, (
            "register_desktop no longer installs the launcher system-wide, so "
            "only one user would get it"
        )
        assert "sudo -u" not in body, (
            "register_desktop targets a single invoking user again instead of "
            "installing system-wide"
        )
