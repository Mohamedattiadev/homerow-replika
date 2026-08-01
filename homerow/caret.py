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

import re
import subprocess
import sys
from dataclasses import dataclass

import cairo
import gi

gi.require_version("Atspi", "2.0")
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Atspi, Gdk, GLib, Gtk  # noqa: E402

from . import config, elements, theme, x11  # noqa: E402
from .overlay import (  # noqa: E402
    draw_legend, normalize_key, place_chip, screen_size, set_identity,
)

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


@dataclass
class WordHit:
    """One word, inside one text block, that matched a caret-search query.

    Mirrors elements.Element's geometry fields so it can go through
    overlay.place_chip unchanged, but the target is a caret *position*
    (block + character offset) rather than a clickable accessible.
    """

    block: object
    offset: int
    word: str
    x: int
    y: int
    w: int
    h: int

    kind = "word"

    @property
    def center(self):
        return self.x + self.w // 2, self.y + self.h // 2


_WORD_RE = re.compile(r"\S+")


def word_hits(block_texts, query, limit=None):
    """WordHit for every word across `block_texts` containing `query`.

    `block_texts` is a list of (block, text) pairs, read once up front so
    repeated searches -- one per keystroke, as the query grows -- don't
    re-read each block's whole text over D-Bus every time; only the
    matched words' on-screen extents are new round trips per keystroke,
    and there are far fewer of those than there is text.
    """
    if not query:
        return []
    needle = query.lower()
    limit = limit or config.CARET_SEARCH_MAX_HITS
    hits = []
    for block, text in block_texts:
        iface = block.accessible.get_text_iface()
        if iface is None:
            continue
        for match in _WORD_RE.finditer(text):
            word = match.group(0)
            if needle not in word.lower():
                continue
            start, end = match.start(), match.end()
            try:
                ext = Atspi.Text.get_range_extents(
                    iface, start, end, Atspi.CoordType.SCREEN)
            except Exception:
                continue
            if ext.width <= 0 or ext.height <= 0:
                continue
            hits.append(WordHit(
                block, start, word, ext.x, ext.y, ext.width, ext.height))
            if len(hits) >= limit:
                return hits
    return hits


def collect(screen_w, screen_h, min_chars=None, require_text=True):
    """Text-bearing elements in the focused window, biggest first.

    `min_chars` defaults to the caret threshold. Search passes a much lower
    one: a sidebar entry reading "Vite" is four characters, is a LABEL rather
    than a link, and was therefore in neither the hintable set nor the caret
    set -- so searching for it found nothing at all.
    """
    if min_chars is None:
        min_chars = config.CARET_MIN_CHARS
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
    # Same foreground-tab restriction as hint mode: a browser keeping five
    # tabs alive otherwise offers five pages of text stacked on one viewport.
    frame = elements.active_frame(app, (win_x, win_y, win_w, win_h))
    scope = frame if frame is not None else app
    try:
        title = x11.window_name(x11.active_window_id() or 0) if \
            x11.available() else ""
        document = elements.active_document(scope, title)
        if document is not None:
            scope = document
    except Exception:
        pass

    collection = scope.get_collection_iface()
    if collection is None:
        return []

    def query(names):
        roles = []
        for name in names:
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
            return collection.get_matches(
                rule, Atspi.CollectionSortOrder.CANONICAL,
                config.MAX_ELEMENTS, True)
        except Exception:
            return []

    # Search wants everything; caret only pays for the numerous roles when the
    # cheap ones turn up nothing to put a cursor in.
    matches = query(config.CARET_ROLES)
    if not require_text:
        matches = list(matches) + list(query(config.CARET_ROLES_FALLBACK))

    found = []
    for accessible, ext in elements._extents(matches, win_x, win_y):
        cx, cy = ext.x + ext.width // 2, ext.y + ext.height // 2
        if not (left <= cx < right and top <= cy < bottom):
            continue
        if ext.width <= 0 or ext.height <= 0:
            continue
        if require_text:
            # Caret mode needs a real Text interface to put an offset into.
            try:
                iface = accessible.get_text_iface()
                if iface is None or \
                        iface.get_character_count() < min_chars:
                    continue
            except Exception:
                continue
        # Search does not: Qt WebEngine labels carry a name and no Text
        # interface at all, so requiring text discarded every sidebar entry.
        # Names are read later, asynchronously, by the search indexer -- doing
        # it here would be a D-Bus round trip per candidate.
        found.append(
            elements.Element(accessible, ext.x, ext.y, ext.width, ext.height))

    if not found and require_text:
        found = _shape(query(config.CARET_ROLES_FALLBACK), win_x, win_y,
                       left, top, right, bottom, min_chars, require_text)

    found.sort(key=lambda e: e.w * e.h, reverse=True)
    return found


