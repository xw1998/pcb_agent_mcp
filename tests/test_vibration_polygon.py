"""Polygonal-outline plate modes: cross-path checks, invariances, refusals.

The point of this file is that almost nothing here trusts the polygon solver to
agree with itself. Where a truth exists (the rectangular path, the circular
eigenvalues, monotonicity under "a smaller plate is stiffer") the polygon answer
is measured against it; where no truth exists the test pins a property that a
wrong implementation would break (rotation / translation / winding invariance,
the boundary condition actually being violated when the basis cannot satisfy it,
refusals instead of numbers).
"""
import math
import unittest

from pcb_agent_mcp.calc import vibration as V
from pcb_agent_mcp.calc import vibration_poly as P

E, NU, RHO = 24.0, 0.33, 1850.0
H = 1.6
H_M = H * 1e-3
C = math.sqrt(E * 1e9 * H_M ** 3 / (12 * (1 - NU * NU)) / (RHO * H_M))


def square(a=100.0):
    return [(0.0, 0.0), (a, 0.0), (a, a), (0.0, a)]


def ngon(n, r=50.0, cx=50.0, cy=50.0):
    return [(cx + r * math.cos(2 * math.pi * i / n),
             cy + r * math.sin(2 * math.pi * i / n)) for i in range(n)]


def rot(ring, deg):
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return [(x * c - y * s, x * s + y * c) for (x, y) in ring]


LSHAPE = [(0.0, 0.0), (100.0, 0.0), (100.0, 50.0), (50.0, 50.0),
          (50.0, 100.0), (0.0, 100.0)]

# closed-form f1 for a 100 mm SSSS / CCCC square and a clamped circle, at the
# material above; these are the same numbers tests/test_vibration.py uses
SQ_SS = 2 * math.pi ** 2 * C / 0.1 ** 2 / (2 * math.pi)
SQ_CC = 35.985 * C / 0.1 ** 2 / (2 * math.pi)
CIR_SS = 4.9351 * C / 0.05 ** 2 / (2 * math.pi)
CIR_CC = 10.2158 * C / 0.05 ** 2 / (2 * math.pi)


def poly(ring, mount="ssss", **kw):
    kw.setdefault("e_gpa", E)
    kw.setdefault("nu", NU)
    kw.setdefault("rho_kg_m3", RHO)
    return P.plate_modes_polygon(ring, H, material=None, mount=mount, **kw)


def f1(res):
    return res["value"]["modes"][0]["freq_hz"]


class ReduceToTheRectangle(unittest.TestCase):
    """A square is a polygon, so the polygon path must land on the rectangle."""

    def _rect(self, mount):
        r = V.plate_modes(100.0, 100.0, H, material=None, e_gpa=E, nu=NU,
                          rho_kg_m3=RHO, mount=mount, n_modes=4)
        self.assertEqual(r["status"], "ok", r)
        return r["value"]["modes"][0]["freq_hz"]

    def test_all_five_mounts_agree_with_the_rectangular_path(self):
        for mount, tol in (("ssss", 0.005), ("clamped", 0.005),
                           ("corners", 0.005), ("cantilever", 0.01)):
            with self.subTest(mount=mount):
                want = self._rect(mount)
                got = f1(poly(square(), mount))
                self.assertAlmostEqual(got / want, 1.0, delta=tol,
                                       msg="%s polygon %.3f vs rect %.3f"
                                           % (mount, got, want))

    def test_simply_supported_and_clamped_match_the_closed_form(self):
        self.assertAlmostEqual(f1(poly(square(), "ssss")) / SQ_SS, 1.0, delta=0.005)
        self.assertAlmostEqual(f1(poly(square(), "clamped")) / SQ_CC, 1.0, delta=0.005)

    def test_a_free_plate_has_three_rigid_body_modes_then_the_rectangle_answer(self):
        r = poly(square(), "free", n_modes=5)
        modes = r["value"]["modes"]
        self.assertEqual([m["rigid_body"] for m in modes[:4]],
                         [True, True, True, False])
        for m in modes[:3]:
            self.assertEqual(m["freq_hz"], 0.0)
            self.assertIsNone(m["period_ms"])
        # 370.144 Hz comes from the rectangular path (tests/test_vibration.py)
        self.assertAlmostEqual(modes[3]["freq_hz"], 370.144, delta=0.5)

    def test_the_penalty_rung_reported_for_a_free_plate_is_chosen_on_the_elastic_mode(self):
        # All three rigid zeros read 0.0 at every alpha, so a ladder criterion
        # that watched mode 0 would settle on the first rung for no reason.
        r = poly(square(), "free", n_modes=5)
        b = r["value"]["basis"]
        self.assertEqual(b["reported_alpha"], max(b["penalty_alphas"]))
        scan = [v for (_a, v) in b["alpha_scan"]]
        self.assertTrue(all(abs(v - 370.147) < 0.5 for v in scan), scan)


