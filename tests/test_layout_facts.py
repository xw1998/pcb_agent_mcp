"""The eight catalogue facts that had no measurement, each with a control that flips it.

These rules used to answer `undetermined - no measurement supplied for 'X'`. The
measurements added here are all the kind that a finished board will punish if they
are written from intuition, so every one of them is pinned twice: once on a board
where the fact must come out one way, and once on a board that differs by exactly
the thing the rule is about and must come out the other way.

Three of the口径 in here were overturned by real boards while they were being
written, and the tests hold the overturned versions out:

* silkscreen tested against a rectangle's two given corners walks the DIAGONAL,
  which is ink that the board does not have (pic_programmer's four sockets read as
  silk-over-pad);
* "the crystal's courtyard" is not the crystal - the legacy HC18-U library draws a
  13.1 x 17.3 mm courtyard around a 4.9 mm lead pitch, so a track 5 mm from the can
  is inside it (interf_u, video);
* the crystal keep-out is not the courtyard's bounding BOX: a 45 degree track that
  clips the box corner clears the part by 0.65 mm (royalblue54L_feather).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp.adapters import kicad_pcb as K  # noqa: E402
from pcb_agent_mcp.ruleset import measure as M  # noqa: E402

_HEAD = """(kicad_pcb (version 20240108) (generator pcbnew)
  (general (thickness 1.6))
  (paper "A4")
  (layers (0 "F.Cu" signal) (31 "B.Cu" signal) (37 "F.SilkS" user)
          (44 "F.CrtYd" user) (45 "B.CrtYd" user))
  (net 0 "") (net 1 "GND") (net 2 "+3V3") (net 3 "SIG")
  (net 4 "DIF_P") (net 5 "DIF_N")
"""

# (net, name) for the net numbers above
_NETS = {1: "GND", 2: "+3V3", 3: "SIG", 4: "DIF_P", 5: "DIF_N"}


def _pad(num, x, y, w=1.0, h=1.0, net=1, layer="F.Cu", shape="rect",
         mask=True, extra=""):
    layers = '"%s"' % layer
    if mask:
        layers += ' "F.Mask"' if layer == "F.Cu" else ' "B.Mask"'
    tail = ""
    if net:
        tail = ' (net %d "%s")' % (net, _NETS.get(net, "N%d" % net))
    return ('    (pad "%s" smd %s (at %s %s) (size %s %s) (layers %s)%s%s)'
            % (num, shape, x, y, w, h, layers, tail,
               (" " + extra) if extra else ""))


def _fp(ref, lib, pads, graphics=(), layer="F.Cu", value=None):
    body = ['  (footprint "%s" (layer "%s") (at 0 0)' % (lib, layer),
            '    (property "Reference" "%s" (at 0 0 0) (layer "F.SilkS"))'
            % ref]
    if value is not None:
        body.append('    (property "Value" "%s" (at 0 0 0) (layer "F.Fab"))'
                    % value)
    body.extend(pads)
    body.extend(graphics)
    body.append("  )")
    return "\n".join(body)


def _crt_rect(sx, sy, ex, ey, w=0.05):
    return ('    (fp_rect (start %s %s) (end %s %s) (stroke (width %s)) '
            '(layer "F.CrtYd"))' % (sx, sy, ex, ey, w))


def _silk(x1, y1, x2, y2, w=0.15):
    return ('    (fp_line (start %s %s) (end %s %s) (stroke (width %s)) '
            '(layer "F.SilkS"))' % (x1, y1, x2, y2, w))


def _seg(x1, y1, x2, y2, net, width=0.2, layer="F.Cu"):
    return ('  (segment (start %s %s) (end %s %s) (width %s) (layer "%s") '
            '(net %d "%s"))' % (x1, y1, x2, y2, width, layer, net,
                                _NETS.get(net, "N%d" % net)))


def _via(x, y, net, size=0.6, drill=0.3):
    return '  (via (at %s %s) (size %s) (drill %s) (net %d "%s"))' % (
        x, y, size, drill, net, _NETS.get(net, "N%d" % net))


def _zone(net, pts, layer="F.Cu"):
    poly = " ".join("(xy %s %s)" % (x, y) for x, y in pts)
    return '\n'.join([
        '  (zone (net %d) (net_name "%s") (layer "%s") (layername "%s")'
        % (net, _NETS.get(net, "N%d" % net), layer, layer),
        '    (hatch edge 0.5) (connect_pads (clearance 0.2)) '
        '(min_thickness 0.25)',
        '    (fill yes (mode solid))',
        '    (polygon (pts %s))' % poly,
        '    (filled_polygon (layer "%s") (pts %s))' % (layer, poly),
        '  )'])


class _Board(unittest.TestCase):
    def board(self, *parts):
        return K.parse_board_text(_HEAD + "\n".join(parts) + "\n)\n")

    def fact(self, name, *parts):
        return M.measure(self.board(*parts))[name]


# --------------------------------------------------------------- crystal keep-out
def _xtal(ref="Y1", court=None, lib="X:Crystal_HC49-SMD"):
    """A crystal at the origin: two pads, and a courtyard around them. The pads sit
    at (1,2) and (3,2), so the body is x 0.5..3.5, y 1.5..2.5 - a track at x=2
    runs under it and a track at x=8 does not."""
    graphics = [c for c in court] if court else [_crt_rect(0, 0, 4, 4)]
    return _fp(ref, lib,
               [_pad("1", 1, 2, 1, 1, net=3), _pad("2", 3, 2, 1, 1, net=3)],
               graphics)


class TestTraceUnderCrystal(_Board):
    def test_a_track_across_the_body_is_a_violation(self):
        v = self.fact("trace_under_crystal", _xtal(), _seg(2, -2, 2, 8, 2))
        self.assertIs(v["value"], True)
        self.assertEqual(v["examples"][0]["net"], "+3V3")

    def test_the_same_track_outside_the_courtyard_is_clean(self):
        v = self.fact("trace_under_crystal", _xtal(), _seg(8, -2, 8, 8, 2))
        self.assertIs(v["value"], False)

    def test_a_track_on_the_far_side_of_the_board_is_not_under_a_placed_part(self):
        """royalblue54L_feather routes a signal on In4, four layers under the
        crystal, and a side-blind rule called it a keep-out violation."""
        v = self.fact("trace_under_crystal",
                      _xtal(), _seg(2, -2, 2, 8, 2, layer="B.Cu"))
        self.assertIs(v["value"], False)

    def test_a_track_outside_the_body_but_inside_a_big_library_courtyard_is_clean(self):
        """The HC18-U library draws a 13.1 x 17.3 mm courtyard; the part is 4.9 mm
        between its leads. interf_u's /PC-A7 runs 2.5 mm from a pad centre and was
        reported as running under the crystal."""
        v = self.fact("trace_under_crystal",
                      _xtal(court=[_crt_rect(-4, -5, 13, 8)]),
                      _seg(8, -2, 8, 7, 2))
        self.assertIs(v["value"], False)

    def test_a_track_that_only_clips_the_courtyard_box_corner_is_clean(self):
        """The keep-out is the drawn outline, not a box around it.

        royalblue54L_feather's Net-(U5-DEC3) leaves the box corner at 45 degrees and
        clears the part by 0.65 mm; a box test called it a violation. Here the
        courtyard is a triangle, so the box corner at (9, 9) is empty space and a
        track crossing it is outside the part.
        """
        tri = ['    (fp_line (start 0 0) (end 10 0) (stroke (width 0.05)) '
               '(layer "F.CrtYd"))',
               '    (fp_line (start 10 0) (end 0 10) (stroke (width 0.05)) '
               '(layer "F.CrtYd"))',
               '    (fp_line (start 0 10) (end 0 0) (stroke (width 0.05)) '
               '(layer "F.CrtYd"))']
        v = self.fact("trace_under_crystal", _xtal(court=tri),
                      _seg(8.0, 9.5, 9.5, 8.0, 2))
        self.assertIs(v["value"], False)

    def test_the_same_triangle_still_catches_a_track_across_its_body(self):
        tri = ['    (fp_line (start 0 0) (end 10 0) (stroke (width 0.05)) '
               '(layer "F.CrtYd"))',
               '    (fp_line (start 10 0) (end 0 10) (stroke (width 0.05)) '
               '(layer "F.CrtYd"))',
               '    (fp_line (start 0 10) (end 0 0) (stroke (width 0.05)) '
               '(layer "F.CrtYd"))']
        v = self.fact("trace_under_crystal", _xtal(court=tri),
                      _seg(2, -2, 2, 8, 2))
        self.assertIs(v["value"], True)


# ------------------------------------------------------------ thermal pad vias
def _ep(ref="U1", lib="X:QFN-16-1EP_3x3mm", n=16):
    pads = [_pad(str(i + 1), 2 + (i % 4) * 0.6, 2 + (i // 4) * 0.6, 0.3, 0.3,
                 net=2) for i in range(n)]
    pads.append(_pad("17", 30, 30, 2, 2, net=1))
    return _fp(ref, lib, pads)


class TestThermalPadVias(_Board):
    def test_an_exposed_pad_with_a_via_under_it_is_fed(self):
        v = self.fact("power_pad_has_thermal_vias",
                      _ep(), _via(30, 30, 1),
                      _zone(1, [(0, 0), (5, 0), (5, 5), (0, 5)]))
        self.assertIs(v["value"], True)
        self.assertIn("U1.17", v["where"])

    def test_the_same_pad_with_no_via_and_no_pour_under_it_is_a_failure(self):
        v = self.fact("power_pad_has_thermal_vias",
                      _ep(),
                      _zone(1, [(0, 0), (5, 0), (5, 5), (0, 5)]))
        self.assertIs(v["value"], False)
        self.assertIn("no heat path", v["where"])

    def test_a_pad_standing_in_its_own_pour_is_not_graded(self):
        """cm5_minima's DFN pad sits in the ground pour of a four-plane board with
        no via under it; the file does not say whether that is enough."""
        v = self.fact("power_pad_has_thermal_vias",
                      _ep(),
                      _zone(1, [(25, 25), (35, 25), (35, 35), (25, 35)]))
        self.assertIsNone(v.get("value"))
        self.assertIn("stand inside their own", v.get("unmeasurable") or "")

    def test_a_part_that_does_not_declare_an_exposed_pad_is_not_graded(self):
        """royalblue54L_feather has 23 large pads on poured nets - 1206 lands and
        USB-C shell tabs - and an area threshold graded every one of them."""
        v = self.fact("power_pad_has_thermal_vias",
                      _fp("J4", "X:USB-C", [_pad("1", 5, 5, 2, 3, net=1)]),
                      _zone(1, [(0, 0), (10, 0), (10, 10), (0, 10)]))
        self.assertIsNone(v.get("value"))
        self.assertIn("large pad", v.get("unmeasurable") or v.get("note") or "")


# -------------------------------------------------------------- stitching vias
class TestStitchingPitch(_Board):
    def test_the_widest_nearest_neighbour_gap_is_the_reading(self):
        v = self.fact("stitching_via_pitch_mm",
                      _via(10, 10, 1), _via(15, 10, 1), _via(50, 10, 1),
                      _zone(1, [(0, 0), (60, 0), (60, 20), (0, 20)]))
        self.assertAlmostEqual(v["value"], 35.0, places=6)

    def test_a_fence_without_an_outlier_reads_its_own_pitch(self):
        v = self.fact("stitching_via_pitch_mm",
                      _via(10, 10, 1), _via(13, 10, 1), _via(16, 10, 1),
                      _zone(1, [(0, 0), (20, 0), (20, 20), (0, 20)]))
        self.assertAlmostEqual(v["value"], 3.0, places=6)

    def test_vias_on_a_net_the_board_does_not_pour_are_not_a_fence(self):
        v = self.fact("stitching_via_pitch_mm",
                      _via(10, 10, 3), _via(13, 10, 3),
                      _zone(1, [(0, 0), (20, 0), (20, 20), (0, 20)]))
        self.assertIn("fewer than two", v["unmeasurable"])


# ---------------------------------------------------------------- differential pair
def _pair(gaps):
    """Parallel D_P/D_N segments 8 mm long, one per entry of ``gaps``.

    The gap is given edge to edge; the centreline separation is that plus one
    track width.
    """
    out = []
    for i, g in enumerate(gaps):
        x0 = i * 12.0
        out.append(_seg(x0, 0, x0 + 8, 0, 4))
        out.append(_seg(x0, 0.2 + g, x0 + 8, 0.2 + g, 5))
    return out


class TestDiffpairGap(_Board):
    def test_a_pair_held_at_one_gap_is_constant(self):
        v = self.fact("diffpair_gap_varies", *_pair([0.2] * 8))
        self.assertIs(v["value"], False)
        self.assertIn("0%", v["where"])

    def test_two_of_eight_coupled_runs_at_another_gap_is_a_variation(self):
        v = self.fact("diffpair_gap_varies", *_pair([0.2] * 6 + [0.5] * 2))
        self.assertIs(v["value"], True)
        self.assertIn("25%", v["where"])

    def test_a_pair_that_splays_at_a_pad_is_not_a_routing_mistake(self):
        """royalblue54L_feather's USB pair reads 0.23..2.14 mm on its fanout while
        the routed length is a flat 0.23; the fanout is where a pair must splay."""
        parts = _pair([0.2] * 8)
        parts.append(_fp("J1", "X:CONN",
                         [_pad("1", 4, 0, 0.3, 0.3, net=4),
                          _pad("2", 4, 6, 0.3, 0.3, net=5)]))
        parts.append(_seg(0, 0, 4, 0, 4))
        parts.append(_seg(0, 6, 4, 6, 5))
        v = self.fact("diffpair_gap_varies", *parts)
        self.assertIs(v["value"], False)


# ------------------------------------------------------------------- bulk capacitor
def _ic(ref="U2", n=6, net=1):
    return _fp(ref, "X:SOIC-8",
               [_pad(str(i + 1), 10 + i, 10, 0.6, 1.2, net=net)
                for i in range(n)])


def _cap(ref, value, net=1):
    return _fp(ref, "X:C_0603", [_pad("1", 20, 20, 0.8, 0.9, net=net),
                                 _pad("2", 22, 20, 0.8, 0.9, net=net)],
               value=value)


class TestBulkCapacitor(_Board):
    def test_an_ic_on_a_poured_rail_holding_only_100nF_is_reported(self):
        v = self.fact("ics_without_bulk_cap", _ic(), _cap("C1", "100n"),
                      _zone(1, [(0, 0), (40, 0), (40, 40), (0, 40)]))
        self.assertEqual(v["value"], 1.0)
        self.assertEqual(v["examples"], ["U2"])

    def test_a_10uF_on_the_same_rail_answers_it(self):
        v = self.fact("ics_without_bulk_cap", _ic(), _cap("C1", "100n"),
                      _cap("C2", "10uF"),
                      _zone(1, [(0, 0), (40, 0), (40, 40), (0, 40)]))
        self.assertEqual(v["value"], 0.0)

    def test_a_capacitor_on_another_rail_does_not_count(self):
        v = self.fact("ics_without_bulk_cap", _ic(), _cap("C2", "10uF", net=2),
                      _zone(1, [(0, 0), (40, 0), (40, 40), (0, 40)]))
        self.assertEqual(v["value"], 1.0)


# -------------------------------------------------------------------- test points
class TestTestPoints(_Board):
    def test_a_rail_with_nowhere_to_probe_is_reported(self):
        v = self.fact("test_points_on_rails_and_connectors",
                      _zone(1, [(0, 0), (20, 0), (20, 20), (0, 20)]))
        self.assertIs(v["value"], False)
        self.assertIn("GND", v["where"])

    def test_a_single_pad_part_on_the_rail_is_a_probe_point(self):
        v = self.fact("test_points_on_rails_and_connectors",
                      _fp("TP1", "X:TP", [_pad("1", 5, 5, 1.5, 1.5, net=1)]),
                      _zone(1, [(0, 0), (20, 0), (20, 20), (0, 20)]))
        self.assertIs(v["value"], True)

    def test_a_poured_signal_is_not_a_rail(self):
        """royalblue54L_feather pours 70 nets; treating every poured net as a rail
        reported "no test point on /Connectors/ANALOG.A0"."""
        v = self.fact("test_points_on_rails_and_connectors",
                      _zone(3, [(0, 0), (20, 0), (20, 20), (0, 20)]))
        self.assertIn("no poured net", v["unmeasurable"])


# --------------------------------------------------------------- mask expansion
class TestMaskExpansion(_Board):
    def test_a_stated_margin_is_read(self):
        v = self.fact("min_mask_expansion",
                      _fp("R1", "X:R", [_pad("1", 5, 5, 1, 1, net=1,
                                            extra="(solder_mask_margin 0.05)")]))
        self.assertAlmostEqual(v["value"], 0.05, places=6)

    def test_a_pad_that_states_nothing_reads_as_the_plotters_zero(self):
        v = self.fact("min_mask_expansion",
                      _fp("R1", "X:R", [_pad("1", 5, 5, 1, 1, net=1)]))
        self.assertAlmostEqual(v["value"], 0.0, places=6)
        self.assertIn("1 of 1", v["note"])


# -------------------------------------------------------------------- guard ring
class TestCrystalGuard(_Board):
    def test_copper_of_the_return_net_around_the_part_is_a_guard(self):
        v = self.fact("crystal_has_guard_ring", _xtal(),
                      _zone(1, [(-5, -5), (30, -5), (30, 30), (-5, 30)]))
        self.assertIs(v["value"], True)

    def test_the_same_pour_on_the_other_side_does_not_enclose_it(self):
        v = self.fact("crystal_has_guard_ring", _xtal(),
                      _zone(1, [(-5, -5), (30, -5), (30, 30), (-5, 30)],
                            layer="B.Cu"))
        self.assertIs(v["value"], False)
        self.assertIn("0 of 8", v["where"])

    def test_a_pour_that_does_not_reach_the_part_is_not_a_guard(self):
        v = self.fact("crystal_has_guard_ring", _xtal(),
                      _zone(1, [(20, 20), (30, 20), (30, 30), (20, 30)]))
        self.assertIs(v["value"], False)


# ------------------------------------------------------------------ silkscreen
class TestSilkOverPad(_Board):
    def test_a_silk_line_crossing_a_pad_is_reported(self):
        v = self.fact("silkscreen_over_pad",
                      _fp("U1", "X:QFN", [_pad("1", 5, 5, 1, 1, net=1)],
                          [_silk(3, 5, 7, 5)]))
        self.assertIs(v["value"], True)
        self.assertEqual(v["examples"][0]["pad"], "U1.1")

    def test_a_silk_line_beside_the_pad_is_clean(self):
        v = self.fact("silkscreen_over_pad",
                      _fp("U1", "X:QFN", [_pad("1", 5, 5, 1, 1, net=1)],
                          [_silk(3, 8, 7, 8)]))
        self.assertIs(v["value"], False)

    def test_a_rectangle_courtyard_drawn_as_silk_walks_its_edges_not_its_diagonal(self):
        """pic_programmer's four sockets read as silk-over-pad because an fp_rect
        was reduced to its two given corners and the pen then drew the diagonal."""
        v = self.fact("silkscreen_over_pad",
                      _fp("U1", "X:QFN", [_pad("1", 5, 5, 1, 1, net=1)],
                          ['    (fp_rect (start 0 0) (end 10 10) '
                           '(stroke (width 0.15)) (layer "F.SilkS"))']))
        self.assertIs(v["value"], False)


# ---------------------------------------------------- high-frequency bypass cap
class TestHfCapacitor(_Board):
    """The rule that a 10 uF per rail made invisible: `ics_without_bulk_cap` counts
    parts of 1 uF and up, so a board with one bulk cap on every rail and no small
    part anywhere reads as fully covered. RK3506 read exactly that way."""

    def _gnd_pour(self):
        return _zone(1, [(0, 0), (40, 0), (40, 40), (0, 40)])

    def test_a_rail_with_only_bulk_on_it_is_reported(self):
        v = self.fact("rails_without_hf_cap", _ic(net=2),
                      _cap("C1", "10uF", net=2), self._gnd_pour())
        self.assertEqual(v["value"], 1.0)
        self.assertEqual(v["examples"], ["+3V3"])
        self.assertEqual(v["rails"][0]["caps"][0]["ref"], "C1")
        self.assertAlmostEqual(v["rails"][0]["caps"][0]["farads"], 1e-05, places=12)

    def test_a_100nF_on_the_same_rail_answers_it(self):
        v = self.fact("rails_without_hf_cap", _ic(net=2),
                      _cap("C1", "10uF", net=2), _cap("C2", "100n", net=2),
                      self._gnd_pour())
        self.assertEqual(v["value"], 0.0)

    def test_a_1uF_is_not_a_bypass_part(self):
        """Control from the other side of the 220 nF line: 1 uF is bulk in this
        reading, so the rail is still bare."""
        v = self.fact("rails_without_hf_cap", _ic(net=2),
                      _cap("C1", "10uF", net=2), _cap("C2", "1uF", net=2),
                      self._gnd_pour())
        self.assertEqual(v["value"], 1.0)

    def test_a_100nF_on_another_rail_does_not_count(self):
        v = self.fact("rails_without_hf_cap", _ic(net=2),
                      _cap("C1", "10uF", net=2), _cap("C2", "100n", net=4),
                      self._gnd_pour())
        self.assertEqual(v["value"], 1.0)

    def test_the_return_net_is_never_a_rail(self):
        """Control: the largest flood is what a bypass part sits AGAINST. Counting
        it as a bare rail would report every board in the world."""
        v = self.fact("rails_without_hf_cap", _ic(net=1),
                      _cap("C9", "100n", net=3), self._gnd_pour())
        self.assertIn("unmeasurable", v)
        self.assertIn("no rail", v["unmeasurable"])

if __name__ == "__main__":
    unittest.main()
