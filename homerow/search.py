"""Search mode: type part of a label, then hint only what matched.

Two phases on purpose. Filtering and hinting at the same time would make every
keystroke ambiguous -- is `a` a search character or the label `a`? Typing the
query, then pressing Enter to switch to hint selection, keeps both alphabets
unambiguous and means the query can contain any character.
"""

import sys

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from . import config, theme  # noqa: E402
from .overlay import screen_size, set_identity  # noqa: E402


def matches(elements, query):
    """Elements whose name or role contains every whitespace-separated term."""
    terms = query.lower().split()
    if not terms:
        return list(elements)
    found = []
    for element in elements:
        haystack = f"{element.name} {element.role}".lower()
        if all(term in haystack for term in terms):
            found.append(element)
    return found


class SearchPrompt:
    """Collects a query, then hands the surviving elements to a callback."""

    def __init__(self, elements, on_query, on_cancel=None):
        self.elements = elements
        self.on_query = on_query
        self.on_cancel = on_cancel or (lambda: None)
        self.query = ""
        self.hits = list(elements)
        self.submitted = False

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

        self.window.add_events(Gdk.EventMask.KEY_PRESS_MASK)
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
        if key == Gdk.KEY_BackSpace:
            self.query = self.query[:-1]
            self._refresh()
            return True

        point = Gdk.keyval_to_unicode(key)
        if point and chr(point).isprintable():
            self.query += chr(point)
            self._refresh()
        return True

    def _refresh(self):
        self.hits = matches(self.elements, self.query)
        self.window.queue_draw()

    def _close(self):
        if self._grabbed:
            Gdk.Display.get_default().get_default_seat().ungrab()
            self._grabbed = False
        self.window.destroy()
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        Gdk.Display.get_default().sync()

        if self.submitted:
            self.on_query(self.hits)
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
        # before committing to hint selection.
        cr.set_line_width(2)
        cr.set_source_rgba(*self.colors["chip_matched"])
        for element in self.hits[:config.MAX_ELEMENTS]:
            cr.rectangle(element.x, element.y, element.w, element.h)
        cr.stroke()

        label = f"search: {self.query}"
        count = f"{len(self.hits)} match" + ("" if len(self.hits) == 1 else "es")
        text = f"{label}    {count}    enter to pick, esc to cancel"
        ext = cr.text_extents(text)
        pad = 8
        w, h = ext.width + pad * 2, config.FONT_SIZE + pad * 2
        x = max((self.width - w) // 2, 0)
        y = max(self.height - h - 40, 0)

        cr.set_source_rgba(*self.colors["chip"])
        cr.rectangle(x, y, w, h)
        cr.fill()
        cr.set_source_rgba(*self.colors["ink"])
        cr.move_to(x + pad, y + h - pad - 2)
        cr.show_text(text)
        return True
