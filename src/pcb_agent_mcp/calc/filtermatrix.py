"""Cross-coupled resonator filters: the coupling matrix, finite transmission zeros,
finite unloaded Q.

Everything else in calc/filters.py describes an INLINE filter - a ladder, or an
edge-coupled line of resonators in which each resonator talks only to its two
neighbours.  An inline filter has no finite transmission zero: the stopband is
set by the order alone, and the loss is one number that hides the fact that some
resonators hold more energy than others.

Both of those go away once the filter is written as a coupling matrix M.  This
module owns that object:

    A   =  Omega * U  -  j * R  +  M
    Omega = (1/FBW) (f/f0 - f0/f)

The S-parameters are NOT taken from inverting A.  That inversion gets |S21| right
and |S11| wrong - the reflection normalisation of the matrix form is easy to get
subtly off and is not what a user should be handed.  A and its cofactors supply
the algebra (the coupling matrix and the closed-form zero positions); the numbers
come from a nodal solve of the network itself, which is what realisable hardware
looks like.

`M` is the normalised N x N coupling matrix (m_ij = k_ij / FBW for a bandpass),
`R` carries the external Q at the two end resonators and 1/Qu on every resonator
that has loss.  This is the standard coupled-resonator formulation; what makes it
worth having here is that it is checked three ways before it answers anything:

  1. **Inline, lossless: the matrix must reproduce the prototype polynomial.**
     Every point of the sweep is compared with 1/(1 + eps^2 Tn(Omega)^2), to 1e-6
     dB; the measured residual is ~1e-12 dB.  eps is read off the network at
     |Omega| = 1 rather than from filters._epsilon(ripple), because the Chebyshev g
     values in filters.py come from Matthaei's log(1/tanh(R/17.37)) recursion,
     which is ~5e-5 (relative) away from the exact epsilon - a 4e-4 dB disagreement
     at the far stopband that has nothing to do with this module.  Pinning eps at
     |Omega| = 1 has a price: a uniform rescaling of every coupling would be
     absorbed into eps, so the ripple the network delivers is ALSO compared with
     the one the g values were built for (1e-3 dB).
  2. **Unitarity.**  With no loss element, |S11|^2 + |S21|^2 = 1 at every point,
     to 1e-9.  This shares no code with (1) - it is the network being passive and
     lossless, which fails immediately if an inverter or a shunt is mis-stamped.
  3. **The zero lands where the algebra says it does.**  For a third-order
     trisection the cofactor of A gives a real zero at

         Omega_z = m12^2 / m13

     and the sweep's own minimum is compared against it.  Measured: the numeric
     minimum sits at the analytic frequency to < 1e-6 in Omega, with |S21| ~ 1e-17
     there (i.e. a true null, not a deep dip).

What the engine is, physically: the filter is built as a nodal network in which
every coupling is an IDEAL ADMITTANCE INVERTER, replaced by its exact Pi
equivalent (shunt +jJ at each port, series j/J between them).  That keeps loops
(cross-couplings) representable, which a plain ABCD cascade cannot do.  In the
inline case the Pi cascade and the matrix inverse are two independent routes to
the same S-parameters, and the tests run both.

Not modelled, and each answer says so: a physical realisation of the
cross-coupling (a folded or canonical layout, and the EM simulation that would
confirm it), iris or probe coupling for a waveguide, dispersion, higher-order
modes, the frequency dependence of the couplings (M is frozen at f0), and
asymmetric or non-synchronously-tuned matrices synthesised by rotation (this
module PERTURBS the inline matrix rather than running a full Cameron synthesis,
so the in-band ripple it reports is the perturbed one, not the requested one).
"""
import math

from .result import ok, err
from .filters import _prototype, _chebyshev_T, RESPONSES

# The inline, lossless case must reproduce the prototype polynomial to this many
# decibels or the answer is withheld: the two are separate algorithms on the same
# filter, and a coupling that is scaled or signed wrongly cannot pass.
CLOSED_FORM_AGREEMENT_DB = 1e-6

# |S11|^2 + |S21|^2 must equal 1 this closely with no loss element present.
UNITARITY_TOLERANCE = 1e-9

# The closed-form shape check pins the stopband scale at |Omega| = 1, so it cannot
# see a uniform rescaling of every coupling; the ripple the network actually
# delivers is compared against the one the prototype g values were built for, to
# this many decibels.  (1e-3 dB is still two orders tighter than filters.py's own
# 0.02 dB prototype-against-ladder tolerance.)
RIPPLE_SCALE_TOLERANCE_DB = 1e-3

