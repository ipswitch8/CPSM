#!/usr/bin/env bash
# install.sh — CPSM installer for Linux.
#
# Usage:
#   ./install.sh <path-to-CPSM-x.y.z-x86_64.AppImage>
#
# Installs runtime dependencies (tmux, sshpass, openssh-client/clients,
# terminal emulator) via the detected package manager, places the AppImage
# under /opt/cpsm (system) or ~/.local/opt/cpsm (per-user), symlinks a
# `cpsm` command into PATH, and registers a freedesktop .desktop launcher.
#
# Re-running is safe; already-installed components are skipped.

set -euo pipefail

# ── Argument & environment validation ──────────────────────────────────────

# Options may appear before or after the AppImage path.
KEEP_OLD=0
APPIMAGE_PATH=""
WANT_HELP=0
MODE_CHOICE=""   # "system" | "user" | "" (ask, or infer from EUID)
for _arg in "$@"; do
    case "$_arg" in
        --keep-old-install) KEEP_OLD=1 ;;
        --system)           MODE_CHOICE="system" ;;
        --user)             MODE_CHOICE="user" ;;
        -h|--help)          WANT_HELP=1 ;;
        -*)                 echo "Error: unknown option '$_arg'" >&2; exit 2 ;;
        *)                  [[ -z "$APPIMAGE_PATH" ]] && APPIMAGE_PATH="$_arg" ;;
    esac
done

if [[ -z "$APPIMAGE_PATH" || "$WANT_HELP" -eq 1 ]]; then
    cat <<EOF
Usage: $0 <path-to-CPSM-x.y.z-x86_64.AppImage>

Installs CPSM and its runtime dependencies on this Linux system.

Behavior:
  - System-wide (root):  /opt/cpsm, symlinked into /usr/local/bin, with the
                         desktop entry in /usr/local/share/applications so
                         EVERY user gets a launcher.
  - Per-user (no root):  ~/.local/opt/cpsm, symlinked into ~/.local/bin, with
                         the desktop entry under ~/.local/share.  Every file
                         is owned by you; sudo is used ONLY to install missing
                         system packages, and only if any are missing.

Run without --system/--user and it asks which you want.

Supports: Debian/Ubuntu/Mint/Pop!_OS, Fedora/RHEL/Rocky/Alma, Arch/Manjaro,
openSUSE, Alpine.  Other distros will get a manual-install message listing
the required packages.

A system-wide install REMOVES an older per-user install (~/.local/opt/cpsm
and its ~/.local/bin/cpsm symlink) if one is present.  Leaving it in place
is what makes a successful install appear to do nothing: ~/.local/bin comes
before /usr/local/bin on a normal PATH, so the old copy keeps winning in
both the terminal and the desktop menu.  Only CPSM's own install layout is
touched; anything else of that name is left alone and reported.

Options:
  --system             Install for all users (re-runs itself with sudo).
  --user               Install for the current user only; never asks.
  --keep-old-install   Do not remove a shadowing per-user install.

Examples:
  ./install.sh CPSM-0.2.1-x86_64.AppImage             # per-user install
  sudo ./install.sh CPSM-0.2.1-x86_64.AppImage        # system-wide install
  sudo ./install.sh CPSM-0.2.1-x86_64.AppImage --keep-old-install
EOF
    exit 1
fi

if [[ ! -f "$APPIMAGE_PATH" ]]; then
    echo "Error: AppImage not found at '$APPIMAGE_PATH'" >&2
    exit 2
fi
APPIMAGE_PATH="$(readlink -f "$APPIMAGE_PATH")"

# Color output when stdout is a TTY.
if [[ -t 1 ]]; then
    RED=$'\e[31m'; YEL=$'\e[33m'; GRN=$'\e[32m'; BLU=$'\e[34m'; RST=$'\e[0m'
else
    RED=''; YEL=''; GRN=''; BLU=''; RST=''
