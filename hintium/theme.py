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

from . import config, themes

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
    """The eight named colours, from the first source that has them.

    Order: a preset asked for by name, then the desktop's own palette, then
    pywal's, then the built-in fallback -- and whatever THEME_COLORS names is
    laid over the top of the winner. That last part is what makes "I like my
    theme but the window chips should be orange" a two-line config file
    instead of a fork.
    """
    named = None
    chosen = themes.get(config.THEME_PRESET)
    if chosen is not None:
        named = chosen
    elif config.FOLLOW_THEME:
        for source in (_from_preset, _from_wal):
            try:
                named = source()
                break
            except Exception:
                continue
    if named is None:
        named = dict(FALLBACK)
    return _overlaid(named)


def _overlaid(named):
    """Apply config.THEME_COLORS over `named`, ignoring anything unusable.

    A colour that will not parse is dropped and the themed one kept, rather
    than taken down the palette with it: a typo in one hex value should cost
    that one colour, not every hint on the screen.
    """
    for slot, value in (config.THEME_COLORS or {}).items():
        slot = str(slot).lower()
        if slot not in themes.SLOTS:
            continue
        try:
            _hex_to_rgb(str(value))
        except (ValueError, IndexError, AttributeError):
            continue
        named[slot] = str(value)
    return named


def palette():
    """The colors the overlay draws with, as rgba tuples.

    Both the themed and the fallback case go through this one path, so a
    missing theme file changes which hex values come in, never how they are
    turned into a palette.
    """
    named = _named()
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
        """The most readable ink for this chip, preferring the theme's own.

        This used to compare luminances against a threshold, which is right
        most of the time and quietly wrong when an accent lands near the
        middle: measured on this desktop's own theme, the window chip's
        purple was given an ink at 2.24:1, well below readable, and window
        labels have been hard to read ever since. Contrast is the thing
        actually being asked about, so it is now the thing measured. Black
        and white stay the last resort rather than the first, so a theme's
        own colours are used wherever they are good enough.
        """
        themed = max((foreground, background),
                     key=lambda color: _contrast(color, chip_color))
        if _contrast(themed, chip_color) >= config.INK_MIN_CONTRAST:
            return themed
        return max(((0, 0, 0), (1, 1, 1)),
                   key=lambda color: _contrast(color, chip_color))

    ink = text_on(chip)
    palette = {
        "chip": chip + (config.CHIP_ALPHA,),
        "chip_matched": matched + (config.CHIP_ALPHA,),
        "chip_window": window + (config.CHIP_ALPHA,),
        "ink": ink + (1.0,),
        # Already-typed characters recede toward the chip they sit on -- as
        # far as they can while staying visible. At a flat mix this measured
        # 2.49:1 against the chip, which is not "receded", it is "gone".
        "ink_typed": _recede(ink, chip, config.INK_TYPED_MIX,
                             config.INK_TYPED_MIN_CONTRAST) + (1.0,),
        "ink_window": text_on(window) + (1.0,),
        "ink_matched": text_on(matched) + (1.0,),
        "dim": background + (config.DIM_ALPHA,),
    }
    # A receded ink per chip, because a legend is drawn on whichever chip its
    # mode uses and the text has to be readable on that one. Measured on this
    # desktop's own theme, the legend's meanings were being drawn in an ink
    # mixed toward the *default* chip whatever chip was actually behind them,
    # at 2.5:1 against it -- which is the "some of the text is not visible"
    # report, and no amount of picking a nicer grey would have fixed it.
    for slot, base in (("", chip), ("_matched", matched), ("_window", window)):
        palette[f"ink_dim{slot}"] = _recede(
            palette[f"ink{slot}"][:3], base,
            config.LEGEND_MEANING_MIX,
            config.LEGEND_MEANING_MIN_CONTRAST) + (1.0,)
    return palette


def _recede(ink, chip, mix, floor):
    """Fade `ink` toward `chip` as far as `mix`, but never past readable.

    The point of the fade is hierarchy -- a key matters more than the word
    explaining it -- and a fixed fraction delivers that on a chip whose
    luminance is far from the ink's while destroying it on one that is not.
    So the fraction is a ceiling rather than a promise: it backs off until the
    contrast is at least `floor`, and a chip that cannot afford any fade
    simply does not get one. Themes here come from the user's wallpaper, so
    the awkward chip is not hypothetical.
    """
    while mix > 0:
        faded = _mix(ink, chip, mix)
        if _contrast(faded, chip) >= floor:
            return faded
        mix -= 0.02
    return tuple(ink)


def _contrast(a, b):
    """WCAG contrast ratio between two rgb colours, 1.0 to 21.0."""
    def channel(value):
        return (value / 12.92 if value <= 0.03928
                else ((value + 0.055) / 1.055) ** 2.4)

    def relative(color):
        r, g, b = (channel(v) for v in color[:3])
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    first, second = relative(a), relative(b)
    high, low = max(first, second), min(first, second)
    return (high + 0.05) / (low + 0.05)
