"""`power_loop`: the five legs of a buck cell, and the four answers that differ.

Why this file exists: "wire up the switching regulator" has four outcomes that a
router usually reports as one. A leg can close (`closed`), it can have no copper
at all (`blocked`, with the name of whatever stopped it), it can have legal
copper at a price that is a layout defect (`over_budget`, with that price as a
number), or the cell can be missing the part that leg belongs to (`missing`).
Merging any two of them ships a board that looks wired.

The cases below pin the four apart, and pin the three rules that decide which one
applies:

1. The inductor is found by net topology (the footprint on the switch node whose
   other side is not a control net and not ground), not by distance. A 0402
   bootstrap capacitor stands closer than a 4.3 uH inductor, so "nearest" routes
   the whole inductor current through a 100 nF part.
2. A switch-node run over the hard budget is not emitted. 13 mm of switch node is
   not a buck, it is an antenna, and emitting it would hide the placement defect
   behind a green count.
3. Copper does not cross the middle of a magnetic. It may cross the courtyard to
   reach that magnetic's own pad - the pad stands under the body edge - so the
   rule is a length of track inside the body that is not on a pad, not "never
   touch the courtyard".

No `pcbnew` here: the boards are duck-typed, which is also what keeps these cases
fast enough to run on every commit.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))

from pcb_agent_mcp import decap_sites as D
from pcb_agent_mcp import power_loop as P

SIDE = 60.0
EDGE = [(0.0, 0.0), (SIDE, 0.0), (SIDE, SIDE), (0.0, SIDE), (0.0, 0.0)]
STACK = [{"name": n} for n in ("F.Cu", "In1.Cu", "In2.Cu", "In3.Cu",
                               "In4.Cu", "B.Cu")]


_SAVED = {}


def setUpModule():
    """Run the searches on a coarse grid, because the fixtures are 10 mm cells.

    The production steps exist for a 45 mm board with a 0.41 mm corridor between
    a buck IC and its divider - the corridor is what needs 0.25 mm. Nothing here
    has a corridor that narrow, and a test that spends four minutes per case
    proving the search is slow teaches nothing. What is under test is which of
    the four answers comes back, so the geometry stays and the step gets coarser.
    """
    for name, value in (("_DETOUR_STEP_MM", 0.5), ("_DETOUR_COARSE_MM", 1.0),
                        ("_ESCAPE_MM", (1.6,))):
        _SAVED[name] = getattr(P, name)
        setattr(P, name, value)


def tearDownModule():
    for name, value in _SAVED.items():
        setattr(P, name, value)


def _pad(ref, number, net, x, y, w=0.8, h=0.532, layers=("F.Cu",)):
    return {"ref": ref, "number": str(number), "net": net, "x": float(x),
            "y": float(y), "w": float(w), "h": float(h), "rotation": 0.0,
            "layers": list(layers), "shape": "rect", "hole_mm": None,
            "smd": True}


def _square(cx, cy, half):
    return [(cx - half, cy - half), (cx + half, cy - half),
            (cx + half, cy + half), (cx - half, cy + half)]


def _fp(ref, x, y, pads, half=0.3):
    poly = _square(x, y, half)
    bbox = {"x_min": x - half, "y_min": y - half,
            "x_max": x + half, "y_max": y + half}
    court = {"bbox": bbox, "contours": [poly]}
    return {"ref": ref, "value": ref, "lib_id": "test:part", "x": float(x),
            "y": float(y), "rotation": 0.0, "courtyard": court, "bbox": bbox,
            "pads": pads}


def _rect(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]

def _zone(net, poly, layers=("In1.Cu",)):
    return {"net": net, "layers": list(layers), "is_keepout": False,
            "filled_polygons": [poly], "outline": [poly]}


class _Board(object):
    def __init__(self, footprints=(), tracks=(), vias=(), zones=()):
        self.outline = [EDGE]
        self.outline_open = []
        self.stackup = list(STACK)
        self.footprints = list(footprints)
        self.tracks = list(tracks)
        self.vias = list(vias)
        self.zones = list(zones)
        self.graphics = []

    def pads(self):
        return [p for f in self.footprints for p in (f.get("pads") or [])]


#: The buck cell this file measures. One IC, one inductor, two capacitors, a
#: bootstrap capacitor and a divider - the six parts `_legs_for` knows by role.
VIN, SW, BS, FB, OUT, GND = ("/+5V_IN", "/SW_U9", "/BS_U9", "/FB_U9",
                             "/+1V2", "/GND")


def _ic(x=10.0, y=20.0):
    pads = [_pad("U9", 4, VIN, x - 1.2, y + 0.95),
            _pad("U9", 5, VIN, x - 1.2, y),
            _pad("U9", 6, SW, x - 1.2, y - 0.95),
            _pad("U9", 1, BS, x + 1.2, y - 0.95),
            _pad("U9", 2, GND, x + 1.2, y),
            _pad("U9", 3, FB, x + 1.2, y + 0.95)]
    return _fp("U9", x, y, pads, half=1.7)


def _inductor(x=10.0, y=15.5, court=1.9):
    pads = [_pad("L9", 1, SW, x - 1.2, y, w=1.6, h=3.0),
            _pad("L9", 2, OUT, x + 1.2, y, w=1.6, h=3.0)]
    return _fp("L9", x, y, pads, half=court)


def _cell(inductor_y=15.5, boot_at=(12.4, 19.05)):
    cin = _fp("C1", 7.0, 20.0,
              [_pad("C1", 1, VIN, 7.0, 20.0, w=0.56, h=0.62),
               _pad("C1", 2, GND, 7.0, 20.96, w=0.56, h=0.62)])
    cout = _fp("C2", 13.0, 15.5,
               [_pad("C2", 1, OUT, 13.0, 14.97, w=0.56, h=0.62),
                _pad("C2", 2, GND, 13.0, 15.93, w=0.56, h=0.62)])
    bx, by = boot_at
    boot = _fp("C3", bx, by,
               [_pad("C3", 1, BS, bx, by, w=0.56, h=0.62),
                _pad("C3", 2, SW, bx, by + 0.96, w=0.56, h=0.62)])
    rtop = _fp("R1", 12.0, 22.0,
               [_pad("R1", 1, OUT, 11.2, 22.0, w=0.54, h=0.64),
                _pad("R1", 2, FB, 12.2, 22.0, w=0.54, h=0.64)])
    rbot = _fp("R2", 13.2, 22.0,
               [_pad("R2", 1, FB, 12.2, 22.0, w=0.54, h=0.64),
                _pad("R2", 2, GND, 13.2, 22.0, w=0.54, h=0.64)])
    return [_ic(), _inductor(y=inductor_y), cin, cout, boot, rtop, rbot]


def _planes():
    big = _square(30.0, 30.0, 29.0)
    return [_zone(GND, big, ("In1.Cu",)),
            _zone(VIN, big, ("In3.Cu",)),
            _zone(OUT, big, ("In3.Cu",))]


class TestCells(unittest.TestCase):
    """Which part is which, decided by nets and not by proximity."""

    def test_the_six_roles_are_found(self):
        cs = P.cells(_Board(footprints=_cell()))
        self.assertEqual(list(cs), ["U9"])
        c = cs["U9"]
        self.assertEqual(c["inductor"], "L9")
        self.assertEqual(c["sw_net"], SW)
        self.assertEqual(c["out_net"], OUT)
        self.assertEqual((c["cin"], c["cout"], c["boot"], c["rtop"],
                          c["rbot"]), ("C1", "C2", "C3", "R1", "R2"))
        self.assertEqual(c["missing"], [])

    def test_the_bootstrap_capacitor_is_not_mistaken_for_the_inductor(self):
        # C3 hangs off the switch node 0.96 mm from the switch pin, and L9 is
        # 4 mm away. Distance would pick the 100 nF part and route the inductor
        # current through it; the net on its other side is what rules it out.
        c3 = [f for f in _cell() if f["ref"] == "C3"][0]
        self.assertIn(SW, [p["net"] for p in c3["pads"]])
        c = P.cells(_Board(footprints=_cell()))["U9"]
        self.assertEqual(c["inductor"], "L9")
        self.assertNotIn("C3", c.get("also_on_sw") or [])

    def test_a_missing_part_is_named_rather_than_guessed(self):
        parts = [f for f in _cell() if f["ref"] != "C3"]
        c = P.cells(_Board(footprints=parts))["U9"]
        self.assertIn({"role": "boot", "nets": [BS, SW]}, c["missing"])

    def test_no_inductor_is_an_error_and_not_a_quiet_skip(self):
        parts = [f for f in _cell() if f["ref"] != "L9"]
        cs = P.cells(_Board(footprints=parts))
        self.assertIn("error", cs["U9"])


class TestLegs(unittest.TestCase):
    """The legs a cell owes, with the width that belongs to its current."""

    def setUp(self):
        self.board = _Board(footprints=_cell())
        self.cell = P.cells(self.board)["U9"]
        self.by_ref, _nets = P._refs(self.board)
        self.legs = {l["name"]: l for l in
                     P._legs_for(self.cell, self.by_ref, VIN, GND)}

    def test_every_leg_is_present(self):
        self.assertEqual(sorted(self.legs), ["boot_bs", "boot_sw", "fb_div",
                                             "fb_top", "out", "sense", "sw",
                                             "vin_cap", "vin_ic"])

    def test_widths_follow_the_current_not_the_part(self):
        self.assertEqual(self.legs["sw"]["width_mm"], 0.6)
        self.assertEqual(self.legs["out"]["width_mm"], 0.6)
        self.assertEqual(self.legs["sense"]["width_mm"], 0.25)
        self.assertEqual(self.legs["fb_top"]["width_mm"], 0.25)

    def test_the_switch_node_is_the_ic_pin_to_the_inductor_pin(self):
        sw = self.legs["sw"]
        self.assertEqual(tuple(sw["from"]), (8.8, 19.05))
        self.assertEqual(tuple(sw["to"]), (8.8, 15.5))

    def test_the_sense_line_taps_the_output_capacitor(self):
        # The divider asks what the load sees; the inductor's own pad is inside
        # the magnetic, where the only way out is the gap between its two pads.
        self.assertAlmostEqual(self.legs["sense"]["from"][0], 13.0)
        self.assertAlmostEqual(self.legs["sense"]["from"][1], 14.97)


class TestMagneticRule(unittest.TestCase):
    """Crossing a part is a length, and a length can be measured.

    The fixture mirrors the board: two pads that cover almost the whole body and
    a gap between them. That gap is the part - it is where the winding sits, and
    it is 2 mm wide here because the real one is 1 mm and a test that only passes
    at 1 mm would be measuring the sampler.
    """

    BOX = (0.0, 0.0, 10.0, 10.0)
    PADS = [(0.0, -0.5, 4.0, 10.5), (6.0, -0.5, 10.0, 10.5)]
    BODIES = [("L1", BOX, PADS)]

    def test_a_run_through_the_middle_is_refused_and_named(self):
        hit = P._run_hits([(0.0, 5.0), (10.0, 5.0)], 0.3, self.BODIES, 0.15)
        self.assertEqual(hit, "L1")

    def test_a_run_beside_the_part_is_allowed(self):
        self.assertIsNone(P._run_hits([(0.0, 11.0), (10.0, 11.0)], 0.3,
                                      self.BODIES, 0.15))

    def test_a_run_onto_the_pad_may_cross_the_outline(self):
        # Getting to the inductor's own pad means crossing its courtyard: the pad
        # stands under the body edge, so 5 mm of centreline inside the body, all
        # of it on the pad, is not a track under the part.
        self.assertIsNone(P._run_hits([(1.0, -1.5), (1.0, 5.0)], 0.3,
                                      self.BODIES, 0.15))

    def test_the_gap_between_two_pads_is_the_part(self):
        # Both ends touch a pad and the track is still under the winding.
        hit = P._run_hits([(3.0, 5.0), (7.0, 5.0)], 0.3, self.BODIES, 0.15)
        self.assertEqual(hit, "L1")

    def test_the_length_decides_not_the_endpoints(self):
        # Two runs, both entirely inside the body, both endpoints on nothing in
        # particular: only their length in the gap separates them.
        short = P._run_hits([(4.2, 5.0), (4.6, 5.0)], 0.3, self.BODIES, 0.15)
        long_ = P._run_hits([(4.2, 5.0), (4.8, 5.0)], 0.3, self.BODIES, 0.15)
        self.assertIsNone(short)
        self.assertEqual(long_, "L1")


_PLANS = {}


class TestPlan(unittest.TestCase):
    """The four answers, kept apart."""

    def setUp(self):
        D.reset_fill_cache()

    def _plan(self, footprints=None, zones=None):
        # One plan per fixture, shared by every case that reads it: `plan` runs
        # the detour search on the legs that need it, and the same numbers are
        # the subject of five cases.
        key = (id(footprints), id(zones))
        if key not in _PLANS:
            board = _Board(footprints=footprints or _cell(),
                           zones=_planes() if zones is None else zones)
            _PLANS[key] = (board, P.plan(board))
        return _PLANS[key]

    def test_the_cell_closes_its_power_legs(self):
        _b, out = self._plan()
        legs = out["cells"]["U9"]["legs"]
        for name in ("vin_ic", "vin_cap", "sw", "out", "boot_bs", "fb_div",
                     "fb_top", "gnd_ic_2", "gnd_cout_2"):
            self.assertEqual(legs[name]["status"], "closed", name)
        self.assertEqual(legs["sw"]["length_mm"], 3.55)
        self.assertEqual(legs["gnd_ic_2"]["length_mm"], 0.65)

    def test_a_refused_leg_emits_no_copper(self):
        _b, out = self._plan()
        for rec in out["refused"]:
            self.assertNotIn(rec["leg"], ())  # refused legs are named
        for cell in out["cells"].values():
            for name, leg in cell["legs"].items():
                if leg["status"] != "closed":
                    self.assertNotIn("run", leg)
                    self.assertIn("why", leg)
        self.assertEqual(out["summary"]["closed"] + out["summary"]["blocked"],
                         out["summary"]["legs"])

    def test_every_op_is_a_track_or_a_via_with_a_net(self):
        _b, out = self._plan()
        self.assertTrue(out["ops"])
        for op in out["ops"]:
            self.assertIn(op["op"], ("add_track", "add_via"))
            self.assertTrue(op["net"].startswith("/"))
        self.assertEqual(out["summary"]["tracks"],
                         sum(1 for o in out["ops"] if o["op"] == "add_track"))
        self.assertEqual(out["summary"]["vias"],
                         sum(1 for o in out["ops"] if o["op"] == "add_via"))

    def test_a_switch_node_over_the_hard_budget_is_priced_and_not_emitted(self):
        # The inductor moved 13 mm down the board: legal copper exists and it is
        # 15 mm long. That is a placement defect, and the answer is the price.
        _b, out = self._plan(footprints=_cell(inductor_y=4.5))
        sw = out["cells"]["U9"]["legs"]["sw"]
        self.assertEqual(sw["status"], "over_budget")
        self.assertGreater(sw["legal_mm"], sw["budget_mm"])
        self.assertEqual(sw["budget_mm"], P._SW_HARD_MM)
        self.assertFalse([o for o in out["ops"] if o["net"] == SW])

    def test_a_blocked_leg_names_what_stopped_it(self):
        # No ground plane at all: every stitching via is refused, and "no filled
        # /GND flood" is what the refusal says.
        _b, out = self._plan(zones=[])
        leg = out["cells"]["U9"]["legs"]["gnd_ic_2"]
        self.assertEqual(leg["status"], "blocked")
        self.assertIn("no filled /GND flood", leg["why"])

    def test_a_rail_with_no_flood_comes_back_with_the_distance(self):
        # A zone that exists but is 20 mm away: the number is the answer, because
        # a bus across the board is what closing this rail would take.
        far = _zone(VIN, _rect(14.0, 12.0, 19.0, 24.0), ("In3.Cu",))
        _b, out = self._plan(zones=[z for z in _planes() if z["net"] != VIN]
                             + [far])
        rail = out["cells"]["U9"]["legs"]["vin_rail"]
        self.assertEqual(rail["status"], "blocked")
        self.assertIn("bus has to be drawn", rail["why"])
        self.assertIn("mm away", rail["why"])
        self.assertGreater(rail["fill_shortfall"]["distance_mm"], 5.0)

    def test_a_rail_on_a_net_with_no_zone_at_all_says_so(self):
        _b, out = self._plan(zones=[z for z in _planes() if z["net"] != VIN])
        rail = out["cells"]["U9"]["legs"]["vin_rail"]
        self.assertEqual(rail["status"], "blocked")
        self.assertIn("defines no zone", rail["why"])

    def test_a_missing_part_is_refused_by_name(self):
        parts = [f for f in _cell() if f["ref"] != "C3"]
        _b, out = self._plan(footprints=parts)
        reasons = [r for r in out["refused"] if r["leg"] == "boot"]
        self.assertTrue(reasons)
        self.assertIn("no footprint bridges", reasons[0]["reason"])

    def test_the_plan_says_which_layer_and_widths_it_used(self):
        _b, out = self._plan()
        self.assertEqual(out["rules"]["layer"], "F.Cu")
        self.assertEqual(out["rules"]["width_mm"]["sw"], 0.6)
        self.assertIn("switch-node run over", out["rules"]["note"])


class TestDetour(unittest.TestCase):
    """When the direct run has nowhere to bend, the search steps out first."""

    def test_a_pin_walled_in_by_its_own_package_still_finds_copper(self):
        # p sits at the bottom of a U-shaped wall; q is straight above it. The
        # only 45-degree shape `_route45` can draw from p cuts the corner into
        # the wall on both sides, so the answer has to come from stepping out.
        walls = [{"net": None, "start": [4.0, 6.0], "end": [8.0, 6.0],
                  "width": 1.0, "layer": "F.Cu"},
                 {"net": None, "start": [4.0, 6.0], "end": [4.0, 11.0],
                  "width": 1.0, "layer": "F.Cu"},
                 {"net": None, "start": [8.0, 6.0], "end": [8.0, 11.0],
                  "width": 1.0, "layer": "F.Cu"}]
        board = _Board(tracks=walls)
        cu = D._Copper(board)
        run, info, _wp = P._route_any(cu, (6.0, 5.0), (6.0, 13.0), "/N", 0.2, 0.15)
        self.assertIsNotNone(run, info)
        self.assertTrue(info.get("detour"))
        self.assertIn("escape", info.get("search", ""))

    def test_the_direct_run_is_preferred_when_there_is_one(self):
        board = _Board()
        cu = D._Copper(board)
        run, info, wp = P._route_any(cu, (2.0, 2.0), (12.0, 2.0), "/N", 0.2, 0.15)
        self.assertEqual(run, [(2.0, 2.0), (12.0, 2.0)])
        self.assertIsNone(wp)
        self.assertNotIn("detour", info)


class TestPlaneRefusal(unittest.TestCase):
    """A rail that cannot reach its plane says *why*, and the why is not always distance.

    Three different boards produce three different refusals, and telling them
    apart is the whole point: "the flood is 0.33 mm away, draw a bus" was emitted
    once for a board whose flood was 0.33 mm away **and entirely under the
    inductor**, where no via may be drilled. The reader of that refusal would
    draw a bus that cannot help.
    """

    PAD = (13.0, 14.97)  # C2's OUT pad in `_cell()`

    def setUp(self):
        D.reset_fill_cache()

    def _leg(self, zones, net=OUT, pad=None):
        board = _Board(footprints=_cell(), zones=zones)
        got, why = P._plane_leg(board, D._Copper(board), D._bodies(board),
                                pad or self.PAD, net, "C2", D._STUB_MM,
                                D._CLEARANCE_MM, D._VIA_DIA_MM)
        self.assertIsNone(got)
        return why

    def test_a_zone_that_was_drawn_and_never_poured_says_exactly_that(self):
        # The outline exists, the pour data does not. "No flood" would hide a
        # one-command fix behind a layout decision.
        zones = [z for z in _planes() if z["net"] != OUT]
        zones.append({"net": OUT, "layers": ["In3.Cu"], "is_keepout": False,
                      "filled_polygons": [], "outline": [_square(11.0, 15.0, 2.0)]})
        why = self._leg(zones)
        self.assertIn("zone outline", why["why"])
        self.assertIn("never filled", why["why"])
        self.assertIn("In3.Cu", why["why"])

    def test_a_net_with_no_zone_at_all_is_not_reported_as_an_unpoured_one(self):
        zones = [z for z in _planes() if z["net"] != OUT]
        why = self._leg(zones)
        self.assertIn("defines no zone", why["why"])
        self.assertNotIn("never filled", why["why"])

    def test_a_flood_under_a_body_is_named_as_a_body_not_as_a_distance(self):
        # The pour stops 1.6 mm short of the pad and every point of it inside
        # that reach is inside L9's courtyard. Real copper, real distance, and
        # still nowhere to drill: the refusal has to say L9.
        zones = [z for z in _planes() if z["net"] != OUT]
        zones.append(_zone(OUT, _rect(10.8, 14.0, 11.4, 16.0), ("In3.Cu",)))
        why = self._leg(zones)
        self.assertIn("L9's body", why["why"])
        self.assertNotIn("bus has to be drawn", why["why"])
        self.assertEqual(why["plane"]["blocked_by"], "body:L9")
        self.assertGreater(why["plane"]["candidates"], 0)

    def test_the_distance_is_taken_from_the_stub_point_not_from_the_reach(self):
        # A via may not sit in the pad, so the gap that matters is measured from
        # 0.65 mm out, not from 2.0 mm out. The pour edge is at x=10.0 and the
        # pad at x=13.0: 3.00 mm from the pad, 2.35 mm from its stub ring.
        zones = [z for z in _planes() if z["net"] != OUT]
        zones.append(_zone(OUT, _rect(4.0, 12.0, 10.0, 18.0), ("In3.Cu",)))
        why = self._leg(zones)
        self.assertIn("2.35 mm", why["why"])
        self.assertNotIn("3.00 mm", why["why"])
        self.assertEqual(why["fill_shortfall"]["at"], "stub")
        self.assertAlmostEqual(why["fill_shortfall"]["pad_distance_mm"], 3.0,
                               places=3)


SIG = "/LCDC_CLK"

def _track(net, layer, x1, y1, x2, y2, width=0.2):
    return {"net": net, "layer": layer, "start": (x1, y1), "end": (x2, y2),
            "width": float(width)}


class TestThroughVia(unittest.TestCase):
    """A via is a hole through the whole stackup, so it must clear every layer.

    Why this file has a class about it: `_gnd_escape` was called with
    `layers=["F.Cu"]` and took that to be the stack the via had to clear. On the
    RK3506 board that planned a ground via at [33.71, 40.66] straight through a
    /LCDC_CLK run on In2.Cu - the fill answered "clear" because the track was on
    a layer nobody asked about. The pre-check found it afterwards ("this plan
    takes the board from 0 to 1 pair closer than 0.1 mm"), which is a gate
    working, but a gate should not be the first thing to notice a short.

    The rule is stated once, here, with the reverse control beside it: same
    board, same call, no inner-layer copper, and the via is found.
    """

    PAD = (7.0, 20.96)      # C1's GND pad in `_cell()`

    def setUp(self):
        D.reset_fill_cache()

    def _comb(self):
        """Vertical In2.Cu runs every 0.5 mm across the pad's escape reach.

        Spacing is chosen so the widest gap between two of them is 0.5 - 0.1 -
        0.1 = 0.3 mm of copper-free air, which a 0.3 mm via ring cannot use
        (it needs 0.3 mm from the barrel, i.e. 0.45 from the centre line).
        """
        out = []
        x = 5.2
        while x <= 8.8 + 1e-9:
            out.append(_track(SIG, "In2.Cu", x, 18.0, x, 24.0, width=0.2))
            x += 0.5
        return out

    def _escape(self, tracks):
        board = _Board(footprints=_cell(), tracks=tracks, zones=_planes())
        return D._gnd_escape(board, D._Copper(board), self.PAD,
                             stem=D._STUB_MM, clearance_mm=D._CLEARANCE_MM,
                             via_dia=D._VIA_DIA_MM, reach_mm=1.6,
                             layers=["F.Cu"], net=GND, bodies=D._bodies(board))

    def test_inner_layer_copper_stops_a_ground_via(self):
        got, why = self._escape(self._comb())
        self.assertIsNone(got)
        self.assertIn("clear of foreign copper", why["why"])

    def test_the_same_search_finds_the_via_once_the_inner_layer_is_clear(self):
        got, why = self._escape([])
        self.assertIsNotNone(got, why)
        self.assertEqual(got["layer"], "In1.Cu")
        self.assertGreaterEqual(got["clearance_mm"],
                               D._VIA_DIA_MM / 2.0 + D._CLEARANCE_MM - 1e-9)

    def test_a_rail_via_refusal_names_the_layer_it_would_have_cut(self):
        # The same rule on the rail side, and the refusal carries the layer:
        # "clearance" alone would send the reader looking at F.Cu, which is
        # the one layer that is fine.
        board = _Board(footprints=_cell(), zones=_planes(),
                       tracks=[_track(SIG, "In2.Cu", 13.0, 12.0, 13.0, 19.0)])
        hit, why = P._via_here(board, D._bodies(board), D._Copper(board),
                               D._fills(board, OUT), (13.0, 15.0), OUT, "C2",
                               D._VIA_DIA_MM / 2.0, D._CLEARANCE_MM,
                               (13.0, 14.97))
        self.assertIsNone(hit)
        self.assertEqual(why["key"], "clearance")
        self.assertIn("In2.Cu", why["text"])
        self.assertIn(SIG, why["text"])


if __name__ == "__main__":
    unittest.main()
