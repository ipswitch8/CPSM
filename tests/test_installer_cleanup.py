# -*- coding: utf-8 -*-
"""Behavioural tests for install.sh's cleanup_shadowing_install().

This function DELETES files, so its guard rails are the point of the tests,
not an afterthought.  A system-wide install removes an older per-user install
because leaving it behind is what made a successful install look like a no-op:
~/.local/bin precedes /usr/local/bin on a normal PATH, so the stale copy kept
winning in the terminal and -- because install-desktop resolves Exec= from
PATH -- in the desktop menu too.

The tests run the REAL function text extracted from install.sh, so they cannot
drift away from what ships.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = ROOT / "install.sh"


def _extract_function(name: str) -> str:
    """Return the source text of shell function *name* from install.sh."""
    text = INSTALL_SH.read_text(encoding="utf-8")
    m = re.search(rf"^{name}\(\) \{{$", text, re.M)
    assert m, f"{name}() not found in install.sh"
    start = m.start()
    end = text.index("\n}\n", start) + len("\n}\n")
    return text[start:end]


def _run(tmp_path: Path, *, keep_old: bool = False, setup: str = "") -> tuple[int, str]:
    """Run cleanup_shadowing_install() against a fake HOME."""
    home = tmp_path / "home"
    (home / ".local" / "bin").mkdir(parents=True)
    (home / ".local" / "opt").mkdir(parents=True)
    (home / ".local" / "share" / "applications").mkdir(parents=True)

    bindir = tmp_path / "usr" / "local" / "bin"
    bindir.mkdir(parents=True)
    target = bindir / "cpsm"
    target.write_text("#!/bin/bash\necho 'cpsm 0.2.0 (build test)'\n", encoding="utf-8")
    target.chmod(0o755)

    script = f"""
set -uo pipefail
HOME={home!s}
BIN_DIR={bindir!s}
KEEP_OLD={1 if keep_old else 0}
info() {{ echo "INFO $*"; }}
warn() {{ echo "WARN $*"; }}
ok()   {{ echo "OK $*"; }}

{setup}

{_extract_function("try_remove")}

{_extract_function("remove_stale_user_desktop")}

{_extract_function("cleanup_shadowing_install")}

cleanup_shadowing_install
echo "---state---"
[ -e "$HOME/.local/opt/cpsm" ] && echo "DIR_EXISTS" || echo "DIR_GONE"
if [ -L "$HOME/.local/bin/cpsm" ] || [ -e "$HOME/.local/bin/cpsm" ]; then
    echo "LINK_EXISTS"
else
    echo "LINK_GONE"
fi
[ -f "$HOME/.local/share/applications/cpsm.desktop" ] && echo "DESKTOP_EXISTS" || echo "DESKTOP_GONE"
"""
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60)
    return r.returncode, r.stdout + r.stderr


def _make_real_install(home_var: str = "$HOME") -> str:
    """Shell that creates CPSM's own per-user install layout."""
    return f"""
mkdir -p {home_var}/.local/opt/cpsm
printf '#!/bin/bash\\necho "cpsm 0.1.0"\\n' > {home_var}/.local/opt/cpsm/cpsm.AppImage
chmod +x {home_var}/.local/opt/cpsm/cpsm.AppImage
ln -sf {home_var}/.local/opt/cpsm/cpsm.AppImage {home_var}/.local/bin/cpsm
"""


class TestRemovesTheShadowingInstall:
    def test_removes_our_own_layout(self, tmp_path):
        rc, out = _run(tmp_path, setup=_make_real_install())
        assert rc == 0, out
        assert "DIR_GONE" in out, out
        assert "LINK_GONE" in out, out
        assert "Removed" in out

    def test_keep_old_install_leaves_it(self, tmp_path):
        """The escape hatch must actually escape."""
        rc, out = _run(tmp_path, keep_old=True, setup=_make_real_install())
        assert rc == 0, out
        assert "DIR_EXISTS" in out and "LINK_EXISTS" in out, out
        assert "keep-old-install" in out

    def test_noop_when_nothing_is_installed(self, tmp_path):
        rc, out = _run(tmp_path)
        assert rc == 0, out
        assert "Removed" not in out


