# -*- coding: utf-8 -*-
"""
tests/ui/test_screen_map_drop_zones.py — drop-zone resolver unit tests.

Spec section: §6.6

Tests cover:
  - All nine cells of the 3x3 zone grid: TL/T/TR/L/C/R/BL/B/BR.
  - Corner disambiguation (dominant edge wins).
  - Modifier-key overrides: Shift → horizontal split, Ctrl → vertical split.
  - Edge cases: degenerate pane sizes, boundary positions.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from cpsm.ui.widgets.screen_map import resolve_drop_zone

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

W = 100.0  # pane width
H = 100.0  # pane height
T = 0.20  # edge threshold fraction


def _edge_frac(frac: float) -> float:
    """Return a position that is *frac* of the edge threshold into an edge."""
    return T * frac * W  # works for equal W/H


# ---------------------------------------------------------------------------
# Core 3x3 grid tests (pure zones, no corners)
# ---------------------------------------------------------------------------


class TestPureZones:
    """Positions that are clearly within a single non-corner zone."""

    def test_center(self) -> None:
        assert resolve_drop_zone(50, 50, W, H) == "center"

    def test_top_center(self) -> None:
        # y = 5 → 5% from top < 20% threshold
        assert resolve_drop_zone(50, 5, W, H) == "top"

    def test_bottom_center(self) -> None:
        # y = 95 → 95% > 80%
        assert resolve_drop_zone(50, 95, W, H) == "bottom"

    def test_left_center(self) -> None:
        # x = 5 → 5% from left
        assert resolve_drop_zone(5, 50, W, H) == "left"

    def test_right_center(self) -> None:
        # x = 95 → 95% > 80%
        assert resolve_drop_zone(95, 50, W, H) == "right"


# ---------------------------------------------------------------------------
# Parametrised zone tests covering every named cell
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "local_x, local_y, expected",
    [
        # --- Center column, not on any edge ---
        (50, 50, "center"),
        # --- Pure top edge ---
        (50, 10, "top"),
        (50, 1, "top"),
        (50, 0, "top"),
        # --- Pure bottom edge ---
        (50, 90, "bottom"),
        (50, 99, "bottom"),
        (50, 100, "bottom"),
        # --- Pure left edge ---
        (10, 50, "left"),
        (1, 50, "left"),
        (0, 50, "left"),
        # --- Pure right edge ---
        (90, 50, "right"),
        (99, 50, "right"),
        (100, 50, "right"),
        # --- TL corner: equal depth → top wins (tie-break: top >= left) ---
        (10, 10, "top"),  # depth_top = 0.10, depth_left = 0.10 → top (>=)
        # --- TR corner: x=90 (depth_right=10), y=10 (depth_top=10) → top ---
        (90, 10, "top"),
        # --- BL corner: x=10 (depth_left=10), y=90 (depth_bottom=10) → left (fp tie-break) ---
        (10, 90, "left"),
        # --- BR corner: x=90, y=90 → bottom ---
        (90, 90, "bottom"),
        # --- TL corner: deeper into left than top → left ---
        (5, 15, "left"),  # depth_top = 5, depth_left = 15 → left (15 > 5)
        # --- TL corner: deeper into top than left → top ---
        (15, 5, "top"),  # depth_top = 15, depth_left = 5 → top
        # --- TR corner: deeper into right than top → right ---
        (97, 15, "right"),  # depth_top=5, depth_right=17 → right
        # --- TR corner: deeper into top than right → top ---
        (85, 3, "top"),  # depth_top=17, depth_right=5 → top
        # --- BL corner: deeper into bottom than left → bottom ---
        (15, 97, "bottom"),
        # --- BL corner: deeper into left than bottom → left ---
        (3, 85, "left"),
        # --- BR corner: deeper into bottom than right → bottom ---
        (85, 97, "bottom"),
        # --- BR corner: deeper into right than bottom → right ---
        (97, 85, "right"),
    ],
)
def test_zone_grid(local_x: float, local_y: float, expected: str) -> None:
    result = resolve_drop_zone(local_x, local_y, W, H)
    assert result == expected, (
        f"resolve_drop_zone({local_x}, {local_y}, {W}, {H}) = {result!r}, expected {expected!r}"
    )


# ---------------------------------------------------------------------------
# Non-square pane (W ≠ H)
# ---------------------------------------------------------------------------


class TestNonSquarePane:
    """Ensure threshold is computed relative to the respective dimension."""

    def test_wide_pane_top(self) -> None:
        # 200 wide x 50 tall; y=5 is 10% of 50 < 20% → top
        assert resolve_drop_zone(100, 5, 200, 50) == "top"

    def test_wide_pane_center(self) -> None:
        # x=100 is centre, y=25 is centre
        assert resolve_drop_zone(100, 25, 200, 50) == "center"

    def test_wide_pane_right(self) -> None:
        # x=195 is > 80% of 200
        assert resolve_drop_zone(195, 25, 200, 50) == "right"

    def test_tall_pane_bottom(self) -> None:
        # 50 wide x 200 tall; y=195 > 80% → bottom
        assert resolve_drop_zone(25, 195, 50, 200) == "bottom"


# ---------------------------------------------------------------------------
# Degenerate pane sizes
# ---------------------------------------------------------------------------


class TestDegeneratePanes:
    def test_zero_width(self) -> None:
        assert resolve_drop_zone(0, 0, 0, 100) == "center"

    def test_zero_height(self) -> None:
        assert resolve_drop_zone(0, 0, 100, 0) == "center"

    def test_zero_both(self) -> None:
        assert resolve_drop_zone(0, 0, 0, 0) == "center"


# ---------------------------------------------------------------------------
# Modifier-key overrides (§6.7)
# ---------------------------------------------------------------------------

# The modifier override is applied by LayoutController._zone_for_modifiers,
# not by resolve_drop_zone itself.  We test the controller helper directly.


class TestModifierOverride:
    """Test _zone_for_modifiers from the layout_controller module."""

    def _call(self, zone: str, shift: bool = False, ctrl: bool = False) -> str:
        from PySide6.QtCore import Qt

        from cpsm.controllers.layout_controller import _zone_for_modifiers

        mods = Qt.KeyboardModifier.NoModifier
        if shift:
            mods |= Qt.KeyboardModifier.ShiftModifier
        if ctrl:
            mods |= Qt.KeyboardModifier.ControlModifier
        return _zone_for_modifiers(zone, mods.value)

    def test_no_modifier_top_stays_top(self) -> None:
        assert self._call("top") == "top"

    def test_no_modifier_center_stays_center(self) -> None:
        assert self._call("center") == "center"

    def test_shift_forces_horizontal_from_top(self) -> None:
        # Shift → "right" (horizontal split)
        assert self._call("top", shift=True) == "right"

    def test_shift_forces_horizontal_from_left(self) -> None:
        assert self._call("left", shift=True) == "right"

    def test_shift_forces_horizontal_from_center(self) -> None:
        assert self._call("center", shift=True) == "right"

    def test_ctrl_forces_vertical_from_left(self) -> None:
        # Ctrl → "bottom" (vertical split)
        assert self._call("left", ctrl=True) == "bottom"

    def test_ctrl_forces_vertical_from_right(self) -> None:
        assert self._call("right", ctrl=True) == "bottom"

    def test_ctrl_forces_vertical_from_center(self) -> None:
        assert self._call("center", ctrl=True) == "bottom"

    def test_shift_takes_precedence_over_ctrl(self) -> None:
        # Both held → Shift wins → "right"
        assert self._call("top", shift=True, ctrl=True) == "right"


# ---------------------------------------------------------------------------
# Zone-to-split direction mapping
# ---------------------------------------------------------------------------


class TestZoneToSplit:
    """Test the _ZONE_TO_SPLIT mapping used by LayoutController."""

    def _check(self, zone: str) -> tuple[str, bool]:
        from cpsm.controllers.layout_controller import _ZONE_TO_SPLIT

        return _ZONE_TO_SPLIT[zone]

    def test_top_is_vertical_before(self) -> None:
        assert self._check("top") == ("v", True)

    def test_bottom_is_vertical_after(self) -> None:
        assert self._check("bottom") == ("v", False)

    def test_left_is_horizontal_before(self) -> None:
        assert self._check("left") == ("h", True)

    def test_right_is_horizontal_after(self) -> None:
        assert self._check("right") == ("h", False)


# ---------------------------------------------------------------------------
# ScreenMapWidget pane_at_scene_pos integration
# ---------------------------------------------------------------------------


class TestPaneHitTest:
    """Test that pane_at_scene_pos correctly identifies panes by scene coords."""

    def test_pane_found_at_center(self, qtbot) -> None:
        from PySide6.QtCore import QObject, Signal

        from cpsm.data.schema import GeometryPct, Monitor, Pane, ScreenLayout, Viewport
        from cpsm.services.monitor_service import MonitorInfo
        from cpsm.ui.widgets.screen_map import ScreenMapWidget

        class _FakeMon(QObject):
            monitor_added: Signal = Signal(object)
            monitor_removed: Signal = Signal(str)

            def snapshot(self):
                return [_mi]

        _mi = MonitorInfo(
            identifier="m1",
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
        layout = ScreenLayout(
            id="layout-ht",
            name="HT",
            monitors=[
                Monitor(
                    identifier="m1",
                    viewports=[
                        Viewport(
                            id="vp-ht",
                            geometry_pct=GeometryPct(x=0, y=0, w=100, h=100),
                            tmux_layout="tiled",
                            panes=[Pane(connection_id=None)],
                        )
                    ],
                )
            ],
        )
        svc = _FakeMon()
        w = ScreenMapWidget(monitor_service=svc, connection_lookup=lambda _: None)
        qtbot.addWidget(w)
        w.set_layout(layout, [_mi])

        assert len(w.pane_registry) == 1
        rec = w.pane_registry[0]
        # Centre of the pane should hit
        cx = rec.scene_x + rec.scene_w / 2
        cy = rec.scene_y + rec.scene_h / 2
        found = w.pane_at_scene_pos(cx, cy)
        assert found is not None
        assert found.pane_id == rec.pane_id

    def test_pane_not_found_outside(self, qtbot) -> None:
        from cpsm.ui.widgets.screen_map import ScreenMapWidget

        w = ScreenMapWidget()
        qtbot.addWidget(w)
        # No layout set → empty registry
        assert w.pane_at_scene_pos(9999, 9999) is None
