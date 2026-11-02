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

# --- copper's caloric constants: the ONE thing Onderdonk leaves out is fusion ---
# Onderdonk's 33 folds density, specific heat and rho20 into one number, so it cannot
# be asked "how much energy to actually melt". Two constants answer that, and both are
# ordinary hand-book copper, not a vendor figure:
#   c_p  = 24.440 J/(mol K) at 25 C / 63.546 g/mol = 384.6 J/(kg K)  -> 385
#   L_f  = 13.26 kJ/mol     / 63.546 g/mol         = 208.7 kJ/kg     (also quoted
#          as 210 J/g; the two differ by 0.6 % and the answer moves by 0.3 %)
# The consistency check that these belong to the same copper as the 33: with
# rho = 8960 kg/m3 and rho20 = 1.72e-8 ohm m, (c*rho/rho20)*(234+25) = 5.194e16
# against 1/(5.0670748e-10^2 * 33 * ln10) = 5.126e16 implied by Onderdonk's own
# constant -> 1.3 %. That 1.3 % is why this module uses 385 and not 400.
COPPER_CP_J_PER_KGK = 385.0
COPPER_LATENT_HEAT_J_PER_KG = 208700.0
COPPER_DENSITY_KG_M3 = 8960.0        # only used by the consistency check above
COPPER_RHO20_OHM_M = 1.72e-8         # ditto - never enters an answer
M2_PER_CMIL = 5.067074790975e-10

# Which melting criterion the caller wants. "sensible" is Onderdonk exactly, byte for
# byte, because that is what every printed fusing table quotes; the other one adds the
# energy the phase change itself absorbs. Not a choice this module makes for you.
MELTING_MODELS = ("sensible", "sensible_plus_latent")

# The fusion energy re-expressed as a temperature: L_f / c_p = 208.7 kJ/kg / 385
# J/(kg K) = 542.08 K. Once a node is AT the melt point, every further joule goes
# into this reservoir instead of into another kelvin, so the reservoir is 542 K deep
# and it cannot be skipped - it is the same 208.7 kJ/kg that adds 25.3 % to I^2t in
# fusing_current. Expressed this way the pulse-train map stays in temperature units.
COPPER_LATENT_RISE_K = COPPER_LATENT_HEAT_J_PER_KG / COPPER_CP_J_PER_KGK

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

def latent_heat_share_of_i2t(t_ambient_c):
    """How much of the fusing I^2t is the latent heat of fusion, as a fraction.

    Derived, not fitted. The adiabatic balance is  C dT/dt = I^2 rho(T),  with
    rho(T) = rho20 (234+T)/(234+Ta), so

        (I^2 t)_sensible = (c rho_m A^2/rho20)(234+Ta) ln((234+Tm)/(234+Ta))

    which is Onderdonk's log10 form times ln10. After T reaches Tm the temperature
    stops rising and the same current pours its power into fusion instead:

        (I^2 t)_latent   = rho_m L_f A^2/rho(Tm)

    Divide one by the other and the geometry, the area and rho20 all cancel:

        latent/sensible  = L_f / (c (234+Tm) ln(ratio))

    Note what this is NOT: it is not L_f/(c dT) = 0.512. The last stretch to Tm is
    travelled while rho is (234+Tm)/(234+Ta) = 5.08x its cold value, so a joule
    spent there costs a fifth of the time it costs at the start; the latent stage
    sits at the hottest point and is therefore cheap in TIME and expensive in
    ENERGY. That is why the fraction is 0.253 and not 0.512.
    """
    ratio = (ONDERDONK_MELT_C - t_ambient_c) / (ONDERDONK_REF_C + t_ambient_c) + 1.0
    return COPPER_LATENT_HEAT_J_PER_KG / (
        COPPER_CP_J_PER_KGK * (ONDERDONK_REF_C + ONDERDONK_MELT_C)
        * math.log(ratio))