class TestRefusesToTouchWhatIsNotOurs:
    """The deletion is narrow on purpose; these are the refusals."""

    def test_symlink_pointing_elsewhere_is_left_alone(self, tmp_path):
        setup = """
mkdir -p "$HOME/.local/opt/cpsm" "$HOME/elsewhere"
printf '#!/bin/bash\\ntrue\\n' > "$HOME/elsewhere/other"
chmod +x "$HOME/elsewhere/other"
printf 'x' > "$HOME/.local/opt/cpsm/cpsm.AppImage"
ln -sf "$HOME/elsewhere/other" "$HOME/.local/bin/cpsm"
"""
        rc, out = _run(tmp_path, setup=setup)
        assert rc == 0, out
        assert "LINK_EXISTS" in out, out
        assert "does not point into" in out, out

    def test_regular_file_is_left_alone(self, tmp_path):
        """Someone's own script named cpsm is not ours to delete."""
        setup = """
mkdir -p "$HOME/.local/opt/cpsm"
printf 'x' > "$HOME/.local/opt/cpsm/cpsm.AppImage"
printf '#!/bin/bash\\necho mine\\n' > "$HOME/.local/bin/cpsm"
chmod +x "$HOME/.local/bin/cpsm"
"""
        rc, out = _run(tmp_path, setup=setup)
        assert rc == 0, out
        assert "LINK_EXISTS" in out, out
        assert "not a symlink" in out, out

    def test_directory_without_the_appimage_is_left_alone(self, tmp_path):
        """Right path, wrong contents — refuse rather than guess."""
        setup = """
mkdir -p "$HOME/.local/opt/cpsm"
printf 'important' > "$HOME/.local/opt/cpsm/something_else.txt"
"""
        rc, out = _run(tmp_path, setup=setup)
        assert rc == 0, out
        assert "DIR_EXISTS" in out, out
        assert "not our layout" in out, out


class TestOrdering:
    def test_cleanup_runs_before_desktop_registration(self):
        """Order is load-bearing, so assert it.

        install-desktop resolves Exec= from PATH when it can, and the stale
        ~/.local/bin/cpsm symlink is exactly what it would find.  Removing it
        first is what stops the launcher being pinned to the old install.
        """
        text = INSTALL_SH.read_text(encoding="utf-8")
        main_at = text.index("main() {")
        cleanup = text.index("    cleanup_shadowing_install", main_at)
        register = text.index("    register_desktop\n", main_at)
        assert cleanup < register, (
            "cleanup_shadowing_install must run before register_desktop, or the "
            "launcher can still be pointed at the install being removed"
        )


class TestCleanupSurvivesFailure:
    """A cleanup that cannot finish must not take the install down with it.

    Reported from a real run: the leftovers were owned by root (an earlier
    root install had created them), the removal ran as the unprivileged user
    and failed with EPERM, and because `rm ... && ok ...` is a failing command
    under `set -e` the whole script aborted -- before register_desktop. The
    user got no launcher update, which was the entire reason for the run.
    """

    def test_unremovable_leftover_warns_and_continues(self, tmp_path):
        # A read-only parent makes the rm fail without needing root.
        setup = """
mkdir -p "$HOME/.local/opt/cpsm"
printf 'x' > "$HOME/.local/opt/cpsm/cpsm.AppImage"
chmod 555 "$HOME/.local/opt"
"""
        rc, out = _run(tmp_path, setup=setup)
        # Restore write permission so pytest can clean up tmp_path.
        (tmp_path / "home" / ".local" / "opt").chmod(0o755)

        assert rc == 0, f"cleanup aborted the install:\n{out}"
        assert "Could not remove" in out, out
        assert "sudo rm -rf" in out, "must tell the user how to finish the job"

    def test_removal_does_not_drop_privileges(self):
        """Regression guard for the reported failure.

        The removal used to run `sudo -u "$SUDO_USER" rm`, giving away the
        privilege the installer already had -- and failing on exactly the
        case that matters, because a previous ROOT install leaves root-owned
        files under the user's home. Safety here comes from the path checks,
        not from the uid.
        """
        body = _extract_function("cleanup_shadowing_install") + _extract_function(
            "try_remove"
        )
        assert "sudo -u" not in body, (
            "the cleanup drops privileges again; it will fail to remove "
            "root-owned leftovers from an earlier system install"
        )

    def test_cleanup_is_called_advisorily(self):
        """Even an unexpected non-zero must not abort main()."""
        text = INSTALL_SH.read_text(encoding="utf-8")
        assert "cleanup_shadowing_install || true" in text, (
            "cleanup_shadowing_install is called without || true, so an "
            "unexpected failure aborts the install before register_desktop"
        )

