"""The whole board as one circuit: what the deck must contain, and what it must not.

`board_netlist` / `board_sim` are only worth having if the deck they produce is
the board. Three things are pinned here, each with the failure it prevents:

* R and L of one segment are ONE piece of copper, so they are in SERIES. Written
  between the same two nodes they become a parallel pair, and at DC an ideal
  inductor wins: the board answered 0.000000 mV with 100 % of the current in the
  L elements. `test_the_drop_across_the_strip_is_not_zero` is the control.
* A node id is unique only inside one net, so resolving the return net's node
  through the current net's map pointed at the segment itself (measured: the
  shunt C vanished, C=0). `test_the_shunt_returns_to_the_return_net` holds both
  ends of that: the far node is the return net's, and a board with no return net
  says C=0 out loud instead of inventing one.
* The DC answer must equal `board_ir_drop`'s, because both read the SAME graph.
  Two extractors would be the obvious way to write this and the certain way to
  have a deck and a report disagree on one board.
"""
import math
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp import oracle as O  # noqa: E402
from pcb_agent_mcp.adapters import kicad_pcb as K  # noqa: E402
from pcb_agent_mcp.sim import board as SB  # noqa: E402
from pcb_agent_mcp.sim import run as SR  # noqa: E402

C0 = SB.C0
PLATING_UM = 25.0

# 20 mm of 0.5 mm 35 um copper: R = 1.724e-8*20e-3/(0.5e-3*35e-6) = 19.7029 mOhm
STRIP_R = 0.019702857142857143

STACKUP = """  (setup (stackup
      (layer "F.Cu" (type "copper") (thickness 0.035))
      (layer "dielectric 1" (type "core") (thickness 1.51) (epsilon_r 4.5))
      (layer "B.Cu" (type "copper") (thickness 0.035))))"""

POUR = """  (zone (net 2) (net_name "GND") (layer "B.Cu") (tstamp 1) (hatch edge 0.5)
    (connect_pads (clearance 0.2))
    (min_thickness 0.25)
    (fill yes (thermal_gap 0.5) (thermal_bridge_width 0.5))
    (polygon (pts (xy 2 2) (xy 48 2) (xy 48 28) (xy 2 28)))
    (filled_polygon (layer "B.Cu") (pts (xy 2 2) (xy 48 2) (xy 48 28)
                                        (xy 2 28))))"""

PADS = """  (footprint "T:A" (layer "F.Cu") (at 10 10)
    (fp_text reference "U1" (at 0 -2) (layer "F.SilkS"))
    (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") (net 1 "VCC")))
  (footprint "T:B" (layer "F.Cu") (at 30 10)
    (fp_text reference "U2" (at 0 -2) (layer "F.SilkS"))
    (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") (net 1 "VCC")))
  (footprint "T:G" (layer "F.Cu") (at 40 20)
    (fp_text reference "J1" (at 0 -2) (layer "F.SilkS"))
    (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") (net 2 "GND")))"""

STRIP = '  (segment (start 10 10) (end 30 10) (width 0.5) (layer "F.Cu") (net 1))'
# on the pad's OWN layer: a B.Cu track under an F.Cu pad is a different node, and
# the pad would then be an island with nothing attached (which is now refused)
GND_STUB = '  (segment (start 40 20) (end 40 22) (width 0.5) (layer "F.Cu") (net 2))'


def _board(stackup=True, pour=True):
    text = ['(kicad_pcb (version 20221018) (generator pcbnew)',
            '  (general (thickness 1.6))',
            '  (layers (0 "F.Cu" signal) (31 "B.Cu" signal) (44 "Edge.Cuts" user))']
    if stackup:
        text.append(STACKUP)
    text.append('  (net 0 "") (net 1 "VCC") (net 2 "GND")')
    text.append('  (gr_rect (start 0 0) (end 50 30) (layer "Edge.Cuts") (width 0.1))')
    text.append(PADS)
    text.append(STRIP)
    text.append(GND_STUB)
    if pour:
        text.append(POUR)
    text.append(')')
    path = os.path.join(tempfile.gettempdir(),
                        "board_sim_%d_%d.kicad_pcb" % (os.getpid(), id(text)))
    with open(path, "w", newline="") as fh:
        fh.write("\n".join(text) + "\n")
    return K.parse_board(path)


