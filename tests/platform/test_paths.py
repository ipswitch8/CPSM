# -*- coding: utf-8 -*-
"""
Tests for PathResolver and resolve_config_path (§2.1, §8).

``resolve_config_path`` is tested here because ``cpsm.platform.paths`` re-exports
it; the function also remains importable from its original location in
``cpsm.data.repository``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from cpsm.platform.paths import PathResolver, resolve_config_path

# ---------------------------------------------------------------------------
# resolve_config_path — §2.1 lookup order
# ---------------------------------------------------------------------------


def test_resolve_config_path_explicit(tmp_path: Path) -> None:
    """Explicit path bypasses all environment variables."""
    explicit = tmp_path / "custom.yaml"
    result = resolve_config_path(explicit)
    assert result == explicit.resolve()


def test_resolve_config_path_env_var(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """$CPSM_CONFIG overrides XDG/APPDATA and home defaults."""
    env_path = tmp_path / "env.yaml"
    monkeypatch.setenv("CPSM_CONFIG", str(env_path))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    result = resolve_config_path()
    assert result == env_path.resolve()


def test_resolve_config_path_xdg_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """$XDG_CONFIG_HOME is used on non-Windows when $CPSM_CONFIG is unset."""
    if sys.platform == "win32":
        pytest.skip("XDG test only applies on Linux")
    monkeypatch.delenv("CPSM_CONFIG", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    result = resolve_config_path()
    assert result == tmp_path / "cpsm" / ".cpsm.yaml"


def test_resolve_config_path_appdata_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """%APPDATA% is used on Windows when $CPSM_CONFIG is unset."""
    if sys.platform != "win32":
        # Simulate Windows path resolution even on Linux
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.delenv("CPSM_CONFIG", raising=False)
        monkeypatch.setenv("APPDATA", str(tmp_path))
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        result = resolve_config_path()
        assert result == tmp_path / "cpsm" / ".cpsm.yaml"
    else:
        monkeypatch.delenv("CPSM_CONFIG", raising=False)
        monkeypatch.setenv("APPDATA", str(tmp_path))
        result = resolve_config_path()
        assert result == tmp_path / "cpsm" / ".cpsm.yaml"


def test_resolve_config_path_home_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """~/.cpsm.yaml is the last-resort fallback."""
    monkeypatch.delenv("CPSM_CONFIG", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    if sys.platform == "win32":
        # On Windows also clear APPDATA
        monkeypatch.delenv("APPDATA", raising=False)
    result = resolve_config_path()
    assert result == (Path.home() / ".cpsm.yaml").resolve()


# ---------------------------------------------------------------------------
# re-export from original location still works
# ---------------------------------------------------------------------------


def test_resolve_config_path_still_importable_from_data_repository() -> None:
    """resolve_config_path must remain importable from its original module."""
    from cpsm.data.repository import resolve_config_path as orig

    assert callable(orig)


# ---------------------------------------------------------------------------
# PathResolver.config_dir
# ---------------------------------------------------------------------------


def test_config_dir_linux_xdg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if sys.platform == "win32":
        pytest.skip("Linux-only")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    pr = PathResolver()
    assert pr.config_dir() == tmp_path / "cpsm"


def test_config_dir_linux_default(monkeypatch: pytest.MonkeyPatch) -> None:
    if sys.platform == "win32":
        pytest.skip("Linux-only")
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    pr = PathResolver()
    assert pr.config_dir() == Path.home() / ".config" / "cpsm"


def test_config_dir_windows_appdata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    pr = PathResolver()
    assert pr.config_dir() == tmp_path / "cpsm"


def test_config_dir_windows_no_appdata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv("APPDATA", raising=False)
    pr = PathResolver()
    assert pr.config_dir() == Path.home() / "AppData" / "Roaming" / "cpsm"


# ---------------------------------------------------------------------------
# PathResolver.log_dir
# ---------------------------------------------------------------------------


def test_log_dir_is_subdir_of_config_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    if sys.platform == "win32":
        pytest.skip("Linux-only variant")
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    pr = PathResolver()
    assert pr.log_dir() == pr.config_dir() / "logs"


# ---------------------------------------------------------------------------
# PathResolver.launcher_tmp_dir
# ---------------------------------------------------------------------------


def test_launcher_tmp_dir_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    pr = PathResolver()
    assert str(pr.launcher_tmp_dir()).startswith("/tmp")


def test_launcher_tmp_dir_windows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("TEMP", str(tmp_path))
    pr = PathResolver()
    assert pr.launcher_tmp_dir() == tmp_path / "cpsm-launchers"


# ---------------------------------------------------------------------------
# PathResolver.launcher_script_path
# ---------------------------------------------------------------------------


def test_launcher_script_path_pattern(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    pr = PathResolver()
    path = pr.launcher_script_path("web01", 1234)
    assert path.name == "cpsm-launcher-web01-1234.sh"
    assert path.parent == pr.launcher_tmp_dir()


# ---------------------------------------------------------------------------
# PathResolver.expand
# ---------------------------------------------------------------------------


def test_expand_tilde(monkeypatch: pytest.MonkeyPatch) -> None:
    pr = PathResolver()
    result = pr.expand("~/somefile.txt")
    assert not str(result).startswith("~")
    assert str(result).startswith(str(Path.home()))


def test_expand_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CPSM_TEST_DIR", "/tmp/testdir")
    pr = PathResolver()
    result = pr.expand("$CPSM_TEST_DIR/file.txt")
    assert "/tmp/testdir" in str(result)


# ---------------------------------------------------------------------------
# PathResolver.is_safe_config_path
# ---------------------------------------------------------------------------


def test_is_safe_config_path_0600(tmp_path: Path) -> None:
    if sys.platform == "win32":
        pytest.skip("Permissions test is Linux-only")
    p = tmp_path / "test.yaml"
    p.write_text("data")
    os.chmod(p, 0o600)
    pr = PathResolver()
    assert pr.is_safe_config_path(p) is True


def test_is_safe_config_path_0644_is_unsafe(tmp_path: Path) -> None:
    if sys.platform == "win32":
        pytest.skip("Permissions test is Linux-only")
    p = tmp_path / "test.yaml"
    p.write_text("data")
    os.chmod(p, 0o644)
    pr = PathResolver()
    assert pr.is_safe_config_path(p) is False


def test_is_safe_config_path_nonexistent_returns_false() -> None:
    if sys.platform == "win32":
        pytest.skip("Permissions test is Linux-only")
    pr = PathResolver()
    assert pr.is_safe_config_path(Path("/nonexistent/path/file.yaml")) is False


def test_is_safe_config_path_windows_always_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    pr = PathResolver()
    assert pr.is_safe_config_path(Path("/any/path")) is True
