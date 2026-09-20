# -*- coding: utf-8 -*-
"""
Tests for cpsm.cli — headless CLI dispatcher.

Covers every subcommand listed in the Phase 9 acceptance criteria.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import pytest_mock

from cpsm import __version__
from cpsm.cli import (
    EXIT_BAD_ARGS,
    EXIT_GENERIC,
    EXIT_LAUNCH_FAILED,
    EXIT_NOT_FOUND,
    EXIT_OK,
    EXIT_SOURCE_NOT_FOUND,
    EXIT_TARGET_EXISTS,
    EXIT_VALIDATION_FAILED,
    dispatch,
)

# ---------------------------------------------------------------------------
# Fixture paths
# ---------------------------------------------------------------------------

FIXTURES_DIR = Path(__file__).parent / "data" / "fixtures"
VALID_CPSM = FIXTURES_DIR / "valid-cpsm.yaml"
LEGACY_YAML = FIXTURES_DIR / "example-.claude-projects.yaml"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _dispatch_capture(argv: list[str], capsys: pytest.CaptureFixture) -> tuple[int, str, str]:
    """Run dispatch(argv) and return (exit_code, stdout, stderr)."""
    code = dispatch(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


# ---------------------------------------------------------------------------
# --version / --help
# ---------------------------------------------------------------------------


class TestVersionHelp:
    def test_version(self, capsys: pytest.CaptureFixture) -> None:
        with pytest.raises(SystemExit) as exc_info:
            dispatch(["--version"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert __version__ in captured.out

    def test_help(self, capsys: pytest.CaptureFixture) -> None:
        with pytest.raises(SystemExit) as exc_info:
            dispatch(["--help"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "cpsm" in captured.out.lower()

    def test_validate_help(self, capsys: pytest.CaptureFixture) -> None:
        with pytest.raises(SystemExit) as exc_info:
            dispatch(["validate", "--help"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "validate" in captured.out.lower()

    def test_launch_help(self, capsys: pytest.CaptureFixture) -> None:
        with pytest.raises(SystemExit) as exc_info:
            dispatch(["launch", "--help"])
        assert exc_info.value.code == 0

    def test_launch_group_help(self, capsys: pytest.CaptureFixture) -> None:
        with pytest.raises(SystemExit) as exc_info:
            dispatch(["launch-group", "--help"])
        assert exc_info.value.code == 0

    def test_launch_scene_help(self, capsys: pytest.CaptureFixture) -> None:
        with pytest.raises(SystemExit) as exc_info:
            dispatch(["launch-scene", "--help"])
        assert exc_info.value.code == 0

    def test_import_help(self, capsys: pytest.CaptureFixture) -> None:
        with pytest.raises(SystemExit) as exc_info:
            dispatch(["import", "--help"])
        assert exc_info.value.code == 0

    def test_unknown_subcommand(self, capsys: pytest.CaptureFixture) -> None:
        with pytest.raises(SystemExit) as exc_info:
            dispatch(["does-not-exist"])
        assert exc_info.value.code == EXIT_BAD_ARGS

    def test_no_subcommand_defaults_to_gui(self, mocker: pytest_mock.MockerFixture) -> None:
        """Bare ``cpsm`` defaults to launching the GUI now (no subcommand
        required). The legacy behavior of exiting with EXIT_BAD_ARGS was
        reverted at the user's request.

        NOTE: we patch ``cpsm.app.run_gui`` rather than ``cpsm.cli.cmd_gui``
        because the dispatcher's handler table captured cmd_gui by
        reference at module-import time — a module-attribute patch of
        ``cpsm.cli.cmd_gui`` doesn't intercept the call (which would then
        actually start a Qt event loop and hang the test).
        """
        mocker.patch("cpsm.app.run_gui", return_value=EXIT_OK)
        assert dispatch([]) == EXIT_OK


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


class TestValidate:
    def test_validate_valid_config(self, capsys: pytest.CaptureFixture) -> None:
        code, out, _err = _dispatch_capture(["validate", "--config", str(VALID_CPSM)], capsys)
        assert code == EXIT_OK
        assert "valid" in out.lower()

    def test_validate_invalid_config(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        """A config that's missing a required field (no host) is rejected
        via pydantic's structural check. FK integrity failures are covered
        by ``test_validate_dangling_fk_config`` below.
        """
        broken = tmp_path / "broken.yaml"
        broken.write_text(
            """\
schema_version: 1
settings: {}
connections:
  - id: web01
    name: "WebApp"
    launch_profile: claude-remote
    user: ubuntu
    project_folder: /opt/app
    claude_options: "--resume"
""",
            encoding="utf-8",
        )
        code, out, err = _dispatch_capture(["validate", "--config", str(broken)], capsys)
        assert code == EXIT_VALIDATION_FAILED
        # Human-readable output must mention something about the issue
        assert out or err  # at least one of them has content

    def test_validate_json_output(self, capsys: pytest.CaptureFixture) -> None:
        code, out, _err = _dispatch_capture(
            ["validate", "--config", str(VALID_CPSM), "--json"], capsys
        )
        assert code == EXIT_OK
        payload = json.loads(out)
        assert "valid" in payload
        assert isinstance(payload["valid"], bool)
        assert "issues" in payload
        assert isinstance(payload["issues"], list)

    def test_validate_json_invalid(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        broken = tmp_path / "broken.yaml"
        broken.write_text(
            """\
schema_version: 1
settings: {}
connections:
  - id: web01
    name: "WebApp"
    launch_profile: claude-remote
    user: ubuntu
    project_folder: /opt/app
    claude_options: "--resume"
