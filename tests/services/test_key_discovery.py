# -*- coding: utf-8 -*-
"""Tests for KeyDiscoveryService — SSH key candidate ranking for a host.

Security assertions
-------------------
- Private key file *contents* are never opened for reading — only
  ``*.pub`` files and ``~/.ssh/config`` may be opened. Verified explicitly by
  wrapping ``Path.open`` and recording every path opened.
- All fixtures use a ``tmp_path`` fake ssh dir; no test reads the real
  ``~/.ssh``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cpsm.services.key_discovery import KeyCandidate, KeyDiscoveryService

# ---------------------------------------------------------------------------
# Isolation guard
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_real_ssh_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Fail loudly if any test in this module reaches the real ``~/.ssh``.

    Every test here is supposed to inject ``ssh_dir=tmp_path``. Nothing
    previously enforced that: a future test that simply forgot the keyword
    would silently fall through to ``Path.home() / ".ssh"`` and scan the
    developer's actual keys. It would probably even pass, for the wrong
    reason, and would behave differently on another machine.

    ``KeyDiscoveryService`` only calls ``Path.home()`` on that fallback path,
    so making it raise turns a silent escape into an immediate, obvious
    failure. Mirrors ``_no_system_key_discovery`` in
    ``tests/ui/test_ssh_key_manager.py``.
    """

    def _forbidden() -> Path:
        raise AssertionError(
            "A test reached Path.home() — KeyDiscoveryService fell back to the "
            "real ~/.ssh. Pass ssh_dir=tmp_path explicitly."
        )

    monkeypatch.setattr(Path, "home", staticmethod(_forbidden))

    # Sealing Path.home() alone is NOT enough, and assuming it was hid a real
    # defect: Path.expanduser() resolves "~" from the HOME environment
    # variable, never from Path.home(). An ssh_config "IdentityFile
    # ~/.ssh/somekey" therefore reached the developer's ACTUAL home directory,
    # and the tilde test passed only because that machine happened to own a
    # key by that name — it failed outright under an empty HOME.
    #
    # Point HOME at an empty directory so tilde expansion resolves somewhere
    # inert. A test that genuinely needs tilde semantics sets HOME itself,
    # which is explicit and hermetic.
    sealed_home = tmp_path_factory.mktemp("sealed-home")
    monkeypatch.setenv("HOME", str(sealed_home))
    monkeypatch.setenv("USERPROFILE", str(sealed_home))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_key_pair(ssh_dir: Path, name: str, comment: str = "") -> tuple[Path, Path]:
    """Write a fake private+public key pair. Private file content is a
    placeholder — no test should ever cause it to be opened for reading."""
    private_path = ssh_dir / name
    private_path.write_text("PRIVATE KEY MATERIAL - DO NOT OPEN\n", encoding="utf-8")
    public_path = ssh_dir / f"{name}.pub"
    public_path.write_text(
        f"ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIfakefakefakefake {comment}\n",
        encoding="utf-8",
    )
    return private_path, public_path


def _write_private_only(ssh_dir: Path, name: str) -> Path:
    private_path = ssh_dir / name
    private_path.write_text("PRIVATE KEY MATERIAL - DO NOT OPEN\n", encoding="utf-8")
    return private_path


def _write_pub_only(ssh_dir: Path, name: str, comment: str) -> Path:
    public_path = ssh_dir / f"{name}.pub"
    public_path.write_text(
        f"ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIfakefakefakefake {comment}\n",
        encoding="utf-8",
    )
    return public_path


# ---------------------------------------------------------------------------
# Basic matching
# ---------------------------------------------------------------------------


def test_match_by_pub_comment_user_at_host(tmp_path: Path) -> None:
    _write_key_pair(tmp_path, "utility", comment="root@192.0.2.44")
    svc = KeyDiscoveryService(ssh_dir=tmp_path)

    results = svc.discover("192.0.2.44", user="root")

    assert len(results) == 1
    assert results[0].source == "pub_comment_user_host"
    assert results[0].private_path == tmp_path / "utility"
    assert results[0].comment == "root@192.0.2.44"


def test_match_by_host_alone_when_no_user_supplied(tmp_path: Path) -> None:
    _write_key_pair(tmp_path, "dash", comment="root@192.0.2.53")
    svc = KeyDiscoveryService(ssh_dir=tmp_path)

    results = svc.discover("192.0.2.53")

    assert len(results) == 1
    assert results[0].source == "pub_comment_host"
    assert results[0].private_path == tmp_path / "dash"


# ---------------------------------------------------------------------------
# The substring trap
# ---------------------------------------------------------------------------


