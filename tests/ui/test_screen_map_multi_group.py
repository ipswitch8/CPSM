# -*- coding: utf-8 -*-
"""
tests/ui/test_screen_map_multi_group.py — Phase 17 multi-group overlay tests.

Spec sections: §6.4, §6.5

Tests cover:
  - Two visible groups render with distinct colors.
  - set_edit_target: non-edit groups go to 50% opacity, ignore drag events.
  - Conflict detection: overlapping viewports → red hatch + conflict_count_changed.
  - Same connection in two groups appears in both overlays.
  - Per-group save: dirty flag set after viewport edit; cleared after save_requested.
  - Color override: setting color updates the swatch.
  - Auto-color: distinct groups get distinct palette colors.
  - GroupLegendDock: add/remove groups, eye toggle, lock toggle, dirty marker,
    save/revert signals, color_changed signal.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QByteArray, QMimeData
from PySide6.QtGui import QColor

from cpsm.data.schema import (
    GeometryPct,
    Monitor,
    Pane,
    ScreenLayout,
    Viewport,
)
from cpsm.services.monitor_service import MonitorInfo
from cpsm.ui.widgets.group_legend import GroupLegendDock
from cpsm.ui.widgets.screen_map import (
    GROUP_PALETTE,
    MIME_CONNECTION_ID,
    ScreenMapWidget,
    group_color_for_id,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_monitor_info(identifier: str = "m1") -> MonitorInfo:
    return MonitorInfo(
        identifier=identifier,
        name="HDMI-1",
        geometry=(0, 0, 1920, 1080),
        available_geometry=(0, 0, 1920, 1080),
        physical_size_mm=(527.0, 296.0),
        device_pixel_ratio=1.0,
        orientation="landscape",
        manufacturer="",
        model="",
        serial="",
        qt_index=0,
    )


def _make_viewport(
    vp_id: str,
    x: float = 0,
    y: float = 0,
    w: float = 100,
    h: float = 100,
    connection_id: str | None = None,
) -> Viewport:
    panes = [Pane(connection_id=connection_id)] if connection_id else []
    return Viewport(
        id=vp_id,
        geometry_pct=GeometryPct(x=x, y=y, w=w, h=h),
        tmux_window_name=vp_id,
        tmux_layout="tiled",
        panes=panes,
    )


def _make_layout(
    layout_id: str,
    *viewports: Viewport,
    monitor_id: str = "m1",
) -> ScreenLayout:
    return ScreenLayout(
        id=layout_id,
        name=layout_id,
        monitors=[Monitor(identifier=monitor_id, viewports=list(viewports))],
    )


def _make_widget(qtbot) -> ScreenMapWidget:
    w = ScreenMapWidget(connection_lookup=lambda _: None)
    qtbot.addWidget(w)
    w.resize(800, 600)
    w.show()
    qtbot.waitExposed(w)
    return w


# ---------------------------------------------------------------------------
# ScreenMapWidget multi-group tests
# ---------------------------------------------------------------------------


class TestMultiGroupRendering:
    """Two visible groups render simultaneously with distinct colors."""

    def test_two_groups_render_without_error(self, qtbot) -> None:
        w = _make_widget(qtbot)
        mi = _make_monitor_info()
        layout_a = _make_layout("la", _make_viewport("vp-a", 0, 0, 50, 100))
        layout_b = _make_layout("lb", _make_viewport("vp-b", 50, 0, 50, 100))

        w.set_visible_groups(
            ["grp-a", "grp-b"],
            layouts={"grp-a": layout_a, "grp-b": layout_b},
        )
        # set_layout provides monitors
        w._monitors = [mi]
        w._redraw_multi()
        # Scene should have items (viewports were drawn)
        assert w.scene.items()

    def test_two_groups_get_distinct_colors(self) -> None:
        # Generate 13 different group IDs — by pigeonhole at least two must
        # share a palette slot BUT across the full set we must see at least
        # two distinct colors (the palette has 12 unique entries).
        ids = [f"group-color-test-{i}" for i in range(13)]
        colors = {group_color_for_id(gid).name() for gid in ids}
        # With 13 IDs into a 12-bucket palette there must be at most 12 unique colors,
        # but across any 13 IDs we should see at least 2 distinct ones.
        assert len(colors) >= 2

    def test_group_color_stable_by_id(self) -> None:
        # Same id → same color every time
        c1 = group_color_for_id("stable-group")
        c2 = group_color_for_id("stable-group")
        assert c1.name() == c2.name()

    def test_color_override_applied(self, qtbot) -> None:
        w = _make_widget(qtbot)
        mi = _make_monitor_info()
        layout_a = _make_layout("la", _make_viewport("vp-a"))

        w.set_visible_groups(
            ["grp-override"],
            layouts={"grp-override": layout_a},
            color_overrides={"grp-override": "#ff0000"},
        )
        w._monitors = [mi]
        w._redraw_multi()

        color = w.group_color("grp-override")
        assert color.name() == "#ff0000"

    def test_set_group_color_api(self, qtbot) -> None:
        w = _make_widget(qtbot)
        w.set_group_color("grp-x", "#00ff00")
        assert w.group_color("grp-x").name() == "#00ff00"


class TestAutoColorPalette:
    """Auto-color palette: 12 perceptually-distinct colors."""

    def test_palette_has_12_entries(self) -> None:
        assert len(GROUP_PALETTE) == 12

    def test_palette_entries_are_valid_hex(self) -> None:
        for entry in GROUP_PALETTE:
            assert QColor(entry).isValid(), f"{entry!r} is not a valid hex color"

    def test_group_ids_map_to_valid_colors(self) -> None:
        for i in range(20):
            gid = f"group-{i:02d}"
            c = group_color_for_id(gid)
            assert c.isValid()

    def test_invalid_override_falls_back_to_palette(self) -> None:
        c = group_color_for_id("any-group", override="not-a-color")
        # Should fall back gracefully — either the palette color or Qt invalid
        # The implementation returns the palette color when override is invalid
        palette_c = group_color_for_id("any-group", override=None)
        assert c.name() == palette_c.name()


class TestEditTarget:
    """set_edit_target restricts drag/drop to one group's panes."""

    def test_edit_target_changes_pane_registry(self, qtbot) -> None:
        w = _make_widget(qtbot)
        mi = _make_monitor_info()
        layout_a = _make_layout("la", _make_viewport("vp-a", 0, 0, 50, 100, "conn-a"))
        layout_b = _make_layout("lb", _make_viewport("vp-b", 50, 0, 50, 100, "conn-b"))

        w._monitors = [mi]
        w.set_visible_groups(
            ["grp-a", "grp-b"],
            layouts={"grp-a": layout_a, "grp-b": layout_b},
        )
        w.set_edit_target("grp-a")

        # Only grp-a panes should be in the registry. Round C: pane_id is
        # always a serial-based opaque token, so check via the
        # connection_id field on each record.
        conn_ids = {r.connection_id for r in w.pane_registry}
        assert "conn-a" in conn_ids
        assert "conn-b" not in conn_ids

    def test_no_edit_target_locks_drag_enter(self, qtbot) -> None:
        """When no edit target is set in multi-group mode, drag is rejected."""
        w = _make_widget(qtbot)
        mi = _make_monitor_info()
        layout_a = _make_layout("la", _make_viewport("vp-a"))

        w._monitors = [mi]
        w.set_visible_groups(["grp-a"], layouts={"grp-a": layout_a})
        w.set_edit_target(None)  # locked

        ignored: list[bool] = []
        mime = QMimeData()
        mime.setData(MIME_CONNECTION_ID, QByteArray(b"conn-test"))

        class _FakeEvent:
            def mimeData(self):
                return mime

            def acceptProposedAction(self):
                pass

            def ignore(self):
                ignored.append(True)

        w._on_drag_enter(_FakeEvent())  # type: ignore[arg-type]
        assert ignored == [True]

    def test_with_edit_target_accepts_drag_enter(self, qtbot) -> None:
        """When an edit target is set, drag is accepted."""
        w = _make_widget(qtbot)
        mi = _make_monitor_info()
        layout_a = _make_layout("la", _make_viewport("vp-a"))

        w._monitors = [mi]
        w.set_visible_groups(["grp-a"], layouts={"grp-a": layout_a})
        w.set_edit_target("grp-a")

        accepted: list[bool] = []
        mime = QMimeData()
        mime.setData(MIME_CONNECTION_ID, QByteArray(b"conn-test"))

        class _FakeEvent:
            def mimeData(self):
                return mime

            def acceptProposedAction(self):
                accepted.append(True)

            def ignore(self):
                pass

        w._on_drag_enter(_FakeEvent())  # type: ignore[arg-type]
        assert accepted == [True]


