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

# ...but not before the mode opens. The pass above warps the pointer onto
# every candidate in turn, scrolls it and scrolls it back, and only puts the
# cursor back where the user left it once the whole sweep is done -- so
# entering scroll mode was a second of the cursor flying about and the page
# jumping with nothing yet drawn. Measured over this desktop's log, 151 real
# sessions: 316ms median, 726ms p90, 2318ms worst, all of it spent before
# anything appeared.
#
# What it buys is a Tab that never lands on a region that cannot scroll, or
# that scrolls the same thing as its neighbour -- which is worth nothing to
# somebody scrolling the region they are already pointing at, and they were
# paying for it every time. So it runs the first time Tab asks for another
# region instead, beside the rescue pass that is deferred for the same
# reason. Set this True to go back to paying on entry.
SCROLL_VERIFY_ON_ENTRY = False

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
# How long after the outline appears before the background rescue probe runs
# (see ScrollSession._promote_deferred). Long enough that the window is drawn
# and a user who knew what they wanted has already pressed j; short enough
# that a snap to a better region still feels like part of opening.
SCROLL_PROMOTE_DELAY_MS = 120
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
# ...but a preference, not a requirement. When nothing on screen clears the
# minimum, caret.collect drops to this instead of coming back empty: with no
# prose to prefer, a short field is what was meant, and refusing it made the
# daemon blame the app ("publishes no text") for a threshold of ours -- on
# exactly the one-line inputs where a cursor is most obviously wanted.
CARET_MIN_CHARS_FLOOR = 1
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
    "nonumber norelativenumber signcolumn=no nocursorline nospell"
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

# Rows the editor keeps for itself: a statusline and a command line. Assumed
# rather than measured, and deliberately so. An earlier version asked the
# editor -- which meant announcing homerow to the user's config, asking them
# to add a condition to their statusline plugin, and three separate probes
# that each measured the wrong thing (a phantom UI's geometry, a dashboard
# where the statusline hides itself, our own write). None of that belongs in
# a tool whose whole promise is that it works with your editor as it is. Two
# rows is the worst case, so the text is visible whatever the config does;
# where the real cost is lower the box is one row roomier than it had to be,
# which is a better failure than a box you cannot see your line in.
EDIT_CHROME_ROWS = 2
# How long after the editor is up before asking it what it actually kept, for
# the benefit of the next field (see edit.learn_chrome). Long enough that a
# statusline plugin reacting to the new UI has finished doing so.
EDIT_LEARN_DELAY_MS = 600

EDIT_COMPACT_TEXT_ROWS = 1
EDIT_COMPACT_MAX_ROWS = 12

# Grow the box as the buffer grows, up to EDIT_COMPACT_MAX_ROWS.
#
# This was left out at first, on the reasoning that resizing a window under
# somebody who has started reading is worse than letting the editor scroll --
# which is true of the *chrome* measurement, where the window is already the
# right size and the change buys nothing. It is not true here. A field opened
# at its minimum has a few rows; press Enter past the last one and the line
# you were writing scrolls out of sight, and getting it back means k. No
# other text field on the desktop behaves that way, and "it is really an
# editor" is not an answer to that -- the box is standing in for a text
# field, so it has to grow like one.
#
# It only ever grows. Shrinking it when a line is deleted is the resize that
# has nothing to offer and moves the text under the cursor, and that is the
# case the original reasoning was actually about.
EDIT_GROW = True

# nvim writes the rows it needs here whenever the buffer changes, and homerow
# watches the file. The alternative is asking over the socket on a timer,
# which is a process spawn per ask (~65ms) for an answer that is usually the
# same as last time. This is homerow's own buffer-local autocmd, applied the
# same way its <buffer> mappings are -- nothing is added to the user's
# config, and nothing is asked of it.
EDIT_ROWS_SUFFIX = ".rows"
# nvim_win_text_height counts *screen* lines, so a line that wraps grows the
# box too. It landed in nvim 0.10; line('$') is the fallback and misses only
# wrapping.
EDIT_ROWS_EXPR = ("exists('*nvim_win_text_height') ? "
                  "nvim_win_text_height(0, {}).all : line('$')")

