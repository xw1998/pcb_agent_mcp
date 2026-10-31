"""Microstrip and cavity filter synthesis: the ADS-shaped half of the toolbox.

The four questions a microwave designer asks a line calculator, and what this
module answers for each:

  1. "Give me the prototype for n poles at R dB"            -> prototype_gvalues
  2. "Turn that into a lumped LC bandpass and show me S21"   -> lumped_bandpass
  3. "Give me w and s for this odd/even impedance pair"      -> coupled_microstrip
  4. "Turn that into an edge-coupled resonator BPF"          -> coupled_resonator_bpf
  and the cavity side: length, Qc, R/Q for any TE_mnp/TM_mnp   -> waveguide_cavity
     plus the mode chart below a band edge (mode charts need neighbours)

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
import random

from .result import ok, err
from .roughness import roughness_factor

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
    "finite unloaded Q in the DISTRIBUTED filters: lumped_bandpass takes q_unloaded "
    "and reports the insertion loss it causes, but coupled_resonator and the cavity "
    "still have Qe and R/Q only, no measured unloaded Q of the printed resonator "
    "(for the coupling-matrix view of a given Qu use filter_coupling_matrix, "
    "docs/22 section on the coupling matrix)",
    "cross-coupling between non-adjacent resonators (folded or canonical topologies): "
    "this module has no matrix formulation, so nothing here can produce a stopband "
    "null - filter_coupling_matrix does that, but it PERTURBS the inline matrix "
    "instead of running a full Cameron synthesis, and it still dimensions no layout",
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


# ------------------------------------------------- loss, parasitics, ladder probe
# The lossless ladder is an idealisation nobody can buy. Two things are added here
# and both go through the SAME ABCD cascade as the ideal case:
#
#   * a finite unloaded Q per resonator (loss is one element per resonator: a series
#     resistance w0*L/Q on a series resonator, a shunt conductance w0*C/Q on a shunt
#     one - which is exactly the definition Q = w0*L/R = w0*C/G at resonance),
#   * first-order parasitics: every inductor gets a parallel capacitance (finite
#     self-resonant frequency) and every capacitor a series inductance (ESL).
#
# Nothing is fitted or taken from a curve: the numbers come out of the same matrix
# product. What makes them checkable is `_ladder_probe`, which propagates the actual
# voltage and current down the same ladder and asserts power conservation, so the
# reported S21 and the reported dissipated power cannot disagree silently.

def _q_vector(q, n):
    """None => lossless; scalar => same Q everywhere; sequence => one Q per resonator."""
    if q is None:
        return None
    if isinstance(q, (int, float)):
        vals = [float(q)] * n
    else:
        try:
            vals = [float(x) for x in q]
        except (TypeError, ValueError):
            raise ValueError("q_unloaded must be a number or a list of numbers")
        if len(vals) != n:
            raise ValueError("q_unloaded has %d entries but the filter has %d resonators"
                             % (len(vals), n))
    for v in vals:
        if not (v > 0.0) or v != v or v == float("inf"):
            raise ValueError("every q_unloaded must be finite and > 0")
    return vals

def _parasitic_parts(par):
    """{'cp_f': farads across each inductor, 'esl_h': henries with each capacitor}."""
    if not par:
        return None
    try:
        cp = float(par.get("cp_f") or 0.0)
        esl = float(par.get("esl_h") or 0.0)
    except (TypeError, ValueError):
        raise ValueError("parasitics must be numbers: {'cp_f': ..., 'esl_h': ...}")
    if cp < 0.0 or esl < 0.0:
        raise ValueError("parasitics cannot be negative")
    if cp == 0.0 and esl == 0.0:
        return None
    return {"cp_f": cp, "esl_h": esl}

def _ladder_elements(order, f0, bw, z0, g):
    """Lowpass-to-bandpass transform: one resonator per prototype element.

    Odd k becomes a shunt parallel LC, even k a series LC. The impedance scaling is
    the part that is easy to get backwards: series impedance scales with z0, shunt
    admittance with 1/z0.
    """
    w0 = 2.0 * math.pi * f0
    # Even-order Chebyshev ladders start on a SERIES element, odd-order ones on a
    # shunt element. That parity is not cosmetic: the prototype's load is g(n+1) != 1
    # for even order, and only the series-first pairing reproduces the equal-ripple
    # response (verified against 1/(1+eps^2 Tn^2) out to |Omega| = 3).
    series_first = (order % 2 == 0)
    els = []
    for k in range(1, order + 1):
        gk = g[k - 1]
        series = ((k % 2 == 1) == series_first)
        if series:
            L = gk * z0 / (bw * w0)
            C = bw / (w0 * z0 * gk)
            kind = "series"
        else:
            C = gk / (bw * w0 * z0)
            L = bw * z0 / (w0 * gk)
            kind = "shunt"
        els.append({"k": k, "kind": kind, "L": L, "C": C})
    return els

def _ladder_sections(elements, f, w0, qs=None, par=None):
    """[('series', Z) | ('shunt', Y)] at frequency f, with loss and parasitics.

    Loss and parasitics never change which branch is series and which is shunt, so
    the topology the prototype dictated is preserved exactly.
    """
    w = 2.0 * math.pi * f
    cp = par["cp_f"] if par else 0.0
    esl = par["esl_h"] if par else 0.0
    out = []
    for i, e in enumerate(elements):
        L, C = e["L"], e["C"]
        z_l = 1j * w * L
        if cp:
            z_l = 1.0 / (1.0 / z_l + 1j * w * cp)
        z_c = 1.0 / (1j * w * C) + 1j * w * esl
        if e["kind"] == "series":
            z = z_l + z_c
            if qs:
                z = z + w0 * L / qs[i]
            out.append(("series", z))
        else:
            y = 1.0 / z_c + 1.0 / z_l
            if qs:
                y = y + w0 * C / qs[i]
            out.append(("shunt", y))
    return out

def _termination_ratio(order, g):
    """Turns ratio of the ideal output transformer that makes both ports equal.

    An even-order Chebyshev prototype is defined with source 1 and load g(n+1) != 1,
    so a 50-in/50-out ladder needs a 1:sqrt(g(n+1)) transformer between the last
    series resonator and the load. Its input impedance is ratio^2*z0 = g(n+1)*z0,
    which is exactly the termination the prototype's g values were computed for. The
    transformer is reported as part of the design because it is part of the network -
    hiding it would make the element values look like a 50 ohm filter when they are
    not.
    """
    if order % 2 == 0 and len(g) > order:
        return math.sqrt(g[order])
    return 1.0

def _ladder_probe(sections, z0, ratio=1.0):
    """S11/S21 from the ABCD product AND the power flow along the same ladder.

    Two numbers come out that must agree if either is right: |S21|^2 and the power
    actually delivered to the load (V*conj(I) at the far end), plus the claim that
    incident power = load + source resistor + every loss element. With a lossless
    ladder the balance is trivially 1; with loss it is the audit that the loss was
    put in the right place, because a series resistance misplaced as a shunt
    conductance keeps |S21| plausible while breaking the balance.
    """
    abcd = _abcd_cascade(sections)
    if ratio != 1.0:
        A, B, C, D = abcd
        abcd = (A * ratio, B / ratio, C * ratio, D / ratio)
    s11, s21 = _s21(abcd, z0)
    vs = 2.0                      # peak source amplitude, chosen so |a|^2 = 1/z0 * ...
    v_in = 1.0 + s11
    i_in = (1.0 - s11) / z0
    v, i = v_in, i_in
    p_elements = []
    for kind, val in sections:
        if kind == "series":
            p_elements.append(0.5 * abs(i) ** 2 * val.real)
            v = v - val * i
        else:
            p_elements.append(0.5 * abs(v) ** 2 * val.real)
            i = i - val * v
    v = v / ratio
    i = i * ratio                                  # ideal transformer: lossless
    p_load = 0.5 * abs(v) ** 2 / z0
    p_source = 0.5 * abs(vs - v_in) ** 2 / z0      # informational only, see _power_audit
    p_incident = 0.5 * vs * vs / (4.0 * z0)
    return {"s11": s11, "s21": s21, "p_load": p_load, "p_source": p_source,
            "p_elements": p_elements, "p_incident": p_incident}

def _power_audit(probe):
    """(relative power-balance error, relative |S21|^2 vs delivered-power error).

    The identity being checked is the wave one, not a battery one: incident power
    splits into the reflected wave and what enters the network, and what enters the
    network leaves as load power plus dissipation in the loss elements. The source
    resistor is deliberately NOT part of that sum - it sits outside the reference
    plane, and adding it is the mistake that makes a correct lossless ladder report
    a balance error of exactly 2.
    """
    entered = probe["p_load"] + sum(probe["p_elements"])
    accepted = probe["p_incident"] * (1.0 - abs(probe["s11"]) ** 2)
    bal = abs(entered - accepted) / max(probe["p_incident"], 1e-300)
    got = abs(probe["s21"]) ** 2
    want = probe["p_load"] / max(probe["p_incident"], 1e-300)
    # the floor keeps a null (-200 dB, |S21|^2 ~ 1e-20) from turning cancellation
    # noise into a "the two derivations disagree" verdict
    rel = abs(got - want) / max(abs(want), 1e-6)
    return bal, rel

def _il_db(s11, s21):
    return -20.0 * math.log10(max(abs(s21), 1e-30))

def _edge_db(il_fn, f0, lower, level):
    """Frequency where the insertion loss reaches `level` dB, walking away from f0."""
    r = 0.98 if lower else 1.02
    inside = f0
    f = f0
    for _ in range(500):
        f *= r
        if f <= 1e-9 or (not lower and f > f0 * 1e4):
            return None
        if il_fn(f) >= level:
            outside, inside_f = f, inside
            for _ in range(120):
                mid = 0.5 * (outside + inside_f)
                if il_fn(mid) >= level:
                    outside = mid
                else:
                    inside_f = mid
            return 0.5 * (outside + inside_f)
        inside = f
    return None

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
                    response="chebyshev", points=31, q_unloaded=None,
                    parasitics=None):
    """LC ladder bandpass from the prototype, with the response it actually gives.

    The element values come from the lowpass-to-bandpass transform; the S21 that
    is reported comes from cascading those elements as ABCD matrices. The ideal
    prototype formula is evaluated at the same frequencies and the two are
    compared - that comparison is the answer's own audit, not decoration.

    q_unloaded turns the idealised lossless ladder into a real one: each resonator
    may be given its own unloaded Q (a scalar applies it to all of them), and the
    insertion loss that follows is computed, not estimated. parasitics adds the
    two first-order effects every real LC bandpass suffers from - a parallel
    capacitance across each inductor (finite self-resonance) and a series
    inductance with each capacitor (ESL).
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

    try:
        qs = _q_vector(q_unloaded, n)
        par = _parasitic_parts(parasitics)
    except ValueError as exc:
        return err(str(exc), "ARG_INVALID",
                   hint="q_unloaded = one number (all resonators) or one per resonator; "
                        "parasitics = {'cp_f': F across each inductor, "
                        "'esl_h': H with each capacitor}")

    ratio = _termination_ratio(n, g)
    base = _ladder_elements(n, f0, bw, z0, g)

    elements = []
    for e in base:
        L, C = e["L"], e["C"]
        row = {"k": e["k"], "kind": e["kind"],
               "C_pf": round(C * 1e12, 6), "L_nh": round(L * 1e9, 6),
               "f_self_res_hz": round(1.0 / (2 * math.pi * math.sqrt(L * C)), 3)}
        if qs:
            row["q_unloaded"] = qs[e["k"] - 1]
            row["loss_element"] = ({"kind": "series_r_ohm",
                                    "value": round(w0 * L / qs[e["k"] - 1], 6)}
                                   if e["kind"] == "series" else
                                   {"kind": "shunt_g_s",
                                    "value": round(w0 * C / qs[e["k"] - 1], 9)})
        if par and par["cp_f"]:
            row["l_self_res_with_cp_hz"] = round(
                1.0 / (2 * math.pi * math.sqrt(L * par["cp_f"])), 3)
        elements.append(row)

    def probe_at(f, with_loss=True):
        sec = _ladder_sections(base, f, w0,
                               qs if with_loss else None,
                               par if with_loss else None)
        return _ladder_probe(sec, z0, ratio)

    def s_at(f, with_loss=True):
        p = probe_at(f, with_loss)
        return p["s11"], p["s21"]

    def omega_at(f):
        return (f / f0 - f0 / f) / bw

    # The audit: element-cascade S21 against the prototype's closed form. Only
    # the stopband beyond the first transmission zero is skipped, because a ladder
    # of finite resonators is not the prototype there (the prototype is a
    # polynomial, the ladder goes to a null); inside and near the band they must
    # agree to a hair. It is deliberately run on the LOSSLESS ladder: the element
    # synthesis is what is being audited, and loss would blur it.
    worst = 0.0
    worst_f = None
    f_lo = f0 / max(1e-9, (1.0 + 2.0 * bw))
    f_hi = f0 * (1.0 + 2.0 * bw)
    for i in range(npts):
        f = f_lo + (f_hi - f_lo) * i / (npts - 1)
        om = omega_at(f)
        if abs(om) > 3.0:
            continue
        _s11, s21 = s_at(f, with_loss=False)
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

    # Power audit at the centre and at both band edges: |S21|^2 must equal the
    # power that actually arrives at the load, and the losses must add up.
    f1 = f0 * (math.sqrt(1.0 + (bw / 2.0) ** 2) - bw / 2.0)
    f2 = f0 * (math.sqrt(1.0 + (bw / 2.0) ** 2) + bw / 2.0)
    bal_worst, s21_worst = 0.0, 0.0
    for f in (f1, f0, f2, f_lo, f_hi):
        bal, rel = _power_audit(probe_at(f))
        bal_worst = max(bal_worst, bal)
        s21_worst = max(s21_worst, rel)
    if bal_worst > 1e-9 or s21_worst > 1e-9:
        return err("power is not conserved along the ladder (balance error %.3g, "
                   "|S21|^2 vs delivered power %.3g) - the loss or parasitic element "
                   "is in the wrong branch" % (bal_worst, s21_worst),
                   "INTERNAL_CHECK_FAILED",
                   hint="code defect, not a user error; run the filters tests")

    edges = {}
    for name, f in (("f_lower_hz", f1), ("f_center_hz", f0), ("f_upper_hz", f2)):
        _s11, s21 = s_at(f)
        edges[name] = round(f, 3)
        edges[name.replace("_hz", "_il_db")] = round(_il_db(_s11, s21), 4)

    sweep = []
    for i in range(npts):
        f = f_lo + (f_hi - f_lo) * i / (npts - 1)
        s11, s21 = s_at(f)
        sweep.append({"f_hz": round(f, 6),
                      "s21_db": round(_il_db(s11, s21), 4),
                      "s11_db": round(_il_db(s11, s11), 4)})

    value = {"order": n, "response": resp, "ripple_db": ripple,
             "f0_hz": round(f0, 6), "fbw": bw,
             "z0_ohm": z0,
             "band_hz": [round(f1, 3), round(f2, 3)],
             "elements": elements,
             "band_edges": edges,
             "response": sweep,
             "self_check": {"max_abs_deviation_db": round(worst, 6),
                            "tolerance_db": RESPONSE_AGREEMENT_DB,
                            "compared_against": "1/(1+eps^2*Tn^2(lambda))",
                            "power_balance_rel_error": "%.3g" % bal_worst,
                            "s21_squared_vs_delivered_power_rel_error": "%.3g" % s21_worst},
             "termination": {
                 "port_ohm": z0,
                 "ladder_load_ohm": round(z0 * ratio * ratio, 6),
                 "output_transformer_ratio": round(ratio, 6),
                 "note": ("even order: the prototype's g(n+1) != 1, so a 1:ratio "
                          "transformer (or an equivalent tapped/capacitively-tapped "
                          "output) is part of the design; odd order terminating "
                          "directly in z0"
                          if ratio != 1.0 else
                          "odd order: the prototype's load is 1, so the ladder "
                          "terminates directly in z0")},
             "prototype_g": proto["value"]["g"]}

    precision = ("element values are exact for the lossless model; the response is "
                 "computed from those same elements, so it is the ladder's answer and "
                 "not a fitted curve")
    assumptions = {"lossless": qs is None and par is None,
                   "terminations": "z0 at both ports" if par is None
                                   else "z0 at both ports, parasitics only on the "
                                        "resonators (none on the ports)",
                   "topology": ("shunt-first alternating ladder" if n % 2 else
                                "series-first alternating ladder (even order: the "
                                "prototype's load is g(n+1) != 1, absorbed by the "
                                "output transformer)")}

    if qs is not None or par is not None:
        il_c = _il_db(*s_at(f0))
        il_lo = _il_db(*s_at(f1))
        il_hi = _il_db(*s_at(f2))
        # "3 dB" is measured from the in-band minimum, not from 0 dB: on a lossy
        # filter those are different levels, and measuring from 0 dB makes the
        # apparent loaded Q *rise* as the loss goes up (the curve drops below the
        # 3 dB line before it is 3 dB wide). The minimum is at f0 for both parities.
        level3 = il_c + 3.0
        f3_lo = _edge_db(lambda f: _il_db(*s_at(f)), f0, True, level3)
        f3_hi = _edge_db(lambda f: _il_db(*s_at(f)), f0, False, level3)
        _s11_c, _s21_c = s_at(f0)
        loss = {"q_unloaded": (qs if qs is None else
                               [round(x, 3) for x in qs]),
                "insertion_loss_center_db": round(il_c, 4),
                "s11_at_center_db": round(_il_db(_s11_c, _s11_c), 4),
                "insertion_loss_lower_edge_db": round(il_lo, 4),
                "insertion_loss_upper_edge_db": round(il_hi, 4),
                "dissipated_fraction_at_center": round(
                    sum(probe_at(f0)["p_elements"]) / probe_at(f0)["p_incident"], 6),
                "loss_model": ("one loss element per resonator: R = w0*L/Q on a "
                               "series resonator, G = w0*C/Q on a shunt one (that is "
                               "the definition of unloaded Q at resonance)"),
                "not_modelled": ["thermal drift of L and C with temperature",
                                 "Q versus frequency (Q is frozen at f0)",
                                 "separate Q for the inductor and the capacitor inside "
                                 "the same resonator",
                                 "core loss or skin effect inside the inductor"]}
        if f3_lo and f3_hi and (f3_hi - f3_lo) > 1e-6 * f0:
            loss["delta_3db_hz"] = round(f3_hi - f3_lo, 3)
            loss["loaded_q"] = round(f0 / max(f3_hi - f3_lo, 1e-30), 4)
            loss["half_power_bandwidth_over_fbw"] = round(
                (f3_hi - f3_lo) / (f2 - f1), 4)
            loss["delta_3db_definition"] = (
                "band where the insertion loss stays within 3 dB of the in-band "
                "minimum (at f0), not within 3 dB of 0 dB")
            loss["loaded_q_caveat"] = (
                "this loaded Q is MEASURED from the lossy response, so it is not "
                "1/(1/Qe + 1/Qu): the loss is energy-weighted (it costs most where the "
                "stored energy peaks, i.e. at band centre), so on a lossy filter the "
                "response narrows slightly as Q falls and the measured ratio barely "
                "moves - read it as a measurement, not as a synthesis input")
        else:
            loss["delta_3db_hz"] = None
            loss["loaded_q"] = None
            loss["no_3db_passband"] = (
                "the filter never gets below 3 dB, so a half-power bandwidth and a "
                "loaded Q do not exist - the loss is larger than the passband "
                "definition itself")
        if par:
            srf = [1.0 / (2 * math.pi * math.sqrt(e["L"] * par["cp_f"]))
                   for e in base] if par["cp_f"] else []
            loss["parasitics"] = {"cp_f": par["cp_f"], "esl_h": par["esl_h"],
                                  "inductor_srf_hz": [round(x, 3) for x in srf],
                                  "srf_below_3f0": bool(srf and min(srf) < 3.0 * f0)}
            if srf and min(srf) < 3.0 * f0:
                loss["parasitics"]["warning"] = (
                    "an inductor self-resonates below 3*f0, so the resonator is a "
                    "capacitor at some frequencies inside the design band - the "
                    "response above is the answer, but the parts do not exist")
        else:
            loss["parasitics"] = None
        # Independent cross-check of the loss model, one formula that shares no code
        # with the cascade: Matthaei's midband dissipation loss for N equal-Q
        # resonators, L0 = 4.343 * sum(g1..gn) / (Qu * FBW). It is asymptotic (exact
        # only in the low-loss limit), which is why it is a band, not an equality -
        # but a factor-of-two mistake in where the loss element goes cannot survive
        # a 50 % band.
        if qs and par is None and (max(qs) - min(qs)) < 1e-9:
            est = 4.343 * sum(g[:n]) / (qs[0] * bw)
            base_il = ripple if n % 2 == 0 else 0.0
            meas = il_c - base_il
            loss["dissipation_loss_estimate_db"] = round(est, 4)
            loss["dissipation_measured_db"] = round(meas, 4)
            loss["dissipation_vs_estimate_pct"] = round(
                100.0 * (meas - est) / max(est, 1e-12), 2)
            loss["estimate_basis"] = (
                "Matthaei midband dissipation loss 4.343*sum(g_k, k=1..n)/(Qu*FBW); "
                "asymptotic in the low-loss limit, so it is compared as a band")
            if est < 3.0 and abs(meas - est) / max(est, 1e-12) > 0.5:
                return err("the cascade says %.3f dB of dissipation at the centre but "
                           "the closed-form dissipation loss says %.3f dB - a factor "
                           "that large means the loss element is misplaced"
                           % (meas, est), "INTERNAL_CHECK_FAILED",
                           hint="code defect, not a user error; run the filter tests")
        else:
            loss["dissipation_loss_estimate_db"] = None
            loss["estimate_skipped_because"] = (
                "the estimate is only written for equal Q on every resonator and no "
                "parasitics (parasitics detune the resonators, so the formula would be "
                "comparing two different filters)")
        value["loss"] = loss
        precision = (
            "element values are exact for the lossless model; with q_unloaded and/or "
            "parasitics the response is the cascade of the lossy elements, and the "
            "insertion loss reported is that cascade's -1*20*log10|S21| at the stated "
            "frequencies, not an estimate from a formula. The dissipated fraction and "
            "|S21| are cross-checked against each other through power conservation "
            "(see self_check)")
        assumptions["loss"] = ("one loss element per resonator at the resonator's own "
                               "f0; parasitic values are the same for every element")
        assumptions["parasitic_sign"] = ("cp only lowers the inductor's impedance "
                                         "above its self-resonance; esl only raises the "
                                         "capacitor's impedance at high frequency - both "
                                         "make the filter worse, never better")

    return ok(
        value=value,
        unit="mixed",
        model=("lossless LC ladder from the lowpass prototype (ABCD cascade)"
               if qs is None and par is None else
               "LC ladder from the lowpass prototype with per-resonator unloaded Q "
               "and first-order element parasitics (ABCD cascade)"),
        basis=("lowpass-to-bandpass frequency transform "
               "Omega = (1/FBW)(f/f0 - f0/f) applied to the Matthaei prototype, "
               "impedance-scaled to z0; response from the ABCD cascade; loss from "
               "Q = w0*L/R = w0*C/G at resonance; parasitics as cp across L and esl "
               "in series with C"),
        precision=precision,
        assumptions=assumptions,
        extra={"not_computed": NOT_COMPUTED,
               "note": "an LC ladder with these values is realisable only if the "
                       "element spread is; compare the largest and smallest C and L "
                       "before trusting a 5-pole 5 % design at 100 MHz",
               "audit": "the cascade and the prototype formula are separate code "
                        "paths; agreement to %.2f dB is what makes the element "
                        "values checkable" % RESPONSE_AGREEMENT_DB,
               "yield": "for component tolerance use filter_monte_carlo, which "
                        "perturbs these same elements and runs the same cascade"})

