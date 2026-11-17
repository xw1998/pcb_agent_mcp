# -*- coding: utf-8 -*-
"""Tests for the three "can I see / can I judge it" tools.

board_net_report, board_pour_audit and board_render exist because the previous
rounds could measure a board but neither point at the net that took the scenic
route nor look at the picture. A test that only asserts "status == ok" would let
all three report nonsense, so every case here has a number worked out by hand and
a REVERSE control that must flip the verdict.
"""
import os
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp import oracle, snapshot  # noqa: E402
from pcb_agent_mcp.adapters import kicad_pcb  # noqa: E402

STACKUP = """(setup (stackup (layer "F.Cu" (type "copper") (thickness 0.035))
    (layer "dielectric 1" (type "core") (thickness 0.2) (epsilon_r 4.3))
    (layer "B.Cu" (type "copper") (thickness 0.035))))"""


def square_board(tracks="", zones=""):
    """40x40 board with one four-pad net per corner of a 10x10 square."""
    pads = """
  (footprint "T:P" (layer "F.Cu") (at 10 10)
    (fp_text reference "A1" (at 0 0) (layer "F.SilkS"))
    (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") (net 1 "SIG")))
  (footprint "T:P" (layer "F.Cu") (at 20 10)
    (fp_text reference "A2" (at 0 0) (layer "F.SilkS"))
    (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") (net 1 "SIG")))
  (footprint "T:P" (layer "F.Cu") (at 20 20)
    (fp_text reference "A3" (at 0 0) (layer "F.SilkS"))
    (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") (net 1 "SIG")))
  (footprint "T:P" (layer "F.Cu") (at 10 20)
    (fp_text reference "A4" (at 0 0) (layer "F.SilkS"))
    (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") (net 1 "SIG")))
  (footprint "T:P" (layer "F.Cu") (at 30 30)
    (fp_text reference "G1" (at 0 0) (layer "F.SilkS"))
    (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") (net 2 "GND")))
"""
    return """(kicad_pcb (version 20221018) %s
      (net 0 "") (net 1 "SIG") (net 2 "GND")
      (gr_rect (start 0 0) (end 40 40) (layer "Edge.Cuts") (width 0.1) (fill none))
%s%s%s
    )""" % (STACKUP, pads, tracks, zones)


# 10 + 10 + 10 = 30 mm is the minimum tree over the four SIG pads (a 10x10 square).
STRAIGHT = """
  (segment (start 10 10) (end 20 10) (width 0.25) (layer "F.Cu") (net 1))
  (segment (start 20 10) (end 20 20) (width 0.25) (layer "F.Cu") (net 1))
  (segment (start 20 20) (end 10 20) (width 0.25) (layer "F.Cu") (net 1))
"""
# Same tree, but the middle leg goes out to y=30 and back: 10 + 10 + 10 + 20 + 10.
DETOUR = """
  (segment (start 10 10) (end 20 10) (width 0.25) (layer "F.Cu") (net 1))
  (segment (start 20 10) (end 20 30) (width 0.25) (layer "F.Cu") (net 1))
  (segment (start 20 30) (end 20 20) (width 0.25) (layer "F.Cu") (net 1))
  (segment (start 20 20) (end 10 20) (width 0.25) (layer "F.Cu") (net 1))
"""


def zone_with(extra_poly):
    """GND pour on B.Cu: one island over the G1 pad, plus whatever `extra_poly` is."""
    return """
  (zone (net 2) (net_name "GND") (layer "B.Cu") (hatch edge 0.5)
    (connect_pads (clearance 0.2))
    (min_thickness 0.25)
    (fill yes)
    (polygon (pts (xy 29 29) (xy 31 29) (xy 31 31) (xy 29 31)))
    (filled_polygon (layer "B.Cu") (pts (xy 29 29) (xy 31 29) (xy 31 31) (xy 29 31)))
    %s
  )
""" % extra_poly


# One arc from 10,10 over 15,15 to 20,10 (file frame): 5 mm of bulge above its own
# chord. Drawn as a chord it is a straight line and this board is the CHORD one.
ARC_SNAKE = """
  (arc (start 10 10) (mid 15 15) (end 20 10) (width 0.25) (layer "F.Cu") (net 1))
"""
# The reverse control: the SAME endpoints as one straight segment. If the renderer
# draws an arc as its chord these two boards render pixel-identically.
CHORD_SNAKE = """
  (segment (start 10 10) (end 20 10) (width 0.25) (layer "F.Cu") (net 1))
"""

