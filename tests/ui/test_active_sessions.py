# -*- coding: utf-8 -*-
"""
pytest-qt tests for cpsm.ui.widgets.active_sessions.ActiveSessionsWidget.

Spec: §5.8, §5.4

All tests run with QT_QPA_PLATFORM=offscreen (set in conftest.py).
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import QObject, Signal

from cpsm.ui.widgets.active_sessions import (
    _COL_STATUS,
    _STATE_COLORS,
    ActiveSessionsWidget,
    _SessionTableModel,
)
from cpsm.workers.status_poller import PaneState, PaneStatus, StatusPoller

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def _make_status(
    pane_id: str = "%1",
    *,
    state: PaneState = PaneState.CONNECTED,
    session: str = "cpsm",
    last_seen: datetime | None = None,
) -> PaneStatus:
    return PaneStatus(
        pane_id=pane_id,
        session=session,
        state=state,
        last_seen=last_seen or _NOW,
        exit_code=None,
        last_output_tail=None,
    )


def _placeholder_status(pane_id: str = "%99") -> PaneStatus:
    return PaneStatus(
        pane_id=pane_id,
        session="cpsm",
        state=PaneState.EMPTY_SLOT,
        last_seen=_NOW,
        exit_code=None,
        last_output_tail=None,
    )


# ---------------------------------------------------------------------------
# Minimal signal emitter that mimics StatusPoller for testing
# ---------------------------------------------------------------------------


class _FakePoller(QObject):
    """Tiny QObject that exposes the same two signals as StatusPoller."""

    poll_complete: Signal = Signal(list)
    state_changed: Signal = Signal(PaneStatus)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def poller(qtbot) -> _FakePoller:  # type: ignore[no-untyped-def]
    p = _FakePoller()
    qtbot.addWidget  # ensure event loop is active
    return p


@pytest.fixture()
def widget(qtbot, poller: _FakePoller) -> ActiveSessionsWidget:  # type: ignore[no-untyped-def]
    w = ActiveSessionsWidget(poller)
    qtbot.addWidget(w)
    w.show()
    return w


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEmptyState:
    """Widget with no panes should render an empty table."""

    def test_empty_table_on_construction(self, widget: ActiveSessionsWidget) -> None:
        assert widget.model.rowCount() == 0

    def test_table_object_name(self, widget: ActiveSessionsWidget) -> None:
        assert widget.table.objectName() == "table_active_sessions"

    def test_widget_object_name(self, widget: ActiveSessionsWidget) -> None:
        assert widget.objectName() == "widget_active_sessions"

    def test_column_count(self, widget: ActiveSessionsWidget) -> None:
        assert widget.model.columnCount() == 8


class TestPollComplete:
    """poll_complete signal updates the table model."""

    def test_single_pane_added(
        self, qtbot, widget: ActiveSessionsWidget, poller: _FakePoller
    ) -> None:
        status = _make_status("%1", state=PaneState.CONNECTED)
        poller.poll_complete.emit([status])
        assert widget.model.rowCount() == 1

    def test_multiple_panes_added(
        self, qtbot, widget: ActiveSessionsWidget, poller: _FakePoller
    ) -> None:
        statuses = [
            _make_status("%1", state=PaneState.CONNECTED),
            _make_status("%2", state=PaneState.STALE),
            _make_status("%3", state=PaneState.ERROR),
        ]
        poller.poll_complete.emit(statuses)
        assert widget.model.rowCount() == 3

    def test_placeholder_excluded(
        self, qtbot, widget: ActiveSessionsWidget, poller: _FakePoller
    ) -> None:
        statuses = [
            _make_status("%1", state=PaneState.CONNECTED),
            _placeholder_status("%99"),
        ]
        poller.poll_complete.emit(statuses)
        assert widget.model.rowCount() == 1

    def test_all_placeholders_excluded(
        self, qtbot, widget: ActiveSessionsWidget, poller: _FakePoller
    ) -> None:
        statuses = [_placeholder_status(f"%{i}") for i in range(5)]
        poller.poll_complete.emit(statuses)
        assert widget.model.rowCount() == 0

    def test_gone_pane_removed(
        self, qtbot, widget: ActiveSessionsWidget, poller: _FakePoller
    ) -> None:
        # Emit two panes
        poller.poll_complete.emit(
            [
                _make_status("%1"),
                _make_status("%2"),
            ]
        )
        assert widget.model.rowCount() == 2

        # Second poll with only one pane — the other disappears
        poller.poll_complete.emit([_make_status("%1")])
        assert widget.model.rowCount() == 1

    def test_state_updated_on_second_poll(
        self, qtbot, widget: ActiveSessionsWidget, poller: _FakePoller
    ) -> None:
        poller.poll_complete.emit([_make_status("%1", state=PaneState.CONNECTED)])
        assert widget.model.rows[0].state == PaneState.CONNECTED

        poller.poll_complete.emit([_make_status("%1", state=PaneState.STALE)])
        assert widget.model.rows[0].state == PaneState.STALE


class TestStatusIcon:
    """Status column displays the correct color/glyph per PaneState."""

    @pytest.mark.parametrize(
        "state,expected_color",
        [
            (PaneState.CONNECTED, _STATE_COLORS[PaneState.CONNECTED]),
            (PaneState.CONNECTING, _STATE_COLORS[PaneState.CONNECTING]),
            (PaneState.STALE, _STATE_COLORS[PaneState.STALE]),
            (PaneState.ERROR, _STATE_COLORS[PaneState.ERROR]),
            (PaneState.DISCONNECTED_CLEAN, _STATE_COLORS[PaneState.DISCONNECTED_CLEAN]),
            (PaneState.UNKNOWN, _STATE_COLORS[PaneState.UNKNOWN]),
        ],
    )
    def test_status_color(
        self,
        qtbot,
        widget: ActiveSessionsWidget,
        poller: _FakePoller,
        state: PaneState,
        expected_color: str,
    ) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QColor

        poller.poll_complete.emit([_make_status("%1", state=state)])
        idx = widget.model.index(0, _COL_STATUS)
        fg = widget.model.data(idx, Qt.ItemDataRole.ForegroundRole)
        assert isinstance(fg, QColor)
        assert fg.name().lower() == expected_color.lower()

    def test_connected_glyph(
        self, qtbot, widget: ActiveSessionsWidget, poller: _FakePoller
    ) -> None:
        from PySide6.QtCore import Qt

        poller.poll_complete.emit([_make_status("%1", state=PaneState.CONNECTED)])
        idx = widget.model.index(0, _COL_STATUS)
        glyph = widget.model.data(idx, Qt.ItemDataRole.DisplayRole)
        assert glyph == "●"

    def test_disconnected_glyph(
        self, qtbot, widget: ActiveSessionsWidget, poller: _FakePoller
    ) -> None:
        from PySide6.QtCore import Qt

        poller.poll_complete.emit([_make_status("%1", state=PaneState.DISCONNECTED_CLEAN)])
        idx = widget.model.index(0, _COL_STATUS)
        glyph = widget.model.data(idx, Qt.ItemDataRole.DisplayRole)
        assert glyph == "○"


class TestActionSignals:
    """Clicking Attach / Reconnect / Kill emits the right signal with the right id."""

    def _get_bar(self, widget: ActiveSessionsWidget, poller: _FakePoller, pane_id: str):
        poller.poll_complete.emit([_make_status(pane_id, state=PaneState.CONNECTED)])
        bar = widget.action_bar_for(pane_id)
        assert bar is not None, f"No action bar found for {pane_id}"
        return bar

    def test_attach_emits_signal(
        self, qtbot, widget: ActiveSessionsWidget, poller: _FakePoller
    ) -> None:
        bar = self._get_bar(widget, poller, "%1")
        with qtbot.waitSignal(widget.attach_requested, timeout=500) as blocker:
            bar.attach_clicked.emit()
        assert blocker.args == ["%1"]

    def test_reconnect_emits_signal(
        self, qtbot, widget: ActiveSessionsWidget, poller: _FakePoller
    ) -> None:
        bar = self._get_bar(widget, poller, "%2")
        with qtbot.waitSignal(widget.reconnect_requested, timeout=500) as blocker:
            bar.reconnect_clicked.emit()
        assert blocker.args == ["%2"]

    def test_kill_emits_signal(
        self, qtbot, widget: ActiveSessionsWidget, poller: _FakePoller
    ) -> None:
        bar = self._get_bar(widget, poller, "%3")
        with qtbot.waitSignal(widget.kill_requested, timeout=500) as blocker:
            bar.kill_clicked.emit()
        assert blocker.args == ["%3"]

    def test_multiple_panes_correct_id(
        self, qtbot, widget: ActiveSessionsWidget, poller: _FakePoller
    ) -> None:
        """Ensure each action bar emits its own pane_id, not a shared value."""
        poller.poll_complete.emit(
            [
                _make_status("%10"),
                _make_status("%20"),
            ]
        )
        bar10 = widget.action_bar_for("%10")
        bar20 = widget.action_bar_for("%20")
        assert bar10 is not None
        assert bar20 is not None

        received: list[str] = []
        widget.kill_requested.connect(received.append)

        bar10.kill_clicked.emit()
        bar20.kill_clicked.emit()

        assert received == ["%10", "%20"]


class TestStateChangedSignal:
    """state_changed signal performs partial updates."""

    def test_state_changed_adds_new_pane(
        self, qtbot, widget: ActiveSessionsWidget, poller: _FakePoller
    ) -> None:
        status = _make_status("%5", state=PaneState.CONNECTING)
        poller.state_changed.emit(status)
        assert widget.model.rowCount() == 1
        assert widget.model.rows[0].state == PaneState.CONNECTING

    def test_state_changed_updates_existing(
        self, qtbot, widget: ActiveSessionsWidget, poller: _FakePoller
    ) -> None:
        poller.poll_complete.emit([_make_status("%5", state=PaneState.CONNECTED)])
        poller.state_changed.emit(_make_status("%5", state=PaneState.ERROR))
        assert widget.model.rowCount() == 1
        assert widget.model.rows[0].state == PaneState.ERROR

    def test_state_changed_placeholder_removes_row(
        self, qtbot, widget: ActiveSessionsWidget, poller: _FakePoller
    ) -> None:
        poller.poll_complete.emit([_make_status("%5", state=PaneState.CONNECTED)])
        assert widget.model.rowCount() == 1
        poller.state_changed.emit(_placeholder_status("%5"))
        assert widget.model.rowCount() == 0


class TestModelDirectly:
    """Unit tests on _SessionTableModel without the full widget."""

    def test_upsert_new(self) -> None:
        model = _SessionTableModel()
        s = _make_status("%1", state=PaneState.CONNECTED)
        model.upsert_status(s)
        assert model.rowCount() == 1

    def test_upsert_update(self) -> None:
        model = _SessionTableModel()
        model.upsert_status(_make_status("%1", state=PaneState.CONNECTED))
        model.upsert_status(_make_status("%1", state=PaneState.ERROR))
        assert model.rowCount() == 1
        assert model.rows[0].state == PaneState.ERROR

    def test_remove_existing(self) -> None:
        model = _SessionTableModel()
        model.upsert_status(_make_status("%1"))
        model.remove_row("%1")
        assert model.rowCount() == 0

    def test_remove_nonexistent_is_noop(self) -> None:
        model = _SessionTableModel()
        model.remove_row("does-not-exist")  # must not raise
        assert model.rowCount() == 0

    def test_replace_all_filters_nothing(self) -> None:
        model = _SessionTableModel()
        statuses = [_make_status(f"%{i}") for i in range(3)]
        model.replace_all(statuses)
        assert model.rowCount() == 3

    def test_enrich_row(self) -> None:
        model = _SessionTableModel()
        model.upsert_status(_make_status("%1"))
        model.enrich_row("%1", connection_name="My Server", profile_glyph="🔗")
        assert model.rows[0].connection_name == "My Server"
        assert model.rows[0].profile_glyph == "🔗"

    def test_enrich_nonexistent_is_noop(self) -> None:
        model = _SessionTableModel()
        model.enrich_row("no-such-pane", connection_name="X")  # must not raise

    def test_header_data(self) -> None:
        from PySide6.QtCore import Qt

        model = _SessionTableModel()
        assert model.headerData(0, Qt.Orientation.Horizontal) == "Status"
        assert model.headerData(7, Qt.Orientation.Horizontal) == "Actions"

    def test_data_invalid_index(self) -> None:
        model = _SessionTableModel()
        from PySide6.QtCore import QModelIndex

        assert model.data(QModelIndex()) is None

    def test_column_count_with_parent(self) -> None:
        model = _SessionTableModel()
        # With a valid parent index, columnCount must return 0
        model.upsert_status(_make_status("%1"))
        parent_idx = model.index(0, 0)
        assert model.columnCount(parent_idx) == 0

    def test_row_count_with_parent(self) -> None:
        model = _SessionTableModel()
        model.upsert_status(_make_status("%1"))
        parent_idx = model.index(0, 0)
        assert model.rowCount(parent_idx) == 0


class TestMagicMockPoller:
    """Widget must not crash when constructed with a MagicMock spec=StatusPoller."""

    def test_construction_with_mock_poller(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        mock_poller = MagicMock(spec=StatusPoller)
        w = ActiveSessionsWidget(mock_poller)
        qtbot.addWidget(w)
        assert w.model.rowCount() == 0