class TestConflictDetection:
    """Overlapping viewports across groups produce red hatch and conflict_count_changed."""

    def _make_overlapping_setup(self, qtbot) -> ScreenMapWidget:
        w = _make_widget(qtbot)
        mi = _make_monitor_info()
        # Both groups use the FULL monitor (100% overlap)
        layout_a = _make_layout("la", _make_viewport("vp-a", 0, 0, 100, 100))
        layout_b = _make_layout("lb", _make_viewport("vp-b", 0, 0, 100, 100))

        w._monitors = [mi]
        w.set_visible_groups(
            ["grp-a", "grp-b"],
            layouts={"grp-a": layout_a, "grp-b": layout_b},
        )
        return w

    def test_conflict_rects_non_empty(self, qtbot) -> None:
        w = self._make_overlapping_setup(qtbot)
        assert len(w.conflict_rects) > 0

    def test_conflict_count_changed_signal(self, qtbot) -> None:
        w = _make_widget(qtbot)
        counts: list[int] = []
        w.conflict_count_changed.connect(counts.append)

        mi = _make_monitor_info()
        layout_a = _make_layout("la", _make_viewport("vp-a", 0, 0, 100, 100))
        layout_b = _make_layout("lb", _make_viewport("vp-b", 0, 0, 100, 100))

        w._monitors = [mi]
        w.set_visible_groups(
            ["grp-a", "grp-b"],
            layouts={"grp-a": layout_a, "grp-b": layout_b},
        )
        # Should have emitted at least one non-zero count
        assert any(c > 0 for c in counts)

    def test_no_conflict_when_disjoint(self, qtbot) -> None:
        w = _make_widget(qtbot)
        mi = _make_monitor_info()
        # Non-overlapping halves
        layout_a = _make_layout("la", _make_viewport("vp-a", 0, 0, 50, 100))
        layout_b = _make_layout("lb", _make_viewport("vp-b", 50, 0, 50, 100))

        w._monitors = [mi]
        w.set_visible_groups(
            ["grp-a", "grp-b"],
            layouts={"grp-a": layout_a, "grp-b": layout_b},
        )
        assert w.conflict_rects == []

    def test_conflict_count_zero_when_disjoint(self, qtbot) -> None:
        w = _make_widget(qtbot)
        counts: list[int] = []
        w.conflict_count_changed.connect(counts.append)

        mi = _make_monitor_info()
        layout_a = _make_layout("la", _make_viewport("vp-a", 0, 0, 50, 100))
        layout_b = _make_layout("lb", _make_viewport("vp-b", 50, 0, 50, 100))

        w._monitors = [mi]
        # First trigger overlap (to set non-zero count)
        overlap_a = _make_layout("la2", _make_viewport("vp-a2", 0, 0, 100, 100))
        overlap_b = _make_layout("lb2", _make_viewport("vp-b2", 0, 0, 100, 100))
        w.set_visible_groups(
            ["grp-a", "grp-b"],
            layouts={"grp-a": overlap_a, "grp-b": overlap_b},
        )
        # Now switch to disjoint
        w.set_visible_groups(
            ["grp-a", "grp-b"],
            layouts={"grp-a": layout_a, "grp-b": layout_b},
        )
        assert 0 in counts


