# -*- coding: utf-8 -*-
"""Tests for LayoutService — split-and-lock invariant and pane removal.

Coverage target: ≥ 90%
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from cpsm.data.schema import (
    CpsmDocument,
    GeometryPct,
    Monitor,
    Pane,
    ScreenLayout,
    Settings,
    Viewport,
)
from cpsm.platform.base import MultiplexerBackend
from cpsm.services.layout_service import LayoutService, _replace_viewport_in_layout

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_backend() -> MagicMock:
    backend = MagicMock(spec=MultiplexerBackend)
    backend.capture_layout.return_value = "f8b6,220x50,0,0[...]"
    return backend


@pytest.fixture
def layout_service(mock_backend: MagicMock) -> LayoutService:
    return LayoutService(mock_backend)


def _make_viewport(vp_id: str = "vp-1", num_panes: int = 2) -> Viewport:
    panes = [Pane(connection_id=f"conn-{i}") for i in range(num_panes)]
    return Viewport(
        id=vp_id,
        geometry_pct=GeometryPct(x=0, y=0, w=100, h=100),
        tmux_layout="tiled",
        panes=panes,
    )


def _make_layout(layout_id: str = "ly-1", viewport: Viewport | None = None) -> ScreenLayout:
    vp = viewport or _make_viewport()
    monitor = Monitor(viewports=[vp])
    return ScreenLayout(id=layout_id, name="Test Layout", monitors=[monitor])


def _make_doc(*, preserve_on_remove: bool = True) -> CpsmDocument:
    settings = Settings(layout_preserve_on_remove=preserve_on_remove)
    return CpsmDocument(settings=settings)


# ---------------------------------------------------------------------------
# apply_change — split-and-lock invariant
# ---------------------------------------------------------------------------


class TestApplyChange:
    @pytest.mark.parametrize("change_kind", ["split", "swap", "resize", "respawn"])
    def test_captures_layout_and_sets_custom(
        self,
        layout_service: LayoutService,
        mock_backend: MagicMock,
        change_kind: str,
    ) -> None:
        vp = _make_viewport()
        layout = _make_layout(viewport=vp)
        doc = _make_doc()
        updated_layout = layout_service.apply_change(
            doc, layout, vp, change_kind=change_kind, window_target="session:0"
        )
        mock_backend.capture_layout.assert_called_once_with("session:0")
        # The viewport in the returned layout must have tmux_layout=custom
        updated_vp = updated_layout.monitors[0].viewports[0]
        assert updated_vp.tmux_layout == "custom"

    def test_custom_layout_string_set_from_capture(
        self, layout_service: LayoutService, mock_backend: MagicMock
    ) -> None:
        captured = "abc123,220x50,0,0"
        mock_backend.capture_layout.return_value = captured
        vp = _make_viewport()
        layout = _make_layout(viewport=vp)
        doc = _make_doc()
        updated_layout = layout_service.apply_change(
            doc, layout, vp, change_kind="split", window_target="session:0"
        )
        updated_vp = updated_layout.monitors[0].viewports[0]
        assert updated_vp.custom_layout_string == captured

    def test_original_layout_not_mutated(
        self, layout_service: LayoutService, mock_backend: MagicMock
    ) -> None:
        vp = _make_viewport()
        layout = _make_layout(viewport=vp)
        doc = _make_doc()
        layout_service.apply_change(doc, layout, vp, change_kind="split", window_target="session:0")
        # Original viewport still has the old tmux_layout
        original_vp = layout.monitors[0].viewports[0]
        assert original_vp.tmux_layout == "tiled"
        assert original_vp.custom_layout_string is None

    def test_other_viewports_untouched(
        self, layout_service: LayoutService, mock_backend: MagicMock
    ) -> None:
        vp1 = _make_viewport("vp-1")
        vp2 = _make_viewport("vp-2")
        # Two viewports side by side (no overlap)
        vp1 = vp1.model_copy(update={"geometry_pct": GeometryPct(x=0, y=0, w=50, h=100)})
        vp2 = vp2.model_copy(update={"geometry_pct": GeometryPct(x=50, y=0, w=50, h=100)})
        monitor = Monitor(viewports=[vp1, vp2])
        layout = ScreenLayout(id="ly-1", name="Test", monitors=[monitor])
        doc = _make_doc()
        updated_layout = layout_service.apply_change(
            doc, layout, vp1, change_kind="resize", window_target="session:0"
        )
        # vp2 is unchanged
        updated_vp2 = updated_layout.monitors[0].viewports[1]
        assert updated_vp2.tmux_layout == "tiled"  # unchanged

    def test_split_change_updates_only_target_viewport(
        self, layout_service: LayoutService, mock_backend: MagicMock
    ) -> None:
        vp1 = _make_viewport("vp-1")
        vp1 = vp1.model_copy(update={"geometry_pct": GeometryPct(x=0, y=0, w=50, h=100)})
        vp2 = _make_viewport("vp-2")
        vp2 = vp2.model_copy(update={"geometry_pct": GeometryPct(x=50, y=0, w=50, h=100)})
        monitor = Monitor(viewports=[vp1, vp2])
        layout = ScreenLayout(id="ly-1", name="Test", monitors=[monitor])
        doc = _make_doc()
        updated_layout = layout_service.apply_change(
            doc, layout, vp1, change_kind="split", window_target="session:0"
        )
        # vp1 updated
        assert updated_layout.monitors[0].viewports[0].tmux_layout == "custom"
        # vp2 NOT updated
        assert updated_layout.monitors[0].viewports[1].tmux_layout == "tiled"


# ---------------------------------------------------------------------------
# remove_pane — preserve_on_remove=True
# ---------------------------------------------------------------------------


class TestRemovePanePreserve:
    def test_respawn_pane_called_when_preserve(
        self, layout_service: LayoutService, mock_backend: MagicMock
    ) -> None:
        vp = _make_viewport(num_panes=3)
        layout = _make_layout(viewport=vp)
        doc = _make_doc(preserve_on_remove=True)
        layout_service.remove_pane(
            doc,
            layout,
            vp,
            1,
            window_target="session:0",
            pane_target="session:0.1",
            placeholder_command="bash /tmp/placeholder.sh",
        )
        mock_backend.respawn_pane.assert_called_once_with("session:0.1", "bash /tmp/placeholder.sh")
        mock_backend.kill_pane.assert_not_called()

    def test_pane_connection_id_set_to_none_when_preserve(
        self, layout_service: LayoutService, mock_backend: MagicMock
    ) -> None:
        vp = _make_viewport(num_panes=3)
        layout = _make_layout(viewport=vp)
        doc = _make_doc(preserve_on_remove=True)
        updated_layout = layout_service.remove_pane(
            doc,
            layout,
            vp,
            1,
            window_target="session:0",
            pane_target="session:0.1",
            placeholder_command="bash /tmp/placeholder.sh",
        )
        updated_vp = updated_layout.monitors[0].viewports[0]
        assert updated_vp.panes[1].connection_id is None

    def test_pane_count_unchanged_when_preserve(
        self, layout_service: LayoutService, mock_backend: MagicMock
    ) -> None:
        vp = _make_viewport(num_panes=3)
        layout = _make_layout(viewport=vp)
        doc = _make_doc(preserve_on_remove=True)
        updated_layout = layout_service.remove_pane(
            doc,
            layout,
            vp,
            1,
            window_target="session:0",
            pane_target="session:0.1",
            placeholder_command="bash /tmp/placeholder.sh",
        )
        updated_vp = updated_layout.monitors[0].viewports[0]
        assert len(updated_vp.panes) == 3  # same count

    def test_layout_locked_to_custom_when_preserve(
        self, layout_service: LayoutService, mock_backend: MagicMock
    ) -> None:
        vp = _make_viewport(num_panes=2)
        layout = _make_layout(viewport=vp)
        doc = _make_doc(preserve_on_remove=True)
        updated_layout = layout_service.remove_pane(
            doc,
            layout,
            vp,
            0,
            window_target="session:0",
            pane_target="session:0.0",
            placeholder_command="bash /tmp/placeholder.sh",
        )
        updated_vp = updated_layout.monitors[0].viewports[0]
        assert updated_vp.tmux_layout == "custom"
        assert updated_vp.custom_layout_string is not None


# ---------------------------------------------------------------------------
# remove_pane — preserve_on_remove=False
# ---------------------------------------------------------------------------


class TestRemovePaneKill:
    def test_kill_pane_called_when_no_preserve(
        self, layout_service: LayoutService, mock_backend: MagicMock
    ) -> None:
        vp = _make_viewport(num_panes=3)
        layout = _make_layout(viewport=vp)
        doc = _make_doc(preserve_on_remove=False)
        layout_service.remove_pane(
            doc,
            layout,
            vp,
            1,
            window_target="session:0",
            pane_target="session:0.1",
            placeholder_command="bash /tmp/placeholder.sh",
        )
        mock_backend.kill_pane.assert_called_once_with("session:0.1")
        mock_backend.respawn_pane.assert_not_called()

    def test_pane_removed_from_list_when_no_preserve(
        self, layout_service: LayoutService, mock_backend: MagicMock
    ) -> None:
        vp = _make_viewport(num_panes=3)
        layout = _make_layout(viewport=vp)
        doc = _make_doc(preserve_on_remove=False)
        updated_layout = layout_service.remove_pane(
            doc,
            layout,
            vp,
            1,
            window_target="session:0",
            pane_target="session:0.1",
            placeholder_command="bash /tmp/placeholder.sh",
        )
        updated_vp = updated_layout.monitors[0].viewports[0]
        assert len(updated_vp.panes) == 2

    def test_layout_locked_to_custom_when_no_preserve(
        self, layout_service: LayoutService, mock_backend: MagicMock
    ) -> None:
        vp = _make_viewport(num_panes=2)
        layout = _make_layout(viewport=vp)
        doc = _make_doc(preserve_on_remove=False)
        updated_layout = layout_service.remove_pane(
            doc,
            layout,
            vp,
            0,
            window_target="session:0",
            pane_target="session:0.0",
            placeholder_command="bash /tmp/placeholder.sh",
        )
        updated_vp = updated_layout.monitors[0].viewports[0]
        assert updated_vp.tmux_layout == "custom"
        assert updated_vp.custom_layout_string is not None

    def test_capture_layout_called_after_kill(
        self, layout_service: LayoutService, mock_backend: MagicMock
    ) -> None:
        vp = _make_viewport(num_panes=2)
        layout = _make_layout(viewport=vp)
        doc = _make_doc(preserve_on_remove=False)
        layout_service.remove_pane(
            doc,
            layout,
            vp,
            0,
            window_target="session:0",
            pane_target="session:0.0",
            placeholder_command="bash /tmp/placeholder.sh",
        )
        mock_backend.capture_layout.assert_called_once_with("session:0")


# ---------------------------------------------------------------------------
# Internal helper: _replace_viewport_in_layout
# ---------------------------------------------------------------------------


class TestReplaceViewportInLayout:
    def test_replaces_matching_viewport(self) -> None:
        vp = _make_viewport("vp-a")
        layout = _make_layout(viewport=vp)
        new_vp = vp.model_copy(update={"tmux_layout": "custom"})
        updated = _replace_viewport_in_layout(layout, "vp-a", new_vp)
        assert updated.monitors[0].viewports[0].tmux_layout == "custom"

    def test_leaves_non_matching_viewports(self) -> None:
        vp1 = _make_viewport("vp-a")
        vp1 = vp1.model_copy(update={"geometry_pct": GeometryPct(x=0, y=0, w=50, h=100)})
        vp2 = _make_viewport("vp-b")
        vp2 = vp2.model_copy(update={"geometry_pct": GeometryPct(x=50, y=0, w=50, h=100)})
        monitor = Monitor(viewports=[vp1, vp2])
        layout = ScreenLayout(id="ly-1", name="T", monitors=[monitor])
        new_vp1 = vp1.model_copy(update={"tmux_layout": "custom"})
        updated = _replace_viewport_in_layout(layout, "vp-a", new_vp1)
        # vp-b unchanged
        assert updated.monitors[0].viewports[1].tmux_layout == "tiled"
