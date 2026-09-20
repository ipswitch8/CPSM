# -*- coding: utf-8 -*-
"""Remove ONLY the executing pin line from ssh_binary.py, leaving prose intact.

Used by prove_pin_removal.sh. This is the surgical attack a reviewer used to
defeat a whole-file text search: cpsm/platform/ssh_binary.py mentions
"IdentitiesOnly=yes" more than a dozen times in comments and docstrings as
design rationale, so deleting the one line that actually emits it left a
`"IdentitiesOnly=yes" in text` check green while every OpenSSH invocation
silently stopped being pinned.

An earlier version of the proof used `sed` to rewrite EVERY occurrence, which
also removed the prose — so it caught a full strip and missed this one. The
difference is the whole point.
"""

from __future__ import annotations

PATH = "cpsm/platform/ssh_binary.py"

TARGET = '                argv += ["-o", "IdentitiesOnly=yes"]\n'
REPLACEMENT = "                pass  # pin removed by the proof harness\n"


def main() -> int:
    text = open(PATH, encoding="utf-8").read()
    if TARGET not in text:
        print("target line not found; ssh_binary.py may have changed")
        return 2
    open(PATH, "w", encoding="utf-8").write(text.replace(TARGET, REPLACEMENT, 1))
    remaining = text.replace(TARGET, REPLACEMENT, 1).count("IdentitiesOnly=yes")
    print(f"  prose occurrences still present after the strip: {remaining}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
