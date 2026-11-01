"""Skin effect in a ROUND conductor: the exact Bessel solution.

Why this module exists
----------------------
`calc/skin.py` solves the skin effect in a FLAT conductor, and it does so
exactly.  A round wire and, more importantly, a plated via barrel are not flat:
the current has to bend around the axis, and the inner surface of the barrel is
a surface the field cannot cross (there is no net current inside the hole, so
Ampere's law around the hole gives H_phi = 0 there).  Squeezing the flat-sheet
formula onto a via barrel is wrong by tens of percent in the regime that
matters, and `via_resistance` was DC-only in 0.0.66.  This module closes that.

What is solved here - and it is solved, not approximated
--------------------------------------------------------
Cylindrical symmetry, current along z, field varying with r only.  With the
time convention e^{+j*omega*t} and k^2 = -j*omega*mu*sigma, k = (1-j)/delta,

    E'' + (1/r) E' + k^2 E = 0      (Bessel's equation of order 0)

The solution is a combination of J0(k r) and Y0(k r).  The boundary conditions,
not the Bessel functions, decide the geometry:

  * the axis is regular  =>  E' = 0 there automatically (J0' and Y0' vanish at
    r = 0 ... for J0; Y0 is singular at 0 so a solid wire keeps only J0);
  * a HOLE of radius a1 encloses no net current, so H_phi(a1) = 0, and since
    H_phi = -E'/(j*omega*mu) that is E'(a1) = 0.  The hole is not a symmetry
    plane: it is a mirror plane, so the right basis for a tube is
    J0(k r) + c*Y0(k r) with c chosen so that E'(a1) = 0;
  * E is scale free, so set E(a2) = 1 and

        Z = j*omega*mu*E(a2) / (2*pi*a2*E'(a2))     ohm per metre.

A solid wire has a closed form, and it is used as the answer:

    solid wire (a1 = 0, R_dc = rho/(pi*a2^2))
        Z/R_dc = (k*a2/2) * J0(k a2)/J1(k a2)

A tube also has one, but it does NOT survive double precision:

    tube (R_dc = rho/(pi*(a2^2-a1^2)))
        c      = -J1(k a1)/Y1(k a1)
        Z/R_dc = (k*a2/2)*(1-(a1/a2)^2) * (J0(k a2)+c*Y0(k a2))
                                        / (J1(k a2)+c*Y1(k a2))

On the (1-j)x ray J_nu -> i*Y_nu, so c -> -i and both (J0 + c*Y0) and
(J1 + c*Y1) become DIFFERENCES of O(1) numbers whose true value is
e^{-x(1-a1/a2)} smaller.  Past x ~ 15 that difference is gone from the
mantissa, and the tube formula does not raise - it returns the SOLID-WIRE
answer, which at the working point of a real via is wrong by a factor of five.
Measured at x = 40, a1/a2 = 0.9: the closed form says (20.2523, 19.9976) which
is exactly the solid answer x/2 + 1/4, the truth (three independent solvers
agree) is (3.8501, 3.7967), and the single-face high-frequency estimate
x(1-a1/a2)/2 * (1+a1/a2) = 3.8 says the truth is the truth.  A 0.15 mm via
barrel at 1 GHz sits at x ~ 72, i.e. deep inside the broken region, so this is
not an academic complaint: `via_resistance` is DC-only and this module is what
an AC via answer has to come from.

The answer for a tube is therefore NOT the closed form.  It is the log
derivative of E,

    eta(rho) = d lnE/d rho,   rho = r/a2,
    d eta/d rho = A - eta^2 - eta/rho,     A = (k a2)^2 = -2j x^2,
    d psi/d rho = eta,                     psi = lnE,
    Z/R_dc = j x^2 (1 - (a1/a2)^2) / eta(1),

started from eta(a1/a2) = 0 (E'(a1) = 0) and integrated outward with RK4.  There
is no subtraction of two nearly equal numbers anywhere in it, its DC limit is
analytic (eta = j x^2 (rho^2 - ri^2)/rho gives Z/R_dc = 1 exactly), and its
high-frequency limit is the 45-degree surface impedance of a slab.  Its step
count is set by the WALL in skin depths, 16*(x_out - x_in), not by x_out, which
is why a 1 mm barrel at 1 MHz and a 0.15 mm via at 40 GHz both cost milliseconds.

Which one runs: solid -> closed form (it is stable, measured against the Riccati
walk to 5e-15 at x = 400 and against the asymptote to 6e-11 at x = 1e6);
tube -> Riccati walk (the closed form is kept only as an independent reference
below x = 10, where it is still sound).

Six limits pin the formulas from six different directions, and each one is a
test that can fail:

    x -> 0      R/R_dc -> 1 + x^4/48,  X/R_dc -> x^2/4   (x = a2/delta; the
                imaginary part gives the DC internal inductance mu/(8*pi) =
                50.0 nH/m of a round wire, which is an independent constant)
    x -> inf    R/R_dc -> x/2 + 1/4 + 3/(32x),  X/R_dc -> x/2 - 3/(32x)
                (surface impedance at 45 degrees on the OUTER surface only)
    a1 -> 0     the tube reproduces the solid wire
    a1 -> a2    the tube reproduces calc.skin's FLAT sheet with faces="one"
                (an annulus with a vanishing wall is locally a slab whose far
                 face is the H = 0 surface of the hole).  This is a cross-check
                 against a different module, so it cannot share a bug with the
                 Bessel code.
plus two solvers that share nothing with them: a radial complex
finite-difference solve of the same ODE, and (for a tube at low x) the closed
form itself.  Both are run as `checks` and both can fail.

Which Bessel implementation, and why it is not a library
--------------------------------------------------------
Zero runtime dependencies means no scipy, so J0/J1/Y0/Y1 are computed here:

    |w| <  20   ascending power series (J: the alternating factorial series;
                Y: the log+psi series of DLMF 10.8.1).  Measured agreement with
                a 400-term evaluation to the last bit; the series is exact
                until the truncated tail starts to cancel, which happens near
                |w| = 30 (1e-11 there, 1e-15 below |w| = 25).
    |w| >= 20   the Debye/Poincare asymptotic series (DLMF 10.17.3/10.17.4),
                truncated at its smallest term.  Its error is 1e-15 for
                |w| >= 20 on the (1-j)x ray.

The two branches overlap on |w| in [20, 28] and agree there to ~1e-12, which is
one of the checks.  The asymptotic branch canNOT use cmath.cos/cmath.sin
directly: on the (1-j)x ray the argument has |Im| = x, and cmath.cos raises
OverflowError at x >~ 710 (measured).  A 1 mm wire hits x = 700 at 16 MHz-ish
in a thin plating and a 0.15 mm via hits it at ~ 40 GHz, so this is reachable.
The fix is to factor the common real factor exp(|Im om|) out of both cos and
sin: every quantity below is then used only as a RATIO at one argument, and the
factor cancels exactly.  The return values of the private helpers are therefore
NOT Bessel functions - they are Bessel functions times a common real scale.

Two arithmetic mistakes were made while deriving this and are kept as a warning
about what the checks have to catch:

  * Y1 came out wrong by exactly (2*gamma/pi)*J1 = 0.0890 at w = 0.5, because
    gamma is counted twice if you use A&S's (ln(z/2)+gamma) form AND the psi
    form of the series.  DLMF 10.8.1 uses the plain log, and that is what is
    implemented.
  * the asymptotic sign (-1)^k belongs to the ORDER j of a_{2j}, not to the
    coefficient index k of a_k.  Indexing it by k leaves an O(1/|w|) error that
    does not shrink with |w|: measured 4.4e-4 at x = 400 instead of 1e-16.

What this is NOT
----------------
  * no proximity effect: a neighbouring wire or the return path changes the
    field, and this is an isolated conductor;
  * no lateral/azimuthal variation: a wire bonded into a pad, or a via that is
    not a full cylinder, is out of scope;
  * no plating interfaces: one rho for the whole wall (a via with a different
    barrier layer is not modelled);
  * no pad, antipad, or 3-D spreading at either end of a via: the answer is a
    per-unit-length impedance of an infinite cylinder;
  * no temperature rise: rho is the value at temp_c and stays there.
"""
import cmath
import math

