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
  * no Q: no dielectric loss tangent, no copper loss, no radiation Q. A planing
    resonance that looks scary at 3 GHz may be completely damped by a lossy
    prepreg; that is not modelled, so no "is this a problem" verdict is derived
    from Q here.
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
            "Q (dielectric tan-delta, copper loss, radiation) - so no 'is this "
            "resonance a problem' judgement is derived here",
            "plane-pair impedance vs frequency (that is pdn_impedance, and it "
            "needs the capacitor bank as input)",
            "via barrel inductance and antipad capacitance",
        ],
        "authoritative": False,
        "verdict_available_from": "no vendor engine reports plane resonances; "
                                  "use a 3-D field solver (HFSS/CST/openEMS) "
                                  "when detuning and Q matter",
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
              "exact for the idealised cavity; no correction for open edges, no "
              "Q, no losses - treat f as the nominal mode and expect a few "
              "percent of detuning in hardware",
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
        out["pitch_for_ratio"] = None  # filled below when a criterion is given

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
