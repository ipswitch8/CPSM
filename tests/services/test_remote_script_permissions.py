# -*- coding: utf-8 -*-
"""
Permission contract for the helper script claude-remote uploads to the target.

The launcher writes ``/tmp/cpsm-remote-<conn>.sh`` on the remote host and then
runs it, optionally via ``su - <sudo_user>``.  ``/tmp`` is world-writable and
shared with every other account on that host, and the helper names the project
folder and the claude options for the session, so it must never be readable by
"other".  The sudo account reaches it through the GROUP bit instead.

These tests do not trust the template's text.  Where the property is about what
the shell actually DOES, they extract ``_secure_remote_cmd`` from the rendered
output and execute it against real files, then stat the result.  A template
that merely mentions 0750 while leaving a path that widens the mode would pass
a grep-based test and fail these.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import tempfile
import types
from pathlib import Path

import pytest

from cpsm.services.template_service import TemplateService

# ---------------------------------------------------------------------------
# Fixture connections
# ---------------------------------------------------------------------------


def _make_remote_conn(
    *,
    conn_id: str = "my-remote",
    host: str = "example.com",
    user: str = "deploy",
    sudo_user: str = "appuser",
    project_folder: str = "/opt/myproject",
    claude_options: str = "--resume",
) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        id=conn_id,
        launcher_profile="claude-remote",
        host=host,
        user=user,
        sudo_user=sudo_user,
        project_folder=project_folder,
        claude_options=claude_options,
        identity_file=None,
        custom_template_id=None,
    )


def _make_settings() -> types.SimpleNamespace:
    return types.SimpleNamespace(
        default_ssh_options="-o ConnectTimeout=10",
        default_claude_options="--resume",
    )


def _render(**kwargs) -> str:
    return TemplateService().render(
        "claude-remote", _make_remote_conn(**kwargs), settings=_make_settings()
    )


# ---------------------------------------------------------------------------
# Executing the emitted snippet for real
# ---------------------------------------------------------------------------

_FN_RE = re.compile(r"^_secure_remote_cmd\(\) \{.*?^\}$", re.S | re.M)


def _extract_secure_fn(rendered: str) -> str:
    """Pull _secure_remote_cmd out of the rendered launcher."""
    match = _FN_RE.search(rendered)
    assert match is not None, (
        "_secure_remote_cmd() not found in the rendered claude-remote script. "
        "If it was renamed, update this test — do not delete it: it is the only "
        "check that the remote helper is actually locked down."
    )
    return match.group(0)


def _emit_secure_cmd(rendered: str, script_path: str, sudo_user: str) -> str:
    """Return the remote command string the launcher would send over SSH."""
    harness = (
        f"{_extract_secure_fn(rendered)}\n"
        f'_REMOTE_SCRIPT={_shq(script_path)}\n'
        f'_SUDO_USER={_shq(sudo_user)}\n'
        "_secure_remote_cmd\n"
    )
    result = subprocess.run(
        ["bash", "-c", harness],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
    )
    assert result.returncode == 0, f"emitting the snippet failed: {result.stderr}"
    return result.stdout


def _shq(value: str) -> str:
    import shlex

    return shlex.quote(value)


def _run_secure_cmd(
    command: str, shell: str = "bash"
) -> subprocess.CompletedProcess[str]:
    """Execute the emitted remote command under *shell*, as sshd's login shell would."""
    return subprocess.run(
        [shell, "-c", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
    )


def _mode(path: str) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


@pytest.fixture()
def victim(tmp_path: Path):
    """A file starting at 0777 — the mode the fix must narrow, never widen."""
    target = tmp_path / "cpsm-remote-fixture.sh"
    target.write_text("#!/bin/bash\necho hi\n", encoding="utf-8")
    target.chmod(0o777)
    return target


def _account_exists(name: str) -> bool:
    import pwd

    try:
        pwd.getpwnam(name)
    except KeyError:
        return False
    return True


def _own_group_name() -> str:
    import grp

    return grp.getgrgid(os.getgid()).gr_name


# ---------------------------------------------------------------------------
# 1. Static contract — no world bits anywhere in any rendered launcher
# ---------------------------------------------------------------------------


class TestNoWorldReadableModes:
    def test_rendered_remote_never_sets_0755(self) -> None:
        assert "0755" not in _render(), (
            "claude-remote still contains a 0755 mode. The uploaded helper lives in "
            "world-writable /tmp and must not be readable by other accounts."
        )

    def test_no_builtin_template_grants_other_any_bit(self) -> None:
        """Every chmod in every built-in template must leave 'other' at 0.

        Reads the shipped template sources rather than one rendered profile, so
        a future template that reintroduces a world-readable mode is caught even
        if nothing else in this file knows about it.
        """
        from importlib.resources import files

        svc = TemplateService()
        offenders: list[tuple[str, str]] = []
        for profile in svc.list_builtin():
            raw = (
                files("cpsm.resources.launcher_templates")
                .joinpath(f"{profile}.sh")
                .read_text(encoding="utf-8")
            )
            for match in re.finditer(r"chmod\s+(?:-\w+\s+)?(\d{3,4})", raw):
                mode = match.group(1)
                if int(mode[-1]) != 0:
                    offenders.append((profile, mode))
        assert offenders == [], (
            f"templates set a non-zero 'other' digit: {offenders}. "
            "Anything in shared /tmp must keep other=0."
        )

    def test_both_upload_paths_restrict_the_umask(self) -> None:
        """scp path pre-creates under 077; the cat fallback sets it inline."""
        rendered = _render()
        assert rendered.count("umask 077") >= 3, (
            "expected umask 077 on the local write, the remote pre-create and the "
            "cat fallback. Without it the helper is briefly world-readable in /tmp "
            "between landing and being chmod'ed."
        )

    def test_securing_failure_is_not_swallowed(self) -> None:
        """A failed lockdown must abort the launch, not fall through to exec."""
        rendered = _render()
        secure_call = [
            line
            for line in rendered.splitlines()
            if "_secure_remote_cmd)" in line and "ssh" in line
        ]
        assert secure_call, "no ssh invocation of _secure_remote_cmd found"
        for line in secure_call:
            assert "|| true" not in line, (
                f"the securing step swallows its own failure: {line!r}. "
                "Suppressed, this either strands the sudo account unable to read "
                "the helper or leaves it at the upload's mode, silently."
            )


# ---------------------------------------------------------------------------
# 2. Behavioural contract — run the emitted snippet and stat the result
# ---------------------------------------------------------------------------


class TestEmittedSnippetBehaviour:
    def test_sudo_user_gets_group_access_at_0750(self, victim: Path) -> None:
        """The documented end state: 0750, group retargeted to the sudo account."""
        group = _own_group_name()
        cmd = _emit_secure_cmd(_render(sudo_user=group), str(victim), group)
        result = _run_secure_cmd(cmd)

        assert result.returncode == 0, f"snippet failed: {result.stderr}"
        assert _mode(str(victim)) == 0o750, (
            f"expected 0750, got {_mode(str(victim)):04o}"
        )
        import grp

        assert grp.getgrgid(os.stat(victim).st_gid).gr_name == group

    def test_no_sudo_user_still_drops_world_bits(self, victim: Path) -> None:
        """With no privilege drop the owner bits suffice — but 'other' still goes."""
        cmd = _emit_secure_cmd(_render(sudo_user=""), str(victim), "")
        result = _run_secure_cmd(cmd)

        assert result.returncode == 0, f"snippet failed: {result.stderr}"
        assert _mode(str(victim)) == 0o750

    def test_unresolvable_account_fails_closed(self, victim: Path) -> None:
        """No group, no ACL — abort loudly rather than widen the mode."""
        cmd = _emit_secure_cmd(
            _render(sudo_user="nosuchacct12345"), str(victim), "nosuchacct12345"
        )
        result = _run_secure_cmd(cmd)

        assert result.returncode != 0, (
            "an unresolvable sudo account must fail, not silently leave the helper "
            "unreachable or world-readable"
        )
        assert "ERROR" in result.stderr
        assert _mode(str(victim)) & 0o007 == 0, (
            f"failure path left 'other' bits set: {_mode(str(victim)):04o}"
        )

    @pytest.mark.parametrize(
        "payload",
        [
            "$(touch {sentinel})",
            "`touch {sentinel}`",
            "a;touch {sentinel};b",
            "a b'; touch {sentinel} #",
            "$(touch {sentinel})\nrm -rf /",
        ],
    )
    def test_adversarial_sudo_user_is_inert(
        self, victim: Path, tmp_path: Path, payload: str
    ) -> None:
        """sudo_user reaches an extra shell layer — it must arrive as data."""
        sentinel = tmp_path / "pwned"
        value = payload.format(sentinel=sentinel)

        cmd = _emit_secure_cmd(_render(sudo_user=value), str(victim), value)
        _run_secure_cmd(cmd)

        assert not sentinel.exists(), (
            f"sudo_user payload {value!r} executed — the printf '%q' re-quoting "
            "for the remote shell layer is not holding."
        )
        assert _mode(str(victim)) & 0o007 == 0, (
            "an injection attempt widened the mode; the failure path must narrow "
            f"it, got {_mode(str(victim)):04o}"
        )

    @pytest.mark.skipif(shutil.which("dash") is None, reason="dash not installed")
    def test_snippet_works_under_a_posix_sh_login_shell(self, victim: Path) -> None:
        """sshd runs the command with the account's login shell, not always bash."""
        group = _own_group_name()
        cmd = _emit_secure_cmd(_render(sudo_user=group), str(victim), group)
        result = _run_secure_cmd(cmd, shell="dash")

        assert result.returncode == 0, f"snippet failed under dash: {result.stderr}"
        assert _mode(str(victim)) == 0o750


# ---------------------------------------------------------------------------
# 3. The fallback chain is ordered, and reaches ACLs before giving up
# ---------------------------------------------------------------------------


class TestFallbackChain:
    def test_chain_is_tried_in_narrowing_order(self) -> None:
        """named group -> primary group -> ACL -> loud failure."""
        cmd = _emit_secure_cmd(_render(sudo_user="appuser"), "/tmp/x.sh", "appuser")
        positions = [cmd.index(token) for token in ("getent group", "id -gn", "setfacl", "exit 1")]
        assert positions == sorted(positions), (
            f"fallback steps are out of order in the emitted command: {cmd!r}"
        )

    def test_no_branch_reopens_the_mode(self) -> None:
        """Every chmod in the emitted snippet keeps 'other' at 0."""
        for sudo_user in ("appuser", ""):
            cmd = _emit_secure_cmd(_render(sudo_user=sudo_user), "/tmp/x.sh", sudo_user)
            modes = re.findall(r"chmod\s+(\d{3,4})", cmd)
            assert modes, f"no chmod found in emitted snippet for sudo_user={sudo_user!r}"
            for mode in modes:
                assert int(mode[-1]) == 0, (
                    f"emitted snippet sets mode {mode} for sudo_user={sudo_user!r}, "
                    "granting access to 'other'"
                )

    def test_acl_is_attempted_before_failing(self) -> None:
        """An unprivileged owner cannot chgrp, but can always set an ACL."""
        cmd = _emit_secure_cmd(_render(sudo_user="appuser"), "/tmp/x.sh", "appuser")
        assert "setfacl" in cmd, (
            "no ACL fallback: an SSH user who is not a member of the sudo account's "
            "group would be left with no way to grant access except world-readable."
        )
        assert "r-x" in cmd, "the ACL must grant read+execute only, not write"

    @pytest.mark.skipif(
        shutil.which("setfacl") is None or shutil.which("getfacl") is None,
        reason="ACL tools not installed",
    )
    def test_acl_branch_actually_grants_access_when_chgrp_cannot(
        self, victim: Path
    ) -> None:
        """Exercise the ACL branch for real, not just its presence in the text.

        Picks an account this test user is not a group member of, so ``chgrp``
        genuinely fails and control reaches ``setfacl``.  Asserting only that
        the string "setfacl" appears would still pass against a branch that had
        been disabled, which is exactly the regression worth catching.
        """
        import grp
        import pwd

        my_groups = {g.gr_name for g in grp.getgrall() if os.getgid() == g.gr_gid}
        my_groups |= {grp.getgrgid(gid).gr_name for gid in os.getgroups()}

        account = next(
            (
                name
                for name in ("daemon", "bin", "nobody", "games")
                if _account_exists(name) and name not in my_groups
            ),
            None,
        )
        if account is None:
            pytest.fail(
                "no unrelated system account available to exercise the ACL branch; "
                "this test needs one to prove the fallback is live"
            )

        cmd = _emit_secure_cmd(_render(sudo_user=account), str(victim), account)
        result = _run_secure_cmd(cmd)
        assert result.returncode == 0, (
            f"the ACL fallback did not rescue the {account!r} case: {result.stderr}"
        )

        acl = subprocess.run(
            ["getfacl", "-c", str(victim)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
        ).stdout
        assert f"user:{account}:r-x" in acl, (
            f"expected an r-x ACL entry for {account!r}, got:\n{acl}"
        )
        assert "other::---" in acl, f"the ACL path left 'other' with access:\n{acl}"
        assert _mode(str(victim)) & 0o007 == 0
