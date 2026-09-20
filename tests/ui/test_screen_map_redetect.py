# -*- coding: utf-8 -*-
"""
tests/ui/test_screen_map_redetect.py — screen re-detection in ScreenMapWidget.

Covers the p02 integration: a persisted ScreenLayout is reconciled against the
live monitor snapshot on every render (which is what makes re-detection happen
at launch) and on ``screenAdded`` hot-plug, while ``screenRemoved`` leaves the
departed display's entry in place for ghost rendering.

All tests run with QT_QPA_PLATFORM=offscreen (set by conftest.py and the
os.environ.setdefault guard below).
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject, Signal

from cpsm.data.schema import GeometryPct, Monitor, Pane, ScreenLayout, Viewport
from cpsm.services.monitor_service import MonitorInfo
from cpsm.ui.widgets.screen_map import ScreenMapWidget

# ---------------------------------------------------------------------------
# Fixtures / builders
# ---------------------------------------------------------------------------


def _info(identifier: str, qt_index: int) -> MonitorInfo:
    return MonitorInfo(
        identifier=identifier,
        name=f"DP-{qt_index}",
        geometry=(qt_index * 1920, 0, 1920, 1080),
        available_geometry=(qt_index * 1920, 0, 1920, 1040),
        physical_size_mm=(527.0, 296.0),
        device_pixel_ratio=1.0,
        orientation="landscape",
        manufacturer="DELL",
        model=f"U27-{qt_index}",
        serial=f"SN{qt_index}",
        qt_index=qt_index,
    )


MON_0 = _info("DELL-U27-0-SN0", 0)
MON_1 = _info("DELL-U27-1-SN1", 1)
MON_2 = _info("DELL-U27-2-SN2", 2)


class _FakeMonitorService(QObject):
    """Stand-in for MonitorService with controllable snapshot contents."""

    monitor_added = Signal(object)
    monitor_removed = Signal(str)

    def __init__(self, monitors: list[MonitorInfo]) -> None:
        super().__init__()
        self._monitors = list(monitors)

    def snapshot(self) -> list[MonitorInfo]:
        return list(self._monitors)

    def plug_in(self, info: MonitorInfo) -> None:
        """Simulate a video change: attach *info* and emit Qt's screenAdded."""
        self._monitors.append(info)
        self.monitor_added.emit(info)

    def unplug(self, info: MonitorInfo) -> None:
        self._monitors = [m for m in self._monitors if m.identifier != info.identifier]
        self.monitor_removed.emit(info.identifier)


def _persisted_two_monitor_layout() -> ScreenLayout:
    """A layout saved while only two displays were attached."""
    return ScreenLayout(
        id="dev-default-layout",
        name="dev default",
        monitors=[
            Monitor(
                identifier=MON_0.identifier,
                viewports=[
                    Viewport(
                        id="dev-vp-0",
                        geometry_pct=GeometryPct(x=0, y=0, w=100, h=100),
                        tmux_window_name="dev-0",
                        tmux_layout="tiled",
                        panes=[Pane(connection_id="web-01")],
                    )
                ],
            ),
            Monitor(
                identifier=MON_1.identifier,
                viewports=[
                    Viewport(
                        id="dev-vp-1",
                        geometry_pct=GeometryPct(x=0, y=0, w=100, h=100),
                        tmux_window_name="dev-1",
                        tmux_layout="tiled",
                        panes=[Pane(connection_id="db-01")],
                    )
                ],
            ),
        ],
    )


@pytest.fixture
def widget(qtbot):
    """A ScreenMapWidget with three displays attached but nothing rendered yet."""
    svc = _FakeMonitorService([MON_0, MON_1, MON_2])
    w = ScreenMapWidget(monitor_service=svc)
    qtbot.addWidget(w)
    w._fake_service = svc  # keep a handle for the tests
    return w


# ---------------------------------------------------------------------------
# Trigger 1 — re-detection at launch
# ---------------------------------------------------------------------------


def _resolved_qt_indexes(w) -> list[int | None]:
    """Report the qt_index each layout entry resolves to, using the renderer's
    OWN resolver (``screen_map.resolve_monitor_info``).

    Counting non-ghost entries is not enough. The identifier map is keyed by
    identifier, so two entries naming one duplicated identifier BOTH resolve —
    to the same display. Neither is a ghost, so a count-based check reports the
    right number while one monitor is drawn twice and another is blank. Only
    the set of resolved identities exposes that.

    Imported rather than reimplemented, so this cannot become a self-fulfilling
    oracle that passes while the real renderer is broken.
    """
    from cpsm.ui.widgets.screen_map import resolve_monitor_info

    if w._layout_data is None:
        return []
    return [
        (lambda i: i.qt_index if i is not None else None)(
            resolve_monitor_info(sm, w._layout_data, w._monitors)
        )
        for sm in w._layout_data.monitors
    ]


