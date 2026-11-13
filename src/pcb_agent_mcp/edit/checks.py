"""Pre-checks: what the plan can *say* before anything is written.

Everything here is a measurement made from the converted tree, so everything
here is labelled `authoritative: false`. Its job is to catch the mistake before
it lands on disk and before the vendor engine runs - not to replace the vendor
engine.

Two checks earn their keep:

* **outline closure.** An open Edge.Cuts loop is not a board: KiCad reports
  `invalid_outline` and the board house has nothing to mill. Finding that while
  the plan is still a proposal is the whole point.
* **geometry outside the outline.** A part or a track placed off the board is
  silently legal in the file and impossible to build.

Scope is stated, not implied: pads are not part of the clearance pre-check, and
the inside/outside test is bounding-box level. The verdict tool is `board_drc`.
"""
from ..fidelity import sexpr as S
from .. import geometry as G

TOL = 1e-4          # 0.1 um - far below any real feature, far above float noise

def _q(p, nd=4):
    return (round(p[0], nd), round(p[1], nd))

# ------------------------------------------------------------------ outline
def _outline_primitives(doc):
    """Every Edge.Cuts primitive as (kind, points...) in the Y-up frame."""
    out = []
    for node in doc.board().items[1:]:
        if not isinstance(node, S.Seq):
            continue
        head = S.head(node)
        if head not in ("gr_line", "gr_arc", "gr_circle", "gr_poly", "gr_rect"):
            continue
        if S.arg_text(S.child(node, "layer"), 1) != "Edge.Cuts":
            continue
        if head == "gr_line":
            st, en = S.child(node, "start"), S.child(node, "end")
            if st is None or en is None:
                continue
            out.append({"kind": "line",
                        "ends": [(_xy(st)), (_xy(en))],
                        "points": [_xy(st), _xy(en)],
                        "uuid": _uid(node)})
        elif head == "gr_arc":
            st, mid, en = S.child(node, "start"), S.child(node, "mid"), S.child(node, "end")
            if st is None or en is None:
                continue
            pts = [_xy(st), _xy(en)]
            if mid is not None:
                pts.insert(1, _xy(mid))
            out.append({"kind": "arc", "ends": [_xy(st), _xy(en)], "points": pts,
                        "uuid": _uid(node)})
        elif head == "gr_circle":
            c, e = S.child(node, "center"), S.child(node, "end")
            if c is None or e is None:
                continue
            cx, cy = _xy(c)
            r = ((_xy(e)[0] - cx) ** 2 + (_xy(e)[1] - cy) ** 2) ** 0.5
            out.append({"kind": "circle", "closed_shape": True,
                        "ends": [], "points": G.circle_points(cx, cy, r, 32),
                        "center": [cx, cy], "radius": r, "uuid": _uid(node)})
        elif head == "gr_poly":
            pts_node = S.child(node, "pts")
            pts = [_xy(n) for n in S.children(pts_node, "xy")] if pts_node else []
            if len(pts) < 3:
                continue
            out.append({"kind": "polygon", "closed_shape": True, "ends": [],
                        "points": pts, "uuid": _uid(node)})
        elif head == "gr_rect":
            st, en = S.child(node, "start"), S.child(node, "end")
            s, e = S.child(node, "start"), S.child(node, "end")
            if s is None or e is None:
                continue
            x0, y0 = _xy(s)
            x1, y1 = _xy(e)
            corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
            out.extend({"kind": "line", "ends": [corners[i], corners[i + 1]],
                        "points": [corners[i], corners[i + 1]], "uuid": _uid(node)}
                       for i in range(4))
    return out

def _xy(node):
    return (S.arg_num(node, 1, 0.0), -S.arg_num(node, 2, 0.0))

def _uid(node):
    for name in ("tstamp", "uuid"):
        n = S.child(node, name)
        if n is not None:
            v = S.arg(n, 1)
            if isinstance(v, S.Atom):
                return v.text
    return None

