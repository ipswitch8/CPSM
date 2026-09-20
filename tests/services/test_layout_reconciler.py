# -*- coding: utf-8 -*-
"""
Tests for cpsm.services.layout_reconciler.

Covers the p01 reconciliation core: a persisted ScreenLayout is merged with the
live monitor snapshot so newly-attached displays gain an entry, existing
viewport/pane assignments survive untouched, and disconnected displays keep
their entry for the renderer's ghost path.
"""

from __future__ import annotations

from cpsm.data.schema import GeometryPct, Monitor, Pane, ScreenLayout, Viewport
from cpsm.services.layout_reconciler import (
    layout_needs_reconcile,
    reconcile_layout_with_monitors,
)
from cpsm.services.monitor_service import MonitorInfo

# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _info(
    identifier: str,
    qt_index: int,
    *,
    x: int = 0,
    width: int = 1920,
    height: int = 1080,
) -> MonitorInfo:
    """A MonitorInfo with plausible geometry for *qt_index*."""
    return MonitorInfo(
        identifier=identifier,
        name=f"DP-{qt_index}",
        geometry=(x, 0, width, height),
        available_geometry=(x, 0, width, height - 40),
        physical_size_mm=(527.0, 296.0),
        device_pixel_ratio=1.0,
        orientation="landscape",
        manufacturer="DELL",
        model=f"U27-{qt_index}",
        serial=f"SN{qt_index}",
        qt_index=qt_index,
    )


def _viewport(vp_id: str, *connection_ids: str) -> Viewport:
    return Viewport(
        id=vp_id,
        geometry_pct=GeometryPct(x=0, y=0, w=100, h=100),
        tmux_window_name=vp_id,
        tmux_layout="tiled",
        panes=[Pane(connection_id=cid) for cid in connection_ids],
    )


def _two_monitor_layout() -> ScreenLayout:
    """A layout persisted while two displays were attached, with real work in
    its panes so we can prove reconciliation does not disturb it."""
    return ScreenLayout(
        id="dev-default-layout",
        name="dev default",
        monitors=[
            Monitor(identifier="DELL-U27-0-SN0", viewports=[_viewport("dev-vp-0", "web-01")]),
            Monitor(
                identifier="DELL-U27-1-SN1",
                viewports=[_viewport("dev-vp-1", "db-01", "cache-01")],
            ),
        ],
    )


# ---------------------------------------------------------------------------
# Adding a newly-detected monitor
# ---------------------------------------------------------------------------


def test_third_monitor_is_added_to_stale_two_monitor_layout() -> None:
    """The reported bug: 2-monitor layout + 3 live displays must become 3."""
    layout = _two_monitor_layout()
    monitors = [_info("DELL-U27-0-SN0", 0), _info("DELL-U27-1-SN1", 1), _info("DELL-U27-2-SN2", 2)]

    result = reconcile_layout_with_monitors(layout, monitors)

    assert len(result.monitors) == 3
    assert result.monitors[2].identifier == "DELL-U27-2-SN2"
    assert result.monitors[2].monitor_index_hint == 2
    assert len(result.monitors[2].viewports) == 1
    assert result.monitors[2].viewports[0].geometry_pct.w == 100
    assert result.monitors[2].viewports[0].geometry_pct.h == 100


def test_existing_pane_assignments_are_preserved() -> None:
    layout = _two_monitor_layout()
    monitors = [_info("DELL-U27-0-SN0", 0), _info("DELL-U27-1-SN1", 1), _info("DELL-U27-2-SN2", 2)]

    result = reconcile_layout_with_monitors(layout, monitors)

    assert [p.connection_id for p in result.monitors[0].viewports[0].panes] == ["web-01"]
    assert [p.connection_id for p in result.monitors[1].viewports[0].panes] == [
        "db-01",
        "cache-01",
    ]
    assert result.monitors[0].viewports[0].id == "dev-vp-0"
    assert result.monitors[1].viewports[0].id == "dev-vp-1"


