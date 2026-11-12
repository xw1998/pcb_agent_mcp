# -*- coding: utf-8 -*-
"""tools/power_tree.py 的用例。

每条判据都配一条"故意弄坏它"的对照：把 gate 写成 return [] 也必须让这里变红。
Nothing here is a route or a placement decision; it is a supply-assignment gate.
"""

import copy
import csv
import importlib.util
import io
import os
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
TOOL = os.path.join(REPO, "tools", "power_tree.py")
GROUPS = os.path.join(REPO, "examples", "rk3506_som", "power_groups_rk3506j.csv")
_MOD = None


def pt():
    global _MOD
    if _MOD is None:
        spec = importlib.util.spec_from_file_location("power_tree", TOOL)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _MOD = mod
    return _MOD


def real_groups():
    return pt().read_groups(GROUPS)


class BuildTest(unittest.TestCase):
    def test_build_real_table(self):
        rails, balls, report = pt().build(GROUPS)
        self.assertEqual(report["balls"], 176)
        self.assertEqual(report["rails"], 10)
        self.assertEqual(len(balls), 176)
        self.assertEqual(len(rails), 10)
        self.assertLessEqual(report["input_power_w"], report["input_budget_w"])

    def test_no_ball_lands_on_two_rails(self):
        _, balls, _ = pt().build(GROUPS)
        # 1A13 is listed twice by the datasheet (VSS run and ACODEC_ADC_AVSS),
        # so uniqueness is asserted at the *rail* level, not the group level.
        per_ball = {}
        for b in balls:
            per_ball.setdefault(b["ball"], set()).add(b["rail"])
        for ball, rails in per_ball.items():
            self.assertEqual(len(rails), 1, "ball %s -> %s" % (ball, rails))
        self.assertEqual(pt().check_unique(balls), [])

    def test_double_listed_ball_is_reported(self):
        _, _, report = pt().build(GROUPS)
        self.assertEqual(report["double_listed"], ["1A13"])

    def test_double_listed_ball_on_two_rails_is_refused(self):
        balls = [{"ball": "1A13", "group": "VSS", "rail": pt().GROUND},
                 {"ball": "1A13", "group": "ACODEC_ADC_AVSS", "rail": "+3V3"}]
        self.assertTrue(pt().check_unique(balls))

    def test_every_group_is_a_supply_or_internal_or_ground(self):
        _, balls, _ = pt().build(GROUPS)
        for b in balls:
            self.assertIn(b["rail"], {r["rail"] for r in pt().build(GROUPS)[0]} |
                          {pt().INTERNAL})

    def test_ground_count_matches_table_2_2(self):
        _, balls, report = pt().build(GROUPS)
        self.assertEqual(report["per_rail_balls"][pt().GROUND], 141)

    def test_new_four_balls_are_in_the_source_table(self):
        groups = {g["group"] for g in real_groups()}
        for name in ("DDR_VREF_CA", "DDR_VREF_DQ",
                     "ACODEC_ADC_VCM", "ACODEC_ADC_AVDD1V6"):
            self.assertIn(name, groups)

    def test_vref_rows_map_to_the_vref_rail(self):
        _, balls, _ = pt().build(GROUPS)
        vref = [b for b in balls if b["rail"] == "+0V675_REF"]
        self.assertEqual(sorted(b["group"] for b in vref),
                         ["DDR_VREF_CA", "DDR_VREF_DQ"])


class VrefTest(unittest.TestCase):
    def test_real_rails_pass(self):
        self.assertEqual(pt().check_vref(), [])

    def test_rounding_vref_to_068_is_refused(self):
        rails = copy.deepcopy(pt().RAILS)
        rails["+0V675_REF"]["typ"] = 0.68
        errs = pt().check_vref(rails)
        self.assertTrue(errs, "0.68 is not VDDQ/2 and must be refused")

    def test_vddq_125_is_refused(self):
        rails = copy.deepcopy(pt().RAILS)
        rails["+1V35_DDR"]["typ"] = 1.25
        rails["+0V675_REF"]["typ"] = 0.625
        # 0.625 *is* 1.25/2 but 1.25 V is not the DDR3L band, caught by the band check
        self.assertEqual(pt().check_vref(rails), [])
        groups = [{"group": "DDR_VDDQ", "ball": "1K6", "min": [1.283],
                   "typ": [1.35], "max": [1.418], "unit": "V"}]
        groups2 = [{"group": "DDR_VDDQ", "ball": "1K6", "min": [1.20],
                    "typ": [1.25], "max": [1.40], "unit": "V"}]
        self.assertTrue(pt().check_rail_bands(groups2, rails))


