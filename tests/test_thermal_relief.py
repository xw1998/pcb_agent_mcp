"""Thermal-relief spokes on board_ir_drop(model_zones=True).

Why this exists: a pour-connected pad is joined to the pour by SPOKES across a
relief gap, not by solid copper. Until 0.0.84 the model joined an in-fill pad at
1 uOhm, which is the one place this answer could be OPTIMISTIC - it under-states
the drop. This file pins the replacement down, and every claim has a control
that can fail:

  * the resistance must equal R_sheet*gap/(n*w) to the digit, and must halve when
    n doubles, halve when w doubles, double when the gap doubles,
  * the paired control is the same board WITHOUT the flag: the default must be
    LOWER than the relief answer (i.e. the default really is optimistic), and
    must still be the 0.0.83 number 0.935986 bit for bit (the switch is off by
    default and nothing else moved),
  * a pad that does not sit in the fill at all must still be connected - the
    anchor is skipped in the node loop when a relief is modelled, so a pad with
    no fill contact has to be picked up by the relief path or it silently drops
    out of the network (that was a real bug on the first cut: the net came back
    NOT_CONNECTED),
  * the relief must carry the SKIN-corrected sheet resistance at AC, not the DC
    foil, because a spoke is the same copper as the sheet,
  * every bad parameter is refused by name (nothing is defaulted: the geometry is
    the whole point, and a silently ignored key would leave the caller believing
    the relief was modelled),
  * what the board FILE says about reliefs is reported and never adopted.

Hand values, 35 um foil, rho(20C):
    R_sheet          = 1.724e-8 / 35e-6            = 4.9257142857e-4 ohm/sq
    4 x 0.5 mm over 0.5 mm = R_sheet * 0.5 / (4*0.5) = 1.2314285714e-4 ohm
"""
import inspect
import os
import sys
import itertools
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_ir_drop_zones import make, zone, PADS  # noqa: E402
from pcb_agent_mcp import oracle  # noqa: E402
from pcb_agent_mcp.calc import zonefield as ZF  # noqa: E402

RHO20 = 1.724e-8
FULL = zone(5, 5, 35, 15)          # reaches both pads
SHORT = zone(5, 5, 28, 15)         # U2 at x = 30 is outside the fill
TR = {"spokes": 4, "spoke_width_mm": 0.5, "gap_mm": 0.5}

_seq = itertools.count()


def sheet_ohm_per_sq(thick_um=35.0):
    return RHO20 / (thick_um * 1e-6)


def hand_ohm(spokes=4, width_mm=0.5, gap_mm=0.5, thick_um=35.0):
    return sheet_ohm_per_sq(thick_um) * gap_mm / (spokes * width_mm)


def run(kw, body=FULL):
    return oracle.board_ir_drop(make(body), "VCC", "U1.1", ["U2.1"],
                                current_a=1.0, model_zones=True,
                                zone_grid_mm=0.5, max_zone_nodes=20000, **kw)


def ok(r):
    assert r["status"] == "ok", r.get("message")
    return r