from .result import ok, err
from .roughness import MU0, roughness_factor

TWO_PI = 2.0 * math.pi
EULER = 0.577215664901532860606512090082402431

# |w| where the ascending series hands over to the asymptotic expansion.  Both
# are accurate to ~1e-15 on their own side (measured against a 400-term series
# and, when the author had scipy at hand, against scipy.special.jv/yv).
_W_SWITCH = 20.0
# Below this x the ratio is 1 to within 1e-32; the walk is not worth starting.
_X_SERIES_SAFE = 1e-8
# Finite-difference reference solve: O(h^2), so it is only meaningful while
# x_out stays moderate.  Measured at 24*x points: 1.3e-6 at x=1, 3.2e-4 at
# x=20, 3.1e-5 at x=72 (n=1728), 3.1e-5 at x=200 (n=4800).  Past _FD_X_MAX even
# _FD_N_MAX points is not enough, and the check says "skipped" rather than
# reporting a number that means nothing.
_FD_X_MAX = 200.0
_FD_N_MAX = 12001


# --------------------------------------------------------------- Bessel ----
def _j01_series(w):
    """J0, J1 by the ascending series (alternating factorial terms)."""
    h = 0.5 * w
    h2 = h * h
    j0 = 1.0 + 0j
    j1 = h
    t0 = 1.0 + 0j
    t1 = h
    for k in range(1, 400):
        t0 = -t0 * h2 / (k * k)
        j0 += t0
        t1 = -t1 * h2 / (k * (k + 1))
        j1 += t1
        if abs(t0) < 1e-20 * abs(j0) and abs(t1) < 1e-20 * abs(j1):
            break
    return j0, j1


def _y01_series(w):
    """Y0, Y1 by DLMF 10.8.1 (plain log) + the psi series.

    Do NOT put gamma in the log as well: psi(k+1)+psi(k+2) already contributes
    +2*gamma, and double counting it shifts the answer by (2*gamma/pi)*J1,
    which is 0.0890 at w = 0.5 - an error no ordinary tolerance would notice.
    """
    j0, j1 = _j01_series(w)
    h = 0.5 * w
    h2 = h * h
    lnz = cmath.log(h)                    # ln(w/2), NO gamma

    s0 = 0j
    term = 1.0 + 0j                       # h^(2k)/(k!)^2, sign (-1)^(k+1)
    hk = 0.0
    for k in range(1, 400):
        term = -term * h2 / (k * k)
        hk += 1.0 / k
        part = -term * hk
        s0 += part
        if abs(part) < 1e-20 * max(abs(s0), 1e-300):
            break
    y0 = (2.0 / math.pi) * ((lnz + EULER) * j0 + s0)

    s1 = 0j
    term = h                              # h^(2k+1)/(k!(k+1)!), k = 0
    hk = 0.0
    hk1 = 1.0
    for k in range(0, 400):
        if k > 0:
            term = -term * h2 / (k * (k + 1))
            hk += 1.0 / k
            hk1 = hk + 1.0 / (k + 1)
        part = term * (hk + hk1 - 2.0 * EULER)
        s1 += part
        if abs(part) < 1e-20 * max(abs(s1), 1e-300):
            break
    y1 = (2.0 / math.pi) * (lnz * j1 - 1.0 / w) - s1 / math.pi
    return y0, y1


def _a_coeffs(nu, kmax):
    """a_k(nu) = prod_{j=1..k}(4nu^2-(2j-1)^2) / (k! 8^k)."""
    a = [1.0]
    mu = 4.0 * nu * nu
    for k in range(1, kmax + 1):
        a.append(a[-1] * (mu - (2 * k - 1) ** 2) / (8.0 * k))
    return a


def _scl_exp(theta, im):
    """exp(1j*theta) * exp(-im), with the exponent clamped into double range."""
    g = -theta.imag - im                  # <= 0 by construction
    if g < -745.0:
        return 0j
    return cmath.exp(complex(g, theta.real))


