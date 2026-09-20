# -*- coding: utf-8 -*-
"""Tests for AdoptSessionDialog (D2).

The dialog has three phases (initial choice, waiting for pid exit, timeout
escalation). We drive it deterministically by:
  - Constructing it with very short poll/timeout values
  - Using a never-existing pid (already-dead) to force immediate accept
  - Using ``os.getpid()`` (always alive) to force timeout
"""

from __future__ import annotations

import os
from typing import Any

from PySide6.QtWidgets import QDialog, QPushButton

from cpsm.services.discovery_service import DiscoveredSession
from cpsm.ui.dialogs.adopt_session import AdoptSessionDialog

# A pid we can be confident is not assigned. Linux pid_max defaults to
# 4_194_304 and the kernel would never assign 2**31-1, so signaling it
# will always raise ProcessLookupError.
_DEAD_PID = 2_147_483_640


def _session(pid: int, kind: str = "claude-local", **kw: Any) -> DiscoveredSession:
    return DiscoveredSession(
        pid=pid,
        kind=kind,
        cmdline=kw.get("cmdline", "claude"),
        cwd=kw.get("cwd", "/tmp/proj"),
        host=kw.get("host", ""),
        user=kw.get("user", ""),
        tty=kw.get("tty", "/dev/pts/3"),
        suggested_connection_id=kw.get("suggested", ""),
    )


def _find_button(dlg: AdoptSessionDialog, key: str) -> QPushButton:
    btn = dlg.findChild(QPushButton, f"btn_adopt_{key}")
    assert btn is not None, f"button btn_adopt_{key} not found"
    return btn


class TestPhase1Choices:
    def test_initial_buttons_present(self, qtbot) -> None:
        dlg = AdoptSessionDialog(
            _session(_DEAD_PID),
            target_label="Test",
            timeout_ms=200,
            poll_interval_ms=50,
        )
        qtbot.addWidget(dlg)
        # All three Phase-1 buttons render.
        for key in ("close_self", "sigterm", "cancel"):
            assert _find_button(dlg, key) is not None

    def test_cancel_rejects(self, qtbot) -> None:
        dlg = AdoptSessionDialog(
            _session(_DEAD_PID),
            target_label="Test",
            timeout_ms=200,
            poll_interval_ms=50,
        )
        qtbot.addWidget(dlg)
        _find_button(dlg, "cancel").click()
        assert dlg.result() == QDialog.DialogCode.Rejected
        assert dlg.adopted is False

    def test_close_self_with_dead_pid_accepts_immediately(self, qtbot) -> None:
        """Pid is already gone, so Phase 2's first poll tick should accept."""
        dlg = AdoptSessionDialog(
            _session(_DEAD_PID),
            target_label="Test",
            timeout_ms=200,
            poll_interval_ms=50,
        )
        qtbot.addWidget(dlg)
        _find_button(dlg, "close_self").click()
        # The dialog enters Phase 2 then immediately checks pid → not alive
        # → accepts. _poll_tick is invoked synchronously inside _show_phase_2.
        assert dlg.result() == QDialog.DialogCode.Accepted
        assert dlg.adopted is True


class TestTimeoutEscalation:
    def test_timeout_offers_sigkill(self, qtbot) -> None:
        """When the pid stays alive past timeout, the dialog enters its
        timeout phase with a SIGKILL button."""
        dlg = AdoptSessionDialog(
            _session(os.getpid()),  # always alive
            target_label="Test",
            timeout_ms=120,
            poll_interval_ms=40,
        )
        qtbot.addWidget(dlg)
        _find_button(dlg, "close_self").click()
        # Wait long enough for the polling loop to time out.
        qtbot.wait(400)
        assert dlg.isVisible() or dlg.result() == 0  # not yet rejected
        # SIGKILL button should now be present (Phase 3).
        sigkill = dlg.findChild(QPushButton, "btn_adopt_sigkill")
        assert sigkill is not None, "SIGKILL button should appear after timeout"

    def test_cancel_during_timeout_rejects(self, qtbot) -> None:
        dlg = AdoptSessionDialog(
            _session(os.getpid()),
            target_label="Test",
            timeout_ms=120,
            poll_interval_ms=40,
        )
        qtbot.addWidget(dlg)
        _find_button(dlg, "close_self").click()
        qtbot.wait(400)
        # Cancel from Phase 3.
        _find_button(dlg, "cancel").click()
        assert dlg.result() == QDialog.DialogCode.Rejected
        assert dlg.adopted is False
