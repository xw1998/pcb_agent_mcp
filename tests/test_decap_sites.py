"""`decap_sites`: where a bypass capacitor may stand, and the two silent bugs.

Why this file exists: the module answered "a site is legal" and "the rail reaches
it" against an index it had quietly built wrong, and both wrong answers were in
the *optimistic* direction -- the kind of green that ships a short.

1. `parse_board` spells a via's width ``diameter``; `_Copper` read ``size``. Every
   via became a point, so every clearance answer in the module was short by a via
   radius (0.10-0.15 mm) and `_connection` would happily run a 0.2 mm stub
   0.05 mm from a via barrel. `_via_radius_mm` is the fix, and case 2 pins it.
2. `_Fill` keyed its 2D bucket grid at `BAND` (0.5 mm) but `edge_distance` walked
   rings of `CELL` (1.0 mm), so every distance query looked in cells that were
   never filled: a point 0.5 mm from a plane edge answered ``None``, which turned
   "a short run of copper reaches the flood" into "no copper of this net at all".
   Case 4 pins it.

Everything else here is a rule that has to fail differently from its neighbours:
a ring courtyard must stay a ring (not become the whole board, and not vanish),
a via may not sit in a stamp-hole frame or under a body, a 45-degree router either
produces 45-degree legs or names what stopped it, and a rail with no reachable
copper must come back with ``chosen=None`` and a reason rather than a placement.

No `pcbnew` here: the board is duck-typed, which is also what keeps these cases
fast enough to run on every commit.
"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))

from pcb_agent_mcp import decap_sites as D

SIDE = 40.0
EDGE = [(0.0, 0.0), (SIDE, 0.0), (SIDE, SIDE), (0.0, SIDE), (0.0, 0.0)]
STACK = [{"name": n} for n in ("F.Cu", "In1.Cu", "In2.Cu", "In3.Cu",
                               "In4.Cu", "B.Cu")]


def _frame(w=1.0, side=SIDE):
    """A courtyard written as four strips - the stamp-hole ring, in miniature."""
    i = side - w
    return [
        [(0.0, 0.0), (side, 0.0), (side, w), (0.0, w)],
        [(0.0, i), (side, i), (side, side), (0.0, side)],
        [(0.0, w), (w, w), (w, i), (0.0, i)],
        [(i, w), (side, w), (side, i), (i, i)],
    ]


def _bbox(poly):
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return {"x_min": min(xs), "y_min": min(ys),
            "x_max": max(xs), "y_max": max(ys)}


def _pad(ref, number, net, x, y, w=0.3, h=0.3, layers=("F.Cu",)):
    return {"ref": ref, "number": str(number), "net": net, "x": float(x),
            "y": float(y), "w": float(w), "h": float(h), "rotation": 0.0,
            "layers": list(layers), "shape": "rect", "hole_mm": None,
            "smd": True}


def _courtyard(contours):
    pts = [p for c in contours for p in c]
    return {"bbox": _bbox(pts) if pts else {"x_min": 0.0, "y_min": 0.0,
                                            "x_max": 0.0, "y_max": 0.0},
            "contours": contours}


def _fp(ref, x, y, pads, contours=None, value="x"):
    court = _courtyard(contours or [])
    return {"ref": ref, "value": value, "lib_id": "test:part", "x": float(x),
            "y": float(y), "rotation": 0.0,
            "courtyard": court, "bbox": court["bbox"], "pads": pads}


def _zone(net, poly, layers=("In1.Cu",), keepout=False):
    return {"net": net, "layers": list(layers), "is_keepout": keepout,
            "filled_polygons": [poly], "outline": [poly]}


class _Board(object):
    def __init__(self, footprints=(), tracks=(), vias=(), zones=(),
                 outline=None):
        self.outline = [outline or EDGE]
        self.outline_open = []
        self.stackup = list(STACK)
        self.footprints = list(footprints)
        self.tracks = list(tracks)
        self.vias = list(vias)
        self.zones = list(zones)
        self.graphics = []

    def pads(self):
        return [p for f in self.footprints for p in (f.get("pads") or [])]


def _square(cx, cy, half):
    return [(cx - half, cy - half), (cx + half, cy - half),
            (cx + half, cy + half), (cx - half, cy + half)]


# ------------------------------------------------------------ the via bug ---
class TestViaRadius(unittest.TestCase):
    """Three spellings of one width, and none of them may become zero."""

    def test_diameter_is_what_parse_board_writes(self):
        self.assertAlmostEqual(D._via_radius_mm({"diameter": 0.4}), 0.2)

    def test_size_is_what_the_edit_layer_writes(self):
        self.assertAlmostEqual(D._via_radius_mm({"size": 0.6}), 0.3)

    def test_neither_becomes_the_board_minimum_not_zero(self):
        # Zero is a clearance check that approves shorts, which is what the
        # `size`-only read produced once `parse_board` switched to `diameter`.
        self.assertAlmostEqual(D._via_radius_mm({}), D._VIA_DIA_MM / 2.0)

    def test_copper_reads_diameter_for_the_bucket(self):
        board = _Board(vias=[{"net": "N1", "x": 10.0, "y": 10.0,
                              "diameter": 0.2, "drill": 0.1}])
        cu = D._Copper(board)
        self.assertEqual(len(cu.vias), 1)
        self.assertAlmostEqual(cu.vias[0][3], 0.1)
        # The barrel is an obstacle with its radius, not a point: copper 0.15 mm
        # from the hole is 0.05 mm from the barrel.
        d, who = cu.nearest(10.15, 10.0, own=set(), layers=("F.Cu",))
        self.assertEqual(who[0], "via")
        self.assertAlmostEqual(d, 0.05, places=3)


# ----------------------------------------------------------- the cell bug ---
class TestFillIndex(unittest.TestCase):
    """`edge_distance` must answer at the granularity its rings walk.

    The polygon is deliberately away from the origin: while the cell grid was
    keyed at `BAND` (0.5 mm) and queried at `CELL` (1.0 mm), the two schemes
    still agreed in the first cell of each axis, so a square at the origin hides
    the bug behind a coincidence.
    """

    def setUp(self):
        self.fill = D._Fill(_square(20.0, 20.0, 5.0))     # (15,15)..(25,25)

    def test_point_half_a_millimetre_off_the_edge_is_half_a_millimetre(self):
        # This returned None before `_kc`, which read as "no copper at all".
        d = self.fill.edge_distance(15.5, 20.0, 1.0)
        self.assertIsNotNone(d)
        self.assertAlmostEqual(d, 0.5, places=6)

    def test_the_answer_does_not_depend_on_where_the_polygon_sits(self):
        for x, y in ((15.4, 20.0), (15.4, 16.4), (24.6, 23.6), (20.0, 24.7)):
            d = self.fill.edge_distance(x, y, 1.0)
            self.assertIsNotNone(d, "no edge distance at (%.1f, %.1f)" % (x, y))
            want = min(x - 15.0, 25.0 - x, y - 15.0, 25.0 - y)
            self.assertAlmostEqual(d, want, places=6)

    def test_point_far_from_the_edge_is_none_not_zero(self):
        self.assertIsNone(self.fill.edge_distance(20.0, 20.0, 2.0))

    def test_covers_inside_and_outside(self):
        self.assertTrue(self.fill.covers(20.0, 20.0))
        self.assertFalse(self.fill.covers(20.0, 26.0))

    def test_covers_agrees_with_a_plain_ray_cast(self):
        for x, y in ((15.1, 15.1), (24.9, 24.9), (15.1, 24.9), (19.999, 15.001)):
            self.assertEqual(self.fill.covers(x, y),
                             D._point_in_poly(x, y, _square(20.0, 20.0, 5.0)),
                             "cover disagrees at (%.3f, %.3f)" % (x, y))


# ----------------------------------------------------------- ring courtyards ---
class TestCourtyardShape(unittest.TestCase):
    def test_frame_survives_as_four_boxes_not_one_board_sized_bbox(self):
        fp = _fp("J1", 20.0, 20.0, [], _frame())
        boxes = D._courtyard_boxes(fp)
        self.assertEqual(len(boxes), 4)
        # The centre of the board is in none of them: a ring is a ring.
        for a, b, c, d in boxes:
            self.assertFalse(a <= 20.0 <= c and b <= 20.0 <= d,
                             "centre of the board is inside a frame strip")
        for a, b, c, d in boxes:
            self.assertTrue(min(c - a, d - b) <= 1.0 + 1e-9,
                            "a frame strip came back board-sized")

    def test_frame_area_is_the_frame_not_the_bbox(self):
        fp = _fp("J1", 20.0, 20.0, [], _frame())
        area = D._courtyard_area(fp)
        self.assertAlmostEqual(area, SIDE * SIDE - (SIDE - 2.0) ** 2, places=6)

    def test_bodies_keeps_the_frame_strips_and_drops_the_centre(self):
        board = _Board(footprints=[_fp("J1", 20.0, 20.0, [], _frame())])
        bodies = D._bodies(board)
        self.assertEqual(len(bodies), 4)
        free = D.site_ok(D._Copper(board), bodies, board, 20.0, 20.0, 0,
                         {"N1"}, D.footprint_shape())
        self.assertIsNone(free, "a 0402 may stand in the middle of a frame")
        inside = D.site_ok(D._Copper(board), bodies, board, 0.5, 20.0, 0,
                           {"N1"}, D.footprint_shape())
        self.assertIsNotNone(inside)

    def test_module_scale_courtyard_is_exempt_by_area(self):
        board = _Board(footprints=[_fp("U1", 20.0, 20.0, [],
                                       [[(0.0, 0.0), (SIDE, 0.0),
                                         (SIDE, SIDE), (0.0, SIDE)]])])
        self.assertEqual(D._bodies(board), [])


# --------------------------------------------------------------- the gate ---
class TestViaLegal(unittest.TestCase):
    def test_via_in_the_edge_keepout_is_refused(self):
        board = _Board()
        why = D._via_legal(board, [], 0.2, 20.0)
        self.assertIsNotNone(why)
        self.assertIn("edge keepout", why)

    def test_via_under_a_body_is_refused_and_the_annulus_counts(self):
        board = _Board()
        bodies = [("U2", (10.0, 10.0, 15.0, 15.0))]
        self.assertIsNotNone(D._via_legal(board, bodies, 12.0, 12.0))
        # Centre outside the box by 0.1 mm, copper still poking into it.
        why = D._via_legal(board, bodies, 9.9, 12.0, margin_mm=0.3)
        self.assertIsNotNone(why)
        self.assertIn("U2", why)

    def test_its_own_courtyard_is_not_an_obstacle(self):
        board = _Board()
        bodies = [("U1", (10.0, 10.0, 15.0, 15.0))]
        self.assertIsNone(D._via_legal(board, bodies, 12.0, 12.0, own_ref="U1"))

    def test_open_space_passes(self):
        self.assertIsNone(D._via_legal(_Board(), [], 20.0, 20.0))


# ------------------------------------------------------------------- 45 deg ---
class TestRoute45(unittest.TestCase):
    def _angles(self, run):
        out = []
        for i in range(len(run) - 1):
            dx = run[i + 1][0] - run[i][0]
            dy = run[i + 1][1] - run[i][1]
            out.append(round(math.degrees(math.atan2(dy, dx)) % 180.0, 3))
        return out

    def test_legs_are_only_0_45_90_135(self):
        board = _Board()
        cu = D._Copper(board)
        run, why = D._route45(cu, (10.0, 10.0), (13.0, 11.5), "N1",
                              ["F.Cu"], 0.2, 0.15)
        self.assertIsNotNone(run)
        self.assertEqual((run[0], run[-1]), ((10.0, 10.0), (13.0, 11.5)))
        for a in self._angles(run):
            self.assertIn(a, (0.0, 45.0, 90.0, 135.0))

    def test_every_shape_is_tried_so_an_L_is_not_the_only_answer(self):
        board = _Board()
        cu = D._Copper(board)
        run, _why = D._route45(cu, (10.0, 10.0), (13.0, 11.5), "N1",
                               ["F.Cu"], 0.2, 0.15)
        # dx=3, dy=1.5: a single right angle would need a 90-degree corner, which
        # is not in the legal set. The 45 has to be spent on the difference.
        self.assertIn(45.0, self._angles(run))

    def test_a_wall_is_refused_and_named(self):
        wall = {"net": "VCC", "layer": "F.Cu", "width": 0.2,
                "start": (11.0, 8.0), "end": (11.0, 12.0)}
        board = _Board(tracks=[wall])
        cu = D._Copper(board)
        run, why = D._route45(cu, (10.0, 10.0), (12.0, 10.0), "N1",
                              ["F.Cu"], 0.2, 0.15)
        self.assertIsNone(run)
        self.assertEqual(why["blocked_by"], ("seg", 0, "VCC"))
        self.assertLessEqual(why["clearance_mm"], 0.15)

    def test_own_net_is_not_an_obstacle(self):
        wall = {"net": "N1", "layer": "F.Cu", "width": 0.2,
                "start": (11.0, 8.0), "end": (11.0, 12.0)}
        cu = D._Copper(_Board(tracks=[wall]))
        run, _why = D._route45(cu, (10.0, 10.0), (12.0, 10.0), "N1",
                               ["F.Cu"], 0.2, 0.15)
        self.assertIsNotNone(run)


class TestCrosses(unittest.TestCase):
    def test_run_through_a_courtyard_is_caught(self):
        rects = [(11.0, 9.0, 11.5, 11.0)]
        self.assertTrue(D._crosses((10.0, 10.0), (12.0, 10.0), rects))
        self.assertFalse(D._crosses((10.0, 12.0), (12.0, 12.0), rects))

    def test_no_rects_is_never_a_crossing(self):
        self.assertFalse(D._crosses((0.0, 0.0), (5.0, 5.0), ()))


# ------------------------------------------------------------------ layout ---
class TestCapGeom(unittest.TestCase):
    def test_rotated_cap_puts_its_pads_on_the_other_axis(self):
        shp = D.footprint_shape()
        _c, p1, p2 = D._cap_geom(10.0, 10.0, 90, shp)
        self.assertAlmostEqual(p1[0], 10.0)
        self.assertAlmostEqual(p1[1], 10.0 - shp["pad_pitch"])
        self.assertAlmostEqual(p2[1], 10.0 + shp["pad_pitch"])

    def test_upright_cap_puts_its_pads_left_and_right(self):
        shp = D.footprint_shape()
        court, p1, p2 = D._cap_geom(10.0, 10.0, 0, shp)
        self.assertAlmostEqual(p1[0], 10.0 - shp["pad_pitch"])
        self.assertAlmostEqual(p2[0], 10.0 + shp["pad_pitch"])
        self.assertAlmostEqual(court[2] - court[0], 2 * shp["court_x"])

    def test_pad_layers_wildcards_count(self):
        self.assertTrue(D._on_layer({"layers": ["*.Cu"]}, "F.Cu"))
        self.assertTrue(D._on_layer({"layers": ["*.Cu"]}, "In3.Cu"))
        self.assertFalse(D._on_layer({"layers": ["F.Cu"]}, "In1.Cu"))
        self.assertTrue(D._on_layer({"layers": ["F&B.Cu"]}, "B.Cu"))


# --------------------------------------------------------- ground escape ---
class TestGndEscape(unittest.TestCase):
    def setUp(self):
        D.reset_fill_cache()

    def _board(self, zones):
        pads = [_pad("U1", 1, "N1", 20.0, 20.0),
                _pad("U1", 2, "/GND", 17.0, 20.0)]
        return _Board(footprints=[_fp("U1", 20.0, 20.0, pads,
                                      [_square(20.0, 20.0, 2.0)])],
                      zones=zones)

    def test_no_return_flood_on_the_plane_layer_is_named(self):
        board = self._board([])
        gnd, why = D._gnd_escape(board, D._Copper(board), (17.0, 20.0))
        self.assertIsNone(gnd)
        self.assertIn("no filled /GND flood on In1", why["why"])

    def test_reach_shorter_than_the_stub_finds_nothing_rather_than_a_drill_in_the_pad(self):
        board = self._board([_zone("/GND", _square(20.0, 20.0, 18.0))])
        gnd, why = D._gnd_escape(board, D._Copper(board), (17.0, 20.0),
                                 reach_mm=0.5)
        self.assertIsNone(gnd)
        self.assertIn("within 0.5 mm", why["why"])

    def test_via_lands_outside_the_pad_and_inside_the_flood(self):
        board = self._board([_zone("/GND", _square(20.0, 20.0, 18.0))])
        gnd, why = D._gnd_escape(board, D._Copper(board), (17.0, 20.0))
        self.assertIsNotNone(gnd, why)
        vx, vy = gnd["via_at"]
        far = math.hypot(vx - 17.0, vy - 20.0)
        self.assertGreaterEqual(far, D._STUB_MM - 1e-9,
                               "via %.3f mm from the pad is a via in the pad"
                               % far)
        self.assertLessEqual(far, D._GND_ESCAPE_MM + 1e-9)
        self.assertEqual(gnd["layer"], "In1.Cu")

    def test_a_foreign_pad_under_every_candidate_blocks_the_via(self):
        # A 4 mm ring of foreign copper around the ground pad: no stub point can
        # hold a drill without cutting somebody else's pad.
        blocker = []
        for k in range(64):
            a = 2.0 * math.pi * k / 64.0
            blocker.append(_pad("U2", k, "VCC",
                                17.0 + 1.0 * math.cos(a),
                                20.0 + 1.0 * math.sin(a), w=0.8, h=0.8))
        pads = [_pad("U1", 2, "/GND", 17.0, 20.0)]
        board = _Board(footprints=[_fp("U1", 17.0, 20.0, pads, []),
                                   _fp("U2", 17.0, 20.0, blocker, [])],
                       zones=[_zone("/GND", _square(20.0, 20.0, 18.0))])
        gnd, why = D._gnd_escape(board, D._Copper(board), (17.0, 20.0),
                                 reach_mm=1.5)
        self.assertIsNone(gnd, "a via was placed on top of a foreign pad")
        self.assertIn("no point", why["why"])


# ------------------------------------------------------------- connection ---
class TestConnection(unittest.TestCase):
    """Three ways a rail pad reaches its rail, and one way it does not."""

    def setUp(self):
        # `_fills` is keyed on `id(board)`; the boards here are short-lived, so
        # the index has to be dropped between cases or one board's flood is
        # answered with another's.
        D.reset_fill_cache()

    def _board(self, tracks=(), zones=()):
        pads = [_pad("U1", i, "+3V3", 2.0, 5.0 + i) for i in range(1, 5)]
        return _Board(footprints=[_fp("U1", 3.0, 6.0, pads,
                                      [_square(3.0, 6.0, 2.5)])],
                      tracks=tracks, zones=zones)

    def test_far_copper_is_refused_by_max_track_not_reported_as_connected(self):
        far = {"net": "+3V3", "layer": "F.Cu", "width": 0.2,
               "start": (15.0, 5.0), "end": (15.0, 9.0)}      # 3 mm from x=12
        board = self._board(tracks=[far])
        cu = D._Copper(board)
        tight = D._connection(board, cu, "+3V3", (12.0, 5.0), max_track_mm=1.5)
        self.assertEqual(tight["kind"], "none")
        loose = D._connection(board, cu, "+3V3", (12.0, 5.0), max_track_mm=5.0)
        self.assertEqual(loose["kind"], "copper")
        self.assertGreater(loose["distance_mm"], 1.5)

    def test_no_copper_at_all_says_none_with_a_reason(self):
        board = self._board()
        conn = D._connection(board, D._Copper(board), "+3V3", (12.0, 5.0))
        self.assertEqual(conn["kind"], "none")
        self.assertIn("no filled zone", conn["why"])

    def test_flood_under_the_pad_puts_the_via_a_stub_outside_it(self):
        board = self._board(zones=[_zone("+3V3", _square(10.0, 10.0, 12.0),
                                         layers=("In3.Cu",))])
        conn = D._connection(board, D._Copper(board), "+3V3", (12.0, 5.0))
        self.assertEqual(conn["kind"], "plane")
        self.assertTrue(conn["under_pad"])
        vx, vy = conn["via_at"]
        # The whole point of `_STUB_MM`: the drill never enters the land.
        self.assertGreaterEqual(math.hypot(vx - 12.0, vy - 5.0),
                                D._STUB_MM - 1e-9)

    def test_a_flood_beside_the_pad_is_preferred_over_a_long_run(self):
        board = self._board(zones=[_zone("+3V3", _square(14.0, 5.0, 1.0),
                                         layers=("In3.Cu",))])
        conn = D._connection(board, D._Copper(board), "+3V3", (12.0, 5.0))
        self.assertEqual(conn["kind"], "plane")
        self.assertFalse(conn["under_pad"])


# -------------------------------------------------------------- the whole ---
class TestFillRefuses(unittest.TestCase):
    """A rail whose copper does not reach any legal site gets no part landed."""

    def setUp(self):
        D.reset_fill_cache()

    def _board(self):
        # Every rail pad sits deep inside a 20 mm courtyard, so the nearest place
        # a part may stand is ~9 mm away and nothing of the net is out there.
        pads = [_pad("U1", i, "+3V3", 19.0 + (i % 2) * 1.0,
                     19.0 + (i // 2) * 1.0) for i in range(1, 9)]
        pads += [_pad("U1", 9, "/GND", 19.0, 21.5)]
        return _Board(footprints=[_fp("U1", 20.0, 20.0, pads,
                                      [_square(20.0, 20.0, 10.0)])],
                      zones=[_zone("/GND", _square(20.0, 20.0, 19.0))])

    def test_unreachable_rail_comes_back_with_no_chosen_part(self):
        board = self._board()
        res = D.fill(board, nets=["+3V3"])
        self.assertIn("+3V3", res["rails"])
        info = res["rails"]["+3V3"]
        self.assertIsNone(info.get("chosen"),
                          "a part was landed on a rail with no reachable copper")
        self.assertTrue(info.get("refused") or info.get("error"),
                        "the refusal carries no reason: %r" % (info,))

    def test_plan_reports_the_same_rail_as_unconnectable_per_site(self):
        board = self._board()
        info = D.plan(board, nets=["+3V3"], top=2)["rails"]["+3V3"]
        for row in info.get("sites") or []:
            self.assertEqual(row["connection"]["kind"], "none")


# ------------------------------------------------------- how far, which way ---
class TestFillShortfall(unittest.TestCase):
    """A refused site must say how much copper is missing and in which direction.

    "connection: none" used to be one sentence for two different situations:
    "the flood is 1.5 mm short of here" and "there is no copper of this net
    anywhere near". Only the first is actionable, and telling them apart by hand
    meant reading polygon vertices - which is the step this probe removes.
    """

    def setUp(self):
        D.reset_fill_cache()

    def _board(self, zone_square, layers=("In3.Cu",)):
        pads = [_pad("U1", i, "+3V3", 2.0, 5.0 + i) for i in range(1, 5)]
        return _Board(footprints=[_fp("U1", 3.0, 6.0, pads,
                                      [_square(3.0, 6.0, 2.5)])],
                      zones=[_zone("+3V3", zone_square, layers=layers)])

    def test_the_distance_is_measured_from_the_stub_point_not_the_pad(self):
        # Flood's left edge at x = 19, rail pad at (12, 5), reach 6 mm. The plane
        # is 7 mm from the pad - out of reach - but only 6.35 mm from the stub
        # point the via would occupy, and that is the point a patch must cover.
        board = self._board(_square(24.0, 5.0, 5.0))
        conn = D._connection(board, D._Copper(board), "+3V3", (12.0, 5.0))
        self.assertEqual(conn["kind"], "none")
        sf = conn["fill_shortfall"]
        self.assertEqual(sf["at"], "stub")
        self.assertAlmostEqual(sf["from"][0], 12.0 + D._STUB_MM, places=6)
        self.assertAlmostEqual(sf["distance_mm"], 19.0 - (12.0 + D._STUB_MM), places=3)
        self.assertAlmostEqual(sf["pad_distance_mm"], 7.0, places=3)
        self.assertEqual(sf["to"], [19.0, 5.0])
        self.assertAlmostEqual(sf["grow_mm"], sf["distance_mm"] + D._VIA_DIA_MM / 2.0,
                               places=3)
        self.assertIn("In3.Cu", sf["layers"])

    def test_a_flood_beyond_the_probe_is_not_reported_as_a_number(self):
        # 25 mm away: outside the 8 mm probe. The answer has to be "I did not
        # find it that far out", never a guess dressed as a measurement.
        board = self._board(_square(45.0, 5.0, 5.0))
        conn = D._connection(board, D._Copper(board), "+3V3", (12.0, 5.0))
        self.assertEqual(conn["kind"], "none")
        sf = conn["fill_shortfall"]
        self.assertNotIn("distance_mm", sf)
        self.assertEqual(sf["probe_mm"], D._FILL_PROBE_MM)
        self.assertIn("no flood", sf["note"])

    def test_edge_nearest_agrees_with_brute_force_on_a_crooked_polygon(self):
        # Concave, off-axis, and not centred on a cell boundary: an index that
        # only looks in the query's own cell has to get this wrong.
        poly = [(10.0, 10.0), (20.0, 10.0), (20.0, 13.0), (13.5, 13.0),
                (13.5, 17.0), (11.0, 17.0), (11.0, 12.3), (10.0, 12.3)]
        fill = D._Fill(poly)

        def brute(x, y):
            best = None
            n = len(poly)
            for i in range(n):
                x1, y1 = poly[i]
                x2, y2 = poly[(i + 1) % n]
                d = D._pt_seg(x, y, x1, y1, x2, y2)
                if best is None or d < best:
                    best = d
            return best

        for pt in ((6.0, 6.0), (12.7, 9.2), (21.5, 15.0), (9.1, 14.9),
                   (14.9, 20.0), (12.2, 14.1), (19.0, 12.0)):
            hit = fill.edge_nearest(pt[0], pt[1], 40.0)
            self.assertIsNotNone(hit, "edge_nearest lost a point inside its probe")
            self.assertAlmostEqual(hit[0], brute(*pt), places=9,
                                   msg="index disagrees with brute force at %r" % (pt,))

    def test_fill_puts_the_shortfall_in_the_refusal(self):
        # A rail that cannot be reached at all still has to answer "how far":
        # courtyard half 7 mm puts every legal site ~7 mm from a flood that sits
        # at the part itself, i.e. past `reach` but inside the probe.
        pads = [_pad("U1", i, "+3V3", 19.0 + (i % 2), 19.0 + (i // 2) * 1.0)
                for i in range(1, 9)]
        pads += [_pad("U1", 9, "/GND", 19.0, 21.5)]
        board = _Board(footprints=[_fp("U1", 20.0, 20.0, pads,
                                       [_square(20.0, 20.0, 7.0)])],
                       zones=[_zone("/GND", _square(20.0, 20.0, 19.0)),
                              _zone("+3V3", _square(20.0, 20.0, 0.5),
                                    layers=("In3.Cu",))])
        info = D.fill(board, nets=["+3V3"])["rails"]["+3V3"]
        self.assertIsNone(info.get("chosen"))
        self.assertIn("grow", info.get("refused") or "")
        sf = info.get("refused_shortfall") or {}
        self.assertIsNotNone(sf.get("distance_mm"),
                             "the refusal dropped the one number that says how "
                             "much copper is missing: %r" % (info,))
        self.assertGreater(sf["distance_mm"], D._REACH_MM)
        self.assertLessEqual(sf["distance_mm"], D._FILL_PROBE_MM)

if __name__ == "__main__":
    unittest.main()