def _jy_asym_scaled(w, nu, kmax=40):
    """(J_nu, Y_nu) * exp(-|Im om|), DLMF 10.17.3/10.17.4.

    Both are multiplied by the SAME positive real number, so any ratio at one
    argument is exact and no exp() of |Im w| is ever formed.  See the module
    docstring: the factor is what makes x = 1000 possible at all.
    """
    a = _a_coeffs(nu, kmax)
    s = cmath.sqrt(2.0 / (math.pi * w))
    om = w - (0.5 * nu + 0.25) * math.pi
    im = abs(om.imag)
    cp = _scl_exp(om, im)                 # exp(+i om) * exp(-im)
    cm = _scl_exp(-om, im)                # exp(-i om) * exp(-im)
    co = 0.5 * (cp + cm)                  # cos(om) * exp(-im)
    si = -0.5j * (cp - cm)                # sin(om) * exp(-im)
    z = 1.0 / w
    ev = 0j
    od = 0j
    zk = 1.0 + 0j
    last = float('inf')
    for j in range(0, (kmax // 2) + 1):
        sg = 1.0 if j % 2 == 0 else -1.0
        te = sg * a[2 * j] * zk if 2 * j <= kmax else 0j
        to = sg * a[2 * j + 1] * zk * z if 2 * j + 1 <= kmax else 0j
        nxt = abs(te) + abs(to)
        if j > 0 and nxt > last:          # divergent series: stop at the smallest term
            break
        last = nxt
        ev += te
        od += to
        zk = zk * z * z
    return s * (co * ev - si * od), s * (si * ev + co * od)


def _jy(w, nu):
    """(J_nu, Y_nu) for one argument, as a pair with a COMMON real scale.

    Below |w| = 20 the scale is 1 and the values are the Bessel functions
    themselves; above, they are Bessel functions times exp(-|Im om|).  Never
    compare values from two different arguments without carrying the scale.
    """
    if abs(w) < _W_SWITCH:
        j0, j1 = _j01_series(w)
        y0, y1 = _y01_series(w)
        return (j0, y0) if nu == 0 else (j1, y1)
    return _jy_asym_scaled(w, nu)


def _scale_log(w, nu=0):
    """ln of the common real scale that _jy divided out (0 in the series branch)."""
    if abs(w) < _W_SWITCH:
        return 0.0
    om = w - (0.5 * nu + 0.25) * math.pi  # only |Im om| matters, and it is |Im w|
    return abs(om.imag)


# ------------------------------------------------------------ impedance ----
def _solid_ratio(x):
    """Z/R_dc for a solid round wire, x = a/delta.  THE ANSWER PATH.

    Only J0 and J1 at one argument are involved and both carry the same real
    scale, so there is no cancellation: measured against the Riccati walk to
    5e-15 at x = 400 and against the asymptote to 6e-11 at x = 1e6.  This is
    why the solid case does not need the walk (which would cost 16*x steps).
    """
    if x <= _X_SERIES_SAFE:
        return complex(1.0, 0.0)
    w = complex(1.0, -1.0) * x
    j0, _ = _jy(w, 0)
    j1, _ = _jy(w, 1)      # _jy returns (J_nu, Y_nu)
    if j1 == 0:
        return complex(1.0, 0.0)
    return 0.5 * w * j0 / j1


def _tube_ratio(x_out, x_in):
    """Z/R_dc for an annulus a1..a2, R_dc on the metal section.  REFERENCE ONLY.

    Sound for x_out <= 10 (agrees with the Riccati walk to 1e-10 there) and
    progressively wrong above that, because c -> -i turns both Bessel
    combinations into a cancellation of two O(1) numbers.  At x_out = 40 with
    a1/a2 = 0.9 it returns the solid-wire answer (20.2523 instead of 3.8501).
    Never call it for the answer - see _eta_walk.
    """
    if x_in <= 0.0:
        return _solid_ratio(x_out)
    if x_out <= _X_SERIES_SAFE:
        return complex(1.0, 0.0)
    wo = complex(1.0, -1.0) * x_out
    wi = complex(1.0, -1.0) * x_in
    j0o, y0o = _jy(wo, 0)
    j1o, y1o = _jy(wo, 1)
    j0i, y0i = _jy(wi, 0)
    j1i, y1i = _jy(wi, 1)
    # E'(a1) = 0  with  J0' = -J1, Y0' = -Y1  =>  J1 + c*Y1 = 0  =>  c = -J1/Y1.
    # Cross-multiplying by Y1(wi) keeps the quotient well conditioned even where
    # |c| is large; both numerator and denominator stay O(1) products.
    num = y1i * j0o - j1i * y0o
    den = y1i * j1o - j1i * y1o
    if den == 0:
        return _solid_ratio(x_out)
    return 0.5 * wo * (1.0 - (x_in / x_out) ** 2) * num / den


# ------------------------------------------------------- Riccati solver ----
def _eta_walk(x_out, x_in, n, keep=False):
    """Integrate (eta, psi) outward from the bore to the outer surface.

    eta = dlnE/drho and psi = lnE obey, from Bessel's equation with rho = r/a2,

        d eta/d rho = A - eta^2 - eta/rho ,   A = (k a2)^2 = -2j x^2
        d psi/d rho = eta

    Starts: bore eta(ri) = 0 (E'(a1) = 0, no net current inside the hole);
    solid rho0 = 1e-6 with eta = j x^2 rho0, the small-rho expansion that
    regularity at the axis gives.  Both are the exact local behaviour, not a
    fudge: at rho -> 0 the ODE reduces to eta' = eta/rho, whose regular solution
    is eta = j x^2 rho.

    Returns (eta(1), psi(1), nodes, rho_start) with nodes = [(rho, eta, psi)]
    when keep is true.  psi(1) is only used by the profile, where it is the
    reference level.

    Step count: the -eta^2 term relaxes at rate 2|eta| ~ 2*x_out, so RK4 wants
    h ~ 1/(16 x_out); the march only spans (1 - ri) = the wall in skin depths,
    so the count is 16*(x_out - x_in) and NOT 16*x_out.  That is what makes a
    1 mm barrel at 1 MHz and a 0.15 mm via at 40 GHz both cheap.
    """
    a = complex(0.0, 2.0 * x_out * x_out)          # A = (k a2)^2 = -2j x^2
    if x_in <= 0.0:
        rho = 1e-6
        eta = complex(0.0, x_out * x_out) * rho
        x_in = 0.0
    else:
        rho = x_in / x_out
        eta = 0j
    n = int(n)
    if n < 40:
        n = 40
    h = (1.0 - rho) / n
    psi = 0j
    nodes = [(rho, eta, psi)] if keep else None
    for _ in range(n):
        # RK4 on the pair (eta, psi); psi' = eta, so the four psi slopes ARE
        # the four eta stage values - no extra function evaluation.
        k1 = a - eta * eta - eta / rho
        hm = rho + 0.5 * h
        p1 = eta
        e = eta + 0.5 * h * k1
        p2 = e
        k2 = a - e * e - e / hm
        e = eta + 0.5 * h * k2
        p3 = e
        k3 = a - e * e - e / hm
        hf = rho + h
        e = eta + h * k3
        p4 = e
        k4 = a - e * e - e / hf
        eta += h * (k1 + 2.0 * k2 + 2.0 * k3 + k4) / 6.0
        psi += h * (p1 + 2.0 * p2 + 2.0 * p3 + p4) / 6.0
        rho = hf
        if keep:
            nodes.append((rho, eta, psi))
    return eta, psi, nodes, (x_in / x_out if x_in > 0.0 else 1e-6)

def riccati_ratio(x_out, x_in=0.0, n=None, richardson=True):
    """(Z/R_dc, convergence estimate, steps) for an annulus, from _eta_walk."""
    if x_out <= _X_SERIES_SAFE:
        return complex(1.0, 0.0), 0.0, 0
    x_in = max(0.0, float(x_in))
    if n is None:
        n = max(200, int(16.0 * (x_out - x_in)) + 200)
    n = max(40, int(n))
    eta1, _, _, _ = _eta_walk(x_out, x_in, n)
    if eta1 == 0:
        return complex(1.0, 0.0), 0.0, n
    k = 1j * x_out * x_out * (1.0 - (x_in / x_out) ** 2)
    z1 = k / eta1
    if not richardson:
        return z1, 0.0, n
    eta2, _, _, _ = _eta_walk(x_out, x_in, 2 * n)
    if eta2 == 0:
        return z1, 0.0, n
    z2 = k / eta2
    # RK4 is O(h^4), so halving h cuts the error 16x: the remaining error is
    # (z2 - z1)/15, and z2 + that is the 4th-order-accurate value.
    conv = abs(z2 - z1) / abs(z2) if z2 else 0.0
    return z2 + (z2 - z1) / 15.0, conv, 2 * n

# --------------------------------------------------- independent solver ----
def fd_annulus(x_out, x_in=0.0, n=801):
    """Radial complex finite-difference solve of the same ODE; Z/R_dc.

    Shares nothing with the Bessel code: no series, no asymptotics, just a
    tridiagonal system for E on n+1 radii with the ghost row E'(-0) = 0 (or
    E'(a1) = 0) and E(a2) = 1, then a one-sided O(h^2) derivative.  Its error is
    O(h^2) * (k a2)^4-ish, so it is used only to confirm the closed form to a
    few 1e-5 - it is not the answer and never will be, because at x = 100 it
    would need n >> 1e5 to reach 1e-9.
    """
    n = int(n)
    if n < 40:
        n = 40
    ro = 1.0
    ri = (x_in / x_out) if x_out > 0.0 else 0.0
    lam = -2j * x_out * x_out / (ro * ro)     # k^2 a2^2 with k^2 = -2j x^2/a^2
    nz = n + 1
    h = (ro - ri) / n
    r = [ri + i * h for i in range(nz)]
    A = [0j] * nz
    B = [0j] * nz
    C = [0j] * nz
    D = [0j] * nz
    for i in range(nz - 1):
        if i == 0:
            A[i] = 0j                          # ghost: E(-h) = E(0)
            B[i] = -2.0 / h ** 2 + lam
            C[i] = 2.0 / h ** 2
        else:
            A[i] = 1.0 / h ** 2 - 1.0 / (2.0 * h * r[i])
            B[i] = -2.0 / h ** 2 + lam
            C[i] = 1.0 / h ** 2 + 1.0 / (2.0 * h * r[i])
    D[nz - 2] = -(1.0 / h ** 2 + 1.0 / (2.0 * h * r[nz - 2]))   # E(a2) = 1
    for i in range(1, nz - 1):
        m = A[i] / B[i - 1]
        B[i] -= m * C[i - 1]
        D[i] -= m * D[i - 1]
    E = [0j] * nz
    E[nz - 1] = 1.0 + 0j
    E[nz - 2] = D[nz - 2] / B[nz - 2]
    for i in range(nz - 3, -1, -1):
        E[i] = (D[i] - C[i] * E[i + 1]) / B[i]
    der = (3.0 * E[nz - 1] - 4.0 * E[nz - 2] + E[nz - 3]) / (2.0 * h)
    if der == 0:
        return complex(1.0, 0.0)
    wmu_over_rho = 2.0 * x_out * x_out          # = omega*mu*sigma in a2 = 1 units
    return 1j * wmu_over_rho * (ro * ro - ri * ri) / (2.0 * ro * der)


# --------------------------------------------------------------- profile ----
def _tube_c(x_in):
    """c = -J1(k a1)/Y1(k a1), the coefficient that enforces E'(a1) = 0.

    The two values come from the same argument, so the common scale of _jy
    cancels and c is exact in either branch.
    """
    if x_in <= 0.0:
        return 0.0
    wi = complex(1.0, -1.0) * x_in
    j1i, y1i = _jy(wi, 1)
    if y1i == 0:
        return 0.0
    return -j1i / y1i


def _hermite(t, p0, m0, p1, m1):
    """Cubic Hermite on t in [0,1] given both end values and both end slopes."""
    t2 = t * t
    t3 = t2 * t
    return ((2.0 * t3 - 3.0 * t2 + 1.0) * p0
            + (t3 - 2.0 * t2 + t) * m0
            + (-2.0 * t3 + 3.0 * t2) * p1
            + (t3 - t2) * m1)

def radial_profile(x_out, x_in, samples=9):
    """|J| and phase versus radius from the Riccati walk; outer surface = 1.

    This does NOT use the Bessel closed form.  For a tube the closed form is
    dead above x ~ 15 (see _eta_walk), and the profile it returns at a real via
    working point is 1.0 everywhere with zero phase - a plausible-looking plot
    of a completely wrong answer.  The walk carries psi = lnE, so
    |J(r)|/|J(a2)| = exp(Re psi(r) - Re psi(a2)) with no cross-argument
    comparison and no scale to recover.

    psi is known at the march nodes only, so it is interpolated with a cubic
    Hermite that uses the exact derivative (psi' = eta) at both ends: error
    O(h^4), measured at 1e-8 against the closed form where that one is valid.

    phase_deg is wrapped to (-180, 180] - what a phasor plot shows.
    phase_lag_deg is the accumulated lag and keeps growing inward; across a
    wire many skin depths wide it is thousands of degrees, and that is real.
    """
    n = int(samples)
    if n < 2:
        n = 2
    steps = max(200, int(16.0 * max(0.0, x_out - x_in)) + 200)
    _, _, nodes, rho0 = _eta_walk(x_out, max(0.0, x_in), steps, keep=True)
    h = (1.0 - rho0) / steps
    rows = []
    for i in range(n):
        frac = i / float(n - 1)
        rho = rho0 + (1.0 - rho0) * frac
        f = (rho - rho0) / h
        k = int(f)
        if k > steps - 1:
            k = steps - 1
        if k < 0:
            k = 0
        t = f - k
        a = nodes[k]
        b = nodes[k + 1]
        rows.append((frac, _hermite(t, a[2], a[1] * h, b[2], b[1] * h)))
    ref = rows[-1][1]
    out = []
    for frac, p in rows:
        d = p - ref
        lag = math.degrees(d.imag)
        ph = lag % 360.0
        if ph > 180.0:
            ph -= 360.0
        out.append({
            "r_over_outer": frac,
            "j_rel": math.exp(d.real) if d.real > -745.0 else 0.0,
            "phase_deg": ph,
            "phase_lag_deg": lag,
        })
    return out

def radial_profile_bessel(x_out, x_in, samples=9):
    """REFERENCE ONLY (sound below x ~ 10): the profile from the closed form.

    At a real via working point (x ~ 70) it is not merely inaccurate, it is
    vacuous: c -> -i makes (J0 + c Y0) a difference of two equal O(1) numbers,
    so it reports j_rel = 1.0 and phase 0 at every radius (measured at x = 72,
    a1/a2 = 0.845).  Used as the cross-check for radial_profile at low x.

    |J| and phase versus radius, normalised to the OUTER surface.

    The magnitude has to cross two arguments, so the real scale that _jy
    factored out is carried explicitly in the log domain:
    |J(r)|/|J(a2)| = exp(logmag(r) - logmag(a2)).  The phase needs no such care
    because the factor is real.  Values below 1e-300 report as 0.0, which is
    what a deep-skin-depth interior really is.
    """
    n = int(samples)
    c = _tube_c(x_in)
    rows = []
    for i in range(n):
        frac = (i / float(n - 1)) if n > 1 else 1.0
        xr = x_in + (x_out - x_in) * frac
        if xr <= 1e-12:
            xr = 1e-12
        w = complex(1.0, -1.0) * xr
        j0, y0 = _jy(w, 0)
        e = (j0 + c * y0) if x_in > 0.0 else j0
        mag = abs(e)
        lg = (math.log(mag) if mag > 0.0 else -1e300) + _scale_log(w, 0)
        rows.append((frac, lg, cmath.phase(e)))
    ref = rows[-1]
    out = []
    for frac, lg, arg in rows:
        d = lg - ref[1]
        rel = math.exp(d) if d > -745.0 else 0.0
        out.append({
            "r_over_outer": frac,
            "j_rel": rel,
            "phase_deg": math.degrees(arg - ref[2]),
        })
    return out


# ------------------------------------------------------------------ tool ----
def ac_round_conductor(frequency_hz, diameter_mm=None, wall_um=None,
                       drill_mm=None, plating_um=None, rho_ohm_mm=None,
                       temp_c=20.0, mu_r=1.0, length_mm=None,
                       roughness_rq_um=None, profile_samples=9,
                       fd_points=801, fd_verify=True):
    """R(f) and X(f) of a round wire or a plated via barrel, with the geometry
    and the regime named in the answer.

    Geometry, exactly one of:
        diameter_mm                    solid wire of that diameter
        diameter_mm + wall_um          tube: outer diameter and wall thickness
        drill_mm + plating_um          via: drilled hole and plating thickness
                                       (outer diameter = drill + 2*plating)
    """
    from .power import _rho as _rho_at

    if frequency_hz is None:
        return err("frequency_hz is required: a skin depth without a frequency is "
                   "just a length", "ARG_REQUIRED")
    try:
        f = float(frequency_hz)
    except (TypeError, ValueError):
        return err("frequency_hz must be a number", "BAD_PARAMETER")
    if not (f > 0.0):
        return err("frequency_hz must be > 0 (the DC answer is via_resistance or "
                   "copper_resistance; both have no skin term at all)",
                   "BAD_PARAMETER")

    # ---- geometry
    if diameter_mm is None and drill_mm is None:
        return err("give a geometry: diameter_mm (a wire), diameter_mm+wall_um "
                   "(a tube), or drill_mm+plating_um (a via barrel)",
                   "ARG_REQUIRED",
                   hint="the bore is never guessed: for a via the drill and the "
                        "plating are separate numbers on the fabrication drawing, "
                        "and the wall is what carries the current")
    if diameter_mm is not None and drill_mm is not None:
        return err("give either diameter_mm or drill_mm, not both - they are two "
                   "descriptions of the same outer diameter and the second would "
                   "silently win", "ARG_INVALID")

    wall = None
    if drill_mm is not None:
        if plating_um is None:
            return err("drill_mm needs plating_um: a drilled hole with no plating "
                       "carries no current", "ARG_REQUIRED")
        try:
            bore = float(drill_mm)
            wall = float(plating_um) / 1e3
        except (TypeError, ValueError):
            return err("drill_mm and plating_um must be numbers", "BAD_PARAMETER")
        if not (bore > 0.0):
            return err("drill_mm must be > 0", "BAD_PARAMETER")
        if not (wall > 0.0):
            return err("plating_um must be > 0", "BAD_PARAMETER")
        a2 = bore / 2.0 + wall
        a1 = bore / 2.0
    else:
        try:
            d = float(diameter_mm)
        except (TypeError, ValueError):
            return err("diameter_mm must be a number", "BAD_PARAMETER")
        if not (d > 0.0):
            return err("diameter_mm must be > 0", "BAD_PARAMETER")
        a2 = d / 2.0
        if wall_um is None:
            a1 = 0.0
        else:
            try:
                wall = float(wall_um) / 1e3
            except (TypeError, ValueError):
                return err("wall_um must be a number", "BAD_PARAMETER")
            if not (wall > 0.0):
                return err("wall_um must be > 0 (omit it for a solid wire)",
                           "BAD_PARAMETER")
            if wall >= a2:
                return err("wall_um is larger than the radius: that is not a "
                           "tube, it is a solid wire with a negative bore",
                           "BAD_PARAMETER")
            a1 = a2 - wall


    # ---- material
    if rho_ohm_mm is None:
        rho = _rho_at(temp_c if temp_c is not None else 20.0) * 1e3
    else:
        try:
            rho = float(rho_ohm_mm)
        except (TypeError, ValueError):
            return err("rho_ohm_mm must be a number", "BAD_PARAMETER")
    if not (rho > 0.0):
        return err("rho_ohm_mm must be > 0", "BAD_PARAMETER")

    try:
        mur = float(mu_r) if mu_r is not None else 1.0
    except (TypeError, ValueError):
        return err("mu_r must be a number", "BAD_PARAMETER")
    if not (mur >= 1.0):
        return err("mu_r must be >= 1 (copper is 1.0)", "BAD_PARAMETER")

    n_prof = int(profile_samples) if profile_samples is not None else 9
    if n_prof < 2:
        return err("profile_samples must be >= 2", "BAD_PARAMETER")

    length = None
    if length_mm is not None:
        try:
            length = float(length_mm)
        except (TypeError, ValueError):
            return err("length_mm must be a number", "BAD_PARAMETER")
        if not (length > 0.0):
            return err("length_mm must be > 0", "BAD_PARAMETER")

    delta_m = math.sqrt(rho * 1e-3 / (math.pi * f * MU0 * mur))
    delta_mm = delta_m * 1e3
    x_out = a2 / delta_mm
    x_in = a1 / delta_mm
    hollow = a1 > 0.0

    # The answer path.  A solid wire keeps the closed form: only J0/J1 at one
    # argument appear, there is no cancellation, and it is measured against the
    # Riccati walk to 5e-15 (x = 400) and the asymptote to 6e-11 (x = 1e6), so
    # spending 16*x steps on a walk would buy nothing.  A tube MUST walk: its
    # closed form cancels to the solid answer above x ~ 15 (see _eta_walk).
    if hollow:
        zr, rk_conv, rk_steps = riccati_ratio(x_out, x_in)
    else:
        zr = _solid_ratio(x_out)
        rk_conv, rk_steps = None, 0
    r_ratio = zr.real
    x_ratio = zr.imag

    area = math.pi * (a2 * a2 - a1 * a1)          # mm^2
    r_dc_per_m = rho * 1000.0 / area               # ohm per metre
    r_ac_per_m = r_dc_per_m * r_ratio
    x_ac_per_m = r_dc_per_m * x_ratio
    l_int_nh_per_m = (x_ac_per_m / (TWO_PI * f)) * 1e9 if f else None

    r_surf = rho / delta_mm                        # ohm per square (bulk Rs)

    # ---- regime
    x_wall = (a2 - a1) / delta_mm
    if x_out <= 0.5:
        regime = "dc (R within 0.2 %% of R_dc)"
    elif hollow and x_wall <= 1.0:
        # x_out is not the criterion here: the WALL is.  A barrel whose plating
        # is thinner than one skin depth carries current in all of the copper it
        # has, and R is then R_dc of that copper, not a surface impedance -
        # whatever x_out says about the outer surface.
        regime = ("wall-limited (the %.3g um wall is %.2f skin depths thick, so "
                  "the current fills it: R is set by how much copper the plating "
                  "put there, not by skin depth)"
                  % ((a2 - a1) * 1e3, x_wall))
    elif x_out < 3.0:
        regime = "transition (R between 1x and 3x R_dc)"
    else:
        regime = ("skin-dominated (current in a shell %.3g um thick at the outer "
                  "surface)" % (delta_mm * 1e3))

    # ---- milestone frequencies.
    # R/R_dc depends on the geometry only through (x_in, x_out) = s*(ri,1)/(1-ri)
    # with s = x_out - x_in the wall in skin depths, so bisect on s and convert.
    # Bisecting on f would walk x up to 1e6 while looking for a bracket, and the
    # walk costs 16*x_wall steps - that is the difference between 1 ms and a
    # timeout.  On s the targets (1.01, 1.10, 2.0) are all reached below s = 5.
    sigma = 1.0 / (rho * 1e-3)
    f_per_x2 = 1.0 / (MU0 * mur * sigma * (a2 * 1e-3) ** 2 * math.pi)
    span = 1.0 - a1 / a2

    def _ratio_s(ss):
        xo = ss / span
        if xo <= _X_SERIES_SAFE:
            return 1.0
        if hollow:
            return riccati_ratio(xo, xo - ss, richardson=False)[0].real
        return _solid_ratio(xo).real

    def _bisect(target):
        lo, hi = 1e-9, 200.0
        if _ratio_s(hi) < target or _ratio_s(lo) > target:
            return None
        for _ in range(90):
            mid = math.sqrt(lo * hi)
            if _ratio_s(mid) < target:
                lo = mid
            else:
                hi = mid
            if hi / lo < 1.0 + 1e-9:
                break
        return (hi / span) ** 2 * f_per_x2

    f_1pct = _bisect(1.01)
    f_10pct = _bisect(1.10)
    f_2x = _bisect(2.0)

    # ---- checks that can fail
    checks = {}
    # 1) DC series in x: R = 1 + x^4/48, X = x^2/4 (a round wire, NOT the 4/45
    #    of a flat sheet - if the two are confused this fails immediately)
    xs = 0.05
    zs = _solid_ratio(xs)
    checks["dc_series_x"] = xs
    checks["dc_series_r_rel_error"] = abs(zs.real - (1.0 + xs ** 4 / 48.0))
    checks["dc_series_x_rel_error"] = abs(zs.imag - xs * xs / 4.0)
    # 2) DC internal inductance must be mu/(8*pi) = 50.0 nH/m of a round wire,
    #    independent of the diameter.  X/R_dc = x^2/4, so L = X/omega - and
    #    omega is NOT the caller's frequency: x itself fixes it through
    #    x^2 = a2^2 omega mu sigma / 2.  Dividing by the caller's f mixes two
    #    different experiments and returns 0.0018 nH/m instead of 50.
    #    x = 3e-3 is chosen for THIS anchor: the first neglected term of the
    #    series is then 3e-12 (measured), while at x = 1e-3 the imaginary part
    #    is 2.5e-7 and the double round-off floor takes over (4e-10 measured).
    xs_l = 3e-3
    zs_l = _solid_ratio(xs_l)
    omega_x = 2.0 * xs_l * xs_l / ((a2 * 1e-3) ** 2 * MU0 * mur * sigma)
    l_dc = (zs_l.imag * (rho * 1000.0 / (math.pi * a2 * a2)) / omega_x) * 1e9
    checks["dc_internal_inductance_nh_per_m"] = l_dc
    checks["dc_internal_inductance_expected"] = 50.0
    checks["dc_internal_inductance_rel_error"] = abs(l_dc / 50.0 - 1.0)
    # 3) high-frequency asymptote of a SOLID wire: R = x/2 + 1/4 + 3/(32x)
    xa = 40.0
    za = _solid_ratio(xa)
    pred_r = xa / 2.0 + 0.25 + 3.0 / (32.0 * xa)
    checks["asymptote_x"] = xa
    checks["asymptote_r_rel_error"] = abs(za.real / pred_r - 1.0)
    checks["asymptote_45deg_rel_error"] = abs(za.imag / (xa / 2.0) - 1.0)
    # 4) the two Bessel branches must agree where they overlap
    w_ov = complex(1.0, -1.0) * (_W_SWITCH / math.sqrt(2.0) * 1.2)
    j_s, y_s = _j01_series(w_ov)[0], _y01_series(w_ov)[0]
    j_a, y_a = _jy_asym_scaled(w_ov, 0)
    sc = math.exp(abs((w_ov - 0.25 * math.pi).imag))
    checks["branch_overlap_w"] = abs(w_ov)
    checks["branch_overlap_j_rel"] = abs(j_a * sc - j_s) / abs(j_s)
    checks["branch_overlap_y_rel"] = abs(y_a * sc - y_s) / abs(y_s)
    # 5) the ANSWER PATH re-derived a second way at the caller's own x.
    #    For a tube that is the closed form - a genuinely independent derivation,
    #    but only while it is still sound; above x = 10 it cancels to the solid
    #    answer, so it is not run and the report says exactly that instead of
    #    printing a number that would look like agreement.
    checks["riccati_steps"] = rk_steps
    checks["riccati_convergence_rel"] = rk_conv
    if hollow:
        if x_out <= 10.0:
            checks["closed_form_x"] = x_out
            checks["closed_form_vs_riccati_rel"] = (
                abs(_tube_ratio(x_out, x_in) - zr) / abs(zr))
        else:
            checks["closed_form_skipped"] = (
                "x_out = %.3g > 10: the tube closed form cancels itself (at "
                "x=40, a1/a2=0.9 it returns the SOLID answer 20.2523 where the "
                "truth is 3.8501), so it is not evaluated here" % x_out)
    elif x_out <= 500.0:
        zrr, rc, _ = riccati_ratio(x_out, 0.0)
        checks["riccati_vs_closed_rel"] = abs(zrr - zr) / abs(zr)
        checks["riccati_vs_closed_conv"] = rc
        checks["riccati_steps"] = max(rk_steps, 2 * max(200, int(16.0 * x_out) + 200))
        checks["riccati_convergence_rel"] = rc
    else:
        checks["riccati_vs_closed_skipped"] = (
            "x_out = %.3g > 500: the walk would need 16*x steps. The closed form "
            "is stable here (6e-11 against the asymptote at x = 1e6), so the "
            "asymptote check stands in for it." % x_out)
    # 6) an independent solver for the SAME ODE, at the caller's own x
    checks["fd_verify"] = bool(fd_verify)
    if fd_verify:
        if x_out > _FD_X_MAX:
            checks["fd_verify"] = "skipped"
            checks["fd_skipped"] = (
                "x_out = %.3g > %g: a second-order radial solve needs n >> x, "
                "which is not worth the time here. The Riccati convergence "
                "estimate and the closed form / asymptote carry this point."
                % (x_out, _FD_X_MAX))
        else:
            nf = int(fd_points)
            if nf < 201:
                nf = 201
            nf = min(max(nf, int(24.0 * x_out)), _FD_N_MAX)
            zfd = fd_annulus(x_out, x_in, nf)
            checks["fd_points"] = nf
            checks["fd_vs_answer_rel_error"] = abs(zfd - zr) / abs(zr) if zr else None
            zfd2 = fd_annulus(x_out, x_in, 2 * nf)
            checks["fd_self_convergence_rel"] = (
                abs(zfd2 - zfd) / abs(zfd2) if zfd2 else None)
            checks["fd_converged"] = bool(
                checks["fd_self_convergence_rel"] is not None
                and checks["fd_self_convergence_rel"] < 2e-3
                and (checks["fd_vs_answer_rel_error"] or 0.0) < 5e-3)
    # 7) a tube whose bore is numerical zero has to BE the solid wire.
    #    Compare PHYSICAL impedances: the two RATIOS differ by the area factor
    #    1-(a1/a2)^2 by construction (exactly 1e-6 at a1/a2 = 1e-3), and leaving
    #    that in would look like a solver error that is not there.
    if hollow and (a1 / a2) < 1e-3:
        zt = zr / (1.0 - (a1 / a2) ** 2)
        checks["bore_to_zero_rel"] = abs(zt - _solid_ratio(x_out)) / abs(zt)
    # 7) a thick-wall tube has to BE the flat sheet of the same thickness, one
    #    face (the hole is an H = 0 surface, so the wall sees exactly one face) -
    #    up to ONE geometric factor that is not an error: the sheet normalises by
    #    the wall area 2*pi*a_mean*t while a cylinder's AC current leaves through
    #    the OUTER circumference 2*pi*a2.  So R_ac/R_dc(tube) = R_ac/R_dc(sheet)
    #    * (a1+a2)/(2*a2) and this check divides that factor back out before
    #    comparing.  Leaving it in would report an 11.6 % "discrepancy" on a 25 um
    #    plating that is exactly physics.  Different module, different formula:
    #    this cannot share a bug with the Bessel code.
    if hollow and (a1 / a2) >= 0.5:
        from .skin import ac_sheet_resistance as _sheet
        sh = _sheet(f, thickness_mm=(a2 - a1), rho_ohm_mm=rho, mu_r=mur,
                    faces="one")
        if sh.get("status") == "ok":
            sv = sh["value"]
            geom = (a1 + a2) / (2.0 * a2)
            checks["thin_wall_vs_flat_sheet"] = {
                "wall_over_skin": x_wall,
                "geom_factor": geom,
                "r_rel_error": abs(r_ratio / (sv["r_ac_over_r_dc"] * geom) - 1.0),
                "x_rel_error": abs(x_ratio / (sv["x_ac_over_r_dc"] * geom) - 1.0)
                if sv["x_ac_over_r_dc"] else None,
                "note": "the residual is the O(1/x) surface term, which a slab "
                        "and a curved wall do not share; it goes to zero as the "
                        "wall gets thicker in skin depths",
            }
    # 8) the radial profile must not increase inward, and its outer end is the
    #    normalisation point by definition
    prof = radial_profile(x_out, x_in, n_prof) if n_prof >= 2 else []
    if len(prof) >= 2:
        jv = [row["j_rel"] for row in prof]
        checks["profile_monotone_outward"] = bool(all(
            jv[i] <= jv[i + 1] * (1.0 + 1e-6) for i in range(len(jv) - 1)))
        checks["profile_outer_is_max"] = bool(abs(jv[-1] - 1.0) <= 1e-12)
        # 9) profile against the closed form, where the closed form still exists
        if x_out <= 5.0:
            pb = radial_profile_bessel(x_out, x_in, n_prof)
            checks["profile_vs_closed_rel"] = max(
                abs(row["j_rel"] / ref["j_rel"] - 1.0) for row, ref in zip(prof, pb))
        else:
            checks["profile_vs_closed_skipped"] = (
                "x_out = %.3g > 5: the closed-form profile is the one that fails "
                "here (it reports a flat 1.0 at x = 72), so it is not used as a "
                "reference" % x_out)

    rgh = None
    if roughness_rq_um is not None:
        try:
            rq = float(roughness_rq_um) * 1e-6
        except (TypeError, ValueError):
            return err("roughness_rq_um must be a number (RMS height Rq, not Rz)",
                       "BAD_PARAMETER")
        if rq < 0.0:
            return err("roughness_rq_um must be >= 0", "BAD_PARAMETER")
        rgh = roughness_factor(rq, delta_m)

    warnings = []
    if hollow and x_wall < 1.0 and x_out > 3.0:
        warnings.append(
            "the wall is %.3g um and the skin depth is %.3g um: the current fills "
            "the whole wall, so this via is NOT skin-limited, it is limited by how "
            "much copper the plating put there. Making the drill bigger helps; "
            "making the plating thicker helps more."
            % ((a2 - a1) * 1e3, delta_mm * 1e3))
    if x_out > 0.5:
        warnings.append(
            "isolated conductor: no proximity effect from a neighbouring wire, no "
            "return-path eddy current, no pad or antipad, no 3-D spreading at the "
            "ends. A via in a plane with a close return is a different problem.")
    if rgh is None and x_out > 0.5 and delta_mm * 1e3 < 3.0:
        warnings.append(
            "skin depth is %.3g um: plated copper and rolled foil have roughness of "
            "the same order, so this R is a LOWER bound. Pass roughness_rq_um "
            "(Hammerstad-Jensen) for the rough number." % (delta_mm * 1e3))
    if not hollow and (a2 / delta_mm) > 300.0:
        warnings.append(
            "x = a/delta = %.3g: the answer is the surface impedance x/2 to better "
            "than 1e-4, and the Bessel branch there is the asymptotic one, so the "
            "number is as good as the geometry description is. It does NOT get "
            "better with more digits." % x_out)

    value = {
        "frequency_hz": f,
        "skin_depth_um": delta_mm * 1e3,
        "skin_depth_mm": delta_mm,
        "outer_diameter_mm": a2 * 2.0,
        "bore_mm": a1 * 2.0,
        "wall_um": (a2 - a1) * 1e3,
        "hollow": hollow,
        "x_outer": x_out,
        "x_inner": x_in,
        "wall_over_skin_depth": x_wall,
        "dc_resistance_ohm_per_m": r_dc_per_m,
        "ac_resistance_ohm_per_m": r_ac_per_m,
        "ac_reactance_ohm_per_m": x_ac_per_m,
        "ac_over_dc": r_ratio,
        "x_over_dc": x_ratio,
        "internal_inductance_nh_per_m": l_int_nh_per_m,
        "surface_resistance_ohm_per_sq": r_surf,
        "outer_perimeter_ohm_per_m_at_hf": r_surf / (TWO_PI * a2 * 1e-3)
        if a2 > 0 else None,
        "f_at_r_1pct_over_dc_hz": f_1pct,
        "f_at_r_10pct_over_dc_hz": f_10pct,
        "f_at_r_2x_dc_hz": f_2x,
        "regime": regime,
        "profile": prof,
        "profile_note": "r_over_outer runs 0 -> 1 from the axis (or from the "
                        "bore) to the outer surface; j_rel is |J| relative to the "
                        "outer surface; phase_deg is wrapped to (-180,180], "
                        "phase_lag_deg is the accumulated lag and keeps decreasing "
                        "inward (thousands of degrees across a wire many skin "
                        "depths wide)",
    }
    if rgh is not None:
        value["roughness_k"] = rgh
        value["ac_resistance_rough_ohm_per_m"] = r_ac_per_m * rgh
    if length is not None:
        value["length_mm"] = length
        value["dc_resistance_ohm"] = r_dc_per_m * length * 1e-3
        value["ac_resistance_ohm"] = r_ac_per_m * length * 1e-3
        value["ac_inductance_nh"] = l_int_nh_per_m * length * 1e-3

    model = (
        "exact Bessel solution of the round geometry: E'' + E'/r + k^2 E = 0 with "
        "k = (1-j)/delta, E'(a1) = 0 for a bore (the hole encloses no net current, "
        "so H_phi(a1) = 0) or regularity on the axis for a solid wire, and "
        "E(a2) = 1 fixing the scale. Solid: Z/R_dc = (k a2/2) J0(k a2)/J1(k a2), "
        "which is numerically stable and IS the answer. Tube: same solution, but "
        "evaluated through the log derivative eta = dlnE/drho (the Riccati "
        "equation of the Bessel equation), which obeys "
        "eta' = (k a2)^2 - eta^2 - eta/rho with eta(a1/a2) = 0 and gives "
        "Z/R_dc = j x^2 (1-(a1/a2)^2)/eta(1). The closed form of the tube "
        "(c = -J1(k a1)/Y1(k a1)) is NOT used for the answer: above x ~ 15 it "
        "cancels to the solid-wire value.")
    basis = ("Maxwell in a good conductor with cylindrical symmetry: displacement "
             "current dropped, mu = mu0*mu_r, one rho, one mu_r, no dependence on "
             "phi or z. delta = sqrt(rho/(pi f mu)) is the same skin depth the rest "
             "of this server uses, so a wire and a foil at the same rho and f never "
             "disagree about delta. The Bessel functions are computed here (ascending "
             "series below |w| = 20, Debye/Poincare asymptotic above, overlapping on "
             "|w| in [20,28]) because this server has no runtime dependencies.")
    fd_txt = ("%d points" % int(checks.get("fd_points") or 0)) if fd_verify else "off"
    precision = (
        "Answer path. Solid: closed form through J0/J1 at one argument - no "
        "subtraction of two nearly equal numbers, measured against the Riccati "
        "walk to 5e-15 (x = 400) and against the asymptote to 6e-11 (x = 1e6). "
        "Tube: RK4 on the Riccati equation with %d steps, reported with a "
        "Richardson convergence estimate in checks (typically 1e-12), and its DC "
        "limit is analytic (eta = j x^2 (rho^2-ri^2)/rho gives exactly 1). Below "
        "|w| = 20 the Bessel values are a power series summed to the last "
        "representable bit, above it the asymptotic series truncated at its "
        "smallest term; the branches overlap and agree to ~1e-12, also in checks. "
        "A radial finite-difference solve of the same ODE (%s) is run whenever "
        "x_out <= 200 - O(h^2), so it confirms the algebra to 1e-5..1e-3, never "
        "the model. The PHYSICS is a single isolated cylinder with one homogeneous "
        "rho: no proximity effect, no pad, no antipad, no end spreading, no "
        "temperature rise." % (checks.get("riccati_steps") or 0, fd_txt))
    assumptions = {
        "outer_diameter_mm": a2 * 2.0,
        "bore_mm": a1 * 2.0,
        "rho_ohm_mm": rho,
        "temp_c": temp_c if rho_ohm_mm is None else None,
        "mu_r": mur,
        "sigma_s_per_m": 1.0 / (rho * 1e-3),
        "frequency": "single frequency; call per point for a band",
        "current": "along z, uniform in phi and z - the only variation solved is "
                   "radial",
        "bore_condition": ("the hole encloses no net current, so H_phi(a1) = 0 and "
                           "E'(a1) = 0. This is NOT the same as a symmetry plane: "
                           "a tube is not a mirror half of a solid wire"),
        "glossary": ("drill_mm is the FINISHED hole (the bore): the copper sits "
                     "OUTSIDE it, so the outer diameter is drill + 2*plating. "
                     "via_resistance models the other convention (its annulus lies "
                     "INSIDE drill_mm, i.e. drill = the drilled hole before "
                     "plating) unless you pass plating_outside=true there. The two "
                     "drawings differ by the annulus, ~18 %% on a 0.3 mm drill with "
                     "25 um plating, and neither is wrong - they are different "
                     "drawings"),
        "roughness": ("Hammerstad-Jensen multiplier on the surface resistance, OFF "
                      "unless roughness_rq_um is given" if rgh is None else
                      "Hammerstad-Jensen multiplier from the Rq given"),
        "temperature": "rho is fixed at temp_c; no I^2R self-heating",
        "answer_path": ("solid -> closed form; tube -> Riccati log-derivative "
                        "integration (the tube closed form cancels above x ~ 15 "
                        "and is used only as a cross-check below x = 10)"),
    }
    return ok(value, "mixed (see keys: Hz, mm, um, ohm, nH, deg, dimensionless)",
              model, basis, precision, assumptions,
              extra={
                  "checks": checks,
                  "warnings": warnings,
                  "delta_m": delta_m,
                  "asymptotes": {
                      "dc_series_r": "1 + x^4/48",
                      "dc_series_x": "x^2/4  (=> internal inductance mu/(8*pi) = "
                                     "50.0 nH/m, independent of the diameter)",
                      "dc_series_flat_sheet": "a FLAT sheet is 1 + 4*s^4/45, not "
                                              "the same coefficient: the round "
                                              "geometry is 16/45 of the flat one",
                      "large_x_solid_r": "x/2 + 1/4 + 3/(32x)",
                      "large_x_x": "x/2 - 3/(32x)  (45 degrees: R = X)",
                      "thin_wall": "the wall degenerates to the flat sheet of the "
                                   "same thickness with faces='one', because the "
                                   "hole is an H = 0 surface",
                      "dc_internal_inductance": "mu/(8*pi) = 50.0 nH/m for a "
                                                "solid round wire, whatever the "
                                                "diameter",
                  },
                  "limits": {
                      "not_modelled": ["proximity effect from a neighbouring "
                                       "conductor or the return path",
                                       "azimuthal variation (a via that is not a "
                                       "full cylinder)",
                                       "plating interfaces / barrier layers",
                                       "pad, antipad and 3-D spreading at the ends",
                                       "displacement current and radiation",
                                       "any temperature rise"],
                      "bore_rule": "a1 = 0 means solid; a1 > 0 always uses the tube "
                                   "formula, the two are not blended",
                  },
              })