class BandTest(unittest.TestCase):
    def test_real_bands_pass(self):
        self.assertEqual(pt().check_rail_bands(real_groups()), [])

    def test_cpu_band_below_rail_min_is_refused(self):
        groups = real_groups()
        for g in groups:
            if g["group"] == "CPU_DVDD":
                g["min"] = [0.80]
        errs = pt().check_rail_bands(groups)
        self.assertTrue(any("CPU_DVDD" in e for e in errs))

    def test_rail_narrowed_below_the_ball_band_is_refused(self):
        rails = copy.deepcopy(pt().RAILS)
        rails["+0V9_CPU"]["max"] = 1.05
        groups = real_groups()
        errs = pt().check_rail_bands(groups, rails)
        self.assertTrue(any("CPU_DVDD" in e for e in errs),
                        "1.103 V ball band must not pass a 1.05 V rail ceiling")

    def test_dual_band_pad_needs_one_band_inside(self):
        rails = copy.deepcopy(pt().RAILS)
        rails["+3V3"]["typ"] = 2.50
        rails["+3V3"]["min"] = 2.40
        rails["+3V3"]["max"] = 2.60
        groups = real_groups()
        errs = pt().check_rail_bands(groups, rails)
        self.assertTrue(any("VCCIO1_VCC" in e for e in errs))

    def test_dual_band_pad_passes_on_the_3v3_band(self):
        rails = copy.deepcopy(pt().RAILS)
        groups = real_groups()
        self.assertEqual(pt().check_rail_bands(groups, rails), [])

    def test_mismatched_dual_band_is_refused(self):
        groups = real_groups()
        for g in groups:
            if g["group"] == "VCCIO2_VCC":
                g["min"] = [1.62, 3.00]
                g["max"] = [1.98]
        errs = pt().check_rail_bands(groups)
        self.assertTrue(any("mismatched" in e for e in errs))


class CoverTest(unittest.TestCase):
    def test_real_cover_pass(self):
        self.assertEqual(pt().check_cover(real_groups()), [])

    def test_typo_group_is_refused(self):
        groups = real_groups() + [{"group": "CPU_DVD", "ball": "1C2",
                                   "min": [0.855], "typ": [0.95], "max": [1.103],
                                   "unit": "V"}]
        self.assertTrue(any("CPU_DVD" in e for e in pt().check_cover(groups)))

    def test_rule_without_ball_is_refused(self):
        groups = [g for g in real_groups() if g["group"] != "VCCIO4_VCC"]
        self.assertTrue(any("VCCIO4_VCC" in e for e in pt().check_cover(groups)))

    def test_unknown_rail_target_is_refused(self):
        saved = pt().GROUPS["VCCIO1_VCC"]
        try:
            pt().GROUPS["VCCIO1_VCC"] = "+3V3X"
            errs = pt().check_cover(real_groups())
            self.assertTrue(any("+3V3X" in e for e in errs))
        finally:
            pt().GROUPS["VCCIO1_VCC"] = saved


class InternalTest(unittest.TestCase):
    def test_real_internal_pass(self):
        self.assertEqual(pt().check_internal(real_groups()), [])

    def test_internal_with_a_supply_band_is_refused(self):
        groups = real_groups()
        for g in groups:
            if g["group"] == "ACODEC_ADC_VCM":
                g["min"] = [1.62]
                g["typ"] = [1.80]
                g["max"] = [1.98]
        errs = pt().check_internal(groups)
        self.assertTrue(any("ACODEC_ADC_VCM" in e for e in errs))

    def test_internal_without_decoupling_rule_is_refused(self):
        saved = dict(pt().INTERNAL_GROUPS)
        try:
            pt().INTERNAL_GROUPS.pop("ACODEC_ADC_AVDD1V6")
            errs = pt().check_internal(real_groups())
            self.assertTrue(any("ACODEC_ADC_AVDD1V6" in e for e in errs))
        finally:
            pt().INTERNAL_GROUPS.update(saved)


