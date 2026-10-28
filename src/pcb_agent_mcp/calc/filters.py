"""Microstrip and cavity filter synthesis: the ADS-shaped half of the toolbox.

The four questions a microwave designer asks a line calculator, and what this
module answers for each:

  1. "Give me the prototype for n poles at R dB"            -> prototype_gvalues
  2. "Turn that into a lumped LC bandpass and show me S21"   -> lumped_bandpass
  3. "Give me w and s for this odd/even impedance pair"      -> coupled_microstrip
  4. "Turn that into an edge-coupled resonator BPF"          -> coupled_resonator_bpf
  and the cavity side: TE101 length, Qc, R/Q                  -> waveguide_cavity

Two rules the whole module is built around:

  * **No closed form is trusted alone.** The lumped response is computed from the
    element values through an ABCD cascade and then compared, point by point,
    with the prototype's own |S21|^2 = 1/(1+eps^2 Tn^2) - two derivations that
    share no code path. A sign error in the impedance scaling cannot survive
    that comparison; it can survive a table.
  * **Geometry comes from the field solver, not from a curve fit.** The coupled
    pair is inverted against calc/field2d.py (2-D finite volume), and the answer
    carries `achieved_z0e_ohm` / `achieved_z0o_ohm` measured at the returned
    geometry, so "the synthesis missed" is visible instead of implied.

What is deliberately NOT here: iris/coupling-window geometry for a waveguide
filter (needs a full-wave solve - the same boundary this project draws
everywhere else), microstrip dispersion, T-junction and open-end reactances
beyond the one open-end length correction, finite-Q insertion loss, and any
layout output. Each answer says so in its own `not_computed` list.
"""
import math

from .result import ok, err

C0 = 299792458.0                 # m/s, exact by definition
EPS0 = 8.8541878128e-12          # F/m
MU0 = 1.25663706212e-6           # H/m
ETA0 = math.sqrt(MU0 / EPS0)     # 376.730 ohm

RESPONSES = ("butterworth", "chebyshev")

# Two independent derivations of the same insertion loss must agree to this many
# decibels, or the element synthesis is wrong and the answer is withheld.
RESPONSE_AGREEMENT_DB = 0.02

NOT_COMPUTED = [
    "waveguide iris / coupling-window geometry: needs a full-wave solve, so a cavity "
    "filter's coupling is not dimensioned here (only the resonator itself is)",
    "microstrip dispersion (Kirschning-Jansen or full-wave): the geometry is "
    "quasi-static, so above roughly f0*h < 0.1 GHz*mm the effective permittivity it "
    "reports is optimistically low",
    "T-junction, step and open-end equivalent circuits other than the single "
    "Hammerstad open-end length correction used for the resonator length",
    "finite unloaded Q: every response here is lossless, so a real filter's insertion "
    "loss is missing (coupling coefficients and Qe are geometric, not measured)",
    "cross-coupling between non-adjacent resonators (folded or canonical topologies)",
    "EM-simulated S-parameters: no full-wave engine is invoked anywhere in this module",
]


# ------------------------------------------------------------------ prototype
def _chebyshev_T(order, x):
    """Chebyshev polynomial of the first kind, stable outside |x|<1."""
    if x >= 1.0:
        return math.cosh(order * math.acosh(x))
    if x <= -1.0:
        return (-1.0) ** order * math.cosh(order * math.acosh(-x))
    return math.cos(order * math.acos(x))


def _epsilon(ripple_db):
    return math.sqrt(10.0 ** (ripple_db / 10.0) - 1.0)


def _prototype(order, response, ripple_db):
    """Lowpass prototype g1..g(n+1), in the standard g0 = 1, wc = 1 normalisation."""
    if response == "butterworth":
        g = [2.0 * math.sin((2.0 * k - 1.0) * math.pi / (2.0 * order))
             for k in range(1, order + 1)]
        g.append(1.0)
        return g
    # Chebyshev: Matthaei's recursion (the same numbers the tables carry)
    beta = math.log(1.0 / math.tanh(ripple_db / 17.37))
    gamma = math.sinh(beta / (2.0 * order))
    a = [math.sin((2.0 * k - 1.0) * math.pi / (2.0 * order))
         for k in range(1, order + 1)]
    b = [gamma * gamma + math.sin(k * math.pi / order) ** 2
         for k in range(1, order + 1)]
    g = [2.0 * a[0] / gamma]
    for k in range(2, order + 1):
        g.append(4.0 * a[k - 2] * a[k - 1] / (b[k - 2] * g[-1]))
    if order % 2:
        g.append(1.0)
    else:
        # even order ends on a series resonator whose termination is not 1:
        # this is the line every table has and every hand derivation forgets
        g.append((1.0 / math.tanh(beta / 4.0)) ** 2)
    return g


