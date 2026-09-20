#!/bin/bash
# Prove the inventory guard catches a real site LOSING its pin.
#
# prove_guard.sh proves the guard notices a NEW, unaccounted site. This proves
# the other direction: that an existing must_pin site which stops pinning is
# caught, by both of the two paths that can catch it.
#
#   A. cpsm/platform/ssh_binary.py assembles argv incrementally, so it has no
#      argv literal to inspect and is covered by the FILE-LEVEL assertion.
#   B. cpsm/ui/main_window.py builds two separate argv literals, so it
#      exercises the PER-ARGV-LIST path. Removing the pin from only ONE of its
#      two probes must still be caught — a file-level check would not notice,
#      because the other probe still contains the string. That is exactly how
#      the :4483 gap survived the original hand audit.
set -uo pipefail
cd "$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"

GUARD=tests/lint/test_ssh_identity_inventory.py
RUN="QT_QPA_PLATFORM=offscreen ./.venv/bin/python -m pytest -q -p no:cacheprovider --no-cov"
RC=0

report() {  # $1 = label, $2 = pytest tail line
    case "$2" in
        *failed*) printf '  ok    %-32s %s\n' "$1" "$2" ;;
        *)        printf '  BAD   %-32s %s\n' "$1" "$2"; RC=1 ;;
    esac
}

echo "=== baseline ==="
echo "  $(eval "$RUN $GUARD" 2>&1 | tail -1)"

echo
echo "=== A1: ssh_binary.py fully stripped (blunt) ==="
cp cpsm/platform/ssh_binary.py /tmp/sshbin.bak
sed -i 's/IdentitiesOnly=yes/IdentitiesOnlyGONE=yes/g' cpsm/platform/ssh_binary.py
report "all occurrences removed" "$(eval "$RUN $GUARD" 2>&1 | tail -1)"
cp /tmp/sshbin.bak cpsm/platform/ssh_binary.py

echo
echo "=== A2: ONLY the executing line removed, prose left intact (surgical) ==="
# This is the attack that defeated a whole-file text search: the file's own
# design comments quote the option a dozen times, so the text stayed present
# while every OpenSSH invocation silently stopped being pinned.
./.venv/bin/python scripts/ssh-identity-proofs/_unpin_ssh_binary_code_only.py
report "code line only" "$(eval "$RUN $GUARD" 2>&1 | tail -1)"
cp /tmp/sshbin.bak cpsm/platform/ssh_binary.py
rm -f /tmp/sshbin.bak

echo
echo "=== B: one of main_window's two probes loses the pin (per-argv-list) ==="
cp cpsm/ui/main_window.py /tmp/mainwin.bak
./.venv/bin/python scripts/ssh-identity-proofs/_unpin_one_probe.py
echo "  pins remaining in main_window.py: $(grep -c 'IdentitiesOnly=yes' cpsm/ui/main_window.py)"
report "one probe unpinned" "$(eval "$RUN $GUARD" 2>&1 | tail -1)"
cp /tmp/mainwin.bak cpsm/ui/main_window.py
rm -f /tmp/mainwin.bak

echo
echo "=== restored ==="
echo "  $(eval "$RUN $GUARD" 2>&1 | tail -1)"
if git diff --quiet cpsm/platform/ssh_binary.py 2>/dev/null; then
    echo "  NOTE ssh_binary.py has no uncommitted diff (unexpected during phase-2)"
fi
echo "  files restored: $(git diff --stat cpsm/platform/ssh_binary.py cpsm/ui/main_window.py | tail -1)"

echo
[ $RC -eq 0 ] && echo "PIN-REMOVAL PROOF: PASS" || echo "PIN-REMOVAL PROOF: FAIL"
exit $RC
