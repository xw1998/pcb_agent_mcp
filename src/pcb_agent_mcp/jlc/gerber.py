"""Independent review of an exported Gerber/Excellon package - reads the files, never the EDA's own DRC.

Ported from jlceda-pcb-ai-workflow (MIT) ``scripts/gerber_guard.py`` with one change that
matters here: layers are identified by **role** (copper_top, mask_bottom, outline, ...),
not by JLCPCB's fixed file names, because this server's own ``board_fab_package`` exports
with KiCad naming (``board-F_Cu.gbr`` / ``board-PTH.drl``). Both namings, and any Gerber
carrying a ``%TF.FileFunction%`` attribute, resolve to the same roles.

What it can reverse-engineer from the files: track widths (circular aperture, D01 outside
a region), drill and slot diameters, board outline closure and its bounding box, minimum
silkscreen *text* width (JLCPCB publishes "character stroke", so component-outline
graphics are reported separately instead of being graded as text).

What it cannot: track spacing, pad-to-track, solder-mask bridge, silk height, silk-to-pad,
copper-to-edge. Those are listed under ``uncovered`` and are never counted as a pass.
"""
import re
import zipfile
import os

from . import spec

MM_PER_INCH = 25.4     # unit conversion, not a fabrication threshold

JLC_EXT_ROLE = {
    "GTL": "copper_top", "GBL": "copper_bottom", "GTO": "silk_top", "GBO": "silk_bottom",
    "GTS": "mask_top", "GBS": "mask_bottom", "GTP": "paste_top", "GBP": "paste_bottom",
    "GKO": "outline", "GDL": "document", "GDD": "drawing",
}

KICAD_NAME_ROLE = {
    "F_CU": "copper_top", "B_CU": "copper_bottom",
    "F_SILKSCREEN": "silk_top", "B_SILKSCREEN": "silk_bottom",
    "F_MASK": "mask_top", "B_MASK": "mask_bottom",
    "F_PASTE": "paste_top", "B_PASTE": "paste_bottom",
    "EDGE_CUTS": "outline", "F_FAB": "document", "B_FAB": "document",
    "F_ADHES": "other", "B_ADHES": "other", "F_ADHV": "other", "B_ADHV": "other",
    "F_CRTY": "other", "B_CRTY": "other", "F_MARGIN": "other", "B_MARGIN": "other",
}

# what the fab actually needs in the package, by role
REQUIRED_ROLES = [("copper_top", "top copper"), ("copper_bottom", "bottom copper"),
                  ("outline", "board outline (the fab cuts to this file)"),
                  ("mask_top", "top solder mask"), ("mask_bottom", "bottom solder mask")]
EXPECTED_ROLES = [("silk_top", "top silkscreen (no refdes/ polarity without it)"),
                  ("silk_bottom", "bottom silkscreen"),
                  ("drill_plated", "plated drill file (vias / through-hole parts)")]

UNCOVERED = ["track spacing (min_track_space)", "pad-to-track (min_pad_to_track)",
             "solder-mask bridge (min_mask_bridge)", "silkscreen height (min_silk_height)",
             "silk-to-pad (min_silk_to_pad)", "copper-to-board-edge (min_copper_to_edge)",
             "via outer diameter / annular ring / hole-to-hole (needs EDA API)"]


class Unsupported(Exception):
    pass


# ------------------------------------------------------------------- packaging
def read_package(path):
    """{file name: bytes} from a zip or a directory."""
    files = {}
    if os.path.isdir(path):
        for name in sorted(os.listdir(path)):
            full = os.path.join(path, name)
            if os.path.isfile(full):
                with open(full, "rb") as handle:
                    files[name] = handle.read()
        return files
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            if not info.is_dir():
                files[os.path.basename(info.filename)] = archive.read(info)
    return files


# ------------------------------------------------------------------ Gerber RS274X
_RE_FS = re.compile(r"%FS([LT])([AI])X(\d)(\d)Y(\d)(\d)\*%")
_RE_MO = re.compile(r"%MO(MM|IN)\*%")
_RE_AD = re.compile(r"%ADD(\d+)([^,*%]+),?([^*%]*)\*%")
_RE_COORD = re.compile(r"^(?:G0?([123]))?(?:X(-?\d+))?(?:Y(-?\d+))?(?:I(-?\d+))?(?:J(-?\d+))?D0?([123])$")
_RE_FF = re.compile(r"%TF\.FileFunction,([^*%]+)\*%")

