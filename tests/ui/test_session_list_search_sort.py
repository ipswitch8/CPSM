# -*- coding: utf-8 -*-
"""Tests for SessionListWidget search field and sort-mode toggle.

Default sort: connections grouped by primary group name (alphabetically),
then by connection name within each group; orphans (no group) appear after
a "Not in any group" separator.

Alphabetical-only mode: flat alphabetical sort, no separator.

Search: case-insensitive substring match against connection display name;
groups are filtered to those containing at least one matching connection.

Groups list is always sorted alphabetically.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt

from cpsm.data.schema import (
    ClaudeLocalConnection,
    CpsmDocument,
    Group,
)
from cpsm.ui.widgets.session_list import SessionListWidget


def _conn(cid: str, name: str) -> ClaudeLocalConnection:
    return ClaudeLocalConnection(
        id=cid,
        name=name,
        launch_profile="claude-local",
        project_folder="/tmp",
        claude_options="",
    )


@pytest.fixture()
def widget(qtbot):  # type: ignore[no-untyped-def]
    w = SessionListWidget()
    qtbot.addWidget(w)
    return w


def _conn_items(widget: SessionListWidget) -> list[tuple[str, str]]:
    """Return (item_id, item_text) tuples in display order; skip categories."""
    out: list[tuple[str, str]] = []
    cat = widget._cat_connections
    for i in range(cat.childCount()):
        item = cat.child(i)
        cid = item.data(0, Qt.ItemDataRole.UserRole) or ""
        out.append((cid, item.text(0)))
    return out


def _group_items(widget: SessionListWidget) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    cat = widget._cat_groups
    for i in range(cat.childCount()):
        item = cat.child(i)
        gid = item.data(0, Qt.ItemDataRole.UserRole) or ""
        out.append((gid, item.text(0)))
    return out


class TestDefaultSort:
    """Default mode: by group, then by name. Orphans after separator."""

    def test_in_group_connections_sort_by_group_then_name(self, widget) -> None:
        # Two groups, intentionally not alpha-ordered in the doc:
        # 'zulu' (z) and 'alpha' (a). Default sort should put alpha-group
        # connections first, then zulu-group connections, each section
        # sorted by connection name.
        c1 = _conn("c1", "Bravo")
        c2 = _conn("c2", "Alpha")
        c3 = _conn("c3", "Delta")
        c4 = _conn("c4", "Charlie")
        zulu = Group(id="g-zulu", name="zulu", members=["c1", "c2"])
        alpha = Group(id="g-alpha", name="alpha", members=["c3", "c4"])
        doc = CpsmDocument(connections=[c1, c2, c3, c4], groups=[zulu, alpha])

        widget.load_document(doc)
        ids = [cid for cid, _ in _conn_items(widget)]

        # alpha-group members first (Charlie, Delta), then zulu-group
        # members (Alpha, Bravo) — each section alphabetical by name.
        assert ids == ["c4", "c3", "c2", "c1"], ids

    def test_orphans_appear_after_separator_alphabetically(self, widget) -> None:
        c_in = _conn("c-in", "Member")
        c_orph_b = _conn("c-b", "Bumblebee")
        c_orph_a = _conn("c-a", "Aardvark")
        grp = Group(id="g1", name="grp", members=["c-in"])
        doc = CpsmDocument(
            connections=[c_in, c_orph_b, c_orph_a], groups=[grp]
        )

        widget.load_document(doc)
        items = _conn_items(widget)
        ids = [cid for cid, _ in items]

        # Member first, then separator, then orphans alphabetically.
        assert ids[0] == "c-in"
        assert items[1][0] == "_separator"
        assert ids[2:] == ["c-a", "c-b"]

    def test_no_separator_when_no_orphans(self, widget) -> None:
        c1 = _conn("c1", "A")
        c2 = _conn("c2", "B")
        grp = Group(id="g1", name="g1", members=["c1", "c2"])
        doc = CpsmDocument(connections=[c1, c2], groups=[grp])

        widget.load_document(doc)
        items = _conn_items(widget)
        # No separator row.
        assert all(cid != "_separator" for cid, _ in items)


class TestAlphaOnlyMode:
    """A→Z toggle: flat alphabetical sort, ignore group membership."""

    def test_alpha_mode_ignores_group_ordering(self, widget) -> None:
        c1 = _conn("c1", "Bravo")
        c2 = _conn("c2", "Alpha")
        c3 = _conn("c3", "Delta")
        c4 = _conn("c4", "Charlie")
        zulu = Group(id="g-zulu", name="zulu", members=["c1", "c2"])
        alpha = Group(id="g-alpha", name="alpha", members=["c3", "c4"])
        doc = CpsmDocument(connections=[c1, c2, c3, c4], groups=[zulu, alpha])

        widget.load_document(doc)
        widget._alpha_only.setChecked(True)
        ids = [cid for cid, _ in _conn_items(widget)]

        # Alpha (c2), Bravo (c1), Charlie (c4), Delta (c3) — pure name order.
        assert ids == ["c2", "c1", "c4", "c3"], ids

    def test_alpha_mode_no_separator(self, widget) -> None:
        c_in = _conn("c-in", "Beta")
        c_orph = _conn("c-orph", "Alpha")
        grp = Group(id="g1", name="g1", members=["c-in"])
        doc = CpsmDocument(connections=[c_in, c_orph], groups=[grp])

        widget.load_document(doc)
        widget._alpha_only.setChecked(True)
        items = _conn_items(widget)

        assert all(cid != "_separator" for cid, _ in items)
        ids = [cid for cid, _ in items]
        assert ids == ["c-orph", "c-in"]


class TestSearchFilter:
    """Search field filters connections by case-insensitive substring."""

    def test_search_narrows_connections(self, widget) -> None:
        c1 = _conn("c1", "SaaS_web")
        c2 = _conn("c2", "SaaS_infra")
        c3 = _conn("c3", "Dotfiles")
        grp = Group(id="g1", name="prod", members=["c1", "c2", "c3"])
        doc = CpsmDocument(connections=[c1, c2, c3], groups=[grp])

        widget.load_document(doc)
        widget._search.setText("saas")
        ids = [cid for cid, _ in _conn_items(widget) if cid != "_separator"]

        assert set(ids) == {"c1", "c2"}
        assert "c3" not in ids

    def test_search_filters_groups_to_those_with_matches(self, widget) -> None:
        c1 = _conn("c1", "SaaS_web")
        c2 = _conn("c2", "Dotfiles")
        c3 = _conn("c3", "Frontend")
        g_saas = Group(id="g-saas", name="SaaS team", members=["c1"])
        g_dot = Group(id="g-dot", name="Dotfiles", members=["c2"])
        g_fe = Group(id="g-fe", name="Frontend", members=["c3"])
        doc = CpsmDocument(connections=[c1, c2, c3], groups=[g_saas, g_dot, g_fe])

        widget.load_document(doc)
        widget._search.setText("saas")
        gids = [gid for gid, _ in _group_items(widget)]

        # Only the group containing the matching connection is shown.
        assert gids == ["g-saas"], gids

    def test_empty_search_shows_everything(self, widget) -> None:
        c1 = _conn("c1", "A")
        c2 = _conn("c2", "B")
        grp = Group(id="g1", name="grp", members=["c1"])
        doc = CpsmDocument(connections=[c1, c2], groups=[grp])

        widget.load_document(doc)
        widget._search.setText("")
        ids = [cid for cid, _ in _conn_items(widget) if cid != "_separator"]
        gids = [gid for gid, _ in _group_items(widget)]

        assert set(ids) == {"c1", "c2"}
        assert gids == ["g1"]

    def test_search_case_insensitive(self, widget) -> None:
        c = _conn("c1", "MyServer")
        doc = CpsmDocument(connections=[c], groups=[])

        widget.load_document(doc)
        widget._search.setText("SERVER")
        ids = [cid for cid, _ in _conn_items(widget) if cid != "_separator"]

        assert ids == ["c1"]


class TestGroupsAlwaysAlphabetical:
    """Groups are always sorted alphabetically regardless of sort mode."""

    def test_groups_alphabetical_default(self, widget) -> None:
        g1 = Group(id="g1", name="zulu", members=[])
        g2 = Group(id="g2", name="alpha", members=[])
        g3 = Group(id="g3", name="mike", members=[])
        # Doc order intentionally NOT alphabetical.
        doc = CpsmDocument(connections=[], groups=[g1, g2, g3])

        widget.load_document(doc)
        gids = [gid for gid, _ in _group_items(widget)]

        assert gids == ["g2", "g3", "g1"]  # alpha < mike < zulu

    def test_groups_alphabetical_in_alpha_mode(self, widget) -> None:
        g1 = Group(id="g1", name="zulu", members=[])
        g2 = Group(id="g2", name="alpha", members=[])
        doc = CpsmDocument(connections=[], groups=[g1, g2])

        widget.load_document(doc)
        widget._alpha_only.setChecked(True)
        gids = [gid for gid, _ in _group_items(widget)]

        assert gids == ["g2", "g1"]


class TestTreeRebuiltSignal:
    """Search/sort changes emit tree_rebuilt so MainWindow can re-apply
    membership highlights and status dots."""

    def test_signal_fires_on_load(self, widget, qtbot) -> None:
        doc = CpsmDocument(connections=[_conn("c1", "A")], groups=[])
        with qtbot.waitSignal(widget.tree_rebuilt, timeout=1000):
            widget.load_document(doc)

    def test_signal_fires_on_search_text_change(self, widget, qtbot) -> None:
        doc = CpsmDocument(connections=[_conn("c1", "A")], groups=[])
        widget.load_document(doc)
        with qtbot.waitSignal(widget.tree_rebuilt, timeout=1000):
            widget._search.setText("a")

    def test_signal_fires_on_sort_toggle(self, widget, qtbot) -> None:
        doc = CpsmDocument(connections=[_conn("c1", "A")], groups=[])
        widget.load_document(doc)
        with qtbot.waitSignal(widget.tree_rebuilt, timeout=1000):
            widget._alpha_only.setChecked(True)


# ---------------------------------------------------------------------------
# Discovered category (D2)
# ---------------------------------------------------------------------------


class TestDiscoveredCategory:
    """Sidebar Discovered category populated via set_discovered_sessions()."""

    def _ds(self, *, pid: int, kind: str = "claude-local",
            cwd: str = "/tmp/p", host: str = "", user: str = "",
            suggested: str = "") -> "DiscoveredSession":
        from cpsm.services.discovery_service import DiscoveredSession
        return DiscoveredSession(
            pid=pid, kind=kind, cmdline=f"{kind} fake",
            cwd=cwd, host=host, user=user, tty="/dev/pts/9",
            suggested_connection_id=suggested,
        )

    def test_category_hidden_when_empty(self, widget) -> None:
        widget.set_discovered_sessions([])
        assert widget._cat_discovered.isHidden()

    def test_category_visible_when_populated(self, widget) -> None:
        widget.set_discovered_sessions([self._ds(pid=1234)])
        assert not widget._cat_discovered.isHidden()
        assert widget._cat_discovered.childCount() == 1

    def test_item_id_encodes_pid(self, widget) -> None:
        widget.set_discovered_sessions([self._ds(pid=4242)])
        item = widget._cat_discovered.child(0)
        assert item.data(0, Qt.ItemDataRole.UserRole) == "discovered:4242"

    def test_get_discovered_session_round_trip(self, widget) -> None:
        s = self._ds(pid=99, kind="ssh-shell", host="x.example", user="u")
        widget.set_discovered_sessions([s])
        assert widget.get_discovered_session(99) is s
        assert widget.get_discovered_session(123) is None

    def test_replacing_list_clears_old_entries(self, widget) -> None:
        widget.set_discovered_sessions([self._ds(pid=1)])
        widget.set_discovered_sessions([self._ds(pid=2), self._ds(pid=3)])
        assert widget._cat_discovered.childCount() == 2
        assert widget.get_discovered_session(1) is None

    def test_label_includes_pid_for_claude_local(self, widget) -> None:
        widget.set_discovered_sessions([
            self._ds(pid=42, kind="claude-local", cwd="/tmp/proj")
        ])
        text = widget._cat_discovered.child(0).text(0)
        assert "PID 42" in text
        assert "/tmp/proj" in text

    def test_label_for_ssh_uses_user_at_host(self, widget) -> None:
        widget.set_discovered_sessions([
            self._ds(pid=42, kind="ssh-shell", host="dev.example.com", user="ubuntu")
        ])
        text = widget._cat_discovered.child(0).text(0)
        assert "ubuntu@dev.example.com" in text

    def test_matched_session_renders_as_subrow_under_connection(self, widget) -> None:
        """D5: matched discovered sessions appear as italic sub-rows under
        their Connection in the Connections category, NOT in the
        Discovered (unmatched) category."""
        c = _conn("dotfiles", "Dotfiles")
        doc = CpsmDocument(connections=[c], groups=[])
        widget.load_document(doc)
        widget.set_discovered_sessions([
            self._ds(pid=99, kind="claude-local", cwd="/home/user/dotfiles",
                     suggested="dotfiles"),
        ])
        # Discovered (unmatched) is hidden — the only session matched.
        assert widget._cat_discovered.isHidden()
        # Connection has exactly one sub-row tagged with the discovered pid.
        cat = widget._cat_connections
        conn_item = cat.child(0)
        assert conn_item.data(0, Qt.ItemDataRole.UserRole) == "dotfiles"
        assert conn_item.childCount() == 1
        sub = conn_item.child(0)
        assert sub.data(0, Qt.ItemDataRole.UserRole) == "discovered:99"
        # Sub-row label includes the PID and is prefixed with the ↳ glyph.
        assert "↳ outside" in sub.text(0)
        assert "PID 99" in sub.text(0)

    def test_matched_subrow_auto_expands_parent_connection(self, widget) -> None:
        c = _conn("dotfiles", "Dotfiles")
        doc = CpsmDocument(connections=[c], groups=[])
        widget.load_document(doc)
        widget.set_discovered_sessions([
            self._ds(pid=99, kind="claude-local", cwd="/x", suggested="dotfiles"),
        ])
        conn_item = widget._cat_connections.child(0)
        assert conn_item.isExpanded()

    def test_multiple_matches_render_as_multiple_subrows(self, widget) -> None:
        c = _conn("dotfiles", "Dotfiles")
        doc = CpsmDocument(connections=[c], groups=[])
        widget.load_document(doc)
        widget.set_discovered_sessions([
            self._ds(pid=99, kind="claude-local", cwd="/x", suggested="dotfiles"),
            self._ds(pid=100, kind="claude-local", cwd="/x", suggested="dotfiles"),
        ])
        conn_item = widget._cat_connections.child(0)
        assert conn_item.childCount() == 2
        sub_pids = [
            int(conn_item.child(i).data(0, Qt.ItemDataRole.UserRole).split(":")[1])
            for i in range(conn_item.childCount())
        ]
        assert sorted(sub_pids) == [99, 100]

    def test_unmatched_session_stays_in_discovered_category(self, widget) -> None:
        c = _conn("dotfiles", "Dotfiles")
        doc = CpsmDocument(connections=[c], groups=[])
        widget.load_document(doc)
        widget.set_discovered_sessions([
            self._ds(pid=88, kind="claude-local", cwd="/tmp/p"),  # suggested=""
        ])
        # Connection has no sub-rows.
        assert widget._cat_connections.child(0).childCount() == 0
        # Unmatched category visible with one row.
        assert not widget._cat_discovered.isHidden()
        assert widget._cat_discovered.childCount() == 1
        assert (
            widget._cat_discovered.child(0).data(0, Qt.ItemDataRole.UserRole)
            == "discovered:88"
        )

    def test_subrow_is_not_draggable(self, widget) -> None:
        """Discovered sub-rows are not Connections — they shouldn't fire
        the connection-drag MIME payload."""
        c = _conn("dotfiles", "Dotfiles")
        doc = CpsmDocument(connections=[c], groups=[])
        widget.load_document(doc)
        widget.set_discovered_sessions([
            self._ds(pid=99, kind="claude-local", cwd="/x", suggested="dotfiles"),
        ])
        sub = widget._cat_connections.child(0).child(0)
        assert not bool(sub.flags() & Qt.ItemFlag.ItemIsDragEnabled)
