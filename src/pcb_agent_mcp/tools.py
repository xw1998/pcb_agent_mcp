"""Tool registry: name -> JSON schema + handler.

Every handler returns the uniform envelope from calc.result.
The schema is the only thing the model sees before calling, so descriptions
must say what the tool will NOT do as clearly as what it does.
"""
import json
import math

from . import desktop as _desktop
from . import parts as _parts
from . import lceda_api as _lceda_api
from . import lceda_live as _live
from . import fidelity as _fidelity
from . import oracle as _oracle
from .fidelity import views as _fviews
from .calc import current as _current
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
from .calc import cavity as _cavity
from .calc import electrothermal as _electro
from .calc import filters as _filters
from .calc import magnetics as _magnetics
from .calc import stackup as _stackup
from .fidelity import fill as _fill
from .calc import chain as _chain
from .calc import rules as _rules
from .calc.result import err, ok
from .adapters import FORMATS as _FORMATS
from .adapters import detect_format as _detect_format
from .adapters import open_board as _open_board


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
    "answer as one-sided: no cooling, no latent heat, rectangle converted to a round "
    "wire by equal area, all of which make the real fusing current HIGHER. Beyond "
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
    }, "required": ["width_mm"]},
    lambda a: _current.fusing_current(
        a.get("width_mm"),
        t_s=(a["t_s"] if a.get("t_s") is not None else 1.0),
        copper_oz=(a["copper_oz"] if a.get("copper_oz") is not None else 1),
        thickness_um=a.get("thickness_um"),
        t_ambient_c=(a["t_ambient_c"] if a.get("t_ambient_c") is not None else 25.0),
        delta_t_c=(a["delta_t_c"] if a.get("delta_t_c") is not None else 10.0),
        layer=a.get("layer", "external"),
        standard=a.get("standard")),
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
        "APP_NOT_RUNNING": "start LCEDA Pro and retry. Measured: launched from a "
                           "shell (console inherited) it exits in about 0.5 s with "
                           "rc 0 and no log; launched via explorer.exe (the double-"
                           "click path) it stays up and the pipe appears",
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
    refusal (`WRITE_HAD_NO_EFFECT`) when the design did not move. Every answer
    carries `verified_against_app: false`, because no write has been run against
    the real application in this build.
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
    try:
        res = _write.run(client, args.get("session"), args.get("code"),
                         probe=args.get("probe"),
                         expect=args.get("expect") or spec["expect"],
                         timeout_ms=args.get("timeout_ms"),
                         label=args.get("label") or op)
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
    return ok(
        value={"op": op, "changed": report["changed"],
               "expect": report["expect"], "probe_used": report["probe"]},
        unit="bool",
        model="lceda_pro_create_save_move",
        basis="before/after probe comparison inside the running editor, with the "
              "caller's own snippet in between",
        precision="NOT VERIFIED AGAINST THE APPLICATION. No write has ever been "
                  "performed on a real project by this build, so every claim here is "
                  "about the snippet's OBSERVED EFFECT, not about the vendor API "
                  "having the semantics the caller assumed. A save has no "
                  "observable state and is confirmed only by 'the call returned "
                  "without an error'",
        assumptions={"verified_against_app": False,
                     "not_verified": report["not_verified"],
                     "why_this_op_exists": spec["why"],
                     "the_snippet_is_yours":
                         "this server never synthesises a write call; the vendor's "
                         "method names and argument shapes are unverified in this "
                         "build and a generated guess is exactly the plausible wrong "
                         "answer this project refuses"},
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
    "it is confirmed only by 'the call returned without an error'. Every answer "
    "carries verified_against_app=false: no write has been run against the real "
    "application in this build, so the snippet is yours and the claim is about its "
    "observed effect, never about the vendor API's semantics.",
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
    "rotation and pad count, plus a bbox the model can reason about.",
    {"type": "object",
     "properties": dict({"layer": _str("only components on this copper layer")}, **_COMMON),
     "required": ["path"]},
    _board_tool(lambda b, a: _oracle.components(b, layer=a.get("layer"))),
)

