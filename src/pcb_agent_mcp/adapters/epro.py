"""LCEDA Pro (立创EDA专业版) adapter, read-only, built on measured facts.

Everything here was read out of real projects, not inferred from a screenshot or
a blog post. The evidence file is `docs/13-立创实测-真实存储与记录格式.md`; the
numbers below are the ones that decide a code path.

Containers
    - `.epro`   ZIP, a *revision* container: `project.json` + `PCB/<uuid>.epcb`
                + `SHEET/...` + `SYMBOL/...` + `FOOTPRINT/...`.
    - `.eprj`, `.eprj2`  SQLite. The board is one row of `documents` with
                `docType=3`, and its `dataStr` is the same record stream.
      Both are parsed through `fidelity/lceda.py` and `fidelity/epro_doc.py` /
      `fidelity/eproj_doc.py`; this module only maps records to the canonical
      board.

Record stream
    One compact JSON array per line, documents separated by
    `["DOCTYPE", kind, version]` lines. A single `.epcb` may hold a `FOOTPRINT`
    document (library shapes) followed by the `PCB` document (the board).

Units (measured, and the single most dangerous thing to get wrong)
    Coordinates and sizes are **mil** (0.001 in), always: * not * what
    `CANVAS[3]` says. `CANVAS[3]` = `"mm"|"mil"` is the *user's display unit*,
    and `RULE[4][0]` is the unit the rule's number was typed in; the stored
    number is mil in both cases. Proof: track widths 19.685/39.3701/78.7402/
    98.4252 mil are exactly 0.5/1/2/2.5 mm; via 12.0078 mil = 0.305 mm; board
    outlines of 20 projects land on sane board sizes (190.0 x 124.5 mm, 21 x 40
    mm, 46.35 x 66 mm) under mil->mm and on metres under mm->mm.

Field map (arity 22-27 is per-document, see `Document.unique_arity`)
    COMPONENT [1]id [2]0 [3]layer(1 TOP / 2 BOTTOM) [4]x [5]y [6]rotation deg
    ATTR      [3]owner id [7]key [8]value; keys in use: Designator, Device,
              Footprint. **This is the only path from a component to its
              footprint uuid** - the component record itself carries no name.
    PAD       [4]layer [5]pad number [6]x [7]y [9]hole ["ROUND",hx,hy]|null
              [10]pad shape ["OVAL"/"RECT"/"ELLIPSE",w,h,...]
    PAD_NET   [1]component id [2]pad number [3]net name - the pad's net, keyed
              by component + pad number (the pad record itself is library-local)
    LINE      [3]net [4]layer [5]x1 [6]y1 [7]x2 [8]y2 [9]width
    ARC       [3]net [4]layer [5]x1 [6]y1 [7]x2 [8]y2 ...
    VIA       [3]net [5]x [6]y [7] [8]; the pair is always 1:2 and [7] < [8],
              so [7] is the hole and [8] the pad - a pad smaller than its hole
              is physically impossible, which is the whole proof.
    POLY      [4]layer [6]geometry, geometry = `x,y,("L"|"ARC"),...` or
              `["CIRCLE",x,y,r]`. Layer 11 is "OUTLINE" (the board edge) and it
              is stored as *segments*, one record per edge, not one per contour.
    POUR      [3]net [4]layer [5]flag [6]name [8][contours] [9][mode, ?]
    POURED    [2]pour id [4]filled? [5][contours] - where a poured board records
              its fill; in 1 of the 4 measured boards there is no POURED at all
    FILL      [3]net [4]layer [5]flag [7][["R",x,y,w,h,rot,r]] - a copper fill
    REGION    [3]layer [4]flag [5][type flags] [6][contours] - keepout area
    PAD       [3]net [4]layer [5]pad number [6]x [7]y [9]hole [10]shape -
              a pad placed *as a board object*, absolute mil coordinates; these
              are free pads / 定位孔, not the pads of library footprints
    PAD_NET   [1]component [2]pad number [3]net [4]pad object id in the
              footprint document - the only link between a placed pad and a net
    STRING    [3]layer [4]x [5]y [6]text [7]font [8]size
    RULE      [1]id [2]name [3]enabled [4][unit, value]; `safeClearance` is an
              upper-triangular matrix over copper layers, still in mil.

Rotation, measured against copper
    Pad geometry comes from the footprint library (a `FOOTPRINT` document in the
    same `.epcb`, or a `FOOTPRINT/<uuid>.efoo` member, or a `components` row with
    `docType=2` in SQLite), in footprint-local coordinates. Placing a pad means
    `component.xy + R(angle) * local`. Which direction R turns was decided by
    counting how many transformed pad centres land on a copper endpoint
    (LINE/ARC vertex) across 60 archives, restricted to components whose angle is
    not 0/180 so the two choices differ:

        +angle  9874 pads  54.1% exact   60.5% within 5 mil
        -angle  9874 pads  43.9% exact   50.1% within 5 mil

    (54% and not 100% because unrouted pads have no copper endpoint.) So the
    turn is `+angle` with the standard CCW rotation matrix, no mirror, for
    top-side parts. Bottom-side parts are much thinner evidence (348 pads):
    negating the local y scored 35.1%, negating x 20.7%, none 32.2% - so the
    bottom-side mirror is **declared, not claimed**, and `source.pad_transform`
    says so.

Y direction
    The file carries no Y direction: a global sign flip is a mirror, and a mirror
    cannot be recovered from the data (every distance, area and clearance is
    invariant under it). So the canonical frame here is the file's own frame and
    ``source.frame`` says exactly that. Rotations stay CCW in that frame, which
    the measurement above confirms is how the numbers are meant.
"""
import json
import math

from ..fidelity import epro_doc, eproj_doc, lceda
from .canonical import Board

#: millimetres per stored unit (mil). Measured, see the module docstring.
MIL_TO_MM = lceda.MM_PER_COORD          # 0.0254

#: Layer code -> name, from the `LAYER` records; 11 is the board edge.
OUTLINE_LAYER_NAME = "OUTLINE"

#: Endpoint join tolerance for chaining board-edge segments, in mil.
CHAIN_TOL = 0.01

#: Closed polyline -> polygon sampling step for arcs, in mil.
ARC_STEP_DEG = 5.0

class EproError(ValueError):
    """Raised when a file cannot be read as an LCEDA Pro board."""

