"""Part catalogue: the offline standard library that ships with LCEDA Pro, and the
same catalogue over the network.

Why this module exists
----------------------
Before it, the only way to get a real part (with a real LCSC part number, a real
symbol and a real footprint) was to copy one by hand. A model that invents a
footprint is worse than one that refuses, so this module reads the catalogue that
is already on the machine and says exactly where every field came from.

Two sources, both measured on a real install (2026-10-29)
---------------------------------------------------------
offline
    ``<LCEDA Pro>/resources/app/assets/db/lceda-std.elib`` is 461 MB but it is
    not a proprietary blob: the first 16 bytes are ``SQLite format 3``. It holds
    ``devices`` (16 828), ``components`` (21 310: docType 2 = symbol, 4 =
    footprint), per-device ``attributes`` (384 775 rows: Manufacturer Part,
    Supplier Part = the LCSC code, Supplier Footprint, Datasheet, Symbol uuid,
    Footprint uuid, ...) and an inverted index (``device_fts_inv_idx``) for
    search. Symbol and footprint geometry are the ``dataStr`` column: EasyEDA's
    own line-oriented document format, one JSON array per line, coordinates in
    mil (``["CANVAS",...,"mil",...]`` says so in the file itself).

online
    ``POST https://pro.lceda.cn/api/v5/devices/search`` with ``{"wd": ...}``
    answers 200 for anonymous callers (no cookie, no token) and covers the whole
    JLCPCB/LCSC catalogue (``count`` was 1 613 360 on 2026-10-29). What it does
    NOT give is geometry: a component record carries ``dataStrId`` +
    ``iv`` + ``key``, i.e. the document is encrypted and served from
    ``modules.lceda.cn``. This module therefore refuses to hand back an online
    symbol or footprint instead of guessing one - the std library on disk covers
    that, and a part outside it comes back with ``geometry: "not_offline"``.

What is NOT verified here
-------------------------
* the mil unit is read from ``["CANVAS"]`` in the footprint document; the symbol
  document has no CANVAS line, so its scale comes from the pin pitch (pins sit on
  a 10-unit grid, and 10 x 0.254 mm = 2.54 mm reproduces a standard 100 mil pitch)
* the rotation sign convention for a rotated pad is inferred from symmetric pads
  only (0 / 90 deg); a non-symmetric rotated polygon has not been checked against
  the app's own rendering
* EasyEDA layers 49/50/51 (COMPONENT_MARKING / PIN_SOLDERING / PIN_FLOATING) are
  dropped in conversion: KiCad derives mask and paste from the pad itself
"""

import json
import os
import re
import sqlite3
import time
import urllib.error
import urllib.request

MIL_MM = 0.0254

# A footprint document declares its own unit in the CANVAS row. Measured over the
# whole std library: 3 911 documents say "mil", 131 say "mm", 2 declare nothing.
# Both declared units are converted; an undeclared or unknown unit is refused
# rather than guessed, because a 39x error in a pad pitch is a wrong footprint.
UNIT_MM = {"mil": 0.0254, "thou": 0.0254, "mm": 1.0, "millimeter": 1.0,
           "cm": 10.0, "inch": 25.4, "in": 25.4}

# One EasyEDA symbol unit is 10 mil: pins sit on a 10-unit grid and a 64-pin MCU
# spans 430 units between its outermost pins, which at 0.254 mm/unit is the
# familiar 32 x 100 mil column.
SYMBOL_UNIT_MM = 0.254

# The LCEDA document HEAD carries a licence that requires every output to name
# "嘉立创EDA/EasyEDA官方库" and link both home pages. Kept here as well as in
# tools/lceda_fp.py (that tool must run without importing the package); a test
# asserts the two strings are identical so they cannot drift apart.
SOURCE_NOTE = ("Geometry from 嘉立创EDA/EasyEDA官方库 official library "
               "(https://lceda.cn/ , https://easyeda.com/)")

def with_source(text):
    """Append the mandatory source declaration, once (idempotent)."""
    d = " ".join((text or "").split())
    if "EasyEDA" in d or "嘉立创" in d:
        return d
    return (d + " | " + SOURCE_NOTE) if d else SOURCE_NOTE

STD_LIB_ENV = "PCB_AGENT_LCEDA_STD"
STD_LIB_REL = os.path.join("resources", "app", "assets", "db", "lceda-std.elib")

ONLINE_BASE = "https://pro.lceda.cn"
SEARCH_PATH = "/api/v5/devices/search"
COMPONENTS_BY_IDS_PATH = "/api/v5/components/searchByIds"

_CACHE_TTL_S = 24 * 3600

# layer numbers -> KiCad layer names (read out of a real footprint document)
LAYERS = {
    1: "F.Cu", 2: "B.Cu",
    3: "F.SilkS", 4: "B.SilkS",
    5: "F.Mask", 6: "B.Mask",
    7: "F.Paste", 8: "B.Paste",
    9: "F.Fab", 10: "B.Fab",
    11: "Edge.Cuts", 12: "*.Cu",
    13: "Cmts.User", 14: "Cmts.User",
}


class PartError(Exception):
    """A refusal, not a fallback: the catalogue could not answer."""

    def __init__(self, message, reason_code="PART_ERROR", hint=None):
        Exception.__init__(self, message)
        self.reason_code = reason_code
        self.hint = hint