_FILEFUNCTION_ROLE = [
    ("copper,l1,top", "copper_top"), ("copper,l2,bot", "copper_bottom"),
    ("soldermask,top", "mask_top"), ("soldermask,bot", "mask_bottom"),
    ("legend,top", "silk_top"), ("legend,bot", "silk_bottom"),
    ("paste,top", "paste_top"), ("paste,bot", "paste_bottom"),
    ("profile,np", "outline"), ("plated,1,2,pth", "drill_plated"),
]


def file_function_role(text):
    match = _RE_FF.search(text[:2000] if text else "")
    if not match:
        return None
    body = match.group(1).strip().lower()
    for needle, role in _FILEFUNCTION_ROLE:
        if body.startswith(needle):
            return role
    return None


def classify(name, text=None):
    """Role of one file. Extension / KiCad layer name first, then the Gerber attribute."""
    stem, dot, ext = name.rpartition(".")
    ext = ext.upper() if dot else ""
    if ext in JLC_EXT_ROLE:
        return JLC_EXT_ROLE[ext]
    if ext in ("DRL", "XNC", "TXT"):
        # a .txt file is only a drill file when its name says so - export notes and
        # drill *drawings* are also shipped as .txt and must not be parsed as Excellon
        low = name.upper()
        drillish = ext in ("DRL", "XNC") or any(t in low for t in ("DRILL", "DRL", "NPTH", "PTH"))
        if drillish:
            if "_VIA" in low:
                return "drill_plated_via"
            if "NPTH" in low or "NON_PLATED" in low:
                return "drill_npth"
            return "drill_plated"
        return "document" if "DRAWING" in low else "other"
    if re.match(r"^.*-In\d+_Cu$", stem, re.I):
        return "inner"
    if re.match(r"^Gerber_InnerLayer(\d+)\.(G|GP)(\d+)$", name):
        return "inner"
    if ext in ("G", "GP") and re.match(r"^Gerber_InnerLayer", name):
        return "inner"
    layer = stem.rsplit("-", 1)[-1].upper() if stem else ""
    if layer in KICAD_NAME_ROLE:
        return KICAD_NAME_ROLE[layer]
    if ext in ("G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8") and re.match(r"^.*-In\d+_Cu$", stem, re.I):
        return "inner"
    return file_function_role(text) or "other"


def parse_gerber(text):
    fs, mo = _RE_FS.search(text), _RE_MO.search(text)
    if not fs or not mo:
        raise Unsupported("no %FS or %MO header")
    if fs.group(1) != "L" or fs.group(2) != "A":
        raise Unsupported("only leading-zero-omitted absolute coordinates (FSLA) are read; "
                          "found FS%s%s" % (fs.group(1), fs.group(2)))
    dec = int(fs.group(4))
    unit = "mm" if mo.group(1) == "MM" else "in"
    scale = 1.0 if unit == "mm" else MM_PER_INCH
    aps = {}
    for match in _RE_AD.finditer(text):
        shape, params = match.group(2), match.group(3)
        dia = None
        if shape == "C" and params:
            try:
                dia = float(params.split("X")[0]) * scale
            except ValueError:
                dia = None
        aps[int(match.group(1))] = {"shape": shape, "dia": dia}
    body = re.sub(r"%[^%]*%", "", text, flags=re.S).replace("\n", "").replace("\r", "")
    info = {"unit": unit, "fmt": "%s:%s" % (fs.group(3), fs.group(4)), "apertures": aps,
            "draw_dia": [], "by_section": {}, "draws": 0, "flashes": 0, "regions": 0,
            "arcs": 0, "segments": [], "points": [], "ends_m02": False}
    current, mode, in_region, section = None, 1, False, ""
    pos = (0.0, 0.0)
    for raw in body.split("*"):
        chunk = raw.strip()
        if not chunk:
            continue
        if chunk.startswith("G04"):
            marker = re.match(r"G04 (\w[\w ]*?) Start$", chunk)
            if marker:
                section = marker.group(1)
            continue
        if chunk == "M02":
            info["ends_m02"] = True
            continue
        if chunk in ("G36", "G37"):
            in_region = chunk == "G36"
            if in_region:
                info["regions"] += 1
            continue
        match = re.fullmatch(r"(?:G54)?D(\d{2,})", chunk)
        if match:
            current = int(match.group(1))
            continue
        match = re.fullmatch(r"G0?([123])", chunk)
        if match:
            mode = int(match.group(1))
            continue
        match = _RE_COORD.match(chunk)
        if not match:
            continue
        g, x, y, _i, _j, d = match.groups()
        if g:
            mode = int(g)
        nx = int(x) / 10 ** dec * scale if x is not None else pos[0]
        ny = int(y) / 10 ** dec * scale if y is not None else pos[1]
        d = int(d)
        if d == 1 and not in_region:
            info["draws"] += 1
            aperture = aps.get(current)
            if aperture and aperture["dia"] is not None:
                info["draw_dia"].append(aperture["dia"])
                info["by_section"].setdefault(section, []).append(aperture["dia"])
            info["segments"].append((pos, (nx, ny)))
            if mode in (2, 3):
                info["arcs"] += 1
        elif d == 3:
            info["flashes"] += 1
        pos = (nx, ny)
        info["points"].append(pos)
    return info


