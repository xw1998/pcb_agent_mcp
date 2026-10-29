"""Cavity: the board itself as a resonator (parallel-plate modes).

Two copper planes with dielectric between them are a cavity. Two vias, or a via
fence, or two stitching rows, are its side walls. At some frequency the thing
rings, and at that frequency the "ground plane" stops being a ground plane - it
becomes a radiator with a standing-wave voltage between the layers. This layer
answers exactly one question about that: *where are the modes*, and *how far
apart can I put the stitching vias before the walls stop looking like walls*.

What it is:
  * f_mn = c / (2*sqrt(er)) * sqrt((m/a)^2 + (n/b)^2)      (TM_mn0, PMC side walls)
  * lambda_g = c / (f * sqrt(er))                           (guided wavelength)

What it is NOT (and says so on every return):
  * the side walls of a real plane pair are neither PMC nor PEC. Open edges
    detune the modes DOWN by a few percent (and a small plane rings higher than
    its nominal size suggests, because the fringing field is part of it). No
    correction is applied here - the number is the textbook one, marked as such.
  * Q is a SEPARATE function (`plane_q`), because a mode location and a mode
    damping are different questions. `plane_q` carries copper loss (which for
    this geometry has the clean answer Qc = h/delta_s, independent of mode and
    of plane size) and dielectric loss (Qd = 1/tan_delta). What it still does
    not carry: radiation Q, surface roughness, and the fact that a real plane
    pair's Q is dominated by whatever is soldered across it. So there is no
    "is this a problem" verdict anywhere - a resonance needs an excitation and a
    coupling to matter, and neither is in this module.
  * no via barrel inductance, no antipad capacitance, no plane-pair impedance
    (that is `power.pdn_impedance`, which takes a capacitor bank as given).

The via-fence rule is deliberately NOT built in. `lambda_g/20` is an engineering
convention, not a derivation, so it is the caller's to state: pass
`max_pitch_ratio` and you get a verdict, leave it out and you get the ratio plus
`indeterminate` - the same discipline as the DFM layer not shipping a fab table.
"""
import math

from .result import ok, err

C0 = 299792458.0      # m/s, exact by definition
MU0 = 4e-7 * math.pi  # H/m, exact in SI-2019
COPPER_SIGMA = 5.8e7  # S/m, IACS copper at 20 C (datasheets print 5.80e7..5.96e7)
LAMBDA_G_NOTE = ("lambda_g = c / (f * sqrt(er)) - the parallel-plate guided "
                 "wavelength, i.e. the wavelength of the standing wave between "
                 "the two planes, not the free-space one")


def _num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v != v or v in (float("inf"), float("-inf")):
        return None
    return v


def _sig(x, digits=6):
    """Format without exponent noise (matches the rest of calc)."""
    if x is None:
        return None
    if x == 0:
        return 0.0
    ax = abs(x)
    if ax >= 1e-4 and ax < 1e7:
        n = digits - 1 - int(math.floor(math.log10(ax)))
        return round(x, n) if n > 0 else float(round(x, n))
    return float("%.*e" % (digits - 1, x))


def _check_plane(a_mm, b_mm, er):
    a = _num(a_mm)
    b = _num(b_mm)
    e = _num(er)
    for name, v in (("a_mm", a), ("b_mm", b), ("er", e)):
        if v is None:
            return None, None, None, err("%s must be a number" % name, "ARG_INVALID")
    for name, v in (("a_mm", a), ("b_mm", b), ("er", e)):
        if v <= 0:
            return None, None, None, err("%s must be > 0" % name, "ARG_RANGE",
                                         extra={"offending": name})
    if e < 1.0:
        return None, None, None, err(
            "er must be >= 1 (vacuum); a value below 1 is not a dielectric",
            "ARG_RANGE", extra={"er": e})
    return a, b, e, None


