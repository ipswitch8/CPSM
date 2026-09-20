# -*- coding: utf-8 -*-
"""
Measure Qt signal-delivery overhead for the StatusPoller -> UI hop.

Why this module exists separately
--------------------------------
``test_poll_cycle_leaks.py`` calls each slot *directly*
(``widget._on_poll_complete(statuses)``).  That measures the handler body but
completely bypasses Qt's signal machinery.

In production the hop is not direct.  ``StatusPoller`` is a ``QThread`` and its
consumers live on the GUI thread, so ``poll_complete``/``state_changed`` use a
**queued** connection: Qt marshals a copy of every argument into a
``QMetaCallEvent`` on the C++ heap, posts it, and the receiving thread consumes
it later.  That allocation is invisible to ``tracemalloc`` and happens ~28,800
times a day per signal.

Given that the live process's swap is concentrated in glibc arenas rather than
Python objects, the marshalling path is a first-class suspect and must be
measured on its own.

``poll_complete`` is declared ``Signal(list)`` and carries a list of
``PaneStatus`` dataclasses, so each emission marshals a container, not a scalar.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from typing import Any

import pytest
from PySide6.QtCore import QCoreApplication, QObject, Qt, QThread, Signal

from tests.perf.leak_harness import measure_leak
from tests.perf.test_poll_cycle_leaks import ITERATIONS, _statuses

pytestmark = [pytest.mark.ui, pytest.mark.perf]


class _Emitter(QObject):
    """Mirrors StatusPoller's two signals exactly."""

    state_changed = Signal(object)
    poll_complete = Signal(list)


class _Receiver(QObject):
    """Mirrors a UI consumer: keeps nothing, just counts."""

    def __init__(self) -> None:
        super().__init__()
        self.polls = 0
        self.states = 0

    def on_poll_complete(self, statuses: list[Any]) -> None:
        self.polls += 1

    def on_state_changed(self, status: Any) -> None:
        self.states += 1


class TestQueuedSignalMarshalling:
    """A queued connection marshals arguments onto the C++ heap.

    Emitter and receiver both sit on the GUI thread here, with the connection
    type forced to ``QueuedConnection``.  That reproduces the marshalling
    exactly while keeping delivery deterministic -- ``processEvents()`` drains
    the queue on demand, so each measured iteration is a complete emit+deliver
    round trip with nothing left in flight.
    """

    def test_measure_queued_poll_complete(self, qtbot: Any) -> None:
        emitter = _Emitter()
        receiver = _Receiver()
        emitter.poll_complete.connect(
            receiver.on_poll_complete, Qt.ConnectionType.QueuedConnection
        )
        statuses = _statuses()
        app = QCoreApplication.instance()

        def cycle(_i: int) -> None:
            emitter.poll_complete.emit(statuses)
            app.processEvents()

        cycle(0)
        assert receiver.polls > 0, "queued signal never delivered — path not exercised"

        report = measure_leak(
            "queued Signal(list) poll_complete", cycle, iterations=ITERATIONS
        )
        report.note = f"deliveries: {receiver.polls}"
        print("\n" + report.format())
        _record(report)

    def test_measure_queued_state_changed(self, qtbot: Any) -> None:
        """``state_changed`` fires once per pane whose state transitions."""
        emitter = _Emitter()
        receiver = _Receiver()
        emitter.state_changed.connect(
            receiver.on_state_changed, Qt.ConnectionType.QueuedConnection
        )
        status = _statuses()[0]
        app = QCoreApplication.instance()

        def cycle(_i: int) -> None:
            emitter.state_changed.emit(status)
            app.processEvents()

        cycle(0)
        assert receiver.states > 0, "queued signal never delivered — path not exercised"

        report = measure_leak(
            "queued Signal(object) state_changed", cycle, iterations=ITERATIONS
        )
        report.note = f"deliveries: {receiver.states}"
        print("\n" + report.format())
        _record(report)


class TestCrossThreadEmission:
    """The genuine production topology: QThread emitter, GUI-thread receiver.

    Slower and less deterministic than the forced-queued case above, so it runs
    fewer iterations; its job is to confirm the same-thread queued result holds
    when a real thread boundary (and Qt's automatic connection-type resolution)
    is involved.
    """

    def test_measure_cross_thread_poll_complete(self, qtbot: Any) -> None:
        statuses = _statuses()
        app = QCoreApplication.instance()

        class _Worker(QThread):
            poll_complete = Signal(list)

            def __init__(self) -> None:
                super().__init__()
                self._pending = 0

            def run(self) -> None:  # pragma: no cover - thread body
                while not self.isInterruptionRequested():
                    if self._pending > 0:
                        self.poll_complete.emit(statuses)
                        self._pending -= 1
                    else:
                        self.msleep(1)

        worker = _Worker()
        receiver = _Receiver()
        worker.poll_complete.connect(receiver.on_poll_complete)
        worker.start()
        try:
            def cycle(_i: int) -> None:
                target = receiver.polls + 1
                worker._pending += 1
                # Drain until this emission has been delivered.
                for _ in range(10000):
                    app.processEvents()
                    if receiver.polls >= target:
                        return
                    QThread.msleep(0)

            cycle(0)
            assert receiver.polls > 0, "cross-thread signal never delivered"

            iters = max(200, ITERATIONS // 4)
            report = measure_leak(
                "cross-thread Signal(list) poll_complete",
                cycle,
                iterations=iters,
                warmup=50,
            )
            report.note = f"deliveries: {receiver.polls}"
            print("\n" + report.format())
            _record(report)
        finally:
            worker.requestInterruption()
            worker.wait(5000)


_RESULTS_PATH = os.path.join(os.path.dirname(__file__), "leak_measurements.txt")


def _record(report: Any) -> None:
    with open(_RESULTS_PATH, "a", encoding="utf-8") as fh:
        fh.write(report.format() + "\n\n")