class TestTheFormulaIsTheFormula(unittest.TestCase):
    """R = R_sheet * gap / (n * w), to the digit - not merely 'plausible'."""

    def test_the_connection_resistance_is_the_hand_value(self):
        r = ok(run({"thermal_relief": TR}))
        tr = r["zone_summary"]["thermal_relief"]
        got = tr["connection_ohm_by_layer"]["F.Cu"]
        # the reported ohm value is rounded to 1e-12 absolute, so the ratio is
        # compared with a relative tolerance rather than a fixed one
        self.assertAlmostEqual(got / hand_ohm(), 1.0, delta=1e-6)
        # reported rounded to 12 decimals, so 1e-12 absolute is the floor here
        self.assertAlmostEqual(got, 1.2314285714e-4, delta=1e-12)
        self.assertEqual(tr["spokes"], 4)
        self.assertEqual(tr["spoke_width_mm"], 0.5)
        self.assertEqual(tr["gap_mm"], 0.5)
        self.assertIn("R_sheet * gap / (spokes * width)", tr["formula"])

    def test_the_helper_alone_equals_the_hand_value(self):
        self.assertAlmostEqual(
            ZF.thermal_spoke_ohm(sheet_ohm_per_sq(), 0.5, 4, 0.5)
            / hand_ohm(), 1.0, places=14)

    def test_doubling_the_spokes_halves_it(self):
        a = ok(run({"thermal_relief": dict(TR, spokes=2)}))
        b = ok(run({"thermal_relief": dict(TR, spokes=4)}))
        ra = a["zone_summary"]["thermal_relief"]["connection_ohm_by_layer"]["F.Cu"]
        rb = b["zone_summary"]["thermal_relief"]["connection_ohm_by_layer"]["F.Cu"]
        self.assertAlmostEqual(ra / rb, 2.0, delta=1e-6)

    def test_doubling_the_width_halves_it(self):
        a = ok(run({"thermal_relief": dict(TR, spoke_width_mm=0.5)}))
        b = ok(run({"thermal_relief": dict(TR, spoke_width_mm=1.0)}))
        ra = a["zone_summary"]["thermal_relief"]["connection_ohm_by_layer"]["F.Cu"]
        rb = b["zone_summary"]["thermal_relief"]["connection_ohm_by_layer"]["F.Cu"]
        self.assertAlmostEqual(ra / rb, 2.0, delta=1e-6)

    def test_doubling_the_gap_doubles_it(self):
        a = ok(run({"thermal_relief": dict(TR, gap_mm=0.5)}))
        b = ok(run({"thermal_relief": dict(TR, gap_mm=1.0)}))
        ra = a["zone_summary"]["thermal_relief"]["connection_ohm_by_layer"]["F.Cu"]
        rb = b["zone_summary"]["thermal_relief"]["connection_ohm_by_layer"]["F.Cu"]
        self.assertAlmostEqual(rb / ra, 2.0, delta=1e-6)


class TestTheDefaultIsTheOptimisticOne(unittest.TestCase):
    """The control that makes the feature worth having: without it the answer
    under-states the drop, and with the switch off nothing moved at all."""

    def test_the_switch_is_off_by_default_in_the_signature(self):
        sig = inspect.signature(oracle.board_ir_drop)
        self.assertIsNone(sig.parameters["thermal_relief"].default)

    def test_the_default_is_still_the_0_0_83_number_bit_for_bit(self):
        r = ok(run({}))
        self.assertAlmostEqual(r["value"]["max_drop_mv"], 0.935986, places=6)
        self.assertNotIn("thermal_relief", r["zone_summary"])

    def test_the_relief_raises_the_drop(self):
        base = ok(run({}))["value"]["max_drop_mv"]
        with_tr = ok(run({"thermal_relief": TR}))["value"]["max_drop_mv"]
        self.assertGreater(with_tr, base)
        self.assertAlmostEqual(base, 0.935986, places=6)
        self.assertAlmostEqual(with_tr, 1.207938, places=6)

    def test_the_rise_is_the_two_connections_in_the_path(self):
        # Both pads sit in the fill, so both connections are reliefs and both are
        # in series with the sheet: the rise must be about 2*R, and it cannot be
        # less than 2*R (that would mean a connection was counted for free).
        base = ok(run({}))["value"]["max_drop_mv"]
        with_tr = ok(run({"thermal_relief": TR}))["value"]["max_drop_mv"]
        rise_ohm = (with_tr - base) * 1e-3
        two = 2.0 * hand_ohm()
        self.assertGreaterEqual(rise_ohm, 0.999 * two)
        # it is above 2R by a little because a real resistance redistributes the
        # current through the sheet, which then drops a bit more too
        self.assertLess(rise_ohm, 1.5 * two)

    def test_both_pads_are_reported_as_modelled(self):
        r = ok(run({"thermal_relief": TR}))
        tr = r["zone_summary"]["thermal_relief"]
        self.assertEqual(tr["pad_connections_modelled"], 2)
        self.assertEqual(len(tr["connections"]), 2)
        for c in tr["connections"]:
            self.assertEqual(c["layer"], "F.Cu")
            self.assertGreater(c["cells"], 1)       # split over the contact cells
        self.assertEqual(tr["pads_split_over_cells"], 2)

    def test_the_partition_is_even_so_the_parallel_combination_is_R(self):
        # each contacted cell gets k*R, so k of them in parallel are R, not R/k:
        # the pad's whole connection is ONE relief, not one per contact cell.
        r = ok(run({"thermal_relief": TR}))
        n = [c["cells"] for c in r["zone_summary"]["thermal_relief"]["connections"]]
        for k in n:
            r_cell = hand_ohm() * float(k)
            self.assertAlmostEqual(1.0 / (k * (1.0 / r_cell)), hand_ohm(), places=14)

    def test_the_envelope_says_what_was_left_out(self):
        r = ok(run({"thermal_relief": TR}))
        tr = r["zone_summary"]["thermal_relief"]
        text = " ".join(tr["not_in_it"])
        self.assertIn("spreading", text)
        self.assertIn("crowding", text)
        self.assertIn("not clamped", text)
        warns = " ".join(r.get("warnings") or [])
        self.assertIn("OPTIMISTIC", warns)
        self.assertIn("thermal reliefs", warns)


