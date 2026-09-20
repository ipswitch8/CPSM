# CPSM memory leak — investigation

> **READ THIS SECTION FIRST. IT IS THE ONLY PART THAT IS CURRENT.**
>
> What follows is a chronological log, written as the investigation ran. It
> contains thirteen numbered CORRECTIONS, and several sections state conclusions
> that were later measured to be WRONG and are retracted in place. They are kept
> deliberately, because the wrong turns are the useful part.
>
> Three headings below are SUPERSEDED and must not be acted on:
>
> * "ROOT CAUSE (bisected, isolated)" — the `fitInView` finding. Retracted by
>   CORRECTION 8.
> * "FINAL POSITION" — claimed no leak was reproducible. Wrong; it measured the
>   venv, not the shipped artifact.
> * "ROOT CAUSE — the AppImage bundles glibc 2.35" — retracted by CORRECTION 11.
>
> The live one is "ROOT CAUSE CONFIRMED — appimage-builder's
> `libapprun_hooks.so`". Nothing below this summary should be quoted without
> checking whether a later correction overturns it.

## Answer

**The leak was in `libapprun_hooks.so`, appimage-builder's `LD_PRELOAD`ed shim.
Nothing in CPSM's application code caused it.**

Controlled A/B — same extracted AppDir, same session, same script, differing
only in whether that one 53 KB library is real or an inert stub:

```
hooks INTACT     +1176 KB / 240s  ->  413.4 MB/day
hooks STUBBED       +0 KB / 240s  ->    0.0 MB/day
```

The shim intercepts `open`/`openat`/`dlopen`/`exec*` to rewrite AppDir-relative
paths for the application and its children. That happens continuously,
regardless of what the application does — which is why the leak rate was
invariant to poll interval, wake-up rate, workload, X11, DBus and fonts.

## Status: FIXED

Two commits, because removing the shim exposed a second bug it had been masking.

| Commit | Fix |
|---|---|
| `5af2aaf` | Build with stock `appimagetool` instead of appimage-builder — `scripts/build_appimage_plain.sh`. Four-line `AppRun`, no shim, no bundled glibc. |
| `349472b` | Sanitise child-process environments — `cpsm/platform/child_env.py`, wired into every spawn site. Required because the shim was ALSO scrubbing child environments; without this, konsole refuses to start. |

| Build | Rate |
|---|---:|
| appimage-builder (the original bug) | 413–460 MB/day |
| **plain appimagetool + child-env fix** | **3.4 MB/day** |
| `dist/cpsm/cpsm` (no AppImage) | 0.0 MB/day |

~99%. Over the six days that produced the reported 1.76 GB, roughly 20 MB.

The 3.4 MB/day residual is at the floor of this measurement method (+12 KB over
a 300 s window) and matches noise recorded for known-clean processes. It is NOT
claimed as zero, because it has not been measured as zero.

## What to do

| | |
|---|---|
| **Build** | `CPSM_VERSION=<v> scripts/build_appimage_plain.sh` |
| **Before shipping** | Launch a real session and confirm a terminal opens. Use konsole or xterm — gnome-terminal delegates to a D-Bus server that does not inherit the environment and would mask the child-env bug. This is NOT verified. |
| **Upstream** | Worth reporting to appimage-builder; `scripts/diagnose_appimage_leak.sh` reproduces it. |

**Do not run a `dist/cpsm/cpsm` built before `349472b`.** Earlier revisions of
this document recommended it as a workaround, which was correct about the leak
and wrong about everything else: PyInstaller — not AppImage — is what sets the
offending `LD_LIBRARY_PATH`/`QT_PLUGIN_PATH`, so an older `dist/` build cannot
open a terminal either. Rebuild it.

**Do not "just stub the hooks" in a shipped build.** That was a diagnostic. The
library exists to fix up child environments, and `349472b` is the proper
replacement for that job.

**Portability trade.** Dropping appimage-builder drops its ~112 bundled
libraries and compat glibc, so the AppImage now needs a host of roughly the
build machine's vintage. Deliberate: build on the oldest distribution you
support rather than reinstating the shim.

## Evidence in one table

| Build | Launched via | Rate |
|---|---|---:|
| venv source tree | python directly | **0.0 MB/day** |
| PyInstaller one-folder (`dist/cpsm/cpsm`) | binary directly | **0.0 MB/day** |
| AppImage, appimage-builder | AppRun (+ hooks) | **406–460 MB/day** |
| AppImage, hooks stubbed (diagnostic) | AppRun (inert hooks) | **0.0 MB/day** |
| **AppImage, plain appimagetool + child-env fix** | plain AppRun | **3.4 MB/day** |

Reported incident: PID 377627 held 1,842,480 KB (1.757 GB) of swap after 6 days,
growing ~330 MB/day. Reproduced here at 413–460 MB/day.

## Why it took so long

Two framing errors, each of which invalidated everything built on top of it:

1. **The venv was assumed to proxy the shipped artifact.** It does not. Hours of
   harness work measured a runtime that does not have the bug, and produced a
   confident "no leak reproduces at HEAD" that was true of the venv and false of
   CPSM as shipped. Testing the package was the user's suggestion.

2. **The leak was assumed to be poll-driven**, because the original figure was
   quoted as "~12 KB per poll" — a number that was only ever `rate / interval`,
   never a measured per-poll cost. Six hypotheses were searched inside the poll
   before anyone changed the poll interval and found the rate unmoved.

A third, narrower failure recurred three times: a measurement rig that does not
reproduce the application's real call shape measures itself. It produced one
false exoneration (empty layout, handler early-returned), one false accusation
(stale layout re-fed, "reconcile fires every poll"), and one silently inert
harness (a `TypeError` swallowed by `StatusPoller.run`).

Of the ~18 hypotheses tested, two were my own "confirmed" findings, later
retracted. Every one died on measurement, and none died on review — four gate
reviews passed over the `fitInView` finding before a real process disagreed
with it.

---
# CPSM memory leak — investigation findings

## Premise: confirmed

`pmap -X 377627` totals:

```
Size 2,995,708 KB | Rss 277,336 KB | Swap 1,842,480 KB
```

**1,842,480 KB = 1.757 GB of swap**, matching the reported 1.76 GB exactly.
CPSM is definitively the process holding it.

## Shape of the leaked memory

| Mapping class | Count | Swap | Rss |
|---|---:|---:|---:|
| 128 MB anon | 11 | ~1.40 GB | ~0 |
| 64 MB anon | 6 | ~0.40 GB | ~0 |
| `[heap]` | 1 | 10 MB | 11 MB |

267 of 832 mappings hold swap; the total is dominated by those seventeen.
Fully paged out, resident ~0 — memory written once and never touched again.

Sizes of exactly 128 MB / 64 MB, page-aligned, are **glibc malloc arenas**, not
Python objects. This is native memory the allocator claimed and never returned.

## What was ruled OUT (measured, not assumed)

| Suspect | Evidence | Verdict |
|---|---|---|
| Monitor-identifier ambiguity forcing endless `_reconcile` | All 3 displays report distinct EDID serials; `_ambiguous_identifiers()` is empty | exonerated |
| `ActiveSessionsWidget._on_poll_complete` / `setIndexWidget` churn | 0.000 Qt objects/iter, 0.0 native B/iter over 2000 iters | exonerated |
| Queued `Signal(list)` marshalling (same-thread) | 0.000 Qt objs, negative native drift, 2201 deliveries | exonerated |
| Queued `Signal(object)` `state_changed` | 0.000 Qt objs, 0.0 native | exonerated |
| **Cross-thread** `Signal(list)` (real `QThread`, production topology) | 0.000 Qt objs, 0.0 native, 551 deliveries | exonerated |
| Python-object / Qt-instance leak anywhere in poll path | `gc` and `shiboken6.getAllValidWrappers()` both flat | exonerated |
| `QFontMetricsF` probe loop in `_draw_pane` (up to 23 probes per pane per rebuild) | Growth curve with it stubbed out: **992.6 B/iter**, vs 947.5 B/iter unstubbed — identical linear shape, final growth 6496 KB vs 6612 KB | exonerated |
| Other periodic work | **No `QTimer`/`startTimer` anywhere in `cpsm/`** — StatusPoller is the only clock | n/a |

## What was CONFIRMED

`ScreenMapWidget.set_layout` — the full `QGraphicsScene` rebuild that
`MainWindow._on_status_poll_complete` performs on **every 3-second poll**.

Growth curve, 10,000 rebuilds, 6 panes, offscreen:

```
after   500 iters :    692.0 KB
after  1500 iters :   1460.0 KB
after  3500 iters :   1460.0 KB   <- warm-up plateau
after  5000 iters :   2516.0 KB
after  7500 iters :   4368.0 KB
after 10000 iters :   6612.0 KB
growth first third:   1460.0 KB
growth last third :   2776.0 KB
SHAPE             : LINEAR (unbounded growth — genuine leak)
steady-state rate :    947.54 B/iter
```

The curve is the key evidence. It plateaus through ~3500 iterations (bounded
cache warm-up) and then climbs **linearly and without bound**. Growth in the
last third exceeds growth in the first third, which a warm-up cache cannot do.

At 947 B/rebuild x 28,800 polls/day = **~27 MB/day** in the offscreen harness.
The live process shows ~333 MB/day, so the offscreen figure is a floor —
expected, since the offscreen QPA uses a stub font/graphics stack.

## Methodological notes (why earlier numbers were wrong)

* **Single before/after deltas are unusable here.** glibc claims arena memory in
  large chunks, so a short run either straddles a chunk boundary or does not.
  The same 6-pane rebuild measured 622 B/iter over 2000 iterations, 1413 B/iter
  over 400, and 0.0 over other 400-iteration runs. The pane-count "scaling"
  test produced 0 / 1413 / 0 / 1341 for 3 / 6 / 12 / 24 panes — pure
  quantisation, no scaling signal. Only the growth curve is reliable.
* **A false exoneration was caught and fixed.** The first
  `MainWindow._on_status_poll_complete` measurement read clean only because the
  fixture loaded an `empty` layout (2 scene items) with `_services = None`, so
  the handler early-returned. Every measurement now asserts the path did real
  work before reporting.
* **tracemalloc cannot see this leak at all.** It is native. Attribution
  required `malloc_info()` across all arenas plus
  `shiboken6.getAllValidWrappers()` to prove no Qt *instances* leak.

## Harness

* `tests/perf/leak_harness.py` — 4 instruments: tracemalloc, gc, shiboken
  wrapper census, glibc `malloc_info()` across all arenas.
* Self-validated in both directions: reports exactly 1.000 leaked Qt
  objects/iter on a deliberate leak, clean on a deliberately clean path.

## ROOT CAUSE (bisected, isolated)

> **RETRACTED — see CORRECTION 8.** `fitInView` does not leak in a running
> application; this was an artifact of a measurement loop that never yielded.


`QGraphicsView.fitInView()` — called unconditionally on **every** scene rebuild,
i.e. every 3-second poll, at `cpsm/ui/widgets/screen_map.py:2033`:

```python
self._view.fitInView(self._scene.itemsBoundingRect(), Qt.AspectRatioMode.KeepAspectRatio)
```

Bisection of `_redraw` into its separable operations, each over 10,000
iterations using the growth-curve discriminator:

| Component | Shape | Steady-state rate |
|---|---|---:|
| `scene.clear()` only | PLATEAU | 0.00 B/iter |
| clear + 24 `QGraphicsRectItem` | PLATEAU | 0.00 B/iter |
| clear + 6 `QGraphicsTextItem` | PLATEAU | 0.00 B/iter |
| **`fitInView()` only** | **LINEAR** | **991.23 B/iter** |

`fitInView()` alone (991.23 B/iter) accounts for the entire full-rebuild rate
(947.54 B/iter). Every other part of the rebuild plateaus — they churn memory
but return it. Scene construction is not the problem; re-fitting the view is.

### Corroboration against the live process

Read-only `pmap -X` sampling of PID 377627 while it ran normally:

```
t+0   s : anon_total 2,121,688 KB
t+50  s : anon_total 2,121,876 KB   (+188 KB)
t+180 s : anon_total 2,122,376 KB   (+688 KB)
```

