"""Golden tests for board_ir_drop - the resistance network of the copper a net
actually carries.

The point of this measurement is the thing a "length times sheet resistance"
estimate cannot see: two layers only touch where a via or a plated pad joins
them. The cross-layer test below is therefore the important one - same total
length, different path, different answer.

Fixtures are real (if tiny) `.kicad_pcb` texts, so the numbers also prove the
adapter hands the Oracle the net, layer and drill it thinks it did.
"""
import itertools
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp import oracle  # noqa: E402
from pcb_agent_mcp.adapters import kicad_pcb  # noqa: E402

RHO20 = 1.724e-8
_seq = itertools.count()

HEAD = """(kicad_pcb (version 20221018) (generator pcbnew)
  (general (thickness 1.6))
  (layers (0 "F.Cu" signal) (31 "B.Cu" signal) (44 "Edge.Cuts" user))
  (setup (stackup
      (layer "F.Cu" (type "copper") (thickness 0.035))
      (layer "dielectric 1" (type "core") (thickness 1.53) (epsilon_r 4.3))
      (layer "B.Cu" (type "copper") (thickness 0.035))))
  (net 0 "") (net 1 "VCC")
  (gr_rect (start 0 0) (end 50 30) (layer "Edge.Cuts") (width 0.1))
  (footprint "T:A" (layer "F.Cu") (at 10 10)
    (fp_text reference "U1" (at 0 -2) (layer "F.SilkS"))
    (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") (net 1 "VCC")))
"""

# U2's pad sits on the layer the test wants the copper to arrive on
U2 = {
    "F.Cu": '  (footprint "T:B" (layer "F.Cu") (at 30 10)\n'
            '    (fp_text reference "U2" (at 0 -2) (layer "F.SilkS"))\n'
            '    (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") '
            '(net 1 "VCC")))\n',
    "B.Cu": '  (footprint "T:B" (layer "B.Cu") (at 30 10)\n'
            '    (fp_text reference "U2" (at 0 -2) (layer "B.SilkS"))\n'
            '    (pad "1" smd rect (at 0 0) (size 1 1) (layers "B.Cu") '
            '(net 1 "VCC")))\n',
}


def board(body, sink_layer="B.Cu"):
    path = os.path.join(tempfile.gettempdir(),
                        "ir_drop_%d_%d.kicad_pcb" % (os.getpid(), next(_seq)))
    with open(path, "w", newline="") as fh:
        fh.write(HEAD + U2[sink_layer] + body + "\n)\n")
    return kicad_pcb.parse_board(path)


ONE_TRACK = '  (segment (start 10 10) (end 30 10) (width 0.5) (layer "F.Cu") (net 1))'

CROSS_LAYER = """  (segment (start 10 10) (end 20 10) (width 0.5) (layer "F.Cu") (net 1))
  (via (at 20 10) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net 1))
  (segment (start 20 10) (end 30 10) (width 0.5) (layer "B.Cu") (net 1))"""


class TestSingleLayer(unittest.TestCase):
    def test_twenty_millimetres_by_hand(self):
        # 20 mm of 0.5 mm 35 um copper at 1 A:
        #   R = 1.724e-8*20e-3/(0.5e-3*35e-6) = 19.7029 mOhm -> 19.7029 mV
        #   J = 1 A / (0.5 mm * 35 um) = 57.1429 A/mm^2
        r = oracle.board_ir_drop(board(ONE_TRACK, "F.Cu"), "VCC", "U1.1", ["U2.1"],
                                 current_a=1.0)
        self.assertEqual(r["status"], "ok")
        v = r["value"]
        self.assertAlmostEqual(v["max_drop_mv"], 19.702857, places=6)
        self.assertEqual(v["worst_sink"], "U2.1")
        self.assertAlmostEqual(v["total_copper_loss_w"], 0.019702857, places=9)
        self.assertAlmostEqual(v["worst_current_density"]["current_density_a_per_mm2"],
                               57.142857, places=5)
        self.assertEqual(v["edge_count"], 1)
        self.assertEqual(v["node_count"], 2)

    def test_pct_agrees_with_mv(self):
        # the percentage and the millivolts are the same quantity twice. They were
        # NOT: 19.7029 mV of 3.3 V is 0.597 %, and the field said 597.06 %. A
        # derived field nobody computes by hand needs a test that ties it to the
        # field it is derived from - otherwise it is decoration.
        r = oracle.board_ir_drop(board(ONE_TRACK, "F.Cu"), "VCC", "U1.1", ["U2.1"],
                                 current_a=1.0)
        v = r["value"]
        self.assertAlmostEqual(v["worst_sink_drop_pct_of_3v3"],
                               100.0 * v["max_drop_mv"] / 1000.0 / 3.3, places=6)
        self.assertAlmostEqual(v["worst_sink_drop_pct_of_3v3"], 0.597056, places=6)

    def test_copper_um_overrides_the_stackup_and_says_so(self):
        # 18 um instead of 35 um is 35/18 = 1.944x the resistance
        r = oracle.board_ir_drop(board(ONE_TRACK, "F.Cu"), "VCC", "U1.1", ["U2.1"],
                                 current_a=1.0, copper_um=18)
        self.assertEqual(r["status"], "ok")
        self.assertAlmostEqual(r["value"]["max_drop_mv"], 38.311111, places=6)
        self.assertEqual(r["assumptions"]["copper_um_source"], "explicit")
        self.assertTrue(any("OVERRIDES the stackup" in w for w in r["warnings"]),
                        r["warnings"])

    def test_current_share_is_split_when_the_sinks_do_not_say(self):
        # two sinks, one track each of the same length: half the current each
        body = ('  (segment (start 10 10) (end 20 10) (width 0.5) (layer "F.Cu") (net 1))\n'
                '  (segment (start 20 10) (end 30 10) (width 0.5) (layer "F.Cu") (net 1))')
        r = oracle.board_ir_drop(board(body, "F.Cu"), "VCC", "U1.1", ["U2.1"],
                                 current_a=1.0)
        self.assertEqual(r["status"], "ok")
        self.assertAlmostEqual(r["value"]["max_drop_mv"], 19.702857, places=6)


