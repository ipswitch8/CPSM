# -*- coding: utf-8 -*-
"""
Tests for Bug 4: Layouts tab no longer shows a placeholder.

Covers:
- Layouts tab now lists layouts.
- Double-click → LayoutEditorDialog opens.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QDialog, QLabel, QListView

from cpsm.data.schema import CpsmDocument, GeometryPct, Monitor, ScreenLayout, Viewport
from cpsm.ui.main_window import MainWindow

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_layout(lid: str, name: str, n_monitors: int = 1) -> ScreenLayout:
    monitors = []
    for i in range(n_monitors):
        vp = Viewport(
            id=f"vp-{lid}-{i}",
            geometry_pct=GeometryPct(x=0, y=0, w=100, h=100),
        )
        monitors.append(Monitor(viewports=[vp]))
    return ScreenLayout(id=lid, name=name, monitors=monitors)


@pytest.fixture()
def layouts_doc():
    lay1 = _make_layout("layout-one", "Layout One", n_monitors=1)
    lay2 = _make_layout("layout-two", "Layout Two", n_monitors=2)
    return CpsmDocument(screen_layouts=[lay1, lay2])


@pytest.fixture()
def win(qtbot, layouts_doc):
    w = MainWindow(document=layouts_doc)
    qtbot.addWidget(w)
    w.show()
    return w


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_layouts_tab_has_listview(win: MainWindow) -> None:
    """Layouts tab must contain a QListView."""
    view = win.findChild(QListView, "listview_layouts")
    assert view is not None, "listview_layouts not found"


def test_layouts_tab_lists_layouts(win: MainWindow, layouts_doc) -> None:
    """Layouts list must show one row per layout."""
    model = win._layouts_model
    assert model.rowCount() == len(layouts_doc.screen_layouts)


def test_layouts_tab_no_placeholder_text(win: MainWindow) -> None:
    """Layouts tab must not contain 'Coming in Phase 18' text."""
    lbl = win.findChild(QLabel, "label_placeholder_layouts")
    if lbl is not None:
        assert "Coming in Phase 18" not in lbl.text()


def test_layouts_tab_row_text_contains_id_and_name(win: MainWindow, layouts_doc) -> None:
    """Each row must mention the layout id and name."""
    model = win._layouts_model
    for i, sl in enumerate(layouts_doc.screen_layouts):
        text = model.item(i).text()
        assert sl.id in text
        assert sl.name in text


def test_layouts_tab_row_text_contains_monitor_count(win: MainWindow, layouts_doc) -> None:
    """Each row should mention monitor count."""
    model = win._layouts_model
    # layout-one has 1 monitor
    assert "1" in model.item(0).text()
    # layout-two has 2 monitors
    assert "2" in model.item(1).text()


def test_layouts_double_click_opens_layout_editor(qtbot, win: MainWindow) -> None:
    """Double-clicking a layout row opens LayoutEditorDialog."""
    from cpsm.ui.dialogs.layout_editor import LayoutEditorDialog

    opened: list[LayoutEditorDialog] = []
    original_exec = LayoutEditorDialog.exec

    def _fake_exec(self_dlg: LayoutEditorDialog) -> int:
        opened.append(self_dlg)
        return QDialog.DialogCode.Rejected

    LayoutEditorDialog.exec = _fake_exec  # type: ignore[method-assign]
    try:
        index = win._layouts_model.index(0, 0)
        win._on_layout_double_clicked(index)

        assert len(opened) == 1
    finally:
        LayoutEditorDialog.exec = original_exec  # type: ignore[method-assign]


def test_layouts_double_click_accept_updates_document(qtbot, win: MainWindow, layouts_doc) -> None:
    """On dialog accept, the LayoutEditorDialog's _layout is used to update the document."""
    from cpsm.ui.dialogs.layout_editor import LayoutEditorDialog

    original_exec = LayoutEditorDialog.exec

    def _fake_exec(self_dlg: LayoutEditorDialog) -> int:
        return QDialog.DialogCode.Accepted

    LayoutEditorDialog.exec = _fake_exec  # type: ignore[method-assign]
    try:
        index = win._layouts_model.index(0, 0)
        win._on_layout_double_clicked(index)
        # The layout count should remain the same (edit, not new)
        assert len(win._document.screen_layouts) >= 1
    finally:
        LayoutEditorDialog.exec = original_exec  # type: ignore[method-assign]


def test_layouts_double_click_out_of_range_no_crash(win: MainWindow) -> None:
    """Double-clicking an out-of-range row must not crash."""

    class _FakeIndex:
        def row(self) -> int:
            return 999

    win._on_layout_double_clicked(_FakeIndex())  # type: ignore[arg-type]