def fusing_current(width_mm, t_s=1.0, copper_oz=1.0, thickness_um=None,
                   t_ambient_c=25.0, delta_t_c=10.0, layer="external",
                   standard=None, melting_model="sensible"):
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
        fusion. This is not "a little": fusion absorbs 208.7 kJ/kg against 407 kJ/kg
        of sensible heat on the way up, and because the latent stage runs at
        rho(1083)/rho(25) = 5.08x the cold power it adds 25.3 % to I^2t, i.e. the
        real melting current is 11.9 % HIGHER (at 25 C). melting_model=
        'sensible_plus_latent' returns that number; the default keeps Onderdonk so
        that a quoted fusing table still reproduces.
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
    if melting_model is None:
        melting_model = "sensible"
    if melting_model not in MELTING_MODELS:
        return err("melting_model must be one of %s" % (", ".join(MELTING_MODELS),),
                   "ARG_INVALID",
                   hint="'sensible' is Onderdonk as printed (melt point only); "
                        "'sensible_plus_latent' adds copper's 208.7 kJ/kg of fusion "
                        "energy, which is +25.3 % on I^2t and +11.9 % on the current")
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
    # the same formula with (1 + latent share) inside the root: I^2 t is what the
    # energy balance fixes, so the current moves by the SQUARE root
    latent_share = latent_heat_share_of_i2t(t_ambient_c)
    i_fuse_sensible = i_fuse
    i_fuse_latent = i_fuse * math.sqrt(1.0 + latent_share)
    if melting_model == "sensible_plus_latent":
        i_fuse = i_fuse_latent

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
        "melting_model": melting_model,
        "fusing_current_sensible_only_a": round(i_fuse_sensible, 6),
        "fusing_current_with_latent_a": round(i_fuse_latent, 6),
        "latent_share_of_i2t": round(latent_share, 6),
        "latent_heat_means": (
            "copper needs 208.7 kJ/kg of fusion energy on top of 407 kJ/kg of sensible "
            "heat, which is %+.2f %% of the fusing I^2t and %+.2f %% on the current at "
            "%.1f C; the model below is the %s one"
            % (100.0 * latent_share, 100.0 * (math.sqrt(1.0 + latent_share) - 1.0),
               t_ambient_c,
               "Onderdonk (melt point)" if melting_model == "sensible"
               else "melt-through")),
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
        # I^2t is what the energy balance fixes, so the melt TIME scales by the same
        # (1 + latent share) the current does not - and only when that stage is asked
        # for. Writing this factor unconditionally made both models print 16.0435 s,
        # which is a silent lie about which criterion was used.
        melt_factor = (1.0 + latent_share
                       if melting_model == "sensible_plus_latent" else 1.0)
        melt_at_cap_s = (math.log10(ratio) * melt_factor /
                         (ONDERDONK_K * (cap_a / area_cmil) ** 2))
        out["steady_rating_latent_note"] = (
            "adiabatic_melt_time_at_capacity_s uses the same melting criterion as the "
            "current above, so it carries the 1 + %.4f latent factor when "
            "melting_model='sensible_plus_latent'" % latent_share)
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
        model=("onderdonk_adiabatic_fusing" if melting_model == "sensible"
               else "onderdonk_adiabatic_fusing_plus_latent_heat"),
        basis="Onderdonk (1928): I = A_cmil * sqrt(log10((1083 - Ta)/(234 + Ta) + 1)"
              " / (33*t)), A in circular mils, t in seconds; copper's constants"
              + ("" if melting_model == "sensible" else
                 "; times sqrt(1 + L_f/(c_p (234+1083) ln(ratio))) for the fusion "
                 "energy, L_f = 208.7 kJ/kg and c_p = 385 J/(kg K)"),
        precision="+/-30% at best and one-sided: adiabatic (no cooling), stops at the "
                  "melt point (no latent heat unless melting_model says otherwise), "
                  "equal-area conversion from a rectangle "
                  "to a round wire. Every one of those makes the real fusing current "
                  "HIGHER, so treat this as the lowest current that can melt it in t",
        assumptions={
            "material": "copper (1083 C, 234 C reference, k = 33) - other metals are "
                        "not covered",
            "cooling": "none (adiabatic)",
            "phase_change": ("not modelled (melt point only): the answer is the "
                             "current that REACHES 1083 C, not the current that melts "
                             "through; pass melting_model='sensible_plus_latent' for "
                             "the latter (+%.1f %% at %.0f C)"
                             % (100.0 * (math.sqrt(1.0 + latent_share) - 1.0),
                                t_ambient_c)
                             if melting_model == "sensible" else
                             "fusion energy included: 208.7 kJ/kg on top of the "
                             "sensible heat, so this is the current that melts "
                             "THROUGH, not the current that just reaches 1083 C"),
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
    if b == 0.0:
        # the off-time is so strong that the trough is back at zero (underflowed to
        # exactly zero) one period later: every shot then starts from the same cold
        # state, so the peak never grows and there is no shot that melts it
        out["shots"] = None
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


def _latent_settles(a, beta, tau, t_on, t_off, dt_melt, latent_rise):
    """Does this train reach a cycle that repeats for ever, with latent heat on?

    Stepping the map shot by shot is the honest way to answer it and the wrong way to
    do it here: at the ceiling the trough creeps towards its fixed point like 1/n, so a
    train that has already settled can need a hundred thousand shots to prove it (this
    was measured - the B geometry needs 1e5 at 41.338 A but only 4e3 at 41.30 A). The
    map is a monotone increasing map of one variable, so the fixed point it converges
    to is the root of F(x) - x = 0, and that root is one bisection away on exactly the
    same algebra. Two ways to melt through come out of it, and they are different:

      * one pulse puts more into the reservoir than it holds (gain >= latent_rise);
      * the off-time cannot empty what the pulse put in (gain > freeze_rise*t_off).

    `F(x)` is the trough one period later starting from trough `x`: the pulse is
    pinned at the melt point for the part it cannot finish, the reservoir is emptied
    only as far as the off-time allows, and whatever is left keeps the trough at the
    melt point.
    """
    if tau == float("inf"):
        # no cooling: any reservoir at all is permanent
        freeze_rise = 0.0
    else:
        freeze_rise = dt_melt / tau
    k = a * beta - (1.0 / tau if tau != float("inf") else 0.0)
    phi = a + k * dt_melt
    if phi <= 0.0:
        return True                     # the melt point is never reached at all

    def gain_at(x):
        if x >= dt_melt:
            t_a = 0.0
        else:
            den = a + k * x
            if den <= 0.0:
                return None             # the pulse starts already running away
            t_a = math.log(phi / den) / k
            if t_a < 0.0:
                t_a = 0.0
        if t_a >= t_on:
            return 0.0
        return phi * (t_on - t_a)

    def next_trough(x):
        g = gain_at(x)
        if g is None:
            return dt_melt, g              # the pulse starts already running away
        if g <= 0.0:
            # the pulse never reaches the melt point: the trough is the cooled PEAK,
            # not the cooled trough - getting this wrong makes every runaway look
            # settled, because the true peak term never enters
            peak = _rise(x, a, k, t_on)
            if tau == float("inf"):
                return peak, g
            return peak * math.exp(-t_off / tau), g
        if freeze_rise <= 0.0:
            return dt_melt, g
        t_hold = g / freeze_rise
        if t_hold < t_off:
            return dt_melt * math.exp(-(t_off - t_hold) / tau), g
        return dt_melt, g

    # the fixed point of a monotone increasing map: bisect F(x) - x, which is >= 0 at
    # x = 0 and <= 0 at x = dt_melt, so a sign change always exists
    lo, hi = 0.0, dt_melt
    for _bis in range(80):
        mid = 0.5 * (lo + hi)
        nx, _g = next_trough(mid)
        if nx - mid > 0.0:
            lo = mid
        else:
            hi = mid
    x_star = 0.5 * (lo + hi)
    g_star = gain_at(x_star)
    if g_star is None:
        return False
    if g_star >= latent_rise:
        return False
    if freeze_rise <= 0.0:
        return g_star <= 0.0
    return g_star <= freeze_rise * t_off


def _train_latent(a, beta, tau, t_on, t_off, dt_melt, latent_rise,
                  max_shots=200000):
    """Shots to melt when the fusion energy is carried as a reservoir.

    Same per-interval map as `_train` - the pulse is still d(dT)/dt = a + k dT - with
    one extra state: once the rise reaches dt_melt the rest of THAT pulse goes into
    `latent_rise` (COPPER_LATENT_RISE_K) instead of into more kelvin, and the off-time
    has to refreeze at the melt point before the trough can fall again. Two regimes
    come out of this and they are not symmetric:

      * the reservoir empties inside the off-time: the trough falls normally and the
        cycle is the same as the sensible one, only hotter for a while;
      * it does not: the trace spends the whole off-time pinned at the melt point and
        still arrives at the next pulse frozen, so the reservoir grows shot after
        shot and melt is certain - the trace cannot sit at 1083 C while absorbing
        energy, because temperature cannot rise until the reservoir is full.

    Returns {"shots": int or None, "why": str, "peak": rise or None,
             "latent_left": K, "gain_per_shot": K, "release_capacity": K}.
    """
    if tau == float("inf"):
        freeze_rise = 0.0                      # h = 0: nothing ever refreezes
    else:
        freeze_rise = dt_melt / tau            # K per second while pinned at the melt
    k = a * beta - (1.0 / tau if tau != float("inf") else 0.0)
    trough, latent = 0.0, 0.0
    prev = None
    gain_last = 0.0
    for shot in range(1, int(max_shots) + 1):
        if a + k * dt_melt <= 0.0:
            return {"shots": None, "why": "cannot reach the melt point",
                    "peak": None, "latent_left": latent,
                    "gain_per_shot": 0.0, "release_capacity": freeze_rise * t_off}
        if trough >= dt_melt:
            t_a = 0.0
        else:
            t_a = math.log((a + k * dt_melt) / (a + k * trough)) / k
            if t_a < 0.0:
                t_a = 0.0
        if t_a >= t_on:
            peak = _rise(trough, a, k, t_on)
            latent_after = latent
            gain_last = 0.0
        else:
            gain_last = (a + k * dt_melt) * (t_on - t_a)     # K of reservoir added
            latent_after = latent + gain_last
            if latent_after >= latent_rise:
                return {"shots": shot, "why": "melt", "peak": dt_melt,
                        "latent_left": latent_after,
                        "gain_per_shot": gain_last,
                        "release_capacity": freeze_rise * t_off}
            peak = dt_melt
        latent = latent_after
        if latent > 0.0:
            t_hold = latent / freeze_rise if freeze_rise > 0.0 else float("inf")
            if t_hold < t_off:
                latent = 0.0
                trough = dt_melt * math.exp(-(t_off - t_hold) / tau)
            else:
                latent -= freeze_rise * t_off
                trough = dt_melt
        else:
            trough = peak * math.exp(-t_off / tau)
        if (latent == 0.0 and prev is not None
                and abs(trough - prev) <= 1e-13 * max(1.0, trough)):
            return {"shots": None, "why": "settled", "peak": peak,
                    "latent_left": 0.0,
                    "gain_per_shot": gain_last,
                    "release_capacity": freeze_rise * t_off}
        if shot % 50 == 0 and _latent_settles(a, beta, tau, t_on, t_off, dt_melt,
                                             latent_rise):
            return {"shots": None, "why": "settled", "peak": peak,
                    "latent_left": 0.0,
                    "gain_per_shot": gain_last,
                    "release_capacity": freeze_rise * t_off}
        prev = trough
    return {"shots": None, "why": "beyond the shot cap", "peak": None,
            "latent_left": latent, "gain_per_shot": gain_last,
            "release_capacity": freeze_rise * t_off}

def _latent_ceiling(mkargs, dt_melt, hi, lo_hint=None, max_shots=4000):
    """Largest current whose pulse train never MELTS THROUGH with latent heat on.

    A different question from the melt-free ceiling: once the peak is pinned at
    1083 C the extra energy goes into the reservoir, and as long as the off-time
    empties that reservoir again the cycle repeats forever at the melt point. The
    train only melts through when the reservoir cannot be emptied, so this ceiling
    sits ABOVE the melt-free one and the two are different numbers. Bisected on the
    same per-shot map: `_train_latent` reports why it stopped, and "settled" is the
    condition we are looking for.
    """
    def settles(cur):
        a = cur * cur * mkargs["rho_amb"] / (mkargs["area"] * mkargs["c_line"])
        st = _train_latent(a, mkargs["beta"], mkargs["tau"], mkargs["t_on"],
                          mkargs["t_off"], dt_melt, COPPER_LATENT_RISE_K,
                          max_shots=max_shots)
        return st["why"] == "settled"

    if hi <= 0.0:
        return None
    # a low point has to be found from below: near the ceiling the trough creeps
    # towards its fixed point like 1/n, so the map needs a shot cap, and a cap that
    # is too small can only make the ceiling look LOWER, never higher. The candidate
    # ladder starts just under the melt-free current, which always settles (there the
    # pulse ends exactly at the melt point, so no reservoir is ever entered).
    lo_i = None
    for cand in (lo_hint, 0.75 * hi, 0.5 * hi, 0.25 * hi):
        if cand and cand > 0.0 and settles(cand):
            lo_i = cand
            break
    if lo_i is None:
        return None
    hi_i = hi
    if settles(hi_i):
        return hi_i
    for _bis in range(60):
        mid = 0.5 * (lo_i + hi_i)
        if settles(mid):
            lo_i = mid
        else:
            hi_i = mid
    return 0.5 * (lo_i + hi_i)


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
                            layer="external", standard=None, melting_model="sensible"):
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

    if melting_model is None:
        melting_model = "sensible"
    if melting_model not in MELTING_MODELS:
        return err("melting_model must be one of " + ", ".join(MELTING_MODELS),
                   "ARG_INVALID",
                   hint="'sensible' stops at the melt POINT (the default, unchanged); "
                        "'sensible_plus_latent' also charges copper's 208.7 kJ/kg of "
                        "fusion energy, which delays the shot count but does not move "
                        "the sustainable current")

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

    # `i_crit` above is the RUNAWAY bound: above it no steady peak exists at all, and
    # that condition is a balance of RATES - it carries no heat capacity and no melt
    # point, because it never asks how hot the trace gets. The current a train
    # actually survives is the one whose steady peak lands ON the melt point, which is
    # strictly lower and can be much lower: on a 1 % duty cycle the runaway bound is
    # 4.4x the melt-free current, because the train settles to a peak far above 1083 C
    # while still having a steady solution. Solved here by bisection on the same exact
    # map rather than on a second approximation, so the two numbers cannot drift.
    if g_line > 0:
        lo_i, hi_i = 0.0, i_crit
        for _bis in range(200):
            mid_i = 0.5 * (lo_i + hi_i)
            st_mid = _train(mid_i * mid_i * rho_amb / (area * c_line),
                            beta, tau, t_on_s, t_off_s, dt_melt)
            pk_mid = st_mid["steady_peak"]
            if pk_mid is None or pk_mid >= dt_melt:
                hi_i = mid_i
            else:
                lo_i = mid_i
        i_nomelt = 0.5 * (lo_i + hi_i)
    else:
        i_nomelt = 0.0

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
        "max_repetitive_current_a": round(i_crit, 6),   # runaway bound, kept as-is
        "max_repetitive_current_a_note": "the RUNAWAY bound - above it no steady peak "
                                         "exists at all. It ignores the melt point and "
                                         "the heat capacity, so it is an upper bound; "
                                         "the survivable current is value / "
                                         "max_current_without_melting_a",
        "single_pulse_fusing_current_a": round(i_fuse_once, 6),
        "derating_vs_single_pulse": (round(i_nomelt / i_fuse_once, 6)
                                     if i_fuse_once > 0 else None),
        "runaway_rule": "no steady peak exists at all when I^2 > area*h*P / "
                        "(D*beta*rho(Ta)); that bound carries no heat capacity, no melt "
                        "point and no pulse length, so it is an UPPER bound on the "
                        "survivable current, not the answer to it - see "
                        "max_current_without_melting_a",
        "max_current_without_melting_a": round(i_nomelt, 6),
        "max_current_without_melting_basis": "root of steady_peak(I) = 1083 C on the "
                                             "same exact per-interval map, bisected; "
                                             "this is the current whose settled peak "
                                             "sits ON the melt point, so below it the "
                                             "train never melts and above it the peak "
                                             "settles above 1083 C and the metal opens",
        "runaway_bound_over_no_melt": (round(i_crit / i_nomelt, 6)
                                       if i_nomelt > 0 else None),
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
        # --- the same question with the fusion energy carried as a reservoir ---
        shots_used = stats["shots"]
        lat_capped = False
        if melting_model == "sensible_plus_latent":
            out["melting_model"] = melting_model
            out["pulses_to_melt_sensible"] = (None if (stats["shots"] is None
                                                       or stats["shots"] >= TRAIN_NEVER)
                                              else stats["shots"])
            lat = _train_latent(a_val, beta, tau, t_on_s, t_off_s, dt_melt,
                               COPPER_LATENT_RISE_K)
            out["latent_rise_k"] = round(COPPER_LATENT_RISE_K, 6)
            out["pulses_to_melt_with_latent"] = lat["shots"]
            out["latent_release_capacity_k_per_period"] = round(
                lat["release_capacity"], 6)
            out["latent_gain_last_pulse_k"] = round(lat["gain_per_shot"], 6)
            if lat["why"] == "settled":
                out["latent_verdict"] = ("the reservoir empties inside the off-time and "
                                         "the train settles below the melt point, so "
                                         "the shot count is None for the same reason "
                                         "as the sensible one")
            elif lat["why"] == "melt":
                out["latent_verdict"] = ("the reservoir is still partly full at the end "
                                         "of every pulse; the trace is pinned at the "
                                         "melt point for part of the cycle and cannot "
                                         "stay there while absorbing energy, so melt is "
                                         "certain at the shot count above")
            elif lat["why"] == "cannot reach the melt point":
                out["latent_verdict"] = ("this current cannot even hold the melt point: "
                                         "the reservoir is never entered")
            else:
                out["latent_verdict"] = ("not resolved within %d shots; the reservoir is "
                                         "accumulating, so it does melt, later than the "
                                         "cap" % 200000)
            lat_capped = (lat["why"] == "beyond the shot cap")
            if (stats["shots"] is not None and lat["shots"] is not None
                    and stats["shots"] < TRAIN_NEVER):
                out["latent_delays_melt_pct"] = round(
                    100.0 * (lat["shots"] / float(stats["shots"]) - 1.0), 3)
            # the number the answer is derated against is unchanged by this: a cyclic
            # steady state cannot hold latent energy, in = out over a period, so the
            # sustainable current is the same one. Stated, not implied.
            out["max_repetitive_current_unaffected_by_latent"] = True
            shots_used = lat["shots"]
        if shots_used is None or shots_used >= TRAIN_NEVER:
            # The sequence of trough temperatures converges below the melting rise:
            # the train settles and no number of extra shots changes that. Reported
            # as None rather than a huge integer, because "never" is not a count.
            # A capped latent run has no shot count but is NOT a survivor: its
            # reservoir was still growing when the cap was hit, which is the one case
            # where "no number" and "never" are different answers.
            out["survives_indefinitely"] = (not lat_capped)
            out["pulses_to_melt"] = None
            if lat_capped:
                out["melt_beyond_shot_cap"] = 200000
        else:
            out["survives_indefinitely"] = False
            out["pulses_to_melt"] = shots_used
            out["melt_after_s"] = round(shots_used * (t_on_s + t_off_s), 6)
        # "will the peak settle?" is a yes/no and must be answered both ways
        out["runs_away"] = peak_rise is None
        # the duty ceiling at this current, when there is one
        if tau != float("inf") and stats["k"] > 0:
            out["max_duty_cycle_at_current"] = round(1.0 / (1.0 + stats["k"] * tau), 6)
        else:
            out["max_duty_cycle_at_current"] = 1.0

    # --- the latent heat's own ceiling: a threshold, so it does not need current_a ---
    if melting_model == "sensible_plus_latent":
        i_meltthrough = _latent_ceiling(
            {"rho_amb": rho_amb, "area": area, "c_line": c_line, "beta": beta,
             "tau": tau, "t_on": t_on_s, "t_off": t_off_s}, dt_melt, i_crit,
            lo_hint=0.9 * i_nomelt)
        out["max_current_without_melt_through_a"] = (
            round(i_meltthrough, 6) if i_meltthrough is not None else None)
        out["melt_through_over_no_melt"] = (
            round(i_meltthrough / i_nomelt, 6)
            if (i_meltthrough is not None and i_nomelt > 0) else None)
        out["max_current_without_melt_through_basis"] = (
            "largest current whose train never melts THROUGH with the reservoir "
            "included: every pulse may pin the peak on 1083 C, but the off-time has to "
            "empty the reservoir again. Above it the reservoir accumulates and melt is "
            "certain. This sits above value because the peak is capped at the melt "
            "point instead of running past it; it is NOT permission to park the copper "
            "at 1083 C")

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
            "repetitive_over_continuous": (round(i_nomelt / cap["value"], 6)
                                           if cap["value"] else None),
            "note": "not a safety margin, and not even the same criterion: capacity_a "
                    "is a DC current that reaches delta_t_c on the standard's cooling "
                    "(board + both faces), while max_current_without_melting_a is the "
                    "train current whose settled peak sits on 1083 C under your h*P. "
                    "Compare at the same temperature instead - see "
                    "dc_equivalent_current_a",
        }

    return ok(
        value=round(i_nomelt, 6),
        unit="A",
        model="lumped_first_order_pulse_train",
        basis="C' dT/dt = I^2 rho(T) - hP(T-Ta) solved exactly per interval; copper's "
              "resistance law with the same 234 as Onderdonk (1928), so the adiabatic "
              "limit reproduces the fusing time. The returned value is the current "
              "whose SETTLED PEAK sits on 1083 C (the melt-free ceiling); the runaway "
              "bound, above which no steady peak exists at all, is reported alongside "
              "as max_repetitive_current_a and is strictly higher"
              + ("" if melting_model == "sensible" else
                 "; the shot count adds the latent heat (L_f/c_p = 542.08 K of "
                 "equivalent rise at the melt point)"),
        precision="+/-40% and dominated by two one-sided choices: h is yours (a factor "
                  "of 2 in h moves the sustained current by sqrt(2) and the steady peak "
                  "far more) and the copper is lumped (one temperature for the whole "
                  "section, no lengthwise gradient, no board, no radiating surface "
                  "separate from h). Two currents come back: the runaway bound (no "
                  "steady solution exists above it - a balance of rates) and the "
                  "melt-free current (settled peak on 1083 C); they are the same only "
                  "as the duty cycle approaches 1 and differ 4.4x at 1 %. The melt "
                  "threshold "
                  + ("ignores latent heat" if melting_model == "sensible"
                     else "charges the latent heat as a 542.08 K reservoir at the melt "
                          "point") + " and the resistance law is copper's only",
        assumptions={
            "material": "copper (rho20 = 1.72e-8 ohm*m, rho_m = 8960 kg/m^3, "
                        "c_p = 385 J/(kg*K), melt at 1083 C)",
            "cooling": "lumped: h * perimeter, one h you supply, no separate "
                       "top/bottom/edge coefficients",
            "spatial": "one temperature for the whole trace cross-section and length",
            "phase_change": ("not modelled: the shot count stops where the rise reaches "
                             "1083 C (melting_model='sensible')"
                             if melting_model == "sensible" else
                             "included: the pulse cannot carry the rise past 1083 C, so "
                             "the rest of each pulse charges copper's 208.7 kJ/kg "
                             "(542.08 K of equivalent rise) and the off-time has to "
                             "refreeze it before the trough can fall"),
            "waveform": "rectangular current pulses of constant amplitude; a pulse of "
                        "the same RMS is not equivalent",
            "skin_effect": "not modelled (short pulses at high frequency make the "
                           "copper effectively thinner and this further optimistic)",
            "standard": standard,
        },
        extra=out,
    )