# ------------------------------------------------------------------- library
def library_candidates(explicit=None):
    """Every place the standard library is looked for, in order."""
    out = []
    if explicit:
        out.append(("explicit", explicit))
    env = os.environ.get(STD_LIB_ENV)
    if env:
        out.append(("env:" + STD_LIB_ENV, env))
    # the exe tells us the install root; the library lives under it
    try:
        from . import desktop as _desktop
        found = _desktop.detect("lceda-pro")
        exe = (found or {}).get("exe") or (found or {}).get("path")
        if exe:
            root = os.path.dirname(exe)
            out.append(("detected:" + exe, os.path.join(root, STD_LIB_REL)))
    except Exception:  # noqa: BLE001 - detection is best effort, never fatal
        pass
    for drive in ("C:", "D:", "E:", "F:"):
        for sub in ("LCEDApro", "LCEDA-Pro", "Program Files/LCEDA Pro",
                    "Program Files (x86)/LCEDA Pro", "LcedaPro", "lceda-pro"):
            out.append(("guess", os.path.join(drive + os.sep, sub,
                                            "lceda-pro", STD_LIB_REL)))
    seen = set()
    uniq = []
    for how, path in out:
        norm = os.path.normcase(os.path.abspath(path))
        if norm in seen:
            continue
        seen.add(norm)
        uniq.append((how, path))
    return uniq


def find_library(explicit=None):
    """Return {'path', 'how', 'size'} for the first candidate that exists.

    An explicit path that does not exist is an error, not a reason to fall back
    to whatever else happens to be installed: the caller named a file, so a
    different file answering would be the wrong answer.
    """
    tried = []
    if explicit and not os.path.isfile(explicit):
        raise PartError(
            "the library you named does not exist: %s (looked in 1 place)"
            % explicit, "LIBRARY_NOT_FOUND",
            hint="set %s to a real lceda-std.elib, or drop the path and let it "
                 "be detected" % STD_LIB_ENV)
    for how, path in library_candidates(explicit):
        tried.append(path)
        if os.path.isfile(path):
            return {"path": path, "how": how, "size": os.path.getsize(path)}
    raise PartError(
        "the LCEDA Pro standard library (lceda-std.elib) was not found; looked in "
        "%d places, e.g. %s" % (len(tried), tried[0] if tried else "nothing"),
        "LIBRARY_NOT_FOUND",
        hint="pass path= explicitly, or set %s to the .elib file; it ships with "
             "LCEDA Pro under resources/app/assets/db/" % STD_LIB_ENV)


def _connect(path):
    # immutable=1: never create -wal/-shm next to an app's file, never write
    uri = "file:%s?mode=ro&immutable=1" % path.replace("\\", "/").replace("?", "%3f")
    con = sqlite3.connect(uri, uri=True, timeout=5.0)
    con.row_factory = sqlite3.Row
    return con


def library_status(explicit=None):
    info = find_library(explicit)
    con = _connect(info["path"])
    try:
        counts = {}
        for table, label in (("devices", "devices"), ("components", "components")):
            counts[label] = con.execute(
                "select count(*) from %s" % table).fetchone()[0]
        counts["symbols"] = con.execute(
            "select count(*) from components where docType=2").fetchone()[0]
        counts["footprints"] = con.execute(
            "select count(*) from components where docType=4").fetchone()[0]
    finally:
        con.close()
    info = dict(info)
    info["counts"] = counts
    return info


# ------------------------------------------------------------------- search
def _device_out(row, attrs):
    a = dict(attrs or {})
    return {
        "uuid": row["uuid"],
        "title": row["display_title"] or row["title"],
        "description": row["description"] or "",
        "category": {
            "parent": _tag_name(row, "parent_tag"),
            "child": _tag_name(row, "child_tag"),
        },
        "lcsc": a.get("Supplier Part"),
        "mpn": a.get("Manufacturer Part"),
        "manufacturer": a.get("Manufacturer"),
        "package": a.get("Supplier Footprint"),
        "datasheet": a.get("Datasheet"),
        "jlc_class": a.get("JLCPCB Part Class"),
        "symbol_uuid": a.get("Symbol"),
        "footprint_uuid": a.get("Footprint"),
        "attributes": a,
    }


def _tag_name(row, key):
    raw = row[key] if key in row.keys() else None
    if not raw:
        return None
    try:
        d = json.loads(raw)
    except Exception:  # noqa: BLE001
        return raw
    return d.get("name_cn") or d.get("name")


def search_offline(query, limit=20, page=1, path=None):
    info = find_library(path)
    con = _connect(info["path"])
    try:
        q = (query or "").strip().lower()
        if not q:
            raise PartError("search needs a non-empty query", "ARG_INVALID")
        offset = max(0, (int(page) - 1) * int(limit))
        # title / text first, then the attribute table: the LCSC code and the
        # manufacturer part number live in attributes, not in the title
        where = ("from devices d where lower(d.display_title) like :q "
                 "or lower(d.title) like :q or lower(d.description) like :q "
                 "or exists (select 1 from attributes a where a.device_uuid=d.uuid "
                 "and lower(a.value) like :q)")
        like = "%" + q + "%"
        sql = ("select d.uuid,d.title,d.display_title,d.description,"
               "d.parent_tag,d.child_tag " + where +
               " order by length(d.display_title) asc limit :lim offset :off")
        rows = con.execute(sql, {"q": like, "lim": int(limit),
                                "off": offset}).fetchall()
        total = con.execute("select count(*) " + where, {"q": like}).fetchone()[0]
        parts = []
        for row in rows:
            attrs = {}
            for a in con.execute(
                    "select key,value from attributes where device_uuid=?",
                    (row["uuid"],)):
                attrs[a["key"]] = a["value"]
            parts.append(_device_out(row, attrs))
    finally:
        con.close()
    return {"source": "offline", "library": info["path"], "query": query,
            "total": total, "page": int(page), "parts": parts}


def _http_json(url, body, timeout=20.0):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST", headers={
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (pcb_agent_mcp)",
        "Origin": ONLINE_BASE,
        "Referer": ONLINE_BASE + "/",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        raise PartError("online catalogue answered HTTP %d for %s"
                        % (exc.code, url), "ONLINE_HTTP_%d" % exc.code)
    except Exception as exc:  # noqa: BLE001
        raise PartError("online catalogue unreachable (%s: %s)"
                        % (type(exc).__name__, exc), "ONLINE_UNREACHABLE")
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:  # noqa: BLE001
        raise PartError("online catalogue returned non-JSON (%d bytes)"
                        % len(raw), "ONLINE_BAD_JSON")


