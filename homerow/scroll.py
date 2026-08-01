"""Scroll mode: pick a scrollable region by hint, then drive it with vim keys.

Scrolling is done with synthetic wheel events aimed at the region, not with
Home/End/PageDown keypresses. A keypress goes wherever focus happens to be, so
it can land as text in an input; a wheel event goes to whatever is under the
pointer and cannot type anything. It also means this works in apps whose
accessibility support is good enough to locate a region but not to scroll it.
"""

import subprocess
import time

import cairo
import gi

gi.require_version("Atspi", "2.0")
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Atspi, Gdk, GLib, Gtk  # noqa: E402

from . import config, elements, theme, x11  # noqa: E402
from .overlay import draw_legend, mode_switch, normalize_key, screen_size, set_identity  # noqa: E402

WHEEL_UP, WHEEL_DOWN = 4, 5
WHEEL_LEFT, WHEEL_RIGHT = 6, 7


def _roles(names):
    out = []
    for name in names:
        role = getattr(Atspi.Role, name, None)
        if role is not None:
            out.append(role)
    return out


def collect(screen_w, screen_h):
    """Scrollable regions in the focused window, largest first.

    Puts the pointer back where it started. Every overflow probe, rescue and
    verify pass warps it to the candidate it is testing, and the last one to
    run used to leave it there -- so by the time best() asked "what is under
    the pointer?", the answer was "whatever was probed last", not where the
    user's hand actually was. Live on devdocs.io: entering with the pointer on
    the sidebar left it at (840,444) in the content pane, so scroll mode opened
    on the content pane every time and the sidebar could only be reached by
    Tab. That is the "it scrolls the wrong thing because of the mouse position"
    report, and it also yanked the visible cursor across the screen on entry.
    """
    origin = _pointer_position()
    try:
        return _collect(screen_w, screen_h, origin)
    finally:
        if origin:
            _restore_pointer(origin)


class Regions(list):
    """The regions found, plus the candidates whose rescue probe was deferred.

    A plain list everywhere it is passed around, so nothing that only wants
    the regions has to know this exists; the leftovers ride along for the
    session that may later want to finish the job (see _collect's rescue pass).
    """

    def __init__(self, items=(), deferred=()):
        super().__init__(items)
        self.deferred = list(deferred)


