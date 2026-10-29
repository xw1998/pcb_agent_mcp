"""Edge-coupled coplanar pair (0.0.24): what can be checked when no closed form exists.

There is no closed form for edge-coupled coplanar lines in this repo, so the
checks are of three different kinds and none of them is "compare with a formula":

  * the MODE DECOMPOSITION against a third solve - linear superposition says
    (C_odd + C_even)/2 equals the capacitance of one strip with its neighbour
    held at 0 V, and that identity is exact up to the solver tolerance;
  * the ANTISYMMETRY of the odd solution, which is exact by construction because
    the mesh runs through x = 0 instead of assuming the plane;
  * the MERGED-STRIP LIMIT at spacing = width/6, where the common-mode answer
    has to fall onto the single-line solver for a 2w strip - and that solver is
    itself checked against the conformal map.

The one limit that is deliberately NOT asserted is spacing -> large: there the
odd-mode impedance rises above the single-line value and must, because a widely
spaced pair has a ground plane on one side of each strip only.

The first test below also carries a NEGATIVE CONTROL: the same identity is run
on a section whose second strip is left in the unknown set, which is the exact
bug the honest implementation had to be fixed for. If that control ever stops
failing, this file is no longer testing anything.
"""

import unittest

from pcb_agent_mcp.calc import field2d
from pcb_agent_mcp.calc.field2d import _CoplanarPair, _Section

GEOM = dict(w=0.3, s=0.2, g=0.2, h=0.2, er=4.3)


def _caps(cls, kind, w, s, g, h, t, er, fine, mode):
    sec = cls(kind, w, s, g, h, t, er, fine)
    sec.build(mode)
    sec._V = sec.solve()
    return sec.charge(sec._V)


class _FreeSecondStrip(_CoplanarPair):
    """Bug 1, kept on purpose: the neighbour is left as an unknown node."""

    def build(self, mode="odd"):
        self.live = list(self.live_right)
        _Section.build(self, odd=False)
        val = {"odd": -1.0, "grounded": 0.0}.get(str(mode), 1.0)
        for n in self.live_left:
            self._fixed[n] = val
        return self


class _SignFlipped(_CoplanarPair):
    """Bug 2, kept on purpose: the odd drive forgets its minus sign.

    The neighbour IS taken out of the unknown set here - otherwise the sweep
    relaxes it and the `val` below never reaches the solution at all (measured
    on this geometry: the identity then reads 1.4e-10 and the control silently
    stops controlling anything).
    """

    def build(self, mode="odd"):
        self.live = list(self.live_right)
        _Section.build(self, odd=False)
        left = set(self.live_left)
        self._rows = [row for row in self._rows if row[0] not in left]
        self._free = [n for n in self._free if n not in left]
        val = 0.0 if str(mode) == "grounded" else 1.0
        for n in self.live_left:
            self._fixed[n] = val
        return self