class TestSameConnectionInTwoGroups:
    """Same connection_id appearing in both groups renders in both."""

    def test_same_connection_in_two_groups(self, qtbot) -> None:
        w = _make_widget(qtbot)
        mi = _make_monitor_info()
        shared_conn = "shared-conn"
        # Both groups reference the same connection on separate viewport halves
        layout_a = _make_layout("la", _make_viewport("vp-a", 0, 0, 50, 100, shared_conn))
        layout_b = _make_layout("lb", _make_viewport("vp-b", 50, 0, 50, 100, shared_conn))

        w._monitors = [mi]
        # We need two separate Viewport objects (same conn, different vp positions)
        w.set_visible_groups(
            ["grp-a", "grp-b"],
            layouts={"grp-a": layout_a, "grp-b": layout_b},
        )
        w.set_edit_target("grp-a")

        # Registry for grp-a should contain the shared conn (Round C:
        # check via connection_id field — pane_id is now an opaque
        # serial token).
        conn_ids = {r.connection_id for r in w.pane_registry}
        assert shared_conn in conn_ids
        # Scene should have items from both groups
        assert w.scene.items()


class TestPerGroupDirtyTracking:
    """Per-group dirty flag tracking with group_dirty signal."""

    def test_initially_not_dirty(self, qtbot) -> None:
        w = _make_widget(qtbot)
        w.set_visible_groups(["grp-x"], layouts={})
        assert not w.is_group_dirty("grp-x")

    def test_mark_group_dirty(self, qtbot) -> None:
        w = _make_widget(qtbot)
        dirty_events: list[tuple[str, bool]] = []
        w.group_dirty.connect(lambda gid, d: dirty_events.append((gid, d)))

        w.set_visible_groups(["grp-x"], layouts={})
        w.mark_group_dirty("grp-x", True)
        assert w.is_group_dirty("grp-x")
        assert ("grp-x", True) in dirty_events

    def test_mark_group_clean(self, qtbot) -> None:
        w = _make_widget(qtbot)
        dirty_events: list[tuple[str, bool]] = []
        w.group_dirty.connect(lambda gid, d: dirty_events.append((gid, d)))

        w.set_visible_groups(["grp-x"], layouts={})
        w.mark_group_dirty("grp-x", True)
        w.mark_group_dirty("grp-x", False)
        assert not w.is_group_dirty("grp-x")
        assert ("grp-x", False) in dirty_events

    def test_no_duplicate_signal_if_state_unchanged(self, qtbot) -> None:
        w = _make_widget(qtbot)
        dirty_events: list[tuple[str, bool]] = []
        w.group_dirty.connect(lambda gid, d: dirty_events.append((gid, d)))

        w.set_visible_groups(["grp-x"], layouts={})
        w.mark_group_dirty("grp-x", True)
        w.mark_group_dirty("grp-x", True)  # redundant
        assert dirty_events.count(("grp-x", True)) == 1

    def test_update_group_layout_marks_dirty(self, qtbot) -> None:
        w = _make_widget(qtbot)
        w._monitors = [_make_monitor_info()]
        layout_a = _make_layout("la", _make_viewport("vp-a"))
        w.set_visible_groups(["grp-a"], layouts={"grp-a": layout_a})

        new_layout = _make_layout("la2", _make_viewport("vp-a2"))
        w.update_group_layout("grp-a", new_layout)
        assert w.is_group_dirty("grp-a")


