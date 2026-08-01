"""Tests for the parts that need no display, no D-Bus and no running apps.

These are exactly the pieces that broke silently during development: label
generation, search ranking, nested-hint collapsing, overflow detection. Every
one of them is a pure function given the right inputs, and every one of them
shipped a regression that a screenshot had to catch.

Run with:  python3 -m unittest discover -s tests -v
"""

import contextlib
import os
import sys
import unittest
import unittest.mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from homerow import config, hints, search  # noqa: E402


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

    def session(self, region, regions=None):
        from homerow import scroll
        instance = object.__new__(scroll.ScrollSession)
        instance.region = region
        instance.regions = list(regions) if regions else [region]
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
        # Just past the region's left edge, vertically already inside it: only
        # x should move, landing just inside the edge -- clear of the seam with
        # whatever is next door, and nowhere near the center.
        with unittest.mock.patch.object(
                scroll, "_pointer_position", return_value=(50, 250)):
            x, y = instance._wheel_target()
        self.assertEqual((x, y), (200 + config.SCROLL_TARGET_MARGIN, 250))

    def test_no_pointer_info_falls_back_to_center(self):
        from homerow import scroll
        region = Fake(x=200, y=200, w=800, h=800)
        instance = self.session(region)
        with unittest.mock.patch.object(
                scroll, "_pointer_position", return_value=None):
            self.assertEqual(instance._wheel_target(), region.center)

    def test_pointer_over_an_enclosed_sibling_steps_off_it(self):
        from homerow import scroll
        # devdocs.io live: only the sidebar gets detected, so best() opens on
        # the whole-window fallback. With the pointer resting on the sidebar,
        # aiming the wheel there scrolled the sidebar for BOTH Tab entries --
        # Tab looked broken. The window's target must land beside the sidebar.
        sidebar = Fake(x=0, y=144, w=336, h=623)
        window = Fake(x=0, y=0, w=1366, h=768)
        instance = self.session(window, [window, sidebar])
        with unittest.mock.patch.object(
                scroll, "_pointer_position", return_value=(150, 400)):
            x, y = instance._wheel_target()
        self.assertFalse(sidebar.x <= x < sidebar.x + sidebar.w
                         and sidebar.y <= y < sidebar.y + sidebar.h)
        self.assertTrue(window.x <= x < window.x + window.w
                        and window.y <= y < window.y + window.h)

    def test_stepping_off_a_sibling_moves_as_little_as_possible(self):
        from homerow import scroll
        sidebar = Fake(x=0, y=0, w=300, h=768)
        window = Fake(x=0, y=0, w=1366, h=768)
        instance = self.session(window, [window, sidebar])
        with unittest.mock.patch.object(
                scroll, "_pointer_position", return_value=(150, 400)):
            x, y = instance._wheel_target()
        # Just past the sidebar's edge -- clear of the drag handle sitting on
        # it -- rather than teleported to the far side or the window's exact
        # center: the cursor visibly moves either way, so it should move the
        # short way.
        self.assertEqual((x, y), (300 + config.SCROLL_TARGET_MARGIN, 400))

    def test_pointer_inside_the_enclosing_pane_is_left_alone(self):
        from homerow import scroll
        # The reverse case: the session is on the sidebar and the pointer is
        # already on it. The pane around it encloses this region rather than
        # the other way round, so it is not in the way.
        sidebar = Fake(x=0, y=144, w=336, h=623)
        window = Fake(x=0, y=0, w=1366, h=768)
        instance = self.session(sidebar, [window, sidebar])
        with unittest.mock.patch.object(
                scroll, "_pointer_position", return_value=(150, 400)):
            self.assertEqual(instance._wheel_target(), (150, 400))

    def test_blockers_covering_everything_still_yield_a_target(self):
        from homerow import scroll
        # Degenerate, but _clear_point must never return None: there is always
        # a wheel event to send somewhere.
        wrapper = Fake(x=0, y=0, w=400, h=400)
        cover = Fake(x=0, y=0, w=400, h=400)
        self.assertEqual(scroll._clear_point(wrapper, [cover], (10, 10)),
                         wrapper.center)


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


