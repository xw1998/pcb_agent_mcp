"""Antenna geometry for printed and structural antennas.

Scope, stated up front because the alternative is a plausible number that is
quietly wrong: this module answers *how big*, *what impedance does it present*,
*how much bandwidth can it possibly have* and *what must the board leave empty*.
It does NOT answer *what does the pattern look like*, *what is the efficiency on
this FR4*, *how much does the battery/H-bridge/hand detune it*, or *what is the
mutual coupling to the other antenna*. Those need a full-wave solver, and every
answer here carries `not_computed` listing them so a downstream reader cannot
mistake a dimension for a simulation.

The models, and how far each one can be trusted:

  * rectangular microstrip patch - the transmission-line model (Balanis). Size to
    better than a few percent, edge resistance and inset depth as a starting
    point, Q and bandwidth from the three loss mechanisms. This is the only
    structure here where the closed form is genuinely predictive, because a patch
    is a resonator with a well-defined mode.
  * printed wire (IFA / meander / printed monopole) - quarter-wave resonance. The
    one hard part is the effective permittivity of a thin printed conductor: it is
    somewhere between 1 and (er+1)/2 and no closed form in this repo can say where.
    So the answer is a RANGE with both ends shown, and the tool says so instead of
    picking a midpoint and looking confident.
  * dipole / monopole / helix - textbook (Kraus) results in free space, valid and
    well-tested, but they are free-space answers: put them next to a battery and
    they move.
  * small loop - the exact small-loop radiation resistance, with its validity
    limit (circumference < 0.1 lambda) enforced as a refusal rather than a
    warning, because outside it the formula does not degrade gracefully.
  * bandwidth ceiling - the Chu/Harrington limit for a sphere of radius a. This is
    the useful one when someone asks for a 2.4 GHz antenna in a 10 mm box: the
    limit says what is impossible, and impossible is worth knowing before layout.
"""
import math

from .result import ok, err
from .feedline import _microstrip
from .rf import C0_MM_PER_NS

MU0 = 1.25663706212e-6
SIGMA_CU = 5.8e7

# What no answer in this module includes. Repeated verbatim in every envelope so a
# caller that only reads one reply still sees it.
NOT_COMPUTED = [
    "radiation pattern / directivity shape (needs full-wave)",
    "radiation efficiency and gain on a real substrate with a real ground plane",
    "detuning by the enclosure, the hand, the battery or nearby metal",
    "mutual coupling between two antennas on the same board",
    "SAR and the human-body interaction",
    "impedance bandwidth after matching, beyond the physical ceiling below",
]

def _lam0(freq_ghz):
    return C0_MM_PER_NS / float(freq_ghz)

def _guard_patch(freq_ghz, er, h, t):
    if freq_ghz <= 0 or h <= 0 or er < 1.0 or t < 0:
        return err("freq_ghz and height must be > 0; er >= 1; copper >= 0",
                   "ARG_RANGE")
    return None