def prototype_gvalues(order, response="chebyshev", ripple_db=None):
    """The lowpass prototype element values, with the insertion loss they imply.

    ripple_db=None means "the natural value for this response": 0.5 dB for a
    Chebyshev, 0 for a Butterworth. Passing 0.5 dB with response='butterworth' is
    a conflict, not a silent no-op - a Butterworth with ripple in the name is the
    kind of thing that reaches a fab drawing.
    """
    try:
        n = int(order or 0)
    except (TypeError, ValueError):
        return err("order must be an integer", "ARG_INVALID")
    resp = str(response or "").strip().lower()
    if resp not in RESPONSES:
        return err("response must be one of " + ", ".join(RESPONSES), "ARG_INVALID")
    try:
        ripple = (0.0 if resp == "butterworth" else 0.5) if ripple_db is None \
            else float(ripple_db)
    except (TypeError, ValueError):
        return err("ripple_db must be a number", "ARG_INVALID")
    if resp not in RESPONSES:
        return err("response must be one of " + ", ".join(RESPONSES), "ARG_INVALID")
    if n < 1 or n > 20:
        return err("order must be 1..20; above that the element spread is wider than "
                   "any realisable part and the recursion loses digits", "ARG_RANGE")
    if resp == "butterworth" and ripple != 0.0:
        return err("a Butterworth prototype has no ripple; ripple_db must be 0 or "
                   "omitted", "ARG_CONFLICT",
                   hint="ask for response='chebyshev' if you meant a ripple")
    if resp == "chebyshev" and ripple <= 0.0:
        return err("a Chebyshev prototype needs ripple_db > 0 (it degenerates into "
                   "Butterworth at 0)", "ARG_RANGE")

    g = _prototype(n, resp, ripple)
    return ok(
        value={"order": n, "response": resp,
               "ripple_db": ripple if resp == "chebyshev" else 0.0,
               "g": [round(x, 6) for x in g],
               "epsilon": (round(_epsilon(ripple), 6) if resp == "chebyshev" else 1.0)},
        unit="dimensionless",
        model="lumped lowpass prototype (g0 = 1, wc = 1, Rs = RL = 1)",
        basis=("Matthaei/Young/Jones 'Microwave Filters, Impedance-Matching Networks "
               "and Coupling Structures' prototype tables: Butterworth "
               "g_k = 2 sin((2k-1)pi/2n); Chebyshev via the beta/gamma recursion"),
        precision=("exact arithmetic in double precision; the tables this is checked "
                   "against quote four decimals, and the tests compare against those "
                   "four decimals"),
        assumptions={"normalisation": "source and load both 1 ohm, cutoff 1 rad/s"},
        extra={"note": "g(n+1) is the load, not an element; for even-order Chebyshev "
                       "it is not 1 - the network ends on a series resonator"})


# ------------------------------------------------------------- lumped bandpass
def _abcd_cascade(sections):
    """(A, B, C, D) of series-impedance / shunt-admittance sections in order."""
    A, B, C, D = 1.0 + 0.0j, 0.0 + 0.0j, 0.0 + 0.0j, 1.0 + 0.0j
    for kind, value in sections:
        if kind == "series":
            a, b, c, d = 1.0 + 0.0j, value, 0.0 + 0.0j, 1.0 + 0.0j
        else:
            a, b, c, d = 1.0 + 0.0j, 0.0 + 0.0j, value, 1.0 + 0.0j
        A, B, C, D = (A * a + B * c, A * b + B * d,
                      C * a + D * c, C * b + D * d)
    return A, B, C, D


def _s21(abcd, z0):
    A, B, C, D = abcd
    denom = A + B / z0 + C * z0 + D
    s21 = 2.0 / denom
    s11 = (A + B / z0 - C * z0 - D) / denom
    return s11, s21


def _prototype_il_db(n, resp, ripple, omega):
    """Ideal insertion loss of the prototype at normalised frequency `omega`."""
    if resp == "butterworth":
        return 10.0 * math.log10(1.0 + omega ** (2 * n))
    eps = _epsilon(ripple)
    return 10.0 * math.log10(1.0 + (eps * _chebyshev_T(n, omega)) ** 2)