# ------------------------------------------------------------------ tolerance
def filter_monte_carlo(order, f0_hz, fbw, tol_c_pct=0.0, tol_l_pct=0.0,
                       n_draws=200, seed=0, il_max_db=1.0,
                       f_stop_hz=None, reject_min_db=None,
                       q_unloaded=None, parasitics=None,
                       response="chebyshev", ripple_db=None, z0_ohm=50.0,
                       distribution="uniform"):
    """Tolerance yield of the lumped bandpass: perturb the parts, run the same cascade.

    Every draw multiplies each capacitor by 1 +- tol_c and each inductor by
    1 +- tol_l (independently, one factor per element - the real world correlates
    them, see `assumptions`), rebuilds the ladder and evaluates the SAME lossy
    ABCD cascade `lumped_bandpass` uses. A draw passes when the insertion loss stays
    at or below `il_max_db` at the centre and at both band edges, and - if asked -
    when the attenuation at `f_stop_hz` is at least `reject_min_db`.

    Zero tolerance is handled as the degenerate case it is: the nominal design is
    evaluated once, the answer says so, and no sampling is pretended. A seed is
    always used, so the same call always returns the same number.
    """
    proto = prototype_gvalues(order, response, ripple_db)
    if proto["status"] != "ok":
        return proto
    try:
        f0 = float(f0_hz)
        bw = float(fbw)
        z0 = float(z0_ohm)
        tc = float(tol_c_pct or 0.0) / 100.0
        tl = float(tol_l_pct or 0.0) / 100.0
        nd = int(n_draws)
        sd = int(seed)
        il_max = float(il_max_db)
    except (TypeError, ValueError):
        return err("numeric f0_hz, fbw, z0_ohm, tolerances, n_draws, seed and "
                   "il_max_db are required", "ARG_INVALID")
    if f0 <= 0 or z0 <= 0:
        return err("f0_hz and z0_ohm must be > 0", "ARG_RANGE")
    if bw <= 0.0 or bw >= 1.0:
        return err("fbw is the fractional bandwidth (0 < fbw < 1)", "ARG_RANGE")
    if tc < 0.0 or tl < 0.0:
        return err("tolerances are magnitudes: give 2 for +-2 %%, not -2", "ARG_RANGE")
    if tc >= 1.0 or tl >= 1.0:
        return err("a tolerance of 100 %% or more means the part may be absent; that is "
                   "not a filter any more", "ARG_RANGE")
    if nd < 1 or nd > 20000:
        return err("n_draws must be 1..20000", "ARG_RANGE")
    if not (il_max > 0.0):
        return err("il_max_db is the passband insertion-loss budget and must be > 0",
                   "ARG_RANGE")
    dist = str(distribution or "uniform").strip().lower()
    if dist not in ("uniform", "gaussian"):
        return err("distribution must be 'uniform' or 'gaussian'", "ARG_INVALID")
    if f_stop_hz is None and reject_min_db is not None:
        return err("reject_min_db without f_stop_hz does not describe a spec",
                   "ARG_CONFLICT",
                   hint="give both: the frequency you must reject at, and by how much")
    if f_stop_hz is not None:
        try:
            f_stop = float(f_stop_hz)
        except (TypeError, ValueError):
            return err("f_stop_hz must be a number", "ARG_INVALID")
        if f_stop <= 0.0:
            return err("f_stop_hz must be > 0", "ARG_RANGE")
        if reject_min_db is None:
            return err("f_stop_hz needs reject_min_db (how much attenuation you need "
                       "there)", "ARG_CONFLICT")
        reject = float(reject_min_db)
        if reject <= 0.0:
            return err("reject_min_db must be > 0", "ARG_RANGE")
    else:
        f_stop, reject = None, None

    n = int(order)
    g = proto["value"]["g"]
    resp = proto["value"]["response"]
    ripple = proto["value"]["ripple_db"]
    w0 = 2.0 * math.pi * f0
    try:
        qs = _q_vector(q_unloaded, n)
        par = _parasitic_parts(parasitics)
    except ValueError as exc:
        return err(str(exc), "ARG_INVALID")

    base = _ladder_elements(n, f0, bw, z0, g)
    ratio = _termination_ratio(n, g)
    f1 = f0 * (math.sqrt(1.0 + (bw / 2.0) ** 2) - bw / 2.0)
    f2 = f0 * (math.sqrt(1.0 + (bw / 2.0) ** 2) + bw / 2.0)

    def metrics(els):
        def il(f):
            sec = _ladder_sections(els, f, w0, qs, par)
            p = _ladder_probe(sec, z0, ratio)
            return _il_db(p["s11"], p["s21"])
        out = {"il_center_db": il(f0), "il_lower_edge_db": il(f1),
               "il_upper_edge_db": il(f2)}
        out["worst_passband_il_db"] = max(out["il_center_db"],
                                         out["il_lower_edge_db"],
                                         out["il_upper_edge_db"])
        if f_stop is not None:
            out["il_stop_db"] = il(f_stop)
        return out

    def passes(m):
        if m["worst_passband_il_db"] > il_max:
            return False
        if f_stop is not None and m["il_stop_db"] < reject:
            return False
        return True

    nominal = metrics(base)
    degenerate = (tc == 0.0 and tl == 0.0)

    rng = random.Random(sd)
    stats = {"il_center_db": [], "il_lower_edge_db": [], "il_upper_edge_db": [],
             "worst_passband_il_db": []}
    if f_stop is not None:
        stats["il_stop_db"] = []
    passed = 0
    used = 0
    invalid = 0
    worst = None

    def scored(m, factors_c, factors_l):
        return m["worst_passband_il_db"] - (m["il_stop_db"] - reject
                                            if f_stop is not None else 0.0)

    for _ in range(1 if degenerate else nd):
        if degenerate:
            fc = [1.0] * n
            fl = [1.0] * n
        elif dist == "uniform":
            fc = [1.0 + tc * (2.0 * rng.random() - 1.0) for _ in range(n)]
            fl = [1.0 + tl * (2.0 * rng.random() - 1.0) for _ in range(n)]
        else:
            # gaussian with sd = tol/2, so +-tol is +-2 sigma (95.4 % of draws)
            fc = [1.0 + rng.gauss(0.0, tc / 2.0) for _ in range(n)]
            fl = [1.0 + rng.gauss(0.0, tl / 2.0) for _ in range(n)]
        if min(fc) <= 0.0 or min(fl) <= 0.0:
            invalid += 1          # a negative part is not a part; counted, not resampled
            continue
        els = [{"k": e["k"], "kind": e["kind"], "L": e["L"] * fl[i],
                "C": e["C"] * fc[i]} for i, e in enumerate(base)]
        m = metrics(els)
        used += 1
        ok_draw = passes(m)
        if ok_draw:
            passed += 1
        for key in stats:
            stats[key].append(m[key])
        score = scored(m, fc, fl)
        if worst is None or score > worst["_score"]:
            worst = {"_score": score, "passes": ok_draw,
                     "metrics": {k: round(v, 4) for k, v in m.items()},
                     "factor_c": [round(x, 6) for x in fc],
                     "factor_l": [round(x, 6) for x in fl]}

    if used == 0:
        return err("every draw produced a non-physical part (negative L or C); the "
                   "tolerance is too wide for this distribution", "ARG_RANGE",
                   hint="use distribution='uniform' or a tolerance below a few percent")

    def q(vals, p):
        s_ = sorted(vals)
        if not s_:
            return None
        idx = min(len(s_) - 1, max(0, int(round(p * (len(s_) - 1)))))
        return round(s_[idx], 4)

    summary = {}
    for key, vals in stats.items():
        summary[key] = {"min": round(min(vals), 4), "median": q(vals, 0.5),
                        "p95": q(vals, 0.95), "max": round(max(vals), 4)}
    worst.pop("_score", None)

    p_hat = passed / float(used)
    ci95 = 1.96 * math.sqrt(max(p_hat * (1.0 - p_hat), 0.0) / used) * 100.0
    value = {
        "n_draws": used, "n_draws_requested": (1 if degenerate else nd),
        "seed": sd, "distribution": dist,
        "tolerances_pct": {"c": round(tc * 100.0, 6), "l": round(tl * 100.0, 6)},
        "spec": {"il_max_db": il_max, "f_stop_hz": f_stop,
                 "reject_min_db": reject},
        "nominal": {k: round(v, 4) for k, v in nominal.items()},
        "nominal_passes": passes(nominal),
        "passed": passed, "failed": used - passed,
        "yield_pct": round(100.0 * p_hat, 3),
        "yield_95ci_pct": round(ci95, 3),
        "degenerate_zero_tolerance": degenerate,
        "stats": summary,
        "worst_draw": worst,
        "invalid_draws": invalid,
        "frequencies_hz": {"center": round(f0, 3), "lower_edge": round(f1, 3),
                           "upper_edge": round(f2, 3), "stop": f_stop},
    }
    if degenerate:
        note = ("both tolerances are zero, so every draw IS the nominal design: the "
                "yield below is 100 % or 0 % by construction and says nothing about "
                "sensitivity")
    elif not passes(nominal):
        note = ("the NOMINAL design already fails this spec, so this yield is not a "
                "tolerance yield - fix the design (order, fbw, ripple, loss) first")
    else:
        note = ("yield is the fraction of {0:d} draws meeting the spec; the 95 % "
                "confidence half-width of {1:.1f} points is what {0:d} draws can "
                "resolve".format(used, ci95))

    return ok(
        value=value,
        unit="percent",
        model=("Monte Carlo over per-element L/C tolerance, evaluating the same ABCD "
               "cascade as lumped_bandpass (loss and parasitics included if given)"),
        basis=("one independent multiplicative factor per element; band edges from "
               "Omega = +-1 of the bandpass transform; spec checked at centre and both "
               "edges (+ optional stopband point)"),
        precision=("the yield is an estimate with a binomial 95 % half-width reported "
                   "alongside it - quoting " + ("%0.1f %% from %d draws" %
                                                (p_hat * 100.0, used)) +
                   " without that number overstates the precision"),
        assumptions={"independence": "each element's error is independent of every "
                                     "other element's - correlated errors (a common "
                                     "reel, temperature, trimming) are not modelled",
                     "distribution": ("uniform on +-tol" if dist == "uniform" else
                                      "gaussian, sd = tol/2, so +-tol is +-2 sigma"),
                     "correlation_within_resonator": "L and C of the same resonator are "
                                                     "perturbed independently; a real "
                                                     "tuning operation moves them "
                                                     "together, which is more benign",
                     "samples": "one frequency point per spec item, not a mask swept "
                                "across the band"},
        extra={"how_to_read": note,
               "loss_and_parasitics": ("pass q_unloaded / parasitics to include them; "
                                       "the yield then reflects the lossy filter, and "
                                       "every loss/parasitic caveat of lumped_bandpass "
                                       "applies unchanged"),
               "why_not_a_closed_form": ("a closed-form yield would assume the response "
                                         "is linear in each part, which a ladder is not "
                                         "near a band edge; sampling the cascade keeps "
                                         "the answer honest at the cost of a seed"),
               "not_computed": ["temperature coefficient of L and C",
                                "aging and solder shift",
                                "correlated (lot) variation",
                                "yield of anything but this LC ladder (the microstrip "
                                "filters are geometric and get their tolerance from the "
                                "etch, which is a different question)",
                                "test cost / guard band placement"]})

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


