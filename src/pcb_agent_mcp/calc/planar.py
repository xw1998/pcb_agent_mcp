"""Planar: the two things `cavity.py` says it does not model about a plane pair.

`cavity.py` answers where the modes are (`plane_resonance`) and how hard they are
damped by copper and dielectric (`plane_q`) - and its own "what it is NOT" list
names the two gaps this module fills:

  * the edges RADIATE (so there is a radiation Q on top of Qc and Qd), and
  * an open edge makes the plane electrically longer than it is drawn (so the
    resonance sits BELOW the textbook f_mn by a few percent).

Both are handled here, each by one closed form with the derivation and its
limits written down, and neither one is smuggled into `plane_q`: that function's
answer does not move (its docstring now points here instead of just refusing).

1. Radiation Q, `plane_radiation_q`.

   An open edge is a narrow slot (gap h) in what is otherwise an infinite flange.
   For a narrow slot the equivalent magnetic current is M = 2*E inside the gap,
   which for h << lambda reduces to a magnetic line source of strength 2*E*h; the
   2-D far field of a magnetic line source then gives, per unit edge length,

       P/L = (1/2) * omega * eps0 * V^2                  (V = E*h, gap voltage)

   i.e. a radiation conductance per unit width G' = omega*eps0 = k0/eta0. Two
   independent ways give the same number (the duality argument above and the
   far-field pattern integrated over the half space), and it passes the check
   that decides whether such a formula is sane at all: the fraction of the
   incident TEM power that leaves at one open end is G'*eta0*h = k0*h = 2*pi*h/
   lambda0, which goes to zero as the gap closes - a narrow gap is nearly a
   perfect open circuit, exactly as it must be. That identity is also the model's
   own validity gate: k0*h >= 1 would mean the edge radiates more than arrives.

   Stored energy of the mode is U = (1/2)*eps0*er*V^2*A/h, so omega cancels:

       Q_rad = er * A / (h * sum over open edges of L_edge)

   For a square plane with all four edges open and the uniform field that is
   er*a/(4h) - 50 mm on 0.2 mm of er=4.3 gives Q_rad = 269, which is the range
   plane pairs are actually measured in, and it needs no fitting constant to
   land there. For mode (m,n) the field shape enters through the two integrals
   int(int)(V/V0)^2 dA and int(V/V0)^2 dl, which for cos*cos modes are closed
   form (a/2 or a, etc.) - so the mode numbers do change the answer, unlike Qc.

2. Open-edge detuning, `plane_open_end`.

   Love (1924) solved the 2-D strip capacitor exactly; Yariv (2021) rederives
   the large-aspect-ratio limit by conformal mapping:

       C ~ eps0*[ l/h + (1/pi)*(ln(2*pi*l/h) + 1) ]     per unit length

   for plates of length 2l separated by 2h. Both ends contribute, so one open
   edge adds an equivalent parallel-plate length of

       delta_l = (h/(2*pi)) * (ln(2*pi*a/h) + 1)

   with a the plate dimension perpendicular to that edge (l/h = a/h). Then
   a_eff = a + (number of open edges in that direction)*delta_l and the mode
   moves down: for 50 mm on 0.2 mm that is delta_l = 0.266 mm, i.e. -1.05 % on
   the (1,0) mode; for 1.6 mm of FR4 it is -6.4 %, which is the "few percent"
   `cavity.py` warns about.

What this module is NOT, and says so on every return:
  * the aperture is a narrow slot in an infinite PEC flange with AIR outside -
    the dielectric slab ending at the same place is ignored, so the radiation
    number is a scale, not a measurement, and the detuning leans high (the real
    fringe field sees air on part of its path);
  * no corner/truncation correction: the edge is treated as infinitely long
    (both formulas are per-unit-length quantities times the edge length), so
    the four corners are not modelled;
  * no radiation from a split plane, a void, or a fence gap - only the four
    straight edges of the rectangle;
  * nothing about what is soldered across the planes, which is usually the
    dominant loss and the dominant detuning of a real PDN;
  * no verdict. A lower resonance or a higher Q is not good or bad on its own.
"""
import math

from .result import ok, err
from .roughness import (MU0, huray_loss_factor, huray_surface_ratio,
                        huray_transition, roughness_factor, skin_depth_m)

C0 = 299792458.0            # m/s, exact by definition
ETA0 = MU0 * C0             # ohm, exact in SI-2019 (mu0 and c are both exact)
EPS0 = 1.0 / (MU0 * C0 * C0)
COPPER_SIGMA = 5.8e7        # S/m, IACS copper at 20 C
EDGE_NAMES = ("x0", "x1", "y0", "y1")
ALL_EDGES = list(EDGE_NAMES)


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
    return float("%.*g" % (digits, x))


