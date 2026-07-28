# homerow-replika

Homerow-style keyboard hints for X11. Press a key, every clickable thing on
screen gets a label, type the label to click it.

Homerow reads macOS's Accessibility API to find UI elements. The direct Linux
equivalent is **AT-SPI2**, which is what this uses — so hints land on real
elements with real bounds, not on guessed rectangles.

Built and verified against this machine: Arch, X11, qtile 0.36, picom, Python
3.14.

## Usage

`alt+shift+f` shows hints over the focused window.

One press hints two kinds of target:

- **clickable elements** in the focused window, in the theme's main accent
- **other visible windows**, in a second accent, so the same keypress switches
  apps instead of clicking. Selecting one focuses it, switching group if
  needed. Turn off with `HINT_WINDOWS = False`.

| Key | Action |
|---|---|
| `a`–`l` (home row) | type a label to click it, or switch to that window |
| `Shift` + label | shift-click (opens links in a new tab) |
| `Ctrl` + label | ctrl-click (new background tab) |
| `,` then label | right-click |
| `.` then label | middle-click |
| `Backspace` | undo a typed character |
| `Esc` | cancel |

Homerow uses `shift+space`; grabbing that globally on X11 would swallow it
everywhere you type, so this uses `alt+shift+f`.

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

## Install

Already wired up:

- `alt+shift+f` added at the top of `keys` in `~/.config/qtile/config.py`
- daemon added to `~/.config/qtile/autostart.sh`, using the same
  `pgrep -f … || …` guard as your other daemons

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
| chip with a typed prefix | `cyan` | `CHIP_SLOT_MATCHED` |
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
assuming a dark desktop, so light themes stay readable. To pin colors instead,
set `FOLLOW_THEME = False` and edit `FALLBACK_PALETTE`.

## Coverage

Surveyed on this machine, each app focused for real and the result checked
against a screenshot:

| App | Toolkit | Result |
|---|---|---|
| Brave | Chromium | 46 elements, 78ms — needs the flag below |
| Firefox | Gecko | full chrome + page content |
| qutebrowser | Qt WebEngine | full chrome + page content |
| VS Code | Electron | works — needs the flag below |
| Kate | Qt / KDE | menus, toolbar, buttons, sidebar |
| pcmanfm | GTK | 40 elements, 27ms |
| pavucontrol | GTK (old ATK) | works, via both fallbacks below |
| Dolphin | Qt / KDE | works, **including file items** |
| pcmanfm-qt | Qt / LXQt | works, **including file items** |
| pcmanfm | GTK | chrome and sidebar only — see below |
| kitty | terminal | nothing, and nothing to be done |

Terminals expose no accessibility tree at all, so they will never hint. Neither
will Flutter, most games, or Electron apps without the flag.

**pcmanfm does not expose its files.** Menus, toolbar and the Places sidebar
hint fine, but the folder view publishes *zero* accessibles — probed directly,
nothing at all exists in that region of the tree. This was tested in **both**
Icon View and Detailed List View (switched via the app's own accessible menu
action): both publish nothing, so it is libfm's folder view lacking ATK
support, not a quirk of one view mode. No filter or role list can recover what
was never published.

**pcmanfm-qt is the drop-in fix** — the Qt port of the same file manager,
installed and verified here: 53 elements in a full-screen window with a hint on
every folder, and 49 in the home directory. Its file items come through as
`list item` with names, and they carry FOCUSABLE, so they survive the container
filter. Dolphin also works if you prefer it.

Switching to it is two edits you may or may not want:

- `~/.config/qtile/config.py:2434` launches `pcmanfm` from the app menu
- `xdg-mime default pcmanfm-qt.desktop inode/directory` for folder handling
  (currently `kitty-open.desktop`)

Two toolkit quirks are handled, both found on pavucontrol:

- **No Collection interface.** Older ATK apps implement it on neither the
  application nor the frame. There is a bounded fallback walk for those, capped
  by `WALK_BUDGET_MS` because walking costs a round trip per node.
- **Broken SCREEN coordinates.** pavucontrol reports every element at `0,0` in
  screen space while its WINDOW-space coordinates are correct. When most of a
  batch lands on the origin, coordinates are re-read in window space and
  offset by the window position.

**Chromium and Electron need a flag.** They expose nothing by default. Already
applied to both, each of which is read at launch only, so restart the app:

- `~/.config/brave-flags.conf`
- `~/.config/code-flags.conf`

```
--force-renderer-accessibility
```

This keeps an accessibility tree live for every tab, which costs memory on
heavy sessions. Remove the line to undo.

VS Code additionally reacts to the flag by switching the editor into "Screen
Reader Optimized" mode, which changes wrapping and disables some rendering
optimisations. To keep hints without that, set in VS Code's settings.json:

```json
"editor.accessibilitySupport": "off"
```

That tells the editor not to adapt itself while leaving the tree exposed.

GTK apps also need `toolkit-accessibility`, which is already set:

```sh
gsettings set org.gnome.desktop.interface toolkit-accessibility true
```

Apps with **no** accessibility tree — Flutter, most games, some Electron
builds, legacy X11 toolkits — show no hints and print a notification saying so.
That is a deliberate limit: this hints real elements or nothing at all.

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
| `homerow/theme.py` | palette from theme-apply / pywal |
| `homerow/config.py` | roles, alphabet, tuning, fallback palette |

## Notes from building this

Three things were not obvious and are worth keeping in mind before changing
the code:

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
- Scroll mode and search mode are not built — click mode only.
