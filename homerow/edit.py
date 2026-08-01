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
import subprocess
import tempfile
import time

import gi

gi.require_version("Atspi", "2.0")
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Atspi, Gdk, Gio, GLib, Gtk  # noqa: E402

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


def warm_widget(log=None):
    """Build and throw away one terminal, to pay its one-off cost early.

    The first Vte.Terminal in a process costs ~345ms to create and realize --
    fonts, the widget's own class setup -- and every one after it costs ~9ms.
    Measured over two sessions in one process: 581ms to spawn the editor, then
    83ms. That first payment landed on whoever pressed alt+e first, which is
    the press that decides whether the mode feels instant.

    Realized rather than shown: realizing is what loads the fonts, and a
    window that is never mapped never appears and never takes focus. Called
    at daemon startup beside start_warm, for the same reason and with the
    same shape -- if it fails, the first field is merely as slow as it used
    to be.
    """
    log = log or (lambda _m: None)
    try:
        window = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        window.set_decorated(False)
        terminal = _vte().Terminal()
        window.add(terminal)
        window.realize()
        terminal.realize()
        window.destroy()
    except Exception as error:
        log(f"could not warm the terminal widget: {error!r}")
        return False
    return True


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
    document = None
    try:
        title = x11.window_name(x11.active_window_id() or 0) if \
            x11.available() else ""
        document = elements.active_document(scope, title)
    except Exception:
        document = None

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

    # Fields inside a background tab, dropped by rectangle rather than by
    # scoping the query to the foreground document. Scoping was how this used
    # to narrow, and it threw the browser's own chrome away with the
    # background tabs: reported live on Google Drive, the page's search box
    # got a hint and the address bar beside it did not, because the address
    # bar is not inside any document. Every tab's document reports the same
    # viewport rectangle, so "inside the viewport but not in the foreground
    # document" is the thing to reject, and chrome -- which is outside the
    # viewport entirely -- is kept.
    viewport = _rect_of(document) if document is not None else None
    background = _background_documents(scope, document, win_x, win_y)

    found = []
    for accessible, ext in elements._extents(matches, win_x, win_y):
        cx, cy = ext.x + ext.width // 2, ext.y + ext.height // 2
        if not (left <= cx < right and top <= cy < bottom):
            continue
        if viewport is not None and background and _within(viewport, cx, cy) \
                and not _belongs_to(accessible, document):
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


def _rect_of(accessible):
    """Screen rectangle of an accessible, or None."""
    try:
        component = accessible.get_component_iface()
        if component is None:
            return None
        ext = component.get_extents(Atspi.CoordType.SCREEN)
        if ext.width <= 0 or ext.height <= 0:
            return None
        return ext.x, ext.y, ext.width, ext.height
    except Exception:
        return None


def _within(rect, x, y):
    left, top, width, height = rect
    return left <= x < left + width and top <= y < top + height


def _background_documents(scope, document, win_x, win_y):
    """True if this window has documents other than the foreground one.

    Only then is any of the ancestry checking below worth paying for: a
    single-document window has no background tab to confuse anything with,
    and every field in it is either the page or the chrome.
    """
    if document is None:
        return False
    try:
        collection = scope.get_collection_iface()
        if collection is None:
            return False
        rule = Atspi.MatchRule.new(
            Atspi.StateSet.new([]), Atspi.CollectionMatchType.ALL,
            {}, Atspi.CollectionMatchType.ALL,
            [Atspi.Role.DOCUMENT_WEB], Atspi.CollectionMatchType.ANY,
            [], Atspi.CollectionMatchType.ALL, False)
        return len(collection.get_matches(
            rule, Atspi.CollectionSortOrder.CANONICAL, 8, True)) > 1
    except Exception:
        return False