def _assert_each_display_drawn_once(w, expected: int) -> None:
    """Assert *expected* entries resolved, each to a DIFFERENT display."""
    resolved = _resolved_qt_indexes(w)
    drawn = [q for q in resolved if q is not None]
    assert len(drawn) == expected, f"expected {expected} drawn, got {resolved}"
    assert len(set(drawn)) == len(drawn), f"a display is drawn twice: {resolved}"


def _drawn_monitor_count(w) -> int:
    """How many layout entries the renderer actually resolved and drew.

    Counting layout entries is not enough: `_build_scene` resolves each entry
    against the live monitor list and silently skips any it cannot match, so a
    reconciler that appends an unmatchable entry would still leave the user
    looking at the old number of displays. Pair this with
    :func:`_assert_each_display_drawn_once` — this count alone cannot see two
    entries collapsing onto the same display.
    """
    if w._layout_data is None:
        return 0
    return len(w._layout_data.monitors) - len(w._ghost_registry)


def test_stale_layout_is_reconciled_on_first_render(widget) -> None:
    """The reported bug: a 2-monitor layout loaded while 3 displays are
    attached must render 3, not 2 — without needing a restart or any user
    action."""
    layout = _persisted_two_monitor_layout()

    widget.set_layout(layout, [MON_0, MON_1, MON_2])

    assert len(widget._layout_data.monitors) == 3
    assert widget._layout_data.monitors[2].identifier == MON_2.identifier


def test_stale_layout_actually_draws_the_third_monitor(widget) -> None:
    """Guards the crux: appending an entry the renderer cannot resolve would
    grow the data and change nothing on screen."""
    before_items = len(widget.scene.items())

    widget.set_layout(_persisted_two_monitor_layout(), [MON_0, MON_1, MON_2])

    assert _drawn_monitor_count(widget) == 3, "all three displays must resolve and draw"
    assert widget._ghost_registry == [], "none of the three should render as a ghost"
    _assert_each_display_drawn_once(widget, 3)
    assert len(widget.scene.items()) > before_items


def test_launch_reconcile_preserves_existing_pane_assignments(widget) -> None:
    layout = _persisted_two_monitor_layout()

    widget.set_layout(layout, [MON_0, MON_1, MON_2])

    monitors = widget._layout_data.monitors
    assert [p.connection_id for p in monitors[0].viewports[0].panes] == ["web-01"]
    assert [p.connection_id for p in monitors[1].viewports[0].panes] == ["db-01"]
    assert monitors[0].viewports[0].id == "dev-vp-0"
    assert monitors[1].viewports[0].id == "dev-vp-1"


def test_launch_reconcile_emits_layout_reconciled(qtbot, widget) -> None:
    layout = _persisted_two_monitor_layout()

    with qtbot.waitSignal(widget.layout_reconciled, timeout=1000) as blocker:
        widget.set_layout(layout, [MON_0, MON_1, MON_2])

    emitted = blocker.args[0]
    assert emitted.id == "dev-default-layout"
    assert len(emitted.monitors) == 3


def test_no_signal_when_layout_already_matches(qtbot, widget) -> None:
    """A layout that already covers every attached display must not emit —
    otherwise the main window would rewrite ~/.cpsm.yaml on every render."""
    layout = _persisted_two_monitor_layout()

    with qtbot.assertNotEmitted(widget.layout_reconciled):
        widget.set_layout(layout, [MON_0, MON_1])

    assert len(widget._layout_data.monitors) == 2


def test_empty_layout_is_left_alone_for_the_ghost_affordance(qtbot, widget) -> None:
    """'No layout authored yet' is a deliberate UI state rendered as live ghost
    outlines; reconciliation must not fill it in behind the user's back."""
    empty = ScreenLayout(id="empty", name="(no layout)", monitors=[])

    with qtbot.assertNotEmitted(widget.layout_reconciled):
        widget.set_layout(empty, [MON_0, MON_1, MON_2])

    assert widget._layout_data.monitors == []


