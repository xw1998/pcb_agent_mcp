"""Current carrying capacity.

Two standards disagree and the difference is large, so the caller MUST choose:
  - ipc2221 : chart-fit formula (implemented here, precision limited)
  - ipc2152 : current standard, requires a data table -> fail-closed if absent

Never return a number without telling which standard produced it.
"""
import math

from .result import ok, err

K_EXTERNAL = 0.048   # IPC-2221 chart fit, external layer
K_INTERNAL = 0.024   # IPC-2221 chart fit, internal layer
MM2_PER_MIL2 = 0.00064516
OZ_TO_UM = 34.8      # 1 oz copper ~= 34.8 um finished
UM_PER_MIL = 25.4

VALID_STANDARDS = ("ipc2221", "ipc2152")


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
        return None, err(
            "IPC-2152 requires a loaded standard data table, which is not present yet",
            "STANDARD_DATA_NOT_LOADED",
            hint=(
                "IPC-2152 values come from measured charts (depends on board thickness, "
                "copper weight, layer, adjacent copper, airflow). Do not approximate it "
                "with the IPC-2221 formula - the two disagree by a large margin. "
                "Load data/ipc2152_tables.json and mark it verified to enable this."
            ),
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
