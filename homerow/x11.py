"""Direct X11 access, replacing xdotool on the hot paths.

Every xdotool call is a process spawn: ~10-30ms before it does anything, and
we were making several per keypress -- one to find the active window, three to
enumerate windows, one per click. That fixed cost dominated everything the
daemon was carefully optimised to avoid.

These are the handful of calls we actually need, bound with ctypes so there is
no new dependency. Each entry point falls back to xdotool if the binding is
unavailable, so a missing libXtst degrades to the old behaviour instead of
breaking.
"""

import ctypes
import ctypes.util
import subprocess
import time

_x11 = None
_xtst = None
_display = None


class _XWindowAttributes(ctypes.Structure):
    _fields_ = [
        ("x", ctypes.c_int), ("y", ctypes.c_int),
        ("width", ctypes.c_int), ("height", ctypes.c_int),
        ("border_width", ctypes.c_int),
        ("depth", ctypes.c_int),
        ("visual", ctypes.c_void_p),
        ("root", ctypes.c_ulong),
        ("class_", ctypes.c_int),
        ("bit_gravity", ctypes.c_int),
        ("win_gravity", ctypes.c_int),
        ("backing_store", ctypes.c_int),
        ("backing_planes", ctypes.c_ulong),
        ("backing_pixel", ctypes.c_ulong),
        ("save_under", ctypes.c_int),
        ("colormap", ctypes.c_ulong),
        ("map_installed", ctypes.c_int),
        ("map_state", ctypes.c_int),
        ("all_event_masks", ctypes.c_long),
        ("your_event_mask", ctypes.c_long),
        ("do_not_propagate_mask", ctypes.c_long),
        ("override_redirect", ctypes.c_int),
        ("screen", ctypes.c_void_p),
    ]