# How the write-back is confirmed. The paste path is asynchronous -- it
# focuses the window and presses keys at it -- so the characters have not
# arrived when write() returns and the field has to be read again to know.
#
# This used to be one read after a fixed 700ms, which had to be long enough
# for the slowest application and so charged every write the worst case.
# Measured, a paste lands 12-55ms after the keystroke. So it is asked
# repeatedly instead, and the answer arrives when it is true; the timeout is
# only how long a write that never landed goes unreported.
EDIT_LANDED_POLL_MS = 25
EDIT_LANDED_TIMEOUT_MS = 1500

# How long to wait, on the way out of a session, for the replacement warm
# server to be ready. Measured: it takes over a second to load the config, and
# a second edit inside that window found nothing and started from cold.
EDIT_WARM_READY_MS = 4000
EDIT_WARM_POLL_MS = 100
# How long to wait for the outgoing warm server to actually die. It has
# to be gone before its replacement starts: nvim unlinks its listen
# socket as it exits, and that is the path the new one is about to use.
EDIT_WARM_STOP_MS = 2000

# How far up the tree to look when asking which document a field belongs
# to. Bounded so a tree with a parent cycle cannot hang the daemon.
EDIT_ANCESTOR_LIMIT = 24

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
    # Esc leaves, the way it leaves every other mode here. In normal mode it
    # otherwise does nothing, and pressing it twice from insert -- once to
    # leave insert, once to leave -- is the shape the rest of homerow already
    # has. It writes rather than discards, like q: this is a one-key exit and
    # a silently thrown-away edit is the worse mistake to make on one of
    # those. :q! is still there for actually discarding.
    "nnoremap <buffer> <Esc> :wq<CR>",
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
# Nothing between select-all and paste: they are sent back to back and the
# server is asked whether it has caught up (x11.sync()), which costs 0.32ms
# against the 50ms this used to sleep for.
#
# How long the borrowed clipboard is held before the user's own contents go
# back. Deliberately still a fixed wait: it is off the path the user is
# watching -- the text has already landed -- and the thing it waits for, an
# application getting round to reading the selection it was sent, is the one
# thing here that cannot be observed from outside.
EDIT_RESTORE_DELAY_MS = 500

# Nothing waits on the clipboard's owner at all -- see edit._borrow_clipboard.
# Reading it means asking another application and waiting for its answer, and
# the blocking form of that call runs a nested main loop: measured at 31
# seconds against an unresponsive owner, with the daemon's every other mode
# frozen behind it. The reply is only wanted by the hand-back, which happens
# EDIT_RESTORE_DELAY_MS later, so the write-back never waits for it.

EDIT_SELECT_ALL = "ctrl+a"
EDIT_PASTE = "ctrl+v"
# What empties a field whose contents are selected. Emptying cannot go
# through the clipboard -- pasting nothing is a no-op, and the field keeps
# every character it had -- so this is the one write that is a keystroke
# rather than a paste. Reported live: deleting the whole buffer and saving
# left the field exactly as it was.
EDIT_CLEAR = "Delete"

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

# Which key does what, while scroll mode is open. Key names are X keysym names
# -- what `xev` prints, and what qtile binds with: single letters are
# themselves, and the rest are names like Down, Page_Up, Home, End. Actions
# are the eight below; anything else is reported and ignored.
#
# Rebinding is the point: these are vim's keys because that is what this is
# modelled on, but somebody who reaches for `e`/`y` or for the arrow keys
# alone should not have to edit Python to get them. Replacing the map replaces
# it wholesale -- what you write is what the mode answers to -- so copy the
# defaults and change the lines you care about.
#
# `gg`, counts (3j), Tab and Escape are not here. They are grammar rather than
# bindings: a doubled key, a number prefix, "next region" and "leave" are the
# same in every mode, and making them rebindable per mode would let them drift
# apart from each other for no benefit.
SCROLL_KEYS = {
    "j": "down", "k": "up",
    "Down": "down", "Up": "up",
    "h": "left", "l": "right",
    "d": "page down", "u": "page up",
    "Page_Down": "page down", "Page_Up": "page up",
    "G": "bottom",
    # Home/End are not letters, so a stuck Caps Lock cannot touch them -- see
    # the note on normalize_key. They reach the same edges `gg`/`G` do.
    "Home": "top", "End": "bottom",
}

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
LEGEND_PAD = 8             # pill edge to content
LEGEND_KEY_GAP = 4         # a key to its own meaning
LEGEND_PAIR_GAP = 12       # one pair to the next
LEGEND_SEGMENT_GAP = 8     # badge to badge, or badges to the keys
LEGEND_BADGE_PAD = 6       # inside an inverted badge
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

