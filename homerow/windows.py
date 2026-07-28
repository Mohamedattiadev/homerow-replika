"""Other open windows as hint targets.

Homerow itself only hints inside the focused app. Hinting the *other* visible
windows too means one keypress covers both "click that button" and "switch to
that terminal", which is the common case on a tiling layout where several
windows are on screen at once.
"""

import subprocess
from dataclasses import dataclass

from . import config, x11


@dataclass
class WindowTarget:
    """A window to switch to. Mirrors elements.Element's geometry fields."""

    window_id: int
    name: str
    x: int
    y: int
    w: int
    h: int

    kind = "window"
    role = "window"

    @property
    def center(self):
        return self.x + self.w // 2, self.y + self.h // 2


def _run(args):
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=1.5
        )
        return result.stdout if result.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _managed_windows():
    """Window ids the WM manages, from _NET_CLIENT_LIST.

    Going through the EWMH list rather than a raw X tree walk keeps out tray
    icons, menus, and our own override-redirect overlay.
    """
    out = _run(["xprop", "-root", "_NET_CLIENT_LIST"])
    if "#" not in out:
        return []
    ids = out.split("#", 1)[1]
    found = []
    for token in ids.split(","):
        token = token.strip()
        if token.startswith("0x"):
            try:
                found.append(int(token, 16))
            except ValueError:
                continue
    return found


def _visible_ids():
    out = _run(["xdotool", "search", "--onlyvisible", "--name", ""])
    return {int(line) for line in out.split() if line.isdigit()}


def _describe(window_ids):
    """Geometry and title for many windows in one xdotool invocation.

    One process per window cost ~950ms for a handful of them, which dwarfed
    everything else. xdotool chains commands, so this is a single spawn.
    """
    if not window_ids:
        return {}

    argv = ["xdotool"]
    for window_id in window_ids:
        argv += ["getwindowgeometry", "--shell", str(window_id)]
        argv += ["getwindowname", str(window_id)]

    described = {}
    current = None
    expecting_name = False
    for line in _run(argv).splitlines():
        line = line.rstrip()
        if expecting_name:
            if current is not None:
                described[current]["name"] = line.strip()
            expecting_name = False
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key == "WINDOW":
            try:
                current = int(value)
            except ValueError:
                current = None
                continue
            described[current] = {"name": ""}
        elif current is not None and key in ("X", "Y", "WIDTH", "HEIGHT"):
            try:
                described[current][key] = int(value)
            except ValueError:
                pass
        elif key == "SCREEN":
            # --shell ends with SCREEN=; getwindowname's line comes next.
            expecting_name = True
    return described


def collect(screen_w, screen_h, exclude_id=None):
    """Visible windows worth offering as switch targets."""
    if not config.HINT_WINDOWS:
        return []

    if x11.available():
        return _collect_x11(screen_w, screen_h, exclude_id)

    visible = _visible_ids()
    candidates = [
        window_id for window_id in _managed_windows()
        if window_id != exclude_id and window_id in visible
    ]

    targets = []
    for window_id, info in _describe(candidates).items():
        try:
            x, y = info["X"], info["Y"]
            w, h = info["WIDTH"], info["HEIGHT"]
        except KeyError:
            continue
        if w < config.MIN_WINDOW_SIZE or h < config.MIN_WINDOW_SIZE:
            continue
        # Fully offscreen windows belong to other groups/workspaces.
        if x + w <= 0 or y + h <= 0 or x >= screen_w or y >= screen_h:
            continue
        targets.append(
            WindowTarget(window_id, info.get("name", ""), x, y, w, h)
        )
    return targets


def is_offerable(x, y, w, h, screen_w, screen_h):
    """Whether a window is worth offering as a switch target.

    Pulled out of the collector so the rule can be tested without an X server;
    it is the same rule that decides whether a window is really on screen.
    """
    if w < config.MIN_WINDOW_SIZE or h < config.MIN_WINDOW_SIZE:
        return False
    return not (x + w <= 0 or y + h <= 0 or x >= screen_w or y >= screen_h)


def _collect_x11(screen_w, screen_h, exclude_id):
    """Same as collect(), without spawning a process per window.

    window_geometry() returns None for anything not viewable, which also
    filters out windows sitting on other groups -- no separate visibility
    query needed.
    """
    targets = []
    for window_id in x11.client_list():
        if window_id == exclude_id:
            continue
        geometry = x11.window_geometry(window_id)
        if geometry is None:
            continue
        x, y, w, h = geometry
        if not is_offerable(x, y, w, h, screen_w, screen_h):
            continue
        targets.append(
            WindowTarget(window_id, x11.window_name(window_id), x, y, w, h)
        )
    return targets


def activate(target):
    """Focus a window, switching group/workspace if the WM needs to."""
    try:
        subprocess.run(
            ["xdotool", "windowactivate", "--sync", str(target.window_id)],
            timeout=2, check=False,
        )
        return "activate"
    except (OSError, subprocess.SubprocessError):
        return "failed"
