# -*- coding: utf-8 -*-
"""
tests/ui/test_group_editor_rename_layout.py

Feature E: Group editor lets the user rename a layout inline.

Covers:
  - Rename button is present in the layout row.
  - Clicking Rename creates an inline QLineEdit.
  - Committing the edit updates doc.screen_layouts and calls save_callback.
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLineEdit, QPushButton

from cpsm.data.schema import (
    CpsmDocument,
    GeometryPct,
    Group,
    Monitor,
    ScreenLayout,
    Viewport,
)
from cpsm.ui.dialogs.group_editor import GroupEditorDialog


def _make_doc() -> CpsmDocument:
    vp = Viewport(id="vp-1", geometry_pct=GeometryPct(x=0, y=0, w=100, h=100), panes=[])
    layout = ScreenLayout(id="grp-test-default-layout", name="Original Name", monitors=[Monitor(viewports=[vp])])
    grp = Group(id="grp-test", name="Test Group", members=[], default_layout_id="grp-test-default-layout")
    return CpsmDocument(groups=[grp], screen_layouts=[layout])


def _open_editor(qtbot: Any, doc: CpsmDocument, save_callback: Any = None) -> GroupEditorDialog:
    grp = doc.groups[0]
    dlg = GroupEditorDialog(
        parent=None,
        group_data=grp.model_dump(mode="python"),
        all_connections=[],
        available_layout_ids=[sl.id for sl in doc.screen_layouts],
        is_new=False,
        doc=doc,
        save_callback=save_callback,
    )
    qtbot.addWidget(dlg)
    dlg.show()
    QApplication.processEvents()
    return dlg


class TestGroupEditorRenameLayout:
    def test_rename_button_present(self, qtbot: Any) -> None:
        """A 'Rename' button exists in each layout row."""
        doc = _make_doc()
        dlg = _open_editor(qtbot, doc)

        btn = dlg.findChild(QPushButton, "btn_rename_layout_grp-test-default-layout")
        assert btn is not None, "Rename button not found"

    def test_rename_creates_inline_edit(self, qtbot: Any) -> None:
        """Clicking Rename inserts a QLineEdit pre-filled with the current name."""
        doc = _make_doc()
        dlg = _open_editor(qtbot, doc)

        btn = dlg.findChild(QPushButton, "btn_rename_layout_grp-test-default-layout")
        assert btn is not None
        btn.click()
        QApplication.processEvents()

        edit = dlg.findChild(QLineEdit, "edit_rename_layout_grp-test-default-layout")
        assert edit is not None, "Inline QLineEdit not found after Rename click"
        assert edit.text() == "Original Name"

    def test_rename_persists_on_editing_finished(self, qtbot: Any) -> None:
        """Committing the rename updates doc.screen_layouts and calls save_callback."""
        doc = _make_doc()
        save_mock = MagicMock()
        dlg = _open_editor(qtbot, doc, save_callback=save_mock)

        btn = dlg.findChild(QPushButton, "btn_rename_layout_grp-test-default-layout")
        assert btn is not None
        btn.click()
        QApplication.processEvents()

        edit = dlg.findChild(QLineEdit, "edit_rename_layout_grp-test-default-layout")
        assert edit is not None

        # Type new name and commit
        edit.setText("New Layout Name")
        edit.editingFinished.emit()
        QApplication.processEvents()

        # Layout name updated in doc
        sl = next(sl for sl in doc.screen_layouts if sl.id == "grp-test-default-layout")
        assert sl.name == "New Layout Name"

        # save_callback was called
        save_mock.assert_called()

    def test_rename_empty_name_is_ignored(self, qtbot: Any) -> None:
        """Committing an empty name must not update the layout."""
        doc = _make_doc()
        dlg = _open_editor(qtbot, doc)

        btn = dlg.findChild(QPushButton, "btn_rename_layout_grp-test-default-layout")
        assert btn is not None
        btn.click()
        QApplication.processEvents()

        edit = dlg.findChild(QLineEdit, "edit_rename_layout_grp-test-default-layout")
        assert edit is not None

        edit.setText("")
        edit.editingFinished.emit()
        QApplication.processEvents()

        # Layout name must stay unchanged
        sl = next(sl for sl in doc.screen_layouts if sl.id == "grp-test-default-layout")
        assert sl.name == "Original Name"