def test_substring_trap_short_host_not_matched_by_longer_ip(tmp_path: Path) -> None:
    # Key commented for .44 must not be returned when searching for .4
    _write_key_pair(tmp_path, "utility", comment="root@192.0.2.44")
    svc = KeyDiscoveryService(ssh_dir=tmp_path)

    results = svc.discover("192.0.2.4")

    assert results == []


def test_substring_trap_long_host_not_matched_by_shorter_ip(tmp_path: Path) -> None:
    # Key commented for .4 must not be returned when searching for .44
    _write_key_pair(tmp_path, "short", comment="root@192.0.2.4")
    svc = KeyDiscoveryService(ssh_dir=tmp_path)

    results = svc.discover("192.0.2.44")

    assert results == []


def test_substring_trap_domain_suffix(tmp_path: Path) -> None:
    _write_key_pair(tmp_path, "example_key", comment="root@example.com")
    svc = KeyDiscoveryService(ssh_dir=tmp_path)

    results = svc.discover("foo.example.com")

    assert results == []


def test_domain_exact_match_still_works(tmp_path: Path) -> None:
    _write_key_pair(tmp_path, "example_key", comment="root@example.com")
    svc = KeyDiscoveryService(ssh_dir=tmp_path)

    results = svc.discover("example.com")

    assert len(results) == 1
    assert results[0].private_path == tmp_path / "example_key"


# ---------------------------------------------------------------------------
# ssh_config
# ---------------------------------------------------------------------------