DEAD_ISLAND = '(filled_polygon (layer "B.Cu") (pts (xy 2 2) (xy 6 2) (xy 6 6) (xy 2 6)))'


def board(text):
    return kicad_pcb.parse_board_text(text)


class TestNetGeometryReport(unittest.TestCase):
    def test_minimum_tree_route_scores_one(self):
        out = oracle.net_geometry_report(board(square_board(STRAIGHT)),
                                         min_segments=1)
        self.assertEqual(out["status"], "ok", out)
        sig = [n for n in out["value"]["nets"] if n["net"] == "SIG"][0]
        self.assertAlmostEqual(sig["length_mm"], 30.0, places=3)
        self.assertAlmostEqual(sig["pad_mst_mm"], 30.0, places=3)
        self.assertAlmostEqual(sig["detour_ratio"], 1.0, places=2)
        self.assertEqual(sig["flags"], [])
        self.assertEqual(sig["pads"], 4)

    def test_scenic_route_is_flagged_and_the_reverse_control_is_not(self):
        out = oracle.net_geometry_report(board(square_board(DETOUR)),
                                         min_segments=1, detour_flag=1.5)
        sig = [n for n in out["value"]["nets"] if n["net"] == "SIG"][0]
        self.assertAlmostEqual(sig["length_mm"], 50.0, places=3)
        self.assertAlmostEqual(sig["detour_ratio"], 50.0 / 30.0, places=2)
        self.assertIn("long_detour", sig["flags"])
        self.assertIn("SIG", out["value"]["flagged"])

    def test_power_net_thin_flag_needs_a_properly_thin_power_net(self):
        text = square_board(STRAIGHT).replace('(net 1 "SIG")', '(net 1 "+3V3")')\
               .replace("(width 0.25)", "(width 0.15)")
        out = oracle.net_geometry_report(board(text), min_segments=1)
        v = [n for n in out["value"]["nets"] if n["net"] == "+3V3"][0]
        self.assertIn("power_net_thin", v["flags"])
        wide = oracle.net_geometry_report(
            board(text.replace("(width 0.15)", "(width 0.5)")), min_segments=1)
        w = [n for n in wide["value"]["nets"] if n["net"] == "+3V3"][0]
        self.assertNotIn("power_net_thin", w["flags"])


class TestPourAudit(unittest.TestCase):
    def test_island_with_a_pad_is_not_dead(self):
        out = oracle.pour_audit(board(square_board(zones=zone_with(""))))
        self.assertEqual(out["status"], "ok", out)
        z = out["value"]["zones"][0]
        self.assertEqual(z["dead_islands"], 0)
        self.assertEqual(out["value"]["verdict"], "pour_geometry_clean")
        self.assertEqual(z["islands"], 1)
        self.assertEqual(z["island_detail"][0]["pads"], 1)

    def test_island_with_nothing_is_dead_and_the_reverse_control_is_clean(self):
        out = oracle.pour_audit(board(square_board(zones=zone_with(DEAD_ISLAND))))
        z = out["value"]["zones"][0]
        self.assertEqual(z["islands"], 2)
        self.assertEqual(z["dead_islands"], 1)
        self.assertEqual(out["value"]["verdict"], "pour_dead_copper")
        dead = [i for i in z["island_detail"] if i["dead_copper"]]
        self.assertEqual(len(dead), 1)
        self.assertAlmostEqual(dead[0]["area_mm2"], 16.0, places=2)

    def test_short_nets_are_filtered_by_default(self):
        """The default min_segments is a filter, not a silent truncation of length."""
        out = oracle.net_geometry_report(board(square_board(STRAIGHT)))
        self.assertEqual(out["value"]["nets_measured"], 0)
        out2 = oracle.net_geometry_report(board(square_board(STRAIGHT)), min_segments=1)
        self.assertEqual(out2["value"]["nets_measured"], 1)

    def test_a_via_makes_an_otherwise_dead_island_live(self):
        """The bug this test exists for: counting pads only invented 10 defects."""
        via = ('(via (at 4 4) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net 2))')
        out = oracle.pour_audit(board(square_board(tracks=via,
                                                  zones=zone_with(DEAD_ISLAND))))
        z = out["value"]["zones"][0]
        self.assertEqual(z["dead_islands"], 0, z["island_detail"])
        self.assertEqual(out["value"]["verdict"], "pour_geometry_clean")


