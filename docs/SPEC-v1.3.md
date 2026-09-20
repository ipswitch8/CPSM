# Design Specification: Cross-Platform Session Manager (CPSM) — v1.3

**Project Code Name:** CPSM (Claude Projects Session Manager)
**Target Users:** System administrators and engineers managing multiple SSH/tmux sessions
**Document Version:** 1.3
**Date:** 2026-04-26

## 1. Application Architecture

### 1.1 Framework: Python 3.11+ with PySide6 (Qt 6.6+)

PySide6 (LGPL Qt 6). Justified by native look on Linux/Windows, mature `QScreen` multi-monitor APIs, strong Qt threading, rich `QGraphicsScene` drag-drop, mature Python ecosystem (ruamel.yaml, cryptography, keyring, pydantic), Selenium-friendly via `objectName`/accessible names.

### 1.2 Layered Architecture

UI → Controllers → Services → Platform Abstraction → Data. Strict separation; UI does no I/O.

### 1.3 Module Layout

```
cpsm/
├── __main__.py
├── app.py
├── ui/
│   ├── main_window.py
│   ├── widgets/{screen_map,session_list,connection_form,group_panel,group_legend}.py
│   └── dialogs/{welcome,import_preview,reimport_merge,connection_editor,
│                group_editor,layout_editor,ssh_key_manager,settings,
│                launcher_templates,drop_disambiguation,validation_errors}.py
├── controllers/{session,config,layout,import,template}_controller.py
├── services/{config,session,key,monitor,import,layout,template}_service.py
├── platform/{base,tmux_backend,itmux_backend,psmux_backend,
│             terminal_launcher,ssh_binary,paths}.py
├── data/{schema,repository,importer,migrations}.py
├── workers/{ssh_worker,status_poller,reconnect_worker}.py
└── resources/
    ├── icons/
    ├── launcher_templates/{claude-remote,claude-local,ssh-shell,
    │                        local-shell,_placeholder}.sh
    └── translations/
```

### 1.5 Threading

UI thread: Qt event loop only. `QThreadPool` for short ops. `QThread` for status poller + reconnect workers. Cancellation via `QAtomicInt`. No asyncio.

## 2. Data Model

### 2.1 Filename

Canonical `.cpsm.yaml`. Resolution: `--config` → `$CPSM_CONFIG` → `$XDG_CONFIG_HOME/cpsm/.cpsm.yaml` (Linux) / `%APPDATA%\cpsm\.cpsm.yaml` (Windows) → `~/.cpsm.yaml`. Legacy `.claude-projects.yaml` is read-only input.

### 2.2 Schema (annotated)

