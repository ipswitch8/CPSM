# -*- coding: utf-8 -*-
"""
pytest-qt tests for cpsm.ui.dialogs.import_preview.ImportPreviewDialog.

Spec sections: §4.3, §4.4

All tests run with QT_QPA_PLATFORM=offscreen (set in tests/ui/conftest.py).
"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: I001

from cpsm.data.importer import ImportPreview, ImportTransform
from cpsm.data.schema import (
    ClaudeRemoteConnection,
    CpsmDocument,
    SshKey,
)
from cpsm.services.import_service import ImportService
from cpsm.ui.dialogs.import_preview import ImportPreviewDialog, _COL_SKIP

# Path to the fixture file shipped with the test suite
_FIXTURE = Path(__file__).parent.parent / "data" / "fixtures" / "example-.claude-projects.yaml"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def example_preview() -> ImportPreview:
    """Return an ImportPreview from the example fixture file."""
    return ImportService.import_legacy(_FIXTURE)


@pytest.fixture()
def minimal_preview() -> ImportPreview:
    """Return a minimal ImportPreview with two connections."""
    key = SshKey(
        id="imported-default",
        name="Imported default key",
        type="ed25519",
        private_path="~/.ssh/id_ed25519",
        public_path="~/.ssh/id_ed25519.pub",
    )
    conn1 = ClaudeRemoteConnection(
        id="web01",
        name="WebApp Frontend",
        launch_profile="claude-remote",
        host="dev.example.com",
        user="ubuntu",
        identity_file_ref="imported-default",
        project_folder="/opt/webapp",
        claude_options="--resume",
    )
    conn2 = ClaudeRemoteConnection(
        id="web02",
        name="WebApp Backend",
        launch_profile="claude-remote",
        host="dev.example.com",
        user="ubuntu",
        identity_file_ref="imported-default",
        project_folder="/opt/api",
        claude_options="--resume",
    )
    doc = CpsmDocument(ssh_keys=[key], connections=[conn1, conn2])
    transforms = [
        ImportTransform(
            kind="synthesized", target_path="ssh_keys[imported-default]", detail="placeholder"
        ),
        ImportTransform(
            kind="added",
            target_path="connections[web01]",
            detail="from projects[].name='WebApp Frontend'",
        ),
        ImportTransform(
            kind="added",
            target_path="connections[web02]",
            detail="from projects[].name='WebApp Backend'",
        ),
    ]
    return ImportPreview(
        source_path=Path("/tmp/fake-.claude-projects.yaml"),
        transforms=transforms,
        document=doc,
    )


@pytest.fixture()
def dlg(qtbot, minimal_preview):
    """Create and show an ImportPreviewDialog with minimal preview."""
    dialog = ImportPreviewDialog(minimal_preview)
    qtbot.addWidget(dialog)
    dialog.show()
    return dialog


@pytest.fixture()
def example_dlg(qtbot, example_preview):
    """Create and show an ImportPreviewDialog with the example fixture preview."""
    dialog = ImportPreviewDialog(example_preview)
    qtbot.addWidget(dialog)
    dialog.show()
    return dialog


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_dialog_constructs_cleanly(qtbot, minimal_preview):
    """ImportPreviewDialog should open without raising."""
    dialog = ImportPreviewDialog(minimal_preview)
    qtbot.addWidget(dialog)
    assert dialog is not None


def test_dialog_object_name(dlg):
    assert dlg.objectName() == "dlg_import_preview"


def test_dialog_title(dlg):
    assert dlg.windowTitle() == "Import Preview"


# ---------------------------------------------------------------------------
# Stable objectNames
# ---------------------------------------------------------------------------


def test_table_transforms_present(dlg):
    from PySide6.QtWidgets import QTableView

    tbl = dlg.findChild(QTableView, "table_transforms")
    assert tbl is not None


def test_lbl_source_present(dlg):
    lbl = dlg.findChild(object, "lbl_source")
    assert lbl is not None


def test_lbl_summary_present(dlg):
    lbl = dlg.findChild(object, "lbl_summary")
    assert lbl is not None


def test_btn_ok_present(dlg):
    from PySide6.QtWidgets import QPushButton

    btn = dlg.findChild(QPushButton, "btn_ok")
    assert btn is not None


def test_btn_cancel_present(dlg):
    from PySide6.QtWidgets import QPushButton

    btn = dlg.findChild(QPushButton, "btn_cancel")
    assert btn is not None


# ---------------------------------------------------------------------------
# Row count
# ---------------------------------------------------------------------------


def test_row_count_matches_transforms(dlg, minimal_preview):
    """Table should have one row per transform."""
    from PySide6.QtWidgets import QTableView

    tbl = dlg.findChild(QTableView, "table_transforms")
    assert tbl is not None
    assert tbl.model().rowCount() == len(minimal_preview.transforms)


def test_example_preview_rows(example_dlg, example_preview):
    """Example fixture should produce correct number of rows."""
    from PySide6.QtWidgets import QTableView

    tbl = example_dlg.findChild(QTableView, "table_transforms")
    assert tbl is not None
    assert tbl.model().rowCount() == len(example_preview.transforms)


# ---------------------------------------------------------------------------
# Skip checkbox behaviour
# ---------------------------------------------------------------------------


def test_skip_excludes_row_from_filtered_preview(qtbot, minimal_preview):
    """Checking Skip on a connection row removes it from filtered_preview."""
    from PySide6.QtCore import Qt

    dialog = ImportPreviewDialog(minimal_preview)
    qtbot.addWidget(dialog)
    model = dialog._model

    # Find a connection "added" row
    conn_rows = [
        i
        for i, t in enumerate(minimal_preview.transforms)
        if t.kind == "added" and t.target_path.startswith("connections[")
    ]
    assert conn_rows, "Expected at least one connection row"
    target_row = conn_rows[0]

    # Check the skip checkbox
    idx = model.index(target_row, _COL_SKIP)
    model.setData(idx, Qt.CheckState.Checked, Qt.ItemDataRole.CheckStateRole)

    filtered = dialog.filtered_preview
    skipped_transform = minimal_preview.transforms[target_row]

    # Extract the skipped connection id
    raw = skipped_transform.target_path  # "connections[web01]"
    skipped_id = raw[len("connections[") : -1]

    conn_ids = [c.id for c in filtered.document.connections]
    assert skipped_id not in conn_ids


def test_skip_row_excluded_from_filtered_transforms(qtbot, minimal_preview):
    """Skipped transform should not appear in filtered_preview.transforms."""
    from PySide6.QtCore import Qt

    dialog = ImportPreviewDialog(minimal_preview)
    qtbot.addWidget(dialog)
    model = dialog._model

    conn_rows = [
        i
        for i, t in enumerate(minimal_preview.transforms)
        if t.kind == "added" and t.target_path.startswith("connections[")
    ]
    target_row = conn_rows[0]
    skipped_transform = minimal_preview.transforms[target_row]

    idx = model.index(target_row, _COL_SKIP)
    model.setData(idx, Qt.CheckState.Checked, Qt.ItemDataRole.CheckStateRole)

    filtered = dialog.filtered_preview
    assert skipped_transform not in filtered.transforms


# ---------------------------------------------------------------------------
# Editing target ID
# ---------------------------------------------------------------------------


def test_edit_target_id_updates_filtered_preview(qtbot, minimal_preview):
    """Editing a connection row's Target Path renames the id in filtered_preview."""
    from cpsm.ui.dialogs.import_preview import _COL_TARGET  # noqa: I001
    from PySide6.QtCore import Qt

    dialog = ImportPreviewDialog(minimal_preview)
    qtbot.addWidget(dialog)
    model = dialog._model

    conn_rows = [
        i
        for i, t in enumerate(minimal_preview.transforms)
        if t.kind == "added" and t.target_path.startswith("connections[")
    ]
    assert conn_rows
    target_row = conn_rows[0]

    # Edit the target path to a new id
    idx = model.index(target_row, _COL_TARGET)
    model.setData(idx, "connections[new-id-01]", Qt.ItemDataRole.EditRole)

    filtered = dialog.filtered_preview
    conn_ids = [c.id for c in filtered.document.connections]
    assert "new-id-01" in conn_ids


