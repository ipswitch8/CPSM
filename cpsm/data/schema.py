# -*- coding: utf-8 -*-
"""
Pydantic v2 models for the CPSM .cpsm.yaml schema.

Schema version: 1
Spec sections: §2.2, §2.3, §2.5
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    Tag,
    field_validator,
    model_validator,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ID_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
_MAX_JUMP_HOST_DEPTH = 4

# Remote-control session name. Shown in the claude.ai session list / phone
# app. Allow alphanumerics, dash, underscore, dot — no whitespace (the
# rendered string passes through a single shell-quoting layer, so a space
# would word-split when bash expands $_CLAUDE_OPTIONS).
_RC_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def _validate_rc_name(value: str | None) -> str | None:
    """Validate ``remote_control_name``. ``None`` / empty → caller falls back
    to ``connection.name`` (or ``id``) at render time."""
    if value is None or value == "":
        return None
    if not _RC_NAME_RE.match(value):
        raise ValueError(
            f"remote_control_name '{value}' must match ^[A-Za-z0-9_.-]{{1,64}}$"
            " (no whitespace)"
        )
    return value


def _validate_id_slug(value: str, field_name: str = "id") -> str:
    """Validate that *value* matches the ID slug regex from §2.5."""
    if not _ID_SLUG_RE.match(value):
        raise ValueError(f"{field_name} '{value}' must match ^[a-z0-9][a-z0-9-]{{1,62}}$")
    return value


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


class Settings(BaseModel):
    """Global CPSM settings (§2.2 settings block)."""

    model_config = ConfigDict(extra="forbid")

    default_multiplexer: Literal["tmux", "itmux", "psmux", "auto"] = "auto"
    default_terminal: Literal[
        "auto", "wt", "gnome-terminal", "konsole", "alacritty", "kitty", "xterm", "wezterm"
    ] = "auto"
    ssh_binary: Literal["auto", "openssh", "plink"] = "auto"
    default_claude_options: str = "--resume"
    default_ssh_options: str = "-o ConnectTimeout=10 -o ServerAliveInterval=30"
    known_hosts_strict: bool = True
    status_poll_interval_ms: int = Field(default=3000, ge=100)
    layout_conflict_default: Literal["move", "keep", "error"] = "move"
    layout_preserve_on_remove: bool = True
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"


# ---------------------------------------------------------------------------
# SSH Keys
# ---------------------------------------------------------------------------


class SshKeyDeployment(BaseModel):
    """Record of a key deployment to a connection."""

    model_config = ConfigDict(extra="forbid")

    connection_id: str
    deployed_at: datetime
    method: str = "ssh-copy-id"

    @field_validator("connection_id")
    @classmethod
    def validate_connection_id(cls, v: str) -> str:
        return _validate_id_slug(v, "connection_id")


class SshKey(BaseModel):
    """An SSH key entry (§2.2 ssh_keys[])."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    type: Literal["ed25519", "rsa", "ecdsa"] = "ed25519"
    private_path: str
    public_path: str
    passphrase_ref: str | None = None
    created_at: datetime | None = None
    deployments: list[SshKeyDeployment] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        return _validate_id_slug(v, "id")


# ---------------------------------------------------------------------------
# Connections — per-profile variant models
# ---------------------------------------------------------------------------
# Common optional fields shared across multiple profiles.


class _ReconnectMixin(BaseModel):
    """Fields shared by profiles that support reconnect."""

    model_config = ConfigDict(extra="forbid")

    auto_reconnect: bool = False
    auto_reconnect_on_clean_exit: bool = False
    reconnect_backoff_ms: list[int] = Field(default_factory=lambda: [1000, 3000, 10000, 30000])
    reconnect_max_attempts: int = Field(default=0, ge=0)


class _CommonOptional(BaseModel):
    """Optional fields applicable to all profiles."""

    model_config = ConfigDict(extra="forbid")

    sudo_user: str | None = None
    env: dict[str, str] = Field(default_factory=dict)
    pre_commands: list[str] = Field(default_factory=list)
    post_commands: list[str] = Field(default_factory=list)
    auto_reconnect: bool = False
    auto_reconnect_on_clean_exit: bool = False
    reconnect_backoff_ms: list[int] = Field(default_factory=lambda: [1000, 3000, 10000, 30000])
    reconnect_max_attempts: int = Field(default=0, ge=0)
    tags: list[str] = Field(default_factory=list)
    notes: str | None = None