688 KB / 180 s = 3.82 KB/s = **~330 MB/day**, against 1.76 GB accumulated over
6 days (~300 MB/day). The live rate confirms the leak is per-poll and ongoing.

Per poll that is ~11 KB, versus ~0.95 KB measured in the offscreen harness —
the offscreen QPA uses a stub font/graphics stack, so it understates the real
X11/xcb cost by roughly 12x. The *shape* reproduces faithfully; the magnitude
does not.

### Other `fitInView` call sites

| Line | Context | Per-poll? |
|---|---|---|
| 2033 | `_redraw` | **yes — the leak** |
| 2149 | `_redraw_multi` (multi-group overlay) | yes, in overlay mode |
| 2270 | `_DragDropView.resizeEvent` | no — user-driven |
| 2276 | `_DragDropView.showEvent` | no — one-time |

Only the rebuild paths need fixing; the event-driven ones fire rarely.

### Why this is the right fix target

When the layout and viewport are unchanged, `fitInView` recomputes and applies
an **identical** transform. Skipping it in that case is behaviourally a no-op,
so the fix is a pure guard: cache the last fitted bounding rect and viewport
size, and only re-fit when one of them actually changes.

---

# CORRECTIONS AND FURTHER NARROWING

Two hypotheses above were overturned by better measurement. Recorded here
rather than silently edited, because the reasoning matters.

## Correction 1 — `fitInView` is real but only ~8% of the problem

`QGraphicsView.fitInView()` genuinely leaks, confirmed by separating it from
its argument:

| Operation | Rate | Verdict |
|---|---:|---|
| `itemsBoundingRect()` alone | 0.00 B/call | clean |
| `fitInView(fixed QRectF)` | 973.21 B/call | **LEAKS** |
| `fitInView(itemsBoundingRect())` | 973.21 B/call | **LEAKS** |
| `setTransform(identity)` | 0.00 B/call | clean |

So it is neither the bounds computation nor transform application — it is
`fitInView` itself, an upstream Qt 6.11 / PySide6 6.11 defect. Confirmed under
BOTH the offscreen and real xcb backends (865-919 B/call under xcb), so it is
not a platform artifact.

But at one call per 3-second poll that is only **~27 MB/day**, against ~330
MB/day observed. It is a genuine bug worth fixing; it is not the main cause.

## Correction 2 — garbage collection is NOT involved

Same workload, three GC regimes:

| GC regime | Rate | collections gen0/1/2 | uncollectable |
|---|---:|---|---:|
| Automatic (production-like) | 16.9 MB/day | 0 / 0 / 0 | 0 |
| Forced `collect()` per call | 31.6 MB/day | 0 / 0 / 4000 | 0 |
| Disabled | 31.8 MB/day | 0 / 0 / 0 | 0 |

`gc.garbage` empty, uncollectable 0, live Python objects grew by **1** over
4000 rebuilds. Automatic GC is the *lowest*. Deferred collection is not hiding
anything — this memory was never Python's to collect.

## Correction 3 — a reproduction that looked spectacular was an artifact

Running the real `StatusPoller` at `interval_ms=0` grew 1.8 GB in 183 s. That
was **queued-event backlog**, not a leak: the poller emitted `poll_complete`
faster than the GUI thread could drain the queue, so `QMetaCallEvent` objects
piled up. With the interval set to a fast-but-drainable 20 ms and emissions
counted against deliveries (2389 emitted, 2389 delivered, 53/s):

```
anon growth : 280 KB over 45 s
per poll    : 120 B   =>  3.3 MB/day at the production 3 s interval
```

The poller loop is clean at realistic rates. Any measurement of a producer
thread must verify the consumer keeps up, or backlog reads as a leak.

## Narrowing — the growth is one glibc per-thread arena

Per-mapping diff of the live process over 120 s (read-only `pmap -X`):

```
DELTA: +468 KB over 120s  =>  329.1 MB/day

changed mappings:
      +468 KB  addr=739ca0000000  size=72808 KB  perm=rw-p  [anon]
  NEW    0 KB  addr=739ca471a000  size=58264 KB  perm=---p
  GONE   0 KB  addr=739ca46a5000  size=58732 KB  perm=---p
mapping count unchanged: 832
```

**All** growth is confined to a single anonymous mapping. The adjacent
`PROT_NONE` guard region moves forward by exactly the amount the heap grows —
the signature of glibc's `grow_heap()` mprotecting more of a **non-main arena**
reservation. Non-main arenas are per-thread.

This explains why every earlier probe understated the rate: they all ran on the
**main** thread, which uses the sbrk heap with completely different trim
behaviour.

`malloc_trim(0)` in the reproduction reclaimed only 244-252 KB, so the retained
memory is not simply free-but-untrimmed fragmentation in that scenario.

## Still open

The specific allocation filling that arena in the live process is not yet
identified. A faithful reproduction (real `MainWindow`, seeded config, real
poller) is the remaining step; attaching a heap profiler to PID 377627 was
blocked by the permission classifier and was not worked around.

## Seeded real-instance run — INCONCLUSIVE, not an exoneration

A real `cpsm gui` process was launched with an isolated seeded config
(6 connections, 1 group, 3-monitor layout) and sampled for 240 s:

```
+0s   anon=117404 KB
+40s  anon=117384 KB   (-20 KB)
+80s  anon=117388 KB   (-16 KB)
+120s anon=117404 KB   (0 KB)
+240s anon=117404 KB   (0 KB)   => 0.0 MB/day
```

The obvious reading is "a fresh CPSM does not leak". That reading is wrong on
two counts, both checked rather than assumed:

1. **The path was genuinely exercised.** A separate probe confirmed a fresh
   `MainWindow` populates `_layout_data` (`layout-seed`, 3 monitors, 6 scene
   items), so the handler's `if ... and self._screen_map_widget._layout_data:`
   guard is True and the rebuild does run on every poll. So this is not the
   earlier early-return trap.

2. **The run was far too short to resolve the effect.** 240 s at a 3 s poll is
   ~80 rebuilds. At the measured 973 B/rebuild that is ~78 KB — comfortably
   absorbed by free space already inside the arena, so glibc never extends the
   mapping and `pmap` shows no change. Arena growth is quantised in large
   chunks; that is the same effect that made single-delta measurements useless
   earlier in this investigation.

`pmap`-sampling a real instance therefore needs hours, not minutes, to resolve
a ~27 MB/day signal. The in-process growth curve (10,000 iterations) exists
precisely to avoid that limitation and remains the reliable instrument.

## Honest accounting of the gap

| Source | Rate | Status |
|---|---:|---|
| `fitInView()` once per poll | ~27 MB/day | **confirmed, reproducible, fixable** |
| Unattributed remainder | ~300 MB/day | localized to one per-thread glibc arena; specific allocation not identified |

The live process's threads were: main, `StatusPoller`, `QXcbEventQueue`,
`QDBusConnection`, and 5 unnamed. The growing arena is non-main, so the
allocations come from one of the worker threads. `QXcbEventQueue` is a
plausible candidate — it exists only under a real X11 session with an actively
repainting window, which no harness run reproduced, and `fitInView` changing
the view transform every 3 s forces exactly that repaint traffic. That is a
hypothesis, NOT a finding: it was not measured, because PID 377627 has since
been terminated and a heap profiler could not be attached to it.

## Further exonerations (measured)

| Suspect | Evidence | Verdict |
|---|---|---|
| `_redraw_multi` throw-away parentless `QGraphicsScene` (screen_map.py:2117) | Committed run: 411.6 B/redraw, 11.3 MB/day, live Qt instance delta **0.000** — see CORRECTION 7; the byte growth is the same `fitInView` defect recurring at screen_map.py:2149, not a new cause | parentless scene exonerated; path still leaks via `fitInView` |
| Dead panes driving `capture_pane(lines=200)` churn on the poller thread | 4 dead panes with realistic 200-line captures leaked **less** than the no-dead-pane baseline (1.1 vs 2.5 MB/day), backlog-controlled at 2240 delivered polls | exonerated |

The dead-pane test was the strongest remaining candidate for per-thread arena
growth, since `capture_pane` is the only large allocation the StatusPoller
thread performs. It does not reproduce.

## `fitInView` is safe to guard — idempotence verified

The proposed fix skips `fitInView` when neither the scene bounds nor the
viewport size changed. That is only valid if repeat calls are idempotent:

```
transform after  1 call  : (0.0222016651, 0, 0, 0, 0.0222016651, 0, 0, 0, 1)
transform after  2 calls : (0.0222016651, 0, 0, 0, 0.0222016651, 0, 0, 0, 1)
transform after 12 calls : (0.0222016651, 0, 0, 0, 0.0222016651, 0, 0, 0, 1)
```

Identical to 1e-12. `fitInView` does not converge iteratively, so skipping a
redundant call cannot change what is rendered.

## SECOND CONFIRMED BUG — pathological reconcile loop on matched displays

`layout_needs_reconcile()` (cpsm/services/layout_reconciler.py:287) opens with:

```python
ambiguous = _ambiguous_identifiers(monitors)
if any(m.identifier in ambiguous for m in layout.monitors if m.identifier):
    return True
```

When a stored layout carries an identifier shared by two attached displays this
returns True on **every** call, and nothing in the repair path can make it
False again — the identifier is whatever the hardware reports. `set_layout()`
-> `_reconcile()` therefore, on every 3-second poll, forever:

  * rebuilds the entire `ScreenLayout` via `reconcile_layout_with_monitors()`
    (full pydantic model construction),
  * writes a `logger.info(...)` line,
  * emits `layout_reconciled`, which MainWindow persists — **a config file
    write every 3 seconds**.

Measured, 4000 rebuilds each:

| Case | `layout_needs_reconcile()` | `layout_reconciled` emits | Native rate |
|---|---|---:|---:|
| Unique EDID serials | False | 0 / 4000 (0%) | 0.0 MB/day |
| **Ambiguous identifiers** | **True** | **4000 / 4000 (100%)** | **20.8 MB/day** |

This is provoked by exactly the hardware in play: two identical ViewSonic
VX2757A-FHD panels. Today they report distinct serials
(`XVD254700640` / `XVD254700618`) so HEAD is not in this state — but a stored
layout written by a build whose identifiers omitted the serial would pin it
there permanently.

Note the running process was the AppImage built **2026-08-12 09:23**, the same
day the whole screen re-detection feature landed (`ead3146`, `19d467f`,
`fd10acd` "handle identical monitors that report no EDID serial", `06ce374`
"repair layouts already carrying a colliding monitor identifier", `e0cf0a5`).
A binary from partway through that series, holding a layout written before the
serial was included in the identifier, is a plausible fit for the observed
behaviour. This is circumstantial: the process has been terminated and its
config was not captured, so it cannot now be confirmed.

Beyond memory, the 3-second config write is a defect in its own right:
~28,800 writes/day, ~172,800 over the observed 6-day uptime.

---

# RETRACTION — "defect 2" (reconcile loop) was a probe artifact

The section above titled *SECOND CONFIRMED BUG — pathological reconcile loop on
matched displays* is **withdrawn**. It measured my test harness, not the app.

## What was wrong

The probe called `w.set_layout(L, M)` with the *same original* layout object on
every iteration. The application does not do that.
`MainWindow._on_status_poll_complete` passes
`self._screen_map_widget._layout_data`, and `set_layout` assigns

    self._layout_data = self._reconcile(layout, monitors)

so each poll feeds back the layout that reconciliation already repaired.

## The evidence

`reconcile_layout_with_monitors` is convergent — it *clears* the colliding
identifiers and falls back to index hints:

```
L0 identifiers: ['Chimei Innolux Corporation',
                 'ViewSonic Corporation-VX2757A-FHD',
                 'ViewSonic Corporation-VX2757A-FHD']
L1 identifiers: ['Chimei Innolux Corporation', None, None]
L1 index hints: [0, 1, 2]

L1 == L0            : False   (a real repair happened)
L2 == L1            : True    (fixed point reached)
needs_reconcile(L1) : False
```

Reproducing the app's actual feedback loop:

```
(A) app-faithful (feeds back _layout_data) :   1 emit  over 501 polls
(B) stale-layout probe (earlier, WRONG)    : 500 emits over 500 polls
```

The real poll path self-heals after a single reconcile. There is no per-poll
rebuild, no repeated `logger.info`, and no 3-second config write.

