# -*- coding: utf-8 -*-
"""
Tests for ProcessRunner.

Spec: §8, §9.3
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from cpsm.platform.process_runner import ProcessRunner


@pytest.fixture()
def runner() -> ProcessRunner:
    return ProcessRunner()


# ---------------------------------------------------------------------------
# Basic execution
# ---------------------------------------------------------------------------


def test_run_returns_completed_process(runner: ProcessRunner) -> None:
    result = runner.run([sys.executable, "-c", "print('hello')"])
    assert result.returncode == 0
    assert result.stdout.strip() == "hello"


def test_run_captures_stderr(runner: ProcessRunner) -> None:
    result = runner.run([sys.executable, "-c", "import sys; sys.stderr.write('err\\n')"])
    assert "err" in result.stderr


def test_run_check_true_raises_on_nonzero(runner: ProcessRunner) -> None:
    with pytest.raises(subprocess.CalledProcessError) as exc_info:
        runner.run([sys.executable, "-c", "import sys; sys.exit(42)"])
    assert exc_info.value.returncode == 42


def test_run_check_false_no_raise_on_nonzero(runner: ProcessRunner) -> None:
    result = runner.run(
        [sys.executable, "-c", "import sys; sys.exit(1)"],
        check=False,
    )
    assert result.returncode == 1


# ---------------------------------------------------------------------------
# UTF-8 decoding
# ---------------------------------------------------------------------------


def test_run_utf8_output(runner: ProcessRunner) -> None:
    """Subprocess output containing non-ASCII characters is decoded as UTF-8."""
    result = runner.run(
        [sys.executable, "-c", "print('café ✓')"],
    )
    assert "café" in result.stdout
    assert "✓" in result.stdout


def test_run_invalid_utf8_replaced_not_raised(runner: ProcessRunner) -> None:
    """Non-UTF-8 bytes in stdout are replaced (errors='replace'), not raised."""
    # Write raw invalid UTF-8 bytes to stdout
    code = "import sys; sys.stdout.buffer.write(b'\\xff\\xfe invalid'); sys.stdout.flush()"
    result = runner.run([sys.executable, "-c", code], check=False)
    # The replacement character (U+FFFD) or '?' should appear, not an exception
    assert result.returncode == 0
    assert result.stdout  # something was captured


# ---------------------------------------------------------------------------
# Timeout
# ---------------------------------------------------------------------------


def test_run_timeout_raises(runner: ProcessRunner) -> None:
    with pytest.raises(subprocess.TimeoutExpired):
        runner.run(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            timeout=0.1,
        )


# ---------------------------------------------------------------------------
# Environment passthrough
# ---------------------------------------------------------------------------


def test_run_env_passthrough(runner: ProcessRunner) -> None:
    import os

    env = {**os.environ, "CPSM_TEST_VAR": "hello_from_env"}
    result = runner.run(
        [sys.executable, "-c", "import os; print(os.environ.get('CPSM_TEST_VAR', ''))"],
        env=env,
    )
    assert result.stdout.strip() == "hello_from_env"


def test_run_env_none_inherits_parent(runner: ProcessRunner) -> None:
    import os

    os.environ["CPSM_INHERIT_TEST"] = "inherited"
    try:
        result = runner.run(
            [sys.executable, "-c", "import os; print(os.environ.get('CPSM_INHERIT_TEST', ''))"],
            env=None,
        )
        assert result.stdout.strip() == "inherited"
    finally:
        del os.environ["CPSM_INHERIT_TEST"]


# ---------------------------------------------------------------------------
# CWD
# ---------------------------------------------------------------------------


def test_run_cwd(runner: ProcessRunner, tmp_path: pytest.fixture) -> None:  # type: ignore[valid-type]
    result = runner.run(
        [sys.executable, "-c", "import os; print(os.getcwd())"],
        cwd=tmp_path,  # type: ignore[arg-type]
    )
    # Resolve both paths to handle symlinks (e.g. /tmp → /private/tmp on macOS)
    from pathlib import Path

    assert Path(result.stdout.strip()).resolve() == tmp_path.resolve()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------


def test_run_stdin_input(runner: ProcessRunner) -> None:
    result = runner.run(
        [sys.executable, "-c", "import sys; print(sys.stdin.read().strip())"],
        input="test_input",
    )
    assert result.stdout.strip() == "test_input"


# ---------------------------------------------------------------------------
# quote_argv
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv,expected_posix,expected_windows",
    [
        (["echo", "hello"], "echo hello", "echo hello"),
        (["echo", "hello world"], "echo 'hello world'", 'echo "hello world"'),
        (["ssh", "-p", "22", "user@host"], "ssh -p 22 user@host", "ssh -p 22 user@host"),
        (
            ["/usr/bin/cmd", "arg with spaces", "safe"],
            "/usr/bin/cmd 'arg with spaces' safe",
            '/usr/bin/cmd "arg with spaces" safe',
        ),
    ],
)
def test_quote_argv_posix(
    runner: ProcessRunner,
    argv: list[str],
    expected_posix: str,
    expected_windows: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On POSIX, quote_argv should use shlex.quote per argument."""
    monkeypatch.setattr(sys, "platform", "linux")
    result = runner.quote_argv(argv)
    assert result == expected_posix


@pytest.mark.parametrize(
    "argv,expected_posix,expected_windows",
    [
        (["echo", "hello"], "echo hello", "echo hello"),
        (["echo", "hello world"], "echo 'hello world'", 'echo "hello world"'),
    ],
)
def test_quote_argv_windows(
    runner: ProcessRunner,
    argv: list[str],
    expected_posix: str,
    expected_windows: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On Windows, quote_argv should use subprocess.list2cmdline."""
    monkeypatch.setattr(sys, "platform", "win32")
    result = runner.quote_argv(argv)
    assert result == expected_windows


def test_quote_argv_special_chars_posix(
    runner: ProcessRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    result = runner.quote_argv(["cmd", "arg;rm -rf /"])
    # shlex.quote wraps in single quotes
    assert "'" in result
    assert "rm -rf /" not in result.split("'")[0]


# ---------------------------------------------------------------------------
# Windows-specific: CREATE_NO_WINDOW
# ---------------------------------------------------------------------------


def test_run_create_no_window_flag_on_windows(
    runner: ProcessRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On win32, CREATE_NO_WINDOW should be added to creationflags."""
    captured: dict[str, object] = {}

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured.update(kwargs)
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(sys, "platform", "win32")

    runner.run(["echo", "test"])
    assert "creationflags" in captured
    # 0x08000000 is the Windows CREATE_NO_WINDOW value; getattr handles absence on Linux
    expected_flag = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    assert captured["creationflags"] == expected_flag


def test_run_no_create_no_window_on_linux(
    runner: ProcessRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On linux, CREATE_NO_WINDOW should NOT appear in subprocess.run kwargs."""
    captured: dict[str, object] = {}

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured.update(kwargs)
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(sys, "platform", "linux")

    runner.run(["echo", "test"])
    assert "creationflags" not in captured
