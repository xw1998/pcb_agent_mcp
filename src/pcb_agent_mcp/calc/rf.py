"""Radio-frequency geometric rules that are pure arithmetic."""
import math

from .result import ok, err

C0_MM_PER_NS = 299.792458  # speed of light, mm per ns


def lambda_over_20(freq_ghz, er=4.3, divisor=20.0):
    """Maximum stitching-via spacing: lambda/20 in the dielectric."""
    try:
        f = float(freq_ghz)
        er = float(er)
        divisor = float(divisor)
    except (TypeError, ValueError):
        return err("numeric arguments required", "ARG_INVALID")
    if f <= 0 or er <= 0 or divisor <= 0:
        return err("arguments must be > 0", "ARG_RANGE")

    # wavelength in mm = c / (f * sqrt(er)), f in GHz -> c in mm/ns
    lam = C0_MM_PER_NS / (f * math.sqrt(er))
    spacing = lam / divisor
    return ok(
        value=round(spacing, 3),
        unit="mm",
        model="lambda_over_divisor_in_dielectric",
        basis="lambda = c / (f * sqrt(er)); spacing = lambda / divisor (divisor default 20)",
        precision="approximate (er uniformity and copper roughness ignored)",
        assumptions={"freq_ghz": f, "er": er, "divisor": divisor,
                     "wavelength_mm": round(lam, 3)},
    )
