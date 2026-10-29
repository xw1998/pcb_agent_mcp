"""Pad placement must equal what KiCad itself says, not what we find plausible.

Why this file exists
--------------------
A "Y flip reverses the sense of rotation" one-liner in the `.kicad_pcb` adapter
negated every footprint angle. Nothing crashed, no test failed, and the adapter
still produced self-consistent geometry -- but on the KiCad demo boards up to 1030
pads of 2118 sat up to 111 mm away from where `pcbnew` puts them. The Oracle then
measured clearance between copper that is not there and reported phantom
violations on a board whose DRC is clean.

So the expectations below are not derived from our own maths: every number in
`PAD_TRUTH` was printed by `pcbnew` 10.0.7 (KiCad's own Python API) loading the
same text. The sweep at the bottom re-derives them from the installed KiCad and
is skipped when KiCad is not on this machine -- it is the same check, just not
pinned to four hard-coded cases.

Coordinate note: `pcbnew` reports the file frame (Y grows down); the canonical
board model mirrors Y, so `canonical_y == -pcbnew_y`.
"""
import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp.adapters import kicad_pcb  # noqa: E402
from pcb_agent_mcp.fidelity import engine  # noqa: E402

# (label, board text, [(ref, pad number, pcbnew x, pcbnew y, pcbnew angle)])
PAD_TRUTH = [
    ("footprint rotated 90 deg, pad written without an angle",
     """(kicad_pcb (version 20221018)
  (net 0 "") (net 1 "A")
  (footprint "T:X" (layer "F.Cu") (at 10 10 90)
    (property "Reference" "U1" (at 0 0 0) (layer "F.SilkS"))
    (pad "1" smd rect (at 2 0) (size 1 1) (layers "F.Cu") (net 1 "A"))
  )
)""",
     [("U1", "1", 10.0, 8.0, 0.0)]),

    ("footprint rotated 45 deg: two pads, both off-axis",
     """(kicad_pcb (version 20221018)
  (net 0 "") (net 1 "A")
  (footprint "T:X" (layer "F.Cu") (at 10 10 45)
    (property "Reference" "U1" (at 0 0 0) (layer "F.SilkS"))
    (pad "1" smd rect (at 2 0) (size 1 2) (layers "F.Cu") (net 1 "A"))
    (pad "2" smd rect (at 0 2) (size 1 2) (layers "F.Cu") (net 1 "A"))
  )
)""",
     [("U1", "1", 11.414214, 8.585786, 0.0),
      ("U1", "2", 11.414214, 11.414214, 0.0)]),

    # Taken from KiCad's own demo board pic_programmer (JP1, a back-side solder
    # jumper): the file already stores the pad offsets as drawn, so mirroring them
    # again on account of the B.Cu layer put 260 pads 3.1 mm off on `video`.
    ("back-side footprint with a custom pad (pic_programmer JP1)",
     """(kicad_pcb (version 20221018)
  (net 0 "") (net 1 "VCC")
  (footprint "Jumper:SolderJumper-2_P1.3mm_Open_TrianglePad1.0x1.5mm"
    (layer "B.Cu") (at 148.082 97.79)
    (property "Reference" "JP1" (at 0 1.8 180) (layer "B.SilkS"))
    (pad "1" smd custom (at -0.725 0)
      (size 0.3 0.3) (layers "B.Cu" "B.Mask") (net 1 "VCC")
      (options (clearance outline) (anchor rect))
      (primitives
        (gr_poly (pts (xy 1 0) (xy 0.5 -0.75) (xy -0.5 -0.75) (xy -0.5 0.75) (xy 0.5 0.75))
          (width 0) (fill yes))))
  )
)""",
     [("JP1", "1", 147.357, 97.79, 0.0)]),

    # From ecc83-pp: a terminal block rotated -90 with the pad angle spelled out.
    # This is the pair that produced the first phantom clearance violation.
    ("footprint rotated -90 deg with an explicit pad angle (ecc83-pp P3)",
     """(kicad_pcb (version 20221018)
  (net 0 "") (net 1 "GND")
  (footprint "T:TB" (layer "F.Cu") (at 128.27 100.711 -90)
    (property "Reference" "P3" (at 0 0 270) (layer "F.SilkS"))
    (pad "2" thru_hole circle (at 5 0 270) (size 3 3) (drill 1.5)
      (layers "*.Cu" "*.Mask") (net 1 "GND"))
  )
)""",
     [("P3", "2", 128.27, 105.711, 270.0)]),
]