_tool(
    "board_nets",
    "Per net: pad/track/via/zone counts and total routed length. Use it to spot a net "
    "that exists in the netlist but has no copper.",
    {"type": "object", "properties": dict(_COMMON), "required": ["path"]},
    _board_tool(lambda b, a: _oracle.nets(b)),
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

def _ledger_note(kind, board_path, payload):
    """Append one run to the ledger. BEST EFFORT - it cannot change an answer.

    Kept out of every return value on purpose: `ledger.record` swallows its own
    errors and this wrapper never raises, because recording what happened must
    never be able to fail the work that happened.
    """
    try:
        from . import ledger as _ledger
        body = dict(payload)
        body["board"] = _ledger.digest(board_path)
        _ledger.record(kind, body)
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
        out["hint"] = ("nothing at this point needs to move: look further along the "
                       "net (board_route on one net tells you which edge failed)")
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
    limit = args.get("limit")
    limit = 20 if limit is None else int(limit)
    got = _ledger.summary(kind=kind, digest_value=digest_value, limit=limit)
    return ok({"enabled": _ledger.enabled(), "file": got["file"],
               "count": got["count"], "totals": got["totals"],
               "runs": got["runs"],
               "board_digest": (None if digest_value is None
                                else digest_value[:12]),
               "stores_per_line": ["board digest (sha256, first 12 shown)",
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
    "RECORD, not a cache: nothing here is ever reused as a result, and no line "
    "contains design data that could reconstruct the board. Recording is best effort "
    "and never changes what a tool returns; PCB_AGENT_LEDGER=0 disables it entirely. "
    "Read-only. " + _NOT_WRITTEN,
    {"type": "object",
     "properties": dict({
         "kind": _str("only runs of this kind", enum=list(
             ("route", "fanout", "drc", "fab", "apply"))),
         "limit": _num("newest first, at most this many rows (default 20)", 20),
     }, **_COMMON),
     "required": []},
    lambda a: _guarded(lambda: _board_run_ledger(a)),
)

_tool(
    "board_pour_connectivity",
    "Do the pads of the pour's net actually land inside the filled copper? A pour that "
    "looks connected in the file can be isolated in fact.",
    {"type": "object",
     "properties": dict({"zone_index": _num("which zone (0-based)", 0)}, **_COMMON),
     "required": ["path"]},
    _board_tool(lambda b, a: _oracle.pour_connectivity(b, a.get("zone_index", 0)),
                derived=True),
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
    "A fill is a SNAPSHOT: any later track/pad/outline change makes it stale.",
    {"type": "object",
     "properties": dict({
         "in_place": {"type": "boolean",
                      "description": "write KiCad's polygons into this file (default "
                                     "false: measure on a copy, touch nothing)"},
     }, **_COMMON, **{"required": ["path"]})},
    lambda a: _guarded(lambda: _fill.fill(a["path"], in_place=bool(a.get("in_place")))),
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
    for key in ("layers", "nets"):
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
    out = ok(
        value={"nets_total": stats["nets_total"], "nets_routed": stats["nets_routed"],
               "nets_failed": stats["nets_failed"], "ops": len(res["ops"]),
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
        },
        extra={
            "diff_pairs": res["diff_pairs"],
            "impedance_targets": res["rules"]["impedance_targets"],
            "length_groups": res["length_groups"],
            "return_path": res.get("return_path"),
            "nets": res["nets"],
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
    "also need gap_mm. For stripline, dielectric_mm is the trace-to-one-plane "
    "distance, the same convention trace_impedance uses",
    ["microstrip", "stripline", "cpw", "gcpw"])

_ER = _num("substrate relative permittivity", 4.3)
_COPPER_UM = _num("copper thickness in um (0 = ignore thickness)", 0.0)

_tool(
    "feedline_impedance",
    "Z0, effective permittivity, guided wavelength and loss of a microstrip, "
    "stripline, CPW or grounded CPW line. Loss (with freq_ghz + tan_delta) reports "
    "conductor loss from the 2-D field solution on microstrip and stripline - "
    "alpha_c = Rs/(2*Z0)*INT g^2 ds, which carries the trace edge crowding and the "
    "return-current spread - alongside the Rs/(Z0*w) sheet figure, plus an optional "
    "Hammerstad-Jensen copper-roughness multiplier. Coplanar conductor loss is still "
    "refused, not guessed. verify_field=true also reports the closed form's error "
    "against the solver.",
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
            "roughness_um": _num("copper surface roughness Ra in um; multiplies Rs by "
                                 "the Hammerstad-Jensen factor (0 = smooth)", 0.0),
        },
        "required": ["kind", "width_mm", "dielectric_mm"],
    },
    lambda a: _feedline.feedline_impedance(
        a["kind"], a["width_mm"], a["dielectric_mm"], a.get("gap_mm"),
        a.get("copper_um", 0.0), a.get("er", 4.3), a.get("freq_ghz"),
        a.get("tan_delta"), a.get("verify_field", False),
        a.get("field_levels", 1), a.get("roughness_um", 0.0)),
)

_tool(
    "feedline_width",
    "The trace width that gives a target impedance, for microstrip / stripline / CPW "
    "/ GCPW. Bisected on the same forward model feedline_impedance exposes, so the "
    "answer can be re-checked. If the target is outside what a sane width range "
    "reaches, it returns the reachable band as an error instead of a width that "
    "silently misses.",
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
        },
        "required": ["target_ohm", "kind", "dielectric_mm"],
    },
    lambda a: _feedline.feedline_width(
        a["target_ohm"], a["kind"], a["dielectric_mm"], a.get("gap_mm"),
        a.get("copper_um", 0.0), a.get("er", 4.3), a.get("tol_ohm", 1e-4)),
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
                          "decoupling impedance and worst-case anti-resonance), and "
                          "thermal (1-D stacks, via arrays, a real 2-D plate solve "
                          "with an energy-balance check, RC transients, heatsink "
                          "budget). Every model is lumped or closed-form: no "
                          "full-wave, no thermal radiation, no convection field solve",
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
                              "comes out below ambient is refused as non-physical. DC only, "
                              "no skin effect, no AC loss",
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
                         "NOT done: assembly compliance, solder-joint creep, "
                         "random+shock combined, drop, and any nonlinear contact",
            "magnetics": "power-inductor sizing for boost / buck / buck-boost: the CCM "
                         "design point (duty, inductance from volt-seconds, peak / "
                         "valley / RMS current, the load at which CCM ends, winding "
                         "loss if a DCR is supplied), the DCM steady state (a "
                         "different duty with a diode interval, refused when the load "
                         "is really in CCM) and Steinmetz core loss with the caller's "
                         "k/alpha/beta and a NAMED unit convention. The ripple ratio is "
                         "the caller's design choice; saturation is graded only when a "
                         "part Isat is supplied; no core selection and no material "
                         "table lives here",
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
            "write_schematic": "KiCad .kicad_sch only: propose (compute only) -> apply "
                               "(checkpoint, atomic write, four gates). The referee is "
                               "the netlister + ERC, not DRC: a schematic's product is "
                               "connectivity, and two coordinates being right does not "
                               "prove two wires are joined",
        },
        "available": [
            "unit_convert", "width_for_current", "current_capacity",
            "via_current_allowed", "fusing_current",
            "trace_impedance", "width_for_impedance",
            "diff_impedance", "diff_impedance_field", "diff_width", "lambda_over_20",
            "board_summary", "board_components", "board_nets", "board_clearance",
            "board_free_space", "board_routability", "board_pour_connectivity",
            "board_zones", "board_fill_zones", "board_route",
            "board_cross_split",
            "board_loop_area", "board_fidelity", "board_view", "board_drc",
            "board_stats", "board_cross_check", "epro_inspect",
            "safety_table_check",
            "board_ops", "board_create", "board_propose", "board_apply",
            "board_checkpoints", "board_restore", "board_progress",
            "schematic_ops", "schematic_propose", "schematic_apply",
            "schematic_summary", "schematic_view",
            "part_library_status", "part_search", "part_get", "part_export",
            "part_footprint_pads", "part_cache_info",
            "app_detect", "app_status", "app_launch", "app_close", "app_open_project",
            "lceda_api_probe",
            "lceda_pro_status", "lceda_pro_api", "lceda_pro_open",
            "lceda_pro_sessions", "lceda_pro_close", "lceda_pro_run",
            "lceda_pro_steps",
            "feedline_impedance", "feedline_width",
            "antenna_patch", "antenna_printed", "antenna_dipole_monopole",
            "antenna_helix", "antenna_small_loop", "antenna_q_limit", "antenna_chip",
            "match_reflection", "match_l", "match_pi", "match_t", "match_stub",
            "match_quarter_wave", "match_bandwidth",
            "link_friis", "link_max_range", "link_aperture", "link_far_field",
            "link_fresnel",
            "board_antenna_keepout",
            "magnetics_inductor", "magnetics_dcm", "magnetics_core_loss",
            "stackup_material", "stackup_design",
            "rf_chain", "design_rules", "layout_review", "schematic_review",
            "sim_spice", "trace_resistance", "via_resistance", "trace_inductance",
            "ir_drop_chain", "pdn_impedance", "pdn_plane_modes", "decoupling_check",
            "thermal_stack", "thermal_via_array", "thermal_plate",
            "thermal_transient", "thermal_heatsink", "thermal_convection",
            "board_ir_drop",
            "dfm_capability", "dfm_check",
            "vibration_plate_modes", "vibration_plate_stress",
            "vibration_random_response",
            "vibration_fatigue", "vibration_shock", "vibration_isolator",
            "board_vibration",
            "cavity_resonance", "cavity_via_fence", "cavity_plane_q",
            "electrothermal_trace", "electrothermal_conductor",
            "board_electrothermal",
            "creepage_required", "clearance_required", "spacing_required",
            "filter_prototype", "filter_lumped_bandpass",
            "filter_mc_yield",
            "filter_coupled_microstrip", "filter_coupled_resonator",
            "filter_waveguide_cavity",
            "fullwave_solver",
            "board_fab_package", "board_export", "board_bom", "board_netlist",
            "board_graphics",
            "schematic_pcb_diff", "schematic_pcb_sync",
            "board_blockers", "board_run_ledger", "board_fanout",
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
            "Power magnetics beyond the electrical design point: no core SELECTION "
            "and no material table (core_loss_steinmetz takes k/alpha/beta from you "
            "and names the unit convention, because the same material fitted by two "
            "vendors gives two triples), no saturation curve of a real material "
            "(only a single Isat number), no AC winding resistance (skin and "
            "proximity - the DCR loss is the DC part only), no gapped-core fringing "
            "loss, no DC-bias Steinmetz, no loop compensation and no soft start. The "
            "tool answers the electrical design point and refuses the parts question "
            "unless an Isat is supplied",
            "Layout and schematic reviews do not read the design for you: they grade "
            "the facts they are given. board_* / schematic_* can produce some of those "
            "facts (clearance, nets, components), but nothing here parses a netlist "
            "into 'this IC's power pins', so sch.* facts must be counted by the "
            "caller - and every rule that was not measured says so instead of "
            "returning a pass",
            "LCEDA Pro write path: the record layout, the mil unit and the "
            "'a save rewrites one member' shape are measured against the 194 .epro "
            "archives in LCEDA-Pro/projects-recovery, and the gates prove the bytes. "
            "What is NOT claimed: that the application accepts a written file - no "
            "written archive was re-opened in LCEDA Pro, so every apply says so in "
            "not_verified. Appending a record the vendor was never seen writing is "
            "reported as verified_by_app=false",
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
            "The live bridge's unverified half (measured 2026-10-28 on 4.1.60): reads "
            "are proven - ping, open, session.list, session.close, doc.api and invoke "
            "were all run against the real editor (see docs/24). NOT proven: any "
            "WRITE. No save, no create, no move, no delete was performed on any "
            "project, so whether a snippet's save is accepted by the app (and whether "
            "the resulting file is what the editor would have written itself) stays "
            "open, and lceda_pro_run says in every answer that the snippet, not the "
            "tool, decides what happens. Also not proven: that open leaves the "
            "project bytes untouched (they were not compared before and after), and "
            "whether the vendor's own '--mcp' transport can be reached at all - this "
            "build's executable rejects the flag before the app sees it",
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
            "DFM rules the fab states that this layer cannot measure: solder-mask "
            "slivers and mask-defined pitch, silkscreen line width / text height, "
            "impedance-controlled stackup sign-off (the fab's coupon is the verdict), "
            "process tolerances (hole position, plating and copper weight, board "
            "thickness), panelisation / tooling rails / V-cut / fiducials, and "
            "copper-to-finished-hole-wall spacing with plating build-up. They are "
            "listed in every dfm_check answer as not_checked_by_design. Also NOT "
            "here: any built-in fab capability table - "
            "dfm_capability REJECTS a document without a source and does not fill "
            "the gaps with typical values",
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
            "PMC-wall TM_mn0 eigenvalues, cavity_via_fence gives pitch/lambda_g and "
            "cavity_plane_q gives the copper + dielectric Q (Qc = h/delta_s, "
            "Qd = 1/tan_delta). Still missing: radiation Q, surface roughness, "
            "detuning from real open edges, plane-pair impedance and leakage through "
            "voids - so the sum of the two modelled losses is an UPPER bound on Q, and "
            "the dominant loss of a real PDN resonance (the caps and packages mounted "
            "across it) is outside. Even with Q there is no verdict: a resonance is a "
            "problem only together with an excitation and a coupling. When that "
            "distinction decides the design, use a 3-D field solver",
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
            "D Q M with .op/.tran/.ac/.model/.temp and rejects F H J K S W X B T O U A "
            "cards with the card named in the error. Who wants the rest: install "
            "ngspice and the same tool can hand the deck to it (engine='ngspice', or "
            "'auto' once a binary is on PATH / in PCB_AGENT_NGSPICE), which covers "
            "subcircuits, behavioural sources, switches, transmission lines and the "
            "real device models. That path is NOT bundled: the binary is not shipped "
            "with this server. The delegation WAS exercised against real ngspice-47 "
            "(MSYS2 Windows build) and cross-checked node by node with the builtin "
            "solver, and then a SECOND build (ngspice-41, conda-forge) was run on the "
            "same decks so the two engines can be compared to each other: .op and .ac "
            "agree bit for bit, .tran differs by at most 1.24e-4 V on a 1 V step "
            "(different internal step control, so their grids differ too - that number "
            "is not an accuracy ranking); verified_against_engine is true only for the "
            "versions in this "
            "build's VERIFIED_ENGINES list, and false for any other version - a "
            "different binary is not a verified engine. What neither path "
            "does: temperature sweeps (one temperature per run), noise (.noise), "
            "distortion, Monte Carlo; the builtin solver also uses dense LU and "
            "refuses above ~400 unknowns",
            "Electrothermal coupling stops at a 2-D laminate plate: "
            "electrothermal_trace / electrothermal_conductor close the loop against one "
            "caller-supplied Rth; board_electrothermal closes it against a 2-D field, "
            "but that field spreads heat through the laminate k the caller gives, NOT "
            "through the real copper pours and planes. Not done: 3-D, radiation, "
            "thermal vias, component thermal mass and contact resistance, AC loss "
            "(skin/proximity), device self-heating inside sim_spice, and reading the "
            "conductors straight off a board file - board_electrothermal takes geometry, "
            "it does not open the .kicad_pcb for you",
            "Surge and fusing beyond one pulse: fusing_current answers the single-pulse "
            "adiabatic question (Onderdonk 1928) and states its four one-sided "
            "assumptions (no cooling, no latent heat, equal-area rectangle, no skin "
            "effect). Not done: repetitive pulses with duty-cycle derating, "
            "electromigration (Black's equation needs a current density a fab will "
            "qualify), arc/plasma behaviour after the copper opens, and board_ir_drop's "
            "A/mm^2 verdict - that tool still reports density with no criterion "
            "attached",
            "The plane impedance CURVE (as opposed to its mode peaks): pdn_impedance / "
            "decoupling_check are lumped RLC in parallel and can prove a bank is clearly "
            "insufficient but not that it is sufficient; pdn_plane_modes gives the peak "
            "|Z| at each cavity mode inside the band, but the modal SUM is not computed "
            "because it does not converge (it moves with the truncation order), so no "
            "anti-resonance between the plane and the caps is claimed. Die-package "
            "inductance, split planes and via-barrel inductance are all outside",
            "Thermal plating thinning, TIM interface resistance and heatsink "
            "selection: thermal_via_array reports an OPTIMISTIC barrel resistance "
            "(uniform plating), rth_cs is an input the user must supply, and "
            "thermal_heatsink returns the BUDGET - there is deliberately no heatsink "
            "table in this server",
            "Vibration is a THIN-PLATE, SINGLE-MODE, LINEAR layer, by name: no shear "
            "deformation or rotary inertia (so a thick or short plate reads too "
            "stiff), no component mass or copper stiffening (so f1 reads high on a "
            "board with one heavy part), no compliance in the mount itself, no "
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
            "Filter synthesis stops at the CIRCUIT, by name: filter_lumped_bandpass gives L/C values with its own S21 audited two ways; finite-Q dissipation and first-order parasitics (inductor cp, capacitor esl) are available but OFF unless asked for, and the loss model is a resistor per branch, not dielectric or conductor physics - no skin effect, no substrate loss, no temperature drift. filter_mc_yield does tolerance yield, but it is uniform and independent only (no correlation, no batch shift, no ageing), so it is a worst case whose confidence half-width grows as 1/sqrt(n_draws). filter_coupled_microstrip / filter_coupled_resonator are QUASI-STATIC and non-dispersive: no T-junctions, no stepped-impedance discontinuities, no cross-coupling between non-adjacent resonators (so stopband nulls are not modelled), no finite substrate/ground-plane size, no radiation Q, and no layout is generated - the w/s/lengths are geometry to draw, not a routed board. filter_waveguide_cavity is TE101 only: no higher-order modes, no mode chart, no iris or probe coupling geometry (a coupling iris must be solved by a full-wave solver), no surface-roughness correction. Nothing here is an EM simulator: a filter that must meet a mask needs the EM run, and these numbers are the starting point handed to it",
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


def _board_view(args):
    doc, e = _load_fidelity(args)
    if e:
        return e
    kind = args.get("kind") or "all"
    limit = int(args.get("limit") or 200)
    from .fidelity import kicad_doc as _kdoc
    if isinstance(doc, _kdoc.KiCadDocument):
        view = _fviews.BoardView(doc)
        if kind == "all":
            data = view.facts()
            return {"status": "ok", "format": "kicad_pcb", "facts": data,
                    "not_written": _NOT_WRITTEN,
                    "note": "every record carries `raw`, the complete S-expression "
                            "it was read from"}
        if kind not in _VIEW_KINDS:
            return err("unknown kind %r" % kind, "ARG_INVALID",
                       hint="one of: " + ", ".join(_VIEW_KINDS))
        items = getattr(view, kind)()
        if isinstance(items, list):
            total = len(items)
            return {"status": "ok", "kind": kind, "total": total,
                    "returned": min(total, limit), "truncated": total > limit,
                    "items": items[:limit], "not_written": _NOT_WRITTEN}
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
    "with no reading rule yet. Every record carries `raw` - the complete original "
    "S-expression - so nothing is lost between the file and the answer.",
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
     },
     "required": ["path"]},
    lambda a: _guarded(lambda: _board_view(a)),
)

