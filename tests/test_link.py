"""Golden tests for the link-budget Calc module.

The one number that must not be wrong here is the free-space constant: written as
20*log10(4*pi/c) + 180 instead of + 120 it makes every distance wrong by a factor of
1000 while looking perfectly reasonable. So 2400 MHz at 1 m = 40.05 dB is pinned
first, and the range answer is checked to invert it.
"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp.calc import link  # noqa: E402


class TestFreeSpacePathLoss(unittest.TestCase):
    def test_the_constant(self):
        # 20*log10(4*pi/c) + 120 = -27.5522 for f in MHz and d in m
        self.assertAlmostEqual(link.FSPL_CONST_DB, -27.5522, places=4)

    def test_2400_mhz_at_one_metre(self):
        # the number everyone half-remembers: about 40 dB
        self.assertAlmostEqual(link.fspl_db(2400, 1.0), 40.052, places=3)

    def test_ten_metres_is_twenty_db_more(self):
        self.assertAlmostEqual(link.fspl_db(2400, 10.0) - link.fspl_db(2400, 1.0),
                               20.0, places=6)

    def test_frequency_and_distance_both_cost_6_db_per_doubling(self):
        self.assertAlmostEqual(link.fspl_db(4800, 1.0) - link.fspl_db(2400, 1.0),
                               6.0206, places=3)
        self.assertAlmostEqual(link.fspl_db(2400, 2.0) - link.fspl_db(2400, 1.0),
                               6.0206, places=3)


class TestFriis(unittest.TestCase):
    def test_term_by_term(self):
        v = link.friis(0.0, 2400, 10.0, tx_gain_dbi=2.0, rx_gain_dbi=2.0)["value"]
        # 0 dBm + 2 + 2 - 60.05 dB of path loss
        self.assertAlmostEqual(v["prx_dbm"], -56.052, places=3)
        self.assertAlmostEqual(v["path_loss_db"], 60.052, places=3)
        self.assertAlmostEqual(v["eirp_dbm"], 2.0, places=6)
        # ERP is EIRP minus the dipole reference, 2.15 dB
        self.assertAlmostEqual(v["erp_dbm"], 2.0 - 2.15, places=3)

    def test_every_term_is_named(self):
        v = link.friis(10.0, 868, 100.0, tx_gain_dbi=1.0, rx_gain_dbi=1.0,
                       tx_loss_db=1.5, rx_loss_db=0.5, other_loss_db=3.0)["value"]
        terms = v["terms"]
        for k in ("tx_dbm", "tx_gain_dbi", "rx_gain_dbi", "tx_loss_db", "rx_loss_db",
                  "other_loss_db", "path_loss_db"):
            self.assertIn(k, terms)
        total = sum(terms.values())
        self.assertAlmostEqual(total, v["prx_dbm"], places=3)

    def test_losses_reduce_the_received_power(self):
        clean = link.friis(0.0, 2400, 10.0)["value"]["prx_dbm"]
        lossy = link.friis(0, 2400, 10.0, other_loss_db=6.0)["value"]["prx_dbm"]
        self.assertAlmostEqual(clean - lossy, 6.0, places=6)


class TestMaxRange(unittest.TestCase):
    def test_the_canonical_ble_class_budget(self):
        # 0 dBm, 2.4 GHz, -95 dBm sensitivity: 559 m in free space, and 353 m once
        # 4 dB of other loss is taken out. In a building neither is the real range,
        # and the answer says so.
        open_air = link.max_range(0.0, 2400, -95.0)["value"]
        self.assertAlmostEqual(open_air["range_m"], 558.984, places=2)
        lossy = link.max_range(0.0, 2400, -95.0, other_loss_db=4.0)["value"]
        self.assertAlmostEqual(lossy["range_m"], 352.695, places=2)
        self.assertAlmostEqual(lossy["allowed_path_loss_db"], 91.0, places=6)
        self.assertTrue(lossy["warnings"])

    def test_it_inverts_fspl(self):
        v = link.max_range(10.0, 915, -100.0, tx_gain_dbi=3.0, rx_gain_dbi=0.0)["value"]
        # feeding the range back through FSPL must reproduce the budget exactly
        budget = 10.0 + 3.0 + 0.0 - (-100.0)
        self.assertAlmostEqual(link.fspl_db(915, v["range_m"]), budget, places=6)

    def test_fade_margin_shortens_the_range(self):
        without = link.max_range(0.0, 2400, -95.0)["value"]["range_m"]
        with_margin = link.max_range(0.0, 2400, -95.0, fade_margin_db=10.0)["value"]["range_m"]
        self.assertLess(with_margin, without)
        # 10 dB of margin is a factor sqrt(10) in free space
        self.assertAlmostEqual(without / with_margin, math.sqrt(10.0), places=3)

    def test_a_bad_frequency_is_refused(self):
        self.assertEqual(link.max_range(0, 0, -95)["reason_code"], "ARG_RANGE")


class TestAperture(unittest.TestCase):
    def test_an_isotrope_at_2_4_ghz(self):
        v = link.aperture(0.0, 2400)["value"]
        lam = 0.1249135
        self.assertAlmostEqual(v["effective_aperture_m2"], lam ** 2 / (4 * math.pi),
                               places=9)

    def test_gain_raises_the_aperture(self):
        a = link.aperture(0.0, 2400)["value"]["effective_aperture_cm2"]
        b = link.aperture(10.0, 2400)["value"]["effective_aperture_cm2"]
        self.assertAlmostEqual(b / a, 10.0, places=3)


class TestFarField(unittest.TestCase):
    def test_three_criteria_are_all_reported(self):
        v = link.far_field(30.0, 2400)["value"]
        lam = 124.9135
        # 2*D^2/lambda with D = 0.03 m
        self.assertAlmostEqual(v["fraunhofer_distance_mm"], 2 * 30.0 ** 2 / lam, places=2)
        self.assertAlmostEqual(v["reactive_near_field_end_mm"], lam / (2 * math.pi),
                               places=2)
        self.assertAlmostEqual(v["five_d_mm"], 150.0, places=2)
        # the answer is the largest of them, and it names which one won
        self.assertAlmostEqual(v["far_field_starts_at_mm"], 150.0, places=2)
        self.assertEqual(v["which_criterion"], "5*D")

    def test_each_criterion_wins_somewhere(self):
        # at 2.4 GHz (lambda = 124.9 mm) the winner changes with D:
        #   D = 2 mm   -> the reactive boundary lambda/(2*pi) = 19.9 mm is the
        #                 furthest of the three
        #   D = 20 mm  -> 5*D
        #   D = 400 mm -> 2*D^2/lambda
        self.assertEqual(link.far_field(2.0, 2400)["value"]["which_criterion"],
                         "lambda/(2*pi)")
        self.assertEqual(link.far_field(20.0, 2400)["value"]["which_criterion"], "5*D")
        self.assertEqual(link.far_field(400.0, 2400)["value"]["which_criterion"],
                         "2*D^2/lambda")
        # 2*D^2/lambda overtakes 5*D at D = 2.5*lambda
        self.assertAlmostEqual(
            link.far_field(2.5 * 124.9135, 2400)["value"]["fraunhofer_distance_mm"],
            link.far_field(2.5 * 124.9135, 2400)["value"]["five_d_mm"], places=1)

    def test_size_must_be_positive(self):
        self.assertEqual(link.far_field(0.0, 2400)["reason_code"], "ARG_RANGE")


class TestFresnel(unittest.TestCase):
    def test_first_zone_at_the_midpoint_of_a_one_km_link(self):
        v = link.fresnel_radius(2400, 500.0, 500.0)["value"]
        lam = 0.1249135
        self.assertAlmostEqual(v["radius_m"], math.sqrt(lam * 250.0), delta=1e-4)
        self.assertAlmostEqual(v["clearance_60pct_m"], 0.6 * v["radius_m"], delta=1e-4)

    def test_the_zone_is_widest_in_the_middle(self):
        mid = link.fresnel_radius(2400, 500.0, 500.0)["value"]["radius_m"]
        end = link.fresnel_radius(2400, 50.0, 950.0)["value"]["radius_m"]
        self.assertGreater(mid, end)

    def test_higher_zones_are_wider(self):
        a = link.fresnel_radius(2400, 500.0, 500.0, n=1)["value"]["radius_m"]
        b = link.fresnel_radius(2400, 500.0, 500.0, n=4)["value"]["radius_m"]
        self.assertAlmostEqual(b / a, 2.0, places=3)


if __name__ == "__main__":
    unittest.main()
