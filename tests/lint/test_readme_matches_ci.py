# -*- coding: utf-8 -*-
"""
tests/lint/test_readme_matches_ci.py — CI lint: the README's Contributing
checklist must not drift from what CI actually runs.

Why this exists
---------------
The README tells contributors which commands to run before opening a PR. That
is a promise: run these, and you have reproduced CI. When CI gains a step and
the README does not, the promise quietly becomes false, and the symptom is a
contributor staring at a red build they cannot reproduce locally with no clue
what is different.

That is not hypothetical here. A lint step was added to `ci.yml` in one change
and the README's "CI runs exactly these" list was not updated in the next,
which is precisely the mechanism-moved-description-didn't-follow drift this
test now prevents.

What this test enforces
-----------------------
Every `run:` command in the `lint-and-type` job of `.github/workflows/ci.yml`
must appear somewhere in the README's Contributing section. Housekeeping steps
that are not a contributor's business — checkout, Python setup, dependency
installation — are excluded by name.

Deliberately one-directional: the README may contain MORE than CI runs (it is
allowed to suggest extra local checks), but never less. It also does not
compare flags character-by-character, because `pytest -q` locally and
`pytest -q --no-cov` in CI are the same instruction to a human; it matches on
the command's distinctive core.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
README = REPO_ROOT / "README.md"
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"

# Steps a contributor does not need to be told to run.
_HOUSEKEEPING = (
    "actions/checkout",
    "actions/setup-python",
    "pip install",
    "python -m pip",
    "apt-get",
)


def _contributing_section() -> str:
    text = README.read_text(encoding="utf-8")
    start = text.find("## Contributing")
    assert start != -1, "README.md has no '## Contributing' section"
    end = text.find("\n## ", start + 1)
    return text[start : end if end != -1 else len(text)]


def _contributing_commands() -> str:
    """Only the fenced command blocks of the Contributing section.

    Matching against the whole section would let surrounding prose satisfy the
    check: a paragraph that merely *mentions* a tool name would keep this test
    green after the actual command was deleted from the checklist. That exact
    false negative was demonstrated during development, which is why this
    narrows to the fenced blocks a contributor would copy and run.
    """
    section = _contributing_section()
    blocks = re.findall(r"```[a-zA-Z]*\n(.*?)```", section, flags=re.DOTALL)
    assert blocks, "README.md's Contributing section has no fenced command block"
    return "\n".join(blocks)


def _lint_job_commands() -> list[str]:
    """Return the meaningful shell commands in ci.yml's lint-and-type job.

    Parsed as text rather than with a YAML loader so the test does not depend
    on PyYAML being installed, and so it keeps working if the workflow grows
    constructs a naive loader would choke on.
    """
    text = CI_WORKFLOW.read_text(encoding="utf-8")
    start = text.find("  lint-and-type:")
    assert start != -1, "ci.yml has no 'lint-and-type' job"
    # The next job starts at the next 2-space-indented key.
    match = re.search(r"\n  [a-z][a-z0-9-]*:\n", text[start + 1 :])
    end = start + 1 + match.start() if match else len(text)
    job = text[start:end]

    commands: list[str] = []
    in_run_block = False
    for raw in job.splitlines():
        line = raw.strip()

        if line.startswith("run:"):
            rest = line[len("run:") :].strip()
            in_run_block = rest in {"|", ">", "|-", ""}
            if not in_run_block and rest:
                commands.append(rest)
            continue

        # Any other YAML key at step level ends the current run: block.
        if re.match(r"^[a-z][a-z0-9_-]*:", line):
            in_run_block = False
            continue
        if line.startswith("-"):
            in_run_block = False
            continue

        if in_run_block and line and not line.startswith("#"):
            commands.append(line)

    return [c for c in commands if not any(h in c for h in _HOUSEKEEPING)]


def _significant_tokens(command: str) -> list[str]:
    """Tokens a human would recognise the command by, ignoring flags.

    Flags are dropped because `pytest -q` locally and `pytest -q --no-cov` in
    CI are the same instruction to a contributor; requiring flag-for-flag
    equality would make this test fail on cosmetic differences and train
    people to ignore it.
    """
    return [t for t in command.split() if not t.startswith("-")][:3]


def _core_of(command: str) -> str:
    return " ".join(_significant_tokens(command)) or command


def test_ci_lint_job_has_commands_to_check() -> None:
    """Guard against the parser silently finding nothing and passing vacuously."""
    commands = _lint_job_commands()
    assert len(commands) >= 3, (
        f"Only parsed {len(commands)} command(s) out of ci.yml's lint-and-type job: "
        f"{commands}. The parser has probably broken against a workflow change — "
        "fix it rather than letting this test pass on an empty list."
    )


@pytest.mark.parametrize("command", _lint_job_commands(), ids=_core_of)
def test_readme_contributing_mentions_every_ci_lint_step(command: str) -> None:
    """Every check CI enforces must be findable in the README's Contributing block."""
    commands = _contributing_commands()
    tokens = _significant_tokens(command)
    missing = [t for t in tokens if t not in commands]
    assert tokens and not missing, (
        f"CI's lint-and-type job runs {command!r}, but the README's Contributing "
        f"checklist never runs {missing!r}.\n\n"
        "A contributor following the README has therefore not reproduced CI, and "
        "will hit a red build they cannot reproduce locally. Add the step to the "
        "Contributing section (or remove it from CI)."
    )