# ---------------------------------------------------------------------------
# Fusing ALONG a taper: a trace whose width changes has a temperature PROFILE
# ---------------------------------------------------------------------------
# fusing_current answers for ONE width and deletes spatial extent on purpose ("one
# temperature for the whole trace cross-section and length"). A real neck - a fuse,
# a via-fanout bottleneck, a trace stepping down into a pad - melts in the narrow
# part, and the copper either side both feeds heat into it and drains heat out of
# it. So the same physics needs a second model, and this one has no fitted
# constants either:
#
#   C_i dT_i/dt = I^2 R_i(T_i) + sum_j G_ij (T_j - T_i)   (+ G_end (T_end - T_i))
#
#   C_i    = rho_m c_p A_i L_i                       J/K   (node = segment centre)
#   R_i    = rho(T_i) L_i / A_i                      ohm
#   G_ij   = 1 / (L_i/(2 k A_i) + L_j/(2 k A_j))     W/K   (two half-segments in series)
#   rho(T) = rho20 (1 + alpha (T - 20))              copper's own law
#
# rho is LINEAR in T, so the source is a_i + b_i T and the system stays tridiagonal:
# backward Euler + Thomas, exact in time per step, no iteration and no convergence
# criterion to fake. ALPHA_234_CU = 1/254 is the (234 + T) law referenced at 20 C -
# the same 234 as Onderdonk (1928) - which is what makes the anchor below work: a
# UNIFORM segment with insulated ends has identical neighbours, every conduction
# term cancels in pairs, and the system collapses to the lumped adiabatic ODE whose
# melt time is Onderdonk's closed form (measured residue < 0.5 %, and it is the
# 33-vs-modern-constants residue, recorded in docs/62 - not fitted away).
ALPHA_234_CU = 1.0 / 254.0   # copper's (234 + T)/254 resistance law, at 20 C
K_THERMAL_CU = 400.0         # W/(m K) at room temperature (401 at 20 C, ~340 at 1000 C)
MELT_CU_C = 1083.0