```yaml
schema_version: 1

settings:
  default_multiplexer: tmux           # tmux|itmux|psmux|auto
  default_terminal: auto              # auto|wt|gnome-terminal|konsole|alacritty|kitty|xterm|wezterm
  ssh_binary: auto                    # auto|openssh|plink
  default_claude_options: "--resume"
  default_ssh_options: "-o ConnectTimeout=10 -o ServerAliveInterval=30"
  known_hosts_strict: true
  status_poll_interval_ms: 3000
  layout_conflict_default: move       # move|keep|error
  layout_preserve_on_remove: true     # empty-slot placeholder vs kill+redistribute
  log_level: INFO

ssh_keys:
  - id: key-prod-2026
    name: "Production ed25519"
    type: ed25519                     # ed25519|rsa|ecdsa
    private_path: ~/.ssh/id_ed25519_prod
    public_path: ~/.ssh/id_ed25519_prod.pub
    passphrase_ref: keyring://cpsm/key-prod-2026
    created_at: 2026-01-15T10:00:00Z
    deployments:
      - { connection_id: web01, deployed_at: 2026-01-15T10:05:00Z, method: ssh-copy-id }

connections:
  - id: web01
    name: "WebApp-Frontend (prod)"
    launch_profile: claude-remote     # claude-remote|claude-local|ssh-shell|local-shell|custom
    host: dev.example.com
    port: 22
    user: ubuntu
    sudo_user: claude-user
    identity_file_ref: key-prod-2026
    jump_host: bastion
    project_folder: /opt/webapp/frontend
    claude_options: "--resume"
    env: { TERM: xterm-256color, LANG: en_US.UTF-8 }
    pre_commands: []
    post_commands: []
    keepalive_interval_s: 60
    connection_timeout_s: 15
    auto_reconnect: false
    auto_reconnect_on_clean_exit: false
    reconnect_backoff_ms: [1000, 3000, 10000, 30000]
    reconnect_max_attempts: 0          # 0 = unlimited
    tags: [production, web]
    notes: "..."

  - id: dotfiles
    name: "Dotfiles (local)"
    launch_profile: claude-local
    project_folder: ~/projects/dotfiles
    claude_options: "--resume"

  - id: bastion-shell
    launch_profile: ssh-shell
    name: "Bastion shell"
    host: bastion.example.com
    user: admin
    identity_file_ref: key-prod-2026
    project_folder: /var/log

  - id: scratch
    launch_profile: local-shell
    name: "Scratch shell"
    project_folder: ~/scratch

  - id: nspawn-dev
    launch_profile: custom
    name: "Dev container"
    custom_template_id: tpl-nspawn-machinectl
    project_folder: /var/lib/machines/dev/srv/app
    env: { MACHINE: dev }

groups:
  - id: project-1
    name: "Project 1"
    color: "#3b82f6"
    members: [web01, dotfiles, bastion-shell]
    launch_order: sequential          # sequential|parallel
    launch_delay_ms: 250
    default_layout_id: layout-project-1
    isolation: shared                 # shared|per-group
    layout_conflict: move             # move|keep|error
    auto_attach: true

screen_layouts:
  - id: layout-project-1
    name: "Project 1 — dual screen"
    inherits_from: null
    monitors:
      - identifier: "DELL-U2723QE-Screen1"
        monitor_index_hint: 0
        viewports:
          - id: vp-p1-main
            geometry_pct: { x: 0, y: 0, w: 100, h: 100 }
            tmux_window_name: "p1-main"
            tmux_layout: custom        # tiled|even-h|even-v|main-h|main-v|custom
            custom_layout_string: "f8b6,238x60,..."
            panes:
              - { connection_id: web01 }
              - { connection_id: null }    # empty slot, intentionally preserved
              - { connection_id: dotfiles }
              - { connection_id: bastion-shell }

scenes:
  - id: workday
    groups: [project-1, project-2]
    on_conflict: error                 # error|first-wins|last-wins

launch_templates:
  - id: tpl-nspawn-machinectl
    description: "Enter a systemd-nspawn container."
    bash: |
      machinectl shell {{user|root}}@{{env.MACHINE}} /bin/bash -c \
        "cd {{project_folder}} && exec bash -il"
```

### 2.3 Launch-Profile Field Matrix

| Field | claude-remote | claude-local | ssh-shell | local-shell | custom |
|---|:-:|:-:|:-:|:-:|:-:|
| host | required | forbidden | required | forbidden | optional |
| port | required | forbidden | required | forbidden | optional |
| user | required | forbidden | required | forbidden | optional |
| sudo_user | optional¹ | optional² | optional¹ | optional² | optional |
| identity_file_ref | required | forbidden | required | forbidden | optional |
| jump_host | optional | forbidden | optional | forbidden | optional |
| project_folder | required (remote) | required (local) | optional (remote cwd) | required (local) | optional |
| claude_options | required³ | required³ | forbidden | forbidden | optional |
| custom_template_id | forbidden | forbidden | forbidden | forbidden | required |
| keepalive_interval_s, connection_timeout_s | optional | forbidden | optional | forbidden | optional |
| env, pre_commands, post_commands, auto_reconnect family | optional | optional | optional | optional | optional |

¹ Remote `su - <sudo_user>`. ² Local `sudo -u <sudo_user> -i`. ³ Defaults to `settings.default_claude_options`.

### 2.5 Validation Rules