def _lines_of(deck):
    return [ln for ln in deck.splitlines() if ln and not ln.startswith("*")]


class TestTheDeckIsTheBoard(unittest.TestCase):
    def test_the_drop_across_the_strip_is_not_zero(self):
        """R and L in parallel short the segment at DC; in series they do not."""
        b = _board()
        out = SR.simulate(b, nets=["VCC"], ground="U1.1",
                          drives=[{"at": "U2.1", "kind": "current",
                                   "current_a": 1.0}],
                          plating_um=PLATING_UM, engine="builtin")
        self.assertEqual(out["status"], "ok", out)
        nodes = out["value"]["board_nodes_v"]
        seen = {k.lower(): v for k, v in nodes.items()}
        # the far pad carries the whole drop; 0 would mean the L shorted it out
        self.assertAlmostEqual(abs(seen["u2_1"]), STRIP_R, places=9)

    def test_the_dc_drop_equals_the_ir_drop_report(self):
        # both sides read oracle.board_ir_drop's graph, so this is a mirror, not a
        # second opinion - which is exactly why it can catch a wiring mistake
        b = _board()
        ir = O.board_ir_drop(b, "VCC", "U1.1", ["U2.1"], current_a=1.0,
                             plating_um=PLATING_UM)
        self.assertEqual(ir["status"], "ok", ir)
        out = SR.simulate(b, nets=["VCC"], ground="U1.1",
                          drives=[{"at": "U2.1", "kind": "current",
                                   "current_a": 1.0}],
                          plating_um=PLATING_UM, engine="builtin")
        got = abs(out["value"]["board_nodes_v"]["U2_1"]) * 1000.0
        self.assertAlmostEqual(got, ir["value"]["max_drop_mv"], places=6)

    def test_the_transmission_line_identity(self):
        """L' * C' == eps_eff / c0^2 by construction: one model, two numbers."""
        ln = SB._Lines()
        got = ln.per_mm("microstrip", 0.5, 1.51, 35.0, 4.5)
        self.assertIsNotNone(got)
        # the cache is per MILLIMETRE, so both numbers carry a 1e-3 and the
        # product carries 1e-6: L'(H/mm)*C'(F/mm) = eps_eff/c0^2 * 1e-6
        self.assertAlmostEqual(
            got["l_per_mm_h"] * got["c_per_mm_f"] * 1e6 / (got["eps_eff"] / C0 ** 2),
            1.0, places=9)

    def test_the_shunt_returns_to_the_return_net(self):
        b = _board()
        r = SB.build(b, plating_um=PLATING_UM)
        self.assertEqual(r["status"], "ok", r)
        v = r["value"]
        self.assertEqual(v["return_net"], "GND")
        self.assertEqual(v["counts"]["elements"]["C"], 1)
        caps = [ln for ln in _lines_of(v["deck"]) if ln.startswith("C")]
        self.assertEqual(len(caps), 1)
        ends = caps[0].split()[1:3]
        ret_nodes = set(t["node"] for t in v["terminals"] if t["net"] == "GND")
        self.assertTrue(set(ends) & ret_nodes,
                        "the shunt C must land on a node of the return net, got %s"
                        % (ends,))
        self.assertFalse(all(e.startswith("U") for e in ends),
                         "a node id is only unique inside one net; both ends "
                         "being VCC pads means the id was read across nets")

    def test_without_a_pour_there_is_no_return_and_the_C_is_zero(self):
        # a board that pours no copper of any net has no plane to return into:
        # the answer is C=0 with a reason, never a capacitor to an invented node
        b = _board(pour=False)
        r = SB.build(b, plating_um=PLATING_UM)
        self.assertEqual(r["status"], "ok", r)
        v = r["value"]
        self.assertIsNone(v["return_net"])
        self.assertEqual(v["return_net_why"], "the board pours copper of no net at all")
        self.assertEqual(v["counts"]["elements"]["C"], 0)
        # VCC's strip and GND's 2 mm stub: two pieces of copper, two resistors
        self.assertEqual(v["counts"]["elements"]["R"], 2)

    def test_no_stackup_refuses_L_and_C_and_still_answers_R(self):
        # with no stackup the copper thickness is also unknown, so R is not the
        # thing that survives - the answer NAMES the missing quantity per net
        b = _board(stackup=False)
        r = SB.build(b, plating_um=PLATING_UM)
        self.assertEqual(r["status"], "ok", r)
        v = r["value"]
        self.assertIsNotNone(v["stackup_refusal"])
        self.assertIn("no usable (stackup ...) block", v["stackup_refusal"])
        self.assertEqual(v["counts"]["elements"]["R"], 0)
        self.assertEqual({s["reason_code"] for s in v["skipped"]}, {"ARG_REQUIRED"})
        self.assertTrue(all("copper thickness" in s["reason"] for s in v["skipped"]),
                        v["skipped"])
        # give the thickness and R comes back, L and C do not
        w = SB.build(b, plating_um=PLATING_UM, copper_um=35.0)["value"]
        self.assertEqual(w["counts"]["elements"]["R"], 2)
        self.assertEqual(w["counts"]["elements"]["L"], 0)
        self.assertEqual(w["counts"]["elements"]["C"], 0)
        self.assertTrue(any("states no stackup" in x for x in w["not_modelled"]),
                        w["not_modelled"])

    def test_include_lc_false_says_the_deck_is_resistive_only(self):
        b = _board()
        r = SB.build(b, plating_um=PLATING_UM, include_lc=False)
        v = r["value"]
        self.assertEqual(v["counts"]["elements"]["L"], 0)
        self.assertEqual(v["counts"]["elements"]["C"], 0)
        self.assertTrue(any("include_lc=false" in x for x in v["not_modelled"]))


