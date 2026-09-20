# -*- coding: utf-8 -*-
"""
Measure per-poll-cycle memory growth for every StatusPoller consumer.

Background
----------
A live CPSM GUI session (PID 377627, 6 days uptime) accumulated ~2 GB of
anonymous memory: 2680 MB mapped, only 235 MB resident, the remainder paged to
swap.  That is the signature of memory that is written once and then never
touched again but is still referenced — i.e. leaked, not cached.

``StatusPoller`` polls every 3000 ms, so 6 days is ~172,800 cycles.  2 GB over
172,800 cycles is ~12 KB per cycle.  These tests drive each ``poll_complete``
consumer directly and report what a single cycle actually costs.

Suspects under test (each must be independently confirmable *or exonerable*):
  1. ``ScreenMapWidget.set_layout``   — main_window._on_status_poll_complete
     rebuilds the whole QGraphicsScene on every poll.
  2. ``ActiveSessionsWidget._on_poll_complete`` — re-runs ``setIndexWidget``
     for every row on every poll.
  3. ``MainWindow._on_status_poll_complete`` — the real production handler,
     end to end.

These are measurements, not pass/fail assertions about a specific culprit; the
only hard assertion is that the harness itself is sound (see
``test_harness_detects_a_known_leak`` / ``test_harness_reports_clean_for_a_non_leaking_path``).
Attribution lives in the printed report.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from datetime import UTC, datetime
from typing import Any

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QGraphicsRectItem

from cpsm.data.schema import (
    ClaudeLocalConnection,
    CpsmDocument,
    GeometryPct,
    Monitor,
    Pane,
    ScreenLayout,
    Viewport,
)
from cpsm.services.monitor_service import MonitorInfo
from cpsm.ui.widgets.active_sessions import ActiveSessionsWidget
from cpsm.ui.widgets.screen_map import ScreenMapWidget
from cpsm.workers.status_poller import PaneState, PaneStatus
from tests.perf.leak_harness import measure_leak

pytestmark = [pytest.mark.ui, pytest.mark.perf]

# Enough iterations to separate a real linear leak from noise, while keeping
# each path well under the 5-minute budget for the whole module.
ITERATIONS = int(os.environ.get("CPSM_LEAK_ITERATIONS", "2000"))


# ---------------------------------------------------------------------------
# Fixtures modelling a realistic desk: 3 monitors, several panes
# ---------------------------------------------------------------------------


def _monitors() -> list[MonitorInfo]:
    """Three monitors, matching the reporting user's actual setup."""
    specs = [
        ("Chimei Innolux Corporation", "DP-4", (0, 0, 1920, 1080)),
        ("ViewSonic Corporation-VX2757A-FHD-XVD254700640", "DP-0", (3840, 0, 1920, 1080)),
        ("ViewSonic Corporation-VX2757A-FHD-XVD254700618", "DP-2", (1920, 0, 1920, 1080)),
    ]
    return [
        MonitorInfo(
            identifier=ident,
            name=name,
            geometry=geo,
            available_geometry=geo,
            physical_size_mm=(598.0, 336.0),
            device_pixel_ratio=1.0,
            orientation="landscape",
            manufacturer="",
            model="",
            serial="",
            qt_index=i,
        )
        for i, (ident, name, geo) in enumerate(specs)
    ]


def _layout(monitors: list[MonitorInfo]) -> ScreenLayout:
    """One viewport per monitor, two panes each."""
    mons = []
    for mi, info in enumerate(monitors):
        vp = Viewport(
            id=f"vp-{mi}",
            geometry_pct=GeometryPct(x=0, y=0, w=100, h=100),
            tmux_layout="tiled",
            panes=[Pane(connection_id=f"conn-{mi}-{p}") for p in range(2)],
        )
        mons.append(Monitor(identifier=info.identifier, monitor_index_hint=mi, viewports=[vp]))
    return ScreenLayout(id="layout-perf", name="layout-perf", monitors=mons)


