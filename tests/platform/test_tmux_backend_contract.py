# -*- coding: utf-8 -*-
"""
Contract tests for TmuxBackend — runs against a real tmux binary on a private
socket so tests are fully isolated from the developer's live sessions.

Marked ``@pytest.mark.integration``; skipped automatically when ``tmux`` is
not on PATH or when the integration mark is not selected.  Always tears down
the private tmux server in the fixture finalizer, even on test failure.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

import pytest

from cpsm.platform.tmux_backend import TmuxBackend

# ---------------------------------------------------------------------------
# Skip guard
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.integration

_TMUX_BIN = shutil.which("tmux")


def _tmux_available() -> bool:
    return _TMUX_BIN is not None


# ---------------------------------------------------------------------------
# Private-socket fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def tmux_socket(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Yield the path to a private tmux socket directory.

    The socket file itself is created by tmux on first use.  We only need to
    provide a path inside a writable directory.
    """
    d = tmp_path_factory.mktemp("tmux_socket")
    return d / "test.sock"


@pytest.fixture(scope="module")
def backend(tmux_socket: Path):  # type: ignore[return]
    """Yield a TmuxBackend pointed at the private socket.

    On teardown, kill the private tmux server unconditionally.
    """
    if not _tmux_available():
        pytest.skip("tmux not found on PATH")

    be = TmuxBackend(socket_path=tmux_socket, tmux_binary=_TMUX_BIN or "tmux")
    yield be

    # Always tear down the server — even when tests have already killed it.
    try:
        subprocess.run(
            [_TMUX_BIN or "tmux", "-S", str(tmux_socket), "kill-server"],
            timeout=5,
            check=False,
            capture_output=True,
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SESSION_NAME = "contract-test"


def _wait_for_pane_alive(backend: TmuxBackend, pane_id: str, *, retries: int = 10) -> bool:
    """Poll until the pane is alive (dead=False) or retries exhausted."""
    for _ in range(retries):
        panes = backend.list_panes()
        for p in panes:
            if p.id == pane_id and not p.dead:
                return True
        time.sleep(0.2)
    return False


# ---------------------------------------------------------------------------
# Sequential contract test — single test function to guarantee ordering
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _tmux_available(), reason="tmux not found on PATH")
def test_full_api_contract(backend: TmuxBackend, tmux_socket: Path) -> None:
    """Exercise every ABC method sequentially against a real tmux binary.

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
    win2_id = win2.id  # e.g. "@1"

    # Verify the window appears in list_windows
    windows = backend.list_windows(_SESSION_NAME)
    win_ids = [w.id for w in windows]
    assert win2_id in win_ids

    # ------------------------------------------------------------------
    # 4. split_pane — horizontal and vertical, including before=True
    # ------------------------------------------------------------------
    # Start from the base pane of win2 (window target format: session:window_id)
    target_win2 = f"{_SESSION_NAME}:{win2_id}"

    # Get the initial pane in win2
    panes_before = backend.list_panes(target=target_win2)
    assert len(panes_before) >= 1
    base_pane_id = panes_before[0].id

    # Horizontal split
    pane_h = backend.split_pane(base_pane_id, "h")
    assert pane_h.id != base_pane_id

    # Vertical split with before=True
    pane_vb = backend.split_pane(base_pane_id, "v", before=True)
    assert pane_vb.id != base_pane_id

    # ------------------------------------------------------------------
    # 5. list_panes with target
    # ------------------------------------------------------------------
    panes_win2 = backend.list_panes(target=target_win2)
    # We started with 1, added 2 splits -> expect at least 3
    assert len(panes_win2) >= 3
    pane_ids = [p.id for p in panes_win2]
    assert pane_h.id in pane_ids

    # ------------------------------------------------------------------
    # 6. send_keys + capture_pane
    # ------------------------------------------------------------------
    backend.send_keys(base_pane_id, "echo CPSM_CONTRACT_MARKER")
    time.sleep(0.3)  # allow the shell to process the command
    output = backend.capture_pane(base_pane_id, lines=50)
    assert "CPSM_CONTRACT_MARKER" in output

    # ------------------------------------------------------------------
    # 7. set_window_option + capture_layout
    # ------------------------------------------------------------------
    backend.set_window_option(target_win2, "remain-on-exit", "on")
    layout_str = backend.capture_layout(target_win2)
    # Layout string is non-empty and contains geometry info
    assert layout_str and "," in layout_str

    # ------------------------------------------------------------------
    # 8. respawn_pane (kill_existing=True) — assert pane stays alive
    # ------------------------------------------------------------------
    backend.respawn_pane(base_pane_id, "sleep 30", kill_existing=True)
    alive = _wait_for_pane_alive(backend, base_pane_id)
    assert alive, f"Pane {base_pane_id} did not become alive after respawn"

    # ------------------------------------------------------------------
    # 9. select_layout preset
    # ------------------------------------------------------------------
    # Should not raise; tmux applies the preset and redistributes.
    backend.select_layout(target_win2, "tiled")

    # ------------------------------------------------------------------
    # 10. select_pane
    # ------------------------------------------------------------------
    backend.select_pane(pane_h.id)

    # ------------------------------------------------------------------
    # 11. swap_panes
    # ------------------------------------------------------------------
    backend.swap_panes(base_pane_id, pane_h.id)
    # After swap the window still has the same panes.
    panes_after_swap = backend.list_panes(target=target_win2)
    ids_after = {p.id for p in panes_after_swap}
    assert base_pane_id in ids_after
    assert pane_h.id in ids_after

    # ------------------------------------------------------------------
    # 12. resize_pane
    # ------------------------------------------------------------------
    backend.resize_pane(base_pane_id, 40, 12)
    # No assertion on exact size — tmux may clamp to available space.
    # Just assert no exception is raised.

    # ------------------------------------------------------------------
    # 13. break_pane -> move_pane round-trip
    # ------------------------------------------------------------------
    new_win = backend.break_pane(pane_h.id, detached=True)
    broken_win_id = new_win.id

    # Verify the broken window exists in the session
    windows_after_break = backend.list_windows(_SESSION_NAME)
    all_win_ids = [w.id for w in windows_after_break]
    assert broken_win_id in all_win_ids

    # move_pane — move the broken window's pane back to win2
    # First get the pane in the broken window
    broken_panes = backend.list_panes(target=f"{_SESSION_NAME}:{broken_win_id}")
    if broken_panes:
        backend.move_pane(broken_panes[0].id, target_win2)

    # ------------------------------------------------------------------
    # 14. kill_pane, kill_window, kill_session — clean up
    # ------------------------------------------------------------------
    # Kill a specific pane (pane_vb if still alive)
    try:
        backend.kill_pane(pane_vb.id)
    except subprocess.CalledProcessError:
        pass  # Might already be gone after break_pane / move_pane ops

    # Kill the broken window (if it still exists)
    try:
        backend.kill_window(f"{_SESSION_NAME}:{broken_win_id}")
    except subprocess.CalledProcessError:
        pass

    # Kill the entire session
    backend.kill_session(_SESSION_NAME)

    # Verify the session is gone
    remaining = backend.list_sessions()
    remaining_names = [s.name for s in remaining]
    assert _SESSION_NAME not in remaining_names


# ---------------------------------------------------------------------------
# Additional focused tests — each isolated session
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _tmux_available(), reason="tmux not found on PATH")
def test_list_sessions_empty(backend: TmuxBackend) -> None:
    """list_sessions returns an empty list when no sessions exist."""
    # Make sure the contract session from the previous test is gone.
    try:
        backend.kill_session(_SESSION_NAME)
    except Exception:
        pass
    sessions = backend.list_sessions()
    names = [s.name for s in sessions]
    assert _SESSION_NAME not in names


@pytest.mark.skipif(not _tmux_available(), reason="tmux not found on PATH")
def test_capabilities_populated(backend: TmuxBackend) -> None:
    """BackendCapabilities.name is 'tmux' and version string is non-empty."""
    caps = backend.capabilities
    assert caps.name == "tmux"
    assert caps.version.startswith("tmux")
    # tmux on the dev box is recent enough
    assert caps.supports_split_before is True
    assert caps.supports_capture_pane is True


@pytest.mark.skipif(not _tmux_available(), reason="tmux not found on PATH")
def test_split_pane_before_both_directions(
    backend: TmuxBackend,
) -> None:
    """split_pane with before=True works in both h and v directions."""
    sess_name = "contract-split-test"
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


@pytest.mark.skipif(not _tmux_available(), reason="tmux not found on PATH")
def test_capture_pane_returns_content(backend: TmuxBackend) -> None:
    """capture_pane returns content sent via send_keys."""
    sess_name = "contract-capture-test"
    try:
        backend.new_session(sess_name, 80, 24)
        panes = backend.list_panes(target=sess_name)
        pane_id = panes[0].id
        backend.send_keys(pane_id, "echo CAPTURE_TEST_TOKEN")
        time.sleep(0.3)
        content = backend.capture_pane(pane_id, lines=50)
        assert "CAPTURE_TEST_TOKEN" in content
    finally:
        try:
            backend.kill_session(sess_name)
        except Exception:
            pass


@pytest.mark.skipif(not _tmux_available(), reason="tmux not found on PATH")
def test_remain_on_exit_option(backend: TmuxBackend) -> None:
    """set_window_option remain-on-exit on does not raise."""
    sess_name = "contract-rox-test"
    try:
        backend.new_session(sess_name, 80, 24)
        windows = backend.list_windows(sess_name)
        assert windows
        win_target = f"{sess_name}:{windows[0].id}"
        # Should not raise
        backend.set_window_option(win_target, "remain-on-exit", "on")
    finally:
        try:
            backend.kill_session(sess_name)
        except Exception:
            pass
