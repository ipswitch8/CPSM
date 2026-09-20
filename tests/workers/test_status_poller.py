# -*- coding: utf-8 -*-
"""
Tests for cpsm.workers.status_poller.

Uses pytest-qt's qtbot fixture to drive Qt signals without a real event loop
or actual multiplexer backend.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from cpsm.platform.base import Pane
from cpsm.workers.status_poller import (
    PaneState,
    PaneStatus,
    StatusPoller,
    _AtomicInt,
    _classify,
    _is_placeholder,
    _process_poll,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _make_pane(
    pane_id: str = "%1",
    *,
    dead: bool = False,
    current_command: str = "ssh",
    session: str = "cpsm",
    dead_status: int | None = None,
) -> Pane:
    """Build a minimal Pane for tests."""
    return Pane(
        id=pane_id,
        session=session,
        window_index=0,
        pane_index=0,
        pid=1234 if not dead else None,
        dead=dead,
        current_command=current_command,
        width=80,
        height=24,
        dead_status=dead_status,
    )


def _make_backend(
    panes_sequence: list[list[Pane]],
    *,
    sessions_attached: dict[str, bool] | None = None,
) -> MagicMock:
    """Return a mock backend whose list_panes() yields successive call results.

    *sessions_attached* maps session names to their attached state. The mock
    backend will return matching :class:`Session`-like objects from
    ``list_sessions()``. When None, every session yields ``attached=True``
    using session names extracted from the first pane batch.
    """
    backend = MagicMock()
    backend.list_panes.side_effect = panes_sequence
    backend.capture_pane.return_value = "last output"

    if sessions_attached is None:
        names: set[str] = set()
        for batch in panes_sequence:
            for p in batch:
                names.add(p.session)
        sessions_attached = {n: True for n in names}

    sessions = [
        SimpleNamespace(
            id=f"${i}",
            name=name,
            attached=attached,
            created_at=_NOW,
        )
        for i, (name, attached) in enumerate(sessions_attached.items())
    ]
    backend.list_sessions.return_value = sessions
    return backend


# ---------------------------------------------------------------------------
# Unit tests for helper functions
# ---------------------------------------------------------------------------


class TestIsPlaceholder:
    def test_exact_marker(self) -> None:
        assert _is_placeholder("_placeholder.sh") is True

    def test_partial_marker(self) -> None:
        assert _is_placeholder("/opt/cpsm/_placeholder.sh") is True

    def test_bare_placeholder(self) -> None:
        assert _is_placeholder("_placeholder") is True

    def test_normal_command(self) -> None:
        assert _is_placeholder("ssh") is False

    def test_empty(self) -> None:
        assert _is_placeholder("") is False


class TestClassify:
    def test_connected(self) -> None:
        pane = _make_pane(dead=False, current_command="ssh")
        status = _classify(pane, None, interval_s=3.0)
        assert status.state is PaneState.CONNECTED
        assert status.exit_code is None
        assert status.last_output_tail is None

    def test_empty_slot(self) -> None:
        pane = _make_pane(dead=False, current_command="_placeholder.sh")
        status = _classify(pane, None, interval_s=3.0)
        assert status.state is PaneState.EMPTY_SLOT

    def test_dead_clean_exit(self) -> None:
        pane = _make_pane(dead=True, dead_status=0)
        status = _classify(pane, None, interval_s=3.0)
        assert status.state is PaneState.DISCONNECTED_CLEAN
        assert status.exit_code == 0

    def test_dead_error_exit(self) -> None:
        pane = _make_pane(dead=True, dead_status=1)
        status = _classify(pane, None, interval_s=3.0, last_output_tail="error log")
        assert status.state is PaneState.ERROR
        assert status.exit_code == 1
        assert status.last_output_tail == "error log"

    def test_dead_unknown_exit_code(self) -> None:
        """When dead_status attribute is absent, fallback to ERROR."""
        pane = _make_pane(dead=True)  # no dead_status attr injected
        status = _classify(pane, None, interval_s=3.0)
        assert status.state is PaneState.ERROR

    def test_stale_after_2x_interval(self) -> None:
        """A pane that was CONNECTED but hasn't been seen for >= 2x interval is STALE."""
        from datetime import timedelta

        old_time = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
        # Simulate last_seen 7 seconds ago with a 3-second interval
        prev = PaneStatus(
            pane_id="%1",
            session="cpsm",
            state=PaneState.CONNECTED,
            last_seen=old_time,
            exit_code=None,
            last_output_tail=None,
        )
        pane = _make_pane(dead=False, current_command="ssh")

        with patch("cpsm.workers.status_poller.datetime") as mock_dt:
            future = old_time + timedelta(seconds=7)
            mock_dt.now.return_value = future
            status = _classify(pane, prev, interval_s=3.0)

        assert status.state is PaneState.STALE

    def test_not_stale_within_2x_interval(self) -> None:
        """A pane seen 5 seconds ago with 3-second interval is still CONNECTED."""
        from datetime import timedelta

        old_time = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
        prev = PaneStatus(
            pane_id="%1",
            session="cpsm",
            state=PaneState.CONNECTED,
            last_seen=old_time,
            exit_code=None,
            last_output_tail=None,
        )
        pane = _make_pane(dead=False, current_command="ssh")

        with patch("cpsm.workers.status_poller.datetime") as mock_dt:
            future = old_time + timedelta(seconds=5)
            mock_dt.now.return_value = future
            status = _classify(pane, prev, interval_s=3.0)

        assert status.state is PaneState.CONNECTED

    def test_carries_current_command(self) -> None:
        """PaneStatus reflects pane.current_command for alive panes."""
        pane = _make_pane(dead=False, current_command="ssh")
        status = _classify(pane, None, interval_s=3.0)
        assert status.current_command == "ssh"

    def test_carries_attached_default_true(self) -> None:
        """attached defaults to True so existing callers stay green."""
        pane = _make_pane(dead=False, current_command="ssh")
        status = _classify(pane, None, interval_s=3.0)
        assert status.attached is True

    def test_carries_attached_false(self) -> None:
        """attached=False propagates into PaneStatus so the UI can show blue."""
        pane = _make_pane(dead=False, current_command="ssh")
        status = _classify(pane, None, interval_s=3.0, attached=False)
        assert status.attached is False
        # State itself is still CONNECTED — interpretation happens in the UI
        # layer where launch profile is known.
        assert status.state is PaneState.CONNECTED

    def test_dead_pane_carries_current_command(self) -> None:
        """Even dead panes carry their last current_command (useful for
        diagnostics in the UI)."""
        pane = _make_pane(dead=True, dead_status=1, current_command="bash")
        status = _classify(pane, None, interval_s=3.0)
        assert status.current_command == "bash"


