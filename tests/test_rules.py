"""Tests for the decision rules, as opposed to the algorithms in test_pure.

Everything here answers "should this be offered at all" -- which window counts
as focused, which window is worth switching to, how a match is graded. These
rules are where the surprising bugs came from: a scratchpad two pixels on
screen being treated as the focused app, the desktop being treated as an app,
an exact match ranked below a substring.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from homerow import config, elements, hints, search, windows  # noqa: E402


class MatchGrading(unittest.TestCase):
    """search._score: lower is better, None means no match."""

    def grade(self, haystack, query):
        return search._score(haystack, query.lower().split())

    def test_exact_beats_word_beats_prefix_beats_interior(self):
        self.assertEqual(self.grade("vite", "vite"), 0)
        self.assertEqual(self.grade("vite link", "vite"), 1)
        self.assertEqual(self.grade("vitest", "vite"), 2)
        self.assertEqual(self.grade("a vitest thing", "vite"), 3)
        self.assertEqual(self.grade("webgl invite", "vite"), 4)

    def test_non_match_is_none(self):
        self.assertIsNone(self.grade("nothing here", "vite"))

    def test_all_terms_required(self):
        self.assertIsNone(self.grade("save button", "save file"))
        self.assertIsNotNone(self.grade("save file button", "save file"))

    def test_grading_is_case_insensitive_on_prepared_input(self):
        # Callers lowercase the haystack; the query is lowercased internally.
        self.assertEqual(self.grade("templates", "TEMPLATES"), 0)


class ActiveWindowRule(unittest.TestCase):
    """elements._mostly_on_screen keeps hidden scratchpads from counting."""

    def setUp(self):
        self._real = elements.x11.screen_size
        elements.x11.screen_size = lambda: (1366, 768)

    def tearDown(self):
        elements.x11.screen_size = self._real

    def test_ordinary_window_counts(self):
        self.assertTrue(elements._mostly_on_screen((0, 38, 1366, 730)))

    def test_scratchpad_parked_above_the_top_edge_does_not(self):
        # qdrop: 359 tall at y=-357, so two pixels are visible.
        self.assertFalse(elements._mostly_on_screen((374, -357, 622, 359)))

    def test_mostly_visible_window_still_counts(self):
        self.assertTrue(elements._mostly_on_screen((0, -40, 1366, 730)))

    def test_window_entirely_off_screen_does_not(self):
        self.assertFalse(elements._mostly_on_screen((2000, 100, 400, 300)))

    def test_missing_screen_size_is_permissive(self):
        # Better to offer a window than to refuse everything if the query fails.
        elements.x11.screen_size = lambda: None
        self.assertTrue(elements._mostly_on_screen((374, -357, 622, 359)))


class SwitchTargetRule(unittest.TestCase):
    """windows.is_offerable decides what appears as a switch hint."""

    def offer(self, x, y, w, h):
        return windows.is_offerable(x, y, w, h, 1366, 768)

    def test_normal_window_is_offered(self):
        self.assertTrue(self.offer(0, 38, 800, 700))

    def test_tiny_window_is_not(self):
        # Tray icons register as 10x10 windows.
        self.assertFalse(self.offer(0, 0, 10, 10))

    def test_window_on_another_workspace_is_not(self):
        self.assertFalse(self.offer(-2000, 0, 800, 700))
        self.assertFalse(self.offer(0, 900, 800, 700))

    def test_partially_visible_window_is_offered(self):
        self.assertTrue(self.offer(-100, 38, 800, 700))

    def test_boundary_exactly_offscreen_is_not(self):
        self.assertFalse(self.offer(1366, 0, 800, 700))


class LabelAlphabet(unittest.TestCase):
    def test_respects_a_custom_alphabet(self):
        labels = hints.generate(4, alphabet="xy")
        self.assertEqual(len(labels), 4)
        self.assertTrue(all(set(label) <= set("xy") for label in labels))

    def test_rejects_an_unusable_alphabet(self):
        # One character cannot produce prefix-free labels of differing length.
        with self.assertRaises(ValueError):
            hints.generate(5, alphabet="a")

    def test_zero_elements_is_empty(self):
        self.assertEqual(hints.generate(0), [])

    def test_alphabet_boundary_uses_one_character(self):
        size = len(config.HINT_ALPHABET)
        self.assertTrue(all(len(x) == 1 for x in hints.generate(size)))
        self.assertGreater(max(len(x) for x in hints.generate(size + 1)), 1)


class SearchLabelConfig(unittest.TestCase):
    def test_labels_are_digits_only(self):
        # Letters have to stay usable in the query; a letter label would make
        # every keystroke ambiguous, which is the bug this design avoids.
        self.assertTrue(config.SEARCH_LABELS.isdigit())

    def test_labels_do_not_overlap_the_hint_alphabet(self):
        self.assertFalse(
            set(config.SEARCH_LABELS) & set(config.HINT_ALPHABET))

    def test_outline_budget_covers_every_label(self):
        # Every numbered match must actually be drawn, or a number would refer
        # to something invisible.
        self.assertGreaterEqual(
            config.SEARCH_MAX_OUTLINES, len(config.SEARCH_LABELS))




class SignalHandlersExist(unittest.TestCase):
    """Every widget.connect(...) must name a method that exists.

    A session once connected "visibility-notify-event" to a handler that a
    botched edit never inserted. The constructor raised, so no session ever
    opened -- scroll appeared to do nothing and Escape had nothing to close.
    Python cannot catch that at import time, but reading the source can.
    """

    def test_connected_handlers_are_defined(self):
        import ast
        import pathlib

        root = pathlib.Path(__file__).resolve().parent.parent / "homerow"
        missing = []
        for path in sorted(root.glob("*.py")):
            tree = ast.parse(path.read_text())
            for klass in [n for n in ast.walk(tree)
                          if isinstance(n, ast.ClassDef)]:
                defined = {n.name for n in klass.body
                           if isinstance(n, ast.FunctionDef)}
                for call in [n for n in ast.walk(klass)
                             if isinstance(n, ast.Call)]:
                    func = call.func
                    if not (isinstance(func, ast.Attribute)
                            and func.attr == "connect"):
                        continue
                    for arg in call.args[1:]:
                        # self._handler -- the form that can silently dangle.
                        if (isinstance(arg, ast.Attribute)
                                and isinstance(arg.value, ast.Name)
                                and arg.value.id == "self"
                                and arg.attr not in defined):
                            missing.append(
                                f"{path.name}:{klass.name}.{arg.attr}")
        self.assertEqual(missing, [], f"connected but undefined: {missing}")

class ModuleNamesResolve(unittest.TestCase):
    """Every sibling module used must actually be imported.

    caret.py called x11.release_modifiers() without importing x11, so closing
    a session raised before destroying its window -- Escape looked dead. The
    call sat on an error path, which is exactly where a NameError hides.
    """

    def test_no_module_is_used_without_being_imported(self):
        import ast
        import pathlib

        root = pathlib.Path(__file__).resolve().parent.parent / "homerow"
        siblings = {p.stem for p in root.glob("*.py")} - {"__init__"}
        offenders = []
        for path in sorted(root.glob("*.py")):
            tree = ast.parse(path.read_text())
            imported = {alias.name for node in ast.walk(tree)
                        if isinstance(node, ast.ImportFrom)
                        for alias in node.names}
            imported |= {alias.name.split(".")[0] for node in ast.walk(tree)
                         if isinstance(node, ast.Import)
                         for alias in node.names}
            for node in ast.walk(tree):
                if (isinstance(node, ast.Attribute)
                        and isinstance(node.value, ast.Name)
                        and node.value.id in siblings
                        and node.value.id != path.stem
                        and node.value.id not in imported):
                    offenders.append(f"{path.name} uses {node.value.id}")
        self.assertEqual(sorted(set(offenders)), [])


class LayoutContainerRule(unittest.TestCase):
    """elements._encloses feeds the rule that drops wrapper elements.

    A page wrapper enclosing the whole content area was being hinted, so its
    chip landed in whatever empty corner the wrapper started at -- one of the
    "hints in empty space" cases.
    """

    def box(self, x, y, w, h):
        from homerow.elements import Element
        return Element(None, x, y, w, h)

    def test_wrapper_encloses_its_children(self):
        from homerow import elements
        outer = self.box(200, 100, 950, 580)
        inner = self.box(220, 120, 40, 40)
        self.assertTrue(elements._encloses(outer, inner))
        self.assertFalse(elements._encloses(inner, outer))

    def test_equal_boxes_do_not_enclose_each_other(self):
        from homerow import elements
        a, b = self.box(0, 0, 100, 100), self.box(0, 0, 100, 100)
        self.assertFalse(elements._encloses(a, b))

    def test_overlapping_but_not_containing_is_not_enclosing(self):
        from homerow import elements
        a, b = self.box(0, 0, 100, 100), self.box(50, 50, 100, 100)
        self.assertFalse(elements._encloses(a, b))

    def test_threshold_keeps_a_button_holding_one_icon(self):
        # A button wrapping a single icon must survive; only boxes holding
        # several targets are layout.
        self.assertGreaterEqual(config.CONTAINER_MIN_CHILDREN, 2)


class SearchOverlapRule(unittest.TestCase):
    """service._overlaps stops a link and its own text both being numbered."""

    def box(self, x, y, w, h):
        from homerow.elements import Element
        return Element(None, x, y, w, h)

    def setUp(self):
        from homerow import service
        self.overlaps = service._overlaps

    def test_text_inside_its_link_is_the_same_target(self):
        link = self.box(100, 100, 200, 20)
        text = self.box(102, 102, 190, 16)
        self.assertTrue(self.overlaps(text, link))

    def test_separate_targets_are_not(self):
        self.assertFalse(self.overlaps(self.box(0, 0, 50, 20),
                                       self.box(300, 300, 50, 20)))

    def test_touching_edges_are_not(self):
        self.assertFalse(self.overlaps(self.box(0, 0, 50, 20),
                                       self.box(50, 0, 50, 20)))

    def test_small_target_inside_a_huge_one_still_counts_as_same(self):
        # The smaller is entirely covered, so numbering both is a duplicate.
        self.assertTrue(self.overlaps(self.box(10, 10, 10, 10),
                                      self.box(0, 0, 500, 500)))


class NestedScrollerRule(unittest.TestCase):
    """scroll._same_scroller collapses a pane and the column inside it.

    Reported case: a Google Developers page offered the viewport pane and the
    content column inside it, which scroll together. Sibling regions -- a
    sidebar next to a content pane -- must survive, because those really are
    two scrollers.
    """

    def region(self, x, y, w, h):
        from homerow.elements import Element
        return Element(None, x, y, w, h)

    def test_content_column_inside_viewport_is_one_scroller(self):
        from homerow import scroll
        viewport = self.region(0, 118, 1366, 650)
        column = self.region(396, 187, 766, 560)
        self.assertTrue(scroll._same_scroller(viewport, column))

    def test_sidebar_and_content_pane_stay_separate(self):
        from homerow import scroll
        sidebar = self.region(0, 65, 336, 672)
        content = self.region(334, 65, 1033, 632)
        self.assertFalse(scroll._same_scroller(sidebar, content))

    def test_a_small_widget_inside_a_pane_survives(self):
        from homerow import scroll
        pane = self.region(0, 0, 1000, 800)
        widget = self.region(100, 100, 200, 150)
        self.assertFalse(scroll._same_scroller(pane, widget))


class WindowScopeRule(unittest.TestCase):
    """elements.active_frame keeps other windows of the same app out.

    Brave reported six frames, three of them sharing one rectangle, so
    clipping results to the focused window's geometry let three windows'
    worth of hints through at once.
    """

    class Frame:
        def __init__(self, x, y, w, h, active=False, showing=True):
            self.rect = (x, y, w, h)
            self.active, self.showing = active, showing
            self.x, self.y, self.width, self.height = x, y, w, h

        def get_state_set(self):
            return self

        def contains(self, state):
            import gi
            gi.require_version("Atspi", "2.0")
            from gi.repository import Atspi
            if state == Atspi.StateType.ACTIVE:
                return self.active
            if state == Atspi.StateType.SHOWING:
                return self.showing
            return False

        def get_component_iface(self):
            return self

        def get_extents(self, _coord):
            return self

    class App:
        def __init__(self, frames):
            self.frames = frames

        def get_child_count(self):
            return len(self.frames)

        def get_child_at_index(self, index):
            return self.frames[index]

    def test_active_frame_wins_outright(self):
        from homerow import elements
        wanted = self.Frame(0, 38, 1366, 730, active=True)
        app = self.App([self.Frame(0, 38, 1366, 730), wanted,
                        self.Frame(0, 38, 1366, 730)])
        self.assertIs(elements.active_frame(app, (0, 38, 1366, 730)), wanted)

    def test_geometry_decides_when_none_is_marked_active(self):
        from homerow import elements
        wanted = self.Frame(204, 111, 952, 580)
        app = self.App([self.Frame(0, 38, 1366, 730), wanted])
        self.assertIs(elements.active_frame(app, (204, 111, 952, 580)), wanted)

    def test_single_frame_needs_no_scoping(self):
        from homerow import elements
        app = self.App([self.Frame(0, 38, 1366, 730)])
        self.assertIsNone(elements.active_frame(app, (0, 38, 1366, 730)))

    def test_no_close_match_falls_back_to_the_whole_app(self):
        from homerow import elements
        app = self.App([self.Frame(0, 0, 100, 100),
                        self.Frame(500, 500, 100, 100)])
        self.assertIsNone(elements.active_frame(app, (0, 38, 1366, 730)))

    def test_hidden_frames_are_ignored(self):
        from homerow import elements
        visible = self.Frame(0, 38, 1366, 730)
        app = self.App([self.Frame(0, 38, 1366, 730, showing=False), visible])
        self.assertIs(elements.active_frame(app, (0, 38, 1366, 730)), visible)


class WorkspaceWatch(unittest.TestCase):
    """A mode must not survive a workspace switch.

    Everything it knows -- scrollable regions, hint positions, the caret's text
    block -- describes the window that was in front when it opened, and the
    overlay keeps the keyboard grabbed over whatever is in front now.
    """

    def daemon(self, desktop, overlay=None):
        import unittest.mock

        from homerow import service
        instance = object.__new__(service.Daemon)
        instance.debug = False
        instance._desktop = desktop
        instance._desktop_watch = None
        instance.log = None
        # spec, so an overlay that has no close_for_workspace really has none:
        # a bare Mock invents every attribute asked of it, which would make
        # every session look like an editor here.
        instance.overlay = (overlay if overlay is not None
                            else unittest.mock.Mock(spec=["dismiss"]))
        return instance

    def test_a_changed_workspace_dismisses_the_overlay(self):
        import unittest.mock

        from homerow import service, x11
        instance = self.daemon(2)
        with unittest.mock.patch.object(x11, "current_desktop",
                                        return_value=5), \
             unittest.mock.patch.object(service.Daemon, "_clear_mode"):
            keep_going = instance._check_workspace()
        instance.overlay.dismiss.assert_called_once()
        self.assertFalse(keep_going)   # and stops polling

    def test_the_same_workspace_leaves_it_alone(self):
        import unittest.mock

        from homerow import x11
        instance = self.daemon(2)
        with unittest.mock.patch.object(x11, "current_desktop",
                                        return_value=2):
            keep_going = instance._check_workspace()
        instance.overlay.dismiss.assert_not_called()
        self.assertTrue(keep_going)

    def test_no_overlay_stops_the_watch(self):
        import unittest.mock

        from homerow import x11
        instance = self.daemon(2)
        instance.overlay = None
        with unittest.mock.patch.object(x11, "current_desktop",
                                        return_value=5) as read:
            self.assertFalse(instance._check_workspace())
        read.assert_not_called()

    def test_a_wm_that_publishes_no_desktop_is_not_treated_as_a_switch(self):
        import unittest.mock

        from homerow import x11
        instance = self.daemon(2)
        with unittest.mock.patch.object(x11, "current_desktop",
                                        return_value=None):
            self.assertTrue(instance._check_workspace())
        instance.overlay.dismiss.assert_not_called()


class InkIsReadable(unittest.TestCase):
    """Every ink has to be readable on the chip it is actually drawn on.

    Measured on this desktop's own theme: the window chip's ink was at
    2.24:1 and a legend's meanings at 2.49:1, both well under readable, which
    is the "some of the text is not visible" report. Both came from picking
    colours by thresholding luminance and then trusting the result -- so
    contrast, the thing actually being asked about, is now the thing measured.
    """

    PAIRS = [("chip", "ink", "ink_dim"),
             ("chip_matched", "ink_matched", "ink_dim_matched"),
             ("chip_window", "ink_window", "ink_dim_window")]

    def palettes(self):
        from homerow import theme
        # The live theme and the built-in fallback: the fallback is what a
        # desktop with no theme file gets, and it goes through the same path.
        return [("live", theme.palette()),
                ("fallback", theme._build(dict(theme.FALLBACK)))]

    def test_every_ink_clears_the_readable_floor_on_its_own_chip(self):
        from homerow import config, theme
        for name, palette in self.palettes():
            for chip, ink, dim in self.PAIRS:
                for key in (ink, dim):
                    with self.subTest(palette=name, text=key, chip=chip):
                        self.assertGreaterEqual(
                            theme._contrast(palette[key], palette[chip]),
                            config.INK_MIN_CONTRAST)

    def test_a_meaning_recedes_from_its_key_where_it_can_afford_to(self):
        from homerow import theme
        # The whole point of the fade is hierarchy. A floor that swallowed it
        # everywhere would be a legend with no hierarchy left.
        for name, palette in self.palettes():
            for chip, ink, dim in self.PAIRS:
                with self.subTest(palette=name, chip=chip):
                    self.assertLessEqual(
                        theme._contrast(palette[dim], palette[chip]),
                        theme._contrast(palette[ink], palette[chip]))

    def test_receding_backs_off_until_the_floor_is_met(self):
        from homerow import theme
        # A fade of 0.9 would be nearly invisible; it has to come back up.
        ink, chip = (0.0, 0.0, 0.0), (0.9, 0.9, 0.9)
        faded = theme._recede(ink, chip, 0.9, 4.5)
        self.assertGreaterEqual(theme._contrast(faded, chip), 4.5)
        self.assertNotEqual(faded, ink)      # it did still recede

    def test_a_chip_that_cannot_afford_any_fade_gets_none(self):
        from homerow import theme
        # Black on mid-grey is 4.41:1 -- under the floor before any fade at
        # all. There is nothing better available, so the answer is the ink
        # itself rather than something worse in pursuit of a number that
        # cannot be reached.
        ink, chip = (0.0, 0.0, 0.0), (0.45, 0.45, 0.45)
        self.assertEqual(theme._recede(ink, chip, 0.9, 4.5), ink)

    def test_the_themes_own_colours_are_preferred_over_black_and_white(self):
        from homerow import theme
        # Black and white are the last resort. A theme whose foreground reads
        # perfectly well on a chip should get its foreground.
        palette = theme._build(dict(theme.FALLBACK, fg="#f0f0f0", bg="#101010"))
        self.assertNotIn(palette["ink"][:3], ((0.0, 0.0, 0.0), (1.0, 1.0, 1.0)))


class EditorLeavesOnWorkspaceChange(unittest.TestCase):
    """An editor closes with the rest, but is never closed empty-handed.

    It used to opt out of the workspace watch entirely, because it holds text
    the user typed and dismiss() is the close that throws that away. The cost
    was worse than the problem: the editor stayed open over a field on a
    workspace nobody was looking at, the bar kept saying "edit", and every
    other mode refused to open behind it.
    """

    def session(self, strategy, on_disk, sent="before", warm=False):
        import tempfile
        import unittest.mock

        from homerow import edit
        instance = object.__new__(edit.EditSession)
        instance.field = unittest.mock.Mock()
        instance.original = "before"
        instance._sent = sent
        instance._log = lambda _message: None
        instance.closed = False
        instance._dismissed = False
        instance.warm = warm
        instance._monitor = None
        instance.window = unittest.mock.Mock()
        instance.on_done = lambda: None
        instance.written = []
        instance.on_write = instance.written.append
        handle, instance.path = tempfile.mkstemp(prefix="homerow-test-")
        with os.fdopen(handle, "w", encoding="utf-8") as temp:
            temp.write(on_disk)
        self.addCleanup(lambda: os.path.exists(instance.path)
                        and os.unlink(instance.path))
        self._strategy = strategy
        return instance

    def leave(self, instance):
        import unittest.mock

        from homerow import edit
        with unittest.mock.patch.object(
                edit, "strategy", return_value=self._strategy), \
             unittest.mock.patch.object(edit, "stop_warm"), \
             unittest.mock.patch.object(edit, "start_warm"), \
             unittest.mock.patch.object(edit, "warm_save") as saved:
            kept = instance.close_for_workspace()
        return kept, saved

    def test_a_quiet_field_is_written_before_closing(self):
        from homerow import edit
        # AT-SPI can write this one directly, which needs no focus -- so the
        # text lands without dragging the user back to the workspace they
        # just left.
        instance = self.session(edit.EDITABLE_TEXT, "after")
        kept, _ = self.leave(instance)
        self.assertIsNone(kept)
        self.assertEqual(instance.written, ["after"])
        self.assertTrue(instance.closed)
        self.assertFalse(os.path.exists(instance.path))

    def test_a_field_needing_focus_keeps_the_text_on_disk(self):
        from homerow import edit
        # Writing this one means focusing its window and typing at it, which
        # would haul the user back. The buffer stays, and the daemon says so.
        instance = self.session(edit.PASTE, "after")
        kept, _ = self.leave(instance)
        self.assertEqual(kept, instance.path)
        self.assertEqual(instance.written, [])
        self.assertTrue(os.path.exists(instance.path))
        with open(instance.path, encoding="utf-8") as temp:
            self.assertEqual(temp.read(), "after")

    def test_an_unchanged_buffer_writes_nothing_and_keeps_nothing(self):
        from homerow import edit
        instance = self.session(edit.PASTE, "before")
        kept, _ = self.leave(instance)
        self.assertIsNone(kept)
        self.assertEqual(instance.written, [])
        self.assertFalse(os.path.exists(instance.path))

    def test_the_warm_server_is_asked_to_save_first(self):
        from homerow import edit
        # The file on disk is only as new as the last :w. Closing on a
        # workspace change must not take a stale copy and call it their work.
        instance = self.session(edit.EDITABLE_TEXT, "after", warm=True)
        _, saved = self.leave(instance)
        saved.assert_called_once()

    def test_the_editors_own_exit_cannot_write_again(self):
        from homerow import edit
        # Closing kills the editor, so child-exited fires on the way out.
        # That is not the user saving, and it must not write a second time.
        instance = self.session(edit.EDITABLE_TEXT, "after")
        self.leave(instance)
        self.assertTrue(instance._dismissed)

    def test_the_daemon_reports_a_kept_buffer(self):
        import unittest.mock

        from homerow import service, x11
        overlay = unittest.mock.Mock(spec=["close_for_workspace"])
        overlay.close_for_workspace.return_value = "/tmp/homerow-x.txt"
        instance = WorkspaceWatch.daemon(self, 2, overlay)
        with unittest.mock.patch.object(x11, "current_desktop",
                                        return_value=5), \
             unittest.mock.patch.object(service, "_notify") as told:
            instance._check_workspace()
        overlay.close_for_workspace.assert_called_once()
        self.assertIn("/tmp/homerow-x.txt", told.call_args.args[0])

    def test_escape_is_bound_to_write_and_close(self):
        from homerow import config
        # A one-key exit that discards is the worse mistake to make, so Esc
        # writes -- the same argument q is bound on.
        escapes = [m for m in config.EDIT_KEYMAPS if "<Esc>" in m]
        self.assertEqual(len(escapes), 1)
        self.assertIn(":wq", escapes[0])
        self.assertIn("<buffer>", escapes[0])


class ModeSwitching(unittest.TestCase):
    """One mode's hotkey, pressed inside another, switches.

    A mode holds the keyboard exclusively -- it has to, or its letters leak
    into the app underneath -- which also takes the other modes' hotkeys away
    from the window manager. So they arrive at the running mode instead, and
    it has to recognise them; otherwise alt+j in hint mode is just the letter
    j and switching costs an Esc first.
    """

    class Event:
        def __init__(self, keyval, state=0):
            self.keyval, self.state = keyval, state

    def alt(self, keyval, shift=False):
        from gi.repository import Gdk
        state = Gdk.ModifierType.MOD1_MASK
        if shift:
            state |= Gdk.ModifierType.SHIFT_MASK
        return self.Event(keyval, state)

    def switcher(self):
        import unittest.mock

        from homerow import overlay
        seen = unittest.mock.Mock()
        overlay.set_mode_switcher(seen)
        self.addCleanup(overlay.set_mode_switcher, None)
        return seen

    def test_a_mode_hotkey_asks_for_that_mode(self):
        from gi.repository import Gdk

        from homerow import overlay
        seen = self.switcher()
        self.assertTrue(overlay.mode_switch(self.alt(Gdk.KEY_j)))
        seen.assert_called_once_with("scroll")

    def test_shift_picks_the_other_caret_mode(self):
        from gi.repository import Gdk

        from homerow import overlay
        seen = self.switcher()
        overlay.mode_switch(self.alt(Gdk.KEY_c))
        overlay.mode_switch(self.alt(Gdk.KEY_C, shift=True))
        self.assertEqual([call.args[0] for call in seen.call_args_list],
                         ["caret", "caret_search"])

    def test_a_plain_letter_is_left_to_the_mode(self):
        from gi.repository import Gdk

        from homerow import overlay
        seen = self.switcher()
        # j scrolls, c is a hint label, e is a word motion. Requiring the
        # modifier is what keeps all of that reachable.
        for key in (Gdk.KEY_j, Gdk.KEY_c, Gdk.KEY_e, Gdk.KEY_space):
            self.assertFalse(overlay.mode_switch(self.Event(key)))
        seen.assert_not_called()

    def test_caps_lock_does_not_switch_modes(self):
        from gi.repository import Gdk

        from homerow import overlay
        # Caps Lock is bound as the launch modifier and is easy to leave on by
        # accident (see normalize_key). Honouring it here would turn every j
        # into a mode switch.
        seen = self.switcher()
        self.assertFalse(overlay.mode_switch(
            self.Event(Gdk.KEY_j, Gdk.ModifierType.LOCK_MASK)))
        seen.assert_not_called()

    def test_an_unbound_key_with_the_modifier_is_not_a_switch(self):
        from gi.repository import Gdk

        from homerow import overlay
        seen = self.switcher()
        self.assertFalse(overlay.mode_switch(self.alt(Gdk.KEY_z)))
        seen.assert_not_called()

    def test_without_a_daemon_the_keys_fall_through(self):
        from gi.repository import Gdk

        from homerow import overlay
        # The standalone CLI runs a mode with no daemon behind it, so there is
        # nothing to switch to and the key belongs to the mode.
        overlay.set_mode_switcher(None)
        self.assertFalse(overlay.mode_switch(self.alt(Gdk.KEY_j)))

    def test_every_switchable_mode_is_a_command_the_daemon_runs(self):
        from homerow import overlay, service
        # Structural: a typo in either table would be a hotkey that silently
        # does nothing, which is indistinguishable from the mode ignoring it.
        self.assertTrue(
            set(overlay.MODE_KEYS.values()) <= service._MODE_COMMANDS,
            set(overlay.MODE_KEYS.values()) - service._MODE_COMMANDS)

    def test_every_mode_reads_the_switch_keys(self):
        import inspect

        from homerow import caret, overlay, scroll, search
        # Any key handler that grabs the keyboard and does not check would be
        # a mode you cannot switch out of.
        handlers = [
            overlay.Overlay._on_key, scroll.ScrollSession._on_key,
            search.SearchPrompt._on_key, caret.CaretSession._on_key,
            caret.CaretSearchPrompt._on_key,
        ]
        for handler in handlers:
            with self.subTest(handler=handler.__qualname__):
                self.assertIn("mode_switch", inspect.getsource(handler))

    def test_the_switch_key_does_not_also_do_what_the_mode_does(self):
        import unittest.mock

        from gi.repository import Gdk

        from homerow import scroll
        # Driving the real handler, not just checking it mentions the switch:
        # alt+j must ask for scroll mode and must not ALSO be read as scroll
        # mode's own j, which would scroll the page on the way out.
        seen = self.switcher()
        session = object.__new__(scroll.ScrollSession)
        session.region = None
        session._idle = None
        with unittest.mock.patch.object(scroll.config, "DEBUG_KEYS", False), \
             unittest.mock.patch.object(scroll.ScrollSession, "_touch"), \
             unittest.mock.patch.object(scroll.ScrollSession, "_apply") as apply:
            handled = session._on_key(None, self.alt(Gdk.KEY_j))
        self.assertTrue(handled)
        seen.assert_called_once_with("scroll")
        apply.assert_not_called()

    def test_an_open_editor_still_refuses_to_be_replaced(self):
        import unittest.mock

        from homerow import service
        # The one exception, and it has to survive this route too: an overlay
        # holds nothing and may be replaced freely, an editor holds text that
        # has not been written back.
        instance = object.__new__(service.Daemon)
        instance.debug = False
        instance.log = None
        instance.overlay = unittest.mock.Mock(holds_unsaved_work=True)
        with unittest.mock.patch.object(service.GLib, "idle_add") as idle, \
             unittest.mock.patch.object(service, "_notify"):
            instance.dispatch("scroll")
        idle.assert_not_called()


if __name__ == "__main__":
    unittest.main()
