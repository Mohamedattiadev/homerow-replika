# Next: scroll mode picks the wrong region since the jitter fix

Hand-off note. Delete this file once the work is done — it is a task, not
documentation.

## The report

> "the scrolling part still bad — before it detected the right scrollable
> thing, now not. but no glitch"

Both halves are true, and they are the same change. Entering scroll mode no
longer moves the cursor (that was the fix), and it now picks the wrong region
on pages with more than one scroller — `https://devdocs.io/less~4/`, which has
a sidebar and a content pane, is the reported case and a good test.

## What changed, and why it broke this

Commit `a48dbcb`. `scroll._collect()` used to end with:

```python
distinct.sort(key=lambda e: e.w * e.h, reverse=True)
if config.SCROLL_VERIFY:
    distinct = verify(distinct, deadline)          # scroll each, watch, keep movers
```

`verify()` warps the pointer onto every candidate, scrolls it, scrolls it
back, and only restores the cursor when the whole sweep is done — measured
over 151 real sessions in this desktop's log at **316ms median, 726ms p90,
2318ms worst**, all of it before anything was drawn. That is the cursor flying
about on entry. It is now gated behind `config.SCROLL_VERIFY_ON_ENTRY`
(default `False`) and runs on the first `Tab` instead, in
`ScrollSession._verify_regions()`.

The part that was missed: `verify()` was not only a tidy-up for `Tab`. It ran
*before* `best()` chose, so by the time anything was picked, regions that do
not actually scroll had already been dropped. `best()` (`scroll.py:613`)
returns **the smallest region under the pointer**:

```python
under = [r for r in regions if r.x <= px < r.x + r.w and r.y <= py < r.y + r.h]
if under:
    return min(under, key=lambda r: r.w * r.h)
```

So with an unverified list, a small nested element under the cursor — one that
publishes plausible overflow but does not scroll — now wins over the real
scroller. The geometry filters that survive (`_same_scroller` dedupe, the
"drop layout containers" rule) are shape-based and cannot catch this.

## What a fix has to do

Both at once, or it is not a fix:

1. Pick the region that actually scrolls, on the page in the report.
2. Do not put a pointer sweep back in front of the outline. Entering must stay
   free of visible cursor movement — verify with the runtime check below, not
   by reading the code.

## Three directions, best first

**A. Probe only the chosen region, after the outline is up.** One probe, not a
sweep, and it happens where `_promote_deferred()` already lives — the outline
is drawn, the keyboard is grabbed, the mode is usable, and it snaps if the
guess was wrong. Cost is one probe instead of N, and it is off the path the
user is watching. `ScrollSession._promote_deferred()` (`scroll.py:~1040`) is
the working example of this pattern, including standing down when `self._acted`
says the user already pressed something.

**B. Let the user's own first scroll be the probe.** `j` is a real wheel event
at a real region: if nothing moves, the region was wrong, so move to the next
candidate and re-send. Zero extra cost, self-correcting, and `_confirm_aim`
already watches for movement after a scroll — that machinery is most of it.
The risk is one wasted keystroke on the wrong region.

**C. Make `best()` cheaper to be right.** `min(..., key=area)` is the fragile
part; the innermost thing under the pointer is not usually the scroller. Rank
by how confidently `_overflows()` measured the region instead of by size.
Cheapest, and the least likely to be fully correct on its own — probably worth
doing *alongside* A or B rather than instead of them.

A is the recommendation; A+C together is the likely answer.

## Verifying it — the rules that matter here

Read `README.md` first, then:

- `$DISPLAY` is the user's **real desktop**, not a sandbox. Never
  `xdotool key`/`type` into their windows, and never trigger a mode
  (`bin/homerow --hint`, or writing a mode to the socket) in a loop: each one
  takes an exclusive keyboard grab for up to 12s and locks them out.
- Drive `scroll.collect()` / `scroll.best()` as Python functions instead. That
  is read-only apart from the probes under test, which is exactly what you
  want to measure.
- For anything that sends synthetic input, use the nested rig: `Xephyr :8` +
  `qtile` + a disposable window. Details in the session memory note
  `nested-x-bench-rig`.
- Chromium needs `--force-renderer-accessibility` or its tree is empty.
- **The user does not accept code reasoning as proof.** Produce the
  measurement.

Runtime check that entry moves nothing — this is the one that matters, because
static reachability lies (the `verify()` call site is still there, just gated):

```python
# with the devdocs page focused
import threading, time
from homerow import scroll, x11
seen, stop = [], threading.Event()
home = x11.pointer_position()
threading.Thread(target=lambda: [
    seen.append(x11.pointer_position()) or time.sleep(0.004)
    while not stop.is_set()], daemon=True).start()   # tidy this up
regions = scroll.collect(*x11.screen_size())
stop.set()
# assert every sample == home, and print which region best() chose
```

Beware two things that bit last time: activating the target window needs ~3s
before Chromium's tree answers (otherwise `collect()` returns 0 regions and the
comparison is meaningless), and pointer samples are contaminated if the user is
moving their own mouse — say so rather than reporting a clean number.

## Done means

- On the devdocs page, entering with the pointer over the sidebar scrolls the
  **sidebar**, and over the content pane scrolls the **content pane** —
  demonstrated, not argued.
- Entry still moves the cursor zero times, shown by the runtime check.
- Entry latency stated, next to the 316ms/726ms/2318ms it replaced.
- `ruff check homerow/ tests/ homerow-cli` clean, `python3 -m unittest
  discover -s tests` green (292 at the time of writing), with a test that
  fails if an unscrollable region can be chosen on entry.
- README's Scroll section updated in its existing voice — it explains *why*,
  with measurements, and currently claims entry is free of this cost. If the
  answer changes that trade, the paragraph has to change with it.
- `homerow --restart` afterwards: the daemon holds `homerow/` in memory.

## Meanwhile

`scroll: verify_on_entry: true` in `~/.config/homerow/config.yaml` (or
`SCROLL_VERIFY_ON_ENTRY = True` in `homerow/config.py`) restores the old
behaviour exactly — correct region, jitter back. It is a workaround, not the
fix, and it is why the setting exists.
