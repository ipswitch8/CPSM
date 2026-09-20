# -*- coding: utf-8 -*-
"""
Tests for SshBinary — flavor detection and argv normalization.

Spec: §8, §9.2
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cpsm.platform.ssh_binary import SshBinary

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_ssh(flavor: str) -> SshBinary:
    """Directly construct an SshBinary bypassing which()."""
    assert flavor in ("openssh", "plink")
    return SshBinary(
        binary="/usr/bin/ssh" if flavor == "openssh" else "/usr/bin/plink",
        flavor=flavor,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# detect()
# ---------------------------------------------------------------------------


def test_detect_openssh_preferred(monkeypatch: pytest.MonkeyPatch) -> None:
    """detect('auto') finds openssh when ssh is on PATH."""
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/ssh" if name == "ssh" else None)
    sb = SshBinary.detect(prefer="auto")
    assert sb.flavor == "openssh"
    assert sb.binary == "/usr/bin/ssh"


def test_detect_plink_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """detect('auto') falls back to plink when ssh is not on PATH."""
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/plink" if name == "plink" else None)
    sb = SshBinary.detect(prefer="auto")
    assert sb.flavor == "plink"


def test_detect_prefer_openssh_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/ssh" if name == "ssh" else None)
    sb = SshBinary.detect(prefer="openssh")
    assert sb.flavor == "openssh"


def test_detect_prefer_plink_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/plink" if name == "plink" else None)
    sb = SshBinary.detect(prefer="plink")
    assert sb.flavor == "plink"


def test_detect_raises_when_nothing_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: None)
    with pytest.raises(RuntimeError, match="No SSH binary found"):
        SshBinary.detect()


def test_detect_prefer_openssh_raises_when_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: None)
    with pytest.raises(RuntimeError, match="No OpenSSH binary"):
        SshBinary.detect(prefer="openssh")


def test_detect_prefer_plink_raises_when_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: None)
    with pytest.raises(RuntimeError, match="No plink binary"):
        SshBinary.detect(prefer="plink")


# ---------------------------------------------------------------------------
# build_argv — OpenSSH
# ---------------------------------------------------------------------------


def test_openssh_basic(monkeypatch: pytest.MonkeyPatch) -> None:
    sb = _make_ssh("openssh")
    argv = sb.build_argv(host="example.com", user="alice")
    assert argv[0] == "/usr/bin/ssh"
    assert "alice@example.com" in argv
    assert "-tt" in argv


def test_openssh_no_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    sb = _make_ssh("openssh")
    argv = sb.build_argv(host="example.com", user="alice", force_tty=False)
    assert "-tt" not in argv


def test_openssh_port_22_omitted() -> None:
    sb = _make_ssh("openssh")
    argv = sb.build_argv(host="h", user="u", port=22)
    assert "-p" not in argv
    assert "-P" not in argv


def test_openssh_port_nondefault() -> None:
    sb = _make_ssh("openssh")
    argv = sb.build_argv(host="h", user="u", port=2222)
    idx = argv.index("-p")
    assert argv[idx + 1] == "2222"


def test_openssh_identity_file() -> None:
    sb = _make_ssh("openssh")
    argv = sb.build_argv(host="h", user="u", identity_file=Path("/home/u/.ssh/id_ed25519"))
    idx = argv.index("-i")
    assert argv[idx + 1] == "/home/u/.ssh/id_ed25519"


def test_openssh_ssh_options() -> None:
    sb = _make_ssh("openssh")
    argv = sb.build_argv(
        host="h", user="u", ssh_options=["ConnectTimeout=10", "ServerAliveInterval=30"]
    )
    assert argv.count("-o") == 2
    assert "ConnectTimeout=10" in argv
    assert "ServerAliveInterval=30" in argv


def test_openssh_remote_command() -> None:
    sb = _make_ssh("openssh")
    argv = sb.build_argv(host="h", user="u", remote_command=["bash", "-c", "uptime"])
    assert "--" in argv
    cmd_start = argv.index("--") + 1
    assert argv[cmd_start:] == ["bash", "-c", "uptime"]


def test_openssh_no_remote_command() -> None:
    sb = _make_ssh("openssh")
    argv = sb.build_argv(host="h", user="u")
    assert "--" not in argv


# ---------------------------------------------------------------------------
# build_argv — plink
# ---------------------------------------------------------------------------


def test_plink_basic() -> None:
    sb = _make_ssh("plink")
    argv = sb.build_argv(host="example.com", user="bob")
    assert argv[0] == "/usr/bin/plink"
    assert "bob@example.com" in argv
    assert "-t" in argv
    assert "-tt" not in argv


def test_plink_no_tty() -> None:
    sb = _make_ssh("plink")
    argv = sb.build_argv(host="h", user="u", force_tty=False)
    assert "-t" not in argv


def test_plink_port_flag_is_uppercase_P() -> None:
    sb = _make_ssh("plink")
    argv = sb.build_argv(host="h", user="u", port=8022)
    assert "-P" in argv
    assert "-p" not in argv
    idx = argv.index("-P")
    assert argv[idx + 1] == "8022"


def test_plink_port_22_omitted() -> None:
    sb = _make_ssh("plink")
    argv = sb.build_argv(host="h", user="u", port=22)
    assert "-P" not in argv
    assert "-p" not in argv


def test_plink_identity_file() -> None:
    sb = _make_ssh("plink")
    argv = sb.build_argv(host="h", user="u", identity_file=Path("C:\\keys\\id_ed25519"))
    assert "-i" in argv
    idx = argv.index("-i")
    assert argv[idx + 1] == "C:\\keys\\id_ed25519"


# ---------------------------------------------------------------------------
# Combinatorial cycle test
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("flavor", ["openssh", "plink"])
@pytest.mark.parametrize("port", [22, 8022])
@pytest.mark.parametrize("identity", [None, Path("/k/id_ed25519")])
@pytest.mark.parametrize("options", [[], ["BatchMode=yes"]])
@pytest.mark.parametrize("force_tty", [True, False])
def test_build_argv_cycle(
    flavor: str,
    port: int,
    identity: Path | None,
    options: list[str],
    force_tty: bool,
) -> None:
    """All combinations of (flavor, port, identity, options, force_tty) must not raise."""
    sb = _make_ssh(flavor)
    argv = sb.build_argv(
        host="host.example.com",
        user="user",
        port=port,
        identity_file=identity,
        ssh_options=options,
        force_tty=force_tty,
    )
    # Invariants
    assert argv[0] in ("/usr/bin/ssh", "/usr/bin/plink")
    assert "user@host.example.com" in argv

    # Port flag correctness
    if port != 22:
        if flavor == "openssh":
            assert "-p" in argv
            assert "-P" not in argv
        else:
            assert "-P" in argv
            assert "-p" not in argv
    else:
        assert "-p" not in argv
        assert "-P" not in argv

    # TTY flag correctness
    if force_tty:
        if flavor == "openssh":
            assert "-tt" in argv
        else:
            assert "-t" in argv
    else:
        assert "-tt" not in argv

    # Identity
    if identity is not None:
        assert "-i" in argv
    # Options.  OpenSSH gains one additional -o for the IdentitiesOnly pin
    # whenever an identity file is supplied and the caller did not already set
    # IdentitiesOnly themselves.  plink never gains it.
    #
    # Kept as an exact equality rather than relaxed to >=: the point of this
    # invariant is to catch stray or duplicated options, which a >= would stop
    # doing.
    if options:
        expected_o = len(options)
        if (
            flavor == "openssh"
            and identity is not None
            and not any(
                opt.split("=")[0].strip().lower() == "identitiesonly"
                for opt in options
            )
        ):
            expected_o += 1
        assert argv.count("-o") == expected_o


# ---------------------------------------------------------------------------
# IdentitiesOnly pinning
# ---------------------------------------------------------------------------


def test_openssh_identity_file_pins_identities_only() -> None:
    """-i must be accompanied by -o IdentitiesOnly=yes.

    Without it ssh treats -i as an addition to the agent's keys and the default
    ~/.ssh/id_* files, so the configured key is not necessarily the key that
    authenticates.
    """
    sb = _make_ssh("openssh")
    argv = sb.build_argv(host="h", user="u", identity_file=Path("/k/id_ed25519"))
    assert "IdentitiesOnly=yes" in argv
    assert argv[argv.index("IdentitiesOnly=yes") - 1] == "-o"


def test_openssh_no_identity_file_does_not_pin() -> None:
    """No -i means no pin.

    IdentitiesOnly=yes with no -i restricts ssh to the default identity files
    and disables agent-held keys, breaking every connection that relies on
    them.  This is the most important regression in this module.
    """
    sb = _make_ssh("openssh")
    argv = sb.build_argv(host="h", user="u")
    assert not any("IdentitiesOnly" in a for a in argv)


def test_openssh_caller_identities_only_is_not_overridden() -> None:
    """An explicit caller setting wins and is not duplicated.

    OpenSSH uses the FIRST value obtained for each parameter and our injection
    would precede the caller's options, so an unconditional inject would
    silently defeat a deliberate IdentitiesOnly=no.
    """
    sb = _make_ssh("openssh")
    argv = sb.build_argv(
        host="h",
        user="u",
        identity_file=Path("/k/id_ed25519"),
        ssh_options=["IdentitiesOnly=no"],
    )
    assert "IdentitiesOnly=no" in argv
    assert "IdentitiesOnly=yes" not in argv
    assert argv.count("IdentitiesOnly=no") == 1


def test_openssh_caller_identities_only_yes_not_duplicated() -> None:
    """A caller that already asks for the pin gets exactly one."""
    sb = _make_ssh("openssh")
    argv = sb.build_argv(
        host="h",
        user="u",
        identity_file=Path("/k/id_ed25519"),
        ssh_options=["IdentitiesOnly=yes"],
    )
    assert argv.count("IdentitiesOnly=yes") == 1


@pytest.mark.parametrize(
    "spelling",
    ["identitiesonly=no", "IDENTITIESONLY=no", "IdentitiesOnly = no",
     "IdentitiesOnly no"],
)
def test_openssh_caller_option_detected_case_and_space_insensitively(
    spelling: str,
) -> None:
    """ssh_config keywords are case-insensitive and tolerate spaces.

    Detection must mirror ssh's own parsing, or a caller writing
    'identitiesonly=no' would go undetected and our 'yes' would be injected
    ahead of it and win.
    """
    sb = _make_ssh("openssh")
    argv = sb.build_argv(
        host="h",
        user="u",
        identity_file=Path("/k/id_ed25519"),
        ssh_options=[spelling],
    )
    assert "IdentitiesOnly=yes" not in argv


@pytest.mark.parametrize(
    "unrelated",
    ["IdentitiesOnlyExtra=yes", "ProxyCommand=echo IdentitiesOnly=yes"],
)
def test_openssh_lookalike_options_do_not_suppress_the_pin(
    unrelated: str,
) -> None:
    """Only a real IdentitiesOnly keyword counts as the caller's preference.

    A different keyword that merely starts with the same text, or a VALUE that
    happens to mention it, must not be mistaken for the caller having set it —
    that would silently drop the pin.
    """
    sb = _make_ssh("openssh")
    argv = sb.build_argv(
        host="h",
        user="u",
        identity_file=Path("/k/id_ed25519"),
        ssh_options=[unrelated],
    )
    assert "IdentitiesOnly=yes" in argv


def test_plink_identity_file_does_not_pin() -> None:
    """plink must not receive IdentitiesOnly.

    It is an OpenSSH ssh_config keyword with no PuTTY equivalent.  plink uses
    only the key given by -i plus Pageant and never scans a default key
    directory, so the failure mode does not arise; emitting the option could
    only break older plink builds that reject unknown options.
    """
    sb = _make_ssh("plink")
    argv = sb.build_argv(host="h", user="u", identity_file=Path("C:\\k\\id"))
    assert not any("IdentitiesOnly" in a for a in argv)
