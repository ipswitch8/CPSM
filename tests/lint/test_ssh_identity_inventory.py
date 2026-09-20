# -*- coding: utf-8 -*-
"""
tests/lint/test_ssh_identity_inventory.py — CI lint: every SSH invocation that
selects an explicit identity must also constrain OpenSSH to that identity.

Why this exists
---------------
When ``ssh`` is given ``-i <key>`` it treats that key as an *addition* to the
identities it already intends to offer: every key in the agent, plus the
default ``~/.ssh/id_*`` files.  It walks that list in its own order.  Two
consequences, both of which CPSM has a stake in:

1.  **The configured key is not necessarily the key used.**  A connection
    pinned to a specific ``identity_file_ref`` can silently authenticate with
    an unrelated agent key.  Any server-side authorisation keyed to the
    intended key is then bypassed, and the operator has no signal.

2.  **``Too many authentication failures``.**  sshd's default
    ``MaxAuthTries`` is 6.  An agent holding more keys than that gets the
    connection torn down before the correct key is ever offered.

``-o IdentitiesOnly=yes`` restricts ssh to the identities named on the command
line (or in the config), which is what a user selecting a key in CPSM means.
It remains compatible with ssh-agent: the agent is still used to *sign*, it is
just no longer allowed to volunteer keys that were not asked for.

What this test enforces
-----------------------
It is an inventory guard, not a behavioural test — the behaviour is covered by
tests/platform/test_ssh_binary.py and tests/services/test_template_service.py.
Its job is to fail when a NEW ssh invocation site appears that nobody has
thought about.  Adding a site is fine; adding one without deciding how it
handles identity selection is not.

Every entry in :data:`EXPECTED_SITES` is a deliberate decision with a reason.
If this test fails, the fix is to make the decision and record it here — never
to delete the entry.
"""

from __future__ import annotations

import ast
import ctypes
import functools
import gc
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CPSM_DIR = REPO_ROOT / "cpsm"

# Binaries whose command lines are subject to OpenSSH identity selection.
SSH_FAMILY = ("ssh", "scp", "sftp", "ssh-copy-id", "plink", "pscp")


# ---------------------------------------------------------------------------
# The inventory
# ---------------------------------------------------------------------------
# Keyed by "<repo-relative path>". Each value describes how that file selects
# an SSH identity and why that is correct. ``must_pin`` records the DECISION for that
# site: whether it is required to pin the identity with IdentitiesOnly=yes.
#
#   must_pin=True   must pass -i AND IdentitiesOnly=yes
#   must_pin=False  must deliberately NOT pin — the reason is mandatory
#
# A site recorded as must_pin=True must EITHER actually pin, or carry an entry
# in KNOWN_GAPS below declaring the debt and naming the phase that clears it.
# There is no third state, and no site can sit silently unpinned — see
# test_must_pin_sites_are_pinned and test_known_gap_is_still_open.
#
EXPECTED_SITES: dict[str, dict[str, object]] = {
    "cpsm/platform/ssh_binary.py": {
        "must_pin": True,
        "reason": (
            "Central argv builder. Must add IdentitiesOnly=yes immediately "
            "after -i, and ONLY when an identity file is actually supplied — "
            "adding it with no -i would restrict ssh to the default identity "
            "files and disable agent-held keys. Its callers (ssh_worker, "
            "correlation_service, remote_control_service) pass no -i of their "
            "own, so they inherit whatever this builder does."
        ),
    },
    "cpsm/resources/launcher_templates/ssh-shell.sh": {
        "must_pin": True,
        "reason": (
            "Interactive shell launcher. _ID_ARG carries the pin, and only "
            "when _IDENTITY_FILE is non-empty, so a connection with no key "
            "configured keeps ssh's default identity resolution. The pin must "
            "NOT override a user who set IdentitiesOnly in "
            "settings.default_ssh_options: ${_ID_ARG} is expanded before "
            "${_SSH_OPTIONS} and OpenSSH takes the FIRST value for a "
            "parameter."
        ),
    },
    "cpsm/resources/launcher_templates/claude-remote.sh": {
        "must_pin": True,
        "reason": (
            "Remote claude launcher. Same _ID_ARG construction and the same "
            "user-override rule, but applied across ALL of its invocations "
            "(7 ssh plus 1 scp) rather than only the first — a fix applied to "
            "one call site would look correct and leave the rest unpinned. "
            "Asserted behaviourally against the argv a rendered launcher "
            "actually hands to ssh, not just against the template text."
        ),
    },
    "cpsm/ui/main_window.py": {
        "must_pin": True,
        "reason": (
            "Two single-key probes, both of which must pin. "
            "_find_working_system_key() tests candidate keys one at a time; "
            "without the pin every candidate appears to work as soon as ANY "
            "agent key authenticates, so the probe reports the wrong key. It "
            "already pins. The agent-refusal probe has the identical "
            "requirement — it must fail when THIS key cannot sign, rather "
            "than succeed on a neighbour's key — and did NOT pin. That gap "
            "was found by this module's inventory sweep, not by the hand "
            "audit that preceded it, and is now fixed. Both probes pin, and "
            "test_each_pinned_argv_list_pins_its_own_identity asserts it per "
            "argv list rather than per file, because a per-file check passed "
            "the whole time the gap was open."
        ),
    },
    "cpsm/services/key_service.py": {
        "must_pin": False,
        "reason": (
            "Key DEPLOYMENT — the bootstrap path, and the one place where "
            "pinning would be actively wrong. The key being installed is by "
            "definition not yet in the target's authorized_keys, so the "
            "connection must authenticate by some other means: an existing "
            "agent key, a password, or a pre-existing default key. "
            "IdentitiesOnly=yes narrows exactly that set and would break "
            "agent-only setups. "
            "Note also that ssh-copy-id's -i is NOT an authentication "
            "selector: /usr/bin/ssh-copy-id passes it to use_id_file(), which "
            "sets PUB_ID_FILE (the key to INSTALL). Its own ssh call carries "
            "no -i at all, and it sources keys from the agent on purpose "
            '(GET_ID="ssh-add -L"). The password path already forces '
            "PubkeyAuthentication=no / IdentityAgent=none, which is the "
            "stronger constraint where one is wanted."
        ),
    },
}

