# -*- coding: utf-8 -*-
"""
Unit tests for ItmuxBackend — all ProcessRunner calls are mocked.

Tests assert the exact argv shape and that output is parsed into the
correct typed dataclass values.  itmux-specific behaviours covered:

- Capability probing parses itmux version correctly.
- Named-pipe socket path (``\\\\.\\pipe\\itmux-...``) is accepted via ``-S``.
- ``resize_pane`` applies pixel↔cell conversion when pixel_geometry is active.
- ``new_session`` falls back to create-then-resize when
  ``supports_initial_size_in_new_session`` is False.
- ``split_pane`` with ``before=True`` simulates the ``-b`` flag via
  ``split-window`` + ``swap-pane`` when ``supports_split_before`` is False.
"""

from __future__ import annotations

import subprocess
from typing import Any
from unittest.mock import MagicMock

from cpsm.platform.itmux_backend import ItmuxBackend

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
    version_stdout: str = "itmux 1.2",
    *,
    socket_path: Any = None,
) -> tuple[ItmuxBackend, MagicMock]:
    """Create an ItmuxBackend with a mocked ProcessRunner.

    Returns the backend and the runner mock so tests can assert calls and
    configure return values.  The runner mock is reset after the probe call
    that happens inside ``__init__`` so tests start with a clean call list.
    """
    runner = MagicMock()
    runner.run.return_value = _make_result(version_stdout)
    backend = ItmuxBackend(runner=runner, socket_path=socket_path)
    runner.run.reset_mock()
    return backend, runner


# ---------------------------------------------------------------------------
# Capability probing
# ---------------------------------------------------------------------------


class TestCapabilityProbing:
    def test_probe_called_with_version_flag(self) -> None:
        runner = MagicMock()
        runner.run.return_value = _make_result("itmux 1.2")
        ItmuxBackend(runner=runner)
        first_argv = runner.run.call_args_list[0][0][0]
        assert first_argv[-1] == "-V"

    def test_capabilities_name_is_itmux(self) -> None:
        backend, _ = _make_backend("itmux 1.2")
        assert backend.capabilities.name == "itmux"

    def test_capabilities_version_stored(self) -> None:
        backend, _ = _make_backend("itmux 1.2")
        assert backend.capabilities.version == "itmux 1.2"

    def test_modern_version_all_features_enabled(self) -> None:
        backend, _ = _make_backend("itmux 1.2")
        caps = backend.capabilities
        assert caps.supports_split_before is True
        assert caps.supports_remain_on_exit is True
        assert caps.supports_capture_pane is True
        assert caps.supports_format_pane_dead is True
        assert caps.supports_initial_size_in_new_session is True

    def test_old_version_disables_split_before(self) -> None:
        """itmux < 1.2 lacks split-window -b."""
        backend, _ = _make_backend("itmux 1.1")
        assert backend.capabilities.supports_split_before is False

    def test_old_version_disables_initial_size(self) -> None:
        """itmux < 1.1 ignores new-session -x/-y."""
        backend, _ = _make_backend("itmux 1.0")
        assert backend.capabilities.supports_initial_size_in_new_session is False

    def test_very_old_version_no_capture_pane(self) -> None:
        """itmux < 1.0 has no capture-pane."""
        backend, _ = _make_backend("itmux 0.9")
        assert backend.capabilities.supports_capture_pane is False

    def test_pixel_geometry_enabled_for_modern(self) -> None:
        """itmux >= 1.0 uses pixel geometry."""
        backend, _ = _make_backend("itmux 1.0")
        assert backend._pixel_geometry is True

    def test_pixel_geometry_disabled_for_old(self) -> None:
        """itmux < 1.0 uses cell geometry like tmux."""
        backend, _ = _make_backend("itmux 0.9")
        assert backend._pixel_geometry is False

    def test_socket_path_included_in_version_probe(self) -> None:
        runner = MagicMock()
        runner.run.return_value = _make_result("itmux 1.2")
        ItmuxBackend(runner=runner, socket_path=r"\\.\pipe\itmux-test")
        first_argv = runner.run.call_args_list[0][0][0]
        assert first_argv[1] == "-S"
        assert first_argv[2] == r"\\.\pipe\itmux-test"

    def test_binary_name_in_probe_argv(self) -> None:
        runner = MagicMock()
        runner.run.return_value = _make_result("itmux 1.2")
        ItmuxBackend(runner=runner, itmux_binary="C:\\itmux\\itmux.exe")
        first_argv = runner.run.call_args_list[0][0][0]
        assert first_argv[0] == "C:\\itmux\\itmux.exe"


