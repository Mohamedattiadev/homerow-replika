"""Layer ~/.config/homerow/config.yaml over the defaults in config.py.

config.py stays the schema: every name that exists there can be set here,
nothing else can, and the default's *type* is what a supplied value is checked
against. That is the whole reason there is no separate schema file to keep in
step with it -- a setting added to config.py is configurable the moment it is
written, and one removed from it stops being configurable the same moment.

Keys are the names from config.py, lowercased, and may be nested under the
prefix they share::

    hint:
      alphabet: "asdfghjkl"     # HINT_ALPHABET
      windows: false            # HINT_WINDOWS
    edit:
      editor: "nvim"            # EDIT_EDITOR
    theme:
      chip_slot: "blue"         # CHIP_SLOT -- no THEME_ prefix exists, so the
                                # section name is dropped rather than demanded

A leaf under section `a` is looked up as `A_KEY` first and then as `KEY`, so
grouping is a convenience for the reader and never something to get right.
Flat top-level keys work too.

**Nothing here may crash the daemon.** A missing file, a file that is not
YAML, a key that does not exist, a string where a number belongs -- each is
reported as a problem and the built-in default stands. A configuration file is
edited by hand, usually in a hurry, and a desktop whose keyboard control dies
because of a stray tab is worse than one that ignores the line.
"""

import os

from homerow import config

# Names in config.py that are computed from other names. Overriding an
# ingredient has to recompute them or the override half-lands: setting
# actionable_roles would leave HINT_ROLES holding the old list, and hinting
# reads HINT_ROLES.
DERIVED = {
    "HINT_ROLES": lambda c: list(c.ACTIONABLE_ROLES) + list(c.CONTAINER_ROLES),
    "CARET_SEARCH_LABELS": lambda c: c.SEARCH_LABELS,
    "CARET_SEARCH_MIN_QUERY": lambda c: c.SEARCH_MIN_QUERY,
    "EDIT_COMPACT_SETTINGS": lambda c: (
        f"autocmd VimEnter * ++once silent! {c.EDIT_COMPACT_SET}"),
}

# Set once, from the defaults, before anything is layered over them -- so
# reloading a config file twice does not compound, and so --show-config can
# say what shipped as well as what is in force.
_defaults = {}


def defaults():
    """The values config.py shipped with, whatever has been applied since."""
    if not _defaults:
        _defaults.update(
            {name: getattr(config, name) for name in dir(config)
             if name.isupper() and not name.startswith("_")})
    return _defaults


def config_path(explicit=None):
    """Where the configuration file lives.

    XDG_CONFIG_HOME is honoured because someone who sets it means it; the
    fallback is the same path everyone else's is under.
    """
    if explicit:
        return os.path.abspath(os.path.expanduser(explicit))
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "homerow", "config.yaml")


# --- reading YAML ----------------------------------------------------------
# PyYAML when it is installed, and a reader for the subset this file needs
# when it is not. Adding a hard dependency for one small file would be the
# wrong trade for a tool whose install is "clone it": the shipped example uses
# nothing but nested maps, scalars and lists, and that is a subset worth 80
# lines. Where PyYAML *is* present it is used, so anchors, multi-line strings
# and anything else a user writes work as they expect.

def _scalar(raw):
    """One YAML scalar, in the subset the fallback reader supports."""
    text = raw.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        return text[1:-1]
    lowered = text.lower()
    if lowered in ("true", "yes", "on"):
        return True
    if lowered in ("false", "no", "off"):
        return False
    if lowered in ("null", "~", ""):
        return None
    for cast in (int, float):
        try:
            return cast(text)
        except ValueError:
            pass
    return text


def _strip_comment(line):
    """Drop a trailing `# ...`, unless it is inside quotes."""
    quote = None
    for index, char in enumerate(line):
        if quote:
            if char == quote:
                quote = None
        elif char in ("'", '"'):
            quote = char
        elif char == "#" and (index == 0 or line[index - 1] in " \t"):
            return line[:index]
    return line


def _inline_list(text):
    inner = text.strip()[1:-1].strip()
    if not inner:
        return []
    return [_scalar(part) for part in inner.split(",")]


def _inline_map(text, line_number):
    """`{}` and `{a: 1, b: 2}` on one line.

    --show-config writes an empty mapping this way, so reading it back is not
    optional: a file this project prints has to be a file it can load.
    """
    inner = text.strip()[1:-1].strip()
    if not inner:
        return {}
    out = {}
    for part in inner.split(","):
        key, sep, value = part.partition(":")
        if not sep:
            raise ValueError(
                f"line {line_number}: `{part.strip()}` is not `key: value`")
        out[key.strip().strip("'\"")] = _scalar(value.strip())
    return out


