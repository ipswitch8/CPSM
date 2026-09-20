# -*- coding: utf-8 -*-
"""Tests for cpsm.platform.desktop_entry — .desktop launcher generator."""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

from cpsm.platform.desktop_entry import (
    DESKTOP_ENTRY_NAME,
    generate_desktop_file_text,
    install_desktop_entry,
)

# ---------------------------------------------------------------------------
# generate_desktop_file_text
# ---------------------------------------------------------------------------


class TestGenerateDesktopFileText:
    def test_contains_required_keys(self) -> None:
        text = generate_desktop_file_text(executable="/usr/local/bin/cpsm")
        for key in [
            "[Desktop Entry]",
            "Type=Application",
            "Name=CPSM",
            "Exec=/usr/local/bin/cpsm gui %u",
            "Icon=cpsm",
            "Terminal=false",
            "Categories=Development;System;TerminalEmulator;",
            "StartupWMClass=cpsm",
        ]:
            assert key in text, f"missing required key: {key!r}"

    def test_exec_appends_gui_and_url_token(self) -> None:
        text = generate_desktop_file_text(executable="/opt/cpsm/bin/cpsm")
        assert "Exec=/opt/cpsm/bin/cpsm gui %u" in text

    def test_custom_icon_name(self) -> None:
        text = generate_desktop_file_text(executable="/usr/bin/cpsm", icon_name="my-cpsm")
        assert "Icon=my-cpsm" in text
        assert "Icon=cpsm\n" not in text

    def test_freedesktop_categories_format(self) -> None:
        """Categories must be semicolon-separated and end with a trailing semicolon."""
        text = generate_desktop_file_text(executable="cpsm")
        line = next(L for L in text.splitlines() if L.startswith("Categories="))
        assert line.endswith(";"), "Categories must end with a trailing semicolon per freedesktop"
        # Each category must be in the registered category list — basic sanity check
        cats = line.split("=", 1)[1].strip(";").split(";")
        for c in cats:
            assert c, f"empty category in {cats!r}"

    def test_keywords_format(self) -> None:
        text = generate_desktop_file_text(executable="cpsm")
        kw_line = next(L for L in text.splitlines() if L.startswith("Keywords="))
        assert kw_line.endswith(";"), "Keywords must end with a trailing semicolon"


# ---------------------------------------------------------------------------
# install_desktop_entry
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_xdg(tmp_path, monkeypatch):
    """Redirect $XDG_DATA_HOME to a tmp directory."""
    data_home = tmp_path / "data"
    data_home.mkdir()
    monkeypatch.setenv("XDG_DATA_HOME", str(data_home))
    # Also redirect $HOME so any fallback to ~/.local/share lands here too
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    return data_home