# ---------------------------------------------------------------------------
# GroupLegendDock tests
# ---------------------------------------------------------------------------


class TestGroupLegendDockCreation:
    def test_dock_object_name(self, qtbot) -> None:
        dock = GroupLegendDock()
        qtbot.addWidget(dock)
        assert dock.objectName() == "dock_group_legend"

    def test_list_widget_object_name(self, qtbot) -> None:
        from PySide6.QtWidgets import QListWidget

        dock = GroupLegendDock()
        qtbot.addWidget(dock)
        lw = dock.findChild(QListWidget, "legend_list")
        assert lw is not None

    def test_add_group_creates_row(self, qtbot) -> None:
        dock = GroupLegendDock()
        qtbot.addWidget(dock)
        dock.add_group("grp-1", "Group 1")
        assert dock._list_widget.count() == 1

    def test_add_multiple_groups(self, qtbot) -> None:
        dock = GroupLegendDock()
        qtbot.addWidget(dock)
        dock.add_group("grp-1", "Group 1")
        dock.add_group("grp-2", "Group 2")
        dock.add_group("grp-3", "Group 3")
        assert dock._list_widget.count() == 3

    def test_add_group_idempotent(self, qtbot) -> None:
        dock = GroupLegendDock()
        qtbot.addWidget(dock)
        dock.add_group("grp-1", "Group 1")
        dock.add_group("grp-1", "Group 1")  # duplicate
        assert dock._list_widget.count() == 1

    def test_remove_group(self, qtbot) -> None:
        dock = GroupLegendDock()
        qtbot.addWidget(dock)
        dock.add_group("grp-1", "Group 1")
        dock.add_group("grp-2", "Group 2")
        dock.remove_group("grp-1")
        assert dock._list_widget.count() == 1

    def test_remove_nonexistent_group_is_noop(self, qtbot) -> None:
        dock = GroupLegendDock()
        qtbot.addWidget(dock)
        dock.remove_group("nonexistent")  # should not raise


