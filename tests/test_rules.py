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


if __name__ == "__main__":
    unittest.main()
