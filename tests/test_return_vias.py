"""Return vias and rail width: the two P1 numbers that were missing.

Both have the same shape of trap:

* the return-via rule speaks about RF and fast signals, and no board file says
  which nets those are, so counting every signal via would condemn a GPIO dev
  board that was manufactured and works. The count is measured, the verdict is
  left to the caller - and that hand-off is a test, not a comment.
* the rail-width rule needs the current, which is not in the file either. The
  width IS in the file, and the narrowest segment is the binding one, so that
  half is measured and the grader refuses until current_a arrives.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp.adapters.canonical import Board  # noqa: E402
from pcb_agent_mcp.ruleset import measure as M  # noqa: E402
from pcb_agent_mcp.ruleset.audit import audit as run_audit  # noqa: E402


def _track(net, start, end, layer="F.Cu", width=0.25):
    return {"net": net, "layer": layer, "width": width,
            "start": list(start), "end": list(end),
            "uuid": "%s-%s-%s" % (net, start, end)}


def _via(net, x, y):
    return {"net": net, "x": x, "y": y, "drill": 0.3, "diameter": 0.6,
            "from": "F.Cu", "to": "B.Cu", "layers": ["F.Cu", "B.Cu"],
            "kind": "through", "uuid": "via-%s-%s" % (net, x)}


def _pour(net, layer="B.Cu"):
    ring = [(0, 0), (30, 0), (30, 30), (0, 30)]
    return {"net": net, "is_keepout": False, "layers": [layer],
            "filled_polygons": [[(float(x), float(y)) for x, y in ring]]}


def _board(tracks=(), vias=(), zones=()):
    b = Board("test", "memory")
    b.footprints = []
    b.tracks = list(tracks)
    b.vias = list(vias)
    b.zones = list(zones)
    b.outline = []
    b.rules = {}
    return b


class TestTheReturnVia(unittest.TestCase):
    def test_a_via_beside_the_ground_via_is_not_counted(self):
        b = _board([_track("SIG", (1, 1), (2, 1))],
                   [_via("SIG", 1.0, 1.0), _via("GND", 1.3, 1.0)], [_pour("GND")])
        m = M.measure(b)
        row = m["layer_changes_without_return_via"]
        self.assertEqual(row["value"], 0.0)

    def test_a_via_with_no_return_via_near_it_is_counted_and_measured(self):
        b = _board([_track("SIG", (1, 1), (2, 1))],
                   [_via("SIG", 1.0, 1.0), _via("GND", 1.0, 6.0)], [_pour("GND")])
        m = M.measure(b)
        row = m["layer_changes_without_return_via"]
        self.assertEqual(row["value"], 1.0)
        self.assertAlmostEqual(row["examples"][0]["nearest_return_via_mm"], 5.0, places=3)

    def test_the_poured_nets_are_read_from_the_file_not_from_the_names(self):
        """The return net is called VRAIL here; nothing may hardcode 'GND'."""
        b = _board([_track("SIG", (1, 1), (2, 1))],
                   [_via("SIG", 1.0, 1.0), _via("VRAIL", 1.2, 1.0)], [_pour("VRAIL")])
        m = M.measure(b)
        self.assertEqual(m["layer_changes_without_return_via"]["value"], 0.0)
        self.assertIn("VRAIL", m["layer_changes_without_return_via"]["where"])

    def test_with_no_return_via_anywhere_it_refuses(self):
        b = _board([_track("SIG", (1, 1), (2, 1))],
                   [_via("SIG", 1.0, 1.0)], [_pour("GND")])
        m = M.measure(b)
        row = m["layer_changes_without_return_via"]
        self.assertIn("unmeasurable", row)
        self.assertIn("none of them on a poured net", row["unmeasurable"])

    def test_with_no_pour_it_refuses(self):
        b = _board([_track("SIG", (1, 1), (2, 1))], [_via("SIG", 1.0, 1.0)])
        m = M.measure(b)
        self.assertIn("no net is carried by a filled pour",
                      m["layer_changes_without_return_via"]["unmeasurable"])


class TestTheRailWidth(unittest.TestCase):
    def test_the_narrowest_segment_on_a_poured_net_is_the_answer(self):
        b = _board([_track("VCC", (1, 1), (20, 1), width=1.0),
                    _track("VCC", (20, 1), (21, 1), width=0.2),
                    _track("SIG", (1, 3), (20, 3), width=0.1)],
                   zones=[_pour("VCC")])
        m = M.measure(b)
        row = m["power_trace_width_mm"]
        self.assertAlmostEqual(row["value"], 0.2, places=4)
        self.assertIn("VCC", row["where"])
        # SIG is 0.1 mm, but it is not a rail and must not set the answer.
        self.assertNotIn("SIG", row["where"])

    def test_a_rail_carried_by_pour_alone_is_named_not_zeroed(self):
        b = _board([_track("VCC", (1, 1), (20, 1), width=1.0)],
                   zones=[_pour("VCC"), _pour("+3V3")])
        m = M.measure(b)
        self.assertIn("+3V3 carried by pour alone", m["power_trace_width_mm"]["note"])

    def test_no_track_on_any_poured_net_refuses(self):
        b = _board([_track("SIG", (1, 1), (20, 1))], zones=[_pour("VCC")])
        m = M.measure(b)
        self.assertIn("carried by copper pour alone",
                      m["power_trace_width_mm"]["unmeasurable"])


class TestTheHandOff(unittest.TestCase):
    def test_the_return_via_count_is_shown_and_not_graded(self):
        b = _board([_track("SIG", (1, 1), (2, 1))],
                   [_via("SIG", 1.0, 1.0), _via("GND", 1.0, 12.0)], [_pour("GND")])
        res = run_audit(b)
        c = [x for x in res["levels"]["P1"]["checks"]
             if x["id"] == "layout.layer_change_return_via"][0]
        self.assertEqual(c["state"], "undetermined")
        self.assertEqual(c["measured"], 1.0)
        self.assertIsNone(c.get("margin"))
        self.assertIn("population", c["reason"])

    def test_the_caller_naming_the_set_turns_it_into_a_verdict(self):
        b = _board([_track("SIG", (1, 1), (2, 1))],
                   [_via("SIG", 1.0, 1.0), _via("GND", 1.0, 12.0)], [_pour("GND")])
        res = run_audit(b, facts={"layer_changes_without_return_via": 0})
        c = [x for x in res["levels"]["P1"]["checks"]
             if x["id"] == "layout.layer_change_return_via"][0]
        self.assertEqual(c["state"], "pass")

    def test_the_rail_width_is_graded_only_when_the_current_is_given(self):
        b = _board([_track("VCC", (1, 1), (20, 1), width=0.15)], zones=[_pour("VCC")])
        res = run_audit(b)
        c = [x for x in res["levels"]["P1"]["checks"]
             if x["id"] == "layout.power_trace_width"][0]
        self.assertEqual(c["state"], "undetermined")
        self.assertIn("current_a", c["reason"])
        res = run_audit(b, facts={"current_a": 2.0, "current_standard": "ipc2221"})
        c = [x for x in res["levels"]["P1"]["checks"]
             if x["id"] == "layout.power_trace_width"][0]
        self.assertEqual(c["state"], "fail")
        self.assertGreater(c["margin"], 0)


if __name__ == "__main__":
    unittest.main()