class ScrollAimCheck(unittest.TestCase):
    """The wheel's aim is settled by watching the user's own scroll land.

    Recorded live in qutebrowser (Qt WebEngine): AT-SPI publishes a devdocs.io
    page as one document and nothing else, so with the cursor resting on the
    sidebar there was no detected sibling to step off and every Tab entry
    scrolled the sidebar -- the content pane did not move in a single frame.
    Geometry cannot see a scroller that was never published, so the aim has to
    be tested against the region itself.

    That test used to run on entry, as a synthetic scroll-and-undo at each
    candidate point: measured live on Wikipedia in Chromium, 12 wheel events
    over 1.14s of the page jumping before a key could be pressed. So the same
    measurement now rides on the scroll the user actually asked for, and these
    tests drive it through _apply() -- the aim is a consequence of scrolling,
    not a phase before it.
    """

    def session(self, region, regions=None):
        from homerow import scroll
        instance = object.__new__(scroll.ScrollSession)
        instance.region = region
        instance.regions = list(regions) if regions else [region]
        instance.count = ""
        instance.window = unittest.mock.Mock()
        return instance

    @contextlib.contextmanager
    def world(self, scrolls_when, watchers=("watcher",), pointer=(150, 400)):
        """A page that moves only when the wheel lands where `scrolls_when` says.

        `_position` reads the offset the fake wheel maintains, so the aim logic
        runs against real movement readings rather than a patched verdict.
        """
        from homerow import scroll
        offset = {"y": 0}
        sent = []

        def wheel(x, y, button, clicks):
            sent.append((x, y, button, clicks))
            if scrolls_when(x, y):
                # A click of the wheel is worth tens of pixels, well past the
                # couple of pixels of slop _moved allows for.
                offset["y"] += clicks * 50 * (
                    1 if button == scroll.WHEEL_DOWN else -1)

        with unittest.mock.patch.object(
                scroll, "_probe_children", return_value=list(watchers)), \
             unittest.mock.patch.object(
                scroll, "_position", lambda _w: offset["y"]), \
             unittest.mock.patch.object(scroll, "_wheel_paced", wheel), \
             unittest.mock.patch.object(scroll.time, "sleep"), \
             unittest.mock.patch.object(
                scroll, "_pointer_position", return_value=pointer):
            yield sent

    def test_pointer_aim_is_kept_when_it_scrolls_the_region(self):
        from homerow import scroll
        document = Fake(x=0, y=15, w=1366, h=736)
        instance = self.session(document)
        with self.world(lambda x, y: True) as sent:
            instance._apply(scroll.WHEEL_DOWN, "line")
            self.assertEqual(document.aim, "pointer")
            self.assertEqual(instance._wheel_target(), (150, 400))
        # One wheel event, at the cursor: a correct aim costs no extra motion
        # and never moves the cursor.
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][:2], (150, 400))

    def test_aim_moves_off_the_pointer_when_the_region_does_not_move(self):
        from homerow import scroll
        # The qutebrowser case: one region covering everything, cursor on an
        # undetected sidebar. Aiming at the pointer scrolls the sidebar, so the
        # region itself never moves and the aim must shift to its middle.
        document = Fake(x=0, y=15, w=1366, h=736)
        instance = self.session(document)
        with self.world(lambda x, y: x > 340) as sent:
            instance._apply(scroll.WHEEL_DOWN, "line")
            self.assertEqual(document.aim, "center_x")
            x, y = instance._wheel_target()
        self.assertEqual(x, document.center[0])
        # The cursor keeps its height: leaving the sidebar is a sideways move,
        # so there is no reason to also drag it up or down the page.
        self.assertEqual(y, 400)
        # The retry is the scroll the user asked for, re-sent where it works --
        # and nothing is scrolled back, so there is no visible bounce.
        self.assertEqual([event[2:] for event in sent],
                         [(scroll.WHEEL_DOWN, scroll.config.SCROLL_LINE_CLICKS)] * 2)

    def test_unverifiable_region_aims_down_the_middle(self):
        from homerow import scroll
        # The whole-window fallback has no accessible, so there is nothing to
        # watch and no way to test the aim.
        window = Fake(x=0, y=0, w=1366, h=768)
        instance = self.session(window)
        with self.world(lambda x, y: True, watchers=()) as sent:
            instance._settle_aim()
            self.assertEqual(window.aim, "center_x")
            self.assertEqual(instance._wheel_target(), (window.center[0], 400))
            instance._apply(scroll.WHEEL_DOWN, "line")
        # Settled without a probe, so scrolling sends exactly one wheel event.
        self.assertEqual(len(sent), 1)

    def test_a_wall_does_not_settle_the_aim(self):
        from homerow import scroll
        # Nothing moves anywhere: either the region does not scroll, or it is
        # simply already at the end being scrolled towards. Those are
        # indistinguishable, so no verdict is recorded -- pressing j at the
        # bottom of a page must not teach the session a wrong aim -- and the
        # cursor is left where it was.
        region = Fake(x=0, y=0, w=800, h=600)
        instance = self.session(region)
        with self.world(lambda x, y: False):
            instance._apply(scroll.WHEEL_DOWN, "line")
            self.assertFalse(getattr(region, "aim_fixed", False))
            self.assertEqual(instance._wheel_target(), (150, 400))

    def test_a_settled_aim_is_not_reprobed_per_keystroke(self):
        from homerow import scroll
        region = Fake(x=0, y=0, w=800, h=600)
        instance = self.session(region)
        with self.world(lambda x, y: True) as sent:
            instance._apply(scroll.WHEEL_DOWN, "line")
            self.assertTrue(region.aim_fixed)
            instance._apply(scroll.WHEEL_DOWN, "line")
            instance._apply(scroll.WHEEL_DOWN, "line")
        # Three keystrokes, three wheel events: the check is not repaid.
        self.assertEqual(len(sent), 3)

    def test_a_sideways_scroll_never_settles_the_aim(self):
        from homerow import scroll
        # _position watches one axis, so a horizontal scroll reads as no
        # movement -- and would condemn an aim that is perfectly good.
        region = Fake(x=0, y=0, w=800, h=600)
        instance = self.session(region)
        with self.world(lambda x, y: True) as sent:
            instance._apply(scroll.WHEEL_RIGHT, "line")
        self.assertFalse(getattr(region, "aim_fixed", False))
        self.assertEqual(len(sent), 1)