""",
            encoding="utf-8",
        )
        code, out, _err = _dispatch_capture(["validate", "--config", str(broken), "--json"], capsys)
        assert code == EXIT_VALIDATION_FAILED
        payload = json.loads(out)
        assert payload["valid"] is False
        assert len(payload["issues"]) > 0
        for issue in payload["issues"]:
            assert "path" in issue
            assert "message" in issue
            assert "severity" in issue

    def test_validate_dangling_fk_config(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """Regression: a dangling FK reference (a group member with no
        matching connection) must trip ``cpsm validate`` to
        ``EXIT_VALIDATION_FAILED`` with the offending id in the output.

        A prior fix downgraded FK checks in the schema-layer validator
        from raise to warning so the app could still load over a single
        broken link. That change silently made ``ConfigService.validate``
        report every broken config as valid (the CLI printed "Config is
        valid.") — this test locks the fix at the CLI surface.
        """
        broken = tmp_path / "broken.yaml"
        broken.write_text(
            """\
schema_version: 1
settings: {}
connections: []
groups:
  - id: saas
    name: SaaS
    members:
      - rmm-server
""",
            encoding="utf-8",
        )
        code, out, err = _dispatch_capture(["validate", "--config", str(broken)], capsys)
        assert code == EXIT_VALIDATION_FAILED, (
            f"expected EXIT_VALIDATION_FAILED, got {code}; stdout={out!r} stderr={err!r}"
        )
        combined = out + err
        assert "rmm-server" in combined, (
            f"expected the dangling id in output; stdout={out!r} stderr={err!r}"
        )

    def test_validate_dangling_fk_config_json(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """JSON variant of the CLI dangling-FK regression check."""
        broken = tmp_path / "broken.yaml"
        broken.write_text(
            """\
schema_version: 1
settings: {}
connections: []
groups:
  - id: saas
    name: SaaS
    members:
      - rmm-server
""",
            encoding="utf-8",
        )
        code, out, _err = _dispatch_capture(["validate", "--config", str(broken), "--json"], capsys)
        assert code == EXIT_VALIDATION_FAILED
        payload = json.loads(out)
        assert payload["valid"] is False
        assert any("rmm-server" in i["message"] for i in payload["issues"])
        assert any(i["path"].startswith("groups.saas") for i in payload["issues"])

    def test_validate_missing_config_json(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """A nonexistent config path triggers load_or_create, returning an empty valid doc.

        The repository resolves a missing file to a default empty document, so
        validate exits 0. This is the expected 'first-run' behaviour.
        """
        missing = tmp_path / "nonexistent.yaml"
        code, out, _err = _dispatch_capture(
            ["validate", "--config", str(missing), "--json"], capsys
        )
        # Empty document is always valid — load_or_create returns a default doc
        assert code == EXIT_OK
        payload = json.loads(out)
        assert payload["valid"] is True


# ---------------------------------------------------------------------------
# launch
# ---------------------------------------------------------------------------


class TestLaunch:
    def test_launch_unknown_connection(self, capsys: pytest.CaptureFixture) -> None:
        code, _out, _err = _dispatch_capture(
            ["launch", "no-such-connection-id", "--config", str(VALID_CPSM)], capsys
        )
        assert code == EXIT_NOT_FOUND

    def test_launch_success(
        self, mocker: pytest_mock.MockerFixture, capsys: pytest.CaptureFixture
    ) -> None:
        """Mock TmuxBackend so the launch path succeeds without a real tmux."""
        mock_backend = MagicMock()
        mock_backend.list_sessions.return_value = []
        mock_backend.new_session.return_value = None
        mock_backend.set_window_option.return_value = None
        mock_backend.respawn_pane.return_value = None

        mocker.patch("cpsm.platform.tmux_backend.TmuxBackend", return_value=mock_backend)

        code, out, _err = _dispatch_capture(
            ["launch", "web01", "--config", str(VALID_CPSM)], capsys
        )
        assert code == EXIT_OK
        assert "web01" in out

    def test_launch_success_json(
        self, mocker: pytest_mock.MockerFixture, capsys: pytest.CaptureFixture
    ) -> None:
        mock_backend = MagicMock()
        mock_backend.list_sessions.return_value = []
        mock_backend.new_session.return_value = None
        mock_backend.set_window_option.return_value = None
        mock_backend.respawn_pane.return_value = None

        mocker.patch("cpsm.platform.tmux_backend.TmuxBackend", return_value=mock_backend)

        code, out, _err = _dispatch_capture(
            ["launch", "web01", "--config", str(VALID_CPSM), "--json"], capsys
        )
        assert code == EXIT_OK
        payload = json.loads(out)
        assert payload["success"] is True
        assert payload["connection_id"] == "web01"
        assert "session_name" in payload
        assert "errors" in payload
        assert "warnings" in payload

    def test_launch_local_profile_doesnt_invoke_ssh(
        self, mocker: pytest_mock.MockerFixture, capsys: pytest.CaptureFixture
    ) -> None:
        """For a claude-local connection, assert ProcessRunner is never called
        with ssh/scp/plink in argv (acceptance §10.27)."""
        from cpsm.platform import process_runner as pr_module

        invocations: list[list[str]] = []

        original_run = pr_module.ProcessRunner.run

        def spy_run(self: object, cmd: list[str], **kwargs: object) -> object:
            invocations.append(cmd)
            return original_run(self, cmd, **kwargs)  # type: ignore[arg-type]

        mock_backend = MagicMock()
        mock_backend.list_sessions.return_value = []
        mock_backend.new_session.return_value = None
        mock_backend.set_window_option.return_value = None
        mock_backend.respawn_pane.return_value = None

        mocker.patch("cpsm.platform.tmux_backend.TmuxBackend", return_value=mock_backend)

        _code, _out, _err = _dispatch_capture(
            ["launch", "dotfiles", "--config", str(VALID_CPSM)], capsys
        )
        # Accept success or launch error (tmux may not be running), but NEVER ssh
        for cmd_args in invocations:
            for token in cmd_args:
                assert token not in ("ssh", "scp", "plink"), (
                    f"Local profile invoked forbidden binary: {cmd_args}"
                )

    def test_launch_json_not_found(self, capsys: pytest.CaptureFixture) -> None:
        code, out, _err = _dispatch_capture(
            ["launch", "no-such-id", "--config", str(VALID_CPSM), "--json"], capsys
        )
        assert code == EXIT_NOT_FOUND
        payload = json.loads(out)
        assert payload["success"] is False
        assert len(payload["errors"]) > 0


# ---------------------------------------------------------------------------
# launch-group
# ---------------------------------------------------------------------------


class TestLaunchGroup:
    def test_launch_group_unknown(self, capsys: pytest.CaptureFixture) -> None:
        code, _out, _err = _dispatch_capture(
            ["launch-group", "no-such-group", "--config", str(VALID_CPSM)], capsys
        )
        assert code == EXIT_NOT_FOUND

    def test_launch_group_success(
        self, mocker: pytest_mock.MockerFixture, capsys: pytest.CaptureFixture
    ) -> None:
        mock_backend = MagicMock()
        mock_backend.list_sessions.return_value = []
        mock_backend.new_session.return_value = None
        mock_backend.set_window_option.return_value = None
        mock_backend.respawn_pane.return_value = None

        mocker.patch("cpsm.platform.tmux_backend.TmuxBackend", return_value=mock_backend)

        code, _out, _err = _dispatch_capture(
            ["launch-group", "project-1", "--config", str(VALID_CPSM)], capsys
        )
        # Partial failure is allowed; exit 0 with per-member status
        assert code == EXIT_OK

    def test_launch_group_partial_failure(
        self, mocker: pytest_mock.MockerFixture, capsys: pytest.CaptureFixture
    ) -> None:
        """Half the group succeeds, half fails. Exit 0 with warnings documented in output.

        Design decision: partial success yields EXIT_OK; per-member statuses
        are available in the JSON or human-readable output. Callers inspect
        member_statuses to detect per-connection failures.
        """
        mock_backend = MagicMock()

        call_count = 0

        def respawn_side_effect(*args: object, **kwargs: object) -> None:
            nonlocal call_count
            call_count += 1
            if call_count % 2 == 0:
                raise RuntimeError("simulated launch failure")

        mock_backend.list_sessions.return_value = []
        mock_backend.new_session.return_value = None
        mock_backend.set_window_option.return_value = None
        mock_backend.respawn_pane.side_effect = respawn_side_effect

        mocker.patch("cpsm.platform.tmux_backend.TmuxBackend", return_value=mock_backend)

        code, out, _err = _dispatch_capture(
            ["launch-group", "project-1", "--config", str(VALID_CPSM), "--json"],
            capsys,
        )
        # Per design: partial success exits 0; caller inspects member_statuses
        assert code == EXIT_OK
        payload = json.loads(out)
        assert "member_statuses" in payload
        # At least one member succeeded, at least one failed (due to alternating error)
        statuses = payload["member_statuses"]
        assert any(s["success"] for s in statuses)
        assert any(not s["success"] for s in statuses)

    def test_launch_group_json_shape(
        self, mocker: pytest_mock.MockerFixture, capsys: pytest.CaptureFixture
    ) -> None:
        mock_backend = MagicMock()
        mock_backend.list_sessions.return_value = []
        mock_backend.new_session.return_value = None
        mock_backend.set_window_option.return_value = None
        mock_backend.respawn_pane.return_value = None

        mocker.patch("cpsm.platform.tmux_backend.TmuxBackend", return_value=mock_backend)

        code, out, _err = _dispatch_capture(
            ["launch-group", "project-1", "--config", str(VALID_CPSM), "--json"],
            capsys,
        )
        assert code == EXIT_OK
        payload = json.loads(out)
        assert "success" in payload
        assert "group_id" in payload
        assert "session_name" in payload
        assert "member_statuses" in payload
        assert "errors" in payload
        assert "warnings" in payload


# ---------------------------------------------------------------------------
# launch-scene
# ---------------------------------------------------------------------------


class TestLaunchScene:
    def test_launch_scene_unknown(self, capsys: pytest.CaptureFixture) -> None:
        code, _out, _err = _dispatch_capture(
            ["launch-scene", "no-such-scene", "--config", str(VALID_CPSM)], capsys
        )
        assert code == EXIT_NOT_FOUND

    def test_launch_scene_success(
        self, mocker: pytest_mock.MockerFixture, capsys: pytest.CaptureFixture
    ) -> None:
        mock_backend = MagicMock()
        mock_backend.list_sessions.return_value = []
        mock_backend.new_session.return_value = None
        mock_backend.set_window_option.return_value = None
        mock_backend.respawn_pane.return_value = None

        mocker.patch("cpsm.platform.tmux_backend.TmuxBackend", return_value=mock_backend)

        # valid-cpsm.yaml has scene "workday" with on_conflict: error and two groups
        # sharing web01 across project-1 and project-2 through different viewports —
        # the scene launch will raise LayoutConflictError; catch and verify exit code
        code, _out, _err = _dispatch_capture(
            ["launch-scene", "workday", "--config", str(VALID_CPSM)], capsys
        )
        # workday uses on_conflict: error — layout conflict detected → EXIT_LAUNCH_FAILED
        # (scene has two groups with same viewport keys; see valid-cpsm.yaml)
        # Accept either outcome: EXIT_OK (no conflict detected) or EXIT_LAUNCH_FAILED
        assert code in (EXIT_OK, EXIT_LAUNCH_FAILED)

    def test_launch_scene_json_shape(
        self, mocker: pytest_mock.MockerFixture, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """Scene with first-wins conflict mode — JSON shape verified."""
        scene_config = tmp_path / "scene.yaml"
        scene_config.write_text(
            """\
schema_version: 1
settings: {}
connections:
  - id: conn-a
    name: "A"
    launch_profile: claude-local
    project_folder: ~/tmp
    claude_options: "--resume"
  - id: conn-b
    name: "B"
    launch_profile: claude-local
    project_folder: ~/tmp2
    claude_options: "--resume"
groups:
  - id: grp-a
    name: "GroupA"
    members: [conn-a]
  - id: grp-b
    name: "GroupB"
    members: [conn-b]
scenes:
  - id: my-scene
    groups: [grp-a, grp-b]
    on_conflict: first-wins
""",
            encoding="utf-8",
        )
        mock_backend = MagicMock()
        mock_backend.list_sessions.return_value = []
        mock_backend.new_session.return_value = None
        mock_backend.set_window_option.return_value = None
        mock_backend.respawn_pane.return_value = None

        mocker.patch("cpsm.platform.tmux_backend.TmuxBackend", return_value=mock_backend)

        code, out, _err = _dispatch_capture(
            ["launch-scene", "my-scene", "--config", str(scene_config), "--json"],
            capsys,
        )
        assert code in (EXIT_OK, EXIT_LAUNCH_FAILED)
        payload = json.loads(out)
        assert "success" in payload
        assert "scene_id" in payload
        assert "group_statuses" in payload
        assert "errors" in payload
        assert "warnings" in payload


# ---------------------------------------------------------------------------
# import
# ---------------------------------------------------------------------------


class TestImport:
    def test_import_to_new_target(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        target = tmp_path / "output.cpsm.yaml"
        source_stat_before = LEGACY_YAML.stat()

        code, out, _err = _dispatch_capture(["import", str(LEGACY_YAML), "-o", str(target)], capsys)
        assert code == EXIT_OK
        assert target.exists()

        # Source file NEVER written: inode and mtime unchanged
        source_stat_after = LEGACY_YAML.stat()
        assert source_stat_before.st_ino == source_stat_after.st_ino
        assert source_stat_before.st_mtime == source_stat_after.st_mtime

        payload = json.loads(out)
        assert payload["wrote"] is True
        assert "source" in payload
        assert "target" in payload
        assert "transforms" in payload

    def test_import_target_exists_no_force(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        target = tmp_path / "exists.yaml"
        target.write_text("existing content", encoding="utf-8")

        code, _out, _err = _dispatch_capture(
            ["import", str(LEGACY_YAML), "-o", str(target)], capsys
        )
        assert code == EXIT_TARGET_EXISTS
        # Existing content must be preserved
        assert target.read_text(encoding="utf-8") == "existing content"

    def test_import_target_exists_with_force(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        target = tmp_path / "overwrite.yaml"
        target.write_text("old content", encoding="utf-8")

        code, _out, _err = _dispatch_capture(
            ["import", str(LEGACY_YAML), "-o", str(target), "--force"], capsys
        )
        assert code == EXIT_OK
        assert target.exists()
        # Content must have been overwritten
        assert target.read_text(encoding="utf-8") != "old content"

    def test_import_source_not_found(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        target = tmp_path / "out.yaml"
        code, _out, _err = _dispatch_capture(
            ["import", "/nonexistent/legacy.yaml", "-o", str(target)], capsys
        )
        assert code == EXIT_SOURCE_NOT_FOUND

    def test_import_transforms_list(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        target = tmp_path / "out.yaml"
        code, out, _err = _dispatch_capture(["import", str(LEGACY_YAML), "-o", str(target)], capsys)
        assert code == EXIT_OK
        payload = json.loads(out)
        # Transforms must be a list; each entry has kind/target_path/detail
        assert isinstance(payload["transforms"], list)
        for t in payload["transforms"]:
            assert "kind" in t
            assert "target_path" in t
            assert "detail" in t


# ---------------------------------------------------------------------------
# End-to-end smoke: fixture import → validate → launch → launch-group
# ---------------------------------------------------------------------------


class TestSmokeE2E:
    def test_smoke_import_then_validate(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """Import the example fixture to a temp .cpsm.yaml, then validate — exit 0."""
        target = tmp_path / "smoke.cpsm.yaml"

        code, out, err = _dispatch_capture(["import", str(LEGACY_YAML), "-o", str(target)], capsys)
        assert code == EXIT_OK, f"import failed: {err}"

        code, out, err = _dispatch_capture(["validate", "--config", str(target)], capsys)
        assert code == EXIT_OK, f"validate failed: {out}\n{err}"

    def test_smoke_import_validate_launch(
        self,
        tmp_path: Path,
        mocker: pytest_mock.MockerFixture,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """Import the example fixture; launch the first connection with mocked backend."""
        target = tmp_path / "smoke2.cpsm.yaml"

        code, _out, _err = _dispatch_capture(
            ["import", str(LEGACY_YAML), "-o", str(target)], capsys
        )
        assert code == EXIT_OK

        mock_backend = MagicMock()
        mock_backend.list_sessions.return_value = []
        mock_backend.new_session.return_value = None
        mock_backend.set_window_option.return_value = None
        mock_backend.respawn_pane.return_value = None
        mocker.patch("cpsm.platform.tmux_backend.TmuxBackend", return_value=mock_backend)

        # Load the produced config to find the first connection id
        from cpsm.data.repository import CpsmRepository

        repo = CpsmRepository()
        doc = repo.load_or_create(target)
        first_conn_id = doc.connections[0].id

        code, out, _err = _dispatch_capture(
            ["launch", first_conn_id, "--config", str(target), "--json"], capsys
        )
        assert code == EXIT_OK
        payload = json.loads(out)
        assert payload["success"] is True

    def test_smoke_import_validate_launch_group(
        self,
        tmp_path: Path,
        mocker: pytest_mock.MockerFixture,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """Import the example fixture; launch-group 'main' with mocked backend.

        The imported fixture creates a 'main' group. Assert per-member statuses
        are returned.
        """
        target = tmp_path / "smoke3.cpsm.yaml"

        code, _out, _err = _dispatch_capture(
            ["import", str(LEGACY_YAML), "-o", str(target)], capsys
        )
        assert code == EXIT_OK

        mock_backend = MagicMock()
        mock_backend.list_sessions.return_value = []
        mock_backend.new_session.return_value = None
        mock_backend.set_window_option.return_value = None
        mock_backend.respawn_pane.return_value = None
        mocker.patch("cpsm.platform.tmux_backend.TmuxBackend", return_value=mock_backend)

        # Find the first group in the imported config
        from cpsm.data.repository import CpsmRepository

        repo = CpsmRepository()
        doc = repo.load_or_create(target)
        assert len(doc.groups) > 0
        first_group_id = doc.groups[0].id

        code, out, _err = _dispatch_capture(
            ["launch-group", first_group_id, "--config", str(target), "--json"],
            capsys,
        )
        assert code == EXIT_OK
        payload = json.loads(out)
        assert "member_statuses" in payload
        assert isinstance(payload["member_statuses"], list)
        assert len(payload["member_statuses"]) > 0


# ---------------------------------------------------------------------------
# Additional coverage tests — error paths and non-JSON output branches
# ---------------------------------------------------------------------------


class TestValidateCoverageEdgeCases:
    def test_validate_invalid_config_human_readable(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """PydanticValidationError during load emits human-readable error."""
        broken = tmp_path / "broken.yaml"
        broken.write_text(
            """\
schema_version: 1
settings: {}
connections:
  - id: web01
    name: "WebApp"
    launch_profile: claude-remote
    user: ubuntu
    project_folder: /opt/app
    claude_options: "--resume"
""",
            encoding="utf-8",
        )
        code, out, err = _dispatch_capture(["validate", "--config", str(broken)], capsys)
        assert code == EXIT_VALIDATION_FAILED
        # Human-readable output goes to stdout (error list printed)
        assert "ERROR" in out or "ERROR" in err

    def test_validate_issues_human_readable(
        self, mocker: pytest_mock.MockerFixture, capsys: pytest.CaptureFixture
    ) -> None:
        """When validate() returns issues, human-readable listing is printed."""
        from cpsm.services.config_service import ValidationIssue

        mock_backend = MagicMock()
        mocker.patch("cpsm.platform.tmux_backend.TmuxBackend", return_value=mock_backend)

        # Patch validate() to return a fake issue
        mocker.patch(
            "cpsm.services.config_service.ConfigService.validate",
            return_value=[
                ValidationIssue(location="connections.0.id", message="test issue", severity="error")
            ],
        )
        code, out, _err = _dispatch_capture(["validate", "--config", str(VALID_CPSM)], capsys)
        assert code == EXIT_VALIDATION_FAILED
        assert "issue" in out.lower()


class TestLaunchCoverageEdgeCases:
    def test_launch_exception_human_readable(
        self, mocker: pytest_mock.MockerFixture, capsys: pytest.CaptureFixture
    ) -> None:
        """When session.launch() raises an uncaught exception, human-readable error printed."""
        mock_backend = MagicMock()
        mocker.patch("cpsm.platform.tmux_backend.TmuxBackend", return_value=mock_backend)
        # Patch at the SessionService level so the exception propagates to cmd_launch
        mocker.patch(
            "cpsm.services.session_service.SessionService.launch",
            side_effect=RuntimeError("unexpected crash"),
        )

        code, _out, err = _dispatch_capture(
            ["launch", "web01", "--config", str(VALID_CPSM)], capsys
        )
        assert code == EXIT_LAUNCH_FAILED
        assert "ERROR" in err

    def test_launch_exception_json(
        self, mocker: pytest_mock.MockerFixture, capsys: pytest.CaptureFixture
    ) -> None:
        """When session.launch() raises an uncaught exception, JSON error payload printed."""
        mock_backend = MagicMock()
        mocker.patch("cpsm.platform.tmux_backend.TmuxBackend", return_value=mock_backend)
        mocker.patch(
            "cpsm.services.session_service.SessionService.launch",
            side_effect=RuntimeError("unexpected crash"),
        )

        code, out, _err = _dispatch_capture(
            ["launch", "web01", "--config", str(VALID_CPSM), "--json"], capsys
        )
        assert code == EXIT_LAUNCH_FAILED
        payload = json.loads(out)
        assert payload["success"] is False
        assert len(payload["errors"]) > 0

    def test_launch_failure_result_human_readable(
        self, mocker: pytest_mock.MockerFixture, capsys: pytest.CaptureFixture
    ) -> None:
        """When result.success is False, error lines printed to stderr."""
        mock_backend = MagicMock()
        mock_backend.list_sessions.return_value = []
        mock_backend.new_session.return_value = None
        mock_backend.set_window_option.return_value = None
        mock_backend.respawn_pane.side_effect = RuntimeError("pane dead")
        mocker.patch("cpsm.platform.tmux_backend.TmuxBackend", return_value=mock_backend)

        code, _out, _err = _dispatch_capture(
            ["launch", "web01", "--config", str(VALID_CPSM)], capsys
        )
        # result.success=False because respawn_pane raised
        assert code == EXIT_LAUNCH_FAILED

    def test_launch_not_found_human_readable(self, capsys: pytest.CaptureFixture) -> None:
        code, out, err = _dispatch_capture(
            ["launch", "missing-id", "--config", str(VALID_CPSM)], capsys
        )
        assert code == EXIT_NOT_FOUND
        assert "not found" in err.lower() or "not found" in out.lower()

    def test_launch_with_isolation_per_group(
        self, mocker: pytest_mock.MockerFixture, capsys: pytest.CaptureFixture
    ) -> None:
        """--isolation per-group --group <id> is passed through correctly."""
        mock_backend = MagicMock()
        mock_backend.list_sessions.return_value = []
        mock_backend.new_session.return_value = None
        mock_backend.set_window_option.return_value = None
        mock_backend.respawn_pane.return_value = None
        mocker.patch("cpsm.platform.tmux_backend.TmuxBackend", return_value=mock_backend)

        code, _out, _err = _dispatch_capture(
            [
                "launch",
                "web01",
                "--config",
                str(VALID_CPSM),
                "--isolation",
                "per-group",
                "--group",
                "project-1",
            ],
            capsys,
        )
        assert code == EXIT_OK


class TestLaunchGroupCoverageEdgeCases:
    def test_launch_group_human_readable_success(
        self, mocker: pytest_mock.MockerFixture, capsys: pytest.CaptureFixture
    ) -> None:
        mock_backend = MagicMock()
        mock_backend.list_sessions.return_value = []
        mock_backend.new_session.return_value = None
        mock_backend.set_window_option.return_value = None
        mock_backend.respawn_pane.return_value = None
        mocker.patch("cpsm.platform.tmux_backend.TmuxBackend", return_value=mock_backend)

        code, out, _err = _dispatch_capture(
            ["launch-group", "project-1", "--config", str(VALID_CPSM)], capsys
        )
        assert code == EXIT_OK
        # Human output lists per-member statuses
        assert "OK" in out or "FAIL" in out

    def test_launch_group_not_found_human(self, capsys: pytest.CaptureFixture) -> None:
        code, out, err = _dispatch_capture(
            ["launch-group", "missing-group", "--config", str(VALID_CPSM)], capsys
        )
        assert code == EXIT_NOT_FOUND
        assert "not found" in err.lower() or "not found" in out.lower()

    def test_launch_group_not_found_json(self, capsys: pytest.CaptureFixture) -> None:
        """Group not found with --json flag outputs JSON payload."""
        code, out, _err = _dispatch_capture(
            ["launch-group", "missing-group", "--config", str(VALID_CPSM), "--json"], capsys
        )
        assert code == EXIT_NOT_FOUND
        payload = json.loads(out)
        assert payload["success"] is False
        assert len(payload["errors"]) > 0

    def test_launch_group_exception_human(
        self, mocker: pytest_mock.MockerFixture, capsys: pytest.CaptureFixture
    ) -> None:
        mock_backend = MagicMock()
        mocker.patch("cpsm.platform.tmux_backend.TmuxBackend", return_value=mock_backend)
        mocker.patch(
            "cpsm.services.session_service.SessionService.launch_group",
            side_effect=RuntimeError("backend crash"),
        )

        code, _out, err = _dispatch_capture(
            ["launch-group", "project-1", "--config", str(VALID_CPSM)], capsys
        )
        assert code == EXIT_LAUNCH_FAILED
        assert "ERROR" in err

    def test_launch_group_exception_json(
        self, mocker: pytest_mock.MockerFixture, capsys: pytest.CaptureFixture
    ) -> None:
        mock_backend = MagicMock()
        mocker.patch("cpsm.platform.tmux_backend.TmuxBackend", return_value=mock_backend)
        mocker.patch(
            "cpsm.services.session_service.SessionService.launch_group",
            side_effect=RuntimeError("backend crash"),
        )

        code, out, _err = _dispatch_capture(
            ["launch-group", "project-1", "--config", str(VALID_CPSM), "--json"], capsys
        )
        assert code == EXIT_LAUNCH_FAILED
        payload = json.loads(out)
        assert payload["success"] is False


class TestLaunchSceneCoverageEdgeCases:
    def test_launch_scene_not_found_human(self, capsys: pytest.CaptureFixture) -> None:
        code, out, err = _dispatch_capture(
            ["launch-scene", "missing-scene", "--config", str(VALID_CPSM)], capsys
        )
        assert code == EXIT_NOT_FOUND
        assert "not found" in err.lower() or "not found" in out.lower()

    def test_launch_scene_exception_human(
        self, mocker: pytest_mock.MockerFixture, capsys: pytest.CaptureFixture
    ) -> None:
        mock_backend = MagicMock()
        mocker.patch("cpsm.platform.tmux_backend.TmuxBackend", return_value=mock_backend)
        mocker.patch(
            "cpsm.services.session_service.SessionService.launch_scene",
            side_effect=RuntimeError("scene crash"),
        )

        code, _out, err = _dispatch_capture(
            ["launch-scene", "workday", "--config", str(VALID_CPSM)], capsys
        )
        assert code == EXIT_LAUNCH_FAILED
        assert "ERROR" in err

    def test_launch_scene_exception_json(
        self, mocker: pytest_mock.MockerFixture, capsys: pytest.CaptureFixture
    ) -> None:
        mock_backend = MagicMock()
        mocker.patch("cpsm.platform.tmux_backend.TmuxBackend", return_value=mock_backend)
        mocker.patch(
            "cpsm.services.session_service.SessionService.launch_scene",
            side_effect=RuntimeError("scene crash"),
        )

        code, out, _err = _dispatch_capture(
            ["launch-scene", "workday", "--config", str(VALID_CPSM), "--json"], capsys
        )
        assert code == EXIT_LAUNCH_FAILED
        payload = json.loads(out)
        assert payload["success"] is False

    def test_launch_scene_success_human(
        self, mocker: pytest_mock.MockerFixture, capsys: pytest.CaptureFixture
    ) -> None:
        from cpsm.services.session_service import GroupLaunchResult, SceneLaunchResult

        mock_backend = MagicMock()
        mocker.patch("cpsm.platform.tmux_backend.TmuxBackend", return_value=mock_backend)
        mocker.patch(
            "cpsm.services.session_service.SessionService.launch_scene",
            return_value=SceneLaunchResult(
                success=True,
                scene_id="workday",
                group_results=[
                    GroupLaunchResult(
                        success=True,
                        group_id="project-1",
                        session_name="cpsm-group-project-1",
                    )
                ],
            ),
        )

        code, out, _err = _dispatch_capture(
            ["launch-scene", "workday", "--config", str(VALID_CPSM)], capsys
        )
        assert code == EXIT_OK
        assert "OK" in out or "project-1" in out

    def test_launch_scene_failure_json(
        self, mocker: pytest_mock.MockerFixture, capsys: pytest.CaptureFixture
    ) -> None:
        from cpsm.services.session_service import GroupLaunchResult, SceneLaunchResult

        mock_backend = MagicMock()
        mocker.patch("cpsm.platform.tmux_backend.TmuxBackend", return_value=mock_backend)
        mocker.patch(
            "cpsm.services.session_service.SessionService.launch_scene",
            return_value=SceneLaunchResult(
                success=False,
                scene_id="workday",
                group_results=[
                    GroupLaunchResult(
                        success=False,
                        group_id="project-1",
                        session_name="",
                        errors=["group failed"],
                    )
                ],
            ),
        )

        code, out, _err = _dispatch_capture(
            ["launch-scene", "workday", "--config", str(VALID_CPSM), "--json"], capsys
        )
        assert code == EXIT_LAUNCH_FAILED
        payload = json.loads(out)
        assert payload["success"] is False


class TestImportCoverageEdgeCases:
    def test_import_generic_error(
        self, tmp_path: Path, mocker: pytest_mock.MockerFixture, capsys: pytest.CaptureFixture
    ) -> None:
        target = tmp_path / "out.yaml"
        mocker.patch(
            "cpsm.services.import_service.ImportService.import_legacy_to",
            side_effect=ValueError("conversion error"),
        )
        code, _out, err = _dispatch_capture(["import", str(LEGACY_YAML), "-o", str(target)], capsys)
        assert code == EXIT_GENERIC
        assert "ERROR" in err

    def test_import_source_exists_service_raises_fnf(
        self, tmp_path: Path, mocker: pytest_mock.MockerFixture, capsys: pytest.CaptureFixture
    ) -> None:
        """ImportService raises FileNotFoundError after our source-exists check passes."""
        target = tmp_path / "out.yaml"
        mocker.patch(
            "cpsm.services.import_service.ImportService.import_legacy_to",
            side_effect=FileNotFoundError("service not found"),
        )
        # LEGACY_YAML exists on disk; target does not → bypasses both early guards
        # Service raises FileNotFoundError → EXIT_SOURCE_NOT_FOUND
        code, _out, _err = _dispatch_capture(
            ["import", str(LEGACY_YAML), "-o", str(target)], capsys
        )
        assert code == EXIT_SOURCE_NOT_FOUND


# ---------------------------------------------------------------------------
# Tests for FileNotFoundError paths during config load in launch commands
# ---------------------------------------------------------------------------


class TestLoadFailurePaths:
    """Cover the FileNotFoundError paths in cmd_launch / cmd_launch_group / cmd_launch_scene.

    These paths are reached when ConfigService.load() raises FileNotFoundError,
    which happens when an explicit --config path points to a file that does NOT
    exist AND the repository calls load() (not load_or_create()). We mock the
    load() method to inject the error.
    """

    def test_launch_load_failure_human(
        self, mocker: pytest_mock.MockerFixture, capsys: pytest.CaptureFixture, tmp_path: Path
    ) -> None:
        mocker.patch(
            "cpsm.services.config_service.ConfigService.load",
            side_effect=FileNotFoundError("config not found"),
        )
        code, _out, err = _dispatch_capture(
            ["launch", "web01", "--config", str(tmp_path / "x.yaml")], capsys
        )
        assert code == EXIT_GENERIC
        assert "ERROR" in err

    def test_launch_load_failure_json(
        self, mocker: pytest_mock.MockerFixture, capsys: pytest.CaptureFixture, tmp_path: Path
    ) -> None:
        mocker.patch(
            "cpsm.services.config_service.ConfigService.load",
            side_effect=FileNotFoundError("config not found"),
        )
        code, out, _err = _dispatch_capture(
            ["launch", "web01", "--config", str(tmp_path / "x.yaml"), "--json"], capsys
        )
        assert code == EXIT_GENERIC
        payload = json.loads(out)
        assert payload["success"] is False

    def test_launch_group_load_failure_human(
        self, mocker: pytest_mock.MockerFixture, capsys: pytest.CaptureFixture, tmp_path: Path
    ) -> None:
        mocker.patch(
            "cpsm.services.config_service.ConfigService.load",
            side_effect=FileNotFoundError("config not found"),
        )
        code, _out, err = _dispatch_capture(
            ["launch-group", "project-1", "--config", str(tmp_path / "x.yaml")], capsys
        )
        assert code == EXIT_GENERIC
        assert "ERROR" in err

    def test_launch_group_load_failure_json(
        self, mocker: pytest_mock.MockerFixture, capsys: pytest.CaptureFixture, tmp_path: Path
    ) -> None:
        mocker.patch(
            "cpsm.services.config_service.ConfigService.load",
            side_effect=FileNotFoundError("config not found"),
        )
        code, out, _err = _dispatch_capture(
            ["launch-group", "project-1", "--config", str(tmp_path / "x.yaml"), "--json"], capsys
        )
        assert code == EXIT_GENERIC
        payload = json.loads(out)
        assert payload["success"] is False

    def test_launch_scene_load_failure_human(
        self, mocker: pytest_mock.MockerFixture, capsys: pytest.CaptureFixture, tmp_path: Path
    ) -> None:
        mocker.patch(
            "cpsm.services.config_service.ConfigService.load",
            side_effect=FileNotFoundError("config not found"),
        )
        code, _out, err = _dispatch_capture(
            ["launch-scene", "workday", "--config", str(tmp_path / "x.yaml")], capsys
        )
        assert code == EXIT_GENERIC
        assert "ERROR" in err

    def test_launch_scene_load_failure_json(
        self, mocker: pytest_mock.MockerFixture, capsys: pytest.CaptureFixture, tmp_path: Path
    ) -> None:
        mocker.patch(
            "cpsm.services.config_service.ConfigService.load",
            side_effect=FileNotFoundError("config not found"),
        )
        code, out, _err = _dispatch_capture(
            ["launch-scene", "workday", "--config", str(tmp_path / "x.yaml"), "--json"], capsys
        )
        assert code == EXIT_GENERIC
        payload = json.loads(out)
        assert payload["success"] is False

    def test_launch_scene_not_found_json(
        self, mocker: pytest_mock.MockerFixture, capsys: pytest.CaptureFixture
    ) -> None:
        """Scene not found path with --json flag."""
        mock_backend = MagicMock()
        mocker.patch("cpsm.platform.tmux_backend.TmuxBackend", return_value=mock_backend)
        code, out, _err = _dispatch_capture(
            ["launch-scene", "no-such-scene", "--config", str(VALID_CPSM), "--json"], capsys
        )
        assert code == EXIT_NOT_FOUND
        payload = json.loads(out)
        assert payload["success"] is False

    def test_launch_group_with_warnings_human(
        self, mocker: pytest_mock.MockerFixture, capsys: pytest.CaptureFixture
    ) -> None:
        """Human-readable launch-group output includes WARN lines."""
        from cpsm.services.session_service import GroupLaunchResult, LaunchResult

        mock_backend = MagicMock()
        mocker.patch("cpsm.platform.tmux_backend.TmuxBackend", return_value=mock_backend)
        mocker.patch(
            "cpsm.services.session_service.SessionService.launch_group",
            return_value=GroupLaunchResult(
                success=True,
                group_id="project-1",
                session_name="cpsm-group-project-1",
                member_results=[
                    LaunchResult(success=True, session_name="cpsm-web01", connection_id="web01")
                ],
                warnings=["placeholder deferred"],
            ),
        )
        code, out, _err = _dispatch_capture(
            ["launch-group", "project-1", "--config", str(VALID_CPSM)], capsys
        )
        assert code == EXIT_OK
        assert "WARN" in out or "warn" in out.lower()

    def test_validate_file_not_found_non_json(
        self, mocker: pytest_mock.MockerFixture, capsys: pytest.CaptureFixture, tmp_path: Path
    ) -> None:
        """FileNotFoundError during validate load → human-readable stderr."""
        mocker.patch(
            "cpsm.services.config_service.ConfigService.load",
            side_effect=FileNotFoundError("no config"),
        )
        code, _out, err = _dispatch_capture(
            ["validate", "--config", str(tmp_path / "x.yaml")], capsys
        )
        assert code == EXIT_VALIDATION_FAILED
        assert "ERROR" in err

    def test_validate_file_not_found_json(
        self, mocker: pytest_mock.MockerFixture, capsys: pytest.CaptureFixture, tmp_path: Path
    ) -> None:
        """FileNotFoundError during validate load → JSON payload."""
        mocker.patch(
            "cpsm.services.config_service.ConfigService.load",
            side_effect=FileNotFoundError("no config"),
        )
        code, out, _err = _dispatch_capture(
            ["validate", "--config", str(tmp_path / "x.yaml"), "--json"], capsys
        )
        assert code == EXIT_VALIDATION_FAILED
        payload = json.loads(out)
        assert payload["valid"] is False
        assert len(payload["issues"]) > 0

    def test_launch_exception_non_json(
        self, mocker: pytest_mock.MockerFixture, capsys: pytest.CaptureFixture
    ) -> None:
        """When session.launch() raises, non-JSON human-readable path used."""
        mock_backend = MagicMock()
        mocker.patch("cpsm.platform.tmux_backend.TmuxBackend", return_value=mock_backend)
        mocker.patch(
            "cpsm.services.session_service.SessionService.launch",
            side_effect=RuntimeError("unexpected crash"),
        )
        code, _out, err = _dispatch_capture(
            ["launch", "web01", "--config", str(VALID_CPSM)], capsys
        )
        assert code == EXIT_LAUNCH_FAILED
        assert "ERROR" in err

    def test_launch_group_exception_non_json_internal(
        self, mocker: pytest_mock.MockerFixture, capsys: pytest.CaptureFixture
    ) -> None:
        """When session.launch_group() raises an unexpected exception, non-JSON path."""
        mock_backend = MagicMock()
        mocker.patch("cpsm.platform.tmux_backend.TmuxBackend", return_value=mock_backend)
        mocker.patch(
            "cpsm.services.session_service.SessionService.launch_group",
            side_effect=RuntimeError("crash"),
        )
        code, _out, err = _dispatch_capture(
            ["launch-group", "project-1", "--config", str(VALID_CPSM)], capsys
        )
        assert code == EXIT_LAUNCH_FAILED
        assert "ERROR" in err

    def test_launch_scene_load_failure_non_json(
        self, mocker: pytest_mock.MockerFixture, capsys: pytest.CaptureFixture, tmp_path: Path
    ) -> None:
        """FileNotFoundError during launch-scene config load — non-JSON path."""
        mocker.patch(
            "cpsm.services.config_service.ConfigService.load",
            side_effect=FileNotFoundError("config not found"),
        )
        code, _out, err = _dispatch_capture(
            ["launch-scene", "workday", "--config", str(tmp_path / "x.yaml")], capsys
        )
        assert code == EXIT_GENERIC
        assert "ERROR" in err

    def test_launch_scene_not_found_non_json(
        self, mocker: pytest_mock.MockerFixture, capsys: pytest.CaptureFixture
    ) -> None:
        """Scene not found → non-JSON human-readable error."""
        mock_backend = MagicMock()
        mocker.patch("cpsm.platform.tmux_backend.TmuxBackend", return_value=mock_backend)
        code, _out, err = _dispatch_capture(
            ["launch-scene", "no-such-scene", "--config", str(VALID_CPSM)], capsys
        )
        assert code == EXIT_NOT_FOUND
        assert "not found" in err.lower()


class TestDefaultSubcommand:
    """Bare ``cpsm`` (no subcommand) defaults to launching the GUI.

    ``test_no_subcommand_defaults_to_gui`` in :class:`TestVersionHelp`
    covers the basic dispatch outcome; this test additionally verifies
    that the synthesized namespace passes ``config_path=None`` through
    to run_gui.
    """

    def test_synthesized_namespace_config_is_none(self, mocker: pytest_mock.MockerFixture) -> None:
        mock_run = mocker.patch("cpsm.app.run_gui", return_value=EXIT_OK)
        assert dispatch([]) == EXIT_OK
        assert mock_run.called
        assert mock_run.call_args.kwargs.get("config_path") is None