def _collect(screen_w, screen_h, origin=None):
    """The actual pass; see collect() for why it is wrapped.

    Atspi.set_timeout() (see service.py) bounds each individual D-Bus call,
    but this makes many of them -- overflow tests, rescue probes, verify()'s
    scroll-and-watch -- each easily fast enough alone to dodge that cap, yet
    summing to a real stall on an AT-SPI service that is merely slow, not
    hung. A single overall deadline for the whole pass covers that case the
    per-call cap cannot; costlier stages (rescue, verify) check it and stop
    early rather than run over budget, biggest-first so what gets dropped
    under time pressure is the least-likely-to-matter candidate.
    """
    deadline = time.monotonic() + config.SCROLL_COLLECT_BUDGET_MS / 1000
    window = elements.active_window()
    if window is None:
        return []
    pid, win_x, win_y, win_w, win_h = window

    app = elements._app_for_pid(pid)
    if app is None:
        return []

    # Scope to the focused window: an app with several windows reports them
    # all, often at identical coordinates, so clipping by rectangle alone lets
    # regions in from windows that are not on screen.
    frame = elements.active_frame(app, (win_x, win_y, win_w, win_h))
    scope = frame if frame is not None else app

    collection = scope.get_collection_iface()
    if collection is None:
        return []

    left, top = max(win_x, 0), max(win_y, 0)
    right = min(win_x + win_w, screen_w)
    bottom = min(win_y + win_h, screen_h)

    def query(names):
        rule = Atspi.MatchRule.new(
            Atspi.StateSet.new(
                [Atspi.StateType.SHOWING, Atspi.StateType.VISIBLE]
            ), Atspi.CollectionMatchType.ALL,
            {}, Atspi.CollectionMatchType.ALL,
            _roles(names), Atspi.CollectionMatchType.ANY,
            [], Atspi.CollectionMatchType.ALL, False,
        )
        try:
            return collection.get_matches(
                rule, Atspi.CollectionSortOrder.CANONICAL,
                config.MAX_ELEMENTS, True)
        except Exception:
            return []

    matches = query(config.SCROLL_ROLES)
    seen = set()
    candidates = _shape(matches, win_x, win_y, left, top, right, bottom, seen)

    def sift(items):
        # Test the biggest first and stop after a fixed number: every overflow
        # test is several D-Bus round trips.
        items.sort(key=lambda e: e.w * e.h, reverse=True)
        kept = []
        for region in items[:config.SCROLL_MAX_CANDIDATES]:
            if time.monotonic() > deadline:
                break
            vertical, horizontal = _overflows(region)
            if vertical or horizontal:
                region.scroll_y = vertical
                region.scroll_x = horizontal
                kept.append(region)
        return kept

    regions = sift(candidates)

    # Always ask for the broad roles too. Running them only when the first
    # tier came up empty meant a page whose document scrolls hid its sidebar:
    # the document answered first, so the SECTION the sidebar lives in was
    # never looked at. The cost is real but a missing sidebar is worse.
    if time.monotonic() < deadline:
        extra = _shape(query(config.SCROLL_ROLES_FALLBACK), win_x, win_y,
                       left, top, right, bottom, seen)
        regions += sift(extra)
    else:
        extra = []

    # Rescue virtualised panes. A list that renders only its visible rows has
    # no children beyond its box, so measuring content extent proves nothing --
    # DevDocs' sidebar scrolls hundreds of entries and looks static. The only
    # way to know is to scroll it, so probe a couple of the biggest rejects,
    # and only when too little was found to be plausible.
    deferred = []
    if len(regions) < config.SCROLL_RESCUE_BELOW and time.monotonic() < deadline:
        # Only candidates that sit *beside* what was already found are worth
        # probing. Ranking by area alone put the page-level wrappers first --
        # they enclose the region we already have, so probing them just
        # rediscovers it, and the sidebar never got a turn.
        rejected = [
            region for region in (candidates + extra)
            if region not in regions
            and not any(_overlapping(region, kept) for kept in regions)
        ]
        rejected.sort(key=lambda e: e.w * e.h, reverse=True)
        # Keep every one that actually scrolls, not just the first. A page
        # can have more than one virtualised pane -- devdocs.io's sidebar and
        # its content pane both render only their visible rows -- and
        # stopping at the first success would rescue the content pane and
        # leave the sidebar looking unscrollable forever. Skip a candidate
        # that overlaps one already rescued: the wrappers around a sidebar
        # are larger than the sidebar itself, so ranking by area alone can
        # still put a wrapper right after the real thing, and it would
        # otherwise "rescue" the same scroller a second time.
        shortlist = rejected[:config.SCROLL_RESCUE_MAX]
        # Probing is the whole cost of entering scroll mode: each candidate is
        # scrolled and scrolled back, which the user watches happen. Measured
        # live on a Wikipedia article in Chromium, this pass alone was 666ms
        # and six wheel events of the page jumping before the outline was even
        # drawn -- and it rescued nothing, as it usually does not.
        #
        # It serves two ends, and only one of them is due now. Entering on the
        # right region needs the candidate under the pointer tested, because
        # best() is about to choose. Offering the others as Tab stops is not
        # needed until Tab is pressed, so it is left to the session to finish
        # (see ScrollSession._rescue_deferred). That keeps the devdocs.io case
        # -- pointer resting on a virtualised sidebar nothing else can see --
        # working at entry, while paying for the rest only when asked.
        wanted = origin or _pointer_position()
        # Only a candidate under the pointer can change best()'s answer, and
        # only when nothing already found is under it -- if something is,
        # best() has what it needs and no probe is owed at all. Of the
        # candidates that do contain the pointer, probe the innermost: every
        # wrapper around the real scroller contains the pointer just as truly,
        # and spending the budget on wrappers is exactly how devdocs.io's
        # content pane went undiscovered. One probe, or none. The rest are
        # Tab's problem.
        entry = []
        if wanted and not any(_inside(kept, *wanted) for kept in regions):
            entry = sorted((region for region in shortlist
                            if _inside(region, *wanted)),
                           key=lambda e: e.w * e.h)[:1]
        rescued = []
        for region in shortlist:
            if region not in entry or time.monotonic() > deadline:
                deferred.append(region)
            elif _scrolls(region, both_ways=config.SCROLL_RESCUE_BOTH_WAYS):
                region.scroll_y = True
                region.scroll_x = False
                rescued.append(region)
        regions.extend(rescued)

    # Collapse regions that would scroll the same thing. A page's document and
    # its content pane usually differ only by a margin, and offering both means
    # two labels with identical behaviour -- and a Tab that appears to do
    # nothing. Genuinely separate scrollers (a sidebar beside a content pane)
    # do not overlap like this and both survive.
    regions.sort(key=lambda e: e.w * e.h)
    distinct = []
    for region in regions:
        if not any(_same_scroller(region, kept) for kept in distinct):
            distinct.append(region)

    # Drop layout containers. A region enclosing two or more other scrollable
    # regions is the thing holding them, not a scroller of its own -- on a docs
    # page that is the wrapper around "sidebar + content", and scrolling it
    # does whatever the content does. Offering it means a label that either
    # duplicates another or appears to do nothing.
    distinct = [
        region for region in distinct
        if sum(1 for other in distinct
               if other is not region and _contains(region, other)) < 2
    ]

    distinct.sort(key=lambda e: e.w * e.h, reverse=True)
    if config.SCROLL_VERIFY:
        distinct = verify(distinct, deadline)
    return Regions(distinct, [region for region in deferred
                              if not any(_overlapping(region, kept)
                                         for kept in distinct)])


def _probe_child(region):
    """A descendant whose position can be watched to see if the region moved."""
    accessible = region.accessible
    if accessible is None:
        return None
    queue = [accessible]
    seen = 0
    while queue and seen < config.SCROLL_PROBE_SEARCH:
        node = queue.pop(0)
        try:
            count = node.get_child_count()
        except Exception:
            continue
        for index in range(min(count, 6)):
            seen += 1
            try:
                child = node.get_child_at_index(index)
                component = child.get_component_iface()
                if component is None:
                    continue
                ext = component.get_extents(Atspi.CoordType.SCREEN)
            except Exception:
                continue
            if ext.height > 0 and ext.width > 0:
                return child
            queue.append(child)
    return None


def _probe_children(region, limit=3):
    """A few descendants of a region whose positions can be watched."""
    accessible = region.accessible
    if accessible is None:
        return []
    found = []
    queue = [accessible]
    seen = 0
    while queue and seen < config.SCROLL_PROBE_SEARCH and len(found) < limit:
        node = queue.pop(0)
        try:
            count = node.get_child_count()
        except Exception:
            continue
        for index in range(min(count, 6)):
            seen += 1
            try:
                child = node.get_child_at_index(index)
                component = child.get_component_iface()
                if component is None:
                    continue
                ext = component.get_extents(Atspi.CoordType.SCREEN)
            except Exception:
                continue
            if ext.height > 0 and ext.width > 0:
                found.append(child)
                if len(found) >= limit:
                    break
            queue.append(child)
    return found


def _position(child):
    try:
        ext = child.get_component_iface().get_extents(Atspi.CoordType.SCREEN)
        return ext.y
    except Exception:
        return None


def _overlapping(a, b, threshold=None):
    """True if two regions share a meaningful part of the smaller one."""
    left, right = max(a.x, b.x), min(a.x + a.w, b.x + b.w)
    top, bottom = max(a.y, b.y), min(a.y + a.h, b.y + b.h)
    if threshold is None:
        threshold = config.SCROLL_BESIDE_OVERLAP
    if right <= left or bottom <= top:
        return False
    shared = (right - left) * (bottom - top)
    smaller = min(a.w * a.h, b.w * b.h)
    return bool(smaller) and shared / smaller >= threshold


