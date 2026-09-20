# -*- coding: utf-8 -*-
"""
pytest-qt tests for LayoutEditorDialog.

Spec sections: §4.10

Covers:
- Construction with empty doc + new layout — opens cleanly
- Construction with existing layout — pre-populates fields
- Save with valid layout: emits saved signal; ConfigService.save called
- Cancel: no save
- Inherits_from cycle: choosing self or a layout that already references
  this one → validation error, Save disabled with tooltip
- Stable objectNames

All tests run with QT_QPA_PLATFORM=offscreen (set in conftest.py).
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from cpsm.data.schema import CpsmDocument, ScreenLayout
from cpsm.ui.dialogs.layout_editor import LayoutEditorDialog

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_doc(*layouts: ScreenLayout) -> CpsmDocument:
    """Return a CpsmDocument containing *layouts*."""
    return CpsmDocument(screen_layouts=list(layouts))


def _sl(id_: str, name: str, inherits_from: str | None = None) -> ScreenLayout:
    return ScreenLayout(id=id_, name=name, inherits_from=inherits_from)


def _make_dlg(
    qtbot,
    *,
    document: CpsmDocument | None = None,
    layout: ScreenLayout | None = None,
    config_service=None,
    is_new: bool = True,
) -> LayoutEditorDialog:
    if document is None:
        document = CpsmDocument()
    if layout is None:
        layout = _sl("new-layout", "New Layout")
    dlg = LayoutEditorDialog(
        document=document,
        layout=layout,
        config_service=config_service,
        is_new=is_new,
    )
    qtbot.addWidget(dlg)
    dlg.show()
    return dlg


# ---------------------------------------------------------------------------
# Object names / stable identifiers
# ---------------------------------------------------------------------------


def test_dialog_object_name(qtbot):
    dlg = _make_dlg(qtbot)
    assert dlg.objectName() == "dlg_layout_editor"


def test_edit_layout_id_object_name(qtbot):
    dlg = _make_dlg(qtbot)
    from PySide6.QtWidgets import QLineEdit

    widget = dlg.findChild(QLineEdit, "edit_layout_id")
    assert widget is not None


def test_edit_layout_name_object_name(qtbot):
    dlg = _make_dlg(qtbot)
    from PySide6.QtWidgets import QLineEdit

    widget = dlg.findChild(QLineEdit, "edit_layout_name")
    assert widget is not None


def test_combo_inherits_from_object_name(qtbot):
    dlg = _make_dlg(qtbot)
    from PySide6.QtWidgets import QComboBox

    widget = dlg.findChild(QComboBox, "combo_inherits_from")
    assert widget is not None


def test_btn_save_object_name(qtbot):
    dlg = _make_dlg(qtbot)
    from PySide6.QtWidgets import QPushButton

    widget = dlg.findChild(QPushButton, "btn_save_layout")
    assert widget is not None


def test_btn_cancel_object_name(qtbot):
    dlg = _make_dlg(qtbot)
    from PySide6.QtWidgets import QPushButton

    widget = dlg.findChild(QPushButton, "btn_cancel_layout")
    assert widget is not None


def test_screen_map_object_name(qtbot):
    from cpsm.ui.widgets.screen_map import ScreenMapWidget

    dlg = _make_dlg(qtbot)
    widget = dlg.findChild(ScreenMapWidget, "layout_editor_screen_map")
    assert widget is not None


# ---------------------------------------------------------------------------
# Construction with empty doc + new layout
# ---------------------------------------------------------------------------


def test_opens_cleanly_empty_doc(qtbot):
    """Opening with an empty CpsmDocument and a new layout must not raise."""
    dlg = _make_dlg(qtbot)
    assert dlg is not None


def test_new_layout_id_editable(qtbot):
    dlg = _make_dlg(qtbot, is_new=True)
    assert not dlg._edit_id.isReadOnly()


def test_new_layout_window_title(qtbot):
    dlg = _make_dlg(qtbot, is_new=True)
    assert "New" in dlg.windowTitle()


# ---------------------------------------------------------------------------
# Construction with existing layout — pre-populates fields
# ---------------------------------------------------------------------------


def test_existing_layout_id_locked(qtbot):
    sl = _sl("my-layout", "My Layout")
    dlg = _make_dlg(qtbot, layout=sl, is_new=False)
    assert dlg._edit_id.isReadOnly()


def test_existing_layout_populates_id(qtbot):
    sl = _sl("existing-layout", "Existing Layout")
    dlg = _make_dlg(qtbot, layout=sl, is_new=False)
    assert dlg._edit_id.text() == "existing-layout"


def test_existing_layout_populates_name(qtbot):
    sl = _sl("my-layout", "My Custom Layout")
    dlg = _make_dlg(qtbot, layout=sl, is_new=False)
    assert dlg._edit_name.text() == "My Custom Layout"


def test_existing_layout_populates_inherits_from(qtbot):
    parent = _sl("parent-layout", "Parent Layout")
    child = _sl("child-layout", "Child Layout", inherits_from="parent-layout")
    doc = _make_doc(parent, child)
    dlg = _make_dlg(qtbot, document=doc, layout=child, is_new=False)
    assert dlg._combo_inherits.currentData() == "parent-layout"


def test_existing_layout_no_inherits_shows_none(qtbot):
    sl = _sl("solo-layout", "Solo Layout")
    dlg = _make_dlg(qtbot, layout=sl, is_new=False)
    assert dlg._combo_inherits.currentData() == ""


def test_edit_window_title(qtbot):
    sl = _sl("my-layout", "My Layout")
    dlg = _make_dlg(qtbot, layout=sl, is_new=False)
    assert "Edit" in dlg.windowTitle()


# ---------------------------------------------------------------------------
# Inherits_from combo lists other layouts (not self)
# ---------------------------------------------------------------------------


def test_inherits_from_combo_lists_other_layouts(qtbot):
    sl_a = _sl("layout-a", "Layout A")
    sl_b = _sl("layout-b", "Layout B")
    doc = _make_doc(sl_a, sl_b)
    # Editing layout-a — combo should offer layout-b but not layout-a
    dlg = _make_dlg(qtbot, document=doc, layout=sl_a, is_new=False)
    items = [dlg._combo_inherits.itemData(i) for i in range(dlg._combo_inherits.count())]
    assert "layout-b" in items
    assert "layout-a" not in items


def test_inherits_from_combo_has_none_option(qtbot):
    dlg = _make_dlg(qtbot)
    assert dlg._combo_inherits.itemData(0) == ""


# ---------------------------------------------------------------------------
# Save with valid layout
# ---------------------------------------------------------------------------


def test_save_emits_saved_signal(qtbot):
    sl = _sl("my-layout", "My Layout")
    doc = _make_doc(sl)
    config_svc = MagicMock()
    dlg = _make_dlg(qtbot, document=doc, layout=sl, config_service=config_svc, is_new=False)

    received: list[ScreenLayout] = []
    dlg.saved.connect(received.append)

    dlg._edit_name.setText("Updated Layout")
    dlg._on_save()

    assert len(received) == 1
    assert isinstance(received[0], ScreenLayout)
    assert received[0].name == "Updated Layout"


def test_save_calls_config_service_save(qtbot):
    sl = _sl("my-layout", "My Layout")
    doc = _make_doc(sl)
    config_svc = MagicMock()
    dlg = _make_dlg(qtbot, document=doc, layout=sl, config_service=config_svc, is_new=False)

    dlg._on_save()

    config_svc.save.assert_called_once()


def test_save_accepts_dialog(qtbot):
    sl = _sl("my-layout", "My Layout")
    doc = _make_doc(sl)
    config_svc = MagicMock()
    dlg = _make_dlg(qtbot, document=doc, layout=sl, config_service=config_svc, is_new=False)

    dlg._on_save()

    assert dlg.result() == QDialog.DialogCode.Accepted


def test_save_without_config_service_still_emits(qtbot):
    sl = _sl("my-layout", "My Layout")
    doc = _make_doc(sl)
    dlg = _make_dlg(qtbot, document=doc, layout=sl, config_service=None, is_new=False)

    received: list[ScreenLayout] = []
    dlg.saved.connect(received.append)

    dlg._on_save()

    assert len(received) == 1


def test_save_preserves_id(qtbot):
    sl = _sl("keep-this-id", "Keep Layout")
    doc = _make_doc(sl)
    config_svc = MagicMock()
    dlg = _make_dlg(qtbot, document=doc, layout=sl, config_service=config_svc, is_new=False)

    received: list[ScreenLayout] = []
    dlg.saved.connect(received.append)
    dlg._on_save()

    assert received[0].id == "keep-this-id"


# ---------------------------------------------------------------------------
# Cancel: no save
# ---------------------------------------------------------------------------


def test_cancel_does_not_call_config_service(qtbot):
    sl = _sl("my-layout", "My Layout")
    doc = _make_doc(sl)
    config_svc = MagicMock()
    dlg = _make_dlg(qtbot, document=doc, layout=sl, config_service=config_svc, is_new=False)

    dlg.reject()

    config_svc.save.assert_not_called()


def test_cancel_does_not_emit_saved(qtbot):
    sl = _sl("my-layout", "My Layout")
    doc = _make_doc(sl)
    dlg = _make_dlg(qtbot, document=doc, layout=sl, config_service=None, is_new=False)

    received: list[ScreenLayout] = []
    dlg.saved.connect(received.append)

    dlg.reject()

    assert received == []


def test_cancel_rejects_dialog(qtbot):
    sl = _sl("my-layout", "My Layout")
    dlg = _make_dlg(qtbot, layout=sl, is_new=False)
    dlg.reject()
    assert dlg.result() == QDialog.DialogCode.Rejected


# ---------------------------------------------------------------------------
# Cycle detection: choosing self
# ---------------------------------------------------------------------------


def test_choosing_self_in_inherits_disables_save(qtbot):
    """When the user manually types a layout id then picks itself it should be rejected.

    Because the combo never shows self, we test the internal cycle detection
    directly via _would_create_cycle and _validate_inherits_from.
    """
    sl = _sl("my-layout", "My Layout")
    doc = _make_doc(sl)
    dlg = _make_dlg(qtbot, document=doc, layout=sl, is_new=False)

    # Force the ID field to match what we're about to test
    # (ID is locked but we can call the validator directly)
    result = dlg._would_create_cycle("my-layout", "my-layout")
    assert result is True


def test_inherits_from_error_shown_on_cycle(qtbot):
    """_validate_inherits_from sets err_inherits visible on cycle detection."""
    # layout-b inherits layout-a; if we try to make layout-a inherit layout-b → cycle
    sl_a = _sl("layout-a", "Layout A")
    sl_b = _sl("layout-b", "Layout B", inherits_from="layout-a")
    doc = _make_doc(sl_a, sl_b)

    # Editing layout-a, proposing to inherit layout-b (which already inherits layout-a)
    dlg = _make_dlg(qtbot, document=doc, layout=sl_a, is_new=False)

    # Simulate selecting layout-b in the combo
    idx = dlg._combo_inherits.findData("layout-b")
    assert idx >= 0, "layout-b should be in combo"
    dlg._combo_inherits.setCurrentIndex(idx)
    dlg._validate_inherits_from()

    assert dlg._err_inherits.isVisible()
    assert dlg._err_inherits.text() != ""


def test_inherits_from_cycle_disables_save(qtbot):
    sl_a = _sl("layout-a", "Layout A")
    sl_b = _sl("layout-b", "Layout B", inherits_from="layout-a")
    doc = _make_doc(sl_a, sl_b)

    dlg = _make_dlg(qtbot, document=doc, layout=sl_a, is_new=False)

    idx = dlg._combo_inherits.findData("layout-b")
    dlg._combo_inherits.setCurrentIndex(idx)
    dlg._validate_inherits_from()

    assert not dlg._btn_save.isEnabled()


def test_inherits_from_cycle_sets_tooltip(qtbot):
    sl_a = _sl("layout-a", "Layout A")
    sl_b = _sl("layout-b", "Layout B", inherits_from="layout-a")
    doc = _make_doc(sl_a, sl_b)

    dlg = _make_dlg(qtbot, document=doc, layout=sl_a, is_new=False)

    idx = dlg._combo_inherits.findData("layout-b")
    dlg._combo_inherits.setCurrentIndex(idx)
    dlg._validate_inherits_from()

    # Save button must have a non-empty tooltip explaining the block
    assert dlg._btn_save.toolTip() != ""


def test_no_cycle_does_not_disable_save(qtbot):
    sl_a = _sl("layout-a", "Layout A")
    sl_b = _sl("layout-b", "Layout B")
    doc = _make_doc(sl_a, sl_b)

    # Editing layout-b; inheriting layout-a is fine (no cycle)
    dlg = _make_dlg(qtbot, document=doc, layout=sl_b, is_new=False)

    idx = dlg._combo_inherits.findData("layout-a")
    assert idx >= 0
    dlg._combo_inherits.setCurrentIndex(idx)
    dlg._validate_inherits_from()

    assert dlg._btn_save.isEnabled()
    assert not dlg._err_inherits.isVisible()


# ---------------------------------------------------------------------------
# Pydantic validation via _on_save
# ---------------------------------------------------------------------------


def test_save_blocked_on_empty_name(qtbot):
    sl = _sl("my-layout", "My Layout")
    dlg = _make_dlg(qtbot, layout=sl, is_new=False)

    dlg._edit_name.setText("")
    dlg._validate_name()

    assert not dlg._btn_save.isEnabled()


def test_save_blocked_on_empty_id_for_new(qtbot):
    dlg = _make_dlg(qtbot, is_new=True)
    dlg._edit_id.setText("")
    dlg._validate_id()
    assert not dlg._btn_save.isEnabled()


def test_save_blocked_on_invalid_id_slug(qtbot):
    dlg = _make_dlg(qtbot, is_new=True)
    dlg._edit_id.setText("INVALID SLUG!")
    dlg._validate_id()
    assert not dlg._btn_save.isEnabled()


def test_save_enabled_with_valid_fields(qtbot):
    dlg = _make_dlg(qtbot, is_new=True)
    dlg._edit_id.setText("valid-layout")
    dlg._edit_name.setText("Valid Layout")
    dlg._validate_id()
    dlg._validate_name()
    dlg._validate_inherits_from()
    assert dlg._btn_save.isEnabled()


# ---------------------------------------------------------------------------
# get_layout public API
# ---------------------------------------------------------------------------


def test_get_layout_returns_screen_layout(qtbot):
    sl = _sl("test-layout", "Test Layout")
    dlg = _make_dlg(qtbot, layout=sl, is_new=False)
    result = dlg.get_layout()
    assert isinstance(result, ScreenLayout)


def test_get_layout_reflects_name_change(qtbot):
    sl = _sl("test-layout", "Test Layout")
    dlg = _make_dlg(qtbot, layout=sl, is_new=False)
    dlg._edit_name.setText("Renamed Layout")
    result = dlg.get_layout()
    assert result.name == "Renamed Layout"


# ---------------------------------------------------------------------------
# Transitive cycle detection (3-level chain)
# ---------------------------------------------------------------------------


def test_transitive_cycle_detected(qtbot):
    """A → B → C, if we try C inherits A → cycle through the chain."""
    sl_a = _sl("layout-a", "Layout A")
    sl_b = _sl("layout-b", "Layout B", inherits_from="layout-a")
    sl_c = _sl("layout-c", "Layout C", inherits_from="layout-b")
    doc = _make_doc(sl_a, sl_b, sl_c)

    dlg = _make_dlg(qtbot, document=doc, layout=sl_a, is_new=False)
    # Would layout-a inherit layout-c (which inherits layout-b which inherits layout-a)?
    result = dlg._would_create_cycle("layout-a", "layout-c")
    assert result is True


def test_no_transitive_cycle_for_unrelated_chain(qtbot):
    """A → B → C, D is independent; D inheriting C is fine."""
    sl_a = _sl("layout-a", "Layout A")
    sl_b = _sl("layout-b", "Layout B", inherits_from="layout-a")
    sl_c = _sl("layout-c", "Layout C", inherits_from="layout-b")
    sl_d = _sl("layout-d", "Layout D")
    doc = _make_doc(sl_a, sl_b, sl_c, sl_d)

    dlg = _make_dlg(qtbot, document=doc, layout=sl_d, is_new=False)
    result = dlg._would_create_cycle("layout-d", "layout-c")
    assert result is False


# Import needed for result code assertions
from PySide6.QtWidgets import QDialog  # noqa: E402