def _edges(open_edges):
    """Validate an edge list. Returns (list, None) or (None, err envelope)."""
    if open_edges is None:
        return list(ALL_EDGES), None
    if isinstance(open_edges, str):
        s = open_edges.strip().lower()
        if s in ("", "all", "none"):
            return (list(ALL_EDGES) if s != "none" else []), None
        open_edges = [p.strip() for p in s.split(",") if p.strip()]
    if not isinstance(open_edges, (list, tuple)):
        return None, err("open_edges must be a list of edge names drawn from "
                         "%s, or the string 'all' / 'none'" % (list(EDGE_NAMES),),
                         "ARG_INVALID", extra={"open_edges": str(open_edges)})
    seen = []
    for e in open_edges:
        if not isinstance(e, str):
            return None, err("open_edges entries must be strings", "ARG_INVALID")
        name = e.strip().lower()
        if name not in EDGE_NAMES:
            return None, err("unknown edge %r: an edge is one of %s (x0/x1 are the "
                             "edges at x=0 and x=a, y0/y1 at y=0 and y=b)"
                             % (e, list(EDGE_NAMES)), "ARG_INVALID",
                             extra={"open_edges": str(open_edges)})
        if name in seen:
            return None, err("edge %r listed twice" % (e,), "ARG_INVALID",
                             hint="each edge either radiates or it does not")
        seen.append(name)
    return seen, None


def _mode_shape(m, n, a_m, b_m):
    """Integrals of the mode shape, for a cos(m*pi*x/a)*cos(n*pi*y/b) field.

    Returns (i_area, per_edge) with the field normalised to V0 = 1 at the
    antinodes, i.e. i_area = int(int)(V/V0)^2 dA [m^2] and per_edge[name] =
    int(V/V0)^2 dl along that edge [m].
    """
    sm = 1.0 if m == 0 else 0.5      # mean of cos^2 over a full period / half
    sn = 1.0 if n == 0 else 0.5
    i_area = a_m * sm * b_m * sn
    per_edge = {"x0": b_m * sn, "x1": b_m * sn,
                "y0": a_m * sm, "y1": a_m * sm}
    return i_area, per_edge


def _which_dominates(q_rad, qc, qd):
    """Name the smallest of the three Qs (the one that sets the damping)."""
    pairs = [("radiation", q_rad), ("copper", qc)]
    if qd is not None:
        pairs.append(("dielectric", qd))
    best = min(pairs, key=lambda kv: kv[1])
    return best[0]


def _validate_plane(a_mm, b_mm, h_mm, er, m, n):
    """Shared argument checks for the two plane-pair functions."""
    a = _num(a_mm)
    b = _num(b_mm)
    h = _num(h_mm)
    e = _num(er)
    mi = _num(m)
    ni = _num(n)
    if a is None:
        return None, err("a_mm must be a number", "ARG_INVALID")
    if b is None:
        return None, err("b_mm must be a number", "ARG_INVALID")
    if h is None:
        return None, err("h_mm must be a number", "ARG_INVALID")
    if e is None:
        return None, err("er must be a number", "ARG_INVALID")
    if mi is None or ni is None:
        return None, err("m and n must be numbers", "ARG_INVALID")
    for name, v in (("a_mm", a), ("b_mm", b), ("h_mm", h)):
        if v <= 0.0:
            return None, err("%s must be > 0" % name, "ARG_RANGE",
                             extra={name: v})
    if e < 1.0:
        return None, err("er = %g < 1: the mode formula assumes a dielectric, "
                         "not a plasma" % e, "ARG_RANGE", extra={"er": e})
    if mi < 0.0 or ni < 0.0:
        return None, err("m and n must be >= 0", "ARG_RANGE")
    if mi != int(mi) or ni != int(ni):
        return None, err("m and n must be non-negative integers - the mode "
                         "numbers of a TM_mn0 resonance", "ARG_INVALID",
                         extra={"m": mi, "n": ni})
    if mi == 0 and ni == 0:
        return None, err("mode (0,0) is the DC/parallel-plate field, not a "
                         "resonance: there is no frequency and no Q",
                         "ARG_RANGE", hint="ask for (1,0), (0,1), (1,1), ...")
    return (a / 1000.0, b / 1000.0, h / 1000.0, e, int(mi), int(ni)), None


