#!/bin/bash
# Find every installed CPSM and report which one your desktop menu launches.
#
# WHY THIS EXISTS
# install.sh has two modes: run as root it installs to /opt/cpsm and symlinks
# into /usr/local/bin; run as your user it installs under ~/.local and symlinks
# into ~/.local/bin.  Use both over time and you have TWO copies, and which one
# you get depends on PATH order (for the terminal) and on the .desktop Exec
# line (for the menu) -- which are not necessarily the same copy.
#
# Worse: install.sh registers the launcher by running `cpsm install-desktop`,
# which writes into the INVOKING user's XDG data dir.  Under sudo that is
# root's home, so a system-wide install never updates your own .desktop -- it
# keeps pointing wherever it pointed before, which is how a fresh install can
# leave Help -> About showing the old version.
#
# Usage:  scripts/diagnose_install.sh

set -uo pipefail

USER_APPS="$HOME/.local/share/applications"
USER_OPT="$HOME/.local/opt/cpsm/cpsm.AppImage"
ROOT_APPS="/root/.local/share/applications"

hr() { printf -- '-%.0s' $(seq 1 74); printf '\n'; }

ver() {  # ver <path> -- what version does that binary report?
    if [ -x "$1" ]; then
        timeout 60 "$1" --version 2>/dev/null | head -1 || echo "(no --version)"
    else
        echo "(not executable)"
    fi
}

echo "CPSM install diagnosis"
hr

echo "1. What 'cpsm' does your shell run?"
w=$(command -v cpsm 2>/dev/null)
if [ -n "$w" ]; then
    echo "   PATH resolves to : $w"
    [ -L "$w" ] && echo "   symlink ->       : $(readlink -f "$w")"
    echo "   reports          : $(ver "$w")"
else
    echo "   no 'cpsm' on PATH"
fi
echo

echo "2. Every cpsm on PATH (the first one wins):"
IFS=: read -ra dirs <<< "$PATH"
found=0
for d in "${dirs[@]}"; do
    [ -e "$d/cpsm" ] || continue
    found=1
    printf '   %-34s %s\n' "$d/cpsm" "$(ver "$d/cpsm")"
done
[ "$found" -eq 0 ] && echo "   none"
echo

echo "3. Known install locations:"
for p in /opt/cpsm/cpsm.AppImage "$USER_OPT"; do
    if [ -e "$p" ]; then
        printf '   %-46s %s\n' "$p" "$(ver "$p")"
        printf '   %-46s built %s\n' "" "$(date -r "$p" '+%Y-%m-%d %H:%M')"
    else
        printf '   %-46s (absent)\n' "$p"
    fi
done
echo

echo "4. Desktop entries -- THIS is what the menu launches:"
shown=0
for d in "$USER_APPS" /usr/local/share/applications /usr/share/applications "$ROOT_APPS"; do
    f="$d/cpsm.desktop"
    [ -r "$f" ] || continue
    shown=1
    echo "   $f"
    echo "      modified: $(date -r "$f" '+%Y-%m-%d %H:%M')"
    grep -E '^Exec=' "$f" | sed 's/^/      /'
    exe=$(grep -m1 '^Exec=' "$f" | sed 's/^Exec=//; s/ gui.*//; s/ %.*//')
    if [ -n "$exe" ] && [ -e "$exe" ]; then
        echo "      that binary reports: $(ver "$exe")"
    elif [ -n "$exe" ]; then
        echo "      that path does not exist: $exe"
    fi
done
[ "$shown" -eq 0 ] && echo "   no cpsm.desktop found in the usual places"
echo

hr
echo "How to read this:"
echo "  * Section 4 governs the GUI menu, i.e. Help -> About."
echo "  * Sections 1 and 2 govern the terminal."
echo "  * If an Exec points at a binary reporting an old version, that is the"
echo "    cause: the .desktop still points at the previous install."