class TestInstallDesktopEntry:
    def test_writes_to_user_dir_when_user_only(self, fake_xdg) -> None:
        if sys.platform == "win32":
            pytest.skip("Linux/Unix only")
        path = install_desktop_entry(user_only=True, force=False, executable="/usr/bin/cpsm")
        assert path == fake_xdg / "applications" / DESKTOP_ENTRY_NAME
        assert path.exists()

    def test_file_perms_0644(self, fake_xdg) -> None:
        if sys.platform == "win32":
            pytest.skip("Linux/Unix only")
        path = install_desktop_entry(user_only=True, force=False, executable="cpsm")
        mode = oct(path.stat().st_mode & 0o777)
        assert mode == "0o644", f"expected 0o644, got {mode}"

    def test_content_matches_generator(self, fake_xdg) -> None:
        if sys.platform == "win32":
            pytest.skip("Linux/Unix only")
        path = install_desktop_entry(user_only=True, force=False, executable="/x/bin/cpsm")
        body = path.read_text(encoding="utf-8")
        assert body == generate_desktop_file_text(executable="/x/bin/cpsm")

    def test_refuses_to_overwrite_without_force(self, fake_xdg) -> None:
        if sys.platform == "win32":
            pytest.skip("Linux/Unix only")
        install_desktop_entry(user_only=True, force=False, executable="cpsm")
        with pytest.raises(FileExistsError):
            install_desktop_entry(user_only=True, force=False, executable="cpsm")

    def test_force_overwrites(self, fake_xdg) -> None:
        if sys.platform == "win32":
            pytest.skip("Linux/Unix only")
        path = install_desktop_entry(user_only=True, force=False, executable="/old/cpsm")
        # Re-install with new exec under --force
        install_desktop_entry(user_only=True, force=True, executable="/new/cpsm")
        body = path.read_text(encoding="utf-8")
        assert "Exec=/new/cpsm gui %u" in body
        assert "/old/cpsm" not in body

    def test_icon_installed_with_0644_perms(self, fake_xdg) -> None:
        """Icon file should be installed in hicolor/scalable/apps with 0o644 perms.

        Regression for Karen's Low-severity finding: shutil.copy2 was preserving
        source perms (0o664). install_desktop_entry should normalise to 0o644.
        """
        if sys.platform == "win32":
            pytest.skip("Linux/Unix only")
        install_desktop_entry(user_only=True, force=False, executable="cpsm")
        icon_path = fake_xdg / "icons" / "hicolor" / "scalable" / "apps" / "cpsm.svg"
        if not icon_path.exists():
            pytest.skip("bundled icon resource missing in this build")
        mode = oct(icon_path.stat().st_mode & 0o777)
        assert mode == "0o644", f"icon perms should be 0o644 per freedesktop conv, got {mode}"

    def test_xdg_data_home_default_when_env_unset(self, tmp_path, monkeypatch) -> None:
        """When $XDG_DATA_HOME is unset, fall back to $HOME/.local/share."""
        if sys.platform == "win32":
            pytest.skip("Linux/Unix only")
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
        home = tmp_path / "h"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        # Path.home() may also need to follow HOME on this platform — verify
        path = install_desktop_entry(user_only=True, force=False, executable="cpsm")
        assert "/.local/share/applications/" in str(path)

    def test_windows_raises(self, monkeypatch) -> None:
        """On Windows we don't install .desktop files — must raise."""
        monkeypatch.setattr(sys, "platform", "win32")
        with pytest.raises(RuntimeError, match="freedesktop"):
            install_desktop_entry(user_only=True, force=False, executable="cpsm")

    def test_executable_fallback_to_python_module(self, fake_xdg, monkeypatch) -> None:
        """When `which cpsm` returns None, Exec must fall back to `python -m cpsm`."""
        if sys.platform == "win32":
            pytest.skip("Linux/Unix only")
        monkeypatch.setattr("shutil.which", lambda _name: None)
        # _resolve_executable consults $APPIMAGE BEFORE falling back to
        # `python -m cpsm`. Without clearing it this test is not hermetic: it
        # passes in CI and fails whenever the suite is run from inside a CPSM
        # AppImage session, because that runtime sets APPIMAGE to a real,
        # existing file and the fallback is never reached.
        # (Sibling tests at lines below already delenv/setenv it deliberately;
        # this one was simply missed.)
        monkeypatch.delenv("APPIMAGE", raising=False)
        path = install_desktop_entry(user_only=True, force=False, executable=None)
        body = path.read_text(encoding="utf-8")
        assert "-m cpsm gui %u" in body

    def test_explicit_executable_wins_over_which(self, fake_xdg, monkeypatch) -> None:
        """An explicit executable arg must override shutil.which discovery."""
        if sys.platform == "win32":
            pytest.skip("Linux/Unix only")
        monkeypatch.setattr("shutil.which", lambda _name: "/usr/bin/cpsm-from-which")
        path = install_desktop_entry(user_only=True, force=False, executable="/explicit/cpsm")
        body = path.read_text(encoding="utf-8")
        assert "Exec=/explicit/cpsm gui %u" in body
        assert "/usr/bin/cpsm-from-which" not in body

    def test_transient_appimage_mount_is_rejected(
        self, fake_xdg, monkeypatch, tmp_path
    ) -> None:
        """`shutil.which` inside a running AppImage returns the FUSE mount
        path (``/tmp/.mount_*/usr/bin/cpsm``).  That path vanishes on
        AppImage exit, so _resolve_executable must NOT bake it into the
        Exec= line — it should fall through to $APPIMAGE instead.
        """
        if sys.platform == "win32":
            pytest.skip("Linux/Unix only")
        monkeypatch.setattr(
            "shutil.which",
            lambda _name: "/tmp/.mount_cpsmXYZ123/usr/bin/cpsm",
        )
        # Point APPIMAGE at a real (empty) file so Path(appimage).exists() is True.
        fake_appimage = tmp_path / "cpsm.AppImage"
        fake_appimage.write_bytes(b"")
        monkeypatch.setenv("APPIMAGE", str(fake_appimage))
        path = install_desktop_entry(user_only=True, force=False, executable=None)
        body = path.read_text(encoding="utf-8")
        assert f"Exec={fake_appimage} gui %u" in body
        assert "/tmp/.mount_" not in body

    def test_transient_mount_falls_back_to_python_when_no_appimage_env(
        self, fake_xdg, monkeypatch
    ) -> None:
        """If we're inside a mount but $APPIMAGE isn't set (shouldn't happen
        in practice, but be defensive), fall back to `python -m cpsm`
        rather than the poisoned which() result.
        """
        if sys.platform == "win32":
            pytest.skip("Linux/Unix only")
        monkeypatch.setattr(
            "shutil.which",
            lambda _name: "/tmp/.mount_cpsmXYZ123/usr/bin/cpsm",
        )
        monkeypatch.delenv("APPIMAGE", raising=False)
        path = install_desktop_entry(user_only=True, force=False, executable=None)
        body = path.read_text(encoding="utf-8")
        assert "/tmp/.mount_" not in body
        assert "-m cpsm gui %u" in body

    def test_update_desktop_database_called_when_available(self, fake_xdg, monkeypatch) -> None:
        """If update-desktop-database is on PATH, it should be invoked."""
        if sys.platform == "win32":
            pytest.skip("Linux/Unix only")
        called = []

        def fake_run(argv, **kwargs):
            called.append(argv)
            from subprocess import CompletedProcess

            return CompletedProcess(argv, 0)

        monkeypatch.setattr(
            "shutil.which",
            lambda name: (
                "/fake/update-desktop-database" if name == "update-desktop-database" else None
            ),
        )
        with patch("subprocess.run", side_effect=fake_run):
            install_desktop_entry(user_only=True, force=False, executable="/usr/bin/cpsm")
        assert len(called) == 1
        assert "/fake/update-desktop-database" in called[0]
