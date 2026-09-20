# -*- coding: utf-8 -*-
"""
KeyDiscoveryService — find which local SSH key is likely to work for a host.

Background
----------

A CPSM connection may have no usable pinned SSH key (e.g. imported from an
external source, or created before a key was chosen). Before we can offer to
probe/pin a key for the user, we need to guess which key in ``~/.ssh`` is the
right one for a given host — the same way a human would: check
``~/.ssh/config`` for an explicit ``IdentityFile``, then look at the
``user@host`` comment convention many key-generation workflows stamp into
``.pub`` files, then fall back to the handful of conventional default key
names.

This module is deliberately pure logic: no Qt import, no subprocess, no
network I/O. It only inspects the filesystem (``~/.ssh`` or an injected
directory for testing) and returns ranked candidates. Actually verifying a
key works against a live host is a later phase's job (probing over the
network); this phase only narrows the search space.

Security constraints
---------------------
- Private key file *contents* are never opened, read, or logged — existence
  is checked with ``Path.exists()`` / ``Path.is_file()`` only, since that is
  all ``ssh -i`` needs us to know.
- Only ``*.pub`` files and ``~/.ssh/config`` have their contents read.
- No ``~/.cpsm.yaml`` access here.
- Logging is minimal (module logger, no comment/identity contents beyond a
  hostname at debug level).
- Unreadable files are skipped gracefully rather than raising, since a real
  ``~/.ssh`` directory can contain files owned by other users, sockets, etc.
- The ``Include`` directive in ``ssh_config`` is out of scope for this phase
  and is silently ignored.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "KeyCandidate",
    "KeyDiscoveryService",
]

logger = logging.getLogger(__name__)

# Conventional default private key filenames, ranked below any explicit match.
_DEFAULT_KEY_NAMES: tuple[str, ...] = ("id_ed25519", "id_rsa", "id_ecdsa")

# Scores — higher is better. Kept as named constants rather than magic
# numbers so ranking rationale is visible at a glance.
_SCORE_SSH_CONFIG = 100
_SCORE_PUB_USER_HOST = 80
_SCORE_PUB_HOST = 60
_SCORE_DEFAULT = 10


@dataclass(frozen=True)
class KeyCandidate:
    """A single candidate SSH private key for a host, with provenance."""

    private_path: Path
    public_path: Path | None
    comment: str
    source: str
    score: int


def _whole_token_match(haystack: str, token: str) -> bool:
    """True if ``token`` appears in ``haystack`` as a standalone token.

    Guards against the substring trap: ``192.0.2.4`` must not match
    ``192.0.2.44`` and ``example.com`` must not match ``foo.example.com``.
    We require that any character immediately preceding/following the match
    (if present) is not alphanumeric and not one of ``.``/``-``/``_`` — i.e.
    the token must be delimited by whitespace, punctuation like ``@``, or
    string boundaries, not by being embedded in a longer hostname/IP.
    """
    if not token:
        return False
    pattern = re.compile(r"(?<![A-Za-z0-9_.\-])" + re.escape(token) + r"(?![A-Za-z0-9_.\-])")
    return pattern.search(haystack) is not None


def _read_text(path: Path) -> str | None:
    """Read a text file (``.pub`` / ``ssh_config`` only) tolerantly.

    Returns ``None`` (never raises) if the file cannot be read for any
    reason — permissions, transient OS errors, or it not being a regular
    file (e.g. a socket dropped into ``~/.ssh``).
    """
    try:
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except (PermissionError, OSError):
        logger.debug("key_discovery: skipping unreadable file")
        return None


@dataclass
class _ConfigStanza:
    hosts: list[str]
    hostnames: list[str]
    identity_files: list[str]


def _parse_ssh_config(text: str) -> list[_ConfigStanza]:
    """Parse ``~/.ssh/config`` into a list of ``Host`` stanzas.

    Minimal parser: recognizes ``Host``, ``HostName`` and ``IdentityFile``
    keywords (case-insensitive), one directive per line, ``#`` comments.
    ``Include`` is intentionally not followed (out of scope this phase).
    """
    stanzas: list[_ConfigStanza] = []
    current: _ConfigStanza | None = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        keyword, value = parts[0].lower(), parts[1].strip()

        if keyword == "host":
            current = _ConfigStanza(hosts=value.split(), hostnames=[], identity_files=[])
            stanzas.append(current)
        elif current is None:
            # Directive before any Host block — not a valid stanza we can
            # attribute; ignore.
            continue
        elif keyword == "hostname":
            current.hostnames.append(value)
        elif keyword == "identityfile":
            # Strip surrounding quotes some configs use.
            current.identity_files.append(value.strip('"'))
        # Other keywords (Include, User, Port, ...) are ignored in this phase.

    return stanzas


def _host_pattern_matches(pattern: str, host: str) -> bool:
    """True if an ssh_config ``Host`` glob pattern matches ``host``.

    Supports the ``*`` and ``?`` wildcards ssh_config uses; a leading ``!``
    negation is not supported in this minimal phase and treated as
    non-matching (safer default — never picks a negated stanza).
    """
    if pattern.startswith("!"):
        return False
    regex = "^" + re.escape(pattern).replace(r"\*", ".*").replace(r"\?", ".") + "$"
    return re.match(regex, host) is not None


def _expand_identity_file(value: str, ssh_dir: Path) -> Path:
    """Expand ``~`` and ``~/.ssh``-relative paths in an ``IdentityFile`` value."""
    expanded = Path(value).expanduser()
    if not expanded.is_absolute():
        expanded = ssh_dir / expanded
    return expanded


class KeyDiscoveryService:
    """Discover candidate SSH private keys for a host from local config.

    All filesystem access is rooted at ``ssh_dir`` (defaults to
    ``Path.home() / ".ssh"``), which is injectable so tests never touch the
    real user's SSH directory.
    """

    def __init__(self, ssh_dir: Path | None = None) -> None:
        self.ssh_dir: Path = ssh_dir if ssh_dir is not None else Path.home() / ".ssh"

    def discover(self, host: str, user: str | None = None) -> list[KeyCandidate]:
        """Return ranked candidate private keys for ``host``, best first.

        Deduplicates by resolved private-key path, keeping the
        highest-scoring source for each. Only candidates whose private key
        file actually exists on disk are returned.
        """
        logger.debug("key_discovery: discovering candidates for host=%s", host)

        candidates: dict[Path, KeyCandidate] = {}

        for candidate in self._from_ssh_config(host):
            self._consider(candidates, candidate)
        for candidate in self._from_pub_comments(host, user):
            self._consider(candidates, candidate)
        for candidate in self._from_defaults():
            self._consider(candidates, candidate)

        return sorted(candidates.values(), key=lambda c: c.score, reverse=True)

    @staticmethod
    def _consider(candidates: dict[Path, KeyCandidate], candidate: KeyCandidate) -> None:
        """Insert ``candidate`` keeping only the highest score per resolved path."""
        try:
            key = candidate.private_path.resolve()
        except OSError:
            key = candidate.private_path
        existing = candidates.get(key)
        if existing is None or candidate.score > existing.score:
            candidates[key] = candidate

    def _from_ssh_config(self, host: str) -> list[KeyCandidate]:
        config_path = self.ssh_dir / "config"
        text = _read_text(config_path)
        if not text:
            return []

        results: list[KeyCandidate] = []
        for stanza in _parse_ssh_config(text):
            matched = any(_host_pattern_matches(p, host) for p in stanza.hosts) or (
                host in stanza.hostnames
            )
            if not matched or not stanza.identity_files:
                continue
            for identity_value in stanza.identity_files:
                private_path = _expand_identity_file(identity_value, self.ssh_dir)
                if not private_path.exists():
                    continue
                public_path = private_path.with_name(private_path.name + ".pub")
                if not public_path.exists():
                    public_path = None
                results.append(
                    KeyCandidate(
                        private_path=private_path,
                        public_path=public_path,
                        comment="",
                        source="ssh_config",
                        score=_SCORE_SSH_CONFIG,
                    )
                )
        return results

    def _from_pub_comments(self, host: str, user: str | None) -> list[KeyCandidate]:
        results: list[KeyCandidate] = []
        try:
            pub_files = sorted(self.ssh_dir.glob("*.pub"))
        except OSError:
            return []

        for pub_path in pub_files:
            text = _read_text(pub_path)
            if not text:
                continue
            for line in text.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                fields = line.split(None, 2)
                if len(fields) < 3:
                    continue
                comment = fields[2].strip()

                private_path = pub_path.with_name(pub_path.name[: -len(".pub")])
                if not private_path.exists():
                    continue

                if user and comment == f"{user}@{host}":
                    results.append(
                        KeyCandidate(
                            private_path=private_path,
                            public_path=pub_path,
                            comment=comment,
                            source="pub_comment_user_host",
                            score=_SCORE_PUB_USER_HOST,
                        )
                    )
                elif _whole_token_match(comment, host):
                    results.append(
                        KeyCandidate(
                            private_path=private_path,
                            public_path=pub_path,
                            comment=comment,
                            source="pub_comment_host",
                            score=_SCORE_PUB_HOST,
                        )
                    )
        return results

    def _from_defaults(self) -> list[KeyCandidate]:
        results: list[KeyCandidate] = []
        for name in _DEFAULT_KEY_NAMES:
            private_path = self.ssh_dir / name
            if not private_path.exists():
                continue
            public_path = self.ssh_dir / f"{name}.pub"
            if not public_path.exists():
                public_path = None
            results.append(
                KeyCandidate(
                    private_path=private_path,
                    public_path=public_path,
                    comment="",
                    source="default",
                    score=_SCORE_DEFAULT,
                )
            )
        return results