# Where to aim the wheel, in the order the aim is escalated through: cheapest
# and least disruptive first. See ScrollSession._aim_point for what each means.
_AIMS = ("pointer", "center_x", "center")


def _moved(watchers, before):
    """True if any watcher's position differs from the sample in `before`.

    A couple of pixels of slop: a watcher's extents can wobble by a rounding
    error between two reads without anything having scrolled.
    """
    after = [_position(w) for w in watchers]
    return any(b is not None and a is not None and abs(a - b) > 2
               for b, a in zip(before, after))


def _scrolls(region, both_ways=False):
    """Scroll the region a little and see whether anything inside moved.

    `both_ways` retries upward for a pane already at its bottom, which does not
    move when scrolled down and would look unscrollable when it plainly is not.
    It doubles the cost, so it is saved for the retry pass rather than paid on
    every candidate.
    """
    return _scrolls_at(region, *region.center, both_ways=both_ways)


def _scrolls_at(region, x, y, both_ways=False):
    """As _scrolls(), but with the wheel aimed at an explicit point.

    A wheel event scrolls whatever sits under the pointer, which is not
    necessarily the region we mean to drive -- so "does this region scroll?"
    and "does aiming *here* scroll this region?" are different questions, and
    ScrollSession needs the second one to find out whether the user's cursor
    happens to be resting on some other scroller.
    """
    watchers = _probe_children(region)
    if not watchers:
        return False
    passes = ((WHEEL_DOWN, WHEEL_UP), (WHEEL_UP, WHEEL_DOWN))
    for forward, back in (passes if both_ways else passes[:1]):
        before = [_position(w) for w in watchers]
        _wheel(x, y, forward, config.SCROLL_PROBE_CLICKS)
        time.sleep(config.SCROLL_PROBE_SETTLE_MS / 1000)
        shifted = _moved(watchers, before)
        _wheel(x, y, back, config.SCROLL_PROBE_CLICKS)
        time.sleep(config.SCROLL_PROBE_SETTLE_MS / 1000)
        if shifted:
            return True
    return False


def verify(regions, deadline=None):
    """Keep regions that really scroll, one per actual scroller.

    Geometry cannot tell a scrollable pane from a tall column merely flowing
    inside one: AT-SPI clips extents to the viewport either way, so both report
    content taller than their visible box. So each candidate is scrolled by a
    single click and every watcher is re-read. Regions that move the same
    watchers are the same scroller; regions that move nothing do not scroll.
    The click is undone immediately.

    `deadline` (a time.monotonic() cutoff) stops testing further candidates
    once collect()'s overall time budget runs out -- regions arrive
    biggest-first, so what goes untested under time pressure is the
    least-likely-to-matter one, not the primary target.
    """
    if len(regions) < 2:
        return regions

    # Several watchers per region: the first child is often a sticky header
    # that never moves however far the pane scrolls.
    watchers = []
    for region in regions:
        watchers.extend(_probe_children(region))
    if not watchers:
        return regions

    tested = []
    for region in regions:
        if deadline is not None and time.monotonic() > deadline:
            break
        before = [_position(w) for w in watchers]
        x, y = region.center
        _wheel(x, y, WHEEL_DOWN, config.SCROLL_PROBE_CLICKS)
        time.sleep(config.SCROLL_PROBE_SETTLE_MS / 1000)
        after = [_position(w) for w in watchers]
        _wheel(x, y, WHEEL_UP, config.SCROLL_PROBE_CLICKS)
        time.sleep(config.SCROLL_PROBE_SETTLE_MS / 1000)
        moved = frozenset(
            index for index, (b, a) in enumerate(zip(before, after))
            if b is not None and a is not None and abs(a - b) > 2)
        tested.append((region, moved))

    kept, seen = [], []
    for region, moved in tested:
        if not moved:
            continue                       # scrolls nothing
        if any(moved & other for other in seen):
            continue                       # already have this scroller
        seen.append(moved)
        kept.append(region)
    return kept or regions


def _same_scroller(a, b):
    """True if two regions are near-coincident, so scrolling either is one act."""
    if not (_contains(a, b) or _contains(b, a)):
        return False
    area_a, area_b = a.w * a.h, b.w * b.h
    if not area_a or not area_b:
        return False
    return min(area_a, area_b) / max(area_a, area_b) >= config.SCROLL_SAME_RATIO


def _clamp(value, start, span):
    """Clamp into [start, start+span), kept clear of the very edge.

    Landing exactly on a pane's boundary is ambiguous: measured live in
    qutebrowser, a target on the 1px seam between the sidebar and the content
    pane scrolled the sidebar even though the content pane was the region being
    driven -- and inconsistently, so a probe could pass there and the real
    scroll still go to the wrong pane. Whatever the region is, a point a few
    pixels in belongs to it unambiguously.
    """
    margin = config.SCROLL_TARGET_MARGIN
    if span <= 4 * margin:
        margin = 0
    return min(max(value, start + margin), start + span - 1 - margin)


def _inside(region, x, y):
    return (region.x <= x < region.x + region.w
            and region.y <= y < region.y + region.h)