# ------------------------------------------------------------------ helpers
def mm(value):
    """mil -> mm, rounded to a stable number of digits (0.1 nm)."""
    if value is None:
        return None
    return round(float(value) * MIL_TO_MM, 6)

def _num(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None

# ------------------------------------------------------------------ container
def _open_container(path):
    """Dispatch on content: ZIP -> `.epro`, SQLite -> `.eprj/.eprj2`."""
    if epro_doc.looks_like_epro(path):
        return epro_doc.EproDocument.from_path(path)
    if eproj_doc.looks_like_eproj(path):
        return eproj_doc.EprjDocument.from_path(path)
    raise EproError("%s is neither a ZIP (.epro) nor a SQLite project "
                    "(.eprj/.eprj2)" % path)

def _pcb_streams(container):
    """[(stream name, [lceda.Document])] for every stream that holds a board.

    `.epro` members are tried largest first: a project keeps one `.epcb` per
    revision and the newest one is also the biggest in every sample.
    """
    if isinstance(container, epro_doc.EproDocument):
        names = [n for n in container.stream_names() if n.endswith(".epcb")]
        names.sort(key=lambda n: len(container.entries[n]), reverse=True)
    else:
        names = [d["uuid"] for d in container.board_documents()]
    out = []
    for name in names:
        member = container.member(name)
        out.append((name, member.documents))
    return out

def _pick_pcb_document(docs):
    """The `PCB` document of a stream, or the one that holds the components."""
    for doc in docs:
        if doc.kind == "PCB":
            return doc
    for doc in docs:
        if doc.by_tag("COMPONENT"):
            return doc
    return docs[0] if docs else None

def _records(doc):
    """tag -> [record]; only parsed array records, blank/`[]` lines skipped."""
    out = {}
    for line in doc.lines:
        rec = line.record
        if not isinstance(rec, list) or not rec:
            continue
        tag = rec[0]
        if not isinstance(tag, str):
            continue
        out.setdefault(tag, []).append(rec)
    return out

def _layer_names(pcb):
    names = {}
    for rec in pcb.get("LAYER", []):
        if len(rec) > 2 and isinstance(rec[1], int):
            names[rec[1]] = str(rec[2])
    return names

def _layer_of(pcb, code, names):
    if code is None:
        return None
    if isinstance(code, str) and not code.strip().lstrip("-").isdigit():
        return code.strip()
    try:
        return names.get(int(code), str(code))
    except (TypeError, ValueError):
        return str(code)

# ------------------------------------------------------------------ geometry
class Prims(list):
    """Primitives plus the assumptions used to build them.

    A plain list would be enough for the geometry, but the reader has to be able
    to say "this rectangle had a corner radius I did not model". Flags travel
    with the result instead of being guessed downstream.
    """

    def __init__(self, items=(), flags=()):
        list.__init__(self, items)
        self.flags = tuple(flags)

def _round_rect(x, y, w, h, radius):
    """Axis-aligned rectangle, corners rounded by `radius` (sampled arcs)."""
    if not radius:
        return [(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)]
    r = min(radius, abs(w) / 2.0, abs(h) / 2.0)
    pts = []
    corners = (
        ((x + w - r, y + r), 270.0),      # bottom-right
        ((x + w - r, y + h - r), 0.0),    # top-right
        ((x + r, y + h - r), 90.0),       # top-left
        ((x + r, y + r), 180.0),          # bottom-left
    )
    steps = max(2, int(round(90.0 / ARC_STEP_DEG)))
    for (cx, cy), start in corners:
        for k in range(steps + 1):
            ang = math.radians(start + 90.0 * k / steps)
            pts.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))
    pts.append(pts[0])
    return pts

def _rect_prims(raw):
    """`["R", x, y, w, h, rotation_deg, corner_radius]` -> primitives + flags."""
    x, y = _num(raw[1]), _num(raw[2])
    w, h = _num(raw[3]), _num(raw[4])
    if None in (x, y, w, h):
        return Prims()
    rot = _num(raw[5]) if len(raw) > 5 else 0.0
    radius = _num(raw[6]) if len(raw) > 6 else 0.0
    flags = []
    pts = _round_rect(x, y, w, h, radius or 0.0)
    if radius:
        flags.append("rect_corner_radius_sampled")
    if rot:
        # The rotation pivot is not established by the file; the rectangle is
        # turned about its own centre, the only choice that leaves it where the
        # record puts it.
        cx, cy = x + w / 2.0, y + h / 2.0
        ar = math.radians(rot)
        pts = [(cx + (px - cx) * math.cos(ar) - (py - cy) * math.sin(ar),
                cy + (px - cx) * math.sin(ar) + (py - cy) * math.cos(ar))
               for px, py in pts]
        flags.append("rect_rotation_about_centre")
    return Prims([("line", pts[i], pts[i + 1]) for i in range(len(pts) - 1)],
                 flags)

def _parse_geometry(raw):
    """Geometry string -> primitives. Four measured forms, nothing else.

    ``x,y,"L",p1,...,pn``
        one point, then a polyline: **one** ``"L"`` for all the points, not one
        per vertex.
    ``x,y,"ARC",sweep,px,py[,"ARC",sweep,px,py]``
        a sweep angle in degrees and the arc's end point; up to two arcs per
        record, never mixed with ``"L"``.
    ``["CIRCLE", x, y, r]``
        the whole shape in one record.
    ``["R", x, y, w, h, rot, radius]``
        a rectangle; ``rot`` turns it about its own centre (the pivot is not
        established by the file, so it is declared in ``Prims.flags``), and
        ``radius`` rounds the corners (sampled at ARC_STEP_DEG).

    Primitives: ("line",(x0,y0),(x1,y1)), ("arc",(x0,y0),sweep,(x1,y1)) and
    ("circle",(x,y),r,None).
    """
    if isinstance(raw, list) and raw and isinstance(raw[0], str):
        head = raw[0].upper()
        if head == "CIRCLE":
            if len(raw) >= 4:
                return Prims([("circle", (_num(raw[1]), _num(raw[2])),
                               _num(raw[3]), None)])
            return Prims()
        if head == "R":
            return _rect_prims(raw)
        return Prims()
    if not isinstance(raw, list) or len(raw) < 4:
        return Prims()
    x0, y0 = _num(raw[0]), _num(raw[1])
    if x0 is None or y0 is None:
        return Prims()
    prims = []
    cursor = (x0, y0)
    i = 2
    while i < len(raw):
        token = raw[i]
        if token == "L":
            i += 1
            moved = False
            while (i + 1 < len(raw)
                   and isinstance(raw[i], (int, float))
                   and isinstance(raw[i + 1], (int, float))):
                nxt = (_num(raw[i]), _num(raw[i + 1]))
                prims.append(("line", cursor, nxt))
                cursor = nxt
                i += 2
                moved = True
            if not moved:
                break
        elif token == "ARC" and i + 3 < len(raw):
            sweep = _num(raw[i + 1])
            nxt = (_num(raw[i + 2]), _num(raw[i + 3]))
            if sweep is None or None in nxt:
                break
            prims.append(("arc", cursor, sweep, nxt))
            cursor = nxt
            i += 4
        else:
            break
    return Prims(prims)

