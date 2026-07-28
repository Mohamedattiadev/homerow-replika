"""Overlay colors, taken from the active desktop theme.

Sources, in the same precedence the qtile popups use
(~/.config/qtile/popups/_wal_colors.py):

    1. ~/.cache/qtile/current_palette.json  -- written by theme-apply on every
       preset *and* wal apply, so it matches the active mode
    2. ~/.cache/wal/colors.json             -- pywal's own output
    3. the built-in fallback below

Read on every hint rather than cached at import, so `theme-apply` takes effect
without restarting the daemon. That is the convention the popups already
follow, and the files are under 1KB.
"""

import json
import os

from . import config

FALLBACK = {
    "bg": "#1c1f24",
    "fg": "#bbc2cf",
    "red": "#ff6c6b",
    "green": "#98be65",
    "yellow": "#fac800",
    "blue": "#51afef",
    "purple": "#c678dd",
    "cyan": "#46d9ff",
}

# Which wal slot backs each named accent. colors.py documents color10 as
# "main/dominant" and color9 as "urgent"; the bright variants (9-14) are used
# throughout so accents stay visible against dim wallpapers.
_WAL_SLOTS = {
    "red": "color9",
    "green": "color10",
    "yellow": "color11",
    "blue": "color12",
    "purple": "color13",
    "cyan": "color14",
}


def _hex_to_rgb(value):
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _luminance(rgb):
    """Relative luminance, for deciding whether text on it should be dark."""
    channels = [
        c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
        for c in rgb
    ]
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _mix(a, b, t):
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))


def _from_preset():
    path = os.path.expanduser("~/.cache/qtile/current_palette.json")
    with open(path) as handle:
        palette = json.load(handle)
    named = {"bg": palette["bg"], "fg": palette["fg"]}
    for key in _WAL_SLOTS:
        named[key] = palette[key]
    return named


def _from_wal():
    path = os.path.expanduser("~/.cache/wal/colors.json")
    with open(path) as handle:
        wal = json.load(handle)
    named = {
        "bg": wal["special"]["background"],
        "fg": wal["special"]["foreground"],
    }
    for key, slot in _WAL_SLOTS.items():
        named[key] = wal["colors"][slot]
    return named


def _named():
    for source in (_from_preset, _from_wal):
        try:
            return source()
        except Exception:
            continue
    return dict(FALLBACK)


def palette():
    """The colors the overlay draws with, as rgba tuples.

    Both the themed and the fallback case go through this one path, so a
    missing theme file changes which hex values come in, never how they are
    turned into a palette.
    """
    named = _named() if config.FOLLOW_THEME else dict(FALLBACK)
    try:
        return _build(named)
    except (KeyError, ValueError):
        return _build(dict(FALLBACK))


def _build(named):
    background = _hex_to_rgb(named["bg"])
    foreground = _hex_to_rgb(named["fg"])
    chip = _hex_to_rgb(named[config.CHIP_SLOT])
    matched = _hex_to_rgb(named[config.CHIP_SLOT_MATCHED])
    window = _hex_to_rgb(named[config.CHIP_SLOT_WINDOW])

    # Text sits on the chip, not on the wallpaper, so pick whichever of the
    # theme's own extremes contrasts with the chip. This is what keeps light
    # themes readable instead of assuming a dark desktop.
    def text_on(chip_color):
        if _luminance(chip_color) > 0.45:
            return background if _luminance(background) < 0.5 else (0, 0, 0)
        return foreground if _luminance(foreground) > 0.5 else (1, 1, 1)

    ink = text_on(chip)
    return {
        "chip": chip + (config.CHIP_ALPHA,),
        "chip_matched": matched + (config.CHIP_ALPHA,),
        "chip_window": window + (config.CHIP_ALPHA,),
        "ink": ink + (1.0,),
        # Already-typed characters recede toward the chip they sit on.
        "ink_typed": _mix(ink, chip, 0.55) + (1.0,),
        "ink_window": text_on(window) + (1.0,),
        "dim": background + (config.DIM_ALPHA,),
    }