class LegendOnOneMonitor(unittest.TestCase):
    """The legend centres on the monitor in use, not on the span of all of them.

    screen_size() spans every monitor, which is right for the overlay window
    and wrong for anything centred: the middle of a two-monitor desktop is the
    seam between the screens, so the pill would be drawn half on each.
    """

    def test_a_single_monitor_is_the_whole_screen(self):
        from homerow import overlay
        self.assertEqual(overlay.focused_monitor(1366, 768), (0, 0, 1366, 768))

    def test_the_pill_lands_on_the_monitor_the_pointer_is_on(self):
        import cairo

        from homerow import overlay, theme
        # Two 1366-wide screens side by side, pointer on the right-hand one.
        right = (1366, 0, 1366, 768)
        drawn = []
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 8, 8)
        cr = cairo.Context(surface)
        original = overlay._rounded_rect

        def record(context, x, y, w, h, radius):
            drawn.append((x, y, w, h))
            return original(context, x, y, w, h, radius)

        with unittest.mock.patch.object(
                overlay, "focused_monitor", return_value=right), \
             unittest.mock.patch.object(overlay, "_rounded_rect", record):
            overlay.draw_legend(cr, [overlay.badge("SCROLL")], 2732, 768,
                                theme.palette())
        pill_x, _, pill_w, _ = drawn[0]
        centre = pill_x + pill_w / 2
        self.assertGreater(centre, 1366)      # not on the left screen
        self.assertAlmostEqual(centre, 1366 + 683, delta=2)   # centred on the right


class LegendLayout(unittest.TestCase):
    """The legend row is laid out from parts, and measured the way it draws.

    It used to be one concatenated string per mode -- mode name, live state
    and forty characters of static help in one weight and one colour. Caret
    mode's reached 150 characters and most of a screen wide, and the part that
    changed while you worked was the part you were least likely to notice.
    """

    def context(self):
        import cairo
        return cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, 8, 8))

    def parts(self, pairs=6):
        from homerow import overlay
        return [overlay.badge("CARET"), overlay.badge("d…  2/7"),
                overlay.keys([(f"k{i}", f"meaning{i}") for i in range(pairs)])]

    def test_a_row_that_fits_is_left_alone(self):
        from homerow import overlay
        cr = self.context()
        parts = self.parts()
        kept = overlay._fit(cr, parts, 10_000)
        self.assertEqual(kept, parts)

    def test_a_row_too_wide_loses_pairs(self):
        from homerow import overlay
        cr = self.context()
        parts = self.parts()
        full = overlay._lay_out(cr, parts)[0]
        kept = overlay._fit(cr, parts, full / 2)
        self.assertLessEqual(overlay._lay_out(cr, kept)[0], full / 2)
        self.assertLess(len(kept[-1][1]), len(parts[-1][1]))

    def test_the_way_out_is_never_dropped(self):
        from homerow import overlay
        cr = self.context()
        parts = self.parts()
        # esc is the last pair in every mode's list. A legend that has dropped
        # the way out is worse than one that is a little too wide.
        last = parts[-1][1][-1]
        for budget in (400, 200, 80, 10):
            with self.subTest(budget=budget):
                kept = overlay._fit(cr, parts, budget)
                self.assertEqual(kept[-1][1][-1], last)

    def test_fitting_does_not_mutate_the_caller(self):
        from homerow import overlay
        # The modes rebuild their parts every draw, but a mode that hoisted
        # the list to a constant would otherwise lose keys permanently the
        # first time it was drawn on a narrow screen.
        cr = self.context()
        parts = self.parts()
        overlay._fit(cr, parts, 50)
        self.assertEqual(len(parts[-1][1]), 6)

    def test_keys_are_placed_as_a_key_then_its_meaning(self):
        from homerow import overlay
        cr = self.context()
        _, placed = overlay._lay_out(
            cr, [overlay.badge("CARET"),
                 overlay.keys([("y", "yank"), ("esc", "leave")])])
        self.assertEqual([kind for kind, _, _, _ in placed],
                         ["badge", "key", "meaning", "key", "meaning"])
        # Left to right, in the order given, never overlapping.
        offsets = [offset for _, _, offset, _ in placed]
        self.assertEqual(offsets, sorted(offsets))
        for (_, _, offset, span), (_, _, next_offset, _) in zip(placed,
                                                                placed[1:]):
            self.assertLessEqual(offset + span, next_offset)

    def test_a_key_sits_closer_to_its_meaning_than_to_the_next_pair(self):
        from homerow import overlay
        # This is the whole grouping mechanism: the gaps say which meaning
        # belongs to which key, so the row is scanned rather than read.
        cr = self.context()
        _, placed = overlay._lay_out(
            cr, [overlay.keys([("y", "yank"), ("p", "put")])])
        key, meaning, next_key = placed[0], placed[1], placed[2]
        own = meaning[2] - (key[2] + key[3])
        across = next_key[2] - (meaning[2] + meaning[3])
        self.assertLess(own, across)

    def test_a_bare_string_still_draws(self):
        from homerow import overlay, theme
        # Pickers pass a prompt with nothing to group. It has to keep working:
        # this is the one caller that has no key/meaning structure at all.
        cr = self.context()
        overlay.draw_legend(cr, "pick a field", 1366, 768, theme.palette())