def test_empty_monitor_snapshot_does_not_rewrite_the_layout(qtbot, widget) -> None:
    """An empty snapshot means 'unknown', not 'every display was unplugged'."""
    layout = _persisted_two_monitor_layout()

    with qtbot.assertNotEmitted(widget.layout_reconciled):
        widget.set_layout(layout, [])

    assert len(widget._layout_data.monitors) == 2


# ---------------------------------------------------------------------------
# Trigger 2 — re-detection on video change (hot-plug)
# ---------------------------------------------------------------------------


def test_hotplug_add_puts_the_new_monitor_into_layout_data(qtbot) -> None:
    """Refreshing the live monitor cache alone was the old behaviour and drew
    nothing new, because the scene is built from _layout_data."""
    svc = _FakeMonitorService([MON_0, MON_1])
    w = ScreenMapWidget(monitor_service=svc)
    qtbot.addWidget(w)
    w.set_layout(_persisted_two_monitor_layout(), [MON_0, MON_1])
    assert len(w._layout_data.monitors) == 2

    svc.plug_in(MON_2)

    assert len(w._layout_data.monitors) == 3
    assert w._layout_data.monitors[2].identifier == MON_2.identifier
    assert len(w._monitors) == 3


def test_hotplug_add_emits_layout_reconciled(qtbot) -> None:
    svc = _FakeMonitorService([MON_0, MON_1])
    w = ScreenMapWidget(monitor_service=svc)
    qtbot.addWidget(w)
    w.set_layout(_persisted_two_monitor_layout(), [MON_0, MON_1])

    with qtbot.waitSignal(w.layout_reconciled, timeout=1000) as blocker:
        svc.plug_in(MON_2)

    assert len(blocker.args[0].monitors) == 3


def test_hotplug_add_redraws_the_scene(qtbot) -> None:
    svc = _FakeMonitorService([MON_0, MON_1])
    w = ScreenMapWidget(monitor_service=svc)
    qtbot.addWidget(w)
    w.set_layout(_persisted_two_monitor_layout(), [MON_0, MON_1])
    before = len(w.scene.items())

    svc.plug_in(MON_2)

    assert len(w.scene.items()) > before, "a third monitor should add scene items"


def test_hotplug_remove_keeps_the_entry_as_a_ghost(qtbot) -> None:
    """An unplug must not silently rewrite the layout — the entry stays so the
    user can see what went missing and where its panes were."""
    svc = _FakeMonitorService([MON_0, MON_1])
    w = ScreenMapWidget(monitor_service=svc)
    qtbot.addWidget(w)
    w.set_layout(_persisted_two_monitor_layout(), [MON_0, MON_1])

    svc.unplug(MON_1)

    assert len(w._layout_data.monitors) == 2
    assert w._layout_data.monitors[1].identifier == MON_1.identifier
    assert [p.connection_id for p in w._layout_data.monitors[1].viewports[0].panes] == [
        "db-01"
    ]
    assert len(w._monitors) == 1
    assert len(w._ghost_registry) == 1


def test_unplug_then_replug_does_not_duplicate_the_entry(qtbot) -> None:
    svc = _FakeMonitorService([MON_0, MON_1])
    w = ScreenMapWidget(monitor_service=svc)
    qtbot.addWidget(w)
    w.set_layout(_persisted_two_monitor_layout(), [MON_0, MON_1])

    svc.unplug(MON_1)
    svc.plug_in(MON_1)

    assert len(w._layout_data.monitors) == 2
    assert w._ghost_registry == []


# ---------------------------------------------------------------------------
# Trigger 3 — the shared entry point behind the manual action
# ---------------------------------------------------------------------------


def test_redetect_screens_picks_up_a_display_attached_while_blind(qtbot) -> None:
    """Covers the case Qt never signals (e.g. the app was started before the
    display woke up): the snapshot is re-queried on demand."""
    svc = _FakeMonitorService([MON_0, MON_1])
    w = ScreenMapWidget(monitor_service=svc)
    qtbot.addWidget(w)
    w.set_layout(_persisted_two_monitor_layout(), [MON_0, MON_1])

    svc._monitors.append(MON_2)  # attached without emitting screenAdded
    assert len(w._layout_data.monitors) == 2

    changed = w.redetect_screens()

    assert changed is True
    assert len(w._layout_data.monitors) == 3
    assert len(w._monitors) == 3