class TestAtomicInt:
    def test_initial_value(self) -> None:
        a = _AtomicInt(0)
        assert a.loadRelaxed() == 0

    def test_store_and_load(self) -> None:
        a = _AtomicInt(0)
        a.storeRelaxed(1)
        assert a.loadRelaxed() == 1

    def test_default_initial_value(self) -> None:
        a = _AtomicInt()
        assert a.loadRelaxed() == 0


class TestProcessPoll:
    """Tests for _process_poll — the pure core logic extracted from StatusPoller.run()."""

    def _capture(self, pane_id: str) -> str:
        return f"output for {pane_id}"

    def test_new_pane_is_changed(self) -> None:
        pane = _make_pane("%1")
        prev_state: dict[str, PaneStatus] = {}
        changed, _all_s, cur_ids = _process_poll(
            [pane], prev_state, interval_s=3.0, capture_pane_fn=self._capture
        )
        assert len(changed) == 1
        assert changed[0].pane_id == "%1"
        assert "%1" in cur_ids

    def test_same_state_not_in_changed(self) -> None:
        pane = _make_pane("%1")
        prev_state: dict[str, PaneStatus] = {}
        _process_poll([pane], prev_state, interval_s=3.0, capture_pane_fn=self._capture)
        # Second call — state is the same, should not be in changed
        changed, _, _ = _process_poll(
            [pane], prev_state, interval_s=3.0, capture_pane_fn=self._capture
        )
        assert changed == []

    def test_dead_pane_triggers_capture(self) -> None:
        captured: list[str] = []

        def capture_fn(pane_id: str) -> str:
            captured.append(pane_id)
            return "error trace"

        pane = _make_pane("%1", dead=True, dead_status=1)
        prev_state: dict[str, PaneStatus] = {}
        changed, _, _ = _process_poll(
            [pane], prev_state, interval_s=3.0, capture_pane_fn=capture_fn
        )
        assert "%1" in captured
        assert changed[0].last_output_tail == "error trace"

    def test_clean_exit_no_capture(self) -> None:
        captured: list[str] = []

        def capture_fn(pane_id: str) -> str:
            captured.append(pane_id)
            return "should not be called"

        pane = _make_pane("%1", dead=True, dead_status=0)
        prev_state: dict[str, PaneStatus] = {}
        _process_poll([pane], prev_state, interval_s=3.0, capture_pane_fn=capture_fn)
        assert captured == []

    def test_capture_exception_gives_none_tail(self) -> None:
        def capture_fn(pane_id: str) -> str:
            raise RuntimeError("capture failed")

        pane = _make_pane("%1", dead=True, dead_status=1)
        prev_state: dict[str, PaneStatus] = {}
        changed, _, _ = _process_poll(
            [pane], prev_state, interval_s=3.0, capture_pane_fn=capture_fn
        )
        assert changed[0].last_output_tail is None

    def test_multiple_panes_current_ids(self) -> None:
        panes = [_make_pane(f"%{i}") for i in range(4)]
        prev_state: dict[str, PaneStatus] = {}
        _, all_s, cur_ids = _process_poll(
            panes, prev_state, interval_s=3.0, capture_pane_fn=self._capture
        )
        assert cur_ids == {"%0", "%1", "%2", "%3"}
        assert len(all_s) == 4

    def test_state_transition_detected(self) -> None:
        alive = _make_pane("%1", dead=False)
        dead = _make_pane("%1", dead=True, dead_status=1)
        prev_state: dict[str, PaneStatus] = {}
        # First poll: alive
        _process_poll([alive], prev_state, interval_s=3.0, capture_pane_fn=self._capture)
        # Second poll: dead -> should appear in changed
        changed, _, _ = _process_poll(
            [dead], prev_state, interval_s=3.0, capture_pane_fn=self._capture
        )
        assert len(changed) == 1
        assert changed[0].state is PaneState.ERROR

    def test_attached_lookup_propagates(self) -> None:
        """When attached_lookup says session is detached, PaneStatus.attached=False."""
        pane = _make_pane("%1", session="cpsm-group-x")
        prev_state: dict[str, PaneStatus] = {}
        _, all_s, _ = _process_poll(
            [pane],
            prev_state,
            interval_s=3.0,
            capture_pane_fn=self._capture,
            attached_lookup=lambda s: False,
        )
        assert all_s[0].attached is False
        assert all_s[0].state is PaneState.CONNECTED  # tmux pane itself alive

    def test_attached_lookup_default_true(self) -> None:
        """Without attached_lookup, attached defaults to True."""
        pane = _make_pane("%1")
        prev_state: dict[str, PaneStatus] = {}
        _, all_s, _ = _process_poll(
            [pane], prev_state, interval_s=3.0, capture_pane_fn=self._capture
        )
        assert all_s[0].attached is True


