"""Search mode: type to filter, Tab to cycle, Enter to click.

Modelled on vim's `/` rather than on a picker dialog. Matches update on every
keystroke, the current one is highlighted, and Enter acts on it -- so the
common case (type three letters, press Enter) never involves a second phase or
a hint label at all.

Cycling is bound to Tab and the arrow keys, never to letters: every letter has
to stay available for the query, which is exactly the ambiguity that made an
earlier "type, then pick a label" version awkward to use.
"""

import sys

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from . import config, theme, x11  # noqa: E402
from .overlay import screen_size, set_identity  # noqa: E402


def _score(haystack, terms):
    """Lower is better. None means no match.

    Ranking matters more than it looks: only the first few matches get a
    number, so an exact match buried at position 12 is a match you cannot
    reach. Whole-word and prefix hits come first for that reason.
    """
    if not all(term in haystack for term in terms):
        return None
    joined = " ".join(terms)
    words = haystack.split()
    if haystack.strip() == joined:
        return 0                      # the whole label is what you typed
    if joined in words:
        return 1                      # a complete word
    if haystack.startswith(joined):
        return 2                      # start of the label
    if any(word.startswith(joined) for word in words):
        return 3                      # start of some word
    return 4                          # somewhere inside


def matches(elements, query, names=None):
    """Elements matching every whitespace-separated term, best first.

    `names` is an optional precomputed list parallel to `elements`; entries
    that are still None have not been read yet and simply do not match.
    """
    terms = query.lower().split()
    if not terms:
        return list(elements)
    scored = []
    for index, element in enumerate(elements):
        if names is None:
            haystack = f"{element.name} {element.role}".lower()
        else:
            name = names[index]
            if name is None:
                continue
            haystack = name
        rank = _score(haystack, terms)
        if rank is not None:
            # Ties keep document order, so equally good matches stay in the
            # order they appear on screen rather than jumping around.
            scored.append((rank, len(haystack), index, element))
    scored.sort(key=lambda item: item[:3])
    return [element for _, _, _, element in scored]


