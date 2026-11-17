"""Where a part is allowed to stand, ranked, in one verdict.

`shape_gate` answers "is this copper drawn the way the house draws it". This is
its sibling for the other half of the same complaint: the reviewer kept saying
"the layout did not change at all" and "the MCP does not constrain you enough",
and the reason a generator can say that and still ship is that placement was
never turned into a **refusal**. `layout_quality` measured crowding and flow,
`board_layout_metrics` scored a board against a corpus, and none of it ever said
"no".

So the rules below are ordered by how expensive they are to discover late:

1. `hole_in_pad` - a via drill standing inside a pad. Not a routing style
   question: it is a process defect (the drill pierces the pad, the barrel wicks
   solder out of the joint), and the board cannot be fixed by moving copper. On
   the SOM board 62 of these survived a full DRC because the pad and the via
   share a net, so no clearance rule fires. The test is geometric: the drill
   wall must not reach the pad's copper, so the pad outline is taken from its
   real polygon (round, oval and rounded-rect included) and a hole that stays
   clear of that outline is a legal dogbone.
2. `courtyard_overlap` - two bodies occupy the same square millimetre. Whatever
   the netlist says, one of them has to move, and every route attached to it is
   wasted work.
3. `edge_keepout` - a body closer to the board edge than the assembly house can
   route. Cheap to check, and a re-spin if it is missed.
4. `copper_under_magnetics` - another net's copper running under an inductor.
   The field there is not the field the inductor was specified for, and the DRC
   has no opinion about it at all.
5. `decoupling_far` - a decoupling capacitor sitting further from the pin it
   serves than the loop it is supposed to shorten. Soft at `SOFT_MM`, blocking
   past `HARD_MM`, because "far" depends on the package and the edge rate and
   this module only knows distances.

Every threshold is a judgement and is echoed back with the number that used it.
Nothing here moves, routes or writes; it reads a `canonical.Board`.

Nothing here is a route.
"""
import math

from .calc.result import ok, err

#: The rules, highest priority first. Kept as data so a caller can print the
#: ordering without running anything.
RULES = (
    {"priority": 1, "id": "hole_in_pad", "blocking": True,
     "rule": "no via drill stands inside a pad"},
    {"priority": 2, "id": "courtyard_overlap", "blocking": True,
     "rule": "no two bodies share the same square millimetre"},
    {"priority": 3, "id": "edge_keepout", "blocking": True,
     "rule": "no body closer to the board edge than the assembly house can route"},
    {"priority": 4, "id": "copper_under_magnetics", "blocking": True,
     "rule": "no foreign net runs under an inductor body"},
    {"priority": 5, "id": "decoupling_far", "blocking": False,
     "rule": "a decoupling capacitor sits near the pin it serves"},
)

#: A body closer than this to the board edge is refused. Judgement: 0.5 mm is the
#: usual minimum for a routed edge with a 0.1 mm tool and a 0.3 mm edge clearance.
EDGE_KEEPOUT_MM = 0.5

#: A decoupling cap is "far" past this, and blocking past the hard cap. Both are
#: judgements about a 0402 loop, not a measurement of the real edge rate.
SOFT_MM = 3.0
HARD_MM = 4.5

#: A footprint whose courtyard covers more than this fraction of the board is a
#: module/connector body that is *supposed* to touch the edge (the SOM's 140-pin
#: stamp holes cover the whole board). Exempting it by area is a measurement, not
#: a name list, so a renamed part cannot sneak out of the rule.
MODULE_AREA_FRAC = 0.40

GROUND_HINTS = ("gnd", "ground", "vss", "agnd", "dgnd", "0v")

#: Past this capacitance a two-terminal part is a reservoir, not a decoupling cap:
#: its job is charge, and its placement rule is "anywhere on the rail", not "inside
#: the pin's loop". Below or at it, the loop distance is what matters. Judgement,
#: and it is what separates the 0.1 uF parts from the 10 uF bulks on this board.
BULK_UF = 1.0