def _taper_adiabatic_current(area_m2, t_s, ta_c, melt_c, alpha, rho_m, cp, rho20):
    """Current whose ADIABATIC melt time is exactly t_s, for a linear rho(T).

    dT/dt = I^2 rho20 (1 + alpha (T - 20)) / (rho_m cp A^2), so

        t_s = rho_m cp A^2 / (I^2 rho20 alpha) * ln((1+a(melt-20))/(1+a(ta-20)))

    Note what is NOT in that expression: the length. Which is exactly why the
    narrowest segment of a taper is the one that goes first, and why this is a
    valid LOWER bound for any geometry with conduction on - conduction can only
    carry heat away from the neck, so the real fusing current is higher.
    """
    num = 1.0 + alpha * (melt_c - T_REF_C)
    den = 1.0 + alpha * (ta_c - T_REF_C)
    if den <= 0.0 or num <= den:
        return None
    shape = math.log(num / den) / alpha
    return area_m2 * math.sqrt(rho_m * cp * shape / (t_s * rho20))


def _taper_geometry(segments, thick_um):
    """Per-segment geometry, or an error. A dropped segment is a different trace."""
    if not isinstance(segments, (list, tuple)) or len(segments) == 0:
        return None, err("segments must be a non-empty list of {length_mm, width_mm} "
                         "objects", "ARG_INVALID",
                         hint="a taper is per-segment geometry: one entry per axial "
                              "section, in order from one end to the other")
    t_m = thick_um * 1e-6
    if not (t_m > 0):
        return None, err("copper thickness must be > 0", "ARG_RANGE")
    geom = []
    x = 0.0
    for i, seg in enumerate(segments):
        if not isinstance(seg, dict):
            return None, err("segments[%d] is not an object with length_mm/width_mm" % i,
                             "ARG_INVALID")
        try:
            L_mm = float(seg.get("length_mm"))
            w_mm = float(seg.get("width_mm"))
        except (TypeError, ValueError):
            return None, err("segments[%d].length_mm and width_mm must be numbers" % i,
                             "ARG_INVALID")
        if not (L_mm > 0):
            return None, err("segments[%d].length_mm must be > 0" % i, "ARG_RANGE")
        if not (w_mm > 0):
            return None, err("segments[%d].width_mm must be > 0" % i, "ARG_RANGE")
        L = L_mm * 1e-3
        w = w_mm * 1e-3
        geom.append({"index": i, "length_mm": round(L_mm, 6),
                     "width_mm": round(w_mm, 6), "length_m": L, "width_m": w,
                     "area_m2": w * t_m, "x_mm": round(x + L_mm / 2.0, 6)})
        x += L_mm
    return {"segments": geom, "total_mm": round(x, 6), "thick_um": thick_um,
            "narrowest_width_mm": round(min(s["width_mm"] for s in geom), 6)}, None


def _thomas(diag, lower, upper, rhs):
    """Tridiagonal solve; lower[i]*x[i-1] + diag[i]*x[i] + upper[i]*x[i+1] = rhs[i]."""
    n = len(diag)
    if diag[0] == 0.0:
        return None
    cp = [0.0] * n
    dp = [0.0] * n
    cp[0] = (upper[0] / diag[0]) if n > 1 else 0.0
    dp[0] = rhs[0] / diag[0]
    for i in range(1, n):
        den = diag[i] - lower[i] * cp[i - 1]
        if den == 0.0:
            return None
        cp[i] = (upper[i] / den) if i < n - 1 else 0.0
        dp[i] = (rhs[i] - lower[i] * dp[i - 1]) / den
    x = [0.0] * n
    x[n - 1] = dp[n - 1]
    for i in range(n - 2, -1, -1):
        x[i] = dp[i] - cp[i] * x[i + 1]
    return x


def _taper_coeffs(g, current_a, k_w_mk, ends_c, alpha, rho_m, cp, rho20):
    """Per-node heat capacity, inter-node conductance, source a_i + b_i T."""
    seg = g["segments"]
    n = len(seg)
    cap = [rho_m * cp * s["area_m2"] * s["length_m"] for s in seg]
    cond = [0.0] * max(0, n - 1)
    if k_w_mk > 0.0:
        for i in range(n - 1):
            r = (0.5 * seg[i]["length_m"] / (k_w_mk * seg[i]["area_m2"]) +
                 0.5 * seg[i + 1]["length_m"] / (k_w_mk * seg[i + 1]["area_m2"]))
            cond[i] = (1.0 / r) if r > 0.0 else 0.0
    g0 = gN = 0.0
    if ends_c is not None and k_w_mk > 0.0:
        # half a segment of copper to the terminal
        g0 = 2.0 * k_w_mk * seg[0]["area_m2"] / seg[0]["length_m"]
        gN = 2.0 * k_w_mk * seg[-1]["area_m2"] / seg[-1]["length_m"]
    a_src = [0.0] * n
    b_src = [0.0] * n
    for i in range(n):
        r = rho20 * seg[i]["length_m"] / seg[i]["area_m2"]
        a_src[i] = current_a * current_a * r * (1.0 - alpha * T_REF_C)
        b_src[i] = current_a * current_a * r * alpha
    return cap, cond, g0, gN, a_src, b_src


