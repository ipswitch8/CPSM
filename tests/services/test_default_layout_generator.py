# -*- coding: utf-8 -*-
"""
tests/services/test_default_layout_generator.py

Unit tests for cpsm.services.default_layout_generator.generate_default_layout.

Change 1 — Auto-generate default Layout per Group.
"""

from __future__ import annotations

from cpsm.services.default_layout_generator import generate_default_layout
from cpsm.services.monitor_service import MonitorInfo

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _monitor(idx: int = 0) -> MonitorInfo:
    """Return a minimal MonitorInfo for testing."""
    return MonitorInfo(
        identifier=f"index-{idx}",
        name=f"SCREEN-{idx}",
        geometry=(idx * 1920, 0, 1920, 1080),
        available_geometry=(idx * 1920, 0, 1920, 1080),
        physical_size_mm=(527.0, 296.0),
        device_pixel_ratio=1.0,
        orientation="landscape",
        manufacturer="",
        model="",
        serial="",
        qt_index=idx,
    )


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_zero_members_returns_empty_layout() -> None:
    """0 members → empty layout with no monitors."""
    layout = generate_default_layout(
        group_id="my-group",
        member_count=0,
        monitors=[_monitor(0)],
    )
    assert layout.id == "my-group-default-layout"
    assert layout.monitors == []


def test_zero_monitors_returns_empty_layout() -> None:
    """0 monitors → empty layout with no monitors."""
    layout = generate_default_layout(
        group_id="my-group",
        member_count=3,
        monitors=[],
    )
    assert layout.id == "my-group-default-layout"
    assert layout.monitors == []


def test_zero_members_and_zero_monitors_empty() -> None:
    """Both 0 members and 0 monitors → empty layout."""
    layout = generate_default_layout(
        group_id="test-grp",
        member_count=0,
        monitors=[],
    )
    assert layout.monitors == []


# ---------------------------------------------------------------------------
# Single monitor
# ---------------------------------------------------------------------------


def test_one_member_one_monitor_single_pane() -> None:
    """1 member, 1 monitor → 1 viewport with 1 pane."""
    layout = generate_default_layout(
        group_id="grp-a",
        member_count=1,
        monitors=[_monitor(0)],
    )
    assert len(layout.monitors) == 1
    monitor = layout.monitors[0]
    assert len(monitor.viewports) == 1
    vp = monitor.viewports[0]
    assert len(vp.panes) == 1
    assert vp.panes[0].connection_id is None  # placeholder


def test_four_members_one_monitor_four_panes() -> None:
    """4 members, 1 monitor → 1 viewport with 4 panes."""
    layout = generate_default_layout(
        group_id="grp-b",
        member_count=4,
        monitors=[_monitor(0)],
    )
    assert len(layout.monitors) == 1
    vp = layout.monitors[0].viewports[0]
    assert len(vp.panes) == 4


# ---------------------------------------------------------------------------
# Multiple monitors — round-robin distribution
# ---------------------------------------------------------------------------


def test_four_members_two_monitors_two_each() -> None:
    """4 members, 2 monitors → 2 viewports with 2 panes each (round-robin)."""
    layout = generate_default_layout(
        group_id="grp-c",
        member_count=4,
        monitors=[_monitor(0), _monitor(1)],
    )
    assert len(layout.monitors) == 2
    panes0 = layout.monitors[0].viewports[0].panes
    panes1 = layout.monitors[1].viewports[0].panes
    assert len(panes0) == 2
    assert len(panes1) == 2


def test_five_members_two_monitors_three_two() -> None:
    """5 members, 2 monitors → 3 panes on monitor 0, 2 on monitor 1."""
    layout = generate_default_layout(
        group_id="grp-d",
        member_count=5,
        monitors=[_monitor(0), _monitor(1)],
    )
    assert len(layout.monitors) == 2
    panes0 = layout.monitors[0].viewports[0].panes
    panes1 = layout.monitors[1].viewports[0].panes
    assert len(panes0) == 3
    assert len(panes1) == 2


# ---------------------------------------------------------------------------
# Viewport ids and tmux_layout
# ---------------------------------------------------------------------------


def test_viewport_ids_follow_pattern() -> None:
    """Viewport ids must follow the documented pattern ``{group_id}-vp-{monitor_index}``."""
    layout = generate_default_layout(
        group_id="my-grp",
        member_count=2,
        monitors=[_monitor(0), _monitor(1)],
    )
    vp_ids = [vp.id for m in layout.monitors for vp in m.viewports]
    assert "my-grp-vp-0" in vp_ids
    assert "my-grp-vp-1" in vp_ids


def test_viewport_ids_are_unique() -> None:
    """All viewport ids within the layout must be unique."""
    layout = generate_default_layout(
        group_id="unique-grp",
        member_count=3,
        monitors=[_monitor(0), _monitor(1), _monitor(2)],
    )
    vp_ids = [vp.id for m in layout.monitors for vp in m.viewports]
    assert len(vp_ids) == len(set(vp_ids))


def test_tmux_layout_is_tiled() -> None:
    """Each viewport must use ``tmux_layout="tiled"``."""
    layout = generate_default_layout(
        group_id="tiled-grp",
        member_count=2,
        monitors=[_monitor(0), _monitor(1)],
    )
    for monitor in layout.monitors:
        for vp in monitor.viewports:
            assert vp.tmux_layout == "tiled"


# ---------------------------------------------------------------------------
# Layout id and name
# ---------------------------------------------------------------------------


def test_layout_id_format() -> None:
    """Layout id must be ``{group_id}-default-layout``."""
    layout = generate_default_layout(
        group_id="proj-x",
        member_count=1,
        monitors=[_monitor(0)],
    )
    assert layout.id == "proj-x-default-layout"


def test_layout_name_uses_group_name_when_provided() -> None:
    """Layout name should use *group_name* when provided."""
    layout = generate_default_layout(
        group_id="proj-x",
        group_name="Project X",
        member_count=1,
        monitors=[_monitor(0)],
    )
    assert "Project X" in layout.name


def test_layout_name_falls_back_to_group_id() -> None:
    """Layout name falls back to group_id when group_name is None."""
    layout = generate_default_layout(
        group_id="proj-y",
        group_name=None,
        member_count=1,
        monitors=[_monitor(0)],
    )
    assert "proj-y" in layout.name