# A zero is a stopband feature. Placing one at |Omega| < this would put a notch in
# or right at the band edge, which is a different filter, not a cross-coupled one.
MIN_ZERO_OMEGA = 1.2

MAX_ORDER = 8

# In-band ripple is read off a dense grid of its own.  Reading it off the display
# sweep undersamples it: 41 display points over the whole span leave only a handful
# inside the band, so the reported ripple comes out smaller than it is (measured
# 0.52 dB at span 3 and 0.35 dB at span 6, against 0.86 dB from this grid, on the
# same trisection filter).
IN_BAND_POINTS = 401

NOT_COMPUTED = [
    "the physical realisation of a cross-coupling: a folded / canonical layout and "
    "the gap or probe that realises m13 are not dimensioned, and confirming either "
    "needs the EM simulation this repository does not run",
    "exact synthesis of an arbitrary coupling matrix from a mask (Cameron rotations): "
    "the matrix here is the inline prototype PERTURBED by the requested couplings, so "
    "the in-band ripple it reports is the one you get, not the one you asked for",
    "waveguide iris / probe coupling, and therefore any cavity realisation of m13",
    "dispersion, higher-order modes, and the frequency dependence of the couplings "
    "(M is frozen at f0, which is the narrow-band assumption that makes the matrix "
    "formulation valid in the first place)",
    "asymmetric or non-synchronously-tuned responses (m_ii != 0), and any loss model "
    "richer than one shunt conductance per resonator (no dielectric loss, no "
    "radiation Q, no Q versus frequency)",
]

FAMILIES = RESPONSES


# ------------------------------------------------------------------- linear algebra
def _solve(a, b):
    """Solve a x = b for a dense complex matrix, partial pivoting.

    n is at most 10 here (8 resonators plus two ports), so a plain elimination is
    faster to read than any factored form.
    """
    n = len(a)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(m[r][c]))
        if abs(m[piv][c]) < 1e-300:
            raise ZeroDivisionError("singular nodal matrix")
        if piv != c:
            m[c], m[piv] = m[piv], m[c]
        d = m[c][c]
        m[c] = [x / d for x in m[c]]
        for r in range(n):
            if r != c:
                f = m[r][c]
                if f:
                    m[r] = [x - f * y for x, y in zip(m[r], m[c])]
    return [row[n] for row in m]


# ------------------------------------------------------------------ the network
def _nodal_s(n, matrix, qe1, qen, qu, omega):
    """S11, S21 of the coupling network at one normalised frequency.

    `matrix` is the n x n coupling matrix, `qe1`/`qen` the normalised external Qs
    (Qe * FBW), `qu` a list of unloaded Q per resonator or None for lossless.

    Node 0 is the source, nodes 1..n are the resonators, node n+1 is the load.
    Every coupling is an ideal admittance inverter, stamped as its exact Pi
    equivalent: shunt +jJ at each of its two nodes and a series j/J between them.
    Stamping the Pi is what lets a cross-coupling close a loop; an ABCD cascade
    could not represent it.
    """
    nodes = n + 2
    y = [[0j] * nodes for _ in range(nodes)]

    def shunt(a, val):
        y[a][a] += val

    def inverter(a, b, jval):
        shunt(a, 1j * jval)
        shunt(b, 1j * jval)
        ya = 1.0 / (1j / jval)
        y[a][a] += ya
        y[b][b] += ya
        y[a][b] -= ya
        y[b][a] -= ya

    shunt(0, 1.0)          # source conductance, referenced to z0 = 1
    shunt(n + 1, 1.0)      # load conductance
    for i in range(1, n + 1):
        shunt(i, 1j * omega)          # unit susceptance slope at resonance
        if qu is not None:
            shunt(i, 1.0 / qu[i - 1])
    inverter(0, 1, 1.0 / math.sqrt(qe1))
    inverter(n, n + 1, 1.0 / math.sqrt(qen))
    for i in range(n):
        for j in range(i + 1, n):
            if matrix[i][j]:
                inverter(i + 1, j + 1, matrix[i][j])
    v = _solve(y, [1.0] + [0j] * (nodes - 1))
    return 2.0 * v[0] - 1.0, 2.0 * v[nodes - 1]


