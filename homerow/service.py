"""Resident daemon.

Almost all of the old per-invocation cost was fixed startup: ~435ms of Python
interpreter and PyGObject imports before any useful work. None of that depends
on the request, so it happens once here and every hint after that is just a
socket write.
"""

import os
import socket
import sys
import time

import gi

gi.require_version("Atspi", "2.0")
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Atspi, GLib, Gtk  # noqa: E402

from . import (  # noqa: E402
    caret, click, config, elements, hints, scroll, search, windows, x11,
)
from .overlay import Overlay, screen_size  # noqa: E402


def log_path():
    """Rotating log location.

    Diagnosing anything used to mean restarting under --debug and hoping the
    problem recurred; the interesting run was always the one not being logged.
    The daemon now always logs, so a report can be answered from the record.
    """
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    directory = os.path.join(base, "homerow")
    os.makedirs(directory, exist_ok=True)
    return os.path.join(directory, "homerow.log")


def socket_path():
    runtime = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    return os.path.join(runtime, "homerow.sock")


def mode_path():
    """Where the name of the currently-open mode is written, for a bar widget
    to poll.

    Runtime dir, not the persistent state dir the log lives in: this is live
    state, not a record, and should not survive past this login session if
    the daemon dies mid-mode. Absence (not an empty file) means idle, so a
    poller's check is a bare `open()` that fails shut -- a leftover file from
    a killed daemon cannot be mistaken for a mode that is actually running,
    because run() below clears it unconditionally on every startup.
    """
    runtime = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    return os.path.join(runtime, "homerow-mode")


def is_running(path=None):
    """True if a daemon is already answering on the socket."""
    path = path or socket_path()
    if not os.path.exists(path):
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.5)
            probe.connect(path)
            probe.sendall(b"ping\n")
            return probe.recv(16).strip() == b"ok"
    except OSError:
        return False