def lumped_bandpass(order, f0_hz, fbw, z0_ohm=50.0, ripple_db=None,
                    response="chebyshev", points=31):
    """LC ladder bandpass from the prototype, with the response it actually gives.

    The element values come from the lowpass-to-bandpass transform; the S21 that
    is reported comes from cascading those elements as ABCD matrices. The ideal
    prototype formula is evaluated at the same frequencies and the two are
    compared - that comparison is the answer's own audit, not decoration.
    """
    proto = prototype_gvalues(order, response, ripple_db)
    if proto["status"] != "ok":
        return proto
    try:
        f0 = float(f0_hz)
        bw = float(fbw)
        z0 = float(z0_ohm)
        npts = int(points or 31)
    except (TypeError, ValueError):
        return err("numeric f0_hz, fbw, z0_ohm required", "ARG_INVALID")
    if f0 <= 0 or z0 <= 0:
        return err("f0_hz and z0_ohm must be > 0", "ARG_RANGE")
    if bw <= 0.0 or bw >= 1.0:
        return err("fbw is the fractional bandwidth (passband/centre, 0 < fbw < 1)",
                   "ARG_RANGE",
                   hint="fbw = 0.1 means a 10 %% band; 0.05 or narrower is where an "
                        "edge-coupled microstrip filter starts to be practical")
    if npts < 5 or npts > 2001:
        return err("points must be 5..2001", "ARG_RANGE")

    n = int(order)
    g = proto["value"]["g"]
    resp = proto["value"]["response"]
    ripple = proto["value"]["ripple_db"]
    w0 = 2.0 * math.pi * f0

    # lowpass -> bandpass, each g_k becomes a resonator; odd k shunt (parallel LC),
    # even k series (series LC). The impedance scaling is the part that is easy to
    # get backwards: series impedance scales with z0, shunt admittance with 1/z0.
    elements = []
    for k in range(1, n + 1):
        gk = g[k - 1]
        if k % 2:
            C = gk / (bw * w0 * z0)
            L = bw * z0 / (w0 * gk)
            elements.append({"k": k, "kind": "shunt",
                             "C_pf": round(C * 1e12, 6),
                             "L_nh": round(L * 1e9, 6),
                             "f_self_res_hz": round(1.0 / (2 * math.pi * math.sqrt(L * C)), 3)})
        else:
            L = gk * z0 / (bw * w0)
            C = bw / (w0 * z0 * gk)
            elements.append({"k": k, "kind": "series",
                             "C_pf": round(C * 1e12, 6),
                             "L_nh": round(L * 1e9, 6),
                             "f_self_res_hz": round(1.0 / (2 * math.pi * math.sqrt(L * C)), 3)})

    def sections_at(f):
        w = 2.0 * math.pi * f
        out = []
        for e in elements:
            L, C = e["L_nh"] * 1e-9, e["C_pf"] * 1e-12
            if e["kind"] == "series":
                out.append(("series", 1j * (w * L - 1.0 / (w * C))))
            else:
                out.append(("shunt", 1j * (w * C - 1.0 / (w * L))))
        return out

    def s_at(f):
        return _s21(_abcd_cascade(sections_at(f)), z0)

    def omega_at(f):
        return (f / f0 - f0 / f) / bw

    # The audit: element-cascade S21 against the prototype's closed form. Only
    # the stopband beyond the first transmission zero is skipped, because a ladder
    # of finite resonators is not the prototype there (the prototype is a
    # polynomial, the ladder goes to a null); inside and near the band they must
    # agree to a hair.
    worst = 0.0
    worst_f = None
    f_lo = f0 / max(1e-9, (1.0 + 2.0 * bw))
    f_hi = f0 * (1.0 + 2.0 * bw)
    for i in range(npts):
        f = f_lo + (f_hi - f_lo) * i / (npts - 1)
        om = omega_at(f)
        if abs(om) > 3.0:
            continue
        _s11, s21 = s_at(f)
        got = -20.0 * math.log10(max(abs(s21), 1e-30))
        want = _prototype_il_db(n, resp, ripple, om)
        d = abs(got - want)
        if d > worst:
            worst, worst_f = d, f
    if worst > RESPONSE_AGREEMENT_DB:
        return err("the element cascade and the prototype formula disagree by %.3f dB "
                   "at %.6g Hz, which means the lowpass-to-bandpass transform or the "
                   "impedance scaling is wrong" % (worst, worst_f or 0.0),
                   "INTERNAL_CHECK_FAILED",
                   hint="this is a code defect, not a user error; the answer is "
                        "withheld rather than reported with a caveat")

    # band edges, where the ripple definition lives
    # Omega = +-1 at the band edges: f/f0 - f0/f = +-FBW
    f1 = f0 * (math.sqrt(1.0 + (bw / 2.0) ** 2) - bw / 2.0)
    f2 = f0 * (math.sqrt(1.0 + (bw / 2.0) ** 2) + bw / 2.0)
    edges = {}
    for name, f in (("f_lower_hz", f1), ("f_center_hz", f0), ("f_upper_hz", f2)):
        _s11, s21 = s_at(f)
        edges[name] = round(f, 3)
        edges[name.replace("_hz", "_il_db")] = round(-20.0 * math.log10(max(abs(s21), 1e-30)), 4)

    sweep = []
    for i in range(npts):
        f = f_lo + (f_hi - f_lo) * i / (npts - 1)
        s11, s21 = s_at(f)
        sweep.append({"f_hz": round(f, 6),
                      "s21_db": round(-20.0 * math.log10(max(abs(s21), 1e-30)), 4),
                      "s11_db": round(-20.0 * math.log10(max(abs(s11), 1e-30)), 4)})

    return ok(
        value={"order": n, "response": resp, "ripple_db": ripple,
               "f0_hz": round(f0, 6), "fbw": bw,
               "z0_ohm": z0,
               "band_hz": [round(f1, 3), round(f2, 3)],
               "elements": elements,
               "band_edges": edges,
               "response": sweep,
               "self_check": {"max_abs_deviation_db": round(worst, 6),
                              "tolerance_db": RESPONSE_AGREEMENT_DB,
                              "compared_against": "1/(1+eps^2*Tn^2(lambda))"},
               "prototype_g": proto["value"]["g"]},
        unit="mixed",
        model="lossless LC ladder from the lowpass prototype (ABCD cascade)",
        basis=("lowpass-to-bandpass frequency transform "
               "Omega = (1/FBW)(f/f0 - f0/f) applied to the Matthaei prototype, "
               "impedance-scaled to z0; response from the ABCD cascade"),
        precision=("element values are exact for the lossless model (no series "
                   "resistance, no Q); the response is computed from those same "
                   "elements, so it is the ladder's answer and not a fitted curve"),
        assumptions={"lossless": True,
                     "terminations": "z0 at both ports, no parasitics",
                     "topology": "shunt-first alternating ladder (odd order starts "
                                 "and ends on a shunt resonator)"},
        extra={"not_computed": NOT_COMPUTED,
               "note": "an LC ladder with these values is realisable only if the "
                       "element spread is; compare the largest and smallest C and L "
                       "before trusting a 5-pole 5 % design at 100 MHz",
               "audit": "the cascade and the prototype formula are separate code "
                        "paths; agreement to %.2f dB is what makes the element "
                        "values checkable" % RESPONSE_AGREEMENT_DB})


