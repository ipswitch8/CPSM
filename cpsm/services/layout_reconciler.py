# -*- coding: utf-8 -*-
"""
cpsm.services.layout_reconciler — reconcile a persisted :class:`ScreenLayout`
against the live monitor snapshot from
:class:`~cpsm.services.monitor_service.MonitorService`.

Why this exists
---------------
``cpsm/ui/widgets/screen_map.py`` renders by walking ``layout.monitors`` (the
*persisted* layout read from ``~/.cpsm.yaml``) and resolving each entry against
the live monitor list.  A physically-connected monitor that has no entry in the
persisted layout is therefore never drawn — which is why a layout authored when
two displays were attached kept showing two displays for weeks after a third was
plugged in, across many restarts.

Reconciliation closes that gap: it ADDS an entry for every live monitor the
layout does not already claim, while leaving every existing entry — and its
viewport / pane assignments — untouched.  Entries whose monitor is no longer
connected are deliberately RETAINED so the existing ghost-rendering path
(``_collect_ghost_records``) keeps working.

The function is pure: it never mutates its arguments and returns a new
``ScreenLayout``.  It is idempotent — reconciling an already-reconciled layout
against the same snapshot yields an equal layout.
"""

from __future__ import annotations

import logging

from cpsm.data.schema import GeometryPct, Monitor, ScreenLayout, Viewport
from cpsm.services.monitor_service import MonitorInfo

logger = logging.getLogger(__name__)

__all__ = ["layout_needs_reconcile", "reconcile_layout_with_monitors"]


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


def _ambiguous_identifiers(monitors: list[MonitorInfo]) -> set[str]:
    """Identifiers shared by more than one monitor in *monitors*.

    ``MonitorService`` derives its identifier from EDID manufacturer/model/
    serial, and plenty of panels report an empty serial.  Two displays of the
    same make and model then produce the *same* identifier string — the
    matched-pair or matched-triple desk setup, which is precisely the hardware
    that provoked the original "only two of my three monitors show up" report.

    Such an identifier cannot select a display.  Worse, the renderer builds
    ``ident_map`` as a plain ``{identifier: MonitorInfo}`` dict, so duplicates
    collapse: two layout entries would resolve to the *same* physical monitor,
    drawing one display twice and the other not at all.  Everything downstream
    therefore treats these identifiers as unusable and falls back to
    index-based matching.
    """
    seen: set[str] = set()
    duplicates: set[str] = set()
    for info in monitors:
        if info.identifier in seen:
            duplicates.add(info.identifier)
        seen.add(info.identifier)
    return duplicates


def _match_index(
    schema_monitor: Monitor,
    positional_index: int,
    monitors: list[MonitorInfo],
    claimed: set[int],
    ambiguous: set[str],
) -> int | None:
    """Return the index into *monitors* that *schema_monitor* refers to.

    Mirrors the resolution order used by ``screen_map._build_scene`` so the
    reconciler and the renderer always agree on which persisted entry owns
    which physical display:

    1. ``identifier`` exact match (stable across replug and restart — derived
       from EDID manufacturer/model/serial),
    2. ``monitor_index_hint`` matched against ``MonitorInfo.qt_index`` — and
       this branch is *terminal* in the renderer: a hint that matches nothing
       resolves to ``None`` rather than falling through,
    3. positional fallback, reached whenever no hint is set — the entry's own
       position in ``layout.monitors`` matched against ``qt_index``.

    Note that step 3 is reached even when ``identifier`` is set but stale (the
    display it named is not currently attached).  That mirrors the renderer,
    which draws such an entry positionally.  Treating it as unmatched here
    would make the reconciler append a *second* entry for a display the
    renderer is already drawing — a visible duplicate.

    A live monitor already *claimed* by an earlier entry is never handed out
    twice; that keeps two hint-less persisted entries from collapsing onto the
    same physical display and leaving a real one looking "unclaimed".

    *ambiguous* holds identifiers shared by several attached displays (see
    :func:`_ambiguous_identifiers`).  Step 1 is skipped for those, since such
    an identifier names no particular display; matching falls through to the
    hint or positional branch instead.
    """
    if schema_monitor.identifier and schema_monitor.identifier not in ambiguous:
        for i, info in enumerate(monitors):
            if i not in claimed and info.identifier == schema_monitor.identifier:
                return i

    if schema_monitor.monitor_index_hint is not None:
        for i, info in enumerate(monitors):
            if i not in claimed and info.qt_index == schema_monitor.monitor_index_hint:
                return i
        return None

    for i, info in enumerate(monitors):
        if i not in claimed and info.qt_index == positional_index:
            return i

    return None


def _is_synthetic_identifier(identifier: str) -> bool:
    """True for the ``index-<n>`` fallback identifier MonitorService emits when
    EDID data is unavailable.  Such identifiers are positional, not stable, so
    they must never be persisted as if they were durable EDID keys."""
    return identifier.startswith("index-")


