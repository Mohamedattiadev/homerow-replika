"""Edit mode: turn any editable field on screen into nvim, in place.

Reading is the easy half, and it is the same everywhere. Every editable field
AT-SPI knows about publishes a Text interface -- Chromium's included -- so the
current contents come out without a keystroke: no `ctrl+a ctrl+c`, no clobbered
selection, no per-app select-all quirks, and it works on a field that is not
even focused. That is what makes picking a field by hint possible at all.

Writing is not. Chromium publishes no EditableText interface whatsoever, so
the write-back is chosen per *element*, not per app: EditableText where it
exists, otherwise focus the field and paste. Per element matters because one
window has both kinds -- on this desktop Brave's omnibox and a page's textarea
answer differently, and a per-app table would get one of them wrong.

The editor is real nvim in a VTE widget, positioned on the field's own screen
rectangle, so it reads as the field becoming an editor rather than as a
terminal opening somewhere else. It is not *inside* the widget: nothing
outside the application can draw there, which is why Firenvim has to be a
browser extension. Sitting exactly on top is as close as an outside process
gets.
"""

import os
import tempfile

import gi

gi.require_version("Atspi", "2.0")
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Atspi, Gdk, GLib, Gtk  # noqa: E402

from . import config, elements, theme, x11  # noqa: E402
from .overlay import screen_size, set_identity  # noqa: E402


def _vte():
    """The VTE terminal widget, imported only when a field is opened.

    Not at module scope, because the daemon imports this module at startup:
    a desktop without gir1.2-vte-2.91 would lose hint, scroll, caret and
    search too, over a dependency only edit mode has.
    """
    gi.require_version("Vte", "2.91")
    from gi.repository import Vte
    return Vte


def available():
    """True if there is a terminal widget to host the editor in."""
    try:
        _vte()
        return True
    except (ImportError, ValueError):
        return False

# How a field's new contents get back into it.
EDITABLE_TEXT = "editable-text"
PASTE = "paste"


def _text(iface, start, end):
    """Atspi.Text.get_text, called unbound.

    Same reason as caret._text: the bound accessor is not what answers here,
    the Text interface method has to be reached explicitly.
    """
    try:
        return Atspi.Text.get_text(iface, start, end) or ""
    except Exception:
        return ""


def collect(screen_w, screen_h):
    """Editable fields in the focused window, largest first.

    Unlike caret.collect this asks for the EDITABLE *state* rather than
    trusting roles alone. A role list on its own is both too wide (a read-only
    paragraph is a TEXT) and too narrow (a contenteditable div is a SECTION),
    and the state is the thing actually being asked about.

    An empty field is a legitimate target -- "compose a message in nvim" is
    the main use -- so unlike caret mode there is no minimum character count.
    """
    window = elements.active_window()
    if window is None:
        return []
    pid, win_x, win_y, win_w, win_h = window
    app = elements._app_for_pid(pid)
    if app is None:
        return []

    left, top = max(win_x, 0), max(win_y, 0)
    right, bottom = min(win_x + win_w, screen_w), min(win_y + win_h, screen_h)

    # Same foreground-tab narrowing as hint and caret mode: a browser keeping
    # five tabs alive otherwise offers five pages of fields on one viewport.
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

    # VISIBLE and EDITABLE, but deliberately not SHOWING, which every other
    # mode here asks for. Chromium publishes page content as VISIBLE without
    # SHOWING -- the textarea on a foreground tab, focused and on screen, has
    # EDITABLE,VISIBLE,FOCUSABLE,SENSITIVE,ENABLED and no SHOWING -- so
    # requiring it found browser chrome and not one field on any page, which
    # is the entire point of the mode. What SHOWING was buying (nothing
    # off-screen) the on-screen rectangle test below buys anyway.
    #
    # An empty role list with match type ALL is how AT-SPI spells "any role",
    # and it is deliberate here: see config.EDIT_SKIP_ROLES.
    rule = Atspi.MatchRule.new(
        Atspi.StateSet.new([
            Atspi.StateType.VISIBLE,
            Atspi.StateType.EDITABLE,
        ]), Atspi.CollectionMatchType.ALL,
        {}, Atspi.CollectionMatchType.ALL,
        [], Atspi.CollectionMatchType.ALL,
        [], Atspi.CollectionMatchType.ALL, False,
    )
    try:
        matches = collection.get_matches(
            rule, Atspi.CollectionSortOrder.CANONICAL,
            config.MAX_ELEMENTS, True)
    except Exception:
        return []

    found = []
    for accessible, ext in elements._extents(matches, win_x, win_y):
        cx, cy = ext.x + ext.width // 2, ext.y + ext.height // 2
        if not (left <= cx < right and top <= cy < bottom):
            continue
        if ext.width < config.EDIT_MIN_SIZE or \
                ext.height < config.EDIT_MIN_SIZE:
            continue
        # Editable fields are few -- a handful per window -- so the round trip
        # per candidate that hint mode cannot afford is fine here. This is now
        # the *only* thing keeping a password field out of a temp file, since
        # the match rule no longer filters by role, which is why it fails
        # closed: an element whose role will not read is dropped rather than
        # kept on the assumption that it is probably fine.
        try:
            if accessible.get_role_name() in config.EDIT_SKIP_ROLES:
                continue
            if not accessible.get_state_set().contains(
                    Atspi.StateType.SENSITIVE):
                continue
            if accessible.get_text_iface() is None:
                continue
        except Exception:
            continue
        found.append(
            elements.Element(accessible, ext.x, ext.y, ext.width, ext.height))

    found.sort(key=lambda e: e.w * e.h, reverse=True)
    return found


