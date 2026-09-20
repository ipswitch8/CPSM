# -*- coding: utf-8 -*-
"""The installer's two modes must each use exactly the privilege they need.

Reported defects, all three fixed here:

  * a PER-USER install ran sudo for file placement, so it wrote root-owned
    files into the user's own home -- which then could not be replaced or
    removed without sudo.  That is how a stale root-owned AppImage got stuck
    in ~/.local/opt/cpsm.
  * a SYSTEM-WIDE install registered the desktop entry for whoever typed
    sudo, so "install for all users" gave exactly one user a launcher.
  * the mode was decided silently by whether sudo happened to be typed.

These run the real function text out of install.sh, so they cannot drift from
what ships.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = ROOT / "install.sh"


def _extract_function(name: str) -> str:
    text = INSTALL_SH.read_text(encoding="utf-8")
    m = re.search(rf"^{name}\(\) \{{$", text, re.M)
    assert m, f"{name}() not found in install.sh"
    start = m.start()
    end = text.index("\n}\n", start) + len("\n}\n")
    return text[start:end]


def _decide(is_root: int, mode: str = "", *, interactive: bool = False) -> tuple[int, str]:
    """Run decide_install_target() with a chosen privilege level."""
    script = f"""
set -uo pipefail
IS_ROOT={is_root}
MODE_CHOICE="{mode}"
KEEP_OLD=0
HOME=/home/testuser
APPIMAGE_PATH=/tmp/CPSM.AppImage
SELF_PATH=/tmp/install.sh
info() {{ echo "INFO $*"; }}
warn() {{ echo "WARN $*"; }}
error() {{ echo "ERROR $*"; }}

{_extract_function("ask_install_mode")}

{_extract_function("decide_install_target")}

decide_install_target
echo "RESULT type=$INSTALL_TYPE dir=$INSTALL_DIR bin=$BIN_DIR desktop=$DESKTOP_SCOPE pkgsudo='$PKG_SUDO'"
"""
    stdin = None if interactive else subprocess.DEVNULL
    r = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, stdin=stdin, timeout=60
    )
    return r.returncode, r.stdout + r.stderr


class TestSystemMode:
    def test_root_installs_to_shared_locations(self):
        rc, out = _decide(is_root=1)
        assert rc == 0, out
        assert "type=system" in out, out
        assert "dir=/opt/cpsm" in out, out
        assert "bin=/usr/local/bin" in out, out

    def test_root_registers_the_launcher_for_all_users(self):
        """The point of a system install: everyone gets a launcher."""
        _rc, out = _decide(is_root=1)
        assert "desktop=system" in out, out

    def test_root_needs_no_sudo(self):
        _rc, out = _decide(is_root=1)
        assert "pkgsudo=''" in out, out

    def test_system_scope_uses_the_system_flag(self):
        """register_desktop must pass --system, not write into one user's home."""
        body = _extract_function("register_desktop")
        assert "install-desktop --system --force" in body, body
        assert "SUDO_USER" not in body, (
            "register_desktop still targets a single invoking user instead of "
            "installing the launcher system-wide"
        )


class TestUserMode:
    def test_user_installs_under_home(self):
        rc, out = _decide(is_root=0, mode="user")
        assert rc == 0, out
        assert "type=user" in out, out
        assert "dir=/home/testuser/.local/opt/cpsm" in out, out
        assert "bin=/home/testuser/.local/bin" in out, out
        assert "desktop=user" in out, out

    def test_placement_never_uses_sudo(self):
        """The reported bug: a user install left root-owned files in $HOME."""
        body = _extract_function("place_appimage")
        for line in body.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            assert "sudo" not in stripped, (
                f"place_appimage still elevates: {stripped!r} — a per-user "
                f"install would write root-owned files into the user's home"
            )

    def test_sudo_is_reserved_for_packages(self):
        """PKG_SUDO exists so privilege is scoped to the one job needing it."""
        _rc, out = _decide(is_root=0, mode="user")
        assert "pkgsudo='sudo'" in out or "pkgsudo=''" in out, out
        text = INSTALL_SH.read_text(encoding="utf-8")
        # Package managers are the only consumers of elevated privilege.
        for mgr in ("apt-get install", "pacman -Sy", "apk add"):
            idx = text.find(mgr)
            if idx == -1:
                continue
            line_start = text.rfind("\n", 0, idx) + 1
            assert "PKG_SUDO" in text[line_start : idx + len(mgr)], mgr


class TestModeSelection:
    def test_non_interactive_defaults_to_user(self):
        """CI and pipes must not hang on a prompt, and must not escalate."""
        rc, out = _decide(is_root=0, mode="")
        assert rc == 0, out
        assert "type=user" in out, out
        assert "Non-interactive" in out, out

    def test_user_flag_while_root_is_refused(self):
        """Silently doing a system install after --user would be a surprise."""
        rc, out = _decide(is_root=1, mode="user")
        assert rc == 5, out
        assert "running as root" in out, out

    def test_prompt_offers_both_scopes(self):
        body = _extract_function("ask_install_mode")
        assert "this user only" in body
        assert "all users" in body
        assert "/dev/tty" in body, "prompt must read from the terminal"