# ---------------------------------------------------------------------------
# Integration tests with QThread / qtbot
# ---------------------------------------------------------------------------


@pytest.mark.ui
class TestStatusPollerThread:
    def test_state_changed_on_first_poll(self, qtbot: object) -> None:
        """state_changed fires for each new pane on the first poll."""
        panes = [_make_pane("%1"), _make_pane("%2")]
        backend = _make_backend([panes, []])  # second poll: empty
        poller = StatusPoller(backend, interval_ms=50)

        received: list[PaneStatus] = []
        poller.state_changed.connect(received.append)  # type: ignore[attr-defined]

        poller.start()
        # Wait for at least 2 signals (one per pane)
        qtbot.waitUntil(lambda: len(received) >= 2, timeout=2000)  # type: ignore[attr-defined]
        poller.stop()
        qtbot.waitUntil(lambda: not poller.isRunning(), timeout=1000)  # type: ignore[attr-defined]

        pane_ids = {s.pane_id for s in received}
        assert "%1" in pane_ids
        assert "%2" in pane_ids

    def test_no_duplicate_signal_on_same_state(self, qtbot: object) -> None:
        """state_changed must NOT fire when state is unchanged across polls."""
        pane = _make_pane("%1")
        # Return the same pane twice; second poll should not emit state_changed.
        backend = _make_backend([[pane], [pane], []])
        poller = StatusPoller(backend, interval_ms=50)

        received: list[PaneStatus] = []
        poller.state_changed.connect(received.append)  # type: ignore[attr-defined]
        poll_count = [0]
        poller.poll_complete.connect(lambda _: poll_count.__setitem__(0, poll_count[0] + 1))  # type: ignore[attr-defined]

        poller.start()
        # Wait for at least 2 complete polls.
        qtbot.waitUntil(lambda: poll_count[0] >= 2, timeout=2000)  # type: ignore[attr-defined]
        poller.stop()
        qtbot.waitUntil(lambda: not poller.isRunning(), timeout=1000)  # type: ignore[attr-defined]

        # Only one state_changed for the initial observation, not for the repeat.
        assert sum(1 for s in received if s.pane_id == "%1") == 1

    def test_dead_pane_detected_within_2x_interval(self, qtbot: object) -> None:
        """Dead pane must be detected by the second poll (2x interval)."""
        alive = _make_pane("%1", dead=False)
        dead = _make_pane("%1", dead=True, dead_status=1)
        # First poll: alive. Second poll: dead.
        backend = _make_backend([[alive], [dead], [dead]])
        poller = StatusPoller(backend, interval_ms=50)

        error_received: list[PaneStatus] = []

        def on_state(s: PaneStatus) -> None:
            if s.state is PaneState.ERROR:
                error_received.append(s)

        poller.state_changed.connect(on_state)  # type: ignore[attr-defined]
        poller.start()

        # Should detect within 2x 50ms = 100ms -- give 1000ms headroom.
        qtbot.waitUntil(lambda: len(error_received) >= 1, timeout=1000)  # type: ignore[attr-defined]
        poller.stop()
        qtbot.waitUntil(lambda: not poller.isRunning(), timeout=1000)  # type: ignore[attr-defined]

        assert error_received[0].pane_id == "%1"
        assert error_received[0].state is PaneState.ERROR

    def test_clean_exit_detected(self, qtbot: object) -> None:
        """Exit code 0 → DISCONNECTED_CLEAN."""
        alive = _make_pane("%1", dead=False)
        dead = _make_pane("%1", dead=True, dead_status=0)
        backend = _make_backend([[alive], [dead], [dead]])
        poller = StatusPoller(backend, interval_ms=50)

        clean_received: list[PaneStatus] = []

        def on_state(s: PaneStatus) -> None:
            if s.state is PaneState.DISCONNECTED_CLEAN:
                clean_received.append(s)

        poller.state_changed.connect(on_state)  # type: ignore[attr-defined]
        poller.start()
        qtbot.waitUntil(lambda: len(clean_received) >= 1, timeout=1000)  # type: ignore[attr-defined]
        poller.stop()
        qtbot.waitUntil(lambda: not poller.isRunning(), timeout=1000)  # type: ignore[attr-defined]

        assert clean_received[0].state is PaneState.DISCONNECTED_CLEAN

    def test_empty_slot_detection(self, qtbot: object) -> None:
        """Pane running _placeholder.sh → EMPTY_SLOT."""
        pane = _make_pane("%1", current_command="/opt/cpsm/_placeholder.sh")
        backend = _make_backend([[pane], []])
        poller = StatusPoller(backend, interval_ms=50)

        slots: list[PaneStatus] = []
        poller.state_changed.connect(  # type: ignore[attr-defined]
            lambda s: slots.append(s) if s.state is PaneState.EMPTY_SLOT else None
        )
        poller.start()
        qtbot.waitUntil(lambda: len(slots) >= 1, timeout=1000)  # type: ignore[attr-defined]
        poller.stop()
        qtbot.waitUntil(lambda: not poller.isRunning(), timeout=1000)  # type: ignore[attr-defined]

        assert slots[0].pane_id == "%1"

    def test_disappeared_pane_emits_unknown(self, qtbot: object) -> None:
        """Pane absent from list_panes after being tracked → UNKNOWN."""
        pane = _make_pane("%1")
        backend = _make_backend([[pane], [], []])
        poller = StatusPoller(backend, interval_ms=50)

        unknown: list[PaneStatus] = []
        poller.state_changed.connect(  # type: ignore[attr-defined]
            lambda s: unknown.append(s) if s.state is PaneState.UNKNOWN else None
        )
        poller.start()
        qtbot.waitUntil(lambda: len(unknown) >= 1, timeout=1000)  # type: ignore[attr-defined]
        poller.stop()
        qtbot.waitUntil(lambda: not poller.isRunning(), timeout=1000)  # type: ignore[attr-defined]

        assert unknown[0].pane_id == "%1"

    def test_stop_returns_promptly(self, qtbot: object) -> None:
        """stop() must cause run() to return within ~200 ms (5x chunk check)."""
        import time as _time

        # Infinite stream of the same pane so the loop doesn't end naturally.
        pane = _make_pane("%1")
        backend = MagicMock()
        backend.list_panes.return_value = [pane]
        backend.capture_pane.return_value = ""

        poller = StatusPoller(backend, interval_ms=5000)  # long interval
        poller.start()
        _time.sleep(0.05)  # let it enter sleep
        poller.stop()
        qtbot.waitUntil(lambda: not poller.isRunning(), timeout=500)  # type: ignore[attr-defined]

    def test_poll_complete_carries_all_panes(self, qtbot: object) -> None:
        """poll_complete list contains all panes from one poll cycle."""
        panes = [_make_pane(f"%{i}") for i in range(3)]
        backend = _make_backend([panes, []])
        poller = StatusPoller(backend, interval_ms=50)

        complete_calls: list[list[PaneStatus]] = []
        poller.poll_complete.connect(complete_calls.append)  # type: ignore[attr-defined]
        poller.start()
        qtbot.waitUntil(lambda: len(complete_calls) >= 1, timeout=1000)  # type: ignore[attr-defined]
        poller.stop()
        qtbot.waitUntil(lambda: not poller.isRunning(), timeout=1000)  # type: ignore[attr-defined]

        # First poll complete should include all 3 panes.
        first = complete_calls[0]
        assert len(first) == 3

    def test_detached_session_propagates_to_snapshot(self, qtbot: object) -> None:
        """When list_sessions() reports attached=False, snapshot reflects it.

        This is the user-visible "terminal app crashed" path: the tmux pane
        is still alive (state stays CONNECTED) but the snapshot tells the
        UI that no client is rendering it, so the sidebar can flip to blue.
        """
        pane = _make_pane("%1", session="cpsm-group-x", current_command="ssh")
        backend = _make_backend(
            [[pane], [pane], [pane]],
            sessions_attached={"cpsm-group-x": False},
        )
        poller = StatusPoller(backend, interval_ms=50)

        snaps: list[list[PaneStatus]] = []
        poller.poll_complete.connect(snaps.append)  # type: ignore[attr-defined]
        poller.start()
        qtbot.waitUntil(lambda: len(snaps) >= 1, timeout=1000)  # type: ignore[attr-defined]
        poller.stop()
        qtbot.waitUntil(lambda: not poller.isRunning(), timeout=1000)  # type: ignore[attr-defined]

        first = snaps[0]
        assert len(first) == 1
        assert first[0].state is PaneState.CONNECTED  # tmux pane still alive
        assert first[0].attached is False  # but no client is attached
        assert first[0].current_command == "ssh"

    def test_list_sessions_failure_defaults_to_attached(self, qtbot: object) -> None:
        """If list_sessions() raises, the poller treats everything as attached
        so live panes don't get incorrectly demoted to detached."""
        pane = _make_pane("%1", session="cpsm-group-x")
        backend = _make_backend([[pane], []])
        backend.list_sessions.side_effect = RuntimeError("tmux dropped sockets")
        poller = StatusPoller(backend, interval_ms=50)

        snaps: list[list[PaneStatus]] = []
        poller.poll_complete.connect(snaps.append)  # type: ignore[attr-defined]
        poller.start()
        qtbot.waitUntil(lambda: len(snaps) >= 1, timeout=1000)  # type: ignore[attr-defined]
        poller.stop()
        qtbot.waitUntil(lambda: not poller.isRunning(), timeout=1000)  # type: ignore[attr-defined]

        # Default: attached=True so we don't show false "disconnected" state.
        assert snaps[0][0].attached is True

    def test_backend_exception_skips_poll(self, qtbot: object) -> None:
        """If list_panes raises, the poller does not crash — it retries next cycle."""
        pane = _make_pane("%1")
        backend = MagicMock()
        # First call raises, second succeeds.
        backend.list_panes.side_effect = [RuntimeError("tmux gone"), [pane]]
        backend.capture_pane.return_value = ""

        poller = StatusPoller(backend, interval_ms=50)
        received: list[PaneStatus] = []
        poller.state_changed.connect(received.append)  # type: ignore[attr-defined]
        poller.start()
        qtbot.waitUntil(lambda: len(received) >= 1, timeout=2000)  # type: ignore[attr-defined]
        poller.stop()
        qtbot.waitUntil(lambda: not poller.isRunning(), timeout=1000)  # type: ignore[attr-defined]

        assert received[0].pane_id == "%1"

    def test_list_panes_failure_synthesizes_unknown_for_known_panes(self, qtbot: object) -> None:
        """When tmux server dies after we knew about a pane, list_panes raises.
        Regression: the poller used to ``continue`` on exception and freeze
        the snapshot, leaving connection borders stuck on red after the last
        cpsm session was reaped.  Now the exception is treated as "no panes",
        so the disappeared-detection branch synthesizes UNKNOWN for the pane
        and the UI can resolve it to "disconnected" (blue).
        """
        pane = _make_pane("%1", session="cpsm-cc-multi")
        backend = MagicMock()
        # First call: pane is alive.  Second call: tmux server gone.
        backend.list_panes.side_effect = [[pane], RuntimeError("no server running")]
        backend.list_sessions.return_value = []
        backend.capture_pane.return_value = ""

        poller = StatusPoller(backend, interval_ms=50)
        snaps: list[list[PaneStatus]] = []
        poller.poll_complete.connect(snaps.append)  # type: ignore[attr-defined]
        poller.start()
        qtbot.waitUntil(lambda: len(snaps) >= 2, timeout=2000)  # type: ignore[attr-defined]
        poller.stop()
        qtbot.waitUntil(lambda: not poller.isRunning(), timeout=1000)  # type: ignore[attr-defined]

        # First snapshot has the live pane; second has it as UNKNOWN
        # (synthesized because the next list_panes raised, which we now treat
        # as panes=[] so the disappeared branch fires).
        assert any(s.pane_id == "%1" and s.state != PaneState.UNKNOWN for s in snaps[0])
        assert any(s.pane_id == "%1" and s.state is PaneState.UNKNOWN for s in snaps[1])