fi
info()  { echo "${BLU}info${RST}   $*"; }
warn()  { echo "${YEL}warn${RST}   $*" >&2; }
error() { echo "${RED}error${RST}  $*" >&2; }
ok()    { echo "${GRN}ok${RST}     $*"; }

# ── Distro / package manager / GUI detection ───────────────────────────────

DISTRO_ID="unknown"
DISTRO_LIKE=""
DISTRO_NAME=""
PM=""
HAS_GUI=0

detect_distro() {
    if [[ ! -f /etc/os-release ]]; then
        error "/etc/os-release missing — cannot detect distro"
        exit 3
    fi
    # shellcheck disable=SC1091
    . /etc/os-release
    DISTRO_ID="${ID:-unknown}"
    DISTRO_LIKE="${ID_LIKE:-}"
    DISTRO_NAME="${PRETTY_NAME:-$DISTRO_ID}"
    info "Distro: $DISTRO_NAME (id=$DISTRO_ID)"
}

detect_pm() {
    for candidate in apt-get dnf yum pacman zypper apk; do
        if command -v "$candidate" >/dev/null 2>&1; then
            PM="$candidate"
            info "Package manager: $PM"
            return 0
        fi
    done
    error "No supported package manager found (apt-get/dnf/yum/pacman/zypper/apk)"
    error "Install tmux + sshpass + openssh-client + a terminal emulator manually"
    exit 4
}

detect_gui() {
    if [[ -n "${DISPLAY:-}" || -n "${WAYLAND_DISPLAY:-}" ]]; then
        HAS_GUI=1
    else
        HAS_GUI=0
        info "Headless session — terminal emulator + desktop entry will be skipped"
    fi
}

# ── Privilege model ────────────────────────────────────────────────────────

PKG_SUDO=""        # sudo, but ONLY for installing system packages
INSTALL_TYPE=""
INSTALL_DIR=""
BIN_DIR=""
DESKTOP_SCOPE=""   # "system" (all users) | "user"
SELF_PATH="$(readlink -f "$0")"
# $EUID is readonly, which makes the privilege branch untestable without
# actually being root.  Mirror it once into a normal variable so the decision
# logic can be exercised for both modes.
IS_ROOT=0
[[ "$EUID" -eq 0 ]] && IS_ROOT=1

decide_install_target() {
    # Two clean modes, and the privilege each actually needs:
    #
    #   system : we are already root.  Files go to /opt and /usr/local, and
    #            the desktop entry goes to /usr/local/share/applications so
    #            every user on the machine gets a launcher -- not just
    #            whoever happened to type sudo.
    #   user   : everything lands under $HOME and is OWNED BY THE USER.
    #
    # An earlier version used $SUDO for file placement in BOTH modes, so a
    # per-user install wrote root-owned files into the user's own home.  They
    # then could not be removed or overwritten without sudo, which is exactly
    # how a stale root-owned AppImage ended up stuck in ~/.local/opt/cpsm.
    # Placement now never uses sudo: in system mode we are already root, and
    # in user mode the target is the user's own home.  sudo survives for one
    # job only -- installing missing system packages -- under PKG_SUDO.
    if [[ "$IS_ROOT" -eq 1 ]]; then
        if [[ "$MODE_CHOICE" == "user" ]]; then
            error "--user was given but this is running as root"
            error "Re-run without sudo to install for the current user only"
            exit 5
        fi
        INSTALL_TYPE="system"
    else
        [[ -z "$MODE_CHOICE" ]] && MODE_CHOICE="$(ask_install_mode)"
        if [[ "$MODE_CHOICE" == "system" ]]; then
            command -v sudo >/dev/null 2>&1 || {
                error "A system-wide install needs root, and sudo is not available"
                error "Re-run as root, or choose the per-user install"
                exit 5
            }
            info "Re-running with sudo for a system-wide install ..."
            exec sudo -- "$SELF_PATH" "$APPIMAGE_PATH" --system \
                 $([[ "$KEEP_OLD" -eq 1 ]] && echo --keep-old-install)
        fi
        INSTALL_TYPE="user"
    fi

    if [[ "$INSTALL_TYPE" == "system" ]]; then
        PKG_SUDO=""                       # already root
        INSTALL_DIR="/opt/cpsm"
        BIN_DIR="/usr/local/bin"
        DESKTOP_SCOPE="system"
    else
        # sudo is for package installation ONLY, and only if something is
        # missing.  Its absence is not fatal: a per-user install of an
        # AppImage needs no privilege at all when the dependencies are
        # already present, which is the common case.
        if command -v sudo >/dev/null 2>&1; then PKG_SUDO="sudo"; else PKG_SUDO=""; fi
        INSTALL_DIR="$HOME/.local/opt/cpsm"
        BIN_DIR="$HOME/.local/bin"
        DESKTOP_SCOPE="user"
    fi
    info "Install mode: $INSTALL_TYPE  (binary: $INSTALL_DIR  symlink: $BIN_DIR)"
}