# Demo boards small enough to load in a couple of seconds, used by the sweep.
SWEEP_BOARDS = ["ecc83/ecc83-pp", "interf_u/interf_u", "pic_programmer/pic_programmer"]
# The shape sweep looks at the pad OUTLINE, where a wrong stadium (the old oval was
# 1.2 mm wide for a 1.6 x 2.8 pad) or a dropped `(drill ... (offset ...))` still
# leaves the pad centre in the right place. Four boards, ~8k probes in total.
SHAPE_BOARDS = ["pic_programmer/pic_programmer", "complex_hierarchy/complex_hierarchy",
                "ecc83/ecc83-pp", "interf_u/interf_u"]


class TestPadPlacementMatchesKiCad(unittest.TestCase):
    def test_the_pinned_cases(self):
        for label, text, want in PAD_TRUTH:
            board = kicad_pcb.parse_board_text(text, path="<memory>")
            pads = {}
            for fp in board.footprints:
                for pad in fp["pads"]:
                    pads[(fp["ref"], pad["number"])] = pad
            for ref, num, x, y, angle in want:
                with self.subTest(label=label, pad="%s.%s" % (ref, num)):
                    pad = pads[(ref, num)]
                    self.assertAlmostEqual(pad["x"], x, places=3,
                                           msg="%s: x of %s.%s" % (label, ref, num))
                    self.assertAlmostEqual(pad["y"], -y, places=3,
                                           msg="%s: canonical y is -pcbnew y" % label)
                    self.assertAlmostEqual((pad["rotation"] or 0.0) % 360.0,
                                           angle % 360.0, places=3,
                                           msg="%s: pad angle is absolute" % label)

    def test_a_custom_pad_keeps_its_anchor(self):
        """The outline is relative to the anchor; the anchor is what pcbnew reports."""
        board = kicad_pcb.parse_board_text(PAD_TRUTH[2][1], path="<memory>")
        pad = board.footprints[0]["pads"][0]
        self.assertAlmostEqual(pad["x"], 147.357, places=3)
        from pcb_agent_mcp.adapters.canonical import pad_points
        pts = pad_points(pad)
        xs = [p[0] for p in pts]
        # the drawn outline still spans the real shape: local x in [-0.5, 1]
        self.assertAlmostEqual(min(xs), 147.357 - 0.5, places=3)
        self.assertAlmostEqual(max(xs), 147.357 + 1.0, places=3)


def _kicad_python():
    cli = engine.find_kicad_cli()
    if not cli:
        return None
    cand = os.path.join(os.path.dirname(cli), "python.exe")
    if os.path.isfile(cand):
        return cand
    return None


def _demos():
    cli = engine.find_kicad_cli()
    if not cli:
        return None
    # <prefix>/bin/kicad-cli.exe -> <prefix>/share/kicad/demos
    root = os.path.dirname(os.path.dirname(cli))
    d = os.path.join(root, "share", "kicad", "demos")
    return d if os.path.isdir(d) else None


_DUMP = r"""
import json, sys, pcbnew
b = pcbnew.LoadBoard(sys.argv[1])
out = []
for fp in b.GetFootprints():
    for p in fp.Pads():
        pos = p.GetPosition()
        out.append({"ref": fp.GetReference(), "num": p.GetNumber(),
                    "x": pos.x / 1e6, "y": pos.y / 1e6,
                    "angle": p.GetOrientation().AsDegrees()})
json.dump(out, sys.stdout)
"""


class TestSweepAgainstPcbnew(unittest.TestCase):
    """Every pad of some real demo boards, against the KiCad installed here."""

    def test_all_pads_of_the_demo_boards(self):
        py, demos = _kicad_python(), _demos()
        if not py or not demos:
            self.skipTest("KiCad's own Python (pcbnew) is not installed here")
        env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
        checked = 0
        for name in SWEEP_BOARDS:
            path = os.path.join(demos, name + ".kicad_pcb")
            if not os.path.isfile(path):
                continue
            proc = subprocess.run([py, "-E", "-s", "-c", _DUMP, path],
                                  capture_output=True, text=True, timeout=180, env=env)
            if proc.returncode != 0 or not (proc.stdout or "").strip():
                self.skipTest("pcbnew could not read %s: %s" % (name, proc.stderr[-300:]))
            truth = json.loads(proc.stdout)
            board = kicad_pcb.parse_board(path)
            groups = {}
            for fp in board.footprints:
                for pad in fp["pads"]:
                    groups.setdefault((fp["ref"], pad["number"] or ""), []).append(pad)
            worst = 0.0
            for t in truth:
                cand = groups.get((t["ref"], t["num"] or "")) or []
                self.assertTrue(cand, "%s: pad %s.%s missing" % (name, t["ref"], t["num"]))
                tx, ty = t["x"], -t["y"]
                best = min(cand, key=lambda p: (p["x"] - tx) ** 2 + (p["y"] - ty) ** 2)
                cand.remove(best)
                d = ((best["x"] - tx) ** 2 + (best["y"] - ty) ** 2) ** 0.5
                worst = max(worst, d)
                checked += 1
                self.assertLess(d, 0.002,
                                "%s: pad %s.%s is %.3f mm from pcbnew's position"
                                % (name, t["ref"], t["num"], d))
                ang = ((best["rotation"] or 0.0) - t["angle"]) % 180.0
                self.assertLess(min(ang, 180.0 - ang), 0.01,
                                "%s: pad %s.%s angle is %.2f deg off"
                                % (name, t["ref"], t["num"], ang))
        self.assertGreater(checked, 500, "the sweep compared suspiciously few pads")


