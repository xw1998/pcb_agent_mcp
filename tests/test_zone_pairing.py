"""Two pours of the SAME net on the SAME layer must not swap their copper.

Measured on the reference board: a 3146 mm2 whole-board GND pour and a 5.25 mm2
GND keep-area pour are both (GND, F.Cu). Pairing the engine's zones back to the
file by (net, layer) alone is therefore a greedy match that lands every
`filled_polygon` in the other zone -- the zone count still matches, every
polygon is valid, and the copper is simply in the wrong place (unconnected went
20 -> 499). The pairing key includes the zone OUTLINE now; this file pins it,
and the reverse control (the old key) is asserted to FAIL.
"""
import os
import shutil
import tempfile
import unittest

from pcb_agent_mcp import edit as E
from pcb_agent_mcp.adapters import kicad_pcb as A
from pcb_agent_mcp.edit import plan as P
from pcb_agent_mcp.fidelity import fill as FILL
from pcb_agent_mcp.fidelity import kicad_doc as KD

NEED_FILL = unittest.skipUnless(
    FILL.find_kicad_python() is not None,
    "no Python with the pcbnew module was found; the fill cannot run")


def _inside(points, box):
    x0, y0, x1, y1 = box
    return all(x0 - 0.01 <= x <= x1 + 0.01 and y0 - 0.01 <= y <= y1 + 0.01
               for x, y in points)


class TestTwoPoursOneNet(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pcbpair-")
        self._home = os.environ.get("PCB_AGENT_HOME")
        os.environ["PCB_AGENT_HOME"] = os.path.join(self.tmp, "home")
        self.board = os.path.join(self.tmp, "p.kicad_pcb")
        E.create_board(self.board, {"width_mm": 40, "height_mm": 30,
                                    "copper_layers": 2, "engine": False})
        names = set(A.parse_board(self.board).nets.values())
        ops = []
        if "GND" not in names:
            ops.append({"op": "add_net", "name": "GND"})
        # Two disjoint GND pours on the same layer: 18x26 on the left,
        # 14x26 on the right. Their outlines are different, so the fingerprint
        # tells them apart while a bare (net, layer) key cannot.
        ops.append({"op": "add_zone", "net": "GND", "layer": "F.Cu",
                    "x0": 2, "y0": 2, "x1": 20, "y1": 28})
        ops.append({"op": "add_zone", "net": "GND", "layer": "F.Cu",
                    "x0": 24, "y0": 2, "x1": 38, "y1": 28})
        plan = E.propose(self.board, ops)
        res = E.apply_plan(plan["plan_id"], confirm=True,
                           options={"engine": False})
        self.assertEqual(res["status"], "ok", res)

    def tearDown(self):
        if self._home is None:
            os.environ.pop("PCB_AGENT_HOME", None)
        else:
            os.environ["PCB_AGENT_HOME"] = self._home
        P.PLANS.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    LEFT = (2, -28, 20, -2)
    RIGHT = (24, -28, 38, -2)

    @NEED_FILL
    def test_each_zone_keeps_its_own_copper(self):
        out = FILL.fill(self.board, in_place=True)
        self.assertEqual(out["status"], "ok", out)
        rows = [r for r in FILL._zone_rows(
            KD.KiCadDocument.from_path(self.board).board()) if not r["keepout"]]
        self.assertEqual(len(rows), 2, rows)
        # The copper must sit inside its OWN outline, not merely be the right
        # size somewhere. A greedy (net, layer) pairing moves the 468 mm2 pour
        # into the 364 mm2 zone and this check fails.
        boxes = []
        for row in rows:
            box = self.LEFT if row["outline_area_mm2"] > 400 else self.RIGHT
            boxes.append(box)
            self.assertTrue(row["outline"], "the outline must be readable")
            self.assertTrue(row["_polys"], "zone %r got no copper" % (box,))
            self.assertGreater(row["fill_area_mm2"], row["outline_area_mm2"] * 0.9)
            for poly in row["_polys"]:
                self.assertTrue(_inside(poly["points"], box),
                                "copper of %r landed outside its outline" % (box,))
        self.assertEqual(sorted(boxes), [self.LEFT, self.RIGHT])
        # Reverse control: the old key collapses the two zones into one bucket,
        # i.e. the pairing had nothing to tell them apart with.
        self.assertEqual(len(set((r["net"], r["layer"]) for r in rows)), 1)
        self.assertEqual(len(set(r["outline_key"] for r in rows)), 2)

    def test_the_outline_fingerprint_ignores_start_vertex_and_direction(self):
        square = [(0, 0), (10, 0), (10, 5), (0, 5)]
        rotated = [(10, 5), (0, 5), (0, 0), (10, 0)]
        reversed_ = list(reversed(square))
        other = [(0, 0), (10, 0), (10, 6), (0, 5)]
        key = FILL._outline_key
        self.assertEqual(key(square), key(rotated))
        self.assertEqual(key(square), key(reversed_))
        self.assertNotEqual(key(square), key(other))
        self.assertEqual(key(None), None)
