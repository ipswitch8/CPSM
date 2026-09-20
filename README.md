# CPSM — Cross-Platform Session Manager

A desktop GUI for managing SSH and tmux sessions across many remote hosts, and
for launching Claude Code against projects on those hosts. One YAML file
describes your connections; CPSM gives you a searchable session list,
multi-monitor layouts, SSH key management with identity pinning, and discovery
of sessions that are already running. Linux is the primary platform; Windows
works via `itmux` or `PSMUX`.

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](LICENSE)

---

## Quick Start

### Install (Linux x86_64)

Download `CPSM-<version>-x86_64.AppImage` and `install.sh` from the
[**Releases page**](https://github.com/ipswitch8/CPSM/releases/latest), then:

```bash
chmod +x CPSM-0.2.0-x86_64.AppImage

# Run it directly — nothing is installed, no dependencies are resolved for you
./CPSM-0.2.0-x86_64.AppImage

# Or install properly: menu entry, PATH symlink, missing packages pulled in
./install.sh CPSM-0.2.0-x86_64.AppImage          # this user only
sudo ./install.sh CPSM-0.2.0-x86_64.AppImage     # every user on the machine
```

The first launch shows a Welcome dialog: **Open** an existing `.cpsm.yaml`,
**Import** a legacy `~/.claude-projects.yaml`, or start with an **Empty**
config.

### Run from source

```bash
git clone https://github.com/ipswitch8/CPSM.git
cd CPSM
python -m venv .venv
source .venv/bin/activate                    # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
python -m cpsm
```

Requires Python 3.11+. On Linux you also need `libsecret-1-0` and `libssl3`
from your distribution.

---

## Table of contents

- [Installing](#installing)
- [Configuration file](#configuration-file)
- [Launch profiles](#launch-profiles)
- [Claude Code Remote Control](#claude-code-remote-control)
- [Groups and layouts](#groups-and-layouts)
- [GUI tour](#gui-tour)
- [SSH key management](#ssh-key-management)
- [Session discovery and adoption](#session-discovery-and-adoption)
- [Cross-platform notes](#cross-platform-notes)
- [Building from source](#building-from-source)
- [Configuration paths and env vars](#configuration-paths-and-env-vars)
- [Contributing](#contributing)
- [License](#license)

---

## Installing

| Method | Audience | Command |
|---|---|---|
| **AppImage** (recommended) | Linux end users | `./CPSM-0.2.0-x86_64.AppImage` |
| **`install.sh`** | Linux end users wanting menu integration + deps | `./install.sh CPSM-0.2.0-x86_64.AppImage` |
| **pip / source** | Developers, packagers | `pip install -e ".[dev]"` |
| **PyInstaller bundle** | Packagers building installers | `pyinstaller --noconfirm packaging/cpsm.spec` |

`install.sh` autodetects the package manager (apt, dnf/yum, pacman, zypper,
apk) and installs `tmux`, `sshpass`, `openssh-client`, plus a terminal emulator
if none is found. It is idempotent — re-running upgrades the AppImage in place.
A system-wide install also removes a shadowing per-user install, which is what
otherwise makes a successful upgrade appear to do nothing.

Register the freedesktop launcher (only needed if you ran the AppImage
manually rather than via `install.sh`): the Welcome dialog prompts on first
run, or you can do it from **Tools → Settings**.

---

## Configuration file

CPSM uses a single YAML file (default: `~/.cpsm.yaml`) validated by Pydantic
v2. Every change in the GUI auto-saves — there is no Save button. Comments
and key order survive round-trip saves.

Top-level keys:

```yaml
schema_version: 1
settings:                  # global preferences
ssh_keys:        []        # SSH key registry (see SSH key management)
connections:     []        # individual hosts/sessions
groups:          []        # named bundles of connections (each owns a layout)
launch_templates:[]        # custom `bash` snippets for profile=custom
```

Every id must match `^[a-z0-9][a-z0-9-]{1,62}$`.

### `settings` block

| Key | Default | Notes |
|---|---|---|
| `default_multiplexer` | `auto` | `tmux` / `itmux` / `psmux` / `auto` |
| `default_terminal` | `auto` | `gnome-terminal`, `konsole`, `alacritty`, `kitty`, `xterm`, `wezterm`, `wt` |
| `ssh_binary` | `auto` | `openssh` or `plink` |
| `default_claude_options` | `--resume` | Appended to every `claude` invocation |
| `default_ssh_options` | `-o ConnectTimeout=10 -o ServerAliveInterval=30` | Appended after CPSM's own flags. Setting `IdentitiesOnly` here overrides CPSM's default pin — see [SSH identity pinning](#ssh-identity-pinning) |
| `known_hosts_strict` | `true` | When `false`, accepts unknown host keys |
| `status_poll_interval_ms` | `3000` | How often the GUI polls tmux for status |
| `layout_conflict_default` | `move` | `move` / `keep` / `error` when re-launching with a different layout |
| `layout_preserve_on_remove` | `true` | Removing a pane leaves an empty placeholder so neighbors don't shift |
| `log_level` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |

All of these are editable from **Tools → Settings**.

---

## Launch profiles

Each connection has a `launch_profile` that selects how it starts. The
Connection editor in the GUI shows only the fields valid for the chosen
profile.

### `claude-remote` — Claude Code over SSH

```yaml
- id: api-service
  launch_profile: claude-remote
  host: 192.168.1.10
  user: ubuntu
  port: 22                         # default 22
  project_folder: /srv/api-service
  claude_options: "--resume"
  identity_file_ref: prod-key      # references ssh_keys[].id
  auth_method: ask                 # ask | key | password
  sudo_user: appuser               # optional `su` step on the remote
  jump_host: bastion@10.0.0.1      # optional, up to 4 hops
  keepalive_interval_s: 30
  connection_timeout_s: 10
  pre_commands:  ["nvm use 20"]
  post_commands: []
  env: { LANG: "C.UTF-8" }
  auto_reconnect: true
  auto_reconnect_on_clean_exit: false
  reconnect_backoff_ms: [1000, 3000, 10000, 30000]
  reconnect_max_attempts: 0        # 0 = unlimited
  tags: [prod]
  notes: "Pinned to Node 20 LTS"
```

### `claude-local` — Claude Code on this machine

```yaml
- id: my-project
  launch_profile: claude-local
  project_folder: /home/me/code/my-project
  claude_options: "--resume"
```

### `ssh-shell` — Plain interactive SSH

```yaml
- id: bastion
  launch_profile: ssh-shell
  host: bastion.example.net
  user: ops
  identity_file_ref: ops-key
  project_folder: /srv/ops          # optional: cd here on connect
```

### `local-shell` — Local terminal at a directory

```yaml
- id: home
  launch_profile: local-shell
  project_folder: /home/me
```

### `custom` — Anything else, via a launch template

```yaml
launch_templates:
  - id: db-shell
    description: "Connect to the production read replica"
    bash: |
      ssh -t {{ user }}@{{ host }} 'psql -U {{ env.PGUSER }} -d {{ env.PGDATABASE }}'

connections:
  - id: prod-db
    launch_profile: custom
    custom_template_id: db-shell
    user: dba
    host: db-replica.internal
    env: { PGUSER: dba, PGDATABASE: app }
```

The template body is rendered with the connection's fields as context.
Manage templates in **Tools → Launcher Templates**.

---

## Claude Code Remote Control

Available on the two Claude profiles (`claude-remote`, `claude-local`).
When enabled, the launch appends `--remote-control <name>` so the running
Claude session becomes drivable from **claude.ai** and the phone app —
no SSH tunnel needed once the session is registered. Requires Claude Code
**v2.1.51+** on the target box.

### How it works at runtime

The remote (or local) Claude process keeps its own outbound HTTPS
connection to the Anthropic API and polls for instructions. CPSM's job is
limited to:

1. Append the `--remote-control <name>` flag at launch.
2. Walk you through the one-time `/login` OAuth bootstrap.

After bootstrap, neither CPSM nor your workstation are in the data path —
your phone reaches each remote Claude directly via Anthropic's relay.

### Per-connection settings

Edit any `claude-remote` or `claude-local` connection. Under **Claude
Options** you'll see:

- **Enable** — checkbox; flips on `--remote-control`.
- **Set up auth…** — opens a guided wizard for the one-time `/login`.

Under **Advanced**:

- **Remote Control Name** — optional override for the label shown in your
  claude.ai session list. Blank = use the connection's `name` (or `id`).
  Only alphanumerics, `-`, `_`, `.` (no whitespace) — the value is
  rendered into the launcher's argv as a single token.

The corresponding YAML fields:

```yaml
- id: api-service
  launch_profile: claude-remote
  host: 192.168.1.10
  user: ubuntu
  project_folder: /srv/api-service
  claude_options: "--resume"
  remote_control_enabled: true
  remote_control_name: web-server-1     # optional; defaults to "name"
```

### One-time OAuth bootstrap (the wizard)

Remote Control needs a full **`/login`** session against claude.ai — API
keys and `setup-token` tokens do **not** work. Pressing **Set up auth…**
opens a wizard that:

1. **Pre-flight checks**, via SSH:
   - `uname -s` — aborts if the target is macOS (Keychain is inaccessible
     from non-GUI SSH sessions; run `/login` locally on a Mac instead).
   - `claude --version` — aborts if < 2.1.51.
   - `printenv ANTHROPIC_API_KEY` — warns if set (it must be unset for
     OAuth to take over).
2. **Opens an auth terminal** SSHed into the target with
   `-L <port>:localhost:<port>` (default `8080`; change if `/login`
   prints a different port). You then run, manually, inside that
   terminal:

   ```bash
   unset ANTHROPIC_API_KEY                # only if the warning fired
   claude
   /login                                 # choose the claude.ai option
   ```

   Paste the printed `http://localhost:<port>/...` URL into your local
   browser. The callback tunnels back to the remote listener and `/login`
   completes.
3. **Polls** the remote for `~/.claude/.credentials.json` every 3 s. When
   the file appears, the wizard advances and CPSM flips
   `remote_control_enabled: true` on the connection.

### Caveats

- **Linux remotes only.** macOS targets cannot be auth'd over SSH; the
  wizard refuses to proceed.
- **Tmux survival is automatic** — CPSM launches everything inside tmux
  so the registered session outlives the SSH connection that spawned it.
- **No new credential surface for CPSM.** The OAuth token lives in
  `~/.claude/.credentials.json` on the target box, owned and refreshed
  by `claude` itself. CPSM never sees it.
- **Anyone signed into your Claude account can drive the session**
  through claude.ai once it's registered. Don't enable RC on a target
  you wouldn't hand the keys to.

---

## Groups and layouts

A **group** bundles connections that should run together as panes within one
tmux session. Each group **owns its layout** — the screen-map geometry is a
property of the group, not a separate top-level concept.

```yaml
groups:
  - id: backend
    name: "Backend services"
    color: "#3FA9F5"               # rendered in sidebar + screen map
    members: [api-service, worker]
    launch_order: parallel         # sequential | parallel
    launch_delay_ms: 0
    isolation: shared              # shared | per-group
    layout_conflict: move          # move | keep | error
    auto_attach: false
```

- **`isolation: shared`** — all groups attach to one tmux session named
  `cpsm-shared`. Pane indices are global across the session.
- **`isolation: per-group`** — each group gets a session named after its id.

The pane geometry for a group is described by viewports placed at percentage
coordinates on each physical monitor:

```yaml
screen_layouts:
  - id: backend-layout
    name: "Backend on monitor 0"
    monitors:
      - identifier: "DP-1"          # OS-reported monitor name (optional)
        monitor_index_hint: 0       # fallback when identifier doesn't match
        viewports:
          - id: top-left
            geometry_pct: { x:  0, y:  0, w: 50, h: 50 }
            tmux_layout: tiled       # tiled | even-h | even-v | main-h | main-v | custom
            panes:
              - { connection_id: api-service }
              - { connection_id: worker }
```

Validation enforces:
- viewport ids unique within a monitor;
- viewports within one monitor cannot overlap by more than 1% area;
- a `connection_id` cannot appear twice in the same viewport;
- `inherits_from` must reference an existing layout (no cycles).

CPSM also supports nested **split trees** (mixed horizontal/vertical splits
with explicit ratios) — automatically derived from the flat `panes` list when
absent, edited live in the screen map widget when present.

---

## GUI tour

CPSM is GUI-only. Everything is reachable from menus, the sidebar, or the
screen map.

### Window layout

```
┌──────────────────────────────────────────────────────────────────┐
│ Menu bar:   File │ Edit │ View │ Sessions │ Tools │ Help         │
│ Toolbar                                                          │
├──────────────────┬───────────────────────────┬───────────────────┤
│ Sidebar          │ Screens canvas            │ Inspector         │
│ (dock left)      │ (visual screen map)       │ (dock right)      │
│                  │                           │                   │
│  ▼ Connections   │  ┌────────┬────────┐      │  Properties of    │
│    api-service   │  │  api…  │ worker │      │  the selected     │
│    worker        │  ├────────┴────────┤      │  sidebar item     │
│                  │  │   web-frontend  │      │                   │
│  ▼ Groups        │  └─────────────────┘      │                   │
│    backend       │                           │                   │
│                  │                           │                   │
│  ▼ Discovered    │                           │                   │
│    (auto-found)  │                           │                   │
├──────────────────┴───────────────────────────┴───────────────────┤
│ Status bar: config path · multiplexer health · validation status │
└──────────────────────────────────────────────────────────────────┘
```

- **Sidebar** (dock, left) — Connections, Groups, and Discovered. Each
  Connection nests under its Group, and matched discovered sessions nest
  under their owning Connection.
- **Screens canvas** (centre) — live render of your physical monitors with
  the active group's layout overlaid. Drag/drop, splits, status colors, etc.
- **Inspector** (dock, right) — properties of the currently-selected
  sidebar item. Toggle with **View → Toggle Inspector** (F4).
- **Status bar** (bottom) — current `.cpsm.yaml` path, multiplexer health,
  validation state.

### Menu bar

| Menu | Items |
|---|---|
| **File** | New Connection · New Group · Open Config · Import (legacy YAML) · Quit |
| **Edit** | Find |
| **View** | Toggle Inspector |
| **Sessions** | Launch (selected) · Stop · Reconnect |
| **Tools** | Launcher Templates · Manage SSH Keys · Settings |
| **Help** | About |

Saves and validation happen automatically — there is no explicit Save or
Validate command.

### Connection editor

Profile-aware form: fields appear/disappear as you switch `launch_profile`.
Validates as you type; saves are gated on a clean validation pass.

Covered:
- identity (id, name, profile)
- network (host, port, user, jump host, keepalive, connection timeout)
- auth (`identity_file_ref`, `auth_method`, `sudo_user`)
- environment (`env` key/value table, `pre_commands`, `post_commands`)
- launch behavior (`claude_options`, `project_folder`, `custom_template_id`)
- reconnect policy (auto-reconnect, backoff, max attempts)
- metadata (tags, notes)

Duplicating a connection auto-opens the editor on the new copy.

### Group editor

Drag connections into the members list, set color, isolation, launch order,
and the default layout.

### Layout editor

Add/remove monitors, rename viewports, edit geometry numerically, pick the
tmux layout per viewport.

### Screens canvas

A live render of your physical monitors with the active group's layout
overlaid.

- **Drag-drop** — drag a connection from the sidebar onto a viewport edge or
  center. Edge zones split, center zone replaces. Ambiguous center drops
  open a disambiguation popup.
- **Modifiers** — `Shift` forces horizontal split, `Ctrl` forces vertical,
  `Alt` swaps with the existing pane.
- **Multi-group overlay** — toggle multiple groups visible at once;
  conflicting panes are marked.
- **Status colors** (4-state model) — green (running + attached), amber
  (running, detached), red (failed/exited), gray (unknown / not yet polled).
- **Empty-pane preservation** — removing a pane leaves a `_placeholder.sh`
  process so neighbors never shift; close the placeholder explicitly to
  re-tile.

### SSH key manager

**Tools → Manage SSH Keys.** Generate keys with `ed25519`/`rsa`/`ecdsa`,
deploy via `ssh-copy-id`, and review which connections each key was deployed
to. Passphrases are stored in the OS keychain (libsecret on Linux,
Credential Manager on Windows, Keychain on macOS) — never in YAML.

### Launcher templates dialog

**Tools → Launcher Templates.** CRUD for the `launch_templates[]` array.
Live preview of the rendered bash against a sample connection.

### Settings dialog

**Tools → Settings.** All keys from the [`settings` block](#settings-block),
plus a multiplexer auto-detect + path probe button.

### Other dialogs

These are reached from context menus, the screen map, or the launch flow —
not the menu bar.

- **Welcome** — first-run dispatcher (Open / Import / Empty).
- **Adopt session** — right-click a Discovered session to claim it as a CPSM
  connection (pre-fills from the discovery data).
- **Launch conflict** — when launching a connection that already has a live
  session: focus existing, kill+relaunch, or open in a new pane.
- **Generate / deploy SSH key** — wizards wrapping `ssh-keygen` and
  `ssh-copy-id`.
- **Drop disambiguation** — when a center-drop on the screen map is
  ambiguous (replace? split? swap?).
- **Screens save** — confirm overwrite when saving over an existing layout.
- **Validation errors** — modal popup if an auto-save would write an
  invalid document; lists every issue with click-to-jump.
- **About** — version, license, build info.

---

## SSH key management

```yaml
ssh_keys:
  - id: prod-key
    name: "Production deploy key"
    type: ed25519
    private_path: ~/.ssh/cpsm_prod_ed25519
    public_path:  ~/.ssh/cpsm_prod_ed25519.pub
    passphrase_ref: keyring://cpsm/prod-key       # OS keychain only
    created_at: 2026-04-01T12:00:00Z
    deployments:
      - connection_id: api-service
        deployed_at:   2026-04-01T12:05:00Z
        method:        ssh-copy-id
```

- **Keys never live in YAML.** Only paths and a keyring reference.
- **Auth method per connection:**
  - `ask` — prompt on first launch and remember the choice;
  - `key` — always use `identity_file_ref`, deploy if needed. The key is
    *pinned*: see [SSH identity pinning](#ssh-identity-pinning);
  - `password` — force password auth (never prompt for key).
- **Deployment tracking** — every successful `ssh-copy-id` writes an entry
  under the key's `deployments[]` list.

Edit the registry from **Tools → Manage SSH Keys**.


### SSH identity pinning

Whenever CPSM hands `ssh` an explicit key with `-i`, it also passes
`-o IdentitiesOnly=yes`:

```
ssh -o IdentitiesOnly=yes -i ~/.ssh/utility root@192.0.2.44
```

**Why.** `-i` alone does not restrict ssh to that key. OpenSSH treats it as an
*addition* to the identities it already intends to offer — every key held by
`ssh-agent`, plus the default `~/.ssh/id_*` files. `ssh -G host` reports the
default plainly:

```
$ ssh -G example.com | grep -i '^identitiesonly'
identitiesonly no
```

Two consequences. The key a connection is configured with need not be the key
that actually authenticates, so any server-side authorisation keyed to the
intended key is bypassed with no signal to the operator. And `sshd`'s default
`MaxAuthTries` is 6, so an agent holding more keys than that can have the
connection torn down with *"Too many authentication failures"* before the
right key is ever offered.

Pinning remains compatible with `ssh-agent`: the agent still performs the
signing, it simply may no longer volunteer keys nobody asked for.

> **Scope of these claims.** The four statements above are about *OpenSSH's*
> documented behaviour, not CPSM's: that `-i` is additive, that
> `IdentitiesOnly` defaults to `no`, that `sshd`'s `MaxAuthTries` defaults to
> 6, and that the pin restricts which identities are *offered* rather than
> whether the agent may sign. They were verified against **OpenSSH 9.7p1**
> (`ssh -G`, `man ssh_config`, `man sshd_config`) and are not pinned by any
> test in this repository — CPSM's own test suite asserts the argv CPSM
> *builds*, which is a different claim. If your OpenSSH differs, trust your
> `ssh -G` output over this paragraph.

**Where it applies.** Every path that passes an explicit key — the launcher
templates (`ssh-shell`, `claude-remote`, including their `scp` upload), the
status and correlation probes, the remote-control preflight, and the
connection-test worker.

**Where it deliberately does not.** Key *deployment* (`Tools → Manage SSH
Keys`, and the automatic deploy on first launch). Deployment is the bootstrap
case: the key being installed is by definition not yet in the target's
`authorized_keys`, so the connection has to authenticate by some other means —
an existing agent key, a password, or a pre-existing default key. Pinning would
narrow exactly that set and break deploying to any host reachable only via an
agent-held key. Where a stronger constraint is wanted, the password path
already forces `PubkeyAuthentication=no` and `IdentityAgent=none`.

**Overriding it.** Set `IdentitiesOnly` yourself in `default_ssh_options` and
CPSM will not add its own — your value wins, in any spelling `ssh` accepts
(`-o IdentitiesOnly=no`, `-oIdentitiesOnly=no`, any case). CPSM only fills the
gap when you have expressed no preference.

---

## Session discovery and adoption

CPSM walks `/proc` (Linux) on every poll cycle and classifies running
processes into:

- **`claude-local`** — a local `claude` process you started outside CPSM.
- **`claude-remote`** — an `ssh ... claude` invocation.
- **`ssh-shell`** — a plain interactive `ssh`.
- **`tmux-session`** — a `tmux` server you might want to attach.

Discovered sessions show up under the sidebar's **Discovered** node and are
**auto-correlated** to existing connections by:

1. **`claude-local` matcher** — exact `cwd` match against
   `connections[].project_folder`.
2. **Remote host+user matcher** — single match → assigned. Multiple matches
   sharing host+user (e.g. three `root@192.0.2.44` connections that differ
   only by `project_folder`) are deferred to step 3.
3. **Remote probe** — CPSM SSHes into the remote, reads `/proc` for the ssh
   peer's PID, extracts its `cwd`, and matches that against each candidate
   Connection's `project_folder`.

Right-click a discovered row → **Adopt** to convert it into a real
`connections[]` entry, pre-filled from the discovery data.

Set `CPSM_LOG_LEVEL=INFO` in the environment to trace probe activity:

```bash
CPSM_LOG_LEVEL=INFO ./CPSM-0.2.0-x86_64.AppImage 2>/tmp/cpsm.log
```

---

## Cross-platform notes

| Platform | Multiplexer | Terminal default | Notes |
|---|---|---|---|
| Linux | `tmux` | `gnome-terminal` / `konsole` / `xterm` (auto) | Primary target. `python-xlib` enables monitor-name fallback. |
| Windows | `itmux` (tmux-compatible) | Windows Terminal (`wt`) | Sockets via named pipes. PowerShell quoting handled internally. |
| Windows | `PSMUX` (PowerShell-native) | Windows Terminal | JSON cmdlet output; pixel vs cell geometry normalized in the backend. |
| macOS | `tmux` | iTerm2 / Terminal.app | Untested on every release; should work for `claude-remote` and `ssh-shell`. |

`settings.default_multiplexer: auto` probes for tmux first, then itmux, then
PSMUX. Override per environment by setting `CPSM_MULTIPLEXER`.

---

## Building from source

```bash
# Prerequisites: Python 3.11+, libsecret-1-0 + libssl3 on Linux
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Run tests
pytest -q

# Lint + type-check
ruff check .
mypy cpsm

# Build a one-folder PyInstaller bundle (dist/cpsm/)
pyinstaller --noconfirm packaging/cpsm.spec

# Build the Linux AppImage
# (NOT appimage-builder — its AppRun LD_PRELOADs libapprun_hooks.so and
#  leaks ~413 MB/day; see docs/MEMORY-LEAK-INVESTIGATION.md)
CPSM_VERSION=0.2.0 scripts/build_appimage_plain.sh
```

The minimal buildable fileset (for distributing source):

```
cpsm/                                # entire package incl. resources/
pyproject.toml
README.md
LICENSE
packaging/cpsm.spec
scripts/build_appimage_plain.sh       # Linux AppImage build
install.sh                           # Linux installer
tests/                               # strongly recommended
docs/SPEC-v1.3.md                    # design spec
```

`build/`, `dist/`, `AppDir/`, `appimage-build/`, `.venv/`, `__pycache__/`,
`.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`, and the AppImage itself are
all regenerated on build.

---

## Configuration paths and env vars

Config path is resolved in this order (first hit wins):

1. `$CPSM_CONFIG` environment variable
2. `$XDG_CONFIG_HOME/cpsm/.cpsm.yaml` (Linux) /
   `%APPDATA%\cpsm\.cpsm.yaml` (Windows)
3. `~/.cpsm.yaml`

Env vars CPSM honors:

| Var | Effect |
|---|---|
| `CPSM_CONFIG` | Override the config-file path |
| `CPSM_LOG_LEVEL` | `DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL` — overrides `settings.log_level` |
| `CPSM_MULTIPLEXER` | Force `tmux`/`itmux`/`psmux` |
| `XDG_CONFIG_HOME` | Standard Linux config root |
| `APPDATA` | Standard Windows config root |

---

## Contributing

Issues and pull requests are welcome at
<https://github.com/ipswitch8/CPSM>.

Before opening a PR:

```bash
pip install -e ".[dev]"

# lint-and-type
ruff check cpsm tests
ruff format --check cpsm tests
mypy cpsm
pytest tests/lint/test_no_real_infrastructure.py -q --no-cov

# tests
QT_QPA_PLATFORM=offscreen pytest -q     # the full suite must pass
```

CI runs these on Python 3.11 and 3.12. Two differences worth knowing before
you are surprised by a red build you cannot reproduce:

- The Windows job runs `pytest -q -m "not integration"` — integration tests
  need a real tmux and are Linux-only.
- The infrastructure-disclosure lint fails the build if a routable IP address,
  a vendor key name, or a developer's home directory reaches a tracked file.
  It is the check most likely to fail on an otherwise good change, because
  test fixtures are easy to seed by pasting from a real config. Use
  `192.0.2.x` / `198.51.100.x` / `203.0.113.x` (RFC 5737) and `example.com`
  (RFC 2606) instead.

The test suite is the specification for a lot of subtle behaviour — SSH
identity pinning, session discovery, layout reconciliation — so a change that
needs a test weakened is usually a change that needs rethinking.

---

## License

AGPLv3 — see [LICENSE](LICENSE).