class ClaudeRemoteConnection(BaseModel):
    """launch_profile: claude-remote (§2.3)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str | None = None
    launch_profile: Literal["claude-remote"]

    # Required for claude-remote
    host: str
    port: int = Field(default=22, ge=1, le=65535)
    user: str
    project_folder: str
    claude_options: str

    # Optional for claude-remote
    identity_file_ref: str | None = None
    # Round-C auth flow: "ask" prompts on first launch; "key" deploys/uses
    # an SSH key (identity_file_ref must be set); "password" forces password
    # auth and never prompts.
    auth_method: Literal["ask", "key", "password"] = "ask"
    key_deployed: bool = False
    sudo_user: str | None = None
    jump_host: str | None = None
    keepalive_interval_s: int | None = None
    connection_timeout_s: int | None = None
    env: dict[str, str] = Field(default_factory=dict)
    pre_commands: list[str] = Field(default_factory=list)
    post_commands: list[str] = Field(default_factory=list)
    auto_reconnect: bool = False
    auto_reconnect_on_clean_exit: bool = False
    reconnect_backoff_ms: list[int] = Field(default_factory=lambda: [1000, 3000, 10000, 30000])
    reconnect_max_attempts: int = Field(default=0, ge=0)
    tags: list[str] = Field(default_factory=list)
    notes: str | None = None

    # Claude Code Remote Control (v2.1.51+): when enabled, the launch
    # appends ``--remote-control <name>`` so the session becomes drivable
    # from claude.ai and the phone app. Requires a one-time OAuth /login
    # on the remote box; see the AuthWizard for the bootstrap flow.
    remote_control_enabled: bool = False
    # When None, the renderer falls back to connection.name → connection.id.
    # See _RC_NAME_RE for character constraints (no whitespace).
    remote_control_name: str | None = None

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        return _validate_id_slug(v, "id")

    @field_validator("identity_file_ref")
    @classmethod
    def validate_identity_file_ref(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        return _validate_id_slug(v, "identity_file_ref")

    @field_validator("remote_control_name")
    @classmethod
    def validate_remote_control_name(cls, v: str | None) -> str | None:
        return _validate_rc_name(v)


class ClaudeLocalConnection(BaseModel):
    """launch_profile: claude-local (§2.3).

    Forbidden: host, port, user, identity_file_ref, jump_host.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str | None = None
    launch_profile: Literal["claude-local"]

    # Required for claude-local
    project_folder: str
    claude_options: str

    # Optional for claude-local
    sudo_user: str | None = None
    env: dict[str, str] = Field(default_factory=dict)
    pre_commands: list[str] = Field(default_factory=list)
    post_commands: list[str] = Field(default_factory=list)
    auto_reconnect: bool = False
    auto_reconnect_on_clean_exit: bool = False
    reconnect_backoff_ms: list[int] = Field(default_factory=lambda: [1000, 3000, 10000, 30000])
    reconnect_max_attempts: int = Field(default=0, ge=0)
    tags: list[str] = Field(default_factory=list)
    notes: str | None = None

    # Claude Code Remote Control: same semantics as ClaudeRemoteConnection.
    # On a local box the auth bootstrap is just running ``claude`` + ``/login``
    # in a terminal — no SSH tunnel required.
    remote_control_enabled: bool = False
    remote_control_name: str | None = None

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        return _validate_id_slug(v, "id")

    @field_validator("remote_control_name")
    @classmethod
    def validate_remote_control_name(cls, v: str | None) -> str | None:
        return _validate_rc_name(v)