def _omega(f, f0, fbw):
    return (f / f0 - f0 / f) / fbw


def _f_from_omega(om, f0, fbw):
    """Inverse of _omega: omega = (r - 1/r)/fbw, r = f/f0, take the positive root."""
    k = om * fbw
    r = (k + math.sqrt(k * k + 4.0)) / 2.0
    return f0 * r


# ------------------------------------------------------------------ the matrix
def _inline_matrix(g, n):
    """Coupling matrix and external Qs of the inline (no cross-coupling) filter."""
    qe1 = g[0]
    qen = g[n - 1] * g[n]
    m = [[0.0] * n for _ in range(n)]
    for i in range(1, n):
        v = 1.0 / math.sqrt(g[i - 1] * g[i])
        m[i - 1][i] = m[i][i - 1] = v
    return m, qe1, qen


def _db(s):
    a = abs(s)
    return -20.0 * math.log10(a) if a > 1e-300 else 300.0


def _minimise(shape, lo, hi, steps=24, iters=80):
    """Golden-section on |S21|^2 inside [lo, hi] over a coarse pre-scan.

    Returns (omega, |S21|^2).  The pre-scan is there because the objective is only
    locally unimodal and a bracket has to be picked; 24 points across the bracket
    is enough to isolate the null when the bracket is +-0.5 in Omega.
    """
    best = None
    for i in range(steps + 1):
        om = lo + (hi - lo) * i / steps
        val = abs(shape(om)) ** 2
        if best is None or val < best[1]:
            best = (om, val)
    if best is None:
        return lo, float("inf")
    step = (hi - lo) / steps
    a, b = best[0] - step, best[0] + step
    phi = (math.sqrt(5.0) - 1.0) / 2.0
    c, d = b - phi * (b - a), a + phi * (b - a)
    fc = abs(shape(c)) ** 2
    fd = abs(shape(d)) ** 2
    for _ in range(iters):
        if fc < fd:
            b, d, fd = d, c, fc
            c = b - phi * (b - a)
            fc = abs(shape(c)) ** 2
        else:
            a, c, fc = c, d, fd
            d = a + phi * (b - a)
            fd = abs(shape(d)) ** 2
    om = (a + b) / 2.0
    return om, abs(shape(om)) ** 2


