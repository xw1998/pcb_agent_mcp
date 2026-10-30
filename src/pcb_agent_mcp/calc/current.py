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

# --- Onderdonk (1928): the adiabatic fusing relation, in copper's own constants ---
#     I = A_cmil * sqrt( log10((Tm - Ta)/(234 + Ta) + 1) / (33 * t) )
# A in circular mils, t in seconds, temperatures in degC. 1083 is copper's melting
# point, 234 its reciprocal temperature coefficient (234.5) - the SAME 234.5 that
# shows up in the resistance-vs-temperature formula - and 33 folds in density,
# specific heat and the room-temperature resistivity. All three constants are
# copper's; for any other metal they change and this module does not own them.
ONDERDONK_MELT_C = 1083.0
ONDERDONK_REF_C = 234.0
ONDERDONK_K = 33.0
CMIL_PER_MIL2 = 4.0 / math.pi    # 1 cmil = pi/4 mil^2, so area_mil2 -> cmil is *4/pi
ONDERDONK_SHORT_PULSE_S = 10.0   # beyond this the adiabatic assumption is a lie

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

def fusing_current(width_mm, t_s=1.0, copper_oz=1.0, thickness_um=None,
                   t_ambient_c=25.0, delta_t_c=10.0, layer="external",
                   standard=None):
    """Current that melts a trace in t_s seconds, and how far that is from the rating.

    The question "what current fuses this trace" is NOT a steady-state question and
    neither standard answers it: IPC-2221 and IPC-2152 both describe a temperature
    RISE with cooling to the board and to the air, and cooling is precisely what is
    deleted in a fusing calculation. So this uses the relation everyone's fusing
    table actually quotes:

        Onderdonk (1928), adiabatic: I = A_cmil * sqrt(ln10( (Tm-Ta)/(234+Ta) + 1 )
                                                       / (33 * t))

    with A in circular mils, t in seconds, Ta ambient, Tm = 1083 C, and the 234/33
    being copper's constants. Everything the formula means:

      * ADIABATIC: every watt of I^2R stays in the conductor. No board, no air, no
        radiation. Real fusing takes MORE current than this because heat escapes -
        so the answer is on the safe (low) side for a pulse, and increasingly
        meaningless as t grows. Beyond ~10 s it is not a fusing time, it is a
        statement that nothing cools.
      * melt POINT, not molten: copper reaching 1083 C, without the latent heat of
        fusion. Blowing apart takes a little more.
      * the resistivity rise IS inside it (that is where 234 comes from), otherwise
        the number would be 3x optimistic.
      * Onderdonk was fitted to round wire. A rectangular trace is converted by
        EQUAL AREA (mil^2 -> cmil), which is an extrapolation: a thin wide trace has
        more surface per unit area than a round wire, so it really cools better and
        needs more current. Same direction as the adiabatic error, same caveat.
      * one pulse, no repeats: at 1 ms the same formula ignores skin effect, which
        for anything above ~100 kHz makes the copper effectively thinner.

    `standard` (optional) is used ONLY to say how far the fusing current is from the
    continuous rating, and that half of the answer names the standard it came from.
    Without it you still get the fusing current; you just do not get the comparison.
    """
    try:
        width_mm = float(width_mm)
        t_s = float(t_s)
        copper_oz = float(copper_oz)
        t_ambient_c = float(t_ambient_c)
        delta_t_c = float(delta_t_c)
    except (TypeError, ValueError):
        return err("numeric arguments required", "ARG_INVALID")
    if thickness_um is None:
        thick_um = copper_oz * OZ_TO_UM
    else:
        try:
            thick_um = float(thickness_um)
        except (TypeError, ValueError):
            return err("thickness_um must be numeric", "ARG_INVALID")
    _, layer_n = _k(layer)
    if layer_n is None:
        return err("layer must be external or internal", "ARG_INVALID")
    if width_mm <= 0 or thick_um <= 0:
        return err("width_mm and copper thickness must be > 0", "ARG_RANGE")
    if t_s <= 0:
        return err("t_s must be > 0", "ARG_RANGE",
                   hint="this is a time-to-melt question; a steady-state current "
                        "does not fuse anything in the adiabatic model")
    if t_ambient_c <= -ONDERDONK_REF_C:
        return err("t_ambient_c must be above -234 C (copper's resistance "
                   "reference temperature)", "ARG_RANGE")
    if t_ambient_c >= ONDERDONK_MELT_C:
        return err("t_ambient_c >= copper's melting point: nothing to compute",
                   "ARG_RANGE")

    # mm -> mil is /0.0254 ; um -> mil is /25.4 (UM_PER_MIL). Mixing these two up
    # costs a factor of 1000 and a 0.003 A "fusing current", so both are spelled out.
    area_mil2 = (width_mm / 0.0254) * (thick_um / UM_PER_MIL)
    area_cmil = area_mil2 * CMIL_PER_MIL2
    ratio = (ONDERDONK_MELT_C - t_ambient_c) / (ONDERDONK_REF_C + t_ambient_c) + 1.0
    i_fuse = area_cmil * math.sqrt(math.log10(ratio) / (ONDERDONK_K * t_s))

    out = {
        "fusing_current_a": round(i_fuse, 3),
        "time_s": round(t_s, 6),
        "width_mm": round(width_mm, 5),
        "thickness_um": round(thick_um, 3),
        "copper_oz": round(thick_um / OZ_TO_UM, 3),
        "t_ambient_c": round(t_ambient_c, 2),
        "area_mil2": round(area_mil2, 4),
        "area_cmil": round(area_cmil, 4),
        "melt_c": ONDERDONK_MELT_C,
        "resistance_ratio_at_melt": round(ratio, 4),
        "adiabatic": True,
        "copper_only": True,
    }
    if t_s > ONDERDONK_SHORT_PULSE_S:
        out["adiabatic_assumption"] = (
            "VIOLATED at t = %.3g s: after ~10 s the cooling the model deletes is the "
            "dominant term, so this is not a fusing time any more. Use it as 'the "
            "energy the copper can absorb', not as a rating." % t_s)
    else:
        out["adiabatic_assumption"] = (
            "short-pulse regime (t <= %g s): heat is treated as fully trapped, so the "
            "real fusing current is somewhat HIGHER than this" % ONDERDONK_SHORT_PULSE_S)
    if thick_um / OZ_TO_UM > 3.0 or thick_um / OZ_TO_UM < 0.5:
        out["outside_usual_copper"] = (
            "Onderdonk does not care about copper weight, but %.2f oz is outside the "
            "0.5-3 oz range the neighbouring rating tools are fitted on"
            % (thick_um / OZ_TO_UM))

    if standard is None:
        out["steady_rating"] = {
            "computed": False,
            "why": "no standard was supplied, and the continuous rating depends "
                   "entirely on which one you mean",
            "how": "pass standard='ipc2152' (or 'ipc2221') to get capacity_a, "
                   "fuse_over_capacity and the adiabatic melt time at that rating",
        }
    else:
        std, e = _guard_standard(standard)
        if e:
            return e
        cap = current_capacity(width_mm, delta_t_c, copper_oz, layer_n, False, std)
        if cap.get("status") != "ok":
            return cap
        cap_a = cap["value"]
        melt_at_cap_s = (math.log10(ratio) /
                         (ONDERDONK_K * (cap_a / area_cmil) ** 2))
        out["steady_rating"] = {
            "computed": True,
            "standard": std,
            "delta_t_c": round(delta_t_c, 3),
            "capacity_a": cap_a,
            "fuse_over_capacity": round(i_fuse / cap_a, 3),
            "adiabatic_melt_time_at_capacity_s": round(melt_at_cap_s, 6),
            "note": "the two numbers answer different questions: capacity_a is a "
                    "cooled steady state by %s, fusing_current_a is an uncooled "
                    "adiabatic melt. Their ratio is how much surge headroom the "
                    "copper has, not a safety margin" % std,
        }

    return ok(
        value=round(i_fuse, 3),
        unit="A",
        model="onderdonk_adiabatic_fusing",
        basis="Onderdonk (1928): I = A_cmil * sqrt(log10((1083 - Ta)/(234 + Ta) + 1)"
              " / (33*t)), A in circular mils, t in seconds; copper's constants",
        precision="+/-30% at best and one-sided: adiabatic (no cooling), stops at the "
                  "melt point (no latent heat), equal-area conversion from a rectangle "
                  "to a round wire. Every one of those makes the real fusing current "
                  "HIGHER, so treat this as the lowest current that can melt it in t",
        assumptions={
            "material": "copper (1083 C, 234 C reference, k = 33) - other metals are "
                        "not covered",
            "cooling": "none (adiabatic)",
            "phase_change": "not modelled (melt point only)",
            "waveform": "constant current for t seconds; a pulse of the same RMS is "
                        "not equivalent once skin effect matters",
            "geometry": "rectangle converted to circular mils by equal area",
            "standard": standard,
        },
        extra=out,
    )