- ID slug regex: `^[a-z0-9][a-z0-9-]{1,62}$`.
- FK integrity on save (jump_host, identity_file_ref, custom_template_id, members, panes[].connection_id, default_layout_id, inherits_from, scenes.groups). `null` connection_id skips FK check.
- jump_host chains acyclic, max depth 4.
- Discriminated union enforced per launch_profile.
- viewports[].id unique within layout. panes[].connection_id unique within viewport (treating null as not-a-key).
- Same non-null connection_id may appear across viewports/layouts (multi-group).
- geometry_pct: 0 ≤ x,y ≤ 100; w,h > 0; x+w ≤ 100; y+h ≤ 100.
- Viewports within a single monitor in a single layout: no overlap > 1%. Cross-layout overlap allowed.
- Connection deletion when in any group: confirm + convert pane refs to null.

## 3. GUI

Main window: menu bar / toolbar / sidebar tree (with profile icons 🔗💻⌨$⚙) / tabbed main content (Connections, Groups, Layouts, Screen Map, Active Sessions) / inspector dock / status bar.

Dialogs: dlg_welcome, dlg_import_preview, dlg_reimport_merge, dlg_connection_editor, dlg_group_editor, dlg_layout_editor, dlg_ssh_key_manager, dlg_generate_key, dlg_deploy_key, dlg_settings, dlg_launcher_templates, dlg_drop_disambiguation, dlg_validation_errors, dlg_about, dlg_confirm_destroy, dlg_launch_progress.

Selenium: every interactive widget gets stable `setObjectName()` from domain id (never positional indexes); `setAccessibleName()`, `setAccessibleDescription()`. CI lint enforces.

Shortcuts: Ctrl+N new conn, Ctrl+Shift+N new group, Ctrl+S save, Ctrl+O open, Ctrl+I import, Ctrl+L validate, F5 launch, Ctrl+Shift+L scene, Shift+F5 stop, Ctrl+R reconnect, F4 inspector, Ctrl+F find, Ctrl+1-5 tabs, Ctrl+M Live↔Preview, Ctrl+T templates. Drag modifiers: Shift forces horizontal split, Ctrl forces vertical, Alt disables snap, Shift+drag-out forces Kill Pane.

## 4. YAML Editor

ruamel.yaml round-trip (typ='rt'). Pydantic v2 discriminated union on launch_profile. Atomic writes. Comments preserved. First-run welcome offers Import / Empty / Open. Import never modifies source `.claude-projects.yaml`. Re-import shows three-way merge.

Connection Editor field set adapts to selected `launch_profile` (per §2.3). Switching profile clears now-forbidden fields with confirm. "Test Connection" runs `ssh -o BatchMode=yes -o ConnectTimeout=5 user@host true` for remote profiles, `Path.is_dir()` for local, renders preview for custom.

Group Editor: two-list drag-drop with profile icons; multi-group cue (info icon, tooltip). Color picker. launch_order, launch_delay_ms, default_layout_id, isolation, layout_conflict.

## 5. Sessions

### 5.1 Identity

One tmux session per connection: `cpsm-<connection_id>`. Multi-group launches reuse. `groups[].isolation: per-group` opts out → `cpsm-<group_id>-<connection_id>`.

### 5.2 Launch Profiles

| Profile | Behavior |
|---|---|
| claude-remote | SCP launcher → `ssh -tt … "su - <sudo_user> -c 'bash <script>'"` → cd → claude $opts → `[r/s/q]` loop |
| claude-local | cd → `[sudo -u <sudo_user> -i] claude $opts` → `[r/s/q]` loop |
| ssh-shell | `ssh -tt user@host -- "[cd <folder>;] exec bash -il"` (no `[r/s/q]` loop) |
| local-shell | `cd <folder>; exec $SHELL -il` |
| custom | Renders launch_templates[<id>].bash via `{{...}}` mustache |

Built-in templates in `cpsm/resources/launcher_templates/`. User-editable via dlg_launcher_templates with "Restore Default".

### 5.3 Pane Lifecycle: Empty Pane vs Kill Pane

