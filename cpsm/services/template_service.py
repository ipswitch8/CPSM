# -*- coding: utf-8 -*-
"""
TemplateService — load and render CPSM launcher templates.

Spec sections: §5.2, §5.3, §7.6

Public surface
--------------
    TemplateService(*, override_dir=None)
    .render(profile, connection, *, settings=None) -> str
    .render_placeholder() -> str
    .list_builtin() -> list[str]
    .restore_default(profile) -> None

Mustache renderer
-----------------
Supports:
    {{field}}          — connection attribute lookup
    {{env.KEY}}        — os.environ lookup
    {{field|default}}  — fallback when field is absent / empty

All values that reach shell commands are shlex.quote()'d (safe_render mode).
This is Phase 4 — Windows quoting (subprocess.list2cmdline / PowerShellQuoter)
is a Phase 19 concern.
"""

from __future__ import annotations

import os
import re
import shlex
from importlib.resources import files
from pathlib import Path
from typing import Any

from cpsm.platform.ssh_binary import has_ssh_option

__all__ = ["TemplateMustacheError", "TemplateNotFoundError", "TemplateService"]

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

_BUILTIN_PACKAGE = "cpsm.resources.launcher_templates"

# Map from canonical profile name → filename (without path)
_PROFILE_TO_FILE: dict[str, str] = {
    "claude-remote": "claude-remote.sh",
    "claude-local": "claude-local.sh",
    "ssh-shell": "ssh-shell.sh",
    "local-shell": "local-shell.sh",
}

_PLACEHOLDER_FILE = "_placeholder.sh"

# Mustache token pattern: {{ ... }}
_TOKEN_RE = re.compile(r"\{\{([^}]+?)\}\}")


class TemplateNotFoundError(FileNotFoundError):
    """Raised when a requested template cannot be located."""


class IdentityKeyNotFoundError(LookupError):
    """A connection names an ``identity_file_ref`` that no SshKey provides.

    Raised rather than falling back to ssh's default key resolution.  The
    fallback is not a harmless default: with no ``-i`` there is nothing for
    ``IdentitiesOnly=yes`` to pin to, so ssh reverts to offering every key in
    the agent.  That silently defeats the pin, and once the agent holds more
    keys than sshd's MaxAuthTries (default 6) the connection is torn down
    before the right key is ever tried.

    This is not hypothetical.  A real config carried 21 connections pointing
    at a key id ``imported-default`` that no longer existed; every one of them
    launched unpinned and failed to authenticate, with nothing in the launcher
    output naming the missing key as the cause.
    """


class TemplateMustacheError(ValueError):
    """Raised when a template token cannot be resolved and has no default."""


# ---------------------------------------------------------------------------
# Mustache renderer (minimal — no conditionals, no loops)
# ---------------------------------------------------------------------------


def _resolve_token(token: str, context: dict[str, Any]) -> str:
    """Resolve a single ``{{token}}`` to a string value.

    Supports:
        field            — context['field']
        env.KEY          — os.environ['KEY']
        field|default    — context.get('field') or 'default'

    Returns the resolved string *before* any shell quoting.
    Raises TemplateMustacheError if the token is unresolvable.
    """
    token = token.strip()

    # Detect default syntax: field|default
    default_value: str | None = None
    if "|" in token:
        token, default_value = token.split("|", 1)
        token = token.strip()
        default_value = default_value.strip()

    # env.KEY lookup — check context first (connection.env values), then os.environ
    if token.startswith("env."):
        key = token[4:]
        # Context stores connection.env entries as "env.<KEY>" keys
        ctx_value = context.get(token)
        if ctx_value is not None and ctx_value != "":
            return str(ctx_value)
        env_value = os.environ.get(key)
        if env_value is not None:
            return env_value
        if default_value is not None:
            return default_value
        raise TemplateMustacheError(f"Environment variable '{key}' is not set and has no default")

    # Regular field lookup.  An empty string is treated as "absent" so that
    # {{field|default}} works when the connection has field=None (stored as "").
    raw = context.get(token)
    value: str | None = str(raw) if raw is not None and raw != "" else None
    if value is None:
        if default_value is not None:
            return default_value
        raise TemplateMustacheError(
            f"Template field '{{{{ {token} }}}}' not found in context and has no default"
        )
    return value