class TestWhatTheRunnerRefuses(unittest.TestCase):
    def test_no_drive_and_no_ground_is_under_determined(self):
        # every solution differs by a constant, so any voltage picked out of the
        # solver would be a choice. This used to reach the engine and come back
        # as "the netlist has no ground node", which names the wrong problem.
        out = SR.simulate(_board(), nets=["VCC"], plating_um=PLATING_UM,
                          engine="builtin")
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["reason_code"], "UNDERDETERMINED")
        self.assertIn("arbitrary constant", out["message"])

    def test_no_drive_but_a_ground_says_the_answer_is_zero(self):
        out = SR.simulate(_board(), nets=["VCC", "GND"], ground="J1.1",
                          plating_um=PLATING_UM, engine="builtin")
        self.assertEqual(out["status"], "ok", out)
        notes = " ".join(out["value"]["board"]["notes"])
        self.assertIn("no drive was given", notes)
        for v in out["value"]["board_nodes_v"].values():
            self.assertAlmostEqual(v, 0.0, places=12)

    def test_a_net_that_is_not_on_the_board_is_named(self):
        r = SB.build(_board(), nets=["NOPE"], plating_um=PLATING_UM)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "ARG_INVALID")
        self.assertIn("NOPE", r["message"])
        self.assertIn("VCC", r["hint"])

    def test_a_ground_that_is_not_on_the_board_is_refused(self):
        out = SR.simulate(_board(), nets=["VCC"], ground="Q9.7",
                          plating_um=PLATING_UM, engine="builtin")
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["reason_code"], "ARG_INVALID")
        self.assertIn("Q9.7", out["message"])

    def test_a_transient_without_a_time_base_is_refused(self):
        out = SR.simulate(_board(), nets=["VCC"], ground="U1.1",
                          analysis={"kind": "tran"}, plating_um=PLATING_UM)
        self.assertEqual(out["status"], "error")
        self.assertIn("tstep_s", out["message"])

    def test_the_ground_tie_carries_the_return_current_when_the_loop_uses_copper(self):
        # source -> VCC copper -> load -> the GND pad -> the 0 V tie. The load's
        # ref is on the OTHER net, so the loop goes through the board and the tie
        # current is the board's return current.
        out = SR.simulate(_board(), nets=["VCC", "GND"], ground="J1.1",
                          drives=[{"at": "U1.1", "kind": "dc", "voltage_v": 1.0,
                                   "ref": "J1.1"}],
                          loads=[{"at": "U2.1", "r_ohm": 100.0, "ref": "0"}],
                          plating_um=PLATING_UM, engine="builtin")
        self.assertEqual(out["status"], "ok", out)
        self.assertIn("ground_current_a", out["value"])
        self.assertAlmostEqual(abs(out["value"]["ground_current_a"]),
                               1.0 / (100.0 + STRIP_R), places=6)

    def test_a_loop_inside_one_net_carries_nothing_at_the_tie(self):
        # the same source referenced to the ground node closes inside itself, so
        # the tie reads 0 - and the note says why, instead of calling 0 the return
        out = SR.simulate(_board(), nets=["VCC"], ground="U1.1",
                          drives=[{"at": "U2.1", "kind": "current",
                                   "current_a": 1.0}],
                          plating_um=PLATING_UM, engine="builtin")
        self.assertEqual(out["status"], "ok", out)
        self.assertAlmostEqual(abs(out["value"]["ground_current_a"]), 0.0, places=9)
        self.assertIn("closes its loop inside itself",
                      " ".join(out["value"]["board"]["notes"]))
        # and the engine that reports only node voltages has to say so
        o2 = SR.simulate(_board(), nets=["VCC"], ground="U1.1",
                         drives=[{"at": "U2.1", "kind": "current",
                                  "current_a": 1.0}],
                         plating_um=PLATING_UM, engine="ngspice")
        if o2["status"] == "ok":
            self.assertNotIn("ground_current_a", o2["value"])
            self.assertIn("not reported",
                          " ".join(o2["value"]["board"]["notes"]))