class TestCrossLayer(unittest.TestCase):
    def test_a_via_is_the_only_way_across(self):
        # 10 mm on F.Cu + a 0.3/25 um via in a 1.6 mm board + 10 mm on B.Cu:
        #   two tracks of 9.851429 mV each + the barrel 1.277129 mV
        # The TOTAL LENGTH is the same 20 mm as the single-layer case, and the
        # answer is 20.98 mV instead of 19.70 - that 1.28 mV is the via, and it
        # is exactly the piece a length-times-rho estimate never charges for.
        r = oracle.board_ir_drop(board(CROSS_LAYER), "VCC", "U1.1", ["U2.1"],
                                 current_a=1.0, plating_um=25.0)
        self.assertEqual(r["status"], "ok")
        v = r["value"]
        self.assertAlmostEqual(v["max_drop_mv"], 20.979986, places=5)
        kinds = sorted(e["kind"] for e in v["edges"])
        self.assertEqual(kinds, ["track", "track", "via"])
        via = [e for e in v["edges"] if e["kind"] == "via"][0]
        self.assertAlmostEqual(via["drop_mv"], 1.277129, places=6)

    def test_via_without_plating_thickness_is_refused(self):
        r = oracle.board_ir_drop(board(CROSS_LAYER), "VCC", "U1.1", ["U2.1"],
                                 current_a=1.0)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "ARG_REQUIRED")
        self.assertIn("plating_um", r["message"])


class TestRefusals(unittest.TestCase):
    def test_a_net_without_tracks_is_not_measurable(self):
        r = oracle.board_ir_drop(board(""), "VCC", "U1.1", ["U2.1"], current_a=1.0)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "NOT_APPLICABLE")

    def test_a_disconnected_sink_is_not_a_zero_drop(self):
        # This is the failure mode that matters: the sink exists on the net but
        # no copper reaches it, so the honest answer is "not connected", not
        # "0 mV, all good". This test used to pin NOT_APPLICABLE, because the only
        # thing the solve could say was "singular at column N" - which names
        # neither the pad nor the reason and reads like a modelling gap. The dense
        # path now makes the same reachability cut the pour path always did, so
        # the answer is NOT_CONNECTED and the pad is in the message.
        body = '  (segment (start 10 10) (end 15 10) (width 0.5) (layer "F.Cu") (net 1))'
        r = oracle.board_ir_drop(board(body, "F.Cu"), "VCC", "U1.1", ["U2.1"],
                                 current_a=1.0)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "NOT_CONNECTED")
        self.assertIn("U2.1", r["message"])

    def test_a_pad_that_is_not_on_the_net_is_refused(self):
        r = oracle.board_ir_drop(board(ONE_TRACK, "F.Cu"), "VCC", "U1.9", ["U2.1"],
                                 current_a=1.0)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "ARG_INVALID")

    def test_no_copper_thickness_anywhere_is_refused(self):
        b = board(ONE_TRACK, "F.Cu")
        b.stackup = []
        r = oracle.board_ir_drop(b, "VCC", "U1.1", ["U2.1"], current_a=1.0)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "ARG_REQUIRED")
        self.assertIn("F.Cu", r["message"])


if __name__ == "__main__":
    unittest.main()
