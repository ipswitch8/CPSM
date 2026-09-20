# -*- coding: utf-8 -*-
"""Tests for RemoteControlAuthDialog.

Drives the wizard end-to-end with a FakeRemoteControlService and a
recording terminal-spawn function so no real SSH is ever attempted.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from cpsm.services.remote_control_service import PreflightResult
from cpsm.ui.dialogs.remote_control_auth import RemoteControlAuthDialog

# Make sure a QApplication exists (pytest-qt does this via qtbot, but we
# don't strictly need its event loop helpers).


@pytest.fixture
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


class FakeService:
    """Stand-in RemoteControlService.

    Behaviour:
      - preflight_result: returned by check_preflight.
      - credentials_after: credentials_present returns False until this many
        calls have occurred, then True.
    """

    def __init__(
        self,
        preflight_result: PreflightResult,
        credentials_after: int = 1,
    ) -> None:
        self.preflight_result = preflight_result
        self.preflight_calls = 0
        self.credentials_calls = 0
        self.credentials_after = credentials_after

    def check_preflight(self, **kw: object) -> PreflightResult:
        self.preflight_calls += 1
        return self.preflight_result

    def credentials_present(self, **kw: object) -> bool:
        self.credentials_calls += 1
        return self.credentials_calls > self.credentials_after

    def build_auth_ssh_argv(self, **kw: object) -> list[str]:
        return ["ssh", "-L", "8080:localhost:8080", "u@h"]


def _pump_until(app: QApplication, predicate, timeout_ms: int = 1000) -> bool:
    """Process events until *predicate* returns True or timeout."""
    import time

    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    app.processEvents()
    return predicate()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_full_flow_advances_through_all_pages(self, app: QApplication) -> None:
        svc = FakeService(
            preflight_result=PreflightResult(
                ok=True,
                os_kernel="Linux",
                claude_version=(2, 1, 51),
                api_key_set=False,
            ),
            credentials_after=1,  # credentials appear on 2nd poll
        )
        spawned: list[tuple[list[str], str]] = []
        dlg = RemoteControlAuthDialog(
            host="example.com",
            user="ubuntu",
            service=svc,
            spawn_terminal=lambda argv, title: spawned.append((argv, title)),
            poll_interval_ms=50,
        )
        dlg.show()

        # Wait for preflight to complete
        assert _pump_until(app, lambda: dlg._btn_next.isEnabled())
        assert svc.preflight_calls == 1
        assert dlg._pages.currentIndex() == 0

        # Click Continue → instructions page
        dlg._btn_next.click()
        app.processEvents()
        assert dlg._pages.currentIndex() == 1

        # Open auth terminal
        dlg._btn_open_terminal.click()
        app.processEvents()
        assert len(spawned) == 1
        assert dlg._pages.currentIndex() == 2  # polling

        # Polling should detect credentials and advance to Done
        assert _pump_until(app, lambda: dlg._pages.currentIndex() == 3)
        assert dlg._btn_next.text() == "Done"

        # Final accept
        dlg._btn_next.click()
        assert dlg.authenticated is True


# ---------------------------------------------------------------------------
# Preflight failure paths
# ---------------------------------------------------------------------------


class TestPreflightBlocking:
    def test_macos_target_does_not_enable_next(self, app: QApplication) -> None:
        svc = FakeService(
            preflight_result=PreflightResult(
                ok=False,
                os_kernel="Darwin",
                claude_version=(2, 1, 51),
                api_key_set=False,
                errors=["macOS targets cannot be authenticated over SSH"],
            )
        )
        dlg = RemoteControlAuthDialog(
            host="mac.local",
            user="ops",
            service=svc,
            spawn_terminal=lambda *a, **k: None,
            poll_interval_ms=50,
        )
        dlg.show()
        assert _pump_until(app, lambda: "macOS" in dlg._lbl_preflight_status.text())
        assert not dlg._btn_next.isEnabled()
        assert dlg._pages.currentIndex() == 0

    def test_too_old_claude_does_not_enable_next(self, app: QApplication) -> None:
        svc = FakeService(
            preflight_result=PreflightResult(
                ok=False,
                os_kernel="Linux",
                claude_version=(2, 1, 50),
                api_key_set=False,
                errors=["Claude Code 2.1.50 is too old"],
            )
        )
        dlg = RemoteControlAuthDialog(
            host="h",
            user="u",
            service=svc,
            spawn_terminal=lambda *a, **k: None,
            poll_interval_ms=50,
        )
        dlg.show()
        assert _pump_until(app, lambda: "too old" in dlg._lbl_preflight_status.text())
        assert not dlg._btn_next.isEnabled()


# ---------------------------------------------------------------------------
# Polling timeout / cancellation
# ---------------------------------------------------------------------------


class TestPolling:
    def test_cancel_stops_polling_timer(self, app: QApplication) -> None:
        svc = FakeService(
            preflight_result=PreflightResult(
                ok=True,
                os_kernel="Linux",
                claude_version=(2, 1, 51),
                api_key_set=False,
            ),
            credentials_after=999,  # never appears
        )
        dlg = RemoteControlAuthDialog(
            host="h",
            user="u",
            service=svc,
            spawn_terminal=lambda *a, **k: None,
            poll_interval_ms=50,
        )
        dlg.show()
        assert _pump_until(app, lambda: dlg._btn_next.isEnabled())
        dlg._btn_next.click()  # → instructions
        app.processEvents()
        dlg._btn_open_terminal.click()  # → polling
        app.processEvents()
        assert dlg._poll_timer is not None
        assert dlg._poll_timer.isActive()

        dlg.reject()
        assert dlg._poll_timer is None


# ---------------------------------------------------------------------------
# Terminal spawn argv
# ---------------------------------------------------------------------------


class TestTerminalSpawn:
    def test_uses_user_supplied_forward_port(self, app: QApplication) -> None:
        captured_argv: list[list[str]] = []
        captured_argv_kw: list[dict[str, object]] = []

        class CapturingService(FakeService):
            def build_auth_ssh_argv(self, **kw: object) -> list[str]:
                captured_argv_kw.append(kw)
                return ["ssh", "-L", f"{kw['forward_port']}:localhost:{kw['forward_port']}", "u@h"]

        svc = CapturingService(
            preflight_result=PreflightResult(
                ok=True,
                os_kernel="Linux",
                claude_version=(2, 1, 51),
                api_key_set=False,
            ),
            credentials_after=999,
        )
        dlg = RemoteControlAuthDialog(
            host="h",
            user="u",
            service=svc,
            spawn_terminal=lambda argv, title: captured_argv.append(argv),
            poll_interval_ms=50,
        )
        dlg.show()
        assert _pump_until(app, lambda: dlg._btn_next.isEnabled())
        dlg._btn_next.click()
        app.processEvents()
        # User changes port from default
        dlg._spin_forward_port.setValue(9090)
        dlg._btn_open_terminal.click()
        app.processEvents()
        assert captured_argv_kw[0]["forward_port"] == 9090
        assert "9090:localhost:9090" in " ".join(captured_argv[0])
