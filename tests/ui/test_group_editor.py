# -*- coding: utf-8 -*-
"""
pytest-qt tests for GroupEditorDialog and GroupPanel.

Spec sections: §4.6

Covers:
- Two-list drag-drop: add/remove updates members model
- Multi-group info icon shown for connections in other groups
- Color picker: clicking opens dialog; selecting updates swatch
- Adding connection from another group is permitted
- Save round-trip yields valid pydantic Group model

All tests run with QT_QPA_PLATFORM=offscreen (set in conftest.py).
"""

from __future__ import annotations

import os
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor

from cpsm.data.schema import Group
from cpsm.ui.dialogs.group_editor import GroupEditorDialog
from cpsm.ui.widgets.group_panel import ConnectionEntry, GroupPanel

# U+2139 INFORMATION SOURCE char used by GroupPanel._INFO_ICON
_INFO_ICON_CHAR: str = chr(0x2139)

# ---------------------------------------------------------------------------
# ConnectionEntry fixtures
# ---------------------------------------------------------------------------

_ENTRIES: list[ConnectionEntry] = [
    ConnectionEntry("conn-remote", "Remote Conn", "claude-remote", ["Other Group"]),
    ConnectionEntry("conn-local", "Local Conn", "claude-local", []),
    ConnectionEntry("conn-ssh", "SSH Shell", "ssh-shell", []),
    ConnectionEntry("conn-shell", "Local Shell", "local-shell", []),
    ConnectionEntry("conn-custom", "Custom Conn", "custom", ["Group A", "Group B"]),
]

_LAYOUT_IDS = ["layout-main", "layout-alt"]

_GROUP_DATA: dict[str, Any] = {
    "id": "test-group",
    "name": "Test Group",
    "color": "#3b82f6",
    "members": ["conn-local", "conn-ssh"],
    "launch_order": "sequential",
    "launch_delay_ms": 200,
    "default_layout_id": "layout-main",
    "isolation": "shared",
    "layout_conflict": "move",
    "auto_attach": False,
}


def _make_dlg(qtbot, group_data=None, is_new=True, **kwargs):
    dlg = GroupEditorDialog(
        group_data=group_data,
        all_connections=_ENTRIES,
        available_layout_ids=_LAYOUT_IDS,
        is_new=is_new,
        **kwargs,
    )
    qtbot.addWidget(dlg)
    dlg.show()
    return dlg


# ---------------------------------------------------------------------------
# Object names / construction
# ---------------------------------------------------------------------------


def test_dialog_object_name(qtbot):
    dlg = _make_dlg(qtbot)
    assert dlg.objectName() == "dlg_group_editor"


def test_group_panel_embedded(qtbot):
    dlg = _make_dlg(qtbot)
    panel = dlg.findChild(GroupPanel, "group_panel_embed")
    assert panel is not None


def test_btn_save_present(qtbot):
    from PySide6.QtWidgets import QPushButton

    dlg = _make_dlg(qtbot)
    assert dlg.findChild(QPushButton, "btn_save") is not None


def test_btn_cancel_present(qtbot):
    from PySide6.QtWidgets import QPushButton

    dlg = _make_dlg(qtbot)
    assert dlg.findChild(QPushButton, "btn_cancel") is not None


def test_btn_color_present(qtbot):
    from PySide6.QtWidgets import QPushButton

    dlg = _make_dlg(qtbot)
    assert dlg.findChild(QPushButton, "btn_color") is not None


# ---------------------------------------------------------------------------
# GroupPanel — available / members list logic
# ---------------------------------------------------------------------------


@pytest.fixture()
def panel(qtbot):
    p = GroupPanel()
    qtbot.addWidget(p)
    p.show()
    p.set_all_connections(_ENTRIES)
    return p


def test_available_list_populated(panel):
    # No members set yet → all 5 in available
    count = panel._model_available.rowCount()
    assert count == 5


def test_set_members_moves_to_members_list(panel):
    panel.set_members(["conn-local", "conn-ssh"])
    assert panel._model_members.rowCount() == 2
    assert panel._model_available.rowCount() == 3


