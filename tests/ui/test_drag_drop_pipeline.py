# -*- coding: utf-8 -*-
"""
tests/ui/test_drag_drop_pipeline.py

Tests for the drag-drop pipeline improvements:
  1. test_members_list_uses_subclass_startdrag
  2. test_pane_drop_swap_in_preview
  3. test_pane_drop_split_appends_in_preview
  4. test_members_list_greys_out_placed_connections
  5. test_members_list_greyed_items_still_draggable
"""

from __future__ import annotations

import os
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QListWidget, QRadioButton

from cpsm.data.schema import (
    ClaudeLocalConnection,
    CpsmDocument,
    GeometryPct,
    Group,
    Monitor,
    Pane,
    ScreenLayout,
    Viewport,
)
from cpsm.ui.main_window import MainWindow, _MembersListWidget

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_conn(cid: str) -> ClaudeLocalConnection:
    return ClaudeLocalConnection(
        id=cid,
        name=cid,
        launch_profile="claude-local",
        project_folder=f"~/{cid}",
        claude_options="--resume",
    )


def _make_layout_two_panes(
    lid: str,
    conn_a: str,
    conn_b: str,
    vp_id: str = "vp-test",
) -> ScreenLayout:
    """One monitor, one viewport, two panes."""
    pane_a = Pane(connection_id=conn_a)
    pane_b = Pane(connection_id=conn_b)
    vp = Viewport(
        id=vp_id,
        geometry_pct=GeometryPct(x=0, y=0, w=100, h=100),
        tmux_layout="tiled",
        panes=[pane_a, pane_b],
    )
    return ScreenLayout(id=lid, name=lid, monitors=[Monitor(viewports=[vp])])


def _make_layout_two_panes_two_viewports(
    lid: str,
    conn_a: str,
    conn_b: str,
    vp_a_id: str = "vp-a",
    vp_b_id: str = "vp-b",
) -> ScreenLayout:
    """One monitor, two viewports, one pane each."""
    vp_a = Viewport(
        id=vp_a_id,
        geometry_pct=GeometryPct(x=0, y=0, w=50, h=100),
        tmux_layout="tiled",
        panes=[Pane(connection_id=conn_a)],
    )
    vp_b = Viewport(
        id=vp_b_id,
        geometry_pct=GeometryPct(x=50, y=0, w=50, h=100),
        tmux_layout="tiled",
        panes=[Pane(connection_id=conn_b)],
    )
    return ScreenLayout(id=lid, name=lid, monitors=[Monitor(viewports=[vp_a, vp_b])])


def _open_win(qtbot: Any, doc: CpsmDocument) -> MainWindow:
    win = MainWindow(document=doc)
    qtbot.addWidget(win)
    win.show()
    QApplication.processEvents()
    return win


def _switch_to_preview(win: MainWindow) -> None:
    radio = win.findChild(QRadioButton, "radio_screens_preview")
    assert radio is not None, "radio_screens_preview not found"
    radio.setChecked(True)
    QApplication.processEvents()


# ---------------------------------------------------------------------------
# Test 1: members list uses the proper subclass (not a bare QListWidget)
# ---------------------------------------------------------------------------


class TestMembersListUsesSubclassStartDrag:
    def test_members_list_uses_subclass_startdrag(self, qtbot: Any) -> None:
        """The group members list must be a _MembersListWidget instance, not
        a bare QListWidget, so that startDrag is honoured by Qt's virtual
        dispatch rather than a Python-level instance-method monkey-patch."""
        conn_a = _make_conn("conn-a")
        grp = Group(id="grp-t", name="Group T", members=["conn-a"])
        doc = CpsmDocument(connections=[conn_a], groups=[grp])

        win = _open_win(qtbot, doc)

        lst = win.findChild(QListWidget, "list_screens_group_members")
        assert lst is not None, "list_screens_group_members widget not found"

        # Must be the subclass — not a bare QListWidget
        assert isinstance(lst, _MembersListWidget), (
            f"Expected _MembersListWidget, got {type(lst).__name__}"
        )
        # _MembersListWidget IS a QListWidget, so this is always True —
        # but the assertion above is the key check
        assert isinstance(lst, QListWidget)


# ---------------------------------------------------------------------------
# Test 2: pane-drop swap in Preview mode
# ---------------------------------------------------------------------------