# --------------------------------------------------- coupled microstrip geometry
def _pair_modes(w, s, h, t, er, fine_div=6.0):
    """(Z0e, Z0o, ee_e, ee_o, nodes) of a symmetric coupled pair from the solver.

    The mesh is tied to the SUBSTRATE height, not to the narrowest of w/s/h: a
    fine grid inside a 0.05 mm gap costs quadratically and buys less than the
    quasi-static model's own error. Measured drift from h/6 to h/12 on a 70/40 ohm
    pair is +0.9 % / +0.7 % (docs/22), which is the number the envelope quotes.
    """
    from . import field2d as _f2
    fine = max(h / max(2.0, float(fine_div)), 1e-6)
    m = _f2._cap_modes("microstrip", w, s, h, t, er, fine)
    z0o = 1.0 / (C0 * math.sqrt(m["c_odd_d"] * EPS0 * m["c_odd_a"] * EPS0))
    z0e = 1.0 / (C0 * math.sqrt(m["c_even_d"] * EPS0 * m["c_even_a"] * EPS0))
    ee_o = m["c_odd_d"] / m["c_odd_a"]
    ee_e = m["c_even_d"] / m["c_even_a"]
    sec = m["sec"]
    return z0e, z0o, ee_e, ee_o, sec.nx * sec.ny


def _solve_width_spacing(z0e_target, z0o_target, h, t, er, fine_div=6.0,
                         tol_pct=0.5, max_iter=10):
    """Quasi-Newton (inverse Broyden) on (ln w, ln s) against the field solver.

    Two unknowns, two equations - but every residual costs a finite-volume solve,
    so the Jacobian is built once by finite differences and then updated by
    Broyden instead of being re-probed: four residuals per design instead of
    twelve. Log space keeps w and s positive without a box constraint; the step is
    damped to at most 50 % so a bad Jacobian cannot throw the geometry into the
    solver's invalid range.
    """
    from . import feedline as _fl

    # start from the single-line width for Z0 = sqrt(Z0e*Z0o) and a gap of h
    z0 = math.sqrt(z0e_target * z0o_target)
    got = _fl.feedline_width(z0, "microstrip", h, copper_um=t * 1000.0, er=er)
    w = (got["value"]["width_mm"] if got["status"] == "ok" else 0.3 * h)
    s = h
    w = min(max(w, 0.05 * h), 20.0 * h)
    s = min(max(s, 0.05 * h), 20.0 * h)

    def residual(w_, s_):
        z0e, z0o, ee_e, ee_o, nodes = _pair_modes(w_, s_, h, t, er, fine_div)
        return ((z0e - z0e_target, z0o - z0o_target),
                (z0e, z0o, ee_e, ee_o, nodes))

    best = None
    hist = []
    e, (z0e, z0o, ee_e, ee_o, nodes) = residual(w, s)
    err_pct = max(abs(e[0]) / z0e_target, abs(e[1]) / z0o_target) * 100.0
    hist.append({"iter": 0, "w_mm": round(w, 6), "s_mm": round(s, 6),
                 "z0e_ohm": round(z0e, 3), "z0o_ohm": round(z0o, 3),
                 "err_pct": round(err_pct, 3), "nodes": nodes})
    best = (err_pct, w, s, z0e, z0o, ee_e, ee_o)
    if err_pct <= tol_pct:
        return best, hist

    # one finite-difference Jacobian, then Broyden: J = [[dZ0e/dw, dZ0e/ds], ...]
    d = 0.05
    ew, _vals = residual(w * (1.0 + d), s)
    es, _vals = residual(w, s * (1.0 + d))
    j11 = (ew[0] - e[0]) / (w * d)
    j12 = (es[0] - e[0]) / (s * d)
    j21 = (ew[1] - e[1]) / (w * d)
    j22 = (es[1] - e[1]) / (s * d)
    det = j11 * j22 - j12 * j21
    if abs(det) < 1e-12:
        return best, hist
    J = [[j11, j12], [j21, j22]]

    for it in range(1, max_iter + 1):
        a_, b_, c_, d_ = J[0][0], J[0][1], J[1][0], J[1][1]
        det = a_ * d_ - b_ * c_
        if abs(det) < 1e-12:
            break
        # Newton step: x = -J^-1 e, in millimetres; converted to a FRACTION of the
        # current value so the damping and the box below are dimensionless (using
        # the millimetre step as if it were a fraction makes Newton crawl at ~3 %
        # per iteration, which looks exactly like a hard-to-reach target)
        dw_mm = -(d_ * e[0] - b_ * e[1]) / det
        ds_mm = -(-c_ * e[0] + a_ * e[1]) / det
        dw = dw_mm / w
        ds = ds_mm / s
        # backtracking: a full step first, halve it while the residual grows. The
        # gap is stiff, so a fixed 50 % damping crawls; a line search keeps the
        # full Newton step when it is good and still cannot run away.
        accepted = None
        for scale in (1.0, 0.5, 0.25):
            w_new = min(max(w * (1.0 + max(-1.5, min(1.5, dw * scale))),
                            0.05 * h), 20.0 * h)
            s_new = min(max(s * (1.0 + max(-0.7, min(0.7, ds * scale))),
                            0.05 * h), 20.0 * h)
            e_new, vals = residual(w_new, s_new)
            err_new = max(abs(e_new[0]) / z0e_target, abs(e_new[1]) / z0o_target) * 100.0
            if err_new < err_pct:
                accepted = (w_new, s_new, e_new, vals, err_new)
                break
            if accepted is None:
                accepted = (w_new, s_new, e_new, vals, err_new)
        w_new, s_new, e_new, vals, err_new = accepted
        z0e, z0o, ee_e, ee_o, nodes = vals
        hist.append({"iter": it, "w_mm": round(w_new, 6), "s_mm": round(s_new, 6),
                     "z0e_ohm": round(z0e, 3), "z0o_ohm": round(z0o, 3),
                     "err_pct": round(err_new, 3), "nodes": nodes})
        if err_new < best[0]:
            best = (err_new, w_new, s_new, z0e, z0o, ee_e, ee_o)
        # good Broyden: J += (df - J dx) dx^T / (dx^T dx)
        dx = (w_new - w, s_new - s)
        df = (e_new[0] - e[0], e_new[1] - e[1])
        jdx = (J[0][0] * dx[0] + J[0][1] * dx[1],
               J[1][0] * dx[0] + J[1][1] * dx[1])
        denom = dx[0] * dx[0] + dx[1] * dx[1]
        if denom > 1e-20:
            J[0][0] += (df[0] - jdx[0]) * dx[0] / denom
            J[0][1] += (df[0] - jdx[0]) * dx[1] / denom
            J[1][0] += (df[1] - jdx[1]) * dx[0] / denom
            J[1][1] += (df[1] - jdx[1]) * dx[1] / denom
        w, s, e, err_pct = w_new, s_new, e_new, err_new
        if err_pct <= tol_pct:
            break
        if len(hist) >= 3:
            recent = [h_["err_pct"] for h_ in hist[-3:]]
            if max(recent) - min(recent) < 1e-4:
                break
    return best, hist


