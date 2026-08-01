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

# Overall wall-clock budget for one scroll.collect() pass. Atspi.set_timeout()
# bounds each individual D-Bus call, but collect() makes many of them --
# overflow tests, rescue probes, verify()'s scroll-and-watch -- each easily
# fast enough alone to dodge that per-call cap, yet summing to a real stall
# when the AT-SPI service is merely slow rather than hung. Costlier stages
# check this deadline and stop early, biggest-first, rather than let a slow
# desktop make scroll mode feel frozen.
SCROLL_COLLECT_BUDGET_MS = 2500

# Two nested regions this close in area are treated as one scroller: a page's
# document and its content pane differ only by a margin, and offering both
# gives two labels that behave identically.
# Scroll each candidate and watch what moves, instead of guessing from
# geometry. It is the only way to tell a scrollable pane from a tall column
# flowing inside one, and the only way to catch a region whose geometry looks
# independent but which is really a nested child of a scroller already found
# -- but it scrolls the page and puts it back for every candidate, so the
# page visibly jitters and entering the mode costs about a second. On by
# default: a wrong or duplicate region is worse than that jitter.
SCROLL_VERIFY = True

# Probe rejected candidates only when this few regions were found, and only
# this many of them. Virtualised lists render just their visible rows, so no
# amount of measuring reveals that they scroll -- but probing every candidate
# would jitter the page and cost a second.
SCROLL_RESCUE_BELOW = 2
# Live-measured on devdocs.io: _overflows() reported nothing scrollable at
# all (0 regions), yet the actual content pane genuinely scrolls -- confirmed
# by probing it directly. It ranked 3rd by area behind two non-scrolling
# page-level wrapper candidates (the whole toolbar+page area, and the whole
# document including the sidebar), so a budget of 2 spent both slots on
# wrappers and never reached it. Raised so a couple of wrapper levels above
# the real scroller no longer exhausts the budget before reaching it.
SCROLL_RESCUE_MAX = 5

# Probe both directions. Trying only downward made the result depend on where
# the page happened to be scrolled: a pane already at its bottom looked
# unscrollable, so the same page offered different regions from one press to
# the next.
SCROLL_RESCUE_BOTH_WAYS = True
# Smooth scrolling animates a wheel click over a couple of hundred
# milliseconds, so a short settle samples mid-animation and sees nothing move.
SCROLL_PROBE_SETTLE_MS = 90
# One click may not shift a sticky header at all; a few guarantee movement.
SCROLL_PROBE_CLICKS = 2
# How many descendants to look through for something whose position moves.
SCROLL_PROBE_SEARCH = 40

# How closely a frame's geometry must match the focused window to be trusted
# as that window's frame, in pixels of total edge difference.
FRAME_MATCH_TOLERANCE = 80

# A nested region this close in area to the one containing it is the same
# scroller. A page's content column sits inside the viewport pane at roughly
# half its area and scrolls exactly with it, so 0.75 left both on offer.
# Only *nested* regions are compared, so a sidebar beside a content pane --
# the genuinely separate case -- is never collapsed by this.
SCROLL_SAME_RATIO = 0.45
SCROLL_CONTAIN_MARGIN = 24

# How much two regions may overlap and still count as sitting beside each
# other rather than one inside the other.
SCROLL_BESIDE_OVERLAP = 0.25

# Samples per axis when looking for a spot inside the current region that is
# not covered by another scroller inside it (see scroll._clear_point). 7 puts
# a probe roughly every 200px across a 1366px pane -- fine enough to find the
# gap beside a sidebar, coarse enough to stay a handful of comparisons.
SCROLL_TARGET_GRID = 7

# How far past a sidebar's edge to aim when stepping off it. On the edge
# itself is often its drag handle, where a wheel event does nothing.
SCROLL_TARGET_MARGIN = 12

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

