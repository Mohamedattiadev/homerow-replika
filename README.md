# hintium

Keyboard control of the whole desktop on X11 — click anything, scroll
anything, search anything, put a cursor in text. Modelled on
[Homerow](https://homerow.app) for macOS, backed by **AT-SPI2**, so hints land
on real elements with real bounds rather than guessed rectangles.

![demo](docs/demo.gif)

Built and used on Arch + qtile + picom, Python 3.14.

## Modes

One key per mode, no chord in the way. Any of them works from inside another.

| Key | Mode | |
|---|---|---|
| `alt+space` | hint | label every clickable element and every other window |
| `alt+j` | scroll | drive the region under the pointer with vim keys |
| `alt+/` | search | type to filter, digits pick |
| `alt+c` | caret | a real text cursor, vim motions, yank and put |
| `alt+shift+c` | caret search | type to find a word, land the caret on it |
| `alt+e` | edit | open a field in nvim, `:wq` writes it back |

`alt` rather than `shift`, because grabbing `shift+<key>` globally would
swallow it everywhere you type.

**Hint** — `a`–`l` types a label; any other letter filters and relabels the
survivors. `Shift`/`Ctrl`+label for new/background tab, `,` then a label for
right-click, `.` for middle-click, `Backspace` to undo. `Esc` clears what you
typed before it clears the filter, and only then leaves.

**Scroll** — `j`/`k` line, `d`/`u` half page, `gg`/`G` top/bottom, `h`/`l`
sideways where content overflows, counts like `3j`, `Tab` for the next region.

**Search** — letters filter, `1`–`9` pick, `Tab` cycles, `Enter` clicks.
Labels are digits so every letter stays usable in the query.

**Caret** — `hjkl` `w` `b` `e` `0` `$` `gg` `G` to move, `v`/`V` to select,
`y`/`yy` yank, `x`/`d`/`dd` delete, `p` put, `1`–`9`/`Tab` to jump between
text blocks, `/` to reopen caret search, `i` to open the field in nvim at the
cursor. In apps with their own caret mode — qutebrowser, Firefox — it enters
theirs and says so.

**Edit** — pick a field, it opens in your nvim in a borderless window sized to
the field, growing as you type. `:wq` writes back, `:q!` doesn't. Nothing to
add to your nvim config.

## Install

```sh
# Arch; the names differ elsewhere, the libraries do not
sudo pacman -S at-spi2-core python-gobject libx11 libxtst \
               xdotool xclip openbsd-netcat vte3
```

`vte3` is edit mode only and imported lazily — without it the other five modes
still work. Nothing to build.

```sh
git clone https://github.com/Mohamedattiadev/Hintium ~/hintium
~/hintium/bin/hintium --doctor
```

`--doctor` is the install instructions in executable form: it checks every
library and command, whether your session publishes an accessibility tree at
all, and whether your browsers have the flag they need — and prints the fix
for each thing it finds. Run it first whenever a mode sees nothing.

**Bind the modes** in your window manager. qtile:

```python
HINTIUM = os.path.expanduser("~/hintium/bin/hintium")
Key([mod2], "space",        lazy.spawn(HINTIUM)),
Key([mod2], "j",            lazy.spawn(HINTIUM + " --scroll")),
Key([mod2], "slash",        lazy.spawn(HINTIUM + " --search")),
Key([mod2], "c",            lazy.spawn(HINTIUM + " --caret")),
Key([mod2, "shift"], "c",   lazy.spawn(HINTIUM + " --caret-search")),
Key([mod2], "e",            lazy.spawn(HINTIUM + " --edit")),
```

That is the only file of yours hintium asks you to touch.

**Start the daemon at login** from your autostart, or with the shipped unit:

```sh
cp ~/hintium/contrib/hintium.service ~/.config/systemd/user/
systemctl --user enable --now hintium
```

If it is not running the client starts it and retries, so nothing breaks when
you forget.

**Chromium and Electron need a flag.** Add `--force-renderer-accessibility` to
`~/.config/brave-flags.conf`, `~/.config/code-flags.conf`,
`~/.config/obsidian/user-flags.conf` — each app reads its own — and restart it.
GTK apps need
`gsettings set org.gnome.desktop.interface toolkit-accessibility true`.

## Commands

`bin/hintium` is a shell script that writes one line to a unix socket, because
starting Python on a keypress cost more than all the real work combined (35ms
through the shell, 645ms through `python3`). It understands the six mode flags
and hands everything else to `hintium-cli`:

```sh
hintium                  # hint and click
hintium --edit           # …and the other five modes
hintium --status         # is a daemon answering?
hintium --restart        # what makes an edit under hintium/ take effect
hintium --log 40         # tail $XDG_STATE_HOME/hintium/hintium.log
hintium --doctor         # check the install
hintium --list           # print what would be hinted, draw nothing
hintium --help           # everything else
```

## Configure

```sh
hintium --write-config   # ~/.config/hintium/config.yaml, fully commented
hintium --show-config    # every setting and its value right now
hintium --check-config   # what it read, and what it could not use
```

The file layers over the built-in defaults, so it only names what you change:

```yaml
hint:
  alphabet: "arstdhneio"        # colemak home row
  windows: false                # stop labelling other windows
scroll:
  keys: {e: down, y: up}        # writing the map replaces it wholesale
edit:
  editor: "hx"
theme:
  preset: nord                  # or leave it out to follow your desktop theme
  colors: {purple: "#ff8800"}   # laid over whichever won
idle_timeout_s: 20
```

Keys are the names from `hintium/config.py`, lowercased, grouped by their
shared prefix or written flat (`hint_alphabet:`) — both work. Anything named
there is settable and nothing else is.

**A config file cannot break your desktop.** An unknown key, a wrong type, an
empty alphabet, a file that is not YAML at all — each is reported in the log
and the built-in default stands. PyYAML is used if you have it; a small reader
for the subset needed stands in if you do not.

Colours come from your desktop theme (`theme-apply`, then pywal) and are
re-read on every press, so a theme switch lands without a restart. Naming a
`preset:` pins it instead — `hintium --list-themes` shows all of them. Chip
ink is chosen by measuring contrast against the chip, so light themes stay
readable.

## Coverage

| App | Result |
|---|---|
| Brave / Chromium | full, with the flag |
| Firefox, qutebrowser | full chrome and page |
| VS Code, Obsidian | full, with the flag |
| Kate, Dolphin, pcmanfm-qt | full, including file lists |
| pcmanfm (GTK) | chrome only — its folder view publishes nothing |
| terminals, VLC | nothing to hint; switching and scrolling still work |

Measured over 15 real sites: 91 hints per page at ~200ms, 1.9 scroll regions,
0.9% of hints without a name.

## Limits

- **AT-SPI is uneven.** macOS AX is one API every app implements; this is
  per-toolkit. Qt WebEngine publishes no text, so caret mode hands off to the
  browser's own. pcmanfm publishes no file list.
- **A page's tree lags its rendering.** Press immediately after a load and you
  may get nothing.
- **JS-heavy apps expose invisible elements** as showing, with real
  coordinates and names.

## Tests

```sh
python3 -m unittest discover -s tests
```

`test_pure.py` covers the algorithms (label generation, ranking, dedup,
overflow detection); `test_rules.py` covers the decisions (which window counts
as focused, what is worth offering, how a match is graded) plus structural
checks — every `connect()` handler must exist, every module referenced must be
imported, and `contrib/config.yaml` is uncommented and loaded for real through
both YAML readers so the shipped example cannot go stale.

## More

[docs/design-notes.md](docs/design-notes.md) is the long version: why each
mode behaves the way it does, and the measurements behind it.

## Licence

MIT.