class SshShellConnection(BaseModel):
    """launch_profile: ssh-shell (§2.3).

    Required: host, port, user, identity_file_ref.
    Forbidden: claude_options.
    project_folder is optional (remote cwd).
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str | None = None
    launch_profile: Literal["ssh-shell"]

    # Required for ssh-shell
    host: str
    port: int = Field(default=22, ge=1, le=65535)
    user: str

    # Optional for ssh-shell
    identity_file_ref: str | None = None
    # See ClaudeRemoteConnection.auth_method for semantics.
    auth_method: Literal["ask", "key", "password"] = "ask"
    key_deployed: bool = False
    sudo_user: str | None = None
    jump_host: str | None = None
    project_folder: str | None = None
    keepalive_interval_s: int | None = None
    connection_timeout_s: int | None = None
    env: dict[str, str] = Field(default_factory=dict)
    pre_commands: list[str] = Field(default_factory=list)
    post_commands: list[str] = Field(default_factory=list)
    auto_reconnect: bool = False
    auto_reconnect_on_clean_exit: bool = False
    reconnect_backoff_ms: list[int] = Field(default_factory=lambda: [1000, 3000, 10000, 30000])
    reconnect_max_attempts: int = Field(default=0, ge=0)
    tags: list[str] = Field(default_factory=list)
    notes: str | None = None

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        return _validate_id_slug(v, "id")

    @field_validator("identity_file_ref")
    @classmethod
    def validate_identity_file_ref(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        return _validate_id_slug(v, "identity_file_ref")


class LocalShellConnection(BaseModel):
    """launch_profile: local-shell (§2.3).

    Forbidden: host, port, user, identity_file_ref, jump_host, claude_options.
    Required: project_folder.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str | None = None
    launch_profile: Literal["local-shell"]

    # Required for local-shell
    project_folder: str

    # Optional for local-shell
    sudo_user: str | None = None
    env: dict[str, str] = Field(default_factory=dict)
    pre_commands: list[str] = Field(default_factory=list)
    post_commands: list[str] = Field(default_factory=list)
    auto_reconnect: bool = False
    auto_reconnect_on_clean_exit: bool = False
    reconnect_backoff_ms: list[int] = Field(default_factory=lambda: [1000, 3000, 10000, 30000])
    reconnect_max_attempts: int = Field(default=0, ge=0)
    tags: list[str] = Field(default_factory=list)
    notes: str | None = None

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        return _validate_id_slug(v, "id")


class CustomConnection(BaseModel):
    """launch_profile: custom (§2.3).

    Required: custom_template_id.
    Everything else optional.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str | None = None
    launch_profile: Literal["custom"]

    # Required for custom
    custom_template_id: str

    # Optional for custom
    host: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    user: str | None = None
    sudo_user: str | None = None
    identity_file_ref: str | None = None
    jump_host: str | None = None
    project_folder: str | None = None
    keepalive_interval_s: int | None = None
    connection_timeout_s: int | None = None
    claude_options: str | None = None
    env: dict[str, str] = Field(default_factory=dict)
    pre_commands: list[str] = Field(default_factory=list)
    post_commands: list[str] = Field(default_factory=list)
    auto_reconnect: bool = False
    auto_reconnect_on_clean_exit: bool = False
    reconnect_backoff_ms: list[int] = Field(default_factory=lambda: [1000, 3000, 10000, 30000])
    reconnect_max_attempts: int = Field(default=0, ge=0)
    tags: list[str] = Field(default_factory=list)
    notes: str | None = None

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        return _validate_id_slug(v, "id")

    @field_validator("custom_template_id")
    @classmethod
    def validate_custom_template_id(cls, v: str) -> str:
        return _validate_id_slug(v, "custom_template_id")

    @field_validator("identity_file_ref")
    @classmethod
    def validate_identity_file_ref(cls, v: str | None) -> str | None:
        if v is not None:
            return _validate_id_slug(v, "identity_file_ref")
        return v


# ---------------------------------------------------------------------------
# Discriminated union for Connection
# ---------------------------------------------------------------------------


def _get_launch_profile(data: Any) -> str:
    """Extract launch_profile tag from raw data dict."""
    if isinstance(data, dict):
        return str(data.get("launch_profile", ""))
    # Already a model instance
    return str(getattr(data, "launch_profile", ""))


Connection = Annotated[
    Annotated[ClaudeRemoteConnection, Tag("claude-remote")]
    | Annotated[ClaudeLocalConnection, Tag("claude-local")]
    | Annotated[SshShellConnection, Tag("ssh-shell")]
    | Annotated[LocalShellConnection, Tag("local-shell")]
    | Annotated[CustomConnection, Tag("custom")],
    Discriminator(_get_launch_profile),
]


# ---------------------------------------------------------------------------
# Groups
# ---------------------------------------------------------------------------


class Group(BaseModel):
    """A CPSM group (§2.2 groups[])."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    color: str | None = None
    members: list[str] = Field(default_factory=list)
    launch_order: Literal["sequential", "parallel"] = "sequential"
    launch_delay_ms: int = Field(default=0, ge=0)
    default_layout_id: str | None = None
    isolation: Literal["shared", "per-group"] = "shared"
    layout_conflict: Literal["move", "keep", "error"] = "move"
    auto_attach: bool = False

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        return _validate_id_slug(v, "id")

    @field_validator("members", mode="before")
    @classmethod
    def validate_members(cls, v: list[Any]) -> list[Any]:
        for m in v:
            if isinstance(m, str):
                _validate_id_slug(m, "members[]")
        return v