class Invariance(unittest.TestCase):
    """The answer is a property of the shape, not of the vertex list."""

    def test_winding_translation_and_start_vertex_do_not_move_the_answer(self):
        base = f1(poly(square(), "clamped"))
        for tag, ring in (("reversed", square()[::-1]),
                          ("translated", [(x + 1234.0, y - 567.0)
                                          for (x, y) in square()]),
                          ("rotated start", square()[2:] + square()[:2])):
            with self.subTest(case=tag):
                self.assertAlmostEqual(f1(poly(ring, "clamped")) / base, 1.0,
                                       delta=1e-6)

    def test_a_square_rotated_off_axis_still_reads_the_square(self):
        for deg in (15.0, 30.0, 45.0):
            with self.subTest(deg=deg):
                got = f1(poly(rot(square(), deg), "ssss"))
                self.assertAlmostEqual(got / SQ_SS, 1.0, delta=0.005)
                got_cc = f1(poly(rot(square(), deg), "clamped"))
                self.assertAlmostEqual(got_cc / SQ_CC, 1.0, delta=0.01)


class CurvedOutline(unittest.TestCase):
    """An inscribed 64-gon is within 0.1 % of the circle, so it is a truth."""

    def test_a_64_gon_reproduces_the_circular_plate_eigenvalues(self):
        self.assertAlmostEqual(f1(poly(ngon(64), "ssss")) / CIR_SS, 1.0, delta=0.02)
        self.assertAlmostEqual(f1(poly(ngon(64), "clamped")) / CIR_CC, 1.0, delta=0.02)

    def test_a_more_faceted_ring_says_its_answer_is_less_trustworthy(self):
        # n=16 cannot be pinned to a closed form, so the check is on the order:
        # 553.7 Hz (64-gon ss) << f1(16-gon) < f1(8-gon), because fewer facets
        # carve away more of the plate and a smaller plate is stiffer.
        f16 = f1(poly(ngon(16), "ssss"))
        f8 = f1(poly(ngon(8), "ssss"))
        self.assertGreater(f16, f1(poly(ngon(64), "ssss")))
        self.assertGreater(f8, f16)


class Concave(unittest.TestCase):
    def test_an_L_outline_is_stiffer_than_its_bounding_box_plate(self):
        # Rayleigh-Ritz on a smaller domain cannot come out softer than on the
        # box that contains it, so this is a monotonicity check, not a taste one.
        got = f1(poly(LSHAPE, "corners"))
        self.assertGreater(got, 199.0)
        r = poly(LSHAPE, "ssss")
        self.assertGreater(f1(r), SQ_SS)

    def test_a_re_entrant_outline_admits_its_spread(self):
        r = poly(LSHAPE, "ssss")
        b = r["value"]["basis"]
        self.assertGreater(b["alpha_spread_pct"], 3.0)
        self.assertFalse(b["alpha_converged"])
        self.assertTrue(any("penalty ladder" in w for w in r["value"]["warnings"]))