class TestPaneDropMoveInPreview:
    def test_pane_drop_move_in_preview(self, qtbot: Any) -> None:
        """Emitting drop_pane_requested with zone='center' must MOVE the
        source pane onto the destination's slot — destination's connection
        is freed (returns to the list); source pane is removed from its
        viewport. Net result: one pane with src's connection_id."""
        layout = _make_layout_two_panes("lay-swap", "conn-a", "conn-b")
        conn_a = _make_conn("conn-a")
        conn_b = _make_conn("conn-b")
        grp = Group(
            id="grp-s",
            name="Group S",
            members=["conn-a", "conn-b"],
            default_layout_id="lay-swap",
        )
        doc = CpsmDocument(
            connections=[conn_a, conn_b],
            groups=[grp],
            screen_layouts=[layout],
        )

        win = _open_win(qtbot, doc)
        _switch_to_preview(win)
        QApplication.processEvents()

        save_calls: list[int] = []
        win._save_document = lambda: save_calls.append(1)  # type: ignore[method-assign]

        # Emit drop_pane_requested: drop pane "conn-a" onto pane "conn-b" center
        win._screen_map_widget.drop_pane_requested.emit("conn-a", "conn-b", "center", 0)
        QApplication.processEvents()

        updated_layout = win._document.screen_layouts[0]
        panes = updated_layout.monitors[0].viewports[0].panes
        # MOVE-replace: src removed; dst now carries src's connection_id.
        # Net: one pane in the viewport with conn-a; conn-b is freed.
        assert len(panes) == 1, (
            f"Expected 1 pane after MOVE-replace, got {len(panes)}: "
            f"{[p.connection_id for p in panes]}"
        )
        assert panes[0].connection_id == "conn-a", (
            f"Expected dst pane to carry conn-a after MOVE, got {panes[0].connection_id!r}"
        )
        assert len(save_calls) >= 1, "Expected _save_document to be called"


# ---------------------------------------------------------------------------
# Test 3: pane-drop split appends in Preview mode
# ---------------------------------------------------------------------------


class TestPaneDropSplitAppendsInPreview:
    def test_pane_drop_split_appends_in_preview(self, qtbot: Any) -> None:
        """Emitting drop_pane_requested with an edge zone must INSERT a new
        pane in the destination viewport with the source's connection_id.

        Setup: two panes (conn-a, conn-b) in different viewports on one monitor.
        Drop conn-a onto conn-b with zone='right'.
        Expected: vp-b (containing conn-b) gains a second pane with conn-a.
        """
        layout = _make_layout_two_panes_two_viewports(
            "lay-split", "conn-a", "conn-b", vp_a_id="vp-a", vp_b_id="vp-b"
        )
        conn_a = _make_conn("conn-a")
        conn_b = _make_conn("conn-b")
        grp = Group(
            id="grp-sp",
            name="Group SP",
            members=["conn-a", "conn-b"],
            default_layout_id="lay-split",
        )
        doc = CpsmDocument(
            connections=[conn_a, conn_b],
            groups=[grp],
            screen_layouts=[layout],
        )

        win = _open_win(qtbot, doc)
        _switch_to_preview(win)
        QApplication.processEvents()

        save_calls: list[int] = []
        win._save_document = lambda: save_calls.append(1)  # type: ignore[method-assign]

        # Emit drop_pane_requested: split conn-a into conn-b's viewport (right edge)
        win._screen_map_widget.drop_pane_requested.emit("conn-a", "conn-b", "right", 0)
        QApplication.processEvents()

        updated_layout = win._document.screen_layouts[0]
        # MOVE semantics: vp-a should be empty (conn-a moved out); vp-b has both.
        vp_a_panes = updated_layout.monitors[0].viewports[0].panes
        vp_b_panes = updated_layout.monitors[0].viewports[1].panes
        assert len(vp_a_panes) == 0, (
            f"Expected vp-a to be empty after MOVE, got {len(vp_a_panes)}: "
            f"{[p.connection_id for p in vp_a_panes]}"
        )
        assert len(vp_b_panes) == 2, (
            f"Expected vp-b to have 2 panes after split-move, got {len(vp_b_panes)}: "
            f"{[p.connection_id for p in vp_b_panes]}"
        )
        vp_b_conn_ids = {p.connection_id for p in vp_b_panes}
        assert vp_b_conn_ids == {"conn-a", "conn-b"}, (
            f"vp-b should contain both conn-a and conn-b, got {vp_b_conn_ids}"
        )

        assert len(save_calls) >= 1, "Expected _save_document to be called"


