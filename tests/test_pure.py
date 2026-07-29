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
import unittest.mock

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
        self.assertTrue(self.nested(Fake(x=10, y=2, w=100, h=20), accepted))

    def test_small_control_inside_large_row_is_kept(self):
        # A button inside a toolbar is a separate target, not a duplicate.
        accepted = [Fake(x=0, y=0, w=1000, h=40)]
        self.assertFalse(self.nested(Fake(x=490, y=10, w=20, h=20), accepted))

    def test_disjoint_elements_are_kept(self):
        accepted = [Fake(x=0, y=0, w=100, h=20)]
        self.assertFalse(self.nested(Fake(x=490, y=490, w=100, h=20), accepted))

    def test_zero_area_accepted_does_not_divide_by_zero(self):
        accepted = [Fake(x=0, y=0, w=0, h=0)]
        self.assertFalse(self.nested(Fake(x=0, y=0, w=100, h=10), accepted))

    def test_combobox_selected_value_label_is_a_duplicate(self):
        # A combobox showing its current value at its own top-left corner,
        # spanning only part of its height -- seen identically on WSJ, Python
        # docs, Amazon, OpenTable and Netflix, none of which are a close area
        # ratio to their box.
        accepted = [Fake(x=935, y=317, w=172, h=46)]
        self.assertTrue(self.nested(Fake(x=935, y=317, w=172, h=11), accepted))

    def test_overlapping_but_not_enclosed_is_kept(self):
        # Same top-left corner but the candidate is not fully inside (taller
        # than the accepted box) -- a real distinct element, not a relabel.
        accepted = [Fake(x=824, y=131, w=516, h=50)]
        self.assertFalse(self.nested(Fake(x=824, y=131, w=67, h=70), accepted))


class ScrollOverflow(unittest.TestCase):
    """scroll._overflows reports (vertical, horizontal) overflow.

    Both axes are reported separately so h and l can be refused on a region
    that only scrolls downwards -- sending horizontal wheel events there is
    silently swallowed, which reads as scroll mode having broken.
    """

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
        def __init__(self, y, height, x=0, width=10):
            self.y, self.height = y, height
            self.x, self.width = x, width

        def get_component_iface(self):
            return self

        def get_extents(self, _coord):
            return self

    def test_content_taller_than_box_overflows_vertically_only(self):
        from homerow import scroll
        children = [self.Child(y, 20) for y in range(0, 2000, 100)]
        vertical, horizontal = scroll._overflows(
            self.Region(300, 400, children))
        self.assertTrue(vertical)
        self.assertFalse(horizontal)

    def test_content_wider_than_box_overflows_horizontally(self):
        from homerow import scroll
        children = [self.Child(0, 20, x, 40) for x in range(0, 3000, 100)]
        vertical, horizontal = scroll._overflows(
            self.Region(300, 400, children))
        self.assertTrue(horizontal)
        self.assertFalse(vertical)

    def test_content_fitting_inside_does_not(self):
        from homerow import scroll
        # 16 rows spanning 218px inside a 591px pane -- the pcmanfm-qt case
        # that was wrongly reported as scrollable.
        children = [self.Child(y, 14) for y in range(0, 218, 14)]
        self.assertEqual(scroll._overflows(self.Region(1196, 591, children)),
                         (False, False))

    def test_childless_container_is_assumed_scrollable(self):
        from homerow import scroll
        # Web documents lay children out lazily; refusing these would lose the
        # main scroll target on most pages.
        self.assertEqual(scroll._overflows(self.Region(300, 400, [])),
                         (True, True))


class ScrollWheelTarget(unittest.TestCase):
    """ScrollSession._wheel_target() decides where the synthetic wheel event
    lands, which also decides whether/how far the real pointer gets warped.

    Regression: this used to fall back to region.center whenever the pointer
    was outside the region -- on a large pane that is a big, disorienting
    teleport, reported live as "the mouse acts weird when I start scrolling".
    Clamping the pointer's own position into the region instead keeps the
    jump to just the axis (or axes) that were actually out of bounds.
    """

    def session(self, region):
        from homerow import scroll
        instance = object.__new__(scroll.ScrollSession)
        instance.region = region
        return instance

    def test_pointer_already_inside_is_used_unchanged(self):
        from homerow import scroll
        region = Fake(x=0, y=0, w=200, h=200)
        instance = self.session(region)
        with unittest.mock.patch.object(
                scroll, "_pointer_position", return_value=(50, 60)):
            self.assertEqual(instance._wheel_target(), (50, 60))

    def test_pointer_outside_is_clamped_not_teleported_to_center(self):
        from homerow import scroll
        region = Fake(x=200, y=200, w=800, h=800)   # center is (600, 600)
        instance = self.session(region)
        # Just past the region's left edge, vertically already inside it:
        # only x should move, landing at the edge -- nowhere near the center.
        with unittest.mock.patch.object(
                scroll, "_pointer_position", return_value=(50, 250)):
            x, y = instance._wheel_target()
        self.assertEqual((x, y), (200, 250))

    def test_no_pointer_info_falls_back_to_center(self):
        from homerow import scroll
        region = Fake(x=200, y=200, w=800, h=800)
        instance = self.session(region)
        with unittest.mock.patch.object(
                scroll, "_pointer_position", return_value=None):
            self.assertEqual(instance._wheel_target(), region.center)


