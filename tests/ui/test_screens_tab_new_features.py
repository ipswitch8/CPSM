# -*- coding: utf-8 -*-
"""
tests/ui/test_screens_tab_new_features.py

Tests for Screens-tab additions:
  A. Group-members drag source list
  B. "New Layout" button
  C. Right-click context menu on canvas
  D. Save button (Preview persist + Live push)
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QMimeData, QPoint, Qt
from PySide6.QtWidgets import (
    QApplication,
    QListWidget,
    QMenu,
    QPushButton,
    QRadioButton,
)

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
from cpsm.ui.main_window import MainWindow
from cpsm.ui.widgets.screen_map import MIME_CONNECTION_ID

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_conn(cid: str, name: str | None = None) -> ClaudeLocalConnection:
    return ClaudeLocalConnection(
        id=cid,
        name=name or cid,
        launch_profile="claude-local",
        project_folder=f"~/{cid}",
        claude_options="--resume",
    )


def _make_layout(lid: str, name: str = "layout") -> ScreenLayout:
    vp = Viewport(id=f"vp-{lid}", geometry_pct=GeometryPct(x=0, y=0, w=100, h=100), panes=[])
    return ScreenLayout(id=lid, name=name, monitors=[Monitor(viewports=[vp])])


def _make_doc(*, with_group: bool = True, with_layout: bool = True) -> CpsmDocument:
    conn_a = _make_conn("conn-a", "Alpha")
    conn_b = _make_conn("conn-b", "Beta")
    layout = _make_layout("grp-x-default-layout", "Group X default") if with_layout else None
    grp = (
        Group(
            id="grp-x",
            name="Group X",
            members=["conn-a", "conn-b"],
            default_layout_id="grp-x-default-layout" if with_layout else None,
        )
        if with_group
        else None
    )
    return CpsmDocument(
        connections=[conn_a, conn_b],
        groups=[grp] if grp else [],
        screen_layouts=[layout] if layout else [],
    )


def _open_win(qtbot: Any, doc: CpsmDocument | None = None) -> MainWindow:
    win = MainWindow(document=doc or _make_doc())
    qtbot.addWidget(win)
    win.show()
    QApplication.processEvents()
    return win


def _switch_to_preview(win: MainWindow) -> None:
    """Switch the Screens tab to Preview mode and process events."""
    radio = win.findChild(QRadioButton, "radio_screens_preview")
    assert radio is not None, "radio_screens_preview not found"
    radio.setChecked(True)
    QApplication.processEvents()


# ---------------------------------------------------------------------------
# A. Group-members drag source list
# ---------------------------------------------------------------------------


class TestGroupMembersList:
    def test_list_has_correct_object_name(self, qtbot: Any) -> None:
        win = _open_win(qtbot)
        lst = win.findChild(QListWidget, "list_screens_group_members")
        assert lst is not None, "list_screens_group_members not found"

    def test_list_populates_on_group_select(self, qtbot: Any) -> None:
        """Selecting a group in Preview mode populates the members list."""
        win = _open_win(qtbot)
        _switch_to_preview(win)

        lst = win.findChild(QListWidget, "list_screens_group_members")
        assert lst is not None

        # After switching to Preview and group auto-selected, list should have items
        assert lst.count() == 2, f"Expected 2 members, got {lst.count()}"

        ids = {lst.item(i).data(Qt.ItemDataRole.UserRole) for i in range(lst.count())}
        assert "conn-a" in ids
        assert "conn-b" in ids

    def test_preview_is_default_mode_at_startup(self, qtbot: Any) -> None:
        """Round A removed the Live/Preview toggle from the UI; Preview is
        always the active mode."""
        win = _open_win(qtbot)
        radio_preview = win.findChild(QRadioButton, "radio_screens_preview")
        assert radio_preview is not None
        assert radio_preview.isChecked()

    def test_list_drag_emits_connection_mime(self, qtbot: Any) -> None:
        """Drag from member list uses MIME_CONNECTION_ID with the correct connection id."""
        win = _open_win(qtbot)
        _switch_to_preview(win)
        QApplication.processEvents()

        lst = win.findChild(QListWidget, "list_screens_group_members")
        assert lst is not None
        assert lst.count() > 0

        # Select the first item
        lst.setCurrentRow(0)
        selected_id = lst.currentItem().data(Qt.ItemDataRole.UserRole)

        # Capture the drag that _on_members_list_start_drag creates
        captured: list[QMimeData] = []

        class _MockDrag:
            def __init__(self, parent: Any) -> None:
                pass

            def setMimeData(self, mime: QMimeData) -> None:
                captured.append(mime)

            def exec(self, actions: Any) -> None:
                pass

        with patch("cpsm.ui.main_window.QDrag", _MockDrag):
            win._on_members_list_start_drag(lst)

        assert len(captured) == 1, "QDrag.setMimeData was not called"
        mime = captured[0]
        assert mime.hasFormat(MIME_CONNECTION_ID)
        payload = mime.data(MIME_CONNECTION_ID).toStdString()
        assert payload == selected_id

    def test_list_repopulates_on_group_change(self, qtbot: Any) -> None:
        """Changing the group combo repopulates the members list."""
        conn_c = _make_conn("conn-c", "Gamma")
        grp2 = Group(id="grp-y", name="Group Y", members=["conn-c"])
        doc = _make_doc()
        doc.connections.append(conn_c)
        doc.groups.append(grp2)

        win = _open_win(qtbot, doc)
        _switch_to_preview(win)
        QApplication.processEvents()

        combo = win._combo_screens_group
        lst = win.findChild(QListWidget, "list_screens_group_members")
        assert lst is not None

        # Switch to grp-y
        idx = combo.findData("grp-y")
        assert idx >= 0
        combo.setCurrentIndex(idx)
        QApplication.processEvents()

        assert lst.count() == 1
        assert lst.item(0).data(Qt.ItemDataRole.UserRole) == "conn-c"


# ---------------------------------------------------------------------------
# B. "New Layout" button
# ---------------------------------------------------------------------------


class TestNewLayoutButton:
    def test_button_exists_and_has_correct_name(self, qtbot: Any) -> None:
        win = _open_win(qtbot)
        btn = win.findChild(QPushButton, "button_screens_new_layout")
        assert btn is not None

    def test_button_hidden_at_startup(self, qtbot: Any) -> None:
        # Round B removed the New Layout button from the visible UI — the
        # auto-generated default layout per group covers the same need.
        # The widget still exists so tests / programmatic flows that click()
        # it keep working.
        win = _open_win(qtbot)
        btn = win.findChild(QPushButton, "button_screens_new_layout")
        assert btn is not None
        assert btn.isHidden()

    def test_auto_layout_creates_and_persists(self, qtbot: Any) -> None:
        """Round C replaced the New Layout button with auto-create-on-load:
        opening a window for a group with no layout must produce one
        automatically and register it as the group's default."""
        conn_a = _make_conn("conn-a", "Alpha")
        grp = Group(id="grp-n", name="Group N", members=["conn-a"])
        doc = CpsmDocument(connections=[conn_a], groups=[grp], screen_layouts=[])

        win = _open_win(qtbot, doc)
        QApplication.processEvents()

        # Auto-layout produced exactly one layout for the group
        assert len(win._document.screen_layouts) == 1
        new_layout = win._document.screen_layouts[0]
        assert "Group N" in new_layout.name

        # Group got default_layout_id set
        grp_updated = next(g for g in win._document.groups if g.id == "grp-n")
        assert grp_updated.default_layout_id == new_layout.id

    def test_new_layout_no_group_shows_statusbar_message(self, qtbot: Any) -> None:
        """If no group is selected, status bar shows a message and no layout is created."""
        win = _open_win(qtbot, CpsmDocument())  # no groups
        _switch_to_preview(win)
        QApplication.processEvents()

        btn = win.findChild(QPushButton, "button_screens_new_layout")
        assert btn is not None

        initial_layout_count = len(win._document.screen_layouts)
        btn.click()
        QApplication.processEvents()

        # No new layouts added
        assert len(win._document.screen_layouts) == initial_layout_count

    def test_new_layout_unique_name_on_collision(self, qtbot: Any) -> None:
        """When a 'Default — <Group>' layout already exists, a numbered variant is used."""
        conn_a = _make_conn("conn-a", "Alpha")
        existing = ScreenLayout(
            id="grp-m-default-layout",
            name="Default — Group M",
            monitors=[],
        )
        grp = Group(
            id="grp-m",
            name="Group M",
            members=["conn-a"],
            default_layout_id="grp-m-default-layout",
        )
        doc = CpsmDocument(connections=[conn_a], groups=[grp], screen_layouts=[existing])

        win = _open_win(qtbot, doc)
        _switch_to_preview(win)
        QApplication.processEvents()

        win._save_document = MagicMock()  # type: ignore[method-assign]

        btn = win.findChild(QPushButton, "button_screens_new_layout")
        assert btn is not None
        btn.click()
        QApplication.processEvents()

        # Should have both old and new layout
        assert len(win._document.screen_layouts) == 2
        new_name = win._document.screen_layouts[1].name
        assert "Default — Group M (2)" == new_name