_tool(
    "board_drc",
    "AUTHORITATIVE design-rule check: runs KiCad's own DRC engine (kicad-cli) and returns "
    "its verdict - violations by severity and type, and unconnected items. This is not "
    "this server's geometry opinion; when it disagrees with board_clearance, believe this "
    "one. Read-only: the report goes to a temp directory, never beside your project.",
    {"type": "object",
     "properties": {
         "path": _str("absolute path to a .kicad_pcb"),
         "severity": _str("which violations to include", ["all", "error"], "all"),
         "strict": _bool("also report warnings as blocking (the write gate's strict "
                         "mode)"),
         "limit": _num("how many blocking/advisory entries to list", 20),
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
    out = _engine.drc(path, severity=args.get("severity") or "all")
    if out.get("status") != "ok":
        return out
    from .edit import drc_policy as _policy
    view = _policy.classify(out.get("violations") or [],
                            strict=bool(args.get("strict")),
                            limit=int(args.get("limit") or 20))
    out["blocking_if_this_were_an_edit"] = view
    out["how_an_apply_treats_this"] = (
        "board_apply compares the DRC of the file before and after the edit and blocks "
        "on NEW violations at KiCad severity 'error' only; warnings are reported and "
        "counted (they were 69/69 on the STM32F401 core board, all silkscreen/text). "
        "Pass options.strict_drc=true to make every new violation block.")
    return out


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
    out = err(str(exc), getattr(exc, "code", "EDIT_FAILED"))
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
    from . import edit as _edit
    try:
        return _edit.apply_plan(args.get("plan_id"), args.get("confirm") is True,
                                args.get("options") or {})
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

# ---------------------------------------------------------------- simulation
_tool(
    "sim_spice",
    "Run a real circuit simulation on a SPICE netlist: DC operating point (.op), transient "
    "(.tran) and small-signal AC (.ac). The answer comes from the BUILTIN stdlib solver "
    "(Newton + junction voltage limiting + gmin stepping, per-element voltage/current/"
    "power reported back) or, if an ngspice binary is installed, from ngspice itself - "
    "see the engine parameter. The builtin solver takes R C L V I E G D Q M and REJECTS "
    "X/F/H/K/S/W/B/T/O/U/A BY NAME, never silently; with ngspice the deck is passed "
    "through, so those cards work, but the per-element report is then absent (this "
    "server will not re-derive it and present its own arithmetic as the engine's). "
    "A deck this server's own parser cannot read is reported with netlist_parsed:false "
    "and netlist_parse_note instead of being quietly treated as a full parse.",
    {"type": "object", "properties": {
        "netlist": _str("SPICE netlist text, one card per line; a leading * is a comment. "
                        "The first line is not skipped unless it starts with * - write "
                        "a title line beginning with * to be safe"),
        "analysis": _str("override or omit the .op/.tran/.ac) card: e.g. "
                         "'.tran 0.1m 5m' or '.ac dec 20 1 1meg'"),
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
    "current capacity is a separate question with a temperature-rise criterion.",
    {"type": "object", "properties": {
        "drill_mm": _num("finished hole diameter"),
        "plating_um": _num("barrel wall thickness (typically 20..25 um)"),
        "length_mm": _num("barrel length = board thickness"),
        "temp_c": _num("copper temperature", 20),
        "count": _num("how many vias in parallel", 1),
    }, "required": ["drill_mm", "plating_um", "length_mm"]},
    lambda a: _power.via_resistance(a.get("drill_mm"), a.get("plating_um"),
                                    a.get("length_mm"),
                                    temp_c=a.get("temp_c") or 20,
                                    count=int(a.get("count") or 1)),
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
    "pdn_plane_modes",
    "Peak |Z| at each parallel-plate (cavity) mode of a plane pair, from the single mode "
    "that rings: |Z_peak| = w*mu0*t*psi_mn(r1)*psi_mn(r2)*Q/k_mn^2 with Q = 1/tan_delta. "
    "Modes with a voltage null at either point are reported as uncoupled instead of as a "
    "tiny number. Only peaks are given - the SUM over modes does not converge near and "
    "above the first resonance (measured: |Z| at 1.45 GHz moves 5.6 -> 9.4 ohm when the "
    "truncation goes from order 10 to 40), so no curve and no anti-resonance is claimed. "
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
    "temperature rise for a stated power. Strictly 1-D - it cannot see spreading, which is "
    "why thermal_plate exists.",
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
    "parallel, which is what a real via field looks like. An OPTIMISTIC number: plating is "
    "thinner mid-barrel.",
    {"type": "object", "properties": {
        "drill_mm": _num("finished hole diameter"),
        "plating_um": _num("barrel wall thickness"),
        "length_mm": _num("board thickness"),
        "count": _num("number of vias", 1),
        "pitch_mm": _num("via pitch, to add the FR4 cell in parallel"),
        "k_board": _num("board dielectric conductivity", 0.3),
    }, "required": ["drill_mm", "plating_um", "length_mm"]},
    lambda a: _thermal.via_array_thermal(a.get("drill_mm"), a.get("plating_um"),
                                         a.get("length_mm"),
                                         count=int(a.get("count") or 1),
                                         pitch_mm=a.get("pitch_mm"),
                                         k_board=a.get("k_board") or 0.3),
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
        "safety_margin": _num("fraction of the budget you are willing to use", 0.8),
    }, "required": ["power_w", "t_j_max_c", "t_ambient_c", "rth_jc_k_per_w"]},
    lambda a: _thermal.heatsink_select(
        a.get("power_w"), a.get("t_j_max_c"), a.get("t_ambient_c"),
        a.get("rth_jc_k_per_w"),
        rth_cs_k_per_w=(a["rth_cs_k_per_w"] if a.get("rth_cs_k_per_w") is not None
                        else 0.0),
        safety_margin=(a["safety_margin"] if a.get("safety_margin") is not None
                       else 0.8)),
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
    "via plating is unknown, or when no track connects a sink to the source.",
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
    }, **_COMMON),
     "required": ["path", "net", "source", "sinks"]},
    _board_tool(lambda b, a: _oracle.board_ir_drop(
        b, a.get("net"), a.get("source"), a.get("sinks"),
        current_a=a.get("current_a"), copper_um=a.get("copper_um"),
        plating_um=a.get("plating_um"),
        quantize_mm=(a["quantize_mm"] if a.get("quantize_mm") is not None else 0.01),
        temp_c=(a["temp_c"] if a.get("temp_c") is not None else 20.0)), derived=True),
)

