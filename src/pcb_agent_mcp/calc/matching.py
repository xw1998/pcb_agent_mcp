"""Impedance matching: what a measured antenna impedance needs to look like 50 ohm.

Everything here is exact algebra on complex impedances - there is no fitted model,
which is why the answers are labelled `exact` and the *practical* limits are stated
separately (component Q, layout parasitics, the fact that a two-element network
only works if the target resistance is on the right side of 50 ohm).

The one thing this module deliberately does not do: design a broadband match. A
two-element L network has exactly one solution pair per topology and its bandwidth
is whatever the load's Q allows; when the required Q is high, the honest answer is
"this antenna is narrowband" and the Chu limit in `antenna.q_limit` says how
narrowband it has to be.

Conventions: every reactance is in ohm, positive = inductive. A series element with
X > 0 is an inductor, X < 0 a capacitor. A shunt element is given as a susceptance
B in siemens, positive = capacitive.
"""
import math
import re

from .result import ok, err
from .rf import C0_MM_PER_NS

def _to_complex(z):
    if isinstance(z, complex):
        return z
    if isinstance(z, dict):
        try:
            return complex(float(z.get("real", z.get("r", 0.0))),
                           float(z.get("imag", z.get("x", 0.0))))
        except (TypeError, ValueError):
            raise ValueError("impedance object needs numeric real/imag")
    if isinstance(z, str):
        txt = z.strip().lower().replace(" ", "").replace("i", "j")
        m = re.match(r"^([+-]?)j([0-9.]+)$", txt)
        if m:
            body = float(m.group(2))
            return complex(0.0, -body if m.group(1) == "-" else body)
        m = re.match(r"^([0-9.]+)j$", txt)
        if m:
            return complex(0.0, float(m.group(1)))
        try:
            return complex(txt)
        except (TypeError, ValueError):
            raise ValueError("impedance string must look like '100+25j', '-j30' or '50'")
    try:
        return complex(float(z))
    except (TypeError, ValueError):
        raise ValueError("impedance must be a number, {real, imag} or '100+25j'")

def _round(v, n=4):
    return None if v is None else round(v, n)

# ------------------------------------------------------------------ reflection
def reflection(z, z0=50.0, freq_ghz=None):
    """Reflection coefficient, VSWR, return loss and mismatch loss of one load.

    These four numbers are the same measurement seen four ways, and mixing them up
    is how a "1.5:1" spec becomes an "11 dB" spec by accident. All four come back
    with their own definition in `basis`.
    """
    try:
        Z = _to_complex(z)
        Z0 = float(z0)
    except (TypeError, ValueError) as exc:
        return err(str(exc), "ARG_INVALID")
    if Z0 <= 0:
        return err("z0 must be > 0", "ARG_RANGE")
    if Z.real <= 0:
        return err("a load with a non-positive resistance has no passive match",
                   "ARG_RANGE")
    g = (Z - Z0) / (Z + Z0)
    mag = abs(g)
    vswr = float("inf") if mag >= 1.0 else (1.0 + mag) / (1.0 - mag)
    out = {
        "z_ohm": {"real": _round(Z.real, 4), "imag": _round(Z.imag, 4)},
        "gamma": {"real": _round(g.real, 6), "imag": _round(g.imag, 6)},
        "gamma_mag": _round(mag, 6),
        "gamma_phase_deg": _round(math.degrees(math.atan2(g.imag, g.real)), 3),
        "vswr": ("inf" if mag >= 1.0 else _round(vswr, 4)),
        "return_loss_db": (None if mag == 0 else _round(-20.0 * math.log10(mag), 3)),
        "mismatch_loss_db": _round(-10.0 * math.log10(max(1e-12, 1.0 - mag * mag)), 4),
        "reflected_power_pct": _round(100.0 * mag * mag, 3),
    }
    if freq_ghz is not None and Z.imag != 0:
        try:
            f = float(freq_ghz)
        except (TypeError, ValueError):
            return err("freq_ghz must be numeric", "ARG_INVALID")
        if f <= 0:
            return err("freq_ghz must be > 0", "ARG_RANGE")
        w = 2.0 * math.pi * f * 1e9
        out["equivalent_reactance"] = {
            "series_inductance_nh": (_round(Z.imag / w * 1e9, 3) if Z.imag > 0 else None),
            "series_capacitance_pf": (_round(-1.0 / (w * Z.imag) * 1e12, 3)
                                      if Z.imag < 0 else None),
            "note": "the reactance of this load seen as a series element at this "
                    "frequency - not a matching solution",
        }
    return ok(
        value=out,
        unit="",
        model="reflection_coefficient",
        basis=("gamma = (Z-Z0)/(Z+Z0); VSWR = (1+|gamma|)/(1-|gamma|); "
               "return loss = -20log10|gamma|; mismatch loss = -10log10(1-|gamma|^2)"),
        precision="exact for the impedance given; the impedance is the input, so its "
                  "own measurement error is the only error here",
        assumptions={"z0_ohm": Z0},
    )