def _parse_simple(text):
    """Nested maps, block and inline lists, scalars. Raises ValueError.

    Deliberately small: it reads the file this project ships and the shapes
    people write by copying it. Anything it cannot make sense of is an error
    rather than a guess, because a silently misread setting is worse than a
    reported one.
    """
    root = {}
    # (indent, container, parent, key-in-parent) innermost last. The parent is
    # kept because `key:` with nothing after it could still turn out to be a
    # list -- which the *next* line is what decides.
    stack = [(-1, root, None, None)]
    for number, original in enumerate(text.splitlines(), 1):
        line = _strip_comment(original).rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        body = line.strip()
        while len(stack) > 1 and indent <= stack[-1][0]:
            stack.pop()
        _, container, parent, key_here = stack[-1]

        if body.startswith("- "):
            if isinstance(container, dict) and not container and parent:
                container = parent[key_here] = []
                stack[-1] = (stack[-1][0], container, parent, key_here)
            if not isinstance(container, list):
                raise ValueError(f"line {number}: a list item outside a list")
            container.append(_scalar(body[2:]))
            continue
        if ":" not in body:
            raise ValueError(f"line {number}: expected `key: value`")
        key, _, value = body.partition(":")
        key, value = key.strip().strip("'\""), value.strip()
        if not key:
            raise ValueError(f"line {number}: empty key")
        if not isinstance(container, dict):
            raise ValueError(f"line {number}: a key inside a list")
        if value.startswith("[") and value.endswith("]"):
            container[key] = _inline_list(value)
        elif value.startswith("{") and value.endswith("}"):
            container[key] = _inline_map(value, number)
        elif value == "":
            # A map or a list follows; which one the next line decides.
            child = {}
            container[key] = child
            stack.append((indent, child, container, key))
        else:
            container[key] = _scalar(value)
    return root


def _empty_sections(node):
    """`key:` with nothing under it, from either reader, as an empty mapping.

    PyYAML reads a bare `key:` as None and the fallback reads it as {}. They
    have to agree, and a section whose settings are all commented out is the
    normal state of a config file copied from the shipped example -- so it is
    the shape that has to be harmless, not the one that has to be diagnosed.
    """
    if isinstance(node, dict):
        return {key: {} if value is None else _empty_sections(value)
                for key, value in node.items()}
    return node


def parse(text):
    """Parse YAML text into a tree. Raises ValueError with a readable reason."""
    try:
        import yaml
    except ImportError:
        return _empty_sections(_parse_simple(text))
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise ValueError(str(error).replace("\n", " ")) from error
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ValueError("the file must be a mapping of settings, "
                         f"not {type(loaded).__name__}")
    return _empty_sections(loaded)


# --- turning a tree into settings ------------------------------------------

def resolve(path):
    """The config.py name a nested key means, or None if there is no such one.

    `["edit", "border"]` is EDIT_BORDER; `["theme", "chip_slot"]` is CHIP_SLOT,
    because no THEME_ prefix exists and the section is then only grouping.
    """
    joined = "_".join(part.upper() for part in path)
    if joined in defaults():
        return joined
    if len(path) > 1:
        tail = "_".join(part.upper() for part in path[1:])
        if tail in defaults():
            return tail
    return None


def flatten(tree, path=()):
    """Walk the tree, yielding (path, value) for every leaf.

    A nested map stops being walked as soon as its path names a real setting,
    so a setting whose own value is a mapping would arrive whole. None of them
    are today; doing it this way means one added later needs no change here.
    """
    for key, value in tree.items():
        here = (*path, str(key))
        if isinstance(value, dict) and resolve(here) is None:
            yield from flatten(value, here)
        else:
            yield here, value


def check(name, value):
    """Coerce `value` for setting `name`, or say what is wrong with it.

    Returns (value, None) or (None, problem). The default's type is the
    contract -- there is no separate schema, see the module docstring.
    """
    default = defaults()[name]
    kind = type(default)

    if isinstance(default, bool):
        if not isinstance(value, bool):
            return None, f"{name}: expected true or false, got {value!r}"
        return value, None

    if isinstance(default, int):
        if isinstance(value, bool) or not isinstance(value, int):
            return None, f"{name}: expected a whole number, got {value!r}"
    elif isinstance(default, float):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None, f"{name}: expected a number, got {value!r}"
        value = float(value)
    elif isinstance(default, str):
        if not isinstance(value, str):
            return None, f"{name}: expected text, got {value!r}"
    elif isinstance(default, (list, tuple, set, frozenset)):
        if isinstance(value, str) or not isinstance(value, (list, tuple, set)):
            return None, f"{name}: expected a list, got {value!r}"
        value = kind(value)
    elif default is not None and not isinstance(value, kind):
        return None, (f"{name}: expected {kind.__name__}, "
                      f"got {type(value).__name__}")

    # Two rules beyond the type, both of them "it defaulted to something
    # usable, so an unusable value is a mistake rather than a preference".
    # A zero timeout or an empty label alphabet does not configure a mode
    # differently, it stops the mode working at all.
    if isinstance(default, (int, float)) and not isinstance(default, bool):
        if default > 0 and value <= 0:
            return None, f"{name}: must be greater than 0, got {value!r}"
    if default and isinstance(default, (str, list, tuple, set, frozenset)):
        if not value:
            return None, f"{name}: must not be empty"
    return value, None