_tool(
    "dfm_capability",
    "Validate a process capability document on its own, before any board is opened: which "
    "known rules it states, which it leaves unanswered, and whether it is usable at all. "
    "An unknown rule key is REJECTED (a typo would otherwise read as 'not stated'), and a "
    "document with no `source` is REJECTED - an unattributed limit cannot be told apart "
    "from an invented one. This server ships NO fab table on purpose.",
    {"type": "object", "properties": {
        "capability": {"type": "object", "description":
                       '{"fab": "...", "source": "where the numbers came from", '
                       '"verified": false, "rules": {"min_track_width_mm": 0.127, ...}}. '
                       "Rule keys: %s" % ", ".join(sorted(_dfm.RULES))},
    }, "required": ["capability"]},
    lambda a: _dfm.validate(a.get("capability")),
)

_tool(
    "dfm_check",
    "Compare THIS board against a supplied fab capability document: narrowest track, "
    "copper-to-copper gap, via drill and annular rings, plated-pad rings, hole-to-hole web, "
    "copper-to-edge, narrowest cut-out, board width/height and copper layer count. The "
    "limits are the document's, the measurements are the board's, and every rule the "
    "document leaves out is reported as UNANSWERED rather than passed. " + _NOT_WRITTEN,
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
    "points you name.",
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
        stress_amplitude_mm=a.get("stress_amplitude_mm")),
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
    "frequency moves 0.04 % between degree 5 and 9 while the stress moves 3.6 %.",
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
    }, "required": ["a_mm", "b_mm", "thickness_mm", "amplitude_mm"]},
    lambda a: _vib.plate_stress(
        a.get("a_mm"), a.get("b_mm"), a.get("thickness_mm"), a.get("amplitude_mm"),
        mode=int(a.get("mode") or 1),
        material=a.get("material"), e_gpa=a.get("e_gpa"), nu=a.get("nu"),
        rho_kg_m3=a.get("rho_kg_m3"),
        mount=(a.get("mount") or "corners"), bc_x=a.get("bc_x"),
        bc_y=a.get("bc_y"),
        poly_degree=(int(a["poly_degree"]) if a.get("poly_degree") is not None else None),
        degrees=(tuple(a["degrees"]) if a.get("degrees") else None)),
)

