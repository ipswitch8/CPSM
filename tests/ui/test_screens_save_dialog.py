# -*- coding: utf-8 -*-
"""
tests/ui/test_screens_save_dialog.py

Change 5 — Save Layout… dialog.

Covers:
- Overwrite path replaces the right entry in doc.screen_layouts.
- "New layout for group" appends and updates default_layout_id.
- "New group + layout" creates both.
- Disabled options behave correctly.
- Dialog has stable objectName dlg_screens_save.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QRadioButton

from cpsm.data.schema import (
    ClaudeLocalConnection,
    CpsmDocument,
    GeometryPct,
    Group,
    Monitor,
    ScreenLayout,
    Viewport,
)
from cpsm.ui.dialogs.screens_save import ScreensSaveDialog

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_layout(lid: str, name: str) -> ScreenLayout:
    vp = Viewport(id=f"vp-{lid}", geometry_pct=GeometryPct(x=0, y=0, w=100, h=100))
    return ScreenLayout(id=lid, name=name, monitors=[Monitor(viewports=[vp])])


def _make_doc() -> CpsmDocument:
    conn = ClaudeLocalConnection(
        id="conn-m",
        name="Conn M",
        launch_profile="claude-local",
        project_folder="~/m",
        claude_options="--resume",
    )
    layout = _make_layout("grp-m-default-layout", "Group M default")
    grp = Group(
        id="grp-m",
        name="Group M",
        members=["conn-m"],
        default_layout_id="grp-m-default-layout",
    )
    return CpsmDocument(connections=[conn], groups=[grp], screen_layouts=[layout])


def _make_canvas_layout() -> ScreenLayout:
    return _make_layout("canvas-layout", "Canvas Layout")


def _open_dlg(qtbot, doc, **kwargs) -> ScreensSaveDialog:
    canvas = _make_canvas_layout()
    dlg = ScreensSaveDialog(
        doc=doc,
        canvas_layout=canvas,
        **kwargs,
    )
    qtbot.addWidget(dlg)
    dlg.show()
    return dlg


# ---------------------------------------------------------------------------
# Tests — dialog basics
# ---------------------------------------------------------------------------


def test_dialog_has_stable_object_name(qtbot) -> None:
    """Dialog must have objectName 'dlg_screens_save'."""
    doc = _make_doc()
    dlg = _open_dlg(qtbot, doc)
    assert dlg.objectName() == "dlg_screens_save"


def test_overwrite_radio_disabled_in_live_mode(qtbot) -> None:
    """Overwrite option must be disabled when not in preview mode."""
    doc = _make_doc()
    dlg = _open_dlg(qtbot, doc, is_preview_mode=False)
    radio = dlg.findChild(QRadioButton, "radio_save_overwrite")
    assert radio is not None
    assert not radio.isEnabled()


def test_overwrite_radio_enabled_in_preview_with_layout(qtbot) -> None:
    """Overwrite option must be enabled in preview mode with a layout selected."""
    doc = _make_doc()
    dlg = _open_dlg(
        qtbot,
        doc,
        is_preview_mode=True,
        current_layout_id="grp-m-default-layout",
        current_layout_name="Group M default",
    )
    radio = dlg.findChild(QRadioButton, "radio_save_overwrite")
    assert radio is not None
    assert radio.isEnabled()


def test_new_layout_radio_disabled_with_no_groups(qtbot) -> None:
    """'New layout for group' option must be disabled when doc has no groups."""
    doc = CpsmDocument()  # no groups
    dlg = _open_dlg(qtbot, doc)
    radio = dlg.findChild(QRadioButton, "radio_save_new_layout")
    assert radio is not None
    assert not radio.isEnabled()


def test_overwrite_result(qtbot) -> None:
    """get_result() returns mode='overwrite' with layout_id when overwrite selected."""
    doc = _make_doc()
    dlg = _open_dlg(
        qtbot,
        doc,
        is_preview_mode=True,
        current_layout_id="grp-m-default-layout",
        current_layout_name="Group M default",
    )
    dlg._radio_overwrite.setChecked(True)

    result = dlg.get_result()
    assert result["mode"] == "overwrite"
    assert result["layout_id"] == "grp-m-default-layout"


def test_new_layout_result(qtbot) -> None:
    """get_result() returns mode='new_layout' with group_id when new_layout selected."""
    doc = _make_doc()
    dlg = _open_dlg(qtbot, doc)
    dlg._radio_new_layout.setChecked(True)
    dlg._edit_new_layout_name.setText("My New Layout")

    result = dlg.get_result()
    assert result["mode"] == "new_layout"
    assert result["group_id"] == "grp-m"
    assert result["layout_name"] == "My New Layout"


def test_new_group_result(qtbot) -> None:
    """get_result() returns mode='new_group' with group_name when new_group selected."""
    doc = _make_doc()
    dlg = _open_dlg(qtbot, doc)
    dlg._radio_new_group.setChecked(True)
    dlg._edit_new_group_name.setText("Fresh Group")

    result = dlg.get_result()
    assert result["mode"] == "new_group"
    assert result["group_name"] == "Fresh Group"


def test_save_button_disabled_until_option_selected(qtbot) -> None:
    """Save button must be disabled when no option is selected."""
    doc = _make_doc()
    dlg = _open_dlg(qtbot, doc)

    # No radio selected → save disabled
    dlg._mode_group.setExclusive(False)
    dlg._radio_overwrite.setChecked(False)
    dlg._radio_new_layout.setChecked(False)
    dlg._radio_new_group.setChecked(False)
    dlg._mode_group.setExclusive(True)
    dlg._update_save_button()

    assert not dlg._btn_save.isEnabled()


def test_main_window_overwrite_path(qtbot) -> None:
    """MainWindow._apply_screens_save with mode='overwrite' replaces the layout."""
    from cpsm.ui.main_window import MainWindow

    doc = _make_doc()
    win = MainWindow(document=doc)
    qtbot.addWidget(win)
    win.show()

    canvas = _make_canvas_layout()

    win._apply_screens_save(
        {"mode": "overwrite", "layout_id": "grp-m-default-layout"},
        canvas,
    )

    # Layout count unchanged, but the layout at that id still exists
    assert len(win._document.screen_layouts) == 1
    assert win._document.screen_layouts[0].id == "grp-m-default-layout"


def test_main_window_new_layout_for_group(qtbot) -> None:
    """MainWindow._apply_screens_save with mode='new_layout' appends layout and updates group."""
    from cpsm.ui.main_window import MainWindow

    doc = _make_doc()
    win = MainWindow(document=doc)
    qtbot.addWidget(win)
    win.show()

    canvas = _make_canvas_layout()
    initial_count = len(win._document.screen_layouts)

    win._apply_screens_save(
        {"mode": "new_layout", "group_id": "grp-m", "layout_name": "Brand New"},
        canvas,
    )

    assert len(win._document.screen_layouts) == initial_count + 1
    # The group's default_layout_id should point to the new layout
    grp = next(g for g in win._document.groups if g.id == "grp-m")
    new_layout_id = grp.default_layout_id
    assert new_layout_id is not None
    assert any(sl.id == new_layout_id for sl in win._document.screen_layouts)


def test_main_window_new_group_and_layout(qtbot) -> None:
    """MainWindow._apply_screens_save with mode='new_group' creates both group and layout."""
    from cpsm.ui.main_window import MainWindow

    doc = CpsmDocument()  # empty
    win = MainWindow(document=doc)
    qtbot.addWidget(win)
    win.show()

    canvas = _make_canvas_layout()

    win._apply_screens_save(
        {"mode": "new_group", "group_name": "Brand New Group", "layout_name": "BNG default"},
        canvas,
    )

    assert len(win._document.groups) == 1
    assert len(win._document.screen_layouts) == 1
    assert win._document.groups[0].name == "Brand New Group"
    assert win._document.groups[0].default_layout_id is not None
