#!/bin/bash
# Identify which CPSM build a binary is, and whether it carries the SSH
# identity-pinning fixes.
#
# WHY THIS EXISTS
# `cpsm --version` printed "cpsm 0.1.0" for every build ever made, so an
# installed binary could not be told apart from one built months earlier.
# Builds from ead474f onward embed the commit (see packaging/cpsm.spec), but
# anything older cannot report it -- so this also probes for the fixes by
# BEHAVIOUR, which works on any build, stamped or not.
#
# Usage:
#   scripts/which_build.sh [path-to-cpsm-binary-or-AppImage]
# Defaults to dist/cpsm/cpsm. Pass the installed path to check what you run.

set -uo pipefail
BIN="${1:-$(git rev-parse --show-toplevel)/dist/cpsm/cpsm}"

if [ ! -x "$BIN" ]; then
    echo "not executable: $BIN"
    exit 2
fi

echo "binary: $BIN"
printf 'reported version: '
"$BIN" --version 2>&1 | head -1

W=$(mktemp -d)
trap 'rm -rf "$W"' EXIT

# A config whose key exists but carries no usable path. Two behaviours differ:
#   - validate flags "empty private_path"  -> has the ead474f schema fix
#   - launch refuses with IdentityKeyNotFound -> has the 2dc9a3d guard
cat > "$W/probe.yaml" <<YAML
settings: {}
ssh_keys:
  - id: blanked
    name: Blanked
    type: rsa
    private_path: ''
    public_path: ''
connections:
  - id: probe
    name: Probe
    launch_profile: ssh-shell
    host: 192.0.2.1
    port: 22
    user: root
    identity_file_ref: blanked
    project_folder: /tmp
groups: []
scenes: []
YAML

check() {  # check <label> <pattern> <command...>
    local label="$1" pat="$2"; shift 2
    printf '  %-46s ' "$label"
    # Capture first, THEN match.  These probes deliberately drive the binary
    # into failure paths (validate exits 3, launch exits 5), and under
    # `set -o pipefail` a `cmd | grep -q` pipeline reports the BINARY's
    # non-zero status, not grep's -- so a matching probe still read as "NO".
    local out
    out=$("$@" 2>&1)
    if printf '%s' "$out" | grep -q "$pat"; then echo "yes"; else echo "NO"; fi
}

echo "capabilities:"
check "validate flags an unusable key (ead474f)" "empty private_path" \
      "$BIN" validate --config "$W/probe.yaml"
check "launch refuses an unusable key (2dc9a3d)" "no usable private" \
      "$BIN" launch probe --config "$W/probe.yaml"

# Dangling reference -- the original reported bug (662b8b6).
sed 's/identity_file_ref: blanked/identity_file_ref: imported-default/' \
    "$W/probe.yaml" > "$W/dangling.yaml"
check "launch refuses a missing key (662b8b6)" "which has no usable\|not defined" \
      "$BIN" launch probe --config "$W/dangling.yaml"

echo
echo "A 'NO' on any line means that binary predates the corresponding fix."