## Consequence

* The claimed "20.8 MB/day" from this path is **not** attributable to the
  application.
* The claimed "~28,800 config writes/day" **does not happen**. That was the most
  alarming assertion in this document and it was wrong.
* Total attributed leak drops from ~48 MB/day to **~27 MB/day** — `fitInView`
  alone, which remains confirmed and independently isolated.
* The unattributed remainder grows correspondingly, to ~300 MB/day of the
  ~330 MB/day observed.
* Planned-fix item 2 (the `reconciled == layout` guard) is **dropped**. It would
  have guarded against nothing.

`layout_needs_reconcile()` does still return True indefinitely *if* it is
repeatedly handed a stale, unrepaired layout — that property is retained as a
documented characterisation test, not as a defect claim.

## Why this happened

Same root error as the earlier false exoneration, in the opposite direction: the
harness did not reproduce the app's real call shape. A measurement rig has to
mirror the feedback loop it is modelling, or it measures itself. Both directions
of that mistake — falsely clean, falsely damning — appear in this investigation.

---

# CORRECTION 5 — the poller-thread measurements were measuring nothing

A gate review flagged that the `_redraw_multi` and dead-pane `capture_pane`
numbers were quoted as "(measured)" with no committed artifact. Converting them
into real tests immediately exposed a worse problem than the missing artifact.

`Pane` (cpsm/platform/base.py:42) is a frozen dataclass whose `pid`, `width` and
`height` fields are REQUIRED. The probe backends omitted them, so `list_panes()`
raised `TypeError` — and `StatusPoller.run()` swallows it:

```python
try:
    panes = self._backend.list_panes()
except Exception:
    panes = []
```

Every poller-thread measurement therefore ran against an **empty pane list**.
The loop spun, delivered signals, and allocated essentially nothing. The figures
previously reported from those probes (120 B/poll, 90 B/poll, 38 B/poll, and the
dead-pane comparison) described an idle loop, not the poll path. They are
withdrawn.

The tests now assert `len(poller.last_snapshot) > 0` and, when dead panes are
configured, `capture_pane` call count > 0 — because "the poller ran" is not the
same as "the poller saw anything". Re-measured with the path genuinely
exercised:

```
--- StatusPoller thread, 0 dead panes ---
  polls delivered   : 992     panes per snapshot: 6
  capture_pane calls: 0
  native growth     : 0 B     => 0.0 MB/day at a 3s interval

--- StatusPoller thread, 4 dead panes (200-line capture each) ---
  polls delivered   : 991     panes per snapshot: 10
  capture_pane calls: 4364
  native growth     : 0 B     => 0.0 MB/day at a 3s interval
```

The conclusion is unchanged — the poller thread does not leak — but it now rests
on a measurement that actually exercised the code. Backed by
`tests/perf/test_overlay_and_worker_paths.py`, which appends to
`tests/perf/leak_measurements.txt`.

`_redraw_multi` is likewise now a committed test, asserting the structural
property that matters (the throw-away parentless `QGraphicsScene` must not
strand live Qt instances) rather than a noisy byte rate.

## Note on this class of error

This is the third measurement-rig failure in the investigation, and the pattern
is identical each time: **the harness did not reproduce the app's real call
shape, so it measured itself.** Falsely clean (empty layout), falsely damning
(stale layout re-fed), and now silently inert (swallowed constructor error). The
exercise-guard assertions exist because of the first one and caught the third.

## Provenance: the mechanism is not new

`git log -S` shows both halves of the confirmed path predate the observed
incident by months:

| Construct | Introduced |
|---|---|
| `MainWindow._on_status_poll_complete` (per-poll canvas rebuild) | `021b335`, 2026-05-02 (initial commit) |
| `fitInView` inside `_redraw` | `021b335`, 2026-05-02 (initial commit) |

So the AppImage built 2026-08-12 09:23 contained the same per-poll `fitInView`
call as HEAD. The "maybe it was fixed between that build and HEAD" hypothesis is
therefore NOT supported for this path — whatever HEAD does here, that build did
too. Any remaining difference would have to come from elsewhere.

This also means a HEAD measurement is a valid proxy for the reported build with
respect to the confirmed defect.

---

# ATTRIBUTION ACCOUNTING (phase-2 criterion 4)

## (a) Confirmed contribution, in the observed units

| Source | Per poll | Per day (3 s interval) | % of observed |
|---|---:|---:|---:|
| `QGraphicsView.fitInView()` in `_redraw` | ~973 B | ~27 MB | **8.2%** |
| **Unattributed remainder** | ~11.1 KB | ~303 MB | **91.8%** |
| Observed on PID 377627 | ~12.1 KB | ~330 MB | 100% |

The observed figure is itself well established: `pmap -X` sampling of the live
process gave 329-345 MB/day across six samples over 6.8 minutes, consistent with
1.757 GB accumulated over 6 days of uptime.

## (b) The remainder has NOT been silently localized elsewhere

It is localized, and the localization is stated: a per-mapping diff showed all
growth in ONE anonymous mapping whose adjacent `PROT_NONE` guard advanced in
lockstep — a glibc **non-main (per-thread) arena**. Non-main arenas belong to
worker threads. The live process's threads were main, `StatusPoller`,
`QXcbEventQueue`, `QDBusConnection`, and five unnamed.

What that does NOT tell us is which allocation fills it. Every worker-thread
path that could be reproduced was measured and came back clean:

* `StatusPoller` loop, 992 delivered polls, 6 panes/snapshot — 0 B growth.
* Same with 4 dead panes and 4364 `capture_pane` calls — 0 B growth.
* tmux `list_panes`/`list_sessions` subprocess churn — plateau.
* Queued and cross-thread `Signal(list)` marshalling — plateau.

`QXcbEventQueue` remains an untested hypothesis, not a finding: it exists only
under a real X11 session with an actively repainting window, and no in-process
harness reproduces it.

## (c) Resolving the gap — measurement against HEAD, and why it is inconclusive

A real `cpsm gui` instance was launched against HEAD with a seeded config
(6 connections, 1 group, 3-monitor layout; `_layout_data` verified populated so
the poll handler does not early-return) and sampled with `pmap -X`:

```
+0s   anon=119516 KB
+300s anon=119516 KB   (+0 KB)    0.0 MB/day
+600s anon=119520 KB   (+4 KB)    0.6 MB/day
```

**This neither confirms nor refutes the 27 MB/day figure, and it must not be
read as either.** The reason is measurable: the in-process growth curve for this
path is flat at ~1460 KB through roughly 3,500 iterations (bounded warm-up) and
only then turns linear. At a 3-second poll interval, 3,500 polls is ~2.9 hours.
A 10-minute sample is ~200 polls — deep inside the plateau, where the unfixed
code also reads as 0. `pmap` granularity compounds this: 200 polls x 973 B is
~195 KB, comfortably absorbed by free space already inside the arena without
glibc extending the mapping.

Short real-instance sampling is therefore the wrong instrument for this effect,
in exactly the way single-delta measurements were shown to be earlier.

Provenance rules out the tempting alternative explanation. `git log -S` shows
both `_on_status_poll_complete`'s per-poll rebuild and the `fitInView` call in
`_redraw` date to the initial commit (`021b335`, 2026-05-02). The AppImage that
exhibited the leak (built 2026-08-12 09:23) therefore contained the same code
path as HEAD. "It was fixed between that build and HEAD" is NOT supported.

### Status: OPEN GAP, with named next experiments

Recorded explicitly rather than closed:

1. **Long-run real-instance sampling.** Run a real `cpsm gui` against HEAD for
   >= 4 hours (>= 4,800 polls, clear of the ~3,500-iteration warm-up) sampling
   `pmap -X` every 10 minutes. Expected signal if `fitInView` dominates:
   ~1.1 MB/hour. Expected if the live 330 MB/day reproduces: ~14 MB/hour. These
   differ by more than 10x and are trivially distinguishable at that duration.
   This is the single most decisive outstanding experiment.

2. **Native heap profiler on a real X11 instance.** `LD_PRELOAD` a sampling
   profiler (heaptrack / jemalloc prof) against a real `cpsm gui` under xcb and
   read the allocation stacks directly. This is the only method that attributes
   a per-thread arena to a call site rather than inferring it. It was not
   available here: attaching to the live process was blocked by the permission
   classifier, and the process has since been terminated.

3. **Thread-attributed arena diff.** Correlate arena base addresses with thread
   stack addresses to identify WHICH thread owns the growing arena. That would
   confirm or eliminate `QXcbEventQueue` without a profiler.

Until one of these runs, the honest position is: **8.2% of the observed leak is
attributed with reproducible evidence; 91.8% is localized to a per-thread glibc
arena but not attributed to a call site.**

---

# CORRECTION 6 — the headline number had no committed backing either

The gate review caught two "(measured)" figures with no reproducible artifact.
Auditing the rest of this document for the same flaw found a worse instance:
**the 973.21 B/call `fitInView` figure — the single most important number here —
was also produced by an ad-hoc probe under `.claude/`, which is gitignored.**

It is now backed by `tests/perf/test_fitinview_isolation.py`, which appends to
`tests/perf/leak_measurements.txt`. Re-run from the committed test:

| Operation | Steady rate | At 1 call / 3 s | Shape |
|---|---:|---:|---|
| `itemsBoundingRect()` only | 0.00 B/call | 0.0 MB/day | PLATEAU (clean) |
| `setTransform(current)` only | 0.00 B/call | 0.0 MB/day | PLATEAU (clean) |
| `fitInView(FIXED rect)` | **973.21 B/call** | **26.7 MB/day** | LINEAR (leak) |
| `fitInView(itemsBoundingRect())` — as shipped | **811.01 B/call** | **22.3 MB/day** | LINEAR (leak) |

The originally-reported 973.21 B/call reproduced to the hundredth. The
attribution is unchanged and now independently re-runnable: the leak is in
`fitInView` itself, not in computing the bounds it is given, and not in
applying a view transform generally.

Taking the as-shipped figure (811.01 B/call, 22.3 MB/day) rather than the
isolated one, the confirmed share of the observed ~330 MB/day is **6.8%**, not
8.2%. The accounting section above is conservative in the wrong direction by
that margin; the open gap is correspondingly slightly larger.

The test also fails LOUDLY if a future Qt fixes the upstream defect
(`assert rate > 100.0` with a message pointing at the guard and this document),
so the workaround does not silently outlive its cause.

## Pattern, stated plainly

Four separate numbers in this investigation were quoted as measured while
resting on gitignored ad-hoc probes, and three separate measurement rigs
produced wrong answers because they did not reproduce the app's real call shape.
Every one was caught by either an exercise-guard assertion or an adversarial
gate review — none by re-reading the code. The controls are doing the work here,
not the intuitions.

---

# CORRECTION 7 — stale `_redraw_multi` figure reconciled

A gate review flagged that the "Further exonerations (measured)" table quoted
**865 B/redraw (23.8 MB/day)** for `_redraw_multi` from an uncommitted probe,
while the committed artifact reports something different. The committed run
(`tests/perf/test_overlay_and_worker_paths.py`, 2000 iterations) is:

```
--- _redraw_multi (3 visible groups) ---
  py objects/ iter : 0.053
  QT objects/ iter : 0.000        <-- no stranded C++ instances
  native bytes/iter: 411.6        <-- glibc arenas
  projected        : 11.3 MB/day
  VERDICT          : LEAKS
  exercised        : 3 groups, 27 scene items
```

The uncommitted 865 B/redraw figure is withdrawn; **411.6 B/redraw
(11.3 MB/day)** is the reproducible number.

## What the original claim got right, and what it got wrong

RIGHT: the throw-away parentless `QGraphicsScene` at `screen_map.py:2117` does
not strand Qt instances — `QT objects/iter` is 0.000, which was the structural
worry. That exoneration stands.

WRONG: the table implied `_redraw_multi` is clean overall. It is not; it reads
`VERDICT: LEAKS`. The cause is almost certainly the SAME defect already
attributed — `_redraw_multi` calls `fitInView` at `screen_map.py:2149`, just as
`_redraw` does at `:2033`. So it is not an *additional* root cause, but it is an
*additional call site* of the confirmed one.

## Effect on the accounting

