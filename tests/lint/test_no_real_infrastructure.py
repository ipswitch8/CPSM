# -*- coding: utf-8 -*-
"""
tests/lint/test_no_real_infrastructure.py — CI lint: no real infrastructure
addresses, vendor identifiers, or developer home paths in tracked files.

Why this exists
---------------
CPSM is developed against real hosts, and its test fixtures are easy to seed
by copy-pasting from a working ``~/.cpsm.yaml``. That is exactly how this repo
previously acquired a fixture containing a verbatim export of a live
configuration: seventeen connections, every one ``root``, across four hosts
including two public IPs, with real project paths and internal repository
names. It sat in the tree from the initial commit until the repository was
prepared for publication.

Nothing caught it, because nothing was looking. Every check made before
publication was a one-time grep by a human or an agent, leaving no control
behind. This test is that control.

Why the forbidden strings are stored as hashes
----------------------------------------------
A denylist normally has to name what it denies — which would make this file
the one place in a public repository still publishing the vendor identifiers
that were scrubbed from everywhere else. The first version of this test did
exactly that, and the pre-publication history scan caught it.

So the vendor entries are SHA-256 digests. The guard tokenises each line and
hashes the tokens, which detects the strings without ever containing them.
That works because these are whole identifiers, not prefixes.

``test_the_digest_mechanism_catches_a_known_token`` proves the machinery with
a public canary token, so the mechanism is demonstrably live in CI. That the
digests correspond to the *real* scrubbed strings is proved out-of-band by a
fault-injection script kept outside the repository, precisely so that proof
does not reintroduce the strings.

What this test enforces
-----------------------
Tracked files may not contain:

* a routable IPv4 address;
* a token matching one of the forbidden digests;
* an absolute home directory belonging to a real account.

Use these instead, all reserved by RFC and unroutable:

* ``192.0.2.0/24``, ``198.51.100.0/24``, ``203.0.113.0/24`` (RFC 5737)
* ``example.com`` / ``example.org`` / ``example.net`` (RFC 2606)
* ``.invalid`` for a host that must never resolve (RFC 2606)

Private ranges (``10/8``, ``172.16/12``, ``192.168/16``) are permitted: they
are unroutable from the internet and appear legitimately in documentation
about LAN setups. They are not the disclosure risk; a public address beside a
username and a project path is.

Scope
-----
Only files tracked by git are checked, via ``git ls-files`` rather than a
filesystem walk, so an untracked working copy cannot hide from it. Known
limits, stated rather than left implicit: IPv4 only, no knowledge of arbitrary
hostnames, binary-suffixed files skipped, and a literal split across two
source lines would evade it. Each of those needs deliberate evasion; none
matches the accidental-paste shape this defends against.
"""

from __future__ import annotations

import hashlib
import ipaddress
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# SHA-256 of identifiers that must never reappear. Stored as digests so this
# file does not itself publish them — see the module docstring.
_FORBIDDEN_DIGESTS: dict[str, str] = {
    "0af677c3de09948c4dca7210b38e22e1ed0a51bbf9591523867148cdec2e0738": (
        "a vendor SSH key filename scrubbed before publication"
    ),
    "25f2a0c6221971e4d7856eeed299a5c61d2d35ec7923d0c2ad43eb8cfe647d4b": (
        "a vendor hosting hostname scrubbed before publication"
    ),
    # Public canary, safe to name: proves the digest path is live in CI.
    "cec5760b7567f02b9c78d6262fa5c8343725e0cc7e44c9b11e41d923584ce266": (
        "the canary token this guard uses to prove itself"
    ),
}

_ALLOWED_NETWORKS = tuple(
    ipaddress.ip_network(n)
    for n in (
        "192.0.2.0/24",  # RFC 5737 TEST-NET-1
        "198.51.100.0/24",  # RFC 5737 TEST-NET-2
        "203.0.113.0/24",  # RFC 5737 TEST-NET-3
        "10.0.0.0/8",  # RFC 1918
        "172.16.0.0/12",  # RFC 1918
        "192.168.0.0/16",  # RFC 1918
        "127.0.0.0/8",  # loopback
        "0.0.0.0/8",  # "this network" / unspecified
        "169.254.0.0/16",  # link-local
        "224.0.0.0/4",  # multicast
        "240.0.0.0/4",  # reserved
        "255.255.255.255/32",
    )
)

