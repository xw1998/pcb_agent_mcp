"""board_net_gaps: one net's copper in how many pieces, and how far apart.

The question comes from the reference board: DRC says `unconnected: 115` and stops.
It counts ratsnest edges; it does not say that +3V3 alone is 38 of them, that they
fall into 39 pieces, or that the nearest two pieces of the rail are 0.05 mm apart at
a named pair of segments. This tool walks the net's own copper as geometry and
answers those, and it routes nothing.

What is pinned here, each with a control that can fail:

1. Two pads of one net are two pieces and the gap is the metal-to-metal distance.
   Control: one track between them and it is one piece, zero gaps - the walker
   follows copper, so a join it did not see would make the number meaningless.
2. A hairline gap is a piece: a track ending 0.15 mm short of its pad does NOT
   connect, and 0.01 mm of overlap does. This is the assertion that the touch
   tolerance is not hiding the small gaps that DRC reports.
3. Vias are what make a two-layer net one conductor. Remove the via and the B.Cu
   half hangs loose; that is a one-op change and the verdict turns.
4. A pad wholly inside a pour of its own net is one piece even though no boundary
   ever touches (the filled polygon simply covers it), and a pad of ANOTHER net
   inside that pour is not absorbed. Without the containment pass the first case
   would read as two pieces; without the net filter the second would read as one.
5. A pair farther apart than `gap_limit_mm` is counted, not measured, and the
   pieces that have no neighbour inside the limit are named as such.
6. With no `net` every net is counted - that is the cross-check against DRC.
7. The tool surface says "routes nothing", admits that pours only count when the
   zone stored a fill result, and writes nothing to the file.
"""
import hashlib
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pcb_agent_mcp import oracle
from pcb_agent_mcp import tools
from pcb_agent_mcp.edit import plan as P

from test_routability import RoutabilityBase

NET = "SIG"

class _Gaps(RoutabilityBase):
    def gaps(self, **kw):
        board, e = tools._load_board({"path": self.board})
        self.assertIsNone(e, e)
        out = oracle.net_gaps(board, **kw)
        self.assertEqual(out["status"], "ok", out)
        return out["value"]

    def raw(self, **kw):
        board, e = tools._load_board({"path": self.board})
        self.assertIsNone(e, e)
        return oracle.net_gaps(board, **kw)

    def restart(self):
        P.PLANS.clear()

    def pad(self, ref, x, y, net, size=1.0):
        return self.place("P" + ref, [("1", 0.0, 0.0, size, size)], ref=ref,
                          x=x, y=y, nets={"1": net})

    def track(self, x1, y1, x2, y2, net=NET, width=0.3, layer="F.Cu"):
        return {"op": "add_track", "x1": x1, "y1": y1, "x2": x2, "y2": y2,
                "width": width, "layer": layer, "net": net, "create_net": True}

    def via(self, x, y, net=NET):
        return {"op": "add_via", "x": x, "y": y, "drill": 0.3, "diameter": 0.5,
                "net": net, "layers": ["F.Cu", "B.Cu"]}

class TestTwoPadsAreTwoPieces(_Gaps):
    def test_the_gap_is_the_distance_between_the_metal(self):
        self.apply(self.pad("U1", 20.0, 20.0, NET),
                   self.pad("U2", 25.0, 20.0, NET))
        v = self.gaps(net=NET)
        self.assertEqual(v["piece_count"], 2, v["pieces_detail"])
        self.assertEqual(v["edges_needed"], 1)
        self.assertEqual(v["gaps_found"], 1)
        gap = v["gaps"][0]
        # 1x1 pads: copper ends at 20.5 and starts again at 24.5.
        self.assertAlmostEqual(gap["gap_mm"], 4.0, places=3)
        self.assertEqual(gap["on"], ["F.Cu"])
        self.assertEqual(sorted([gap["a"]["ref"], gap["b"]["ref"]]),
                         ["U1.1", "U2.1"])

    def test_one_track_makes_it_one_piece_and_no_gaps(self):
        # The control for the number above: same two pads, one op more.
        self.apply(self.pad("U1", 20.0, 20.0, NET),
                   self.pad("U2", 25.0, 20.0, NET),
                   self.track(20.0, 20.0, 25.0, 20.0))
        v = self.gaps(net=NET)
        self.assertEqual(v["piece_count"], 1)
        self.assertEqual(v["edges_needed"], 0)
        self.assertEqual(v["gaps_found"], 0)
        self.assertEqual(v["pieces_detail"][0]["pads"], 2)
        self.assertEqual(v["pieces_detail"][0]["tracks"], 1)