def _next_viewport_serial(layout: ScreenLayout) -> int:
    """Return a starting serial that will not collide with any existing
    ``viewport.id`` of the form ``<layout-id>-vp-<n>``."""
    prefix = f"{layout.id}-vp-"
    highest = -1
    for schema_monitor in layout.monitors:
        for vp in schema_monitor.viewports:
            if vp.id.startswith(prefix):
                suffix = vp.id[len(prefix) :]
                if suffix.isdigit():
                    highest = max(highest, int(suffix))
    return highest + 1


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def reconcile_layout_with_monitors(
    layout: ScreenLayout,
    monitors: list[MonitorInfo],
) -> ScreenLayout:
    """Return a copy of *layout* extended to cover every monitor in *monitors*.

    Parameters
    ----------
    layout:
        The persisted :class:`~cpsm.data.schema.ScreenLayout`.  Never mutated.
    monitors:
        Live monitor snapshot, normally ``MonitorService.snapshot()``.

    Returns
    -------
    ScreenLayout
        A new layout in which:

        * every pre-existing ``Monitor`` entry is preserved verbatim, including
          its viewports and pane assignments — except that a bare entry which
          resolved positionally gets its stable ``identifier`` backfilled so
          future matches survive display re-ordering;
        * every live monitor with no owning entry gains a new entry carrying one
          full-screen viewport and no panes;
        * every entry whose monitor is currently disconnected is retained, so
          the renderer still draws it as a ghost.

    Notes
    -----
    When *monitors* is empty (headless, or the snapshot failed) the layout is
    returned unchanged — an empty snapshot means "we don't know", not "no
    displays exist", and must never be allowed to look like a mass unplug.
    """
    if not monitors:
        return layout.model_copy(deep=True)

    result = layout.model_copy(deep=True)
    ambiguous = _ambiguous_identifiers(monitors)

    claimed: set[int] = set()
    for positional_index, schema_monitor in enumerate(result.monitors):
        matched = _match_index(schema_monitor, positional_index, monitors, claimed, ambiguous)
        if matched is None:
            continue
        claimed.add(matched)

        info = monitors[matched]

        # REPAIR an ambiguous identifier already persisted on this entry.
        # Skipping it during matching is not enough: the renderer resolves
        # independently, and its ``ident_map`` is a plain dict, so two entries
        # naming the same duplicated identifier both collapse onto whichever
        # monitor happens to win the dict — drawing that one twice and leaving
        # the other blank.  Any layout written before ambiguity was understood
        # is in exactly that shape, so it must be actively repaired rather than
        # merely tolerated: clear the useless identifier and pin the entry to
        # the display it actually matched.
        if schema_monitor.identifier and schema_monitor.identifier in ambiguous:
            schema_monitor.identifier = None
            schema_monitor.monitor_index_hint = info.qt_index
            logger.info(
                "reconcile: cleared ambiguous identifier on layout %r monitor %d; "
                "pinned to qt_index %d",
                result.id,
                positional_index,
                info.qt_index,
            )
            continue

        # Backfill a stable EDID identifier onto entries that resolved by hint
        # or by position.  Without this, unplugging any display shifts every
        # later qt_index and silently reassigns persisted viewports to the
        # wrong physical screen.  An identifier shared by several attached
        # displays is never backfilled — it would name no particular one; such
        # entries get a hint instead.
        if (not schema_monitor.identifier and _is_synthetic_identifier(info.identifier)) or (
            not schema_monitor.identifier and info.identifier in ambiguous
        ):
            if schema_monitor.monitor_index_hint is None:
                schema_monitor.monitor_index_hint = info.qt_index
        elif not schema_monitor.identifier:
            schema_monitor.identifier = info.identifier
            logger.debug(
                "reconcile: backfilled identifier %r onto layout %r monitor %d",
                info.identifier,
                result.id,
                positional_index,
            )

    unclaimed = [info for i, info in enumerate(monitors) if i not in claimed]
    if not unclaimed:
        return result

    serial = _next_viewport_serial(result)
    for info in unclaimed:
        viewport = Viewport(
            id=f"{result.id}-vp-{serial}",
            geometry_pct=GeometryPct(x=0, y=0, w=100, h=100),
            tmux_window_name=f"{result.id}-{serial}",
            tmux_layout="tiled",
            panes=[],
        )
        serial += 1

        # Store an identifier only when it can actually select this display.
        # A synthetic "index-<n>" is positional, and an identifier shared by
        # several attached panels names none of them — persisting either would
        # make the renderer's ident_map collapse two entries onto one monitor,
        # drawing it twice and leaving the other blank.  monitor_index_hint
        # carries the match in those cases.
        identifier = (
            None
            if (_is_synthetic_identifier(info.identifier) or info.identifier in ambiguous)
            else info.identifier
        )
        result.monitors.append(
            Monitor(
                identifier=identifier,
                monitor_index_hint=info.qt_index,
                viewports=[viewport],
            )
        )
        logger.info(
            "reconcile: added newly-detected monitor %r (qt_index=%d) to layout %r",
            info.identifier,
            info.qt_index,
            result.id,
        )

    return result


def layout_needs_reconcile(layout: ScreenLayout, monitors: list[MonitorInfo]) -> bool:
    """True when reconciling *layout* against *monitors* would change it.

    That covers two cases:

    * a live monitor that no entry claims — the display is missing from the
      layout and needs adding;
    * an entry carrying an identifier shared by several attached displays —
      the layout is already corrupt and needs repairing, even though the
      monitor *count* is right. Without this second case the caller would
      short-circuit and render the collapsed layout unchanged, which is
      precisely the state every pre-existing config with matched panels is in.

    Callers use this to avoid marking a document dirty (and re-rendering) when
    nothing has actually changed.
    """
    if not monitors:
        return False
    ambiguous = _ambiguous_identifiers(monitors)
    if any(m.identifier in ambiguous for m in layout.monitors if m.identifier):
        return True
    claimed: set[int] = set()
    for positional_index, schema_monitor in enumerate(layout.monitors):
        matched = _match_index(schema_monitor, positional_index, monitors, claimed, ambiguous)
        if matched is not None:
            claimed.add(matched)
    return len(claimed) < len(monitors)