def _belongs_to(accessible, document):
    """True if `accessible` is inside `document`.

    Walks up rather than down, and only for the few candidates that sit
    inside the viewport at all -- editable fields are a handful per window,
    so the round trips this costs are affordable where hint mode's would not
    be. Bounded, because a broken tree with a parent cycle would otherwise
    hang the daemon on a mode that is meant to feel instant.
    """
    node = accessible
    for _ in range(config.EDIT_ANCESTOR_LIMIT):
        if node is None:
            return False
        try:
            if node == document:
                return True
            node = node.get_parent()
        except Exception:
            return False
    return False


def resolve_editor(editor=None):
    """$VISUAL, then $EDITOR, then the configured default."""
    return editor or os.environ.get("VISUAL") or \
        os.environ.get("EDITOR") or config.EDIT_EDITOR


def is_vim_like(editor):
    argv = editor.split()
    return bool(argv) and \
        os.path.basename(argv[0]) in config.EDIT_VIM_LIKE


def warm_socket_path():
    runtime = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
    return os.path.join(runtime, config.EDIT_WARM_SOCKET)


_warm = {"proc": None}


def warm_alive():
    proc = _warm["proc"]
    return (proc is not None and proc.poll() is None
            and os.path.exists(warm_socket_path()))


def start_warm(editor=None, log=None):
    """Start the headless nvim that the next edit will attach to.

    Called at daemon startup and again after every session, so the cost of
    loading the user's config is always already paid by the time a field is
    opened rather than being paid in front of them.
    """
    log = log or (lambda _m: None)
    if not config.EDIT_WARM_SERVER or warm_alive():
        return False
    editor = resolve_editor(editor)
    if not is_vim_like(editor):
        return False           # only nvim speaks --listen/--remote-ui
    socket_path = warm_socket_path()
    try:
        os.unlink(socket_path)
    except OSError:
        pass
    import subprocess
    try:
        _warm["proc"] = subprocess.Popen(
            editor.split() + ["--headless", "--listen", socket_path],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True,
        )
    except (OSError, ValueError) as error:
        log(f"could not start the warm editor: {error!r}")
        _warm["proc"] = None
        return False
    return True


def focused(fields):
    """The field that already has keyboard focus, if exactly one does.

    Half of all edit sessions here had two fields on screen, so the picker
    was asking which of two things you meant when one of them was the box you
    were already typing in. If the application says which that is, there is
    nothing to ask. Exactly one, or none: two focused fields is a claim no
    toolkit should make, and guessing between them is worse than hinting.
    """
    hit = None
    for field in fields:
        try:
            if not field.accessible.get_state_set().contains(
                    Atspi.StateType.FOCUSED):
                continue
        except Exception:
            continue
        if hit is not None:
            return None
        hit = field
    return hit


def wait_warm(timeout_ms=None, log=None):
    """Block until the warm server answers, or the timeout runs out.

    Called on the way out of a session, where nobody is waiting on us, so
    that the *next* open finds a server instead of paying for a cold start.
    Existence of the socket is not enough -- nvim creates it before the
    config has finished loading -- so this asks it a question.
    """
    log = log or (lambda _m: None)
    timeout_ms = timeout_ms or config.EDIT_WARM_READY_MS
    editor = resolve_editor()
    if not is_vim_like(editor):
        return False
    import subprocess
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        if warm_alive():
            try:
                answer = subprocess.run(
                    editor.split() + ["--server", warm_socket_path(),
                                      "--remote-expr", "1"],
                    capture_output=True, timeout=2, check=False)
                if answer.returncode == 0:
                    return True
            except (OSError, subprocess.SubprocessError):
                pass
        time.sleep(config.EDIT_WARM_POLL_MS / 1000)
    log("warm editor did not come back in time; the next open may be cold")
    return False


