"""Tool registry: name -> JSON schema + handler.

Every handler returns the uniform envelope from calc.result.
The schema is the only thing the model sees before calling, so descriptions
must say what the tool will NOT do as clearly as what it does.
"""
import json

from . import desktop as _desktop
from . import fidelity as _fidelity
from . import oracle as _oracle
from .fidelity import views as _fviews
from .calc import current as _current
from .calc import creepage as _creepage
from .calc import diffpair as _diffpair
from .calc import impedance as _impedance
from .calc import rf as _rf
from .calc import units as _units
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
    "Inspect an EasyEDA Pro .epro WITHOUT interpreting it: members, per-document object "
    "type counts, and the raw key names of the first payloads of each type. Use it to "
    "close an unmapped field instead of guessing a key name.",
    {"type": "object",
     "properties": {"path": _str("absolute path to the .epro"),
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
            "desktop": "find / start / stop the EDA and open a project, verified by "
                       "window title - never touches a project file",
        },
        "available": [
            "unit_convert", "width_for_current", "current_capacity",
            "via_current_allowed", "trace_impedance", "width_for_impedance",
            "diff_impedance", "diff_width", "lambda_over_20",
            "board_summary", "board_components", "board_nets", "board_clearance",
            "board_free_space", "board_pour_connectivity", "board_cross_split",
            "board_loop_area", "board_fidelity", "board_view", "board_drc",
            "board_stats", "epro_inspect",
            "app_detect", "app_status", "app_launch", "app_close", "app_open_project",
        ],
        "fail_closed_pending_data": {
            "creepage_required": "needs a verified IEC table in data/creepage_tables.json",
            "clearance_required": "needs a verified IEC table in data/creepage_tables.json",
            "standard=ipc2152": "needs a verified IPC-2152 data table",
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
                "board_cross_split", "board_loop_area"],
            "authoritative (vendor engine said it)": ["board_drc", "board_stats"],
            "why_it_matters": "a computed clearance is a measurement with a model and a "
                              "precision; a DRC verdict is the tool's own ruling. When "
                              "they disagree, the verdict wins - so every derived answer "
                              "carries authoritative=false and names board_drc",
        },
        "not_implemented_yet": [
            "WRITE path: nothing here edits, creates or deletes a board. Writing goes "
            "through propose_plan -> digest -> approve in a later phase",
            "EasyEDA Pro live API drive (websocket gateway on 49620-49629): the port is "
            "only opened after the user enables 外部交互权限, and no client is implemented",
            "DFM checks (fabricator capability rules)",
            "BGA/thermal via array temperature rise, fuse behaviour, electrothermal "
            "coupling (IPC-2152 tables not loaded)",
            "Impedance/creepage tables for standards other than IPC-2221 / IPC-2141A",
        ],
        "contract": "every value carries model + basis + precision + assumptions; a "
                    "missing fact is refused or declared absent, never defaulted",
        "verified_against": {
            "calc": "hand-computed golden values (tests/test_calc.py, tests/test_diffpair.py)",
            "fidelity": "KiCad side proven against kicad-cli 10.0.7: identical stats, "
                        "identical plotted Gerbers, identical positions after a round "
                        "trip; the sample board itself is now validated by KiCad (an "
                        "invalid sample was the first thing this check caught)",
            "adapters": "SYNTHETIC boards only - no real .epro/.kicad_pcb was available "
                        "when this was written, so payload key names and the 0.01-inch "
                        "unit are unverified (board_summary.source says so); "
                        "board_fidelity(key_inventory) on a real .epro closes that",
            "desktop": "real install: LCEDA Pro 4.1.60 and KiCad 10.0.7 both detected by "
                       "drive scan; window-title parsing unit-tested, live open confirmed "
                       "only for KiCad's argv form",
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


def _board_stats(args):
    from .fidelity import engine as _engine
    path = args.get("path")
    if not path:
        return err("path is required", "ARG_MISSING")
    return _engine.stats(path)


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