# ---------------------------------------------------------------------------
# Repetitive pulses: one shot is a fusing question, a TRAIN is a cooling question
# ---------------------------------------------------------------------------
# fusing_current deletes cooling on purpose - it is a fusing question, and cooling
# is what a fusing calculation removes. The moment the question becomes "a pulse
# train", cooling stops being negligible: between shots the copper gives heat back,
# and whether the train melts the trace depends on the ratio of the cooling time
# constant to the period, not on the pulse alone. So the same copper needs a second
# model, and this one has no fitting constants at all:
#
#   C' dT/dt = I^2 rho'(T) - h P (T - Ta)                    [per unit length]
#
#   rho'(T) = rho20 (234 + T)/(234 + 20)   copper's own law, the SAME 234 as
#                                          Onderdonk -> the adiabatic limit of
#                                          this model reproduces Onderdonk
#   C'      = rho_m c_p (w t)              [J/(m K)]
#   h P     = h 2(w + t)                   [W/(m K)], P = perimeter
#
# Linear in T inside every interval, so it is exact rather than a step:
#
#   d(dT)/dt = A (1 + beta dT) - dT/tau = A + k dT,  A = I^2 rho'(Ta)/(area C')
#                                                    beta = 1/(234 + Ta)
#                                                    tau  = C'/(h P)
#                                                    k    = A beta - 1/tau
#   dT(t) = (dT0 + A/k) exp(k t) - A/k
#
# One shot is that; a train is a two-branch map (rise for t_on, decay for t_off)
# whose fixed point is a geometric series. k > 0 means the copper heats faster
# than it can shed heat, the map grows without bound, and the only answer left is
# how many shots it takes.
RHO20_CU = 1.72e-8     # ohm*m at 20 C
RHO_MASS_CU = 8960.0   # kg/m^3
CP_CU = 385.0          # J/(kg*K) at 20 C; rises to ~0.44 by 1000 C (not modelled)
T_REF_C = 20.0
TRAIN_NEVER = 1e12     # "more shots than this" is reported as survives-indefinitely