def _taper_run(g, current_a, t_end_s, dt_s, melt_c, ta_c, ends_c, k_w_mk,
               alpha, rho_m, cp, rho20):
    """March the axial system to t_end_s (or to the first melt) and report both."""
    seg = g["segments"]
    n = len(seg)
    cap, cond, g0, gN, a_src, b_src = _taper_coeffs(
        g, current_a, k_w_mk, ends_c, alpha, rho_m, cp, rho20)
    temp = [float(ta_c)] * n
    nsteps = int(math.ceil(t_end_s / dt_s - 1e-9))
    t = 0.0
    t_melt = None
    idx = None
    for _step in range(nsteps):
        diag = [0.0] * n
        lower = [0.0] * n
        upper = [0.0] * n
        rhs = [0.0] * n
        for i in range(n):
            s = 0.0
            if i > 0:
                lower[i] = -cond[i - 1]
                s += cond[i - 1]
            if i < n - 1:
                upper[i] = -cond[i]
                s += cond[i]
            if i == 0:
                s += g0
            if i == n - 1:
                s += gN
            diag[i] = cap[i] / dt_s - b_src[i] + s
            rhs[i] = cap[i] / dt_s * temp[i] + a_src[i]
        if g0:
            rhs[0] += g0 * ends_c
        if gN:
            rhs[n - 1] += gN * ends_c
        new = _thomas(diag, lower, upper, rhs)
        if new is None:
            return None, err("the axial system went singular at t = %.4g s (the "
                             "current has passed the point where heat generation "
                             "outruns conduction)" % t, "SOLVER_SINGULAR")
        for v in new:
            if v != v or v in (float("inf"), float("-inf")):
                return None, err("non-finite temperature at t = %.4g s" % t,
                                 "SOLVER_DIVERGED")
        for i in range(n):
            if temp[i] < melt_c <= new[i]:
                frac = (melt_c - temp[i]) / (new[i] - temp[i])
                tt = t + frac * dt_s
                if t_melt is None or tt < t_melt:
                    t_melt, idx = tt, i
        if t_melt is not None:
            break
        temp = new
        t += dt_s
    return {"melted": t_melt is not None, "t_melt_s": t_melt, "melt_segment": idx,
            "temperature_c": temp, "at_s": t, "steps": nsteps,
            "dt_s": dt_s, "conduction_delay_pct": None}, None


def _taper_latent_share(t_ambient_c, melt_c, alpha_per_k, cp_j_kgk, latent_j_kg):
    """How much LONGER a full melt takes than merely reaching the melt point.

    One node, no conduction, rho(T) = rho20 (1 + alpha (T - 20)):

        t_reach = (C / (I^2 R alpha)) ln(r(Tm) / r(Ta))
        t_lat   = (L_f / c_p) C / (I^2 R r(Tm))

    Divide and the geometry, the area and rho20 cancel:

        t_lat / t_reach = (L_f / c_p) alpha / (r(Tm) ln(r(Tm)/r(Ta)))

    With copper's own numbers this is 0.253093 - the same 25.3 % that
    fusing_current reports for I^2t, which is why it is an anchor and not a new
    constant. It is the LOWER bracket of the taper's own bisection: latent heat can
    only add time, never remove it, and for a single node it is the exact factor.
    """
    r_melt = 1.0 + alpha_per_k * (melt_c - T_REF_C)
    r_amb = 1.0 + alpha_per_k * (t_ambient_c - T_REF_C)
    if not (r_amb > 0.0) or not (r_melt > r_amb):
        return None
    return (latent_j_kg / cp_j_kgk) * alpha_per_k / (
        r_melt * math.log(r_melt / r_amb))

def _taper_run_latent(g, current_a, t_end_s, dt_s, melt_c, ta_c, ends_c, k_w_mk,
                      alpha, rho_m, cp, rho20, latent_j_kg):
    """_taper_run, plus a per-node latent reservoir - so the answer is MELT THROUGH.

    Each node carries (T, phi):  phi in [0, 1] is the molten FRACTION of that node,

        E_i = C_i (T_i - Tm) + phi_i L_i,   C_i = rho_m c_p A_i l_i,
                                            L_i = rho_m L_f A_i l_i

    so phi = 0 is solid, phi = 1 is liquid, and 0 < phi < 1 is a node sitting ON the
    melt point whose temperature cannot move while the reservoir fills. That plateau
    is EXACT: the unknown for such a node is phi, not T, so there is no artificial
    "mushy" temperature band - nothing to tune and nothing to tune wrongly. It is
    the switching system the 0.0.86 note said this needs.

    The active set (which nodes are pinned to Tm) is re-solved until it stops
    changing. Every iteration is one backward-Euler step over the same tridiagonal
    system as the sensible model, so with latent_j_kg = 0 the arithmetic is IDENTICAL
    to _taper_run and reproduces it bit for bit - that is the negative control.

    Two times come out, and they are different questions:
      t_onset_s  - a node first reaches melt_c      (what the sensible model answers)
      t_melt_s   - a node is FULLY liquid           (what melting costs on top)
    """
    seg = g["segments"]
    n = len(seg)
    cap, cond, g0, gN, a_src, b_src = _taper_coeffs(
        g, current_a, k_w_mk, ends_c, alpha, rho_m, cp, rho20)
    lat = [rho_m * latent_j_kg * s["area_m2"] * s["length_m"] for s in seg]
    temp = [float(ta_c)] * n
    phi = [0.0] * n
    nsteps = int(math.ceil(t_end_s / dt_s - 1e-9))
    t = 0.0
    t_onset = None
    i_onset = None
    t_full = None
    i_full = None
    max_iters = 0
    gen_total = 0.0
    flux_total = 0.0
    e_start = sum(cap[i] * (temp[i] - melt_c) for i in range(n))
    for _step in range(nsteps):
        temp_old = list(temp)
        phi_old = list(phi)
        pinned = [1 if (lat[i] > 0.0 and 0.0 < phi[i] < 1.0) else 0 for i in range(n)]
        x = None
        x_first = None
        newtemp = list(temp_old)
        newphi = list(phi_old)
        for _it in range(80):
            max_iters = max(max_iters, _it + 1)
            diag = [0.0] * n
            lower = [0.0] * n
            upper = [0.0] * n
            rhs = [0.0] * n
            for i in range(n):
                if pinned[i]:
                    diag[i] = 1.0
                    rhs[i] = melt_c
                    continue
                s = 0.0
                if i > 0:
                    s += cond[i - 1]
                    if pinned[i - 1]:
                        rhs[i] += cond[i - 1] * melt_c
                    else:
                        lower[i] = -cond[i - 1]
                if i < n - 1:
                    s += cond[i]
                    if pinned[i + 1]:
                        rhs[i] += cond[i] * melt_c
                    else:
                        upper[i] = -cond[i]
                if i == 0:
                    s += g0
                if i == n - 1:
                    s += gN
                # The phi term CANCELS on a node whose phase is not changing:
                #   C(T' - Tm) + phi L = C(T - Tm) + phi L + dt Q
                # so the linear system is the sensible one and the latent book
                # keeping happens in the projection below, where E is inverted.
                diag[i] = cap[i] / dt_s - b_src[i] + s
                rhs[i] += cap[i] / dt_s * temp_old[i] + a_src[i]
                if g0 and i == 0:
                    rhs[i] += g0 * ends_c
                if gN and i == n - 1:
                    rhs[i] += gN * ends_c
            x = _thomas(diag, lower, upper, rhs)
            if x is None:
                return None, err("the axial enthalpy system went singular at t = "
                                 "%.4g s (generation has outrun conduction)" % t,
                                 "SOLVER_SINGULAR")
            for v in x:
                if v != v or v in (float("inf"), float("-inf")):
                    return None, err("non-finite temperature at t = %.4g s" % t,
                                     "SOLVER_DIVERGED")
            if _it == 0:
                # The first solve is the UNPINNED one, so it still carries how far
                # past melt_c this step would have gone. Once a node is pinned its
                # solve returns exactly melt_c, which would collapse the sub-step
                # interpolation to whole steps - so keep the first one for that.
                x_first = list(x)
            for i in range(n):
                if pinned[i]:
                    q = a_src[i] + b_src[i] * melt_c
                    if i > 0 and not pinned[i - 1]:
                        q += cond[i - 1] * (x[i - 1] - melt_c)
                    if i < n - 1 and not pinned[i + 1]:
                        q += cond[i] * (x[i + 1] - melt_c)
                    if i == 0 and g0:
                        q += g0 * (ends_c - melt_c)
                    if i == n - 1 and gN:
                        q += gN * (ends_c - melt_c)
                    e_new = phi_old[i] * lat[i] + dt_s * q
                    f = e_new / lat[i]
                    if f <= 0.0:
                        newphi[i] = 0.0
                        newtemp[i] = melt_c + e_new / cap[i]
                    elif f >= 1.0:
                        newphi[i] = 1.0
                        newtemp[i] = melt_c + (e_new - lat[i]) / cap[i]
                    else:
                        newphi[i] = f
                        newtemp[i] = melt_c
                    continue
                e_new = cap[i] * (x[i] - melt_c) + phi_old[i] * lat[i]
                if lat[i] <= 0.0:
                    # no reservoir on this node: plain sensible temperature, and it
                    # may pass melt_c without any phase change (the L = 0 control)
                    newphi[i] = 0.0
                    newtemp[i] = x[i]
                    continue
                # E = C (T - Tm) + phi L is INVERTIBLE, so the projection from the
                # solved energy is unique and needs no phase test: E < 0 is solid,
                # E > L is liquid, anything between is a node ON the plateau.
                f = e_new / lat[i]
                if f <= 0.0:
                    newphi[i] = 0.0
                    newtemp[i] = melt_c + e_new / cap[i]
                elif f >= 1.0:
                    newphi[i] = 1.0
                    newtemp[i] = melt_c + (e_new - lat[i]) / cap[i]
                else:
                    newphi[i] = f
                    newtemp[i] = melt_c
            trial = [1 if (lat[i] > 0.0 and 0.0 < newphi[i] < 1.0) else 0
                     for i in range(n)]
            if trial == pinned:
                break
            pinned = trial
        for v in newtemp:
            if v != v or v in (float("inf"), float("-inf")):
                return None, err("non-finite temperature at t = %.4g s" % t,
                                 "SOLVER_DIVERGED")
        for i in range(n):
            if temp_old[i] < melt_c and (newtemp[i] >= melt_c or newphi[i] > 0.0):
                base = temp_old[i]
                src = x_first[i] if x_first is not None else x[i]
                top = src if src > base else base
                frac = 1.0 if top <= base else (melt_c - base) / (top - base)
                frac = min(1.0, max(0.0, frac))
                tt = t + frac * dt_s
                if t_onset is None or tt < t_onset:
                    t_onset, i_onset = tt, i
            if lat[i] > 0.0 and phi_old[i] < 1.0 and newphi[i] >= 1.0:
                den = newphi[i] - phi_old[i]
                frac = 1.0 if den <= 0.0 else (1.0 - phi_old[i]) / den
                frac = min(1.0, max(0.0, frac))
                tt = t + frac * dt_s
                if t_full is None or tt < t_full:
                    t_full, i_full = tt, i
        # Stop where the sensible model stops: on the event, with the state left at
        # the last COMPLETED step (that is what _taper_run returns, and with
        # latent_j_kg = 0 this makes the two profiles bit-identical).
        if t_full is not None:
            break
        if t_onset is not None and not any(v > 0.0 for v in lat):
            # nothing to melt THROUGH on a reservoir this model does not have
            break
        for i in range(n):
            gen_total += dt_s * (a_src[i] + b_src[i] * newtemp[i])
        if g0:
            flux_total += dt_s * g0 * (ends_c - newtemp[0])
        if gN:
            flux_total += dt_s * gN * (ends_c - newtemp[n - 1])
        temp = newtemp
        phi = newphi
        t += dt_s
    e_end = sum(cap[i] * (temp[i] - melt_c) + phi[i] * lat[i] for i in range(n))
    denom = abs(gen_total)
    if denom <= 0.0:
        denom = 1.0
    return {"melted": t_full is not None,
            "t_melt_s": t_full,
            "melt_segment": i_full,
            "reached_melt_point": t_onset is not None,
            "t_onset_s": t_onset,
            "onset_segment": i_onset,
            "temperature_c": temp,
            "melt_fraction": phi,
            "at_s": t,
            "steps": nsteps,
            "dt_s": dt_s,
            "conduction_delay_pct": None,
            "energy_balance_rel": abs((e_end - e_start) -
                                      (gen_total - flux_total)) / denom,
            "active_set_iters_max": max_iters}, None

