# -*- coding: utf-8 -*-
"""
pytest-qt tests for LauncherTemplatesDialog.

Spec section: §4.9

Covers:
- All 5 built-in profiles listed (claude-remote, claude-local, ssh-shell,
  local-shell, _placeholder).
- _placeholder.sh is read-only (Save Override and editor both disabled).
- "Restore Default" calls template_service.restore_default().
- Custom template editing updates doc.launch_templates[].

All tests run with QT_QPA_PLATFORM=offscreen (set in conftest.py).
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import (
    QListWidget,
    QPlainTextEdit,
    QPushButton,
)

from cpsm.data.schema import CpsmDocument, LaunchTemplate
from cpsm.services.template_service import TemplateService
from cpsm.ui.dialogs.launcher_templates import (
    _BUILTIN_PROFILES,
    LauncherTemplatesDialog,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_template_service(override_dir: Path | None = None) -> TemplateService:
    return TemplateService(override_dir=override_dir)


def _make_doc_with_custom(*templates: LaunchTemplate) -> CpsmDocument:
    doc = CpsmDocument()
    doc.launch_templates = list(templates)
    return doc


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestBuiltinProfilesListed:
    def test_all_five_profiles_listed(self, qtbot):
        svc = _make_template_service()
        dlg = LauncherTemplatesDialog(svc)
        qtbot.addWidget(dlg)

        lst: QListWidget = dlg.findChild(QListWidget, "list_templates")
        assert lst is not None

        labels = [lst.item(i).text() for i in range(lst.count())]

        for profile in _BUILTIN_PROFILES:
            assert any(profile in label for label in labels), (
                f"Profile '{profile}' not found in list items: {labels}"
            )
        # _placeholder
        assert any("_placeholder" in label for label in labels)

    def test_list_count_without_custom(self, qtbot):
        svc = _make_template_service()
        dlg = LauncherTemplatesDialog(svc)
        qtbot.addWidget(dlg)

        lst: QListWidget = dlg.findChild(QListWidget, "list_templates")
        # 4 built-in + 1 placeholder = 5
        assert lst.count() == 5

    def test_list_count_with_custom(self, qtbot):
        svc = _make_template_service()
        tpl = LaunchTemplate(id="tpl-custom-one", bash="echo hi", description="test")
        doc = _make_doc_with_custom(tpl)
        dlg = LauncherTemplatesDialog(svc, doc=doc)
        qtbot.addWidget(dlg)

        lst: QListWidget = dlg.findChild(QListWidget, "list_templates")
        assert lst.count() == 6  # 5 built-in + 1 custom


class TestPlaceholderReadOnly:
    def _select_placeholder(self, dlg: LauncherTemplatesDialog, lst: QListWidget) -> None:
        for i in range(lst.count()):
            item = lst.item(i)
            if item and "_placeholder" in item.text():
                lst.setCurrentRow(i)
                return
        raise AssertionError("_placeholder not found in list")

    def test_placeholder_editor_readonly(self, qtbot):
        svc = _make_template_service()
        dlg = LauncherTemplatesDialog(svc)
        qtbot.addWidget(dlg)

        lst: QListWidget = dlg.findChild(QListWidget, "list_templates")
        self._select_placeholder(dlg, lst)

        editor: QPlainTextEdit = dlg.findChild(QPlainTextEdit, "text_template_body")
        assert editor is not None
        assert editor.isReadOnly(), "Editor should be read-only for _placeholder"

    def test_placeholder_save_override_disabled(self, qtbot):
        svc = _make_template_service()
        dlg = LauncherTemplatesDialog(svc)
        qtbot.addWidget(dlg)

        lst: QListWidget = dlg.findChild(QListWidget, "list_templates")
        self._select_placeholder(dlg, lst)

        btn = dlg.findChild(QPushButton, "btn_save_override")
        assert btn is not None
        assert not btn.isEnabled(), "Save Override should be disabled for _placeholder"

    def test_placeholder_body_not_empty(self, qtbot):
        svc = _make_template_service()
        dlg = LauncherTemplatesDialog(svc)
        qtbot.addWidget(dlg)

        lst: QListWidget = dlg.findChild(QListWidget, "list_templates")
        self._select_placeholder(dlg, lst)

        editor: QPlainTextEdit = dlg.findChild(QPlainTextEdit, "text_template_body")
        assert editor.toPlainText().strip() != "", "Placeholder body should not be empty"


class TestRestoreDefault:
    def test_restore_default_calls_service(self, qtbot):
        svc = MagicMock(spec=TemplateService)
        svc._load_builtin_or_override = MagicMock(return_value="# template body")
        svc.restore_default = MagicMock()

        dlg = LauncherTemplatesDialog(svc)
        qtbot.addWidget(dlg)

        lst: QListWidget = dlg.findChild(QListWidget, "list_templates")
        # Select "claude-remote"
        for i in range(lst.count()):
            item = lst.item(i)
            if item and "claude-remote" in item.text():
                lst.setCurrentRow(i)
                break

        with patch("cpsm.ui.dialogs.launcher_templates.QMessageBox") as MockMsgBox:
            MockMsgBox.information = MagicMock()
            dlg._on_restore_default()

        svc.restore_default.assert_called_once_with("claude-remote")

    def test_restore_default_disabled_for_placeholder(self, qtbot):
        svc = _make_template_service()
        dlg = LauncherTemplatesDialog(svc)
        qtbot.addWidget(dlg)

        lst: QListWidget = dlg.findChild(QListWidget, "list_templates")
        for i in range(lst.count()):
            item = lst.item(i)
            if item and "_placeholder" in item.text():
                lst.setCurrentRow(i)
                break

        btn = dlg.findChild(QPushButton, "btn_restore_default")
        assert btn is not None
        assert not btn.isEnabled()


class TestCustomTemplateEditing:
    def test_custom_template_in_list(self, qtbot):
        svc = _make_template_service()
        tpl = LaunchTemplate(
            id="tpl-nspawn", bash="machinectl shell root@dev", description="nspawn"
        )
        doc = _make_doc_with_custom(tpl)
        dlg = LauncherTemplatesDialog(svc, doc=doc)
        qtbot.addWidget(dlg)

        lst: QListWidget = dlg.findChild(QListWidget, "list_templates")
        labels = [lst.item(i).text() for i in range(lst.count())]
        assert any("tpl-nspawn" in label for label in labels)

    def test_editing_custom_template_updates_doc(self, qtbot):
        svc = _make_template_service()
        tpl = LaunchTemplate(id="tpl-edit-test", bash="echo original", description="editable")
        doc = _make_doc_with_custom(tpl)
        dlg = LauncherTemplatesDialog(svc, doc=doc)
        qtbot.addWidget(dlg)

        lst: QListWidget = dlg.findChild(QListWidget, "list_templates")
        for i in range(lst.count()):
            item = lst.item(i)
            if item and "tpl-edit-test" in item.text():
                lst.setCurrentRow(i)
                break

        editor: QPlainTextEdit = dlg.findChild(QPlainTextEdit, "text_template_body")
        assert editor is not None
        assert not editor.isReadOnly(), "Custom template editor should be editable"

        editor.setPlainText("echo modified")

        # Simulate "Edit Custom Template" → only saves body
        with patch("cpsm.ui.dialogs.launcher_templates.QInputDialog") as MockInput:
            MockInput.getText.return_value = ("editable", True)
            dlg._on_edit_custom()

        # Doc should be updated
        updated = next(t for t in doc.launch_templates if t.id == "tpl-edit-test")
        assert updated.bash == "echo modified"

    def test_edit_custom_button_enabled_only_for_custom(self, qtbot):
        svc = _make_template_service()
        tpl = LaunchTemplate(id="tpl-check", bash="echo check")
        doc = _make_doc_with_custom(tpl)
        dlg = LauncherTemplatesDialog(svc, doc=doc)
        qtbot.addWidget(dlg)

        lst: QListWidget = dlg.findChild(QListWidget, "list_templates")
        btn_edit = dlg.findChild(QPushButton, "btn_edit_custom")

        # Select a built-in — edit custom should be disabled
        for i in range(lst.count()):
            item = lst.item(i)
            if item and "claude-remote" == item.text():
                lst.setCurrentRow(i)
                break
        assert not btn_edit.isEnabled()

        # Select the custom — edit custom should be enabled
        for i in range(lst.count()):
            item = lst.item(i)
            if item and "tpl-check" in item.text():
                lst.setCurrentRow(i)
                break
        assert btn_edit.isEnabled()


class TestBuiltinBodyShown:
    def test_claude_remote_body_shown(self, qtbot):
        svc = _make_template_service()
        dlg = LauncherTemplatesDialog(svc)
        qtbot.addWidget(dlg)

        lst: QListWidget = dlg.findChild(QListWidget, "list_templates")
        for i in range(lst.count()):
            item = lst.item(i)
            if item and item.text() == "claude-remote":
                lst.setCurrentRow(i)
                break

        editor: QPlainTextEdit = dlg.findChild(QPlainTextEdit, "text_template_body")
        body = editor.toPlainText()
        assert len(body) > 0, "Template body should be shown"
        assert "claude" in body.lower() or "CPSM" in body