def test_match_via_ssh_config_host_stanza_with_tilde_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ``~``-prefixed IdentityFile must expand against HOME.

    This test used to be a false green. It wrote its fake key into tmp_path
    but pointed IdentityFile at ``~/.ssh/vendor_hosting_key``, and
    expanduser() resolved that against the developer's REAL home — where a
    key of exactly that name happened to exist. It therefore asserted nothing
    about tmp_path at all, and failed the moment HOME was empty.

    Now HOME is an explicit fake containing its own .ssh, so the assertion
    that the resolved path lives under that fake home is meaningful.
    """
    fake_home = tmp_path / "home"
    home_ssh = fake_home / ".ssh"
    home_ssh.mkdir(parents=True)
    expected = _write_private_only(home_ssh, "vendor_hosting_key")

    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))

    ssh_dir = tmp_path / "ssh"
    ssh_dir.mkdir()
    (ssh_dir / "config").write_text(
        "Host ssh.vendor.example.com\n"
        "    IdentityFile ~/.ssh/vendor_hosting_key\n",
        encoding="utf-8",
    )
    svc = KeyDiscoveryService(ssh_dir=ssh_dir)

    results = svc.discover("ssh.vendor.example.com")

    assert len(results) == 1
    assert results[0].source == "ssh_config"
    # The point of the test: it resolved under the FAKE home, not anywhere else.
    assert results[0].private_path == expected
    assert fake_home in results[0].private_path.parents


def test_match_via_ssh_config_relative_identity_file(tmp_path: Path) -> None:
    _write_private_only(tmp_path, "vendorhost_key")
    config = tmp_path / "config"
    config.write_text(
        "Host ssh.vendor.example.com\n"
        "    IdentityFile vendorhost_key\n",
        encoding="utf-8",
    )
    svc = KeyDiscoveryService(ssh_dir=tmp_path)

    results = svc.discover("ssh.vendor.example.com")

    assert len(results) == 1
    assert results[0].source == "ssh_config"
    assert results[0].private_path == tmp_path / "vendorhost_key"


def test_ssh_config_hostname_directive_matches(tmp_path: Path) -> None:
    _write_private_only(tmp_path, "bastion_key")
    config = tmp_path / "config"
    config.write_text(
        "Host bastion\n"
        "    HostName 10.20.30.40\n"
        "    IdentityFile bastion_key\n",
        encoding="utf-8",
    )
    svc = KeyDiscoveryService(ssh_dir=tmp_path)

    results = svc.discover("10.20.30.40")

    assert len(results) == 1
    assert results[0].source == "ssh_config"


def test_ssh_config_outranks_pub_comment(tmp_path: Path) -> None:
    # Same host matched two ways: an ssh_config IdentityFile, and a
    # differently-named key with a matching .pub comment. ssh_config wins.
    _write_private_only(tmp_path, "config_key")
    (tmp_path / "config_key.pub").write_text(
        "ssh-ed25519 AAAA fake config_key\n", encoding="utf-8"
    )
    config = tmp_path / "config"
    config.write_text(
        "Host 192.0.2.44\n" "    IdentityFile config_key\n",
        encoding="utf-8",
    )
    _write_key_pair(tmp_path, "pub_only_match", comment="root@192.0.2.44")

    svc = KeyDiscoveryService(ssh_dir=tmp_path)
    results = svc.discover("192.0.2.44", user="root")

    assert len(results) == 2
    assert results[0].source == "ssh_config"
    assert results[0].private_path == tmp_path / "config_key"
    assert results[1].source == "pub_comment_user_host"


# ---------------------------------------------------------------------------
# No match / defaults
# ---------------------------------------------------------------------------


def test_no_match_returns_empty_when_no_defaults_present(tmp_path: Path) -> None:
    _write_key_pair(tmp_path, "utility", comment="root@192.0.2.44")
    svc = KeyDiscoveryService(ssh_dir=tmp_path)

    results = svc.discover("192.168.1.1")

    assert results == []


def test_no_match_returns_only_defaults_when_present(tmp_path: Path) -> None:
    _write_key_pair(tmp_path, "utility", comment="root@192.0.2.44")
    _write_private_only(tmp_path, "id_ed25519")

    svc = KeyDiscoveryService(ssh_dir=tmp_path)
    results = svc.discover("192.168.1.1")

    assert len(results) == 1
    assert results[0].source == "default"
    assert results[0].private_path == tmp_path / "id_ed25519"


# ---------------------------------------------------------------------------
# Private key absence
# ---------------------------------------------------------------------------


def test_pub_without_private_key_is_not_returned(tmp_path: Path) -> None:
    _write_pub_only(tmp_path, "orphan", comment="root@192.0.2.44")
    svc = KeyDiscoveryService(ssh_dir=tmp_path)

    results = svc.discover("192.0.2.44", user="root")

    assert results == []


def test_ssh_config_identity_file_missing_private_key_not_returned(tmp_path: Path) -> None:
    config = tmp_path / "config"
    config.write_text(
        "Host ghost.example.com\n" "    IdentityFile nonexistent_key\n",
        encoding="utf-8",
    )
    svc = KeyDiscoveryService(ssh_dir=tmp_path)

    results = svc.discover("ghost.example.com")

    assert results == []


# ---------------------------------------------------------------------------
# Ranking order with several matches
# ---------------------------------------------------------------------------


def test_ranking_order_with_several_matches(tmp_path: Path) -> None:
    # default present
    _write_private_only(tmp_path, "id_rsa")
    # pub host-only match
    _write_key_pair(tmp_path, "host_match", comment="someone@target.example.com")
    # pub user@host match (higher than host-only)
    _write_key_pair(tmp_path, "user_host_match", comment="alice@target.example.com")
    # ssh_config match (highest)
    _write_private_only(tmp_path, "config_match")
    config = tmp_path / "config"
    config.write_text(
        "Host target.example.com\n" "    IdentityFile config_match\n",
        encoding="utf-8",
    )

    svc = KeyDiscoveryService(ssh_dir=tmp_path)
    results = svc.discover("target.example.com", user="alice")

    sources_in_order = [c.source for c in results]
    assert sources_in_order == [
        "ssh_config",
        "pub_comment_user_host",
        "pub_comment_host",
        "default",
    ]
    # Scores strictly decreasing.
    scores = [c.score for c in results]
    assert scores == sorted(scores, reverse=True)
    assert len(scores) == len(set(scores))


# ---------------------------------------------------------------------------
# Malformed / unreadable files
# ---------------------------------------------------------------------------


def test_malformed_empty_pub_file_does_not_raise(tmp_path: Path) -> None:
    (tmp_path / "broken.pub").write_text("", encoding="utf-8")
    (tmp_path / "broken").write_text("PRIVATE", encoding="utf-8")
    # Also a .pub with garbage/no comment field.
    (tmp_path / "garbage.pub").write_text("not-a-valid-key-line\n", encoding="utf-8")
    (tmp_path / "garbage").write_text("PRIVATE", encoding="utf-8")

    svc = KeyDiscoveryService(ssh_dir=tmp_path)

    results = svc.discover("anyhost.example.com")

    assert results == []


def test_unreadable_pub_file_does_not_raise(tmp_path: Path) -> None:
    unreadable = tmp_path / "noaccess.pub"
    unreadable.write_text("ssh-ed25519 AAAA fake root@anyhost\n", encoding="utf-8")
    (tmp_path / "noaccess").write_text("PRIVATE", encoding="utf-8")
    unreadable.chmod(0o000)

    try:
        svc = KeyDiscoveryService(ssh_dir=tmp_path)
        # Should not raise even though the file cannot be read (best effort;
        # running as root in CI may still be able to read it, so we only
        # assert no exception propagates).
        svc.discover("anyhost")
    finally:
        unreadable.chmod(0o644)


def test_unreadable_ssh_config_does_not_raise(tmp_path: Path) -> None:
    config = tmp_path / "config"
    config.write_text("Host foo\n    IdentityFile bar\n", encoding="utf-8")
    config.chmod(0o000)

    try:
        svc = KeyDiscoveryService(ssh_dir=tmp_path)
        svc.discover("foo")
    finally:
        config.chmod(0o644)


# ---------------------------------------------------------------------------
# Security: private key contents are never read
# ---------------------------------------------------------------------------


def test_private_key_file_is_never_opened_for_reading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No path other than a ``.pub`` file or ``ssh_config`` may ever be
    passed to ``Path.open`` — proving discovery never reads private key
    material, only stats/checks existence of it."""
    _write_key_pair(tmp_path, "utility", comment="root@192.0.2.44")
    _write_private_only(tmp_path, "id_ed25519")
    config = tmp_path / "config"
    config.write_text(
        "Host ssh.vendor.example.com\n"
        "    IdentityFile ~/.ssh/utility\n",
        encoding="utf-8",
    )

    opened_paths: list[Path] = []
    original_open = Path.open

    def recording_open(self: Path, *args, **kwargs):  # type: ignore[no-untyped-def]
        opened_paths.append(self)
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", recording_open)

    svc = KeyDiscoveryService(ssh_dir=tmp_path)
    svc.discover("192.0.2.44", user="root")       # pub-comment path
    svc.discover("ssh.vendor.example.com")            # ssh_config path
    svc.discover("no-such-host.invalid")           # falls through to defaults

    # The recorder must actually have recorded something. Without this, the
    # loop below is vacuous: an empty list satisfies it trivially, so a
    # refactor that stopped routing reads through Path.open would leave this
    # test green while proving nothing at all.
    assert opened_paths, (
        "Path.open recorder captured nothing — the test can no longer see "
        "the reads it is supposed to be policing"
    )
    opened_names = {path.name for path in opened_paths}
    assert "utility.pub" in opened_names, "the .pub read path was not exercised"
    assert "config" in opened_names, "the ssh_config read path was not exercised"

    for path in opened_paths:
        assert path.name.endswith(".pub") or path.name == "config", (
            f"unexpected file opened for reading: {path}"
        )

    # Named explicitly: the private halves must never appear, including the
    # default key, whose discovery path the third discover() call above
    # exercises.
    for private_name in ("utility", "id_ed25519"):
        assert private_name not in opened_names, (
            f"private key {private_name!r} was opened for reading"
        )