def test_input_layout_is_not_mutated() -> None:
    layout = _two_monitor_layout()
    monitors = [_info("DELL-U27-0-SN0", 0), _info("DELL-U27-1-SN1", 1), _info("DELL-U27-2-SN2", 2)]

    reconcile_layout_with_monitors(layout, monitors)

    assert len(layout.monitors) == 2


def test_new_viewport_ids_do_not_collide_with_existing() -> None:
    """A layout already using <layout-id>-vp-N ids must not get a duplicate."""
    layout = ScreenLayout(
        id="dev-default-layout",
        name="dev default",
        monitors=[
            Monitor(
                identifier="DELL-U27-0-SN0",
                viewports=[_viewport("dev-default-layout-vp-0", "web-01")],
            ),
        ],
    )
    monitors = [_info("DELL-U27-0-SN0", 0), _info("DELL-U27-1-SN1", 1)]

    result = reconcile_layout_with_monitors(layout, monitors)

    all_ids = [vp.id for m in result.monitors for vp in m.viewports]
    assert len(all_ids) == len(set(all_ids)), all_ids
    assert "dev-default-layout-vp-1" in all_ids


def test_two_monitors_added_at_once() -> None:
    layout = ScreenLayout(
        id="dev-default-layout",
        name="dev default",
        monitors=[Monitor(identifier="DELL-U27-0-SN0", viewports=[_viewport("dev-vp-0")])],
    )
    monitors = [_info("DELL-U27-0-SN0", 0), _info("DELL-U27-1-SN1", 1), _info("DELL-U27-2-SN2", 2)]

    result = reconcile_layout_with_monitors(layout, monitors)

    assert len(result.monitors) == 3
    assert [m.identifier for m in result.monitors] == [
        "DELL-U27-0-SN0",
        "DELL-U27-1-SN1",
        "DELL-U27-2-SN2",
    ]


# ---------------------------------------------------------------------------
# Disconnected monitors stay as ghosts
# ---------------------------------------------------------------------------


def test_disconnected_monitor_entry_is_retained_for_ghost_rendering() -> None:
    layout = _two_monitor_layout()
    monitors = [_info("DELL-U27-0-SN0", 0)]  # second display unplugged

    result = reconcile_layout_with_monitors(layout, monitors)

    assert len(result.monitors) == 2
    assert result.monitors[1].identifier == "DELL-U27-1-SN1"
    assert [p.connection_id for p in result.monitors[1].viewports[0].panes] == [
        "db-01",
        "cache-01",
    ]


def test_unplug_then_replug_reuses_the_original_entry() -> None:
    """Reconciling after a replug must not append a duplicate entry."""
    layout = _two_monitor_layout()

    unplugged = reconcile_layout_with_monitors(layout, [_info("DELL-U27-0-SN0", 0)])
    replugged = reconcile_layout_with_monitors(
        unplugged, [_info("DELL-U27-0-SN0", 0), _info("DELL-U27-1-SN1", 1)]
    )

    assert len(replugged.monitors) == 2
    assert [p.connection_id for p in replugged.monitors[1].viewports[0].panes] == [
        "db-01",
        "cache-01",
    ]


# ---------------------------------------------------------------------------
# Idempotency and no-op cases
# ---------------------------------------------------------------------------


def test_reconcile_is_idempotent() -> None:
    layout = _two_monitor_layout()
    monitors = [_info("DELL-U27-0-SN0", 0), _info("DELL-U27-1-SN1", 1), _info("DELL-U27-2-SN2", 2)]

    once = reconcile_layout_with_monitors(layout, monitors)
    twice = reconcile_layout_with_monitors(once, monitors)

    assert twice.model_dump() == once.model_dump()


def test_matching_layout_is_unchanged() -> None:
    layout = _two_monitor_layout()
    monitors = [_info("DELL-U27-0-SN0", 0), _info("DELL-U27-1-SN1", 1)]

    result = reconcile_layout_with_monitors(layout, monitors)

    assert result.model_dump() == layout.model_dump()


def test_empty_snapshot_leaves_layout_untouched() -> None:
    """An empty snapshot means 'unknown', not 'every display was unplugged'."""
    layout = _two_monitor_layout()

    result = reconcile_layout_with_monitors(layout, [])

    assert result.model_dump() == layout.model_dump()