def _load():
    """Open a private display connection. Returns False if unavailable."""
    global _x11, _xtst, _display
    if _display is not None:
        return True
    try:
        x11_path = ctypes.util.find_library("X11")
        tst_path = ctypes.util.find_library("Xtst")
        if not x11_path:
            return False
        x11 = ctypes.CDLL(x11_path)
        x11.XOpenDisplay.restype = ctypes.c_void_p
        x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        display = x11.XOpenDisplay(None)
        if not display:
            return False

        x11.XInternAtom.restype = ctypes.c_ulong
        x11.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p,
                                    ctypes.c_int]
        x11.XDefaultRootWindow.restype = ctypes.c_ulong
        x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        x11.XGetWindowProperty.restype = ctypes.c_int
        x11.XGetWindowProperty.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_long,
            ctypes.c_long, ctypes.c_int, ctypes.c_ulong,
            ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.POINTER(ctypes.c_ubyte)),
        ]
        x11.XFree.argtypes = [ctypes.c_void_p]
        x11.XGetWindowAttributes.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong,
            ctypes.POINTER(_XWindowAttributes),
        ]
        x11.XTranslateCoordinates.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong,
            ctypes.c_int, ctypes.c_int,
            ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_ulong),
        ]
        x11.XQueryPointer.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong,
            ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_uint),
        ]
        x11.XWarpPointer.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong,
            ctypes.c_int, ctypes.c_int, ctypes.c_uint, ctypes.c_uint,
            ctypes.c_int, ctypes.c_int,
        ]
        x11.XStringToKeysym.restype = ctypes.c_ulong
        x11.XStringToKeysym.argtypes = [ctypes.c_char_p]
        x11.XKeysymToKeycode.restype = ctypes.c_ubyte
        x11.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        x11.XGetAtomName.restype = ctypes.c_void_p
        x11.XGetAtomName.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        x11.XFlush.argtypes = [ctypes.c_void_p]
        x11.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]

        xtst = ctypes.CDLL(tst_path) if tst_path else None
        if xtst is not None:
            xtst.XTestFakeButtonEvent.argtypes = [
                ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
            xtst.XTestFakeKeyEvent.argtypes = [
                ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
            xtst.XTestFakeMotionEvent.argtypes = [
                ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                ctypes.c_ulong]

        _x11, _xtst, _display = x11, xtst, display
        return True
    except Exception:
        return False


def available():
    return _load()


def _property(window, name, expect_type=0):
    """Read an X property as a list of longs."""
    atom = _x11.XInternAtom(_display, name.encode(), False)
    actual_type = ctypes.c_ulong()
    actual_format = ctypes.c_int()
    count = ctypes.c_ulong()
    remaining = ctypes.c_ulong()
    data = ctypes.POINTER(ctypes.c_ubyte)()
    status = _x11.XGetWindowProperty(
        _display, window, atom, 0, 1024, False, expect_type,
        ctypes.byref(actual_type), ctypes.byref(actual_format),
        ctypes.byref(count), ctypes.byref(remaining), ctypes.byref(data),
    )
    if status != 0 or not data:
        return []
    try:
        values = ctypes.cast(
            data, ctypes.POINTER(ctypes.c_ulong))[:count.value]
        return list(values)
    finally:
        _x11.XFree(data)


def active_window_id():
    if not _load():
        return None
    root = _x11.XDefaultRootWindow(_display)
    values = _property(root, "_NET_ACTIVE_WINDOW")
    return values[0] if values else None


def screen_size():
    """Root window size, for judging whether a window is really on screen."""
    if not _load():
        return None
    root = _x11.XDefaultRootWindow(_display)
    attrs = _XWindowAttributes()
    if not _x11.XGetWindowAttributes(_display, root, ctypes.byref(attrs)):
        return None
    return attrs.width, attrs.height


def client_list():
    if not _load():
        return []
    root = _x11.XDefaultRootWindow(_display)
    return _property(root, "_NET_CLIENT_LIST")


def window_type(window):
    """The _NET_WM_WINDOW_TYPE atom names for a window."""
    if not _load():
        return []
    names = []
    for atom in _property(window, "_NET_WM_WINDOW_TYPE"):
        try:
            raw = _x11.XGetAtomName(_display, atom)
            if raw:
                names.append(ctypes.cast(raw, ctypes.c_char_p).value.decode())
                _x11.XFree(raw)
        except Exception:
            continue
    return names


def window_pid(window):
    if not _load():
        return None
    values = _property(window, "_NET_WM_PID")
    return values[0] if values else None


def window_geometry(window):
    """(x, y, w, h) in root coordinates, or None. Unmapped windows return None."""
    if not _load():
        return None
    attrs = _XWindowAttributes()
    if not _x11.XGetWindowAttributes(_display, window, ctypes.byref(attrs)):
        return None
    if attrs.map_state != 2:  # IsViewable
        return None
    root = _x11.XDefaultRootWindow(_display)
    x, y = ctypes.c_int(), ctypes.c_int()
    child = ctypes.c_ulong()
    _x11.XTranslateCoordinates(_display, window, root, 0, 0,
                               ctypes.byref(x), ctypes.byref(y),
                               ctypes.byref(child))
    return x.value, y.value, attrs.width, attrs.height


def window_name(window):
    if not _load():
        return ""
    for prop in ("_NET_WM_NAME", "WM_NAME"):
        atom = _x11.XInternAtom(_display, prop.encode(), False)
        actual_type = ctypes.c_ulong()
        actual_format = ctypes.c_int()
        count = ctypes.c_ulong()
        remaining = ctypes.c_ulong()
        data = ctypes.POINTER(ctypes.c_ubyte)()
        status = _x11.XGetWindowProperty(
            _display, window, atom, 0, 256, False, 0,
            ctypes.byref(actual_type), ctypes.byref(actual_format),
            ctypes.byref(count), ctypes.byref(remaining), ctypes.byref(data),
        )
        if status == 0 and data:
            try:
                raw = bytes(ctypes.cast(
                    data, ctypes.POINTER(ctypes.c_ubyte))[:count.value])
                text = raw.decode("utf-8", "replace").strip("\x00")
                if text:
                    return text
            finally:
                _x11.XFree(data)
    return ""


def pointer_position():
    if not _load():
        return None
    root = _x11.XDefaultRootWindow(_display)
    root_ret, child_ret = ctypes.c_ulong(), ctypes.c_ulong()
    rx, ry, wx, wy = (ctypes.c_int() for _ in range(4))
    mask = ctypes.c_uint()
    ok = _x11.XQueryPointer(
        _display, root, ctypes.byref(root_ret), ctypes.byref(child_ret),
        ctypes.byref(rx), ctypes.byref(ry), ctypes.byref(wx),
        ctypes.byref(wy), ctypes.byref(mask))
    return (rx.value, ry.value) if ok else None


def warp_pointer(x, y):
    if not _load():
        return False
    root = _x11.XDefaultRootWindow(_display)
    _x11.XWarpPointer(_display, 0, root, 0, 0, 0, 0, int(x), int(y))
    _x11.XSync(_display, False)
    return True


def _keycode(name):
    keysym = _x11.XStringToKeysym(name.encode())
    if not keysym:
        return 0
    return _x11.XKeysymToKeycode(_display, keysym)


_MODIFIER_NAMES = {
    "ctrl": "Control_L", "control": "Control_L",
    "shift": "Shift_L", "alt": "Alt_L", "super": "Super_L",
}


# Modifier bit in the pointer mask -> a keysym that releases it.
_MODIFIER_BITS = (
    (1 << 0, "Shift_L"), (1 << 2, "Control_L"), (1 << 3, "Alt_L"),
    (1 << 6, "Super_L"), (1 << 7, "Alt_R"),
)


ALL_MODIFIERS = ("Shift_L", "Shift_R", "Control_L", "Control_R",
                 "Alt_L", "Alt_R", "Super_L", "Super_R", "Meta_L", "Meta_R")


def release_modifiers(force=True):
    """Force every held modifier up.

    A keyboard grab taken while the hotkey's own modifier is still physically
    down swallows that modifier's release: the key-up goes to the grab window
    and X is left believing the modifier is still held, so afterwards every
    keystroke behaves as though alt or super were down. That is the "keys stop
    working until qtile is reloaded" failure. Releasing explicitly on the way
    out costs nothing and cannot make things worse -- a modifier that is
    genuinely still held will be re-reported on its next event.
    """
    if not _load() or _xtst is None:
        return
    if force:
        # Release every modifier unconditionally. Reading the pointer mask
        # first sounds tidier but is unreliable: the mask reflects what the
        # server believes, and the whole failure being fixed here is the
        # server believing wrong. Releasing an already-released key is a
        # no-op, so there is nothing to lose by being blunt.
        for name in ALL_MODIFIERS:
            code = _keycode(name)
            if code:
                _xtst.XTestFakeKeyEvent(_display, code, False, 0)
        _x11.XFlush(_display)
        return

    root = _x11.XDefaultRootWindow(_display)
    root_ret, child_ret = ctypes.c_ulong(), ctypes.c_ulong()
    rx, ry, wx, wy = (ctypes.c_int() for _ in range(4))
    mask = ctypes.c_uint()
    if not _x11.XQueryPointer(
            _display, root, ctypes.byref(root_ret), ctypes.byref(child_ret),
            ctypes.byref(rx), ctypes.byref(ry), ctypes.byref(wx),
            ctypes.byref(wy), ctypes.byref(mask)):
        return
    for bit, name in _MODIFIER_BITS:
        if mask.value & bit:
            code = _keycode(name)
            if code:
                _xtst.XTestFakeKeyEvent(_display, code, False, 0)
    _x11.XFlush(_display)


def send_combo(combo):
    """Send something like 'shift+ctrl+Right' via XTest."""
    if not _load() or _xtst is None:
        return False
    parts = combo.split("+")
    mods, key = parts[:-1], parts[-1]
    codes = [_keycode(_MODIFIER_NAMES.get(m.lower(), m)) for m in mods]
    key_code = _keycode(key)
    if not key_code or any(c == 0 for c in codes):
        return False
    try:
        for code in codes:
            _xtst.XTestFakeKeyEvent(_display, code, True, 0)
        _xtst.XTestFakeKeyEvent(_display, key_code, True, 0)
        _xtst.XTestFakeKeyEvent(_display, key_code, False, 0)
    finally:
        # Always lift them, even if the press half failed part way through.
        for code in reversed(codes):
            _xtst.XTestFakeKeyEvent(_display, code, False, 0)
        _x11.XFlush(_display)
    return True


def click(button, x=None, y=None, modifiers=(), times=1, delay_ms=0,
          hold_ms=0, pre_ms=0):
    """Click at (x, y), optionally with modifiers held, `times` times.

    `hold_ms` separates press from release. A zero-length press with the
    pointer moving immediately afterwards is what a toolkit interprets as the
    start of a drag -- which is exactly what happened to links: they were
    picked up and dragged instead of followed.
    """
    if not _load() or _xtst is None:
        return False
    if x is not None and y is not None:
        warp_pointer(x, y)
        if pre_ms:
            # Let the app process the motion before the button goes down.
            # Warping and pressing in the same instant is read as press-while-
            # moving, which is how a link ends up being dragged.
            time.sleep(pre_ms / 1000)
    codes = [_keycode(_MODIFIER_NAMES.get(m.lower(), m)) for m in modifiers]
    try:
        for code in codes:
            if code:
                _xtst.XTestFakeKeyEvent(_display, code, True, 0)
        # hold_ms separates press from release; delay_ms spaces the clicks.
        for index in range(times):
            _xtst.XTestFakeButtonEvent(
                _display, button, True, 0 if index == 0 else delay_ms)
            _xtst.XTestFakeButtonEvent(_display, button, False, hold_ms)
    finally:
        for code in reversed(codes):
            if code:
                _xtst.XTestFakeKeyEvent(_display, code, False, 0)
        _x11.XSync(_display, False)
    return True


def fallback_run(argv, timeout=2):
    try:
        subprocess.run(argv, timeout=timeout, check=False,
                       capture_output=True)
    except (OSError, subprocess.SubprocessError):
        pass
