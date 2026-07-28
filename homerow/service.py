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
    caret, click, config, elements, hints, scroll, search, windows,
)
from .overlay import Overlay, screen_size  # noqa: E402


def socket_path():
    runtime = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    return os.path.join(runtime, "homerow.sock")


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

    def run(self):
        if is_running(self.path):
            print("homerow: daemon already running", file=sys.stderr)
            return 1

        # Nothing answered, so any socket file left behind is stale.
        if os.path.exists(self.path):
            os.unlink(self.path)

        Atspi.init()

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

        self._log(f"search over {len(found)} elements")

        def on_pick(element):
            self._choose(element, click.BUTTON_LEFT, ())

        self.overlay = search.SearchPrompt(
            found, on_pick, self._finished,
        )
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
                self.overlay = caret.CaretSession(block, self._finished)
                self.overlay.show()
                return False
            GLib.idle_add(go)

        if len(blocks) == 1:
            start(blocks[0])
            return False

        self.overlay = Overlay(
            blocks, hints.assign(blocks),
            lambda block, button, modifiers: start(block),
            self._finished,
        )
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

        # One region is not worth asking about -- go straight in.
        if len(regions) == 1:
            self._log(f"1 region in "
                      f"{(time.perf_counter() - started) * 1000:.0f}ms; "
                      f"entering scroll mode")
            self._enter_scroll(regions[0])
            return False

        labels = hints.assign(regions)
        self._log(f"{len(regions)} scrollable regions in "
                  f"{(time.perf_counter() - started) * 1000:.0f}ms")
        self.overlay = Overlay(
            regions, labels,
            lambda region, button, modifiers: self._enter_scroll(region),
            self._finished,
        )
        self.overlay.show()
        return False

    def _enter_scroll(self, region):
        # Deferred: when this comes from the picker, the picker's own on_done
        # runs after on_choose and would clear self.overlay right after we set
        # it, leaving the daemon unable to dismiss the session later.
        def start():
            self.overlay = scroll.ScrollSession(region, self._finished)
            self.overlay.show()
            return False
        GLib.idle_add(start)

    def _choose(self, element, button, modifiers):
        method = click.perform(element, button, modifiers)
        self._log(f"clicked button={button} mods={modifiers} via {method}")
        self._release_chord_if_typing(element)

    def _release_chord_if_typing(self, element):
        """Leave the qtile chord when the click lands in a text field.

        The chord stays active between actions so h/s/f keep working, but a
        focused text field needs those letters to be letters. Clicking into one
        and then having `h` open hint mode instead of typing `h` is worse than
        reopening the chord, so this exits it -- and only in that case.
        """
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

    def _log(self, message):
        if self.debug:
            print(f"homerow: {message}", flush=True)


def _active_window_id():
    """X id of the focused window, so it is not offered as a switch target."""
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
