"""Find actionable UI elements in the focused window via AT-SPI.

The whole design hangs on Collection.get_matches: it runs the role/state filter
inside the target application and returns matches in a single D-Bus round trip.
Walking the tree by hand costs one round trip per node (~480 nodes/sec here),
which is far too slow to feel instant.
"""

import subprocess
import time
from dataclasses import dataclass

import gi

gi.require_version("Atspi", "2.0")
from gi.repository import Atspi  # noqa: E402

from . import config, x11  # noqa: E402


@dataclass
class Element:
    """A hintable target, already resolved to screen coordinates.

    `name` and `role` are deliberately lazy: each is a D-Bus round trip
    (~13ms per element on this machine) and drawing hints never needs them.
    Only --debug and --list pay for them.
    """

    accessible: object
    x: int
    y: int
    w: int
    h: int

    kind = "element"

    @property
    def center(self):
        return self.x + self.w // 2, self.y + self.h // 2

    @property
    def name(self):
        try:
            return self.accessible.get_name() or ""
        except Exception:
            return ""

    @property
    def role(self):
        try:
            return self.accessible.get_role_name() or ""
        except Exception:
            return ""


def _roles(names=None):
    out = []
    for name in (names if names is not None else config.HINT_ROLES):
        role = getattr(Atspi.Role, name, None)
        if role is not None:
            out.append(role)
    return out


def active_window():
    """(pid, x, y, w, h) of the focused X11 window, or None.

    The geometry lets us scope results to the focused window without the
    per-child AT-SPI state probing that frame detection would cost (~127ms).
    """
    if x11.available():
        window = x11.active_window_id()
        if window:
            pid = x11.window_pid(window)
            geometry = x11.window_geometry(window)
            if pid and geometry and _mostly_on_screen(geometry) \
                    and not _is_desktop(window):
                return (pid, *geometry)
        return None

    try:
        out = subprocess.run(
            ["xdotool", "getactivewindow", "getwindowpid",
             "getactivewindow", "getwindowgeometry", "--shell"],
            capture_output=True, text=True, timeout=1,
        )
        if out.returncode != 0:
            return None
        lines = out.stdout.strip().splitlines()
        pid = int(lines[0])
        values = dict(
            line.split("=", 1) for line in lines[1:] if "=" in line
        )
        return (pid, int(values["X"]), int(values["Y"]),
                int(values["WIDTH"]), int(values["HEIGHT"]))
    except (ValueError, KeyError, IndexError, OSError,
            subprocess.SubprocessError):
        return None


def _is_desktop(window):
    """True for the desktop or a panel, which are not app windows.

    pcmanfm draws the desktop as a full-screen window. Treating it as the
    focused app meant clipping to "the active window" filtered nothing, so
    elements from that app's *other* windows -- sitting on other groups with
    stale coordinates -- were all offered, scattered over the wallpaper.
    """
    types = x11.window_type(window)
    return any(t.endswith(("_DESKTOP", "_DOCK")) for t in types)


def _mostly_on_screen(geometry):
    """Reject windows that are only technically the active window.

    A hidden scratchpad parked above the top edge still holds
    _NET_ACTIVE_WINDOW when nothing else is focused, so clicking empty space
    and pressing a hotkey targeted qdrop instead of doing nothing.
    """
    screen = x11.screen_size()
    if screen is None:
        return True
    screen_w, screen_h = screen
    x, y, w, h = geometry
    visible_w = max(0, min(x + w, screen_w) - max(x, 0))
    visible_h = max(0, min(y + h, screen_h) - max(y, 0))
    if visible_w < config.MIN_WINDOW_SIZE or visible_h < config.MIN_WINDOW_SIZE:
        return False
    area = w * h
    return not area or (visible_w * visible_h) / area >= config.MIN_ONSCREEN


_app_cache = {}


def _app_for_pid(pid):
    """The AT-SPI application for a pid, cached.

    Scanning the desktop costs a round trip per application -- about 20ms with
    a dozen apps open, paid on every single press. Application objects are
    stable for the life of the process, so the cache only needs one round trip
    to confirm the entry still refers to the same pid.
    """
    cached = _app_cache.get(pid)
    if cached is not None:
        try:
            if cached.get_process_id() == pid:
                return cached
        except Exception:
            pass
        _app_cache.pop(pid, None)

    desktop = Atspi.get_desktop(0)
    for i in range(desktop.get_child_count()):
        try:
            app = desktop.get_child_at_index(i)
            if app and app.get_process_id() == pid:
                _app_cache[pid] = app
                return app
        except Exception:
            continue
    return None


