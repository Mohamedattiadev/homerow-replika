"""Dispatch a click at an element."""

import subprocess

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

    # A plain left click with no modifiers is the only case the accessible
    # action can express; anything else has to go through the pointer.
    if config.CLICK_METHOD == "atspi" and button == BUTTON_LEFT \
            and not modifiers:
        if _atspi_click(element):
            return "atspi"
    return _pointer_click(element, button, modifiers)


def _atspi_click(element):
    try:
        action = element.accessible.get_action_iface()
        if action is None:
            return False
        for i in range(action.get_n_actions()):
            if (action.get_action_name(i) or "").lower() in _ACTIVATE:
                return bool(action.do_action(i))
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
        if x11.click(button, x, y, modifiers):
            if origin:
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
