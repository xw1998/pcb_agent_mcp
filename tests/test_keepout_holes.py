"""A rule area can leave copper alone in a hole inside itself.

Measured on the reference board: an LCEDA REGION whose `prohibitType` lists only
`COPPER` bans POUR but not hand-drawn FILL, and the source puts two GND fills
inside that region. We express a FILL as a zone, so a plain `copperpour` rule
area clipped those fills to zero and 25 GND vias lost their copper. A KiCad zone
carries its holes as extra `(polygon ...)` nodes, so `add_keepout(holes=...)`
can carve them back out while the rest of the area keeps banning pour.

The reverse control is the same board without `holes`: the copper of the pour
must then be missing by the hole's area, i.e. the carve-out is what brings it
back and not something else in the fixture.
"""
import os
import shutil
import tempfile
import unittest

from pcb_agent_mcp import edit as E
from pcb_agent_mcp.fidelity import fill as FILL
from pcb_agent_mcp.fidelity import kicad_doc as KD

NEED_FILL = unittest.skipUnless(
    FILL.find_kicad_python() is not None,
    "no Python with the pcbnew module was found; the fill cannot run")

POUR = (0, 20, 20, 0)          # x0, y1, x1, y0 in Y-up millimetres
AREA = [(4, 16), (16, 16), (16, 4), (4, 4)]
HOLE = [(7, 13), (13, 13), (13, 7), (7, 7)]


class _Base(unittest.TestCase):
    holes = None

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pcbhole-")
        self._home = os.environ.get("PCB_AGENT_HOME")
        os.environ["PCB_AGENT_HOME"] = os.path.join(self.tmp, "home")
        self.board = os.path.join(self.tmp, "p.kicad_pcb")
        E.create_board(self.board, {"width_mm": 24, "height_mm": 24,
                                    "copper_layers": 2, "engine": False})
        ops = [{"op": "add_net", "name": "GND"},
               {"op": "add_zone", "net": "GND", "layer": "F.Cu",
                "x0": 1, "y0": 1, "x1": 19, "y1": 19}]
        keep = {"op": "add_keepout", "layer": "F.Cu", "points": AREA,
                "rules": ["copperpour"]}
        if self.holes is not None:
            keep["holes"] = self.holes
        ops.append(keep)
        plan = E.propose(self.board, ops)
        res = E.apply_plan(plan["plan_id"], confirm=True,
                           options={"engine": False})
        self.assertEqual(res["status"], "ok", res)

    def tearDown(self):
        if self._home is None:
            os.environ.pop("PCB_AGENT_HOME", None)
        else:
            os.environ["PCB_AGENT_HOME"] = self._home
        from pcb_agent_mcp.edit import plan as P
        P.PLANS.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _pour(self):
        out = FILL.fill(self.board, in_place=True)
        self.assertEqual(out["status"], "ok", out)
        rows = [r for r in FILL._zone_rows(
            KD.KiCadDocument.from_path(self.board).board()) if not r["keepout"]]
        self.assertEqual(len(rows), 1, rows)
        return rows[0]


class TestKeepoutWithoutHoles(_Base):
    """Reverse control: no holes => the pour is cut by the whole rule area."""
    holes = None

    @NEED_FILL
    def test_pour_is_missing_the_rule_area(self):
        row = self._pour()
        # 18x18 pour minus the 12x12 rule area = 324 - 144 = 180 mm2, i.e. the
        # hole area (36 mm2) is NOT there. Same fixture as the holes case.
        self.assertLess(row["fill_area_mm2"], 200)
        self.assertGreater(row["fill_area_mm2"], 180 * 0.9)


class TestKeepoutWithHoles(_Base):
    """The carve-out gives back exactly the hole, and only the hole."""
    holes = [HOLE]

    @NEED_FILL
    def test_hole_restores_only_its_own_copper(self):
        row = self._pour()
        # 324 mm2 pour minus the 144 mm2 rule area plus the 36 mm2 hole.
        self.assertGreater(row["fill_area_mm2"], 200)
        self.assertLess(row["fill_area_mm2"], 230)
        # The carve-out has to be where we asked for it, not somewhere else: one
        # ring of the pour must have exactly the hole's bounding box.
        boxes = []
        for poly in row["_polys"]:
            xs = [p[0] for p in poly["points"]]
            ys = [p[1] for p in poly["points"]]
            boxes.append((round(min(xs), 2), round(min(ys), 2),
                          round(max(xs), 2), round(max(ys), 2)))
        self.assertIn((7.0, -13.0, 13.0, -7.0), boxes, boxes)


class TestHolesAreWritten(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pcbhole2-")
        self.board = os.path.join(self.tmp, "p.kicad_pcb")
        E.create_board(self.board, {"width_mm": 24, "height_mm": 24,
                                    "copper_layers": 2, "engine": False})

    def tearDown(self):
        from pcb_agent_mcp.edit import plan as P
        P.PLANS.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_hole_ring_must_be_a_polygon(self):
        with self.assertRaises(E.OpError) as ctx:
            E.propose(self.board, [{"op": "add_keepout", "layer": "F.Cu",
                                    "points": AREA, "rules": ["copperpour"],
                                    "holes": [[[0, 0], [1, 0]]]}])
        self.assertEqual(ctx.exception.reason_code, "ARG_INVALID")

    def test_each_ring_becomes_one_more_polygon(self):
        plan = E.propose(self.board, [{"op": "add_keepout", "layer": "F.Cu",
                                       "points": AREA, "rules": ["copperpour"],
                                       "holes": [HOLE, HOLE]}])
        res = E.apply_plan(plan["plan_id"], confirm=True,
                           options={"engine": False})
        self.assertEqual(res["status"], "ok", res)
        text = open(self.board, encoding="utf-8").read()
        zone = text[text.index("(zone "):text.index("(zone ") + 4000]
        self.assertEqual(zone.count("(polygon"), 3, zone[:400])
        self.assertEqual(zone.count("(keepout"), 1)
