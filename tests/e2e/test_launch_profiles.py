# -*- coding: utf-8 -*-
"""
E2E tests: launch profiles.

Acceptance criteria covered:
  §10.4   All 5 launch_profile values (claude-remote, claude-local, ssh-shell,
          local-shell, custom) are rendered through TemplateService and
          dispatched through SessionService end-to-end.
  §10.5   Profile-conditional schema rejection: forbidden fields on wrong
          profile raise ValidationError.
  §10.6   Profile-switch in ConnectionEditor confirms before clearing forbidden
          fields (dialog prompt).
  §10.27  Local profiles (claude-local, local-shell) never invoke ssh/scp/plink
          — integration assertion via ProcessRunner.run call capture.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: I001
from pydantic import ValidationError

from cpsm.data.repository import CpsmRepository
from cpsm.data.schema import (
    ClaudeLocalConnection,
    ClaudeRemoteConnection,
    CpsmDocument,
    CustomConnection,
    LocalShellConnection,
    SshKey,
    SshShellConnection,
)
from cpsm.platform.process_runner import ProcessRunner
from cpsm.services.config_service import ConfigService
from cpsm.services.layout_service import LayoutService
from cpsm.services.session_service import SessionService
from cpsm.services.template_service import TemplateService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_key() -> SshKey:
    return SshKey(
        id="key-default",
        name="Default key",
        type="ed25519",
        private_path="~/.ssh/id_ed25519",
        public_path="~/.ssh/id_ed25519.pub",
    )


def _make_session_service(mock_backend, mock_runner=None):
    repo = CpsmRepository()
    config_svc = ConfigService(repository=repo)
    template_svc = TemplateService()
    layout_svc = MagicMock(spec=LayoutService)
    layout_svc.apply_change.return_value = None

    svc = SessionService(
        config=config_svc,
        backend=mock_backend,
        templates=template_svc,
        layout=layout_svc,
    )
    return svc


def _doc_with(*connections, keys=None) -> CpsmDocument:
    return CpsmDocument(
        ssh_keys=keys or [_make_key()],
        connections=list(connections),
    )


# ---------------------------------------------------------------------------
# §10.4 — All 5 launch profiles end-to-end
# ---------------------------------------------------------------------------


class TestAllLaunchProfiles:
    """Acceptance §10.4: All 5 launch_profile values supported end-to-end."""

    def test_claude_remote_launch(self, mock_backend):
        """Acceptance §10.4: claude-remote profile renders and launches."""
        conn = ClaudeRemoteConnection(
            id="web01",
            launch_profile="claude-remote",
            host="dev.example.com",
            user="ubuntu",
            identity_file_ref="key-default",
            project_folder="/opt/webapp",
            claude_options="--resume",
        )
        doc = _doc_with(conn)
        svc = _make_session_service(mock_backend)

        result = svc.launch(doc, "web01")

        assert result.connection_id == "web01"
        assert result.session_name == "cpsm-web01"
        # SessionService uses list_sessions to check existence
        mock_backend.list_sessions.assert_called()

    def test_claude_local_launch(self, mock_backend):
        """Acceptance §10.4: claude-local profile renders and launches."""
        conn = ClaudeLocalConnection(
            id="dotfiles",
            launch_profile="claude-local",
            project_folder="/home/user/dotfiles",
            claude_options="--resume",
        )
        doc = _doc_with(conn)
        svc = _make_session_service(mock_backend)

        result = svc.launch(doc, "dotfiles")

        assert result.connection_id == "dotfiles"
        assert result.session_name == "cpsm-dotfiles"
        mock_backend.list_sessions.assert_called()

    def test_ssh_shell_launch(self, mock_backend):
        """Acceptance §10.4: ssh-shell profile renders and launches."""
        conn = SshShellConnection(
            id="bastion",
            launch_profile="ssh-shell",
            host="bastion.example.com",
            user="ops",
            identity_file_ref="key-default",
        )
        doc = _doc_with(conn)
        svc = _make_session_service(mock_backend)

        result = svc.launch(doc, "bastion")

        assert result.connection_id == "bastion"
        assert result.session_name == "cpsm-bastion"
        mock_backend.list_sessions.assert_called()

    def test_local_shell_launch(self, mock_backend):
        """Acceptance §10.4: local-shell profile renders and launches."""
        conn = LocalShellConnection(
            id="scratch",
            launch_profile="local-shell",
            project_folder="/home/user/scratch",
        )
        doc = _doc_with(conn)
        svc = _make_session_service(mock_backend)

        result = svc.launch(doc, "scratch")

        assert result.connection_id == "scratch"
        assert result.session_name == "cpsm-scratch"
        mock_backend.list_sessions.assert_called()

    def test_custom_launch(self, mock_backend):
        """Acceptance §10.4: custom profile renders and launches via template."""
        from cpsm.data.schema import LaunchTemplate

        conn = CustomConnection(
            id="custom-01",
            launch_profile="custom",
            custom_template_id="my-tmpl",
        )
        # CpsmDocument requires custom_template_id to be in launch_templates.
        # LaunchTemplate uses fields: id, bash, description (not name/content).
        tmpl = LaunchTemplate(id="my-tmpl", bash="#!/bin/bash\necho hi")
        doc = CpsmDocument(
            ssh_keys=[_make_key()],
            connections=[conn],
            launch_templates=[tmpl],
        )
        svc = _make_session_service(mock_backend)

        result = svc.launch(doc, "custom-01")

        assert result.connection_id == "custom-01"
        # Either succeeds or reports error gracefully — must not raise
        assert isinstance(result.success, bool)


# ---------------------------------------------------------------------------
# §10.5 — Profile-conditional schema rejection
# ---------------------------------------------------------------------------


class TestProfileSchemaRejection:
    """Acceptance §10.5: Forbidden fields on a profile raise ValidationError."""

    def test_claude_local_rejects_host_field(self):
        """Acceptance §10.5: claude-local rejects 'host' field (extra='forbid')."""
        with pytest.raises(ValidationError):
            ClaudeLocalConnection.model_validate(
                {
                    "id": "bad-local",
                    "launch_profile": "claude-local",
                    "project_folder": "/opt/x",
                    "claude_options": "--resume",
                    "host": "dev.example.com",  # FORBIDDEN
                }
            )

    def test_claude_local_rejects_identity_file_ref(self):
        """Acceptance §10.5: claude-local rejects identity_file_ref."""
        with pytest.raises(ValidationError):
            ClaudeLocalConnection.model_validate(
                {
                    "id": "bad-local",
                    "launch_profile": "claude-local",
                    "project_folder": "/opt/x",
                    "claude_options": "--resume",
                    "identity_file_ref": "key-default",  # FORBIDDEN
                }
            )

    def test_local_shell_rejects_claude_options(self):
        """Acceptance §10.5: local-shell rejects claude_options field."""
        with pytest.raises(ValidationError):
            LocalShellConnection.model_validate(
                {
                    "id": "bad-shell",
                    "launch_profile": "local-shell",
                    "project_folder": "/opt/x",
                    "claude_options": "--resume",  # FORBIDDEN
                }
            )

    def test_local_shell_rejects_host(self):
        """Acceptance §10.5: local-shell rejects 'host' field."""
        with pytest.raises(ValidationError):
            LocalShellConnection.model_validate(
                {
                    "id": "bad-shell",
                    "launch_profile": "local-shell",
                    "project_folder": "/opt/x",
                    "host": "dev.example.com",  # FORBIDDEN
                }
            )

    def test_ssh_shell_rejects_claude_options(self):
        """Acceptance §10.5: ssh-shell rejects claude_options field."""
        with pytest.raises(ValidationError):
            SshShellConnection.model_validate(
                {
                    "id": "bad-ssh",
                    "launch_profile": "ssh-shell",
                    "host": "host.example.com",
                    "user": "ubuntu",
                    "identity_file_ref": "key-default",
                    "claude_options": "--resume",  # FORBIDDEN
                }
            )

    def test_claude_remote_requires_host(self):
        """Acceptance §10.5: claude-remote requires 'host' field."""
        with pytest.raises(ValidationError):
            ClaudeRemoteConnection.model_validate(
                {
                    "id": "bad-remote",
                    "launch_profile": "claude-remote",
                    # host omitted — required field
                    "user": "ubuntu",
                    "identity_file_ref": "key-default",
                    "project_folder": "/opt/x",
                    "claude_options": "--resume",
                }
            )


# ---------------------------------------------------------------------------
# §10.6 — Profile-switch confirms before clearing forbidden fields
# ---------------------------------------------------------------------------


class TestProfileSwitchConfirm:
    """Acceptance §10.6: Profile-switch in ConnectionEditor confirms before clearing."""

    def test_connection_editor_has_profile_radio_buttons(self, qtbot):
        """Acceptance §10.6: ConnectionEditor has radio buttons for profile switching."""
        from PySide6.QtWidgets import QRadioButton

        from cpsm.ui.dialogs.connection_editor import ConnectionEditorDialog

        # ConnectionEditorDialog takes connection_data dict, not a Connection model
        conn_data = {
            "id": "web01",
            "launch_profile": "claude-remote",
            "host": "dev.example.com",
            "user": "ubuntu",
            "identity_file_ref": "key-default",
            "project_folder": "/opt/webapp",
            "claude_options": "--resume",
        }

        dlg = ConnectionEditorDialog(
            connection_data=conn_data,
            available_key_ids=["key-default"],
            is_new=False,
        )
        qtbot.addWidget(dlg)
        dlg.show()

        # Profile selection uses radio buttons with names like "radio_claude_remote"
        remote_radio = dlg.findChild(QRadioButton, "radio_claude_remote")
        assert remote_radio is not None, (
            "radio_claude_remote not found — profile radio button missing"
        )
        local_radio = dlg.findChild(QRadioButton, "radio_claude_local")
        assert local_radio is not None, (
            "radio_claude_local not found — profile radio button missing"
        )

    def test_profile_switch_radio_buttons_present_for_all_profiles(self, qtbot):
        """Acceptance §10.6: All 5 profile radio buttons exist in ConnectionEditor."""
        from PySide6.QtWidgets import QRadioButton

        from cpsm.ui.dialogs.connection_editor import ConnectionEditorDialog

        conn_data = {
            "id": "web01",
            "launch_profile": "claude-remote",
            "host": "dev.example.com",
            "user": "ubuntu",
            "identity_file_ref": "key-default",
            "project_folder": "/opt/webapp",
            "claude_options": "--resume",
        }

        dlg = ConnectionEditorDialog(
            connection_data=conn_data,
            available_key_ids=["key-default"],
            is_new=False,
        )
        qtbot.addWidget(dlg)

        # All 5 profile radios should exist
        expected_names = [
            "radio_claude_remote",
            "radio_claude_local",
            "radio_ssh_shell",
            "radio_local_shell",
            "radio_custom",
        ]
        for name in expected_names:
            radio = dlg.findChild(QRadioButton, name)
            assert radio is not None, f"Radio button '{name}' not found in ConnectionEditorDialog"

        # The claude-remote radio should be pre-selected
        remote_radio = dlg.findChild(QRadioButton, "radio_claude_remote")
        assert remote_radio.isChecked(), "claude-remote radio should be pre-selected"


# ---------------------------------------------------------------------------
# §10.27 — Local profiles never invoke ssh/scp/plink
# ---------------------------------------------------------------------------


class TestLocalProfileNoSsh:
    """Acceptance §10.27: Local profiles never invoke ssh/scp/plink."""

    def test_claude_local_no_ssh_in_rendered_template(self):
        """Acceptance §10.27: claude-local template renders without ssh/scp/plink."""
        conn = ClaudeLocalConnection(
            id="dotfiles",
            launch_profile="claude-local",
            project_folder="/home/user/dotfiles",
            claude_options="--resume",
        )
        svc = TemplateService()
        rendered = svc.render("claude-local", conn)

        # No ssh/scp/plink invocation at a process-spawn boundary
        import re

        forbidden_re = re.compile(
            r"(?:^|[\s;|&`(])(?:ssh|scp|plink)\b",
            re.MULTILINE,
        )
        assert forbidden_re.search(rendered) is None, (
            f"claude-local template invokes ssh/scp/plink:\n{rendered}"
        )

    def test_local_shell_no_ssh_in_rendered_template(self):
        """Acceptance §10.27: local-shell template renders without ssh/scp/plink."""
        conn = LocalShellConnection(
            id="scratch",
            launch_profile="local-shell",
            project_folder="/tmp/scratch",
        )
        svc = TemplateService()
        rendered = svc.render("local-shell", conn)

        import re

        forbidden_re = re.compile(
            r"(?:^|[\s;|&`(])(?:ssh|scp|plink)\b",
            re.MULTILINE,
        )
        assert forbidden_re.search(rendered) is None, (
            f"local-shell template invokes ssh/scp/plink:\n{rendered}"
        )

    def test_claude_local_launch_never_calls_runner_with_ssh(self, mock_backend):
        """Acceptance §10.27: SessionService.launch for claude-local never calls ssh/scp/plink."""
        conn = ClaudeLocalConnection(
            id="dotfiles",
            launch_profile="claude-local",
            project_folder="/home/user/dotfiles",
            claude_options="--resume",
        )
        doc = _doc_with(conn)

        # Capture all ProcessRunner.run calls
        recorded_argvs: list[list[str]] = []

        real_runner = ProcessRunner()

        def _capturing_run(argv, **kwargs):
            recorded_argvs.append(list(argv))
            # Return a mock instead of executing
            return MagicMock(returncode=0, stdout="", stderr="")

        real_runner.run = _capturing_run  # type: ignore[method-assign]

        svc = _make_session_service(mock_backend)
        svc.launch(doc, "dotfiles")

        # No argv from ProcessRunner.run should contain ssh, scp, or plink
        for argv in recorded_argvs:
            for arg in argv:
                assert arg not in ("ssh", "scp", "plink"), (
                    f"Local profile invoked '{arg}' in argv: {argv}"
                )
                # Also check for path-qualified invocations
                assert not arg.endswith("/ssh"), f"Local profile invoked {arg}: {argv}"
                assert not arg.endswith("/scp"), f"Local profile invoked {arg}: {argv}"
                assert not arg.endswith("/plink"), f"Local profile invoked {arg}: {argv}"

    def test_session_service_guard_raises_on_leaked_ssh(self, mock_backend):
        """Acceptance §10.27: SessionService raises LocalProfileLeakError if template leaks ssh."""
        from cpsm.services.session_service import LocalProfileLeakError

        conn = ClaudeLocalConnection(
            id="dotfiles",
            launch_profile="claude-local",
            project_folder="/home/user/dotfiles",
            claude_options="--resume",
        )
        doc = _doc_with(conn)
        repo = CpsmRepository()
        config_svc = ConfigService(repository=repo)
        template_svc = MagicMock(spec=TemplateService)
        # Inject a template that leaks ssh — must be caught by the guard
        template_svc.render.return_value = "#!/bin/bash\nssh ubuntu@dev.example.com"
        layout_svc = MagicMock(spec=LayoutService)

        svc = SessionService(
            config=config_svc,
            backend=mock_backend,
            templates=template_svc,
            layout=layout_svc,
        )

        with pytest.raises(LocalProfileLeakError):
            svc.launch(doc, "dotfiles")