def plane_resonance(a_mm, b_mm, er=4.3, m_max=2, n_max=2, f_operate_hz=None,
                    top=12):
    """Parallel-plate (cavity) resonances of an a_mm x b_mm plane pair.

    The classical solution for a rectangular cavity with perfect magnetic
    side walls and perfect electric top/bottom plates is

        f_mn = c / (2*sqrt(er)) * sqrt((m/a)^2 + (n/b)^2),  m,n >= 0, not both 0

    with a, b in metres. m=n=0 is the trivial uniform field (0 Hz) and is
    skipped. Degenerate pairs (a == b -> (1,0)/(0,1)) are reported separately,
    not merged, so the caller sees the multiplicity.

    `f_operate_hz` (optional) only adds a read-out: which mode is nearest and at
    what ratio f/f_mn. It does not produce a pass/fail - there is no criterion
    here, and a resonance is only harmful together with an excitation and a Q.
    """
    a, b, e, bad = _check_plane(a_mm, b_mm, er)
    if bad:
        return bad
    mm_, nn_ = _num(m_max), _num(n_max)
    if mm_ is None or nn_ is None or mm_ < 0 or nn_ < 1 or int(mm_) != mm_ or int(nn_) != nn_:
        return err("m_max / n_max must be integers >= 0 / >= 1", "ARG_INVALID",
                   hint="the (0,0) mode is skipped, so n_max >= 1 is needed for "
                        "any mode to exist")
    m_max, n_max = int(mm_), int(nn_)

    a_m = a * 1e-3
    b_m = b * 1e-3
    k = C0 / (2.0 * math.sqrt(e))

    modes = []
    for m in range(0, m_max + 1):
        for n in range(0, n_max + 1):
            if m == 0 and n == 0:
                continue
            f = k * math.sqrt((m / a_m) ** 2 + (n / b_m) ** 2)
            modes.append((f, m, n))
    if not modes:
        return err("no modes in the requested range", "ARG_RANGE")
    modes.sort()
    modes = modes[:int(top)] if top else modes

    rows = []
    for f, m, n in modes:
        lam = C0 / (f * math.sqrt(e))
        rows.append({
            "m": m,
            "n": n,
            "f_hz": _sig(f),
            "f_ghz": _sig(f / 1e9),
            "lambda_g_mm": _sig(lam * 1e3),
            "lambda_0_mm": _sig(C0 / f * 1e3),
        })

    f0 = modes[0][0]
    extra = {
        "modes": rows,
        "lowest_mode": {"m": modes[0][1], "n": modes[0][2], "f_hz": _sig(f0),
                        "f_ghz": _sig(f0 / 1e9),
                        "lambda_g_mm": _sig(C0 / (f0 * math.sqrt(e)) * 1e3)},
        "mode_count_returned": len(rows),
        "cavity_is_uniform_field_below_hz": _sig(f0),
        "not_computed": [
            "detuning from open (non-PMC) side walls - real planes ring a few "
            "percent off the textbook f_mn, always with an unquantified sign per "
            "this model",
            "Q - plane_q() answers that one (copper loss and dielectric "
            "tan-delta in closed form); radiation Q and the damping contributed "
            "by whatever is mounted across the planes are still not modelled, "
            "and no 'is this resonance a problem' judgement exists because a "
            "mode needs an excitation and a coupling to be a problem",
            "plane-pair impedance vs frequency (that is pdn_impedance, and it "
            "needs the capacitor bank as input)",
            "via barrel inductance and antipad capacitance",
        ],
        "authoritative": False,
        "where_the_rest_is": "plane_q() for copper + dielectric damping, "
                             "via_fence() for the wall model",
        "verdict_available_from": "no vendor engine reports plane resonances; "
                                  "use a 3-D field solver (HFSS/CST/openEMS) "
                                  "when detuning, radiation Q or coupling to a "
                                  "real load matter",
    }
    if f_operate_hz is not None:
        fo = _num(f_operate_hz)
        if fo is None or fo <= 0:
            return err("f_operate_hz must be a positive number", "ARG_INVALID")
        best = min(modes, key=lambda t: abs(t[0] - fo))
        extra["operating_point"] = {
            "f_operate_hz": _sig(fo),
            "nearest_mode": {"m": best[1], "n": best[2], "f_hz": _sig(best[0])},
            "f_operate_over_f_mode": _sig(fo / best[0]),
            "delta_hz": _sig(fo - best[0]),
        }

    return ok(rows, "mode list", "TM_mn0 rectangular cavity, PMC side walls",
              "f_mn = c/(2*sqrt(er)) * sqrt((m/a)^2+(n/b)^2); c = 299792458 m/s "
              "(exact); a, b are the plane extents in metres, er the dielectric "
              "constant BETWEEN the planes",
              "exact for the idealised cavity; no correction for open edges and "
              "no losses in the eigenvalue solve - treat f as the nominal mode and "
              "expect a few percent of detuning in hardware. Pass this f to "
              "plane_q() if you need the damping",
              {"side_walls": "PMC (magnetic) - a via fence only approximates "
                             "this, and only when the pitch is small vs lambda_g",
               "plates": "PEC, zero thickness, infinite conductivity",
               "dielectric": "homogeneous er, no tan-delta, no thickness "
                             "dependence (the cavity model has no h in it, which "
                             "is why f_mn does not depend on plane spacing)",
               "lambda_g_note": LAMBDA_G_NOTE},
              extra)