class TestTheDeckSolves(unittest.TestCase):
    def test_a_rc_on_the_boards_own_copper(self):
        # the copper's own shunt C (1.1 pF over 20 mm) is far too small to see at
        # 1 kHz, so the answer is the strip's R again - the point is that the deck
        # is solvable in AC at all and that R, not L, sets it
        # no GND net here on purpose: without model_zones the pour is not a
        # conductor, so the only return-net node would be a dangling stub and an
        # AC solve on it is singular - correctly
        out = SR.simulate(_board(), nets=["VCC"], ground="U1.1",
                          drives=[{"at": "U2.1", "kind": "current",
                                   "current_a": 1e-6}],
                          analysis={"kind": "ac", "fstart_hz": 1e3,
                                    "fstop_hz": 1e6, "points": 1},
                          outputs=["U2.1"], plating_um=PLATING_UM, engine="builtin")
        self.assertEqual(out["status"], "ok", out)
        pts = out["value"]["points"]
        self.assertTrue(pts)
        self.assertEqual({k.lower() for p_ in pts for k in p_["nodes_v"]}, {"u2_1"})

    def test_every_answer_carries_what_it_did_not_model(self):
        out = SR.simulate(_board(), nets=["VCC", "GND"], ground="J1.1",
                          plating_um=PLATING_UM, engine="builtin")
        nm = out["value"]["board"]["not_modelled"]
        self.assertTrue(any("mutual inductance" in x for x in nm))
        self.assertTrue(any("plane SHEET" in x for x in nm))
        self.assertIn("extraction of the board file", out["basis"])


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# One net is not always one piece of copper. Measured on the KiCad demo corpus:
# every finished board (stickhub, video, cm5_minima, interf_u, openair-max, ...)
# made `board_ir_drop` answer NOT_APPLICABLE "singular at column 181" - a
# linear-algebra fact about the deck that says nothing about the board. The truth
# was that a net's pads sit on several disconnected pieces of copper (pads a pour
# would have joined, pads nothing reaches), and a floating island either makes
# the matrix singular or - worse - gets gmin-solved into plausible numbers.
# ---------------------------------------------------------------------------