# ---------------------------------------------------------------- L network
def _element_from_x(x, w):
    """Series reactance -> the part that realises it."""
    if x is None or x == 0:
        return {"kind": "none"}
    if x < 0:
        return {"kind": "capacitor_series", "c_pf": _round(-1.0 / (w * x) * 1e12, 4)}
    return {"kind": "inductor_series", "l_nh": _round(x / w * 1e9, 4)}

def _element_from_b(b, w):
    if b is None or b == 0:
        return {"kind": "none"}
    if b > 0:
        return {"kind": "capacitor_shunt", "c_pf": _round(b / w * 1e12, 4)}
    return {"kind": "inductor_shunt", "l_nh": _round(-1.0 / (w * b) * 1e9, 4)}

def l_match(z_load, freq_ghz, z0=50.0):
    """Two-element L match, solved exactly from the load admittance.

    With a shunt element across the load (used when the load's parallel resistance is
    above Z0):

        Y_L = G + jB_L,  add jB:  Re[1/(Y_L+jB)] = Z0  =>  (B+B_L)^2 = G/Z0 - G^2
        then the series X cancels what is left:  X = (B+B_L)/(G^2+(B+B_L)^2)

    With a series element first (used when the load resistance is below Z0):

        R + j(X+X_L),  1/(R+jX_tot) must be real after the shunt:  X_tot^2 = R*Z0 - R^2

    Both branches give two solutions (the two signs), and both are returned: they
    differ in whether the network starts inductive or capacitive, which decides
    which one is physically buildable with the parts on hand.
    """
    try:
        ZL = _to_complex(z_load)
        f = float(freq_ghz)
        Z0 = float(z0)
    except (TypeError, ValueError) as exc:
        return err(str(exc), "ARG_INVALID")
    if f <= 0 or Z0 <= 0:
        return err("freq_ghz and z0 must be > 0", "ARG_RANGE")
    if ZL.real <= 0:
        return err("the load resistance must be > 0", "ARG_RANGE")
    w = 2.0 * math.pi * f * 1e9
    R, X = ZL.real, ZL.imag
    solutions = []

    YL = 1.0 / ZL
    G, BL = YL.real, YL.imag
    if G <= 1.0 / Z0:                       # parallel resistance >= Z0
        rad = G / Z0 - G * G
        if rad < 0:
            return err("no passive L solution for this load", "NO_SOLUTION")
        for sign in (1.0, -1.0):
            btot = sign * math.sqrt(rad)
            b = btot - BL
            den = G * G + btot * btot
            x = btot / den
            solutions.append({
                "topology": "shunt_across_load_then_series",
                "shunt": _element_from_b(b, w),
                "series": _element_from_x(x, w),
                "series_x_ohm": _round(x, 4),
                "shunt_b_siemens": _round(b, 6),
                "loaded_q": _round(abs(btot) / G, 4),
            })
    else:                                    # series resistance < Z0
        rad = R * Z0 - R * R
        if rad < 0:
            return err("no passive L solution for this load", "NO_SOLUTION")
        for sign in (1.0, -1.0):
            xtot = sign * math.sqrt(rad)
            x = xtot - X
            den = R * R + xtot * xtot
            # the shunt element cancels the susceptance of (R + j*xtot), which is
            # -xtot/den, so the element itself must carry +xtot/den. With the sign
            # the wrong way round the recombination below lands on R/2 + j*R, i.e.
            # a residual VSWR of 0.71 on a 25 ohm load - which is how this was
            # caught: every solution is recombined and its residual reported.
            b = xtot / den
            solutions.append({
                "topology": "series_with_load_then_shunt",
                "series": _element_from_x(x, w),
                "shunt": _element_from_b(b, w),
                "series_x_ohm": _round(x, 4),
                "shunt_b_siemens": _round(b, 6),
                "loaded_q": _round(abs(xtot) / R, 4),
            })

    # prove it: recombine each solution and report the residual
    for s in solutions:
        x = s["series_x_ohm"] or 0.0
        b = s["shunt_b_siemens"] or 0.0
        if s["topology"].startswith("shunt"):
            zin = 1.0 / (YL + complex(0.0, b)) + complex(0.0, x)
        else:
            zin = 1.0 / (1.0 / (ZL + complex(0.0, x)) + complex(0.0, b))
        s["resulting_z_ohm"] = {"real": _round(zin.real, 5), "imag": _round(zin.imag, 5)}
        s["residual_vswr"] = _round(abs((zin - Z0) / (zin + Z0)), 6)

    return ok(
        value={"z_load_ohm": {"real": _round(R, 4), "imag": _round(X, 4)},
               "freq_ghz": f, "z0_ohm": Z0, "solutions": solutions},
        unit="ohm",
        model="l_match_exact",
        basis=("exact algebra on complex impedances: with a shunt element first, "
               "(B+B_L)^2 = G/Z0 - G^2; with a series element first, "
               "X_tot^2 = R*Z0 - R^2. Each solution is recombined and the residual "
               "VSWR is reported, so the arithmetic is checked rather than claimed"),
        precision=("exact for ideal components. Component Q, tolerance, pad "
                   "capacitance and the ground return path all move this: a real "
                   "2.4 GHz L match on FR4 lands within a few percent of the ideal "
                   "values but usually needs one round of tuning"),
        assumptions={"freq_ghz": f, "z0_ohm": Z0,
                     "reactance_sign": "positive = inductive (series L or shunt C is "
                                       "negative B)",
                     "bandwidth": ("not a design parameter of an L match: it is set by "
                                   "the loaded Q reported per solution")},
    )