def coupled_microstrip(z0e_ohm, z0o_ohm, h_mm, er=4.3, t_um=35.0,
                       tol_pct=0.5, fine_div=6.0):
    """w and s for a symmetric coupled pair, inverted against the 2-D solver."""
    try:
        ze_t = float(z0e_ohm)
        zo_t = float(z0o_ohm)
        h = float(h_mm)
        er = float(er)
        t = float(t_um or 0.0) / 1000.0
        tol = float(tol_pct)
    except (TypeError, ValueError):
        return err("numeric z0e_ohm, z0o_ohm, h_mm required", "ARG_INVALID")
    if h <= 0 or t < 0 or er < 1.0:
        return err("h_mm must be > 0, t_um >= 0, er >= 1", "ARG_RANGE")
    if not (0.0 < zo_t < ze_t):
        return err("a symmetric coupled pair needs 0 < z0o < z0e (even mode has the "
                   "larger impedance: less capacitance to ground in the magnetic wall)",
                   "ARG_RANGE")
    k = (ze_t - zo_t) / (ze_t + zo_t)
    if k < 0.02:
        return err("coupling coefficient k = (z0e-z0o)/(z0e+z0o) = %.4f is below 0.02: "
                   "the traces are too weakly coupled for the solver to invert "
                   "reliably (and for a filter, too weak to be a resonator coupling)"
                   % k, "OUT_OF_VALIDITY")

    best, hist = _solve_width_spacing(ze_t, zo_t, h, t, er, fine_div=fine_div)
    if best is None:
        return err("the solver did not return a geometry", "INTERNAL_FAILURE")
    err_pct, w, s, z0e, z0o, ee_e, ee_o = best
    k_ach = (z0e - z0o) / (z0e + z0o)
    return ok(
        value={"w_mm": round(w, 6), "s_mm": round(s, 6),
               "z0e_ohm": round(z0e, 3), "z0o_ohm": round(z0o, 3),
               "k_coefficient": round(k_ach, 6),
               "ee_even": round(ee_e, 5), "ee_odd": round(ee_o, 5),
               "target": {"z0e_ohm": ze_t, "z0o_ohm": zo_t, "k": round(k, 6)},
               "worst_error_pct": round(err_pct, 3),
               "iterations": hist,
               "z_diff_ohm": round(2.0 * z0o, 3),
               "z_common_ohm": round(0.5 * z0e, 3)},
        unit="mm",
        model="2-D finite-volume coupled microstrip (field2d), Newton inversion",
        basis=("symmetric coupled microstrip solved from Laplace's equation on the "
               "cross-section (electric wall for the odd mode, magnetic wall for the "
               "even mode), capacitance -> impedance; geometry inverted by damped "
               "Newton in log(w), log(s)"),
        precision=("the solver is quasi-static and first-order in the mesh; the "
                   "reported achieved impedances are what this mesh produced, and the "
                   "drift between two mesh densities is reported by "
                   "field_impedance for the same geometry. Expect ~1-2 %% on w and s, "
                   "which is why the tolerance is a percentage, not a digit count. Measured mesh drift on w=0.2948/s=0.096/h=0.2/er=4.3: Z0e 64.52 (h/3) -> 69.57 (h/6) -> 70.17 (h/12), i.e. h/6 to h/12 is ~+0.9 %/+0.7 %"),
        assumptions={"symmetry": "both traces the same width, gap s edge to edge",
                     "infinite ground": "one ground plane at distance h, no finite "
                                        "plane edges or neighbouring copper",
                     "quasi_static": "no dispersion: valid while f0*h < ~0.1 GHz*mm"},
        extra={"validity_note": ("the solver's own documented weak case is a narrow "
                                 "strip (w/h below 0.5), where the strip-edge "
                                 "singularity is under-resolved") if w / h < 0.5 else None,
               "k_resolution_note": (
                   "the impedances are met to %.2f %%, but k = (z0e-z0o)/(z0e+z0o) is "
                   "a DIFFERENCE of two nearly equal numbers: a 0.5 %% impedance error "
                   "is +-%.4f in k, i.e. %.0f %% of this coupling. Treat k as a "
                   "starting point to be tuned in an EM run, not as a measured number"
                   % (err_pct, 0.01 * math.sqrt(ze_t * zo_t) / (z0e + z0o),
                      100.0 * (0.01 * math.sqrt(ze_t * zo_t)
                               / (z0e + z0o)) / max(k_ach, 1e-9))
                   if abs(100.0 * (k_ach - k) / k) > 3.0 else None),
               "not_computed": NOT_COMPUTED,
               "history_note": "every iteration is reported so a non-converged "
                               "inversion cannot look like a converged one",
               "tolerance_pct": tol})