def parse_drill(text, kind_hint=None):
    unit = "mm" if "METRIC" in text else ("in" if "INCH" in text else None)
    if unit is None:
        raise Unsupported("Excellon file has neither METRIC nor INCH")
    scale = 1.0 if unit == "mm" else MM_PER_INCH
    if "TYPE=PLATED" in text:
        ptype = "plated"
    elif "TYPE=NON_PLATED" in text:
        ptype = "nonplated"
    elif kind_hint:
        ptype = kind_hint
    else:
        ptype = "unknown"
    tools, hits, slots, current, ended = {}, {}, {}, None, False
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith(";"):
            continue
        if s == "M30":
            ended = True
            continue
        match = re.match(r"^T(\d+)C([\d.]+)", s)
        if match:
            tools[int(match.group(1))] = float(match.group(2)) * scale
            continue
        match = re.fullmatch(r"T(\d+)", s)
        if match:
            current = int(match.group(1))
            continue
        if s.startswith(("X", "Y")) and current is not None:
            target = slots if "G85" in s else hits
            target[current] = target.get(current, 0) + 1

    def diameters(table):
        return sorted({tools[t] for t in table if t in tools})

    def hist(table):
        out = {}
        for tool, count in table.items():
            if tool in tools:
                key = round(tools[tool], 4)
                out[key] = out.get(key, 0) + count
        return out

    return {"unit": unit, "type": ptype, "tools": tools, "hole_dias": diameters(hits),
            "slot_dias": diameters(slots), "hole_hist": hist(hits), "slot_hist": hist(slots),
            "ends_m30": ended, "hole_count": sum(hits.values()), "slot_count": sum(slots.values())}


def outline_closed(segments, ndig=3):
    """Every endpoint has even degree -> the outline is a closed set of contours."""
    degree = {}
    for a, b in segments:
        for point in (a, b):
            key = (round(point[0], ndig), round(point[1], ndig))
            degree[key] = degree.get(key, 0) + 1
    odd = [k for k, v in degree.items() if v % 2]
    return (len(segments) > 0 and not odd), odd


def _decode(raw):
    return raw.decode("utf-8", "replace")