# Ask which install the user wants.  Only when we can actually ask: a
# non-interactive run (CI, a pipe) must not block forever waiting on a prompt,
# so it takes the choice that needs no privileges.
ask_install_mode() {
    if [[ ! -t 0 || ! -t 1 ]]; then
        info "Non-interactive; installing for the current user only." >&2
        info "Pass --system to install for all users." >&2
        echo "user"
        return 0
    fi

    local reply
    echo ""                                            >&2
    echo "Install CPSM for:"                            >&2
    echo "  [1] this user only  ($HOME/.local) — no sudo needed"  >&2
    echo "  [2] all users       (/opt, /usr/local) — needs sudo"  >&2
    echo ""                                            >&2
    while true; do
        read -r -p "Choice [1/2, default 1]: " reply </dev/tty || { echo "user"; return 0; }
        case "${reply:-1}" in
            1|u|user)   echo "user";   return 0 ;;
            2|a|all|system) echo "system"; return 0 ;;
            *) echo "Please answer 1 or 2." >&2 ;;
        esac
    done
}

# ── Package name mapping per distro ────────────────────────────────────────
# Empty values mean "this distro doesn't ship the package in default repos —
# skip and warn".  PKG_TERM is the terminal emulator we install when none is
# already present; xterm is the universal cheapest option.

PKG_KEYGEN=""
PKG_TMUX=""
PKG_SSHPASS=""
PKG_TERM=""
NEEDS_EPEL=0

map_packages() {
    case "$DISTRO_ID" in
        debian|ubuntu|linuxmint|raspbian|pop|elementary|kali)
            PKG_KEYGEN="openssh-client"
            PKG_TMUX="tmux"
            PKG_SSHPASS="sshpass"
            PKG_TERM="xterm"
            ;;
        fedora|rhel|centos|rocky|almalinux|ol)
            PKG_KEYGEN="openssh-clients"
            PKG_TMUX="tmux"
            PKG_SSHPASS="sshpass"
            PKG_TERM="xterm"
            NEEDS_EPEL=1
            ;;
        arch|manjaro|endeavouros|garuda|artix)
            PKG_KEYGEN="openssh"
            PKG_TMUX="tmux"
            PKG_SSHPASS=""  # AUR-only
            PKG_TERM="xterm"
            ;;
        opensuse*|suse|sles)
            PKG_KEYGEN="openssh-clients"
            PKG_TMUX="tmux"
            PKG_SSHPASS="sshpass"
            PKG_TERM="xterm"
            ;;
        alpine)
            PKG_KEYGEN="openssh-keygen"
            PKG_TMUX="tmux"
            PKG_SSHPASS="sshpass"
            PKG_TERM="xterm"
            ;;
        *)
            # Try ID_LIKE for derivatives we don't recognize directly.
            for like in $DISTRO_LIKE; do
                case "$like" in
                    debian|ubuntu)
                        warn "Unknown distro '$DISTRO_ID' — treating as Debian/Ubuntu"
                        DISTRO_ID="$like"; map_packages; return ;;
                    fedora|rhel)
                        warn "Unknown distro '$DISTRO_ID' — treating as Fedora/RHEL"
                        DISTRO_ID="$like"; map_packages; return ;;
                    arch)
                        warn "Unknown distro '$DISTRO_ID' — treating as Arch"
                        DISTRO_ID="$like"; map_packages; return ;;
                    suse)
                        warn "Unknown distro '$DISTRO_ID' — treating as openSUSE"
                        DISTRO_ID="opensuse"; map_packages; return ;;
                esac
            done
            error "Unsupported distro '$DISTRO_ID' — install these manually:"
            error "  - tmux"
            error "  - sshpass         (for password-based SSH key deploy)"
            error "  - openssh-client  (for ssh-keygen)"
            error "  - a terminal emulator (xterm, konsole, alacritty, …)"
            exit 6
            ;;
    esac
}