class TestTheModeDecomposition(unittest.TestCase):
    def test_the_three_drives_satisfy_the_superposition_identity(self):
        chk = field2d.coplanar_pair_mode_check("cpw", GEOM["w"], GEOM["s"],
                                               GEOM["g"], GEOM["h"], 0.0,
                                               GEOM["er"], fine_div=5.0)
        # bounded by the SOR tolerance, not by machine epsilon: three sweeps
        self.assertLess(abs(chk["residual_dielectric"]), 1e-6)
        self.assertLess(abs(chk["residual_air"]), 1e-6)
        drives = chk["drives"]
        # and the drives really are three different numbers, not one number
        # printed three times (that is what the bug looked like)
        self.assertGreater(drives["odd"]["c_dielectric"],
                           drives["grounded"]["c_dielectric"])
        self.assertGreater(drives["grounded"]["c_dielectric"],
                           drives["even"]["c_dielectric"])

    def test_a_free_second_strip_collapses_the_two_modes(self):
        args = ("cpw", GEOM["w"], GEOM["s"], GEOM["g"], GEOM["h"], 0.0,
                GEOM["er"], 0.04)
        c_odd = _caps(_FreeSecondStrip, *args, mode="odd")
        c_even = _caps(_FreeSecondStrip, *args, mode="even")
        # the SOR sweep relaxes the "fixed" strip, so the drive stops mattering
        # and the two modes come out as the same number - which is exactly what
        # the honest implementation produced before the fix
        self.assertAlmostEqual(c_odd, c_even, places=9)
        # and that number is NEITHER of the honest modes: the collapse is not
        # "both modes ended up right", it is "the drive stopped mattering"
        honest = field2d.coplanar_pair_mode_check("cpw", GEOM["w"], GEOM["s"],
                                                  GEOM["g"], GEOM["h"], 0.0,
                                                  GEOM["er"], fine_div=5.0)
        c_honest_odd = honest["drives"]["odd"]["c_dielectric"]
        c_honest_even = honest["drives"]["even"]["c_dielectric"]
        self.assertLess(c_odd, 0.9 * c_honest_odd)
        self.assertGreater(c_odd, 1.1 * c_honest_even)

    def test_the_identity_catches_a_sign_flip_but_not_a_floating_strip(self):
        # Worth stating plainly, because it decides what each check is for: a
        # FLOATING neighbour satisfies the superposition identity to 1.5e-10 on
        # this geometry (measured), so the identity is blind to bug 1 - its
        # floating potential is the same linear function of the drive in all
        # three runs. What the identity does catch is a wrong potential, e.g.
        # the odd drive forgetting its minus sign.
        args = ("cpw", GEOM["w"], GEOM["s"], GEOM["g"], GEOM["h"], 0.0,
                GEOM["er"], 0.04)
        c_odd = _caps(_SignFlipped, *args, mode="odd")
        c_even = _caps(_SignFlipped, *args, mode="even")
        c_gnd = _caps(_SignFlipped, *args, mode="grounded")
        self.assertAlmostEqual(c_odd, c_even, places=9)
        self.assertGreater(abs(0.5 * (c_odd + c_even) - c_gnd) / c_gnd, 0.05)

        c_odd = _caps(_FreeSecondStrip, *args, mode="odd")
        c_even = _caps(_FreeSecondStrip, *args, mode="even")
        c_gnd = _caps(_FreeSecondStrip, *args, mode="grounded")
        self.assertLess(abs(0.5 * (c_odd + c_even) - c_gnd) / c_gnd, 1e-6)