def test_add_button_moves_item_to_members(qtbot, panel):
    panel.set_members([])
    # Select conn-local in available list
    idx = None
    for row in range(panel._model_available.rowCount()):
        item = panel._model_available.item(row)
        if item.data(Qt.ItemDataRole.UserRole) == "conn-local":
            idx = panel._model_available.index(row, 0)
            break
    assert idx is not None
    panel._list_available.setCurrentIndex(idx)
    panel._on_add_clicked()
    assert "conn-local" in panel.get_members()
    assert panel._model_members.rowCount() == 1


def test_remove_button_moves_item_back_to_available(qtbot, panel):
    panel.set_members(["conn-local"])
    idx = panel._model_members.index(0, 0)
    panel._list_members.setCurrentIndex(idx)
    panel._on_remove_clicked()
    assert panel.get_members() == []


def test_members_changed_signal_emitted(qtbot, panel):
    panel.set_members([])
    received = []
    panel.members_changed.connect(received.append)
    # Select first available
    idx = panel._model_available.index(0, 0)
    panel._list_available.setCurrentIndex(idx)
    panel._on_add_clicked()
    assert received  # signal was emitted


def test_move_up_reorders_members(panel):
    panel.set_members(["conn-local", "conn-ssh", "conn-shell"])
    # Select row 1 (conn-ssh) and move up
    idx = panel._model_members.index(1, 0)
    panel._list_members.setCurrentIndex(idx)
    panel._on_move_up()
    members = panel.get_members()
    assert members[0] == "conn-ssh"
    assert members[1] == "conn-local"


def test_move_down_reorders_members(panel):
    panel.set_members(["conn-local", "conn-ssh", "conn-shell"])
    idx = panel._model_members.index(0, 0)
    panel._list_members.setCurrentIndex(idx)
    panel._on_move_down()
    members = panel.get_members()
    assert members[0] == "conn-ssh"
    assert members[1] == "conn-local"


def test_search_filters_available_list(panel):
    panel.set_members([])
    panel._on_search_changed("Remote")
    count = panel._model_available.rowCount()
    assert count == 1
    item = panel._model_available.item(0)
    assert "Remote" in item.text()


def test_search_clears_filter(panel):
    panel.set_members([])
    panel._on_search_changed("Remote")
    panel._on_search_changed("")
    assert panel._model_available.rowCount() == 5


# ---------------------------------------------------------------------------
# Multi-group info icon
# ---------------------------------------------------------------------------


def test_info_icon_shown_for_multi_group_connection(panel):
    """conn-remote is in 'Other Group' → its item label should contain the info icon."""
    panel.set_members([])
    found_item = None
    for row in range(panel._model_available.rowCount()):
        item = panel._model_available.item(row)
        if item.data(Qt.ItemDataRole.UserRole) == "conn-remote":
            found_item = item
            break
    assert found_item is not None
    # The info icon (U+2139 INFORMATION SOURCE) should be in the label
    assert _INFO_ICON_CHAR in found_item.text()


def test_info_icon_tooltip_lists_other_groups(panel):
    """The tooltip for a multi-group connection lists the other groups."""
    panel.set_members([])
    found_item = None
    for row in range(panel._model_available.rowCount()):
        item = panel._model_available.item(row)
        if item.data(Qt.ItemDataRole.UserRole) == "conn-remote":
            found_item = item
            break
    assert found_item is not None
    assert "Other Group" in found_item.toolTip()


def test_no_info_icon_for_single_group_connection(panel):
    """conn-local is not in any other group → no info icon."""
    panel.set_members([])
    found_item = None
    for row in range(panel._model_available.rowCount()):
        item = panel._model_available.item(row)
        if item.data(Qt.ItemDataRole.UserRole) == "conn-local":
            found_item = item
            break
    assert found_item is not None
    assert _INFO_ICON_CHAR not in found_item.text()