# ---------------------------------------------------------------------------
# Screen Layouts
# ---------------------------------------------------------------------------


class GeometryPct(BaseModel):
    """Viewport geometry as percentage of monitor (§2.5)."""

    model_config = ConfigDict(extra="forbid")

    x: float = Field(ge=0, le=100)
    y: float = Field(ge=0, le=100)
    w: float = Field(gt=0, le=100)
    h: float = Field(gt=0, le=100)

    @model_validator(mode="after")
    def validate_bounds(self) -> GeometryPct:
        if self.x + self.w > 100 + 1e-9:
            raise ValueError(f"x ({self.x}) + w ({self.w}) exceeds 100")
        if self.y + self.h > 100 + 1e-9:
            raise ValueError(f"y ({self.y}) + h ({self.h}) exceeds 100")
        return self


class Pane(BaseModel):
    """A single pane reference within a viewport (§2.2)."""

    model_config = ConfigDict(extra="forbid")

    connection_id: str | None = None

    @field_validator("connection_id")
    @classmethod
    def validate_connection_id(cls, v: str | None) -> str | None:
        if v is not None:
            return _validate_id_slug(v, "connection_id")
        return v


class Split(BaseModel):
    """A split node in a viewport's split tree (Round C — quadrant fix).

    Represents a horizontal or vertical split with N children, each of
    which is either a leaf :class:`Pane` or a nested :class:`Split` for
    mixed-orientation layouts. Round 1 ships the data model only —
    canvas rendering and tmux launch keep using the flat ``panes`` list
    until Rounds 2/3 land.
    """

    model_config = ConfigDict(extra="forbid")

    direction: Literal["h", "v"]
    children: list[Pane | Split] = Field(default_factory=list)
    # Optional explicit ratios — len(ratios) == len(children) - 1, each in
    # (0, 1); the last child gets the remainder.
    ratios: list[float] | None = None

    @model_validator(mode="after")
    def validate_split(self) -> Split:
        if len(self.children) < 2:
            raise ValueError(
                f"Split must have at least 2 children, got {len(self.children)}"
            )
        if self.ratios is not None:
            if len(self.ratios) != len(self.children) - 1:
                raise ValueError(
                    f"ratios length {len(self.ratios)} must equal "
                    f"len(children) - 1 = {len(self.children) - 1}"
                )
            if any(r <= 0 or r >= 1 for r in self.ratios):
                raise ValueError("each ratio must be strictly between 0 and 1")
            if sum(self.ratios) >= 1:
                raise ValueError("ratios must sum to less than 1")
        return self


