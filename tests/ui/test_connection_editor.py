# -*- coding: utf-8 -*-
"""
pytest-qt tests for ConnectionEditorDialog and ConnectionForm.

Spec sections: §4.5, §2.3

Covers:
- Field visibility per profile (§2.3 matrix)
- Profile-switch confirmation: reject keeps values; accept clears them
- Save validation: invalid host → error label, Save disabled, tooltip
- Test Connection dispatched to correct code path per profile
- Member-of label lists correct groups

All tests run with QT_QPA_PLATFORM=offscreen (set in conftest.py).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QMessageBox, QPushButton

from cpsm.data.schema import SshKey
from cpsm.services.key_discovery import KeyCandidate
from cpsm.services.template_service import IdentityKeyNotFoundError
from cpsm.ui.dialogs.connection_editor import ConnectionEditorDialog
from cpsm.ui.widgets.connection_form import ConnectionForm, _field_visible

# ---------------------------------------------------------------------------
# Isolation guard
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_real_key_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail loudly if a test reaches the real key-generation machinery.

    ``ConnectionEditorDialog._on_new_key_requested`` takes its KeyService and
    its GenerateKeyDialog from constructor injection, but falls back to the
    REAL ``KeyService()`` and the REAL ``GenerateKeyDialog`` when neither is
    supplied. That fallback is correct for production and dangerous in a test:
    a test that clicks "+ New Key…" without injecting would generate an actual
    ed25519 key pair into the developer's actual ~/.ssh, and would then likely
    pass, having quietly written to the user's real key directory.

    Nothing previously stopped that — the existing tests inject, but only
    because their authors remembered to. This makes forgetting an immediate,
    obvious failure instead of a silent side effect. Mirrors
    ``_no_real_ssh_dir`` in tests/services/test_key_discovery.py and
    ``_no_system_key_discovery`` in tests/ui/test_ssh_key_manager.py.

    The dialog imports both names lazily inside the handler, so patching the
    attribute on the defining module is what actually intercepts them.
    """
    import cpsm.services.key_service as key_service_mod
    import cpsm.ui.dialogs.generate_key as generate_key_mod

    def _forbidden_key_service(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError(
            "A test reached the real KeyService — it would generate an actual "
            "SSH key pair in the real ~/.ssh. Pass key_service=MagicMock() to "
            "ConnectionEditorDialog."
        )

    def _forbidden_dialog(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError(
            "A test reached the real GenerateKeyDialog. Pass "
            "generate_key_dialog_cls=<fake> to ConnectionEditorDialog."
        )

    monkeypatch.setattr(key_service_mod, "KeyService", _forbidden_key_service)
    monkeypatch.setattr(generate_key_mod, "GenerateKeyDialog", _forbidden_dialog)


@pytest.fixture(autouse=True)
def _no_real_key_probing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail loudly if a test reaches real key discovery or a real ssh probe.

    ``ConnectionEditorDialog._run_discovery_and_probe`` takes its
    ``KeyDiscoveryService`` and its probe task from constructor injection
    (``key_discovery_service`` / ``key_probe_factory``), but falls back to
    the REAL ``KeyDiscoveryService()`` (which defaults to scanning the real
    ``~/.ssh``) and a REAL ``KeyDiscoveryProbeTask`` (which launches a real
    ssh subprocess against whatever host the connection under test names)
    when neither is supplied.

    Mirrors ``_no_real_key_generation`` above: a test that forgets to inject
    should fail immediately and obviously, not silently reach the network.
    Both names are imported lazily inside the dialog, so patching the
    attribute on the defining module is what actually intercepts them.
    """
    import cpsm.services.key_discovery as key_discovery_mod
    import cpsm.workers.ssh_worker as ssh_worker_mod

    def _forbidden_discovery(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError(
            "A test reached the real KeyDiscoveryService — it would scan the "
            "real ~/.ssh. Pass key_discovery_service=MagicMock(...) to "
            "ConnectionEditorDialog."
        )

    def _forbidden_probe_task(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError(
            "A test reached the real KeyDiscoveryProbeTask — it would launch "
            "a real ssh subprocess against a possibly-production host. Pass "
            "key_probe_factory=<fake> to ConnectionEditorDialog."
        )

    monkeypatch.setattr(key_discovery_mod, "KeyDiscoveryService", _forbidden_discovery)
    monkeypatch.setattr(ssh_worker_mod, "KeyDiscoveryProbeTask", _forbidden_probe_task)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_GROUPS: list[dict[str, Any]] = [
    {"id": "grp-alpha", "name": "Alpha", "members": ["conn-remote", "conn-custom"]},
    {"id": "grp-beta", "name": "Beta", "members": ["conn-remote"]},
]

_ALL_KEY_IDS = ["key-prod", "key-dev"]
_ALL_TEMPLATE_IDS = ["tpl-nspawn", "tpl-docker"]
_ALL_CONN_IDS = ["conn-remote", "conn-local", "conn-ssh", "conn-shell", "conn-custom"]

# A resolvable SshKey matching the "key-prod" identity_file_ref used by
# _CONN_REMOTE / _CONN_SSH below, so tests dispatching Test Connection
# straight to ssh_test_factory exercise THAT path rather than falling
# through to key discovery (Phase 3: identity_file_ref is an id slug that
# must resolve via ssh_keys, never a bare filesystem path).
_KEY_PROD = SshKey(
    id="key-prod",
    name="Key Prod",
    type="ed25519",
    private_path="~/.ssh/key-prod",
    public_path="~/.ssh/key-prod.pub",
)

_CONN_REMOTE = {
    "id": "conn-remote",
    "name": "Remote Conn",
    "launch_profile": "claude-remote",
    "host": "dev.example.com",
    "port": 22,
    "user": "ubuntu",
    "identity_file_ref": "key-prod",
    "project_folder": "/opt/app",
    "claude_options": "--resume",
}

_CONN_LOCAL = {
    "id": "conn-local",
    "name": "Local Conn",
    "launch_profile": "claude-local",
    "project_folder": "~/projects/app",
    "claude_options": "--resume",
}

_CONN_SSH = {
    "id": "conn-ssh",
    "name": "SSH Shell",
    "launch_profile": "ssh-shell",
    "host": "bastion.example.com",
    "port": 22,
    "user": "admin",
    "identity_file_ref": "key-prod",
}

_CONN_SHELL = {
    "id": "conn-shell",
    "name": "Local Shell",
    "launch_profile": "local-shell",
    "project_folder": "~/scratch",
}

_CONN_CUSTOM = {
    "id": "conn-custom",
    "name": "Custom Conn",
    "launch_profile": "custom",
    "custom_template_id": "tpl-nspawn",
}


def _make_dlg(qtbot, conn_data=None, is_new=True, **kwargs):
    dlg = ConnectionEditorDialog(
        connection_data=conn_data,
        groups=_GROUPS,
        available_key_ids=_ALL_KEY_IDS,
        available_template_ids=_ALL_TEMPLATE_IDS,
        available_connection_ids=_ALL_CONN_IDS,
        is_new=is_new,
        **kwargs,
    )
    qtbot.addWidget(dlg)
    dlg.show()
    return dlg


@pytest.fixture()
def dlg_new(qtbot):
    return _make_dlg(qtbot)


@pytest.fixture()
def dlg_remote(qtbot):
    return _make_dlg(qtbot, conn_data=_CONN_REMOTE, is_new=False)


@pytest.fixture()
def dlg_local(qtbot):
    return _make_dlg(qtbot, conn_data=_CONN_LOCAL, is_new=False)


@pytest.fixture()
def dlg_ssh(qtbot):
    return _make_dlg(qtbot, conn_data=_CONN_SSH, is_new=False)


@pytest.fixture()
def dlg_shell(qtbot):
    return _make_dlg(qtbot, conn_data=_CONN_SHELL, is_new=False)


@pytest.fixture()
def dlg_custom(qtbot):
    return _make_dlg(qtbot, conn_data=_CONN_CUSTOM, is_new=False)


# ---------------------------------------------------------------------------
# Object names / construction
# ---------------------------------------------------------------------------


def test_dialog_object_name(dlg_new):
    assert dlg_new.objectName() == "dlg_connection_editor"


def test_form_embedded(dlg_new):
    form = dlg_new.findChild(ConnectionForm, "connection_form_embed")
    assert form is not None


def test_btn_save_present(dlg_new):
    from PySide6.QtWidgets import QPushButton

    btn = dlg_new.findChild(QPushButton, "btn_save")
    assert btn is not None


def test_btn_test_connection_present(dlg_new):
    from PySide6.QtWidgets import QPushButton

    btn = dlg_new.findChild(QPushButton, "btn_test_connection")
    assert btn is not None


# ---------------------------------------------------------------------------
# §2.3 field-visibility matrix — one test per profile
# ---------------------------------------------------------------------------


class TestVisibilityClaudeRemote:
    """claude-remote: host/port/user/identity/jump shown; custom_template hidden."""

    def _form(self, qtbot):
        form = ConnectionForm(available_key_ids=_ALL_KEY_IDS)
        qtbot.addWidget(form)
        form.show()
        # Load remote data (sets profile)
        form.load_data(_CONN_REMOTE)
        return form

    def test_host_visible(self, qtbot):
        f = self._form(qtbot)
        assert f._edit_host.isVisible()

    def test_port_visible(self, qtbot):
        f = self._form(qtbot)
        assert f._spin_port.isVisible()

    def test_user_visible(self, qtbot):
        f = self._form(qtbot)
        assert f._edit_user.isVisible()

    def test_identity_file_ref_visible(self, qtbot):
        f = self._form(qtbot)
        assert f._combo_identity_file_ref.isVisible()

    def test_jump_host_visible(self, qtbot):
        f = self._form(qtbot)
        assert f._combo_jump_host.isVisible()

    def test_project_folder_visible(self, qtbot):
        f = self._form(qtbot)
        assert f._edit_project_folder.isVisible()

    def test_claude_options_visible(self, qtbot):
        f = self._form(qtbot)
        assert f._edit_claude_options.isVisible()

    def test_custom_template_hidden(self, qtbot):
        f = self._form(qtbot)
        assert not f._combo_custom_template_id.isVisible()

    def test_keepalive_visible(self, qtbot):
        f = self._form(qtbot)
        assert f._spin_keepalive_interval_s.isVisible()


class TestVisibilityClaudeLocal:
    """claude-local: host/port/user/identity/jump hidden; claude_options shown."""

    def _form(self, qtbot):
        form = ConnectionForm()
        qtbot.addWidget(form)
        form.show()
        form.load_data(_CONN_LOCAL)
        return form

    def test_host_hidden(self, qtbot):
        f = self._form(qtbot)
        assert not f._edit_host.isVisible()

    def test_port_hidden(self, qtbot):
        f = self._form(qtbot)
        assert not f._spin_port.isVisible()

    def test_user_hidden(self, qtbot):
        f = self._form(qtbot)
        assert not f._edit_user.isVisible()

    def test_identity_hidden(self, qtbot):
        f = self._form(qtbot)
        assert not f._combo_identity_file_ref.isVisible()

    def test_jump_host_hidden(self, qtbot):
        f = self._form(qtbot)
        assert not f._combo_jump_host.isVisible()

    def test_project_folder_visible(self, qtbot):
        f = self._form(qtbot)
        assert f._edit_project_folder.isVisible()

    def test_claude_options_visible(self, qtbot):
        f = self._form(qtbot)
        assert f._edit_claude_options.isVisible()

    def test_custom_template_hidden(self, qtbot):
        f = self._form(qtbot)
        assert not f._combo_custom_template_id.isVisible()

    def test_keepalive_hidden(self, qtbot):
        f = self._form(qtbot)
        assert not f._spin_keepalive_interval_s.isVisible()


class TestVisibilitySshShell:
    """ssh-shell: host/port/user/identity shown; claude_options hidden."""

    def _form(self, qtbot):
        form = ConnectionForm(available_key_ids=_ALL_KEY_IDS)
        qtbot.addWidget(form)
        form.show()
        form.load_data(_CONN_SSH)
        return form

    def test_host_visible(self, qtbot):
        assert self._form(qtbot)._edit_host.isVisible()

    def test_port_visible(self, qtbot):
        assert self._form(qtbot)._spin_port.isVisible()

    def test_user_visible(self, qtbot):
        assert self._form(qtbot)._edit_user.isVisible()

    def test_identity_visible(self, qtbot):
        assert self._form(qtbot)._combo_identity_file_ref.isVisible()

    def test_claude_options_hidden(self, qtbot):
        assert not self._form(qtbot)._edit_claude_options.isVisible()

    def test_custom_template_hidden(self, qtbot):
        assert not self._form(qtbot)._combo_custom_template_id.isVisible()

    def test_keepalive_visible(self, qtbot):
        assert self._form(qtbot)._spin_keepalive_interval_s.isVisible()


class TestVisibilityLocalShell:
    """local-shell: host/port/user/identity/jump/claude_options hidden."""

    def _form(self, qtbot):
        form = ConnectionForm()
        qtbot.addWidget(form)
        form.show()
        form.load_data(_CONN_SHELL)
        return form

    def test_host_hidden(self, qtbot):
        assert not self._form(qtbot)._edit_host.isVisible()

    def test_port_hidden(self, qtbot):
        assert not self._form(qtbot)._spin_port.isVisible()

    def test_user_hidden(self, qtbot):
        assert not self._form(qtbot)._edit_user.isVisible()

    def test_identity_hidden(self, qtbot):
        assert not self._form(qtbot)._combo_identity_file_ref.isVisible()

    def test_jump_host_hidden(self, qtbot):
        assert not self._form(qtbot)._combo_jump_host.isVisible()

    def test_claude_options_hidden(self, qtbot):
        assert not self._form(qtbot)._edit_claude_options.isVisible()

    def test_custom_template_hidden(self, qtbot):
        assert not self._form(qtbot)._combo_custom_template_id.isVisible()

    def test_keepalive_hidden(self, qtbot):
        assert not self._form(qtbot)._spin_keepalive_interval_s.isVisible()

    def test_project_folder_visible(self, qtbot):
        assert self._form(qtbot)._edit_project_folder.isVisible()


class TestVisibilityCustom:
    """custom: all optional; custom_template_id required and shown."""

    def _form(self, qtbot):
        form = ConnectionForm(available_template_ids=_ALL_TEMPLATE_IDS)
        qtbot.addWidget(form)
        form.show()
        form.load_data(_CONN_CUSTOM)
        return form

    def test_custom_template_visible(self, qtbot):
        assert self._form(qtbot)._combo_custom_template_id.isVisible()

    def test_host_visible(self, qtbot):
        # custom: host shown (optional)
        assert self._form(qtbot)._edit_host.isVisible()

    def test_identity_visible(self, qtbot):
        assert self._form(qtbot)._combo_identity_file_ref.isVisible()

    def test_claude_options_visible(self, qtbot):
        assert self._form(qtbot)._edit_claude_options.isVisible()

    def test_keepalive_visible(self, qtbot):
        assert self._form(qtbot)._spin_keepalive_interval_s.isVisible()


# ---------------------------------------------------------------------------
# Remote Control fields (claude-remote / claude-local only)
# ---------------------------------------------------------------------------


class TestRemoteControl:
    """ConnectionForm exposes a Remote Control checkbox + name override
    for the two Claude profiles. The fields are hidden for SSH/local-shell
    and ``collect_data`` strips them so Pydantic's ``extra='forbid'``
    doesn't reject the dict on those profiles."""

    def test_visible_on_claude_remote(self, qtbot):
        form = ConnectionForm(available_key_ids=_ALL_KEY_IDS)
        qtbot.addWidget(form)
        form.show()
        form.load_data(_CONN_REMOTE)
        assert form._chk_remote_control_enabled.isVisible()
        assert form._btn_setup_remote_control.isVisible()
        assert form._edit_remote_control_name.isVisible()

    def test_visible_on_claude_local(self, qtbot):
        form = ConnectionForm()
        qtbot.addWidget(form)
        form.show()
        form.load_data(_CONN_LOCAL)
        assert form._chk_remote_control_enabled.isVisible()
        assert form._edit_remote_control_name.isVisible()

    def test_hidden_on_ssh_shell(self, qtbot):
        form = ConnectionForm(available_key_ids=_ALL_KEY_IDS)
        qtbot.addWidget(form)
        form.show()
        form.load_data(_CONN_SSH)
        assert not form._chk_remote_control_enabled.isVisible()
        assert not form._edit_remote_control_name.isVisible()

    def test_hidden_on_local_shell(self, qtbot):
        form = ConnectionForm()
        qtbot.addWidget(form)
        form.show()
        form.load_data(_CONN_SHELL)
        assert not form._chk_remote_control_enabled.isVisible()

    def test_collect_emits_rc_fields_on_claude_remote(self, qtbot):
        form = ConnectionForm(available_key_ids=_ALL_KEY_IDS)
        qtbot.addWidget(form)
        form.show()
        data = dict(_CONN_REMOTE)
        data["remote_control_enabled"] = True
        data["remote_control_name"] = "web-1"
        form.load_data(data)
        out = form.collect_data()
        assert out["remote_control_enabled"] is True
        assert out["remote_control_name"] == "web-1"

    def test_collect_strips_rc_fields_on_ssh_shell(self, qtbot):
        """Pydantic's ``extra='forbid'`` would reject these fields on
        SshShellConnection. ``collect_data`` must omit them when the
        current profile doesn't accept them."""
        form = ConnectionForm(available_key_ids=_ALL_KEY_IDS)
        qtbot.addWidget(form)
        form.show()
        form.load_data(_CONN_SSH)
        out = form.collect_data()
        assert "remote_control_enabled" not in out
        assert "remote_control_name" not in out

    def test_empty_name_collected_as_none(self, qtbot):
        """A blank override field means "use connection.name at render"."""
        form = ConnectionForm()
        qtbot.addWidget(form)
        form.show()
        form.load_data(_CONN_LOCAL)
        form._edit_remote_control_name.setText("")
        out = form.collect_data()
        assert out["remote_control_name"] is None

    def test_signal_emitted_on_setup_button_click(self, qtbot):
        form = ConnectionForm(available_key_ids=_ALL_KEY_IDS)
        qtbot.addWidget(form)
        form.show()
        form.load_data(_CONN_REMOTE)
        with qtbot.waitSignal(
            form.remote_control_auth_requested, timeout=500,
        ):
            form._btn_setup_remote_control.click()

    def test_invalid_name_sets_error(self, qtbot):
        form = ConnectionForm(available_key_ids=_ALL_KEY_IDS)
        qtbot.addWidget(form)
        form.show()
        form.load_data(_CONN_REMOTE)
        form._edit_remote_control_name.setText("bad name with spaces")
        form._validate_field("remote_control_name")
        assert form._errors.get("remote_control_name"), form._errors


# ---------------------------------------------------------------------------
# Profile-switch confirmation
# ---------------------------------------------------------------------------


def test_profile_switch_confirm_reject_reverts(qtbot):
    """Rejecting the confirm dialog reverts the radio to the previous profile."""
    form = ConnectionForm(available_key_ids=_ALL_KEY_IDS)
    qtbot.addWidget(form)
    form.show()
    # Start at claude-remote with non-default host
    form.load_data(_CONN_REMOTE)
    assert form.current_profile == "claude-remote"

    # Inject confirm fn that returns False (user clicks No)
    form._confirm_fn = lambda _parent, _title, _msg: False

    # Click the local-shell radio
    form._radios["local-shell"].click()

    # Radio should have reverted back to claude-remote
    assert form.current_profile == "claude-remote"
    assert form._radios["claude-remote"].isChecked()


def test_profile_switch_confirm_accept_clears_fields(qtbot):
    """Accepting the confirm dialog clears the forbidden fields and applies the new profile."""
    form = ConnectionForm(available_key_ids=_ALL_KEY_IDS)
    qtbot.addWidget(form)
    form.show()
    form.load_data(_CONN_REMOTE)
    assert form._edit_host.text() == "dev.example.com"

    # Inject confirm fn that returns True (user clicks Yes)
    form._confirm_fn = lambda _parent, _title, _msg: True

    form._radios["local-shell"].click()

    assert form.current_profile == "local-shell"
    # host should be cleared
    assert form._edit_host.text() == ""


def test_profile_switch_no_confirm_when_default_values(qtbot):
    """No confirmation should appear when switching away from a profile with default values."""
    form = ConnectionForm()
    qtbot.addWidget(form)
    form.show()
    # default profile, all fields empty
    confirm_called = []
    form._confirm_fn = lambda *_: confirm_called.append(True) or False

    form._radios["local-shell"].click()
    # Should have switched without confirmation
    assert not confirm_called
    assert form.current_profile == "local-shell"


def test_profile_switch_no_confirm_during_load_data(qtbot):
    """load_data() must never show the confirmation dialog."""
    form = ConnectionForm(available_key_ids=_ALL_KEY_IDS)
    qtbot.addWidget(form)
    form.show()

    confirm_called = []
    form._confirm_fn = lambda *_: confirm_called.append(True) or False

    form.load_data(_CONN_REMOTE)  # should suppress confirm
    assert not confirm_called


# ---------------------------------------------------------------------------
# Save validation
# ---------------------------------------------------------------------------


def test_save_disabled_when_no_name(qtbot):
    dlg = _make_dlg(qtbot)
    dlg._edit_name.setText("")
    dlg._validate_name()
    assert not dlg._btn_save.isEnabled()


def test_save_disabled_when_invalid_id(qtbot):
    dlg = _make_dlg(qtbot)
    dlg._edit_name.setText("My Conn")
    dlg._edit_id.setText("INVALID ID with spaces")
    dlg._validate_id()
    assert not dlg._btn_save.isEnabled()


def test_save_enabled_with_valid_remote(qtbot):
    dlg = _make_dlg(qtbot, conn_data=_CONN_REMOTE, is_new=False)
    # Fill required fields
    dlg._form._edit_host.setText("host.example.com")
    dlg._form._validate_field("host")
    dlg._form._edit_user.setText("admin")
    dlg._form._validate_field("user")
    dlg._form._edit_project_folder.setText("/opt/app")
    dlg._form._validate_field("project_folder")
    dlg._form._edit_claude_options.setText("--resume")
    dlg._form._validate_field("claude_options")
    dlg._form._combo_identity_file_ref.setCurrentText("key-prod")
    dlg._form._validate_field("identity_file_ref")
    dlg._validate_name()
    dlg._validate_id()
    assert dlg._btn_save.isEnabled()


def test_error_label_shown_for_empty_host(qtbot):
    dlg = _make_dlg(qtbot, conn_data=_CONN_REMOTE, is_new=False)
    form = dlg._form
    form._edit_host.setText("")
    form._validate_field("host")
    assert form._err_host.isVisible()
    assert "required" in form._err_host.text().lower()


def test_host_error_style(qtbot):
    dlg = _make_dlg(qtbot, conn_data=_CONN_REMOTE, is_new=False)
    form = dlg._form
    form._edit_host.setText("")
    form._validate_field("host")
    assert "red" in form._edit_host.styleSheet()


def test_save_button_tooltip_lists_issues(qtbot):
    dlg = _make_dlg(qtbot)
    dlg._edit_name.setText("")
    dlg._validate_name()
    tooltip = dlg._btn_save.toolTip()
    assert tooltip  # Should have some text when not saveable


# ---------------------------------------------------------------------------
# Test Connection dispatch
# ---------------------------------------------------------------------------


def test_test_connection_ssh_calls_factory_for_remote(qtbot):
    """For claude-remote, _on_test_connection calls the ssh_test_factory."""
    calls = []

    def fake_factory(*, host, user, port, identity_file, on_result):
        calls.append({"host": host, "user": user, "port": port})
        on_result(True, "")

    dlg = _make_dlg(
        qtbot, conn_data=_CONN_REMOTE, is_new=False, ssh_test_factory=fake_factory,
        ssh_keys=[_KEY_PROD],
    )
    dlg._form._edit_host.setText("dev.example.com")
    dlg._form._edit_user.setText("ubuntu")
    dlg._on_test_connection()
    assert len(calls) == 1
    assert calls[0]["host"] == "dev.example.com"


def test_test_connection_ssh_calls_factory_for_ssh_shell(qtbot):
    calls = []

    def fake_factory(*, host, user, port, identity_file, on_result):
        calls.append(host)
        on_result(True, "")

    dlg = _make_dlg(
        qtbot, conn_data=_CONN_SSH, is_new=False, ssh_test_factory=fake_factory,
        ssh_keys=[_KEY_PROD],
    )
    dlg._on_test_connection()
    assert calls


def test_test_connection_local_dir_exists(qtbot, tmp_path):
    """For local-shell, test checks path.is_dir()."""
    dlg = _make_dlg(qtbot, conn_data={**_CONN_SHELL, "project_folder": str(tmp_path)}, is_new=False)
    dlg._on_test_connection()
    assert "✓" in dlg._lbl_test_result.text()


def test_test_connection_local_dir_missing(qtbot, tmp_path):
    missing = str(tmp_path / "nonexistent_dir")
    dlg = _make_dlg(qtbot, conn_data={**_CONN_SHELL, "project_folder": missing}, is_new=False)
    dlg._on_test_connection()
    assert "✗" in dlg._lbl_test_result.text()


def test_test_connection_claude_local_uses_path(qtbot, tmp_path):
    dlg = _make_dlg(qtbot, conn_data={**_CONN_LOCAL, "project_folder": str(tmp_path)}, is_new=False)
    dlg._on_test_connection()
    assert "✓" in dlg._lbl_test_result.text()


def test_test_connection_custom_calls_template_service(qtbot):
    """For custom profile, _run_custom_preview calls template_service.render."""
    mock_svc = MagicMock()
    mock_svc.render.return_value = "rendered bash script"

    dlg = _make_dlg(qtbot, conn_data=_CONN_CUSTOM, is_new=False, template_service=mock_svc)
    dlg._on_test_connection()

    mock_svc.render.assert_called_once()
    assert dlg._txt_test_preview.isVisible()
    assert "rendered bash script" in dlg._txt_test_preview.toPlainText()


def test_test_connection_custom_no_template(qtbot):
    """Without template_id, shows warning."""
    dlg = _make_dlg(
        qtbot, conn_data={"id": "c", "launch_profile": "custom", "custom_template_id": ""}
    )
    dlg._form._combo_custom_template_id.setCurrentText("")
    dlg._on_test_connection()
    assert "⚠" in dlg._lbl_test_result.text()


def test_test_connection_ssh_result_success_shows_check(qtbot):
    def factory(*, host, user, port, identity_file, on_result):
        on_result(True, "")

    dlg = _make_dlg(
        qtbot, conn_data=_CONN_REMOTE, is_new=False, ssh_test_factory=factory,
        ssh_keys=[_KEY_PROD],
    )
    dlg._on_test_connection()
    assert "✓" in dlg._lbl_test_result.text()


def test_test_connection_ssh_result_failure_shows_x(qtbot):
    def factory(*, host, user, port, identity_file, on_result):
        on_result(False, "Connection refused")

    dlg = _make_dlg(
        qtbot, conn_data=_CONN_REMOTE, is_new=False, ssh_test_factory=factory,
        ssh_keys=[_KEY_PROD],
    )
    dlg._on_test_connection()
    assert "✗" in dlg._lbl_test_result.text()


# ---------------------------------------------------------------------------
# Member-of label
# ---------------------------------------------------------------------------


def test_member_of_label_two_groups(qtbot):
    """A connection in two groups shows both group names in the Member of line."""
    dlg = _make_dlg(qtbot, conn_data=_CONN_REMOTE, is_new=False)
    text = dlg._lbl_member_of.text()
    # conn-remote is in grp-alpha and grp-beta
    assert "Alpha" in text
    assert "Beta" in text


def test_member_of_label_none(qtbot):
    """A connection in no groups shows (none)."""
    dlg = _make_dlg(
        qtbot,
        conn_data=_CONN_SSH,
        is_new=False,
    )
    # conn-ssh is not in any group in _GROUPS fixture
    text = dlg._lbl_member_of.text()
    assert "none" in text.lower()


def test_member_of_links_emit_signal(qtbot):
    """Clicking a group link emits open_group_editor with the group id."""
    dlg = _make_dlg(qtbot, conn_data=_CONN_REMOTE, is_new=False)
    received = []
    dlg.open_group_editor.connect(received.append)
    dlg._on_group_link_clicked("grp-alpha")
    assert received == ["grp-alpha"]


# ---------------------------------------------------------------------------
# collect_data round-trip
# ---------------------------------------------------------------------------


def test_collect_data_remote(qtbot):
    dlg = _make_dlg(qtbot, conn_data=_CONN_REMOTE, is_new=False)
    data = dlg.get_connection_data()
    assert data["launch_profile"] == "claude-remote"
    assert data["host"] == "dev.example.com"
    assert data["id"] == "conn-remote"
    assert data["name"] == "Remote Conn"


def test_collect_data_local(qtbot):
    dlg = _make_dlg(qtbot, conn_data=_CONN_LOCAL, is_new=False)
    data = dlg.get_connection_data()
    assert data["launch_profile"] == "claude-local"
    assert "host" not in data or data.get("host") is None


def test_collect_data_custom(qtbot):
    dlg = _make_dlg(qtbot, conn_data=_CONN_CUSTOM, is_new=False)
    data = dlg.get_connection_data()
    assert data["launch_profile"] == "custom"
    assert data["custom_template_id"] == "tpl-nspawn"


# ---------------------------------------------------------------------------
# field_visible helper (unit)
# ---------------------------------------------------------------------------


def test_field_visible_host_remote():
    assert _field_visible("host", "claude-remote") is True


def test_field_visible_host_local():
    assert _field_visible("host", "claude-local") is False


def test_field_visible_custom_template_custom():
    assert _field_visible("custom_template_id", "custom") is True


def test_field_visible_custom_template_others():
    for p in ("claude-remote", "claude-local", "ssh-shell", "local-shell"):
        assert _field_visible("custom_template_id", p) is False


def test_field_visible_claude_options_ssh_shell():
    assert _field_visible("claude_options", "ssh-shell") is False


def test_field_visible_claude_options_local_shell():
    assert _field_visible("claude_options", "local-shell") is False


def test_field_visible_env_all_profiles():
    for p in ("claude-remote", "claude-local", "ssh-shell", "local-shell", "custom"):
        assert _field_visible("env", p) is True


class TestPreviewResolvesIdentityKey:
    """The preview must resolve identity_file_ref, not report a false error.

    _run_custom_preview renders through the real TemplateService.  It used to
    call render() without ssh_keys, so the resolver saw no keys at all.  Once
    a chosen-but-unresolvable key became a hard error, that omission turned
    every keyed connection's preview into "Render error" -- for a key that was
    perfectly valid.  The dialog now receives the document's keys.
    """

    @staticmethod
    def _key(tmp_path):
        from cpsm.data.schema import SshKey

        priv = tmp_path / "utility"
        priv.write_text("x", encoding="utf-8")
        (tmp_path / "utility.pub").write_text("x", encoding="utf-8")
        return SshKey(
            id="utility",
            name="Utility",
            type="rsa",
            private_path=str(priv),
            public_path=str(priv) + ".pub",
        )

    def test_preview_pins_when_the_key_resolves(self, qtbot, tmp_path):
        from cpsm.services.template_service import TemplateService

        key = self._key(tmp_path)
        dlg = _make_dlg(qtbot, ssh_keys=[key], template_service=TemplateService())
        dlg._run_custom_preview(
            {
                "id": "c1",
                "name": "Pinned",
                "launch_profile": "ssh-shell",
                "custom_template_id": "tpl-nspawn",
                "host": "192.0.2.44",
                "port": 22,
                "user": "root",
                "identity_file_ref": "utility",
                "project_folder": "/tmp",
            }
        )
        label = dlg._lbl_test_result.text()
        assert "Render error" not in label, label
        assert "IdentitiesOnly=yes" in dlg._txt_test_preview.toPlainText()

    def test_preview_reports_a_key_that_really_is_missing(self, qtbot, tmp_path):
        """The guard must still speak up when the key genuinely is not there."""
        from cpsm.services.template_service import TemplateService

        dlg = _make_dlg(qtbot, ssh_keys=[self._key(tmp_path)],
                        template_service=TemplateService())
        dlg._run_custom_preview(
            {
                "id": "c1",
                "name": "Broken",
                "launch_profile": "ssh-shell",
                "custom_template_id": "tpl-nspawn",
                "host": "192.0.2.44",
                "port": 22,
                "user": "root",
                "identity_file_ref": "imported-default",
                "project_folder": "/tmp",
            }
        )
        assert "Render error" in dlg._lbl_test_result.text()
        assert "imported-default" in dlg._lbl_test_result.text()


# ---------------------------------------------------------------------------
# Phase 2 (cpsm-connection-key-ux): identity-key dropdown population and
# "+ New Key…" wiring.
#
# No test in this section may write to ~/.cpsm.yaml, generate a real SSH
# key, or touch the real ~/.ssh — GenerateKeyDialog and KeyService are
# always injected via the dialog's `generate_key_dialog_cls` / `key_service`
# constructor params.
# ---------------------------------------------------------------------------


def _make_key(key_id: str, *, private_path: str = "~/.ssh/id_ed25519") -> SshKey:
    return SshKey(
        id=key_id,
        name=f"Key {key_id}",
        type="ed25519",
        private_path=private_path,
        public_path=private_path + ".pub",
        created_at=datetime.now(tz=UTC),
    )


class _FakeGenerateKeyDialog:
    """Stand-in for GenerateKeyDialog with the same ctor shape and
    `created_key` property, but that never touches disk or ~/.ssh."""

    _result: SshKey | None = None

    def __init__(self, key_service: Any, default_dir: Any, parent: Any = None) -> None:
        self.key_service = key_service
        self.default_dir = default_dir
        self.parent = parent

    def exec(self) -> int:
        from PySide6.QtWidgets import QDialog

        return int(QDialog.DialogCode.Accepted)

    @property
    def created_key(self) -> SshKey | None:
        return type(self)._result


def _fake_dialog_cls_returning(key: SshKey | None) -> type[_FakeGenerateKeyDialog]:
    """Build a fake GenerateKeyDialog class whose `created_key` is *key*
    (None simulates the user cancelling)."""

    return type("_FakeGenerateKeyDialogBound", (_FakeGenerateKeyDialog,), {"_result": key})


class TestIdentityKeyDropdownPopulation:
    """This is the test that would have caught the original
    'the identity-key dropdown is always empty' bug: populate_keys() was
    defined but never called from anywhere."""

    def test_combo_contains_exactly_the_documents_keys(self, qtbot):
        keys = [_make_key("key-a"), _make_key("key-b"), _make_key("key-c")]
        dlg = _make_dlg(qtbot, ssh_keys=keys)
        qtbot.addWidget(dlg)
        combo = dlg._form._combo_identity_file_ref
        items = [combo.itemText(i) for i in range(combo.count())]
        assert items == ["key-a", "key-b", "key-c"]

    def test_dangling_identity_ref_still_shown_not_blanked(self, qtbot):
        """A saved connection referencing a key id absent from ssh_keys must
        keep showing that id — not silently blank out — so the user can see
        and correct it."""
        keys = [_make_key("key-a")]
        conn_data = dict(_CONN_REMOTE)
        conn_data["identity_file_ref"] = "key-does-not-exist"
        dlg = _make_dlg(qtbot, conn_data=conn_data, is_new=False, ssh_keys=keys)
        combo = dlg._form._combo_identity_file_ref
        assert combo.currentText() == "key-does-not-exist"

    def test_populate_templates_is_also_called(self, qtbot):
        """Same latent bug, same fix shape: available_template_ids must
        reach the custom_template_id combo."""
        dlg = _make_dlg(qtbot)
        combo = dlg._form._combo_custom_template_id
        items = [combo.itemText(i) for i in range(combo.count())]
        assert items == _ALL_TEMPLATE_IDS


class TestNewKeyButton:
    """'+ New Key…' used to do nothing at all — no .clicked.connect() call
    existed anywhere in the codebase."""

    def test_click_generates_appends_repopulates_and_selects(self, qtbot):
        new_key = _make_key("key-fresh")
        dlg = _make_dlg(
            qtbot,
            ssh_keys=[_make_key("key-a")],
            generate_key_dialog_cls=_fake_dialog_cls_returning(new_key),
            key_service=MagicMock(),
        )
        btn = dlg.findChild(QPushButton, "btn_new_key")
        assert btn is not None
        btn.click()

        assert [k.id for k in dlg._ssh_keys] == ["key-a", "key-fresh"]

        combo = dlg._form._combo_identity_file_ref
        items = [combo.itemText(i) for i in range(combo.count())]
        assert items == ["key-a", "key-fresh"]
        assert combo.currentText() == "key-fresh"

        # Exposed so main_window can persist it into doc.ssh_keys.
        assert [k.id for k in dlg.new_ssh_keys] == ["key-fresh"]

    def test_cancel_changes_nothing(self, qtbot):
        dlg = _make_dlg(
            qtbot,
            ssh_keys=[_make_key("key-a")],
            generate_key_dialog_cls=_fake_dialog_cls_returning(None),
            key_service=MagicMock(),
        )
        combo = dlg._form._combo_identity_file_ref
        before_items = [combo.itemText(i) for i in range(combo.count())]
        before_text = combo.currentText()

        btn = dlg.findChild(QPushButton, "btn_new_key")
        btn.click()

        after_items = [combo.itemText(i) for i in range(combo.count())]
        assert after_items == before_items
        assert combo.currentText() == before_text
        assert dlg.new_ssh_keys == []
        assert [k.id for k in dlg._ssh_keys] == ["key-a"]

    def test_duplicate_key_id_not_appended_twice(self, qtbot):
        dlg = _make_dlg(
            qtbot,
            ssh_keys=[_make_key("key-a")],
            generate_key_dialog_cls=_fake_dialog_cls_returning(_make_key("key-a")),
            key_service=MagicMock(),
        )
        btn = dlg.findChild(QPushButton, "btn_new_key")
        btn.click()

        assert [k.id for k in dlg._ssh_keys].count("key-a") == 1
        assert dlg.new_ssh_keys == []


class TestNewKeyPersistedOnSave:
    """A key created inside the New Connection dialog must not be lost when
    the connection is saved — main_window must pull dlg.new_ssh_keys into
    doc.ssh_keys before persisting."""

    def test_new_key_lands_in_document_and_is_not_lost_on_save(self, qtbot):
        """Focused equivalent of a full main_window test: exercises the real
        `_open_connection_editor` method (unbound) against a minimal stand-in
        object, with the dialog and the persistence side-effects
        (`load_document` / `_save_document`) stubbed out — this isolates the
        one thing under test: does a key exposed via `new_ssh_keys` get
        merged into `doc.ssh_keys` before anything is saved."""
        from cpsm.data.schema import CpsmDocument
        from cpsm.ui.main_window import MainWindow

        new_key = _make_key("key-brand-new")

        class _StubDialog:
            DialogCode = ConnectionEditorDialog.DialogCode

            def __init__(self, *args, **kwargs):
                pass

            def setObjectName(self, name):
                pass

            def exec(self):
                return int(self.DialogCode.Accepted)

            def get_connection_data(self):
                return {
                    "id": "conn-new",
                    "name": "New Conn",
                    "launch_profile": "claude-remote",
                    "host": "h",
                    "port": 22,
                    "user": "u",
                    "identity_file_ref": "key-brand-new",
                    "project_folder": "/tmp",
                    "claude_options": "--resume",
                }

            @property
            def new_ssh_keys(self):
                return [new_key]

        class _Stand:
            """Minimal object exposing only what _open_connection_editor
            touches: self._document, self.load_document, self._save_document.
            _make_conn_editor_kwargs / _reconstruct_connection are borrowed
            unchanged from MainWindow — they are pure functions of
            self._document, so real MainWindow behaviour is preserved."""

            _make_conn_editor_kwargs = MainWindow._make_conn_editor_kwargs
            _reconstruct_connection = MainWindow._reconstruct_connection

            def __init__(self):
                self._document = CpsmDocument()
                self.load_document_calls: list[Any] = []
                self.save_document_calls = 0

            def load_document(self, doc):
                self.load_document_calls.append(doc)

            def _save_document(self):
                self.save_document_calls += 1

        stand = _Stand()

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "cpsm.ui.dialogs.connection_editor.ConnectionEditorDialog",
                _StubDialog,
            )
            MainWindow._open_connection_editor(stand, None)

        assert any(k.id == "key-brand-new" for k in stand._document.ssh_keys)
        assert any(c.id == "conn-new" for c in stand._document.connections)
        assert stand.save_document_calls == 1


# ---------------------------------------------------------------------------
# Claim 4: exactly one source of truth for the identity-key ids
# ---------------------------------------------------------------------------


class TestKeyIdSourceOfTruth:
    """`ssh_keys` wins; the two lists are never merged.

    The original "dropdown always empty" bug was born of two parallel key
    lists that could disagree, so "there is exactly one source of truth" is a
    load-bearing claim rather than a stylistic one. Until this test existed it
    was true only because the if/else happened to be written correctly — a
    later refactor merging the two would have regressed it silently, with the
    combo showing ids the document does not contain, which is precisely the
    dangling-reference failure this pipeline exists to remove.
    """

    def test_ssh_keys_wins_and_available_key_ids_is_not_merged(self, qtbot) -> None:
        keys = [
            SshKey(
                id="key-from-document",
                name="From document",
                private_path="~/.ssh/doc_key",
                public_path="~/.ssh/doc_key.pub",
            )
        ]
        dlg = ConnectionEditorDialog(
            connection_data=None,
            groups=_GROUPS,
            # Deliberately disjoint from `keys` — if these ever appear in the
            # combo, the two sources have been merged.
            available_key_ids=["legacy-id-a", "legacy-id-b"],
            available_template_ids=_ALL_TEMPLATE_IDS,
            available_connection_ids=_ALL_CONN_IDS,
            ssh_keys=keys,
            is_new=True,
        )
        qtbot.addWidget(dlg)

        combo = dlg._form._combo_identity_file_ref
        shown = [combo.itemText(i) for i in range(combo.count())]

        assert shown == ["key-from-document"], (
            "ssh_keys must be the sole source of truth for the identity combo; "
            f"got {shown!r}"
        )
        assert "legacy-id-a" not in shown
        assert "legacy-id-b" not in shown

    def test_available_key_ids_still_used_when_no_ssh_keys_supplied(self, qtbot) -> None:
        """The fallback must stay live — other call sites still rely on it."""
        dlg = ConnectionEditorDialog(
            connection_data=None,
            groups=_GROUPS,
            available_key_ids=["legacy-id-a", "legacy-id-b"],
            available_template_ids=_ALL_TEMPLATE_IDS,
            available_connection_ids=_ALL_CONN_IDS,
            ssh_keys=None,
            is_new=True,
        )
        qtbot.addWidget(dlg)

        combo = dlg._form._combo_identity_file_ref
        shown = [combo.itemText(i) for i in range(combo.count())]

        assert shown == ["legacy-id-a", "legacy-id-b"]


# ---------------------------------------------------------------------------
# Phase 3 (cpsm-connection-key-ux):
#   Part A — Test Connection / Remote Control resolve identity_file_ref via
#            self._ssh_keys instead of treating the id slug as a path.
#   Part B — key discovery + probe-based auto-pin when no usable key is
#            pinned.
#
# No test in this section performs a live network probe. KeyDiscoveryService
# and the probe task are always injected (key_discovery_service /
# key_probe_factory) — see the autouse _no_real_key_probing guard above,
# which fails loudly if a test forgets.
# ---------------------------------------------------------------------------


class TestResolveIdentityPath:
    """_resolve_identity_path: the unit the whole Phase 3 fix hinges on."""

    def test_valid_ref_resolves_to_private_path(self, qtbot) -> None:
        key = SshKey(
            id="key-a",
            name="Key A",
            private_path="/home/user/.ssh/id_ed25519_a",
            public_path="/home/user/.ssh/id_ed25519_a.pub",
        )
        dlg = _make_dlg(qtbot, ssh_keys=[key])
        resolved = dlg._resolve_identity_path("key-a")
        assert resolved == Path("/home/user/.ssh/id_ed25519_a").expanduser()

    def test_valid_ref_expands_tilde(self, qtbot) -> None:
        key = SshKey(
            id="key-a", name="Key A",
            private_path="~/.ssh/id_ed25519_a", public_path="~/.ssh/id_ed25519_a.pub",
        )
        dlg = _make_dlg(qtbot, ssh_keys=[key])
        resolved = dlg._resolve_identity_path("key-a")
        assert "~" not in str(resolved)

    def test_dangling_ref_raises_identity_key_not_found_error(self, qtbot) -> None:
        dlg = _make_dlg(qtbot, ssh_keys=[])
        with pytest.raises(IdentityKeyNotFoundError, match="key-does-not-exist"):
            dlg._resolve_identity_path("key-does-not-exist")

    def test_ref_matching_id_but_blank_private_path_also_raises(self, qtbot) -> None:
        """Keys off the resolved PATH, not the id match — a blanked-out
        entry is the same failure as a ref matching nothing at all."""
        key = SshKey(id="key-a", name="Key A", private_path="", public_path="")
        dlg = _make_dlg(qtbot, ssh_keys=[key])
        with pytest.raises(IdentityKeyNotFoundError):
            dlg._resolve_identity_path("key-a")


class TestTestConnectionResolvesRefBeforeDiscovery:
    """A valid ref must resolve straight through — no discovery involved."""

    def test_valid_ref_passes_resolved_path_to_factory(self, qtbot) -> None:
        key = SshKey(
            id="key-prod", name="Prod",
            private_path="/home/user/.ssh/prodkey", public_path="/home/user/.ssh/prodkey.pub",
        )
        calls = []

        def fake_factory(*, host, user, port, identity_file, on_result):
            calls.append(identity_file)
            on_result(True, "")

        discovery = MagicMock()
        dlg = _make_dlg(
            qtbot, conn_data=_CONN_REMOTE, is_new=False,
            ssh_keys=[key], ssh_test_factory=fake_factory,
            key_discovery_service=discovery,
        )
        dlg._on_test_connection()

        assert calls == [Path("/home/user/.ssh/prodkey")]
        discovery.discover.assert_not_called()


class TestNoRefOrDanglingRefFallsThroughToDiscovery:
    def test_no_ref_triggers_discovery(self, qtbot) -> None:
        discovery = MagicMock()
        discovery.discover.return_value = []
        probe_factory = MagicMock()

        conn = {**_CONN_REMOTE, "identity_file_ref": None}
        dlg = _make_dlg(
            qtbot, conn_data=conn, is_new=False, ssh_keys=[],
            key_discovery_service=discovery, key_probe_factory=probe_factory,
        )
        dlg._form._combo_identity_file_ref.setCurrentText("")
        dlg._on_test_connection()

        discovery.discover.assert_called_once()
        _, kwargs = discovery.discover.call_args
        assert discovery.discover.call_args[0][0] == "dev.example.com"

    def test_dangling_ref_triggers_discovery(self, qtbot) -> None:
        discovery = MagicMock()
        discovery.discover.return_value = []

        dlg = _make_dlg(
            qtbot, conn_data=_CONN_REMOTE, is_new=False, ssh_keys=[],
            key_discovery_service=discovery,
        )
        dlg._on_test_connection()

        discovery.discover.assert_called_once()

    def test_no_candidates_shows_clear_message_and_pins_nothing(self, qtbot) -> None:
        discovery = MagicMock()
        discovery.discover.return_value = []
        probe_factory = MagicMock()

        dlg = _make_dlg(
            qtbot, conn_data=_CONN_REMOTE, is_new=False, ssh_keys=[],
            key_discovery_service=discovery, key_probe_factory=probe_factory,
        )
        dlg._on_test_connection()

        probe_factory.assert_not_called()
        assert "No" in dlg._lbl_test_result.text()
        assert dlg._ssh_keys == []
        assert dlg.new_ssh_keys == []


def _make_candidate(
    tmp_path: Path,
    name: str = "found_key",
    *,
    source: str = "pub_comment_host",
    score: int = 60,
    comment: str = "",
) -> KeyCandidate:
    priv = tmp_path / name
    return KeyCandidate(
        private_path=priv,
        public_path=priv.with_name(priv.name + ".pub"),
        comment=comment,
        source=source,
        score=score,
    )


class TestDiscoveryProbeOrdering:
    """The probe factory is handed ALL ranked candidates; ordering and
    "stop at first success" are KeyDiscoveryProbeTask's job (covered in
    tests/workers/test_ssh_worker.py) — here we only check the dialog wires
    discovery's ranked output straight through, unmodified."""

    def test_candidates_passed_through_in_ranked_order(self, qtbot, tmp_path) -> None:
        c1 = _make_candidate(tmp_path, "key1", score=100)
        c2 = _make_candidate(tmp_path, "key2", score=60)
        discovery = MagicMock()
        discovery.discover.return_value = [c1, c2]
        captured = {}

        def probe_factory(*, host, user, port, candidates, on_result):
            captured["candidates"] = candidates

        dlg = _make_dlg(
            qtbot, conn_data=_CONN_REMOTE, is_new=False, ssh_keys=[],
            key_discovery_service=discovery, key_probe_factory=probe_factory,
        )
        dlg._on_test_connection()

        assert captured["candidates"] == [c1, c2]


class TestAutoPinConfirmFlow:
    """Confirm/decline around a successfully-probed discovered key."""

    def test_confirming_pins_key_and_creates_ssh_keys_entry(self, qtbot, tmp_path) -> None:
        candidate = _make_candidate(
            tmp_path, "utility", source="pub_comment_user_host", comment="root@192.0.2.44"
        )
        discovery = MagicMock()
        discovery.discover.return_value = [candidate]

        def probe_factory(*, host, user, port, candidates, on_result):
            on_result(candidates[0])

        confirmed = []

        def confirm_fn(parent, cand):
            confirmed.append(cand)
            return True

        dlg = _make_dlg(
            qtbot, conn_data=_CONN_REMOTE, is_new=False, ssh_keys=[],
            key_discovery_service=discovery, key_probe_factory=probe_factory,
            confirm_pin_fn=confirm_fn,
        )
        dlg._on_test_connection()

        assert confirmed == [candidate]
        assert len(dlg._ssh_keys) == 1
        pinned = dlg._ssh_keys[0]
        assert str(pinned.private_path) == str(candidate.private_path)
        assert dlg.new_ssh_keys == [pinned]
        assert dlg._form._combo_identity_file_ref.currentText() == pinned.id
        assert "✓" in dlg._lbl_test_result.text()
        assert pinned.id in dlg._lbl_test_result.text()

    def test_pinned_rsa_key_is_recorded_as_rsa_not_ed25519(self, qtbot, tmp_path) -> None:
        """The pinned entry must carry the key's REAL algorithm.

        Testing _infer_key_type in isolation is not enough: it passes just as
        happily when _pin_discovered_key ignores it and hardcodes a type,
        which is what the code did before. Verified by injection — reverting
        the pin site to type="ed25519" leaves the isolated inference tests
        green and turns only this one red. This is the test that actually
        pins the wiring down.
        """
        candidate = _make_candidate(
            tmp_path, "legacy_host_key",
            source="pub_comment_user_host", comment="root@192.0.2.44",
        )
        assert candidate.public_path is not None
        candidate.public_path.write_text(
            "ssh-rsa AAAAB3NzaC1yc2EAAAAfake root@192.0.2.44\n", encoding="utf-8"
        )

        discovery = MagicMock()
        discovery.discover.return_value = [candidate]

        def probe_factory(*, host, user, port, candidates, on_result):
            on_result(candidates[0])

        dlg = _make_dlg(
            qtbot, conn_data=_CONN_REMOTE, is_new=False, ssh_keys=[],
            key_discovery_service=discovery, key_probe_factory=probe_factory,
            confirm_pin_fn=lambda parent, cand: True,
        )
        dlg._on_test_connection()

        assert len(dlg._ssh_keys) == 1
        assert dlg._ssh_keys[0].type == "rsa", (
            "a discovered RSA key was recorded as "
            f"{dlg._ssh_keys[0].type!r} — the pin site is not using "
            "_infer_key_type"
        )

    def test_declining_leaves_document_and_form_untouched(self, qtbot, tmp_path) -> None:
        candidate = _make_candidate(tmp_path, "utility")
        discovery = MagicMock()
        discovery.discover.return_value = [candidate]

        def probe_factory(*, host, user, port, candidates, on_result):
            on_result(candidates[0])

        dlg = _make_dlg(
            qtbot, conn_data=_CONN_REMOTE, is_new=False, ssh_keys=[],
            key_discovery_service=discovery, key_probe_factory=probe_factory,
            confirm_pin_fn=lambda parent, cand: False,
        )

        before_ssh_keys = list(dlg._ssh_keys)
        before_combo_text = dlg._form._combo_identity_file_ref.currentText()
        before_new_keys = list(dlg.new_ssh_keys)

        dlg._on_test_connection()

        assert dlg._ssh_keys == before_ssh_keys
        assert dlg.new_ssh_keys == before_new_keys
        assert dlg._form._combo_identity_file_ref.currentText() == before_combo_text
        assert "declined" in dlg._lbl_test_result.text().lower()

    def test_no_candidate_authenticates_shows_message_and_pins_nothing(
        self, qtbot, tmp_path
    ) -> None:
        candidate = _make_candidate(tmp_path, "utility")
        discovery = MagicMock()
        discovery.discover.return_value = [candidate]
        confirm_fn = MagicMock(return_value=True)

        def probe_factory(*, host, user, port, candidates, on_result):
            on_result(None)

        dlg = _make_dlg(
            qtbot, conn_data=_CONN_REMOTE, is_new=False, ssh_keys=[],
            key_discovery_service=discovery, key_probe_factory=probe_factory,
            confirm_pin_fn=confirm_fn,
        )
        dlg._on_test_connection()

        confirm_fn.assert_not_called()
        assert dlg._ssh_keys == []
        assert dlg.new_ssh_keys == []
        assert "✗" in dlg._lbl_test_result.text()

    def test_candidate_already_in_ssh_keys_is_not_duplicated(self, qtbot, tmp_path) -> None:
        candidate = _make_candidate(tmp_path, "utility")
        existing_key = SshKey(
            id="already-here", name="Already here",
            private_path=str(candidate.private_path),
            public_path=str(candidate.public_path),
        )
        discovery = MagicMock()
        discovery.discover.return_value = [candidate]

        def probe_factory(*, host, user, port, candidates, on_result):
            on_result(candidates[0])

        dlg = _make_dlg(
            qtbot, conn_data=_CONN_REMOTE, is_new=False, ssh_keys=[existing_key],
            key_discovery_service=discovery, key_probe_factory=probe_factory,
            confirm_pin_fn=lambda parent, cand: True,
        )
        dlg._on_test_connection()

        assert [k.id for k in dlg._ssh_keys] == ["already-here"]
        assert dlg.new_ssh_keys == []
        assert dlg._form._combo_identity_file_ref.currentText() == "already-here"

    def test_default_confirm_dialog_has_stable_object_and_accessible_names(
        self, qtbot, tmp_path, monkeypatch
    ) -> None:
        """When confirm_pin_fn is NOT injected, the real QMessageBox must
        carry a stable objectName + accessible name/description (automation
        friendliness for pywinauto/FlaUI on native UI)."""
        candidate = _make_candidate(tmp_path, "utility", comment="root@192.0.2.44")
        discovery = MagicMock()
        discovery.discover.return_value = [candidate]

        def probe_factory(*, host, user, port, candidates, on_result):
            on_result(candidates[0])

        captured_boxes = []
        real_exec = QMessageBox.exec

        def fake_exec(self):
            captured_boxes.append(self)
            return int(QMessageBox.StandardButton.Yes)

        monkeypatch.setattr(QMessageBox, "exec", fake_exec)

        dlg = _make_dlg(
            qtbot, conn_data=_CONN_REMOTE, is_new=False, ssh_keys=[],
            key_discovery_service=discovery, key_probe_factory=probe_factory,
        )
        dlg._on_test_connection()

        assert len(captured_boxes) == 1
        box = captured_boxes[0]
        assert box.objectName() == "dlg_confirm_pin_discovered_key"
        assert box.accessibleName()
        assert box.accessibleDescription()
        assert "root@192.0.2.44" in box.text()


class TestRemoteControlKeyPathResolvesViaSshKeys:
    """Part A, second half: the stale 'the editor only has access to key
    ids, not paths' comment is false — self._ssh_keys is right there."""

    def _install_fake_rc_dialog(self, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
        captured: dict[str, Any] = {}

        class _FakeRemoteControlAuthDialog:
            def __init__(self, *, host, user, port, key_path, parent=None):
                captured["host"] = host
                captured["user"] = user
                captured["port"] = port
                captured["key_path"] = key_path
                self.authenticated = False

            def exec(self) -> int:
                return 0

        monkeypatch.setattr(
            "cpsm.ui.dialogs.remote_control_auth.RemoteControlAuthDialog",
            _FakeRemoteControlAuthDialog,
        )
        return captured

    def test_resolves_pinned_key_to_its_private_path(self, qtbot, monkeypatch) -> None:
        captured = self._install_fake_rc_dialog(monkeypatch)
        key = SshKey(
            id="key-prod", name="Prod",
            private_path="/home/user/.ssh/prodkey", public_path="/home/user/.ssh/prodkey.pub",
        )
        dlg = _make_dlg(qtbot, conn_data=_CONN_REMOTE, is_new=False, ssh_keys=[key])
        dlg._on_remote_control_auth_requested()

        assert captured["key_path"] == str(Path("/home/user/.ssh/prodkey"))

    def test_dangling_ref_falls_back_to_empty_string(self, qtbot, monkeypatch) -> None:
        captured = self._install_fake_rc_dialog(monkeypatch)
        dlg = _make_dlg(qtbot, conn_data=_CONN_REMOTE, is_new=False, ssh_keys=[])
        dlg._on_remote_control_auth_requested()

        assert captured["key_path"] == ""

    def test_no_ref_at_all_falls_back_to_empty_string(self, qtbot, monkeypatch) -> None:
        captured = self._install_fake_rc_dialog(monkeypatch)
        conn = {**_CONN_REMOTE, "identity_file_ref": None}
        dlg = _make_dlg(qtbot, conn_data=conn, is_new=False, ssh_keys=[])
        dlg._form._combo_identity_file_ref.setCurrentText("")
        dlg._on_remote_control_auth_requested()

        assert captured["key_path"] == ""


class TestNewKeyFallbackIsGuarded:
    """The autouse guard must actually intercept the un-injected path."""

    def test_uninjected_new_key_click_is_caught_not_silently_real(self, qtbot) -> None:
        dlg = ConnectionEditorDialog(
            connection_data=None,
            groups=_GROUPS,
            available_key_ids=[],
            available_template_ids=_ALL_TEMPLATE_IDS,
            available_connection_ids=_ALL_CONN_IDS,
            ssh_keys=[],
            is_new=True,
            # key_service / generate_key_dialog_cls deliberately NOT injected.
        )
        qtbot.addWidget(dlg)

        with pytest.raises(AssertionError, match="real KeyService"):
            dlg._on_new_key_requested()


# ---------------------------------------------------------------------------
# Discovered-key algorithm inference
# ---------------------------------------------------------------------------


class TestDiscoveredKeyTypeInference:
    """A discovered key must be recorded with its real algorithm.

    This was hardcoded to "ed25519" for every discovered key. That is the
    wrong default for precisely the population this feature targets: the
    reason a host needs key discovery at all is usually that its working key
    is an older one, and older means quite likely RSA. Writing "ed25519" into
    the user's config for an RSA key is a quiet falsehood in their data.

    The .pub file names its own algorithm in field 0, so that is preferred;
    the filename is the fallback. The private key is never read.
    """

    @staticmethod
    def _candidate(tmp_path, name: str, pub_first_field: str | None):
        from cpsm.services.key_discovery import KeyCandidate

        private_path = tmp_path / name
        private_path.write_text("PRIVATE - MUST NOT BE READ\n", encoding="utf-8")
        public_path = None
        if pub_first_field is not None:
            public_path = tmp_path / f"{name}.pub"
            public_path.write_text(
                f"{pub_first_field} AAAAB3NzaC1fake root@192.0.2.44\n",
                encoding="utf-8",
            )
        return KeyCandidate(
            private_path=private_path,
            public_path=public_path,
            comment="root@192.0.2.44",
            source="pub_comment_user_host",
            score=80,
        )

    @pytest.mark.parametrize(
        ("pub_first_field", "expected"),
        [
            ("ssh-ed25519", "ed25519"),
            ("ssh-rsa", "rsa"),
            ("ecdsa-sha2-nistp256", "ecdsa"),
        ],
    )
    def test_algorithm_read_from_public_key(
        self, tmp_path, pub_first_field: str, expected: str
    ) -> None:
        candidate = self._candidate(tmp_path, "somekey", pub_first_field)
        assert ConnectionEditorDialog._infer_key_type(candidate) == expected

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("id_rsa", "rsa"),
            ("id_ecdsa", "ecdsa"),
            ("utility", "ed25519"),
        ],
    )
    def test_falls_back_to_filename_when_no_public_key(
        self, tmp_path, name: str, expected: str
    ) -> None:
        candidate = self._candidate(tmp_path, name, None)
        assert ConnectionEditorDialog._infer_key_type(candidate) == expected

    def test_unreadable_public_key_falls_back_without_raising(self, tmp_path) -> None:
        candidate = self._candidate(tmp_path, "id_rsa", "ssh-rsa")
        assert candidate.public_path is not None
        candidate.public_path.chmod(0o000)
        try:
            assert ConnectionEditorDialog._infer_key_type(candidate) == "rsa"
        finally:
            candidate.public_path.chmod(0o644)

    def test_private_key_is_never_opened_while_inferring(
        self, tmp_path, monkeypatch
    ) -> None:
        """Inference must not read private key material to decide a type."""
        candidate = self._candidate(tmp_path, "id_rsa", "ssh-rsa")

        opened: list[Path] = []
        original_open = Path.open

        def recording_open(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            opened.append(self)
            return original_open(self, *args, **kwargs)

        monkeypatch.setattr(Path, "open", recording_open)
        ConnectionEditorDialog._infer_key_type(candidate)

        assert opened, "recorder captured nothing — it can no longer see reads"
        for path in opened:
            assert path.name.endswith(".pub"), f"private key opened: {path}"


# ---------------------------------------------------------------------------
# Claim 8: the no-live-probe guard must itself be load-bearing
# ---------------------------------------------------------------------------


class TestKeyProbeFallbackIsGuarded:
    """The autouse `_no_real_key_probing` guard must demonstrably fire.

    The guard existed and worked, but nothing depended on it: flipping its
    ``autouse=True`` to ``False`` left every test in this file green, so it
    could have been deleted or quietly broken and nobody would learn of it
    until a test reached the network — against a production host, using the
    developer's real keys.

    Note the two halves of the guard surface differently, which is why there
    are two tests rather than one:

      * the discovery half raises out of ``_run_discovery_and_probe``
        directly, because ``KeyDiscoveryService()`` is constructed OUTSIDE
        the method's try/except;
      * the probe half is constructed INSIDE a ``except Exception`` block
        that turns any failure into a red result label, so its AssertionError
        never propagates. Asserting on the label is therefore the honest
        mechanism for that half — it still proves the real task was
        intercepted and no ssh subprocess ran.

    Every host used here is ``.invalid`` (RFC 2606 reserved, guaranteed not
    to resolve) so that even a total failure of the guard cannot reach a
    real machine.
    """

    def test_uninjected_discovery_is_caught_not_silently_real(self, qtbot) -> None:
        dlg = _make_dlg(
            qtbot, conn_data=_CONN_REMOTE, is_new=False, ssh_keys=[],
            # key_discovery_service / key_probe_factory deliberately NOT injected.
        )
        with pytest.raises(AssertionError, match="real KeyDiscoveryService"):
            dlg._run_discovery_and_probe(
                host="no-such-host.invalid", user="root", port=22
            )

    def test_uninjected_probe_task_is_caught_not_silently_real(
        self, qtbot, tmp_path
    ) -> None:
        """Discovery injected, probe not — the probe half must intercept.

        Without this, the test above would pass on the discovery guard alone
        and the probe-task half of the fixture would go entirely unexercised
        — and that is the half that would open a network connection.
        """
        candidate = _make_candidate(tmp_path, "utility")
        discovery = MagicMock()
        discovery.discover.return_value = [candidate]

        dlg = _make_dlg(
            qtbot, conn_data=_CONN_REMOTE, is_new=False, ssh_keys=[],
            key_discovery_service=discovery,
            # key_probe_factory deliberately NOT injected.
        )
        dlg._run_discovery_and_probe(
            host="no-such-host.invalid", user="root", port=22
        )

        assert "real KeyDiscoveryProbeTask" in dlg._lbl_test_result.text(), (
            "the probe guard did not intercept — a real ssh subprocess would "
            f"have been launched; label said: {dlg._lbl_test_result.text()!r}"
        )


# ---------------------------------------------------------------------------
# Claim 10: a probe outliving its dialog must not crash
# ---------------------------------------------------------------------------


class TestProbeResultAfterDialogGone:
    """A probe result arriving after the dialog closed must return quietly.

    Probing is blocking network I/O on a QThreadPool thread with a timeout
    per candidate, so a user closing the editor mid-probe is not an edge
    case — it is the normal way to react to a slow probe. The handler guards
    with isVisible()/_closed and catches RuntimeError for an already-deleted
    C++ object, but nothing asserted any of it: the guard was copied from the
    pre-existing test path and trusted by inspection.
    """

    def test_closed_dialog_ignores_a_late_probe_result(self, qtbot, tmp_path) -> None:
        candidate = _make_candidate(tmp_path, "utility")
        confirmed: list[Any] = []

        def confirm_fn(parent, cand):
            confirmed.append(cand)
            return True

        dlg = _make_dlg(
            qtbot, conn_data=_CONN_REMOTE, is_new=False, ssh_keys=[],
            confirm_pin_fn=confirm_fn,
        )
        dlg.close()
        assert dlg._closed is True

        # Must not raise, must not prompt, must not pin.
        dlg._on_key_probe_result(candidate, "192.0.2.44", "root", 22)

        assert confirmed == [], "a closed dialog still prompted the user"
        assert dlg._ssh_keys == []
        assert dlg.new_ssh_keys == []

    def test_deleted_dialog_ignores_a_late_probe_result(self, qtbot, tmp_path) -> None:
        """The harder case: the underlying C++ object is already gone.

        ``isVisible()`` then raises RuntimeError rather than returning False,
        which is the specific reason the handler wraps its guard in
        try/except. Without this test that except branch is never reached by
        the suite and could be deleted silently — and the crash it prevents
        would land on a background thread, where it is hardest to diagnose.
        """
        from shiboken6 import Shiboken

        candidate = _make_candidate(tmp_path, "utility")
        confirmed: list[Any] = []

        def confirm_fn(parent, cand):
            confirmed.append(cand)
            return True

        dlg = _make_dlg(
            qtbot, conn_data=_CONN_REMOTE, is_new=False, ssh_keys=[],
            confirm_pin_fn=confirm_fn,
        )
        handler = dlg._on_key_probe_result
        Shiboken.delete(dlg)

        # The C++ side is gone; the bound handler is all that survives.
        handler(candidate, "192.0.2.44", "root", 22)

        assert confirmed == [], "a deleted dialog still prompted the user"

    def test_none_candidate_reports_no_key_found(self, qtbot) -> None:
        """The no-candidate-authenticated path still reports, when open."""
        dlg = _make_dlg(qtbot, conn_data=_CONN_REMOTE, is_new=False, ssh_keys=[])
        dlg._on_key_probe_result(None, "192.0.2.44", "root", 22)
        assert "No working SSH key found" in dlg._lbl_test_result.text()


# ---------------------------------------------------------------------------
# Phase 4 (cpsm-connection-key-ux): end-to-end journeys.
#
# Per-unit tests above already cover each seam in isolation (dropdown
# population, "+ New Key…" wiring, identity resolution, discovery+probe,
# confirm-pin). What they cannot catch is a break BETWEEN phases — e.g. the
# new-key handler appending to the wrong list, or the combo selection not
# actually feeding get_connection_data(), or the persisted document ending
# up with the connection but not the key it now references. These tests
# chain the real, non-mocked dialog methods across that boundary.
#
# Honesty about coverage: `ssh_test_factory` / `key_probe_factory` are still
# injected fakes here — no test in this suite may launch a real ssh
# subprocess or touch the real ~/.ssh (see the autouse guards above and the
# pipeline's hard constraints). So "Test Connection succeeds" below proves
# the dialog correctly RESOLVES a ref and DISPATCHES to whatever performs
# the network probe, with the right arguments — not that a real ssh
# roundtrip returns 0. The mock-to-mock boundary is exactly the seam between
# `_resolve_identity_path`/`_run_discovery_and_probe` (real, exercised code)
# and the actual `ssh` subprocess (never exercised here, by design and by
# the pipeline's own no-live-network constraint) — that boundary is covered
# separately, and only under a real ssh binary, in
# tests/workers/test_ssh_worker.py.
# ---------------------------------------------------------------------------


class TestEndToEndNewConnectionNewKeyTestConnection:
    """Journey: create a connection -> "+ New Key…" -> see it in the
    dropdown -> Test Connection resolves and uses that exact key -> the
    dialog exposes it for persistence."""

    def test_full_journey_generates_selects_and_tests_new_key(self, qtbot) -> None:
        new_key = _make_key("key-journey", private_path="/home/user/.ssh/id_journey")

        resolved_paths_used: list[Path] = []

        def fake_ssh_factory(*, host, user, port, identity_file, on_result):
            # This is the exact boundary get_connection_data()'s ref must
            # cross to reach a real probe in production: assert the dialog
            # handed us the RESOLVED private_path of the key just generated,
            # not the bare id slug and not some other key.
            resolved_paths_used.append(identity_file)
            on_result(True, "")

        dlg = _make_dlg(
            qtbot,
            conn_data=None,
            is_new=True,
            # Seed with a pre-existing key so the combo is NOT a
            # single-item list — otherwise Qt auto-selects the sole item
            # and the "selection actually happened" assertion below would
            # pass even if `_on_new_key_requested` forgot to call
            # setCurrentText explicitly.
            ssh_keys=[_make_key("key-existing")],
            generate_key_dialog_cls=_fake_dialog_cls_returning(new_key),
            key_service=MagicMock(),
            ssh_test_factory=fake_ssh_factory,
        )

        # Step 1: fill in the identity fields + host/user (claude-remote is
        # the default profile — see ConnectionForm._current_profile).
        dlg._edit_name.setText("Journey Conn")
        dlg._edit_id.setText("journey-conn")
        dlg._form._edit_host.setText("no-such-host.invalid")
        dlg._form._edit_user.setText("ubuntu")
        dlg._form._edit_project_folder.setText("/opt/app")
        dlg._form._edit_claude_options.setText("--resume")

        # Step 2: "+ New Key…" — the button a user would actually click.
        btn_new_key = dlg.findChild(QPushButton, "btn_new_key")
        assert btn_new_key is not None
        btn_new_key.click()

        # Step 3: it must now be selectable and selected in the dropdown.
        combo = dlg._form._combo_identity_file_ref
        items = [combo.itemText(i) for i in range(combo.count())]
        assert "key-journey" in items
        assert combo.currentText() == "key-journey"

        # Step 4: what the user would actually save must reference it.
        data = dlg.get_connection_data()
        assert data["identity_file_ref"] == "key-journey"

        # Step 5: Test Connection, using the ref exactly as collected above
        # (mirrors _on_test_connection reading self._form.collect_data()).
        dlg._on_test_connection()

        assert resolved_paths_used == [Path("/home/user/.ssh/id_journey")], (
            "Test Connection did not resolve identity_file_ref through the "
            "just-generated key's real private_path"
        )
        assert "✓" in dlg._lbl_test_result.text()

        # Step 6: the key is exposed for the caller (main_window) to persist
        # — this is the exact hand-off tested against a stub dialog in
        # TestNewKeyPersistedOnSave; here it is the REAL dialog end to end.
        assert [k.id for k in dlg.new_ssh_keys] == ["key-journey"]

    def test_journey_persists_through_main_window_open_connection_editor(
        self, qtbot
    ) -> None:
        """Same journey, one layer further out: run it through the REAL
        ConnectionEditorDialog (not a stub, unlike TestNewKeyPersistedOnSave)
        and feed its output into the REAL, unbound
        MainWindow._open_connection_editor / _reconstruct_connection, so the
        seam between "dialog produced a new key + connection data" and
        "document.ssh_keys / document.connections actually gained them" is
        exercised with no double."""
        from cpsm.data.schema import CpsmDocument
        from cpsm.ui.main_window import MainWindow

        new_key = _make_key("key-journey-2", private_path="/home/user/.ssh/id_journey2")

        class _Stand:
            _make_conn_editor_kwargs = MainWindow._make_conn_editor_kwargs
            _reconstruct_connection = MainWindow._reconstruct_connection

            def __init__(self) -> None:
                self._document = CpsmDocument()
                self.load_document_calls: list[Any] = []
                self.save_document_calls = 0

            def load_document(self, doc: Any) -> None:
                self.load_document_calls.append(doc)

            def _save_document(self) -> None:
                self.save_document_calls += 1

        stand = _Stand()

        def _dialog_factory(parent=None, **kwargs):
            # `parent` here is the _Stand instance (main_window passes
            # parent=self), not a QWidget — pass None to the real QDialog.
            dlg = ConnectionEditorDialog(
                parent=None,
                generate_key_dialog_cls=_fake_dialog_cls_returning(new_key),
                key_service=MagicMock(),
                **kwargs,
            )
            qtbot.addWidget(dlg)
            dlg._edit_name.setText("Journey Two")
            dlg._edit_id.setText("journey-two")
            dlg._form._edit_host.setText("no-such-host.invalid")
            dlg._form._edit_user.setText("ubuntu")
            dlg._form._edit_project_folder.setText("/opt/app")
            dlg._form._edit_claude_options.setText("--resume")

            btn_new_key = dlg.findChild(QPushButton, "btn_new_key")
            btn_new_key.click()

            # Fake .exec() -> Accepted, mirroring the user clicking Save.
            dlg.exec = lambda: int(dlg.DialogCode.Accepted)  # type: ignore[method-assign]
            return dlg

        # `_open_connection_editor` reads `ConnectionEditorDialog.DialogCode`
        # off whatever name it resolves after patching — attach the real
        # enum so that lookup still works with the factory in place.
        _dialog_factory.DialogCode = ConnectionEditorDialog.DialogCode

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "cpsm.ui.dialogs.connection_editor.ConnectionEditorDialog",
                _dialog_factory,
            )
            MainWindow._open_connection_editor(stand, None)

        assert any(k.id == "key-journey-2" for k in stand._document.ssh_keys)
        conn = next(
            (c for c in stand._document.connections if c.id == "journey-two"), None
        )
        assert conn is not None
        assert conn.identity_file_ref == "key-journey-2"
        assert stand.save_document_calls == 1