class SourceTest(unittest.TestCase):
    def test_real_sources_pass(self):
        self.assertEqual(pt().check_sources(), [])

    def test_cycle_is_refused(self):
        rails = copy.deepcopy(pt().RAILS)
        rails["+0V9_CPU"]["src"] = "+0V9_LOGIC"
        rails["+0V9_LOGIC"]["src"] = "+0V9_CPU"
        errs = pt().check_sources(rails)
        self.assertTrue(any("cycle" in e for e in errs))

    def test_orphan_rail_is_refused(self):
        rails = copy.deepcopy(pt().RAILS)
        rails["+0V9_LOGICAL"] = {"typ": 0.9, "min": 0.855, "max": 0.998,
                                 "kind": "bead", "src": None, "device": "x",
                                 "lcsc": "", "rating_ma": None, "load_ma": 1,
                                 "basis": "tamper"}
        errs = pt().check_sources(rails)
        self.assertTrue(any("+0V9_LOGICAL" in e for e in errs))

    def test_unknown_source_is_refused(self):
        rails = copy.deepcopy(pt().RAILS)
        rails["+0V9_ANA"]["src"] = "+0V9_NOWHERE"
        errs = pt().check_sources(rails)
        self.assertTrue(any("+0V9_NOWHERE" in e for e in errs))

    def test_ground_without_source_is_fine(self):
        # regression: the first version flagged GND as an orphan rail
        self.assertEqual(pt().check_sources(), [])


class LoadTest(unittest.TestCase):
    def test_real_load_passes(self):
        errs, power = pt().check_load()
        self.assertEqual(errs, [])
        self.assertLessEqual(power, pt().INPUT_BUDGET_W)

    def test_over_derating_is_refused(self):
        rails = copy.deepcopy(pt().RAILS)
        rails["+0V9_CPU"]["load_ma"] = 2600   # 2600 > 0.8 * 3000
        errs, _ = pt().check_load(rails)
        self.assertTrue(any("+0V9_CPU" in e for e in errs))

    def test_input_budget_is_enforced(self):
        rails = copy.deepcopy(pt().RAILS)
        rails["+3V3"]["load_ma"] = 2400        # exactly the derated ceiling
        errs, power = pt().check_load(rails)
        self.assertTrue(any("budget" in e for e in errs), "power=%.3f" % power)

    def test_ldo_branch_is_not_counted_as_input_power(self):
        # the 1V8 LDO is fed from 3V3, so only the 3V3 converter counts at 5 V
        errs, power = pt().check_load()
        self.assertAlmostEqual(power, 8.220, places=3)


class CliTest(unittest.TestCase):
    def test_cli_writes_both_tables(self):
        tmp = tempfile.mkdtemp()
        rails = os.path.join(tmp, "power_rails.csv")
        balls = os.path.join(tmp, "power_balls.csv")
        rc = pt().main(["--groups", GROUPS, "--out-rails", rails, "--out-balls", balls])
        self.assertEqual(rc, 0)
        with io.open(rails, encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual(len(rows), 10)
        self.assertEqual(list(rows[0].keys()), pt().RAIL_FIELDS)
        with io.open(balls, encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual(len(rows), 176)
        self.assertEqual(list(rows[0].keys()), pt().BALL_FIELDS)

    def test_cli_refuses_a_broken_table(self):
        tmp = tempfile.mkdtemp()
        bad = os.path.join(tmp, "bad.csv")
        src = io.open(GROUPS, encoding="utf-8").read().replace("CPU_DVDD,1C2",
                                                              "CPU_DVD,1C2")
        io.open(bad, "w", encoding="utf-8", newline="").write(src)
        rc = pt().main(["--groups", bad,
                        "--out-rails", os.path.join(tmp, "r.csv"),
                        "--out-balls", os.path.join(tmp, "b.csv")])
        self.assertEqual(rc, 2, "a typo must refuse the whole tree, not warn")


if __name__ == "__main__":
    unittest.main()