def outline_report(doc):
    """Is there a board outline, is it closed, how many loops, where are the gaps."""
    prims = _outline_primitives(doc)
    if not prims:
        return {"present": False, "closed": False, "loops": 0,
                "primitives": 0, "open_ends": [], "gaps_mm": [],
                "message": "no Edge.Cuts geometry at all: this file has no board shape",
                "authoritative": False}

    closed_shapes = [p for p in prims if p.get("closed_shape")]
    chains = [p for p in prims if not p.get("closed_shape")]

    # endpoint graph: degree 2 everywhere means the chain closes
    adj = {}
    for i, p in enumerate(chains):
        for end in p["ends"]:
            adj.setdefault(_q(end), []).append(i)
    odd = sorted(q for q, edges in adj.items() if len(edges) % 2 == 1)

    # components over the chain edges (union of endpoints)
    seen = set()
    loops = len(closed_shapes)
    open_components = 0
    for i in range(len(chains)):
        if i in seen:
            continue
        stack, comp = [i], set()
        while stack:
            j = stack.pop()
            if j in comp:
                continue
            comp.add(j)
            for end in chains[j]["ends"]:
                for k in adj.get(_q(end), ()):
                    if k not in comp:
                        stack.append(k)
        seen |= comp
        comp_ok = all(len(adj.get(_q(e), ())) % 2 == 0
                      for j in comp for e in chains[j]["ends"])
        if comp_ok:
            loops += 1
        else:
            open_components += 1

    gaps = []
    for q in odd:
        best = None
        for other in odd:
            if other == q:
                continue
            d = G.dist_point_point(q, other)
            if best is None or d < best[0]:
                best = (d, other)
        if best:
            gaps.append({"from": list(q), "to": list(best[1]),
                         "gap_mm": round(best[0], 4)})

    pts = [p for prim in prims for p in prim["points"]]
    box = G.bbox_of(pts)
    perimeter = 0.0
    for p in chains:
        perimeter += G.dist_point_point(p["points"][0], p["points"][-1])
    for p in closed_shapes:
        if p["kind"] == "circle":
            import math
            perimeter += 2 * math.pi * p["radius"]

    return {
        "present": True,
        "closed": not odd,
        "loops": loops,
        "open_components": open_components,
        "primitives": len(prims),
        "lines_and_arcs": len(chains),
        "closed_shapes": len(closed_shapes),
        "open_ends": [list(q) for q in odd],
        "gaps_mm": gaps[:8],
        "largest_gap_mm": round(max([g["gap_mm"] for g in gaps] or [0.0]), 4),
        "bbox_mm": {"x0": box[0], "y0": box[1], "x1": box[2], "y1": box[3]},
        "size_mm": {"width": round(box[2] - box[0], 4),
                    "height": round(box[3] - box[1], 4)},
        "perimeter_mm": round(perimeter, 4),
        "authoritative": False,
        "message": ("closed" if not odd else
                    "OPEN: %d loose end(s), largest gap %s mm - a board house "
                    "cannot mill this and KiCad reports invalid_outline"
                    % (len(odd), round(max([g["gap_mm"] for g in gaps] or [0.0]), 4))),
    }

# ------------------------------------------------------------------ outside
def outside_report(doc, margin=0.0):
    """Which placed objects sit outside the outline's bounding box."""
    report = outline_report(doc)
    if not report["present"]:
        return {"checkable": False, "reason": "there is no outline to be inside of",
                "authoritative": False}
    box = report["bbox_mm"]
    x0, x1 = box["x0"] - margin, box["x1"] + margin
    y0, y1 = box["y0"] - margin, box["y1"] + margin

    def inside(p):
        return x0 - TOL <= p[0] <= x1 + TOL and y0 - TOL <= p[1] <= y1 + TOL

    out = {"footprints": [], "tracks": [], "vias": []}
    counts = {"footprints": 0, "tracks": 0, "vias": 0}
    for node in doc.board().items[1:]:
        if not isinstance(node, S.Seq):
            continue
        head = S.head(node)
        if head in ("footprint", "module"):
            at = S.child(node, "at")
            if at is None:
                continue
            counts["footprints"] += 1
            p = _xy(at)
            if not inside(p):
                out["footprints"].append({"ref": _ref(node), "at": list(p)})
        elif head == "segment":
            st, en = S.child(node, "start"), S.child(node, "end")
            if st is None or en is None:
                continue
            counts["tracks"] += 1
            for p in (_xy(st), _xy(en)):
                if not inside(p):
                    out["tracks"].append({"end": list(p), "layer":
                                          S.arg_text(S.child(node, "layer"), 1),
                                          "net": S.arg_text(S.child(node, "net"), 1)})
                    break
        elif head == "via":
            at = S.child(node, "at")
            if at is None:
                continue
            counts["vias"] += 1
            p = _xy(at)
            if not inside(p):
                out["vias"].append({"at": list(p)})
    total = sum(len(v) for v in out.values())
    return {"checkable": True, "counts": counts, "outside": out,
            "outside_total": total, "checked_against": "outline bounding box",
            "authoritative": False,
            "message": ("all inside the outline box" if total == 0 else
                        "%d object(s) outside the outline box" % total),
            "caveat": ("this is a bounding-box test: a concave or circular outline "
                       "needs KiCad's own DRC (board_drc) for a real verdict")}

