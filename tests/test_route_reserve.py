"""A bundle band can reserve lateral room for the nets that must meander.

`docs/188 §7` left one debt at the layout level: the DDR corridor packs its
members at clearance (0.25-0.30 mm centre to centre), so `route.tune_board` has
no room for a *readable* tooth (1.5x track width of air on each side) and the
binding nets come back `shortfall` no matter how the ladder is tuned. The room
cannot be made afterwards - by then there is copper in it - so the claim has to
be made while the bus is being routed. That is `bundle_bands[].reserve_nets` /
`reserve_mm` / `reserve_price`.

The claims these tests hold the code to:

* a reservation actually moves copper (the reserved net ends up with measurably
  more air at the corridor's waist than it had before), and the number reported
  is that air, not the pad-to-pad distance which is fixed by the layout;
* it stays a *price*: every net still routes, and no emitted segment is illegal,
  even at a price high enough to make the search hate the ring;
* a reservation that cannot mean anything is refused with a reason - a ring
  around a net that is not in the band, a ring with no width, a price that is
  no reservation at all or that decides the search instead of steering it.
"""
import os
import shutil
import tempfile
import unittest

from pcb_agent_mcp import edit as E
from pcb_agent_mcp import tools
from pcb_agent_mcp.route import router

SPEC = {"width_mm": 60, "height_mm": 40, "copper_layers": 2, "engine": False,
        "title": "reserve"}

HEAD = """(footprint "SYN:%(name)s" (version 20221018) (generator pcbnew)
  (layer "F.Cu")
  (attr smd)
  (fp_text reference "REF**" (at 0 -6) (layer "F.SilkS")
    (effects (font (size 0.7 0.7) (thickness 0.12))))
  (fp_text value "%(name)s" (at 0 6) (layer "F.Fab")
    (effects (font (size 0.7 0.7) (thickness 0.12))))
"""
PAD = '''  (pad "%(number)s" smd rect (at %(x).4f %(y).4f) (size %(w).4f %(h).4f)
    (layers "F.Cu" "F.Paste" "F.Mask") (tstamp 5%(n)07d-1111-1111-1111-111111111111))
'''


def body(name, pads):
    out = [HEAD % {"name": name}]
    for n, (num, dx, dy, w, h) in enumerate(pads):
        out.append(PAD % {"number": num, "x": dx, "y": dy, "w": w, "h": h,
                          "n": n})
    out.append(")\n")
    return "".join(out)


class ReserveBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pcbrsv-")
        self._home = os.environ.get("PCB_AGENT_HOME")
        os.environ["PCB_AGENT_HOME"] = os.path.join(self.tmp, "home")
        self.board = os.path.join(self.tmp, "b.kicad_pcb")
        E.create_board(self.board, dict(SPEC))
        self.lib = os.path.join(self.tmp, "Syn.pretty")
        os.makedirs(self.lib)

    def tearDown(self):
        if self._home is None:
            os.environ.pop("PCB_AGENT_HOME", None)
        else:
            os.environ["PCB_AGENT_HOME"] = self._home
        shutil.rmtree(self.tmp, ignore_errors=True)

    def place(self, name, pads, ref, x, y, nets):
        with open(os.path.join(self.lib, name + ".kicad_mod"), "w",
                  encoding="utf-8", newline="\n") as fh:
            fh.write(body(name, pads))
        plan = E.propose(self.board, [{
            "op": "add_footprint", "ref": ref, "x": x, "y": y,
            "library": self.lib, "name": name,
            "pad_nets": nets, "create_nets": True}])
        self.assertEqual(plan["blocking"], [], plan["blocking"])
        E.apply_plan(plan["plan_id"], confirm=True, options={"engine": False})

    def three_wire_bus(self):
        """Three nets on a 20 mm run: the pair C-B is packed at 0.7 mm centre to
        centre, which is the DDR corridor's situation in miniature."""
        left = [("1", -2.0, -3.0, 0.5, 0.5), ("2", -2.0, -1.0, 0.5, 0.5),
                ("3", -2.0, -0.3, 0.5, 0.5)]
        right = [("1", 2.0, -3.0, 0.5, 0.5), ("2", 2.0, -1.0, 0.5, 0.5),
                 ("3", 2.0, -0.3, 0.5, 0.5)]
        self.place("three", left, "U1", 10.0, 20.0,
                   {"1": "/BUS_A", "2": "/BUS_B", "3": "/BUS_C"})
        self.place("three", right, "U2", 30.0, 20.0,
                   {"1": "/BUS_A", "2": "/BUS_B", "3": "/BUS_C"})

    def route(self, band):
        board, e = tools._load_board({"path": self.board})
        self.assertIsNone(e, e)
        return router.plan(board, pitch_mm=0.1, layers=["F.Cu", "B.Cu"],
                           clearance_mm=0.2, track_mm=0.25,
                           via_diameter_mm=0.6, via_drill_mm=0.3,
                           edge_clearance_mm=0.3, bundle_bands=[band])

    def band(self, **kw):
        spec = {"name": "bus",
                "nets": ["/BUS_A", "/BUS_B", "/BUS_C"],
                "radius_mm": 1.2, "discount": 0.6}
        spec.update(kw)
        return spec