def search_online(query, limit=20, page=1, timeout=20.0):
    out = _http_json(ONLINE_BASE + SEARCH_PATH,
                     {"wd": query, "page": int(page), "pageSize": int(limit)},
                     timeout=timeout)
    if not out.get("success", True):
        raise PartError("online catalogue refused the query: %s"
                        % out.get("message"), "ONLINE_REFUSED",
                        hint="code=%s" % out.get("code"))
    res = out.get("result") or {}
    lists = res.get("lists") or {}
    groups = []
    for key in ("lcsc", "mine", "user"):
        for d in lists.get(key) or []:
            groups.append((key, d))
    parts = []
    for group, d in groups:
        a = dict(d.get("attributes") or {})
        parts.append({
            "uuid": d.get("uuid"),
            "group": group,
            "title": d.get("display_title") or d.get("title"),
            "description": d.get("description") or "",
            "category": {
                "parent": ((d.get("tags") or {}).get("parent_tag") or {}).get("name_cn"),
                "child": ((d.get("tags") or {}).get("child_tag") or {}).get("name_cn"),
            },
            "lcsc": a.get("Supplier Part") or d.get("product_code"),
            "mpn": a.get("Manufacturer Part"),
            "manufacturer": a.get("Manufacturer"),
            "package": a.get("Supplier Footprint"),
            "datasheet": a.get("Datasheet"),
            "jlc_class": a.get("JLCPCB Part Class"),
            "symbol_uuid": a.get("Symbol"),
            "footprint_uuid": a.get("Footprint"),
            "attributes": a,
        })
    return {"source": "online", "endpoint": ONLINE_BASE + SEARCH_PATH,
            "query": res.get("wd", query), "total": res.get("count"),
            "page": res.get("page") or int(page),
            "page_size": res.get("pageSize"), "parts": parts}


def search(query, limit=20, page=1, source="auto", path=None, timeout=20.0):
    """Offline first (it carries geometry), online second (it carries the whole
    catalogue). In ``auto`` an offline miss is not an answer: the search moves on
    and the reply says which sources were tried and why one was passed over."""
    order = {"auto": ("offline", "online"), "offline": ("offline",),
             "online": ("online",)}.get(source)
    if order is None:
        raise PartError("unknown source %r" % (source,), "ARG_INVALID",
                        hint="one of: auto, offline, online")
    tried = []
    for name in order:
        try:
            if name == "offline":
                out = search_offline(query, limit=limit, page=page, path=path)
            else:
                out = search_online(query, limit=limit, page=page, timeout=timeout)
        except PartError as exc:
            tried.append({"source": name, "ok": False,
                          "reason_code": exc.reason_code, "message": str(exc)})
            continue
        tried.append({"source": name, "ok": True, "parts": len(out["parts"])})
        if out["parts"] or source != "auto":
            out["tried"] = tried
            return out
    if tried and any(t.get("ok") for t in tried):
        last = [t for t in tried if t.get("ok")][-1]
        if last["source"] == "offline":
            out = search_offline(query, limit=limit, page=page, path=path)
        else:
            out = search_online(query, limit=limit, page=page, timeout=timeout)
        out["tried"] = tried
        return out
    raise PartError("no source could answer %r" % (query,), "NO_SOURCE",
                    hint=json.dumps(tried, ensure_ascii=False))


# --------------------------------------------------------------- device/data
def get_offline(uuid, path=None):
    info = find_library(path)
    con = _connect(info["path"])
    try:
        row = con.execute(
            "select uuid,title,display_title,description,parent_tag,child_tag,"
            "footprint_type,symbol_type from devices where uuid=?",
            (uuid,)).fetchone()
        if row is None:
            raise PartError("no device with uuid %s in the offline library" % uuid,
                            "PART_NOT_FOUND", hint=info["path"])
        attrs = {}
        for a in con.execute("select key,value from attributes where device_uuid=?",
                            (uuid,)):
            attrs[a["key"]] = a["value"]
        out = _device_out(row, attrs)
        out["footprint_type"] = row["footprint_type"]
        out["symbol_type"] = row["symbol_type"]
        out["source"] = "offline"
    finally:
        con.close()
    return out


def get(uuid, source="auto", path=None, timeout=20.0):
    """One device by uuid. Online can only be looked up through the search index,
    so an online-only uuid must be resolved with ``search`` first."""
    if source in ("auto", "offline"):
        try:
            return get_offline(uuid, path=path)
        except PartError:
            if source == "offline":
                raise
    raise PartError(
        "device %s is not in the offline library; the online catalogue has no "
        "by-uuid lookup, so it can only be reached through part_search" % uuid,
        "PART_NOT_FOUND_OFFLINE")


def data_strings(uuids, path=None):
    """dataStr for component uuids (symbols docType 2, footprints docType 4)."""
    info = find_library(path)
    con = _connect(info["path"])
    out = {}
    try:
        for uuid in uuids:
            row = con.execute(
                "select uuid,title,display_title,docType,dataStr from components "
                "where uuid=?", (uuid,)).fetchone()
            if row is None:
                out[uuid] = None
                continue
            out[uuid] = {"uuid": row["uuid"], "title": row["title"],
                         "display_title": row["display_title"],
                         "doc_type": row["docType"], "data": row["dataStr"]}
    finally:
        con.close()
    return out


# ------------------------------------------------------- document primitives
def _lines(data):
    for line in (data or "").split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        # a real library contains the odd empty row; a row with no type tag
        # carries no information, so it is skipped rather than crashing
        if isinstance(row, list) and row:
            yield row