def huray_roughness(f_hz, nodule_radius_um=None, surface_ratio=None,
                    nodule_density_per_mm2=None, conductivity_s_m=COPPER_SIGMA,
                    mur=1.0, rq_um=None):
    """Huray snowball model: the Rs multiplier from a nodular copper surface.

    Take it as: a rough surface is a carpet of hemispheres of radius r, N per
    unit area. At DC the current has to climb over them, which multiplies the
    surface area (and therefore Rs) by the Hall-Huray surface ratio

        RF = 1 + (3/2)*sr,        sr = 4*pi*r^2*N      (sr = 0 is a flat foil)

    At frequency the skin depth decides how much of that carpet the current
    actually resolves, through Huray's transition function

        F = 1/sqrt(1 + 2x + 2x^2),     x = 2*delta_s/r
        K = 1 + (RF - 1)*F

    and F is the modulus of the causal form [1 + (1-j)*2*delta/r]^(-1/2) that
    Shlepnev shows produces exactly the loss increase of the original Huray
    model. Both limits are built in and both are checked in the tests: x -> 0
    (delta -> 0, high frequency) gives K -> RF, the worst case; x -> inf (low
    frequency, the current never sees the bumps) gives K -> 1. The knee is
    x = 1, i.e. delta_s = r/2, where F = 1/sqrt(5) exactly - that is the anchor
    the tests use instead of a remembered table.

    Numbers for copper at 1 GHz (delta_s = 2.090 um), r = 0.5 um:
        sr = 1  ->  RF = 2.5,  F = 0.0797,  K = 1.120
        sr = 6  ->  RF = 10,   F = 0.0797,  K = 1.717
    and at 10 GHz (delta_s = 0.661 um) the same surface gives K = 1.334 / 3.001,
    because the skin depth has come down to the size of the nodules. Compare
    Hammerstad-Jensen at Rq = 0.5 um on 1 GHz copper: K = 1.051. The two models
    are within a factor of a few and are NOT interconvertible - Rq is a height
    statistic, (r, sr) is a geometry, and different surfaces share one Rq.

    How to specify the surface: give the nodule radius plus EITHER the Hall-Huray
    surface ratio (what Ansys SIwave calls "Huray surface ratio" = 4*pi*a^2*N/A)
    OR the areal nodule density in per-mm^2. Not both, and not neither.

    What it is NOT: no foil profile, no anisotropic roughness (a rolled foil is
    not a carpet of equal spheres), no frequency dependence other than the skin
    depth, no dielectric wicking into the tooth, and no conversion to or from Rq
    (`rq_um` here is only reported alongside as the Hammerstad number for the
    same frequency, so the two can be compared, not substituted).
    """
    f = _num(f_hz)
    r_um = _num(nodule_radius_um)
    sg = _num(conductivity_s_m)
    mu_r = _num(mur)
    sr_in = None if surface_ratio is None else _num(surface_ratio)
    nd = None if nodule_density_per_mm2 is None else _num(nodule_density_per_mm2)
    rq_um = None if rq_um is None else _num(rq_um)

    if f is None:
        return err("f_hz must be a number", "ARG_INVALID")
    if f <= 0.0:
        return err("f_hz must be > 0", "ARG_RANGE", extra={"f_hz": f})
    if r_um is None:
        return err("nodule_radius_um is required (the radius of one Huray "
                   "nodule; typical published values are 0.1..1 um)",
                   "ARG_INVALID")
    if r_um <= 0.0:
        return err("nodule_radius_um must be > 0: a flat surface is described by "
                   "leaving this function out, not by a zero radius", "ARG_RANGE",
                   extra={"nodule_radius_um": r_um})
    if sg is None or sg <= 0.0:
        return err("conductivity_s_m must be a number > 0", "ARG_INVALID")
    if mu_r is None or mu_r <= 0.0:
        return err("mur must be a number > 0", "ARG_INVALID")
    if surface_ratio is not None and sr_in is None:
        return err("surface_ratio must be a number", "ARG_INVALID")
    if nodule_density_per_mm2 is not None and nd is None:
        return err("nodule_density_per_mm2 must be a number", "ARG_INVALID")
    if surface_ratio is not None and nodule_density_per_mm2 is not None:
        return err("give EITHER surface_ratio OR nodule_density_per_mm2: they "
                   "describe the same carpet and an inconsistent pair would "
                   "silently pick one", "ARG_INVALID")
    if surface_ratio is None and nodule_density_per_mm2 is None:
        return err("the surface needs a nodule density: pass surface_ratio "
                   "(Hall-Huray, 4*pi*r^2*N per unit flat area) or "
                   "nodule_density_per_mm2", "ARG_MISSING",
                   hint="published 'high/medium/low roughness' Huray sets use "
                        "r = 0.5 um with surface ratio 6 / 3 / 1")
    r = r_um * 1e-6
    if sr_in is not None:
        sr = sr_in
        if sr < 0.0:
            return err("surface_ratio must be >= 0 (0 = a flat surface)",
                       "ARG_RANGE", extra={"surface_ratio": sr})
        nd_per_m2 = (sr / (4.0 * math.pi * r * r)) if r > 0 else None
    else:
        if nd is None or nd < 0.0:
            return err("nodule_density_per_mm2 must be >= 0", "ARG_RANGE")
        nd_per_m2 = nd * 1e6
        sr = huray_surface_ratio(r, nd_per_m2)
    if sr is None:
        return err("could not build a surface ratio from those arguments",
                   "ARG_INVALID")
    if sr > 30.0:
        return err("surface_ratio = %g implies RF = %g, far outside anything "
                   "measured (published Huray sets are 1/3/6, and a carpet of "
                   "hemispheres cannot exceed sr ~ 2*pi before the nodules "
                   "overlap)" % (sr, 1.0 + 1.5 * sr), "ARG_RANGE",
                   extra={"surface_ratio": sr})

    w = 2.0 * math.pi * f
    delta = math.sqrt(2.0 / (w * MU0 * mu_r * sg))
    lam0 = C0 / f
    if r > lam0 / 100.0:
        return err("nodule radius %.3g um is not small against the wavelength "
                   "(%.3g mm at %.6g Hz): the snowball model is quasi-static and "
                   "cannot be used here" % (r_um, lam0 * 1e3, f),
                   "MODEL_SCOPE", extra={"nodule_radius_um": r_um,
                                         "lambda0_mm": _sig(lam0 * 1e3)})

    x = 2.0 * delta / r
    trans = huray_transition(r, delta)
    rf = 1.0 + 1.5 * sr
    k_huray = huray_loss_factor(delta, r, sr)
    rs_smooth = 1.0 / (sg * delta)
    rs_rough = rs_smooth * k_huray
    # delta = r/2  <=>  w = 8/(mu0*sg*r^2); the frequency is w/2pi. The
    # 2*pi is easy to drop, and dropping it moves the knee up by 2*pi (in
    # f) so the reported knee lands at delta = r/2.5 instead of r/2.
    f_knee = 8.0 / (2.0 * math.pi * MU0 * mu_r * sg * r * r)

    out = {
        "f_hz": _sig(f),
        "skin_depth_um": _sig(delta * 1e6),
        "nodule_radius_um": _sig(r_um),
        "surface_ratio": _sig(sr),
        "nodule_density_per_mm2": (_sig(nd_per_m2 * 1e-6)
                                   if nd_per_m2 is not None else None),
        "surface_ratio_dc": _sig(rf),
        "roughness_factor_RF": _sig(rf),
        "transition_x": _sig(x),
        "transition_F": _sig(trans),
        "loss_factor_K": _sig(k_huray),
        "surface_resistance_ohm_sq_smooth": _sig(rs_smooth),
        "surface_resistance_ohm_sq": _sig(rs_rough),
        "excess_loss_pct": _sig(100.0 * (k_huray - 1.0)),
        "knee_frequency_hz": _sig(f_knee),
        "knee_skin_depth_um": _sig(r / 2.0 * 1e6),
        "limits": {
            "delta_over_r_to_0": "K -> RF = %s (worst case, all nodules resolved)"
                                 % _sig(rf),
            "delta_over_r_to_inf": "K -> 1 (the current never sees the nodules)",
            "at_transition_x_1": "delta = r/2 gives F = 1/sqrt(5) = 0.447214 and "
                                 "K = 1 + 0.447214*(RF-1)",
            "monotone_in_f": "K rises with frequency because delta_s falls; the "
                             "half-way point in x is the knee above",
        },
        "which_regime": ("nodules larger than the skin depth (x = %.3g < 2): part "
                         "of every nodule is already resolved, so K is near RF"
                         % x) if x < 2.0 else
                        ("skin depth well above the nodule radius (x = %.3g): the "
                         "current mostly flows over the carpet, K is closer to 1"
                         % x),
    }
    if rq_um is not None:
        if rq_um < 0.0:
            return err("rq_um must be >= 0", "ARG_RANGE")
        k_h = roughness_factor(rq_um * 1e-6, delta)
        out["hammerstad_comparison"] = {
            "rq_um": _sig(rq_um),
            "hammerstad_loss_factor": _sig(k_h),
            "huray_loss_factor": _sig(k_huray),
            "ratio_huray_over_hammerstad": _sig(k_huray / k_h if k_h else None),
            "model": "K = 1 + (2/pi)*atan(1.4*(Rq/delta_s)^2) - a DIFFERENT "
                     "parameterisation of the same physics, reported for "
                     "comparison only: no Rq <-> (r, sr) conversion is done here "
                     "or anywhere else in this build",
        }

    return ok(out, "dimensionless Rs multiplier",
              "Huray snowball (nodular) surface-roughness model, in Shlepnev's "
              "unified form",
              "K = 1 + (RF-1)*F with RF = 1 + (3/2)*sr, sr = 4*pi*r^2*N, "
              "F = |1 + (1-j)*2*delta_s/r|^(-1) /^... = 1/sqrt(1+2x+2x^2), "
              "x = 2*delta_s/r, delta_s = sqrt(2/(omega*mu*sigma)); Rs *= K "
              "(equivalently sigma_eff = sigma/K^2)",
              "closed form; two limits are exact (K->RF as delta->0, K->1 as "
              "delta->inf) and the knee delta=r/2 gives F=1/sqrt(5) exactly. The "
              "model is a two-parameter idealisation of a real foil, so treat "
              "the multiplier as a scale (published Huray sets span K = 1.05..3 "
              "over 1..10 GHz), not as a measurement",
              {"surface": "hemispheres of one radius, one areal density, sitting "
                          "on a flat plane - no height distribution, no foil "
                          "profile, no anisotropy",
               "conductor": "sigma and mu as single numbers at 20 C; no "
                            "temperature dependence beyond what you pass in",
               "interface": "the roughness multiplies Rs of the copper alone; "
                            "the dielectric wicking into the tooth and the "
                            "profile's effect on the field are not modelled",
               "conversion": "Rq and (r, sr) are not interconverted - pass rq_um "
                             "only to see the Hammerstad number beside this one"},
              {"source": "Yu. Shlepnev, 'Unified approach to interconnect "
                         "conductor surface roughness modelling', EPEPS 2017, "
                         "eqs. (1), (4), (5) (free PDF: simberian.com); the "
                         "RF = 1 + (3/2)*sr identity is stated there",
               "how_to_use": "multiply Rs (or divide Qc) by loss_factor_K; for a "
                             "plane pair use plane_radiation_q's sibling "
                             "cavity_plane_q with the equivalent Rq only if you "
                             "accept the Hammerstad model instead",
               "does_not_prove": "which of the two models is right for a given "
                                 "foil - that needs the foil's measured loss"})