def sweep_temp_files():
    """Delete edit buffers left behind by a daemon that died mid-edit.

    A session unlinks its own file when it closes, so anything still here is
    from a daemon that was killed while an editor was open -- and it holds
    the contents of somebody's field, at 0600 but indefinitely. Run at
    startup, before any session of this daemon exists to be caught by it.
    """
    import glob
    removed = 0
    pattern = os.path.join(tempfile.gettempdir(), f"homerow-*{config.EDIT_SUFFIX}")
    for path in glob.glob(pattern):
        try:
            os.unlink(path)
            removed += 1
        except OSError:
            pass
    return removed


def strategy(accessible):
    """How this element's contents can be put back: EDITABLE_TEXT or PASTE.

    Probed per element rather than configured per app. kindaVim needs a
    hand-maintained override list because macOS apps claim accessibility
    support they do not have; AT-SPI apps answer honestly -- Brave returns no
    EditableText rather than an EditableText that silently does nothing -- so
    asking is both cheaper and more accurate than a table.
    """
    try:
        if accessible.get_editable_text_iface() is not None:
            return EDITABLE_TEXT
    except Exception:
        pass
    return PASTE


def read(accessible):
    """The field's current contents."""
    try:
        iface = accessible.get_text_iface()
        if iface is None:
            return ""
        count = Atspi.Text.get_character_count(iface)
    except Exception:
        return ""
    if count <= 0:
        return ""
    return _text(iface, 0, count)


def strip_added_newline(original, edited):
    """Undo the trailing newline an editor adds to a file that lacked one.

    nvim writes POSIX text files, so a one-line search box round-trips as
    "query\\n" and pastes a newline into a field where Enter means submit.
    Only the newline the editor added comes off -- one the user typed
    deliberately, on top of an original that already ended in one, stays.
    """
    if edited.endswith("\n") and not original.endswith("\n"):
        return edited[:-1]
    return edited


def frame_rect(field, screen_w, screen_h, min_w, min_h):
    """Where the editor window goes: the field's own rectangle, made usable.

    The field's top-left is the anchor and its size is the size, floored at
    whatever the caller worked out an editor actually needs, and pushed back
    on screen if that overflowed. The floor is small and measured in
    character cells (see EditSession._fit) rather than a round pixel number:
    a fixed floor is what turned a one-line omnibox into a window over half
    the page.
    """
    w = max(field.w, min_w)
    h = max(field.h, min_h)
    x = max(0, min(field.x, screen_w - w))
    y = max(0, min(field.y, screen_h - h))
    return x, y, w, h


def compact_rows(field_h, char_h, threshold):
    """True if this field is too short to spend rows on editor chrome."""
    if char_h <= 0:
        return False
    return field_h < threshold * char_h