# ---------------------------------------------------------------------------
# C. Right-click context menu on canvas
# ---------------------------------------------------------------------------


class TestCanvasContextMenu:
    """Test that the canvas right-click menu is wired and builds correct menus."""

    def _make_doc_with_monitor_layout(self) -> CpsmDocument:
        """A doc where layout has one monitor with a viewport."""
        vp = Viewport(
            id="vp-test",
            geometry_pct=GeometryPct(x=0, y=0, w=100, h=100),
            panes=[Pane(connection_id="conn-a")],
        )
        mon = Monitor(viewports=[vp])
        layout = ScreenLayout(id="test-layout", name="Test", monitors=[mon])
        conn = _make_conn("conn-a", "Alpha")
        grp = Group(id="grp-t", name="Group T", members=["conn-a"], default_layout_id="test-layout")
        return CpsmDocument(connections=[conn], groups=[grp], screen_layouts=[layout])

    def test_context_menu_policy_is_custom(self, qtbot: Any) -> None:
        """The screen map view has CustomContextMenu policy set."""
        win = _open_win(qtbot)
        assert win._screen_map_widget.view.contextMenuPolicy() == (
            Qt.ContextMenuPolicy.CustomContextMenu
        )

    def test_empty_canvas_context_menu_offers_redetect_screens(self, qtbot: Any) -> None:
        """Right-click on empty canvas offers "Re-detect Screens".

        Supersedes the earlier round-late behaviour, where an empty-canvas
        right-click was swallowed and produced no menu at all. That left the
        user with no way to ask for a re-detect from the one place they are
        looking when a display is missing from the map. The pane-only 'Clear
        Pane' action is still absent here — see
        ``test_pane_context_menu_still_offers_clear_pane``.
        """
        win = _open_win(qtbot, CpsmDocument())
        QApplication.processEvents()

        menus_shown: list[QMenu] = []

        def _capture_exec(menu: QMenu, pos: Any) -> None:
            menus_shown.append(menu)

        win._exec_menu = _capture_exec  # type: ignore[method-assign]

        win._on_screens_canvas_context_menu(QPoint(10, 10))
        QApplication.processEvents()

        assert len(menus_shown) == 1, "empty-canvas right-click must produce a menu"
        actions = menus_shown[0].actions()
        names = [a.objectName() for a in actions]
        assert "action_screens_redetect" in names, names
        assert "action_screens_clear_pane" not in names, names

    def test_redetect_action_is_automation_friendly(self, qtbot: Any) -> None:
        """Stable objectName plus tool-tip/status-tip, per project convention
        for pywinauto/WinAppDriver/FlaUI-driven testing. QAction has no
        setAccessibleName — its accessible name is derived from text()."""
        win = _open_win(qtbot, CpsmDocument())
        QApplication.processEvents()

        menus_shown: list[QMenu] = []
        win._exec_menu = lambda menu, pos: menus_shown.append(menu)  # type: ignore[method-assign]
        win._on_screens_canvas_context_menu(QPoint(10, 10))
        QApplication.processEvents()

        act = next(
            a for a in menus_shown[0].actions() if a.objectName() == "action_screens_redetect"
        )
        assert act.text() == "Re-detect Screens"
        assert "displays" in act.toolTip().lower()
        assert act.statusTip() == act.toolTip()

    def test_redetect_action_invokes_widget_redetect(self, qtbot: Any) -> None:
        """Triggering the action goes through ScreenMapWidget.redetect_screens,
        the same entry point the launch and hot-plug triggers use."""
        win = _open_win(qtbot, CpsmDocument())
        QApplication.processEvents()

        calls: list[bool] = []
        win._screen_map_widget.redetect_screens = (  # type: ignore[method-assign]
            lambda: (calls.append(True), False)[1]
        )

        menus_shown: list[QMenu] = []
        win._exec_menu = lambda menu, pos: menus_shown.append(menu)  # type: ignore[method-assign]
        win._on_screens_canvas_context_menu(QPoint(10, 10))
        QApplication.processEvents()

        act = next(
            a for a in menus_shown[0].actions() if a.objectName() == "action_screens_redetect"
        )
        act.trigger()
        QApplication.processEvents()

        assert calls == [True]

    def test_pane_context_menu_still_offers_clear_pane(self, qtbot: Any) -> None:
        """Regression guard: adding the re-detect action must not displace the
        existing pane menu."""
        win = _open_win(qtbot, self._make_doc_with_monitor_layout())
        QApplication.processEvents()

        menus_shown: list[QMenu] = []
        win._exec_menu = lambda menu, pos: menus_shown.append(menu)  # type: ignore[method-assign]

        # _open_win wires no services, so _query_live_monitors() would return
        # [] and nothing would render. Give the window a monitor so both the
        # render and the handler's hit-test see the same single display.
        from types import SimpleNamespace

        from cpsm.services.monitor_service import MonitorInfo

        mon = MonitorInfo(
            identifier="test-mon",
            name="OFFSCREEN-1",
            geometry=(0, 0, 1920, 1080),
            available_geometry=(0, 0, 1920, 1040),
            physical_size_mm=(527.0, 296.0),
            device_pixel_ratio=1.0,
            orientation="landscape",
            manufacturer="",
            model="",
            serial="",
            qt_index=0,
        )
        win._services = SimpleNamespace(
            monitor_service=SimpleNamespace(snapshot=lambda: [mon])
        )

        # Render the document's layout on the canvas. The handler reads the
        # canvas layout back via _cmx_get_layout(), so this is the same state
        # a user would have after selecting the group in the Screens tab.
        win._screen_map_widget.set_layout(
            win._document.screen_layouts[0], win._query_live_monitors()
        )
        QApplication.processEvents()

        # Take the pane's own scene rect from the registry the renderer built,
        # then map its centre back to view coordinates — far more robust than
        # sweeping the viewport, which depends on the offscreen widget size.
        registry = win._screen_map_widget.pane_registry
        assert registry, "the layout's connection-bearing pane should be in the registry"
        rec = registry[0]
        view = win._screen_map_widget.view
        hit_pos = view.mapFromScene(
            rec.scene_x + rec.scene_w / 2.0, rec.scene_y + rec.scene_h / 2.0
        )

        win._on_screens_canvas_context_menu(hit_pos)
        QApplication.processEvents()

        assert len(menus_shown) == 1
        names = [a.objectName() for a in menus_shown[0].actions()]
        assert "action_screens_clear_pane" in names, names
        assert "action_screens_redetect" in names, names

    def test_monitor_context_menu_builds_add_viewport_action(self, qtbot: Any) -> None:
        """Right-click on a monitor rect builds the 'Add viewport' menu."""
        from cpsm.data.schema import Monitor as SchemaMonitor
        from cpsm.ui.widgets.screen_map_context_menu import ScreenMapContextMenuMixin

        mon = SchemaMonitor(identifier="m0", monitor_index_hint=0, viewports=[])

        # Use the mixin directly (it doesn't depend on 'self' for build-monitor-menu)
        mixin = ScreenMapContextMenuMixin()
        menu = mixin._cmx_build_monitor_menu(mon)

        action_names = [a.objectName() for a in menu.actions() if not a.isSeparator()]
        assert "action_screens_add_viewport_full" in action_names, action_names


