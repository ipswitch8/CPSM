# -*- coding: utf-8 -*-
"""Phase 1 smoke tests: package imports, version string, --version exit."""

from __future__ import annotations

import subprocess
import sys

import pytest

import cpsm
from cpsm.__main__ import main


def test_package_imports() -> None:
    assert hasattr(cpsm, "__version__")
    assert isinstance(cpsm.__version__, str)
    assert cpsm.__version__


def test_version_flag_exits_zero(capsys) -> None:
    # argparse --version raises SystemExit(0)
    with pytest.raises(SystemExit) as exc_info:
        main(["--version"])
    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    assert cpsm.__version__ in out


def test_help_flag_exits_zero(capsys) -> None:
    # argparse --help raises SystemExit(0)
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])
    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    assert "cpsm" in out.lower()


def test_no_args_dispatches_to_gui(monkeypatch) -> None:
    """Bare ``cpsm`` (no subcommand) defaults to launching the GUI now —
    the legacy "print help and exit 2" behavior was reverted at the
    user's request. We mock run_gui so the test doesn't actually start
    a Qt event loop."""
    calls: dict[str, object] = {}

    def fake_run_gui(*args, **kwargs):
        calls["called"] = True
        calls["config_path"] = kwargs.get("config_path")
        return 0

    monkeypatch.setattr("cpsm.app.run_gui", fake_run_gui)
    rc = main([])
    assert rc == 0
    assert calls.get("called") is True
    assert calls.get("config_path") is None


def test_python_m_cpsm_version_subprocess() -> None:
    """Direct subprocess test of `python -m cpsm --version`."""
    result = subprocess.run(
        [sys.executable, "-m", "cpsm", "--version"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
    )
    assert result.returncode == 0
    assert cpsm.__version__ in result.stdout