class ScrollDeferredRescue(unittest.TestCase):
    """Candidates that can only be proved by scrolling them wait for Tab.

    Rescuing a virtualised pane means scrolling it and putting it back, which
    the user watches happen. Measured on a Wikipedia article in Chromium,
    doing that for the whole shortlist on entry was ~1.25s of the page moving
    before the outline appeared; probing only what best() actually needs cut
    that to ~0.5s, and the rest happens on the keystroke that asks for it.
    """

    def session(self, region, regions, deferred):
        from homerow import scroll
        instance = object.__new__(scroll.ScrollSession)
        instance.region = region
        instance.regions = list(regions)
        instance.deferred = list(deferred)
        return instance

    def test_tab_rescues_what_entry_left_alone(self):
        from homerow import scroll
        content = Fake(x=340, y=0, w=1000, h=768)
        sidebar = Fake(x=0, y=0, w=340, h=768)
        instance = self.session(content, [content], [sidebar])
        with unittest.mock.patch.object(
                scroll, "_scrolls", return_value=True):
            instance._rescue_deferred()
        self.assertEqual(instance.regions, [content, sidebar])
        self.assertTrue(sidebar.scroll_y)

    def test_a_candidate_that_does_not_scroll_is_not_offered(self):
        from homerow import scroll
        content = Fake(x=340, y=0, w=1000, h=768)
        banner = Fake(x=0, y=0, w=340, h=100)
        instance = self.session(content, [content], [banner])
        with unittest.mock.patch.object(
                scroll, "_scrolls", return_value=False):
            instance._rescue_deferred()
        self.assertEqual(instance.regions, [content])

    def test_the_rescue_is_paid_for_once(self):
        from homerow import scroll
        content = Fake(x=340, y=0, w=1000, h=768)
        sidebar = Fake(x=0, y=0, w=340, h=768)
        instance = self.session(content, [content], [sidebar])
        with unittest.mock.patch.object(
                scroll, "_scrolls", return_value=True) as scrolls:
            instance._rescue_deferred()
            instance._rescue_deferred()
            instance._rescue_deferred()
        # Tab is pressed repeatedly to cycle; the probing is not.
        self.assertEqual(scrolls.call_count, 1)

    def test_a_candidate_already_covered_is_not_probed(self):
        from homerow import scroll
        # collect()'s own rescue found this one at entry, because the pointer
        # was on it. Probing the wrapper around it again would offer a second
        # Tab stop that scrolls the same thing.
        pane = Fake(x=0, y=0, w=340, h=768)
        wrapper = Fake(x=0, y=0, w=344, h=768)
        instance = self.session(pane, [pane], [wrapper])
        with unittest.mock.patch.object(
                scroll, "_scrolls", return_value=True) as scrolls:
            instance._rescue_deferred()
        self.assertEqual(instance.regions, [pane])
        self.assertEqual(scrolls.call_count, 0)