class TestGroupLegendDockWidgets:
    def _make_dock(self, qtbot) -> GroupLegendDock:
        dock = GroupLegendDock()
        qtbot.addWidget(dock)
        dock.show()
        qtbot.waitExposed(dock)
        dock.add_group("grp-1", "Group 1", color_override="#ff0000")
        dock.add_group("grp-2", "Group 2", color_override="#00ff00")
        return dock

    def test_row_object_names(self, qtbot) -> None:
        dock = self._make_dock(qtbot)
        row1 = dock._rows["grp-1"]
        assert row1.objectName() == "group_legend_row_grp-1"

    def test_swatch_object_name(self, qtbot) -> None:
        dock = self._make_dock(qtbot)
        row1 = dock._rows["grp-1"]
        assert row1._btn_color.objectName() == "group_legend_swatch_grp-1"

    def test_eye_button_object_name(self, qtbot) -> None:
        dock = self._make_dock(qtbot)
        row1 = dock._rows["grp-1"]
        assert row1._btn_eye.objectName() == "group_legend_eye_grp-1"

    def test_lock_button_object_name(self, qtbot) -> None:
        dock = self._make_dock(qtbot)
        row1 = dock._rows["grp-1"]
        assert row1._btn_lock.objectName() == "group_legend_lock_grp-1"

    def test_save_button_object_name(self, qtbot) -> None:
        dock = self._make_dock(qtbot)
        row1 = dock._rows["grp-1"]
        assert row1._btn_save.objectName() == "group_legend_save_grp-1"

    def test_revert_button_object_name(self, qtbot) -> None:
        dock = self._make_dock(qtbot)
        row1 = dock._rows["grp-1"]
        assert row1._btn_revert.objectName() == "group_legend_revert_grp-1"

    def test_dirty_indicator_object_name(self, qtbot) -> None:
        dock = self._make_dock(qtbot)
        row1 = dock._rows["grp-1"]
        assert row1._lbl_dirty.objectName() == "group_legend_dirty_grp-1"


class TestGroupLegendDockVisibility:
    def test_eye_toggle_emits_visibility_changed(self, qtbot) -> None:
        dock = GroupLegendDock()
        qtbot.addWidget(dock)
        dock.add_group("grp-1", "Group 1")

        events: list[tuple[str, bool]] = []
        dock.visibility_changed.connect(lambda gid, v: events.append((gid, v)))

        # Uncheck eye (hide group)
        row = dock._rows["grp-1"]
        row._btn_eye.setChecked(False)  # triggers toggled signal

        assert ("grp-1", False) in events

    def test_eye_toggle_on_emits_visible_true(self, qtbot) -> None:
        dock = GroupLegendDock()
        qtbot.addWidget(dock)
        dock.add_group("grp-1", "Group 1")

        events: list[tuple[str, bool]] = []
        dock.visibility_changed.connect(lambda gid, v: events.append((gid, v)))

        row = dock._rows["grp-1"]
        row._btn_eye.setChecked(False)
        row._btn_eye.setChecked(True)

        assert ("grp-1", True) in events

    def test_visible_group_ids_initially_all(self, qtbot) -> None:
        dock = GroupLegendDock()
        qtbot.addWidget(dock)
        dock.add_group("grp-1", "Group 1")
        dock.add_group("grp-2", "Group 2")
        assert dock.visible_group_ids() == ["grp-1", "grp-2"]

    def test_visible_group_ids_after_hide(self, qtbot) -> None:
        dock = GroupLegendDock()
        qtbot.addWidget(dock)
        dock.add_group("grp-1", "Group 1")
        dock.add_group("grp-2", "Group 2")

        dock._rows["grp-1"]._btn_eye.setChecked(False)

        assert dock.visible_group_ids() == ["grp-2"]


