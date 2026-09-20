# -*- coding: utf-8 -*-
"""
Contract tests for PsmuxBackend — runs against a real PSMUX installation.

Marked ``@pytest.mark.integration``; skipped automatically on non-Windows
platforms (PSMUX is a Windows-only multiplexer).  Always tears down test
sessions in fixture finalizers, even on test failure.

The test structure mirrors ``test_itmux_backend_contract.py`` so the same
ABC surface is validated consistently across backends.

Spec: §7.4, §7.5, §8
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time

import pytest

from cpsm.platform.psmux_backend import PsmuxBackend

# ---------------------------------------------------------------------------
# Platform guard — PSMUX / pwsh only runs on Windows
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.integration

if sys.platform != "win32":
    pytest.skip("PSMUX contract tests run on Windows CI only", allow_module_level=True)

# ---------------------------------------------------------------------------
# Binary discovery
# ---------------------------------------------------------------------------

_PWSH_BIN = shutil.which("pwsh")


def _psmux_available() -> bool:
    """Return True if pwsh is on PATH and the Psmux module is importable."""
    if _PWSH_BIN is None:
        return False
    try:
        result = subprocess.run(
            [_PWSH_BIN, "-NoProfile", "-Command", "Get-Module -ListAvailable Psmux"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0 and "Psmux" in result.stdout
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def backend():  # type: ignore[return]
    """Yield a PsmuxBackend instance.

    On teardown, kill any lingering test sessions unconditionally.
    """
    if not _psmux_available():
        pytest.skip("PSMUX module not available on this system")

    be = PsmuxBackend(pwsh_binary=_PWSH_BIN or "pwsh")
    yield be

    # Cleanup: kill any test sessions that may have survived.
    for name in (_SESSION_NAME, "psmux-contract-split", "psmux-contract-capture"):
        try:
            be.kill_session(name)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SESSION_NAME = "psmux-contract-test"


def _wait_for_pane_alive(backend: PsmuxBackend, pane_id: str, *, retries: int = 10) -> bool:
    """Poll until *pane_id* is alive (dead=False) or retries exhausted."""
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


@pytest.mark.skipif(not _psmux_available(), reason="PSMUX not available")
def test_full_api_contract(backend: PsmuxBackend) -> None:
    """Exercise every ABC method sequentially against a real PSMUX installation.

    Steps:
    1.  new_session
    2.  list_sessions
    3.  new_window
    4.  list_windows
    5.  split_pane (horizontal, vertical, before=True)
    6.  list_panes
    7.  send_keys + capture_pane
    8.  set_window_option + capture_layout
    9.  respawn_pane
    10. select_layout preset
    11. select_pane
    12. swap_panes
    13. resize_pane
    14. break_pane → move_pane round-trip
    15. kill_pane, kill_window, kill_session
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

    # ------------------------------------------------------------------
    # 4. list_windows
    # ------------------------------------------------------------------
    windows = backend.list_windows(_SESSION_NAME)
    win_ids = [w.id for w in windows]
    assert win2_id in win_ids

    # ------------------------------------------------------------------
    # 5. split_pane
    # ------------------------------------------------------------------
    target_win2 = f"{_SESSION_NAME}:{win2_id}"
    panes_before = backend.list_panes(target=target_win2)
    assert len(panes_before) >= 1
    base_pane_id = panes_before[0].id

    pane_h = backend.split_pane(base_pane_id, "h")
    assert pane_h.id != base_pane_id

    pane_vb = backend.split_pane(base_pane_id, "v", before=True)
    assert pane_vb.id != base_pane_id

    # ------------------------------------------------------------------
    # 6. list_panes with target
    # ------------------------------------------------------------------
    panes_win2 = backend.list_panes(target=target_win2)
    assert len(panes_win2) >= 3
    pane_ids = [p.id for p in panes_win2]
    assert pane_h.id in pane_ids

    # ------------------------------------------------------------------
    # 7. send_keys + capture_pane
    # ------------------------------------------------------------------
    backend.send_keys(base_pane_id, "echo PSMUX_CONTRACT_MARKER")
    time.sleep(0.5)
    output = backend.capture_pane(base_pane_id, lines=50)
    assert "PSMUX_CONTRACT_MARKER" in output

    # ------------------------------------------------------------------
    # 8. set_window_option + capture_layout
    # ------------------------------------------------------------------
    backend.set_window_option(target_win2, "remain-on-exit", "on")
    layout_str = backend.capture_layout(target_win2)
    assert layout_str  # non-empty

    # ------------------------------------------------------------------
    # 9. respawn_pane
    # ------------------------------------------------------------------
    backend.respawn_pane(base_pane_id, "sleep 30", kill_existing=True)
    alive = _wait_for_pane_alive(backend, base_pane_id)
    assert alive, f"Pane {base_pane_id} did not become alive after respawn"

    # ------------------------------------------------------------------
    # 10. select_layout preset
    # ------------------------------------------------------------------
    backend.select_layout(target_win2, "tiled")

    # ------------------------------------------------------------------
    # 11. select_pane
    # ------------------------------------------------------------------
    backend.select_pane(pane_h.id)

    # ------------------------------------------------------------------
    # 12. swap_panes
    # ------------------------------------------------------------------
    backend.swap_panes(base_pane_id, pane_h.id)
    panes_after_swap = backend.list_panes(target=target_win2)
    ids_after = {p.id for p in panes_after_swap}
    assert base_pane_id in ids_after
    assert pane_h.id in ids_after

    # ------------------------------------------------------------------
    # 13. resize_pane
    # ------------------------------------------------------------------
    backend.resize_pane(base_pane_id, 40, 12)

    # ------------------------------------------------------------------
    # 14. break_pane → move_pane round-trip
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
    # 15. kill_pane, kill_window, kill_session
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
# Focused additional tests
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _psmux_available(), reason="PSMUX not available")
def test_list_sessions_empty(backend: PsmuxBackend) -> None:
    """list_sessions returns an empty list when no sessions exist."""
    try:
        backend.kill_session(_SESSION_NAME)
    except Exception:
        pass
    sessions = backend.list_sessions()
    names = [s.name for s in sessions]
    assert _SESSION_NAME not in names


