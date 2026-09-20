# -*- coding: utf-8 -*-
"""Tests for the MainWindow ↔ DiscoveryService wiring (D2).

Verifies:
  - load_document populates the sidebar Discovered category from
    services.discovery.find_outside_sessions(doc).
  - The Ignore action filters subsequent refreshes.
  - Adoption launches with --continue appended to claude_options for
    Claude profiles, untouched for ssh-shell.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from cpsm.data.schema import (
    ClaudeLocalConnection,
    CpsmDocument,
    Group,
    Settings,
    SshKey,
    SshShellConnection,
)
from cpsm.services.discovery_service import DiscoveredSession
from cpsm.ui.main_window import MainWindow

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def doc() -> CpsmDocument:
    key = SshKey(
        id="key-1",
        name="k",
        type="ed25519",
        private_path="~/.ssh/id_ed25519",
        public_path="~/.ssh/id_ed25519.pub",
    )
    claude = ClaudeLocalConnection(
        id="dotfiles",
        name="Dotfiles",
        launch_profile="claude-local",
        project_folder="~/projects/dotfiles",
        claude_options="--resume",
    )
    ssh = SshShellConnection(
        id="prod-web",
        name="Prod Web",
        launch_profile="ssh-shell",
        host="prod.example.com",
        user="ubuntu",
        identity_file_ref="key-1",
    )
    return CpsmDocument(
        settings=Settings(),
        ssh_keys=[key],
        connections=[claude, ssh],
        groups=[Group(id="g1", name="g1", members=["dotfiles", "prod-web"])],
    )


@pytest.fixture()
def discovered_local() -> DiscoveredSession:
    return DiscoveredSession(
        pid=1234,
        kind="claude-local",
        cmdline="claude --resume",
        cwd="/home/user/projects/dotfiles",
        host="",
        user="",
        tty="/dev/pts/0",
        suggested_connection_id="dotfiles",
    )


@pytest.fixture()
def discovered_ssh() -> DiscoveredSession:
    return DiscoveredSession(
        pid=5678,
        kind="ssh-shell",
        cmdline="ssh ubuntu@prod.example.com",
        cwd="",
        host="prod.example.com",
        user="ubuntu",
        tty="/dev/pts/1",
        suggested_connection_id="prod-web",
    )


@pytest.fixture()
def services_with_discovery(doc, discovered_local, discovered_ssh) -> SimpleNamespace:
    discovery = MagicMock()
    discovery.find_outside_sessions.return_value = [
        discovered_local,
        discovered_ssh,
    ]
    svc = SimpleNamespace(
        config=MagicMock(),
        session=MagicMock(),
        layout=MagicMock(),
        templates=MagicMock(),
        repository=MagicMock(),
        key_service=MagicMock(),
        config_path=Path("/tmp/x.yaml"),
        status_poller=MagicMock(),
        monitor_service=None,
        discovery=discovery,
    )
    svc.config.validate.return_value = []
    svc.config.load.return_value = doc
    return svc


@pytest.fixture()
def win(qtbot, doc, services_with_discovery, monkeypatch) -> MainWindow:
    monkeypatch.setattr(
        "cpsm.controllers.layout_controller.LayoutController.__init__",
        lambda self, **kwargs: None,
        raising=False,
    )
    w = MainWindow(services=services_with_discovery, document=doc)
    qtbot.addWidget(w)
    w.show()
    return w


# ---------------------------------------------------------------------------
# Discovery wiring
# ---------------------------------------------------------------------------


class TestDiscoveryWiring:
    def test_discovered_sessions_populated_on_load(self, win, services_with_discovery) -> None:
        # find_outside_sessions was invoked during load_document.
        services_with_discovery.discovery.find_outside_sessions.assert_called()
        # Both fixture sessions are matched, so they nest under their
        # Connections (D5) and the unmatched category stays hidden.
        assert win._session_list._cat_discovered.isHidden()
        # Each Connection has exactly one sub-row.
        cat = win._session_list._cat_connections
        for i in range(cat.childCount()):
            item = cat.child(i)
            cid = item.data(0, 0x0100)
            if cid in ("dotfiles", "prod-web"):
                assert item.childCount() == 1, (cid, item.childCount())
                sub = item.child(0)
                assert sub.data(0, 0x0100).startswith("discovered:")

    def test_discovered_pid_round_trip_via_widget(self, win, discovered_local) -> None:
        s = win._session_list.get_discovered_session(discovered_local.pid)
        assert s is discovered_local

    def test_ignore_pid_filters_next_refresh(
        self, win, services_with_discovery, discovered_local, discovered_ssh
    ) -> None:
        # Ignore the claude-local pid; the dotfiles connection should now
        # have no sub-row, while prod-web still has its ssh-shell sub-row.
        win._ignore_discovered_pid(discovered_local.pid)
        cat = win._session_list._cat_connections
        for i in range(cat.childCount()):
            item = cat.child(i)
            cid = item.data(0, 0x0100)
            if cid == "dotfiles":
                assert item.childCount() == 0
            elif cid == "prod-web":
                assert item.childCount() == 1
                sub = item.child(0)
                assert sub.data(0, 0x0100) == f"discovered:{discovered_ssh.pid}"

    def test_clicking_subrow_shows_parent_connection_inspector(self, win, discovered_local) -> None:
        """D5: a sub-row visually belongs to its Connection, so selecting
        it should show that Connection's details (not clear the Inspector)."""
        # Find the sub-row item under the dotfiles Connection.
        cat = win._session_list._cat_connections
        sub = None
        for i in range(cat.childCount()):
            conn_item = cat.child(i)
            if conn_item.data(0, 0x0100) == "dotfiles":
                assert conn_item.childCount() == 1
                sub = conn_item.child(0)
                break
        assert sub is not None

        # Spy on _show_inspector_for / _clear_inspector and trigger the
        # selection handler directly.
        shown: list[Any] = []
        cleared = {"count": 0}
        win._show_inspector_for = lambda conn: shown.append(conn)
        win._clear_inspector = lambda: cleared.update(count=cleared["count"] + 1)

        win._on_sidebar_item_selected(sub, None)

        assert cleared["count"] == 0, "Inspector was cleared instead of populated"
        assert len(shown) == 1
        assert shown[0].id == "dotfiles"

    def test_double_click_matched_subrow_triggers_adopt(
        self, win, discovered_local, monkeypatch
    ) -> None:
        """Double-clicking a matched sub-row opens the adopt flow for that
        connection instead of being a silent no-op."""
        cat = win._session_list._cat_connections
        sub = None
        for i in range(cat.childCount()):
            conn_item = cat.child(i)
            if conn_item.data(0, 0x0100) == "dotfiles":
                sub = conn_item.child(0)
                break
        assert sub is not None

        called = {"args": None}
        monkeypatch.setattr(
            type(win),
            "_adopt_discovered_session",
            lambda self, s, cid: called.update(args=(s, cid)),
        )

        win._on_sidebar_double_clicked(sub, 0)

        assert called["args"] is not None
        passed_session, passed_cid = called["args"]
        assert passed_session is discovered_local
        assert passed_cid == "dotfiles"

    def test_double_click_unmatched_row_opens_new_connection_editor(
        self, qtbot, doc, monkeypatch
    ) -> None:
        """Unmatched discovered rows have no parent Connection — double-
        clicking should offer to create a new Connection from the process."""
        from cpsm.services.discovery_service import DiscoveredSession

        unmatched = DiscoveredSession(
            pid=4242,
            kind="claude-local",
            cmdline="claude",
            cwd="/home/user/orphan",
            host="",
            user="",
            tty="/dev/pts/9",
            suggested_connection_id="",
        )
        discovery = MagicMock()
        discovery.find_outside_sessions.return_value = [unmatched]
        services = SimpleNamespace(
            config=MagicMock(),
            session=MagicMock(),
            layout=MagicMock(),
            templates=MagicMock(),
            repository=MagicMock(),
            key_service=MagicMock(),
            config_path=Path("/tmp/x.yaml"),
            status_poller=MagicMock(),
            monitor_service=None,
            discovery=discovery,
        )
        services.config.validate.return_value = []
        services.config.load.return_value = doc
        monkeypatch.setattr(
            "cpsm.controllers.layout_controller.LayoutController.__init__",
            lambda self, **kwargs: None,
            raising=False,
        )
        win = MainWindow(services=services, document=doc)
        qtbot.addWidget(win)

        # The unmatched row is in the Discovered (unmatched) category.
        cat = win._session_list._cat_discovered
        assert cat.childCount() == 1
        row = cat.child(0)
        assert row.data(0, 0x0100) == "discovered:4242"

        called = {"session": None}
        monkeypatch.setattr(
            type(win),
            "_open_connection_editor_from_discovered",
            lambda self, s: called.update(session=s),
        )

        win._on_sidebar_double_clicked(row, 0)
        assert called["session"] is unmatched

    def test_correlation_result_remaps_subrow_to_correct_connection(
        self, qtbot, doc, monkeypatch
    ) -> None:
        """When CorrelationWorker reports a refined PID → conn-id mapping,
        the sidebar re-renders the affected DiscoveredSession under the new
        parent Connection. This is the auto-disambiguation user-facing
        flow for ambiguous SSH matches (D6)."""
        from cpsm.services.correlation_service import CorrelationResult
        from cpsm.services.discovery_service import DiscoveredSession

        # Initial local match: pid 5678 wrongly suggested for prod-web; we
        # pretend there's a sibling we'd rather it be tied to (dotfiles is
        # used as a stand-in here just to verify the remap actually moves
        # the sub-row to a different parent).
        initial = DiscoveredSession(
            pid=5678,
            kind="ssh-shell",
            cmdline="ssh ubuntu@prod.example.com",
            cwd="",
            host="prod.example.com",
            user="ubuntu",
            tty="/dev/pts/1",
            suggested_connection_id="prod-web",
        )
        discovery = MagicMock()
        discovery.find_outside_sessions.return_value = [initial]
        services = SimpleNamespace(
            config=MagicMock(),
            session=MagicMock(),
            layout=MagicMock(),
            templates=MagicMock(),
            repository=MagicMock(),
            key_service=MagicMock(),
            config_path=Path("/tmp/x.yaml"),
            status_poller=MagicMock(),
            monitor_service=None,
            discovery=discovery,
            correlation=MagicMock(),
        )
        services.config.validate.return_value = []
        services.config.load.return_value = doc
        monkeypatch.setattr(
            "cpsm.controllers.layout_controller.LayoutController.__init__",
            lambda self, **kwargs: None,
            raising=False,
        )
        # Don't actually start a thread — we'll feed the result directly.
        monkeypatch.setattr(
            MainWindow,
            "_start_correlation_worker",
            lambda self, *a, **kw: None,
        )

        win = MainWindow(services=services, document=doc)
        qtbot.addWidget(win)

        # Pre-correlation: the initial local match places it under prod-web.
        cat = win._session_list._cat_connections
        prod_item = next(
            cat.child(i)
            for i in range(cat.childCount())
            if cat.child(i).data(0, 0x0100) == "prod-web"
        )
        assert prod_item.childCount() == 1

        # Simulate the worker callback with a remap to dotfiles.
        win._on_correlation_done(CorrelationResult(by_pid={5678: "dotfiles"}))

        # Sub-row moved: dotfiles now has it, prod-web is empty.
        cat = win._session_list._cat_connections
        prod_item = next(
            cat.child(i)
            for i in range(cat.childCount())
            if cat.child(i).data(0, 0x0100) == "prod-web"
        )
        dot_item = next(
            cat.child(i)
            for i in range(cat.childCount())
            if cat.child(i).data(0, 0x0100) == "dotfiles"
        )
        assert prod_item.childCount() == 0
        assert dot_item.childCount() == 1
        assert dot_item.child(0).data(0, 0x0100) == "discovered:5678"

    def test_correlation_with_empty_mapping_is_a_noop(self, qtbot, doc, monkeypatch) -> None:
        """An empty CorrelationResult must not clobber the local matches —
        it just means the probe found nothing useful."""
        from cpsm.services.correlation_service import CorrelationResult
        from cpsm.services.discovery_service import DiscoveredSession

        initial = DiscoveredSession(
            pid=1234,
            kind="claude-local",
            cmdline="claude",
            cwd="/home/user/projects/dotfiles",
            host="",
            user="",
            tty="/dev/pts/0",
            suggested_connection_id="dotfiles",
        )
        discovery = MagicMock()
        discovery.find_outside_sessions.return_value = [initial]
        services = SimpleNamespace(
            config=MagicMock(),
            session=MagicMock(),
            layout=MagicMock(),
            templates=MagicMock(),
            repository=MagicMock(),
            key_service=MagicMock(),
            config_path=Path("/tmp/x.yaml"),
            status_poller=MagicMock(),
            monitor_service=None,
            discovery=discovery,
            correlation=MagicMock(),
        )
        services.config.validate.return_value = []
        services.config.load.return_value = doc
        monkeypatch.setattr(
            "cpsm.controllers.layout_controller.LayoutController.__init__",
            lambda self, **kwargs: None,
            raising=False,
        )
        monkeypatch.setattr(
            MainWindow,
            "_start_correlation_worker",
            lambda self, *a, **kw: None,
        )

        win = MainWindow(services=services, document=doc)
        qtbot.addWidget(win)

        win._on_correlation_done(CorrelationResult(by_pid={}))

        # Original match preserved.
        cat = win._session_list._cat_connections
        dot_item = next(
            cat.child(i)
            for i in range(cat.childCount())
            if cat.child(i).data(0, 0x0100) == "dotfiles"
        )
        assert dot_item.childCount() == 1

    def test_discovery_service_exception_does_not_crash(self, qtbot, doc, monkeypatch) -> None:
        """A misbehaving DiscoveryService must not break load_document."""
        bad_discovery = MagicMock()
        bad_discovery.find_outside_sessions.side_effect = RuntimeError("boom")
        services = SimpleNamespace(
            config=MagicMock(),
            session=MagicMock(),
            layout=MagicMock(),
            templates=MagicMock(),
            repository=MagicMock(),
            key_service=MagicMock(),
            config_path=Path("/tmp/x.yaml"),
            status_poller=MagicMock(),
            monitor_service=None,
            discovery=bad_discovery,
        )
        services.config.validate.return_value = []
        services.config.load.return_value = doc
        monkeypatch.setattr(
            "cpsm.controllers.layout_controller.LayoutController.__init__",
            lambda self, **kwargs: None,
            raising=False,
        )
        # Should construct without raising.
        win = MainWindow(services=services, document=doc)
        qtbot.addWidget(win)
        # Discovered category is empty after the exception.
        assert win._session_list._cat_discovered.childCount() == 0