def _num(value, scale, default=0.0):
    try:
        return round(float(value) * scale, 6)
    except (TypeError, ValueError):
        return default


def _flat_points(raw):
    """Authoritative reader for the flat [x, y, "L", x, y, ...] form."""
    nums = [x for x in (raw or []) if isinstance(x, (int, float))]
    return [(nums[i], nums[i + 1]) for i in range(0, len(nums) - 1, 2)]


def parse_footprint_doc(data):
    """Read a footprint document into pads / graphics / attributes.

    Coordinates stay in the document's own unit; ``doc["scale"]`` is the factor
    that turns them into mm (None when the unit is unknown, which callers must
    treat as a refusal).
    """
    doc = {"inches_declared": None, "unit": None, "scale": None, "pads": [],
           "graphics": [], "texts": [], "layers": {}, "note": []}
    pads = {}
    for row in _lines(data):
        kind = row[0]
        if kind == "CANVAS":
            doc["unit"] = row[3]
        elif kind == "LAYER":
            doc["layers"][row[1]] = row[2]
        elif kind == "PAD":
            pad = _parse_pad(row)
            if pad:
                pads[pad["id"]] = pad
                doc["pads"].append(pad)
        elif kind in ("POLY", "FILL", "RECT", "CIRCLE", "ARC", "ELLIPSE"):
            g = _parse_graphic(kind, row)
            if g:
                doc["graphics"].append(g)
        elif kind == "ATTR":
            txt = _parse_attr(row)
            if txt:
                doc["texts"].append(txt)
        elif kind == "STRING":
            txt = _parse_string(row)
            if txt:
                doc["texts"].append(txt)
        elif kind == "VIA":
            if "via" not in doc["note"]:
                doc["note"].append("via")
        elif kind == "CONNECT":
            continue
        elif kind in ("DOCTYPE", "ACTIVE_LAYER", "LINESTYLE", "FONTSTYLE",
                      "HEAD"):
            continue
        else:
            if kind not in doc["note"]:
                doc["note"].append(kind)
    if doc["unit"] is None:
        doc["note"].append("no CANVAS line: unit unknown")
    else:
        doc["scale"] = UNIT_MM.get(str(doc["unit"]).strip().lower())
        if doc["scale"] is None:
            doc["note"].append("unit:%s" % doc["unit"])
    return doc


def _parse_pad(row):
    if len(row) < 11:
        return None
    layer = row[4]
    number = row[5]
    x, y = row[6], row[7]
    rot = row[8]
    hole = row[9]
    shape = row[10]
    rest = row[12:]
    extra_rot = None
    if len(rest) >= 3:
        extra_rot = rest[2]
    shape_kind = shape[0] if isinstance(shape, list) and shape else None
    pad = {"id": row[1], "layer": LAYERS.get(layer, "F.Cu"), "layer_number": layer,
           "number": str(number) if number is not None else "",
           "x": x, "y": y, "rot": rot, "shape": shape_kind,
           "hole": None, "size": None, "polygon": None,
           "shape_rot": extra_rot}
    # Two angle fields exist. Field 8 is constant across every pad of a footprint
    # (0 on c0402, 180 on led0805, 270 on res-adj-th); field 14 is the one that
    # turns an SO-8's 0.574 x 1.887 oval into a 1.887 x 0.574 pad, i.e. the one
    # the geometry needs. The converter therefore uses field 14 and reports
    # field 8 without acting on it.
    if isinstance(hole, list) and hole:
        pad["hole"] = {"shape": hole[0], "w": hole[1],
                       "h": hole[2] if len(hole) > 2 else hole[1]}
    if isinstance(shape, list) and len(shape) >= 3:
        if shape_kind == "POLY":
            pts = shape[1] if isinstance(shape[1], list) else shape[1:]
            pad["polygon"] = _flat_points(pts if isinstance(pts, list) else [])
        else:
            pad["size"] = (shape[1], shape[2])
            if len(shape) >= 4 and isinstance(shape[3], (int, float)):
                pad["corner_radius"] = shape[3]
    return pad


def _parse_graphic(kind, row):
    layer = row[4]
    if kind == "POLY":
        width = row[5]
        pts = _flat_points(row[6])
        return {"kind": "poly", "layer": LAYERS.get(layer, "Cmts.User"),
                "layer_number": layer, "width": width, "points": pts, "closed": False}
    if kind == "FILL":
        width = row[5]
        raw = row[7]
        if raw and isinstance(raw[0], list):
            sub = raw[0]
            if sub and sub[0] == "CIRCLE":
                return {"kind": "circle", "layer": LAYERS.get(layer, "Cmts.User"),
                        "layer_number": layer, "center": (sub[1], sub[2]),
                        "radius": sub[3], "width": width}
        return {"kind": "fill", "layer": LAYERS.get(layer, "Cmts.User"),
                "layer_number": layer, "points": _flat_points(raw), "closed": True}
    if kind == "RECT":
        return {"kind": "rect", "layer": LAYERS.get(layer, "Cmts.User"),
                "layer_number": layer, "start": (row[2], row[3]),
                "end": (row[4], row[5])}
    if kind == "CIRCLE":
        return {"kind": "circle", "layer": LAYERS.get(layer, "Cmts.User"),
                "layer_number": layer, "center": (row[5], row[6]),
                "radius": row[7], "width": row[3] if len(row) > 3 else 0}
    return None


def _parse_attr(row):
    # Footprint form: ["ATTR", id, 0, "", layer, null, null, key, text, ...]
    # Symbol form:    ["ATTR", id, parent, key, text, ...]   (row[3] holds the key)
    if len(row) < 5:
        return None
    if isinstance(row[3], str) and row[3]:
        return {"id": row[1], "parent": row[2], "layer": None,
                "layer_number": None, "key": row[3], "text": row[4]}
    if len(row) < 9:
        return None
    return {"id": row[1], "parent": row[2], "layer": LAYERS.get(row[4], None),
            "layer_number": row[4], "key": row[7], "text": row[8]}