def _arc_points(start, sweep, end, step=ARC_STEP_DEG):
    """Sample a circular arc given both endpoints, the sweep angle and its sign."""
    if sweep is None or start is None or end is None:
        return [start] if start else []
    sx, sy = start
    ex, ey = end
    chord = math.hypot(ex - sx, ey - sy)
    if chord < 1e-9 or abs(sweep) < 1e-9:
        return [start, end]
    half = math.radians(abs(sweep)) / 2.0
    if abs(math.sin(half)) < 1e-12:
        return [start, end]
    radius = chord / (2.0 * math.sin(half))
    # Two centres fit the chord; the sweep's *sign* picks one of them: the arc
    # from start to end must turn through `sweep` degrees in that direction.
    mx, my = (sx + ex) / 2.0, (sy + ey) / 2.0
    nx, ny = -(ey - sy) / chord, (ex - sx) / chord
    h = radius * math.cos(half)
    best = None
    for sign in (1.0, -1.0):
        cx, cy = mx + sign * h * nx, my + sign * h * ny
        a0 = math.degrees(math.atan2(sy - cy, sx - cx))
        a1 = math.degrees(math.atan2(ey - cy, ex - cx))
        delta = (a1 - a0 + 540.0) % 360.0 - 180.0
        err = abs(delta - sweep)
        if best is None or err < best[0]:
            best = (err, cx, cy, a0, delta)
    _err, cx, cy, a0, delta = best
    n = max(2, int(abs(delta) / max(step, 0.5)) + 1)
    pts = []
    for k in range(n + 1):
        a = math.radians(a0 + delta * k / float(n))
        pts.append((cx + radius * math.cos(a), cy + radius * math.sin(a)))
    pts[0], pts[-1] = start, end
    return pts

def _chain(prims, tol=CHAIN_TOL):
    """Join primitives end-to-start into polylines. Order in the file is not
    guaranteed, so this is a join, not a concatenation."""
    remaining = list(prims)
    chains = []
    while remaining:
        first = remaining.pop(0)
        pts = _to_points(first)
        if not pts:
            continue
        changed = True
        while changed:
            changed = False
            for idx, prim in enumerate(remaining):
                seg = _to_points(prim)
                if len(seg) < 2:
                    remaining.pop(idx)
                    changed = True
                    break
                if _close(pts[-1], seg[0], tol):
                    pts.extend(seg[1:])
                elif _close(pts[0], seg[-1], tol):
                    pts = seg[:-1] + pts
                elif _close(pts[-1], seg[-1], tol):
                    pts.extend(reversed(seg[:-1]))
                elif _close(pts[0], seg[0], tol):
                    pts = list(reversed(seg[1:])) + pts
                else:
                    continue
                remaining.pop(idx)
                changed = True
                break
        chains.append(pts)
    return chains

def _to_points(prim):
    if prim[0] == "line":
        return [prim[1], prim[2]]
    if prim[0] == "arc":
        return _arc_points(prim[1], prim[2], prim[3])
    if prim[0] == "circle":
        cx, cy = prim[1]
        r = prim[2] or 0.0
        n = 72
        return [(cx + r * math.cos(2 * math.pi * k / n),
                 cy + r * math.sin(2 * math.pi * k / n)) for k in range(n + 1)]
    return []

def _close(a, b, tol=CHAIN_TOL):
    return abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol

def _area(poly):
    s = 0.0
    for i in range(len(poly)):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % len(poly)]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0

def _inside(pt, poly):
    x, y = pt
    hit = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            xi = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if xi > x:
                hit = not hit
    return hit

def _to_mm_poly(pts):
    return [(mm(x), mm(y)) for x, y in pts]