class TestRender(unittest.TestCase):
    def test_png_is_a_real_png_of_the_right_size(self):
        b = board(square_board(STRAIGHT))
        out = snapshot.render(b, layer="F.Cu", px_per_mm=5)
        self.assertEqual(out["status"], "ok", out)
        path = out["value"]["png"]
        try:
            with open(path, "rb") as fh:
                head = fh.read(33)
            self.assertEqual(head[:8], b"\x89PNG\r\n\x1a\n")
            w, h = struct.unpack(">II", head[16:24])
            self.assertEqual((w, h), (out["value"]["width_px"],
                                      out["value"]["height_px"]))
            self.assertGreaterEqual(w, 40 * 5)
        finally:
            os.unlink(path)

    def test_the_two_layers_do_not_render_identically(self):
        """A render that ignores `layer` would return the same bytes twice."""
        text = square_board(STRAIGHT, zone_with(""))
        b = board(text)
        a = snapshot.render(b, layer="F.Cu", px_per_mm=5,
                            out=os.path.join(tempfile.gettempdir(), "r_f.png"))
        c = snapshot.render(b, layer="B.Cu", px_per_mm=5,
                            out=os.path.join(tempfile.gettempdir(), "r_b.png"))
        try:
            fa = open(a["value"]["png"], "rb").read()
            fb = open(c["value"]["png"], "rb").read()
            self.assertNotEqual(fa, fb)
            self.assertGreater(len(fb), 100)
        finally:
            for p in (a["value"]["png"], c["value"]["png"]):
                os.unlink(p)

    def test_zero_resolution_is_refused_not_guessed(self):
        b = board(square_board(STRAIGHT))
        out = snapshot.render(b, layer="F.Cu", px_per_mm=0)
        self.assertEqual(out["status"], "error")

    def test_a_region_crops_the_canvas_to_the_window_asked_for(self):
        """Without a crop a 300 mm board is unreadable: the whole point is a window."""
        b = board(square_board(STRAIGHT))
        # explicit out paths: the default name is one per layer, so two renders of
        # the same layer would share it and the second would replace the first
        whole = snapshot.render(b, layer="F.Cu", px_per_mm=5,
                                out=os.path.join(tempfile.gettempdir(), "reg_whole.png"))
        # the window the square's four pads enclose; y is the server's usual Y-up
        # frame (file y 10..20 becomes -20..-10), not the file's own sign
        crop = snapshot.render(b, layer="F.Cu", px_per_mm=5,
                               region=[10.0, -20.0, 20.0, -10.0],
                               out=os.path.join(tempfile.gettempdir(), "reg_crop.png"))
        try:
            self.assertEqual(whole["status"], "ok", whole)
            self.assertEqual(crop["status"], "ok", crop)
            # 10 mm at 5 px/mm is 50 px plus the 2 px border, not the 40 mm board
            self.assertEqual(crop["value"]["width_px"], 52)
            self.assertEqual(crop["value"]["height_px"], 52)
            self.assertGreater(whole["value"]["width_px"], 3 * 52)
            self.assertEqual(crop["value"]["region_mm"], [10.0, -20.0, 20.0, -10.0])
        finally:
            for p in (whole["value"]["png"], crop["value"]["png"]):
                os.unlink(p)

    def test_a_backwards_region_is_refused_not_silently_widened(self):
        b = board(square_board(STRAIGHT))
        out = snapshot.render(b, layer="F.Cu", px_per_mm=5,
                             region=[20.0, 20.0, 10.0, 10.0])
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["reason_code"], "ARG_INVALID")

    def test_the_difference_says_which_side_the_change_is_on(self):
        """A diff that cannot tell 'added' from 'removed' is a rumour.

        STRAIGHT vs DETOUR: DETOUR adds two legs up to y=30 and drops the
        straight 20,10 -> 20,20 leg. So the extra copper is on the compare
        board, and reversing the two arguments must flip the counts.
        """
        straight = board(square_board(STRAIGHT))
        detour = board(square_board(DETOUR))
        t = tempfile.gettempdir()
        a = snapshot.render(straight, layer="F.Cu", px_per_mm=10, compare=detour,
                            out=os.path.join(t, "diff_a.png"))
        bb = snapshot.render(detour, layer="F.Cu", px_per_mm=10, compare=straight,
                             out=os.path.join(t, "diff_b.png"))
        same = snapshot.render(straight, layer="F.Cu", px_per_mm=10,
                               compare=straight,
                               out=os.path.join(t, "diff_same.png"))
        try:
            for out in (a, bb, same):
                self.assertEqual(out["status"], "ok", out)
            self.assertFalse(a["value"]["identical"])
            self.assertTrue(same["value"]["identical"], same["value"])
            self.assertEqual(same["value"]["pixels_only_here"], 0)
            self.assertEqual(same["value"]["pixels_only_there"], 0)
            # the extra legs are only on the compare board
            self.assertEqual(a["value"]["pixels_only_here"], 0)
            self.assertGreater(a["value"]["pixels_only_there"], 100)
            self.assertEqual(bb["value"]["pixels_only_there"], 0)
            self.assertEqual(bb["value"]["pixels_only_here"],
                             a["value"]["pixels_only_there"])
            # and it says WHERE: the changed window is the detour's legs, which the
            # file draws at y 20..30 and this server reports as y -30..-20
            x0, y0, x1, y1 = a["value"]["changed_bbox_mm"]
            self.assertLessEqual(y0, -29.0)
            self.assertGreaterEqual(y1, -21.0)
            self.assertLessEqual(x0, 21.0)
            self.assertGreaterEqual(x1, 19.0)
        finally:
            for out in (a, bb, same):
                if out.get("value", {}).get("png"):
                    os.unlink(out["value"]["png"])

    def test_an_arc_track_is_drawn_as_its_curve_not_its_chord(self):
        """The picture is the only proof of shape, so it must draw the curve.

        KiCad stores an arc as start/mid/end; the adapter samples it into
        `_points` so lengths stay honest. Drawing `start -> end` instead throws
        that away and turns a 5 mm bulge into a straight line - which is how a
        serpentine of small-radius arcs came out looking like a triangle. The
        reverse control is the same endpoints as one segment: drawing chords
        would make these two boards pixel-identical.
        """
        arc = board(square_board(ARC_SNAKE))
        chord = board(square_board(CHORD_SNAKE))
        self.assertEqual(len(arc.tracks), 1, arc.tracks)
        self.assertTrue(arc.tracks[0].get("_arc"))
        self.assertGreaterEqual(len(arc.tracks[0].get("_points") or []), 3)
        t = tempfile.gettempdir()
        a = snapshot.render(arc, layer="F.Cu", px_per_mm=20,
                            out=os.path.join(t, "arc_only.png"))
        c = snapshot.render(chord, layer="F.Cu", px_per_mm=20,
                            out=os.path.join(t, "arc_chord.png"))
        d = snapshot.render(arc, layer="F.Cu", px_per_mm=20, compare=chord,
                            out=os.path.join(t, "arc_vs_chord.png"))
        try:
            for out in (a, c, d):
                self.assertEqual(out["status"], "ok", out)
            # the count says which primitive was drawn, not just that ink happened
            self.assertEqual(a["value"]["drawn"]["arcs"], 1, a["value"]["drawn"])
            self.assertEqual(a["value"]["drawn"]["tracks"], 0, a["value"]["drawn"])
            self.assertEqual(c["value"]["drawn"]["arcs"], 0, c["value"]["drawn"])
            self.assertEqual(c["value"]["drawn"]["tracks"], 1, c["value"]["drawn"])
            # copper the arc has and the chord does not (above the chord)
            self.assertFalse(d["value"]["identical"], d["value"])
            self.assertGreater(d["value"]["pixels_only_here"], 200, d["value"])
            # and copper the chord has and the arc does not (its interior)
            self.assertGreater(d["value"]["pixels_only_there"], 200, d["value"])
            # and WHERE: the bulge reaches file y 15, this server reports y -15
            x0, y0, x1, y1 = d["value"]["changed_bbox_mm"]
            self.assertLessEqual(y0, -14.0, d["value"]["changed_bbox_mm"])
            self.assertGreaterEqual(y1, -10.5, d["value"]["changed_bbox_mm"])
            self.assertLessEqual(x0, 10.5)
            self.assertGreaterEqual(x1, 19.5)
        finally:
            for out in (a, c, d):
                p = out.get("value", {}).get("png")
                if p:
                    os.unlink(p)

    def test_a_compare_that_cannot_be_read_is_an_error(self):
        b = board(square_board(STRAIGHT))
        out = snapshot.render(b, layer="F.Cu", px_per_mm=5,
                             compare=os.path.join(tempfile.gettempdir(),
                                                  "no_such_board_xyz.kicad_pcb"))
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["reason_code"], "PARSE_FAILED")