def _te101_q_numeric(a, b, d, f0, rs_ohm, n=24, eps=EPS0, mu=MU0):
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

    u_e = 0.25 * eps * int_e                        # peak-phasor time average
    p_c = 0.5 * rs_ohm * wall / (w0 * mu) ** 2      # |H| carries 1/(w mu)
    if p_c <= 0:
        return None
    return w0 * 2.0 * u_e / p_c


# ------------------------------------------------------- general cavity modes
def _sin2(idx, length):
    """Integral of sin^2(idx*pi*x/length) over [0, length], in closed form."""
    return 0.0 if idx == 0 else 0.5 * length

def _cos2(idx, length):
    """Integral of cos^2(idx*pi*x/length) over [0, length], in closed form."""
    return length if idx == 0 else 0.5 * length

def _parse_cavity_mode(mode):
    """(family, m, n, p) from 'TE101' / 'te_1_0_1' / 'TM110'; None when unreadable.

    Two spellings on purpose: three single digits (how everyone writes TE101) and
    underscored integers (the only way to say TE_2_0_1 without ambiguity - 'TE201'
    could also be 20,1 and a guess there would silently answer a different mode).
    """
    s = str(mode or "").strip().upper().replace(" ", "")
    if len(s) < 3:
        return None
    fam, tail = s[:2], s[2:]
    if fam not in ("TE", "TM"):
        return None
    if tail.startswith("_"):
        parts = tail[1:].split("_")
    elif len(tail) == 3 and tail.isdigit():
        parts = list(tail)
    else:
        return None
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        return None
    return fam, int(parts[0]), int(parts[1]), int(parts[2])

