# -*- coding: utf-8 -*-
"""
pytest-qt tests for cpsm.ui.dialogs.reimport_merge.ReimportMergeDialog.

Spec section: §4.4

All tests run with QT_QPA_PLATFORM=offscreen (set in tests/ui/conftest.py).
"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from cpsm.data.importer import ImportPreview, ImportTransform
from cpsm.data.schema import (
    ClaudeRemoteConnection,
    CpsmDocument,
    SshKey,
)
from cpsm.services.import_service import ImportService
from cpsm.ui.dialogs.reimport_merge import ReimportMergeDialog

_FIXTURE = Path(__file__).parent.parent / "data" / "fixtures" / "example-.claude-projects.yaml"

# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


def _make_key() -> SshKey:
    return SshKey(
        id="imported-default",
        name="Imported default key",
        type="ed25519",
        private_path="~/.ssh/id_ed25519",
        public_path="~/.ssh/id_ed25519.pub",
    )


def _make_remote_conn(
    conn_id: str, host: str = "dev.example.com", name: str | None = None
) -> ClaudeRemoteConnection:
    return ClaudeRemoteConnection(
        id=conn_id,
        name=name or conn_id,
        launch_profile="claude-remote",
        host=host,
        user="ubuntu",
        identity_file_ref="imported-default",
        project_folder=f"/opt/{conn_id}",
        claude_options="--resume",
    )


def _make_preview(
    connections: list[ClaudeRemoteConnection], transforms: list[ImportTransform] | None = None
) -> ImportPreview:
    key = _make_key()
    doc = CpsmDocument(ssh_keys=[key], connections=connections)
    if transforms is None:
        transforms = [
            ImportTransform(
                kind="added",
                target_path=f"connections[{c.id}]",
                detail=f"from projects[].name='{c.id}'",
            )
            for c in connections
        ]
    return ImportPreview(
        source_path=Path("/tmp/fake.yaml"),
        transforms=transforms,
        document=doc,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def existing_doc() -> CpsmDocument:
    """Existing document with two connections."""
    key = _make_key()
    return CpsmDocument(
        ssh_keys=[key],
        connections=[
            _make_remote_conn("web01"),
            _make_remote_conn("web02"),
        ],
    )


@pytest.fixture()
def source_preview_matching(existing_doc) -> ImportPreview:
    """Source preview with same connections as existing (verbatim match)."""
    return _make_preview(list(existing_doc.connections))


@pytest.fixture()
def source_preview_with_diff() -> ImportPreview:
    """Source preview with web01 changed host and web03 new."""
    key = _make_key()
    conn_changed = _make_remote_conn("web01", host="changed.example.com")
    conn_new = _make_remote_conn("web03")
    doc = CpsmDocument(ssh_keys=[key], connections=[conn_changed, conn_new])
    transforms = [
        ImportTransform(kind="added", target_path="connections[web01]", detail="web01 changed"),
        ImportTransform(kind="added", target_path="connections[web03]", detail="web03 new"),
    ]
    return ImportPreview(source_path=Path("/tmp/fake.yaml"), transforms=transforms, document=doc)


@pytest.fixture()
def dlg(qtbot, existing_doc, source_preview_matching):
    """Dialog with matching existing and source (no conflicts)."""
    dialog = ReimportMergeDialog(existing_doc, source_preview_matching)
    qtbot.addWidget(dialog)
    dialog.show()
    return dialog


@pytest.fixture()
def conflict_dlg(qtbot, existing_doc, source_preview_with_diff):
    """Dialog with one conflicting connection and one new."""
    dialog = ReimportMergeDialog(existing_doc, source_preview_with_diff)
    qtbot.addWidget(dialog)
    dialog.show()
    return dialog


# ---------------------------------------------------------------------------
# Construction tests
# ---------------------------------------------------------------------------


def test_dialog_constructs_with_empty_existing(qtbot):
    """Dialog should construct cleanly with an empty existing document."""
    empty_existing = CpsmDocument()
    conn = _make_remote_conn("fresh01")
    preview = _make_preview([conn])
    dialog = ReimportMergeDialog(empty_existing, preview)
    qtbot.addWidget(dialog)
    assert dialog is not None


def test_dialog_constructs_cleanly(qtbot, existing_doc, source_preview_matching):
    dialog = ReimportMergeDialog(existing_doc, source_preview_matching)
    qtbot.addWidget(dialog)
    assert dialog is not None


def test_dialog_object_name(dlg):
    assert dlg.objectName() == "dlg_reimport_merge"


def test_dialog_title(dlg):
    assert dlg.windowTitle() == "Re-Import Merge"


# ---------------------------------------------------------------------------
# Stable objectNames
# ---------------------------------------------------------------------------


def test_table_merge_present(dlg):
    from PySide6.QtWidgets import QTableView

    tbl = dlg.findChild(QTableView, "table_merge")
    assert tbl is not None


def test_btn_ok_present(dlg):
    from PySide6.QtWidgets import QPushButton

    btn = dlg.findChild(QPushButton, "btn_ok")
    assert btn is not None


def test_btn_cancel_present(dlg):
    from PySide6.QtWidgets import QPushButton

    btn = dlg.findChild(QPushButton, "btn_cancel")
    assert btn is not None


# ---------------------------------------------------------------------------
# Default decisions
# ---------------------------------------------------------------------------


def test_matching_rows_default_skip(dlg, existing_doc, source_preview_matching):
    """Connections identical in both existing and source should default to 'skip'."""
    decisions = dlg.merge_decisions
    for conn in existing_doc.connections:
        assert decisions.get(conn.id) == "skip", (
            f"Expected 'skip' for matching connection '{conn.id}', got '{decisions.get(conn.id)}'"
        )


def test_conflict_row_defaults_to_update(conflict_dlg):
    """Changed connection (web01) should default to 'update'."""
    decisions = conflict_dlg.merge_decisions
    assert decisions.get("web01") == "update"


def test_new_row_defaults_to_add(conflict_dlg):
    """Connection only in source (web03) should default to 'add'."""
    decisions = conflict_dlg.merge_decisions
    assert decisions.get("web03") == "add"


def test_existing_only_row_defaults_to_skip(conflict_dlg):
    """Connection only in existing (web02) should default to 'skip'."""
    decisions = conflict_dlg.merge_decisions
    assert decisions.get("web02") == "skip"


# ---------------------------------------------------------------------------
# Conflict highlighting
# ---------------------------------------------------------------------------


def test_conflict_row_has_background_colour(conflict_dlg):
    """Conflict rows should have a yellow background colour."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QTableView

    tbl = conflict_dlg.findChild(QTableView, "table_merge")
    assert tbl is not None
    model = tbl.model()

    # Find web01 row (the conflicting one)
    conflict_row = None
    for row in range(model.rowCount()):
        idx_existing = model.index(row, 0)
        existing_text = model.data(idx_existing, Qt.ItemDataRole.DisplayRole) or ""
        if "web01" in existing_text:
            conflict_row = row
            break

    assert conflict_row is not None, "Expected to find web01 conflict row"
    # Background should be set for conflict rows
    idx = model.index(conflict_row, 0)
    bg = model.data(idx, Qt.ItemDataRole.BackgroundRole)
    assert bg is not None


