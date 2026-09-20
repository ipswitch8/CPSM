# -*- coding: utf-8 -*-
"""
tests/ui/test_screen_map_layout_selector.py — Gap A: Screen Map tab layout selector.

Verifies that:
- combo_screen_map_layout lists all doc.screen_layouts[].
- Selecting a different layout changes the rendered layout.
- With zero layouts, the combo is hidden and the empty-state label is shown.
- When a new layout is added (via _open_layout_editor + save), the combo
  is refreshed and the new layout selected.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QComboBox, QLabel

from cpsm.data.schema import (
    CpsmDocument,
    ScreenLayout,
)
from cpsm.ui.main_window import MainWindow

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sl(sl_id: str, name: str) -> ScreenLayout:
    return ScreenLayout(id=sl_id, name=name, monitors=[])


def _doc(*layouts: ScreenLayout) -> CpsmDocument:
    return CpsmDocument(screen_layouts=list(layouts))


def _open_win(qtbot, doc: CpsmDocument) -> MainWindow:
    win = MainWindow(document=doc)
    qtbot.addWidget(win)
    win.show()
    return win


# ---------------------------------------------------------------------------
# Tests — combo presence and visibility
# ---------------------------------------------------------------------------


def test_combo_exists_as_named_widget(qtbot) -> None:
    """combo_screen_map_layout is findable by objectName."""
    doc = _doc(_sl("lay-a", "Layout A"))
    win = _open_win(qtbot, doc)

    combo = win.findChild(QComboBox, "combo_screen_map_layout")
    assert combo is not None


def test_combo_hidden_when_no_layouts(qtbot) -> None:
    """combo_screen_map_layout is hidden when doc has no screen_layouts."""
    win = _open_win(qtbot, CpsmDocument())

    combo = win.findChild(QComboBox, "combo_screen_map_layout")
    assert combo is not None
    # In offscreen Qt, isVisible() depends on the window being mapped; use isHidden()
    assert combo.isHidden()


def test_empty_label_always_hidden_in_new_design(qtbot) -> None:
    """label_screen_map_empty is always hidden in the redesigned Screens tab.

    The canvas itself communicates the empty state via ghost-monitor rendering.
    The standalone label is kept only for backward-compat objectName lookups.
    """
    win = _open_win(qtbot, CpsmDocument())

    lbl = win.findChild(QLabel, "label_screen_map_empty")
    assert lbl is not None
    assert lbl.isHidden()


def test_combo_hidden_when_layouts_exist(qtbot) -> None:
    """Round B removed the legacy combo from the visible UI; it stays hidden
    even when the document has layouts."""
    doc = _doc(_sl("lay-x", "Layout X"))
    win = _open_win(qtbot, doc)

    combo = win.findChild(QComboBox, "combo_screen_map_layout")
    assert combo is not None
    assert combo.isHidden()


def test_empty_label_hidden_when_layouts_exist(qtbot) -> None:
    """label_screen_map_empty is explicitly hidden when there are layouts."""
    doc = _doc(_sl("lay-y", "Layout Y"))
    win = _open_win(qtbot, doc)

    lbl = win.findChild(QLabel, "label_screen_map_empty")
    assert lbl is not None
    assert lbl.isHidden()


# ---------------------------------------------------------------------------
# Tests — combo contents
# ---------------------------------------------------------------------------


def test_combo_lists_all_layouts(qtbot) -> None:
    """Combo contains one entry per layout in doc.screen_layouts."""
    doc = _doc(_sl("lay-a", "Layout A"), _sl("lay-b", "Layout B"), _sl("lay-c", "Layout C"))
    win = _open_win(qtbot, doc)

    combo = win.findChild(QComboBox, "combo_screen_map_layout")
    assert combo is not None
    assert combo.count() == 3


def test_combo_item_data_is_layout_id(qtbot) -> None:
    """Each combo item's data() is the layout's id."""
    doc = _doc(_sl("lay-alpha", "Alpha"), _sl("lay-beta", "Beta"))
    win = _open_win(qtbot, doc)

    combo = win.findChild(QComboBox, "combo_screen_map_layout")
    assert combo is not None
    ids = [combo.itemData(i) for i in range(combo.count())]
    assert "lay-alpha" in ids
    assert "lay-beta" in ids


def test_combo_first_item_selected_by_default(qtbot) -> None:
    """The first layout is selected by default."""
    doc = _doc(_sl("first-layout", "First"), _sl("second-layout", "Second"))
    win = _open_win(qtbot, doc)

    combo = win.findChild(QComboBox, "combo_screen_map_layout")
    assert combo is not None
    assert combo.currentIndex() == 0
    assert combo.currentData() == "first-layout"


# ---------------------------------------------------------------------------
# Tests — selecting a different layout changes the rendered layout
# ---------------------------------------------------------------------------


def test_changing_combo_selection_changes_rendered_layout(qtbot) -> None:
    """Changing the legacy layout combo updates the rendered layout.

    The legacy combo is still wired even though the new Screens tab uses
    Live/Preview modes. After load_document, the active-mode refresh loads the
    Live layout (which has no monitors when no MonitorService is wired and no
    sessions are active). Selecting a layout via the legacy combo should
    override that and render the chosen layout.
    """
    lay_a = _sl("lay-a", "Layout A")
    lay_b = _sl("lay-b", "Layout B")
    doc = _doc(lay_a, lay_b)
    win = _open_win(qtbot, doc)

    combo = win.findChild(QComboBox, "combo_screen_map_layout")
    assert combo is not None
    assert combo.count() == 2

    # Force the legacy path to render the first layout
    win._refresh_screen_map_tab()
    assert win._screen_map_widget._layout_data is not None
    assert win._screen_map_widget._layout_data.id == "lay-a"

    # Change selection and re-render via the legacy path
    combo.setCurrentIndex(1)
    win._refresh_screen_map_tab()
    assert win._screen_map_widget._layout_data is not None
    assert win._screen_map_widget._layout_data.id == "lay-b"


# ---------------------------------------------------------------------------
# Tests — combo refreshed when new layout added
# ---------------------------------------------------------------------------


def test_combo_refreshed_after_load_document(qtbot) -> None:
    """After load_document with a new layout, combo reflects the update."""
    doc = _doc(_sl("lay-a", "Layout A"))
    win = _open_win(qtbot, doc)

    combo = win.findChild(QComboBox, "combo_screen_map_layout")
    assert combo is not None
    assert combo.count() == 1

    # Add a new layout and reload
    new_lay = _sl("lay-b", "Layout B")
    win._document.screen_layouts.append(new_lay)
    win.load_document(win._document)

    assert combo.count() == 2
    ids = [combo.itemData(i) for i in range(combo.count())]
    assert "lay-b" in ids


def test_combo_preserves_selection_on_reload(qtbot) -> None:
    """When reloading with more layouts, previously selected layout stays selected."""
    doc = _doc(_sl("lay-a", "Layout A"), _sl("lay-b", "Layout B"))
    win = _open_win(qtbot, doc)

    combo = win.findChild(QComboBox, "combo_screen_map_layout")
    assert combo is not None

    # Select the second layout
    combo.setCurrentIndex(1)
    assert combo.currentData() == "lay-b"

    # Add a third layout and reload
    new_lay = _sl("lay-c", "Layout C")
    win._document.screen_layouts.append(new_lay)
    win.load_document(win._document)

    # lay-b should still be selected
    assert combo.currentData() == "lay-b"