# Files that merely mention an ssh-family name without invoking one:
# argument-parsing tables, UI labels, schema defaults, log strings.
NON_INVOCATION_ALLOWLIST = {
    "cpsm/services/discovery_service.py",  # parses OTHER processes' ssh argv
    "cpsm/platform/child_env.py",  # docstring cites konsole/ssh breakage
    "cpsm/platform/desktop_entry.py",
    "cpsm/platform/base.py",
    "cpsm/platform/tmux_backend.py",
    "cpsm/cli.py",
    "cpsm/data/importer.py",
    "cpsm/data/schema.py",
    "cpsm/services/correlation_service.py",
    "cpsm/services/remote_control_service.py",
    "cpsm/services/session_service.py",
    "cpsm/services/template_service.py",
    "cpsm/workers/ssh_worker.py",
    "cpsm/workers/correlation_worker.py",
    "cpsm/workers/key_deploy_worker.py",
    "cpsm/workers/status_poller.py",
}

# A shell invocation of an ssh-family binary at the head of a command.
# Used for .sh files, and for command STRINGS embedded in Python.
_SH_INVOCATION = re.compile(
    r"(?:^|[|&;(]|\bexec\b|\bif\b|!\s*)\s*"
    r"(?P<name>" + "|".join(SSH_FAMILY) + r")\s+[-\"'$]"
)


@functools.lru_cache(maxsize=1)
def _invocation_files_cached() -> frozenset[str]:
    """Cached result of the tree sweep.

    Cached deliberately, not just for speed.  Every test in this module used to
    re-read and re-parse every .py file under cpsm/, and that allocation churn
    is not free: it perturbs the glibc malloc arena enough to break
    tests/perf/test_fitinview_isolation.py, whose tight-loop measurements
    assume they are the ones allocating.  Running this module before that one
    turned two of its assertions from pass to fail.  Parsing once removes the
    interference and is the right thing to do regardless.
    """
    result = frozenset(_find_invocation_files())
    _release_arena()
    return result


