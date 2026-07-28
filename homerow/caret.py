"""Caret mode: a real text cursor driven by vim motions.

The earlier visual mode sent shift+arrow keys and relied on the application
already having a caret, which a web page does not. This drives AT-SPI's Text
interface instead: the caret is an offset we own, motions are computed against
the actual string, and the cursor and selection are drawn from
get_character_extents / get_range_extents. That gives a visible cursor and vim
word motions in any app whose Text interface is real.

It is not universal, and cannot be: Qt WebEngine publishes no text at all, so
qutebrowser pages have nothing to put a caret into. Native toolkits and
Chromium do.
"""

import subprocess

import cairo
import gi

gi.require_version("Atspi", "2.0")
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Atspi, Gdk, GLib, Gtk  # noqa: E402

from . import config, elements, theme  # noqa: E402
from .overlay import screen_size, set_identity  # noqa: E402

WORD = Atspi.TextGranularity.WORD
LINE = Atspi.TextGranularity.LINE


def _text_of(iface, start, end):
    """Atspi.Text.get_text, called unbound.

    `iface.get_text(...)` resolves to the deprecated Accessible.get_text and
    raises; the Text interface method has to be reached explicitly.
    """
    try:
        return Atspi.Text.get_text(iface, start, end) or ""
    except Exception:
        return ""


def collect(screen_w, screen_h):
    """Text-bearing elements in the focused window, biggest first."""
    window = elements.active_window()
    if window is None:
        return []
    pid, win_x, win_y, win_w, win_h = window
    app = elements._app_for_pid(pid)
    if app is None:
        return []

    left, top = max(win_x, 0), max(win_y, 0)
    right, bottom = min(win_x + win_w, screen_w), min(win_y + win_h, screen_h)

    # Ask for text-bearing roles rather than walking. The Text interface is not
    # a role and cannot be queried directly, but the roles that carry text are
    # a short list -- and walking for it cost ~2.5s, which is unusable.
    collection = app.get_collection_iface()
    if collection is None:
        return []
    roles = []
    for name in config.CARET_ROLES:
        role = getattr(Atspi.Role, name, None)
        if role is not None:
            roles.append(role)
    rule = Atspi.MatchRule.new(
        Atspi.StateSet.new(
            [Atspi.StateType.SHOWING, Atspi.StateType.VISIBLE]
        ), Atspi.CollectionMatchType.ALL,
        {}, Atspi.CollectionMatchType.ALL,
        roles, Atspi.CollectionMatchType.ANY,
        [], Atspi.CollectionMatchType.ALL, False,
    )
    try:
        matches = collection.get_matches(
            rule, Atspi.CollectionSortOrder.CANONICAL, config.MAX_ELEMENTS,
            True)
    except Exception:
        return []

    found = []
    for accessible, ext in elements._extents(matches, win_x, win_y):
        cx, cy = ext.x + ext.width // 2, ext.y + ext.height // 2
        if not (left <= cx < right and top <= cy < bottom):
            continue
        if ext.width <= 0 or ext.height <= 0:
            continue
        try:
            iface = accessible.get_text_iface()
            if iface is None or \
                    iface.get_character_count() < config.CARET_MIN_CHARS:
                continue
        except Exception:
            continue
        found.append(
            elements.Element(accessible, ext.x, ext.y, ext.width, ext.height))

    found.sort(key=lambda e: e.w * e.h, reverse=True)
    return found