class ScrollWindowFallbackTabStop(unittest.TestCase):
    """The whole window stays reachable by Tab even when regions were found.

    Live on devdocs.io/html-global-attributes: detection found the sidebar and
    not the content pane, and with the pointer resting on the sidebar best()
    opened on it -- leaving a one-entry Tab list, so scroll mode could only
    ever scroll the sidebar.
    """

    def test_window_is_appended_when_it_is_not_already_offered(self):
        from homerow import scroll
        sidebar = Fake(x=0, y=144, w=336, h=623)
        window = Fake(x=0, y=0, w=1366, h=768)
        with unittest.mock.patch.object(
                scroll, "window_region", return_value=window):
            regions = scroll.with_window_fallback([sidebar])
        # Last, so Tab reaches the precisely-outlined regions first.
        self.assertEqual(regions, [sidebar, window])

    def test_window_is_not_duplicated(self):
        from homerow import scroll
        document = Fake(x=0, y=8, w=1366, h=755)
        window = Fake(x=0, y=0, w=1366, h=768)
        with unittest.mock.patch.object(
                scroll, "window_region", return_value=window):
            regions = scroll.with_window_fallback([document])
        self.assertEqual(regions, [document])

    def test_no_window_reported_changes_nothing(self):
        from homerow import scroll
        sidebar = Fake(x=0, y=144, w=336, h=623)
        with unittest.mock.patch.object(
                scroll, "window_region", return_value=None):
            self.assertEqual(scroll.with_window_fallback([sidebar]), [sidebar])


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

    def _close(self, prompt):
        # _close() ends with Gdk.Display.get_default().sync() unconditionally
        # -- fine on a real desktop (this file's usual dev environment), but
        # CI runs headless with no display at all, where get_default()
        # returns None. Patched here rather than skipped so the test still
        # runs in CI instead of being silently absent from it.
        from homerow import search
        with unittest.mock.patch.object(search.Gdk.Display, "get_default"):
            prompt._close()

    def test_successful_pick_still_calls_on_done(self):
        hit = Fake(name="result")
        prompt, picked, done = self.make([hit], submitted=True)
        self._close(prompt)
        self.assertEqual(picked, [hit])
        self.assertEqual(done, [True])

    def test_cancel_calls_on_done_without_picking(self):
        prompt, picked, done = self.make([Fake(name="result")], submitted=False)
        self._close(prompt)
        self.assertEqual(picked, [])
        self.assertEqual(done, [True])


class CaretVisualAndYank(unittest.TestCase):
    """CaretSession's v/V (visual/visual-line) and y/yy (yank/yank-line).

    Regression: _yank() used to call self._close() immediately after
    copying to the clipboard, so a real "yy" was impossible -- the session
    would already be destroyed after the first y, before a second keystroke
    could ever arrive. Yanking now just yanks and stays open, matching vim.
    """

    LINES = ["Content line 0", "Content line 1", "Content line 2"]
    TEXT = "\n".join(LINES) + "\n"

    def session(self):
        from homerow import caret
        instance = object.__new__(caret.CaretSession)
        instance.text = self.TEXT
        instance.length = len(self.TEXT)
        instance.offset = 0
        instance.anchor = None
        instance.linewise = False
        instance.pending_y = False
        instance.pending_g = False
        instance.iface = None
        instance.window = unittest.mock.Mock()

        starts = [0]
        for i, ch in enumerate(self.TEXT):
            if ch == "\n":
                starts.append(i + 1)

        def line_bounds(offset):
            start = max(s for s in starts if s <= offset)
            later = [s - 1 for s in starts if s > offset]
            end = min(later) if later else len(self.TEXT)
            return start, end

        instance._line_bounds = line_bounds
        return instance

    def test_charwise_selection_spans_just_the_marked_range(self):
        instance = self.session()
        instance.anchor, instance.offset = 8, 11
        self.assertEqual(instance._selection(), (8, 12))

    def test_linewise_selection_spans_whole_lines_regardless_of_column(self):
        instance = self.session()
        instance.anchor = 8                                  # mid line 0
        instance.offset = len(self.LINES[0]) + 1 + 5         # mid line 1
        instance.linewise = True
        start, end = instance._selection()
        self.assertEqual(
            instance.text[start:end], self.LINES[0] + "\n" + self.LINES[1] + "\n")

    def test_yank_copies_and_stays_open(self):
        from homerow import caret
        instance = self.session()
        instance.anchor, instance.offset = 8, 11
        with unittest.mock.patch.object(instance, "_set_clipboard") as set_clip, \
             unittest.mock.patch.object(caret, "_text_of", return_value="line"):
            instance._yank()
        set_clip.assert_called_once_with("line")
        self.assertIsNone(instance.anchor)          # visual mode exited
        self.assertFalse(instance.linewise)

    def test_yy_yanks_the_current_line(self):
        from homerow import caret
        instance = self.session()
        instance.offset = 5  # somewhere inside line 0
        expected = self.LINES[0] + "\n"
        with unittest.mock.patch.object(instance, "_set_clipboard") as set_clip, \
             unittest.mock.patch.object(caret, "_text_of", return_value=expected):
            instance._yank_line()
        set_clip.assert_called_once_with(expected)

    def test_empty_yank_does_not_touch_the_clipboard(self):
        # Guards the old sentinel-clipboard bug: an empty span must not
        # overwrite whatever the user already had copied.
        from homerow import caret
        instance = self.session()
        instance.anchor, instance.offset = 5, 5
        with unittest.mock.patch.object(instance, "_set_clipboard") as set_clip, \
             unittest.mock.patch.object(caret, "_text_of", return_value=""):
            instance._yank()
        set_clip.assert_not_called()


