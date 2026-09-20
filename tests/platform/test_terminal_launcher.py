# -*- coding: utf-8 -*-
"""
Tests for TerminalLauncher ABC, LocalShellLauncher, stub launchers, and
discover_launchers().

Spec: §8
"""

from __future__ import annotations

import sys

import pytest

from cpsm.platform.terminal_launcher import (
    AlacrittyLauncher,
    GnomeTerminalLauncher,
    KittyLauncher,
    KonsoleLauncher,
    LocalShellLauncher,
    TerminalLauncher,
    WezTermLauncher,
    WindowsTerminalLauncher,
    XtermLauncher,
    discover_launchers,
)

# ---------------------------------------------------------------------------
# LocalShellLauncher
# ---------------------------------------------------------------------------


def test_local_shell_launcher_name() -> None:
    lsl = LocalShellLauncher()
    assert lsl.name == "local-shell"


def test_local_shell_launcher_no_geometry() -> None:
    lsl = LocalShellLauncher()
    assert lsl.supports_geometry is False


def test_local_shell_launcher_spawn_returns_pid(tmp_path: pytest.fixture) -> None:  # type: ignore[valid-type]
    """LocalShellLauncher.spawn must return a positive PID."""
    lsl = LocalShellLauncher()
    pid = lsl.spawn(["/bin/echo", "hi"], title="test")
    assert isinstance(pid, int)
    assert pid > 0


def test_local_shell_launcher_spawn_with_cwd(tmp_path: pytest.fixture) -> None:  # type: ignore[valid-type]
    lsl = LocalShellLauncher()
    # Just check it doesn't raise and returns a pid
    pid = lsl.spawn(["/bin/echo", "hi"], title="test", cwd=tmp_path)  # type: ignore[arg-type]
    assert pid > 0


def test_local_shell_launcher_move_returns_false() -> None:
    lsl = LocalShellLauncher()
    result = lsl.move(12345, (0, 0, 1920, 1080))
    assert result is False


def test_local_shell_launcher_repr() -> None:
    lsl = LocalShellLauncher()
    assert "LocalShellLauncher" in repr(lsl)
    assert "local-shell" in repr(lsl)


# ---------------------------------------------------------------------------
# Stub launchers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "launcher_cls,expected_name,expected_geometry",
    [
        (WezTermLauncher, "wezterm", True),
        (AlacrittyLauncher, "alacritty", True),
        (KittyLauncher, "kitty", True),
        (KonsoleLauncher, "konsole", True),
        (GnomeTerminalLauncher, "gnome-terminal", True),
        (XtermLauncher, "xterm", True),
        (WindowsTerminalLauncher, "wt", True),
    ],
)
def test_stub_launcher_metadata(
    launcher_cls: type[TerminalLauncher],
    expected_name: str,
    expected_geometry: bool,
) -> None:
    launcher = launcher_cls()
    assert launcher.name == expected_name
    assert launcher.supports_geometry is expected_geometry


@pytest.mark.parametrize(
    "launcher_cls",
    [
        WindowsTerminalLauncher,
    ],
)
def test_stub_launcher_spawn_raises_not_implemented(
    launcher_cls: type[TerminalLauncher],
) -> None:
    """Round 6: only WindowsTerminalLauncher remains a stub on Linux. All
    Linux launchers (xterm, konsole, alacritty, kitty, wezterm,
    gnome-terminal) now have working geometry-aware spawn methods."""
    launcher = launcher_cls()
    with pytest.raises(NotImplementedError, match="Phase"):
        launcher.spawn(["echo", "hi"], title="test")


