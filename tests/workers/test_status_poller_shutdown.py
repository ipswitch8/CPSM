# -*- coding: utf-8 -*-
"""
tests/workers/test_status_poller_shutdown.py

Feature F: StatusPoller clean shutdown regression test.

Verifies that:
  - After start() + stop() the thread is no longer running.
  - No "QThread: Destroyed while thread is still running" abort occurs.
"""

from __future__ import annotations

import os
import threading
from typing import Any
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from cpsm.workers.status_poller import StatusPoller


def _make_backend_that_blocks() -> MagicMock:
    """Backend whose list_panes blocks for ~50 ms per call, simulating a real poll."""
    backend = MagicMock()
    event = threading.Event()

    def _slow_list_panes() -> list:
        # Simulate poll that takes 50 ms
        event.wait(timeout=0.05)
        return []

    backend.list_panes.side_effect = _slow_list_panes
    backend.capture_pane.return_value = None
    return backend


def _make_backend_fast() -> MagicMock:
    backend = MagicMock()
    backend.list_panes.return_value = []
    backend.capture_pane.return_value = None
    return backend


class TestStatusPollerCleanShutdown:
    def test_thread_not_running_after_stop_and_wait(self, qtbot: Any) -> None:
        """After stop() + quit() + wait(), isRunning() must be False."""
        backend = _make_backend_fast()
        poller = StatusPoller(backend, interval_ms=50)

        poller.start()
        # Give it time to enter its loop
        qtbot.waitUntil(lambda: poller.isRunning(), timeout=1000)  # type: ignore[attr-defined]

        poller.stop()
        poller.quit()
        finished = poller.wait(2000)  # 2-second budget

        assert finished, "QThread.wait() timed out — thread did not exit cleanly"
        assert not poller.isRunning(), "Thread still running after stop+quit+wait"

    def test_thread_not_running_after_app_shutdown_sequence(self, qtbot: Any) -> None:
        """The app.py _stop_poller() sequence is reproduced: stop+quit+wait(2000)."""
        backend = _make_backend_fast()
        poller = StatusPoller(backend, interval_ms=100)

        poller.start()
        qtbot.waitUntil(lambda: poller.isRunning(), timeout=1000)  # type: ignore[attr-defined]

        # Reproduce the _stop_poller() logic from app.py
        poller.stop()
        poller.quit()
        if not poller.wait(2000):
            poller.terminate()
            poller.wait(500)

        assert not poller.isRunning(), "Thread still running after full shutdown sequence"

    def test_stop_during_slow_poll_eventually_exits(self, qtbot: Any) -> None:
        """stop() during a blocking list_panes call allows the loop to exit."""
        backend = _make_backend_that_blocks()
        poller = StatusPoller(backend, interval_ms=200)

        poller.start()
        # Wait for at least one poll to start
        qtbot.waitUntil(lambda: backend.list_panes.call_count >= 1, timeout=2000)  # type: ignore[attr-defined]

        poller.stop()
        poller.quit()
        finished = poller.wait(3000)

        assert finished, "Thread did not exit within 3 s after stop() during poll"
        assert not poller.isRunning()

    def test_multiple_start_stop_cycles_are_clean(self, qtbot: Any) -> None:
        """A poller can be started and stopped multiple times without hanging."""
        backend = _make_backend_fast()

        for _cycle in range(3):
            poller = StatusPoller(backend, interval_ms=50)
            poller.start()
            qtbot.waitUntil(lambda: poller.isRunning(), timeout=1000)  # type: ignore[attr-defined]
            poller.stop()
            poller.quit()
            finished = poller.wait(2000)
            assert finished, f"Thread did not exit cleanly on cycle {_cycle}"
            assert not poller.isRunning()