class TestEndToEndDiscoveryTriggeredAutoPin:
    """Journey: no usable pinned key -> discovery finds a candidate -> the
    probe succeeds -> the user confirms -> the document is updated, all the
    way out through the same MainWindow persistence path used above.

    Mock-to-mock honesty: `key_discovery_service` and `key_probe_factory`
    are injected fakes (as they must be — no live network probe is allowed
    in this suite), so this proves the WIRING from probe result to prompt to
    persisted document, not that a real host actually authenticates.
    """

    def test_confirmed_auto_pin_updates_document_via_main_window(self, qtbot) -> None:
        from cpsm.data.schema import CpsmDocument
        from cpsm.ui.main_window import MainWindow

        candidate = KeyCandidate(
            private_path=Path("/home/user/.ssh/id_autodiscovered"),
            public_path=Path("/home/user/.ssh/id_autodiscovered.pub"),
            comment="root@no-such-host.invalid",
            source="pub_comment_user_host",
            score=80,
        )
        discovery = MagicMock()
        discovery.discover.return_value = [candidate]

        def probe_factory(*, host, user, port, candidates, on_result):
            on_result(candidates[0])

        confirmations: list[Any] = []

        def confirm_fn(parent, cand):
            confirmations.append(cand)
            return True

        class _Stand:
            _make_conn_editor_kwargs = MainWindow._make_conn_editor_kwargs
            _reconstruct_connection = MainWindow._reconstruct_connection

            def __init__(self) -> None:
                self._document = CpsmDocument()
                self.load_document_calls: list[Any] = []
                self.save_document_calls = 0

            def load_document(self, doc: Any) -> None:
                self.load_document_calls.append(doc)

            def _save_document(self) -> None:
                self.save_document_calls += 1

        stand = _Stand()

        def _dialog_factory(parent=None, **kwargs):
            # See note above: `parent` is the _Stand instance, not a QWidget.
            dlg = ConnectionEditorDialog(
                parent=None,
                key_discovery_service=discovery,
                key_probe_factory=probe_factory,
                confirm_pin_fn=confirm_fn,
                **kwargs,
            )
            qtbot.addWidget(dlg)
            dlg.show()
            dlg._edit_name.setText("Discovered Conn")
            dlg._edit_id.setText("discovered-conn")
            dlg._form._edit_host.setText("no-such-host.invalid")
            dlg._form._edit_user.setText("root")
            dlg._form._edit_project_folder.setText("/opt/app")
            dlg._form._edit_claude_options.setText("--resume")
            # No identity key selected — the unpinned case that must fall
            # through to discovery (see _run_ssh_test). `_on_key_probe_result`
            # early-returns unless the dialog isVisible(), so it must be
            # shown (mirrors `_make_dlg` elsewhere in this file) even though
            # this factory bypasses `_make_dlg` to control ctor kwargs.

            dlg._on_test_connection()

            dlg.exec = lambda: int(dlg.DialogCode.Accepted)  # type: ignore[method-assign]
            return dlg

        _dialog_factory.DialogCode = ConnectionEditorDialog.DialogCode

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "cpsm.ui.dialogs.connection_editor.ConnectionEditorDialog",
                _dialog_factory,
            )
            MainWindow._open_connection_editor(stand, None)

        assert confirmations == [candidate]
        pinned = next(
            (k for k in stand._document.ssh_keys if str(k.private_path) == str(candidate.private_path)),
            None,
        )
        assert pinned is not None, "confirmed discovery pin never reached document.ssh_keys"
        conn = next(
            (c for c in stand._document.connections if c.id == "discovered-conn"), None
        )
        assert conn is not None
        assert conn.identity_file_ref == pinned.id
        assert stand.save_document_calls == 1