# ------------------------------------------------- coupled resonator bandpass
def _open_end_extension_mm(w, h, ee):
    """Hammerstad's open-end length correction for a microstrip (mm)."""
    u = w / h
    return 0.412 * h * ((ee + 0.3) / (ee - 0.258)) * ((u + 0.262) / (u + 0.813))


def coupled_resonator_bpf(order, f0_hz, fbw, z0_ohm=50.0, ripple_db=None,
                          response="chebyshev", h_mm=0.2, er=4.3, t_um=35.0,
                          tol_pct=1.0):
    """Edge-coupled half-wave resonator filter: Qe, k_ij and the geometry for each.

    The electrical design (Qe, k) is textbook and exact for the prototype; the
    microstrip realisation is the approximation, so every coupling carries the
    achieved k measured at the geometry actually returned.
    """
    proto = prototype_gvalues(order, response, ripple_db)
    if proto["status"] != "ok":
        return proto
    try:
        f0 = float(f0_hz)
        bw = float(fbw)
        z0 = float(z0_ohm)
        h = float(h_mm)
        er = float(er)
        t = float(t_um or 0.0) / 1000.0
    except (TypeError, ValueError):
        return err("numeric f0_hz, fbw, z0_ohm, h_mm required", "ARG_INVALID")
    if f0 <= 0 or h <= 0 or er < 1.0:
        return err("f0_hz and h_mm must be > 0, er >= 1", "ARG_RANGE")
    if bw <= 0.0 or bw >= 0.5:
        return err("fbw must be 0 < fbw < 0.5", "ARG_RANGE")
    n = int(order)
    g = proto["value"]["g"]
    resp = proto["value"]["response"]
    ripple = proto["value"]["ripple_db"]

    # external Q at the two ends and the inter-resonator couplings
    qe_in = g[0] / bw
    qe_out = (g[n - 1] * g[n] / bw)
    couplings = []
    for i in range(1, n):
        couplings.append({"between": [i, i + 1],
                          "k": round(bw / math.sqrt(g[i - 1] * g[i]), 6)})

    sections = []
    for c in couplings:
        k = c["k"]
        ze = z0 * math.sqrt((1.0 + k) / (1.0 - k))
        zo = z0 * math.sqrt((1.0 - k) / (1.0 + k))
        geo = coupled_microstrip(ze, zo, h, er=er, t_um=t * 1000.0,
                                 tol_pct=tol_pct)
        if geo["status"] != "ok":
            c["geometry"] = None
            c["geometry_note"] = geo.get("message")
            sections.append(c)
            continue
        c["target_z0e_ohm"] = round(ze, 3)
        c["target_z0o_ohm"] = round(zo, 3)
        c["w_mm"] = geo["value"]["w_mm"]
        c["s_mm"] = geo["value"]["s_mm"]
        c["achieved_k"] = geo["value"]["k_coefficient"]
        c["k_error_pct"] = round(100.0 * (geo["value"]["k_coefficient"] - k) / k, 3)
        c["meets_target"] = abs(c["k_error_pct"]) <= max(3.0, tol_pct)
        if not c["meets_target"]:
            c["k_note"] = ("the quasi-static geometry resolves the two impedances to a "
                           "fraction of a percent but not their DIFFERENCE; this "
                           "coupling is too weak for the mesh to pin down, so expect "
                           "to tune s on the bench or in an EM run")
        c["z0e_ohm"] = geo["value"]["z0e_ohm"]
        c["z0o_ohm"] = geo["value"]["z0o_ohm"]
        sections.append(c)

    # resonator length: half a guided wavelength at the resonator's own width
    # (sized for z0 by the same Hammerstad model the rest of the server uses),
    # shortened by an open-end correction at each end.
    from . import feedline as _fl
    wgot = _fl.feedline_width(z0, "microstrip", h, copper_um=t * 1000.0, er=er)
    w_feed = (wgot["value"]["width_mm"] if wgot.get("status") == "ok" else 0.3 * h)
    line = _fl.feedline_impedance("microstrip", w_feed, h, copper_um=t * 1000.0,
                                  er=er, freq_ghz=f0 / 1e9)
    if line["status"] == "ok":
        ee = line["value"]["eps_eff"]
        lam_g_mm = line["value"]["lambda_g_mm"]
    else:
        ee = (er + 1.0) / 2.0
        lam_g_mm = C0 / (f0 * math.sqrt(ee)) * 1000.0
    dl = _open_end_extension_mm(w_feed, h, ee)
    length = lam_g_mm / 2.0 - 2.0 * dl

    return ok(
        value={"order": n, "response": resp, "ripple_db": ripple,
               "f0_hz": round(f0, 6), "fbw": bw, "z0_ohm": z0,
               "q_external": {"in": round(qe_in, 4), "out": round(qe_out, 4)},
               "couplings": sections,
               "resonator": {"lambda_g_mm": round(lam_g_mm, 4),
                             "open_end_extension_mm": round(dl, 4),
                             "length_mm": round(length, 4),
                             "ee_effective": round(ee, 5),
                             "feed_width_mm": round(w_feed, 4)},
               "prototype_g": g,
               "band_hz": [round(f0 * (math.sqrt(1.0 + (bw / 2.0) ** 2) - bw / 2.0), 3),
                           round(f0 * (math.sqrt(1.0 + (bw / 2.0) ** 2) + bw / 2.0), 3)]},
        unit="mm",
        model="edge-coupled half-wave microstrip resonators + coupled-line synthesis",
        basis=("Cohn/coupled-resonator filter theory: Qe = g1/FBW and "
               "k(i,i+1) = FBW/sqrt(g_i g_(i+1)); coupled-line impedances "
               "Z0e,Z0o = Z0 sqrt((1+-k)/(1-+k)); geometry from calc/field2d.py; "
               "resonator length = lambda_g/2 minus two open-end corrections"),
        precision=("the electrical design is exact for the prototype; the microstrip "
                   "realisation is quasi-static with no discontinuity reactances, so "
                   "expect the as-built centre frequency to sit a few percent low and "
                   "the passband edges to move. Every coupling reports its achieved k "
                   "so the miss is visible"),
        assumptions={"resonators": "half-wave, all the same length, end-fed",
                     "coupling": "adjacent resonators only",
                     "lossless": True},
        extra={"not_computed": NOT_COMPUTED,
               "note": "this is a first-order synthesis, not a design that can go "
                       "straight to fab: the usual next step is an EM simulation of "
                       "the layout, which this repository does not do",
               "q_external_note": "Qe is geometric (gap to the feed line); dimensioning "
                                  "that gap needs the tapped/parallel feed model, which "
                                  "is not implemented here"})