def editor_argv(path, editor=None, compact=False):
    """The editor command for `path`.

    $VISUAL then $EDITOR then nvim, matching what every other tool that shells
    out to an editor does -- someone whose $EDITOR is helix should get helix,
    and gets it without the vim-only flags below.
    """
    editor = editor or os.environ.get("VISUAL") or \
        os.environ.get("EDITOR") or config.EDIT_EDITOR
    argv = editor.split()
    if argv and os.path.basename(argv[0]) in config.EDIT_VIM_LIKE:
        # -c, not --cmd: these have to run *after* the user's config, or
        # their own statusline plugin simply turns the settings back on, and
        # the mapping would be attached before there is a buffer to attach
        # it to.
        for mapping in config.EDIT_KEYMAPS:
            argv += ["-c", mapping]
        if compact:
            argv += ["-c", config.EDIT_COMPACT_SETTINGS]
    return argv + [path]


def write(field, text, window_id=None, log=None):
    """Put `text` into `field`, and say which way it went.

    The paste path is asynchronous -- it has to let the window manager focus
    the window before typing at it -- so this reports the route taken, not
    whether the characters arrived.
    """
    log = log or (lambda _message: None)
    how = strategy(field.accessible)
    if how == EDITABLE_TEXT:
        if _write_editable_text(field.accessible, text):
            return EDITABLE_TEXT
        # Advertised and then refused. Falling back beats failing: the paste
        # path works on anything that supports ctrl+a and ctrl+v.
        log("EditableText refused the write; falling back to paste")
    _write_paste(field, text, window_id, log)
    return PASTE


def _write_editable_text(accessible, text):
    try:
        iface = accessible.get_editable_text_iface()
        if iface is None:
            return False
        return bool(Atspi.EditableText.set_text_contents(iface, text))
    except Exception:
        return False


def _write_paste(field, text, window_id, log):
    """Select the field's contents and paste over them.

    The clipboard is the user's, so it is borrowed and handed back. The daemon
    owns the selection while it holds it, which is the reason this is a GTK
    clipboard and not a shell-out to xclip: xclip has to fork a process to
    stay the owner, and that process outliving the paste is exactly how a
    clipboard manager ends up with the field's contents in its history.
    """
    clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
    previous = clipboard.wait_for_text()
    clipboard.set_text(text, -1)

    def focus():
        if window_id:
            x11.activate_window(window_id)
        # Focus the field itself without moving the pointer. Clicking it would
        # work and is the fallback, but a click lands wherever the field
        # happens to be *now*, and moving the user's pointer to write text is
        # a side effect nobody asked for.
        if not _grab_focus(field.accessible):
            log("could not focus the field over AT-SPI; clicking it")
            x11.click(1, *field.center)

    def select_all():
        # The hotkey's own modifiers can still be physically down, and a
        # lingering alt turns ctrl+a into ctrl+alt+a. This is the same
        # precaution every other synthetic key in this codebase takes.
        x11.release_modifiers()
        x11.send_combo(config.EDIT_SELECT_ALL)

    def paste():
        x11.send_combo(config.EDIT_PASTE)

    def restore():
        if previous is None:
            clipboard.clear()
        else:
            clipboard.set_text(previous, -1)

    def then_type():
        _chain([
            (0, select_all),
            (config.EDIT_KEY_DELAY_MS, paste),
            (config.EDIT_RESTORE_DELAY_MS, restore),
        ])

    focus()
    # Waiting a fixed interval for the window manager was both slower than it
    # needed to be and not long enough to be safe: the keystrokes go to
    # whatever holds focus, so a slow focus meant select-all and paste landed
    # in the wrong window and the edit was simply gone. Polling costs a
    # fraction of the old delay in the normal case and is correct in the bad
    # one.
    _when_focused(window_id, then_type, log)


def _when_focused(window_id, then, log):
    """Call `then` once `window_id` has the focus, or after giving up."""
    if window_id is None or not x11.available():
        then()
        return

    def check(remaining):
        try:
            focused = x11.active_window_id()
        except Exception:
            focused = None
        if focused == window_id:
            then()
            return False
        if remaining <= 0:
            log("window never took focus back; pasting anyway")
            then()
            return False
        GLib.timeout_add(config.EDIT_FOCUS_POLL_MS,
                         lambda: check(remaining - 1))
        return False

    check(config.EDIT_FOCUS_TRIES)