def prime_warm(log=None):
    """Make the warm server open one throwaway buffer, so the next is fast.

    A server that answers is not a server that is ready. Answering `1` costs
    nothing, but the first real `:edit` pays for filetype detection and every
    plugin that lazy-loads on a buffer appearing -- measured here at 356ms
    against 60ms and 42ms for the two after it. That is the same 300ms the
    warm server exists to remove, arriving one step later than the thing that
    was supposed to remove it.

    Done on a scratch file with the same suffix, so the same filetype plugins
    load, and the buffer is wiped afterwards: a leftover modified buffer is
    what makes the next `:edit` fail with E37, which is why this server is
    replaced after every session in the first place.
    """
    log = log or (lambda _m: None)
    if not warm_alive():
        return False
    editor = resolve_editor()
    if not is_vim_like(editor):
        return False
    handle, path = tempfile.mkstemp(prefix="homerow-prime-",
                                    suffix=config.EDIT_SUFFIX)
    os.close(handle)
    try:
        subprocess.run(
            editor.split() + [
                "--server", warm_socket_path(), "--remote-expr",
                f"execute({_vim_list([f'edit {path}', 'silent! bwipeout!'])})"],
            capture_output=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError) as error:
        log(f"could not prime the warm editor: {error!r}")
        return False
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    return True


def stop_warm():
    """Kill the warm server, if any. Its buffer state is not reusable."""
    proc = _warm["proc"]
    _warm["proc"] = None
    if proc is None:
        return
    try:
        proc.terminate()
        # Reaped before anything replaces it, and this is load-bearing rather
        # than tidiness. nvim unlinks its listen socket as it exits; the
        # replacement server is started immediately after this returns and
        # creates a socket at the same path, so an unreaped predecessor
        # deletes its successor's socket on the way out. Measured: every edit
        # after the first found no server and started from cold, and the
        # "warm" path was warm exactly once per daemon.
        proc.wait(timeout=config.EDIT_WARM_STOP_MS / 1000)
    except OSError:
        pass
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
            proc.wait(timeout=config.EDIT_WARM_STOP_MS / 1000)
        except (OSError, subprocess.TimeoutExpired):
            pass
    try:
        os.unlink(warm_socket_path())
    except OSError:
        pass


def _vim_list(items):
    """A vimscript list literal of strings."""
    return "[" + ",".join(
        "'" + item.replace("'", "''") + "'" for item in items) + "]"


def rows_path(path):
    """Where the editor reports how many rows it needs, for `path`."""
    return path + config.EDIT_ROWS_SUFFIX


def rows_watch(path):
    """The autocmd that makes the editor report its height as it is typed in.

    Buffer-local, like the mappings beside it, and homerow's own -- see
    config.EDIT_GROW. Returns None when growing is switched off.
    """
    if not config.EDIT_GROW:
        return None
    return ("autocmd TextChanged,TextChangedI <buffer> silent! call "
            f"writefile([{config.EDIT_ROWS_EXPR}], '{rows_path(path)}')")


def setup_commands(path, compact, cursor=None):
    """Everything the editor is told about this buffer, warm or cold.

    One list, so the two paths cannot drift: a mapping that only works on a
    cold editor is worse than one that works nowhere, because it is the path
    nobody takes.
    """
    commands = list(config.EDIT_KEYMAPS)
    watch = rows_watch(path)
    if watch:
        commands.append(watch)
    if compact:
        commands.append(f"silent! {config.EDIT_COMPACT_SET}")
    if cursor:
        commands.append("call cursor({}, {})".format(*cursor))
    return commands


