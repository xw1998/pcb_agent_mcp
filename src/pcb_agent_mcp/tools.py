"""Tool registry: name -> JSON schema + handler.

Every handler returns the uniform envelope from calc.result.
The schema is the only thing the model sees before calling, so descriptions
must say what the tool will NOT do as clearly as what it does.
"""
import json

from .calc import current as _current
from .calc import creepage as _creepage
from .calc import impedance as _impedance
from .calc import rf as _rf
from .calc import units as _units
from .calc.result import err


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


TOOLS = {}


def _tool(name, description, schema, handler):
    TOOLS[name] = {"description": description, "inputSchema": schema, "handler": handler}


# ---------------------------------------------------------------- units
_tool(
    "unit_convert",
    "Convert a length between PCB units. Needed because EasyEDA Pro stores 0.01 inch "
    "internally while KiCad uses mm: a silent 25.4x error passes every DRC.",
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
    "REQUIRED. Which standard to use. ipc2221 = chart fit (implemented). "
    "ipc2152 = current standard, needs a loaded data table (not yet available). "
    "There is no default: the two disagree by more than 3x and the wrong choice "
    "is a wrong answer, not a smaller one.",
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
    "coating": _bool("conformal coating applied with complete coverage"),
    "safety_standard": _str("REQUIRED. safety standard family, e.g. IEC 60664-1, "
                            "IEC 62368-1. Different families give different distances."),
}


def _safety(kind):
    def handler(a):
        fn = (_creepage.creepage_required if kind == "creepage"
              else _creepage.clearance_required)
        return fn(
            a["voltage_v"], a.get("pollution_degree", 2),
            a.get("material_group", "IIIa"), a.get("insulation", "basic"),
            a.get("altitude_m", 0), a.get("coating", False), a.get("safety_standard"))
    return handler


_tool(
    "creepage_required",
    "Required creepage distance (shortest path ALONG the insulating surface). "
    "No EDA's DRC computes this - a spacing matrix is electrical clearance, not creepage. "
    "FAILS CLOSED: refuses to answer until a verified standard table is loaded, because "
    "a guessed safety distance is worse than no answer.",
    {"type": "object",
     "properties": dict(_SAFE_ARGS),
     "required": ["voltage_v", "safety_standard"]},
    _safety("creepage"),
)

_tool(
    "clearance_required",
    "Required electrical clearance (shortest distance THROUGH air). "
    "FAILS CLOSED for the same reason as creepage_required.",
    {"type": "object",
     "properties": dict(_SAFE_ARGS),
     "required": ["voltage_v", "safety_standard"]},
    _safety("clearance"),
)


# ------------------------------------------------------- capabilities
def _capabilities(_args):
    return {
        "status": "ok",
        "server": "pcb-agent-mcp",
        "layer": "Calc (stateless engineering calculation, no board handle needed)",
        "available": [
            "unit_convert", "width_for_current", "current_capacity",
            "via_current_allowed", "trace_impedance", "width_for_impedance",
            "lambda_over_20",
        ],
        "fail_closed_pending_data": {
            "creepage_required": "needs verified IEC table in data/creepage_tables.json",
            "clearance_required": "needs verified IEC table in data/creepage_tables.json",
            "standard=ipc2152": "needs verified IPC-2152 data table",
        },
        "not_implemented_yet": [
            "Oracle layer (measure THIS board: fits_where / min_clearance / loop_area / "
            "cross_split / pour_connectivity)",
            "Adapters (KiCad .kicad_pcb, EasyEDA Pro .epro)",
            "Differential impedance (coupled microstrip/stripline - needs edge coupling, "
            "not derivable from the single-ended closed form; USB 90 ohm / Ethernet 100 ohm "
            "targets are NOT solved by width_for_impedance)",
            "BGA/thermal via array temperature rise, fuse behaviour, and any electrothermal "
            "coupling (IPC-2152 tables not loaded)",
        ],
        "contract": "every value carries model + basis + precision + assumptions; "
                    "missing basis is treated as invalid",
    }


_tool(
    "calc_capabilities",
    "Self-description: what this server can compute, what it refuses to compute and why, "
    "and what is not implemented yet. Call this first.",
    {"type": "object", "properties": {}},
    _capabilities,
)


def list_tools():
    return [
        {"name": name, "description": spec["description"],
         "inputSchema": spec["inputSchema"]}
        for name, spec in TOOLS.items()
    ]


def call_tool(name, arguments):
    spec = TOOLS.get(name)
    if spec is None:
        return err("unknown tool: " + str(name), "TOOL_NOT_FOUND",
                   hint="call tools/list")
    try:
        return spec["handler"](arguments or {})
    except KeyError as exc:
        return err("missing required argument: %s" % exc, "ARG_MISSING")
    except Exception as exc:  # noqa: BLE001
        return err("tool raised: %s: %s" % (type(exc).__name__, exc), "TOOL_RAISED")


def result_to_text(result):
    return json.dumps(result, ensure_ascii=False, indent=2)
