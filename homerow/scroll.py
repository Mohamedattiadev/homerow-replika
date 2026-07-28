"""Scroll mode: pick a scrollable region by hint, then drive it with vim keys.

Scrolling is done with synthetic wheel events aimed at the region, not with
Home/End/PageDown keypresses. A keypress goes wherever focus happens to be, so
it can land as text in an input; a wheel event goes to whatever is under the
pointer and cannot type anything. It also means this works in apps whose
accessibility support is good enough to locate a region but not to scroll it.
"""

import subprocess

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
        return [r for r in items[:config.SCROLL_MAX_CANDIDATES]
                if _overflows(r)]

    regions = sift(candidates)
    if not regions:
        # Nothing obvious scrolls, so pay for the broad roles now.
        candidates = _shape(query(config.SCROLL_ROLES_FALLBACK), win_x, win_y,
                            left, top, right, bottom, seen)
        regions = sift(candidates)

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
    return distinct


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
    """True if the region's content is bigger than the region itself.

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
        return True
    if count <= 0:
        return True

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
        return True

    ratio = config.SCROLL_OVERFLOW_RATIO
    return ((bottom - top) > region.h * ratio
            or (right - left) > region.w * ratio)


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
    return elements.Element(None, x, y, w, h)


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

    def __init__(self, region, on_done=None, on_caret=None,
                 regions=None):
        self.region = region
        self.on_done = on_done or (lambda: None)
        # v leaves scrolling and starts a text cursor. It used to run a
        # shift+arrow selection that caret mode replaced, so it became a key
        # that silently did nothing.
        self.on_caret = on_caret
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

    # -- input ----------------------------------------------------------

    def _grab(self):
        gdk_window = self.window.get_window()
        if gdk_window is not None:
            seat = Gdk.Display.get_default().get_default_seat()
            if seat.grab(gdk_window, Gdk.SeatCapabilities.KEYBOARD,
                         False, None, None, None, None) == \
                    Gdk.GrabStatus.SUCCESS:
                self._grabbed = True
                self.window.present()
                return False
        self._attempts += 1
        if self._attempts > 40:
            self._close()
            return False
        GLib.timeout_add(50, self._grab)
        return False

    def _on_key(self, _widget, event):
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

        if key == Gdk.KEY_v and self.on_caret is not None:
            handoff = self.on_caret
            self._close()
            handoff()
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
        self._apply(button, amount)
        return True

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

        legend = ("j/k line   d/u page   gg/G ends   h/l sideways   "
                  "3j counts   v caret   esc")
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

        # Sit the legend just inside the region's top edge, or just below it
        # when the region starts at the very top of the screen. Hanging it
        # above a region flush with the screen edge put it off-screen.
        x = min(max(region.x + (region.w - w) // 2, 0), max(self.width - w, 0))
        y = region.y - h - 6
        if y < 0:
            y = min(region.y + 6, self.height - h)

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