# Two search targets sharing this much of the smaller one are one thing: a
# link and the text inside it would otherwise both be numbered.
OVERLAP_SAME = 0.7

# Only outline what you could actually reach. Drawing every match buried the
# labelled ones among dozens of identical boxes.
SEARCH_MAX_OUTLINES = 12

# --- caret mode ------------------------------------------------------------
# Text elements shorter than this are labels and chrome, not prose worth
# putting a cursor in.
# Digits jump between text blocks. The motions are all letters, so digits are
# unambiguous here -- as in search, and for the same reason.
CARET_LABELS = "123456789"

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

# --- caret search (type to find a word, land the caret there) --------------
# Stop collecting word matches past this many. Each is a D-Bus round trip for
# its on-screen extents, paid again on every keystroke as the query narrows.
CARET_SEARCH_MAX_HITS = 200

# Labels shown beside caret-search matches. Same alphabet as search mode, for
# the same reason: letters have to stay available for the query.
CARET_SEARCH_LABELS = SEARCH_LABELS
CARET_SEARCH_MIN_QUERY = SEARCH_MIN_QUERY

# Caret mode's operators (x, d, dd, p) write through EditableText where the
# app publishes one, and press keys at the app where it does not -- Chromium
# publishes no EditableText and does accept a caret position, so the fallback
# is to put the caret where the range starts and press Delete over it.
CARET_PASTE = "ctrl+v"
# Ceiling on that. Deleting a 4000-character line one Delete at a time is a
# visible freeze and almost certainly not what was meant.
CARET_MAX_KEYSTROKES = 400
# How long the application is given to process those keys before the block is
# read back. Reading immediately returns the text as it was before them.
CARET_EDIT_SETTLE_MS = 120

# --- edit (open a field in nvim, on top of the field) ----------------------
# Edit mode asks for the EDITABLE *state* and does not filter by role at all.
# A role list was tried first and kept missing things for no good reason:
# GitHub's "Go to file" is a `combo box`, Chromium's contenteditables are
# `section`s, and every role added to the list only revealed the next one it
# lacked. The state is the thing actually being asked about, so it decides,
# and the list below subtracts rather than adds.
#
# The safety consequence is that PASSWORD_TEXT now comes back from the match
# rule and is refused in Python instead. edit.collect fails closed for this
# reason: an element whose role cannot be read is skipped, not kept.
EDIT_SKIP_ROLES = {"password text", "terminal"}

# Below this a field is a layout artefact rather than something to write in.
EDIT_MIN_SIZE = 12

# Extension on the temp file, which is what gives nvim its filetype -- and
# with it wrapping, spellcheck and whatever else the user's config attaches
# to prose. Most fields being edited at this length are prose.
EDIT_SUFFIX = ".md"

# Used when neither $VISUAL nor $EDITOR is set.
EDIT_EDITOR = "nvim"

# The editor is sized in character cells measured off the running terminal,
# not in pixels, so it comes out as close to the field's own rectangle as a
# usable editor can be. A fixed pixel floor was tried first and is what made
# a one-line omnibox open a 720x400 window sitting over half the page --
# unmistakably a terminal that had appeared, rather than the field becoming
# an editor.
EDIT_MIN_COLS = 32
EDIT_MIN_ROWS = 6