_IPV4_RE = re.compile(r"(?<![\w.])(\d{1,3}(?:\.\d{1,3}){3})(?![\w.])")
_TOKEN_RE = re.compile(r"[A-Za-z0-9_.\-]+")

# Absolute home directories, matched as whole paths.
#
# An earlier version flagged every `/home/<name>/` it found and treated an
# allowlist of "placeholder-looking" names as safe. That does not work: the
# repo legitimately contains /home/ubuntu/, /home/deploy/, /home/testuser/,
# /home/ops/ and others as fictional example paths, and no pattern can tell a
# made-up account from a real one.
#
# So this checks two things that are decidable:
#   1. the home directory of whoever is running the tests — hardcoding your
#      own home into a tracked file is always a mistake, and this catches it
#      on the machine that made it, naming nobody;
#   2. a digest of the one historical home path that was scrubbed before
#      publication, so CI still catches that specific string.
_HOME_PATH_RE = re.compile(r"/(?:home|Users)/[A-Za-z0-9_.\-]+")
_FORBIDDEN_HOME_DIGESTS: dict[str, str] = {
    "c0bd14e65ed265f29481b401480575c070af818a040f4baabca6cfa37b83f657": (
        "a home directory scrubbed before publication"
    ),
}

_BINARY_SUFFIXES = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".gz", ".qm", ".woff", ".woff2"}
)

# Every exemption is a hole in the control; keep this empty.
_IP_SWEEP_EXEMPT: frozenset[str] = frozenset()


def _tracked_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [REPO_ROOT / p for p in out.split("\0") if p]


def _readable_tracked_files() -> list[Path]:
    this_file = Path(__file__).resolve()
    return [
        p
        for p in _tracked_files()
        if p.suffix.lower() not in _BINARY_SUFFIXES and p.is_file() and p.resolve() != this_file
    ]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _is_public_ip(text: str) -> bool:
    try:
        addr = ipaddress.ip_address(text)
    except ValueError:
        return False
    return not any(addr in net for net in _ALLOWED_NETWORKS)


def _forbidden_reason(token: str) -> str | None:
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return _FORBIDDEN_DIGESTS.get(digest)


def test_no_forbidden_identifier_in_tracked_files() -> None:
    """A vendor identifier scrubbed before publication must not come back."""
    hits: list[str] = []
    for path in _readable_tracked_files():
        rel = path.relative_to(REPO_ROOT)
        for lineno, line in enumerate(_read(path).splitlines(), 1):
            for token in _TOKEN_RE.findall(line):
                reason = _forbidden_reason(token)
                if reason:
                    hits.append(f"  {rel}:{lineno}: {reason}")

    assert not hits, (
        f"Found {len(hits)} forbidden identifier(s) in tracked files.\n"
        + "\n".join(hits)
        + "\n\nThis repository is public. Use a generic placeholder or an "
        "example.com hostname instead. See this file's docstring for why."
    )


def test_no_public_ip_addresses_in_tracked_files() -> None:
    """No routable IPv4 address may appear in a tracked file.

    A public address next to a username and a project path is an
    infrastructure disclosure even when nothing secret accompanies it.
    """
    hits: list[str] = []
    for path in _readable_tracked_files():
        rel = str(path.relative_to(REPO_ROOT))
        if rel in _IP_SWEEP_EXEMPT:
            continue
        for lineno, line in enumerate(_read(path).splitlines(), 1):
            for candidate in _IPV4_RE.findall(line):
                if _is_public_ip(candidate):
                    hits.append(f"  {rel}:{lineno}: {candidate}")

    assert not hits, (
        f"Found {len(hits)} routable IPv4 address(es) in tracked files.\n"
        + "\n".join(hits)
        + "\n\nThis repository is public. Replace with an RFC 5737 documentation "
        "address: 192.0.2.x, 198.51.100.x or 203.0.113.x."
    )


