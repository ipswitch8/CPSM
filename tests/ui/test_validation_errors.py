# -*- coding: utf-8 -*-
"""
pytest-qt tests for cpsm.ui.dialogs.validation_errors.ValidationErrorsDialog.

Spec: §4.10

All tests run with QT_QPA_PLATFORM=offscreen (set in conftest.py).
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from cpsm.services.config_service import ValidationIssue
from cpsm.ui.dialogs.validation_errors import ValidationErrorsDialog, _top_level_key

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _err(location: str, message: str = "Some error") -> ValidationIssue:
    return ValidationIssue(location=location, message=message, severity="error")


def _warn(location: str, message: str = "Some warning") -> ValidationIssue:
    return ValidationIssue(location=location, message=message, severity="warning")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def empty_dialog(qtbot) -> ValidationErrorsDialog:  # type: ignore[no-untyped-def]
    dlg = ValidationErrorsDialog([])
    qtbot.addWidget(dlg)
    return dlg


@pytest.fixture()
def mixed_dialog(qtbot) -> ValidationErrorsDialog:  # type: ignore[no-untyped-def]
    issues = [
        _err("connections.0.host", "Host is required"),
        _err("connections.1.user", "User is required"),
        _warn("ssh_keys.0.public_path", "Public key file not found"),
        _err("settings.theme", "Unknown theme value"),
    ]
    dlg = ValidationErrorsDialog(issues)
    qtbot.addWidget(dlg)
    return dlg


# ---------------------------------------------------------------------------
# Tests: empty issue list
# ---------------------------------------------------------------------------


class TestEmptyIssues:
    def test_object_name(self, empty_dialog: ValidationErrorsDialog) -> None:
        assert empty_dialog.objectName() == "dlg_validation_errors"

    def test_tree_object_name(self, empty_dialog: ValidationErrorsDialog) -> None:
        assert empty_dialog.tree.objectName() == "tree_issues"

    def test_empty_tree(self, empty_dialog: ValidationErrorsDialog) -> None:
        assert empty_dialog.tree.topLevelItemCount() == 0

    def test_title_no_issues(self, empty_dialog: ValidationErrorsDialog) -> None:
        title = empty_dialog.windowTitle()
        assert "No issues" in title

    def test_issue_count_zero(self, empty_dialog: ValidationErrorsDialog) -> None:
        assert empty_dialog.issue_count == 0


# ---------------------------------------------------------------------------
# Tests: title bar counts
# ---------------------------------------------------------------------------


class TestTitleBarCounts:
    def test_errors_only_in_title(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        dlg = ValidationErrorsDialog([_err("connections.0.host")] * 3)
        qtbot.addWidget(dlg)
        title = dlg.windowTitle()
        assert "3 errors" in title
        assert "warning" not in title

    def test_warnings_only_in_title(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        dlg = ValidationErrorsDialog([_warn("ssh_keys.0.name")] * 2)
        qtbot.addWidget(dlg)
        title = dlg.windowTitle()
        assert "2 warnings" in title
        assert "error" not in title

    def test_mixed_counts_in_title(self, mixed_dialog: ValidationErrorsDialog) -> None:
        title = mixed_dialog.windowTitle()
        assert "3 errors" in title
        assert "1 warning" in title

    def test_singular_error(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        dlg = ValidationErrorsDialog([_err("settings.theme")])
        qtbot.addWidget(dlg)
        title = dlg.windowTitle()
        assert "1 error" in title
        assert "1 errors" not in title

    def test_singular_warning(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        dlg = ValidationErrorsDialog([_warn("ssh_keys.0.name")])
        qtbot.addWidget(dlg)
        title = dlg.windowTitle()
        assert "1 warning" in title
        assert "1 warnings" not in title


# ---------------------------------------------------------------------------
# Tests: grouping by top-level location
# ---------------------------------------------------------------------------


class TestGrouping:
    def _collect_top_level_texts(self, dlg: ValidationErrorsDialog) -> list[str]:
        tree = dlg.tree
        return [tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())]

    def test_groups_by_top_level(self, mixed_dialog: ValidationErrorsDialog) -> None:
        groups = self._collect_top_level_texts(mixed_dialog)
        # settings, ssh_keys, connections all appear (in canonical order)
        assert "settings" in groups
        assert "ssh_keys" in groups
        assert "connections" in groups

    def test_canonical_order(self, mixed_dialog: ValidationErrorsDialog) -> None:
        groups = self._collect_top_level_texts(mixed_dialog)
        # settings before ssh_keys before connections (per _TOP_LEVEL_ORDER)
        assert groups.index("settings") < groups.index("ssh_keys")
        assert groups.index("ssh_keys") < groups.index("connections")

    def test_children_count_under_connections(self, mixed_dialog: ValidationErrorsDialog) -> None:
        tree = mixed_dialog.tree
        groups = self._collect_top_level_texts(mixed_dialog)
        conn_idx = groups.index("connections")
        conn_item = tree.topLevelItem(conn_idx)
        assert conn_item.childCount() == 2

    def test_children_count_under_settings(self, mixed_dialog: ValidationErrorsDialog) -> None:
        tree = mixed_dialog.tree
        groups = self._collect_top_level_texts(mixed_dialog)
        settings_idx = groups.index("settings")
        settings_item = tree.topLevelItem(settings_idx)
        assert settings_item.childCount() == 1

    def test_same_top_level_key_grouped(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        issues = [_err(f"connections.{i}.host") for i in range(5)]
        dlg = ValidationErrorsDialog(issues)
        qtbot.addWidget(dlg)
        tree = dlg.tree
        assert tree.topLevelItemCount() == 1
        assert tree.topLevelItem(0).childCount() == 5

    def test_unknown_top_level_appears_last(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        issues = [
            _err("connections.0.host"),
            _err("custom_section.0.field"),
        ]
        dlg = ValidationErrorsDialog(issues)
        qtbot.addWidget(dlg)
        tree = dlg.tree
        texts = [tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())]
        # connections is in canonical order; custom_section is appended after
        assert texts.index("connections") < texts.index("custom_section")

    def test_location_shown_in_row(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        dlg = ValidationErrorsDialog([_err("connections.0.host", "Required")])
        qtbot.addWidget(dlg)
        tree = dlg.tree
        child = tree.topLevelItem(0).child(0)
        assert "connections.0.host" in child.text(1)

    def test_message_shown_in_row(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        dlg = ValidationErrorsDialog([_err("connections.0.host", "Host is required")])
        qtbot.addWidget(dlg)
        tree = dlg.tree
        child = tree.topLevelItem(0).child(0)
        assert "Host is required" in child.text(2)


# ---------------------------------------------------------------------------
# Tests: "Open in editor" button emits open_requested
# ---------------------------------------------------------------------------


class TestOpenRequested:
    def test_open_requested_signal(self, qtbot, mixed_dialog: ValidationErrorsDialog) -> None:
        tree = mixed_dialog.tree
        # Find the connections group and its first child
        groups_texts = [tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())]
        conn_idx = groups_texts.index("connections")
        conn_item = tree.topLevelItem(conn_idx)
        child = conn_item.child(0)

        # The "Open in editor" button is in column 3 via setItemWidget
        btn_widget = tree.itemWidget(child, 3)
        assert btn_widget is not None

        # Find the QPushButton inside the container
        from PySide6.QtWidgets import QPushButton

        btn = btn_widget.findChild(QPushButton)
        assert btn is not None

        with qtbot.waitSignal(mixed_dialog.open_requested, timeout=500) as blocker:
            btn.click()

        # The emitted path should be the location of this issue
        emitted_path: str = blocker.args[0]
        assert emitted_path.startswith("connections.")

    def test_open_requested_correct_path(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        specific_location = "ssh_keys.0.private_path"
        dlg = ValidationErrorsDialog([_err(specific_location, "File not found")])
        qtbot.addWidget(dlg)

        tree = dlg.tree
        child = tree.topLevelItem(0).child(0)

        from PySide6.QtWidgets import QPushButton

        btn_widget = tree.itemWidget(child, 3)
        btn = btn_widget.findChild(QPushButton)
        assert btn is not None

        received: list[str] = []
        dlg.open_requested.connect(received.append)
        btn.click()

        assert received == [specific_location]

    def test_each_row_has_open_button(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        issues = [_err(f"connections.{i}.host") for i in range(3)]
        dlg = ValidationErrorsDialog(issues)
        qtbot.addWidget(dlg)

        from PySide6.QtWidgets import QPushButton

        tree = dlg.tree
        group_item = tree.topLevelItem(0)
        for i in range(group_item.childCount()):
            child = group_item.child(i)
            btn_widget = tree.itemWidget(child, 3)
            assert btn_widget is not None
            btn = btn_widget.findChild(QPushButton)
            assert btn is not None


# ---------------------------------------------------------------------------
# Tests: severity icons and colors
# ---------------------------------------------------------------------------


class TestSeverityDisplay:
    def test_error_icon_glyph(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        dlg = ValidationErrorsDialog([_err("connections.0.host")])
        qtbot.addWidget(dlg)
        tree = dlg.tree
        child = tree.topLevelItem(0).child(0)
        assert child.text(0) == "✖"

    def test_warning_icon_glyph(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        dlg = ValidationErrorsDialog([_warn("ssh_keys.0.name")])
        qtbot.addWidget(dlg)
        tree = dlg.tree
        child = tree.topLevelItem(0).child(0)
        assert child.text(0) == "⚠"


# ---------------------------------------------------------------------------
# Tests: internal helpers
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_top_level_key_simple(self) -> None:
        assert _top_level_key("connections.0.host") == "connections"

    def test_top_level_key_single(self) -> None:
        assert _top_level_key("settings") == "settings"

    def test_top_level_key_empty(self) -> None:
        assert _top_level_key("") == "(root)"

    def test_top_level_key_deep(self) -> None:
        assert _top_level_key("groups.0.members.2.id") == "groups"