def _taper_steady(g, current_a, ends_c, k_w_mk, alpha, rho_m, cp, rho20):
    """Steady state with FIXED terminals: peak temperature, or None if singular.

    With insulated ends there is no steady state at all for I > 0 (nothing removes
    heat), so this only exists when ends_c is supplied - which is exactly the
    question "below what current does it stop heating".
    """
    seg = g["segments"]
    n = len(seg)
    cap, cond, g0, gN, a_src, b_src = _taper_coeffs(
        g, current_a, k_w_mk, ends_c, alpha, rho_m, cp, rho20)
    diag = [0.0] * n
    lower = [0.0] * n
    upper = [0.0] * n
    rhs = [0.0] * n
    for i in range(n):
        s = 0.0
        if i > 0:
            lower[i] = -cond[i - 1]
            s += cond[i - 1]
        if i < n - 1:
            upper[i] = -cond[i]
            s += cond[i]
        if i == 0:
            s += g0
        if i == n - 1:
            s += gN
        diag[i] = -b_src[i] + s
        rhs[i] = a_src[i]
    if g0:
        rhs[0] += g0 * ends_c
    if gN:
        rhs[n - 1] += gN * ends_c
    # A non-positive diagonal means the generation term has already outrun the
    # conduction term: the steady matrix is no longer an M-matrix and there IS no
    # steady state. Solving it anyway returns a negative temperature, which is the
    # classic way to get a confident wrong branch, so say so instead.
    if min(diag) <= 0.0:
        return None
    x = _thomas(diag, lower, upper, rhs)
    if x is None:
        return None
    if any(v != v or v > 1e6 for v in x):
        return None
    return max(x)


def _taper_steady_threshold(g, melt_c, ends_c, k_w_mk, alpha, rho_m, cp, rho20):
    """Bisect the current whose steady peak sits exactly at the melt point."""
    lo, hi = 1e-9, 1.0
    for _ in range(60):
        peak = _taper_steady(g, hi, ends_c, k_w_mk, alpha, rho_m, cp, rho20)
        if peak is None or peak >= melt_c:
            break
        lo, hi = hi, hi * 4.0
    else:
        return None
    for _ in range(120):
        mid = 0.5 * (lo + hi)
        peak = _taper_steady(g, mid, ends_c, k_w_mk, alpha, rho_m, cp, rho20)
        if peak is None or peak >= melt_c:
            hi = mid
        else:
            lo = mid
        if hi / lo - 1.0 < 1e-9:
            break
    return 0.5 * (lo + hi)