# -------------------------------------------------------------- pi / T network
def pi_match(rsource, rload, freq_ghz, q=None):
    """Pi network (shunt-series-shunt) between two real terminations at a given Q.

    Built from two back-to-back L sections through a virtual series resistance
    Rv < min(Rs, Rl):

        Q1 = sqrt(Rs/Rv - 1),  Q2 = sqrt(Rl/Rv - 1),  Q = (Q1+Q2)/2
        Xp1 = Rs/Q1,  Xs = Rv*(Q1+Q2),  Xp2 = Rl/Q2

    Q is solved for Rv by bisection, so the Q that comes back is the Q that was
    asked for. A pi network is what you reach for when the load needs a low-pass
    shape and a controllable bandwidth; a T when it needs a high-pass shape.
    """
    return _lc_network("pi", rsource, rload, freq_ghz, q)

def t_match(rsource, rload, freq_ghz, q=None):
    """T network (series-shunt-series), the dual of the pi network."""
    return _lc_network("t", rsource, rload, freq_ghz, q)

def _lc_network(shape, rsource, rload, freq_ghz, q):
    try:
        rs = float(rsource)
        rl = float(rload)
        f = float(freq_ghz)
    except (TypeError, ValueError):
        return err("numeric arguments required", "ARG_INVALID")
    if rs <= 0 or rl <= 0 or f <= 0:
        return err("all resistances and freq_ghz must be > 0", "ARG_RANGE")
    w = 2.0 * math.pi * f * 1e9
    rmin, rmax = min(rs, rl), max(rs, rl)

    if q is None:
        # the minimum possible Q for this pair: Rv at the edge of its range
        q = math.sqrt(rmax / rmin - 1.0)
    try:
        q = float(q)
    except (TypeError, ValueError):
        return err("q must be numeric", "ARG_INVALID")
    if q < math.sqrt(rmax / rmin - 1.0) - 1e-9:
        return err("Q = %.3f is below the minimum %.3f this pair allows"
                   % (q, math.sqrt(rmax / rmin - 1.0)), "ARG_RANGE",
                   hint="raise Q, or use an L match which has no Q freedom")

    if shape == "pi":
        # Rv < rmin, and Q = (Q1+Q2)/2 grows as Rv falls
        lo, hi = 1e-9 * rmin, rmin * (1.0 - 1e-12)
        def q_of(rv):
            return 0.5 * (math.sqrt(rs / rv - 1.0) + math.sqrt(rl / rv - 1.0))
        for _ in range(200):
            mid = 0.5 * (lo + hi)
            if q_of(mid) > q:
                lo = mid
            else:
                hi = mid
        rv = 0.5 * (lo + hi)
        q1 = math.sqrt(rs / rv - 1.0)
        q2 = math.sqrt(rl / rv - 1.0)
        xp1, xp2 = rs / q1, rl / q2
        xs = rv * (q1 + q2)
        # A shunt element with reactance magnitude X is realised by the same part a
        # series element of reactance -X would need (shunt C: B = wC = 1/X;
        # shunt L: B = -1/(wL) = -1/X), so -X is fed to the series helper and the
        # result is renamed. With Xp > 0 this yields the low-pass form: series
        # inductor, shunt capacitors - the standard pi configuration.
        elements = {"shunt_source_side": _element_from_x(-xp1, w),
                    "series": _element_from_x(xs, w),
                    "shunt_load_side": _element_from_x(-xp2, w)}
        for key in ("shunt_source_side", "shunt_load_side"):
            e = elements[key]
            if e.get("kind", "").startswith("capacitor"):
                e["kind"] = "capacitor_shunt"
            elif e.get("kind", "").startswith("inductor"):
                e["kind"] = "inductor_shunt"
    else:
        # Rv > rmax, and Q = (Q1+Q2)/2
        lo, hi = rmax * (1.0 + 1e-12), rmax * 1e9
        def q_of(rv):
            return 0.5 * (math.sqrt(rv / rs - 1.0) + math.sqrt(rv / rl - 1.0))
        for _ in range(200):
            mid = 0.5 * (lo + hi)
            if q_of(mid) < q:
                lo = mid
            else:
                hi = mid
        rv = 0.5 * (lo + hi)
        q1 = math.sqrt(rv / rs - 1.0)
        q2 = math.sqrt(rv / rl - 1.0)
        elements = {"series_source_side": _element_from_x(q1 * rs, w),
                    "shunt": _element_from_x(-rv / (q1 + q2), w),
                    "series_load_side": _element_from_x(q2 * rl, w)}
        e = elements["shunt"]
        if e.get("kind", "").startswith("capacitor"):
            e["kind"] = "capacitor_shunt"
        elif e.get("kind", "").startswith("inductor"):
            e["kind"] = "inductor_shunt"

    return ok(
        value={"shape": shape, "r_source_ohm": rs, "r_load_ohm": rl,
               "freq_ghz": f, "q": _round(q, 4),
               "virtual_resistance_ohm": _round(rv, 5),
               "elements": elements},
        unit="ohm",
        model=shape + "_match_exact",
        basis=("two back-to-back L sections through a virtual resistance Rv: pi uses "
               "Rv < min(Rs,Rl) with Xs = Rv*(Q1+Q2), Xp = R/Q; T uses Rv > max(Rs,Rl) "
               "with Xs = Q*R and Xp = Rv/(Q1+Q2); Q = (Q1+Q2)/2, solved for Rv by "
               "bisection so the returned Q is the requested one. The parts come out "
               "in the LOW-PASS form (series inductor, shunt capacitors); the "
               "high-pass dual is the same network with every reactance negated"),
        precision=("exact for ideal lossless elements and an all-real termination. A "
                   "pi/T network is designed for a bandwidth, and the loss of real "
                   "inductors (Q of 20-80 at 2.4 GHz) is what usually decides whether "
                   "the network is usable - that loss is NOT in this answer"),
        assumptions={"q": q, "shape": shape,
                     "terminations": "both purely resistive: a complex load has to be "
                                     "absorbed into the nearest element or matched first"},
    )