class TestTheRingBuysAir(ReserveBase):
    def test_the_reserved_net_gains_room_at_the_waist(self):
        self.three_wire_bus()
        plain = self.route(self.band())
        self.assertEqual(plain["stats"]["nets_failed"], 0, plain["failed"])
        self.assertIsNone(plain["stats"]["bundle_bands"][0].get("reserve"),
                          "no reservation asked for, none reported")

        res = self.route(self.band(reserve_nets=["/BUS_C"], reserve_mm=1.0,
                                   reserve_price=4.0))
        self.assertEqual(res["stats"]["nets_failed"], 0, res["failed"])
        rep = res["stats"]["bundle_bands"][0]["reserve"]
        self.assertEqual(rep["nets"], ["/BUS_C"])
        self.assertGreater(rep["ring_cells"], 0, rep)
        # The pads are 0.7 mm apart, so the pad-to-pad number would stay 0.7 no
        # matter what the search did; the reported gap is the waist, and it has
        # to grow towards the ring that was bought.
        got = rep["gap_mm"]["/BUS_C"]
        self.assertGreaterEqual(got["gap_mm"], 0.9,
                                "ring of 1.0 mm bought %.3f mm of air" %
                                got["gap_mm"])
        self.assertIn(got["nearest"], ("/BUS_A", "/BUS_B"))
        # `gap_mm` says how much air was bought; `pinch` has to say by whom, and
        # it must be a real object - a named layer, a point on the run, a
        # non-negative edge gap - or the report is a guess dressed as a number.
        pinch = got.get("pinch")
        self.assertIsNotNone(pinch, got)
        self.assertIn(pinch["kind"], ("pad", "track", "via"), pinch)
        self.assertNotEqual(pinch["net"], "/BUS_C", pinch)
        self.assertIn(pinch["layer"], ("F.Cu", "B.Cu"), pinch)
        self.assertEqual(len(pinch["at"]), 2, pinch)
        self.assertGreaterEqual(pinch["edge_mm"], 0.0, pinch)
        if pinch["kind"] == "pad":
            self.assertIn(".", pinch["source"], pinch)
        else:
            self.assertIn(pinch["source"], ("board", "this_plan"), pinch)

    def test_it_stays_a_price_and_never_a_wall(self):
        self.three_wire_bus()
        res = self.route(self.band(reserve_nets=["/BUS_C"], reserve_mm=1.0,
                                   reserve_price=18.0))
        self.assertEqual(res["stats"]["nets_failed"], 0, res["failed"])
        self.assertEqual(res["stats"]["nets_routed"], 3)
        self.assertEqual(res["stats"]["rejected_ops"], 0,
                         "a reservation may lose a route; it may never buy a "
                         "clearance violation")
        for row in res["nets"]:
            self.assertEqual(row["status"], "routed", row)


