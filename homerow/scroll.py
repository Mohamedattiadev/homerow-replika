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
from .overlay import screen_size, set_identity  # noqa: E402

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
    """Scrollable regions in the focused window, largest first."""
    window = elements.active_window()
    if window is None:
        return []
    pid, win_x, win_y, win_w, win_h = window

    app = elements._app_for_pid(pid)
    if app is None:
        return []

    collection = app.get_collection_iface()
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
    extra = _shape(query(config.SCROLL_ROLES_FALLBACK), win_x, win_y,
                   left, top, right, bottom, seen)
    regions += sift(extra)

    # Rescue virtualised panes. A list that renders only its visible rows has
    # no children beyond its box, so measuring content extent proves nothing --
    # DevDocs' sidebar scrolls hundreds of entries and looks static. The only
    # way to know is to scroll it, so probe a couple of the biggest rejects,
    # and only when too little was found to be plausible.
    if len(regions) < config.SCROLL_RESCUE_BELOW:
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
        # Stop at the first one that actually scrolls. The wrappers around a
        # sidebar are larger than the sidebar itself, so ranking by area alone
        # spends the budget on boxes that do nothing.
        for region in rejected[:config.SCROLL_RESCUE_MAX]:
            if _scrolls(region):
                region.scroll_y = True
                region.scroll_x = False
                regions.append(region)
                break

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
        distinct = verify(distinct)
    return distinct


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


def _overlapping(a, b, threshold=0.25):
    """True if two regions share a meaningful part of the smaller one."""
    left, right = max(a.x, b.x), min(a.x + a.w, b.x + b.w)
    top, bottom = max(a.y, b.y), min(a.y + a.h, b.y + b.h)
    if right <= left or bottom <= top:
        return False
    shared = (right - left) * (bottom - top)
    smaller = min(a.w * a.h, b.w * b.h)
    return bool(smaller) and shared / smaller >= threshold


def _scrolls(region):
    """Scroll the region a little and see whether anything inside moved.

    Both directions are tried: a pane already at its bottom does not move when
    scrolled down, and would look unscrollable when it plainly is not.
    """
    watchers = _probe_children(region)
    if not watchers:
        return False
    x, y = region.center
    for forward, back in ((WHEEL_DOWN, WHEEL_UP), (WHEEL_UP, WHEEL_DOWN)):
        before = [_position(w) for w in watchers]
        _wheel(x, y, forward, config.SCROLL_PROBE_CLICKS)
        time.sleep(config.SCROLL_PROBE_SETTLE_MS / 1000)
        after = [_position(w) for w in watchers]
        _wheel(x, y, back, config.SCROLL_PROBE_CLICKS)
        time.sleep(config.SCROLL_PROBE_SETTLE_MS / 1000)
        if any(b is not None and a is not None and abs(a - b) > 2
               for b, a in zip(before, after)):
            return True
    return False


def verify(regions):
    """Keep regions that really scroll, one per actual scroller.

    Geometry cannot tell a scrollable pane from a tall column merely flowing
    inside one: AT-SPI clips extents to the viewport either way, so both report
    content taller than their visible box. So each candidate is scrolled by a
    single click and every watcher is re-read. Regions that move the same
    watchers are the same scroller; regions that move nothing do not scroll.
    The click is undone immediately.
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

    signatures = []
    for region in regions:
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
        signatures.append(moved)

    kept, seen = [], []
    for region, moved in zip(regions, signatures):
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
    if x11.available() and x11.click(button, x, y, times=times,
                                     delay_ms=config.SCROLL_CLICK_DELAY):
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
    return regions[0]


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
    }
    AMOUNTS = {
        "line": config.SCROLL_LINE_CLICKS,
        "page": config.SCROLL_PAGE_CLICKS,
        "edge": config.SCROLL_EDGE_CLICKS,
    }

    def __init__(self, region, on_done=None, regions=None):
        self.region = region
        self.on_done = on_done or (lambda: None)
        # Tab cycles the other candidates, so skipping the picker never means
        # being stuck with the wrong guess.
        self.regions = list(regions) if regions else [region]
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
        key = event.keyval

        if key in (Gdk.KEY_Escape, Gdk.KEY_q):
            self._close()
            return True

        if key in (Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab) \
                and len(self.regions) > 1:
            step = -1 if key == Gdk.KEY_ISO_Left_Tab else 1
            self.index = (self.index + step) % len(self.regions)
            self.region = self.regions[self.index]
            self.window.queue_draw()
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
        x, y = self.region.center
        # An edge jump is already "all the way", so a count would only make it
        # slower for no further movement.
        clicks = self.AMOUNTS[amount]
        if amount != "edge":
            clicks *= max(1, min(repeat, config.SCROLL_MAX_COUNT))
        _wheel(x, y, button, clicks)
        self.window.queue_draw()

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
        cr.select_font_face(config.FONT_FAMILY)
        cr.set_font_size(config.FONT_SIZE)
        ext = cr.text_extents(legend)
        pad = 8
        w, h = ext.width + pad * 2, config.FONT_SIZE + pad + 4

        # Fixed to the bottom of the screen, like every other mode's legend.
        # Positioning it relative to the region meant it landed wherever the
        # region happened to be -- off the top when a page container reported
        # a negative y, and over the WM bar once clamped. A constant place is
        # both always visible and always where you already looked.
        x = min(max((self.width - w) // 2, 0), max(self.width - w, 0))
        y = max(self.height - h - config.LEGEND_MARGIN, 0)

        _rounded(cr, x, y, w, h, config.SCROLL_RADIUS)
        cr.set_source_rgba(*colors["chip_matched"])
        cr.fill()
        cr.set_source_rgba(*colors["ink"])
        cr.move_to(x + pad, y + h - pad + 1)
        cr.show_text(legend)
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
    try:
        subprocess.run(
            ["xdotool", "mousemove", "--sync", str(origin[0]), str(origin[1])],
            timeout=2, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        pass