def active_frame(app, window_rect):
    """The top-level frame for the focused window, or None.

    An application with several windows reports them all, and a window manager
    that stacks them puts several at the very same coordinates -- Brave here
    had six frames, three sharing one rectangle. Clipping results to the
    focused window's rectangle therefore lets the other windows through, so
    hints and scroll regions arrive from windows that are not on screen.

    Apps that mark a frame ACTIVE answer this directly; for the rest, the
    frame whose geometry matches the focused window is the best available
    signal.
    """
    try:
        count = app.get_child_count()
    except Exception:
        return None
    if count < 2:
        return None

    frames = []
    for index in range(count):
        try:
            frame = app.get_child_at_index(index)
            states = frame.get_state_set()
            if not states.contains(Atspi.StateType.SHOWING):
                continue
            frames.append((frame, states))
        except Exception:
            continue

    for frame, states in frames:
        if states.contains(Atspi.StateType.ACTIVE):
            return frame

    if window_rect is None:
        return None
    wx, wy, ww, wh = window_rect
    best, best_score = None, None
    for frame, _ in frames:
        try:
            ext = frame.get_component_iface().get_extents(
                Atspi.CoordType.SCREEN)
        except Exception:
            continue
        score = (abs(ext.x - wx) + abs(ext.y - wy)
                 + abs(ext.width - ww) + abs(ext.height - wh))
        if best_score is None or score < best_score:
            best, best_score = frame, score
    # Only trust a close match; a wildly different frame means we cannot tell.
    if (best is not None and best_score is not None
            and best_score <= config.FRAME_MATCH_TOLERANCE):
        return best
    return None


def active_document(app, window_title):
    """The document node for the foreground tab, or None.

    Qt WebEngine keeps every background tab's accessibility tree alive, all
    reporting SHOWING, VISIBLE and the very same rectangle -- so a five-tab
    window offered five pages of hints stacked on one viewport, most of them
    over blank space. No state distinguishes the foreground tab; the window
    title does, because the WM title is the active tab's title.
    """
    collection = app.get_collection_iface()
    if collection is None:
        return None
    rule = Atspi.MatchRule.new(
        Atspi.StateSet.new([]), Atspi.CollectionMatchType.ALL,
        {}, Atspi.CollectionMatchType.ALL,
        _roles(["DOCUMENT_WEB"]), Atspi.CollectionMatchType.ANY,
        [], Atspi.CollectionMatchType.ALL, False)
    try:
        docs = collection.get_matches(
            rule, Atspi.CollectionSortOrder.CANONICAL, 40, True)
    except Exception:
        return None
    if len(docs) < 2:
        return None          # single document: nothing to disambiguate

    # Chromium marks the foreground document FOCUSED. Prefer that: it is the
    # browser's own answer, and unlike the window title it cannot go stale
    # when a tab closes -- a stale title once matched a background document
    # and hinted the wrong page entirely.
    focused = []
    for doc in docs:
        try:
            if doc.get_state_set().contains(Atspi.StateType.FOCUSED):
                focused.append(doc)
        except Exception:
            continue
    if len(focused) == 1:
        return focused[0]

    # Qt WebEngine marks none of them, so fall back to the window title, which
    # is the active tab's title.
    title = (window_title or "").lower()
    if not title:
        return None
    best, best_len = None, 0
    for doc in docs:
        try:
            name = (doc.get_name() or "").strip().lower()
        except Exception:
            continue
        if name and name in title and len(name) > best_len:
            best, best_len = doc, len(name)
    return best


def _candidates(app):
    """Actionable accessibles under `app`, by whichever route it supports.

    Collection.get_matches is the fast path -- one D-Bus round trip for the
    whole filter. Not every toolkit implements it though (pavucontrol exposes
    no Collection interface on its application *or* its frame), so fall back to
    walking. The walk is bounded because untargeted walking runs at roughly
    480 nodes/sec, which is unusable on a large tree.
    """
    collection = app.get_collection_iface()
    if collection is not None:
        return _query_both(collection)

    # Some apps implement Collection per top-level rather than per application.
    matches = []
    walk_roots = []
    for index in range(app.get_child_count()):
        try:
            frame = app.get_child_at_index(index)
        except Exception:
            continue
        frame_collection = frame.get_collection_iface()
        if frame_collection is None:
            walk_roots.append(frame)
            continue
        matches.extend(_query_both(frame_collection))

    deadline = time.monotonic() + config.WALK_BUDGET_MS / 1000
    for root in walk_roots:
        matches.extend(_walk(root, deadline, config.MAX_ELEMENTS - len(matches)))
    return matches


