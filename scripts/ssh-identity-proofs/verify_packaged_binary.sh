#!/bin/bash
# End-to-end check that a BUILT artifact emits the identity pin.
#
# Everything else in this repo tests the source tree. This runs the actual
# packaged binary — dist/cpsm/cpsm, or the AppImage if you pass its path — with
# a fake `ssh` early on PATH that records its argv, then asserts the pin is
# there. It is the only check that would catch "the code is right but the
# shipped artifact is stale", which is exactly the state the build was in
# before this script existed.
#
# Usage:
#   scripts/ssh-identity-proofs/verify_packaged_binary.sh [path-to-binary]
# Defaults to dist/cpsm/cpsm.
set -uo pipefail
cd "$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"

BIN="${1:-dist/cpsm/cpsm}"
[ -x "$BIN" ] || { echo "not executable: $BIN"; exit 2; }

WORK=$(mktemp -d)
BINDIR="$WORK/bin"
LOG="$WORK/calls.log"
mkdir -p "$BINDIR"
RC=0

# Fake ssh/scp that record argv and exit cleanly.
for name in ssh scp; do
    cat > "$BINDIR/$name" <<EOF
#!/bin/bash
printf '%s ' "$name" >> "$LOG"
printf '%s ' "\$@" >> "$LOG"
printf '\n' >> "$LOG"
exit 0
EOF
    chmod +x "$BINDIR/$name"
done

# A terminal stub: CPSM launches the generated script through a terminal
# emulator, so give it one that just runs the script directly.
cat > "$BINDIR/xterm" <<'EOF'
#!/bin/bash
# Consume xterm-style args and execute whatever command follows -e.
while [ $# -gt 0 ]; do
    case "$1" in
        -e) shift; exec "$@" ;;
        *)  shift ;;
    esac
done
exit 0
EOF
chmod +x "$BINDIR/xterm"

# Minimal config: one ssh-shell connection with an identity file.
KEY="$WORK/id_test"
ssh-keygen -t ed25519 -N "" -f "$KEY" -q
cat > "$WORK/.cpsm.yaml" <<EOF
settings:
  default_terminal: xterm
  default_ssh_options: "-o ConnectTimeout=10"
ssh_keys:
  - id: test-key
    name: test-key
    private_path: $KEY
    public_path: $KEY.pub
connections:
  - id: pinned-conn
    name: pinned-conn
    launch_profile: ssh-shell
    host: 192.0.2.44
    port: 22
    user: root
    identity_file_ref: test-key
    project_folder: /tmp
groups: []
scenes: []
EOF

echo "=== running the packaged binary: $BIN ==="
PATH="$BINDIR:$PATH" HOME="$WORK" timeout 60 "$BIN" launch pinned-conn \
    --config "$WORK/.cpsm.yaml" >"$WORK/out.txt" 2>&1
echo "  exit: $?  (a non-zero exit is fine — we only need the argv)"

echo
echo "=== argv the packaged binary handed to ssh ==="
if [ ! -s "$LOG" ]; then
    echo "  NOTHING RECORDED — the fake ssh never ran."
    echo "  Launcher output:"
    sed 's/^/    /' "$WORK/out.txt" | head -20
    RC=1
else
    sed 's/^/  /' "$LOG"
    echo
    if grep -q 'IdentitiesOnly=yes' "$LOG"; then
        echo "  ok    the pin IS present in the packaged artifact"
    else
        echo "  BAD   the pin is MISSING — the artifact is stale"
        RC=1
    fi
    if grep -q -- '-i ' "$LOG"; then
        echo "  ok    -i is present"
    else
        echo "  BAD   -i missing"
        RC=1
    fi
fi

rm -rf "$WORK"
echo
[ $RC -eq 0 ] && echo "PACKAGED ARTIFACT: PASS" || echo "PACKAGED ARTIFACT: FAIL"
exit $RC