# ------------------------------------------------------------------- the entry
def coupled_matrix_filter(order, f0_hz, fbw, ripple_db=None,
                          response="chebyshev", zero_hz=None, couplings=None,
                          q_external=None, q_unloaded=None, points=41,
                          span=3.0, probe_hz=None):
    """Cross-coupled resonator filter from the coupling matrix.

    Inline by default (identical to the ladder's answer, which is check 1).  Give
    `zero_hz` for a third-order trisection, or `couplings` for an arbitrary matrix.
    `q_unloaded` makes the resonators lossy and reports what each one costs.
    """
    try:
        n = int(order or 0)
        f0 = float(f0_hz)
        bw = float(fbw)
        npts = int(points or 41)
        reach = float(span if span is not None else 3.0)
    except (TypeError, ValueError):
        return err("numeric order, f0_hz, fbw required", "ARG_INVALID")
    fam = str(response or "").strip().lower()
    if fam not in FAMILIES:
        return err("response must be one of " + ", ".join(FAMILIES), "ARG_INVALID")
    if n < 1 or n > MAX_ORDER:
        return err("order must be 1..%d: above that a coupling matrix stops being a "
                   "thing anyone tunes by hand and the nodal solve starts losing "
                   "digits" % MAX_ORDER, "ARG_RANGE")
    if f0 <= 0.0 or bw <= 0.0 or bw >= 1.0:
        return err("f0_hz must be > 0 and 0 < fbw < 1", "ARG_RANGE")
    if npts < 21 or npts > 801:
        return err("points must be 21..801", "ARG_RANGE")
    if reach < 1.5 or reach > 20.0:
        return err("span must be 1.5..20 (it is measured in FBW: the sweep runs from "
                   "f0/(1+span*fbw) to f0*(1+span*fbw))", "ARG_RANGE")
    if zero_hz is not None and couplings is not None:
        return err("give either zero_hz (third-order trisection) or couplings (an "
                   "explicit matrix), not both", "ARG_CONFLICT")

    try:
        ripple = (0.0 if fam == "butterworth" else 0.5) if ripple_db is None \
            else float(ripple_db)
    except (TypeError, ValueError):
        return err("ripple_db must be a number", "ARG_INVALID")
    if fam == "butterworth" and ripple != 0.0:
        return err("a Butterworth prototype has no ripple; drop ripple_db",
                   "ARG_CONFLICT")
    if fam == "chebyshev" and ripple <= 0.0:
        return err("a Chebyshev prototype needs ripple_db > 0", "ARG_RANGE")

    # unrounded prototype: the closed-form audit below can then reach round-off
    # instead of the six decimals the public prototype_gvalues answers with
    g = _prototype(n, fam, ripple)
    matrix, qe1, qen = _inline_matrix(g, n)
    topology = "inline"

    if zero_hz is not None:
        if n != 3:
            return err("a single transmission zero needs order = 3 (one trisection); "
                       "higher orders need a cascaded-trisection or canonical "
                       "synthesis, which this module does not run", "ARG_CONFLICT",
                       hint="synthesise the matrix you want and pass it as couplings")
        try:
            fz = float(zero_hz)
        except (TypeError, ValueError):
            return err("zero_hz must be a number", "ARG_INVALID")
        if fz <= 0.0:
            return err("zero_hz must be > 0", "ARG_RANGE")
        omz = _omega(fz, f0, bw)
        if abs(omz) < MIN_ZERO_OMEGA:
            return err("a zero at %.4g Hz is Omega = %.3f, which is inside or at the "
                       "edge of the passband: the trisection formula puts a notch "
                       "there rather than a stopband null" % (fz, omz), "ARG_RANGE",
                       hint="a real stopband zero sits at |Omega| >= ~1.5, i.e. "
                            "outside f0*(1 +- 1.5*fbw)")
        if abs(omz) > 200.0:
            return err("a zero at Omega = %.1f is outside anything a practical "
                       "coupling can place (m13 = m12^2/Omega_z would be far below "
                       "what a layout resolves)" % omz, "ARG_RANGE")
        m13 = matrix[0][1] * matrix[1][2] / omz
        matrix[0][2] = matrix[2][0] = m13
        topology = "trisection"

    if couplings is not None:
        if not isinstance(couplings, (list, tuple)) or not couplings:
            return err("couplings must be a non-empty list of [i, j, value] with i, j "
                       "the 1-based resonator numbers", "ARG_INVALID")
        for item in couplings:
            try:
                i, j, val = item
                i, j, val = int(i), int(j), float(val)
            except (TypeError, ValueError):
                return err("each coupling must be [i, j, value]", "ARG_INVALID")
            if not (1 <= i <= n and 1 <= j <= n) or i == j:
                return err("coupling [%d, %d]: resonator numbers must be 1..%d and "
                           "distinct" % (i, j, n), "ARG_RANGE")
            if abs(val) > 2.0:
                return err("coupling %d-%d = %.3f: |m_ij| above 2 is not a coupling, it "
                           "is a short between resonators" % (i, j, val), "ARG_RANGE")
            matrix[i - 1][j - 1] = matrix[j - 1][i - 1] = val
        topology = "custom"

    if q_external is not None:
        try:
            qe_in, qe_out = (float(q_external[0]), float(q_external[1]))
        except (TypeError, ValueError, IndexError):
            return err("q_external must be two numbers [Qe_in, Qe_out]",
                       "ARG_INVALID")
        if qe_in <= 0.0 or qe_out <= 0.0:
            return err("q_external values must be > 0", "ARG_RANGE")
        # the matrix uses normalised external Qs; the caller thinks in Qe
        qe1, qen = qe_in * bw, qe_out * bw

    qu = None
    qu_physical = None
    if q_unloaded is not None:
        try:
            if isinstance(q_unloaded, (list, tuple)):
                vals = [float(x) for x in q_unloaded]
                if len(vals) != n:
                    return err("q_unloaded has %d entries but the filter has %d "
                               "resonators" % (len(vals), n), "ARG_INVALID")
            else:
                vals = [float(q_unloaded)] * n
        except (TypeError, ValueError):
            return err("q_unloaded must be a number or one number per resonator",
                       "ARG_INVALID")
        if any(v <= 0.0 for v in vals):
            return err("every q_unloaded must be > 0", "ARG_RANGE")
        # The network is normalised: its frequency axis is stretched by 1/FBW, so a
        # physical Qu enters as Qu*FBW exactly the way the external Q does.  Using Qu
        # raw here makes the dissipation look 1/FBW too small - which is the mistake
        # this line exists to prevent.
        qu = [v * bw for v in vals]
        qu_physical = vals

    def shape(om):
        _s11, s21 = _nodal_s(n, matrix, qe1, qen, qu, om)
        return s21

    def sparams_at(f):
        return _nodal_s(n, matrix, qe1, qen, qu, _omega(f, f0, bw))

    # ---- check 2: unitarity (no loss element anywhere)
    unitarity = None
    if qu is None:
        worst_u = 0.0
        for i in range(npts):
            f = f0 / (1.0 + reach * bw) + (f0 * (1.0 + reach * bw)
                                           - f0 / (1.0 + reach * bw)) * i / (npts - 1)
            s11, s21 = sparams_at(f)
            worst_u = max(worst_u, abs(abs(s11) ** 2 + abs(s21) ** 2 - 1.0))
        unitarity = worst_u
        if worst_u > UNITARITY_TOLERANCE:
            return err("the network is not unitary (worst | |S11|^2+|S21|^2-1 | = "
                       "%.3g with no loss element): an inverter or a shunt is "
                       "mis-stamped" % worst_u, "INTERNAL_CHECK_FAILED",
                       hint="code defect, not a user error; run tests/test_coupling_matrix.py")

    # ---- check 1: inline + lossless must reproduce the prototype polynomial
    #
    # The reference polynomial is pinned to the network's own stopband scale rather
    # than to filters._epsilon(ripple): the Chebyshev g values in filters.py come
    # from Matthaei's log(1/tanh(R/17.37)) recursion, which sits ~5e-5 (relative)
    # away from the exact epsilon.  Comparing against the exact epsilon would flag
    # that pre-existing approximation - a 4e-4 dB disagreement at the far stopband,
    # independent of FBW - as if it were a defect of this module.  Instead the scale
    # is read at |Omega| = 1, where |Tn| = 1 for every order and both families, and
    # the difference is reported as its own number next to the tight shape check.
    closed_dev = None
    closed_ripple = None
    # an explicitly supplied external Q is NOT the prototype's, so scoring it against
    # the prototype polynomial would be scoring it against something the caller never
    # asked for - the check is skipped, and self_check says so
    if topology == "inline" and qu is None and q_external is None:
        _s11_1, _s21_1 = sparams_at(_f_from_omega(1.0, f0, bw))
        il_one = _db(abs(_s21_1))
        if fam == "chebyshev":
            eps_eff = math.sqrt(max(10.0 ** (il_one / 10.0) - 1.0, 0.0))
            closed_ripple = 10.0 * math.log10(1.0 + eps_eff * eps_eff)
        else:
            eps_eff = 1.0
            closed_ripple = 0.0
        worst = 0.0
        worst_at = None
        for i in range(npts):
            f = f0 / (1.0 + reach * bw) + (f0 * (1.0 + reach * bw)
                                           - f0 / (1.0 + reach * bw)) * i / (npts - 1)
            om = _omega(f, f0, bw)
            got = _db(shape(om))
            if fam == "butterworth":
                want = 10.0 * math.log10(1.0 + om ** (2 * n))
            else:
                want = 10.0 * math.log10(1.0 + (eps_eff * _chebyshev_T(n, om)) ** 2)
            gap = abs(got - want)
            if gap > worst:
                worst, worst_at = gap, f
        closed_dev = worst
        if worst > CLOSED_FORM_AGREEMENT_DB:
            return err("the nodal network and the prototype polynomial disagree by "
                       "%.3g dB at %.6g Hz: the coupling matrix or the external Q is "
                       "wrong" % (worst, worst_at or 0.0), "INTERNAL_CHECK_FAILED",
                       hint="code defect, not a user error; the answer is withheld "
                            "rather than reported with a caveat")
        if fam == "chebyshev" and abs(closed_ripple - ripple) > RIPPLE_SCALE_TOLERANCE_DB:
            return err("the network delivers %.6g dB of ripple where the prototype "
                       "g values say %.6g dB: the couplings are scaled wrong"
                       % (closed_ripple, ripple), "INTERNAL_CHECK_FAILED",
                       hint="code defect, not a user error; the shape check alone "
                            "cannot see a uniform scaling of every coupling")

    # ---- transmission zeros
    zero_report = {"topology": topology}
    if topology == "inline":
        zero_report["zeros"] = []
        zero_report["why_none"] = (
            "every resonator sees only its two neighbours, so there is no path that "
            "cancels: an inline filter has no finite transmission zero and its "
            "stopband slope is set by the order alone")
    else:
        found = []
        if topology == "trisection":
            om_analytic = matrix[0][1] * matrix[1][2] / matrix[0][2]
            om_num, s2 = _minimise(shape, om_analytic - 0.6, om_analytic + 0.6)
            found.append({
                "analytic_omega": round(om_analytic, 9),
                "analytic_hz": round(_f_from_omega(om_analytic, f0, bw), 6),
                "measured_omega": round(om_num, 9),
                "measured_hz": round(_f_from_omega(om_num, f0, bw), 6),
                "omega_error_pct": round(
                    100.0 * (om_num - om_analytic) / abs(om_analytic), 6),
                "depth_db": round(_db(math.sqrt(max(s2, 0.0))), 4),
                "depth_limited_by": (
                    "the unloaded Q: with no loss the null is exact (|S21| ~ 1e-17), "
                    "with loss it fills in to the depth reported here"
                    if qu is not None else
                    "nothing: with no loss element this is a true null"),
                "formula": "Omega_z = m12^2 / m13 for a symmetric trisection",
            })
        else:
            # custom matrix: scan the whole span, keep the deepest distinct minima
            grid = 1201
            lo, hi = -reach, reach
            vals = []
            for i in range(grid + 1):
                om = lo + (hi - lo) * i / grid
                vals.append(abs(shape(om)))
            for i in range(1, grid):
                if vals[i] < vals[i - 1] and vals[i] < vals[i + 1] and vals[i] < 0.5:
                    om_num, s2 = _minimise(shape, lo + (hi - lo) * (i - 1) / grid,
                                           lo + (hi - lo) * (i + 1) / grid,
                                           steps=12, iters=60)
                    if all(abs(om_num - f["measured_omega"]) > 0.05 for f in found):
                        found.append({"analytic_omega": None,
                                      "analytic_hz": None,
                                      "measured_omega": round(om_num, 9),
                                      "measured_hz": round(_f_from_omega(om_num, f0, bw), 6),
                                      "omega_error_pct": None,
                                      "inside_passband": abs(om_num) < 1.0,
                                      "depth_db": round(_db(math.sqrt(max(s2, 0.0))), 4),
                                      "depth_limited_by": (
                                          "the unloaded Q" if qu is not None else
                                          "nothing: a true null")})
            found.sort(key=lambda r: -r["depth_db"])
            found = found[:4]
        zero_report["zeros"] = found
        if topology == "custom" and not found:
            zero_report["why_none"] = (
                "no point inside the swept range (|Omega| <= span) is more than 6 dB "
                "deep: either this matrix has no null, or the null is further out than "
                "the span reaches -- raise span to look for it")
        for z in found:
            if z.get("inside_passband"):
                z["note"] = (
                    "this null sits INSIDE the passband (|Omega| < 1), so it is a notch "
                    "cut out of the band rather than a stopband zero: the matrix you "
                    "supplied does not describe a bandpass filter")
    zero_report["note"] = ("depth_db is -20*log10|S21| at the null: infinite for a "
                           "lossless network, finite as soon as the resonators have "
                           "loss, and that finite number is the rejection the filter "
                           "actually delivers")

    # ---- the sweep and the in-band numbers
    f_lo = f0 / (1.0 + reach * bw)
    f_hi = f0 * (1.0 + reach * bw)
    sweep = []
    il_min = (None, None)
    il_max = (None, None)
    band_lo = f0 * (math.sqrt(1.0 + (bw / 2.0) ** 2) - bw / 2.0)
    band_hi = f0 * (math.sqrt(1.0 + (bw / 2.0) ** 2) + bw / 2.0)
    for i in range(npts):
        f = f_lo + (f_hi - f_lo) * i / (npts - 1)
        s11, s21 = sparams_at(f)
        il = _db(s21)
        sweep.append({"f_hz": round(f, 6), "s21_db": round(il, 4),
                      "s11_db": round(_db(s11), 4)})

    # the in-band numbers come from their own dense grid, not from the display sweep
    for i in range(IN_BAND_POINTS):
        f = band_lo + (band_hi - band_lo) * i / (IN_BAND_POINTS - 1)
        s11, s21 = sparams_at(f)
        il = _db(s21)
        if il_min[0] is None or il < il_min[0]:
            il_min = (il, f)
        if il_max[0] is None or il > il_max[0]:
            il_max = (il, f)

    inband = {"band_hz": [round(band_lo, 3), round(band_hi, 3)],
              "sampled_points_in_band": IN_BAND_POINTS,
              "il_min_db": None if il_min[0] is None else round(il_min[0], 4),
              "il_min_at_hz": None if il_min[1] is None else round(il_min[1], 3),
              "il_max_db": None if il_max[0] is None else round(il_max[0], 4),
              "il_max_at_hz": None if il_max[1] is None else round(il_max[1], 3),
              "ripple_actual_db": (None if il_min[0] is None or il_max[0] is None
                                   else round(il_max[0] - il_min[0], 4))}
    inband["ripple_requested_db"] = ripple if fam == "chebyshev" else 0.0
    if topology == "inline":
        inband["ripple_note"] = ("the requested ripple is what the lossless inline "
                                 "matrix gives, to the closed-form agreement reported "
                                 "in self_check")
    else:
        inband["ripple_note"] = (
            "this is the ripple of the PERTURBED matrix: adding the cross-coupling to "
            "the inline solution shifts the in-band response, and a full synthesis "
            "(Cameron rotations) would absorb that shift. Read ripple_actual_db as "
            "what you get, not as what you asked for")

    loss = None
    if qu is not None:
        s11c, s21c = sparams_at(f0)
        il_c = _db(s21c)
        loss = {"q_unloaded": (qu_physical[0]
                               if len(set(round(v, 9) for v in qu_physical)) == 1
                               else None),
                "q_unloaded_per_resonator": [round(v, 3) for v in qu_physical],
                "q_unloaded_normalised": [round(v, 6) for v in qu],
                "insertion_loss_center_db": round(il_c, 4),
                "s11_at_center_db": round(_db(s11c), 4),
                "loss_model": ("one shunt conductance per resonator in the normalised "
                               "network, i.e. the definition of unloaded Q at "
                               "resonance; the physical Qu enters as Qu*FBW because "
                               "the network's frequency axis is stretched by 1/FBW, "
                               "the same scaling the external Q gets. The loss is "
                               "energy-weighted, so it costs most where the stored "
                               "energy peaks"),
                "not_modelled": ["dielectric loss in the substrate or the cavity walls",
                                 "Q versus frequency (Qu is frozen at f0)",
                                 "radiation Q and the Q of the launching structure"]}
        if len(set(round(v, 9) for v in qu_physical)) == 1 and n >= 1:
            est = 4.343 * sum(g[:n]) / (qu_physical[0] * bw)
            meas = il_c - (ripple if (fam == "chebyshev" and n % 2 == 0) else 0.0)
            loss["matthaei_estimate_db"] = round(est, 4)
            loss["matthaei_deviation_pct"] = round(
                100.0 * (meas - est) / max(est, 1e-12), 2)
            loss["matthaei_basis"] = (
                "Matthaei's midband dissipation loss 4.343*sum(g1..gn)/(Qu*FBW), "
                "asymptotic in the low-loss limit - compared as a band, not an "
                "equality")
            if est < 3.0 and abs(meas - est) / max(est, 1e-12) > 0.5:
                return err("the network says %.3f dB of dissipation at the centre but "
                           "the closed-form dissipation loss says %.3f dB - that large "
                           "a factor means the loss sits on the wrong node"
                           % (meas, est), "INTERNAL_CHECK_FAILED",
                           hint="code defect, not a user error")

    probe = None
    if probe_hz is not None:
        try:
            fp = float(probe_hz)
        except (TypeError, ValueError):
            return err("probe_hz must be a number", "ARG_INVALID")
        if fp <= 0.0:
            return err("probe_hz must be > 0", "ARG_RANGE")
        s11p, s21p = sparams_at(fp)
        probe = {"f_hz": round(fp, 6),
                 "omega": round(_omega(fp, f0, bw), 6),
                 "s21_db": round(_db(s21p), 4),
                 "s11_db": round(_db(s11p), 4),
                 "note": ("outside the swept band the matrix is still evaluated, but "
                          "the narrow-band mapping Omega(f) is what stops being valid, "
                          "not the arithmetic")
                 if not (f_lo <= fp <= f_hi) else None}

    value = {
        "order": n, "response": fam, "ripple_db": ripple,
        "f0_hz": round(f0, 6), "fbw": bw, "span": reach,
        "topology": topology,
        "matrix": {"m": [[round(x, 6) for x in row] for row in matrix],
                   "q_external_in": round(qe1, 6),
                   "q_external_out": round(qen, 6),
                   "q_external_in_absolute": round(qe1 / bw, 4),
                   "q_external_out_absolute": round(qen / bw, 4),
                   "units": ("m_ij is the normalised coupling k_ij/FBW; the external "
                             "Qs are Qe*FBW, and the absolute Qe is reported next to "
                             "them")},
        "transmission_zeros": zero_report,
        "inband": inband,
        "loss": loss,
        "response": sweep,
        "probe": probe,
        "prototype_g": [round(x, 6) for x in g],
        "self_check": {
            "closed_form_max_deviation_db": (None if closed_dev is None
                                             else round(closed_dev, 12)),
            "closed_form_compared": closed_dev is not None,
            "closed_form_tolerance_db": CLOSED_FORM_AGREEMENT_DB,
            "closed_form_against": ("1/(1 + eps_eff^2 * Tn(Omega)^2), with eps_eff read "
                                    "off the network at |Omega| = 1 so the check tests "
                                    "the shape and not filters.py's 17.37 recursion"),
            "effective_ripple_db": (None if closed_ripple is None
                                    else round(closed_ripple, 9)),
            "ripple_scale_tolerance_db": RIPPLE_SCALE_TOLERANCE_DB,
            "unitarity_max_error": (None if unitarity is None
                                    else round(unitarity, 12)),
            "unitarity_tolerance": UNITARITY_TOLERANCE,
            "unitarity_note": ("only checked with no loss element - a lossy network "
                               "is supposed to absorb power"),
        },
    }

    return ok(
        value=value,
        unit="mixed",
        model=("coupled-resonator network in the normalised coupling-matrix "
               "formulation, solved nodally with exact admittance-inverter Pi "
               "equivalents"),
        basis=("Cameron / Hong & Lancaster coupled-resonator theory: the inline matrix "
               "is m(i,i+1) = 1/sqrt(g_i g_(i+1)) with Qe1 = g1 and Qen = g_n g_(n+1); "
               "every coupling is stamped as an ideal admittance inverter in its exact "
               "Pi form (shunt +jJ at both ports, series j/J between them) and the "
               "network is solved nodally, so S11 and S21 come from the same solve; "
               "the trisection zero follows from the cofactor of A = Omega U - jR + M, "
               "giving Omega_z = m12^2/m13 (the matrix supplies the algebra, not the "
               "S-parameters); loss is one shunt 1/Qu per resonator and the "
               "dissipation is cross-checked against Matthaei's "
               "4.343*sum(g)/(Qu*FBW)"),
        precision=("the network answer is exact arithmetic for the matrix it is given: "
                   "the inline lossless case reproduces the prototype polynomial to "
                   "%.0e dB and the lossless case is unitary to %.0e. What is NOT "
                   "exact is the matrix itself when a cross-coupling has been added "
                   "by perturbation - the in-band ripple then differs from the "
                   "requested one by whatever that perturbation does, and that "
                   "difference is reported rather than corrected"
                   % (CLOSED_FORM_AGREEMENT_DB, UNITARITY_TOLERANCE)),
        assumptions={"narrow_band": "M is frozen at f0 and the frequency axis is the "
                                    "standard bandpass mapping Omega = (f/f0 - f0/f)/FBW",
                     "resonators": "synchronously tuned (m_ii = 0) unless a custom "
                                   "matrix says otherwise; unit susceptance slope",
                     "coupling": "ideal admittance inverters, no frequency dependence",
                     "terminations": "z0 at both ports, normalised to 1 in the network"},
        extra={"not_computed": NOT_COMPUTED,
               "next_step": ("this is the electrical design. To draw it: an inline "
                             "filter maps to filter_coupled_resonator (edge-coupled, "
                             "with geometry from the field solver); a trisection needs "
                             "a folded layout whose gap dimensions this repository "
                             "does not generate, so that one goes to an EM run with "
                             "the matrix in this answer as the target"),
               "sign_convention": ("m13 < 0 places the zero below the band and m13 > 0 "
                                   "above it, for the same magnitudes - the sign of "
                                   "the cross-coupling is the design choice")})