# A field shorter than this many rows is edited in "compact" nvim: no
# statusline, no line numbers, no sign column. One line of chrome around one
# line of text is most of the box, and hiding it is the difference between
# the field looking like nvim and nvim looking like a window over the field.
EDIT_COMPACT_ROWS = 3
# cmdheight=0 is the row this actually wins back: without it nvim reserves a
# row for the command line, and a one-line field cannot spare one.
#
# laststatus=0 is asked for and does not stick, which is why compact still
# budgets two rows below. A statusline plugin sets laststatus itself, after
# this and after VimEnter -- measured against the real config here, lualine
# leaves it at 3 (global statusline) however late this is applied. Rather
# than fight a plugin whose whole job is owning that row, the row is paid
# for. The setting stays because someone without such a plugin does get it.
# nomore and shortmess are not cosmetic here, they are what makes a short
# window usable at all: with three rows nvim pauses on `-- More --` for
# almost any message it prints, including the one it prints on writing the
# file, and every keystroke after that goes to the pager instead of the
# buffer. It reads exactly like the editor having frozen.
EDIT_COMPACT_SET = (
    "set laststatus=0 cmdheight=0 nomore shortmess+=aoOtTIcF "
    "nonumber norelativenumber signcolumn=no nocursorline"
)
# Cold start has to defer these past the user's config; a warm server has
# already passed VimEnter and applies EDIT_COMPACT_SET directly.
EDIT_COMPACT_SETTINGS = f"autocmd VimEnter * ++once silent! {EDIT_COMPACT_SET}"

# Keep a headless nvim running so opening a field does not pay for loading
# the user's config every time -- measured at 285ms here, which was most of
# the delay between pressing the key and being able to type. The editor in
# the field is then a remote UI attaching to it, which costs single-digit
# milliseconds because it loads no config of its own.
#
# The server is replaced after every session rather than reused: a dismissed
# editor leaves a modified buffer behind, and the next `:edit` into it fails
# with E37. Restarting costs nothing that is on the critical path.
EDIT_WARM_SERVER = True
EDIT_WARM_SOCKET = "homerow-nvim.sock"

# Push the field's new contents on every `:w`, not only when the editor
# closes. Only ever done where AT-SPI can write the field directly: the paste
# path would have to steal focus away from the editor mid-edit to type into
# the field, which is worse than waiting for the close.
EDIT_LIVE_WRITE = True

# Rows a compact editor gets. Three of these are spoken for before any text
# is visible -- statusline (which a plugin will not give up, see
# EDIT_COMPACT_SETTINGS), command line, and one line of buffer -- so three
# was the floor and read as a slot rather than an editor. Five leaves room
# to see what you are writing. Raise it for a roomier box; the window is
# still anchored on the field either way.
EDIT_COMPACT_MIN_ROWS = 5
# Editors the compact settings above are valid for; anything else is launched
# untouched rather than handed flags it will not understand.
EDIT_VIM_LIKE = {"nvim", "vim", "gvim", "vi"}

# Normal-mode shortcuts for the one thing this buffer exists to do. <buffer>
# is the important part: they live on the scratch buffer and nothing else, so
# `q` is still macro recording and `<Space>` is still the leader everywhere
# else in the same nvim -- including in another window of it.
#
# Both write rather than quitting bare, and `:q!` remains the way to discard:
# an edit thrown away silently is the worse mistake to make on a keystroke
# this short.
#
# Both also *quit*, because the field is only written when the editor exits
# -- a `:w` that stays open would report success and change nothing on
# screen, which is worse than not offering it.
EDIT_KEYMAPS = [
    "nnoremap <buffer> q :wq<CR>",
    "nnoremap <buffer> <Space>w :wq<CR>",
]

EDIT_BORDER = 2

# WM_WINDOW_ROLE on the editor window. WM_CLASS is `homerow` for every window
# this process opens, so a compositor or tiling rule that wants to treat the
# editor differently from the hint overlay has to match on something, and
# this is it.
EDIT_WM_ROLE = "homerow-edit"

# Paste-path timings. Focus is polled rather than waited out: the keystrokes
# go wherever focus actually is, so guessing an interval was slow when it
# guessed high and lost the edit into another window when it guessed low.
EDIT_FOCUS_POLL_MS = 20
EDIT_FOCUS_TRIES = 30          # ~600ms before giving up and trying anyway
EDIT_KEY_DELAY_MS = 50
# How long the borrowed clipboard is held before the user's own contents go
# back. Long enough that the application has read the paste it was sent.
EDIT_RESTORE_DELAY_MS = 500

