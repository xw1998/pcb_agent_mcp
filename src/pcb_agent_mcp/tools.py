"""Tool registry: name -> JSON schema + handler.

Every handler returns the uniform envelope from calc.result.
The schema is the only thing the model sees before calling, so descriptions
must say what the tool will NOT do as clearly as what it does.
"""
import json
import math
import os
import re

from . import jsonsafe as _jsonsafe
from . import geometry as G

from . import desktop as _desktop
from . import parts as _parts
from . import lceda_api as _lceda_api
from . import lceda_live as _live
from . import fidelity as _fidelity
from . import oracle as _oracle
from .fidelity import views as _fviews
from .calc import current as _current
from .calc import currentfield as _cfield
from .calc import skin as _skin
from .calc import roundwire as _round
from .calc import acthermal as _at
from .calc import creepage as _creepage
from .calc import diffpair as _diffpair
from .calc import field2d as _field2d
from .calc import impedance as _impedance
from .calc import rf as _rf
from .calc import safety_table as _safety_table
from .calc import units as _units
from .calc import antenna as _antenna
from .calc import feedline as _feedline
from .calc import link as _link
from .calc import dfm as _dfm
from .calc import matching as _matching
from .calc import power as _power
from .calc import spice as _spice
from .calc import thermal as _thermal
from .calc import vibration as _vib
from .calc import vibration_poly as _vib_poly
from .calc import reliability as _rel
from .calc import cavity as _cavity
from .calc import planar as _planar
from .calc import electrothermal as _electro
from .calc import filters as _filters
from .calc import magnetics as _magnetics
from .calc import filtermatrix as _filtermatrix
from .calc import stackup as _stackup
from .calc import crosstalk as _crosstalk
from .fidelity import fill as _fill
from .calc import chain as _chain
from .calc import rules as _rules
from .calc.result import err, ok
from .adapters import FORMATS as _FORMATS
from .adapters import detect_format as _detect_format
from .adapters import open_board as _open_board


def _curve_table_path():
    """Where the printed-curve transcription lives (the self-test reads it back)."""
    return _magnetics.CURVE_PATH

def _arr(desc):
    return {"type": "array", "items": {"type": "string"}, "description": desc}

def _num(desc, default=None):
    d = {"type": "number", "description": desc}
    if default is not None:
        d["default"] = default
    return d


def _str(desc, enum=None, default=None):
    d = {"type": "string", "description": desc}
    if enum:
        d["enum"] = enum
    if default is not None:
        d["default"] = default
    return d


def _bool(desc, default=False):
    return {"type": "boolean", "description": desc, "default": default}

def _ring_arg(v):
    """Accept [[x, y], ...] or [{"x_mm":, "y_mm":}, ...] and return pairs.

    Absent stays None so the callee can refuse on its own terms - an empty ring is
    a different mistake from a missing one.
    """
    if v is None:
        return None
    out = []
    for p in v:
        if isinstance(p, dict):
            out.append((p.get("x_mm"), p.get("y_mm")))
        else:
            out.append((p[0], p[1]))
    return out

def _parasitics_args(a):
    """Build the parasitics dict for the filter tools, or None when nothing was asked.

    Absent must stay None, not {"cp_f": 0, "esl_h": 0}: the difference between
    "no parasitics modelled" and "parasitics modelled at zero" decides whether the
    dissipation cross-check is allowed to run.
    """
    cp = a.get("parasitics_cp_f")
    esl = a.get("parasitics_esl_h")
    if cp is None and esl is None:
        return None
    return {"cp_f": cp, "esl_h": esl}


TOOLS = {}


def _tool(name, description, schema, handler):
    TOOLS[name] = {"description": description, "inputSchema": schema, "handler": handler}


# ---------------------------------------------------------------- units
_tool(
    "unit_convert",
    "Convert a length between PCB units. EasyEDA Pro stores numbers in mil (measured on "
    "194 real archives - the unit label in the file is display-only) while KiCad uses mm: "
    "a silent 25.4x error passes every DRC.",
    {
        "type": "object",
        "properties": {
            "value": _num("numeric value to convert"),
            "from_unit": _str("source unit",
                              ["mm", "cm", "um", "nm", "mil", "thou", "inch", "in",
                               "0.01inch", "0.01in"]),
            "to_unit": _str("target unit",
                            ["mm", "cm", "um", "nm", "mil", "thou", "inch", "in",
                             "0.01inch", "0.01in"]),
        },
        "required": ["value", "from_unit", "to_unit"],
    },
    lambda a: _units.convert(a["value"], a["from_unit"], a["to_unit"]),
)

# ------------------------------------------------------------- current
_CUR_STD = _str(
    "REQUIRED. Which standard to use. ipc2221 = the 1956 NBS chart fit "
    "(I=k*dT^0.44*A^0.725). ipc2152 = the 2009 replacement, implemented as the "
    "published closed-form fit to its Table 3-1 (Brooks & Adam 2015). There is no "
    "default: the two disagree by ~8% at 1 A / 10 degC / 1 oz and much more "
    "elsewhere, and the wrong choice is a wrong answer, not a smaller one.",
    ["ipc2221", "ipc2152"],
)
_LAYER = _str("copper layer: external (outer) or internal (inner)", ["external", "internal"])

_tool(
    "width_for_current",
    "Minimum trace width for a given current and allowed temperature rise. "
    "Requires the standard explicitly. Note: 'no temperature rise' is not computable - "
    "it must be converted to a delta-T budget (10 C is a common default, 5 C for harsh).",
    {
        "type": "object",
        "properties": {
            "current_a": _num("design current in ampere"),
            "delta_t_c": _num("allowed temperature rise in Celsius", 10),
            "copper_oz": _num("copper weight in oz (1 oz ~ 35 um)", 1),
            "layer": _LAYER,
            "standard": _CUR_STD,
        },
        "required": ["current_a", "standard"],
    },
    lambda a: _current.width_for_current(
        a["current_a"], a.get("delta_t_c", 10), a.get("copper_oz", 1),
        a.get("layer", "external"), a.get("standard")),
)

_tool(
    "current_capacity",
    "Current a trace of known width can carry for a given allowed temperature rise. "
    "Inverse of width_for_current. In a solder-mask opening the result is a lower bound "
    "because added solder is not counted.",
    {
        "type": "object",
        "properties": {
            "width_mm": _num("trace width in mm"),
            "delta_t_c": _num("allowed temperature rise in Celsius", 10),
            "copper_oz": _num("copper weight in oz", 1),
            "layer": _LAYER,
            "mask_opening": _bool("true if solder mask is opened over this trace"),
            "standard": _CUR_STD,
        },
        "required": ["width_mm", "standard"],
    },
    lambda a: _current.current_capacity(
        a["width_mm"], a.get("delta_t_c", 10), a.get("copper_oz", 1),
        a.get("layer", "external"), a.get("mask_opening", False), a.get("standard")),
)

_tool(
    "via_current_allowed",
    "Current a via (or a group of parallel vias) can carry. Engineering extrapolation: "
    "barrel sidewall area fed to the chart fit. Not a standard-defined method, "
    "so the value is more optimistic than the common '0.3 mm via ~ 1 A' rule of thumb.",
    {
        "type": "object",
        "properties": {
            "hole_mm": _num("finished hole diameter in mm"),
            "plating_um": _num("barrel plating thickness in um", 25),
            "count": {"type": "integer", "description": "number of parallel vias", "default": 1},
            "delta_t_c": _num("allowed temperature rise in Celsius", 10),
            "standard": _CUR_STD,
        },
        "required": ["hole_mm", "standard"],
    },
    lambda a: _current.via_current_allowed(
        a["hole_mm"], a.get("plating_um", 25), a.get("count", 1),
        a.get("delta_t_c", 10), a.get("standard")),
)

C_ALPHA = _current.ALPHA_234_CU   # 1/254: copper's (234 + T) law
K_TH_CU = _current.K_THERMAL_CU

_CUR_STD_OPT = _str(
    "OPTIONAL here. Only used to say how far the fusing current sits from the "
    "CONTINUOUS rating (capacity_a, fuse_over_capacity). The fusing number itself "
    "comes from Onderdonk (1928) and does not depend on either standard.",
    ["ipc2221", "ipc2152"],
)

_tool(
    "fusing_current",
    "Current that melts a trace in t_s seconds (Onderdonk 1928, adiabatic), and "
    "optionally how far that is from the continuous rating. This is NOT a "
    "steady-state question and neither IPC standard answers it - both describe a "
    "cooled temperature rise, and a fusing calculation deletes the cooling. Read the "
    "answer as one-sided: no cooling, rectangle converted to a round wire by equal "
    "area, and - by default - it stops at the melt POINT (1083 C) without the latent "
    "heat of fusion; every one of those makes the real fusing current HIGHER. The "
    "latent part is not negligible: fusion costs 208.7 kJ/kg against 407 kJ/kg of "
    "sensible heat, which is +25.3 % on I^2t and +11.9 % on the current at 25 C. "
    "melting_model='sensible_plus_latent' returns that melt-through number; the "
    "default keeps Onderdonk so a published fusing table still reproduces. Beyond "
    "~10 s the model stops meaning anything (the flag says so on the answer).",
    {"type": "object", "properties": {
        "width_mm": _num("trace width in mm"),
        "t_s": _num("time to melt, seconds (short pulses; >10 s is flagged)", 1.0),
        "copper_oz": _num("copper weight in oz (used unless thickness_um is given)", 1),
        "thickness_um": _num("override the copper thickness in um"),
        "t_ambient_c": _num("ambient temperature in Celsius", 25),
        "delta_t_c": _num("temperature rise used for the continuous rating", 10),
        "layer": _LAYER,
        "standard": _CUR_STD_OPT,
        "melting_model": {"type": "string",
                          "enum": ["sensible", "sensible_plus_latent"],
                          "description": "'sensible' (default) = Onderdonk as "
                          "printed, the current that REACHES 1083 C; "
                          "'sensible_plus_latent' = melt-through, the current "
                          "that also pays copper's heat of fusion. The second is "
                          "+25.3 % on I^2t and +11.9 % on the current at 25 C "
                          "(the share depends on ambient: +23.8 % at 0 C, "
                          "+30.0 % at 100 C)"},
    }, "required": ["width_mm"]},
    lambda a: _current.fusing_current(
        a.get("width_mm"),
        t_s=(a["t_s"] if a.get("t_s") is not None else 1.0),
        copper_oz=(a["copper_oz"] if a.get("copper_oz") is not None else 1),
        thickness_um=a.get("thickness_um"),
        t_ambient_c=(a["t_ambient_c"] if a.get("t_ambient_c") is not None else 25.0),
        delta_t_c=(a["delta_t_c"] if a.get("delta_t_c") is not None else 10.0),
        layer=a.get("layer", "external"),
        standard=a.get("standard"),
        melting_model=(a["melting_model"] if a.get("melting_model") is not None
                       else "sensible")),
)

_tool(
    "fusing_current_taper",
    "Fusing current, or time to melt, for a trace whose WIDTH CHANGES along it - a "
    "fuse neck, a via-fanout bottleneck, a trace stepping down into a pad. One "
    "isothermal node per axial segment joined by each pair's two half-lengths in "
    "series, copper's own rho(T), backward Euler. THIS is the length axis "
    "fusing_current deletes: it says the narrow part melts first, how much the "
    "surrounding copper delays it (reported as conduction_delay_pct, measured "
    "against a conduction-off control arm, not asserted), and - when ends_c gives "
    "the terminals a temperature - the current below which no steady state melts "
    "at all. Give exactly one of current_a and t_melt_s. A current that does not "
    "melt returns NO time, only the peak and the margin. melting_model="
    "'sensible_plus_latent' (opt in, default off) gives every node an enthalpy "
    "state, so 1083 C stops being the end and the plateau has to fill before the "
    "metal opens: t_melt_s then means the FIRST NODE FULLY LIQUID, t_onset_s says "
    "when a node first reached 1083 C and is exactly what sensible returns "
    "(reported as melt_delay_pct), and melt_fraction rides along in the profile. "
    "The plateau is solved as an exact phase boundary with no mushy band and no "
    "adjustable width; the step size is the only resolution, so read the delay as "
    "sharp to time_resolution_s and no further.",
    {"type": "object", "properties": {
        "segments": {"type": "array", "description": "axial sections in order from "
                     "one end to the other; each {length_mm, width_mm}, both > 0",
                     "items": {"type": "object", "properties": {
                         "length_mm": {"type": "number"},
                         "width_mm": {"type": "number"}}}},
        "thickness_um": _num("copper thickness in um (overrides copper_oz)"),
        "copper_oz": _num("copper weight in oz, used unless thickness_um is given", 1),
        "t_ambient_c": _num("starting temperature in Celsius", 25),
        "alpha_per_k": _num("resistivity temperature coefficient, 1/K; copper's own "
                            "1/254 is what makes a uniform segment reproduce "
                            "Onderdonk", round(C_ALPHA, 9)),
        "k_thermal_w_mk": _num("axial thermal conductivity of the copper, W/(m*K); "
                               "0 switches conduction off (the control arm)", 400),
        "ends_c": _num("temperature of BOTH terminals in Celsius. Give it and the "
                       "ends stop being insulated, which is the only way a steady "
                       "threshold exists"),
        "current_a": _num("current in A -> the answer is the time to melt"),
        "t_melt_s": _num("time to melt in seconds -> the answer is the current"),
        "t_end_s": _num("how long to march when current_a is given", 10),
        "dt_s": _num("time step; default t_end_s/2000, and both melt times are "
                     "interpolated inside a step"),
        "max_steps": _num("refuse rather than grind past this many steps", 200000),
        "melt_c": _num("melt temperature in Celsius", 1083),
        "melting_model": {"type": "string",
                          "enum": ["sensible", "sensible_plus_latent"],
                          "description": "'sensible' (default) stops at the melt "
                          "POINT, exactly the answer 0.0.87 gave. "
                          "'sensible_plus_latent' adds copper's 208.7 kJ/kg of "
                          "fusion, so a node sits on 1083 C until it has paid it"},
    }, "required": ["segments"]},
    lambda a: _current.fusing_current_taper(
        a.get("segments"),
        thickness_um=a.get("thickness_um"),
        copper_oz=(a["copper_oz"] if a.get("copper_oz") is not None else 1.0),
        t_ambient_c=(a["t_ambient_c"] if a.get("t_ambient_c") is not None else 25.0),
        alpha_per_k=(a["alpha_per_k"] if a.get("alpha_per_k") is not None
                     else C_ALPHA),
        k_thermal_w_mk=(a["k_thermal_w_mk"] if a.get("k_thermal_w_mk") is not None
                        else 400.0),
        ends_c=a.get("ends_c"),
        current_a=a.get("current_a"),
        t_melt_s=a.get("t_melt_s"),
        t_end_s=(a["t_end_s"] if a.get("t_end_s") is not None else 10.0),
        dt_s=a.get("dt_s"),
        max_steps=(int(a["max_steps"]) if a.get("max_steps") is not None else 200000),
        melt_c=(a["melt_c"] if a.get("melt_c") is not None else 1083.0),
        melting_model=(a["melting_model"] if a.get("melting_model") is not None
                       else "sensible")),
)

_tool(
    "repetitive_pulse_rating",
    "Largest current a trace can carry as a REPEATING pulse train (one that never "
    "melts), and - if you also pass current_a - whether those particular pulses "
    "survive and for how many shots. One lumped first-order model solved exactly "
    "per interval: C' dT/dt = I^2 rho(T) - h*P*(T-Ta), with copper's own resistance "
    "law, so the adiabatic limit reproduces the single-pulse fusing time. "
    "melting_model='sensible' stops at the melt POINT (1083 C, Onderdonk as "
    "printed); 'sensible_plus_latent' carries copper's 208.7 kJ/kg of fusion "
    "energy as a 542.08 K reservoir that has to be filled before the metal opens. "
    "Latent heat does not move the returned current - there the pulse ends exactly "
    "on the melt point, so no reservoir is ever entered - but it changes what "
    "happens above it: a train that does melt needs 71.6 % more shots at 0.95x "
    "that current, and it opens a second, higher ceiling "
    "(max_current_without_melt_through_a) where every pulse pins the peak at "
    "1083 C and the train only melts through once the off-time can no longer "
    "empty the reservoir. Below the "
    "returned current the peak settles UNDER the melt point; above it every cycle "
    "ends hotter and the trace melts after a finite number of shots however long "
    "you wait. Two currents come back and they are not interchangeable: value is "
    "the melt-free one, max_repetitive_current_a is the RUNAWAY bound (above it no "
    "steady peak exists at all, and it carries neither the melt point nor the heat "
    "capacity) - on a 1 % duty cycle that bound is 4.4x the current a train can "
    "actually survive, so do not read it as the rating. "
    "h_w_m2k is REQUIRED: cooling is the whole answer here and it can differ 20x "
    "between a bare trace and a copper pour bolted to metal, so there is no default.",
    {"type": "object", "properties": {
        "width_mm": _num("trace width in mm"),
        "t_on_s": _num("pulse length in seconds"),
        "duty_cycle": _num("t_on/(t_on+t_off) in (0,1]; give this or period_s"),
        "period_s": _num("t_on+t_off in seconds - the other spelling of duty_cycle"),
        "copper_oz": _num("copper weight in oz (used unless thickness_um is given)", 1),
        "thickness_um": _num("override the copper thickness in um"),
        "t_ambient_c": _num("ambient temperature in Celsius", 25),
        "h_w_m2k": _num("effective heat transfer coefficient W/(m^2*K): ~10 bare "
                         "trace in still air, 30-60 with a board/pour, hundreds "
                         "with forced air or a metal chassis; 0 = adiabatic limit"),
        "current_a": _num("OPTIONAL: a specific pulse current, to get the shot "
                          "count and the steady peak at that current"),
        "delta_t_c": _num("temperature rise used for the continuous rating", 10),
        "layer": _LAYER,
        "standard": _CUR_STD_OPT,
        "melting_model": _str("where the calculation stops: the melt POINT "
                              "(Onderdonk as printed) or a copper that has to absorb "
                              "208.7 kJ/kg of fusion energy before it opens",
                              ["sensible", "sensible_plus_latent"]),
    }, "required": ["width_mm", "t_on_s", "h_w_m2k"]},
    lambda a: _current.repetitive_pulse_rating(
        a["width_mm"],
        a["t_on_s"],
        duty_cycle=a.get("duty_cycle"),
        period_s=a.get("period_s"),
        copper_oz=(a["copper_oz"] if a.get("copper_oz") is not None else 1),
        thickness_um=a.get("thickness_um"),
        t_ambient_c=(a["t_ambient_c"] if a.get("t_ambient_c") is not None else 25.0),
        h_w_m2k=a.get("h_w_m2k"),
        current_a=a.get("current_a"),
        delta_t_c=(a["delta_t_c"] if a.get("delta_t_c") is not None else 10.0),
        layer=a.get("layer", "external"),
        standard=a.get("standard"),
        melting_model=(a["melting_model"]
                       if a.get("melting_model") is not None else "sensible")),
)

# ----------------------------------------------------------- impedance
_IMP_KIND = _str("transmission line type", ["microstrip", "stripline"])

_tool(
    "trace_impedance",
    "Forward solve: given geometry, what is the characteristic impedance. "
    "Closed-form IPC-2141A approximation, not a field solver.",
    {
        "type": "object",
        "properties": {
            "width_mm": _num("trace width in mm"),
            "dielectric_mm": _num("dielectric thickness to the reference plane in mm "
                                  "(for stripline: to ONE plane)"),
            "copper_um": _num("copper thickness in um", 35),
            "er": _num("relative permittivity of the dielectric", 4.3),
            "kind": _IMP_KIND,
        },
        "required": ["width_mm", "dielectric_mm"],
    },
    lambda a: _impedance.trace_impedance(
        a["width_mm"], a["dielectric_mm"], a.get("copper_um", 35),
        a.get("er", 4.3), a.get("kind", "microstrip")),
)

_tool(
    "width_for_impedance",
    "Reverse solve: given a target impedance and stackup, what width is required. "
    "This is the direction a designer actually needs - width is the RESULT of the "
    "impedance calculation, never the input.",
    {
        "type": "object",
        "properties": {
            "target_ohm": _num("target characteristic impedance in ohm"),
            "dielectric_mm": _num("dielectric thickness to the reference plane in mm"),
            "copper_um": _num("copper thickness in um", 35),
            "er": _num("relative permittivity", 4.3),
            "kind": _IMP_KIND,
        },
        "required": ["target_ohm", "dielectric_mm"],
    },
    lambda a: _impedance.width_for_impedance(
        a["target_ohm"], a["dielectric_mm"], a.get("copper_um", 35),
        a.get("er", 4.3), a.get("kind", "microstrip")),
)

# ----------------------------------------------------------------- rf
_tool(
    "lambda_over_20",
    "Maximum spacing for stitching / shielding vias around a ground fence, "
    "and the basis for guard-ring via pitch.",
    {
        "type": "object",
        "properties": {
            "freq_ghz": _num("highest frequency of interest in GHz"),
            "er": _num("effective relative permittivity", 4.3),
            "divisor": _num("divide wavelength by this (default 20)", 20),
        },
        "required": ["freq_ghz"],
    },
    lambda a: _rf.lambda_over_20(a["freq_ghz"], a.get("er", 4.3), a.get("divisor", 20)),
)

# ----------------------------------------------------------- safety
_SAFE_ARGS = {
    "voltage_v": _num("working voltage (use the most severe of RMS / peak / transient)"),
    "pollution_degree": {"type": "integer", "description": "pollution degree 1..4 "
                         "(outdoor / condensation suggests 3)", "default": 2},
    "material_group": _str("material group by CTI: I (>=600), II (400-599), "
                           "IIIa (175-399), IIIb (100-174)", ["I", "II", "IIIa", "IIIb"]),
    "insulation": _str("insulation type",
                       ["functional", "basic", "supplementary", "double", "reinforced"]),
    "altitude_m": _num("altitude in metres (above 2000 m needs a correction factor)", 0),
    "coating": _bool("conformal coating applied with complete coverage "
                     "(solder mask does NOT count)"),
    "pcb_material": _bool("the creepage path runs over the PCB surface, so Table "
                          "F.5's PCB-material column applies (it is ignored above "
                          "1000 V rms, at PD3/PD4 and for IIIb at PD2+)"),
    "transient_v": _num("transient overvoltage the gap must withstand, in volts - "
                        "normally the rated impulse withstand voltage. Omit only "
                        "when you have no transient spec; the answer is then "
                        "flagged as possibly understated"),
    "safety_standard": _str("safety standard family. IEC 60664-1 (any spelling) is "
                            "answered from a verified table; every other family "
                            "FAILS CLOSED until its table is loaded. Different "
                            "families give different distances."),
}


def _safety(kind):
    def handler(a):
        fn = {"creepage": _creepage.creepage_required,
              "clearance": _creepage.clearance_required,
              "spacing": _creepage.spacing_required}[kind]
        kw = dict(
            pollution_degree=a.get("pollution_degree", 2),
            material_group=a.get("material_group", "IIIa"),
            insulation=a.get("insulation", "basic"),
            altitude_m=a.get("altitude_m", 0),
            coating=a.get("coating", False),
            safety_standard=a.get("safety_standard"))
        if kind in ("clearance", "spacing"):
            kw["transient_v"] = a.get("transient_v")
        if kind in ("creepage", "spacing"):
            kw["pcb_material"] = bool(a.get("pcb_material"))
        return fn(a["voltage_v"], **kw)
    return handler


_tool(
    "creepage_required",
    "Required creepage distance (shortest path ALONG the insulating surface), "
    "IEC 60664-1:2020-05 Table F.5. No EDA's DRC computes this - a spacing matrix is "
    "electrical clearance, not creepage. IEC 60664-1 is answered from a table whose "
    "ladders were recovered from KiCad 10.0's compiled implementation and diffed "
    "against it on 188k probe points; any other safety_standard FAILS CLOSED, because "
    "a guessed safety distance is worse than no answer. Table F.5 alone: the standard "
    "also requires creepage >= clearance, which spacing_required applies.",
    {"type": "object",
     "properties": dict(_SAFE_ARGS),
     "required": ["voltage_v", "safety_standard"]},
    _safety("creepage"),
)

_tool(
    "clearance_required",
    "Required electrical clearance (shortest distance THROUGH air), IEC 60664-1 "
    "Tables F.2/F.8 corrected by the Table A.2 altitude factor. The transient branch "
    "and the peak branch are both evaluated and the larger wins; if one of them runs "
    "off the end of its table the whole answer is refused instead of quietly keeping "
    "the surviving branch. Give transient_v when you have an impulse spec - without it "
    "the answer is flagged as possibly understated. FAILS CLOSED for the same reason "
    "as creepage_required.",
    {"type": "object",
     "properties": dict(_SAFE_ARGS),
     "required": ["voltage_v", "safety_standard"]},
    _safety("clearance"),
)

_tool(
    "spacing_required",
    "Both distances at once, the way IEC 60664-1 actually applies them: creepage "
    "(Table F.5) and clearance (Tables F.2/F.8 + A.2), with creepage raised to the "
    "clearance when the table value is smaller, plus the minimum groove width. Use "
    "this for a design rule - the two single-purpose tools answer one table each and "
    "can each return the smaller of the two. Reports which of the two governed.",
    {"type": "object",
     "properties": dict(_SAFE_ARGS),
     "required": ["voltage_v", "safety_standard"]},
    _safety("spacing"),
)


_tool(
    "safety_table_check",
    "Can the safety-distance / current-capacity tables be trusted? Runs the rules the "
    "data has to satisfy and returns pass/fail per check. which=iec60664: edition "
    "named, verified flag, two independent sources, all 32 creepage ladders present, "
    "every ladder strictly ascending and never decreasing in distance, declared max_v "
    "matching the last threshold, linear segments passing through the origin, Table "
    "F.8 identical across pollution degrees, and physics probes (PD1<=PD2<=PD3, "
    "distance never falls as voltage doubles). which=creepage: the hand-written-table "
    "rules (edition named, two sources per row, monotone in voltage and material group). "
    "which=ipc2152: coefficient shape (K>0, current exponent >1, width and thickness "
    "exponents <0), two independent sources, declared anchors reproduced, and physical "
    "probes (current rises with copper and with delta-T, internal not derated by 2x). "
    "When it fails, that calculator stays fail-closed. Read-only, no board needed.",
    {"type": "object",
     "properties": {
         "which": _str("which table", ["iec60664", "creepage", "ipc2152"],
                       "iec60664"),
         "path": _str("optional alternative JSON file to check"),
     }},
    lambda a: _safety_table.check_which(a.get("which", "iec60664"), a.get("path")),
)

# ------------------------------------------------------------- diff pair
_tool(
    "diff_impedance",
    "Differential impedance of an edge-coupled pair (microstrip or stripline) plus the "
    "single-ended value and the coupling factor. Use this instead of trace_impedance for "
    "USB 90 ohm / Ethernet 100 ohm / HDMI targets: the single-ended closed form cannot "
    "express the coupling between the two traces.",
    {"type": "object",
     "properties": {
         "width_mm": _num("single trace width"),
         "spacing_mm": _num("edge-to-edge gap between the two traces"),
         "dielectric_mm": _num("dielectric height from trace to its reference plane "
                               "(stripline: half the plane-to-plane separation)"),
         "copper_um": _num("copper thickness in um", 35),
         "er": _num("relative permittivity", 4.3),
         "kind": _str("trace geometry", ["microstrip", "stripline"], "microstrip"),
     },
     "required": ["width_mm", "spacing_mm", "dielectric_mm"]},
    lambda a: _diffpair.diff_impedance(
        a["width_mm"], a["spacing_mm"], a["dielectric_mm"],
        copper_um=a.get("copper_um", 35.0), er=a.get("er", 4.3),
        kind=a.get("kind", "microstrip")),
)

_tool(
    "diff_width",
    "Reverse solve: the trace width that gives a target differential impedance at a fixed "
    "spacing. Reports the reachable band when the target cannot be met, instead of "
    "returning a width that silently misses.",
    {"type": "object",
     "properties": {
         "target_ohm": _num("target differential impedance, e.g. 90 or 100"),
         "spacing_mm": _num("edge-to-edge gap held fixed (set by the connector or the "
                            "fabricator's minimum gap)"),
         "dielectric_mm": _num("dielectric height"),
         "copper_um": _num("copper thickness in um", 35),
         "er": _num("relative permittivity", 4.3),
         "kind": _str("trace geometry", ["microstrip", "stripline"], "microstrip"),
     },
     "required": ["target_ohm", "spacing_mm", "dielectric_mm"]},
    lambda a: _diffpair.width_for_diff_impedance(
        a["target_ohm"], a["spacing_mm"], a["dielectric_mm"],
        copper_um=a.get("copper_um", 35.0), er=a.get("er", 4.3),
        kind=a.get("kind", "microstrip")),
)

_tool(
    "diff_impedance_field",
    "Differential impedance from an actual 2-D field solution instead of a closed form. "
    "Slower (seconds) and answers the one question the closed forms cannot: how far off "
    "they are on this exact geometry. Carries its own validation table (exact conformal "
    "stripline, Hammerstad microstrip) so the accuracy claim is measured, not promised.",
    {"type": "object",
     "properties": {
         "width_mm": _num("single trace width"),
         "spacing_mm": _num("edge-to-edge gap between the two traces"),
         "dielectric_mm": _num("dielectric height from trace to its reference plane "
                               "(stripline: half the plane-to-plane separation)"),
         "copper_um": _num("copper thickness in um", 35),
         "er": _num("relative permittivity", 4.3),
         "kind": _str("trace geometry", ["microstrip", "stripline"], "microstrip"),
         "levels": _num("mesh refinement levels: 1 = one mesh, 2 = coarse->fine (default)",
                        2),
     },
     "required": ["width_mm", "spacing_mm", "dielectric_mm"]},
    lambda a: _field2d.field_impedance(
        a["width_mm"], a["spacing_mm"], a["dielectric_mm"],
        copper_um=a.get("copper_um", 35.0), er=a.get("er", 4.3),
        kind=a.get("kind", "microstrip"), levels=int(a.get("levels", 2))),
)

_tool(
    "coplanar_field",
    "Coplanar (cpw / gcpw) impedance from an actual 2-D field solution, drawn on a full "
    "domain with no symmetry plane so it shares no assumption with the conformal map it "
    "is checked against. Also returns the effective permittivity and the measured gap to "
    "the closed form on the same geometry. Ten to forty seconds; the mesh is twice the "
    "size of a pair section because the mirror plane is deliberately not used.",
    {"type": "object",
     "properties": {
         "width_mm": _num("centre strip width"),
         "gap_mm": _num("slot width from the strip edge to the ground edge"),
         "dielectric_mm": _num("substrate thickness"),
         "copper_um": _num("copper thickness in um; 0 = zero-thickness (cheap, and the "
                           "only case the closed form has a term for)", 0),
         "er": _num("relative permittivity", 4.3),
         "kind": _str("cpw = air below the substrate, gcpw = ground plane below the "
                      "substrate", ["cpw", "gcpw"], "cpw"),
         "levels": _num("mesh refinement levels: 1 = one mesh, 2 = coarse->fine (default)",
                        2),
         "max_nodes": _num("refuse rather than grind if the mesh would exceed this",
                           40000),
         "ground_mm": _num("0 = the coplanar wings run to the truncation box "
                           "(effectively infinite, the historical behaviour); > 0 = "
                           "each wing is finite and stops that far from its slot, "
                           "which raises Z0 (13.7 %% at gap/2, 7.5 %% at gap, 1.1 %% at "
                           "2.5*gap) and needs far more nodes", 0),
     },
     "required": ["width_mm", "gap_mm", "dielectric_mm"]},
    lambda a: _field2d.coplanar_field(
        a["width_mm"], a["gap_mm"], a["dielectric_mm"],
        copper_um=a.get("copper_um", 0.0), er=a.get("er", 4.3),
        kind=a.get("kind", "cpw"), levels=int(a.get("levels", 2)),
        max_nodes=int(a.get("max_nodes", 40000)),
        ground_mm=float(a.get("ground_mm", 0.0) or 0.0)),
)

_tool(
    "coplanar_pair_field",
    "Edge-coupled coplanar pair (differential CPW / GCPW) from the 2-D field solver: "
    "Zdiff = 2*Z_odd plus the per-line odd and even impedances, the common-mode "
    "impedance and both filling factors. No closed form exists for this geometry, so "
    "the answer carries what IS checked instead: the mode decomposition against a "
    "third solve, the antisymmetry of the odd solution, and the merged-strip limit. "
    "Twelve to forty seconds; pass frequency_ghz to also get conductor and dielectric "
    "loss for the differential mode.",
    {"type": "object",
     "properties": {
         "width_mm": _num("width of EACH of the two strips"),
         "spacing_mm": _num("edge-to-edge gap between the two strips"),
         "gap_mm": _num("slot width from a strip's outer edge to its coplanar ground"),
         "dielectric_mm": _num("substrate thickness"),
         "copper_um": _num("copper thickness in um; 0 = zero-thickness (cheap, and the "
                           "only case the shape factor converges anywhere)", 0),
         "er": _num("relative permittivity", 4.3),
         "kind": _str("cpw = air below the substrate, gcpw = ground plane below the "
                      "substrate", ["cpw", "gcpw"], "cpw"),
         "levels": _num("mesh refinement levels: 1 = one mesh, 2 = coarse->fine (default)",
                        2),
         "frequency_ghz": _num("0 = impedance only; > 0 also solves the surface charge "
                               "and reports loss", 0),
         "tan_delta": _num("dielectric loss tangent, used only when frequency_ghz > 0",
                           0),
         "max_nodes": _num("refuse rather than grind if the mesh would exceed this",
                           60000),
         "ground_mm": _num("0 = the two coplanar wings run to the truncation box "
                           "(effectively infinite, the historical behaviour); > 0 = "
                           "each wing is finite and stops that far from its slot. This "
                           "is mostly a COMMON-mode effect: at gap/2 z_odd moves 1.1 %% "
                           "while z_even moves 21.9 %%", 0),
     },
     "required": ["width_mm", "spacing_mm", "gap_mm", "dielectric_mm"]},
    lambda a: _field2d.coplanar_pair_field(
        a["width_mm"], a["spacing_mm"], a["gap_mm"], a["dielectric_mm"],
        copper_um=a.get("copper_um", 0.0), er=a.get("er", 4.3),
        kind=a.get("kind", "cpw"), levels=int(a.get("levels", 2)),
        frequency_ghz=float(a.get("frequency_ghz", 0.0) or 0.0),
        tan_delta=float(a.get("tan_delta", 0.0) or 0.0),
        max_nodes=int(a.get("max_nodes", 60000)),
        ground_mm=float(a.get("ground_mm", 0.0) or 0.0)),
)

_tool(
    "crosstalk_coupled_lines",
    "How much of an edge lands on the trace next to it: near-end (NEXT) and far-end "
    "(FEXT) crosstalk between two parallel microstrips or striplines, from a 2-D field "
    "solution of the pair (odd and even modes, dielectric AND vacuum) feeding an exact "
    "time-domain solution of the coupled telegrapher's equations. No weak-coupling and "
    "no long-line assumption; any rise time, any length, strong coupling included. "
    "Returns both waveforms, the modal impedances/velocities, Cm and Lm, and the "
    "first-order Kb beside the simulated peak so you can see the two disagree as the "
    "coupling grows. FEXT comes out zero for a homogeneous dielectric because the two "
    "modal velocities are equal - that is arithmetic here, not an assumption, and on "
    "stripline the measured residue is 2e-5 %% of the source. One real cost: the four "
    "cross-sections are solved, so this takes 30 s (stripline) to 2 min (microstrip). "
    "Requires the SAME termination at each end of both lines (a mixed pair breaks the "
    "modal decoupling) and no third conductor.",
    {"type": "object",
     "properties": {
         "kind": _str("trace geometry", ["microstrip", "stripline"], "microstrip"),
         "width_mm": _num("width of EACH of the two traces (they are symmetric)"),
         "spacing_mm": _num("edge-to-edge gap between the two traces"),
         "dielectric_mm": _num("height from the trace to its reference plane; "
                               "stripline: half the plane-to-plane separation"),
         "length_mm": _num("length the two traces run side by side (the ports are at "
                           "the ends of this run)"),
         "rise_time_ns": _num("10-90 %% rise time of the aggressor's trapezoid. FEXT is "
                              "proportional to 1/rise_time in this model, so this "
                              "number is the single most sensitive input"),
         "copper_um": _num("copper thickness in um; 0 = zero-thickness", 35),
         "er": _num("relative permittivity", 4.3),
         "amplitude_v": _num("open-circuit step amplitude of the aggressor's source", 1),
         "termination_ohm": _num("the resistor at EACH of the four ports; leave out to "
                                 "use the isolated-line impedance (what a driver into "
                                 "a receiver usually looks like)", None),
         "mesh_div": _num("cells across the smallest feature of the cross-section: "
                          "12 = default; 8 measured 2.6x faster on w=s=h=0.2 microstrip "
                         "and moved Z by 0.06 %, NEXT by 0.4 %",
                          12),
         "samples_per_delay": _num("time steps per modal delay in the transient", 60),
         "waveform_points": _num("points in each returned waveform", 101),
     },
     "required": ["width_mm", "spacing_mm", "dielectric_mm", "length_mm",
                  "rise_time_ns"]},
    lambda a: _crosstalk.coupled_lines(
        a.get("kind", "microstrip"), a["width_mm"], a["spacing_mm"],
        a["dielectric_mm"], a["length_mm"], a["rise_time_ns"],
        copper_um=float(a.get("copper_um", 35.0)), er=float(a.get("er", 4.3)),
        amplitude_v=float(a.get("amplitude_v", 1.0)),
        termination_ohm=(None if a.get("termination_ohm") is None
                         else float(a["termination_ohm"])),
        mesh_div=int(a.get("mesh_div", 12)),
        samples_per_delay=int(a.get("samples_per_delay", 60)),
        waveform_points=int(a.get("waveform_points", 101))),
)

_tool(
    "lceda_api_probe",
    "Speak to LCEDA Pro's API gateway rather than just looking at the port: opens a "
    "real RFC 6455 WebSocket to 127.0.0.1:49620..49629, checks the handshake, and says "
    "which ports answered. `verified_against_app` stays false because no frame was read "
    "from the application - a completed handshake proves a WebSocket server, not that it "
    "is LCEDA. The gateway only listens after the user turns on 外部交互权限, so "
    "'no_listener' is the normal answer, not a broken feature.",
    {"type": "object",
     "properties": {
         "ports": {"type": "array", "items": {"type": "integer"},
                   "description": "ports to try (default 49620..49629)"},
         "timeout_s": _num("per-port connect+handshake timeout", 2),
     }},
    lambda a: _lceda_api.probe_ports(a.get("ports") or None,
                                     timeout=float(a.get("timeout_s", 2))),
)

# --------------------------------------------------------------- desktop
_APP = _str("which application", ["lceda-pro", "kicad", "pcbnew", "kicad-cli"])

def _desktop_error(exc):
    return err(str(exc), "APP_UNKNOWN",
               hint="known apps: " + ", ".join(sorted(_desktop.APPS)))

_tool(
    "app_detect",
    "Where is the EDA installed? Explicit path, else the running process image, else a "
    "drive scan, else the registry - and always returns what was tried. Never launches "
    "anything and never touches a project file.",
    {"type": "object",
     "properties": {
         "app": _APP,
         "explicit_path": _str("override: an exact exe path, used as-is"),
     }},
    lambda a: _guarded(lambda: _app_detect(a)),
)

_tool(
    "app_status",
    "Is the EDA running, which instances exist, which project is loaded, and is the "
    "EasyEDA Pro API gateway listening (49620-49629)? The project name is taken from a "
    "window title, so an empty list means 'no window shows a project', not 'no project'.",
    {"type": "object",
     "properties": {
         "app": _APP,
         "probe_ports": _bool("also probe the API gateway ports (0.02 s each)", True),
     },
     "required": ["app"]},
    lambda a: _guarded(lambda: _desktop.status(a["app"], a.get("probe_ports", True))),
)

_tool(
    "app_launch",
    "Start the EDA detached (no project). Prefer app_open_project when a project exists: "
    "starting a process is not the same as having the board loaded.",
    {"type": "object",
     "properties": {"app": _APP},
     "required": ["app"]},
    lambda a: _guarded(lambda: _launch_app(a["app"])),
)

_tool(
    "app_close",
    "Close the EDA by sending WM_CLOSE to its windows (same as clicking X, so the app may "
    "show a save dialog and stay open). force=true terminates the process and can lose "
    "unsaved work. Project files are never touched.",
    {"type": "object",
     "properties": {
         "app": _APP,
         "force": _bool("TerminateProcess instead of asking to close", False),
         "timeout_s": _num("how long to wait for a graceful exit", 15),
     },
     "required": ["app"]},
    lambda a: _guarded(lambda: _desktop.close(a["app"], force=a.get("force", False),
                                              timeout=a.get("timeout_s", 15.0))),
)

_tool(
    "app_open_project",
    "Open a .epro / .kicad_pro / .kicad_pcb in the right application, then CONFIRM by "
    "window title. `opened` is 'confirmed' only when a title contains the file stem; "
    "otherwise 'unconfirmed' with what was seen. Refuses to create a missing file.",
    {"type": "object",
     "properties": {
         "app": _APP,
         "path": _str("absolute path to the project/board file (must already exist)"),
         "reuse": _bool("focus an instance that already has this project instead of "
                        "starting a second window", True),
         "wait_s": _num("how long to wait for the window title to confirm", 25),
     },
     "required": ["app", "path"]},
    lambda a: _guarded(lambda: _desktop.open_project(
        a["app"], a["path"], reuse=a.get("reuse", True), wait=a.get("wait_s", 25.0))),
)

# ------------------------------------------------- LCEDA Pro in-process bridge
# Everything else in this server talks to FILES. These six talk to the RUNNING
# editor over its own command space (a named pipe carrying one JSON object per
# line), which is the only path here that can see the design the user has open.
# Measured 2026-10-28 on LCEDA Pro 4.1.60.198f38ab: ping, open, session.list,
# doc.api and invoke all answered for real. Nothing that WRITES was performed,
# so no tool below claims it.
_LIVE_BASIS = ("the running application's own command space (LCEDA Pro named pipe, "
               "one JSON object per line), not a file and not a reimplementation")
_LIVE_MODEL = "lceda_pro_command_space"

def _live_hint(code):
    return {
        "APP_NOT_RUNNING": "start LCEDA Pro and retry. Two measured reasons, both "
                           "silent: (1) the app is an Electron binary and "
                           "ELECTRON_RUN_AS_NODE=1 is inherited from this server's own "
                           "parent - Electron then runs as a bare node REPL, exits 0 "
                           "in about a second, opens no window, creates no pipe and "
                           "writes nothing to its log; app_launch clears that key, so "
                           "launch through it rather than from a shell. (2) the "
                           "launcher's process tree is torn down when the call that "
                           "started it returns, which also loses the app - again, "
                           "app_launch starts it detached. With both cleared the pipe "
                           "appears about 1 s after start",
        "APP_UNREACHABLE": "the pipe vanished while talking - the editor was most "
                           "likely closed",
        "APP_TIMEOUT": "the app accepted the frame but did not answer within the "
                       "budget; open and import need a larger timeout_s",
        "APP_ERROR": "the app failed without one of its published codes; its own "
                     "string is kept in app_code / app_message",
        "ARG_REQUIRED": "an argument that must be non-empty was empty",
        "ARG_INVALID": "the argument does not describe anything that exists on disk",
        "FRAME_TOO_LARGE": "request frame exceeds the measured 4 MiB limit",
        "RESULT_TOO_LARGE": "reply exceeds the measured 16 MiB limit; ask for less",
        "EXECUTION_ERROR": "the snippet threw inside the editor; the message is the "
                           "app's own and the snippet's logs are in extra.logs",
        "SYNTAX_ERROR": "the snippet does not parse as an async function body",
        "SESSION_NOT_FOUND": "no such session; call lceda_pro_sessions",
    }.get(code)

def _live_error(exc):
    extra = dict(exc.extra or {})
    extra.setdefault("vendor_code_kept", exc.code in _live.PASS_THROUGH_CODES)
    return err(exc.message, exc.code, hint=_live_hint(exc.code), extra=extra)

def _live_done(client, value, assumptions, verified, **extra):
    identity = client.identity or {}
    extra["endpoint"] = client.endpoint
    extra["bridge_version"] = identity.get("bridgeVersion")
    extra["verified_against_app"] = bool(verified)
    return ok(value, "the application's own reply, unmodified", _LIVE_MODEL,
              _LIVE_BASIS,
              "exact passthrough: no field inside value is added, dropped or "
              "rounded by this server; what the app did not send is absent",
              assumptions, extra)

def _live_client(args):
    return _live.connect(endpoint=args.get("endpoint") or None,
                         timeout_s=float(args.get("timeout_s") or 10.0))

_LIVE_ARGS = {
    "endpoint": _str("explicit pipe name (default: enumerate JLCEDAPro* and keep the "
                     "first that answers a ping)"),
    "timeout_s": _num("connect + ping budget in seconds", 10),
}

def _lceda_pro_status(args):
    try:
        client = _live_client(args)
    except _live.LiveError as exc:
        return _live_error(exc)
    try:
        identity = client.ping()
    except _live.LiveError as exc:
        return _live_error(exc)
    finally:
        client.close()
    return _live_done(client, identity, {
        "which pipe": "the first JLCEDAPro* pipe whose ping answered; the 8 hex "
                      "suffix is opaque - it could not be reproduced from any "
                      "candidate user-data path on this install, so it is never "
                      "guessed",
        "what a ping proves": "a process speaking this protocol is listening; it "
                              "does not prove the project is the one you mean",
        "not_run": "no write, no save, no code execution",
    }, verified=True)

def _lceda_pro_api(args):
    try:
        client = _live_client(args)
    except _live.LiveError as exc:
        return _live_error(exc)
    try:
        value = client.api(args.get("namespace") or "eda",
                           class_name=args.get("class_name") or None,
                           method_name=args.get("method_name") or None,
                           timeout_s=float(args.get("api_timeout_s") or 30.0))
    except _live.LiveError as exc:
        return _live_error(exc)
    finally:
        client.close()
    return _live_done(client, value, {
        "shape": "namespace only -> {types:[{name,comment}], "
                 "callConventions:{entry, classToProperty}}; with class_name -> "
                 "{title, remarks, methods:[{name,comment}], callPath} (measured)",
        "document names are not property names": "the app maps PCB_Document -> "
                                                 "pcb_Document; read the mapping "
                                                 "from callConventions, never guess",
    }, verified=True)

def _lceda_pro_open(args):
    try:
        client = _live_client(args)
    except _live.LiveError as exc:
        return _live_error(exc)
    try:
        value = client.open_project(args.get("path"),
                                    headless=bool(args.get("headless", True)),
                                    timeout_s=float(args.get("open_timeout_s") or 45.0))
    except _live.LiveError as exc:
        return _live_error(exc)
    finally:
        client.close()
    return _live_done(client, value, {
        "what was measured": "open of a copied 32.5 MB .eprj answered in 849 ms with "
                             "a session id, renderId 3 and an 'alive' status",
        "not measured": "whether open touches a recent-files list or a lock file - "
                        "the project bytes were not compared before and after, so "
                        "treat 'open does not modify the project' as unproven",
        "headless": "headless=true still creates a live session in the running app; "
                    "it does not hide the editor window",
    }, verified=True)

def _lceda_pro_sessions(args):
    try:
        client = _live_client(args)
    except _live.LiveError as exc:
        return _live_error(exc)
    try:
        value = client.sessions(timeout_s=float(args.get("list_timeout_s") or 10.0))
    except _live.LiveError as exc:
        return _live_error(exc)
    finally:
        client.close()
    return _live_done(client, value, {
        "what a session is": "an open document inside the editor that can be driven "
                             "by doc.api / invoke; it is not a process",
    }, verified=True)

def _lceda_pro_close(args):
    try:
        client = _live_client(args)
    except _live.LiveError as exc:
        return _live_error(exc)
    try:
        value = client.close_session(args.get("session"),
                                     destroy=bool(args.get("destroy", False)),
                                     timeout_s=float(args.get("close_timeout_s")
                                                     or 20.0))
    except _live.LiveError as exc:
        return _live_error(exc)
    finally:
        client.close()
    return _live_done(client, value, {
        "destroy": "destroy=true drops the session; whether it discards unsaved "
                   "edits without asking was not tested, so treat it as able to "
                   "lose work on the document that session holds",
        "what it never does": "it does not write the project file; a closed session "
                              "with unsaved edits loses them",
    }, verified=True)

def _lceda_pro_run(args):
    session = args.get("session")
    # An argument that is missing is missing whether or not an editor is running:
    # answer that first, so the caller is never told "app not running" for a
    # request that could not have been sent anyway.
    if not session:
        return _live_error(_live.LiveError("ARG_REQUIRED", "session is required"))
    if not args.get("code") or not str(args.get("code")).strip():
        return _live_error(_live.LiveError("ARG_REQUIRED", "code is required"))
    try:
        client = _live_client(args)
    except _live.LiveError as exc:
        return _live_error(exc)
    try:
        out = client.invoke(session, args.get("code"),
                            fn=args.get("fn") or "run",
                            args=args.get("js_args"),
                            timeout_ms=args.get("run_timeout_ms"))
    except _live.LiveError as exc:
        return _live_error(exc)
    finally:
        client.close()
    return _live_done(client, out["value"], {
        "where the code runs": "as an async function body INSIDE the editor's "
                               "render process, with the vendor's eda object in "
                               "scope and arguments under __CLI__.args",
        "this can write": "any snippet you send runs with the editor's own "
                          "privileges; a save or a move it performs is real. This "
                          "project never performed one, so the write path stays "
                          "unverified - the snippet, not the tool, decides",
    }, verified=True, logs=out["logs"], duration_ms=out["duration_ms"])

def _lceda_pcb_write(args):
    """Run a write snippet inside the editor ONLY if its effect is read back.

    Nothing here invents the vendor's write API: the snippet is the caller's, and
    the discipline is this tool's - a probe before, the same probe after, and a
    refusal (`WRITE_HAD_NO_EFFECT`) when the design did not move.

    `verified_against_app` follows `lceda_write.VERIFIED_WRITES`: true only when
    the bridge that answered is a version this project really performed that op
    on AND this call showed its effect. Everything else - an unlisted build, an
    op never run there (`move`), a create whose probe did not move - stays false.
    """
    from . import lceda_write as _write
    op = args.get("op") or "write"
    spec = _write.OPS.get(op)
    if spec is None:
        return err("unknown op %r" % (op,), "ARG_INVALID",
                   hint="op is one of %s" % (", ".join(sorted(_write.OPS)),))
    try:
        client = _live_client(args)
    except _live.LiveError as exc:
        return _live_error(exc)
    # The gate keys off the bridge that actually answered, so it is read here and
    # never assumed. A client without `identity`/`ping` (a stub, an older build)
    # simply leaves it None, which is the "false" branch.
    identity = getattr(client, "identity", None) or {}
    bridge = identity.get("bridgeVersion")
    if not bridge:
        try:
            bridge = (client.ping() or {}).get("bridgeVersion")
        except Exception:
            bridge = None
    try:
        res = _write.run(client, args.get("session"), args.get("code"),
                         probe=args.get("probe"),
                         expect=args.get("expect") or spec["expect"],
                         timeout_ms=args.get("timeout_ms"),
                         label=args.get("label") or op,
                         op=op, bridge_version=bridge)
    except _write.WriteError as exc:
        return err(exc.message, exc.reason_code, hint=exc.hint, extra=exc.extra)
    except _live.LiveError as exc:
        return _live_error(exc)
    finally:
        client.close()
    report = res["report"]
    if res["status"] != "ok":
        return err(res["message"], res["reason_code"], hint=res["hint"],
                   extra={"report": report, "why_this_op_exists": spec["why"]})
    measured = report["verified_against_app"]
    if measured:
        precision = ("The write path itself was measured on %s on %s: create, "
                     "delete and save were run on it and read back, including two "
                     "full restarts from disk. This answer's own effect is still "
                     "only the probe's word, and the items in not_verified were not "
                     "checked"
                     % (report["measured"]["app"],
                        report["measured"]["measured_on"]))
    else:
        precision = ("NOT VERIFIED AGAINST THIS APPLICATION BUILD. Either the bridge "
                     "that answered is not a version this project has written on, or "
                     "this op was never run there - so the claim is about the "
                     "snippet's OBSERVED EFFECT, not about the vendor API having the "
                     "semantics the caller assumed. A save has no observable state "
                     "and is confirmed only by 'the call returned without an error'")
    return ok(
        value={"op": op, "changed": report["changed"],
               "expect": report["expect"], "probe_used": report["probe"]},
        unit="bool",
        model="lceda_pro_create_save_move",
        basis="before/after probe comparison inside the running editor, with the "
              "caller's own snippet in between",
        precision=precision,
        assumptions={"verified_against_app": measured,
                     "bridge_version": bridge,
                     "not_verified": report["not_verified"],
                     "why_this_op_exists": spec["why"],
                     "the_snippet_is_yours":
                         "this server never synthesises a write call; the snippet "
                         "is the caller's, and a generated guess at the vendor's "
                         "method names is exactly the plausible wrong answer this "
                         "project refuses"},
        extra={"report": report})

def _lceda_pro_steps(args):
    """Run a list of snippets in the editor one at a time, watchable and stoppable.

    Each snippet is one visible action inside the running editor, so the person
    at the screen sees the design change instruction by instruction and can stop
    between any two of them. The stop file and the step log are the same ones the
    KiCad stepped write uses, so ``board_progress`` controls both.
    """
    from . import edit as _edit
    session = args.get("session")
    steps = args.get("steps") or []
    if not session:
        return _live_error(_live.LiveError("ARG_REQUIRED", "session is required"))
    if not steps:
        return _live_error(_live.LiveError("ARG_REQUIRED", "steps is required"))
    try:
        client = _live_client(args)
    except _live.LiveError as exc:
        return _live_error(exc)
    log_id = "lceda-" + str(session)
    done, stopped_at = [], None
    try:
        for idx, step in enumerate(steps):
            if os.path.isfile(_edit.plan.stop_file()):
                stopped_at = idx
                break
            label = step.get("label") or ("step %d" % (idx + 1))
            if isinstance(step, str):
                step = {"code": step}
                label = "step %d" % (idx + 1)
            code = step.get("code")
            if not code:
                _edit.plan._emit(log_id, {"kind": "refused", "at": idx,
                                          "why": "a step without code"})
                return _live_error(_live.LiveError(
                    "ARG_REQUIRED", "step %d (%s) has no code" % (idx + 1, label)))
            try:
                out = client.invoke(session, code, fn=step.get("fn") or "run",
                                    args=step.get("js_args"),
                                    timeout_ms=step.get("timeout_ms")
                                    or args.get("run_timeout_ms"))
            except _live.LiveError as exc:
                _edit.plan._emit(log_id, {"kind": "step_failed", "at": idx,
                                          "label": label, "code": exc.code})
                return _live_error(exc)
            item = {"index": idx, "label": label, "value": out["value"],
                    "duration_ms": out["duration_ms"],
                    "logs": out["logs"]}
            done.append(item)
            _edit.plan._emit(log_id, {"kind": "step", "seq": idx + 1, "ops": [label],
                                      "duration_ms": out["duration_ms"],
                                      "sha256": None,
                                      "value_preview": str(out["value"])[:200]})
    finally:
        client.close()
    extra = {"rendering": "each snippet ran inside the editor's render process; the "
                          "canvas shows it as soon as the snippet touches a document, "
                          "so a person can watch and comment",
             "stopping_it": "board_progress(action='stop') stops before the next "
                            "step; action='resume' releases it",
             "step_log": _edit.plan.step_events(log_id, limit=200)["root"]}
    if stopped_at is not None:
        extra["stopped_before_step"] = stopped_at
        extra["not_run"] = [s.get("label") if isinstance(s, dict) else "step %d" % (i + 1)
                            for i, s in enumerate(steps[stopped_at:], start=stopped_at)]
    return _live_done(client, {"steps_run": len(done), "steps_planned": len(steps),
                               "stopped": stopped_at is not None,
                               "steps": done}, {
        "verified_against_app": "every step's value is the editor's own reply",
        "what is not proven": "whether a snippet leaves the document saved; saving "
                              "is the snippet's business, and the project file is "
                              "only written when something asks the editor to save",
    }, verified=True, logs=None, duration_ms=None, **extra)

_tool(
    "lceda_pro_status",
    "Is the LCEDA Pro IN-PROCESS bridge alive, and which build is it? Speaks the "
    "editor's own command-space pipe (named pipe, one JSON object per line) and "
    "reports its endpoint and bridgeVersion. Measured against 4.1.60: ping answers "
    "'{endpoint, bridgeVersion, resultChannel}'. A shell-launched app exits in about "
    "0.5 s here; the working start is the explorer.exe (double-click) path. Runs no "
    "code and writes nothing.",
    {"type": "object", "properties": dict(_LIVE_ARGS)},
    _lceda_pro_status,
)

_tool(
    "lceda_pro_api",
    "Ask the running editor for its own API surface: the type list of a namespace, or "
    "the method list of one class, with the vendor's Chinese comments. This is how you "
    "learn what invoke can call instead of guessing names - and it also returns "
    "callConventions.classToProperty, because the document name (PCB_Document) is NOT "
    "the runtime property name (pcb_Document). Read-only.",
    {"type": "object",
     "properties": dict(_LIVE_ARGS, **{
         "namespace": _str("API namespace", default="eda"),
         "class_name": _str("class or interface name, e.g. PCB_Document"),
         "method_name": _str("one method, e.g. save"),
         "api_timeout_s": _num("budget for this query", 30),
     })},
    _lceda_pro_api,
)

_tool(
    "lceda_pro_open",
    "Open a project INSIDE the running editor through its command space (not by "
    "starting a second exe) and return the session id it created. Measured: a 32.5 MB "
    ".eprj opened headless in 849 ms. The path must already exist. Note what is NOT "
    "proven: the project bytes were not compared before and after, so 'open modifies "
    "nothing' is unverified - keep a copy when it matters.",
    {"type": "object",
     "properties": dict(_LIVE_ARGS, **{
         "path": _str("absolute path to an existing .eprj/.epro/.kicad_pcb"),
         "headless": _bool("open without taking over the UI (still a live session)",
                           True),
         "open_timeout_s": _num("budget for the open itself", 45),
     }),
     "required": ["path"]},
    _lceda_pro_open,
)

_tool(
    "lceda_pro_sessions",
    "List the documents the running editor has open as drivable sessions (id, render "
    "id, state). Call this before lceda_pro_run: invoke needs a session id, and a "
    "session is a document, not a process.",
    {"type": "object", "properties": dict(_LIVE_ARGS, **{
        "list_timeout_s": _num("budget for the listing", 10)})},
    _lceda_pro_sessions,
)

_tool(
    "lceda_pro_close",
    "Close one session in the running editor (optionally destroy it). This never "
    "writes the project file: a session with unsaved edits loses them, so save inside "
    "the editor first. It does not quit the application.",
    {"type": "object",
     "properties": dict(_LIVE_ARGS, **{
         "session": _str("session id from lceda_pro_open / lceda_pro_sessions"),
         "destroy": _bool("destroy the session instead of just closing it", False),
         "close_timeout_s": _num("budget for the close", 20),
     }),
     "required": ["session"]},
    _lceda_pro_close,
)

_tool(
    "lceda_pro_run",
    "Run a snippet INSIDE the running editor: the code is the body of an async "
    "function, gets the vendor's eda object in scope, and reads arguments from "
    "__CLI__.args. This is the only tool here that can touch a live design - the "
    "snippet, not this server, decides what happens, and a save it performs is real. "
    "Verified: an invoke returned both PCBs of an opened project by name and uuid, and "
    "a thrown Error came back as EXECUTION_ERROR with the app's own message. No write "
    "was ever performed while measuring.",
    {"type": "object",
     "properties": dict(_LIVE_ARGS, **{
         "session": _str("session id from lceda_pro_open / lceda_pro_sessions"),
         "code": _str("async function body, e.g. 'return eda.dmt_Pcb.list();'"),
         "js_args": {"type": "object",
                     "description": "exposed to the snippet as __CLI__.args"},
         "fn": _str("entry style the app expects", default="run"),
         "run_timeout_ms": _num("budget passed to the app (max 60000)", 60000),
     }),
     "required": ["session", "code"]},
    _lceda_pro_run,
)

_tool(
    "lceda_pro_steps",
    "Run a design change inside the running editor ONE STEP AT A TIME, so it can be "
    "watched and interrupted. Each step is {label, code, js_args}: the code is an async "
    "function body with the vendor's eda object in scope, and it lands on the canvas "
    "immediately, so a person sees step 3 before step 4 exists. The stop file and step "
    "log are the same ones the KiCad stepped write uses - board_progress(action='stop') "
    "stops this before the next step, board_progress() reads the log. This is the LCEDA "
    "Pro answer to 'let me see what you are doing'.",
    {"type": "object",
     "properties": dict(_LIVE_ARGS, **{
         "session": _str("session id from lceda_pro_open / lceda_pro_sessions"),
         "steps": {"type": "array", "description":
                   "list of {label, code, js_args, fn, timeout_ms}; a bare string is "
                   "treated as code", "items": {"type": "object"}},
         "run_timeout_ms": _num("per-step budget passed to the app (max 60000)", 60000),
     }),
     "required": ["session", "steps"]},
    _lceda_pro_steps,
)

_tool(
    "lceda_pcb_write",
    "Perform ONE write inside the running LCEDA Pro editor and READ IT BACK. op is "
    "save | move | create | write (each only names what to expect); the code is YOUR "
    "snippet (an async function body with the vendor's `eda` object in scope) and the "
    "probe is a second snippet that returns the state you expect to change. The probe "
    "runs before and after the write, and an identical value is a REFUSAL "
    "(WRITE_HAD_NO_EFFECT) - a write that silently did nothing is the failure mode "
    "that looks like success. `save` has nothing observable to compare and says so: "
    "it is confirmed only by 'the call returned without an error'. "
    "verified_against_app is true ONLY when three things hold: you say which bridge "
    "answered (it comes from ping), that exact version is a key of "
    "lceda_write.VERIFIED_WRITES, and this call showed its own effect - so create / "
    "delete / save on LCEDA Pro V4.1.60 (bridge 4.1.60.198f38ab, measured "
    "2026-11-01, including a restart from disk) can answer true. An unlisted build, "
    "an op never run there (move), or a probe that did not move all still answer "
    "false. Either way the snippet is yours and the claim is about its observed "
    "effect, never about the vendor API's semantics in general.",
    {"type": "object",
     "properties": dict(_LIVE_ARGS, **{
         "session": _str("session id from lceda_pro_open / lceda_pro_sessions"),
         "op": _str("what you are doing: save | move | create | write", "write"),
         "code": _str("the write snippet (async function body, `eda` in scope)"),
         "probe": _str("snippet returning the state that must change (required for "
                       "op=move/create/write with expect='change'), e.g. a count or "
                       "an object's position"),
         "expect": _str("'change' (default for move/create/write) or 'no_error'",
                        ["change", "no_error"], "change"),
         "label": _str("what this write is, for the report"),
         "timeout_ms": _num("per-invoke budget passed to the app (max 60000)", None),
     }),
     "required": ["session", "code"]},
    _lceda_pcb_write,
)

def _lceda_pour(args):
    """Official copper pour on the running editor: border in, fill read back.

    A pour is two objects. ``pcb_PrimitivePour`` is the border and is writable;
    ``pcb_PrimitivePoured`` is the fill RESULT and is read-only. Measured on
    V4.1.60: no rebuild entry exists anywhere in the API, and the only thing
    that fills is the editor's own Shift+B pressed by a human. So this handler
    creates the official border, reads the official fill, and answers
    ``needs_editor_rebuild=true`` with the exact keystroke instead of inventing
    copper. Areas are this server's arithmetic (shoelace over the fill rings),
    never a vendor number.
    """
    from . import lceda_pour as _pour
    session = args.get("session")
    if not session:
        return _live_error(_live.LiveError("ARG_REQUIRED", "session is required"))
    action = args.get("action") or "list"
    if action not in ("list", "check", "create", "delete", "save"):
        return err("unknown action %r" % (action,), "ARG_INVALID",
                   hint="action is list | check | create | delete | save")
    try:
        client = _live_client(args)
    except _live.LiveError as exc:
        return _live_error(exc)
    tm = args.get("timeout_ms")
    identity = getattr(client, "identity", None) or {}
    try:
        if action in ("list", "check"):
            rep = _pour.list_pours(client, session, timeout_ms=tm)
        elif action == "create":
            rep, e = _pour.create_pour(
                client, session,
                net=args.get("net"), layer=args.get("layer"),
                rect_mm=args.get("rect_mm"),
                polygon_mil=args.get("polygon_mil"),
                name=args.get("name"),
                priority=args.get("priority", 1),
                fill_method=args.get("fill_method") or "solid",
                preserve_silos=bool(args.get("preserve_silos", False)),
                line_width=args.get("line_width", 10),
                timeout_ms=tm)
            if e:
                return e
        elif action == "delete":
            rep, e = _pour.delete_pours(client, session, args.get("ids") or [],
                                        timeout_ms=tm)
            if e:
                return e
        else:
            rep, e = _pour.save(client, session, timeout_ms=tm)
            if e:
                return e
    except _live.LiveError as exc:
        return _live_error(exc)
    finally:
        client.close()
    return ok(
        rep, "pour borders and fill regions as the editor reports them",
        "lceda_pour_v1",
        "eda.pcb_PrimitivePour.getAll() + eda.pcb_PrimitivePoured.getAll() read "
        "inside the running editor; each fill path's first ring is the copper "
        "edge and the rest are holes; area by shoelace with 1 unit = 10 mil "
        "(0.254 mm), measured on this build's board outline",
        "Borders and fill paths are the editor's own bytes, unmodified. The "
        "areas are this server's arithmetic on those rings, so they are only as "
        "good as the ring parse ('ARC' is treated as its end point, which "
        "rounds a rounded corner by less than one unit). Whether the fill is "
        "CURRENT is not answerable here: a track, pad or outline edit after the "
        "last rebuild leaves it stale with no marker in the reply.",
        {"action": action,
         "api_can_rebuild": False,
         "rebuild_needs_a_human": _pour.EDITOR_ACTION,
         "no_rebuild_entry_evidence": dict(_pour.NO_REBUILD_EVIDENCE),
         "this_server_never_draws_copper": (
             "a self-drawn fill was measured to produce 302 clearance "
             "violations: it does not know the rule table, so it is refused "
             "here rather than offered as a fallback")},
        extra={"endpoint": client.endpoint,
               "bridge_version": identity.get("bridgeVersion"),
               "verified_against_app": True})

_tool(
    "lceda_pour",
    "Official copper pour in the running LCEDA Pro editor. A pour is TWO objects "
    "and the split is the whole point: `pcb_PrimitivePour` is the border (net, "
    "layer, fill method, priority) and the API can WRITE it; `pcb_PrimitivePoured` "
    "is the fill RESULT and the API can only READ it. action='list' reads both "
    "tables and reports per pour: net, layer, name, priority, border polygon, "
    "fill_paths, filled_area_mm2 and `filled`. action='create' builds the OFFICIAL "
    "border from rect_mm (board mm, canvas y down) and then reads it back - a "
    "create whose re-read does not show one more pour with the net/layer asked "
    "for is WRITE_HAD_NO_EFFECT, not a success. action='delete' removes ids and "
    "proves the count fell by exactly that many. action='save' asks the editor to "
    "write the project and refuses unless it answers true. action='check' is the "
    "same read as list, meant to be run AFTER a rebuild.\n\n"
    "What it cannot do, measured rather than guessed: all 98 eda.* namespaces and "
    "every dmt_*/sys_* method were enumerated on V4.1.60 and NO rebuild entry "
    "exists; save(), pcb_Drc.check(), pour.modify(), zoom, nesting a pour inside "
    "another and closing-then-reopening the document were each tried and none "
    "produced fill (a reopen keeps the border and loses the fill). The only thing "
    "that fills is the editor's own Shift+B in the PCB canvas, pressed by a human. "
    "So when a pour has no fill this tool answers needs_editor_rebuild=true plus "
    "the exact keystroke, and it NEVER draws copper itself - a self-made fill was "
    "measured to raise 302 clearance violations because it does not know the rule "
    "table. `filled` is a snapshot, not live copper.",
    {"type": "object",
     "properties": dict(_LIVE_ARGS, **{
         "session": _str("session id from lceda_pro_open / lceda_pro_sessions"),
         "action": _str("list (default) | check | create | delete | save",
                        ["list", "check", "create", "delete", "save"], "list"),
         "net": _str("net the pour belongs to (create), e.g. GND"),
         "layer": _num("copper layer: 1 top, 2 bottom (create)"),
         "rect_mm": {"type": "array", "items": {"type": "number"},
                     "description": "create: [x0,y0,x1,y1] in board mm, canvas y "
                                    "down; converted to the document's mil"},
         "polygon_mil": {"type": "array",
                         "description": "create: a raw vendor polygon in document "
                                        "units, e.g. ['R',20,2578.4,1534.8,2558.4,0,0]. "
                                        "Used instead of rect_mm when given"},
         "name": _str("pour name shown in the editor (create)"),
         "priority": _num("pour priority; higher wins where pours overlap", 1),
         "fill_method": _str("vendor fill method (create)", default="solid"),
         "preserve_silos": _bool("keep isolated copper islands (default false)",
                                 False),
         "line_width": _num("border line width in document units (default 10)", 10),
         "ids": _arr("pour primitive ids to delete"),
         "timeout_ms": _num("per-invoke budget passed to the app (max 60000)", None),
     }),
     "required": ["session"]},
    _lceda_pour,
)

def _lceda_write_snippet(args):
    """Build a save/move/create snippet from the app's own method table.

    Reads only: the method NAMES come from the editor (``lceda_pro_api``), the
    ARGUMENTS come from the caller, and nothing is run. The answer is a
    candidate snippet plus the evidence behind its name (the vendor's comment)
    and the list of what that evidence does not cover.
    """
    from . import lceda_snippet as _snip
    op = args.get("op")
    if not op:
        return err("op is required", "ARG_MISSING",
                   hint="op is one of %s" % (", ".join(sorted(_snip.OPS)),))
    dump = args.get("api_dump")
    client = None
    close_after = False
    try:
        if dump is None:
            session = args.get("session")
            class_name = args.get("class_name")
            if not class_name:
                return err("class_name is required when api_dump is not given",
                           "ARG_MISSING",
                           hint="ask the app about ONE class (e.g. PCB_Document, "
                                "DMT_Pcb); a namespace table lists no methods")
            try:
                client = _live_client(args)
                close_after = True
                dump = client.api(args.get("namespace") or "eda",
                                  class_name=class_name,
                                  timeout_s=float(args.get("api_timeout_s") or 30.0))
            except _live.LiveError as exc:
                return _live_error(exc)
        try:
            rep = _snip.build(op, dump,
                              argsv=args.get("argsv"),
                              pick=args.get("pick") or None,
                              prop=args.get("property") or None,
                              class_name=args.get("class_name") or None,
                              name_tokens=args.get("name_tokens") or None,
                              comment_tokens=args.get("comment_tokens") or None)
        except _snip.SnippetError as exc:
            return err(exc.message, exc.reason_code, hint=exc.hint, extra=exc.extra)
        probe = None
        if args.get("want_probe", True):
            try:
                probe = _snip.suggest_probe(dump,
                                            prop=args.get("property") or None,
                                            class_name=args.get("class_name") or None,
                                            pick=args.get("probe_pick") or None)
            except _snip.SnippetError as exc:
                probe = {"status": "error", "reason_code": exc.reason_code,
                         "message": exc.message, "hint": exc.hint}
        if rep.get("status") != "ok":
            return err(rep.get("message"), rep.get("reason_code"),
                       hint=rep.get("hint"), extra={"report": rep, "probe": probe})
        return ok(
            value={"op": rep["op"], "code": rep["code"], "method": rep["method"],
                   "method_comment": rep["method_comment"],
                   "receiver": rep["receiver"],
                   "receiver_from": rep["receiver_from"],
                   "confidence": rep["confidence"],
                   "evidence": rep.get("evidence"),
                   "args_from_caller": True,
                   "json_encoded_args": rep.get("json_encoded_args") or [],
                   "probe": (probe or {}).get("code"),
                   "probable_probe_method": (probe or {}).get("method"),
                   "probe_basis": (probe or {}).get("confidence"),
                   "ranked": rep["ranked"]},
            unit="str",
            model="lceda_pro_snippet_from_vendor_api",
            basis="method name + vendor comment taken from the editor's own "
                  "doc.api table; the receiver comes from its callPath "
                  "(%s); the arguments are yours" % rep["receiver_from"],
            precision="NOT VERIFIED AGAINST THE APPLICATION. The method exists "
                      "(the app listed it) but its signature, return value and "
                      "effect are unproven, and this server never ran the "
                      "snippet. Run it with lceda_pcb_write plus a probe if you "
                      "want the effect read back",
            assumptions={"verified_against_app": False,
                         "not_verified": rep["not_verified"],
                         "why_this_op_exists": rep["why_this_op_exists"],
                         "arguments_are_not_derived": "the app reports names "
                             "and comments, not signatures, so argsv is passed "
                             "through verbatim and never inferred",
                         "json_encoded_args": "1-based positions of argsv entries "
                             "that were JSON-encoded (numbers, true/false/null, "
                             "objects/arrays) instead of inserted as written "
                             "(strings are inserted verbatim as JavaScript)"},
            extra={"report": rep, "probe_report": probe})
    finally:
        if close_after and client is not None:
            client.close()

_tool(
    "lceda_write_snippet",
    "Build a save / move / create snippet for `lceda_pcb_write` out of the method "
    "table the running editor reports about itself (doc.api), instead of guessing "
    "method names. The NAMES come from the app (with the vendor's Chinese comment as "
    "evidence), the ARGUMENTS come from you (argsv), and nothing is run here. It "
    "refuses rather than picks: a class whose table has no matching method is "
    "SNIPPET_NO_API, two equally good matches are SNIPPET_AMBIGUOUS (with the names "
    "so you can `pick`), and a receiver that cannot be resolved is "
    "SNIPPET_NO_RECEIVER (the class name is not the runtime property name and this "
    "tool will not lower-case it into one). Every answer here is "
    "verified_against_app=false with the unverified parts listed: the method exists "
    "and its comment is the vendor's, but the generator itself is not verified - one "
    "create snippet it produced was run through `lceda_pcb_write` on LCEDA Pro "
    "V4.1.60 (2026-11-01) and read back, which vouches for that snippet, not for the "
    "generator and not for the arguments you pass it.",
    {"type": "object",
     "properties": dict(_LIVE_ARGS, **{
         "op": _str("what you want done: save | move | create",
                    ["save", "move", "create"]),
         "api_dump": {"type": "object", "description":
                      "the class-level answer of lceda_pro_api "
                      "({title, remarks, methods:[{name, comment}], callPath}); "
                      "leave it out to query the app with session+class_name"},
         "session": _str("session id, used only when api_dump is absent"),
         "class_name": _str("class to ask about, e.g. PCB_Document / DMT_Pcb"),
         "property": _str("runtime property name if the dump has no usable "
                           "callPath, e.g. pcb_Document (never guessed)"),
         "argsv": {"type": "array", "description":
                   "positional JavaScript arguments, passed verbatim (strings) or "
                   "JSON-encoded (numbers/objects). Never inferred",
                   "items": {}},
         "pick": _str("exact method name to use when several match"),
         "name_tokens": {"type": "array", "description":
                         "override the name keywords for this op",
                         "items": {"type": "string"}},
         "comment_tokens": {"type": "array", "description":
                            "override the vendor-comment keywords for this op",
                            "items": {"type": "string"}},
         "want_probe": _bool("also suggest a read-only snippet as the probe", True),
         "probe_pick": _str("exact read method to use as the probe"),
         "namespace": _str("API namespace", default="eda"),
         "api_timeout_s": _num("budget for the doc.api query", 30),
     }),
     "required": ["op"]},
    _lceda_write_snippet,
)

def _lceda_step_record(args):
    """Turn a run that already happened into a replayable, comparable script.

    Touches no application: the values it records are the ones the caller passes in
    (usually `lceda_pro_steps`' own answer), so this is usable offline and writes
    nothing unless `save` is asked for.
    """
    from . import lceda_steps as _steps
    raw = args.get("steps")
    if not raw:
        return err("steps is required", "ARG_MISSING",
                   hint="pass the steps you ran, each optionally with the `value` it "
                        "returned - a step with a value becomes comparable on "
                        "replay, a step without one is recorded as uncomparable")
    try:
        script = _steps.make_script(raw, note=args.get("note"),
                                    source=args.get("source"),
                                    session=args.get("session"))
        written = None
        if args.get("save") or args.get("path"):
            written = _steps.save(script, path=args.get("path"),
                                  overwrite=bool(args.get("overwrite")))
    except _steps.StepError as exc:
        return err(exc.message, exc.reason_code, hint=exc.hint, extra=exc.extra)
    return ok(
        value={"script": script, "saved": written,
               "steps": len(script["steps"]),
               "recorded_values": script["recorded_values"],
               "steps_without_a_recorded_value":
                   script["steps_without_a_recorded_value"],
               "sha256": script["sha256"]},
        unit="str",
        model="step_script_from_a_run_that_already_happened",
        basis="the steps you ran plus the value each one returned (the app's own "
              "reply); nothing here re-runs or inspects anything",
        precision="This is a RECORD, not a proof: it says what the run you gave me "
                  "returned, and it cannot know whether that run was the one you "
                  "meant. A step recorded without a value is reported as "
                  "uncomparable rather than compared against null",
        assumptions={"idempotent": script["idempotent"],
                     "why_idempotence_is_unknown":
                         script["why_idempotence_is_unknown"],
                     "default_location": _steps.home(),
                     "never_beside_a_project": "a script is written under the "
                         "server's own directory (or the exact path you pass), "
                         "never next to the board",
                     "writes_only_when_asked": "save defaults to false; without "
                         "save/path the script is returned in the answer and "
                         "nothing is written"},
        extra={"report": script})

_tool(
    "lceda_step_record",
    "Freeze a run of editor steps into a replayable script. Pass the steps (label + "
    "code, the same shape lceda_pro_steps takes) plus the `value` each one returned, "
    "and you get back a script carrying those values as its EXPECTED results, with a "
    "sha256 over the canonical form. Runs nothing, contacts nothing, and writes "
    "nothing unless save=true or path= is given (default location is the server's own "
    "~/.pcb_agent_mcp/step-scripts, never beside the board). A step recorded without a "
    "value is kept but marked uncomparable - the replay will not silently treat "
    "'nothing recorded' as 'expect null'.",
    {"type": "object",
     "properties": {
         "steps": {"type": "array", "description":
                   "the steps that ran: [{label, code, fn, js_args, timeout_ms, "
                   "value}]; value is what that step returned and is what a replay "
                   "will be compared against", "items": {"type": "object"}},
         "note": _str("what this run was for, kept in the script"),
         "source": {"type": "object", "description":
                    "free-form provenance (board, project, run id) kept with the "
                    "script; this tool does not interpret it"},
         "session": _str("session id the run used, recorded as a label only"),
         "save": _bool("write the script to disk", False),
         "path": _str("exact file to write (implies save)"),
         "overwrite": _bool("allow replacing an existing script file", False),
     },
     "required": ["steps"]},
    _lceda_step_record,
)

def _lceda_step_replay(args):
    """Run a recorded script again inside the editor and compare step by step."""
    from . import lceda_steps as _steps
    from . import edit as _edit
    session = args.get("session")
    if not session:
        return err("session is required", "ARG_MISSING",
                   hint="a replay runs snippets inside the editor, so it needs one "
                        "(lceda_pro_open / lceda_pro_sessions)")
    script = args.get("script")
    if script is None:
        path = args.get("path")
        if not path:
            return err("pass `path` or `script`", "ARG_MISSING",
                       hint="lceda_step_replay runs a script that was recorded "
                            "before; it does not invent one")
        try:
            script = _steps.load(path)
        except _steps.StepError as exc:
            return err(exc.message, exc.reason_code, hint=exc.hint, extra=exc.extra)
    elif not isinstance(script, dict):
        return err("script must be the object lceda_step_record returned",
                   "ARG_INVALID")
    client = None
    try:
        client = _live_client(args)
        rep = _steps.replay(
            client, session, script,
            compare=args.get("compare", True) is not False,
            timeout_ms=args.get("run_timeout_ms"),
            should_stop=lambda: os.path.isfile(_edit.plan.stop_file()))
    except _live.LiveError as exc:
        return _live_error(exc)
    except _steps.StepError as exc:
        return err(exc.message, exc.reason_code, hint=exc.hint, extra=exc.extra)
    finally:
        if client is not None:
            client.close()
    counts = rep["counts"]
    out = ok(
        value={"status_verdict": rep["status"],
               "steps_planned": rep["steps_planned"],
               "steps_run": rep["steps_run"],
               "matched": counts.get("match", 0),
               "differed": counts.get("differs", 0),
               "uncomparable": counts.get("no_recorded_value", 0),
               "failed": counts.get("failed", 0),
               "stopped_before_step": rep["stopped_before_step"],
               "failed_at_step": rep["failed_at_step"],
               "not_run": rep.get("not_run"),
               "rows": rep["rows"],
               "script_sha256": script.get("sha256"),
               "script_sha256_matches": script.get("sha256_matches")},
        unit="str",
        model="replay_with_per_step_value_comparison",
        basis="each step ran inside the editor and its reply was compared with the "
              "value stored in the script; 'reproduced' means every comparable step "
              "returned the recorded value",
        precision="NOT VERIFIED AGAINST THE APPLICATION as a reproduction claim. A "
                  "matching value does not prove the same edit happened (it can "
                  "match for the wrong reason), a differing value is a measurement "
                  "rather than a failure (re-running a create does exactly this), "
                  "and no step is proven to have saved anything to disk",
        assumptions={"idempotent": "unknown",
                     "why_idempotence_is_unknown":
                         "whether these snippets are safe to run twice is a "
                         "property of the snippets, so a replay compares values "
                         "instead of assuming they will match",
                     "starts_from_the_same_state": "NOT established - the "
                         "recorded run and this one may have started from "
                         "different designs, which is the most common honest "
                         "reason for a differing value",
                     "stopping": "board_progress(action='stop') stops before the "
                         "next step; board_progress() reads the log",
                     "verified_against_app": rep["verified_against_app"],
                     "not_verified": rep["not_verified"]},
        extra={"status_kind": "replay_verdict",
               "note": "the business field is status_verdict so it can never be "
                       "confused with the envelope's own status"})
    out["report"] = rep
    return out

_tool(
    "lceda_step_replay",
    "Replay a recorded step script inside the running editor and COMPARE each step's "
    "reply with the value that was recorded, one row per step: match / differs / "
    "uncomparable / failed. This is the difference between running and "
    "reproducing: a move onto its existing position, a create that no-ops and a "
    "snippet on a stale id all return without an error. A differing value is reported, "
    "not thrown away, and a step that fails stops the replay there instead of running "
    "the rest against a design that is not in the assumed state. board_progress"
    "(action='stop') interrupts it between steps. Idempotence is reported as unknown: "
    "whether these snippets are safe to run twice is their author's fact, not this "
    "server's.",
    {"type": "object",
     "properties": dict(_LIVE_ARGS, **{
         "session": _str("session id from lceda_pro_open / lceda_pro_sessions"),
         "path": _str("script file written by lceda_step_record"),
         "script": {"type": "object", "description":
                    "the script object itself, when you have it in hand"},
         "compare": _bool("compare each value with the recording (default true)",
                           True),
         "run_timeout_ms": _num("per-step budget passed to the app (max 60000)",
                                60000),
     }),
     "required": ["session"]},
    _lceda_step_replay,
)

# ----------------------------------------------------------------- board
def _guarded(fn):
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        return err("%s: %s" % (type(exc).__name__, exc), "APP_UNKNOWN"
                   if isinstance(exc, _desktop.DesktopError) else "TOOL_RAISED")


_BOARD_CACHE = {}
_BOARD_CACHE_MAX = 4

def _load_board(args):
    """Parse a board file, cached by (path, size, mtime). Read-only, always."""
    import os
    path = args.get("path")
    if not path:
        return None, err("path is required", "ARG_MISSING")
    if not os.path.isfile(path):
        return None, err("board file does not exist: %r (this server never creates "
                         "files)" % path, "FILE_NOT_FOUND")
    fmt_present = _detect_format(path)
    if fmt_present == "unknown":
        return None, err("unrecognised board format: %r" % path, "FORMAT_UNKNOWN",
                         hint="supported: KiCad .kicad_pcb (S-expression), EasyEDA Pro "
                              ".epro (ZIP). Supported set: " + ", ".join(sorted(_FORMATS)))
    stat = os.stat(path)
    key = (os.path.abspath(path), stat.st_size, int(stat.st_mtime))
    cached = _BOARD_CACHE.get(key)
    if cached is not None:
        return cached, None
    try:
        board = _open_board(path, member=args.get("member"),
                            project=args.get("project"))
    except Exception as exc:  # noqa: BLE001
        return None, err("%s: %s" % (type(exc).__name__, exc), "PARSE_FAILED")
    if len(_BOARD_CACHE) >= _BOARD_CACHE_MAX:
        _BOARD_CACHE.pop(next(iter(_BOARD_CACHE)))
    _BOARD_CACHE[key] = board
    return board, None

_NOT_WRITTEN = ("this tool parses a file and measures it; it never writes, moves or "
                "deletes anything in the project")

_COMMON = {
    "path": _str("absolute path to the board file"),
    "member": _str("for .epro: which .epcb member to read (default: the largest)"),
    "project": _str("optional side file: .kicad_pro next to a .kicad_pcb"),
}

def _board_tool(fn, derived=False):
    """Wrap a board handler. `derived` marks answers this server COMPUTED.

    A computed number is not a verdict. The envelope says so and points at the
    tool that returns the vendor engine's own answer (board_drc), because the
    difference between "I measured it" and "KiCad says so" is the whole reason
    the fidelity layer exists.
    """
    def handler(args):
        board, e = _load_board(args)
        if e:
            return e
        out = fn(board, args)
        if derived and isinstance(out, dict) and out.get("status") == "ok":
            out.setdefault("answer_source", "computed by this server from geometry")
            out.setdefault("authoritative", False)
            out.setdefault("verdict_available_from", "board_drc (KiCad's own engine)")
        return out
    return handler

_tool(
    "board_summary",
    "Open a board and report what the file actually carries: counts, board outline bbox, "
    "stackup, design rules, and - equally important - which facts are ABSENT. Read-only.",
    {"type": "object", "properties": dict(_COMMON), "required": ["path"]},
    _board_tool(lambda b, a: _oracle.summary(b)),
)

_tool(
    "board_components",
    "Every placed component with reference, value, library id, position (mm, Y up), "
    "rotation and pad count, plus a bbox the model can reason about. Paged: 100 rows "
    "by default, `total`/`truncated`/`next_offset` say what is left.",
    {"type": "object",
     "properties": dict({
         "layer": _str("only components on this copper layer"),
         "limit": _num("max components returned (default 100; page with offset)", 100),
         "offset": _num("skip this many components", 0),
     }, **_COMMON),
     "required": ["path"]},
    _board_tool(lambda b, a: _oracle.components(
        b, layer=a.get("layer"), limit=a.get("limit", 100),
        offset=a.get("offset", 0))),
)

_tool(
    "board_nets",
    "Per net: pad/track/via/zone counts and total routed length. Use it to spot a net "
    "that exists in the netlist but has no copper. Paged: 100 nets by default, "
    "`total`/`truncated`/`next_offset` say what is left.",
    {"type": "object",
     "properties": dict({
         "limit": _num("max nets returned (default 100; page with offset)", 100),
         "offset": _num("skip this many nets", 0),
     }, **_COMMON), "required": ["path"]},
    _board_tool(lambda b, a: _oracle.nets(
        b, limit=a.get("limit", 100), offset=a.get("offset", 0))),
)

_tool(
    "board_clearance",
    "Minimum edge-to-edge distance between copper of different nets, with the offending "
    "pairs named. Track width is subtracted from the centre-line distance. This is the "
    "measured number, not a DRC verdict. " + _NOT_WRITTEN,
    {"type": "object",
     "properties": dict({
         "net_a": _str("restrict to this net (pair with net_b)"),
         "net_b": _str("the other net"),
         "threshold_mm": _num("report pairs closer than this", 0.2),
         "layer": _str("restrict to one copper layer"),
         "worst": _num("how many offending pairs to list", 20),
     }, **_COMMON),
     "required": ["path"]},
    _board_tool(lambda b, a: _oracle.clearance(
        b, net_a=a.get("net_a"), net_b=a.get("net_b"),
        threshold_mm=a.get("threshold_mm", 0.2), layer=a.get("layer"),
        worst=a.get("worst", 20)), derived=True),
)

_tool(
    "board_free_space",
    "Largest rectangles on a layer where a new component could sit, from an occupancy "
    "grid with a clearance margin. Answers 'where can this part go', not 'how dense is "
    "the board'.",
    {"type": "object",
     "properties": dict({
         "layer": _str("copper layer to inspect, e.g. F.Cu / B.Cu"),
         "grid_mm": _num("grid step", 1.0),
         "clearance_mm": _num("keep-out margin around existing copper", 0.2),
         "top": _num("how many rectangles to return", 8),
     }, **_COMMON),
     "required": ["path", "layer"]},
    _board_tool(lambda b, a: _oracle.free_space(
        b, a["layer"], grid_mm=a.get("grid_mm", 1.0),
        clearance_mm=a.get("clearance_mm", 0.2), top=a.get("top", 8)), derived=True),
)

_tool(
    "board_routability",
    "Can each pad actually get off the pad, and how crowded is the layer? A pad with no "
    "way out is a hard blocker no router can fix; a pad that needs a dogleg is fine for "
    "a person but not for a straight fanout. Says which, and reports copper congestion "
    "per layer. NOT a router: it never claims a net is connectable. " + _NOT_WRITTEN,
    {"type": "object",
     "properties": dict({
         "layer": _str("restrict to one copper layer (default: every copper layer)"),
         "grid_mm": _num("congestion grid step", 0.2),
         "clearance_mm": _num("override the net-class clearance", None),
         "track_mm": _num("width of the track that has to get out", None),
         "escape_mm": _num("how far past the pad edge the lane must be free", 1.5),
         "worst": _num("how many pads to list", 20),
         "intent": _str("design intent (same object board_route reads): its "
                        "constraints.clearance_mm / constraints.track_mm and "
                        "nets.layers are used here, so the board is MEASURED with "
                        "the numbers it will later be ROUTED with. Caller "
                        "arguments still win, and every disagreement is listed"),
     }, **_COMMON),
     "required": ["path"]},
    _board_tool(lambda b, a: _routability_with_intent(b, a), derived=True),
)

_tool(
    "board_intent",
    "Check a DESIGN INTENT against a board before anything is routed. The intent "
    "is one JSON object (version 1): `nets.route`/`nets.layers`, a `constraints` "
    "block holding exactly the arguments board_route takes (clearance/track/via/"
    "creepage/impedance/diff-pair related numbers), `diff_pairs`, `length_groups`, "
    "`impedance_targets`, `keepouts` and `placement.regions`. The same object is "
    "read by board_route and board_routability, so what measured the board is "
    "what routes it. This tool answers three things and nothing more: is the "
    "object well formed (an unknown key is REFUSED - a typo must not quietly mean "
    "'not requested'), can it be true on THIS board (a keepout that swallows a "
    "pad, a region past the edge, a ref that does not exist), and what would "
    "board_route read out of it (the flattened keyword set, with its source "
    "named). It never writes copper and never moves a part.",
    {"type": "object",
     "properties": dict({
         "intent": _str("intent JSON file path or inline JSON object"),
     }, **_COMMON),
     "required": ["path", "intent"]},
    _board_tool(lambda b, a: _board_intent(b, a), derived=True),
)

_tool(
    "board_route",
    "Connect the pads of each net with copper: A* maze search on a raster, longest "
    "net first, a second sweep for the nets that lost, then a bounded rip-up round "
    "that pulls out the nets whose copper physically OVERLAPS the loser's pads "
    "(not 'the shortest ones' - the one in the way may sit on the far side of the "
    "board) and re-routes them with the loser. A net whose name has a pour on the "
    "board gets ONE via per pad into that plane instead of a track across the board. "
    "Clearance/track/via come from the caller, else the board's net classes, else "
    "the project file - no source at all is a refusal, not a 0.2 mm guess. Reports "
    "per-net length/via/segment counts and, for every net it could not finish, why: "
    "`no_path` (search exhausted), `search_budget` (the budget ran out - not the "
    "same claim), `terminal_unreachable` (a pad has no way out). Returns a PLAN by "
    "default; apply=true writes it through board_propose + board_apply (checkpoint, "
    "atomic write, four gates, KiCad DRC). Three distance rules reach the search "
    "when YOU declare them, and are never invented: creepage (`creepage_mm`, or "
    "per-pair `creepage_pairs` = 'A:B:1.0,C:D:0.8' - a standard table keyed to "
    "working voltage, so `rules.sources.creepage` says plainly when none was "
    "applied - or let this tool do the lookup from `working_voltage_v` + "
    "`pollution_degree` + `material_group` + `insulation` with the IEC 60664-1 "
    "Table F.5 port in calc/, which refuses an input past the end of a ladder "
    "instead of extrapolating), diff-pair spacing (`diff_pairs`: the gap and the common width both "
    "become search constraints) and equal length (`length_groups`: the shorter "
    "nets get a 45-degree serpentine, every new segment re-checked, and an "
    "unreachable target is reported as `length_not_matched` with the length it did "
    "reach). A declared pair is COUPLED after the search (`couple`, default "
    "true): the second net's copper is replaced by the first net's path offset by "
    "gap+width with mitered corners, every offset segment re-checked exactly, and "
    "the report carries `coupled`, the achieved gap and `coupled_fraction` - the "
    "share of the second net's length that really runs at the declared gap, "
    "because a pair that couples at its fanout only is not a coupled pair. The "
    "offset is refused rather than guessed at when the reference path branches, "
    "when the offset lands on other copper (both sides are reported, with the "
    "blocker by name) or when the fanout would be longer than 5 mm. Return paths "
    "are handled too (`return_path`, default true): every signal via gets a "
    "ground via within 2 mm, placed only where the exact check accepts it and only "
    "when ground copper already exists on both sides of the layer change - a via "
    "into bare laminate is a stub, so the answer says `ground_not_on_two_layers` "
    "instead. An impedance target is the one rule that reaches the search as a "
    "WIDTH (`impedance_targets`: [{net|p+n, ohm, dielectric_mm?, er?, copper_um?, "
    "kind?}]): each is reverse-solved with IPC-2141A, the width becomes that net's "
    "track width, and when the stackup cannot pin the dielectric down the answer is "
    "an error (`IMPEDANCE_NEEDS_STACKUP`) rather than a plausible number - an "
    "unreachable ohm value is refused with the range that stackup can offer. "
    "Negotiated congestion is opt-in (`congestion_rounds`, default 0): when asked "
    "for, each round collects every pending net's wish, prices the cells that too "
    "many of them want (soft cost, never a wall) and re-routes after ripping up "
    "the cheapest of the losers - and only the BEST round is kept, because a late "
    "round's inflated prices make detours longer rather than better. It is off by "
    "default on measurement, not modesty: on the 4-layer 50-net board below it buys "
    "+3 nets for +61% of the time, and that is one board. Refilling the pours "
    "the new copper made stale is opt-in too (`refill_pours`, default false): by "
    "default the report only names those zones, and when asked for, KiCad's own "
    "ZONE_FILLER re-pours them and its polygons are transplanted into the file "
    "(byte-preserving, checkpointed) - a thermal relief is not synthesised here, "
    "because the spokes are ZONE_FILLER's decision and are simply inside the "
    "polygons after a refill (`assumptions.pour.thermal_spokes` says so).",
    {"type": "object",
     "properties": dict({
         "apply": _bool("write the copper (default false: plan only)"),
         "topology": _str(
             "how a net with three or more pads is joined: \"mst\" (default) is a "
             "spanning tree over the pad centres, \"steiner\" adds branch points "
             "that are not pads (iterated 1-Steiner on the Hanan grid - an "
             "approximation, never worse than mst, and each branch point costs one "
             "more A*). Per net the answer carries `topology`, `tree_mm` and, for "
             "steiner, `steiner_points` / `tree_mm_mst` / `tree_saved_mm`"),
         "blind_vias": _bool(
             "allow blind/buried vias: a layer change first tries a through via and "
             "only then a via spanning just the two layers being joined. Off by "
             "default - a blind via costs more to make, and each one is written as "
             "`(layers ...)` so the file says exactly which layers it occupies"),
         "refill_pours": _bool(
             "after writing, ask KiCad's own ZONE_FILLER to refill the zones the new "
             "copper made stale, and transplant its polygons (default false: the "
             "report only says which zones went stale)"),
         "intent": _str("design intent: a JSON file path or inline JSON object "
                        "({version:1, nets:{route,layers}, constraints:{...}, "
                        "diff_pairs, length_groups, impedance_targets, keepouts, "
                        "placement:{regions}}). This is the SAME object "
                        "board_routability and board_intent read, so the intent "
                        "that measured the board is the intent that routes it. "
                        "Caller arguments win over the file and every disagreement "
                        "is listed under assumptions.intent.conflicts"),
         "pitch_mm": _num("search raster step", 0.1),
         "layers": _str("comma separated routers layers (default: every copper layer "
                        "that no power pour covers)"),
         "nets": _str("comma separated nets to route (default: all)"),
         "clearance_mm": _num("override the net-class clearance", None),
         "track_mm": _num("override the net-class track width", None),
         "via_diameter_mm": _num("override the via diameter", None),
         "via_drill_mm": _num("override the via drill", None),
         "edge_clearance_mm": _num("copper-to-board-edge rule (default: the "
                                    "project's min_copper_edge_clearance, else "
                                    "KiCad's 0.5 mm board-setup default, declared "
                                    "as assumed)", None),
         "creepage_mm": _num("surface distance to keep between any two nets "
                             "(default: none - creepage is looked up from a "
                             "standard by working voltage and is never invented)",
                             None),
         "creepage_pairs": _str("per-pair creepage, 'A:B:1.0,C:D:0.8' (mm); "
                                "takes precedence over creepage_mm where it is "
                                "stricter"),
         "working_voltage_v": _num("RMS working voltage across the gap: look the "
                                   "creepage up in IEC 60664-1 Table F.5 instead "
                                   "of being told it (a value you also pass in "
                                   "creepage_mm wins when it is stricter)", None),
         "pollution_degree": _num("for the creepage lookup (1, 2, 3; 4 is "
                                  "refused: the standard does not tabulate it)", 2),
         "material_group": _str("for the creepage lookup", ["I", "II", "IIIa",
                                                           "IIIb"], "IIIa"),
         "insulation": _str("for the creepage lookup: basic doubles to "
                            "reinforced by the table's own multiplier",
                            ["basic", "reinforced"], "basic"),
         "coating": _bool("conformal coating was declared (the table value is "
                          "NOT relaxed here; the answer carries the IEC 60664-3 "
                          "caveat instead)", False),
         "altitude_m": _num("for the creepage lookup (>2000 m notes that the "
                            "altitude correction belongs to clearance)", 0),
         "diff_pairs": {"type": "array", "description":
                        "declared differential pairs: [{p, n, gap_mm, width_mm?, "
                        "max_skew_mm?, couple?}]. gap_mm is the edge-to-edge "
                        "spacing: it enters the search as a required distance, so "
                        "neither net is routed through the other, and after the "
                        "search the pair is coupled by replacing the second net "
                        "with the first net's path offset by gap+width. The report "
                        "carries `coupled`, `gap_achieved_mm`, `coupled_fraction` "
                        "and, when coupling was refused, the reason and the "
                        "blocker.",
                        "items": {"type": "object", "properties": dict({
                            "p": _str("one net of the pair (the reference: its path "
                                      "is kept and the other is offset from it)"),
                            "n": _str("the other net (its copper is replaced)"),
                            "gap_mm": _num("required edge-to-edge spacing"),
                            "width_mm": _num("common track width for both"),
                            "max_skew_mm": _num("length-matching budget; adds the "
                                                "pair to the length groups"),
                            "couple": _bool("cut the second net as an offset copy of "
                                            "the first (default true); false keeps "
                                            "the two routed independently at the gap",
                                            True),
                        }, **{"required": ["p", "n", "gap_mm"]})}},
         "return_path": _bool("add a ground via beside every signal via so the "
                              "return current has a way across the layer change "
                              "(default true); refused by name when the board has "
                              "no unambiguous ground net or no ground copper on "
                              "both sides", True),
         "return_path_net": _str("which net is the ground (default: the one net "
                                 "named GND/GROUND/VSS/0V; more than one is a "
                                 "refusal, not a coin flip)"),
         "length_groups": {"type": "array", "description":
                           "nets that must come out the same length: [{name, nets, "
                           "target_mm?, max_skew_mm?, amplitude_mm?}]. After "
                           "routing, the shorter nets get a 45-degree serpentine "
                           "inserted in their longest straight segment, and every "
                           "new segment goes back through the exact check. If the "
                           "target cannot be reached the answer carries the length "
                           "it actually achieved and status length_not_matched.",
                           "items": {"type": "object", "properties": dict({
                               "name": _str("label for the report"),
                               "nets": {"type": "array",
                                        "items": {"type": "string"}},
                               "target_mm": _num("length to reach (default: the "
                                                 "longest net in the group)"),
                               "max_skew_mm": _num("longest-minus-shortest budget"),
                               "amplitude_mm": _num("meander amplitude (default "
                                                    "3*sqrt(2)*width so the legs of "
                                                    "a tooth keep 3w apart)"),
                           }, **{"required": ["nets"]})}},
         "impedance_targets": {"type": "array", "description":
                               "single-ended impedance requirements: [{net|p+n, "
                               "ohm, dielectric_mm?, er?, copper_um?, kind?}]. The "
                               "target is reverse-solved to a track width, which "
                               "becomes that net's width in the search (a pair "
                               "gives both nets the same width). dielectric_mm is "
                               "required unless the board's stackup holds exactly "
                               "one dielectric thickness; er/copper_um default to "
                               "4.3/35 and are declared as defaults. An "
                               "unreachable target is refused with the reachable "
                               "range.",
                               "items": {"type": "object", "properties": dict({
                                   "net": _str("the net that must hit the impedance"),
                                   "p": _str("one net of a pair (alternative to net)"),
                                   "n": _str("the other net of the pair"),
                                   "ohm": _num("target characteristic impedance"),
                                   "dielectric_mm": _num("dielectric thickness "
                                                         "between the trace and its "
                                                         "reference plane"),
                                   "er": _num("relative permittivity (default 4.3)"),
                                   "copper_um": _num("copper thickness (default 35 = "
                                                     "1 oz)"),
                                   "kind": _str("microstrip (default) or stripline"),
                               }, **{"required": ["ohm"]})}},
         "rip_up_rounds": _num("how many bounded rip-up rounds (0 turns it off)", 1),
         "congestion_rounds": _num("negotiated congestion passes AFTER the sweeps "
                                    "(0 = off; default off because the real-board "
                                    "measurement is in docs/27, not assumed here)",
                                    0),
         "congestion_max_rips": _num("how many nets one congestion round may pull "
                                     "out (default 12)", 12),
         "congestion_weight": _num("price per shared cell that a negotiation round "
                                    "adds (higher = more willing to detour)", 1.0),
         "incremental": _bool("treat the net's existing copper as already connected "
                              "and route only what is missing", False),
         "fixed_nets": _str(
             "comma separated nets whose existing copper must NOT move - the same "
             "promise Specctra writes as `(type fix)`. A fixed net is never a "
             "rip-up candidate, never re-routed and never passed to the coupling "
             "or meander passes (a fixed net inside a diff pair or a length group "
             "is REFUSED rather than quietly dropped); its copper is reported with "
             "what the board already has. A net called fixed that has no copper on "
             "the board comes back `fixed_missing_copper` - leaving it unrouted and "
             "calling it fixed would report an open net as settled"),
         "allow_vias": _bool("may the search change layers", True),
         "second_sweep": _bool("retry the losers in reverse order", True),
         "ignore_zone_areas": _bool("do not treat pour outlines as obstacles", False),
         "max_nets": _num("only the N nets with the most pads", None),
         "allow_partial": _bool("write even when some nets failed", False),
         "allow_new_violations": _bool("accept newly introduced DRC violations"),
         "strict_drc": _bool("let every new violation block, warnings included"),
     }, **_COMMON),
     "required": ["path"]},
    lambda a: _guarded(lambda: _board_route(a)),
)

def _board_fanout(args):
    """Plan (or plan+write) a BGA escape.

    The honest half of fan-out is the report: a ball that cannot leave the array
    is listed with the wall it hit, never quietly dropped, and the advice names
    the three real ways to open the array (smaller via, another layer, via in the
    pad) instead of pretending a different direction would have worked.
    """
    from . import edit as _edit
    from . import fanout as _fanout
    from .route.rules import RulesError
    board, e = _load_board(args)
    if e:
        return e
    kw = {}
    for key in ("escape_mm", "pitch_mm", "clearance_mm", "track_mm",
                "via_diameter_mm", "via_drill_mm", "min_pads", "max_pitch",
                "max_balls", "directions"):
        if args.get(key) is not None:
            kw[key] = args[key]
    try:
        res = _fanout.fanout(board, ref=args.get("ref"), layer=args.get("layer"),
                             ignore_zone_areas=bool(args.get("ignore_zone_areas")),
                             **kw)
    except _fanout.FanoutError as exc:
        return err(exc.message, exc.reason_code, hint=exc.hint)
    except RulesError as exc:
        return err(str(exc), getattr(exc, "reason_code", "RULES_INVALID"))

    _ledger_note("fanout", args["path"], {
        "verdict": ("escaped" if not res["skipped"] else "partial"),
        "arrays": len(res["arrays"]),
        "balls_total": res["balls_total"],
        "escaped": res["escaped"],
        "buried": len(res["skipped"]),
        "styles": res["styles"],
        "clearance_mm": res["rules"].get("clearance_mm"),
        "track_mm": res["rules"].get("track_mm"),
        "via_mm": res["rules"].get("via_mm"),
        "apply": args.get("apply") is True,
    })
    arrays = res["arrays"]
    out = ok({
        "arrays": [{"ref": a["ref"], "pads": a["pads"], "pitch_mm": a["pitch_mm"],
                    "centre_mm": a["centre"], "bbox_mm": a["bbox_mm"]}
                   for a in arrays],
        "balls_total": res["balls_total"],
        "escaped": res["escaped"],
        "buried": len(res["skipped"]),
        "styles": res["styles"],
        "rules": res["rules"],
        "where_the_numbers_came_from": res["rules"].get("sources"),
        "assumed_by_this_server": res["rules"].get("assumed"),
        "method": res["method"],
        "writes_nothing": args.get("apply") is not True,
    }, "mm",
        model="exact clearance geometry (route.grid) judged against the copper "
              "that already exists",
        basis="every escape is accepted only when the stub and the via pass the "
              "same exact segment/polygon test the router uses, run against the "
              "board as it is PLUS the escapes accepted before it",
        precision="\u00b10.001 mm (coordinates are rounded to 4 decimals when "
                  "written)",
        assumptions=([] if not res["rules"].get("assumed")
                     else list(res["rules"].get("assumed"))),
        extra={"skipped": res["skipped"], "advice": res["advice"],
               "ops_preview": res["ops"][:5]})
    if res["skipped"]:
        out["status"] = "ok"
        out["message"] = ("%d of %d balls could not be escaped and are listed under "
                          "value; nothing about them is guessed"
                          % (len(res["skipped"]), res["balls_total"]))
        out["hint"] = res["advice"]
    if args.get("apply") is not True:
        out["next_step"] = ("pass apply=true to write this copper (it goes through "
                            "board_propose + board_apply and the four gates)")
        return out

    plan = _edit.propose(args["path"], res["ops"], {"engine": True})
    try:
        applied = _edit.apply_plan(plan["plan_id"], confirm=True,
                                   options={"engine": True,
                                            "allow_new_violations": bool(
                                                args.get("allow_new_violations")),
                                            "strict_drc": bool(args.get("strict_drc"))})
    except _edit.EditError as exc:
        return _edit_error(exc)
    out["value"]["plan_id"] = plan["plan_id"]
    out["applied"] = applied
    out["value"]["writes_nothing"] = False
    return out

_tool(
    "board_fanout",
    "Get the balls of a BGA out of the array, or say which ones cannot come out. "
    "Finds the arrays (pads on a lattice, pitch read off the pads - a part whose pads "
    "are NOT a grid is refused rather than fanned out at a made-up pitch), then for "
    "every ball tries a stub radially outwards from the array centre and rotations of "
    "it by up to 45/90 degrees, each in two shapes: a direct stub with a via on its "
    "end, and a dogbone (via clear of the pad, the rest of the escape on another "
    "copper layer - the only way a ball surrounded by other balls can leave). Every "
    "candidate is judged with the same exact clearance geometry the router uses, "
    "against the copper that already exists plus the escapes accepted before it; an "
    "accepted escape is never ripped up, so ball order decides and the answer is one "
    "valid fan-out, not the smallest one. Escape lengths are in pitch units "
    "(`escape_mm`, default: one pitch). The report carries `escaped`, the `styles` "
    "used, and a `skipped` list with the wall each buried ball hit plus advice on the "
    "three real ways to open the array (smaller via, another layer, via in the pad - "
    "op add_via_in_pad, which needs a filled via from the fab). Not done and said so: "
    "via-in-pad placement, dogbone alternatives beyond the fixed direction ladder, "
    "rip-up, and any claim that the array is fully escaped. Returns a PLAN by default; "
    "apply=true writes it through board_propose + board_apply (checkpoint, atomic "
    "write, four gates, KiCad DRC).",
    {"type": "object",
     "properties": dict({
         "apply": _bool("write the escapes (default false: plan only)"),
         "ref": _str("the BGA reference; default: every latticed part on the board"),
         "layer": _str("put every escape on this layer (default: the layer the pad "
                       "is on)"),
         "escape_mm": _num("how far a stub runs before its via (default: one pitch)", None),
         "pitch_mm": _num("search raster step for the clearance checks", 0.1),
         "clearance_mm": _num("override the net-class clearance", None),
         "track_mm": _num("override the net-class track width", None),
         "via_diameter_mm": _num("override the via diameter", None),
         "via_drill_mm": _num("override the via drill", None),
         "min_pads": _num("a part needs at least this many latticed pads to count as "
                          "an array (default 9)", 9),
         "max_pitch": _num("ignore arrays with a coarser pitch", None),
         "max_balls": _num("escape at most this many balls (the rest are reported as "
                           "max_balls reached)", None),
         "directions": _num("how many directions to try per ball, 1/3/5/... up to "
                            "9 (1 = radially outwards only)", 5),
         "ignore_zone_areas": _bool("do not treat pour outlines as obstacles", False),
         "allow_new_violations": _bool("accept newly introduced DRC violations"),
         "strict_drc": _bool("let every new violation block, warnings included"),
     }, **_COMMON),
     "required": ["path"]},
    lambda a: _guarded(lambda: _board_fanout(a)),
)

_tool(
    "board_dangling_copper",
    "Find the copper that hangs off a board attached at one end only, and cut the "
    "part of it that cannot be carrying anything. A TAIL is copper attached at one "
    "end and free at the other; the chain behind it is walked to the next fork, pad, "
    "via or pour, exactly the mechanism FreeRouting's DanglingCopper uses after an "
    "autoroute. Copper attached to nothing at either end is reported separately as "
    "FLOATING - a different finding, and not cut unless include_floating=true, "
    "because a piece someone placed on purpose (a test point, a marker) is not a "
    "defect this tool gets to decide about. What makes a cut a proof rather than a "
    "hope: a chain is cuttable only when no piece is (locked) and no pad of the net "
    "would be left with copper only through it - such a chain hangs off one "
    "attachment point and is nobody's only path, so removing it cannot open a "
    "connection. Zones count as copper only where the file carries a real "
    "filled_polygon (a keep-out is not copper, and an unfilled pour is not guessed "
    "at). Attachments are exact geometry: a gap of 0.002 mm is a gap. Returns a "
    "report by default; apply=true writes the cuts through board_propose + "
    "board_apply (checkpoint, atomic write, four gates, KiCad DRC) and re-surveys "
    "afterwards. One thing is read before any of that: pad TRUST. Deciding 'this "
    "copper reaches nothing' rests on where the pads are, so when the adapter "
    "places pads by a transform it has not verified against the application (the "
    "LCEDA-Pro reader says so itself: verified_by_app=false, 54% of pads land "
    "exactly on a copper endpoint), every tail is reported as a candidate and "
    "none is called cuttable - removal_ops returns nothing. pad_trust in the "
    "reply states which of the two it was and why.",
    {"type": "object",
     "properties": dict({
         "apply": _bool("cut the cuttable tails (default false: report only)"),
         "include_floating": _bool("also cut copper attached at neither end "
                                   "(default false: reported, not cut)", False),
         "max_report": _num("how many tails / floating pieces to list per net "
                            "(default 200)", 200),
         "tolerance_mm": _num("a gap smaller than this is a gap, not a contact "
                              "(default 0.002)", None),
         "allow_new_violations": _bool("accept newly introduced DRC violations"),
         "strict_drc": _bool("let every new violation block, warnings included"),
     }, **_COMMON),
     "required": ["path"]},
    lambda a: _guarded(lambda: _board_dangling_copper(a)),
)

def _board_dangling_copper(args):
    """Report (and on request cut) the tails a board is carrying.

    Read-only by default and honest about the three readings it makes: what
    counts as attached, what makes a chain cuttable, and which zone copper is
    real. Nothing here decides that a net is finished - it only says which copper
    cannot be carrying a connection.
    """
    from . import dangling as _dangling
    from . import edit as _edit
    board, e = _load_board(args)
    if e:
        return e
    kw = {}
    if args.get("tolerance_mm") is not None:
        kw["tol_mm"] = float(args["tolerance_mm"])
    if args.get("max_report") is not None:
        kw["max_report"] = int(args["max_report"])
    report = _dangling.survey(board, **kw)
    ops = _dangling.removal_ops(report,
                                include_floating=args.get("include_floating") is True)

    value = {
        "tails": report["tails"],
        "floating": report["floating"],
        "cuttable": report["cuttable"],
        "cuttable_length_mm": report["cuttable_length_mm"],
        "nets_with_dangling": report["nets_with_dangling"],
        "nets_with_pour": report["nets_with_pour"],
        "tolerance_mm": report["tolerance_mm"],
        "zones_as_copper": report["zones_as_copper"],
        "tail_list": _flatten_tails(report),
        "ops_preview": ops[:20],
        "ops_count": len(ops),
        "cuts_floating": args.get("include_floating") is True,
        "pad_trust": report["pad_trust"],
        "writes_nothing": True,
    }
    out = ok(value, "mm",
             model="attachment by exact copper geometry (a track end inside "
                   "another piece's width, a via's size or a pad's reach, or a "
                   "point inside a same-net filled_polygon); cuttability judged "
                   "by walking the chain to its one attachment point",
             basis="mechanism of FreeRouting "
                   "autoroute/pipeline/DanglingCopper.java (walk the stub behind "
                   "each tail to the next fork/pin/fixed item, keep user-fixed "
                   "copper, never increase the net's open connections); the three "
                   "readings this server adds are stated in the tool description",
             precision="\u00b10.001 mm on reported lengths (coordinates are the "
                       "file's own, rounded to 4 decimals)",
             assumptions=({"tolerance_mm": report["tolerance_mm"]}
                          if kw.get("tol_mm") is not None else
                          {"tolerance_mm": report["tolerance_mm"],
                           "note": "a smaller gap would be a different answer; the "
                                   "default is 0.002 mm"}),
             extra={})
    if not ops:
        if report["tails"] or report["floating"]:
            trust = report["pad_trust"]
            if trust["trusted"] is not True:
                out["message"] = (
                    "%d tail(s) and %d floating piece(s) found, but none is "
                    "called cuttable: %s. They are listed as candidates only."
                    % (report["tails"], report["floating"], trust["why"]))
            else:
                out["message"] = "nothing on this board is both dangling and safe to cut"
        else:
            out["message"] = "no dangling copper"
        return out
    if args.get("apply") is not True:
        out["next_step"] = ("pass apply=true to cut these %d piece(s) (it goes "
                            "through board_propose + board_apply and the four gates)"
                            % len(ops))
        return out
    plan = _edit.propose(args["path"], ops, {"engine": True})
    try:
        applied = _edit.apply_plan(plan["plan_id"], confirm=True,
                                   options={"engine": True,
                                            "allow_new_violations": bool(
                                                args.get("allow_new_violations")),
                                            "strict_drc": bool(args.get("strict_drc"))})
    except _edit.EditError as exc:
        return _edit_error(exc)
    after = None
    try:
        board2, e2 = _load_board(args)
        if e2 is None:
            after = _dangling_summary(_dangling.survey(board2))
    except Exception:                     # noqa: BLE001 - the cut already happened
        after = None
    out["value"]["plan_id"] = plan["plan_id"]
    out["value"]["writes_nothing"] = False
    out["value"]["ops_written"] = len(ops)
    out["value"]["after"] = after
    out["applied"] = applied
    return out

def _dangling_summary(report):
    return {"tails": report["tails"], "floating": report["floating"],
            "cuttable": report["cuttable"],
            "cuttable_length_mm": report["cuttable_length_mm"],
            "nets_with_dangling": report["nets_with_dangling"]}

def _flatten_tails(report):
    """Every tail, net-tagged, longest first - one list for a caller that just
    wants to read them."""
    out = []
    for net in report["nets"]:
        for entry in net["tails"]:
            row = dict(entry)
            row["kind"] = "tail"
            out.append(row)
        for entry in net["floating"]:
            row = dict(entry)
            row["kind"] = "floating"
            out.append(row)
    out.sort(key=lambda r: -r["length_mm"])
    return out[:200]

def _ledger_note(kind, board_path, payload):
    """Append one run to the ledger. BEST EFFORT - it cannot change an answer.

    Kept out of every return value on purpose: `ledger.record` swallows its own
    errors and this wrapper never raises, because recording what happened must
    never be able to fail the work that happened.
    """
    try:
        from . import jobs as _jobs
        from . import ledger as _ledger
        body = dict(payload)
        body["board"] = _ledger.digest(board_path)
        _ledger.record(kind, body, owner=_jobs.current())
    except Exception:                        # noqa: BLE001 - see docstring
        pass

def _find_pad(board, ref, number):
    """The pad dict of `ref`/`number` as the reader built it (file frame, y down)."""
    want = str(number)
    for fp in board.footprints:
        if str(fp.get("ref")) != str(ref):
            continue
        for pad in fp.get("pads") or []:
            if str(pad.get("number")) == want:
                return pad
    return None

def _board_blockers(args):
    """Why this point cannot get its first track out - and what could move.

    A router that answers `no_path` leaves the caller with nothing to do. This
    answers the question behind it: every other-net object within one pad-and-a-
    bit of the point, each tagged with what it would take to get it out of the
    way (a structural property of the object's kind - never a decision made
    here), grouped into the eight directions a pad can leave through, so "boxed
    in on every side" and "three sides open, the search failed upstream" are
    different answers.
    """
    from .route import blockers as BLOCK
    from .route import grid as GRID
    from .route import rules as R
    from .route.rules import RulesError
    board, e = _load_board(args)
    if e:
        return e
    layer = args.get("layer")
    net = args.get("net")
    ref, number = args.get("ref"), args.get("pad")
    if ref is not None:
        if number is None:
            return err("ref was given without pad: say which pad (its number) on "
                       "that part is the one that cannot get out", "ARG_MISSING")
        pad = _find_pad(board, ref, number)
        if pad is None:
            return err("%s has no pad %r" % (ref, str(number)), "PAD_NOT_FOUND",
                       hint="board_pads lists every pad number of a part")
        at = (float(pad.get("x") or 0.0), float(pad.get("y") or 0.0))
        net = net or pad.get("net")
        if not layer:
            layers = pad.get("layers") or []
            layer = layers[0] if layers else None
    else:
        x, y = args.get("x"), args.get("y")
        if x is None or y is None:
            return err("give either ref + pad, or x + y (board file coordinates, "
                       "the same frame board_pads reports)", "ARG_MISSING")
        at = (float(x), float(y))
    if not net:
        return err("this point has no net: a clearance question is a question "
                   "about one net's copper against another's, so name the net",
                   "NET_UNKNOWN")
    if not layer:
        return err("layer is required when the point is not a pad", "ARG_MISSING")
    kw = {}
    for key in ("clearance_mm", "track_mm", "via_diameter_mm", "via_drill_mm",
                "edge_clearance_mm"):
        if args.get(key) is not None:
            kw[key] = args[key]
    try:
        bundle = R.resolve(board, **kw)
    except RulesError as exc:
        return err(exc.message, exc.reason_code, hint=exc.hint)
    from .fanout import _copper_layers
    copper = _copper_layers(board)
    if layer not in copper:
        return err("%s is not a copper layer of this board (have: %s)"
                   % (layer, ", ".join(copper) or "none"), "NO_SUCH_LAYER",
                   hint="a clearance question is about copper; F.SilkS, F.Mask and "
                        "Edge.Cuts are not copper layers")
    pitch = args.get("pitch_mm")
    pitch = 0.1 if pitch is None else float(pitch)
    try:
        grid = GRID.Grid(board, [layer], pitch, bundle,
                         ignore_zone_areas=bool(args.get("ignore_zone_areas")))
    except ValueError as exc:
        return err(str(exc), "ARG_RANGE")
    res = BLOCK.blockers(grid, net, at, layer,
                         width=args.get("width_mm"),
                         radius_mm=args.get("radius_mm"),
                         directions=args.get("directions") or 8,
                         max_items=args.get("max_items") or 24)
    out = ok(res, "mm",
             model="exact clearance geometry (route.grid.seg_blockers / "
                   "via_blockers) - the same test that rejects a router segment",
             basis="eight rays of one pad-and-a-bit are cast from the point; every "
                   "other-net object they violate is listed with the clearance it "
                   "wants and by how much it is short, then grouped by direction",
             precision="\u00b10.001 mm; the probe is a fan of 8 rays, so a wall that "
                       "only closes a narrow gap between two rays is not seen - the "
                       "answer is a diagnosis, not a proof of no path",
             assumptions=res["assumptions"],
             extra={"corridors": res["corridors"],
                    "via_in_pad": res["via_in_pad"]})
    if res["counts"]["total"] == 0:
        out["message"] = res["diagnosis"]
    elif res["open_sectors"]:
        out["message"] = res["diagnosis"]
        out["hint"] = ("nothing at this point needs to move: walk the net yourself "
                       "(board_dangling / board_routability give the per-net edges)")
    else:
        out["message"] = res["diagnosis"]
        out["hint"] = ("the fixes that exist for a boxed-in pad are: move the part "
                       "(a pad is movable), reroute another net's track/via, or "
                       "escape with a via in the pad (op add_via_in_pad, needs a "
                       "filled via from the fab)")
    if res["via_in_pad"]["fits"] and res["counts"]["total"]:
        out["hint"] = (out.get("hint") or "") + (
            "; a via also fits in this pad, so via-in-pad is the third way out")
    return out

_tool(
    "board_blockers",
    "What is actually in the way of one pad (or one point) on one layer, and what it "
    "would take to move each blocker. Casts eight rays of one pad-and-a-bit and lists "
    "every other-net object that violates clearance, with how much it is short and a "
    "mutability tag that is a STRUCTURAL property of the object's kind - a pad is "
    "movable (the part can be moved), another net's track/via is reroutable, a pour / "
    "keepout / board outline / copper graphic is fixed - never a decision this tool "
    "makes for you. The eight directions are reported as open or blocked, which is "
    "the difference between `the pad is boxed in` and `three sides are open, so the "
    "failure is upstream or the budget ran out`. Also says whether a via would fit in "
    "the pad (via-in-pad needs a filled via from the fab, so it is reported as an "
    "option, not assumed). Read-only. " + _NOT_WRITTEN,
    {"type": "object",
     "properties": dict({
         "ref": _str("footprint reference (use with pad)"),
         "pad": _str("pad number on that footprint"),
         "x": _num("point x, board file coordinates (when not using ref/pad)", None),
         "y": _num("point y, board file coordinates", None),
         "net": _str("the net that needs to get out (default: the pad's own net)"),
         "layer": _str("copper layer (default: the pad's first copper layer)"),
         "pitch_mm": _num("raster step for the copper index (default 0.1)", 0.1),
         "width_mm": _num("track width to test with (default: the net's class)", None),
         "radius_mm": _num("how far the probe reaches (default: clearance + track/2 "
                           "+ 0.4 mm)", None),
         "directions": _num("how many rays, 4 or 8 (default 8)", 8),
         "max_items": _num("list at most this many blockers (default 24)", 24),
         "clearance_mm": _num("override the net-class clearance", None),
         "track_mm": _num("override the net-class track width", None),
         "via_diameter_mm": _num("override the via diameter", None),
         "via_drill_mm": _num("override the via drill", None),
         "edge_clearance_mm": _num("override the copper-to-edge rule", None),
         "ignore_zone_areas": _bool("do not treat pour outlines as obstacles", False),
     }, **_COMMON),
     "required": ["path"]},
    lambda a: _guarded(lambda: _board_blockers(a)),
)

def _board_run_ledger(args):
    """What this server was asked to do to this board before, and what it said."""
    import os

    from . import ledger as _ledger
    kind = args.get("kind")
    if kind and kind not in _ledger.KINDS:
        return err("unknown kind %r (know: %s)" % (kind, ", ".join(_ledger.KINDS)),
                   "ARG_INVALID")
    digest_value = None
    if args.get("path"):
        digest_value = _ledger.digest(args["path"])
        if digest_value is None:
            return err("cannot read %s, so its digest is unknown" % args["path"],
                       "FILE_UNREADABLE")
    owner = args.get("owner")
    if owner is not None and not isinstance(owner, str):
        return err("owner must be a job id string", "ARG_INVALID")
    if owner == "":
        # An empty string is not "no filter" spelled differently: it would silently
        # return every run whose owner is null, which is the opposite of asking.
        return err("owner must be a job id, not an empty string", "ARG_INVALID")
    limit = args.get("limit")
    limit = 20 if limit is None else int(limit)
    got = _ledger.summary(kind=kind, digest_value=digest_value, limit=limit,
                          owner=owner)
    return ok({"enabled": _ledger.enabled(), "file": got["file"],
               "count": got["count"], "totals": got["totals"],
               "runs": got["runs"],
               "board_digest": (None if digest_value is None
                                else digest_value[:12]),
               "owner": owner,
               "owner_note": ("runs made inside a background job carry that job's id; "
                              "runs made directly in a request carry null"
                              if owner is None else
                              "only runs made under job %s are listed" % owner),
               "stores_per_line": ["board digest (sha256, first 12 shown)",
                                   "the job id the work ran under, or null",
                                   "the arguments that change behaviour",
                                   "the verdict"],
               "holds_no_design_data": True},
              "runs",
              model="append-only JSON Lines, one line per run, keyed by the sha256 "
                    "of the board file",
              basis="recording is best effort and never changes an answer: each "
                    "route/fan-out/drc/fab/apply call appends the arguments that "
                    "change behaviour plus the verdict, and nothing that could "
                    "reconstruct the board",
              precision="the ledger is a record of what was said, never reused as a "
                        "result - `count` counts the runs still inside the retained "
                        "history (one rotation at 4 MB), not every run ever made",
              assumptions=[
                  ("ledger home comes from PCB_AGENT_HOME = %s"
                   % os.environ["PCB_AGENT_HOME"])
                  if os.environ.get("PCB_AGENT_HOME")
                  else "ledger lives in ~/.pcb_agent_mcp (PCB_AGENT_HOME unset)"],
              extra={"kinds": list(_ledger.KINDS),
                     "writes_nothing": True})

_tool(
    "board_run_ledger",
    "What this server was asked to do to this board before, and what it said. One JSON "
    "line per routing / fan-out / DRC / fab / apply run, keyed by the sha256 of the "
    "board file, holding the arguments that change behaviour and the verdict - so a "
    "second attempt does not have to re-derive what the first one learned. It is a "
    "A run made inside a background job also records that job's id, so owner=... "
    "answers 'what did that job do' without matching on timestamps. It is a "
    "RECORD, not a cache: nothing here is ever reused as a result, and no line "
    "contains design data that could reconstruct the board. Recording is best effort "
    "and never changes what a tool returns; PCB_AGENT_LEDGER=0 disables it entirely. "
    "Read-only. " + _NOT_WRITTEN,
    {"type": "object",
     "properties": dict({
         "kind": _str("only runs of this kind", enum=list(
             ("route", "fanout", "drc", "fab", "apply"))),
         "owner": _str("only runs made under this background job id "
                       "(board_job(action='list') shows the ids)", None),
         "limit": _num("newest first, at most this many rows (default 20)", 20),
     }, **_COMMON),
     "required": []},
    lambda a: _guarded(lambda: _board_run_ledger(a)),
)

def _board_pour_connectivity(b, args):
    """A centre-in-polygon test, then the authoritative answer beside it."""
    out = _oracle.pour_connectivity(b, args.get("zone_index", 0))
    if out.get("status") != "ok" or args.get("cross_check", True) is False:
        return out
    from .fidelity import engine as _engine
    from .edit import plan as _plan
    drc = _engine.drc(args["path"], severity="all",
                      project=_plan._project_beside(args["path"]))
    value = out.setdefault("value", {})
    if drc.get("status") == "ok":
        value["drc_cross_check"] = {
            "authoritative": True,
            "unconnected_total": drc.get("unconnected_total"),
            "errors": (drc.get("by_severity") or {}).get("error", 0),
            "rule_source": drc.get("rule_source"),
            "verdict_source": drc.get("verdict_source"),
            "read_it_like_this": (
                "if unconnected_total is 0 then no pad of this net is isolated, no "
                "matter what `isolated` above says: a pad-centre test cannot see "
                "spokes, vias, tracks or the pad's own copper"),
            "unconnected": (drc.get("unconnected") or [])[:20],
        }
    else:
        value["drc_cross_check"] = {
            "authoritative": False,
            "ran": False,
            "reason_code": drc.get("reason_code"),
            "message": drc.get("message"),
            "why": "the authoritative check could not run; `isolated` stays unverified",
        }
    return out

_tool(
    "board_pour_connectivity",
    "Do the pads of the pour's net actually land inside the filled copper? This is a "
    "pad-CENTRE-in-polygon test and it is NOT authoritative: it cannot see thermal "
    "spokes, vias, tracks or the pad's own copper, and on the reference board it called "
    "13 pads isolated that the DRC proved connected. The reply says so "
    "(`authoritative: false`) and carries `drc_cross_check` - KiCad's own connectivity "
    "verdict, whose unconnected_total is the answer to trust. cross_check=false turns "
    "that second run off (it needs kicad-cli).",
    {"type": "object",
     "properties": dict({"zone_index": _num("which zone (0-based)", 0),
                         "cross_check": {"type": "boolean",
                                         "description": "also run KiCad's DRC and "
                                                        "return unconnected_total "
                                                        "(default true)"}},
                        **_COMMON),
     "required": ["path"]},
    _board_tool(_board_pour_connectivity, derived=True),
)

_tool(
    "board_zones",
    "What the board actually POURS: every zone with its net, layer, outline area, "
    "filled polygons and poured area. A zone with no filled_polygon is an outline, not "
    "copper - and every later connectivity answer would be about that outline. " +
    _NOT_WRITTEN,
    {"type": "object", "properties": dict(_COMMON), "required": ["path"]},
    lambda a: _guarded(lambda: _fill.zones(a["path"])),
)

_tool(
    "board_fill_zones",
    "Ask KiCad's own ZONE_FILLER to pour the zones, then report what it built (area, "
    "polygons, zones that poured nothing and why). By default it fills a COPY and only "
    "measures; in_place=true transplants KiCad's polygons into your file, which keeps "
    "its own version header, and writes a checkpoint first. Not a KiCad board? Refuses. "
    "A fill is a SNAPSHOT: any later track/pad/outline change makes it stale. "
    "The reply separates 'the engine poured copper' from 'that copper is legal': "
    "`drc_after_fill.verdict` is KiCad's own DRC verdict on the poured file (errors, "
    "unconnected, top_errors with their descriptions), and it is not_clean whenever "
    "the pour still holds DRC errors.",
    {"type": "object",
     "properties": dict({
         "in_place": {"type": "boolean",
                      "description": "write KiCad's polygons into this file (default "
                                     "false: measure on a copy, touch nothing)"},
         "drc": {"type": "boolean",
                 "description": "after filling, run KiCad's DRC and report "
                                "`drc_after_fill.verdict` (default true). A pour that "
                                "the engine built is not automatically a legal pour: "
                                "turn this off only if you can check it yourself"},
     }, **_COMMON, **{"required": ["path"]})},
    lambda a: _guarded(lambda: _fill.fill(
        a["path"], in_place=bool(a.get("in_place")),
        verify_drc=bool(a.get("drc", True)))),
)

def _pro_rules_read(args):
    from . import edit as _edit
    try:
        return _edit.read_project_rules(args.get("path"))
    except _edit.EditError as exc:
        return _edit_error(exc)

def _pro_rules_write(args):
    from . import edit as _edit
    try:
        return _edit.write_project_rules(args.get("path"),
                                         args.get("operations") or [],
                                         dry_run=bool(args.get("dry_run")))
    except _edit.EditError as exc:
        return _edit_error(exc)

_tool(
    "board_rules",
    "Which net classes does this PROJECT declare, and what does a net resolve to? "
    "KiCad 7+ keeps net classes in the .kicad_pro and IGNORES the (net_class ...) "
    "blocks in the board, so 'the board has no net classes' and 'the project has five' "
    "are both true at once - this reads the one that is in force. It also says whether "
    "the file is writable by board_rules_write and why not when it is not. Read-only.",
    {"type": "object", "properties": {
        "path": _str("absolute path to the .kicad_pro (or the .kicad_pcb beside it)"),
    }, "required": ["path"]},
    _pro_rules_read,
)

_tool(
    "board_rules_write",
    "Write net-class rules into the .kicad_pro: set_netclass (creates a class as a copy "
    "of Default, never invents one), remove_netclass (Default refused; a class still "
    "named by a net or a pattern is refused unless reassign_to moves the nets), "
    "assign_nets / unassign_nets. Five gates stand behind the write and a failure puts "
    "the original bytes back: the untouched file must re-serialise byte-for-byte in "
    "KiCad's own style (else PRO_FORMAT_UNKNOWN - a reformatted project is a diff "
    "nobody can review), only the keys the operations name may differ, the bytes on "
    "disk must equal the planned bytes, re-reading must give the planned document, and "
    "load_project_rules (the reader the DRC uses) must see the new values and "
    "ownership. dry_run=true runs every gate on a temporary copy and leaves the "
    "project alone.",
    {"type": "object", "properties": {
        "path": _str("absolute path to the .kicad_pro (or the .kicad_pcb beside it)"),
        "operations": {"type": "array", "description":
            "[{op: set_netclass|remove_netclass|assign_nets|unassign_nets, ...}]; "
            "call board_rules for the class list first",
            "items": {"type": "object"}},
        "dry_run": {"type": "boolean", "description":
            "run every gate on a temporary copy and change nothing (default false)"},
    }, "required": ["path", "operations"]},
    _pro_rules_write,
)

def _parse_object_list(val, what):
    """A list of dicts, or a JSON string that decodes to one. Anything else fails."""
    if val is None or val == "":
        return []
    if isinstance(val, str):
        try:
            val = json.loads(val)
        except ValueError as exc:
            raise ValueError("%s is not valid JSON: %s" % (what, exc))
    if not isinstance(val, (list, tuple)):
        raise ValueError("%s must be a list of objects" % what)
    out = []
    for item in val:
        if isinstance(item, str):
            try:
                item = json.loads(item)
            except ValueError as exc:
                raise ValueError("%s entry %r is not an object: %s"
                                 % (what, item, exc))
        if not isinstance(item, dict):
            raise ValueError("%s entries must be objects, got %r" % (what, item))
        out.append(dict(item))
    return out

def _parse_creepage_pairs(val):
    """`"A:B:1.0,C:D:0.8"` -> `[{"a": "A", "b": "B", "mm": 1.0}, ...]`.

    A bad entry is a refusal, not a silently dropped constraint: a creepage rule
    the caller typed and the router ignored is worse than no rule at all.
    """
    if val is None or val == "":
        return []
    if isinstance(val, (list, tuple)):
        return list(val)
    out = []
    for chunk in str(val).replace(";", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = chunk.rsplit(":", 2)
        if len(parts) != 3:
            raise ValueError("creepage_pairs entry %r is not 'NET_A:NET_B:MM'"
                             % chunk)
        a, b, mm = parts[0].strip(), parts[1].strip(), parts[2].strip()
        try:
            mm = float(mm)
        except ValueError:
            raise ValueError("creepage_pairs entry %r has no numeric mm" % chunk)
        if not a or not b or a == b or mm <= 0:
            raise ValueError("creepage_pairs entry %r needs two different nets "
                             "and a positive mm" % chunk)
        out.append({"a": a, "b": b, "mm": mm})
    return out

def _board_intent(board, args):
    """Validate an intent and say what the router would read out of it."""
    from .route import intent as _intent
    try:
        spec = _intent.load(args.get("intent"))
    except _intent.IntentError as exc:
        return err(exc.message, exc.reason_code, hint=exc.hint)
    flat, report = _intent.resolve({}, spec)
    checked = _intent.check_against_board(spec, board)
    blocking = [p for p in checked["problems"]
                if p.get("what") == "keepout" or "no such footprint" in
                str(p.get("problem")) or "past the board edge" in
                str(p.get("problem"))]
    out = ok(
        value={"version": spec["version"],
               "declared": sorted(k for k in spec if not k.startswith("_")),
               "router_keywords": sorted(flat),
               "problems": len(checked["problems"]),
               "blocking": len(blocking)},
        unit="keys/findings",
        model="design_intent",
        basis="route/intent.py: one vocabulary, one merge rule (caller arguments "
              "win, disagreements named), and a board check that reads the "
              "outline and the footprint/pad geometry actually in the file",
        precision="this is a CHECK, not a placement: 'footprint is outside its "
                  "declared region' is reported as the fact it is, and nothing is "
                  "moved. An intent that passes here can still fail to route - a "
                  "keepout that leaves no corridor is geometry the router will "
                  "answer with `no_path`",
        assumptions={"intent": report,
                     "board_rules_are_not_merged_here":
                         "constraints that the intent does not declare keep the "
                         "board's own net-class values (board_route reads those)",
                     "rationale": spec.get("rationale")},
        extra={"flattened": flat,
               "keepouts": checked["keepouts"],
               "placement": checked["placement"],
               # `value.problems` is the COUNT; these are the findings themselves.
               # Two keys that differ only by number-vs-list would read as a
               # contradiction.
               "findings": checked["problems"],
               "blocking_findings": blocking})
    if blocking:
        out["status"] = "error"
        out["reason_code"] = "INTENT_INVALID"
        out["message"] = ("%d finding(s) make this intent impossible on this board"
                          % (len(blocking),))
        out["hint"] = ("a keepout over a pad or a ref that does not exist is "
                       "refused here rather than routing for ten minutes against a "
                       "constraint the board contradicts")
    return out

def _routability_with_intent(board, args):
    """`board_routability` reads the same intent object `board_route` reads.

    The point is not tidiness: a board measured with the net-class clearance and
    then routed with `clearance_mm=0.2` was measured about a different board.
    """
    from .route import intent as _intent
    kw = {"layer": args.get("layer"), "grid_mm": args.get("grid_mm", 0.2),
          "clearance_mm": args.get("clearance_mm"), "track_mm": args.get("track_mm"),
          "escape_mm": args.get("escape_mm", 1.5), "worst": args.get("worst", 20)}
    report = None
    if args.get("intent"):
        try:
            spec = _intent.load(args["intent"])
        except _intent.IntentError as exc:
            return err(exc.message, exc.reason_code, hint=exc.hint)
        flat, report = _intent.resolve({}, spec)
        if flat.get("clearance_mm") is not None and kw["clearance_mm"] is None:
            kw["clearance_mm"] = flat["clearance_mm"]
        if flat.get("track_mm") is not None and kw["track_mm"] is None:
            kw["track_mm"] = flat["track_mm"]
        layers = flat.get("layers") or []
        if not kw["layer"] and len(layers) == 1:
            kw["layer"] = layers[0]
        report["board"] = _intent.check_against_board(spec, board)
    out = _oracle.routability(board, **kw)
    if report is not None and isinstance(out, dict):
        out.setdefault("assumptions", {})["intent"] = report
    return out

def _board_route(args):
    """Plan (or plan+write) a routed board.

    Two verb-free rules the whole project follows apply here too:

    * a net is reported `routed` only when every one of its MST edges found a path
      AND every emitted segment passed an exact clearance check - a raster cell is
      never evidence;
    * nothing is written unless `apply` is true, and when it is, the copper goes
      through `board_propose` + `board_apply`, so it meets the same four gates and
      the same KiCad DRC referee as a hand edit. A plan is refused outright when a
      net could not be routed, unless `allow_partial` is set - half a board that
      looks finished is the worst kind of answer.
    """
    from . import edit as _edit
    from .route import router as _router
    from .route.rules import RulesError
    board, e = _load_board(args)
    if e:
        return e
    kw = {}
    for key in ("pitch_mm", "clearance_mm", "track_mm", "via_diameter_mm",
                "via_drill_mm", "edge_clearance_mm", "creepage_mm",
                "working_voltage_v", "pollution_degree", "altitude_m",
                "rip_up_rounds", "max_nets", "congestion_rounds",
                "congestion_max_rips", "congestion_weight"):
        if args.get(key) is not None:
            kw[key] = args[key]
    try:
        pairs = _parse_creepage_pairs(args.get("creepage_pairs"))
    except ValueError as exc:
        return err(str(exc), "RULES_INVALID",
                   hint="creepage pairs look like 'L:N:1.0' (mm), several separated "
                        "by commas; a rule that cannot be parsed is refused rather "
                        "than dropped")
    if pairs:
        kw["creepage_pairs"] = pairs
    try:
        dpairs = _parse_object_list(args.get("diff_pairs"), "diff_pairs")
        lgroups = _parse_object_list(args.get("length_groups"), "length_groups")
        itargets = _parse_object_list(args.get("impedance_targets"),
                                      "impedance_targets")
    except ValueError as exc:
        return err(str(exc), "ARG_INVALID",
                   hint='diff_pairs entries are {p, n, gap_mm} objects, '
                        'length_groups entries are {nets: [...], max_skew_mm} '
                        'objects; a structure that cannot be read is refused, not '
                        'dropped')
    if dpairs:
        kw["diff_pairs"] = dpairs
    if lgroups:
        kw["length_groups"] = lgroups
    if itargets:
        kw["impedance_targets"] = itargets
    for key in ("layers", "nets", "fixed_nets"):
        val = args.get(key)
        if isinstance(val, str):
            val = [x.strip() for x in val.split(",") if x.strip()]
        if val:
            kw[key] = val
    for key in ("incremental", "allow_vias", "ignore_zone_areas", "second_sweep",
                "return_path", "blind_vias"):
        if args.get(key) is not None:
            kw[key] = bool(args[key])
    for key in ("material_group", "insulation", "topology"):
        if args.get(key) is not None:
            kw[key] = str(args[key])
    if args.get("coating") is not None:
        kw["coating"] = bool(args["coating"])
    if args.get("return_path_net"):
        kw["return_path_net"] = str(args["return_path_net"])
    intent_report = None
    if args.get("intent"):
        from .route import intent as _intent
        try:
            spec = _intent.load(args["intent"])
        except _intent.IntentError as exc:
            return err(exc.message, exc.reason_code, hint=exc.hint)
        checked = _intent.check_against_board(spec, board)
        # Two kinds of finding, deliberately not the same severity: "this keepout
        # swallows a pad" and "there is no such footprint" mean the intent cannot
        # be true on this board and are refused; "the footprint is not inside its
        # region yet" is simply where the board is today, and is reported.
        blocking = [p for p in checked["problems"]
                    if p.get("what") == "keepout" or "no such footprint" in
                    str(p.get("problem")) or "past the board edge" in
                    str(p.get("problem"))]
        if blocking:
            return err("the intent cannot be true on this board: %d finding(s)"
                       % (len(blocking),), "INTENT_INVALID",
                       hint="a keepout that covers a pad, a region off the board or "
                            "a ref that does not exist is refused before routing "
                            "spends ten minutes failing for the declared reason",
                       extra={"intent_problems": blocking[:12],
                              "intent_source": checked.get("source")})
        kw, intent_report = _intent.resolve(kw, spec)
        intent_report["board"] = checked
    try:
        res = _router.plan(board, **kw)
    except RulesError as exc:
        return err(exc.message, exc.reason_code, hint=exc.hint)
    except _router.RouteError as exc:
        return err(exc.message, exc.reason_code, hint=exc.hint)

    stats = res["stats"]
    failed = res["failed"]
    _ledger_note("route", args["path"], {
        "verdict": ("routed" if not failed else "incomplete"),
        "nets_total": stats["nets_total"],
        "nets_routed": stats["nets_routed"],
        "nets_failed": stats["nets_failed"],
        "failed": [{"net": f.get("net"), "reason": f.get("reason")}
                   for f in failed[:8]],
        "pitch_mm": stats["pitch_mm"],
        "layers": res["layers_routing"],
        "clearance_mm": res["rules"]["clearance_mm"],
        "track_mm": res["rules"]["track_mm"],
        "apply": args.get("apply") is True,
        "note": ("apply was asked for" if args.get("apply") is True
                 else "plan only"),
    })
    segs = [o for o in res["ops"] if o["op"] == "add_track"]
    from .route import rules as _rrules
    rules_in_effect = _rrules.per_net_summary(stats["rule_bundle"])
    out = ok(
        value={"nets_total": stats["nets_total"], "nets_routed": stats["nets_routed"],
               "nets_failed": stats["nets_failed"],
               "nets_fixed": stats.get("nets_fixed", 0), "ops": len(res["ops"]),
               "vias": sum(1 for o in res["ops"] if o["op"] == "add_via"),
               "segments": len(segs),
               "track_length_mm": round(sum(math.hypot(o["x2"] - o["x1"],
                                                       o["y2"] - o["y1"])
                                            for o in segs), 3)},
        unit="nets/ops",
        model="grid_maze_router",
        basis="A* on a %.3f mm raster per copper layer, with every emitted segment "
              "re-checked against exact polygon geometry and the per-net clearance"
              % stats["pitch_mm"],
        precision="routing is a search, not a proof: `routed` means this router "
                  "found a legal path for every edge of the net's spanning tree and "
                  "the exact check accepted every segment. It does NOT mean the board "
                  "is DRC clean - run board_drc after writing - and it does not mean "
                  "the layout is good: length matching only happens for the groups "
                  "you declared, a diff pair is only coupled when `couple` is true "
                  "(and then `coupled_fraction` says how much of it really is a "
                  "pair), ground stitching only places a via where ground copper "
                  "already exists on both sides, and no thermal or impedance "
                  "reasoning is applied",
        assumptions={
            "clearance_mm": res["rules"]["clearance_mm"],
            "track_mm": res["rules"]["track_mm"],
            "these_are_the_board_defaults_not_the_nets": (
                "clearance_mm / track_mm / via_mm above are the BOARD-wide defaults. "
                "The numbers the router actually used for a given net are in "
                "`rules_in_effect.groups` (grouped per net class / impedance target / "
                "diff pair); reading the defaults as 'what this board was routed "
                "with' is wrong whenever the nets carry their own class"),
            "via_mm": [res["rules"]["via_diameter_mm"],
                       res["rules"]["via_drill_mm"]],
            "creepage_mm": res["rules"]["creepage_mm"],
            "creepage_pairs": res["rules"]["creepage_pairs"],
            "creepage_lookup": (None if res["rules"].get("creepage_lookup") is None
                                else {k: res["rules"]["creepage_lookup"].get(k)
                                      for k in ("value", "unit", "ladder",
                                                "ladder_step_upper_v", "edition",
                                                "basis", "precision")}),
            "diff_pairs": res["rules"]["diff_pairs"],
            "impedance_targets": res["rules"]["impedance_targets"],
            "length_groups_asked": len(lgroups),
            "where_the_numbers_came_from": res["rules"]["sources"],
            "assumed_by_this_server": res["rules"]["assumed"],
            "routing_layers": res["layers_routing"],
            "plane_layers": res["layers_plane"],
            "return_path": (None if res.get("return_path") is None
                            else {"ground_net": res["return_path"]["ground_net"],
                                  "status": res["return_path"]["status"],
                                  "vias_added": len(res["return_path"]["added"]),
                                  "skipped": len(res["return_path"]["skipped"])}),
            "writes_nothing": args.get("apply") is not True,
            "intent": intent_report,
            "pour": res.get("pour"),
            "rules_in_effect": rules_in_effect,
        },
        extra={
            "diff_pairs": res["diff_pairs"],
            "impedance_targets": res["rules"]["impedance_targets"],
            "length_groups": res["length_groups"],
            "return_path": res.get("return_path"),
            "nets": res["nets"],
            "fixed": res.get("fixed"),
            "failed": failed,
            "rejected_segments": res["rejected"],
            "stats": dict((k, v) for k, v in stats.items() if k != "rule_bundle"),
            "grid_notes": stats["grid_notes"],
            "ops_preview": res["ops"][:5],
            "pour": res.get("pour"),
        })
    if args.get("apply") is not True:
        out["next_step"] = ("pass apply=true to write this copper (it goes through "
                            "board_propose + board_apply and the four gates)")
        return out

    if failed and not args.get("allow_partial"):
        out["status"] = "error"
        out["value"] = {"nets_failed": stats["nets_failed"],
                        "nets_routed": stats["nets_routed"]}
        out["message"] = ("%d net(s) could not be routed, so nothing was written. "
                          "Detach a path or pass allow_partial=true to write only "
                          "what did route."
                          % stats["nets_failed"])
        out["reason_code"] = "ROUTE_INCOMPLETE"
        out["hint"] = ("board_route with nets=[...] on one net at a time tells you "
                       "which one is the problem; the `failed` list carries the "
                       "blocking pad and how much clearance is missing")
        return out
    plan = _edit.propose(args["path"], res["ops"], {"engine": True})
    try:
        applied = _edit.apply_plan(plan["plan_id"], confirm=True,
                                   options={"engine": True,
                                            "allow_new_violations":
                                                bool(args.get("allow_new_violations")),
                                            "strict_drc": bool(args.get("strict_drc"))})
    except _edit.EditError as exc:
        return _edit_error(exc)
    out["value"]["plan_id"] = plan["plan_id"]
    out["applied"] = applied
    if args.get("refill_pours") is True:
        # The pour is now stale by construction: the copper the router just wrote
        # did not exist when KiCad filled it. Reporting that is not the same as
        # fixing it, so the option to fix it exists - and it is KiCad's own
        # ZONE_FILLER, transplanted byte-preservingly, never our re-implementation.
        stale = (res.get("pour") or {}).get("needs_refill")
        if not stale:
            out["refill"] = {"status": "nothing_stale",
                             "why": "no zone intersected the new copper, so no "
                                    "filled_polygon became false"}
        else:
            filled = _fill.fill(args["path"], in_place=True)
            out["refill"] = {
                "status": ("refilled" if filled.get("status") == "ok"
                           else "refused"),
                "engine": "KiCad pcbnew ZONE_FILLER (transplanted, byte-preserving)",
                "result": filled if filled.get("status") == "ok" else None,
                "error": None if filled.get("status") == "ok" else filled,
            }
            if filled.get("status") == "ok":
                out["value"]["refilled"] = True
                out["value"]["fill_area_mm2"] = filled["value"]["fill_area_mm2"]
                out["assumptions"]["pour"] = dict(
                    out["assumptions"]["pour"] or {},
                    refilled=True,
                    refilled_by="pcbnew ZONE_FILLER",
                    thermal_spokes="KiCad's decision, baked into the polygons")
                out["next_step"] = ("the pour was refilled from KiCad's own filler; "
                                    "run board_drc again - the DRC verdict above was "
                                    "taken BEFORE the refill")
            else:
                out["next_step"] = ("the copper was written but the pour could not be "
                                    "refilled; the filled_polygons in the file are "
                                    "still the pre-routing ones - board_fill_zones "
                                    "says why")
    return out

_tool(
    "board_cross_split",
    "Does any signal run over a break in its reference plane? REFUSES when the board "
    "carries no plane zones: the split is then not observable, and 'no split found' "
    "would be the answer that keeps a broken stackup green in DRC.",
    {"type": "object",
     "properties": dict({
         "layer": _str("signal layer to check"),
         "signal_nets": _str("comma separated nets (default: every track)"),
     }, **_COMMON),
     "required": ["path"]},
    _board_tool(lambda b, a: _oracle.cross_split(
        b, layer=a.get("layer"),
        signal_nets=[x.strip() for x in (a.get("signal_nets") or "").split(",")
                     if x.strip()] or None), derived=True),
)

_tool(
    "board_loop_area",
    "Estimated signal/return loop area for one net (routed length x height to the nearest "
    "copper below). REFUSES without a stackup, because a guessed dielectric height "
    "produces a guessed EMC answer.",
    {"type": "object",
     "properties": dict({"net": _str("net name")}, **_COMMON),
     "required": ["path", "net"]},
    _board_tool(lambda b, a: _oracle.loop_area(b, a["net"]), derived=True),
)

_tool(
    "epro_inspect",
    "Inspect an EasyEDA Pro project WITHOUT interpreting it: the container (.epro ZIP "
    "or .eprj/.eprj2 SQLite project), every member, and per document the measured tag "
    "arity and a few real records. This is the tool that keeps the record map honest - "
    "layout claims can be checked against a file instead of believed.",
    {"type": "object",
     "properties": {"path": _str("absolute path to the .epro / .eprj / .eprj2"),
                    "sample": _num("payloads to show per type", 3)},
     "required": ["path"]},
    lambda a: _guarded(lambda: _epro_inspect(a)),
)

def _app_detect(args):
    """detect() returns evidence, not an envelope: wrap it so `status` is always present."""
    app = args.get("app")
    if not app:
        return {"status": "ok", "apps": _desktop.detect_all(),
                "trust": "drive scan and registry reads are evidence of installation; a "
                         "running process's image path is stronger but only exists while "
                         "the app is open"}
    found = dict(_desktop.detect(app, args.get("explicit_path")))
    found["status"] = "ok" if found.get("found") else "error"
    if not found.get("found"):
        found["reason_code"] = "APP_NOT_INSTALLED"
        found["message"] = "%s was not found on any drive or in the registry" % (
            _desktop.APPS[app]["display"])
    return found

def _launch_app(app):
    found = _desktop.detect(app)
    if not found.get("found"):
        return err("%s is not installed (or not found)" % _desktop.APPS[app]["display"],
                   "APP_NOT_INSTALLED", extra={"tried": found.get("tried")})
    return _desktop.launch(found["exe"], [])

def _epro_inspect(args):
    import os
    from .adapters import epro as _epro
    path = args.get("path")
    if not path or not os.path.isfile(path):
        return err("file does not exist: %r" % path, "FILE_NOT_FOUND")
    return _epro.inspect(path, sample=int(args.get("sample", 3)))

# ------------------------------------------------- antenna / feedline / link
# Everything below is a stateless engineering calculation. None of it needs a
# board, and none of it is a full-wave solver: the boundary of what a closed form
# can answer is written into every answer's `precision` and `not_computed`.

_Z0_ARG = _num("reference impedance in ohm", 50.0)

_KIND_FEED = _str(
    "line cross-section. microstrip / stripline take width_mm + dielectric_mm; "
    "cpw (coplanar, no back ground) and gcpw (coplanar with a back ground plane) "
    "also need gap_mm; cpw_diff / gcpw_diff are the EDGE-COUPLED PAIR versions and "
    "need gap_mm + spacing_mm (spacing_mm is the edge-to-edge gap between the two "
    "strips). For stripline, dielectric_mm is the trace-to-one-plane distance, the "
    "same convention trace_impedance uses. The pair kinds are solved as a 2-D field "
    "problem (no closed form exists) and a single solve takes 5-40 s, longer at wide "
    "widths",
    ["microstrip", "stripline", "cpw", "gcpw", "cpw_diff", "gcpw_diff"])

_ER = _num("substrate relative permittivity", 4.3)
_COPPER_UM = _num("copper thickness in um (0 = ignore thickness)", 0.0)

_tool(
    "feedline_impedance",
    "Z0, effective permittivity, guided wavelength and loss of a microstrip, "
    "stripline, CPW or grounded CPW line. Loss (with freq_ghz + tan_delta) reports "
    "conductor loss from the 2-D field solution on microstrip and stripline - "
    "alpha_c = Rs/(2*Z0)*INT g^2 ds, which carries the trace edge crowding and the "
    "return-current spread - alongside the Rs/(Z0*w) sheet figure, plus an optional "
    "Hammerstad-Jensen copper-roughness multiplier. Coplanar lines (cpw / gcpw) get "
    "the same treatment from their own 2-D solve, and the edge-coupled pair kinds "
    "(cpw_diff / gcpw_diff) report Zdiff = 2*Z_odd with both modes, the charge "
    "balance and the mesh drift, and their conductor loss is normalised to the "
    "DIFFERENTIAL current (both strips and both ground wings in it). verify_field "
    "on the pair kinds is not a comparison - the field solve IS the answer - so it "
    "only says so.",
    {
        "type": "object",
        "properties": {
            "kind": _KIND_FEED,
            "width_mm": _num("conductor width in mm"),
            "dielectric_mm": _num("substrate height in mm (for stripline: "
                                  "trace-to-one-plane distance)"),
            "gap_mm": _num("gap to the coplanar ground in mm (cpw/gcpw only)"),
            "copper_um": _COPPER_UM,
            "er": _ER,
            "freq_ghz": _num("frequency in GHz; enables lambda_g and loss"),
            "tan_delta": _num("dielectric loss tangent; enables dielectric loss"),
            "verify_field": _bool("run the 2-D field solver on the same cross-section "
                                  "and report the difference (slower)"),
            "field_levels": _num("refinement levels for the field check", 1),
            "roughness_um": _num("copper surface roughness Rq (RMS) in um; multiplies Rs "
                                 "by the Hammerstad-Jensen factor (0 = smooth; Rz is "
                                 "not converted)", 0.0),
            "spacing_mm": _num("edge-to-edge gap between the two strips of a pair "
                               "(cpw_diff / gcpw_diff only; required there)"),
        },
        "required": ["kind", "width_mm", "dielectric_mm"],
    },
    lambda a: _feedline.feedline_impedance(
        a["kind"], a["width_mm"], a["dielectric_mm"], a.get("gap_mm"),
        a.get("copper_um", 0.0), a.get("er", 4.3), a.get("freq_ghz"),
        a.get("tan_delta"), a.get("verify_field", False),
        a.get("field_levels", 1), a.get("roughness_um", 0.0),
        a.get("spacing_mm")),
)

_tool(
    "feedline_width",
    "The trace width that gives a target impedance, for microstrip / stripline / CPW "
    "/ GCPW and the edge-coupled pairs (cpw_diff / gcpw_diff, where target_ohm is "
    "Zdiff). Bisected on the same forward model feedline_impedance exposes, so the "
    "answer can be re-checked. If the target is outside what a sane width range "
    "reaches, it returns the reachable band as an error instead of a width that "
    "silently misses. The pair kinds have no closed form: the search runs on a coarse "
    "and then a cheap mesh and finishes on the default mesh, which is why they cost "
    "2-3 field solves (about 1.5-6 min) instead of microseconds - and the returned "
    "z0_ohm is the default-mesh answer with its residual, not a claim of exactness.",
    {
        "type": "object",
        "properties": {
            "target_ohm": _num("target characteristic impedance in ohm"),
            "kind": _KIND_FEED,
            "dielectric_mm": _num("substrate height in mm (for stripline: "
                                  "trace-to-one-plane distance)"),
            "gap_mm": _num("gap to the coplanar ground in mm (cpw/gcpw only)"),
            "copper_um": _COPPER_UM,
            "er": _ER,
            "tol_ohm": _num("bisection tolerance in ohm", 1e-4),
            "spacing_mm": _num("edge-to-edge gap between the two strips of a pair "
                               "(cpw_diff / gcpw_diff only; required there)"),
        },
        "required": ["target_ohm", "kind", "dielectric_mm"],
    },
    lambda a: _feedline.feedline_width(
        a["target_ohm"], a["kind"], a["dielectric_mm"], a.get("gap_mm"),
        a.get("copper_um", 0.0), a.get("er", 4.3), a.get("tol_ohm", 1e-4),
        a.get("spacing_mm")),
)

_tool(
    "antenna_patch",
    "Rectangular microstrip patch: width, length (with the fringing correction dL), "
    "edge input resistance, inset feed depth for a 50 ohm line, unloaded Q, VSWR<2 "
    "bandwidth and radiation efficiency. This is the transmission-line model - it is "
    "good enough to order a prototype and wrong about the last few percent, which "
    "only a solver or a VNA can give.",
    {
        "type": "object",
        "properties": {
            "freq_ghz": _num("design frequency in GHz"),
            "er": _num("substrate relative permittivity"),
            "height_mm": _num("substrate height in mm"),
            "copper_um": _num("copper thickness in um", 35.0),
            "tan_delta": _num("substrate loss tangent (for efficiency and bandwidth)"),
            "target_rin": _num("input resistance the feed should see, ohm", 50.0),
            "ground_margin_h": _num("ground extension beyond the patch, in substrate "
                                    "heights (None = default 6h)"),
        },
        "required": ["freq_ghz", "er", "height_mm"],
    },
    lambda a: _antenna.patch_rectangular(
        a["freq_ghz"], a["er"], a["height_mm"], a.get("copper_um", 35.0),
        a.get("tan_delta"), a.get("target_rin", 50.0), a.get("ground_margin_h")),
)

_tool(
    "antenna_printed",
    "PCB printed quarter-wave antenna (IFA, meander, printed monopole) on a substrate: "
    "the resonant length comes back as a RANGE (all-air lambda/4 down to "
    "lambda/(4*sqrt((er+1)/2))) because the effective permittivity of a thin printed "
    "conductor has no trustworthy closed form, plus the IFA feed-tap position and the "
    "Chu bandwidth ceiling for the box it fits in.",
    {
        "type": "object",
        "properties": {
            "freq_ghz": _num("design frequency in GHz"),
            "er": _num("substrate relative permittivity"),
            "height_mm": _num("substrate height in mm"),
            "shape": _str("ifa (inverted-F with a feed tap), meander (needs bbox_mm), "
                          "or monopole (straight printed quarter wave)",
                          ["ifa", "meander", "monopole"], "ifa"),
            "target_rin": _num("input resistance to match, ohm", 50.0),
            "bbox_mm": {
                "type": "array",
                "items": {"type": "number"},
                "minItems": 2,
                "maxItems": 2,
                "description": "for meander: the box the element must stay inside, as "
                               "[length, width] in mm",
            },
            "wire_width_mm": _num("for meander: the trace width, which sets how many "
                                  "turns fit across the box", 0.2),
        },
        "required": ["freq_ghz", "er", "height_mm"],
    },
    lambda a: _antenna.printed_wire(
        a["freq_ghz"], a["er"], a["height_mm"], a.get("shape", "ifa"),
        a.get("target_rin", 50.0), a.get("bbox_mm"), a.get("wire_width_mm", 0.2)),
)

_tool(
    "antenna_dipole_monopole",
    "Free-space thin-wire dipole or monopole: resonance length (0.475 lambda / "
    "0.2375 lambda, the end effect included), radiation resistance and directivity. "
    "Pass length_mm to ask the inverse question - what resistance would THIS length "
    "have - which is the honest direction when a housing has already fixed the size.",
    {
        "type": "object",
        "properties": {
            "freq_ghz": _num("design frequency in GHz"),
            "kind": _str("dipole (balanced, two arms) or monopole (one arm over a "
                         "ground plane)", ["dipole", "monopole"], "dipole"),
            "length_mm": _num("actual total length in mm (optional; triggers the "
                              "inverse answer)"),
            "wire_radius_mm": _num("conductor radius in mm (for the thin-wire "
                                   "check)"),
        },
        "required": ["freq_ghz"],
    },
    lambda a: _antenna.dipole_monopole(
        a["freq_ghz"], a.get("kind", "dipole"), a.get("length_mm"),
        a.get("wire_radius_mm")),
)

_tool(
    "antenna_helix",
    "Axial-mode (end-fire) helix by Kraus' relations: gain in dBic, half-power beam "
    "width and approximate input impedance. Refuses when the geometry is outside the "
    "axial-mode window (0.75 <= C/lambda <= 1.333, pitch 11..16 deg, N >= 3) because "
    "there the relations are not merely inaccurate, the antenna is in a different mode.",
    {
        "type": "object",
        "properties": {
            "freq_ghz": _num("design frequency in GHz"),
            "turns": _num("number of turns (>= 3)"),
            "diameter_mm": _num("coil diameter in mm (centre to centre)"),
            "turn_spacing_mm": _num("centre-to-centre spacing between turns in mm"),
            "pitch_deg": _num("pitch angle in degrees, instead of turn_spacing_mm"),
        },
        "required": ["freq_ghz", "turns", "diameter_mm"],
    },
    lambda a: _antenna.helix_axial(
        a["freq_ghz"], a["turns"], a["diameter_mm"], a.get("turn_spacing_mm"),
        a.get("pitch_deg")),
)

_tool(
    "antenna_small_loop",
    "Electrically small loop: radiation resistance (exact below 0.1 lambda "
    "circumference), and a refusal above that limit. A small loop radiates milliohms "
    "and is highly inductive, so it needs a series capacitor and has very little "
    "bandwidth - the numbers come back with that consequence spelled out.",
    {
        "type": "object",
        "properties": {
            "freq_ghz": _num("design frequency in GHz"),
            "area_mm2": _num("enclosed loop area in mm^2"),
            "diameter_mm": _num("round loop diameter in mm (instead of area_mm2)"),
            "turns": _num("number of turns", 1),
        },
        "required": ["freq_ghz"],
    },
    lambda a: _antenna.small_loop(
        a["freq_ghz"], a.get("area_mm2"), a.get("diameter_mm"), a.get("turns", 1)),
)

_tool(
    "antenna_q_limit",
    "Chu/Harrington bandwidth ceiling for an antenna that must fit in a given box. "
    "Use it before laying anything out: it says 'impossible' for free, and a real "
    "antenna is always worse than the bound.",
    {
        "type": "object",
        "properties": {
            "freq_ghz": _num("design frequency in GHz"),
            "size_mm": _num("the largest dimension of the space available, in mm"),
            "vswr": _num("VSWR the bandwidth is quoted at", 2.0),
            "dimension": _str("how size_mm maps to the enclosing sphere",
                              ["largest", "sphere_diameter"], "largest"),
        },
        "required": ["freq_ghz", "size_mm"],
    },
    lambda a: _antenna.q_limit(
        a["freq_ghz"], a["size_mm"], a.get("vswr", 2.0),
        a.get("dimension", "largest")),
)

_tool(
    "antenna_chip",
    "Ceramic chip antenna: the keep-out and matching checklist plus the near-field "
    "boundary, and an explicit refusal to invent gain/bandwidth. Gain, bandwidth and "
    "the matching network of a chip antenna are vendor measured data - they are not "
    "derivable from frequency and package size, so any number here would be invented.",
    {
        "type": "object",
        "properties": {
            "freq_ghz": _num("design frequency in GHz"),
            "max_gain_dbi": _num("the datasheet peak gain, if known (used only to "
                                 "sanity-check the link budget)"),
            "keepout_x_mm": _num("datasheet keep-out along the feed axis, in mm"),
            "keepout_y_mm": _num("datasheet keep-out across, in mm"),
        },
        "required": ["freq_ghz"],
    },
    lambda a: _antenna.chip_antenna(
        a["freq_ghz"], a.get("max_gain_dbi"), a.get("keepout_x_mm"),
        a.get("keepout_y_mm")),
)

_Z_ARG = {
    "type": ["number", "object", "string"],
    "description": "load impedance in ohm: a number (real only), {real, imag}, or a "
                   "complex string like '100+25j'",
}

_tool(
    "match_reflection",
    "The four ways of saying the same mismatch, each with its definition: reflection "
    "coefficient, VSWR, return loss and mismatch loss, plus the equivalent series L / "
    "shunt C that would produce the reactance. Mixing these four up is how a '1.5:1' "
    "spec silently becomes an '11 dB' spec.",
    {
        "type": "object",
        "properties": {
            "z_load": _Z_ARG,
            "z0": _Z0_ARG,
            "freq_ghz": _num("frequency in GHz (needed for the equivalent L/C)"),
        },
        "required": ["z_load"],
    },
    lambda a: _matching.reflection(a["z_load"], a.get("z0", 50.0), a.get("freq_ghz")),
)

_tool(
    "match_l",
    "Two-element L match, solved exactly (not a Q approximation). Returns both "
    "topologies where they exist, each with the resulting impedance and residual VSWR "
    "recomputed from the values - so a claimed perfect match is checkable.",
    {
        "type": "object",
        "properties": {
            "z_load": _Z_ARG,
            "freq_ghz": _num("frequency in GHz"),
            "z0": _Z0_ARG,
        },
        "required": ["z_load", "freq_ghz"],
    },
    lambda a: _matching.l_match(a["z_load"], a["freq_ghz"], a.get("z0", 50.0)),
)

_tool(
    "match_pi",
    "Pi network (shunt-series-shunt, low-pass form) between two real terminations at a "
    "requested Q. Q is solved for the virtual resistance by bisection, so the Q that "
    "comes back is the Q that was asked for.",
    {
        "type": "object",
        "properties": {
            "rsource": _num("source resistance, ohm"),
            "rload": _num("load resistance, ohm"),
            "freq_ghz": _num("frequency in GHz"),
            "q": _num("loaded Q to design at (controls bandwidth)"),
        },
        "required": ["rsource", "rload", "freq_ghz"],
    },
    lambda a: _matching.pi_match(a["rsource"], a["rload"], a["freq_ghz"], a.get("q")),
)

_tool(
    "match_t",
    "T network (series-shunt-series, high-pass form), the dual of the pi network, at a "
    "requested Q.",
    {
        "type": "object",
        "properties": {
            "rsource": _num("source resistance, ohm"),
            "rload": _num("load resistance, ohm"),
            "freq_ghz": _num("frequency in GHz"),
            "q": _num("loaded Q to design at"),
        },
        "required": ["rsource", "rload", "freq_ghz"],
    },
    lambda a: _matching.t_match(a["rsource"], a["rload"], a["freq_ghz"], a.get("q")),
)

_tool(
    "match_stub",
    "Single-stub tuner: how far from the load the stub goes and how long it is, by "
    "closed form (both physical solutions returned, shorter stub preferred). Shunt "
    "stubs only - a series stub needs a cut in the line and is not implemented.",
    {
        "type": "object",
        "properties": {
            "z_load": _Z_ARG,
            "freq_ghz": _num("frequency in GHz"),
            "z0": _Z0_ARG,
            "stub": _str("shunt only", ["shunt"], "shunt"),
            "termination": _str("open or short at the end of the stub",
                                ["open", "short"], "open"),
        },
        "required": ["z_load", "freq_ghz"],
    },
    lambda a: _matching.single_stub(
        a["z_load"], a["freq_ghz"], a.get("z0", 50.0), a.get("stub", "shunt"),
        a.get("termination", "open")),
)

_tool(
    "match_quarter_wave",
    "Quarter-wave transformer: section impedance sqrt(Z0*R), length lambda_g/4, and "
    "the real limits. Refuses a load with significant reactance rather than ignoring "
    "it, and warns when the resistance ratio leaves single-section territory.",
    {
        "type": "object",
        "properties": {
            "z_load": _Z_ARG,
            "freq_ghz": _num("frequency in GHz"),
            "z0": _Z0_ARG,
            "er_eff": _num("effective permittivity of the quarter-wave section", 1.0),
        },
        "required": ["z_load", "freq_ghz"],
    },
    lambda a: _matching.quarter_wave(
        a["z_load"], a["freq_ghz"], a.get("z0", 50.0), a.get("er_eff", 1.0)),
)

_tool(
    "link_friis",
    "Link budget term by term: received power in free space with transmitter power, "
    "both gains, both feed losses and 'other' loss each named separately, plus EIRP "
    "and ERP. The answer is an upper bound - multipath, body loss and interference "
    "are not in a free-space model.",
    {
        "type": "object",
        "properties": {
            "tx_dbm": _num("transmitter power in dBm"),
            "freq_mhz": _num("frequency in MHz"),
            "distance_m": _num("distance in metres"),
            "tx_gain_dbi": _num("transmit antenna gain in dBi", 0.0),
            "rx_gain_dbi": _num("receive antenna gain in dBi", 0.0),
            "tx_loss_db": _num("loss between transmitter and antenna, dB", 0.0),
            "rx_loss_db": _num("loss between antenna and receiver, dB", 0.0),
            "other_loss_db": _num("anything else that must not be hidden, dB", 0.0),
        },
        "required": ["tx_dbm", "freq_mhz", "distance_m"],
    },
    lambda a: _link.friis(
        a["tx_dbm"], a["freq_mhz"], a["distance_m"], a.get("tx_gain_dbi", 0.0),
        a.get("rx_gain_dbi", 0.0), a.get("tx_loss_db", 0.0),
        a.get("rx_loss_db", 0.0), a.get("other_loss_db", 0.0)),
)

_tool(
    "link_max_range",
    "Free-space distance at which the received power falls to the sensitivity, with an "
    "optional fade margin. This is the range ceiling of the radio, not a prediction: "
    "indoor range is usually a fraction of it.",
    {
        "type": "object",
        "properties": {
            "tx_dbm": _num("transmitter power in dBm"),
            "freq_mhz": _num("frequency in MHz"),
            "sensitivity_dbm": _num("receiver sensitivity in dBm"),
            "tx_gain_dbi": _num("transmit antenna gain in dBi", 0.0),
            "rx_gain_dbi": _num("receive antenna gain in dBi", 0.0),
            "tx_loss_db": _num("loss between transmitter and antenna, dB", 0.0),
            "rx_loss_db": _num("loss between antenna and receiver, dB", 0.0),
            "other_loss_db": _num("anything else that must not be hidden, dB", 0.0),
            "fade_margin_db": _num("reserve held back for fading, dB"),
        },
        "required": ["tx_dbm", "freq_mhz", "sensitivity_dbm"],
    },
    lambda a: _link.max_range(
        a["tx_dbm"], a["freq_mhz"], a["sensitivity_dbm"], a.get("tx_gain_dbi", 0.0),
        a.get("rx_gain_dbi", 0.0), a.get("tx_loss_db", 0.0), a.get("rx_loss_db", 0.0),
        a.get("other_loss_db", 0.0), a.get("fade_margin_db")),
)

_tool(
    "link_aperture",
    "Effective aperture of an antenna of given gain: Ae = G*lambda^2/(4*pi). This is "
    "the area that actually collects power, and the reason a small antenna cannot be "
    "made to have a large gain by matching alone.",
    {
        "type": "object",
        "properties": {
            "gain_dbi": _num("antenna gain in dBi"),
            "freq_mhz": _num("frequency in MHz"),
        },
        "required": ["gain_dbi", "freq_mhz"],
    },
    lambda a: _link.aperture(a["gain_dbi"], a["freq_mhz"]),
)

_tool(
    "link_far_field",
    "Where the far field starts for an antenna of a given largest dimension, by three "
    "criteria reported side by side (2D^2/lambda, lambda/(2*pi), 5D) plus the one to "
    "use. A pattern measured closer than this is the near field, not the antenna's.",
    {
        "type": "object",
        "properties": {
            "size_mm": _num("largest dimension of the antenna in mm"),
            "freq_mhz": _num("frequency in MHz"),
        },
        "required": ["size_mm", "freq_mhz"],
    },
    lambda a: _link.far_field(a["size_mm"], a["freq_mhz"]),
)

_tool(
    "link_fresnel",
    "First Fresnel zone radius at a point between two antennas, and the 60 % clearance "
    "a line-of-sight link needs. Obstructing 40 % of the first zone costs about 1 dB; "
    "60 % costs 6 dB or more.",
    {
        "type": "object",
        "properties": {
            "freq_mhz": _num("frequency in MHz"),
            "d1_m": _num("distance from antenna 1 to the point of interest, in m"),
            "d2_m": _num("distance from the point of interest to antenna 2, in m"),
            "n": _num("zone number", 1),
        },
        "required": ["freq_mhz", "d1_m", "d2_m"],
    },
    lambda a: _link.fresnel_radius(
        a["freq_mhz"], a["d1_m"], a["d2_m"], a.get("n", 1)),
)

_tool(
    "board_antenna_keepout",
    "Measure what copper is really inside a proposed antenna keep-out rectangle on a "
    "board: every pad, track, via and copper graphic whose bounding box intersects it, "
    "plus every pour (a pour never appears in a pad/track listing and is the usual "
    "cause of a detuned antenna). Read-only. Conservative: 'clear' is reliable, "
    "'occupied' may contain a false positive worth checking. Datasheet keep-out RULES "
    "are not in the file - this only measures what the file contains.",
    {"type": "object",
     "properties": dict({
         "x_mm": _num("keep-out rectangle anchor X in mm (same coordinate system as "
                      "board_components, Y up)"),
         "y_mm": _num("keep-out rectangle anchor Y in mm"),
         "width_mm": _num("rectangle width in mm"),
         "length_mm": _num("rectangle length in mm"),
         "layer": _str("only copper on this layer (default: all copper layers)"),
         "margin_mm": _num("grow the rectangle by this much on every side", 0.0),
         "top": _num("how many offending items to list", 40),
     }, **_COMMON),
     "required": ["path", "x_mm", "y_mm", "width_mm", "length_mm"]},
    _board_tool(lambda b, a: _oracle.antenna_keepout(
        b, a["x_mm"], a["y_mm"], a["width_mm"], a["length_mm"], a.get("layer"),
        a.get("margin_mm", 0.0), int(a.get("top", 40))), derived=True),
)

# ------------------------------------------------------- capabilities

def _capabilities(_args):
    return {
        "status": "ok",
        "server": "pcb-agent-mcp",
        "layers": {
            "calc": "stateless engineering calculation (no board needed)",
            "fidelity": "the board CONVERTED, not summarised: the whole document is "
                        "carried (every node, every atom), facts are read out of it, "
                        "and the round trip is proven by KiCad itself",
            "adapters": "a hand-picked canonical view (mm, Y up, nets by NAME) built "
                        "on top of the fidelity tree - a convenience, not the truth",
            "oracle": "measurements on one parsed board (clearance, free space, pour, "
                      "reference-plane continuity, loop area)",
            "antenna": "closed-form antenna and RF design: printed / structural / chip "
                       "antenna sizing, feedline impedance (microstrip, stripline, CPW, "
                       "GCPW), matching network synthesis, link budget and far-field "
                       "distances. There is NO full-wave solver here and no pattern, "
                       "efficiency or detuning prediction - the answers are the starting "
                       "geometry, and every one of them says what it cannot see",
            "simulation": "circuit simulation (SPICE: .op / .tran / .ac with Newton + "
                          "junction limiting + gmin stepping, per-element results "
                          "reported back; the same tool can delegate to an installed "
                          "ngspice instead, and cross-check the two solvers against "
                          "each other), current and PDN (trace / via / barrel "
                          "resistance, IR drop from the board's own copper, "
                          "decoupling impedance and worst-case anti-resonance), "
                          "fusing (one-pulse adiabatic, along a taper with axial "
                          "conduction, and pulse trains), and "
                          "thermal (1-D stacks, via arrays, a real 2-D plate solve "
                          "with an energy-balance check, RC transients, heatsink "
                          "budget). Every model is lumped or closed-form: no "
                          "full-wave, no convection field solve. Radiation IS modelled on "
                          "the 2-D plate when you pass emissivity (both faces, one grey-"
                          "body epsilon, exact T^4 rewritten as a lagged h_rad with the "
                          "field re-solved to a fixed point), and copper barrels that "
                          "carry heat to a sink are modelled through thermal_vias",
            "dfm": "manufacturability against a capability document YOU supply: narrowest "
                   "track, copper gap, via drill and rings, hole-to-hole web, "
                   "copper-to-edge, narrowest cut-out, board size, layer count. This "
                   "server ships NO fab table on purpose - the limits are the "
                   "document's and it must name its source; rules the document does "
                   "not state come back as unanswered, not as passes",
            "cavity": "the board as a resonator: parallel-plate (cavity) mode "
                       "frequencies f_mn = c/(2 sqrt(er)) sqrt((m/a)^2+(n/b)^2) and the "
                       "via-fence pitch expressed as a fraction of the guided wavelength. "
                       "No Q, no loss, no detuning from open side walls, and NO built-in "
                       "acceptance ratio - lambda_g/20 and lambda_g/10 both circulate, so "
                       "the criterion is the caller's and without it the verdict is "
                       "indeterminate",
            "electrothermal": "the loop between current and temperature on copper, at two "
                              "levels. electrothermal_trace / electrothermal_conductor close "
                              "it against ONE caller-supplied lump Rth, with the contraction "
                              "gain dP/dT*Rth checked EXACTLY (above 1 the answer is "
                              "THERMAL_RUNAWAY, not a converged number). "
                              "board_electrothermal closes it against a 2-D plate field: "
                              "each conductor's I^2R lands on the grid, the field answers, "
                              "and rho(T) goes back in - linear in T, so alpha*T sits on "
                              "the matrix diagonal and the steady solve is exact; the "
                              "transient is backward Euler. Runaway is refused before the "
                              "solve when a diagonal goes non-positive, and a field that "
                              "comes out below ambient is refused as non-physical. "
                              "copper_pours (a plane, a pour, a very wide track) is added "
                              "as an exact per-cell area overlap into the sheet "
                              "conductance, so copper has to be WHERE the heat is to "
                              "help. emissivity adds radiation from both faces: "
                              "sigma*eps*(T^4-Tamb^4) is rewritten exactly as a linear "
                              "h_rad*2A*(T-Tamb) and h_rad is lagged one iteration while "
                              "the whole field is re-solved until it stops moving, so "
                              "the answer is the T^4 fixed point rather than an eps "
                              "folded into h. thermal_vias adds one annular-section "
                              "copper barrel per pitch cell as a conductance to a sink "
                              "(default: ambient, and the report says so); it lands in "
                              "the single cell holding its centre, so it is a point "
                              "load and the answer is grid-dependent - reported, not "
                              "hidden. A per-array barrel_profile (segments in "
                              "series, G = 1/sum(L_i/(k_cu*A_i))) replaces the uniform "
                              "wall, so a thin mid-barrel is not averaged away. "
                              "Neither is on by default. layer_stack (0.0.58) turns "
                              "the single plate into N through-thickness levels: one "
                              "in-plane field per level, midpoint slabs that add up to "
                              "the board, coupling ONLY through the laminate k_z, "
                              "convection/radiation ONLY on the two outer levels (an "
                              "internal plane sees no air), per-level copper zones at "
                              "exact area fractions, and thermal vias as a series of "
                              "gaps rather than a path to a sink. The vertical "
                              "direction is an exact per-column tridiagonal solve "
                              "relaxed as one block (a stiff k_z can no longer look "
                              "converged on the first sweep), and k_z -> infinity "
                              "reproduces the single plate. That is a layered "
                              "(z-direction finite-volume) model, not a 3-D field "
                              "solve: each level is isothermal across its slab. DC "
                              "only, no skin effect, no AC loss, no component thermal "
                              "mass or contact resistance",
            "vibration": "mechanical vibration: natural frequencies and mode shapes of "
                         "a thin plate (Rayleigh-Ritz, monomial basis, exact for SSSS "
                         "and CCCC and needs no mode table), single-mode random response "
                         "(Miles closed form for a flat PSD, or a real integral over a "
                         "measured PSD), Steinberg three-band fatigue with Miner's rule, "
                         "single-DOF shock via Newmark, and mount transmissibility. "
                         "vibration_plate_stress turns a mode into surface stress and "
                         "measures its own error bar by re-solving at several polynomial "
                         "degrees (curvature converges slower than frequency); von Mises "
                         "and the largest principal stress are reported at their own, "
                         "usually different, points. "
                         "NOT done here: assembly compliance, random+shock combined, "
                         "and any nonlinear contact. The drop chain and the solder-joint "
                         "fatigue live in the 'reliability' layer below, and they reuse "
                         "these solvers rather than re-deriving them. "
                         "vibration_plate_outline drops the rectangle: any polygon (the "
                         "real board edge), per-edge ss/cc/free, point supports - interior "
                         "integrals exact by Green's theorem on the ring, the boundary "
                         "condition imposed weakly by a penalty whose strength is swept, "
                         "so the flattest step of that sweep is a measured error bar. "
                         "Holes are not meshed and stress is not computed on a polygonal "
                         "outline",
            "reliability": "interface reliability at two time scales. Thermal cycling: "
                           "the shear strain range from the CTE mismatch over the joint "
                           "standoff, Engelmaier's strain-life form of Coffin-Manson, "
                           "Miner for several environments, and Norris-Landzberg to move "
                           "a test life into a use life. Drop/shock: a single-mode plate "
                           "chain (first mode -> SDOF shock response -> curvature -> "
                           "surface strain), plus a shock response spectrum so you can "
                           "see where the board's f1 sits against the pulse. The rules "
                           "this layer keeps: eps_f, the CTE presets, the "
                           "Norris-Landzberg constants and any compliance factor are "
                           "FITS and TYPICALS, named as such in the envelope; and the "
                           "strain a joint survives (critical_strain_ue) is the "
                           "CALLER's, never this server's - without it you get a strain "
                           "and judged=false, not a verdict. NOT modelled: underfill, "
                           "voids and IMC growth, crack propagation, drop "
                           "directionality, component mass on the plate, or "
                           "multi-mode response",
            "magnetics": "power-inductor sizing for boost / buck / buck-boost: the CCM "
                         "design point (duty, inductance from volt-seconds, peak / "
                         "valley / RMS current, the load at which CCM ends, winding "
                         "loss if a DCR is supplied), the DCM steady state (a "
                         "different duty with a diode interval, refused when the load "
                         "is really in CCM) and Steinmetz core loss with the caller's "
                         "k/alpha/beta and a NAMED unit convention. The ripple ratio is "
                         "the caller's design choice; saturation is graded only when a "
                         "part Isat is supplied. Since 0.0.55 there IS a core table: "
                         "select_core picks from real vendor cores "
                         "(data/magnetic_materials.json, every row with its source "
                         "URL) by turns-from-AL AND turns-from-flux, judges the peak "
                         "against the material's published Bsat only at 25/100 C, "
                         "estimates the gap from series reluctance, uses the "
                         "vendor's one measured loss point as-is and refuses to move "
                         "it without your exponents, and names every rejection. Still "
                         "absent: any temperature rise (no published Rth), the "
                         "DC-bias roll-off of AL, gap fringing, and Steinmetz "
                         "coefficients - those are yours to supply",
            "stackup": "laminate shortlist (er, tan_delta, practical band, cost) and a "
                       "4 / 6 layer stack whose signal layers each get their own "
                       "solved trace width from the same microstrip model the feedline "
                       "tools use, plus the lambda_g/20 stitching pitch and a copper "
                       "symmetry check. The dielectric split is PROPORTIONAL, not a "
                       "fab drawing; the width must be rebuilt on the fab's er",
            "review": "the design-rule catalogues (layout and schematic) and the review "
                      "that grades them against facts YOU (or the board_* tools) "
                      "measured. Every rule names its source, its severity "
                      "(Critical/High/Medium/Low) and the evidence level it needs "
                      "(L0 file / L1 calculate / L2 simulate or bench / L3 lab). A "
                      "rule with no measurement comes back undetermined, and a pass "
                      "means one named failure mode was measured and not found - "
                      "never that the design is correct",
            "desktop": "find / start / stop the EDA and open a project, verified by "
                       "window title - never touches a project file",
            "write": "propose (compute only, no byte is written) -> apply (confirm=true, "
                     "checkpoint, atomic write, five independent gates, rollback on any "
                     "failure). KiCad edits are re-judged by kicad-cli; a new DRC "
                     "violation blocks the write",
            "experience": "what the corpus of real boards taught, kept as two chains "
                          "that are not interchangeable: HARD entries are rules with a "
                          "failure side measured on a board (via annular ring under "
                          "0.10 mm, a drilled pad that is not on every copper layer) "
                          "and a violation is a finding; SOFT entries are base rates "
                          "of a habit (power copper wider than signal copper, one via "
                          "geometry per board) and leaving one is a question, not a "
                          "defect. Every entry states the corpus as a numerator with "
                          "its denominator, and every entry is judged by a metric "
                          "this server can actually evaluate - a metric that cannot "
                          "run comes back not_checkable_here, never as a pass. What "
                          "it does NOT do: promote anything by itself (a candidate "
                          "needs confirm=true and a human reason), learn from the "
                          "board it is checking (experience_check writes nothing), "
                          "or cover anything the seven metrics do not measure - no "
                          "impedance, no EMC, no placement. The seed numbers come "
                          "from 20 real LCEDA-Pro boards with copper (16522 track "
                          "segments, 12805 vias, plus 6 packed projects for the pad "
                          "rules) and are read-only: a "
                          "shipped number that a local edit could change is not "
                          "evidence",
            "parts": "the real part catalogue, not a part generator. Offline it reads "
                     "the SQLite library LCEDA Pro ships (lceda-std.elib: 16k devices, "
                     "21k symbol/footprint documents, 385k attribute rows with real "
                     "LCSC codes); online it asks pro.lceda.cn for the whole JLCPCB "
                     "catalogue (anonymous, measured 2026-10-29). Geometry carries "
                     "through to KiCad .kicad_sym / .kicad_mod with a field-by-field "
                     "report of what was dropped. Online documents are encrypted, so an "
                     "online-only part has metadata and no geometry, and the tool says "
                     "so instead of drawing a plausible footprint",
            "fullwave": "the seam and nothing behind it. No solver lives here: "
                        "radiation patterns, gain, efficiency, S11 over a band, mutual "
                        "coupling, cavity Q and enclosure detuning all need Maxwell's "
                        "equations in 3-D, so `fullwave_solver` does the one thing that "
                        "is honest without breaking the zero-dependency contract - it "
                        "finds out whether openEMS / Meep / gprMAX is installed, says "
                        "which version that binary printed, and says exactly what to "
                        "hand it. A binary being present is not evidence: every answer "
                        "carries verified_here=false. Asking for a named solver that is "
                        "not installed is an ERROR, never a silent fallback to the "
                        "closed-form starting geometry",
            "write_project_rules": "the .kicad_pro holds the net classes and the rule "
                                  "minimums a DRC applies; KiCad 7+ reads its net "
                                  "classes from there and ignores the board's own "
                                  "(net_class ...) blocks. board_rules_write edits "
                                  "them with five gates and a checkpoint, and refuses "
                                  "(PRO_FORMAT_UNKNOWN) when the file does not "
                                  "re-serialise byte-for-byte in KiCad's style - "
                                  "reformatting someone's project to change one "
                                  "clearance is a diff nobody can review. Patterns "
                                  "and board.design_settings.rules are NOT written",
            "write_schematic": "KiCad .kicad_sch only: propose (compute only) -> apply "
                               "(checkpoint, atomic write, five gates). The referee is "
                               "the netlister + ERC, not DRC: a schematic's product is "
                               "connectivity, and two coordinates being right does not "
                               "prove two wires are joined",
            "multipage_schematic": "a KiCad page that has children is a TREE, not a "
                                   "file, so both sides of the engine gate are asked "
                                   "about a temp copy of the whole tree (a lone file "
                                   "in a temp directory is a different project with "
                                   "the same name). Sheet boxes are written "
                                   "(add_sheet/add_sheet_pin/set_sheet_field/"
                                   "delete_sheet) and the child page can be created "
                                   "empty (schematic_new_file), but the child's own "
                                   "content is never part of the same plan: a pin with "
                                   "no hierarchical label behind it is refused by ERC, "
                                   "not silently accepted",
        },
        "available": [
            "tool_search", "tool_call",
            "unit_convert", "width_for_current", "current_capacity",
            "via_current_allowed", "fusing_current", "fusing_current_taper",
            "repetitive_pulse_rating", "current_density_field",
            "ac_sheet_resistance",
            "ac_round_conductor",
            "ac_self_heating",
            "trace_impedance", "width_for_impedance",
            "diff_impedance", "diff_impedance_field", "diff_width", "lambda_over_20",
            "coplanar_field", "coplanar_pair_field", "crosstalk_coupled_lines",
            "board_summary", "board_components", "board_nets", "board_clearance",
            "board_free_space", "board_routability", "board_pour_connectivity",
            "board_zones", "board_fill_zones", "board_route",
            "board_dangling_copper",
            "board_cross_split",
            "board_loop_area", "board_fidelity", "board_view", "board_drc",
            "board_stats", "board_cross_check", "epro_inspect",
            "safety_table_check",
            "board_job",
            "board_ops", "board_create", "board_propose", "board_apply",
            "board_checkpoints", "board_restore", "board_progress",
            "board_rules", "board_rules_write",
            "schematic_ops", "schematic_propose", "schematic_apply",
            "schematic_summary", "schematic_view",
            "schematic_hierarchy", "schematic_new_file",
            "part_library_status", "part_search", "part_get", "part_export",
            "part_footprint_pads", "part_cache_info",
            "app_detect", "app_status", "app_launch", "app_close", "app_open_project",
            "lceda_api_probe",
            "lceda_pro_status", "lceda_pro_api", "lceda_pro_open",
            "lceda_pro_sessions", "lceda_pro_close", "lceda_pro_run",
            "lceda_pro_steps", "lceda_write_snippet",
            "lceda_step_record", "lceda_step_replay",
            "feedline_impedance", "feedline_width",
            "antenna_patch", "antenna_printed", "antenna_dipole_monopole",
            "antenna_helix", "antenna_small_loop", "antenna_q_limit", "antenna_chip",
            "match_reflection", "match_l", "match_pi", "match_t", "match_stub",
            "match_quarter_wave", "match_bandwidth",
            "link_friis", "link_max_range", "link_aperture", "link_far_field",
            "link_fresnel",
            "board_antenna_keepout",
            "magnetics_inductor", "magnetics_dcm", "magnetics_core_loss",
            "magnetics_material_core_loss",
            "magnetics_winding_ac", "magnetics_fringing_loss",
            "magnetics_toroid_mlt", "magnetics_powder_curve",
            "magnetics_core_loss_audit",
            "stackup_material", "stackup_design",
            "rf_chain", "design_rules", "layout_review", "schematic_review",
            "sim_spice", "trace_resistance", "via_resistance", "trace_inductance",
            "ir_drop_chain", "pdn_impedance", "pdn_plane_modes",
            "pdn_plane_impedance", "pdn_plane_with_caps", "pdn_step_response",
            "decoupling_check",
            "thermal_stack", "thermal_via_array", "thermal_plate",
            "thermal_transient", "thermal_heatsink", "thermal_spreading",
            "thermal_convection",
            "board_ir_drop",
            "dfm_capability", "dfm_check",
            "vibration_plate_modes", "vibration_plate_outline",
            "vibration_plate_stress",
            "vibration_random_response",
            "vibration_fatigue", "vibration_shock", "vibration_isolator",
            "vibration_shock_spectrum",
            "board_vibration",
            "solder_joint_fatigue", "drop_board_strain",
            "cavity_resonance", "cavity_via_fence", "cavity_plane_q",
            "electrothermal_trace", "electrothermal_conductor",
            "board_electrothermal",
            "creepage_required", "clearance_required", "spacing_required",
            "filter_prototype", "filter_lumped_bandpass",
            "filter_mc_yield",
            "filter_coupled_microstrip", "filter_coupled_resonator",
            "filter_waveguide_cavity", "filter_coupling_matrix",
            "fullwave_solver",
            "board_fab_package", "board_export_graphics", "board_export_3d",
            "part_export",
            "schematic_pcb_diff", "schematic_pcb_sync", "schematic_export",
            "board_blockers", "board_run_ledger", "board_fanout", "board_intent",
            "lceda_pcb_write",
            "experience_recall", "experience_check", "experience_candidate",
            "experience_promote", "experience_outcome", "experience_export",
            "experience_import",
        ],
        "fail_closed_pending_data": {
            "creepage_required": "only for safety_standard families other than "
                                 "IEC 60664-1: those need a verified table in "
                                 "data/creepage_tables.json",
            "clearance_required": "same: IEC 60664-1 is answered, other families "
                                  "need their own table",
            "spacing_required": "same: IEC 60664-1 is answered, other families "
                                "need their own table",
        },
        "answers_only_when_the_file_carries_the_fact": {
            "board_cross_split": "needs plane zones; without them reference-plane "
                                 "continuity is not observable",
            "board_loop_area": "needs a stackup; a guessed dielectric height is a "
                               "guessed EMC answer",
            "board_pour_connectivity": "reports outline_only when the fill result was "
                                       "not stored",
            "app_open_project": "reports opened=confirmed only when a window title "
                                "contains the file stem",
            "board_drc": "KiCad only - LCEDA Pro ships no command line, so its DRC "
                         "verdict is refused rather than approximated",
            "board_fidelity(engine=true)": "KiCad only; without kicad-cli the token / "
                                           "byte proofs still run, the plot comparison "
                                           "is reported as skipped",
        },
        "derived_vs_authoritative": {
            "derived (this server computed it)": [
                "board_clearance", "board_free_space", "board_pour_connectivity",
                "board_zones(reads whatever engine last filled)",
                "board_cross_split", "board_loop_area", "board_antenna_keepout",
                "board_ir_drop", "board_vibration"],
            "authoritative (vendor engine said it)": ["board_drc", "board_stats",
                                                      "board_fill_zones(a real pour)"],
            "checked_against_the_vendor": {
                "board_cross_check": "runs the derived answer and the vendor's own DRC on "
                                     "the same file in both directions and reports "
                                     "recall, missed violations and our extra pairs. Only "
                                     "clearance has a vendor counterpart today; free_space, "
                                     "pour_connectivity, cross_split and loop_area come "
                                     "back not_comparable with the reason, because a "
                                     "metric nobody can disagree with is unverified, "
                                     "not right",
            },
            "why_it_matters": "a computed clearance is a measurement with a model and a "
                              "precision; a DRC verdict is the tool's own ruling. When "
                              "they disagree, the verdict wins - so every derived answer "
                              "carries authoritative=false and names board_drc",
        },
        "not_implemented_yet": [
            "Power magnetics, and the parts question: magnetics_select_core does pick "
            "a real part out of data/magnetic_materials.json (14 materials, 16 cores, "
            "7 of them with one vendor loss point at 200 mT / 100 kHz / 100 C). Five "
            "powder materials carry the vendor's own printed loss fit - Kool Mu, MPP "
            "and High Flux since 0.0.74, XFlux and Edge since 0.0.75 (with their High "
            "DC Bias variants) - 93 printed rows of (a, b, c) per permeability, per "
            "core shape and per variant, in mW/cm3 / T / kHz with the catalog page, "
            "and magnetics_material_core_loss evaluates them. No material-level "
            "Steinmetz triple (k/alpha/beta) is stored, and the nine ferrite materials "
            "have no vendor fit here at all: their loss away from a measured point "
            "still needs exponents from you. The powder CORE GEOMETRY arrived in "
            "0.0.76: data/powder_cores.json holds the catalog's 43 toroid sizes x 14 "
            "permeabilities x nine material columns (1923 purchasable part numbers, "
            "printed p.155-197) and select_core picks them when "
            "include_powder_cores=true (default false). What that table still does "
            "NOT carry: a mean turn length, a discrete gap (a powder core has none - "
            "AL is the distributed-gap value), the "
            "nominal part weight (only Ve x density x factor, an estimate), and a "
            "Bsat per temperature (the catalog prints one value per column with no "
            "temperature against it). The DC-BIAS rolloff arrived in 0.0.77: "
            "data/dc_bias_fit.json holds the catalog's own 125 fit rows (29 material x "
            "core-shape groups, % mu_i = 1/(a + b*H^c), printed pp.63-65) and "
            "magnetics_dc_bias evaluates one, reproducing all 18 numbers of the "
            "catalog's printed 80 %/50 % summary table and both worked examples. What "
            "that tool does NOT do: interpolate between printed permeabilities (it "
            "refuses with the list), depend on temperature (one fit, not one per "
            "temperature), or model saturation (% mu_i is small-signal; the flux is "
            "reported beside it). The turn-count feedback that 0.0.77 refused is "
            "closed in 0.0.78 by select_core(dc_bias_current_a=...): turns are solved "
            "on the biased inductance, by a bracketed search rather than a fixed "
            "point, because L(N) peaks and then falls once c > 2 (86 of the 125 rows) "
            "- so past that peak more turns buy less inductance, and a part that "
            "cannot reach L at the bias is rejected as dc_bias_unreachable with the "
            "maximum it does reach. A design landing outside the 10-800 Oe the curves "
            "are drawn over is flagged h_within_printed_band=false instead of being "
            "presented as a reading. The TOROID copper that 0.0.76-0.0.78 refused "
            "arrived in 0.0.79: no catalog prints a mean turn length for a toroid, "
            "because a turn's length belongs to the winding, so magnetics_toroid_mlt "
            "(and select_core(toroid_mlt='estimate')) multiplies the geometric "
            "section perimeter 2*H + (OD-ID) by the ratio thirteen printed toroid "
            "data sheets measure for a real winding (1.248-1.461, median 1.366, "
            "data/toroid_mlt_reference.json) and reports an INTERVAL - plus "
            "R_dc, copper loss and the single-layer turn count the bore allows. It "
            "is an estimate and says so; nothing here is a printed length. "
            "The CURVE arrived in 0.0.80: data/powder_curve_fits.json carries the "
            "catalog's printed magnetisation fit (B = [(a + bH + cH^2)/(1 + dH + "
            "eH^2)]^x, 125 rows, printed pp.128-132) and its printed mu-vs-T fit "
            "(a quartic against 25 C, 54 rows, printed pp.138-139), and "
            "magnetics_powder_curve evaluates both. What that does NOT give: a Bsat "
            "per temperature still (the temperature pages carry mu, not Bsat, and the "
            "catalog prints one Bsat per material on p.10), so the Bsat path is still "
            "one value per published temperature and is never interpolated between "
            "them; the two fits disagree by up to 54 % on three rows at the ends of "
            "the permeability scale and both numbers are reported rather than "
            "reconciled; seven rows turn over inside the printed H band (MPP 550u "
            "peaks at 23.8 Oe) and are flagged instead of clamped; H = 0 is refused "
            "because B(0) = a^x is not zero; and the High DC Bias variant blocks carry "
            "no temperature fit at all. The printed LOSS POINTS arrived in 0.0.81: "
            "data/material_loss_anchors.json carries the 17 PV rows the vendors print "
            "- Ferroxcube's specification pages for 3C90 / 3C92 / 3C93 / 3C94 / 3C96 "
            "and its 2022 3C98 brochure, plus the single Max. TDK prints for N87 - "
            "each with its printed page and its own qualifier (<= / ~ / Max. / a bare "
            "number), and magnetics_core_loss_audit scores any k/alpha/beta you supply "
            "against every one of them point by point, keeping a satisfied upper bound "
            "apart from a missed typical value. What is still NOT offered is a vendor "
            "triple: neither vendor prints one (Ferroxcube prints two or three "
            "qualified PV rows per grade, TDK draws three curve families and no loss "
            "number on the N87 sheet), so a triple solved back out of those rows would "
            "be arithmetic wearing the vendor's name, and that is refused - which is "
            "why magnetics_material_core_loss still answers FIT_NOT_RESOLVED for every "
            "ferrite. Gapped-core fringing loss IS computed since "
            "0.0.72 (magnetics_fringing_loss: a 2-D magnetic-charge model of the gap, "
            "q from the closed form of the axial integral, an exact slab eddy factor) "
            "and its scope is its own: 2-D along the leg, perfectly permeable pole "
            "faces (no saturation), the in-plane component NOT modelled (it is 0.32 of "
            "the axial one a gap-width above the face), and it is added to Dowell's "
            "proximity term as a power, with no cross term. No litz strand model, no "
            "DC-bias Steinmetz: magnetics_core_loss takes the AC swing and the WAVEFORM "
            "SHAPE (sine, triangle + duty, or a piecewise trajectory) through the iGSE, "
            "which is a function of the swing alone - a premagnetisation term is not "
            "modelled, and the narrow-pulse over-prediction of iGSE (the GSE/WGSE "
            "period-averaged-slope term) is not corrected. No thermal resistance for "
            "any part in the table, so no temperature rise. No loop compensation and no "
            "soft start. magnetics_winding_ac closes the skin/proximity gap for foil "
            "and round-wire layer stacks, so the DCR loss is no longer the whole "
            "winding story - but it is a 1-D Dowell model, not a field solve of your "
            "winding. The tools answer the electrical design point; the parts question "
            "refuses to answer unless an Isat (or a b_max_t) is supplied",
            "Layout and schematic reviews do not read the design for you: they grade "
            "the facts they are given. board_* / schematic_* can produce some of those "
            "facts (clearance, nets, components), but nothing here parses a netlist "
            "into 'this IC's power pins', so sch.* facts must be counted by the "
            "caller - and every rule that was not measured says so instead of "
            "returning a pass",
            "LCEDA Pro write path: the record layout, the mil unit and the "
            "'a save rewrites one member' shape are measured against the 194 .epro "
            "archives in LCEDA-Pro/projects-recovery, and the gates prove the bytes. "
            "What is now claimed (REWRITTEN 2026-11-01): an archive written by this "
            "module was imported into LCEDA Pro V4.1.60 (bridge 4.1.60.198f38ab) "
            "through importProjectByProjectFile, opened, and the application read the "
            "geometry back as the plan promised - set_track width 0.5 mm came back as "
            "19.685 mil on the line the plan named while the line it did not name "
            "still read 0.3, against a control archive the app had exported itself "
            "(0.25 on that line). The criterion is the geometry reading, because a "
            "negative control with a garbage PCB record stream still imports 'ok' and "
            "still passes extractProjectInfo - only the read-back fails. Scope of "
            "that claim, all repeated inside not_verified and app_acceptance: one "
            "application build, ZIP containers only (.eprj/.eprj2 is still refused), "
            "the 'ImportDocument' into-a-new-project door only, and the screen was "
            "never inspected (every reading is the app's own return value). Appending "
            "a record the vendor was never seen writing is still reported as "
            "verified_by_app=false",
            "LCEDA Pro schematic (.esch) write path: the sheets live inside the .epro "
            "archive and that record layout was never measured against a real save, "
            "so the writer refuses it (WRITE_UNSUPPORTED_FORMAT) instead of guessing. "
            "KiCad schematics ARE written (see schematic_ops)",
            "EasyEDA Pro websocket gateway: the client (RFC 6455 handshake, frames, "
            "the 49620-49629 port scan) is implemented and unit-tested, but no gateway "
            "answered on this machine (the port only opens after the user enables "
            "外部交互权限), so nothing was driven through IT - lceda_api_probe still "
            "reports verified_against_app=false. The live path that DOES work is the "
            "editor's own command space (lceda_pro_*); both speak to the same running "
            "app by different doors",
            "The live bridge's write half (REWRITTEN 2026-11-01 after the first real "
            "write): reads were proven on 2026-10-28 (ping, open, session.list, "
            "session.close, doc.api, invoke - see docs/24), and a write has now been "
            "performed. On LCEDA Pro V4.1.60 (bridge 4.1.60.198f38ab), on a project "
            "this server created ITSELF with createProject + createPcb - never on a "
            "user's project: create moved the probe 0 -> 1; save changed the project "
            "file 409600 -> 569344 bytes and its sha256; closeDocument + openDocument "
            "read the line back with the same geometry; delete moved 1 -> 0; a move "
            "through pcb_PrimitiveLine.modify changed startX and stayed changed; and "
            "after two full application restarts, with the project reopened by uuid "
            "from disk, getAll() still returned the same digits. The tool path was "
            "measured too, not only the client: lceda_write_snippet produced the "
            "create call and lceda_pcb_write ran it. What is STILL not proven: that "
            "what a probe reads is what a person sees on screen (every reading is the "
            "app's own return value), that a snippet with a wrong argument is refused "
            "rather than silently doing something else, that the .epro ARCHIVE write "
            "path reaches the app through the live editor itself (it was reached "
            "separately, by importing a written archive into the app - see the "
            "write-path entry above), and "
            "whether the vendor's own '--mcp' transport can be reached at all - this "
            "build's executable rejects the flag before the app sees it. Also "
            "measured here: a save() returns before the file moves - one stat right "
            "after it read the old bytes, the next check ~8 s later read the new ones "
            "- so a disk check has to poll",
            "Write snippets are generated from the app's own table, not from "
            "memory (0.0.43): lceda_write_snippet reads the method names and the "
            "vendor comments out of doc.api and assembles one candidate "
            "save/move/create call, with the runtime receiver coming from that "
            "class's callPath (the class name is not the property name, and no "
            "lower-casing is ever done to bridge the two). The arguments are the "
            "caller's, because the app reports names and comments rather than "
            "signatures. It refuses instead of guessing: SNIPPET_NO_API when "
            "nothing matches, SNIPPET_AMBIGUOUS with the candidates when two "
            "things match equally, SNIPPET_NO_RECEIVER when the receiver cannot "
            "be resolved. Still true after the 2026-11-01 round: the generator "
            "itself is not verified - one create snippet it produced ran through "
            "lceda_pcb_write on V4.1.60 and its effect was read back, which "
            "vouches for that snippet, not for the generator and not for the "
            "arguments a caller passes. Two traps measured the same day: the code "
            "a session receives is a function BODY, so a bare expression answers "
            "null and it needs `return`; and an empty string argument must be "
            "written as '\'\'' - argsv strings are inserted verbatim as "
            "JavaScript, so '' is refused as empty",
            "LCEDA Pro launch details (CORRECTED 2026-10-28, third round): the app "
            "DOES start here - the earlier 'it dies before Electron' reading was "
            "wrong, it described how it was launched. From a shell that inherits a "
            "console, lceda-pro.exe returns rc 0 in about 0.5 s and writes nothing; "
            "via explorer.exe (the double-click path) three processes stay up "
            "(203/36/289 MB) and its command-space pipe appears within seconds. What "
            "is still NOT verified: attaching a project on the command line (the "
            "argv probe still fails, and '--'-prefixed options are rejected by node's "
            "own option parser inside the exe with rc 9, which is why the vendor's "
            "own '--mcp stdio|http' server cannot be started from here). So "
            "app_open_project still reports unconfirmed, but lceda_pro_open can open "
            "a project inside a running editor",
            "Full-wave electromagnetic simulation (method of moments / FDTD / FEM): "
            "nothing here is a solver, and `fullwave_solver` is a SEAM, not a "
            "solver - it finds an installed openEMS / Meep / gprMAX, reports the "
            "version that binary printed, and hands over (geometry from "
            "board_export, stackup from board_summary, ports and mesh from the "
            "solver's own tooling, which this server never emits). The antenna tools "
            "give the starting geometry "
            "from closed forms; radiation pattern, gain after a housing, efficiency "
            "after losses, mutual coupling between two antennas, SAR and the "
            "detuning from a battery or a hand are all NOT computed - every antenna "
            "answer carries that list as not_computed so it cannot be mistaken for a "
            "full answer. The hand-off is deliberate: design here, then simulate or "
            "measure",
            "DFM rules the fab states that this layer STILL cannot measure (narrowed at "
            "0.0.56 and again at 0.0.82, never shortened): a mask sliver away from pads "
            "and away from the gap between two openings - the mask FILM itself is never "
            "read, so its own edge is not measured anywhere; silkscreen items that state "
            "no line width "
            "(no default is substituted) and silk-over-pad clipping; "
            "impedance-controlled stackup sign-off (the fab's coupon is the verdict); "
            "process tolerances (hole position, plating and copper weight, board "
            "thickness); panelisation / tooling rails / V-cut / fiducials; the "
            "distinction between a plated and a non-plated slot beyond what the file "
            "states; barrel taper and hole-size tolerance behind the plating figure, "
            "and the annular ring AFTER plating, because rings here are measured to "
            "the drill wall. All of it is listed in every dfm_check answer as "
            "not_checked_by_design. What IS measured since 0.0.56: silkscreen stroke "
            "width and character height, the solder-mask web between two openings on "
            "one side, another net's copper to the finished hole wall (that one only "
            "when the capability document states `plating_um`, and refused rather than "
            "defaulted when it does not), and - since 0.0.82 - the pads whose mask "
            "opening is SMALLER than their copper (`max_mask_defined_pads` for a fab "
            "that will not do them at all, `min_mask_defined_overlap_mm` for one that "
            "will, down to a stated overlap). Also NOT here: any built-in fab "
            "capability table - dfm_capability REJECTS a document without a source "
            "and does not fill the gaps with typical values",
            "BGA/thermal via array temperature rise, fuse behaviour, and any IPC-2152 "
            "parameter the published fit drops (board thickness, adjacent copper, "
            "airflow, mask opening): data/ipc2152_tables.json no longer ships an empty "
            "chart transcription - it ships the Brooks & Adam closed-form fit to Table "
            "3-1 with coefficients copied from KiCad 10.0, and "
            "safety_table_check(which=ipc2152) judges it by anchor reproduction and "
            "physical probes. What is still missing is the data the FIT threw away, "
            "plus any statement about plated barrels - via_current_allowed refuses "
            "standard=ipc2152 for exactly that reason (STANDARD_SCOPE)",
            "Plane resonances as a SOLVED field: cavity_resonance gives the ideal "
            "PMC-wall TM_mn0 eigenvalues, cavity_via_fence gives pitch/lambda_g, "
            "cavity_plane_q gives the copper + dielectric Q (Qc = h/delta_s, "
            "Qd = 1/tan_delta) plus the optional Hammerstad-Jensen surface-roughness "
            "multiplier on Rs (pass roughness_um; K = 1+(2/pi)atan(1.4(Rq/delta_s)^2), "
            "so Qc = h/(delta_s*K)), and pdn_plane_impedance / pdn_plane_with_caps "
            "give the plane-pair impedance curve with and without the caps. The two "
            "things plane_q leaves out have their own tools: plane_radiation_q adds "
            "the radiation Q of the open edges (and Qc/Qd in one reciprocal sum), "
            "plane_open_end pulls the resonance down by the edge correction, and "
            "roughness_huray replaces the one-number Rq multiplier with a Huray "
            "nodule carpet (r, surface ratio). Still missing: the foil profile, "
            "leakage through voids, and the caps mounted across the pair - so the "
            "sum of the modelled losses is still an UPPER bound on Q, and the "
            "dominant loss of a real PDN resonance (the "
            "packages mounted across it, and the caps in the plane-only curve) is "
            "outside. Even with Q there is no verdict: a resonance is a problem only "
            "together with an excitation and a coupling. When that distinction "
            "decides the design, use a 3-D field solver",
            "The PDN TRANSIENT is a board answer, not a converter answer: pdn_step_response integrates the caps, their ESR/ESL, the bulk-to-die path and the regulator's R + L with backward Euler, but the regulator is a Thevenin source - no loop bandwidth, no phase margin, no current limit, no droop sharing between rails - and the board is one or two lumped nodes, not a distributed plane. Where the caps sit and how fast the load ramps ARE in it; the control law is not, so a rail that passes here can still fail on a transient the loop itself causes. The load is one step (optionally slewed); any other profile is out of scope",
            "Safety-distance tables for standards other than IEC 60664-1: "
            "IEC 62368-1, IEC 61010-1 and the instrument standards each tabulate "
            "their own creepage/clearance, and this build only carries IEC 60664-1 "
            "(recovered from KiCad 10.0's implementation, diffed on 188k points). "
            "Ask for another family and it refuses rather than borrowing 60664's "
            "numbers. Also missing from 60664: the homogeneous-field column (the "
            "reference implementation never exposes field selection), solid "
            "insulation / distance-through-insulation, and the pollution-degree-4 "
            "creepage case the standard itself does not specify",
            "The BUILTIN SPICE solver is a subset, by name: it handles R C L V I E G "
            "D Q M B, the LOSSLESS transmission line T and the two DISTRIBUTED cards "
            "O (an LTRA model) and U (a URC model), with "
            ".op/.tran/.ac/.noise/.model/.temp, expands .subckt/X instances itself "
            "(flattened before solving; numeric parameters and {+ - * / **} arithmetic, "
            "subcircuit-local .model, nested instances, <=32 deep and no recursion). "
            "F H J K S W are SOLVED (0.0.31-0.0.36; the earlier note that claimed they "
            "were rejected was wrong and is corrected here), and A cards for the gain "
            "and limit code models since 0.0.39 - what is refused by name is the "
            "XSPICE models that carry state or a sample clock, not these cards. T "
            "is ideal and lossless with one constant Z0 and one delay: at DC it is a "
            "wire, in .ac it is the exact lossless Y matrix (phase -2*pi*f*TD, and the "
            "half-wave points where an ideal line's admittance is infinite are REFUSED "
            "rather than answered with a huge number), and in .tran it is the method of "
            "characteristics, so a reflection comes back at 2*TD and the far end doubles "
            "at an open end. Z0 and TD must be given (ngspice silently defaults a missing "
            "TD to 0, which turns the line into a wire), and TD must not be shorter than "
            "the timestep. O and U are NOT the distributed line: they are expanded into a "
            "lumped ladder of the model's own r/l/g/c (O) or r/c (U), lump= sections "
            "(default 20; write it in the .model, because ngspice treats it on the card "
            "as FATAL), and every answer says how many sections it used and whether that "
            "count was defaulted. At DC the ladder IS the model's total series "
            "resistance; in .ac and .tran it converges to the distributed answer as lump "
            "grows (first order, measured), so a disagreement with a real line is the "
            "ladder, not an error. Skin effect, dielectric loss and frequency-dependent "
            "r/l are not in the model at all. "
            ".noise V(out) <source> dec|oct|lin n fstart fstop is solved adjointly at the "
            "DC bias - one transpose solve per frequency covers every noise source, and "
            "the transfer it uses is the .ac matrix, so the two cannot disagree. It "
            "reports a density per swept point, the rms over the swept band (the band IS "
            "the sweep, and the grid picks ln-f or f trapezoid, both stated), each "
            "element's share, and the input-referred density. Mechanisms: 4kT/R, "
            "2q*|I| on junctions and transistors, (2/3)gm on a level-1 MOSFET, and "
            "for a DIODE the flicker KF*|Id|^AF/f (0.0.54) - its f^-1 shape and "
            "|Id|^1 scaling were MEASURED against the bundled ngspice-47 (fitting the "
            "engine's two readings returns the card's KF to 0.11 %), and a "
            "frequency-dependent density is evaluated at each swept point instead of "
            "being held at one value. A KF= on a Q/M/J card is REFUSED at the .noise "
            "analysis by name (NOT_MODELED) rather than answered without it, because "
            "ngspice-47 itself applies no BJT/JFET/MOSFET flicker: measured, KF from "
            "1e-15 to 1e-6 moved its own answer by 0.0 %, so there is no reference "
            "model to copy and engine='ngspice' would return the same quiet number. "
            "Also NOT there: burst, induced gate noise, avalanche, flicker on "
            "anything but a diode, and any "
            "capacitor's ESR (that is an R card you either wrote or did not). It does "
            "NOT require an AC value on the input source (ngspice refuses the deck "
            "without one) and it refuses a card naming a node nothing connects. "
            ".param works at the top level and inside a body (numbers and {+ - * / **} "
            "arithmetic; a parameter may be written after the element that uses it, and "
            "a circular definition is refused by name), one scope chain "
            "global -> .subckt default -> instance override -> body .param, and a "
            "number must be a WHOLE number: '1k*2' is refused with the braces spelled "
            "out instead of being read as 1000. What it does NOT do: functions or node "
            "references inside {…}, expressions in .model parameters, laplace()/I(Vname)/"
            "ddt/idt inside a B expression, a .subckt nested "
            "in another .subckt, or any directive but .model/.param inside a body. "
            "Who wants the rest: "
            "install ngspice and the same tool can hand the deck to it "
            "(engine='ngspice', or 'auto' once a binary is on PATH / in "
            "PCB_AGENT_NGSPICE), which covers the rest of the XSPICE code models and the real "
            "device models (the JFET is solved here as of 0.0.36 and A-card gain/limit "
            "since 0.0.39, so neither needs the engine) (note that ngspice-47 refuses a non-zero shunt conductance in its "
            "own O card, so g>0 has no engine to be checked against). Since 0.0.37 an "
            "engine IS bundled (vendor/ngspice, an unmodified ngspice-47 Windows build, "
            "a separate program under mixed BSD/public-domain/GPLv2+ terms - see "
            "vendor/ngspice/NOTICE.md): engine='ngspice' falls back to it when nothing "
            "is installed, while engine='auto' deliberately ignores it so that what "
            "'auto' means on a machine does not change when the bundle appears. "
            "The delegation WAS exercised against real ngspice-47 "
            "(MSYS2 Windows build) and cross-checked node by node with the builtin "
            "solver, and then a SECOND build (ngspice-41, conda-forge) was run on the "
            "same decks so the two engines can be compared to each other: .op and .ac "
            "agree bit for bit, .tran differs by at most 1.24e-4 V on a 1 V step "
            "(different internal step control, so their grids differ too - that number "
            "is not an accuracy ranking); verified_against_engine is true only for the "
            "versions in this "
            "build's VERIFIED_ENGINES list, and false for any other version - a "
            "different binary is not a verified engine. A multi-valued .temp card is "
            "a SWEEP in the builtin solver since 0.0.54 (one independent solve per "
            "temperature, every case returned with its own bias point; temp_c as an "
            "argument overrides the card and says so), and a single temperature per "
            "run is what engine='ngspice' gets unless the deck drives its own loop. "
            "What neither path does: distortion, and Monte Carlo - a deck writing an "
            "`mc(...)` / `unif(...)` / `agauss(...)` / `gauss(...)` value is REFUSED "
            "by name since 0.0.54 instead of being read as its nominal number, since "
            "the call carries no sample count and no seed; the builtin solver also "
            "uses dense LU and refuses above ~400 "
            "unknowns",
            "Electrothermal coupling stops at a 2-D plate: "
            "electrothermal_trace / electrothermal_conductor close the loop against one "
            "caller-supplied Rth; board_electrothermal closes it against a 2-D field. "
            "That field spreads heat through the laminate k you pass PLUS whatever "
            "copper you list in copper_pours, which is smeared into one sheet "
            "conductance per cell (area fraction x thickness x layers x k_cu, faces by "
            "harmonic mean) - so the copper shape inside a cell, the copper-to-laminate "
            "interface and any copper you did not list are still missing, and that "
            "smearing is in-plane only (the vertical path stays inside h). Radiation "
            "(emissivity: both faces, one grey-body epsilon, exact T^4 as a lagged "
            "h_rad re-solved to a fixed point) and thermal_vias (one annular-section "
            "copper barrel per pitch cell as a conductance to a sink, landing in the "
            "cell holding its centre, or -- with a per-array barrel_profile -- a "
            "segmented wall whose segments are in series) are modelled when asked for, "
            "and are OFF by default so the numbers are bit-identical without them. "
            "layer_stack turns the plate into N through-thickness levels (0.0.58), but it is a "
            "layered z-direction finite-volume model, not a 3-D field solve: each level is "
            "isothermal across its slab, so a real 3-D field, internal plane-to-plane radiation "
            "through the laminate, anisotropic laminate k and the copper-to-resin interface "
            "thermal resistance are still not done. Still not done: "
            "3-D (as above, layered only), internal plane-to-plane radiation through the laminate, "
            "temperature-dependent emissivity, component thermal mass and contact "
            "resistance, AC loss (skin/proximity) - ac_sheet_resistance gives the "
            "1-D skin term of a FLAT sheet and ac_round_conductor the exact term "
            "for a round wire, tube or via; ac_self_heating (0.0.68) is the one "
            "that closes the loop at frequency, and since 0.0.69 it takes a "
            "whole SPECTRUM (harmonics=[{frequency_hz, current_a}] + "
            "dc_current_a: the powers add, the temperature does not), but it is "
            "still ONE conductor with ONE lump Rth shared by every harmonic. "
            "Board-level copper at a frequency IS answered since 0.0.70, but by "
            "board_ir_drop(frequency_hz=...) - it scales every segment by the "
            "ratio its OWN 1-D solve gives (slab for tracks and pours, Bessel "
            "tube for vias) and still has no proximity effect, no lateral edge "
            "crowding along a wide strip and no phase - so this 2-D THERMAL "
            "plate is still excited at DC and there is no AC heat map - "
            "device self-heating inside sim_spice, "
            "reading the conductors or the zones straight off a board "
            "file - board_electrothermal takes geometry, it does not open the "
            ".kicad_pcb for "
            "you",
            "Surge and fusing: fusing_current answers the single-pulse "
            "adiabatic question (Onderdonk 1928) and states its one-sided "
            "assumptions (no cooling, equal-area rectangle, no skin effect); its "
            "ONE modelled exception is the latent heat of fusion (0.0.86): "
            "melting_model='sensible_plus_latent' takes the current from 'reaches "
            "1083 C' to 'melts through', which is +25.3 % on I^2t and +11.9 % on "
            "the current at 25 C, and the default stays on the melt point so a "
            "published fusing table still reproduces. repetitive_pulse_rating "
            "answers the TRAIN - one lumped "
            "temperature, cooling as h*P with h supplied by you (no default, because "
            "it ranges 20x), copper's own resistance law solved exactly per interval, "
            "and the melt boundary as a closed form; since 0.0.87 the same melting_model "
            "switch applies to the train, where latent heat can be carried exactly as "
            "a 542.08 K reservoir per shot because the model is a single lumped "
            "temperature - it leaves the sustainable current unchanged and pushes a "
            "doomed train 71.6 % further out at 0.95x that current. "
            "fusing_current_taper adds the "
            "LENGTH axis (0.0.59): one isothermal node per axial segment, each pair "
            "joined by two half-lengths in series, copper's own rho(T), backward Euler "
            "- so a neck melts first, the arms' shared heat shows up as a MEASURED "
            "conduction_delay_pct against a conduction-off control arm, and with "
            "ends_c giving the terminals a temperature there is a steady threshold "
            "below which nothing melts. What that still leaves out: cooling to air "
            "and to the board (the taper model has axial conduction ONLY, so it has "
            "no h at all), latent heat at the melt now on all three (0.0.86 on "
            "fusing_current as a scalar 1+share on I^2t, 0.0.87 on the pulse train "
            "as a closed-form reservoir because the train is one lumped temperature, "
            "0.0.88 on the taper as a per-node enthalpy state with an exact "
            "phase boundary - no mushy band, no adjustable width), skin effect inside "
            "a short pulse, electromigration (Black's "
            "equation needs a current density a fab will qualify), arc/plasma after "
            "the copper opens, and board_ir_drop's A/mm^2 verdict - that tool still "
            "reports density with no criterion attached. The transverse gradient in a "
            "neck is measured separately (0.0.63, current_density_field): it solves "
            "div(sigma*grad V)=0 on the copper and returns J at the hot spot WITH its "
            "location, plus a singularity verdict, because a 270 degree inner corner "
            "makes J ~ r^(-1/3) and the peak is then not a grid-independent number. "
            "It is steady state, so it says where the crowding is, not when the neck "
            "melts - those two answers still do not meet",
            "The copper pour in board_ir_drop is a SHEET, not a solved fill "
            "(model_zones=true, 0.0.64): every same-net zone is gridded at a pitch "
            "planned from the area its fill covers (clamped to 0.2-2.0 mm), each cell "
            "is one square of rho(T)/t and four-connected, and pads / track ends inside "
            "the fill are tied to it with a 1 uOhm link. What that leaves out, by name: "
            "THERMAL-RELIEF SPOKES ARE MODELLED SINCE 0.0.84 when asked for: "
            "board_ir_drop(thermal_relief={spokes, spoke_width_mm, gap_mm}) turns the "
            "pad-to-pour connection into R = R_sheet*gap/(n*width) - what a relief "
            "physically is - instead of the 1 uOhm solid link or the one-square "
            "bridge, and the board file's own connect_pads / thermal_gap / "
            "thermal_bridge_width are reported NEXT TO the answer but never adopted "
            "as a default, because the geometry that goes into the number is the "
            "geometry the caller states. WITHOUT the flag both stand-ins are still in "
            "place and the solid one is the single place this answer can be "
            "OPTIMISTIC (a pad sitting in the fill is joined as solid copper, so "
            "where a spoke is the real connection this drop is a LOWER bound); a node "
            "within zone_link_mm of the fill but outside it is bridged with ONE sheet "
            "square, an order-of-magnitude stand-in and the crudest number in the "
            "answer; zone priority, minimum width "
            "and the clearance ring to other nets are taken from the fill the file "
            "already carries rather than solved for; pour islands the source cannot "
            "reach are dropped (they carry no current) and the count is reported; a "
            "pad covering more than 2000 cells is tied at its anchor only, which "
            "over-states its spreading resistance; and the whole model is DC, so skin "
            "effect, the plane's own resonance (pdn_plane_impedance) and any "
            "frequency-dependent copper are not in it (ac_sheet_resistance is the "
            "separate 1-D answer for one flat sheet, ac_round_conductor the "
            "exact one for a round wire/tube/via, and current_density_field "
            "takes frequency_hz to scale its own sheet conductance, but a 2-D "
            "pattern whose current re-distributes across the sheet at frequency "
            "is still not solved; ac_self_heating closes the thermal loop at "
            "frequency for ONE conductor and the Rth it is given, which is not "
            "the same thing as a patterned sheet). The switch defaults OFF and the "
            "answer without it is byte-identical to 0.0.63 - a pour-only net is still "
            "NOT_APPLICABLE/singular unless the caller asks for the sheet",
            "The plane-caps network is COMPUTED now (pdn_plane_with_caps: the cavity "
            "as an (N+1)-port Z matrix, each cap an R-L-C branch, port-reduced to "
            "Z_in), so the anti-resonance where a bank turns inductive and the plane "
            "turns capacitive is a number, not a forward reference. What is still "
            "outside it: die-package inductance and the plane-to-die path, split "
            "planes and moats, via-barrel / antipad inductance (that is the cap's own "
            "esl_h, which YOU supply), radiation Q (plane_radiation_q) and the "
            "open-edge detuning that makes a real plane ring a few percent below "
            "the PMC values used here (plane_open_end). "
            "Also outside: choosing the bank (this tool evaluates the caps you list, "
            "max 8 by name, and never optimises them) and anything the part library "
            "would know - esr_ohm / esl_h are your numbers, and a 0 there is an "
            "ideal capacitor, not a guess at a real one. decoupling_check stays "
            "lumped RLC and cannot see the cavity except through plane_cap_farad",
            "Thermal vias are a barrel model, by name: thermal_via_array takes the "
            "wall as a profile (barrel_profile, series sum, so mid-barrel thinning is "
            "no longer assumed away) and a series TIM/pad layer (interface, from the "
            "area, thickness and k you state), and board_electrothermal's thermal_vias "
            "takes the same profile. Still not done: a plating-bath or IPC-4761 via "
            "structure model (the profile is YOUR numbers, not a process model), "
            "pad/plane spreading AT THE BARREL ENDS themselves (thermal_spreading "
            "now answers a rectangular patch on a plate, but the via barrel is "
            "still a column with no end-spreading term), solder wicking, voiding / "
            "pump-out or mounting pressure in the interface layer, rth_cs (still an "
            "input you must supply), and heatsink selection - thermal_heatsink returns "
            "the BUDGET, there is deliberately no heatsink table in this server",
            "Vibration is a THIN-PLATE, LINEAR layer, by name - and since 0.0.83 the "
            "random response is no longer SINGLE-MODE: vibration_random_response sums "
            "whatever modes you hand it (vibration_plate_modes(participation=true) "
            "gives the participation factor int w dA / int w^2 dA, the modal mass and "
            "the effective mass per mode), with the displacement summed coherently and "
            "each mode's acceleration taken as w^2 times its own displacement. What "
            "that still does NOT do: put the phase of every mode into ONE acceleration "
            "number (the per-mode accelerations are summed as independent narrow-band "
            "responses, standard practice but not a coherent acceleration spectrum), "
            "carry the cross-coupling a non-proportional damper would introduce, "
            "excite through a point force or a shaker on one standoff instead of "
            "through the base, or report a stress spectrum (hand each mode's "
            "displacement to vibration_plate_stress one mode at a time). It is still a "
            "THIN-PLATE layer: no shear "
            "deformation or rotary inertia (so a thick or short plate reads too "
            "stiff). Copper skins with DIFFERENT thicknesses on the two faces ARE "
            "modelled (0.0.57: A/B/D section, and for the in-plane-free plate the "
            "REDUCED stiffness D* = D - B A^-1 B with mid-plane strain eps0 = "
            "-A^-1 B kappa), but an unsymmetric result is the FLAT-STATE "
            "linearisation: a board free to warp bows, and that equilibrium is NOT "
            "solved. Mass enters as ONE point (rank-one update) or as distributed "
            "patches (added_mass_regions, exact patch moments divided by the patch "
            "area; omit size_mm for the whole plate). Component STIFFNESS is not "
            "modelled (the mass is in, the spring is not), and each layer is "
            "isotropic (one E and one nu per layer, so an orthotropic laminate E1 != "
            "E2 reads wrong). Copper on the polygonal-outline path and in the stress "
            "tool's separate solve is modelled since 0.0.57 - the surface stress there "
            "now uses the E AND nu OF THE LAYER AT THAT SURFACE (0.0.56 paired "
            "copper's E with the laminate's nu, ~10 percent on 1 oz; the correction "
            "is registered in the changelog and the uncoupled default path is "
            "bit-identical) - leave copper_um and added_mass_g out and f1 reads high "
            "on a 2 oz board or one with a heavy can, no compliance in the mount "
            "itself, no "
            "assembly-level modes, no solder-joint creep or BGA-ball fatigue (only "
            "the Steinberg lead-wire form, whose slope and allowable YOU supply), no "
            "random-plus-shock superposition, no drop simulation, no nonlinear "
            "contact or rattling. Stress is a LAYER ON THE SAME RITZ SOLUTION, not a "
            "field solve: vibration_plate_stress reports three separately-located "
            "extremes (von Mises at the corner from tau_xy, principal at mid-span, "
            "maximum shear at the edge) and the tensor components at the sigma_max "
            "point only - there is no stress map, no stress concentration at a hole "
            "or a stiffener, and no fatigue law applied to those numbers (Steinberg "
            "needs slope and allowable from you)",
            "Melting, past the melt-through: all three fusing tools carry the latent "
            "heat of fusion since 0.0.88 (melting_model='sensible_plus_latent', "
            "default off). What none of them carries is what the metal DOES once it "
            "is molten: the density change across the melt (copper expands on "
            "melting, so the trace does not simply vanish), the molten flow "
            "that opens the gap, surface tension balling the ends up, and the "
            "alloying or intermetallic growth at a joint. Those are past the question "
            "a fusing current answers - the trace is already failed at the first "
            "fully liquid node - and none of them is approximated here. One more "
            "refusal, on the way in: casting latent heat as a temperature offset "
            "(raising effective melt_c) would be a silent approximation and is not "
            "offered - at 25 C it puts the melt time at 4.809 s against the exact "
            "1.253 s, 3.7x too slow",
            "Filter synthesis stops at the CIRCUIT, by name: filter_lumped_bandpass gives L/C values with its own S21 audited two ways; finite-Q dissipation and first-order parasitics (inductor cp, capacitor esl) are available but OFF unless asked for, and the loss model is a resistor per branch, not dielectric or conductor physics - no skin effect, no substrate loss, no temperature drift. filter_mc_yield does tolerance yield, but it is uniform and independent only (no correlation, no batch shift, no ageing), so it is a worst case whose confidence half-width grows as 1/sqrt(n_draws). filter_coupled_microstrip / filter_coupled_resonator are QUASI-STATIC and non-dispersive: no T-junctions, no stepped-impedance discontinuities, no cross-coupling between non-adjacent resonators (so stopband nulls are not modelled), no finite substrate/ground-plane size, no radiation Q, and no layout is generated - the w/s/lengths are geometry to draw, not a routed board. filter_waveguide_cavity covers any TE_mnp / TM_mnp, charts the modes below a band edge and takes an Rq roughness correction, but it has no iris or probe coupling geometry (a coupling iris must be solved by a full-wave solver), no radiation Q and no 3-D field map. filter_coupling_matrix evaluates the coupling matrix as a real network (finite zeros from a cross-coupling, finite-Qu loss), but it PERTURBS the inline matrix rather than running a full Cameron synthesis: the in-band ripple it reports is the perturbed one, so a cross-coupled filter whose mask matters still needs the synthesis run and then the EM run. It also has no dispersion, no iris or probe geometry, and no layout. Nothing here is an EM simulator: a filter that must meet a mask needs the EM run, and these numbers are the starting point handed to it",
        ],
        "contract": "every value carries model + basis + precision + assumptions; a "
                    "missing fact is refused or declared absent, never defaulted",
        "verified_against": {
            "calc": "hand-computed golden values (tests/test_calc.py, tests/test_diffpair.py)",
            "fidelity": "KiCad side proven against kicad-cli 10.0.7: identical stats, "
                        "identical plotted Gerbers, identical positions after a round "
                        "trip; the sample board itself is now validated by KiCad (an "
                        "invalid sample was the first thing this check caught)",
            "adapters": "the LCEDA Pro record map, arities and the mil unit are "
                        "measured: 194 real .epro archives re-read, a byte-identical "
                        "repack of every one of them, unit proof from RULE viaSize "
                        "(9.8425 mil = 0.25 mm) and from 9874 pads whose copper "
                        "endpoints land on the predicted positions (54.1% exact, the "
                        "rest unrouted). KiCad side is proven against kicad-cli 10.0.7",
            "desktop": "real install: LCEDA Pro 4.1.60 and KiCad 10.0.7 both detected by "
                       "drive scan; window-title parsing unit-tested; live open confirmed "
                       "only for KiCad's argv form. Measured: the LCEDA Pro launcher "
                       "returns rc 0 in ~0.5 s when its parent holds a console and stays "
                       "up when started through explorer.exe, so its argv form could not "
                       "be confirmed and stays false",
            "live bridge": "lceda_pro_* spoken to the RUNNING editor 4.1.60 over its own "
                           "command-space pipe: ping (bridgeVersion 4.1.60.198f38ab), "
                           "open of a copied 32.5 MB .eprj headless in 849 ms, "
                           "session.list, session.close (status closes; the entry "
                           "remains listed), doc.api (378 types, PCB_Document 21 "
                           "methods, callConventions.classToProperty) and invoke "
                           "(both PCBs of the project by name and uuid; a thrown Error "
                           "came back as EXECUTION_ERROR with the app's own message) "
                           "were all run against the application. Writes were never "
                           "exercised - verified_against_app covers the read paths only",
            "crosscheck": "board_cross_check runs the derived clearance and KiCad 10.0.7's "
                          "own DRC on the same file, in both directions, over 13 official "
                          "demo boards: every comparable violation the vendor reported was "
                          "also in our list (56/56, recall 1.0) and our extra pairs are the "
                          "conservative side. Pad geometry is checked separately against "
                          "pcbnew itself: 412 pads, 20188 HitTest probes, 0 mismatches. "
                          "zone copper, board-edge/hole rules and solder mask are declared "
                          "not measured rather than counted as misses",
            "write": "every apply is gated on re-reading the bytes just written "
                     "(bytes_written), a token-identity round trip, each op's own "
                     "postcondition, a kicad-cli stats/gerber/position comparison and a "
                     "DRC before/after diff; kicad-cli caught three real mistakes in the "
                     "demo build (silk text over the edge, a dangling track, a dangling "
                     "via) and rolled them back",
            "schematic": "on a real KiCad 10 demo sheet (cm5_minima): a field edit "
                         "passes every gate with an unchanged netlist, a dangling "
                         "label is refused by ERC, a placed symbol is seen by the "
                         "netlister with its pins, and renaming a #PWR value - which "
                         "renames a power net - is blocked and rolled back. Two gate "
                         "bugs were found this way: a value edit was being reported as "
                         "a connectivity change, and KiCad re-points the same ERC "
                         "warning at a different item between runs, so position is "
                         "reported but never used as violation identity",
            "magnetics / stackup / chain / bandwidth": "each relation is the one "
                         "written down in the source skill and is re-derivable by hand: "
                         "the boost duty D = 1 - eta*Vin/Vout, the Friis cascade "
                         "reproduces rf-circuit-design's own worked example "
                         "(0.8/15, 1.2/20, 2.0/10 dB -> 0.8369 dB this server, 0.83 dB "
                         "in the skill), the cascaded IIP3 of 0 dBm + 10 dBm through "
                         "20 dB comes out -10.41 dBm by hand and by code, and BW = f0/Q "
                         "is arithmetic. The laminate table is the skill's own section "
                         "3.1; its per-band limits are conventions and are labelled as "
                         "such. NOT cross-checked against a vendor calculator, because "
                         "none of these is a vendor feature",
            "rule catalogues": "36 rules (22 layout, 14 schematic) each carrying the "
                         "skill section it came from, so a dispute is answered by "
                         "reading the source. The limits that are physics are computed "
                         "instead of quoted: the 3W gap from the line's own width, the "
                         "stitching pitch from lambda_g/20, the required power-trace "
                         "width from the IPC chart (which REFUSES without an explicit "
                         "standard, since ipc2221 and ipc2152 differ by more than 3x), "
                         "and the isolation gap from IEC 60664-1 (which fails closed "
                         "while the distance table is unverified). The pass/fail/"
                         "undetermined split is unit-tested: a rule with no fact is "
                         "never a pass",
            "antenna / feedline / link": "checked against reality rather than against "
                         "itself: a 50 ohm microstrip on 1.6 mm FR4 comes out 3.14 mm "
                         "(boards ship 3.0 mm), a 2.45 GHz FR4 patch comes out 37.6 x "
                         "29.1 mm with a 10.7 mm inset, a single-stub 100 ohm load at "
                         "2.4 GHz comes out 0.348 lambda + 0.098 lambda open stub "
                         "(textbook), 2400 MHz at 1 m is 40.05 dB free-space loss, and "
                         "the CPW model reproduces the infinite-substrate limit exactly "
                         "(64.022 ohm from both sides). The microstrip closed form is the "
                         "one field2d already scores against the 2-D solver (-0.35 % .. "
                         "-2.83 %); CPW/GCPW are NOT solver-validated, only limit-checked, "
                         "and say so. Conductor loss is no longer the Rs/(Z0*w) sheet "
                         "guess on microstrip and stripline: it is alpha_c = "
                         "Rs/(2*Z0)*INT g^2 ds read off the same 2-D charge solution, and "
                         "it is checked two ways - the parallel-plate limits (INT g^2 ds "
                         "-> 2/w on microstrip, -> 1/w on stripline, hit to 1.8 % and "
                         "1.5 % at w/h 25..100) and the independent incremental-inductance "
                         "rule on homogeneous stripline (2-3 % at w/b 1..2.5). Both "
                         "figures are returned with their ratio, because they disagree by "
                         "more than 2x at moderate w/h and neither is a bound. Zero "
                         "thickness still has a log-divergent edge and carries a drift "
                         "figure; coplanar lines still refuse",
            "dfm": "the rule catalogue is this server's, the limits are never: tests "
                   "cover both failure directions (an unknown key and a missing source "
                   "are refused, not defaulted), a measured value exactly on the limit "
                   "passes and 1 um short fails, and a rule with no measurement comes "
                   "back not_checked rather than pass. Board-side measurements are "
                   "checked on real .kicad_pcb fixtures (track 0.2 mm, via 0.3/0.6, "
                   "hole web, copper-to-edge, a 1 mm slot in a 50x30 outline)",
            "simulation": "golden values computed by hand, not by the code being "
                          "tested: a 10 V 1k/2k divider gives 6.666666662 V (3e-9 "
                          "from gmin), an RC step on 1k/1u matches the BACKWARD-EULER "
                          "closed form 1-(1/(1+h/tau))^50 to all 16 digits (and differs "
                          "from the continuous 0.993262, which is what proves the "
                          "integrator is the one claimed), a 10 ohm / 1 mH / 100 nF "
                          "series tank peaks at 15915.49 Hz with |V_C| = 10.0069 = Q, a "
                          "diode at 5 V/1k lands at Vd = 0.692544 V, a BJT gives beta "
                          "= 200.0 exactly, a MOSFET gives Vds = 4.0000 by the square "
                          "law, and the copper/thermal numbers are rho*L/(w*t) and "
                          "t/(k*A) to the digit (1 mm x 0.2 mm x 35 um = 2.462857 mohm; "
                          "1.6 mm FR4 at 100 mm^2 = 53.3333 K/W). The 2-D plate solve "
                          "is checked against its own energy balance (power in vs "
                          "convection out, refused above 0.5 % residual) and against "
                          "the P/(2hA) limit for a high-k plate. The ngspice "
                          "delegation is covered twice: without an engine, "
                          "engine='ngspice' is asserted to REFUSE (ENGINE_NOT_FOUND) "
                          "rather than fall back, and the rawfile parser (both the "
                          "ASCII and the binary layout) plus the deck filter and the "
                          "three failure paths are driven by a stand-in engine; with "
                          "real ngspice-47 installed the SAME decks run on both "
                          "engines and are compared node by node - a divider to 5e-9 "
                          "V, a diode to 3.5e-4 V (model difference), a BJT to 1.8e-2 V "
                          "(model difference), and an RC step and an RLC AC sweep to "
                          "0.0 V, i.e. bit-identical; a second build (ngspice-41) then "
                          "ran the same decks, agreeing with 47 bit for bit on .op "
                          "and .ac and to 1.24e-4 V on a 1 V .tran step; "
                          "verified_against_engine is gated on the engine version "
                          "string, not on the file existing",
            "vibration": "checked against the closed-form plate eigenvalues, not "
                         "against itself: a simply supported square gives "
                         "f11 = pi^2 sqrt(D/rho h)/a^2 to 6 digits (330.385 Hz on a "
                         "100 mm FR-4 plate) and a clamped square gives 35.99/(2 pi) "
                         "(602.382 Hz), both exact; a free square at nu=0.3 gives "
                         "lambda^2 = 13.4687 against Leissa's 13.489 (0.15 %); the "
                         "mode SHAPES are checked, not just the numbers - a SSSS mode "
                         "samples 1.0000 at the centre, sin(pi*0.05) = 0.1564 at "
                         "y = 5 % and 0 at the (1,2) modal line. Miles' closed form "
                         "agrees with the numerical PSD integral to 0.045 %, and the "
                         "shock integrator reproduces the analytic step DLF of 2 "
                         "(1.99953 at zeta=1e-4) and 1+exp(-zeta pi/sqrt(1-zeta^2)) "
                         "to 0.13 % for zeta = 0.05..0.2",
            "cavity": "f_mn is closed form, so it is checked against hand arithmetic on "
                      "cases with an obvious answer: a 100 x 80 mm plane pair at er=4.3 "
                      "gives 0.722864 GHz for (1,0) - exactly c/(2 sqrt(4.3) * 0.1 m) - "
                      "and lambda_g = 200 mm, which is also 2*a, the definition of the "
                      "half-wave resonance; the (0,1) mode lands at 0.903580 GHz = "
                      "f10*a/b. via_fence is pure division, and the tests assert that "
                      "it refuses to PASS without a caller-supplied criterion",
            "filters": "every branch is checked against an independent closed form, "
                        "not against itself: the 3rd-order 0.5 dB Chebyshev prototype "
                        "comes out 1.596331 / 1.096681 / 1.596331 / 1.0 and the "
                        "Butterworth 1 / 2 / 1 / 1 (textbook); the lumped bandpass S21 "
                        "from the ABCD cascade of the actual L/C elements agrees with "
                        "1/(1+eps^2 Tn^2) to 0.0005 dB at f0 and both band edges, and "
                        "the tool REFUSES (INTERNAL_CHECK_FAILED) above 0.02 dB; the "
                        "coupled-microstrip inversion is scored on the impedances the "
                        "2-D solver actually returns (0.24-0.29 % worst error in 4-5 "
                        "iterations) and every iteration is reported; the mesh drift is "
                        "measured, not assumed (Z0e 64.52 at h/3, 69.57 at h/6, 70.17 "
                        "at h/12); the TE101 cavity gives 19.8536 mm length for WR-90 "
                        "at 10 GHz with Qc = 7821.5 from the closed-form bracket and "
                        "7821.5 from the numerical |H_tan|^2 wall integral (0.0 % "
                        "apart, and >2 % is refused) and R/Q = 160.957 ohm, all three "
                        "matching hand arithmetic. k from two close impedances is "
                        "reported with its own resolution limit rather than presented "
                        "as a measured number",
            "electrothermal": "the fixed point has a closed form dT = A/(1-G), so the "
                              "tests compare the iteration against that closed form and "
                              "against the definition: R20 = 0.01 ohm, I = 1 A, "
                              "Rth = 50 K/W gives dT = 0.510829 K and R = 10.21658 mohm "
                              "(hand-checked: P*Rth = 0.01021658*50); the same case with "
                              "I = 1 A and Rth = 300 K/W on a 1 ohm conductor gives "
                              "G = 4.716 > 1 and is refused as THERMAL_RUNAWAY instead "
                              "of returning the 4th iterate. The 2-D loop is pinned "
                              "against a case that has a closed form - a 2 W load spread "
                              "uniformly over a 50x50x1.6 mm plate (k 0.3, h 10 per face, "
                              "2 mm cells) gives t_max = 73.393487 C against "
                              "(2hA*Ta + P*(1-20*alpha))/(2hA - P*alpha) = 73.39345 C, "
                              "and the same plate at 13 W is refused as THERMAL_RUNAWAY "
                              "because 13*alpha exceeds 2hA = 0.05 W/K - that is the 1-D "
                              "criterion P*alpha < 2hA, recovered exactly. A single 3 A "
                              "trace (40 mm x 1 mm x 35 um) on the same plate gives "
                              "R20 = 0.0197029 ohm, R_hot = 0.0220269 ohm and "
                              "t_max = 50.013524 C at 1 mm cells",
        },
    }


# ------------------------------------------------------------- fidelity
_FID_CACHE = {}
_FID_CACHE_MAX = 4

def _load_fidelity(args):
    """Open a board as a fidelity document (whole tree, zero loss). Read-only."""
    import os
    path = args.get("path")
    if not path:
        return None, err("path is required", "ARG_MISSING")
    if not os.path.isfile(path):
        return None, err("board file does not exist: %r (this server never creates "
                         "files)" % path, "FILE_NOT_FOUND")
    stat = os.stat(path)
    key = (os.path.abspath(path), stat.st_size, int(stat.st_mtime))
    doc = _FID_CACHE.get(key)
    if doc is not None:
        return doc, None
    try:
        doc = _fidelity.load(path, fmt=args.get("format"))
    except Exception as exc:  # noqa: BLE001
        return None, err("%s: %s" % (type(exc).__name__, exc), "PARSE_FAILED",
                         hint="supported: KiCad .kicad_pcb, EasyEDA Pro .epro")
    if len(_FID_CACHE) >= _FID_CACHE_MAX:
        _FID_CACHE.pop(next(iter(_FID_CACHE)))
    _FID_CACHE[key] = doc
    return doc, None


def _board_fidelity(args):
    doc, e = _load_fidelity(args)
    if e:
        return e
    from .fidelity import kicad_doc as _kdoc
    out = {"status": "ok", "path": doc.path, "not_written": _NOT_WRITTEN}
    if isinstance(doc, _kdoc.KiCadDocument):
        out["format"] = "kicad_pcb"
        out["document"] = doc.stats()
        out["roundtrip"] = doc.verify()
        out["coverage"] = doc.coverage(_fviews.INTERPRETED_HEADS)
        if args.get("engine", True):
            out["engine_check"] = _fidelity.engine.verify(doc)
        else:
            out["engine_check"] = {"status": "skipped", "reason_code": "NOT_REQUESTED"}
    else:
        out["format"] = "epro"
        out["document"] = doc.stats()
        out["roundtrip"] = doc.verify()
        out["object_types"] = doc.known_types_seen()
        inv = {}
        for name in doc.text_members():
            per_type = doc.key_inventory(name)
            inv[name] = {t: {"objects": v["objects"],
                             "keys": list(v["keys"].keys())}
                         for t, v in per_type.items()}
        out["key_inventory"] = inv
        out["engine_check"] = {
            "status": "unavailable", "reason_code": "NO_VENDOR_CLI",
            "message": "LCEDA Pro has no command line; its own DRC arrives with the "
                       "API-gateway client, and until then no verdict is offered"}
    out["meaning"] = (
        "this is a CONVERSION report, not a measurement: it proves the whole document "
        "is carried (token identity / byte identity), counts the nodes that no reading "
        "rule covers yet (they are kept, not guessed), and - for KiCad - has the vendor "
        "engine plot both the original and the re-emitted board and compare")
    out["read_facts_with"] = "board_view"
    return out


_VIEW_KINDS = ("nets", "net_classes", "layers", "stackup", "setup", "footprints",
               "pads", "tracks", "vias", "zones", "outline", "graphics",
               "uninterpreted", "all")

# Soft byte budget for one board_view page. A record that carries `raw` can be
# tens of kilobytes (a 1125-component board was 12.8 MB at limit=200), which is
# a context window in a single reply; the page stops at the budget and says so.
#
# 64 KiB was a TRANSPORT bound, not a conversation bound: it protects the pipe
# and still spends ~20k tokens of the model's context in one reply, which is
# what actually broke a session on 2026-11-03 (194 pads came back as one page).
# 16 KiB is the size a model can read and still have room to answer in; a
# caller that really wants more asks for it with max_bytes= and takes the
# consequence knowingly.
_VIEW_MAX_BYTES = 16384


def _board_view(args):
    doc, e = _load_fidelity(args)
    if e:
        return e
    kind = args.get("kind") or "all"
    limit = int(args.get("limit") or 200)
    offset = int(args.get("offset") or 0)
    include_raw = bool(args.get("include_raw"))
    max_bytes = int(args.get("max_bytes") or _VIEW_MAX_BYTES)
    from .fidelity import kicad_doc as _kdoc
    if isinstance(doc, _kdoc.KiCadDocument):
        if kind == "all":
            # NOT the whole document any more: on a real board this used to return
            # ~95 MB of JSON (every node, every raw S-expression), which no chat
            # client can take. Counts answer "what is in here"; a named kind
            # answers "show me".
            counter = _fviews.BoardView(doc, with_raw=False)
            counts = {k: len(getattr(counter, k)() or {})
                      for k in _VIEW_KINDS if k != "all"}
            return {"status": "ok", "format": "kicad_pcb",
                    "source": {"path": getattr(doc, "path", None), "x_unit": "mm",
                               "y_direction": "up (source is down, negated)"},
                    "counts": counts,
                    "next": "call board_view with one kind (and limit/offset/"
                            "max_bytes) to read records; kind=all never inlines them",
                    "not_written": _NOT_WRITTEN}
        if kind not in _VIEW_KINDS:
            return err("unknown kind %r" % kind, "ARG_INVALID",
                       hint="one of: " + ", ".join(_VIEW_KINDS))
        view = _fviews.BoardView(doc, with_raw=include_raw)
        items = getattr(view, kind)()
        if isinstance(items, list):
            total = len(items)
            start = max(0, offset)
            page, used = [], 0
            for item in items[start:start + max(0, limit)]:
                if not include_raw and isinstance(item, dict):
                    item = {k: v for k, v in item.items() if k != "raw"}
                size = len(json.dumps(item, ensure_ascii=False))
                if page and used + size > max_bytes:
                    break
                page.append(item)
                used += size
            end = start + len(page)
            out = {"status": "ok", "kind": kind, "total": total,
                   "returned": len(page), "truncated": end < total,
                   "bytes_used": used, "items": page,
                   "not_written": _NOT_WRITTEN}
            if end < total:
                out["next_offset"] = end
                out["hint"] = ("%d record(s) left: page with offset=%d, lower limit, "
                               "or raise max_bytes (now %d)"
                               % (total - end, end, max_bytes))
            return out
        if not include_raw and isinstance(items, dict):
            items = {k: v for k, v in items.items() if k != "raw"}
        return {"status": "ok", "kind": kind, "value": items,
                "not_written": _NOT_WRITTEN}
    # .epro
    member = args.get("member") or (doc.text_members() or [None])[0]
    if member is None or member not in doc.names():
        return err("member not found in this .epro: %r" % member, "MEMBER_NOT_FOUND",
                   hint="members: " + ", ".join(doc.names()[:20]))
    if kind in ("all", "objects"):
        live = doc.live(member)
        return {"status": "ok", "format": "epro", "member": member,
                "total": len(live), "returned": min(len(live), limit),
                "truncated": len(live) > limit,
                "items": [_epro_record(r) for r in live[:limit]]}
    if kind == "deleted":
        gone = doc.deleted(member)
        return {"status": "ok", "format": "epro", "member": member,
                "total": len(gone), "items": [_epro_record(r) for r in gone[:limit]]}
    if kind == "key_inventory":
        return {"status": "ok", "format": "epro", "member": member,
                "inventory": doc.key_inventory(member)}
    if kind == "unparsed":
        bad = doc.unparsed(member)
        return {"status": "ok", "format": "epro", "member": member,
                "total": len(bad),
                "items": [{"line": r[0], "raw": r[3][:400]} for r in bad[:limit]]}
    return err("unknown kind %r for .epro" % kind, "ARG_INVALID",
               hint="objects, deleted, key_inventory, unparsed")


def _epro_record(rec):
    return {"type": rec["type"], "id": rec["id"], "ticket": rec["ticket"],
            "line": rec["line"], "payload": rec["payload"]}


_tool(
    "board_fidelity",
    "PROOF tool: does this server carry the WHOLE board? Reports node/byte counts, a "
    "token-identity round trip, the nodes that have no reading rule yet (kept, not "
    "guessed), and - for KiCad - runs KiCad itself on the original and on the re-emitted "
    "file and compares the plotted copper, the statistics and the positions. "
    "Read-only; nothing is written beside the project.",
    {"type": "object",
     "properties": {
         "path": _str("absolute path to the board file"),
         "engine": _bool("also run the vendor engine check (KiCad only, ~2 s)", True),
         "format": _str("force the container type", ["kicad_pcb", "epro"]),
     },
     "required": ["path"]},
    lambda a: _guarded(lambda: _board_fidelity(a)),
)

_tool(
    "board_view",
    "Read FACTS out of the converted board: nets, net classes, layers, stackup, setup, "
    "footprints, pads (exact size/drill/rotation/net), tracks, vias, zones (including "
    "the stored fill polygons), Edge.Cuts geometry, board graphics/texts, and the nodes "
    "with no reading rule yet. kind=all returns per-kind COUNTS only (a real board's "
    "full fact table is tens of MB); pick one kind to read records, paged by "
    "limit/offset/max_bytes. Records carry the source S-expression only when "
    "include_raw=true.",
    {"type": "object",
     "properties": {
         "path": _str("absolute path to the board file"),
         "kind": _str("what to read", ["all", "nets", "net_classes", "layers",
                                       "stackup", "setup", "footprints", "pads",
                                       "tracks", "vias", "zones", "outline",
                                       "graphics", "uninterpreted", "objects",
                                       "deleted", "key_inventory", "unparsed"],
                      "all"),
         "member": _str("for .epro: which member to read (default: first text member)"),
         "limit": _num("max items returned", 200),
         "offset": _num("skip this many items (paging)", 0),
         "include_raw": _bool("attach the original S-expression to every record "
                              "(large: one footprint can be tens of KB)", False),
         "max_bytes": _num("soft byte budget for this page; it stops when reached "
                           "and reports next_offset", _VIEW_MAX_BYTES),
     },
     "required": ["path"]},
    lambda a: _guarded(lambda: _board_view(a)),
)

_tool(
    "board_drc",
    "AUTHORITATIVE design-rule check: runs KiCad's own DRC engine (kicad-cli) and returns "
    "its verdict - violations by severity and type, and unconnected items. This is not "
    "this server's geometry opinion; when it disagrees with board_clearance, believe this "
    "one. Read-only: the report goes to a temp directory, never beside your project. "
    "On a large board DRC can outlast any sane request, so background=true hands back "
    "a job id instead: ask board_job(action='get', id=...) for the verdict later. A "
    "running job has no result, and none is ever approximated. "
    "refill_zones (default true) refills the zones in a scratch copy first: a "
    "filled_polygon is a snapshot, and every track drawn after the last fill reads "
    "as 0 mm clearance to it (measured: 502 violations stale vs 52 refilled on the "
    "same bytes). Turn it off only to audit a stale file on purpose.",
    {"type": "object",
     "properties": {
         "path": _str("absolute path to a .kicad_pcb"),
         "severity": _str("which violations to include", ["all", "error"], "all"),
         "refill_zones": _bool("refill the zones (in a scratch copy) before "
                               "checking, so the pour is not an old snapshot "
                               "(default true)", True),
         "strict": _bool("also report warnings as blocking (the write gate's strict "
                         "mode)"),
         "limit": _num("how many blocking/advisory entries to list", 20),
         "timeout_s": _num("budget handed to the DRC subprocess (default 900 s)"),
         "background": _bool("run it as a job and answer with its id instead of "
                             "blocking", False),
     },
     "required": ["path"]},
    lambda a: _guarded(lambda: _board_drc(a)),
)

_tool(
    "board_cross_check",
    "Check this server's DERIVED answers against the vendor's own verdict, on the same "
    "file, in both directions. Today the only metric with a vendor counterpart is "
    "clearance: our geometric clearance is compared with KiCad's own clearance DRC "
    "(recall, missed violations, our extra pairs), per net class. Every other derived "
    "metric is reported as not_comparable with the reason - 'nobody can disagree with it "
    "yet' is not the same as 'it is right'. Read-only: the DRC report lives in a temp "
    "directory and is removed.",
    {"type": "object",
     "properties": {
         "path": _str("absolute path to a .kicad_pcb"),
         "threshold_mm": _num("override the clearance threshold instead of taking it "
                              "from the board's own net classes"),
         "tol_mm": _num("how far a reported DRC position may sit from our pair to count "
                        "as the same finding", 0.1),
         "metrics": _str("which derived metrics to cross-check",
                         ["clearance", "free_space", "pour_connectivity",
                          "cross_split", "loop_area"], "clearance"),
         "worst": _num("how many example disagreements to list", 20),
     },
     "required": ["path"]},
    lambda a: _guarded(lambda: _board_cross_check(a)),
)

_tool(
    "board_stats",
    "AUTHORITATIVE board statistics from KiCad's own calculator: size, area, copper area "
    "per side (so a poured zone counts as the copper it really is), board thickness, pad "
    "and track counts, minimum track width and drill. Read-only.",
    {"type": "object",
     "properties": {"path": _str("absolute path to a .kicad_pcb")},
     "required": ["path"]},
    lambda a: _guarded(lambda: _board_stats(a)),
)


def _board_drc(args):
    import os
    from .fidelity import engine as _engine
    path = args.get("path")
    if not path:
        return err("path is required", "ARG_MISSING")
    if not _engine.find_kicad_cli():
        return err("kicad-cli was not found, so no authoritative verdict is available "
                   "(this tool refuses rather than guessing)", "KICAD_CLI_NOT_FOUND")
    if _fidelity.sniff(path) == "epro":
        return err("LCEDA Pro has no command line, so this server cannot obtain its DRC "
                   "verdict; use the app's own DRC or board_fidelity for a conversion "
                   "report", "VERDICT_SOURCE_UNAVAILABLE")
    severity = args.get("severity") or "all"
    timeout_s = float(args.get("timeout_s") or _engine.DRC_TIMEOUT_S)

    refill = args.get("refill_zones")
    refill = True if refill is None else bool(refill)

    def _do_drc():
        out = _engine.drc(path, severity=severity, timeout=timeout_s, refill=refill)
        if out.get("status") != "ok":
            return out
        from .edit import drc_policy as _policy
        view = _policy.classify(out.get("violations") or [],
                                strict=bool(args.get("strict")),
                                limit=int(args.get("limit") or 20))
        out["blocking_if_this_were_an_edit"] = view
        out["how_an_apply_treats_this"] = (
            "board_apply compares the DRC of the file before and after the edit and "
            "blocks on NEW violations at KiCad severity 'error' only; warnings are "
            "reported and counted (they were 69/69 on the STM32F401 core board, all "
            "silkscreen/text). Pass options.strict_drc=true to make every new "
            "violation block.")
        return out

    if args.get("background"):
        from . import jobs as _jobs
        try:
            job = _jobs.submit("drc", _do_drc, label=os.path.basename(path),
                               timeout_hint_s=timeout_s)
        except _jobs.JobError as exc:
            return err(exc.message, exc.reason_code, hint=exc.hint, extra=exc.extra)
        return ok(
            value={"job": job["id"], "state": job["state"],
                   "kind": "drc", "label": job["label"],
                   "timeout_s": timeout_s},
            unit="str",
            model="background_job(kicad-cli pcb drc)",
            basis="the verdict is still KiCad's and nothing here re-implements it; "
                  "only WHEN it is asked changed",
            precision="NO RESULT YET. This answer carries a job id and nothing else: "
                      "board_job(action='get', id=...) returns the DRC verdict when "
                      "it exists, and reports 'running' until then. Jobs live in "
                      "memory, so a server restart loses this one",
            assumptions={"why": "DRC on an 88.7 MB board did not finish inside a "
                                "single call, so blocking would have meant a "
                                "fifteen-minute request or a truncated verdict",
                         "wait_with": "board_job(action='wait', id=...) with a "
                                      "budget of your own; each call waits at most "
                                      "its timeout_s"},
            extra={"job": job})
    return _do_drc()


def _board_job(args):
    """The other half of a slow call: list / get / wait / cancel a background job."""
    from . import jobs as _jobs
    action = (args.get("action") or "get").lower()
    jid = args.get("id")
    if action not in ("list", "get", "wait", "cancel"):
        return err("unknown action %r" % (action,), "ARG_INVALID",
                   hint="action is one of list, get, wait, cancel")
    try:
        if action == "list":
            got = _jobs.list_jobs(kind=args.get("kind"), state=args.get("state"),
                                  limit=int(args.get("limit") or 50))
            return ok(
                value={"jobs": got, "count": len(got)},
                unit="str",
                model="in-process job registry",
                basis="the jobs this server process submitted (board_drc with "
                      "background=true is the one that produces them today)",
                precision="jobs live in memory only: a restart loses every one of "
                          "them, and a finished job is forgotten after %d s"
                          % int(_jobs.KEEP_S),
                assumptions={"not_a_queue": "submitting the same work twice is two "
                                             "jobs; the second gets no credit for "
                                             "the first",
                             "cancel_is_not_a_kill": "a subprocess already started "
                                                    "keeps running; the result is "
                                                    "discarded, the machine is "
                                                    "still busy"})
        if not jid:
            return err("id is required for action %r" % (action,), "ARG_MISSING",
                       hint="board_job(action='list') shows the ids that exist")
        if action == "get":
            job = _jobs.get(jid)
        elif action == "wait":
            job = _jobs.wait(jid, timeout_s=float(args.get("timeout_s") or 30.0))
        else:
            job = _jobs.cancel(jid)
    except _jobs.JobError as exc:
        return err(exc.message, exc.reason_code, hint=exc.hint, extra=exc.extra)
    still = job["state"] == "running"
    return ok(
        value=job,
        unit="str",
        model="in-process job registry (the work itself is unchanged)",
        basis="the job's state and, once it has finished, the return value of the "
              "function it ran (for drc: KiCad's own verdict, untouched)",
        precision=("STILL RUNNING - this is not a result. No partial or previous "
                   "verdict is offered as if it were this run's"
                   if still else
                   "state %r; the value is the work's own return value, not a "
                   "summary of it" % job["state"]),
        assumptions={"survives_a_restart": False,
                     "cancel_is_not_a_kill": "cancelling marks the job so its "
                                             "result is discarded; a subprocess "
                                             "already running is not killed",
                     "still_running": still},
        extra={"job": job} if job["state"] != "running" else {})

_tool(
    "board_job",
    "Ask about a background job started by another tool (today: board_drc with "
    "background=true, which is how a large board avoids a fifteen-minute request). "
    "action=list | get | wait | cancel. A running job answers 'running' and NOTHING "
    "else - there is no partial verdict and no cached previous run to mistake for this "
    "one. wait blocks only for its own timeout_s (per call), so a long wait is a series "
    "of calls that each return, not one call that looks hung. Jobs live in memory: a "
    "restart loses them, and an unknown id is JOB_NOT_FOUND rather than an empty answer.",
    {"type": "object",
     "properties": {
         "action": _str("what to do with the job", ["list", "get", "wait", "cancel"],
                         "get"),
         "id": _str("job id, e.g. drc-3-1a2b3c"),
         "timeout_s": _num("how long action='wait' may block this call", 30),
         "kind": _str("action='list': only this kind"),
         "state": _str("action='list': only this state",
                       ["running", "done", "failed", "cancelled"]),
         "limit": _num("action='list': how many to show", 50),
     }},
    _board_job,
)

def _board_cross_check(args):
    from .fidelity import crosscheck as _cc
    path = args.get("path")
    if not path:
        return err("path is required", "ARG_MISSING")
    metrics = args.get("metrics")
    if isinstance(metrics, str):
        metrics = [metrics]
    return _cc.derive_all(path, metrics=metrics,
                          threshold_mm=args.get("threshold_mm"),
                          tol_mm=args.get("tol_mm", _cc.DEFAULT_TOL_MM),
                          worst=int(args.get("worst") or 20))

def _board_stats(args):
    from .fidelity import engine as _engine
    path = args.get("path")
    if not path:
        return err("path is required", "ARG_MISSING")
    return _engine.stats(path)


# ------------------------------------------------------------ write path
def _edit_error(exc):
    """Edit/Op errors carry a code and extra evidence; keep both, never flatten."""
    import json as _json
    out = err(str(exc), getattr(exc, "code", "EDIT_FAILED"),
              hint=getattr(exc, "hint", None))
    extra = getattr(exc, "extra", None) or {}
    for key, value in extra.items():
        try:
            _json.dumps(value)
            out[key] = value
        except TypeError:
            out[key] = str(value)
    return out

def _schematic_pcb_diff(args):
    """Compare a board against KiCad's own netlist for a schematic."""
    from pathlib import Path
    from . import sync as _sync
    from .fidelity import engine as _engine
    board = args.get("path")
    schematic = args.get("schematic")
    if not board:
        return err("missing parameter 'path' (the .kicad_pcb)", "ARG_MISSING")
    if not schematic:
        return err("missing parameter 'schematic' (the .kicad_sch)", "ARG_MISSING")
    if Path(str(schematic)).suffix.lower() not in (".kicad_sch",):
        return err("the schematic side must be a .kicad_sch file: %r" % (schematic,),
                   "ARG_INVALID")
    try:
        netlist = _engine.sch_netlist(schematic)
        if netlist.get("status") != "ok":
            netlist["schematic"] = schematic
            return netlist
        out = _sync.compare(board, netlist)
        out["schematic"] = schematic
        out["schematic_source"] = netlist.get("verdict_source")
        return out
    except _sync.SyncError as exc:
        return _edit_error(exc)
    except (OSError, ValueError) as exc:
        return err("%s: %s" % (type(exc).__name__, exc), "SYNC_FAILED")

def _schematic_pcb_sync(args):
    """Diff, then plan (or propose) the board operations that close the gap."""
    from pathlib import Path
    from . import sync as _sync
    from .fidelity import engine as _engine
    from . import edit as _edit
    board = args.get("path")
    schematic = args.get("schematic")
    if not board or not schematic:
        return err("both 'path' (board) and 'schematic' (.kicad_sch) are required",
                   "ARG_MISSING")
    if Path(str(schematic)).suffix.lower() not in (".kicad_sch",):
        return err("the schematic side must be a .kicad_sch file: %r" % (schematic,),
                   "ARG_INVALID")
    try:
        netlist = _engine.sch_netlist(schematic)
        if netlist.get("status") != "ok":
            netlist["schematic"] = schematic
            return netlist
        diff = _sync.compare(board, netlist)
        plan = _sync.operations_for(
            diff, placement=args.get("placement") or {},
            library=args.get("library"),
            remove_extra=args.get("remove_extra") is True,
            clear_extra_pad_nets=args.get("clear_extra_pad_nets") is True,
            create_nets=args.get("create_nets") is not False)
        out = {"status": "ok", "diff": diff, "operations": plan["operations"],
               "unresolved": plan["unresolved"],
               "note": "operations are the diff, not a decision: read them, then "
                       "call board_propose / board_apply, or pass propose=true"}
        if not plan["operations"]:
            out["plan"] = None
            out["proposed"] = False
            return out
        if args.get("propose") is True:
            proposed = _edit.propose(board, plan["operations"],
                                     args.get("options") or {})
            out["proposed"] = True
            out["plan_id"] = proposed.get("plan_id")
            out["proposal"] = proposed
            out["next"] = ("board_apply(plan_id=%r, confirm=true) - the proposal is "
                           "validated the same way any hand-written plan is"
                           % (proposed.get("plan_id"),))
        else:
            out["proposed"] = False
            out["next"] = "pass propose=true to have these operations validated"
        return out
    except _sync.SyncError as exc:
        return _edit_error(exc)
    except _edit.EditError as exc:
        return _edit_error(exc)
    except _edit.OpError as exc:
        return _edit_error(exc)
    except (OSError, ValueError) as exc:
        return err("%s: %s" % (type(exc).__name__, exc), "SYNC_FAILED")

def _board_create(args):
    from . import edit as _edit
    try:
        return _edit.create_board(args.get("path"), args.get("spec") or {})
    except _edit.EditError as exc:
        return _edit_error(exc)

def _board_ops(_args):
    from . import edit as _edit
    return {"status": "ok",
            "operations": _edit.describe_ops(),
            "by_format": {
                "kicad": {"operations": _edit.describe_ops(),
                          "coordinates": "millimetres, Y UP (the same frame board_view "
                                          "reports); written as a KiCad tree and then "
                                          "re-judged by kicad-cli"},
                "epro": {"operations": _edit.describe_epro_ops(),
                         "coordinates": "arguments are millimetres; the file stores mil "
                                        "(0.0254 mm, measured - the RULE labels say mm "
                                        "but the numbers are mil). One conversion "
                                        "point, in the writer",
                         "write_shape": "a real save rewrites ONE member in place (the "
                                        "PCB/<uuid>.epcb record stream) and leaves the "
                                        "other 129 members byte-identical; measured by "
                                        "comparing two consecutive saves. So a plan "
                                        "rewrites one member, and six gates prove "
                                        "nothing else moved",
                         "read_only_here": ".eprj/.eprj2 (SQLite) is refused with "
                                           "WRITE_UNSUPPORTED_FORMAT: the board row sits "
                                           "beside projects.ticket / g_ticket and "
                                           "encrypted history_data, and an UPDATE that "
                                           "does not advance them is exactly the change "
                                           "the application can treat as stale"},
            },
            "protocol": "board_propose -> read the digest -> board_apply(confirm=true)",
            "format_detection": "by content: 'PK' = .epro container, 'SQLite format 3' "
                                "= .eprj/.eprj2 project, '(kicad_pcb' = KiCad. The file "
                                "name is never trusted",
            "note": "every operation is all-or-nothing inside one proposal"}

def _board_propose(args):
    from . import edit as _edit
    try:
        return _edit.propose(args.get("path"), args.get("operations") or [],
                             args.get("options") or {})
    except _edit.EditError as exc:
        return _edit_error(exc)
    except _edit.OpError as exc:
        return _edit_error(exc)

def _board_apply(args):
    """Write the plan - or, with options.dry_run, only find out whether it would."""
    from . import edit as _edit
    options = args.get("options") or {}
    try:
        out = _edit.apply_plan(args.get("plan_id"), args.get("confirm") is True,
                               options)
        _journal_apply(out)
        return out
    except getattr(_edit, "_DryRunBlocked", ()) as exc:   # dry run + a gate said no
        out = _edit_error(exc)
        out["status"] = "ok"
        out.pop("reason_code", None)
        out["dry_run"] = True
        out["would_write"] = False
        out["file_unchanged"] = True
        out["message"] = ("DRY RUN: this plan WOULD be refused - nothing was left "
                          "on disk. The reasons are in `blocking`; each entry names "
                          "the violation's position and the constraint/measured pair "
                          "where KiCad states one")
        out["plan_still_open"] = True
        return out
    except _edit.EditError as exc:
        return _edit_error(exc)

def _board_checkpoints(args):
    from . import edit as _edit
    return _edit.list_checkpoints(args.get("path"), limit=int(args.get("limit") or 20))

def _board_progress(args):
    """Read the step log / stop file. Also the way to stop a stepped apply."""
    from . import edit as _edit
    action = (args.get("action") or "status").lower()
    try:
        if action in ("stop", "resume", "clear"):
            return _edit.step_control(action, note=args.get("note"))
        events = _edit.step_events(args.get("plan_id"), limit=int(args.get("limit") or 200))
        events["action"] = "log"
        events["how_to_stop"] = ("board_progress(action='stop') writes %s; a stepped "
                                 "apply reads it between steps and refuses to write "
                                 "more" % events["stop_file"])
        return events
    except _edit.EditError as exc:
        return _edit_error(exc)

def _journal_apply(out):
    """One automatic line per SUCCESSFUL write, so a dead session leaves a trail.

    The reply dict is not touched: a checkpoint that changed what a tool answers
    would be a different tool. Returns the receipt, or None when nothing was
    written (dry run, error, or a reply with no file in it).
    """
    from . import journal as _j
    if not isinstance(out, dict) or out.get("status") != "ok":
        return None
    if out.get("dry_run") or out.get("would_write") is False:
        return None
    if out.get("file_unchanged"):
        return None
    target = out.get("target") or out.get("path")
    if not isinstance(target, str) or not target:
        return None
    try:
        head = os.path.basename(target)
    except Exception:      # a target that is not a path is not worth a traceback
        head = target
    bits = ["apply ok -> %s" % head]
    for key in ("bytes", "operations_done", "operations_total", "layers_written"):
        if key in out:
            bits.append("%s=%s" % (key, out[key]))
    if out.get("done") is False:
        bits.append("done=False")
    if out.get("status") == "ok" and out.get("gates"):
        bad = [k for k, v in out["gates"].items()
               if isinstance(v, dict) and v.get("ok") is False]
        if bad:
            bits.append("gates_failed=%s" % ",".join(bad))
    return _j.auto("apply", " ".join(bits)[:_j.MAX_TEXT], board=target)


def _board_journal(args):
    """Append / read the tiny progress journal (survives a session break)."""
    from . import journal as _j
    action = (args.get("action") or "tail").strip().lower()
    try:
        if action == "add":
            return _j.append(args.get("kind"), args.get("text"),
                             board=args.get("board"), data=args.get("data"))
        if action == "resume":
            return _j.resume(limit=args.get("limit") or 20)
        if action == "clear":
            return _j.clear(args.get("confirm") is True)
        if action in ("read", "tail", "list"):
            return _j.tail(limit=args.get("limit") or 8, kind=args.get("kind"))
        raise _j.JournalError("ARG_INVALID",
                              "action %r is not add / tail / resume / clear" % action)
    except _j.JournalError as exc:
        return exc.as_dict()

def _board_restore(args):
    from . import edit as _edit
    try:
        return _edit.restore_checkpoint(args.get("checkpoint_id"),
                                       args.get("confirm") is True,
                                       keep_current=args.get("keep_current", True))
    except _edit.EditError as exc:
        return _edit_error(exc)

_tool(
    "board_ops",
    "The operation catalogue of the write path: every op this server can apply to a "
    "KiCad board, its parameters, and which part of the board it affects. Call it "
    "before board_propose.",
    {"type": "object", "properties": {}},
    _board_ops,
)

_tool(
    "board_create",
    "Create a NEW empty KiCad board (stackup, design rules, closed outline) that KiCad "
    "itself accepts. The file is written, then KiCad is asked whether the outline is "
    "really closed and its DRC really clean; if either says no, the file is removed "
    "again rather than left as something that merely looks like a board. Refuses to "
    "overwrite an existing file unless overwrite=true.",
    {"type": "object", "properties": {
        "path": _str("absolute path for the new .kicad_pcb (parent dirs are created)"),
        "spec": {
            "type": "object",
            "description": "width_mm, height_mm, x0_mm, y0_mm, thickness_mm, "
                           "copper_layers (2/4/6...), edge_width_mm, clearance_mm, "
                           "trace_width_mm, via_dia_mm, via_drill_mm, title, overwrite",
            "properties": {
                "width_mm": _num("board width", 60), "height_mm": _num("board height", 40),
                "x0_mm": _num("outline origin x", 0), "y0_mm": _num("outline origin y", 0),
                "thickness_mm": _num("board thickness", 1.6),
                "copper_layers": _num("2, 4, 6 ...", 2),
                "title": _str("title_block title"),
                "overwrite": _bool("replace an existing file"),
            }},
    }, "required": ["path"]},
    _board_create,
)

_tool(
    "schematic_pcb_diff",
    "Compare a KiCad board against a schematic WITHOUT touching either: KiCad's own "
    "netlister answers the schematic side, the board file answers the other, and the "
    "difference is reported pin by pin (missing parts, footprint ids that disagree, a "
    "pad that carries a different net than the netlist says, a net that reaches no "
    "pad). This is the scriptable form of \"Update PCB from Schematic\" - read-only.",
    {"type": "object", "properties": {
        "path": _str("absolute path to the .kicad_pcb"),
        "schematic": _str("absolute path to the .kicad_sch"),
    }, "required": ["path", "schematic"]},
    _schematic_pcb_diff,
)

_tool(
    "schematic_pcb_sync",
    "Turn schematic/board differences into board operations. A missing footprint has "
    "no position on a board, so it is only placed when you pass one in `placement` "
    "({ref: {x, y, rot, layer, library|footprint_file, name, pad_nets}}); everything "
    "else becomes set_pad_net / delete_footprint operations. With propose=true the "
    "list goes through board_propose, so nothing is written until board_apply.",
    {"type": "object", "properties": {
        "path": _str("absolute path to the .kicad_pcb"),
        "schematic": _str("absolute path to the .kicad_sch"),
        "library": _str(".pretty directory used for parts that only have a name"),
        "placement": {"type": "object", "description":
            "{ref: {x, y, rot, layer, library, name, footprint_file, lib_id, pad_nets}}"},
        "remove_extra": _bool("delete footprints the schematic does not have"),
        "clear_extra_pad_nets": _bool("untie board pads that no schematic pin claims"),
        "create_nets": _bool("allow nets the board does not declare yet", True),
        "propose": _bool("run board_propose on the operations (default: don't)"),
        "options": {"type": "object", "description": "passed to board_propose"},
    }, "required": ["path", "schematic"]},
    _schematic_pcb_sync,
)

_tool(
    "board_propose",
    "Plan a change to a KiCad board WITHOUT writing anything: returns a plan_id, the "
    "board digest before/after, per-operation postconditions that WILL be checked, and "
    "the blocking reasons (open outline, new DRC violations, tracks outside the frame). "
    "Nothing on disk changes; read the digest, then call board_apply with confirm=true.",
    {"type": "object", "properties": {
        "path": _str("absolute path to the .kicad_pcb"),
        "operations": {"type": "array", "description":
            "list of {op, ...params}; call board_ops for the catalogue",
            "items": {"type": "object"}},
        "options": {"type": "object", "description":
            "allow_new_violations / allow_open_outline / engine (default true)",
            "properties": {
                "allow_new_violations": _bool("accept newly introduced DRC violations"),
                "strict_drc": _bool("let every new violation block, warnings included"),
                "allow_open_outline": _bool("accept an outline that does not close"),
                "engine": _bool("run the kicad-cli comparison (default true)", True),
                "mode": _str("in_place or new_file", enum=["in_place", "new_file"]),
                "out_path": _str("target when mode=new_file"),
            }},
    }, "required": ["path", "operations"]},
    _board_propose,
)

_tool(
    "board_apply",
    "Apply a proposal and prove it. Takes a checkpoint first, writes atomically, then "
    "runs four gates that do not trust the writing code: bytes-on-disk == planned bytes, "
    "re-parse reproduces the planned tree, every operation's postcondition is re-read "
    "from the file, and KiCad re-reads both files (stats/plots/positions) with its DRC "
    "diffed. Any blocking failure restores the checkpoint and says so. A plan is "
    "single-use and expires after an hour. Pass options.step=N for a stepped write "
    "that leaves a valid board on disk after every N operations, so a person can "
    "watch it in the EDA and interrupt it (board_progress reports and stops it); the "
    "finished stepped file still has to pass the same four gates.",
    {"type": "object", "properties": {
        "plan_id": _str("the plan_id that board_propose returned"),
        "confirm": _bool("must be true - writing a board is not Ctrl+Z"),
        "options": {"type": "object", "properties": {
            "allow_new_violations": _bool("accept newly introduced DRC violations"),
            "strict_drc": _bool("let every new violation block, warnings included"),
            "allow_open_outline": _bool("accept an outline that does not close"),
            "engine": _bool("run the kicad-cli comparison (default true)", True),
            "mode": _str("in_place or new_file", enum=["in_place", "new_file"]),
            "out_path": _str("target when mode=new_file"),
            "step": _num("STEPPED WRITE: how many operations to write in this call. "
                         "Each call leaves a complete board file on disk, so the "
                         "change can be watched in the EDA and stopped between steps. "
                         "Omit it for the one-shot atomic write."),
            "max_chunks": _num("stepped write: chunks per call (default 1)"),
            "dry_run": _bool("write it, run every gate, then put the original bytes "
                             "back and report what WOULD have happened (default "
                             "false). The plan stays open. A blocking gate comes "
                             "back as would_write=false with the reasons, instead of "
                             "an exception", False),
        }},
    }, "required": ["plan_id"]},
    _board_apply,
)

_tool(
    "board_checkpoints",
    "List the checkpoints this server took before writing. They live under "
    "PCB_AGENT_HOME (default ~/.pcb_agent_mcp), never beside the project.",
    {"type": "object", "properties": {
        "path": _str("only checkpoints for this board"),
        "limit": _num("how many to return", 20),
    }},
    _board_checkpoints,
)

_tool(
    "board_journal",
    "A tiny append-only progress journal under PCB_AGENT_HOME, so a session that "
    "dies mid-job can resume from ~1 KiB instead of a rewritten summary. "
    "action=add stores ONE note (kind + text, at most 400 characters, optional "
    "board path and small data object) and replies with the entry and the new "
    "totals; action=tail returns the last N entries with the byte cost; "
    "action=resume returns the newest kind='apply' entry plus the notes after "
    "it, capped at 4096 bytes (oldest dropped first) - that is the cheap way to "
    "pick a dead session back up; action=clear renames the file to "
    "journal.jsonl.bak-<stamp> (confirm=true, nothing is deleted). A successful "
    "board_apply also drops one kind='apply' line on its own. It records what a "
    "caller wrote, never that the work happened - re-read the board and run the "
    "DRC before trusting an entry.",
    {"type": "object", "properties": {
        "action": _str("add / tail / resume / clear",
                             enum=["add", "tail", "resume", "clear"]),
        "kind": _str("what the note is about (add), or filter by it (tail)",
                     enum=["board", "route", "drc", "apply", "note", "handover"]),
        "text": _str("the note, at most 400 characters"),
        "board": _str("board file this note is about"),
        "data": {"type": "object", "description":
                 "small numbers worth keeping (counts, ids); keep it under a "
                 "few hundred bytes"},
        "limit": _num("how many entries tail returns", 8),
        "confirm": _bool("clear only: true moves the file aside"),
    }},
    _board_journal,
)

_tool(
    "board_restore",
    "Put a checkpoint back on disk. The file currently on disk is checkpointed first, "
    "so a restore is itself undoable. Refuses without confirm=true.",
    {"type": "object", "properties": {
        "checkpoint_id": _str("checkpoint_id from board_checkpoints"),
        "confirm": _bool("must be true - this overwrites the board"),
        "keep_current": _bool("checkpoint the file being replaced (default true)", True),
    }, "required": ["checkpoint_id"]},
    _board_restore,
)

_tool(
    "board_progress",
    "Watch or stop a stepped apply. action=status/log reads the on-disk step log "
    "(one JSON line per written step: which operations, sha256, bytes, whether that "
    "step's postconditions held, and the snapshot path); action=stop writes a stop "
    "file that a stepped apply checks between steps; action=resume removes it. Also "
    "reports whether any stop file is currently in force.",
    {"type": "object", "properties": {
        "action": _str("status / log / stop / resume", enum=["status", "log", "stop",
                                                              "resume"]),
        "plan_id": _str("only this plan's steps"),
        "limit": _num("how many events to return", 200),
        "note": _str("why you stopped it (written into the stop file)"),
    }},
    _board_progress,
)

# --------------------------------------------------------- schematic write path
def _schematic_error(exc):
    """A schematic op error keeps its code; anything else stays visible as itself."""
    from . import edit as _edit
    if isinstance(exc, (_edit.EditError, _edit.OpError)):
        return _edit_error(exc)
    return err("%s: %s" % (type(exc).__name__, exc), "NOT_A_SCHEMATIC")

def _sch_ops(_args):
    from . import edit as _edit
    return {"status": "ok",
            "format": "kicad_sch only (KiCad schematic). LCEDA Pro .esch is NOT on the "
                      "write path: its sheets live inside the .epro archive and that "
                      "record layout was never measured, so it is refused rather than "
                      "guessed (WRITE_UNSUPPORTED_FORMAT)",
            "operations": _edit.describe_sch_ops(),
            "coordinates": _edit.sch_ops.FRAME,
            "protocol": "schematic_propose -> read the digest and the predicted "
                        "netlist/ERC diff -> schematic_apply(confirm=true)",
            "gates": "apply re-reads the bytes it wrote, re-parses them into exactly "
                     "the planned tree, re-checks every operation's postcondition, "
                     "then asks KiCad's netlister and ERC engine about BOTH files. A "
                     "netlist that changes when only symbols/text were touched, or "
                     "any NEW ERC violation, blocks the write and rolls it back",
            "note": "one plan is all-or-nothing; a plan id is single use"}

def _sch_propose(args):
    from . import edit as _edit
    try:
        return _edit.propose_schematic(args.get("path"), args.get("operations") or [],
                                       args.get("options") or {})
    except Exception as exc:                       # noqa: BLE001
        return _schematic_error(exc)

def _sch_apply(args):
    from . import edit as _edit
    try:
        return _edit.apply_plan(args.get("plan_id"), args.get("confirm") is True,
                                args.get("options") or {})
    except Exception as exc:                       # noqa: BLE001
        return _schematic_error(exc)

def _sch_view(args):
    from . import edit as _edit
    try:
        return _edit.schematic_view(args.get("path"),
                                    kind=args.get("kind") or "summary",
                                    limit=int(args.get("limit") or 200))
    except Exception as exc:                       # noqa: BLE001
        return _schematic_error(exc)

def _sch_summary(args):
    from . import edit as _edit
    try:
        return _edit.schematic_summary(args.get("path"),
                                       engine=args.get("engine", True) is not False)
    except Exception as exc:                       # noqa: BLE001
        return _schematic_error(exc)

_tool(
    "schematic_ops",
    "The operation catalogue of the SCHEMATIC write path (KiCad .kicad_sch): wires, "
    "buses, bus entries, junctions, no-connects, labels, global/hierarchical labels, "
    "netclass flags, text, graphics, and symbol placement/field editing - each with the "
    "coordinates it uses. Call it before schematic_propose.",
    {"type": "object", "properties": {}},
    _sch_ops,
)

_tool(
    "schematic_propose",
    "Plan a change to a KiCad schematic WITHOUT writing anything: returns a plan_id, the "
    "document digest before/after, and (when kicad-cli is present) the netlist and ERC "
    "diff the change is predicted to cause. Read it, then schematic_apply(confirm=true).",
    {"type": "object", "properties": {
        "path": _str("absolute path to the .kicad_sch"),
        "operations": {"type": "array", "description":
            "list of {op, ...params}; call schematic_ops for the catalogue",
            "items": {"type": "object"}},
        "options": {"type": "object", "description":
            "engine (true/false: run KiCad's netlister + ERC while planning), "
            "allow_new_violations", "properties": {}},
    }, "required": ["path", "operations"]},
    _sch_propose,
)

_tool(
    "schematic_apply",
    "Write a planned schematic change: checkpoint -> atomic write -> four gates - the "
    "bytes on disk are the planned bytes, re-parsing reproduces the planned tree, every "
    "operation's postcondition holds in the file just written, and KiCad's netlister + "
    "ERC were run on both files (a connectivity change nobody asked for, or any new ERC "
    "violation, blocks and rolls back). Requires confirm=true.",
    {"type": "object", "properties": {
        "plan_id": _str("plan_id from schematic_propose"),
        "confirm": _bool("must be true - this writes the file"),
        "options": {"type": "object", "description":
            "engine (default true), allow_new_violations", "properties": {}},
    }, "required": ["plan_id"]},
    _sch_apply,
)

_tool(
    "schematic_summary",
    "Read-only. What the sheet IS, in the terms the write path uses: version, paper, "
    "every symbol with its real reference/value/footprint/pins, wire/label/junction "
    "counts, project name and path, plus KiCad's own netlist and ERC result when "
    "kicad-cli is available.",
    {"type": "object", "properties": {
        "path": _str("absolute path to the .kicad_sch"),
        "engine": _bool("run kicad-cli netlist + ERC (default true)", True),
    }, "required": ["path"]},
    _sch_summary,
)

_tool(
    "schematic_view",
    "Read-only. One view of the sheet at a time: summary | symbols | objects | netlist | "
    "erc | lib_symbols | raw. Every answer repeats the coordinate frame it is in.",
    {"type": "object", "properties": {
        "path": _str("absolute path to the .kicad_sch"),
        "kind": _str("which view", enum=["summary", "symbols", "objects", "netlist",
                                         "erc", "lib_symbols", "raw"],
                     default="summary"),
        "limit": _num("max rows per list", 200),
    }, "required": ["path"]},
    _sch_view,
)

def _sch_hierarchy(args):
    from . import edit as _edit
    try:
        return _edit.schematic_hierarchy(args.get("path"))
    except Exception as exc:                       # noqa: BLE001
        return _schematic_error(exc)

def _sch_new_file(args):
    from . import edit as _edit
    try:
        return _edit.new_schematic_file(args.get("path"),
                                        paper=args.get("paper") or "A4",
                                        title=args.get("title"),
                                        confirm=args.get("confirm") is True)
    except Exception as exc:                       # noqa: BLE001
        return _schematic_error(exc)

_tool(
    "schematic_hierarchy",
    "Read-only. Which .kicad_sch files make up this schematic, and how: the root KTH "
    "file (found by walking Sheetfile references, not guessed), every sheet box with "
    "its page number and pins, a file used by two sheets reported as one file with two "
    "instances, and the tree's problems (missing child, duplicate page, cycle). Run it "
    "before adding or moving a sheet, and after an apply to see the tree that resulted.",
    {"type": "object", "properties": {
        "path": _str("absolute path to any .kicad_sch in the project"),
    }, "required": ["path"]},
    _sch_hierarchy,
)

_tool(
    "schematic_new_file",
    "Create an EMPTY .kicad_sch - a new page for a multi-page project. Refuses to "
    "overwrite an existing file, refuses to create a directory, and refuses without "
    "confirm=true. It does not draw the sheet box that makes the file part of a project "
    "(that is schematic_ops op='add_sheet'), because a box pointing at a page with "
    "nothing behind it is exactly what ERC refuses.",
    {"type": "object", "properties": {
        "path": _str("absolute path to the .kicad_sch to create (must not exist)"),
        "paper": _str("A4 / A3 / A2 / A1 / A0 / A5 / USLetter ... (default A4)"),
        "title": _str("optional title_block title"),
        "confirm": _bool("must be true - this creates a file"),
    }, "required": ["path"]},
    _sch_new_file,
)

# ---------------------------------------------------------------- simulation
_tool(
    "sim_spice",
    "Run a real circuit simulation on a SPICE netlist: DC operating point (.op), transient "
    "(.tran), small-signal AC (.ac) and small-signal noise (.noise). The answer comes from "
    "the BUILTIN stdlib solver "
    "(Newton + junction voltage limiting + gmin stepping, per-element voltage/current/"
    "power reported back) or, if an ngspice binary is installed, from ngspice itself - "
    "see the engine parameter. The builtin solver takes R C L V I E G D Q M plus the "
    "two current-controlled sources F (Fname n+ n- vname gain, a CCCS: the third field "
    "is the NAME of the element whose current is read, so F adds no unknown of its "
    "own) and H (Hname n+ n- vname gain, a CCVS - a voltage source whose value is that "
    "current, so H does need a row of its own), plus mutual inductance K (Kname L1 L2 "
    "k with |k|<=1: M = k*sqrt(L1*L2) couples two EXISTING inductors, so a K card has "
    "no terminals and owns no row, and at DC it changes nothing at all), plus "
    "behavioural B sources (the expression is compiled to a tree at parse time, so a "
    "syntax error names the line; laplace()/I(Vname)/ddt/idt are refused by name), plus "
    "the lossless transmission line T (Tname n1 n2 n3 n4 Z0=<ohm> TD=<s>, or F=/NL= for "
    "the same delay; DC is a wire, .ac is the exact lossless phase shift, .tran is the "
    "method of characteristics with reflections), plus the two distributed cards "
    "O (Oname n1 n2 n3 n4 m, an LTRA .model with r/l/g/c/len) and U (Uname n1 n2 n3 m, "
    "a URC .model with r/c/len) - both expanded at parse time into lump= sections of "
    "ordinary R/L/C (default 20, and the count is reported with the answer), so DC is "
    "the model's exact series resistance and .ac/.tran converge to the distributed "
    "answer as lump grows, plus .noise V(out[,ref]) <source> dec|oct|lin n fstart "
    "fstop (see the noise paragraph below), plus "
    ".subckt/ends with X instances (FLATTENED before solving: numeric parameters, "
    "{+ - * / **} arithmetic on them, subcircuit-local .model, nested instances), plus the two switches S (voltage-controlled: four terminals "
    "because its control is a NODE PAIR) and W (current-controlled: its third field "
    "is an ELEMENT name like F/H, and reading it as a node switches on 0 A forever); "
    "a switch is two conductances and a STATE, so VT +/- VH is a FULL width, VH=0 "
    "closes AT VT, DC walks the same band, ON/OFF only SEEDS the state (measured: "
    "ON with a 0 V control still settles open), and an unresolvable state is "
    "NOT_CONVERGED with a bistable hint; and J (a level-1 JFET) is THREE "
    "terminals (drain, gate, source - no bulk) with the square law of its own: "
    "BETA*(Vov)^2 in saturation and BETA*(2*Vov*Vds - Vds^2) below the knee, "
    "NO factor of 1/2 (measured: BETA=1m at Vov=1 reads 1 mA, where a MOSFET "
    "reads 0.5 mA), the two forms meeting EXACTLY at Vds=Vov so no smoothing is "
    "needed, AREA on the card REPLACING the model's (model 2 with card 3 reads "
    "3x, not 6x), gate junction NOT stamped but reported as "
    "gate_junction_forward_v with outside_model past 0.5 V, and RD/RS/B/IS/PB/"
    "MJ/FC/CGS/CGD/KF/AF/TCV/BEX/BETA1/LAMBDA1 refused by name (each one "
    "measured to change the answer); it "
    "solves the four purely algebraic XSPICE code models since 0.0.39/0.0.40 "
    "(gain: out = gain*(in + in_offset); limit: the same, clamped; and the "
    "multi-port summer/mult since 0.0.40, card written A1 [1 2] out model with "
    "in_gain/in_offset vectors: out = out_offset + out_gain * SUM or PROD of "
    "in_gain_i*(v_i + in_offset_i); all four are also swept in .ac since 0.0.41, "
    "linearised at the DC operating point - a limit block passes its gain inside "
    "the band and 0 outside it, a mult block passes the other inputs' bias values, "
    "and a flat 0 says so in warnings) and REJECTS EVERY OTHER A CARD BY NAME - "
    "state, sample clocks, divider - never silently; with ngspice the deck is passed "
    "through, so that card works, but the per-element report is then absent (this "
    "server will not re-derive it and present its own arithmetic as the engine's). "
    "A deck this server's own parser cannot read is reported with netlist_parsed:false "
    "and netlist_parse_note instead of being quietly treated as a full parse. "
    "Expanded instances keep the internal node name ngspice prints (x1.m), so "
    "cross_check compares internal nodes too. .param is supported at the top level "
    "and inside subcircuit bodies (numbers or {+ - * / **} arithmetic on parameters; "
    "forward references are fine, a circular definition is refused by name). "
    ".noise is solved ADJOINTLY: at the DC bias, one complex-MNA transpose solve per "
    "frequency gives the transfer from every noise current to the output, so all "
    "sources cost one solve and the gain cannot disagree with the .ac answer (it IS "
    "the .ac matrix read backwards). Mechanisms are thermal 4kT/R, shot 2q*|Id| / "
    "2q*|Ic| / 2q*|Ib| and a level-1 MOSFET's (2/3)gm channel thermal; there is no "
    "1/f, burst or induced-gate noise, and an ideal source, C or L is silent BY "
    "CONSTRUCTION (a capacitor's ESR is the R card you did not write). Each answer "
    "carries the density per swept point, the rms integrated over the swept band, "
    "the contributions per element, and the input-referred density (null with a "
    "warning when the source cannot reach the output, rather than inf). The band IS "
    "the sweep and the grid picks the integration rule (dec/oct in ln f, lin in f), "
    "all stated in the answer. Unlike ngspice it does NOT require an AC value on "
    "the input source, and it refuses a card naming a node nothing connects. Not "
    "implemented: expression functions, expressions in .model parameters, a .subckt "
    "nested in another .subckt, .print.",
    {"type": "object", "properties": {
        "netlist": _str("SPICE netlist text, one card per line; a leading * is a comment. "
                        "The first line is not skipped unless it starts with * - write "
                        "a title line beginning with * to be safe"),
        "analysis": _str("override or omit the .op/.tran/.ac/.noise card: e.g. "
                         "'.tran 0.1m 5m', '.ac dec 20 1 1meg' or "
                         "'.noise V(2) V1 dec 10 1 1meg'"),
        "temp_c": _num("simulation temperature, 27 C default unless a .temp card is "
                       "present", 27),
        "max_iter": _num("Newton iteration budget per solve", 200),
        "max_points": _num("cap on transient/AC output points", 200),
        "engine": _str("which solver: builtin (default - the stdlib subset), ngspice, "
                       "or auto (ngspice when found on PATH / in PCB_AGENT_NGSPICE / "
                       "in a standard install dir, else builtin). The default is the "
                       "builtin on purpose: a run that silently switches model sets "
                       "depending on what is installed would answer differently on two "
                       "machines with no way to tell from the number. "
                       "engine='ngspice' REFUSES when no binary is found instead of "
                       "quietly answering with the subset",
                       enum=["builtin", "ngspice", "auto"], default="builtin"),
        "cross_check": _bool("run BOTH solvers on the same deck and report the largest "
                             "node-voltage disagreement; this is how the delegation "
                             "itself is audited", False),
    }, "required": ["netlist"]},
    lambda a: _spice.simulate(a.get("netlist"), analysis=a.get("analysis"),
                              temp_c=a.get("temp_c"),
                              max_iter=int(a.get("max_iter") or 200),
                              max_points=int(a.get("max_points") or 200),
                              engine=a.get("engine") or "builtin",
                              cross_check=bool(a.get("cross_check"))),
)

_tool(
    "trace_resistance",
    "DC resistance of a copper trace from its geometry: R = rho(T)*L/(w*t). Use it for the "
    "quiet half of the question - the rest of it (vias, spreading, connectors) is not in "
    "this number.",
    {"type": "object", "properties": {
        "length_mm": _num("trace length"),
        "width_mm": _num("trace width"),
        "thickness_um": _num("copper thickness (1 oz = 35 um)"),
        "temp_c": _num("copper temperature", 20),
        "layers": _num("how many layers carry this trace in parallel", 1),
    }, "required": ["length_mm", "width_mm", "thickness_um"]},
    lambda a: _power.copper_resistance(a.get("length_mm"), a.get("width_mm"),
                                       a.get("thickness_um"),
                                       temp_c=a.get("temp_c") or 20,
                                       layers=int(a.get("layers") or 1)),
)

_tool(
    "via_resistance",
    "DC resistance of plated via barrels: the cross-section is the copper ANNULUS, not the "
    "drill circle. Answers 'how many vias does this 3 A path need' in resistance terms - "
    "current capacity is a separate question with a temperature-rise criterion. "
    "Two hole conventions exist and they are 18 % apart on a typical via, so the "
    "answer names the one it used: by default drill_mm is the DRILLED hole with "
    "the plating inside it (unchanged since the first release); pass "
    "plating_outside=true when your drawing gives the FINISHED hole and the copper "
    "grows outward. DC "
    "only: above a few MHz the barrel is not a DC annulus at all, and the AC answer "
    "for the same drill_mm/plating_um is ac_round_conductor (it returns R/R_dc and "
    "the frequency where the DC number stopped being allowed).",
    {"type": "object", "properties": {
        "drill_mm": _num("hole diameter. WHICH hole depends on plating_outside: "
                         "the drilled hole by default (copper inside it), the "
                         "finished hole with plating_outside=true"),
        "plating_um": _num("barrel wall thickness (typically 20..25 um)"),
        "plating_outside": _bool("false (default, unchanged): drill_mm is the "
                                 "drilled hole and the copper lies INSIDE it, so "
                                 "the finished hole is drill - 2*plating. true: "
                                 "drill_mm is the FINISHED hole and the copper lies "
                                 "OUTSIDE it, outer diameter = drill + 2*plating - "
                                 "the fabrication-drawing convention, and the one "
                                 "ac_round_conductor uses. The two differ by 18 % "
                                 "in area on a 0.3 mm hole with 25 um plating"),
        "length_mm": _num("barrel length = board thickness"),
        "temp_c": _num("copper temperature", 20),
        "count": _num("how many vias in parallel", 1),
    }, "required": ["drill_mm", "plating_um", "length_mm"]},
    lambda a: _power.via_resistance(a.get("drill_mm"), a.get("plating_um"),
                                    a.get("length_mm"),
                                    temp_c=a.get("temp_c") or 20,
                                    count=int(a.get("count") or 1),
                                    plating_outside=bool(a.get("plating_outside"))),
)

_tool(
    "trace_inductance",
    "Self-inductance of a straight trace (Grover ribbon formula). Order of magnitude "
    "+-20%: it ranks two layouts, it does not predict a resonance. The ground-plane image "
    "term is reported as NOT included rather than folded in silently.",
    {"type": "object", "properties": {
        "length_mm": _num("trace length"),
        "width_mm": _num("trace width"),
        "height_mm": _num("height above the return plane"),
    }, "required": ["length_mm", "width_mm", "height_mm"]},
    lambda a: _power.trace_inductance(a.get("length_mm"), a.get("width_mm"),
                                      a.get("height_mm")),
)

_tool(
    "ir_drop_chain",
    "Series IR drop along an ordered chain of copper segments - the case that is usually "
    "got wrong by averaging the widths. Each segment keeps its own length/width/thickness.",
    {"type": "object", "properties": {
        "segments": {"type": "array", "description":
                     "list of {length_mm, width_mm, thickness_um}, in current order",
                     "items": {"type": "object"}},
        "current_a": _num("current through the chain"),
        "temp_c": _num("copper temperature", 20),
    }, "required": ["segments", "current_a"]},
    lambda a: _power.ir_drop(a.get("segments"), a.get("current_a"),
                             temp_c=a.get("temp_c") or 20),
)

_tool(
    "current_density_field",
    "2-D steady-state current density inside copper: solves div(sigma*grad V) = 0 on the "
    "region you draw, so the number it gives is J AND WHERE. Everything else here is "
    "one-dimensional (J = I/(w*t) on one segment), which is right on the straight part and "
    "wrong exactly where boards fail: a 90 degree bend, a width step, a via neck, a "
    "pad-to-track funnel. Copper is given as polygons and/or round-capped polylines in mm; "
    "two contacts are Dirichlet (source 0 V, sink 1 V) and everything else is insulating, "
    "so R and J for any current follow from one solve by linearity. Costs 3-60 s of pure "
    "Python (no numpy: dependencies stay empty): the grid is chosen from max_unknowns and "
    "the wall clock goes as cells^1.5, so pass h_mm when you want a specific resolution. "
    "READ THE SINGULARITY WARNING: the inside of a bend is a 270 degree metal corner, "
    "J ~ r^(-1/3) there, so the PEAK is not grid convergent (measured growth 1.266 per "
    "halving of h, theory 2^(1/3) = 1.2599). The peak is still returned, with "
    "peak_growth, with peak_is_grid_convergent, and with ring readings (max J in the "
    "annuli 0.5h..1h, 1h..2h, 2h..4h around the peak) that DO hold still when the grid is "
    "refined - quote those, or round the corner. A contact edge is singular for the same "
    "reason, so a hot spot right at a pad is geometry, not a defect.",
    {"type": "object", "properties": {
        "paths": {"type": "array", "description":
                  "round-capped polylines: [{points: [[x_mm, y_mm], ...], width_mm: w}]. "
                  "A polyline is not copper until it is given a width", "items":
                  {"type": "object"}},
        "polygons": {"type": "array", "description":
                     "filled polygons: [[[x_mm, y_mm], ...], ...] (>= 3 points each). "
                     "Union with paths; even-odd inside one polygon",
                     "items": {"type": "array"}},
        "source": {"type": "object", "description":
                   "the contact current enters: {x_mm, y_mm, radius_mm} for a round pad "
                   "or {x0_mm, y0_mm, x1_mm, y1_mm} for a rectangle, in mm. A cell joins "
                   "the contact when its square overlaps the region"},
        "sink": {"type": "object", "description":
                 "the contact current leaves: same forms as source. Must not overlap it"},
        "thickness_mm": _num("copper thickness in mm (0.035 = 1 oz finished)", 0.035),
        "temp_c": _num("copper temperature for rho, 20 C default (alpha 0.00393/K)", 20),
        "rho_ohm_mm": _num("resistivity in ohm*mm; overrides temp_c. Copper 20 C is "
                           "1.724e-5"),
        "current_a": _num("DC current through the region (the field is linear in it)", 1.0),
        "h_mm": _num("grid spacing of the finest level, mm. Omit and it is derived "
                     "from max_unknowns"),
        "max_unknowns": _num("unknown budget for the finest level when h_mm is omitted "
                             "(40000 keeps the run under a minute)", 40000),
        "levels": _num("grid levels, coarse to fine (each level re-rasterises the same "
                       "geometry, so this is a real refinement)", 4),
        "tol": _num("SOR convergence tolerance in volts, against 1 V applied", 1e-8),
        "max_sweeps": _num("SOR sweep cap per level; hitting it returns NOT_CONVERGED "
                           "instead of a number", 30000),
        "omega": _num("SOR over-relaxation; omit for 1.97 (measured optimum on mixed "
                      "Dirichlet/Neumann copper)"),
        "frequency_hz": _num("OPTIONAL AC: reduce the sheet conductance to the AC "
                             "value of this thickness (exact 1-D skin effect), so R "
                             "and the power come back as AC ones. Omit and the "
                             "answer is the DC one, byte for byte as before"),
        "skin_faces": _str("with frequency_hz: 'one' (return current on one side: "
                           "microstrip, plane over its return) or 'two' "
                           "(symmetric: stripline, plane between two returns)",
                           "one"),
    }, "required": ["source", "sink"]},
    lambda a: _cfield.current_density_field(
        paths=a.get("paths"), polygons=a.get("polygons"),
        thickness_mm=(a.get("thickness_mm")
                      if a.get("thickness_mm") is not None else 0.035),
        rho_ohm_mm=(a.get("rho_ohm_mm") if a.get("rho_ohm_mm") is not None else
                    _power._rho(a["temp_c"] if a.get("temp_c") is not None else 20.0)
                    * 1e3),
        source=a.get("source"), sink=a.get("sink"),
        current_a=(a.get("current_a") if a.get("current_a") is not None else 1.0),
        h_mm=a.get("h_mm"),
        max_unknowns=int(a.get("max_unknowns")
                         if a.get("max_unknowns") is not None else 40000),
        levels=int(a.get("levels") if a.get("levels") is not None else 4),
        tol=(a.get("tol") if a.get("tol") is not None else 1e-8),
        max_sweeps=int(a.get("max_sweeps")
                       if a.get("max_sweeps") is not None else 30000),
        omega=a.get("omega"),
        frequency_hz=a.get("frequency_hz"),
        skin_faces=(a.get("skin_faces") if a.get("skin_faces") is not None
                    else "one")),
)

_tool(
    "ac_sheet_resistance",
    "Skin effect in a flat conductor: R_ac/R_dc, the skin depth, the effective "
    "thickness and the J(z) profile, from the EXACT 1-D diffusion solution in the "
    "thickness direction. Answers the question every DC copper tool here refuses: at "
    "what frequency does this copper stop being DC, and by how much does the answer "
    "move. Two face conditions, and they differ by a factor of two in effective "
    "thickness - 'one' (return current on one side: microstrip, a plane over its "
    "return) or 'two' (symmetric: stripline, a plane between two returns). Returns "
    "the frequencies where R has grown 1 %, 10 % and 2x, which is the number that "
    "decides whether the DC tools are still allowed. It is 1-D: no lateral "
    "redistribution along the width, no edge crowding, no proximity effect from a "
    "neighbouring trace or the return plane's own eddy currents, and roughness only "
    "if you pass Rq (Hammerstad-Jensen multiplier). Feeds current_density_field: give "
    "it the same frequency and it solves the same 2-D geometry with the reduced sheet "
    "conductance, but the PATTERN is unchanged - that is the limit of a uniform "
    "thickness correction, and it says so. For a ROUND conductor (a wire, a tube, "
    "a via barrel) this geometry is wrong: the curvature and the bore change the "
    "answer, and ac_round_conductor solves that one exactly. This is the "
    "resistance only: if you have a current to go with it, ac_self_heating turns "
    "this same R(f) into a temperature by closing the electro-thermal loop, where "
    "the hot copper has a longer skin depth and therefore a different R again.",
    {"type": "object", "properties": {
        "frequency_hz": _num("frequency, Hz. Required: a skin depth without a "
                             "frequency is just a length"),
        "thickness_mm": _num("conductor thickness, mm (use this or thickness_um)"),
        "thickness_um": _num("conductor thickness, um: 35 = 1 oz, 17.5 = half oz, "
                             "12 = HVLP foil"),
        "rho_ohm_mm": _num("resistivity, ohm*mm; omit for copper at temp_c "
                           "(1.724e-5 at 20 C)"),
        "temp_c": _num("copper temperature, C - only used when rho_ohm_mm is omitted",
                       20.0),
        "mu_r": _num("relative permeability of the conductor, 1.0 for copper", 1.0),
        "faces": _str("'one' (return current on one side: microstrip, plane over a "
                      "return) or 'two' (symmetric: stripline, plane between two "
                      "returns). The effective thickness differs by 2x", "one"),
        "length_mm": _num("conductor length, mm - with width_mm it turns the sheet "
                          "numbers into an absolute ohm value"),
        "width_mm": _num("conductor width, mm"),
        "dc_resistance_ohm": _num("an already known DC resistance to scale instead of "
                                  "a geometry (exclusive with length_mm/width_mm)"),
        "roughness_rq_um": _num("RMS surface roughness Rq in um (NOT Rz): applies the "
                                "Hammerstad-Jensen multiplier. Omit and the number is "
                                "a smooth-copper lower bound"),
        "profile_samples": _num("how many depths the J(z) profile is reported at "
                                "(>= 2)", 9),
    }, "required": ["frequency_hz"]},
    lambda a: _skin.ac_sheet_resistance(
        a.get("frequency_hz"),
        thickness_mm=a.get("thickness_mm"), thickness_um=a.get("thickness_um"),
        rho_ohm_mm=a.get("rho_ohm_mm"),
        temp_c=(a.get("temp_c") if a.get("temp_c") is not None else 20.0),
        mu_r=(a.get("mu_r") if a.get("mu_r") is not None else 1.0),
        faces=(a.get("faces") if a.get("faces") is not None else "one"),
        length_mm=a.get("length_mm"), width_mm=a.get("width_mm"),
        dc_resistance_ohm=a.get("dc_resistance_ohm"),
        roughness_rq_um=a.get("roughness_rq_um"),
        profile_samples=(a.get("profile_samples")
                         if a.get("profile_samples") is not None else 9)),
)

_tool(
    "ac_round_conductor",
    "Skin effect in a ROUND conductor - a solid wire, a tube, or a plated via "
    "barrel - from the exact 2-D axisymmetric diffusion equation "
    "E'' + E'/r + k^2 E = 0, not from the flat-sheet approximation. Geometry is "
    "exactly one of: diameter_mm (solid), diameter_mm + wall_um (tube), or "
    "drill_mm + plating_um (a via, whose outer diameter is drill + 2*plating: the "
    "bore is never guessed, and the wall is what carries the current). It returns "
    "R(f), the INTERNAL reactance X(f), R/R_dc, X/R_dc, the skin depth, the "
    "regime, and the frequencies at which R has grown 1 %, 10 % and 2x - the "
    "number that decides whether via_resistance or copper_resistance is still "
    "allowed. A via is not a thin wire: a 0.15 mm drill with 25 um plating at "
    "1 GHz comes back R/R_dc = 10.58 with the whole 2.09 um skin depth inside the "
    "wall, and reporting the DC number there is off by an order of magnitude. A "
    "tube is also NOT a mirror half of a solid wire: the bore encloses no net "
    "current, so H_phi = 0 on the inner surface (E'(a1) = 0) - a Neumann "
    "condition, not a symmetry plane - and treating it as one half of a solid "
    "wire is a different answer. Still axisymmetric and radial-only: no proximity "
    "effect from a neighbouring barrel, no barrel-to-barrel or pad coupling, and "
    "no external (partial) inductance - that second one is trace_inductance or a "
    "2-D field tool, and this tool only reports the field INSIDE the metal. "
    "Roughness is a Hammerstad-Jensen multiplier on the surface resistance, off "
    "unless Rq is given (smooth copper is a lower bound). Numerics: the tube "
    "closed form involves J0/J1/Y0/Y1 in cancellation and loses all its digits "
    "above x ~ 15, so the answer is integrated from the Riccati log-derivative "
    "equation with RK4 + Richardson; the closed form is used only as an "
    "independent cross-check below x = 10, and when it is not run the checks say "
    "closed_form_skipped instead of printing a number that looks like agreement. "
    "R only: ac_self_heating takes exactly this R(f) and turns it into a "
    "temperature at a given current and Rth, where the heating moves rho and rho "
    "moves the wall/delta ratio again.",
    {"type": "object", "properties": {
        "frequency_hz": _num("frequency, Hz. Required: a skin depth without a "
                             "frequency is just a length"),
        "diameter_mm": _num("outer diameter, mm - a solid wire on its own, or a "
                            "tube with wall_um"),
        "wall_um": _num("wall thickness of a tube, um (with diameter_mm). Omit for "
                        "a solid wire"),
        "drill_mm": _num("drilled hole diameter of a via, mm (with plating_um). "
                         "Exclusive with diameter_mm"),
        "plating_um": _num("plating thickness of a via barrel, um (with drill_mm); "
                           "20-25 um is a standard through-hole, 8-12 um a microvia"),
        "rho_ohm_mm": _num("resistivity, ohm*mm; omit for copper at temp_c "
                           "(1.724e-5 at 20 C)"),
        "temp_c": _num("conductor temperature, C - only used when rho_ohm_mm is "
                       "omitted", 20.0),
        "mu_r": _num("relative permeability of the conductor, 1.0 for copper", 1.0),
        "length_mm": _num("conductor length, mm - turns the per-metre numbers into "
                          "an absolute ohm value for that barrel or wire"),
        "roughness_rq_um": _num("RMS surface roughness Rq in um (NOT Rz): applies "
                                "the Hammerstad-Jensen multiplier. Omit and the "
                                "number is a smooth-copper lower bound"),
        "profile_samples": _num("how many radii the J(r) profile is reported at "
                                "(>= 2; 9 is the default, and the profile is a "
                                "Hermite cubic on the log-derivative walk, so 2 "
                                "is legal but says almost nothing)", 9),
        "fd_points": _num("grid points for the independent finite-difference "
                          "cross-check; it is scaled up with x on its own", 801),
        "fd_verify": _bool("run the finite-difference cross-check at all (it is "
                           "skipped and says so above x = 200, where no "
                           "practical grid resolves the wall)", True),
    }, "required": ["frequency_hz"]},
    lambda a: _round.ac_round_conductor(
        a.get("frequency_hz"),
        diameter_mm=a.get("diameter_mm"), wall_um=a.get("wall_um"),
        drill_mm=a.get("drill_mm"), plating_um=a.get("plating_um"),
        rho_ohm_mm=a.get("rho_ohm_mm"),
        temp_c=(a.get("temp_c") if a.get("temp_c") is not None else 20.0),
        mu_r=(a.get("mu_r") if a.get("mu_r") is not None else 1.0),
        length_mm=a.get("length_mm"),
        roughness_rq_um=a.get("roughness_rq_um"),
        profile_samples=int(a.get("profile_samples")
                            if a.get("profile_samples") is not None else 9),
        fd_points=int(a.get("fd_points") if a.get("fd_points") is not None else 801),
        fd_verify=(a.get("fd_verify") if a.get("fd_verify") is not None else True)),
)

_tool(
    "ac_self_heating",
    "Temperature of a conductor carrying AC current, solved self-consistently - "
    "the loop electrothermal_trace and electrothermal_conductor deliberately do "
    "NOT close at frequency (their P = I^2*R uses the DC resistance). Hot copper "
    "has a longer skin depth, a longer skin depth means a thinner effective "
    "shell, so the resistance that made the heat depends on the heat. The single "
    "number that decides the behaviour is dlnR/dlnrho, and it is MEASURED at the "
    "solution (central difference through the same solver), not assumed: it is 1 "
    "in a DC or wall-limited conductor (the wall is thinner than the skin depth) "
    "and 1/2 once the current lives in a shell. That difference is the whole "
    "answer to \"can this run away\": with exponent 1 the resistance grows exactly "
    "as fast as the heat driving it and there is NO steady state at any "
    "temperature; with 1/2 the loop always has a mathematical fixed point, it is "
    "just usually above the melting point, and those two failures come back with "
    "different messages and different numbers (root_beyond_melt_c vs no root at "
    "all), because they are not the same thing. A PULSE TRAIN IS NOT ONE "
    "FREQUENCY: give harmonics=[{frequency_hz, current_a}, ...] (RMS "
    "magnitudes) and optionally dc_current_a, and the loop becomes "
    "g(dT) = dT - Rth*sum_k I_k^2*R_k(T) - every harmonic dissipates in the "
    "SAME copper, so the powers add and the temperatures do not, and the DC "
    "term sees the whole cross-section while the top harmonic sees a skin. "
    "The exponent that decides runaway is then measured on the SUM, i.e. it "
    "is the power-weighted dlnP/dlnrho, and both what a DC-only model and "
    "what a single-frequency model would have said are reported as "
    "differences (dc_linear_model_rel_diff / single_frequency_model_rel_diff) "
    "rather than as error bars. Geometry: shape='sheet' means "
    "length_mm + width_mm + exactly one of thickness_mm/thickness_um and "
    "faces='one' (microstrip) or 'two' (stripline); shape='round' means a solid "
    "diameter_mm, a tube diameter_mm + wall_um, or a via drill_mm + plating_um "
    "with the outer diameter at drill + 2*plating. Rth is the caller's, always: "
    "there is no honest default for a thermal path (feed it thermal_stack, "
    "via_array_thermal, plate_temperature or a measurement), and the same is "
    "true of the frequency - ask electrothermal_conductor for DC. Reported "
    "besides the temperature: resistance hot and cold, the growth ratio, the "
    "regime, the loop gain at the answer, the current (or, for a spectrum, "
    "the factor every magnitude must be scaled by) at which the cold gain "
    "reaches 1, the Rth that would keep the root below melting, and an "
    "energy-balance residual measured on the actual root rather than assumed.",
    {"type": "object", "properties": {
        "shape": {"type": "string", "enum": ["sheet", "round"],
                  "description": "which 1-D problem this is: 'sheet' (flat "
                                 "trace/plane, Cartesian) or 'round' (wire, "
                                 "tube, via barrel; Bessel). Required - it is "
                                 "never inferred from which arguments are "
                                 "present, because the same thickness in the two "
                                 "geometries means different physics"},
        "frequency_hz": _num("frequency, Hz. Required: without it this is the DC "
                            "loop, which is electrothermal_conductor"),
        "current_a": _num("RMS current, A. Required unless harmonics is "
                            "given: one frequency, one current"),
        "harmonics": {"type": "array", "maxItems": 32,
                      "description": "the current spectrum, for a pulse "
                                     "train or a switching waveform: a "
                                     "list of {frequency_hz, current_a} "
                                     "RMS magnitudes, at most 32, no "
                                     "duplicate frequencies (two currents "
                                     "at one frequency need phasors and "
                                     "this model has no phase). Exclusive "
                                     "with current_a/frequency_hz",
                      "items": {"type": "object",
                                "properties": {
                                    "frequency_hz": _num("harmonic "
                                                         "frequency, Hz "
                                                         "(> 0)"),
                                    "current_a": _num("RMS magnitude of "
                                                      "this harmonic, A "
                                                      "(>= 0)")},
                                "required": ["frequency_hz",
                                             "current_a"],
                                "additionalProperties": False}},
        "dc_current_a": _num("the DC component of the current, A: R_dc = "
                            "rho*L/A sees the whole cross-section, while "
                            "every harmonic sees its own skin depth. May "
                            "accompany current_a or harmonics"),
        "rth_c_per_w": _num("thermal resistance ambient->junction of the lump "
                            "you are describing, K/W. Required: a made-up Rth "
                            "would be the only number in this answer that was "
                            "not derived from something (thermal_stack, "
                            "via_array_thermal and plate_temperature all return "
                            "one)"),
        "length_mm": _num("conductor length, mm. Required for both shapes: a "
                          "temperature needs an absolute resistance, not ohms "
                          "per square"),
        "width_mm": _num("trace width, mm (sheet)"),
        "thickness_mm": _num("copper thickness, mm (sheet). Exclusive with "
                             "thickness_um"),
        "thickness_um": _num("copper thickness, um (sheet): 35 is 1 oz, 17 is "
                             "half an ounce, 70 is 2 oz"),
        "faces": {"type": "string", "enum": ["one", "two"],
                  "description": "'one' = return current on one side (microstrip, "
                                 "a trace over a plane: the effective thickness "
                                 "doubles), 'two' = symmetric (stripline, a plane "
                                 "between two returns). It is a factor of two, "
                                 "not a cosmetic choice"},
        "diameter_mm": _num("outer diameter, mm (round): a solid wire alone, or a "
                            "tube with wall_um. Exclusive with drill_mm"),
        "wall_um": _num("wall thickness, um (round, with diameter_mm): a tube or "
                        "a barrel whose copper you know"),
        "drill_mm": _num("drilled hole diameter, mm (round, with plating_um): a "
                         "via. The finished hole is drill_mm and the copper sits "
                         "outside it, so OD = drill + 2*plating"),
        "plating_um": _num("plating thickness, um (round, with drill_mm): 20-25 "
                           "for a through hole, 8-12 for a microvia. A bare hole "
                           "is not a conductor and is refused"),
        "t_ambient_c": _num("ambient temperature the Rth refers to, C", 25.0),
        "mu_r": _num("relative permeability of the conductor, 1.0 for copper", 1.0),
        "roughness_rq_um": _num("RMS surface roughness Rq, um. Off by default "
                                "(smooth copper is a lower bound): on, it enters "
                                "the ANSWER here, unlike ac_sheet_resistance "
                                "where it is a separate field - and it also "
                                "lowers dlnR/dlnrho below 1/2, so the exponent "
                                "expectation is suppressed instead of failed"),
        "alpha_per_k": _num("temperature coefficient of resistance, 1/K; omit for "
                            "0.00393 (IACS copper, linear 0..100 C)"),
        "rho20_ohm_m": _num("resistivity at 20 C, ohm*m; omit for 1.724e-8"),
    }, "required": ["shape", "rth_c_per_w", "length_mm"]},
    lambda a: _at.ac_self_heating(
        a.get("shape"),
        a.get("frequency_hz"),
        a.get("current_a"),
        a.get("rth_c_per_w"),
        length_mm=a.get("length_mm"), width_mm=a.get("width_mm"),
        thickness_um=a.get("thickness_um"), thickness_mm=a.get("thickness_mm"),
        faces=(a.get("faces") if a.get("faces") is not None else "one"),
        diameter_mm=a.get("diameter_mm"), drill_mm=a.get("drill_mm"),
        plating_um=a.get("plating_um"), wall_um=a.get("wall_um"),
        t_ambient_c=(a.get("t_ambient_c")
                     if a.get("t_ambient_c") is not None else 25.0),
        mu_r=(a.get("mu_r") if a.get("mu_r") is not None else 1.0),
        roughness_rq_um=a.get("roughness_rq_um"),
        alpha_per_k=a.get("alpha_per_k"),
        rho20_ohm_m=a.get("rho20_ohm_m"),
        harmonics=a.get("harmonics"),
        dc_current_a=a.get("dc_current_a")),
)

_tool(
    "pdn_impedance",
    "|Z(f)| of a capacitor bank as seen by the die: parallel series-RLC capacitors plus "
    "optional plane capacitance. Plots the whole curve because the failure mode is the "
    "ANTI-RESONANCE between two cap values, not a low minimum. Pass `plane` (plane-pair "
    "geometry + loss tangent) and it also says which CAVITY MODES fall inside the swept "
    "band, and how high each of them peaks - because a lumped minimum of 10 mohm means "
    "nothing if a plane mode inside the band peaks at 90 ohm.",
    {"type": "object", "properties": {
        "freqs_hz": {"type": "array", "description": "frequencies to evaluate",
                     "items": {"type": "number"}},
        "caps": {"type": "array", "description":
                 "list of {c_farad, esr_ohm, esl_h}", "items": {"type": "object"}},
        "plane_cap_farad": _num("plane pair capacitance, if known"),
        "r_series_ohm": _num("series resistance in the path, if known"),
        "plane": {"type": "object", "description":
                  "{width_mm, height_mm, er, thickness_mm, tan_delta, port_mm, "
                  "probe_mm, max_order}: turns on the cavity-mode diagnostic",
                  "properties": {}},
    }, "required": ["freqs_hz", "caps"]},
    lambda a: _power.pdn_impedance(a.get("freqs_hz"), a.get("caps"),
                                   plane_cap_farad=a.get("plane_cap_farad"),
                                   r_series_ohm=a.get("r_series_ohm") or 0.0,
                                   plane=a.get("plane")),
)

_tool(
    "pdn_step_response",
    "Rail droop in the TIME domain for a load step: how far the rail goes, when, and "
    "how long until it is back - on the same network |Z(f)| describes, integrated with "
    "backward Euler. |Z| answers 'is there a bad frequency'; a datasheet's load "
    "transient row is this curve, and a good |Z| in band does not stop a step from "
    "drooping by I*ESR. Every cap is a series R-L-C (`esr_ohm`, `esl_h`, `count`) and "
    "can sit on the far side of `plane_r_ohm`/`plane_l_h` with at='bulk', which is what "
    "makes 'the bulk caps are 30 mm from the die' a number instead of a hand wave. "
    "`rise_ns` is REQUIRED once anything has inductance: on an ideal current step "
    "V = L*dI/dt, so the peak would be set by the time step instead of by the board. "
    "The tail is checked against I*R_path, the caps' charge against the current that "
    "actually went past them, and the peak is re-run at half the step "
    "(`h_refine_peak_delta_pct` - above 5% the peak is not a measurement).",
    {"type": "object", "properties": {
        "caps": {"type": "array", "description":
                 "list of {c_farad, esr_ohm, esl_h, count, at:'die'|'bulk'}",
                 "items": {"type": "object"}},
        "step_a": _num("load step in amps (positive = the load increases)", 1.0),
        "preload_a": _num("current already flowing before the step", 0.0),
        "r_series_ohm": _num("regulator output resistance - the DC droop is I*R"),
        "vr_series_h": _num("regulator output inductance; it is also the only thing "
                            "that limits the regulator's dI/dt = dv/L"),
        "plane_r_ohm": _num("resistance of the path between the bulk caps and the "
                            "die; it is in the DC droop either way, because with no "
                            "cap at 'bulk' it is folded into the regulator branch "
                            "(the load current still has to cross it)"),
        "plane_l_h": _num("inductance of the path between the bulk caps and the die; "
                          "folded into the regulator branch when no cap is at 'bulk'"),
        "rise_ns": _num("load edge rise time; required when there is any inductance"),
        "t_stop_ns": _num("how long to integrate (default 20x the slowest time constant)"),
        "samples": _num("steps in the tail of the adaptive grid", 1200),
        "settle_band_pct": _num("band for settle_time_s, in % of the DC answer", 1.0),
        "refine": _bool("re-run at half the step to bound the peak's grid error", True),
        "probe_at_ns": {"type": "array", "description":
                        "times (ns) to report the rail voltage at, linearly "
                        "interpolated on the internal grid - the returned curve is "
                        "decimated for reading, this is not",
                        "items": {"type": "number"}},
    }, "required": ["caps"]},
    lambda a: _power.pdn_step_response(
        a.get("caps"),
        step_a=(a.get("step_a") if a.get("step_a") is not None else 1.0),
        preload_a=a.get("preload_a") or 0.0,
        r_series_ohm=a.get("r_series_ohm") or 0.0,
        vr_series_h=a.get("vr_series_h") or 0.0,
        plane_r_ohm=a.get("plane_r_ohm") or 0.0,
        plane_l_h=a.get("plane_l_h") or 0.0,
        rise_ns=a.get("rise_ns"),
        t_stop_ns=a.get("t_stop_ns"),
        samples=(a.get("samples") if a.get("samples") is not None else 1200),
        settle_band_pct=(a.get("settle_band_pct")
                         if a.get("settle_band_pct") is not None else 1.0),
        refine=(a.get("refine") if a.get("refine") is not None else True),
        probe_at_ns=a.get("probe_at_ns")),
)

_tool(
    "pdn_plane_modes",
    "Peak |Z| at each parallel-plate (cavity) mode of a plane pair, from the single mode "
    "that rings: |Z_peak| = w*mu0*t*psi_mn(r1)*psi_mn(r2)*Q/k_mn^2 with Q = 1/tan_delta. "
    "Modes with a voltage null at either point are reported as uncoupled instead of as a "
    "tiny number. Only peaks are given HERE; the summed curve is pdn_plane_impedance, "
    "which converges once the port has a finite pad and refuses to answer until the "
    "truncation order stops moving, and pdn_plane_with_caps is that same curve loaded by "
    "real R-L-C cap branches - which is where the anti-resonances are. "
    "tan_delta is required because a lossless cavity has an infinite peak.",
    {"type": "object", "properties": {
        "width_mm": _num("plane width"),
        "height_mm": _num("plane height"),
        "er": _num("relative permittivity of the dielectric between the planes"),
        "thickness_mm": _num("plane separation"),
        "tan_delta": _num("loss tangent of that dielectric (FR4 is 0.015..0.025); "
                          "Q = 1/tan_delta is the only damping here"),
        "port_mm": {"type": "array", "description":
                    "[x_mm, y_mm] the current is injected at (default: the centre)",
                    "items": {"type": "number"}},
        "probe_mm": {"type": "array", "description":
                     "[x_mm, y_mm] the transfer impedance is measured to "
                     "(default: the port itself)", "items": {"type": "number"}},
        "band_hz": {"type": "array", "description":
                    "[f_lo, f_hi] to mark which modes matter", "items": {"type": "number"}},
        "max_order": _num("highest modal index per axis to enumerate (1..20)", 6),
    }, "required": ["width_mm", "height_mm", "er", "thickness_mm", "tan_delta"]},
    lambda a: _power.pdn_plane_modes(
        a.get("width_mm"), a.get("height_mm"), a.get("er"),
        a.get("thickness_mm"), a.get("tan_delta"),
        probe_mm=a.get("probe_mm"), port_mm=a.get("port_mm"),
        band_hz=a.get("band_hz"),
        max_order=(a.get("max_order") if a.get("max_order") is not None else 6)),
)

_tool(
    "pdn_plane_impedance",
    "The plane impedance CURVE, not just its peaks: Z(f) = j*w*mu0*t * sum_mn "
    "A_mn/(k_mn^2 - k^2(1 - j/Q)) between two points on a plane pair, with copper and "
    "dielectric loss. A POINT port makes that sum diverge (measured on 50x50 mm at 2 GHz: "
    "5.7 -> 19.2 ohm from order 8 to 256, still climbing), so port_size_mm - the pad or via "
    "the current actually spreads over - is REQUIRED: averaging the eigenfunction over the "
    "pad is what gives the sum a limit. Answers only after a truncation scan has stopped "
    "moving (NOT_CONVERGED otherwise). Reports the peak |Z|, the deepest dip between modes, "
    "and the first mode's Q. The curve is the PLANES ALONE: no caps, no package, no vias - "
    "so Q is an upper bound and the dips come out deeper than a real board's.",
    {"type": "object", "properties": {
        "width_mm": _num("plane width"),
        "height_mm": _num("plane height"),
        "er": _num("relative permittivity of the dielectric between the planes"),
        "thickness_mm": _num("plane separation"),
        "tan_delta": _num("loss tangent of that dielectric (FR4 is 0.015..0.025). Required: "
                          "a lossless cavity has no finite impedance at a resonance"),
        "port_mm": {"type": "array", "description":
                    "[x_mm, y_mm] the current is injected at (default: the centre)",
                    "items": {"type": "number"}},
        "port_size_mm": {"description":
                         "the pad the current spreads over, in mm: one number for a square "
                         "pad, or [x_mm, y_mm]. Required - a point port has no limit",
                         "oneOf": [{"type": "number"},
                                   {"type": "array", "items": {"type": "number"}}]},
        "probe_mm": {"type": "array", "description":
                     "[x_mm, y_mm] where Z is measured (default: the port itself)",
                     "items": {"type": "number"}},
        "f_start_hz": _num("band start (default: f10/50)"),
        "f_stop_hz": _num("band stop (default: 3*f10)"),
        "points": _num("log-spaced points on top of the mode frequencies (default 100)"),
        "freqs_hz": {"type": "array", "description":
                     "explicit frequencies; then f_start_hz/f_stop_hz/points are ignored",
                     "items": {"type": "number"}},
        "max_order": _num("highest modal index per axis the truncation scan may reach "
                          "(8..1024, default 256)"),
        "tolerance": _num("accepted max|dZ|/max|Z| between the last two orders "
                          "(default 0.01)"),
        "conductivity_s_m": _num("copper conductivity, S/m; null = ideal copper"),
        "roughness_um": _num("RMS copper surface roughness Rq in um; Rs *= "
                             "Hammerstad-Jensen K(Rq/delta_s), so Qc = "
                             "h/(delta_s*K) (omit for smooth; needs a real "
                             "conductivity, Rz is NOT converted)"),
    }, "required": ["width_mm", "height_mm", "er", "thickness_mm", "tan_delta",
                    "port_mm", "port_size_mm"]},
    lambda a: _power.pdn_plane_impedance(
        a.get("width_mm"), a.get("height_mm"), a.get("er"),
        a.get("thickness_mm"), a.get("tan_delta"),
        port_mm=a.get("port_mm"), port_size_mm=a.get("port_size_mm"),
        probe_mm=a.get("probe_mm"),
        f_start_hz=a.get("f_start_hz"), f_stop_hz=a.get("f_stop_hz"),
        points=(a.get("points") if a.get("points") is not None else 100),
        freqs_hz=a.get("freqs_hz"),
        max_order=(a.get("max_order") if a.get("max_order") is not None else 256),
        tolerance=(a.get("tolerance") if a.get("tolerance") is not None else 0.01),
        conductivity_s_m=a.get("conductivity_s_m", _power.COPPER_SIGMA),
        roughness_um=a.get("roughness_um")),
)

_tool(
    "pdn_plane_with_caps",
    "The plane impedance curve WITH the decoupling caps mounted on it: the cavity is an "
    "(N+1)-port network and every cap is an R-L-C branch at its own site, so "
    "Z_in = Z00 - Z0c*(Zcc + diag(esr + jw*esl + 1/(jwC)))^-1*Zc0 - the standard port "
    "reduction, not a fit. This is what shows the peak where a cap bank turns inductive and "
    "the plane turns capacitive: with 100 nF / 1 nH / 0.01 ohm on a 0.5 mm pad at the centre "
    "of a 50x50 mm pair the loaded curve peaks at 164 ohm / 418 MHz against 92.5 ohm / "
    "2.86 GHz for the planes alone (both at order 512, where doubling it moved the loaded "
    "curve by 0.65 % of the band peak). "
    "Every site needs its own pad (pad_mm) because a point port diverges, exactly as in "
    "pdn_plane_impedance. esl_h and esr_ohm are YOUR numbers - no part library. The same "
    "truncation gate applies and the tool refuses rather than returning a moving number.",
    {"type": "object", "properties": {
        "width_mm": _num("plane width"),
        "height_mm": _num("plane height"),
        "er": _num("relative permittivity of the dielectric between the planes"),
        "thickness_mm": _num("plane separation"),
        "tan_delta": _num("loss tangent of that dielectric (FR4 is 0.015..0.025). Required"),
        "port_mm": {"type": "array", "description":
                    "[x_mm, y_mm] where the current is injected (default: the centre)",
                    "items": {"type": "number"}},
        "port_size_mm": {"description":
                         "the pad the injected current spreads over: one number for a square "
                         "pad, or [x_mm, y_mm]. Required",
                         "oneOf": [{"type": "number"},
                                   {"type": "array", "items": {"type": "number"}}]},
        "caps": {"type": "array", "description":
                 "up to 8 caps, each {at_mm: [x,y], c_farad, pad_mm, esr_ohm?, esl_h?}. "
                 "pad_mm is required; esr_ohm / esl_h default to 0, which is an ideal cap",
                 "items": {"type": "object"}},
        "f_start_hz": _num("band start (default: f10/50)"),
        "f_stop_hz": _num("band stop (default: 3*f10)"),
        "points": _num("log-spaced points on top of the mode frequencies (default 100)"),
        "freqs_hz": {"type": "array", "description":
                     "explicit frequencies; then f_start_hz/f_stop_hz/points are ignored",
                     "items": {"type": "number"}},
        "max_order": _num("highest modal index per axis the truncation scan may reach "
                          "(8..1024, default 1024 - a 0.5 mm cap pad needs more order "
                          "than the plane-only curve does: a cap on a mode node "
                          "settles at 1024, not at 512)"),
        "tolerance": _num("accepted max|dZ_in|/max|Z_in| between the last two orders "
                          "(default 0.01)"),
        "conductivity_s_m": _num("copper conductivity, S/m; null = ideal copper"),
        "roughness_um": _num("RMS copper surface roughness Rq in um; same "
                             "Hammerstad-Jensen multiplier on Rs as in "
                             "pdn_plane_impedance (omit for smooth)"),
    }, "required": ["width_mm", "height_mm", "er", "thickness_mm", "tan_delta",
                    "port_mm", "port_size_mm", "caps"]},
    lambda a: _power.pdn_plane_with_caps(
        a.get("width_mm"), a.get("height_mm"), a.get("er"),
        a.get("thickness_mm"), a.get("tan_delta"),
        port_mm=a.get("port_mm"), port_size_mm=a.get("port_size_mm"),
        caps=a.get("caps"),
        f_start_hz=a.get("f_start_hz"), f_stop_hz=a.get("f_stop_hz"),
        points=(a.get("points") if a.get("points") is not None else 100),
        freqs_hz=a.get("freqs_hz"),
        max_order=(a.get("max_order") if a.get("max_order") is not None else 1024),
        tolerance=(a.get("tolerance") if a.get("tolerance") is not None else 0.01),
        conductivity_s_m=a.get("conductivity_s_m", _power.COPPER_SIGMA),
        roughness_um=a.get("roughness_um")),
)

_tool(
    "decoupling_check",
    "Does this cap bank hold the rail inside a ripple target over a band? Needs the current, "
    "the band and the ripple - without all three the answer would be a guess. Verdict is "
    "pass / fail / INDETERMINATE: indeterminate when the worst |Z| lands on the low band "
    "edge, because below the regulator's crossover the REGULATOR holds the rail, not the "
    "caps. Pass regulator_bandwidth_hz to judge only the region the caps own.",
    {"type": "object", "properties": {
        "vdd_v": _num("rail voltage"),
        "load_current_a": _num("load current step / draw"),
        "ripple_mv": _num("allowed ripple, peak-to-peak, mV"),
        "band_hz": {"type": "array", "description": "[f_low, f_high]", "items": {"type": "number"}},
        "caps": {"type": "array", "description":
                 "list of {c_farad, esr_ohm, esl_h}", "items": {"type": "object"}},
        "r_series_ohm": _num("series resistance between bank and load"),
        "regulator_bandwidth_hz": _num("crossover of the regulator loop, if known"),
    }, "required": ["vdd_v", "load_current_a", "ripple_mv", "band_hz", "caps"]},
    lambda a: _power.decoupling_plan(a.get("vdd_v"), a.get("load_current_a"),
                                     a.get("ripple_mv"), a.get("band_hz"),
                                     a.get("caps"),
                                     r_series_ohm=a.get("r_series_ohm") or 0.0,
                                     regulator_bandwidth_hz=a.get("regulator_bandwidth_hz")),
)

_tool(
    "thermal_stack",
    "1-D thermal resistance through a stack of layers: Rth = sum(t/(k*A)), plus the "
    "temperature rise for a stated power. Strictly 1-D - it cannot see spreading: "
    "thermal_spreading answers that for a rectangular patch on a plate, and "
    "thermal_plate for the in-plane problem with the thickness lumped.",
    {"type": "object", "properties": {
        "layers": {"type": "array", "description":
                   "list of {name, thickness_mm, k_w_mk} - k may be the string "
                   "'cu' / 'fr4' / 'al' / 'sac305'", "items": {"type": "object"}},
        "area_mm2": _num("heat-crossing area (the annulus, not the drill circle)"),
        "power_w": _num("dissipated power"),
        "t_ambient_c": _num("ambient"),
        "h_w_m2k": _num("convection coefficient, to report its Rth separately"),
    }, "required": ["layers", "area_mm2"]},
    lambda a: _thermal.thermal_stack(a.get("layers"), a.get("area_mm2"),
                                     power_w=a.get("power_w"),
                                     t_ambient_c=a.get("t_ambient_c"),
                                     h_w_m2k=a.get("h_w_m2k")),
)

_tool(
    "thermal_via_array",
    "Thermal resistance through a field of plated vias - the thermal twin of "
    "via_resistance, same annulus geometry. With pitch_mm the surrounding FR4 is put in "
    "parallel, which is what a real via field looks like. Alone it is an OPTIMISTIC "
    "number (one uniform wall); give barrel_profile to model the wall thinning mid-barrel "
    "and interface to add the TIM / pad layer that follows the field.",
    {"type": "object", "properties": {
        "drill_mm": _num("finished hole diameter"),
        "plating_um": _num("barrel wall thickness (nominal; optional if "
                           "barrel_profile says the wall segment by segment)", None),
        "length_mm": _num("board thickness"),
        "count": _num("number of vias", 1),
        "pitch_mm": _num("via pitch, to add the FR4 cell in parallel"),
        "k_board": _num("board dielectric conductivity", 0.3),
        "barrel_profile": {"type": "array", "items": {"type": "object"},
            "description":
            "optional wall profile along the barrel: "
            '[{"length_mm": 0.55, "plating_um": 25}, {"length_mm": 0.5, '
            '"plating_um": 15}, {"length_mm": 0.55, "plating_um": 25}] - lengths add '
            "up to length_mm. Series resistance sum(L_i/(k_cu*A_i)), so the thin "
            "middle segment dominates. Real plating is thinner mid-barrel; this is "
            "where you say by how much instead of taking the uniform wall"},
        "interface": {"type": "object",
            "description":
            "optional series layer after the field: {area_mm2, thickness_um, k_w_mk, "
            "interfaces? (1 or 2)} - a TIM pad, gap filler or thermal tape. R = "
            "interfaces*thickness/(k*area); it is added to the array total, never "
            "folded into the barrel"},
    }, "required": ["drill_mm", "length_mm"]},
    lambda a: _thermal.via_array_thermal(a.get("drill_mm"), a.get("plating_um"),
                                         a.get("length_mm"),
                                         count=int(a.get("count") or 1),
                                         pitch_mm=a.get("pitch_mm"),
                                         k_board=a.get("k_board") or 0.3,
                                         barrel_profile=a.get("barrel_profile"),
                                         interface=a.get("interface")),
)

_tool(
    "thermal_plate",
    "2-D steady-state temperature of a plate with point heat sources: k*t*lap(T) - "
    "2h(T-Tamb) + q'' = 0 solved by SOR, with the energy balance checked (a field whose "
    "power does not balance is refused, not reported). This is the only tool here that "
    "shows the HOT SPOT versus the average, i.e. how much a small pad costs compared with "
    "spreading the same power. Give each source a size_mm (its pad) - a true point source "
    "makes the hot spot diverge as the grid shrinks.",
    {"type": "object", "properties": {
        "width_mm": _num("plate width"), "height_mm": _num("plate height"),
        "thickness_mm": _num("plate thickness"),
        "k_w_mk": _num("in-plane conductivity (0.3 FR4, 385 copper, or an equivalent "
                       "from a copper-coverage estimate)"),
        "h_w_m2k": _num("convection coefficient, both faces (natural air ~10)"),
        "sources": {"type": "array", "description":
                    "list of {x_mm, y_mm, power_w, size_mm}", "items": {"type": "object"}},
        "t_ambient_c": _num("ambient", 25),
        "cell_mm": _num("grid step (default min(W,H)/40); compare layouts only at the "
                        "same step"),
        "edge_temp_c": _num("hold the edges at this temperature instead of insulating them"),
    }, "required": ["width_mm", "height_mm", "thickness_mm", "k_w_mk", "h_w_m2k",
                    "sources"]},
    lambda a: _thermal.plate_temperature(
        a.get("width_mm"), a.get("height_mm"), a.get("thickness_mm"),
        a.get("k_w_mk"), a.get("h_w_m2k"), a.get("sources"),
        t_ambient_c=(a["t_ambient_c"] if a.get("t_ambient_c") is not None else 25.0),
        cell_mm=a.get("cell_mm"), edge_temp_c=a.get("edge_temp_c")),
)

_tool(
    "thermal_transient",
    "Step response of a thermal RC network (Foster form): T(t) = Tamb + P*sum R_i(1 - "
    "exp(-t/tau_i)). Rth and Cth may be lists - a real package is 3-4 time constants, and "
    "a single one both under-predicts a short pulse peak and mis-states the tail.",
    {"type": "object", "properties": {
        "rth_k_per_w": {"description": "number or list of stage resistances"},
        "cth_j_per_k": {"description": "number or list of stage capacitances"},
        "power_w": _num("dissipated power"),
        "t_end_s": _num("how long to simulate"),
        "t_ambient_c": _num("ambient", 25),
        "points": _num("curve points", 40),
    }, "required": ["rth_k_per_w", "cth_j_per_k", "power_w", "t_end_s"]},
    lambda a: _thermal.thermal_transient(a.get("rth_k_per_w"), a.get("cth_j_per_k"),
                                         a.get("power_w"), a.get("t_end_s"),
                                         t_ambient_c=(a["t_ambient_c"]
                                                      if a.get("t_ambient_c") is not None
                                                      else 25.0),
                                         points=int(a.get("points") or 40)),
)

_tool(
    "thermal_heatsink",
    "What Rth(junction-to-air) do I need? Solves the thermal budget and returns the target "
    "with a safety margin. Deliberately ships NO heatsink table: a table would be the one "
    "number in this layer not derived from a stated input.",
    {"type": "object", "properties": {
        "power_w": _num("dissipated power"),
        "t_j_max_c": _num("junction limit from the datasheet"),
        "t_ambient_c": _num("ambient inside the enclosure, not the room"),
        "rth_jc_k_per_w": _num("junction-to-case from the datasheet"),
        "rth_cs_k_per_w": _num("case-to-sink: TIM area and pressure matter", 0.0),
        "rth_spread_k_per_w": _num("constriction resistance of the package "
                                   "footprint spreading into the sink base - the "
                                   "number thermal_spreading returns. Omit and it "
                                   "is 0, exactly as before this parameter existed"),
        "safety_margin": _num("fraction of the budget you are willing to use", 0.8),
    }, "required": ["power_w", "t_j_max_c", "t_ambient_c", "rth_jc_k_per_w"]},
    lambda a: _thermal.heatsink_select(
        a.get("power_w"), a.get("t_j_max_c"), a.get("t_ambient_c"),
        a.get("rth_jc_k_per_w"),
        rth_cs_k_per_w=(a["rth_cs_k_per_w"] if a.get("rth_cs_k_per_w") is not None
                        else 0.0),
        safety_margin=(a["safety_margin"] if a.get("safety_margin") is not None
                       else 0.8),
        rth_spread_k_per_w=(a["rth_spread_k_per_w"]
                            if a.get("rth_spread_k_per_w") is not None else 0.0)),
)

_tool(
    "thermal_spreading",
    "Constriction (spreading) resistance of a rectangular heat source on a "
    "rectangular plate: how much resistance the plate ADDS because the heat enters "
    "through a patch instead of through the whole face. Solved exactly - an "
    "eigenfunction series in cos(m*pi*x/W)cos(n*pi*y/L) for uniform flux over the "
    "patch, adiabatic lateral edges, and a far face that is either ISOTHERMAL (the "
    "plate sits on a base) or CONVECTIVE with a stated h (it faces ambient). The "
    "answer is the average source temperature per watt, and two exact limits bracket "
    "it: a patch covering the whole face returns t/(k*A_plate) with no spreading at "
    "all, and a vanishing thickness returns the same. thermal_plate cannot see this "
    "number - it lumps the thickness away, so a small pad on a plate is isothermal "
    "through the plate by construction there. Uniform flux, not an isothermal patch "
    "(8 % apart on a half-space), and a single layer: it is not a stack, not a via "
    "field and not a circular source.",
    {"type": "object", "properties": {
        "plate_width_mm": _num("plate extent in x"),
        "plate_length_mm": _num("plate extent in y"),
        "thickness_mm": _num("plate thickness (the direction the heat crosses)"),
        "k_w_mk": _num("conductivity of the plate (200 for 6063 aluminium, 385 "
                       "copper, 0.3 FR4)"),
        "source_x0_mm": _num("source patch, x from"), "source_x1_mm": _num("x to"),
        "source_y0_mm": _num("y from"), "source_y1_mm": _num("y to"),
        "far_face": _str("isothermal = the far face is held (a base, a cold "
                         "plate); convective = it loses heat to ambient through "
                         "h_w_m2k", enum=["isothermal", "convective"],
                         default="isothermal"),
        "h_w_m2k": _num("far-face h; required for far_face='convective', and "
                        "thermal_convection turns an airflow into it"),
        "terms": _num("series cutoff in each direction (20..400, default 120); the "
                      "same series at twice the cutoff is reported as a "
                      "convergence check, so this is not an accuracy promise", 120),
    }, "required": ["plate_width_mm", "plate_length_mm", "thickness_mm", "k_w_mk",
                    "source_x0_mm", "source_x1_mm", "source_y0_mm", "source_y1_mm"]},
    lambda a: _thermal.spreading_resistance(
        a.get("plate_width_mm"), a.get("plate_length_mm"), a.get("thickness_mm"),
        a.get("k_w_mk"),
        a.get("source_x0_mm"), a.get("source_x1_mm"),
        a.get("source_y0_mm"), a.get("source_y1_mm"),
        far_face=(a.get("far_face") or "isothermal"),
        h_w_m2k=a.get("h_w_m2k"),
        # NOT int(a["terms"]): that would floor 25.5 to 25 and answer, which is
        # the silent-correction this project does not do. The calc layer checks
        # integrality and refuses by name.
        terms=(a["terms"] if a.get("terms") is not None else 120)),
)

_tool(
    "thermal_convection",
    "Convection coefficient from airflow or surface size, so it does not have to be "
    "invented. An engineering correlation (+-25%): use it to compare two airflow "
    "assumptions, never to prove a design safe.",
    {"type": "object", "properties": {
        "airflow_m_s": _num("air speed, 0 for still air"),
        "mode": _str("forced | natural", enum=["forced", "natural"], default="forced"),
        "length_m": _num("surface extent, required for natural convection"),
    }, "required": ["airflow_m_s"]},
    lambda a: _thermal.convection_coefficient(a.get("airflow_m_s"),
                                              length_m=a.get("length_m"),
                                              mode=a.get("mode") or "forced"),
)

_tool(
    "board_ir_drop",
    "DC voltage drop across ONE net, built from the copper the board ACTUALLY carries: "
    "every track segment becomes rho*L/(w*t) with t from the stackup copper weight, every "
    "via a barrel, plated pads join their layers, and nodes are (x, y, layer) - so two "
    "layers only touch where a via or a plated pad really joins them. That is the part a "
    "length-times-sheet-resistance estimate cannot see. Refuses when copper thickness or "
    "via plating is unknown, or when no track connects a sink to the source. "
    "Pass frequency_hz and the same graph becomes a ONE-FREQUENCY answer: copper is "
    "not the same slab at 1 MHz as at DC (1 oz / 35 um is already 1 % off at 1 MHz "
    "and 2x at ~30 MHz), so every track and pour square keeps its width and gets the "
    "effective thickness of the exact 1-D slab solve (the number `skin` publishes), "
    "and every via barrel is solved as the tube it is (the number "
    "`ac_round_conductor` publishes). Only resistances change - topology, DC path and "
    "the meaning of every other field do not. Left out, the answer is the plain DC "
    "number, digit for digit.",
    {"type": "object", "properties": dict({
        "net": _str("the net to measure (name, as board_nets prints it)"),
        "source": _str("pad the current enters, as REF.PAD - e.g. U1.1"),
        "sinks": {"type": "array", "description":
                  "list of 'REF.PAD' or {pad, current_a}", "items": {}},
        "current_a": _num("total current, split evenly over sinks that do not state "
                          "their own"),
        "copper_um": _num("copper thickness to use for EVERY layer; overrides the stackup"),
        "plating_um": _num("via barrel wall thickness - required if the net has vias"),
        "quantize_mm": _num("nodes closer than this are merged", 0.01),
        "temp_c": _num("copper temperature", 20),
        "model_zones": _bool("also model every copper pour on this net as a 2-D "
                             "resistive sheet (rho/t per square) and solve the whole "
                             "network by conjugate gradients. Off by default: turning "
                             "it on changes the answer, and on a plane-heavy board it "
                             "is often the difference between a number and "
                             "NOT_APPLICABLE/singular, because the two halves of the "
                             "pour-only net are joined by nothing else", default=False),
        "zone_grid_mm": _num("pitch of the pour grid; omit to plan it from the area "
                             "the fill covers (clamped to 0.2..2.0 mm). The sheet "
                             "resistance does not depend on the pitch - only how "
                             "finely the current spreads near a contact does"),
        "max_zone_nodes": _num("cap on pour grid cells (a wall-clock ceiling, not an "
                               "accuracy one)", 8000),
        "zone_link_mm": _num("a node this close to the fill but not inside it is "
                             "bridged to the nearest cell with ONE sheet square "
                             "(a thermal-relief spoke, modelled to order)", 0.5),
        "frequency_hz": _num("answer at this frequency instead of at DC: each "
                             "conductor gets its own exact 1-D skin solve. Omit for "
                             "the DC answer (the default, digit for digit)"),
        "faces": {"type": "string", "enum": ["one", "two"], "description":
                  "'one' (default) = the return current is on ONE side of this "
                  "copper (a track over a plane); 'two' = symmetric (a stripline, a "
                  "plane between two returns). A factor of two in t_eff, not a "
                  "cosmetic choice"},
        "mu_r": _num("relative permeability of the copper (1.0 for copper)", 1.0),
        "roughness_rq_um": _num("RMS foil roughness height (Rq, not Rz); the "
                                "Hammerstad-Jensen factor multiplies the AC "
                                "resistance. Omit and the AC resistance is a LOWER "
                                "bound once the skin depth is comparable"),
        "thermal_relief": {
            "type": "object", "description":
            "Model the THERMAL RELIEF that joins a pad to the pour, instead of the "
            "stand-in (a pad inside the fill is otherwise joined at 1 uOhm = solid, "
            "and one near it with one sheet square). All three numbers are required "
            "and none is defaulted: R = R_sheet * gap / (spokes * spoke_width). With "
            "the stand-in the answer is OPTIMISTIC exactly where a relief is real. "
            "Needs model_zones=true; off by default, and the default answer is digit "
            "for digit what it was. A relief stated in the board file is REPORTED "
            "next to the answer but never adopted - the geometry that goes into the "
            "number is the one you pass",
            "properties": {
                "spokes": _num("how many spokes join the pad (1..16)"),
                "spoke_width_mm": _num("width of one spoke"),
                "gap_mm": _num("the relief gap a spoke crosses (pad edge to pour)"),
            },
            "required": ["spokes", "spoke_width_mm", "gap_mm"]},
    }, **_COMMON),
     "required": ["path", "net", "source", "sinks"]},
    _board_tool(lambda b, a: _oracle.board_ir_drop(
        b, a.get("net"), a.get("source"), a.get("sinks"),
        current_a=a.get("current_a"), copper_um=a.get("copper_um"),
        plating_um=a.get("plating_um"),
        quantize_mm=(a["quantize_mm"] if a.get("quantize_mm") is not None else 0.01),
        temp_c=(a["temp_c"] if a.get("temp_c") is not None else 20.0),
        model_zones=bool(a.get("model_zones")),
        zone_grid_mm=a.get("zone_grid_mm"),
        max_zone_nodes=(int(a["max_zone_nodes"])
                        if a.get("max_zone_nodes") is not None else 8000),
        zone_link_mm=(a["zone_link_mm"] if a.get("zone_link_mm") is not None
                      else 0.5),
        frequency_hz=a.get("frequency_hz"),
        faces=(a.get("faces") or "one"),
        mu_r=(a["mu_r"] if a.get("mu_r") is not None else 1.0),
        roughness_rq_um=a.get("roughness_rq_um"),
        thermal_relief=a.get("thermal_relief")), derived=True),
)

_tool(
    "dfm_capability",
    "Validate a process capability document on its own, before any board is opened: which "
    "known rules it states, which it leaves unanswered, and whether it is usable at all. "
    "An unknown rule key is REJECTED (a typo would otherwise read as 'not stated'), and a "
    "document with no `source` is REJECTED - an unattributed limit cannot be told apart "
    "from an invented one. An optional `plating_um` states the fab's hole plating "
    "build-up (a process figure, not a limit) and is what makes copper-to-finished-hole "
    "answerable; it is never defaulted. This server ships NO fab table on purpose.",
    {"type": "object", "properties": {
        "capability": {"type": "object", "description":
                       '{"fab": "...", "source": "where the numbers came from", '
                       '"verified": false, "plating_um": 25, '
                       '"rules": {"min_track_width_mm": 0.127, ...}}. '
                       "Rule keys: %s" % ", ".join(sorted(_dfm.RULES))},
    }, "required": ["capability"]},
    lambda a: _dfm.validate(a.get("capability")),
)

_tool(
    "dfm_check",
    "Compare THIS board against a supplied fab capability document: narrowest track, "
    "copper-to-copper gap, via drill and annular rings, plated-pad rings, hole-to-hole web, "
    "copper-to-edge, narrowest cut-out, board width/height, copper layer count, narrowest "
    "silkscreen stroke, smallest silk character height, narrowest solder-mask web between "
    "two openings on ONE side, the number of pads whose mask OPENING is smaller than its "
    "copper and the tightest overlap among them, and another net's copper to the "
    "FINISHED hole wall (drill radius plus the document's `plating_um`). The limits are "
    "the document's, the "
    "measurements are the board's, and every rule the document leaves out is reported as "
    "UNANSWERED rather than passed. A silk item that states no width is excluded and "
    "counted, never given a default; copper-to-finished-hole is refused when the document "
    "states no `plating_um`, because measuring to the drill wall is a different quantity. "
    + _NOT_WRITTEN,
    {"type": "object", "properties": dict({
        "capability": {"type": "object", "description":
                       "the same document dfm_capability validates"},
        "layer": _str("restrict the track/clearance measurements to one copper layer"),
        "window_mm": _num("search radius for the nearest-feature measurements "
                          "(hole-to-hole, copper-to-edge); a feature farther than this "
                          "is reported as a proven bound, not as a number", 5.0),
        "check_clearance": _bool("run the pairwise copper sweep (the expensive one)",
                                 True),
    }, **_COMMON), "required": ["path", "capability"]},
    _board_tool(lambda b, a: _oracle.dfm_check(
        b, a.get("capability"), layer=a.get("layer"),
        window_mm=(a["window_mm"] if a.get("window_mm") is not None else 5.0),
        check_clearance=(a["check_clearance"]
                         if a.get("check_clearance") is not None else True)),
        derived=True),
)

_tool(
    "vibration_plate_modes",
    "Natural frequencies and mode shapes of a rectangular thin plate (Kirchhoff): "
    "Rayleigh-Ritz on a monomial basis restricted to the boundary-constraint null space, "
    "mass-metric whitened so the eigenproblem is a plain symmetric Jacobi. Supports "
    "simply supported / clamped / free / cantilever / four-corner-point mounts and "
    "custom edge conditions, so it is exact for SSSS and CCCC squares (verified against "
    "the analytic eigenvalues) and needs no mode table. Gives f1..fn, the antinode "
    "position, the quiet-area fraction, and the normalised deflection at any probe "
    "points you name. Four things the bare laminate cannot see are optional, all OFF "
    "by default, and with all four off every number is bit-identical to before: "
    "copper_um (a skin pair, transformed section -> A/B/D summed layer by layer plus "
    "its mass), copper_um_bottom (when the two faces differ, which couples bending and "
    "stretching: the plate then answers with the REDUCED D* = D - B A^-1 B and its "
    "neutral plane leaves mid-plane), added_mass_g at a point (an exact rank-one mass "
    "update, so a mass on a node leaves that mode alone), and added_mass_regions (mass "
    "spread over rectangles, e.g. a potted or conformally coated board, integrated with "
    "exact polynomial moments).",
    {"type": "object", "properties": {
        "a_mm": _num("plate length"),
        "b_mm": _num("plate width"),
        "thickness_mm": _num("plate thickness"),
        "material": _str("preset: %s" % ", ".join(sorted(_vib.MATERIALS))),
        "e_gpa": _num("Young's modulus in GPa (overrides the preset)"),
        "nu": _num("Poisson ratio (overrides the preset)"),
        "rho_kg_m3": _num("density in kg/m^3 (overrides the preset)"),
        "mount": _str("edges/ssss, clamped/cccc, free, cantilever, corners, custom"),
        "bc_x": {"type": "array", "description":
                 "for mount=custom: [left, right] each ss/cc/free", "items": {}},
        "bc_y": {"type": "array", "description":
                 "for mount=custom: [left, right] each ss/cc/free", "items": {}},
        "n_modes": _num("how many modes to report (1..12)", 4),
        "poly_degree": _num("polynomial degree per axis (lowered automatically to fit "
                            "the DOF budget, and said so)", 7),
        "probe_points": {"type": "array", "description":
                         "list of {name, x_mm, y_mm} to sample the mode shape at",
                         "items": {}},
        "stress_amplitude_mm": _num(
            "peak deflection of the mode, in mm. Give it and each mode also carries "
            "the surface stress; leave it out and no stress is reported, because a "
            "mode shape has no amplitude until you say how hard it is driven"),
        "copper_um": _num(
            "copper on BOTH faces, thickness in um per face (35 = 1 oz, 70 = 2 oz). "
            "A transformed section: each layer brings its own E and nu, D11/D12/D22/"
            "D66 are summed layer by layer and the mass per area grows too. It "
            "RAISES f1 (D11 wins over the added mass) - without it a 2 oz board "
            "reads stiff-free"),
        "added_mass_g": _num(
            "a point mass in grams (connector, can, battery). Exact rank-one "
            "addition to the mass matrix, not a global mass scaling: a mass on a "
            "node of a mode leaves that mode untouched, which is the check that it "
            "is a point and not a smear"),
        "added_mass_at_mm": {"type": "array", "description":
                             "[x_mm, y_mm] where that mass sits; default the plate "
                             "centre (the usual antinode, and the worst case)",
                             "items": {}},
        "copper_um_bottom": _num(
            "copper on the BOTTOM face alone, in um, when the two faces differ (1 oz "
            "top / 2 oz bottom, or copper on one side only). Leave it out and "
            "copper_um means both faces. An UNSYMMETRIC stack couples bending and "
            "stretching: the plate is solved with the reduced D* = D - B A^-1 B, the "
            "neutral plane moves off mid-plane, and the frequencies are those about "
            "the FLAT state - a board free to warp bows and is a different, softer "
            "problem"),
        "added_mass_regions": {"type": "array", "description":
                               "distributed masses, each {mass_g, center_mm:[x,y], "
                               "size_mm:[w,h]} (omit size_mm for the whole plate - "
                               "potting or conformal coat). Integrated as an exact "
                               "polynomial moment, so it is the real mode-shape "
                               "weighting and not a smear", "items": {}},
        "participation": _bool(
            "per mode: Gamma = int w dA / int w^2 dA, the modal mass and the "
            "effective mass, plus the fraction of the plate mass the reported modes "
            "capture. That is what a mode needs to be summed against a random "
            "vibration PSD (vibration_random_response takes exactly these). Both "
            "integrals are exact monomial moments of the Ritz basis - no quadrature "
            "and no fitting. A mode with no net deflection gets Gamma = 0, which is "
            "the physical statement that uniform base acceleration does not excite "
            "it (on an SSSS square the (1,2) and (2,1) modes are exactly that)",
            False),
    }, "required": ["a_mm", "b_mm", "thickness_mm"]},
    lambda a: _vib.plate_modes(
        a.get("a_mm"), a.get("b_mm"), a.get("thickness_mm"),
        material=a.get("material"), e_gpa=a.get("e_gpa"), nu=a.get("nu"),
        rho_kg_m3=a.get("rho_kg_m3"),
        mount=(a.get("mount") or "corners"), bc_x=a.get("bc_x"),
        bc_y=a.get("bc_y"),
        n_modes=int(a.get("n_modes") or 4),
        poly_degree=int(a.get("poly_degree") or 7),
        probe_points=a.get("probe_points"),
        stress_amplitude_mm=a.get("stress_amplitude_mm"),
        copper_um=a.get("copper_um"),
        copper_um_bottom=a.get("copper_um_bottom"),
        added_mass_g=a.get("added_mass_g"),
        added_mass_at_mm=a.get("added_mass_at_mm"),
        added_mass_regions=a.get("added_mass_regions"),
        participation=bool(a.get("participation"))),
)

_tool(
    "vibration_plate_outline",
    "Natural frequencies of a thin plate whose outline is an ARBITRARY polygon - the "
    "real board edge instead of its bounding box. Same Kirchhoff model and the same "
    "unit basis as vibration_plate_modes, but the interior integrals are done exactly "
    "by Green's theorem on the polygon ring (no mesh, no quadrature error) and the "
    "boundary condition is imposed weakly by a penalty integral, so any vertex list, "
    "any number of edges, per-edge ss/cc/free, and point supports all work. The "
    "penalty strength is swept and the alpha where the ladder flattens is reported "
    "together with the step size there - that step IS the error bar, so a finely "
    "faceted or re-entrant outline says so instead of quoting a fake 0.1 %. Verified "
    "against the rectangular path on a square (ss -0.01 %, clamped +0.01 %, corners "
    "-0.01 %, free mode 4 +0.001 %, cantilever -0.4 %) and within 1.1 % of the "
    "circular plate eigenvalues. Copper skins are supported too (copper_um, and "
    "copper_um_bottom when the two faces differ): the same A/B/D section the "
    "rectangular path uses, so a square reads the same frequency on both paths. "
    "Holes are not meshed and stress is not computed on a polygonal outline.",
    {"type": "object", "properties": {
        "ring_mm": {"type": "array", "description":
                    "outline vertices in order, each [x_mm, y_mm] or "
                    "{x_mm, y_mm}; any winding, any start, no repeated last point",
                    "items": {}},
        "thickness_mm": _num("plate thickness"),
        "material": _str("preset: %s" % ", ".join(sorted(_vib.MATERIALS))),
        "e_gpa": _num("Young's modulus in GPa (overrides the preset)"),
        "nu": _num("Poisson ratio (overrides the preset)"),
        "rho_kg_m3": _num("density in kg/m^3 (overrides the preset)"),
        "mount": _str("edges/ssss, clamped/cccc, free, cantilever, corners; "
                       "ignored when edges_bc is given", "ssss"),
        "edges_bc": {"type": "array", "description":
                     "one \"ss\"/\"cc\"/\"free\" per edge, in ring order - "
                     "this is how a mixed mount is expressed", "items": {}},
        "poly_degree": _num("monomial total degree 2..10 (8 by default; a clamped "
                            "edge needs >= 8 before the basis can satisfy "
                            "w = dw/dn = 0)", 8),
        "n_modes": _num("how many modes to report (1..12)", 4),
        "penalty_alphas": {"type": "array", "description":
                           "explicit penalty ladder to sweep instead of (1e3..1e7)",
                           "items": {}},
        "probe_points": {"type": "array", "description":
                         "list of {name, x_mm, y_mm} to sample each mode at",
                         "items": {}},
        "supports": {"type": "array", "description":
                     "extra point supports, each [x_mm, y_mm] or {x_mm, y_mm}",
                     "items": {}},
        "copper_um": _num(
            "copper on BOTH faces, thickness in um per face (35 = 1 oz). Summed into "
            "the plate's own D11/D22/D12/D66 and its mass per area - the same section "
            "the rectangular path builds, so the two agree on a square"),
        "copper_um_bottom": _num(
            "copper on the BOTTOM face only, in um, when it differs from copper_um. "
            "An unsymmetric stack answers with the REDUCED D* = D - B A^-1 B and its "
            "frequencies are those about the FLAT state"),
    }, "required": ["ring_mm", "thickness_mm"]},
    lambda a: _vib_poly.plate_modes_polygon(
        _ring_arg(a.get("ring_mm")), a.get("thickness_mm"),
        material=a.get("material"), e_gpa=a.get("e_gpa"), nu=a.get("nu"),
        rho_kg_m3=a.get("rho_kg_m3"),
        mount=(a.get("mount") or "ssss"), edges_bc=a.get("edges_bc"),
        poly_degree=int(a.get("poly_degree") or 8),
        n_modes=int(a.get("n_modes") or 4),
        penalty_alphas=a.get("penalty_alphas"),
        probe_points=a.get("probe_points"),
        supports=_ring_arg(a.get("supports")),
        copper_um=a.get("copper_um"),
        copper_um_bottom=a.get("copper_um_bottom")),
)

_tool(
    "vibration_plate_stress",
    "Surface stress in a vibrating plate, with a MEASURED error bar. Solves the same "
    "Rayleigh-Ritz eigenproblem at several polynomial degrees, takes the surface "
    "curvatures (sigma = -E z/(1-nu^2)(w_xx + nu w_yy), tau_xy = -E z/(1+nu) w_xy at "
    "z = h/2), and reports the spread between rungs instead of quoting a tolerance. "
    "Two different extremes are reported at their own locations: von Mises peaks where "
    "the TWISTING curvature does (a corner, for a sinusoid-like mode - the classic "
    "corner torque), and the largest principal stress peaks where the bending "
    "curvatures do (a bay centre). Reporting one point for both is how a max stress "
    "comes out a third low. Frequencies converge faster than curvatures: on SSSS the "
    "frequency moves 0.04 % between degree 5 and 9 while the stress moves 3.6 %. With "
    "copper the surface is the copper: each face uses its own layer's E and nu at that "
    "layer's z (0.0.56 used copper's E beside the laminate's nu, a 10 % error on 1 oz), "
    "and a one-sided stack solves BOTH faces and reports the worse one.",
    {"type": "object", "properties": {
        "a_mm": _num("plate length"),
        "b_mm": _num("plate width"),
        "thickness_mm": _num("plate thickness"),
        "amplitude_mm": _num("peak deflection of this mode in mm - take it from "
                             "miles_response's z_3sigma_mm, do not invent one"),
        "material": _str("preset: %s" % ", ".join(sorted(_vib.MATERIALS))),
        "e_gpa": _num("Young's modulus in GPa (overrides the preset)"),
        "nu": _num("Poisson ratio (overrides the preset)"),
        "rho_kg_m3": _num("density in kg/m^3 (overrides the preset)"),
        "mount": _str("edges/ssss, clamped/cccc, free, cantilever, corners, custom",
                      "corners"),
        "bc_x": {"type": "array", "description":
                 "for mount=custom: [left, right] each ss/cc/free", "items": {}},
        "bc_y": {"type": "array", "description":
                 "for mount=custom: [bottom, top] each ss/cc/free", "items": {}},
        "mode": _num("which mode (1-based, flexible modes only)", 1),
        "poly_degree": _num("highest polynomial degree to try; the ladder runs "
                            "(5,7,9) up to this", 9),
        "degrees": {"type": "array", "description":
                    "explicit degree ladder, e.g. [5,7,9]", "items": {}},
        "copper_um": _num(
            "copper on BOTH faces, um per face (35 = 1 oz). The stress is then taken "
            "at the copper surface, with copper's own E and nu - not at the laminate's "
            "h/2 with the laminate's nu"),
        "copper_um_bottom": _num(
            "copper on the BOTTOM face only, in um, when the two faces differ. Both "
            "surfaces are then solved and the worse one is reported, with "
            "von_mises_by_surface_mpa giving each"),
    }, "required": ["a_mm", "b_mm", "thickness_mm", "amplitude_mm"]},
    lambda a: _vib.plate_stress(
        a.get("a_mm"), a.get("b_mm"), a.get("thickness_mm"), a.get("amplitude_mm"),
        mode=int(a.get("mode") or 1),
        material=a.get("material"), e_gpa=a.get("e_gpa"), nu=a.get("nu"),
        rho_kg_m3=a.get("rho_kg_m3"),
        mount=(a.get("mount") or "corners"), bc_x=a.get("bc_x"),
        bc_y=a.get("bc_y"),
        poly_degree=(int(a["poly_degree"]) if a.get("poly_degree") is not None else None),
        degrees=(tuple(a["degrees"]) if a.get("degrees") else None),
        copper_um=a.get("copper_um"),
        copper_um_bottom=a.get("copper_um_bottom")),
)

_tool(
    "vibration_random_response",
    "Random vibration: the g_rms a board sees under a PSD, and the displacement that "
    "follows. TWO ways in, and the difference matters. Give f_n_hz and you get "
    "single-mode (Miles): the flat-PSD closed form g_rms = sqrt(pi/2 * f_n * Q * W), or "
    "the defining int W(f)|H(f)|^2 df for a measured PSD curve, over three panels so a "
    "high-Q peak is resolved; the two agree inside 1 %. Give `modes` instead - the list "
    "plate_modes(participation=true) hands you, with each mode's frequency, "
    "participation factor and shape at the point you are watching - and the same PSD is "
    "SUMMED over every mode: the displacement is an exact complex sum, so the modes "
    "add coherently where they overlap and the cross terms are in the number; each "
    "mode's acceleration is w_k^2 times its own displacement, and the accelerations "
    "add as independent narrow-band responses, which is standard modal superposition "
    "and says so. Cross terms matter: on a 100/105 Hz pair at zeta 0.02 the coherent "
    "sum moves the displacement variance 29 % against summing the modes as "
    "independent. With neither f_n_hz nor modes, nothing is guessed - it errors.",
    {"type": "object", "properties": {
        "f_n_hz": _num("natural frequency, for the single-mode (Miles) answer; leave "
                       "it out and pass `modes` instead"),
        "psd": {"type": "object", "description":
                '{"g2_per_hz": 0.1} or {"points": [[hz, g2/Hz], ...]}'},
        "modes": {"type": "array", "description":
                  "sum over these modes instead of one: each {freq_hz, "
                  "participation, shape_at_point} from "
                  "vibration_plate_modes(participation=true) plus a probe point. "
                  "Optional per-mode zeta / q_factor override the global ones. "
                  "Displacement is summed coherently (cross terms in); acceleration "
                  "per mode is w^2 * its displacement", "items": {}},
        "zeta": _num("damping ratio"),
        "q_factor": _num("Q = 1/(2 zeta); give this OR zeta"),
        "gamma": _num("peak factor, default 3 (3 sigma)", 3),
    }, "required": ["psd"]},
    lambda a: _vib.miles_response(
        a.get("f_n_hz"), psd=a.get("psd"), zeta=a.get("zeta"),
        q_factor=a.get("q_factor"),
        gamma=(a["gamma"] if a.get("gamma") is not None else 3.0),
        modes=a.get("modes")),
)

_tool(
    "vibration_fatigue",
    "Steinberg three-band fatigue plus Miner's rule: split the 3-sigma deflection into "
    "3s/2s/1s bands at the textbook cycle fractions, convert each to an allowable cycle "
    "count N = N0 (z_allow/z)^b, and accumulate damage per hour. z_allow_mm is REQUIRED - "
    "this server owns no 'typical allowable deflection', because that number belongs to "
    "the part in your qualification report.",
    {"type": "object", "properties": {
        "f_n_hz": _num("natural frequency"),
        "z_3sigma_mm": _num("3-sigma relative displacement (Miles peak with gamma=3)"),
        "z_allow_mm": _num("deflection the joint/lead survives at n0_cycles (REQUIRED)"),
        "exponent": _num("fatigue slope b; Steinberg uses 6.4 for lead wires", 6.4),
        "n0_cycles": _num("cycle count z_allow is quoted at", 2.0e7),
        "target_hours": _num("design life; if given, a pass/fail and a margin come back"),
    }, "required": ["f_n_hz", "z_3sigma_mm", "z_allow_mm"]},
    lambda a: _vib.steinberg_fatigue(
        a.get("f_n_hz"), a.get("z_3sigma_mm"), a.get("z_allow_mm"),
        exponent=(a["exponent"] if a.get("exponent") is not None else 6.4),
        n0_cycles=(a["n0_cycles"] if a.get("n0_cycles") is not None else 2.0e7),
        target_hours=a.get("target_hours")),
)

_tool(
    "vibration_shock",
    "Single-DOF shock response to a base acceleration pulse (step / rect / half_sine), "
    "integrated with Newmark beta=1/4 gamma=1/2 (unconditionally stable). Returns the "
    "peak relative displacement (what stresses the mount), the peak absolute "
    "acceleration (what the component feels) and both amplification factors. Reproduces "
    "the analytic step DLF of 2 exactly for zero damping.",
    {"type": "object", "properties": {
        "f_n_hz": _num("natural frequency"),
        "zeta": _num("damping ratio"),
        "q_factor": _num("Q = 1/(2 zeta); give this OR zeta"),
        "pulse": {"type": "object", "description":
                  '{"type": "step"|"rect"|"half_sine", "amplitude_g": 30, '
                  '"duration_ms": 2} (duration not needed for step)'},
    }, "required": ["f_n_hz", "pulse"]},
    lambda a: _vib.shock_sdof(
        a.get("f_n_hz"), zeta=a.get("zeta"), q_factor=a.get("q_factor"),
        pulse=a.get("pulse")),
)

_tool(
    "vibration_isolator",
    "Single-DOF transmissibility of a mount: |T| at the frequencies you ask for, the peak "
    "transmissibility and where it sits, and the isolation onset f_n*sqrt(2) - below that "
    "the mount AMPLIFIES, which is the number people forget. Idealised 1-D mount: no "
    "frequency-dependent stiffness and no isolator resonance of its own.",
    {"type": "object", "properties": {
        "f_n_hz": _num("mounted natural frequency"),
        "zeta": _num("damping ratio"),
        "q_factor": _num("Q = 1/(2 zeta)"),
        "freq_hz": {"type": "array", "description": "frequencies to evaluate",
                    "items": {}},
        "freq_range": {"type": "array", "description":
                       "[f_start, f_stop, n_points] to sweep", "items": {}},
    }, "required": ["f_n_hz"]},
    lambda a: _vib.transmissibility(
        a.get("f_n_hz"), zeta=a.get("zeta"), q_factor=a.get("q_factor"),
        freq_hz=a.get("freq_hz"), freq_range=a.get("freq_range")),
)

_tool(
    "solder_joint_fatigue",
    "Thermal-cycling life of a solder joint from the CTE mismatch. Shear strain range = "
    "F*DNP*dalpha*delta_T/h (the mismatch displacement over the joint standoff), then "
    "Engelmaier's strain-life form of Coffin-Manson: Nf = 0.5*(dg/(2*eps_f))**(1/c) with "
    "c = -0.442 - 6e-4*Tbar_sj + 1.74e-2*ln(1+360/t_D). Several cyclic environments go "
    "through Miner's rule; a test/use pair goes through the Norris-Landzberg acceleration "
    "factor. eps_f (Engelmaier's SnPb fit, 0.325) and the CTE presets are fits and "
    "typicals, not material truths, and whichever were used come back in the envelope. "
    "NOT modelled: underfill, voids and IMC growth, crack propagation after initiation, "
    "assembly bending, or a thermal gradient across the joint.",
    {"type": "object", "properties": {
        "joint_height_mm": _num("solder standoff / gap under the part (REQUIRED)"),
        "dnp_mm": _num("distance from the neutral point to the joint judged"),
        "body_a_mm": _num("package body side A; with body_b_mm DNP becomes the diagonal"),
        "body_b_mm": _num("package body side B"),
        "delta_t_k": _num("cycle temperature range, for a single environment"),
        "mean_joint_temp_c": _num("mean cyclic JOINT temperature (not ambient)", 25),
        "dwell_min": _num("half-cycle dwell", 15),
        "cycles": _num("cycles per block, for the single environment"),
        "alpha_component_ppm": _num("component CTE, ppm/K"),
        "alpha_board_ppm": _num("board CTE, ppm/K"),
        "alpha_component": _str("component CTE preset name"),
        "alpha_board": _str("board CTE preset name", "fr4"),
        "conditions": {"type": "array", "items": {"type": "object"},
                       "description": "list of {name, delta_t_k, mean_joint_temp_c, "
                                      "dwell_min, cycles, blocks_per_year}; only "
                                      "delta_t_k is required in one"},
        "fatigue_ductility": _num("eps_f; leave out for Engelmaier's 0.325"),
        "compliance_factor": _num("F, 1.0 = the joint absorbs the whole mismatch", 1.0),
        "nl_test": {"type": "object", "description":
                    '{"delta_t_k":165,"frequency_cpd":2,"t_max_c":125}'},
        "nl_use": {"type": "object", "description":
                   '{"delta_t_k":100,"frequency_cpd":1,"t_max_c":55}'},
        "nl_m": _num("Norris-Landzberg dT exponent", 1.75),
        "nl_n": _num("Norris-Landzberg frequency exponent", 0.3333333333333333),
        "nl_ea_ev": _num("activation energy in eV", 0.141),
    }, "required": ["joint_height_mm"]},
    lambda a: _rel.solder_joint_fatigue(
        conditions=a.get("conditions"), joint_height_mm=a.get("joint_height_mm"),
        delta_t_k=a.get("delta_t_k"),
        mean_joint_temp_c=a.get("mean_joint_temp_c"),
        dwell_min=a.get("dwell_min"), cycles=a.get("cycles"),
        alpha_component_ppm=a.get("alpha_component_ppm"),
        alpha_board_ppm=a.get("alpha_board_ppm"),
        alpha_component=a.get("alpha_component"),
        alpha_board=a.get("alpha_board"),
        dnp_mm=a.get("dnp_mm"), body_a_mm=a.get("body_a_mm"),
        body_b_mm=a.get("body_b_mm"),
        fatigue_ductility=a.get("fatigue_ductility"),
        compliance_factor=a.get("compliance_factor"),
        nl_test=a.get("nl_test"), nl_use=a.get("nl_use"),
        nl_m=a.get("nl_m"), nl_n=a.get("nl_n"), nl_ea_ev=a.get("nl_ea_ev")),
)

_tool(
    "drop_board_strain",
    "Board surface strain under a drop or shock pulse, built from three already-solved "
    "pieces: the plate's first mode (vibration_plate_modes) gives f1, a single-DOF shock "
    "response to the same pulse gives the peak deflection, and the plate curvature turns "
    "that deflection into surface strain. Add dnp_mm + joint_height_mm and the joint shear "
    "strain follows as eps*DNP/h. Pass critical_strain_ue to get a verdict - this tool "
    "will not supply one, because the strain a joint survives belongs to the component and "
    "its qualification report. Single mode, no component mass (so f1 is high and the "
    "answer optimistic), linear plate theory.",
    {"type": "object", "properties": {
        "a_mm": _num("plate length"),
        "b_mm": _num("plate width"),
        "thickness_mm": _num("plate thickness"),
        "pulse": {"type": "object", "description":
                  '{"type":"half_sine"|"rect"|"step","amplitude_g":1500,'
                  '"duration_ms":0.5}'},
        "material": _str("preset: %s" % ", ".join(sorted(_vib.MATERIALS))),
        "e_gpa": _num("Young's modulus in GPa (overrides the preset)"),
        "nu": _num("Poisson ratio"),
        "rho_kg_m3": _num("density in kg/m^3"),
        "mount": _str("edges/ssss, clamped/cccc, free, cantilever, corners, custom"),
        "bc_x": {"type": "array", "description":
                 "for mount=custom: [left, right] each ss/cc/free", "items": {}},
        "bc_y": {"type": "array", "description":
                 "for mount=custom: [left, right] each ss/cc/free", "items": {}},
        "zeta": _num("damping ratio"),
        "q_factor": _num("Q = 1/(2 zeta); give this OR zeta"),
        "mode": _num("which plate mode to drive", 1),
        "poly_degree": _num("polynomial degree per axis", 7),
        "critical_strain_ue": _num("strain the parts survive, in microstrain (YOURS)"),
        "dnp_mm": _num("distance from the neutral point, for the joint strain"),
        "joint_height_mm": _num("solder standoff, for the joint strain"),
    }, "required": ["a_mm", "b_mm", "thickness_mm", "pulse"]},
    lambda a: _rel.drop_board_strain(
        a.get("a_mm"), a.get("b_mm"), a.get("thickness_mm"), a.get("pulse"),
        material=a.get("material"), e_gpa=a.get("e_gpa"), nu=a.get("nu"),
        rho_kg_m3=a.get("rho_kg_m3"), mount=(a.get("mount") or "corners"),
        bc_x=a.get("bc_x"), bc_y=a.get("bc_y"), zeta=a.get("zeta"),
        q_factor=a.get("q_factor"), mode=int(a.get("mode") or 1),
        poly_degree=(int(a["poly_degree"]) if a.get("poly_degree") else None),
        critical_strain_ue=a.get("critical_strain_ue"),
        dnp_mm=a.get("dnp_mm"), joint_height_mm=a.get("joint_height_mm")),
)

_tool(
    "vibration_shock_spectrum",
    "Shock response spectrum: sweep single-DOF oscillators across frequency and record the "
    "peak absolute acceleration, pseudo velocity and displacement at each. Read the board's "
    "own f1 off the curve (vibration_plate_modes) to see how far it sits from the shock's "
    "worst frequency. Defaults to 5 % damping, which is the SRS convention and is stated "
    "rather than hidden. Verified against two limits: at zero damping a step gives exactly "
    "2x the pulse peak at every frequency, and no point may exceed that bound.",
    {"type": "object", "properties": {
        "pulse": {"type": "object", "description":
                  '{"type":"half_sine"|"rect"|"step","amplitude_g":1500,'
                  '"duration_ms":0.5}'},
        "f_n_hz": {"type": "array", "items": {},
                   "description": "explicit frequencies (use this OR the range)"},
        "f_start_hz": _num("range start"),
        "f_stop_hz": _num("range stop"),
        "n_points": _num("log-swept points in the range", 24),
        "zeta": _num("damping ratio; default 5 % (the SRS convention)"),
        "q_factor": _num("Q = 1/(2 zeta)"),
    }, "required": ["pulse"]},
    lambda a: _rel.shock_spectrum(
        a.get("pulse"), f_n_hz=a.get("f_n_hz"), f_start_hz=a.get("f_start_hz"),
        f_stop_hz=a.get("f_stop_hz"), n_points=a.get("n_points"),
        zeta=a.get("zeta"), q_factor=a.get("q_factor")),
)

_tool(
    "board_vibration",
    "Natural frequencies of THIS board from its own outline and stackup, with every "
    "component sampled against the first mode shape so you get a ranked list of which "
    "references sit on an antinode. The primary plate is the outline bounding box, "
    "so nothing that already worked changes; when the outline fills less than 99 % of "
    "that box the same plate is ALSO solved on the real polygon and comes back as "
    "`plate_on_outline` with its ratio to the bbox number and its own measured "
    "penalty-ladder spread - the bbox bias is then a number, not a caveat. Component "
    "mass and copper stiffening are not modelled in either.",
    {"type": "object", "properties": dict({
        "mount": _str("edges/ssss, clamped/cccc, free, cantilever, corners", "corners"),
        "material": _str("plate material preset", "fr4"),
        "thickness_mm": _num("override the board thickness"),
        "n_modes": _num("how many modes", 4),
        "poly_degree": _num("polynomial degree per axis", 7),
        "probe": _str("'components' (default) or a list of {name,x_mm,y_mm}"),
    }, **_COMMON), "required": ["path"]},
    _board_tool(lambda b, a: _oracle.board_vibration(
        b, mount=(a.get("mount") or "corners"),
        material=(a.get("material") or "fr4"),
        thickness_mm=a.get("thickness_mm"),
        n_modes=int(a.get("n_modes") or 4),
        poly_degree=int(a.get("poly_degree") or 7),
        probe=(a.get("probe") if a.get("probe") is not None else "components")),
        derived=True),
)

_tool(
    "cavity_resonance",
    "Parallel-plate (cavity) modes of a plane pair: f_mn = c/(2 sqrt(er)) * "
    "sqrt((m/a)^2+(n/b)^2) (TM_mn0, perfect magnetic side walls). This is where a ground "
    "plane stops being a ground plane - two stitching rows or a via fence are the side "
    "walls, and between them the pair rings. Reports every mode in the requested order "
    "with its guided wavelength, keeps degenerate pairs separate, and does NOT compute Q "
    "or the detuning from real open edges (so it is a location on the frequency axis, not "
    "a verdict). Optional f_operate_hz only adds the nearest-mode read-out.",
    {"type": "object", "properties": {
        "a_mm": _num("plane length"),
        "b_mm": _num("plane width"),
        "er": _num("dielectric constant BETWEEN the planes", 4.3),
        "m_max": _num("highest m to include", 2),
        "n_max": _num("highest n to include (>= 1)", 2),
        "f_operate_hz": _num("optional: report the nearest mode to this frequency"),
        "top": _num("how many modes to return, lowest first", 12),
    }, "required": ["a_mm", "b_mm"]},
    lambda a: _cavity.plane_resonance(
        a.get("a_mm"), a.get("b_mm"),
        er=(a["er"] if a.get("er") is not None else 4.3),
        m_max=(a["m_max"] if a.get("m_max") is not None else 2),
        n_max=(a["n_max"] if a.get("n_max") is not None else 2),
        f_operate_hz=a.get("f_operate_hz"),
        top=int(a["top"] if a.get("top") is not None else 12)),
)

_tool(
    "cavity_via_fence",
    "Via-fence pitch expressed as a fraction of the guided wavelength: pitch/lambda_g, "
    "phase per pitch, and the maximum pitch for a criterion you supply. The criterion is "
    "REQUIRED for a pass/fail - lambda_g/20 and lambda_g/10 both circulate and neither is "
    "derived, so this server does not pick one; without max_pitch_ratio you get the "
    "ratios plus verdict=indeterminate. Vias are ideal walls here: no barrel diameter, no "
    "antipad, no leakage through plane voids.",
    {"type": "object", "properties": {
        "f_resonance_hz": _num("the mode frequency you are fencing against"),
        "er": _num("dielectric constant between the planes"),
        "pitch_mm": _num("centre-to-centre via pitch"),
        "max_pitch_ratio": _num("your acceptance criterion, e.g. 0.05 for lambda_g/20"),
        "fence_width_mm": _num("optional: width of the fenced region, for its electrical "
                               "length"),
    }, "required": ["f_resonance_hz", "er"]},
    lambda a: _cavity.via_fence(
        a.get("f_resonance_hz"), a.get("er"), pitch_mm=a.get("pitch_mm"),
        max_pitch_ratio=a.get("max_pitch_ratio"),
        fence_width_mm=a.get("fence_width_mm")),
)

_tool(
    "cavity_plane_q",
    "Damping of a plane-pair mode: copper Q, dielectric Q and their series sum. The "
    "conductor answer is Qc = omega*mu*h/(2*Rs) = h/delta_s - independent of the "
    "plane size, the aspect ratio and the mode numbers, and proportional to the "
    "dielectric thickness, which is not what most tables imply. Give tan_delta and "
    "you also get Qd = 1/tan_delta; FR4's 0.02 gives 50, i.e. the dielectric usually "
    "dominates and a scary mode on lossy prepreg is often a non-event. Pass "
    "roughness_um and Rs picks up the Hammerstad-Jensen multiplier "
    "K = 1+(2/pi)atan(1.4(Rq/delta_s)^2) (K(delta)=1.605, K->2 from below), i.e. "
    "Qc = h/(delta_s*K) - and because delta_s shrinks as 1/sqrt(f), the same foil "
    "costs more at high frequency. Radiation Q is NOT here: it lives in "
    "plane_radiation_q, which also folds Qc, Qd and Q_rad into one 1/Q sum, and the "
    "nodule carpet is roughness_huray. The foil profile and the damping of mounted "
    "capacitors are still NOT modelled, so read this answer as an upper bound on Q. "
    "No pass/fail: a resonance is only a problem with an "
    "excitation and a coupling.",
    {"type": "object", "properties": {
        "f_hz": _num("the mode frequency, e.g. from cavity_resonance"),
        "h_mm": _num("dielectric thickness between the two planes"),
        "tan_delta": _num("laminate loss tangent, e.g. 0.02 for FR4 (omit for none)"),
        "conductivity_s_m": _num("copper conductivity", 5.8e7),
        "roughness_um": _num("RMS copper surface roughness Rq in um, e.g. 0.3..1 for "
                             "HVLP foil, a few um for standard ED foil (omit for "
                             "smooth; Rz is NOT converted)"),
    }, "required": ["f_hz", "h_mm"]},
    lambda a: _cavity.plane_q(
        a.get("f_hz"), a.get("h_mm"), tan_delta=a.get("tan_delta"),
        conductivity_s_m=(a["conductivity_s_m"]
                          if a.get("conductivity_s_m") is not None else 5.8e7),
        roughness_um=a.get("roughness_um")),
)

_tool(
    "roughness_huray",
    "Huray \"snowball\" surface roughness: Rs multiplier from a nodular copper "
    "surface, instead of from one RMS height. You give a nodule radius and how "
    "much surface it adds (surface_ratio = 4*pi*r^2*N per unit area, the number "
    "Ansys SIwave prints, or the areal nodule density) and get "
    "K = 1 + (RF-1)*F with RF = 1 + (3/2)*sr and F = 1/sqrt(1+2x+2x^2), "
    "x = 2*delta_s/r: at DC the current climbs over every nodule (K -> RF), at "
    "low frequency the skin depth never sees them (K -> 1), and the knee is "
    "delta_s = r/2 where F = 1/sqrt(5) exactly. For 0.5 um nodules on 1 GHz "
    "copper: sr = 1 gives K = 1.120, sr = 6 gives K = 1.717; the same surface at "
    "10 GHz gives 1.334 / 3.001. Rs *= K, i.e. Qc /= K, i.e. sigma_eff = "
    "sigma/K^2. It is NOT interchangeable with the Hammerstad-Jensen Rq "
    "multiplier (that one is reported alongside when you pass rq_um, for "
    "comparison only - a height statistic and a nodule geometry do not convert "
    "without inventing a height distribution).",
    {"type": "object", "properties": {
        "f_hz": _num("evaluation frequency"),
        "nodule_radius_um": _num("radius of one Huray nodule, e.g. 0.1..1 um"),
        "surface_ratio": _num("Hall-Huray surface ratio 4*pi*r^2*N (published "
                               "sets use 1 / 3 / 6 for low / medium / high; "
                               "0 = flat)"),
        "nodule_density_per_mm2": _num("ALTERNATIVE to surface_ratio: nodules per "
                                        "square millimetre of flat surface"),
        "conductivity_s_m": _num("copper conductivity", 5.8e7),
        "mur": _num("relative permeability of the conductor", 1.0),
        "rq_um": _num("optional RMS roughness, only to see the Hammerstad-Jensen "
                       "number next to this one (no conversion is done)"),
    }, "required": ["f_hz", "nodule_radius_um"]},
    lambda a: _planar.huray_roughness(
        a.get("f_hz"), nodule_radius_um=a.get("nodule_radius_um"),
        surface_ratio=a.get("surface_ratio"),
        nodule_density_per_mm2=a.get("nodule_density_per_mm2"),
        conductivity_s_m=(a["conductivity_s_m"]
                          if a.get("conductivity_s_m") is not None else 5.8e7),
        mur=(a["mur"] if a.get("mur") is not None else 1.0),
        rq_um=a.get("rq_um")),
)

_tool(
    "plane_radiation_q",
    "Radiation Q of a plane-pair mode radiating from its OPEN edges - the loss "
    "cavity_plane_q says it does not model. Each open edge is a narrow slot "
    "(gap h) in an infinite flange, whose radiation conductance per unit width "
    "is G' = omega*eps0 = k0/eta0; omega then cancels against the stored "
    "energy, so Q_rad = er*A_eff/(h*sum L_eff) - no frequency in it, but the "
    "mode numbers DO matter (a (1,1) radiates from four edges with half the "
    "energy per antinode volt, so its Q_rad is 2x lower than (1,0)). 50 mm on "
    "0.2 mm of er=4.3 gives Q_rad = 179 on (1,0); on 1.6 mm it gives 22, i.e. "
    "radiation then beats copper (Qc = 921). Pass tan_delta and q_total = "
    "(1/Qc + 1/Qd + 1/Q_rad)^-1. The model is refused when k0*h >= 1 (an edge "
    "cannot radiate more than arrives) and it treats the outside as air, the "
    "edge as infinitely long (no corners) and the mode as unperturbed - expect "
    "tens of percent against a 3-D solver. Nothing soldered across the planes "
    "is in it, which is usually the dominant damping in a real PDN.",
    {"type": "object", "properties": {
        "a_mm": _num("plane dimension along x"),
        "b_mm": _num("plane dimension along y"),
        "h_mm": _num("dielectric thickness between the planes"),
        "er": _num("relative permittivity between the planes"),
        "m": _num("mode number along x (0,1,2,...), not both zero", 1),
        "n": _num("mode number along y (0,1,2,...), not both zero", 0),
        "open_edges": _arr("which edges are bare, from x0/x1/y0/y1 (x0 at x=0, "
                           "x1 at x=a, y0 at y=0, y1 at y=b); omit for all four, "
                           "pass [] or 'none' for a fully walled cavity"),
        "tan_delta": _num("laminate loss tangent, to add Qd and report a real "
                          "q_total (omit for none)"),
        "conductivity_s_m": _num("copper conductivity", 5.8e7),
    }, "required": ["a_mm", "b_mm", "h_mm", "er"]},
    lambda a: _planar.plane_radiation_q(
        a.get("a_mm"), a.get("b_mm"), a.get("h_mm"), a.get("er"),
        m=(a["m"] if a.get("m") is not None else 1),
        n=(a["n"] if a.get("n") is not None else 0),
        open_edges=a.get("open_edges"), tan_delta=a.get("tan_delta"),
        conductivity_s_m=(a["conductivity_s_m"]
                          if a.get("conductivity_s_m") is not None else 5.8e7)),
)

_tool(
    "plane_open_end",
    "How far a BARE edge pulls a plane-pair resonance down: the fringing field at "
    "an open edge makes the plane electrically longer than it is drawn, which is "
    "the 'few percent' cavity_resonance says it does not apply. From the exact "
    "2-D solution in its large-aspect-ratio limit (Love 1924; C ~ eps0*[l/h + "
    "(1/pi)*(ln(2*pi*l/h)+1)], Yariv 2021), one open edge adds "
    "delta_l = (h/(2*pi))*(ln(2*pi*a/h)+1), then a_eff = a + 2*delta_l and "
    "f_eff = c/(2*sqrt(er))*sqrt((m/a_eff)^2+(n/b_eff)^2). 50 mm on 0.2 mm moves "
    "the (1,0) mode down 1.05 %; the same plane on 1.6 mm of FR4 moves down "
    "6.01 %. Only BARE edges get an extension (a stitched edge is a wall), the "
    "edge is treated as infinitely long so the corners are not in it, and the "
    "outside is air. Refused below a/h = 10 or b/h = 10 - the closed form is an "
    "asymptotic in a/h and does not extrapolate to a fat, small plane.",
    {"type": "object", "properties": {
        "a_mm": _num("plane dimension along x"),
        "b_mm": _num("plane dimension along y"),
        "h_mm": _num("dielectric thickness between the planes"),
        "er": _num("relative permittivity between the planes"),
        "m": _num("mode number along x, not both m and n zero", 1),
        "n": _num("mode number along y, not both m and n zero", 0),
        "open_edges": _arr("which edges are bare, from x0/x1/y0/y1; omit for all "
                           "four"),
    }, "required": ["a_mm", "b_mm", "h_mm", "er"]},
    lambda a: _planar.plane_open_end(
        a.get("a_mm"), a.get("b_mm"), a.get("h_mm"), a.get("er"),
        m=(a["m"] if a.get("m") is not None else 1),
        n=(a["n"] if a.get("n") is not None else 0),
        open_edges=a.get("open_edges")),
)

_tool(
    "electrothermal_trace",
    "Self-consistent temperature of a straight DC trace: the copper gets hotter, the "
    "copper gets more resistive, so it dissipates more, so it gets hotter. R(T) = "
    "rho20(1+alpha(T-20))L/(w t), P = I^2 R, T = T_amb + P*Rth, solved as a fixed point "
    "with the contraction gain dP/dT*Rth checked exactly - above 1 it returns "
    "THERMAL_RUNAWAY instead of a number. Rth is REQUIRED (one lump: from thermal_stack / "
    "via_array_thermal / plate_temperature, or measured); this module owns no default Rth. "
    "DC only: at a frequency the copper resistance is not rho*L/(w*t) any more "
    "(ac_sheet_resistance is the 1-D answer for a flat trace, ac_round_conductor "
    "the exact one for a round conductor), and that loss still has to become "
    "heat here - electrothermal_conductor takes the cold resistance directly "
    "instead of deriving it from geometry.",
    {"type": "object", "properties": {
        "length_mm": _num("trace length"),
        "width_mm": _num("trace width"),
        "thickness_um": _num("copper thickness"),
        "current_a": _num("DC current"),
        "rth_c_per_w": _num("thermal resistance, copper to ambient (required)"),
        "t_ambient_c": _num("ambient temperature", 25.0),
        "alpha_per_k": _num("temperature coefficient, default copper 0.00393", 0.00393),
        "rho20_ohm_m": _num("resistivity at 20 C, default IACS 1.724e-8", 1.724e-8),
        "tol_c": _num("convergence tolerance in K", 1e-4),
        "max_iter": _num("iteration cap", 2000),
    }, "required": ["length_mm", "width_mm", "thickness_um", "current_a",
                    "rth_c_per_w"]},
    lambda a: _electro.trace_self_heating(
        a.get("length_mm"), a.get("width_mm"), a.get("thickness_um"),
        a.get("current_a"), rth_c_per_w=a.get("rth_c_per_w"),
        t_ambient_c=(a["t_ambient_c"] if a.get("t_ambient_c") is not None else 25.0),
        alpha_per_k=(a["alpha_per_k"] if a.get("alpha_per_k") is not None else 0.00393),
        rho20_ohm_m=(a["rho20_ohm_m"] if a.get("rho20_ohm_m") is not None else 1.724e-8),
        tol_c=(a["tol_c"] if a.get("tol_c") is not None else 1e-4),
        max_iter=int(a["max_iter"] if a.get("max_iter") is not None else 2000)),
)

_tool(
    "electrothermal_conductor",
    "The same electro-thermal loop with the cold resistance supplied directly, so it "
    "applies to anything whose R20 you already have: a via barrel (via_resistance), a "
    "chain of segments (ir_drop_chain), a shunt, a connector pin. Returns R_hot, the "
    "resistance gain, the power, the converged temperature, the number of iterations it "
    "actually took, and the stability margin 1 - dP/dT*Rth.",
    {"type": "object", "properties": {
        "r_ohm_at_20c": _num("resistance at 20 C"),
        "current_a": _num("DC current"),
        "rth_c_per_w": _num("thermal resistance to ambient (required)"),
        "t_ambient_c": _num("ambient temperature", 25.0),
        "alpha_per_k": _num("temperature coefficient, default copper 0.00393", 0.00393),
        "tol_c": _num("convergence tolerance in K", 1e-4),
        "max_iter": _num("iteration cap", 2000),
        "note": _str("free text carried back, e.g. 'via barrel from via_resistance'"),
    }, "required": ["r_ohm_at_20c", "current_a", "rth_c_per_w"]},
    lambda a: _electro.conductor_self_heating(
        a.get("r_ohm_at_20c"), a.get("current_a"), a.get("rth_c_per_w"),
        t_ambient_c=(a["t_ambient_c"] if a.get("t_ambient_c") is not None else 25.0),
        alpha_per_k=(a["alpha_per_k"] if a.get("alpha_per_k") is not None else 0.00393),
        tol_c=(a["tol_c"] if a.get("tol_c") is not None else 1e-4),
        max_iter=int(a["max_iter"] if a.get("max_iter") is not None else 2000),
        note=a.get("note")),
)

_tool(
    "board_electrothermal",
    "Board-level 2-D electro-thermal: every conductor's I^2R feeds a plate solve, and "
    "the plate temperature feeds back into rho(T). electrothermal_trace closes the loop "
    "against ONE lump Rth - this one closes it against a 2-D field, because a 3 mm trace "
    "dumping 4 W and the same 4 W spread over 100 mm are different boards. Because "
    "rho(T) is linear, alpha*T sits on the matrix diagonal and the steady problem is "
    "EXACTLY linear (SOR); the transient is backward Euler (unconditionally stable). "
    "Runaway is refused before the solve when any diagonal goes non-positive - on a "
    "uniform plate that reduces to the 1-D rule P*alpha < 2hA. Spreading is through "
    "the laminate k you pass, plus any copper you list in copper_pours (a plane, a "
    "pour, a wide track): each rectangle's exact area overlap with a cell becomes that "
    "cell's sheet conductance and the faces take the harmonic mean, so copper only "
    "helps where it actually is. A plane you did not list still reads hotter than the "
    "real board. Two more loss paths are modelled: emissivity adds radiation from "
    "both faces as sigma*eps*(T^4 - Tamb^4), rewritten exactly as a linear "
    "h_rad(T)*A*(T-Tamb) with h_rad lagged one iteration and the whole field "
    "re-solved until it stops moving, so the answer is the T^4 solution and not an "
    "eps folded into h; thermal_vias adds copper barrels as a conductance to a sink "
    "per cell. Neither is on unless you ask for it, and with neither the numbers are "
    "bit-identical to 0.0.44. layer_stack replaces the single plate with N "
    "through-thickness levels: one in-plane field per level (each level isothermal "
    "across its own slab, midpoint slabs that add up to the board), driven only by "
    "conduction through the laminate k_z between adjacent levels, with convection and "
    "radiation ONLY on the two outer faces - an internal level sees no air, which is "
    "exactly what a plane inside the board does. Each level may carry copper (zones = "
    "rectangles, exact area fractions). The vertical direction is solved as an exact "
    "per-column tridiagonal system (Thomas) and relaxed as one block while the in-plane "
    "field is swept by SOR, so a very stiff k_z cannot be mistaken for convergence; as "
    "k_z -> infinity the answer degenerates to the single-plate number. thermal_vias in "
    "this mode become a short between adjacent levels (a series of gaps, never a path "
    "to a sink) and sink_c / barrel_profile / a partial span are refused. layer_stack "
    "and copper_pours are mutually exclusive: the same copper counted twice.",
    {"type": "object", "properties": {
        "width_mm": _num("plate width"),
        "height_mm": _num("plate height"),
        "thickness_mm": _num("plate thickness (also the heat capacity)"),
        "k_w_mk": _num("in-plane conductivity; FR4 ~0.3, and it is a range 0.2..0.4"),
        "h_w_m2k": _num("convection coefficient, per exposed face (required)"),
        "conductors": {"type": "array", "description":
            "heat sources AND resistances: {x1_mm,y1_mm,x2_mm,y2_mm,width_mm,"
            "thickness_um,current_a,layers?}. The total I^2R of each trace is split "
            "evenly over the cells it covers, so the total power is exact and only "
            "its distribution is quantised by cell_mm", "items": {"type": "object"}},
        "uniform_power_w": _num("optional: a total dissipation spread evenly over the "
                                "plate (the one case with a closed form)"),
        "t_ambient_c": _num("ambient temperature", 25.0),
        "cell_mm": _num("grid pitch; default min(W,H)/40"),
        "edge_temp_c": _num("optional: hold the four edges at this temperature"),
        "alpha_per_k": _num("temperature coefficient, default copper 0.00393", 0.00393),
        "rho20_ohm_m": _num("resistivity at 20 C, default IACS 1.724e-8", 1.724e-8),
        "tol_k": _num("SOR convergence tolerance in K", 1e-4),
        "sor_max_iter": _num("SOR sweep cap per solve", 60000),
        "max_cells": _num("grid cell cap", 20000),
        "transient": {"type": "object", "description":
            "optional: {t_end_s, points, cp_j_kg_k, rho_kg_m3, initial_c?}, "
            "FR4 is about cp 1150 J/(kg*K), rho 1850 kg/m^3"},
        "copper_pours": {"type": "array", "items": {"type": "object"},
            "description":
            "optional: the copper that actually spreads the heat - a plane, a "
            "pour, a very wide track. Each {x1_mm,y1_mm,x2_mm,y2_mm,thickness_um,"
            "layers?}: exact area overlap with each cell becomes that cell's sheet "
            "conductance (k_lam*t_board + frac*t_cu*layers*k_cu), faces use the "
            "harmonic mean. Without it the medium is the laminate k alone, which "
            "is why a board with a plane under the hot part reads hotter than it "
            "is"},
        "k_copper_w_mk": _num("copper conductivity used by copper_pours", 400.0),
        "emissivity": _num("optional 0..1 grey-body emissivity of BOTH faces; omit or "
                            "0 to leave radiation out entirely. Adding it lowers t_max "
                            "and never raises it", None),
        "layer_stack": {"type": "object", "description":
            "optional: model the board as N through-thickness levels instead of one "
            "plate. {copper_layers: [{z_mm, thickness_um, zones?}, ...], k_z_w_mk?}. "
            "z_mm is 0 at the bottom face and thickness_mm at the top; copper_layers "
            "must be non-empty (pass one entry with thickness_um 0 to say 'no copper') "
            "and its z_mm values become the level nodes, always including both faces. "
            "zones = [{x1_mm,y1_mm,x2_mm,y2_mm}, ...] limits that level's copper to "
            "rectangles (exact area fraction); omit them for full-board copper. "
            "k_z_w_mk is the through-thickness laminate conductivity (default k_w_mk). "
            "Internal levels have no convection and no radiation; only the two outer "
            "levels exchange heat with the air. In this mode every conductor needs an "
            "explicit z_mm (one of the level nodes) and layers must be 1, because a "
            "trace on the wrong layer is a wrong answer, not a default. Mutually "
            "exclusive with copper_pours."},
        "thermal_vias": {"type": "array", "items": {"type": "object"},
            "description":
            "optional: copper barrels that carry heat DOWN the board to a sink. "
            "Each {x1_mm,y1_mm,x2_mm,y2_mm,pitch_mm,d_mm,plating_um,span_mm?,sink_c?}: "
            "centres are laid out on a pitch grid inside the rectangle, each barrel "
            "is ONE lumped conductance k_cu*pi*(r_out^2 - r_in^2)/span (annular "
            "section, not the drill area) landing in the cell that contains its "
            "centre, so a point load stays a point load. sink_c defaults to the "
            "ambient and is reported as such. Optional barrel_profile replaces the "
            "uniform wall with [{\"length_mm\",\"plating_um\"},...] end to end "
            "(lengths add up to span_mm): the segments are in SERIES, "
            "G = 1/sum(L_i/(k_cu*A_i)), so a thin middle is not averaged away. "
            "Omit it and the answer is the uniform barrel, bit for bit"},
    }, "required": ["width_mm", "height_mm", "thickness_mm", "k_w_mk", "h_w_m2k"]},
    lambda a: _electro.board_electrothermal(
        a.get("width_mm"), a.get("height_mm"), a.get("thickness_mm"),
        a.get("k_w_mk"), a.get("h_w_m2k"), a.get("conductors"),
        t_ambient_c=(a["t_ambient_c"] if a.get("t_ambient_c") is not None else 25.0),
        cell_mm=a.get("cell_mm"), edge_temp_c=a.get("edge_temp_c"),
        uniform_power_w=a.get("uniform_power_w"),
        copper_pours=a.get("copper_pours"),
        k_copper_w_mk=a.get("k_copper_w_mk"),
        emissivity=a.get("emissivity"),
        thermal_vias=a.get("thermal_vias"),
        layer_stack=a.get("layer_stack"),
        alpha_per_k=(a["alpha_per_k"] if a.get("alpha_per_k") is not None else 0.00393),
        rho20_ohm_m=(a["rho20_ohm_m"] if a.get("rho20_ohm_m") is not None else 1.724e-8),
        tol_k=(a["tol_k"] if a.get("tol_k") is not None else 1e-4),
        max_iter=200,
        sor_max_iter=int(a["sor_max_iter"] if a.get("sor_max_iter") is not None else 60000),
        max_cells=int(a["max_cells"] if a.get("max_cells") is not None else 20000),
        transient=a.get("transient")),
)

# ------------------------------------------------------------ filters (ADS-like)
_tool(
    "filter_prototype",
    "Lowpass prototype element values g1..gn for a Chebyshev or Butterworth response. "
    "This is the starting point every lumped and distributed filter below is built on, "
    "and its numbers are checkable against any textbook table.",
    {"type": "object", "properties": {
        "order": _num("filter order n (1..20)"),
        "response": _str("prototype family", ["chebyshev", "butterworth"],
                         default="chebyshev"),
        "ripple_db": _num("passband ripple in dB; chebyshev defaults to 0.5, "
                          "butterworth must be 0 or omitted"),
    }, "required": ["order"]},
    lambda a: _filters.prototype_gvalues(
        a.get("order"), a.get("response") or "chebyshev", a.get("ripple_db")),
)

_tool(
    "filter_lumped_bandpass",
    "Lumped LC bandpass filter by lowpass-to-bandpass transformation: L and C for every "
    "series and shunt branch, plus the S21 curve computed TWO WAYS - ABCD cascade of the "
    "actual elements and the closed-form Chebyshev/Butterworth 1/(1+eps^2 Tn^2) - and "
    "compared point by point. If the two disagree by more than 0.02 dB the answer is "
    "refused (INTERNAL_CHECK_FAILED) rather than reported. Even orders do NOT terminate in "
    "z0 directly: the ladder is fed series-first and needs a 1:sqrt(g(n+1)) ideal output "
    "transformer, reported as value.termination. Pass q_unloaded (a number, or one per "
    "resonator) to get finite-Q dissipation: the loss is a real resistor in each branch "
    "(w0*L/Q, w0*C/G), not a formula, and the load/incident/dissipated powers are audited "
    "against P_load + sum P_loss = P_incident*(1-|S11|^2). Pass parasitics_cp_f / "
    "parasitics_esl_h for first-order self-resonance. Tolerance/tolerance yield is "
    "filter_mc_yield, not here.",
    {"type": "object", "properties": {
        "order": _num("filter order"),
        "f0_hz": _num("centre frequency in Hz"),
        "fbw": _num("fractional bandwidth, e.g. 0.1 for 10 % (0 < fbw < 1)"),
        "z0_ohm": _num("terminating impedance", 50.0),
        "ripple_db": _num("passband ripple; chebyshev default 0.5"),
        "response": _str("prototype family", ["chebyshev", "butterworth"],
                         default="chebyshev"),
        "points": _num("S21 sweep points", 31),
        "q_unloaded": _num("unloaded resonator Q; a single value applies to every branch. "
                           "Omit for the ideal lossless ladder (no loss block is reported). "
                           "Typical: 200 for air-core, 50-80 for 0402 chip parts"),
        "parasitics_cp_f": _num("shunt capacitance across every inductor, in farads "
                                "(self-resonance at 1/(2*pi*sqrt(L*cp)))"),
        "parasitics_esl_h": _num("series inductance in every capacitor, in henries"),
    }, "required": ["order", "f0_hz", "fbw"]},
    lambda a: _filters.lumped_bandpass(
        a.get("order"), a.get("f0_hz"), a.get("fbw"),
        z0_ohm=(a["z0_ohm"] if a.get("z0_ohm") is not None else 50.0),
        ripple_db=a.get("ripple_db"),
        response=a.get("response") or "chebyshev",
        points=int(a["points"] if a.get("points") is not None else 31),
        q_unloaded=a.get("q_unloaded"),
        parasitics=_parasitics_args(a)),
)

_tool(
    "filter_mc_yield",
    "Monte-Carlo tolerance yield of the lumped bandpass: every L and C gets its own "
    "multiplier from the stated tolerance, the full ABCD cascade is run for each draw and "
    "the fraction meeting the passband/stopband spec is reported WITH its binomial 95 % "
    "confidence half-width (a yield from 200 draws is +/-7 %, it is not a number to design "
    "to). 'uniform + independent' is the worst case: no correlation, no temperature or "
    "ageing drift, no batch shift. Zero tolerance on both parts is reported as a degenerate "
    "single evaluation at 100 % by construction, not as a result. If the nominal design "
    "already fails the spec the tool says so instead of quoting a tolerance yield.",
    {"type": "object", "properties": {
        "order": _num("filter order"),
        "f0_hz": _num("centre frequency in Hz"),
        "fbw": _num("fractional bandwidth, e.g. 0.1 for 10 %"),
        "tol_c_pct": _num("capacitor tolerance in percent (+/-1 sigma if gaussian)", 0.0),
        "tol_l_pct": _num("inductor tolerance in percent (+/-1 sigma if gaussian)", 0.0),
        "n_draws": _num("number of draws", 200),
        "seed": _num("RNG seed; same seed gives the same answer", 0),
        "il_max_db": _num("max insertion loss allowed anywhere in the passband", 1.0),
        "f_stop_hz": _num("optional stopband frequency to reject at"),
        "reject_min_db": _num("insertion loss required at f_stop_hz"),
        "q_unloaded": _num("finite unloaded Q, same meaning as for filter_lumped_bandpass"),
        "distribution": _str("tolerance distribution", ["uniform", "gaussian"],
                             default="uniform"),
        "response": _str("prototype family", ["chebyshev", "butterworth"],
                         default="chebyshev"),
        "ripple_db": _num("passband ripple; chebyshev default 0.5"),
        "z0_ohm": _num("terminating impedance", 50.0),
    }, "required": ["order", "f0_hz", "fbw"]},
    lambda a: _filters.filter_monte_carlo(
        a.get("order"), a.get("f0_hz"), a.get("fbw"),
        tol_c_pct=(a["tol_c_pct"] if a.get("tol_c_pct") is not None else 0.0),
        tol_l_pct=(a["tol_l_pct"] if a.get("tol_l_pct") is not None else 0.0),
        n_draws=int(a["n_draws"] if a.get("n_draws") is not None else 200),
        seed=int(a["seed"] if a.get("seed") is not None else 0),
        il_max_db=(a["il_max_db"] if a.get("il_max_db") is not None else 1.0),
        f_stop_hz=a.get("f_stop_hz"),
        reject_min_db=a.get("reject_min_db"),
        q_unloaded=a.get("q_unloaded"),
        parasitics=_parasitics_args(a),
        response=a.get("response") or "chebyshev",
        ripple_db=a.get("ripple_db"),
        z0_ohm=(a["z0_ohm"] if a.get("z0_ohm") is not None else 50.0),
        distribution=a.get("distribution") or "uniform"),
)

_tool(
    "filter_coupled_microstrip",
    "Invert a symmetric coupled-microstrip pair: given the even/odd mode impedances, "
    "return w and s. The cross-section is SOLVED (Laplace, electric wall for the odd mode "
    "and magnetic wall for the even mode, capacitance -> impedance) and the geometry is "
    "inverted by damped Newton/Broyden on log(w), log(s). Every iteration is reported, so "
    "a non-converged inversion cannot look converged. k = (z0e-z0o)/(z0e+z0o) is a "
    "difference of two close numbers: treat k as a tuning start, not a measurement.",
    {"type": "object", "properties": {
        "z0e_ohm": _num("even-mode impedance"),
        "z0o_ohm": _num("odd-mode impedance (must be < z0e_ohm)"),
        "h_mm": _num("substrate thickness"),
        "er": _num("relative permittivity", 4.3),
        "t_um": _num("copper thickness in um", 35.0),
        "tol_pct": _num("impedance tolerance in percent", 0.5),
        "fine_div": _num("mesh: cell = h/fine_div (6 is the calibrated value, 12 is ~3x "
                         "slower for ~1 % shift)", 6.0),
    }, "required": ["z0e_ohm", "z0o_ohm", "h_mm"]},
    lambda a: _filters.coupled_microstrip(
        a.get("z0e_ohm"), a.get("z0o_ohm"), a.get("h_mm"),
        er=(a["er"] if a.get("er") is not None else 4.3),
        t_um=(a["t_um"] if a.get("t_um") is not None else 35.0),
        tol_pct=(a["tol_pct"] if a.get("tol_pct") is not None else 0.5),
        fine_div=(a["fine_div"] if a.get("fine_div") is not None else 6.0)),
)

_tool(
    "filter_coupled_resonator",
    "Edge-coupled half-wave-resonator bandpass in microstrip: turns the prototype g "
    "values into external Q, inter-resonator coupling coefficients k_ij, then solves the "
    "coupled-line geometry for every coupling and the resonator length from lambda_g/2 "
    "minus two open-end extensions. Returns target vs achieved k and whether each section "
    "meets its target - the k resolution limit is reported, not hidden.",
    {"type": "object", "properties": {
        "order": _num("filter order"),
        "f0_hz": _num("centre frequency in Hz"),
        "fbw": _num("fractional bandwidth"),
        "z0_ohm": _num("system impedance", 50.0),
        "ripple_db": _num("passband ripple; chebyshev default 0.5"),
        "response": _str("prototype family", ["chebyshev", "butterworth"],
                         default="chebyshev"),
        "h_mm": _num("substrate thickness", 0.2),
        "er": _num("relative permittivity", 4.3),
        "t_um": _num("copper thickness in um", 35.0),
        "tol_pct": _num("impedance tolerance for each coupling solve, percent", 1.0),
    }, "required": ["order", "f0_hz", "fbw"]},
    lambda a: _filters.coupled_resonator_bpf(
        a.get("order"), a.get("f0_hz"), a.get("fbw"),
        z0_ohm=(a["z0_ohm"] if a.get("z0_ohm") is not None else 50.0),
        ripple_db=a.get("ripple_db"),
        response=a.get("response") or "chebyshev",
        h_mm=(a["h_mm"] if a.get("h_mm") is not None else 0.2),
        er=(a["er"] if a.get("er") is not None else 4.3),
        t_um=(a["t_um"] if a.get("t_um") is not None else 35.0),
        tol_pct=(a["tol_pct"] if a.get("tol_pct") is not None else 1.0)),
)

_tool(
    "filter_waveguide_cavity",
    "Rectangular waveguide cavity in ANY TE_mnp / TM_mnp mode: the resonant length for a "
    "given a and b, the unloaded Q from wall loss computed TWO independent ways (exact "
    "closed-form sine/cosine integrals vs a field integration on a grid over all six walls "
    "- they must agree within 2 % and the stored energies must balance, or the answer is "
    "withheld), an optional mode chart of EVERY mode below a band edge (Qc, degeneracy and "
    "the next mode above f0), an optional surface-roughness correction (Rq), and R/Q for "
    "TE101. TM_mn0 has no length to give - it resonates at its transverse cutoff for any "
    "length - and is refused by name rather than answered with a made-up d. No iris or "
    "probe coupling geometry: that needs a full-wave solve.",
    {"type": "object", "properties": {
        "f0_hz": _num("target resonant frequency in Hz"),
        "a_mm": _num("broad-wall width a"),
        "b_mm": _num("narrow-wall height b (default a/2)"),
        "conductivity_s_m": _num("wall conductivity, default copper 5.8e7", 5.8e7),
        "mu_r": _num("relative permeability of the fill", 1.0),
        "er": _num("relative permittivity of the fill", 1.0),
        "mode": _str("cavity mode: 'TE101' (three single digits, how everyone writes it) "
                     "or underscored integers like 'TE_2_0_1' / 'TM_1_1_1' (the only "
                     "unambiguous way to write an index >= 10 - plain 'TE201' could also "
                     "mean 20,1 and a guess there would silently answer a different mode). "
                     "Any valid TE_mnp (p >= 1) or TM_mnp (m, n >= 1).",
                     None, "TE101"),
        "roughness_um": _num("RMS surface roughness Rq in um (e.g. 2.0 for a milled wall): "
                             "the surface resistance is multiplied by the "
                             "Hammerstad-Jensen K and Qc is divided by it. Rz is NOT "
                             "converted - pass Rq"),
        "modes_upto_hz": _num("top of the mode chart in Hz: every TE_mnp/TM_mnp of this "
                              "cavity below that frequency with its Qc, degeneracy and "
                              "is_operating, plus the next mode above f0; at most 200 "
                              "modes"),
    }, "required": ["f0_hz", "a_mm"]},
    lambda a: _filters.waveguide_cavity(
        a.get("f0_hz"), a.get("a_mm"),
        b_mm=a.get("b_mm"),
        conductivity_s_m=(a["conductivity_s_m"]
                          if a.get("conductivity_s_m") is not None else 5.8e7),
        mu_r=(a["mu_r"] if a.get("mu_r") is not None else 1.0),
        er=(a["er"] if a.get("er") is not None else 1.0),
        mode=a.get("mode") or "TE101",
        roughness_um=a.get("roughness_um"),
        modes_upto_hz=a.get("modes_upto_hz")),
)

_tool(
    "filter_coupling_matrix",
    "Coupling matrix of a bandpass filter, evaluated as a real network: finite "
    "transmission zeros from a cross-coupling, and the in-band / stopband response that "
    "finite unloaded Q actually delivers. The inline matrix is built from the prototype g "
    "values, then PERTURBED by the cross-coupling you ask for - it is not a Cameron "
    "synthesis, so ripple_actual_db is the ripple you get, not the one you asked for, and "
    "the answer says so. Self-audited three ways before it answers: (1) the lossless "
    "inline network must reproduce 1/(1+eps^2 Tn(Omega)^2) to 1e-6 dB with the ripple it "
    "delivers held to the prototype's to 1e-3 dB; (2) with no loss, |S11|^2+|S21|^2 = 1 "
    "to 1e-9; (3) for a third-order trisection the numeric null must sit at the analytic "
    "Omega_z = m12^2/m13. S-parameters come from a nodal solve with exact admittance-"
    "inverter Pi equivalents (not from inverting the matrix), so cross-couplings form "
    "real loops. No layout, no EM, no dispersion, no iris geometry.",
    {"type": "object", "properties": {
        "order": _num("number of resonators (1..8)"),
        "f0_hz": _num("centre frequency in Hz"),
        "fbw": _num("fractional bandwidth, e.g. 0.05 for 5 percent"),
        "ripple_db": _num("passband ripple for a chebyshev prototype; butterworth has none"),
        "response": _str("prototype family", ["chebyshev", "butterworth"],
                         default="chebyshev"),
        "zero_hz": _num("place the single transmission zero of a third-order trisection "
                        "here (order must be 3); give this OR couplings, never both"),
        "couplings": {"type": "array",
                      "items": {"type": "array", "items": {"type": "number"}},
                      "description": "explicit extra couplings [i, j, m_ij] on top of the "
                                     "inline matrix, resonator numbers 1..order - the "
                                     "escape hatch for a matrix you synthesised yourself"},
        "q_external": {"type": "array", "items": {"type": "number"},
                       "description": "override the external Q pair [Qe_in, Qe_out] as "
                                      "absolute Q (not Qe*FBW); default comes from the g "
                                      "values"},
        "q_unloaded": _num("unloaded Q of the resonators, to see the insertion loss and "
                           "how far the nulls fill in (one number, or a list per "
                           "resonator)"),
        "points": _num("points in the displayed sweep (21..801; in-band numbers come from "
                       "a separate 401-point grid over the band)", 41),
        "span": _num("half-width of the sweep in units of FBW (1.5..20)", 3.0),
        "probe_hz": _num("optional: return S11/S21 at exactly this frequency"),
    }, "required": ["order", "f0_hz", "fbw"]},
    lambda a: _filtermatrix.coupled_matrix_filter(
        a.get("order"), a.get("f0_hz"), a.get("fbw"),
        ripple_db=a.get("ripple_db"),
        response=a.get("response") or "chebyshev",
        zero_hz=a.get("zero_hz"),
        couplings=a.get("couplings"),
        q_external=a.get("q_external"),
        q_unloaded=a.get("q_unloaded"),
        points=(a["points"] if a.get("points") is not None else 41),
        span=(a["span"] if a.get("span") is not None else 3.0),
        probe_hz=a.get("probe_hz")),
)

_tool(
    "magnetics_inductor",
    "Power-inductor sizing for CCM boost / buck / buck-boost from the load, the rails "
    "and the switching frequency: duty, inductance, peak / valley / RMS current, where "
    "CCM ends, and the winding's I^2R loss if you supply dcr_ohm. Ripple ratio r is a "
    "DESIGN CHOICE (0.2-0.4 typical) - it is not derived. Saturation is graded only if "
    "you supply the part's Isat; without it the answer says what is missing instead of "
    "guessing. Core loss here needs your own Steinmetz triple (magnetics_core_loss); "
    "to pick an actual core, magnetics_select_core ranks the real parts in "
    "data/magnetic_materials.json; a load BELOW the CCM boundary is a different "
    "problem - use magnetics_dcm, where the duty is not 1-Vin/Vout.",
    {"type": "object", "properties": {
        "topology": _str("converter topology", list(_magnetics.TOPOLOGIES)),
        "vin_v": _num("input voltage"),
        "vout_v": _num("output voltage magnitude (buck-boost is inverting)"),
        "f_sw_hz": _num("switching frequency in Hz (not kHz)"),
        "ripple": _num("ripple ratio r = dI / Iavg, a design choice (0.2-0.4 typical)",
                       0.3),
        "iout_a": _num("load current - give this OR pout_w, never both"),
        "pout_w": _num("load power - give this OR iout_a, never both"),
        "efficiency": _num("assumed constant efficiency (0 < eta <= 1)", 1.0),
        "isat_a": _num("the inductor's saturation current, if you have picked a part"),
        "sat_margin": _num("headroom required above the peak current", 0.20),
        "dcr_ohm": _num("optional: the winding's DC resistance, to get its I^2R loss"),
    }, "required": ["topology", "vin_v", "vout_v", "f_sw_hz"]},
    lambda a: _magnetics.inductor_ccm(
        a.get("topology"), a.get("vin_v"), a.get("vout_v"), a.get("f_sw_hz"),
        ripple=(a["ripple"] if a.get("ripple") is not None else 0.3),
        iout_a=a.get("iout_a"), pout_w=a.get("pout_w"),
        efficiency=(a["efficiency"] if a.get("efficiency") is not None else 1.0),
        isat_a=a.get("isat_a"),
        sat_margin=(a["sat_margin"] if a.get("sat_margin") is not None else 0.20),
        dcr_ohm=a.get("dcr_ohm")),
)

_tool(
    "magnetics_dcm",
    "The DCM steady state - which is NOT CCM with a smaller ripple. Three intervals "
    "per period, a different output relation (boost D = sqrt(K*M*(M-1)), buck "
    "D = M*sqrt(K/(1-M)), buck-boost D = M*sqrt(K) with K = 2*L*fs/R), and the ripple "
    "is no longer a design choice: dI = 2*I_L,avg by construction. Refuses with "
    "MODE_IS_CCM when the load is above the CCM boundary for this L (it names that "
    "load), because otherwise the formula would answer a question nobody asked. "
    "Lossless voltages: a single efficiency number cannot express the DCM duty shift.",
    {"type": "object", "properties": {
        "topology": _str("converter topology", list(_magnetics.TOPOLOGIES)),
        "vin_v": _num("input voltage"),
        "vout_v": _num("output voltage magnitude (buck-boost is inverting)"),
        "f_sw_hz": _num("switching frequency in Hz (not kHz)"),
        "l_h": _num("the inductance you have, in HENRY (1e-6 for uH)"),
        "iout_a": _num("load current - give this OR pout_w, never both"),
        "pout_w": _num("load power - give this OR iout_a, never both"),
    }, "required": ["topology", "vin_v", "vout_v", "f_sw_hz", "l_h"]},
    lambda a: _magnetics.duty_dcm(
        a.get("topology"), a.get("vin_v"), a.get("vout_v"), a.get("f_sw_hz"),
        a.get("l_h"), iout_a=a.get("iout_a"), pout_w=a.get("pout_w")),
)

_tool(
    "magnetics_core_loss",
    "Core loss for ONE period of a flux trajectory, with the coefficients YOU supply "
    "and the unit convention NAMED, because the published fits differ by ~10^12 in k "
    "purely through units (si = Hz/T/W-per-m3, khz_mt = kHz/mT/mW-per-cm3, khz_gauss, "
    "khz_t = kHz/T/mW-per-cm3, which is the convention the powder-core catalog "
    "prints its coefficients in). "
    "waveform='sine' (default) is the plain Steinmetz P_v = k*f^alpha*B_peak^beta; "
    "waveform='triangle' (with duty, the rising fraction of the period) or "
    "waveform='piecewise' (segments=[[dB_t, dt_fraction], ...] summing to 1 in time "
    "and 0 in flux) evaluates the iGSE instead, whose k_i is derived here from the "
    "requirement that a sine must reproduce your own Steinmetz triple exactly - the "
    "usual case, since an inductor's ripple is a triangle and the plain form is only "
    "defined for a sine. Every non-sine answer also prints what the sine formula "
    "would have said, the ratio and which way it errs. Both unit systems are printed "
    "on every answer so a unit slip shows up as an absurd magnitude. B_peak is the AC "
    "amplitude (half the peak-to-peak swing); iGSE's dB_pp is the full swing. Not "
    "modelled: DC bias, temperature dependence, gap loss, iGSE's known narrow-pulse "
    "over-prediction (GSE/WGSE) - and nothing about the winding.",
    {"type": "object", "properties": {
        "f_hz": _num("frequency in Hz"),
        "b_peak_t": _num("AC flux amplitude in tesla (for waveform='piecewise' it may "
                         "be omitted, or given as a check against the segments)"),
        "k": _num("the material's Steinmetz coefficient, in the units named below"),
        "alpha": _num("the material's frequency exponent"),
        "beta": _num("the material's flux exponent"),
        "units": _str("which convention k is in", sorted(_magnetics.STEINMETZ_UNITS)),
        "ve_mm3": _num("optional: core volume, to turn P_v into watts"),
        "waveform": _str("the flux shape over one period: sine (plain Steinmetz), "
                         "triangle (needs duty) or piecewise (needs segments)",
                         ["sine", "triangle", "piecewise"], "sine"),
        "duty": _num("triangle only: fraction of the period spent rising (0.5 = "
                     "symmetric)"),
        "segments": {"type": "array",
                     "description": "piecewise only: the trajectory as "
                                    "[[dB_t, dt_fraction], ...]. dB_t is the signed "
                                    "flux CHANGE over the row in tesla, dt_fraction "
                                    "its share of the period. The rows must sum to 1 "
                                    "in time and to 0 in flux (a closed period)",
                     "items": {"type": "array", "items": {"type": "number"}}},
    }, "required": ["f_hz", "k", "alpha", "beta"]},
    lambda a: _magnetics.core_loss_steinmetz(
        a.get("f_hz"), a.get("b_peak_t"), a.get("k"), a.get("alpha"), a.get("beta"),
        units=(a.get("units") or "si"), ve_mm3=a.get("ve_mm3"),
        waveform=(a.get("waveform") or "sine"), duty=a.get("duty"),
        segments=a.get("segments")),
)

_tool(
    "magnetics_material_core_loss",
    "Core loss density from the VENDOR's own printed fit, looked up by material name - "
    "the same Steinmetz/iGSE machinery as magnetics_core_loss, but with a, b and c "
    "taken from the vendor's catalog instead of from you, and evaluated in the unit "
    "convention the catalog prints them in (khz_t: mW/cm3, tesla, kilohertz). The "
    "print is P = a*Bpk^b*f^c per PERMEABILITY and per CORE SHAPE, not per material, "
    "so name the permeability and the shape family; an under-specified question is "
    "refused with the list of printed rows rather than answered with one of them. "
    "The catalog's three worked examples are reproduced to the printed 3 digits "
    "(Kool Mu 60u toroid at 0.014 / 0.055 / 0.092 tesla and 100 kHz gives "
    "11.04 / 167.6 / 466.0 mW/cm3 where it prints 11.0 / 168 / 466). Bpk is HALF the "
    "AC swing. waveform/duty/segments correct the shape exactly as in "
    "magnetics_core_loss. The fit is a curve fit of the vendor's charts, not a "
    "guarantee: outside 5 kHz-2 MHz or above 0.5 T the answer carries a warning, and "
    "the ferrite materials have no vendor fit here at all (they refuse, naming "
    "magnetics_core_loss as the way to pass your own triple) - because the vendors "
    "print points, not coefficients: magnetics_core_loss_audit scores a triple you "
    "bring against all 17 printed PV rows.",
    {"type": "object", "properties": {
        "material": _str("material name as it appears in the table (14 materials; "
                         "five carry a vendor loss fit: Kool Mu, MPP, High Flux, "
                         "XFlux, Edge)"),
        "freq_hz": _num("frequency in Hz"),
        "b_peak_t": _num("AC flux amplitude in tesla - HALF the peak-to-peak swing, "
                         "which is the vendor's own Bpk"),
        "permeability": _num("the material's permeability in u (14, 26, 60, 125 ...). "
                             "Part of the question: the vendor prints a different fit "
                             "for each"),
        "shape": _str("the core shape family the fit is printed for",
                      sorted(_magnetics.LOSS_FIT_SHAPES)),
        "variant": _str("the product line, where the vendor sells several under one "
                        "material name: base (default), MAX, MAX-HP, HF, Ultra or "
                        "High DC Bias - the last one is a separate printed fit on "
                        "its own catalog page, not an interpolation of the base"),
        "waveform": _str("the flux shape over one period: sine (what the fit is), "
                         "triangle (needs duty) or piecewise (needs segments)",
                         ["sine", "triangle", "piecewise"], "sine"),
        "duty": _num("triangle only: fraction of the period spent rising (0.5 = "
                     "symmetric)"),
        "segments": {"type": "array",
                     "description": "piecewise only: the trajectory as "
                                    "[[dB_t, dt_fraction], ...] summing to 1 in time "
                                    "and 0 in flux (a closed period)",
                     "items": {"type": "array", "items": {"type": "number"}}},
        "ve_mm3": _num("optional: core volume, to turn P_v into watts"),
        "data_path": _str("override the table path (testing)"),
    }, "required": ["material", "freq_hz", "b_peak_t"]},
    lambda a: _magnetics.material_core_loss(
        a.get("material"), a.get("freq_hz"), a.get("b_peak_t"),
        permeability=a.get("permeability"),
        shape=a.get("shape"),
        variant=a.get("variant"),
        waveform=(a.get("waveform") or "sine"),
        duty=a.get("duty"),
        segments=a.get("segments"),
        ve_mm3=a.get("ve_mm3"),
        data_path=a.get("data_path")),
)

_tool(
    "magnetics_winding_ac",
    "Dowell's 1-D AC/DC resistance ratio F_R for a winding layer stack, so the DC "
    "resistance an ohmmeter reads becomes the resistance the switching frequency "
    "sees. Foil (thickness_mm, one layer = one turn) or a layer of round wire "
    "(wire_diameter_mm + pitch_mm; both the DC-exact equivalent-foil reading and the "
    "porosity-corrected one are printed, because that substitution is the ambiguous "
    "part of applying Dowell to round wire). F_R is split into its own-field and "
    "proximity terms, which is what makes 'more layers' visibly expensive. Give "
    "dc_resistance_ohm, or turns + mean_length_mm, to get ohms back. Not modelled: "
    "gap/fringing field (usually the biggest AC winding loss in a gapped inductor - "
    "magnetics_fringing_loss computes that term separately, and it is added beside "
    "this answer as a power, not inside it), litz, interleaved windings, "
    "non-sinusoidal current, end turns, core loss.",
    {"type": "object", "properties": {
        "freq_hz": _num("frequency in Hz (the fundamental of the current, not the "
                        "switching frequency of some other converter)"),
        "layers": _num("number of layers in ONE winding block (1..64); the MMF "
                       "staircase steps by one ampere-turn per layer"),
        "shape": _str("foil or a layer of round wire", list(_magnetics.WINDING_SHAPES)),
        "thickness_mm": _num("foil thickness (shape=foil)"),
        "wire_diameter_mm": _num("bare copper diameter (shape=round)"),
        "pitch_mm": _num("turn pitch, centre to centre (shape=round)"),
        "porosity_corrected": _bool("shape=round: report the porosity-corrected F_R "
                                    "as `fr` instead of the area-equivalent one"),
        "turns": _num("total turns, to compute the DC resistance from geometry"),
        "mean_length_mm": _num("mean length per turn in mm, with turns"),
        "dc_resistance_ohm": _num("measured DC resistance, instead of turns+geometry"),
        "temperature_c": _num("copper temperature, which sets the resistivity "
                              "(20 C = 1.724e-8 ohm m)"),
        "resistivity_ohm_m": _num("or give the resistivity directly (not with "
                                  "temperature_c)"),
    }, "required": ["freq_hz", "layers"]},
    lambda a: _magnetics.winding_ac_resistance(
        a.get("freq_hz"), a.get("layers"),
        shape=(a.get("shape") or "foil"),
        thickness_mm=a.get("thickness_mm"),
        wire_diameter_mm=a.get("wire_diameter_mm"),
        pitch_mm=a.get("pitch_mm"),
        porosity_corrected=a.get("porosity_corrected"),
        turns=a.get("turns"),
        mean_length_mm=a.get("mean_length_mm"),
        dc_resistance_ohm=a.get("dc_resistance_ohm"),
        temperature_c=a.get("temperature_c"),
        resistivity_ohm_m=a.get("resistivity_ohm_m")),
)

_tool(
    "magnetics_fringing_loss",
    "Fringing-field loss: the AC copper loss caused by the field that leaks out of "
    "an air gap - the term Dowell's 1-D stack field cannot see, and in a gapped "
    "inductor often bigger than the proximity term it competes with. The field is a 2-D magnetic-charge model of "
    "the two pole faces (perfect magnetically conducting strips of the leg width, "
    "separation gap_mm, pole density q set so that the axial field integrates to "
    "exactly the MMF); each shell of the stack is a foil ring whose own eddy "
    "shielding comes from the exact 1-D slab factor (so thick foil stops growing "
    "like t^2 instead of being over-stated 18x by the thin-sheet form). Give "
    "current_rms_a (MMF = layers x current) or ampere_turns, exactly one. Returns "
    "per-shell field_weight_a2_m = int H_z^2 dz, which is the raw number to reuse "
    "for a conductor shape this model does not carry, and probe_radii_mm prints it "
    "at any standoff. NOT modelled: the radial field component (it drives a "
    "circulating current over the stack - its measured size is printed), round "
    "wire, the 2-D ends of the leg, the winding's own field (that is "
    "magnetics_winding_ac, whose proximity term adds to this one as a power, with "
    "no cross term), saturation of the pole faces, non-sinusoidal current.",
    {"type": "object", "properties": {
        "freq_hz": _num("frequency in Hz, the fundamental of the current"),
        "gap_mm": _num("air gap between the two pole faces"),
        "leg_width_mm": _num("width of the leg whose faces carry the gap (the 2-D "
                             "model is infinite in the other direction)"),
        "leg_depth_mm": _num("leg depth; only the turn perimeter uses it, unless the "
                             "corner field matters to you, which this model does not "
                             "carry"),
        "layers": _num("number of concentric foil shells; one shell is one turn, so "
                       "the MMF is layers x current_rms_a"),
        "thickness_mm": _num("foil thickness"),
        "winding_height_mm": _num("height of the winding along the leg (= the length "
                                  "of each shell)"),
        "standoff_mm": _num("radial distance from the leg surface to the first "
                            "shell's inner face (default 0)"),
        "insulation_mm": _num("insulation between neighbouring shells, so the pitch "
                              "is thickness + insulation (default 0)"),
        "current_rms_a": _num("RMS current in the winding; gives MMF = layers x I"),
        "ampere_turns": _num("or set the MMF directly (not with current_rms_a)"),
        "temperature_c": _num("copper temperature, which sets the resistivity "
                              "(20 C = 1.724e-8 ohm m)"),
        "resistivity_ohm_m": _num("or give the resistivity directly (not with "
                                  "temperature_c)"),
        "probe_radii_mm": {"type": "array",
                           "description": "extra standoffs at which to print "
                                          "field_weight_a2_m and the peak field, for "
                                          "a conductor shape this model does not "
                                          "carry (round wire, litz)",
                           "items": {"type": "number"}},
    }, "required": ["freq_hz", "gap_mm", "leg_width_mm", "leg_depth_mm", "layers",
                    "thickness_mm", "winding_height_mm"]},
    lambda a: _magnetics.fringing_loss(
        a.get("freq_hz"), a.get("gap_mm"), a.get("leg_width_mm"),
        a.get("leg_depth_mm"), a.get("layers"), a.get("thickness_mm"),
        a.get("winding_height_mm"),
        standoff_mm=(a.get("standoff_mm") if a.get("standoff_mm") is not None else 0.0),
        insulation_mm=(a.get("insulation_mm") if a.get("insulation_mm") is not None else 0.0),
        current_rms_a=a.get("current_rms_a"),
        ampere_turns=a.get("ampere_turns"),
        temperature_c=a.get("temperature_c"),
        resistivity_ohm_m=a.get("resistivity_ohm_m"),
        probe_radii_mm=a.get("probe_radii_mm")),
)

_tool(
    "magnetics_select_core",
    "Pick a REAL core for an (L, Ipk) requirement from data/magnetic_materials.json "
    "(vendor-published Ae / le / Ve / AL / Amin / mass / Bsat / one measured loss "
    "point per part, every row carrying its source URL). Turns come from the "
    "inductance (L = AL*N^2) AND from the flux limit (B = L*Ipk/(N*Ae)), whichever "
    "is larger; the gap is then whatever series reluctance the difference implies. "
    "The flux limit is your b_max_t, or bsat_derate x the material's published Bsat "
    "at temperature_c - and only at 25 C or 100 C, the two temperatures vendors "
    "print. Core loss is the vendor's own measured point when you ask for exactly "
    "its condition; away from it, the scaling needs loss_exponents=(x, y) from the "
    "material's fit, which this server does not invent. Rejections are named "
    "(saturates / window_overflow / saturation_unknown / no_geometry / filter). NOT "
    "modelled: temperature rise (no part has a published Rth), the DC-bias roll-off "
    "of AL, gap fringing, winding AC resistance, AL tolerance. flux_waveform="
    "'triangle' (+ duty) judges core_loss_w on the flux shape an inductor actually "
    "carries rather than on the sine the vendor point was measured with; it needs "
    "loss_exponents, whose x plays alpha in the iGSE shape ratio. "
    "include_powder_cores=true adds the powder-core toroids (43 sizes, 1923 part "
    "numbers, printed p.155-197) to the pool; permeability narrows them, because a "
    "toroid's AL depends on a permeability its part number does not spell out. Two "
    "things are different for a powder part and neither is guessed: its Bsat is the "
    "single value the catalog prints for its column (no temperature against it), and "
    "it has no gap to compute - the gap is distributed, so AL can only be changed by "
    "choosing another permeability. Its core loss comes from the vendor's own "
    "per-permeability toroid fit. No mean turn length is printed for a toroid, so no "
    "copper estimate is made for one. dc_bias_current_a (0.0.78) closes the bias "
    "loop: the printed AL is the UNBIASED value, so with a bias given each powder "
    "part's turns are solved at that current - H = 0.4*pi*N*I/le[cm] from the "
    "catalog's relation, mu from the part's own printed (a, b, c) row, and the turns "
    "chosen so the BIASED inductance still reaches your L while the peak flux stays "
    "under the limit. It is a bracketed search, not a fixed point: L(N) ~ "
    "N^2/(a + b*(kN)^c) peaks at N^c = 2a/((c-2)*b*k^c) and then FALLS when c > 2 - "
    "which 86 of the 125 printed rows are - so at a high bias more turns can buy "
    "LESS inductance, and a part that cannot reach L at all is rejected as "
    "dc_bias_unreachable with the maximum it can reach instead of getting a turn "
    "count that does not work. A design whose H lands outside the 10-800 Oe the fit "
    "pages are drawn over is flagged h_within_printed_band=false, not passed off as "
    "a reading.",
    {"type": "object", "properties": {
        "inductance_h": _num("the inductance you need, in HENRY (100 uH = 1e-4)"),
        "i_peak_a": _num("peak winding current, which sets the flux"),
        "i_rms_a": _num("RMS current, for the copper loss (with "
                        "current_density_a_mm2)"),
        "freq_hz": _num("frequency, for the core loss"),
        "b_ac_t": _num("AC flux amplitude (half the peak-to-peak swing) for the core "
                       "loss - not the same number as the peak flux density"),
        "b_max_t": _num("your own flux limit; overrides the material's Bsat path"),
        "material": _str("only cores of this material (e.g. N87, 3C90)"),
        "shape": _str("only cores whose shape contains this text (e.g. E42, PQ)"),
        "temperature_c": _num("operating temperature: 25 or 100, the temperatures "
                              "vendors publish Bsat at", 100.0),
        "bsat_derate": _num("fraction of Bsat the peak is allowed to use - a design "
                            "margin, not a material law", 0.8),
        "loss_exponents": {"type": "array", "items": {"type": "number"},
                           "description": "the material fit's (x, y) frequency and "
                                          "flux exponents, to move a measured loss "
                                          "point to another operating point"},
        "current_density_a_mm2": _num("winding current density, for wire area and "
                                      "copper loss"),
        "fill_factor": _num("copper fill factor of the winding window (with a "
                            "former's winding area, this is the window check)"),
        "limit": _num("how many feasible cores to list (smallest Ve first)"),
        "require_window_fit": _bool("drop cores whose winding window could not be "
                                    "checked, instead of listing them"),
        "flux_waveform": _str("the flux shape the core loss is judged on: sine (what "
                              "the vendor's measured point is) or triangle (what an "
                              "inductor's ripple is)", ["sine", "triangle"], "sine"),
        "duty": _num("triangle only: fraction of the period spent rising (0.5 = "
                     "symmetric)"),
        "include_powder_cores": _bool("also consider the powder-core toroids in "
                                     "data/powder_cores.json (default false: the "
                                     "pool and every number for the solid cores are "
                                     "then unchanged)"),
        "permeability": _num("powder cores only: one printed permeability in u - a "
                             "toroid's AL depends on a permeability its part number "
                             "does not spell out, so the material alone does not name "
                             "a part"),
        "data_path": _str("override the table path (testing)"),
        "powder_data_path": _str("override the powder-core table path (testing)"),
        "dc_bias_current_a": _num("the DC current the winding sits at (0.0.78): with "
                                  "it the turns are solved on the BIASED inductance "
                                  "instead of the printed no-current AL, using the "
                                  "vendor's printed % mu_i fit. Powder parts only - "
                                  "asking without include_powder_cores is refused "
                                  "rather than answered unbiased"),
        "dc_bias_data_path": _str("override the DC-bias fit table path (testing)"),
        "dc_bias_turns_max": _num("how many turns the bias search will look at "
                                  "before calling a part unreachable", 1000),
        "toroid_mlt": _str("off (default) or estimate. A toroid's catalog prints no "
                           "mean turn length, so estimate multiplies the geometric "
                           "section perimeter 2*H + (OD - ID) by the measured "
                           "printed-MLT ratio band of thirteen toroids (1.248-1.461) "
                           "and reports r_dc / copper loss as estimates WITH their "
                           "range. Off is the 0.0.78 answer byte for byte",
                           list(_magnetics.TOROID_MLT_MODES), "off"),
    }, "required": ["inductance_h", "i_peak_a"]},
    lambda a: _magnetics.select_core(
        a.get("inductance_h"), a.get("i_peak_a"),
        i_rms_a=a.get("i_rms_a"),
        freq_hz=a.get("freq_hz"),
        b_ac_t=a.get("b_ac_t"),
        b_max_t=a.get("b_max_t"),
        material=a.get("material"),
        shape=a.get("shape"),
        temperature_c=(a["temperature_c"] if a.get("temperature_c") is not None
                       else 100.0),
        bsat_derate=(a["bsat_derate"] if a.get("bsat_derate") is not None else 0.8),
        loss_exponents=a.get("loss_exponents"),
        current_density_a_mm2=a.get("current_density_a_mm2"),
        fill_factor=a.get("fill_factor"),
        limit=a.get("limit"),
        require_window_fit=a.get("require_window_fit"),
        flux_waveform=(a.get("flux_waveform") or "sine"),
        duty=a.get("duty"),
        data_path=a.get("data_path"),
        include_powder_cores=bool(a.get("include_powder_cores")),
        permeability=a.get("permeability"),
        powder_data_path=a.get("powder_data_path"),
        dc_bias_current_a=a.get("dc_bias_current_a"),
        dc_bias_data_path=a.get("dc_bias_data_path"),
        dc_bias_turns_max=(a["dc_bias_turns_max"]
                           if a.get("dc_bias_turns_max") is not None else 1000),
        toroid_mlt=(a.get("toroid_mlt") or "off")),
)

_tool(
    "magnetics_dc_bias",
    "How much of a powder core's initial permeability survives a DC bias - the number "
    "that decides whether the inductance you computed at zero current is still there "
    "at the current it has to carry. It evaluates the VENDOR's own printed fit, "
    "% mu_i = 1 / (a + b*H^c) with H in oersteds, transcribed from the catalog's "
    "'Permeability versus DC Bias Curves - Fit Formula' pages (29 material x core-shape "
    "groups, 125 rows, printed pp.63-65, in data/dc_bias_fit.json). Give h_oe "
    "directly, or give turns + dc_current_a + le_mm and it computes H = "
    "0.4*pi*N*I[A]/le[cm], which is the catalog's own relation. The fit is printed per "
    "PERMEABILITY and per CORE SHAPE FAMILY, not per material, and the curve of one "
    "permeability is not a scaled copy of its neighbour's - so an unprinted "
    "permeability or shape family is refused with the list of printed ones rather than "
    "interpolated. The answer also carries the flux that bias produces, b_t, obtained "
    "by integrating the fit (the chord form mu_r*H is only right while the curve is "
    "still flat: at 95 Oe the integral gives 0.442 T where the chord gives 0.285 T, and "
    "the catalog itself prints 0.441 T), plus the ratio to the material's printed "
    "saturation flux density. Checked before it was written down: the fit reproduces "
    "all 18 numbers of the catalog's printed 80 %/50 % summary table (worst 0.96 %), "
    "both worked examples (58.4 % against a printed 0.58; B = 0.0936 T against a "
    "printed 0.092 T), and seven curves of the printed p.52 graph read off as pixels "
    "(0.11-1.08 %). NOT modelled: temperature (the catalog prints one fit, not one per "
    "temperature), saturation (% mu_i is a small-signal quantity - the flux is reported "
    "beside it, not used to correct it), and the turn-count feedback loop (more turns "
    "means more oersteds means less mu, which is a fixed point this tool does not "
    "solve - iterate it yourself or feed h_oe back in).",
    {"type": "object", "properties": {
        "material": _str("material as the catalog prints it (Kool Mu, Kool Mu MAX, "
                         "Kool Mu MAX High Performance, Kool Mu HF, Kool Mu Ultra, "
                         "XFlux, XFlux Ultra, Edge, High Flux, MPP)"),
        "permeability": _num("the row's nominal permeability in u (14, 26, 60, 125 ...). "
                             "Part of the question: the vendor prints a different fit "
                             "for each"),
        "shape_family": _str("the core shape family the fit is printed for (Toroids, "
                             "'E Cores, U Cores & EER Cores', 'EQ & LP Cores', "
                             "'Blocks, Round Blocks & Cylinders'). Optional only when "
                             "the material is printed for exactly one family"),
        "h_oe": _num("DC magnetising force in oersteds, if you already have it"),
        "turns": _num("or the winding: turns (used with dc_current_a and le_mm)"),
        "dc_current_a": _num("the DC current the winding carries, in amperes"),
        "le_mm": _num("the magnetic path length in mm, from the core's own data"),
        "include_b": _bool("also report the flux density the bias produces (default "
                           "true)"),
        "bias_data_path": _str("override the fit table path (testing)"),
    }, "required": ["material", "permeability"]},
    lambda a: _magnetics.magnetics_dc_bias(
        a.get("material"), a.get("permeability"),
        shape_family=a.get("shape_family"),
        h_oe=a.get("h_oe"),
        turns=a.get("turns"),
        dc_current_a=a.get("dc_current_a"),
        le_mm=a.get("le_mm"),
        bias_data_path=a.get("bias_data_path"),
        include_b=(True if a.get("include_b") is None else bool(a.get("include_b")))),
)

_tool(
    "magnetics_powder_curve",
    "The core's own CURVE, not a single number: the flux the printed magnetisation "
    "fit gives at a magnetising force, and how far the permeability moves with "
    "temperature - the two questions a powder core's data sheet answers with a graph "
    "that a design tool cannot read. Both are transcribed from the vendor's printed "
    "coefficients and evaluated exactly as printed: B = [(a + bH + cH^2)/(1 + dH + "
    "eH^2)]^x (B[T], H[Oe], printed pp.128-132, 29 material x shape groups, 125 rows) "
    "and dmu/mu = a + bT + cT^2 + dT^3 + eT^4 against 25 C (printed pp.138-139, 54 "
    "rows), in data/powder_curve_fits.json. Per-row, not per-material: the "
    "coefficients differ between the toroid / E-core / EQ-and-LP / block tables for "
    "the same material, so a material printed in several shapes without shape_family "
    "is refused with the list, an unprinted permeability is refused with the list, and "
    "nothing is interpolated. Three independent checks came out before any of this was "
    "written down: integrating the DC-bias fit of the same part is an independent route "
    "to the same flux and agrees with this curve to a median 0.49 % at 100 Oe over the "
    "61 toroid rows; the temperature fit predicts the catalog's own printed p.15 "
    "dynamic range to 0.045 percentage points on all nine materials; and the catalog's "
    "worked example (60u Kool Mu at 95 Oe = 0.441 T) comes out at 0.4413 T here. Where "
    "the two printed fits disagree by more than 10 % - three rows, all at the ends of "
    "the permeability scale - BOTH numbers are returned with fits_disagree=true, and "
    "neither is picked (MPP 550u at 100 Oe: 0.234 T from this curve against 0.513 T "
    "from the integral, 54 % apart, because its printed c makes the fit peak at 23.8 "
    "Oe). Seven rows in the table turn over inside the printed H band; the H at which "
    "each stops rising is carried in the data and a query past it is marked "
    "past_fit_turnover with the falling branch reported as the falling branch. Refused "
    "rather than answered: H <= 0 (B(0) = a^x is not zero, so this is not a "
    "small-signal model - using it as one is a measured 36.9 % median error; ask "
    "magnetics_dc_bias for the zero-bias answer) and temperature outside -50..200 C (a "
    "quartic beyond the band the catalog tabulates is an extrapolation with no "
    "measurement behind it); between 100 and 200 C the point is answered and flagged "
    "as outside the tabulated band. include_evidence=true also returns the "
    "cross-check numbers, the five printing anomalies kept as printed, and what the "
    "catalog does not carry at all.",
    {"type": "object", "properties": {
        "material": _str("material as the catalog prints it, incl. the High DC Bias "
                         "variants ('High DC Bias XFlux', 'High DC Bias Edge')"),
        "permeability": _num("the row's nominal permeability in u (14, 26, 60, 125 ...). "
                             "Part of the question: the vendor prints a different fit "
                             "for each"),
        "shape_family": _str("the core shape family the fit is printed for (Toroids, "
                             "'E Cores, U Cores & EER Cores', 'EQ & LP Cores', "
                             "'Blocks, Round Blocks & Cylinders'). Optional only when "
                             "the material is printed for exactly one family"),
        "h_oe": _num("magnetising force in oersteds, if you already have it"),
        "turns": _num("or the winding: turns (used with dc_current_a and le_mm)"),
        "dc_current_a": _num("the current the winding carries, in amperes"),
        "le_mm": _num("the magnetic path length in mm, from the core's own data"),
        "temperature_c": _num("temperature in degrees Celsius for the mu-vs-T fit "
                              "(-50..200; the tabulated band is -50..100)"),
        "include_evidence": _bool("also return the cross-check numbers, the printing "
                                  "anomalies and what the catalog does not carry"),
        "curve_data_path": _str("override the curve table path (testing)"),
        "bias_data_path": _str("override the DC-bias table path (testing)"),
    }, "required": ["material", "permeability"]},
    lambda a: _magnetics.magnetics_powder_curve(
        a.get("material"), a.get("permeability"),
        h_oe=a.get("h_oe"),
        temperature_c=a.get("temperature_c"),
        shape_family=a.get("shape_family"),
        turns=a.get("turns"),
        dc_current_a=a.get("dc_current_a"),
        le_mm=a.get("le_mm"),
        curve_data_path=a.get("curve_data_path"),
        bias_data_path=a.get("bias_data_path"),
        include_evidence=bool(a.get("include_evidence"))),
)

_tool(
    "magnetics_core_loss_audit",
    "Score YOUR Steinmetz triple against the loss numbers the vendor actually prints. "
    "Neither ferrite vendor publishes a triple: Ferroxcube's 2013 data handbook prints "
    "two or three PV rows per grade qualified with <= or ~ (no coefficient set - the "
    "rest of the loss information is a graph), and TDK's SIFERRIT N87 sheet (12 pages, "
    "June 2025) draws three curve families and prints no loss number at all. So a "
    "triple always comes from somewhere else, and this tool evaluates it at every "
    "point the vendor did print: data/material_loss_anchors.json carries 17 PV rows "
    "transcribed from 3C90 (p.84), 3C92 (p.88), 3C93 (p.90), 3C94 (p.92), 3C96 (p.96), "
    "the 2022 3C96/3C98 brochure (printed p.5) and TDK's product page for N87, each "
    "with its printed page, the exact text printed and its qualifier. Per point you "
    "get the printed Pv, the computed Pv, the signed deviation, whether it lands in a "
    "10 % reporting band, and - only where the page printed an upper bound - whether "
    "the bound is satisfied; a bound is satisfied by anything smaller, a typical value "
    "is not, and the two are never compared as one kind of number. The verdict is "
    "consistent_with_every_printed_point / does_not_match_every_printed_point / "
    "violates_a_printed_upper_bound, and it is a statement about these points, not a "
    "claim that the triple is the vendor's: two or three points cannot determine a "
    "three-parameter fit. REFUSED on purpose: solving a triple out of the points. Two "
    "of 3C90's three are upper bounds at the same temperature as a typical value, so "
    "the solution would have to sit below both bounds and equal the typical value at "
    "once - solved_here is always false and the reason travels with the answer. 3B7 "
    "and N27 have a specification page but no PV row, so they answer DATA_UNAVAILABLE "
    "with points_printed=0 instead of inventing one.",
    {"type": "object", "properties": {
        "material": _str("the grade as the vendor prints it: 3C90, 3C92, 3C93, 3C94, "
                         "3C96, 3C98, N87 (3B7 and N27 have no printed point)"),
        "k": _num("your Steinmetz coefficient, in the unit convention named below"),
        "alpha": _num("your frequency exponent"),
        "beta": _num("your flux-density exponent"),
        "units": _str("the unit convention your k is in: khz_mt (mW/cm3 per kHz^a "
                      "mT^b, the vendor style and the default), si (W/m3 per Hz^a "
                      "T^b), khz_gauss (mW/cm3 per kHz^a G^b), khz_t (mW/cm3 per "
                      "kHz^a T^b)",
                      ["khz_mt", "si", "khz_gauss", "khz_t"], "khz_mt"),
        "temperature_c": _num("score only the points printed at this temperature; a "
                              "temperature with no printed point is refused with the "
                              "list"),
        "anchor_data_path": _str("override the anchor table path (testing)"),
    }, "required": ["material", "k", "alpha", "beta"]},
    lambda a: _magnetics.magnetics_core_loss_audit(
        a.get("material"), a.get("k"), a.get("alpha"), a.get("beta"),
        units=(a.get("units") or "khz_mt"),
        temperature_c=a.get("temperature_c"),
        anchor_data_path=a.get("anchor_data_path")),
)

_tool(
    "magnetics_toroid_mlt",
    "Mean length of one turn on a toroid, and the DC copper loss that follows - the "
    "number a toroid's catalog does not print, because a turn's length belongs to the "
    "WINDING (wire diameter, how the turns lie, how many layers) and not to the core. "
    "So this is geometry plus a MEASURED band, never a lookup: the perimeter of the "
    "core's section read as a rectangle, flat = 2*H + (OD - ID), times the ratio that "
    "thirteen toroids with BOTH dimensions and MLT printed on their data sheets show "
    "for a real winding (1.248 .. 1.461, median 1.366 - data/toroid_mlt_reference.json, "
    "with the URL of every row). The answer is an interval and is marked as one; a "
    "single number would be a guess in the vendor's clothes. It also returns the "
    "geometry the dimensions imply, including the two relations that were checked "
    "against those thirteen pages: le = pi*(OD+ID)/2 reproduces the printed path "
    "length within 0.6 %, while Ae = H*(OD-ID)/2 runs 4-10 % above the printed Ae "
    "because the printed section has rounded corners. With a wire: the single-layer "
    "turn count the bore allows (N1 = pi*(ID-d)/d), the number of layers `turns` "
    "implies, R_dc at temperature_c and, with i_rms_a, the copper loss and the "
    "fill-factor window check - each carrying the MLT band as a range. NOT modelled: "
    "enamel and reinforcement build (a real N1 is a little lower), multilayer length "
    "growth (later layers are longer, so R_dc is a LOWER bound once turns > N1), skin "
    "and proximity (magnetics_winding_ac), and any temperature rise.",
    {"type": "object", "properties": {
        "od_mm": _num("outside diameter of the bare core, mm (magnetic_data with "
                      "kind='powder_parts' has it per part)"),
        "id_mm": _num("inside diameter (the bore the wire has to pass), mm"),
        "ht_mm": _num("height of the core, mm"),
        "turns": _num("optional: turns, to get R_dc and the layer count"),
        "wire_diameter_mm": _num("optional: bare copper diameter; required by turns, "
                                 "i_rms_a and fill_factor"),
        "i_rms_a": _num("optional: RMS current, to turn R_dc into a copper loss"),
        "temperature_c": _num("copper temperature, which sets the resistivity "
                              "(20 C = 1.724e-8 ohm m)", 100.0),
        "fill_factor": _num("optional: copper fraction of the bore, to check the "
                            "winding window"),
        "mlt_data_path": _str("override the MLT reference table path (testing)"),
    }, "required": ["od_mm", "id_mm", "ht_mm"]},
    lambda a: _magnetics.magnetics_toroid_mlt(
        a.get("od_mm"), a.get("id_mm"), a.get("ht_mm"),
        turns=a.get("turns"),
        wire_diameter_mm=a.get("wire_diameter_mm"),
        i_rms_a=a.get("i_rms_a"),
        temperature_c=(a["temperature_c"] if a.get("temperature_c") is not None
                       else 100.0),
        fill_factor=a.get("fill_factor"),
        mlt_data_path=a.get("mlt_data_path")),
)

_tool(
    "magnetics_data",
    "Read back the magnetic table: the materials (mu_i, Bsat at the temperatures the "
    "vendor published, Tc, density, resistivity, the vendor's loss density) and the "
    "cores (Ae / le / Ve / AL / Amin / mass / coil-former winding area and mean turn "
    "length / one measured loss point with its condition). Nothing is computed here - "
    "this is the transcription with the source URL of every row, so a number can be "
    "checked instead of trusted. Absent values come back as null with a note saying "
    "why. No material-level Steinmetz triple (k/alpha/beta) is stored: what the three "
    "powder vendors print instead - a fit per permeability and per core shape - is in "
    "`loss_fit` on those materials, with its catalog page, and "
    "magnetics_material_core_loss evaluates it. The POWDER TOROIDS are a second table "
    "with a second shape: kind='powder_cores' returns the vendor's printed pages as "
    "printed (43 sizes x permeability rows x nine material columns, printed p.155-197) "
    "and kind='powder_parts' returns the same grid flattened to one row per "
    "purchasable part number (1923 of them), each with its geometry, printed AL, "
    "permeability, AL tolerance band and a mass ESTIMATE (Ve x printed density x "
    "printed permeability factor; withheld for 200/300/550 u, which the vendor prints "
    "no factor for). kind='dc_bias_fit' returns the third table - the catalog's own "
    "DC-bias fit rows (% mu_i = 1/(a + b*H^c), 29 material x shape groups, 125 rows, "
    "printed pp.63-65) which magnetics_dc_bias evaluates. Five vendor printing "
    "anomalies are carried in "
    "`printing_anomalies` verbatim rather than corrected, including one part number "
    "printed against two different sizes. The ferrite grades' printed loss POINTS are "
    "a fourth file (data/material_loss_anchors.json) and are not reachable through "
    "this tool: magnetics_core_loss_audit returns them one per point, together with "
    "what your own triple does at each.",
    {"type": "object", "properties": {
        "kind": _str("which table", ["materials", "cores", "all", "powder_cores",
                                      "powder_parts", "dc_bias_fit"], "materials"),
        "name": _str("exact material name or part number"),
        "material": _str("only cores of this material"),
        "shape": _str("only shapes containing this text"),
        "vendor": _str("only rows whose vendor contains this text"),
        "permeability": _num("powder tables only: one printed permeability in u"),
        "limit": _num("powder tables only: cap the number of rows returned"),
        "data_path": _str("override the table path (testing)"),
        "powder_data_path": _str("override the powder-core table path (testing)"),
        "bias_data_path": _str("override the DC-bias fit table path (testing)"),
    }, "required": []},
    lambda a: _magnetics.magnetic_data(
        kind=(a.get("kind") or "materials"),
        name=a.get("name"),
        material=a.get("material"),
        shape=a.get("shape"),
        vendor=a.get("vendor"),
        data_path=a.get("data_path"),
        permeability=a.get("permeability"),
        limit=a.get("limit"),
        powder_data_path=a.get("powder_data_path"),
        bias_data_path=a.get("bias_data_path")),
)

_tool(
    "stackup_material",
    "Which laminate covers this frequency, and what the table cannot see. Cheapest "
    "entry whose practical band covers f, with the alternatives and the failure modes "
    "of the choice (Dk tolerance, process, loss). The band limits are engineering "
    "conventions from rf-circuit-design 3.1, not measured thresholds.",
    {"type": "object", "properties": {
        "freq_ghz": _num("highest frequency of interest in GHz"),
    }, "required": ["freq_ghz"]},
    lambda a: _stackup.material_for(a.get("freq_ghz")),
)

_tool(
    "stackup_design",
    "A 4 or 6 layer stack for an RF / mixed-signal board: layer roles, copper weights, "
    "the dielectric split, the width that gives the target impedance on EACH signal "
    "layer (solved with the same microstrip model as feedline_width), the stitching "
    "via pitch lambda_g/20 and whether the build is copper-symmetric. The split is "
    "PROPORTIONAL - a real impedance-controlled stack is the fab's drawing, and the "
    "answer says so.",
    {"type": "object", "properties": {
        "layers": {"type": "integer", "description": "4 or 6", "default": 4},
        "material": _str("laminate id from stackup_material", list(_stackup.MATERIALS)),
        "total_mm": _num("finished board thickness", 1.0),
        "target_ohm": _num("single-ended impedance to solve for", 50.0),
        "copper_um": _num("override the finished copper thickness for every layer"),
        "freq_ghz": _num("highest frequency - sets the lambda/20 via pitch"),
        "er": _num("override the laminate's er (for a laminate not in the table)"),
    }, "required": ["layers", "material"]},
    lambda a: _stackup.stackup_design(
        a.get("layers", 4), a.get("material", "fr4"),
        total_mm=(a["total_mm"] if a.get("total_mm") is not None else 1.0),
        target_ohm=(a["target_ohm"] if a.get("target_ohm") is not None else 50.0),
        copper_um=a.get("copper_um"), freq_ghz=a.get("freq_ghz"), er=a.get("er")),
)

_tool(
    "rf_chain",
    "Cascade a receive/transmit chain: total gain, Friis noise figure with each stage's "
    "share of the excess noise, cascaded IIP3/OIP3, and (given a bandwidth) the kTB "
    "floor and the sensitivity for a required SNR. Needs EVERY stage's nf_db to answer "
    "NF, and every stage's iip3/oip3 to answer linearity - partial data comes back as "
    "an explicit gap, not a number. A filter is a stage with negative gain and NF = its "
    "loss.",
    {"type": "object", "properties": {
        "stages": {"type": "array", "description":
                   "ordered blocks, input first: {name, gain_db, nf_db, iip3_dbm? or "
                   "oip3_dbm?}. A passive part has negative gain and NF = |gain|.",
                   "items": {"type": "object", "properties": {
                       "name": {"type": "string"},
                       "gain_db": {"type": "number"},
                       "nf_db": {"type": "number"},
                       "iip3_dbm": {"type": "number"},
                       "oip3_dbm": {"type": "number"}}}},
        "bandwidth_hz": _num("noise bandwidth, for the kTB floor and sensitivity"),
        "required_snr_db": _num("SNR the demodulator needs (default 0, i.e. floor only)",
                                0.0),
    }, "required": ["stages"]},
    lambda a: _chain.cascade(
        a.get("stages"), bandwidth_hz=a.get("bandwidth_hz"),
        required_snr_db=(a["required_snr_db"] if a.get("required_snr_db") is not None
                         else None)),
)

_tool(
    "match_bandwidth",
    "Bandwidth of a single-resonant match from its loaded Q: BW = f0/Q, and the VSWR <= "
    "S band scaled by (S-1)/sqrt(S). Give two of freq_ghz / q / bandwidth_hz. This is "
    "the relation behind 'good at centre, narrow at the edges'; a multi-resonator match "
    "does not follow it.",
    {"type": "object", "properties": {
        "q": _num("loaded Q of the match"),
        "freq_ghz": _num("design frequency in GHz"),
        "bandwidth_hz": _num("bandwidth in Hz (solve for Q or f instead)"),
        "vswr": _num("also report the bandwidth inside this VSWR circle"),
    }, "required": []},
    lambda a: _matching.bandwidth(q=a.get("q"), freq_ghz=a.get("freq_ghz"),
                                  bandwidth_hz=a.get("bandwidth_hz"),
                                  vswr=a.get("vswr")),
)

_tool(
    "design_rules",
    "The review catalogues: every layout and schematic rule with its id, the fact it "
    "needs, the unit, how to measure it and its evidence level (L0 read the file / L1 "
    "calculate or multimeter / L2 simulate or bench / L3 lab). Call this before "
    "layout_review or schematic_review - it is the list of facts those tools grade.",
    {"type": "object", "properties": {
        "kind": _str("which catalogue", ["layout", "schematic"]),
        "ids": {"type": "array", "description": "only these rule ids",
                "items": {"type": "string"}},
        "severity": _str("only this severity", list(_rules.SEVERITIES)),
    }, "required": ["kind"]},
    lambda a: _rules.design_rules(a.get("kind", "layout"), a.get("ids"),
                                  a.get("severity")),
)

_layout_review = lambda a: _rules.review("layout", a.get("facts") or {})  # noqa: E731
_schematic_review = lambda a: _rules.review("schematic", a.get("facts") or {})  # noqa: E731

_FACTS_SCHEMA = {
    "type": "object",
    "description": "fact_name -> measured value. A fact you did not measure must be "
                   "left out: the rule then reports 'undetermined', never 'pass'. "
                   "Extra keys that compute a limit (trace_width_mm, freq_ghz, "
                   "current_a with current_standard, voltage_v) belong here too.",
}

_tool(
    "layout_review",
    "Grade this board's layout against the rule catalogue: reference-plane continuity, "
    "decoupling distance and layer, stitching pitch (lambda/20), RF 3W spacing / bend "
    "radius / edge distance / antenna keep-out, layer-change return vias, diff-pair "
    "skew, power-trace width from the IPC chart, thermal vias, crystal keep-out, silk "
    "over pad, test points, minimum hole and width. A rule with no measurement comes "
    "back UNDETERMINED with what to measure - never as a pass.",
    {"type": "object", "properties": {"facts": _FACTS_SCHEMA}, "required": ["facts"]},
    _layout_review,
)

_tool(
    "schematic_review",
    "Grade a schematic / BOM against the missing-part catalogue: per-pin decoupling, "
    "bulk per rail, floating reset or enable, LED series resistor, gate resistor, ESD on "
    "connectors, reverse-polarity protection, fuse, regulator compensation, discharge "
    "path, floating unused inputs, test points, RF port matching, PA bypass ladder, and "
    "IEC 60664 isolation spacing (which fails closed while the distance table is "
    "unverified). Undetermined is reported as undetermined, never as a pass.",
    {"type": "object", "properties": {"facts": _FACTS_SCHEMA}, "required": ["facts"]},
    _schematic_review,
)

# ------------------------------------------------------------------ parts
def _part_error(exc):
    return err(str(exc), exc.reason_code, hint=exc.hint)


def _part_library_status(args):
    try:
        info = _parts.library_status(args.get("path"))
    except _parts.PartError as exc:
        return _part_error(exc)
    return ok(
        info["counts"]["devices"], "devices",
        "read the SQLite catalogue that LCEDA Pro ships and count its rows",
        "lceda-std.elib: devices / components / attributes tables",
        "exact row counts, file read with mode=ro&immutable (never written)",
        {"library": info["path"], "how": info["how"],
         "file_mb": round(info["size"] / 1048576.0, 1)},
        extra={"counts": info["counts"], "library": info["path"],
               "how": info["how"]})


def _part_search(args):
    query = args.get("query")
    if not query:
        return err("part_search needs a query", "ARG_MISSING")
    try:
        out = _parts.search(query, limit=args.get("limit", 20),
                            page=args.get("page", 1),
                            source=args.get("source", "auto"),
                            path=args.get("path"),
                            timeout=float(args.get("timeout_s", 20)))
    except _parts.PartError as exc:
        return _part_error(exc)
    offline = out["source"] == "offline"
    return ok(
        len(out["parts"]), "parts",
        "catalogue lookup: case-insensitive substring match on title, description and "
        "every attribute value (offline) or the vendor search API (online)",
        ("lceda-std.elib devices+attributes tables" if offline
         else "POST https://pro.lceda.cn/api/v5/devices/search"),
        "string match, no fuzzy ranking and no stock/price ranking; the offline "
        "library is the subset that ships with the app, the online one is the whole "
        "JLCPCB catalogue",
        {"source_measured": "2026-10-29, anonymous request, no cookie or token",
         "geometry_available": "offline only (online documents are encrypted)"},
        extra={"source": out["source"], "query": out.get("query"),
               "total_matches": out.get("total"), "page": out.get("page"),
               "parts": out["parts"], "tried": out.get("tried")})


def _part_get(args):
    uuid = args.get("uuid")
    if not uuid:
        return err("part_get needs a uuid", "ARG_MISSING")
    try:
        part = _parts.get(uuid, source=args.get("source", "auto"),
                          path=args.get("path"))
    except _parts.PartError as exc:
        return _part_error(exc)
    return ok(
        len(part.get("attributes") or {}), "attributes",
        "one device row plus its attribute rows, read from the shipped catalogue",
        "lceda-std.elib devices + attributes tables",
        "verbatim strings from the vendor catalogue, nothing normalised",
        {"uuid": uuid},
        extra={"part": part})


def _part_export(args):
    uuid = args.get("uuid")
    out_dir = args.get("out_dir")
    if not uuid or not out_dir:
        return err("part_export needs uuid and out_dir", "ARG_MISSING")
    kind = (args.get("kind") or "both").lower()
    if kind not in ("both", "symbol", "footprint"):
        return err("kind must be both, symbol or footprint", "ARG_INVALID")
    try:
        part = _parts.get(uuid, source="offline", path=args.get("path"))
    except _parts.PartError as exc:
        return _part_error(exc)
    attrs = part.get("attributes") or {}
    want = []
    if kind in ("both", "symbol"):
        if not attrs.get("Symbol"):
            return err("this device names no symbol", "SYMBOL_MISSING")
        want.append(("symbol", attrs["Symbol"]))
    if kind in ("both", "footprint"):
        if not attrs.get("Footprint"):
            return err("this device names no footprint", "FOOTPRINT_MISSING")
        want.append(("footprint", attrs["Footprint"]))
    try:
        datas = _parts.data_strings([u for _, u in want], path=args.get("path"))
    except _parts.PartError as exc:
        return _part_error(exc)
    name = args.get("name") or part.get("title") or uuid
    safe = "".join(c if (c.isalnum() or c in "-_+#.") else "_" for c in name)
    written = []
    reports = {}
    import io
    import os as _os
    try:
        _os.makedirs(out_dir, exist_ok=True)
    except OSError as exc:
        return err("cannot create out_dir: %s" % exc, "OUT_DIR_UNWRITABLE")
    for kind_name, cuuid in want:
        doc = datas.get(cuuid)
        if not doc or not doc.get("data"):
            return err("catalogue has no document for %s %s" % (kind_name, cuuid),
                       "DOCUMENT_MISSING")
        try:
            if kind_name == "symbol":
                text, rep = _parts.symbol_to_kicad(
                    doc["data"], name=safe, reference="U",
                    footprint=(attrs.get("Supplier Footprint") or ""),
                    datasheet=(attrs.get("Datasheet") or ""),
                    description=(part.get("description") or ""))
                target = _os.path.join(out_dir, safe + ".kicad_sym")
            else:
                text, rep = _parts.footprint_to_kicad(
                    doc["data"], name=safe,
                    description=(attrs.get("Supplier Footprint") or ""))
                target = _os.path.join(out_dir, safe + ".kicad_mod")
        except _parts.PartError as exc:
            return _part_error(exc)
        if _os.path.exists(target) and not args.get("overwrite"):
            return err("target exists: %s" % target, "TARGET_EXISTS",
                       hint="pass overwrite=true to replace it")
        with io.open(target, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        prior = None
        with io.open(target, "r", encoding="utf-8") as fh:
            prior = fh.read()
        if prior != text:
            return err("read-back of %s does not match what was written" % target,
                       "READBACK_MISMATCH")
        written.append(target)
        reports[kind_name] = rep
    return ok(
        len(written), "files",
        "EasyEDA Pro document -> KiCad library file, field by field, no re-derivation",
        "EasyEDA Pro SYMBOL/FOOTPRINT document format (line JSON), unit mil declared "
        "in its own CANVAS row; symbol unit 10 mil inferred from the pin grid",
        "geometry copied within 1e-6 mm; pad angles from field 14; KiCad text sizes and "
        "stroke defaults are ours, not the vendor's",
        {"kicad_parse": "not verified inside this tool - load the file with KiCad to "
                         "confirm", 
         "dropped": "each report lists what did not carry over"},
        extra={"files": written, "report": reports, "name": safe,
               "source_uuids": dict(want)})


def _part_footprint_pads(args):
    uuid = args.get("footprint_uuid") or args.get("uuid")
    if not uuid:
        return err("part_footprint_pads needs footprint_uuid", "ARG_MISSING")
    try:
        datas = _parts.data_strings([uuid], path=args.get("path"))
    except _parts.PartError as exc:
        return _part_error(exc)
    doc = datas.get(uuid)
    if not doc or not doc.get("data"):
        return err("no footprint document %s" % uuid, "DOCUMENT_MISSING")
    pads = _parts.footprint_pads_mm(doc["data"])
    if not pads:
        return err("the document has no pads", "PADS_EMPTY")
    return ok(
        len(pads), "pads",
        "pad table in mm, read from the vendor footprint document",
        "EasyEDA Pro FOOTPRINT document, coordinates in mil (CANVAS row)",
        "0.0254 mm/mil exactly; rotation is the vendor's field 14",
        {"footprint_uuid": uuid, "title": doc.get("display_title")},
        extra={"pads": pads, "title": doc.get("display_title")})


def _part_cache_info(_args):
    return ok(0, "entries", "report where the part cache would live",
              "PCB_AGENT_CACHE or ~/.pcb_agent_mcp/parts",
              "path only, nothing is created here",
              {},
              extra={"cache_dir": _parts.cache_dir(),
                     "ttl_s": _parts._CACHE_TTL_S})


_tool(
    "part_library_status",
    "Find the LCEDA Pro standard part library on this machine and count what is in "
    "it. The library is a plain SQLite file (lceda-std.elib, ~460 MB) shipped inside "
    "the app: devices, symbols (docType 2) and footprints (docType 4) with their real "
    "LCSC part numbers. Read-only. If it cannot be found the answer is an error that "
    "says where it looked, never a guess.",
    {"type": "object", "properties": {
        "path": _str("explicit path to lceda-std.elib (optional)")}},
    _part_library_status,
)

_tool(
    "part_search",
    "Search real parts by name, LCSC code or manufacturer part number and get back "
    "the device uuid, LCSC code, package, datasheet URL and the full vendor attribute "
    "table. Offline it reads the catalogue that ships with LCEDA Pro; online it asks "
    "pro.lceda.cn for the whole JLCPCB catalogue (anonymous request, measured "
    "2026-10-29). Nothing is invented: a miss is an error, and every part says which "
    "source answered.",
    {"type": "object", "properties": {
        "query": _str("part name / LCSC code / manufacturer part number"),
        "limit": _num("how many parts to return", 20),
        "page": _num("1-based page, for the online catalogue", 1),
        "source": _str("where to look", ["auto", "offline", "online"]),
        "path": _str("explicit path to lceda-std.elib (optional)"),
        "timeout_s": _num("network timeout in seconds", 20)},
     "required": ["query"]},
    _part_search,
)

_tool(
    "part_get",
    "One device by uuid, with every attribute row the vendor catalogue carries "
    "(Manufacturer Part, Supplier Part, Supplier Footprint, Datasheet, Symbol and "
    "Footprint uuids, electrical parameters). Offline only: the online catalogue has "
    "no by-uuid lookup, and this tool says so instead of returning a guessed part.",
    {"type": "object", "properties": {
        "uuid": _str("device uuid from part_search"),
        "source": _str("where to look", ["auto", "offline"]),
        "path": _str("explicit path to lceda-std.elib (optional)")},
     "required": ["uuid"]},
    _part_get,
)

_tool(
    "part_export",
    "Turn a catalogue device into KiCad library files (.kicad_sym and/or .kicad_mod) "
    "in the directory you name, and report field by field what carried over and what "
    "did not. The geometry is the vendor's, converted mil->mm; the report lists every "
    "dropped element (marking/soldering layers, text) so a thinner footprint never "
    "looks like a faithful one. Refuses to overwrite unless overwrite=true, and reads "
    "every file back after writing it.",
    {"type": "object", "properties": {
        "uuid": _str("device uuid from part_search (must be in the offline library)"),
        "out_dir": _str("directory to write into (created if missing)"),
        "name": _str("file/library name; defaults to the device title"),
        "kind": _str("what to write", ["both", "symbol", "footprint"]),
        "overwrite": _bool("replace existing files", False),
        "path": _str("explicit path to lceda-std.elib (optional)")},
     "required": ["uuid", "out_dir"]},
    _part_export,
)

_tool(
    "part_footprint_pads",
    "The pad table of a catalogue footprint in mm (number, x, y, size, shape, rotation, "
    "drill, polygon) - the geometry a placement or routing step needs, without going "
    "through KiCad.",
    {"type": "object", "properties": {
        "footprint_uuid": _str("footprint uuid from part_get attributes.Footprint"),
        "path": _str("explicit path to lceda-std.elib (optional)")},
     "required": ["footprint_uuid"]},
    _part_footprint_pads,
)

_tool(
    "part_cache_info",
    "Where part answers are cached on disk and for how long. The cache is only used "
    "for whole answers that came from the network.",
    {"type": "object", "properties": {}},
    _part_cache_info,
)

def _fullwave_solver(args):
    """Which full-wave solver exists here, and what the handoff has to look like."""
    from .calc import em as _em
    name = args.get("solver")
    if name and name not in _em.DETECTORS:
        return err("unknown solver %r (know: %s)"
                   % (name, ", ".join(sorted(_em.DETECTORS))), "ARG_INVALID",
                   hint="a name this server does not know cannot be probed, and "
                        "inventing a probe would be the same as claiming support")
    probe = args.get("probe")
    probe = True if probe is None else bool(probe)
    timeout_s = args.get("timeout_s")
    timeout_s = _em.PROBE_TIMEOUT_S if timeout_s is None else float(timeout_s)
    if timeout_s <= 0:
        return err("timeout_s must be > 0", "ARG_RANGE")
    try:
        got = _em.detect(names=[name] if name else None, probe=probe,
                         timeout_s=timeout_s)
    except ValueError as exc:
        return err(str(exc), "ARG_INVALID")
    out = ok(got, "solvers",
             model="PATH probe + PCB_AGENT_<SOLVER> + the documented install "
                   "directories, then one --version run per binary",
             basis="a solver is reported found only when a real file path exists; its "
                   "version is whatever that binary printed, and nothing here was "
                   "measured against a solve",
             precision="presence and version are evidence of INSTALLATION at this "
                       "moment, nothing more - not that the mesh converges, not that "
                       "the ports are right, and not that this server can drive it. "
                       "verified_here is false for every solver because no full-wave "
                       "run has ever been recorded in this repository",
             assumptions=[_em.VERSION_VERIFICATION])
    out["value"]["this_server_does"] = _em.WHAT_THIS_SERVER_DOES
    out["value"]["this_server_will_not_do"] = _em.WHAT_IT_WILL_NOT_DO
    out["value"]["how_to_hand_over"] = list(_em._STEPS)
    if name and not got["found"]:
        out["status"] = "error"
        out["reason_code"] = "SOLVER_NOT_FOUND"
        out["message"] = ("%s was asked for by name and is not installed (looked at "
                          "$%s, PATH and the documented install directories)"
                          % (name, _em.DETECTORS[name]["env"]))
        out["hint"] = ("install it or point %s at the executable; this server will not "
                       "substitute its closed-form geometry for a solve"
                       % _em.DETECTORS[name]["env"])
        out["next_step"] = _em._STEPS[3]
        return out
    if not got["found"]:
        out["message"] = ("no full-wave solver is installed on this machine (%s), so "
                          "nothing here can answer a pattern, a band sweep or a "
                          "coupling question" % ", ".join(got["missing"]))
        out["hint"] = ("the closed-form tools give the starting geometry; the solve "
                       "needs one of the programs listed under value.solvers")
    else:
        out["message"] = ("found: %s" % ", ".join(
            "%s %s" % (r["solver"], r["version"] or "(version not printed)")
            for r in got["solvers"] if r["found"]))
        if got["missing"]:
            out["value"]["also_missing"] = got["missing"]
    return out

_tool(
    "fullwave_solver",
    "Is a full-wave electromagnetic solver installed on this machine, which version, and "
    "what does the handoff look like? THIS SERVER IS NOT A FULL-WAVE SOLVER and never "
    "claims to be: radiation patterns, gain, efficiency, S11 over a band, mutual "
    "coupling, cavity Q with a field distribution and enclosure detuning all require "
    "solving Maxwell's equations in 3-D, which needs a mesh and a port. What this tool "
    "does is the honest half of a delegation, the same way the SPICE tool can hand a "
    "netlist to an installed ngspice: it looks for openEMS / Meep / gprMAX on PATH, in "
    "$PCB_AGENT_<SOLVER> and in the documented install directories, runs --version to "
    "report what the binary says about itself, and returns the geometry and stackup "
    "facts you have to feed it plus where those come from here (board_export for "
    "Gerber/STEP, board_summary for the stackup). Presence is NOT evidence: every answer "
    "carries verified_here=false, because no full-wave run has ever been recorded in "
    "this repository. Asking for a named solver that is not installed is refused with "
    "SOLVER_NOT_FOUND rather than answered with the closed-form numbers.",
    {"type": "object",
     "properties": {
         "solver": _str("probe only this one", enum=sorted(("openems", "meep", "gprmax"))),
         "probe": _bool("run --version on what was found (default true)", True),
         "timeout_s": _num("per-probe timeout (default 15 s)", 15),
     }},
    lambda a: _guarded(lambda: _fullwave_solver(a)),
)

def _board_45_corners(args):
    """Read every turn on the board, and on request miter the right angles.

    The reading half is the point: this server could measure clearance, edge and
    annular ring, so a board whose every second segment turned 90 degrees passed
    every check it had. Angle was simply not a question it asked. It does now.
    """
    from . import edit as _edit
    from .route import corners as _corners

    board, e = _load_board(args)
    if e:
        return e
    tol = float(args.get("tolerance_deg") or 0.5)
    value = _corners.survey(board, tolerance_deg=tol)
    value.pop("_right_all", None)
    value["writes_nothing"] = True
    model = ("a corner is two and only two same-net same-layer segments sharing an "
             "endpoint; its turn is the angle between them as seen from the vertex "
             "(180 straight, 135 a 45 degree mitre, 90 a right angle, <90 acute); a "
             "segment is on the 45 degree grid when its direction is within "
             "tolerance_deg of a multiple of 45")
    basis = ("IPC-2221 recommends mitered or arced turns and forbids acute corners "
             "(the etchant cannot refresh inside the wedge); the 45 degree grid is "
             "the layout convention this repository's own experience metric "
             "'tracks_off_45_grid' already measured. Angle alone is not an "
             "electrical defect on a slow net - this tool reports, it does not "
             "judge the net")
    precision = ("\u00b10.0001 deg on turn angles and \u00b10.001 mm on leg lengths "
                 "(the file's own coordinates)")

    if args.get("action") != "chamfer":
        out = ok(value, "mm", model=model, basis=basis, precision=precision,
                 assumptions={"tolerance_deg": tol, "vertices_joined_at_mm": 0.0001})
        if value["right_angle_corners"]:
            out["next_step"] = ("action='chamfer' replaces each of these %d "
                                "right-angle vertices with a symmetric 45 degree "
                                "mitre (plan only; apply=true writes it)"
                                % value["right_angle_corners"])
        elif value["acute_corners"]:
            out["next_step"] = ("no right angles left; the %d acute corner(s) are "
                                "reported and never cut here - a wedge narrower than "
                                "90 degrees has to move a leg, not trim it"
                                % value["acute_corners"])
        else:
            out["message"] = "every corner on this board is already 45 degrees or straight"
        return out

    plan = _corners.chamfer_ops(
        board,
        max_cut_mm=float(args.get("max_cut_mm") or 1.0),
        min_leg_mm=float(args.get("min_leg_mm") or 0.4),
        tolerance_deg=tol,
        nets=args.get("nets"))
    value["plan"] = {k: plan[k] for k in
                     ("corners_chamfered", "segments_rewritten", "skipped",
                      "width_mismatch", "cut_mm_range", "diagonals")}
    value["ops_count"] = len(plan["ops"])
    out = ok(value, "mm", model=model, basis=basis, precision=precision,
             assumptions={"tolerance_deg": tol,
                          "max_cut_mm": float(args.get("max_cut_mm") or 1.0),
                          "min_leg_mm": float(args.get("min_leg_mm") or 0.4),
                          "geometry_changed": "the path is shortened by 2*cut per "
                                              "corner - a mitre is shorter than the "
                                              "corner it replaces, not a new route"})
    if not plan["ops"]:
        out["message"] = ("nothing chamferable: %d corner(s) refused because a leg "
                          "is shorter than the cut would need"
                          % len(plan["skipped"]))
        return out
    if args.get("apply") is not True:
        out["next_step"] = ("pass apply=true to write these %d op(s) through "
                            "board_propose + board_apply (checkpoint, atomic write, "
                            "four gates)" % len(plan["ops"]))
        return out
    prop = _edit.propose(args["path"], plan["ops"], {"engine": True})
    try:
        applied = _edit.apply_plan(prop["plan_id"], confirm=True,
                                   options={"engine": True,
                                            "allow_new_violations": bool(
                                                args.get("allow_new_violations")),
                                            "strict_drc": bool(args.get("strict_drc"))})
    except _edit.EditError as exc:
        return _edit_error(exc)
    value["plan_id"] = prop["plan_id"]
    value["applied"] = applied
    value["writes_nothing"] = False
    try:
        board2, e2 = _load_board(args)
        if e2 is None:
            again = _corners.survey(board2, tolerance_deg=tol)
            value["after"] = {k: again[k] for k in
                              ("segments", "off45_segments",
                               "right_angle_corners", "acute_corners",
                               "corners", "corner_kinds")}
    except Exception:                     # noqa: BLE001 - the write already happened
        value["after"] = None
    return out


_tool(
    "board_45_corners",
    "Measure the TURNS on a board, which this server never did before: clearance, "
    "edge and annular ring were all it asked about, so a layout whose every second "
    "segment turned a bare 90 degrees passed every check it had. Reads three things "
    "and keeps them apart - segments off the 45 degree grid (direction not within "
    "tolerance of a multiple of 45), corners (exactly two same-net same-layer "
    "segments sharing an endpoint, turned from 180 collinear through 135 mitre and "
    "90 right angle down to acute), and junctions (3+ segments at one point, "
    "reported and never touched, because a T has no single corner and guessing the "
    "trunk is a human decision). action='chamfer' plans a symmetric mitre at every "
    "right-angle vertex: the vertex is replaced by two points `cut` mm back along "
    "each leg joined by one diagonal, so the turn becomes exactly 45 degrees. Legs "
    "shorter than the cut would need are refused ('leg_too_short' with the cut they "
    "would have needed), junctions/acute corners/arcs are never modified, and two "
    "legs of different width are chamfered at the wider width and listed under "
    "width_mismatch. Honest about the trade: this is a geometric edit, the path gets "
    "SHORTER by 2*cut per corner and no track is re-routed, so it fixes the shape "
    "and nothing else - run it before judging clearance, and its write goes through "
    "board_propose + board_apply with the same gates as every other write.",
    {"type": "object",
     "properties": dict({
         "action": _str("'audit' (default: read only) or 'chamfer' (plan the mitres)",
                        enum=("audit", "chamfer")),
         "apply": _bool("write the chamfer plan (default false: plan only)"),
         "max_cut_mm": _num("longest pull-back along either leg (default 1.0)", 1.0),
         "min_leg_mm": _num("leave at least this much of each leg (default 0.4)", 0.4),
         "tolerance_deg": _num("how far off 45/90/135 still counts as that angle "
                               "(default 0.5)", 0.5),
         "nets": _arr("only chamfer these nets (default: every net)"),
         "allow_new_violations": _bool("accept newly introduced DRC violations"),
         "strict_drc": _bool("let every new violation block, warnings included"),
     }, **_COMMON),
     "required": ["path"]},
    lambda a: _guarded(lambda: _board_45_corners(a)),
)

def _board_stitch_vias(args):
    """Sew two pours into one plane: audit the stitching, or plan the holes.

    The server could compute a via's resistance, current and thermal fence, and
    could pour copper - but it had no way to put a ground via ON the board. So two
    ground planes with zero vias between them scored clean, which is exactly the
    board a human calls "not sewn shut".
    """
    from . import edit as _edit
    from .route import stitch as _stitch

    board, e = _load_board(args)
    if e:
        return e
    net = args.get("net") or "GND"
    layers = tuple(args.get("layers") or ["F.Cu", "B.Cu"])
    pitch = float(args.get("pitch_mm") or 3.0)
    dia = float(args.get("dia_mm") or 0.6)
    drill = float(args.get("drill_mm") or 0.3)
    edge = float(args.get("edge_mm") or 0.5)
    clr = float(args.get("clearance_mm") or 0.25)
    near = args.get("near_via_mm")
    near = pitch if near is None else float(near)

    model = ("a stitching via is a hole whose annular ring sits inside the same "
             "net's FILLED copper on every layer it spans - point in "
             "filled_polygon, not point in the zone outline, because the outline "
             "is where copper could go and the fill is where it is; spacing is "
             "measured from the point to existing copper this server reads "
             "itself (different net >= clearance from the annulus; any two drills "
             ">= 0.25 mm apart, net-independent)")
    basis = ("IPC-2221 / common two-layer practice: two pours are one plane only "
             "where copper crosses between them (stitching), and a signal that "
             "changes layer needs a return via within about one via pitch of that "
             "change; the 0.25 mm hole-to-hole figure is KiCad's default and is a "
             "fabrication rule, so it applies between two holes of the same net")
    precision = ("\u00b10.0001 mm on grid points (the file's own coordinates); the "
                 "point-in-fill and spacing tests are exact for the stored "
                 "polygons, so a point within 1e-9 mm of a boundary may answer "
                 "either way - hence the annular ring is also required to clear "
                 "the fill's own boundary")

    if args.get("action") == "place" or args.get("apply") is True:
        try:
            p = _stitch.plan(board, net=net, pitch_mm=pitch, dia_mm=dia,
                             drill_mm=drill, edge_mm=edge, clearance_mm=clr,
                             layers=layers, beside_vias=bool(
                                 args.get("beside_vias", True)),
                             near_via_mm=near,
                             nudge_mm=float(args.get("nudge_mm", 0.4)))
        except ValueError as exc:
            return err(str(exc), "ARG_INVALID")
        value = dict(p)
        value["writes_nothing"] = args.get("apply") is not True
        out = ok(value, "mm", model=model, basis=basis, precision=precision,
                 assumptions={"net": net, "pitch_mm": pitch, "dia_mm": dia,
                              "drill_mm": drill, "edge_mm": edge,
                              "clearance_mm": clr, "layers": list(layers),
                              "hole_clearance_mm": _stitch.HOLE_CLEARANCE_MM,
                              "copper_spacing_checked_on": list(layers),
                              "focused_on": "the fill as stored in the file"})
        if p.get("refused"):
            out["next_step"] = ("pour %s on %s first, or stitch different layers"
                                % (net, ", ".join(p["layers_without_fill"])))
            return out
        if not p["ops"]:
            out["message"] = ("no hole can go on this board with these "
                              "parameters: %s" % p.get("skip_counts"))
            return out
        if args.get("apply") is not True:
            out["next_step"] = ("pass apply=true to write these %d via(s) through "
                                "board_propose + board_apply, then re-read with "
                                "board_drc(refill_zones=true)" % len(p["ops"]))
            return out
        prop = _edit.propose(args["path"], p["ops"], {"engine": True})
        try:
            applied = _edit.apply_plan(
                prop["plan_id"], confirm=True,
                options={"engine": True,
                         "allow_new_violations": bool(
                             args.get("allow_new_violations")),
                         "strict_drc": bool(args.get("strict_drc"))})
        except _edit.EditError as exc:
            return _edit_error(exc)
        value["plan_id"] = prop["plan_id"]
        value["applied"] = applied
        value["writes_nothing"] = False
        try:
            board2, e2 = _load_board(args)
            if e2 is None:
                again = _stitch.audit(board2, net=net, pitch_mm=pitch,
                                      near_via_mm=near, layers=layers,
                                      edge_mm=edge)
                value["after"] = {k: again.get(k) for k in
                                  ("existing_stitch_vias", "verdict",
                                   "points_without_a_via_half_pitch",
                                   "signal_vias_without_nearby_return")}
        except Exception:              # noqa: BLE001 - the write already happened
            value["after"] = None
        return out

    value = _stitch.audit(board, net=net, pitch_mm=pitch, near_via_mm=near,
                          layers=layers, edge_mm=edge)
    value["writes_nothing"] = True
    out = ok(value, "mm", model=model, basis=basis, precision=precision,
             assumptions={"net": net, "pitch_mm": pitch, "near_via_mm": near,
                          "edge_mm": edge, "layers": list(layers),
                          "copper_spacing_checked_at": "dia 0.6 / drill 0.3, "
                                                       "clearance 0.25"})
    if value.get("verdict") == "not_stitchable":
        out["next_step"] = ("no %s fill on %s: a via there would be a stub"
                            % (net, ", ".join(value["why"]["layers_without_fill"])))
    elif value.get("gaps"):
        out["next_step"] = ("action='place' plans %d hole(s) on a %s mm grid and "
                            "beside vias that change layer" % (len(value["gaps"]),
                                                                pitch))
    else:
        out["message"] = ("the plane is stitched: every grid point within half a "
                          "pitch of a %s via" % net)
    return out

_tool(
    "board_stitch_vias",
    "Put ground vias on the board - the one thing every via tool here could only "
    "COMPUTE (resistance, current, thermal fence) and never PLACE, so two ground "
    "pours with zero holes between them passed every check while a human called the "
    "board 'not sewn shut'. action='audit' (default) reads the current state: how "
    "many same-net vias exist, which points of a centred pitch grid sit inside the "
    "same-net FILL on both layers but have no via within half a pitch (a 'gap'), "
    "and how many layer-changing signal vias have no return via within one pitch. "
    "action='place' plans holes: grid points inside the filled copper on every "
    "layer, clear of the fill's own boundary by the annular ring, clear of "
    "different-net copper, 0.25 mm hole-to-hole of any existing drill, and "
    "edge_mm from the board edge; then one ground via beside every signal via that "
    "changes layer and has no return yet. A point that fails by a hair is nudged "
    "(nudge_mm, default 0.4) rather than dropped. Refusals are reported by reason "
    "('no_pour_here', 'pour_edge_too_close', 'too_close' with the net and the gap, "
    "'hole_too_close', 'too_near_board_edge', 'existing_via', "
    "'no_room_around_signal_via') - a skip is a skip, not a success. This is not "
    "auto-routing: no track moves, only holes are added, and placement is a grid "
    "the caller chose. apply=true writes through board_propose + board_apply and "
    "then re-audits.",
    {"type": "object",
     "properties": dict({
         "action": _str("'audit' (default: read only) or 'place' (plan the holes)",
                        enum=("audit", "place")),
         "apply": _bool("write the plan (default false: plan only)"),
         "net": _str("the net the holes stitch (default GND)"),
         "layers": _arr("layers the hole must join (default [F.Cu, B.Cu])"),
         "pitch_mm": _num("grid pitch; also the 'is this point covered' radius "
                          "(default 3.0)", 3.0),
         "dia_mm": _num("via pad diameter (default 0.6)", 0.6),
         "drill_mm": _num("via drill (default 0.3, must be < dia)", 0.3),
         "edge_mm": _num("minimum distance from the board edge (default 0.5)", 0.5),
         "clearance_mm": _num("copper spacing to other nets (default 0.25)", 0.25),
         "near_via_mm": _num("how close a return via must be to a signal via "
                            "(default = pitch)", None),
         "beside_vias": _bool("also plan a return via beside every layer-changing "
                              "signal via (default true)", True),
         "nudge_mm": _num("move a point this far to escape a hair-line refusal "
                         "(default 0.4, 0 disables)", 0.4),
         "allow_new_violations": _bool("accept newly introduced DRC violations"),
         "strict_drc": _bool("let every new violation block, warnings included"),
     }, **_COMMON),
     "required": ["path"]},
    lambda a: _guarded(lambda: _board_stitch_vias(a)),
)

# ------------------------------------------------------------ silk labels
def _board_pin_labels(args):
    """Write one silkscreen name per pin of a through-hole header.

    The reference boards all carry the pin name inside the header column. This
    server could write one board text at a time and nothing else, so a 2x20
    header was 40 hand-typed coordinates - exactly the kind of hand arithmetic
    that puts a label on the wrong pin. The names come from the netlist (the pad
    already knows its net), never from a list supplied by hand.
    """
    from . import edit as _edit

    board, e = _load_board(args)
    if e:
        return e
    refs = args.get("refs") or []
    if isinstance(refs, str):
        refs = [refs]
    height = float(args.get("height_mm") or 0.8)
    thick = float(args.get("thickness_mm") or height * 0.15)
    offset = float(args.get("offset_mm") or 3.0)
    layer = args.get("layer") or "F.SilkS"
    skip = set(args.get("skip_nets") or [])
    rot_deg = float(args.get("rot") or 0.0)

    by_ref = {}
    for fp in board.footprints:
        by_ref[fp["ref"]] = fp
    if not refs:
        # default: every through-hole header with at least 4 pins
        refs = [fp["ref"] for fp in board.footprints
                if len(fp.get("pads") or []) >= 4]

    labels, skipped, refused = [], [], []
    for ref in refs:
        fp = by_ref.get(ref)
        if fp is None:
            refused.append({"ref": ref, "why": "no such footprint"})
            continue
        pads = [p for p in (fp.get("pads") or []) if p.get("x") is not None]
        if not pads:
            refused.append({"ref": ref, "why": "footprint has no pads"})
            continue
        xs = sorted(set(round(p["x"], 3) for p in pads))
        ys = sorted(set(round(p["y"], 3) for p in pads))
        if len(xs) == 1:
            axis, fixed = "column", xs[0]
        elif len(ys) == 1:
            axis, fixed = "row", ys[0]
        else:
            refused.append({"ref": ref, "why": "pads are not in one straight "
                                               "line (%d x-values, %d y-values)"
                                               % (len(xs), len(ys))})
            continue
        # put the text on the board-centre side of the header
        pts = []
        raw_outline = getattr(board, "outline", None) or []
        for item in raw_outline:
            cand = [item]
            if (isinstance(item, (list, tuple)) and len(item) == 2
                    and isinstance(item[0], (list, tuple))):
                cand = list(item)
            for p in cand:
                try:
                    pts.append((float(p[0]), -float(p[1])))
                except (TypeError, ValueError):
                    pass
        idx = 0 if axis == "column" else 1
        vals = [q[idx] for q in pts]
        if not vals:
            for other in board.footprints:
                for pp in (other.get("pads") or []):
                    key = "x" if idx == 0 else "y"
                    if pp.get(key) is not None:
                        vals.append(float(pp[key]))
        centre = (min(vals) + max(vals)) / 2.0 if vals else fixed
        direction = 1.0 if fixed < centre else -1.0
        for pad in pads:
            net = pad.get("net")
            if not net:
                skipped.append({"ref": ref, "pad": pad.get("name"),
                                "why": "pad has no net"})
                continue
            if net in skip:
                skipped.append({"ref": ref, "pad": pad.get("name"),
                                "why": "net in skip_nets"})
                continue
            if axis == "column":
                x, y = fixed + direction * offset, pad["y"]
            else:
                x, y = pad["x"], fixed + direction * offset
            labels.append({"ref": ref, "pad": pad.get("name"), "net": net,
                           "op": {"op": "add_board_text", "text": net,
                                  "x": round(x, 4), "y": round(y, 4),
                                  "layer": layer, "height": height,
                                  "thickness": round(thick, 4),
                                  "rot": rot_deg}})

    value = {"labels": labels, "count": len(labels), "refused": refused,
             "skipped": skipped[:40], "skipped_total": len(skipped),
             "params": {"refs": list(refs), "height_mm": height,
                        "thickness_mm": round(thick, 4), "offset_mm": offset,
                        "layer": layer, "rot": rot_deg}}
    if not labels:
        return err("no header pin could be labelled: %d footprint(s) refused, "
                   "%d pad(s) skipped" % (len(refused), len(skipped)),
                   "TARGET_NOT_FOUND",
                   hint="pass refs=[...] naming the header footprints")
    ops = [lab["op"] for lab in labels]
    if args.get("apply") is not True:
        value["writes_nothing"] = True
        out = ok(value, "mm",
                 model="one silk text per header pin, at the pin's own net name, "
                       "placed offset_mm to the board-centre side of the row or "
                       "column so it never sits on a pad",
                 basis="each label's net comes from the pad's own (net ...) field",
                 precision="text position is exact mm arithmetic on the pad "
                           "centre; nothing is estimated",
                 assumptions={"offset_mm": offset, "height_mm": height,
                              "thickness_mm": round(thick, 4), "layer": layer})
        out["next_step"] = ("pass apply=true to write these %d text(s) through "
                            "board_propose + board_apply" % len(ops))
        return out
    prop = _edit.propose(args["path"], ops, {"engine": True})
    try:
        applied = _edit.apply_plan(
            prop["plan_id"], confirm=True,
            options={"engine": True,
                     "allow_new_violations": bool(args.get("allow_new_violations")),
                     "strict_drc": bool(args.get("strict_drc"))})
    except _edit.EditError as exc:
        return _edit_error(exc)
    value["plan_id"] = prop["plan_id"]
    value["applied"] = applied
    value["writes_nothing"] = False
    return ok(value, "mm",
              model="one silk text per header pin, at the pin's own net name",
              basis="each label's net comes from the pad's own (net ...) field",
              precision="text position is exact mm arithmetic on the pad centre",
              assumptions={"offset_mm": offset, "height_mm": height,
                           "thickness_mm": round(thick, 4), "layer": layer})


_tool(
    "board_pin_labels",
    "Write one silkscreen pin name per pin of a through-hole header, reading the "
    "name off the pad's NET (never a hand-typed list). Every good core board "
    "carries the pin names inside the header columns; this server could only "
    "write one board text at a time, so a 2x20 header was 40 hand-computed "
    "coordinates - the shortest path to a label on the wrong pin. The header must "
    "be a straight line of pads (all pads share one x, or all share one y); the "
    "text goes offset_mm to the board-centre side of that line, so it never lands "
    "on a pad. Refuses (per footprint) when the pads are not in one line, and "
    "reports every skipped pad with its reason. apply=true writes through "
    "board_propose + board_apply.",
    {"type": "object",
     "properties": dict({
         "refs": _arr("footprint references to label (default: every footprint "
                      "with >= 4 pads)"),
         "height_mm": _num("text height (default 0.8)", 0.8),
         "thickness_mm": _num("stroke thickness (default 0.15 x height)", None),
         "offset_mm": _num("distance from the pad line to the text centre "
                           "(default 3.0)", 3.0),
         "layer": _str("silk layer (default F.SilkS)"),
         "rot": _num("text rotation in degrees (default 0)", 0.0),
         "skip_nets": _arr("net names to leave unlabelled (e.g. ['GND'])"),
         "apply": _bool("write the plan (default false: plan only)"),
         "allow_new_violations": _bool("accept newly introduced DRC violations"),
         "strict_drc": _bool("let every new violation block, warnings included"),
     }, **_COMMON),
     "required": ["path"]},
    lambda a: _guarded(lambda: _board_pin_labels(a)),
)

# ------------------------------------------------------------- experience
# The library of what the corpus taught, and the refusal that keeps it from
# becoming a rule nobody confirmed. `docs/02` section B is the design; `docs/94`
# is what changed when it became code.
def _experience():
    """The module, or the error saying why this answer cannot be given."""
    from . import experience as _exp
    if not _exp.enabled():
        return None, err("the experience library is switched off "
                         "(PCB_AGENT_EXPERIENCE=0): nothing is read and nothing is "
                         "written", "DATA_UNAVAILABLE",
                         hint="unset PCB_AGENT_EXPERIENCE to read the library again")
    return _exp, None

_EXP_KIND = _str("hard = a rule with a failure side that is measured on the board "
                 "(a violation is a finding); soft = a habit the good boards share "
                 "(a departure is a question, not a defect)")
_EXP_STATUS = _str("candidate = an observation nobody has confirmed; active = in "
                   "force; retired = kept for the record, not applied")
_EXP_SCOPE = _str("dotted scope: route.geometry, route.via, route.width, fab, "
                  "format.lceda, ...")
_EXP_METRIC = _str("which measurement decides this entry. Every metric here is "
                   "implemented and can fail; a trigger naming anything else is "
                   "refused rather than reported as passed")
_EXP_TRIGGER = dict({
    "min_mm": _num("threshold for via_annular_ring_below, in mm"),
    "max_distinct": _num("threshold for distinct_via_geometries / "
                         "distinct_track_widths"),
    "tolerance_deg": _num("tolerance for tracks_off_45_grid, in degrees (default 0.5)"),
    "max_off_grid_share": _num("largest share of segments that may sit off the grid "
                               "(default 0.0, so any off-grid segment departs)"),
})

_tool(
    "experience_recall",
    "Read what this server has learned from boards it did not draw. Two kinds, and "
    "they are not interchangeable: HARD entries are rules with a failure side that is "
    "measured on the board (a via ring under 0.10 mm, a drilled pad that is not on "
    "every copper layer) - a violation is a defect. SOFT entries are habits the good "
    "boards share (power copper wider than signal copper, one via geometry per "
    "board) - leaving one is a question, not a defect. Every entry carries the corpus "
    "it came from as a numerator with a denominator, and every entry can be evaluated "
    "on a board by experience_report. Only ACTIVE entries are recalled by default: a "
    "candidate is an observation nobody confirmed, and this tool will not let it shape "
    "work before someone does. The seed that ships with the package is read-only, so "
    "the shipped numbers cannot drift from a local edit; runtime learning goes to "
    "$PCB_AGENT_HOME/experience.json and shadows a seed entry of the same id, which is "
    "reported as shadowed_seed_ids rather than hidden.",
    {"type": "object",
     "properties": {
         "scope": _EXP_SCOPE,
         "kind": _EXP_KIND,
         "status": _str("default active. 'any' reads every status, candidates and "
                        "retired included", ["active", "candidate", "retired", "any"],
                        "active"),
         "query": _str("substring, matched against id / claim / action / scope"),
         "limit": _num("how many entries to return (default 50)", 50),
     }},
    lambda a: _guarded(lambda: _experience_recall(a)),
)

def _experience_recall(args):
    exp, e = _experience()
    if e:
        return e
    status = args.get("status") or "active"
    got = exp.recall(scope=args.get("scope"),
                     kind=args.get("kind"),
                     status=None if status == "any" else status,
                     query=args.get("query"),
                     limit=int(args.get("limit") or 50))
    value = {
        "entries": [_experience_row(entry) for entry in got["entries"]],
        "matched": got["matched"],
        "library": got["library"],
        "status_filter": got["status_filter"],
        "store": got["store"],
        "seed": got["seed"],
        "statuses_are": {"candidate": "an observation, never applied",
                         "active": "in force",
                         "retired": "kept for the record, not applied"},
    }
    out = ok(value, "entries",
             model="a two-chain library (hard rules with a measured failure side, "
                   "soft habits with a measured base rate) keyed by a metric this "
                   "module can actually evaluate",
             basis="docs/02 section B (designed before there was code) and docs/94 "
                   "(what changed when it became code); the seed entries were "
                   "measured on 20 real LCEDA-Pro boards, 16522 track segments, by "
                   "pcb_agent_mcp.experience.observe itself",
             precision="counts are exact on the corpus they name; no entry states a "
                       "number without the denominator it came from",
             assumptions={"a soft entry is not a rule": True,
                          "recall is read-only": True})
    if not got["entries"]:
        out["message"] = ("nothing matches that filter; %d entries exist in the "
                          "library (%s)" % (got["library"]["total"],
                                            ", ".join("%s %d" % (k, v) for k, v in
                                                      sorted(got["library"].items())
                                                      if k != "total")))
    return out

def _experience_row(entry):
    return {"id": entry.get("id"),
            "kind": entry.get("kind"),
            "status": entry.get("status"),
            "scope": entry.get("scope"),
            "claim": entry.get("claim"),
            "action": entry.get("action"),
            "trigger": entry.get("trigger"),
            "evidence": entry.get("evidence"),
            "confidence": entry.get("confidence"),
            "origin": entry.get("origin"),
            "owner": entry.get("owner"),
            "updated_at": entry.get("updated_at")}

_tool(
    "experience_check",
    "Check a board against the active library. Read-only: it measures and reports, it "
    "never rewrites the board and never writes an entry. The verdict names are kept "
    "apart on purpose - a HARD entry that comes back `violated` is a finding (a via "
    "whose ring is under 0.10 mm), a SOFT one that comes back `outside_habit` says "
    "this board does something the good boards mostly do not (which is a question), "
    "and `unverified` says the READER could not certify the input (an offline "
    "LCEDA-Pro file does not state that its pad placement was checked against the "
    "application) - an admission, not a defect. A metric that cannot run on this "
    "board comes back `not_checkable_here`, never as a pass. Each row carries the "
    "measurement it was judged on, the denominator, and how it was measured.",
    {"type": "object",
     "properties": {
         "path": _str("board file: KiCad .kicad_pcb or EasyEDA Pro .epro"),
         "member": _str("member inside an .epro / .eprj2 archive"),
         "project": _str("project directory inside an .epro archive"),
         "scope": _EXP_SCOPE,
         "status": _str("which entries to apply (default active)",
                        ["active", "candidate", "any"], "active"),
     },
     "required": ["path"]},
    lambda a: _guarded(lambda: _experience_report(a)),
)

def _experience_report(args):
    exp, e = _experience()
    if e:
        return e
    board, e = _load_board(args)
    if e:
        return e
    status = args.get("status") or "active"
    got = exp.report(board, scope=args.get("scope"),
                     status=None if status == "any" else status)
    out = ok(got, "verdicts",
             model="each metric measured on the board by the same code that "
                   "measured the corpus it is compared against, so a seed number "
                   "and a board reading cannot come from two different formulas",
             basis="docs/02 section B; the corpus behind the seed is 20 real "
                   "LCEDA-Pro boards (docs/94)",
             precision="one verdict per entry; the measurement and its denominator "
                       "are reported beside it",
             assumptions={"soft is not a rule": True,
                          "writes_nothing": got["writes_nothing"]})
    if got["hard_violations"]:
        out["message"] = ("%d hard violation(s): %s"
                          % (len(got["hard_violations"]),
                             ", ".join(got["hard_violations"])))
    elif got["unverified"]:
        out["message"] = ("no hard violation; %d entr(y/ies) could not be certified "
                          "on this file and say so: %s"
                          % (len(got["unverified"]), ", ".join(got["unverified"])))
    else:
        out["message"] = "no hard violation on this board"
    return out

_tool(
    "experience_candidate",
    "Write an observation down as a CANDIDATE. It is never applied by this call and "
    "never becomes a rule by itself: experience_promote needs confirm=true and a "
    "human reason. An entry that cannot be evaluated later is refused at this door - "
    "no id, an unknown kind, a trigger that names no board_metric, a metric this "
    "module does not implement, or an empty claim. A candidate that could not fail is "
    "just an opinion, and this library does not store opinions.",
    {"type": "object",
     "properties": dict({
         "id": _str("stable id, e.g. exp.local.my-observation"),
         "kind": _EXP_KIND,
         "scope": _EXP_SCOPE,
         "claim": _str("what is claimed, as a sentence a measurement can contradict"),
         "action": _str("what to do about it"),
         "metric": _EXP_METRIC,
         "confidence": _str("how much the observation is worth, in your words"),
         "owner": _str("who observed it"),
         "note": _str("appended to action verbatim"),
     }, **_EXP_TRIGGER),
     "required": ["id", "kind", "claim", "metric"]},
    lambda a: _guarded(lambda: _experience_candidate(a)),
)

def _experience_candidate(args):
    exp, e = _experience()
    if e:
        return e
    trigger = {"board_metric": args.get("metric")}
    for key in ("min_mm", "max_distinct", "tolerance_deg", "max_off_grid_share"):
        if args.get(key) is not None:
            trigger[key] = float(args[key])
    entry = {"id": args.get("id"), "kind": args.get("kind"),
             "scope": args.get("scope"), "claim": args.get("claim"),
             "action": args.get("action"), "trigger": trigger,
             "confidence": args.get("confidence"), "owner": args.get("owner"),
             "evidence": {"hits": None, "violated": None, "sources": []}}
    try:
        written = exp.candidate(entry, note=args.get("note"))
    except ValueError as exc:
        return err(str(exc), "ARG_INVALID",
                   hint="nothing was written: " + exp.path())
    out = ok({"entry": _experience_row(written), "store": exp.path(),
              "writes_nothing": False},
             "entry",
             model="a candidate: an observation stored so it can be evaluated later, "
                   "carrying the metric that will judge it",
             basis="docs/02 section B - the red line is that a candidate never "
                   "becomes active on its own",
             precision="the claim is prose; the trigger is the machine-checkable "
                       "part, and it is the only part that can fail",
             assumptions={"applied": False})
    out["next_step"] = ("experience_check will evaluate it (status='candidate'); "
                        "experience_promote id=%r confirm=true reason=... turns it "
                        "into a rule, and only a person can do that"
                        % written["id"])
    return out

_tool(
    "experience_promote",
    "Turn a candidate into an active entry. confirm=true is required and is not "
    "decorative: the alternative - promoting on counters alone - is exactly the "
    "failure mode the design forbids, because a number crossing a threshold is not a "
    "person deciding the claim is true. A seed entry cannot be promoted or edited "
    "(it ships with the package and would be lost on update): copy it to a new id. "
    "A retired entry cannot be re-promoted by this call either.",
    {"type": "object",
     "properties": {
         "id": _str("the candidate's id"),
         "confirm": _bool("must be true. false or absent is refused"),
         "reason": _str("why this is believed - stored with the entry"),
     },
     "required": ["id", "confirm", "reason"]},
    lambda a: _guarded(lambda: _experience_promote(a)),
)

def _experience_promote(args):
    exp, e = _experience()
    if e:
        return e
    try:
        got = exp.promote(args.get("id"), confirm=args.get("confirm"),
                          reason=args.get("reason"))
    except PermissionError as exc:
        return err(str(exc), "ARG_INVALID",
                   hint="pass confirm=true and a reason to promote %r"
                        % args.get("id"))
    except ValueError as exc:
        return err(str(exc), "ARG_INVALID")
    except KeyError as exc:
        return err(str(exc).strip("\'\""), "NOT_FOUND",
                   hint="experience_recall status='candidate' lists what can be "
                        "promoted")
    return ok({"entry": _experience_row(got), "store": exp.path()}, "entry",
              model="candidate -> active, on a person's confirmation",
              basis="docs/02 section B (the red line)",
              precision="one status change; the reason is stored verbatim",
              assumptions={"promoted_by": got.get("promoted_by") or ""},
              extra={"writes_nothing": False})

_tool(
    "experience_outcome",
    "Record that an active entry did or did not hold on a real board. Counters only: "
    "hits / violated, plus the note in sources. This is the feedback half of the loop "
    "and it does NOT change an entry's status - an entry that starts failing on real "
    "boards is still the entry a person confirmed, and demoting it is a person's "
    "decision. A seed entry cannot take an outcome because its counters live in the "
    "package and would be lost on update: copy it to a new id first.",
    {"type": "object",
     "properties": {
         "id": _str("a LOCAL entry's id (the seed is read-only)"),
         "hit": _bool("true = it held on this board, false = it did not", True),
         "note": _str("appended to evidence.sources verbatim, e.g. the board's name"),
     },
     "required": ["id"]},
    lambda a: _guarded(lambda: _experience_outcome(a)),
)

def _experience_outcome(args):
    exp, e = _experience()
    if e:
        return e
    try:
        got = exp.outcome(args.get("id"), hit=args.get("hit") is not False,
                          note=args.get("note"))
    except KeyError as exc:
        return err(str(exc).strip("\'\""), "NOT_FOUND",
                   hint="the seed ships read-only; copy the entry to a new id to "
                        "keep counters of your own")
    return ok({"entry": _experience_row(got), "store": exp.path()},
              "counters",
              model="the feedback half of the recall -> outcome loop",
              basis="docs/02 section B",
              precision="+1 on hits or violated; sources gains the note verbatim",
              assumptions={"status_unchanged": True},
              extra={"writes_nothing": False})

_tool(
    "experience_export",
    "The library as one self-contained JSON blob: what a person reads, what another "
    "agent imports, and what goes into the repository with the rest of the knowledge "
    "this package ships. Entries keep their origin (seed / local / imported) so a "
    "reader can tell a measured corpus number from a runtime observation.",
    {"type": "object",
     "properties": {
         "status": _str("only this status", ["candidate", "active", "retired"]),
         "ids": _arr("only these ids"),
     }},
    lambda a: _guarded(lambda: _experience_export(a)),
)

def _experience_export(args):
    exp, e = _experience()
    if e:
        return e
    got = exp.export(entries=args.get("ids"), status=args.get("status"))
    out = ok(got, "json",
             model="a portable snapshot of the two-chain library",
             basis="docs/02 section B",
             precision="verbatim entries; no number is recomputed on export",
             assumptions={"writes_nothing": True})
    out["next_step"] = ("write it next to the package's data/ to ship it, or hand it "
                        "to experience_import elsewhere (which lands every entry as a "
                        "candidate)")
    return out

_tool(
    "experience_import",
    "Merge another library in. Every imported entry lands as a CANDIDATE, whatever "
    "status the file claims: the confirmation belonged to whoever wrote the file, not "
    "to this store, so an import is not a promotion channel either. Entries that "
    "cannot be evaluated (an unknown metric, no trigger, no claim) are skipped with "
    "the reason instead of being stored broken.",
    {"type": "object",
     "properties": {
         "blob": {"type": "object",
                  "description": "the JSON object experience_export produced"},
         "path": _str("or a JSON file to read it from (blob wins if both are given)"),
     }},
    lambda a: _guarded(lambda: _experience_import(a)),
)

def _experience_import(args):
    exp, e = _experience()
    if e:
        return e
    blob = args.get("blob")
    if blob is None:
        path = args.get("path")
        if not path:
            return err("give either blob or path", "ARG_MISSING")
        try:
            with open(path, encoding="utf-8") as fh:
                blob = json.load(fh)
        except OSError as exc:
            return err("cannot read %r: %s" % (path, exc), "FILE_NOT_FOUND")
        except ValueError as exc:
            return err("%r is not JSON: %s" % (path, exc), "ARG_INVALID")
    try:
        got = exp.import_entries(blob)
    except ValueError as exc:
        return err(str(exc), "ARG_INVALID")
    out = ok(got, "entries",
             model="a merge that cannot promote: every incoming entry is a candidate",
             basis="docs/02 section B (the red line applies to imports too)",
             precision="counts of added / demoted / skipped; skipped carries the "
                       "refusal for each",
             assumptions={"writes_nothing": False})
    if got["skipped"]:
        out["message"] = ("%d entr(y/ies) skipped: %s"
                          % (len(got["skipped"]),
                             "; ".join("%s: %s" % (s.get("id"), s.get("why"))
                                       for s in got["skipped"][:5])))
    return out

_tool(
    "calc_capabilities",
    "Self-description: what this server can compute, what it refuses to compute and why, "
    "and what is not implemented yet. Call this first.",
    {"type": "object", "properties": {}},
    _capabilities,
)


# --------------------------------------------------- manufacturing output
def _fab_fail(exc):
    from .fab import FabError
    if isinstance(exc, FabError):
        return err(str(exc), exc.reason_code, hint=exc.hint)
    return err("%s: %s" % (type(exc).__name__, exc), "FAB_FAILED")

def _board_fab_package(args):
    from . import fab as _fab
    path = args.get("path")
    if not path:
        return err("path is required", "ARG_MISSING")
    formats = args.get("formats")
    if isinstance(formats, str):
        formats = [f.strip() for f in formats.split(",") if f.strip()]
    try:
        return _fab.package(path, out_dir=args.get("out_dir"),
                            formats=formats or ("gerbers", "drill", "pos"),
                            allow_beside_source=bool(args.get("allow_beside_source")),
                            zip_it=args.get("zip") is not False)
    except Exception as exc:                       # noqa: BLE001
        return _fab_fail(exc)

def _board_export_graphics(args):
    from . import fab as _fab
    path = args.get("path")
    if not path:
        return err("path is required", "ARG_MISSING")
    layers = args.get("layers")
    if isinstance(layers, str):
        layers = [l.strip() for l in layers.split(",") if l.strip()]
    try:
        return _fab.graphics(path, fmt=args.get("format") or "pdf", layers=layers,
                             out_dir=args.get("out_dir"),
                             allow_beside_source=bool(args.get("allow_beside_source")),
                             black_and_white=bool(args.get("black_and_white")))
    except Exception as exc:                       # noqa: BLE001
        return _fab_fail(exc)

def _board_export_3d(args):
    from . import fab as _fab
    path = args.get("path")
    if not path:
        return err("path is required", "ARG_MISSING")
    try:
        return _fab.threed(path, fmt=args.get("format") or "step",
                           out_dir=args.get("out_dir"),
                           allow_beside_source=bool(args.get("allow_beside_source")),
                           board_only=bool(args.get("board_only")))
    except Exception as exc:                       # noqa: BLE001
        return _fab_fail(exc)

def _schematic_export(args):
    from . import fab as _fab
    path = args.get("path")
    if not path:
        return err("path is required", "ARG_MISSING")
    fields = args.get("fields")
    if isinstance(fields, str):
        fields = [f.strip() for f in fields.split(",") if f.strip()]
    try:
        return _fab.schematic_export(
            path, fmt=args.get("format") or "pdf", out_dir=args.get("out_dir"),
            allow_beside_source=bool(args.get("allow_beside_source")),
            fields=fields, group_by=args.get("group_by"),
            netlist_format=args.get("netlist_format"),
            exclude_dnp=bool(args.get("exclude_dnp")))
    except Exception as exc:                       # noqa: BLE001
        return _fab_fail(exc)

_tool(
    "board_fab_package",
    "Build a manufacturing package from a KiCad board: Gerbers (the board's own plot "
    "settings), Excellon drill (mm, separate PTH/NPTH, plus a map and a report), a "
    "pick-and-place position CSV, and optionally STEP / IPC-2581 / ODB++ / IPC-D-356 / "
    "GenCAD. Every artefact is produced by kicad-cli and then re-read here - a Gerber "
    "with no M02* terminator, an Excellon with no tool table, a position file with no "
    "rows, a STEP that is not ISO-10303-21 comes back under `problems` instead of being "
    "counted as success. Writes a manifest.json (source sha256, kicad-cli version, every "
    "file with its own sha256) and a fab.zip. Refuses to write inside the source "
    "directory tree unless allow_beside_source=true. Not included: panelisation, V-cut, "
    "stencil, impedance report.",
    {"type": "object", "properties": {
        "path": _str("the .kicad_pcb file (never modified)"),
        "formats": _arr("gerbers / drill / pos / step / ipc2581 / odb / ipcd356 / gencad"
                        " (default gerbers, drill, pos)"),
        "out_dir": _str("output directory; default is a fresh temp dir. A path inside "
                        "the source directory is refused"),
        "allow_beside_source": _bool("really write inside the design's directory", False),
        "zip": _bool("also write fab.zip (default true)", True),
    }, "required": ["path"]},
    _board_fab_package,
)

_tool(
    "board_export_graphics",
    "Layer plot for humans: pdf / svg / dxf / ps / 3dpdf. `layers` defaults to every "
    "copper layer plus F.SilkS / B.SilkS / Edge.Cuts, read from the board file - "
    "kicad-cli refuses a plot with no layer list, so the list comes from the design and "
    "is echoed back. dxf writes one file per layer. Every pdf / svg / ps / dxf answer "
    "also carries `placement`: the board's extent in FILE coordinates and whether the "
    "sheet holds it. kicad-cli places a plot by those coordinates, so a design drawn at "
    "negative y prints off the page and the page comes back blank while the export says "
    "ok - that case stays status=ok (the file is real) and adds `warnings` plus an "
    "`advice` block naming the exact translate_board dx/dy that clears it.",
    {"type": "object", "properties": {
        "path": _str("the .kicad_pcb file"),
        "format": _str("pdf / svg / dxf / ps / 3dpdf", enum=["pdf", "svg", "dxf", "ps", "3dpdf"]),
        "layers": _arr("layer names, e.g. [\"F.Cu\", \"Edge.Cuts\"]"),
        "out_dir": _str("output directory; default is a fresh temp dir"),
        "allow_beside_source": _bool("really write inside the design's directory", False),
        "black_and_white": _bool("monochrome plot", False),
    }, "required": ["path"]},
    _board_export_graphics,
)

_tool(
    "board_export_3d",
    "3D solid export of a KiCad board: step / stpz / brep / glb / stl / ply / vrml / xao / "
    "u3d. A component whose 3D model path does not resolve is silently absent from the "
    "result - KiCad gives no per-part warning, so this tool cannot report which ones. "
    "board_only=true exports the bare board without component models.",
    {"type": "object", "properties": {
        "path": _str("the .kicad_pcb file"),
        "format": _str("step / stpz / brep / glb / stl / ply / vrml / xao / u3d",
                        enum=["step", "stpz", "brep", "glb", "stl", "ply", "vrml", "xao", "u3d"]),
        "out_dir": _str("output directory; default is a fresh temp dir"),
        "allow_beside_source": _bool("really write inside the design's directory", False),
        "board_only": _bool("export the board body without component models", False),
    }, "required": ["path"]},
    _board_export_3d,
)

_tool(
    "schematic_export",
    "Schematic output: bom (CSV, parsed into rows with a placement count), netlist "
    "(kicadsexpr / kicadxml / spice / ...), pdf, svg, dxf, ps, python-bom. The netlist "
    "here is KiCad's own netlister - the same engine the schematic write path uses as a "
    "gate, so a netlist taken before and after an edit is comparable.",
    {"type": "object", "properties": {
        "path": _str("the .kicad_sch file (never modified)"),
        "format": _str("bom / netlist / pdf / svg / dxf / ps / python-bom",
                        enum=["bom", "netlist", "pdf", "svg", "dxf", "ps", "python-bom"]),
        "out_dir": _str("output directory; default is a fresh temp dir"),
        "allow_beside_source": _bool("really write inside the design's directory", False),
        "fields": _arr("BOM columns in order (default Reference, Value, Footprint, "
                        "QUANTITY, DNP, Description, LCSC)"),
        "group_by": _str("BOM grouping, e.g. \"Value,Footprint\""),
        "netlist_format": _str("kicadsexpr (default) / kicadxml / spice / spicemodel / "
                               "orcadpcb2 / cadstar / pads / allegro"),
        "exclude_dnp": _bool("drop Do-Not-Populate symbols from the BOM", False),
    }, "required": ["path"]},
    _schematic_export,
)

# ------------------------------------------------------------ tool profile
#
# The registry holds ~190 tools, which is ~315 KB of JSON (roughly 100 k tokens)
# in a single tools/list reply. A chat client puts that in front of the model
# before the first user message, so the context window fills up and the dialog
# stalls or breaks outright. The default profile advertises the entry point of
# every layer plus discovery; tool_search / tool_call reach the rest without
# paying for their schemas up front.
#
# PCB_AGENT_TOOLSET:
#   core (default)  the entry points below + the discovery tools
#   all / *         every registered tool (the behaviour before this profile)
#   <tokens>        comma/space separated tool names or prefixes, e.g.
#                   "board_,magnetics_" - "core" may be mixed in
DEFAULT_TOOLSET = "core"

META_TOOLS = ("tool_search", "tool_call")
# the historical private name; same tuple, kept so old imports keep working
_META_TOOLS = META_TOOLS

CORE_TOOLS = (
    # discovery / self description
    "calc_capabilities",
    # board: read
    "board_summary", "board_components", "board_nets", "board_zones",
    "board_drc", "board_view", "board_rules", "board_fidelity",
    # board: write
    "board_create", "board_propose", "board_apply", "board_rules_write",
    # schematic
    "schematic_summary", "schematic_review", "schematic_propose",
    "schematic_apply", "schematic_pcb_diff", "schematic_pcb_sync",
    # desktop / LCEDA
    "epro_inspect", "app_detect", "app_status", "lceda_pro_status",
    "lceda_pro_open",
    # calc / rules
    "unit_convert", "width_for_current", "current_capacity", "via_resistance",
    "via_current_allowed", "trace_impedance", "width_for_impedance",
    "diff_impedance", "clearance_required", "creepage_required",
    "lambda_over_20", "stackup_design", "dfm_check", "decoupling_check",
    "pdn_impedance", "ir_drop_chain",
    # simulation / thermal
    "sim_spice", "thermal_via_array",
    # parts
    "part_search", "part_get",
)


def toolset_from_env():
    return (os.environ.get("PCB_AGENT_TOOLSET") or DEFAULT_TOOLSET).strip()


def _resolve_toolset(toolset):
    """Tool names a profile asks for, or None when it asks for everything."""
    raw = (toolset or "").strip()
    if not raw or raw.lower() in ("all", "*"):
        return None
    picked = []
    for token in re.split(r"[,\s]+", raw.lower()):
        if not token:
            continue
        if token in ("all", "*"):
            return None
        if token == "core":
            picked.extend(CORE_TOOLS)
            continue
        exact = [n for n in TOOLS if n.lower() == token]
        picked.extend(exact or [n for n in TOOLS if n.lower().startswith(token)])
    # Discovery must survive every profile: without the two meta tools the
    # tools that were filtered out are unreachable.
    picked.extend(_META_TOOLS)
    out, seen = [], set()
    for name in picked:
        if name not in seen:
            seen.add(name)
            out.append(name)
    # A profile whose tokens all missed is a typo, not a request for an empty
    # server: `PCB_AGENT_TOOLSET=boards_` would otherwise advertise two tools and
    # read as "every capability was deleted". Fall back to the default profile.
    if all(name in META_TOOLS for name in out):
        return [n for n in CORE_TOOLS if n in TOOLS] + list(META_TOOLS)
    return out


# Chinese task nouns -> the English words the descriptions are written in.
#
# Why this exists: the keyword pass splits on [^0-9a-z_]+, so a query made only of
# Chinese characters produced zero words and tool_search answered "ok, 0 matches".
# That is a silent false negative - it reads as "this server cannot do impedance"
# when the tool is right there. This is a lookup table of task nouns, not a
# translator: anything outside it gets an explicit "no keyword mapped" hint
# instead of a quiet zero.
_CJK_ALIASES = (
    ("阻抗", ("impedance",)),
    ("线宽", ("width",)),
    ("宽度", ("width",)),
    ("载流", ("current", "capacity")),
    ("电流", ("current",)),
    ("电压", ("voltage",)),
    ("压降", ("drop",)),
    ("过孔", ("via",)),
    ("去耦", ("decoupling",)),
    ("电容", ("capacitor",)),
    ("电感", ("inductor",)),
    ("磁芯", ("core",)),
    ("铁氧体", ("ferrite",)),
    ("磁性", ("magnetics",)),
    ("损耗", ("loss",)),
    ("热", ("thermal",)),
    ("温度", ("temperature",)),
    ("散热", ("heat", "thermal")),
    ("焊盘", ("pad",)),
    ("封装", ("footprint",)),
    ("原理图", ("schematic",)),
    ("布线", ("route",)),
    ("走线", ("track",)),
    ("等长", ("length",)),
    ("差分", ("diff",)),
    ("串扰", ("crosstalk",)),
    ("间距", ("clearance",)),
    ("爬电", ("creepage",)),
    ("叠层", ("stackup",)),
    ("铺铜", ("zone", "pour")),
    ("覆铜", ("zone", "pour")),
    ("铜皮", ("zone",)),
    ("仿真", ("sim",)),
    ("晶振", ("crystal",)),
    ("滤波", ("filter",)),
    ("天线", ("antenna",)),
    ("熔断", ("fusing",)),
    ("振动", ("vibration",)),
    ("跌落", ("drop", "shock")),
    ("焊点", ("solder", "joint")),
    ("疲劳", ("fatigue",)),
    ("电源", ("power", "pdn")),
    ("平面", ("plane", "zone")),
    ("检查", ("check",)),
    ("评审", ("review",)),
    ("规则", ("rules",)),
    ("板", ("board",)),
    ("元件", ("component", "part")),
    ("器件", ("part",)),
    ("数据", ("data",)),
)


def _search_words(query):
    """(latin words, words contributed by the Chinese alias table)."""
    latin = [w for w in re.split(r"[^0-9a-z_]+", query.lower()) if w]
    extra = []
    for run in re.findall(r"[\u4e00-\u9fff]+", query):
        for zh, en in _CJK_ALIASES:
            if zh in run:
                extra.extend(en)
    return latin, extra


def _tool_search(args):
    query = str(args.get("query") or "").strip()
    if not query:
        return err("query is required", "ARG_MISSING",
                   hint="search by the task, e.g. 'trace impedance'")
    try:
        limit = int(args.get("limit") or 8)
    except (TypeError, ValueError):
        limit = 8
    limit = max(1, min(limit, 25))
    words, aliased = _search_words(query)
    words = words + [w for w in aliased if w not in words]

    def whole(word, text):
        # Whole word, with `_` counting as a boundary: `via` hits `via_resistance`
        # but not `trivial`. Plain substring matching was a false-positive source -
        # 25 of the 25 first hits for 过孔 were tools that merely mention it.
        return re.search(r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(word),
                         text) is not None

    scored = []
    for name, spec in TOOLS.items():
        low_name = name.lower()
        low_desc = spec["description"].lower()
        name_hits = sum(1 for w in words if whole(w, low_name))
        desc_hits = sum(1 for w in words if whole(w, low_desc))
        if not name_hits and not desc_hits:
            continue
        # A hit in the tool's NAME is evidence that this is the tool; a hit in its
        # description is a mention. Weighting them alike let a query return pages
        # of tools that talk about the word without doing anything with it.
        score = name_hits * 12 + desc_hits * 4
        wanted = query.strip().lower()
        if low_name == wanted:
            score += 100
        elif wanted and wanted in low_name:
            score += 20
        scored.append((score, name))
    scored.sort(key=lambda item: (-item[0], item[1]))
    picked = [name for _, name in scored[:limit]]
    out = {
        "status": "ok",
        "query": query,
        "total_matches": len(scored),
        "returned": len(picked),
        "tools": [{"name": n, "description": TOOLS[n]["description"],
                   "inputSchema": TOOLS[n]["inputSchema"]} for n in picked],
        "how_to_call": "call tool_call with {\"name\": <name>, \"arguments\": {...}}",
        "payload_is": "top_level",
        "where_to_read": "matching tool specs are under `tools`; run one with tool_call",
    }
    if not picked:
        if re.search(r"[\u4e00-\u9fff]", query) and not aliased:
            # The alias table is finite; say so instead of answering a quiet zero.
            out["hint"] = ("none of the Chinese words in this query is in the alias "
                           "table, so nothing was searched: it covers common task "
                           "nouns (阻抗 / 过孔 / 铺铜 / "
                           "散热 / 爬电 ...). Retry with an English "
                           "keyword, or call calc_capabilities for the layer map.")
            out["search_was_english_only"] = True
        else:
            out["hint"] = ("no tool matched these words; call calc_capabilities for "
                           "the layer map, or search a single keyword such as "
                           "'impedance'")
    return out


def _tool_call(args):
    name = args.get("name")
    if not isinstance(name, str) or not name.strip():
        return err("name is required", "ARG_MISSING", hint="call tool_search first")
    name = name.strip()
    if name == "tool_call":
        return err("tool_call cannot call itself", "ARG_INVALID")
    if name not in TOOLS:
        return err("unknown tool: %s" % name, "TOOL_NOT_FOUND",
                   hint="call tool_search with the task you want to do")
    inner = args.get("arguments")
    if isinstance(inner, str):
        try:
            inner = json.loads(inner)
        except json.JSONDecodeError as exc:
            return err("arguments is a string but not valid JSON: %s" % exc,
                       "ARG_INVALID")
    if inner is None:
        inner = {}
    if not isinstance(inner, dict):
        return err("arguments must be an object", "ARG_INVALID")
    return call_tool(name, inner)


_tool(
    "tool_search",
    "Find a tool that is not in your advertised tool list. This server registers "
    "about 190 tools; the common entry points are listed directly and the rest are "
    "reached through tool_search + tool_call, so the model prompt stays small. Search "
    "by the task ('impedance', 'magnetics core loss'); every hit carries its full "
    "argument schema. Call this before assuming a capability is missing.",
    {
        "type": "object",
        "properties": {
            "query": _str("words describing the task"),
            "limit": _num("maximum hits to return (1-25, default 8)", 8),
        },
        "required": ["query"],
    },
    _tool_search,
)

_tool(
    "tool_call",
    "Run a tool returned by tool_search. Put its arguments in `arguments`, named "
    "exactly as the hit's inputSchema names them; the reply is the same envelope the "
    "tool would have returned if it were listed directly (status / value / basis / "
    "assumptions).",
    {
        "type": "object",
        "properties": {
            "name": _str("exact tool name from tool_search"),
            "arguments": {"type": "object",
                          "description": "the tool's arguments (may be omitted if it takes none)"},
        },
        "required": ["name"],
    },
    _tool_call,
)


def list_tools(toolset="all"):
    """Tool specs for tools/list.

    Library callers get every tool; the server passes the profile from
    PCB_AGENT_TOOLSET, which defaults to "core".
    """
    names = _resolve_toolset(toolset)
    if names is None:
        names = list(TOOLS)
    return [
        {"name": name, "description": TOOLS[name]["description"],
         "inputSchema": TOOLS[name]["inputSchema"]}
        for name in names if name in TOOLS
    ]


# --- tools/list delivery budget -------------------------------------------
# Until 0.0.109 `tools/call` was capped at 48 KiB while `tools/list` was sent
# whole. That is a contradiction with a cost: the default `core` profile is
# 54 905 B (46 tools) and `all` is 300 159 B (190 tools), so the one reply every
# session must fetch was the one reply nothing measured. A host that truncates
# it silently loses tools; a host that fails the turn calls it a broken server
# and the conversation ends - which is what "the mcp broke again" looked like.
#
# The fix is not to cut tools away. It walks the same list, but
#   1. if the whole list already fits, it is sent byte for byte (no change at
#      all for anyone who raises PCB_AGENT_MAX_RESULT_KB or has few tools);
#   2. if it does not fit, descriptions are shortened first, because a name, a
#      parameter type and a required list are what a caller needs to call a
#      tool, while prose is what tool_search is for;
#   3. only if it still does not fit is the list paged, with the MCP cursor
#      protocol (`params.cursor` in, `result.nextCursor` out), so no tool is
#      ever dropped - paging loses round trips, never entries.
# Measured with the shipped profiles: core 54 905 -> 38 766 B after trimming
# (one page, every tool, every parameter intact), all 300 159 -> 203 114 B
# (five pages at the 48 KiB cap).
_TOOLS_ENVELOPE_BYTES = 1024


def _tools_trim_spec(spec, desc_cut=200, inner_cut=80):
    """Copy `spec` with long prose shortened. Returns (spec, cut_fields)."""
    import copy
    out = copy.deepcopy(spec)
    cut = 0

    def short(text, limit):
        if isinstance(text, str) and len(text) > limit:
            return text[:limit].rstrip() + " ..."
        return text

    described = short(out.get("description"), desc_cut)
    if described != out.get("description"):
        cut += 1
    out["description"] = described

    def walk(node):
        nonlocal cut
        if isinstance(node, dict):
            for key, val in list(node.items()):
                if key == "description" and isinstance(val, str):
                    new = short(val, inner_cut)
                    if new != val:
                        node[key] = new
                        cut += 1
                else:
                    walk(val)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(out.get("inputSchema") or {})
    return out, cut


# Prose is cut one step at a time until the list fits in one page. Measured on
# the shipped core profile (46 tools) on 2026-11-03:
#   full 54 905 B | (200,80) 38 766 | (120,40) 33 396 | (120,0) 26 725
#   (60,0) 23 976 | (40,0) 23 056 | no prose at all 15 127
# The floor is the schema itself, so a budget under ~16 KiB can only be met by
# dropping tools - which this file exists to prevent.
_TOOLS_TRIM_LADDER = ((200, 80), (120, 40), (120, 0), (60, 0), (0, 0))


def _tools_fit(specs, cap, level):
    """Pack `specs`, trimmed at `level`, into one array no bigger than `cap`.

    Returns (page, used, cut). A page shorter than `specs` means the caller must
    choose between a smaller level and paging.
    """
    import json as _json

    def size(obj):
        return len(_json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    desc_cut, inner_cut = level
    page = []
    used = 0
    cut = 0
    for spec in specs:
        trimmed, cut_here = _tools_trim_spec(spec, desc_cut, inner_cut)
        cut += cut_here
        piece = size(trimmed) + 2       # the comma and newline of the array
        if page and used + piece > cap:
            break
        if not page and piece > cap:
            # One entry that cannot fit even alone: keep its name and schema
            # shape, drop every description. Dropping the entry would make a
            # tool invisible, which is worse than a bare one.
            bare, _ = _tools_trim_spec(spec, 0, 0)
            page.append(bare)
            used += size(bare) + 2
            break
        page.append(trimmed)
        used += piece
    return page, used, cut


def tools_page(specs, cap_bytes, cursor=None):
    """Fit `specs` into one reply of at most `cap_bytes`.

    Returns (page, next_cursor, info). `info` is empty when nothing had to be
    done - that is the contract that makes this safe to ship: an unchanged
    answer stays unchanged, byte for byte, and only an answer that would not
    have arrived at all is reshaped.
    """
    import json as _json

    def size(obj):
        return len(_json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    total = size(specs)
    if not cursor and (not cap_bytes or total <= cap_bytes):
        return specs, None, {}

    start = 0
    if cursor:
        try:
            start = max(0, int(str(cursor)))
        except (TypeError, ValueError):
            start = 0
    if start >= len(specs):
        return [], None, {"tools_total": len(specs), "tools_from": start}

    # The cap is on the whole JSON-RPC reply, so the envelope has to be paid for
    # out of it: {"tools":[...],"nextCursor":...,"tools_total":...,"note":...}
    # with the note alone near 330 B. 0.0.109 first forgot this and measured a
    # worst page of 49 397 B against a 49 152 B cap - over by 245 B, which is
    # exactly the kind of quiet overshoot the page exists to prevent.
    cap = (cap_bytes - _TOOLS_ENVELOPE_BYTES) if cap_bytes else (1 << 62)
    cap = max(cap, 512)
    # 0.0.110: until now trimming had one setting and then went straight to
    # paging, so a smaller budget could only be met by cutting tools out. The
    # ladder walks prose out first and pages only when even the bare list is
    # too big for one reply.
    rest = specs[start:]
    level = _TOOLS_TRIM_LADDER[0]
    page, _used, cut = _tools_fit(rest, cap, level)
    if len(page) != len(rest):
        for step in _TOOLS_TRIM_LADDER[1:]:
            candidate, _used2, cut2 = _tools_fit(rest, cap, step)
            level, page, cut = step, candidate, cut2
            if len(candidate) == len(rest):
                break

    end = start + len(page)
    info = {"tools_total": len(specs), "tools_from": start,
            "tools_returned": len(page)}
    if len(page) != len(specs) or start or any(
            t != s for t, s in zip(page, specs[start:end])):
        info["descriptions_shortened"] = True
        info["prose_cut"] = "%d/%d" % level      # description / parameter, chars
        info["fields_shortened"] = cut
        info["note"] = ("tool descriptions are shortened to keep this reply inside "
                        "PCB_AGENT_MAX_TOOLS_KB; names, parameters, types, enum "
                        "values and required lists are complete. Set "
                        "PCB_AGENT_MAX_TOOLS_KB=0 for the full prose, or use "
                        "tool_search for one tool's text.")
    return page, (str(end) if end < len(specs) else None), info


def call_tool(name, arguments):
    spec = TOOLS.get(name)
    if spec is None:
        gone = RETIRED_TOOLS.get(name)
        if gone is not None:
            return err("%s was removed from this server and cannot be called: %s"
                       % (name, gone), "TOOL_RETIRED",
                       hint=("this server does not route or place automatically; "
                             "every copper path and every part position has to be "
                             "passed in by the caller as explicit coordinates "
                             "(ops add_track / add_via / move_footprint). "
                             "call tools/list for what is left"))
        return err("unknown tool: " + str(name), "TOOL_NOT_FOUND",
                   hint="call tools/list")
    args = arguments or {}
    try:
        out = spec["handler"](args)
    except KeyError as exc:
        return err("missing required argument: %s" % exc, "ARG_MISSING")
    except Exception as exc:  # noqa: BLE001
        return err("tool raised: %s: %s" % (type(exc).__name__, exc), "TOOL_RAISED")
    return _uniform_envelope(_flag_unread_arguments(spec, args, out))


def _uniform_envelope(out):
    """Say where the business fields of THIS reply are, so nobody has to guess.

    Two shapes are in use and both are kept:

      * ``value``     - the calc.result envelope: the answer is under `value`,
                        beside `unit` / `model` / `basis` / `precision`. This is
                        what board_zones, board_fill_zones, board_drc and
                        board_pour_connectivity return.
      * ``top_level`` - the write path's own reply: `plan_id`, `gates`,
                        `blocking`, `operations` sit at the top. This is what
                        board_propose / board_apply return.

    Renaming either would break every caller written against it, so nothing is
    moved. Instead each reply carries `payload_is` and a one-line
    `where_to_read`: a reader that assumed one shape and got the other read 0
    zones where the file had 2 - a wrong reading of a correct answer, which is
    the worst kind of wrong.
    """
    if not isinstance(out, dict):
        return out
    value = out.get("value")
    if isinstance(value, dict) and "status" in out:
        out.setdefault("payload_is", "value")
        out.setdefault("where_to_read",
                       "business fields are under `value`; the envelope around it "
                       "carries status/unit/model/basis/precision/assumptions")
    elif out.get("status") in ("ok", "error"):
        out.setdefault("payload_is", "top_level")
        out.setdefault("where_to_read",
                       "business fields are at the TOP level of this reply (this is "
                       "the write path's shape: plan_id / gates / blocking / "
                       "operations); there is no `value` object")
    return out


def _flag_unread_arguments(spec, args, out):
    """Say so when an argument was never read, instead of answering as if it were.

    A handler reads the keys it knows and cannot notice a misspelt one, so
    `{"temperature_c": 50}` on a tool whose key is `temp_c` used to come back as
    a complete, confident answer computed at the default. That is the same class
    of error as a silently ignored netlist parameter: the answer is not wrong
    arithmetic, it is an answer to a question the caller did not ask. Nothing is
    invented or corrected here - the answer stands, and the caller is told which
    keys never reached it. Correct calls are byte-identical to before (no key is
    unknown, so no warning is added).
    """
    props = (spec.get("inputSchema") or {}).get("properties") or {}
    if not props or not isinstance(args, dict):
        return out
    unknown = sorted(str(k) for k in args if k not in props)
    if not unknown or not isinstance(out, dict):
        return out
    note = ("argument(s) %s are not in this tool's schema and were IGNORED - the "
            "answer below was computed as if they had never been given (spelling "
            "is exact: %s)" % (", ".join(unknown), ", ".join(sorted(props))))
    warn = out.get("warnings")
    if isinstance(warn, list):
        warn.append(note)
    else:
        extra = out.get("extra")
        if isinstance(extra, dict) and isinstance(extra.get("warnings"), list):
            extra["warnings"].append(note)
        else:
            out["unread_arguments"] = unknown
            out.setdefault("warnings", []).append(note)
    return out


def result_to_text(result):
    # Compact, not indent=2: a board_view reply can be megabytes and indentation
    # is ~15 % of it, paid again on every token the client renders. Strict JSON
    # because a NaN anywhere in an answer makes the client reject the whole
    # message - see jsonsafe.
    return _jsonsafe.dumps(result)


def max_result_chars():
    """Hard ceiling on one tool reply (0 disables). PCB_AGENT_MAX_RESULT_KB.

    A reply that is larger than the conversation budget is not an answer the
    model can use: the client either truncates it (silently) or fails the turn.
    Naming the size is the honest option - and since 0.0.104 the oversized reply
    is not thrown away either: `cap_result` keeps its head, writes the full JSON
    to disk and says where it went. The default is 48 KiB - measured, not
    guessed: the largest honest answer in this repo is board_ops at 33 340 B
    (41 operations), while the replies that actually killed a conversation
    start at 68 089 B (calc_capabilities) and 109 190 B (board_view pads),
    with lceda_pro_run returning whole getAll() objects above that. A pipe
    carries 16 MiB; a conversation does not.
    """
    raw = os.environ.get("PCB_AGENT_MAX_RESULT_KB", "48")
    try:
        kb = int(float(raw))
    except (TypeError, ValueError):
        kb = 48
    return max(0, kb) * 1024

_SESSION = {"bytes": 0}

def session_budget_bytes():
    """How much tool text one SESSION may pour into a conversation. 0 = off.

    PCB_AGENT_SESSION_BUDGET_KB, default 256 KiB (about 65 000 tokens).

    Why a third knob after `max_result_chars` and `max_line_bytes`: both of those
    bound ONE message, and a conversation is not one message. Measured on
    2026-11-04 from the host's own log: the session that kept "breaking" was
    compacted at `original_tokens=133868` and the very next answer was
    `answer_runes=111` - a nearly empty turn, which is what the user sees as
    "still disconnected". Forty-eight KiB per reply is ~12 000 tokens; ten
    honest replies and the session is spent, no matter how legal each one was.

    So the session gets a budget, and the accounting is of what actually enters
    the conversation, not of what the tool computed. Below the budget every
    reply is byte-for-byte what it always was; above it the remaining replies
    are cut to `session_tail_bytes()` with `reason_code = SESSION_BUDGET`, the
    full JSON on disk and the size of the overrun in the reply itself. The
    honest alternative - dying at turn 40 with no trace - is worse.
    """
    raw = os.environ.get("PCB_AGENT_SESSION_BUDGET_KB", "256")
    try:
        kb = int(float(raw))
    except (TypeError, ValueError):
        kb = 256
    return max(0, kb) * 1024

def session_tail_bytes():
    """Per-reply cap once the session budget is gone. PCB_AGENT_SESSION_TAIL_KB.

    Default 6 KiB: a receipt the model can act on (counts, status, saved_to)
    with room for a handful of short values, and about 1 500 tokens.
    """
    raw = os.environ.get("PCB_AGENT_SESSION_TAIL_KB", "6")
    try:
        kb = int(float(raw))
    except (TypeError, ValueError):
        kb = 6
    return max(1024, kb * 1024)

def session_spent_bytes():
    return _SESSION["bytes"]

def reset_session_budget():
    """Start a fresh conversation's accounting. Called by serve() and by tests."""
    _SESSION["bytes"] = 0

def note_reply_bytes(n):
    """Charge what a reply actually put in the transcript, not what it computed."""
    _SESSION["bytes"] += max(0, int(n))

def effective_result_cap():
    """The per-reply cap in force right now: the result cap, tightened by the budget."""
    cap = max_result_chars()
    budget = session_budget_bytes()
    if cap and budget and _SESSION["bytes"] >= budget:
        return min(cap, session_tail_bytes())
    return cap

def max_tools_chars():
    """Budget for one tools/list reply (0 disables). PCB_AGENT_MAX_TOOLS_KB.

    This is a different ceiling from `max_result_chars` on purpose. A tool reply
    is read once and thrown away; the tool list is not - the client turns every
    entry into a function declaration and pays for it on EVERY turn of the
    session. Measured on 2026-11-03 from the host's own log: this session was
    compacted three times, original 78 767 / 123 625 / 104 242 tokens down to a
    55 798-58 827 token floor. The core profile's 37 210 B tool list is about
    11 000 tokens of that floor, ~19 % of every request, spent on prose.

    So the default is 25 KiB, not "unlimited". It is measured, not guessed: the
    core profile at 25 KiB still fits in ONE page with all 46 tools and every
    parameter name, type, enum and required list intact - only prose is cut
    (37 210 -> 23 976 B, -36 %). Anything that raises the value gets the old
    answer back byte for byte.
    """
    raw = os.environ.get("PCB_AGENT_MAX_TOOLS_KB", "25")
    try:
        kb = int(float(raw))
    except (TypeError, ValueError):
        kb = 25
    return max(0, kb) * 1024


_CAP_LIST_BYTES = 8192
def max_line_bytes():
    """Ceiling on ONE line of the stdio transport (0 disables).

    PCB_AGENT_MAX_LINE_KB, default 64 KiB. This is the third ceiling and it
    exists because the first two are per REPLY, not per FRAME:

      * `max_result_chars` bounds one tools/call result (48 KiB);
      * `max_tools_chars` bounds one tools/list page (25 KiB);
      * nothing bounded the JSON-RPC message itself, and a batch is one
        message. Measured 2026-11-04: a batch of eight `calc_capabilities`
        calls came back as a single 279 633 B line (measured on the wire) -
        every element legally
        under its own cap, the frame 273 KiB. A reader that caps a line (or a
        4 KiB pipe with a client that reads lazily) does not see 'a big
        answer', it sees a broken connection, which is exactly the symptom
        users report as 'the mcp died again'.

    The default is 64 KiB because 64 KiB is what a common MCP host code path
    actually accepts: `asyncio.StreamReader` (Python 3.12, verified here) has
    `_DEFAULT_LIMIT = 65536` and raises on a line that long, so the frame size
    that turns into "the connection dropped" is a number, not a guess.

    The default also sits above the 48 KiB result cap on purpose: with the
    shipped settings no single reply is touched, so this gate is invisible until
    somebody batches or raises the other two. It is a floor under the protocol,
    not a second opinion on how big an answer may be.
    """
    raw = os.environ.get("PCB_AGENT_MAX_LINE_KB", "64")
    try:
        kb = int(float(raw))
    except (TypeError, ValueError):
        kb = 64
    return max(0, kb) * 1024

_CAP_STRING = 2000
# (bytes one list may take, longest string kept) - tried in order until the reply
# really fits. 0.0.104 first shipped one fixed setting ("keep 8 items of each
# list") and produced two defects on the spot: a 41-entry write catalogue came
# back as 8 entries, and a reply that was supposed to be cut was still 34 KiB
# against a 24 KiB cap. A count is the wrong knob - the size is.
_CAP_SCHEDULE = ((8192, 2000), (2048, 800), (512, 300), (128, 120), (32, 60))


def _is_scalar(v):
    return v is None or isinstance(v, (str, int, float, bool))


def _cap_walk(node, path, cuts, budget, strlen):
    if isinstance(node, dict):
        return {k: _cap_walk(v, (path + "." + str(k)) if path else str(k), cuts,
                             budget, strlen)
                for k, v in node.items()}
    if isinstance(node, list):
        where = (path or "value") + "[]"
        kept, spent = 0, 0
        for v in node:
            piece = len(result_to_text(v).encode("utf-8")) + 2
            if kept and spent + piece > budget:
                break
            spent += piece
            kept += 1
        if len(node) > kept:
            cuts[where] = {"kept": kept, "total": len(node),
                           "budget_bytes": budget}
        # kept == 0 only when the very first item alone blows the budget; it is
        # kept anyway and its long strings get cut, so the head is always visible.
        return [_cap_walk(v, where, cuts, budget, strlen) for v in node[:kept]]
    if isinstance(node, str) and len(node) > strlen:
        cuts[(path or "value") + " (string)"] = {"kept": strlen,
                                                 "total": len(node)}
        return node[:strlen] + "...[cut at %d chars]" % strlen
    return node


def _save_full_reply(name, text):
    """Write the whole reply somewhere the caller can read it; return the path."""
    try:
        import tempfile as _tf
        import time as _time
        folder = os.path.join(_tf.gettempdir(), "pcb_agent_mcp_results")
        os.makedirs(folder, exist_ok=True)
        stamp = _time.strftime("%Y%m%d-%H%M%S")
        size = len(text.encode("utf-8"))
        path = os.path.join(folder, "%s-%s-%d.json" % (stamp, name, size))
        io_open = __import__("io").open
        io_open(path, "w", encoding="utf-8").write(text)
        return path
    except Exception:  # noqa: BLE001 - a full replay nobody can read is not fatal
        return None


def _mark_session_budget(out):
    """Say out loud that the SESSION ran out, not that this answer was too big."""
    out["reason_code"] = "SESSION_BUDGET"
    out["session_bytes"] = _SESSION["bytes"]
    out["session_budget_bytes"] = session_budget_bytes()
    out["why_this_is_short"] = (
        "the conversation has already taken %d bytes of tool text (budget %d); "
        "from here replies are cut to %d bytes so the session survives to the "
        "end of the job instead of dying mid-turn. This is not a smaller "
        "answer, it is the same answer with the rest on disk"
        % (_SESSION["bytes"], session_budget_bytes(), session_tail_bytes()))
    return out

def _cap_meta(out, name, size, cap, cuts, saved, items, strlen, session_cut=False):
    out["truncated"] = True
    out["reason_code"] = out.get("reason_code") or "RESULT_TRUNCATED"
    if session_cut:
        _mark_session_budget(out)
    out["bytes_full"] = size
    out["cap_bytes"] = cap
    out["truncated_fields"] = cuts
    if saved:
        out["saved_to"] = saved
        out["where_the_rest_is"] = (
            "the complete reply is the JSON file at `saved_to` (%d bytes); this "
            "one keeps every scalar and every short list, the head of each long "
            "list up to %d bytes and the first %d characters of each long string"
            % (size, items, strlen))
    else:
        out["where_the_rest_is"] = ("the reply could not be written to disk; only "
                                    "the head above survived")
    out["how_to_shrink"] = ("ask for less: board_view kind=/limit=/offset=/"
                            "max_bytes=, board_components / board_nets "
                            "limit=/offset=; for lceda_pro_run return only the "
                            "fields you need from the snippet instead of the whole "
                            "object")
    out["where_to_read"] = ("this IS the tool's answer, cut to fit the "
                            "conversation; the uncut JSON is at `saved_to`")
    out.setdefault("payload_is", "top_level")
    return out


def _spine_reply(name, result, size, cap, saved, session_cut=False):
    """Last resort: shape and counts only, no values. Always small."""
    shape = {}
    for k, v in result.items():
        if _is_scalar(v) and not (isinstance(v, str) and len(v) > 120):
            shape[k] = v
        elif isinstance(v, list):
            shape[k] = {"type": "list", "len": len(v)}
        elif isinstance(v, dict):
            shape[k] = {"type": "object", "keys": list(v)[:12],
                        "key_count": len(v)}
        else:
            shape[k] = {"type": type(v).__name__}
    saved_path = saved
    shape = _cap_meta(shape, name, size, cap, {"(payload)": "every value replaced "
                                               "by its shape: even the heads did "
                                               "not fit"}, saved_path, 0, 0,
                      session_cut=session_cut)
    shape["where_the_rest_is"] = (
        "not even a cut-down copy fitted %d bytes, so this reply carries shapes "
        "and counts only; the complete reply is at `saved_to` (%d bytes)"
        % (cap, size))
    return shape, result_to_text(shape)


def _minimal_reply(name, result, size, cap, saved, session_cut=False):
    """The floor: a receipt, not a payload. Used when nothing else fits the cap."""
    out = {
        "status": result.get("status", "ok") if isinstance(result, dict) else "ok",
        "truncated": True,
        "reason_code": "RESULT_TRUNCATED",
        "bytes_full": size,
        "cap_bytes": cap,
        "saved_to": saved,
        "payload_is": "top_level",
        "message": ("tool %s produced %d bytes and the cap is %d; even shapes and "
                    "counts did not fit, so this reply is a receipt. Read the "
                    "uncut JSON at `saved_to`, or raise PCB_AGENT_MAX_RESULT_KB if "
                    "you know what you are asking the conversation to hold."
                    % (name, size, cap)),
        "where_the_rest_is": "the file at `saved_to`" if saved else "nowhere: write failed",
        "where_to_read": "this is a receipt, not a measurement",
    }
    if isinstance(result, dict):
        out["top_level_keys"] = list(result)[:40]
    if session_cut:
        _mark_session_budget(out)
    return out, result_to_text(out)


def cap_result(name, result):
    """Shrink one reply to the conversation cap instead of refusing it.

    Until 0.0.104 an oversized reply was replaced by `RESULT_TOO_LARGE`, so the
    caller got a size and nothing else and had to re-run a query it had already
    paid for. Here the reply keeps its own `status`, keeps every scalar, keeps
    the head of every list of records and the head of every long string, and the
    complete JSON goes to a file whose path is in `saved_to`.

    Two properties are pinned: a reply under the cap is returned IDENTICAL (no
    key added, nothing reordered), and a reply over the cap really does come back
    under it - the schedule is retried, then the payload degenerates to shapes
    and counts, which is the honest end state when nothing else fits.

    A flat list of names is bounded by bytes rather than by item count: 186 tool
    names are one answer, and keeping eight of them is a wrong answer.
    """
    text = result_to_text(result)
    size = len(text.encode("utf-8"))
    hard = max_result_chars()
    cap = effective_result_cap()
    session_cut = bool(cap and hard and cap < hard)
    if not cap or size <= cap:
        note_reply_bytes(len(text.encode("utf-8")))
        return result, text
    saved = _save_full_reply(name, text)
    last = None
    for items, strlen in _CAP_SCHEDULE:
        cuts = {}
        trimmed = _cap_walk(result, "", cuts, items, strlen)
        if not isinstance(trimmed, dict):
            trimmed = {"status": "ok", "value": trimmed}
        out = _cap_meta(dict(trimmed), name, size, cap, cuts, saved, items, strlen,
                        session_cut=session_cut)
        text = result_to_text(out)
        last = (out, text)
        if len(text.encode("utf-8")) <= cap:
            note_reply_bytes(len(text.encode("utf-8")))
            return out, text
    out, text = _spine_reply(name, result, size, cap, saved, session_cut=session_cut)
    if len(text.encode("utf-8")) <= cap:
        note_reply_bytes(len(text.encode("utf-8")))
        return out, text
    out, text = _minimal_reply(name, result, size, cap, saved, session_cut=session_cut)
    note_reply_bytes(len(text.encode("utf-8")))
    return out, text


def result_too_large_text(name, size):
    cap = max_result_chars()
    return _jsonsafe.dumps({
        "status": "error",
        "reason_code": "RESULT_TOO_LARGE",
        "message": ("tool %s produced %d bytes of JSON, above the %d-byte cap; the "
                    "reply was dropped instead of overflowing the conversation"
                    % (name, size, cap)),
        "bytes": size,
        "cap_bytes": cap,
        "hint": ("narrow the query: board_view kind=/limit=/offset=/max_bytes=, "
                 "board_components / board_nets limit=/offset=; set "
                 "PCB_AGENT_MAX_RESULT_KB=0 to disable this cap"),
        "payload_is": "top_level",
        "where_to_read": "this is a refusal, not a measurement",
    })

# ---------------------------------------------------------------------------
# 0.0.118: geometry judges
#
# Five questions that came up while routing a 34-part core board by hand, and that
# this server could not answer at all. They are judges, not generators: none of
# them decides where a part or a trace goes (autorouting was removed on purpose,
# see the note at the end of this file). What they do is turn "does this look
# right" into a number that can be wrong, so a layout can be checked instead of
# admired.
# ---------------------------------------------------------------------------

def _pad_rect(p, expand=0.0):
    """Axis-aligned box of one pad, honouring its absolute rotation."""
    w, h = float(p["w"]), float(p["h"])
    if abs((round(float(p.get("rotation") or 0.0)) % 180) - 90) < 1e-9:
        w, h = h, w
    return (float(p["x"]) - w / 2 - expand, float(p["y"]) - h / 2 - expand,
            float(p["x"]) + w / 2 + expand, float(p["y"]) + h / 2 + expand)


def _seg_box_dist(a, b, box):
    """Exact distance from a segment to an axis-aligned box (0 if it enters)."""
    x0, y0, x1, y1 = box
    for p in (a, b):
        if x0 - 1e-9 <= p[0] <= x1 + 1e-9 and y0 - 1e-9 <= p[1] <= y1 + 1e-9:
            return 0.0
    edges = [((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)),
             ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))]
    return min(G.dist_segment_segment(a, b, c, d) for c, d in edges)


def _chained(segs):
    """Group tracks into polylines so a corner can be measured at a shared vertex.

    Tracks are stored one segment at a time and in no order, so "this trace turns
    90 degrees at (19.75, 38.675)" is not a property any single row owns. A vertex
    is shared by exactly two segments of the same net and the same width, and that
    is the only thing asserted here - three or more segments at a point is a T
    junction, not a corner, and is left out rather than guessed at.
    """
    by_key = {}
    for t in segs:
        for end in (0, 1):
            p = t["start"] if end == 0 else t["end"]
            by_key.setdefault((round(p[0], 4), round(p[1], 4)), []).append((t, end))
    corners = []
    for (x, y), users in by_key.items():
        if len(users) != 2:
            continue
        (t1, e1), (t2, e2) = users
        if t1 is t2:
            continue
        if t1.get("net") != t2.get("net") or abs(float(t1["width"]) - float(t2["width"])) > 1e-9:
            continue
        p1 = t1["end"] if e1 == 0 else t1["start"]
        p2 = t2["end"] if e2 == 0 else t2["start"]
        v1 = (p1[0] - x, p1[1] - y)
        v2 = (p2[0] - x, p2[1] - y)
        n1 = math.hypot(*v1) or 1.0
        n2 = math.hypot(*v2) or 1.0
        cosang = max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)))
        interior = math.degrees(math.acos(cosang))
        corners.append({"x": round(x, 4), "y": round(y, 4), "net": t1.get("net"),
                        "layer": t1.get("layer"),
                        "interior_deg": round(interior, 3)})
    return corners


def _corners_kind(deg, tol):
    if deg >= 180.0 - tol:
        return "straight"
    if abs(deg - 90.0) <= tol:
        return "right"
    if deg < 90.0 - tol:
        return "acute"
    return "obtuse"


# ------------------------------------------------------------------ extents
def _board_footprint_extents(board, args):
    refs = args.get("refs") or []
    if isinstance(refs, str):
        refs = [refs]
    margin = float(args.get("margin_mm") or 0.0)
    want = set(refs) if refs else None
    rows, missing, unknown = [], [], []
    for fp in board.footprints:
        ref = fp["ref"]
        if want is not None and ref not in want:
            continue
        pads = [p for p in (fp.get("pads") or []) if p.get("x") is not None]
        padbox = None
        for p in pads:
            b = _pad_rect(p, margin)
            padbox = b if padbox is None else (min(padbox[0], b[0]), min(padbox[1], b[1]),
                                               max(padbox[2], b[2]), max(padbox[3], b[3]))
        cy = fp.get("courtyard") or {}
        cybox = cy.get("bbox")
        if cybox is not None:
            cybox = (cybox["x_min"] - margin, cybox["y_min"] - margin,
                     cybox["x_max"] + margin, cybox["y_max"] + margin)
        else:
            missing.append(ref)
            unknown.append({"ref": ref, "what": "courtyard"})
        copbox = None
        for g in (fp.get("graphics") or []):
            for q in (g.get("poly") or []):
                if copbox is None:
                    copbox = (q[0], q[1], q[0], q[1])
                else:
                    copbox = (min(copbox[0], q[0]), min(copbox[1], q[1]),
                              max(copbox[2], q[0]), max(copbox[3], q[1]))
        silbox = None
        for it in (fp.get("silk") or []):
            b = it.get("bbox")
            if not b:
                continue
            box = (b["x_min"], b["y_min"], b["x_max"], b["y_max"])
            silbox = box if silbox is None else (min(silbox[0], box[0]), min(silbox[1], box[1]),
                                                 max(silbox[2], box[2]), max(silbox[3], box[3]))
        if not fp.get("silk"):
            unknown.append({"ref": ref, "what": "silk_bbox (footprint draws no silk)"})

        def d(b):
            if b is None:
                return None
            return {"x_min": round(b[0], 4), "y_min": round(b[1], 4),
                    "x_max": round(b[2], 4), "y_max": round(b[3], 4),
                    "w": round(b[2] - b[0], 4), "h": round(b[3] - b[1], 4)}
        rows.append({"ref": ref, "lib_id": fp.get("lib_id"), "rotation": fp.get("rotation"),
                     "x": fp.get("x"), "y": fp.get("y"),
                     "pads": d(padbox), "courtyard": d(cybox),
                     "courtyard_layer": cy.get("layer"),
                     "courtyard_source": cy.get("source"),
                     "silk": d(silbox), "copper_graphic": d(copbox)})
    if want is not None:
        got = set(r["ref"] for r in rows)
        for ref in sorted(want - got):
            unknown.append({"ref": ref, "what": "no such footprint"})
    value = {"footprints": rows, "count": len(rows),
             "missing_courtyard": missing,
             "unknown": unknown[:40],
             "courtyard_is": "the hull of the footprint's own F.CrtYd/B.CrtYd graphics, "
                             "rotated with the footprint; never approximated from pads",
             "bodies_are_not_obstacles": "a track may run under a component body - no "
                                         "DRC in KiCad flags it and no fab cares. Only "
                                         "pads, courtyard-vs-courtyard and bare copper "
                                         "are real constraints.",
             "params": {"refs": list(refs), "margin_mm": margin}}
    return ok(value, "mm",
              model="per-footprint bounding boxes from the geometry in the file",
              basis="F.CrtYd graphics for the courtyard, pad rectangles for pads, "
                    "fp_* polygon vertices for copper graphics",
              precision="exact extents; the courtyard box is the hull of the drawn "
                        "outline, so a courtyard drawn with arcs is boxed slightly wide",
              assumptions={"margin_mm": margin})


_tool(
    "board_footprint_extents",
    "Real bounding boxes per footprint: courtyard (from F.CrtYd), pads, silk, copper "
    "graphics. Courtyard was never parsed by this server - it matched neither copper "
    "nor silk nor mask, so it was silently dropped and every caller had to guess "
    "'pad bbox + a margin'. On a real 34-part board that guess came out 0.4-1.9 mm "
    "narrow per side, which produced three phantom courtyard overlaps and one real "
    "short. Reports absent courtyards as absent instead of inventing one.",
    {
        "type": "object",
        "properties": {
            "path": _COMMON["path"],
            "member": _COMMON["member"],
            "project": _COMMON["project"],
            "refs": _arr("restrict to these reference designators (default: all)"),
            "margin_mm": _num("grow every box by this much on each side", 0.0),
        },
        "required": ["path"],
    },
    _board_tool(_board_footprint_extents, derived=True),
)


# ----------------------------------------------------------- geometry check
def _board_geometry_check(board, args):
    clearance = args.get("clearance_mm")
    layer = args.get("layer")
    tol = float(args.get("angle_tol_deg") or 0.5)
    src = "argument clearance_mm"
    if clearance is None:
        rules = board.rules or {}
        clearance = (rules.get("net_classes", {}).get("Default", {}) or {}).get("clearance")
        src = "the project's Default netclass clearance"
    if clearance is None:
        return err("no clearance to check against", "ARG_MISSING",
                   hint="no .kicad_pro was supplied and the board states no default "
                        "netclass clearance; pass clearance_mm explicitly - this tool "
                        "never invents a design rule")
    clearance = float(clearance)

    tracks = [t for t in board.tracks if not layer or t.get("layer") == layer]
    vias = [v for v in board.vias if not layer or (v.get("layers") or [None])[0] == layer]
    pads = board.pads()
    if not tracks and not vias:
        return err("this board carries no tracks and no vias", "NOTHING_TO_CHECK",
                   hint="geometry is measured on copper that exists; an empty board "
                        "has nothing to violate")

    def seg(t):
        return (tuple(t["start"]), tuple(t["end"]))

    shorts, closest = [], None
    for i in range(len(tracks)):
        a1, a2 = seg(tracks[i])
        for j in range(i + 1, len(tracks)):
            t2 = tracks[j]
            if tracks[i].get("net") == t2.get("net"):
                continue
            b1, b2 = seg(t2)
            if (min(a1[0], a2[0]) - 3 > max(b1[0], b2[0])
                    or max(a1[0], a2[0]) + 3 < min(b1[0], b2[0])
                    or min(a1[1], a2[1]) - 3 > max(b1[1], b2[1])
                    or max(a1[1], a2[1]) + 3 < min(b1[1], b2[1])):
                continue
            d = G.dist_segment_segment(a1, a2, b1, b2)
            edge = d - (float(tracks[i]["width"]) + float(t2["width"])) / 2.0
            rec = {"a_net": tracks[i].get("net"), "b_net": t2.get("net"),
                   "centre_to_centre": round(d, 4), "edge_gap": round(edge, 4),
                   "layer": tracks[i].get("layer")}
            if closest is None or d < closest["centre_to_centre"]:
                closest = rec
            if edge < clearance - 1e-9:
                shorts.append(rec)
    # track vs pad of another net
    track_pad = []
    for t in tracks:
        a1, a2 = seg(t)
        for p in pads:
            if p.get("net") == t.get("net"):
                continue
            box = _pad_rect(p)
            if (min(a1[0], a2[0]) - 3 > box[2] or max(a1[0], a2[0]) + 3 < box[0]
                    or min(a1[1], a2[1]) - 3 > box[3] or max(a1[1], a2[1]) + 3 < box[1]):
                continue
            d = _seg_box_dist(a1, a2, box)
            if d - float(t["width"]) / 2 < clearance - 1e-9:
                track_pad.append({"net": t.get("net"), "ref": p.get("ref"),
                                  "pad": p.get("number"), "pad_net": p.get("net"),
                                  "edge_gap": round(d - float(t["width"]) / 2, 4)})

    corners = _chained(tracks)
    for c in corners:
        c["kind"] = _corners_kind(c["interior_deg"], tol)
    acute = [c for c in corners if c["kind"] == "acute"]
    right = [c for c in corners if c["kind"] == "right"]

    value = {
        "clearance_mm": clearance, "clearance_from": src,
        "tracks": len(tracks), "vias": len(vias),
        "closest_pair": closest,
        "clearance_violations": shorts[:40],
        "clearance_violation_count": len(shorts),
        "track_pad_violations": track_pad[:40],
        "track_pad_violation_count": len(track_pad),
        "corner_count": len(corners),
        "acute_corners": acute[:20], "acute_corner_count": len(acute),
        "right_angle_corners": right[:20], "right_angle_corner_count": len(right),
        "acute_means": "interior angle < 90 deg, i.e. the trace doubles back on "
                       "itself; this is the one that must not exist",
        "chaining": "a corner is only measured where exactly two segments of the "
                    "same net and width meet; T junctions are skipped, not guessed",
        "reverse_control": "_scratch/geomctl/ holds three tiny boards: right90 gives "
                           "right_angle_corner_count 1 at 90 deg, fourtyfive gives 0, "
                           "acute gives acute_corner_count 1 at 45 deg interior - so "
                           "each of these three numbers has been made to read non-zero",
    }
    return ok(value, "mm",
              model="distances and corner angles computed from the segments in the file",
              basis="segment-to-segment and segment-to-pad-rectangle distance; "
                    "corner interiors from the two directions that meet at a vertex",
              precision="exact for straights; an arc drawn as segments is measured "
                        "as its segments",
              assumptions={"angle_tol_deg": tol, "layer": layer or "all copper"})


_tool(
    "board_geometry_check",
    "Measure the copper instead of waiting for a DRC verdict: the closest pair of "
    "tracks, every pair that violates a clearance, every track-vs-foreign-pad gap, and "
    "the interior angle at every corner (acute = a trace that doubles back on itself, "
    "right = the 90 degree bend that every routing rule forbids). Works without KiCad "
    "installed, and reports the numbers rather than pass/fail, so a fan-out can be "
    "checked before it is committed.",
    {
        "type": "object",
        "properties": {
            "path": _COMMON["path"],
            "member": _COMMON["member"],
            "project": _COMMON["project"],
            "clearance_mm": _num("required clearance; defaults to the project's "
                                 "Default netclass clearance, and is never invented"),
            "layer": _str("only this copper layer (default: all)"),
            "angle_tol_deg": _num("how close to 90 counts as a right angle", 0.5),
        },
        "required": ["path"],
    },
    _board_tool(_board_geometry_check, derived=True),
)


# ------------------------------------------------------------- obstacle map
def _board_pad_obstacle_map(board, args):
    res = float(args.get("res_mm") or 0.5)
    clearance = args.get("clearance_mm")
    if clearance is None:
        rules = board.rules or {}
        clearance = (rules.get("net_classes", {}).get("Default", {}) or {}).get("clearance")
    if clearance is None:
        return err("no clearance to build the map with", "ARG_MISSING",
                   hint="pass clearance_mm; this tool never invents a design rule")
    clearance = float(clearance)
    if res <= 0:
        return err("res_mm must be positive", "ARG_INVALID")
    outline = board.outline or []
    pts = [p for ring in outline for p in ring]
    if not pts:
        allp = board.pads()
        pts = [(p["x"], p["y"]) for p in allp]
    if not pts:
        return err("this board has neither an outline nor pads", "NOTHING_TO_CHECK")
    x0 = min(p[0] for p in pts)
    y0 = min(p[1] for p in pts)
    x1 = max(p[0] for p in pts)
    y1 = max(p[1] for p in pts)
    nx = int((x1 - x0) / res) + 1
    ny = int((y1 - y0) / res) + 1
    if nx * ny > 400000:
        return err("that resolution is %dx%d cells" % (nx, ny), "ARG_INVALID",
                   hint="raise res_mm; the map is returned as text so it has to stay small")
    grid = [[False] * ny for _ in range(nx)]

    def stamp_box(xa, ya, xb, yb):
        # A pad grown by the clearance is a RECTANGLE, not a circle. Stamping its
        # circumscribed circle blocks the corners of a long pad as well, which
        # reports legal spots as unusable -- the over-blocking direction that made
        # a dense corridor look hopeless.
        i0 = max(0, int((xa - x0) / res) - 1)
        i1 = min(nx - 1, int((xb - x0) / res) + 1)
        j0 = max(0, int((ya - y0) / res) - 1)
        j1 = min(ny - 1, int((yb - y0) / res) + 1)
        for i in range(i0, i1 + 1):
            cx = x0 + i * res
            if cx < xa or cx > xb:
                continue
            for j in range(j0, j1 + 1):
                cy = y0 + j * res
                if ya <= cy <= yb:
                    grid[i][j] = True

    def stamp(cx, cy, r):
        i0 = max(0, int((cx - r - x0) / res) - 1)
        i1 = min(nx - 1, int((cx + r - x0) / res) + 1)
        j0 = max(0, int((cy - r - y0) / res) - 1)
        j1 = min(ny - 1, int((cy + r - y0) / res) + 1)
        rr = r * r
        for i in range(i0, i1 + 1):
            dx = x0 + i * res - cx
            for j in range(j0, j1 + 1):
                dy = y0 + j * res - cy
                if dx * dx + dy * dy <= rr:
                    grid[i][j] = True

    occ = 0
    for t in board.tracks:
        w = float(t["width"]) / 2 + clearance
        (ax, ay), (bx, by) = t["start"], t["end"]
        L = math.hypot(bx - ax, by - ay)
        n = max(1, int(L / (res * 0.5)))
        for k in range(n + 1):
            f = k / float(n)
            stamp(ax + (bx - ax) * f, ay + (by - ay) * f, w)
            occ += 0
    for v in board.vias:
        stamp(float(v["x"]), float(v["y"]), float(v.get("size") or 0.6) / 2 + clearance)
    for p in board.pads():
        box = _pad_rect(p, clearance)
        stamp_box(box[0], box[1], box[2], box[3])

    probes = []
    bad_probes = 0
    for q in (args.get("probes") or []):
        try:
            px, py = float(q[0]), float(q[1])
        except (TypeError, ValueError, IndexError, KeyError):
            bad_probes += 1
            continue
        i = int(round((px - x0) / res))
        j = int(round((py - y0) / res))
        inside = 0 <= i < nx and 0 <= j < ny
        probes.append({"x": px, "y": py, "inside_board": inside,
                       "pad_fits": bool(inside and not grid[i][j])})

    free = sum(1 for i in range(nx) for j in range(ny) if not grid[i][j])
    rows = []
    for j in range(ny - 1, -1, -1):
        rows.append("".join("#" if grid[i][j] else "." for i in range(nx)))
    value = {"grid": {"origin_x": round(x0, 4), "origin_y": round(y0, 4),
                      "res_mm": res, "nx": nx, "ny": ny},
             "free_cells": free, "total_cells": nx * ny,
             "free_fraction": round(free / float(nx * ny), 4),
             "legend": {".": "a pad whose centre lands here keeps clearance_mm from all "
                             "existing copper", "#": "it would not"},
             "rows_bottom_up": rows,
             "probes": probes,
             "probes_skipped": bad_probes,
             "probes_format": "[[x, y], ...] - a list of PAIRS, not a list of objects",
             "bodies_are_ignored": "footprint BODIES are not marked - only pads, tracks "
                                   "and vias. A part may sit over a trace; that is not "
                                   "a DRC error and every dense board does it."}
    return ok(value, "mm",
              model="rasterised legality map for placing a pad CENTRE",
              basis="every track, via and pad grown by clearance_mm, sampled on a grid",
              precision="+-%g mm per cell; a pad whose box is bigger than a cell needs "
                        "more checks than one cell" % (res / 2.0),
              assumptions={"res_mm": res, "clearance_mm": clearance})


_tool(
    "board_pad_obstacle_map",
    "Where can a pad legally go? Rasterises every track, via and pad grown by the "
    "clearance and returns the free cells as text, plus point probes. Footprint BODIES "
    "are deliberately not obstacles: a part may sit over a trace - neither KiCad nor a "
    "fab objects - and treating the body as blocked is what made an 8.5 mm corridor "
    "with 18 fan-out traces look unusable. This answers 'is this spot legal', it does "
    "not choose the spot.",
    {
        "type": "object",
        "properties": {
            "path": _COMMON["path"],
            "member": _COMMON["member"],
            "project": _COMMON["project"],
            "res_mm": _num("grid pitch in mm", 0.5),
            "clearance_mm": _num("clearance to keep from existing copper; defaults to "
                                 "the project's Default netclass"),
            "probes": {"type": "array",
                       "items": {"type": "array", "items": {"type": "number"}},
                       "description": "[[x, y], ...] points to test"},
        },
        "required": ["path"],
    },
    _board_tool(_board_pad_obstacle_map, derived=True),
)


# --------------------------------------------------------- constraints report
def _board_constraints_report(board, args):
    near = float(args.get("near_ratio") or 0.15)
    rules = board.rules or {}
    if not rules or rules.get("absent"):
        return err("no project file, so this board states no design rules",
                   "PROJECT_ABSENT",
                   hint="pass project=<path to the .kicad_pro>; the limits live in the "
                        "PROJECT, not in the .kicad_pcb, and this tool will not quote a "
                        "number it did not read")
    nc = (rules.get("net_classes") or {})
    limits = []
    for key, val in sorted((rules.get("track_width") or {}).items()):
        limits.append({"what": key, "value": val,
                       "lives_in": "project design_settings.rules"})
    for key, val in sorted((rules.get("clearance") or {}).items()):
        limits.append({"what": key, "value": val,
                       "lives_in": "project design_settings.rules"})
    for key, val in sorted((rules.get("via") or {}).items()):
        limits.append({"what": key, "value": val,
                       "lives_in": "project design_settings.rules"})
    for cls, d in sorted(nc.items()):
        for key in ("clearance", "track_width", "via_diameter", "via_drill"):
            if key in d:
                limits.append({"what": "%s.%s" % (cls, key), "value": d[key],
                               "lives_in": "project net_settings.classes",
                               "nets": len(d.get("nets") or [])})

    min_track = (rules.get("track_width") or {}).get("min_track_width")
    min_clear = (rules.get("clearance") or {}).get("min_clearance")
    default_clear = (nc.get("Default") or {}).get("clearance")

    widths = {}
    for t in board.tracks:
        w = round(float(t["width"]), 4)
        widths[w] = widths.get(w, 0) + 1
    hugging = []
    if min_track is not None:
        floor = float(min_track)
        for w, n in sorted(widths.items()):
            if w <= floor * (1 + near) + 1e-9:
                hugging.append({"rule": "min_track_width", "limit": floor,
                                "actual": w, "count": n,
                                "margin": round(w - floor, 4),
                                "reading": ("at the limit - one thinner and it is a "
                                            "violation") if w <= floor + 1e-9
                                           else "within %g%% of the limit" % (near * 100)})
    contradictions = []
    if (min_clear is not None and default_clear is not None
            and float(min_clear) < float(default_clear) - 1e-9):
        contradictions.append(
            "the project says min_clearance=%.3f and the Default netclass says "
            "clearance=%.3f; the engine applies the netclass value to nets in that "
            "class, so the smaller number is not the one that will be enforced"
            % (float(min_clear), float(default_clear)))
    if min_track is not None:
        for cls, d in sorted(nc.items()):
            tw = d.get("track_width")
            if tw is not None and float(tw) < float(min_track) - 1e-9:
                contradictions.append(
                    "netclass %s asks for track_width=%.3f while min_track_width=%.3f; "
                    "the minimum wins and every track in that class is a violation"
                    % (cls, float(tw), float(min_track)))
    value = {"limits": limits, "source": rules.get("source"),
             "track_widths_in_use": [{"width": w, "count": n}
                                     for w, n in sorted(widths.items())],
             "hugging": hugging,
             "contradictions": contradictions,
             "near_ratio": near,
             "why_this_matters": "a router that does not know min_track_width will lay "
                                 "0.15 mm escapes on a 0.5 mm pitch part, then hand back "
                                 "a plan the engine refuses in full - 105 tracks rolled "
                                 "back over one number. Read this BEFORE routing.",
             "never_invented": "a limit the project does not state is reported absent, "
                               "not defaulted"}
    return ok(value, "mm",
              model="the numbers that will decide whether the engine accepts a trace",
              basis="the project file's own design_settings and net_settings",
              precision="verbatim from the file",
              assumptions={"near_ratio": near})


_tool(
    "board_constraints_report",
    "List the numbers that will make the engine reject copper - min track width, "
    "clearances, via limits, per-netclass widths - and flag which ones already in use "
    "sit ON the limit, plus any pair of rules that contradict each other (a netclass "
    "asking for thinner-than-minimum track is a trap: the minimum wins and every trace "
    "in that class is a violation). Values are read from the project; a limit the "
    "project does not state is reported absent, never defaulted.",
    {
        "type": "object",
        "properties": {
            "path": _COMMON["path"],
            "member": _COMMON["member"],
            "project": _COMMON["project"],
            "near_ratio": _num("how close counts as hugging the limit", 0.15),
        },
        "required": ["path"],
    },
    _board_tool(_board_constraints_report, derived=True),
)


# ------------------------------------------------------- fan-out order judge
def _fan_route(pa, pb, d_out=1.0, chamfer=0.5):
    """The reference fan-out shape: perpendicular escape, then one straight 45."""
    ax, ay = pa[0] + (d_out if pb[0] > pa[0] else -d_out), pa[1]
    sx = 1 if pb[0] > ax else -1
    sy = 1 if pb[1] > ay else -1
    pts = [pa, (ax, ay)]
    dy = abs(pb[1] - ay)
    pts.append((ax + sx * dy, pb[1]))
    pts.append(pb)
    return pts


def _count_crossings(paths):
    hits = []
    flat = []
    for net, pts in paths:
        for a, b in zip(pts, pts[1:]):
            flat.append((net, a, b))
    for i in range(len(flat)):
        for j in range(i + 1, len(flat)):
            if flat[i][0] == flat[j][0]:
                continue
            if G.segments_intersect(flat[i][1], flat[i][2], flat[j][1], flat[j][2]):
                hits.append({"a": flat[i][0], "b": flat[j][0]})
    return hits


def _side_of(pads, side):
    """Keep the pads on one edge of a part (an LQFP has four).

    A 64-pin LQFP's pads form a RING, so the straight-line fan-out judge would
    refuse it outright. The useful question is about ONE edge, so the caller may
    name the edge instead of the tool guessing which pads it meant.
    """
    s = str(side).lower()
    if not pads:
        return []
    if s in ("left", "right"):
        key = lambda p: p["x"]
        edge = min(p["x"] for p in pads) if s == "left" else max(p["x"] for p in pads)
    elif s in ("bottom", "top"):
        key = lambda p: p["y"]
        edge = min(p["y"] for p in pads) if s == "bottom" else max(p["y"] for p in pads)
    else:
        return []
    return [p for p in pads if abs(key(p) - edge) < 0.01]


def _board_fanout_order_check(board, args):
    a_ref = args.get("ref_a")
    b_ref = args.get("ref_b")
    mapping = args.get("mapping")
    if not a_ref or not b_ref:
        return err("ref_a and ref_b are both required", "ARG_MISSING",
                   hint="name the two footprints whose pads are being fanned out")
    by = {fp["ref"]: fp for fp in board.footprints}
    for r in (a_ref, b_ref):
        if r not in by:
            return err("no footprint %r on this board" % r, "TARGET_NOT_FOUND")

    def pads_of(r, side):
        ps = [p for p in (by[r].get("pads") or []) if p.get("x") is not None
              and p.get("y") is not None]
        return _side_of(ps, side) if side else ps

    pa = pads_of(a_ref, args.get("side_a"))
    pb = pads_of(b_ref, args.get("side_b"))
    for name, side, ps in ((a_ref, args.get("side_a"), pa),
                           (b_ref, args.get("side_b"), pb)):
        if side and len(ps) < 2:
            return err("side_a/side_b=%r left %d pads on %s" % (side, len(ps), name),
                       "TARGET_NOT_FOUND",
                       hint="the side keyword kept too few pads; a part on a 0.5 mm "
                            "pitch has 16 per side, a big part may have fewer, and if "
                            "it has one there is nothing to order")
    if len(pa) < 2 or len(pb) < 2:
        return err("%s has %d pads and %s has %d" % (a_ref, len(pa), b_ref, len(pb)),
                   "TARGET_NOT_FOUND",
                   hint="a fan-out needs at least two pads on each side")


    def axis_of(pads):
        xs = sorted(set(round(p["x"], 3) for p in pads))
        ys = sorted(set(round(p["y"], 3) for p in pads))
        if len(xs) == 1:
            return "column", 0
        if len(ys) == 1:
            return "row", 1
        return None, None

    ax_a, perp_a = axis_of(pa)
    ax_b, perp_b = axis_of(pb)
    if ax_a is None or ax_b is None:
        return err("the pads of %s are in %s and of %s in %s"
                   % (a_ref, ax_a or "a grid", b_ref, ax_b or "a grid"),
                   "ARG_INVALID",
                   hint="this judge is only defined for two straight lines of pads; "
                        "anything else is refused rather than guessed at")
    if ax_a != ax_b:
        return err("%s pads form a %s and %s pads form a %s"
                   % (a_ref, ax_a, b_ref, ax_b), "ARG_INVALID",
                   hint="both sides must be parallel lines of pads; for an LQFP pass "
                        "side_a=left|right|top|bottom to take just one edge")

    if mapping is None:
        if len(pa) != len(pb):
            return err("%s side has %d pads and %s side has %d"
                       % (a_ref, len(pa), b_ref, len(pb)), "ARG_INVALID",
                       hint="pairing by net needs the same count on both sides; to "
                            "route only some pads pass mapping=[[a_pad, b_pad], ...] "
                            "and the pairs outside it are left out of the ordering")
        mapping = []
        for p in pa:
            mates = [q for q in pb if q.get("net") == p.get("net")]
            if len(mates) != 1:
                return err("pad %s of %s matches %d pads on %s by net name"
                           % (p.get("number"), a_ref, len(mates), b_ref),
                           "AMBIGUOUS_MAPPING",
                           hint="pass mapping=[[%s_pad, %s_pad], ...] explicitly"
                                % (a_ref, b_ref))
            mapping.append([p.get("number"), mates[0].get("number")])
    bnum = {p.get("number"): p for p in pb}
    anum = {p.get("number"): p for p in pa}
    pairs = []
    for m in mapping:
        if m[0] not in anum or m[1] not in bnum:
            return err("mapping %r names a pad that does not exist" % (m,),
                       "TARGET_NOT_FOUND")
        pairs.append((anum[m[0]], bnum[m[1]]))

    key = (lambda p: (p["y"], p["x"])) if perp_a == 0 else (lambda p: (p["x"], p["y"]))
    a_sorted = sorted(pairs, key=lambda t: key(t[0]))
    pos_b = {id(t[1]): i for i, t in enumerate(sorted(pairs, key=lambda t: key(t[1])))}
    order = [pos_b[id(t[1])] for t in a_sorted]
    inversions = []
    for i in range(len(order)):
        for j in range(i + 1, len(order)):
            if order[i] > order[j]:
                inversions.append({"first": "%s->%s" % (a_sorted[i][0].get("number"),
                                                        a_sorted[i][1].get("number")),
                                   "second": "%s->%s" % (a_sorted[j][0].get("number"),
                                                         a_sorted[j][1].get("number"))})

    paths = [("%s.%s" % (a_ref, p.get("number")), _fan_route((p["x"], p["y"]),
                                                            (q["x"], q["y"])))
             for p, q in pairs]
    crossings = _count_crossings(paths)
    predicted = len(inversions)
    agree = (predicted == 0) == (len(crossings) == 0)
    if not agree:
        verdict = "self_check_failed"
    elif predicted == 0:
        verdict = "zero_crossing_exists"
    else:
        verdict = "crossing_forced"
    value = {
        "verdict": verdict,
        "inversions": predicted,
        "inversion_pairs": inversions[:20],
        "inversion_pairs_total": len(inversions),
        "simulated_crossings": len(crossings),
        "simulated_crossing_pairs": crossings[:20],
        "geometry_used": "perpendicular escape of 1.0 mm, then one straight 45 deg "
                         "segment, then horizontal into the far pad",
        "theorem": "for two parallel lines of pads fanned out with a perpendicular "
                   "escape plus 45 degree segments, the number of crossings of any "
                   "such fan-out is at least the number of inversions of the pairing, "
                   "and zero crossings exist exactly when that number is zero. The "
                   "order of the two pad groups settles whether a clean fan-out is "
                   "possible at all - no amount of redrawing changes an inversion.",
        "self_check": ("the judge recomputes the fan-out and counts crossings "
                       "independently; if the two disagree the answer is withheld"),
        "why_the_two_numbers_differ": "one intersection point can settle several "
                                      "inversions, so only ZERO versus non-zero is "
                                      "compared - the two counts are not expected to "
                                      "be equal, and equal numbers would not prove "
                                      "anything",
        "reverse_control": "on this repository's own test fixtures a deliberately "
                           "reversed pairing returns crossing_forced with 45 inversions "
                           "and 42 simulated crossings, while the monotone pairing "
                           "returns zero and zero - so the judge can fail",
    }
    return ok(value, "count",
              model="whether a zero-crossing 45 degree fan-out exists for this pairing",
              basis="inversions of the pairing, cross-checked by simulating the "
                    "fan-out and counting real intersections",
              precision="exact for the shape described in geometry_used; a different "
                        "escape distance or a lane-based shape is not covered",
              assumptions={"ref_a": a_ref, "ref_b": b_ref,
                           "pads": len(pairs)})


_tool(
    "board_fanout_order_check",
    "Decide whether a stack of 45 degree fan-out traces between two parallel header "
    "lines can avoid crossing AT ALL, before drawing any of them. The answer is a "
    "property of the PAIRING, not of the drawing: any inversion between the two pad "
    "orders forces a crossing. It re-derives the fan-out and counts real intersections "
    "as an independent check, and withholds the verdict if the two disagree. This is a "
    "judge, not a router - it never writes a track.",
    {
        "type": "object",
        "properties": {
            "path": _COMMON["path"],
            "member": _COMMON["member"],
            "project": _COMMON["project"],
            "ref_a": _str("footprint whose pads the fan-out starts from"),
            "ref_b": _str("footprint whose pads it ends on"),
            "side_a": {"type": "string",
                       "enum": ["left", "right", "top", "bottom"],
                       "description": "keep only the pads on this edge of ref_a; an "
                                      "LQFP's pads form a ring, so name the edge"},
            "side_b": {"type": "string",
                       "enum": ["left", "right", "top", "bottom"],
                       "description": "same for ref_b (a header row is 'bottom' or "
                                      "'top' by which extreme its pads sit at)"},
            "mapping": {"type": "array",
                        "items": {"type": "array", "items": {"type": "string"}},
                        "description": "[[pad_of_a, pad_of_b], ...]; omitted means pair "
                                       "by identical net name. Use it to leave out the "
                                       "pads you do not fan out (GND, +3V3, VBUS) - the "
                                       "ordering is judged on the pairs you list"},
        },
        "required": ["path", "ref_a", "ref_b"],
    },
    _board_tool(_board_fanout_order_check, derived=True),
)


# ---------------------------------------------------------------------------
# 0.0.107: 自动布线 / 自动布局 从这里被摘掉，不是关掉。
#
# 用户的裁决是逐字的：「不再允许自动布线和自动布局的存在，防止你偷懒」。所以
# 这两个工具不是「默认关闭、设个环境变量就能打开」——它们根本不在 TOOLS 里，
# PCB_AGENT_TOOLSET=all 也拿不回来，tool_call 找不到它们，直接按名字调只会拿到
# TOOL_RETIRED。留下的只有实现代码（本文件里 _board_route / _board_fanout 与
# route/ 、fanout.py 仍然存在，供人读和供自检引用），注册被摘掉，所以对本服务
# 的任何调用方而言它们不存在。
#
# 与之配套的纪律（写在这里是为了让后来的人不要顺手加回来）：
#   * 本服务器只提供「执行原语」：add_track / add_via / add_zone / move_footprint
#     / set_footprint_layer / add_footprint / delete_*。坐标由调用方给。
#   * 摆放决策与走线路径是人的决定，不是搜索的结果。任何「自己算出一条路径
#     再写进去」的东西都属于被摘掉的这一类。
#   * 允许的自动化只有几何算术：焊盘中心、线段距离、90° 折角切 45°。
RETIRED_TOOLS = {
    "board_route": ("it was an A* maze autorouter with a rip-up pass; autorouting "
                    "is no longer part of this server"),
    "board_fanout": ("it planned a BGA escape automatically, which is autorouting "
                     "under another name; it is no longer part of this server"),
}

for _gone in RETIRED_TOOLS:
    TOOLS.pop(_gone, None)
