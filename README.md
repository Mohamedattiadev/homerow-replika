# homerow-replika

Keyboard control of the whole desktop on X11 — click anything, scroll anything,
search anything, put a cursor in text. Modelled on
[Homerow](https://homerow.app) for macOS.

![demo](docs/demo.gif)

Homerow reads macOS's Accessibility API to find real UI elements. The direct
Linux equivalent is **AT-SPI2**, which is what this uses — so hints land on real
elements with real bounds, not on guessed rectangles or screen-scraped pixels.

Built and used on Arch + qtile + picom, Python 3.14.

## Modes

One key per mode, no chord in the way:

| Key | Mode | Homerow's |
|---|---|---|
| `alt+space` | hint / click | `shift+space` |
| `alt+j` | scroll | `shift+J` |
| `alt+/` | search | `shift+/` |
| `alt+c` | caret | — |
| `alt+shift+c` | caret search | — |
| `alt+e` | edit in nvim | — |

`alt` rather than `shift`, because grabbing `shift+<key>` globally on X11 would
swallow it everywhere you type.

**Any of these works from inside another mode.** `alt+j` while hints are up
enters scroll mode directly; there is no Esc first. Each mode holds the
keyboard exclusively — it has to, or its letters leak into the app underneath
— so the window manager never sees these keys while a mode is open and the
mode reads them itself. The modifier is what keeps them apart from a mode's
own plain letters: `j` still scrolls, `c` is still a hint label. The one
refusal is an open editor, which holds text you have typed and not written
back (see Edit below).

### Hint

Labels every clickable element, and every other visible window.

| Key | Action |
|---|---|
| `a`–`l` | type a label to click it, or switch to that window |
| any other letter | **filter** — narrows the hints and relabels the survivors |
| `Shift`+label | shift-click (new tab) |
| `Ctrl`+label | ctrl-click (background tab) |
| `,` then label | right-click |
| `.` then label | middle-click |
| `Backspace` | undo |
| `Esc` | cancel |

Windows are labelled in a second colour, so the same keypress that clicks a
button can instead switch apps.

Chips sit **on** the element they label, overlapping its top-left corner, and
only move aside for another chip. Beside it was tried first for years, and it
is what makes a label ambiguous: a chip in the gap between two controls
belongs to whichever one you assume, and on a toolbar or a row of links the
assumption is wrong about half the time. A chip sitting on a thing cannot be
misread, which is why Homerow and Vimium both put it there and accept covering
a few pixels of the target.

### Scroll

Enters immediately on the region under the pointer, else the largest. No picker.

| Key | Action |
|---|---|
| `j` / `k` | line down / up |
| `d` / `u` | half page |
| `gg` / `G` | top / bottom |
| `h` / `l` | sideways — only offered where content overflows sideways |
| `3j`, `5k` | count prefixes |
| `Tab` | next region |
| `Esc` | leave |

### Search

Modelled on vim's `/`.

| Key | Action |
|---|---|
| any letter | filter; matches are outlined and numbered |
| `1`–`9` | click that match |
| `Tab` / `Shift+Tab` | cycle |
| `Enter` | click the current match |
| `Esc` | cancel |

Labels are **digits** so every letter stays usable in the query — that is what
lets filtering and picking share one prompt instead of needing two phases.
Matches are ranked: whole-label hits, then whole-word, then prefix, then
substring.

### Caret

A real text cursor driven by vim motions, over AT-SPI's Text interface.

| Key | Action |
|---|---|
| `h j k l` | move by character / line |
| `w` `b` `e` | by word |
| `0` `$` | line start / end |
| `gg` `G` | document start / end |
| `v` / `V` | visual select / visual line select |
| `y` | yank the selection (or the word under the cursor) |
| `yy` | yank the current line |
| `x` | delete the selection, or the character under the cursor |
| `d` | delete the selection (visual), `dd` the line |
| `p` | put the clipboard at the cursor |
| `1`–`9` / `Tab` | jump between text blocks |
| `/` | reopen caret search (see below) without leaving caret mode |
| `i` | open this field in nvim, cursor where the caret is (see Edit) |
| `Esc` | leave |

Yanking stays in caret mode afterward, same as vim — it doesn't exit, so `yy`
can be followed by more motion or another yank. `/` works the same way: land
on a word via caret search, select or yank something, then `/` again to find
the next thing — no need to back out to Esc and press the hotkey over again.

`x`, `d` and `p` change the text, and do it the same two ways edit mode
writes a field back: through AT-SPI's `EditableText` where the app publishes
one, and by pressing keys at the app where it does not. Chromium publishes no
`EditableText` — but it does accept a caret position, so the fallback puts
the caret where the range starts and presses `Delete` over it. That is what
kindaVim calls its Keyboard Strategy, reached from the same dead end.

The overlay holds an exclusive keyboard grab, so it drops the grab for the
length of those keystrokes and takes it again afterwards; otherwise the keys
meant for the application would be delivered straight back to us.

`i` is the bridge between the two modes that know the most about where you
are. The caret cannot insert text of its own — it is a cursor over a Text
interface, not an input method — so `i` does the honest version: it opens the
field the caret is in as an editor, at that exact offset. Land on a word with
caret search, press `i`, and nvim opens with the cursor already on it. Only
where the text is actually editable; opening page prose in an editor that
could never write it back is a dead end dressed up as a feature, so it says so
instead.

In apps with their own vim caret mode — qutebrowser, Firefox — it enters
*theirs* instead, and says so.

### Caret search

Type to find a word or link anywhere on the page; matches are labelled as
you type, same as search mode. Picking one doesn't click it — it opens caret
mode with the cursor already sitting on that exact word, so a long article
or a page full of links is reachable by name instead of by Tab-cycling
through whole blocks one at a time.

| Key | Action |
|---|---|
| any letter | filter; matches are outlined and numbered |
| `1`–`9` | jump the caret to that match |
| `Tab` / `Shift+Tab` | cycle |
| `Enter` | jump to the current match |
| `Esc` | cancel |

Bound separately from plain caret mode (`--caret-search`, see Install below)
so the existing "land on the biggest/nearest block immediately" behavior of
`--caret` is unchanged.

### Edit

Labels every editable field on screen. Pick one and it opens in nvim — your
nvim, your config — in a borderless window sitting on the field itself, sized
to it. Write, `:wq`, and the text goes back into the field. Quit with `:q!`
and nothing is written.

The window is measured in character cells off the running terminal, so it
comes out the width of the field and as few rows as the editor can work in.
A one-line field gets a small box, sized to the content it opens with —
wrapping counted — up to a cap. Two rows are budgeted for whatever the editor
keeps for itself (a statusline, a command line), so your text is visible
whatever your config does.

The box does **not** grow as you type past it; the editor scrolls, the way an
editor in any other window does. A box that resized itself under your cursor
would move the text you were reading to make room for text you had not
written yet, and nvim already has a good answer for running out of room.

Sizing it means counting three things and getting all three right: the cell,
the frame homerow draws, and the padding the terminal widget keeps for
itself. Missing the third cost exactly one row — see the note at the end.

**Nothing has to be added to your editor config for this.** An earlier version
announced homerow to nvim as `g:started_by_homerow` and asked you to add a
condition to your statusline plugin so it would stand its row down. That is
the wrong shape for a tool whose promise is that it works with your editor as
it is, and it was fragile in three separate ways besides — measuring a phantom
UI's geometry, measuring a dashboard where the statusline hides itself, and
measuring its own write. What is left asks nothing of your config: two rows
are assumed, and homerow asks *its own* editor over its own socket what it
actually kept, then sizes the **next** field for that. Being one row generous
for one field is cheaper than resizing a window somebody has started reading.

| Key | Action |
|---|---|
| `a`–`l` | type a label to open that field |
| `Esc` | cancel |

Then, inside the editor:

| Key | Action |
|---|---|
| `Esc` | write back and close |
| `q` | the same |
| `<Space>w` | the same |
| `:wq` | the same thing, longhand |
| `:q!` | close and leave the field alone |

`Esc` leaves, the way it leaves every other mode here — from insert that is
two presses, one to leave insert and one to leave, which is the shape the
rest of homerow already has. All three are mapped `<buffer>`-locally, so `q`
is still macro recording, `<Space>` is still your leader, and `Esc` is still
just `Esc` everywhere else in the same nvim. They write rather than
discarding, because an edit thrown away silently is the worse mistake to make
on a keystroke that short. `:q!` is still there for actually discarding.

In a GTK or Qt field, `:w` pushes the text into the field without closing, so
you can watch it land and keep editing. In a browser it does not: updating a
Chromium field means typing into it, and stealing focus away from the editor
mid-edit is worse than waiting — there, the field updates when you close.

**Opening is warm.** A headless nvim is started with the daemon and the
editor in the field is a remote UI attaching to it, so opening a field does
not pay to load your config — measured at 285ms here, which was most of the
delay before you could type. The server is replaced after each session
rather than reused, because a dismissed editor leaves a modified buffer that
the next `:edit` would fail on. If anything about that path fails, the mode
falls back to starting nvim from cold.

**Reap the old server before starting its replacement.** nvim unlinks its
listen socket as it exits, and the replacement listens on the same path — so
an unreaped predecessor deletes its successor's socket on the way out.
Measured over three real sessions: every edit after the first found no server
and started cold, so the warm path was warm exactly once per daemon. Worse
than slow, too — while both are alive, a command sent to "the socket" and a UI
attached to it can reach different processes, and then the editor you are
looking at is not the one holding your field.

**The write-back is checked.** After the text goes back, the field is read
again and compared. The paste path cannot fail loudly — it borrows the
clipboard, focuses the window and presses `ctrl+a ctrl+v`, and an application
that was busy swallows any of that while the mode reports success. Reading
needs no focus and no keystroke, so confirming costs one round trip. And an
edit that empties a field which had something in it keeps a copy of what was
there and says where: it is the one write that editing again cannot undo.

With exactly one editable field on screen there is nothing to pick, so it
opens straight away — and neither does a field you are already typing in. If
the application says which field has focus, that is the one, and the picker is
a question with a known answer. Measured over this desktop's log, half of all
edit sessions had exactly two fields on screen, so the hint step was asking
which of two things you meant when one of them was the box the cursor was
already in. While an editor is open no other homerow mode will
open — an overlay may be replaced freely, an editor holding unsaved text
may not.

**Switching workspace closes it too**, like every other mode, but never
empty-handed. The buffer is saved first, so what leaves with the editor is
what you typed rather than what you last wrote. A GTK or Qt field then takes
the text immediately — that needs no focus, so it lands without dragging you
back. A browser field cannot be written without focusing its window, which
would haul you to the workspace you just left; that one keeps its buffer in
`/tmp` and tells you where. It used to opt out of the workspace watch
entirely, and the cost was worse than the problem: the editor stayed open
over a field nobody was looking at, the bar kept saying `edit`, and every
other mode refused to open behind it.

vim-anywhere opens an *empty* buffer and leaves the pasting to you; this reads
the field first, so it round-trips. What neither vim-anywhere nor GhostText nor
Firenvim can do is choose the field: they all start from wherever the focus
already is. Reading through AT-SPI is what makes picking one possible.

**How the text gets back in.** Two ways, probed per element rather than
configured per app:

| | Used for | Cost |
|---|---|---|
| AT-SPI `EditableText` | GTK, Qt | none — no clipboard, no keystrokes |
| clipboard + `ctrl+a ctrl+v` | Chromium, Electron, anything else | borrows the clipboard for half a second, then hands it back |

Chromium publishes no `EditableText` interface at all, so every browser field
takes the second route. Reading is unaffected — the `Text` interface is there
in every app, which is why the field's current contents come out without a
`ctrl+a ctrl+c` and without the field even being focused.

kindaVim needs a hand-maintained per-app override list for the equivalent
choice, because macOS applications advertise accessibility support they do not
have. AT-SPI applications answer honestly, so this asks each element instead.

**The browser's own fields count too.** Edit mode narrows to the foreground
document so that a browser keeping five tabs alive does not offer five pages
of fields on one viewport — and that narrowing used to throw the browser's
chrome out with them. Reported live on Google Drive: the page's search box got
a hint and the address bar beside it did not, because the address bar is not
inside any document. Every tab's document reports the same viewport rectangle,
so what gets rejected is "inside the viewport but not in the foreground
document", and chrome — outside the viewport entirely — is kept.

**What counts as a field** is the AT-SPI `EDITABLE` state, not a list of
roles. A role list was tried first and kept missing things — GitHub's "Go to
file" is a `combo box`, Chromium's contenteditables are `section`s — and every
role added to it only revealed the next one it lacked.

**Not offered:** password fields (edit mode writes to a temp file, and a
password has no business on disk) and terminals. Since the match rule no
longer filters by role, that exclusion happens in Python and fails closed: a
field whose role cannot be read is dropped, not kept.

## Install

### 1. Dependencies

```sh
# Arch; the names differ elsewhere, the libraries do not
sudo pacman -S at-spi2-core python-gobject libx11 libxtst \
               xdotool xclip openbsd-netcat vte3
```

| | For |
|---|---|
| `at-spi2-core` | the accessibility tree — this is the whole premise |
| `python-gobject`, `gtk3` | the overlay, and the AT-SPI bindings |
| `libX11`, `libXtst` | window queries and synthetic clicks, through ctypes |
| `xdotool` | pointer moves, wheel clicks, window activation |
| `xclip` | the clipboard, for yanking and for writing browser fields |
| `openbsd-netcat` | the fast client — one line to a unix socket |
| `vte3` | **edit mode only**, imported lazily |
| `picom` | only if your compositor is not already one |

Nothing else, and nothing to build. `vte3` is the one dependency a mode can
do without: it is imported lazily, so a desktop lacking it loses edit mode
and keeps the other five.

### 2. Clone, and check

```sh
git clone <this repo> ~/homerow-replika
~/homerow-replika/bin/homerow --doctor
```

`--doctor` is the install instructions in executable form. It checks every
library, typelib and command above, whether your session publishes an
accessibility tree at all, and whether the browsers you have installed have
the flag they need — and prints the command to fix each thing it finds. It is
also the first thing to run when a mode is not seeing anything.

### 3. Bind the modes

They are just commands, so any window manager works. qtile:

```python
HOMEROW = os.path.expanduser("~/homerow-replika/bin/homerow")
Key([mod2], "space",        lazy.spawn(HOMEROW)),
Key([mod2], "j",            lazy.spawn(HOMEROW + " --scroll")),
Key([mod2], "slash",        lazy.spawn(HOMEROW + " --search")),
Key([mod2], "c",            lazy.spawn(HOMEROW + " --caret")),
Key([mod2, "shift"], "c",   lazy.spawn(HOMEROW + " --caret-search")),
Key([mod2], "e",            lazy.spawn(HOMEROW + " --edit")),
```

That is the only file of yours homerow asks you to touch, and it asks
because binding a key is your window manager's job and nobody else's.
Nothing needs adding to your nvim config, your GTK settings or your shell —
if a version of this ever asks you to, that is a bug, and one this project
has already made and removed once (see Edit).

### 4. Start the daemon at login

A line in your autostart, or the unit in `contrib/homerow.service`:

```sh
mkdir -p ~/.config/systemd/user
cp ~/homerow-replika/contrib/homerow.service ~/.config/systemd/user/
systemctl --user enable --now homerow
```

If it is not running, the client starts it and retries, so nothing breaks
when you forget.

### 5. Chromium and Electron need a flag

They expose nothing to accessibility by default. Add it to
`~/.config/brave-flags.conf`, `~/.config/code-flags.conf`,
`~/.config/obsidian/user-flags.conf` — each app reads its own file — and
restart the app:

```
--force-renderer-accessibility
```

GTK apps need `gsettings set org.gnome.desktop.interface toolkit-accessibility true`.

VS Code additionally switches into "Screen Reader Optimized" mode with the
flag; `"editor.accessibilitySupport": "off"` in its settings keeps hints
without that.

`homerow --doctor` checks both of these and prints the exact line to add.

## Configure

Everything is tunable, and none of it requires editing the source.

```sh
homerow --write-config      # ~/.config/homerow/config.yaml, fully commented
homerow --check-config      # what it read, and what it could not use
homerow --show-config       # every setting and its value right now
homerow --restart           # the daemon reads the file once, at startup
```

The file layers over the built-in defaults, so it only has to name what you
want changed:

```yaml
hint:
  alphabet: "arstdhneio"     # colemak home row
  windows: false             # stop labelling other windows
scroll:
  page_clicks: 10
edit:
  editor: "hx"
chip_slot: blue              # which slot of the desktop theme a chip takes
idle_timeout_s: 20
```

Keys are the names from `homerow/config.py`, lowercased. They can be grouped
under the prefix they share, as above, or written flat (`hint_alphabet:`) —
both work, so the grouping is there to read, never to get right.
`config.py` stays the schema and the reason for every default: anything named
there is settable, nothing else is, and the default's *type* is what a value
is checked against. There is no second list to fall out of step with it.

**A config file cannot break your desktop.** An unknown key, a string where a
number belongs, an empty alphabet that would leave hint mode with no labels,
a file that is not YAML at all — each is reported in the log and the built-in
default stands. Keyboard control that dies over a stray tab is worse than
keyboard control that ignores the line.

PyYAML is used if you have it, and a small reader for the subset the config
file needs stands in if you do not — so this adds no dependency. Both are
tested against the same shipped example and required to agree.

## How it runs

A resident daemon holds the Python interpreter, the PyGObject imports and the
AT-SPI connection. `bin/homerow` is a shell script that writes one line to a
unix socket and exits — deliberately not Python, because the interpreter alone
cost more than all the real work combined. Measured here: 35ms through the
shell, 645ms for the identical request through `python3`.

```
alt+space
  └─ bin/homerow (sh + nc, ~12ms)
       └─ $XDG_RUNTIME_DIR/homerow.sock
            └─ the daemon ── collect ~25ms ── overlay ~2ms
```

So `bin/homerow` understands the six mode flags and nothing else. Everything
that happens at human speed goes to `homerow-cli` unread, which is where the
whole surface lives — one command, `homerow --help`:

```sh
homerow                  # hint and click
homerow --edit           # …and the other five modes

homerow --status         # is one answering?
homerow --log 40         # tail the log
homerow --quit           # stop it
homerow --restart        # stop it and start another
homerow --daemon         # run one in the foreground
homerow --doctor         # check the install

homerow --config PATH    # use this file instead of the default
homerow --show-config    # …see Configure above
homerow -l/--list        # print what would be hinted, draw nothing
homerow --standalone     # a hint pass without a daemon, when one won't start
homerow -h/-v/-d         # help, version, debug
```

`homerow-hint` and `homerow-daemon` still work — they are two-line shims now,
kept because somebody has them in their keybindings.

The daemon always logs to `$XDG_STATE_HOME/homerow/homerow.log`. It holds the
modules in memory, so `homerow --restart` is what makes an edit under
`homerow/` take effect.

## Theming

Colours come from the desktop theme, re-read on every press so a theme switch
lands immediately:

1. `~/.cache/qtile/current_palette.json` (written by `theme-apply`)
2. `~/.cache/wal/colors.json`
3. a built-in fallback

| Role | Slot | Setting |
|---|---|---|
| hint chip | dominant accent | `chip_slot` |
| typed prefix, scroll outline | cyan | `chip_slot_matched` |
| window chip | purple | `chip_slot_window` |

Text colour is chosen by measuring its **contrast** against the chip, so light
themes stay readable. It used to threshold the chip's luminance and trust the
result, which is right most of the time and quietly wrong when an accent lands
near the middle: measured here, the window chip's ink came out at 2.24:1 and a
legend's meanings at 2.49:1, both well under readable. Contrast is the thing
actually being asked about, so it is the thing measured — the theme's own
foreground or background wherever one of them clears
`ink_min_contrast`, and plain black or white only when neither does. Nothing
is hardcoded: even the fallback is hex names run through the same slot and
contrast logic. `follow_theme: false` pins it.

Everything tunable lives in `homerow/config.py` — named settings, no magic
numbers left in the modules — and every one of them can be set from
`~/.config/homerow/config.yaml` without touching the source. See Configure.

## Coverage

| App | Result |
|---|---|
| Brave / Chromium | full, with the flag |
| Firefox | full |
| qutebrowser | full chrome and page |
| VS Code, Obsidian | full, with the flag |
| Kate, Dolphin, pcmanfm-qt | full, including file lists |
| pavucontrol | works, via two fallbacks |
| pcmanfm (GTK) | chrome only — its folder view publishes nothing |
| terminals, VLC | nothing to hint; window switching and scrolling still work |

Measured over 15 real sites: 91 hints per page on average at ~200ms, 1.9 scroll
regions, 0.9% of hints without a name.

## Tests

```sh
python3 -m unittest discover -s tests
```

Split by subject: `test_pure.py` covers the algorithms (label generation,
ranking, dedup, overflow detection); `test_rules.py` covers the decisions
(which window counts as focused, what is worth offering, how a match is graded)
plus structural checks that caught real crashes — every `connect()` handler
must exist, every module referenced must be imported.

Two of those structural checks are about documentation that would otherwise
go stale unnoticed. `contrib/config.yaml` ships with every setting commented
out, so nothing about reading it reveals that a setting it names has been
renamed or that a default it states has moved on — the tests uncomment it and
load it for real. And it is loaded through *both* YAML readers and the results
compared, because an install without PyYAML must not be quietly a different
program.

## Notes from building this

Things that were not obvious, kept because they will bite again:

**Never walk the AT-SPI tree.** Every node access is a D-Bus round trip, about
480 nodes/sec. `Collection.get_matches` runs the filter inside the target app
and answers in one trip. Names and roles are lazy for the same reason.

**Don't shell out on a hot path.** Every `xdotool` call is an ~11ms process
spawn. `homerow/x11.py` binds what is actually needed through ctypes: active
window lookup went 11ms → 0.3ms, window enumeration 114ms → 1ms.

**Scope to the window, then the tab.** An app reports every window it has, and
a WM that stacks them puts several at identical coordinates — Brave had six
frames, three sharing one rectangle. Clipping by geometry alone lets windows
you cannot see contribute hints. Background tabs do the same: Qt WebEngine
keeps them all alive claiming SHOWING and VISIBLE, so a five-tab window offered
five pages of hints stacked on one viewport.

**Override-redirect overlays do not composite** under qtile + picom. A `POPUP`
maps, `draw` fires, and nothing appears. `TOPLEVEL` with the DOCK hint works,
plus an explicit `raise_()` — and picom needs the window excluded from its
animations, or hints slide in from the top instead of appearing on their
targets.

**A cleanup call that fails open can strand the whole desktop.** Clicking a
text field makes homerow leave the qtile chord, so `h`/`s`/`f` go back to
being letters. qtile's `ungrab_chord()` calls `ungrab_keys()` *first* and only
then checks whether a chord was actually active — when none was, it logs a
debug line and returns without re-grabbing anything. Launch hint mode from the
direct `alt+space` binding (not a chord), click one text field, and every
keybinding on the machine is dead until the config is reloaded. The fix is to
ask and act in the same breath, inside qtile: `self.ungrab_chord() if
self.chord_stack else None`. Worth stating generally — a "tidy up after
yourself" call that is a no-op in the normal case is exactly the one nobody
tests against the case where there was nothing to tidy.

**Grab the keyboard exclusively, and never indefinitely.** `owner_events=False`
or keystrokes leak to the app underneath. And every mode closes itself after a
spell with no input: the grab is exclusive, so a session left open by accident
is indistinguishable from the keyboard breaking.

**Measure scrollability, then verify it.** Role is a bad signal — a short list
is still a `LIST`. Content overflow is better, but a virtualised list renders
only its visible rows, so nothing measurable proves it scrolls. Those are found
by actually scrolling them and watching what moves.

**A rescue budget spent on wrappers never reaches the real scroller.** Live on
devdocs.io: overflow measurement found nothing scrollable at all, yet the
content pane plainly scrolls — confirmed by probing it directly. It ranked
3rd by area behind two non-scrolling page-level wrapper candidates (the whole
toolbar+page area, and the whole document including the sidebar), so a rescue
budget of 2 spent both slots on wrappers and never got to it. Two fixes:
raise the budget, and keep testing after the first success instead of
stopping there — a page can have more than one virtualised pane (a sidebar
*and* its content), and stopping early rescues one and leaves the other
looking permanently unscrollable.

**A legend is scanned, not read.** Every mode keeps a pill at the bottom of
the screen saying what its keys do, and each built its own by joining strings:
the mode name, whatever was live in it, and forty characters of static help,
all in one weight and one colour. Caret mode's reached 150 characters and
1070px, most of a screen wide, and the part that changed while you worked — a
part-typed `d`, which block the caret was in — was the part you were least
likely to notice, because it looked exactly like the help text it was wedged
between. It is now composed from parts rather than concatenated: the mode and
its live state are inverted badges, each key is inked and its meaning recedes,
and the gap between a key and its own meaning is smaller than the gap to the
next pair, so the grouping is carried by spacing instead of punctuation. Keys
are spelled without spaces inside them — `hjkl`, not `h j k l`, which is how a
vim user reads it anyway and a third narrower. The same row is now 770px. Past
a screen's width the pairs drop from the end, but never the last one — every
mode's list ends in `esc`, and a legend that has dropped the way out is worse
than one that is slightly too wide.

**Don't spend the user's page to answer a question they haven't asked.**
Proving a region scrolls means scrolling it and putting it back, and that is
not a measurement — it is the page visibly jumping, while they wait. Entering
scroll mode did it twice over: once to pick which of three points to aim the
wheel at, once to rescue virtualised panes. Measured on a Wikipedia article in
Chromium, ~1250ms of visible motion before a key could be pressed, and the aim
probe's usual verdict was the default it started with. Both are now paid
lazily and by whoever benefits: the aim is settled by watching the user's own
first `j` land, which costs nothing when it was right, and the rescue happens
on `Tab`, the key that asks for another region. The one probe that genuinely
had to happen — deciding which region the session opens on, when the pointer
rests on a pane nothing else can see — still happens, but *after* the outline
is drawn rather than before, so the mode is usable while it runs and the
outline snaps over if it finds something. It stands down the moment a key is
pressed: the user's own scroll answers the same question, and two things
scrolling one page at once is worse than either. Measured over three runs on
the same page and pointer position, counting frames that actually move:
~1250ms of motion before a key could be pressed, and none now.

**A widget's grid is its allocation, not what you set it to.** Edit mode sized
its window at `rows * char_height` plus its own frame, and every box came out
one row short of what it had computed. `Vte.Terminal` fits
`(allocation - padding) // cell` characters, not `allocation // cell` — it
carries 1px of CSS padding on each edge under this theme — and it re-derives
its grid from the allocation on the next size-allocate, so the
`set_size(cols, rows)` before it is overruled a moment later regardless.

In a compact box the missing row is the only one the text was going to be on.
Measured on a 681x30 field: the widget settled at 96x1 while nvim still had
`&lines=2`, so nvim drew two rows into a one-row grid, the terminal scrolled,
and the one visible row was lualine's statusline — a box showing a statusline,
dead space, and none of your text. Same field with the padding counted:
allocation 677x48, grid 96x2, `&lines=2`, text on row 1.

Two things worth keeping from how that was found. The obvious suspect was
`set_size()` running *before* `spawn_async()`, since VTE creates the pty
during spawn — and it was innocent: a probe spawning `stty size` showed the
child getting the grid we asked for. And the whole thing was measured
offscreen, against `nvim --remote-expr &lines` and `screenstring()`, without
opening a window on the desktop being worked on. An `OffscreenWindow` was
tried first and lies about exactly the thing under test — it ignores
`resize()` after `show()` — so the probe used a real window parked at
-4000,-4000 with `set_focus_on_map(False)`. Reasoning about geometry is how
this bug survived several attempts; asking the editor what it thinks its
screen is is what ended it.

**A wall-clock deadline for the whole pass, not just each call.**
`Atspi.set_timeout()` bounds one D-Bus call; `scroll.collect()` makes many of
them per press. Each easily dodges that per-call cap alone, but they can sum
to a real stall when the accessibility service is merely slow, not hung. A
single overall budget (`scroll: collect_budget_ms`) covers that case instead,
learned from reading a comparable project's collector
([museslabs/stochos](https://github.com/museslabs/stochos)), which wraps its
whole async collection in one outer deadline for the same reason.

## Limits

- **AT-SPI is uneven.** macOS AX is one API every app implements; this is
  per-toolkit. Qt WebEngine publishes no text at all, so caret mode hands off
  to the browser's own. pcmanfm publishes no file list, in any view mode.
- **A page's tree lags its rendering.** Press immediately after a page loads
  and you may get nothing.
- **JS-heavy apps expose invisible elements** as showing, with real coordinates
  and names — indistinguishable from real controls.

## Licence

MIT.