def _clear_point(region, blockers, position):
    """A point inside `region` but over none of `blockers`, nearest `position`.

    Sampled rather than solved exactly: the shape left over once a sidebar
    (or two) is subtracted from a pane is not a rectangle, and a handful of
    probes finds a usable spot on every real layout while staying trivial to
    reason about. The samples are the points just off each blocker's edges,
    which is where the cursor should end up in the ordinary
    pointer-resting-on-the-sidebar case, plus a coarse grid for anything less
    tidy. Whichever valid one is nearest the pointer wins, so the cursor moves
    as little as the geometry allows -- the same reason the clamp above exists.
    """
    px, py = position
    steps = [(i + 0.5) / config.SCROLL_TARGET_GRID
             for i in range(config.SCROLL_TARGET_GRID)]
    samples = [(int(region.x + region.w * fx), int(region.y + region.h * fy))
               for fy in steps for fx in steps]
    # Clear of the edge, not on it: the pixel immediately past a sidebar is
    # often the drag handle that resizes it, and a wheel event there does
    # nothing at all -- which looks exactly like the bug this fixes.
    step = config.SCROLL_TARGET_MARGIN
    for other in blockers:
        samples += [(other.x - step, py), (other.x + other.w + step, py),
                    (px, other.y - step), (px, other.y + other.h + step)]

    best_point, best_distance = None, None
    for x, y in samples:
        if not _inside(region, x, y):
            continue
        if any(_inside(other, x, y) for other in blockers):
            continue
        distance = (x - px) ** 2 + (y - py) ** 2
        if best_distance is None or distance < best_distance:
            best_point, best_distance = (x, y), distance
    # Every sample was blocked -- the siblings cover this region completely,
    # so there is nowhere better to aim than its middle.
    return best_point or region.center


def _contains(outer, inner):
    margin = config.SCROLL_CONTAIN_MARGIN
    return (outer.x - margin <= inner.x
            and outer.y - margin <= inner.y
            and outer.x + outer.w + margin >= inner.x + inner.w
            and outer.y + outer.h + margin >= inner.y + inner.h)