class TestAHairlineGapIsAPiece(_Gaps):
    def test_a_track_that_stops_short_does_not_connect(self):
        # Track centreline ends at x=24.2, half a width (0.15) of copper beyond it,
        # pad edge at 24.5: 0.15 mm of air. DRC calls this unconnected and so must
        # this walker, or every real gap would be swallowed by a tolerance.
        self.apply(self.pad("U1", 20.0, 20.0, NET),
                   self.pad("U2", 25.0, 20.0, NET),
                   self.track(20.0, 20.0, 24.2, 20.0))
        v = self.gaps(net=NET)
        self.assertEqual(v["piece_count"], 2, v["pieces_detail"])
        self.assertAlmostEqual(v["gaps"][0]["gap_mm"], 0.15, places=3)

    def test_the_other_tenth_of_a_millimetre_closes_it(self):
        # The control: centreline at 24.4 puts copper at 24.55, past the pad edge.
        self.apply(self.pad("U1", 20.0, 20.0, NET),
                   self.pad("U2", 25.0, 20.0, NET),
                   self.track(20.0, 20.0, 24.4, 20.0))
        self.assertEqual(self.gaps(net=NET)["piece_count"], 1)

class TestViasAreWhatJoinLayers(_Gaps):
    def test_a_two_layer_net_is_one_piece_through_its_vias(self):
        self.apply(self.pad("U1", 20.0, 20.0, NET),
                   self.pad("U2", 25.0, 20.0, NET),
                   self.track(20.0, 20.0, 22.0, 20.0),
                   self.via(22.0, 20.0),
                   self.track(22.0, 20.0, 24.0, 20.0, layer="B.Cu"),
                   self.via(24.0, 20.0),
                   self.track(24.0, 20.0, 25.0, 20.0))
        v = self.gaps(net=NET)
        self.assertEqual(v["piece_count"], 1, v["pieces_detail"])
        self.assertEqual(v["pieces_detail"][0]["vias"], 2)

    def test_without_the_second_via_the_bottom_track_hangs_loose(self):
        # Control: one op removed. The B.Cu track is still copper of the net, so it
        # is a piece - not invisible - and the gap names it.
        self.apply(self.pad("U1", 20.0, 20.0, NET),
                   self.pad("U2", 25.0, 20.0, NET),
                   self.track(20.0, 20.0, 22.0, 20.0),
                   self.via(22.0, 20.0),
                   self.track(22.0, 20.0, 24.0, 20.0, layer="B.Cu"),
                   self.track(24.0, 20.0, 25.0, 20.0))
        v = self.gaps(net=NET)
        self.assertEqual(v["piece_count"], 2, v["pieces_detail"])
        # The B.Cu track is still copper of the net, so it is a piece - the walk
        # follows geometry, not intent. It hangs on the via at one end and on
        # nothing at the other, which is the shape DRC calls unconnected.
        # 4 items on the left (pad, F.Cu track, via, B.Cu track) and 2 on the right
        self.assertEqual(sorted(p["items"] for p in v["pieces_detail"]), [2, 4])
        loose = [p for p in v["pieces_detail"] if p["vias"] == 1][0]
        self.assertEqual(loose["tracks"], 2)

class TestWhatIsInsideAPour(_Gaps):
    def pour(self, x0, y0, x1, y1, net="GND", layer="F.Cu", exists=True):
        rect = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
        ops = [] if exists else [{"op": "add_net", "name": net}]
        ops.append({"op": "add_zone", "net": net, "layer": layer,
                    "x0": x0, "y0": y0, "x1": x1, "y1": y1})
        return ops, rect

    def filled(self, rect, net="GND", layer="F.Cu"):
        """Write the fill result straight onto the loaded board.

        `add_zone` stores the outline; KiCad writes the fill only after a fill run.
        The fill is the copper that matters here, so it is injected rather than
        refilled - and that also pins the documented limit: a zone whose fill was
        never computed contributes no copper at all.
        """
        board, e = tools._load_board({"path": self.board})
        self.assertIsNone(e, e)
        for z in board.zones:
            if z.get("net") == net and layer in (z.get("layers") or []):
                z["filled_polygons"] = [list(rect)]
        return board

    def test_a_pad_wholly_inside_its_own_pour_is_the_same_conductor(self):
        ops, rect = self.pour(15.0, 15.0, 30.0, 25.0)
        self.apply(self.pad("U1", 20.0, 20.0, "GND"), *ops)
        board = self.filled(rect)
        v = oracle.net_gaps(board, net="GND")["value"]
        self.assertEqual(v["piece_count"], 1, v["pieces_detail"])
        self.assertEqual(v["pieces_detail"][0]["pour_pieces"], 1)
        self.assertEqual(v["pieces_detail"][0]["pads"], 1)

    def test_a_pad_of_another_net_in_the_same_pour_is_not_absorbed(self):
        # Control: identical geometry, the pad's net renamed. If the net filter
        # leaked, SIG's pad would vanish into GND's pour and the total would be 1.
        ops, rect = self.pour(15.0, 15.0, 30.0, 25.0, exists=False)
        self.apply(self.pad("U1", 20.0, 20.0, NET), *ops)
        board = self.filled(rect)
        v = oracle.net_gaps(board)["value"]
        per = dict((s["net"], s["pieces"]) for s in v["nets"])
        # Both nets are one piece each, so nothing is unconnected - and the SIG pad
        # is still its own net's piece, which is the point: a leak in the net filter
        # would have merged it into the pour and dropped NET from this table.
        self.assertEqual(per["GND"], 1)
        self.assertEqual(per[NET], 1)
        self.assertEqual(v["edges_needed_total"], 0)

    def test_pours_connect_false_answers_the_same_question_without_them(self):
        ops, rect = self.pour(15.0, 15.0, 30.0, 25.0)
        self.apply(self.pad("U1", 20.0, 20.0, "GND"), *ops)
        board = self.filled(rect)
        v = oracle.net_gaps(board, net="GND", pours_connect=False)["value"]
        self.assertEqual(v["pieces_detail"][0]["pour_pieces"], 0)
        self.assertEqual(v["pours_counted_as_copper"], 0)