def _connections() -> list[ClaudeLocalConnection]:
    return [
        ClaudeLocalConnection(
            id=f"conn-{m}-{p}",
            name=f"conn-{m}-{p}",
            launch_profile="claude-local",
            project_folder=f"~/proj-{m}-{p}",
            claude_options="--resume",
        )
        for m in range(3)
        for p in range(2)
    ]


def _statuses() -> list[PaneStatus]:
    """A poll snapshot covering every pane, as the poller would emit."""
    now = datetime.now(tz=UTC)
    out = []
    for m in range(3):
        for p in range(2):
            out.append(
                PaneStatus(
                    pane_id=f"%{m}{p}",
                    session=f"cpsm-group-g-mon-{m}",
                    state=PaneState.CONNECTED,
                    last_seen=now,
                    exit_code=None,
                    last_output_tail=None,
                    window_index=0,
                    pane_index=p,
                    current_command="ssh",
                    attached=True,
                    start_command=f"bash /tmp/cpsm-launcher-conn-{m}-{p}.sh",
                )
            )
    return out


class _FakePoller(QObject):
    """Stands in for StatusPoller: same two signals, no thread."""

    state_changed = Signal(object)
    poll_complete = Signal(list)

    def __init__(self) -> None:
        super().__init__()
        self.last_snapshot: list[PaneStatus] = []


class _FakeMonitorService:
    def __init__(self, monitors: list[MonitorInfo]) -> None:
        self._monitors = monitors

    def snapshot(self) -> list[MonitorInfo]:
        return list(self._monitors)


class _FakeSessionService:
    """Records calls so the test can prove the handler really ran."""

    def __init__(self) -> None:
        self.cleanup_calls = 0

    def cleanup_dead_panes(self, statuses: list[PaneStatus]) -> None:
        self.cleanup_calls += 1


class _FakeServices:
    """Minimal stand-in for the services container MainWindow reads.

    Without this, ``_services`` is None, ``_query_live_monitors()`` returns []
    and ``cleanup_dead_panes`` is skipped — which silently reduces the
    measured handler to a no-op and produces a false exoneration.
    """

    def __init__(self, monitors: list[MonitorInfo], statuses: list[PaneStatus]) -> None:
        self.monitor_service = _FakeMonitorService(monitors)
        self.session = _FakeSessionService()
        poller = _FakePoller()
        poller.last_snapshot = statuses
        self.status_poller = poller


# ---------------------------------------------------------------------------
# Harness self-validation — these are the only hard assertions.
#
# A measurement tool that cannot fail is worthless.  Before trusting any
# verdict about CPSM's code, prove the harness reports LEAKS for a path that
# provably leaks and clean for one that provably does not.
# ---------------------------------------------------------------------------


class TestHarnessIsSound:
    def test_harness_detects_a_known_leak(self, qtbot: Any) -> None:
        """A deliberately-retained QGraphicsRectItem per call must register."""
        widget = ScreenMapWidget()
        qtbot.addWidget(widget)
        sink: list[QGraphicsRectItem] = []

        def leaky(_i: int) -> None:
            item = QGraphicsRectItem(0, 0, 10, 10)
            widget.scene.addItem(item)
            sink.append(item)  # never released

        report = measure_leak("known-leak", leaky, iterations=500, warmup=50)
        print("\n" + report.format())

        assert report.leaks, "harness failed to detect a deliberate leak"
        assert report.qt_objects_per_iter >= 0.9, (
            f"expected ~1 leaked Qt object per iteration, got {report.qt_objects_per_iter:.3f}"
        )
        assert "QGraphicsRectItem" in report.qt_type_histogram

    def test_harness_reports_clean_for_a_non_leaking_path(self, qtbot: Any) -> None:
        """Creating and dropping an item each call must NOT register."""
        widget = ScreenMapWidget()
        qtbot.addWidget(widget)

        def clean(_i: int) -> None:
            item = QGraphicsRectItem(0, 0, 10, 10)
            widget.scene.addItem(item)
            widget.scene.removeItem(item)
            del item

        report = measure_leak("known-clean", clean, iterations=500, warmup=50)
        print("\n" + report.format())

        assert not report.leaks_qt_objects, (
            f"harness reported a Qt leak on a clean path "
            f"({report.qt_objects_per_iter:.3f}/iter) — false positive"
        )