def _walk(root, deadline, cap):
    """Breadth-first hunt for hintable roles, bounded by time and count."""
    actionable = set(_roles(config.ACTIONABLE_ROLES))
    containers = set(_roles(config.CONTAINER_ROLES))
    found = []
    queue = [root]
    while queue and len(found) < cap:
        if time.monotonic() > deadline:
            break
        node = queue.pop(0)
        try:
            count = node.get_child_count()
        except Exception:
            continue
        for index in range(count):
            try:
                child = node.get_child_at_index(index)
                role = child.get_role()
            except Exception:
                continue
            if role in actionable or role in containers:
                try:
                    states = child.get_state_set()
                    if states.contains(Atspi.StateType.SHOWING) and (
                        role in actionable
                        or states.contains(Atspi.StateType.FOCUSABLE)
                    ):
                        found.append(child)
                except Exception:
                    pass
            queue.append(child)
    return found


def _match_rule(roles=None, require_focusable=False):
    wanted = [Atspi.StateType.SHOWING, Atspi.StateType.VISIBLE]
    if require_focusable:
        wanted.append(Atspi.StateType.FOCUSABLE)
    return Atspi.MatchRule.new(
        Atspi.StateSet.new(wanted), Atspi.CollectionMatchType.ALL,
        {}, Atspi.CollectionMatchType.ALL,
        _roles(roles), Atspi.CollectionMatchType.ANY,
        [], Atspi.CollectionMatchType.ALL,
        False,
    )


def _query(collection, roles, require_focusable):
    return collection.get_matches(
        _match_rule(roles, require_focusable),
        Atspi.CollectionSortOrder.CANONICAL,
        config.MAX_ELEMENTS, True,
    )


def _query_both(collection):
    """Actionable roles unconditionally, container roles only when focusable."""
    found = _query(collection, config.ACTIONABLE_ROLES, False)
    found += _query(collection, config.CONTAINER_ROLES, True)
    return found


class _Rect:
    __slots__ = ("x", "y", "width", "height")

    def __init__(self, x, y, width, height):
        self.x, self.y, self.width, self.height = x, y, width, height


def _extents(matches, win_x, win_y):
    """Pair each accessible with its screen rectangle.

    Some toolkits report SCREEN coordinates as 0,0 for everything -- pavucontrol
    does, while its WINDOW coordinates are correct. Detect that case by looking
    at the whole batch rather than any single element, since one element
    legitimately sitting at the origin is not evidence of anything, then
    re-read in WINDOW space and offset by the window's own position.
    """
    pairs = []
    for acc in matches:
        try:
            component = acc.get_component_iface()
            if component is None:
                continue
            ext = component.get_extents(Atspi.CoordType.SCREEN)
        except Exception:
            continue
        pairs.append((acc, _Rect(ext.x, ext.y, ext.width, ext.height)))

    at_origin = sum(1 for _, e in pairs if e.x == 0 and e.y == 0)
    if len(pairs) < 3 or at_origin < len(pairs) * 0.75:
        return pairs

    repaired = []
    for acc, screen_ext in pairs:
        try:
            ext = acc.get_component_iface().get_extents(Atspi.CoordType.WINDOW)
        except Exception:
            repaired.append((acc, screen_ext))
            continue
        repaired.append((
            acc,
            _Rect(ext.x + win_x, ext.y + win_y, ext.width, ext.height),
        ))
    return repaired


def _encloses(outer, inner):
    return (outer.x <= inner.x and outer.y <= inner.y
            and outer.x + outer.w >= inner.x + inner.w
            and outer.y + outer.h >= inner.y + inner.h
            and (outer.w * outer.h) > (inner.w * inner.h))


def _inside(ext, rect):
    if rect is None:
        return False
    x, y, w, h = rect
    cx, cy = ext.x + ext.width // 2, ext.y + ext.height // 2
    return x <= cx < x + w and y <= cy < y + h


