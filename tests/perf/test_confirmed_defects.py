# -*- coding: utf-8 -*-
"""Pin the two defects the leak investigation actually confirmed.

Both are measurements rather than tight assertions -- native arena growth is
quantised and noisy, so asserting an exact byte rate would be flaky. What IS
asserted is the structural property that makes each a defect, which is stable:

  1. QGraphicsView.fitInView() is idempotent, therefore calling it once per
     3-second poll with unchanged inputs is pure waste. (It also leaks ~950 B
     per call on Qt 6.11 / PySide6 6.11 -- see docs/MEMORY-LEAK-INVESTIGATION.md
     -- but that is an upstream defect, so the assertion here is on the
     redundancy we control, not on the leak we do not.)

  2. layout_needs_reconcile() returns True for as long as it is handed a
     layout still carrying a monitor identifier shared by two attached
     displays. This is a CHARACTERISATION test, not a defect claim: an earlier
     version of this file asserted it caused a per-poll rebuild and a
     3-second config write, and that was WRONG -- it measured a probe that
     re-fed a stale layout. The real poll path feeds back
     _screen_map_widget._layout_data, which set_layout has already replaced
     with the reconciled result, so it converges after a single reconcile
     (1 emit over 501 polls). See the RETRACTION section of
     docs/MEMORY-LEAK-INVESTIGATION.md.

See docs/MEMORY-LEAK-INVESTIGATION.md for the full evidence.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from typing import Any

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGraphicsRectItem

from cpsm.data.schema import GeometryPct, Monitor, Pane, ScreenLayout, Viewport
from cpsm.services.layout_reconciler import (
    layout_needs_reconcile,
    reconcile_layout_with_monitors,
)
from cpsm.services.monitor_service import MonitorInfo
from cpsm.ui.widgets.screen_map import ScreenMapWidget

pytestmark = [pytest.mark.ui, pytest.mark.perf]


def _monitor(ident: str, idx: int) -> MonitorInfo:
    geo = (idx * 1920, 0, 1920, 1080)
    return MonitorInfo(
        identifier=ident,
        name=f"DP-{idx}",
        geometry=geo,
        available_geometry=geo,
        physical_size_mm=(598.0, 336.0),
        device_pixel_ratio=1.0,
        orientation="landscape",
        manufacturer="",
        model="",
        serial="",
        qt_index=idx,
    )


def _layout(mons: list[MonitorInfo]) -> ScreenLayout:
    ms = []
    for mi, info in enumerate(mons):
        vp = Viewport(
            id=f"vp-{mi}",
            geometry_pct=GeometryPct(x=0, y=0, w=100, h=100),
            tmux_layout="tiled",
            panes=[Pane(connection_id=f"conn-{mi}")],
        )
        ms.append(Monitor(identifier=info.identifier, monitor_index_hint=mi, viewports=[vp]))
    return ScreenLayout(id="layout-defect", name="layout-defect", monitors=ms)


class TestFitInViewIsRedundantWhenNothingChanged:
    """Justifies guarding the per-poll fitInView call."""

    def test_fit_in_view_is_idempotent(self, qtbot: Any) -> None:
        w = ScreenMapWidget()
        qtbot.addWidget(w)
        w.resize(640, 400)
        w.scene.addItem(QGraphicsRectItem(0, 0, 1920, 1080))
        br = w.scene.itemsBoundingRect()

        def transform() -> tuple[float, ...]:
            t = w._view.transform()
            return (t.m11(), t.m12(), t.m21(), t.m22(), t.m31(), t.m32())

        w._view.fitInView(br, Qt.AspectRatioMode.KeepAspectRatio)
        first = transform()
        for _ in range(12):
            w._view.fitInView(br, Qt.AspectRatioMode.KeepAspectRatio)
        later = transform()

        assert first == pytest.approx(later, abs=1e-12), (
            "fitInView is not idempotent — a guard that skips redundant calls "
            "would change rendering, so the fix must be reconsidered"
        )


class TestAmbiguousIdentifierReconcileConverges:
    """Characterise reconcile on matched (identical) displays.

    The point of these tests is that reconciliation CONVERGES. They exist to
    stop a future change from reintroducing a non-convergent repair, which
    would put the poll loop into a genuine per-poll rebuild.
    """

    def test_unique_identifiers_do_not_need_reconcile(self) -> None:
        mons = [
            _monitor("Chimei Innolux Corporation", 0),
            _monitor("ViewSonic Corporation-VX2757A-FHD-XVD254700640", 1),
            _monitor("ViewSonic Corporation-VX2757A-FHD-XVD254700618", 2),
        ]
        assert layout_needs_reconcile(_layout(mons), mons) is False

    def test_ambiguous_identifiers_request_reconcile(self) -> None:
        """Two identical panels reporting no EDID serial collide."""
        shared = "ViewSonic Corporation-VX2757A-FHD"
        mons = [
            _monitor("Chimei Innolux Corporation", 0),
            _monitor(shared, 1),
            _monitor(shared, 2),
        ]
        assert layout_needs_reconcile(_layout(mons), mons) is True

    def test_reconcile_converges_so_the_poll_loop_settles(self) -> None:
        """Reconciling a repaired layout must be a fixed point.

        This is the property that keeps the 3-second poll loop cheap. If a
        change made the repair non-convergent, every poll would rebuild the
        ScreenLayout and re-emit layout_reconciled (which MainWindow
        persists), turning a status refresh into a config write.
        """
        shared = "ViewSonic Corporation-VX2757A-FHD"
        mons = [
            _monitor("Chimei Innolux Corporation", 0),
            _monitor(shared, 1),
            _monitor(shared, 2),
        ]
        first = _layout(mons)
        repaired = reconcile_layout_with_monitors(first, mons)

        assert repaired != first, "expected the collision to actually be repaired"
        assert layout_needs_reconcile(repaired, mons) is False, (
            "repaired layout still requests reconciliation — the poll loop "
            "would rebuild and re-persist on every poll"
        )
        assert reconcile_layout_with_monitors(repaired, mons) == repaired, (
            "reconcile is not a fixed point"
        )
