"""EasyEDA (LCEDA) Pro `.epro` adapter (read-only).

`.epro` is a ZIP container. The interesting members are:

    project.json      project manifest
    *.epcb            PCB document, JSON Lines, append-only log (V3)
    *.esch            schematic document
    *.esym            symbol
    *.efoo            footprint
    *.ecop            copper pour fill result (separate file)
    *.eblob           binary blobs (3D models, images)

Three facts make this format dangerous for a naive reader, and all three are
handled here and nowhere else:

1. **Unit** - coordinates are integers in 0.01 inch (0.254 mm). Reading them as
   mm understates every dimension by 25.4x and every DRC still passes.
2. **Y axis** - grows downwards, canonical grows upwards.
3. **Append-only log** - a line is `{"type","id","ticket"}||{payload}`. The same
   object appears many times with increasing `ticket`; the highest ticket wins,
   and a payload that sets a field to `""` means "deleted". A reader that takes
   the first (or last) occurrence blindly gets a stale board.

Key names inside payloads are not part of a published schema, so this adapter
resolves each field through a candidate list and **reports what it could not
map** instead of silently substituting a zero. `inspect()` exists so the exact
key names of a real project can be read out and the candidate lists corrected -
that is the honest way to close an unmapped field.
"""
import json
import zipfile

from .canonical import Board

# EasyEDA Pro internal unit: 0.01 inch
UNIT_TO_MM = 0.254

CANDIDATES = {
    "x": ("x", "left", "px", "startx", "x1"),
    "y": ("y", "top", "py", "starty", "y1"),
    "x2": ("x2", "endx", "right"),
    "y2": ("y2", "endy", "bottom"),
    "width": ("width", "lineWidth", "strokeWidth", "w"),
    "height": ("height", "h"),
    "rotation": ("rotation", "rotate", "angle", "rot"),
    "layer": ("layer", "layerId", "layerName"),
    "net": ("net", "netId", "netName", "net_name"),
    "name": ("name", "title", "netName", "designator", "ref", "text"),
    "designator": ("designator", "ref", "name", "prefix", "title"),
    "number": ("number", "num", "padNumber", "name"),
    "hole": ("holeRadius", "hole", "holeDiameter", "drill", "holeSize"),
    "pad_diameter": ("padRadius", "padDiameter", "diameter", "size"),
    "points": ("points", "polygon", "path", "pointArr", "pts", "polyline"),
    "rule_type": ("ruleType", "type"),
    "parent": ("parentId", "parent", "ownerId", "componentId", "pid"),
    "shape": ("shape", "padShape", "shapeType"),
}

_TYPE_ALIASES = {
    "LAYER": "LAYER", "NET": "NET", "RULE": "RULE", "VIA": "VIA", "LINE": "LINE",
    "ARC": "ARC", "POLY": "POLY", "FILL": "FILL", "POUR": "POUR", "POURED": "POURED",
    "TEARDROP": "TEARDROP", "REGION": "REGION", "PAD": "PAD", "COMPONENT": "COMPONENT",
    "PAD_NET": "PAD_NET", "ATTR": "ATTR", "STRING": "STRING", "IMAGE": "IMAGE", "OBJ": "OBJ",
}


class EproError(Exception):
    pass


def _norm_keys(d):
    """Lower-case, strip separators: {"NetName":1} -> {"netname":1}."""
    out = {}
    for k, v in d.items():
        key = "".join(ch for ch in str(k).lower() if ch.isalnum())
        if key not in out:
            out[key] = v
    return out


def _get(flat, name, default=None):
    for cand in CANDIDATES.get(name, (name,)):
        key = "".join(ch for ch in cand.lower() if ch.isalnum())
        if key in flat and flat[key] not in (None, ""):
            return flat[key]
    return default


def _num(v, default=None):
    if isinstance(v, bool):
        return default
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return default


def split_line(line):
    """Split one log line into (header, payload). Handles both shapes.

    V3: `{"type":"PAD","id":"gge1","ticket":12}||{"x":100,...}`
    Also accepts a plain object (single snapshot line, or older exports) where
    header fields are embedded in the same object.
    """
    line = line.strip()
    if not line:
        return None, None
    if "||" in line:
        head, _, tail = line.partition("||")
        try:
            header = json.loads(head)
        except ValueError:
            return None, None
        try:
            payload = json.loads(tail) if tail.strip() else {}
        except ValueError:
            payload = {}
        return header, payload
    try:
        obj = json.loads(line)
    except ValueError:
        return None, None
    if isinstance(obj, dict):
        header = {k: obj[k] for k in ("type", "id", "ticket") if k in obj}
        payload = {k: v for k, v in obj.items() if k not in header}
        return header, payload
    return None, None