| Op | tmux command | Layout effect | Default trigger |
|---|---|---|---|
| Empty Pane | `respawn-pane -k -t <target> "bash <_placeholder.sh>"` | None — geometry preserved | "Remove from Layout"; drag-out; connection deleted |
| Kill Pane | `kill-pane -t <target>` | tmux redistributes per current layout | Right-click → Kill Pane; Shift+drag-out |

`_placeholder.sh`:
```bash
#!/bin/bash
clear
printf '\033[2;37m[ empty slot — drop a connection here ]\033[0m\n'
exec sleep infinity
```

`settings.layout_preserve_on_remove: false` flips defaults to kill+redistribute.

### 5.4–5.6 Launch Flows

Single: `SessionService.launch(id)` → resolve session name → ensure session exists with `remain-on-exit on` → render launcher → `respawn-pane -k` or `split-window` → spawn terminal at viewport geometry → status poller picks up.

Group: resolve `default_layout_id` → for each viewport, launch terminal at `geometry_pct` translated to absolute pixels → attach to `cpsm-<connection_id>` session(s) → null-id panes spawn as `_placeholder.sh` → apply layout (custom_layout_string or preset) → sequential honors launch_delay_ms; parallel via worker pool.

Scene: launches groups in order applying `on_conflict` (error/first-wins/last-wins).

### 5.7 Disconnect / Reconnect

Detection: status poller watches `#{pane_dead}`. `remain-on-exit on` keeps dead panes visible. `capture-pane -p -S -200` for last 2KB.

State: Connected (green) / Connecting (yellow) / Stale (orange) / Error (red) / Disconnected clean (gray) / Empty slot (dashed) / Host unreachable.

Profile semantics: claude-* have in-pane `[r/s/q]` loop, ssh-shell/local-shell don't. GUI Reconnect always = `respawn-pane -k -t <target> "bash <launcher>"`.

Auto-reconnect: walks `reconnect_backoff_ms`; gates on non-zero exit by default; `auto_reconnect_on_clean_exit: true` overrides.

### 5.8 Active Sessions Panel

QTableView columns: Status icon | Profile glyph | Connection | Launched From | tmux Target | Started | Latency (remote) | Actions (Attach/Reconnect/Kill). Empty-slot panes excluded.

## 6. Visual Screen Mapping

### 6.1 OS-Driven Detection

`QGuiApplication.screens()` → one `QScreen` per monitor. Used: geometry, availableGeometry, physicalSize, devicePixelRatio, orientation, manufacturer, model, serialNumber, name. Persistent identifier from manufacturer-model-serial (or index when EDID serial unavailable). Hot-plug via screenAdded/screenRemoved signals.

### 6.2 Widget

QGraphicsScene + QGraphicsView. Monitors as QGraphicsRectItem scaled to canvas (max 800px). Viewports as child rects at geometry_pct. Panes as children of viewports per tmux_layout. Profile glyph in pane corner. Empty-slot panes: dashed border, dim "▭ empty slot" label.

### 6.3 Mode Toggle: Live ↔ Preview (Ctrl+M)

### 6.4 Multi-Group Overlay

Group selector chips, edit-target dropdown, legend dock with eye/lock toggles, per-group save/revert. Auto-assigned colors, override via `groups[].color`. Other groups render at 50% opacity, locked.

### 6.5 Conflict Detection

Pairwise viewport intersection per monitor across visible groups. Red diagonal hatch on overlap regions.

### 6.6 Drop Targeting Zones

```
┌─────┬──────────────┬─────┐
│ TL  │     top      │ TR  │ ← top edge: split-window -v -b
├─────┼──────────────┼─────┤
│left │   center     │right│ ← left/right edges: split-window -h [-b]
│     │              │     │
├─────┼──────────────┼─────┤
│ BL  │   bottom     │ BR  │ ← bottom edge: split-window -v
└─────┴──────────────┴─────┘
```

Center on occupied: dlg_drop_disambiguation (Replace / Split right / Split below / Cancel). Center on empty-slot: silent respawn-pane.