# ------------------------------------------------------------- stubs and lambda/4
def single_stub(z_load, freq_ghz, z0=50.0, stub="shunt", termination="open"):
    """Single-stub tuner: where the stub goes, and how long it is.

    The distance d to the stub is chosen so that the line admittance there has a
    real part of exactly 1/Z0; the stub then cancels the remaining susceptance.
    Closed form, no search:

        G(d) = r(1+t^2)/(r^2+(x+t)^2) = 1,   t = tan(beta*d),  z_L = r+jx

    which is a quadratic in t for r != 1 and linear for r = 1. Both roots are real
    and both are physical (the two places along the line where this works), so both
    are returned with the shorter stub preferred.
    """
    stub = str(stub or "").strip().lower()
    termination = str(termination or "").strip().lower()
    if stub not in ("shunt", "series"):
        return err("stub must be shunt or series", "ARG_INVALID")
    if termination not in ("open", "short"):
        return err("termination must be open or short", "ARG_INVALID")
    try:
        ZL = _to_complex(z_load)
        f = float(freq_ghz)
        Z0 = float(z0)
    except (TypeError, ValueError) as exc:
        return err(str(exc), "ARG_INVALID")
    if f <= 0 or Z0 <= 0 or ZL.real <= 0:
        return err("freq_ghz and z0 must be > 0 and the load resistance must be > 0",
                   "ARG_RANGE")
    lam = C0_MM_PER_NS / f
    if stub == "series":
        # a series stub is the dual problem: work on the admittance of the load
        return err("a series stub needs the dual formulation and this tool only "
                   "carries the shunt case", "NOT_IMPLEMENTED",
                   hint="model it as a shunt stub on the admittance of the load")
    r, x = ZL.real / Z0, ZL.imag / Z0
    roots = []
    if abs(r - 1.0) < 1e-12:
        if abs(x) < 1e-12:
            return err("the load is already 1.0 + j0: no stub needed", "NO_SOLUTION")
        roots = [-x / 2.0]
    else:
        disc = x * x + (r - 1.0) * (x * x + r * r - r)
        if disc < 0:
            return err("no stub position exists for this load at this Z0",
                       "NO_SOLUTION")
        s = math.sqrt(disc)
        for sign in (1.0, -1.0):
            roots.append((x + sign * s) / (r - 1.0))
    out = []
    for t in roots:
        t = t if t != 0 else 1e-12
        beta_d = math.atan(t)
        if beta_d < 0:
            beta_d += math.pi
        d = beta_d / (2.0 * math.pi) * lam
        den = r * r + (x + t) ** 2
        b_stub = ((x + t) * (1.0 - x * t) - r * r * t) / den / Z0   # -Im(Y(d))/...
        if termination == "open":
            # B = tan(beta*l)/Z0  ->  tan(beta*l) = B*Z0
            tan_bl = b_stub * Z0
        else:
            # short: B = -1/(Z0*tan(beta*l))  ->  tan(beta*l) = -1/(B*Z0)
            if abs(b_stub) < 1e-15:
                tan_bl = 0.0
            else:
                tan_bl = -1.0 / (b_stub * Z0)
        beta_l = math.atan(tan_bl)
        if beta_l <= 0:
            beta_l += math.pi
        l = beta_l / (2.0 * math.pi) * lam
        out.append({"distance_from_load_mm": _round(d, 4),
                    "stub_length_mm": _round(l, 4),
                    "stub_reactance_ohm": _round(1.0 / b_stub if b_stub else None, 4),
                    "stub_susceptance_siemens": _round(b_stub, 6)})
    out.sort(key=lambda s: s["stub_length_mm"])
    return ok(
        value={"z_load_ohm": {"real": _round(ZL.real, 4), "imag": _round(ZL.imag, 4)},
               "lambda_mm": _round(lam, 4), "stub": stub,
               "termination": termination, "solutions": out},
        unit="mm",
        model="single_stub_shunt",
        basis=("closed form: the stub distance makes Re{Y(d)} = 1/Z0 with "
               "G(d) = r(1+t^2)/(r^2+(x+t)^2), t = tan(beta*d); the stub length then "
               "cancels the remaining susceptance (open: B = tan(beta*l)/Z0, short: "
               "B = -1/(Z0*tan(beta*l))). The reactance reported is 1/B, i.e. the "
               "stub seen as a shunt reactance"),
        precision=("exact for a lossless line and an ideal stub; the answer assumes the "
                   "line impedance between the load and the stub equals z0 exactly, "
                   "which on a real board is the number to verify with "
                   "feedline_impedance first"),
        assumptions={"freq_ghz": f, "z0_ohm": Z0, "stub": stub,
                     "termination": termination,
                     "line": "the same z0 from the load to the stub and beyond"},
    )