def _desktop(exec_path: str) -> str:
    """Shell that writes a per-user cpsm.desktop with the given Exec=."""
    return f"""
cat > "$HOME/.local/share/applications/cpsm.desktop" <<DESKTOP_EOF
[Desktop Entry]
Type=Application
Name=CPSM
Exec={exec_path} gui %u
Icon=cpsm
DESKTOP_EOF
"""


class TestStaleLauncherIsRemoved:
    """A per-user .desktop outranks the system-wide one, so it must go too.

    Reported: after a successful system install the app vanished from the
    desktop environment entirely.  $XDG_DATA_HOME/applications takes
    precedence over /usr/local/share/applications for the same desktop-file
    ID, so the old per-user entry kept winning -- and because the cleanup had
    just deleted the AppImage its Exec= named, it pointed at nothing.  An
    entry whose target is missing is dropped, so the launcher disappeared
    instead of updating.
    """

    def test_stale_entry_pointing_at_the_removed_install_is_deleted(self, tmp_path):
        setup = _make_real_install() + _desktop("$HOME/.local/opt/cpsm/cpsm.AppImage")
        rc, out = _run(tmp_path, setup=setup)
        assert rc == 0, out
        assert "DESKTOP_GONE" in out, out
        assert "per-user launcher" in out, out

    def test_entry_pointing_elsewhere_is_left_alone(self, tmp_path):
        """A launcher the user re-pointed is theirs, not ours to delete."""
        setup = _make_real_install() + _desktop("/usr/bin/something-else")
        rc, out = _run(tmp_path, setup=setup)
        assert rc == 0, out
        assert "DESKTOP_EXISTS" in out, out
        assert "does not launch" in out, out
        # It still decides what launches, so the warning has to say so.
        assert "precedence" in out, out

    def test_stale_entry_is_cleaned_even_when_the_install_is_already_gone(
        self, tmp_path
    ):
        """The state a half-finished cleanup leaves behind.

        The install directory and symlink are already removed, so the earlier
        code returned before ever looking at the launcher -- leaving the one
        file that actually hides the application.
        """
        setup = _desktop("$HOME/.local/opt/cpsm/cpsm.AppImage")
        rc, out = _run(tmp_path, setup=setup)
        assert rc == 0, out
        assert "DESKTOP_GONE" in out, out

    def test_no_desktop_entry_is_a_noop(self, tmp_path):
        rc, out = _run(tmp_path, setup=_make_real_install())
        assert rc == 0, out
        assert "per-user launcher" not in out

    def test_entry_that_merely_mentions_the_path_is_kept(self, tmp_path):
        """The match must be the invoked BINARY, not a substring of Exec=.

        An unanchored test over the whole Exec= line deleted any launcher that
        happened to contain the old install path anywhere -- including as an
        argument to a completely different program. Reproduced against the
        real function before this was anchored: the entry below was removed.
        """
        setup = _make_real_install() + _desktop(
            "/usr/bin/some-other-app --note=$HOME/.local/opt/cpsm"
        )
        rc, out = _run(tmp_path, setup=setup)
        assert rc == 0, out
        assert "DESKTOP_EXISTS" in out, (
            "a launcher for an unrelated program was deleted because it "
            "mentioned the old install path as an argument"
        )

    def test_quoted_exec_path_is_still_matched(self, tmp_path):
        """Anchoring must not be defeated by ordinary quoting."""
        setup = _make_real_install() + _desktop(
            '"$HOME/.local/opt/cpsm/cpsm.AppImage"'
        )
        rc, out = _run(tmp_path, setup=setup)
        assert rc == 0, out
        assert "DESKTOP_GONE" in out, out