def _cavity_mode_name(fam, m, n, p):
    return "%s_%d_%d_%d" % (fam, m, n, p)

def _cavity_mode_reason(fam, m, n, p):
    """Why (family, m, n, p) is not a cavity mode, or None when it is one."""
    if m < 0 or n < 0 or p < 0:
        return "mode indices must be >= 0"
    if fam == "TE":
        if m == 0 and n == 0:
            return ("TE_mnp needs at least one of m, n non-zero: with both zero the "
                    "transverse field is uniform (kc = 0) and no TE mode exists")
        if p == 0:
            return ("TE_mn0 needs p >= 1: the axial field goes as sin(p*pi*z/d), "
                    "identically zero at p = 0, so TE_mn0 is not a mode")
    else:
        if m == 0 or n == 0:
            return ("TM_mnp needs m >= 1 and n >= 1: with either index zero the axial "
                    "E field sin(m*pi*x/a)*sin(n*pi*y/b) vanishes everywhere")
    return None

def _cavity_q_closed(fam, m, n, p, a, b, d, f0, rs_ohm, mur=1.0):
    """Qc of TE_mnp / TM_mnp from the exact volume and wall integrals.

    Qc = w*mu*int|H|^2 dV / (Rs * contour|H_tan|^2 dS) for peak phasors: the
    stored energy at resonance is U = int(mu|H|^2)/2 and the wall loss is
    Pc = Rs*contour(|H_tan|^2)/2. Every integral below is a product of
    int sin^2 = L/2 and int cos^2 = L/2 (L itself when the index is 0), so the
    answer is closed form - no quadrature, and no table of "the usual formula"
    that could be copied with the wrong index pair.

    For TM the H components carry a common (w*eps/kc^2) factor which cancels
    between numerator and denominator, so it is dropped.
    """
    kx = m * math.pi / a
    ky = n * math.pi / b
    kz = p * math.pi / d
    kc2 = kx * kx + ky * ky
    if kc2 <= 0.0:
        return None
    sx, cx = _sin2(m, a), _cos2(m, a)
    sy, cy = _sin2(n, b), _cos2(n, b)
    sz, cz = _sin2(p, d), _cos2(p, d)
    if fam == "TE":
        fx = kz * kx / kc2
        fy = kz * ky / kc2
        vol = cx * cy * sz + fx * fx * sx * cy * cz + fy * fy * cx * sy * cz
        # walls: z = 0,d carry Hx,Hy; x = 0,a carry Hy,Hz; y = 0,b carry Hx,Hz
        wall = 2.0 * ((fx * fx * sx * cy + fy * fy * cx * sy)
                      + (fy * fy * sy * cz + cy * sz)
                      + (fx * fx * sx * cz + cx * sz))
    else:
        vol = ky * ky * sx * cy * cz + kx * kx * cx * sy * cz
        # walls: z = 0,d carry Hx,Hy; x = 0,a carry Hy only; y = 0,b carry Hx only
        wall = 2.0 * ((ky * ky * sx * cy + kx * kx * cx * sy)
                      + kx * kx * sy * cz + ky * ky * sx * cz)
    if vol <= 0.0 or wall <= 0.0:
        return None
    return (2.0 * math.pi * f0) * MU0 * mur * vol / (rs_ohm * wall)