# ---------------------------------------------------------------------------
# Test 4: members list greys out placed connections
# ---------------------------------------------------------------------------


class TestMembersListGreysOutPlacedConnections:
    def test_members_list_greys_out_placed_connections(self, qtbot: Any) -> None:
        """After _refresh_screens_members_list, items for connections already
        placed on the canvas must have a grey foreground color; items not placed
        must retain the default (unset / not-grey) foreground."""
        pane_a = Pane(connection_id="conn-a")
        pane_b = Pane(connection_id="conn-b")
        vp = Viewport(
            id="vp-test",
            geometry_pct=GeometryPct(x=0, y=0, w=100, h=100),
            tmux_layout="tiled",
            panes=[pane_a, pane_b],
        )
        layout = ScreenLayout(
            id="lay-grey",
            name="Grey Test",
            monitors=[Monitor(viewports=[vp])],
        )
        conn_a = _make_conn("conn-a")
        conn_b = _make_conn("conn-b")
        conn_c = _make_conn("conn-c")
        grp = Group(
            id="grp-g",
            name="Group G",
            members=["conn-a", "conn-b", "conn-c"],
            default_layout_id="lay-grey",
        )
        doc = CpsmDocument(
            connections=[conn_a, conn_b, conn_c],
            groups=[grp],
            screen_layouts=[layout],
        )

        win = _open_win(qtbot, doc)
        _switch_to_preview(win)
        QApplication.processEvents()

        lst = win.findChild(QListWidget, "list_screens_group_members")
        assert lst is not None
        assert lst.count() == 3, f"Expected 3 items, got {lst.count()}"

        _grey_hex = "#94a3b8"

        items_by_id: dict[str, Any] = {}
        for i in range(lst.count()):
            item = lst.item(i)
            cid = item.data(Qt.ItemDataRole.UserRole)
            items_by_id[cid] = item

        # conn-a and conn-b are placed → grey foreground
        for placed_id in ("conn-a", "conn-b"):
            item = items_by_id[placed_id]
            fg = item.foreground().color()
            assert fg.name().lower() == _grey_hex.lower(), (
                f"Expected grey foreground for placed '{placed_id}', "
                f"got {fg.name()!r}"
            )
            assert "Already on canvas" in item.toolTip(), (
                f"Expected 'Already on canvas' marker in tooltip for '{placed_id}', "
                f"got {item.toolTip()!r}"
            )

        # conn-c is NOT placed → no grey override (color should not be #94a3b8)
        item_c = items_by_id["conn-c"]
        fg_c = item_c.foreground().color()
        assert fg_c.name().lower() != _grey_hex.lower(), (
            f"conn-c should not have grey foreground, got {fg_c.name()!r}"
        )
        assert "(already placed)" not in item_c.toolTip()


# ---------------------------------------------------------------------------
# Test 5: greyed items remain draggable
# ---------------------------------------------------------------------------


class TestMembersListGreyedItemsStillDraggable:
    def test_members_list_greyed_items_still_draggable(self, qtbot: Any) -> None:
        """Greyed (already-placed) items remain draggable so the user can
        re-place them on a different pane. Greying is a visual hint, not a
        functional restriction."""
        pane_a = Pane(connection_id="conn-a")
        vp = Viewport(
            id="vp-drag",
            geometry_pct=GeometryPct(x=0, y=0, w=100, h=100),
            tmux_layout="tiled",
            panes=[pane_a],
        )
        layout = ScreenLayout(
            id="lay-drag",
            name="Drag Test",
            monitors=[Monitor(viewports=[vp])],
        )
        conn_a = _make_conn("conn-a")
        grp = Group(
            id="grp-d",
            name="Group D",
            members=["conn-a"],
            default_layout_id="lay-drag",
        )
        doc = CpsmDocument(
            connections=[conn_a],
            groups=[grp],
            screen_layouts=[layout],
        )

        win = _open_win(qtbot, doc)
        _switch_to_preview(win)
        QApplication.processEvents()

        lst = win.findChild(QListWidget, "list_screens_group_members")
        assert lst is not None
        assert lst.count() == 1

        item = lst.item(0)
        assert item.data(Qt.ItemDataRole.UserRole) == "conn-a"

        flags = item.flags()
        assert flags & Qt.ItemFlag.ItemIsEnabled, (
            "Greyed item must still have ItemIsEnabled"
        )
        assert flags & Qt.ItemFlag.ItemIsDragEnabled, (
            "Greyed (already-placed) item must remain draggable"
        )
