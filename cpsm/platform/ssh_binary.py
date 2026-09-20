# -*- coding: utf-8 -*-
"""
SshBinary — detects OpenSSH vs plink, normalises argv differences.

Spec: §8, §9.2
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Iterable
from pathlib import Path
from typing import Literal

__all__ = ["SshBinary", "has_ssh_option"]


def has_ssh_option(ssh_options: Iterable[str] | None, keyword: str) -> bool:
    """True if *ssh_options* already sets ssh_config *keyword*.

    PUBLIC, and deliberately so: cpsm/services/template_service.py imports this
    to decide whether the user already pinned the identity, so the launcher
    templates and the argv builder share ONE keyword rule. It was private
    (``_has_option``) at first, which meant nothing marked it as supported
    shared surface -- a refactor here could have changed its semantics as "just
    internals" while another module silently depended on it.

    CONVENTION, not an enforced rule: import this rather than writing a second
    keyword comparison. Two rules that can drift is the failure this exists to
    prevent -- the shell version that preceded it was wrong twice, in opposite
    directions, while this one was right, precisely because they were separate.

    There is deliberately no lint check policing that. Four successive versions
    of one were written and each was defeated: a keyword built by concatenation,
    ``.join()``, ``%``-format or ``.replace()``; a locally-defined shadow of
    this function; the name re-imported or reassigned over; a rebinding hidden
    in a module-level ``if`` block or done through ``globals()``. Proving "no
    second implementation exists anywhere" by inspecting program text is not
    decidable, and the structural property is in any case neither necessary nor
    sufficient for correctness: a second implementation with identical
    semantics would be harmless, and satisfying the structure says nothing
    about whether the logic here is right.

    What actually guards the behaviour is behavioural: the nine user-override
    forms and TestSshOptionValues in tests/services/test_template_service.py,
    and the identity/no-identity/override/lookalike/plink cases in
    tests/platform/test_ssh_binary.py. Those caught a real drift that a
    structural check missed, which is why they are the mechanism and this
    paragraph is only a convention.

    ssh_config keywords are case-insensitive, and ``ssh -o`` accepts both
    ``Key=Value`` and ``Key Value``.  Matching has to mirror ssh's own parsing:
    a caller writing ``identitiesonly=no`` must be detected, or CPSM would
    inject its own ``IdentitiesOnly=yes`` ahead of it and — because OpenSSH
    honours the FIRST value obtained for a parameter — silently override a
    deliberate setting.

    Verified against OpenSSH_9.7p1::

        $ ssh -G -o IdentitiesOnly=yes -o IdentitiesOnly=no h | grep -i ^identitiesonly
        identitiesonly yes
        $ ssh -G -o IdentitiesOnly=no -o IdentitiesOnly=yes h | grep -i ^identitiesonly
        identitiesonly no
    """
    want = keyword.strip().lower()
    for opt in ssh_options or []:
        text = opt.strip()
        if not text:
            continue
        # The keyword is everything up to the first '=' or whitespace, which is
        # how ssh separates an option name from its value.  Splitting is what
        # keeps ``IdentitiesOnlyExtra=yes`` and
        # ``ProxyCommand=echo IdentitiesOnly=yes`` from matching.
        name = re.split(r"[=\s]", text, maxsplit=1)[0]
        if name.strip().lower() == want:
            return True
    return False


class SshBinary:
    """Detect and wrap an SSH binary (OpenSSH or PuTTY plink).

    Differences normalised here so callers never need to know which binary is
    in use:

    * Port flag: OpenSSH ``-p <port>``, plink ``-P <port>``
    * Force TTY: OpenSSH ``-tt``, plink ``-t``
    * Identity file: both use ``-i <path>``.  OpenSSH additionally gets
      ``-o IdentitiesOnly=yes``, because there ``-i`` only ADDS to the keys ssh
      will offer (agent keys plus the default ``~/.ssh/id_*`` files) rather
      than restricting it to the one named.  plink does not need it: it uses
      only the key given plus Pageant and never scans a default key directory,
      and ``IdentitiesOnly`` is an OpenSSH ssh_config keyword with no PuTTY
      equivalent.
    * Extra options: OpenSSH ``-o Key=Value``, plink ``-o Key=Value``
      (plink ≥ 0.73 supports -o, older versions silently ignore it)

    Plink does NOT support forwarding passphrases through stdin when invoked
    non-interactively; that path is guarded by Phase 7 KeyService, not here.
    """

    binary: str
    flavor: Literal["openssh", "plink"]

    # Names to search for, in preference order
    _OPENSSH_NAMES = ("ssh",)
    _PLINK_NAMES = ("plink",)

    def __init__(self, binary: str, flavor: Literal["openssh", "plink"]) -> None:
        self.binary = binary
        self.flavor = flavor

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def detect(
        cls,
        prefer: Literal["auto", "openssh", "plink"] = "auto",
    ) -> SshBinary:
        """Detect an SSH binary on PATH and return an ``SshBinary`` instance.

        *prefer* controls the search order:
        * ``"auto"`` — try OpenSSH first, fall back to plink.
        * ``"openssh"`` — only search for OpenSSH binaries.
        * ``"plink"`` — only search for plink.

        Raises:
            RuntimeError: if no suitable binary is found on PATH.
        """
        if prefer in ("auto", "openssh"):
            for name in cls._OPENSSH_NAMES:
                path = shutil.which(name)
                if path:
                    return cls(binary=path, flavor="openssh")

        if prefer in ("auto", "plink"):
            for name in cls._PLINK_NAMES:
                path = shutil.which(name)
                if path:
                    return cls(binary=path, flavor="plink")

        if prefer == "openssh":
            raise RuntimeError(
                f"No OpenSSH binary found on PATH (searched: {', '.join(cls._OPENSSH_NAMES)})"
            )
        if prefer == "plink":
            raise RuntimeError(
                f"No plink binary found on PATH (searched: {', '.join(cls._PLINK_NAMES)})"
            )

        raise RuntimeError("No SSH binary found (openssh or plink)")

    # ------------------------------------------------------------------
    # Argv builder
    # ------------------------------------------------------------------

    def build_argv(
        self,
        *,
        host: str,
        user: str,
        port: int = 22,
        identity_file: Path | None = None,
        ssh_options: list[str] | None = None,
        remote_command: list[str] | None = None,
        force_tty: bool = True,
    ) -> list[str]:
        """Build an argv list for the detected SSH binary.

        Args:
            host: Remote hostname or IP.
            user: Remote username.
            port: Remote port (default 22).  Omitted from argv when 22 so the
                command stays clean; the server default is 22 anyway.
            identity_file: Path to private key file (``-i``).
            ssh_options: Extra ``-o Key=Value`` strings (no leading ``-o``
                prefix needed — they are added by this method).
            remote_command: Command + args to execute on the remote host.
                When ``None`` an interactive session is opened.
            force_tty: Request pseudo-TTY allocation (``-tt`` / ``-t``).

        Returns:
            A list of strings ready to pass to :class:`ProcessRunner`.
        """
        argv: list[str] = [self.binary]

        # TTY allocation
        if force_tty:
            if self.flavor == "openssh":
                argv += ["-tt"]
            else:  # plink
                argv += ["-t"]

        # Port (only add when non-default)
        if port != 22:
            flag = "-P" if self.flavor == "plink" else "-p"
            argv += [flag, str(port)]

        # Identity file.
        #
        # ``-i`` alone does NOT restrict ssh to that key.  OpenSSH treats it as
        # an ADDITION to the identities it already intends to offer: every key
        # held by ssh-agent, plus the default ~/.ssh/id_* files.  ssh reports
        # the default itself::
        #
        #     $ ssh -G host | grep -i '^identitiesonly'
        #     identitiesonly no
        #
        # Two consequences, and CPSM has a stake in both:
        #
        #   * The key the user configured is not necessarily the key used.  A
        #     connection pinned to a specific identity_file_ref can silently
        #     authenticate with an unrelated agent key, so any server-side
        #     authorisation keyed to the intended key is bypassed with no
        #     signal to the operator.
        #   * sshd's default MaxAuthTries is 6.  An agent holding more keys
        #     than that gets the connection torn down with "Too many
        #     authentication failures" before the correct key is ever offered.
        #
        # ``IdentitiesOnly=yes`` restricts ssh to the identities named on the
        # command line, which is what choosing a key in CPSM means.  It stays
        # compatible with ssh-agent: the agent still SIGNS, it just may no
        # longer volunteer keys nobody asked for.
        if identity_file is not None:
            argv += ["-i", str(identity_file)]
            # Only for OpenSSH, and only when the caller has not already
            # expressed a preference.  This block precedes the caller's
            # ssh_options below and OpenSSH honours the FIRST value obtained
            # for a parameter, so injecting unconditionally would silently
            # override a deliberate IdentitiesOnly=no.
            #
            # plink is excluded deliberately.  IdentitiesOnly is an OpenSSH
            # ssh_config keyword with no PuTTY equivalent; per PuTTY's
            # documentation plink uses only the key given by -i plus Pageant
            # and never scans a default key directory, so the failure mode
            # above does not arise there, and older plink silently ignores -o.
            #
            # BASIS: PuTTY documentation, NOT measurement — plink is not
            # installed on the machine this was developed on, so the claim
            # about plink's behaviour is unverified here.  What IS verified is
            # CPSM's own behaviour: test_plink_identity_file_does_not_pin pins
            # the argv this builder emits for the plink flavor.
            if self.flavor == "openssh" and not has_ssh_option(ssh_options, "IdentitiesOnly"):
                argv += ["-o", "IdentitiesOnly=yes"]

        # Extra -o options (OpenSSH and plink ≥ 0.73)
        for opt in ssh_options or []:
            argv += ["-o", opt]

        # User@host
        argv.append(f"{user}@{host}")

        # Optional remote command
        if remote_command:
            argv += ["--", *list(remote_command)]

        return argv
