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


if __name__ == "__main__":
    unittest.main()