def _cavity_q_numeric(fam, m, n, p, a, b, d, f0, rs_ohm, mur=1.0, er=1.0,
                      n_grid=24):
    """(Qc, U_e/U_m) for TE_mnp / TM_mnp by integrating the fields on a grid.

    Deliberately shares no algebra with _cavity_q_closed: the fields are written
    out and both energies are integrated independently, so the returned ratio
    U_e/U_m must come back at 1.0 (at resonance electric and magnetic energy are
    equal - if it does not, an E or H expression is wrong and the tool refuses).
    The ratio is u_e/u_m and not u_e/(u_e+u_m) on purpose: the latter is 0.5 for
    every mode that balances, so it cannot fail and would be a gate with no teeth.
    """
    kx = m * math.pi / a
    ky = n * math.pi / b
    kz = p * math.pi / d
    kc2 = kx * kx + ky * ky
    if kc2 <= 0.0:
        return None, None
    w0 = 2.0 * math.pi * f0
    mu = MU0 * mur
    eps = EPS0 * er
    dx, dy, dz = a / n_grid, b / n_grid, d / n_grid
    sum_e = sum_h = 0.0
    for i in range(n_grid):
        x = (i + 0.5) * dx
        for j in range(n_grid):
            y = (j + 0.5) * dy
            for k in range(n_grid):
                z = (k + 0.5) * dz
                if fam == "TE":
                    hz = math.cos(kx * x) * math.cos(ky * y) * math.sin(kz * z)
                    hx = -(kz * kx / kc2) * math.sin(kx * x) * math.cos(ky * y) * math.cos(kz * z)
                    hy = -(kz * ky / kc2) * math.cos(kx * x) * math.sin(ky * y) * math.cos(kz * z)
                    ex = -(w0 * mu * ky / kc2) * math.cos(kx * x) * math.sin(ky * y) * math.sin(kz * z)
                    ey = (w0 * mu * kx / kc2) * math.sin(kx * x) * math.cos(ky * y) * math.sin(kz * z)
                    e2 = ex * ex + ey * ey
                    h2 = hx * hx + hy * hy + hz * hz
                else:
                    ez = math.sin(kx * x) * math.sin(ky * y) * math.cos(kz * z)
                    ex = -(kz * kx / kc2) * math.cos(kx * x) * math.sin(ky * y) * math.sin(kz * z)
                    ey = -(kz * ky / kc2) * math.sin(kx * x) * math.cos(ky * y) * math.sin(kz * z)
                    hx = (w0 * eps * ky / kc2) * math.sin(kx * x) * math.cos(ky * y) * math.cos(kz * z)
                    hy = -(w0 * eps * kx / kc2) * math.cos(kx * x) * math.sin(ky * y) * math.cos(kz * z)
                    e2 = ex * ex + ey * ey + ez * ez
                    h2 = hx * hx + hy * hy
                sum_e += e2
                sum_h += h2
    cell = dx * dy * dz
    int_e = sum_e * cell
    int_h = sum_h * cell

    def _face(fn, nu, nv, du, dv):
        total = 0.0
        for i in range(nu):
            u = (i + 0.5) * du
            for j in range(nv):
                total += fn(u, (j + 0.5) * dv)
        return total * du * dv

    def _z_face(u, v):                       # fixed z, cos(kz z)^2 = 1
        if fam == "TE":
            hx = -(kz * kx / kc2) * math.sin(kx * u) * math.cos(ky * v)
            hy = -(kz * ky / kc2) * math.cos(kx * u) * math.sin(ky * v)
        else:
            hx = (w0 * eps * ky / kc2) * math.sin(kx * u) * math.cos(ky * v)
            hy = -(w0 * eps * kx / kc2) * math.cos(kx * u) * math.sin(ky * v)
        return hx * hx + hy * hy

    def _x_face(u, v):                       # fixed x (u = y, v = z)
        if fam == "TE":
            hy = -(kz * ky / kc2) * math.sin(ky * u) * math.cos(kz * v)
            hz = math.cos(ky * u) * math.sin(kz * v)
        else:
            hy = -(w0 * eps * kx / kc2) * math.sin(ky * u) * math.cos(kz * v)
            hz = 0.0
        return hy * hy + hz * hz

    def _y_face(u, v):                       # fixed y (u = x, v = z)
        if fam == "TE":
            hx = -(kz * kx / kc2) * math.sin(kx * u) * math.cos(kz * v)
            hz = math.cos(kx * u) * math.sin(kz * v)
        else:
            hx = (w0 * eps * ky / kc2) * math.sin(kx * u) * math.cos(kz * v)
            hz = 0.0
        return hx * hx + hz * hz

    wall = 2.0 * (_face(_z_face, n_grid, n_grid, dx, dy)
                  + _face(_x_face, n_grid, n_grid, dy, dz)
                  + _face(_y_face, n_grid, n_grid, dx, dz))
    u_e = 0.25 * eps * int_e
    u_m = 0.25 * mu * int_h
    energy = u_e + u_m
    loss = 0.5 * rs_ohm * wall
    if energy <= 0.0 or loss <= 0.0:
        return None, None
    return w0 * energy / loss, (u_e / u_m if u_m > 0.0 else None)