class Daemon:
    def __init__(self, debug=False):
        self.debug = debug
        self.overlay = None
        self.path = socket_path()
        self.log = _open_log()

    def run(self):
        if is_running(self.path):
            print("homerow: daemon already running", file=sys.stderr)
            return 1

        # Nothing answered, so any socket file left behind is stale.
        if os.path.exists(self.path):
            os.unlink(self.path)
        # Likewise a mode file: if the last daemon died mid-mode, its overlay
        # died with it, so nothing is actually open even though this file
        # says otherwise.
        self._clear_mode()

        Atspi.init()
        # Without a cap, one hung app's accessibility service blocks every
        # AT-SPI call forever -- and every mode makes those calls synchronously
        # on this same main loop, so it takes the whole daemon down with it.
        Atspi.set_timeout(config.ATSPI_CALL_TIMEOUT_MS, config.ATSPI_CALL_TIMEOUT_MS)

        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(self.path)
        self.server.listen(8)
        self.server.setblocking(False)
        GLib.io_add_watch(
            self.server.fileno(), GLib.PRIORITY_DEFAULT, GLib.IO_IN,
            self._on_connection,
        )

        # Touching the accessibility tree once now means the first real hint
        # does not pay for connection setup.
        try:
            Atspi.get_desktop(0).get_child_count()
        except Exception:
            pass

        self._log(f"listening on {self.path}")
        try:
            Gtk.main()
        finally:
            self._cleanup()
        return 0

    def _cleanup(self):
        try:
            self.server.close()
        except OSError:
            pass
        try:
            os.unlink(self.path)
        except OSError:
            pass
        self._clear_mode()

    def _set_mode(self, name):
        try:
            with open(mode_path(), "w", encoding="utf-8") as handle:
                handle.write(name)
        except OSError:
            pass

    def _clear_mode(self):
        try:
            os.unlink(mode_path())
        except OSError:
            pass

    def _on_connection(self, _fd, _condition):
        try:
            conn, _ = self.server.accept()
        except OSError:
            return True

        with conn:
            conn.settimeout(0.5)
            try:
                command = conn.recv(64).decode("utf-8", "replace").strip()
            except OSError:
                return True
            # Answer and hang up before doing the work, so the client process
            # exits immediately instead of waiting on the overlay. A client
            # that hung up without reading still gets its command run --
            # failing to acknowledge is not a reason to drop the request.
            try:
                conn.sendall(b"ok\n")
            except OSError:
                pass

        if command == "hint":
            GLib.idle_add(self._hint)
        elif command == "scroll":
            GLib.idle_add(self._scroll)
        elif command == "search":
            GLib.idle_add(self._search)
        elif command == "caret":
            GLib.idle_add(self._caret)
        elif command == "caret_search":
            GLib.idle_add(self._caret_search)
        elif command == "quit":
            GLib.idle_add(Gtk.main_quit)
        return True

    def _hint(self):
        if self.overlay is not None:
            # Pressing the hotkey again re-hints rather than doing nothing.
            # Silently ignoring it meant one overlay that failed to close made
            # every later press a no-op with no clue why.
            self._log("overlay already open; replacing it")
            try:
                self.overlay.dismiss()
            except Exception:
                pass
            self.overlay = None

        started = time.perf_counter()
        width, height = screen_size()
        active = elements.active_window()
        self._log(f"active window: {active}")
        try:
            found = elements.collect(width, height)
        except Exception as error:
            print(f"homerow: collect failed: {error!r}", file=sys.stderr)
            found = []

        try:
            switchable = windows.collect(width, height, _active_window_id())
        except Exception as error:
            print(f"homerow: window scan failed: {error!r}", file=sys.stderr)
            switchable = []
        found = found + switchable

        if not found:
            self._log("no hintable elements")
            _notify("No hintable elements — this app exposes no "
                    "accessibility tree.")
            return False

        collected = time.perf_counter()
        window_count = len(switchable)
        labels = hints.assign(found)
        self.overlay = Overlay(found, labels, self._choose, self._finished)
        self._set_mode("hint")
        self.overlay.show()
        shown = time.perf_counter()
        self._log(
            f"{len(found) - window_count} elements + "
            f"{window_count} windows: "
            f"collect {(collected - started) * 1000:.0f}ms, "
            f"overlay {(shown - collected) * 1000:.0f}ms, "
            f"total {(shown - started) * 1000:.0f}ms"
        )
        return False

    def _search(self):
        if self.overlay is not None:
            self._log("overlay already open; replacing it")
            try:
                self.overlay.dismiss()
            except Exception:
                pass
            self.overlay = None

        width, height = screen_size()
        try:
            found = elements.collect(width, height)
        except Exception as error:
            print(f"homerow: collect failed: {error!r}", file=sys.stderr)
            return False
        if not found:
            self._log("nothing to search")
            _notify("Nothing to search — this app exposes no accessibility "
                    "tree.")
            return False

        # Search covers text blocks as well as clickable elements. Restricting
        # it to things that can be hinted meant the thing you were looking for
        # -- a heading, a paragraph, a list entry -- was often not in the set
        # being searched at all, so an exact match simply never appeared.
        try:
            blocks = caret.collect(width, height,
                                   min_chars=config.SEARCH_MIN_CHARS,
                                   require_text=False)
        except Exception:
            blocks = []
        # A link and the text block inside it are the same target seen twice,
        # and they are not the same rectangle, so a geometry key alone left
        # both -- a label with a second label nested inside it.
        occupied = {(e.x // 6, e.y // 6, e.w // 6, e.h // 6) for e in found}
        for block in blocks:
            key = (block.x // 6, block.y // 6, block.w // 6, block.h // 6)
            if key in occupied:
                continue
            if any(_overlaps(block, e) for e in found):
                continue
            occupied.add(key)
            found.append(block)

        self._log(f"search over {len(found)} elements "
                  f"({len(blocks)} text blocks)")

        def on_pick(element):
            self._choose(element, click.BUTTON_LEFT, ())

        self.overlay = search.SearchPrompt(
            found, on_pick, self._finished,
        )
        self._set_mode("search")
        self.overlay.show()
        return False

    def _caret(self):
        if self.overlay is not None:
            self._log("overlay already open; replacing it")
            try:
                self.overlay.dismiss()
            except Exception:
                pass
            self.overlay = None

        started = time.perf_counter()
        width, height = screen_size()

        native = _native_caret_key()
        if native:
            self._log(f"handing caret off to the app's own mode ({native})")
            x11.send_combo(native)
            # Say so: an unannounced handoff looks like the key did nothing,
            # since the app's own mode looks nothing like ours.
            _notify("Caret mode: using this app's own vim mode.")
            return False

        try:
            blocks = caret.collect(width, height)
        except Exception as error:
            print(f"homerow: caret scan failed: {error!r}", file=sys.stderr)
            return False

        if not blocks:
            self._log("no text to put a caret in")
            _notify("No text here — this app publishes no text through "
                    "accessibility.")
            return False

        self._log(f"{len(blocks)} text blocks in "
                  f"{(time.perf_counter() - started) * 1000:.0f}ms")

        def start(block):
            def go():
                self.overlay = caret.CaretSession(
                    block, self._finished, blocks=blocks,
                    on_search=lambda: GLib.idle_add(self._caret_search))
                self._set_mode("caret")
                self.overlay.show()
                return False
            GLib.idle_add(go)

        start(caret.best(blocks))
        return False

    def _caret_search(self):
        """Type to find a word, pick it, and land a caret exactly there."""
        if self.overlay is not None:
            self._log("overlay already open; replacing it")
            try:
                self.overlay.dismiss()
            except Exception:
                pass
            self.overlay = None

        started = time.perf_counter()
        width, height = screen_size()

        native = _native_caret_key()
        if native:
            self._log(f"handing caret off to the app's own mode ({native})")
            x11.send_combo(native)
            _notify("Caret mode: using this app's own vim mode.")
            return False

        try:
            blocks = caret.collect(width, height)
        except Exception as error:
            print(f"homerow: caret scan failed: {error!r}", file=sys.stderr)
            return False

        if not blocks:
            self._log("no text to put a caret in")
            _notify("No text here — this app publishes no text through "
                    "accessibility.")
            return False

        self._log(f"{len(blocks)} text blocks in "
                  f"{(time.perf_counter() - started) * 1000:.0f}ms; "
                  f"entering caret search")

        def on_pick(hit):
            # Deferred: this fires from inside CaretSearchPrompt._close(),
            # whose on_done runs right after and would clear self.overlay
            # the instant we set it here otherwise -- see _enter_scroll for
            # the same reasoning.
            def go():
                session = caret.CaretSession(
                    hit.block, self._finished, blocks=blocks,
                    on_search=lambda: GLib.idle_add(self._caret_search))
                session.offset = hit.offset
                session._sync_caret()
                self.overlay = session
                self._set_mode("caret")
                session.show()
                return False
            GLib.idle_add(go)

        self.overlay = caret.CaretSearchPrompt(blocks, on_pick, self._finished)
        self._set_mode("caret-search")
        self.overlay.show()
        return False

    def _scroll(self):
        if self.overlay is not None:
            self._log("overlay already open; replacing it")
            try:
                self.overlay.dismiss()
            except Exception:
                pass
            self.overlay = None

        started = time.perf_counter()
        width, height = screen_size()
        try:
            regions = scroll.collect(width, height)
        except Exception as error:
            print(f"homerow: scroll scan failed: {error!r}", file=sys.stderr)
            return False

        if not regions and config.SCROLL_FALLBACK_TO_WINDOW:
            fallback = scroll.window_region()
            if fallback is not None:
                self._log("no region reported; scrolling the window itself")
                self._enter_scroll(fallback)
                return False

        if not regions:
            self._log("no scrollable regions")
            _notify("Nothing scrollable here.")
            return False

        # No picker. Acting on the best candidate immediately is most of what
        # makes this feel immediate rather than like a dialog; Tab inside the
        # session reaches the others, so a wrong guess costs one key.
        chosen = scroll.best(regions)
        # best() can return a whole-window fallback that isn't one of the
        # detected candidates (pointer sat over something AT-SPI didn't
        # recognise as scrollable) -- fold it into the Tab list so cycling
        # still reaches the regions that WERE detected, instead of Tab's
        # first press silently discarding the choice just made.
        if chosen not in regions:
            regions = [chosen] + regions
        self._log(f"{len(regions)} scrollable region(s) in "
                  f"{(time.perf_counter() - started) * 1000:.0f}ms; "
                  f"entering scroll mode")
        self._enter_scroll(chosen, regions)
        return False

    def _enter_scroll(self, region, regions=None):
        # Deferred: when this comes from the picker, the picker's own on_done
        # runs after on_choose and would clear self.overlay right after we set
        # it, leaving the daemon unable to dismiss the session later.
        def start():
            self.overlay = scroll.ScrollSession(
                region, self._finished, regions=regions)
            self._set_mode("scroll")
            self.overlay.show()
            return False
        GLib.idle_add(start)

    def _choose(self, element, button, modifiers):
        method = click.perform(element, button, modifiers)
        self._log(f"clicked button={button} mods={modifiers} via {method}")
        self._release_chord_if_done(element)

    def _release_chord_if_done(self, element):
        """Leave the qtile chord on a text field, or a switch to another window.

        The chord stays active between actions so h/s/f keep working across a
        chain of clicks in the *same* window -- but a focused text field needs
        those letters to be letters, and a window switch is just as much "done
        with the chord" as that: h/s/f in a freshly-focused window are still
        going to want to be homerow's own bindings again from a fresh alt+space,
        not silently reopen a chord this window never asked for. Reopening
        the chord in either case is worse than leaving it, so this exits it --
        and only in these two cases.
        """
        if getattr(element, "kind", "element") == "window":
            self._log("switched window; leaving the chord")
        else:
            try:
                role = element.role
            except Exception:
                return
            if role not in config.TEXT_ENTRY_ROLES:
                return
            self._log(f"clicked a {role}; leaving the chord so typing works")
        try:
            import subprocess
            subprocess.run(
                ["qtile", "cmd-obj", "-o", "root", "-f", "ungrab_chord"],
                timeout=2, check=False, capture_output=True,
            )
        except (OSError, subprocess.SubprocessError):
            pass

    def _finished(self):
        self.overlay = None
        self._clear_mode()

    def _log(self, message):
        now = time.time()
        stamp = (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
                 + f".{int(now * 1000) % 1000:03d}")
        line = f"{stamp} {message}"
        if self.debug:
            print(f"homerow: {message}", flush=True)
        if self.log is not None:
            try:
                self.log.write(line + "\n")
                self.log.flush()
            except Exception:
                pass


def _open_log():
    """Append to the log, rotating once it gets large."""
    try:
        path = log_path()
        if os.path.exists(path) and os.path.getsize(path) > config.LOG_MAX_BYTES:
            os.replace(path, path + ".1")
        return open(path, "a", encoding="utf-8")
    except Exception:
        return None


def _overlaps(a, b):
    """True if two targets cover essentially the same place."""
    left, right = max(a.x, b.x), min(a.x + a.w, b.x + b.w)
    top, bottom = max(a.y, b.y), min(a.y + a.h, b.y + b.h)
    if right <= left or bottom <= top:
        return False
    shared = (right - left) * (bottom - top)
    smaller = min(a.w * a.h, b.w * b.h)
    return bool(smaller) and shared / smaller >= config.OVERLAP_SAME


def _native_caret_key():
    """The key that enters this app's own caret mode, if it has one."""
    window = elements.active_window()
    if window is None:
        return None
    app = elements._app_for_pid(window[0])
    if app is None:
        return None
    try:
        name = (app.get_name() or "").lower()
    except Exception:
        return None
    for known, key in config.CARET_NATIVE.items():
        if known in name:
            return key
    return None


def _active_window_id():
    """X id of the focused window, so it is not offered as a switch target.

    x11.active_window_id() is the same ctypes call elements.active_window()
    already made a moment earlier in the same request (~0.3ms); shelling out
    to xdotool here as well cost an extra ~10-30ms process spawn on every
    single hint press for no new information, so that fallback is now only
    used on the systems where the ctypes binding is unavailable.
    """
    if x11.available():
        return x11.active_window_id()
    import subprocess
    try:
        result = subprocess.run(
            ["xdotool", "getactivewindow"],
            capture_output=True, text=True, timeout=1,
        )
        return int(result.stdout.strip()) if result.returncode == 0 else None
    except (ValueError, OSError, subprocess.SubprocessError):
        return None


def _notify(message):
    import subprocess
    try:
        subprocess.run(
            ["notify-send", "-t", "1500", "-a", "homerow", "Homerow", message],
            timeout=1, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        pass