def _ref(node):
    for t in S.children(node, "fp_text"):
        if S.arg_text(t, 1) == "reference":
            return S.arg_text(t, 2)
    for p in S.children(node, "property"):
        if S.arg_text(p, 1) in ("Reference", "reference"):
            return S.arg_text(p, 2)
    return None

# ------------------------------------------------------------------ copper
def _copper_items(doc):
    """(net_code, layer, geometry, kind) for tracks and vias only."""
    items = []
    for node in doc.board().items[1:]:
        if not isinstance(node, S.Seq):
            continue
        head = S.head(node)
        if head == "segment":
            st, en = S.child(node, "start"), S.child(node, "end")
            if st is None or en is None:
                continue
            w = S.arg_num(S.child(node, "width"), 1) or 0.0
            items.append({"kind": "track", "net": S.arg_text(S.child(node, "net"), 1),
                          "layer": S.arg_text(S.child(node, "layer"), 1),
                          "a": _xy(st), "b": _xy(en), "half": w / 2.0,
                          "uuid": _uid(node)})
        elif head == "via":
            at = S.child(node, "at")
            if at is None:
                continue
            d = S.arg_num(S.child(node, "size"), 1) or 0.0
            items.append({"kind": "via", "net": S.arg_text(S.child(node, "net"), 1),
                          "layer": "*", "a": _xy(at), "b": _xy(at), "half": d / 2.0,
                          "uuid": _uid(node)})
    return items

def copper_clearance(doc, region=None, threshold_mm=0.2, worst=10):
    """Smallest copper-to-copper gap between DIFFERENT nets from tracks and vias.

    `region` is an (x0, y0, x1, y1) window in the Y-up frame: the pre-check only
    looks where the plan touched, so it stays fast on a real board.
    """
    items = _copper_items(doc)
    if region is not None:
        x0, y0, x1, y1 = region
        keep = []
        for it in items:
            if (x0 - threshold_mm <= it["a"][0] <= x1 + threshold_mm and
                    y0 - threshold_mm <= it["a"][1] <= y1 + threshold_mm) or \
               (x0 - threshold_mm <= it["b"][0] <= x1 + threshold_mm and
                    y0 - threshold_mm <= it["b"][1] <= y1 + threshold_mm):
                keep.append(it)
        items = keep
    pairs = []
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            a, b = items[i], items[j]
            if a["net"] == b["net"]:
                continue
            if a["layer"] != "*" and b["layer"] != "*" and a["layer"] != b["layer"]:
                continue
            d = G.dist_segment_segment(a["a"], a["b"], b["a"], b["b"]) - a["half"] - b["half"]
            # a gap of exactly `threshold_mm` is compliant; only a gap that is
            # materially smaller than it is a violation.  Without this epsilon a
            # pair sitting exactly on the rule (float noise of a few 1e-16 mm)
            # would be reported as "closer than", which the geometry engine can
            # never satisfy.
            if d < threshold_mm - 1e-6:
                pairs.append({"gap_mm": round(max(d, 0.0), 4),
                              "net_a": a["net"], "net_b": b["net"],
                              "layer": a["layer"] if a["layer"] != "*" else b["layer"],
                              "at": list(a["a"]) if a["kind"] == "track" else list(a["a"])})
    pairs.sort(key=lambda p: p["gap_mm"])
    return {"scope": "tracks and vias only (pads are left to board_drc)",
            "threshold_mm": threshold_mm, "violations": len(pairs),
            "worst": pairs[:worst], "authoritative": False,
            "measured_in_region": list(region) if region else "whole board"}

# ------------------------------------------------------------------ digest
def digest(doc, before=None):
    """The numbers a plan has to show before it may write anything."""
    report = outline_report(doc)
    counts = {"footprints": 0, "tracks": 0, "vias": 0, "zones": 0, "nets": 0,
              "edge_cuts_primitives": report.get("primitives", 0)}
    nets = set()
    for node in doc.board().items[1:]:
        if not isinstance(node, S.Seq):
            continue
        head = S.head(node)
        if head in ("footprint", "module"):
            counts["footprints"] += 1
        elif head == "segment":
            counts["tracks"] += 1
        elif head == "via":
            counts["vias"] += 1
        elif head == "zone":
            counts["zones"] += 1
        elif head == "net":
            name = S.arg_text(node, 2)
            if name:
                nets.add(name)
    counts["nets"] = len(nets)
    out = {"outline": report, "counts": counts}
    if before is not None:
        was = digest(before)["counts"]
        out["delta"] = {k: counts[k] - was.get(k, 0) for k in counts
                        if counts[k] != was.get(k, 0)}
    return out
