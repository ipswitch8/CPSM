# -*- coding: utf-8 -*-
"""
Quantitative leak-measurement harness.

Why this exists
---------------
CPSM's ``StatusPoller`` fires ``poll_complete`` every 3000 ms (``cpsm/app.py``).
A long-lived GUI session therefore executes its poll handlers ~28,800 times per
day.  A leak of only a few kilobytes per cycle is invisible in a short run but
compounds to hundreds of megabytes per day.

Waiting for a real soak is not an option, so this harness drives a single call
path directly, thousands of times, and measures what survives a full
``gc.collect()``.  A path that leaks shows a positive, *linear* per-iteration
delta; a path that merely churns shows a flat one.

What gets measured
------------------
Three independent instruments, because no single one sees everything:

``py_bytes_per_iter``
    ``tracemalloc`` delta.  Sees Python-heap allocations only.

``py_objects_per_iter``
    ``len(gc.get_objects())`` delta.  Sees Python container/instance growth
    even when the bytes are small.

``qt_objects_per_iter``
    ``shiboken6.getAllValidWrappers()`` delta.  **This is the important one for
    a Qt app.**  ``QGraphicsItem`` / ``QWidget`` instances live on the C++ heap,
    which ``tracemalloc`` cannot see at all.  A scene rebuild that orphans C++
    objects is invisible to the first two instruments and obvious to this one.

``maxrss_delta_kb``
    ``ru_maxrss`` high-water mark, as a coarse whole-process cross-check.  It
    never decreases, so it is a leak *confirmation* signal, not a clean gauge.

Reading the result
------------------
``qt_type_histogram`` is the attribution tool: it names the exact Qt classes
whose live-instance count grew, which points straight at the offending code.
"""

from __future__ import annotations

import ctypes
import gc
import re
import resource
import tracemalloc
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import shiboken6

# Per-iteration growth at or below these thresholds is treated as measurement
# noise rather than a leak.  Qt/Python both do lazy caching (font metrics, glyph
# caches, interned strings) that settles after warmup but never reaches exactly
# zero.
NOISE_BYTES_PER_ITER = 64.0
NOISE_OBJECTS_PER_ITER = 0.10
NOISE_QT_OBJECTS_PER_ITER = 0.10


