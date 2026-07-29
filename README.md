# homerow-replika

Homerow-style keyboard hints for X11. Press a key, every clickable thing on
screen gets a label, type the label to click it.

Homerow reads macOS's Accessibility API to find UI elements. The direct Linux
equivalent is **AT-SPI2**, which is what this uses — so hints land on real
elements with real bounds, not on guessed rectangles.

Built and verified against this machine: Arch, X11, qtile 0.36, picom, Python
3.14.

## Usage

One key per mode, the way Homerow does it — no chord in the way:

| Key | Mode | Homerow's |
|---|---|---|
| `alt+space` | hint / click | `shift+space` |
| `alt+j` | scroll | `shift+J` |
| `alt+/` | search | `shift+/` |
| `alt+c` | caret | — |

`alt` rather than `shift`, because grabbing `shift+<key>` globally on X11 would
swallow it everywhere you type. Caret is on `alt+c` because `alt+v` is already
the CopyQ picker.

The **`win+shift+f`** chord still holds all of them, for discovery and for
chaining several actions without reaching for a modifier each time:

| Key | Mode |
|---|---|
| `h` | **hint** — same as `alt+space` |
| `s` | **scroll** — pick a scrollable region, drive it with vim keys |
| `f` | **search** — type part of a label, then hint the matches |
| `n` / `w` | warpd normal / warpd hints, for apps with no accessibility |
| `q` / `Esc` | leave the chord |

The chord **stays active** across actions, so you can scroll one region, then
hint, then scroll another without reopening it. The one exception is clicking
into a text field: homerow leaves the chord automatically, because `h`/`s`/`f`
have to be letters again the moment you are typing. Reopen with `win+shift+f`. homerow's own keyboard grab
outranks qtile's passive key grabs while an overlay is up, so the chord cannot
steal those keys; once the overlay closes the chord is simply still there.
`q` or `Esc` leaves.

Nothing is bound globally, so `h`, `s` and `f` stay ordinary letters
everywhere else.

### Hint mode

Labels two kinds of target at once:

- **clickable elements** in the focused window, in the theme's main accent
- **other visible windows**, in a second accent — the same keypress that
  clicks a button can instead switch apps. Off with `HINT_WINDOWS = False`.

| Key | Action |
|---|---|
| `a`–`l` (home row) | type a label to click it, or switch to that window |
| any other letter | **filter** — narrows the hints and relabels the survivors |

Filtering reads element names, which are lazy D-Bus round trips, so they are
read in the background from the moment hints appear. Type immediately and the
filter resolves as soon as indexing catches up; doing it inside the key handler
instead froze the first keystroke on a busy page.
| `Shift` + label | shift-click (opens links in a new tab) |
| `Ctrl` + label | ctrl-click (new background tab) |
| `,` then label | right-click |
| `.` then label | middle-click |
| `Backspace` | undo a typed character |
| `Esc` | cancel |

### Scroll mode

Enters immediately on the best region — the one under the pointer, else the
largest. `Tab` cycles the others, shown as `[2/3 tab]` in the legend.

There is deliberately no picker. Asking which region first turned every scroll
into pick-then-scroll, and that extra step is most of what made this feel like
a dialog rather than a keystroke. A wrong guess costs one `Tab`.

| Key | Action |
|---|---|
| `j` / `k` | line down / up |
| `d` / `u` | half page down / up |
| `gg` / `G` | top / bottom |
| `h` / `l` | sideways — only offered when the region scrolls sideways |
| `3j`, `5k` | count prefixes |
| `Tab` | next region |
| `Esc` | leave |

No mode-switch key here. Caret has its own binding, so a `v` inside scroll was
a second route to the same place and a hidden transition inside a mode —
Homerow's scroll mode has none either.

`h`/`l` are refused, and dropped from the legend, on a region whose content
only overflows downwards. The overflow test already measures both axes; horizontal
wheel events sent at a vertical-only region are silently swallowed, and a key
that does nothing reads as the mode being broken.

`gg`/`G` mean "all the way", so they overshoot deliberately rather than use a
tuned click count — 50 clicks looked right on short pages and left `G` stranded
mid-document on long ones. Clicks past the end cost nothing.

#### Caret mode (`v` in the chord)

`v` inside scroll mode selects text with vim motions, driving the
application's **own** selection rather than reimplementing one:

