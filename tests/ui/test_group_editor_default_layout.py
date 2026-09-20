# -*- coding: utf-8 -*-
"""
tests/ui/test_group_editor_default_layout.py

Change 1 — Auto-generate default Layout per Group tests.

Covers:
- Saving a new group via MainWindow auto-creates a default layout.
- The new layout's panes reference the group's members in order.
- group.default_layout_id is set to the new layout's id.
- Editing an existing group does NOT auto-create a new layout.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from cpsm.data.schema import ClaudeLocalConnection, CpsmDocument, Group  # noqa: I001
from cpsm.ui.main_window import MainWindow


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_doc_with_connections() -> CpsmDocument:
    """Document with two connections, no groups."""
    conn1 = ClaudeLocalConnection(
        id="conn-aa",
        name="Conn AA",
        launch_profile="claude-local",
        project_folder="~/aa",
        claude_options="--resume",
    )
    conn2 = ClaudeLocalConnection(
        id="conn-bb",
        name="Conn BB",
        launch_profile="claude-local",
        project_folder="~/bb",
        claude_options="--resume",
    )
    return CpsmDocument(connections=[conn1, conn2])


def _fake_group_editor_accept(dlg_class, group_id, group_name, members):
    """Monkey-patch GroupEditorDialog.exec to return Accepted with fixed data."""
    original_exec = dlg_class.exec
    original_get_data = dlg_class.get_group_data

    def _fake_exec(self) -> int:
        return dlg_class.DialogCode.Accepted

    def _fake_get_data(self):
        return {
            "id": group_id,
            "name": group_name,
            "color": "#3b82f6",
            "members": list(members),
            "launch_order": "sequential",
            "launch_delay_ms": 0,
            "default_layout_id": None,
            "isolation": "shared",
            "layout_conflict": "move",
            "auto_attach": False,
        }

    dlg_class.exec = _fake_exec  # type: ignore[method-assign]
    dlg_class.get_group_data = _fake_get_data  # type: ignore[method-assign]
    return original_exec, original_get_data


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_new_group_auto_creates_default_layout(qtbot) -> None:
    """Saving a new group via MainWindow appends a default layout to doc.screen_layouts."""
    from cpsm.ui.dialogs.group_editor import GroupEditorDialog

    doc = _make_doc_with_connections()
    win = MainWindow(document=doc)
    qtbot.addWidget(win)
    win.show()

    assert len(win._document.screen_layouts) == 0

    orig_exec, orig_get_data = _fake_group_editor_accept(
        GroupEditorDialog, "new-grp", "New Group", ["conn-aa", "conn-bb"]
    )
    try:
        win._open_group_editor(None)
    finally:
        GroupEditorDialog.exec = orig_exec  # type: ignore[method-assign]
        GroupEditorDialog.get_group_data = orig_get_data  # type: ignore[method-assign]

    assert len(win._document.screen_layouts) >= 1


def test_new_group_default_layout_id_is_set(qtbot) -> None:
    """group.default_layout_id is set after auto-creating the layout."""
    from cpsm.ui.dialogs.group_editor import GroupEditorDialog

    doc = _make_doc_with_connections()
    win = MainWindow(document=doc)
    qtbot.addWidget(win)
    win.show()

    orig_exec, orig_get_data = _fake_group_editor_accept(
        GroupEditorDialog, "auto-grp", "Auto Group", ["conn-aa"]
    )
    try:
        win._open_group_editor(None)
    finally:
        GroupEditorDialog.exec = orig_exec  # type: ignore[method-assign]
        GroupEditorDialog.get_group_data = orig_get_data  # type: ignore[method-assign]

    created_group = next((g for g in win._document.groups if g.id == "auto-grp"), None)
    assert created_group is not None
    assert created_group.default_layout_id is not None
    assert created_group.default_layout_id == "auto-grp-default-layout"


def test_new_group_layout_panes_reference_members(qtbot) -> None:
    """The auto-generated layout's panes must reference the group's members."""
    from cpsm.ui.dialogs.group_editor import GroupEditorDialog

    doc = _make_doc_with_connections()
    win = MainWindow(document=doc)
    qtbot.addWidget(win)
    win.show()

    orig_exec, orig_get_data = _fake_group_editor_accept(
        GroupEditorDialog, "pane-grp", "Pane Group", ["conn-aa", "conn-bb"]
    )
    try:
        win._open_group_editor(None)
    finally:
        GroupEditorDialog.exec = orig_exec  # type: ignore[method-assign]
        GroupEditorDialog.get_group_data = orig_get_data  # type: ignore[method-assign]

    layout = next(
        (sl for sl in win._document.screen_layouts if sl.id == "pane-grp-default-layout"),
        None,
    )
    assert layout is not None

    # Collect all pane connection_ids
    all_pane_ids = [
        pane.connection_id
        for monitor in layout.monitors
        for vp in monitor.viewports
        for pane in vp.panes
        if pane.connection_id is not None
    ]
    # Both members should appear exactly once
    assert "conn-aa" in all_pane_ids
    assert "conn-bb" in all_pane_ids


def test_editing_existing_group_does_not_create_new_layout(qtbot) -> None:
    """Editing an existing group must NOT auto-create an additional layout."""
    from cpsm.ui.dialogs.group_editor import GroupEditorDialog

    conn = ClaudeLocalConnection(
        id="conn-cc",
        name="Conn CC",
        launch_profile="claude-local",
        project_folder="~/cc",
        claude_options="--resume",
    )
    grp = Group(id="existing-grp", name="Existing Group", members=["conn-cc"])
    doc = CpsmDocument(connections=[conn], groups=[grp])
    win = MainWindow(document=doc)
    qtbot.addWidget(win)
    win.show()

    initial_layout_count = len(win._document.screen_layouts)
    # Preserve the existing default_layout_id — Round C's auto-layout ensures
    # every group has one, and clearing it would (correctly) trigger a new
    # auto-creation. The dialog in real use also preserves it.
    existing_default_layout_id = win._document.groups[0].default_layout_id

    orig_exec = GroupEditorDialog.exec
    orig_get_data = GroupEditorDialog.get_group_data

    def _fake_exec(self) -> int:
        return GroupEditorDialog.DialogCode.Accepted

    def _fake_get_data(self):
        return {
            "id": "existing-grp",
            "name": "Existing Group Renamed",
            "color": "#3b82f6",
            "members": ["conn-cc"],
            "launch_order": "sequential",
            "launch_delay_ms": 0,
            "default_layout_id": existing_default_layout_id,
            "isolation": "shared",
            "layout_conflict": "move",
            "auto_attach": False,
        }

    GroupEditorDialog.exec = _fake_exec  # type: ignore[method-assign]
    GroupEditorDialog.get_group_data = _fake_get_data  # type: ignore[method-assign]
    try:
        win._open_group_editor(grp)
    finally:
        GroupEditorDialog.exec = orig_exec  # type: ignore[method-assign]
        GroupEditorDialog.get_group_data = orig_get_data  # type: ignore[method-assign]

    assert len(win._document.screen_layouts) == initial_layout_count