class Viewport(BaseModel):
    """A viewport within a monitor (§2.2)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    geometry_pct: GeometryPct
    tmux_window_name: str | None = None
    tmux_layout: Literal["tiled", "even-h", "even-v", "main-h", "main-v", "custom"] = "tiled"
    custom_layout_string: str | None = None
    panes: list[Pane] = Field(default_factory=list)
    # Round C: structured split tree. None = derive from flat panes +
    # tmux_layout on load (see migrate_or_sync_split_tree).
    split_tree: Pane | Split | None = None

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        return _validate_id_slug(v, "id")

    @model_validator(mode="after")
    def validate_pane_connection_id_uniqueness(self) -> Viewport:
        """panes[].connection_id must be unique within a viewport (null exempt, §2.5)."""
        seen: set[str] = set()
        for pane in self.panes:
            cid = pane.connection_id
            if cid is None:
                continue
            if cid in seen:
                raise ValueError(
                    f"connection_id '{cid}' appears more than once in viewport '{self.id}'"
                )
            seen.add(cid)
        return self

    @model_validator(mode="after")
    def migrate_or_sync_split_tree(self) -> Viewport:
        """Round C: keep ``split_tree`` and ``panes`` consistent after model
        construction.

        - If ``split_tree`` is ``None`` and ``panes`` is non-empty, build a
          tree from the flat panes + ``tmux_layout``. The tree's leaves are
          the same Pane instances as ``panes``.
        - If ``split_tree`` is provided (loaded from YAML alongside
          ``panes``, or set programmatically), the tree is canonical:
          ``panes`` is **always** rebuilt from the tree's in-order leaves
          so the two collections share Python identity (required by drop
          handlers, which look up leaves via ``is``).
        - If both are empty, nothing to do.
        """
        if self.split_tree is None:
            if not self.panes:
                return self
            if len(self.panes) == 1:
                self.split_tree = self.panes[0]
                return self
            direction: Literal["h", "v"] = (
                "h" if self.tmux_layout in ("even-h", "main-h") else "v"
            )
            self.split_tree = Split(
                direction=direction,
                children=list(self.panes),
            )
            return self
        # split_tree wins — always re-derive panes so they share identity
        # with the tree's leaves. (Previously only re-derived when panes
        # was empty, which silently broke drop after a save-roundtrip.)
        self.panes = _flatten_split_tree_leaves(self.split_tree)
        return self


def _flatten_split_tree_leaves(node: Pane | Split) -> list[Pane]:
    """In-order traversal of a split tree → list of leaf Panes."""
    if isinstance(node, Pane):
        return [node]
    out: list[Pane] = []
    for child in node.children:
        out.extend(_flatten_split_tree_leaves(child))
    return out


# ---------------------------------------------------------------------------
# Round C — split-tree mutation helpers
# ---------------------------------------------------------------------------


def _find_leaf_in_tree(
    node: Pane | Split,
    target: Pane,
    parent: Split | None = None,
    idx: int = -1,
) -> tuple[Split | None, int] | None:
    """Find *target* leaf in *node*; return ``(parent, child_index)`` or
    ``None`` if the leaf isn't found. ``parent=None`` means the leaf IS
    the root."""
    if node is target:
        return (parent, idx)
    if isinstance(node, Pane):
        return None
    for i, child in enumerate(node.children):
        result = _find_leaf_in_tree(child, target, node, i)
        if result is not None:
            return result
    return None


def split_pane_in_viewport(
    vp: Viewport,
    target: Pane,
    zone: Literal["left", "right", "top", "bottom"],
    new_pane: Pane,
) -> bool:
    """Insert *new_pane* alongside *target* by introducing a Split (or
    extending an existing same-direction parent Split). Returns True on
    success. Also re-syncs ``vp.panes`` from the resulting tree.

    Edge ``zone`` decides direction + position:
      - left/top  → new_pane goes BEFORE target.
      - right/bottom → new_pane goes AFTER target.
      - left/right → horizontal split.
      - top/bottom → vertical split.
    """
    if vp.split_tree is None:
        return False
    direction: Literal["h", "v"] = "h" if zone in ("left", "right") else "v"
    insert_after = zone in ("right", "bottom")

    found = _find_leaf_in_tree(vp.split_tree, target)
    if found is None:
        return False
    parent, idx = found

    # If parent already splits in this direction, just insert into it.
    if parent is not None and parent.direction == direction:
        new_idx = idx + 1 if insert_after else idx
        parent.children.insert(new_idx, new_pane)
    else:
        # Replace target with a fresh Split containing [target, new] or
        # [new, target].
        children = [target, new_pane] if insert_after else [new_pane, target]
        new_split = Split(direction=direction, children=children)
        if parent is None:
            # Target is the root leaf; replace the whole tree.
            vp.split_tree = new_split
        else:
            parent.children[idx] = new_split

    _resync_viewport_panes(vp)
    return True


def remove_pane_from_viewport(vp: Viewport, target: Pane) -> bool:
    """Remove leaf *target* from the viewport's split tree. If the parent
    Split is left with a single child, collapse it (recursively up the
    tree). Returns True if the leaf was found and removed.
    """
    if vp.split_tree is None:
        return False
    found = _find_leaf_in_tree(vp.split_tree, target)
    if found is None:
        return False
    parent, idx = found
    if parent is None:
        # Target was the root leaf; viewport becomes empty.
        vp.split_tree = None
        vp.panes = []
        return True
    del parent.children[idx]
    # Collapse single-child Splits up to the root.
    _collapse_single_child(vp, parent)
    _resync_viewport_panes(vp)
    return True


def _collapse_single_child(vp: Viewport, node: Split) -> None:
    """If *node* has only one child after a removal, replace it in its
    grandparent (or the root) with that child. Recurse upward."""
    while len(node.children) == 1:
        sole = node.children[0]
        # Find node in its parent
        if vp.split_tree is node:
            vp.split_tree = sole
            return
        if vp.split_tree is None:
            return
        parent_lookup = _find_node_parent(vp.split_tree, node)
        if parent_lookup is None:
            return
        parent_split, parent_idx = parent_lookup
        parent_split.children[parent_idx] = sole
        if not isinstance(sole, Split):
            return
        node = parent_split  # walk up — but parent might now also be collapsible
    return


def _find_node_parent(
    root: Pane | Split, target: Split,
) -> tuple[Split, int] | None:
    """Return ``(parent, index)`` of *target* Split inside *root*, or None."""
    if isinstance(root, Pane):
        return None
    for i, child in enumerate(root.children):
        if child is target:
            return (root, i)
        if isinstance(child, Split):
            inner = _find_node_parent(child, target)
            if inner is not None:
                return inner
    return None


def _resync_viewport_panes(vp: Viewport) -> None:
    """Re-derive ``vp.panes`` from ``vp.split_tree``'s leaf order. Called
    after any tree mutation so legacy code paths see consistent state."""
    if vp.split_tree is None:
        vp.panes = []
        return
    vp.panes = _flatten_split_tree_leaves(vp.split_tree)


class Monitor(BaseModel):
    """A monitor within a screen layout."""

    model_config = ConfigDict(extra="forbid")

    identifier: str | None = None
    monitor_index_hint: int | None = None
    viewports: list[Viewport] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_viewport_id_uniqueness(self) -> Monitor:
        """viewports[].id must be unique within a monitor layout (§2.5)."""
        seen: set[str] = set()
        for vp in self.viewports:
            if vp.id in seen:
                raise ValueError(f"viewport id '{vp.id}' is not unique within this monitor")
            seen.add(vp.id)
        return self

    @model_validator(mode="after")
    def validate_no_viewport_overlap(self) -> Monitor:
        """Viewports within a single monitor must not overlap > 1% of monitor area (§2.5)."""
        vps = self.viewports
        for i in range(len(vps)):
            for j in range(i + 1, len(vps)):
                overlap_pct = _compute_overlap_pct(vps[i].geometry_pct, vps[j].geometry_pct)
                if overlap_pct > 1.0:
                    raise ValueError(
                        f"Viewports '{vps[i].id}' and '{vps[j].id}' overlap by "
                        f"{overlap_pct:.2f}% (max 1%) within the same monitor"
                    )
        return self


def _compute_overlap_pct(a: GeometryPct, b: GeometryPct) -> float:
    """Return overlap area as a percentage of total monitor area (0-100).

    geometry_pct values are in percent units (0-100), so multiplying two pct edges
    yields square-percent units. Total monitor area is 100*100 = 10000 sq-pct, so
    we divide by 100 to express overlap as a fraction-of-monitor percentage.
    """
    x_overlap = max(0.0, min(a.x + a.w, b.x + b.w) - max(a.x, b.x))
    y_overlap = max(0.0, min(a.y + a.h, b.y + b.h) - max(a.y, b.y))
    return (x_overlap * y_overlap) / 100.0


class ScreenLayout(BaseModel):
    """A named screen layout (§2.2 screen_layouts[])."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    inherits_from: str | None = None
    monitors: list[Monitor] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        return _validate_id_slug(v, "id")