# ------------------------------------------------------------------ footprint library
def _library(container):
    """Footprint pads and the names the board document only references by uuid.

    Measured sources (no guessing, each one checked by dumping the archives):

    `.epro` (ZIP)
        a `FOOTPRINT` document per library footprint, in its own
        `FOOTPRINT/<uuid>.efoo` member (and, in some revisions, inside the
        `.epcb` as a second document); the human names live in `project.json`
        as `footprints[<uuid>].title` and `devices[<uuid>].title`.
    `.eprj` / `.eprj2` (SQLite)
        the same `FOOTPRINT` documents as `components` rows - in the samples
        `docType=4` is FOOTPRINT and `docType=2/18/19/20` are SYMBOL, so the rows
        are split on the document's own `DOCTYPE` line, not on `docType`;
        titles come from `components.title` and `devices.title`.

    Returns {"pads": {fp_uuid: {pad_number: pad}}, "fp_title": {...},
             "device_title": {...}, "title_source": str, "footprint_docs": n}.
    """
    pads = {}
    fp_title = {}
    device_title = {}
    docs = []
    if isinstance(container, epro_doc.EproDocument):
        for name in container.stream_names():
            uuid = name.rsplit("/", 1)[-1].split(".")[0]
            for doc in container.member(name).documents:
                if doc.kind == "FOOTPRINT":
                    docs.append((uuid, doc))
        project = container.project() or {}
        for uuid, item in (project.get("footprints") or {}).items():
            if isinstance(item, dict) and item.get("title"):
                fp_title[uuid] = item["title"]
        for uuid, item in (project.get("devices") or {}).items():
            if isinstance(item, dict) and item.get("title"):
                device_title[uuid] = item["title"]
        title_source = "project.json footprints[].title / devices[].title"
    else:
        for row in container.library_rows():
            uuid, text = row["uuid"], row["text"]
            if row.get("title"):
                fp_title[uuid] = row["title"]
            try:
                member = lceda.Member(uuid, text)
            except lceda.LcedaError:
                continue
            for doc in member.documents:
                if doc.kind == "FOOTPRINT":
                    docs.append((uuid, doc))
        for row in container.table_titles("devices"):
            device_title[row["uuid"]] = row["title"]
        title_source = "components.title / devices.title"

    for uuid, doc in docs:
        if pads.get(uuid):
            continue
        found = {}
        for line in doc.by_tag("PAD"):
            rec = line.record
            if not isinstance(rec, list) or len(rec) < 8:
                continue
            number = str(rec[5]) if rec[5] is not None else ""
            shape = rec[10] if len(rec) > 10 and isinstance(rec[10], list) else None
            hole = rec[9] if len(rec) > 9 and isinstance(rec[9], list) else None
            kind = str(shape[0]).lower() if shape else None
            w = h = corner = None
            polygon = None
            if kind in ("oval", "rect", "rectangle", "ellipse", "round") and len(shape) > 2:
                w, h = _num(shape[1]), _num(shape[2])
                if len(shape) > 3:
                    corner = _num(shape[3])
            elif kind in ("polygon", "poly") and len(shape) > 1:
                prims = _parse_geometry(shape[1])
                pts = [pt for prim in prims for pt in _to_points(prim)]
                if len(pts) >= 3:
                    # Absolute in the footprint frame: the bounding box centre of
                    # this polygon equals PAD[6],PAD[7] on every sample pad.
                    polygon = [(pt[0] - _num(rec[6]), pt[1] - _num(rec[7]))
                               for pt in pts]
            found.setdefault(number, {
                "x": _num(rec[6]), "y": _num(rec[7]),
                # The pad's own layer field. Measured over 6 projects / 4724
                # pads: code 1 = top, 2 = bottom, 12 = every copper layer, and
                # "12" holds for exactly the 413 pads that carry a hole
                # (413/413 both directions, zero exceptions). Reading the
                # component's layer instead of this field is what made every
                # through-hole pad look single-sided.
                "layer_code": str(rec[4]) if len(rec) > 4 else None,
                "hole": hole,
                "hole_kind": str(hole[0]).lower() if hole else None,
                "hole_size": (_num(hole[1]) if hole and len(hole) > 1 else None),
                "hole2": (_num(hole[2]) if hole and len(hole) > 2 else None),
                "shape": kind, "w": w, "h": h, "corner": corner,
                "polygon": polygon,
            })
        if found:
            pads[uuid] = found
    return {"pads": pads, "fp_title": fp_title, "device_title": device_title,
            "title_source": title_source, "footprint_docs": len(docs)}

# ------------------------------------------------------------------ parse
_MULTI_LAYER_CODE = "12"


def _pad_layers(component_layer, layer_code, hole_kind):
    """Every copper layer a pad sits on.

    A through-hole pad is not on the component's side only: it is on all of
    them. The file says so itself - a drilled pad carries layer code 12
    ("multi") in its own PAD record, measured 413/413 in the sample corpus.
    Until 0.0.92 this reader ignored that field and reported the component's
    side, so a track arriving on the far side of a through-hole pad looked
    like it ended in mid-air.
    """
    if hole_kind is not None or str(layer_code) == _MULTI_LAYER_CODE:
        return ["*.Cu"]
    return [component_layer] if component_layer else []


def parse_board(path, member=None):
    """Read one LCEDA Pro board into a canonical `Board`. Never writes."""
    container = _open_container(path)
    try:
        return _parse(container, path, member)
    finally:
        close = getattr(container, "close", None)
        if close:
            close()