@dataclass
class LeakReport:
    """Result of measuring one call path."""

    label: str
    iterations: int
    py_bytes_per_iter: float
    py_objects_per_iter: float
    qt_objects_per_iter: float
    native_bytes_per_iter: float
    maxrss_delta_kb: int
    qt_type_histogram: dict[str, int] = field(default_factory=dict)
    top_python_sites: list[str] = field(default_factory=list)
    note: str = ""
    """Free-text context, e.g. proof that the path was genuinely exercised."""
    yield_every: int = 0
    """How often the loop pumped the Qt event loop (0 = never)."""

    # -- verdicts ------------------------------------------------------

    @property
    def leaks_qt_objects(self) -> bool:
        """True when live Qt (C++) instances grow per iteration."""
        return self.qt_objects_per_iter > NOISE_QT_OBJECTS_PER_ITER

    @property
    def leaks_python(self) -> bool:
        """True when Python-heap bytes or object count grow per iteration."""
        return (
            self.py_bytes_per_iter > NOISE_BYTES_PER_ITER
            or self.py_objects_per_iter > NOISE_OBJECTS_PER_ITER
        )

    @property
    def leaks_native(self) -> bool:
        """True when the glibc allocator's OS footprint grows per iteration."""
        return self.native_bytes_per_iter > NOISE_BYTES_PER_ITER

    @property
    def leaks(self) -> bool:
        return self.leaks_qt_objects or self.leaks_python or self.leaks_native

    @property
    def verdict(self) -> str:
        return "LEAKS" if self.leaks else "clean"

    def projected_mb_per_day(self, interval_s: float = 3.0) -> float:
        """Extrapolate the Python-heap leak to a real polling session.

        Qt C++ bytes are not included — ``qt_objects_per_iter`` is a *count*,
        not a size — so for a Qt-object leak this is a floor, not an estimate.
        """
        cycles_per_day = 86400.0 / interval_s
        worst = max(self.py_bytes_per_iter, self.native_bytes_per_iter)
        return (worst * cycles_per_day) / (1024.0 * 1024.0)

    def format(self) -> str:
        lines = [
            f"--- {self.label} ---",
            f"  iterations           : {self.iterations}",
            f"  py bytes  / iter     : {self.py_bytes_per_iter:10.1f}",
            f"  py objects/ iter     : {self.py_objects_per_iter:10.3f}",
            f"  QT objects/ iter     : {self.qt_objects_per_iter:10.3f}   <-- C++ instances",
            f"  native bytes/ iter   : {self.native_bytes_per_iter:10.1f}   <-- glibc arenas",
            f"  maxrss delta (kB)    : {self.maxrss_delta_kb:10d}",
            f"  projected MB/day     : {self.projected_mb_per_day():10.1f} (max of py/native)",
            f"  VERDICT              : {self.verdict}",
            f"  yielded every        : {self.yield_every or 'never (tight loop)'}",
        ]
        if self.note:
            lines.append(f"  exercised            : {self.note}")
        if self.qt_type_histogram:
            lines.append("  leaked Qt types (net live-instance delta):")
            for name, delta in sorted(self.qt_type_histogram.items(), key=lambda kv: -kv[1])[:12]:
                lines.append(f"      {delta:+8d}  {name}")
        if self.top_python_sites:
            lines.append("  top python allocation sites:")
            for site in self.top_python_sites[:8]:
                lines.append(f"      {site}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Native (glibc) heap probe
#
# The live process's swap is dominated by 128 MB / 64 MB anonymous mappings with
# RSS ~0 -- the signature of glibc malloc arenas, not Python objects.  Neither
# tracemalloc nor shiboken can see that memory, so measure it directly.
#
# mallinfo2() only describes the MAIN arena.  A threaded process spreads
# allocations across per-thread arenas, which is exactly the case here, so use
# malloc_info() instead: it emits XML covering every arena.
# ---------------------------------------------------------------------------

_libc = ctypes.CDLL("libc.so.6")
_libc.malloc_info.argtypes = [ctypes.c_int, ctypes.c_void_p]
_libc.malloc_info.restype = ctypes.c_int
_libc.open_memstream.argtypes = [
    ctypes.POINTER(ctypes.c_char_p),
    ctypes.POINTER(ctypes.c_size_t),
]
_libc.open_memstream.restype = ctypes.c_void_p
_libc.fclose.argtypes = [ctypes.c_void_p]
_libc.fflush.argtypes = [ctypes.c_void_p]
_libc.free.argtypes = [ctypes.c_void_p]

_SYSTEM_CURRENT_RE = re.compile(rb'<system type="current" size="(\d+)"/>')


def native_heap_bytes() -> int:
    """Bytes the glibc allocator currently holds from the OS, all arenas.

    This is the number that maps onto the anonymous mappings seen in
    ``pmap -X``.  It counts memory glibc has claimed, whether or not the
    application still considers it in use -- which is the point: memory that
    was freed but never returned to the OS still occupies swap.
    """
    buf = ctypes.c_char_p()
    size = ctypes.c_size_t()
    stream = _libc.open_memstream(ctypes.byref(buf), ctypes.byref(size))
    if not stream:
        return 0
    try:
        _libc.malloc_info(0, stream)
        _libc.fflush(stream)
        _libc.fclose(stream)
        raw = ctypes.string_at(buf, size.value)
    finally:
        if buf:
            _libc.free(buf)
    # The final <system type="current"> is the process-wide total; the
    # per-heap ones precede it.  Take the max to be robust to ordering.
    vals = [int(m) for m in _SYSTEM_CURRENT_RE.findall(raw)]
    return max(vals) if vals else 0


def malloc_trim() -> None:
    """Ask glibc to return free arena memory to the OS."""
    try:
        _libc.malloc_trim(0)
    except Exception:
        pass


def _qt_wrapper_census() -> Counter[str]:
    """Live C++-backed Qt object count, bucketed by class name."""
    return Counter(type(o).__name__ for o in shiboken6.getAllValidWrappers())


def _settle() -> None:
    """Drain Qt's deferred-delete queue, then fully collect."""
    try:
        from PySide6.QtCore import QCoreApplication

        app = QCoreApplication.instance()
        if app is not None:
            # deleteLater() targets are only reaped when the event loop spins.
            for _ in range(3):
                app.processEvents()
                app.sendPostedEvents(None, 0)  # 0 == QEvent.DeferredDelete
    except Exception:
        pass
    for _ in range(3):
        gc.collect()


# How often the measurement loop yields to the Qt event loop.
#
# This is not a detail. Driving a call thousands of times with NO yielding
# measures something a real application never does. QGraphicsView.fitInView()
# grows ~910 B/call in a fully tight loop and *exactly 0* when the loop yields
# even briefly (80 ms of total sleeping across 4000 calls is enough) -- and a
# real app spins its event loop continuously. Reporting the tight-loop figure
# as a leak rate was wrong, and it took two real processes plateauing to catch
# it. See CORRECTION 8 in docs/MEMORY-LEAK-INVESTIGATION.md.
#
# Yield every N calls so the measurement models an application, not a
# benchmark. Set to 0 to measure unyielded behaviour deliberately.
DEFAULT_YIELD_EVERY = 50


def _pump() -> None:
    """Give the Qt event loop a chance to run, as a live application would."""
    try:
        from PySide6.QtCore import QCoreApplication

        app = QCoreApplication.instance()
        if app is not None:
            app.processEvents()
    except Exception:
        pass


def measure_leak(
    label: str,
    call: Callable[[int], Any],
    *,
    iterations: int,
    warmup: int = 200,
    yield_every: int = DEFAULT_YIELD_EVERY,
) -> LeakReport:
    """Drive *call* ``iterations`` times and report what survives collection.

    Args:
        label:      Human-readable name of the call path under test.
        call:       Invoked as ``call(i)`` with the iteration index.
        iterations: Measured iterations (excludes warmup).
        warmup:     Unmeasured iterations run first, so that one-time lazy
                    caches (fonts, glyph atlases, import side effects) are
                    already populated and don't masquerade as a leak.
        yield_every: Pump the Qt event loop every N measured iterations, so the
                    measurement reflects a running application rather than an
                    unyielding benchmark. 0 disables it.

    Returns:
        A :class:`LeakReport`.
    """
    for i in range(warmup):
        call(i)
        if yield_every and i % yield_every == 0:
            _pump()
    _settle()

    # Object/RSS baselines are taken BEFORE tracemalloc starts and AFTER it
    # stops.  tracemalloc allocates its own bookkeeping objects while running,
    # which would otherwise be miscounted as leaked by the caller's code — that
    # inflated a provably-clean path to 0.366 objects/iter during development.
    objs_before = len(gc.get_objects())
    rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    qt_before = _qt_wrapper_census()
    native_before = native_heap_bytes()

    tracemalloc.start(20)
    snap_before = tracemalloc.take_snapshot()

    for i in range(iterations):
        call(warmup + i)
        if yield_every and i % yield_every == 0:
            _pump()

    _settle()

    snap_after = tracemalloc.take_snapshot()

    diff = snap_after.compare_to(snap_before, "lineno")
    total_bytes = sum(d.size_diff for d in diff)
    top_sites = [
        f"{d.size_diff:+9d} B  {d.traceback[0]}"
        for d in sorted(diff, key=lambda d: -d.size_diff)[:8]
        if d.size_diff > 0
    ]
    tracemalloc.stop()

    # Now that tracemalloc's bookkeeping is torn down, take the object census.
    _settle()
    qt_after = _qt_wrapper_census()
    objs_after = len(gc.get_objects())
    rss_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    native_after = native_heap_bytes()

    qt_delta: dict[str, int] = {}
    for name in set(qt_before) | set(qt_after):
        d = qt_after[name] - qt_before[name]
        if d != 0:
            qt_delta[name] = d
    qt_total = sum(v for v in qt_delta.values() if v > 0)

    return LeakReport(
        label=label,
        iterations=iterations,
        py_bytes_per_iter=total_bytes / iterations,
        py_objects_per_iter=(objs_after - objs_before) / iterations,
        qt_objects_per_iter=qt_total / iterations,
        yield_every=yield_every,
        native_bytes_per_iter=(native_after - native_before) / iterations,
        maxrss_delta_kb=rss_after - rss_before,
        qt_type_histogram=qt_delta,
        top_python_sites=top_sites,
    )