# ── Dependency check ───────────────────────────────────────────────────────

NEED_KEYGEN=0
NEED_TMUX=0
NEED_SSHPASS=0
NEED_TERM=0
FOUND_TERM=""

check_deps() {
    command -v ssh-keygen >/dev/null 2>&1 || NEED_KEYGEN=1
    command -v tmux       >/dev/null 2>&1 || NEED_TMUX=1
    command -v sshpass    >/dev/null 2>&1 || NEED_SSHPASS=1
    if [[ "$HAS_GUI" -eq 1 ]]; then
        for term in xterm konsole alacritty kitty wezterm gnome-terminal foot xfce4-terminal; do
            if command -v "$term" >/dev/null 2>&1; then
                FOUND_TERM="$term"; break
            fi
        done
        if [[ -z "$FOUND_TERM" ]]; then NEED_TERM=1; fi
    fi

    local present=()
    local missing=()
    [[ $NEED_KEYGEN  -eq 0 ]] && present+=("ssh-keygen") || missing+=("ssh-keygen")
    [[ $NEED_TMUX    -eq 0 ]] && present+=("tmux")       || missing+=("tmux")
    [[ $NEED_SSHPASS -eq 0 ]] && present+=("sshpass")    || missing+=("sshpass")
    if [[ "$HAS_GUI" -eq 1 ]]; then
        [[ -n "$FOUND_TERM" ]] && present+=("term=$FOUND_TERM") || missing+=("terminal-emulator")
    fi
    info "Already present: ${present[*]:-none}"
    if [[ ${#missing[@]} -gt 0 ]]; then
        info "Will install:    ${missing[*]}"
    fi
}

# ── Package installation ───────────────────────────────────────────────────

install_packages() {
    local to_install=()
    [[ $NEED_KEYGEN  -eq 1 && -n "$PKG_KEYGEN"  ]] && to_install+=("$PKG_KEYGEN")
    [[ $NEED_TMUX    -eq 1 && -n "$PKG_TMUX"    ]] && to_install+=("$PKG_TMUX")
    [[ $NEED_SSHPASS -eq 1 && -n "$PKG_SSHPASS" ]] && to_install+=("$PKG_SSHPASS")
    [[ $NEED_TERM    -eq 1 && -n "$PKG_TERM"    ]] && to_install+=("$PKG_TERM")

    if [[ ${#to_install[@]} -eq 0 ]]; then
        ok "All dependencies already installed"
        return 0
    fi

    info "Installing via $PM: ${to_install[*]}"

    case "$PM" in
        apt-get)
            $PKG_SUDO apt-get update -qq
            DEBIAN_FRONTEND=noninteractive $PKG_SUDO apt-get install -y "${to_install[@]}"
            ;;
        dnf|yum)
            if [[ "$NEEDS_EPEL" -eq 1 && $NEED_SSHPASS -eq 1 ]]; then
                if ! rpm -q epel-release >/dev/null 2>&1; then
                    info "Enabling EPEL repository (sshpass lives there on RHEL family)"
                    $PKG_SUDO "$PM" install -y epel-release \
                        || warn "EPEL enable failed — sshpass install may fail too"
                fi
            fi
            $PKG_SUDO "$PM" install -y "${to_install[@]}"
            ;;
        pacman)
            $PKG_SUDO pacman -Sy --noconfirm --needed "${to_install[@]}"
            ;;
        zypper)
            $PKG_SUDO zypper --non-interactive install "${to_install[@]}"
            ;;
        apk)
            $PKG_SUDO apk add "${to_install[@]}"
            ;;
        *)
            error "Internal error: unknown PM=$PM"
            exit 7
            ;;
    esac
    ok "Package install complete"
}