ISLAND_PADS = """  (footprint "T:A" (layer "F.Cu") (at 10 10)
    (fp_text reference "U1" (at 0 -2) (layer "F.SilkS"))
    (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") (net 1 "VCC")))
  (footprint "T:B" (layer "F.Cu") (at 30 10)
    (fp_text reference "U2" (at 0 -2) (layer "F.SilkS"))
    (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") (net 1 "VCC")))
  (footprint "T:C" (layer "F.Cu") (at 40 25)
    (fp_text reference "U3" (at 0 -2) (layer "F.SilkS"))
    (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") (net 1 "VCC")))
  (footprint "T:D" (layer "F.Cu") (at 45 27)
    (fp_text reference "U4" (at 0 -2) (layer "F.SilkS"))
    (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") (net 1 "VCC")))
  (footprint "T:G" (layer "F.Cu") (at 40 20)
    (fp_text reference "J1" (at 0 -2) (layer "F.SilkS"))
    (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") (net 2 "GND")))
  (footprint "T:E" (layer "F.Cu") (at 5 5)
    (fp_text reference "U5" (at 0 -2) (layer "F.SilkS"))
    (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") (net 3 "V33")))
  (footprint "T:F" (layer "F.Cu") (at 20 5)
    (fp_text reference "U6" (at 0 -2) (layer "F.SilkS"))
    (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") (net 3 "V33")))"""

ISLAND_TRACKS = """  (segment (start 10 10) (end 30 10) (width 0.5) (layer "F.Cu") (net 1))
  (segment (start 40 25) (end 40 26) (width 0.5) (layer "F.Cu") (net 1))
  (segment (start 40 20) (end 40 22) (width 0.5) (layer "F.Cu") (net 2))
  (segment (start 5 5) (end 20 5) (width 0.5) (layer "F.Cu") (net 3))"""


def _island_board():
    """VCC has three pieces: U1-U2 (one strip), U3 (its own little stub), U4 (a
    pad no track touches). V33 has a strip and nothing wires it. GND has one pad."""
    text = ['(kicad_pcb (version 20221018) (generator pcbnew)',
            '  (general (thickness 1.6))',
            '  (layers (0 "F.Cu" signal) (31 "B.Cu" signal) (44 "Edge.Cuts" user))',
            STACKUP,
            '  (net 0 "") (net 1 "VCC") (net 2 "GND") (net 3 "V33")',
            '  (gr_rect (start 0 0) (end 50 30) (layer "Edge.Cuts") (width 0.1))',
            ISLAND_PADS, ISLAND_TRACKS, POUR, ')']
    path = os.path.join(tempfile.gettempdir(),
                        "board_islands_%d.kicad_pcb" % os.getpid())
    with open(path, "w", newline="") as fh:
        fh.write("\n".join(text) + "\n")
    return K.parse_board(path)