def read_document(zip_file, member):
    """Resolve an append-only doc into {id: (type, payload)}, highest ticket wins.

    Returns (objects, stats). `stats` carries how many lines were seen, how many
    lost to a newer ticket, how many were deletions and how many were unparsable
    - the last number matters: silently dropping lines is how a reader ends up
    with a board that never existed.
    """
    objects = {}
    tickets = {}
    stats = {"lines": 0, "objects": 0, "superseded": 0, "deleted": 0,
             "unparsable": 0, "delete_doc": 0, "overrides": 0}
    with zip_file.open(member) as fh:
        raw = fh.read()
    text = raw.decode("utf-8", errors="replace")
    for line in text.splitlines():
        stats["lines"] += 1
        header, payload = split_line(line)
        if header is None:
            stats["unparsable"] += 1
            continue
        otype = str(header.get("type") or "").upper()
        oid = header.get("id")
        if otype in ("DELETE_DOC", "DELETE"):
            stats["delete_doc"] += 1
            continue
        if oid is None:
            # some lines are document-level overrides without an id
            stats["overrides"] += 1
            continue
        try:
            ticket = float(header.get("ticket") or 0)
        except (TypeError, ValueError):
            ticket = 0.0
        key = (otype, str(oid))
        prev = tickets.get(key)
        if prev is not None and ticket < prev:
            stats["superseded"] += 1
            continue
        if prev is not None:
            stats["superseded"] += 1
        # a payload of entirely empty values is the log's delete marker
        if payload is not None and all(v == "" for v in payload.values()):
            objects.pop(key, None)
            tickets[key] = ticket
            stats["deleted"] += 1
            continue
        tickets[key] = ticket
        objects[key] = payload
    stats["objects"] = len(objects)
    return objects, stats


# ------------------------------------------------------------------ inspect
def inspect(path, sample=3):
    """Report what is inside a `.epro` without interpreting it.

    This is the tool that closes unknown fields: it lists every member, the
    document type counts of each `.epcb`, and the first few payloads of each type
    with their raw key names. Guessing key names is not acceptable; reading them
    out of a real project is.
    """
    out = {"status": "ok", "path": path, "members": [], "documents": []}
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        out["members"] = [{"name": n, "size": zf.getinfo(n).file_size} for n in names]
        pcb_members = [n for n in names if n.lower().endswith(".epcb")]
        for member in pcb_members:
            objects, stats = read_document(zf, member)
            by_type = {}
            for (otype, oid), payload in objects.items():
                entry = by_type.setdefault(otype, {"count": 0, "samples": []})
                entry["count"] += 1
                if len(entry["samples"]) < sample:
                    entry["samples"].append({"id": oid,
                                             "keys": sorted(payload.keys())[:24]})
            out["documents"].append({"member": member, "stats": stats,
                                     "types": by_type})
        if not pcb_members:
            out["note"] = "no .epcb member found; this .epro holds no PCB document"
    return out


# -------------------------------------------------------------------- parse
def _points_from(flat):
    raw = _get(flat, "points")
    pts = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                x, y = _num(item[0]), _num(item[1])
                if x is not None and y is not None:
                    pts.append((x, y))
            elif isinstance(item, dict):
                f = _norm_keys(item)
                x, y = _num(_get(f, "x")), _num(_get(f, "y"))
                if x is not None and y is not None:
                    pts.append((x, y))
            elif isinstance(item, str):
                parts = item.replace(",", " ").split()
                if len(parts) >= 2:
                    try:
                        pts.append((float(parts[0]), float(parts[1])))
                    except ValueError:
                        pass
    elif isinstance(raw, str):
        cleaned = raw.replace(",", " ")
        parts = [p for p in cleaned.replace(";", " ").split() if p]
        for i in range(0, len(parts) - 1, 2):
            try:
                pts.append((float(parts[i]), float(parts[i + 1])))
            except ValueError:
                break
    return pts