Overlay mode is not the default, so the ~330 MB/day observed on PID 377627 is
attributed against the single-group path. But if a user runs in multi-group
overlay mode, `_redraw_multi` contributes its own `fitInView` call per poll,
and the criterion-4 table above does not include it.

The planned fix covers both call sites (`:2033` and `:2149`) via the same
`_fit_view_if_needed` guard, so both are addressed regardless. The accounting
table is left describing the observed single-group case, with this note as the
explicit caveat.

---

# CORRECTION 8 — the confirmed defect is withdrawn. It was a harness artifact.

**`fitInView` does not leak in a running application. This investigation's one
confirmed finding is retracted.**

## What prompted the recheck

Two real `cpsm gui` instances on HEAD contradicted the prediction. The xcb one
grew +24 KB total and then went FLAT from 900s through 1800s (~600 polls). At
811 B/call it should have accumulated ~486 KB and still been climbing.
A twenty-fold discrepancy is not noise; something was wrong with the model.

## The measurement

Same call, 4000 iterations, varying ONLY what happens between calls:

| Between calls | Growth | Rate |
|---|---:|---:|
| nothing (tight loop) | 3,641,344 B | **910.3 B/call** |
| `sleep(1ms)` every 50 | 135,168 B | 33.8 B/call |
| `processEvents()` every 50 | **0 B** | **0.0 B/call** |
| `sendPostedEvents(DeferredDelete)` every 50 | 139,264 B | 34.8 B/call |
| `processEvents()` every call | **0 B** | **0.0 B/call** |

Eighty milliseconds of total yielding across 4000 calls collapses 3.6 MB to
zero. `malloc_trim` does NOT reclaim the tight-loop growth, so within that loop
the memory is genuinely retained — it simply never accumulates once the process
yields at all.

## Why this invalidates the finding

A real application spins its event loop continuously, and CPSM polls every
**3 seconds** — an eternity in these terms, with the loop idle throughout. The
condition that produces the growth (thousands of consecutive calls with zero
yielding) cannot occur in production.

The tight-loop rate was never a leak rate. It was a measure of how fast
`fitInView` can outrun whatever reclaims after it when nothing is allowed to
run in between.

Corroborated independently by the real instances: xcb +24 KB over 30 minutes
then flat; offscreen +16 KB over 10 minutes. Both are noise.

## Consequences

* **Confirmed leaking call paths on HEAD: ZERO.** The attribution table
  claiming 6.8-8.2% is withdrawn in full. Nothing is attributed.
* The planned `_fit_view_if_needed` guard is **withdrawn**. It would have
  defended against a non-problem, added state to `ScreenMapWidget`, and created
  a real risk of stale rendering after a resize for no benefit.
* Every "LINEAR (leak)" verdict produced by the growth-curve instrument is
  suspect for the same reason: `_curve()` drives its call in a tight loop and
  only calls `processEvents()` inside `_settle()`, at checkpoint boundaries.
  The instrument measures unyielded-loop behaviour, not application behaviour.
* The exonerations are UNAFFECTED and if anything strengthened — a path that
  measured clean under the harshest possible conditions is clean.

## What this leaves

The user's 1.76 GB was real and precisely measured (1,842,480 KB, ~330 MB/day
sustained across six samples). **Nothing in this investigation now explains
it.** Attribution stands at 0%.

The most likely remaining explanations, none tested:

1. Something specific to a real X11 session with sustained rendering and real
   user interaction over days — the `QXcbEventQueue` thread hypothesis, still
   unmeasured.
2. Something driven by real tmux sessions with live ssh/claude panes, which no
   reproduction had. Every harness used synthetic backends.
3. Something in the 2026-08-12 AppImage build not present in a HEAD source
   checkout — bundled Qt plugins, different library versions. `git log -S`
   rules this out for the `fitInView` code path specifically, but not for the
   packaged runtime as a whole.

## The lesson, stated bluntly

The harness's central instrument had a systematic bias that inverted its
headline conclusion, and no amount of internal consistency exposed it. Four
gate reviews passed over it. What caught it was a REAL PROCESS behaving
differently from the model — the plateau at +24 KB.

Synthetic loops measure synthetic behaviour. The reproduction should have been
validated against a real instance BEFORE any finding was reported, not after.

## CORRECTION 8b — the growth also SATURATES

Adding the pacing control to `tests/perf/test_fitinview_isolation.py` produced
a further result that reinforces CORRECTION 8 from a different direction.

Run as the fifth test in that module — after the four preceding tests had
already made roughly 33,000 `fitInView` calls in the same process — a fresh
4,000-call tight loop measured **0.0 B/call**. The same tight loop measures
~910 B/call early in a process's life.

The growth is therefore a large but **bounded, one-time arena expansion**, not
unbounded accumulation. A real leak does not stop merely because enough has
already leaked.

The committed test asserts only the property that holds regardless of process
history — that yielding keeps growth at noise — and records the tight-loop
figure as informational with that caveat attached. Asserting "the tight loop
must grow" would be asserting something order-dependent, which is how this
whole misreading started.

Combined with CORRECTION 8, three independent lines now agree that `fitInView`
does not leak in a running application:

1. Any yielding removes the growth entirely (0.0 B/call).
2. The growth saturates within a process (~33,000 calls in, further calls are free).
3. Two real instances on HEAD plateaued (+24 KB over 30 min; +16 KB over 10 min).

---

# CORRECTION 9 — instrument fixed; the leak disappears entirely

The tight-loop bias identified in CORRECTION 8 was not confined to the
`fitInView` probe. It was in the **core instrument**: both
`leak_harness.measure_leak()` and `test_growth_curve._curve()` drove their call
thousands of times with no yielding, pumping the Qt event loop only at
checkpoint boundaries. Every "LINEAR (leak)" verdict either produced inherited
the bias.

Both now yield to the Qt event loop every 50 calls by default
(`DEFAULT_YIELD_EVERY`, `CPSM_CURVE_YIELD_EVERY`), so a measurement models a
running application rather than a benchmark.

## Result: the leak vanishes

Same path, same 10,000 iterations, same native-heap instrument:

| Instrument | first third | last third | Steady rate | Shape |
|---|---:|---:|---:|---|
| Before (tight loop) | 1460 KB | 2776 KB | 947.54 B/iter | **LINEAR (leak)** |
| **After (yields every 50)** | **0.0 KB** | **0.0 KB** | **0.00 B/iter** | **PLATEAU (clean)** |

Identical with the `QFontMetricsF` probe stubbed: 0.00 B/iter, PLATEAU.

The harness self-validation still passes in both directions after the change —
a deliberate leak reports LEAKS (1.000 Qt objects/iter, `+500 QGraphicsRectItem`)
and a deliberate clean path reports clean — so the instrument has not simply
been blunted into always saying "clean".

## Real-instance confirmation, completed

The 40-minute xcb instance on HEAD finished:

```
+0s    anon=119516 KB
+600s  anon=119520 KB   (+4 KB)
+900s  anon=119540 KB   (+24 KB)
+1200s anon=119540 KB   (+24 KB)
+1500s anon=119540 KB   (+24 KB)
+1800s anon=119540 KB   (+24 KB)
+2100s anon=119540 KB   (+24 KB)
+2400s anon=119540 KB   (+24 KB)   <- 40 min, ~800 polls
```

Growth stopped completely at 900 s and never resumed. Total +24 KB over 40
minutes. At the retracted 811 B/call figure this should have been ~650 KB and
still climbing.

# FINAL POSITION

> **SUPERSEDED.** This section concluded no leak was reproducible. It measured
> the venv, not the shipped AppImage. See "BREAKTHROUGH" and
> "ROOT CAUSE CONFIRMED" below.


**No memory leak is reproducible in CPSM at HEAD.**

* Confirmed leaking call paths: **zero**.
* Every suspect measured under a corrected instrument: PLATEAU / clean.
* Two real instances on HEAD: flat.

The user's 1.76 GB was real, precisely measured (1,842,480 KB; ~330 MB/day
sustained across six independent samples), and remains **entirely
unexplained**. This investigation attributes **0%** of it.

What survives as durable value:

1. A measurement harness with four instruments, self-validating in both
   directions, that now models application behaviour rather than benchmark
   behaviour.
2. A broad set of genuine exonerations — signal marshalling (queued and
   cross-thread), subprocess churn, dead-pane captures, action-bar churn,
   garbage collection, font metrics, monitor reconciliation, the parentless
   overlay scene. These were measured under the HARSHEST conditions and are
   therefore stronger, not weaker, after the correction.
3. A documented account of how a plausible, internally-consistent, four-times-
   gate-reviewed finding can still be an artifact of its own measurement rig.

What would actually find the leak, none of it doable from here:

1. Catch it in situ: attach a heap profiler (heaptrack / jemalloc prof) to a
   real long-running CPSM showing the growth. Nothing synthetic substitutes.
2. Reproduce the real workload: live tmux sessions with real ssh/claude panes
   over days. Every harness here used synthetic backends.
3. Test the packaged runtime, not a source checkout — the affected process was
   the 2026-08-12 AppImage with its own bundled Qt.

## Full suite re-measured under the corrected instrument

31 tests pass. Latest verdict per path, all yielding every 50 calls:

| Path | Verdict | native B/iter |
|---|---|---:|
| `ScreenMapWidget.set_layout` | clean | 0.0 |
| `MainWindow._on_status_poll_complete` | clean | 0.0 |
| `ActiveSessionsWidget._on_poll_complete` | clean | 0.0 |
| queued `Signal(list)` poll_complete | clean | 0.0 |
| queued `Signal(object)` state_changed | clean | 0.0 |
| cross-thread `Signal(list)` poll_complete | clean | 0.0 |
| `_redraw_multi` (3 visible groups) | clean | 0.0 |
| growth curve: `set_layout` (6 panes) | **PLATEAU** | — |
| growth curve: `set_layout` (QFontMetricsF stubbed) | **PLATEAU** | — |
| growth curve: `scene.clear()` only | PLATEAU | — |
| growth curve: clear + 24 `QGraphicsRectItem` | PLATEAU | — |
| growth curve: clear + 6 `QGraphicsTextItem` | PLATEAU | — |
| **growth curve: `fitInView()` only** | **PLATEAU** | — |

`fitInView` — the retracted culprit — now plateaus like everything else once the
loop yields. Nothing in the application leaks.

### Two entries that still read LEAKS, and why they are not counterexamples

**`fitInView isolation: fitInView(...)` -> LINEAR.** Deliberate. Those tests
exist to DEMONSTRATE the tight-loop artifact and do not yield; the module's
`TestTightLoopGrowthIsAHarnessArtifact` pins the contrast.

**`set_layout @ {3,6,12,24} panes` -> LEAKS.** These use `measure_leak`'s single
before/after delta rather than a growth curve, and report:

```
 3 panes:    0.0 B/iter
 6 panes:    0.0 B/iter
12 panes: 1525.8 B/iter
24 panes: 1679.4 B/iter
```

Non-monotonic and bimodal between exactly-zero and ~1.5 KB — the glibc
chunk-quantisation signature documented earlier, where a run either straddles an
arena boundary or does not. The growth curve for the same path over 10,000
iterations is PLATEAU. Where a single delta and a growth curve disagree, the
curve is the reliable one; that is the entire reason it was built.

These entries are left in the artifact rather than deleted, because suppressing
inconvenient measurements is how the earlier mistakes happened.

---

# BREAKTHROUGH — the packaged AppImage runtime leaks; the venv source tree does not

At the user's suggestion, the PACKAGED runtime was tested rather than a source
checkout. Every measurement up to this point had run CPSM from the venv. The
process that actually leaked was the AppImage.

First result, same machine, same seeded 3-monitor config, same 3 s poll:

```
AppImage (PyInstaller frozen, bundled Qt 6.11.0), xcb:
  +0s   anon=135172 KB
  +300s anon=136780 KB   delta=+1608 KB   rate=452.2 MB/day
```

against, on the same machine and workload:

```
venv source, xcb      : +24 KB over 2400 s  (0.8 MB/day)  -- flat from 900s
venv source, offscreen: +24 KB over 1200 s  (1.7 MB/day)
```

That is a difference of two to three orders of magnitude, and 452 MB/day is the
same order as the 330 MB/day measured on the user's PID 377627.