def via_fence(f_resonance_hz, er, pitch_mm=None, max_pitch_ratio=None,
              fence_width_mm=None):
    """Is the stitching-via pitch small enough that the walls look like walls?

    This function computes `pitch / lambda_g` (and the per-pitch phase in
    degrees) because those are facts. Whether that is small enough is NOT
    invented here: `lambda_g/20` and `lambda_g/10` both circulate, they disagree
    by 2x, and neither is derived - so the acceptance ratio must be supplied by
    the caller (`max_pitch_ratio`). Without it the verdict is `indeterminate`
    and the ratio is still returned, which is the useful half.

    Not modelled: via barrel diameter / antipad size (a fence of 0.3 mm drills
    and a fence of 1 mm drills are treated as identical walls), the number of
    rows, the fence's own resonances, or leakage through gaps where a signal
    layer's plane is voided.
    """
    f = _num(f_resonance_hz)
    e = _num(er)
    if f is None or f <= 0:
        return err("f_resonance_hz must be a positive number", "ARG_REQUIRED",
                   hint="take it from plane_resonance() - the mode you care about")
    if e is None or e < 1.0:
        return err("er must be a number >= 1", "ARG_INVALID")

    lam_g_mm = C0 / (f * math.sqrt(e)) * 1e3
    out = {
        "lambda_g_mm": _sig(lam_g_mm),
        "lambda_g_note": LAMBDA_G_NOTE,
        "not_computed": [
            "via barrel diameter, antipad size and fence row count - all fences "
            "are treated as ideal walls at this level",
            "leakage through slots where a plane is voided (the real failure "
            "mode in mixed-signal plane pairs)",
            "the fence's own stop-band / band-gap behaviour",
        ],
    }
    if fence_width_mm is not None:
        w = _num(fence_width_mm)
        if w is None or w <= 0:
            return err("fence_width_mm must be a positive number", "ARG_INVALID")
        out["fence_width_over_lambda_g"] = _sig(w / lam_g_mm)
        out["fence_electrical_length_deg"] = _sig(360.0 * w / lam_g_mm)

    p = _num(pitch_mm)
    if p is not None:
        if p <= 0:
            return err("pitch_mm must be > 0", "ARG_RANGE")
        ratio = p / lam_g_mm
        out["pitch_mm"] = _sig(p)
        out["pitch_over_lambda_g"] = _sig(ratio)
        out["phase_per_pitch_deg"] = _sig(360.0 * ratio)
        out["vias_per_lambda_g"] = _sig(1.0 / ratio)

    crit = _num(max_pitch_ratio)
    if crit is not None:
        if crit <= 0:
            return err("max_pitch_ratio must be > 0", "ARG_RANGE")
        out["criterion"] = {"max_pitch_ratio": _sig(crit),
                            "source": "caller-supplied (not built in)"}
        out["max_pitch_mm"] = _sig(crit * lam_g_mm)
        if p is None:
            # No pitch given: hand back the limit, do not invent a pitch.
            out["verdict"] = "indeterminate"
            out["verdict_reason"] = ("no pitch_mm supplied, so there is nothing "
                                     "to compare against the criterion")
            return ok(out, "ratio / mm",
                      "pitch/lambda_g against a caller-supplied criterion",
                      "ratio = pitch / lambda_g, lambda_g = c/(f*sqrt(er)); the "
                      "criterion itself is an engineering convention and is taken "
                      "from the caller, never from this module",
                      "ratio is exact arithmetic; the criterion's validity is the "
                      "caller's, and the wall model is ideal (see not_computed)",
                      {"side_wall_model": "ideal PMC replacement by the fence"},
                      out)
        if p > crit * lam_g_mm:
            out["verdict"] = "fail"
            out["verdict_reason"] = ("pitch/lambda_g = %.4g exceeds the supplied "
                                     "limit %.4g" % (p / lam_g_mm, crit))
        else:
            out["verdict"] = "pass"
            out["verdict_reason"] = ("pitch/lambda_g = %.4g is within the supplied "
                                     "limit %.4g" % (p / lam_g_mm, crit))
        out["margin_mm"] = _sig(crit * lam_g_mm - p)
    else:
        if p is not None:
            out["verdict"] = "indeterminate"
            out["verdict_reason"] = ("no max_pitch_ratio supplied - lambda_g/20 and "
                                     "lambda_g/10 both circulate and neither is "
                                     "derived, so this module does not pick one")
        else:
            return err("need pitch_mm, max_pitch_ratio, or both", "ARG_REQUIRED",
                       hint="pitch_mm alone gives ratios; add max_pitch_ratio to "
                            "get a pass/fail")

    return ok(out, "ratio / mm", "pitch/lambda_g against a caller-supplied criterion",
              "ratio = pitch / lambda_g, lambda_g = c/(f*sqrt(er)); the criterion "
              "itself is an engineering convention and is taken from the caller, "
              "never from this module",
              "ratio is exact arithmetic; the criterion's validity is the "
              "caller's, and the wall model is ideal (see not_computed)",
              {"side_wall_model": "ideal PMC replacement by the fence"}, out)