class TestPinchNamesWhatIsInTheWay(ReserveBase):
    def test_it_names_pre_existing_board_copper_not_just_this_plan(self):
        """The number that comes back small is only useful with a name attached,
        and on a real board the name is usually not this plan's copper at all -
        it is the fan-out that was already there. A blocker written before the
        route therefore has to show up with `source == "board"`."""
        # One reserved net with a single neighbour far below it: the only
        # copper in the corridor is the track written before the route, so there
        # is nothing else `pinch` could name.
        pads = [("1", -2.0, 0.0, 0.5, 0.5), ("2", -2.0, -2.5, 0.5, 0.5)]
        npads = [("1", 2.0, 0.0, 0.5, 0.5), ("2", 2.0, -2.5, 0.5, 0.5)]
        self.place("pair", pads, "U1", 10.0, 20.0,
                   {"1": "/BUS_C", "2": "/GND"})
        self.place("pair", npads, "U2", 30.0, 20.0,
                   {"1": "/BUS_C", "2": "/GND"})
        plan = E.propose(self.board, [{
            "op": "add_track", "x1": 14.0, "y1": 20.5,
            "x2": 24.0, "y2": 20.5, "width": 0.25, "layer": "F.Cu",
            "net": "/GND"}])
        self.assertEqual(plan["blocking"], [], plan["blocking"])
        E.apply_plan(plan["plan_id"], confirm=True, options={"engine": False})
        res = self.route({"name": "bus", "nets": ["/BUS_C", "/GND"],
                          "radius_mm": 1.2, "discount": 0.6,
                          "reserve_nets": ["/BUS_C"], "reserve_mm": 0.5,
                          "reserve_price": 3.0})
        self.assertEqual(res["stats"]["nets_failed"], 0, res["failed"])
        pinch = res["stats"]["bundle_bands"][0]["reserve"]["gap_mm"][
            "/BUS_C"]["pinch"]
        self.assertEqual(pinch["source"], "board", pinch)
        self.assertEqual(pinch["kind"], "track", pinch)
        self.assertEqual(pinch["net"], "/GND", pinch)
        self.assertEqual(pinch["layer"], "F.Cu", pinch)
        self.assertAlmostEqual(pinch["edge_mm"], 0.25, delta=0.06)

class TestTheReservedNetClaimsSpaceFirst(ReserveBase):
    def test_it_routes_before_the_band_members_it_clears_room_from(self):
        """The ring is painted from copper that already exists. If the reserved
        net went last there would be nothing to paint around and the reservation
        would silently buy nothing while reporting success."""
        self.three_wire_bus()
        order = []

        def band(**kw):
            spec = self.band(**kw)
            return spec

        board, e = tools._load_board({"path": self.board})
        self.assertIsNone(e, e)
        res = router.plan(board, pitch_mm=0.1, layers=["F.Cu", "B.Cu"],
                          clearance_mm=0.2, track_mm=0.25,
                          via_diameter_mm=0.6, via_drill_mm=0.3,
                          edge_clearance_mm=0.3,
                          progress=lambda p: order.append(p["net"]),
                          bundle_bands=[band(reserve_nets=["/BUS_C"],
                                             reserve_mm=1.0,
                                             reserve_price=4.0)])
        self.assertEqual(res["stats"]["nets_failed"], 0, res["failed"])
        self.assertEqual(order[0], "/BUS_C", order)
        self.assertGreater(
            res["stats"]["bundle_bands"][0]["reserve"]["gap_mm"]["/BUS_C"]
            ["gap_mm"], 0.9)


class TestAMalformedReservationIsRefused(ReserveBase):
    def setUp(self):
        ReserveBase.setUp(self)
        self.three_wire_bus()

    def _refuse(self, band):
        with self.assertRaises(router.RouteError) as ctx:
            self.route(band)
        return ctx.exception

    def test_a_net_outside_the_band_is_named(self):
        exc = self._refuse(self.band(reserve_nets=["/SOMETHING_ELSE"],
                                     reserve_mm=0.5))
        self.assertEqual(exc.reason_code, "ARG_INVALID")
        self.assertIn("/SOMETHING_ELSE", exc.message)

    def test_a_ring_with_no_width_is_refused(self):
        exc = self._refuse(self.band(reserve_nets=["/BUS_C"]))
        self.assertIn("reserve_mm", exc.message)

    def test_a_ring_wider_than_a_corridor_is_refused(self):
        exc = self._refuse(self.band(reserve_nets=["/BUS_C"],
                                     reserve_mm=9.0))
        self.assertIn("reserve_mm", exc.message)

    def test_a_price_of_one_is_not_a_reservation(self):
        exc = self._refuse(self.band(reserve_nets=["/BUS_C"], reserve_mm=0.5,
                                     reserve_price=1.0))
        self.assertIn("reserve_price", exc.message)

    def test_a_price_that_decides_the_search_is_refused(self):
        exc = self._refuse(self.band(reserve_nets=["/BUS_C"], reserve_mm=0.5,
                                     reserve_price=50.0))
        self.assertIn("reserve_price", exc.message)


if __name__ == "__main__":
    unittest.main()
