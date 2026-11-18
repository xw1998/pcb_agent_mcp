# -*- coding: utf-8 -*-
"""The site search has to refuse what `shape_gate` would report as a violation.

`_dg_i.kicad_pcb` moved 45 holes out of their pads and still read `shape_fail`:
two 45 degree corners at `/+3V3` 11.8,11.12 / 11.8,13.92 (a stitch started at an
existing track end where a 45 degree branch already left), two 0 degree corners
(a stitch laid on existing copper) and one redundant loop.  Moving a hole out of
a pad is `place_gate` rule 1; an acute corner is `shape_gate` rule 1.  Both block
the board, so buying one with the other is a swap, not a fix - the planner has to
know the difference before it commits, not after.

`_shape_local` is that knowledge, and it is deliberately a re-statement of the
gate's own arithmetic (`round(x,3)` vertices, corner = angle between two
outgoing rays, `< 90 - 1.0` is acute, same direction is the 0 degree pair).  A
second, "reasonable" convention would refuse sites the gate accepts and accept
sites it refuses - the same mistake pointing two ways.

Every case comes with its reverse: a 90 degree joint must pass, a tee must pass,
a 45 degree branch that is not continued past must pass, and the track being
slid must stop counting at the place it left.
"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import off_pad_dogbone as O  # noqa: E402

NET = "/X"
LA = "In2.Cu"


def _t(x1, y1, x2, y2, layer=LA, net=NET, w=0.4):
    return {"net": net, "layer": layer, "x1": x1, "y1": y1,
            "x2": x2, "y2": y2, "w": w}


class _Model(object):
    """Only what `_shape_local` reads: `tracks`, optionally `by_net_layer`."""

    def __init__(self, tracks, indexed=False):
        self.tracks = list(tracks)
        if indexed:
            idx = {}
            for t in self.tracks:
                idx.setdefault((t["net"], t["layer"]), []).append(t)
            self.by_net_layer = idx


class ShapeLocal(unittest.TestCase):

    # -- the real one: continuing past a vertex that already has a 45 branch --

    def test_stitch_along_a_track_leaves_the_gate_45_degrees(self):
        # `/+3V3` C8.1: 11.4->11.8 horizontal, a 45 degree branch leaves at
        # 11.8, and the stitch is laid from 11.8 outwards along the horizontal.
        # The rays at 11.8 are 0 (the stitch), 45 (the branch) and 180 (the
        # horizontal) - the smallest pair is 45, which is what the gate read.
        m = _Model([_t(11.4, 11.12, 11.8, 11.12),
                    _t(11.8, 11.12, 12.2, 11.52)])
        why = O._shape_local(m, NET,
                             [(LA, 0.4, (11.8, 11.12), (12.0, 11.12))], [])
        self.assertIsNotNone(why)
        self.assertIn("45.0 deg corner at 11.800,11.120", why)
        self.assertIn(LA, why)

    def test_same_vertex_turning_90_is_fine(self):
        # reverse: at a vertex with only the horizontal, leaving upwards is a
        # right angle and has to be accepted
        m = _Model([_t(11.4, 11.12, 11.8, 11.12)])
        self.assertIsNone(O._shape_local(
            m, NET, [(LA, 0.4, (11.8, 11.12), (11.8, 11.52))], []))

    # -- 0 degrees: the pair the gate calls duplicate copper --

    def test_redrawing_existing_copper_is_caught(self):
        m = _Model([_t(35.0, 18.89, 35.6, 18.89, layer="In4.Cu", w=0.25)])
        why = O._shape_local(
            m, NET, [("In4.Cu", 0.25, (35.0, 18.89), (35.6, 18.89))], [])
        self.assertIsNotNone(why)
        self.assertIn("0.0 deg corner", why)

    def test_a_t_junction_is_not_a_corner(self):
        # reverse: starting in the MIDDLE of existing copper draws a tee; the
        # gate reports that in its own bucket and it is not an acute corner, so
        # the site search must not refuse it either
        m = _Model([_t(35.0, 18.89, 35.6, 18.89, layer="In4.Cu", w=0.25)])
        self.assertIsNone(O._shape_local(
            m, NET, [("In4.Cu", 0.25, (35.3, 18.89), (35.3, 19.09))], []))

    # -- the slide: the old vertex stops counting, the new one starts --

    def test_a_slid_track_stops_counting_where_it_left(self):
        # T runs (1,0)-(2.4,0); S leaves (1,0) straight up.  The stitch carries
        # on from (1,0) along the horizontal, and T is being slid away, so the
        # only copper left at (1,0) is the vertical: a right angle.
        m = _Model([_t(1.0, 0.0, 2.4, 0.0, w=0.1),
                    _t(1.0, 0.0, 1.0, 0.9, w=0.1)])
        self.assertIsNone(O._shape_local(
            m, NET,
            [(LA, 0.1, (1.0, 0.0), (1.4, 0.0))],
            [(LA, (2.0, 0.0), (2.4, 0.0), (1.0, 0.0))]))

    def test_a_slide_whose_old_end_matches_no_track_is_reported(self):
        # reverse control for the case above: with an `old` end that matches no
        # track, the stale horizontal ray at (1,0) pairs with the stitch and the
        # 0 degree overlap is reported - so the pass above really comes from
        # dropping the slid track, not from the check never looking there.
        m = _Model([_t(1.0, 0.0, 2.4, 0.0, w=0.1),
                    _t(1.0, 0.0, 1.0, 0.9, w=0.1)])
        why = O._shape_local(
            m, NET,
            [(LA, 0.1, (1.0, 0.0), (1.4, 0.0))],
            [(LA, (2.0, 0.0), (2.4, 0.0), (9.0, 0.0))])
        self.assertIsNotNone(why)
        self.assertIn("0.0 deg corner at 1.000,0.000", why)

    def test_a_slide_that_lands_on_a_45_branch_is_refused(self):
        # at the NEW site the slid piece leaves along its fixed end; if that is
        # 45 degrees to copper already there, the site is refused there
        m = _Model([_t(2.0, 0.0, 2.0, 0.9, w=0.1),
                    _t(1.0, 0.0, 2.4, 0.0, w=0.1)])
        why = O._shape_local(
            m, NET, [],
            [(LA, (2.0, 0.0), (2.4, 0.0), (1.0, 0.0))])
        self.assertIsNone(why)  # 0 degrees to nothing: the track is collinear
        why = O._shape_local(
            m, NET, [],
            [(LA, (2.0, 0.0), (2.2, 0.2), (1.0, 0.0))])
        self.assertIsNotNone(why)
        self.assertIn("45.0 deg corner at 2.000,0.000", why)

    # -- tolerance: the gate folds a heading within one degree --

    def test_just_inside_the_tolerance_passes(self):
        # a stitch 89.5 degrees off the horizontal is folded to a right angle by
        # the gate, so the planner must not refuse it
        m = _Model([_t(0.0, 0.0, 1.0, 0.0, w=0.1)])
        a = math.radians(89.5)
        self.assertIsNone(O._shape_local(
            m, NET, [(LA, 0.1, (0.0, 0.0), (math.cos(a), math.sin(a)))], []))

    def test_just_outside_the_tolerance_fails(self):
        # reverse: 88 degrees is a real 2 degree acute and has to be refused
        m = _Model([_t(0.0, 0.0, 1.0, 0.0, w=0.1)])
        a = math.radians(88.0)
        why = O._shape_local(
            m, NET, [(LA, 0.1, (0.0, 0.0), (math.cos(a), math.sin(a)))], [])
        self.assertIsNotNone(why)
        self.assertIn("88.0 deg corner", why)

    # -- plumbing --

    def test_nothing_to_draw_is_not_a_violation(self):
        self.assertIsNone(O._shape_local(_Model([]), NET, [], []))

    def test_indexed_model_reads_the_same_tracks(self):
        tracks = [_t(11.4, 11.12, 11.8, 11.12),
                  _t(11.8, 11.12, 12.2, 11.52)]
        drawn = [(LA, 0.4, (11.8, 11.12), (12.0, 11.12))]
        self.assertEqual(
            O._shape_local(_Model(tracks), NET, drawn, []),
            O._shape_local(_Model(tracks, indexed=True), NET, drawn, []))

    def test_other_layers_are_not_mixed_in(self):
        # a 45 branch on F.Cu must not make an In2 stitch look acute
        m = _Model([_t(11.4, 11.12, 11.8, 11.12),
                    _t(11.8, 11.12, 12.2, 11.52, layer="F.Cu", w=0.2)])
        self.assertIsNone(O._shape_local(
            m, NET, [(LA, 0.4, (11.8, 11.12), (12.0, 11.12))], []))

    def test_other_nets_are_not_mixed_in(self):
        m = _Model([_t(11.4, 11.12, 11.8, 11.12),
                    _t(11.8, 11.12, 12.2, 11.52, net="/Y")])
        self.assertIsNone(O._shape_local(
            m, NET, [(LA, 0.4, (11.8, 11.12), (12.0, 11.12))], []))


if __name__ == "__main__":
    unittest.main()