def _parse(container, path, member):
    streams = _pcb_streams(container)
    if not streams:
        raise EproError("no board stream inside %s (an .epro needs a PCB/*.epcb "
                        "member, a project database a documents row with "
                        "docType=3)" % path)
    chosen_name = None
    chosen_doc = None
    if member:
        for name, docs in streams:
            if name.endswith(member) or member.endswith(name.rsplit("/", 1)[-1]):
                chosen_name, chosen_doc = name, _pick_pcb_document(docs)
                break
        if chosen_doc is None:
            raise EproError("member %r not found in %s" % (member, path))
    else:
        for name, docs in streams:
            doc = _pick_pcb_document(docs)
            if doc is not None and doc.by_tag("COMPONENT"):
                chosen_name, chosen_doc = name, doc
                break
        if chosen_doc is None:
            chosen_name, chosen_doc = streams[0][0], _pick_pcb_document(streams[0][1])

    board = Board("lceda_epro", path)
    board.source.update({
        "container": ("epro_zip" if isinstance(container, epro_doc.EproDocument)
                      else "sqlite"),
        "member": chosen_name,
        "doc_doctype": chosen_doc.kind,
        "doc_version": chosen_doc.version,
        "coord_unit": "mil",
        "mm_per_coord": MIL_TO_MM,
        "unit_verified": True,
        "unit_evidence": ("track widths 19.685/39.3701/78.7402/98.4252 are "
                          "exactly 0.5/1/2/2.5 mm; and RULE numbers stay mil even "
                          "when the rule says otherwise - viaSize carries the unit "
                          "label \"mm\" but holds 9.8425/12.0079/196.8504, i.e. "
                          "exactly 0.25/0.305/5.0 mm once read as mil (92 archives), "
                          "trackWidth \"mm\" holds 5/10/100 = 0.127/0.254/2.54 mm. "
                          "So CANVAS[3] and RULE[4][0] are display units only"),
        "frame": ("file (y not inverted); a global sign flip is a mirror and is "
                  "not recoverable from the file - distances, areas and "
                  "clearances are invariant under it"),
    })
    pcb = _records(chosen_doc)
    names = _layer_names(pcb)
    board.source["layers"] = [{"code": c, "name": n} for c, n in sorted(names.items())]

    # ---------------------------------------------------------- nets
    for rec in pcb.get("NET", []):
        name = rec[1] if len(rec) > 1 else None
        if isinstance(name, str):
            board.nets[name] = name
    board.source["n_net_records"] = len(board.nets)

    # ---------------------------------------------------------- footprints
    library = _library(container)
    pads_by_number = library["pads"]
    board.source["library"] = {
        "footprint_documents": library["footprint_docs"],
        "footprints_with_pads": len(pads_by_number),
        "name_source": library["title_source"],
    }
    attrs = {}
    for rec in pcb.get("ATTR", []):
        if len(rec) > 8 and isinstance(rec[3], str):
            attrs.setdefault(rec[3], {})[str(rec[7])] = rec[8]
    pad_nets = {}
    for rec in pcb.get("PAD_NET", []):
        if len(rec) > 3:
            pad_nets.setdefault(str(rec[1]), {})[str(rec[2])] = rec[3]

    resolved = unresolved = 0
    for rec in pcb.get("COMPONENT", []):
        if len(rec) < 7:
            continue
        cid = str(rec[1])
        layer = _layer_of(pcb, rec[3], names)
        x, y = _num(rec[4]), _num(rec[5])
        angle = _num(rec[6]) or 0.0
        meta = attrs.get(cid, {})
        fp_uuid = meta.get("Footprint")
        device_uuid = meta.get("Device")
        pads = pads_by_number.get(fp_uuid) if fp_uuid else None
        if pads:
            resolved += 1
        elif fp_uuid:
            unresolved += 1
        entry = {
            "ref": meta.get("Designator") or "",
            # `Device` is a uuid in the board document; the readable name is the
            # device title in project.json (`devices`) / the `devices` table.
            "value": library["device_title"].get(device_uuid) if device_uuid else None,
            "device_uuid": device_uuid or "",
            "footprint_name": library["fp_title"].get(fp_uuid) if fp_uuid else None,
            "lib_id": fp_uuid or "",
            "x": mm(x), "y": mm(y),
            "rotation": angle,
            "layer": layer,
            "bbox": None,
            "pads": [],
            "_id": cid,
            "_pads_source": "footprint library (FOOTPRINT document / .efoo / components row)"
                            if pads else "missing: the footprint this component names is "
                                         "not stored in the file",
        }
        mirrored = str(layer or "").upper().startswith("BOT")
        for number, pad in sorted((pads or {}).items(), key=lambda kv: kv[0]):
            px, py = pad["x"], pad["y"]
            if px is None or py is None:
                continue
            # Bottom side: the footprint is NOT y-mirrored. Measured over 2549
            # net-bearing pads on 6 projects, by asking which transform puts the
            # pad centre exactly on a copper endpoint of its own net (tolerance
            # 0.05 mm): no mirror 62.1% top / 82.6% bottom, y-mirror 37.0% /
            # 53.2%, x-mirror 3.0% / 6.0%, both 1.7% / 3.5%; cross-net hits per
            # hypothesis were 1/1/0/0 top and 3/2/0/0 bottom, so the ordering is
            # not an artefact of loose tolerance. The hit rate is a lower bound
            # (an unrouted pad or a pad fed only by a pour carries no endpoint):
            # placement is therefore still declared, not verified.
            a = math.radians(angle)
            cos_a, sin_a = math.cos(a), math.sin(a)
            rx = x + px * cos_a - py * sin_a
            ry = y + px * sin_a + py * cos_a
            hole_kind = pad.get("hole_kind")
            polygon = pad.get("polygon")
            # Centre-relative and *not* rotated: `canonical.pad_points` applies
            # `rotation` to a custom polygon, so storing it already turned would
            # turn it twice. No bottom-side flip either - see the measurement
            # above; a flipped polygon under an unflipped centre would be a shape
            # that contradicts its own position.
            rel_polygon = None
            if polygon:
                rel_polygon = []
                for dx, dy in polygon:
                    if rel_polygon and _close(rel_polygon[-1], (dx, dy), 1e-9):
                        continue
                    rel_polygon.append((dx, dy))
            entry["pads"].append({
                "number": number,
                "net": pad_nets.get(cid, {}).get(number),
                "x": mm(rx), "y": mm(ry),
                "w": mm(pad.get("w")),
                "h": mm(pad.get("h")),
                "shape": _pad_shape(pad.get("shape"), pad.get("w"), pad.get("h")),
                "polygon": ([(mm(dx), mm(dy)) for dx, dy in rel_polygon]
                            if rel_polygon else None),
                "corner_radius_mm": mm(pad.get("corner")),
                "rotation": angle,
                "layers": _pad_layers(layer, pad.get("layer_code"), hole_kind),
                # What this reader reported before 0.0.92: the component's
                # side, with through-hole pads folded into it. Kept so the old
                # reading stays traceable instead of silently vanishing.
                "layers_as_written": [layer] if layer else [],
                "hole_mm": mm(pad.get("hole_size")),
                "hole_kind": hole_kind,
                "hole_length_mm": (mm(pad.get("hole2"))
                                   if hole_kind == "slot" else None),
                "smd": hole_kind is None,
                "_local": (mm(px), mm(py)),
            })
        board.footprints.append(entry)
    board.source["pad_transform"] = {
        "top_side": "component.xy + R(+angle) * footprint-local pad xy",
        "bottom_side": "the same transform - no y mirror (measured, see below)",
        "verified_against": "copper LINE endpoints, 6 projects, 2549 net-bearing pads "
                            "with a footprint-local position",
        "verified_by_app": False,
        "top_side_hit_rate": {"exact": 0.621, "n": 2002,
                              "alternatives": {"y-mirror": 0.370,
                                               "x-mirror": 0.030,
                                               "both": 0.017}},
        "bottom_side_hit_rate": {"exact": 0.826, "n": 547,
                                 "alternatives": {"y-mirror": 0.532,
                                                  "x-mirror": 0.060,
                                                  "both": 0.035}},
        "hit_rate_is": "a lower bound on placement accuracy: a pad that is unrouted, "
                       "or fed only by a pour, carries no track endpoint to land on",
        "cross_net_hits": {"top": {"none": 1, "y-mirror": 1, "x-mirror": 0,
                                   "both": 0},
                           "bottom": {"none": 3, "y-mirror": 2, "x-mirror": 0,
                                      "both": 0}},
        "note": "0.0.92 corrected two numbers this reader used to report, both from "
                "re-measuring rather than re-reasoning: the bottom-side transform is "
                "no-mirror (not y-mirror, whose 0.351 was a measurement of the wrong "
                "hypothesis), and through-hole pads sit on every copper layer, which "
                "the file states itself in the pad's own layer code 12 (413/413).",
    }

    # PAD records that the board document itself stores (no component link in
    # the file: PAD ids and PAD_NET's pad ids live in different id spaces).
    loose = []
    for rec in pcb.get("PAD", []):
        if len(rec) < 8:
            continue
        shape = rec[10] if len(rec) > 10 and isinstance(rec[10], list) else []
        hole = rec[9] if len(rec) > 9 and isinstance(rec[9], list) else None
        loose.append({
            "number": str(rec[5]) if rec[5] is not None else "",
            "net": None,
            "x": mm(_num(rec[6])), "y": mm(_num(rec[7])),
            "w": mm(_num(shape[1])) if len(shape) > 1 else None,
            "h": mm(_num(shape[2])) if len(shape) > 2 else None,
            "shape": _pad_shape(shape[0] if shape else None),
            "rotation": 0.0,
            "layers": _pad_layers(_layer_of(pcb, rec[4], names),
                                  str(rec[4]) if len(rec) > 4 else None,
                                  str(hole[0]).lower() if hole else None),
            "layers_as_written": [_layer_of(pcb, rec[4], names)],
            "hole_mm": mm(_num(hole[1])) if hole and len(hole) > 1 else None,
            "smd": hole is None,
        })
    if loose:
        board.footprints.append({
            "ref": "", "value": "", "lib_id": "", "x": None, "y": None,
            "rotation": 0.0, "layer": "", "bbox": None, "pads": loose,
            "_synthetic": ("PAD records stored in the board document itself; the file "
                           "carries no component link for them"),
        })

    # ---------------------------------------------------------- copper
    for rec in pcb.get("LINE", []):
        if len(rec) < 9:
            continue
        board.tracks.append({
            "id": rec[1],
            "net": rec[3] if len(rec) > 3 else None,
            "layer": _layer_of(pcb, rec[4], names),
            "width": mm(_num(rec[9])) if len(rec) > 9 else None,
            "start": [mm(_num(rec[5])), mm(_num(rec[6]))],
            "end": [mm(_num(rec[7])), mm(_num(rec[8]))],
        })
    for rec in pcb.get("ARC", []):
        if len(rec) < 8:
            continue
        # ARC carries both endpoints; `segments` keeps the curvature, `start/end`
        # keep the shape the canonical model expects from a track.
        board.tracks.append({
            "id": rec[1],
            "net": rec[3] if len(rec) > 3 else None,
            "layer": _layer_of(pcb, rec[4], names),
            "width": mm(_num(rec[10])) if len(rec) > 10 else None,
            "start": [mm(_num(rec[5])), mm(_num(rec[6]))],
            "end": [mm(_num(rec[7])), mm(_num(rec[8]))],
            "_kind": "arc",
        })
    for rec in pcb.get("VIA", []):
        if len(rec) < 7:
            continue
        board.vias.append({
            "id": rec[1],
            "net": rec[3] if len(rec) > 3 else None,
            "x": mm(_num(rec[5])), "y": mm(_num(rec[6])),
            "drill": mm(_num(rec[7])),
            "diameter": mm(_num(rec[8])) if len(rec) > 8 else None,
            "from": "TOP", "to": "BOTTOM", "kind": "through",
        })

    # ---------------------------------------------------------- pours / fills
    pour_by_id = {}
    for rec in pcb.get("POUR", []):
        if len(rec) < 9:
            continue
        contours = _contours(rec[8])
        zone = {
            "net": rec[3] if len(rec) > 3 else None,
            "layer": _layer_of(pcb, rec[4], names),
            # POUR[5] holds only 0.2 (12,232 pours) or 1 (168) in all 194
            # measured archives - it is a flag, not a distance, so it is kept
            # raw and no millimetre value is claimed for it.
            "clearance": None,
            "clearance_raw": _num(rec[5]),
            "name": rec[6] if len(rec) > 6 else "",
            "outline": _to_mm_poly(contours[0]) if contours else [],
            "filled_polygons": [],
            "fill_mode": "solid",
            "priority": 0,
            "keepout": None,
            "is_keepout": False,
            "origin_type": "POUR",
        }
        board.zones.append(zone)
        pour_by_id[str(rec[1])] = zone
    for rec in pcb.get("POURED", []):
        if len(rec) < 6:
            continue
        zone = pour_by_id.get(str(rec[2]))
        if zone is None:
            continue
        contours = _contours(rec[5])
        if rec[4] is False or (contours == [] and rec[3] == 0 and rec[4] is False):
            continue
        zone["filled_polygons"] = [_to_mm_poly(c) for c in contours]
    for rec in pcb.get("FILL", []):
        if len(rec) < 8:
            continue
        shapes = rec[7] if isinstance(rec[7], list) else []
        poly = _fill_outline(shapes)
        if len(poly) < 3:
            continue
        board.zones.append({
            "net": rec[3] if len(rec) > 3 else None,
            "layer": _layer_of(pcb, rec[4], names),
            "clearance": None,
            "clearance_raw": _num(rec[5]) if len(rec) > 5 else None,
            "outline": _to_mm_poly(poly),
            "filled_polygons": [_to_mm_poly(poly)],
            "fill_mode": "solid",
            "priority": 0,
            "keepout": None,
            "is_keepout": False,
            "origin_type": "FILL",
        })
    for rec in pcb.get("REGION", []):
        if len(rec) < 8:
            continue
        # Measured: ["REGION", id, ?, layer, <flag 0.2|1>, [keepout flags],
        # [contours], ..] - the flags sit at [5] and the contours at [6].
        contours = _contours(rec[6])
        if not contours:
            continue
        board.zones.append({
            "net": None,
            "layer": _layer_of(pcb, rec[3], names),
            "clearance": None,
            "clearance_raw": _num(rec[4]) if len(rec) > 4 else None,
            "outline": _to_mm_poly(contours[0]),
            "filled_polygons": [],
            "fill_mode": "none",
            "priority": 0,
            "keepout": {"flags": rec[5] if len(rec) > 5 else None},
            "is_keepout": True,
            "origin_type": "REGION",
        })

    # ---------------------------------------------------------- board edge
    board.outline, board.cutouts, edge_note, open_edges = _outline(pcb, names)
    board.outline_open = open_edges
    if edge_note:
        board.source["outline_note"] = edge_note
    board.source["zone_clearance_note"] = (
        "POUR[5] / FILL[5] / REGION[4] hold only 0.2 or 1 across all 194 measured "
        "archives (12,232 / 168 pours), so the slot is a flag, not a distance: it "
        "is reported as clearance_raw and no millimetre value is claimed")
    board.source["pad_shape_note"] = (
        "ELLIPSE pads become circle (w==h) or oval (stadium); rectangular pads "
        "keep their measured corner radius in pad.corner_radius_mm; POLYGON pads "
        "keep their measured outline in pad.polygon")

    # ---------------------------------------------------------- text / rules
    texts = []
    for rec in pcb.get("STRING", []):
        if len(rec) < 9:
            continue
        texts.append({
            "id": rec[1],
            "layer": _layer_of(pcb, rec[3], names),
            "x": mm(_num(rec[4])), "y": mm(_num(rec[5])),
            "text": rec[6], "font": rec[7], "size_mm": mm(_num(rec[8])),
            "rotation": _num(rec[13]) if len(rec) > 13 else None,
        })
    board.source["texts"] = texts

    # Free pads: `PAD` records that live in the board document itself, with
    # absolute mil coordinates. They are NOT the pads of library footprints
    # (measured: 271 of them in the 1000W project, whose 187 components carry
    # 732 pads between them, and no field of a PAD record names a component).
    free_pads = []
    for rec in pcb.get("PAD", []):
        if len(rec) < 11:
            continue
        hole = rec[9]
        free_pads.append({
            "id": rec[1], "net": rec[3] or None,
            "layer": _layer_of(pcb, rec[4], names),
            "number": str(rec[5]),
            "x": mm(_num(rec[6])), "y": mm(_num(rec[7])),
            "hole_mm": mm(_num(hole[1])) if isinstance(hole, list) and len(hole) > 1 else None,
            "shape": rec[10] if isinstance(rec[10], list) else None,
            "pos_source": "recorded (PAD record in the board document)",
        })
    board.source["free_pads"] = free_pads

    _rules(board, pcb)
    _stackup(board, pcb, names)

    # ---------------------------------------------------------- provenance
    head = pcb.get("HEAD")
    editor = None
    if head and len(head) > 1 and isinstance(head[1], dict):
        editor = head[1].get("editorVersion")
    board.source["editor_version"] = editor
    board.source["record_counts"] = {tag: len(recs) for tag, recs in sorted(pcb.items())}
    board.source["footprint_resolution"] = {"resolved": resolved,
                                            "missing": unresolved}

    board.mark("footprints", bool(board.footprints))
    board.mark("pads", bool(board.pads()),
               None if resolved else "no component footprint resolved to a library "
                                     "definition, so component pads are not in this file")
    board.mark("tracks", bool(board.tracks))
    board.mark("vias", bool(board.vias))
    board.mark("free_pads", bool(free_pads),
               None if free_pads else
               "no PAD record in the board document: this board has no free pads")
    board.mark("zones", bool(board.zones))
    board.mark("zone_fill", any(z["filled_polygons"] for z in board.zones),
               None if any(z["filled_polygons"] for z in board.zones) else
               "no POURED record for these pours: the file holds pours as outlines")
    board.mark("nets", bool(board.nets))
    board.mark("outline", bool(board.outline or board.outline_open),
               edge_note or "POLY records on the OUTLINE layer, chained by endpoint")
    board.mark("cutouts", bool(board.cutouts),
               None if board.cutouts else "no second closed contour inside the board edge")
    board.mark("stackup", bool(board.stackup),
               None if board.stackup else
               "LAYER_PHYS carries layer order but no dielectric thickness in these files")
    board.mark("design_rules", bool(board.rules),
               "RULE values are stored in mil; the matrix is the upper triangle")
    board.mark("pad_to_mask", False,
               "per-pad mask/paste expansion sits at PAD[17]/[18] but the mapping was "
               "not verified against a rendered board, so it is not reported")
    board.mark("component_3d", False,
               "3D bodies are SHELL/SHELL_ENTITY/BOSS records, not evaluated")

    board.resolve_nets()
    return board