EDIT_SELECT_ALL = "ctrl+a"
EDIT_PASTE = "ctrl+v"

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

# A scroll target must also be a real fraction of the window. A browser tab
# strip is horizontally scrollable and about 40px tall, so it passed the size
# floor and turned up as a region you could Tab to for no reason.
MIN_SCROLL_FRACTION = 0.22

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
# Distance from the bottom of the screen to a mode's legend.
LEGEND_MARGIN = 40

# The legend row. It is drawn as badges (the mode, and whatever is live in it)
# followed by key/meaning pairs, so the gaps carry the grouping: a key sits
# tight against its own meaning and further from the next pair, which is what
# lets the row be scanned rather than read.
LEGEND_PAD = 10            # pill edge to content
LEGEND_KEY_GAP = 5         # a key to its own meaning
LEGEND_PAIR_GAP = 15       # one pair to the next
LEGEND_SEGMENT_GAP = 10    # badge to badge, or badges to the keys
LEGEND_BADGE_PAD = 7       # inside an inverted badge
LEGEND_BADGE_INSET = 5     # badge inset from the pill's top and bottom
# Keep the row off the screen edges; past this, pairs are dropped from the
# end rather than letting it run off.
LEGEND_SCREEN_MARGIN = 80

SCROLL_BORDER = 2
SCROLL_RADIUS = 6

# When nothing reports itself as scrollable, scroll the focused window anyway
# rather than refusing. Wheel events do not need accessibility to work, so a
# missing region usually means the app under-reports, not that it cannot
# scroll -- terminals and pcmanfm's file view are both in that category.
SCROLL_FALLBACK_TO_WINDOW = True

# Nested elements of a similar size are treated as one target. Widen the band
# to collapse more aggressively; narrow it if distinct controls get swallowed.
# An element enclosing at least this many other hintable elements is treated
# as a layout container and not offered.
CONTAINER_MIN_CHILDREN = 3

NEST_MIN_RATIO = 0.4
NEST_MAX_RATIO = 2.5

# A candidate sharing an accepted box's top-left corner (within this many
# pixels) and fully inside it is treated as the same target regardless of
# area ratio -- e.g. a combobox's own selected-value label, which the ratio
# check above misses because it is far smaller than the box that contains it.
NEST_CORNER_SLOP = 2

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

# Hard cap on any single AT-SPI D-Bus call, set once via Atspi.set_timeout at
# daemon startup. Without it a frozen or hung app's accessibility service
# blocks a call forever -- and every AT-SPI call in this codebase is
# synchronous on the daemon's one GLib main loop, so that one app hangs every
# mode for every window until it answers. Bounded to this instead: worst case
# is a stall this long, not a dead daemon that needs a manual restart.
ATSPI_CALL_TIMEOUT_MS = 3000

# Appearance
# Gap between a chip and the edge of the target it labels, and between a
# chip and any neighbour it was placed to avoid (see overlay.place_chip).
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

# A chip brighter than this gets dark text; the midpoint decides whether the
# theme's own background or foreground is the readable choice against it.
CHIP_LIGHT_ABOVE = 0.45
LUMINANCE_MIDPOINT = 0.5

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
# A mode closes itself after this long with no keypress. Its keyboard grab is
# exclusive, so while it is open every other binding on the desktop is dead --
# a session left open by accident looks exactly like the keyboard breaking.
IDLE_TIMEOUT_S = 12

# How often, while a mode is open, to check whether the workspace changed under
# it. A mode's whole picture of the screen belongs to the window that was in
# front when it opened, so switching workspace closes it. One in-process X
# property read per tick, and only while something is actually open.
WORKSPACE_POLL_MS = 250

DEBUG_KEYS = False

# The daemon always logs to $XDG_STATE_HOME/homerow/homerow.log, rotating at
# this size. --debug additionally mirrors it to stdout.
LOG_MAX_BYTES = 512 * 1024
