"""Closing the pour loop: the zone is only copper AFTER the engine poured it.

`add_zone` writes an outline plus `(fill yes)`. Everything measured from that file
before a fill - connectivity, `zone_fill` presence, Gerber copper, the poured area
- is measured from an OUTLINE. This test file pins the closure:

* reading says "outline" until filled (`board_zones`);
* the fill is KiCad's own `ZONE_FILLER`, measured on a copy by default, so
  nothing is written when the caller only asked a question;
* `in_place=true` transplants KiCad's polygons and keeps the file's own version
  header - pcbnew's SaveBoard would re-serialise the whole board into KiCad-10
  layout and bump `(version 20221018)`, which silently rewrites a user's file;
* a keepout inside the pour really takes copper away (that is the engine's job,
  and it is measured, not assumed);
* `board_apply(fill_zones=true)` closes the loop inside the write path, and
  failing to fill is a failure rather than a footnote.
"""
import os
import shutil
import tempfile
import unittest

from pcb_agent_mcp import edit as E
from pcb_agent_mcp import tools as TOOLS
from pcb_agent_mcp.adapters import kicad_pcb as A
from pcb_agent_mcp.edit import plan as P
from pcb_agent_mcp.fidelity import fill as FILL

HAVE_FILL = FILL.find_kicad_python() is not None
NEED_FILL = unittest.skipUnless(
    HAVE_FILL, "no Python with the pcbnew module was found; the fill cannot run")


class ZoneBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pcbzone-")
        self._home = os.environ.get("PCB_AGENT_HOME")
        os.environ["PCB_AGENT_HOME"] = os.path.join(self.tmp, "home")
        self.board = os.path.join(self.tmp, "z.kicad_pcb")
        E.create_board(self.board, {"width_mm": 40, "height_mm": 30,
                                    "copper_layers": 2, "engine": False})

    def tearDown(self):
        if self._home is None:
            os.environ.pop("PCB_AGENT_HOME", None)
        else:
            os.environ["PCB_AGENT_HOME"] = self._home
        P.PLANS.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def add(self, ops, **options):
        names = set(A.parse_board(self.board).nets.values())
        ops = ops if "GND" in names else [{"op": "add_net", "name": "GND"}] + ops
        plan = E.propose(self.board, ops)
        return E.apply_plan(plan["plan_id"], confirm=True,
                            options=dict({"engine": False}, **options))

    def pour(self, **extra):
        op = {"op": "add_zone", "net": "GND", "layer": "F.Cu",
              "x0": 2, "y0": 2, "x1": 38, "y1": 28}
        op.update(extra)
        return self.add([op])

    def bytes(self):
        with open(self.board, "rb") as fh:
            return fh.read()


class TestReadingTheZones(ZoneBase):
    def test_an_unfilled_zone_is_an_outline_not_copper(self):
        self.pour()
        out = FILL.zones(self.board)
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["value"]["zones"], 1)
        self.assertEqual(out["value"]["poured_zones"], 0)
        self.assertEqual(out["value"]["pour_area_mm2"], 0)
        row = out["value"]["per_zone"][0]
        self.assertEqual(row["net"], "GND")
        self.assertEqual(row["layer"], "F.Cu")
        self.assertEqual(row["fill_flag"], "yes")
        self.assertAlmostEqual(row["outline_area_mm2"], 36 * 26, places=1)

    def test_a_keepout_is_not_counted_as_a_pour(self):
        self.add([{"op": "add_keepout", "layer": "F.Cu", "x0": 10, "y0": 10,
                   "x1": 20, "y1": 20, "tracks": True, "vias": True, "pads": True,
                   "copperpour": True}])
        out = FILL.zones(self.board)
        self.assertEqual(out["value"]["zones"], 1)
        self.assertEqual(out["value"]["poured_zones"], 0)
        self.assertTrue(out["value"]["per_zone"][0]["keepout"])
        self.assertEqual(out["assumptions"]["unfillable"], ["F.Cu"])

    def test_board_without_zones_refuses_to_fill(self):
        out = FILL.fill(self.board)
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["reason_code"], "NO_GEOMETRY")


@NEED_FILL
class TestFillingOnACopy(ZoneBase):
    def test_measuring_does_not_touch_the_file(self):
        self.pour()
        before = self.bytes()
        out = FILL.fill(self.board)
        self.assertEqual(out["status"], "ok", out)
        self.assertFalse(out["value"]["written_to_file"])
        self.assertEqual(self.bytes(), before, "a measurement must not write")
        self.assertEqual(out["value"]["poured_zones"], 1)
        self.assertGreater(out["value"]["fill_area_mm2"], 36 * 26 * 0.95)

    def test_the_reply_says_what_the_engine_decided_and_what_it_did_not(self):
        self.pour()
        out = FILL.fill(self.board)
        self.assertIn("ZONE_FILLER", out["model"])
        self.assertTrue(out["assumptions"]["fill_is_a_snapshot"])
        self.assertIn("refill_needed_after", out["assumptions"])
        self.assertIn("KiCad's own pcbnew", out["basis"])
        self.assertIn("thermal spokes", out["precision"])


