# -*- coding: utf-8 -*-
"""
Attribute the screen-map rebuild leak to a specific construct.

Established by the other modules in this package:
  * ``ScreenMapWidget.set_layout`` grows the glibc heap ~622 B per rebuild
    while leaking **zero** Python objects and **zero** live Qt instances.
  * Every other StatusPoller consumer (action bars, queued/cross-thread signal
    marshalling) is clean.

Zero object growth alongside real native growth means the memory is not held by
anything the application still references -- it is allocator/cache memory that
Qt claimed and never returned.  ``_draw_pane`` contains the obvious candidate:

    probe = QFont()
    for size in range(28, 5, -1):
        probe.setPointSize(size)
        fm = QFontMetricsF(probe)          # <-- up to 23 per pane, per rebuild
        ...

Each ``QFontMetricsF`` construction can populate Qt's font-engine and glyph
caches.  At 6 panes that is up to 138 constructions every 3 seconds; the caches
are keyed by (family, size, ...) and are not bounded by anything the app owns.

These tests answer two questions with numbers:
  1. Does the native growth scale with pane count?  (If yes, a busy real-world
     layout leaks proportionally faster than the 6-pane baseline.)
  2. Is the font-probe loop *causal*?  Replacing ``QFontMetricsF`` with a stub
     should collapse the growth if so.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from typing import Any

import pytest
from PySide6 import QtGui

from cpsm.data.schema import (
    ClaudeLocalConnection,
    GeometryPct,
    Monitor,
    Pane,
    ScreenLayout,
    Viewport,
)
from cpsm.ui.widgets.screen_map import ScreenMapWidget
from tests.perf.leak_harness import measure_leak
from tests.perf.test_poll_cycle_leaks import _monitors, _record

pytestmark = [pytest.mark.ui, pytest.mark.perf]

SCALE_ITERATIONS = int(os.environ.get("CPSM_LEAK_SCALE_ITERATIONS", "400"))


def _layout_with_panes(monitors: list[Any], panes_per_viewport: int) -> ScreenLayout:
    """One viewport per monitor, *panes_per_viewport* panes in each."""
    mons = []
    for mi, info in enumerate(monitors):
        vp = Viewport(
            id=f"vp-{mi}",
            geometry_pct=GeometryPct(x=0, y=0, w=100, h=100),
            tmux_layout="tiled",
            panes=[
                Pane(connection_id=f"conn-{mi}-{p}") for p in range(panes_per_viewport)
            ],
        )
        mons.append(
            Monitor(identifier=info.identifier, monitor_index_hint=mi, viewports=[vp])
        )
    return ScreenLayout(id="layout-scale", name="layout-scale", monitors=mons)


def _conns_for(monitors: list[Any], panes_per_viewport: int) -> dict[str, Any]:
    out = {}
    for mi in range(len(monitors)):
        for p in range(panes_per_viewport):
            cid = f"conn-{mi}-{p}"
            out[cid] = ClaudeLocalConnection(
                id=cid,
                name=f"connection-{mi}-{p}",
                launch_profile="claude-local",
                project_folder=f"~/proj-{mi}-{p}",
                claude_options="--resume",
            )
    return out


def _build(qtbot: Any, panes_per_viewport: int) -> tuple[Any, ScreenLayout, list[Any]]:
    monitors = _monitors()
    layout = _layout_with_panes(monitors, panes_per_viewport)
    conns = _conns_for(monitors, panes_per_viewport)
    widget = ScreenMapWidget()
    qtbot.addWidget(widget)
    widget._connection_lookup = lambda cid: conns.get(cid)
    widget._status_lookup = lambda *a, **k: "connected"
    return widget, layout, monitors


class TestLeakScalesWithPaneCount:
    """If the leak tracks pane count, the font-probe loop is implicated."""

    @pytest.mark.parametrize("panes", [1, 2, 4, 8])
    def test_measure_scaling(self, qtbot: Any, panes: int) -> None:
        widget, layout, monitors = _build(qtbot, panes)

        def cycle(_i: int) -> None:
            widget.set_layout(layout, monitors)

        cycle(0)
        items = len(widget.scene.items())
        assert items > panes, f"only {items} scene items for {panes} panes/viewport"

        total_panes = panes * len(monitors)
        report = measure_leak(
            f"set_layout @ {total_panes} panes", cycle, iterations=SCALE_ITERATIONS
        )
        report.note = f"{total_panes} panes, {items} scene items"
        print("\n" + report.format())
        print(
            f"  >> native bytes per pane per rebuild: "
            f"{report.native_bytes_per_iter / total_panes:8.1f}"
        )
        _record(report)


class TestFontProbeLoopIsCausal:
    """Replace QFontMetricsF with a stub; if growth collapses, it is the cause."""

    def test_measure_with_font_metrics_stubbed(
        self, qtbot: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        widget, layout, monitors = _build(qtbot, 2)

        class _StubMetrics:
            """Fixed metrics — constructs no Qt font engine."""

            def __init__(self, _font: Any) -> None:
                pass

            def horizontalAdvance(self, text: str) -> float:
                return float(len(text) * 6)

            def height(self) -> float:
                return 12.0

        # _draw_pane does `from PySide6.QtGui import QFontMetricsF` at call
        # time, so patching the module attribute takes effect.
        monkeypatch.setattr(QtGui, "QFontMetricsF", _StubMetrics)

        def cycle(_i: int) -> None:
            widget.set_layout(layout, monitors)

        cycle(0)
        report = measure_leak(
            "set_layout @ 6 panes (QFontMetricsF stubbed)",
            cycle,
            iterations=SCALE_ITERATIONS,
        )
        report.note = "font-metric probing disabled"
        print("\n" + report.format())
        _record(report)