# ---------------------------------------------------------------------------
# Q of one plane-pair mode
# ---------------------------------------------------------------------------
def _plane_q_by_integration(a_mm, b_mm, m, n, h_mm, f_hz,
                            conductivity_s_m=COPPER_SIGMA, samples=160):
    """Q_conductor of one mode by adding up the fields, with no closed form used.

    Perturbation theory written out longhand: build the mode's own E and H, sum
    the stored energy over the dielectric volume and the copper loss over the two
    plates, divide. Nothing in here knows that the answer is h/delta_s, so the
    tests comparing this with plane_q() are a genuine second derivation instead
    of a restatement of the first.

    Textbook ingredients only: E_z = cos(m pi x/a) cos(n pi y/b),
    H_t = (1/(i omega mu)) curl of it, W = eps |E|^2 h /2 (voltage amplitude 1),
    P = Rs * integral(|H_t|^2) over BOTH plates, eps from the resonance
    condition omega^2 mu eps = (m pi/a)^2 + (n pi/b)^2.
    """
    a = a_mm * 1e-3
    b = b_mm * 1e-3
    h = h_mm * 1e-3
    w = 2.0 * math.pi * f_hz
    rs = math.sqrt(w * MU0 / (2.0 * conductivity_s_m))
    km = m * math.pi / a
    kn = n * math.pi / b
    dx = a / samples
    dy = b / samples
    int_e2 = 0.0
    int_h2 = 0.0
    for i in range(samples):
        x = (i + 0.5) * dx
        cx = math.cos(km * x)
        sx = math.sin(km * x)
        for j in range(samples):
            y = (j + 0.5) * dy
            cy = math.cos(kn * y)
            sy = math.sin(kn * y)
            ez = cx * cy
            hx = kn * sy * cx / (w * MU0)
            hy = km * sx * cy / (w * MU0)
            int_e2 += ez * ez * dx * dy
            int_h2 += (hx * hx + hy * hy) * dx * dy
    eps = (km * km + kn * kn) / (w * w * MU0)
    stored = eps * int_e2 * h / 2.0     # 2 * W_e, peak amplitude
    lost = rs * int_h2                  # two plates, each Rs/2 * integral
    return w * stored / lost