# ---------------------------------------------------------------------------
# Direct run() coverage tests — call run() in main thread for line coverage.
# These tests exploit the stop flag to exit the loop immediately so the
# main-thread coverage tracer sees the loop body.
# ---------------------------------------------------------------------------


class TestStatusPollerRunDirect:
    """Call StatusPoller.run() from the main thread so coverage traces it."""

    def _make_poller(self, backend: MagicMock) -> StatusPoller:
        return StatusPoller(backend, interval_ms=50)

    def test_run_exits_on_stop_before_start(self) -> None:
        """If stop flag is set before run(), the loop never enters."""
        backend = MagicMock()
        backend.list_panes.return_value = [_make_pane("%1")]
        backend.capture_pane.return_value = ""
        poller = self._make_poller(backend)
        poller.stop()
        poller.run()  # must return immediately
        backend.list_panes.assert_not_called()

    def test_run_processes_one_poll_then_exits(self) -> None:
        """Run completes a poll and exits after second call returns empty + stop."""
        pane = _make_pane("%1")
        backend = MagicMock()
        call_count = [0]
        poller_holder: list[StatusPoller] = []

        def list_panes_side_effect() -> list[Pane]:
            call_count[0] += 1
            if call_count[0] == 1:
                return [pane]
            poller_holder[0].stop()
            return [pane]  # return same pane so loop body runs

        backend.list_panes.side_effect = list_panes_side_effect
        backend.capture_pane.return_value = ""

        poller = self._make_poller(backend)
        poller_holder.append(poller)

        received: list[PaneStatus] = []
        poller.state_changed.connect(received.append)  # type: ignore[attr-defined]
        poller.run()

        # At minimum, first poll fired
        assert call_count[0] >= 1

    def test_run_emits_unknown_for_disappeared_pane(self) -> None:
        """Pane removed after first poll → UNKNOWN emitted in second poll.

        We use a 3-call sequence: first call returns pane, second call returns
        empty (triggering UNKNOWN), third call stops the loop.
        """
        pane = _make_pane("%1")
        backend = MagicMock()
        call_count = [0]
        poller_holder: list[StatusPoller] = []

        def list_panes_side_effect() -> list[Pane]:
            call_count[0] += 1
            if call_count[0] == 1:
                return [pane]
            if call_count[0] == 2:
                return []  # pane gone — triggers UNKNOWN
            poller_holder[0].stop()
            return []

        backend.list_panes.side_effect = list_panes_side_effect
        backend.capture_pane.return_value = ""

        poller = self._make_poller(backend)
        poller_holder.append(poller)

        unknown: list[PaneStatus] = []
        poller.state_changed.connect(  # type: ignore[attr-defined]
            lambda s: unknown.append(s) if s.state is PaneState.UNKNOWN else None
        )
        poller.run()

        assert any(u.pane_id == "%1" for u in unknown)

    def test_run_handles_backend_exception(self) -> None:
        """list_panes raising does not crash run(); it sleeps and retries."""
        backend = MagicMock()
        call_count = [0]
        poller_holder: list[StatusPoller] = []

        def list_panes_side_effect() -> list[Pane]:
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("backend down")
            poller_holder[0].stop()
            return [_make_pane("%1")]

        backend.list_panes.side_effect = list_panes_side_effect
        backend.capture_pane.return_value = ""

        poller = self._make_poller(backend)
        poller_holder.append(poller)

        # Should not raise
        poller.run()
        assert call_count[0] >= 2

    def test_run_stop_during_pane_iteration(self) -> None:
        """Stop flag set during pane iteration causes early return."""
        backend = MagicMock()
        poller_holder: list[StatusPoller] = []
        call_count = [0]

        def list_panes_side_effect() -> list[Pane]:
            call_count[0] += 1
            panes = [_make_pane(f"%{i}") for i in range(5)]
            if call_count[0] == 1:
                # stop after returning the list — will be checked in loop
                poller_holder[0].stop()
            return panes

        backend.list_panes.side_effect = list_panes_side_effect
        backend.capture_pane.return_value = ""

        poller = self._make_poller(backend)
        poller_holder.append(poller)
        poller.run()  # must not hang

    def test_interruptible_sleep_stops_early(self) -> None:
        """_interruptible_sleep exits immediately when stop is set."""
        import time as _time

        backend = MagicMock()
        poller = self._make_poller(backend)
        poller.stop()
        start = _time.monotonic()
        poller._interruptible_sleep(10.0)
        elapsed = _time.monotonic() - start
        assert elapsed < 0.5

    def test_interruptible_sleep_full_duration(self) -> None:
        """_interruptible_sleep runs for at least the requested time."""
        import time as _time

        backend = MagicMock()
        poller = self._make_poller(backend)
        start = _time.monotonic()
        poller._interruptible_sleep(0.1)
        elapsed = _time.monotonic() - start
        assert elapsed >= 0.09
