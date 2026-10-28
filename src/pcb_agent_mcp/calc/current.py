"""Current carrying capacity.

Two standards disagree and the difference is large, so the caller MUST choose:
  - ipc2221 : the 1956 NBS chart fit (I = k*dT^0.44*A^0.725), superseded but still
              what most rule-of-thumb tables quote
  - ipc2152 : the 2009 replacement. It publishes charts only, so what is
              implemented here is the published closed-form fit to its Table 3-1
              by Brooks & Adam (2015), coefficients taken verbatim from KiCad
              10.0 open source (common/track_width_calculations.cpp).

Never return a number without telling which standard produced it, and never let
one standard answer a question asked about the other.
"""
import json
import math
import os

from .result import ok, err

K_EXTERNAL = 0.048   # IPC-2221 chart fit, external layer
K_INTERNAL = 0.024   # IPC-2221 chart fit, internal layer
MM2_PER_MIL2 = 0.00064516
OZ_TO_UM = 34.8      # 1 oz copper ~= 34.8 um finished
UM_PER_MIL = 25.4

VALID_STANDARDS = ("ipc2221", "ipc2152")

# --- IPC-2152 closed-form fit: dT = K * I^a * W^b * Th^c  (W, Th in mil, I in A) ---
# Copied verbatim from KiCad 10.0 common/track_width_calculations.cpp, which in turn
# names D. G. Brooks and J. Adam, "Trace Currents and Temperatures Revisited" (2015),
# fitting IPC-2152 Table 3-1. IPC-2152 itself publishes charts, not equations.
BA_EXTERNAL = (215.3, 2.0, -1.15, -1.0)
BA_INTERNAL = (
    (0.5, 0.689, (120.0, 2.0, -1.10, -1.52)),
    (1.0, 1.378, (200.0, 1.9, -1.10, -1.52)),
    (2.0, 2.756, (300.0, 2.0, -1.15, -1.52)),
    (3.0, 4.134, (262.5, 1.9, -1.15, -1.52)),
)
THICKNESS_MIL_PER_OZ = 1.378   # nominal: 1 oz/ft^2 finished ~= 35.0 um

MODEL_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))),
    "data", "ipc2152_tables.json",
)
_model_status = None