| Key | Action |
|---|---|
| `h` `j` `k` `l` | extend selection by character / line |
| `w` / `b` | by word |
| `0` / `$` | to line start / end |
| `gg` / `G` | to document start / end |
| `y` | yank to clipboard and exit |
| `Esc` | back to scrolling |

Each motion sends the combination the app already understands — `shift+Right`,
`shift+ctrl+Right`, `ctrl+c` — which is what makes it behave natively in a text
field, an editor, or a browser with caret browsing on. It does **not** work
where the app has no caret: a normal web page without caret browsing has
nothing to extend a selection from.

The keyboard grab is dropped for the instant each combination is sent and
retaken afterwards. Without that the synthetic keypress is routed straight back
to our own overlay by the grab, and visual mode silently does nothing.

A container counts as scrollable only when its content actually overflows it.
Two separate mistakes are possible here and both were made:

- **Trusting roles** put scroll hints on things that could not scroll — a short
  list is still a `LIST`.
- **Whitelisting "scrolling" roles** then missed the regions people actually
  want: on a web page the scrollable sidebar is a `SECTION` and the content
  pane is a `PANEL`. Neither sounds scrollable, and a tight whitelist saw
  neither, leaving only the whole page.

So roles nominate candidates and the overflow test decides. Only the largest
`SCROLL_MAX_CANDIDATES` are tested, since each test costs several round trips.
Chromium exposes no scrollbars at all through AT-SPI, so scrollbar checks were
never an option; comparing sampled child extents against the visible box is.

The roles are queried in **two tiers**. `SCROLL_ROLES` — the ones that sound
like they scroll — are few and are the answer in most apps.
`SCROLL_ROLES_FALLBACK` (`SECTION`, `PANEL`, `FILLER`) is only asked for when
the first tier finds nothing that overflows. It has to exist, because a web
page's scrollable sidebar is a `SECTION`; but there are hundreds of them and
every candidate costs a round trip for its rectangle. Asking unconditionally
spent ~330ms fetching 333 rectangles to keep one. Caret mode is tiered the
same way.

Nested scrollables are all offered rather than resolved automatically. A page's
document overflows as well as its sidebar and its content pane, and nothing
here can know which you meant — dropping ancestors would lose the main scroll
target on any page containing a small overflowing widget.

When nothing reports itself as scrollable, scroll mode drives the focused
window instead of refusing. Wheel events do not need accessibility, so a
missing region usually means the app under-reports rather than that it cannot
scroll — terminals and pcmanfm's file view are both in that category.

Scrolling uses synthetic **wheel events**, not `PageDown`/`Home` keypresses. A
keypress goes wherever focus is and can land as text in an input; a wheel event
goes to whatever is under the pointer and cannot type anything.

The session window carries an empty input shape, so it is invisible to the
pointer — otherwise the fullscreen overlay sits under the cursor and eats the
wheel events itself, drawing an outline over a region that never moves.

### Search mode

Modelled on vim's `/`, not on a picker dialog.

| Key | Action |
|---|---|
| any letter | filter as you type; matches get numbered labels |
| `1`–`9` | click that numbered match |
| `Tab` / `Shift+Tab` | next / previous match |
| `Enter` | click the current match |
| `Esc` | cancel |

Labels are **digits**, and that is the whole trick. Letters have to stay
available for the query, so a letter-based label alphabet forces a second
"now pick" phase — which is what made an earlier version awkward. Digits never
collide with what you are typing, so filtering and picking share one prompt:
type a few letters, press the number, done.

Terms are whitespace-separated and all must match, against the accessible name,
the role, and the element's visible text. Text blocks are searched as well as
clickable elements: restricting the set to hintable things meant the heading or
list entry you were looking for was often not being searched at all.

Matches are **ranked**, because only the first few get a number — an exact
match sitting at position twelve is a match you cannot reach. Whole-label hits
come first, then whole-word, then prefix, then anything containing the query.
Searching `bg` really does turn up `WebGLBuffer` (we**bg**l), which is why
ranking rather than filtering is what makes the list usable.

Names are read **in the background**, a chunk per idle tick, and the prompt
says `reading n/m` while it catches up. Reading them all up front cost ~2s on a
busy page: nothing happened, then everything did, which is indistinguishable
from the feature being broken. Names are lazy everywhere else for the same
reason — each one is a D-Bus round trip — and search is the only mode that
needs them at all.