# ---------------------------------------------------------------------------
# Named-pipe socket prefix
# ---------------------------------------------------------------------------


class TestNamedPipeSocket:
    def test_named_pipe_path_injected_in_every_call(self) -> None:
        """Windows named-pipe path appears as -S <path> in every command."""
        pipe = r"\\.\pipe\itmux-alice"
        backend, runner = _make_backend(socket_path=pipe)
        runner.run.return_value = _make_result()
        backend.kill_session("sess")
        argv = runner.run.call_args[0][0]
        assert argv[0] == "itmux"
        assert argv[1] == "-S"
        assert argv[2] == pipe

    def test_no_socket_no_dash_s(self) -> None:
        backend, runner = _make_backend()
        runner.run.return_value = _make_result()
        backend.kill_session("sess")
        argv = runner.run.call_args[0][0]
        assert "-S" not in argv


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
        assert argv == ["itmux", "list-sessions", "-F", backend._SESSION_FMT]

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
        pipe = r"\\.\pipe\itmux-x"
        backend, runner = _make_backend(socket_path=pipe)
        runner.run.return_value = _make_result(_SESSION_OUTPUT)
        backend.list_sessions()
        argv = runner.run.call_args[0][0]
        assert argv[:3] == ["itmux", "-S", pipe]


# ---------------------------------------------------------------------------
# new_session — with initial size supported
# ---------------------------------------------------------------------------


class TestNewSessionWithInitialSize:
    def test_argv_detached_with_size(self) -> None:
        """Modern itmux: -x/-y included in new-session."""
        backend, runner = _make_backend("itmux 1.2")
        runner.run.side_effect = [
            _make_result(),  # new-session
            _make_result("$0|newsess|0|1700000000\n"),  # list-sessions
        ]
        backend.new_session("newsess", 120, 40)
        first_argv = runner.run.call_args_list[0][0][0]
        assert first_argv == [
            "itmux",
            "new-session",
            "-d",
            "-s",
            "newsess",
            "-x",
            "120",
            "-y",
            "40",
        ]

    def test_argv_attached_no_d_flag(self) -> None:
        backend, runner = _make_backend("itmux 1.2")
        runner.run.side_effect = [
            _make_result(),
            _make_result("$0|newsess|1|1700000000\n"),
        ]
        backend.new_session("newsess", 80, 24, detached=False)
        first_argv = runner.run.call_args_list[0][0][0]
        assert "-d" not in first_argv

    def test_returns_session(self) -> None:
        backend, runner = _make_backend("itmux 1.2")
        runner.run.side_effect = [
            _make_result(),
            _make_result("$0|newsess|0|1700000000\n"),
        ]
        sess = backend.new_session("newsess", 80, 24)
        assert sess.name == "newsess"


# ---------------------------------------------------------------------------
# new_session — without initial size (old itmux, must resize after create)
# ---------------------------------------------------------------------------