class CaretSearchReopen(unittest.TestCase):
    """/ inside caret mode reopens caret search instead of requiring Esc
    then the hotkey again from scratch to look for something else.

    on_search must run before on_done, mirroring search.SearchPrompt's
    on_pick/on_done ordering: on_done is what clears the daemon's overlay
    reference, so if it ran first, the new session on_search schedules
    would have its assignment wiped the instant it landed.
    """

    def session(self):
        from homerow import caret
        instance = object.__new__(caret.CaretSession)
        instance.window = unittest.mock.Mock()
        instance._grabbed = False
        instance._idle = None
        calls = []
        instance.on_done = lambda: calls.append("done")
        instance.on_search = lambda: calls.append("search")
        return instance, calls

    def test_reopen_search_calls_on_search_before_on_done(self):
        from homerow import caret
        instance, calls = self.session()
        with unittest.mock.patch.object(caret.Gdk.Display, "get_default"):
            instance._close(reopen_search=True)
        self.assertEqual(calls, ["search", "done"])

    def test_plain_close_never_calls_on_search(self):
        from homerow import caret
        instance, calls = self.session()
        with unittest.mock.patch.object(caret.Gdk.Display, "get_default"):
            instance._close()
        self.assertEqual(calls, ["done"])


class CaretSearchMatching(unittest.TestCase):
    """caret.word_hits(): type-to-find matching for caret search.

    Backs the "type a word, pick the labelled hint, land the caret there"
    flow -- CaretSearchPrompt narrows on every keystroke by calling this,
    so a wrong match here is a wrong caret position for every user of it.
    """

    class FakeIface:
        """Stands in for a real Atspi.Text; get_range_extents is patched at
        the Atspi.Text class level rather than implemented here, since GI
        bindings type-check their arguments against the real interface."""

    def block_texts(self, *texts):
        pairs = []
        for text in texts:
            block = Fake(name="")
            block.accessible = type(
                "Acc", (), {"get_text_iface": lambda self: self._iface})()
            block.accessible._iface = self.FakeIface()
            pairs.append((block, text))
        return pairs

    def patched(self, hits_fn):
        import gi
        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi

        class Ext:
            def __init__(self, x, w):
                self.x, self.y, self.width, self.height = x, 100, w, 14

        def fake_extents(_iface, start, end, _coord):
            return Ext(start, end - start)

        return unittest.mock.patch.object(
            Atspi.Text, "get_range_extents", side_effect=fake_extents)

    def test_matches_are_case_insensitive_substrings(self):
        from homerow import caret
        pairs = self.block_texts("the Quick CANVAS jumps over canvas2")
        with self.patched(caret.word_hits):
            hits = caret.word_hits(pairs, "canvas")
        self.assertEqual([h.word for h in hits], ["CANVAS", "canvas2"])

    def test_empty_query_matches_nothing(self):
        from homerow import caret
        pairs = self.block_texts("anything at all")
        with self.patched(caret.word_hits):
            self.assertEqual(caret.word_hits(pairs, ""), [])

    def test_matches_span_multiple_blocks_in_order(self):
        from homerow import caret
        pairs = self.block_texts("first canvas here", "second canvas there")
        with self.patched(caret.word_hits):
            hits = caret.word_hits(pairs, "canvas")
        self.assertEqual(len(hits), 2)
        self.assertIs(hits[0].block, pairs[0][0])
        self.assertIs(hits[1].block, pairs[1][0])

    def test_offset_points_at_the_start_of_the_matched_word(self):
        from homerow import caret
        text = "abc canvas def"
        pairs = self.block_texts(text)
        with self.patched(caret.word_hits):
            hits = caret.word_hits(pairs, "canvas")
        self.assertEqual(hits[0].offset, text.index("canvas"))

    def test_hit_cap_stops_collection_early(self):
        from homerow import caret
        text = " ".join(f"canvas{i}" for i in range(50))
        pairs = self.block_texts(text)
        with self.patched(caret.word_hits):
            hits = caret.word_hits(pairs, "canvas", limit=5)
        self.assertEqual(len(hits), 5)


class EditWriteBack(unittest.TestCase):
    """The parts of edit mode that decide what text goes back into a field."""

    def test_editor_added_newline_comes_off(self):
        from homerow import edit
        # The case that matters: a one-line search box, where a stray newline
        # is not whitespace but a submit.
        self.assertEqual(edit.strip_added_newline("query", "edited\n"),
                         "edited")

    def test_newline_the_field_already_had_is_kept(self):
        from homerow import edit
        self.assertEqual(edit.strip_added_newline("a\n", "b\n"), "b\n")

    def test_only_one_newline_comes_off(self):
        from homerow import edit
        self.assertEqual(edit.strip_added_newline("q", "a\n\n"), "a\n")

    def test_text_without_a_trailing_newline_is_untouched(self):
        from homerow import edit
        self.assertEqual(edit.strip_added_newline("q", "abc"), "abc")

    def test_empty_original_still_loses_the_added_newline(self):
        from homerow import edit
        self.assertEqual(edit.strip_added_newline("", "typed\n"), "typed")