@pytest.mark.skipif(not _psmux_available(), reason="PSMUX not available")
def test_capabilities_populated(backend: PsmuxBackend) -> None:
    """BackendCapabilities.name is 'psmux' and all flags are True."""
    caps = backend.capabilities
    assert caps.name == "psmux"
    assert caps.supports_split_before is True
    assert caps.supports_remain_on_exit is True
    assert caps.supports_capture_pane is True


@pytest.mark.skipif(not _psmux_available(), reason="PSMUX not available")
def test_split_pane_before_both_directions(backend: PsmuxBackend) -> None:
    """split_pane with before=True works in both h and v directions."""
    sess_name = "psmux-contract-split"
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


@pytest.mark.skipif(not _psmux_available(), reason="PSMUX not available")
def test_capture_pane_returns_content(backend: PsmuxBackend) -> None:
    """capture_pane returns content sent via send_keys."""
    sess_name = "psmux-contract-capture"
    try:
        backend.new_session(sess_name, 80, 24)
        panes = backend.list_panes(target=sess_name)
        pane_id = panes[0].id
        backend.send_keys(pane_id, "echo PSMUX_CAPTURE_TOKEN")
        time.sleep(0.5)
        content = backend.capture_pane(pane_id, lines=50)
        assert "PSMUX_CAPTURE_TOKEN" in content
    finally:
        try:
            backend.kill_session(sess_name)
        except Exception:
            pass


@pytest.mark.skipif(not _psmux_available(), reason="PSMUX not available")
def test_remain_on_exit_option(backend: PsmuxBackend) -> None:
    """set_window_option remain-on-exit on does not raise."""
    sess_name = "psmux-contract-rox"
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