class TestNewSessionWithoutInitialSize:
    def test_no_xy_flags_when_not_supported(self) -> None:
        """Old itmux: new-session is issued without -x/-y."""
        backend, runner = _make_backend("itmux 1.0")
        # side_effect order: new-session, list-panes (for resize), resize-pane,
        # list-sessions (re-query)
        _pane_output = "newsess|0|0|%0|1234|0|bash|80|24\n"
        runner.run.side_effect = [
            _make_result(),  # new-session
            _make_result(_pane_output),  # list-panes
            _make_result("10|20"),  # display-message cell dims (resize)
            _make_result(),  # resize-pane
            _make_result("$0|newsess|0|1700000000\n"),  # list-sessions
        ]
        backend.new_session("newsess", 120, 40)
        new_session_argv = runner.run.call_args_list[0][0][0]
        assert "-x" not in new_session_argv
        assert "-y" not in new_session_argv

    def test_resize_pane_called_after_create(self) -> None:
        """Old itmux: resize_pane is invoked on the first pane after create."""
        backend, runner = _make_backend("itmux 1.0")
        _pane_output = "newsess|0|0|%0|1234|0|bash|80|24\n"
        runner.run.side_effect = [
            _make_result(),  # new-session
            _make_result(_pane_output),  # list-panes
            _make_result("10|20"),  # display-message cell dims
            _make_result(),  # resize-pane
            _make_result("$0|newsess|0|1700000000\n"),  # list-sessions
        ]
        backend.new_session("newsess", 120, 40)
        # resize-pane call is at index 3 in this sequence
        resize_argv = runner.run.call_args_list[3][0][0]
        assert "resize-pane" in resize_argv
        assert "-t" in resize_argv
        assert "%0" in resize_argv


# ---------------------------------------------------------------------------
# attach_session / kill_session
# ---------------------------------------------------------------------------


class TestAttachKillSession:
    def test_attach_argv(self) -> None:
        backend, runner = _make_backend()
        runner.run.return_value = _make_result()
        backend.attach_session("mysess")
        argv = runner.run.call_args[0][0]
        assert argv == ["itmux", "attach-session", "-t", "mysess"]

    def test_kill_argv(self) -> None:
        backend, runner = _make_backend()
        runner.run.return_value = _make_result()
        backend.kill_session("mysess")
        argv = runner.run.call_args[0][0]
        assert argv == ["itmux", "kill-session", "-t", "mysess"]


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
            "itmux",
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
        assert windows[0].session == "mysession"


# ---------------------------------------------------------------------------
# new_window / kill_window
# ---------------------------------------------------------------------------