class TestExactInvariants(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = field2d.coplanar_pair_solve("cpw", GEOM["w"], GEOM["s"],
                                                 GEOM["g"], GEOM["h"], 0.0,
                                                 GEOM["er"], levels=(1.0,))
        cls.shape = field2d.coplanar_pair_shape_factor("cpw", GEOM["w"], GEOM["s"],
                                                       GEOM["g"], GEOM["h"], 0.0,
                                                       GEOM["er"], refine=None)

    def test_the_odd_drive_puts_exactly_the_opposite_charge_on_the_other_strip(self):
        run = self.result["runs"][0]
        self.assertAlmostEqual(run["odd"]["charge_balance"], -1.0, places=9)
        self.assertAlmostEqual(run["even"]["charge_balance"], 1.0, places=9)

    def test_odd_is_below_even_and_both_are_physical(self):
        self.assertLess(self.result["z_odd"], self.result["z_even"])
        self.assertGreater(self.result["eps_eff_odd"], 1.0)
        self.assertLess(self.result["eps_eff_even"], GEOM["er"])
        # a tighter gap packs the field between the strips: filling factor goes up
        self.assertGreater(self.result["eps_eff_odd"], self.result["eps_eff_even"])

    def test_the_two_strips_and_the_two_wings_carry_identical_shares(self):
        s = self.shape
        self.assertAlmostEqual(s["strip_left_share"], s["strip_right_share"],
                               places=12)
        self.assertAlmostEqual(s["ground_share_left"], s["ground_share_right"],
                               places=12)
        self.assertEqual(s["ground_share_back"], 0.0)
        # nothing is lost in the partition
        total = (s["strip_left_share"] + s["strip_right_share"]
                 + s["ground_share_left"] + s["ground_share_right"]
                 + s["ground_share_back"])
        self.assertAlmostEqual(total, s["shape_factor_per_mm"], places=12)

    def test_a_backed_pair_returns_current_on_the_backside(self):
        backed = field2d.coplanar_pair_shape_factor("gcpw", GEOM["w"], GEOM["s"],
                                                    GEOM["g"], GEOM["h"], 0.0,
                                                    GEOM["er"], refine=None)
        self.assertGreater(backed["ground_share_back"], 0.0)
        self.assertAlmostEqual(backed["ground_share_left"],
                               backed["ground_share_right"], places=12)


class TestTheMergedStripLimit(unittest.TestCase):
    def test_common_mode_falls_onto_one_strip_of_twice_the_width(self):
        w = GEOM["w"]
        # spacing = width/6; a coarser mesh than the tool's default, because the
        # fine step follows the spacing and this is a test, not a design number
        pair = field2d.coplanar_pair_solve("cpw", w, w / 6.0, GEOM["g"],
                                           GEOM["h"], 0.0, GEOM["er"],
                                           levels=(1.0,), fine_div=3.0)
        merged, _, _ = field2d.coplanar_solve_pair("cpw", 2 * w, GEOM["g"],
                                                    GEOM["h"], 0.0, GEOM["er"],
                                                    levels=(1.0,))
        gap = 100.0 * (pair["z_common"] - merged) / merged
        # measured -0.62 %; the tolerance is loose because the two are different
        # geometries solved on different meshes, and this is a limit not a value
        self.assertLess(abs(gap), 1.5)
        # the odd mode must NOT collapse there: an infinitesimal gap between two
        # strips at +/-V is an infinite capacitance, so Z_odd goes to zero
        self.assertLess(pair["z_odd"], merged)


class TestRefusals(unittest.TestCase):
    def test_kind_must_be_a_coplanar_topology(self):
        for kind in ("stripline", "microstrip", "", None):
            with self.assertRaises(ValueError):
                field2d.coplanar_pair_solve(kind, 0.3, 0.2, 0.2, 0.2, 0.0, 4.3)
            with self.assertRaises(ValueError):
                field2d.coplanar_pair_shape_factor(kind, 0.3, 0.2, 0.2, 0.2, 0.0, 4.3)

    def test_geometry_must_be_positive(self):
        for w, s, g, h, t, er in ((0.0, 0.2, 0.2, 0.2, 0.0, 4.3),
                                  (0.3, 0.0, 0.2, 0.2, 0.0, 4.3),
                                  (0.3, 0.2, -1.0, 0.2, 0.0, 4.3),
                                  (0.3, 0.2, 0.2, 0.0, 0.0, 4.3),
                                  (0.3, 0.2, 0.2, 0.2, -0.1, 4.3),
                                  (0.3, 0.2, 0.2, 0.2, 0.0, 0.5)):
            with self.assertRaises(ValueError):
                field2d.coplanar_pair_solve("cpw", w, s, g, h, t, er)

    def test_the_node_limit_is_checked_before_the_sweeps(self):
        with self.assertRaises(ValueError) as ctx:
            field2d.coplanar_pair_solve("cpw", 0.3, 0.2, 0.2, 0.2, 0.0, 4.3,
                                        levels=(1.0,), max_nodes=200)
        self.assertIn("over the 200-node limit", str(ctx.exception))


class TestTheTool(unittest.TestCase):
    def test_envelope_and_the_identity_between_value_and_extra(self):
        r = field2d.coplanar_pair_field(0.3, 0.2, 0.2, 0.2, levels=1)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["unit"], "ohm")
        # value IS Zdiff, and Zdiff IS 2*Z_odd: not two numbers that drifted apart
        self.assertAlmostEqual(r["value"], 2.0 * r["z_odd_ohm"], places=1)
        self.assertAlmostEqual(r["z_common_ohm"], 0.5 * r["z_even_ohm"], places=2)
        self.assertEqual(r["charge_balance_odd"], -1.0)
        # the table in the answer must be the table in the module
        self.assertIn("mode_decomposition_third_solve", r["validated_against"])
        self.assertIsNone(r["conductor_db_per_100mm"])

    def test_a_non_coplanar_kind_is_an_argument_error(self):
        r = field2d.coplanar_pair_field(0.3, 0.2, 0.2, 0.2, kind="stripline")
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "ARG_INVALID")

    def test_the_node_limit_comes_back_as_out_of_validity(self):
        r = field2d.coplanar_pair_field(0.3, 0.2, 0.2, 0.2, levels=1,
                                        max_nodes=200)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "OUT_OF_VALIDITY")
        self.assertIn("200-node limit", r["message"])

    def test_asking_for_loss_adds_both_mechanisms_and_they_add_up(self):
        r = field2d.coplanar_pair_field(0.3, 0.2, 0.2, 0.2, levels=1,
                                        frequency_ghz=2.4, tan_delta=0.02)
        self.assertEqual(r["status"], "ok")
        self.assertGreater(r["conductor_db_per_100mm"], 0.0)
        self.assertGreater(r["dielectric_db_per_100mm"], 0.0)
        self.assertAlmostEqual(r["total_db_per_100mm"],
                               r["conductor_db_per_100mm"]
                               + r["dielectric_db_per_100mm"], places=3)
        self.assertIn("zero-thickness", r["conductor_note"])


if __name__ == "__main__":
    unittest.main()