class TestOneNetIsNotAlwaysOnePiece(unittest.TestCase):

    def test_an_island_that_no_wired_pad_touches_is_dropped_and_named(self):
        b = _island_board()
        v = SB.build(b, plating_um=PLATING_UM,
                     keep_pads=["U1.1", "U2.1", "J1.1"])["value"]
        vcc = [n for n in v["nets"] if n["net"] == "VCC"][0]
        self.assertEqual(vcc["nodes"], 2)          # the strip only
        self.assertNotIn("U3.1", [t["pad"] for t in v["terminals"]])
        self.assertNotIn("U4.1", [t["pad"] for t in v["terminals"]])
        codes = {}
        for s in v["skipped"]:
            codes.setdefault(s["reason_code"], []).extend(s.get("pads") or [])
        self.assertIn("U3.1", codes.get("ISLAND_NOT_WIRED", []))
        self.assertIn("U4.1", codes.get("PAD_WITHOUT_COPPER", []))
        self.assertIn("V33", [s["net"] for s in v["skipped"]
                              if s["reason_code"] == "FLOATING_NET"])

    def test_the_wired_strip_still_answers_the_ir_drop(self):
        b = _island_board()
        out = SR.simulate(b, nets=["VCC"], ground="U1.1",
                          drives=[{"at": "U2.1", "kind": "current",
                                   "current_a": 1.0}],
                          include_lc=False, plating_um=PLATING_UM)
        self.assertEqual(out["status"], "ok", out)
        got = out["value"]["board_nodes_v"]["u2_1"] * 1000.0
        self.assertAlmostEqual(got, STRIP_R * 1000.0, places=6)
        self.assertTrue(any("not in the deck" in n
                            for n in out["value"]["board"]["notes"]))

    def test_a_net_that_nothing_wires_does_not_make_the_solve_singular(self):
        # this is `nets=None` on a board with a rail nothing is attached to: the
        # rail must be named in skipped, not turned into a linear-algebra error
        b = _island_board()
        out = SR.simulate(b, nets=None, ground="U1.1",
                          drives=[{"at": "U2.1", "kind": "current",
                                   "current_a": 1.0, "ref": "U1.1"}],
                          include_lc=False, plating_um=PLATING_UM)
        self.assertEqual(out["status"], "ok", out)
        nets = [s["net"] for s in out["value"]["board"]["skipped"]
                if s["reason_code"] == "FLOATING_NET"]
        self.assertIn("V33", nets)
        self.assertAlmostEqual(out["value"]["board_nodes_v"]["u2_1"] * 1000.0,
                               STRIP_R * 1000.0, places=6)

    def test_a_pad_with_no_copper_cannot_be_grounded(self):
        b = _island_board()
        out = SR.simulate(b, nets=["VCC"], ground="U4.1",
                          drives=[{"at": "U2.1", "kind": "current",
                                   "current_a": 1.0}],
                          include_lc=False, plating_um=PLATING_UM)
        self.assertEqual(out["status"], "error", out)
        self.assertEqual(out["reason_code"], "NOT_CONNECTED")
        self.assertIn("U4.1", out["message"])
        self.assertIn("no track", out["message"])

    def test_a_capacitor_is_not_a_dc_path_to_ground(self):
        # a load whose ref is the engine's 0 does not reference anything at DC if
        # it is a capacitor; counting it would call a floating island connected
        b = _island_board()
        out = SR.simulate(b, nets=["VCC"], ground="U3.1",
                          loads=[{"at": "U1.1", "c_f": 1e-9}],
                          include_lc=False, plating_um=PLATING_UM)
        self.assertEqual(out["status"], "error", out)
        self.assertEqual(out["reason_code"], "NOT_CONNECTED")
        self.assertIn("no resistive or source path", out["message"])

    def test_the_ir_drop_tool_names_the_pad_instead_of_the_column(self):
        b = _island_board()
        ir = O.board_ir_drop(b, "VCC", "U1.1", ["U3.1"], current_a=1.0,
                             plating_um=PLATING_UM)
        self.assertEqual(ir["status"], "error", ir)
        self.assertEqual(ir["reason_code"], "NOT_CONNECTED")
        self.assertIn("U3.1", ir["message"])