def _shape(matches, win_x, win_y, left, top, right, bottom, min_chars,
           require_text):
    """Filter raw matches down to usable text blocks."""
    out = []
    for accessible, ext in elements._extents(matches, win_x, win_y):
        cx, cy = ext.x + ext.width // 2, ext.y + ext.height // 2
        if not (left <= cx < right and top <= cy < bottom):
            continue
        if ext.width <= 0 or ext.height <= 0:
            continue
        if require_text:
            try:
                iface = accessible.get_text_iface()
                if iface is None or \
                        iface.get_character_count() < min_chars:
                    continue
            except Exception:
                continue
        out.append(
            elements.Element(accessible, ext.x, ext.y, ext.width, ext.height))
    return out


def best(blocks):
    """The text block to put the caret in without asking."""
    if not blocks:
        return None
    position = _pointer_position()
    if position:
        px, py = position
        under = [b for b in blocks
                 if b.x <= px < b.x + b.w and b.y <= py < b.y + b.h]
        if under:
            return min(under, key=lambda b: b.w * b.h)
    return blocks[0]


def _pointer_position():
    return x11.pointer_position() if x11.available() else None


class CaretSession:
    """A vim-style caret over one text element."""

    def __init__(self, element, on_done=None, blocks=None, on_search=None):
        self.element = element
        self.on_done = on_done or (lambda: None)
        # / reopens caret search from inside caret mode, so landing on one
        # word and then wanting a different one doesn't mean leaving the
        # mode entirely and pressing the hotkey again from scratch -- see
        # _on_key's Gdk.KEY_slash handling.
        self.on_search = on_search or (lambda: None)
        self.iface = element.accessible.get_text_iface()
        self.length = self.iface.get_character_count() if self.iface else 0
        self.text = _text_of(self.iface, 0, self.length)
        self.blocks = list(blocks) if blocks else [element]
        try:
            self.index = self.blocks.index(element)
        except ValueError:
            self.index = 0
        self.offset = 0
        self.anchor = None          # set when visual mode is on
        self.linewise = False       # True for V (visual line), False for v
        self.pending_g = False
        self.pending_y = False
        self.pending_d = False

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
            gdk_window.input_shape_combine_region(cairo.Region(), 0, 0)
        GLib.idle_add(self._grab)
        # Never hold the keyboard indefinitely. The grab is exclusive, so
        # while a session is open every other binding on the desktop is dead
        # -- including the ones that would close it. A session left open by
        # accident is indistinguishable from the keyboard having broken.
        self._idle = GLib.timeout_add_seconds(
            config.IDLE_TIMEOUT_S, self._on_idle)

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
        if event.state != Gdk.VisibilityState.UNOBSCURED:
            gdk_window = self.window.get_window()
            if gdk_window is not None:
                gdk_window.raise_()
        return False

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
                # The modifier that launched this is probably still held, and
                # the grab will swallow its release. Clear it now so typing a
                # label is not read as alt+label, and so the desktop is never
                # left believing a modifier is down.
                x11.release_modifiers()
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
        self._touch()
        key = normalize_key(event.keyval, event.state)
        moved = True
        if config.DEBUG_KEYS:
            x11.debug_log(f"[caret] key={Gdk.keyval_name(key)!r} "
                          f"offset={self.offset} anchor={self.anchor}")

        if key == Gdk.KEY_Escape:
            if self.anchor is not None:
                self.anchor = None
                self.linewise = False
                self.window.queue_draw()
                return True
            self._close()
            return True
        if key == Gdk.KEY_slash:
            # Reopen caret search from here, same as vim's / -- landing on
            # one word and then wanting a different one shouldn't mean
            # backing all the way out and pressing the hotkey again.
            self._close(reopen_search=True)
            return True
        # Digits jump straight to a block. Tab was the only way to reach one,
        # which on a page with dozens of blocks meant pressing it dozens of
        # times. Digits are free here: the motions are all letters.
        point = Gdk.keyval_to_unicode(key)
        char = chr(point) if point else ""
        if char and char in config.CARET_LABELS and len(self.blocks) > 1:
            index = config.CARET_LABELS.index(char)
            if index < len(self.blocks):
                self.index = index
                self._use_block(self.blocks[index])
            return True

        if key in (Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab) \
                and len(self.blocks) > 1:
            step = -1 if key == Gdk.KEY_ISO_Left_Tab else 1
            self.index = (self.index + step) % len(self.blocks)
            self._use_block(self.blocks[self.index])
            return True

        if key == Gdk.KEY_v:
            self.pending_g = self.pending_y = False
            if self.anchor is not None and not self.linewise:
                self.anchor = None
            else:
                self.anchor, self.linewise = self.offset, False
            self.window.queue_draw()
            return True
        if key == Gdk.KEY_V:
            self.pending_g = self.pending_y = False
            if self.anchor is not None and self.linewise:
                self.anchor, self.linewise = None, False
            else:
                self.anchor, self.linewise = self.offset, True
            self.window.queue_draw()
            return True
        if key == Gdk.KEY_x:
            self.pending_g = self.pending_y = self.pending_d = False
            self._delete_selection_or_char()
            return True
        if key == Gdk.KEY_d:
            self.pending_g = self.pending_y = False
            if self.anchor is not None:
                # Visual mode: d deletes the selection now, same as vim --
                # there is no motion left to wait for.
                self.pending_d = False
                self._delete_selection_or_char()
                return True
            if self.pending_d:
                self.pending_d = False
                self._delete_line()
            else:
                self.pending_d = True
                self.window.queue_draw()
            return True
        if key == Gdk.KEY_p:
            self.pending_g = self.pending_y = self.pending_d = False
            self._put(self._clipboard_text())
            return True
        if key == Gdk.KEY_y:
            self.pending_g = False
            self.pending_d = False
            if self.anchor is not None:
                # Visual mode: y always yanks the selection immediately,
                # same as vim -- there is no motion left to wait for.
                self.pending_y = False
                self._yank()
                return True
            # Normal mode: y is a pending operator, same as real vim -- a
            # lone y is not itself a complete command. Only yy (mirroring
            # gg) is supported; any other key cancels it, below.
            if self.pending_y:
                self.pending_y = False
                self._yank_line()
            else:
                self.pending_y = True
                self.window.queue_draw()
            return True
        if key == Gdk.KEY_g:
            self.pending_y = False
            if self.pending_g:
                self.pending_g = False
                self.offset = 0
            else:
                self.pending_g = True
                return True
        else:
            if self.pending_y or self.pending_d:
                self.pending_y = self.pending_d = False
                self.window.queue_draw()
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
        # Caps Lock inverts every letter's case, and there is no way to tell
        # a genuine `G` from an accidentally-capitalised `g` once it is on --
        # see normalize_key. Home/End are not letters, so they reach the same
        # ends regardless of Caps Lock.
        elif key == Gdk.KEY_Home:
            self.offset = 0
        elif key == Gdk.KEY_End:
            self.offset = max(self.length - 1, 0)
        elif key != Gdk.KEY_g:
            moved = False

        if moved:
            self._sync_caret()
            self.window.queue_draw()
        return True

    def _use_block(self, block):
        """Move the caret into a different block of text."""
        self.element = block
        self.iface = block.accessible.get_text_iface()
        self.length = self.iface.get_character_count() if self.iface else 0
        self.text = _text_of(self.iface, 0, self.length)
        self.offset = 0
        self.anchor = None
        self.linewise = False
        self._sync_caret()
        self.window.queue_draw()

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
        if self.linewise:
            start = self._line_bounds(start)[0]
            end = self._line_bounds(end)[1]
        return start, min(end + 1, self.length)

    def _set_clipboard(self, text):
        """Best-effort clipboard write, falling back to xclip if GTK raises.

        Never let an exception here escape uncaught: every caller runs from
        the key handler, and a raised exception there would abort mid-motion
        without ever reaching the redraw/state cleanup that follows it.
        """
        try:
            clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
            clipboard.set_text(text, -1)
            clipboard.store()
        except Exception:
            _clipboard_fallback(text)

    def _clipboard_text(self):
        try:
            return Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).wait_for_text()
        except Exception:
            return None

    def _yank(self):
        """Yank the selection, or the word under the cursor if none.

        Matches vim: yanking does not exit caret mode -- you stay put, free
        to keep navigating or yank again. (This used to close the session
        immediately after one yank, which made a real yy -- yank, and still
        be there to yank again -- impossible, since the first y would have
        already torn the session down before a second keystroke arrived.)
        """
        span = self._selection()
        if span is None:
            span = self._word_bounds(self.offset)
        text = _text_of(self.iface, *span)
        if text:
            self._set_clipboard(text)
        self.anchor = None
        self.linewise = False
        self.window.queue_draw()

    def _editable(self):
        """This block's EditableText interface, if it publishes one."""
        try:
            return self.element.accessible.get_editable_text_iface()
        except Exception:
            return None

    def _delete(self, start, end):
        """Remove [start, end) from the field.

        Two ways, same split as edit mode and for the same reason: Chromium
        publishes no EditableText at all. What it does publish is a caret
        that can be set, so the fallback puts the caret at the start of the
        range and presses Delete over it -- which is what kindaVim calls its
        Keyboard Strategy, arrived at from the same dead end.
        """
        start = max(0, min(start, self.length))
        end = max(start, min(end, self.length))
        if end <= start:
            return

        iface = self._editable()
        if iface is not None:
            try:
                Atspi.EditableText.delete_text(iface, start, end)
                self.offset = start
                self._refresh()
                return
            except Exception:
                pass                 # advertised and refused; press keys
        self._by_keystroke(start, [("Delete", end - start)])

    def _put(self, text):
        """Insert `text` at the caret."""
        if not text:
            return
        iface = self._editable()
        if iface is not None:
            try:
                Atspi.EditableText.insert_text(
                    iface, self.offset, text, len(text))
                self.offset += len(text)
                self._refresh()
                return
            except Exception:
                pass
        self._set_clipboard(text)
        self._by_keystroke(self.offset, [(config.CARET_PASTE, 1)])

    def _by_keystroke(self, offset, combos):
        """Put the caret at `offset`, then send keys to the app itself.

        The overlay holds an exclusive keyboard grab, so synthetic keys would
        otherwise be delivered straight back to us instead of to the
        application underneath. The grab is dropped for the duration and
        taken again afterwards.
        """
        try:
            Atspi.Text.set_caret_offset(self.iface, offset)
        except Exception:
            return
        self._release_grab()
        x11.release_modifiers()
        for combo, times in combos:
            for _ in range(min(times, config.CARET_MAX_KEYSTROKES)):
                x11.send_combo(combo)

        def resume():
            # The application processes those keys on its own loop, so the
            # text is only worth re-reading once it has had a moment to.
            self.offset = offset
            self._refresh()
            GLib.idle_add(self._grab)
            return False

        GLib.timeout_add(config.CARET_EDIT_SETTLE_MS, resume)

    def _release_grab(self):
        if self._grabbed:
            try:
                Gdk.Display.get_default().get_default_seat().ungrab()
            except Exception:
                pass
            self._grabbed = False

    def _refresh(self):
        """Re-read the block after it was edited underneath us."""
        try:
            self.length = self.iface.get_character_count()
            self.text = _text_of(self.iface, 0, self.length)
        except Exception:
            pass
        self.offset = max(0, min(self.offset, self.length))
        self.anchor = None
        self.linewise = False
        self._sync_caret()
        self.window.queue_draw()

    def _delete_selection_or_char(self):
        """x, and d in visual mode."""
        span = self._selection()
        if span is None:
            span = (self.offset, min(self.offset + 1, self.length))
        self._delete(*span)

    def _delete_line(self):
        """dd: the line, and the newline ending it if there is one."""
        start, end = self._line_bounds(self.offset)
        self._delete(start, min(end + 1, self.length))

    def _yank_line(self):
        """yy: yank the current line, cursor-position selection or not."""
        start, end = self._line_bounds(self.offset)
        text = _text_of(self.iface, start, min(end + 1, self.length))
        if text:
            self._set_clipboard(text)
        self.anchor = None
        self.linewise = False
        self.window.queue_draw()

    def _close(self, reopen_search=False):
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
        # Both must run, same reasoning as search.SearchPrompt's on_pick/
        # on_done: on_done is what tells the daemon this session is over
        # (clears its overlay reference and mode file), and on_search --
        # which schedules the *next* session via GLib.idle_add -- has to be
        # called before it, not after, or on_done would clear the overlay
        # reference the instant the new session sets it.
        if reopen_search:
            self.on_search()
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

        # Number the other blocks so one keypress reaches any of them.
        if len(self.blocks) > 1 and self.anchor is None:
            cr.select_font_face(config.FONT_FAMILY)
            cr.set_font_size(config.FONT_SIZE)
            for number, block in enumerate(
                    self.blocks[:len(config.CARET_LABELS)]):
                if number == self.index:
                    continue
                label = config.CARET_LABELS[number]
                ext = cr.text_extents(label)
                w = ext.width + config.PAD_X * 2
                h = config.FONT_SIZE + config.PAD_Y * 2
                x = min(max(block.x - w - config.HINT_GAP, 0),
                        max(self.width - w, 0))
                y = min(max(block.y, 0), max(self.height - h, 0))
                cr.set_source_rgba(*self.colors["chip_matched"])
                cr.rectangle(x, y, w, h)
                cr.fill()
                cr.set_source_rgba(*self.colors["ink"])
                cr.move_to(x + config.PAD_X, y + h - config.PAD_Y - 2)
                cr.show_text(label)

        if self.anchor is not None:
            mode = "VISUAL LINE" if self.linewise else "VISUAL"
        else:
            mode = "CARET"
        legend = (f"{mode}   h/j/k/l move   w/b/e word   0/$ line   "
                  f"gg/G doc   v/V select   y yank   x/d cut   p put   "
                  f"/ search   esc")
        if self.pending_y:
            legend = "y…   " + legend
        if self.pending_d:
            legend = "d…   " + legend
        if len(self.blocks) > 1:
            legend = (f"[{self.index + 1}/{len(self.blocks)} "
                      f"1-9 jump, tab next]   " + legend)
        draw_legend(cr, legend, self.width, self.height, self.colors)
        return True