def _parse_string(row):
    # ["STRING", id, 0, layer, x, y, text, font, size, ...]
    if len(row) < 7:
        return None
    return {"id": row[1], "parent": None, "layer": LAYERS.get(row[3], None),
            "layer_number": row[3], "key": "STRING", "text": row[6],
            "x": row[4], "y": row[5]}


def _scale_of(doc):
    if doc.get("scale") is None:
        raise PartError(
            "footprint document declares unit %r, which has not been verified; "
            "refusing rather than guessing a 39x scale error"
            % (doc.get("unit") or "(none)"), "UNIT_UNVERIFIED",
            hint="known units: mil, mm (measured over the whole std library)")
    return doc["scale"]

def footprint_pads_mm(data):
    """Pads in mm with the fields a layout needs. No conversion to KiCad here.

    ``rot`` is the shape rotation (document field 14), i.e. the angle that turns
    the pad's w/h box; field 8 is constant per footprint and is only reported.
    """
    doc = parse_footprint_doc(data)
    s = _scale_of(doc)
    out = []
    for pad in doc["pads"]:
        item = {"number": pad["number"], "x": _num(pad["x"], s),
                "y": _num(pad["y"], s), "layer": pad["layer"],
                "shape": pad["shape"],
                "rot": int(pad.get("shape_rot") or 0)}
        if pad["size"]:
            item["w"] = _num(pad["size"][0], s)
            item["h"] = _num(pad["size"][1], s)
        if pad["hole"]:
            item["drill"] = _num(pad["hole"]["w"], s)
        if pad["polygon"]:
            item["polygon"] = [(_num(a, s), _num(b, s))
                               for a, b in pad["polygon"]]
        out.append(item)
    return out


# ------------------------------------------------------ KiCad: footprint
def _kicad_pad_shape(pad):
    kind = (pad.get("shape") or "").upper()
    if kind == "RECT":
        return "rect"
    if kind == "OVAL":
        return "oval"
    if kind in ("ELLIPSE", "ROUND", "CIRCLE"):
        size = pad.get("size") or (0, 0)
        if abs(size[0] - size[1]) < 1e-9:
            return "circle"
        return "oval"
    if kind == "POLY":
        return "custom"
    return "rect"


def footprint_to_kicad(data, name=None, reference="REF**", value=None,
                       description=None, extra_note=None):
    """EasyEDA Pro footprint document -> .kicad_mod text.

    Returns (text, report). The report lists what was carried and what was
    dropped, because a silently thinner footprint is a wrong footprint.
    """
    doc = parse_footprint_doc(data)
    s = _scale_of(doc)
    report = {"pads": 0, "graphics": 0, "texts": 0, "dropped": [],
              "unit": doc["unit"], "unknown_rows": doc["note"]}
    name = name or "part"
    value = value or name
    thr = [p for p in doc["pads"] if p["hole"]]
    smd = [p for p in doc["pads"] if not p["hole"]]
    attr = "smd" if smd and not thr else ("through_hole" if thr and not smd
                                          else "smd through_hole")
    lines = ['(footprint "%s"' % _kq(name)]
    lines.append('  (version 20240108)')
    lines.append('  (generator "pcb_agent_mcp")')
    lines.append('  (generator_version "0.0.13")')
    lines.append('  (layer "F.Cu")')
    if description or extra_note:
        lines.append('  (descr "%s")' % _kq(" ".join(
            x for x in (description, extra_note) if x)))
    lines.append('  (attr %s)' % attr)
    lines.append('  (fp_text reference "%s" (at 0 -%s) (layer "F.SilkS")'
                 % (_kq(reference), _fmt(0)))
    lines.append('    (effects (font (size 1 1) (thickness 0.15))))')
    lines.append('  (fp_text value "%s" (at 0 %s) (layer "F.Fab")'
                 % (_kq(value), _fmt(0)))
    lines.append('    (effects (font (size 1 1) (thickness 0.15))))')

    for g in doc["graphics"]:
        text = _kicad_graphic(g, report, s)
        if text:
            lines.extend(text)

    for pad in doc["pads"]:
        block = _kicad_pad(pad, report, s)
        if block:
            lines.extend(block)

    for t in doc["texts"]:
        if t["key"] in ("Footprint", "Designator"):
            report["texts"] += 1
            continue
        report["dropped"].append("text:%s@layer-%s" % (t["key"], t["layer_number"]))
    dropped = sorted(set(report["dropped"]))
    report["dropped"] = dropped
    if "pad_angle_field8" in report:
        report["pad_angle_field8"] = sorted(set(report["pad_angle_field8"]))
    lines.append(")")
    return "\n".join(lines) + "\n", report


def _kq(text):
    return str(text).replace("\\", "\\\\").replace('"', '\\"')


def _fmt(value):
    return ("%.6f" % float(value)).rstrip("0").rstrip(".") or "0"


def _kicad_layers_for(pad):
    name = pad["layer"]
    if name == "*.Cu":
        return '"*.Cu" "*.Mask"'
    if name.startswith("B."):
        base = name[2:]
        return '"B.Cu" "B.%s" "B.Mask"' % base
    base = name[2:] if name.startswith("F.") else name
    return '"F.Cu" "F.%s" "F.Mask"' % base


def _rotate(x, y, deg):
    import math
    a = math.radians(deg or 0)
    ca, sa = math.cos(a), math.sin(a)
    return (x * ca - y * sa, x * sa + y * ca)