def plane_radiation_q(a_mm=None, b_mm=None, h_mm=None, er=None, m=1, n=0,
                      open_edges=None, tan_delta=None,
                      conductivity_s_m=COPPER_SIGMA):
    """Radiation Q of a plane-pair mode, from its open edges.

    Each open edge is a narrow slot of width h in an infinite flange. The
    radiation conductance per unit width of such a slot is G' = omega*eps0 =
    k0/eta0 (derived in the module docstring; the same number comes out of the
    magnetic-line-source argument and of the far-field integral), so

        P_edge = (1/2)*G'*int(V^2 dl)   and   U = (1/2)*eps0*er*(1/h)*int(int)V^2 dA

    and omega cancels, leaving a number that depends on the shape and on er but
    NOT on frequency:

        Q_rad = er * int(int)(V/V0)^2 dA / (h * sum_open_edges int(V/V0)^2 dl)

    For a square plane with every edge open and the uniform field, that is
    er*a/(4h): 50 mm over 0.2 mm of er=4.3 gives 269, over 1.6 mm it gives 34.
    The mode numbers do matter here (unlike Qc = h/delta_s): a (1,1) mode has
    half the stored energy per unit antinode voltage of a (1,0) and radiates
    from all four edges instead of two, so its Q_rad is lower by 2x.

    Validity, and it is the model's own gate: the fraction of the incident TEM
    power that leaves at one open edge is k0*h = 2*pi*h/lambda0. That has to be
    below 1 - otherwise the edge radiates more than arrives - so h >> lambda0/6
    is refused (MODEL_SCOPE) and k0*h > 0.1 is flagged. Above that, the narrow-
    slot approximation is the weak point: the real plane pair's edge is a
    half-guided-wavelength long, which is exactly the regime no per-unit-length
    model covers well. Compare with a 3-D solver before believing a number.

    Q_total: pass tan_delta and the copper Qc = h/delta_s and Qd = 1/tan_delta
    are added in reciprocal form, giving the damping a real board has at that
    mode - except that what is soldered across the planes is not in it.

    Not modelled: the dielectric slab ending at the same place as the copper
    (the outside is treated as air), the four corners, radiation from a split
    plane / void / a via-fence gap, and any excitation or coupling. No verdict.
    """
    geo, e = _validate_plane(a_mm, b_mm, h_mm, er, m, n)
    if e:
        return e
    a, b, h, er, m, n = geo
    edges, e = _edges(open_edges)
    if e:
        return e
    if not edges:
        return err("no open edges: with all four edges walled the mode is a "
                   "closed cavity and has no radiation Q at all", "ARG_RANGE",
                   hint="leave open_edges out for a bare rectangle, or list the "
                        "edges that really are open (x0/x1/y0/y1)")
    td = None
    if tan_delta is not None:
        td = _num(tan_delta)
        if td is None:
            return err("tan_delta must be a number", "ARG_INVALID")
        if td <= 0.0:
            return err("tan_delta must be > 0: a lossless dielectric is expressed "
                       "by leaving it out", "ARG_RANGE")
        if td >= 1.0:
            return err("tan_delta %g >= 1 is not a dielectric loss" % td,
                       "ARG_RANGE")
    sg = _num(conductivity_s_m)
    if sg is None or sg <= 0.0:
        return err("conductivity_s_m must be a number > 0", "ARG_INVALID")

    f = C0 / (2.0 * math.sqrt(er)) * math.hypot(m / a, n / b)
    lam0 = C0 / f
    w = 2.0 * math.pi * f
    k0h = w / C0 * h
    if k0h >= 1.0:
        return err("k0*h = %.3f >= 1: the gap is not narrow against the "
                   "wavelength (h = %.4g mm at %.4g Hz), so the edge radiates "
                   "more power than the mode carries and the narrow-slot model "
                   "has nothing to say" % (k0h, h * 1e3, f), "MODEL_SCOPE",
                   extra={"k0_h": _sig(k0h), "lambda0_mm": _sig(lam0 * 1e3)})

    i_area, per_edge = _mode_shape(m, n, a, b)
    i_edge = sum(per_edge[k] for k in edges)
    q_rad = er * i_area / (h * i_edge)
    p_per_v2 = 0.5 * w * EPS0 * i_edge          # W for a 1 V (peak) gap voltage

    qc = h / skin_depth_m(f, sg)
    qd = (1.0 / td) if td else None
    q_total = q_rad
    if qd is None:
        q_total = 1.0 / (1.0 / qc + 1.0 / q_rad)
    else:
        q_total = 1.0 / (1.0 / qc + 1.0 / qd + 1.0 / q_rad)

    out = {
        "f_hz": _sig(f),
        "lambda0_mm": _sig(lam0 * 1e3),
        "k0_h": _sig(k0h),
        "mode": {"m": m, "n": n},
        "open_edges": list(edges),
        "edge_integral_m": _sig(i_edge),
        "area_integral_m2": _sig(i_area),
        "q_radiation": _sig(q_rad),
        "radiation_conductance_per_width_s_per_m": _sig(w * EPS0),
        "radiated_power_w_at_1v_gap": _sig(p_per_v2),
        "q_conductor": _sig(qc),
        "q_dielectric": _sig(qd) if qd is not None else None,
        "q_total": _sig(q_total),
        "which_dominates": _which_dominates(q_rad, qc, qd),
        "per_edge": {k: {"length_m": _sig(per_edge[k]),
                         "q_rad_alone": _sig(er * i_area / (h * per_edge[k]))}
                     for k in edges},
        "scaling": {
            "q_radiation_vs_h": "proportional to 1/h - a thinner dielectric "
                                "radiates comparatively less and rings longer",
            "q_radiation_vs_er": "proportional to er (more stored energy, same "
                                 "radiated power)",
            "q_radiation_vs_f": "flat for a fixed geometry - omega cancels "
                                "between U and P",
        },
    }
    if k0h > 0.1:
        out["warning"] = ("k0*h = %.3f is above 0.1: the narrow-slot aperture "
                          "model is being pushed" % k0h)

    return ok(out, "dimensionless Q",
              "radiation Q of a plane-pair mode radiating from its open edges "
              "(narrow-slot aperture, infinite PEC flange)",
              "P per open edge = (1/2)*omega*eps0*int(V^2)dl with the radiation "
              "conductance per unit width G' = omega*eps0 (= k0/eta0) for a slot "
              "narrow against lambda0; U = (1/2)*eps0*er*(1/h)*int(int)V^2 dA; "
              "Q_rad = omega*U/P = er*A_eff/(h*sum L_eff); 1/Q = 1/Qc + 1/Qd + "
              "1/Q_rad",
              "closed form on the narrow-slot approximation (h << lambda0, "
              "checked through k0*h) with an unperturbed cos*cos mode and an "
              "infinite PEC flange; the edge is treated as infinitely long, so "
              "no corner correction. Expect tens of percent against a 3-D "
              "solver, and read it as 'does radiation matter at all here'",
              {"outside": "air (eps0, mu0): the dielectric slab is assumed to end "
                          "at the same place as the copper, so the aperture is "
                          "not half-embedded and the radiated power is not "
                          "corrected for the slab",
               "mode": "cos(m*pi*x/a)*cos(n*pi*y/b) with PMC side walls where an "
                       "edge is closed - the idealised cavity field, not the "
                       "perturbed one",
               "walls": "a 'closed' edge is a PMC wall here; a real via fence "
                        "leaks, and that leakage is not radiation from the edge",
               "components": "no soldered capacitance, no package, no load - the "
                             "dominant damping of a real PDN resonance"},
              {"how_to_use": "1/Q_total above already adds Qc and Qd in "
                             "reciprocal form; for a detuned f use "
                             "plane_open_end() first and feed its f_eff here",
               "does_not_prove": "that the resonance rings too much or too "
                                 "little - that needs an excitation and a "
                                 "coupling, neither of which is in this model"})


