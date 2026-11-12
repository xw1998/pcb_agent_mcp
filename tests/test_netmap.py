"""Tests for tools/gen_netmap.py.

Every audit in the tool gets a reverse control here: the table is broken on
purpose and the build must *refuse*, with the message that names the break.
A passing build alone would only prove that the happy path is still the happy
path; it would not prove that any of the rules is actually enforced.

Nothing here is a route, a placement, or a copper decision.
"""

import csv
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import gen_netmap as G  # noqa: E402

EXAMPLES = os.path.join(ROOT, "examples", "rk3506_som")

TABLES = ("pinmap_rk3506j.csv", "power_balls.csv", "power_rails.csv",
          "ddr3l_signals.csv", "som_pinout_140.csv")
SYMBOLS = {"DDR": "MT41K256M16", "NAND": "W25N01GVZEIG",
           "XTAL24": "X322524MSB4SI", "XTAL32": "NX2012SA",
           "RESET": "SKRPACE010"}


def real_symbols():
    lib = os.path.join(EXAMPLES, "lib")
    return {k: G.read_symbol_pins(os.path.join(lib, v + ".kicad_sym"))
            for k, v in SYMBOLS.items()}


def nets_of(nets, cls=None):
    return sorted(n for n, v in nets.items() if cls is None or v["class"] == cls)


def terms_of(terminals, net):
    return [(d, p) for n, d, p in terminals if n == net]