def plane_q(f_hz, h_mm, tan_delta=None, conductivity_s_m=COPPER_SIGMA):
    """Damping of a plane-pair mode at f_hz: copper Q, dielectric Q, and their sum.

    Conductor loss. Perturbation theory on the idealised cavity (PMC side walls,
    two PEC plates a distance h apart) gives a result with no a, no b and no m,n
    in it:

        Qc = omega * mu * h / (2 * Rs) = h / delta_s

    which is worth stating plainly because it is the answer people get wrong by a
    factor of two, and because it says the copper Q of a plane pair depends only
    on the dielectric thickness. The factor of 2 comes from counting BOTH plates:
    each plate loses (Rs/2)*integral|H_t|^2, the stored energy is eps*|E|^2*h/2
    with the pair's voltage normalised to 1 V, and on resonance omega^2*mu*eps
    cancels the (m*pi/a)^2 + (n*pi/b)^2 that sits in |H_t|^2 - which is exactly
    why the mode numbers drop out. The same number falls out of the
    transmission-line argument (a parallel-plate line has alpha_c = Rs/(eta*h),
    so any half-wave resonator built from it has Q = beta/(2*alpha_c) =
    h/delta_s). `_plane_q_by_integration` is the third route and the tests
    require all three to agree.

    So: a plane pair with 1.6 mm of FR4 between the copper has Qc ~ 770 at 1 GHz
    no matter how big or small it is, rising as sqrt(f) (delta ~ 1/sqrt(f)).

    Dielectric loss. Qd = 1/tan_delta, flat in frequency for a given laminate -
    and that is the one that usually wins: FR4's 0.02 gives Qd = 50, so the pair
    damps to Q ~ 47 whatever the copper does. This is why a scary-looking mode at
    3 GHz on a lossy prepreg is often a non-event.

    Total: 1/Q = 1/Qc + 1/Qd (the two loss mechanisms see the same stored energy,
    so the reciprocals add).

    Not modelled, and these can dominate in a real board:
      * radiation Q (open edges, a split plane, an unshielded edge radiating) -
        not in the parallel-plate model at all;
      * surface roughness, which raises Rs by 20-80% above ~1 GHz;
      * inhomogeneous Dk (glass weave, prepreg/core asymmetry);
      * the damping of whatever is mounted across the planes - decoupling
        capacitors and IC packages are usually the dominant loss of a real PDN
        resonance, and they are exactly what this idealisation leaves out;
      * temperature and frequency dependence of tan_delta.

    No verdict: `higher Q = worse` is not a rule. A resonance is a problem when
    something drives it and something couples to it; this function reports the
    damping and says so.
    """
    f = _num(f_hz)
    h = _num(h_mm)
    sg = _num(conductivity_s_m)
    if f is None:
        return err("f_hz must be a number", "ARG_INVALID")
    if h is None:
        return err("h_mm must be a number", "ARG_INVALID")
    if f <= 0:
        return err("f_hz must be > 0", "ARG_RANGE", extra={"f_hz": f})
    if h <= 0:
        return err("h_mm must be > 0", "ARG_RANGE", extra={"h_mm": h})
    if sg is None:
        return err("conductivity_s_m must be a number", "ARG_INVALID")
    if sg <= 0:
        return err("conductivity_s_m must be > 0", "ARG_RANGE")

    w = 2.0 * math.pi * f
    delta = math.sqrt(2.0 / (w * MU0 * sg))         # skin depth, m
    rs = 1.0 / (sg * delta)                          # = sqrt(w mu / (2 sigma))
    qc = h * 1e-3 / delta                            # = omega mu h / (2 Rs)

    td = None
    if tan_delta is not None:
        td = _num(tan_delta)
        if td is None:
            return err("tan_delta must be a number", "ARG_INVALID")
        if td <= 0:
            return err("tan_delta must be > 0", "ARG_RANGE",
                       hint="a lossless dielectric is expressed by leaving "
                            "tan_delta out, not by passing 0")
        if td >= 1.0:
            return err("tan_delta %g >= 1 cannot be a resonance (Qd would be %g)"
                       % (td, 1.0 / td), "ARG_RANGE",
                       hint="tan_delta >= 1 is a lossy conductor-like medium, not a "
                            "resonator; the lumped 1/Q addition also stops being "
                            "meaningful")
    qd = (1.0 / td) if td else None
    if qd is None:
        q_total = qc
    else:
        q_total = 1.0 / (1.0 / qc + 1.0 / qd)

    out = {
        "f_hz": _sig(f),
        "h_mm": _sig(h),
        "conductivity_s_m": _sig(sg),
        "skin_depth_um": _sig(delta * 1e6),
        "surface_resistance_ohm_sq": _sig(rs),
        "q_conductor": _sig(qc),
        "q_conductor_closed_form": "h / delta_s = omega*mu*h / (2*Rs)",
        "q_dielectric": _sig(qd) if qd is not None else None,
        "tan_delta": _sig(td) if td is not None else None,
        "q_total": _sig(q_total),
        "half_power_bandwidth_hz": _sig(f / q_total),
        "ringing_amplitude_tau_s": _sig(q_total / (math.pi * f)),
        "conductor_q_is_independent_of_size_and_mode": True,
        "scaling": {
            "q_conductor_vs_f": "proportional to sqrt(f) (skin depth ~ 1/sqrt(f))",
            "q_conductor_vs_h": "proportional to h (thicker dielectric, higher Q)",
            "q_dielectric_vs_f": "flat for a fixed tan_delta",
        },
    }
    if qd is None:
        out["dielectric_not_included"] = (
            "no tan_delta was supplied, so q_total is the conductor limit only. "
            "For FR4-class prepreg tan_delta is ~0.02, i.e. Qd = 50, which is "
            "LOWER than Qc in a normal stackup - the omission is not conservative.")
    else:
        out["which_dominates"] = ("dielectric" if qd < qc else "copper")

    return ok(out, "dimensionless Q",
              "perturbation-theory Q of a parallel-plate mode",
              "Qc = omega*mu*h/(2*Rs) = h/delta_s with delta_s = sqrt(2/(omega*mu*"
              "sigma)) and Rs = 1/(sigma*delta_s); Qd = 1/tan_delta; "
              "1/Q = 1/Qc + 1/Qd",
              "closed form on the idealised cavity (PEC plates, PMC side walls): "
              "Qc is exact for that model and independent of a, b, m, n, which is "
              "the surprising part and is cross-checked numerically in the tests. "
              "The model omits radiation Q, surface roughness and the damping of "
              "mounted components, so a real plane pair usually rings LESS than "
              "this; treat it as an upper bound on Q",
              {"plates": "PEC, smooth, both plates counted",
               "dielectric": "homogeneous, loss expressed as one tan_delta",
               "side_walls": "PMC - no side-wall loss, hence none of the fence "
                             "geometry appears in Qc",
               "coupling": "no excitation and no load, so no verdict follows"},
              {"not_computed": [
                  "radiation Q (open edges, split planes, an edge that radiates)",
                  "surface roughness correction to Rs (~+20..80% above 1 GHz)",
                  "Dk/Df variation with frequency and temperature, glass weave",
                  "damping by decoupling capacitors and packages - usually the "
                  "dominant loss of a real PDN resonance",
               ],
               "how_to_use": "take f from plane_resonance() for the mode you care "
                             "about, then ask here for its damping",
               "does_not_prove": "that the resonance is or is not a problem"})