def plane_open_end(a_mm=None, b_mm=None, h_mm=None, er=None, m=1, n=0,
                   open_edges=None):
    """How far an open edge pulls a plane-pair resonance down.

    The 2-D strip capacitor (two plates of length 2l, separation 2h) has an
    exact solution (Love 1924) whose large-aspect-ratio limit is

        C ~ eps0*[ l/h + (1/pi)*(ln(2*pi*l/h) + 1) ]      per unit length

    (Yariv 2021, eq. 5.20 / A5, rederived by conformal mapping). The bracket is
    the ideal parallel-plate capacitance plus ONE correction shared by the two
    ends, so a single open edge behaves like an extra parallel-plate length of

        delta_l = (h/(2*pi)) * (ln(2*pi*a/h) + 1)

    with a the plate dimension PERPENDICULAR to that edge (l/h = a/h). Then
    a_eff = a + (open edges along x)*delta_l_x, likewise in y, and

        f_eff = c/(2*sqrt(er)) * sqrt((m/a_eff)^2 + (n/b_eff)^2)

    For 50 mm on 0.2 mm: delta_l = 0.266 mm and the (1,0) mode moves down
    1.05 %. For 1.6 mm of FR4 on the same 50 mm: delta_l = 1.60 mm and the mode
    moves down 6.39 % - both are the "a few percent, downwards" that
    `cavity.py` says it does not apply.

    Why it is logarithmic: in two dimensions the fringing field's far tail
    decays only as 1/r, so the excess charge keeps accumulating with the plate
    aspect ratio. That also means the answer is sensitive to a/h - a small,
    fat plane detunes much more in relative terms than a large, thin one, and
    the closed form is only valid in the large-aspect-ratio limit, which is why
    below a/h = 10 or b/h = 10 this refuses instead of extrapolating.

    Not modelled: the dielectric slab ending at the copper edge (air outside is
    what the fringe sees on part of its path in a real board, which makes this
    delta_l lean high), the corners (the edge is taken as infinitely long), a
    via fence or a split plane (a walled edge gets no extension here), and what
    is soldered across the planes - capacitors detune a real PDN far more than
    the edge does.
    """
    geo, e = _validate_plane(a_mm, b_mm, h_mm, er, m, n)
    if e:
        return e
    a, b, h, er, m, n = geo
    edges, e = _edges(open_edges)
    if e:
        return e
    if not edges:
        return err("no open edges: a fully walled rectangle has no fringing "
                   "field at its edges and no detuning from them", "ARG_RANGE")
    for name, dim in (("a", a), ("b", b)):
        if dim / h < 10.0:
            return err("%s/h = %.3g is below 10: the edge correction used here is "
                       "the large-aspect-ratio limit (C ~ eps0*[l/h + (1/pi)(ln "
                       "(2*pi*l/h) + 1)]) and does not extend to a fat, small "
                       "plane" % (name, dim / h), "MODEL_SCOPE",
                       extra={"aspect_ratio": _sig(dim / h)})

    def dl(dim):
        return h / (2.0 * math.pi) * (math.log(2.0 * math.pi * dim / h) + 1.0)

    dl_x = dl(a)
    dl_y = dl(b)
    nx = len([k for k in edges if k.startswith("x")])
    ny = len([k for k in edges if k.startswith("y")])
    a_eff = a + nx * dl_x
    b_eff = b + ny * dl_y
    f0 = C0 / (2.0 * math.sqrt(er)) * math.hypot(m / a, n / b)
    f_eff = C0 / (2.0 * math.sqrt(er)) * math.hypot(m / a_eff, n / b_eff)
    # first-order form, as an independent path to the same number
    sa = (m / a) ** 2
    sb = (n / b) ** 2
    df_first = -0.5 * (sa * (2.0 * (a_eff - a) / a) + sb * (2.0 * (b_eff - b) / b))
    df_first /= (sa + sb)
    df_exact = f_eff / f0 - 1.0
    rel = (df_exact / df_first - 1.0) if df_first else None
    # The two paths are NOT supposed to agree exactly - the gap between them is
    # the second-order term. For a one-dimensional mode the algebra is exact:
    # f ~ 1/a, so f_eff/f0 = 1/(1+eps) and the first-order form is -eps, hence
    # df_exact/df_first - 1 = -eps/(1+eps) with eps = (a_eff-a)/a. For a general
    # (m,n) it is bounded by max(eps_a, eps_b). Both are asserted in the tests,
    # which is what makes this a check instead of a tolerance.
    eps_a = (a_eff - a) / a
    eps_b = (b_eff - b) / b
    eps_max = max(eps_a, eps_b)

    out = {
        "f_resonance_hz": _sig(f0),
        "f_detuned_hz": _sig(f_eff),
        "delta_f_hz": _sig(f_eff - f0),
        "delta_f_over_f": _sig(df_exact),
        "delta_f_over_f_pct": _sig(100.0 * df_exact),
        "mode": {"m": m, "n": n},
        "open_edges": list(edges),
        "edge_extension_mm": {"x": _sig(dl_x * 1e3), "y": _sig(dl_y * 1e3)},
        "edge_extension_over_h": {"x": _sig(dl_x / h), "y": _sig(dl_y / h)},
        "a_eff_mm": _sig(a_eff * 1e3),
        "b_eff_mm": _sig(b_eff * 1e3),
        "delta_a_mm": _sig((a_eff - a) * 1e3),
        "delta_b_mm": _sig((b_eff - b) * 1e3),
        "aspect_ratio": {"a_over_h": _sig(a / h), "b_over_h": _sig(b / h)},
        "first_order_check": {
            "delta_f_over_f_first_order": _sig(df_first),
            "relative_gap": _sig(rel),
            "gap_bound": _sig(-eps_max) if rel is not None else None,
            "note": "two paths to the same number: the exact detuned f_eff and "
                    "the first-order -(1/2)*dS/S form. They are NOT supposed to "
                    "agree exactly: the relative gap is the second-order term, "
                    "bounded by -(max delta_dim/dim) and equal to "
                    "-eps/(1+eps) for a one-dimensional mode. A gap outside "
                    "that bound means one of the two paths is wrong, not that "
                    "the detuning is small",
        },
        "scaling": {
            "vs_h": "delta_l ~ h*(ln(2*pi*a/h)+1)/(2*pi): thicker dielectric, "
                    "much bigger pull-down",
            "vs_size": "for a fixed h the relative shift FALLS as the plane "
                       "grows (the log grows, a grows faster)",
            "vs_edges": "only open edges get an extension; two open edges in a "
                        "direction add 2*delta_l",
        },
    }
    return ok(out, "dimensionless frequency shift (and Hz)",
              "open-edge detuning of a plane-pair mode from the quasi-static "
              "fringe capacitance of the edge",
              "delta_l = (h/(2*pi))*(ln(2*pi*a/h)+1) per open edge, from C ~ "
              "eps0*[l/h + (1/pi)*(ln(2*pi*l/h)+1)] (Love 1924 exact 2-D "
              "solution in the large-aspect-ratio limit; Yariv 2021 eq. 5.20); "
              "a_eff = a + 2*delta_l, f_eff = c/(2*sqrt(er))*sqrt((m/a_eff)^2 + "
              "(n/b_eff)^2)",
              "leading-order asymptotic in 1/(a/h) with the O(ln) correction kept "
              "and the O(1) coefficient taken from the conformal-mapping "
              "solution; the edge is treated as infinitely long (no corner "
              "term) and the outside is air. Refused below a/h = 10 or b/h = 10",
              {"dielectric": "homogeneous between the plates; the slab is "
                             "assumed to end with the copper, so the real fringe "
                             "field (partly in air) has less capacitance and this "
                             "delta_l leans high",
               "edge": "infinitely long, so the two ends of that edge (the "
                       "corners) are not modelled; the 2-D result is used per "
                       "unit edge length times the edge length",
               "walls": "a closed edge is a perfect wall with no extension; a "
                        "real via fence leaks and detunes too",
               "components": "nothing soldered across the planes"},
              {"source": "E. Yariv, 'Edge corrections for parallel-plate "
                         "capacitors', Euro. J. Appl. Math. 32 (2021) 226-241, "
                         "Open Access, eqs. (1.2), (5.20), (A5)",
               "how_to_use": "feed f_detuned_hz into cavity_plane_q() or "
                             "plane_radiation_q() when the edges are bare; leave "
                             "it out when the edge is stitched to another plane",
               "does_not_prove": "the real mode position of a finished board - "
                                 "the dielectric slab, the caps and the fence "
                                 "each move it by more than the corner terms do"})