# --- angle audit fixtures --------------------------------------------------
# A 45-chamfered turn: 10,10 -> 20,10 straight, then 20,10 -> 21,11 (45), then
# 21,11 -> 21,20 vertical. Every heading is H / 45 / V, and the only bend is 135.
CHAMFER = """
  (segment (start 10 10) (end 20 10) (width 0.25) (layer "F.Cu") (net 1))
  (segment (start 20 10) (end 21 11) (width 0.25) (layer "F.Cu") (net 1))
  (segment (start 21 11) (end 21 19) (width 0.25) (layer "F.Cu") (net 1))
  (segment (start 21 19) (end 20 20) (width 0.25) (layer "F.Cu") (net 1))
  (segment (start 20 20) (end 10 20) (width 0.25) (layer "F.Cu") (net 1))
"""
# Same board with the chamfer deleted: the H leg runs straight into a V leg and
# the vertex is a square 90 corner.
SQUARE = """
  (segment (start 10 10) (end 21 10) (width 0.25) (layer "F.Cu") (net 1))
  (segment (start 21 10) (end 21 20) (width 0.25) (layer "F.Cu") (net 1))
  (segment (start 21 20) (end 10 20) (width 0.25) (layer "F.Cu") (net 1))
"""
# Nothing wrong with the headings, but one off-45 heading drawn on purpose.
ODD = """
  (segment (start 10 10) (end 20 10) (width 0.25) (layer "F.Cu") (net 1))
  (segment (start 20 10) (end 22 11) (width 0.25) (layer "F.Cu") (net 1))
"""
# A branch teeing off the middle of a trunk at 90: this is a tee, not a bend.
TEE = """
  (segment (start 10 15) (end 30 15) (width 0.5) (layer "F.Cu") (net 1))
  (segment (start 20 15) (end 20 11) (width 0.25) (layer "F.Cu") (net 1))
"""


