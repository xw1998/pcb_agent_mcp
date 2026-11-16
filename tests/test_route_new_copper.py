"""Which layers new copper may land on, told apart from which layers a pad is on.

`layers` answers "which pads may I start from". On a real DDR bus that answer is
"the pads, wherever their fan-out put them", and using the same set as "where may
this net draw" is how a plan pinned to the inner layers came back with F.Cu
segments: the terminals were inner, and the middle of every path happily walked
the outer layer because that layer was legal to collect from.

The tests below pin the three ways this can be wrong:

* a restricted set that does not actually restrict (segments leak to a forbidden
  layer - the failure this parameter exists for);
* a pad that has no layer in the set vanishing from the report, so a caller reads
  "not routed" as "not asked for" (silent wrong answer);
* the default path changing at all (a knob that moves the historical behaviour is
  a knob nobody can turn off).
"""
import os
import shutil
import tempfile
import unittest

from pcb_agent_mcp import edit as E
from pcb_agent_mcp import tools
from pcb_agent_mcp.route import router

SPEC = {"width_mm": 30, "height_mm": 20, "copper_layers": 2, "engine": False,
        "title": "newcopper"}

HEAD = """(footprint "SYN:%(name)s" (version 20221018) (generator pcbnew)
  (layer "F.Cu")
  (attr smd)
  (fp_text reference "REF**" (at 0 -6) (layer "F.SilkS")
    (effects (font (size 0.7 0.7) (thickness 0.12))))
  (fp_text value "%(name)s" (at 0 6) (layer "F.Fab")
    (effects (font (size 0.7 0.7) (thickness 0.12))))
"""
PAD_SMD = '''  (pad "%(number)s" smd rect (at %(x).4f %(y).4f) (size %(w).4f %(h).4f)
    (layers "F.Cu" "F.Paste" "F.Mask") (tstamp 5%(n)07d-1111-1111-1111-111111111111))
'''
PAD_TH = '''  (pad "%(number)s" thru_hole circle (at %(x).4f %(y).4f) (size 1.2 1.2) (drill 0.6)
    (layers "*.Cu" "*.Mask") (tstamp 5%(n)07d-2222-2222-2222-222222222222))
'''


def body(name, pads, pad=PAD_SMD):
    out = [HEAD % {"name": name}]
    for n, (num, dx, dy, w, h) in enumerate(pads):
        out.append(pad % {"number": num, "x": dx, "y": dy, "w": w, "h": h,
                          "n": n})
    out.append(")\n")
    return "".join(out)


class NewCopperBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pcbcu-")
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

    def place(self, name, pads, ref, x, y, nets, pad=PAD_SMD):
        with open(os.path.join(self.lib, name + ".kicad_mod"), "w",
                  encoding="utf-8", newline="\n") as fh:
            fh.write(body(name, pads, pad))
        plan = E.propose(self.board, [{
            "op": "add_footprint", "ref": ref, "x": x, "y": y,
            "library": self.lib, "name": name,
            "pad_nets": nets, "create_nets": True}])
        self.assertEqual(plan["blocking"], [], plan["blocking"])
        E.apply_plan(plan["plan_id"], confirm=True, options={"engine": False})

    def bus(self, pad=PAD_SMD):
        pads = [("1", 0.0, -1.5, 0.8, 0.8), ("2", 0.0, 1.5, 0.8, 0.8)]
        self.place("two", pads, "U1", 10.0, 10.0,
                   {"1": "/BUS_A", "2": "/BUS_B"}, pad)
        self.place("two", pads, "U2", 20.0, 10.0,
                   {"1": "/BUS_A", "2": "/BUS_B"}, pad)

    def route(self, **kw):
        board, e = tools._load_board({"path": self.board})
        self.assertIsNone(e, e)
        return router.plan(board, **kw)

    def kw(self):
        return dict(pitch_mm=0.1, layers=["F.Cu", "B.Cu"], clearance_mm=0.2,
                    track_mm=0.25, via_diameter_mm=0.6, via_drill_mm=0.3,
                    edge_clearance_mm=0.3)


class TestARestrictedSetActuallyRestricts(NewCopperBase):
    def test_no_segment_lands_on_a_forbidden_layer(self):
        # Through-hole pads so a terminal exists on both layers: the restriction
        # is then purely about where NEW copper goes, not about reachability.
        self.bus(pad=PAD_TH)
        res = self.route(new_copper_layers=["B.Cu"], **self.kw())
        self.assertEqual(res["stats"]["nets_failed"], 0, res["failed"])
        tracks = [op for op in res["ops"] if op["op"] == "add_track"]
        self.assertTrue(tracks, "nothing was routed, so the test proves nothing")
        off = sorted({op["layer"] for op in tracks} - {"B.Cu"})
        self.assertEqual(off, [], "new copper landed on %s" % (off,))
        self.assertEqual(res["stats"]["new_copper_layers"], ["B.Cu"])

    def test_the_default_uses_every_terminal_layer(self):
        self.bus(pad=PAD_TH)
        res = self.route(**self.kw())
        self.assertEqual(res["stats"]["nets_failed"], 0, res["failed"])
        self.assertNotIn("new_copper_layers", res["stats"],
                         "the knob must not change the default report")


class TestAPadWithNoAllowedLayerIsNamed(NewCopperBase):
    def test_the_net_fails_with_the_pads_it_lost(self):
        # SMD pads are on F.Cu only. Asking for new copper on B.Cu alone leaves
        # them nowhere legal to start, and that must read as a failure naming the
        # pads - not as a net that was never asked for.
        self.bus()
        res = self.route(new_copper_layers=["B.Cu"], **self.kw())
        self.assertEqual(res["stats"]["nets_routed"], 0, res["stats"])
        self.assertEqual(res["stats"]["terminals_off_new_copper_layers"], 4)
        codes = set(f["reason"]["code"] for f in res["failed"])
        self.assertEqual(codes, {"no_terminal_on_new_copper_layers"}, res["failed"])
        pads = set(f["reason"]["detail"][0]["pad"] for f in res["failed"])
        self.assertEqual(pads, {"1", "2"})


class TestABadSetIsRefused(NewCopperBase):
    def test_a_layer_the_board_does_not_have_is_named(self):
        self.bus(pad=PAD_TH)
        with self.assertRaises(router.RouteError) as ctx:
            self.route(new_copper_layers=["In7.Cu"], **self.kw())
        exc = ctx.exception
        self.assertEqual(exc.reason_code, "ARG_INVALID")
        self.assertIn("In7.Cu", exc.message)
        self.assertIn("F.Cu", exc.message)

    def test_a_layer_that_is_not_routed_on_is_refused(self):
        # B.Cu is copper but not in `layers`, so there is nowhere to put copper.
        self.bus(pad=PAD_TH)
        with self.assertRaises(router.RouteError) as ctx:
            self.route(layers=["F.Cu"], new_copper_layers=["B.Cu"],
                       pitch_mm=0.1, clearance_mm=0.2, track_mm=0.25,
                       via_diameter_mm=0.6, via_drill_mm=0.3,
                       edge_clearance_mm=0.3)
        exc = ctx.exception
        self.assertEqual(exc.reason_code, "ARG_INVALID")
        self.assertIn("routing layer", exc.message)


if __name__ == "__main__":
    unittest.main()