# ---------------------------------------------------------------------------
# Phase 4 (cpsm-connection-key-ux): accessible-description accuracy.
#
# The claim-check gate flagged that accessible-description accuracy has no
# enforcing mechanism, and named the specific drift that already happened
# once: btn_new_key's description read "wired in Phase 13" long after that
# was misleading. A narrow, low-maintenance regression guard: none of the
# touched widgets' accessible descriptions may contain a phase reference or
# language implying the feature is not yet implemented.
# ---------------------------------------------------------------------------


class TestAccessibleDescriptionsDoNotClaimUnfinishedWork:
    _DRIFT_MARKERS = ("phase ", "not yet", "wired in", "todo", "tbd", "unimplemented")

    def _assert_clean(self, widget, label: str) -> None:
        desc = widget.accessibleDescription()
        assert desc, f"{label} has no accessible description at all"
        lowered = desc.lower()
        for marker in self._DRIFT_MARKERS:
            assert marker not in lowered, (
                f"{label}'s accessible description still references "
                f"unfinished/phase-numbered work ({marker!r}): {desc!r}"
            )

    def test_btn_new_key(self, qtbot) -> None:
        dlg = _make_dlg(qtbot)
        btn = dlg.findChild(QPushButton, "btn_new_key")
        assert btn is not None
        self._assert_clean(btn, "btn_new_key")

    def test_combo_identity_file_ref(self, qtbot) -> None:
        dlg = _make_dlg(qtbot)
        self._assert_clean(dlg._form._combo_identity_file_ref, "combo_identity_file_ref")

    def test_confirm_pin_dialog(self, qtbot, tmp_path) -> None:
        candidate = _make_candidate(tmp_path, "utility", comment="root@192.0.2.44")
        discovery = MagicMock()
        discovery.discover.return_value = [candidate]

        def probe_factory(*, host, user, port, candidates, on_result):
            on_result(candidates[0])

        captured_boxes: list[Any] = []
        real_exec = QMessageBox.exec

        def fake_exec(self):
            captured_boxes.append(self)
            return int(QMessageBox.StandardButton.Yes)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(QMessageBox, "exec", fake_exec)
            dlg = _make_dlg(
                qtbot, conn_data=_CONN_REMOTE, is_new=False, ssh_keys=[],
                key_discovery_service=discovery, key_probe_factory=probe_factory,
            )
            dlg._on_test_connection()

        assert len(captured_boxes) == 1
        self._assert_clean(captured_boxes[0], "dlg_confirm_pin_discovered_key")