def _kicad_pad(pad, report, scale=MIL_MM):
    shape = _kicad_pad_shape(pad)
    x = _num(pad["x"], scale)
    y = _num(pad["y"], scale)
    rot = pad.get("shape_rot") or 0
    size = pad.get("size") or (0, 0)
    w = _num(size[0], scale)
    h = _num(size[1], scale)
    number = pad["number"] or ""
    layers = _kicad_layers_for(pad)
    drill = pad.get("hole")
    head = '  (pad "%s" %s' % (_kq(number), "thru_hole" if drill else "smd")
    lines = ['%s %s (at %s %s%s)' % (
        head, shape, _fmt(x), _fmt(y),
        (" %s" % _fmt(rot)) if rot else "")]
    if shape == "custom":
        lines = ['%s custom (at %s %s%s)' % (
            head, _fmt(x), _fmt(y), (" %s" % _fmt(rot)) if rot else "")]
        lines.append('    (size 0 0) (layers %s)' % layers)
        pts = " ".join('(xy %s %s)' % (_fmt(_num(px, scale)),
                                       _fmt(_num(py, scale)))
                       for px, py in (pad.get("polygon") or []))
        lines.append('    (options (clearance outline) (anchor rect))')
        lines.append('    (primitives (gr_poly (pts %s)' % pts)
        lines.append('      (width 0) (fill yes) (layer "F.Cu")))')
        lines.append('  )')
        report["pads"] += 1
        return lines
    lines.append('    (size %s %s) (layers %s)' % (_fmt(w), _fmt(h), layers))
    if pad.get("rot"):
        # field 8: constant per footprint, not applied (see _parse_pad)
        report.setdefault("pad_angle_field8", []).append(pad["rot"])
    if shape == "rect" and pad.get("corner_radius"):
        lines.append('    (roundrect_rratio 0.25)')
    if drill:
        d = drill
        dw = _num(d["w"], scale)
        dh = _num(d["h"], scale)
        if abs(dw - dh) < 1e-9:
            lines.append('    (drill %s)' % _fmt(dw))
        else:
            lines.append('    (drill oval %s %s)' % (_fmt(dw), _fmt(dh)))
    lines.append('  )')
    report["pads"] += 1
    return lines


# Layers KiCad derives on its own (mask/paste) or has no equivalent for.
DROP_LAYERS = {
    47: "through-hole marker", 48: "component body", 49: "component marking",
    50: "pin soldering", 51: "pin floating", 52: "component model",
}
# Kept, but not as graphics: the outline belongs to Edge.Cuts only when the
# footprint is a board outline, and 13/14 are documentation layers.
USER_LAYERS = (13, 14)

def _kicad_graphic(g, report, scale=MIL_MM):
    layer = g["layer"]
    number = g["layer_number"]
    if number in DROP_LAYERS:
        report["dropped"].append("graphic-on-layer-%d" % number)
        return None
    if number in USER_LAYERS or layer in ("Cmts.User", "Edge.Cuts"):
        report["dropped"].append("graphic-on-layer-%s" % number)
        return None
    kind = g["kind"]
    if kind == "poly":
        pts = [(_num(a, scale), _num(b, scale)) for a, b in g["points"]]
        if len(pts) < 2:
            return None
        width = _num(g["width"], scale)
        out = []
        for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
            out.append('  (fp_line (start %s %s) (end %s %s)'
                       % (_fmt(x1), _fmt(y1), _fmt(x2), _fmt(y2)))
            out.append('    (stroke (width %s) (type solid)) (layer "%s"))'
                       % (_fmt(width), layer))
        report["graphics"] += 1
        return out
    if kind == "fill":
        pts = [(_num(a, scale), _num(b, scale)) for a, b in g["points"]]
        if len(pts) < 3:
            return None
        body = " ".join("(xy %s %s)" % (_fmt(px), _fmt(py)) for px, py in pts)
        report["graphics"] += 1
        return ['  (fp_poly (pts %s) (stroke (width 0) (type solid))'
                ' (fill solid) (layer "%s"))' % (body, layer)]
    if kind == "circle":
        cx, cy = g["center"]
        report["graphics"] += 1
        return ['  (fp_circle (center %s %s) (end %s %s)'
                ' (stroke (width %s) (type solid)) (fill solid) (layer "%s"))'
                % (_fmt(_num(cx, scale)), _fmt(_num(cy, scale)),
                   _fmt(_num(cx, scale) + _num(g["radius"], scale)),
                   _fmt(_num(cy, scale)),
                   _fmt(_num(g.get("width") or 0, scale)), layer)]
    if kind == "rect":
        x1, y1 = g["start"]
        x2, y2 = g["end"]
        report["graphics"] += 1
        return ['  (fp_rect (start %s %s) (end %s %s)'
                ' (stroke (width 0.1) (type solid)) (fill none) (layer "%s"))'
                % (_fmt(_num(x1, scale)), _fmt(_num(y1, scale)),
                   _fmt(_num(x2, scale)), _fmt(_num(y2, scale)), layer)]
    report["dropped"].append("graphic:" + kind)
    return None


# --------------------------------------------------------- KiCad: symbol
PIN_ROT = {0: 0, 90: 90, 180: 180, 270: 270, -90: 270, -180: 180, -270: 90}


def parse_symbol_doc(data):
    doc = {"pins": [], "shapes": [], "attrs": [], "github": [], "note": []}
    pins = {}
    attrs = []
    for row in _lines(data):
        kind = row[0]
        if kind == "PIN":
            pin = {"id": row[1], "x": row[4], "y": row[5], "length": row[6],
                   "rot": row[7], "name": "", "number": "", "electrical": None}
            pins[pin["id"]] = pin
            doc["pins"].append(pin)
        elif kind == "ATTR":
            # symbol form: ["ATTR", id, parent, key, text, ...]
            if len(row) >= 5 and isinstance(row[3], str) and row[3]:
                attrs.append({"id": row[1], "parent": row[2], "key": row[3],
                              "text": row[4]})
        elif kind in ("RECT", "POLYLINE", "CIRCLE", "ELLIPSE", "ARC", "POLY",
                      "BEZIER", "TEXT"):
            doc["shapes"].append({"kind": kind, "row": row})
        elif kind in ("DOCTYPE", "HEAD", "PART", "LINESTYLE", "FONTSTYLE",
                      "CANVAS", "ACTIVE_LAYER", "LAYER"):
            continue
        else:
            if kind not in doc["note"]:
                doc["note"].append(kind)
    for a in attrs:
        pin = pins.get(a["parent"])
        if pin is not None:
            if a["key"] == "NAME":
                pin["name"] = a["text"]
            elif a["key"] == "NUMBER":
                pin["number"] = a["text"]
            elif a["key"] == "Pin Type":
                pin["electrical"] = a["text"]
        else:
            doc["attrs"].append(a)
    return doc