## Why this was missed for the entire investigation

Every harness, probe and reproduction ran `./.venv/bin/python`. The source tree
and the packaged application are materially different runtimes even at an
identical Qt version (6.11.0 in both, verified from the bundled
`libQt6Core.so.6`): PyInstaller freezes imports, bundles its own copies of
shared libraries, and changes the link and preload set. None of that is
exercised by a venv run.

The "no leak reproduces at HEAD" conclusion in CORRECTION 9 was correct **for
the venv**, and wrong as a statement about CPSM as shipped.

## Status: PRELIMINARY, deliberately not yet a finding

One 300-second sample is not a rate. Startup allocation is front-loaded, and a
spot check ~40 s after that sample showed +36 KB (~78 MB/day), well below the
452 MB/day the first interval implies. The steady-state rate must come from
later intervals, not the first.

This is recorded now because it is the strongest lead in the investigation, and
explicitly marked preliminary so it is not quoted as established. The full
40-minute run is in progress.

---

# CONFIRMED — the leak is in the PACKAGED AppImage runtime

Steady-state rates, measured over identical 180-second intervals with the same
read-only `pmap` method, on the same machine, same seeded 3-monitor / 6-connection
config, same 3-second poll interval, same source revision, same Qt 6.11.0:

| Runtime | delta over 180 s | Rate | Per 3 s poll |
|---|---:|---:|---:|
| **AppImage (PyInstaller frozen)** | **+928 KB** | **435.0 MB/day** | **15,838 B** |
| venv source tree | +0 KB | **0.0 MB/day** | 0 B |

The AppImage measurement was taken ~10 minutes after launch, well clear of
startup allocation, so this is a sustained rate and not a front-loaded artifact.

## This matches the reported incident

| | Reported (PID 377627) | AppImage reproduction |
|---|---:|---:|
| Rate | ~330 MB/day | 435.0 MB/day |
| Per 3 s poll | ~12.1 KB | 15.5 KB |

Same order, same mechanism, and the reproduction runs a slightly heavier layout
(6 connections across 3 monitors), which plausibly accounts for the difference.
The reproduction process even has the same form as the reported one —
`/tmp/.mount_CPSM-*/usr/bin/cpsm gui`.

## The X11 backend is NOT the cause

Controlled for: the venv was also run under real xcb for 40 minutes and stayed
flat (+24 KB total, 0.8 MB/day, no growth at all after 900 s). So the difference
is the packaging, not the display platform.

| Build | Platform | Rate |
|---|---|---:|
| venv source | offscreen | 0.0 MB/day |
| venv source | **xcb** | 0.8 MB/day |
| **AppImage** | **xcb** | **435.0 MB/day** |

## What this means for everything above

Every harness, probe, reproduction and gate in this investigation ran
`./.venv/bin/python`. All of it measured a runtime that does not have the bug.
The exonerations remain valid statements about the SOURCE, and the CORRECTION 9
conclusion ("no leak reproduces at HEAD") was true of the venv and false of CPSM
as shipped. That distinction was never drawn until the user suggested testing
the package.

The `fitInView` retraction stands independently — it was withdrawn on evidence
(yielding removes it, it saturates, real instances plateau), not because of this.

## Still to determine

WHAT in the frozen runtime leaks. Both bundle Qt 6.11.0, verified from
`libQt6Core.so.6`, so it is not a Qt version difference. Candidates: bundled
copies of other shared libraries, the PyInstaller bootloader's import machinery,
or an allocator/link-order difference in the frozen environment.

## 2x2: packaging is the sole factor, the display platform is irrelevant

Same machine, same config, same source, same Qt 6.11.0. Steady-state rates over
identical 180 s intervals:

| Build | offscreen | xcb |
|---|---:|---:|
| venv source tree | **0.0 MB/day** | **0.8 MB/day** |
| **AppImage (frozen)** | **427.5 MB/day** | **435.0 MB/day** |

The AppImage leaks at essentially the same rate with no display server at all
(427.5 vs 435.0 MB/day), so this is not an X11/xcb effect, not a rendering
effect, and not related to the `QXcbEventQueue` thread hypothesized earlier.
That hypothesis is dead.

It is the packaging. A frozen PyInstaller build of the same source leaks
~15.5 KB per 3-second poll; the same source run from the venv leaks nothing
measurable.

## Narrowed further: it is the AppImage BUNDLE, not PyInstaller freezing

`dist/cpsm/cpsm` is the same PyInstaller-frozen binary the AppImage wraps, run
directly against SYSTEM shared libraries instead of the bundled ones.

| Build | What differs | Rate |
|---|---|---:|
| venv source tree | interpreted, system libs | **0.0 MB/day** |
| **PyInstaller frozen (`dist/cpsm/cpsm`)** | frozen, **system libs** | **0.0 MB/day** |
| **AppImage** | frozen, **112 bundled libs** + AppRun env | **437 MB/day** |

(AppImage sustained over 900 s: 452.2 -> 442.1 -> 436.9 MB/day, converging ~437.)

Freezing is exonerated. The same frozen executable leaks nothing when it loads
the system's shared libraries and ~437 MB/day when it loads appimage-builder's
bundled copies.

The remaining difference is the bundle: `libfontconfig.so.1`,
`libfreetype.so.6`, `libharfbuzz.so.0`, `libglib-2.0.so.0`,
`libdbus-1.so.3.19.13`, `libicuuc.so.73`, `libpng16.so.16`,
`libz.so.1.2.11` and ~104 others, plus the AppRun environment
(`LD_LIBRARY_PATH`, font/XDG paths).

This also finally explains the shape of the original evidence: the growth was in
a glibc **non-main arena**, i.e. a worker thread's allocations. A bundled
library behaving differently from its system counterpart on a worker thread fits
that exactly.

---

# ROOT CAUSE — the AppImage bundles glibc 2.35; the system has 2.40

> **RETRACTED — see CORRECTION 11.** The glibc rebuild was measured and changed
> nothing (437 -> 417.7 MB/day). The real cause is `libapprun_hooks.so`.


```
bundled  (squashfs-root/runtime/compat/lib/x86_64-linux-gnu/libc.so.6):
    GNU C Library (Ubuntu GLIBC 2.35-0ubuntu3) stable release version 2.35
system   (ldd --version):
    ldd (Ubuntu GLIBC 2.40-1ubuntu3.1) 2.40
```

`AppRun.env` sets `APPDIR_LIBC_VERSION=2.35` and `APPDIR_LIBC_LIBRARY_PATH=$APPDIR/runtime/compat/...`,
so the AppImage runs the application against **glibc 2.35's allocator**, five
minor releases behind the system's 2.40.

This is consistent with every observation:

* The growth was localized to a glibc **non-main (per-thread) arena**, with its
  `PROT_NONE` guard advancing in lockstep — a malloc-internal behaviour.
* It appears ONLY under the AppImage.
* The identical PyInstaller-frozen binary (`dist/cpsm/cpsm`) is clean at
  0.0 MB/day when it loads the SYSTEM glibc.
* It is unaffected by the display platform.

## The elimination chain

| Build | Freezing | Qt | glibc | Rate |
|---|---|---|---|---:|
| venv source | no | 6.11.0 (PySide6 wheel) | system 2.40 | 0.0 MB/day |
| `dist/cpsm/cpsm` | **yes** | 6.11.0 (bundled by PyInstaller) | system 2.40 | **0.0 MB/day** |
| AppImage | yes | 6.11.0 (same) | **bundled 2.35** | **437 MB/day** |

Freezing: exonerated (row 2). Qt version: identical across all three, and
row 2 already bundles Qt. The variable that tracks the leak is the **glibc**.

Whether glibc 2.35 has a malloc defect here, or simply does not reclaim
per-thread arena memory that 2.40 does, the operational conclusion is the same:
CPSM as shipped runs on an allocator that grows without bound under its 3-second
poll workload, and the same code on the system allocator does not.

## CORRECTION 10 — the "AppImage offscreen" row was mislabelled

`AppRun.env` line 15 sets `QT_QPA_PLATFORM=xcb` unconditionally. The run
launched with `QT_QPA_PLATFORM=offscreen` was therefore almost certainly still
running xcb, which is why it measured 427.5 MB/day — indistinguishable from the
explicit xcb run's 435.0.

The 2x2 table's "AppImage / offscreen" cell is withdrawn: that configuration was
never actually tested.

The CONCLUSION it supported is unaffected, because the platform control lives on
the venv side, which WAS genuinely tested both ways:
venv offscreen 0.0 MB/day, venv xcb 0.8 MB/day. The display platform does not
cause the leak; the packaging does.

## Practical consequences

* **Immediate workaround:** `dist/cpsm/cpsm` (the PyInstaller one-folder build)
  does not leak. Running that instead of the AppImage avoids the problem today.
* **Fix:** rebuild the AppImage against a newer glibc base, or configure
  appimage-builder not to bundle `runtime/compat` glibc so the system allocator
  is used. `packaging/AppImageBuilder.yml` is where that is decided.
* **Mitigation if the bundle must stay:** periodically call `malloc_trim(0)`, or
  set `M_ARENA_MAX=1`/`MALLOC_ARENA_MAX=1` so per-thread arenas are not created.
  Both are untested here and would need measuring before being relied on.

## MALLOC_ARENA_MAX=1 is only a PARTIAL mitigation — measured, not assumed

| Configuration | Rate |
|---|---:|
| AppImage, unmitigated | ~437 MB/day |
| **AppImage, `MALLOC_ARENA_MAX=1`** | **249.1 MB/day** (+1240 KB over 420 s) |
| `dist/cpsm/cpsm` (system glibc 2.40) | 0.0 MB/day |

Capping arenas removes roughly 43% and leaves a substantial leak. So the problem
is not merely per-thread arena proliferation — the main arena grows under the
bundled glibc 2.35 too. **Do not ship `MALLOC_ARENA_MAX=1` as the fix.**

This is why it was measured before being recommended; it looked like an obvious
one-line mitigation and it is not one.

## Where the bundled glibc comes from

`packaging/AppImageBuilder.yml`:

```yaml
  apt:
    sources:
      - sourceline: deb [arch=amd64] http://archive.ubuntu.com/ubuntu jammy main restricted universe
```

**jammy = Ubuntu 22.04 = glibc 2.35.** appimage-builder resolves the runtime
compat libc from that apt base, which is what populates
`runtime/compat/lib/x86_64-linux-gnu/libc.so.6` and sets
`APPDIR_LIBC_VERSION=2.35` in the generated `AppRun.env`.

Build host for reference: Ubuntu 24.10 (oracular), glibc 2.40.

---

# CORRECTION 11 — the glibc rebuild did NOT fix it. Root cause was wrong.

The AppImage was rebuilt against the noble (Ubuntu 24.04) apt base, raising the
bundled glibc from 2.35 to 2.39. Verified in the artifact:

```
runtime/compat libc : Ubuntu GLIBC 2.39-0ubuntu8
AppRun.env          : APPDIR_LIBC_VERSION=2.39
bundled Qt          : 6.11.0   (unchanged — variable cleanly isolated)
```

Measured steady-state, same method, same seeded config, same 3 s poll:

| Build | Bundled glibc | Rate |
|---|---|---:|
| jammy | 2.35 | 437 MB/day |
| **noble** | **2.39** | **417.7 MB/day** |
| `dist/cpsm/cpsm` | none (system 2.40) | 0.0 MB/day |

**A 4% change. The leak is not caused by the bundled glibc version.**

The "ROOT CAUSE — the AppImage bundles glibc 2.35" section above is therefore
**wrong** and is retracted as a causal claim. What remains true from it: the
AppImage does bundle its own glibc, and the growth does live in a glibc arena.
But arena growth is where allocator memory *appears*; it does not identify what
requests it. Correlating the bundled-glibc difference with the leak was
reasoning from a coincidence of two facts about the same subsystem.

## Why this was caught

Only because the rebuild was measured instead of declared. The change was
plausible, the version gap was real, the mechanism fit the arena evidence, and
it was still wrong. That is now the fourth hypothesis in this investigation to
survive review and die on measurement.

## What the elimination chain actually establishes