# ---------------------------------------------------------------------- checks
def check_package(files, process=None):
    items, meas, notes = [], {}, []

    def add(ident, status, msg):
        items.append({"id": ident, "verdict": status, "msg": msg})

    texts = {name: _decode(raw) for name, raw in files.items()}
    roles = {name: classify(name, text) for name, text in texts.items()}
    inner = sorted(n for n, r in roles.items() if r == "inner")
    layout = spec.load("jlc-gerber-layout.json")
    declared = process.get("layers") if process else None
    inferred = 2 + len(inner)
    if declared is not None and declared != inferred and declared > 2:
        add("GB.LAYERS.MATCH", "BLOCK", "declared %s layer(s), but the package carries %d inner "
            "file(s) (inferred %d layers)" % (declared, len(inner), inferred))
    elif declared == 2 and inner:
        add("GB.LAYERS.MATCH", "BLOCK", "declared 2 layers, but the package carries inner files %s" % (inner,))

    present = set(roles.values())
    for role, why in REQUIRED_ROLES:
        if role not in present:
            add("GB.FILE.REQUIRED", "BLOCK", "no %s file in the package (%s)" % (role, why))
    for role, why in EXPECTED_ROLES:
        if role not in present:
            add("GB.FILE.EXPECTED", "WARN", "no %s file in the package (%s)" % (role, why))
    for name, raw in files.items():
        if len(raw) == 0:
            add("GB.FILE.EMPTY", "BLOCK", "%s is a 0-byte file" % name)

    parsed, fmts, units, copper_ops = {}, set(), set(), {}
    for name, raw in sorted(files.items()):
        role = roles[name]
        if role in ("copper_top", "copper_bottom", "inner", "silk_top", "silk_bottom",
                    "mask_top", "mask_bottom", "paste_top", "paste_bottom", "outline", "document"):
            try:
                gerber = parse_gerber(texts[name])
            except Unsupported as exc:
                add("GB.PARSE", "BLOCK", "%s cannot be parsed: %s (BLOCKED, never a pass)" % (name, exc))
                continue
            parsed[name] = (role, gerber)
            fmts.add(gerber["fmt"])
            units.add(gerber["unit"])
            if not gerber["ends_m02"]:
                add("GB.END.M02", "BLOCK", "%s does not end with M02* (file may be truncated)" % name)
            if role in ("copper_top", "copper_bottom", "inner"):
                copper_ops[name] = gerber["draws"] + gerber["flashes"] + gerber["regions"]
        elif role.startswith("drill"):
            hint = "nonplated" if role == "drill_npth" else "plated"
            try:
                drill = parse_drill(texts[name], hint)
            except Unsupported as exc:
                add("GB.PARSE", "BLOCK", "%s cannot be parsed: %s" % (name, exc))
                continue
            parsed[name] = ("drill", drill)
            units.add(drill["unit"])
            if not drill["ends_m30"]:
                add("GB.END.M30", "BLOCK", "%s does not end with M30 (drill file may be truncated)" % name)
    if len(fmts) > 1:
        add("GB.FMT.CONSIST", "BLOCK", "coordinate format differs between layers: %s" % sorted(fmts))
    if len(units) > 1:
        add("GB.UNIT.CONSIST", "BLOCK", "units differ between files: %s" % sorted(units))

    heuristics = layout["heuristics"]
    for name, ops in copper_ops.items():
        if ops < heuristics["copper_layer_min_ops"]:
            add("GB.COPPER.EMPTY", "WARN", "%s carries only %d graphic operations, almost empty: %s"
                % (name, ops, heuristics["copper_layer_min_ops_why"]))

    for name, (role, gerber) in parsed.items():
        if role != "outline":
            continue
        closed, odd = outline_closed(gerber["segments"])
        if not gerber["segments"]:
            add("GB.OUTLINE.EMPTY", "BLOCK", "%s has no outline segment" % name)
        elif not closed:
            add("GB.OUTLINE.CLOSED", "BLOCK", "outline not closed: %d endpoint(s) with odd degree "
                "(e.g. %s)" % (len(odd), odd[:3]))
        else:
            xs = [p[0] for p in gerber["points"]]
            ys = [p[1] for p in gerber["points"]]
            w, h = max(xs) - min(xs), max(ys) - min(ys)
            meas["board_w"], meas["board_h"] = round(w, 4), round(h, 4)
            meas["min_board_dim"] = round(min(w, h), 4)
            tag = " (contains arcs, bounding box is approximate)" if gerber["arcs"] else ""
            add("GB.OUTLINE.CLOSED", "PASS", "outline closed, bounding box %.3f x %.3f mm%s" % (w, h, tag))

    track = [dia for name, (role, g) in parsed.items()
             if role in ("copper_top", "copper_bottom", "inner") for dia in g["draw_dia"]]
    if track:
        meas["min_track_width"] = round(min(track), 4)
    elif copper_ops and any(copper_ops.values()):
        add("GB.TRACK.NONE", "WARN", "no copper layer draws a track (only pads/fills): the board "
            "may be unrouted or the export incomplete; no width was measured, so do not report "
            "the width as passing")

    silk_text = [dia for name, (role, g) in parsed.items() if role in ("silk_top", "silk_bottom")
                 for dia in g["by_section"].get("Text", [])]
    silk_gfx = [dia for name, (role, g) in parsed.items() if role in ("silk_top", "silk_bottom")
                for section, values in g["by_section"].items() if section != "Text" for dia in values]
    if silk_text:
        meas["min_silk_width"] = round(min(silk_text), 4)
    elif any(role in ("silk_top", "silk_bottom") for role, _g in parsed.values()):
        notes.append("silkscreen carries no text draw segments, so the character stroke width "
                     "was not measured")
    if silk_gfx and (not silk_text or min(silk_gfx) < min(silk_text)):
        notes.append("thinnest silkscreen graphic (component outline / marks) is %.4f mm: that is "
                     "not the published 'character stroke' rule, only a legibility hint" % min(silk_gfx))

    via_hist = pth_hist = None
    for name, (role, drill) in parsed.items():
        if role != "drill" or drill["type"] != "plated":
            continue
        if name.upper().endswith("_VIA.DRL"):
            via_hist = drill["hole_hist"]
        else:
            pth_hist = drill["hole_hist"] if pth_hist is None else {**pth_hist, **drill["hole_hist"]}
    if via_hist is not None and pth_hist is not None:
        meas["via_count"] = sum(via_hist.values())
        through = {dia: c - via_hist.get(dia, 0) for dia, c in pth_hist.items()
                   if c - via_hist.get(dia, 0) > 0}
        if through:
            meas["min_th_drill"] = min(through)
        bad = [dia for dia, c in via_hist.items() if c > pth_hist.get(dia, 0)]
        if bad:
            add("GB.DRILL.VIA_SUBSET", "BLOCK", "apertures %s in _Via.DRL exceed PTH_Through.DRL; "
                "the two files disagree" % bad)
    for name, (role, drill) in parsed.items():
        if role != "drill":
            continue
        if drill["type"] == "plated" and drill["hole_dias"]:
            meas["min_plated_drill"] = round(min(meas.get("min_plated_drill", 1e9), min(drill["hole_dias"])), 4)
        if drill["type"] == "nonplated" and drill["hole_dias"]:
            meas["min_npth_drill"] = round(min(meas.get("min_npth_drill", 1e9), min(drill["hole_dias"])), 4)
        if drill["slot_dias"]:
            key = "min_slot_plated_width" if drill["type"] == "plated" else "min_slot_npth_width"
            meas[key] = round(min(meas.get(key, 1e9), min(drill["slot_dias"])), 4)
    if not any(role.startswith("drill") for role in roles.values()):
        notes.append("no drill file in the package, so no hole diameter was measured")
    elif via_hist is None:
        notes.append("no *_Via.DRL in this package (older export): vias and through-hole holes are "
                     "merged, so min_th_drill was not separated")

    for name, text in texts.items():
        match = re.search(r"G04 (EasyEDA Pro v[^,]+), ([\d-]+ [\d:]+)\*", text[:600])
        if match:
            notes.append("exported by %s at %s - confirm it matches the design version you reviewed"
                         % (match.group(1), match.group(2)))
            break
    return items, meas, notes, inferred