# A named palette from homerow/themes.py -- "nord", "gruvbox-dark",
# "catppuccin-mocha", and so on; `homerow --list-themes` prints them all.
# Empty means "follow the desktop", which is the default and what a machine
# running theme-apply or pywal wants.
#
# This exists because a desktop with neither of those files had exactly one
# palette and no way to pick another: "make the hints match my terminal" meant
# editing Python. Naming a preset turns following the desktop off, since
# asking for nord and getting the wallpaper's colours is not what anyone means
# by it.
THEME_PRESET = ""

# Individual colours, laid over whatever the above resolved to. Any of the
# eight slots -- bg, fg, red, green, yellow, blue, purple, cyan -- as hex:
#
#   theme:
#     colors: {purple: "#ff8800"}
#
# So "I like my theme, but the window chips should be orange" is two lines
# rather than a fork. A value that will not parse is dropped and the themed
# one kept: a typo in one colour should cost that colour, not every hint on
# the screen.
THEME_COLORS = {}

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
# Ink is picked by measuring contrast against the chip, not by thresholding
# its luminance (see theme.text_on). Below this ratio the theme's own fg/bg
# is abandoned for plain black or white. 4.5 is WCAG AA for body text.
INK_MIN_CONTRAST = 4.5

DIM_ALPHA = 0.18
CHIP_ALPHA = 0.96

# How far text recedes toward the chip behind it. 0 is the full ink colour,
# 1 is invisible.
INK_TYPED_MIX = 0.55       # a hint label's already-typed prefix
LEGEND_MEANING_MIX = 0.28  # what a key means, beside the key
# ...but never so far that it stops being readable. The fade above is a
# ceiling; theme._recede backs off until the meaning clears this contrast
# ratio against the chip behind it. 4.5 is WCAG AA for body text.
LEGEND_MEANING_MIN_CONTRAST = 4.5
# The typed prefix gets a lower floor than that on purpose. You do not read it
# -- you just typed it, and the whole point is that the letters still to press
# stand out against it -- so it may recede further than something meant to be
# read. 3.0 is WCAG AA for large text and UI components: still visible, still
# clearly the receded half.
INK_TYPED_MIN_CONTRAST = 3.0

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
# The pointer is borrowed and put straight back, so this is how long the
# cursor visibly sits somewhere the user did not put it. It was 120ms, which
# with the press either side of it meant the cursor was away for 173ms --
# long enough to see it happen on every hint click that cannot use the
# element's own action (54% of them, counted over this desktop's log).
#
# Swept against a real button in a nested server, five clicks per value:
# every one of 120/60/30/10/0ms landed exactly one click, and none of them
# started a drag. 20ms keeps a margin for an application that debounces --
# the drag this guards against is a real thing that happened to links, and a
# GTK button is not a browser link -- while cutting the cursor's time away
# from 173ms to about 65ms. Raise it again if a click ever picks something up
# instead of following it.
CLICK_SETTLE_MS = 20

# Log every key the overlay receives. Useful when hints appear but typing them
# does nothing, which usually means the keyboard grab is not exclusive.
# A mode closes itself after this long with no keypress. Its keyboard grab is
# exclusive, so while it is open every other binding on the desktop is dead --
# a session left open by accident looks exactly like the keyboard breaking.
IDLE_TIMEOUT_S = 12
# Longer for the modes you sit inside and read in. Hint and search are typed
# through in a second or two, so a short leash there is free; caret and scroll
# are used while actually reading the thing they are pointed at, and 12s of
# that is a mode that closes under you mid-paragraph. Still bounded: the grab
# is exclusive, so it can never be indefinite.
DWELL_TIMEOUT_S = 45

# How often, while a mode is open, to check whether the workspace changed under
# it. A mode's whole picture of the screen belongs to the window that was in
# front when it opened, so switching workspace closes it. One in-process X
# property read per tick, and only while something is actually open.
WORKSPACE_POLL_MS = 250

DEBUG_KEYS = False

# The daemon always logs to $XDG_STATE_HOME/homerow/homerow.log, rotating at
# this size. --debug additionally mirrors it to stdout.
LOG_MAX_BYTES = 512 * 1024