class TestNewWindow:
    def test_argv_no_name(self) -> None:
        backend, runner = _make_backend()
        runner.run.return_value = _make_result("@2|2|bash|even-horizontal\n")
        backend.new_window("mysess")
        argv = runner.run.call_args[0][0]
        assert argv == [
            "itmux",
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


class TestKillWindow:
    def test_argv(self) -> None:
        backend, runner = _make_backend()
        runner.run.return_value = _make_result()
        backend.kill_window("mysess:1")
        argv = runner.run.call_args[0][0]
        assert argv == ["itmux", "kill-window", "-t", "mysess:1"]


# ---------------------------------------------------------------------------
# select_layout / capture_layout / set_window_option
# ---------------------------------------------------------------------------


class TestLayoutMethods:
    def test_select_layout_argv(self) -> None:
        backend, runner = _make_backend()
        runner.run.return_value = _make_result()
        backend.select_layout("mysess:1", "tiled")
        argv = runner.run.call_args[0][0]
        assert argv == ["itmux", "select-layout", "-t", "mysess:1", "tiled"]

    def test_capture_layout_argv(self) -> None:
        backend, runner = _make_backend()
        runner.run.return_value = _make_result("tiled,80x24,0,0{}\n")
        layout = backend.capture_layout("mysess:1")
        argv = runner.run.call_args[0][0]
        assert argv == [
            "itmux",
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
            "itmux",
            "set-window-option",
            "-t",
            "mysess:1",
            "remain-on-exit",
            "on",
        ]


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
        assert argv == ["itmux", "list-panes", "-a", "-F", backend._PANE_FMT]

    def test_argv_with_target(self) -> None:
        backend, runner = _make_backend()
        runner.run.return_value = _make_result(_PANE_OUTPUT)
        backend.list_panes(target="s1")
        argv = runner.run.call_args[0][0]
        assert argv == ["itmux", "list-panes", "-t", "s1", "-F", backend._PANE_FMT]

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
# split_pane — with supports_split_before=True (native -b flag)
# ---------------------------------------------------------------------------

_SPLIT_PANE_OUTPUT = "%3|s1|0|2|5678|0|bash|40|24\n"


class TestSplitPaneNative:
    def test_argv_horizontal(self) -> None:
        backend, runner = _make_backend("itmux 1.2")
        runner.run.return_value = _make_result(_SPLIT_PANE_OUTPUT)
        backend.split_pane("%0", "h")
        argv = runner.run.call_args[0][0]
        assert argv == [
            "itmux",
            "split-window",
            "-h",
            "-t",
            "%0",
            "-P",
            "-F",
            backend._SPLIT_PANE_FMT,
        ]

    def test_argv_vertical(self) -> None:
        backend, runner = _make_backend("itmux 1.2")
        runner.run.return_value = _make_result(_SPLIT_PANE_OUTPUT)
        backend.split_pane("%0", "v")
        argv = runner.run.call_args[0][0]
        assert "-v" in argv
        assert "-h" not in argv

    def test_argv_before_flag_native(self) -> None:
        """Modern itmux: -b flag is emitted directly."""
        backend, runner = _make_backend("itmux 1.2")
        runner.run.return_value = _make_result(_SPLIT_PANE_OUTPUT)
        backend.split_pane("%0", "h", before=True)
        argv = runner.run.call_args[0][0]
        assert "-b" in argv

    def test_no_before_flag_by_default(self) -> None:
        backend, runner = _make_backend("itmux 1.2")
        runner.run.return_value = _make_result(_SPLIT_PANE_OUTPUT)
        backend.split_pane("%0", "h")
        argv = runner.run.call_args[0][0]
        assert "-b" not in argv

    def test_returns_pane(self) -> None:
        backend, runner = _make_backend("itmux 1.2")
        runner.run.return_value = _make_result(_SPLIT_PANE_OUTPUT)
        pane = backend.split_pane("%0", "h")
        assert pane.id == "%3"
        assert pane.pid == 5678
        assert pane.dead is False
        assert pane.width == 40
        assert pane.height == 24


# ---------------------------------------------------------------------------
# split_pane — without supports_split_before (simulate via swap-pane)
# ---------------------------------------------------------------------------


class TestSplitPaneSimulatedBefore:
    def test_no_b_flag_in_split_window_call(self) -> None:
        """Old itmux: split-window is called without -b."""
        backend, runner = _make_backend("itmux 1.1")
        runner.run.side_effect = [
            _make_result(_SPLIT_PANE_OUTPUT),  # split-window
            _make_result(),  # swap-pane
        ]
        backend.split_pane("%0", "h", before=True)
        split_argv = runner.run.call_args_list[0][0][0]
        assert "-b" not in split_argv

    def test_swap_pane_called_after_split(self) -> None:
        """Old itmux: swap-pane is called to simulate -b positioning."""
        backend, runner = _make_backend("itmux 1.1")
        runner.run.side_effect = [
            _make_result(_SPLIT_PANE_OUTPUT),  # split-window
            _make_result(),  # swap-pane
        ]
        backend.split_pane("%0", "h", before=True)
        swap_argv = runner.run.call_args_list[1][0][0]
        # swap-pane -s <new_pane> -t <original_target>
        assert "swap-pane" in swap_argv
        assert "-s" in swap_argv
        assert "%3" in swap_argv  # new pane id from _SPLIT_PANE_OUTPUT
        assert "%0" in swap_argv  # original target

    def test_swap_pane_not_called_without_before(self) -> None:
        """Old itmux: swap-pane is NOT called when before=False."""
        backend, runner = _make_backend("itmux 1.1")
        runner.run.return_value = _make_result(_SPLIT_PANE_OUTPUT)
        backend.split_pane("%0", "h", before=False)
        # Only one call: split-window
        assert runner.run.call_count == 1

    def test_returns_new_pane_after_swap(self) -> None:
        """Returned Pane is the newly created pane (not the original target)."""
        backend, runner = _make_backend("itmux 1.1")
        runner.run.side_effect = [
            _make_result(_SPLIT_PANE_OUTPUT),
            _make_result(),
        ]
        pane = backend.split_pane("%0", "h", before=True)
        assert pane.id == "%3"

    def test_vertical_split_before_simulation(self) -> None:
        """Simulation also works for vertical splits."""
        backend, runner = _make_backend("itmux 1.1")
        runner.run.side_effect = [
            _make_result(_SPLIT_PANE_OUTPUT),
            _make_result(),
        ]
        backend.split_pane("%0", "v", before=True)
        split_argv = runner.run.call_args_list[0][0][0]
        assert "-v" in split_argv
        assert "-b" not in split_argv
        swap_argv = runner.run.call_args_list[1][0][0]
        assert "swap-pane" in swap_argv


# ---------------------------------------------------------------------------
# resize_pane — pixel↔cell conversion
# ---------------------------------------------------------------------------


class TestResizePanePixelMode:
    def test_cell_mode_passes_dimensions_unchanged(self) -> None:
        """Old itmux (pixel_geometry=False): dimensions are not scaled."""
        backend, runner = _make_backend("itmux 0.9")
        assert backend._pixel_geometry is False
        runner.run.return_value = _make_result()
        backend.resize_pane("%0", 100, 50)
        argv = runner.run.call_args[0][0]
        assert argv == ["itmux", "resize-pane", "-t", "%0", "-x", "100", "-y", "50"]

    def test_pixel_mode_queries_cell_dimensions(self) -> None:
        """Modern itmux (pixel_geometry=True): cell dims are queried first."""
        backend, runner = _make_backend("itmux 1.2")
        assert backend._pixel_geometry is True
        runner.run.side_effect = [
            _make_result("8|16"),  # display-message cell dims (8px wide, 16px tall)
            _make_result(),  # resize-pane
        ]
        backend.resize_pane("%0", 10, 5)
        cell_query_argv = runner.run.call_args_list[0][0][0]
        assert "display-message" in cell_query_argv
        assert "#{client_cell_width}|#{client_cell_height}" in cell_query_argv

    def test_pixel_mode_multiplies_dimensions(self) -> None:
        """Modern itmux: width and height are multiplied by cell pixel size."""
        backend, runner = _make_backend("itmux 1.2")
        runner.run.side_effect = [
            _make_result("8|16"),  # cell_w=8, cell_h=16
            _make_result(),  # resize-pane
        ]
        backend.resize_pane("%0", 10, 5)
        resize_argv = runner.run.call_args_list[1][0][0]
        # 10 cells * 8px = 80; 5 cells * 16px = 80
        assert resize_argv == ["itmux", "resize-pane", "-t", "%0", "-x", "80", "-y", "80"]

    def test_pixel_mode_fallback_on_bad_cell_output(self) -> None:
        """When cell dimension query returns garbage, fallback to 1x1 pixels."""
        backend, runner = _make_backend("itmux 1.2")
        runner.run.side_effect = [
            _make_result("bad|output"),  # non-numeric — falls back to 1,1
            _make_result(),
        ]
        backend.resize_pane("%0", 80, 24)
        resize_argv = runner.run.call_args_list[1][0][0]
        # 80 * 1 = 80, 24 * 1 = 24
        assert "-x" in resize_argv
        assert "80" in resize_argv
        assert "24" in resize_argv

    def test_pixel_mode_argv_shape(self) -> None:
        """Full argv shape: itmux resize-pane -t <id> -x <px> -y <px>."""
        backend, runner = _make_backend("itmux 1.2")
        runner.run.side_effect = [
            _make_result("10|20"),
            _make_result(),
        ]
        backend.resize_pane("%2", 3, 4)
        resize_argv = runner.run.call_args_list[1][0][0]
        # 3 * 10 = 30, 4 * 20 = 80
        assert resize_argv == ["itmux", "resize-pane", "-t", "%2", "-x", "30", "-y", "80"]


# ---------------------------------------------------------------------------
# select_pane / swap_panes
# ---------------------------------------------------------------------------


class TestPaneMiscMethods:
    def test_select_pane_argv(self) -> None:
        backend, runner = _make_backend()
        runner.run.return_value = _make_result()
        backend.select_pane("%2")
        argv = runner.run.call_args[0][0]
        assert argv == ["itmux", "select-pane", "-t", "%2"]

    def test_swap_panes_argv(self) -> None:
        backend, runner = _make_backend()
        runner.run.return_value = _make_result()
        backend.swap_panes("%1", "%2")
        argv = runner.run.call_args[0][0]
        assert argv == ["itmux", "swap-pane", "-s", "%1", "-t", "%2"]


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
            "itmux",
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
        assert argv == ["itmux", "move-pane", "-s", "%2", "-t", "mysess:1"]


# ---------------------------------------------------------------------------
# send_keys
# ---------------------------------------------------------------------------


class TestSendKeys:
    def test_argv_with_enter(self) -> None:
        backend, runner = _make_backend()
        runner.run.return_value = _make_result()
        backend.send_keys("%0", "echo hello")
        argv = runner.run.call_args[0][0]
        assert argv == ["itmux", "send-keys", "-t", "%0", "echo hello", "Enter"]

    def test_argv_without_enter(self) -> None:
        backend, runner = _make_backend()
        runner.run.return_value = _make_result()
        backend.send_keys("%0", "echo hello", enter=False)
        argv = runner.run.call_args[0][0]
        assert argv == ["itmux", "send-keys", "-t", "%0", "echo hello"]

    def test_keys_passed_as_single_arg(self) -> None:
        """Keys with spaces must be a single positional arg, not split."""
        backend, runner = _make_backend()
        runner.run.return_value = _make_result()
        backend.send_keys("%0", "ls -la /tmp")
        argv = runner.run.call_args[0][0]
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
        assert argv == ["itmux", "kill-pane", "-t", "%3"]

    def test_respawn_pane_kill_existing(self) -> None:
        backend, runner = _make_backend()
        runner.run.return_value = _make_result()
        backend.respawn_pane("%0", "bash script.sh")
        argv = runner.run.call_args[0][0]
        assert argv == ["itmux", "respawn-pane", "-k", "-t", "%0", "bash script.sh"]

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
        assert argv == ["itmux", "capture-pane", "-p", "-t", "%0", "-S", "-200"]

    def test_argv_custom_lines(self) -> None:
        backend, runner = _make_backend()
        runner.run.return_value = _make_result("some output\n")
        backend.capture_pane("%0", lines=50)
        argv = runner.run.call_args[0][0]
        assert argv == ["itmux", "capture-pane", "-p", "-t", "%0", "-S", "-50"]

    def test_returns_stdout(self) -> None:
        backend, runner = _make_backend()
        expected = "line1\nline2\n"
        runner.run.return_value = _make_result(expected)
        assert backend.capture_pane("%0") == expected


# ---------------------------------------------------------------------------
# Default timeout is 5 seconds
# ---------------------------------------------------------------------------


class TestTimeout:
    def test_default_timeout_5s(self) -> None:
        backend, runner = _make_backend()
        runner.run.return_value = _make_result()
        backend.kill_pane("%0")
        call_kwargs = runner.run.call_args[1]
        assert call_kwargs.get("timeout", 5.0) == 5.0