def _cavity_mode_chart(f_max, a, b, d, rs_ohm, c_eff, cap=200):
    """[(f, family, m, n, p)] with f <= f_max, or (None, count) when over cap."""
    m_max = int(math.floor(2.0 * f_max * a / c_eff)) + 1
    n_max = int(math.floor(2.0 * f_max * b / c_eff)) + 1
    p_max = int(math.floor(2.0 * f_max * d / c_eff)) + 1
    rows = []
    for fam in ("TE", "TM"):
        for m in range(m_max + 1):
            for n in range(n_max + 1):
                for p in range(p_max + 1):
                    if _cavity_mode_reason(fam, m, n, p):
                        continue
                    f = 0.5 * c_eff * math.sqrt((m / a) ** 2 + (n / b) ** 2
                                                + (p / d) ** 2)
                    if f <= f_max * (1.0 + 1e-12):
                        rows.append((f, fam, m, n, p))
    if len(rows) > cap:
        return None, len(rows)
    rows.sort()
    return rows, len(rows)


def waveguide_cavity(f0_hz, a_mm, b_mm=None, conductivity_s_m=5.8e7,
                     mu_r=1.0, er=1.0, mode="TE101", roughness_um=None,
                     modes_upto_hz=None):
    """Rectangular cavity in any TE_mnp / TM_mnp mode: length, Qc, R/Q, mode chart.

    No iris geometry and no coupling: those need a full-wave solve. What is here
    is the resonator every cavity filter is built from, with Qc computed twice -
    once from an exact closed form and once by integrating the fields over the
    walls - and the two must agree before the answer is returned.

    Two things that used to be refused and are now answered:

      * any mode, not just TE101 - the length comes from kz = p*pi/d, so a mode
        with p = 0 (TM_mn0) has no length to give and is refused by name rather
        than answered with a made-up d;
      * the mode CHART (pass modes_upto_hz), because "my filter is fine" is a
        statement about the neighbours, not about the mode you designed.
    """
    parsed = _parse_cavity_mode(mode)
    if parsed is None:
        return err("mode must be three single digits ('TE101') or underscored "
                   "integers ('TE_2_0_1'); got %r" % (mode,), "ARG_INVALID")
    fam, m, n, p = parsed
    why = _cavity_mode_reason(fam, m, n, p)
    if why:
        return err(why, "ARG_INVALID")
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

    rq = None
    if roughness_um is not None:
        try:
            rq_um = float(roughness_um)
        except (TypeError, ValueError):
            return err("roughness_um must be a number (RMS Rq in um) or omitted",
                       "ARG_RANGE")
        if rq_um < 0:
            return err("roughness_um must be >= 0: roughness cannot be negative. "
                       "The model wants Rq (RMS); Rz is NOT converted", "ARG_RANGE")
        rq = rq_um * 1e-6

    f_max = None
    if modes_upto_hz is not None:
        try:
            f_max = float(modes_upto_hz)
        except (TypeError, ValueError):
            return err("modes_upto_hz must be a number (the top of the chart) or "
                       "omitted", "ARG_RANGE")
        if f_max <= 0:
            return err("modes_upto_hz must be > 0", "ARG_RANGE")

    if b_mm is None:
        b = a / 2.0
        b_assumed = True
    else:
        b = float(b_mm) / 1000.0
        b_assumed = False
        if b <= 0:
            return err("b_mm must be > 0", "ARG_RANGE")

    c = C0 / math.sqrt(er * mur)
    kx = m * math.pi / a
    ky = n * math.pi / b
    kc2 = kx * kx + ky * ky
    k0 = 2.0 * math.pi * f0 / c
    f_cut = c * math.sqrt(kc2) / (2.0 * math.pi)

    if p == 0:
        # TM_mn0: kz = 0, the fields do not vary along the length, so d is not
        # determined by the resonance. Say the resonance instead of inventing a d.
        return err("TM_%d_%d_0 does not fix the cavity length: the fields are uniform "
                   "along it, so this mode resonates at %.6g Hz for ANY length "
                   "(its 2-D transverse resonance) - pass a mode with p >= 1 to get a "
                   "length, or size the length from the bandwidth and Q you want"
                   % (m, n, f_cut), "OUT_OF_VALIDITY")

    kz2 = k0 * k0 - kc2
    if kz2 <= 0.0:
        return err("f0_hz = %.6g Hz is below the %s_%d_%d cutoff of this "
                   "cross-section (%.6g Hz): no %s resonance exists at that frequency "
                   "and the length would be imaginary"
                   % (f0, fam, m, n, f_cut, _cavity_mode_name(fam, m, n, p)),
                   "OUT_OF_VALIDITY")
    kz = math.sqrt(kz2)
    d = p * math.pi / kz

    # surface resistance at f0, and the roughness multiplier on it. delta_s is
    # built from THIS module's MU0 so that K, Rs and the reported skin depth
    # never disagree with each other (roughness.skin_depth_m uses 4*pi*1e-7;
    # the two differ by 5.4e-10 relative, far below the 6 digits reported).
    rs = math.sqrt(math.pi * f0 * MU0 * mur / sigma)
    delta = math.sqrt(1.0 / (math.pi * f0 * MU0 * mur * sigma))
    k_rough = roughness_factor(rq, delta)
    rs_used = rs * k_rough

    if (fam, m, n, p) == ("TE", 1, 0, 1):
        bracket = (kx * kx * b * d + kz * kz * a * b
                   + (kx * kx + kz * kz) * a * d / 2.0)
        q_closed = (2.0 * math.pi * f0) * MU0 * mur * k0 * k0 * (a * b * d) \
            / (4.0 * rs_used * bracket)
        q_num = _te101_q_numeric(a, b, d, f0, rs_used,
                                 eps=EPS0 * er, mu=MU0 * mur)
        energy_ratio = None
        r_over_q = 4.0 * b / ((2.0 * math.pi * f0) * EPS0 * er * a * d)
    else:
        q_closed = _cavity_q_closed(fam, m, n, p, a, b, d, f0, rs_used, mur)
        q_num, energy_ratio = _cavity_q_numeric(fam, m, n, p, a, b, d, f0, rs_used,
                                                mur, er)
        r_over_q = None
        if q_closed is None:
            return err("this mode has no stored energy to divide by (kc = 0 or an "
                       "empty wall integral), so Qc is not defined here", "ARG_INVALID")
    q_agree_pct = (None if q_num is None
                   else 100.0 * (q_num - q_closed) / q_closed)
    if q_agree_pct is not None and abs(q_agree_pct) < 1e-9:
        q_agree_pct = 0.0          # do not print "-0.0" for an exact match
    if q_num is not None and abs(q_agree_pct) > 2.0:
        return err("the closed-form Qc (%.1f) and the field-integral Qc (%.1f) differ "
                   "by %.2f %%, so one of the two derivations is wrong"
                   % (q_closed, q_num, q_agree_pct), "INTERNAL_CHECK_FAILED")
    if energy_ratio is not None and abs(energy_ratio - 1.0) > 1e-6:
        return err("electric and magnetic stored energy disagree (U_e/U_m = %.9f), so "
                   "one of the field expressions is wrong" % (energy_ratio,),
                   "INTERNAL_CHECK_FAILED")

    out = {"mode": _cavity_mode_name(fam, m, n, p),
           "length_mm": round(d * 1000.0, 4),
           "a_mm": round(a * 1000.0, 4), "b_mm": round(b * 1000.0, 4),
           "f0_hz": round(f0, 6),
           "surface_resistance_ohm": round(rs_used, 6),
           "q_conductor": round(q_closed, 1),
           "q_conductor_numeric": (None if q_num is None else round(q_num, 1)),
           "q_agreement_pct": (None if q_agree_pct is None else round(q_agree_pct, 4)),
           "r_over_q_ohm": (None if r_over_q is None else round(r_over_q, 4)),
           "unloaded_q": round(q_closed, 1),
           "b_assumed": b_assumed}

    if rq is not None:
        out["roughness"] = {"rq_um": round(rq * 1e6, 6),
                            "loss_factor": round(k_rough, 6),
                            "skin_depth_um": round(delta * 1e6, 6),
                            # q_closed was computed with Rs*K, so the smooth value is
                            # q_closed*K. Dividing here would divide by K twice and land
                            # on a number no wall ever has.
                            "q_conductor_smooth": round(q_closed * k_rough, 1),
                            "model": "Hammerstad-Jensen K = 1+(2/pi)atan(1.4(Rq/ds)^2); "
                                     "Rs *= K, so Qc /= K"}

    if f_max is not None:
        rows, count = _cavity_mode_chart(f_max, a, b, d, rs_used, c)
        if rows is None:
            return err("modes_upto_hz = %.6g Hz asks for %d modes, more than the 200 "
                       "this tool will list; pass a narrower band"
                       % (f_max, count), "ARG_RANGE")
        chart = []
        for f, cfam, cm, cn, cp in rows:
            # Rs belongs to THIS mode's frequency, not to f0: Rs ~ sqrt(f), so
            # reusing rs_used made every row above f0 look 10-25 % better than it
            # is (the test that caught it compares a chart row against the same
            # mode asked for directly). K moves with the skin depth too.
            _delta_f = math.sqrt(1.0 / (math.pi * f * MU0 * mur * sigma))
            _rs_f = (math.sqrt(math.pi * f * MU0 * mur / sigma)
                     * roughness_factor(rq, _delta_f))
            q = _cavity_q_closed(cfam, cm, cn, cp, a, b, d, f, _rs_f, mur)
            chart.append({"mode": _cavity_mode_name(cfam, cm, cn, cp),
                          "f_hz": round(f, 6),
                          "f_over_f0": round(f / f0, 6),
                          "q_conductor": (None if q is None else round(q, 1))})
        for i, row in enumerate(chart):
            same = sum(1 for other in chart
                       if abs(other["f_hz"] - row["f_hz"]) <= 1e-9 * row["f_hz"])
            row["degeneracy"] = same
            row["is_operating"] = abs(row["f_hz"] - f0) <= 1e-9 * f0
        nxt = None
        for row in chart:
            if row["f_hz"] > f0 * (1.0 + 1e-9):
                nxt = dict(row)
                break
        out["mode_chart"] = chart
        out["mode_chart_count"] = len(chart)
        out["mode_chart_upto_hz"] = round(f_max, 6)
        out["next_mode"] = nxt

    not_computed = ["iris / coupling-window geometry (full-wave)",
                    "dielectric and radiation loss (Qd, Qr)"]
    if rq is None:
        not_computed.append("surface roughness correction to Qc (pass roughness_um)")
    if f_max is None:
        not_computed.append("the mode chart (pass modes_upto_hz): every answer is "
                           "this one mode, not the neighbours that may sit in your band")
    not_computed.append("thermal drift of the resonance (needs the expansion "
                        "coefficient)")
    if r_over_q is None:
        not_computed.append("R/Q of %s: R/Q is the integral of E along the coupling "
                           "path (a probe or a loop), so it is only reported for "
                           "TE101 where 4b/(w*eps*a*d) is the standard measure"
                           % (_cavity_mode_name(fam, m, n, p),))

    return ok(
        value=out,
        unit="mm",
        model="rectangular %s cavity: resonance, wall-loss Qc%s%s"
              % (_cavity_mode_name(fam, m, n, p),
                 ", R/Q" if r_over_q is not None else " (R/Q is TE101 only)",
                 ", mode chart" if f_max is not None else ""),
        basis=("f_mnp = c/2 sqrt((m/a)^2+(n/b)^2+(p/d)^2); Qc = w*U/Pc with "
               "U = mu*int|H|^2/2 and Pc = Rs*contour|H_tan|^2/2, evaluated twice "
               "(closed-form sine/cosine integrals and a field integration on a "
               "grid); the two must agree within 2 % or the answer is withheld"
               + ("; R/Q = 4b/(w*eps*a*d) (TE101)" if r_over_q is not None else "")
               + ("; the mode chart enumerates every TE_mnp/TM_mnp below "
                  "modes_upto_hz for this cavity, each with its closed-form Qc - the "
                  "grid cross-check is run on the mode you asked for, not on all of "
                  "them" if f_max is not None else "")),
        precision=("exact for a perfect rectangular cavity with smooth walls and no "
                   "coupling; Qc is the ideal-conductor value - real cavities lose "
                   "another 10-30 % to surface finish, joints and the iris, and "
                   "dielectric loss is not included at all"),
        assumptions={"walls": "same conductivity on every wall; surface roughness %s"
                              % ("included as Rs *= K (one-number Hammerstad-Jensen)"
                                 if rq is not None else "ignored"),
                     "dielectric": "lossless (er and mu_r only shift the resonance)",
                     "coupling": "none: no iris, no probe, no loop"},
        extra={"not_computed": not_computed,
               "note": "a cavity filter is this resonator plus N-1 irises; the iris "
                       "dimensioning is exactly the part that is not here",
               "q_audit": "two independent derivations of Qc are compared; a mismatch "
                          "above 2 % withholds the answer"})