def _offending_home(path_text: str) -> str | None:
    """Return a reason if ``path_text`` is a real home directory, else None."""
    digest = hashlib.sha256(path_text.encode("utf-8")).hexdigest()
    reason = _FORBIDDEN_HOME_DIGESTS.get(digest)
    if reason:
        return reason
    if path_text == str(Path.home()):
        return "your own home directory"
    return None


def test_no_real_home_directory_in_tracked_files() -> None:
    """No real absolute home directory may appear in a tracked file.

    A hardcoded home path silently breaks the file for everyone else — which
    is exactly what five scripts in this repo did before publication, each
    working only on the author's machine.

    Fictional example paths (``/home/ubuntu/``, ``/home/deploy/`` and friends)
    are fine and are used deliberately in fixtures; see the comment on
    ``_HOME_PATH_RE`` for why this cannot simply reject all of them.
    """
    hits: list[str] = []
    for path in _readable_tracked_files():
        rel = str(path.relative_to(REPO_ROOT))
        for lineno, line in enumerate(_read(path).splitlines(), 1):
            for candidate in _HOME_PATH_RE.findall(line):
                reason = _offending_home(candidate)
                if reason:
                    hits.append(f"  {rel}:{lineno}: {reason}")

    assert not hits, (
        f"Found {len(hits)} real home directory path(s) in tracked files.\n"
        + "\n".join(hits)
        + "\n\nUse $HOME, ~, a repo-relative path, or /home/user/ as a "
        "placeholder. A hardcoded home directory works only on your machine."
    )


def test_the_guard_recognises_a_routable_address() -> None:
    """Prove the IP detector has teeth.

    Uses well-known public addresses belonging to nobody involved in this
    project. Do not "improve" this by substituting the addresses that were
    actually scrubbed — this file is published, and writing them here would
    reintroduce the disclosure the guard exists to prevent.
    """
    assert _is_public_ip("8.8.8.8")  # Google DNS
    assert _is_public_ip("1.1.1.1")  # Cloudflare DNS
    assert _is_public_ip("93.184.216.34")  # example.com, per IANA

    assert not _is_public_ip("192.0.2.44")  # RFC 5737
    assert not _is_public_ip("198.51.100.226")  # RFC 5737
    assert not _is_public_ip("203.0.113.81")  # RFC 5737
    assert not _is_public_ip("10.0.0.5")  # RFC 1918
    assert not _is_public_ip("127.0.0.1")  # loopback


def test_the_digest_mechanism_catches_a_known_token() -> None:
    """Prove the digest path is live, without naming the real strings.

    The canary is public and harmless; that the other digests correspond to
    the real scrubbed identifiers is proved out-of-band, by a fault-injection
    script deliberately kept outside this repository.
    """
    assert _forbidden_reason("cpsm-forbidden-canary-do-not-use") is not None
    assert _forbidden_reason("an-ordinary-token") is None
    assert len(_FORBIDDEN_DIGESTS) >= 3, "forbidden-digest list was emptied"


def test_the_home_detector_fires_on_your_own_home_but_not_on_examples() -> None:
    """Prove the home-path detector distinguishes real from fictional.

    The point of keying on ``Path.home()`` is that it needs no list of names:
    whoever runs this, it catches *their* home and ignores the invented ones
    the fixtures use.
    """
    own = str(Path.home())
    assert _HOME_PATH_RE.findall(f"cd {own}/cc_multi") == [own]
    # Non-None rather than an exact reason: on the original author's machine
    # the digest entry matches first, and on anyone else's the Path.home()
    # branch does. Both are correct detections of the same mistake.
    assert _offending_home(own) is not None

    for fictional in ("/home/ubuntu", "/home/deploy", "/home/testuser", "/Users/nobody"):
        if fictional == own:  # pragma: no cover - only if someone is literally /home/ubuntu
            continue
        assert _offending_home(fictional) is None, (
            f"{fictional} is a fictional example path used in fixtures and must not be flagged"
        )


def test_ip_sweep_exemption_list_stays_empty() -> None:
    """Every exemption is a hole in the control; make adding one deliberate."""
    assert _IP_SWEEP_EXEMPT == frozenset(), (
        "An exemption was added to the public-IP sweep. That is a hole in the "
        "control this file exists to provide. If it is genuinely necessary, "
        "document why in the file docstring and update this test deliberately."
    )
