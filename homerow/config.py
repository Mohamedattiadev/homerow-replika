"""User-tunable settings. Edit freely; everything else reads from here."""

# Characters used to build hint labels. Home row first, then the easy reaches.
# Keep these lowercase and unique.
HINT_ALPHABET = "asdfghjkl"

# Type text to narrow the hints instead of reading every label. Any character
# outside HINT_ALPHABET filters by element name; label keys still select. The
# labels are recomputed over the survivors, so filtering to one thing leaves a
# single-character label.
HINT_FILTER = True

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

# Clicking one of these focuses somewhere you are about to type, so the qtile
# chord is released -- otherwise h/s/f would open modes instead of typing.
# Role *names* as AT-SPI reports them, not Atspi.Role members.
TEXT_ENTRY_ROLES = {
    "entry", "password text", "text", "document text", "spin button",
    "terminal", "paragraph",
}

# Elements smaller/larger than these are almost always layout artefacts
# rather than real targets.
MIN_SIZE = 4
MAX_FRACTION_OF_SCREEN = 0.9

# --- scroll mode -----------------------------------------------------------
# Regions worth offering as scroll targets.
# Containers that might scroll. This is deliberately broad: on a web page the
# scrollable sidebar is a SECTION and the content pane is a PANEL, neither of
# which is a "scrolling" role, so a tight whitelist misses exactly the regions
# people want. Roles only nominate candidates -- the overflow test below is
# what actually decides.
# Queried first. These are few even on a busy page, and on most apps they are
# the answer, so the expensive pass below never runs.
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
]

# Only if the above found nothing scrollable. A web page's scrollable sidebar
# is a SECTION and its content pane a PANEL, so these are needed for
# correctness -- but there are hundreds of them, and every candidate costs a
# round trip for its extents. Asking for them unconditionally spent ~330ms
# fetching 333 rectangles to keep one.
SCROLL_ROLES_FALLBACK = [
    "SECTION",
    "PANEL",
    "FILLER",
    "GROUPING",
    "INTERNAL_FRAME",
    "LAYERED_PANE",
    "SPLIT_PANE",
]

# Only the largest candidates are overflow-tested; each test costs a handful of
# D-Bus round trips and small containers are never what you meant to scroll.
SCROLL_MAX_CANDIDATES = 10

# --- search mode -----------------------------------------------------------
# Names are read this many per idle tick, so the prompt stays responsive while
# indexing a page with hundreds of elements.
SEARCH_INDEX_CHUNK = 12

# Visible text is searched as well as the accessible name, since many web
# controls have no name but do have text. Capped because it is per element.
SEARCH_TEXT_CHARS = 60

# Labels shown beside search matches. Digits, so that every letter stays
# available for the query -- that is what lets filtering and picking share one
# prompt instead of needing a second phase.
SEARCH_LABELS = "123456789"

# Search reaches down to very short text. Sidebar entries like "Vite" are
# LABEL elements, not links, so without this they are in neither the hintable
# set nor the caret set and simply cannot be found.
SEARCH_MIN_CHARS = 2

# Nothing is outlined until the query is this long. One letter matches most of
# the screen, and outlining seventy things is noise, not help.
SEARCH_MIN_QUERY = 2

# Only outline what you could actually reach. Drawing every match buried the
# labelled ones among dozens of identical boxes.
SEARCH_MAX_OUTLINES = 12

# --- caret mode ------------------------------------------------------------
# Text elements shorter than this are labels and chrome, not prose worth
# putting a cursor in.
CARET_MIN_CHARS = 12
# Roles that carry text. The Text interface is not a role and cannot be
# queried for directly, so these nominate candidates and the character count
# decides -- walking the tree for it instead cost ~2.5s.
# Apps with their own vim caret mode. Handing off beats reimplementing: our
# AT-SPI caret needs a real Text interface, and Qt WebEngine publishes none at
# all, so in qutebrowser it could only ever miss things. Its own caret mode is
# already vim -- h/j/k/l, w/b/e, v to select, y to yank -- and instant.
# Keyed on the AT-SPI application name, value is the key that enters the mode.
CARET_NATIVE = {
    "qutebrowser": "v",     # its own caret mode, already vim
    "firefox": "F7",        # caret browsing; prompts once, then persists
}

