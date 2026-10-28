"""Golden tests for the Calc layer.

These are the only layer that can be verified exhaustively: pure functions, no
geometry, no file format. Every expected value below was computed by hand from
the stated formula, not copied from the implementation.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp.calc import current, impedance, units, rf, creepage  # noqa: E402
from pcb_agent_mcp import tools  # noqa: E402

REQUIRED_KEYS = ("status", "value", "unit", "model", "basis", "precision", "assumptions")


class TestUnits(unittest.TestCase):
    def test_easyeda_internal_unit(self):
        r = units.convert(4000, "0.01inch", "mm")
        self.assertEqual(r["status"], "ok")
        self.assertAlmostEqual(r["value"], 1016.0, places=6)

    def test_mil_to_mm(self):
        self.assertAlmostEqual(units.convert(1, "mil", "mm")["value"], 0.0254, places=9)

    def test_unknown_unit_refused(self):
        self.assertEqual(units.convert(1, "furlong", "mm")["reason_code"], "UNIT_UNKNOWN")


class TestCurrent(unittest.TestCase):
    def test_one_amp_external(self):
        # A = (1/(0.048*10^0.44))^(1/0.725) = 16.30 mil^2 ; t=1.378mil ; w=11.83mil
        r = current.width_for_current(1.0, 10, 1, "external", "ipc2221")
        self.assertAlmostEqual(r["value"], 0.3021, places=3)

    def test_internal_needs_more_width(self):
        ext = current.width_for_current(1.0, 10, 1, "external", "ipc2221")["value"]
        intl = current.width_for_current(1.0, 10, 1, "internal", "ipc2221")["value"]
        self.assertGreater(intl, ext * 2)

    def test_lower_delta_t_needs_more_width(self):
        r10 = current.width_for_current(1.0, 10, 1, "external", "ipc2221")["value"]
        r5 = current.width_for_current(1.0, 5, 1, "external", "ipc2221")["value"]
        self.assertGreater(r5, r10)

    def test_roundtrip(self):
        w = current.width_for_current(3.0, 10, 1, "external", "ipc2221")["value"]
        i = current.current_capacity(w, 10, 1, "external", standard="ipc2221")["value"]
        self.assertAlmostEqual(i, 3.0, places=2)

    def test_standard_is_mandatory(self):
        self.assertEqual(current.width_for_current(1.0)["reason_code"], "STANDARD_REQUIRED")

    # --- IPC-2152: the closed-form fit (Brooks & Adam 2015 to Table 3-1). The
    # expected numbers below are computed by hand from that formula with the
    # coefficients copied from KiCad 10.0, not read back off this implementation.
    def test_ipc2152_one_amp_external(self):
        # W = (10 / (215.3 * 1^2 * 1.378^-1))^(1/-1.15) = 10.9165 mil = 0.2773 mm
        r = current.width_for_current(1.0, 10, 1, "external", "ipc2152")
        self.assertAlmostEqual(r["value"], 0.2773, places=3)
        self.assertEqual(r["model"], "ipc2152_brooks_adam_fit")

    def test_ipc2152_current_from_width(self):
        # 10 mil at 1 oz with 10 degC budget: I = (10 / (215.3*10^-1.15*1.378^-1))^(1/2)
        i = current.current_capacity(0.254, 10, 1, "external", standard="ipc2152")["value"]
        self.assertAlmostEqual(i, 0.951, places=3)

    def test_ipc2152_roundtrip(self):
        w = current.width_for_current(3.0, 15, 2, "external", "ipc2152")["value"]
        i = current.current_capacity(w, 15, 2, "external", standard="ipc2152")["value"]
        self.assertAlmostEqual(i, 3.0, places=2)

    def test_ipc2152_internal_is_not_derated_by_two(self):
        # IPC-2152 found internal traces cool about as well as external ones; the
        # closed-form fit reproduces that (9.78 mil vs 10.92 mil at 1 oz), which is
        # the opposite of the IPC-2221 2x rule. A swapped coefficient would break it.
        ext = current.width_for_current(1.0, 10, 1, "external", "ipc2152")["value"]
        intl = current.width_for_current(1.0, 10, 1, "internal", "ipc2152")["value"]
        self.assertLess(intl, ext)
        self.assertGreater(intl, ext * 0.8)

    def test_ipc2152_is_less_conservative_than_ipc2221_outside(self):
        w2152 = current.width_for_current(1.0, 10, 1, "external", "ipc2152")["value"]
        w2221 = current.width_for_current(1.0, 10, 1, "external", "ipc2221")["value"]
        self.assertLess(w2152, w2221)

    def test_ipc2152_bucket_switch_is_at_the_midpoint(self):
        # KiCad picks the internal bucket by nominal thickness midpoints; with
        # 1.378 mil/oz the 0.5/1 oz boundary is 0.75 oz exactly (strictly less -> 0.5).
        low = current.width_for_current(1.0, 10, 0.74, "internal", "ipc2152")
        high = current.width_for_current(1.0, 10, 0.76, "internal", "ipc2152")
        self.assertIn("0.5 oz", low["coefficient_set"])
        self.assertIn("1 oz", high["coefficient_set"])

    def test_ipc2152_flags_out_of_table_copper_weight(self):
        r = current.width_for_current(1.0, 10, 4.0, "external", "ipc2152")
        self.assertIn("outside_fit_copper_weight", r)

    def test_ipc2152_refuses_via_barrels(self):
        r = current.via_current_allowed(0.3, 25, 1, 10, "ipc2152")
        self.assertEqual(r["reason_code"], "STANDARD_SCOPE")

    def test_ipc2152_still_validates_arguments(self):
        self.assertEqual(
            current.width_for_current(1.0, 10, 1, "sideways", "ipc2152")["reason_code"],
            "ARG_INVALID")
        self.assertEqual(
            current.width_for_current(-1.0, 10, 1, "external", "ipc2152")["reason_code"],
            "ARG_RANGE")

    def test_safety_table_ipc2152_model_is_judged(self):
        from pcb_agent_mcp.calc import safety_table
        report = safety_table.check_which("ipc2152")
        self.assertTrue(report["usable"], report["failed"])
        self.assertIn("model_reproduces_anchors", [c["check"] for c in report["checks"]])

    def test_safety_table_ipc2152_catches_a_broken_coefficient(self):
        import json as _json
        from pcb_agent_mcp.calc import safety_table
        with open(safety_table.IPC2152_PATH, "r", encoding="utf-8") as fh:
            doc = _json.load(fh)
        doc["model"]["external"]["K"] = 400.0
        report = safety_table.validate_ipc2152(doc)
        self.assertFalse(report["usable"])
        self.assertIn("model_reproduces_anchors", report["failed"])
        doc["model"]["external"]["K"] = 215.3
        doc["model"]["internal"][1]["c"] = 0.5   # thicker copper would heat up: impossible
        report = safety_table.validate_ipc2152(doc)
        self.assertFalse(report["usable"])
        self.assertIn("model_coefficients_sane", report["failed"])

    def test_zero_temp_rise_refused(self):
        self.assertEqual(current.width_for_current(1.0, 0, standard="ipc2221")["reason_code"],
                         "ARG_RANGE")

    def test_via_has_precision_warning(self):
        r = current.via_current_allowed(0.3, 25, 1, 10, "ipc2221")
        self.assertIn("extrapolation", r["basis"])
        self.assertTrue(r["assumptions"]["not_standard_defined"])


class TestImpedance(unittest.TestCase):
    def test_microstrip_forward(self):
        r = impedance.trace_impedance(0.3, 0.2, 35, 4.3, "microstrip")
        self.assertAlmostEqual(r["value"], 53.52, places=1)

    def test_reverse_roundtrip(self):
        z = impedance.trace_impedance(0.3, 0.2, 35, 4.3, "microstrip")["value"]
        w = impedance.width_for_impedance(z, 0.2, 35, 4.3, "microstrip")["value"]
        self.assertAlmostEqual(w, 0.3, places=4)

    def test_wider_is_lower_impedance(self):
        z1 = impedance.trace_impedance(0.2, 0.2, 35, 4.3)["value"]
        z2 = impedance.trace_impedance(0.5, 0.2, 35, 4.3)["value"]
        self.assertGreater(z1, z2)

    def test_stripline_lower_than_microstrip(self):
        ms = impedance.trace_impedance(0.3, 0.2, 35, 4.3, "microstrip")["value"]
        sl = impedance.trace_impedance(0.3, 0.2, 35, 4.3, "stripline")["value"]
        self.assertLess(sl, ms)

    def test_out_of_range_target_refused(self):
        # 5 ohm is below the 10..200 validity window -> refused before reachability
        r = impedance.width_for_impedance(5, 0.2, 35, 4.3, "microstrip")
        self.assertEqual(r["reason_code"], "OUT_OF_VALIDITY")

    def test_unreachable_target_refused(self):
        # h=0.2mm / er=4.3 microstrip tops out near 128 ohm as w->0,
        # so 150 ohm is inside the 10..200 window but physically unreachable.
        r = impedance.width_for_impedance(150, 0.2, 35, 4.3, "microstrip")
        self.assertEqual(r["reason_code"], "UNREACHABLE")
        self.assertIn("reachable", r["hint"].lower())


class TestRF(unittest.TestCase):
    def test_lambda_over_20_fr4_2g4(self):
        # lambda = 299.79 / (2.4 * sqrt(4.3)) = 60.2 mm ; /20 = 3.01 mm
        self.assertAlmostEqual(rf.lambda_over_20(2.4, 4.3)["value"], 3.011, places=2)

    def test_higher_freq_tighter(self):
        self.assertLess(rf.lambda_over_20(5.8)["value"], rf.lambda_over_20(2.4)["value"])


class TestSafetyFailClosed(unittest.TestCase):
    def test_creepage_refuses_without_table(self):
        r = creepage.creepage_required(250)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "SAFETY_TABLE_MISSING")
        self.assertIn("hint", r)

    def test_clearance_refuses_without_table(self):
        self.assertEqual(creepage.clearance_required(250)["reason_code"],
                         "SAFETY_TABLE_MISSING")


class TestEnvelopeContract(unittest.TestCase):
    def test_every_ok_result_has_all_contract_keys(self):
        calls = [
            ("unit_convert", {"value": 1, "from_unit": "mm", "to_unit": "mil"}),
            ("width_for_current", {"current_a": 1, "standard": "ipc2221"}),
            ("current_capacity", {"width_mm": 0.3, "standard": "ipc2221"}),
            ("via_current_allowed", {"hole_mm": 0.3, "standard": "ipc2221"}),
            ("trace_impedance", {"width_mm": 0.3, "dielectric_mm": 0.2}),
            ("width_for_impedance", {"target_ohm": 50, "dielectric_mm": 0.2}),
            ("lambda_over_20", {"freq_ghz": 2.4}),
        ]
        for name, args in calls:
            r = tools.call_tool(name, args)
            self.assertEqual(r["status"], "ok", name)
            for key in REQUIRED_KEYS:
                self.assertIn(key, r, "%s missing %s" % (name, key))
            self.assertTrue(str(r["basis"]).strip(), name)
            self.assertTrue(str(r["precision"]).strip(), name)

    def test_unknown_tool(self):
        self.assertEqual(tools.call_tool("nope", {})["reason_code"], "TOOL_NOT_FOUND")

    def test_missing_argument(self):
        self.assertEqual(tools.call_tool("width_for_current", {})["reason_code"], "ARG_MISSING")

    def test_every_tool_has_description_and_schema(self):
        for spec in tools.list_tools():
            self.assertTrue(spec["description"].strip())
            self.assertEqual(spec["inputSchema"]["type"], "object")


if __name__ == "__main__":
    unittest.main(verbosity=2)