# ---------------------------------------------------------------------------
# Scenes
# ---------------------------------------------------------------------------


class Scene(BaseModel):
    """A scene that launches multiple groups (§2.2 scenes[])."""

    model_config = ConfigDict(extra="forbid")

    id: str
    groups: list[str] = Field(default_factory=list)
    on_conflict: Literal["error", "first-wins", "last-wins"] = "error"

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        return _validate_id_slug(v, "id")

    @field_validator("groups", mode="before")
    @classmethod
    def validate_group_ids(cls, v: list[Any]) -> list[Any]:
        for g in v:
            if isinstance(g, str):
                _validate_id_slug(g, "scenes[].groups[]")
        return v


# ---------------------------------------------------------------------------
# Launch Templates
# ---------------------------------------------------------------------------


class LaunchTemplate(BaseModel):
    """A custom launch template (§2.2 launch_templates[])."""

    model_config = ConfigDict(extra="forbid")

    id: str
    description: str | None = None
    bash: str

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        return _validate_id_slug(v, "id")


# ---------------------------------------------------------------------------
# Top-level document with FK integrity and jump-host cycle checks (§2.5)
# ---------------------------------------------------------------------------


class CpsmDocument(BaseModel):
    """The complete .cpsm.yaml document."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    settings: Settings = Field(default_factory=Settings)
    ssh_keys: list[SshKey] = Field(default_factory=list)
    connections: list[Connection] = Field(default_factory=list)
    groups: list[Group] = Field(default_factory=list)
    screen_layouts: list[ScreenLayout] = Field(default_factory=list)
    scenes: list[Scene] = Field(default_factory=list)
    launch_templates: list[LaunchTemplate] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_document(self) -> CpsmDocument:
        """§2.5 cross-document integrity checks.

        Dangling FK references are tolerated (Round C extended): each is
        logged as a warning via ``collect_fk_issues`` so the app can still
        load over a single broken link — the same list is surfaced as
        error-severity issues by ``ConfigService.validate`` (CLI
        ``cpsm validate``, GUI Validate action).

        Structural checks that would corrupt runtime behavior even without
        a launch (jump_host chain cycles / max depth) still raise.
        """
        import logging as _logging
        _fk_log = _logging.getLogger(__name__)
        for location, message in collect_fk_issues(self):
            _fk_log.warning("%s: %s", location, message)

        # --- jump_host chain: acyclic, max depth 4 ---
        conn_ids = {c.id for c in self.connections}
        jump_host_map: dict[str, str] = {}
        for conn in self.connections:
            jh = getattr(conn, "jump_host", None)
            if jh is not None:
                jump_host_map[conn.id] = jh
        for conn_id in jump_host_map:
            _check_jump_host_chain(conn_id, jump_host_map, conn_ids)

        return self


def _check_jump_host_chain(start: str, jump_map: dict[str, str], all_conn_ids: set[str]) -> None:
    """DFS to detect jump_host cycles and enforce max-depth-4 (§2.5).

    # TODO(spec): §2.5 says "jump_host chains acyclic, max depth 4".  We
    # interpret this as: the chain may contain at most 4 *nodes* (including
    # the start connection), i.e. at most 3 hops.  A 5-node chain (4 hops)
    # is rejected.  If the spec means 4 hops instead, increment
    # _MAX_JUMP_HOST_DEPTH to 5 or change the loop range below.
    """
    visited: list[str] = [start]
    current = start
    for _ in range(_MAX_JUMP_HOST_DEPTH - 1):
        next_hop = jump_map.get(current)
        if next_hop is None:
            return  # Chain terminates cleanly within allowed depth
        if next_hop in visited:
            raise ValueError(
                f"jump_host chain starting at '{start}' contains a cycle: "
                f"{' -> '.join(visited)} -> {next_hop}"
            )
        visited.append(next_hop)
        current = next_hop

    # After _MAX_JUMP_HOST_DEPTH - 1 hops, if there is still a next hop
    # the chain is too long.
    next_hop = jump_map.get(current)
    if next_hop is not None:
        if next_hop in visited:
            raise ValueError(
                f"jump_host chain starting at '{start}' contains a cycle: "
                f"{' -> '.join(visited)} -> {next_hop}"
            )
        raise ValueError(
            f"jump_host chain starting at '{start}' exceeds max depth "
            f"{_MAX_JUMP_HOST_DEPTH}: {' -> '.join(visited)} -> {next_hop}"
        )


# ---------------------------------------------------------------------------
# Shared FK integrity walker (§2.5)
# ---------------------------------------------------------------------------


def collect_fk_issues(doc: CpsmDocument) -> list[tuple[str, str]]:
    """Walk *doc* and yield ``(location, message)`` for every dangling FK.

    Single source of truth for §2.5 cross-reference integrity, consumed by
    two callers with different severity:

    - ``CpsmDocument.validate_document`` logs each issue as a WARNING so a
      single stale reference doesn't block the whole config from loading.
    - ``ConfigService.validate`` returns each issue as an error-severity
      ``ValidationIssue`` so ``cpsm validate`` (CLI) and the GUI Validate
      action still surface the problem to the user.

    Locations use dotted-path notation compatible with the ones pydantic
    emits from ``ValidationError.errors()[i]['loc']``.
    """
    conn_ids = {c.id for c in doc.connections}
    key_ids = {k.id for k in doc.ssh_keys}
    key_paths = {k.id: getattr(k, "private_path", "") for k in doc.ssh_keys}
    group_ids = {g.id for g in doc.groups}
    layout_ids = {sl.id for sl in doc.screen_layouts}
    template_ids = {t.id for t in doc.launch_templates}

    issues: list[tuple[str, str]] = []

    for conn in doc.connections:
        jh = getattr(conn, "jump_host", None)
        if jh is not None and jh not in conn_ids:
            issues.append(
                (
                    f"connections.{conn.id}.jump_host",
                    f"jump_host '{jh}' not found in connections",
                )
            )
        ref = getattr(conn, "identity_file_ref", None)
        if ref is not None and ref not in key_ids:
            issues.append(
                (
                    f"connections.{conn.id}.identity_file_ref",
                    f"identity_file_ref '{ref}' not found in ssh_keys",
                )
            )
        elif ref is not None and not (key_paths.get(ref) or "").strip():
            # The id resolves but the entry carries no usable private key, so
            # the launcher still cannot emit -i and still cannot pin.  Checking
            # only the id made `cpsm validate` report a clean config that then
            # refused to launch -- the two must agree on what "usable" means.
            issues.append(
                (
                    f"connections.{conn.id}.identity_file_ref",
                    f"identity_file_ref '{ref}' resolves to an ssh_keys entry "
                    f"with an empty private_path",
                )
            )
        tpl = getattr(conn, "custom_template_id", None)
        if tpl is not None and tpl not in template_ids:
            issues.append(
                (
                    f"connections.{conn.id}.custom_template_id",
                    f"custom_template_id '{tpl}' not found in launch_templates",
                )
            )

    for group in doc.groups:
        for m in group.members:
            if m not in conn_ids:
                issues.append(
                    (
                        f"groups.{group.id}.members",
                        f"member '{m}' not found in connections",
                    )
                )
        dlid = group.default_layout_id
        if dlid is not None and dlid not in layout_ids:
            issues.append(
                (
                    f"groups.{group.id}.default_layout_id",
                    f"default_layout_id '{dlid}' not found in screen_layouts",
                )
            )

    for layout in doc.screen_layouts:
        inh = layout.inherits_from
        if inh is not None and inh not in layout_ids:
            issues.append(
                (
                    f"screen_layouts.{layout.id}.inherits_from",
                    f"inherits_from '{inh}' not found in screen_layouts",
                )
            )
        for monitor in layout.monitors:
            for vp in monitor.viewports:
                for pane in vp.panes:
                    cid = pane.connection_id
                    if cid is not None and cid not in conn_ids:
                        issues.append(
                            (
                                f"screen_layouts.{layout.id}.viewports."
                                f"{vp.id}.panes.connection_id",
                                f"pane connection_id '{cid}' not found in connections",
                            )
                        )

    for scene in doc.scenes:
        for gid in scene.groups:
            if gid not in group_ids:
                issues.append(
                    (
                        f"scenes.{scene.id}.groups",
                        f"group '{gid}' not found in groups",
                    )
                )

    return issues


# ---------------------------------------------------------------------------
# Re-export clean public surface
# ---------------------------------------------------------------------------

__all__ = [
    "ClaudeLocalConnection",
    "ClaudeRemoteConnection",
    "Connection",
    "CpsmDocument",
    "CustomConnection",
    "GeometryPct",
    "Group",
    "LaunchTemplate",
    "LocalShellConnection",
    "Monitor",
    "Pane",
    "Scene",
    "ScreenLayout",
    "Settings",
    "Split",
    "SshKey",
    "SshKeyDeployment",
    "SshShellConnection",
    "Viewport",
]