class CaretSearchPrompt:
    """Type to find a word or link, pick it, and land a caret there.

    Modelled closely on search.SearchPrompt's type-then-pick flow, but the
    target is a caret position inside a block of text rather than a click --
    typing "canvas" and picking a hit opens caret mode with the cursor
    already sitting on that exact word, instead of Tab-cycling through whole
    blocks by hand to find it.
    """

    def __init__(self, blocks, on_pick, on_done=None):
        self.blocks = blocks
        # Each block's text is read once up front: word_hits() runs again on
        # every keystroke as the query narrows, and re-reading the same text
        # over D-Bus that often is exactly the per-keystroke round trip that
        # made search mode's own indexer need to be a background job.
        self.block_texts = []
        for block in blocks:
            iface = block.accessible.get_text_iface()
            if iface is None:
                continue
            length = iface.get_character_count()
            self.block_texts.append((block, _text_of(iface, 0, length)))

        self.on_pick = on_pick
        self.on_done = on_done or (lambda: None)
        self.query = ""
        self.hits = []
        self.current = 0
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
        self._idle = GLib.timeout_add_seconds(
            config.IDLE_TIMEOUT_S, self._on_idle)

    def dismiss(self):
        self._close()

    def _on_idle(self):
        self._idle = None
        self._close()
        return False

    def _touch(self):
        if getattr(self, "_idle", None) is not None:
            GLib.source_remove(self._idle)
        self._idle = GLib.timeout_add_seconds(
            config.IDLE_TIMEOUT_S, self._on_idle)

    def _on_visibility(self, _widget, event):
        if event.state != Gdk.VisibilityState.UNOBSCURED:
            gdk_window = self.window.get_window()
            if gdk_window is not None:
                gdk_window.raise_()
        return False

    # -- input ------------------------------------------------------------

    def _grab(self):
        gdk_window = self.window.get_window()
        if gdk_window is not None:
            seat = Gdk.Display.get_default().get_default_seat()
            status = seat.grab(gdk_window, Gdk.SeatCapabilities.KEYBOARD,
                               False, None, None, None, None)
            if status == Gdk.GrabStatus.SUCCESS:
                self._grabbed = True
                x11.release_modifiers()
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
        self._touch()
        key = event.keyval
        if config.DEBUG_KEYS:
            x11.debug_log(f"[caret-search] key={Gdk.keyval_name(key)!r} "
                          f"query={self.query!r} hits={len(self.hits)}")
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

        if char and char in config.CARET_SEARCH_LABELS \
                and len(self.query) >= config.CARET_SEARCH_MIN_QUERY:
            index = config.CARET_SEARCH_LABELS.index(char)
            if index < len(self.hits):
                self.current = index
                self.submitted = True
                self._close()
            return True

        if char and char.isprintable():
            self.query += char
            self._refresh()
        return True

    def _refresh(self):
        self.hits = word_hits(self.block_texts, self.query)
        self.current = 0
        self.window.queue_draw()

    def _close(self):
        if getattr(self, "_idle", None) is not None:
            GLib.source_remove(self._idle)
            self._idle = None
        if self._grabbed:
            Gdk.Display.get_default().get_default_seat().ungrab()
            self._grabbed = False
            x11.release_modifiers()
        self.window.destroy()
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        Gdk.Display.get_default().sync()

        # Both must run on every path: on_done is what tells the daemon this
        # session is over (see search.SearchPrompt's own history -- skipping
        # it after a successful pick once left the daemon's overlay
        # reference stale, and the next hotkey replayed the pick a second
        # time).
        if self.submitted and self.hits:
            self.on_pick(self.hits[min(self.current, len(self.hits) - 1)])
        self.on_done()

    # -- drawing ------------------------------------------------------------

    def _draw_labels(self, cr):
        cr.select_font_face(config.FONT_FAMILY)
        cr.set_font_size(config.FONT_SIZE)
        shown = self.hits[:len(config.CARET_SEARCH_LABELS)]
        element_rects = [(h.x, h.y, h.w, h.h) for h in shown]
        placed = []
        for index, hit in enumerate(shown):
            label = config.CARET_SEARCH_LABELS[index]
            ext = cr.text_extents(label)
            w = ext.width + config.PAD_X * 2
            h = config.FONT_SIZE + config.PAD_Y * 2
            x, y = place_chip(hit, w, h, self.width, self.height,
                              index, element_rects, placed)
            placed.append((x, y, w, h))

            key = "chip" if index == self.current else "chip_matched"
            cr.set_source_rgba(*self.colors[key])
            cr.rectangle(x, y, w, h)
            cr.fill()
            cr.set_source_rgba(*self.colors["ink"])
            cr.move_to(x + config.PAD_X, y + h - config.PAD_Y - 2)
            cr.show_text(label)

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

        if len(self.query) >= config.CARET_SEARCH_MIN_QUERY:
            cr.set_line_width(2)
            cr.set_source_rgba(*self.colors["chip_matched"])
            for index, hit in enumerate(self.hits[:config.SEARCH_MAX_OUTLINES]):
                if index == self.current:
                    continue
                cr.rectangle(hit.x, hit.y, hit.w, hit.h)
            cr.stroke()

            if self.current < len(self.hits):
                hit = self.hits[self.current]
                cr.set_source_rgba(*self.colors["chip"])
                cr.set_line_width(3)
                cr.rectangle(hit.x - 1, hit.y - 1, hit.w + 2, hit.h + 2)
                cr.stroke()
                cr.set_source_rgba(*self.colors["chip"][:3], 0.22)
                cr.rectangle(hit.x, hit.y, hit.w, hit.h)
                cr.fill()

            self._draw_labels(cr)

        label = f"caret search: {self.query}_"
        count = (f"{self.current + 1}/{len(self.hits)}" if self.hits
                 else f"{len(self.hits)} match"
                      + ("" if len(self.hits) == 1 else "es"))
        text = f"{label}    {count}    1-9 pick · tab next · enter jump · esc"
        draw_legend(cr, text, self.width, self.height, self.colors)
        return True


def _clipboard_fallback(text):
    try:
        subprocess.run(["xclip", "-selection", "clipboard"],
                       input=text.encode(), timeout=2, check=False)
    except (OSError, subprocess.SubprocessError):
        pass
