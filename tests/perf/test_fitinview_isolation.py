# -*- coding: utf-8 -*-
"""Isolate the confirmed leak to QGraphicsView.fitInView() itself.

This is the primary evidence for the investigation's one confirmed defect, so
it belongs in a committed test rather than an ad-hoc probe. (It originally was
an ad-hoc probe under .claude/, which is gitignored — meaning the headline
number had no reproducible backing. A gate review caught the same problem on
two lesser numbers; this is the same fix applied to the important one.)

`_redraw` performs, once per 3-second status poll:

    self._view.fitInView(self._scene.itemsBoundingRect(), KeepAspectRatio)

That is two operations, and the leak had to be attributed to one of them. Each
is measured separately here, plus `setTransform` as a control for "applying a
view transform" in general:

  * itemsBoundingRect()      — computing the bounds
  * fitInView(FIXED rect)    — fitting, with no bounds computation involved
  * fitInView(itemsBoundingRect()) — the combination as shipped
  * setTransform(current)    — applying a transform, unrelated to fitInView

Growth-curve shape is the discriminator, not a single delta: glibc claims arena
memory in large chunks, so one before/after difference is dominated by whether
the run happened to straddle a chunk boundary. See test_growth_curve.py.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import gc
from typing import Any

import pytest
from PySide6.QtCore import QRectF, Qt
from PySide6.QtWidgets import QGraphicsRectItem

from cpsm.ui.widgets.screen_map import ScreenMapWidget
from tests.perf.leak_harness import malloc_trim, native_heap_bytes

pytestmark = [pytest.mark.ui, pytest.mark.perf, pytest.mark.slow]

CHECKPOINTS = int(os.environ.get("CPSM_FIT_CHECKPOINTS", "16"))
PER_CHECKPOINT = int(os.environ.get("CPSM_FIT_PER_CHECKPOINT", "500"))

_RESULTS = os.path.join(os.path.dirname(__file__), "leak_measurements.txt")


def _curve(label: str, call: Any) -> tuple[str, float]:
    """Return (report text, steady-state B/call)."""
    for _ in range(300):
        call()
    for _ in range(3):
        gc.collect()
    # Return any free arena space to the OS before establishing the baseline.
    #
    # Without this the measurement depends on whatever ran EARLIER in the
    # process. glibc keeps freed memory in its arena rather than returning it,
    # so a test module that allocated and freed a lot beforehand leaves space
    # the loop below can satisfy its allocations from -- the curve then reports
    # PLATEAU and the "this loop grows" assertions fail, describing the
    # allocator's history rather than the call under test.
    #
    # Observed concretely: tests/perf/ alone passes 31/31, and this module
    # alone passes, but running tests/lint/ first flipped
    # test_fit_in_view_as_shipped_leaks_at_same_rate to fail. A measurement
    # that changes with what preceded it is not measuring its subject.
    malloc_trim()
    base = native_heap_bytes()
    samples: list[int] = []
    for _c in range(CHECKPOINTS):
        for _ in range(PER_CHECKPOINT):
            call()
        for _ in range(3):
            gc.collect()
        samples.append(native_heap_bytes() - base)

    third = max(1, CHECKPOINTS // 3)
    first = samples[third - 1]
    last = samples[-1] - samples[-third - 1]
    rate = last / (third * PER_CHECKPOINT)
    shape = "LINEAR (leak)" if last > 0 and last >= first * 0.5 else "PLATEAU (clean)"
    text = (
        f"--- fitInView isolation: {label} ---\n"
        f"  calls            : {CHECKPOINTS * PER_CHECKPOINT}\n"
        f"  total growth     : {samples[-1] / 1024:.1f} KB\n"
        f"  first third      : {first / 1024:.1f} KB\n"
        f"  last third       : {last / 1024:.1f} KB\n"
        f"  steady rate      : {rate:.2f} B/call\n"
        f"  => at 1 call/3s  : {rate * (86400 / 3.0) / (1024 * 1024):.1f} MB/day\n"
        f"  SHAPE            : {shape}"
    )
    print("\n" + text)
    with open(_RESULTS, "a", encoding="utf-8") as fh:
        fh.write(text + "\n\n")
    return text, rate


@pytest.fixture()
def view(qtbot: Any) -> ScreenMapWidget:
    w = ScreenMapWidget()
    qtbot.addWidget(w)
    w.resize(640, 400)
    w.scene.addItem(QGraphicsRectItem(0, 0, 1920, 1080))
    return w


class TestFitInViewIsolation:
    """Attribute the per-poll leak to fitInView rather than its argument."""

    def test_items_bounding_rect_alone_is_clean(self, view: ScreenMapWidget) -> None:
        _, rate = _curve("itemsBoundingRect() only", lambda: view.scene.itemsBoundingRect())
        assert rate <= 64.0, (
            f"itemsBoundingRect() grew {rate:.1f} B/call — the leak would not be "
            f"attributable to fitInView alone"
        )

    def test_set_transform_alone_is_clean(self, view: ScreenMapWidget) -> None:
        """Control: applying a view transform is not inherently leaky."""
        _, rate = _curve(
            "setTransform(current) only",
            lambda: view._view.setTransform(view._view.transform()),
        )
        assert rate <= 64.0, f"setTransform grew {rate:.1f} B/call"

    def test_fit_in_view_with_fixed_rect_leaks(self, view: ScreenMapWidget) -> None:
        """The defect: fitInView leaks even with a pre-built, constant rect.

        Documents upstream Qt 6.11 / PySide6 6.11 behaviour. If a future Qt
        fixes it this test will fail — which is the correct signal, because the
        guard added in cpsm/ui/widgets/screen_map.py could then be revisited.
        """
        fixed = QRectF(0, 0, 1920, 1080)
        _, rate = _curve(
            "fitInView(FIXED rect) only",
            lambda: view._view.fitInView(fixed, Qt.AspectRatioMode.KeepAspectRatio),
        )
        assert rate > 100.0, (
            f"fitInView grew only {rate:.1f} B/call — the upstream leak this "
            f"project guards against may have been fixed in Qt; revisit "
            f"ScreenMapWidget._fit_view_if_needed and docs/MEMORY-LEAK-INVESTIGATION.md"
        )

    def test_fit_in_view_as_shipped_leaks_at_same_rate(self, view: ScreenMapWidget) -> None:
        """The combination as `_redraw` calls it."""
        _, rate = _curve(
            "fitInView(itemsBoundingRect()) — as shipped",
            lambda: view._view.fitInView(
                view.scene.itemsBoundingRect(), Qt.AspectRatioMode.KeepAspectRatio
            ),
        )
        assert rate > 100.0, f"as-shipped call grew only {rate:.1f} B/call"


class TestTightLoopGrowthIsAHarnessArtifact:
    """The tight-loop rate above is NOT an application leak rate.

    Two real `cpsm gui` instances on HEAD contradicted the prediction: one grew
    +24 KB and then went FLAT from 900s through 1800s (~600 polls), where
    ~811 B/call implies ~486 KB and still climbing.

    The cause is the loop, not the call. Growth only occurs when thousands of
    calls run with NO yielding in between. Any yield at all removes it, and a
    real application yields constantly -- CPSM polls every 3 seconds with an
    idle event loop throughout.

    These tests pin that down, so nobody re-derives the retracted conclusion
    from the tight-loop numbers sitting above them in this same file.
    See CORRECTION 8 in docs/MEMORY-LEAK-INVESTIGATION.md.
    """

    ITERS = 4000

    def _measure(self, view: ScreenMapWidget, between: Any) -> float:
        """Return native B/call, running `between(i)` after each call."""
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        fixed = QRectF(0, 0, 1920, 1080)

        def fit() -> None:
            view._view.fitInView(fixed, Qt.AspectRatioMode.KeepAspectRatio)

        for _ in range(200):
            fit()
        for _ in range(3):
            gc.collect()
        if app is not None:
            app.processEvents()
        base = native_heap_bytes()
        for i in range(self.ITERS):
            fit()
            between(i)
        for _ in range(3):
            gc.collect()
        return (native_heap_bytes() - base) / self.ITERS

    def test_tight_loop_grows_but_any_yield_removes_it(self, view: ScreenMapWidget) -> None:
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()

        tight = self._measure(view, lambda i: None)
        paced = self._measure(view, lambda i: app.processEvents() if i % 50 == 0 else None)

        text = (
            f"--- fitInView: tight loop vs yielding (CORRECTION 8) ---\n"
            f"  iterations           : {self.ITERS}\n"
            f"  tight loop           : {tight:.1f} B/call\n"
            f"  processEvents()/50   : {paced:.1f} B/call\n"
            f"  (tight-loop figure depends on how many fitInView calls this\n"
            f"   process already made; growth saturates once the arena has\n"
            f"   expanded, which is itself evidence against an unbounded leak)\n"
            f"  => the growth is a HARNESS ARTIFACT of an unyielding loop;\n"
            f"     a real app spins its event loop and polls every 3 s, so it\n"
            f"     never accumulates. See CORRECTION 8 in\n"
            f"     docs/MEMORY-LEAK-INVESTIGATION.md"
        )
        print("\n" + text)
        with open(_RESULTS, "a", encoding="utf-8") as fh:
            fh.write(text + "\n\n")

        # NOT asserted: that the tight loop grows. Whether it does depends on
        # how many fitInView calls this PROCESS has already made. Running this
        # test after the four above (~33,000 calls) measured 0.0 B/call, because
        # the arena had already expanded and had free space to reuse. That
        # saturation is itself evidence against an unbounded leak: a real leak
        # does not stop just because you have leaked enough already.
        #
        # What IS asserted is the property that matters and holds regardless of
        # process history: with yielding, growth stays at noise.
        assert paced <= 64.0, (
            f"yielding every 50 calls grew {paced:.1f} B/call — the CORRECTION 8 "
            f"conclusion (that the tight-loop growth is an artifact of not "
            f"yielding) would then be WRONG and fitInView may leak for real"
        )
        assert paced <= max(64.0, tight), (
            f"yielding ({paced:.1f} B/call) grew MORE than not yielding "
            f"({tight:.1f} B/call) — the artifact explanation does not hold"
        )