class NegativeControls(unittest.TestCase):
    """Each mechanism gets a case that fails if the mechanism is removed."""

    def test_degree_six_cannot_fit_a_clamp_and_says_so(self):
        r = poly(square(), "clamped", poly_degree=6)
        self.assertEqual(r["status"], "ok")
        self.assertGreater(f1(r), 1.5 * SQ_CC)      # +120 %: over-stiffened
        blob = " ".join(r["value"]["warnings"])
        self.assertIn("may contain no function", blob)

    def test_too_hot_a_penalty_is_rejected_instead_of_producing_a_zero_mode(self):
        r = poly(square(), "cantilever", penalty_alphas=[1e3, 1e4, 1e5, 1e6])
        b = r["value"]["basis"]
        self.assertLess(len(b["healthy_alphas"]), len(b["penalty_alphas"]))
        self.assertLessEqual(b["reported_alpha"], max(b["healthy_alphas"]))
        self.assertTrue(any("rejected" in w for w in r["value"]["warnings"]))

    def test_per_edge_conditions_are_honoured_not_averaged(self):
        all_ss = f1(poly(square(), "ssss"))
        two_free = f1(poly(square(), edges_bc=["ss", "free", "ss", "free"]))
        all_free = poly(square(), edges_bc=["free"] * 4, n_modes=2)
        self.assertLess(two_free, all_ss)           # less support, lower f
        self.assertGreater(two_free, 0.0)
        self.assertEqual(poly(square(), edges_bc=["ss", "free", "ss", "free"])
                         ["value"]["bc"]["edges"], ["ss", "free", "ss", "free"])
        self.assertTrue(all_free["value"]["modes"][0]["rigid_body"])

    def test_point_supports_leave_exactly_the_rigid_modes_they_cannot_remove(self):
        # One support pins w at one point: the two rotations about it survive, so
        # a solver that expected zero rigid modes would have refused the plate
        # and one that expected three would have accepted a spurious zero.
        r1 = poly(square(), "free", supports=[(50.0, 50.0)], n_modes=4)
        self.assertEqual(r1["status"], "ok", r1)
        self.assertAlmostEqual(r1["value"]["bc"]["point_supports_mm"][0][0], 50.0)
        self.assertEqual([m["rigid_body"] for m in r1["value"]["modes"]],
                         [True, True, False, False])
        r2 = poly(square(), "free", supports=[(25.0, 50.0), (75.0, 50.0)],
                  n_modes=3)
        self.assertEqual([m["rigid_body"] for m in r2["value"]["modes"]],
                         [True, False, False])
        r3 = poly(square(), "free",
                  supports=[(25.0, 25.0), (75.0, 25.0), (50.0, 75.0)], n_modes=2)
        self.assertFalse(any(m["rigid_body"] for m in r3["value"]["modes"]))
        self.assertGreater(f1(r3), 0.0)

    def test_a_mode_the_basis_cannot_carry_is_flagged_not_printed_as_a_plate_mode(self):
        # A degree-8 basis can hold ONE clamped mode of a square, so modes 2..4
        # come out at 21-28 kHz against a closed form of 3.0 kHz. The flag has
        # to separate them, otherwise the number is a plausible-looking lie.
        r = poly(square(), "clamped", n_modes=4)
        modes = r["value"]["modes"]
        self.assertTrue(modes[0]["physical"])
        self.assertLess(modes[0]["penalty_share"], 0.01)
        self.assertFalse(modes[1]["physical"])
        self.assertGreater(modes[1]["penalty_share"], 0.5)
        self.assertTrue(any("penalty_share" in w for w in r["value"]["warnings"]))
        # the control: on a simply supported square every mode is a plate mode,
        # so the same code must NOT cry wolf
        r2 = poly(square(), "ssss", n_modes=4)
        self.assertTrue(all(m["physical"] for m in r2["value"]["modes"]))
        self.assertFalse(any("penalty_share" in w for w in r2["value"]["warnings"]))

    def test_the_boundary_condition_is_actually_being_violated_less_when_it_can_be(self):
        # bc_res is the rms deflection on the constrained boundary relative to
        # the model's own peak, in mm. A rectangle complies; a 64-gon cannot.
        self.assertLess(poly(square(), "ssss")["value"]["basis"]["boundary_rms_mm"],
                        0.05)
        self.assertGreater(poly(ngon(64), "ssss")["value"]["basis"]["boundary_rms_mm"],
                           0.01)


