#!/bin/bash
# Prove _pins_in_code's SHELL branch cannot be fooled by prose.
#
# Raised as a carry-forward by the phase-2 claim-check gate: while both
# templates sat in KNOWN_GAPS, nothing reached the shell branch, and it only
# stripped lines whose FIRST character was '#'. Measured then, a trailing '#'
# comment and a here-doc body both satisfied it. Phase-4 removes the templates
# from KNOWN_GAPS, so the branch is now load-bearing and must be re-attacked.
#
# Each attack deletes the REAL pin from the template and hides the string
# somewhere prose-shaped. All must FAIL the guard.
set -uo pipefail
cd "$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"

T=cpsm/resources/launcher_templates/ssh-shell.sh
GUARD=tests/lint/test_ssh_identity_inventory.py
RUN="QT_QPA_PLATFORM=offscreen ./.venv/bin/python -m pytest -q -p no:cacheprovider --no-cov"
BAK=$(mktemp)
cp "$T" "$BAK"
RC=0

report() {
    case "$2" in
        *failed*) printf '  ok    %-34s %s\n' "$1" "$2" ;;
        *)        printf '  BAD   %-34s %s\n' "$1" "$2"; RC=1 ;;
    esac
}

# Remove the real pin, then append $1 as the decoy.
attack() {
    cp "$BAK" "$T"
    ./.venv/bin/python - "$T" <<'PY'
import sys
p = sys.argv[1]
s = open(p, encoding="utf-8").read()
s = s.replace('        _ID_ARG="-o IdentitiesOnly=yes ${_ID_ARG}"\n',
              '        :  # pin removed by the proof harness\n', 1)
open(p, "w", encoding="utf-8").write(s)
PY
    printf '%s\n' "$2" >> "$T"
    report "$1" "$(eval "$RUN $GUARD" 2>&1 | tail -1)"
    cp "$BAK" "$T"
}

echo "=== baseline ==="
echo "  $(eval "$RUN $GUARD" 2>&1 | tail -1)"

echo
echo "=== ATTACK 1: pin removed, no decoy (control) ==="
attack "no decoy" ''

echo
echo "=== ATTACK 2: pin removed, string in a FULL-LINE comment ==="
attack "full-line comment" '# IdentitiesOnly=yes'

echo
echo "=== ATTACK 3: pin removed, string in a TRAILING comment ==="
attack "trailing comment" 'echo hi   # IdentitiesOnly=yes'

echo
echo "=== ATTACK 4: pin removed, string inside a here-doc body ==="
attack "here-doc body" 'cat > /dev/null <<'"'"'EOF'"'"'
IdentitiesOnly=yes
EOF'

echo
echo "=== restored ==="
if diff -q "$BAK" "$T" >/dev/null; then
    echo "  template restored byte-identical"
else
    echo "  BAD template not restored"; RC=1
fi
echo "  $(eval "$RUN $GUARD" 2>&1 | tail -1)"
rm -f "$BAK"

echo
[ $RC -eq 0 ] && echo "SHELL BRANCH PROOF: PASS" || echo "SHELL BRANCH PROOF: FAIL"
exit $RC
