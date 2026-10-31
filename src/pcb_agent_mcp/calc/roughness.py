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

What this is NOT: the Huray "snowball" model (needs nodule radius and density),
any frequency dependence beyond the skin depth, anisotropic roughness, the
profile of a specific foil, or the extra loss where the dielectric has wicked
into the tooth. It is the one-number correction that turns "above ~1 GHz the
copper is rougher than the model assumes" into a number, and it says on every
return that it is a multiplier, not a surface model.
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


def effective_conductivity(sigma_s_m, rq_m, delta_m):
    """sigma_eff = sigma / K^2: the same loss, expressed as a worse conductor.

    This is the cross-check the tool uses: feeding sigma_eff into the smooth
    model must reproduce the rough-copper Qc exactly, because Rs' = Rs*K and
    Rs = 1/(sigma*delta_s) with delta_s ~ 1/sqrt(sigma).
    """
    k = roughness_factor(rq_m, delta_m)
    return float(sigma_s_m) / (k * k)