def _nested(rect, accepted):
    """True if this candidate is a duplicate of something already accepted.

    Two shapes count as one thing wearing two hats:

    - Similarly sized boxes whose centres coincide. A small button
      legitimately sits inside a large row or toolbar, and those are
      separate targets; it is the near-same-size wrapper/child pair that is
      one thing, which is why only a close area ratio counts here.
    - A candidate flush against an accepted box's own top-left corner and
      fully inside it, regardless of size ratio. A combobox showing its
      currently selected value exposes that value as its own "menu item"
      accessible sitting at the box's own origin, spanning only part of its
      height -- e.g. a 172x46 edition/language/date selector with an 11px-tall
      selected-item label starting at the same corner (seen identically on
      WSJ, Python docs, Amazon, OpenTable and Netflix). The area ratio there
      is nowhere near 1, so only the shared-corner check catches it.
    """
    cx, cy = rect.x + rect.w // 2, rect.y + rect.h // 2
    area = rect.w * rect.h
    for other in accepted:
        if not (other.x <= cx < other.x + other.w
                and other.y <= cy < other.y + other.h):
            continue
        other_area = other.w * other.h
        if not other_area:
            continue
        ratio = area / other_area
        if config.NEST_MIN_RATIO <= ratio <= config.NEST_MAX_RATIO:
            return True

    slop = config.NEST_CORNER_SLOP
    for other in accepted:
        if (abs(rect.x - other.x) <= slop and abs(rect.y - other.y) <= slop
                and rect.x + rect.w <= other.x + other.w + slop
                and rect.y + rect.h <= other.y + other.h + slop):
            return True
    return False


def collect(screen_w, screen_h):
    """Actionable elements in the focused window, in reading order."""
    window = active_window()
    if window is None:
        return []
    pid, win_x, win_y, win_w, win_h = window

    app = _app_for_pid(pid)
    if app is None:
        # Nothing in the a11y tree owns the focused window -- the app simply
        # does not expose accessibility. Nothing to hint.
        return []

    # Restrict to the foreground tab when the app keeps several alive, but
    # keep the chrome around the page -- tab bar, url bar, status line -- by
    # taking anything outside the document's rectangle from the whole app.
    # Narrow to the focused window first, then to its foreground tab.
    scope = app
    frame = active_frame(app, (win_x, win_y, win_w, win_h))
    if frame is not None:
        scope = frame

    document = None
    doc_rect = None
    try:
        title = x11.window_name(x11.active_window_id() or 0) if \
            x11.available() else ""
        document = active_document(scope, title)
        if document is not None:
            ext = document.get_component_iface().get_extents(
                Atspi.CoordType.SCREEN)
            doc_rect = (ext.x, ext.y, ext.width, ext.height)
    except Exception:
        document, doc_rect = None, None

    try:
        if document is not None:
            matches = list(_candidates(document))
            for accessible, ext in _extents(_candidates(scope), win_x, win_y):
                if not _inside(ext, doc_rect):
                    matches.append(accessible)
        else:
            matches = _candidates(scope)
    except Exception:
        return []

    max_w = screen_w * config.MAX_FRACTION_OF_SCREEN
    max_h = screen_h * config.MAX_FRACTION_OF_SCREEN

    # Clip to the focused window so an occluded window belonging to the same
    # application cannot contribute phantom hints.
    left, top = max(win_x, 0), max(win_y, 0)
    right, bottom = min(win_x + win_w, screen_w), min(win_y + win_h, screen_h)

    seen = set()
    elements = []
    for acc, ext in _extents(matches, win_x, win_y):
        if ext.width < config.MIN_SIZE or ext.height < config.MIN_SIZE:
            continue
        if ext.width > max_w and ext.height > max_h:
            continue  # a container masquerading as a target
        # Require the centre inside the window; edge overlap alone would let
        # neighbouring windows leak in.
        cx, cy = ext.x + ext.width // 2, ext.y + ext.height // 2
        if not (left <= cx < right and top <= cy < bottom):
            continue

        # Web content nests actionable elements constantly -- a link wrapping
        # an image reports two boxes a few pixels apart, which produced two
        # chips for one thing you can click. Exact-ish bucketing misses those
        # because the bounds are merely similar, not equal, so also drop a
        # candidate whose centre falls inside one already accepted.
        key = (ext.x // 6, ext.y // 6, ext.width // 6, ext.height // 6)
        if key in seen:
            continue
        candidate = Element(acc, ext.x, ext.y, ext.width, ext.height)
        if _nested(candidate, elements):
            continue
        seen.add(key)

        elements.append(candidate)

    # Drop layout containers. An element enclosing several other hintable
    # elements is the box holding them, not a target -- its chip lands in
    # whatever empty corner the box happens to start at. Same rule scroll uses
    # for regions.
    elements = [
        element for element in elements
        if sum(1 for other in elements
               if other is not element and _encloses(element, other))
        < config.CONTAINER_MIN_CHILDREN
    ]

    return elements
