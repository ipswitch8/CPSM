#!/bin/bash
# Reproduce the AppImage memory leak, and prove its cause, from a clean checkout.
#
# WHAT THIS SHOWS
#
# CPSM's AppImage leaks ~400-460 MB/day. The same source run from a venv, and
# the same PyInstaller-frozen binary run directly (dist/cpsm/cpsm), leak
# nothing. The difference is appimage-builder's AppRun, which LD_PRELOADs
# libapprun_hooks.so to rewrite AppDir-relative paths for the application and
# its children.
#
# This script runs a controlled A/B against an extracted AppDir, differing ONLY
# in whether that one library is the real shim or an inert stub:
#
#     hooks INTACT     +1176 KB / 240s  ->  413.4 MB/day
#     hooks STUBBED       +0 KB / 240s  ->    0.0 MB/day
#
# See docs/MEMORY-LEAK-INVESTIGATION.md for the full elimination chain.
#
# WHY A WALL-CLOCK A/B RATHER THAN AN ITERATION COUNT
#
# The other measurements in tests/perf/ drive a specific call N thousand times
# and report bytes-per-iteration. That instrument does not apply here: the shim
# acts continuously on file operations underneath the application, not on any
# call path the test suite can invoke. Its rate is invariant to poll interval,
# wake-up rate and workload -- an EMPTY document still leaks 406 MB/day -- so
# there is no iteration to count. Elapsed wall-clock against a live process is
# the correct instrument, and the discriminating signal (413 MB/day vs exactly
# zero) is far larger than any measurement noise.
#
# WHY THIS IS NOT A PYTEST
#
# It needs a built AppImage, launches real GUI processes, and takes ~15 minutes.
# It is a reproduction script, not a unit test. tests/perf/ covers what can be
# measured in-process.
#
# REQUIREMENTS
#   - CPSM-<version>-x86_64.AppImage built at the repo root
#   - gcc (to build the inert stub)
#   - a venv at .venv with the project installed (used only to seed a config)
#
# USAGE
#   scripts/diagnose_appimage_leak.sh              # both arms, ~15 min
#   scripts/diagnose_appimage_leak.sh stub         # stubbed arm only
#   scripts/diagnose_appimage_leak.sh intact       # intact arm only
#
# NOTE: stubbing the hooks is a DIAGNOSTIC, not a fix. The shim exists to fix up
# LD_LIBRARY_PATH for child processes, and CPSM spawns terminals and tmux. An
# idle memory measurement cannot show whether launching still works. The
# recommended fix is to package with linuxdeploy or plain appimagetool, neither
# of which uses this machinery.

set -u

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

APPIMAGE=$(ls -1 CPSM-*-x86_64.AppImage 2>/dev/null | head -1)
[ -n "$APPIMAGE" ] || { echo "ERROR: no CPSM-*-x86_64.AppImage at $REPO"; exit 1; }
APPDIR="$REPO/squashfs-root"
HOOK="$APPDIR/lib/x86_64/libapprun_hooks.so"

SETTLE=${SETTLE:-120}     # seconds to let startup allocation finish
WINDOW=${WINDOW:-240}     # measurement window

extract_appdir() {
    [ -d "$APPDIR" ] && return 0
    echo "extracting $APPIMAGE ..."
    ./"$APPIMAGE" --appimage-extract >/dev/null 2>&1 || {
        echo "ERROR: --appimage-extract failed"; exit 1; }
    # Force offscreen: AppRun.env pins QT_QPA_PLATFORM=xcb unconditionally, and
    # the leak is present headless too (X11 is not involved). Running headless
    # keeps the measurement independent of any display server.
    sed -i 's/^QT_QPA_PLATFORM=xcb$/QT_QPA_PLATFORM=offscreen/' "$APPDIR/AppRun.env"
}