# ---------------------------------------------------------------------------
# Dataclass sanity
# ---------------------------------------------------------------------------


def test_key_candidate_is_frozen(tmp_path: Path) -> None:
    candidate = KeyCandidate(
        private_path=tmp_path / "k",
        public_path=None,
        comment="",
        source="default",
        score=10,
    )
    with pytest.raises(AttributeError):
        candidate.score = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Module purity — an enforced boundary, not a docstring promise
# ---------------------------------------------------------------------------


def test_key_discovery_module_imports_nothing_impure() -> None:
    """``key_discovery`` must stay pure: no Qt, no subprocess, no network.

    The module's whole premise is that it is cheap, synchronous and safe to
    call from anywhere, including a UI thread. Phase-3 adds a network probe
    that verifies candidate keys against a live host, and the obvious wrong
    place to put it is right here next to the code that produced the
    candidates. Until this test existed, nothing would have caught that —
    purity was true only by inspection, and inspection does not run in CI.

    Parsing the AST rather than grepping means a lazily-imported
    ``subprocess`` inside a function body is caught too, which is exactly how
    such a dependency would most plausibly sneak in.
    """
    import ast

    forbidden = {"PySide6", "subprocess", "socket", "asyncio", "requests", "urllib"}

    source = (
        Path(__file__).resolve().parent.parent.parent
        / "cpsm"
        / "services"
        / "key_discovery.py"
    ).read_text(encoding="utf-8")

    offenders: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in forbidden:
                    offenders.append(f"line {node.lineno}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in forbidden:
                offenders.append(f"line {node.lineno}: from {node.module} import ...")

    assert not offenders, (
        "key_discovery.py must remain pure logic (no Qt, no subprocess, no "
        "network) — it is called synchronously from the UI and its tests "
        "assume it cannot execute or connect to anything. Found: "
        + "; ".join(offenders)
    )