class Result:
    """What a load did: what it applied, what it could not, and from where."""

    def __init__(self, path):
        self.path = path
        self.applied = {}
        self.problems = []
        self.found = False

    @property
    def ok(self):
        return not self.problems

    def summary(self):
        if not self.found:
            return f"no config file at {self.path}; using the built-in defaults"
        settings = f"{len(self.applied)} setting(s) from {self.path}"
        if self.problems:
            return f"{settings}; {len(self.problems)} ignored"
        return settings


def apply(tree, module=config):
    """Layer a parsed tree over `module`. Returns (applied, problems)."""
    applied, problems = {}, []
    for path, value in flatten(tree):
        name = resolve(path)
        if value == {} and not isinstance(value, (list, tuple, set)):
            # A section with every setting under it commented out, or a key
            # with nothing after it. Neither asks for anything -- unless the
            # setting it names genuinely takes a mapping, where an empty one
            # is a real value meaning "override nothing" (theme.colors is
            # written that way by --show-config, and has to read back).
            if name is not None and isinstance(defaults().get(name), dict):
                checked, problem = check(name, value)
                if problem is None:
                    setattr(module, name, checked)
                    applied[name] = checked
                    continue
            if name is not None:
                problems.append(f"{name}: no value given")
            continue
        if name is None:
            problems.append(f"{'.'.join(path)}: no such setting")
            continue
        if name in DERIVED:
            problems.append(f"{'.'.join(path)}: {name} is computed from other "
                            f"settings and cannot be set directly")
            continue
        checked, problem = check(name, value)
        if problem:
            problems.append(problem)
            continue
        setattr(module, name, checked)
        applied[name] = checked
    if applied:
        for name, recompute in DERIVED.items():
            setattr(module, name, recompute(module))
    return applied, problems


def reset(module=config):
    """Put every setting back to what config.py shipped. For tests, and for
    a second load in one process not compounding on the first."""
    for name, value in defaults().items():
        setattr(module, name, value)


def load(explicit=None, module=config):
    """Read the config file and layer it over the defaults.

    Never raises. Every failure is a line in the result's problems and a
    default left standing -- see the module docstring for why.
    """
    result = Result(config_path(explicit))
    reset(module)
    try:
        with open(result.path, encoding="utf-8") as handle:
            text = handle.read()
    except FileNotFoundError:
        if explicit:
            result.problems.append(f"no such file: {result.path}")
        return result
    except OSError as error:
        result.problems.append(f"could not read {result.path}: {error}")
        return result

    result.found = True
    try:
        tree = parse(text)
    except ValueError as error:
        result.problems.append(f"{result.path} is not valid YAML: {error}")
        return result
    if not isinstance(tree, dict):
        result.problems.append(f"{result.path}: expected a mapping of settings")
        return result

    result.applied, result.problems = apply(tree, module)
    return result


def effective(module=config):
    """Every setting and its value right now, for --show-config."""
    return {name: getattr(module, name) for name in sorted(defaults())}


def _yaml_scalar(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return repr(value)
    text = str(value)
    # Quote anything that would come back as something other than itself.
    if text == "" or text != text.strip() or _scalar(text) != text:
        return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if any(char in text for char in ":#[]{}&*!|>%@`,"):
        return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return text


def dump(settings, changed=()):
    """Settings as YAML this same module can read back.

    Written flat and lowercase rather than grouped: it is the form with no
    ambiguity about which setting a line means, and `--show-config >
    config.yaml` has to produce a file that loads.

    Computed settings are shown, because someone reading this wants to see
    what is in force, but commented out -- setting one directly is refused,
    and printing it as though it were an option would be printing a lie.
    """
    lines = []
    for name in sorted(settings):
        value = settings[name]
        key = name.lower()
        if name in DERIVED:
            lines.append(f"# {key}: {_flow(value)}"
                         "   # computed; set what it is made of instead")
            continue
        mark = "" if name not in changed else "   # set by your config file"
        if isinstance(value, dict):
            if not value:
                lines.append(f"{key}: {{}}{mark}")
                continue
            lines.append(f"{key}:{mark}")
            lines.extend(f"  {inner}: {_yaml_scalar(setting)}"
                         for inner, setting in sorted(value.items()))
        elif isinstance(value, (list, tuple, set, frozenset)):
            items = (sorted(value) if isinstance(value, (set, frozenset))
                     else list(value))
            if not items:
                lines.append(f"{key}: []{mark}")
                continue
            lines.append(f"{key}:{mark}")
            lines.extend(f"  - {_yaml_scalar(item)}" for item in items)
        else:
            lines.append(f"{key}: {_yaml_scalar(value)}{mark}")
    return "\n".join(lines) + "\n"


def _flow(value):
    """One line, for a computed setting shown in a comment."""
    if isinstance(value, (list, tuple, set, frozenset)):
        items = (sorted(value) if isinstance(value, (set, frozenset))
                 else list(value))
        return "[" + ", ".join(_yaml_scalar(item) for item in items) + "]"
    return _yaml_scalar(value)