PIN_TYPE = {
    "IN": "input", "OUT": "output", "BI": "bidirectional",
    "TRI": "tri_state", "PASSIVE": "passive", "PWR": "power_in",
    "GND": "power_in", "OPEN": "open_collector", "HIZ": "no_connect",
    "CLK": "input", "NC": "no_connect", "UNDEFINED": "unspecified",
}


def symbol_to_kicad(data, name=None, reference="U", footprint="",
                    datasheet="", description="", hide_pin_names=False):
    """EasyEDA Pro symbol document -> .kicad_sym text (one symbol)."""
    doc = parse_symbol_doc(data)
    if not doc["pins"]:
        raise PartError("symbol document has no PIN rows; nothing to convert",
                        "SYMBOL_EMPTY")
    name = name or "part"
    description = with_source(description)
    s = SYMBOL_UNIT_MM
    report = {"pins": len(doc["pins"]), "shapes": 0, "dropped": [],
              "unit_mm": s, "unknown_rows": doc["note"]}
    lines = ["(kicad_symbol_lib", "  (version 20241209)",
             '  (generator "pcb_agent_mcp")',
             '  (generator_version "0.0.13")',
             '  (symbol "%s"' % _kq(name)]
    top = _extent_top(doc, s)      # above the drawing, never on the pins
    for label, text, k in (("Reference", reference, 6.35), ("Value", name, 3.81)):
        lines.append('    (property "%s" "%s" (at 0 %s 0) (effects (font (size 1.27 1.27)) (justify left)))'
                     % (_kq(label), _kq(text), _fmt(top + k)))
    lines.append('    (property "Footprint" "%s" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))'
                 % _kq(footprint))
    lines.append('    (property "Datasheet" "%s" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))'
                 % _kq(datasheet))
    if description:
        lines.append('    (property "Description" "%s" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))'
                     % _kq(description))
    for a in doc["attrs"]:
        if a["key"] in ("Symbol", "Designator"):
            continue
        lines.append('    (property "%s" "%s" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))'
                     % (_kq(a["key"]), _kq(a["text"])))
    if hide_pin_names:
        # A BGA ball name is a four-function datasheet string ("ETH_RMII0_MDC/
        # DSM_AUD_LN_M1/SAI3_SDI/GPIO2_B6_d", 53 mm wide at 1.27 mm text). It is
        # wider than the space between any two pin columns, so it lands on the
        # net labels. The name stays in the library and in the frozen CSV; what
        # the sheet shows per pin is the ball number and the net.
        lines.append('    (pin_names (offset 0.508) (hide yes))')
    lines.append('    (symbol "%s_0_1"' % _kq(name))
    for shape in doc["shapes"]:
        block = _kicad_symbol_shape(shape, s, report)
        if block:
            lines.extend(block)
    stacked = separate_stacked_pins(doc["pins"])
    for pin in doc["pins"]:
        lines.extend(_kicad_symbol_pin(pin, s, report))
    lines.append("    )")
    lines.append("  )")
    lines.append(")")
    report["dropped"] = sorted(set(report["dropped"]))
    report["stacked_separated"] = stacked
    return "\n".join(lines) + "\n", report


def _extent_top(doc, s):
    """Symbol-space y of the highest drawn thing, so fields go above it.

    Measured on the RK3506 sheet: the two fields defaulted to (0, 5.08) and
    (0, 0) -- the middle of the body -- and printed the part name on top of its
    own ``GND``/``VSS`` pins. Seven parts were doing it.
    """
    ys = [2.54 * 2]               # a symbol with no shapes still needs a row
    for shape in doc["shapes"]:
        row, kind = shape["row"], shape["kind"]
        if kind == "RECT" and len(row) >= 6:
            ys.append(max(_num(row[3], s), _num(row[5], s)))
        elif kind in ("POLY", "POLYLINE"):
            raw = row[2] if kind == "POLY" else (row[5] if len(row) > 5 else [])
            ys.extend(_num(v, s) for v in _flat_points(raw)[1::2])
        elif kind == "CIRCLE" and len(row) >= 5:
            ys.append(_num(row[3], s) + _num(row[4], s))
    for pin in doc["pins"]:
        ys.append(_num(pin["y"], s) + 2.54)
    return max(ys)