def _release_arena() -> None:
    """Return this module's freed memory to the OS before other tests run.

    Sweeping the tree means reading every .py file under cpsm/ and building an
    AST for each.  Those object graphs are large and short-lived, and glibc
    keeps the freed space in its arena rather than returning it.  That is
    normally harmless, but tests/perf/test_fitinview_isolation.py measures
    NATIVE HEAP GROWTH in a tight loop and asserts the loop grows: handed a
    warm arena, the loop satisfies its allocations from already-free space,
    reports PLATEAU, and its assertions fail.

    Measured: running this module immediately before that one flipped
    test_fit_in_view_as_shipped_leaks_at_same_rate from pass to fail.  Neither
    a generic Python allocation churn test nor tests/lint/test_object_names.py
    (which builds a whole Qt MainWindow) reproduces it, so the interference is
    specific to this sweep, and cleaning up after itself is this module's
    responsibility rather than a reason to loosen a measurement elsewhere.
    """
    gc.collect()
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except (OSError, AttributeError):  # pragma: no cover - non-glibc platforms
        pass


def _iter_source_files():
    for path in sorted(CPSM_DIR.rglob("*")):
        if path.suffix not in (".py", ".sh"):
            continue
        if "__pycache__" in path.parts:
            continue
        yield path


def _python_invocation_lines(path: Path) -> list[int]:
    """Return line numbers in *path* that invoke an ssh-family binary.

    Parsed with :mod:`ast` rather than scanned line-by-line.  A line-oriented
    regex was the first implementation and it had two demonstrated blind
    spots, both found by fault injection during review:

    * a single-line argv literal, ``argv = ["ssh", "-i", key, "user@host"]``,
      because the old pattern required a line consisting solely of ``"ssh",``;
    * a shell command string, ``subprocess.run(f"ssh -i {key} {host}",
      shell=True)``, because there was no string detector for ``.py`` files
      at all.

    Both introduced a real, invisible SSH call site while the guard stayed
    green.  The AST sees an argv list as one node regardless of how it is
    wrapped, which removes the layout sensitivity entirely.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:  # pragma: no cover - a broken file is not our problem
        return []

    hits: list[int] = []
    for node in ast.walk(tree):
        # Case 1: an argv list/tuple literal naming an ssh-family binary.
        #
        # Two conditions, both needed to tell an argv list from a plain list
        # of words.  The binary must be the FIRST string constant (argv[0]),
        # and some element must look like a flag.  Without the first
        # condition, cpsm/ui/dialogs/settings.py's
        # ``["auto", "openssh", "plink"]`` -- the ssh_binary dropdown choices
        # -- is reported as an invocation site.  Without the second,
        # discovery_service's ``("ssh", "scp")`` name-matching tuple is.
        # Leading ``*sshpass_prefix`` entries are skipped naturally, since
        # only Constant elements are considered.
        if isinstance(node, (ast.List, ast.Tuple)):
            strings = [
                el.value
                for el in node.elts
                if isinstance(el, ast.Constant) and isinstance(el.value, str)
            ]
            has_flag = any(s.startswith("-") for s in strings)
            if strings and strings[0] in SSH_FAMILY and has_flag:
                hits.append(node.lineno)
        # Case 2: a plain string that reads as a shell command line.
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if _SH_INVOCATION.search(node.value):
                hits.append(node.lineno)
        # Case 3: an f-string.  Substituted expressions are replaced by a
        # placeholder so that "ssh -i {key}" still matches on its literal
        # prefix.
        elif isinstance(node, ast.JoinedStr):
            text = "".join(
                v.value if isinstance(v, ast.Constant) and isinstance(v.value, str) else "\x00"
                for v in node.values
            )
            if _SH_INVOCATION.search(text):
                hits.append(node.lineno)
    return hits


def _find_invocation_files() -> set[str]:
    """Return repo-relative paths that actually invoke an ssh-family binary."""
    hits: set[str] = set()
    for path in _iter_source_files():
        rel = path.relative_to(REPO_ROOT).as_posix()
        if path.suffix == ".py":
            if _python_invocation_lines(path):
                hits.add(rel)
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            if raw.strip().startswith("#"):
                continue
            if _SH_INVOCATION.search(raw):
                hits.add(rel)
                break
    return hits


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_no_unaccounted_ssh_invocation_site() -> None:
    """Every file invoking ssh/scp/plink must appear in the inventory.

    A failure here means a new SSH call site was added without deciding how it
    selects an identity.  Make the decision, then add it to EXPECTED_SITES
    with a reason.
    """
    found = set(_invocation_files_cached())
    unaccounted = found - set(EXPECTED_SITES) - NON_INVOCATION_ALLOWLIST
    assert not unaccounted, (
        "New SSH invocation site(s) with no identity-selection decision:\n  "
        + "\n  ".join(sorted(unaccounted))
        + "\n\nAdd each to EXPECTED_SITES in this file, recording whether it "
        "pins the identity with IdentitiesOnly=yes and why."
    )


def test_inventory_has_no_stale_entries() -> None:
    """Every inventory entry must still name a file that exists.

    Guards against the inventory rotting into a list of decisions about code
    that was deleted years ago.
    """
    missing = [rel for rel in EXPECTED_SITES if not (REPO_ROOT / rel).is_file()]
    assert not missing, "EXPECTED_SITES names files that no longer exist: " + ", ".join(missing)


def test_every_inventory_entry_states_a_reason() -> None:
    """A decision with no recorded reason is not a decision."""
    for rel, entry in EXPECTED_SITES.items():
        reason = str(entry.get("reason", "")).strip()
        assert len(reason) > 40, f"{rel}: inventory entry needs a real reason"


@pytest.mark.parametrize(
    "rel",
    sorted(rel for rel, e in EXPECTED_SITES.items() if not e["must_pin"]),
)
def test_unconstrained_sites_stay_unconstrained(rel: str) -> None:
    """Files declared 'not constrained' must not silently acquire the pin.

    key_service is the deployment/bootstrap path.  Adding IdentitiesOnly=yes
    there would break deploying a key to a host that CPSM can currently only
    reach via an agent key or a password.  If this fails, read the reason in
    EXPECTED_SITES before "fixing" it.
    """
    text = (REPO_ROOT / rel).read_text(encoding="utf-8")
    code = "\n".join(line for line in text.splitlines() if not line.strip().startswith("#"))
    assert "IdentitiesOnly=yes" not in code, (
        f"{rel} acquired IdentitiesOnly=yes, but the inventory records it as "
        "deliberately unconstrained:\n\n" + str(EXPECTED_SITES[rel]["reason"])
    )


# ---------------------------------------------------------------------------
# Known gaps — sites that MUST pin but do not yet
# ---------------------------------------------------------------------------
# A decision recorded only in a planning document is not a control: the plan
# can be edited, abandoned or lost, and nothing goes red to say so.  This
# registry is the git-tracked mechanism instead.
#
# Each entry pins the CURRENT, WRONG state on purpose.  The suite stays green
# today, and the moment the site is fixed ``test_known_gap_is_still_open``
# FAILS — which forces whoever fixed it to delete the entry, at which point
# ``test_must_pin_sites_are_pinned_or_declared`` starts requiring the real
# assertion.  Neither direction can pass silently.
#
# Adding an entry here is not a way to dodge the requirement: an entry is a
# declared, visible debt with a named owner phase.
# Sites that assemble argv incrementally (``argv += [...]``) rather than as one
# literal, so there is no single list node for the per-argv-list assertion to
# inspect. Their behaviour is pinned behaviourally instead — by building real
# argv values and asserting on them — which is stronger than inspecting source.
# Listed explicitly rather than inferred, so that a site cannot drop out of the
# per-argv-list check by accident.
KNOWN_GAPS: dict[str, str] = {
    # Empty: every must_pin site now pins. Entries here declare a site that
    # MUST pin but does not yet, naming the phase that clears it. The registry
    # is kept (rather than deleted with its last entry) because it is the
    # mechanism that stops a future gap being carried as an intention instead
    # of as something the suite enforces -- test_known_gap_is_still_open fails
    # the moment a declared gap is fixed, forcing the entry out and handing the
    # site to test_must_pin_sites_are_pinned.
}


def test_known_gap_is_still_open() -> None:
    """A declared gap that has been FIXED must be un-declared.

    This is expected to fail exactly once per site: at the moment the fix
    lands. That failure is the point. It converts "we intend to fix this" from
    a promise in a planning document into something the suite enforces, and it
    survives that document being lost, since this file is tracked in git.

    When it fails: delete the site's KNOWN_GAPS entry. Doing so hands the site
    to test_must_pin_sites_are_pinned, so the debt cannot simply be dropped.

    Written as a loop rather than a parametrisation so that an EMPTY registry —
    the good state, where every site pins — still runs a real test instead of
    emitting a skip marker for an empty parameter set.
    """
    fixed = [rel for rel in KNOWN_GAPS if _pins_in_code(REPO_ROOT / rel)]
    assert not fixed, (
        "These sites now pin, which is good — remove their KNOWN_GAPS entries "
        f"in {__file__} so the real assertions take over:\n  "
        + "\n  ".join(f"{rel}  (was: {KNOWN_GAPS[rel]})" for rel in fixed)
    )


def test_known_gaps_are_all_declared_must_pin() -> None:
    """A gap may only be declared for a site the inventory says must pin."""
    orphans = [
        rel
        for rel in KNOWN_GAPS
        if rel not in EXPECTED_SITES or not EXPECTED_SITES[rel]["must_pin"]
    ]
    assert not orphans, (
        "KNOWN_GAPS names sites that are not recorded as must_pin=True: " + ", ".join(orphans)
    )


@pytest.mark.parametrize(
    "rel",
    sorted(rel for rel, e in EXPECTED_SITES.items() if e["must_pin"] and rel not in KNOWN_GAPS),
)
def test_must_pin_sites_are_pinned(rel: str) -> None:
    """Every must_pin site either pins, or is a declared KNOWN_GAP.

    This is the load-bearing test.  Without it, a site recorded as "must pin"
    could sit unpinned indefinitely with nothing to say so — which is exactly
    the state cpsm/ui/main_window.py:4483 was in before the inventory sweep
    found it.  There is now no third option: pin it, or declare the debt.

    Sites with a declared KNOWN_GAP are excluded from the parametrisation
    rather than skipped inside the test body, so no skip markers appear in the
    run.  They are not unwatched: test_known_gap_is_still_open asserts against
    each of them and fires the moment one is fixed.
    """
    assert _pins_in_code(REPO_ROOT / rel), (
        f"{rel} is recorded as must_pin=True but does not pin IN CODE (a "
        "comment or docstring quoting the option does not count), and is not "
        "declared in KNOWN_GAPS. Either add the pin or declare the debt."
    )


def _pins_in_code(path: Path) -> bool:
    """True if *path* pins the identity in CODE, ignoring comments and docs.

    A plain ``"IdentitiesOnly=yes" in text`` check is not enough, and this is
    not hypothetical: these files carry long design-rationale comments that
    quote the option, so deleting the line that actually emits it left a
    text-level check green. A review caught exactly that.

    For Python the AST is used, which excludes comments entirely (they are not
    nodes) and excludes docstrings (a string that is the whole value of an
    ``ast.Expr`` statement). For shell templates, ``#`` comment lines are
    stripped the same way test_unconstrained_sites_stay_unconstrained does.
    """
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".py":
        tree = ast.parse(text)
        docstrings = {
            id(node.value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
        }
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in docstrings
                and "IdentitiesOnly=yes" in node.value
            ):
                return True
        return False
    return "IdentitiesOnly=yes" in _shell_code_only(text)


def _shell_code_only(text: str) -> str:
    """Return *text* with shell comments and here-doc bodies removed.

    Stripping only lines whose first character is ``#`` is not enough, and the
    gap was found by review before it could bite: a trailing comment on a code
    line, or the body of a here-doc, would both satisfy a naive text search
    while the code that actually emits the option was gone. These templates
    contain both — long explanatory comments and a quoted here-doc carrying the
    remote helper script.

    This is a deliberately conservative approximation of shell lexing, not a
    parser. It errs toward removing text (so the check errs toward FAILING,
    which is the safe direction), and the templates' real behaviour is pinned
    independently by tests/services/test_template_service.py, which asserts on
    the argv a rendered launcher actually hands to ssh.
    """
    out: list[str] = []
    heredoc_end: str | None = None
    for raw in text.splitlines():
        if heredoc_end is not None:
            if raw.strip() == heredoc_end:
                heredoc_end = None
            continue
        stripped = raw.strip()
        if stripped.startswith("#"):
            continue
        # Opening a here-doc: <<EOF, <<-EOF, <<'EOF', <<"EOF".
        m = re.search(r"<<-?\s*[\'\"]?([A-Za-z_][A-Za-z0-9_]*)[\'\"]?", raw)
        if m:
            heredoc_end = m.group(1)
            # The line opening the here-doc is still code; keep it, minus any
            # trailing comment.
        # Drop a trailing comment: a '#' preceded by whitespace and not inside
        # single or double quotes. Tracked crudely, which is why this is
        # documented as conservative.
        cleaned: list[str] = []
        quote: str | None = None
        prev = ""
        for ch in raw:
            if quote:
                if ch == quote:
                    quote = None
                cleaned.append(ch)
            elif ch in ("'", '"'):
                quote = ch
                cleaned.append(ch)
            elif ch == "#" and (prev == "" or prev.isspace()):
                break
            else:
                cleaned.append(ch)
            prev = ch
        out.append("".join(cleaned))
    return "\n".join(out)


def _argv_literals_with_identity(path: Path) -> list[tuple[int, list[str]]]:
    """Return (lineno, string elements) for each argv literal containing -i."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[tuple[int, list[str]]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.List, ast.Tuple)):
            continue
        strings = [
            el.value
            for el in node.elts
            if isinstance(el, ast.Constant) and isinstance(el.value, str)
        ]
        if "-i" in strings and strings and strings[0] in SSH_FAMILY:
            found.append((node.lineno, strings))
    return found