def quarter_wave(z_load, freq_ghz, z0=50.0, er_eff=1.0):
    """Quarter-wave transformer: the section impedance, length, and its real limits.

    Z0' = sqrt(Z0 * R_load) and the section is lambda_g/4 long. Only the real part of
    the load can be transformed: a reactive load leaves the reactance in place, which
    is why this refuses a load with significant reactance instead of quietly ignoring
    it.

    The bandwidth caveat is stated rather than number-faked: a single section is
    practical up to a resistance ratio of roughly 4:1; beyond that the reflection
    grows too fast away from the centre frequency and a multi-section or a taper is
    the right answer (neither is implemented here).
    """
    try:
        ZL = _to_complex(z_load)
        f = float(freq_ghz)
        Z0 = float(z0)
        ee = float(er_eff)
    except (TypeError, ValueError) as exc:
        return err(str(exc), "ARG_INVALID")
    if f <= 0 or Z0 <= 0 or ZL.real <= 0 or ee < 1.0:
        return err("freq_ghz, z0 and the load resistance must be > 0; er_eff >= 1",
                   "ARG_RANGE")
    if abs(ZL.imag) > 0.1 * ZL.real:
        return err("the load has %.1f ohm of reactance at this frequency, which a "
                   "quarter-wave section cannot remove" % ZL.imag, "OUT_OF_VALIDITY",
                   hint="tune the reactance out first (series or shunt element), then "
                        "transform the resistance")
    zp = math.sqrt(Z0 * ZL.real)
    lam = C0_MM_PER_NS / f / math.sqrt(ee)
    ratio = max(ZL.real, Z0) / min(ZL.real, Z0)
    out = {
        "section_z0_ohm": _round(zp, 4),
        "section_length_mm": _round(lam / 4.0, 4),
        "lambda_g_mm": _round(lam, 4),
        "resistance_ratio": _round(ratio, 4),
    }
    if ratio > 4.0:
        out["bandwidth_warning"] = (
            "R2/R1 = %.1f is above the ~4:1 where a single section is practical: the "
            "match will be narrow and sensitive to the section impedance tolerance. "
            "A multi-section or tapered transformer is the correct answer and is NOT "
            "implemented here" % ratio)
    else:
        out["bandwidth_note"] = (
            "narrowband by nature: the 2:1 VSWR bandwidth of a single section is "
            "roughly proportional to 1/ln(R2/R1), and it is not computed here because "
            "the useful number depends on the tolerance you can hold on the section "
            "impedance")
    return ok(
        value=out,
        unit="ohm",
        model="quarter_wave_transformer",
        basis="Z0' = sqrt(Z0*R_load), length = lambda_g/4 with lambda_g = c/(f*sqrt(er_eff))",
        precision="exact at the design frequency for an ideal section; a real section's "
                  "impedance tolerance is the dominant error, and the bandwidth is "
                  "narrow (see the warning)",
        assumptions={"freq_ghz": f, "z0_ohm": Z0, "er_eff": ee,
                     "load": "resistive part transformed; reactance must be removed "
                             "first"},
    )