class TestMonotonicity(unittest.TestCase):
    def test_more_spokes_is_less_drop(self):
        vals = [ok(run({"thermal_relief": dict(TR, spokes=n)}))["value"]["max_drop_mv"]
                for n in (1, 2, 4, 8)]
        self.assertEqual(vals, sorted(vals, reverse=True), vals)
        self.assertAlmostEqual(vals[0], 1.947975, places=6)
        self.assertAlmostEqual(vals[2], 1.207938, places=6)

    def test_a_wider_spoke_is_less_drop(self):
        vals = [ok(run({"thermal_relief": dict(TR, spoke_width_mm=w)}))["value"]["max_drop_mv"]
                for w in (0.25, 0.5, 1.0)]
        self.assertEqual(vals, sorted(vals, reverse=True), vals)
        self.assertAlmostEqual(vals[0], 1.454998, places=6)
        self.assertAlmostEqual(vals[2], 1.083380, places=6)

    def test_a_wider_gap_is_more_drop(self):
        vals = [ok(run({"thermal_relief": dict(TR, gap_mm=g)}))["value"]["max_drop_mv"]
                for g in (0.25, 0.5, 1.0)]
        self.assertEqual(vals, sorted(vals), vals)


class TestTheSkinDepthComesAlong(unittest.TestCase):
    def test_the_spoke_carries_the_effective_thickness_at_ac(self):
        dc = ok(run({"thermal_relief": TR}))
        ac = ok(run({"thermal_relief": TR, "frequency_hz": 1e6}))
        r_dc = dc["zone_summary"]["thermal_relief"]["connection_ohm_by_layer"]["F.Cu"]
        r_ac = ac["zone_summary"]["thermal_relief"]["connection_ohm_by_layer"]["F.Cu"]
        ratio = ac["zone_summary"]["sheets"][0]["r_ac_over_rdc"]
        self.assertGreater(ratio, 1.0)
        self.assertAlmostEqual(r_ac / r_dc, ratio, delta=1e-6)
        self.assertGreater(ac["value"]["max_drop_mv"], dc["value"]["max_drop_mv"])