def test_empty_layout_gains_one_entry_per_live_monitor() -> None:
    layout = ScreenLayout(id="empty-layout", name="(no layout)", monitors=[])
    monitors = [_info("DELL-U27-0-SN0", 0), _info("DELL-U27-1-SN1", 1)]

    result = reconcile_layout_with_monitors(layout, monitors)

    assert len(result.monitors) == 2
    assert [m.viewports[0].id for m in result.monitors] == [
        "empty-layout-vp-0",
        "empty-layout-vp-1",
    ]
    assert all(m.viewports[0].panes == [] for m in result.monitors)


# ---------------------------------------------------------------------------
# Identifier backfill / positional matching
# ---------------------------------------------------------------------------


def test_positional_entry_is_matched_and_identifier_backfilled() -> None:
    """A layout written before EDID identifiers were persisted matches by
    position, and gains the stable identifier so later re-ordering is safe."""
    layout = ScreenLayout(
        id="dev-default-layout",
        name="dev default",
        monitors=[
            Monitor(viewports=[_viewport("dev-vp-0", "web-01")]),
            Monitor(viewports=[_viewport("dev-vp-1", "db-01")]),
        ],
    )
    monitors = [_info("DELL-U27-0-SN0", 0), _info("DELL-U27-1-SN1", 1), _info("DELL-U27-2-SN2", 2)]

    result = reconcile_layout_with_monitors(layout, monitors)

    assert len(result.monitors) == 3
    assert result.monitors[0].identifier == "DELL-U27-0-SN0"
    assert result.monitors[1].identifier == "DELL-U27-1-SN1"
    assert [p.connection_id for p in result.monitors[0].viewports[0].panes] == ["web-01"]


def test_monitor_index_hint_is_honoured() -> None:
    layout = ScreenLayout(
        id="dev-default-layout",
        name="dev default",
        monitors=[Monitor(monitor_index_hint=1, viewports=[_viewport("dev-vp-1", "db-01")])],
    )
    monitors = [_info("DELL-U27-0-SN0", 0), _info("DELL-U27-1-SN1", 1)]

    result = reconcile_layout_with_monitors(layout, monitors)

    assert len(result.monitors) == 2
    # The hinted entry claimed qt_index 1; the new entry is the unclaimed index 0.
    assert result.monitors[0].identifier == "DELL-U27-1-SN1"
    assert result.monitors[1].identifier == "DELL-U27-0-SN0"


def test_synthetic_index_identifier_is_not_persisted() -> None:
    """MonitorService falls back to 'index-<n>' when EDID is unavailable; that
    string is positional and must not be stored as a durable key."""
    layout = ScreenLayout(id="dev-default-layout", name="dev default", monitors=[])
    monitors = [_info("index-0", 0), _info("index-1", 1)]

    result = reconcile_layout_with_monitors(layout, monitors)

    assert [m.identifier for m in result.monitors] == [None, None]
    assert [m.monitor_index_hint for m in result.monitors] == [0, 1]


def test_stale_identifier_without_hint_falls_through_to_positional() -> None:
    """The renderer's ``_resolve_monitor_info`` falls through to positional
    matching when a set identifier matches nothing and no hint is present, so
    it draws such an entry.  The reconciler must agree — otherwise it appends a
    second entry for a display already on screen, producing a duplicate."""
    layout = ScreenLayout(
        id="dev-default-layout",
        name="dev default",
        monitors=[
            Monitor(identifier="OLD-MONITOR-GONE", viewports=[_viewport("dev-vp-0", "web-01")]),
        ],
    )
    monitors = [_info("DELL-U27-0-SN0", 0)]

    result = reconcile_layout_with_monitors(layout, monitors)

    assert len(result.monitors) == 1, "stale-identifier entry must not be duplicated"
    assert [p.connection_id for p in result.monitors[0].viewports[0].panes] == ["web-01"]