# ---------------------------------------------------------- bandwidth by Q
def bandwidth(q=None, freq_ghz=None, bandwidth_hz=None, vswr=None):
    """Fractional bandwidth of a resonant match, and the Q it implies.

    Source: rf-circuit-design / SKILL.md 4.3 - "S11 is fine at the centre and the
    bandwidth is narrow" is the symptom of an excessive loaded Q, and the standard
    relation between them is BW = f0 / Q for a single-resonance match, i.e.
    Q = f0 / BW. It is a resonance relation, not a filter synthesis rule: a
    multi-resonator match does NOT follow it, and neither does a node whose
    bandwidth is set by the load rather than by the matching network.
    """
    if q is None and bandwidth_hz is None:
        return err("give q (loaded Q) or bandwidth_hz", "ARG_MISSING",
                   hint="BW = f0/Q needs two of the three numbers f0, BW, Q")
    try:
        f = None if freq_ghz is None else float(freq_ghz)
        Q = None if q is None else float(q)
        b = None if bandwidth_hz is None else float(bandwidth_hz)
    except (TypeError, ValueError):
        return err("numeric arguments required", "ARG_INVALID")
    if f is not None and f <= 0:
        return err("freq_ghz must be > 0", "ARG_RANGE")
    if Q is not None and Q <= 0:
        return err("q must be > 0", "ARG_RANGE")
    if b is not None and b <= 0:
        return err("bandwidth_hz must be > 0", "ARG_RANGE")

    out = {}
    if Q is not None and f is not None:
        b = f * 1e9 / Q
    elif b is not None and f is not None and Q is None:
        Q = f * 1e9 / b
    elif Q is not None and b is not None and f is None:
        f = Q * b / 1e9
    else:
        return err("two of freq_ghz / q / bandwidth_hz are needed", "ARG_MISSING")

    out["freq_ghz"] = _round(f, 6)
    out["q"] = _round(Q, 4)
    out["bandwidth_hz"] = _round(b, 3)
    out["fractional_bandwidth"] = _round(b / (f * 1e9), 6)
    if vswr is not None:
        try:
            s = float(vswr)
        except (TypeError, ValueError):
            return err("vswr must be numeric", "ARG_INVALID")
        if s <= 1.0:
            return err("vswr must be > 1", "ARG_RANGE")
        # the classic single-pole relation between the VSWR circle and loaded Q
        out["bandwidth_at_vswr_hz"] = _round((f * 1e9 / Q) * ((s - 1.0) / math.sqrt(s)), 3)
        out["vswr"] = s
        out["bandwidth_at_vswr_note"] = (
            "multiplied by (S-1)/sqrt(S): the width of the VSWR <= S circle for a "
            "single resonance. For S = 2 that factor is 0.707, so the 2:1 band is "
            "about 70 % of the BW = f0/Q band")
    out["what_it_does_not_say"] = (
        "the shape of S11 inside the band, the effect of component tolerance or Q, "
        "and any multi-resonator behaviour - a two-section match can be flatter "
        "and wider than this relation suggests")
    return ok(
        value=out,
        unit="bandwidth",
        model="single_resonance_q_bandwidth",
        basis="BW = f0/Q (loaded Q of a single resonance); VSWR band scaled by (S-1)/sqrt(S)",
        precision="engineering approximation: exact only for a single-resonant, "
                  "lossless match with the loaded Q as stated",
        assumptions={"single_resonance": True,
                     "q_source": "the caller's loaded Q, not a computed one"},
    )
