# -*- coding: utf-8 -*-
"""Measurements for two paths the other perf modules do not cover.

Both were originally run as throwaway probes, and their numbers were quoted in
docs/MEMORY-LEAK-INVESTIGATION.md as "(measured)" with no committed artifact
backing them. A gate review caught that. They are proper tests now, and they
append to leak_measurements.txt like every other measurement here.

1. ``_redraw_multi`` -- multi-group overlay mode. Unlike ``_redraw`` it builds a
   THROW-AWAY, PARENTLESS ``QGraphicsScene`` per redraw (screen_map.py:2117)
   solely to obtain a pane registry, then discards it. A parentless scene owns
   the items added to it and its lifetime depends on the Python wrapper being
   collected -- a classic PySide6 leak shape, so it needs measuring rather than
   assuming.

2. Dead-pane ``capture_pane`` churn on the StatusPoller THREAD. ``_process_poll``
   calls ``capture_pane(pid, lines=200)`` for every pane that died with a
   non-zero status, every poll, because tmux ``remain-on-exit`` keeps dead panes
   listed until reaped. That is the largest allocation the poller thread makes,
   and it lands in a per-thread glibc arena -- which is where the live process's
   growth was localized. Worth ruling in or out explicitly.

   The poller is a producer and the GUI thread is its consumer, so emissions are
   COUNTED AGAINST DELIVERIES. Without that control a fast poll interval simply
   backs up the queued-event queue and the resulting growth reads as a leak: an
   earlier version of this measurement "found" 1.8 GB in 183 s that way.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import gc
import time
from typing import Any

import pytest
from PySide6.QtCore import QCoreApplication

from cpsm.data.schema import (
    ClaudeLocalConnection,
    GeometryPct,
    Monitor,
    Pane as SchemaPane,
    ScreenLayout,
    Viewport,
)
from cpsm.platform.base import Pane
from cpsm.services.monitor_service import MonitorInfo
from cpsm.ui.widgets.screen_map import ScreenMapWidget
from cpsm.workers.status_poller import StatusPoller
from tests.perf.leak_harness import measure_leak, native_heap_bytes

pytestmark = [pytest.mark.ui, pytest.mark.perf, pytest.mark.slow]

_RESULTS = os.path.join(os.path.dirname(__file__), "leak_measurements.txt")


def _record(text: str) -> None:
    print("\n" + text)
    with open(_RESULTS, "a", encoding="utf-8") as fh:
        fh.write(text + "\n\n")


def _monitors() -> list[MonitorInfo]:
    specs = [("mon-a", (0, 0, 1920, 1080)), ("mon-b", (1920, 0, 1920, 1080)),
             ("mon-c", (3840, 0, 1920, 1080))]
    return [
        MonitorInfo(identifier=i, name=f"DP-{k}", geometry=g, available_geometry=g,
                    physical_size_mm=(598.0, 336.0), device_pixel_ratio=1.0,
                    orientation="landscape", manufacturer="", model="", serial="",
                    qt_index=k)
        for k, (i, g) in enumerate(specs)
    ]


def _group_layout(gid: str, mons: list[MonitorInfo]) -> ScreenLayout:
    ms = []
    for mi, info in enumerate(mons):
        vp = Viewport(
            id=f"vp-{gid}-{mi}", geometry_pct=GeometryPct(x=0, y=0, w=100, h=100),
            tmux_layout="tiled",
            panes=[SchemaPane(connection_id=f"conn-{gid}-{mi}-{p}") for p in range(2)],
        )
        ms.append(Monitor(identifier=info.identifier, monitor_index_hint=mi, viewports=[vp]))
    return ScreenLayout(id=f"layout-{gid}", name=f"layout-{gid}", monitors=ms)


class TestMultiGroupOverlayRedraw:
    """Does the throw-away parentless scene in _redraw_multi leak?"""

    def test_measure_redraw_multi(self, qtbot: Any) -> None:
        mons = _monitors()
        gids = ["group-alpha", "group-beta", "group-gamma"]
        layouts = {g: _group_layout(g, mons) for g in gids}
        conns = {
            f"conn-{g}-{mi}-{p}": ClaudeLocalConnection(
                id=f"conn-{g}-{mi}-{p}", name=f"c-{g}-{mi}-{p}",
                launch_profile="claude-local", project_folder=f"~/{g}{mi}{p}",
                claude_options="--resume")
            for g in gids for mi in range(3) for p in range(2)
        }

        w = ScreenMapWidget()
        qtbot.addWidget(w)
        w._monitors = mons
        w._connection_lookup = lambda cid: conns.get(cid)
        w._status_lookup = lambda *a, **k: "connected"
        w.set_visible_groups(gids, layouts)
        w.set_edit_target(gids[0])

        def cycle(_i: int) -> None:
            w._redraw_multi()

        cycle(0)
        items = len(w.scene.items())
        assert items > len(gids), (
            f"overlay rendered only {items} scene items — path not exercised, "
            f"so a clean verdict would be false"
        )

        report = measure_leak("_redraw_multi (3 visible groups)", cycle, iterations=2000)
        report.note = f"{len(gids)} groups, {items} scene items"
        _record(report.format())

        # The structural claim: the throw-away scene must not strand live Qt
        # instances. Byte rates are noisy; instance counts are not.
        assert not report.leaks_qt_objects, (
            f"_redraw_multi stranded {report.qt_objects_per_iter:.3f} live Qt "
            f"objects per redraw: {report.qt_type_histogram}"
        )


class _FakeSession:
    def __init__(self, name: str) -> None:
        self.name = name
        self.attached = True


# A realistic 200-line scrollback: what tmux capture-pane returns for a pane
# that died after an ssh session. Module level because a class-body genexpr
# cannot see names defined in that same class body.
_CAPTURE_LINE = "2026-08-23 10:15:42 ssh: connect to 192.0.2.44 port 22: timed out"
_CAPTURE = "\n".join(f"{_CAPTURE_LINE} [{i}]" for i in range(200))


class _DeadPaneBackend:
    """Backend whose dead panes force capture_pane() on every poll."""

    CAPTURE = _CAPTURE

    def __init__(self, live: int, dead: int) -> None:
        self._live, self._dead = live, dead
        self.captures = 0

    def list_panes(self) -> list[Pane]:
        # Pane is a frozen dataclass and pid/width/height are REQUIRED. Omitting
        # them raises TypeError inside list_panes -- which StatusPoller.run()
        # swallows via `except Exception: panes = []`, silently polling an empty
        # list forever. An earlier version of this test did exactly that and
        # "measured" a poller doing no work at all.
        out = [
            Pane(id=f"%{i}", session=f"cpsm-group-g-mon-{i % 3}", window_index=0,
                 pane_index=i, pid=1000 + i, dead=False, current_command="ssh",
                 width=80, height=24)
            for i in range(self._live)
        ]
        for d in range(self._dead):
            out.append(
                Pane(id=f"%d{d}", session=f"cpsm-group-g-mon-{d % 3}", window_index=0,
                     pane_index=100 + d, pid=None, dead=True, current_command="",
                     width=80, height=24, dead_status=255)
            )
        return out

    def list_sessions(self) -> list[_FakeSession]:
        return [_FakeSession(f"cpsm-group-g-mon-{i}") for i in range(3)]

    def capture_pane(self, pane_id: str, lines: int = 200) -> str:
        self.captures += 1
        return self.CAPTURE


class TestDeadPaneCaptureChurn:
    """Does per-poll capture_pane churn on the poller thread grow the heap?"""

    @pytest.mark.parametrize("dead", [0, 4])
    def test_measure_dead_pane_churn(self, qtbot: Any, dead: int) -> None:
        app = QCoreApplication.instance()
        backend = _DeadPaneBackend(live=6, dead=dead)
        poller = StatusPoller(backend, interval_ms=20)
        delivered = [0]
        poller.poll_complete.connect(lambda s: delivered.__setitem__(0, delivered[0] + 1))
        poller.state_changed.connect(lambda s: None)

        poller.start()
        try:
            time.sleep(2.0)
            for _ in range(3):
                gc.collect()
            app.processEvents()

            base = native_heap_bytes()
            d0 = delivered[0]
            t0 = time.time()
            while time.time() - t0 < 20.0:
                app.processEvents()
                time.sleep(0.01)
            for _ in range(3):
                gc.collect()
            app.processEvents()

            grown = native_heap_bytes() - base
            polls = delivered[0] - d0
            captures = backend.captures
            snapshot_len = len(poller.last_snapshot)
        finally:
            poller.stop()
            poller.wait(5000)

        assert polls > 100, f"only {polls} polls delivered — poller not exercised"
        # StatusPoller.run() swallows backend exceptions and falls back to an
        # empty pane list, so "it ran" is not the same as "it saw any panes".
        assert snapshot_len > 0, (
            "poller produced an empty snapshot — list_panes() raised and was "
            "swallowed, so this measured an idle loop, not the poll path"
        )
        if dead:
            assert captures > 0, "dead panes present but capture_pane never called"
        per = grown / polls
        text = (
            f"--- StatusPoller thread, {dead} dead panes (200-line capture each) ---\n"
            f"  polls delivered  : {polls}\n"
            f"  panes per snapshot: {snapshot_len}\n"
            f"  capture_pane calls: {captures}\n"
            f"  native growth    : {grown} B\n"
            f"  per poll         : {per:.0f} B\n"
            f"  => at 3s interval: {per * (86400 / 3.0) / (1024 * 1024):.1f} MB/day\n"
            f"  backlog control  : emissions counted against deliveries"
        )
        _record(text)
