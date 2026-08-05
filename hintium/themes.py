"""Named colour palettes, for desktops that have no theme to follow.

hintium's colours normally come from the desktop itself -- theme-apply's
`current_palette.json`, else pywal's `colors.json` -- and are re-read on every
press, so changing the wallpaper changes the hints without restarting
anything. That is the good path and it stays the default.

It is also a path a stranger cloning this repo does not have. Without either
file the overlay fell back to one hardcoded palette with no way to choose
another, so "make the hints match my terminal" meant editing Python. These are
that choice, by name:

    theme:
      preset: nord

Each palette gives the eight slots the overlay draws from. `bg` and `fg` are
what ink is picked from (see theme.text_on), and the six accents are what
CHIP_SLOT and friends name. Values are the projects' own published colours.

Setting a preset turns off following the desktop, because asking for a named
palette and getting the wallpaper's is not what anyone means by it. Individual
colours can still be overridden on top -- see config.THEME_COLORS -- so a
preset is a starting point rather than a wall.
"""

PRESETS = {
    # The built-in fallback, kept here by name so it can be asked for
    # explicitly rather than only reached by absence.
    "doom-one": {
        "bg": "#1c1f24", "fg": "#bbc2cf",
        "red": "#ff6c6b", "green": "#98be65", "yellow": "#fac800",
        "blue": "#51afef", "purple": "#c678dd", "cyan": "#46d9ff",
    },
    "gruvbox-dark": {
        "bg": "#282828", "fg": "#ebdbb2",
        "red": "#fb4934", "green": "#b8bb26", "yellow": "#fabd2f",
        "blue": "#83a598", "purple": "#d3869b", "cyan": "#8ec07c",
    },
    "gruvbox-light": {
        "bg": "#fbf1c7", "fg": "#3c3836",
        "red": "#9d0006", "green": "#79740e", "yellow": "#b57614",
        "blue": "#076678", "purple": "#8f3f71", "cyan": "#427b58",
    },
    "nord": {
        "bg": "#2e3440", "fg": "#d8dee9",
        "red": "#bf616a", "green": "#a3be8c", "yellow": "#ebcb8b",
        "blue": "#81a1c1", "purple": "#b48ead", "cyan": "#88c0d0",
    },
    "dracula": {
        "bg": "#282a36", "fg": "#f8f8f2",
        "red": "#ff5555", "green": "#50fa7b", "yellow": "#f1fa8c",
        "blue": "#bd93f9", "purple": "#ff79c6", "cyan": "#8be9fd",
    },
    "catppuccin-mocha": {
        "bg": "#1e1e2e", "fg": "#cdd6f4",
        "red": "#f38ba8", "green": "#a6e3a1", "yellow": "#f9e2af",
        "blue": "#89b4fa", "purple": "#cba6f7", "cyan": "#94e2d5",
    },
    "catppuccin-latte": {
        "bg": "#eff1f5", "fg": "#4c4f69",
        "red": "#d20f39", "green": "#40a02b", "yellow": "#df8e1d",
        "blue": "#1e66f5", "purple": "#8839ef", "cyan": "#179299",
    },
    "tokyo-night": {
        "bg": "#1a1b26", "fg": "#c0caf5",
        "red": "#f7768e", "green": "#9ece6a", "yellow": "#e0af68",
        "blue": "#7aa2f7", "purple": "#bb9af7", "cyan": "#7dcfff",
    },
    "solarized-dark": {
        "bg": "#002b36", "fg": "#93a1a1",
        "red": "#dc322f", "green": "#859900", "yellow": "#b58900",
        "blue": "#268bd2", "purple": "#d33682", "cyan": "#2aa198",
    },
    "solarized-light": {
        "bg": "#fdf6e3", "fg": "#586e75",
        "red": "#dc322f", "green": "#859900", "yellow": "#b58900",
        "blue": "#268bd2", "purple": "#d33682", "cyan": "#2aa198",
    },
    "everforest-dark": {
        "bg": "#2d353b", "fg": "#d3c6aa",
        "red": "#e67e80", "green": "#a7c080", "yellow": "#dbbc7f",
        "blue": "#7fbbb3", "purple": "#d699b6", "cyan": "#83c092",
    },
    "rose-pine": {
        "bg": "#191724", "fg": "#e0def4",
        "red": "#eb6f92", "green": "#31748f", "yellow": "#f6c177",
        "blue": "#9ccfd8", "purple": "#c4a7e7", "cyan": "#ebbcba",
    },
}

SLOTS = ("bg", "fg", "red", "green", "yellow", "blue", "purple", "cyan")


def names():
    """Every preset name, for --list-themes and for error messages."""
    return sorted(PRESETS)


def get(name):
    """A preset by name, or None. Case and separators are forgiving.

    `Tokyo Night`, `tokyo_night` and `tokyo-night` are the same request, and
    refusing two of them would be a spelling test nobody asked to sit.
    """
    if not name:
        return None
    wanted = str(name).strip().lower().replace(" ", "-").replace("_", "-")
    palette = PRESETS.get(wanted)
    return dict(palette) if palette else None