def _cu_resistivity(t_c):
    """Copper's resistance law written with the same 234 that Onderdonk uses."""
    return RHO20_CU * (ONDERDONK_REF_C + t_c) / (ONDERDONK_REF_C + T_REF_C)


def _rise(dt0, a, k, t):
    """Exact solution of d(dT)/dt = a + k*dT over t, from dt0."""
    if abs(k * t) < 1e-12:
        return dt0 + a * t
    if k * t > 700.0:
        return float("inf")
    return (dt0 + a / k) * math.exp(k * t) - a / k


def _train(a, beta, tau, t_on, t_off, dt_melt):
    """Steady state of the pulse train and the number of shots to melt.

    `a`, `k` and `tau` are the per-interval ODE coefficients; `dt_melt` is the
    rise to copper's melting point. Everything is closed form; `_brute_train`
    is the same map stepped cycle by cycle, used as the internal check.
    """
    k = a * beta - 1.0 / tau
    kt = k * t_on
    e_on = float("inf") if kt > 700.0 else math.exp(kt)
    e_off = math.exp(-t_off / tau)
    if abs(k) < 1e-300:
        b = a * t_on * e_off
    else:
        b = (a / k) * (e_on - 1.0) * e_off
    q = e_on * e_off
    out = {"k": k, "q": q, "b": b, "e_on": e_on, "e_off": e_off,
           "steady_trough": None, "steady_peak": None, "shots": None}
    if q < 1.0:
        out["steady_trough"] = b / (1.0 - q)
        out["steady_peak"] = _rise(out["steady_trough"], a, k, t_on)
    # shots to melt: the peak of shot n uses the trough after n-1 shots, and the
    # trough after m shots is b (q^m - 1)/(q - 1) - the same expression whether the
    # series converges or diverges, which is why one formula covers both.
    if e_on == float("inf"):
        out["shots"] = 1
        return out
    if abs(k) < 1e-300:
        u = dt_melt - a * t_on
    else:
        u = (dt_melt + a / k) / e_on - a / k
    if u <= 0.0:
        out["shots"] = 1
        return out
    if abs(q - 1.0) < 1e-12:
        m = (u / b) if b > 0 else float("inf")
    else:
        arg = 1.0 + u * (q - 1.0) / b
        if arg <= 0.0:
            # q < 1 and the target sits above the fixed point: it never gets there
            m = float("inf")
        elif arg == 1.0:
            m = 0.0
        else:
            m = math.log(arg) / math.log(q)
            if m < 0.0:
                m = 0.0
    if m == float("inf"):
        out["shots"] = None              # survives indefinitely
        return out
    shots = int(math.ceil(m - 1e-9)) + 1
    out["shots"] = max(1, shots)
    return out


