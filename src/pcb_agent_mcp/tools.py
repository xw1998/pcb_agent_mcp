"""Tool registry: name -> JSON schema + handler.

Every handler returns the uniform envelope from calc.result.
The schema is the only thing the model sees before calling, so descriptions
must say what the tool will NOT do as clearly as what it does.
"""
import json

from . import desktop as _desktop
from . import lceda_api as _lceda_api
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
from .calc.result import err
from .adapters import FORMATS as _FORMATS
from .adapters import detect_format as _detect_format
from .adapters import open_board as _open_board


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
    "stripline, CPW or grounded CPW line. verify_field=true also runs the 2-D field "
    "solver on the same cross-section and reports the closed form's error, so the "
    "number can be checked rather than trusted.",
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
        },
        "required": ["kind", "width_mm", "dielectric_mm"],
    },
    lambda a: _feedline.feedline_impedance(
        a["kind"], a["width_mm"], a["dielectric_mm"], a.get("gap_mm"),
        a.get("copper_um", 0.0), a.get("er", 4.3), a.get("freq_ghz"),
        a.get("tan_delta"), a.get("verify_field", False),
        a.get("field_levels", 1)),
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
            "desktop": "find / start / stop the EDA and open a project, verified by "
                       "window title - never touches a project file",
            "write": "propose (compute only, no byte is written) -> apply (confirm=true, "
                     "checkpoint, atomic write, five independent gates, rollback on any "
                     "failure). KiCad edits are re-judged by kicad-cli; a new DRC "
                     "violation blocks the write",
            "write_schematic": "KiCad .kicad_sch only: propose (compute only) -> apply "
                               "(checkpoint, atomic write, four gates). The referee is "
                               "the netlister + ERC, not DRC: a schematic's product is "
                               "connectivity, and two coordinates being right does not "
                               "prove two wires are joined",
        },
        "available": [
            "unit_convert", "width_for_current", "current_capacity",
            "via_current_allowed", "trace_impedance", "width_for_impedance",
            "diff_impedance", "diff_impedance_field", "diff_width", "lambda_over_20",
            "board_summary", "board_components", "board_nets", "board_clearance",
            "board_free_space", "board_pour_connectivity", "board_cross_split",
            "board_loop_area", "board_fidelity", "board_view", "board_drc",
            "board_stats", "board_cross_check", "epro_inspect",
            "safety_table_check",
            "board_ops", "board_create", "board_propose", "board_apply",
            "board_checkpoints", "board_restore",
            "schematic_ops", "schematic_propose", "schematic_apply",
            "schematic_summary", "schematic_view",
            "app_detect", "app_status", "app_launch", "app_close", "app_open_project",
            "lceda_api_probe",
            "feedline_impedance", "feedline_width",
            "antenna_patch", "antenna_printed", "antenna_dipole_monopole",
            "antenna_helix", "antenna_small_loop", "antenna_q_limit", "antenna_chip",
            "match_reflection", "match_l", "match_pi", "match_t", "match_stub",
            "match_quarter_wave",
            "link_friis", "link_max_range", "link_aperture", "link_far_field",
            "link_fresnel",
            "board_antenna_keepout",
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
            "cavity_resonance", "cavity_via_fence",
            "electrothermal_trace", "electrothermal_conductor",
            "board_electrothermal",
            "creepage_required", "clearance_required", "spacing_required",
            "filter_prototype", "filter_lumped_bandpass",
            "filter_coupled_microstrip", "filter_coupled_resonator",
            "filter_waveguide_cavity",
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
                "board_cross_split", "board_loop_area", "board_antenna_keepout",
                "board_ir_drop", "board_vibration"],
            "authoritative (vendor engine said it)": ["board_drc", "board_stats"],
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
            "EasyEDA Pro live API drive: the websocket client (RFC 6455 handshake, "
            "frames, the 49620-49629 port scan) is implemented and unit-tested, but no "
            "gateway answered on this machine (the port only opens after the user "
            "enables 外部交互权限), so nothing was driven end to end - every reply "
            "carries verified_against_app=false. What the install DOES contain, "
            "measured 2026-10-28: resources/app/assets/pro-api/0.3.38.25cc6d44 "
            "(api.js + api.d.ts + extension.json declaring the default/sch/symbol/pcb/"
            "panel services, 378 types by its own help CLI, PCB_Document 21 methods, "
            "SCH_Document 9, PNL_Document 1). That is an IN-PROCESS extension API, not "
            "a transport, so it does not make an out-of-process call possible - it only "
            "means the vendor has a stable programmatic surface to target",
            "LCEDA Pro launch: lceda-pro.exe exits in about 0.5 s when started "
            "programmatically here (rc 0 with no arguments, rc 1 with a project "
            "path), no window and no user-data write - so open_argv_verified stays "
            "false and app_open_project reports unconfirmed instead of pretending. "
            "A second, better-instrumented round (2026-10-28) located the layer: the "
            "exe never reaches Electron - the app's own log "
            "(Documents\\LCEDA-Pro\\logs\\warn\\<date>.log) and the Chromium "
            "debug.log get ZERO new lines, the process peaks at ~34 MB and dies with "
            "rc 0, and '--enable-logging=stderr' is rejected by a launcher shell with "
            "rc 9 before Chromium sees argv. kicad.exe starts fine from the same "
            "shell, so this is not 'no GUI here'",
            "Full-wave electromagnetic simulation (method of moments / FDTD / FEM): "
            "nothing here is a solver. The antenna tools give the starting geometry "
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
            "PMC-wall TM_mn0 eigenvalues and cavity_via_fence gives pitch/lambda_g, but "
            "no Q (dielectric tan-delta, copper loss, radiation), no detuning from real "
            "open edges, no plane-pair impedance and no leakage through voids. A "
            "resonance with no Q is a location on the frequency axis, not a verdict - "
            "when it matters, use a 3-D field solver",
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
            "Transient current density / surge and fusing: current_capacity is a "
            "steady-state table check, and board_ir_drop reports A/mm^2 with no "
            "criterion attached. Fusing a trace needs a temperature-rise criterion and "
            "an adiabatic energy calculation, neither of which is here",
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
            "Filter synthesis stops at the CIRCUIT, by name: filter_lumped_bandpass gives ideal lossless L/C values and its own S21, but no finite-Q insertion loss, no component parasitics, no real capacitor/inductor series resonance and no tolerance or Monte-Carlo yield. filter_coupled_microstrip / filter_coupled_resonator are QUASI-STATIC and non-dispersive: no T-junctions, no stepped-impedance discontinuities, no cross-coupling between non-adjacent resonators (so stopband nulls are not modelled), no finite substrate/ground-plane size, no radiation Q, and no layout is generated - the w/s/lengths are geometry to draw, not a routed board. filter_waveguide_cavity is TE101 only: no higher-order modes, no mode chart, no iris or probe coupling geometry (a coupling iris must be solved by a full-wave solver), no surface-roughness correction. Nothing here is an EM simulator: a filter that must meet a mask needs the EM run, and these numbers are the starting point handed to it",
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
                       "only for KiCad's argv form. Measured this round: the "
                       "LCEDA Pro launcher exits in ~0.5 s when started from a "
                       "non-interactive parent, so its argv form could not be "
                       "confirmed and stays false",
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
                         "and say so",
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
    "single-use and expires after an hour.",
    {"type": "object", "properties": {
        "plan_id": _str("the plan_id that board_propose returned"),
        "confirm": _bool("must be true - writing a board is not Ctrl+Z"),
        "options": {"type": "object", "properties": {
            "allow_new_violations": _bool("accept newly introduced DRC violations"),
            "allow_open_outline": _bool("accept an outline that does not close"),
            "engine": _bool("run the kicad-cli comparison (default true)", True),
            "mode": _str("in_place or new_file", enum=["in_place", "new_file"]),
            "out_path": _str("target when mode=new_file"),
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
    "refused (INTERNAL_CHECK_FAILED) rather than reported.",
    {"type": "object", "properties": {
        "order": _num("filter order"),
        "f0_hz": _num("centre frequency in Hz"),
        "fbw": _num("fractional bandwidth, e.g. 0.1 for 10 % (0 < fbw < 1)"),
        "z0_ohm": _num("terminating impedance", 50.0),
        "ripple_db": _num("passband ripple; chebyshev default 0.5"),
        "response": _str("prototype family", ["chebyshev", "butterworth"],
                         default="chebyshev"),
        "points": _num("S21 sweep points", 31),
    }, "required": ["order", "f0_hz", "fbw"]},
    lambda a: _filters.lumped_bandpass(
        a.get("order"), a.get("f0_hz"), a.get("fbw"),
        z0_ohm=(a["z0_ohm"] if a.get("z0_ohm") is not None else 50.0),
        ripple_db=a.get("ripple_db"),
        response=a.get("response") or "chebyshev",
        points=int(a["points"] if a.get("points") is not None else 31)),
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