# ── AppImage placement ─────────────────────────────────────────────────────

place_appimage() {
    info "Installing AppImage to $INSTALL_DIR/cpsm.AppImage"
    # No sudo here, in either mode.  System mode is already root; user mode
    # writes into the user's own home and must leave everything owned by that
    # user.  Using sudo here is what produced root-owned files under $HOME.
    mkdir -p "$INSTALL_DIR" "$BIN_DIR"
    cp -f "$APPIMAGE_PATH" "$INSTALL_DIR/cpsm.AppImage"
    chmod 0755 "$INSTALL_DIR/cpsm.AppImage"
    ln -sf "$INSTALL_DIR/cpsm.AppImage" "$BIN_DIR/cpsm"
    ok "Installed: $BIN_DIR/cpsm → $INSTALL_DIR/cpsm.AppImage"
}

# ── Desktop integration ────────────────────────────────────────────────────

register_desktop() {
    if [[ "$HAS_GUI" -eq 0 ]]; then
        info "Skipping desktop entry (headless system)"
        return 0
    fi
    info "Registering desktop launcher via 'cpsm install-desktop'"
    # install-desktop writes into the INVOKING user's XDG data dir.  Under
    # sudo that is root's home, so a system-wide install used to leave the
    # real user's ~/.local/share/applications/cpsm.desktop untouched --
    # still pointing at whatever it pointed at before.  Someone who had
    # previously done a per-user install then saw their menu keep launching
    # the OLD AppImage after a successful system install, with no error
    # anywhere to explain it.  Observed in the field; that is this branch.
    #
    # --executable is passed explicitly for two independent reasons:
    #   * root's PATH is not the user's, so resolving `cpsm` from PATH under
    #     sudo can bake in the wrong copy (or none at all);
    #   * inside the running AppImage, install-desktop's own
    #     shutil.which("cpsm") returns the transient FUSE mount path
    #     (/tmp/.mount_cpsm*/usr/bin/cpsm) which vanishes the moment the
    #     AppImage exits, leaving a launcher that points at nothing.
    # Pinning it to the persistent symlink avoids both.
    local desktop_rc=0
    if [[ "$DESKTOP_SCOPE" == "system" ]]; then
        # /usr/local/share/applications — visible to EVERY user, which is what
        # "install for all users" has to mean.  Registering into the invoking
        # user's ~/.local/share instead gave a launcher to exactly one person,
        # and left every other user with nothing.
        info "Registering launcher system-wide (all users)"
        "$BIN_DIR/cpsm" install-desktop --system --force \
             --executable "$BIN_DIR/cpsm" || desktop_rc=$?
    else
        "$BIN_DIR/cpsm" install-desktop --force \
             --executable "$BIN_DIR/cpsm" || desktop_rc=$?
    fi

    if [[ "$desktop_rc" -eq 0 ]]; then
        ok "Desktop entry registered"
    else
        warn "cpsm install-desktop returned non-zero — launcher may not appear"
        warn "You can retry manually: $BIN_DIR/cpsm install-desktop --force"
    fi
}

# ── Shadowing check ────────────────────────────────────────────────────────