def _pad_shape(shape, w=None, h=None):
    """Map a measured shape name onto the canonical primitive set.

    `ELLIPSE` is the only approximation: the canonical model has no ellipse, so
    a square one becomes `circle` and the rest become `oval` (a stadium, which
    overstates the area between the axes). That is declared in
    `source.pad_shape_note`, not silently assumed.
    """
    if not shape:
        return "rect"
    s = str(shape).lower()
    if s == "ellipse":
        if w is not None and h is not None and abs(w - h) < 1e-9:
            return "circle"
        return "oval"
    return {"oval": "oval", "round": "circle",
            "rect": "rect", "rectangle": "rect",
            "polygon": "custom", "poly": "custom"}.get(s, s)

def _contours(raw):
    """A POUR/POURED/REGION/FILL boundary -> [contour], each contour chained.

    Three shapes occur: a bare geometry (`[x, y, "L", ...]`), a named primitive
    (`["CIRCLE", x, y, r]` / `["R", ...]`), and lists nested one or more levels
    (`POUR[8]` and `POURED[5]` hold `[[geometry], ...]`). Chained, not flattened:
    a polyline stored as N primitives repeats every interior vertex, and a
    contour with duplicated points is not what the file holds.
    """
    if not isinstance(raw, list) or not raw:
        return []
    head = raw[0]
    if isinstance(head, (int, float, str)):
        return [c for c in _chain(_parse_geometry(raw)) if len(c) >= 2][:1]
    out = []
    for item in raw:
        if isinstance(item, list):
            out.extend(_contours(item))
    return out