def test_redetect_screens_is_a_noop_when_nothing_changed(qtbot) -> None:
    svc = _FakeMonitorService([MON_0, MON_1])
    w = ScreenMapWidget(monitor_service=svc)
    qtbot.addWidget(w)
    w.set_layout(_persisted_two_monitor_layout(), [MON_0, MON_1])

    with qtbot.assertNotEmitted(w.layout_reconciled):
        changed = w.redetect_screens()

    assert changed is False
    assert len(w._layout_data.monitors) == 2


def test_redetect_screens_without_a_layout_does_not_raise(qtbot) -> None:
    svc = _FakeMonitorService([MON_0, MON_1])
    w = ScreenMapWidget(monitor_service=svc)
    qtbot.addWidget(w)

    assert w.redetect_screens() is False


def test_redetect_survives_a_failing_monitor_service(qtbot) -> None:
    """A broken snapshot must degrade to 'render what we have', never crash the
    Screens tab."""

    class _Broken(_FakeMonitorService):
        def snapshot(self):  # noqa: ANN201
            raise RuntimeError("X server went away")

    svc = _Broken([MON_0, MON_1])
    w = ScreenMapWidget(monitor_service=svc)
    qtbot.addWidget(w)
    w._layout_data = _persisted_two_monitor_layout()
    w._monitors = [MON_0, MON_1]

    assert w.redetect_screens() is False
    assert len(w._layout_data.monitors) == 2


# ---------------------------------------------------------------------------
# Trigger 3 — the widget-internal context menu
# ---------------------------------------------------------------------------


def _redetect_action(menu):
    """Return the 'Re-detect Screens' action from *menu*, or None."""
    for act in menu.actions():
        if act.objectName() == "screenmap_ctx_redetect_screens":
            return act
    return None


def test_empty_canvas_context_menu_offers_redetect(qtbot) -> None:
    """Right-clicking empty canvas used to be swallowed entirely, leaving no
    way to ask for a re-detect from the place the user is actually looking."""
    svc = _FakeMonitorService([MON_0, MON_1])
    w = ScreenMapWidget(monitor_service=svc)
    qtbot.addWidget(w)
    w.set_layout(_persisted_two_monitor_layout(), [MON_0, MON_1])

    # A scene point far outside every rendered rect.
    menu = w.build_canvas_context_menu(-10_000.0, -10_000.0)

    act = _redetect_action(menu)
    assert act is not None, [a.objectName() for a in menu.actions()]
    assert act.text() == "Re-detect Screens"


def test_redetect_action_is_automation_friendly(qtbot) -> None:
    """Stable objectName plus tool-tip/status-tip. QAction has no
    setAccessibleName — its accessible name is derived from text()."""
    from PySide6.QtWidgets import QMenu

    svc = _FakeMonitorService([MON_0])
    w = ScreenMapWidget(monitor_service=svc)
    qtbot.addWidget(w)

    act = w._build_redetect_action(QMenu(w))

    assert act.objectName() == "screenmap_ctx_redetect_screens"
    assert act.text() == "Re-detect Screens"
    assert "displays" in act.toolTip().lower()
    assert act.statusTip() == act.toolTip()


def test_triggering_the_action_reconciles_the_layout(qtbot) -> None:
    from PySide6.QtWidgets import QMenu

    svc = _FakeMonitorService([MON_0, MON_1])
    w = ScreenMapWidget(monitor_service=svc)
    qtbot.addWidget(w)
    w.set_layout(_persisted_two_monitor_layout(), [MON_0, MON_1])

    svc._monitors.append(MON_2)  # attached without a screenAdded signal
    act = w._build_redetect_action(QMenu(w))
    act.trigger()

    assert len(w._layout_data.monitors) == 3
    assert _drawn_monitor_count(w) == 3, "the manual trigger must actually draw it"
    assert w._ghost_registry == []
    _assert_each_display_drawn_once(w, 3)


def test_pane_context_menu_still_offers_its_own_actions(qtbot) -> None:
    """Regression guard: the re-detect item must be added to the pane menu,
    not replace it."""
    svc = _FakeMonitorService([MON_0, MON_1])
    w = ScreenMapWidget(monitor_service=svc)
    qtbot.addWidget(w)
    w.set_layout(_persisted_two_monitor_layout(), [MON_0, MON_1])

    registry = w.pane_registry
    assert registry, "the layout's panes should be in the registry"
    rec = registry[0]

    menu = w.build_canvas_context_menu(
        rec.scene_x + rec.scene_w / 2.0, rec.scene_y + rec.scene_h / 2.0
    )

    names = [a.objectName() for a in menu.actions()]
    assert "screenmap_ctx_attach" in names, names
    assert "screenmap_ctx_save_layout" in names, names
    assert "screenmap_ctx_redetect_screens" in names, names


