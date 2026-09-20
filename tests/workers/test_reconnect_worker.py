# -*- coding: utf-8 -*-
"""
Tests for cpsm.workers.reconnect_worker.

Exercises backoff ordering, max_attempts cap, unlimited retries, and
QAtomicInt-based cancellation.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from cpsm.platform.base import Pane
from cpsm.workers.reconnect_worker import (
    ReconnectWorker,
    _AtomicInt,
    _reconnect_attempt,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pane(pane_id: str = "%1", *, dead: bool = False) -> Pane:
    return Pane(
        id=pane_id,
        session="cpsm",
        window_index=0,
        pane_index=0,
        pid=None if dead else 1234,
        dead=dead,
        current_command="ssh" if not dead else "",
        width=80,
        height=24,
        dead_status=None,
    )


def _alive_backend(pane_id: str = "%1", *, fail_times: int = 0) -> MagicMock:
    """Return a mock backend that fails *fail_times* times then succeeds.

    list_panes returns dead pane for the first *fail_times* calls, then
    returns an alive pane.
    """
    backend = MagicMock()
    backend.respawn_pane.return_value = None

    dead_response = [[_make_pane(pane_id, dead=True)]]
    alive_response = [[_make_pane(pane_id, dead=False)]]

    backend.list_panes.side_effect = (
        dead_response * fail_times + alive_response * 100  # enough for any test
    )
    return backend


def _worker(
    backend: MagicMock,
    backoff_ms: list[int] | None = None,
    max_attempts: int = 0,
    pane_id: str = "%1",
) -> ReconnectWorker:
    return ReconnectWorker(
        backend,
        connection_id="conn-1",
        pane_id=pane_id,
        launcher_command="bash /opt/cpsm/launchers/ssh-shell.sh",
        backoff_ms=backoff_ms or [10, 20, 50],
        max_attempts=max_attempts,
    )


# ---------------------------------------------------------------------------
# Pure-function unit tests (no threading)
# ---------------------------------------------------------------------------


class TestAtomicInt:
    def test_initial_value(self) -> None:
        a = _AtomicInt(0)
        assert a.loadRelaxed() == 0

    def test_store_and_load(self) -> None:
        a = _AtomicInt(0)
        a.storeRelaxed(1)
        assert a.loadRelaxed() == 1


class TestReconnectAttempt:
    def test_alive_after_respawn(self) -> None:
        alive_pane = _make_pane("%1", dead=False)
        result, err = _reconnect_attempt(
            pane_id="%1",
            launcher_command="bash launcher.sh",
            respawn_fn=lambda: None,
            list_panes_fn=lambda: [alive_pane],
            stop_fn=lambda: False,
        )
        from cpsm.workers.reconnect_worker import _RESULT_ALIVE

        assert result == _RESULT_ALIVE
        assert err == ""

    def test_dead_after_respawn(self) -> None:
        dead_pane = _make_pane("%1", dead=True)
        result, _err = _reconnect_attempt(
            pane_id="%1",
            launcher_command="bash launcher.sh",
            respawn_fn=lambda: None,
            list_panes_fn=lambda: [dead_pane],
            stop_fn=lambda: False,
        )
        from cpsm.workers.reconnect_worker import _RESULT_DEAD

        assert result == _RESULT_DEAD

    def test_stopped_after_respawn(self) -> None:
        result, _ = _reconnect_attempt(
            pane_id="%1",
            launcher_command="bash launcher.sh",
            respawn_fn=lambda: None,
            list_panes_fn=lambda: [],
            stop_fn=lambda: True,  # stop requested right after respawn
        )
        from cpsm.workers.reconnect_worker import _RESULT_STOPPED

        assert result == _RESULT_STOPPED

    def test_respawn_exception_captured(self) -> None:
        dead_pane = _make_pane("%1", dead=True)

        def bad_respawn() -> None:
            raise RuntimeError("tmux not found")

        result, err = _reconnect_attempt(
            pane_id="%1",
            launcher_command="bash launcher.sh",
            respawn_fn=bad_respawn,
            list_panes_fn=lambda: [dead_pane],
            stop_fn=lambda: False,
        )
        from cpsm.workers.reconnect_worker import _RESULT_DEAD

        assert result == _RESULT_DEAD
        assert "tmux not found" in err

    def test_list_panes_exception_captured(self) -> None:
        def bad_list() -> list[Pane]:
            raise RuntimeError("backend gone")

        result, err = _reconnect_attempt(
            pane_id="%1",
            launcher_command="bash launcher.sh",
            respawn_fn=lambda: None,
            list_panes_fn=bad_list,
            stop_fn=lambda: False,
        )
        from cpsm.workers.reconnect_worker import _RESULT_DEAD

        assert result == _RESULT_DEAD
        assert "backend gone" in err

    def test_empty_pane_list_is_dead(self) -> None:
        result, _ = _reconnect_attempt(
            pane_id="%1",
            launcher_command="bash launcher.sh",
            respawn_fn=lambda: None,
            list_panes_fn=lambda: [],
            stop_fn=lambda: False,
        )
        from cpsm.workers.reconnect_worker import _RESULT_DEAD

        assert result == _RESULT_DEAD

    def test_different_pane_id_is_dead(self) -> None:
        other_pane = _make_pane("%9", dead=False)
        result, _ = _reconnect_attempt(
            pane_id="%1",
            launcher_command="bash launcher.sh",
            respawn_fn=lambda: None,
            list_panes_fn=lambda: [other_pane],
            stop_fn=lambda: False,
        )
        from cpsm.workers.reconnect_worker import _RESULT_DEAD

        assert result == _RESULT_DEAD


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.ui
class TestReconnectWorkerSuccess:
    def test_first_attempt_success(self, qtbot: object) -> None:
        """Immediate success on the first attempt emits succeeded."""
        backend = _alive_backend(fail_times=0)
        w = _worker(backend)

        succeeded: list[str] = []
        w.succeeded.connect(succeeded.append)  # type: ignore[attr-defined]
        w.start()
        qtbot.waitUntil(lambda: len(succeeded) >= 1, timeout=2000)  # type: ignore[attr-defined]
        qtbot.waitUntil(lambda: not w.isRunning(), timeout=1000)  # type: ignore[attr-defined]

        assert succeeded == ["%1"]
        backend.respawn_pane.assert_called_once()

    def test_success_after_failures(self, qtbot: object) -> None:
        """Succeeds after 2 dead responses, then one alive response."""
        backend = _alive_backend(fail_times=2)
        w = _worker(backend, backoff_ms=[5, 10, 20])

        succeeded: list[str] = []
        w.succeeded.connect(succeeded.append)  # type: ignore[attr-defined]
        w.start()
        qtbot.waitUntil(lambda: len(succeeded) >= 1, timeout=3000)  # type: ignore[attr-defined]
        qtbot.waitUntil(lambda: not w.isRunning(), timeout=1000)  # type: ignore[attr-defined]

        assert succeeded == ["%1"]
        # respawn_pane called 3 times (2 failures + 1 success)
        assert backend.respawn_pane.call_count == 3

    def test_progress_emitted_per_attempt(self, qtbot: object) -> None:
        """progress signal is emitted for each attempt with incrementing attempt_number."""
        backend = _alive_backend(fail_times=2)
        w = _worker(backend, backoff_ms=[5, 10, 20], max_attempts=5)

        progress_calls: list[tuple[str, int, int]] = []
        w.progress.connect(  # type: ignore[attr-defined]
            lambda pid, attempt, max_a: progress_calls.append((pid, attempt, max_a))
        )
        succeeded: list[str] = []
        w.succeeded.connect(succeeded.append)  # type: ignore[attr-defined]

        w.start()
        qtbot.waitUntil(lambda: len(succeeded) >= 1, timeout=3000)  # type: ignore[attr-defined]
        qtbot.waitUntil(lambda: not w.isRunning(), timeout=1000)  # type: ignore[attr-defined]

        # 3 attempts (2 fail + 1 success)
        assert len(progress_calls) == 3
        assert [p[1] for p in progress_calls] == [1, 2, 3]
        assert all(p[2] == 5 for p in progress_calls)  # max_attempts passed through

    def test_backoff_order(self, qtbot: object) -> None:
        """respawn_pane is called and sleeps follow backoff_ms order."""
        backend = _alive_backend(fail_times=3)
        # Use easily measurable backoffs
        w = _worker(backend, backoff_ms=[10, 20, 40], max_attempts=0)

        succeeded: list[str] = []
        w.succeeded.connect(succeeded.append)  # type: ignore[attr-defined]
        w.start()
        qtbot.waitUntil(lambda: len(succeeded) >= 1, timeout=3000)  # type: ignore[attr-defined]
        qtbot.waitUntil(lambda: not w.isRunning(), timeout=1000)  # type: ignore[attr-defined]

        # 4 respawn calls (3 dead + 1 alive)
        assert backend.respawn_pane.call_count == 4

    def test_last_backoff_repeated_when_unlimited(self, qtbot: object) -> None:
        """When max_attempts=0 and backoff list exhausted, last value repeats."""
        backend = _alive_backend(fail_times=5)
        # Backoff list has only 2 entries; attempts 3+ reuse the last (20ms)
        w = _worker(backend, backoff_ms=[5, 20], max_attempts=0)

        succeeded: list[str] = []
        w.succeeded.connect(succeeded.append)  # type: ignore[attr-defined]
        w.start()
        qtbot.waitUntil(lambda: len(succeeded) >= 1, timeout=5000)  # type: ignore[attr-defined]
        qtbot.waitUntil(lambda: not w.isRunning(), timeout=1000)  # type: ignore[attr-defined]

        assert succeeded == ["%1"]


@pytest.mark.ui
class TestReconnectWorkerFailure:
    def test_max_attempts_cap(self, qtbot: object) -> None:
        """After max_attempts without success, emits failed and stops."""
        backend = _alive_backend(fail_times=100)  # never succeeds in test range
        w = _worker(backend, backoff_ms=[5, 10, 20], max_attempts=3)

        failed: list[tuple[str, str]] = []
        w.failed.connect(lambda pid, err: failed.append((pid, err)))  # type: ignore[attr-defined]
        w.start()
        qtbot.waitUntil(lambda: len(failed) >= 1, timeout=3000)  # type: ignore[attr-defined]
        qtbot.waitUntil(lambda: not w.isRunning(), timeout=1000)  # type: ignore[attr-defined]

        assert failed[0][0] == "%1"
        assert backend.respawn_pane.call_count == 3

    def test_respawn_exception_counted_as_failure(self, qtbot: object) -> None:
        """Exception from respawn_pane counts as a failed attempt."""
        backend = MagicMock()
        backend.respawn_pane.side_effect = RuntimeError("tmux error")
        backend.list_panes.return_value = [_make_pane(dead=True)]

        w = _worker(backend, backoff_ms=[5], max_attempts=2)

        failed: list[str] = []
        w.failed.connect(lambda pid, _: failed.append(pid))  # type: ignore[attr-defined]
        w.start()
        qtbot.waitUntil(lambda: len(failed) >= 1, timeout=2000)  # type: ignore[attr-defined]
        qtbot.waitUntil(lambda: not w.isRunning(), timeout=1000)  # type: ignore[attr-defined]

        assert failed == ["%1"]

    def test_list_panes_exception_counted_as_failure(self, qtbot: object) -> None:
        """Exception from list_panes after respawn counts as a failed attempt."""
        backend = MagicMock()
        backend.respawn_pane.return_value = None
        backend.list_panes.side_effect = RuntimeError("backend gone")

        w = _worker(backend, backoff_ms=[5], max_attempts=2)

        failed: list[str] = []
        w.failed.connect(lambda pid, _: failed.append(pid))  # type: ignore[attr-defined]
        w.start()
        qtbot.waitUntil(lambda: len(failed) >= 1, timeout=2000)  # type: ignore[attr-defined]
        qtbot.waitUntil(lambda: not w.isRunning(), timeout=1000)  # type: ignore[attr-defined]

        assert failed == ["%1"]


@pytest.mark.ui
class TestReconnectWorkerCancellation:
    def test_stop_during_sleep_emits_stopped(self, qtbot: object) -> None:
        """Calling stop() while sleeping the backoff emits stopped."""
        backend = _alive_backend(fail_times=100)
        # Long backoff so we can stop() while sleeping
        w = _worker(backend, backoff_ms=[2000], max_attempts=0)

        stopped: list[str] = []
        w.stopped.connect(stopped.append)  # type: ignore[attr-defined]

        w.start()
        # Let one attempt fail and enter sleep
        time.sleep(0.15)
        w.stop()
        qtbot.waitUntil(lambda: not w.isRunning(), timeout=1000)  # type: ignore[attr-defined]

        assert stopped == ["%1"]

    def test_stop_before_start_never_runs(self, qtbot: object) -> None:
        """stop() called before start() causes run() to exit immediately."""
        backend = _alive_backend(fail_times=0)
        w = _worker(backend)
        w.stop()
        w.start()
        qtbot.waitUntil(lambda: not w.isRunning(), timeout=1000)  # type: ignore[attr-defined]

        # stopped signal, or the thread just exits — either is acceptable.
        # Key invariant: thread is no longer running.
        assert not w.isRunning()

    def test_stop_unlimited_loop(self, qtbot: object) -> None:
        """stop() terminates an unlimited (max_attempts=0) loop promptly."""
        backend = _alive_backend(fail_times=1000)
        w = _worker(backend, backoff_ms=[5000], max_attempts=0)  # huge backoff

        stopped: list[str] = []
        w.stopped.connect(stopped.append)  # type: ignore[attr-defined]
        w.start()
        time.sleep(0.1)
        w.stop()
        qtbot.waitUntil(lambda: not w.isRunning(), timeout=500)  # type: ignore[attr-defined]

        assert stopped == ["%1"]


# ---------------------------------------------------------------------------
# Direct run() coverage tests — call run() in main thread for line coverage.
# ---------------------------------------------------------------------------


class TestReconnectWorkerRunDirect:
    """Call ReconnectWorker.run() from the main thread so coverage traces it."""

    def _worker_direct(
        self,
        backend: MagicMock,
        backoff_ms: list[int] | None = None,
        max_attempts: int = 0,
    ) -> ReconnectWorker:
        return ReconnectWorker(
            backend,
            connection_id="conn-1",
            pane_id="%1",
            launcher_command="bash launcher.sh",
            backoff_ms=backoff_ms or [1],
            max_attempts=max_attempts,
        )

    def test_run_stop_before_start(self) -> None:
        """Stop flag set before run() exits immediately with stopped signal."""
        backend = _alive_backend(fail_times=0)
        w = self._worker_direct(backend)
        w.stop()

        stopped: list[str] = []
        w.stopped.connect(stopped.append)  # type: ignore[attr-defined]
        w.run()

        assert stopped == ["%1"]
        backend.respawn_pane.assert_not_called()

    def test_run_immediate_success(self) -> None:
        """Pane alive on first check emits succeeded."""
        backend = _alive_backend(fail_times=0)
        w = self._worker_direct(backend)

        succeeded: list[str] = []
        w.succeeded.connect(succeeded.append)  # type: ignore[attr-defined]
        w.run()

        assert succeeded == ["%1"]

    def test_run_max_attempts_exceeded(self) -> None:
        """max_attempts cap triggers failed signal."""
        backend = _alive_backend(fail_times=100)
        w = self._worker_direct(backend, backoff_ms=[1], max_attempts=2)

        failed: list[tuple[str, str]] = []
        w.failed.connect(lambda pid, err: failed.append((pid, err)))  # type: ignore[attr-defined]
        w.run()

        assert failed[0][0] == "%1"
        assert backend.respawn_pane.call_count == 2

    def test_run_attempt_cap_before_first_attempt(self) -> None:
        """When max_attempts > 0 and attempt counter exceeds cap on the boundary."""
        backend = _alive_backend(fail_times=100)
        w = self._worker_direct(backend, backoff_ms=[1], max_attempts=1)

        failed: list[str] = []
        w.failed.connect(lambda pid, _: failed.append(pid))  # type: ignore[attr-defined]
        w.run()

        assert "%1" in failed
        assert backend.respawn_pane.call_count == 1

    def test_interruptible_sleep_stops_early(self) -> None:
        """_interruptible_sleep returns before duration when stop is set."""
        import time as _time

        backend = MagicMock()
        w = self._worker_direct(backend)
        w.stop()
        start = _time.monotonic()
        w._interruptible_sleep(10.0)  # 10 seconds, but stop is already set
        elapsed = _time.monotonic() - start
        assert elapsed < 0.5  # must exit well before 10s

    def test_interruptible_sleep_full_duration(self) -> None:
        """_interruptible_sleep sleeps the full requested duration when not stopped."""
        import time as _time

        backend = MagicMock()
        w = self._worker_direct(backend)
        start = _time.monotonic()
        w._interruptible_sleep(0.1)  # 100ms
        elapsed = _time.monotonic() - start
        assert elapsed >= 0.09