def test_stale_identifier_with_unmatched_hint_is_a_ghost() -> None:
    """A hint is terminal in the renderer: it resolves to None rather than
    falling through, so the entry renders as a ghost and the live monitor it
    does not cover is genuinely unclaimed."""
    layout = ScreenLayout(
        id="dev-default-layout",
        name="dev default",
        monitors=[
            Monitor(
                identifier="OLD-MONITOR-GONE",
                monitor_index_hint=7,
                viewports=[_viewport("dev-vp-0", "web-01")],
            ),
        ],
    )
    monitors = [_info("DELL-U27-0-SN0", 0)]

    result = reconcile_layout_with_monitors(layout, monitors)

    assert len(result.monitors) == 2
    assert result.monitors[0].identifier == "OLD-MONITOR-GONE"  # retained as ghost
    assert result.monitors[1].identifier == "DELL-U27-0-SN0"


def test_stale_identifier_still_reports_needs_reconcile_correctly() -> None:
    layout = ScreenLayout(
        id="dev-default-layout",
        name="dev default",
        monitors=[Monitor(identifier="OLD-MONITOR-GONE", viewports=[_viewport("dev-vp-0")])],
    )
    assert layout_needs_reconcile(layout, [_info("DELL-U27-0-SN0", 0)]) is False
    assert (
        layout_needs_reconcile(layout, [_info("DELL-U27-0-SN0", 0), _info("DELL-U27-1-SN1", 1)])
        is True
    )


def test_two_hintless_entries_do_not_claim_the_same_monitor() -> None:
    layout = ScreenLayout(
        id="dev-default-layout",
        name="dev default",
        monitors=[
            Monitor(viewports=[_viewport("dev-vp-0")]),
            Monitor(viewports=[_viewport("dev-vp-1")]),
        ],
    )
    monitors = [_info("DELL-U27-0-SN0", 0), _info("DELL-U27-1-SN1", 1)]

    result = reconcile_layout_with_monitors(layout, monitors)

    assert len(result.monitors) == 2
    assert result.monitors[0].identifier == "DELL-U27-0-SN0"
    assert result.monitors[1].identifier == "DELL-U27-1-SN1"


# ---------------------------------------------------------------------------
# layout_needs_reconcile
# ---------------------------------------------------------------------------


def test_needs_reconcile_true_when_a_monitor_is_unclaimed() -> None:
    layout = _two_monitor_layout()
    monitors = [_info("DELL-U27-0-SN0", 0), _info("DELL-U27-1-SN1", 1), _info("DELL-U27-2-SN2", 2)]
    assert layout_needs_reconcile(layout, monitors) is True


def test_needs_reconcile_false_when_layout_already_covers_all() -> None:
    layout = _two_monitor_layout()
    monitors = [_info("DELL-U27-0-SN0", 0), _info("DELL-U27-1-SN1", 1)]
    assert layout_needs_reconcile(layout, monitors) is False


def test_needs_reconcile_false_for_empty_snapshot() -> None:
    assert layout_needs_reconcile(_two_monitor_layout(), []) is False


def test_needs_reconcile_false_when_a_monitor_is_merely_disconnected() -> None:
    """Fewer live monitors than entries is not a reason to rewrite the layout."""
    layout = _two_monitor_layout()
    assert layout_needs_reconcile(layout, [_info("DELL-U27-0-SN0", 0)]) is False


# ---------------------------------------------------------------------------
# Identical monitors with no EDID serial (ambiguous identifiers)
# ---------------------------------------------------------------------------


def _no_serial(qt_index: int) -> MonitorInfo:
    """A panel that reports make and model but no serial — so two of the same
    model produce byte-identical identifiers. Common on matched desk setups,
    which is the exact hardware behind the original 3-monitor report."""
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


def test_identical_monitors_each_get_their_own_entry() -> None:
    """Three identical panels must produce three entries, not collapse into
    one. The renderer's ident_map is a plain dict, so a duplicated identifier
    would make several entries resolve to the same physical display — drawing
    one twice and leaving the others blank."""
    layout = ScreenLayout(id="dev-default-layout", name="dev default", monitors=[])
    monitors = [_no_serial(0), _no_serial(1), _no_serial(2)]

    result = reconcile_layout_with_monitors(layout, monitors)

    assert len(result.monitors) == 3
    assert [m.monitor_index_hint for m in result.monitors] == [0, 1, 2]