def test_multiple_other_groups_in_tooltip(panel):
    """conn-custom is in Group A and Group B → both in tooltip."""
    panel.set_members([])
    found_item = None
    for row in range(panel._model_available.rowCount()):
        item = panel._model_available.item(row)
        if item.data(Qt.ItemDataRole.UserRole) == "conn-custom":
            found_item = item
            break
    assert found_item is not None
    tooltip = found_item.toolTip()
    assert "Group A" in tooltip
    assert "Group B" in tooltip


# ---------------------------------------------------------------------------
# Adding a connection that's already in another group is permitted
# ---------------------------------------------------------------------------


def test_add_multi_group_connection_permitted(panel):
    """conn-remote (already in 'Other Group') can be added to this group."""
    panel.set_members([])
    idx = None
    for row in range(panel._model_available.rowCount()):
        item = panel._model_available.item(row)
        if item.data(Qt.ItemDataRole.UserRole) == "conn-remote":
            idx = panel._model_available.index(row, 0)
            break
    assert idx is not None
    panel._list_available.setCurrentIndex(idx)
    panel._on_add_clicked()
    assert "conn-remote" in panel.get_members()


# ---------------------------------------------------------------------------
# Color picker
# ---------------------------------------------------------------------------


def test_color_picker_opens_dialog(qtbot):
    """Clicking btn_color invokes the color dialog factory."""
    dialog_opened = []

    def fake_color_dialog(initial: QColor) -> QColor:
        dialog_opened.append(initial.name())
        return QColor("#ff0000")

    dlg = _make_dlg(qtbot, color_dialog_factory=fake_color_dialog)
    dlg._btn_color.click()
    assert dialog_opened  # dialog was opened


def test_color_picker_updates_swatch(qtbot):
    """Selecting a color updates the button background and label."""

    def fake_color_dialog(initial: QColor) -> QColor:
        return QColor("#ff0000")

    dlg = _make_dlg(qtbot, color_dialog_factory=fake_color_dialog)
    dlg._btn_color.click()
    assert "#ff0000" in dlg._btn_color.styleSheet()
    assert dlg._lbl_color_value.text() == "#ff0000"
    assert dlg._current_color == "#ff0000"


def test_color_picker_cancel_keeps_old_color(qtbot):
    """Cancelling the color dialog (invalid color) keeps the current color."""

    def fake_color_dialog(initial: QColor) -> QColor:
        return QColor()  # invalid = cancelled

    dlg = _make_dlg(qtbot, color_dialog_factory=fake_color_dialog)
    original = dlg._current_color
    dlg._btn_color.click()
    assert dlg._current_color == original


# ---------------------------------------------------------------------------
# Populate from group_data
# ---------------------------------------------------------------------------


def test_populate_sets_name(qtbot):
    dlg = _make_dlg(qtbot, group_data=_GROUP_DATA, is_new=False)
    assert dlg._edit_name.text() == "Test Group"


def test_populate_sets_id(qtbot):
    dlg = _make_dlg(qtbot, group_data=_GROUP_DATA, is_new=False)
    assert dlg._edit_id.text() == "test-group"


def test_populate_sets_color(qtbot):
    dlg = _make_dlg(qtbot, group_data=_GROUP_DATA, is_new=False)
    assert dlg._current_color == "#3b82f6"


def test_populate_sets_members(qtbot):
    dlg = _make_dlg(qtbot, group_data=_GROUP_DATA, is_new=False)
    members = dlg._group_panel.get_members()
    assert "conn-local" in members
    assert "conn-ssh" in members


def test_populate_sets_sequential_order(qtbot):
    dlg = _make_dlg(qtbot, group_data=_GROUP_DATA, is_new=False)
    assert dlg._radio_sequential.isChecked()


def test_populate_sets_parallel_order(qtbot):
    data = {**_GROUP_DATA, "launch_order": "parallel"}
    dlg = _make_dlg(qtbot, group_data=data, is_new=False)
    assert dlg._radio_parallel.isChecked()


def test_populate_sets_delay(qtbot):
    dlg = _make_dlg(qtbot, group_data=_GROUP_DATA, is_new=False)
    assert dlg._spin_launch_delay_ms.value() == 200


