"""Surface roughness -> conductor-loss multiplier (Hammerstad-Jensen).

A rough copper surface makes the RF current follow a longer path than the
geometric one, so the surface resistance rises above the smooth value. The
first-order model used across microwave CAD is the arctan form published by
Hammerstad and Jensen (1980):

    K = 1 + (2/pi) * atan(1.4 * (Rq/delta_s)^2)

with Rq the RMS roughness and delta_s the skin depth at the evaluation
frequency. It MULTIPLIES Rs, which is the same as reducing the effective
conductivity (sigma_eff = sigma / K^2, because Rs = 1/(sigma*delta_s) and
delta_s ~ 1/sqrt(sigma)). Its properties, which the tests pin down:

    K(0)     = 1        (smooth copper, and this is why the argument is
                         optional: without it nothing about the old number moves)
    K(delta) = 1.60514  (one skin depth of roughness already costs 60 %)
    K(2del)  = 1.88750
    K(inf)   = 2        (from below, never above: this form cannot make the
                         current travel more than twice the flat path)

Why RMS and not Rz: the model's input is the RMS height Rq. Foil datasheets
usually quote Rz (peak-to-valley), and there is no single conversion factor
between them - it depends on the height distribution - so the API takes Rq and
says so instead of pretending to convert. HVLP foils are Rq ~ 0.3..1 um;
standard electrodeposited foil is several um.

What this is NOT: any frequency dependence beyond the skin depth, anisotropic
roughness, the profile of a specific foil, or the extra loss where the
dielectric has wicked into the tooth. It is the one-number correction that
turns "above ~1 GHz the copper is rougher than the model assumes" into a
number, and it says on every return that it is a multiplier, not a surface
model.

The Huray "snowball" model IS here - see `huray_transition`, `huray_surface_ratio`
and `huray_loss_factor` below - but it takes a different set of inputs (nodule
radius + surface ratio, not Rq), so it is a separate call rather than a flag on
`roughness_factor`: the two parameterisations are not interconvertible without
inventing a distribution (see `huray_loss_factor`'s docstring).

Source for the Huray form used here: Yu. Shlepnev, "Unified approach to
interconnect conductor surface roughness modelling", EPEPS 2017 (free PDF at
simberian.com) - eqs. (1), (4), (5), which is also where the identity between
the causal transition function and the original Huray transition function is
stated, and where RF = 1 + (3/2)*sr is given.
"""
import math

MU0 = 4.0e-7 * math.pi


def skin_depth_m(f_hz, sigma_s_m):
    """Classical skin depth sqrt(1/(pi*f*mu0*sigma)), in metres."""
    return math.sqrt(1.0 / (math.pi * float(f_hz) * MU0 * float(sigma_s_m)))


def roughness_factor(rq_m, delta_m):
    """K >= 1 multiplying Rs (Hammerstad-Jensen); 1.0 for a smooth surface.

    rq_m may be None or 0 (smooth); delta_m <= 0 (no skin depth to compare
    against) also returns 1.0 rather than a made-up number.
    """
    if rq_m is None or delta_m is None:
        return 1.0
    try:
        rq = float(rq_m)
        ds = float(delta_m)
    except (TypeError, ValueError):
        return 1.0
    if rq <= 0.0 or ds <= 0.0:
        return 1.0
    u = rq / ds
    return 1.0 + (2.0 / math.pi) * math.atan(1.4 * u * u)


def huray_surface_ratio(nodule_radius_m, nodule_count_per_m2):
    """Hall-Huray surface ratio sr = 4*pi*r^2*N (dimensionless).

    N is the areal density of hemispherical nodules (per m^2) and r their
    radius. sr is the ratio of the nodules' total hemispherical surface area to
    the flat area they sit on, which is why a flat surface has sr = 0.

    Returns None for a non-positive radius or count (nothing to describe).
    """
    try:
        r = float(nodule_radius_m)
        n = float(nodule_count_per_m2)
    except (TypeError, ValueError):
        return None
    if r <= 0.0 or n < 0.0:
        return None
    return 4.0 * math.pi * r * r * n


def huray_transition(nodule_radius_m, delta_m):
    """Huray's transition function F(x) = 1/sqrt(1 + 2x + 2x^2), x = 2*delta/r.

    This is the magnitude of the causal form
    F = [1 + (1 - j)*2*delta/r]^(-1/2), and Shlepnev's paper states that its
    modulus produces exactly the same loss increase as the original Huray
    model, i.e. it is the same physics written so that it decays at low
    frequency instead of growing without bound.

    Two limits are what the tests pin down, because they are the whole reason
    the model is usable:
        delta -> 0 (x -> 0):  F -> 1   =>  K -> RF   (roughness at its worst)
        delta -> inf (x -> inf): F -> 0 =>  K -> 1    (no roughness effect)
    x = 1 (delta = r/2) gives F = 1/sqrt(5) = 0.4472136 exactly, which is the
    anchor value: a nodule radius equal to twice the skin depth is the knee.
    """
    try:
        r = float(nodule_radius_m)
        ds = float(delta_m)
    except (TypeError, ValueError):
        return None
    if r <= 0.0 or ds <= 0.0:
        return None
    x = 2.0 * ds / r
    return 1.0 / math.sqrt(1.0 + 2.0 * x + 2.0 * x * x)


def huray_loss_factor(delta_m, nodule_radius_m, surface_ratio):
    """K_multiplier = 1 + (RF - 1)*F  (Huray, in Shlepnev's unified form).

        RF = 1 + (3/2)*sr      nodules at DC: the current sees their area
        F  = huray_transition  how much of that extra area the skin depth
                               actually resolves at this frequency

    K is the same kind of number as `roughness_factor` returns (it multiplies
    Rs), but it is NOT the same surface model and the two are not
    interconvertible: Rq is a height statistic, (r, sr) is a nodule geometry,
    and many surfaces share one Rq with different (r, sr). This module refuses
    to convert between them rather than pick a distribution.

    Returns 1.0 for a smooth surface (sr = 0 or r = 0), and None for inputs it
    cannot use.
    """
    try:
        ds = float(delta_m)
        r = float(nodule_radius_m)
        sr = float(surface_ratio)
    except (TypeError, ValueError):
        return None
    if ds <= 0.0 or r < 0.0 or sr < 0.0:
        return None
    f = huray_transition(r, ds)
    if f is None:
        return 1.0 if sr == 0.0 else None
    return 1.0 + 1.5 * sr * f


def effective_conductivity(sigma_s_m, rq_m, delta_m):
    """sigma_eff = sigma / K^2: the same loss, expressed as a worse conductor.

    This is the cross-check the tool uses: feeding sigma_eff into the smooth
    model must reproduce the rough-copper Qc exactly, because Rs' = Rs*K and
    Rs = 1/(sigma*delta_s) with delta_s ~ 1/sqrt(sigma).
    """
    k = roughness_factor(rq_m, delta_m)
    return float(sigma_s_m) / (k * k)