CARET_ROLES = [
    "TEXT", "PARAGRAPH", "HEADING", "DOCUMENT_TEXT", "ENTRY", "TERMINAL",
    "STATIC",
]

# Searched as well as the above, and used for caret only when the above found
# nothing. Labels and list items are numerous; asking for them every time is
# what made caret slow.
CARET_ROLES_FALLBACK = [
    "LABEL", "LIST_ITEM", "TABLE_CELL", "SECTION", "DOCUMENT_WEB",
    "DOCUMENT_FRAME",
]

# A container only counts as scrollable if its content actually overflows it.
# Role alone is a bad signal -- a short list is still a LIST, and offering it
# put scroll hints on things that could not scroll. Chromium exposes no
# scrollbars at all through AT-SPI, so a scrollbar check is not an option;
# comparing content extent against the visible box is.
SCROLL_OVERFLOW_RATIO = 1.08
# Children sampled per candidate when measuring content extent. Each is a
# D-Bus round trip, so this is a latency/accuracy tradeoff, not a limit on
# how much can scroll.
SCROLL_PROBE_CHILDREN = 4

# A scroll target has to be big enough to be worth aiming at.
MIN_SCROLL_SIZE = 80

# Wheel clicks per key. Scrolling is done with synthetic wheel events rather
# than Home/End keypresses: wheel events cannot land as text in a focused
# field, which keypresses can.
SCROLL_LINE_CLICKS = 1
SCROLL_PAGE_CLICKS = 6
# gg/G mean "all the way", so this has to overshoot the longest realistic
# content rather than be a tuned guess. 50 looked fine on short pages and left
# G stranded mid-document on long ones. Extra clicks past the end are free.
SCROLL_EDGE_CLICKS = 400

# Repeat delay in ms passed to xdotool for multi-click scrolls. Too low and
# smooth-scrolling apps coalesce the events into one small jump.
SCROLL_CLICK_DELAY = 6

# Upper bound on a count prefix, so a mistyped 999j cannot hang the session.
SCROLL_MAX_COUNT = 50

# Scroll overlay styling.
SCROLL_BORDER = 2
SCROLL_RADIUS = 6

# When nothing reports itself as scrollable, scroll the focused window anyway
# rather than refusing. Wheel events do not need accessibility to work, so a
# missing region usually means the app under-reports, not that it cannot
# scroll -- terminals and pcmanfm's file view are both in that category.
SCROLL_FALLBACK_TO_WINDOW = True

# Nested elements of a similar size are treated as one target. Widen the band
# to collapse more aggressively; narrow it if distinct controls get swallowed.
NEST_MIN_RATIO = 0.4
NEST_MAX_RATIO = 2.5

# Also hint the other visible windows, so one keypress covers both "click that
# button" and "switch to that terminal".
HINT_WINDOWS = True
MIN_WINDOW_SIZE = 60

# Fraction of a window that must actually be on screen for it to count as the
# active window. Hidden scratchpads are parked just off the top edge and still
# hold _NET_ACTIVE_WINDOW when nothing is focused.
MIN_ONSCREEN = 0.4

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

# "action" tries the element's own accessible action first and falls back to
# the pointer; "pointer_only" always warps the real cursor. Action first is the
# default because a pointer click on a link is press-move-release, which is
# indistinguishable from a drag and was picking links up instead of following
# them.
CLICK_METHOD = "action"

# How long the button stays down, and how long to wait before putting the
# pointer back. A zero-length press followed by immediate pointer motion is
# read as the start of a drag -- links were being picked up and dragged
# instead of followed.
CLICK_HOLD_MS = 14
CLICK_PREPRESS_MS = 30
CLICK_SETTLE_MS = 120

# Log every key the overlay receives. Useful when hints appear but typing them
# does nothing, which usually means the keyboard grab is not exclusive.
DEBUG_KEYS = False

# The daemon always logs to $XDG_STATE_HOME/homerow/homerow.log, rotating at
# this size. --debug additionally mirrors it to stdout.
LOG_MAX_BYTES = 512 * 1024