def guard(package, process=None, strict=False, structure_only=False, extra_measurements=None):
    """Full answer for one package: structure + (unless structure-only) capability review."""
    files = read_package(package)
    items, meas, notes, inferred = check_package(files, process)
    if extra_measurements:
        meas.update(extra_measurements)
    spec_out, spec_code, spec_summary = None, 0, None
    if structure_only:
        notes.append("structure-only: the capability review was not run")
    elif not process:
        spec_code = 2
        notes.append("BLOCKED: no process declaration given, so no capability rule can be "
                     "selected (layers / copper_oz / thickness_mm / surface stay undeclared)")
    else:
        try:
            result = spec.check(process, meas, partial=not strict)
            spec_summary, spec_code = result["summary"], result["exit"]
            spec_out = spec.render(result["results"], result["summary"], process, spec_code)
        except ValueError as exc:
            spec_code = 2
            notes.append("BLOCKED: %s" % exc)
    blocks = sum(1 for item in items if item["verdict"] == "BLOCK")
    code = 1 if (blocks or spec_code == 1) else (2 if spec_code == 2 else 0)
    return {"package": package, "file_count": len(files), "inferred_layers": inferred,
            "structure": items, "measurements": meas, "notes": notes,
            "spec_summary": spec_summary, "spec_report": spec_out,
            "uncovered": UNCOVERED, "exit": code,
            "verdict": {0: "no blocker inside the declared scope (not a promise it can be built)",
                        1: "blocked - do not release",
                        2: "BLOCKED - missing declaration / unparsable input / missing evidence"}[code]}
