# -*- coding: utf-8 -*-
"""Tests for LaunchConflictDialog and the MainWindow conflict-resolution
flow (D3)."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QDialog, QPushButton, QRadioButton

from cpsm.data.schema import (
    ClaudeLocalConnection,
    CpsmDocument,
    Group,
    Settings,
)
from cpsm.services.discovery_service import DiscoveredSession
from cpsm.ui.dialogs.launch_conflict import LaunchConflictDialog
from cpsm.ui.main_window import MainWindow

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ds(
    pid: int, kind: str = "claude-local", cwd: str = "/tmp/p", host: str = "", user: str = ""
) -> DiscoveredSession:
    return DiscoveredSession(
        pid=pid,
        kind=kind,
        cmdline=f"{kind}",
        cwd=cwd,
        host=host,
        user=user,
        tty="/dev/pts/0",
        suggested_connection_id="",
    )


def _select_radio(dlg: LaunchConflictDialog, conn_id: str, action: str) -> None:
    radio = dlg.findChild(QRadioButton, f"radio_conflict_{conn_id}_{action}")
    assert radio is not None, f"radio for {conn_id}/{action} not found"
    radio.setChecked(True)


def _click_continue(dlg: LaunchConflictDialog) -> None:
    btn = dlg.findChild(QPushButton, "btn_conflict_continue")
    assert btn is not None
    btn.click()


# ---------------------------------------------------------------------------
# LaunchConflictDialog itself
# ---------------------------------------------------------------------------


class TestLaunchConflictDialog:
    def test_one_row_per_conflict(self, qtbot) -> None:
        c1 = _ds(100)
        c2 = _ds(200, kind="ssh-shell", host="x.example", user="u")
        dlg = LaunchConflictDialog(
            [
                ("conn-a", "Conn A", c1),
                ("conn-b", "Conn B", c2),
            ]
        )
        qtbot.addWidget(dlg)
        # Three radios per row × 2 rows = 6 (plus radios from QButtonGroups
        # are still QRadioButton instances).
        radios = dlg.findChildren(QRadioButton)
        assert len(radios) == 6

    def test_default_selection_is_adopt(self, qtbot) -> None:
        dlg = LaunchConflictDialog([("c1", "C1", _ds(1))])
        qtbot.addWidget(dlg)
        adopt = dlg.findChild(QRadioButton, "radio_conflict_c1_adopt")
        assert adopt is not None
        assert adopt.isChecked()

    def test_continue_returns_actions_dict(self, qtbot) -> None:
        dlg = LaunchConflictDialog(
            [
                ("a", "A", _ds(1)),
                ("b", "B", _ds(2)),
                ("c", "C", _ds(3)),
            ]
        )
        qtbot.addWidget(dlg)
        _select_radio(dlg, "a", "adopt")
        _select_radio(dlg, "b", "duplicate")
        _select_radio(dlg, "c", "skip")
        _click_continue(dlg)
        assert dlg.result() == QDialog.DialogCode.Accepted
        assert dlg.chosen_actions == {"a": "adopt", "b": "duplicate", "c": "skip"}

    def test_cancel_leaves_actions_empty(self, qtbot) -> None:
        dlg = LaunchConflictDialog([("a", "A", _ds(1))])
        qtbot.addWidget(dlg)
        cancel_btn = dlg.findChild(QPushButton, "btn_conflict_cancel")
        assert cancel_btn is not None
        cancel_btn.click()
        assert dlg.result() == QDialog.DialogCode.Rejected
        assert dlg.chosen_actions == {}


# ---------------------------------------------------------------------------
# MainWindow.launch_group conflict integration
# ---------------------------------------------------------------------------


@pytest.fixture()
def doc() -> CpsmDocument:
    c1 = ClaudeLocalConnection(
        id="alpha",
        name="Alpha",
        launch_profile="claude-local",
        project_folder="~/projects/alpha",
        claude_options="--resume",
    )
    c2 = ClaudeLocalConnection(
        id="beta",
        name="Beta",
        launch_profile="claude-local",
        project_folder="~/projects/beta",
        claude_options="",
    )
    c3 = ClaudeLocalConnection(
        id="gamma",
        name="Gamma",
        launch_profile="claude-local",
        project_folder="~/projects/gamma",
        claude_options="",
    )
    return CpsmDocument(
        settings=Settings(),
        connections=[c1, c2, c3],
        groups=[Group(id="g1", name="G1", members=["alpha", "beta", "gamma"])],
    )


@pytest.fixture()
def services(doc) -> SimpleNamespace:
    discovery = MagicMock()

    # Only "alpha" has a conflict.
    def find_for(_doc, cid):
        if cid == "alpha":
            return _ds(11111, cwd="/home/user/projects/alpha")
        return None

    discovery.find_for_connection.side_effect = find_for
    discovery.find_outside_sessions.return_value = []
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
def win(qtbot, doc, services, monkeypatch) -> MainWindow:
    monkeypatch.setattr(
        "cpsm.controllers.layout_controller.LayoutController.__init__",
        lambda self, **kwargs: None,
        raising=False,
    )
    # Bypass any auth prompts so launches can complete.
    monkeypatch.setattr(MainWindow, "_ensure_auth_for_connection", lambda self, conn: True)
    w = MainWindow(services=services, document=doc)
    qtbot.addWidget(w)
    return w


class TestLaunchGroupWithConflicts:
    def test_no_conflicts_skips_dialog(self, qtbot, win, services, doc, monkeypatch) -> None:
        # Override discovery to find nothing.
        services.discovery.find_for_connection.side_effect = lambda *a, **kw: None
        # Sentinel: dialog must never instantiate.
        called = {"count": 0}
        from cpsm.ui.dialogs import launch_conflict as lc_mod

        original = lc_mod.LaunchConflictDialog

        def _spy(*args, **kwargs):
            called["count"] += 1
            return original(*args, **kwargs)

        monkeypatch.setattr(lc_mod, "LaunchConflictDialog", _spy)

        grp = doc.groups[0]
        win._launch_group(grp)
        assert called["count"] == 0
        services.session.launch_group.assert_called_once()

    def test_skip_action_removes_member_from_launch(
        self, qtbot, win, services, doc, monkeypatch
    ) -> None:
        # Stub the conflict dialog to return action=skip for alpha.
        from cpsm.ui.dialogs import launch_conflict as lc_mod

        class _StubDlg:
            def __init__(self, conflicts, parent=None):
                self.chosen_actions = {cid: "skip" for cid, _, _ in conflicts}
                self.DialogCode = QDialog.DialogCode

            def exec(self):
                return QDialog.DialogCode.Accepted

        monkeypatch.setattr(lc_mod, "LaunchConflictDialog", _StubDlg)

        grp = doc.groups[0]
        win._launch_group(grp)

        services.session.launch_group.assert_called_once()
        launch_doc, gid = services.session.launch_group.call_args.args
        assert gid == "g1"
        # alpha skipped, remaining members are beta + gamma.
        skipped_grp = next(g for g in launch_doc.groups if g.id == "g1")
        assert skipped_grp.members == ["beta", "gamma"]

    def test_cancel_aborts_launch(self, qtbot, win, services, doc, monkeypatch) -> None:
        from cpsm.ui.dialogs import launch_conflict as lc_mod

        class _CancelDlg:
            def __init__(self, *a, **kw):
                self.chosen_actions = {}
                self.DialogCode = QDialog.DialogCode

            def exec(self):
                return QDialog.DialogCode.Rejected

        monkeypatch.setattr(lc_mod, "LaunchConflictDialog", _CancelDlg)

        grp = doc.groups[0]
        win._launch_group(grp)
        services.session.launch_group.assert_not_called()

    def test_adopt_action_appends_continue_to_doc(
        self, qtbot, win, services, doc, monkeypatch
    ) -> None:
        # Stub conflict dialog -> adopt
        from cpsm.ui.dialogs import launch_conflict as lc_mod

        class _AdoptDlg:
            def __init__(self, conflicts, parent=None):
                self.chosen_actions = {cid: "adopt" for cid, _, _ in conflicts}
                self.DialogCode = QDialog.DialogCode

            def exec(self):
                return QDialog.DialogCode.Accepted

        monkeypatch.setattr(lc_mod, "LaunchConflictDialog", _AdoptDlg)
        # Stub adopt-pid dialog to instantly succeed without UI.
        monkeypatch.setattr(
            MainWindow,
            "_adopt_pid_via_dialog",
            lambda self, session, label: True,
        )

        grp = doc.groups[0]
        win._launch_group(grp)

        services.session.launch_group.assert_called_once()
        launch_doc, _ = services.session.launch_group.call_args.args
        adopted = next(c for c in launch_doc.connections if c.id == "alpha")
        # alpha had --resume; adoption appends --continue.
        assert adopted.claude_options == "--resume --continue"
        # beta/gamma untouched (no conflict for them in this fixture).
        beta = next(c for c in launch_doc.connections if c.id == "beta")
        assert beta.claude_options == ""

    def test_adopt_cancel_aborts_group_launch(self, qtbot, win, services, doc, monkeypatch) -> None:
        """If the user cancels the inner AdoptSessionDialog, the whole
        group launch is aborted (we don't half-launch the group)."""
        from cpsm.ui.dialogs import launch_conflict as lc_mod

        class _AdoptDlg:
            def __init__(self, conflicts, parent=None):
                self.chosen_actions = {cid: "adopt" for cid, _, _ in conflicts}
                self.DialogCode = QDialog.DialogCode

            def exec(self):
                return QDialog.DialogCode.Accepted

        monkeypatch.setattr(lc_mod, "LaunchConflictDialog", _AdoptDlg)
        monkeypatch.setattr(
            MainWindow,
            "_adopt_pid_via_dialog",
            lambda self, session, label: False,  # user cancelled
        )

        grp = doc.groups[0]
        win._launch_group(grp)
        services.session.launch_group.assert_not_called()

    def test_adopt_with_disappeared_pid_still_appends_continue(
        self, qtbot, win, services, doc, monkeypatch
    ) -> None:
        """Race: outside pid exits between the conflict dialog (where it
        was visible) and the per-member adopt step. We must NOT crash and
        must still mutate the doc with --continue so Claude resumes."""
        from cpsm.ui.dialogs import launch_conflict as lc_mod

        class _AdoptDlg:
            def __init__(self, conflicts, parent=None):
                self.chosen_actions = {cid: "adopt" for cid, _, _ in conflicts}
                self.DialogCode = QDialog.DialogCode

            def exec(self):
                return QDialog.DialogCode.Accepted

        monkeypatch.setattr(lc_mod, "LaunchConflictDialog", _AdoptDlg)

        # First find_for_connection (during _resolve_launch_conflicts)
        # returns the discovered session; the second call (during the
        # per-member adopt loop) returns None to simulate the pid having
        # exited in between.
        call_state = {"count": 0}

        def _flaky_find(_doc, cid):
            call_state["count"] += 1
            if cid != "alpha":
                return None
            # First call: present. Subsequent: gone.
            return _ds(11111, cwd="/home/user/projects/alpha") if call_state["count"] == 1 else None

        services.discovery.find_for_connection.side_effect = _flaky_find

        # If _adopt_pid_via_dialog gets called, fail the test — the
        # disappeared pid means we should bypass the dialog entirely.
        sentinel = {"called": False}
        monkeypatch.setattr(
            MainWindow,
            "_adopt_pid_via_dialog",
            lambda self, *a, **kw: sentinel.update(called=True) or True,
        )

        grp = doc.groups[0]
        win._launch_group(grp)

        assert sentinel["called"] is False, (
            "AdoptSessionDialog should NOT run when the pid is already gone"
        )
        services.session.launch_group.assert_called_once()
        launch_doc, _ = services.session.launch_group.call_args.args
        adopted = next(c for c in launch_doc.connections if c.id == "alpha")
        assert adopted.claude_options == "--resume --continue"