def fusing_current_taper(segments, thickness_um=None, copper_oz=1.0, t_ambient_c=25.0,
                         alpha_per_k=ALPHA_234_CU, k_thermal_w_mk=K_THERMAL_CU,
                         ends_c=None, current_a=None, t_melt_s=None, t_end_s=10.0,
                         dt_s=None, max_steps=200000, melt_c=MELT_CU_C,
                         rho_mass_kg_m3=RHO_MASS_CU, cp_j_kgk=CP_CU,
                         rho20_ohm_m=RHO20_CU, melting_model="sensible",
                         latent_heat_j_kg=COPPER_LATENT_HEAT_J_PER_KG):
    """Fusing current (or time to melt) for a trace whose WIDTH CHANGES along it.

    Two questions, pick one and give the other as its answer:

      * current_a given  -> how long until the hottest segment reaches melt
      * t_melt_s given   -> what current melts it in that time (bisection)

    `melting_model` picks which of those two the answer is, exactly as on
    fusing_current:
      * "sensible" (default) - stop where a node reaches melt_c. This is what every
        printed fusing table means, and it is bit-for-bit what this tool printed
        before 0.0.88.
      * "sensible_plus_latent" - carry the fusion enthalpy as a per-node reservoir
        and answer "when is that node FULLY liquid". `t_onset_s` is still when it
        first reaches melt_c (so the two models meet at that number, and they do, to
        the last digit), and `melt_delay_pct` is what melting costs on top.

    What the model is, in one line: one isothermal node per axial segment, joined by
    each pair's two half-lengths in series, copper's own rho(T), backward Euler. No
    fitted constants - it is the same physics as repetitive_pulse_rating minus the
    cooling to air/board and plus the length axis.

    Read the answer as one-sided, and in a direction that FLIPS with the geometry:
    deleting cooling to air/board makes the number low (it really can shed heat
    sideways), while making each cross-section isothermal makes it high (a narrow
    neck really runs hotter at its edges than at its centre). Which error wins
    depends on the aspect ratio, so no blanket sign is claimed.

    `ends_c` is the temperature of the two terminals (degC). Give it and the ends
    stop being insulated, which is what makes a steady state exist at all - and then
    `steady_threshold_a` (the current whose steady peak is the melt point) becomes a
    real number instead of "there is none".
    """
    try:
        copper_oz = float(copper_oz)
        t_ambient_c = float(t_ambient_c)
        alpha_per_k = float(alpha_per_k)
        k_thermal_w_mk = float(k_thermal_w_mk)
        t_end_s = float(t_end_s)
        max_steps = int(max_steps)
        melt_c = float(melt_c)
        rho_mass_kg_m3 = float(rho_mass_kg_m3)
        cp_j_kgk = float(cp_j_kgk)
        rho20_ohm_m = float(rho20_ohm_m)
        latent_heat_j_kg = float(latent_heat_j_kg)
    except (TypeError, ValueError):
        return err("numeric arguments required", "ARG_INVALID")
    if latent_heat_j_kg < 0:
        return err("latent_heat_j_kg must be >= 0", "ARG_RANGE")
    if melting_model is None:
        melting_model = "sensible"
    if melting_model not in MELTING_MODELS:
        return err("melting_model must be one of %s" % (", ".join(MELTING_MODELS),),
                   "ARG_INVALID",
                   hint="sensible stops at the melt point (Onderdonk); "
                        "sensible_plus_latent pays the fusion enthalpy and answers "
                        "melt-through")
    latent = melting_model == "sensible_plus_latent"
    if thickness_um is None:
        thick_um = copper_oz * OZ_TO_UM
    else:
        try:
            thick_um = float(thickness_um)
        except (TypeError, ValueError):
            return err("thickness_um must be numeric", "ARG_INVALID")
    if not (thick_um > 0):
        return err("copper thickness must be > 0", "ARG_RANGE")
    if alpha_per_k <= 0:
        return err("alpha_per_k must be > 0: with no resistivity rise there is no "
                   "runaway and this model has no melt criterion to solve", "ARG_RANGE",
                   hint="copper's own value is 1/254 = %.6f" % ALPHA_234_CU)
    if k_thermal_w_mk < 0:
        return err("k_thermal_w_mk must be >= 0 (0 = conduction switched off)",
                   "ARG_RANGE")
    if t_ambient_c <= -ONDERDONK_REF_C:
        return err("t_ambient_c must be above -234 C", "ARG_RANGE")
    if not (t_ambient_c < melt_c):
        return err("melt_c must be above t_ambient_c", "ARG_RANGE")
    if melt_c >= 1e5:
        return err("melt_c is not a temperature", "ARG_RANGE")
    if ends_c is not None:
        try:
            ends_c = float(ends_c)
        except (TypeError, ValueError):
            return err("ends_c must be numeric (the terminal temperature in C)",
                       "ARG_INVALID")
        if ends_c <= -ONDERDONK_REF_C:
            return err("ends_c must be above -234 C", "ARG_RANGE")
        if not (ends_c < melt_c):
            return err("ends_c must be below melt_c: a terminal already at the melt "
                       "point answers nothing", "ARG_RANGE")
    if (current_a is None) == (t_melt_s is None):
        return err("give exactly one of current_a (-> time to melt) or t_melt_s "
                   "(-> current that melts in that time)", "ARG_REQUIRED",
                   hint="the two are the same curve read in opposite directions")
    g, e = _taper_geometry(segments, thick_um)
    if e:
        return e
    if not (rho_mass_kg_m3 > 0 and cp_j_kgk > 0 and rho20_ohm_m > 0):
        return err("rho_mass_kg_m3, cp_j_kgk and rho20_ohm_m must all be > 0",
                   "ARG_RANGE")
    n = len(g["segments"])

    def _run(i_amp, t_window, dt, k_w=None, ends="__keep__"):
        """One march under whichever melting model was asked for."""
        if k_w is None:
            k_w = k_thermal_w_mk
        if ends == "__keep__":
            ends = ends_c
        if latent:
            return _taper_run_latent(g, i_amp, t_window, dt, melt_c, t_ambient_c,
                                     ends, k_w, alpha_per_k, rho_mass_kg_m3,
                                     cp_j_kgk, rho20_ohm_m, latent_heat_j_kg)
        return _taper_run(g, i_amp, t_window, dt, melt_c, t_ambient_c, ends, k_w,
                          alpha_per_k, rho_mass_kg_m3, cp_j_kgk, rho20_ohm_m)

    beta_amb = None
    if latent:
        beta_amb = _taper_latent_share(t_ambient_c, melt_c, alpha_per_k, cp_j_kgk,
                                       latent_heat_j_kg)
        if beta_amb is None:
            return err("the latent share is not defined for these t_ambient_c / "
                       "melt_c", "ARG_RANGE")

    asked_time = current_a is None
    if current_a is not None:
        try:
            current_a = float(current_a)
        except (TypeError, ValueError):
            return err("current_a must be numeric", "ARG_INVALID")
        if current_a <= 0:
            return err("current_a must be > 0", "ARG_RANGE")
        if not (t_end_s > 0):
            return err("t_end_s must be > 0", "ARG_RANGE")
        dt = float(dt_s) if dt_s is not None else t_end_s / 2000.0
        if not (dt > 0):
            return err("dt_s must be > 0", "ARG_RANGE")
        if dt > t_end_s:
            dt = t_end_s
        steps = int(math.ceil(t_end_s / dt - 1e-9))
        if steps > max_steps:
            return err("this would take %d steps (max_steps = %d)" % (steps, max_steps),
                       "ARG_RANGE", hint="raise dt_s or max_steps")
        run, e = _run(current_a, t_end_s, dt)
        if e:
            return e
    else:
        try:
            t_melt_target = float(t_melt_s)
        except (TypeError, ValueError):
            return err("t_melt_s must be numeric", "ARG_INVALID")
        if not (t_melt_target > 0):
            return err("t_melt_s must be > 0", "ARG_RANGE")
        dt = float(dt_s) if dt_s is not None else t_melt_target / 2000.0
        if not (dt > 0):
            return err("dt_s must be > 0", "ARG_RANGE")
        if dt > t_melt_target:
            dt = t_melt_target
        steps = int(math.ceil(t_melt_target / dt - 1e-9))
        if steps > max_steps:
            return err("this would take %d steps (max_steps = %d)" % (steps, max_steps),
                       "ARG_RANGE", hint="raise dt_s or max_steps")
        # lower bracket: the adiabatic current of the NARROWEST segment, which melts
        # first with no conduction at all -> conduction can only push the answer up
        narrow = min(s["area_m2"] for s in g["segments"])
        i_lo = _taper_adiabatic_current(narrow, t_melt_target, t_ambient_c, melt_c,
                                        alpha_per_k, rho_mass_kg_m3, cp_j_kgk,
                                        rho20_ohm_m)
        if i_lo is None or not (i_lo > 0):
            return err("the adiabatic lower bracket came out non-positive; check "
                       "melt_c / t_ambient_c", "INTERNAL")
        i_lo *= 0.999
        if latent:
            # the adiabatic anchor is the time to REACH melt_c; melting through
            # adds exactly (1+beta) to I^2t, i.e. divides the current by its root
            i_lo /= math.sqrt(1.0 + beta_amb)

        def melts(i_test):
            r, ee = _run(i_test, t_melt_target, dt)
            if ee:
                return ee, None
            return None, (r["melted"] and r["t_melt_s"] <= t_melt_target)

        # expand the top of the bracket until it melts in time
        for _ in range(40):
            ee, hit = melts(i_lo)
            if ee:
                return ee
            if not hit:
                break
            i_lo *= 0.5
        else:
            return err("could not find a current that does NOT melt in time",
                       "INTERNAL")
        i_hi = i_lo * 4.0
        for _ in range(40):
            ee, hit = melts(i_hi)
            if ee:
                return ee
            if hit:
                break
            i_hi *= 4.0
        else:
            return err("no current up to %.3g A melts this in %.4g s" % (i_hi, t_melt_target),
                       "NO_SOLUTION",
                       hint="that cannot happen with alpha > 0 unless dt_s is too big "
                            "to resolve the rise; try a smaller dt_s")
        lo, hi = i_lo, i_hi
        for _ in range(60):
            mid = math.sqrt(lo * hi)
            ee, hit = melts(mid)
            if ee:
                return ee
            if hit:
                hi = mid
            else:
                lo = mid
            if hi / lo - 1.0 < 1e-9:
                break
        current_a = math.sqrt(lo * hi)
        # 2 % past the target so the crossing is strictly INSIDE the last step: at the
        # bracket point itself the node lands on melt_c exactly and "crossed" is
        # ambiguous, which would report melted=false on a converged answer
        run, e = _run(current_a, t_melt_target * 1.02, dt)
        if e:
            return e

    # --- the adiabatic control: same current, conduction and terminals switched off
    if run["melted"]:
        ctrl_window = max(run["at_s"], run["t_melt_s"] * 1.02)
    elif asked_time:
        ctrl_window = float(t_melt_s)
    else:
        ctrl_window = t_end_s
    ctrl, e = _run(current_a, ctrl_window, dt, k_w=0.0, ends=None)
    delay = None
    if not e and run["melted"] and ctrl["melted"] and ctrl["t_melt_s"] > 0:
        delay = 100.0 * (run["t_melt_s"] - ctrl["t_melt_s"]) / ctrl["t_melt_s"]
        run["conduction_delay_pct"] = delay

    seg = g["segments"]
    profile = []
    for i in range(n):
        row = {"segment": i, "x_mm": seg[i]["x_mm"],
               "width_mm": seg[i]["width_mm"],
               "t_c": round(run["temperature_c"][i], 4)}
        if latent:
            row["melt_fraction"] = round(run["melt_fraction"][i], 6)
        profile.append(row)
    hot = max(range(n), key=lambda i: run["temperature_c"][i])
    extra = {
        "melted": run["melted"],
        "t_melt_s": (round(run["t_melt_s"], 6) if run["melted"] else None),
        "melt_at_mm": (seg[run["melt_segment"]]["x_mm"]
                       if run["melt_segment"] is not None else None),
        "melt_segment": run["melt_segment"],
        "melt_c": round(melt_c, 3),
        "current_a": round(current_a, 6),
        "peak_c": round(run["temperature_c"][hot], 4),
        "peak_segment": hot,
        "peak_at_s": round(run["at_s"], 6),
        "margin_to_melt_k": round(melt_c - run["temperature_c"][hot], 4),
        "segment_count": n,
        "length_total_mm": g["total_mm"],
        "narrowest_width_mm": g["narrowest_width_mm"],
        "thickness_um": round(thick_um, 3),
        "copper_oz": round(thick_um / OZ_TO_UM, 3),
        "t_ambient_c": round(t_ambient_c, 2),
        "alpha_per_k": alpha_per_k,
        "k_thermal_w_mk": k_thermal_w_mk,
        "ends_c": (round(ends_c, 3) if ends_c is not None else None),
        "ends_are": ("fixed at ends_c" if ends_c is not None else
                     "insulated (no heat leaves through the ends)"),
        "dt_s": dt,
        "steps": run["steps"],
        "time_resolution_s": dt,
        "temperature_profile_c": profile,
        "adiabatic_control": {
            "conduction": "off (k_thermal_w_mk = 0, ends insulated)",
            "melted": ctrl["melted"] if not e else None,
            "t_melt_s": (round(ctrl["t_melt_s"], 6)
                         if (not e and ctrl["melted"]) else None),
            "conduction_delay_pct": (round(delay, 3) if delay is not None else None),
            "why_null": (None if delay is not None else
                         "no pair of melt times to compare (one of the two runs did "
                         "not melt within its window, or the control itself errored)"),
        },
    }
    if latent:
        t_on = run["t_onset_s"]
        t_full = run["t_melt_s"]
        extra["melting_model"] = melting_model
        extra["latent_rise_k"] = round(latent_heat_j_kg / cp_j_kgk, 6)
        extra["latent_share_of_i2t"] = round(beta_amb, 9)
        extra["reached_melt_point"] = run["reached_melt_point"]
        extra["t_onset_s"] = round(t_on, 6) if t_on is not None else None
        extra["onset_at_mm"] = (seg[run["onset_segment"]]["x_mm"]
                               if run["onset_segment"] is not None else None)
        extra["fully_melted"] = run["melted"]
        extra["melt_delay_pct"] = (
            round(100.0 * (t_full - t_on) / t_on, 3)
            if (t_full is not None and t_on) else None)
        extra["melt_delay_note"] = (
            "how much LONGER a full melt takes than merely reaching melt_c, at this "
            "current and this geometry. This is NOT bounded by the adiabatic "
            "latent_share_of_i2t (25.31 % at 25 C): conduction lengthens the onset "
            "AND taxes the plateau, and it does the two unequally, so the ratio can "
            "land either side of that share. Measured, dt = 2e-4 s, ends_c = 25: on "
            "a uniform 10 mm x 0.5 mm trace (k = 400) it is 76.9 % at 8 A, 35.96 % at "
            "10 A, 28.95 % at 14 A, 26.91 % at 20 A, 25.91 % at 26 A, 26.54 % at "
            "30 A, 25.70 % at 40 A - steep where conduction dominates the onset, then "
            "hovering either side of the share. The same neck with insulated ends "
            "stays well under it (8.46 % at 4 A, 12.51 % at 50 A). Read this as "
            "output, not as a correction to that anchor")
        extra["t_melt_s_means"] = (
            "the first node is FULLY liquid. t_onset_s is when a node first reaches "
            "melt_c, and that is exactly what the sensible model returns")
        extra["energy_balance_rel"] = run["energy_balance_rel"]
        extra["active_set_iters_max"] = run["active_set_iters_max"]
    if ends_c is not None:
        thr = _taper_steady_threshold(g, melt_c, ends_c, k_thermal_w_mk, alpha_per_k,
                                      rho_mass_kg_m3, cp_j_kgk, rho20_ohm_m)
        extra["steady_threshold_a"] = (round(thr, 6) if thr is not None else None)
        extra["steady_threshold_note"] = (
            "current whose STEADY peak (terminals fixed, no time stepping) is exactly "
            "melt_c; above it no steady state exists and the trace melts. This is the "
            "1-D fin solution, so it is exact for this discretisation"
            if thr is not None else
            "could not bracket a steady threshold inside 60 doublings from 1 A")
    else:
        extra["steady_threshold_a"] = None
        extra["steady_threshold_note"] = (
            "none exists: with insulated ends nothing removes heat at steady state, so "
            "for any positive current the temperature keeps rising. Pass ends_c to ask "
            "the question with an answer")

    if asked_time:
        value, unit = round(current_a, 6), "A"
    elif run["melted"]:
        value, unit = round(run["t_melt_s"], 6), "s"
    else:
        value, unit = None, "s"

    return ok(
        value=value,
        unit=unit,
        model="taper_1d_axial_conduction_fusing",
        basis="1-D axial finite volume on segment centres: C_i dT_i/dt = I^2 R_i(T_i) "
              "+ sum_j G_ij (T_j - T_i), R_i = rho(T_i) L_i / A_i, G_ij = the two "
              "half-lengths in series, rho(T) = rho20 (1 + alpha (T - 20)) with "
              "alpha = 1/254 (copper's own 234 law, the same 234 as Onderdonk 1928); "
              "backward Euler + Thomas, melt at the point where a node crosses melt_c"
              + ("; each node also carries an enthalpy state E = C (T - melt_c) + phi L "
                 "with phi its molten fraction, so a melting node is PINNED at melt_c "
                 "(an exact plateau - no mushy band, nothing fitted) and the answer is "
                 "the time to melt THROUGH" if latent else ""),
        precision="+/-25% and two one-sided choices that point in OPPOSITE directions: "
                  "cooling to air/board is deleted (only axial conduction survives), "
                  "which makes the number low, while each cross-section is one "
                  "temperature, which misses the edge crowding at a narrow neck and "
                  "makes it high (current_density_field solves that 2-D crowding on "
                  "the same neck, so if the neck is where it melts, measure it there "
                  "and feed the result back as a geometry change). k_thermal_w_mk is yours (400 is bulk copper; a thin "
                  "film, a plated layer or a trace over a void is worse). "
                  + ("Latent heat IS modelled (per-node enthalpy, exact plateau), so "
                     "the melt time is the time to melt through; what is still missing "
                     "is what happens after that (no melt flow, no volume change), and "
                     "time is resolved to dt_s - every melt time is interpolated "
                     "within a step, so the latent delay is only as sharp as dt_s"
                     if latent else
                     "Latent heat is not modelled (melt point, not molten: 20.2 % "
                     "short at 25 C - melting_model='sensible_plus_latent' answers "
                     "that), and time is resolved to dt_s - both melt times in the "
                     "answer are interpolated within a step"),
        assumptions={
            "material": "copper: rho20 = %.3g ohm*m, rho_m = %.4g kg/m^3, "
                        "c_p = %.4g J/(kg*K), melt %.0f C, k = %.4g W/(m*K)"
                        % (rho20_ohm_m, rho_mass_kg_m3, cp_j_kgk, melt_c, k_thermal_w_mk),
            "spatial": "1-D along the trace. Each segment is isothermal over its whole "
                       "cross-section and its length is lumped to one node at the "
                       "centre; no transverse gradient, so a narrow neck's hot edges "
                       "are not resolved",
            "cooling": "axial conduction only. No convection, no radiation, no board, "
                       "no plane, no solder - %s" % (
                           "the two ends are pinned at ends_c"
                           if ends_c is not None else
                           "and the ends are insulated, so nothing leaves"),
            "phase_change": (
                "latent heat is modelled exactly (0.0.88): %g J/kg of fusion energy, "
                "stored per node as E = C (T - melt_c) + phi L, phi in [0, 1]. A node "
                "on the plateau is pinned at melt_c, so there is no mushy band and no "
                "artificial width to tune. Not modelled: the volume change on melting, "
                "melt flow after the copper opens, and any mushy-zone physics inside a "
                "real alloy" % latent_heat_j_kg
                if latent else
                "not modelled (melt point only, no latent heat)"),
            "waveform": "constant current; a pulse of the same RMS is not equivalent",
            "skin_effect": "not modelled",
            "geometry": "rectangular segments of constant width, joined end to end; "
                        "corner heating, current crowding at the step and the extra "
                        "surface of a step are not modelled",
        },
        extra=extra,
    )