_tool(
    "vibration_random_response",
    "Single-mode (Miles) random vibration: the g_rms a mode sees under a PSD, and the "
    "displacement that follows. A flat PSD uses Miles' closed form "
    "g_rms = sqrt(pi/2 * f_n * Q * W); a measured PSD curve is integrated as "
    "int W(f)|H(f)|^2 df over three panels so a high-Q peak is actually resolved. The two "
    "agree inside 1 % on a flat PSD. Single mode only - does not sum the other modes.",
    {"type": "object", "properties": {
        "f_n_hz": _num("natural frequency (from vibration_plate_modes)"),
        "psd": {"type": "object", "description":
                '{"g2_per_hz": 0.1} or {"points": [[hz, g2/Hz], ...]}'},
        "zeta": _num("damping ratio"),
        "q_factor": _num("Q = 1/(2 zeta); give this OR zeta"),
        "gamma": _num("peak factor, default 3 (3 sigma)", 3),
    }, "required": ["f_n_hz", "psd"]},
    lambda a: _vib.miles_response(
        a.get("f_n_hz"), psd=a.get("psd"), zeta=a.get("zeta"),
        q_factor=a.get("q_factor"),
        gamma=(a["gamma"] if a.get("gamma") is not None else 3.0)),
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
    "board_vibration",
    "Natural frequencies of THIS board from its own outline and stackup, with every "
    "component sampled against the first mode shape so you get a ranked list of which "
    "references sit on an antinode. Uses the outline bounding box as the plate, so a "
    "non-rectangular board comes out SOFTER (f1 biased low) - the fill fraction is "
    "reported so you can see how much. Component mass and copper stiffening are not "
    "modelled.",
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
    "dominates and a scary mode on lossy prepreg is often a non-event. Radiation Q, "
    "surface roughness and the damping of mounted capacitors are NOT modelled, so "
    "read the answer as an upper bound on Q. No pass/fail: a resonance is only a "
    "problem with an excitation and a coupling.",
    {"type": "object", "properties": {
        "f_hz": _num("the mode frequency, e.g. from cavity_resonance"),
        "h_mm": _num("dielectric thickness between the two planes"),
        "tan_delta": _num("laminate loss tangent, e.g. 0.02 for FR4 (omit for none)"),
        "conductivity_s_m": _num("copper conductivity", 5.8e7),
    }, "required": ["f_hz", "h_mm"]},
    lambda a: _cavity.plane_q(
        a.get("f_hz"), a.get("h_mm"), tan_delta=a.get("tan_delta"),
        conductivity_s_m=(a["conductivity_s_m"]
                          if a.get("conductivity_s_m") is not None else 5.8e7)),
)