# ---------------------------------------------------------------------------
# Adoption launch
# ---------------------------------------------------------------------------


class TestConnectionSideMarker:
    """Connections in the Connections section get a 👻 suffix when they
    have at least one matching outside session in Discovered."""

    def _conn_text(self, win, conn_id: str) -> str:
        cat = win._session_list._cat_connections
        for i in range(cat.childCount()):
            item = cat.child(i)
            if item.data(0, 0x0100) == conn_id:  # Qt.ItemDataRole.UserRole
                return item.text(0)
        return ""

    def test_no_ghost_marker_on_connection_row(self, win) -> None:
        """D5+ retired the 👻 marker — the nested sub-rows now communicate
        that a Connection has outside instances. Verify the marker is
        absent regardless of match state."""
        text = self._conn_text(win, "dotfiles")
        assert "👻" not in text, text

    def test_no_marker_when_no_matches(self, win, services_with_discovery) -> None:
        services_with_discovery.discovery.find_outside_sessions.return_value = []
        win._refresh_discovered_sessions()
        text = self._conn_text(win, "dotfiles")
        assert "👻" not in text, text


class TestLaunchWithContinue:
    def test_claude_profile_gets_continue_appended(self, win, services_with_discovery, doc) -> None:
        claude = next(c for c in doc.connections if c.id == "dotfiles")
        win._launch_connection_with_continue(claude)

        services_with_discovery.session.launch.assert_called_once()
        call = services_with_discovery.session.launch.call_args
        launch_doc, conn_id = call.args
        assert conn_id == "dotfiles"
        # The doc passed to launch has the connection's claude_options
        # mutated to include --continue.
        adopted = next(c for c in launch_doc.connections if c.id == "dotfiles")
        assert adopted.claude_options == "--resume --continue"

    def test_continue_not_double_appended(self, win, services_with_discovery, doc) -> None:
        # Mutate the doc so the connection already has --continue.
        claude = doc.connections[0].model_copy(update={"claude_options": "--continue"})
        win._launch_connection_with_continue(claude)
        call = services_with_discovery.session.launch.call_args
        launch_doc, _ = call.args
        adopted = next(c for c in launch_doc.connections if c.id == "dotfiles")
        assert adopted.claude_options == "--continue"

    def test_adopt_with_disappeared_pid_falls_through_to_launch(
        self, win, services_with_discovery, doc
    ) -> None:
        """Race: between conflict resolution and execution, the outside
        pid exits. The adopt path must still launch with --continue so the
        saved conversation is resumed."""
        # Pretend the pid is gone now.
        services_with_discovery.discovery.find_for_connection.return_value = None
        win._adopt_discovered_session(None, "dotfiles")
        # Should have launched without crashing.
        services_with_discovery.session.launch.assert_called_once()
        call = services_with_discovery.session.launch.call_args
        launch_doc, conn_id = call.args
        assert conn_id == "dotfiles"
        adopted = next(c for c in launch_doc.connections if c.id == "dotfiles")
        assert adopted.claude_options == "--resume --continue"

    def test_ssh_shell_launches_unchanged(self, win, services_with_discovery, doc) -> None:
        ssh = next(c for c in doc.connections if c.id == "prod-web")
        win._launch_connection_with_continue(ssh)
        # ssh-shell has no claude_options field at all; we just call launch
        # with the unmodified document.
        call = services_with_discovery.session.launch.call_args
        launch_doc, conn_id = call.args
        assert conn_id == "prod-web"
        # The launch doc is the original (we don't model_copy for non-Claude).
        assert launch_doc is doc