# ------------------------------------------------------------------- patch
def patch_rectangular(freq_ghz, er, height_mm, copper_um=35.0, tan_delta=None,
                      target_rin=50.0, ground_margin_h=None):
    """Rectangular microstrip patch: size, edge resistance, inset, Q, bandwidth.

    Transmission-line model (Balanis, Antenna Theory ch.14):

        W    = lambda0/2 * sqrt(2/(er+1))
        eps  = effective permittivity of a microstrip of width W  (feedline module)
        dL   = 0.412*h*(eps+0.3)*(W/h+0.264) / ((eps-0.258)*(W/h+0.8))
        L    = lambda0/(2*sqrt(eps)) - 2*dL

    The two fringing slots at the radiating edges are the reason L is shorter than
    half a guided wavelength by 2*dL; a patch designed without dL resonates high.

    Input resistance at the radiating edge:

        Rin(edge) = 90 * er^2/(er-1) * (L/W)^2

    and the inset feed taps it down as Rin(y) = Rin(edge)*cos^2(pi*y/L).

    Q:
        Q_rad = lambda0*sqrt(eps)/(4*h)      Q_c = h*sqrt(pi*f*mu*sigma)
        Q_d   = 1/tan_delta                  1/Q = 1/Q_rad + 1/Q_c + 1/Q_d
        BW(VSWR<S) = (S-1)/(Q*sqrt(S))

    A patch is a high-Q resonator, which is why the bandwidth is a few percent and
    why the number below is a ceiling rather than a promise.
    """
    try:
        f = float(freq_ghz)
        er = float(er)
        h = float(height_mm)
        t = float(copper_um or 0.0) / 1000.0
        target = float(target_rin)
    except (TypeError, ValueError):
        return err("numeric arguments required", "ARG_INVALID")
    e = _guard_patch(f, er, h, t)
    if e:
        return e
    if target <= 0:
        return err("target_rin must be > 0", "ARG_RANGE")

    lam0 = _lam0(f)
    W = lam0 / 2.0 * math.sqrt(2.0 / (er + 1.0))
    eps = _microstrip(W, h, t, er)[1]         # effective permittivity of a
                                             # microstrip of this width
    if W / h < 0.05:
        return err("the patch is too narrow relative to the substrate for the "
                   "transmission-line model (W/h < 0.05): use a thicker substrate",
                   "OUT_OF_VALIDITY")
    dL = (0.412 * h * (eps + 0.3) * (W / h + 0.264)
          / ((eps - 0.258) * (W / h + 0.8)))
    L = lam0 / (2.0 * math.sqrt(eps)) - 2.0 * dL
    if L <= 0:
        return err("degenerate patch: the fringing correction exceeds the resonant "
                   "length", "OUT_OF_VALIDITY")

    rin_edge = 90.0 * er * er / (er - 1.0) * (L / W) ** 2
    out = {
        "width_mm": round(W, 3),
        "length_mm": round(L, 3),
        "eps_eff": round(eps, 4),
        "fringing_dL_mm": round(dL, 4),
        "lambda_g_mm": round(lam0 / math.sqrt(eps), 4),
        "rin_at_radiating_edge_ohm": round(rin_edge, 2),
        "patch_area_mm2": round(W * L, 2),
    }
    # inset feed: nothing to solve when the edge resistance is already below target
    if rin_edge > target:
        y0 = L / math.pi * math.acos(min(1.0, math.sqrt(target / rin_edge)))
        out["inset_depth_mm"] = round(y0, 3)
        out["inset_note"] = ("measured from the radiating edge; the feed point must be "
                             "inside the patch and the inset slot width is a separate "
                             "matching variable this model does not carry")
    else:
        out["inset_depth_mm"] = None
        out["inset_note"] = ("the edge resistance %.1f ohm is already at or below the "
                             "target %.1f ohm: no inset can raise it, so the match has "
                             "to be done in the feed line instead"
                             % (rin_edge, target))

    q_rad = lam0 * math.sqrt(eps) / (4.0 * h)
    q_c = h * math.sqrt(math.pi * f * 1e9 * MU0 * SIGMA_CU)
    out["q"] = {"rad": round(q_rad, 2), "conductor": round(q_c, 1)}
    inv = 1.0 / q_rad + 1.0 / q_c
    if tan_delta is not None:
        try:
            td = float(tan_delta)
        except (TypeError, ValueError):
            return err("tan_delta must be numeric", "ARG_INVALID")
        if td < 0:
            return err("tan_delta must be >= 0", "ARG_RANGE")
        if td > 0:
            out["q"]["dielectric"] = round(1.0 / td, 1)
            inv += td
        out["radiation_efficiency_est"] = round(1.0 / (inv * q_rad), 3)
    q_total = 1.0 / inv
    out["q"]["total"] = round(q_total, 2)
    bw = {}
    for s in (1.5, 2.0):
        frac = (s - 1.0) / (q_total * math.sqrt(s))
        bw["vswr_lt_%.1f" % s] = {
            "fractional_pct": round(100.0 * frac, 2),
            "band_mhz": round(1000.0 * f * frac, 1),
        }
    out["bandwidth"] = bw
    if ground_margin_h is not None:
        out["ground_margin_mm"] = round(float(ground_margin_h) * h, 3)
    else:
        out["ground_margin_mm"] = round(6.0 * h, 3)
        out["ground_margin_note"] = ("the usual 6*h beyond the patch on every side "
                                     "(fringing needs substrate and ground); a smaller "
                                     "ground pulls the resonant frequency down")

    return ok(
        value=out,
        unit="mm",
        model="rectangular_patch_transmission_line",
        basis=("Balanis transmission-line model: W = lambda0/2*sqrt(2/(er+1)), "
               "dL = 0.412h(eps+0.3)(W/h+0.264)/((eps-0.258)(W/h+0.8)), "
               "L = lambda0/(2*sqrt(eps)) - 2dL; Rin(edge) = 90*er^2/(er-1)*(L/W)^2; "
               "1/Q = 1/Q_rad+1/Q_c+1/Q_d; BW = (S-1)/(Q*sqrt(S))"),
        precision=("size: a few percent - the model is accurate enough to cut the first "
                   "prototype, not to skip tuning. Feed point and bandwidth are "
                   "first-order: the inset model assumes a single resonant mode and no "
                   "slot, and the Q sum ignores radiation from the feed and the finite "
                   "ground. Efficiency needs tan_delta and still ignores surface waves"),
        assumptions={
            "freq_ghz": f, "er": er, "height_mm": h, "copper_um": (t * 1000.0),
            "tan_delta": tan_delta, "target_rin_ohm": target,
            "dominant_mode": "TM10 (the long dimension L is the resonant one)",
        },
        extra={"not_computed": NOT_COMPUTED},
    )