Modifiers: Shift = force horizontal, Ctrl = force vertical, Alt = disable snap. Live hover preview of resulting geometry.

### 6.7 Gesture → tmux Map

| Gesture | Operation |
|---|---|
| Drag viewport (move/resize) | Update geometry_pct; in Live, TerminalLauncher.move(handle, x, y, w, h) if supported |
| Drop connection on empty-slot | `respawn-pane -k -t <target> "bash <launcher>"` (geometry exact) |
| Drop connection on occupied edge | `split-window -h\|-v [-b] -t <target> -P -F "#{pane_id}"` + send-keys + capture/lock layout |
| Drop connection on occupied center | dlg_drop_disambiguation; on split, capture/lock layout |
| Drag pane to pane (same window, center) | `swap-pane -s <src> -t <dst>` |
| Drag pane to pane (same window, edge) | swap-pane then respawn source as placeholder (per layout_preserve_on_remove) |
| Drag pane across viewports/monitors | break-pane source (→ placeholder), move-pane to dst, split per drop zone |
| Drag pane out to empty space | `break-pane -s <src> -d` (new window, same session); source → placeholder |
| Right-click → Remove from Layout | `respawn-pane -k -t <target> "bash <_placeholder.sh>"` |
| Right-click → Kill Pane | `kill-pane -t <target>` (warn) |
| Shift+drag-out | Force Kill Pane on source |
| Pane resize handle | `resize-pane -t <target> -x <w> -y <h>`; capture/lock layout |
| Apply preset (right-click) | `select-layout -t <window> tiled\|...`; warn about redistribution |
| Save Layout | `display-message -p "#{window_layout}"` → custom_layout_string; lock tmux_layout: custom |

### 6.8 Split-and-Lock Pattern

Every add/resize: issue tmux op → capture `#{window_layout}` → write to custom_layout_string → set tmux_layout: custom. Prevents silent redistribution.

## 7. Multiplexer Integration

### 7.1 Backend ABC

```python
class MultiplexerBackend(ABC):
    list_sessions, list_windows, list_panes
    new_session, attach_session, kill_session
    new_window, kill_window
    split_pane(target, direction, before=False), select_pane, swap_panes
    break_pane, move_pane, resize_pane
    send_keys, select_layout, capture_layout
    kill_pane, respawn_pane
    set_window_option, capture_pane
```

### 7.2 tmux Commands

All queries use `-F` pipe-delimited format strings. ProcessRunner.run([...], timeout=5). `set-window-option remain-on-exit on` per CPSM-managed window. `capture-pane -p -S -200` for stderr. `split-window -b` for before-target splits.

### 7.3 itmux (Windows): tmux-compatible; pixel vs cell geometry; sockets via named pipes.

### 7.4 PSMUX (PowerShell): cmdlet-driven via `pwsh -NoProfile -Command`; JSON output.

### 7.5 Normalization: abstract (session, window, pane) tuples; BackendCapabilities probed at startup.

### 7.6 Launcher Script Lifecycle

Render template → write `/tmp/cpsm-launcher-<id>-<pid>.sh` (0700 perms Linux) or `%TEMP%\...\.sh` Windows → `respawn-pane -k -t <target> "bash <path>"` → script handles its lifecycle. Cleanup on exit; stale > 24h pruned at startup.

Internal templates: claude-remote.sh, claude-local.sh, ssh-shell.sh, local-shell.sh (user-editable), _placeholder.sh (read-only display).

#### 7.6.1 Remote helper permissions (claude-remote)

`claude-remote` additionally uploads a helper to `/tmp/cpsm-remote-<conn_id>.sh`
**on the target host**. That host's `/tmp` is world-writable and shared with
every other account on it, and the helper names the project folder and the
claude options, so it must never be readable by "other".

End state: **owner = the SSH login user, group = `sudo_user`, mode 0750.** The
sudo account reaches the helper through the GROUP bit; leaving the group as the
SSH user's own would make 0750 unusable and is why the group is retargeted
rather than the world bits opened.

Group selection, in order:

1. the group NAMED for `sudo_user` (the usual per-user-group layout);
2. failing that, `sudo_user`'s primary group (`id -gn`);
3. failing that, a POSIX ACL (`setfacl -m u:<sudo_user>:r-x`) — which any file
   OWNER may set without privileges, covering an unprivileged SSH user that
   cannot `chgrp` into a foreign group;
4. failing all three, abort with a message. **No path widens the mode back to
   `o+rx`**: a failed launch is recoverable, a world-readable helper on a
   shared host is not. The launcher removes the helper rather than running one
   it could not lock down.

The upload window is closed as well as the final mode: the helper is
pre-created empty under `umask 077` before `scp`, which does not chmod an
existing file, so it is never briefly world-readable between landing and being
secured. The local staging copy is 0600 — it is only ever read, never executed
locally.

When `sudo_user` is empty there is no privilege drop, so mode 0750 with the SSH
user's own group is sufficient and no group retargeting happens.

Note the group retargeting also grants access to any existing SECONDARY members
of that group (e.g. a `cur` group that already contains `nginx`). That is
inherent to "group = the sudo account" and is still a large narrowing from
world-readable, but it is not zero group exposure.

`{{...}}` mustache: any connection field, `env.KEY`, `field|default` fallback.

## 8. Cross-Platform

Detection via `sys.platform`. Multiplexer auto-discovery: Linux=tmux only; Windows=itmux→PSMUX→WSL-tmux (advisory).

Paths via pathlib.Path. Config: $XDG_CONFIG_HOME/cpsm/.cpsm.yaml or %APPDATA%\cpsm\.cpsm.yaml; ~/.cpsm.yaml fallback. Local project_folder validated as dir at save; remote validated at launch.

Terminal launcher with geometry: wezterm/alacritty/kitty/konsole/xterm/gnome-terminal (via wmctrl fallback) on Linux; wt.exe/pwsh on Windows.

Quoting: shlex.quote (POSIX), subprocess.list2cmdline (Windows), PowerShellQuoter (PSMUX).

Line endings: write \n; tolerate \r\n.

SSH binary: OpenSSH preferred, plink Windows fallback. ssh-copy-id with manual fallback for Windows.

Subprocess: text=True, encoding="utf-8", errors="replace". Windows: CREATE_NO_WINDOW for background.

## 9. Implementation Notes

### 9.1 Library Stack

PySide6 ≥ 6.6, ruamel.yaml ≥ 0.18, pydantic ≥ 2.5, cryptography ≥ 42.0, keyring ≥ 24.0, subprocess+system ssh, wmctrl/python-xlib (Linux fallback), PyInstaller, AppImage, WiX MSI, pytest, pytest-qt, pytest-mock, Selenium.

### 9.2 Security

- Private keys never in YAML — paths only.
- Passphrases via OS keychain (`keyring://cpsm/<key_id>`) — libsecret/Credential Manager/Keychain.
- No plaintext passwords. Deploy-key dialog zeros buffer after use.
- known_hosts: respect ~/.ssh/known_hosts; strict mode adds StrictHostKeyChecking=yes; mismatches surface dedicated dialog.
- Refuse private keys with perms > 0600 (Linux) or non-owner-accessible (Windows).
- Logging redaction filter for keys/tokens/passwords.
- Config file 0600 (Linux) or owner-only ACL (Windows pywin32).
- Remote helper scripts uploaded by `claude-remote` are 0750 with the group set to `sudo_user` — never world-readable (see §7.6.1).
- Local profiles never invoke ssh/scp/plink (platform-layer guard).
- No telemetry.

### 9.3 UTF-8 Everywhere

All file I/O encoding="utf-8". subprocess text=True, encoding="utf-8", errors="replace". Qt: QStringConverter.Utf8. yaml.encoding = "utf-8". `# -*- coding: utf-8 -*-` header at top of every module.

### 9.4 Errors

Three-tier: Recoverable → Result[T,E] → toast; User-actionable → modal with remediation; Fatal → sys.excepthook + Qt handler → "Report Issue" dialog. Subprocess failures include exit code, last 2KB stderr, redacted command.