def parse_board(path, member=None, assume_unit=UNIT_TO_MM):
    """Parse the PCB document of an `.epro` into a canonical Board. Read-only."""
    board = Board("easyeda_epro", path)
    if not zipfile.is_zipfile(path):
        raise EproError("not a ZIP container (an .epro must be a ZIP): %s" % path)
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        pcb_members = [n for n in names if n.lower().endswith(".epcb")]
        if not pcb_members:
            raise EproError("no .epcb member inside %s (members: %s)"
                            % (path, ", ".join(names[:8])))
        chosen = member or max(pcb_members, key=lambda n: zf.getinfo(n).file_size)
        objects, stats = read_document(zf, chosen)
        board.source["member"] = chosen
        board.source["members"] = [n for n in names if n.lower().endswith(
            (".epcb", ".ecop", ".esch"))]

        # project.json: project name + any unit hint the format actually stores
        for n in names:
            if n.lower().endswith("project.json"):
                try:
                    manifest = json.loads(zf.read(n).decode("utf-8", errors="replace"))
                    board.source["project_name"] = manifest.get("name") or manifest.get("title")
                except Exception:                                # noqa: BLE001
                    pass
                break

    def to_mm(v):
        return round(float(v) * assume_unit, 6) if v is not None else None

    def pt(xy_pair):
        return [to_mm(xy_pair[0]), -to_mm(xy_pair[1])]      # Y flip

    unmapped = set()
    unknown_types = {}
    pad_by_id = {}

    for (otype, oid), payload in objects.items():
        flat = _norm_keys(payload)
        if otype == "NET":
            name = _get(flat, "name")
            if name in (None, ""):
                unmapped.add("NET.name")
                continue
            board.nets[str(oid)] = str(name)
        elif otype == "COMPONENT":
            x = _num(_get(flat, "x"))
            y = _num(_get(flat, "y"))
            if x is None or y is None:
                unmapped.add("COMPONENT.x/y")
                continue
            rot = _num(_get(flat, "rotation"), 0.0) or 0.0
            board.footprints.append({
                "ref": str(_get(flat, "designator", "") or ""),
                "value": str(_get(flat, "name", "") or ""),
                "lib_id": str(payload.get("footprint") or payload.get("device") or ""),
                "x": to_mm(x), "y": -to_mm(y),
                "rotation": -rot, "layer": str(_get(flat, "layer", "") or ""),
                "bbox": None, "pads": [], "_id": oid,
            })
        elif otype == "PAD":
            x = _num(_get(flat, "x"))
            y = _num(_get(flat, "y"))
            if x is None or y is None:
                unmapped.add("PAD.x/y")
                continue
            w = _num(_get(flat, "width"))
            h = _num(_get(flat, "height"), w)
            hole = _num(_get(flat, "hole"))
            net = _get(flat, "net")
            pad = {
                "number": str(_get(flat, "number", "") or ""),
                "net": board.net_name(net) if net is not None else None,
                "x": to_mm(x), "y": -to_mm(y),
                "w": to_mm(w) if w is not None else None,
                "h": to_mm(h) if h is not None else None,
                "shape": str(_get(flat, "shape", "rect") or "rect").lower(),
                "rotation": -(_num(_get(flat, "rotation"), 0.0) or 0.0),
                "layers": [str(_get(flat, "layer", "") or "")],
                "hole_mm": to_mm(hole) if hole else None,
                "smd": hole is None,
                "ref": str(_get(flat, "parent", "") or ""),
            }
            pad_by_id[oid] = pad
        elif otype in ("LINE", "ARC", "POLY"):
            pts = _points_from(flat)
            x1, y1 = _num(_get(flat, "x")), _num(_get(flat, "y"))
            x2, y2 = _num(_get(flat, "x2")), _num(_get(flat, "y2"))
            if not pts and None not in (x1, y1, x2, y2):
                pts = [(x1, y1), (x2, y2)]
            if len(pts) < 2:
                unmapped.add(otype + ".points")
                continue
            width = _num(_get(flat, "width"), 0.0) or 0.0
            net = _get(flat, "net")
            board.tracks.append({
                "net": board.net_name(net) if net is not None else None,
                "layer": str(_get(flat, "layer", "") or ""),
                "width": to_mm(width),
                "start": pt(pts[0]),
                "end": pt(pts[-1]),
                "_points": [pt(p) for p in pts],
            })
        elif otype == "VIA":
            x = _num(_get(flat, "x"))
            y = _num(_get(flat, "y"))
            if x is None or y is None:
                unmapped.add("VIA.x/y")
                continue
            hole = _num(_get(flat, "hole"))
            dia = _num(_get(flat, "pad_diameter"))
            net = _get(flat, "net")
            board.vias.append({
                "net": board.net_name(net) if net is not None else None,
                "x": to_mm(x), "y": -to_mm(y),
                "drill": to_mm(hole) if hole else None,
                "diameter": to_mm(dia) if dia else None,
                "from": str(_get(flat, "layer", "") or ""),
                "to": None, "kind": "through",
            })
        elif otype in ("POUR", "POURED", "FILL", "REGION"):
            pts = _points_from(flat)
            if len(pts) < 3:
                unmapped.add(otype + ".points")
                continue
            keepout = otype == "REGION"
            net = _get(flat, "net")
            board.zones.append({
                "net": board.net_name(net) if net is not None else None,
                "layers": [str(_get(flat, "layer", "") or "")],
                "outline": [pt(p) for p in pts],
                "filled_polygons": [] if otype != "POURED" else [[pt(p) for p in pts]],
                "fill_mode": "solid",
                "priority": 0,
                "keepout": {"flags": payload.get("flags")} if keepout else None,
                "is_keepout": keepout,
                "origin_type": otype,
            })
        elif otype == "RULE":
            rtype = payload.get("ruleType") or payload.get("type")
            try:
                rtype = int(rtype)
            except (TypeError, ValueError):
                rtype = None
            if rtype == 3:
                w = _num(_get(flat, "width"))
                if w is not None:
                    board.rules.setdefault("track_width", {}).setdefault(
                        "values_mm", []).append(to_mm(w))
                    board.rules["track_width"]["source"] = "epcb RULE ruleType=3"
            elif rtype == 1:
                board.rules.setdefault("clearance", {})["raw"] = payload
                board.rules["clearance"]["source"] = "epcb RULE ruleType=1 (matrix, raw)"
        elif otype in ("LAYER", "ATTR", "STRING", "IMAGE", "OBJ", "PAD_NET", "TEARDROP"):
            unknown_types[otype] = unknown_types.get(otype, 0) + 1
        else:
            unknown_types[otype] = unknown_types.get(otype, 0) + 1

    # attach pads to their parent footprint when the log says who owns them
    unowned = []
    for oid, pad in pad_by_id.items():
        parent = pad.pop("ref", "")
        target = None
        for fp in board.footprints:
            if parent and fp.get("_id") == parent:
                target = fp
                break
        if target is None:
            unowned.append(pad)
        else:
            target["pads"].append(pad)
    if unowned:
        board.footprints.append({"ref": "", "value": "", "lib_id": "",
                                 "x": None, "y": None, "rotation": 0.0,
                                 "layer": "", "bbox": None, "pads": unowned,
                                 "_synthetic": "pads without a resolvable parent"})

    # board outline: POUR/REGION on the board-outline layer, else the widest contour
    outline_layer_hits = [z for z in board.zones
                          if str(z.get("layers", [""])[0]).lower().find("outline") >= 0]
    if outline_layer_hits:
        board.outline = [outline_layer_hits[0]["outline"]]
        board.cutouts = [z["outline"] for z in outline_layer_hits[1:]]
    else:
        board.declare_absent(
            "outline",
            "no board-outline contour found in this .epcb: EasyEDA Pro keeps the board "
            "outline in a dedicated layer/region whose key names this adapter has not "
            "confirmed against a real project (run epro_inspect)")

    board.source["unit_assumed"] = "%s mm per unit (0.01 inch)" % UNIT_TO_MM
    board.source["unit_verified"] = False
    board.source["log_stats"] = stats
    board.source["unknown_types"] = unknown_types

    bbox = _bbox(board)
    if bbox:
        board.source["bbox_sanity"] = {
            "width_mm": bbox[2] - bbox[0], "height_mm": bbox[3] - bbox[1]}

    board.mark("footprints", bool(board.footprints))
    board.mark("pads", bool(board.pads()))
    board.mark("tracks", bool(board.tracks))
    board.mark("vias", bool(board.vias))
    board.mark("zones", bool(board.zones))
    board.mark("nets", bool(board.nets))
    board.mark("outline", bool(board.outline))
    board.mark("cutouts", bool(board.cutouts))
    board.mark("zone_fill", any(z.get("origin_type") == "POURED" for z in board.zones),
               "pour fill lives in *.ecop, which this adapter does not read yet")
    board.mark("stackup", False,
               "layer stackup is in the LAYER lines; thickness/er are not carried "
               "unless the user filled the layer manager")
    board.mark("design_rules", bool(board.rules),
               "ruleType=3 widths and ruleType=1 matrix are captured; net classes are not")
    board.mark("pad_to_mask", False, "solder mask expansion not mapped")
    board.mark("component_3d", False, "3D models are .eblob members, not evaluated")
    for fact in sorted(unmapped):
        board.declare_absent(fact, "field names not matched by the candidate list; "
                                   "run epro_inspect on a real project and extend CANDIDATES")
    board.resolve_nets()
    return board


def _bbox(board):
    xs, ys = [], []
    for track in board.tracks:
        for point in (track.get("_points") or [track["start"], track["end"]]):
            xs.append(point[0])
            ys.append(point[1])
    for pad in board.pads():
        if pad.get("x") is not None and pad.get("y") is not None:
            xs.append(pad["x"])
            ys.append(pad["y"])
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))