class ScrollBest(unittest.TestCase):
    """scroll.best picks which detected region a session opens on.

    The case that matters most is the pointer sitting over something AT-SPI
    never surfaced as a candidate at all -- an undetected sidebar, say --
    where falling back to the largest region would silently scroll the
    content pane instead of what the user is actually looking at.
    """

    def test_pointer_over_a_candidate_wins(self):
        from homerow import scroll
        content = Fake(x=200, y=0, w=800, h=600)
        sidebar = Fake(x=0, y=0, w=200, h=600)
        with unittest.mock.patch.object(
                scroll, "_pointer_position", return_value=(50, 50)):
            self.assertIs(scroll.best([content, sidebar]), sidebar)

    def test_pointer_over_nothing_detected_falls_back_to_the_window(self):
        from homerow import scroll
        content = Fake(x=200, y=0, w=800, h=600)
        window = Fake(x=0, y=0, w=1000, h=600)
        with unittest.mock.patch.object(
                scroll, "_pointer_position", return_value=(50, 50)), \
             unittest.mock.patch.object(
                scroll, "window_region", return_value=window):
            self.assertIs(scroll.best([content]), window)

    def test_no_pointer_falls_back_to_largest(self):
        from homerow import scroll
        content = Fake(x=200, y=0, w=800, h=600)
        sidebar = Fake(x=0, y=0, w=200, h=600)
        with unittest.mock.patch.object(
                scroll, "_pointer_position", return_value=None):
            self.assertIs(scroll.best([content, sidebar]), content)


class ScrollVerifyDeadline(unittest.TestCase):
    """scroll.verify() must stop probing once collect()'s time budget is up.

    Atspi.set_timeout() bounds each individual D-Bus call, but verify() makes
    several of them per candidate -- each easily fast enough alone to dodge
    that cap, yet summing to a real stall when the AT-SPI service is merely
    slow, not hung. An expired deadline should short-circuit before any wheel
    event is even sent, and fail open (return the untested regions) rather
    than report them as non-scrolling.
    """

    class FakeExt:
        def __init__(self, y):
            self.x, self.y, self.width, self.height = 0, y, 10, 10

    class FakeChild:
        def __init__(self, y):
            self._y = y

        def get_component_iface(self):
            return self

        def get_extents(self, _coord):
            return ScrollVerifyDeadline.FakeExt(self._y)

    class FakeAccessible:
        def __init__(self, y):
            self._child = ScrollVerifyDeadline.FakeChild(y)

        def get_child_count(self):
            return 1

        def get_child_at_index(self, _index):
            return self._child

    def region(self, y):
        fake = Fake(x=0, y=0, w=100, h=100)
        fake.accessible = self.FakeAccessible(y)
        return fake

    def test_expired_deadline_skips_probing_and_keeps_everything(self):
        from homerow import scroll
        import time
        regions = [self.region(0), self.region(50)]
        with unittest.mock.patch.object(scroll, "_wheel") as wheel:
            result = scroll.verify(regions, deadline=time.monotonic() - 1)
        wheel.assert_not_called()
        self.assertEqual(result, regions)

    def test_no_deadline_behaves_as_before(self):
        # Passing no deadline (collect()'s only caller always passes one, but
        # nothing else should require it) must not change existing behaviour.
        from homerow import scroll
        regions = [self.region(0), self.region(50)]
        with unittest.mock.patch.object(scroll, "_wheel"):
            result = scroll.verify(regions)
        self.assertEqual(len(result), len(regions))


class SearchPromptCleanup(unittest.TestCase):
    """SearchPrompt._close must tell the daemon the session is over on every
    path, not only when nothing was picked.

    Regression: on a successful pick, on_done() -- which clears the daemon's
    overlay reference and its mode file -- never ran. The next hotkey then
    saw a "still open" overlay and tried to dismiss it, which replayed
    on_pick a second time: a successful search click silently fired twice.

    Built via object.__new__ rather than SearchPrompt(...) so this never
    constructs a real GTK window or touches the display.
    """

    class StubWindow:
        def destroy(self):
            pass

    def make(self, hits, submitted):
        from homerow import search
        prompt = object.__new__(search.SearchPrompt)
        prompt.window = self.StubWindow()
        prompt._grabbed = False
        prompt._idle = None
        prompt.hits = hits
        prompt.current = 0
        prompt.submitted = submitted
        picked, done = [], []
        prompt.on_pick = picked.append
        prompt.on_done = lambda: done.append(True)
        return prompt, picked, done

    def test_successful_pick_still_calls_on_done(self):
        hit = Fake(name="result")
        prompt, picked, done = self.make([hit], submitted=True)
        prompt._close()
        self.assertEqual(picked, [hit])
        self.assertEqual(done, [True])

    def test_cancel_calls_on_done_without_picking(self):
        prompt, picked, done = self.make([Fake(name="result")], submitted=False)
        prompt._close()
        self.assertEqual(picked, [])
        self.assertEqual(done, [True])


if __name__ == "__main__":
    unittest.main()