def test_conflict_row_has_tooltip(conflict_dlg):
    """Conflict rows should carry a tooltip listing differing fields."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QTableView

    tbl = conflict_dlg.findChild(QTableView, "table_merge")
    assert tbl is not None
    model = tbl.model()

    conflict_row = None
    for row in range(model.rowCount()):
        idx_existing = model.index(row, 0)
        existing_text = model.data(idx_existing, Qt.ItemDataRole.DisplayRole) or ""
        if "web01" in existing_text:
            conflict_row = row
            break

    assert conflict_row is not None
    idx = model.index(conflict_row, 0)
    tooltip = model.data(idx, Qt.ItemDataRole.ToolTipRole)
    assert tooltip is not None
    assert "host" in tooltip or "Conflict" in tooltip


# ---------------------------------------------------------------------------
# merge_decisions reflects all rows
# ---------------------------------------------------------------------------


def test_merge_decisions_covers_all_ids(conflict_dlg, existing_doc, source_preview_with_diff):
    """merge_decisions should contain entries for every id in union of existing and source."""
    decisions = conflict_dlg.merge_decisions
    source_ids = {c.id for c in source_preview_with_diff.document.connections}
    existing_ids = {c.id for c in existing_doc.connections}
    expected_ids = source_ids | existing_ids
    assert set(decisions.keys()) == expected_ids


# ---------------------------------------------------------------------------
# OK / Cancel
# ---------------------------------------------------------------------------


def test_ok_button_accepts(qtbot, dlg):
    from PySide6.QtWidgets import QPushButton

    btn_ok = dlg.findChild(QPushButton, "btn_ok")
    assert btn_ok is not None
    with qtbot.waitSignal(dlg.accepted, timeout=2000):
        btn_ok.click()


def test_cancel_button_rejects(qtbot, dlg):
    from PySide6.QtWidgets import QPushButton

    btn_cancel = dlg.findChild(QPushButton, "btn_cancel")
    assert btn_cancel is not None
    with qtbot.waitSignal(dlg.rejected, timeout=2000):
        btn_cancel.click()


# ---------------------------------------------------------------------------
# Example fixture end-to-end
# ---------------------------------------------------------------------------


def test_example_fixture_dialog_constructs(qtbot):
    """ReimportMergeDialog should handle the real example fixture."""
    preview = ImportService.import_legacy(_FIXTURE)
    existing = CpsmDocument()  # empty existing
    dialog = ReimportMergeDialog(existing, preview)
    qtbot.addWidget(dialog)
    dialog.show()
    decisions = dialog.merge_decisions
    # All connections in source against empty existing → all should default to "add"
    for conn_id, decision in decisions.items():
        assert decision == "add", f"Expected 'add' for new connection '{conn_id}', got '{decision}'"