Run it directly to inspect what it sees:

```sh
./homerow-hint --list     # print elements, draw nothing
./homerow-hint --debug    # print timings, then show hints
```

## How it runs

A resident daemon holds the Python interpreter, the PyGObject imports, and the
AT-SPI connection. `homerow-hint` is a shell script that writes one line to a
unix socket and exits — it is not Python, because the interpreter alone cost
more than everything else combined.

```
alt+shift+f
  └─ homerow-hint (sh + nc, ~12ms)
       └─ socket: $XDG_RUNTIME_DIR/homerow.sock
            └─ homerow-daemon ── collect ~50ms ── overlay ~2ms
```

About **65ms** from keypress to hints, against ~750ms before. The daemon starts
from qtile's `autostart.sh`; if it is not running, the client starts it and
retries, so nothing breaks when it is missing.

```sh
./homerow-daemon --status   # is one answering?
./homerow-daemon --quit     # stop it (blocks until actually gone)
./homerow-daemon --debug    # run in foreground, log every request
```

Restart the daemon after editing anything under `homerow/` — it holds the old
modules in memory.

## Tests

```sh
python3 -m unittest discover -s tests
```

43 tests in two files, split by what they are about:

- `test_pure.py` — the algorithms: label generation (counts, uniqueness,
  prefix-freedom, ordering), search ranking, nested-hint collapsing, overflow
  detection.
- `test_rules.py` — the decisions: which window counts as focused, which is
  worth offering as a switch target, how a match is graded, and invariants the
  config has to satisfy (search labels must be digits and must not overlap the
  hint alphabet, or every keystroke becomes ambiguous).

Both cover bugs that actually shipped: a scratchpad two pixels on screen being
treated as the focused app, an exact match ranked below a substring, sixteen
rows in a tall pane counted as scrollable. Anything needing a display, D-Bus or
a running app is deliberately not here.

## Logs

The daemon always logs to `$XDG_STATE_HOME/homerow/homerow.log`, rotating at
512KB. `homerow-daemon --log` prints the tail. `--debug` additionally mirrors
it to stdout. Diagnosing used to mean restarting under `--debug` and hoping
the problem recurred.

## Install

Already wired up:

- `alt+shift+f` added at the top of `keys` in `~/.config/qtile/config.py`
- daemon added to `~/.config/qtile/autostart.sh`, using the same
  `pgrep -f … || …` guard as your other daemons

A systemd user unit is in `contrib/homerow.service` if you would rather have
restart-on-failure and journal integration:

```sh
mkdir -p ~/.config/systemd/user
cp contrib/homerow.service ~/.config/systemd/user/
systemctl --user enable --now homerow
```

Remove the `autostart.sh` line if you switch, or the two will race.

Reload qtile to pick up the binding. Everything else was already present:
`at-spi2-core`, `python-gobject`, `xdotool`, `picom`, `openbsd-netcat`.

## Theming

Hints follow the active desktop theme, using the same sources and precedence as
the qtile popups (`~/.config/qtile/popups/_wal_colors.py`):

1. `~/.cache/qtile/current_palette.json` — written by `theme-apply` on every
   preset *and* wal apply, so it tracks the active mode rather than a stale
   wal palette
2. `~/.cache/wal/colors.json` — pywal's own output, using the bright variants
   (`color11`/`color14`) so chips stay legible on dim wallpapers
3. a built-in fallback

The palette is re-read on **every** hint, not cached at import, so
`theme-apply` shows up on the next press without restarting the daemon.

| Role | Slot | Setting |
|---|---|---|
| hint chip | `green` — the dominant accent | `CHIP_SLOT` |
| typed prefix, scroll outline | `cyan` | `CHIP_SLOT_MATCHED` |
| switch-to-window chip | `purple` | `CHIP_SLOT_WINDOW` |
| label text | `bg` or `fg`, whichever contrasts | — |
| screen dim | `bg` at 18% | `DIM_ALPHA` |

`green` is not literally green: `colors.py` maps it to wal's `color10`, which
its own comment documents as **"main/dominant"** — the wallpaper's most-used
hue, the same one the qtile bar highlights with. Slot names are just the keys
in `current_palette.json`: `red green yellow blue purple cyan`. Change
`CHIP_SLOT` in `homerow/config.py` to any of them.

