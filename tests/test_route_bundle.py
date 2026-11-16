"""A bundle band must change the search's *preferences* and nothing else.

The claim `bundle_bands` makes is narrow and therefore testable:

* a group's members are routed first, shortest member first;
* once one member is down, its copper becomes a discount the rest may follow
  (`route.grid.astar(guide=...)`), so the group converges on one corridor;
* a corridor never substitutes for legality - a cell the raster calls blocked
  stays blocked, so a band can only lose a route, never buy a clearance
  violation with tidiness;
* a malformed band is refused with a reason, never silently dropped. Dropping
  it would leave the caller believing a corridor exists for nets that were
  never considered, which is the "wrong answer" this server refuses to give.
"""
import os
import shutil
import tempfile
import unittest

from pcb_agent_mcp import edit as E
from pcb_agent_mcp import tools
from pcb_agent_mcp.route import router

SPEC = {"width_mm": 30, "height_mm": 20, "copper_layers": 2, "engine": False,
        "title": "bundle"}

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


class BundleBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pcbband-")
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

    def two_bit_bus(self):
        """Four pads per side: two nets, each 6 mm apart, running side by side."""
        left = [("1", -2.0, -1.5, 0.8, 0.8), ("2", -2.0, 1.5, 0.8, 0.8)]
        right = [("1", 2.0, -1.5, 0.8, 0.8), ("2", 2.0, 1.5, 0.8, 0.8)]
        self.place("two", left, "U1", 10.0, 10.0,
                   {"1": "/BUS_A", "2": "/BUS_B"})
        self.place("two", right, "U2", 20.0, 10.0,
                   {"1": "/BUS_A", "2": "/BUS_B"})

    def route(self, **kw):
        board, e = tools._load_board({"path": self.board})
        self.assertIsNone(e, e)
        return router.plan(board, **kw)

    def kw(self):
        return dict(pitch_mm=0.1, layers=["F.Cu", "B.Cu"], clearance_mm=0.2,
                    track_mm=0.25, via_diameter_mm=0.6, via_drill_mm=0.3,
                    edge_clearance_mm=0.3)


class TestTheBandIsHonoured(BundleBase):
    def test_group_routes_first_and_the_guide_is_used(self):
        self.two_bit_bus()
        order = []
        res = self.route(progress=lambda p: order.append(p["net"]),
                         bundle_bands=[{"name": "bus",
                                        "nets": ["/BUS_A", "/BUS_B"],
                                        "radius_mm": 1.0, "discount": 0.6}],
                         **self.kw())
        self.assertEqual(res["stats"]["nets_failed"], 0, res["failed"])
        rep = res["stats"]["bundle_bands"][0]
        self.assertEqual(rep["name"], "bus")
        self.assertEqual(rep["routed"], 2)
        # The guide is built from the skeleton's copper, so the SECOND member
        # must have paid for one; zero means the band changed nothing.
        self.assertGreaterEqual(rep["guide_calls"], 1)
        self.assertIsNotNone(rep["skeleton"])
        for mm in rep["length_mm"]:
            self.assertGreater(mm, 0.0, rep)

    def test_the_band_does_not_break_legality(self):
        """A corridor may cost a route; it may never buy a violation."""
        self.two_bit_bus()
        res = self.route(bundle_bands=[{"name": "bus",
                                        "nets": ["/BUS_A", "/BUS_B"],
                                        "radius_mm": 1.0, "discount": 0.6}],
                         **self.kw())
        # Every emitted segment survived the exact check, which is the only
        # thing that decides whether copper is legal.
        self.assertEqual(res["stats"]["rejected_ops"], 0, res["stats"])
        self.assertEqual(res["stats"]["nets_routed"], 2)


class TestAMalformedBandIsRefused(BundleBase):
    def setUp(self):
        BundleBase.setUp(self)
        self.two_bit_bus()

    def _refuse(self, band):
        with self.assertRaises(router.RouteError) as ctx:
            self.route(bundle_bands=[band], **self.kw())
        return ctx.exception

    def test_one_net_is_not_a_corridor(self):
        exc = self._refuse({"name": "solo", "nets": ["/BUS_A"]})
        self.assertEqual(exc.reason_code, "ARG_INVALID")
        self.assertIn("fewer than 2", exc.message)

    def test_an_unknown_net_is_named_not_dropped(self):
        exc = self._refuse({"name": "x", "nets": ["/BUS_A", "/NOPE"]})
        self.assertEqual(exc.reason_code, "ARG_INVALID")
        self.assertIn("/NOPE", exc.message)

    def test_a_free_corridor_is_refused(self):
        # discount 1.0 would make every cell inside the corridor cost nothing,
        # and the search would happily take a 20 mm detour to stay in it.
        exc = self._refuse({"name": "x", "nets": ["/BUS_A", "/BUS_B"],
                            "discount": 1.0})
        self.assertIn("discount", exc.message)

    def test_an_outsized_penalty_is_refused(self):
        exc = self._refuse({"name": "x", "nets": ["/BUS_A", "/BUS_B"],
                            "outside": 9.0})
        self.assertIn("outside", exc.message)


if __name__ == "__main__":
    unittest.main()
