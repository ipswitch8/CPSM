# -*- coding: utf-8 -*-
"""Tests for cpsm.build_id / version_string.

Motivation: `--version` moves only on release, so every build between two
releases reported an identical string and a stale binary could not be told
apart from a current one.  These lock in the behaviour that makes "am I
running the build with the fix?" answerable -- and, just as importantly, that
the answer is never confidently wrong.
"""

from __future__ import annotations

import sys
import types

import pytest

import cpsm
from cpsm import __version__, build_id, version_string


def _fake_stamp(monkeypatch, commit="deadbee"):
    mod = types.ModuleType("cpsm._build_stamp")
    mod.COMMIT = commit  # type: ignore[attr-defined]
    mod.BUILT_AT = "2026-09-04T00:00:00Z"  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "cpsm._build_stamp", mod)
    monkeypatch.setattr(cpsm, "_build_stamp", mod, raising=False)
    return mod


@pytest.fixture
def _no_stamp(monkeypatch):
    """Ensure no _build_stamp is visible, whatever the environment.

    Deleting the sys.modules entry is not enough: packaging writes a real
    cpsm/_build_stamp.py into the source tree, so the import simply re-reads
    it from disk and the test passes or fails depending on whether a build has
    been run.  Binding the name to None makes the import raise instead, which
    is the state these tests mean to describe.
    """
    monkeypatch.setitem(sys.modules, "cpsm._build_stamp", None)
    monkeypatch.delattr(cpsm, "_build_stamp", raising=False)


class TestBuildId:
    def test_version_string_contains_version_and_build(self):
        s = version_string()
        assert s.startswith(__version__)
        assert "build" in s

    def test_reports_the_commit_in_a_real_checkout(self, _no_stamp):
        bid = build_id()
        assert bid != "unknown"
        base = bid.removesuffix("-dirty")
        assert len(base) >= 7 and all(c in "0123456789abcdef" for c in base), bid

    def test_never_raises_when_git_is_unavailable(self, monkeypatch, _no_stamp):
        """A version string is not worth crashing a launch over."""

        def _boom(*a, **kw):
            raise OSError("git not found")

        monkeypatch.setattr("cpsm.subprocess.run", _boom)
        assert build_id() == "unknown"
        assert version_string() == f"{__version__} (build unknown)"


class TestStampDoesNotShadowSource:
    """A stamp must never mask the state of a source checkout.

    Packaging writes cpsm/_build_stamp.py INTO the tree, and this project
    builds in place, so after any build the checkout permanently holds a stamp
    naming the commit that build came from.  Preferring it made every later
    source run report that frozen commit no matter how far the tree had moved
    -- with no -dirty marker, because the stamp recorded a build that was
    clean at the time.  A confidently wrong answer is worse than "unknown".
    """

    def test_stamp_loses_to_git_in_a_checkout(self, monkeypatch):
        """The regression: a stale stamp must not win where git can answer."""
        _fake_stamp(monkeypatch, "deadbee")
        bid = build_id()
        assert bid != "deadbee", (
            "the stamp shadowed git in a source checkout -- a stale stamp "
            "would be reported as the running commit"
        )
        assert bid.removesuffix("-dirty").isalnum()

    def test_stamp_is_used_when_there_is_no_checkout(self, monkeypatch, tmp_path):
        """A frozen artifact has no .git, and its stamp cannot be stale."""
        _fake_stamp(monkeypatch, "deadbee")
        monkeypatch.setattr(cpsm, "_source_root", lambda: tmp_path)
        assert build_id() == "deadbee"

    def test_no_git_and_no_stamp_is_unknown(self, monkeypatch, tmp_path, _no_stamp):
        monkeypatch.setattr(cpsm, "_source_root", lambda: tmp_path)
        assert build_id() == "unknown"

    def test_checkout_without_working_git_reports_unknown(self, monkeypatch, tmp_path):
        """No git means no way to tell whether the stamp still applies.

        Reporting the stamp anyway would be exactly the confident-but-wrong
        answer this class exists to prevent, so it degrades to "unknown".
        """
        _fake_stamp(monkeypatch, "deadbee")
        (tmp_path / ".git").mkdir()  # looks like a checkout
        monkeypatch.setattr(cpsm, "_source_root", lambda: tmp_path)
        monkeypatch.setattr(
            "cpsm.subprocess.run", lambda *a, **kw: (_ for _ in ()).throw(OSError("no git"))
        )
        assert build_id() == "unknown"

    def test_dirty_tree_is_flagged(self, monkeypatch, tmp_path):
        """-dirty must survive: "which commit" is half an answer otherwise."""
        _fake_stamp(monkeypatch, "deadbee")
        (tmp_path / ".git").mkdir()
        monkeypatch.setattr(cpsm, "_source_root", lambda: tmp_path)

        calls = {"n": 0}

        class _R:
            returncode = 0

            def __init__(self, out):
                self.stdout = out

        def _fake_run(argv, **kw):
            calls["n"] += 1
            if "rev-parse" in argv:
                return _R("abc1234")
            return _R(" M cpsm/cli.py")  # status --porcelain: dirty

        monkeypatch.setattr("cpsm.subprocess.run", _fake_run)
        assert build_id() == "abc1234-dirty"
        assert calls["n"] == 2