class TestTheGapLimit(_Gaps):
    def test_a_pair_beyond_the_limit_is_counted_not_measured(self):
        self.apply(self.pad("U1", 20.0, 20.0, NET),
                   self.pad("U2", 40.0, 20.0, NET))
        v = self.gaps(net=NET, gap_limit_mm=1.0)
        self.assertEqual(v["piece_count"], 2)
        self.assertEqual(v["gaps_found"], 0)
        self.assertEqual(v["pairs_beyond_limit"], 1)
        self.assertEqual(v["pieces_with_no_neighbour_within_limit"], [0, 1])

    def test_the_same_board_at_a_wider_limit_measures_it(self):
        self.apply(self.pad("U1", 20.0, 20.0, NET),
                   self.pad("U2", 40.0, 20.0, NET))
        v = self.gaps(net=NET, gap_limit_mm=25.0)
        self.assertEqual(v["gaps_found"], 1)
        self.assertAlmostEqual(v["gaps"][0]["gap_mm"], 19.0, places=3)

class TestWhatCannotBeAsked(_Gaps):
    def test_a_net_that_is_not_there_says_so(self):
        self.apply(self.pad("U1", 20.0, 20.0, NET))
        out = self.raw(net="NOPE")
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["reason_code"], "NO_GEOMETRY")

    def test_a_cell_or_a_limit_of_zero_is_refused(self):
        self.apply(self.pad("U1", 20.0, 20.0, NET))
        self.assertEqual(self.raw(net=NET, cell_mm=0)["reason_code"], "ARG_RANGE")
        self.assertEqual(self.raw(net=NET, gap_limit_mm=0)["reason_code"], "ARG_RANGE")

class TestEveryNetAtOnce(_Gaps):
    def test_no_net_counts_them_all_and_sorts_by_how_broken(self):
        self.apply(self.pad("U1", 20.0, 20.0, NET),
                   self.pad("U2", 25.0, 20.0, NET),
                   self.pad("U3", 20.0, 30.0, "OTHER"),
                   self.pad("U4", 22.0, 30.0, "OTHER"),
                   self.pad("U5", 24.0, 30.0, "OTHER"))
        v = self.gaps()
        per = dict((s["net"], s["edges_needed"]) for s in v["nets"])
        self.assertEqual(per[NET], 1)
        self.assertEqual(per["OTHER"], 2)
        self.assertEqual(v["edges_needed_total"], 3)
        # worst first
        self.assertEqual(v["nets"][0]["net"], "OTHER")

class TestToolSurface(unittest.TestCase):
    def test_the_schema_and_the_description(self):
        entry = [t for t in tools.list_tools() if t["name"] == "board_net_gaps"][0]
        props = entry["inputSchema"]["properties"]
        for key in ("net", "pours_connect", "gap_limit_mm", "limit", "cell_mm",
                    "path"):
            self.assertIn(key, props)
        self.assertEqual(entry["inputSchema"]["required"], ["path"])
        text = entry["description"]
        self.assertIn("routes nothing", text)
        self.assertIn("pours", text)

    def test_it_writes_nothing(self):
        tmp = os.path.join(os.path.dirname(os.path.abspath(__file__)))
        board = os.path.join(tmp, "..", "examples", "sky_star", "sky_star_routed.kicad_pcb")
        if not os.path.exists(board):
            self.skipTest("reference board not in this checkout")
        with open(board, "rb") as fh:
            before = hashlib.sha256(fh.read()).hexdigest()
        b, e = tools._load_board({"path": board})
        self.assertIsNone(e, e)
        out = oracle.net_gaps(b, net="+3V3", limit=3)
        self.assertEqual(out["status"], "ok", out)
        with open(board, "rb") as fh:
            self.assertEqual(before, hashlib.sha256(fh.read()).hexdigest())
        self.assertIn("routes nothing", out["assumptions"]["not_claimed"])

if __name__ == "__main__":
    unittest.main()
