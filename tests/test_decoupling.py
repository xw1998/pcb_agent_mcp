"""The decoupling measurement: a copper path, and the population it cannot bound.

Every case is a board whose answer is known by construction, and two of them are
the failure side that matters more than the pass:

* a capacitor whose net is carried by no track (a pour) has NO path, and the
  measurement says so instead of quoting a straight line through the laminate;
* a capacitor whose nearest same-net neighbour is a two-pin part is not
  "decoupling" anything, so two-pin parts are not targets at all.

The last two tests are the honesty gate: the audit must show the number and
refuse to grade it, unless the caller says which capacitor serves which pin.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp.adapters.canonical import Board  # noqa: E402
from pcb_agent_mcp.ruleset import measure as M  # noqa: E402
from pcb_agent_mcp.ruleset.audit import audit as run_audit  # noqa: E402


def _pad(net, x, y, number, layers=("F.Cu",)):
    return {"number": number, "net": net, "x": x, "y": y, "w": 1.0, "h": 1.0,
            "shape": "rect", "rotation": 0.0, "layers": list(layers),
            "hole_mm": None, "smd": True, "type": "smd"}


def _fp(ref, pads):
    return {"ref": ref, "value": "X", "lib_id": "test:X", "x": 0.0, "y": 0.0,
            "rotation": 0.0, "layer": "F.Cu", "pads": pads}


def _track(net, start, end, layer="F.Cu"):
    return {"net": net, "layer": layer, "width": 0.25,
            "start": list(start), "end": list(end),
            "uuid": "%s-%s-%s" % (net, start, end)}


def _via(net, x, y):
    return {"net": net, "x": x, "y": y, "drill": 0.3, "diameter": 0.6,
            "from": "F.Cu", "to": "B.Cu", "layers": ["F.Cu", "B.Cu"],
            "kind": "through", "uuid": "via-%s-%s" % (net, x)}


def _board(footprints, tracks=(), vias=()):
    b = Board("test", "memory")
    b.footprints = list(footprints)
    b.tracks = list(tracks)
    b.vias = list(vias)
    b.outline = []
    b.rules = {}
    return b


# C1 sits at x=0, U1 at x=10; both pads are 1 mm wide, so a track drawn from pad
# anchor to pad anchor is 10 mm of copper and the answer is exactly 10.
def _cap(px=0.0, py=0.0, net_a="VCC", net_b="GND"):
    return _fp("C1", [_pad(net_a, px, py, "1"), _pad(net_b, px, py + 2.0, "2")])


def _ic(px=10.0):
    return _fp("U1", [_pad("VCC", px, 0.0, "1"), _pad("GND", px, 2.0, "2"),
                      _pad("SIG", px, 4.0, "3")])


class TestThePath(unittest.TestCase):
    def test_a_routed_cap_reports_the_copper_length(self):
        b = _board([_cap(), _ic()],
                   [_track("VCC", (0, 0), (5, 0)), _track("VCC", (5, 0), (10, 0))])
        m = M.measure(b)
        self.assertAlmostEqual(m["decoupling_max_distance_to_pin_mm"]["value"], 10.0,
                               places=4)
        self.assertEqual(m["decoupling_max_distance_to_pin_mm"]["where"],
                         "C1.1 to U1.1 on VCC")
        self.assertIs(m["decoupling_via_in_path"]["value"], False)

    def test_a_pour_carried_net_has_no_path_and_says_so(self):
        """No track at all: the honest answer is "no path", not "0 mm apart"."""
        b = _board([_cap(), _ic()])
        m = M.measure(b)
        self.assertIn("unmeasurable", m["decoupling_max_distance_to_pin_mm"])
        self.assertIn("no capacitor reaches",
                      m["decoupling_max_distance_to_pin_mm"]["unmeasurable"])

    def test_a_two_pin_part_is_not_the_pin_it_serves(self):
        """R1 sits 1 mm away on the same net; it still is not the decoupled pin."""
        r1 = _fp("R1", [_pad("VCC", 1.0, 0.0, "1"), _pad("VCC", 1.5, 0.0, "2")])
        b = _board([_cap(), r1, _ic()],
                   [_track("VCC", (0, 0), (5, 0)), _track("VCC", (5, 0), (10, 0))])
        m = M.measure(b)
        self.assertAlmostEqual(m["decoupling_max_distance_to_pin_mm"]["value"], 10.0,
                               places=4)
        self.assertEqual(m["decoupling_max_distance_to_pin_mm"]["where"],
                         "C1.1 to U1.1 on VCC")

    def test_a_forced_layer_change_is_reported_as_a_via_in_the_path(self):
        b = _board([_cap(), _ic()],
                   [_track("VCC", (0, 0), (5, 0)),
                    _track("VCC", (5, 0), (10, 0), layer="B.Cu")],
                   [_via("VCC", 5.0, 0.0), _via("VCC", 10.0, 0.0)])
        m = M.measure(b)
        self.assertAlmostEqual(m["decoupling_max_distance_to_pin_mm"]["value"], 10.0,
                               places=4)
        self.assertIs(m["decoupling_via_in_path"]["value"], True)
        self.assertIn("C1", m["decoupling_via_in_path"]["where"])

    def test_a_via_free_alternative_keeps_the_rule_passing(self):
        """A via exists on the net, but not in the path that is actually used."""
        b = _board([_cap(), _ic()],
                   [_track("VCC", (0, 0), (5, 0)), _track("VCC", (5, 0), (10, 0)),
                    _track("VCC", (0, 0), (0, 4)), _track("VCC", (0, 4), (10, 4))],
                   [_via("VCC", 0.0, 4.0)])
        m = M.measure(b)
        self.assertAlmostEqual(m["decoupling_max_distance_to_pin_mm"]["value"], 10.0,
                               places=4)
        self.assertIs(m["decoupling_via_in_path"]["value"], False)

    def test_no_capacitor_at_all_is_unmeasurable_not_a_pass(self):
        b = _board([_ic()])
        m = M.measure(b)
        self.assertIn("starts with C",
                      m["decoupling_max_distance_to_pin_mm"]["unmeasurable"])


class TestTheHonestyGate(unittest.TestCase):
    def test_the_audit_shows_the_number_and_refuses_to_grade_it(self):
        b = _board([_cap(), _ic()],
                   [_track("VCC", (0, 0), (5, 0)), _track("VCC", (5, 0), (10, 0))])
        res = run_audit(b)
        row = [c for c in res["levels"]["P1"]["checks"]
               if c["id"] == "layout.decoupling_distance"][0]
        self.assertEqual(row["state"], "undetermined")
        self.assertAlmostEqual(row["measured"], 10.0, places=4)
        self.assertEqual(row["measured_state"], "fail")
        self.assertNotIn("margin", row)
        self.assertIn("population", row["reason"])

    def test_a_caller_supplied_fact_wins_and_is_graded(self):
        b = _board([_cap(), _ic()],
                   [_track("VCC", (0, 0), (5, 0)), _track("VCC", (5, 0), (10, 0))])
        res = run_audit(b, facts={"decoupling_max_distance_to_pin_mm": 2.0})
        row = [c for c in res["levels"]["P1"]["checks"]
               if c["id"] == "layout.decoupling_distance"][0]
        self.assertEqual(row["state"], "pass")
        self.assertAlmostEqual(row["measured"], 2.0, places=4)

        res = run_audit(b, facts={"decoupling_max_distance_to_pin_mm": 9.0})
        row = [c for c in res["levels"]["P1"]["checks"]
               if c["id"] == "layout.decoupling_distance"][0]
        self.assertEqual(row["state"], "fail")
        self.assertAlmostEqual(row["margin"], 6.0, places=4)


if __name__ == "__main__":
    unittest.main()
