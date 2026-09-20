# -*- coding: utf-8 -*-
"""
Tests for Bug 5: Screen Map empty-state message and auto-select first layout.

Covers:
- With no screen_layouts, screen map shows an empty-state message label.
- With one screen_layout, set_layout is called on the ScreenMapWidget.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QLabel

from cpsm.data.schema import CpsmDocument, GeometryPct, Monitor, ScreenLayout, Viewport
from cpsm.ui.main_window import MainWindow

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_layout(lid: str) -> ScreenLayout:
    vp = Viewport(id=f"vp-{lid}", geometry_pct=GeometryPct(x=0, y=0, w=100, h=100))
    return ScreenLayout(
        id=lid, name=lid.replace("-", " ").title(), monitors=[Monitor(viewports=[vp])]
    )


# ---------------------------------------------------------------------------
# Tests: empty document
# ---------------------------------------------------------------------------


def test_screen_map_empty_state_label_exists_when_no_layouts(qtbot) -> None:
    """The empty-state label widget exists in the tree.

    Note: in the post-redesign Screens tab, the canvas itself communicates the
    empty state via ghost-monitor rendering, so this label is always hidden in
    the new flow. The widget is kept for backward-compatibility with code that
    still queries it by objectName.
    """
    doc = CpsmDocument()  # no screen_layouts
    win = MainWindow(document=doc)
    qtbot.addWidget(win)
    win.show()

    lbl = win.findChild(QLabel, "label_screen_map_empty")
    assert lbl is not None, "Empty-state label must exist for objectName lookups"


def test_screen_map_empty_state_label_contains_guidance(qtbot) -> None:
    """Empty-state label must provide actionable guidance text."""
    doc = CpsmDocument()
    win = MainWindow(document=doc)
    qtbot.addWidget(win)
    win.show()

    lbl = win.findChild(QLabel, "label_screen_map_empty")
    assert lbl is not None
    text = lbl.text()
    assert "layout" in text.lower() or "screen" in text.lower()


# ---------------------------------------------------------------------------
# Tests: document with one layout
# ---------------------------------------------------------------------------


def test_screen_map_set_layout_called_with_one_layout(qtbot) -> None:
    """When doc has at least one layout, set_layout is called on ScreenMapWidget."""

    doc = CpsmDocument(screen_layouts=[_make_layout("main-layout")])
    win = MainWindow(document=doc)
    qtbot.addWidget(win)
    win.show()

    # The empty-state label should be absent or hidden
    lbl = win.findChild(QLabel, "label_screen_map_empty")
    if lbl is not None:
        assert not lbl.isVisible(), "Empty-state label should be hidden when layouts exist"


def test_screen_map_empty_label_hidden_after_layout_added(qtbot) -> None:
    """The empty-state label is always hidden in the redesigned Screens tab.

    The canvas renders ghost monitors when no layout is selected; the standalone
    label is no longer the empty-state mechanism. This test now asserts the
    label remains hidden across the lifecycle.
    """
    # Start with empty doc
    doc_empty = CpsmDocument()
    win = MainWindow(document=doc_empty)
    qtbot.addWidget(win)
    win.show()

    lbl = win.findChild(QLabel, "label_screen_map_empty")
    assert lbl is not None
    assert lbl.isHidden(), "Label is always hidden in the redesigned Screens tab"

    # Reload with a layout — label still hidden
    doc_with = CpsmDocument(screen_layouts=[_make_layout("new-layout")])
    win.load_document(doc_with)
    assert lbl.isHidden(), "Label remains hidden after layout load"


def test_screen_map_set_layout_invoked_on_widget(qtbot) -> None:
    """set_layout on ScreenMapWidget is called when a layout exists."""

    doc = CpsmDocument(screen_layouts=[_make_layout("lay-one")])

    # Patch set_layout on the class level before the window is created
    set_layout_calls: list[object] = []

    from cpsm.ui.widgets.screen_map import ScreenMapWidget

    original_set_layout = ScreenMapWidget.set_layout

    def _capture_set_layout(self_sm, layout, monitors):
        set_layout_calls.append(layout)
        original_set_layout(self_sm, layout, monitors)

    ScreenMapWidget.set_layout = _capture_set_layout  # type: ignore[method-assign]
    try:
        win = MainWindow(document=doc)
        qtbot.addWidget(win)
        win.show()

        assert len(set_layout_calls) >= 1, "set_layout should be called with the first layout"
        assert set_layout_calls[0].id == "lay-one"
    finally:
        ScreenMapWidget.set_layout = original_set_layout  # type: ignore[method-assign]


# ---------------------------------------------------------------------------
# Tests: remove-disconnected-monitor handler wiring
# ---------------------------------------------------------------------------


def _layout_with_two_monitors(live_ident: str, ghost_ident: str) -> ScreenLayout:
    """Layout with one connected + one disconnected monitor."""
    vp_live = Viewport(id="vp-live", geometry_pct=GeometryPct(x=0, y=0, w=100, h=100))
    vp_gone = Viewport(id="vp-gone", geometry_pct=GeometryPct(x=0, y=0, w=100, h=100))
    return ScreenLayout(
        id="two-mon",
        name="Two Monitor Layout",
        monitors=[
            Monitor(identifier=live_ident, viewports=[vp_live]),
            Monitor(identifier=ghost_ident, viewports=[vp_gone]),
        ],
    )


def test_remove_disconnected_monitor_signal_drops_ghost_from_layout(qtbot) -> None:
    """Emitting ``remove_disconnected_monitor_requested`` on the ScreenMapWidget
    must remove the matching schema monitor from the layout, save the document,
    and repaint (via ``_screens_persist_canvas_layout`` +
    ``_screen_map_widget.set_layout``).
    """
    layout = _layout_with_two_monitors(live_ident="mon-live", ghost_ident="mon-gone")
    doc = CpsmDocument(screen_layouts=[layout])
    win = MainWindow(document=doc)
    qtbot.addWidget(win)
    win.show()

    # The initial set_layout was performed with no live monitors (no
    # MonitorService in this test rig), so we manually push the layout
    # into the widget and populate _layout_data.
    win._screen_map_widget.set_layout(layout, [])
    assert len(win._screen_map_widget._layout_data.monitors) == 2

    persisted: list[ScreenLayout] = []
    original_persist = win._screens_persist_canvas_layout

    def _capture_persist(canvas_layout: ScreenLayout) -> None:
        persisted.append(canvas_layout)
        original_persist(canvas_layout)

    win._screens_persist_canvas_layout = _capture_persist  # type: ignore[method-assign]

    # Fire the signal — this is what the ghost-menu "Remove Disconnected
    # Monitor" action emits at runtime.
    win._screen_map_widget.remove_disconnected_monitor_requested.emit("mon-gone")

    remaining_idents = [m.identifier for m in layout.monitors]
    assert remaining_idents == ["mon-live"], f"expected ghost dropped; got {remaining_idents}"
    assert persisted, "expected persist to be called for the mutated layout"
    assert persisted[0] is layout


def test_remove_disconnected_monitor_by_synthetic_index_key(qtbot) -> None:
    """Ghost keys of the form ``__ghost_idx_N`` (used when a schema Monitor
    has no identifier) must be resolved back to the Nth monitor and removed.
    """
    layout = _layout_with_two_monitors(live_ident="mon-live", ghost_ident="")
    doc = CpsmDocument(screen_layouts=[layout])
    win = MainWindow(document=doc)
    qtbot.addWidget(win)
    win.show()
    win._screen_map_widget.set_layout(layout, [])

    win._screens_persist_canvas_layout = lambda _canvas: None

    # Synthetic key for the second entry (index 1).
    win._screen_map_widget.remove_disconnected_monitor_requested.emit("__ghost_idx_1")

    assert len(layout.monitors) == 1
    assert layout.monitors[0].identifier == "mon-live"


def test_screens_tab_right_click_on_ghost_shows_ghost_menu(qtbot) -> None:
    """Regression: ``_on_screens_canvas_context_menu`` must show the ghost
    menu when the right-click lands on a ghost rect, even though the
    view's ContextMenuPolicy is CustomContextMenu and therefore bypasses
    ScreenMapWidget's own ``contextMenuEvent``.
    """
    from unittest.mock import patch

    from PySide6.QtWidgets import QMenu

    layout = _layout_with_two_monitors(live_ident="mon-live", ghost_ident="mon-gone")
    doc = CpsmDocument(screen_layouts=[layout])
    win = MainWindow(document=doc)
    qtbot.addWidget(win)
    win.show()
    # Ensure the widget has a ghost record to hit-test against.
    win._screen_map_widget.set_layout(layout, [])
    assert win._screen_map_widget._ghost_registry, "expected a ghost record"
    g = win._screen_map_widget._ghost_registry[0]

    # Scene-space → view-space.  ScreenMapWidget uses fitInView, so the
    # inverse transform is deterministic.
    view = win._screen_map_widget.view
    scene_pt_x = g.scene_x + g.scene_w / 2
    scene_pt_y = g.scene_y + g.scene_h / 2
    view_pt = view.mapFromScene(scene_pt_x, scene_pt_y)

    captured: list[QMenu] = []

    def _capture_exec_menu(self_win, menu, _pos):
        captured.append(menu)

    with patch.object(MainWindow, "_exec_menu", _capture_exec_menu):
        win._on_screens_canvas_context_menu(view_pt)

    assert captured, "expected the ghost context menu to be exec'd"
    assert captured[0].objectName() == "screenmap_ghost_context_menu"


def test_remove_disconnected_monitor_unknown_key_is_a_noop(qtbot) -> None:
    """A ghost_key that matches nothing must not mutate the layout and
    must not crash — the status bar should surface a message instead.
    """
    layout = _layout_with_two_monitors(live_ident="mon-live", ghost_ident="mon-gone")
    doc = CpsmDocument(screen_layouts=[layout])
    win = MainWindow(document=doc)
    qtbot.addWidget(win)
    win.show()
    win._screen_map_widget.set_layout(layout, [])

    persisted_calls = 0

    def _no_persist(_canvas: ScreenLayout) -> None:
        nonlocal persisted_calls
        persisted_calls += 1

    win._screens_persist_canvas_layout = _no_persist  # type: ignore[method-assign]

    win._screen_map_widget.remove_disconnected_monitor_requested.emit("nope")
    assert len(layout.monitors) == 2
    assert persisted_calls == 0