@NEED_FILL
class TestFillingInPlace(ZoneBase):
    def test_polygons_are_transplanted_and_the_format_is_not_upgraded(self):
        self.pour()
        out = FILL.fill(self.board, in_place=True)
        self.assertEqual(out["status"], "ok", out)
        self.assertTrue(out["value"]["written_to_file"])
        self.assertTrue(out["value"]["checkpoint_id"])
        raw = self.bytes()
        # pcbnew's own SaveBoard would have written KiCad 10 layout and bumped the
        # version. Both would be an unasked-for rewrite of the user's file.
        self.assertIn(b"(version 20221018)", raw)
        self.assertIn(b"(generator pcb_agent_mcp)", raw)
        self.assertIn(b"filled_polygon", raw)
        board = A.parse_board(self.board)
        self.assertEqual(board.present.get("zone_fill", {}).get("present"), True)

    def test_connectivity_is_measured_on_real_copper_afterwards(self):
        self.pour()
        before = FILL.zones(self.board)["value"]["poured_zones"]
        self.assertEqual(before, 0)
        FILL.fill(self.board, in_place=True)
        after = FILL.zones(self.board)["value"]
        self.assertEqual(after["poured_zones"], 1)
        self.assertGreater(after["pour_area_mm2"], 0)

    def test_the_checkpoint_can_restore_the_unfilled_file(self):
        self.pour()
        before = self.bytes()
        out = FILL.fill(self.board, in_place=True)
        cid = out["value"]["checkpoint_id"]
        E.restore_checkpoint(cid, confirm=True)
        self.assertEqual(self.bytes(), before)
        self.assertEqual(FILL.zones(self.board)["value"]["poured_zones"], 0)

    def test_a_keepout_really_takes_the_copper_away(self):
        self.pour()
        full = FILL.fill(self.board)["value"]["fill_area_mm2"]
        # same pour, plus a 10x10 keepout in the middle of it
        self.add([{"op": "add_keepout", "layer": "F.Cu", "x0": 15, "y0": 10,
                   "x1": 25, "y1": 20, "copperpour": True, "tracks": True,
                   "vias": True, "pads": True}])
        carved = FILL.fill(self.board)["value"]["fill_area_mm2"]
        self.assertLess(carved, full - 60, (full, carved))
        self.assertGreater(carved, full - 140, (full, carved))


@NEED_FILL
class TestTheWritePathClosesTheLoop(ZoneBase):
    def test_apply_can_fill_and_says_so(self):
        self.pour()
        # the same edit, this time asking for the closure
        plan = E.propose(self.board, [{"op": "add_zone", "net": "GND",
                                       "layer": "B.Cu", "x0": 2, "y0": 2,
                                       "x1": 38, "y1": 28}])
        r = E.apply_plan(plan["plan_id"], confirm=True,
                         options={"engine": False, "fill_zones": True})
        gate = r["gates"]["zone_fill"]
        self.assertTrue(gate["ok"], gate)
        self.assertFalse(gate["drc_rechecked"])
        self.assertGreaterEqual(gate["poured_zones"], 1)
        poured = FILL.zones(self.board)["value"]["poured_zones"]
        self.assertEqual(poured, 2, "both zones should be copper now")

    def test_without_the_option_the_outline_stays_an_outline(self):
        r = self.pour()
        self.assertNotIn("zone_fill", r["gates"])
        detail = [o for o in r["gates"]["postconditions"]["operations"]
                  if o["op"] == "add_zone"][0]
        self.assertEqual(FILL.zones(self.board)["value"]["poured_zones"], 0)


class TestThroughTheToolSurface(ZoneBase):
    def test_both_tools_are_registered(self):
        self.assertIn("board_zones", TOOLS.TOOLS)
        self.assertIn("board_fill_zones", TOOLS.TOOLS)

    def test_board_zones_speaks_through_the_envelope(self):
        self.pour()
        out = TOOLS.call_tool("board_zones", {"path": self.board})
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["value"]["zones"], 1)
        self.assertIn("filled_polygon", out["basis"])

    @NEED_FILL
    def test_board_fill_zones_measures_by_default(self):
        self.pour()
        before = self.bytes()
        out = TOOLS.call_tool("board_fill_zones", {"path": self.board})
        self.assertEqual(out["status"], "ok", out)
        self.assertFalse(out["value"]["written_to_file"])
        self.assertEqual(self.bytes(), before)


if __name__ == "__main__":
    unittest.main()