def _uf(value):
    """Capacitance in microfarads from a value string, or None if it is not one.

    Handles 100nF / 10uF / 4.7uF / 10.0uF / 1n5-style spellings. It never guesses:
    a value it cannot read returns None and the cap is judged as a decoupling part.
    """
    txt = str(value or "").strip().lower().replace(" ", "")
    if not txt:
        return None
    mult = None
    for suf, m in (("uf", 1.0), ("nf", 1e-3), ("pf", 1e-6), ("u", 1.0),
                   ("n", 1e-3), ("p", 1e-6)):
        if txt.endswith(suf):
            txt, mult = txt[:-len(suf)], m
            break
    if mult is None:
        return None
    try:
        return float(txt) * mult
    except ValueError:
        return None


def _is_ground(name):
    low = (name or "").lower().strip("/")
    return any(low == h or low.startswith(h + "_") or low.endswith("_" + h)
               for h in GROUND_HINTS)


def _bbox_of(box):
    if not box:
        return None
    return (float(box["x_min"]), float(box["y_min"]),
            float(box["x_max"]), float(box["y_max"]))


def _contour_boxes(fp):
    """Courtyard contours as rectangles, one box per contour.

    A ring is not a rectangle. The stamp-hole frame is drawn as four strips; its
    convex hull is the whole board, which makes every part inside it "overlap the
    module" - the reason this gate used to exempt J1 by AREA and, by exempting it,
    stopped seeing that a capacitor was standing on the castellations.
    """
    out = []
    for poly in ((fp.get("courtyard") or {}).get("contours") or []):
        if len(poly) < 3:
            continue
        xs = [float(p[0]) for p in poly]
        ys = [float(p[1]) for p in poly]
        out.append((min(xs), min(ys), max(xs), max(ys)))
    return out

def _contour_area(fp):
    """Area the courtyard actually covers. Bounding boxes lie about frames."""
    tot = 0.0
    for poly in ((fp.get("courtyard") or {}).get("contours") or []):
        if len(poly) < 3:
            continue
        s = 0.0
        for i in range(len(poly)):
            x1, y1 = float(poly[i][0]), float(poly[i][1])
            x2, y2 = float(poly[(i + 1) % len(poly)][0]), float(poly[(i + 1) % len(poly)][1])
            s += x1 * y2 - x2 * y1
        tot += abs(s) / 2.0
    return tot

def _fp_box(fp):
    cy = fp.get("courtyard") or {}
    box = _bbox_of(cy.get("bbox")) or _bbox_of(fp.get("bbox"))
    if box is None:
        x, y = float(fp.get("x") or 0.0), float(fp.get("y") or 0.0)
        box = (x, y, x, y)
    return box


def _board_box(board):
    xs, ys = [], []
    for poly in (board.outline or []):
        for p in poly:
            xs.append(float(p[0]))
            ys.append(float(p[1]))
    for chain in (board.outline_open or []):
        for p in chain:
            xs.append(float(p[0]))
            ys.append(float(p[1]))
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def _box_area(box):
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _box_overlap(a, b):
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0.0 or h <= 0.0:
        return 0.0
    return w * h


def _hull(points):
    """Monotone-chain convex hull. Courtyards are rectangles in practice, so the
    hull is the contour and the separating-axis test below is exact for them."""
    pts = sorted(set((round(float(p[0]), 5), round(float(p[1]), 5)) for p in points))
    if len(pts) <= 2:
        return pts

    def half(seq):
        out = []
        for p in seq:
            while len(out) >= 2:
                (x1, y1), (x2, y2) = out[-2], out[-1]
                if (x2 - x1) * (p[1] - y1) - (y2 - y1) * (p[0] - x1) <= 0:
                    out.pop()
                else:
                    break
            out.append(p)
        return out

    return half(pts)[:-1] + half(pts[::-1])[:-1]