Because the slot is the wallpaper's dominant hue, a warm wallpaper gives warm
chips. That is the palette doing its job, not a hardcoded color — on other
wallpapers in your cache the same slot resolves to red or orange.

Text color is chosen by measuring the chip's relative luminance rather than
assuming a dark desktop, so light themes stay readable. `FOLLOW_THEME = False`
pins the palette to `theme.FALLBACK` — which is still hex named colors run
through the same slot and contrast logic, so there is no second set of colors
anywhere that could drift out of step.

## Background tabs

Qt WebEngine keeps every background tab's accessibility tree alive, all
reporting SHOWING, VISIBLE and the *same rectangle* as the foreground tab. A
five-tab qutebrowser window therefore offered five pages of hints stacked on
one viewport — 135 where 14 were real, most of them floating over blank space.

No state distinguishes the foreground document; they are identical. The window
title does, because the WM title is the active tab's title, so the document
whose name appears in it wins. Chrome outside the document rectangle — tab bar,
url bar, status line — is still taken from the whole application, so nothing
around the page is lost.

This only engages when an app exposes more than one document, so Chromium and
native apps are untouched.

## Coverage

Every app below was focused for real, with the focus verified before
measuring — activating a window across qtile groups fails silently often
enough that unverified numbers are worthless.

| App | Toolkit | Elements | Scroll regions |
|---|---|---|---|
| VS Code | Electron | 64 | 1 |
| pcmanfm-qt | Qt / LXQt | 51 | window fallback |
| qutebrowser | Qt WebEngine | 42 | window fallback |
| Kate | Qt / KDE | 35 | 2 |
| Brave | Chromium | 34 | 1 |
| Dolphin | Qt / KDE | 34 | window fallback |
| qalculate-gtk | GTK | 85 | 2 |
| pcmanfm | GTK | 24 | 2 |
| pavucontrol | GTK (old ATK) | 12 | window fallback |
| Firefox | Gecko | 11 | window fallback |
| blueman-manager | GTK | 7 | 1 |
| Obsidian | Electron | 6 | window fallback |
| VLC | Qt | 0 | window fallback |
| kitty | terminal | 0 | window fallback |

"window fallback" is not a failure: those windows had nothing that overflowed
at the time, so scroll mode drives the whole window, which is what you want.

Terminals and VLC expose no usable tree, so they will never hint — but window
switching and scrolling still work in them, since neither needs accessibility.

**Chromium and Electron need a flag**, applied to all three already. Each is
read at launch, so restart the app:

```
--force-renderer-accessibility
```

- `~/.config/brave-flags.conf`
- `~/.config/code-flags.conf`
- `~/.config/obsidian/user-flags.conf`

Every Electron app needs its own file, and they do not share a location —
Obsidian reads `obsidian/user-flags.conf` while VS Code reads
`code-flags.conf`. An Electron app showing zero elements almost always means
its flags file is missing rather than anything being broken.

VS Code additionally switches into "Screen Reader Optimized" mode with the
flag. `"editor.accessibilitySupport": "off"` in its settings.json keeps hints
without the editor adapting its wrapping.

GTK apps need `toolkit-accessibility`, already set:

```sh
gsettings set org.gnome.desktop.interface toolkit-accessibility true
```

**pcmanfm does not expose its files.** Menus, toolbar and the Places sidebar
hint fine, but the folder view publishes *zero* accessibles, in both Icon View
and Detailed List View — it is libfm's folder view lacking ATK support, not a
quirk of one view mode. **pcmanfm-qt** is the drop-in fix: same file manager,
Qt toolkit, files hint correctly. Dolphin works too.

## Layout

| File | Role |
|---|---|
| `homerow-hint` | fast client (shell), what qtile calls |
| `homerow-daemon` | resident daemon entry point |
| `homerow-cli` | standalone one-shot, for `--list` / `--debug` |
| `homerow/service.py` | socket server, request handling |
| `homerow/elements.py` | AT-SPI discovery |
| `homerow/hints.py` | prefix-free label generation |
| `homerow/overlay.py` | GTK overlay, keyboard grab |
| `homerow/click.py` | click dispatch |
| `homerow/windows.py` | other windows as switch targets |
| `homerow/scroll.py` | scroll mode: regions + vim-key session |
| `homerow/theme.py` | palette from theme-apply / pywal |
| `homerow/config.py` | roles, alphabet, tuning, fallback palette |