_tool(
    "electrothermal_trace",
    "Self-consistent temperature of a straight DC trace: the copper gets hotter, the "
    "copper gets more resistive, so it dissipates more, so it gets hotter. R(T) = "
    "rho20(1+alpha(T-20))L/(w t), P = I^2 R, T = T_amb + P*Rth, solved as a fixed point "
    "with the contraction gain dP/dT*Rth checked exactly - above 1 it returns "
    "THERMAL_RUNAWAY instead of a number. Rth is REQUIRED (one lump: from thermal_stack / "
    "via_array_thermal / plate_temperature, or measured); this module owns no default Rth. "
    "DC only.",
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
    "uniform plate that reduces to the 1-D rule P*alpha < 2hA. Spreading is through the "
    "laminate k you pass, NOT through the real copper pours, so a design with a plane "
    "under the trace runs cooler than this says.",
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
    }, "required": ["width_mm", "height_mm", "thickness_mm", "k_w_mk", "h_w_m2k"]},
    lambda a: _electro.board_electrothermal(
        a.get("width_mm"), a.get("height_mm"), a.get("thickness_mm"),
        a.get("k_w_mk"), a.get("h_w_m2k"), a.get("conductors"),
        t_ambient_c=(a["t_ambient_c"] if a.get("t_ambient_c") is not None else 25.0),
        cell_mm=a.get("cell_mm"), edge_temp_c=a.get("edge_temp_c"),
        uniform_power_w=a.get("uniform_power_w"),
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
    "Rectangular waveguide cavity in the TE101 mode: resonant length for a given a and b, "
    "unloaded Q from wall loss computed TWO independent ways (closed-form bracket vs a "
    "numerical |H_tan|^2 surface integral over all six walls - they must agree within 2 % "
    "or the answer is refused), and R/Q. Only TE101: no higher-order modes, no mode chart, "
    "no iris/coupling geometry.",
    {"type": "object", "properties": {
        "f0_hz": _num("target resonant frequency in Hz"),
        "a_mm": _num("broad-wall width a"),
        "b_mm": _num("narrow-wall height b (default a/2)"),
        "conductivity_s_m": _num("wall conductivity, default copper 5.8e7", 5.8e7),
        "mu_r": _num("relative permeability of the fill", 1.0),
        "er": _num("relative permittivity of the fill", 1.0),
        "mode": _str("cavity mode", ["TE101"]),
    }, "required": ["f0_hz", "a_mm"]},
    lambda a: _filters.waveguide_cavity(
        a.get("f0_hz"), a.get("a_mm"),
        b_mm=a.get("b_mm"),
        conductivity_s_m=(a["conductivity_s_m"]
                          if a.get("conductivity_s_m") is not None else 5.8e7),
        mu_r=(a["mu_r"] if a.get("mu_r") is not None else 1.0),
        er=(a["er"] if a.get("er") is not None else 1.0),
        mode=a.get("mode") or "TE101"),
)

