# -*- coding: utf-8 -*-
"""
tests/ui/test_main_window_redetect.py — MainWindow side of screen re-detection.

When ScreenMapWidget reports that re-detection grew a layout, the main window
must write the reconciled layout back into the document and persist it, so the
newly-detected display survives the next restart rather than being re-detected
(and re-forgotten) on every launch.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace

import pytest

from cpsm.data.schema import CpsmDocument, GeometryPct, Monitor, Pane, ScreenLayout, Viewport
from cpsm.ui.main_window import MainWindow

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _viewport(vp_id: str, *connection_ids: str) -> Viewport:
    return Viewport(
        id=vp_id,
        geometry_pct=GeometryPct(x=0, y=0, w=100, h=100),
        tmux_window_name=vp_id,
        tmux_layout="tiled",
        panes=[Pane(connection_id=cid) for cid in connection_ids],
    )


def _layout(n_monitors: int, lid: str = "dev-default-layout") -> ScreenLayout:
    return ScreenLayout(
        id=lid,
        name="dev default",
        monitors=[
            Monitor(identifier=f"MON-{i}", viewports=[_viewport(f"{lid}-vp-{i}")])
            for i in range(n_monitors)
        ],
    )


@pytest.fixture
def win(qtbot):
    """A MainWindow holding a two-monitor layout, with saves captured."""
    doc = CpsmDocument(screen_layouts=[_layout(2)])
    window = MainWindow(document=doc)
    qtbot.addWidget(window)
    window._saved: list[CpsmDocument] = []
    window._save_document = lambda: window._saved.append(window._document)
    return window


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_reconciled_layout_replaces_the_document_copy(win) -> None:
    win._on_screen_map_layout_reconciled(_layout(3))

    stored = win._document.screen_layouts[0]
    assert len(stored.monitors) == 3
    assert stored.id == "dev-default-layout"


def test_reconciled_layout_is_persisted(win) -> None:
    win._on_screen_map_layout_reconciled(_layout(3))

    assert len(win._saved) == 1, "the newly-detected display must be written to disk"


def test_identical_layout_is_not_persisted(win) -> None:
    """Re-rendering an already-current layout must not rewrite ~/.cpsm.yaml."""
    win._on_screen_map_layout_reconciled(_layout(2))

    assert win._saved == []


def test_layout_absent_from_the_document_is_ignored(win) -> None:
    """The transient '(no layout)' placeholder, and temporary layouts, are not
    part of the document and must never be injected into it."""
    win._on_screen_map_layout_reconciled(_layout(3, lid="empty"))

    assert len(win._document.screen_layouts) == 1
    assert len(win._document.screen_layouts[0].monitors) == 2
    assert win._saved == []


def test_payload_without_an_id_is_ignored(win) -> None:
    win._on_screen_map_layout_reconciled(SimpleNamespace())

    assert win._saved == []


def test_status_bar_reports_the_new_monitor_count(win) -> None:
    win._on_screen_map_layout_reconciled(_layout(3))

    message = win.statusBar().currentMessage()
    assert "3 monitors" in message


def test_screen_map_widget_signal_is_connected(win) -> None:
    """The wiring itself, not just the slot: emitting on the widget must reach
    the document."""
    win._screen_map_widget.layout_reconciled.emit(_layout(3))

    assert len(win._document.screen_layouts[0].monitors) == 3
    assert len(win._saved) == 1