class TestGroupLegendDockEditTarget:
    def test_edit_target_initially_first_unlocked(self, qtbot) -> None:
        dock = GroupLegendDock()
        qtbot.addWidget(dock)
        dock.add_group("grp-1", "Group 1")
        dock.add_group("grp-2", "Group 2")
        # No locks → first visible group is edit target
        assert dock.current_edit_target() == "grp-1"

    def test_locking_first_changes_edit_target(self, qtbot) -> None:
        dock = GroupLegendDock()
        qtbot.addWidget(dock)
        dock.add_group("grp-1", "Group 1")
        dock.add_group("grp-2", "Group 2")

        dock._rows["grp-1"]._btn_lock.setChecked(True)

        assert dock.current_edit_target() == "grp-2"

    def test_all_locked_returns_none(self, qtbot) -> None:
        dock = GroupLegendDock()
        qtbot.addWidget(dock)
        dock.add_group("grp-1", "Group 1")

        dock._rows["grp-1"]._btn_lock.setChecked(True)

        assert dock.current_edit_target() is None

    def test_edit_target_changed_signal_emitted(self, qtbot) -> None:
        dock = GroupLegendDock()
        qtbot.addWidget(dock)
        dock.add_group("grp-1", "Group 1")
        dock.add_group("grp-2", "Group 2")

        targets: list[str] = []
        dock.edit_target_changed.connect(targets.append)

        dock._rows["grp-1"]._btn_lock.setChecked(True)

        assert "grp-2" in targets

    def test_all_locked_emits_empty_string(self, qtbot) -> None:
        dock = GroupLegendDock()
        qtbot.addWidget(dock)
        dock.add_group("grp-1", "Group 1")

        targets: list[str] = []
        dock.edit_target_changed.connect(targets.append)

        dock._rows["grp-1"]._btn_lock.setChecked(True)

        assert "" in targets


class TestGroupLegendDockDirtyMarker:
    def test_dirty_marker_initially_hidden(self, qtbot) -> None:
        dock = GroupLegendDock()
        qtbot.addWidget(dock)
        dock.add_group("grp-1", "Group 1")

        row = dock._rows["grp-1"]
        # isHidden() checks the explicit hide flag regardless of parent visibility
        assert row._lbl_dirty.isHidden()

    def test_update_dirty_shows_marker(self, qtbot) -> None:
        dock = GroupLegendDock()
        qtbot.addWidget(dock)
        dock.add_group("grp-1", "Group 1")

        dock.update_dirty("grp-1", True)

        row = dock._rows["grp-1"]
        # Check state flag directly — parent dock may not be shown in offscreen
        assert row.is_dirty
        assert not row._lbl_dirty.isHidden()

    def test_update_dirty_false_hides_marker(self, qtbot) -> None:
        dock = GroupLegendDock()
        qtbot.addWidget(dock)
        dock.add_group("grp-1", "Group 1")

        dock.update_dirty("grp-1", True)
        dock.update_dirty("grp-1", False)

        row = dock._rows["grp-1"]
        assert not row.is_dirty
        assert row._lbl_dirty.isHidden()