def _model_check():
    """Cross-check the coefficients in this module against data/ipc2152_tables.json.

    The constants above are the working copy (the tool must still answer if the data
    file is missing); the JSON is the documented copy. If both exist and disagree, the
    two sources have drifted and either answer would be a guess, so refuse.
    """
    global _model_status
    if _model_status is not None:
        return _model_status
    try:
        with open(MODEL_PATH, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as exc:
        _model_status = ("missing", "model file not readable: %s" % (exc,))
        return _model_status
    model = doc.get("model") or {}
    ext = model.get("external") or {}
    got_ext = (ext.get("K"), ext.get("a"), ext.get("b"), ext.get("c"))
    if tuple(got_ext) != BA_EXTERNAL:
        _model_status = ("drift", "external coefficients differ: file=%r code=%r"
                         % (got_ext, BA_EXTERNAL))
        return _model_status
    rows = model.get("internal") or []
    got_int = tuple((float(r.get("copper_oz")), float(r.get("thickness_mil")),
                     (float(r.get("K")), float(r.get("a")), float(r.get("b")),
                      float(r.get("c")))) for r in rows)
    want_int = tuple((float(o), float(t), tuple(c)) for o, t, c in BA_INTERNAL)
    if got_int != want_int:
        _model_status = ("drift", "internal coefficients differ between file and code")
        return _model_status
    _model_status = ("ok", "%s (verified=%r)" % (doc.get("edition"), doc.get("verified")))
    return _model_status


def _ba_coeffs(layer_n, thickness_mil):
    """Pick the coefficient set the same way KiCad's coefficients() does."""
    if layer_n != "internal":
        return BA_EXTERNAL, "external, all copper weights"
    for i in range(len(BA_INTERNAL) - 1):
        lo = BA_INTERNAL[i][1]
        hi = BA_INTERNAL[i + 1][1]
        if thickness_mil < (lo + hi) / 2.0:
            return BA_INTERNAL[i][2], "internal, %g oz bucket" % BA_INTERNAL[i][0]
    last = BA_INTERNAL[-1]
    return last[2], "internal, %g oz bucket" % last[0]


def _ba_width_mil(current_a, delta_t_c, thickness_mil, coeffs):
    k, a, b, c = coeffs
    return (delta_t_c / (k * current_a ** a * thickness_mil ** c)) ** (1.0 / b)


def _ba_current_a(width_mil, delta_t_c, thickness_mil, coeffs):
    k, a, b, c = coeffs
    return (delta_t_c / (k * width_mil ** b * thickness_mil ** c)) ** (1.0 / a)


def _k(layer):
    layer = str(layer).lower()
    if layer.startswith("ext"):
        return K_EXTERNAL, "external"
    if layer.startswith("int"):
        return K_INTERNAL, "internal"
    return None, None


def _area_mil2(current_a, delta_t_c, k):
    """IPC-2221 chart fit:  I = k * dT^0.44 * A^0.725  ->  A = (I / (k*dT^0.44))^(1/0.725)"""
    return (current_a / (k * delta_t_c ** 0.44)) ** (1.0 / 0.725)


def _current_from_area(area_mil2, delta_t_c, k):
    return k * delta_t_c ** 0.44 * area_mil2 ** 0.725


def _guard_standard(standard):
    std = str(standard or "").strip().lower()
    if std == "":
        return None, err(
            "standard is required",
            "STANDARD_REQUIRED",
            hint=(
                "PCB current capacity differs by more than 3x between standards. "
                "Pass standard='ipc2221' or standard='ipc2152'. "
                "Choosing for the caller would be a wrong answer, not a helpful default."
            ),
        )
    if std not in VALID_STANDARDS:
        return None, err("unknown standard: " + std, "STANDARD_UNKNOWN",
                         hint="supported: " + ", ".join(VALID_STANDARDS))
    if std == "ipc2152":
        state, detail = _model_check()
        if state == "drift":
            return None, err(
                "IPC-2152 model in code and in data/ipc2152_tables.json disagree: " + detail,
                "INTERNAL_CHECK_FAILED",
                hint="Two copies of the same coefficients drifted apart; either one would "
                     "be a guess. Fix the data file or the constants before trusting this.",
            )
    return std, None


def width_for_current(current_a, delta_t_c=10.0, copper_oz=1.0, layer="external", standard=None):
    std, e = _guard_standard(standard)
    if e:
        return e
    k, layer_n = _k(layer)
    if k is None:
        return err("layer must be external or internal", "ARG_INVALID",
                   hint="layer='external' or layer='internal'")
    try:
        current_a = float(current_a)
        delta_t_c = float(delta_t_c)
        copper_oz = float(copper_oz)
    except (TypeError, ValueError):
        return err("numeric arguments required", "ARG_INVALID")
    if current_a <= 0:
        return err("current_a must be > 0", "ARG_RANGE")
    if delta_t_c <= 0:
        return err(
            "delta_t_c must be > 0",
            "ARG_RANGE",
            hint=(
                "Zero temperature rise implies infinite copper. 'No temperature rise' "
                "is not computable - it must be converted into a delta-T budget first."
            ),
        )
    if copper_oz <= 0:
        return err("copper_oz must be > 0", "ARG_RANGE")
    if current_a > 35:
        return err("current_a above 35 A is outside the chart fit validity",
                   "OUT_OF_VALIDITY", hint="use copper pour / bus bar analysis instead")

    if std == "ipc2152":
        thick_mil = copper_oz * THICKNESS_MIL_PER_OZ
        coeffs, bucket = _ba_coeffs(layer_n, thick_mil)
        width_mil = _ba_width_mil(current_a, delta_t_c, thick_mil, coeffs)
        width_mm = width_mil * 0.0254
        state, model_note = _model_check()
        ipc2221_mm = _area_mil2(current_a, delta_t_c, k) / thick_mil * 0.0254
        extra = {
            "coefficient_set": bucket,
            "coeffs": {"K": coeffs[0], "a": coeffs[1], "b": coeffs[2], "c": coeffs[3]},
            "thickness_mil": round(thick_mil, 4),
            "area_mm2": round(width_mil * thick_mil * MM2_PER_MIL2, 6),
            "cross_check_vs_ipc2221": {
                "width_mm": round(ipc2221_mm, 4),
                "difference_pct": round((width_mm - ipc2221_mm) / ipc2221_mm * 100.0, 1),
                "note": "Same question answered by the superseded chart fit. A large gap is "
                        "expected, not an error - do not average them.",
            },
            "model_file": {"state": state, "detail": model_note},
            "fit_drops": ["board thickness", "adjacent copper", "airflow", "mask opening"],
        }
        if copper_oz < 0.5 or copper_oz > 3.0:
            extra["outside_fit_copper_weight"] = (
                "The published fit is tabulated for 0.5/1/2/3 oz. %.2f oz uses the nearest "
                "bucket's coefficients; treat it as an extrapolation." % copper_oz
            )
        return ok(
            value=round(width_mm, 4),
            unit="mm",
            model="ipc2152_brooks_adam_fit",
            basis="dT = K*I^a*W^b*Th^c fitted to IPC-2152 Table 3-1 "
                  "(D. G. Brooks & J. Adam 2015; coefficients from KiCad 10.0)",
            precision="+/-10% typical for the fit to the published data; drops board "
                      "thickness, adjacent copper and airflow, so it is not the standard itself",
            assumptions={
                "current_a": current_a,
                "delta_t_c": delta_t_c,
                "copper_oz": copper_oz,
                "layer": layer_n,
                "mask_opening": False,
                "no_adjacent_copper": True,
                "standard": std,
            },
            extra=extra,
        )

    area_mil2 = _area_mil2(current_a, delta_t_c, k)
    thick_mil = copper_oz * OZ_TO_UM / UM_PER_MIL
    width_mil = area_mil2 / thick_mil
    width_mm = width_mil * 0.0254

    return ok(
        value=round(width_mm, 4),
        unit="mm",
        model="ipc2221_chart_fit",
        basis="IPC-2221 chart fit: I = k*dT^0.44*A^0.725 (k=0.048 external, 0.024 internal)",
        precision="+/-20% (chart-fit approximation, not measured data)",
        assumptions={
            "current_a": current_a,
            "delta_t_c": delta_t_c,
            "copper_oz": copper_oz,
            "layer": layer_n,
            "mask_opening": False,
            "no_adjacent_copper": True,
            "standard": std,
        },
        extra={
            "area_mm2": round(area_mil2 * MM2_PER_MIL2, 6),
            "cross_check_rule_of_thumb": {
                "note": "A widely circulated '1A per 1mm at 1oz/10C' rule is ~3.3x more conservative than this result.",
                "why_it_matters": "Do not mix the two in one design review.",
            },
            "standard_choice_warning": (
                "IPC-2152 supersedes this chart fit and is generally less conservative "
                "for external traces. Same question, different answer."
            ),
        },
    )


def current_capacity(width_mm, delta_t_c=10.0, copper_oz=1.0, layer="external",
                    mask_opening=False, standard=None):
    std, e = _guard_standard(standard)
    if e:
        return e
    k, layer_n = _k(layer)
    if k is None:
        return err("layer must be external or internal", "ARG_INVALID")
    try:
        width_mm = float(width_mm)
        delta_t_c = float(delta_t_c)
        copper_oz = float(copper_oz)
    except (TypeError, ValueError):
        return err("numeric arguments required", "ARG_INVALID")
    if width_mm <= 0 or delta_t_c <= 0 or copper_oz <= 0:
        return err("width, delta_t and copper_oz must be > 0", "ARG_RANGE")

    if std == "ipc2152":
        thick_mil = copper_oz * THICKNESS_MIL_PER_OZ
        coeffs, bucket = _ba_coeffs(layer_n, thick_mil)
        width_mil = width_mm / 0.0254
        current = _ba_current_a(width_mil, delta_t_c, thick_mil, coeffs)
        state, model_note = _model_check()
        ipc2221_a = _current_from_area(width_mil * thick_mil, delta_t_c, k)
        extra = {
            "coefficient_set": bucket,
            "coeffs": {"K": coeffs[0], "a": coeffs[1], "b": coeffs[2], "c": coeffs[3]},
            "thickness_mil": round(thick_mil, 4),
            "cross_check_vs_ipc2221": {
                "current_a": round(ipc2221_a, 3),
                "difference_pct": round((current - ipc2221_a) / ipc2221_a * 100.0, 1),
                "note": "The superseded chart fit on the same trace. Do not average them.",
            },
            "model_file": {"state": state, "detail": model_note},
            "fit_drops": ["board thickness", "adjacent copper", "airflow", "mask opening"],
        }
        if mask_opening:
            extra["mask_opening_note"] = (
                "Solder-mask opening with added solder increases effective copper+tin "
                "cross-section. The fit has no term for it; treat this value as a lower bound."
            )
        if copper_oz < 0.5 or copper_oz > 3.0:
            extra["outside_fit_copper_weight"] = (
                "The published fit is tabulated for 0.5/1/2/3 oz. %.2f oz uses the nearest "
                "bucket's coefficients; treat it as an extrapolation." % copper_oz
            )
        return ok(
            value=round(current, 3),
            unit="A",
            model="ipc2152_brooks_adam_fit",
            basis="dT = K*I^a*W^b*Th^c fitted to IPC-2152 Table 3-1 "
                  "(D. G. Brooks & J. Adam 2015; coefficients from KiCad 10.0)",
            precision="+/-10% typical for the fit to the published data; drops board "
                      "thickness, adjacent copper and airflow, so it is not the standard itself",
            assumptions={
                "width_mm": width_mm,
                "delta_t_c": delta_t_c,
                "copper_oz": copper_oz,
                "layer": layer_n,
                "mask_opening": bool(mask_opening),
                "standard": std,
            },
            extra=extra,
        )

    thick_mil = copper_oz * OZ_TO_UM / UM_PER_MIL
    area_mil2 = (width_mm / 0.0254) * thick_mil
    current = _current_from_area(area_mil2, delta_t_c, k)

    extra = {}
    if mask_opening:
        extra["mask_opening_note"] = (
            "Solder-mask opening with added solder increases effective copper+tin "
            "cross-section. This tool does NOT add that margin; treat the value as a "
            "lower bound when a mask opening is present."
        )
    return ok(
        value=round(current, 3),
        unit="A",
        model="ipc2221_chart_fit",
        basis="IPC-2221 chart fit: I = k*dT^0.44*A^0.725",
        precision="+/-20%",
        assumptions={
            "width_mm": width_mm,
            "delta_t_c": delta_t_c,
            "copper_oz": copper_oz,
            "layer": layer_n,
            "mask_opening": bool(mask_opening),
            "standard": std,
        },
        extra=extra,
    )


def via_current_allowed(hole_mm, plating_um=25.0, count=1, delta_t_c=10.0, standard=None):
    """Barrel sidewall area treated with the same chart fit.

    This is an engineering extrapolation, not a standard-defined method:
    IPC-2221 charts describe flat conductors, not plated barrels.
    """
    std, e = _guard_standard(standard)
    if e:
        return e
    if std == "ipc2152":
        return err(
            "IPC-2152 does not describe plated through holes",
            "STANDARD_SCOPE",
            hint=("The 2152 data is flat-conductor temperature rise; a barrel is embedded in "
                  "laminate and has a different thermal path. Use standard='ipc2221' only as a "
                  "flagged extrapolation, or size the via from plating current density."),
        )
    try:
        hole_mm = float(hole_mm)
        plating_um = float(plating_um)
        count = int(count)
        delta_t_c = float(delta_t_c)
    except (TypeError, ValueError):
        return err("numeric arguments required", "ARG_INVALID")
    if hole_mm <= 0 or plating_um <= 0 or count <= 0 or delta_t_c <= 0:
        return err("all arguments must be > 0", "ARG_RANGE")

    # barrel sidewall area = pi * d * t
    area_mm2 = math.pi * hole_mm * (plating_um / 1000.0)
    area_mil2 = area_mm2 / MM2_PER_MIL2
    one = _current_from_area(area_mil2, delta_t_c, K_EXTERNAL)
    total = one * count

    return ok(
        value=round(total, 3),
        unit="A",
        model="ipc2221_chart_fit_on_barrel_sidewall",
        basis="Engineering extrapolation: barrel sidewall area pi*d*t fed to IPC-2221 chart fit",
        precision="+/-30% (extrapolation; barrel is embedded in laminate, thermal path differs from a flat trace)",
        assumptions={
            "hole_mm": hole_mm,
            "plating_um": plating_um,
            "count": count,
            "delta_t_c": delta_t_c,
            "barrel_area_mm2": round(area_mm2, 6),
            "standard": std,
            "not_standard_defined": True,
        },
        extra={
            "per_via_a": round(one, 3),
            "rule_of_thumb_gap": (
                "Common practice quotes about 1 A for a 0.3 mm via with 25 um plating; "
                "this extrapolation is more optimistic. Prefer the conservative value "
                "unless you can measure."
            ),
        },
    )