# ---------------------------------------------------------------------------
# Multi-group overlay mode
# ---------------------------------------------------------------------------


def test_set_visible_groups_reconciles_each_group_layout(qtbot) -> None:
    """_redraw_multi renders straight from _group_layouts and never goes
    through set_layout, so without reconciliation here a user in overlay mode
    would still see only the displays attached when each group was saved."""
    svc = _FakeMonitorService([MON_0, MON_1, MON_2])
    w = ScreenMapWidget(monitor_service=svc)
    qtbot.addWidget(w)
    w._monitors = [MON_0, MON_1, MON_2]

    w.set_visible_groups(["dev"], layouts={"dev": _persisted_two_monitor_layout()})

    assert len(w._group_layouts["dev"].monitors) == 3


def test_redetect_screens_covers_multi_group_layouts(qtbot) -> None:
    svc = _FakeMonitorService([MON_0, MON_1])
    w = ScreenMapWidget(monitor_service=svc)
    qtbot.addWidget(w)
    w._monitors = [MON_0, MON_1]
    w.set_visible_groups(["dev"], layouts={"dev": _persisted_two_monitor_layout()})
    assert len(w._group_layouts["dev"].monitors) == 2

    svc._monitors.append(MON_2)
    changed = w.redetect_screens()

    assert changed is True
    assert len(w._group_layouts["dev"].monitors) == 3


# ---------------------------------------------------------------------------
# Combined scenario — all three triggers in sequence (p04)
# ---------------------------------------------------------------------------


def test_all_three_triggers_in_sequence(qtbot) -> None:
    """The full journey the user actually reported, end to end:

    1. launch with a stale 2-monitor layout while 3 displays are attached,
    2. a runtime hot-plug adding a 4th,
    3. a manual re-detect picking up a 5th that Qt never signalled about.

    Throughout, the original monitors keep their viewport and pane
    assignments untouched.
    """
    mon_3 = _info("DELL-U27-3-SN3", 3)
    mon_4 = _info("DELL-U27-4-SN4", 4)

    # 1. Launch: layout saved with 2 displays, 3 currently attached.
    svc = _FakeMonitorService([MON_0, MON_1, MON_2])
    w = ScreenMapWidget(monitor_service=svc)
    qtbot.addWidget(w)
    w.set_layout(_persisted_two_monitor_layout(), svc.snapshot())
    assert len(w._layout_data.monitors) == 3, "launch must pick up the third display"

    # 2. Video change: a fourth display is hot-plugged.
    svc.plug_in(mon_3)
    assert len(w._layout_data.monitors) == 4, "hot-plug must pick up the fourth"

    # 3. Manual: a fifth appears without Qt emitting screenAdded.
    svc._monitors.append(mon_4)
    assert w.redetect_screens() is True
    assert len(w._layout_data.monitors) == 5, "manual re-detect must pick up the fifth"

    # The two originally-persisted monitors are untouched throughout.
    monitors = w._layout_data.monitors
    assert monitors[0].identifier == MON_0.identifier
    assert monitors[1].identifier == MON_1.identifier
    assert monitors[0].viewports[0].id == "dev-vp-0"
    assert monitors[1].viewports[0].id == "dev-vp-1"
    assert [p.connection_id for p in monitors[0].viewports[0].panes] == ["web-01"]
    assert [p.connection_id for p in monitors[1].viewports[0].panes] == ["db-01"]

    # Every viewport id across the grown layout is still unique.
    vp_ids = [vp.id for m in monitors for vp in m.viewports]
    assert len(vp_ids) == len(set(vp_ids)), vp_ids

    # And all five actually resolve and draw — not merely present in the data.
    assert _drawn_monitor_count(w) == 5
    assert w._ghost_registry == []
    _assert_each_display_drawn_once(w, 5)


def test_ghost_survives_the_combined_journey(qtbot) -> None:
    """A display disconnected mid-journey stays a ghost and is not resurrected
    or duplicated by the later triggers."""
    svc = _FakeMonitorService([MON_0, MON_1])
    w = ScreenMapWidget(monitor_service=svc)
    qtbot.addWidget(w)
    w.set_layout(_persisted_two_monitor_layout(), svc.snapshot())

    svc.unplug(MON_1)
    assert len(w._ghost_registry) == 1, "the unplugged display must render as a ghost"

    svc.plug_in(MON_2)
    assert len(w._layout_data.monitors) == 3
    assert len(w._ghost_registry) == 1, "MON_1 must still be a ghost"

    w.redetect_screens()
    assert len(w._layout_data.monitors) == 3, "no duplicate entry for the ghost"
    assert w._layout_data.monitors[1].identifier == MON_1.identifier
    assert [p.connection_id for p in w._layout_data.monitors[1].viewports[0].panes] == [
        "db-01"
    ]