# ---------------------------------------------------------------------------
# D. Save button
# ---------------------------------------------------------------------------


class TestSaveButton:
    def test_save_in_preview_mode_calls_save_document(self, qtbot: Any) -> None:
        """Clicking Save in Preview mode persists the canvas layout."""
        win = _open_win(qtbot)
        _switch_to_preview(win)
        QApplication.processEvents()

        saved_calls: list[int] = []
        win._save_document = lambda: saved_calls.append(1)  # type: ignore[method-assign]

        # Set a non-None layout on the canvas so the save path proceeds
        layout = _make_layout("test-save-layout", "Save Test")
        win._screen_map_widget.set_layout(layout, [])
        QApplication.processEvents()

        btn = win.findChild(QPushButton, "btn_screens_save")
        assert btn is not None
        btn.click()
        QApplication.processEvents()

        assert len(saved_calls) >= 1, "save_document was not called"

    def test_save_in_live_mode_calls_layout_controller(self, qtbot: Any) -> None:
        """Clicking Save in Live mode also calls _screens_push_live_layout."""
        win = _open_win(qtbot)
        # Round A removed the Live toggle from the UI; programmatically force
        # Live to exercise the live-push path that still exists internally.
        win._radio_screens_live.setChecked(True)

        layout = _make_layout("test-live-layout", "Live Test")
        win._screen_map_widget.set_layout(layout, [])
        QApplication.processEvents()

        push_calls: list[Any] = []
        win._screens_push_live_layout = lambda cl: push_calls.append(cl)  # type: ignore[method-assign]
        win._save_document = lambda: None  # type: ignore[method-assign]

        btn = win.findChild(QPushButton, "btn_screens_save")
        assert btn is not None
        btn.click()
        QApplication.processEvents()

        assert len(push_calls) == 1, "push_live_layout was not called in Live mode"
        assert push_calls[0].id == "test-live-layout"

    def test_save_appends_to_doc_when_layout_new(self, qtbot: Any) -> None:
        """Save with a canvas layout not in doc.screen_layouts appends it."""
        win = _open_win(qtbot, CpsmDocument())
        QApplication.processEvents()

        win._save_document = lambda: None  # type: ignore[method-assign]

        layout = ScreenLayout(id="brand-new-id", name="Brand New", monitors=[])
        win._screen_map_widget.set_layout(layout, [])
        QApplication.processEvents()

        btn = win.findChild(QPushButton, "btn_screens_save")
        assert btn is not None
        btn.click()
        QApplication.processEvents()

        ids = [sl.id for sl in win._document.screen_layouts]
        assert "brand-new-id" in ids

    def test_save_overwrites_existing_layout_in_doc(self, qtbot: Any) -> None:
        """Save with a canvas layout whose id already exists overwrites it."""
        layout = _make_layout("existing-id", "Old Name")
        doc = CpsmDocument(screen_layouts=[layout])

        win = _open_win(qtbot, doc)
        QApplication.processEvents()
        win._save_document = lambda: None  # type: ignore[method-assign]

        new_layout = ScreenLayout(
            id="existing-id", name="Updated Name", monitors=[]
        )
        win._screen_map_widget.set_layout(new_layout, [])
        btn = win.findChild(QPushButton, "btn_screens_save")
        assert btn is not None
        btn.click()
        QApplication.processEvents()

        # Should still have one layout, name updated
        assert len(win._document.screen_layouts) == 1
        assert win._document.screen_layouts[0].name == "Updated Name"
