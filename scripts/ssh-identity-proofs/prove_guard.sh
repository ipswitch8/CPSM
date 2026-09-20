#!/bin/bash
# Fault-injection proof for tests/lint/test_ssh_identity_inventory.py.
#
# Re-proves the guard against the two blind spots the claim-check gate
# demonstrated (single-line argv literal, and a shell-string f-string call),
# plus the multi-line form the validator gate used, plus two NEGATIVE cases
# that must NOT be flagged.
#
# A guard that cannot be shown to fail is worth nothing; a guard that fires on
# everything is worse. Both directions are proved here.
set -uo pipefail
cd "$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"

T=tests/lint/test_ssh_identity_inventory.py::test_no_unaccounted_ssh_invocation_site
RUN="QT_QPA_PLATFORM=offscreen ./.venv/bin/python -m pytest -q -p no:cacheprovider --no-cov"
RC=0

check() { # name  expect(FAIL|PASS)  file
    local name="$1" expect="$2" f="$3"
    local out
    out=$(eval "$RUN $T" 2>&1 | tail -1)
    if [[ "$out" == *"1 passed"* ]]; then got=PASS
    elif [[ "$out" == *"1 failed"* ]]; then got=FAIL
    else got="?? ($out)"; fi
    if [ "$got" = "$expect" ]; then
        printf '  ok    %-34s guard=%s (expected %s)\n' "$name" "$got" "$expect"
    else
        printf '  BAD   %-34s guard=%s (expected %s)\n' "$name" "$got" "$expect"
        RC=1
    fi
    rm -f "$f"
}

echo "=== POSITIVE: a new site MUST be caught ==="

cat > cpsm/_probe_multiline.py <<'EOF'
argv = [
    "ssh",
    "-i", "/tmp/fake",
    "user@host",
]
EOF
check "multi-line argv literal" FAIL cpsm/_probe_multiline.py

cat > cpsm/_probe_singleline.py <<'EOF'
def build(key):
    argv = ["ssh", "-i", key, "user@host"]
    return argv
EOF
check "single-line argv literal" FAIL cpsm/_probe_singleline.py

cat > cpsm/_probe_fstring.py <<'EOF'
import subprocess
def build(key, host):
    subprocess.run(f"ssh -i {key} {host}", shell=True)
EOF
check "f-string shell invocation" FAIL cpsm/_probe_fstring.py

cat > cpsm/_probe_plainstr.py <<'EOF'
import subprocess
def build():
    subprocess.run("scp -i /tmp/k a b", shell=True)
EOF
check "plain-string shell invocation" FAIL cpsm/_probe_plainstr.py

echo
echo "=== NEGATIVE: these must NOT be flagged ==="

cat > cpsm/_probe_wordlist.py <<'EOF'
# A UI dropdown, not a command line -- mirrors dialogs/settings.py
_SSH_BINARY_OPTIONS = ["auto", "openssh", "plink"]
EOF
check "word list (no flag, not argv0)" PASS cpsm/_probe_wordlist.py

cat > cpsm/_probe_nametuple.py <<'EOF'
# Name-matching tuple, not an invocation -- mirrors discovery_service.py
if name in ("ssh", "scp"):
    pass
EOF
check "name-matching tuple" PASS cpsm/_probe_nametuple.py

echo
echo "=== tree restored ==="
git status --porcelain | grep -E '^\?\? cpsm/_probe' && { echo "  LEFTOVER PROBE FILES"; RC=1; } || echo "  clean: no probe files left"

echo
[ $RC -eq 0 ] && echo "GUARD PROOF: PASS" || echo "GUARD PROOF: FAIL"
exit $RC