def test_ambiguous_identifier_is_never_persisted() -> None:
    """An identifier shared by several attached panels names none of them, so
    storing it would be worse than storing nothing — index matching must carry
    the association instead."""
    layout = ScreenLayout(id="dev-default-layout", name="dev default", monitors=[])
    monitors = [_no_serial(0), _no_serial(1)]

    result = reconcile_layout_with_monitors(layout, monitors)

    assert [m.identifier for m in result.monitors] == [None, None]
    assert [m.monitor_index_hint for m in result.monitors] == [0, 1]


def test_third_identical_monitor_is_added_to_a_stale_layout() -> None:
    """The reported bug, on matched hardware: 2 persisted entries, 3 attached
    identical panels — the third must still be detected."""
    layout = ScreenLayout(
        id="dev-default-layout",
        name="dev default",
        monitors=[
            Monitor(monitor_index_hint=0, viewports=[_viewport("dev-vp-0", "web-01")]),
            Monitor(monitor_index_hint=1, viewports=[_viewport("dev-vp-1", "db-01")]),
        ],
    )
    monitors = [_no_serial(0), _no_serial(1), _no_serial(2)]

    result = reconcile_layout_with_monitors(layout, monitors)

    assert len(result.monitors) == 3
    assert result.monitors[2].monitor_index_hint == 2
    assert [p.connection_id for p in result.monitors[0].viewports[0].panes] == ["web-01"]
    assert [p.connection_id for p in result.monitors[1].viewports[0].panes] == ["db-01"]


def test_ambiguous_identifier_is_not_backfilled_onto_existing_entries() -> None:
    layout = ScreenLayout(
        id="dev-default-layout",
        name="dev default",
        monitors=[
            Monitor(viewports=[_viewport("dev-vp-0")]),
            Monitor(viewports=[_viewport("dev-vp-1")]),
        ],
    )
    monitors = [_no_serial(0), _no_serial(1)]

    result = reconcile_layout_with_monitors(layout, monitors)

    assert [m.identifier for m in result.monitors] == [None, None]
    assert len(result.monitors) == 2, "no spurious extra entry"


def _renderer_resolve(layout: ScreenLayout, monitors: list[MonitorInfo]) -> list[int | None]:
    """Return the ``qt_index`` each layout entry resolves to, using the
    renderer's OWN resolver.

    Asserting on the reconciler's output alone is not enough for the collision
    case: the renderer resolves independently, and its identifier map is keyed
    by identifier, so two entries naming one duplicated identifier collapse
    onto whichever monitor wins — drawing it twice and leaving the other blank.

    This imports ``screen_map.resolve_monitor_info`` rather than reimplementing
    it. A local copy would be a self-fulfilling oracle: a change to the real
    renderer that broke this mapping would leave the copy — and so the test —
    happily passing.
    """
    from cpsm.ui.widgets.screen_map import resolve_monitor_info

    resolved: list[int | None] = []
    for schema_monitor in layout.monitors:
        info = resolve_monitor_info(schema_monitor, layout, monitors)
        resolved.append(info.qt_index if info is not None else None)
    return resolved