class CaretSession:
    """A vim-style caret over one text element."""

    def __init__(self, element, on_done=None):
        self.element = element
        self.on_done = on_done or (lambda: None)
        self.iface = element.accessible.get_text_iface()
        self.length = self.iface.get_character_count() if self.iface else 0
        self.text = _text_of(self.iface, 0, self.length)
        self.offset = 0
        self.anchor = None          # set when visual mode is on
        self.pending_g = False

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
            gdk_window.input_shape_combine_region(cairo.Region(), 0, 0)
        GLib.idle_add(self._grab)

    def dismiss(self):
        self._close()

    # -- motions --------------------------------------------------------

    def _word_bounds(self, offset):
        try:
            span = Atspi.Text.get_string_at_offset(self.iface, offset, WORD)
            return span.start_offset, span.end_offset
        except Exception:
            return offset, offset

    def _line_bounds(self, offset):
        try:
            span = Atspi.Text.get_string_at_offset(self.iface, offset, LINE)
            return span.start_offset, span.end_offset
        except Exception:
            return offset, offset

    def _next_word(self):
        _, end = self._word_bounds(self.offset)
        offset = min(max(end, self.offset + 1), self.length)
        # Land on the first character of the next word, not the space.
        while offset < self.length and self.text[offset:offset + 1].isspace():
            offset += 1
        return offset

    def _prev_word(self):
        offset = max(self.offset - 1, 0)
        while offset > 0 and self.text[offset:offset + 1].isspace():
            offset -= 1
        start, _ = self._word_bounds(offset)
        return start

    def _word_end(self):
        offset = min(self.offset + 1, self.length)
        while offset < self.length and self.text[offset:offset + 1].isspace():
            offset += 1
        _, end = self._word_bounds(offset)
        return max(end - 1, self.offset)

    def _line_move(self, delta):
        start, end = self._line_bounds(self.offset)
        column = self.offset - start
        if delta < 0:
            if start == 0:
                return self.offset
            new_start, _ = self._line_bounds(start - 1)
            target_start, target_end = new_start, start - 1
        else:
            if end >= self.length:
                return self.offset
            target_start, target_end = self._line_bounds(end + 1)
            target_end = max(target_end - 1, target_start)
        return min(target_start + column, target_end)

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
                self._sync_caret()
                return False
        self._attempts += 1
        if self._attempts > 40:
            self._close()
            return False
        GLib.timeout_add(50, self._grab)
        return False

    def _on_key(self, _widget, event):
        key = event.keyval
        moved = True

        if key == Gdk.KEY_Escape:
            if self.anchor is not None:
                self.anchor = None
                self.window.queue_draw()
                return True
            self._close()
            return True
        if key == Gdk.KEY_v:
            self.anchor = None if self.anchor is not None else self.offset
            self.window.queue_draw()
            return True
        if key == Gdk.KEY_y:
            self._yank()
            return True
        if key == Gdk.KEY_g:
            if self.pending_g:
                self.pending_g = False
                self.offset = 0
            else:
                self.pending_g = True
                return True
        else:
            self.pending_g = False

        if key in (Gdk.KEY_l, Gdk.KEY_Right):
            self.offset = min(self.offset + 1, max(self.length - 1, 0))
        elif key in (Gdk.KEY_h, Gdk.KEY_Left):
            self.offset = max(self.offset - 1, 0)
        elif key in (Gdk.KEY_j, Gdk.KEY_Down):
            self.offset = self._line_move(1)
        elif key in (Gdk.KEY_k, Gdk.KEY_Up):
            self.offset = self._line_move(-1)
        elif key == Gdk.KEY_w:
            self.offset = self._next_word()
        elif key == Gdk.KEY_b:
            self.offset = self._prev_word()
        elif key == Gdk.KEY_e:
            self.offset = self._word_end()
        elif key == Gdk.KEY_0:
            self.offset = self._line_bounds(self.offset)[0]
        elif key == Gdk.KEY_dollar:
            self.offset = max(self._line_bounds(self.offset)[1] - 1, 0)
        elif key == Gdk.KEY_G:
            self.offset = max(self.length - 1, 0)
        elif key != Gdk.KEY_g:
            moved = False

        if moved:
            self._sync_caret()
            self.window.queue_draw()
        return True

    def _sync_caret(self):
        """Mirror our offset into the app, so its own cursor follows along."""
        try:
            Atspi.Text.set_caret_offset(self.iface, self.offset)
        except Exception:
            pass

    def _selection(self):
        if self.anchor is None:
            return None
        start, end = sorted((self.anchor, self.offset))
        return start, min(end + 1, self.length)

    def _yank(self):
        span = self._selection()
        if span is None:
            span = self._word_bounds(self.offset)
        text = _text_of(self.iface, *span)
        if text:
            Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(text, -1)
            Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).store()
        self._close()

    def _close(self):
        if self._grabbed:
            Gdk.Display.get_default().get_default_seat().ungrab()
            self._grabbed = False
        self.window.destroy()
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        Gdk.Display.get_default().sync()
        self.on_done()

    # -- drawing --------------------------------------------------------

    def _extents(self, offset):
        try:
            ext = Atspi.Text.get_character_extents(
                self.iface, offset, Atspi.CoordType.SCREEN)
            if ext.width <= 0:
                ext.width = 8
            if ext.height <= 0:
                ext.height = config.FONT_SIZE + 4
            return ext
        except Exception:
            return None

    def _on_draw(self, widget, cr):
        cr.set_operator(1)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(2)

        gdk_window = widget.get_window()
        if gdk_window is not None:
            _, ox, oy = gdk_window.get_origin()
            cr.translate(-ox, -oy)

        span = self._selection()
        if span is not None:
            try:
                rect = Atspi.Text.get_range_extents(
                    self.iface, span[0], span[1], Atspi.CoordType.SCREEN)
                cr.set_source_rgba(*self.colors["chip_matched"][:3], 0.35)
                cr.rectangle(rect.x, rect.y, rect.width, rect.height)
                cr.fill()
            except Exception:
                pass

        ext = self._extents(self.offset)
        if ext is not None:
            # A block cursor when selecting, a bar when just moving -- the same
            # distinction vim draws between visual and normal mode.
            cr.set_source_rgba(*self.colors["chip"])
            if self.anchor is not None:
                cr.rectangle(ext.x, ext.y, ext.width, ext.height)
                cr.fill()
            else:
                cr.rectangle(ext.x, ext.y, 2, ext.height)
                cr.fill()

        mode = "VISUAL" if self.anchor is not None else "CARET"
        legend = (f"{mode}   h/j/k/l move   w/b/e word   0/$ line   "
                  f"gg/G doc   v select   y yank   esc")
        cr.select_font_face(config.FONT_FAMILY)
        cr.set_font_size(config.FONT_SIZE)
        text_ext = cr.text_extents(legend)
        pad = 8
        w, h = text_ext.width + pad * 2, config.FONT_SIZE + pad * 2
        x = max((self.width - w) // 2, 0)
        y = max(self.height - h - 40, 0)
        cr.set_source_rgba(*self.colors["chip"])
        cr.rectangle(x, y, w, h)
        cr.fill()
        cr.set_source_rgba(*self.colors["ink"])
        cr.move_to(x + pad, y + h - pad - 2)
        cr.show_text(legend)
        return True


def _clipboard_fallback(text):
    try:
        subprocess.run(["xclip", "-selection", "clipboard"],
                       input=text.encode(), timeout=2, check=False)
    except (OSError, subprocess.SubprocessError):
        pass