# ---------------------------------------------------------------------------
# Identical panels with no EDID serial, through the real render path
# ---------------------------------------------------------------------------


def _panel(qt_index: int) -> MonitorInfo:
    """A panel reporting make and model but no serial, so several of the same
    model share one identifier — the matched-desk hardware behind the original
    report."""
    return MonitorInfo(
        identifier="DELL-U2723QE",
        name=f"DP-{qt_index}",
        geometry=(qt_index * 1920, 0, 1920, 1080),
        available_geometry=(qt_index * 1920, 0, 1920, 1040),
        physical_size_mm=(527.0, 296.0),
        device_pixel_ratio=1.0,
        orientation="landscape",
        manufacturer="DELL",
        model="U2723QE",
        serial="",
        qt_index=qt_index,
    )


def _collapsed_layout() -> ScreenLayout:
    """A layout persisted before ambiguity was understood: every entry carries
    the same duplicated identifier, so the renderer collapses them."""
    return ScreenLayout(
        id="dev-default-layout",
        name="dev default",
        monitors=[
            Monitor(
                identifier="DELL-U2723QE",
                viewports=[
                    Viewport(
                        id="dev-vp-0",
                        geometry_pct=GeometryPct(x=0, y=0, w=100, h=100),
                        tmux_window_name="dev-0",
                        tmux_layout="tiled",
                        panes=[Pane(connection_id="web-01")],
                    )
                ],
            ),
            Monitor(
                identifier="DELL-U2723QE",
                viewports=[
                    Viewport(
                        id="dev-vp-1",
                        geometry_pct=GeometryPct(x=0, y=0, w=100, h=100),
                        tmux_window_name="dev-1",
                        tmux_layout="tiled",
                        panes=[Pane(connection_id="db-01")],
                    )
                ],
            ),
        ],
    )


def test_collapsed_layout_is_repaired_on_render(qtbot) -> None:
    """Loading a config whose entries share one duplicated identifier must
    leave every display drawn exactly once — not one drawn twice and another
    blank."""
    svc = _FakeMonitorService([_panel(0), _panel(1)])
    w = ScreenMapWidget(monitor_service=svc)
    qtbot.addWidget(w)

    w.set_layout(_collapsed_layout(), svc.snapshot())

    assert [m.identifier for m in w._layout_data.monitors] == [None, None]
    _assert_each_display_drawn_once(w, 2)
    assert [p.connection_id for p in w._layout_data.monitors[0].viewports[0].panes] == [
        "web-01"
    ]


def test_collapsed_layout_repair_is_persisted(qtbot) -> None:
    """The repair must reach the document, or it is redone on every launch."""
    svc = _FakeMonitorService([_panel(0), _panel(1)])
    w = ScreenMapWidget(monitor_service=svc)
    qtbot.addWidget(w)

    with qtbot.waitSignal(w.layout_reconciled, timeout=1000) as blocker:
        w.set_layout(_collapsed_layout(), svc.snapshot())

    assert [m.identifier for m in blocker.args[0].monitors] == [None, None]


def test_repaired_layout_does_not_re_emit_on_every_render(qtbot) -> None:
    """Once repaired the layout must settle, or the app rewrites ~/.cpsm.yaml
    on every single render."""
    svc = _FakeMonitorService([_panel(0), _panel(1)])
    w = ScreenMapWidget(monitor_service=svc)
    qtbot.addWidget(w)
    w.set_layout(_collapsed_layout(), svc.snapshot())
    repaired = w._layout_data

    with qtbot.assertNotEmitted(w.layout_reconciled):
        w.set_layout(repaired, svc.snapshot())


def test_third_identical_panel_is_detected_and_drawn(qtbot) -> None:
    """The reported bug on matched hardware, end to end through the widget."""
    svc = _FakeMonitorService([_panel(0), _panel(1), _panel(2)])
    w = ScreenMapWidget(monitor_service=svc)
    qtbot.addWidget(w)

    w.set_layout(_collapsed_layout(), svc.snapshot())

    assert len(w._layout_data.monitors) == 3
    _assert_each_display_drawn_once(w, 3)