def _shape(matches, win_x, win_y, left, top, right, bottom, seen):
    """Turn raw matches into sized, on-screen, de-duplicated candidates."""
    out = []
    for acc, ext in elements._extents(matches, win_x, win_y):
        if ext.width < config.MIN_SCROLL_SIZE or \
                ext.height < config.MIN_SCROLL_SIZE:
            continue
        # Thin strips are chrome, not content: a tab bar scrolls sideways and
        # is about 40px tall, which cleared the absolute floor easily.
        if ext.height < (bottom - top) * config.MIN_SCROLL_FRACTION:
            continue
        cx, cy = ext.x + ext.width // 2, ext.y + ext.height // 2
        if not (left <= cx < right and top <= cy < bottom):
            continue
        key = (ext.x // 20, ext.y // 20, ext.width // 20, ext.height // 20)
        if key in seen:
            continue
        seen.add(key)
        out.append(elements.Element(acc, ext.x, ext.y, ext.width, ext.height))
    return out


def _overflows(region):
    """(vertical, horizontal) -- which axes the content overflows.

    Both axes are reported rather than or-ed together, because a region that
    only overflows downwards has nothing to scroll sideways: h and l would
    send horizontal wheel events that the app quietly swallows, which reads as
    scroll mode having broken.

    Sampling the first and last few children rather than all of them: each
    child is a D-Bus round trip, and a container that scrolls almost always
    has its extremes outside the visible box. Containers that report no usable
    children are kept -- a web document's children can be laid out lazily, and
    dropping those would lose the main case.
    """
    accessible = region.accessible
    try:
        count = accessible.get_child_count()
    except Exception:
        return True, True
    if count <= 0:
        return True, True

    probe = config.SCROLL_PROBE_CHILDREN
    indexes = list(range(min(probe, count)))
    indexes += [i for i in range(max(count - probe, 0), count)
                if i not in indexes]

    top = bottom = left = right = None
    for index in indexes:
        try:
            child = accessible.get_child_at_index(index)
            component = child.get_component_iface()
            if component is None:
                continue
            ext = component.get_extents(Atspi.CoordType.SCREEN)
        except Exception:
            continue
        if ext.width <= 0 or ext.height <= 0:
            continue
        top = ext.y if top is None else min(top, ext.y)
        left = ext.x if left is None else min(left, ext.x)
        bottom = (ext.y + ext.height if bottom is None
                  else max(bottom, ext.y + ext.height))
        right = (ext.x + ext.width if right is None
                 else max(right, ext.x + ext.width))

    if top is None:
        return True, True

    ratio = config.SCROLL_OVERFLOW_RATIO
    return ((bottom - top) > region.h * ratio,
            (right - left) > region.w * ratio)


def _wheel(x, y, button, times):
    # sync=False: see x11.click's docstring.
    if x11.available() and x11.click(button, x, y, times=times,
                                     delay_ms=config.SCROLL_CLICK_DELAY,
                                     sync=False):
        return
    argv = [
        "xdotool", "mousemove", "--sync", str(x), str(y),
        "click", "--repeat", str(times),
        "--delay", str(config.SCROLL_CLICK_DELAY), str(button),
    ]
    try:
        subprocess.run(argv, timeout=8, check=False)
    except (OSError, subprocess.SubprocessError):
        pass


def _wheel_paced(x, y, button, times):
    """Same as _wheel(), but paced by our own timer instead of XTest's.

    _wheel() asks the X server to sleep `delay_ms` between each of the
    `times` events it queues -- fine for the couple of clicks a scroll
    probe sends, but a gg/G edge jump asks for SCROLL_EDGE_CLICKS (400) of
    them, and that per-event server-side sleep blocks the server's *entire*
    synthetic-input queue while it works through them, not just this
    request. Confirmed live: the very next synthetic input sent by anyone
    -- including homerow's own next keypress, or the Escape that closes this
    session -- gets stuck behind the batch and does not actually fire until
    it finishes draining, seconds later. That is the "feels laggy, then
    suddenly catches up" symptom, not a rendering problem.

    Sending each click with delay_ms=0 and pacing the *next* one ourselves
    via a GLib timer keeps the server's queue empty between clicks, so nothing
    else ever has to wait behind it. Used for the interactive path (_apply);
    the probes in verify()/_scrolls() still use _wheel() -- they explicitly
    sleep and re-check afterward, so the extra few milliseconds of being
    server-paced for just two clicks was never the problem there.
    """
    if not x11.available():
        _wheel(x, y, button, times)
        return
    x11.warp_pointer(x, y)
    remaining = [times]

    def tick():
        if remaining[0] <= 0:
            return False
        x11.click(button, times=1, delay_ms=0, sync=False)
        remaining[0] -= 1
        return remaining[0] > 0

    if tick() and remaining[0] > 0:
        GLib.timeout_add(config.SCROLL_CLICK_DELAY, tick)


def best(regions):
    """The region to act on without asking.

    Under the pointer wins, since that is where you are looking; otherwise the
    largest, which is the main content pane on essentially every layout.
    Asking first turned every scroll into pick-a-region-then-scroll.
    """
    if not regions:
        return None
    position = _pointer_position()
    if position:
        px, py = position
        under = [r for r in regions
                 if r.x <= px < r.x + r.w and r.y <= py < r.y + r.h]
        if under:
            return min(under, key=lambda r: r.w * r.h)
        # The pointer is over something AT-SPI never surfaced as a candidate
        # -- a sidebar built from a widget the role/overflow probes didn't
        # recognise, say. Falling back to the largest known region would
        # silently scroll the content pane instead of what's under the
        # cursor. The whole window covers the pointer too, and
        # ScrollSession._wheel_target() prefers the real cursor position
        # whenever it falls inside self.region -- so this still lands the
        # wheel event exactly where the user is looking, the way native
        # wheel scrolling would, rather than wherever detection guessed.
        window = window_region()
        if window is not None:
            return window
    return regions[0]


def with_window_fallback(regions):
    """`regions` plus the whole window, as a last Tab stop.

    Detection routinely finds only some of a page's scrollers -- on
    devdocs.io/html-global-attributes it found the sidebar and not the content
    pane. When the one it found is also the one under the pointer, best()
    opens on it and the Tab list holds nothing else, so scroll mode can only
    ever scroll the sidebar and the content pane is unreachable. Keeping the
    window in the list means whatever went undetected is always one Tab away:
    the window covers it, and ScrollSession._wheel_target() steps the aim off
    the scrollers that WERE detected.

    Appended last, so cycling reaches the real, precisely-outlined regions
    first, and skipped when a detected region already spans the window --
    there it would just be a duplicate of something already offered.
    """
    if not config.SCROLL_FALLBACK_TO_WINDOW:
        return regions
    window = window_region()
    if window is None:
        return regions
    if any(_same_scroller(window, region) for region in regions):
        return regions
    return list(regions) + [window]


def window_region():
    """The focused window as a scroll target, for apps that report none."""
    window = elements.active_window()
    if window is None:
        return None
    _, x, y, w, h = window
    if w < config.MIN_SCROLL_SIZE or h < config.MIN_SCROLL_SIZE:
        return None
    region = elements.Element(None, x, y, w, h)
    # Nothing was measured, so do not rule either axis out.
    region.scroll_x = region.scroll_y = True
    return region


class ScrollSession:
    """Holds the keyboard and translates vim keys into wheel events."""

    KEYS = {
        Gdk.KEY_j: (WHEEL_DOWN, "line"),
        Gdk.KEY_k: (WHEEL_UP, "line"),
        Gdk.KEY_Down: (WHEEL_DOWN, "line"),
        Gdk.KEY_Up: (WHEEL_UP, "line"),
        Gdk.KEY_h: (WHEEL_LEFT, "line"),
        Gdk.KEY_l: (WHEEL_RIGHT, "line"),
        Gdk.KEY_d: (WHEEL_DOWN, "page"),
        Gdk.KEY_u: (WHEEL_UP, "page"),
        Gdk.KEY_Page_Down: (WHEEL_DOWN, "page"),
        Gdk.KEY_Page_Up: (WHEEL_UP, "page"),
        Gdk.KEY_G: (WHEEL_DOWN, "edge"),
        # Caps Lock (bound as the launch modifier -- see README) inverts case
        # for every letter, and there is no way to tell a genuine `G` from an
        # accidentally-capitalised `g` at the X11 protocol level once it is
        # on: both arrive as the same keysym with the same modifier state.
        # normalize_key resolves that ambiguity in favour of the far more
        # common case (plain letters), which makes `G` alone unreachable
        # while Caps Lock is stuck on. Home/End are not letters, so Caps Lock
        # cannot touch them -- they reach the same edges regardless.
        Gdk.KEY_Home: (WHEEL_UP, "edge"),
        Gdk.KEY_End: (WHEEL_DOWN, "edge"),
    }
    AMOUNTS = {
        "line": config.SCROLL_LINE_CLICKS,
        "page": config.SCROLL_PAGE_CLICKS,
        "edge": config.SCROLL_EDGE_CLICKS,
    }

    def __init__(self, region, on_done=None, regions=None, deferred=None):
        self.region = region
        self.on_done = on_done or (lambda: None)
        # Tab cycles the other candidates, so skipping the picker never means
        # being stuck with the wrong guess.
        self.regions = list(regions) if regions else [region]
        # Candidates that could only be confirmed by scrolling them, which is
        # what Tab is for. See _rescue_deferred.
        self.deferred = list(deferred) if deferred else []
        try:
            self.index = self.regions.index(region)
        except ValueError:
            self.index = 0
        self.pending_g = False
        self.count = ""
        self.origin = _pointer_position()

        set_identity()
        self.colors = theme.palette()
        self.width, self.height = screen_size()

        self.window = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        self.window.set_type_hint(Gdk.WindowTypeHint.DOCK)
        self.window.set_app_paintable(True)
        self.window.set_decorated(False)
        self.window.set_keep_above(True)
        self.window.set_accept_focus(True)
        self.window.set_skip_taskbar_hint(True)
        self.window.set_skip_pager_hint(True)
        self.window.set_default_size(self.width, self.height)

        screen = Gdk.Screen.get_default()
        visual = screen.get_rgba_visual()
        self.translucent = visual is not None
        if self.translucent:
            self.window.set_visual(visual)

        self.window.add_events(
            Gdk.EventMask.KEY_PRESS_MASK
            | Gdk.EventMask.VISIBILITY_NOTIFY_MASK
        )
        # The pointer passes through this overlay, so clicking a window
        # underneath raises that window above it -- the session is still live
        # and grabbing keys, but invisible. Re-raise whenever that happens.
        self.window.connect("visibility-notify-event", self._on_visibility)
        self.window.connect("draw", self._on_draw)
        self.window.connect("key-press-event", self._on_key)
        self._grabbed = False
        self._attempts = 0

    def show(self):
        self.window.show_all()
        self.window.move(0, 0)
        self.window.resize(self.width, self.height)
        self.window.fullscreen()
        gdk_window = self.window.get_window()
        if gdk_window is not None:
            gdk_window.raise_()
            # An empty input shape makes the overlay invisible to the pointer.
            # Without this the window sits under the cursor and swallows the
            # synthetic wheel events, so the region below never scrolls --
            # the outline appeared but nothing moved.
            gdk_window.input_shape_combine_region(cairo.Region(), 0, 0)
        GLib.idle_add(self._grab)
        GLib.idle_add(self._settle_aim)
        # Never hold the keyboard indefinitely. The grab is exclusive, so
        # while a session is open every other binding on the desktop is dead
        # -- including the ones that would close it. A session left open by
        # accident is indistinguishable from the keyboard having broken.
        self._idle = GLib.timeout_add_seconds(
            config.IDLE_TIMEOUT_S, self._on_idle)

    # -- input ----------------------------------------------------------

    def _grab(self):
        gdk_window = self.window.get_window()
        if gdk_window is not None:
            seat = Gdk.Display.get_default().get_default_seat()
            if seat.grab(gdk_window, Gdk.SeatCapabilities.KEYBOARD,
                         False, None, None, None, None) == \
                    Gdk.GrabStatus.SUCCESS:
                self._grabbed = True
                # The modifier that launched this is probably still held, and
                # the grab will swallow its release. Clear it now so typing a
                # label is not read as alt+label, and so the desktop is never
                # left believing a modifier is down.
                x11.release_modifiers()
                self.window.present()
                return False
        self._attempts += 1
        if self._attempts > 40:
            self._close()
            return False
        GLib.timeout_add(50, self._grab)
        return False

    def _on_key(self, _widget, event):
        self._touch()
        key = normalize_key(event.keyval, event.state)
        if config.DEBUG_KEYS:
            x11.debug_log(
                f"[scroll] key={Gdk.keyval_name(key)!r} "
                f"region=({self.region.x},{self.region.y},"
                f"{self.region.w},{self.region.h}) "
                f"pointer={x11.pointer_position()}")

        if mode_switch(event):
            return True
        if key in (Gdk.KEY_Escape, Gdk.KEY_q):
            self._close()
            return True

        if key in (Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab):
            self._rescue_deferred()
            if len(self.regions) < 2:
                return True
            step = -1 if key == Gdk.KEY_ISO_Left_Tab else 1
            self.index = (self.index + step) % len(self.regions)
            self.region = self.regions[self.index]
            self.window.queue_draw()
            GLib.idle_add(self._settle_aim)
            return True

        # Digits accumulate into a count, so 3j scrolls three lines. 0 is only
        # a count digit once a count is started; on its own it is not a motion
        # here, unlike in caret mode.
        unicode_point = Gdk.keyval_to_unicode(key)
        char = chr(unicode_point) if unicode_point else ""
        if char.isdigit() and (char != "0" or self.count):
            self.count = (self.count or "") + char
            self.window.queue_draw()
            return True

        # gg -- top. G alone is bottom, so only the doubled g means top.
        if key == Gdk.KEY_g:
            if self.pending_g:
                self.pending_g = False
                self._apply(WHEEL_UP, "edge")
            else:
                self.pending_g = True
            return True
        self.pending_g = False

        action = self.KEYS.get(key)
        if action is None:
            self.count = ""
            self.window.queue_draw()
            return True
        button, amount = action
        if button in (WHEEL_LEFT, WHEEL_RIGHT) and not self._sideways():
            # Nothing overflows sideways here, so these would be swallowed.
            self.count = ""
            self.window.queue_draw()
            return True
        self._apply(button, amount)
        return True

    def _sideways(self):
        return getattr(self.region, "scroll_x", True)

    def _apply(self, button, amount):
        repeat = int(self.count) if self.count else 1
        self.count = ""
        x, y = self._wheel_target()
        # An edge jump is already "all the way", so a count would only make it
        # slower for no further movement.
        clicks = self.AMOUNTS[amount]
        if amount != "edge":
            clicks *= max(1, min(repeat, config.SCROLL_MAX_COUNT))
        # Until the aim is settled, read the watchers first so this scroll can
        # double as the probe that settles it. Sideways scrolls are not used
        # for that: _position watches one axis, so a horizontal move reads as
        # no movement and would condemn a perfectly good aim.
        watchers, before = [], []
        if not getattr(self.region, "aim_fixed", False) \
                and button in (WHEEL_UP, WHEEL_DOWN):
            watchers = _probe_children(self.region)
            before = [_position(w) for w in watchers]
        if config.DEBUG_KEYS:
            started = time.perf_counter()
            _wheel_paced(x, y, button, clicks)
            elapsed = (time.perf_counter() - started) * 1000
            x11.debug_log(
                f"[scroll] wheel button={button} clicks={clicks} "
                f"target=({x},{y}) dispatch={elapsed:.2f}ms")
        else:
            _wheel_paced(x, y, button, clicks)
        self.window.queue_draw()
        if watchers:
            self._confirm_aim(watchers, before, button, clicks)

    def _wheel_target(self):
        """Where to land the synthetic wheel event.

        Always warping to the region's center, every keystroke, visibly
        yanked the real cursor there even though scroll mode's own default
        (scroll.best()) is to enter on the region already under the pointer
        -- the common case needed no warp at all, and got one every single
        j/k anyway. Preferring the pointer's current position when it is
        already inside the region avoids that.

        When the pointer really is outside the region (Tab switched regions,
        or the window-fallback region in scroll.best() was picked because
        nothing was detected where the pointer actually was), clamp the
        pointer's own position into the region rather than teleporting to
        its exact center: on a large pane the center can be far from wherever
        the user's hand actually was, and a full teleport there is a much
        bigger, more disorienting jump than nudging just the axis (or axes)
        that were actually out of bounds.

        Being inside the region is not enough on its own, though. A wheel
        event goes to whatever is under the pointer, so when the pointer sits
        over a *different* scroller that happens to live inside this region --
        the whole-window fallback encloses a page's sidebar, for instance --
        aiming there scrolls that inner scroller, not the region the outline
        is drawn around. Reported live on devdocs.io: with the pointer resting
        on the sidebar, both Tab entries scrolled the sidebar, so Tab looked
        broken. So step off any enclosed sibling first, and where the aim has
        been settled against the region itself (see _confirm_aim), use what
        actually worked.
        """
        aim = getattr(self.region, "aim", None)
        if aim in ("center_x", "center"):
            return self._aim_point(aim)
        return self._aim_point("pointer")

    def _aim_point(self, strategy):
        """Where `strategy` puts the wheel event for the current region.

        Strategies, in the order the aim is escalated through:

        "pointer"   the pointer's own position, clamped into the region and
                    stepped off any enclosed sibling. Costs no cursor movement
                    in the common case and scrolls what the user is looking at.
        "center_x"  the region's horizontal middle at the pointer's own height.
                    For the usual sidebar-beside-content layout this leaves the
                    sidebar while moving the cursor as little as possible.
        "center"    the region's middle, for anything the first two miss.
        """
        region = self.region
        cx, cy = region.center
        if strategy == "center":
            return cx, cy
        position = _pointer_position()
        if not position:
            return cx, cy
        px, py = position
        if strategy == "center_x":
            return cx, _clamp(py, region.y, region.h)
        x = _clamp(px, region.x, region.w)
        y = _clamp(py, region.y, region.h)
        blockers = self._enclosed_siblings()
        if not any(_inside(other, x, y) for other in blockers):
            return x, y
        return _clear_point(region, blockers, (px, py))

    def _rescue_deferred(self):
        """Finish collect()'s rescue pass, the first time Tab asks for it.

        These are candidates that publish no measurable overflow -- a
        virtualised pane renders only its visible rows, so DevDocs' sidebar
        looks static however far it really scrolls. Only scrolling one proves
        anything, and that is what made entering scroll mode a second of the
        page visibly jumping. Nothing here is needed to enter: collect()
        already probed whatever sat under the pointer, because best() had to
        choose. The rest are Tab stops, so they are found when Tab is pressed
        -- once, and with the outline already on screen, where a moment of
        movement reads as the mode looking for somewhere to go rather than as
        the page glitching before it opens.
        """
        pending, self.deferred = self.deferred, []
        for region in pending:
            if any(_overlapping(region, kept) for kept in self.regions):
                continue
            if _scrolls(region, both_ways=config.SCROLL_RESCUE_BOTH_WAYS):
                region.scroll_y = True
                region.scroll_x = False
                self.regions.append(region)

    def _settle_aim(self):
        """Decide the aim for the current region as far as that is free.

        Only the part that costs no scrolling happens here. A region with
        nothing to watch -- the whole-window fallback has no accessible at
        all -- can never be tested, so it takes "center_x" now: the fallback
        is an approximation by nature, and aiming down its middle is a better
        guess than trusting a cursor that may well be parked on a sidebar.
        Anything else is left undecided, which means "pointer" until the
        first real scroll says otherwise (see _confirm_aim).
        """
        region = self.region
        if getattr(region, "aim_fixed", False):
            return False
        if not _probe_children(region):
            region.aim = "center_x"
            region.aim_fixed = True
        return False

    def _confirm_aim(self, watchers, before, button, clicks):
        """Check the scroll just sent actually moved this region; retry if not.

        The step-off in _aim_point only works on scrollers AT-SPI actually
        reported. qutebrowser (Qt WebEngine) reports a devdocs.io page as one
        document and nothing else, so with the cursor resting on the sidebar
        there was no sibling to step off and every Tab entry scrolled the
        sidebar -- recorded live, the content pane never moved once. Geometry
        cannot see a scroller that was never published, so the aim has to be
        tested the only way that is toolkit-independent: scroll, and watch
        whether *this* region moved.

        What changed is when. This used to run on entry, as a synthetic
        scroll-and-undo at each candidate point in turn -- and measured live
        on a Wikipedia article in Chromium, that was 12 wheel events over
        1.14s of the page visibly jumping down and back before a single
        keystroke could be typed, ending on "pointer": the default it would
        have used anyway. The probe is the same measurement either way, so it
        rides on the scroll the user actually asked for instead of a synthetic
        one. Aiming right costs nothing at all; aiming wrong costs one settle
        and a re-send at the next point, which is the scroll they wanted
        anyway rather than a jump that undoes itself.

        There is no undo here for the same reason: a wrong aim scrolled
        something else, and scrolling that back is a second wrong scroll.
        Whatever moved, moved -- as it would have if they had aimed the real
        mouse there.
        """
        region = self.region
        current = getattr(region, "aim", None) or "pointer"
        time.sleep(config.SCROLL_PROBE_SETTLE_MS / 1000)
        if _moved(watchers, before):
            region.aim = current
            region.aim_fixed = True
            return

        # Nothing moved. Either the aim is wrong, or the region is simply at
        # the end it was asked to scroll towards -- and from here those look
        # identical. Trying the next point tells them apart, so try; but if
        # none of them move it either, leave the aim undecided rather than
        # recording a verdict reached at a wall. Sitting at the bottom of a
        # page and pressing j must not be what teaches a session that the
        # pointer aim is wrong.
        for strategy in _AIMS[_AIMS.index(current) + 1:]:
            x, y = self._aim_point(strategy)
            sample = [_position(w) for w in watchers]
            _wheel_paced(x, y, button, clicks)
            time.sleep(config.SCROLL_PROBE_SETTLE_MS / 1000)
            if _moved(watchers, sample):
                region.aim = strategy
                region.aim_fixed = True
                self.window.queue_draw()
                return

    def _enclosed_siblings(self):
        """Other Tab candidates that live inside the current region.

        Only regions this one encloses count: when the current region is
        itself the inner scroller, the pane around it is not in the way --
        the pointer being over it is exactly right.
        """
        region = self.region
        return [
            other for other in self.regions
            if other is not region
            and _contains(region, other) and not _contains(other, region)
        ]

    def _on_idle(self):
        """Close after a spell with no keys: a stuck grab locks the desktop."""
        self._idle = None
        self._close()
        return False

    def _touch(self):
        """Restart the idle countdown; called on every keystroke."""
        if getattr(self, "_idle", None) is not None:
            GLib.source_remove(self._idle)
        self._idle = GLib.timeout_add_seconds(
            config.IDLE_TIMEOUT_S, self._on_idle)

    def _on_visibility(self, _widget, event):
        """Re-raise when something covers us.

        The pointer passes through this overlay, so clicking a window
        underneath raises that window above it -- the session stays live and
        keeps grabbing keys while being invisible.
        """
        if event.state != Gdk.VisibilityState.UNOBSCURED:
            gdk_window = self.window.get_window()
            if gdk_window is not None:
                gdk_window.raise_()
        return False

    def dismiss(self):
        """Tear down from outside, so the daemon can replace this session."""
        self._close()

    def _close(self):
        if getattr(self, "_idle", None) is not None:
            GLib.source_remove(self._idle)
            self._idle = None
        if self._grabbed:
            Gdk.Display.get_default().get_default_seat().ungrab()
            self._grabbed = False
            # See Overlay._ungrab: a grab taken under a held modifier eats the
            # key-up, leaving the modifier logically stuck.
            x11.release_modifiers()
        self.window.destroy()
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        Gdk.Display.get_default().sync()
        if self.origin:
            _restore_pointer(self.origin)
        self.on_done()

    # -- drawing --------------------------------------------------------

    def _on_draw(self, widget, cr):
        cr.set_operator(1)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(2)

        gdk_window = widget.get_window()
        if gdk_window is not None:
            _, origin_x, origin_y = gdk_window.get_origin()
            cr.translate(-origin_x, -origin_y)

        region = self.region
        colors = self.colors

        # Dim everything except the region, rather than drawing a bare outline
        # on top of the page. A stroke alone competes with whatever it happens
        # to land on; a cut-out reads as "this is the thing you are driving"
        # regardless of what is underneath.
        if config.DIM_BACKGROUND and self.translucent:
            cr.save()
            cr.set_fill_rule(1)  # EVEN_ODD -- the region punches a hole
            cr.rectangle(0, 0, self.width, self.height)
            _rounded(cr, region.x, region.y, region.w, region.h,
                     config.SCROLL_RADIUS)
            cr.set_source_rgba(*colors["dim"])
            cr.fill()
            cr.restore()

        cr.set_source_rgba(*colors["chip_matched"])
        cr.set_line_width(config.SCROLL_BORDER)
        half = config.SCROLL_BORDER / 2
        _rounded(cr, region.x + half, region.y + half,
                 region.w - config.SCROLL_BORDER,
                 region.h - config.SCROLL_BORDER, config.SCROLL_RADIUS)
        cr.stroke()

        legend = "j/k line   d/u page   gg/G ends   "
        if self._sideways():
            legend += "h/l sideways   "
        legend += "3j counts   esc"
        if len(self.regions) > 1:
            legend = (f"[{self.index + 1}/{len(self.regions)} tab]   "
                      + legend)
        if self.count:
            legend = f"{self.count}…   " + legend

        # Fixed to the bottom of the screen, like every other mode's legend.
        # Positioning it relative to the region meant it landed wherever the
        # region happened to be -- off the top when a page container reported
        # a negative y, and over the WM bar once clamped. A constant place is
        # both always visible and always where you already looked.
        draw_legend(cr, legend, self.width, self.height, colors,
                    chip="chip_matched")
        return True


def _rounded(cr, x, y, w, h, radius):
    import math
    radius = max(0, min(radius, w / 2, h / 2))
    cr.new_sub_path()
    cr.arc(x + w - radius, y + radius, radius, -math.pi / 2, 0)
    cr.arc(x + w - radius, y + h - radius, radius, 0, math.pi / 2)
    cr.arc(x + radius, y + h - radius, radius, math.pi / 2, math.pi)
    cr.arc(x + radius, y + radius, radius, math.pi, 3 * math.pi / 2)
    cr.close_path()


def _pointer_position():
    # In-process ctypes first, same as click.py -- this runs on entering and
    # leaving every scroll session, so a ~10-30ms xdotool spawn here was
    # visible as a stutter each time, not just a latency number. xdotool is
    # the fallback for a system without libX11 bound, not the normal path.
    if x11.available():
        position = x11.pointer_position()
        if position:
            return position
    try:
        out = subprocess.run(
            ["xdotool", "getmouselocation", "--shell"],
            capture_output=True, text=True, timeout=1,
        )
        if out.returncode != 0:
            return None
        values = dict(
            line.split("=", 1)
            for line in out.stdout.strip().splitlines() if "=" in line
        )
        return int(values["X"]), int(values["Y"])
    except (ValueError, KeyError, OSError, subprocess.SubprocessError):
        return None


def _restore_pointer(origin):
    if x11.available() and x11.warp_pointer(*origin):
        return
    try:
        subprocess.run(
            ["xdotool", "mousemove", "--sync", str(origin[0]), str(origin[1])],
            timeout=2, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        pass
