"""Fullscreen hint overlay.

Window type matters here. An override-redirect POPUP is the obvious choice
for an unmanaged overlay, but under qtile + picom it maps without ever being
composited -- it draws to nothing. A TOPLEVEL carrying the DOCK type hint
composites correctly and qtile keeps it out of the tiling layout. We never set
_NET_WM_STRUT, so no space is reserved for it either.
"""

import sys

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from . import config, theme  # noqa: E402

CANCEL_KEYS = {Gdk.KEY_Escape, Gdk.KEY_q}
BUTTON_PREFIXES = {",": 3, ".": 2}  # right, middle


def set_identity():
    """Give our windows a stable WM_CLASS of `homerow`.

    Compositor rules match on this. Without it the class is derived from the
    process name, which differs between the daemon and the standalone CLI, so
    picom rules would apply to one and not the other. Must run before any
    window is realized.
    """
    GLib.set_prgname(config.WM_CLASS)
    Gdk.set_program_class(config.WM_CLASS)


def screen_size():
    """Size of the whole virtual screen, spanning every monitor.

    Gdk.Screen.get_width/height would do it but are deprecated and warn on
    every run; the monitor API is also correct for multi-head setups.
    """
    display = Gdk.Display.get_default()
    right = bottom = 0
    for i in range(display.get_n_monitors()):
        geometry = display.get_monitor(i).get_geometry()
        right = max(right, geometry.x + geometry.width)
        bottom = max(bottom, geometry.y + geometry.height)
    return right, bottom