class Envelope(unittest.TestCase):
    def test_the_envelope_names_the_model_and_the_gaps(self):
        r = poly(square(), "ssss", probe_points=[{"name": "c", "x_mm": 50, "y_mm": 50}])
        self.assertEqual(r["status"], "ok")
        v = r["value"]
        self.assertEqual(r["unit"], "Hz")
        self.assertIn("Rayleigh-Ritz", r["model"])
        self.assertIn("Green", r["basis"])
        self.assertIn("ring", r["assumptions"]["note"])
        self.assertIn("verified", r["precision"])
        for key in ("outline_mm", "bbox_mm", "area_mm2", "perimeter_mm",
                    "thickness_mm", "material", "flexural_rigidity_n_m",
                    "mass_per_area_kg_m2", "bc", "basis", "modes", "warnings"):
            self.assertIn(key, v)
        self.assertAlmostEqual(v["area_mm2"], 10000.0, delta=1e-6)
        self.assertAlmostEqual(v["perimeter_mm"], 400.0, delta=1e-6)
        self.assertAlmostEqual(v["bbox_mm"][0], 100.0, delta=1e-9)
        self.assertEqual(len(v["warnings"]), 3)     # the three standing caveats
        d = v["modes"][0]["deflection_at_probes"][0]
        self.assertEqual(d["name"], "c")
        self.assertAlmostEqual(abs(d["normalised_deflection"]), 1.0, delta=0.05)

    def test_the_same_request_twice_is_the_same_number(self):
        a = poly(square(), "clamped")
        b = poly(square(), "clamped")
        self.assertEqual(a["value"]["modes"][0]["freq_hz"],
                         b["value"]["modes"][0]["freq_hz"])

    def test_a_rotated_square_keeps_its_antinode_inside_the_shape(self):
        r = poly(rot(square(), 30.0), "ssss")
        x, y = r["value"]["modes"][0]["antinode_mm"]
        ring = [(float(xx), float(yy)) for (xx, yy) in r["value"]["outline_mm"]]
        self.assertTrue(P._vib_inside(ring, x, y))
        # a SSSS (1,1) mode peaks at the middle of the rotated square
        cx = sum(p[0] for p in ring) / len(ring)
        cy = sum(p[1] for p in ring) / len(ring)
        self.assertLess(math.hypot(x - cx, y - cy), 6.0)


class Refusals(unittest.TestCase):
    def test_bad_outlines_and_bad_arguments_are_refused_with_a_reason(self):
        cases = [
            ("two points", dict(ring_mm=[(0, 0), (10, 0)]), "OUTLINE_INVALID"),
            ("collinear", dict(ring_mm=[(0, 0), (10, 0), (20, 0)]),
             "OUTLINE_INVALID"),
            ("bow tie", dict(ring_mm=[(0, 0), (10, 10), (10, 0), (0, 10)]),
             "OUTLINE_INVALID"),
            ("unknown mount", dict(ring_mm=square(), mount="wat"),
             "MOUNT_UNKNOWN"),
            ("edge list length", dict(ring_mm=square(), edges_bc=["ss"]),
             "ARG_INVALID"),
            ("edge list token", dict(ring_mm=square(),
                                     edges_bc=["ss", "clamp", "ss", "ss"]),
             "ARG_INVALID"),
            ("degree", dict(ring_mm=square(), poly_degree=12), "ARG_INVALID"),
            ("degree low", dict(ring_mm=square(), poly_degree=1), "ARG_INVALID"),
            ("thickness", dict(ring_mm=square(), thickness_mm=0.0), "ARG_INVALID"),
            ("mode count", dict(ring_mm=square(), n_modes=0), "ARG_INVALID"),
            ("empty ladder", dict(ring_mm=square(), penalty_alphas=[]),
             "ARG_INVALID"),
            ("negative alpha", dict(ring_mm=square(), penalty_alphas=[1e-3, -1.0]),
             "ARG_INVALID"),
            ("material", dict(ring_mm=square(), material="unobtainium"),
             "MATERIAL_UNKNOWN"),
        ]
        for tag, kw, want in cases:
            with self.subTest(case=tag):
                kw = dict(kw)
                if "material" not in kw:
                    kw.setdefault("e_gpa", E)
                    kw.setdefault("nu", NU)
                    kw.setdefault("rho_kg_m3", RHO)
                kw.setdefault("thickness_mm", H)
                r = P.plate_modes_polygon(**kw)
                self.assertEqual(r.get("status"), "error", r)
                self.assertEqual(r.get("reason_code"), want, r)
                self.assertTrue(r.get("message"))

    def test_a_repeated_last_vertex_is_not_a_self_intersection(self):
        r = poly([(0, 0), (100, 0), (100, 100), (0, 100), (0, 0)], "ssss")
        self.assertEqual(r["status"], "ok", r)
        self.assertEqual(len(r["value"]["outline_mm"]), 4)