seed_config() {
    local out="$1"
    ./.venv/bin/python - "$out" <<'PY'
import sys
from pathlib import Path
from cpsm.data.repository import CpsmRepository
from cpsm.data.schema import (CpsmDocument, ClaudeLocalConnection, Group,
                              ScreenLayout, Monitor, Viewport, Pane, GeometryPct)
conns, panes = [], []
for i in range(6):
    cid = f"conn-diag-{i}"
    conns.append(ClaudeLocalConnection(
        id=cid, name=f"Diag Conn {i}", launch_profile="claude-local",
        project_folder=f"~/diag{i}", claude_options="--resume"))
    panes.append(Pane(connection_id=cid))
mons = []
for m in range(3):
    vp = Viewport(id=f"vp-diag-{m}", geometry_pct=GeometryPct(x=0, y=0, w=100, h=100),
                  tmux_layout="tiled", panes=panes[m * 2:(m * 2) + 2])
    mons.append(Monitor(monitor_index_hint=m, viewports=[vp]))
layout = ScreenLayout(id="layout-diag", name="Diag Layout", monitors=mons)
grp = Group(id="group-diag", name="Diag Group",
            members=[c.id for c in conns], default_layout_id=layout.id)
# A seeded config matters: with NO config the app enters its first-run wizard
# and never reaches the main window, so nothing is measured.
CpsmRepository().save(CpsmDocument(connections=conns, groups=[grp],
                                   screen_layouts=[layout]), Path(sys.argv[1]))
PY
}

anon_kb() { pmap -X "$1" 2>/dev/null | tail -1 | awk '{print $2+$13}'; }

run_arm() {
    local mode="$1" label="$2"
    [ -f "$HOOK" ] || { echo "ERROR: hook library missing at $HOOK"; return 1; }
    [ -f "$HOOK.real" ] || cp "$HOOK" "$HOOK.real"

    if [ "$mode" = "stub" ]; then
        printf 'void __appimage_stub(void){}\n' > "$REPO/.diag_stub.c"
        gcc -shared -fPIC -o "$HOOK" "$REPO/.diag_stub.c" 2>/dev/null || {
            echo "ERROR: could not build stub (is gcc installed?)"
            cp "$HOOK.real" "$HOOK"; rm -f "$REPO/.diag_stub.c"; return 1; }
        rm -f "$REPO/.diag_stub.c"
    else
        cp "$HOOK.real" "$HOOK"
    fi

    local tmp; tmp=$(mktemp -d /tmp/cpsm-diag-XXXXXX)
    mkdir -p "$tmp/cpsm"
    XDG_CONFIG_HOME="$tmp" seed_config "$tmp/cpsm/.cpsm.yaml"
    [ -s "$tmp/cpsm/.cpsm.yaml" ] || { echo "$label: SEED FAILED"; rm -rf "$tmp"; return 1; }

    XDG_CONFIG_HOME="$tmp" "$APPDIR/AppRun" gui >"$tmp/app.log" 2>&1 &
    local launcher=$!
    sleep 30
    local pid
    pid=$(ps -eo pid,cmd | grep "[c]psm gui" | grep -v AppRun | awk '{print $1}' | tail -1)
    [ -z "$pid" ] && pid=$launcher
    if ! kill -0 "$pid" 2>/dev/null; then
        echo "$label: APP DIED — measurement invalid"
        tail -15 "$tmp/app.log"; rm -rf "$tmp"; return 1
    fi

    sleep "$SETTLE"
    local a b t0 t1
    a=$(anon_kb "$pid"); t0=$(date +%s)
    sleep "$WINDOW"
    b=$(anon_kb "$pid"); t1=$(date +%s)

    kill "$pid" "$launcher" 2>/dev/null; sleep 2
    kill -9 "$pid" "$launcher" 2>/dev/null

    if [ -z "$b" ]; then
        echo "$label: process exited during the window — measurement invalid"
        rm -rf "$tmp"; return 1
    fi
    awk -v l="$label" -v a="$a" -v b="$b" -v e="$((t1-t0))" 'BEGIN{
        d = b - a
        printf "%-34s delta=%+7d KB / %ds -> %8.1f MB/day\n", l, d, e, d*86400.0/e/1024.0
    }'
    rm -rf "$tmp"
}

cleanup() {
    [ -f "$HOOK.real" ] && cp "$HOOK.real" "$HOOK" 2>/dev/null
    ps -eo pid,cmd 2>/dev/null | grep -E "[c]psm gui|[A]ppRun gui" \
        | awk '{print $1}' | while read -r p; do kill "$p" 2>/dev/null; done
}
trap cleanup EXIT

extract_appdir
WHICH="${1:-both}"
echo "AppImage : $APPIMAGE"
echo "settle   : ${SETTLE}s   window: ${WINDOW}s"
echo
case "$WHICH" in
    stub)   run_arm stub   "hooks STUBBED" ;;
    intact) run_arm intact "hooks INTACT"  ;;
    *)      run_arm intact "hooks INTACT"
            run_arm stub   "hooks STUBBED" ;;
esac