### 9.5 Logging

Rotating file `<config_dir>/logs/cpsm.log`, 5MB × 5. Default INFO. Per-module level. JSON option. Qt warnings via qInstallMessageHandler.

### 9.6 Testing

| Layer | Approach | Target |
|---|---|---|
| Schema | pytest + ruamel round-trip; discriminated-union per profile; null connection_id round-trip | 95% |
| Importer | pytest fixtures from real `.claude-projects.yaml` examples | 95% |
| Services | pytest mocked backends | 90% |
| TemplateService | snapshot tests per template | 95% |
| Drop-targeting logic | per-zone resolution unit tests | 95% |
| Split-and-lock invariant | every add gesture leaves tmux_layout=custom + non-null custom_layout_string | 100% |
| Empty-slot lifecycle | remove → placeholder same geometry; re-add → string unchanged | 100% |
| Backends | contract tests against real tmux (Linux CI), itmux (Windows CI) | 80% |
| UI widgets | pytest-qt; per-profile field visibility | 75% |
| Multi-group screen-map | synthetic QScreen mocks; visual regression | scenario |
| Drag-drop hover | pytest-qt + QTest.mouseMove through zones | scenario |
| E2E | Selenium Qt driver, Linux + Windows CI | scenario |

CI lint: every interactive widget has setObjectName().

### 9.7 Packaging

Linux: PyInstaller one-folder → AppImage. Also `pip install cpsm`. Targets: Ubuntu 22.04+, Fedora 38+, Debian 12+.
Windows: PyInstaller one-folder → WiX MSI. Code-signed. Installs to %LOCALAPPDATA%\Programs\cpsm.

### 9.9 Out of Scope (v1)

Mobile/web/remote control; built-in terminal emulator; recording/replay; team config sync; SSH password auth; auto-update; macOS as primary; cross-host synchronize-panes; WSL-tmux first-class; multi-way pane rotation.

## 10. Acceptance Criteria

1. First-run Welcome (Import/Empty/Open); import never modifies source.
2. Load/edit/validate/save .cpsm.yaml with comment preservation.
3. Re-import three-way merge.
4. All 5 launch_profiles end-to-end.
5. Profile-conditional schema rejection.
6. Profile-switch confirms before clearing forbidden fields.
7. All 3 backends pass shared contract tests including set_window_option remain-on-exit, split_pane(before=True).
8. Every dialog/widget has stable objectName + accessible names.
9. Sidebar profile icons; mixed-profile groups render/launch.
10. Screen map = OS-detected layout; updates on hot-plug.
11. Multi-group preview: color overlay, edit-target lock, conflict hatch, per-group save/revert.
12. Viewport drag updates geometry_pct; live mode repositions terminals when supported.
13. Every gesture in §6.7 produces correct tmux command.
14. Drop targeting per §6.6 (edges, center popup, empty-slot silent respawn, modifiers).
15. Split-and-lock invariant: every add captures layout, sets custom + custom_layout_string.
16. Removal preserves geometry by default; layout_preserve_on_remove=false reverts.
17. Drop on empty-slot uses respawn-pane, layout string byte-stable.
18. Same-window pane drop = swap-pane, content swap, geometry unchanged.
19. Drop into full window splits target only.
20. Connections appear under multiple groups in sidebar.
21. Same connection from two groups reuses cpsm-<connection_id> (shared); per-group opts out.
22. Key gen/deploy/keychain works on Linux + Windows.
23. Group launches report per-member status; scenes apply on_conflict; null-id panes spawn _placeholder.sh.
24. Dead panes detected within 2× poll interval; in-pane [r/s/q] for claude-*; GUI Reconnect for all profiles.
25. Auto-reconnect honors backoff/max-attempts; clean exits don't auto-reconnect by default.
26. dlg_launcher_templates exposes built-ins with Restore Default.
27. Local profiles never invoke ssh/scp/plink (integration test asserts).
28. AppImage and MSI smoke tests pass in CI.
29. Coverage targets met.
30. No private key/password/passphrase in YAML, logs, or process arguments.