def test_stale_entries_naming_an_ambiguous_identifier_are_repaired() -> None:
    """The realistic upgrade case: a config persisted before ambiguity was
    understood already carries the duplicated identifier on every entry.

    Skipping the identifier during matching is not enough — the renderer
    resolves on its own and would still collapse both entries onto one display.
    The reconciler must actively clear the useless identifier and pin each
    entry to the display it matched.
    """
    layout = ScreenLayout(
        id="dev-default-layout",
        name="dev default",
        monitors=[
            Monitor(identifier="DELL-U2723QE", viewports=[_viewport("dev-vp-0", "web-01")]),
            Monitor(identifier="DELL-U2723QE", viewports=[_viewport("dev-vp-1", "db-01")]),
        ],
    )
    monitors = [_no_serial(0), _no_serial(1)]

    # The un-repaired layout genuinely is broken in the renderer.
    assert _renderer_resolve(layout, monitors) == [1, 1], (
        "precondition: the persisted layout collapses onto one display"
    )
    assert layout_needs_reconcile(layout, monitors) is True, (
        "a collapsed layout must be reported as needing work, or nothing repairs it"
    )

    result = reconcile_layout_with_monitors(layout, monitors)

    assert len(result.monitors) == 2
    assert [m.identifier for m in result.monitors] == [None, None]
    assert [m.monitor_index_hint for m in result.monitors] == [0, 1]
    # Pane assignments survive the repair.
    assert [p.connection_id for p in result.monitors[0].viewports[0].panes] == ["web-01"]
    assert [p.connection_id for p in result.monitors[1].viewports[0].panes] == ["db-01"]
    # And the renderer now resolves them to two DISTINCT displays.
    resolved = _renderer_resolve(result, monitors)
    assert resolved == [0, 1], resolved
    assert len(set(resolved)) == len(resolved), "each entry must own a different display"


def test_repair_is_idempotent_and_settles() -> None:
    """Once repaired, the layout must stop reporting as needing work — else the
    app rewrites ~/.cpsm.yaml on every single render."""
    layout = ScreenLayout(
        id="dev-default-layout",
        name="dev default",
        monitors=[
            Monitor(identifier="DELL-U2723QE", viewports=[_viewport("dev-vp-0", "web-01")]),
            Monitor(identifier="DELL-U2723QE", viewports=[_viewport("dev-vp-1", "db-01")]),
        ],
    )
    monitors = [_no_serial(0), _no_serial(1)]

    once = reconcile_layout_with_monitors(layout, monitors)
    assert layout_needs_reconcile(once, monitors) is False
    twice = reconcile_layout_with_monitors(once, monitors)
    assert twice.model_dump() == once.model_dump()


def test_three_identical_panels_all_resolve_distinctly_after_repair() -> None:
    """The reported hardware, upgraded: a 2-entry collapsed layout with three
    identical panels attached must end up with three entries resolving to three
    different displays."""
    layout = ScreenLayout(
        id="dev-default-layout",
        name="dev default",
        monitors=[
            Monitor(identifier="DELL-U2723QE", viewports=[_viewport("dev-vp-0", "web-01")]),
            Monitor(identifier="DELL-U2723QE", viewports=[_viewport("dev-vp-1", "db-01")]),
        ],
    )
    monitors = [_no_serial(0), _no_serial(1), _no_serial(2)]

    result = reconcile_layout_with_monitors(layout, monitors)

    assert len(result.monitors) == 3
    resolved = _renderer_resolve(result, monitors)
    assert sorted(resolved) == [0, 1, 2], resolved
    assert len(set(resolved)) == 3, "no display may be drawn twice"


def test_unique_identifiers_still_take_precedence_over_index() -> None:
    """The ambiguity guard must not disturb the normal EDID path: a display
    that moved to a different qt_index is still matched by identifier."""
    layout = _two_monitor_layout()
    # Same two displays, order swapped by a cable change.
    monitors = [_info("DELL-U27-1-SN1", 0), _info("DELL-U27-0-SN0", 1)]

    result = reconcile_layout_with_monitors(layout, monitors)

    assert len(result.monitors) == 2, "no new entry — both displays are known"
    assert [p.connection_id for p in result.monitors[0].viewports[0].panes] == ["web-01"]


def test_mixed_ambiguous_and_unique_identifiers() -> None:
    """Two identical panels plus one distinct display: the distinct one keeps
    its stable identifier, the ambiguous pair fall back to index hints."""
    layout = ScreenLayout(id="dev-default-layout", name="dev default", monitors=[])
    monitors = [_no_serial(0), _no_serial(1), _info("DELL-U27-9-SN9", 2)]

    result = reconcile_layout_with_monitors(layout, monitors)

    assert len(result.monitors) == 3
    assert [m.identifier for m in result.monitors] == [None, None, "DELL-U27-9-SN9"]