# ------------------------------------------------------------- waveguide cavity
def _te101_fields(x, y, z, a, b, d, e0=1.0):
    """TE101 field amplitudes (peak phasors) at a point, in the cavity frame.

    E_y = E0 sin(pi x/a) sin(pi z/d) and the two H components that Maxwell's curl
    equation forces. Written out (rather than taken from a table) because the Q
    below integrates |H_tan|^2 over the walls, and a wrong sign or a missing
    factor there is invisible in the final number.
    """
    kx = math.pi / a
    kz = math.pi / d
    ey = e0 * math.sin(kx * x) * math.sin(kz * z)
    # H = (1/(j w mu)) curl-ish; the amplitudes below are |H| with the 1/(w mu)
    # factored out, so the caller scales them
    hx = kz * math.sin(kx * x) * math.cos(kz * z)
    hz = -kx * math.cos(kx * x) * math.sin(kz * z)
    return ey, hx, hz


def _te101_q_numeric(a, b, d, f0, rs_ohm, n=24):
    """Qc by integrating the wall currents and the stored energy numerically.

    Deliberately independent of the closed-form bracket algebra: this is the
    cross-check, not the answer. Surface integrals use the midpoint rule on the
    five distinct wall pairs; the fields above are exact, so the only error is
    quadrature.
    """
    w0 = 2.0 * math.pi * f0
    kx = math.pi / a
    kz = math.pi / d
    # volume: |E|^2 and |H|^2 (with the 1/(w mu) factored out of H)
    s_e = s_h = 0.0
    for i in range(n):
        x = (i + 0.5) * a / n
        for k in range(n):
            z = (k + 0.5) * d / n
            ey, hx, hz = _te101_fields(x, 0.0, z, a, b, d)
            s_e += ey * ey
            s_h += hx * hx + hz * hz
    cell = (a / n) * (d / n)
    int_e = s_e * cell * b
    int_h = s_h * cell * b

    # walls: |H_tan|^2 per unit area, integrated
    wall = 0.0
    for i in range(n):                      # x = 0 and x = a (only Hz is tangential)
        z = (i + 0.5) * d / n
        _ey, _hx, hz0 = _te101_fields(0.0, 0.0, z, a, b, d)
        _ey, _hx, hza = _te101_fields(a, 0.0, z, a, b, d)
        wall += (hz0 * hz0 + hza * hza) * (d / n) * b
    for i in range(n):                      # z = 0 and z = d (only Hx)
        x = (i + 0.5) * a / n
        _ey, hx0, _hz = _te101_fields(x, 0.0, 0.0, a, b, d)
        _ey, hxd, _hz = _te101_fields(x, 0.0, d, a, b, d)
        wall += (hx0 * hx0 + hxd * hxd) * (a / n) * b
    for i in range(n):                      # y = 0 and y = b (both components)
        for k in range(n):
            x = (i + 0.5) * a / n
            z = (k + 0.5) * d / n
            _ey, hx, hz = _te101_fields(x, 0.0, z, a, b, d)
            wall += 2.0 * (hx * hx + hz * hz) * (a / n) * (d / n)

    u_e = 0.25 * EPS0 * int_e                       # peak-phasor time average
    p_c = 0.5 * rs_ohm * wall / (w0 * MU0) ** 2     # |H| carries 1/(w mu)
    if p_c <= 0:
        return None
    return w0 * 2.0 * u_e / p_c