def _grab_focus(accessible):
    try:
        component = accessible.get_component_iface()
        if component is None:
            return False
        return bool(Atspi.Component.grab_focus(component))
    except Exception:
        return False


def _chain(steps):
    """Run (delay_ms, callable) steps in order, on the main loop.

    Sleeping between the keystrokes would be simpler and would freeze the
    daemon for half a second while it did -- and the daemon's one main loop is
    what every other mode, and the overlay, and the workspace watch all run on.
    """
    def step(index):
        if index >= len(steps):
            return False
        delay, action = steps[index]

        def go():
            try:
                action()
            except Exception:
                pass
            step(index + 1)
            return False

        GLib.timeout_add(delay, go)
        return False

    step(0)


class EditSession:
    """nvim in a borderless window sitting on the field it is editing.

    Presents the same surface as Overlay -- `show`, `dismiss`, an `on_done`
    the daemon uses to drop its reference -- so the daemon's mode bookkeeping
    does not have to know which kind of thing is open.
    """

    # Unlike every other mode, this one survives a workspace change: it holds
    # work the user has not saved yet. See Daemon._check_workspace.
    follows_workspace = False
    # And for the same reason no other mode may replace it while it is open.
    # See Daemon._on_connection.
    holds_unsaved_work = True

    def __init__(self, field, original, on_done=None, on_write=None,
                 log=None):
        self.field = field
        self.original = original
        self.on_done = on_done or (lambda: None)
        self.on_write = on_write or (lambda _text: None)
        self._log = log or (lambda _message: None)
        self.closed = False
        self._dismissed = False

        handle, self.path = tempfile.mkstemp(
            prefix="homerow-", suffix=config.EDIT_SUFFIX)
        with os.fdopen(handle, "w", encoding="utf-8") as temp:
            temp.write(original)

        set_identity()
        self.compact = False
        screen_w, screen_h = screen_size()
        # Provisional: the real size needs the terminal's character cell,
        # which is not known until the widget has been realized. _fit()
        # applies it before the editor starts.
        x, y, w, h = frame_rect(field, screen_w, screen_h, field.w, field.h)

        self.window = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        self.window.set_decorated(False)
        self.window.set_keep_above(True)
        self.window.set_skip_taskbar_hint(True)
        self.window.set_skip_pager_hint(True)
        # A dialog hint is what stops a tiling WM from retiling the workspace
        # around this. The entire point is that it sits on the field; tiled
        # into a column on the other side of the screen it would still work
        # and would no longer be the feature.
        self.window.set_type_hint(Gdk.WindowTypeHint.DIALOG)
        self.window.set_role(config.EDIT_WM_ROLE)
        self.window.set_default_size(w, h)
        self.window.move(x, y)
        self.window.connect("delete-event", lambda *_: self._close())

        self._Vte = _vte()
        self.terminal = self._Vte.Terminal()
        self.terminal.set_scrollback_lines(0)
        self.terminal.connect("child-exited", self._on_child_exited)

        # A border, so an undecorated editor over a page still reads as
        # something homerow put there rather than as part of the page.
        frame = Gtk.Frame()
        frame.set_shadow_type(Gtk.ShadowType.NONE)
        frame.add(self.terminal)
        self._style(frame)
        self.window.add(frame)

    def _style(self, frame):
        try:
            accent = theme.palette()["chip"]
        except Exception:
            accent = (0.9, 0.7, 0.2)
        red, green, blue = (int(round(c * 255)) for c in accent[:3])
        css = (
            "frame > border { border: 0; } "
            "frame { border: %dpx solid rgb(%d,%d,%d); }"
            % (config.EDIT_BORDER, red, green, blue)
        ).encode()
        try:
            provider = Gtk.CssProvider()
            provider.load_from_data(css)
            frame.get_style_context().add_provider(
                provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        except Exception:
            pass

    def _fit(self):
        """Size the window to the field, in character cells.

        Runs after the terminal is realized, because get_char_width/height
        are what make "as close to the field as an editor can be" a measured
        answer rather than a guessed pixel constant.
        """
        try:
            char_w = self.terminal.get_char_width()
            char_h = self.terminal.get_char_height()
        except Exception:
            char_w = char_h = 0
        if char_w <= 0 or char_h <= 0:
            return

        self.compact = compact_rows(
            self.field.h, char_h, config.EDIT_COMPACT_ROWS)
        rows = (config.EDIT_COMPACT_MIN_ROWS if self.compact
                else config.EDIT_MIN_ROWS)
        border = 2 * config.EDIT_BORDER
        min_w = config.EDIT_MIN_COLS * char_w + border
        min_h = rows * char_h + border

        screen_w, screen_h = screen_size()
        x, y, w, h = frame_rect(self.field, screen_w, screen_h, min_w, min_h)

        # Tell the terminal its grid, not just the window its pixels. A
        # Vte.Terminal defaults to 80x24 and requests that much space, so
        # resizing the window alone leaves an 80x24 terminal clipped by a
        # two-row window -- which renders as a statusline where the text
        # should be and an empty row under it. set_size also resizes the pty,
        # so nvim redraws for the size it is actually being shown at.
        cols = max(1, (w - border) // char_w)
        grid_rows = max(1, (h - border) // char_h)
        try:
            self.terminal.set_size(cols, grid_rows)
        except Exception:
            pass
        self._log(f"editor box {w}x{h} at {x},{y} "
                  f"({cols}x{grid_rows} cells of {char_w}x{char_h}, "
                  f"compact={self.compact})")

        self.window.move(x, y)
        self.window.resize(w, h)

    def show(self):
        self.window.show_all()
        self._fit()
        self.window.present()
        # Keyboard focus has to be real here, unlike every other mode in this
        # codebase: nvim needs the keys, so this must not grab them.
        try:
            self.window.get_window().focus(Gdk.CURRENT_TIME)
        except Exception:
            pass
        self.terminal.grab_focus()

        argv = editor_argv(self.path, compact=self.compact)
        self._log(f"editing in {' '.join(argv)}")
        try:
            # Positional, and the order is not the one Python introspection
            # reports: inspect.signature omits child_setup_data, so passing
            # arguments to match it silently shifts everything after
            # child_setup by one and the timeout arrives as a Cancellable.
            # pty_flags, cwd, argv, envv, spawn_flags, child_setup,
            # child_setup_data, timeout, cancellable, callback.
            self.terminal.spawn_async(
                self._Vte.PtyFlags.DEFAULT, os.path.expanduser("~"), argv,
                None, GLib.SpawnFlags.SEARCH_PATH, None, None, -1, None,
                self._on_spawned,
            )
        except Exception as error:
            self._log(f"could not start the editor: {error!r}")
            self._close()

    def _on_spawned(self, _terminal, pid, error, *_rest):
        if error is not None or pid == -1:
            self._log(f"editor failed to start: {error!r}")
            self._close()

    def _on_child_exited(self, _terminal, status):
        """The editor exited; take whatever it left on disk."""
        # Destroying the window kills the editor, so this also fires on the
        # way out of dismiss(). That is not the user saving anything.
        if self._dismissed:
            return
        edited = None
        try:
            with open(self.path, encoding="utf-8") as temp:
                edited = temp.read()
        except OSError as error:
            self._log(f"could not read the edited file back: {error!r}")

        self._close()

        if edited is None:
            return
        if status != 0:
            # A crashed or killed editor has not agreed to anything; writing
            # its buffer into the user's field would be putting words in
            # their mouth.
            self._log(f"editor exited {status}; not writing back")
            return
        edited = strip_added_newline(self.original, edited)
        if edited == self.original:
            self._log("unchanged; nothing to write back")
            return
        self.on_write(edited)

    def dismiss(self):
        """Close *without* writing back.

        A dismissal is the daemon closing this session for its own reasons,
        not the user saving. Whatever is in the buffer is half-finished, and
        pasting it into the field overwrites the real contents with it --
        which is exactly what happened when a second editor was opened over
        a first: both sessions wrote back, two milliseconds apart, and the
        abandoned one landed last.
        """
        self._dismissed = True
        self._close()

    def _close(self):
        if self.closed:
            return
        self.closed = True
        try:
            self.window.destroy()
        except Exception:
            pass
        # The field's contents were on disk; they should not stay there.
        try:
            os.unlink(self.path)
        except OSError:
            pass
        self.on_done()
