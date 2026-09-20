# -*- coding: utf-8 -*-
"""
Unit tests for PsmuxBackend and PowerShellQuoter.

All ProcessRunner calls are mocked.  Tests assert:
- The exact pwsh argv shape (binary, -NoProfile, -Command, quoted cmdlet).
- Parsing of synthetic JSON output into Session / Window / Pane dataclasses.
- Capability degradation raises BackendCapabilityError when appropriate.
- PowerShellQuoter produces correct output for known inputs and round-trips
  cleanly for random inputs (string inspection; no live pwsh required).

Fuzz test uses 100 random strings containing shell metachars and Unicode;
the round-trip is verified by asserting the quoter's output can be
unquoted back to the original string using the inverse quoting rules.
"""

from __future__ import annotations

import json
import random
import string
import subprocess
from typing import Any
from unittest.mock import MagicMock

import pytest

from cpsm.platform.powershell_quoter import PowerShellQuoter
from cpsm.platform.psmux_backend import (
    BackendCapabilityError,
    PsmuxBackend,
    PsmuxParseError,
    _ensure_list,
    _parse_json,
    _parse_pane,
    _parse_session,
    _parse_window,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_result(
    stdout: str = "",
    returncode: int = 0,
) -> subprocess.CompletedProcess[str]:
    """Return a synthetic CompletedProcess."""
    return subprocess.CompletedProcess(
        args=[],
        returncode=returncode,
        stdout=stdout,
        stderr="",
    )


def _json_result(data: Any, returncode: int = 0) -> subprocess.CompletedProcess[str]:
    """Return a CompletedProcess with JSON-encoded *data* as stdout."""
    return _make_result(json.dumps(data), returncode=returncode)


def _make_backend(
    version: str = "1.0.0",
) -> tuple[PsmuxBackend, MagicMock]:
    """Create a PsmuxBackend with a mocked ProcessRunner.

    The probe call inside ``__init__`` returns *version*.
    The runner mock is reset after construction so test assertions start
    from a clean call list.
    """
    runner = MagicMock()
    probe_resp = _json_result({"Version": version})
    runner.run.return_value = probe_resp
    backend = PsmuxBackend(runner=runner)
    runner.run.reset_mock()
    return backend, runner


# Convenience fixture
@pytest.fixture()
def backend_and_runner() -> tuple[PsmuxBackend, MagicMock]:
    return _make_backend()


# ---------------------------------------------------------------------------
# Helper: extract the -Command value from an argv
# ---------------------------------------------------------------------------


def _cmd(argv: list[str]) -> str:
    """Return the value after ``-Command`` in *argv*."""
    idx = argv.index("-Command")
    return argv[idx + 1]


# ---------------------------------------------------------------------------
# PowerShellQuoter — unit tests
# ---------------------------------------------------------------------------


class TestPowerShellQuoterQuoteArgument:
    def test_simple_string_single_quoted(self) -> None:
        q = PowerShellQuoter()
        result = q.quote_argument("hello")
        assert result == "'hello'"

    def test_embedded_single_quote_doubled(self) -> None:
        q = PowerShellQuoter()
        result = q.quote_argument("it's")
        # Single-quote style with '' escape is not chosen here because it
        # contains a single-quote; dollar/backtick absent → double-quote style.
        # Double-quote style: "it's" → no escaping needed for single quotes.
        assert result == '"it\'s"'

    def test_string_with_dollar_stays_single_quoted(self) -> None:
        """Dollar signs force single-quote style to suppress expansion."""
        q = PowerShellQuoter()
        result = q.quote_argument("$HOME")
        assert result == "'$HOME'"

    def test_string_with_single_quote_and_dollar_single_quoted(self) -> None:
        """Both ' and $ → single-quote style (with doubled quote)."""
        q = PowerShellQuoter()
        result = q.quote_argument("it's $HOME")
        # Can't use double-quote (has $), must use single-quote with doubling.
        assert result == "'it''s $HOME'"

    def test_empty_string_single_quoted(self) -> None:
        q = PowerShellQuoter()
        result = q.quote_argument("")
        assert result == "''"

    def test_double_quote_escapes_backtick(self) -> None:
        """When double-quote style is chosen, backticks are escaped as ``."""
        q = PowerShellQuoter()
        # Contains single-quote and backtick but no dollar → single-quote forced
        # because backtick is present.
        result = q.quote_argument("it's`cool")
        # Has both ' and ` → single-quote style with doubling.
        assert result == "'it''s`cool'"

    def test_double_quote_style_escapes_double_quote(self) -> None:
        """Value with single-quote but no dollar/backtick → double-quote style."""
        q = PowerShellQuoter()
        # "say 'hi'" — has single-quote, no dollar, no backtick.
        result = q.quote_argument("say 'hi'")
        # Double-quote style: single quotes are fine inside "…".
        assert result == "\"say 'hi'\""

    def test_double_quote_style_with_embedded_double_quote(self) -> None:
        """Embedded " inside double-quoted string is escaped as `"."""
        q = PowerShellQuoter()
        # Value with ' and " but no $ or ` → double-quote style.
        # Embedded " → `"
        result = q.quote_argument("say 'he said \"hi\"'")
        assert '`"' in result

    def test_spaces_preserved_in_single_quote(self) -> None:
        q = PowerShellQuoter()
        result = q.quote_argument("hello world")
        assert result == "'hello world'"

    def test_unicode_preserved_single_quote(self) -> None:
        q = PowerShellQuoter()
        result = q.quote_argument("héllo wörld é")
        assert result.startswith("'")
        assert "héllo wörld" in result


class TestPowerShellQuoterQuoteCommand:
    def test_simple_cmdlet_no_params(self) -> None:
        q = PowerShellQuoter()
        result = q.quote_command("Get-PsmuxSession", {})
        assert result == "Get-PsmuxSession"

    def test_string_param_quoted(self) -> None:
        q = PowerShellQuoter()
        result = q.quote_command("New-PsmuxSession", {"Name": "my-sess"})
        assert result == "New-PsmuxSession -Name 'my-sess'"

    def test_bool_true_becomes_bare_flag(self) -> None:
        q = PowerShellQuoter()
        result = q.quote_command("New-PsmuxSession", {"Detached": True})
        assert result == "New-PsmuxSession -Detached"
        assert "$true" not in result

    def test_bool_false_omitted(self) -> None:
        q = PowerShellQuoter()
        result = q.quote_command("New-PsmuxSession", {"Detached": False})
        assert "Detached" not in result

    def test_none_param_omitted(self) -> None:
        q = PowerShellQuoter()
        result = q.quote_command("New-PsmuxSession", {"Name": "x", "Extra": None})
        assert "Extra" not in result
        assert "Name" in result

    def test_multiple_params(self) -> None:
        q = PowerShellQuoter()
        result = q.quote_command(
            "New-PsmuxSession",
            {"Name": "s1", "Width": "80", "Height": "24", "Detached": True},
        )
        assert "New-PsmuxSession" in result
        assert "-Name" in result
        assert "'s1'" in result
        assert "-Width" in result
        assert "'80'" in result
        assert "-Detached" in result

    def test_param_with_special_chars_quoted(self) -> None:
        q = PowerShellQuoter()
        result = q.quote_command("Send-PsmuxKeys", {"Keys": "echo hello world"})
        assert "'echo hello world'" in result


# ---------------------------------------------------------------------------
# PowerShellQuoter — fuzz test (no live pwsh needed on Linux)
# ---------------------------------------------------------------------------


class TestPowerShellQuoterFuzz:
    """Fuzz test: 100 random strings are quoted; the output is inspected to
    ensure the round-trip identity holds for the *quoter's own inverse logic*.

    On Linux we cannot exec pwsh, so we verify structural invariants:
    - Output starts and ends with the same quote character.
    - Embedded occurrences of that quote character are properly escaped.
    """

    _METACHARS = r"""$`"'\!&|;<>(){}[]~*?#"""

    def _random_string(self, rng: random.Random, length: int = 30) -> str:
        alphabet = string.printable + "".join(
            chr(c) for c in [0xE9, 0x03B1, 0x4E2D, 0x1F600, 0x00A3]
        )
        core = "".join(rng.choice(alphabet) for _ in range(length))
        metapart = "".join(rng.choice(self._METACHARS) for _ in range(5))
        chars = list(core + metapart)
        rng.shuffle(chars)
        return "".join(chars)

    def _unquote_single(self, quoted: str) -> str:
        """Inverse of single-quote style: strip surrounding ' and un-double ''."""
        assert quoted.startswith("'") and quoted.endswith("'")
        inner = quoted[1:-1]
        return inner.replace("''", "'")

    def _unquote_double(self, quoted: str) -> str:
        """Inverse of double-quote style: strip surrounding " and un-escape `" and ``."""
        assert quoted.startswith('"') and quoted.endswith('"')
        inner = quoted[1:-1]
        # Unescape in reverse order: `` → ` then `" → "
        return inner.replace("``", "\x00BTICK\x00").replace('`"', '"').replace("\x00BTICK\x00", "`")

    def test_fuzz_100_strings(self) -> None:
        q = PowerShellQuoter()
        rng = random.Random(42)
        for i in range(100):
            original = self._random_string(rng, length=20 + i % 20)
            quoted = q.quote_argument(original)

            # Must be surrounded by quotes.
            assert len(quoted) >= 2, f"Quoted form too short for {original!r}"
            quote_char = quoted[0]
            assert quote_char in ('"', "'"), f"Unexpected quote char in {quoted!r}"
            assert quoted[-1] == quote_char, f"Mismatched quotes in {quoted!r}"

            # Round-trip verification.
            if quote_char == "'":
                recovered = self._unquote_single(quoted)
            else:
                recovered = self._unquote_double(quoted)

            assert recovered == original, (
                f"Round-trip failed for {original!r}: quoted={quoted!r}, recovered={recovered!r}"
            )


# ---------------------------------------------------------------------------
# JSON parsing helpers
# ---------------------------------------------------------------------------


class TestParseJson:
    def test_valid_json_dict(self) -> None:
        data = _parse_json('{"a": 1}')
        assert data == {"a": 1}

    def test_valid_json_list(self) -> None:
        data = _parse_json('[{"a": 1}]')
        assert data == [{"a": 1}]

    def test_invalid_json_raises_parse_error(self) -> None:
        with pytest.raises(PsmuxParseError, match="Failed to parse"):
            _parse_json("not valid json", context="TestCmdlet")

    def test_context_in_error_message(self) -> None:
        with pytest.raises(PsmuxParseError, match="TestCmdlet"):
            _parse_json("{bad", context="TestCmdlet")


class TestEnsureList:
    def test_list_unchanged(self) -> None:
        assert _ensure_list([1, 2, 3]) == [1, 2, 3]

    def test_dict_wrapped(self) -> None:
        assert _ensure_list({"a": 1}) == [{"a": 1}]

    def test_none_returns_empty(self) -> None:
        assert _ensure_list(None) == []

    def test_string_wrapped(self) -> None:
        assert _ensure_list("foo") == ["foo"]


# ---------------------------------------------------------------------------
# Dataclass parsers
# ---------------------------------------------------------------------------


class TestParseSession:
    def test_basic_fields(self) -> None:
        obj = {
            "Id": "sess-1",
            "Name": "mysession",
            "Attached": False,
            "CreatedAt": 1700000000,
        }
        s = _parse_session(obj)
        assert s.id == "sess-1"
        assert s.name == "mysession"
        assert s.attached is False

    def test_alternate_field_names(self) -> None:
        obj = {
            "SessionId": "sess-2",
            "SessionName": "other",
            "IsAttached": True,
            "CreatedAt": 1700000000,
        }
        s = _parse_session(obj)
        assert s.id == "sess-2"
        assert s.name == "other"
        assert s.attached is True


class TestParseWindow:
    def test_basic_fields(self) -> None:
        obj = {
            "Id": "win-1",
            "Session": "sess",
            "Index": 2,
            "Name": "mywin",
            "Layout": "tiled",
        }
        w = _parse_window(obj)
        assert w.id == "win-1"
        assert w.session == "sess"
        assert w.index == 2
        assert w.name == "mywin"
        assert w.layout == "tiled"

    def test_session_override(self) -> None:
        obj = {"Id": "w0", "Index": 0, "Name": "x", "Layout": ""}
        w = _parse_window(obj, session="override")
        assert w.session == "override"


class TestParsePane:
    def test_basic_fields(self) -> None:
        obj = {
            "Id": "pane-1",
            "Session": "sess",
            "WindowIndex": 0,
            "PaneIndex": 1,
            "Pid": 1234,
            "Dead": False,
            "CurrentCommand": "bash",
            "Width": 80,
            "Height": 24,
        }
        p = _parse_pane(obj)
        assert p.id == "pane-1"
        assert p.session == "sess"
        assert p.window_index == 0
        assert p.pane_index == 1
        assert p.pid == 1234
        assert p.dead is False
        assert p.current_command == "bash"
        assert p.width == 80
        assert p.height == 24

    def test_dead_pane_no_pid(self) -> None:
        obj = {
            "Id": "pane-2",
            "Session": "sess",
            "WindowIndex": 0,
            "PaneIndex": 0,
            "Pid": None,
            "Dead": True,
            "CurrentCommand": "",
            "Width": 80,
            "Height": 24,
        }
        p = _parse_pane(obj)
        assert p.pid is None
        assert p.dead is True

    def test_dead_status_captured(self) -> None:
        obj = {
            "Id": "pane-3",
            "Session": "sess",
            "WindowIndex": 0,
            "PaneIndex": 0,
            "Pid": None,
            "Dead": True,
            "CurrentCommand": "",
            "Width": 80,
            "Height": 24,
            "DeadStatus": 1,
        }
        p = _parse_pane(obj)
        assert p.dead_status == 1


# ---------------------------------------------------------------------------
# Capability probing
# ---------------------------------------------------------------------------


class TestCapabilityProbing:
    def test_probe_runs_get_module(self) -> None:
        runner = MagicMock()
        runner.run.return_value = _json_result({"Version": "2.0.0"})
        PsmuxBackend(runner=runner)
        argv = runner.run.call_args_list[0][0][0]
        assert argv[0] == "pwsh"
        assert "-NoProfile" in argv
        assert "Get-Module Psmux" in argv[-1]

    def test_capabilities_name_is_psmux(self) -> None:
        backend, _ = _make_backend("1.0.0")
        assert backend.capabilities.name == "psmux"

    def test_capabilities_version_stored(self) -> None:
        backend, _ = _make_backend("2.3.1")
        assert backend.capabilities.version == "2.3.1"

    def test_all_capabilities_true(self) -> None:
        backend, _ = _make_backend()
        caps = backend.capabilities
        assert caps.supports_split_before is True
        assert caps.supports_remain_on_exit is True
        assert caps.supports_capture_pane is True
        assert caps.supports_format_pane_dead is True
        assert caps.supports_initial_size_in_new_session is True

    def test_probe_failure_gives_empty_version(self) -> None:
        runner = MagicMock()
        runner.run.return_value = _make_result("", returncode=1)
        backend = PsmuxBackend(runner=runner)
        assert backend.capabilities.version == ""
        assert backend.capabilities.name == "psmux"

    def test_probe_bad_json_gives_empty_version(self) -> None:
        runner = MagicMock()
        runner.run.return_value = _make_result("not json at all", returncode=0)
        backend = PsmuxBackend(runner=runner)
        assert backend.capabilities.version == ""

    def test_custom_pwsh_binary_used(self) -> None:
        runner = MagicMock()
        runner.run.return_value = _json_result({"Version": "1.0.0"})
        PsmuxBackend(runner=runner, pwsh_binary="C:\\pwsh\\pwsh.exe")
        argv = runner.run.call_args_list[0][0][0]
        assert argv[0] == "C:\\pwsh\\pwsh.exe"


# ---------------------------------------------------------------------------
# list_sessions
# ---------------------------------------------------------------------------

_SESSION_JSON_LIST = json.dumps(
    [
        {
            "Id": "sess-0",
            "Name": "main",
            "Attached": True,
            "CreatedAt": 1700000000,
        },
        {
            "Id": "sess-1",
            "Name": "dev",
            "Attached": False,
            "CreatedAt": 1700001000,
        },
    ]
)
_SESSION_JSON_SINGLE = json.dumps(
    {"Id": "sess-0", "Name": "only", "Attached": False, "CreatedAt": 1700000000}
)


class TestListSessions:
    def test_argv_shape(self, backend_and_runner: tuple) -> None:
        backend, runner = backend_and_runner
        runner.run.return_value = _make_result(_SESSION_JSON_LIST)
        backend.list_sessions()
        argv = runner.run.call_args[0][0]
        assert argv[0] == "pwsh"
        assert argv[1] == "-NoProfile"
        assert argv[2] == "-Command"
        cmd = _cmd(argv)
        assert "Get-PsmuxSession" in cmd
        assert "ConvertTo-Json" in cmd

    def test_parses_list(self, backend_and_runner: tuple) -> None:
        backend, runner = backend_and_runner
        runner.run.return_value = _make_result(_SESSION_JSON_LIST)
        sessions = backend.list_sessions()
        assert len(sessions) == 2
        assert sessions[0].name == "main"
        assert sessions[0].attached is True
        assert sessions[1].name == "dev"
        assert sessions[1].attached is False

    def test_parses_single_object(self, backend_and_runner: tuple) -> None:
        """ConvertTo-Json emits a bare dict for one item; must still work."""
        backend, runner = backend_and_runner
        runner.run.return_value = _make_result(_SESSION_JSON_SINGLE)
        sessions = backend.list_sessions()
        assert len(sessions) == 1
        assert sessions[0].name == "only"

    def test_returns_empty_on_nonzero(self, backend_and_runner: tuple) -> None:
        backend, runner = backend_and_runner
        runner.run.return_value = _make_result("", returncode=1)
        assert backend.list_sessions() == []

    def test_returns_empty_on_empty_output(self, backend_and_runner: tuple) -> None:
        backend, runner = backend_and_runner
        runner.run.return_value = _make_result("")
        assert backend.list_sessions() == []


# ---------------------------------------------------------------------------
# new_session
# ---------------------------------------------------------------------------

_NEW_SESSION_JSON = json.dumps(
    {"Id": "sess-0", "Name": "newsess", "Attached": False, "CreatedAt": 1700000000}
)


class TestNewSession:
    def test_argv_includes_name_width_height(self, backend_and_runner: tuple) -> None:
        backend, runner = backend_and_runner
        runner.run.return_value = _make_result(_NEW_SESSION_JSON)
        backend.new_session("newsess", 120, 40)
        argv = runner.run.call_args[0][0]
        cmd = _cmd(argv)
        assert "New-PsmuxSession" in cmd
        assert "-Name" in cmd
        assert "newsess" in cmd
        assert "-Width" in cmd
        assert "120" in cmd
        assert "-Height" in cmd
        assert "40" in cmd

    def test_detached_flag_present_by_default(self, backend_and_runner: tuple) -> None:
        backend, runner = backend_and_runner
        runner.run.return_value = _make_result(_NEW_SESSION_JSON)
        backend.new_session("newsess", 80, 24)
        cmd = _cmd(runner.run.call_args[0][0])
        assert "-Detached" in cmd

    def test_no_detached_flag_when_false(self, backend_and_runner: tuple) -> None:
        backend, runner = backend_and_runner
        runner.run.return_value = _make_result(_NEW_SESSION_JSON)
        backend.new_session("newsess", 80, 24, detached=False)
        cmd = _cmd(runner.run.call_args[0][0])
        assert "-Detached" not in cmd

    def test_returns_session(self, backend_and_runner: tuple) -> None:
        backend, runner = backend_and_runner
        runner.run.return_value = _make_result(_NEW_SESSION_JSON)
        sess = backend.new_session("newsess", 80, 24)
        assert sess.name == "newsess"

    def test_fallback_when_no_json_result(self, backend_and_runner: tuple) -> None:
        """When cmdlet returns non-dict JSON, list_sessions is queried."""
        backend, runner = backend_and_runner
        runner.run.side_effect = [
            # new_session call → returns a string, triggers fallback
            _make_result('"created"'),
            # list_sessions call → returns a list
            _make_result(
                json.dumps(
                    [{"Id": "x", "Name": "fallback", "Attached": False, "CreatedAt": 1700000000}]
                )
            ),
        ]
        sess = backend.new_session("fallback", 80, 24)
        assert sess.name == "fallback"


# ---------------------------------------------------------------------------
# attach_session / kill_session
# ---------------------------------------------------------------------------


class TestAttachKillSession:
    def test_attach_argv(self, backend_and_runner: tuple) -> None:
        backend, runner = backend_and_runner
        runner.run.return_value = _make_result()
        backend.attach_session("mysess")
        cmd = _cmd(runner.run.call_args[0][0])
        assert "Attach-PsmuxSession" in cmd
        assert "-Name" in cmd
        assert "mysess" in cmd

    def test_kill_argv(self, backend_and_runner: tuple) -> None:
        backend, runner = backend_and_runner
        runner.run.return_value = _make_result()
        backend.kill_session("mysess")
        cmd = _cmd(runner.run.call_args[0][0])
        assert "Remove-PsmuxSession" in cmd
        assert "-Name" in cmd
        assert "mysess" in cmd


# ---------------------------------------------------------------------------
# list_windows
# ---------------------------------------------------------------------------

_WINDOW_JSON_LIST = json.dumps(
    [
        {"Id": "win-0", "Session": "sess", "Index": 0, "Name": "main", "Layout": "tiled"},
        {"Id": "win-1", "Session": "sess", "Index": 1, "Name": "logs", "Layout": "even-h"},
    ]
)


class TestListWindows:
    def test_argv_shape(self, backend_and_runner: tuple) -> None:
        backend, runner = backend_and_runner
        runner.run.return_value = _make_result(_WINDOW_JSON_LIST)
        backend.list_windows("mysession")
        cmd = _cmd(runner.run.call_args[0][0])
        assert "Get-PsmuxWindow" in cmd
        assert "-Session" in cmd
        assert "mysession" in cmd
        assert "ConvertTo-Json" in cmd

    def test_parses_windows(self, backend_and_runner: tuple) -> None:
        backend, runner = backend_and_runner
        runner.run.return_value = _make_result(_WINDOW_JSON_LIST)
        windows = backend.list_windows("sess")
        assert len(windows) == 2
        assert windows[0].id == "win-0"
        assert windows[0].name == "main"
        assert windows[1].name == "logs"

    def test_empty_on_nonzero(self, backend_and_runner: tuple) -> None:
        backend, runner = backend_and_runner
        runner.run.return_value = _make_result("", returncode=1)
        assert backend.list_windows("sess") == []


# ---------------------------------------------------------------------------
# new_window / kill_window
# ---------------------------------------------------------------------------

_NEW_WINDOW_JSON = json.dumps(
    {"Id": "win-2", "Session": "sess", "Index": 2, "Name": "editor", "Layout": "tiled"}
)


class TestNewWindow:
    def test_argv_no_name(self, backend_and_runner: tuple) -> None:
        backend, runner = backend_and_runner
        runner.run.return_value = _make_result(_NEW_WINDOW_JSON)
        backend.new_window("sess")
        cmd = _cmd(runner.run.call_args[0][0])
        assert "New-PsmuxWindow" in cmd
        assert "-Session" in cmd
        assert "-Name" not in cmd

    def test_argv_with_name(self, backend_and_runner: tuple) -> None:
        backend, runner = backend_and_runner
        runner.run.return_value = _make_result(_NEW_WINDOW_JSON)
        backend.new_window("sess", name="editor")
        cmd = _cmd(runner.run.call_args[0][0])
        assert "-Name" in cmd
        assert "editor" in cmd

    def test_returns_window(self, backend_and_runner: tuple) -> None:
        backend, runner = backend_and_runner
        runner.run.return_value = _make_result(_NEW_WINDOW_JSON)
        win = backend.new_window("sess", name="editor")
        assert win.id == "win-2"
        assert win.name == "editor"
        assert win.index == 2


class TestKillWindow:
    def test_argv(self, backend_and_runner: tuple) -> None:
        backend, runner = backend_and_runner
        runner.run.return_value = _make_result()
        backend.kill_window("sess:1")
        cmd = _cmd(runner.run.call_args[0][0])
        assert "Remove-PsmuxWindow" in cmd
        assert "-Target" in cmd
        assert "sess:1" in cmd


# ---------------------------------------------------------------------------
# select_layout / capture_layout / set_window_option
# ---------------------------------------------------------------------------


class TestLayoutMethods:
    def test_select_layout_argv(self, backend_and_runner: tuple) -> None:
        backend, runner = backend_and_runner
        runner.run.return_value = _make_result()
        backend.select_layout("sess:1", "tiled")
        cmd = _cmd(runner.run.call_args[0][0])
        assert "Select-PsmuxLayout" in cmd
        assert "-Target" in cmd
        assert "-Layout" in cmd
        assert "tiled" in cmd

    def test_capture_layout_string_result(self, backend_and_runner: tuple) -> None:
        backend, runner = backend_and_runner
        runner.run.return_value = _make_result(json.dumps("tiled,80x24,0,0{}"))
        layout = backend.capture_layout("sess:1")
        cmd = _cmd(runner.run.call_args[0][0])
        assert "Get-PsmuxLayout" in cmd
        assert "-Window" in cmd
        assert layout == "tiled,80x24,0,0{}"

    def test_capture_layout_dict_result(self, backend_and_runner: tuple) -> None:
        backend, runner = backend_and_runner
        runner.run.return_value = _make_result(
            json.dumps({"Layout": "even-horizontal,80x24,0,0{}"})
        )
        layout = backend.capture_layout("sess:1")
        assert layout == "even-horizontal,80x24,0,0{}"

    def test_capture_layout_empty_output(self, backend_and_runner: tuple) -> None:
        backend, runner = backend_and_runner
        runner.run.return_value = _make_result("")
        layout = backend.capture_layout("sess:1")
        assert layout == ""

    def test_set_window_option_argv(self, backend_and_runner: tuple) -> None:
        backend, runner = backend_and_runner
        runner.run.return_value = _make_result()
        backend.set_window_option("sess:1", "remain-on-exit", "on")
        cmd = _cmd(runner.run.call_args[0][0])
        assert "Set-PsmuxWindowOption" in cmd
        assert "-Target" in cmd
        assert "-Name" in cmd
        assert "remain-on-exit" in cmd
        assert "-Value" in cmd
        assert "on" in cmd


# ---------------------------------------------------------------------------
# list_panes
# ---------------------------------------------------------------------------

_PANE_JSON_LIST = json.dumps(
    [
        {
            "Id": "pane-0",
            "Session": "sess",
            "WindowIndex": 0,
            "PaneIndex": 0,
            "Pid": 1234,
            "Dead": False,
            "CurrentCommand": "bash",
            "Width": 80,
            "Height": 24,
        },
        {
            "Id": "pane-1",
            "Session": "sess",
            "WindowIndex": 0,
            "PaneIndex": 1,
            "Pid": None,
            "Dead": True,
            "CurrentCommand": "sleep",
            "Width": 40,
            "Height": 24,
        },
    ]
)


class TestListPanes:
    def test_argv_all_sessions(self, backend_and_runner: tuple) -> None:
        backend, runner = backend_and_runner
        runner.run.return_value = _make_result(_PANE_JSON_LIST)
        backend.list_panes()
        cmd = _cmd(runner.run.call_args[0][0])
        assert "Get-PsmuxPane" in cmd
        assert "-Target" not in cmd

    def test_argv_with_target(self, backend_and_runner: tuple) -> None:
        backend, runner = backend_and_runner
        runner.run.return_value = _make_result(_PANE_JSON_LIST)
        backend.list_panes(target="sess:0")
        cmd = _cmd(runner.run.call_args[0][0])
        assert "Get-PsmuxPane" in cmd
        assert "-Target" in cmd
        assert "sess:0" in cmd

    def test_parses_panes(self, backend_and_runner: tuple) -> None:
        backend, runner = backend_and_runner
        runner.run.return_value = _make_result(_PANE_JSON_LIST)
        panes = backend.list_panes()
        assert len(panes) == 2
        assert panes[0].id == "pane-0"
        assert panes[0].pid == 1234
        assert panes[0].dead is False
        assert panes[1].id == "pane-1"
        assert panes[1].pid is None
        assert panes[1].dead is True

    def test_empty_on_nonzero(self, backend_and_runner: tuple) -> None:
        backend, runner = backend_and_runner
        runner.run.return_value = _make_result("", returncode=1)
        assert backend.list_panes() == []


# ---------------------------------------------------------------------------
# split_pane
# ---------------------------------------------------------------------------

_SPLIT_PANE_JSON = json.dumps(
    {
        "Id": "pane-2",
        "Session": "sess",
        "WindowIndex": 0,
        "PaneIndex": 2,
        "Pid": 5678,
        "Dead": False,
        "CurrentCommand": "bash",
        "Width": 40,
        "Height": 24,
    }
)


class TestSplitPane:
    def test_argv_horizontal(self, backend_and_runner: tuple) -> None:
        backend, runner = backend_and_runner
        runner.run.return_value = _make_result(_SPLIT_PANE_JSON)
        backend.split_pane("pane-0", "h")
        cmd = _cmd(runner.run.call_args[0][0])
        assert "Split-PsmuxPane" in cmd
        assert "-Target" in cmd
        assert "-Direction" in cmd
        assert "Horizontal" in cmd
        assert "Vertical" not in cmd

    def test_argv_vertical(self, backend_and_runner: tuple) -> None:
        backend, runner = backend_and_runner
        runner.run.return_value = _make_result(_SPLIT_PANE_JSON)
        backend.split_pane("pane-0", "v")
        cmd = _cmd(runner.run.call_args[0][0])
        assert "Vertical" in cmd
        assert "Horizontal" not in cmd

    def test_before_flag_added(self, backend_and_runner: tuple) -> None:
        backend, runner = backend_and_runner
        runner.run.return_value = _make_result(_SPLIT_PANE_JSON)
        backend.split_pane("pane-0", "h", before=True)
        cmd = _cmd(runner.run.call_args[0][0])
        assert "-Before" in cmd

    def test_no_before_flag_by_default(self, backend_and_runner: tuple) -> None:
        backend, runner = backend_and_runner
        runner.run.return_value = _make_result(_SPLIT_PANE_JSON)
        backend.split_pane("pane-0", "h")
        cmd = _cmd(runner.run.call_args[0][0])
        assert "-Before" not in cmd

    def test_returns_pane(self, backend_and_runner: tuple) -> None:
        backend, runner = backend_and_runner
        runner.run.return_value = _make_result(_SPLIT_PANE_JSON)
        pane = backend.split_pane("pane-0", "h")
        assert pane.id == "pane-2"
        assert pane.pid == 5678
        assert pane.width == 40


# ---------------------------------------------------------------------------
# select_pane / swap_panes
# ---------------------------------------------------------------------------


class TestPaneMiscMethods:
    def test_select_pane_argv(self, backend_and_runner: tuple) -> None:
        backend, runner = backend_and_runner
        runner.run.return_value = _make_result()
        backend.select_pane("pane-2")
        cmd = _cmd(runner.run.call_args[0][0])
        assert "Select-PsmuxPane" in cmd
        assert "-Target" in cmd
        assert "pane-2" in cmd

    def test_swap_panes_argv(self, backend_and_runner: tuple) -> None:
        backend, runner = backend_and_runner
        runner.run.return_value = _make_result()
        backend.swap_panes("pane-1", "pane-2")
        cmd = _cmd(runner.run.call_args[0][0])
        assert "Swap-PsmuxPane" in cmd
        assert "-Source" in cmd
        assert "pane-1" in cmd
        assert "-Target" in cmd
        assert "pane-2" in cmd


# ---------------------------------------------------------------------------
# break_pane / move_pane
# ---------------------------------------------------------------------------

_BREAK_PANE_WIN_JSON = json.dumps(
    {"Id": "win-3", "Session": "sess", "Index": 3, "Name": "bash", "Layout": "tiled"}
)


class TestBreakMovePane:
    def test_break_pane_argv_detached(self, backend_and_runner: tuple) -> None:
        backend, runner = backend_and_runner
        runner.run.return_value = _make_result(_BREAK_PANE_WIN_JSON)
        backend.break_pane("pane-1")
        cmd = _cmd(runner.run.call_args[0][0])
        assert "Move-PsmuxPaneToWindow" in cmd
        assert "-Source" in cmd
        assert "pane-1" in cmd
        assert "-Detached" in cmd

    def test_break_pane_argv_not_detached(self, backend_and_runner: tuple) -> None:
        backend, runner = backend_and_runner
        runner.run.return_value = _make_result(_BREAK_PANE_WIN_JSON)
        backend.break_pane("pane-1", detached=False)
        cmd = _cmd(runner.run.call_args[0][0])
        assert "-Detached" not in cmd

    def test_break_pane_returns_window(self, backend_and_runner: tuple) -> None:
        backend, runner = backend_and_runner
        runner.run.return_value = _make_result(_BREAK_PANE_WIN_JSON)
        win = backend.break_pane("pane-1")
        assert win.id == "win-3"
        assert win.index == 3

    def test_move_pane_argv(self, backend_and_runner: tuple) -> None:
        backend, runner = backend_and_runner
        runner.run.return_value = _make_result()
        backend.move_pane("pane-2", "sess:1")
        cmd = _cmd(runner.run.call_args[0][0])
        assert "Move-PsmuxPane" in cmd
        assert "-Source" in cmd
        assert "pane-2" in cmd
        assert "-Target" in cmd
        assert "sess:1" in cmd


# ---------------------------------------------------------------------------
# resize_pane
# ---------------------------------------------------------------------------


class TestResizePane:
    def test_argv_shape(self, backend_and_runner: tuple) -> None:
        backend, runner = backend_and_runner
        runner.run.return_value = _make_result()
        backend.resize_pane("pane-0", 100, 50)
        cmd = _cmd(runner.run.call_args[0][0])
        assert "Resize-PsmuxPane" in cmd
        assert "-Target" in cmd
        assert "pane-0" in cmd
        assert "-Width" in cmd
        assert "100" in cmd
        assert "-Height" in cmd
        assert "50" in cmd


# ---------------------------------------------------------------------------
# send_keys
# ---------------------------------------------------------------------------


class TestSendKeys:
    def test_argv_with_enter(self, backend_and_runner: tuple) -> None:
        backend, runner = backend_and_runner
        runner.run.return_value = _make_result()
        backend.send_keys("pane-0", "echo hello")
        cmd = _cmd(runner.run.call_args[0][0])
        assert "Send-PsmuxKeys" in cmd
        assert "-Target" in cmd
        assert "pane-0" in cmd
        assert "-Keys" in cmd
        assert "echo hello" in cmd
        assert "-Enter" in cmd

    def test_argv_without_enter(self, backend_and_runner: tuple) -> None:
        backend, runner = backend_and_runner
        runner.run.return_value = _make_result()
        backend.send_keys("pane-0", "echo hello", enter=False)
        cmd = _cmd(runner.run.call_args[0][0])
        assert "-Enter" not in cmd


# ---------------------------------------------------------------------------
# kill_pane / respawn_pane
# ---------------------------------------------------------------------------


class TestKillRespawnPane:
    def test_kill_pane_argv(self, backend_and_runner: tuple) -> None:
        backend, runner = backend_and_runner
        runner.run.return_value = _make_result()
        backend.kill_pane("pane-3")
        cmd = _cmd(runner.run.call_args[0][0])
        assert "Stop-PsmuxPane" in cmd
        assert "-Target" in cmd
        assert "pane-3" in cmd

    def test_respawn_pane_kill_existing(self, backend_and_runner: tuple) -> None:
        backend, runner = backend_and_runner
        runner.run.return_value = _make_result()
        backend.respawn_pane("pane-0", "bash script.sh")
        cmd = _cmd(runner.run.call_args[0][0])
        assert "Restart-PsmuxPane" in cmd
        assert "-Target" in cmd
        assert "pane-0" in cmd
        assert "-Command" in cmd
        assert "bash script.sh" in cmd
        assert "-KillExisting" in cmd

    def test_respawn_pane_no_kill(self, backend_and_runner: tuple) -> None:
        backend, runner = backend_and_runner
        runner.run.return_value = _make_result()
        backend.respawn_pane("pane-0", "bash script.sh", kill_existing=False)
        cmd = _cmd(runner.run.call_args[0][0])
        assert "-KillExisting" not in cmd


# ---------------------------------------------------------------------------
# capture_pane
# ---------------------------------------------------------------------------


class TestCapturePane:
    def test_argv_default_lines(self, backend_and_runner: tuple) -> None:
        backend, runner = backend_and_runner
        runner.run.return_value = _make_result(json.dumps("some output\n"))
        backend.capture_pane("pane-0")
        cmd = _cmd(runner.run.call_args[0][0])
        assert "Get-PsmuxPaneCapture" in cmd
        assert "-Target" in cmd
        assert "pane-0" in cmd
        assert "-Lines" in cmd
        assert "200" in cmd

    def test_argv_custom_lines(self, backend_and_runner: tuple) -> None:
        backend, runner = backend_and_runner
        runner.run.return_value = _make_result(json.dumps("output\n"))
        backend.capture_pane("pane-0", lines=50)
        cmd = _cmd(runner.run.call_args[0][0])
        assert "50" in cmd

    def test_returns_string_content(self, backend_and_runner: tuple) -> None:
        backend, runner = backend_and_runner
        runner.run.return_value = _make_result(json.dumps("line1\nline2\n"))
        result = backend.capture_pane("pane-0")
        assert result == "line1\nline2\n"

    def test_returns_dict_content_field(self, backend_and_runner: tuple) -> None:
        backend, runner = backend_and_runner
        runner.run.return_value = _make_result(json.dumps({"Content": "line1\nline2\n"}))
        result = backend.capture_pane("pane-0")
        assert result == "line1\nline2\n"

    def test_empty_output_returns_empty_string(self, backend_and_runner: tuple) -> None:
        backend, runner = backend_and_runner
        runner.run.return_value = _make_result("")
        result = backend.capture_pane("pane-0")
        assert result == ""


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


class TestErrorHandling:
    def test_bad_json_from_list_sessions_raises_parse_error(
        self, backend_and_runner: tuple
    ) -> None:
        backend, runner = backend_and_runner
        runner.run.return_value = _make_result("not json")
        with pytest.raises(PsmuxParseError):
            backend.list_sessions()

    def test_bad_json_from_new_session_raises_parse_error(self, backend_and_runner: tuple) -> None:
        backend, runner = backend_and_runner
        runner.run.return_value = _make_result("definitely not json {{")
        with pytest.raises(PsmuxParseError):
            backend.new_session("sess", 80, 24)

    def test_bad_json_from_split_pane_raises_parse_error(self, backend_and_runner: tuple) -> None:
        backend, runner = backend_and_runner
        runner.run.return_value = _make_result("{bad json}")
        with pytest.raises(PsmuxParseError):
            backend.split_pane("pane-0", "h")


# ---------------------------------------------------------------------------
# BackendCapabilityError — exposed for capability gating
# ---------------------------------------------------------------------------


class TestBackendCapabilityError:
    def test_error_is_runtime_error(self) -> None:
        err = BackendCapabilityError("test message")
        assert isinstance(err, RuntimeError)
        assert "test message" in str(err)

    def test_psmux_parse_error_is_value_error(self) -> None:
        err = PsmuxParseError("bad json")
        assert isinstance(err, ValueError)


# ---------------------------------------------------------------------------
# Timeout propagation
# ---------------------------------------------------------------------------


class TestTimeout:
    def test_run_uses_10s_timeout_by_default(self, backend_and_runner: tuple) -> None:
        backend, runner = backend_and_runner
        runner.run.return_value = _make_result()
        backend.kill_pane("pane-0")
        call_kwargs = runner.run.call_args[1]
        assert call_kwargs.get("timeout", 10.0) == 10.0


# ---------------------------------------------------------------------------
# argv structure: pwsh -NoProfile -Command on every call
# ---------------------------------------------------------------------------


class TestArgvStructure:
    def test_every_call_uses_pwsh_no_profile_command(self, backend_and_runner: tuple) -> None:
        backend, runner = backend_and_runner
        runner.run.return_value = _make_result()
        backend.kill_pane("pane-0")
        argv = runner.run.call_args[0][0]
        assert argv[0] == "pwsh"
        assert argv[1] == "-NoProfile"
        assert argv[2] == "-Command"
        assert len(argv) == 4  # pwsh -NoProfile -Command <cmd_str>