| Build | Frozen | Qt | glibc | Other libs | Rate |
|---|---|---|---|---|---:|
| venv source | no | 6.11.0 | system 2.40 | system | 0.0 |
| `dist/cpsm/cpsm` | yes | 6.11.0 | system 2.40 | system | 0.0 |
| AppImage jammy | yes | 6.11.0 | bundled 2.35 | bundled | 437 |
| AppImage noble | yes | 6.11.0 | bundled 2.39 | bundled | 417.7 |

Freezing: exonerated. Qt version: constant throughout. Bundled glibc VERSION:
now exonerated (2.35 vs 2.39 makes no difference).

Remaining differences between `dist/` (clean) and the AppImage (leaking):

1. The ~112 OTHER bundled libraries — fontconfig, freetype, harfbuzz, glib,
   dbus, ICU, and the rest.
2. The AppRun environment — `LD_LIBRARY_PATH` ordering, `XDG_DATA_DIRS`,
   `GIO_MODULE_DIR`, `GSETTINGS_SCHEMA_DIR`, `GTK_*`, and a read-only
   squashfs prefix (which can defeat font/GIO cache writes, forcing repeated
   rescans).
3. The squashfs/FUSE mount itself.

The recipe change to noble is being KEPT regardless — a 2029-supported base is
better than an EOL one — but it must not be described as the leak fix, because
it is not.

## Why the glibc bump could never have worked

`readelf -p .interp` on the two binaries:

```
AppImage : lib64/ld-linux-x86-64.so.2      <- RELATIVE (leading slash stripped)
dist     : /lib64/ld-linux-x86-64.so.2     <- absolute, system loader
```

appimage-builder rewrites the ELF interpreter to a path relative to the AppDir
so the bundled loader is used, and ships two runtimes: `runtime/default/`
(host libc) and `runtime/compat/` (bundled libc). AppRun selects between them by
comparing the host glibc against `APPDIR_LIBC_VERSION`.

The host here is glibc **2.40**, newer than both the jammy bundle (2.35) and the
noble bundle (2.39). If AppRun selects `default` whenever the host is newer —
which is its documented purpose — then **both builds were already running on the
host's glibc 2.40**, the same allocator `dist/cpsm/cpsm` uses without leaking.

That makes the glibc bump a no-op by construction, and the measured
437 -> 417.7 MB/day is exactly what a no-op looks like. The hypothesis was not
merely unsupported; it was untestable by that route, and a single `readelf`
would have shown that BEFORE the rebuild rather than after.

The remaining difference between clean `dist/` and the leaking AppImage is
therefore the **~112 bundled system libraries** — the AppImage ships jammy/noble
copies of fontconfig, freetype, harfbuzz, glib, dbus and ICU, while `dist/`
resolves those from the host.

Next test: `LD_PRELOAD` the SYSTEM font stack (freetype, fontconfig, harfbuzz)
over the bundled copies. CPSM builds `QFont` objects on every canvas redraw —
once per 3 s poll — so the font stack is the highest-prior suspect.

## Font stack exonerated

`LD_PRELOAD` of the host's `libfreetype.so.6`, `libfontconfig.so.1` and
`libharfbuzz.so.0` over the bundled copies:

| Configuration | Rate |
|---|---:|
| AppImage, bundled font stack | ~437 MB/day |
| **AppImage + host font stack preloaded** | **409.2 MB/day** |

No meaningful change. The font libraries are not responsible, despite CPSM
constructing `QFont` objects on every redraw.

## A better suspect, found by looking instead of guessing

`pmap` on the live AppImage process lists what is ACTUALLY mapped, rather than
what the bundle contains. Among the loaded libraries:

```
libapprun_hooks.so
```

That is appimage-builder's own shim. It intercepts `exec*`/`fork` to rewrite the
environment for child processes so they do not inherit the AppDir's
`LD_LIBRARY_PATH`.

CPSM's `StatusPoller` calls `list_panes()` and `list_sessions()` on **every
3-second poll**, each spawning a `tmux` subprocess through `ProcessRunner`
(`cpsm/platform/tmux_backend.py:212,220`). That is two intercepted `exec()`
calls per poll, forever, on a worker thread.

Arithmetic: observed ~15.2 KB/poll / 2 execs = ~7.6 KB per interception.

This hypothesis fits every constraint the evidence imposes, which none of the
earlier ones did:

| Evidence | Fits? |
|---|---|
| AppImage-only (venv and `dist/` spawn the same tmux and are clean) | yes — only the AppImage has apprun hooks |
| Per-poll, unbounded | yes — one interception per subprocess, forever |
| Growth in a **non-main** glibc arena | yes — spawning happens on the StatusPoller THREAD |
| Independent of Qt version | yes |
| Independent of bundled glibc version (2.35 vs 2.39) | yes |
| Independent of display platform | yes |
| Independent of the font stack | yes |

**Not yet a finding.** Four hypotheses in this investigation have survived
review and died on measurement. The discriminating test is running: shadow
`tmux` with a failing stub so no subprocess is ever spawned, while the poll
loop, signal emissions and canvas rebuild continue unchanged
(`StatusPoller.run()` swallows the backend exception via
`except Exception: panes = []`).

  * leak stops    -> exec interception confirmed
  * leak persists -> this hypothesis dies too

## Exec interception exonerated — and the poll-driven assumption is now suspect

`tmux` was shadowed with a failing stub so `TmuxBackend.list_panes()` raises and
`StatusPoller.run()` swallows it (`except Exception: panes = []`). No subprocess
is spawned at all, while the poll loop, signal emissions and canvas rebuild
continue unchanged.

| Configuration | Rate |
|---|---:|
| AppImage, tmux available (2 execs/poll) | 417.7 MB/day |
| **AppImage, tmux unavailable (0 execs)** | **407.8 MB/day** |

Essentially unchanged. `libapprun_hooks.so`'s exec interception is NOT the
cause, despite fitting every constraint on paper. Fifth hypothesis killed by
measurement.

### The assumption nobody tested

Every hypothesis in this investigation — mine and the gate agents' — assumed the
leak scales with the 3-second status poll, because the original evidence was
~12 KB "per poll". But that figure was only ever a RATE DIVIDED BY THE POLL
INTERVAL. It was never established that the poll causes it.

Five poll-path hypotheses have now died: `fitInView`, reconcile, signal
marshalling, dead-pane captures, and exec interception. The subprocess test is
the sharpest of them — removing the per-poll subprocess entirely changed
nothing.

CPSM exposes `status_poll_interval_ms`, so the assumption is directly testable.
Running at 30000 ms (10x slower):

  * rate drops ~10x -> genuinely poll-driven; bisect further inside the poll
  * rate unchanged  -> NOT poll-driven; the poll loop is a red herring and
                       something on its own clock is responsible

This should have been the FIRST experiment, not the twelfth. Anchoring on
"per poll" framed every subsequent hypothesis and cost most of this
investigation.

---

# CORRECTION 12 — the leak is NOT poll-driven. The entire framing was wrong.

`status_poll_interval_ms` raised from 3000 to 30000 (10x slower polling), same
AppImage, same config, same method:

| Poll interval | Polls/day | Rate |
|---|---:|---:|
| 3000 ms | 28,800 | 417.7 MB/day |
| **30000 ms** | **2,880** | **460.1 MB/day** |

Ten times fewer polls. The rate did not drop — it rose slightly, i.e. unchanged
within run-to-run variance.

**The status poll does not cause the leak.** Something running on its own clock
does.

## What this invalidates

The original figure was "~12 KB per poll". That number was only ever
`observed rate / poll interval` — an arithmetic conversion, never a measured
per-poll cost. Every hypothesis in this investigation, mine and the gate agents',
inherited that framing and searched inside the poll:

  fitInView, reconcile, signal marshalling, dead-pane captures,
  action-bar churn, exec interception, GC deferral

All were eliminated, and eliminating them was never going to find anything,
because the poll was never the driver. The single cheapest experiment —
change the interval, watch the rate — would have shown that at the start and
saved essentially the whole investigation.

## A gap this exposes

`AppRun.env` line 15 sets `QT_QPA_PLATFORM=xcb` UNCONDITIONALLY. The run
labelled "AppImage offscreen" earlier was therefore still xcb (already noted as
CORRECTION 10). Consequence: **the AppImage has never actually been run without
X11.** `QXcbEventQueue` was declared "dead" as a hypothesis on the strength of
that mislabelled run. That declaration was unfounded and is withdrawn.

Threads in a leaking instance:

```
cpsm (main)  StatusPoller  QXcbEventQueue  QDBusConnection
```

`StatusPoller` is now excluded by the interval test. The remaining
self-clocked candidates are `QXcbEventQueue` (bundled xcb/X11 stack) and
`QDBusConnection` (bundled libdbus/glib).

Next: run the AppImage genuinely headless by editing the extracted AppDir's
`AppRun.env`, which is the only way to defeat the forced xcb.

## Headless AppImage still leaks — X11 and DBus exonerated

The extracted AppDir's `AppRun.env` was patched to `QT_QPA_PLATFORM=offscreen`
and run via `./squashfs-root/AppRun`, which is the only way to defeat the forced
xcb. Thread list confirms it took effect:

```
before (xcb) : cpsm  StatusPoller  QXcbEventQueue  QDBusConnection
after (real
   offscreen): cpsm  StatusPoller  5x Thread (pooled)
```

Both `QXcbEventQueue` and `QDBusConnection` are gone — and it still leaks
(+1032 KB in ~4 min, ~360 MB/day, against a much smaller ~92.7 MB baseline).

The bundled X11/xcb stack and the bundled libdbus/glib are therefore both
exonerated.

## The candidate that explains the interval result

`StatusPoller._interruptible_sleep` (cpsm/workers/status_poller.py:405):

```python
chunk = 0.05  # 50 ms granularity
while elapsed < duration_s:
    if self._stop.loadRelaxed():   # acquires a threading.Lock
        return
    time.sleep(min(chunk, duration_s - elapsed))
```

The wake-up rate is **20 per second, constant, regardless of
`status_poll_interval_ms`**. Polling every 30 s instead of every 3 s changes the
number of POLLS tenfold but leaves the number of WAKE-UPS identical.

That is precisely the observed behaviour: 3 s -> 417.7 MB/day,
30 s -> 460.1 MB/day.

Arithmetic: 20/s x 86400 = 1,728,000 wake-ups/day; 460 MB/day works out to
~279 bytes per wake-up. Each wake-up does a `threading.Lock` acquire/release
and a `time.sleep()`.

The venv runs the identical loop and does not leak, so this would still be an
AppImage-runtime interaction rather than a defect in the code itself — but it
is the first candidate whose CLOCK matches the measured invariance.

Discriminating test: raise `chunk` from 0.05 to 0.5 (10x fewer wake-ups, same
poll cadence) and rebuild.

  * rate drops ~10x -> confirmed: the leak scales with wake-ups
  * rate unchanged  -> this dies too

## Wake-up rate exonerated — and the rate is invariant to EVERYTHING

`_interruptible_sleep`'s `chunk` was raised 0.05 -> 0.5 (10x fewer wake-ups,
same poll cadence), dist and AppImage rebuilt, measured identically:

| Wake-ups/sec | Rate |
|---:|---:|
| 20 (chunk 0.05) | 417.7 MB/day |
| **2 (chunk 0.5)** | **414.8 MB/day** |

Unchanged. Sixth hypothesis dead. (The experimental patch has been reverted;
`chunk` is back to 0.05.)

### The pattern that should have been noticed sooner

Every measured configuration lands in the same narrow band:

```
407.8  no subprocesses at all      427.5  "offscreen" (actually xcb)
409.2  host font stack preloaded   435.0  xcb
414.8  10x fewer wake-ups          442.1  noble glibc 2.39
417.7  3s poll                     452.2  jammy glibc 2.35
                                   460.1  30s poll (10x fewer polls)
```

Turning off subprocess spawning, the font stack, X11, DBus, 90% of the polls and
90% of the wake-ups moves the number by less than run-to-run variance. **The
leak is not driven by what the application does.**

### Where the memory goes — same signature as the original incident

Per-mapping diff of a leaking AppImage instance over 120 s:

```
DELTA: +588 KB over 120s  =>  413.4 MB/day
      +588 KB  addr=775020000000  size=2992 KB  perm=rw-p  [anon]
  NEW    0 KB  addr=7750202ec000  size=62544 KB perm=---p
 GONE    0 KB  addr=775020257000  size=63140 KB perm=---p
mapping count unchanged: 794
```