def _safe_render(template: str, context: dict[str, Any]) -> str:
    """Render *template* substituting each ``{{token}}`` with a shell-quoted value.

    Quoting strategy: every substituted value is wrapped in shlex.quote() so
    that no injected content can be interpreted as a shell metacharacter **in
    a single shell layer** (e.g. a bare variable assignment ``_VAR='value'``).

    Scope and limitations
    ~~~~~~~~~~~~~~~~~~~~~
    shlex.quote() produces output that is safe for **one** shell-parsing layer.
    Templates that pass values across multiple layers — unquoted heredoc bodies,
    SSH command strings, ``sudo -c`` strings, ``bash -ic`` strings — MUST use
    positional argv passthrough (``bash -s --``, ``bash -ilc '...' -- "$1"``) or
    ``printf '%q'`` re-quoting per additional layer, rather than relying on this
    function alone.

    See ``claude-remote.sh`` (quoted heredoc + positional argv) and
    ``claude-local.sh`` (``sudo -i bash -ic '...' -- "$@"``) for the canonical
    multi-layer patterns used in this project.
    """

    def replacer(match: re.Match[str]) -> str:
        raw_value = _resolve_token(match.group(1), context)
        return shlex.quote(raw_value)

    return _TOKEN_RE.sub(replacer, template)


# ---------------------------------------------------------------------------
# TemplateService
# ---------------------------------------------------------------------------


def _ssh_option_values(raw: str) -> list[str]:
    """Extract the ``-o`` VALUES from a raw ssh flag string.

    ``settings.default_ssh_options`` is free text typed into a QLineEdit, so it
    can use any spelling ssh itself accepts::

        -o IdentitiesOnly=no        separate argument
        -oIdentitiesOnly=no         concatenated, no space -- ssh accepts this
        -o "IdentitiesOnly no"      space-separated key and value

    Returns the value part of each ``-o``, e.g. ``["ConnectTimeout=10"]``.
    Anything that is not a ``-o`` is ignored.

    Parsed in Python rather than matched in the shell template deliberately.
    Two successive shell regexes got this wrong in opposite directions: a bare
    substring search suppressed the pin for an unrelated
    ``ProxyCommand=/usr/bin/identitiesonlyproxy.sh``, and anchoring it on
    whitespace then MISSED the concatenated ``-oIdentitiesOnly=no`` form, which
    would have silently overridden a deliberate user setting. Doing it here
    means one implementation, in one language, sharing the keyword-matching
    rule with SshBinary rather than reimplementing it.
    """
    try:
        tokens = shlex.split(raw)
    except ValueError:
        # Unbalanced quotes in user input: fall back to whitespace splitting
        # rather than raising out of a launcher render.
        tokens = raw.split()

    values: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "-o" and i + 1 < len(tokens):
            values.append(tokens[i + 1])
            i += 2
        elif tok.startswith("-o") and len(tok) > 2:
            values.append(tok[2:])
            i += 1
        else:
            i += 1
    return values