class TestAPadOutsideTheFillIsStillConnected(unittest.TestCase):
    """The anchor is skipped in the node loop when a relief is modelled; a pad
    with no fill contact therefore has to be picked up by the relief path."""

    def test_a_pad_two_mm_outside_the_fill_connects_across_the_gap(self):
        r = ok(run({"thermal_relief": TR, "zone_link_mm": 3.0}, body=SHORT))
        conns = r["zone_summary"]["thermal_relief"]["connections"]
        self.assertEqual(len(conns), 2)
        far = [c for c in conns if c["gap_mm"] is not None]
        self.assertEqual(len(far), 1)
        self.assertEqual(far[0]["cells"], 1)
        self.assertGreater(far[0]["gap_mm"], 1.0)

    def test_the_relief_is_better_than_the_one_square_stand_in(self):
        # without the flag the bridge is priced as ONE sheet square regardless of
        # how far it is; with the relief it is 4 x 0.5 mm over 0.5 mm, which is a
        # quarter of a square - so the relief answer must come out LOWER here.
        bare = ok(run({"zone_link_mm": 3.0}, body=SHORT))["value"]["max_drop_mv"]
        used = ok(run({"thermal_relief": TR, "zone_link_mm": 3.0},
                      body=SHORT))["value"]["max_drop_mv"]
        self.assertLess(used, bare)
        self.assertLess(hand_ohm(), sheet_ohm_per_sq())


class TestTheFileValueIsReportedNeverAdopted(unittest.TestCase):
    def _board_with(self, connect_pads):
        return zone(5, 5, 35, 15).replace("(connect_pads (clearance 0.2))",
                                          connect_pads)

    def test_a_wordless_pour_is_a_thermal_relief_and_is_flagged_not_adopted(self):
        # MEASURED on KiCad 10.0.7: a zone that writes NO mode word comes back
        # from pcbnew's GetPadConnection() as ZONE_CONNECTION_THERMAL (1) - that
        # is how KiCad itself saves a thermal zone. So the file states nothing
        # (`connect_pads` is None) and KiCad reads "thermal".
        r = ok(run({}, body=self._board_with("(connect_pads (clearance 0.2))")))
        st = r["zone_summary"]["board_file_states"]
        self.assertEqual(len(st), 1)
        self.assertIsNone(st[0]["connect_pads"])
        self.assertEqual(st[0]["connect_pads_effective"], "thermal")
        self.assertEqual(st[0]["layers"], ["F.Cu"])
        self.assertAlmostEqual(st[0]["thermal_gap_mm"], 0.5)
        self.assertAlmostEqual(st[0]["thermal_bridge_width_mm"], 0.5)
        warns = " ".join(r.get("warnings") or [])
        self.assertIn("THERMAL RELIEF", warns)
        self.assertIn("OPTIMISTIC", warns)
        self.assertIn("Pass thermal_relief", warns)

    def test_a_solid_pour_is_reported_as_solid(self):
        # `(connect_pads yes ...)` is SOLID (pcbnew ZONE_CONNECTION_FULL = 2).
        r = ok(run({}, body=self._board_with("(connect_pads yes (clearance 0.2))")))
        st = r["zone_summary"]["board_file_states"]
        self.assertEqual(st[0]["connect_pads"], "solid")
        self.assertEqual(st[0]["connect_pads_effective"], "solid")
        self.assertEqual(st[0]["connect_pads_source"], "file")
        # solid copper is what the answer assumes anyway, so nothing to flag
        self.assertNotIn("THERMAL RELIEF", " ".join(r.get("warnings") or []))

    def test_no_connection_is_reported_as_none(self):
        # `(connect_pads no ...)` is ZONE_CONNECTION_NONE = 0.
        r = ok(run({}, body=self._board_with("(connect_pads no (clearance 0.2))")))
        st = r["zone_summary"]["board_file_states"]
        self.assertEqual(st[0]["connect_pads"], "none")
        self.assertEqual(st[0]["connect_pads_effective"], "none")
        # and the answer is NOT the relief answer: the file said so, we did not act
        self.assertAlmostEqual(r["value"]["max_drop_mv"], 0.935986, places=6)

    def test_passing_the_numbers_silences_the_flag(self):
        r = ok(run({"thermal_relief": TR},
                   body=self._board_with("(connect_pads yes (clearance 0.2))")))
        warns = " ".join(r.get("warnings") or [])
        self.assertNotIn("but thermal_relief was not passed", warns)