# Remove *path*, reporting rather than aborting on failure.
#
# Runs with whatever privileges the installer already has.  An earlier version
# dropped to the invoking user via `sudo -u`, which failed on exactly the case
# that matters: leftovers created by a previous ROOT install are owned by root
# even though they sit under the user's home.  De-escalating gave up the
# privilege needed to clean up the mess a root install had made, and bought no
# safety -- what makes this narrow is the path checks in the caller, not the
# uid it runs as.
#
# Always returns 0.  Under `set -e` a failed `rm && ok` previously took the
# whole script down, skipping register_desktop, so a cleanup that could not
# finish silently cost the user the launcher update they ran the installer for.
try_remove() {
    local path="$1"
    if rm -rf "$path" 2>/dev/null; then
        ok "Removed $path"
    elif [[ ! -e "$path" && ! -L "$path" ]]; then
        ok "Removed $path"
    else
        warn "Could not remove $path"
        warn "  it will keep shadowing this install"
        warn "  remove it manually with: sudo rm -rf $path"
    fi
    return 0
}

cleanup_shadowing_install() {
    # A system install does not disturb an older per-user one, and
    # ~/.local/bin precedes /usr/local/bin on a normal PATH -- so the stale
    # copy keeps winning in the terminal AND, because install-desktop
    # resolves Exec= from PATH, in the desktop menu too.  Everything reports
    # success while the user goes on testing the old binary.  Warning about
    # it only moved the work; this removes it.
    #
    # Deliberately narrow: it removes CPSM's own install layout and nothing
    # else.  The directory must be exactly <home>/.local/opt/cpsm and must
    # contain cpsm.AppImage; the symlink must resolve INTO that directory.
    # Anything that fails those checks is reported and left alone -- a file
    # merely named "cpsm" on someone's PATH is not ours to delete.
    local user_home other_dir other_link target_real target
    target="$BIN_DIR/cpsm"

    # Whose home to look in: under sudo, $HOME may still be root's.
    if [[ -n "${SUDO_USER:-}" && "$SUDO_USER" != "root" ]]; then
        user_home="$(getent passwd "$SUDO_USER" | cut -d: -f6)"
    else
        user_home="$HOME"
    fi
    [[ -n "$user_home" ]] || return 0

    other_dir="$user_home/.local/opt/cpsm"
    other_link="$user_home/.local/bin/cpsm"

    # Installing INTO that layout: it is not a shadow, it is the target.
    [[ "$other_link" == "$target" ]] && return 0

    # The launcher is checked even when the install itself is already gone.
    # A leftover per-user .desktop outranks the system-wide one and, once its
    # Exec= names a binary that no longer exists, hides the application
    # entirely -- so the file that must be cleaned up is exactly the one that
    # survives a half-finished cleanup.
    if [[ ! -e "$other_dir" && ! -L "$other_link" ]]; then
        remove_stale_user_desktop "$user_home" "$other_dir"
        return 0
    fi

    info "Found an older per-user install that would shadow this one:"
    if [[ -x "$other_dir/cpsm.AppImage" ]]; then
        info "  $other_dir/cpsm.AppImage — $("$other_dir/cpsm.AppImage" --version 2>/dev/null | head -1)"
    fi
    info "  this install — $("$target" --version 2>/dev/null | head -1)"

    if [[ "$KEEP_OLD" -eq 1 ]]; then
        warn "--keep-old-install given; leaving it in place"
        warn "Note: ~/.local/bin usually precedes $BIN_DIR on PATH, so the"
        warn "older copy may still be the one that runs."
        return 0
    fi

    # Only remove a symlink that actually points into our install directory.
    if [[ -L "$other_link" ]]; then
        target_real="$(readlink -f "$other_link" 2>/dev/null || true)"
        if [[ "$target_real" == "$other_dir"/* ]]; then
            try_remove "$other_link"
        else
            warn "Left $other_link alone — it does not point into $other_dir"
            warn "  it resolves to: ${target_real:-<broken link>}"
        fi
    elif [[ -e "$other_link" ]]; then
        warn "Left $other_link alone — not a symlink, so not ours to remove"
    fi

    # Only remove the directory if it looks like our install.
    if [[ -d "$other_dir" ]]; then
        if [[ -f "$other_dir/cpsm.AppImage" ]]; then
            try_remove "$other_dir"
        else
            warn "Left $other_dir alone — no cpsm.AppImage inside, not our layout"
        fi
    fi

    remove_stale_user_desktop "$user_home" "$other_dir"
}

# Drop the per-user .desktop that pointed at the install we just removed.
#
# XDG precedence is the whole problem: $XDG_DATA_HOME/applications outranks
# /usr/local/share/applications for the same desktop-file ID, so a stale
# per-user cpsm.desktop keeps winning over the system-wide one this installer
# just wrote.  Worse, its Exec= now names a binary that no longer exists, and a
# dock favourite whose target is missing is silently dropped -- the icon
# disappears instead of updating.
#
# Narrow, like the rest of the cleanup: the file is removed only if its Exec=
# actually references the per-user install directory being removed.  An entry
# the user has re-pointed somewhere else is theirs, and is reported instead.
remove_stale_user_desktop() {
    local user_home="$1" other_dir="$2"
    local entry="$user_home/.local/share/applications/cpsm.desktop"

    [[ -f "$entry" ]] || return 0

    local exec_line exec_bin
    exec_line="$(grep -m1 '^Exec=' "$entry" 2>/dev/null || true)"

    # Compare the BINARY the entry runs, not the whole line.  A substring test
    # over the entire Exec= deletes any launcher that merely mentions the old
    # path -- e.g. `Exec=/usr/bin/other --note=/home/u/.local/opt/cpsm` names a
    # different program entirely, and is not ours to remove.  Take the first
    # field after Exec= (the program), strip surrounding quotes, and require it
    # to BE the AppImage we are removing.
    exec_bin="${exec_line#Exec=}"
    exec_bin="${exec_bin%% *}"
    exec_bin="${exec_bin%\"}"; exec_bin="${exec_bin#\"}"
    exec_bin="${exec_bin%\'}"; exec_bin="${exec_bin#\'}"

    if [[ "$exec_bin" != "$other_dir/cpsm.AppImage" ]]; then
        warn "Left $entry alone — it does not launch $other_dir/cpsm.AppImage"
        warn "  $exec_line"
        warn "  NOTE: a per-user entry takes precedence over the system-wide one,"
        warn "  so this file decides what your menu and dock launch."
        return 0
    fi

    info "Removing the per-user launcher that pointed at the old install"
    info "  $exec_line"
    try_remove "$entry"

    # Let the desktop notice, so the system-wide entry takes over promptly.
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database "$user_home/.local/share/applications" 2>/dev/null || true
    fi
}

# ── Final report ───────────────────────────────────────────────────────────

report() {
    echo ""
    echo "════════════════════════════════════════════════════════════════════════"
    ok  "CPSM installation complete"
    echo ""
    echo "  Run from terminal:  ${BLU}cpsm gui${RST}"
    if [[ "$HAS_GUI" -eq 1 ]]; then
        echo "  From app menu:      ${BLU}CPSM${RST} (Development / System category)"
    fi
    echo ""
    if [[ $NEED_SSHPASS -eq 1 && -z "$PKG_SSHPASS" ]]; then
        warn "sshpass was not installed automatically on this distro."
        case "$DISTRO_ID" in
            arch|manjaro|endeavouros|garuda|artix)
                warn "Arch ships sshpass via the AUR.  Install with an AUR helper, e.g.:"
                warn "    yay -S sshpass        # or paru -S sshpass"
                ;;
            *)
                warn "Install sshpass manually if you need password-based key deployment."
                ;;
        esac
    fi
    if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
        warn "$BIN_DIR is not in your \$PATH"
        warn "Add it to your shell profile, e.g.:"
        warn "    echo 'export PATH=\"$BIN_DIR:\$PATH\"' >> ~/.bashrc"
    fi
    echo "════════════════════════════════════════════════════════════════════════"
}

# ── Main ───────────────────────────────────────────────────────────────────

main() {
    detect_distro
    detect_pm
    detect_gui
    decide_install_target
    map_packages
    check_deps
    install_packages
    place_appimage
    # Advisory: a cleanup problem must never abort the install itself.
    cleanup_shadowing_install || true
    register_desktop
    report
}

main "$@"