class EditWindowPlacement(unittest.TestCase):
    def test_a_one_line_field_stays_a_one_line_box(self):
        from homerow import edit
        # The omnibox case, and the whole point of measuring in cells: a
        # 28px-tall field must not open a 400px-tall editor over the page.
        field = Fake(x=100, y=200, w=565, h=28)
        # One row of a 17px cell plus the border.
        _, _, w, h = edit.frame_rect(field, 1920, 1080, 260, 17 + 4)
        self.assertEqual((w, h), (565, 28))

    def test_anchor_is_the_field_corner(self):
        from homerow import edit
        field = Fake(x=100, y=200, w=565, h=28)
        x, y, _, _ = edit.frame_rect(field, 1920, 1080, 260, 21)
        self.assertEqual((x, y), (100, 200))

    def test_a_tiny_field_grows_to_the_floor(self):
        from homerow import edit
        field = Fake(x=10, y=10, w=40, h=8)
        _, _, w, h = edit.frame_rect(field, 1920, 1080, 260, 21)
        self.assertEqual((w, h), (260, 21))

    def test_large_field_keeps_its_own_size(self):
        from homerow import edit
        field = Fake(x=10, y=10, w=900, h=600)
        _, _, w, h = edit.frame_rect(field, 1920, 1080, 260, 120)
        self.assertEqual((w, h), (900, 600))

    def test_field_near_an_edge_is_pushed_back_on_screen(self):
        from homerow import edit
        field = Fake(x=1800, y=1050, w=400, h=300)
        x, y, w, h = edit.frame_rect(field, 1920, 1080, 260, 120)
        self.assertGreaterEqual(x, 0)
        self.assertGreaterEqual(y, 0)
        self.assertLessEqual(x + w, 1920)
        self.assertLessEqual(y + h, 1080)

    def test_editor_wider_than_the_screen_still_starts_on_it(self):
        from homerow import edit
        field = Fake(x=50, y=50, w=10, h=10)
        x, y, _, _ = edit.frame_rect(field, 400, 300, 720, 400)
        self.assertEqual((x, y), (0, 0))


class EditCompactMode(unittest.TestCase):
    def test_a_single_line_field_is_compact(self):
        from homerow import edit
        self.assertTrue(edit.compact_rows(28, 17, 3))

    def test_a_textarea_is_not_compact(self):
        from homerow import edit
        self.assertFalse(edit.compact_rows(220, 17, 3))

    def test_an_unmeasured_cell_is_never_compact(self):
        from homerow import edit
        self.assertFalse(edit.compact_rows(28, 0, 3))

    def test_compact_hides_chrome_for_vim(self):
        from homerow import edit
        argv = edit.editor_argv("/tmp/f.md", editor="nvim", compact=True)
        self.assertIn("-c", argv)
        self.assertIn(config.EDIT_COMPACT_SETTINGS, argv)
        self.assertEqual(argv[-1], "/tmp/f.md")

    def test_compact_leaves_a_non_vim_editor_alone(self):
        from homerow import edit
        argv = edit.editor_argv("/tmp/f.md", editor="helix", compact=True)
        self.assertEqual(argv, ["helix", "/tmp/f.md"])

    def test_a_full_size_field_gets_no_compact_flags(self):
        from homerow import edit
        argv = edit.editor_argv("/tmp/f.md", editor="nvim", compact=False)
        self.assertNotIn(config.EDIT_COMPACT_SETTINGS, argv)

    def test_save_shortcuts_are_present_whatever_the_size(self):
        from homerow import edit
        for compact in (True, False):
            argv = edit.editor_argv("/tmp/f.md", editor="nvim",
                                    compact=compact)
            for mapping in config.EDIT_KEYMAPS:
                self.assertIn(mapping, argv)

    def test_both_q_and_space_w_are_mapped(self):
        joined = " ".join(config.EDIT_KEYMAPS)
        self.assertIn("> q :", joined)
        self.assertIn("<Space>w", joined)

    def test_the_mappings_are_buffer_local(self):
        # Otherwise they would shadow macro recording and the leader key in
        # the user's own nvim.
        for mapping in config.EDIT_KEYMAPS:
            self.assertIn("<buffer>", mapping)

    def test_the_shortcuts_write_rather_than_discarding(self):
        for mapping in config.EDIT_KEYMAPS:
            self.assertIn("wq", mapping)

    def test_a_non_vim_editor_gets_no_mapping(self):
        from homerow import edit
        argv = edit.editor_argv("/tmp/f.md", editor="helix", compact=False)
        self.assertEqual(argv, ["helix", "/tmp/f.md"])

    def test_an_absolute_editor_path_is_still_recognised(self):
        from homerow import edit
        argv = edit.editor_argv("/tmp/f.md", editor="/usr/bin/nvim",
                                compact=True)
        self.assertIn("-c", argv)