def _kicad_symbol_shape(shape, s, report):
    row = shape["row"]
    kind = shape["kind"]
    if kind == "RECT":
        x1, y1, x2, y2 = row[2], row[3], row[4], row[5]
        report["shapes"] += 1
        return ['      (rectangle (start %s %s) (end %s %s)'
                % (_fmt(_num(x1, s)), _fmt(_num(y1, s)),
                   _fmt(_num(x2, s)), _fmt(_num(y2, s))),
                '        (stroke (width 0.254) (type solid)) (fill (type background)))']
    if kind in ("POLYLINE", "POLY"):
        # symbol POLY: ["POLY", id, [x,y,x,y,...], 0, style, 0] (points at index 2)
        raw = row[2] if kind == "POLY" else (row[5] if len(row) > 5 else [])
        pts = _flat_points(raw)
        if len(pts) < 2:
            report["dropped"].append("empty-polyline")
            return None
        body = " ".join("(xy %s %s)" % (_fmt(_num(a, s)), _fmt(_num(b, s)))
                        for a, b in pts)
        report["shapes"] += 1
        return ['      (polyline (pts %s)' % body,
                '        (stroke (width 0.254) (type solid)) (fill (type none)))']
    if kind == "CIRCLE":
        # symbol CIRCLE: ["CIRCLE", id, cx, cy, r, style, 0]
        cx, cy, radius = row[2], row[3], row[4]
        report["shapes"] += 1
        return ['      (circle (center %s %s) (radius %s)'
                % (_fmt(_num(cx, s)), _fmt(_num(cy, s)), _fmt(_num(radius, s))),
                '        (stroke (width 0.254) (type solid)) (fill (type none)))']
    if kind == "ARC":
        # symbol ARC: three points on the arc, start / mid / end (verified: the
        # three points of 82 real arcs are concyclic).
        pts = [(row[2], row[3]), (row[4], row[5]), (row[6], row[7])]
        report["shapes"] += 1
        return ['      (arc (start %s %s) (mid %s %s) (end %s %s)'
                % (_fmt(_num(pts[0][0], s)), _fmt(_num(pts[0][1], s)),
                   _fmt(_num(pts[1][0], s)), _fmt(_num(pts[1][1], s)),
                   _fmt(_num(pts[2][0], s)), _fmt(_num(pts[2][1], s))),
                '        (stroke (width 0.254) (type solid)) (fill (type none)))']
    if kind == "TEXT":
        # symbol TEXT: ["TEXT", id, x, y, rot, "G", style]
        report["shapes"] += 1
        return ['      (text "%s" (at %s %s 0)'
                % (_kq(row[5]), _fmt(_num(row[2], s)), _fmt(_num(row[3], s))),
                '        (effects (font (size 1.27 1.27))))']
    report["dropped"].append("symbol-shape:" + kind)
    return None


def _kicad_symbol_pin(pin, s, report):
    rot = PIN_ROT.get(int(pin["rot"] or 0) % 360, 0)
    etype = PIN_TYPE.get((pin.get("electrical") or "IN").upper(), "unspecified")
    name = pin.get("name") or ""
    number = pin.get("number") or ""
    length = _num(pin.get("length"), s)
    return ['      (pin %s line (at %s %s %s) (length %s)'
            % (etype, _fmt(_num(pin["x"], s)), _fmt(_num(pin["y"], s)),
               rot, _fmt(length or s * 10)),
            '        (name "%s" (effects (font (size 1.27 1.27))))' % _kq(name),
            '        (number "%s" (effects (font (size 1.27 1.27)))))' % _kq(number)]


# ------------------------------------------------ stacked (colinear) pins
_PIN_OUTWARD = {0: (-1, 0), 180: (1, 0), 90: (0, 1), 270: (0, -1)}


def separate_stacked_pins(pins, step_raw=40):
    """Split pins that sit on the *same* point, outward, and say which moved.

    EasyEDA symbols stack pins to save space -- here 32 ``VSS`` balls are drawn
    on top of a power ball each. Two pins on one point cannot carry two nets:
    a label on that point shorts ``GND`` to ``+0V9_LOGIC`` (or similar).  So
    the duplicates are pushed outward along their own pin direction, one step
    (``step_raw`` * ``SYMBOL_UNIT_MM``) at a time, until the point is free.

    The step defaults to four grids (10.16 mm). The two pins keep the same row,
    so what travels between them is text: the inner pin's net label (``GND`` is
    3.05 mm) and the outer pin's ball number (~3.5 mm). Measured on the RK3506
    sheet, one grid (2.54 mm) put the labels on top of each other, two grids
    (5.08 mm) put the number on top of the label; four grids clears both.  The first pin of each group keeps its place, so the ball
    order in ``number`` is reproduced deterministically.

    Returns ``[{"number", "net_name", "from", "to"}]`` -- never a silent edit.
    """
    groups = {}
    for p in pins:
        groups.setdefault((p["x"], p["y"]), []).append(p)
    occupied = set(groups)
    moved = []
    for (x, y), group in groups.items():
        if len(group) < 2:
            continue
        for p in group[1:]:
            dx, dy = _PIN_OUTWARD.get(int(p["rot"] or 0) % 360, (1, 0))
            k = 1
            while True:
                nx = x + dx * k * step_raw
                ny = y + dy * k * step_raw
                if (nx, ny) not in occupied:
                    break
                k += 1
            occupied.add((nx, ny))
            moved.append({"number": p["number"], "name": p.get("name", ""),
                          "from": [x, y], "to": [nx, ny]})
            p["x"], p["y"] = nx, ny
    return moved


# ------------------------------------------------------------- disk cache
def cache_dir():
    root = os.environ.get("PCB_AGENT_CACHE")
    if root:
        return root
    home = os.path.expanduser("~")
    return os.path.join(home, ".pcb_agent_mcp", "parts")


def cache_read(key):
    path = os.path.join(cache_dir(), key + ".json")
    try:
        if time.time() - os.path.getmtime(path) > _CACHE_TTL_S:
            return None
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001
        return None


def cache_write(key, value):
    path = os.path.join(cache_dir(), key + ".json")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(value, fh, ensure_ascii=False)
        return path
    except Exception:  # noqa: BLE001
        return None


def cache_key(*parts):
    raw = "|".join(str(p) for p in parts)
    out = []
    for ch in raw:
        out.append(ch if (ch.isalnum() or ch in "-_.") else "_")
    return "".join(out)[:120]


_UUID_RE = re.compile(r"^[0-9a-f]{32}$")


def looks_like_uuid(text):
    return bool(_UUID_RE.match(str(text or "").strip().lower()))