class TestBadParametersAreRefusedByName(unittest.TestCase):
    def _bad(self, tr, **kw):
        r = oracle.board_ir_drop(make(FULL), "VCC", "U1.1", ["U2.1"],
                                 current_a=1.0, model_zones=True,
                                 zone_grid_mm=0.5, max_zone_nodes=20000,
                                 thermal_relief=tr, **kw)
        self.assertEqual(r["status"], "error", r)
        return r

    def test_not_an_object(self):
        r = self._bad([4, 0.5, 0.5])
        self.assertEqual(r["reason_code"], "ARG_INVALID")
        self.assertIn("must be an object", r["message"])

    def test_unknown_key_is_an_error_not_a_shrug(self):
        r = self._bad(dict(TR, spokes_mm=0.5))
        self.assertEqual(r["reason_code"], "ARG_INVALID")
        self.assertIn("spokes_mm", r["message"])

    def test_missing_key(self):
        for key in ("spokes", "spoke_width_mm", "gap_mm"):
            tr = dict(TR)
            tr.pop(key)
            r = self._bad(tr)
            self.assertEqual(r["reason_code"], "ARG_REQUIRED")
            self.assertIn(key, r["message"])
            self.assertIn("no sane default", r["message"])

    def test_spokes_out_of_range(self):
        for n in (0, 17, -1):
            r = self._bad(dict(TR, spokes=n))
            self.assertEqual(r["reason_code"], "ARG_RANGE")
            self.assertIn("1..16", r["message"])

    def test_zero_width(self):
        r = self._bad(dict(TR, spoke_width_mm=0.0))
        self.assertEqual(r["reason_code"], "ARG_RANGE")

    def test_zero_gap_points_at_the_solid_case(self):
        r = self._bad(dict(TR, gap_mm=0.0))
        self.assertEqual(r["reason_code"], "ARG_RANGE")
        self.assertIn("solid", (r.get("hint") or ""))

    def test_non_numbers(self):
        r = self._bad(dict(TR, spokes="four"))
        self.assertEqual(r["reason_code"], "ARG_INVALID")

    def test_the_pour_must_be_on(self):
        r = oracle.board_ir_drop(make(FULL), "VCC", "U1.1", ["U2.1"],
                                 current_a=1.0, thermal_relief=TR)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "ARG_CONFLICT")
        self.assertIn("model_zones", r["message"])


class TestTheToolExposesIt(unittest.TestCase):
    def test_the_schema_asks_for_all_three_and_defaults_none_of_them(self):
        from pcb_agent_mcp import tools
        sch = tools.TOOLS["board_ir_drop"]["inputSchema"]["properties"]["thermal_relief"]
        self.assertEqual(sch["type"], "object")
        self.assertEqual(sorted(sch["required"]),
                         ["gap_mm", "spoke_width_mm", "spokes"])
        self.assertIn("R_sheet", sch["description"])
        self.assertIn("OPTIMISTIC", sch["description"])

    def test_the_tool_still_returns_the_optimistic_default_when_not_asked(self):
        from pcb_agent_mcp import tools
        from test_ir_drop_zones import write_board
        args = {"path": write_board(FULL), "net": "VCC", "source": "U1.1", "sinks": ["U2.1"],
                "current_a": 1.0, "model_zones": True, "zone_grid_mm": 0.5,
                "max_zone_nodes": 20000}
        r = tools.TOOLS["board_ir_drop"]["handler"](args)
        self.assertEqual(r["status"], "ok", r.get("message"))
        self.assertAlmostEqual(r["value"]["max_drop_mv"], 0.935986, places=6)
        r2 = tools.TOOLS["board_ir_drop"]["handler"](dict(args, thermal_relief=TR))
        self.assertEqual(r2["status"], "ok", r2.get("message"))
        self.assertAlmostEqual(r2["value"]["max_drop_mv"], 1.207938, places=6)


if __name__ == "__main__":
    unittest.main()