# ---------------------------------------------------------------------------
# The three suspects
# ---------------------------------------------------------------------------


class TestPollCycleSuspects:
    """Measure each StatusPoller consumer.

    Every test asserts the path was *actually exercised* before reporting.
    A measurement that silently degrades to a no-op reads as "clean" and is
    worse than no measurement at all — that exact false exoneration happened
    during development when MainWindow loaded an ``empty`` layout.
    """

    def test_measure_screen_map_set_layout(self, qtbot: Any) -> None:
        """Suspect 1: full QGraphicsScene rebuild, once per poll."""
        monitors = _monitors()
        layout = _layout(monitors)
        conns = {c.id: c for c in _connections()}

        widget = ScreenMapWidget()
        qtbot.addWidget(widget)
        widget._connection_lookup = lambda cid: conns.get(cid)
        widget._status_lookup = lambda *a, **k: "connected"

        def cycle(_i: int) -> None:
            widget.set_layout(layout, monitors)

        cycle(0)
        item_count = len(widget.scene.items())
        assert item_count > 10, (
            f"screen map only rendered {item_count} scene items — the rebuild "
            f"path is not being exercised, so a 'clean' verdict would be false"
        )

        report = measure_leak("ScreenMapWidget.set_layout", cycle, iterations=ITERATIONS)
        report.note = f"scene items per rebuild: {item_count}"
        print("\n" + report.format())
        _record(report)

    def test_measure_active_sessions_poll_complete(self, qtbot: Any) -> None:
        """Suspect 2: setIndexWidget re-installed for every row, every poll."""
        poller = _FakePoller()
        widget = ActiveSessionsWidget(poller)
        qtbot.addWidget(widget)
        statuses = _statuses()

        def cycle(_i: int) -> None:
            widget._on_poll_complete(statuses)

        cycle(0)
        row_count = len(widget.model.rows)
        assert row_count == len(statuses), (
            f"expected {len(statuses)} model rows, got {row_count} — the "
            f"action-bar path is not being exercised"
        )

        report = measure_leak(
            "ActiveSessionsWidget._on_poll_complete", cycle, iterations=ITERATIONS
        )
        report.note = f"model rows per poll: {row_count}"
        print("\n" + report.format())
        _record(report)

    def test_measure_main_window_status_poll_complete(self, qtbot: Any) -> None:
        """Suspect 3: the real production handler, end to end.

        The handler early-outs unless ``_screen_map_widget._layout_data`` is
        truthy and ``_services`` is wired, so both are set up here to match a
        real running session.
        """
        from cpsm.ui.main_window import MainWindow

        monitors = _monitors()
        layout = _layout(monitors)
        statuses = _statuses()
        doc = CpsmDocument(connections=_connections(), screen_layouts=[layout])

        win = MainWindow(document=doc)
        qtbot.addWidget(win)

        services = _FakeServices(monitors, statuses)
        win._services = services
        win._screen_map_widget.set_layout(layout, monitors)

        def cycle(_i: int) -> None:
            win._on_status_poll_complete(statuses)

        cycle(0)
        item_count = len(win._screen_map_widget.scene.items())
        assert win._screen_map_widget._layout_data, "layout_data empty — handler early-outs"
        assert item_count > 10, (
            f"screen map only rendered {item_count} scene items — handler "
            f"early-outs, so a 'clean' verdict would be false"
        )
        assert services.session.cleanup_calls > 0, "cleanup_dead_panes never reached"

        report = measure_leak("MainWindow._on_status_poll_complete", cycle, iterations=ITERATIONS)
        report.note = f"scene items: {item_count}, cleanup calls: {services.session.cleanup_calls}"
        print("\n" + report.format())
        _record(report)


# ---------------------------------------------------------------------------
# Results artifact
# ---------------------------------------------------------------------------

_RESULTS_PATH = os.path.join(os.path.dirname(__file__), "leak_measurements.txt")


def _record(report: Any) -> None:
    """Append a report to the committed results artifact."""
    with open(_RESULTS_PATH, "a", encoding="utf-8") as fh:
        fh.write(report.format() + "\n\n")