def _fill_outline(shapes):
    """FILL[7]: `[["R",x,y,w,h,..]]` rectangles or `[[point string]]` polygons."""
    pts = []
    for item in shapes or []:
        if not isinstance(item, list) or not item:
            continue
        if str(item[0]).upper() == "R" and len(item) >= 5:
            x, y, w, h = (_num(item[1]), _num(item[2]), _num(item[3]), _num(item[4]))
            if None in (x, y, w, h):
                continue
            pts.extend([(x, y), (x + w, y), (x + w, y + h), (x, y + h)])
        elif isinstance(item[0], (int, float)):
            for prim in _parse_geometry(item):
                pts.extend(_to_points(prim))
        elif isinstance(item[0], list):
            pts.extend(_fill_outline(item))
    return pts

def _join_gap(chains):
    """Smallest distance between endpoints of two different chains, in mil.

    This is how far apart the outline still is from closing: a measured number,
    so a caller can see whether the file is sloppy (a fraction of a mil) or the
    geometry really is several pieces.
    """
    ends = []
    for idx, chain in enumerate(chains):
        if len(chain) >= 1:
            ends.append((idx, chain[0]))
            ends.append((idx, chain[-1]))
    best = None
    for i in range(len(ends)):
        for j in range(i + 1, len(ends)):
            if ends[i][0] == ends[j][0]:
                continue
            d = math.hypot(ends[i][1][0] - ends[j][1][0],
                           ends[i][1][1] - ends[j][1][1])
            if best is None or d < best:
                best = d
    if best is None:
        # One chain only: the gap that matters is between its own two ends.
        if len(chains) == 1 and len(chains[0]) >= 2:
            return math.hypot(chains[0][0][0] - chains[0][-1][0],
                              chains[0][0][1] - chains[0][-1][1])
        return -1.0
    return best