class TemplateService:
    """Load and render CPSM launcher templates.

    Parameters
    ----------
    override_dir:
        Optional directory that contains user-edited template files.
        When set, ``render()`` checks this directory first before falling
        back to the built-in package resources.  This hook is wired to
        ``dlg_launcher_templates`` in Phase 13.
    """

    def __init__(self, *, override_dir: Path | None = None) -> None:
        self._override_dir = override_dir

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def render(
        self,
        profile: str,
        connection: Any,
        *,
        settings: Any | None = None,
        templates: list[Any] | None = None,
        ssh_keys: list[Any] | None = None,
    ) -> str:
        """Render *profile*'s template against *connection*.

        Parameters
        ----------
        profile:
            One of ``"claude-remote"``, ``"claude-local"``, ``"ssh-shell"``,
            ``"local-shell"``, or ``"custom"``.
        connection:
            A CPSM connection model (or any object with the expected attributes).
        settings:
            Optional ``Settings`` model.  Used to populate ``{{ssh_options}}``
            via ``settings.default_ssh_options``.
        templates:
            List of ``LaunchTemplate`` objects.  Required when *profile* is
            ``"custom"`` — the template whose ``id`` matches
            ``connection.custom_template_id`` is used.

        Returns
        -------
        str
            Fully-rendered, shell-safe script content.
        """
        if profile == "custom":
            template_text = self._load_custom_template(connection, templates)
        else:
            template_text = self._load_builtin_or_override(profile)

        context = self._build_context(connection, settings=settings, ssh_keys=ssh_keys)
        return _safe_render(template_text, context)

    def render_placeholder(self) -> str:
        """Return the rendered _placeholder.sh content (no substitution needed)."""
        return self._load_file_from_package(_PLACEHOLDER_FILE)

    def list_builtin(self) -> list[str]:
        """Return the list of built-in profile names (excluding _placeholder)."""
        return list(_PROFILE_TO_FILE.keys())

    def restore_default(self, profile: str) -> None:
        """Delete the override file for *profile* so the built-in is used again.

        No-ops if there is no override directory or the override file does not
        exist.  Raises ``TemplateNotFoundError`` if *profile* is unknown.
        """
        if profile not in _PROFILE_TO_FILE:
            raise TemplateNotFoundError(f"Unknown profile: {profile!r}")
        if self._override_dir is None:
            return
        override_path = self._override_dir / _PROFILE_TO_FILE[profile]
        if override_path.exists():
            override_path.unlink()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _load_builtin_or_override(self, profile: str) -> str:
        """Load template text for *profile*, checking override_dir first."""
        filename = _PROFILE_TO_FILE.get(profile)
        if filename is None:
            raise TemplateNotFoundError(
                f"No built-in template for profile {profile!r}. "
                f"Valid profiles: {list(_PROFILE_TO_FILE)}"
            )

        # Check override directory first
        if self._override_dir is not None:
            override_path = self._override_dir / filename
            if override_path.is_file():
                return override_path.read_text(encoding="utf-8")

        # Fall back to package resource
        return self._load_file_from_package(filename)

    @staticmethod
    def _load_file_from_package(filename: str) -> str:
        """Read *filename* from the built-in launcher_templates package resource."""
        resource = files(_BUILTIN_PACKAGE).joinpath(filename)
        # importlib.resources files() returns a Traversable; .read_text is supported.
        return resource.read_text(encoding="utf-8")

    def _load_custom_template(
        self,
        connection: Any,
        templates: list[Any] | None,
    ) -> str:
        """Load the custom template referenced by *connection.custom_template_id*."""
        template_id = getattr(connection, "custom_template_id", None)
        if template_id is None:
            raise TemplateNotFoundError(
                "Connection has launch_profile='custom' but no custom_template_id"
            )

        if templates is None:
            raise TemplateNotFoundError(
                f"Cannot resolve custom_template_id={template_id!r}: "
                "no 'templates' list was passed to render()"
            )

        for tpl in templates:
            if getattr(tpl, "id", None) == template_id:
                return str(tpl.bash)

        raise TemplateNotFoundError(
            f"custom_template_id={template_id!r} not found in provided templates list"
        )

    @staticmethod
    def _build_context(
        connection: Any,
        *,
        settings: Any | None,
        ssh_keys: list[Any] | None = None,
    ) -> dict[str, Any]:
        """Build the mustache rendering context from *connection* + *settings*.

        All fields are extracted as strings (empty string for None values) so
        the renderer always has something to shlex.quote().
        """

        def _get(attr: str, fallback: str = "") -> str:
            v = getattr(connection, attr, None)
            if v is None:
                return fallback
            return str(v)

        # ssh_options: prefer settings.default_ssh_options, fall back to empty
        ssh_options = ""
        if settings is not None:
            ssh_options = str(getattr(settings, "default_ssh_options", "") or "")

        # Whether the launcher should add -o IdentitiesOnly=yes.
        #
        # Decided HERE, in Python, and rendered as a plain flag, so the shell
        # template has no ssh-option syntax left to parse. Two shell regexes
        # got this wrong in opposite directions before it was moved: a
        # substring search suppressed the pin for an unrelated
        # ProxyCommand=/usr/bin/identitiesonlyproxy.sh, and anchoring on
        # whitespace then MISSED the concatenated -oIdentitiesOnly=no form
        # (which ssh accepts), silently overriding a deliberate user setting.
        #
        # The keyword comparison is SshBinary's has_ssh_option -- the same rule
        # the non-template code paths use, so there is one implementation
        # rather than two that can disagree.
        user_set_identities_only = has_ssh_option(_ssh_option_values(ssh_options), "IdentitiesOnly")

        # Resolve identity_file: connection schema has identity_file_ref (a
        # SshKey id slug); the launcher needs the on-disk private_path.  If
        # the caller passed an ssh_keys list, look it up there.
        #
        # An empty result means no -i, which means ssh's default key
        # resolution -- fine when NO key was ever configured, but wrong when a
        # key was configured and merely failed to resolve.  Those two cases
        # are distinguished below rather than collapsed into one.
        identity_file = ""
        ref = getattr(connection, "identity_file_ref", None)
        for k in ssh_keys or ():
            if getattr(k, "id", None) == ref:
                identity_file = str(getattr(k, "private_path", "") or "")
                break
        if not identity_file:
            # Backward compatibility: callers that set ``connection.identity_file``
            # directly (rare; tests) keep working.
            identity_file = _get("identity_file")
        if ref and not identity_file:
            # A key was chosen and we could not produce a path for it.  Refuse
            # rather than degrade to an unpinned connection -- see the
            # exception's docstring for why that fallback is not harmless.
            #
            # The test is "did we end up with a path", NOT "did the id match".
            # Three inputs reach this point and all three are the same defect:
            #   * the id matches nothing in ssh_keys      (the reported case)
            #   * ssh_keys is empty or None               (every key deleted)
            #   * the id matches, but private_path is ""  (blanked-out entry)
            # An earlier version keyed off the id lookup instead, which let the
            # last two render an unpinned launcher exactly as before the fix.
            available = (
                ", ".join(sorted(str(getattr(k, "id", "")) for k in ssh_keys))
                if ssh_keys
                else "(none defined)"
            )
            raise IdentityKeyNotFoundError(
                f"Connection {getattr(connection, 'name', None) or ref!r} "
                f"references SSH key id {ref!r}, which has no usable private "
                f"key path. Available key ids: {available}. "
                f"Point the connection at an existing key, or re-add the "
                f"missing key, before launching."
            )
        # Expand ~ — the renderer shlex-quotes every value, so a literal "~"
        # would survive into the bash launcher and ssh -i doesn't expand
        # tilde itself.  Empty string passes through unchanged.
        if identity_file:
            identity_file = str(Path(identity_file).expanduser())

        # Compose effective claude_options. If Remote Control is enabled,
        # prepend ``--remote-control <name>`` so the launched claude becomes
        # drivable from claude.ai / the phone app. The name is shlex.quote'd
        # individually here (rather than letting _safe_render quote the
        # whole composed string as one piece) so that names containing
        # ``-``/``_``/``.`` survive the single layer of shell parsing inside
        # the launcher template. RC names are validated against
        # ``_RC_NAME_RE`` (no whitespace) at the schema level.
        claude_options = _get("claude_options")
        if getattr(connection, "remote_control_enabled", False):
            rc_name = (
                getattr(connection, "remote_control_name", None)
                or getattr(connection, "name", None)
                or _get("id")
            )
            rc_flag = f"--remote-control {shlex.quote(str(rc_name))}"
            claude_options = f"{rc_flag} {claude_options}".strip() if claude_options else rc_flag

        ctx: dict[str, Any] = {
            # Connection identity
            "connection_id": _get("id"),
            "name": _get("name"),
            "launch_profile": _get("launch_profile"),
            # Remote access
            "host": _get("host"),
            "port": _get("port", "22"),
            "user": _get("user"),
            "identity_file": identity_file,
            "sudo_user": _get("sudo_user"),
            "jump_host": _get("jump_host"),
            # Local / remote paths
            "project_folder": _get("project_folder"),
            # Claude
            "claude_options": claude_options,
            # SSH options from settings
            "ssh_options": ssh_options,
            # Non-empty when the launcher should pin the identity: empty when
            # there is no identity to pin, or when the user already expressed
            # an IdentitiesOnly preference of their own.
            "pin_identity": ("1" if (identity_file and not user_set_identities_only) else ""),
            # Env dict flattened as individual env.KEY lookups (handled in _resolve_token)
            # Tags / notes (rarely needed in templates but available)
            "notes": _get("notes"),
        }

        # Merge connection.env into the context so {{env.KEY}} works even
        # without os.environ (the _resolve_token fallback hits os.environ;
        # connection-level overrides must be injected via the env dict).
        env: dict[str, str] = getattr(connection, "env", {}) or {}
        # We store them under the "env" key as a sub-dict so _resolve_token
        # can reach them.  _resolve_token uses os.environ directly for env.KEY,
        # but we also inject them into os.environ temporarily.  Instead,
        # we put them as top-level "env.<KEY>" keys for direct resolution.
        for k, v in env.items():
            ctx[f"env.{k}"] = str(v)

        return ctx
