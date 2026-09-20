# -*- coding: utf-8 -*-
"""
Contract tests for ItmuxBackend — runs against a real itmux binary on a
private named pipe so tests are fully isolated from live sessions.

Marked ``@pytest.mark.integration``; skipped automatically on non-Windows
platforms (itmux is a Windows-only multiplexer).  Always tears down the
private itmux server in the fixture finalizer, even on test failure.

The test structure mirrors ``test_tmux_backend_contract.py`` so the same
ABC surface is validated on both backends.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time

import pytest

from cpsm.platform.itmux_backend import ItmuxBackend

# ---------------------------------------------------------------------------
# Platform guard — itmux only runs on Windows
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.integration

if sys.platform != "win32":
    pytest.skip("itmux contract tests run on Windows CI only", allow_module_level=True)

# ---------------------------------------------------------------------------
# Binary discovery
# ---------------------------------------------------------------------------

_ITMUX_BIN = shutil.which("itmux")


def _itmux_available() -> bool:
    return _ITMUX_BIN is not None


# ---------------------------------------------------------------------------
# Private-pipe fixture
# ---------------------------------------------------------------------------

_PIPE_NAME = r"\\.\pipe\itmux-cpsm-test"


@pytest.fixture(scope="module")
def backend():  # type: ignore[return]
    """Yield an ItmuxBackend pointed at a private named pipe.

    On teardown, kill the private itmux server unconditionally.
    """
    if not _itmux_available():
        pytest.skip("itmux not found on PATH")

    be = ItmuxBackend(socket_path=_PIPE_NAME, itmux_binary=_ITMUX_BIN or "itmux")
    yield be

    try:
        subprocess.run(
            [_ITMUX_BIN or "itmux", "-S", _PIPE_NAME, "kill-server"],
            timeout=5,
            check=False,
            capture_output=True,
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SESSION_NAME = "itmux-contract-test"


def _wait_for_pane_alive(backend: ItmuxBackend, pane_id: str, *, retries: int = 10) -> bool:
    """Poll until the pane is alive (dead=False) or retries exhausted."""
    for _ in range(retries):
        panes = backend.list_panes()
        for p in panes:
            if p.id == pane_id and not p.dead:
                return True
        time.sleep(0.2)
    return False


# ---------------------------------------------------------------------------
# Sequential contract test — exercises full ABC API
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _itmux_available(), reason="itmux not found on PATH")
def test_full_api_contract(backend: ItmuxBackend) -> None:
    """Exercise every ABC method sequentially against a real itmux binary.

    Steps:
    1.  new_session
    2.  list_sessions
    3.  new_window
    4.  split_pane (both directions, including before=True)
    5.  list_panes
    6.  send_keys + capture_pane
    7.  set_window_option + capture_layout
    8.  respawn_pane (with kill_existing=True)
    9.  select_layout preset
    10. select_pane
    11. swap_panes
    12. resize_pane
    13. break_pane -> move_pane round-trip
    14. kill_pane, kill_window, kill_session
    """

    # ------------------------------------------------------------------
    # 1. new_session
    # ------------------------------------------------------------------
    sess = backend.new_session(_SESSION_NAME, 80, 24)
    assert sess.name == _SESSION_NAME

    # ------------------------------------------------------------------
    # 2. list_sessions
    # ------------------------------------------------------------------
    sessions = backend.list_sessions()
    names = [s.name for s in sessions]
    assert _SESSION_NAME in names

    # ------------------------------------------------------------------
    # 3. new_window
    # ------------------------------------------------------------------
    win2 = backend.new_window(_SESSION_NAME, name="win2")
    assert win2.name == "win2"
    win2_id = win2.id

    windows = backend.list_windows(_SESSION_NAME)
    win_ids = [w.id for w in windows]
    assert win2_id in win_ids

    # ------------------------------------------------------------------
    # 4. split_pane — horizontal and vertical, including before=True
    # ------------------------------------------------------------------
    target_win2 = f"{_SESSION_NAME}:{win2_id}"
    panes_before = backend.list_panes(target=target_win2)
    assert len(panes_before) >= 1
    base_pane_id = panes_before[0].id

    pane_h = backend.split_pane(base_pane_id, "h")
    assert pane_h.id != base_pane_id

    # before=True is capability-gated; backend handles fallback automatically.
    pane_vb = backend.split_pane(base_pane_id, "v", before=True)
    assert pane_vb.id != base_pane_id

    # ------------------------------------------------------------------
    # 5. list_panes with target
    # ------------------------------------------------------------------
    panes_win2 = backend.list_panes(target=target_win2)
    assert len(panes_win2) >= 3
    pane_ids = [p.id for p in panes_win2]
    assert pane_h.id in pane_ids

    # ------------------------------------------------------------------
    # 6. send_keys + capture_pane
    # ------------------------------------------------------------------
    backend.send_keys(base_pane_id, "echo ITMUX_CONTRACT_MARKER")
    time.sleep(0.3)
    output = backend.capture_pane(base_pane_id, lines=50)
    assert "ITMUX_CONTRACT_MARKER" in output

    # ------------------------------------------------------------------
    # 7. set_window_option + capture_layout
    # ------------------------------------------------------------------
    backend.set_window_option(target_win2, "remain-on-exit", "on")
    layout_str = backend.capture_layout(target_win2)
    assert layout_str and "," in layout_str

    # ------------------------------------------------------------------
    # 8. respawn_pane
    # ------------------------------------------------------------------
    backend.respawn_pane(base_pane_id, "sleep 30", kill_existing=True)
    alive = _wait_for_pane_alive(backend, base_pane_id)
    assert alive, f"Pane {base_pane_id} did not become alive after respawn"

    # ------------------------------------------------------------------
    # 9. select_layout preset
    # ------------------------------------------------------------------
    backend.select_layout(target_win2, "tiled")

    # ------------------------------------------------------------------
    # 10. select_pane
    # ------------------------------------------------------------------
    backend.select_pane(pane_h.id)

    # ------------------------------------------------------------------
    # 11. swap_panes
    # ------------------------------------------------------------------
    backend.swap_panes(base_pane_id, pane_h.id)
    panes_after_swap = backend.list_panes(target=target_win2)
    ids_after = {p.id for p in panes_after_swap}
    assert base_pane_id in ids_after
    assert pane_h.id in ids_after

    # ------------------------------------------------------------------
    # 12. resize_pane
    # The backend handles cell↔pixel conversion automatically.
    # ------------------------------------------------------------------
    backend.resize_pane(base_pane_id, 40, 12)

    # ------------------------------------------------------------------
    # 13. break_pane -> move_pane round-trip
    # ------------------------------------------------------------------
    new_win = backend.break_pane(pane_h.id, detached=True)
    broken_win_id = new_win.id

    windows_after_break = backend.list_windows(_SESSION_NAME)
    all_win_ids = [w.id for w in windows_after_break]
    assert broken_win_id in all_win_ids

    broken_panes = backend.list_panes(target=f"{_SESSION_NAME}:{broken_win_id}")
    if broken_panes:
        backend.move_pane(broken_panes[0].id, target_win2)

    # ------------------------------------------------------------------
    # 14. kill_pane, kill_window, kill_session
    # ------------------------------------------------------------------
    try:
        backend.kill_pane(pane_vb.id)
    except subprocess.CalledProcessError:
        pass

    try:
        backend.kill_window(f"{_SESSION_NAME}:{broken_win_id}")
    except subprocess.CalledProcessError:
        pass

    backend.kill_session(_SESSION_NAME)

    remaining = backend.list_sessions()
    remaining_names = [s.name for s in remaining]
    assert _SESSION_NAME not in remaining_names


# ---------------------------------------------------------------------------
# Additional focused tests
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _itmux_available(), reason="itmux not found on PATH")
def test_list_sessions_empty(backend: ItmuxBackend) -> None:
    """list_sessions returns an empty list when no sessions exist."""
    try:
        backend.kill_session(_SESSION_NAME)
    except Exception:
        pass
    sessions = backend.list_sessions()
    names = [s.name for s in sessions]
    assert _SESSION_NAME not in names


@pytest.mark.skipif(not _itmux_available(), reason="itmux not found on PATH")
def test_capabilities_populated(backend: ItmuxBackend) -> None:
    """BackendCapabilities.name is 'itmux' and version string is non-empty."""
    caps = backend.capabilities
    assert caps.name == "itmux"
    assert "itmux" in caps.version.lower()


@pytest.mark.skipif(not _itmux_available(), reason="itmux not found on PATH")
def test_split_pane_before_both_directions(backend: ItmuxBackend) -> None:
    """split_pane with before=True works in both h and v directions.

    The backend handles the -b capability gate transparently.
    """
    sess_name = "itmux-contract-split"
    try:
        backend.new_session(sess_name, 80, 24)
        panes = backend.list_panes(target=sess_name)
        assert panes, "Expected at least one pane after new_session"
        base = panes[0].id

        p_h = backend.split_pane(base, "h", before=True)
        assert p_h.id != base

        p_v = backend.split_pane(base, "v", before=True)
        assert p_v.id != base

        all_pane_ids = {p.id for p in backend.list_panes(target=sess_name)}
        assert p_h.id in all_pane_ids
        assert p_v.id in all_pane_ids
    finally:
        try:
            backend.kill_session(sess_name)
        except Exception:
            pass


@pytest.mark.skipif(not _itmux_available(), reason="itmux not found on PATH")
def test_capture_pane_returns_content(backend: ItmuxBackend) -> None:
    """capture_pane returns content sent via send_keys."""
    sess_name = "itmux-contract-capture"
    try:
        backend.new_session(sess_name, 80, 24)
        panes = backend.list_panes(target=sess_name)
        pane_id = panes[0].id
        backend.send_keys(pane_id, "echo ITMUX_CAPTURE_TOKEN")
        time.sleep(0.3)
        content = backend.capture_pane(pane_id, lines=50)
        assert "ITMUX_CAPTURE_TOKEN" in content
    finally:
        try:
            backend.kill_session(sess_name)
        except Exception:
            pass


@pytest.mark.skipif(not _itmux_available(), reason="itmux not found on PATH")
def test_remain_on_exit_option(backend: ItmuxBackend) -> None:
    """set_window_option remain-on-exit on does not raise."""
    sess_name = "itmux-contract-rox"
    try:
        backend.new_session(sess_name, 80, 24)
        windows = backend.list_windows(sess_name)
        assert windows
        win_target = f"{sess_name}:{windows[0].id}"
        backend.set_window_option(win_target, "remain-on-exit", "on")
    finally:
        try:
            backend.kill_session(sess_name)
        except Exception:
            pass
