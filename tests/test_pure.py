"""Tests for the parts that need no display, no D-Bus and no running apps.

These are exactly the pieces that broke silently during development: label
generation, search ranking, nested-hint collapsing, overflow detection. Every
one of them is a pure function given the right inputs, and every one of them
shipped a regression that a screenshot had to catch.

Run with:  python3 -m unittest discover -s tests -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from homerow import hints, search  # noqa: E402


class Fake:
    """Stands in for elements.Element without touching AT-SPI."""

    def __init__(self, name="", role="link", x=0, y=0, w=10, h=10):
        self.name, self.role = name, role
        self.x, self.y, self.w, self.h = x, y, w, h

    @property
    def center(self):
        return self.x + self.w // 2, self.y + self.h // 2

    def __repr__(self):
        return f"<{self.name or '?'}>"


class HintLabels(unittest.TestCase):
    def test_counts_match_exactly(self):
        for n in (1, 2, 5, 9, 10, 15, 50, 81, 82, 200, 400):
            self.assertEqual(len(hints.generate(n)), n, f"n={n}")

    def test_labels_are_unique(self):
        for n in (9, 10, 81, 82, 300):
            labels = hints.generate(n)
            self.assertEqual(len(set(labels)), n, f"n={n}")

    def test_labels_are_prefix_free(self):
        # Without this, finishing a label is ambiguous with starting a longer
        # one and the overlay cannot know when you are done typing.
        for n in (10, 50, 82, 200):
            labels = hints.generate(n)
            for a in labels:
                for b in labels:
                    if a != b:
                        self.assertFalse(
                            b.startswith(a), f"{a!r} prefixes {b!r} at n={n}")

    def test_short_labels_come_first(self):
        labels = hints.generate(12)
        self.assertLessEqual(len(labels[0]), len(labels[-1]))

    def test_assign_orders_by_position(self):
        # Top-left elements get the shortest labels. Needs enough elements
        # that label lengths actually differ -- with three, every label is one
        # character and "shortest" means nothing.
        elements = [Fake(y=y) for y in (900, 100, 500, 300, 700,
                                        200, 800, 400, 600, 50, 950, 150)]
        labels = hints.assign(elements)
        self.assertEqual(len(set(labels)), len(elements))
        shortest = min(len(label) for label in labels)
        self.assertGreater(max(len(label) for label in labels), shortest)
        topmost = min(range(len(elements)), key=lambda i: elements[i].y)
        self.assertEqual(len(labels[topmost]), shortest)

    def test_assign_is_stable_for_same_input(self):
        elements = [Fake(y=y) for y in (30, 10, 20)]
        self.assertEqual(hints.assign(elements), hints.assign(elements))

    def test_single_element_gets_single_character(self):
        self.assertEqual(len(hints.generate(1)[0]), 1)


class SearchRanking(unittest.TestCase):
    def rank(self, names, query):
        elements = [Fake(name=n) for n in names]
        haystacks = [f"{n} link".lower() for n in names]
        return [e.name for e in search.matches(elements, query, haystacks)]

    def test_exact_label_ranks_first(self):
        # The bug: BigInt was buried past the last reachable number.
        order = self.rank(
            ["BigInt64Array", "Errors: BigInt syntax", "BigInt",
             "JavaScript / BigInt"], "bigint")
        self.assertEqual(order[0], "BigInt")

    def test_whole_word_beats_substring(self):
        order = self.rank(["WebGLBuffer", "bg"], "bg")
        self.assertEqual(order[0], "bg")

    def test_prefix_beats_interior(self):
        order = self.rank(["a bgColor thing", "bgColor"], "bgcolor")
        self.assertEqual(order[0], "bgColor")

    def test_all_terms_must_match(self):
        self.assertEqual(self.rank(["Save File", "Save"], "save file"),
                         ["Save File"])

    def test_no_match_is_empty(self):
        self.assertEqual(self.rank(["alpha", "beta"], "zzz"), [])

    def test_empty_query_keeps_everything(self):
        self.assertEqual(len(self.rank(["a", "b", "c"], "")), 3)

    def test_unindexed_names_do_not_match(self):
        # Names stream in asynchronously; entries still None must not match
        # rather than crash or match everything.
        elements = [Fake(name="alpha"), Fake(name="beta")]
        result = search.matches(elements, "a", [None, "beta link"])
        self.assertEqual([e.name for e in result], ["beta"])

    def test_ties_keep_document_order(self):
        order = self.rank(["one thing", "two thing", "red thing"], "thing")
        self.assertEqual(order, ["one thing", "two thing", "red thing"])


class NestedElements(unittest.TestCase):
    """elements._nested collapses a wrapper and its child into one hint."""

    def setUp(self):
        from homerow import elements
        self.nested = elements._nested

    def test_similar_sized_overlap_is_a_duplicate(self):
        accepted = [Fake(x=0, y=0, w=100, h=20)]
        self.assertTrue(self.nested(50, 10, 100 * 20, accepted))

    def test_small_control_inside_large_row_is_kept(self):
        # A button inside a toolbar is a separate target, not a duplicate.
        accepted = [Fake(x=0, y=0, w=1000, h=40)]
        self.assertFalse(self.nested(500, 20, 20 * 20, accepted))

    def test_disjoint_elements_are_kept(self):
        accepted = [Fake(x=0, y=0, w=100, h=20)]
        self.assertFalse(self.nested(500, 500, 100 * 20, accepted))

    def test_zero_area_accepted_does_not_divide_by_zero(self):
        accepted = [Fake(x=0, y=0, w=0, h=0)]
        self.assertFalse(self.nested(0, 0, 100, accepted))


class ScrollOverflow(unittest.TestCase):
    """scroll._overflows decides what is actually scrollable."""

    class Region:
        def __init__(self, w, h, children):
            self.w, self.h = w, h
            self._children = children
            self.accessible = self

        # Minimal AT-SPI surface used by _overflows.
        def get_child_count(self):
            return len(self._children)

        def get_child_at_index(self, index):
            return self._children[index]

        def get_component_iface(self):
            return self

        def get_extents(self, _coord):
            raise AssertionError("region extents are not read")

    class Child:
        def __init__(self, y, height):
            self.y, self.height = y, height
            self.x, self.width = 0, 10

        def get_component_iface(self):
            return self

        def get_extents(self, _coord):
            return self

    def test_content_taller_than_box_overflows(self):
        from homerow import scroll
        children = [self.Child(y, 20) for y in range(0, 2000, 100)]
        self.assertTrue(scroll._overflows(self.Region(300, 400, children)))

    def test_content_fitting_inside_does_not(self):
        from homerow import scroll
        # 16 rows spanning 218px inside a 591px pane -- the pcmanfm-qt case
        # that was wrongly reported as scrollable.
        children = [self.Child(y, 14) for y in range(0, 218, 14)]
        self.assertFalse(scroll._overflows(self.Region(1196, 591, children)))

    def test_childless_container_is_assumed_scrollable(self):
        from homerow import scroll
        # Web documents lay children out lazily; refusing these would lose the
        # main scroll target on most pages.
        self.assertTrue(scroll._overflows(self.Region(300, 400, [])))


if __name__ == "__main__":
    unittest.main()