def warm_open(path, compact, editor=None, log=None, cursor=None):
    """Load `path` into the warm server, set up as a cold nvim would be.

    One remote-expr rather than several: each is a process spawn, and the
    whole point of this path is the milliseconds.
    """
    log = log or (lambda _m: None)
    editor = resolve_editor(editor)
    commands = [f"edit {path}"] + setup_commands(path, compact, cursor)
    import subprocess
    try:
        result = subprocess.run(
            editor.split() + ["--server", warm_socket_path(),
                              "--remote-expr", f"execute({_vim_list(commands)})"],
            capture_output=True, timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        log(f"warm editor did not answer: {error!r}")
        return False
    if result.returncode != 0:
        log(f"warm editor refused the file: "
            f"{result.stderr.decode('utf-8', 'replace').strip()[:120]}")
        return False
    return True


def warm_save(editor=None, log=None):
    """Ask the warm server to write its buffer now.

    The file on disk is only as new as the last `:w`, so anything that has to
    read the buffer without the editor exiting first -- closing because the
    workspace changed, say -- would otherwise take a stale copy and call it
    the user's work. Only possible on the warm path: a cold editor is a
    process with no socket to ask.
    """
    log = log or (lambda _m: None)
    editor = resolve_editor(editor)
    import subprocess
    try:
        result = subprocess.run(
            editor.split() + ["--server", warm_socket_path(),
                              "--remote-expr", 'execute("silent! write")'],
            capture_output=True, timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        log(f"warm editor would not save: {error!r}")
        return False
    return result.returncode == 0


def sweep_temp_files():
    """Delete edit buffers left behind by a daemon that died mid-edit.

    A session unlinks its own file when it closes, so anything still here is
    from a daemon that was killed while an editor was open -- and it holds
    the contents of somebody's field, at 0600 but indefinitely. Run at
    startup, before any session of this daemon exists to be caught by it.
    """
    import glob
    removed = 0
    pattern = os.path.join(tempfile.gettempdir(),
                           f"homerow-*{config.EDIT_SUFFIX}")
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


def cursor_at(text, offset):
    """(line, column) for a character offset, both 1-based as vim counts them.

    Caret mode knows where the cursor is as an offset into the field's text;
    an editor wants a line and a column. Handing over the position is most of
    what makes opening the field from caret mode better than opening it from
    scratch -- otherwise you land at the top of a paragraph you had already
    navigated into.
    """
    offset = max(0, min(offset, len(text)))
    before = text[:offset]
    line = before.count("\n") + 1
    column = offset - (before.rfind("\n") + 1) + 1
    return line, column


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


def wrapped_rows(text, cols):
    """Rows this text needs at `cols` columns, counting wrapping.

    An empty field is one row, not none: there is still a line to type on.
    """
    if cols < 1:
        return 1
    total = 0
    for line in (text or "").split("\n"):
        total += max(1, -(-len(line) // cols))     # ceil
    return max(1, total)


def cell_padding(terminal):
    """Pixels the terminal keeps around its grid: (horizontal, vertical).

    A Vte.Terminal fits `(allocation - padding) // cell` characters, not
    `allocation // cell` -- it carries a CSS padding of its own, 1px per edge
    with the theme here. Sizing a window at `rows * char_height` therefore
    buys one row fewer than was asked for, every time, and in a compact box
    the row lost is the only one the text was going to be on: with a global
    statusline the single surviving row *is* the statusline, which is exactly
    what "the box shows a statusline and none of my text" looks like.

    Measured off the widget rather than assumed, because it is a theme's to
    set. Returns (0, 0) if the style context cannot be read, which is the old
    behaviour and no worse than it was.
    """
    try:
        style = terminal.get_style_context()
        state = style.get_state()
        pad = style.get_padding(state)
        border = style.get_border(state)
    except Exception:
        return 0, 0
    horizontal = pad.left + pad.right + border.left + border.right
    vertical = pad.top + pad.bottom + border.top + border.bottom
    return horizontal, vertical


def compact_height(text_rows, chrome):
    """Total rows a compact box gets, for `text_rows` rows of text.

    Floored, and the floor is the point. Sizing the box to exactly the text
    it opens with gives a one-line field one row to type on, and one row is
    not an editor -- it is a slot. Press Enter in it and the line you were
    writing scrolls out of sight, and getting it back means k, which is not
    what pressing Enter in a text field does anywhere else on the desktop.

    Capped at the other end so a long field does not open a window over half
    the page; past the cap the editor scrolls, which is what an editor is
    for.
    """
    return max(config.EDIT_COMPACT_MIN_ROWS,
               min(config.EDIT_COMPACT_MAX_ROWS, text_rows + chrome))


def compact_rows(field_h, char_h, threshold):
    """True if this field is too short to spend rows on editor chrome."""
    if char_h <= 0:
        return False
    return field_h < threshold * char_h


def editor_argv(path, editor=None, compact=False, cursor=None):
    """The editor command for `path`.

    $VISUAL then $EDITOR then nvim, matching what every other tool that shells
    out to an editor does -- someone whose $EDITOR is helix should get helix,
    and gets it without the vim-only flags below.
    """
    editor = resolve_editor(editor)
    argv = editor.split()
    if argv and os.path.basename(argv[0]) in config.EDIT_VIM_LIKE:
        # -c, not --cmd: these have to run *after* the user's config, or
        # their own statusline plugin simply turns the settings back on, and
        # the mapping would be attached before there is a buffer to attach
        # it to.
        for mapping in config.EDIT_KEYMAPS:
            argv += ["-c", mapping]
        watch = rows_watch(path)
        if watch:
            argv += ["-c", watch]
        if compact:
            argv += ["-c", config.EDIT_COMPACT_SETTINGS]
        if cursor:
            argv += ["-c", "call cursor({}, {})".format(*cursor)]
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


# What the editor kept for itself last time, learned from our own editor and
# never from the user's configuration. None until the first session has been
# open long enough to ask.
_learned = {"chrome": None}


def learn_chrome(editor=None, log=None):
    """Ask the running editor how many rows it kept, and remember.

    Deliberately does not resize anything: the answer is used by the *next*
    session, not this one. Sizing the window a second time after the editor
    is already up is a jump under the eye of somebody who has started
    reading, and being one row generous for one session is cheaper than that.

    This only ever talks to the editor homerow itself started, over its own
    socket. Nothing is asked of the user's config and nothing has to be added
    to it -- an earlier version announced homerow to the config and asked for
    a line in the statusline plugin, which is the opposite of the point.
    """
    log = log or (lambda _m: None)
    if not warm_alive():
        return None
    editor = resolve_editor(editor)
    if not is_vim_like(editor):
        return None
    try:
        answer = subprocess.run(
            editor.split() + ["--server", warm_socket_path(), "--remote-expr",
                              "(&laststatus > 0 ? 1 : 0) + &cmdheight"],
            capture_output=True, timeout=5, check=False)
        if answer.returncode != 0:
            return None
        measured = max(0, min(int(answer.stdout.decode().strip()),
                              config.EDIT_CHROME_ROWS))
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        log(f"could not read the editor's own layout: {error!r}")
        return None
    if measured != _learned["chrome"]:
        log(f"this editor keeps {measured} row(s); "
            f"sizing the next field for that")
    _learned["chrome"] = measured
    return measured


def keep_buffer(text, log=None):
    """Save `text` where the user can get it back; returns the path, or None.

    For the one write that cannot be undone by editing again: replacing a
    field's contents with nothing. Editing again cannot recover what was
    there, so a copy outlives the session. Swept at the next daemon start
    like every other buffer this mode leaves behind.
    """
    log = log or (lambda _m: None)
    try:
        handle, path = tempfile.mkstemp(prefix="homerow-kept-",
                                        suffix=config.EDIT_SUFFIX)
        with os.fdopen(handle, "w", encoding="utf-8") as temp:
            temp.write(text)
        log(f"kept the field's previous contents at {path}")
        return path
    except OSError as error:
        log(f"could not keep the field's previous contents: {error!r}")
        return None


def verify(field, expected, log=None):
    """Read the field back and say whether it holds what was sent.

    Answers the question the mode could not answer before: did it land? The
    Text interface reads without focus and without a keystroke -- the same
    property that makes reading a field possible in the first place -- so
    checking costs one round trip and nothing else.

    Worth doing because the paste path cannot fail loudly. It borrows the
    clipboard, focuses the window and presses ctrl+a ctrl+v at it; every one
    of those can be swallowed by an application that was busy, and the mode
    would report success either way. Returns True, False, or None when the
    field cannot be read at all.
    """
    log = log or (lambda _m: None)
    try:
        actual = read(field.accessible)
    except Exception as error:
        log(f"could not read the field back: {error!r}")
        return None
    if actual.strip() == expected.strip():
        return True
    log(f"write-back did not land: field holds {len(actual)} chars, "
        f"sent {len(expected)}")
    return False


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
        held = clipboard.wait_for_text()
        log(f"pasting: clipboard holds {0 if held is None else len(held)} "
            f"chars, wanted {len(text)}")
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

    # No other mode may replace this one while it is open: it holds work the
    # user has not saved yet. See Daemon.dispatch. A workspace change does
    # close it, but through close_for_workspace() rather than dismiss().
    holds_unsaved_work = True

    def __init__(self, field, original, on_done=None, on_write=None,
                 log=None, cursor=None):
        self.field = field
        # Where to put the editor's cursor, as (line, column). Caret mode
        # hands this over so that opening the field lands you where you
        # already were rather than back at the top.
        self.cursor = cursor
        self.original = original
        self.on_done = on_done or (lambda: None)
        self.on_write = on_write or (lambda _text: None)
        self._log = log or (lambda _message: None)
        self.closed = False
        self._dismissed = False
        self.warm = False
        self._monitor = None
        # Set by _fit, and only used to grow the box afterwards.
        self._rows = 0
        self._cell_h = 0
        self._chrome_px = 0
        self._chrome_rows = 0
        self._rows_monitor = None
        # What the field is believed to hold. Live writes move it, so the
        # write on close does not repeat one that already landed.
        self._sent = original

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
        # The editor keeps a couple of rows for itself, and how many is
        # assumed rather than asked. Asking meant announcing homerow to the
        # user's editor config and asking them to change it, which is the
        # opposite of what this is for -- see config.EDIT_CHROME_ROWS.
        # What the window spends on everything that is not grid: our own
        # frame, and the terminal's own padding (see cell_padding -- getting
        # this wrong costs a whole row, not a few pixels).
        pad_w, pad_h = cell_padding(self.terminal)
        chrome_w = 2 * config.EDIT_BORDER + pad_w
        chrome_h = 2 * config.EDIT_BORDER + pad_h
        min_w = config.EDIT_MIN_COLS * char_w + chrome_w
        rows = config.EDIT_MIN_ROWS
        if self.compact:
            chrome = (_learned["chrome"] if _learned["chrome"] is not None
                      else config.EDIT_CHROME_ROWS)
            # As tall as the text is, not as tall as the field is. Matching
            # the field exactly makes a one-line box for a one-line field,
            # which looks right and edits badly: a line that wraps, or a
            # field holding a paragraph, leaves you typing through a slot
            # with no sight of the lines above or below. The window floats
            # over the page anyway, so it can be taller than the thing it
            # sits on -- it just should not be taller than it needs to be.
            cols = max(1, (self.field.w - chrome_w) // char_w)
            text = max(config.EDIT_COMPACT_TEXT_ROWS,
                       wrapped_rows(self.original, cols))
            rows = compact_height(text, chrome)
            self._log(f"{wrapped_rows(self.original, cols)} row(s) of text "
                      f"+ {chrome} the editor keeps; using {rows}")
        min_h = rows * char_h + chrome_h
        # Kept so the box can grow later without measuring any of it again:
        # growing has to be cheap, it happens while somebody is typing.
        self._cell_h = char_h
        self._chrome_px = chrome_h
        self._chrome_rows = chrome
        self._rows = rows

        screen_w, screen_h = screen_size()
        x, y, w, h = frame_rect(self.field, screen_w, screen_h, min_w, min_h)

        # Tell the terminal its grid, not just the window its pixels. A
        # Vte.Terminal defaults to 80x24 and requests that much space, so
        # resizing the window alone leaves an 80x24 terminal clipped by a
        # two-row window -- which renders as a statusline where the text
        # should be and an empty row under it. set_size also resizes the pty,
        # so nvim redraws for the size it is actually being shown at.
        cols = max(1, (w - chrome_w) // char_w)
        grid_rows = max(1, (h - chrome_h) // char_h)
        try:
            self.terminal.set_size(cols, grid_rows)
        except Exception:
            pass
        self._log(f"editor box {w}x{h} at {x},{y} "
                  f"({cols}x{grid_rows} cells of {char_w}x{char_h}, "
                  f"{chrome_w}x{chrome_h} of trim, compact={self.compact})")

        self.window.move(x, y)
        self.window.resize(w, h)

    def _argv(self):
        """Attach to the warm server if there is one, else start cold.

        Falling back rather than failing: the warm path has more that can go
        wrong (no server, a server that will not take the file) and none of
        it is worth losing the mode over.
        """
        editor = resolve_editor()
        if warm_alive() and is_vim_like(editor):
            if warm_open(self.path, self.compact, editor, self._log,
                         cursor=self.cursor):
                self.warm = True
                return editor.split() + [
                    "--server", warm_socket_path(), "--remote-ui"]
            self._log("falling back to a cold editor")
        return editor_argv(self.path, compact=self.compact,
                           cursor=self.cursor)

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

        argv = self._argv()
        self._watch_for_saves()
        self._watch_for_growth()
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
            return
        # Learn what this editor keeps for itself, for the next field rather
        # than this one -- see learn_chrome. Only worth asking on the warm
        # path, which is the only one with a socket to ask over.
        if self.warm and self.compact:
            GLib.timeout_add(config.EDIT_LEARN_DELAY_MS, self._learn)

    def _learn(self):
        if not self.closed:
            learn_chrome(log=self._log)
        return False

    def _watch_for_growth(self):
        """Grow the box as the buffer grows -- see config.EDIT_GROW.

        Only in a compact box. A full-size one already opened at the height
        the field asked for, and nothing there is standing in for a one-line
        text field.
        """
        if not config.EDIT_GROW or not self.compact:
            return
        try:
            handle = Gio.File.new_for_path(rows_path(self.path))
            self._rows_monitor = handle.monitor_file(
                Gio.FileMonitorFlags.NONE, None)
            self._rows_monitor.connect("changed", self._on_rows_changed)
        except Exception as error:
            self._log(f"the box will not grow: {error!r}")
            self._rows_monitor = None

    def _on_rows_changed(self, _monitor, _file, _other, _event):
        if self.closed:
            return
        try:
            with open(rows_path(self.path), encoding="utf-8") as handle:
                wanted = int(handle.read().strip())
        except (OSError, ValueError):
            return          # half-written, or written while we were reading
        self._grow(wanted)

    def _grow(self, text_rows):
        """Make the box `text_rows` rows of text tall, if that is taller.

        Never shorter. Shrinking is the resize with nothing to offer: it
        moves the text under the cursor to reclaim space nobody asked for.
        """
        if self.closed or self._cell_h <= 0:
            return
        rows = compact_height(text_rows, self._chrome_rows)
        if rows <= self._rows:
            return
        screen_w, screen_h = screen_size()
        height = rows * self._cell_h + self._chrome_px
        x, y, w, h = frame_rect(self.field, screen_w, screen_h,
                                self.field.w, height)
        self._rows = rows
        try:
            self.terminal.set_size(self.terminal.get_column_count(), rows)
            self.window.move(x, y)
            self.window.resize(w, h)
        except Exception as error:
            self._log(f"could not grow the box: {error!r}")
            return
        self._log(f"grew to {rows} rows for {text_rows} row(s) of text")

    def _watch_for_saves(self):
        """Push the field on every `:w`, where that can be done quietly.

        Only for fields AT-SPI can write directly. The paste path would have
        to pull focus off the editor and type into the field to do this,
        which in the middle of an edit is worse than waiting for the close --
        so in a browser `:w` still only saves, and the field updates when the
        editor exits.
        """
        if not config.EDIT_LIVE_WRITE:
            return
        if strategy(self.field.accessible) != EDITABLE_TEXT:
            return
        try:
            handle = Gio.File.new_for_path(self.path)
            self._monitor = handle.monitor_file(
                Gio.FileMonitorFlags.NONE, None)
            self._monitor.connect("changed", self._on_file_changed)
        except Exception as error:
            self._log(f"no live write-back: {error!r}")
            self._monitor = None

    def _on_file_changed(self, _monitor, _file, _other, event):
        if event != Gio.FileMonitorEvent.CHANGES_DONE_HINT:
            return
        if self.closed or self._dismissed:
            return
        try:
            with open(self.path, encoding="utf-8") as temp:
                edited = strip_added_newline(self.original, temp.read())
        except OSError:
            return
        if edited == self._sent:
            return
        self._sent = edited
        self.on_write(edited)

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
        if edited == self._sent:
            # Either nothing was changed, or a `:w` already pushed exactly
            # this and repeating it would be a second write for no reason.
            self._log("nothing further to write back")
            return
        self._sent = edited
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

    def close_for_workspace(self):
        """Leave because the workspace changed; the path text was kept at, or None.

        Every other mode simply closes when the workspace changes: what it
        knows describes a window that is not in front any more. This one used
        to opt out, because it holds text the user has typed and closing it
        the way the others close would throw that away. The cost of opting
        out was worse than the problem: the editor stayed open over a field
        on a workspace nobody was looking at, the bar kept saying "edit", and
        every other mode refused to open because an editor was still up.

        So it closes too, but never empty-handed. On the warm path the server
        is asked to write first, so what leaves with it is what was typed and
        not just what was last saved. A field AT-SPI can write directly then
        takes the text right now -- that needs no focus, so it lands without
        dragging anybody back. A field that needs the clipboard cannot be
        written without focusing its window, which would haul the user to the
        workspace they just left; that one keeps its buffer on disk and says
        where, which costs the convenience and loses nothing.
        """
        # Whatever happens below, the editor's own exit must not write again.
        self._dismissed = True
        if self.warm:
            warm_save(log=self._log)
        edited = None
        try:
            with open(self.path, encoding="utf-8") as temp:
                edited = strip_added_newline(self.original, temp.read())
        except OSError as error:
            self._log(f"could not read the edited file back: {error!r}")

        if edited is None or edited == self._sent:
            self._log("workspace changed; nothing further to write back")
            self._close()
            return None
        if strategy(self.field.accessible) == EDITABLE_TEXT:
            self._log("workspace changed; writing back before closing")
            self._sent = edited
            self.on_write(edited)
            self._close()
            return None
        self._log(f"workspace changed; keeping the buffer at {self.path}")
        self._close(keep_file=True)
        return self.path

    def _close(self, keep_file=False):
        if self.closed:
            return
        self.closed = True
        for name in ("_monitor", "_rows_monitor"):
            monitor = getattr(self, name, None)
            if monitor is not None:
                try:
                    monitor.cancel()
                except Exception:
                    pass
            setattr(self, name, None)
        try:
            self.window.destroy()
        except Exception:
            pass
        if self.warm:
            # The server's buffer state is not reusable -- a dismissed editor
            # leaves it modified, and the next `:edit` into it fails -- so it
            # is replaced rather than kept. The new one warms up in the
            # background, off the path of anything the user is waiting for.
            stop_warm()
            start_warm(log=self._log)
            # ...but "in the background" is not "instantly": measured, the
            # replacement takes over a second to load the config and answer.
            # Edit two fields in quick succession and the second one found no
            # server and started an editor from cold -- the slow path, for no
            # reason other than arriving too soon after the first. Waiting for
            # the socket costs nothing here (this is the close, nobody is
            # typing) and spares the next open.
            wait_warm(log=self._log)
            # And a server that answers is not a server that is ready -- see
            # prime_warm. Also free here, for the same reason.
            prime_warm(log=self._log)
        # The field's contents were on disk; they should not stay there --
        # unless this is the one case that deliberately left them, where the
        # file is the only copy of the edit (see close_for_workspace). The
        # next daemon start sweeps it.
        if not keep_file:
            try:
                os.unlink(self.path)
            except OSError:
                pass
        try:
            os.unlink(rows_path(self.path))
        except OSError:
            pass
        self.on_done()
