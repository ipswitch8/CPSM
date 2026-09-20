# -*- coding: utf-8 -*-
"""
Bisect the screen-map rebuild to find which construct leaks natively.

Established so far:
  * ``ScreenMapWidget.set_layout`` leaks ~950 B per rebuild, linearly, forever.
  * Stubbing out the ``QFontMetricsF`` probe loop changes nothing
    (992.6 B/iter stubbed vs 947.5 B/iter unstubbed) — so the font-fitting
    loop, the obvious suspect, is NOT the cause.
  * Nothing leaks at the Python or Qt-instance level.

``_redraw`` does four separable things.  Each is measured here in isolation,
using the same growth-curve discriminator, so the leak can be attributed to one
of them rather than to "the rebuild" as a whole:

  1. ``scene.clear()`` on its own
  2. clear + re-add plain ``QGraphicsRectItem`` (the pane/monitor rects)
  3. clear + re-add ``QGraphicsTextItem`` (each owns a ``QTextDocument``)
  4. ``QGraphicsView.fitInView()`` on its own
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from typing import Any

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QGraphicsRectItem, QGraphicsTextItem

from cpsm.ui.widgets.screen_map import ScreenMapWidget
from tests.perf.test_growth_curve import CHECKPOINTS, PER_CHECKPOINT, _curve

pytestmark = [pytest.mark.ui, pytest.mark.perf, pytest.mark.slow]

# Matches the 6-pane layout used in the other measurements: 24 scene items,
# 6 of which carry text.
N_RECTS = 24
N_TEXTS = 6


def _record_curve(out: str) -> None:
    print("\n" + out)
    with open(
        os.path.join(os.path.dirname(__file__), "leak_measurements.txt"),
        "a",
        encoding="utf-8",
    ) as fh:
        fh.write(out + "\n\n")


@pytest.fixture()
def widget(qtbot: Any) -> ScreenMapWidget:
    w = ScreenMapWidget()
    qtbot.addWidget(w)
    return w


class TestRebuildBisection:
    def test_scene_clear_only(self, widget: ScreenMapWidget) -> None:
        """Baseline: does clearing an empty scene leak?"""

        def cycle(_i: int) -> None:
            widget.scene.clear()

        _record_curve(_curve("scene.clear() only", cycle, CHECKPOINTS, PER_CHECKPOINT))

    def test_clear_and_add_rect_items(self, widget: ScreenMapWidget) -> None:
        """Rect items only — no text, no fonts."""

        def cycle(_i: int) -> None:
            widget.scene.clear()
            for k in range(N_RECTS):
                widget.scene.addItem(QGraphicsRectItem(k, k, 50, 30))

        _record_curve(
            _curve(
                f"clear + {N_RECTS} QGraphicsRectItem", cycle, CHECKPOINTS, PER_CHECKPOINT
            )
        )

    def test_clear_and_add_text_items(self, widget: ScreenMapWidget) -> None:
        """Text items only — each QGraphicsTextItem owns a QTextDocument."""
        color = QColor("#ffffff")

        def cycle(_i: int) -> None:
            widget.scene.clear()
            for k in range(N_TEXTS):
                item = QGraphicsTextItem(f"connection-{k}")
                font = QFont()
                font.setPointSize(8)
                item.setFont(font)
                item.setDefaultTextColor(color)
                widget.scene.addItem(item)

        _record_curve(
            _curve(
                f"clear + {N_TEXTS} QGraphicsTextItem", cycle, CHECKPOINTS, PER_CHECKPOINT
            )
        )

    def test_fit_in_view_only(self, widget: ScreenMapWidget) -> None:
        """View transform recalculation, performed once per rebuild."""
        widget.scene.addItem(QGraphicsRectItem(0, 0, 800, 600))

        def cycle(_i: int) -> None:
            widget._view.fitInView(
                widget.scene.itemsBoundingRect(), Qt.AspectRatioMode.KeepAspectRatio
            )

        _record_curve(
            _curve("fitInView() only", cycle, CHECKPOINTS, PER_CHECKPOINT)
        )