def _outline(pcb, names):
    """Board edge from POLY records on the OUTLINE layer, chained into contours.

    The file stores one record per edge (a rounded corner is a separate ARC
    record), so the contours have to be rebuilt by joining endpoints. A closed
    contour that contains another one is treated as its cutout - containment is
    measured, not assumed.
    """
    prims = []
    note = None
    for rec in pcb.get("POLY", []):
        if len(rec) < 7:
            continue
        if _layer_of(pcb, rec[4], names) != OUTLINE_LAYER_NAME:
            continue
        prims.extend(_parse_geometry(rec[6]))
    if not prims:
        return [], [], "no POLY record on the %s layer" % OUTLINE_LAYER_NAME, []

    chains = [c for c in _chain(prims) if len(c) >= 2]
    closed = []
    open_chains = []
    for chain in chains:
        if len(chain) >= 3 and _close(chain[0], chain[-1], 0.5):
            closed.append(chain)
        else:
            open_chains.append(chain)
    if not closed:
        return ([], [], ("%d board-edge segments on the %s layer do not close "
                         "into a contour (the nearest free ends are %.4f mil "
                         "apart); they are returned as open chains, not fitted "
                         "into a fake outline"
                         % (len(prims), OUTLINE_LAYER_NAME, _join_gap(chains))),
                [_to_mm_poly(c) for c in open_chains])
    closed.sort(key=_area, reverse=True)
    outer = closed[0]
    cutouts = []
    other = []
    for chain in closed[1:]:
        if _inside(chain[0], outer):
            cutouts.append(chain)
        else:
            other.append(chain)
    if other and not note:
        note = ("%d closed contour(s) outside the largest one: panels or "
                "duplicate outlines" % len(other))
    outline = [_to_mm_poly(outer)] + [_to_mm_poly(c) for c in other]
    if open_chains and not note:
        note = ("%d open edge chain(s) besides the closed contour(s)" %
                len(open_chains))
    return (outline, [_to_mm_poly(c) for c in cutouts], note,
            [_to_mm_poly(c) for c in open_chains])

def _rules(board, pcb):
    out = {}
    for rec in pcb.get("RULE", []):
        if len(rec) < 5:
            continue
        rule_id, name, enabled, payload = rec[1], rec[2], rec[3], rec[4]
        if not enabled or not isinstance(payload, list) or len(payload) < 2:
            continue
        unit, value = payload[0], payload[1]
        entry = {"rule_id": str(rule_id), "unit_label": unit}
        if isinstance(value, (int, float)):
            entry["value_mm"] = mm(value)
        elif isinstance(value, list):
            entry["matrix_mm"] = [[mm(v) for v in row] for row in value
                                  if isinstance(row, list)]
        out[str(name)] = entry
    if out:
        board.rules["lceda"] = out
        board.rules["source"] = ("RULE records; every number is mil, the label in "
                                 "RULE[4][0] is the unit the user typed it in")

def _stackup(board, pcb, names):
    """LAYER_PHYS: [id, ?, ?, ?, ?, ?, ?]. Only the adjacency and copper weight
    are reliable, so that is all that is reported."""
    entries = []
    for rec in pcb.get("LAYER_PHYS", []):
        if len(rec) < 2:
            continue
        entries.append({"id": rec[1],
                        "raw": rec,
                        "name": _layer_of(pcb, rec[1], names)})
    if not entries:
        return
    total = 0.0
    for ent in entries:
        rec = ent["raw"]          # ["LAYER_PHYS", id, material, mil, er, extra, 1]
        thickness = _num(rec[3]) if len(rec) > 3 else None
        ent["material"] = rec[2] if len(rec) > 2 else None
        ent["thickness_mm"] = mm(thickness) if thickness is not None else None
        ent["thickness_unit"] = "mil"
        ent["er"] = (rec[4] if len(rec) > 4 and isinstance(rec[4], (int, float))
                     else None)
        ent["extra"] = rec[5] if len(rec) > 5 else None
        if ent["thickness_mm"]:
            total += ent["thickness_mm"]
        del ent["raw"]
    board.stackup = entries
    board.stackup_total_mm = round(total, 4) if total else None
    board.thickness_mm = board.stackup_total_mm

# ------------------------------------------------------------------ inspect
def inspect(path, sample=3):
    """Report what is inside without interpreting it.

    This is the tool that keeps the field map honest: it prints the container,
    every document, the measured arity and position types of every tag, and a
    few real records, so a wrong assumption in the map above can be seen instead
    of silently producing a board that never existed.
    """
    container = _open_container(path)
    try:
        out = {"status": "ok", "path": path,
               "container": ("epro_zip" if isinstance(container, epro_doc.EproDocument)
                             else "sqlite"),
               "coord_unit": "mil", "mm_per_coord": MIL_TO_MM,
               "unit_verified": True}
        if isinstance(container, epro_doc.EproDocument):
            out["project"] = container.project()
            out["boards"] = container.boards()
            out["members"] = [{"name": n, "bytes": len(container.entries[n])}
                              for n in container.names()]
            out["verify"] = container.verify()
        else:
            out["db_versions"] = container.db_versions()
            out["boards"] = container.boards()
            out["project"] = {k: v for k, v in (container.project() or {}).items()
                              if k != "boards"} if container.project() else None
            out["documents"] = container.documents()

        docs = []
        for name, stream_docs in _pcb_streams(container):
            for doc in stream_docs:
                recs = _records(doc)
                docs.append({
                    "member": name,
                    "kind": doc.kind,
                    "version": doc.version,
                    "display_unit": doc.display_unit(),
                    "n_lines": len(doc.lines),
                    "tags": {tag: {"count": len(rows),
                                   "arity": sorted({len(r) for r in rows}),
                                   "samples": rows[:sample]}
                             for tag, rows in sorted(recs.items())},
                })
                if not sample:
                    for ent in docs[-1]["tags"].values():
                        ent.pop("samples", None)
        out["documents"] = docs
        if not docs:
            out["note"] = ("no board stream found; this file holds no PCB document"
                           if not isinstance(container, eproj_doc.EprjDocument)
                           else "no documents row with docType=3")
        return out
    finally:
        close = getattr(container, "close", None)
        if close:
            close()