One anonymous mapping grows; its `PROT_NONE` guard advances by exactly the same
amount. A glibc **non-main (per-thread) arena** — identical to what was measured
on PID 377627 at the very start.

Also exonerated by the genuinely-headless AppDir run (which leaked ~360 MB/day
with no X11, no DBus, and no FUSE mount): the squashfs/FUSE layer.

Next: run with a valid but EMPTY document — no connections, groups or layouts,
so the poller polls nothing and the canvas renders nothing. If it still leaks,
the leak is in the AppImage RUNTIME and CPSM's own code is not involved at all.

---

# CPSM'S CODE IS NOT INVOLVED

A valid but entirely EMPTY document — no connections, no groups, no layouts —
so the poller polls nothing, the canvas renders nothing, no status dots update:

| Document | Rate |
|---|---:|
| 6 connections, 1 group, 3-monitor layout | 417.7 MB/day |
| **EMPTY (nothing to do)** | **406.4 MB/day** |

Unchanged. **The leak does not depend on CPSM doing any work at all.**

Combined with everything else measured, the conclusion is forced:

| Variable | Result |
|---|---|
| venv source | 0.0 MB/day |
| PyInstaller frozen, system libs (`dist/cpsm/cpsm`) | 0.0 MB/day |
| AppImage, any configuration | 406-460 MB/day |

The leak belongs to the **AppImage runtime**. It is not a defect in CPSM's
application code, and no change to `cpsm/` will fix it.

## Complete elimination table

| Hypothesis | Verdict | Evidence |
|---|---|---|
| `fitInView` per poll | RETRACTED (mine) | yielding removes it; saturates; real instances plateau |
| reconcile loop / config writes | RETRACTED (mine) | 1 emit / 501 polls on the real feedback path |
| GC deferral | exonerated | 3 regimes; +1 live object over 4000 rebuilds |
| signal marshalling | exonerated | queued and cross-thread both flat |
| dead-pane `capture_pane` | exonerated | 4364 captures, 0 B growth |
| action-bar / `setIndexWidget` churn | exonerated | 0.0 B/iter |
| font stack | exonerated | host freetype/fontconfig/harfbuzz preloaded: 409.2 |
| subprocess exec interception | exonerated | tmux removed entirely: 407.8 |
| X11 / `QXcbEventQueue` | exonerated | genuinely headless: still leaks, thread absent |
| DBus / `QDBusConnection` | exonerated | same headless run, thread absent |
| squashfs / FUSE | exonerated | extracted AppDir leaks too |
| bundled Qt version | exonerated | 6.11.0 in every build |
| bundled glibc version | exonerated | 2.35 -> 2.39 rebuild changed nothing |
| PyInstaller freezing | exonerated | `dist/` is the same frozen binary, clean |
| poll interval | exonerated | 10x fewer polls: 460.1 |
| wake-up rate | exonerated | 10x fewer wake-ups: 414.8 |
| **application workload** | **exonerated** | **empty document: 406.4** |
| `MALLOC_ARENA_MAX=1` | partial mitigation | 249.1 (43% reduction, not a fix) |

## What to do about it

**Workaround, available now and measured:** run `dist/cpsm/cpsm` — the
PyInstaller one-folder build — instead of the AppImage. 0.0 MB/day.

**Fix:** the defect is in appimage-builder's runtime (AppRun + its preloaded
`libapprun_hooks.so` + bundled loader), not in CPSM. Options, in order of
confidence:

1. Package with different tooling — `linuxdeploy` or plain `appimagetool`,
   neither of which uses appimage-builder's AppRun/hook machinery. Neither is
   currently installed here.
2. Ship the one-folder `dist/` tree directly (tarball or distro package).
3. Report upstream to appimage-builder with this reproduction.

The `packaging/AppImageBuilder.yml` change to the noble base is retained, but on
its own merits (jammy is old; noble is supported to 2029) — **not** as the fix.
(Both the `.bak` recipe and the old `.jammy-leaking` binary were build
scratch and have since been removed; `*.AppImage` is gitignored, so the leaking
binary was never in the repo. Recover the old recipe from git history if needed.)

---

# ROOT CAUSE CONFIRMED — appimage-builder's `libapprun_hooks.so`

`AppRun` `LD_PRELOAD`s `libapprun_hooks.so`, appimage-builder's shim that
intercepts `open`/`openat`/`dlopen`/`exec*` to rewrite AppDir-relative paths.
Replacing it with an inert stub `.so` (so the preload still resolves but the
hooks do nothing), same extracted AppDir, same headless config, same method:

| Configuration | delta / 240 s | Rate |
|---|---:|---:|
| hooks intact (headless) | ~+1000 KB | ~360 MB/day |
| **hooks STUBBED** | **+0 KB** | **0.0 MB/day** |

Not reduced — **zero**. The anonymous footprint did not move by a single
kilobyte over the whole measurement window.

An earlier test had ruled out only the hooks' `exec*` path, by removing the
subprocesses CPSM spawns. That was too narrow: the library also intercepts file
operations, which happen continuously regardless of what the application does —
which is exactly why the rate was invariant to poll interval, wake-up rate,
workload, X11, DBus and everything else.

## Why every earlier hypothesis failed

The leak is in the *launcher shim*, below the application entirely. That
explains, in one stroke, every otherwise-puzzling result:

* venv and `dist/cpsm/cpsm` clean — neither is launched via AppRun.
* Rate invariant across poll interval, wake-ups, workload, empty document —
  the hooks do not care what the app does.
* Unaffected by Qt version, glibc version, fonts, X11, DBus, FUSE — none of
  them is the shim.
* Growth in a glibc **non-main arena** — the hooks allocate on whichever
  thread makes the intercepted call.
* Present only in the AppImage, at ~420 MB/day, matching the ~330 MB/day
  observed on the user's PID 377627.

## Fix

The defect is in appimage-builder, not in CPSM. Nothing in `cpsm/` needs to
change.

1. **Package with different tooling** — `linuxdeploy` or plain `appimagetool`.
   Neither uses AppRun's hook machinery. Not currently installed here.
2. **Ship the PyInstaller one-folder tree** (`dist/cpsm/`) as a tarball or
   distro package. Measured at 0.0 MB/day.
3. **Report upstream** to appimage-builder with this reproduction.

**Workaround available immediately:** run `dist/cpsm/cpsm`. Measured clean.

The `packaging/AppImageBuilder.yml` noble base change is retained on its own
merits (jammy is EOL-adjacent; noble is supported to 2029) but is NOT the fix
and was measured not to be — see CORRECTION 11.

## Controlled A/B — same script, same session, same AppDir

Both arms produced by the committed `scripts/diagnose_appimage_leak.sh`
(originally an ad-hoc script under gitignored `.claude/`), differing ONLY in whether
`libapprun_hooks.so` is the real library or an inert stub:

```
AppImage, apprun hooks STUBBED   delta=    +0 KB / 240s ->    0.0 MB/day
AppImage, apprun hooks INTACT    delta= +1176 KB / 240s ->  413.4 MB/day
```

413.4 MB/day to zero, by swapping one 53 KB shared object. This is the
confirmed root cause.

### Caveat on "just stub the hooks"

Stubbing is a DIAGNOSTIC, not a recommended fix. `libapprun_hooks.so` exists to
rewrite `LD_LIBRARY_PATH` and AppDir-relative paths for CHILD processes. CPSM
spawns terminals and tmux; with the hooks inert those children may inherit the
AppDir's library path and misbehave in ways a 4-minute idle measurement would
not reveal. The app started and ran in the stubbed arm, but that is not
evidence that launching works.

Recommended fixes remain, in order:

1. Repackage with `linuxdeploy` or plain `appimagetool` (no hook machinery).
2. Ship the PyInstaller one-folder tree `dist/cpsm/` — measured 0.0 MB/day.
3. Report upstream to appimage-builder with this reproduction.

## Reproducing this from a clean checkout

`scripts/diagnose_appimage_leak.sh` is the committed, tracked reproduction of
the decisive A/B. It extracts the AppDir, forces headless, and runs both arms —
real hook library vs an inert stub — reporting MB/day for each.

```
scripts/diagnose_appimage_leak.sh          # both arms, ~15 min
scripts/diagnose_appimage_leak.sh stub     # stubbed arm only
scripts/diagnose_appimage_leak.sh intact   # intact arm only
```

It aborts with `APP DIED` or `process exited during the window` rather than
reporting a false zero, so a `0.0 MB/day` result cannot be an artifact of the
application failing to start.

An earlier version of this script lived only under `.claude/`, which is
gitignored (`.gitignore:62`) — meaning the single most important measurement in
this document was not reproducible from the repository. A gate review caught it.
That is the SECOND time this exact failure occurred here (see CORRECTION 6,
where the `fitInView` figure had the same problem), which is why the
reproduction now lives in `scripts/`.

### On phase-2 criterion 3's "growth-curve isolation over >=10,000 iterations"

That criterion was written when the leak was believed to be a discrete CPSM call
path, where iteration count is the natural unit. It is not the right instrument
for what was actually found.

`libapprun_hooks.so` acts continuously on file operations beneath the
application. There is no call path for a harness to invoke N times, and the rate
is invariant to poll interval (10x fewer polls: 460.1 MB/day), wake-up rate
(10x fewer: 414.8) and workload (empty document: 406.4). Counting iterations of
something would measure nothing.

Elapsed wall-clock against a live process is the correct instrument, and the
signal it produces — 413.4 MB/day versus exactly 0 KB of growth over a 240 s
window — is orders of magnitude larger than the noise the iteration-based
instrument was built to overcome. The criterion is satisfied in substance:
a leaking component is isolated by a controlled experiment and corroborated by
native-heap measurement (the `pmap` per-mapping diff showing a single glibc
non-main arena growing).

---

# FIX IMPLEMENTED AND VERIFIED — plain appimagetool

`scripts/build_appimage_plain.sh` builds the AppImage with stock `appimagetool`
instead of appimage-builder. The AppDir it assembles has a four-line `AppRun`:

```sh
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
export PATH="$HERE/usr/bin:$PATH"
exec "$HERE/usr/bin/cpsm" "$@"
```

No `LD_PRELOAD`, no `libapprun_hooks.so`, no bundled glibc, no ELF interpreter
rewriting, no environment munging.

## Verified absent

```
hook shim (libapprun_hooks.so) : not present
runtime/compat (bundled glibc) : not present
ELF interpreter                : /lib64/ld-linux-x86-64.so.2   (absolute, system loader)
```

Compare the appimage-builder output, whose interpreter was the RELATIVE
`lib64/ld-linux-x86-64.so.2` pointing at its own bundled loader.

## Measured

Same seeded config (6 connections, 1 group, 3-monitor layout, 3 s poll), same
method, same machine:

| Build | Rate | Per poll |
|---|---:|---:|
| appimage-builder | 413-460 MB/day | ~15.2 KB |
| **plain appimagetool** | **4.2 MB/day** | **154 B** |
| `dist/cpsm/cpsm` (no AppImage at all) | 0.0 MB/day | 0 B |

**~99% reduction.** Over the six days that produced the original 1.76 GB, this
build would accumulate roughly 25 MB.

The residual 4.2 MB/day is close to the floor of this measurement method
(+12 KB over a 240 s window) and may be measurement noise or a settling tail
rather than real growth; a longer run is the way to tell. It is not worth
chasing at ~1/100th of the original rate.

Functional check: `./CPSM-0.1.0-x86_64.AppImage --version` -> `cpsm 0.1.0`, and
the GUI starts and runs the poll loop (the measurement above is of a live GUI
process). That is NOT a full functional test — see the caveat below.

## What this trades away

appimage-builder bundles ~112 system libraries and a compat glibc so its output
runs on older distributions. This build does not. The PyInstaller bundle ships
Python and Qt, but whatever it links from the host — libc, libstdc++, libGL,
libssl — must be present and new enough on the target. In practice the AppImage
now requires a host of roughly the build machine's vintage or newer.

That is the deliberate trade: portability across old distributions, in exchange
for not shipping a memory leak. If wide portability matters more, build on the
OLDEST distribution you intend to support rather than reintroducing the shim.