class SearchPrompt:
    """Collects a query, then hands the surviving elements to a callback."""

    def __init__(self, elements, on_pick, on_cancel=None):
        self.elements = elements
        self.on_pick = on_pick
        self.on_cancel = on_cancel or (lambda: None)
        self.query = ""
        self.hits = list(elements)
        self.current = 0
        self.submitted = False

        # Names are lazy because each is a D-Bus round trip. Reading them all
        # before showing the prompt cost ~2s on a busy page, which reads as
        # "search is broken" -- nothing happens, then everything does. Instead
        # the prompt opens immediately and names stream in on the idle loop.
        self.names = [None] * len(elements)
        self.indexed = 0
        GLib.idle_add(self._index_chunk)

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
        GLib.idle_add(self._grab)

    def dismiss(self):
        self._close()

    def _on_visibility(self, _widget, event):
        """Come back to the front if something is stacked over us."""
        if event.state != Gdk.VisibilityState.UNOBSCURED:
            gdk_window = self.window.get_window()
            if gdk_window is not None:
                gdk_window.raise_()
        return False

    # -- input ----------------------------------------------------------

    def _grab(self):
        gdk_window = self.window.get_window()
        if gdk_window is not None:
            seat = Gdk.Display.get_default().get_default_seat()
            status = seat.grab(gdk_window, Gdk.SeatCapabilities.KEYBOARD,
                               False, None, None, None, None)
            if status == Gdk.GrabStatus.SUCCESS:
                self._grabbed = True
                self.window.present()
                return False
        self._attempts += 1
        if self._attempts > 40:
            print("homerow: could not grab keyboard", file=sys.stderr)
            self._close()
            return False
        GLib.timeout_add(50, self._grab)
        return False

    def _on_key(self, _widget, event):
        key = event.keyval
        if key == Gdk.KEY_Escape:
            self._close()
            return True
        if key in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            if self.hits:
                self.submitted = True
                self._close()
            return True
        if key in (Gdk.KEY_Tab, Gdk.KEY_Down):
            if self.hits:
                self.current = (self.current + 1) % len(self.hits)
                self.window.queue_draw()
            return True
        if key in (Gdk.KEY_ISO_Left_Tab, Gdk.KEY_Up):
            if self.hits:
                self.current = (self.current - 1) % len(self.hits)
                self.window.queue_draw()
            return True
        if key == Gdk.KEY_BackSpace:
            self.query = self.query[:-1]
            self._refresh()
            return True

        point = Gdk.keyval_to_unicode(key)
        char = chr(point) if point else ""

        # Digits pick a labelled match outright. Letters have to stay available
        # for the query, so labels are drawn from digits instead -- that is the
        # whole reason this works without a second phase.
        if char and char in config.SEARCH_LABELS \
                and len(self.query) >= config.SEARCH_MIN_QUERY:
            index = config.SEARCH_LABELS.index(char)
            if index < len(self.hits):
                self.current = index
                self.submitted = True
                self._close()
            return True

        if char and char.isprintable():
            self.query += char
            self._refresh()
        return True

    def _draw_labels(self, cr):
        """Number the first few matches so one keypress can pick any of them."""
        cr.select_font_face(config.FONT_FAMILY)
        cr.set_font_size(config.FONT_SIZE)
        for index, element in enumerate(self.hits[:len(config.SEARCH_LABELS)]):
            label = config.SEARCH_LABELS[index]
            ext = cr.text_extents(label)
            w = ext.width + config.PAD_X * 2
            h = config.FONT_SIZE + config.PAD_Y * 2
            x = element.x - w - config.HINT_GAP
            y = element.y + (element.h - h) // 2
            if x < 0:
                x = element.x + config.HINT_GAP
            x = min(max(x, 0), max(self.width - w, 0))
            y = min(max(y, 0), max(self.height - h, 0))

            key = "chip" if index == self.current else "chip_matched"
            cr.set_source_rgba(*self.colors[key])
            cr.rectangle(x, y, w, h)
            cr.fill()
            cr.set_source_rgba(*self.colors["ink"])
            cr.move_to(x + config.PAD_X, y + h - config.PAD_Y - 2)
            cr.show_text(label)

    def _index_chunk(self):
        """Read the next few names, then yield back to the main loop."""
        end = min(self.indexed + config.SEARCH_INDEX_CHUNK,
                  len(self.elements))
        for index in range(self.indexed, end):
            element = self.elements[index]
            try:
                parts = [element.name, element.role]
                # Many web controls carry no accessible name but do expose
                # their visible text, and that is what you would search for.
                accessible = element.accessible
                if accessible is not None:
                    try:
                        text_iface = accessible.get_text_iface()
                        if text_iface is not None:
                            count = text_iface.get_character_count()
                            if count:
                                parts.append(text_iface.get_text(
                                    0, min(count, config.SEARCH_TEXT_CHARS)))
                    except Exception:
                        pass
                self.names[index] = " ".join(p for p in parts if p).lower()
            except Exception:
                self.names[index] = ""
        self.indexed = end
        if self.query:
            self._refresh(keep_current=True)
        self.window.queue_draw()
        return self.indexed < len(self.elements)

    def _refresh(self, keep_current=False):
        previous = self.hits[self.current] if (
            keep_current and self.current < len(self.hits)) else None
        self.hits = matches(self.elements, self.query, self.names)
        # Editing the query restarts at the first match; results arriving from
        # background indexing must not move the selection out from under you.
        self.current = self.hits.index(previous) if (
            previous is not None and previous in self.hits) else 0
        self.window.queue_draw()

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

        if self.submitted and self.hits:
            self.on_pick(self.hits[min(self.current, len(self.hits) - 1)])
        else:
            self.on_cancel()

    # -- drawing --------------------------------------------------------

    def _on_draw(self, widget, cr):
        cr.set_operator(1)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(2)

        if config.DIM_BACKGROUND and self.translucent:
            cr.set_source_rgba(*self.colors["dim"])
            cr.paint()

        gdk_window = widget.get_window()
        if gdk_window is not None:
            _, origin_x, origin_y = gdk_window.get_origin()
            cr.translate(-origin_x, -origin_y)

        cr.select_font_face(config.FONT_FAMILY)
        cr.set_font_size(config.FONT_SIZE)

        # Outline what currently matches, so the query can be refined by eye
        # before committing to hint selection. Nothing is outlined for an empty
        # query: "everything matches" is true but drawing a box round every
        # element just looks like noise.
        if len(self.query) >= config.SEARCH_MIN_QUERY:
            cr.set_line_width(2)
            cr.set_source_rgba(*self.colors["chip_matched"])
            for index, element in enumerate(self.hits[:config.SEARCH_MAX_OUTLINES]):
                if index == self.current:
                    continue
                cr.rectangle(element.x, element.y, element.w, element.h)
            cr.stroke()

            # The current match is what Enter acts on, so it has to be
            # obviously different from the rest, not just one of many boxes.
            if self.current < len(self.hits):
                element = self.hits[self.current]
                cr.set_source_rgba(*self.colors["chip"])
                cr.set_line_width(3)
                cr.rectangle(element.x - 1, element.y - 1,
                             element.w + 2, element.h + 2)
                cr.stroke()
                cr.set_source_rgba(*self.colors["chip"][:3], 0.22)
                cr.rectangle(element.x, element.y, element.w, element.h)
                cr.fill()

            self._draw_labels(cr)

        label = f"search: {self.query}_"
        if self.hits and self.query:
            count = f"{self.current + 1}/{len(self.hits)}"
        else:
            count = f"{len(self.hits)} match" + (
                "" if len(self.hits) == 1 else "es")
        if self.indexed < len(self.elements):
            count += f"  (reading {self.indexed}/{len(self.elements)})"
        text = f"{label}    {count}    1-9 pick · tab next · enter click · esc"
        ext = cr.text_extents(text)
        pad = 8
        w, h = ext.width + pad * 2, config.FONT_SIZE + pad * 2
        x = max((self.width - w) // 2, 0)
        y = max(self.height - h - config.LEGEND_MARGIN, 0)

        cr.set_source_rgba(*self.colors["chip"])
        cr.rectangle(x, y, w, h)
        cr.fill()
        cr.set_source_rgba(*self.colors["ink"])
        cr.move_to(x + pad, y + h - pad - 2)
        cr.show_text(text)
        return True