def test_populate_sets_default_layout(qtbot):
    dlg = _make_dlg(qtbot, group_data=_GROUP_DATA, is_new=False)
    assert dlg._combo_default_layout.currentData() == "layout-main"


def test_populate_sets_isolation(qtbot):
    dlg = _make_dlg(qtbot, group_data=_GROUP_DATA, is_new=False)
    assert dlg._radio_shared.isChecked()


def test_populate_sets_per_group_isolation(qtbot):
    data = {**_GROUP_DATA, "isolation": "per-group"}
    dlg = _make_dlg(qtbot, group_data=data, is_new=False)
    assert dlg._radio_per_group.isChecked()


def test_populate_sets_layout_conflict(qtbot):
    dlg = _make_dlg(qtbot, group_data=_GROUP_DATA, is_new=False)
    assert dlg._combo_layout_conflict.currentData() == "move"


def test_populate_sets_auto_attach(qtbot):
    data = {**_GROUP_DATA, "auto_attach": True}
    dlg = _make_dlg(qtbot, group_data=data, is_new=False)
    assert dlg._chk_auto_attach.isChecked()


# ---------------------------------------------------------------------------
# ID auto-suggestion from name
# ---------------------------------------------------------------------------


def test_id_auto_suggested_from_name(qtbot):
    dlg = _make_dlg(qtbot, is_new=True)
    dlg._edit_name.setText("My New Group")
    dlg._on_name_changed("My New Group")
    assert dlg._edit_id.text() == "my-new-group"


def test_id_locked_when_editing(qtbot):
    dlg = _make_dlg(qtbot, group_data=_GROUP_DATA, is_new=False)
    assert dlg._edit_id.isReadOnly()


# ---------------------------------------------------------------------------
# Save validation
# ---------------------------------------------------------------------------


def test_save_disabled_empty_name(qtbot):
    dlg = _make_dlg(qtbot)
    dlg._edit_name.setText("")
    dlg._validate_name()
    assert not dlg._btn_save.isEnabled()


def test_save_disabled_invalid_id(qtbot):
    dlg = _make_dlg(qtbot)
    dlg._edit_name.setText("Good Name")
    dlg._edit_id.setText("BAD ID")
    dlg._validate_id()
    assert not dlg._btn_save.isEnabled()


def test_save_enabled_valid(qtbot):
    dlg = _make_dlg(qtbot)
    dlg._edit_name.setText("My Group")
    dlg._edit_id.setText("my-group")
    dlg._validate_name()
    dlg._validate_id()
    assert dlg._btn_save.isEnabled()


# ---------------------------------------------------------------------------
# Save round-trip → valid pydantic Group
# ---------------------------------------------------------------------------


def test_save_round_trip_valid_group(qtbot):
    """get_group_model() returns a Group that pydantic validates successfully."""
    dlg = _make_dlg(qtbot, group_data=_GROUP_DATA, is_new=False)
    group = dlg.get_group_model()
    assert isinstance(group, Group)
    assert group.id == "test-group"
    assert group.name == "Test Group"
    assert group.color == "#3b82f6"
    assert "conn-local" in group.members
    assert group.launch_order == "sequential"
    assert group.launch_delay_ms == 200
    assert group.isolation == "shared"
    assert group.layout_conflict == "move"


def test_save_round_trip_parallel_group(qtbot):
    data = {**_GROUP_DATA, "launch_order": "parallel", "id": "parallel-grp"}
    dlg = _make_dlg(qtbot, group_data=data, is_new=False)
    group = dlg.get_group_model()
    assert group.launch_order == "parallel"


def test_get_group_data_returns_dict(qtbot):
    dlg = _make_dlg(qtbot, group_data=_GROUP_DATA, is_new=False)
    data = dlg.get_group_data()
    assert isinstance(data, dict)
    assert data["id"] == "test-group"
    assert isinstance(data["members"], list)


def test_get_group_data_no_default_layout(qtbot):
    data = {**_GROUP_DATA, "default_layout_id": None}
    dlg = _make_dlg(qtbot, group_data=data, is_new=False)
    d = dlg.get_group_data()
    assert d["default_layout_id"] is None