class NetmapCase(unittest.TestCase):
    """Copies the frozen tables into a temp dir so a test can break one."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="netmap_")
        for name in TABLES:
            shutil.copy(os.path.join(EXAMPLES, name),
                        os.path.join(self.dir, name))
        self.symbols = real_symbols()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    # --- table editing helpers ------------------------------------------
    def read(self, name):
        with open(os.path.join(self.dir, name), newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))

    def write(self, name, rows):
        with open(os.path.join(self.dir, name), "w", newline="",
                  encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            for r in rows:
                w.writerow(r)

    def edit(self, name, mutate):
        rows = self.read(name)
        mutate(rows)
        self.write(name, rows)

    def build(self):
        return G.build(self.dir, self.symbols)

    def refused(self, *fragments):
        with self.assertRaises(G.NetmapError) as ctx:
            self.build()
        msg = str(ctx.exception)
        for frag in fragments:
            self.assertIn(frag, msg)
        return msg


class HappyPath(NetmapCase):
    def test_counts(self):
        nets, terminals, report = self.build()
        self.assertEqual(report["balls"], 332)
        self.assertEqual(report["nets"], len(nets))
        self.assertEqual(report["terminals"], len(terminals))
        self.assertEqual(report["terminals"], 593)
        self.assertEqual(report["by_class"],
                         {"ground": 1, "nc": 11, "power": 9, "signal": 153})

    def test_every_soc_ball_on_exactly_one_net(self):
        pinmap = self.read("pinmap_rk3506j.csv")
        nets, terminals, _ = self.build()
        soc = [p for n, d, p in terminals if d == "SoC"]
        self.assertEqual(len(soc), len(pinmap))
        self.assertEqual(sorted(soc), sorted(r["ball"] for r in pinmap))
        self.assertEqual(len(set(soc)), len(soc))

    def test_no_single_ended_net_except_nc_and_internal(self):
        nets, terminals, _ = self.build()
        for net in nets:
            n = len(terms_of(terminals, net))
            if nets[net]["class"] in ("nc", "internal"):
                self.assertEqual(n, 1, net)
            else:
                self.assertGreaterEqual(n, 2, net)

    def test_a15_has_no_chip_pad_and_is_a_named_no_connect(self):
        nets, terminals, _ = self.build()
        self.assertEqual(terms_of(terminals, "NC_Y17"), [("SoC", "Y17")])
        self.assertEqual(nets["NC_Y17"]["class"], "nc")
        self.assertNotIn("A15", G.DDR_SOC_TO_CHIP)
        self.assertEqual(G.DDR_SOC_TO_CHIP["A14"], "A14")

    def test_ddr_pairs_meet_on_one_net(self):
        nets, terminals, _ = self.build()
        for tok, chip in G.DDR_SOC_TO_CHIP.items():
            if tok in G.DDR_SOC_NO_CHIP_PIN or tok in ("RZQ", "VREF_CA", "VREF_DQ"):
                continue
            net = "DDR_" + tok
            self.assertIn(net, nets)
            devs = {d for d, _p in terms_of(terminals, net)}
            self.assertEqual(devs, {"SoC", "DDR"}, (tok, chip, net))

    def test_vref_is_one_node_at_half_of_ddr_rail(self):
        nets, terminals, _ = self.build()
        devs = {d for d, _p in terms_of(terminals, "+0V675_REF")}
        self.assertEqual(devs, {"DDR", "RAIL", "SoC"})
        self.assertNotIn("DDR_VREF_CA", nets)
        self.assertNotIn("DDR_VREF_DQ", nets)

    def test_zq_is_a_signal_with_two_ends(self):
        nets, terminals, _ = self.build()
        self.assertEqual(nets["DDR_ZQ"]["class"], "signal")
        self.assertEqual({d for d, _p in terms_of(terminals, "DDR_ZQ")},
                         {"SoC", "DDR"})

    def test_exported_rails_reach_the_stamp_holes(self):
        nets, terminals, _ = self.build()
        for rail in G.EXPORTED_RAILS:
            self.assertIn(rail, nets)
            self.assertIn("STAMP", {d for d, _p in terms_of(terminals, rail)})
        self.assertEqual({d for d, _p in terms_of(terminals, "+5V_IN")}, {"STAMP"})

    def test_all_ten_rails_exist(self):
        rails = [r["rail"] for r in self.read("power_rails.csv")]
        nets, _t, _r = self.build()
        self.assertEqual(len(rails), 10)
        for rail in rails:
            self.assertIn(rail, nets)

    def test_dropped_parts_name_the_32k_crystal_and_the_reason(self):
        _n, _t, report = self.build()
        parts = [d["part"] for d in report["dropped_parts"]]
        self.assertEqual(parts, ["NX2012SA (32.768 kHz)"])
        why = report["dropped_parts"][0]["why"]
        self.assertIn("N3", why)
        self.assertIn("SPI1_CSN0", why)
        # the crystal's own pads are not in the net list at all
        nets, terminals, _r = self.build()
        self.assertEqual({d for _n2, d, _p in terminals if d == "XTAL32"}, set())

    def test_receiving_side_parts_are_wired(self):
        _n, terminals, _r = self.build()
        devices = {d for _n2, d, _p in terminals}
        for dev in ("NAND", "XTAL24", "RESET"):
            self.assertIn(dev, devices)
        self.assertEqual({d for d, _p in terms_of(terminals, "FSPI_CLK")},
                         {"SoC", "NAND"})
        self.assertEqual({d for d, _p in terms_of(terminals, "RESET_N")},
                         {"SoC", "RESET", "STAMP"})


class ReverseControls(NetmapCase):
    """One break per audit; each must refuse and name what broke."""

    def test_duplicate_ball_in_pinmap(self):
        def m(rows):
            rows.append(dict(rows[0]))
        self.edit("pinmap_rk3506j.csv", m)
        self.refused("appears twice in pinmap_rk3506j.csv")

    def test_one_pad_on_two_nets(self):
        # the same stamp pin number twice: pad identity collision
        def m(rows):
            extra = [r for r in rows if r["pin"] == "3"][0]
            extra["net"] = "+0V9_CPU"
            rows.append(extra)
        self.edit("som_pinout_140.csv", m)
        self.refused("already on net")

    def test_chip_signal_pin_not_covered_by_the_name_table(self):
        saved = dict(G.DDR_SOC_TO_CHIP)
        try:
            del G.DDR_SOC_TO_CHIP["A11"]
            self.refused("is not in DDR_SOC_TO_CHIP")
        finally:
            G.DDR_SOC_TO_CHIP.clear()
            G.DDR_SOC_TO_CHIP.update(saved)

    def test_name_table_is_not_a_bijection(self):
        saved = dict(G.DDR_SOC_TO_CHIP)
        try:
            G.DDR_SOC_TO_CHIP["A0"] = "A1"
            self.refused("maps two SoC tokens onto")
        finally:
            G.DDR_SOC_TO_CHIP.clear()
            G.DDR_SOC_TO_CHIP.update(saved)

    def test_a15_put_back_into_the_name_table(self):
        saved = dict(G.DDR_SOC_TO_CHIP)
        try:
            G.DDR_SOC_TO_CHIP["A15"] = "A15"
            self.refused("has no chip pin")
        finally:
            G.DDR_SOC_TO_CHIP.clear()
            G.DDR_SOC_TO_CHIP.update(saved)

    def test_ddr_ball_exported_on_a_stamp_hole(self):
        def m(rows):
            for r in rows:
                if r["pin"] == "1":
                    r["net"] = "DDR_DQ0"
                    r["ball"] = "V23"
                    r["kind"] = "signal"
        self.edit("som_pinout_140.csv", m)
        self.refused("the DDR bus stays on the board")

    def test_ruled_ball_renamed_by_a_stamp_hole(self):
        # A1 is a VSS ball: the rail table rules it GND, a stamp row may not
        # attach a second name to it
        def m(rows):
            for r in rows:
                if r["pin"] == "3":
                    r["net"] = "LCDC_D0"
                    r["ball"] = "A1"
        self.edit("som_pinout_140.csv", m)
        self.refused("by rule")

    def test_one_ball_on_two_holes(self):
        def m(rows):
            for r in rows:
                if r["pin"] == "1":
                    r["ball"] = "F2"        # already exported on pin 2
        self.edit("som_pinout_140.csv", m)
        self.refused("one ball, one hole")

    def test_stamp_ball_unknown_to_the_pin_map(self):
        def m(rows):
            rows[0]["ball"] = "ZZ99"
        self.edit("som_pinout_140.csv", m)
        self.refused("which pinmap_rk3506j.csv does not have",
                     "which no SoC pin provides")

    def test_oscillator_ball_exported_under_another_name(self):
        def m(rows):
            for r in rows:
                if r["pin"] == "1":
                    r["net"] = "LCDC_D0"
                    r["ball"] = "H3"        # OSC_XIN: the pin name decides
        self.edit("som_pinout_140.csv", m)
        self.refused("the pin name decides")

    def test_lcdc_ball_no_longer_exported_gets_a_no_connect(self):
        # drop the stamp rows and the ball must not silently vanish: R6 makes
        # it a named no-connect instead
        def m(rows):
            rows[:] = [r for r in rows if r["net"] != "LCDC_D0"]
        self.edit("som_pinout_140.csv", m)
        nets, _t, _r = self.build()
        self.assertNotIn("LCDC_D0", nets)
        self.assertEqual(nets["NC_G3"]["class"], "nc")

    def test_rail_without_a_producing_device(self):
        def m(rows):
            for r in rows:
                if r["rail"] == "+1V8_ANA":
                    r["device"] = ""
        self.edit("power_rails.csv", m)
        self.refused("has no producing device")

    def test_rail_fed_from_a_non_rail(self):
        def m(rows):
            for r in rows:
                if r["rail"] == "+0V9_ANA":
                    r["source"] = "+5V_IN_GHOST"
        self.edit("power_rails.csv", m)
        self.refused("which is not a rail")

    def test_vref_node_is_not_half_of_the_ddr_rail(self):
        def m(rows):
            for r in rows:
                if r["rail"] == "+0V675_REF":
                    r["typ_v"] = "0.9000"
        self.edit("power_rails.csv", m)
        self.refused("must stay 2:1")

    def test_required_exported_rail_missing(self):
        def m(rows):
            rows[:] = [r for r in rows if r["rail"] != "+3V3"]
        self.edit("power_rails.csv", m)
        self.refused("required on the stamp holes")

    def test_alias_points_at_no_rail(self):
        saved = dict(G.RAIL_ALIASES)
        try:
            G.RAIL_ALIASES["3V3_OUT"] = "+3V3_GONE"
            self.refused("no such rail")
        finally:
            G.RAIL_ALIASES.clear()
            G.RAIL_ALIASES.update(saved)

    def test_ddr_power_row_must_land_on_the_ddr_rail(self):
        def m(rows):
            for r in rows:
                if r["signal"] == "VDDQ":
                    r["ball"] = "A13"       # a VSS ball
        self.edit("ddr3l_signals.csv", m)
        self.refused("but that ball is on rail")

    def test_ddr_signal_says_a_ball_that_is_on_a_rail(self):
        def m(rows):
            for r in rows:
                if r["signal"] == "A0":
                    r["ball"] = "A13"      # A13 is a VSS ball
        self.edit("ddr3l_signals.csv", m)
        self.refused("but that ball is 'VSS'")

    def test_net_end_rule_on_a_toy_net_list(self):
        nets = {"SIG": {"class": "signal", "note": ""},
                "NC_A": {"class": "nc", "note": ""},
                "INT_B": {"class": "internal", "note": ""}}
        bad = [("SIG", "U1", "1")]
        self.assertIn("net 'SIG' has 1 terminal(s): a net needs two ends",
                      "; ".join(G.audit_net_ends(nets, bad)))
        good = [("SIG", "U1", "1"), ("SIG", "U2", "1"),
                ("NC_A", "U1", "2"), ("INT_B", "U1", "3")]
        self.assertEqual(G.audit_net_ends(nets, good), [])
        nc_two = good + [("NC_A", "U2", "2")]
        self.assertIn("nc net 'NC_A' must have exactly its own pin, has 2",
                      "; ".join(G.audit_net_ends(nets, nc_two)))
        int_zero = [t for t in good if t[0] != "INT_B"]
        self.assertIn("internal net 'INT_B' must have exactly its own pin, has 0",
                      "; ".join(G.audit_net_ends(nets, int_zero)))

    def test_ddr_signal_says_an_unknown_ball(self):
        def m(rows):
            for r in rows:
                if r["signal"] == "A0":
                    r["ball"] = "ZZ99"
        self.edit("ddr3l_signals.csv", m)
        self.refused("pinmap has no such ball")

    def test_chip_side_not_supplied(self):
        with self.assertRaises(G.NetmapError) as ctx:
            G.build(self.dir, {})
        self.assertIn("no DDR symbol pins supplied", str(ctx.exception))


class SymbolPins(unittest.TestCase):
    def test_pin_counts(self):
        syms = real_symbols()
        self.assertEqual(len(syms["DDR"]), 96)
        self.assertEqual(len(syms["NAND"]), 9)
        self.assertEqual(len(syms["XTAL24"]), 4)
        self.assertEqual(len(syms["RESET"]), 4)

    def test_ddr_names_are_the_chip_vocabulary(self):
        names = {n for n, _num in real_symbols()["DDR"]}
        for want in ("A10/AP", "A12/BC#", "CAS#", "RAS#", "WE#", "CS#",
                     "CK", "CK#", "LDQS", "UDQS", "LDM", "UDM", "ZQ",
                     "VREFCA", "VREFDQ"):
            self.assertIn(want, names)
        self.assertNotIn("A15", names)

    def test_ddr_token_reads_ddr3_and_ignores_ddr2(self):
        self.assertEqual(G.ddr_token("DDR2_RASN/DDR3_A10"), "A10")
        self.assertEqual(G.ddr_token("DDR_DQ12"), "DQ12")
        self.assertIsNone(G.ddr_token("VO_LCDC_D0/UART5_RX_M1"))
        self.assertTrue(G._is_ddr_ball("DDR3_A10"))
        self.assertFalse(G._is_ddr_ball("VO_LCDC_D0"))


class Cli(unittest.TestCase):
    """main() loads the symbols from <examples>/lib, so these run on the real tree."""

    def test_writes_both_csvs(self):
        out_n = os.path.join(tempfile.mkdtemp(prefix="netmap_cli_"), "n.csv")
        out_t = os.path.join(os.path.dirname(out_n), "t.csv")
        rc = G.main(["--examples", EXAMPLES, "--out-nets", out_n,
                     "--out-terminals", out_t])
        self.assertEqual(rc, 0)
        for path, field in ((out_n, "net"), (out_t, "device")):
            with open(path, newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            self.assertTrue(rows)
            self.assertIn(field, rows[0])

    def test_refuses_without_writing_a_byte(self):
        d = tempfile.mkdtemp(prefix="netmap_bad_")
        for name in TABLES:
            shutil.copy(os.path.join(EXAMPLES, name), os.path.join(d, name))
        p = os.path.join(d, "pinmap_rk3506j.csv")
        with open(p, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        rows.append(dict(rows[0]))
        with open(p, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            for r in rows:
                w.writerow(r)
        out_n = os.path.join(d, "n.csv")
        out_t = os.path.join(d, "t.csv")
        rc = G.main(["--examples", d, "--out-nets", out_n,
                     "--out-terminals", out_t])
        self.assertEqual(rc, 2)
        self.assertFalse(os.path.exists(out_n))
        self.assertFalse(os.path.exists(out_t))


class Docstring(unittest.TestCase):
    def test_says_it_is_not_a_route(self):
        doc = G.__doc__ or ""
        self.assertIn("Nothing here is a route", doc)
        for rule in ("R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8"):
            self.assertIn(rule, doc)


if __name__ == "__main__":
    unittest.main()