class TestAngleAudit(unittest.TestCase):
    def test_a_chamfered_route_has_no_off_45_segment_and_no_square_corner(self):
        out = oracle.angle_audit(board(square_board(CHAMFER)))
        self.assertEqual(out["status"], "ok", out)
        st = out["value"]["per_layer"]["F.Cu"]
        self.assertEqual(st["other"], 0)
        self.assertEqual((st["H"], st["V"], st["diag45"]), (2, 1, 2))
        c = out["value"]["corners"]["F.Cu"]
        self.assertEqual(c["corner_90"], 0)
        self.assertEqual(c["corner_acute"], 0)
        # 20,10 ; 21,11 ; 21,19 ; 20,20 are the four turns, all 135
        self.assertEqual(c["corner_135"], 4)
        self.assertEqual(out["value"]["verdict"], "angles_clean")

    def test_removing_the_chamfer_turns_the_same_route_into_a_square_corner(self):
        """Reverse control: identical topology, chamfer deleted -> verdict flips."""
        out = oracle.angle_audit(board(square_board(SQUARE)))
        self.assertEqual(out["value"]["per_layer"]["F.Cu"]["other"], 0)
        c = out["value"]["corners"]["F.Cu"]
        self.assertEqual(c["corner_90"], 2)
        self.assertEqual(c["corner_135"], 0)
        self.assertIn("right_angle_corner@F.Cu", out["value"]["problems"])
        self.assertEqual(out["value"]["verdict"],
                         "angles_right_angle_corner@F.Cu")
        self.assertEqual(out["value"]["corners"]["F.Cu"]["right_angle_list"][0]["x"], 21.0)

    def test_an_off_45_heading_is_the_only_rule_violation(self):
        out = oracle.angle_audit(board(square_board(ODD)))
        st = out["value"]["per_layer"]["F.Cu"]
        self.assertEqual(st["other"], 1)
        h = st["other_segments"][0]["heading_deg"]
        self.assertGreater(min(abs(h), abs(h - 45), abs(h - 90),
                               abs(h - 135), abs(h - 180)), 5.0)
        self.assertAlmostEqual(st["other_pct"], 50.0, places=2)
        self.assertIn("other_heading@F.Cu", out["value"]["problems"])

    def test_a_tee_is_reported_as_a_branch_not_as_a_corner(self):
        out = oracle.angle_audit(board(square_board(TEE)))
        t = out["value"]["tees"]["F.Cu"]
        self.assertEqual(t["count"], 1)
        self.assertEqual(t["square"], 1)
        self.assertEqual(t["list"][0]["tee_angle_deg"], 90.0)
        # The ee must NOT be scored as a square corner.
        self.assertEqual(out["value"]["corners"]["F.Cu"]["corner_90"], 0)
        self.assertEqual(out["value"]["verdict"], "angles_clean")

    def test_a_via_split_vertex_is_invisible_and_the_tool_says_so(self):
        """Two collinear halves are not a corner: precision note must carry that."""
        out = oracle.angle_audit(board(square_board(CHAMFER)))
        self.assertIn("share an endpoint", out["precision"])



if __name__ == "__main__":
    unittest.main()
