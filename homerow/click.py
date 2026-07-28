"""Dispatch a click at an element."""

import subprocess
import time

import gi

gi.require_version("Atspi", "2.0")
from gi.repository import Atspi  # noqa: E402

from . import config, x11  # noqa: E402

BUTTON_LEFT, BUTTON_MIDDLE, BUTTON_RIGHT = 1, 2, 3

# Action names apps use for "activate this"; varies by toolkit and role.
_ACTIVATE = ("click", "press", "activate", "jump", "open", "toggle")

def perform(element, button=BUTTON_LEFT, modifiers=()):
    """Act on a target. Returns the method used, for logging."""
    if getattr(element, "kind", "element") == "window":
        from . import windows
        return windows.activate(element)

    # Prefer the element's own accessible action for a plain left click. It
    # moves no pointer at all, so it cannot be mistaken for a drag -- which is
    # what kept happening to links, where press-move-release is a real gesture
    # meaning "pick this up". Timing tweaks only made that rarer, never safe.
    # Anything the action cannot express (modifiers, middle, right) still has
    # to go through the pointer.
    if config.CLICK_METHOD != "pointer_only" and button == BUTTON_LEFT \
            and not modifiers:
        if _atspi_click(element):
            return "atspi"
    return _pointer_click(element, button, modifiers)


def _atspi_click(element):
    """Fire the element's own activate action, or focus it and press Enter.

    Qt WebEngine exposes only "SetFocus" on links -- no click action at all --
    so without the focus+Enter path every web link falls through to the
    pointer, which is where drags come from. Activating a focused link with
    Enter is what a keyboard user would do anyway.
    """
    try:
        action = element.accessible.get_action_iface()
        if action is None:
            return False
        names = []
        for i in range(action.get_n_actions()):
            try:
                names.append((Atspi.Action.get_action_name(action, i) or "").lower())
            except Exception:
                names.append("")
        for index, name in enumerate(names):
            if name in _ACTIVATE:
                return bool(action.do_action(index))
        # No focus+Enter fallback here on purpose. Qt WebEngine exposes only
        # "SetFocus" on links, and focusing then pressing Enter reports
        # success while navigating nowhere -- qutebrowser's normal mode does
        # not follow a focused link. Claiming success meant the click silently
        # did nothing instead of falling through to the pointer.
    except Exception:
        pass
    return False


def _pointer_click(element, button, modifiers):
    x, y = element.center

    # Restoring the pointer keeps hover states and drag targets from being
    # disturbed by a click the user made with the keyboard.
    origin = _pointer_position()

    if x11.available():
        # XTest in-process: no subprocess, so the click lands in well under a
        # millisecond instead of the ~15ms an xdotool spawn costs.
        if x11.click(button, x, y, modifiers,
                     hold_ms=config.CLICK_HOLD_MS,
                     pre_ms=config.CLICK_PREPRESS_MS):
            if origin:
                # Let the click be consumed before moving the pointer away.
                # Warping straight back turns the click into a drag gesture.
                time.sleep(config.CLICK_SETTLE_MS / 1000)
                x11.warp_pointer(*origin)
            return "xtest"

    cmd = ["xdotool", "mousemove", "--sync", str(x), str(y)]
    for mod in modifiers:
        cmd += ["keydown", mod]
    cmd += ["click", str(button)]
    for mod in reversed(modifiers):
        cmd += ["keyup", mod]
    if origin:
        cmd += ["mousemove", "--sync", str(origin[0]), str(origin[1])]

    try:
        subprocess.run(cmd, timeout=2, check=False)
    except (OSError, subprocess.SubprocessError):
        return "failed"
    return "pointer"


def _pointer_position():
    if x11.available():
        position = x11.pointer_position()
        if position:
            return position
    try:
        out = subprocess.run(
            ["xdotool", "getmouselocation", "--shell"],
            capture_output=True, text=True, timeout=1,
        )
        if out.returncode != 0:
            return None
        values = dict(
            line.split("=", 1)
            for line in out.stdout.strip().splitlines() if "=" in line
        )
        return int(values["X"]), int(values["Y"])
    except (ValueError, KeyError, OSError, subprocess.SubprocessError):
        return None


def focus(element):
    """Put keyboard focus on an element without clicking it."""
    try:
        component = element.accessible.get_component_iface()
        if component is not None:
            return bool(component.grab_focus())
    except Exception:
        pass
    return False