_DUMP_SHAPE = r"""
import json, sys, pcbnew
b = pcbnew.LoadBoard(sys.argv[1])
out = []
for fp in b.GetFootprints():
    for p in fp.Pads():
        pos = p.GetPosition()
        box = p.GetBoundingBox()
        x0, y0 = box.GetX() / 1e6, box.GetY() / 1e6
        W, H = box.GetWidth() / 1e6, box.GetHeight() / 1e6
        pad = 0.4 * max(W, H)          # probe outside the pad as well as inside
        probes = []
        n = 9
        for i in range(n):
            for j in range(n):
                px = x0 - pad + (W + 2 * pad) * (i + 0.5) / n
                py = y0 - pad + (H + 2 * pad) * (j + 0.5) / n
                hit = p.HitTest(pcbnew.VECTOR2I(int(round(px * 1e6)),
                                                int(round(py * 1e6))))
                probes.append([px, py, bool(hit)])
        out.append({"ref": fp.GetReference(), "num": p.GetNumber(),
                    "x": pos.x / 1e6, "y": pos.y / 1e6, "probes": probes})
json.dump(out, sys.stdout)
"""


def _point_in_polygon(x, y, pts):
    inside = False
    n = len(pts)
    j = n - 1
    for i in range(n):
        xi, yi = pts[i]
        xj, yj = pts[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


class TestPadShapeAgainstPcbnew(unittest.TestCase):
    """Our pad outline must agree with pcbnew's own HitTest, point by point."""

    def test_pad_outlines_match_hittest(self):
        py, demos = _kicad_python(), _demos()
        if not py or not demos:
            self.skipTest("KiCad's own Python (pcbnew) is not installed here")
        env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
        from pcb_agent_mcp.adapters.canonical import pad_points
        checked = pads = 0
        for name in SHAPE_BOARDS:
            path = os.path.join(demos, name + ".kicad_pcb")
            if not os.path.isfile(path):
                continue
            proc = subprocess.run([py, "-E", "-s", "-c", _DUMP_SHAPE, path],
                                  capture_output=True, text=True, timeout=300, env=env)
            if proc.returncode != 0 or not (proc.stdout or "").strip():
                self.skipTest("pcbnew could not read %s: %s" % (name, proc.stderr[-300:]))
            truth = json.loads(proc.stdout)
            board = kicad_pcb.parse_board(path)
            groups = {}
            for fp in board.footprints:
                for pad in fp["pads"]:
                    groups.setdefault((fp["ref"], pad["number"] or ""), []).append(pad)
            for t in truth:
                cand = groups.get((t["ref"], t["num"] or "")) or []
                self.assertTrue(cand, "%s: pad %s.%s missing" % (name, t["ref"], t["num"]))
                tx, ty = t["x"], -t["y"]
                best = min(cand, key=lambda p: (p["x"] - tx) ** 2 + (p["y"] - ty) ** 2)
                cand.remove(best)
                poly = pad_points(best)
                pads += 1
                for (px, pyy, hit) in t["probes"]:
                    got = _point_in_polygon(px, -pyy, poly)
                    checked += 1
                    if got != hit:
                        self.fail("%s: pad %s.%s (%s %.2fx%.2f rot %.1f) disagrees with "
                                  "pcbnew HitTest at (%.3f, %.3f) file frame"
                                  % (name, t["ref"], t["num"], best.get("shape"),
                                     best.get("w") or 0.0, best.get("h") or 0.0,
                                     best.get("rotation") or 0.0, px, pyy))
        self.assertGreater(checked, 5000, "the sweep compared suspiciously few probes")
        self.assertGreater(pads, 300, "the sweep compared suspiciously few pads")



class TestACustomPadIsItsWholeOutline(unittest.TestCase):
    """A custom pad is the UNION of its primitives, so it is measured as one.

    Taking the first `gr_poly` measured a pad smaller than the copper on the board
    - an optimistic error, the one direction a clearance check must never make.
    The convex hull of every primitive is never smaller than their union, the
    field `outline` says that is what happened, and a kind this build cannot
    express is NAMED in `ignored_primitives` instead of vanishing.

    ONE primitive is the exception and must stay one: a primitive can be concave
    (pic_programmer JP1.2 is a notched rectangle), and hulling it would fill the
    notch with copper that is not on the board.
    """

    TEXT = """(kicad_pcb (version 20221018)
  (net 0 "") (net 1 "GND")
  (footprint "T:Custom" (layer "F.Cu") (at 100 100)
    (property "Reference" "U9" (at 0 0) (layer "F.SilkS"))
    (pad "1" smd custom (at 0 0)
      (size 0.3 0.3) (layers "F.Cu") (net 1 "GND")
      (options (clearance outline) (anchor rect))
      (primitives
        (gr_rect (start -3 -1) (end -1 1) (width 0) (fill yes))
        (gr_rect (start 1 -1) (end 3 1) (width 0) (fill yes))
        (gr_curve (pts (xy 0 0) (xy 1 1) (xy 2 0)))))
  )
)"""

    def test_the_hull_spans_every_primitive_and_the_unknown_kind_is_named(self):
        board = kicad_pcb.parse_board_text(self.TEXT, path="<memory>")
        pad = board.footprints[0]["pads"][0]
        self.assertEqual(pad["shape"], "custom")
        self.assertEqual(pad["primitives"], 2)
        self.assertEqual(pad["outline"], "convex_hull_of_primitives")
        self.assertEqual(pad["ignored_primitives"], ["gr_curve"])
        # Both rectangles are inside the measured copper: the span is 6 mm, not the
        # 2 mm one of them alone would have given.
        self.assertAlmostEqual(pad["w"], 6.0, places=6)
        self.assertAlmostEqual(pad["h"], 2.0, places=6)

    NOTCHED = """(kicad_pcb (version 20221018)
  (net 0 "") (net 1 "GND")
  (footprint "T:Notched" (layer "F.Cu") (at 100 100)
    (property "Reference" "JP1" (at 0 0) (layer "F.SilkS"))
    (pad "2" smd custom (at 0.725 0)
      (size 0.3 0.3) (layers "F.Cu") (net 1 "GND")
      (options (clearance outline) (anchor rect))
      (primitives
        (gr_poly
          (pts (xy 0.5 -0.75) (xy -0.65 -0.75) (xy -0.15 0)
               (xy -0.65 0.75) (xy 0.5 0.75))
          (width 0) (fill yes)))))
)"""

    def test_a_notched_primitive_keeps_its_notch(self):
        """The notch is where the copper is NOT, so it must survive parsing."""
        board = kicad_pcb.parse_board_text(self.NOTCHED, path="<memory>")
        pad = board.footprints[0]["pads"][0]
        self.assertEqual(pad["primitives"], 1)
        self.assertEqual(pad["outline"], "primitive")
        self.assertEqual(len(pad["polygon"]), 5)      # a rectangle would be 4
        # The notch tip sits at (-0.15, 0) relative to the anchor: inside the
        # bounding box, outside the copper.
        self.assertFalse(_point_in_polygon(100.0 + 0.725 - 0.5, -100.0,
                                          [(100.0 + 0.725 + px, -100.0 + py)
                                           for px, py in pad["polygon"]]))
        # ...while both arms of the shape still reach the full height.
        self.assertAlmostEqual(pad["h"], 1.5, places=6)
        self.assertAlmostEqual(pad["w"], 1.15, places=6)

    def test_the_anchor_is_still_the_anchor(self):
        """The outline is relative to the pad's `at`; that is what pcbnew reports."""
        board = kicad_pcb.parse_board_text(self.TEXT, path="<memory>")
        pad = board.footprints[0]["pads"][0]
        self.assertAlmostEqual(pad["x"], 100.0, places=6)
        self.assertAlmostEqual(pad["y"], -100.0, places=6)   # canonical y is mirrored
        from pcb_agent_mcp.adapters.canonical import pad_points
        xs = [p[0] for p in pad_points(pad)]
        self.assertAlmostEqual(min(xs), 97.0, places=6)
        self.assertAlmostEqual(max(xs), 103.0, places=6)

if __name__ == "__main__":
    unittest.main()
