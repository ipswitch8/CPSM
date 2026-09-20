# -*- coding: utf-8 -*-
"""
Distinguish a true leak from bounded cache warm-up.

Why a single delta is not enough
--------------------------------
``measure_leak`` reports one before/after difference.  glibc claims arena
memory from the OS in large chunks (64 MB / 128 MB here), so a short run either
straddles a chunk boundary or does not, and the per-iteration figure swings
wildly: the same 6-pane rebuild measured 622 B/iter over 2000 iterations and
1413 B/iter over 400, while a 3-pane rebuild measured 0.0.

That quantisation makes a single delta unable to answer the question that
actually matters:

    Does the native heap grow **without bound**, or does it rise once as
    caches populate and then flatten?

A leak is linear forever.  Cache warm-up is a step that plateaus.  Only a
growth *curve* over many checkpoints separates them, and the shape is robust to
chunking in a way that a lone delta is not.

This test samples the native heap at regular checkpoints and reports the curve,
plus the growth in the final third versus the first third — the discriminator.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import gc
from typing import Any

import pytest

from tests.perf.leak_harness import native_heap_bytes
from tests.perf.test_screen_map_attribution import _build

pytestmark = [pytest.mark.ui, pytest.mark.perf, pytest.mark.slow]

CHECKPOINTS = int(os.environ.get("CPSM_CURVE_CHECKPOINTS", "20"))
PER_CHECKPOINT = int(os.environ.get("CPSM_CURVE_PER_CHECKPOINT", "500"))


# Pump the Qt event loop every N calls. A measurement loop that never yields
# does not model an application: fitInView grows ~910 B/call unyielded and
# exactly 0 when the loop yields at all, and every "LINEAR (leak)" verdict this
# function produced before this change inherited that bias. See CORRECTION 8 in
# docs/MEMORY-LEAK-INVESTIGATION.md.
YIELD_EVERY = int(os.environ.get("CPSM_CURVE_YIELD_EVERY", "50"))


def _pump() -> None:
    from PySide6.QtCore import QCoreApplication

    app = QCoreApplication.instance()
    if app is not None:
        app.processEvents()


def _curve(label: str, call: Any, checkpoints: int, per_checkpoint: int) -> str:
    """Run *call* in blocks, sampling the native heap after each block.

    Yields to the Qt event loop every ``YIELD_EVERY`` calls so the curve
    describes application behaviour rather than benchmark behaviour.
    """
    # Warm up so one-time initialisation is not counted as growth.
    for i in range(300):
        call(i)
        if YIELD_EVERY and i % YIELD_EVERY == 0:
            _pump()
    for _ in range(3):
        gc.collect()

    samples: list[int] = []
    base = native_heap_bytes()
    n = 0
    for _c in range(checkpoints):
        for _ in range(per_checkpoint):
            call(n)
            if YIELD_EVERY and n % YIELD_EVERY == 0:
                _pump()
            n += 1
        for _ in range(3):
            gc.collect()
        samples.append(native_heap_bytes() - base)

    total = checkpoints * per_checkpoint
    third = max(1, checkpoints // 3)
    first_third = samples[third - 1]
    last_third = samples[-1] - samples[-third - 1] if checkpoints > third else 0

    lines = [
        f"--- growth curve: {label} ---",
        f"  total iterations     : {total}",
        f"  checkpoint size      : {per_checkpoint}",
        f"  yielded every        : {YIELD_EVERY or 'never (tight loop)'}",
        "  native heap growth (KB) per checkpoint:",
    ]
    for i, s in enumerate(samples, 1):
        lines.append(f"      after {i * per_checkpoint:6d} iters : {s / 1024:10.1f} KB")
    lines.append(f"  growth in first third  : {first_third / 1024:10.1f} KB")
    lines.append(f"  growth in last third   : {last_third / 1024:10.1f} KB")
    if last_third <= 0:
        shape = "PLATEAU (bounded cache warm-up, not a leak)"
    elif last_third >= first_third * 0.5:
        shape = "LINEAR (unbounded growth — genuine leak)"
    else:
        shape = "DECAYING (mostly warm-up, small residual)"
    lines.append(f"  SHAPE                  : {shape}")
    lines.append(
        f"  steady-state rate      : {last_third / max(1, third * per_checkpoint):10.2f} B/iter"
    )
    return "\n".join(lines)


class TestScreenMapGrowthShape:
    def test_growth_curve_screen_map_rebuild(self, qtbot: Any) -> None:
        widget, layout, monitors = _build(qtbot, 2)  # 6 panes across 3 monitors

        def cycle(_i: int) -> None:
            widget.set_layout(layout, monitors)

        out = _curve("ScreenMapWidget.set_layout (6 panes)", cycle, CHECKPOINTS, PER_CHECKPOINT)
        print("\n" + out)
        with open(
            os.path.join(os.path.dirname(__file__), "leak_measurements.txt"),
            "a",
            encoding="utf-8",
        ) as fh:
            fh.write(out + "\n\n")


class TestFontProbeCausality:
    """Is the QFontMetricsF probe loop the *cause* of the linear growth?

    The earlier single-delta stub test reported 0.0 B/iter, but 400 iterations
    sits inside the warm-up plateau, where the unstubbed path also reads 0.0 —
    so that result distinguished nothing.  Only a full-length curve, compared
    against the unstubbed curve over the same range, is evidence.
    """

    def test_growth_curve_with_font_metrics_stubbed(
        self, qtbot: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from PySide6 import QtGui

        widget, layout, monitors = _build(qtbot, 2)

        class _StubMetrics:
            def __init__(self, _font: Any) -> None:
                pass

            def horizontalAdvance(self, text: str) -> float:
                return float(len(text) * 6)

            def height(self) -> float:
                return 12.0

        monkeypatch.setattr(QtGui, "QFontMetricsF", _StubMetrics)

        def cycle(_i: int) -> None:
            widget.set_layout(layout, monitors)

        out = _curve(
            "ScreenMapWidget.set_layout (6 panes, QFontMetricsF stubbed)",
            cycle,
            CHECKPOINTS,
            PER_CHECKPOINT,
        )
        print("\n" + out)
        with open(
            os.path.join(os.path.dirname(__file__), "leak_measurements.txt"),
            "a",
            encoding="utf-8",
        ) as fh:
            fh.write(out + "\n\n")
