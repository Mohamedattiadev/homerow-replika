"""User-tunable settings. Edit freely; everything else reads from here."""

# Characters used to build hint labels. Home row first, then the easy reaches.
# Keep these lowercase and unique.
HINT_ALPHABET = "asdfghjkl"

# Roles worth hinting. Names map to Atspi.Role members; unknown names are
# skipped so this list stays portable across atspi versions.
ACTIONABLE_ROLES = [
    "PUSH_BUTTON",
    "LINK",
    "ENTRY",
    "CHECK_BOX",
    "RADIO_BUTTON",
    "COMBO_BOX",
    "MENU_ITEM",
    "CHECK_MENU_ITEM",
    "RADIO_MENU_ITEM",
    "TOGGLE_BUTTON",
    "PAGE_TAB",
    "SLIDER",
    "SPIN_BUTTON",
    "PASSWORD_TEXT",
]

# Roles that are sometimes a real target and sometimes pure layout. Web pages
# are full of nameless list items used for structure, which produced hints
# floating in empty space. Requiring FOCUSABLE separates the two, and does it
# server-side so it costs nothing -- checking names instead would mean a D-Bus
# round trip per element, which is exactly what the lazy fields avoid.
CONTAINER_ROLES = [
    "LIST_ITEM",
    "TABLE_CELL",
    "TREE_ITEM",
]

HINT_ROLES = ACTIONABLE_ROLES + CONTAINER_ROLES

# Elements smaller/larger than these are almost always layout artefacts
# rather than real targets.
MIN_SIZE = 4
MAX_FRACTION_OF_SCREEN = 0.9

# --- scroll mode -----------------------------------------------------------
# Regions worth offering as scroll targets.
SCROLL_ROLES = [
    "SCROLL_PANE",
    "DOCUMENT_WEB",
    "DOCUMENT_FRAME",
    "DOCUMENT_TEXT",
    "LIST",
    "TREE",
    "TREE_TABLE",
    "TABLE",
    "VIEWPORT",
    "TEXT",
]

# A scroll target has to be big enough to be worth aiming at.
MIN_SCROLL_SIZE = 80

# Wheel clicks per key. Scrolling is done with synthetic wheel events rather
# than Home/End keypresses: wheel events cannot land as text in a focused
# field, which keypresses can.
SCROLL_LINE_CLICKS = 2
SCROLL_PAGE_CLICKS = 8
SCROLL_EDGE_CLICKS = 40

# Nested elements of a similar size are treated as one target. Widen the band
# to collapse more aggressively; narrow it if distinct controls get swallowed.
NEST_MIN_RATIO = 0.4
NEST_MAX_RATIO = 2.5

# Also hint the other visible windows, so one keypress covers both "click that
# button" and "switch to that terminal".
HINT_WINDOWS = True
MIN_WINDOW_SIZE = 60

# Stop collecting past this many elements. Each one costs a D-Bus round trip
# for its extents, so this is the main latency knob.
MAX_ELEMENTS = 400

# Time budget for the fallback tree walk, used only for apps that expose no
# Collection interface (older ATK apps such as pavucontrol). Walking costs one
# D-Bus round trip per node, so this caps the damage on a big tree.
WALK_BUDGET_MS = 400

# Appearance
# Where a chip sits relative to its target.
#   "margin" - just outside the left edge, falling back inside at the screen
#              edge. Keeps the target's own first characters readable.
#   "inside" - overlapping the target's top-left corner.
HINT_PLACEMENT = "margin"
HINT_GAP = 2

FONT_FAMILY = "monospace"
FONT_SIZE = 13
PAD_X, PAD_Y = 5, 2
RADIUS = 3

# Follow the desktop theme (theme-apply / pywal). Re-read on every hint, so a
# theme switch shows up without restarting the daemon. Set False to pin the
# palette below instead.
FOLLOW_THEME = True

# Which theme slot each chip takes its color from. Valid names are the keys in
# ~/.cache/qtile/current_palette.json: red, green, yellow, blue, purple, cyan.
#
# "green" is the dominant/main accent, not literally green -- colors.py maps
# it to wal's color10, which its own comment documents as "main/dominant", the
# wallpaper's most-used hue. That is what the qtile bar highlights with, so
# hints match the rest of the desktop. Change these to taste.
CHIP_SLOT = "green"
CHIP_SLOT_MATCHED = "cyan"
# Windows you can switch to, to tell them apart from clickable elements.
CHIP_SLOT_WINDOW = "purple"

DIM_ALPHA = 0.18
CHIP_ALPHA = 0.96

# There is deliberately no palette of literal colors here. Every color the
# overlay draws is derived from the active theme by homerow/theme.py, including
# the last-resort fallback -- see theme.FALLBACK, which is hex named colors run
# through the same slot and contrast logic rather than a second hardcoded set
# that could drift out of step.

DIM_BACKGROUND = True

# X11 class of the overlay window. picom matches on this to exempt the overlay
# from compositor animations -- without that, hints slide in from the top
# instead of appearing on their targets.
WM_CLASS = "homerow"

# "atspi" fires the element's own accessible action (no pointer movement, but
# some apps implement it poorly). "pointer" warps the real cursor, clicks, and
# warps back -- closer to what Homerow does on macOS.
CLICK_METHOD = "pointer"

# Log every key the overlay receives. Useful when hints appear but typing them
# does nothing, which usually means the keyboard grab is not exclusive.
DEBUG_KEYS = False
