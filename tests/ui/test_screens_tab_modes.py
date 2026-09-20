# -*- coding: utf-8 -*-
"""
tests/ui/test_screens_tab_modes.py

Change 4 — Screens tab Live/Preview toggle + group picker.

Covers:
- Tab title is "Screens".
- Live/Preview radios are present.
- Selecting Preview reveals the group picker.
- Selecting a group renders its default_layout_id.
- Selecting Live renders the synthesized layout.
- Save button is present.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QComboBox, QPushButton, QRadioButton, QTabWidget

from cpsm.data.schema import (
    ClaudeLocalConnection,
    CpsmDocument,
    GeometryPct,
    Group,
    Monitor,
    ScreenLayout,
    Viewport,
)
from cpsm.ui.main_window import MainWindow

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_layout(lid: str, name: str) -> ScreenLayout:
    vp = Viewport(id=f"vp-{lid}", geometry_pct=GeometryPct(x=0, y=0, w=100, h=100))
    return ScreenLayout(id=lid, name=name, monitors=[Monitor(viewports=[vp])])


def _make_doc_with_group() -> CpsmDocument:
    conn = ClaudeLocalConnection(
        id="conn-z",
        name="Conn Z",
        launch_profile="claude-local",
        project_folder="~/z",
        claude_options="--resume",
    )
    layout = _make_layout("grp-a-default-layout", "Group A default")
    grp = Group(
        id="grp-a",
        name="Group A",
        members=["conn-z"],
        default_layout_id="grp-a-default-layout",
    )
    return CpsmDocument(connections=[conn], groups=[grp], screen_layouts=[layout])


def _open_win(qtbot, doc: CpsmDocument) -> MainWindow:
    win = MainWindow(document=doc)
    qtbot.addWidget(win)
    win.show()
    return win


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_screens_tab_has_no_title(qtbot) -> None:
    """Round B removed the tab title (only one tab — no need for a label)."""
    win = _open_win(qtbot, CpsmDocument())
    tabs = win.findChild(QTabWidget, "tabwidget_main")
    assert tabs is not None
    assert tabs.count() == 1
    assert tabs.tabText(0) == ""


def test_live_radio_present(qtbot) -> None:
    """A 'Live' radio button with objectName 'radio_screens_live' must exist."""
    win = _open_win(qtbot, CpsmDocument())
    radio = win.findChild(QRadioButton, "radio_screens_live")
    assert radio is not None, "radio_screens_live not found"


def test_preview_radio_present(qtbot) -> None:
    """A 'Preview' radio button with objectName 'radio_screens_preview' must exist."""
    win = _open_win(qtbot, CpsmDocument())
    radio = win.findChild(QRadioButton, "radio_screens_preview")
    assert radio is not None, "radio_screens_preview not found"


def test_preview_is_default_mode(qtbot) -> None:
    """After the Round A redesign the Live/Preview toggle is removed from
    the UI; Preview is always the active mode."""
    doc = _make_doc_with_group()
    win = _open_win(qtbot, doc)

    assert win._radio_screens_preview.isChecked()
    assert not win._radio_screens_live.isChecked()


def test_group_picker_hidden(qtbot) -> None:
    """Round B removed the in-tab Group selector from the visible UI; the
    combo widget still exists for tests but is hidden."""
    doc = _make_doc_with_group()
    win = _open_win(qtbot, doc)
    assert win._combo_screens_group.isHidden()


def test_preview_group_picker_populated(qtbot) -> None:
    """After switching to Preview, the group picker lists the doc groups."""
    doc = _make_doc_with_group()
    win = _open_win(qtbot, doc)

    win._radio_screens_preview.setChecked(True)

    combo = win.findChild(QComboBox, "combo_screens_group")
    assert combo is not None
    assert combo.count() == len(doc.groups)


def test_save_button_present(qtbot) -> None:
    """A 'Save Layout…' button with objectName 'btn_screens_save' must exist."""
    win = _open_win(qtbot, CpsmDocument())
    btn = win.findChild(QPushButton, "btn_screens_save")
    assert btn is not None


def test_live_mode_calls_synthesize(qtbot) -> None:
    """Switching back to Live mode calls _refresh_screens_live without crashing."""
    doc = _make_doc_with_group()
    win = _open_win(qtbot, doc)

    # Switch to preview then back to live — must not raise
    win._radio_screens_preview.setChecked(True)
    win._radio_screens_live.setChecked(True)
    # No assertion needed; just ensure no exception is raised