def _convex_overlap(a, b):
    """Separating axis test between two convex polygons, with a `tol` inset so two
    courtyards drawn exactly flush do not read as a collision."""
    if not a or not b:
        return False
    for poly in (a, b):
        n = len(poly)
        for i in range(n):
            x1, y1 = poly[i]
            x2, y2 = poly[(i + 1) % n]
            ax, ay = y1 - y2, x2 - x1
            norm = math.hypot(ax, ay)
            if norm < 1e-12:
                continue
            ax, ay = ax / norm, ay / norm
            pa = [x * ax + y * ay for x, y in a]
            pb = [x * ax + y * ay for x, y in b]
            if max(pa) <= min(pb) + 1e-6 or max(pb) <= min(pa) + 1e-6:
                return False
    return True


def _pad_polygon(pad):
    """The pad's real copper outline, or None when the pad has no area.

    Using the pad's `w`/`h` rectangle instead is what made this rule report 8
    holes on a board that has 62: an oval pad is 0.56 x 1.40 mm, so a hole at the
    end of a track inside that oval sits outside the 0.56-wide rectangle while
    still removing the pad's copper. The outline comes from the same builder the
    router uses, so the two cannot disagree about where the copper is.
    """
    from .adapters.canonical import pad_points
    try:
        pts = pad_points(pad)
    except Exception:
        return None
    return pts if len(pts) >= 3 else None

def _point_in_polygon(px, py, pts):
    inside = False
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        if (y1 > py) != (y2 > py):
            if px < (x2 - x1) * (py - y1) / (y2 - y1) + x1:
                inside = not inside
    return inside

def _seg_point_distance(px, py, x1, y1, x2, y2):
    dx, dy = x2 - x1, y2 - y1
    l2 = dx * dx + dy * dy
    if l2 < 1e-18:
        return math.hypot(px - x1, py - y1)
    t = ((px - x1) * dx + (py - y1) * dy) / l2
    if t <= 0.0:
        return math.hypot(px - x1, py - y1)
    if t >= 1.0:
        return math.hypot(px - x2, py - y2)
    return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))

def _to_polygon(px, py, pts):
    """Distance from a point to a closed outline; 0.0 when the point is inside."""
    if _point_in_polygon(px, py, pts):
        return 0.0
    best = float("inf")
    n = len(pts)
    for i in range(n):
        d = _seg_point_distance(px, py, pts[i][0], pts[i][1],
                                pts[(i + 1) % n][0], pts[(i + 1) % n][1])
        if d < best:
            best = d
    return best

def _rect_contains(cx, cy, pad):
    """Is (cx, cy) inside this pad's copper rectangle?

    Kept for callers that only have a pad box (a footprint courtyard probe, a
    test fixture). It is NOT the `hole_in_pad` test any more: a drill removes pad
    copper whenever its wall crosses the outline, and the rectangle is a poor
    stand-in for that outline on a round or oval pad.
    """
    px, py = float(pad.get("x") or 0.0), float(pad.get("y") or 0.0)
    w = float(pad.get("w") or 0.0)
    h = float(pad.get("h") or 0.0)
    if w <= 0.0 or h <= 0.0:
        return False
    ang = math.radians(-float(pad.get("rotation") or 0.0))
    ca, sa = math.cos(ang), math.sin(ang)
    dx, dy = cx - px, cy - py
    lx = dx * ca - dy * sa
    ly = dx * sa + dy * ca
    return abs(lx) <= w / 2.0 and abs(ly) <= h / 2.0


def _seg_hits_box(x1, y1, x2, y2, box):
    """Liang-Barsky. Returns the clipped length inside the box, in mm."""
    xmin, ymin, xmax, ymax = box
    dx, dy = x2 - x1, y2 - y1
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, x1 - xmin), (dx, xmax - x1),
                 (-dy, y1 - ymin), (dy, ymax - y1)):
        if abs(p) < 1e-12:
            if q < 0.0:
                return 0.0
            continue
        r = q / p
        if p < 0.0:
            if r > t1:
                return 0.0
            if r > t0:
                t0 = r
        else:
            if r < t0:
                return 0.0
            if r < t1:
                t1 = r
    return max(0.0, t1 - t0) * math.hypot(dx, dy)


