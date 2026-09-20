# -*- coding: utf-8 -*-
"""
Unit tests for TmuxBackend — all ProcessRunner calls are mocked.

Tests assert the exact argv shape and that output is parsed into the correct
typed dataclass values.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from cpsm.platform.tmux_backend import (
    TmuxBackend,
    _parse_bool,
    _parse_format,
    _parse_int,
    _version_at_least,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_result(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    """Return a fake CompletedProcess."""
    return subprocess.CompletedProcess(
        args=[],
        returncode=returncode,
        stdout=stdout,
        stderr="",
    )


def _make_backend(
    version_stdout: str = "tmux 3.4",
    *,
    socket_path: Any = None,
) -> tuple[TmuxBackend, MagicMock]:
    """Create a TmuxBackend with a mocked ProcessRunner.

    Returns the backend and the runner mock so tests can assert calls and set
    return values.
    """
    runner = MagicMock()
    # Probe call during __init__
    runner.run.return_value = _make_result(version_stdout)
    backend = TmuxBackend(runner=runner, socket_path=socket_path)
    # Reset so tests start with a clean call list.
    runner.run.reset_mock()
    return backend, runner


# ---------------------------------------------------------------------------
# _parse_int / _parse_bool helpers
# ---------------------------------------------------------------------------


class TestParseInt:
    @pytest.mark.parametrize(
        "value, expected",
        [
            ("42", 42),
            (" 7 ", 7),
            ("0", 0),
            ("", None),
            ("   ", None),
            ("abc", None),
        ],
    )
    def test_parse_int(self, value: str, expected: int | None) -> None:
        assert _parse_int(value) == expected


class TestParseBool:
    @pytest.mark.parametrize(
        "value, expected",
        [
            ("0", False),
            ("", False),
            ("   ", False),
            ("1", True),
            ("2", True),
            ("yes", True),
        ],
    )
    def test_parse_bool(self, value: str, expected: bool) -> None:
        assert _parse_bool(value) == expected


# ---------------------------------------------------------------------------
# _version_at_least
# ---------------------------------------------------------------------------


class TestVersionAtLeast:
    @pytest.mark.parametrize(
        "version_string, min_version, expected",
        [
            ("tmux 3.4", "1.8", True),
            ("tmux 1.8", "1.8", True),
            ("tmux 1.7", "1.8", False),
            ("tmux 2.0", "1.8", True),
            ("tmux 3.5a", "3.5", True),
            ("tmux 1.5", "1.6", False),
            ("tmux 1.6", "1.6", True),
            ("no version info", "1.0", False),
        ],
    )
    def test_version_at_least(self, version_string: str, min_version: str, expected: bool) -> None:
        assert _version_at_least(version_string, min_version) == expected


# ---------------------------------------------------------------------------
# _parse_format
# ---------------------------------------------------------------------------


class TestParseFormat:
    def test_basic_parsing(self) -> None:
        output = "name1|value1\nname2|value2\n"
        fields = ["key_a", "key_b"]
        result = _parse_format(output, fields)
        assert len(result) == 2
        assert result[0] == {"key_a": "name1", "key_b": "value1"}
        assert result[1] == {"key_a": "name2", "key_b": "value2"}

    def test_numeric_fields_coerced(self) -> None:
        output = "12|34|56|1|0"
        fields = ["pane_pid", "pane_width", "pane_height", "window_index", "pane_index"]
        result = _parse_format(output, fields)
        assert result[0] == {
            "pane_pid": 12,
            "pane_width": 34,
            "pane_height": 56,
            "window_index": 1,
            "pane_index": 0,
        }

    def test_bool_fields_coerced(self) -> None:
        output = "0|1\n1|0"
        fields = ["pane_dead", "session_attached"]
        result = _parse_format(output, fields)
        assert result[0] == {"pane_dead": False, "session_attached": True}
        assert result[1] == {"pane_dead": True, "session_attached": False}

    def test_empty_numeric_field_gives_none(self) -> None:
        output = "|80|24|0|0"
        fields = ["pane_pid", "pane_width", "pane_height", "window_index", "pane_index"]
        result = _parse_format(output, fields)
        assert result[0]["pane_pid"] is None
        assert result[0]["pane_width"] == 80

    def test_skips_empty_lines(self) -> None:
        output = "a|b\n\n\nc|d\n"
        result = _parse_format(output, ["x", "y"])
        assert len(result) == 2

    def test_short_line_padded(self) -> None:
        output = "only_one"
        result = _parse_format(output, ["f1", "f2", "f3"])
        assert result[0]["f1"] == "only_one"
        assert result[0]["f2"] == ""
        assert result[0]["f3"] == ""

    @pytest.mark.parametrize(
        "dead_val, expected_dead",
        [("0", False), ("", False), ("1", True), ("2", True)],
    )
    def test_pane_dead_variants(self, dead_val: str, expected_dead: bool) -> None:
        output = f"s1|0|0|%0|1234|{dead_val}|bash|80|24"
        fields = [
            "session_name",
            "window_index",
            "pane_index",
            "pane_id",
            "pane_pid",
            "pane_dead",
            "pane_current_command",
            "pane_width",
            "pane_height",
        ]
        result = _parse_format(output, fields)
        assert result[0]["pane_dead"] == expected_dead


# ---------------------------------------------------------------------------
# Construction / capability probing
# ---------------------------------------------------------------------------


class TestCapabilityProbing:
    def test_probe_called_with_version_flag(self) -> None:
        runner = MagicMock()
        runner.run.return_value = _make_result("tmux 3.4")
        TmuxBackend(runner=runner)
        # The first run call must be for '-V'
        first_call_args = runner.run.call_args_list[0]
        argv = first_call_args[0][0]
        assert argv[-1] == "-V"

    def test_capabilities_populated(self) -> None:
        backend, _ = _make_backend("tmux 3.4")
        caps = backend.capabilities
        assert caps.name == "tmux"
        assert caps.version == "tmux 3.4"
        assert caps.supports_split_before is True
        assert caps.supports_remain_on_exit is True
        assert caps.supports_capture_pane is True
        assert caps.supports_format_pane_dead is True

    def test_old_version_disables_features(self) -> None:
        backend, _ = _make_backend("tmux 1.4")
        caps = backend.capabilities
        assert caps.supports_split_before is False
        assert caps.supports_remain_on_exit is False
        assert caps.supports_capture_pane is False
        assert caps.supports_format_pane_dead is False

    def test_socket_path_included_in_version_probe(self) -> None:
        runner = MagicMock()
        runner.run.return_value = _make_result("tmux 3.4")
        sock = Path("/tmp/test.sock")
        TmuxBackend(runner=runner, socket_path=sock)
        first_argv = runner.run.call_args_list[0][0][0]
        assert first_argv[1] == "-S"
        assert first_argv[2] == str(sock)

    def test_version_1_4_capture_pane_false(self) -> None:
        """tmux 1.4 is below the 1.5 threshold for capture-pane."""
        backend, _ = _make_backend("tmux 1.4")
        assert backend.capabilities.supports_capture_pane is False


# ---------------------------------------------------------------------------
# list_sessions
# ---------------------------------------------------------------------------

_SESSION_OUTPUT = "$0|mysession|1|1700000000\n$1|other|0|1700001000\n"


class TestListSessions:
    def test_argv_shape(self) -> None:
        backend, runner = _make_backend()
        runner.run.return_value = _make_result(_SESSION_OUTPUT)
        backend.list_sessions()
        argv = runner.run.call_args[0][0]
        assert argv == ["tmux", "list-sessions", "-F", backend._SESSION_FMT]

    def test_parses_sessions(self) -> None:
        backend, runner = _make_backend()
        runner.run.return_value = _make_result(_SESSION_OUTPUT)
        sessions = backend.list_sessions()
        assert len(sessions) == 2
        assert sessions[0].id == "$0"
        assert sessions[0].name == "mysession"
        assert sessions[0].attached is True
        assert sessions[1].attached is False

    def test_returns_empty_on_nonzero(self) -> None:
        backend, runner = _make_backend()
        runner.run.return_value = _make_result("", returncode=1)
        assert backend.list_sessions() == []

    def test_socket_prefix_injected(self) -> None:
        backend, runner = _make_backend(socket_path=Path("/run/tmux.sock"))
        runner.run.return_value = _make_result(_SESSION_OUTPUT)
        backend.list_sessions()
        argv = runner.run.call_args[0][0]
        assert argv[:3] == ["tmux", "-S", "/run/tmux.sock"]


# ---------------------------------------------------------------------------
# list_windows
# ---------------------------------------------------------------------------

_WINDOW_OUTPUT = "@0|0|main|even-horizontal\n@1|1|logs|tiled\n"


class TestListWindows:
    def test_argv_shape(self) -> None:
        backend, runner = _make_backend()
        runner.run.return_value = _make_result(_WINDOW_OUTPUT)
        backend.list_windows("mysession")
        argv = runner.run.call_args[0][0]
        assert argv == [
            "tmux",
            "list-windows",
            "-t",
            "mysession",
            "-F",
            backend._WINDOW_FMT,
        ]

    def test_parses_windows(self) -> None:
        backend, runner = _make_backend()
        runner.run.return_value = _make_result(_WINDOW_OUTPUT)
        windows = backend.list_windows("mysession")
        assert len(windows) == 2
        assert windows[0].id == "@0"
        assert windows[0].index == 0
        assert windows[0].name == "main"
        assert windows[0].layout == "even-horizontal"
        assert windows[0].session == "mysession"
        assert windows[1].name == "logs"


# ---------------------------------------------------------------------------
# list_panes
# ---------------------------------------------------------------------------

_PANE_OUTPUT = "s1|0|0|%0|1234|0|bash|80|24\ns1|0|1|%1||1|sleep|40|24\n"


class TestListPanes:
    def test_argv_all_sessions(self) -> None:
        backend, runner = _make_backend()
        runner.run.return_value = _make_result(_PANE_OUTPUT)
        backend.list_panes()
        argv = runner.run.call_args[0][0]
        assert argv == ["tmux", "list-panes", "-a", "-F", backend._PANE_FMT]

    def test_argv_with_target(self) -> None:
        backend, runner = _make_backend()
        runner.run.return_value = _make_result(_PANE_OUTPUT)
        backend.list_panes(target="s1")
        argv = runner.run.call_args[0][0]
        assert argv == ["tmux", "list-panes", "-t", "s1", "-F", backend._PANE_FMT]

    def test_parses_panes(self) -> None:
        backend, runner = _make_backend()
        runner.run.return_value = _make_result(_PANE_OUTPUT)
        panes = backend.list_panes()
        assert len(panes) == 2
        assert panes[0].id == "%0"
        assert panes[0].session == "s1"
        assert panes[0].window_index == 0
        assert panes[0].pane_index == 0
        assert panes[0].pid == 1234
        assert panes[0].dead is False
        assert panes[0].current_command == "bash"
        assert panes[0].width == 80
        assert panes[0].height == 24

    def test_dead_pane_no_pid(self) -> None:
        backend, runner = _make_backend()
        runner.run.return_value = _make_result("s1|0|1|%1||1|sleep|40|24\n")
        panes = backend.list_panes()
        assert panes[0].pid is None
        assert panes[0].dead is True


# ---------------------------------------------------------------------------
# new_session
# ---------------------------------------------------------------------------


class TestNewSession:
    def test_argv_detached(self) -> None:
        backend, runner = _make_backend()
        runner.run.side_effect = [
            _make_result(),  # new-session
            _make_result("$0|newsess|0|1700000000\n"),  # list-sessions for re-query
        ]
        backend.new_session("newsess", 120, 40)
        first_call = runner.run.call_args_list[0]
        argv = first_call[0][0]
        assert argv == ["tmux", "new-session", "-d", "-s", "newsess", "-x", "120", "-y", "40"]

    def test_argv_attached(self) -> None:
        backend, runner = _make_backend()
        runner.run.side_effect = [
            _make_result(),
            _make_result("$0|newsess|1|1700000000\n"),
        ]
        backend.new_session("newsess", 80, 24, detached=False)
        first_argv = runner.run.call_args_list[0][0][0]
        assert "-d" not in first_argv

    def test_returns_session(self) -> None:
        backend, runner = _make_backend()
        runner.run.side_effect = [
            _make_result(),
            _make_result("$0|newsess|0|1700000000\n"),
        ]
        sess = backend.new_session("newsess", 80, 24)
        assert sess.name == "newsess"


# ---------------------------------------------------------------------------
# attach_session / kill_session
# ---------------------------------------------------------------------------


class TestAttachKillSession:
    def test_attach_argv(self) -> None:
        backend, runner = _make_backend()
        runner.run.return_value = _make_result()
        backend.attach_session("mysess")
        argv = runner.run.call_args[0][0]
        assert argv == ["tmux", "attach-session", "-t", "mysess"]

    def test_kill_argv(self) -> None:
        backend, runner = _make_backend()
        runner.run.return_value = _make_result()
        backend.kill_session("mysess")
        argv = runner.run.call_args[0][0]
        assert argv == ["tmux", "kill-session", "-t", "mysess"]


# ---------------------------------------------------------------------------
# new_window
# ---------------------------------------------------------------------------


class TestNewWindow:
    def test_argv_no_name(self) -> None:
        backend, runner = _make_backend()
        runner.run.return_value = _make_result("@2|2|bash|even-horizontal\n")
        backend.new_window("mysess")
        argv = runner.run.call_args[0][0]
        assert argv == [
            "tmux",
            "new-window",
            "-t",
            "mysess",
            "-P",
            "-F",
            backend._WINDOW_FMT,
        ]

    def test_argv_with_name(self) -> None:
        backend, runner = _make_backend()
        runner.run.return_value = _make_result("@2|2|editor|even-horizontal\n")
        backend.new_window("mysess", name="editor")
        argv = runner.run.call_args[0][0]
        assert "-n" in argv
        assert "editor" in argv

    def test_returns_window(self) -> None:
        backend, runner = _make_backend()
        runner.run.return_value = _make_result("@2|2|editor|tiled\n")
        win = backend.new_window("mysess", name="editor")
        assert win.index == 2
        assert win.name == "editor"
        assert win.layout == "tiled"


# ---------------------------------------------------------------------------
# kill_window
# ---------------------------------------------------------------------------


class TestKillWindow:
    def test_argv(self) -> None:
        backend, runner = _make_backend()
        runner.run.return_value = _make_result()
        backend.kill_window("mysess:1")
        argv = runner.run.call_args[0][0]
        assert argv == ["tmux", "kill-window", "-t", "mysess:1"]


# ---------------------------------------------------------------------------
# select_layout / capture_layout
# ---------------------------------------------------------------------------


class TestLayoutMethods:
    def test_select_layout_argv(self) -> None:
        backend, runner = _make_backend()
        runner.run.return_value = _make_result()
        backend.select_layout("mysess:1", "tiled")
        argv = runner.run.call_args[0][0]
        assert argv == ["tmux", "select-layout", "-t", "mysess:1", "tiled"]

    def test_capture_layout_argv(self) -> None:
        backend, runner = _make_backend()
        runner.run.return_value = _make_result("tiled,80x24,0,0{}\n")
        layout = backend.capture_layout("mysess:1")
        argv = runner.run.call_args[0][0]
        assert argv == [
            "tmux",
            "display-message",
            "-p",
            "-t",
            "mysess:1",
            "#{window_layout}",
        ]
        assert layout == "tiled,80x24,0,0{}"

    def test_set_window_option_argv(self) -> None:
        backend, runner = _make_backend()
        runner.run.return_value = _make_result()
        backend.set_window_option("mysess:1", "remain-on-exit", "on")
        argv = runner.run.call_args[0][0]
        assert argv == [
            "tmux",
            "set-window-option",
            "-t",
            "mysess:1",
            "remain-on-exit",
            "on",
        ]


# ---------------------------------------------------------------------------
# split_pane
# ---------------------------------------------------------------------------

_SPLIT_PANE_OUTPUT = "%3|s1|0|2|5678|0|bash|40|24\n"


class TestSplitPane:
    def test_argv_horizontal(self) -> None:
        backend, runner = _make_backend()
        runner.run.return_value = _make_result(_SPLIT_PANE_OUTPUT)
        backend.split_pane("%0", "h")
        argv = runner.run.call_args[0][0]
        assert argv == [
            "tmux",
            "split-window",
            "-h",
            "-t",
            "%0",
            "-P",
            "-F",
            backend._SPLIT_PANE_FMT,
        ]

    def test_argv_vertical(self) -> None:
        backend, runner = _make_backend()
        runner.run.return_value = _make_result(_SPLIT_PANE_OUTPUT)
        backend.split_pane("%0", "v")
        argv = runner.run.call_args[0][0]
        assert "-v" in argv
        assert "-h" not in argv

    def test_argv_before_flag(self) -> None:
        backend, runner = _make_backend()
        runner.run.return_value = _make_result(_SPLIT_PANE_OUTPUT)
        backend.split_pane("%0", "h", before=True)
        argv = runner.run.call_args[0][0]
        assert "-b" in argv

    def test_argv_no_before_flag_by_default(self) -> None:
        backend, runner = _make_backend()
        runner.run.return_value = _make_result(_SPLIT_PANE_OUTPUT)
        backend.split_pane("%0", "h")
        argv = runner.run.call_args[0][0]
        assert "-b" not in argv

    def test_returns_pane(self) -> None:
        backend, runner = _make_backend()
        runner.run.return_value = _make_result(_SPLIT_PANE_OUTPUT)
        pane = backend.split_pane("%0", "h")
        assert pane.id == "%3"
        assert pane.pid == 5678
        assert pane.dead is False
        assert pane.width == 40
        assert pane.height == 24


# ---------------------------------------------------------------------------
# select_pane / swap_panes / resize_pane
# ---------------------------------------------------------------------------


class TestPaneMiscMethods:
    def test_select_pane_argv(self) -> None:
        backend, runner = _make_backend()
        runner.run.return_value = _make_result()
        backend.select_pane("%2")
        argv = runner.run.call_args[0][0]
        assert argv == ["tmux", "select-pane", "-t", "%2"]

    def test_swap_panes_argv(self) -> None:
        backend, runner = _make_backend()
        runner.run.return_value = _make_result()
        backend.swap_panes("%1", "%2")
        argv = runner.run.call_args[0][0]
        assert argv == ["tmux", "swap-pane", "-s", "%1", "-t", "%2"]

    def test_resize_pane_argv(self) -> None:
        backend, runner = _make_backend()
        runner.run.return_value = _make_result()
        backend.resize_pane("%0", 100, 50)
        argv = runner.run.call_args[0][0]
        assert argv == ["tmux", "resize-pane", "-t", "%0", "-x", "100", "-y", "50"]


# ---------------------------------------------------------------------------
# break_pane / move_pane
# ---------------------------------------------------------------------------


class TestBreakMovePane:
    def test_break_pane_argv_detached(self) -> None:
        backend, runner = _make_backend()
        runner.run.return_value = _make_result("@3|2|bash|even-horizontal\n")
        backend.break_pane("%1")
        argv = runner.run.call_args[0][0]
        assert argv == [
            "tmux",
            "break-pane",
            "-s",
            "%1",
            "-d",
            "-P",
            "-F",
            backend._WINDOW_FMT,
        ]

    def test_break_pane_argv_attached(self) -> None:
        backend, runner = _make_backend()
        runner.run.return_value = _make_result("@3|2|bash|even-horizontal\n")
        backend.break_pane("%1", detached=False)
        argv = runner.run.call_args[0][0]
        assert "-d" not in argv

    def test_break_pane_returns_window(self) -> None:
        backend, runner = _make_backend()
        runner.run.return_value = _make_result("@3|2|bash|even-horizontal\n")
        win = backend.break_pane("%1")
        assert win.id == "@3"
        assert win.index == 2

    def test_move_pane_argv(self) -> None:
        backend, runner = _make_backend()
        runner.run.return_value = _make_result()
        backend.move_pane("%2", "mysess:1")
        argv = runner.run.call_args[0][0]
        assert argv == ["tmux", "move-pane", "-s", "%2", "-t", "mysess:1"]


# ---------------------------------------------------------------------------
# send_keys
# ---------------------------------------------------------------------------


class TestSendKeys:
    def test_argv_with_enter(self) -> None:
        backend, runner = _make_backend()
        runner.run.return_value = _make_result()
        backend.send_keys("%0", "echo hello")
        argv = runner.run.call_args[0][0]
        assert argv == ["tmux", "send-keys", "-t", "%0", "echo hello", "Enter"]

    def test_argv_without_enter(self) -> None:
        backend, runner = _make_backend()
        runner.run.return_value = _make_result()
        backend.send_keys("%0", "echo hello", enter=False)
        argv = runner.run.call_args[0][0]
        assert argv == ["tmux", "send-keys", "-t", "%0", "echo hello"]

    def test_keys_passed_as_single_arg(self) -> None:
        """Keys with spaces must be a single positional arg, not split."""
        backend, runner = _make_backend()
        runner.run.return_value = _make_result()
        backend.send_keys("%0", "ls -la /tmp")
        argv = runner.run.call_args[0][0]
        # The keys "ls -la /tmp" must appear as one element in argv.
        assert "ls -la /tmp" in argv


# ---------------------------------------------------------------------------
# kill_pane / respawn_pane
# ---------------------------------------------------------------------------


class TestKillRespawnPane:
    def test_kill_pane_argv(self) -> None:
        backend, runner = _make_backend()
        runner.run.return_value = _make_result()
        backend.kill_pane("%3")
        argv = runner.run.call_args[0][0]
        assert argv == ["tmux", "kill-pane", "-t", "%3"]

    def test_respawn_pane_kill_existing(self) -> None:
        backend, runner = _make_backend()
        runner.run.return_value = _make_result()
        backend.respawn_pane("%0", "bash script.sh")
        argv = runner.run.call_args[0][0]
        assert argv == ["tmux", "respawn-pane", "-k", "-t", "%0", "bash script.sh"]

    def test_respawn_pane_no_kill(self) -> None:
        backend, runner = _make_backend()
        runner.run.return_value = _make_result()
        backend.respawn_pane("%0", "bash script.sh", kill_existing=False)
        argv = runner.run.call_args[0][0]
        assert "-k" not in argv
        assert "bash script.sh" in argv


# ---------------------------------------------------------------------------
# capture_pane
# ---------------------------------------------------------------------------


class TestCapturePane:
    def test_argv_default_lines(self) -> None:
        backend, runner = _make_backend()
        runner.run.return_value = _make_result("some output\n")
        backend.capture_pane("%0")
        argv = runner.run.call_args[0][0]
        assert argv == ["tmux", "capture-pane", "-p", "-t", "%0", "-S", "-200"]

    def test_argv_custom_lines(self) -> None:
        backend, runner = _make_backend()
        runner.run.return_value = _make_result("some output\n")
        backend.capture_pane("%0", lines=50)
        argv = runner.run.call_args[0][0]
        assert argv == ["tmux", "capture-pane", "-p", "-t", "%0", "-S", "-50"]

    def test_returns_stdout(self) -> None:
        backend, runner = _make_backend()
        expected = "line1\nline2\n"
        runner.run.return_value = _make_result(expected)
        assert backend.capture_pane("%0") == expected


# ---------------------------------------------------------------------------
# Default timeout is 5 seconds per §7.2
# ---------------------------------------------------------------------------


class TestTimeout:
    def test_default_timeout_5s(self) -> None:
        backend, runner = _make_backend()
        runner.run.return_value = _make_result()
        backend.kill_pane("%0")
        call_kwargs = runner.run.call_args[1]
        assert call_kwargs.get("timeout", 5.0) == 5.0