# --------------------------------------------------------------- wire antennas
def printed_wire(freq_ghz, er, height_mm, shape="ifa", target_rin=50.0,
                 bbox_mm=None, wire_width_mm=0.2):
    """Quarter-wave printed antennas: IFA, meander, printed monopole.

    The length question has exactly one honest answer: a RANGE.

    A thin printed conductor on a substrate is loaded by a dielectric that only
    partly surrounds it, so its effective permittivity sits between 1 (all field in
    air) and (er+1)/2 (half the field in the substrate, the usual "average"
    approximation). Nobody can pick the value from a closed form - it depends on
    the line width, the substrate thickness and how close the ground plane is.
    Therefore:

        L_max = lambda0/4                    (er_eff = 1, all air)
        L_min = lambda0/(4*sqrt((er+1)/2))   (er_eff = (er+1)/2)

    and the true length is between them. The free-space end is the better guess on
    a thin substrate (1.6 mm FR4 at 2.4 GHz lands near lambda0/4, not near the
    lower end); the tool says that too rather than hiding it.

    For the IFA the feed tap is modelled as a tapped quarter-wave resonator,
    Rin(d) = R_rad / cos^2(pi*d/(2L)) with R_rad = 36.5 ohm (a quarter wave over a
    ground plane). That model is lossless and ignores the capacitive loading a real
    element carries, so it over-estimates the tap distance - the note says so.
    """
    shape = str(shape or "").strip().lower()
    if shape not in ("ifa", "meander", "monopole"):
        return err("shape must be ifa, meander or monopole", "ARG_INVALID")
    try:
        f = float(freq_ghz)
        er = float(er)
        h = float(height_mm)
        wire_width = float(wire_width_mm)
        target = float(target_rin)
    except (TypeError, ValueError):
        return err("numeric arguments required", "ARG_INVALID")
    if f <= 0 or er < 1.0 or h <= 0 or target <= 0:
        return err("freq_ghz, height_mm and target_rin must be > 0; er >= 1",
                   "ARG_RANGE")
    if wire_width <= 0:
        return err("wire_width_mm must be > 0", "ARG_RANGE")

    lam0 = _lam0(f)
    er_mid = (er + 1.0) / 2.0
    l_max = lam0 / 4.0
    l_min = lam0 / (4.0 * math.sqrt(er_mid))
    quarter = 36.5                      # radiation resistance of a quarter wave
    out = {
        "shape": shape,
        "total_length_mm": {"max_all_air": round(l_max, 3),
                            "min_half_dielectric": round(l_min, 3)},
        "suggested_start_mm": round(l_max if h <= 2.0 else (l_max + l_min) / 2.0, 3),
        "suggested_start_why": ("on a thin substrate (<= 2 mm) practice lands near the "
                                "free-space quarter wave, so that end is the better "
                                "first prototype; the geometric middle is used only "
                                "when the substrate is thick"),
        "lambda0_mm": round(lam0, 3),
        "quarter_wave_free_space_mm": round(lam0 / 4.0, 3),
    }
    # feed tap, IFA only
    if shape == "ifa":
        taps = []
        for frac in (0.02, 0.05, 0.1, 0.2, 0.3, 0.5):
            d = frac * l_max
            rin = quarter / (math.cos(math.pi * d / (2.0 * l_max)) ** 2)
            taps.append({"offset_mm": round(d, 3), "fraction_of_L": frac,
                         "rin_ohm": round(rin, 1)})
        # invert for the target
        if target < quarter:
            out["tap_for_target_mm"] = 0.0
            out["tap_note"] = ("target is below the 36.5 ohm of a quarter wave: feeding "
                               "at the short (tap = 0) is the lowest resistance this "
                               "model offers, so the match needs a matching network")
        else:
            ratio = min(1.0, math.sqrt(quarter / target))
            d = 2.0 * l_max / math.pi * math.acos(ratio)
            out["tap_for_target_mm"] = round(d, 3)
            out["tap_note"] = ("tapped quarter-wave estimate with no dielectric or "
                               "ground-plane loading: a real printed element usually "
                               "matches at a SHORTER tap than this. Start here and move "
                               "toward the short")
        out["tap_table"] = taps
    if shape == "meander":
        # A meander is the only shape here whose LENGTH is not free: it has to fit
        # inside a box, and the box decides how much wire can exist at all. So the
        # answer is "this much wire fits / it does not", never a length that the
        # box cannot hold.
        if not bbox_mm:
            return err("a meander needs bbox_mm = [length, width] to say how many "
                       "turns fit", "ARG_REQUIRED")
        try:
            bl, bw = float(bbox_mm[0]), float(bbox_mm[1])
        except (TypeError, ValueError, IndexError):
            return err("bbox_mm must be [length, width]", "ARG_INVALID")
        if bl <= 0 or bw <= 0:
            return err("bbox_mm entries must be > 0", "ARG_RANGE")
        # turns are packed across the WIDTH: each pitch costs one trace plus one
        # gap, and each turn contributes one pass along the LENGTH plus the
        # connector along the width
        gap = 0.5
        pitch = wire_width + gap
        turns = max(1, int((bw + gap) // pitch))
        wire = turns * bl + max(0, turns - 1) * bw
        out["meander"] = {
            "turns_that_fit": turns,
            "bbox_mm": [bl, bw],
            "trace_width_assumed_mm": wire_width,
            "pitch_assumed_mm": round(pitch, 3),
            "wire_length_mm": round(wire, 2),
            "wire_needed_mm": out["total_length_mm"],
            "fits": wire >= out["suggested_start_mm"],
            "fits_without_meandering": max(bl, bw) >= out["suggested_start_mm"],
            "fits_against": ("suggested_start_mm (the free-space quarter wave, the "
                             "longer end of the needed range) - if it does not fit "
                             "against that number it certainly does not fit against "
                             "the shorter, dielectric-loaded end either"),
            "note": ("a meander buys length with radiation resistance and bandwidth: "
                     "the turns cancel each other's current, so an element that fits "
                     "only by meandering is a narrowband, low-efficiency radiator - "
                     "the ceiling below is the number to look at, not the length"),
        }
    # the physical ceiling this geometry can never beat
    if bbox_mm:
        a = max(float(bbox_mm[0]), float(bbox_mm[1])) / 2.0 / 1000.0
    else:
        a = (l_max / 2.0) / 1000.0
    ka = 2.0 * math.pi * a / (lam0 / 1000.0)
    q_min = 1.0 / (ka ** 3) + 1.0 / ka if ka > 0 else float("inf")
    out["bandwidth_ceiling"] = {
        "enclosing_sphere_radius_mm": round(a * 1000.0, 2),
        "ka": round(ka, 3),
        "q_min_chu": round(q_min, 3),
        "max_bandwidth_vswr_lt_2_pct": round(100.0 * 0.7071 / q_min, 2),
        "note": ("Chu/Harrington: Q >= 1/(ka)^3 + 1/(ka) for any antenna inside that "
                 "sphere. This is a hard ceiling on bandwidth, not an estimate of what "
                 "you will get - a real printed element lands well below it"),
    }
    return ok(
        value=out,
        unit="mm",
        model="printed_quarter_wave_" + shape,
        basis=("quarter-wave resonance L = lambda0/(4*sqrt(er_eff)) with er_eff bracketed "
               "between 1 and (er+1)/2 because the dielectric loading of a thin printed "
               "conductor has no closed form here; IFA tap from the tapped quarter-wave "
               "model Rin = R_rad/cos^2(pi*d/(2L)); bandwidth ceiling from the "
               "Chu/Harrington limit"),
        precision=("length: a range, not a number - expect to tune by 10-20 % on the "
                   "first prototype. Tap: first order, biased long (see tap_note). "
                   "Ceiling: physical bound, exact for a lossless antenna in that sphere"),
        assumptions={"freq_ghz": f, "er": er, "height_mm": h,
                     "target_rin_ohm": target, "shape": shape,
                     "ground_plane": ("assumed present and close (an IFA without a "
                                      "ground plane is a bent monopole, which is a "
                                      "different antenna)" if shape == "ifa" else None)},
        extra={"not_computed": NOT_COMPUTED},
    )

def dipole_monopole(freq_ghz, kind="dipole", length_mm=None, wire_radius_mm=None):
    """Free-space dipole / monopole: the resonance length and radiation resistance.

    A thin half-wave dipole resonates slightly short of lambda/2 (the classic 0.47
    to 0.48 lambda, the end effect), a quarter-wave monopole over an infinite ground
    plane is exactly half of it with half the resistance and 3 dB more directivity
    in the upper half space.

    These are free-space, thin-wire results. Length reduction from a nearby ground
    plane, a dielectric former or a housing is NOT included: pass length_mm and the
    tool will tell you what resistance that length implies instead, which is the
    honest direction.
    """
    kind = str(kind or "").strip().lower()
    if kind not in ("dipole", "monopole"):
        return err("kind must be dipole or monopole", "ARG_INVALID")
    try:
        f = float(freq_ghz)
    except (TypeError, ValueError):
        return err("freq_ghz must be numeric", "ARG_INVALID")
    if f <= 0:
        return err("freq_ghz must be > 0", "ARG_RANGE")
    lam0 = _lam0(f)
    frac = 0.475 if kind == "dipole" else 0.2375
    r_rad = 73.1 if kind == "dipole" else 36.5
    gain_dbi = 2.15 if kind == "dipole" else 5.15
    out = {
        "kind": kind,
        "lambda0_mm": round(lam0, 3),
        "resonant_length_mm": round(frac * lam0, 3),
        "length_rule": ("0.475*lambda0 (thin-wire end effect). A thicker conductor "
                        "resonates even shorter - the shortening factor grows with the "
                        "wire radius, and no thin-wire closed form is trustworthy there"),
        "radiation_resistance_ohm": r_rad,
        "directivity_dbi": gain_dbi,
        "feed_gap_mm": (round(lam0 / 200.0, 3) if kind == "dipole" else None),
        "feed_gap_note": ("a dipole is fed at its centre; the gap is a layout choice "
                          "(typically lambda/100 to lambda/200), not a resonance "
                          "parameter" if kind == "dipole" else
                          "a monopole is fed against the ground plane at its base"),
        "ground_plane_requirement": (
            "an infinite ground plane is assumed; a real one needs a radius of at least "
            "lambda/4 (better lambda/2) or the pattern tilts up and the resistance "
            "changes" if kind == "monopole" else None),
    }
    if length_mm is not None:
        try:
            L = float(length_mm)
        except (TypeError, ValueError):
            return err("length_mm must be numeric", "ARG_INVALID")
        if L <= 0:
            return err("length_mm must be > 0", "ARG_RANGE")
        # sinusoidal current: R at any feed point, referred to the current maximum
        out["requested_length"] = {
            "length_mm": L,
            "fraction_of_lambda0": round(L / lam0, 4),
            "note": ("a length other than the resonant one makes the input impedance "
                     "reactive; this model only carries the resistance of the "
                     "sinusoidal current distribution (73.1 ohm at lambda/2), so it "
                     "cannot say what the reactance is at %.3f lambda" % (L / lam0)),
        }
    if wire_radius_mm is not None:
        out["wire_radius_mm"] = float(wire_radius_mm)
        out["wire_note"] = ("thin-wire results assume radius << lambda/100; at "
                            "%.3f lambda the shortening is larger than the 0.475 factor"
                            % (float(wire_radius_mm) / lam0))
    return ok(
        value=out,
        unit="mm",
        model="free_space_" + kind,
        basis=("thin-wire sinusoidal-current theory: resonant length ~0.475*lambda0 "
               "(dipole) / 0.2375*lambda0 (monopole over an infinite ground plane), "
               "R_rad = 73.1 / 36.5 ohm, D = 2.15 / 5.15 dBi"),
        precision=("resonant length +/- 5 % for a thin wire in free space; the resistance "
                   "is exact for the assumed sinusoidal current and is what a real "
                   "element deviates from first"),
        assumptions={"freq_ghz": f, "kind": kind, "medium": "free space, no dielectric"},
        extra={"not_computed": NOT_COMPUTED},
    )

def helix_axial(freq_ghz, turns, diameter_mm, turn_spacing_mm=None, pitch_deg=None):
    """Axial-mode helix (Kraus). Valid only inside its own mode window.

    Kraus' empirical design relations for the axial (end-fire) mode:

        C = pi*D                 circumference
        3/4 < C/lambda0 < 4/3     and 12 deg < pitch < 15 deg, N > 3
        G  [dBic] ~ 11.8 + 10*log10(C_lambda^2 * N * S_lambda)
        HPBW ~ 52 / (C_lambda * sqrt(N*S_lambda))  degrees
        Z_in ~ 140 * C_lambda ohm  (approximate, matching still needed)

    Outside that window the helix is in normal mode (a poor short dipole) and none
    of these relations hold, so the call is refused rather than answered.
    """
    try:
        f = float(freq_ghz)
        n = float(turns)
        d = float(diameter_mm)
    except (TypeError, ValueError):
        return err("numeric arguments required", "ARG_INVALID")
    if f <= 0 or n <= 0 or d <= 0:
        return err("freq_ghz, turns and diameter_mm must be > 0", "ARG_RANGE")
    lam0 = _lam0(f)
    C = math.pi * d
    if turn_spacing_mm is not None:
        S = float(turn_spacing_mm)
    elif pitch_deg is not None:
        S = C * math.tan(math.radians(float(pitch_deg)))
    else:
        return err("give turn_spacing_mm or pitch_deg", "ARG_REQUIRED")
    if S <= 0:
        return err("turn spacing must be > 0", "ARG_RANGE")
    c_lam = C / lam0
    s_lam = S / lam0
    alpha = math.degrees(math.atan(S / C))
    problems = []
    if not (0.75 <= c_lam <= 4.0 / 3.0):
        problems.append("C/lambda0 = %.3f is outside 0.75..1.333" % c_lam)
    if not (11.0 <= alpha <= 16.0):
        problems.append("pitch %.1f deg is outside 11..16 deg" % alpha)
    if n < 3:
        problems.append("N = %g is below 3 turns" % n)
    if problems:
        return err("this helix is not in the axial mode: " + "; ".join(problems),
                   "OUT_OF_VALIDITY",
                   hint="axial mode needs 3/4 < C/lambda < 4/3, pitch 12-15 deg and "
                        "N >= 3. Outside it the antenna radiates in normal mode "
                        "(a short dipole) and Kraus' relations do not apply",
                   extra={"C_over_lambda": round(c_lam, 4), "pitch_deg": round(alpha, 2),
                          "turns": n})
    gain = 11.8 + 10.0 * math.log10(c_lam ** 2 * n * s_lam)
    hpbw = 52.0 / (c_lam * math.sqrt(n * s_lam))
    out = {
        "circumference_mm": round(C, 3),
        "C_over_lambda": round(c_lam, 4),
        "turn_spacing_mm": round(S, 4),
        "pitch_deg": round(alpha, 2),
        "axial_length_mm": round(n * S, 3),
        "turns": n,
        "gain_dbic_estimate": round(gain, 2),
        "hpbw_deg_estimate": round(hpbw, 1),
        "z_in_ohm_estimate": round(140.0 * c_lam, 1),
        "notes": [
            "Kraus' gain and beamwidth relations are fits to measurements: +/-1 dB and "
            "a few degrees, and they assume a good ground plane at the back",
            "the input impedance estimate is the roughest number here - an axial helix "
            "is fed through a balun or a tapered line in practice",
            "the first turn and the last turn matter: a 1/4-turn stub at each end is "
            "how real designs trim the match",
        ],
    }
    return ok(
        value=out,
        unit="mm",
        model="helix_axial_kraus",
        basis=("Kraus axial-mode helix: G = 11.8 + 10log10(C_lambda^2*N*S_lambda) dBic, "
               "HPBW = 52/(C_lambda*sqrt(N*S_lambda)), Z_in ~ 140*C_lambda"),
        precision=("gain +/-1 dB, beamwidth a few degrees, impedance the roughest "
                   "(a starting value for the balun, not a match)"),
        assumptions={"freq_ghz": f, "turns": n, "diameter_mm": d,
                     "turn_spacing_mm": S, "mode": "axial (end fire)"},
        extra={"not_computed": NOT_COMPUTED},
    )

def small_loop(freq_ghz, area_mm2=None, diameter_mm=None, turns=1):
    """Small loop: radiation resistance, and a refusal when it is not small.

    For a loop whose circumference is below 0.1 lambda the current is uniform and
    the radiation resistance is exact:

        R_rad = 31200 * (A/lambda0^2)^2 * N^2      [ohm]

    A small loop is a very poor radiator (milliohms to a few ohm) and highly
    inductive, so it needs a series capacitor and has a narrow bandwidth. Above the
    0.1 lambda limit the formula does not degrade gracefully - it becomes wrong by
    an unbounded factor - so the call is refused instead.
    """
    try:
        f = float(freq_ghz)
        n = float(turns)
    except (TypeError, ValueError):
        return err("freq_ghz and turns must be numeric", "ARG_INVALID")
    if f <= 0 or n <= 0:
        return err("freq_ghz and turns must be > 0", "ARG_RANGE")
    if area_mm2 is None and diameter_mm is None:
        return err("give area_mm2 or diameter_mm", "ARG_REQUIRED")
    if diameter_mm is not None:
        d = float(diameter_mm)
        A = math.pi * (d / 2.0) ** 2
        perim = math.pi * d
    else:
        A = float(area_mm2)
        perim = 2.0 * math.sqrt(math.pi * A)      # equivalent circle
    if A <= 0:
        return err("area must be > 0", "ARG_RANGE")
    lam0 = _lam0(f)
    c_over_lam = perim / lam0
    r_rad = 31200.0 * (A / (lam0 * lam0)) ** 2 * n * n
    if c_over_lam > 0.1:
        return err("circumference is %.3f lambda0: the uniform-current small-loop "
                   "formula only holds below 0.1 lambda0" % c_over_lam,
                   "OUT_OF_VALIDITY",
                   hint="a loop between 0.1 and 1 lambda is a resonant loop: its "
                        "radiation resistance is 50-200 ohm and depends on the shape "
                        "and the feed position. That needs a full-wave solver, and "
                        "this tool will not guess it",
                   extra={"circumference_over_lambda": round(c_over_lam, 4),
                          "r_rad_if_formula_were_used_ohm": float("%.6g" % r_rad)})
    # loop inductance (a circular loop of wire radius a): L = mu0*r*(ln(8r/a)-2)
    out = {
        "area_mm2": round(A, 3),
        "circumference_mm": round(perim, 3),
        "circumference_over_lambda": round(c_over_lam, 4),
        # keep the significant digits: a physically real loop on 13.56 MHz has
        # R_rad around 1e-7 ohm, and rounding that to 0.0 would read as "no
        # radiation at all" instead of "far below the copper loss"
        "r_rad_ohm": float("%.6g" % r_rad),
        "r_rad_mohm": float("%.6g" % (r_rad * 1000.0)),
        "turns": n,
        "directivity_dbi": 1.76,
        "notes": [
            "a small loop is a magnetic dipole: D = 1.76 dBi, and the pattern is the "
            "same as a small dipole rotated 90 degrees",
            "R_rad is tiny compared with the loss resistance of real copper at these "
            "sizes, so the efficiency is a loss problem, not a radiation problem - "
            "use wide trace, many vias and thick copper",
            "the input impedance is dominated by the loop inductance: it must be "
            "tuned out with a series capacitor, and that sets the bandwidth",
        ],
    }
    if c_over_lam > 0.05:
        out["validity_note"] = ("%.3f lambda0 is inside the formula's range but close "
                                "to its edge; the error grows quickly as the "
                                "circumference approaches 0.1" % c_over_lam)
    return ok(
        value=out,
        unit="ohm",
        model="small_loop_uniform_current",
        basis=("uniform-current small loop: R_rad = 31200*(A/lambda0^2)^2*N^2 ohm, "
               "D = 1.5 (1.76 dBi), valid for circumference < 0.1*lambda0"),
        precision=("R_rad is exact inside the 0.1 lambda limit under the uniform-current "
                   "assumption (which the limit is there to guarantee); efficiency is "
                   "NOT computed because it is dominated by copper and ground losses "
                   "this model does not have"),
        assumptions={"freq_ghz": f, "turns": n, "uniform_current": True},
        extra={"not_computed": NOT_COMPUTED},
    )

def q_limit(freq_ghz, size_mm, vswr=2.0, dimension="largest"):
    """Chu/Harrington bandwidth ceiling for an antenna inside a given box.

    For any antenna that fits inside a sphere of radius a:

        Q >= 1/(ka)^3 + 1/(ka),   k = 2*pi/lambda0
        fractional bandwidth (VSWR < S) <= (S-1)/(Q*sqrt(S))

    This is the number to quote when someone asks for a 2.4 GHz antenna in a 10 mm
    cube: it answers "impossible" before anyone spends a week on layout. It is a
    bound on the best possible antenna, so a real design is always worse.
    """
    try:
        f = float(freq_ghz)
        size = float(size_mm)
        s = float(vswr)
    except (TypeError, ValueError):
        return err("numeric arguments required", "ARG_INVALID")
    if f <= 0 or size <= 0 or s <= 1.0:
        return err("freq_ghz and size_mm must be > 0; vswr must be > 1", "ARG_RANGE")
    lam0 = _lam0(f)
    a = size / 2.0                                  # sphere radius, mm
    ka = 2.0 * math.pi * a / lam0
    q_min = 1.0 / (ka ** 3) + 1.0 / ka if ka > 0 else float("inf")
    frac = (s - 1.0) / (q_min * math.sqrt(s))
    out = {
        "sphere_radius_mm": round(a, 3),
        "ka": round(ka, 4),
        "q_min": round(q_min, 3),
        "max_fractional_bandwidth_pct": round(100.0 * frac, 3),
        "max_band_mhz": round(1000.0 * f * frac, 2),
        "regime": ("electrically small (ka < 1): bandwidth is the binding constraint"
                   if ka < 1.0 else
                   "resonant size (ka >= 1): the limit is loose and the structure, not "
                   "the size, decides"),
        "note": ("the bound assumes a lossless antenna and a single resonance; a lossy "
                 "antenna can LOOK wider because loss flattens the match, which is why "
                 "a wide VSWR curve is not evidence of a good radiator"),
    }
    return ok(
        value=out,
        unit="%",
        model="chu_harrington_limit",
        basis=("Chu/Harrington: Q >= 1/(ka)^3 + 1/(ka) for an antenna inside a sphere "
               "of radius a; fractional bandwidth (VSWR<S) = (S-1)/(Q*sqrt(S))"),
        precision=("a hard lower bound on Q, hence a hard upper bound on bandwidth. "
                   "Not an estimate of the bandwidth a design will achieve"),
        assumptions={"freq_ghz": f, "size_mm": size, "vswr": s,
                     "sphere": "radius = size/2 (" + str(dimension) + " dimension)"},
        extra={"not_computed": NOT_COMPUTED},
    )

def chip_antenna(freq_ghz, max_gain_dbi=None, keepout_x_mm=None, keepout_y_mm=None):
    """Ceramic chip antenna: what can be computed, and what only the datasheet has.

    A chip antenna is a purchased resonator in a package. Its gain, bandwidth and
    matching network are the vendor's measured data - they are NOT derivable from
    the frequency and the package size, and any tool that pretends otherwise is
    producing a plausible number. What CAN be computed here:

      * the keep-out area an antenna of this class needs (the datasheet number is
        authoritative; the free-space near-field estimate is a sanity check on it)
      * the matching requirement once the vendor's impedance is known
      * the size the ground plane has to give up

    So this returns the datasheet-driven checklist plus a physical sanity number,
    and refuses to invent a gain.
    """
    try:
        f = float(freq_ghz)
    except (TypeError, ValueError):
        return err("freq_ghz must be numeric", "ARG_INVALID")
    if f <= 0:
        return err("freq_ghz must be > 0", "ARG_RANGE")
    lam0 = _lam0(f)
    out = {
        "lambda0_mm": round(lam0, 3),
        "near_field_boundary_mm": round(lam0 / (2.0 * math.pi), 3),
        "far_field_distance_note": ("far field starts at 2*D^2/lambda0 from an "
                                    "aperture of the largest dimension D; pass "
                                    "size_mm to have it computed for this part"),
        "checklist": [
            "the vendor's keep-out rectangle (length x width x height) is the "
            "authoritative number - it is usually expressed as 'no copper on any "
            "layer', including the ground plane underneath and the return path",
            "the ground plane edge is part of the antenna: the datasheet specifies "
            "how much ground must be kept and in which direction",
            "the matching network (usually a single series or shunt part) is given "
            "for a specific layout, and it changes if the keep-out or the ground "
            "size changes",
            "the 'gain' in the datasheet is peak gain measured on the reference "
            "board; it is not the gain on this board",
        ],
        "not_computed": [
            "gain / efficiency of a chip antenna (vendor measurement only)",
            "the matching component values (need the vendor's impedance at frequency)",
            "detuning by the enclosure or the product's plastics",
        ],
    }
    if keepout_x_mm is not None and keepout_y_mm is not None:
        kx, ky = float(keepout_x_mm), float(keepout_y_mm)
        out["keepout"] = {
            "required_mm": [kx, ky],
            "in_wavelengths": [round(kx / lam0, 4), round(ky / lam0, 4)],
            "area_mm2": round(kx * ky, 2),
            "note": ("a keep-out of %.3f x %.3f lambda is a reasonable order for a "
                     "2.4 GHz chip part; if the datasheet number is much smaller, "
                     "trust the datasheet - it is measured"
                     % (kx / lam0, ky / lam0)),
        }
        warnings = []
        if kx < lam0 / 20.0 or ky < lam0 / 20.0:
            warnings.append("keep-out smaller than lambda/20 in one dimension: "
                            "re-check the datasheet, this is unusually tight for a "
                            "radiating element")
        near = lam0 / (2.0 * math.pi)
        if min(kx, ky) < near / 2.0:
            warnings.append("the keep-out is much smaller than the reactive near "
                            "field boundary (%.1f mm): the antenna will couple into "
                            "whatever is inside that boundary - the ground plane, "
                            "the battery, the enclosure" % near)
        if warnings:
            out["keepout"]["warnings"] = warnings
            out["warnings"] = warnings
    if max_gain_dbi is not None:
        out["datasheet_gain_dbi"] = float(max_gain_dbi)
        out["datasheet_gain_note"] = ("echoed, not computed: this is the vendor's peak "
                                      "gain on their reference board")
    return ok(
        value=out,
        unit="mm",
        model="chip_antenna_checklist",
        basis=("no closed form exists for a packaged ceramic resonator: the answer is "
               "the datasheet-driven checklist plus the free-space near/far field "
               "boundaries lambda/(2*pi) and 2D^2/lambda"),
        precision=("the field boundaries are exact for far-field distance; the keep-out "
                   "sanity number is an order-of-magnitude check, the datasheet rules"),
        assumptions={"freq_ghz": f},
        extra={"not_computed": out["not_computed"]},
    )
