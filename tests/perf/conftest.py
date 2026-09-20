# -*- coding: utf-8 -*-
"""
Shared configuration for the perf test package.

The memory-measurement harness (``tests/perf/leak_harness.py``) uses
``resource.getrusage``, which is a Unix-only stdlib module. Every test module
in this package imports it transitively, so on Windows the whole package fails
at *collection* with ``ModuleNotFoundError: No module named 'resource'`` —
taking the entire Windows CI run down with it, before a single test executes.

That platform requirement has always been true; it was simply never declared.
Declaring it here is not weakening the suite: these tests measure RSS growth,
which needs a POSIX resource API and a machine that is not a noisy shared
runner. They run in full on Linux, where they are meaningful.

Mirrors the convention already used by the Windows-only backend contract
tests in ``tests/platform/`` — a module-level skip with a reason that says
why, rather than a silent exclusion in the CI invocation where nobody would
see it.
"""

from __future__ import annotations

import sys

import pytest

if sys.platform == "win32":
    pytest.skip(
        "perf tests measure RSS via resource.getrusage, which is Unix-only",
        allow_module_level=True,
    )