class Overlay:
    """Shows labelled hints and resolves one keystroke sequence to a choice.

    `on_choose(element, button, modifiers)` runs after the overlay is gone, so
    the click lands on the application underneath rather than on us.
    """

    def __init__(self, elements, labels, on_choose, on_done=None):
        self.elements = elements
        self.labels = labels
        self.on_choose = on_choose
        # Standalone runs own a Gtk main loop and quit it here; the daemon
        # keeps one loop for its whole lifetime and passes nothing.
        self.on_done = on_done or (lambda: None)

        self.typed = ""
        self.button = 1
        self.result = None
        self._grabbed = False
        self._grab_attempts = 0

        set_identity()
        # Per open, so a theme-apply between hints is picked up.
        self.colors = theme.palette()
        screen = Gdk.Screen.get_default()
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

        visual = screen.get_rgba_visual()
        self.translucent = visual is not None
        if self.translucent:
            self.window.set_visual(visual)

        self.window.add_events(
            Gdk.EventMask.KEY_PRESS_MASK | Gdk.EventMask.BUTTON_PRESS_MASK
        )
        self.window.connect("draw", self._on_draw)
        self.window.connect("key-press-event", self._on_key)
        self.window.connect("button-press-event", lambda *_: self._close())

    def run(self):
        """Show the overlay and drive a private main loop until dismissed."""
        self.on_done = Gtk.main_quit
        self.show()
        Gtk.main()

    def show(self):
        """Map and grab, returning immediately.

        `on_choose` fires later, once the overlay has been torn down.
        """
        self.window.show_all()
        # Position after mapping; a managed window may be placed by the WM
        # before we get a say.
        self.window.move(0, 0)
        self.window.resize(self.width, self.height)
        # Ask for true fullscreen so the WM cannot shrink us to the area left
        # over by its bar, which would clip hints near the screen edges.
        self.window.fullscreen()
        # A fullscreen client can end up stacked above a freshly mapped dock,
        # which leaves the overlay drawing to nothing.
        gdk_window = self.window.get_window()
        if gdk_window is not None:
            gdk_window.raise_()
        GLib.idle_add(self._grab)

    # -- input ----------------------------------------------------------

    def _grab(self):
        """Take the keyboard, retrying until the window is grabbable.

        A grab attempted before the window is viewable fails with
        NOT_VIEWABLE, and the overlay would then silently swallow nothing
        while every keystroke went to the application underneath.
        """
        gdk_window = self.window.get_window()
        if gdk_window is not None:
            seat = Gdk.Display.get_default().get_default_seat()
            # owner_events=False makes the grab exclusive. With True, key
            # events can still be routed by focus, and this window carries the
            # DOCK hint so the WM may never focus it -- keystrokes then leak to
            # the application underneath and the overlay misses them.
            status = seat.grab(
                gdk_window, Gdk.SeatCapabilities.KEYBOARD,
                False, None, None, None, None,
            )
            if status == Gdk.GrabStatus.SUCCESS:
                self.window.present()
                self._grabbed = True
                return False

        self._grab_attempts += 1
        if self._grab_attempts > 40:  # ~2s
            print("homerow: could not grab keyboard", file=sys.stderr)
            self._close()
            return False
        GLib.timeout_add(50, self._grab)
        return False

    def _ungrab(self):
        if self._grabbed:
            Gdk.Display.get_default().get_default_seat().ungrab()
            self._grabbed = False

    def _on_key(self, _widget, event):
        key = event.keyval
        if config.DEBUG_KEYS:
            unicode_point = Gdk.keyval_to_unicode(event.keyval)
            print(f"homerow: key {key} "
                  f"{chr(unicode_point) if unicode_point else ''!r} "
                  f"typed={self.typed!r}", flush=True)

        if key in CANCEL_KEYS and not self.typed:
            self._close()
            return True
        if key == Gdk.KEY_Escape:
            self._close()
            return True
        if key == Gdk.KEY_BackSpace:
            self.typed = self.typed[:-1]
            self.window.queue_draw()
            return True

        char = Gdk.keyval_to_unicode(Gdk.keyval_to_lower(key))
        if not char:
            return True
        char = chr(char)

        if char in BUTTON_PREFIXES and not self.typed:
            self.button = BUTTON_PREFIXES[char]
            self.window.queue_draw()
            return True

        if char not in config.HINT_ALPHABET:
            return True

        candidate = self.typed + char
        if not any(label.startswith(candidate) for label in self.labels):
            return True  # dead end; ignore rather than dropping what was typed

        self.typed = candidate
        if candidate in self.labels:
            self._choose(self.labels.index(candidate), event.state)
        else:
            self.window.queue_draw()
        return True

    def _choose(self, index, state):
        modifiers = []
        if state & Gdk.ModifierType.SHIFT_MASK:
            modifiers.append("shift")
        if state & Gdk.ModifierType.CONTROL_MASK:
            modifiers.append("ctrl")
        self.result = (self.elements[index], self.button, modifiers)
        self._close()

    def dismiss(self):
        """Tear down without selecting anything."""
        self.result = None
        self._close()

    def _close(self):
        self._ungrab()
        self.window.destroy()
        # Let X tear the window down before anything tries to click through
        # where it used to be, or the click lands on the overlay.
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        Gdk.Display.get_default().sync()

        if self.result is not None:
            element, button, modifiers = self.result
            self.result = None
            self.on_choose(element, button, modifiers)
        self.on_done()

    # -- drawing --------------------------------------------------------

    def _on_draw(self, widget, cr):
        cr.set_operator(1)  # SOURCE -- replace, do not blend with stale content
        if self.translucent:
            cr.set_source_rgba(0, 0, 0, 0)
        else:
            cr.set_source_rgba(0.1, 0.1, 0.1, 1)
        cr.paint()
        cr.set_operator(2)  # OVER

        if config.DIM_BACKGROUND and self.translucent:
            cr.set_source_rgba(*self.colors["dim"])
            cr.paint()

        # Element coordinates are absolute screen coordinates, but the WM does
        # not necessarily honour move(0, 0) -- qtile offsets this window by its
        # bar height. Translating by the real origin keeps hints on target
        # wherever the window actually landed.
        gdk_window = widget.get_window()
        if gdk_window is not None:
            _, origin_x, origin_y = gdk_window.get_origin()
            cr.translate(-origin_x, -origin_y)

        cr.select_font_face(config.FONT_FAMILY)
        cr.set_font_size(config.FONT_SIZE)

        for element, label in zip(self.elements, self.labels):
            if self.typed and not label.startswith(self.typed):
                continue
            self._draw_hint(cr, element, label)
        return True

    def _draw_hint(self, cr, element, label):
        ext = cr.text_extents(label)
        w = ext.width + config.PAD_X * 2
        h = config.FONT_SIZE + config.PAD_Y * 2

        # Sitting the chip in the margin keeps the target's own first
        # characters legible; overlapping them made dense lists hard to read.
        # Vertically centred so it reads as belonging to that row.
        if config.HINT_PLACEMENT == "margin":
            x = element.x - w - config.HINT_GAP
            y = element.y + (element.h - h) // 2
            if x < 0:
                x = element.x + config.HINT_GAP
        else:
            x, y = element.x, element.y

        x = min(max(x, 0), max(self.width - w, 0))
        y = min(max(y, 0), max(self.height - h, 0))

        is_window = getattr(element, "kind", "element") == "window"
        if self.typed:
            chip, ink, ink_typed = ("chip_matched", "ink", "ink_typed")
        elif is_window:
            chip, ink, ink_typed = ("chip_window", "ink_window", "ink_window")
        else:
            chip, ink, ink_typed = ("chip", "ink", "ink_typed")

        cr.set_source_rgba(*self.colors[chip])
        _rounded_rect(cr, x, y, w, h, config.RADIUS)
        cr.fill()

        # Typed characters are dimmed so the eye lands on what is left to press.
        baseline = y + h - config.PAD_Y - 2
        cx = x + config.PAD_X
        for i, char in enumerate(label):
            typed = i < len(self.typed)
            cr.set_source_rgba(
                *(self.colors[ink_typed] if typed else self.colors[ink])
            )
            cr.move_to(cx, baseline)
            cr.show_text(char)
            cx += cr.text_extents(char).x_advance


def _rounded_rect(cr, x, y, w, h, r):
    import math
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()