_tool(
    "magnetics_inductor",
    "Power-inductor sizing for CCM boost / buck / buck-boost from the load, the rails "
    "and the switching frequency: duty, inductance, peak / valley / RMS current, where "
    "CCM ends, and the winding's I^2R loss if you supply dcr_ohm. Ripple ratio r is a "
    "DESIGN CHOICE (0.2-0.4 typical) - it is not derived. Saturation is graded only if "
    "you supply the part's Isat; without it the answer says what is missing instead of "
    "guessing. No core selection and no core loss here (magnetics_core_loss takes your "
    "own Steinmetz triple); a load BELOW the CCM boundary is a different problem - use "
    "magnetics_dcm, where the duty is not 1-Vin/Vout.",
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
    "Steinmetz core loss P_v = k*f^alpha*B_peak^beta with the coefficients YOU supply "
    "and the unit convention NAMED, because the published fits differ by ~10^12 in k "
    "purely through units (si = Hz/T/W-per-m3, khz_mt = kHz/mT/mW-per-cm3, khz_gauss). "
    "Both unit systems are printed on every answer so a unit slip shows up as an "
    "absurd magnitude. B_peak is the AC amplitude (half the peak-to-peak swing). Not "
    "modelled: DC bias, non-sinusoidal flux, temperature dependence, gap loss - and "
    "nothing about the winding, which is a separate number.",
    {"type": "object", "properties": {
        "f_hz": _num("frequency in Hz"),
        "b_peak_t": _num("AC flux amplitude in tesla"),
        "k": _num("the material's Steinmetz coefficient, in the units named below"),
        "alpha": _num("the material's frequency exponent"),
        "beta": _num("the material's flux exponent"),
        "units": _str("which convention k is in", sorted(_magnetics.STEINMETZ_UNITS)),
        "ve_mm3": _num("optional: core volume, to turn P_v into watts"),
    }, "required": ["f_hz", "b_peak_t", "k", "alpha", "beta"]},
    lambda a: _magnetics.core_loss_steinmetz(
        a.get("f_hz"), a.get("b_peak_t"), a.get("k"), a.get("alpha"), a.get("beta"),
        units=(a.get("units") or "si"), ve_mm3=a.get("ve_mm3")),
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
    "is echoed back. dxf writes one file per layer.",
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