class EditorCommand(unittest.TestCase):
    def test_visual_wins_over_editor(self):
        from homerow import edit
        with unittest.mock.patch.dict(
                os.environ, {"VISUAL": "helix", "EDITOR": "vim"}):
            self.assertEqual(edit.editor_argv("/tmp/f.md"),
                             ["helix", "/tmp/f.md"])

    def test_editor_is_used_when_visual_is_unset(self):
        from homerow import edit
        env = dict(os.environ)
        env.pop("VISUAL", None)
        env["EDITOR"] = "vim"
        with unittest.mock.patch.dict(os.environ, env, clear=True):
            argv = edit.editor_argv("/tmp/f.md")
        self.assertEqual(argv[0], "vim")
        self.assertEqual(argv[-1], "/tmp/f.md")

    def test_an_editor_with_arguments_is_split(self):
        from homerow import edit
        argv = edit.editor_argv("/tmp/f.md", editor="nvim -u NONE")
        self.assertEqual(argv[:3], ["nvim", "-u", "NONE"])
        self.assertEqual(argv[-1], "/tmp/f.md")

    def test_falls_back_to_the_configured_editor(self):
        from homerow import edit
        env = {k: v for k, v in os.environ.items()
               if k not in ("VISUAL", "EDITOR")}
        with unittest.mock.patch.dict(os.environ, env, clear=True):
            argv = edit.editor_argv("/tmp/f.md")
        self.assertEqual(argv[0], config.EDIT_EDITOR)
        self.assertEqual(argv[-1], "/tmp/f.md")


class WarmEditor(unittest.TestCase):
    """The warm nvim server that opening a field attaches to."""

    def test_only_vim_speaks_the_remote_protocol(self):
        from homerow import edit
        self.assertTrue(edit.is_vim_like("nvim"))
        self.assertTrue(edit.is_vim_like("/usr/bin/nvim"))
        self.assertTrue(edit.is_vim_like("nvim -u NONE"))
        self.assertFalse(edit.is_vim_like("helix"))
        self.assertFalse(edit.is_vim_like(""))

    def test_visual_still_wins_when_resolving(self):
        from homerow import edit
        with unittest.mock.patch.dict(
                os.environ, {"VISUAL": "helix", "EDITOR": "vim"}):
            self.assertEqual(edit.resolve_editor(), "helix")
        self.assertEqual(edit.resolve_editor("nvim"), "nvim")

    def test_commands_become_a_vim_list(self):
        from homerow import edit
        self.assertEqual(edit._vim_list(["a", "b"]), "['a','b']")

    def test_quotes_in_a_command_are_escaped(self):
        from homerow import edit
        # A path with an apostrophe would otherwise end the string literal
        # and the rest would be parsed as vimscript.
        self.assertEqual(edit._vim_list(["edit /tmp/it's.md"]),
                         "['edit /tmp/it''s.md']")

    def test_the_socket_lives_in_the_runtime_dir(self):
        from homerow import edit
        with unittest.mock.patch.dict(
                os.environ, {"XDG_RUNTIME_DIR": "/run/user/42"}):
            self.assertEqual(edit.warm_socket_path(),
                             "/run/user/42/" + config.EDIT_WARM_SOCKET)


class EditDismissal(unittest.TestCase):
    """A dismissed editor must never write its buffer into the field.

    Two editors were once open over one field at the same time; both wrote
    back two milliseconds apart, and the abandoned one landed last and
    overwrote the real edit.
    """

    def session(self):
        from homerow import edit
        # Built without __init__: constructing one needs a display, a VTE
        # widget and a temp file, none of which this is about.
        session = edit.EditSession.__new__(edit.EditSession)
        session.closed = True          # so _close() is a no-op
        session._dismissed = False
        session.original = "real contents"
        session.path = "/nonexistent/homerow-test.md"
        session.written = []
        session.on_write = session.written.append
        session.on_done = lambda: None
        session._log = lambda _m: None
        return session

    def test_dismissed_session_does_not_write_back(self):
        session = self.session()
        session.dismiss()
        session._on_child_exited(None, 0)
        self.assertEqual(session.written, [])

    def test_dismissal_is_what_suppresses_it_not_the_exit_status(self):
        session = self.session()
        session._dismissed = True
        session._on_child_exited(None, 0)
        self.assertEqual(session.written, [])

    def test_an_editor_that_was_killed_does_not_write_back(self):
        session = self.session()
        session._on_child_exited(None, 9)
        self.assertEqual(session.written, [])

    def test_only_edit_sessions_claim_unsaved_work(self):
        from homerow import edit, overlay
        self.assertTrue(edit.EditSession.holds_unsaved_work)
        self.assertFalse(
            getattr(overlay.Overlay, "holds_unsaved_work", False))


class EditSafety(unittest.TestCase):
    def test_password_fields_are_refused(self):
        # Edit mode writes a field's contents to a temp file. The match rule
        # no longer filters by role, so this set is the only thing standing
        # between a password field and disk.
        self.assertIn("password text", config.EDIT_SKIP_ROLES)
        self.assertIn("terminal", config.EDIT_SKIP_ROLES)


if __name__ == "__main__":
    unittest.main()