## Notes from building this

Three things were not obvious and are worth keeping in mind before changing
the code:

**Do not shell out on a hot path.** Every `xdotool` call is a process spawn —
~11ms before it does anything — and there were several per keypress: one to
find the active window, three to enumerate windows, one per click. That fixed
cost dominated everything the daemon exists to avoid. `homerow/x11.py` binds
the handful of calls actually needed (`_NET_ACTIVE_WINDOW`, `_NET_CLIENT_LIST`,
window geometry, XTest button and key events) through ctypes, with an xdotool
fallback if the libraries are missing. Active-window lookup went 11ms → 0.3ms
and window enumeration 114ms → 1ms.

**Never walk the AT-SPI tree.** Every node access is a D-Bus round trip,
measured here at ~480 nodes/sec — seconds for a real page.
`Collection.get_matches` runs the filter inside the target app and returns
everything in one call: 2ms instead. Element names and roles are lazy
properties for the same reason; fetching them eagerly cost 258ms per hint pass
and nothing but `--debug` uses them. Collection went from ~1080ms to ~35ms.

**Override-redirect overlays do not composite here.** A `POPUP` window maps at
the right size, `draw` fires, and nothing appears on screen. A `TOPLEVEL` with
the `DOCK` type hint works. It also needs an explicit `raise_()` after
mapping, or a fullscreen client stays stacked above it.

**Grab the keyboard with a retry, and make it exclusive.** A seat grab
attempted before the window is viewable fails silently, and then every
keystroke goes to the app underneath while the overlay just sits there — an
intermittent failure that looked like a click-dispatch bug. Separately the grab
must use `owner_events=False`: with `True`, key events are still routed by
focus, and this window carries the DOCK hint so the WM may never focus it.
Keystrokes leaked to the application below and the overlay silently missed
them.

**Reply to a socket client before acting on its command, but never make the
command depend on the reply succeeding.** `--quit` sent its line and hung up
without reading; the daemon's acknowledgement hit a broken pipe, raised, and
the command was dropped. The daemon answered pings the whole time, so it looked
alive and merely unkillable.

**Filter with states, not with names.** Web pages use nameless list items for
layout, which produced hints floating in empty space. The tempting fix is to
drop elements with no name — but names are lazy precisely because each costs a
D-Bus round trip, so that reintroduces the cost the design removed. `FOCUSABLE`
distinguishes real targets from layout and is evaluated server-side inside the
match rule, so it is free. Container roles require it; inherently actionable
roles like buttons and links do not.

The overlay translates the cairo canvas by the window's true origin, because
qtile does not honour `move(0, 0)` — it offset the window by the bar height,
which put every hint 75px above its target.

## Measured across real sites

15 sites with different layouts (Wikipedia, HN, GitHub, MDN, Arch wiki, Python
docs, GitLab, Reddit, lobste.rs, rustdoc, W3C, and simple static pages):

| | |
|---|---|
| hints | avg 91 per page, 202ms |
| scroll regions | **avg 1.9, never more than 3** |
| caret blocks | avg 36, 150ms |
| hints with no name | avg 0.9%, worst 9.5% (HN) |

The scroll figure is the one that matters: before layout containers were
dropped, a docs page offered three regions where two behave identically.

A page's accessibility tree lags its rendering — rustdoc reported nothing six
seconds after load and 81 elements shortly after. Nothing here can fix that;
it is the browser publishing the tree late.

## Rough edges

- **Collection is the remaining cost**, ~50ms typical but 100ms+ on heavy
  pages, because each element's screen bounds is its own D-Bus round trip
  (~2-10ms). There is no batch extents call in AT-SPI. A thread pool is the
  obvious next step, but libatspi is not obviously thread-safe, so it needs
  care rather than optimism.
- **Nested web elements can still double up.** A link wrapping an image
  sometimes yields two chips a few pixels apart. The overlap dedup catches
  identical rectangles, not nested ones.
- **The overlay grabs the keyboard exclusively while it is up.** Anything you
  type goes to it, not to the app underneath, and a stray keystroke that
  completes a label will click. Escape gets out.
- Search mode (Homerow's `shift+/`) is not built.