@pytest.mark.parametrize(
    "rel",
    sorted(
        rel
        for rel, e in EXPECTED_SITES.items()
        if e["must_pin"] and rel.endswith(".py") and rel not in KNOWN_GAPS
    ),
)
def test_each_pinned_argv_list_pins_its_own_identity(rel: str) -> None:
    """Per-argv-list check: every argv passing -i must also pass the pin.

    A file-level ``"IdentitiesOnly=yes" in text`` check is not sufficient, and
    this is not hypothetical: cpsm/ui/main_window.py builds two separate ssh
    probes, already contained the string at line 4108 for the first one, and
    the second went unpinned and unnoticed through an entire hand audit.  A
    file-level check passes as soon as ONE argv is pinned, so the assertion
    has to be made against each argv list individually.

    A file that assembles argv incrementally (``argv += [...]``, as
    cpsm/platform/ssh_binary.py does) has no single literal to inspect. That
    is DERIVED from the source here, not declared in a registry.

    An earlier version required such files to declare an exemption naming the
    tests that pinned them behaviourally. Reviewers defeated seven successive
    versions of the check that verified those declarations — by a comment, by
    a failure-message literal, by asserting on an unrelated value, by
    shadowing the name, by hiding the real call in a branch visited last, and
    by putting the assert in a nested function that was never called. Each fix
    modelled source-text adjacency rather than real control flow, so each was
    gamed one layer deeper.

    The declaration itself was the mistake. "This file contains no argv
    literal" is a fact the checker establishes for itself, so there is nothing
    to claim and nothing to fake. Such a file must still carry the pin
    (asserted below, so this test is never vacuous), and its behaviour is
    pinned by its own module's tests — for ssh_binary.py, the eight cases in
    tests/platform/test_ssh_binary.py covering identity/no-identity,
    caller-override, lookalike options and plink.
    """
    path = REPO_ROOT / rel
    literals = _argv_literals_with_identity(path)
    if not literals:
        assert _pins_in_code(path), (
            f"{rel} builds no argv literal containing -i AND does not pin in "
            "CODE. A comment or docstring quoting the option does not count — "
            "this file's own design notes mention it repeatedly, so a text "
            "match would stay green after the real line was deleted."
        )
        return
    unpinned = [lineno for lineno, strings in literals if "IdentitiesOnly=yes" not in strings]
    assert not unpinned, (
        f"{rel}: argv list(s) starting at line(s) "
        + ", ".join(str(n) for n in unpinned)
        + " pass -i without -o IdentitiesOnly=yes. Without the pin, ssh offers "
        "every agent key in addition to the one named, so the key actually "
        "used may not be the key requested."
    )