# ---------------------------------------------------------------------------
# Duplicate id disables OK
# ---------------------------------------------------------------------------


def test_duplicate_id_disables_ok(qtbot, minimal_preview):
    """Setting two connection rows to the same id should disable the OK button."""
    from cpsm.ui.dialogs.import_preview import _COL_TARGET  # noqa: I001
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QPushButton

    dialog = ImportPreviewDialog(minimal_preview)
    qtbot.addWidget(dialog)
    model = dialog._model

    conn_rows = [
        i
        for i, t in enumerate(minimal_preview.transforms)
        if t.kind == "added" and t.target_path.startswith("connections[")
    ]
    assert len(conn_rows) >= 2, "Need at least two connection rows"

    # Set both to the same id
    for row in conn_rows[:2]:
        idx = model.index(row, _COL_TARGET)
        model.setData(idx, "connections[duplicate-id]", Qt.ItemDataRole.EditRole)

    btn_ok = dialog.findChild(QPushButton, "btn_ok")
    assert btn_ok is not None
    assert not btn_ok.isEnabled()


def test_unique_ids_enables_ok(qtbot, minimal_preview):
    """With no duplicate ids, OK button should be enabled."""
    from PySide6.QtWidgets import QPushButton

    dialog = ImportPreviewDialog(minimal_preview)
    qtbot.addWidget(dialog)

    btn_ok = dialog.findChild(QPushButton, "btn_ok")
    assert btn_ok is not None
    assert btn_ok.isEnabled()


# ---------------------------------------------------------------------------
# OK / Cancel buttons
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
# filtered_preview preserves source_path
# ---------------------------------------------------------------------------


def test_filtered_preview_preserves_source_path(qtbot, minimal_preview):
    """filtered_preview should carry the same source_path as the original."""
    dialog = ImportPreviewDialog(minimal_preview)
    qtbot.addWidget(dialog)
    assert dialog.filtered_preview.source_path == minimal_preview.source_path


# ---------------------------------------------------------------------------
# Example fixture end-to-end
# ---------------------------------------------------------------------------


def test_example_fixture_preview_ok_accept(qtbot, example_preview):
    """Full accept path with the example fixture should complete cleanly."""
    from PySide6.QtWidgets import QPushButton

    dialog = ImportPreviewDialog(example_preview)
    qtbot.addWidget(dialog)
    dialog.show()

    btn_ok = dialog.findChild(QPushButton, "btn_ok")
    assert btn_ok is not None

    with qtbot.waitSignal(dialog.accepted, timeout=2000):
        btn_ok.click()

    fp = dialog.filtered_preview
    assert fp is not None
    assert fp.source_path == example_preview.source_path