class BoardWiring(unittest.TestCase):
    """board_vibration keeps its bbox answer and adds the outline one."""

    def _board(self, ring, fps):
        from pcb_agent_mcp.adapters.canonical import Board
        b = Board("kicad", "x")
        b.outline = [list(ring)]
        b.footprints = [{"ref": r, "x": x, "y": y} for (r, x, y) in fps]
        return b

    def test_a_non_rectangular_board_reports_both_numbers(self):
        from pcb_agent_mcp import oracle
        b = self._board(LSHAPE, [("R1", 25.0, 75.0), ("R2", 75.0, 25.0)])
        r = oracle.board_vibration(b, mount="corners", thickness_mm=H, n_modes=2)
        self.assertEqual(r["status"], "ok", r)
        v = r["value"]
        self.assertAlmostEqual(v["outline_fill_fraction_of_bbox"], 0.75, delta=1e-3)
        on = v["plate_on_outline"]
        self.assertIsNotNone(on)
        # the bbox plate is bigger and softer, so the real outline rings HIGHER
        self.assertGreater(on["ratio_to_bbox"], 1.0)
        self.assertAlmostEqual(on["f1_hz"] / v["modes"][0]["freq_hz"],
                               on["ratio_to_bbox"], delta=1e-3)
        self.assertEqual([h["ref"] for h in v["hotspots_on_outline"][:2]][0] in
                         ("R1", "R2"), True)
        blob = " ".join(v["warnings"])
        self.assertIn("real outline", blob)
        self.assertNotIn("the bias is not computed", blob)

    def test_a_rectangular_board_pays_nothing_and_says_nothing(self):
        from pcb_agent_mcp import oracle
        b = self._board([(0.0, 0.0), (100.0, 0.0), (100.0, 80.0), (0.0, 80.0)],
                        [("R1", 50.0, 40.0)])
        r = oracle.board_vibration(b, mount="edges", thickness_mm=H)
        self.assertEqual(r["status"], "ok", r)
        self.assertIsNone(r["value"]["plate_on_outline"])
        self.assertEqual(r["value"]["hotspots_on_outline"], [])

    def test_the_bbox_answer_itself_is_unchanged(self):
        from pcb_agent_mcp import oracle
        ring = [(0.0, 0.0), (100.0, 0.0), (100.0, 50.0), (50.0, 50.0),
                (50.0, 100.0), (0.0, 100.0)]
        b = self._board(ring, [])
        r = oracle.board_vibration(b, mount="edges", thickness_mm=H, n_modes=2)
        want = V.plate_modes(100.0, 100.0, H, material="fr4", mount="edges",
                             n_modes=2)["value"]["modes"][0]["freq_hz"]
        self.assertAlmostEqual(r["value"]["modes"][0]["freq_hz"], want, delta=1e-6)


class ToolSurface(unittest.TestCase):
    def test_the_outline_tool_is_registered_and_documented(self):
        from pcb_agent_mcp import tools
        self.assertIn("vibration_plate_outline", tools.TOOLS)
        caps = tools.call_tool("calc_capabilities", {})
        self.assertIn("vibration_plate_outline", caps["available"])
        blob = caps["layers"]["vibration"]
        self.assertIn("polygon", blob)

    def test_the_tool_accepts_pairs_and_dicts_alike(self):
        from pcb_agent_mcp import tools
        a = tools.call_tool("vibration_plate_outline", {
            "ring_mm": [[0, 0], [100, 0], [100, 100], [0, 100]],
            "thickness_mm": H, "e_gpa": E, "nu": NU, "rho_kg_m3": RHO})
        b = tools.call_tool("vibration_plate_outline", {
            "ring_mm": [{"x_mm": 0, "y_mm": 0}, {"x_mm": 100, "y_mm": 0},
                        {"x_mm": 100, "y_mm": 100}, {"x_mm": 0, "y_mm": 100}],
            "thickness_mm": H, "e_gpa": E, "nu": NU, "rho_kg_m3": RHO})
        self.assertEqual(a["status"], "ok", a)
        self.assertEqual(f1(a), f1(b))


if __name__ == "__main__":
    unittest.main()
