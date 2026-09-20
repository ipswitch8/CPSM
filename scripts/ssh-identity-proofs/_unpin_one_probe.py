# -*- coding: utf-8 -*-
"""Remove the pin from ONE of main_window.py's two ssh probes.

Used by prove_pin_removal.sh. In its own file because the replacement text
contains triple-quoted strings, which corrupt the shell script when nested
inside a heredoc.

The point of leaving the sibling probe pinned is that a file-level
"IdentitiesOnly=yes" in text check still passes afterwards — so if the guard
catches this, it is genuinely checking per argv list, which is what let the
:4483 gap survive the original audit.
"""

from __future__ import annotations

PATH = "cpsm/ui/main_window.py"

# The agent-refusal probe. Its distinguishing neighbour is
# PreferredAuthentications=publickey, which the sibling probe does not have.
TARGET = (
    '            "-o", "PreferredAuthentications=publickey",\n'
    '            "-o", "IdentitiesOnly=yes",\n'
)
REPLACEMENT = '            "-o", "PreferredAuthentications=publickey",\n'


def main() -> int:
    text = open(PATH, encoding="utf-8").read()
    if TARGET not in text:
        print("target block not found; main_window.py may have changed")
        return 2
    open(PATH, "w", encoding="utf-8").write(text.replace(TARGET, REPLACEMENT, 1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