def waveguide_cavity(f0_hz, a_mm, b_mm=None, conductivity_s_m=5.8e7,
                     mu_r=1.0, er=1.0, mode="TE101"):
    """TE101 rectangular cavity: the length that resonates, Qc and R/Q.

    No iris geometry and no coupling: those need a full-wave solve. What is here
    is the resonator every cavity filter is built from, with Qc computed twice -
    once from the standard wall-loss bracket and once by integrating the fields
    over the walls - and the two must agree before the answer is returned.
    """
    if str(mode or "").strip().upper() not in ("TE101", "TE101 "):
        return err("only TE101 is implemented (TE011 and higher modes are the same "
                   "algebra with different index pairs, deliberately not guessed at)",
                   "NOT_IMPLEMENTED")
    try:
        f0 = float(f0_hz)
        a = float(a_mm) / 1000.0
        sigma = float(conductivity_s_m)
        mur = float(mu_r)
        er = float(er)
    except (TypeError, ValueError):
        return err("numeric f0_hz, a_mm required", "ARG_INVALID")
    if f0 <= 0 or a <= 0 or sigma <= 0 or mur <= 0 or er < 1.0:
        return err("f0_hz, a_mm, conductivity and mu_r must be > 0; er >= 1",
                   "ARG_RANGE")
    if b_mm is None:
        b = a / 2.0
        b_assumed = True
    else:
        b = float(b_mm) / 1000.0
        b_assumed = False
        if b <= 0:
            return err("b_mm must be > 0", "ARG_RANGE")

    c = C0 / math.sqrt(er * mur)
    kx = math.pi / a
    k0 = 2.0 * math.pi * f0 / c
    disc = k0 * k0 - kx * kx
    if disc <= 0:
        return err("f0_hz = %.6g Hz is below the TE10 cutoff of this width "
                   "(%.6g Hz): no TE101 resonance exists, the length would be "
                   "imaginary" % (f0, c / (2.0 * a)), "OUT_OF_VALIDITY")
    kz = math.sqrt(disc)
    d = math.pi / kz

    # surface resistance at f0 for this conductivity
    rs = math.sqrt(math.pi * f0 * MU0 * mur / sigma)
    bracket = (kx * kx * b * d + kz * kz * a * b + (kx * kx + kz * kz) * a * d / 2.0)
    q_closed = (2.0 * math.pi * f0) * MU0 * mur * k0 * k0 * (a * b * d) / (4.0 * rs * bracket)
    q_num = _te101_q_numeric(a, b, d, f0, rs)
    q_agree_pct = (None if q_num is None
                   else 100.0 * (q_num - q_closed) / q_closed)
    if q_num is not None and abs(q_agree_pct) > 2.0:
        return err("the closed-form Qc (%.1f) and the wall-integral Qc (%.1f) differ by "
                   "%.2f %%, so one of the two derivations is wrong"
                   % (q_closed, q_num, q_agree_pct), "INTERNAL_CHECK_FAILED")

    w0 = 2.0 * math.pi * f0
    r_over_q = 4.0 * b / (w0 * EPS0 * er * a * d)

    return ok(
        value={"mode": "TE101",
               "length_mm": round(d * 1000.0, 4),
               "a_mm": round(a * 1000.0, 4), "b_mm": round(b * 1000.0, 4),
               "f0_hz": round(f0, 6),
               "surface_resistance_ohm": round(rs, 6),
               "q_conductor": round(q_closed, 1),
               "q_conductor_numeric": (None if q_num is None else round(q_num, 1)),
               "q_agreement_pct": (None if q_agree_pct is None else round(q_agree_pct, 4)),
               "r_over_q_ohm": round(r_over_q, 4),
               "unloaded_q": round(q_closed, 1),
               "b_assumed": b_assumed},
        unit="mm",
        model="rectangular TE101 cavity: resonance, wall-loss Qc, R/Q",
        basis=("TE101 resonance c/2 sqrt((1/a)^2+(1/d)^2); Qc = w*U/Pc with the "
               "surface integral of |H_tan|^2 over the five wall pairs, evaluated "
               "twice (closed-form bracket and numerical integration); "
               "R/Q = 4b/(w*eps*a*d)"),
        precision=("exact for a perfect rectangular cavity with smooth walls and no "
                   "coupling; Qc is the ideal conductor value - real cavities lose "
                   "another 10-30 %% to surface finish, joints and the iris, and "
                   "dielectric loss is not included at all"),
        assumptions={"walls": "same conductivity on all five walls, surface "
                              "roughness ignored",
                     "dielectric": "lossless (er only shifts the resonance)",
                     "coupling": "none: no iris, no probe, no loop"},
        extra={"not_computed": [
                   "iris / coupling-window geometry (full-wave)",
                   "dielectric and radiation loss (Qd, Qr)",
                   "surface roughness correction to Qc",
                   "higher-order modes and mode charts: TE101 only",
                   "thermal drift of the resonance (needs the expansion coefficient)"],
               "note": "a cavity filter is this resonator plus N-1 irises; the "
                       "iris dimensioning is exactly the part that is not here",
               "q_audit": "two independent derivations of Qc are compared; a mismatch "
                          "above 2 % withholds the answer"})