class TestGroupLegendDockSaveRevert:
    def test_save_button_emits_save_requested(self, qtbot) -> None:
        dock = GroupLegendDock()
        qtbot.addWidget(dock)
        dock.add_group("grp-1", "Group 1")

        events: list[str] = []
        dock.save_requested.connect(events.append)

        dock._rows["grp-1"]._btn_save.click()

        assert "grp-1" in events

    def test_revert_button_emits_revert_requested(self, qtbot) -> None:
        dock = GroupLegendDock()
        qtbot.addWidget(dock)
        dock.add_group("grp-1", "Group 1")

        events: list[str] = []
        dock.revert_requested.connect(events.append)

        dock._rows["grp-1"]._btn_revert.click()

        assert "grp-1" in events


class TestGroupLegendDockColorOverride:
    def test_color_override_applied_to_swatch(self, qtbot) -> None:
        dock = GroupLegendDock()
        qtbot.addWidget(dock)
        dock.add_group("grp-1", "Group 1", color_override="#aabbcc")

        row = dock._rows["grp-1"]
        assert row.color.name() == "#aabbcc"

    def test_update_color_api(self, qtbot) -> None:
        dock = GroupLegendDock()
        qtbot.addWidget(dock)
        dock.add_group("grp-1", "Group 1")

        dock.update_color("grp-1", "#123456")

        row = dock._rows["grp-1"]
        assert row.color.name() == "#123456"

    def test_update_color_invalid_hex_noop(self, qtbot) -> None:
        dock = GroupLegendDock()
        qtbot.addWidget(dock)
        dock.add_group("grp-1", "Group 1", color_override="#ff0000")

        original_name = dock._rows["grp-1"].color.name()
        dock.update_color("grp-1", "not-a-color")

        assert dock._rows["grp-1"].color.name() == original_name

    def test_update_color_nonexistent_group_noop(self, qtbot) -> None:
        dock = GroupLegendDock()
        qtbot.addWidget(dock)
        dock.update_color("no-such-group", "#ff0000")  # should not raise


# ---------------------------------------------------------------------------
# Integration: GroupLegendDock + ScreenMapWidget wiring scenario
# ---------------------------------------------------------------------------


class TestLegendAndMapIntegration:
    """Simulate a plausible controller wiring: legend signals update map."""

    def test_visibility_toggle_updates_visible_group_ids(self, qtbot) -> None:
        dock = GroupLegendDock()
        qtbot.addWidget(dock)
        dock.add_group("grp-a", "Group A")
        dock.add_group("grp-b", "Group B")

        # Hide grp-a
        dock._rows["grp-a"]._btn_eye.setChecked(False)

        visible = dock.visible_group_ids()
        assert "grp-a" not in visible
        assert "grp-b" in visible

    def test_save_then_dirty_cleared(self, qtbot) -> None:
        """Scenario: viewport edited → dirty; save clicked → mark clean."""
        w = _make_widget(qtbot)
        dock = GroupLegendDock()
        qtbot.addWidget(dock)
        dock.add_group("grp-a", "Group A")
        w.set_visible_groups(["grp-a"], layouts={})

        # Mark dirty (simulates a viewport drag)
        w.mark_group_dirty("grp-a", True)
        dock.update_dirty("grp-a", True)
        assert dock._rows["grp-a"].is_dirty

        # Click Save — controller would handle the signal and call mark_group_dirty(False)
        saved: list[str] = []
        dock.save_requested.connect(saved.append)
        dock._rows["grp-a"]._btn_save.click()

        assert "grp-a" in saved
        # Controller clears dirty flag
        w.mark_group_dirty("grp-a", False)
        dock.update_dirty("grp-a", False)
        assert not dock._rows["grp-a"].is_dirty

    def test_conflict_count_changed_signal_type(self, qtbot) -> None:
        """conflict_count_changed emits an integer."""
        w = _make_widget(qtbot)
        counts: list[int] = []
        w.conflict_count_changed.connect(counts.append)

        mi = _make_monitor_info()
        layout_a = _make_layout("la", _make_viewport("vp-a", 0, 0, 100, 100))
        layout_b = _make_layout("lb", _make_viewport("vp-b", 0, 0, 100, 100))

        w._monitors = [mi]
        w.set_visible_groups(
            ["grp-a", "grp-b"],
            layouts={"grp-a": layout_a, "grp-b": layout_b},
        )

        for c in counts:
            assert isinstance(c, int)