def test_gnome_terminal_launcher_spawn_invokes_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Quick implementation of GnomeTerminalLauncher must call
    subprocess.Popen with the gnome-terminal command."""
    captured: list[list[str]] = []

    class _FakeProc:
        pid = 12345

    def _fake_popen(cmd, **kwargs):
        captured.append(cmd)
        return _FakeProc()

    monkeypatch.setattr("subprocess.Popen", _fake_popen)
    pid = GnomeTerminalLauncher().spawn(
        ["tmux", "attach", "-t", "test"], title="My Title",
    )
    assert pid == 12345
    assert len(captured) == 1
    assert captured[0][0] == "gnome-terminal"
    assert "--title" in captured[0]
    # Round 5: a unique suffix is appended so wmctrl can identify the
    # window. The user's title remains as a prefix.
    title_idx = captured[0].index("--title")
    title_value = captured[0][title_idx + 1]
    assert title_value.startswith("My Title")
    assert "--" in captured[0]
    sep_idx = captured[0].index("--")
    assert captured[0][sep_idx + 1 :] == ["tmux", "attach", "-t", "test"]


def test_xterm_launcher_spawn_invokes_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Quick implementation of XtermLauncher must call subprocess.Popen
    with the xterm command and an -e separator."""
    captured: list[list[str]] = []

    class _FakeProc:
        pid = 54321

    def _fake_popen(cmd, **kwargs):
        captured.append(cmd)
        return _FakeProc()

    monkeypatch.setattr("subprocess.Popen", _fake_popen)
    pid = XtermLauncher().spawn(
        ["tmux", "attach", "-t", "test"],
        title="Title",
        geometry=(100, 200, 800, 600),
    )
    assert pid == 54321
    assert len(captured) == 1
    assert captured[0][0] == "xterm"
    # Geometry was provided → must include -geometry COLSxROWS+X+Y
    geo_idx = captured[0].index("-geometry")
    geo_arg = captured[0][geo_idx + 1]
    assert geo_arg.endswith("+100+200")
    # -e <argv...> must follow
    e_idx = captured[0].index("-e")
    assert captured[0][e_idx + 1 : e_idx + 5] == ["tmux", "attach", "-t", "test"]


def test_stub_launcher_move_returns_false() -> None:
    lsl = WezTermLauncher()
    assert lsl.move(0, (0, 0, 100, 100)) is False


# ---------------------------------------------------------------------------
# discover_launchers
# ---------------------------------------------------------------------------


def test_discover_launchers_always_has_local_shell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LocalShellLauncher is always present as the last element."""
    monkeypatch.setattr("shutil.which", lambda name: None)
    launchers = discover_launchers()
    assert launchers, "discover_launchers must return at least one launcher"
    assert isinstance(launchers[-1], LocalShellLauncher)


def test_discover_launchers_detects_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    """discover_launchers returns a stub launcher for a detected binary."""
    # Simulate wezterm being on PATH
    monkeypatch.setattr(
        "shutil.which", lambda name: "/usr/bin/wezterm" if name == "wezterm" else None
    )
    monkeypatch.setattr(sys, "platform", "linux")
    launchers = discover_launchers()
    names = [launcher.name for launcher in launchers]
    assert "wezterm" in names
    assert "local-shell" in names


def test_discover_launchers_linux_priority(monkeypatch: pytest.MonkeyPatch) -> None:
    """On Linux, wezterm is checked before xterm."""
    detected: list[str] = []

    def fake_which(name: str) -> str | None:
        detected.append(name)
        return f"/usr/bin/{name}" if name in ("wezterm", "xterm") else None

    monkeypatch.setattr("shutil.which", fake_which)
    monkeypatch.setattr(sys, "platform", "linux")
    launchers = discover_launchers()
    launcher_names = [launcher.name for launcher in launchers if launcher.name != "local-shell"]
    assert launcher_names.index("wezterm") < launcher_names.index("xterm")


def test_discover_launchers_windows_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """On Windows, wt.exe is checked first."""
    monkeypatch.setattr(
        "shutil.which",
        lambda name: f"C:\\Programs\\{name}.exe" if name == "wt" else None,
    )
    monkeypatch.setattr(sys, "platform", "win32")
    launchers = discover_launchers()
    names = [launcher.name for launcher in launchers]
    assert "wt" in names
    assert names[0] == "wt"