def gate(board, edge_keepout_mm=None, soft_mm=None, hard_mm=None, top=20):
    """One verdict for "is this board placed the way the house places it".

    Returns the ranked rule table with each rule's measured value, the blocking
    subset, and a `next_fix` naming the single highest-priority item. It is not a
    DRC: clearance, mask, holes-to-hole and connectivity are `board_drc`'s job.
    """
    fps = list(board.footprints or [])
    pads = list(board.pads() or [])
    bbox = _board_box(board)
    if bbox is None:
        return err("this board declares no outline, so edge distance and the "
                   "module-area exemption cannot be measured",
                   "NO_OUTLINE",
                   hint="draw the board edge (Edge.Cuts) first; a placement gate "
                        "that guesses an outline would pass boards it cannot see")
    board_area = _box_area(bbox) or 1.0
    edge_k = float(EDGE_KEEPOUT_MM if edge_keepout_mm is None else edge_keepout_mm)
    soft = float(SOFT_MM if soft_mm is None else soft_mm)
    hard = float(HARD_MM if hard_mm is None else hard_mm)

    boxes = {}
    modules = set()
    solid = set()
    for fp in fps:
        ref = fp.get("ref")
        box = _fp_box(fp)
        boxes[ref] = box
        if _box_area(box) / board_area >= MODULE_AREA_FRAC:
            # Its courtyard edge IS the board edge, so an edge distance is not a
            # measurement. Two different questions, two different answers.
            modules.add(ref)
        if _contour_area(fp) / board_area >= MODULE_AREA_FRAC:
            # A courtyard that really covers the board cannot collide with
            # anything. A frame covers 9% of it and very much can.
            solid.add(ref)

    # -- 1 hole_in_pad ------------------------------------------------------
    # The test is "the drill wall removes pad copper", not "the drill centre is
    # inside the pad's box". A hole whose wall stays clear of the outline is a
    # dogbone and is fine; a hole that grazes the outline is a process defect
    # however small the overlap, because that is where the barrel meets the
    # joint. `hole_gap_mm` is echoed so a reader can see how deep it went.
    hole_hits = []
    pad_polys = []
    for p in pads:
        pts = _pad_polygon(p)
        if pts is not None:
            pad_polys.append((p, pts))
    for v in (board.vias or []):
        vx, vy = float(v.get("x") or 0.0), float(v.get("y") or 0.0)
        vnet = v.get("net")
        drill = float(v.get("drill") or 0.0)
        if drill <= 0.0:
            continue
        for p, pts in pad_polys:
            if p.get("net") != vnet:
                continue
            gap = _to_polygon(vx, vy, pts)
            if gap < drill / 2.0 - 1e-6:
                hole_hits.append({
                    "ref": p.get("ref"), "pad": p.get("number"), "net": vnet,
                    "via": [round(vx, 3), round(vy, 3)],
                    "drill_mm": round(drill, 3),
                    "hole_gap_mm": round(gap, 3),
                    "layer": v.get("from") if v.get("from") == v.get("to")
                    else "%s-%s" % (v.get("from"), v.get("to")),
                })
                break

    # -- 2 courtyard_overlap ------------------------------------------------
    overlaps = []
    refs = [r for r in boxes if r not in solid]
    by_ref = {}
    for fp in fps:
        by_ref[fp.get("ref")] = fp
    for i in range(len(refs)):
        for j in range(i + 1, len(refs)):
            ra, rb = refs[i], refs[j]
            a, b = boxes[ra], boxes[rb]
            if _box_overlap(a, b) <= 0.0:
                continue
            fa, fb = by_ref[ra], by_ref[rb]
            ca, cb = _contour_boxes(fa), _contour_boxes(fb)
            if ca and cb:
                best = 0.0
                for x in ca:
                    for y in cb:
                        if _convex_overlap(_hull([(x[0], x[1]), (x[2], x[1]),
                                                  (x[2], x[3]), (x[0], x[3])]),
                                           _hull([(y[0], y[1]), (y[2], y[1]),
                                                  (y[2], y[3]), (y[0], y[3])])):
                            best = max(best, min(_box_overlap(x, y),
                                                 _box_area(x), _box_area(y)))
                if best <= 0.0:
                    continue
                how = "courtyard contour"
            else:
                best = min(_box_overlap(a, b), _box_area(a), _box_area(b))
                how = "courtyard bbox (no closed contour to test)"
            overlaps.append({"a": ra, "b": rb, "how": how,
                             "overlap_mm2": round(best, 3)})
    overlaps.sort(key=lambda r: -r["overlap_mm2"])

    # -- 3 edge_keepout -----------------------------------------------------
    edge_hits = []
    for ref, box in boxes.items():
        if ref in modules:
            continue
        dist = min(box[0] - bbox[0], box[1] - bbox[1],
                   bbox[2] - box[2], bbox[3] - box[3])
        if dist < edge_k:
            edge_hits.append({"ref": ref, "clearance_mm": round(dist, 3)})
    edge_hits.sort(key=lambda r: r["clearance_mm"])

    # -- 4 copper_under_magnetics -------------------------------------------
    magnetics = [f for f in fps
                 if str(f.get("ref") or "").upper().startswith("L")
                 and "inductor" in str(f.get("lib_id") or "").lower()]
    under = []
    for f in magnetics:
        ref = f.get("ref")
        box = boxes[ref]
        own = set(p.get("net") for p in (f.get("pads") or []))
        for t in (board.tracks or []):
            if t.get("net") in own:
                continue
            st, en = t.get("start"), t.get("end")
            if not st or not en:
                continue
            run = _seg_hits_box(float(st[0]), float(st[1]),
                                float(en[0]), float(en[1]), box)
            if run > 0.0:
                under.append({"ref": ref, "net": t.get("net"),
                              "layer": [t.get("layer")],
                              "inside_mm": round(run, 3)})
    # merge per (inductor, net) so one net crossing the body twice reads once
    merged = {}
    for row in under:
        k = (row["ref"], row["net"])
        if k in merged:
            merged[k]["inside_mm"] += row["inside_mm"]
            merged[k]["layer"] = sorted(set(merged[k]["layer"]) | set(row["layer"]))
        else:
            merged[k] = dict(row)
    under = list(merged.values())
    under.sort(key=lambda r: -r["inside_mm"])

    # -- 5 decoupling_far ---------------------------------------------------
    pin_pads = {}
    for p in pads:
        net = p.get("net")
        if net:
            pin_pads.setdefault(net, []).append(p)
    far = []
    for f in fps:
        pf = f.get("pads") or []
        if len(pf) != 2 or not str(f.get("ref") or "").upper().startswith("C"):
            continue
        halls = [p.get("net") for p in pf]
        if not any(_is_ground(n) for n in halls):
            continue
        rail = next(p for p in pf if not _is_ground(p.get("net")))
        net = rail.get("net")
        best, where = None, None
        for q in pin_pads.get(net, []):
            if q.get("ref") == f.get("ref"):
                continue
            d = math.hypot(float(q.get("x") or 0.0) - float(rail.get("x") or 0.0),
                           float(q.get("y") or 0.0) - float(rail.get("y") or 0.0))
            if best is None or d < best:
                best, where = d, "%s/%s" % (q.get("ref"), q.get("number"))
        if best is None or best < soft:
            continue
        uf = _uf(f.get("value"))
        if uf is not None and uf > BULK_UF:
            # A bulk reservoir is placed by charge, not by loop area: the classic
            # rule is "0.1 uF at every pin, bulk anywhere on the rail". So a bulk
            # cap is reported with its number but never blocks, and the row says
            # why - hiding it would be the wrong kind of quiet.
            sev = "bulk"
        else:
            sev = "hard" if best >= hard else "soft"
        far.append({"ref": f.get("ref"), "value": f.get("value"),
                    "microfarad": uf, "net": net, "to": where,
                    "distance_mm": round(best, 3), "severity": sev})
    far.sort(key=lambda r: -r["distance_mm"])

    rules = []

    def _rule(r, value, blocked, detail, items=None):
        rules.append({"priority": r["priority"], "id": r["id"], "rule": r["rule"],
                      "measured": value, "blocking": bool(blocked),
                      "detail": detail, "items": (items or [])[:int(top)]})

    _rule(RULES[0], len(hole_hits), bool(hole_hits),
          "%d via drill(s) stand inside a same-net pad - a process defect, not a "
          "clearance: the barrel wicks solder out of the joint" % len(hole_hits),
          hole_hits)
    _rule(RULES[1], len(overlaps), bool(overlaps),
          "%d footprint pair(s) have overlapping courtyards" % len(overlaps),
          overlaps)
    _rule(RULES[2], len(edge_hits), bool(edge_hits),
          "%d bod(y/ies) closer than %.2f mm to the board edge"
          % (len(edge_hits), edge_k), edge_hits)
    _rule(RULES[3], len(under), bool(under),
          "%d foreign net(s) run under an inductor body" % len(under), under)
    _rule(RULES[4], len(far), bool([r for r in far if r["severity"] == "hard"]),
          "%d cap(s) are %.1f mm or further from the pin they serve: %d decoupling "
          "hard, %d soft, %d bulk (bulk past %.1f uF is reported, never blocking)"
          % (len(far), soft,
             len([r for r in far if r["severity"] == "hard"]),
             len([r for r in far if r["severity"] == "soft"]),
             len([r for r in far if r["severity"] == "bulk"]), BULK_UF),
          far)

    blocking = [r for r in rules if r["blocking"]]
    verdict = "place_pass" if not blocking else "place_fail"
    next_fix = None
    if blocking:
        top_rule = min(blocking, key=lambda r: r["priority"])
        next_fix = {"rule": top_rule["id"], "priority": top_rule["priority"],
                    "measured": top_rule["measured"],
                    "detail": top_rule["detail"]}

    return ok(
        value={
            "verdict": verdict,
            "blocking": [r["id"] for r in blocking],
            "module_bodies_exempt": sorted(modules),
            "next_fix": next_fix,
            "rules": rules,
            "rule_order": [{"priority": r["priority"], "id": r["id"],
                            "blocking_by_default": r["blocking"],
                            "rule": r["rule"]} for r in RULES],
            "read_it_like_this": (
                "verdict is pass only when no blocking rule fires; `rules` is "
                "sorted by priority, so fix rules[0] first. This is NOT a DRC: "
                "clearance, mask and connectivity are board_drc's job. A footprint "
                "whose courtyard covers %.0f%% or more of the board is treated as a "
                "module body and exempt from the overlap and edge rules by "
                "measurement, not by name." % (MODULE_AREA_FRAC * 100)),
        },
        unit="count / mm",
        model="courtyards (drawn contour, convex hull + separating axis) + pad "
              "copper (real outline polygon: round, oval, rounded rectangle) + "
              "via drills + track segments vs inductor bodies, each folded into "
              "a ranked pass/fail",
        basis="the board's own footprint courtyards and pad outlines; a "
              "footprint with no courtyard falls back to its bounding box, and "
              "the fallback is named in the row",
        precision="overlap is exact for convex courtyards (all rectangles here) "
                  "and conservative for concave ones; edge distance uses the "
                  "courtyard bounding box, which is never further out than the "
                  "drawn contour; decoupling distance is pad centre to pad centre",
        assumptions={"edge_keepout_mm": edge_k, "soft_mm": soft,
                     "hard_mm": hard,
                     "module_area_frac": MODULE_AREA_FRAC})