## Caveat — what has NOT been tested

The measurement exercises the poll loop and canvas of a running GUI. It does not
exercise CPSM's terminal/tmux launching. appimage-builder's hooks existed to fix
up `LD_LIBRARY_PATH` for child processes; without them, a spawned terminal or
tmux inherits whatever this AppRun sets (only `PATH`). That is more likely to be
correct than the shim's rewriting, since children now see a clean environment —
but it is unverified. **Launch a real session from this build before shipping
it.**

`packaging/AppImageBuilder.yml` is retained for reference and for anyone who
needs the old-distro portability, with its noble base change. It should not be
used for releases while the shim leaks.

---

# SECOND DEFECT — removing the shim exposed a pre-existing child-environment bug

The packaging fix above eliminated the leak and, on its own, **broke CPSM's
ability to open a terminal**. This was found by a code-level analysis before
shipping, and independently reproduced by a gate agent.

## The bug

PyInstaller's bootloader sets `LD_LIBRARY_PATH` and `QT_PLUGIN_PATH` to point
at the bundle's `_internal` directory, so the frozen app finds its own Qt,
OpenSSL and so on. Correct for CPSM; wrong for everything CPSM spawns.

No spawn site passed `env=`, so every child inherited it verbatim:

* `cpsm/platform/terminal_launcher.py` — 7 `subprocess.Popen` sites
* `cpsm/platform/process_runner.py` — `env` defaulted to `None` (inherit)
* `cpsm/platform/tmux_backend.py` — never passed `env=`, so tmux and everything
  tmux spawns (launcher scripts, ssh) inherited it too

With system Qt 6.6.2 and a bundle carrying Qt 6.11.0, konsole does not degrade —
it refuses to start:

```
$ LD_LIBRARY_PATH=dist/cpsm/_internal \
  QT_PLUGIN_PATH=dist/cpsm/_internal/PySide6/Qt/plugins konsole --version
konsole: symbol lookup error: /lib/x86_64-linux-gnu/libQt6Multimedia.so.6:
         undefined symbol: _ZN14QObjectPrivateC2Ei, version Qt_6_PRIVATE_API

$ konsole --version
konsole 24.08.1
```

Libraries hijacked out of the bundle: konsole 55, xterm 18, ssh 11 (including
`libcrypto.so.3`), tmux 3, bash 1. konsole is the highest-priority launcher
available on this machine (`terminal_launcher.py:393-400`).

## Why it did not show up before

appimage-builder's `AppRun` shim was scrubbing child environments — that was
part of its job. Direct evidence: this session's shell is a descendant of the
OLD AppImage (`APPDIR=/tmp/.mount_cpsm.A1GHDhv` is in its environment), and in
it `LD_LIBRARY_PATH` is UNSET while a stale `LD_LIBRARY_PATH_ORIG` still holds
the AppDir list.

So the leaking shim was masking a real bug. **The bug is not AppImage-specific**
— the variables come from PyInstaller, so a bare `dist/cpsm/cpsm` run has it
too, and so would a linuxdeploy build. It has presumably been broken in
non-AppImage runs all along.

## The fix

`cpsm/platform/child_env.py` — `child_env()` returns an environment safe to hand
to a system binary:

* restore `<NAME>_ORIG` when PyInstaller saved one (an EMPTY saved value means
  the variable was unset originally, and must end up unset — setting it to `""`
  would tell the loader to search the current directory);
* otherwise drop the variable if it points into `sys._MEIPASS`;
* otherwise leave it alone, because it is the user's own setting;
* not frozen -> pass through untouched.

Wired into EVERY spawn site in `cpsm/`:

| Module | Sites | Spawns |
|---|---|---|
| `platform/process_runner.py` | default when `env is None` | tmux, ssh via `SshBinary`, launcher scripts |
| `platform/terminal_launcher.py` | 7 `Popen` + 2 `subprocess.run` | terminal emulators, `wmctrl` |
| `platform/desktop_entry.py` | 1 | `update-desktop-database` |
| `services/correlation_service.py` | 1 | `ssh` (links libcrypto) |
| `ui/main_window.py` | 3 | `ssh-keygen` (links libcrypto), key probes |

An earlier version of this section claimed covering `ProcessRunner` and
`terminal_launcher.py` was sufficient. **That was wrong** — the last four rows
call `subprocess.run` directly and bypass `ProcessRunner` entirely. They were
found by auditing the whole package AFTER wiring the first two, and a gate agent
independently found the same gap. `ldd` confirms the risk was real: with the
bundle path set, `ssh` resolves `libcrypto.so.3` and `libk5crypto.so.3` out of
the bundle.

## Verified

```
konsole spawned from a simulated frozen CPSM:
  inheriting os.environ (BUG)   rc=127  symbol lookup error ...
  via child_env() (FIX)         rc=0    konsole 24.08.1
```

`tests/platform/test_child_env.py` (12 tests) covers restoration, the
empty-original case, bundle-path dropping, leaving user values alone,
pass-through when not frozen, non-mutation of the input, that `ProcessRunner`
actually sanitises a real child, and that an explicit `env` is passed through
untouched.

`TestEverySpawnSiteIsSanitised` is the one that matters most: it sweeps the
WHOLE `cpsm/` package and fails if any `subprocess.run`/`Popen` lacks a
sanitised `env`, with a single explicit exemption (`process_runner.py`, which
IS the sanitiser). Without it, the next spawn site added would silently
reintroduce this — which is exactly what happened between the first and second
pass of this fix.

## Note

This is the second time in this investigation that a fix's side effects mattered
more than the fix. The packaging change was correct and measured; it was also
incomplete in a way no leak measurement could have revealed. It took asking
"what did the thing I removed actually do?" — and the answer was "two jobs, and
I only accounted for one".

---

# FINAL VERIFICATION — both fixes, rebuilt artifact

The AppImage measured earlier carried only the packaging fix. Rebuilt with both
(`349472b` adds the child-environment sanitiser) and re-measured, same seeded
config, same method:

| Build | Rate | Per poll |
|---|---:|---:|
| appimage-builder (the original bug) | 413-460 MB/day | ~15.2 KB |
| plain appimagetool, packaging fix only | 2.2-4.2 MB/day | 154 B |
| **plain appimagetool + child-env fix** | **3.4 MB/day** | **123 B** |
| `dist/cpsm/cpsm` (no AppImage at all) | 0.0 MB/day | 0 B |

`+12 KB over 300 s`. Over the six days that produced the reported 1.76 GB, this
build accumulates roughly **20 MB** — a ~99% reduction.

Artifact verified: no `libapprun_hooks.so`, no `runtime/compat` bundled glibc,
`tests/packaging/` 6/6 green against it.

## The two fixes, and why both were needed

1. **`5af2aaf`** — build with stock `appimagetool` instead of appimage-builder.
   Removes `libapprun_hooks.so`, which was the leak.
2. **`349472b`** — sanitise child-process environments at every spawn site (14 outside
   `process_runner.py`, which is itself the sanitiser).
   Necessary *because* of fix 1: the shim was doing two jobs, and removing it
   would otherwise have shipped a konsole-breaking regression.

Fix 2 is independent of packaging. The offending variables come from
PyInstaller, so it repairs `dist/cpsm/cpsm` too — including the workaround
recommended earlier in this investigation, which had the same bug.

## What remains unverified

* **No real session has been launched from the rebuilt AppImage.** The leak
  measurements exercise a live GUI's poll loop and canvas; the konsole
  reproduction is a direct spawn. Neither is "select a connection and get a
  working terminal". That check needs a human at a desktop and should happen
  before shipping.
* **The residual ~3 MB/day is not claimed to be zero.** It is at the floor of
  this measurement method (+12 KB over a 300 s window) and matches the noise
  previously recorded for known-clean processes, but it has not been measured as
  zero and is not asserted as such.
* **Portability of the new build is untested on other distributions.** Dropping
  appimage-builder means dropping its bundled system libraries and compat glibc;
  the AppImage now requires a host of roughly the build machine's vintage. That
  is a deliberate trade, not an oversight — see the note in
  `scripts/build_appimage_plain.sh`.

## The "pre-existing failure" was a non-hermetic test, and is now fixed

Throughout this pipeline, one test was carried as a known pre-existing failure
and excluded from every gate:

    tests/platform/test_desktop_entry.py::TestInstallDesktopEntry::test_executable_fallback_to_python_module

It was genuinely pre-existing — `git log` put the last change to that file well
before this work, and a gate agent proved it by stashing this pipeline's only
change to it and observing the identical failure. But "pre-existing" is not the
same as "unfixable", and carrying it meant phase-4's criterion ("full suite
passes with **zero** failures", without phase-3's "new" qualifier) could not be
met literally. A gate agent correctly FAILed on that wording and deferred the
choice to the planner.

Amending the criterion was the wrong fix. The test is simply not hermetic.

`_resolve_executable` (cpsm/platform/desktop_entry.py:43) consults `$APPIMAGE`
BEFORE falling back to `python -m cpsm`:

```python
found = shutil.which("cpsm")
if found and not _is_transient_appimage_mount(found):
    return found
appimage = os.environ.get("APPIMAGE")
if appimage and Path(appimage).exists():
    return appimage
return f"{sys.executable} -m cpsm"
```

The test monkeypatches `shutil.which` to None and asserts the `python -m cpsm`
fallback — but never clears `$APPIMAGE`. So it passes in CI (where nothing sets
that variable) and fails whenever the suite is run from inside a CPSM AppImage
session, because that runtime sets `APPIMAGE` to a real, existing file and the
fallback is never reached.

This machine is exactly that case: `APPIMAGE=$HOME/.local/opt/cpsm/cpsm.AppImage`,
which exists. Two sibling tests in the same class already `delenv`/`setenv` it
deliberately; this one was simply missed.

Fix: `monkeypatch.delenv("APPIMAGE", raising=False)`.

Result: **2249 passed, 12 skipped, 0 failed** (full suite incl. tests/perf). No exclusions, no deselection, no
criterion amendment.

Worth noting how it surfaced: the same ambient `$APPIMAGE` that broke this test
is the evidence, earlier in this document, that appimage-builder's shim really
was scrubbing child environments. One environmental quirk explained a fixture
bug and confirmed a root cause.

---

# CORRECTION 13 — the child-env fix had the inverse bug in development runs

An independent consistency review of the committed work found a real defect in
`child_env()` itself.

The `<NAME>_ORIG` restore branch ran **regardless of frozen state**. `bundle`
was consulted only in the `elif`. So in a NON-frozen (development) run whose
environment already carried a stale `LD_LIBRARY_PATH_ORIG`, `child_env()` would
*set* `LD_LIBRARY_PATH` from it — handing children a dead AppDir path the parent
did not even have set. The exact inverse of this module's purpose.

That contradicted the module's own docstring ("Not frozen means nothing was
overridden, so the environment passes through untouched") and the same claim in
`349472b`.

Not hypothetical. This machine's shell is a descendant of an old CPSM AppImage,
so it carries a stale `LD_LIBRARY_PATH_ORIG` pointing at a mount that no longer
exists — the very fact recorded earlier in this document as evidence that the
shim was scrubbing child environments. Reproduced directly:

```
dev run, stale _ORIG present -> LD_LIBRARY_PATH: /tmp/.mount_cpsm.DEAD/usr/bin/_internal
BUG CONFIRMED: a dev run INJECTS a dead AppDir path into children
               that the parent did not even have set.
```

Fix: an early return when `bundle_dir()` is None, so a non-frozen run passes the
environment through untouched, matching the documented contract.

Four existing tests failed after the gate — and that was the point: they had
been exercising restoration WITHOUT declaring frozen state, i.e. asserting
behaviour on the development path where it must not happen. They now take an
explicit `frozen` fixture. Two new tests pin the regression from both sides: a
dev run must not resurrect a stale `_ORIG`, and a frozen run must still restore.
14 tests, all passing.

## Why the existing guard did not catch it

`tests/platform/test_child_env.py::test_unfrozen_keeps_environment` explicitly
`delenv`s `LD_LIBRARY_PATH_ORIG` before checking pass-through — so it tested the
non-frozen path with the one variable that triggers the bug removed. A test can
cover a code path and still miss its defect if the fixture clears the input that
would expose it.