def _brute_train(a, beta, tau, t_on, t_off, cycles=3):
    """The same map, stepped cycle by cycle - the internal check on the algebra."""
    k = a * beta - 1.0 / tau
    e_off = math.exp(-t_off / tau)
    trough = 0.0
    peak = 0.0
    for _ in range(cycles):
        peak = _rise(trough, a, k, t_on)
        trough = peak * e_off
    return trough, peak


def repetitive_pulse_rating(width_mm, t_on_s, duty_cycle=None, period_s=None,
                            copper_oz=1.0, thickness_um=None, t_ambient_c=25.0,
                            h_w_m2k=None, current_a=None, delta_t_c=10.0,
                            layer="external", standard=None):
    """Largest current a trace can carry as a repeating pulse train, forever.

    The single number is the current at which the train stops being able to shed
    its heat: below it the peak temperature settles, above it every cycle ends
    hotter than the last one started and the trace melts after a finite number of
    shots no matter how long you wait. Give `current_a` as well and the same call
    answers the other direction - "will THESE pulses survive, and for how many".

    Cooling is not optional and it is not guessed: h_w_m2k is the effective heat
    transfer coefficient of your stack-up (one number, and it can differ 20x
    between a bare board in still air and a copper pour bolted to metal), so this
    function refuses to invent one. h = 0 is the adiabatic limit and is allowed.
    """
    try:
        width_mm = float(width_mm)
        t_on_s = float(t_on_s)
        copper_oz = float(copper_oz)
        t_ambient_c = float(t_ambient_c)
        delta_t_c = float(delta_t_c)
    except (TypeError, ValueError):
        return err("numeric arguments required", "ARG_INVALID")
    if thickness_um is None:
        thick_um = copper_oz * OZ_TO_UM
    else:
        try:
            thick_um = float(thickness_um)
        except (TypeError, ValueError):
            return err("thickness_um must be numeric", "ARG_INVALID")
    cur = None
    if current_a is not None:
        try:
            cur = float(current_a)
        except (TypeError, ValueError):
            return err("current_a must be numeric", "ARG_INVALID")
    h = None
    if h_w_m2k is not None:
        try:
            h = float(h_w_m2k)
        except (TypeError, ValueError):
            return err("h_w_m2k must be numeric", "ARG_INVALID")

    _, layer_n = _k(layer)
    if layer_n is None:
        return err("layer must be external or internal", "ARG_INVALID")
    if width_mm <= 0 or thick_um <= 0:
        return err("width_mm and copper thickness must be > 0", "ARG_RANGE")
    if t_on_s <= 0:
        return err("t_on_s must be > 0", "ARG_RANGE",
                   hint="a pulse has a length; 0 is the continuous case, which "
                        "current_capacity answers")
    if cur is not None and cur <= 0:
        return err("current_a must be > 0", "ARG_RANGE")
    if h is None:
        return err("h_w_m2k is required", "ARG_INVALID",
                   hint="cooling is the whole answer for a pulse train: the peak "
                        "temperature settles or runs away depending on how much "
                        "heat leaves the copper between shots. No default is given "
                        "because it ranges from ~10 W/(m^2*K) (bare trace, still "
                        "air) to hundreds (copper pour, forced air, metal chassis). "
                        "Pass 0 for the adiabatic limit (no cooling at all).")
    if h < 0:
        return err("h_w_m2k must be >= 0 (0 = no cooling)", "ARG_RANGE")
    if t_ambient_c <= -ONDERDONK_REF_C:
        return err("t_ambient_c must be above -234 C", "ARG_RANGE")
    if t_ambient_c >= ONDERDONK_MELT_C:
        return err("t_ambient_c >= copper's melting point: nothing to compute",
                   "ARG_RANGE")

    # --- timing: duty_cycle and period_s are two spellings of the same thing ---
    duty = None
    try:
        if duty_cycle is not None:
            duty = float(duty_cycle)
        per = None if period_s is None else float(period_s)
    except (TypeError, ValueError):
        return err("duty_cycle and period_s must be numeric", "ARG_INVALID")
    if duty is None and per is None:
        return err("give duty_cycle or period_s", "ARG_INVALID",
                   hint="the train needs a repetition rate: duty_cycle = t_on/"
                        "(t_on + t_off) in (0, 1], or period_s = t_on + t_off")
    if per is not None:
        if per <= 0:
            return err("period_s must be > 0", "ARG_RANGE")
        if per < t_on_s:
            return err("period_s is shorter than t_on_s: the pulse never ends",
                       "ARG_RANGE",
                       extra={"period_s": per, "t_on_s": t_on_s})
        d_from_period = t_on_s / per
        if duty is not None and abs(duty - d_from_period) > 1e-6 * max(duty, d_from_period):
            return err("duty_cycle and period_s disagree", "ARG_RANGE",
                       extra={"duty_cycle": duty,
                              "t_on_s_over_period_s": round(d_from_period, 9),
                              "period_s": per, "t_on_s": t_on_s},
                       hint="t_on/period and duty_cycle must be the same number; "
                            "they are two spellings of one repetition rate")
        duty = d_from_period
    if duty <= 0 or duty > 1:
        return err("duty_cycle must be in (0, 1]", "ARG_RANGE")
    t_off_s = t_on_s * (1.0 - duty) / duty

    # --- the model ---
    area = (width_mm / 1000.0) * (thick_um / 1e6)          # m^2
    perim = 2.0 * (width_mm / 1000.0 + thick_um / 1e6)     # m
    rho_amb = _cu_resistivity(t_ambient_c)                 # ohm*m
    beta = 1.0 / (ONDERDONK_REF_C + t_ambient_c)           # 1/K
    c_line = RHO_MASS_CU * CP_CU * area                    # J/(m*K)
    g_line = h * perim                                     # W/(m*K)
    tau = (c_line / g_line) if g_line > 0 else float("inf")
    dt_melt = ONDERDONK_MELT_C - t_ambient_c               # K

    # The runaway threshold, in closed form: k*t_on < t_off/tau is
    #   I^2 < area h P /(D beta rho'(Ta))   with D = t_on/(t_on+t_off)
    # Note it carries no heat capacity: this is a balance of rates, not of energy.
    i_crit = math.sqrt(area * g_line / (duty * beta * rho_amb)) if g_line > 0 else 0.0

    # single pulse that would melt it - the number the train is derated against
    area_mil2 = (width_mm / 0.0254) * (thick_um / UM_PER_MIL)
    area_cmil = area_mil2 * CMIL_PER_MIL2
    ratio = (ONDERDONK_MELT_C - t_ambient_c) / (ONDERDONK_REF_C + t_ambient_c) + 1.0
    i_fuse_once = area_cmil * math.sqrt(math.log10(ratio) / (ONDERDONK_K * t_on_s))
    if i_fuse_once <= 0:
        return err("the single-pulse fusing current came out non-positive",
                   "INTERNAL_CHECK_FAILED")

    # --- gate 1: the adiabatic limit must BE Onderdonk, not nearly it ---
    # Our model with no cooling melts in ln(ratio)/(A beta). Decoding Onderdonk's
    # 33 into rho, rho_m and c_p has to land on the same time, otherwise one of the
    # two constants is wrong and neither answer is trustworthy.
    a_once = i_fuse_once ** 2 * rho_amb / (area * c_line)
    t_melt_ours = math.log(ratio) / (a_once * beta)
    gate_adiabatic_pct = 100.0 * (t_melt_ours / t_on_s - 1.0)
    if abs(gate_adiabatic_pct) > 2.0:
        return err(
            "adiabatic limit does not reproduce Onderdonk: %.3f %% off (%.6g s vs "
            "%.6g s)" % (gate_adiabatic_pct, t_melt_ours, t_on_s),
            "INTERNAL_CHECK_FAILED",
            hint="The two routes are the same physics with different constants. If "
                 "they disagree, rho20 / rho_m / c_p drifted away from copper, and "
                 "the pulse-train answer would be built on them.",
            extra={"adiabatic_melt_time_s": round(t_melt_ours, 9),
                   "onderdonk_time_s": t_on_s,
                   "adiabatic_off_pct": round(gate_adiabatic_pct, 4)},
        )

    out = {
        "width_mm": round(width_mm, 5),
        "thickness_um": round(thick_um, 3),
        "copper_oz": round(thick_um / OZ_TO_UM, 3),
        "area_mm2": round(area * 1e6, 8),
        "perimeter_mm": round(perim * 1000.0, 5),
        "t_on_s": round(t_on_s, 9),
        "t_off_s": round(t_off_s, 9),
        "duty_cycle": round(duty, 6),
        "period_s": round(t_on_s + t_off_s, 9),
        "t_ambient_c": round(t_ambient_c, 2),
        "h_w_m2k": round(h, 6),
        "thermal_time_constant_s": (None if tau == float("inf") else round(tau, 9)),
        "time_constant_over_period": (None if tau == float("inf")
                                      else round(tau / (t_on_s + t_off_s), 6)),
        "max_repetitive_current_a": round(i_crit, 6),
        "single_pulse_fusing_current_a": round(i_fuse_once, 6),
        "derating_vs_single_pulse": (round(i_crit / i_fuse_once, 6)
                                     if i_fuse_once > 0 else None),
        "runaway_rule": "melts when I^2 > area*h*P / (D*beta*rho(Ta)); the threshold "
                        "does not involve heat capacity - it is a balance of rates",
        "adiabatic_limit_reproduced_pct": round(gate_adiabatic_pct, 4),
        "melts_at_c": ONDERDONK_MELT_C,
    }
    if tau == float("inf"):
        out["no_cooling"] = ("h = 0: every shot adds heat that never leaves, so no "
                             "current above zero can be sustained and only the shot "
                             "count is meaningful")

    # --- the other direction: these particular pulses ---
    stats = None
    if cur is not None:
        a_val = cur ** 2 * rho_amb / (area * c_line)
        k_on = a_val * beta - (1.0 / tau if tau != float("inf") else 0.0)
        if tau == float("inf"):
            # pure energy accumulation: heat depends only on total on-time
            t_fuse = math.log(ratio) / (a_val * beta)
            shots = int(math.ceil(t_fuse / t_on_s - 1e-9))
            stats = {"k": k_on, "q": None, "b": None, "steady_trough": None,
                     "steady_peak": None, "shots": shots}
        else:
            stats = _train(a_val, beta, tau, t_on_s, t_off_s, dt_melt)
            # gate 2: the geometric series against the same map stepped three
            # times. The identity trough(n) = b (q^n - 1)/(q - 1) holds whether the
            # series converges or not, so this checks the algebra without waiting
            # for convergence (1 - q can be 1e-4 on a long thermal time constant).
            qq = stats["q"]
            trough_3, _peak_3 = _brute_train(a_val, beta, tau, t_on_s, t_off_s, 3)
            # (q^3 - 1)/(q - 1) = 1 + q + q^2 EXACTLY, and that factored form has no
            # cancellation. Near the runaway boundary q - 1 is ~1e-10, so the
            # unfactored quotient loses most of its digits and used to trip this
            # gate on a correct series.
            want = stats["b"] * (1.0 + qq + qq * qq)
            ref = max(abs(want), abs(trough_3), 1e-12)
            if abs(trough_3 - want) > 1e-9 * ref:
                return err(
                    "the stepped map disagrees with the closed form after 3 shots: "
                    "%.6g vs %.6g K" % (trough_3, want),
                    "INTERNAL_CHECK_FAILED",
                    hint="The geometric series and the cycle-by-cycle map are the same "
                         "algebra; if they differ, one of them has a sign error and "
                         "both the peak temperature and the shot count would be wrong.",
                )
            # gate 3: the shot count itself, stepped until it melts (cheap while
            # the count is small; skipped when it is astronomically large).
            if stats["shots"] is not None and 0 < stats["shots"] <= 2000:
                kk = stats["k"]
                e_off = stats["e_off"]
                tr = 0.0
                peak_before = 0.0
                for _shot in range(1, stats["shots"]):
                    peak_before = _rise(tr, a_val, kk, t_on_s)
                    tr = peak_before * e_off
                peak_at = _rise(tr, a_val, kk, t_on_s)
                if stats["shots"] > 1 and peak_before >= dt_melt:
                    return err(
                        "the shot count is too late: shot %d already melts (%.6g K)"
                        % (stats["shots"] - 1, peak_before),
                        "INTERNAL_CHECK_FAILED",
                    )
                if peak_at < dt_melt:
                    return err(
                        "the shot count is too early: shot %d stays at %.6g K, below "
                        "the %.6g K melting rise"
                        % (stats["shots"], peak_at, dt_melt),
                        "INTERNAL_CHECK_FAILED",
                    )
        out["current_a"] = round(cur, 6)
        out["current_over_max_repetitive"] = round(cur / i_crit, 6) if i_crit > 0 else None
        peak_rise = stats["steady_peak"]
        if peak_rise is not None:
            out["steady_peak_c"] = round(t_ambient_c + peak_rise, 3)
            out["steady_trough_c"] = round(t_ambient_c + stats["steady_trough"], 3)
            out["steady_ripple_c"] = round(peak_rise - stats["steady_trough"], 3)
            # mean over one cycle, by quadrature - no second closed form to get wrong
            nq = 200
            tot = 0.0
            for idx in range(nq):
                # mid-point of each of nq slices across the period
                tt = (idx + 0.5) * (t_on_s + t_off_s) / nq
                if tt <= t_on_s:
                    v = _rise(stats["steady_trough"], a_val, stats["k"], tt)
                else:
                    v = peak_rise * math.exp(-(tt - t_on_s) / tau)
                tot += v
            out["steady_mean_rise_c"] = round(tot / nq, 3)
            # The train's average heating is the DC heating that would hold the same
            # mean rise under the same cooling: I_eq^2 rho(Ta)(1 + beta dT) = h P area dT
            rise_mean = tot / nq
            out["dc_equivalent_current_a"] = round(
                math.sqrt(g_line * area * rise_mean /
                          (rho_amb * (1.0 + beta * rise_mean)))
                if rise_mean > 0 else 0.0, 6)
            if peak_rise >= dt_melt:
                out["steady_state_would_melt"] = True
            # sensitivity to the one number that was taken from the user
            for mult, key in ((0.5, "steady_peak_c_at_half_h"),
                              (2.0, "steady_peak_c_at_double_h")):
                hh = h * mult
                if hh <= 0:
                    continue
                tau2 = c_line / (hh * perim)
                st2 = _train(a_val, beta, tau2, t_on_s, t_off_s, dt_melt)
                if st2["steady_peak"] is not None:
                    out[key] = round(t_ambient_c + st2["steady_peak"], 3)
            vals = [v for v in (out.get("steady_peak_c_at_half_h"),
                                out.get("steady_peak_c_at_double_h")) if v is not None]
            if vals and abs(out["steady_peak_c"] - t_ambient_c) > 1e-9:
                spread = (max(vals) - min(vals)) / (out["steady_peak_c"] - t_ambient_c)
                out["h_sensitivity"] = (
                    "halving/doubling h moves the steady peak by %.0f %% of its own "
                    "rise: the answer is only as good as your h" % (100.0 * spread))
        if stats["shots"] is None or stats["shots"] >= TRAIN_NEVER:
            # The sequence of trough temperatures converges below the melting rise:
            # the train settles and no number of extra shots changes that. Reported
            # as None rather than a huge integer, because "never" is not a count.
            out["survives_indefinitely"] = True
            out["pulses_to_melt"] = None
        else:
            out["survives_indefinitely"] = False
            out["pulses_to_melt"] = stats["shots"]
            out["melt_after_s"] = round(stats["shots"] * (t_on_s + t_off_s), 6)
        # "will the peak settle?" is a yes/no and must be answered both ways
        out["runs_away"] = peak_rise is None
        # the duty ceiling at this current, when there is one
        if tau != float("inf") and stats["k"] > 0:
            out["max_duty_cycle_at_current"] = round(1.0 / (1.0 + stats["k"] * tau), 6)
        else:
            out["max_duty_cycle_at_current"] = 1.0

    if standard is not None:
        std, e = _guard_standard(standard)
        if e:
            return e
        cap = current_capacity(width_mm, delta_t_c, copper_oz, layer_n, False, std)
        if cap.get("status") != "ok":
            return cap
        out["continuous_rating"] = {
            "standard": std,
            "delta_t_c": round(delta_t_c, 3),
            "capacity_a": cap["value"],
            "repetitive_over_continuous": (round(i_crit / cap["value"], 6)
                                           if cap["value"] else None),
            "note": "not a safety margin, and not even the same criterion: capacity_a "
                    "is a DC current that reaches delta_t_c on the standard's cooling "
                    "(board + both faces), while max_repetitive_current_a is the train "
                    "current at the melt boundary on your h*P. Compare at the same "
                    "temperature instead - see dc_equivalent_current_a",
        }

    return ok(
        value=round(i_crit, 6),
        unit="A",
        model="lumped_first_order_pulse_train",
        basis="C' dT/dt = I^2 rho(T) - hP(T-Ta) solved exactly per interval; copper's "
              "resistance law with the same 234 as Onderdonk (1928), so the adiabatic "
              "limit reproduces the fusing time; runaway when k*t_on >= t_off/tau",
        precision="+/-40% and dominated by two one-sided choices: h is yours (a factor "
                  "of 2 in h moves the sustained current by sqrt(2) and the steady peak "
                  "far more) and the copper is lumped (one temperature for the whole "
                  "section, no lengthwise gradient, no board, no radiating surface "
                  "separate from h). The melt threshold ignores latent heat and the "
                  "resistance law is copper's only",
        assumptions={
            "material": "copper (rho20 = 1.72e-8 ohm*m, rho_m = 8960 kg/m^3, "
                        "c_p = 385 J/(kg*K), melt at 1083 C)",
            "cooling": "lumped: h * perimeter, one h you supply, no separate "
                       "top/bottom/edge coefficients",
            "spatial": "one temperature for the whole trace cross-section and length",
            "phase_change": "not modelled (melt point only)",
            "waveform": "rectangular current pulses of constant amplitude; a pulse of "
                        "the same RMS is not equivalent",
            "skin_effect": "not modelled (short pulses at high frequency make the "
                           "copper effectively thinner and this further optimistic)",
            "standard": standard,
        },
        extra=out,
    )
