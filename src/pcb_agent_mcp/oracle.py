"""Oracle: measure THIS board and answer the questions an AI actually asks.

The Oracle only ever reads a `canonical.Board`. It must never see a format name,
a ticket, a layer id of a specific EDA, or a raw 0.01-inch integer - if it does,
an adapter leaked and the architecture is broken (docs/07 §3 C3).

Every answer is a measurement, not a boolean:
  * it names the method (`model`) and the approximation it made (`precision`),
  * it reports the margin or the offending geometry, not just "violation: yes",
  * when a fact the format does not carry is needed, it says so and stops.

That last point is the whole reason this layer exists: `cross_split` on a board
with no plane zones cannot be answered, and answering "no split" would be a
lie that the DRC would happily confirm.
"""
import math

from . import geometry as G
from .calc.result import ok, err

# ---------------------------------------------------------------- item model
def _copper_layers(board):
    """Copper layer names, in stackup order (the order is what makes a via span)."""
    names = [str(s.get("name")) for s in (board.stackup or [])
             if str(s.get("name") or "").endswith(".Cu")]
    return names or ["F.Cu", "B.Cu"]


def _layer_span(board, frm, to):
    """Every copper layer a via passes through, inclusive."""
    cu = _copper_layers(board)
    if frm in cu and to in cu:
        i, j = cu.index(frm), cu.index(to)
        return list(cu[min(i, j):max(i, j) + 1])
    return list(cu)


def _item_layers(board, raw_layers):
    """Normalise a pad's layer list to copper layers.

    Two spellings are wildcards, not layer names: `*.Cu` (any copper) and
    `F&B.Cu` (front AND back, which is how KiCad writes an NPTH mounting hole
    that spans the board). Keeping `F&B.Cu` as a literal layer name broke the
    layer test in `clearance` - it never intersected `F.Cu` or `B.Cu`, so a pad
    on it was compared against other `F&B.Cu` pads only, and two stacked
    mounting holes of two footprints came out as a 0.0 mm "violation" on
    cm5_minima that the vendor never reports.
    """
    cu = _copper_layers(board)
    out = []
    for name in raw_layers or []:
        name = str(name)
        if name in ("*.Cu", "*"):
            out.extend(cu)
        elif name == "F&B.Cu":
            out.extend([n for n in cu if n in ("F.Cu", "B.Cu")])
        elif name.endswith(".Cu"):
            out.append(name)
    seen, uniq = set(), []
    for n in out:
        if n not in seen:
            seen.add(n)
            uniq.append(n)
    return uniq


def _items(board, layer=None, nets=None):
    """Flatten pads / tracks / vias into distance-comparable items.

    item = {"kind", "net", "layer", "poly" (for area shapes) | "seg" (a,b,width), "ref"}
    """
    from .adapters.canonical import pad_points
    items = []
    # Two footprints can share a reference (microwave draws its board out of four
    # footprints all called `POLY`, and hierarchical sheets reuse refs). A duplicate
    # ref makes a violation report point at the wrong part, so the occurrence number
    # is appended to the SECOND and later ones.
    seen_refs = {}

    def _uniq_ref(name):
        """Append `#2`, `#3`, ... to a reference that already occurred.

        A duplicate reference is not hypothetical: microwave draws the whole
        board out of four footprints all called `POLY`, and hierarchical sheets
        reuse refs across instances. Two items with the same printed name make a
        measurement useless - the caller cannot tell which one is meant - so the
        occurrence number is part of the name.
        """
        n = seen_refs.get(name, 0) + 1
        seen_refs[name] = n
        return name if n == 1 else "%s#%d" % (name, n)

    for fp in board.footprints:
        for pad in fp.get("pads") or []:
            if layer and layer not in (pad.get("layers") or []):
                # SMD pads sit on one layer; through-hole pads appear on all
                if pad.get("hole_mm") is None:
                    continue
            if nets and pad.get("net") not in nets:
                continue
            pts = pad_points(pad)
            if not pts:
                continue
            hole = pad.get("hole_mm")
            if (pad.get("type") == "np_thru_hole" and hole
                    and float(pad.get("w") or 0) <= float(hole) + 1e-6
                    and float(pad.get("h") or 0) <= float(hole) + 1e-6):
                # An NPTH pad whose copper size equals its drill has NO copper at
                # all (the hole is the entire pad). KiCad's DRC skips it; counting
                # it made cm5_minima's two stacked module mounting holes a 0.0 mm
                # violation nobody else sees.
                continue
            cu = _item_layers(board, pad.get("layers"))
            if not cu:
                # A pad on F.Paste / F.Mask only carries NO copper: it is how a
                # stencil opening is drawn. KiCad's DRC never compares it, and
                # comparing it here invented 0.0 mm "violations" between the paste
                # geometry of neighbouring pads (royalblue54L_feather, openair-max).
                continue
            items.append({"kind": "pad", "net": pad.get("net"),
                          "uuid": pad.get("uuid"),
                          # A local clearance on the pad/footprint replaces the net
                          # class value for anything measured against it.
                          "local_clearance": pad.get("clearance"),
                          "net_tie": (fp.get("ref"), pad.get("net_tie_group"))
                                     if pad.get("net_tie_group") is not None else None,
                          "layer": cu[0],
                          "layers": cu,
                          "poly": pts,
                          "ref": _uniq_ref("%s.%s" % (fp.get("ref") or "?",
                                                   pad.get("number") or "?"))})
    for gi, g in enumerate(board.graphics or []):
        if layer and g.get("layer") != layer:
            continue
        items.append({"kind": "graphic", "net": None, "uuid": g.get("uuid"),
                      "layer": g.get("layer"), "layers": [g.get("layer")],
                      "poly": g["poly"],
                      "ref": _uniq_ref("graphic@%s" % (g.get("layer") or "?"))})
    for fp in board.footprints:
        for gi, g in enumerate(fp.get("graphics") or []):
            if layer and g.get("layer") != layer:
                continue
            items.append({"kind": "graphic", "net": None, "uuid": g.get("uuid"),
                          "layer": g.get("layer"), "layers": [g.get("layer")],
                          "poly": g["poly"],
                          "ref": _uniq_ref("%s.graphic%d" % (fp.get("ref") or "?", gi))})
    for tr in board.tracks:
        if layer and tr.get("layer") != layer:
            continue
        if nets and tr.get("net") not in nets:
            continue
        pts = tr.get("_points") or [tr["start"], tr["end"]]
        for i in range(len(pts) - 1):
            items.append({"kind": "track", "net": tr.get("net"),
                          "uuid": tr.get("uuid"),
                          "layer": tr.get("layer"),
                          "layers": [tr.get("layer")] if tr.get("layer") else [],
                          "seg": (tuple(pts[i]), tuple(pts[i + 1]),
                                  float(tr.get("width") or 0.0)),
                          "ref": "track@%s" % (tr.get("layer") or "?")})
    for via in board.vias:
        if layer and layer not in (via.get("from"), via.get("to")):
            continue
        if nets and via.get("net") not in nets:
            continue
        dia = via.get("diameter") or via.get("drill") or 0.4
        items.append({"kind": "via", "net": via.get("net"),
                      "uuid": via.get("uuid"), "layer": None,
                      "layers": _layer_span(board, via.get("from"), via.get("to")),
                      "poly": G.circle_points(via["x"], via["y"], dia / 2.0, 16),
                      "ref": "via@(%.3f,%.3f)" % (via["x"], via["y"])})
    return items


def _item_bbox(item):
    if "poly" in item:
        return G.bbox_of(item["poly"])
    a, b, w = item["seg"]
    half = w / 2.0
    return (min(a[0], b[0]) - half, min(a[1], b[1]) - half,
            max(a[0], b[0]) + half, max(a[1], b[1]) + half)


def _dist(it1, it2):
    """Edge-to-edge distance between two items (conservative, see precision note)."""
    if "poly" in it1 and "poly" in it2:
        return G.dist_polygon_polygon(it1["poly"], it2["poly"])
    if "poly" in it1:
        poly, seg = it1["poly"], it2["seg"]
        if G.point_in_polygon(seg[0], poly) or G.point_in_polygon(seg[1], poly):
            return 0.0
        d = min(G.dist_point_polygon(seg[0], poly), G.dist_point_polygon(seg[1], poly))
        n = len(poly)
        for i in range(n):
            d = min(d, G.dist_segment_segment(seg[0], seg[1], poly[i], poly[(i + 1) % n]))
        return max(0.0, d - seg[2] / 2.0)
    if "poly" in it2:
        return _dist(it2, it1)
    a1, b1, w1 = it1["seg"]
    a2, b2, w2 = it2["seg"]
    return max(0.0, G.dist_segment_segment(a1, b1, a2, b2) - (w1 + w2) / 2.0)


def _bucketize(items, cell, pad=0.0):
    """Spatial hash. `pad` inflates every bbox so a near pair can never fall into
    two non-adjacent cells - without it, two items 0.7 mm apart at an unlucky
    coordinate land in different buckets and are never compared at all (the bug
    this pad exists to prevent)."""
    grid = {}
    for idx, item in enumerate(items):
        x0, y0, x1, y1 = _item_bbox(item)
        x0, y0, x1, y1 = x0 - pad, y0 - pad, x1 + pad, y1 + pad
        for gx in range(int(math.floor(x0 / cell)), int(math.floor(x1 / cell)) + 1):
            for gy in range(int(math.floor(y0 / cell)), int(math.floor(y1 / cell)) + 1):
                grid.setdefault((gx, gy), []).append(idx)
    return grid

def _bbox_list(items):
    """Bounding boxes once per item, in the same order.

    `_nearest_from` needs them to skip items that are certainly farther than the
    best found so far; a 200-cell-wide band otherwise calls the exact
    polygon/segment distance on every candidate in nine cells per sample point.
    """
    return [_item_bbox(it) for it in items]

def _nearest_from(items, buckets, cell, px, py, rings=3, bboxes=None):
    """The nearest item to the point (px, py), or None.

    Ring by ring, stopping once something has been found: an item 0.5 mm away sits
    in the NEXT cell when the sample lands near a cell boundary, so a single-cell
    lookup reported "nothing nearby" and every distance collapsed to whatever the
    bounds allowed - the bug that made a via look legal on top of a pad.

    Returns (distance, item). One implementation for every caller, because two
    copies of this drifted apart once already.
    """
    if not items:
        return None
    gx = int(math.floor(px / cell))
    gy = int(math.floor(py / cell))
    probe = {"seg": ((px, py), (px, py), 0.0)}
    best = None
    for r in range(rings + 1):
        for ox in range(-r, r + 1):
            for oy in range(-r, r + 1):
                if r and max(abs(ox), abs(oy)) != r:
                    continue
                for idx in (buckets.get((gx + ox, gy + oy)) or ()):
                    it = items[idx]
                    if bboxes is not None and best is not None:
                        bb = bboxes[idx]
                        gap_x = max(0.0, bb[0] - px, px - bb[2])
                        gap_y = max(0.0, bb[1] - py, py - bb[3])
                        # The point-to-bbox gap is a LOWER bound on the point-to-
                        # copper distance, and a track's `_dist` subtracts half its
                        # width from the centreline distance, so give that half
                        # width back before comparing - a bound that is too high
                        # would skip a genuinely nearer item.
                        bound = math.hypot(gap_x, gap_y)
                        if it.get("seg"):
                            bound -= it["seg"][2] / 2.0
                        if bound >= best[0]:
                            continue
                    d = _dist(probe, it)
                    if best is None or d < best[0]:
                        best = (d, it)
        if best is not None and r >= 1:
            break
    return best


# ------------------------------------------------------------------- queries
def _stackup_total_mm(board):
    """Sum of the declared layers, or None when no layer declares a thickness.

    KiCad's own `stats` sums the stackup and ignores `(general (thickness ...))`,
    so these two numbers disagreeing is not cosmetic: the finished board is the
    stackup total. A file that says 1.6 and sums to 1.18 was written by us for
    as long as `create_board` divided the dielectrics wrongly, and no gate saw it.
    """
    total = 0.0
    seen = False
    for layer in board.stackup or []:
        v = layer.get("thickness_mm")
        if isinstance(v, (int, float)) and v > 0:
            total += float(v)
            seen = True
    return round(total, 4) if seen else None


def summary(board):
    data = board.as_dict(items=False)
    stackup_mm = _stackup_total_mm(board)
    stated = board.thickness_mm
    agrees = (None if stated is None or stackup_mm is None
              else abs(float(stated) - stackup_mm) <= 0.005)
    return ok(
        value=data["counts"],
        unit="counts",
        model="canonical_model_summary",
        basis="counts and presence flags read directly from the parsed "
              "canonical model (no estimation)",
        precision="exact for counts; outline bbox is the bounding box of the "
                  "outline vertices, not the copper extents; thickness_agrees "
                  "compares the stated finished thickness with the stackup sum "
                  "(the stackup sum is what a fab builds)",
        assumptions={"format": board.source.get("format"),
                     "path": board.source.get("path")},
        extra={
            "source": board.source,
            "outline_bbox": data["outline_bbox"],
            "thickness_mm": board.thickness_mm,
            "thickness_from_stackup_mm": stackup_mm,
            "thickness_agrees": agrees,
            "stackup": board.stackup,
            "present": board.present,
            "absent": board.absent,
            "rules": board.rules,
        },
    )


def components(board, layer=None, limit=None, offset=0):
    rows = []
    for fp in board.footprints:
        pads = fp.get("pads") or []
        nets = sorted({p.get("net") for p in pads if p.get("net")})
        rows.append({
            "ref": fp.get("ref"),
            "value": fp.get("value"),
            "lib_id": fp.get("lib_id"),
            "x_mm": fp.get("x"),
            "y_mm": fp.get("y"),
            "rotation_deg": fp.get("rotation"),
            "layer": fp.get("layer"),
            "pads": len(pads),
            "nets": nets,
            "bbox": fp.get("bbox"),
        })
    if layer:
        rows = [r for r in rows if r["layer"] == layer]
    total = len(rows)
    start = max(0, int(offset or 0))
    page = rows[start:] if limit is None else rows[start:start + max(0, int(limit))]
    returned = len(page)
    truncated = start + returned < total
    extra = {"total": total, "returned": returned, "truncated": truncated}
    if truncated:
        extra["next_offset"] = start + returned
        extra["message"] = ("components %d-%d of %d; call again with offset=%d for "
                            "the next page" % (start + 1, start + returned, total,
                                               start + returned))
    return ok(
        value=page,
        unit="list",
        model="canonical_component_table",
        basis="footprint placement and pad-to-net assignment read from the file",
        precision="exact for positions/rotations; bbox is derived from pad extents "
                  "(courtyard and 3D height are not included)",
        assumptions={"count": total, "returned": returned, "layer_filter": layer,
                     "offset": start, "truncated": truncated},
        extra=extra,
    )


def nets(board, limit=None, offset=0):
    stats = {}
    for fp in board.footprints:
        for pad in fp.get("pads") or []:
            net = pad.get("net")
            if net in (None, ""):
                continue
            entry = stats.setdefault(str(net), {"pads": 0, "tracks": 0, "vias": 0,
                                                "zones": 0, "track_length_mm": 0.0})
            entry["pads"] += 1
    for tr in board.tracks:
        net = tr.get("net")
        if net in (None, ""):
            continue
        entry = stats.setdefault(str(net), {"pads": 0, "tracks": 0, "vias": 0,
                                            "zones": 0, "track_length_mm": 0.0})
        entry["tracks"] += 1
        pts = tr.get("_points") or [tr["start"], tr["end"]]
        for i in range(len(pts) - 1):
            entry["track_length_mm"] += G.dist_point_point(pts[i], pts[i + 1])
    for via in board.vias:
        net = via.get("net")
        if net in (None, ""):
            continue
        stats.setdefault(str(net), {"pads": 0, "tracks": 0, "vias": 0,
                                    "zones": 0, "track_length_mm": 0.0})["vias"] += 1
    for zone in board.zones:
        net = zone.get("net")
        if net in (None, ""):
            continue
        stats.setdefault(str(net), {"pads": 0, "tracks": 0, "vias": 0,
                                    "zones": 0, "track_length_mm": 0.0})["zones"] += 1
    for entry in stats.values():
        entry["track_length_mm"] = round(entry["track_length_mm"], 3)
    items = list(stats.items())
    total = len(items)
    start = max(0, int(offset or 0))
    page_items = items[start:] if limit is None else items[start:start + max(0, int(limit))]
    page = dict(page_items)
    returned = len(page)
    truncated = start + returned < total
    extra = {"total": total, "returned": returned, "truncated": truncated}
    if truncated:
        extra["next_offset"] = start + returned
        extra["message"] = ("nets %d-%d of %d; call again with offset=%d for the "
                            "next page" % (start + 1, start + returned, total,
                                           start + returned))
    return ok(
        value=page,
        unit="per-net counts",
        model="canonical_net_table",
        basis="pads/tracks/vias/zones grouped by net name; length is the sum of "
              "centreline segment lengths",
        precision="exact counts; track_length ignores arc true length (arcs are "
                  "polyline-approximated by the adapter)",
        assumptions={"nets": total, "returned": returned, "offset": start,
                     "truncated": truncated},
        extra=extra,
    )


def clearance(board, net_a=None, net_b=None, threshold_mm=0.2, layer=None, worst=20,
              with_items=False):
    """Minimum edge-to-edge distance between copper of DIFFERENT nets.

    Answers "is anything too close", not just "how many violations": every pair
    below the threshold is returned with both references and the measured gap, so
    the model can decide what to move.

    `with_items=True` attaches the two raw item dicts (their `poly` / `seg`) to every
    returned pair. It exists for the cross-check against the vendor DRC, which only
    says WHERE it saw the violation - matching a reported position back to our own
    shapes needs the shapes, not just the names.
    """
    if net_a and net_b and net_a == net_b:
        return err("net_a and net_b are the same net; clearance is between different nets",
                   "ARG_INVALID")
    nets_filter = None
    if net_a or net_b:
        nets_filter = {n for n in (net_a, net_b) if n}
    items = _items(board, layer=layer, nets=nets_filter)
    if nets_filter:
        present_nets = {it["net"] for it in items}
        missing = sorted(n for n in nets_filter if n not in present_nets)
        if missing:
            all_nets = sorted({t.get("net") for t in board.tracks} |
                              {p.get("net") for p in board.pads()} - {None})
            return err("no copper found on net(s) %s%s" % (", ".join(missing),
                                                           " on layer %r" % layer if layer else ""),
                       "NO_GEOMETRY",
                       hint="nets with copper on this board: %s" % (", ".join(
                           n for n in all_nets if n) or "none"),
                       extra={"nets_missing": missing, "nets_available": all_nets})
    if len(items) < 2:
        return err("fewer than 2 copper items on the requested layer/nets",
                   "NO_GEOMETRY",
                   hint="check whether the adapter read tracks/pads (see board_summary.present)")
    threshold = float(threshold_mm)
    cell = max(threshold * 4.0, 1.0)
    grid = _bucketize(items, cell, pad=cell)
    seen = set()
    pairs = []
    min_pair = None
    for bucket in grid.values():
        for i in range(len(bucket)):
            for j in range(i + 1, len(bucket)):
                a, b = bucket[i], bucket[j]
                key = (a, b) if a < b else (b, a)
                if key in seen:
                    continue
                seen.add(key)
                it1, it2 = items[a], items[b]
                # Two items with the SAME net never need clearance. Copper with NO
                # net is different: KiCad's DRC still compares it (microwave's only
                # two violations are a no-net pad against a no-net copper polygon),
                # so `None == None` must not be read as "same net".
                if it1["net"] is not None and it1["net"] == it2["net"]:
                    continue
                tie = it1.get("net_tie")
                if tie is not None and tie == it2.get("net_tie"):
                    # same footprint, same declared net-tie group: the vendor connects
                    # these pads on purpose (royalblue JP1 is a bridged solder jumper),
                    # so their overlap is not a violation
                    continue
                l1, l2 = it1.get("layers"), it2.get("layers")
                if l1 and l2 and not (set(l1) & set(l2)):
                    continue          # different copper layers: not a 2-D distance
                if net_a and net_b:
                    got = {it1["net"], it2["net"]}
                    if got != {net_a, net_b}:
                        continue
                elif nets_filter and not ((it1["net"] in nets_filter) or
                                          (it2["net"] in nets_filter)):
                    continue
                d = _dist(it1, it2)
                if min_pair is None or d < min_pair["gap_mm"]:
                    min_pair = {"gap_mm": round(d, 4), "a": it1, "b": it2}
                if d < threshold:
                    pair = {
                        "gap_mm": round(d, 4),
                        "net_a": it1["net"], "net_b": it2["net"],
                        "item_a": it1["ref"], "item_b": it2["ref"],
                        "kind_a": it1["kind"], "kind_b": it2["kind"],
                    }
                    if with_items:
                        pair["_item_a"] = it1
                        pair["_item_b"] = it2
                    pairs.append(pair)
    pairs.sort(key=lambda p: p["gap_mm"])
    out = {
        "violations": len(pairs),
        "threshold_mm": threshold,
        "worst": pairs[:int(worst)],
        "min_gap_mm": min_pair["gap_mm"] if min_pair else None,
        "items_checked": len(items),
    }
    if min_pair:
        out["min_gap_between"] = {"a": min_pair["a"]["ref"], "b": min_pair["b"]["ref"],
                                  "net_a": min_pair["a"]["net"],
                                  "net_b": min_pair["b"]["net"]}
    return ok(
        value=out,
        unit="mm",
        model="pairwise_item_distance",
        basis="edge-to-edge distance between pad/via polygons and track segments; "
              "track width is subtracted from the centreline distance",
        precision="conservative for tracks (width subtraction over-reports for angled "
                  "corners); pads are their outline polygon, so corner radius and "
                  "solder-mask expansion are ignored; arcs are polylines; pairs are "
                  "compared inside a %.1f mm spatial window, so `min_gap_mm` is exact "
                  "only when it is below that window and is omitted otherwise"
                  % cell,
        assumptions={"layer": layer, "net_a": net_a, "net_b": net_b,
                     "cell_mm": cell, "items": len(items)},
    )


def free_space(board, layer, grid_mm=1.0, clearance_mm=0.2, top=8):
    """Occupancy grid on one layer -> the largest rectangles a component can sit in.

    The AI's recurring question is "where can this part go", and the answer is a
    region with a margin, not a density percentage.
    """
    if not layer:
        return err("layer is required (e.g. 'F.Cu' or 'TopLayer')", "ARG_MISSING")
    step = float(grid_mm)
    if step <= 0:
        return err("grid_mm must be > 0", "ARG_RANGE")
    items = _items(board)
    items = [it for it in items if it["layer"] in (layer, None)]
    if not items:
        return err("no copper items found on %s" % layer, "NO_GEOMETRY")
    x0 = min(_item_bbox(it)[0] for it in items) - 5.0
    y0 = min(_item_bbox(it)[1] for it in items) - 5.0
    x1 = max(_item_bbox(it)[2] for it in items) + 5.0
    y1 = max(_item_bbox(it)[3] for it in items) + 5.0
    if board.outline:
        ox0, oy0, ox1, oy1 = G.bbox_of([p for poly in board.outline for p in poly])
        x0, y0 = max(x0, ox0), max(y0, oy0)
        x1, y1 = min(x1, ox1), min(y1, oy1)
    cols = max(1, int(math.ceil((x1 - x0) / step)))
    rows = max(1, int(math.ceil((y1 - y0) / step)))
    if cols * rows > 400000:
        return err("grid too fine: %dx%d cells (max 400k). Increase grid_mm" % (cols, rows),
                   "GRID_TOO_FINE")
    blocked = [[False] * cols for _ in range(rows)]
    pad_margin = float(clearance_mm)

    def mark(cx, cy, half):
        gx0 = max(0, int(math.floor((cx - half - x0) / step)))
        gx1 = min(cols - 1, int(math.floor((cx + half - x0) / step)))
        gy0 = max(0, int(math.floor((cy - half - y0) / step)))
        gy1 = min(rows - 1, int(math.floor((cy + half - y0) / step)))
        for gy in range(gy0, gy1 + 1):
            row = blocked[gy]
            for gx in range(gx0, gx1 + 1):
                row[gx] = True

    for item in items:
        if "poly" in item:
            bx = _item_bbox(item)
            pad_margin = float(clearance_mm)
            mark((bx[0] + bx[2]) / 2.0, (bx[1] + bx[3]) / 2.0,
                 max(bx[2] - bx[0], bx[3] - bx[1]) / 2.0 + pad_margin)
        else:
            a, b, w = item["seg"]
            length = G.dist_point_point(a, b)
            steps = max(1, int(math.ceil(length / (step / 2.0))))
            for i in range(steps + 1):
                t = i / float(steps)
                mark(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t,
                     w / 2.0 + pad_margin)
    for zone in board.zones:
        if not zone.get("is_keepout"):
            continue
        if layer not in (zone.get("layers") or []):
            continue
        poly = zone.get("outline") or []
        if len(poly) < 3:
            continue
        zb = G.bbox_of(poly)
        for gy in range(rows):
            cy = y0 + (gy + 0.5) * step
            for gx in range(cols):
                cx = x0 + (gx + 0.5) * step
                if zb[0] <= cx <= zb[2] and zb[1] <= cy <= zb[3] and \
                        G.point_in_polygon((cx, cy), poly):
                    blocked[gy][gx] = True

    rects = _free_rects(blocked, rows, cols, x0, y0, step)
    rects.sort(key=lambda r: r["area_mm2"], reverse=True)
    return ok(
        value={
            "grid": {"x0": round(x0, 3), "y0": round(y0, 3), "step_mm": step,
                     "cols": cols, "rows": rows},
            "free_ratio": round(1.0 - sum(sum(1 for v in row if v) for row in blocked) /
                                float(rows * cols), 4),
            "free_rects": rects[:int(top)],
        },
        unit="mm (rectangles), ratio (dimensionless)",
        model="occupancy_grid",
        basis="copper items (pads, tracks, vias) rasterised with a clearance margin; "
              "free cells merged into rectangles",
        precision="+/-1 grid cell (%.2f mm) on every dimension; a rectangle is "
                  "blocked-or-free per cell, so a sliver thinner than one cell is "
                  "invisible" % step,
        assumptions={"layer": layer, "clearance_mm": float(clearance_mm),
                     "cells": rows * cols},
    )


def _free_rects(blocked, rows, cols, x0, y0, step):
    """Greedy maximal rectangles over free cells (row runs, then vertical growth)."""
    runs = {}
    for gy in range(rows):
        row = blocked[gy]
        gy_runs = []
        gx = 0
        while gx < cols:
            if row[gx]:
                gx += 1
                continue
            start = gx
            while gx < cols and not row[gx]:
                gx += 1
            gy_runs.append((start, gx - 1))
        runs[gy] = gy_runs
    out = []
    for gy in range(rows):
        for (cx0, cx1) in runs[gy]:
            if cx0 > 0 and not blocked[gy][cx0 - 1]:
                continue          # not the left edge of a maximal rectangle
            height = 1
            while gy + height < rows:
                covered = False
                for (nx0, nx1) in runs[gy + height]:
                    if nx0 <= cx0 and cx1 <= nx1:
                        covered = True
                        break
                if not covered:
                    break
                height += 1
            width_mm = (cx1 - cx0 + 1) * step
            height_mm = height * step
            out.append({
                "x_mm": round(x0 + cx0 * step, 3),
                "y_mm": round(y0 + gy * step, 3),
                "w_mm": round(width_mm, 3),
                "h_mm": round(height_mm, 3),
                "area_mm2": round(width_mm * height_mm, 2),
            })
    return out


def routability(board, layer=None, grid_mm=0.2, clearance_mm=None, track_mm=None,
                escape_mm=1.5, worst=20, width_scan=None):
    """Can each pad get out of its own courtyard, and how crowded is the layer?

    This is NOT a router and does not claim a net is routable. It answers the two
    questions that decide whether spending ten minutes routing is worth it:

    * **escape**: from each pad, is there at least one direction in which a track of
      `track_mm` could leave the pad (for `escape_mm` measured from the pad's edge)
      while keeping `clearance_mm` from every other net's copper, every keepout and
      the board edge? A pad with ZERO escape directions is a hard blocker - no
      router, no autorouter and no amount of patience can connect it, and that is
      what a person wants to know before watching a router fail. A pad that a track
      or via of its OWN net already touches is not a blocker: it is connected.
    * **congestion**: what fraction of the layer's usable area is already copper,
      measured on a raster grid (`grid_mm`).

    The escape test is geometric, not rasterised, because the raster lied: a
    0.5 mm-pitch QFP pad cannot be rasterised at 0.2 mm with a 0.325 mm clearance
    margin without every pad's halo swallowing its neighbours, and the first run
    reported 78 of 180 pads as unconnectable on a board where each of those pads
    has a clear lane straight out of the package.
    """
    step = float(grid_mm)
    if step <= 0:
        return err("grid_mm must be > 0", "ARG_RANGE")
    rules = board.rules or {}
    # The rules live in net classes, not in one global number: a board that assigns
    # USB_DIFF 0.2032 mm and Default 0.2 mm must not be measured with whichever of
    # the two happened to be read first.
    classes = dict((k, v) for k, v in (rules.get("net_classes") or {}).items()
                   if isinstance(v, dict))
    clr_by_net, tw_by_net = {}, {}
    for name, spec in classes.items():
        c = spec.get("clearance")
        w = spec.get("track_width")
        if w is None:
            w = spec.get("trace_width")
        for net in spec.get("nets") or []:
            if c is not None:
                clr_by_net[net] = float(c)
            if w is not None:
                tw_by_net[net] = float(w)
    default_cls = classes.get("Default") or {}
    rule_clr = default_cls.get("clearance")
    if rule_clr is None and clr_by_net:
        rule_clr = min(clr_by_net.values())
    rule_tw = default_cls.get("track_width") or default_cls.get("trace_width")
    if rule_tw is None and tw_by_net:
        rule_tw = min(tw_by_net.values())
    floor = (rules.get("clearance") or {}).get("min_clearance")
    floor = float(floor) if floor else None
    clr = float(clearance_mm) if clearance_mm is not None else float(rule_clr or 0.2)
    tw = float(track_mm) if track_mm is not None else float(rule_tw or 0.25)
    if floor is not None and clearance_mm is None:
        clr = max(clr, floor)
    rule_clr_source = ("caller" if clearance_mm is not None
                       else "net class Default" if default_cls.get("clearance")
                       is not None else "net class (loosest)" if clr_by_net
                       else "nothing in the file, 0.2 mm assumed")
    rule_tw_source = ("caller" if track_mm is not None
                      else "net class Default"
                      if rule_tw is not None else "nothing in the file, 0.25 mm assumed")
    escape = float(escape_mm)
    if escape <= 0:
        return err("escape_mm must be > 0", "ARG_RANGE")
    short_escape = max(0.2, min(0.4, escape / 2.0))

    layers = [layer] if layer else [l for l in _copper_layers(board)]
    for name in layers:
        if name not in _copper_layers(board):
            return err("no such copper layer: %r (board has %s)"
                       % (name, ", ".join(_copper_layers(board))), "ARG_INVALID")

    # Copper of other nets, bucketed per layer. Graphics with no net are skipped:
    # a clearance figure cannot be attributed to them, so pretending they block
    # would invent an obstacle.
    items_by_layer, iboxes, buckets = {}, {}, {}
    cell = max(1.0, escape + tw + 1.0)
    for name in layers:
        items = [it for it in _items(board, layer=name) if it.get("net")]
        items_by_layer[name] = items
        iboxes[name] = [_item_bbox(it) for it in items]
        buckets[name] = _bucketize(items, cell, pad=escape + tw + 1.0)

    # Keep the RINGS, do not flatten them: `point_in_polygon` wants a ring, and a
    # flattened point list made every corridor land outside the board.
    outline = None
    if board.outline:
        rings = [poly for poly in board.outline if len(poly) >= 3]
        outline = rings or None

    keepouts_by_layer = {}
    for z in board.zones or []:
        if not z.get("is_keepout"):
            continue
        for name in (z.get("layers") or []):
            keepouts_by_layer.setdefault(name, []).append(z.get("outline") or [])

    # Which pads already HAVE copper leaving them. A pad that a track or via of its
    # own net already touches is connected whatever the free space looks like, and
    # reporting it as "no router can connect it" would be a false alarm.
    by_net = {}
    for tr in board.tracks:
        pts = tr.get("_points") or [tr.get("start"), tr.get("end")]
        for i in range(len(pts) - 1):
            by_net.setdefault(tr.get("net"), {"tracks": [], "vias": []})["tracks"] \
                .append((tuple(pts[i]), tuple(pts[i + 1]),
                         float(tr.get("width") or 0.0)))
    for via in board.vias:
        dia = via.get("diameter") or via.get("drill") or 0.4
        by_net.setdefault(via.get("net"), {"tracks": [], "vias": []})["vias"] \
            .append((float(via["x"]), float(via["y"]), float(dia)))

    def _touches(pad_net, x, y, half):
        own = by_net.get(pad_net)
        if not own:
            return False
        for (x1, y1), (x2, y2), w in own["tracks"]:
            if G.dist_point_segment((x, y), (x1, y1), (x2, y2)) <= half + w / 2.0 + 0.06:
                return True
        for vx, vy, dia in own["vias"]:
            if math.hypot(vx - x, vy - y) <= half + dia / 2.0 + 0.06:
                return True
        return False

    dirs = [(1, 0), (-1, 0), (0, 1), (0, -1),
            (0.7071, 0.7071), (-0.7071, 0.7071),
            (0.7071, -0.7071), (-0.7071, -0.7071)]

    def _lane_blocked(name, x, y, dx, dy, half, net, pad_clr, esc=None,
                      tw_override=None):
        """Is the straight lane out of this pad in this direction illegal?

        Returns `(blocked, why)`, where `why` names the obstacle and how much
        clearance is missing - "it does not fit" is useless next to "it is 0.13 mm
        short of C4 on B's clearance".
        """
        esc = escape if esc is None else esc
        twl = tw if tw_override is None else tw_override
        end = (x + dx * (half + esc), y + dy * (half + esc))
        lane = {"seg": ((x, y), end, twl)}
        slack = pad_clr + twl / 2.0 + 0.05
        bx = _item_bbox(lane)
        cand = set()
        for gx in range(int(math.floor((bx[0] - slack) / cell)),
                        int(math.floor((bx[2] + slack) / cell)) + 1):
            for gy in range(int(math.floor((bx[1] - slack) / cell)),
                            int(math.floor((bx[3] + slack) / cell)) + 1):
                got = buckets[name].get((gx, gy))
                if got:
                    cand.update(got)
        lx0, ly0, lx1, ly1 = bx
        worst = None
        for idx in cand:
            it = items_by_layer[name][idx]
            if it.get("net") == net:
                continue                     # its own copper never blocks it
            ib = iboxes[name][idx]
            # A bounding-box gap is a LOWER bound on the real distance, so a lane
            # whose boxes are far enough apart cannot be too close. Without this
            # the exact polygon/segment distance ran on every item in the bucket
            # and a 180-pad board took 14 s for an answer nobody reads that fast.
            gap_x = max(0.0, ib[0] - lx1, lx0 - ib[2])
            gap_y = max(0.0, ib[1] - ly1, ly0 - ib[3])
            if math.hypot(gap_x, gap_y) >= pad_clr:
                continue
            d = _dist(lane, it)
            if d >= pad_clr - 1e-9:
                continue
            info = {"layer": name, "net": it.get("net"), "kind": it.get("kind"),
                    "reference": it.get("ref"),
                    "needs_mm": round(pad_clr - d, 3)}
            if worst is None or info["needs_mm"] > worst["needs_mm"]:
                worst = info
        if worst is not None:
            return True, worst
        for ring in keepouts_by_layer.get(name) or []:
            if len(ring) >= 3 and any(G.point_in_polygon(
                    (x + dx * t, y + dy * t), ring)
                    for t in (0.0, (half + esc) / 2.0, half + esc)):
                return True, {"layer": name, "reason": "keepout",
                              "needs_mm": None}
        if outline:
            # Three samples on the centreline, plus the two shoulder corners at the
            # far end: a lane that leaves the board is not an escape, even if the
            # copper between here and the edge happens to be free.
            probes = [(x + dx * t, y + dy * t) for t in (0.0, (half + esc) / 2.0,
                                                         half + esc)]
            nx, ny = -dy, dx
            probes.append((end[0] + nx * twl / 2.0, end[1] + ny * twl / 2.0))
            probes.append((end[0] - nx * twl / 2.0, end[1] - ny * twl / 2.0))
            for pt in probes:
                if not any(G.point_in_polygon(pt, ring) for ring in outline):
                    return True, {"layer": name, "reason": "board_edge",
                                  "needs_mm": None}
        return False, None

    pads_out = []
    per_net = {}
    for fp in board.footprints:
        ref = fp.get("ref") or fp.get("reference")
        for pad in fp.get("pads") or []:
            net = pad.get("net")
            if not net:
                continue          # an unconnected pad has nothing to escape to
            hole = pad.get("hole_mm")
            if pad.get("type") == "np_thru_hole" and hole and \
                    float(pad.get("w") or 0) <= float(hole) + 1e-6 and \
                    float(pad.get("h") or 0) <= float(hole) + 1e-6:
                continue          # no copper at all
            x, y = _pad_xy(fp, pad)
            if x is None:
                continue
            pad_layers = [l for l in layers if l in (pad.get("layers") or [])
                          or hole is not None]
            if not pad_layers:
                continue
            half = max(float(pad.get("w") or 0), float(pad.get("h") or 0)) / 2.0
            pad_clr = max(clr, clr_by_net.get(net, clr))
            open_dirs, asked, width_by_dir = [], [], []
            for name in pad_layers:
                for dx, dy in dirs:
                    blocked, why = _lane_blocked(name, x, y, dx, dy, half, net,
                                                 pad_clr)
                    if blocked:
                        asked.append(why)
                        continue
                    open_dirs.append((name, (dx, dy)))
                    if width_scan:
                        # widest track that still fits, on the sizes a layout
                        # actually uses - largest first, stop at the first fit
                        for w in width_scan:
                            b2, _w2 = _lane_blocked(name, x, y, dx, dy, half, net,
                                                    pad_clr, tw_override=float(w))
                            if not b2:
                                width_by_dir.append({"layer": name,
                                                     "dir": [dx, dy],
                                                     "max_width_mm": float(w)})
                                break
            done = _touches(net, x, y, half)
            kind = None
            best = None
            if not open_dirs and not done:
                # Every direction refused: report the one that came CLOSEST, so the
                # caller can say "0.13 mm short of C4" instead of "blocked".
                if asked:
                    best = max(asked, key=lambda w: (w.get("needs_mm") or -1.0))
                else:
                    best = None

                # A pad with no long lane is not necessarily stuck: a person can
                # leave the pad and bend. Ask the same question over a SHORT lane;
                # if even that fails there is no way off the pad at all, which is
                # the claim worth making loudly.
                short = [(_n, (dx, dy)) for _n in pad_layers for dx, dy in dirs
                         if not _lane_blocked(_n, x, y, dx, dy, half, net, pad_clr,
                                              short_escape)[0]]
                kind = "bend_required" if short else "hard"
            entry = {"reference": ref, "pad": pad.get("number"), "net": net,
                     "at": [round(x, 3), round(y, 3)],
                     "escape_directions": len(open_dirs),
                     "layers_with_an_escape": sorted({d[0] for d in open_dirs}),
                     "already_connected": done, "blocker_kind": kind,
                     "why_blocked": best}
            if width_scan:
                entry["widest_escape"] = (max(width_by_dir,
                                              key=lambda d: d["max_width_mm"])
                                          if width_by_dir else None)
            per_net.setdefault(net, {"pads": 0, "blocked": 0, "connected": 0})
            per_net[net]["pads"] += 1
            if done:
                per_net[net]["connected"] += 1
            elif not open_dirs:
                per_net[net]["blocked"] += 1
            pads_out.append(entry)

    narrow = []
    if width_scan:
        for p_ in pads_out:
            w = p_.get("widest_escape")
            if w:
                narrow.append({"reference": p_["reference"], "pad": p_["pad"],
                               "net": p_["net"], "layer": w["layer"],
                               "dir": w["dir"],
                               "max_width_mm": w["max_width_mm"]})
        narrow.sort(key=lambda r: (r["max_width_mm"], r["reference"]))
        narrow = narrow[:int(worst)]

    if not pads_out:
        return err("no pad on this board carries a net, so there is nothing to "
                   "escape from", "NO_GEOMETRY",
                   hint="pads without a net assignment are not inspected: an "
                        "unconnected pad has nothing to be connected to")

    # A pad is only a hard blocker if it is neither already connected nor able to
    # leave: "no router can connect it" must not be said about a pad a track
    # already reaches.
    hard = [p for p in pads_out if p["blocker_kind"] == "hard"]
    bends = [p for p in pads_out if p["blocker_kind"] == "bend_required"]
    blocked = hard
    tight = [p for p in pads_out
             if p["escape_directions"] <= 1 and not p["already_connected"]
             and p["blocker_kind"] is None]
    connected_pads = [p for p in pads_out if p["already_connected"]]

    congestion = {}
    for name in layers:
        items = items_by_layer[name]
        keepouts = [z for z in board.zones
                    if z.get("is_keepout") and name in (z.get("layers") or [])]
        grid = _occupancy(board, items, keepouts, step, clr + tw / 2.0)
        used = inside = 0
        for row in grid["cells"]:
            for cellv in row:
                if cellv is None:
                    continue          # outside the board, not "usable area"
                inside += 1
                if cellv is not False:
                    used += 1
        congestion[name] = round(used / float(inside), 4) if inside else None

    blocked_net = {}
    for p in blocked:
        blocked_net[p["net"]] = blocked_net.get(p["net"], 0) + 1

    if blocked:
        verdict = "blocked_pads"
        why = ("%d pad(s) on %d net(s) have no way off the pad at all (not even a "
               "%.2f mm stub) and no copper of their own net touching them: no "
               "router can connect them as the board stands"
               % (len(blocked), len(blocked_net), short_escape))
    elif bends:
        verdict = "bends_needed"
        why = ("%d pad(s) cannot leave in a straight %.1f mm lane but CAN leave over "
               "%.2f mm, i.e. they need a dogleg: fine for a person, a straight-lane "
               "fanout will not do it" % (len(bends), escape, short_escape))
    elif tight and len(tight) >= max(4, len(pads_out) // 10):
        verdict = "tight"
        why = ("every unconnected pad has at least one way out, but %d pad(s) have "
               "exactly one - a single mistake during layout closes them" % len(tight))
    else:
        verdict = "escapes_exist"
        why = ("every unconnected pad has at least one free direction on this grid "
               "(%d of %d pads are already connected)" % (len(connected_pads),
                                                          len(pads_out)))

    return ok(
        value={
            "verdict": verdict,
            "why": why,
            "pads_checked": len(pads_out),
            "already_connected_pads": len(connected_pads),
            "blocked_pads": len(blocked),
            "bend_required_pads": len(bends),
            "one_way_pads": len(tight),
            "nets_with_blocked_pads": blocked_net,
            "blocked": sorted(blocked, key=lambda p: (p["net"], p["reference"]))[:int(worst)],
            "bend_required": sorted(bends, key=lambda p: (p["net"], p["reference"]))[:int(worst)],
            "one_way": tight[:int(worst)],
            "per_net": {k: v for k, v in sorted(per_net.items())[:int(worst)]},
            "congestion_by_layer": congestion,
            "narrowest_escapes": narrow,
            "grid": {"step_mm": step, "layers": layers,
                     "margin_mm": round(clr + tw / 2.0, 3)},
        },
        unit="mm, count, ratio (dimensionless)",
        model="geometric_escape_lane + rasterised_congestion",
        basis="each pad is offered 8 straight lanes of `track_mm` width leaving the "
              "pad over `escape_mm`; a lane is illegal when its edge-to-edge distance "
              "to any OTHER net's copper on that layer is below the clearance, when "
              "it crosses a keepout, or when any of its 5 probe points is outside the "
              "board outline. Congestion is rasterised copper coverage of the layer's "
              "usable area",
        precision="clearance is exact for pads/tracks/vias (segment and polygon "
                  "distance, track width subtracted); a lane is a straight 8-direction "
                  "test, so a bent or diagonal-between-orthogonals escape is reported "
                  "as blocked when it is not. Congestion is +/-1 grid cell (%.2f mm). "
                  "A pad counts as already connected when a track/via of its own net "
                  "comes within half the pad plus its width" % step,
        assumptions={"clearance_mm": clr, "track_mm": tw, "escape_mm": escape,
                     "clearance_from": rule_clr_source,
                     "track_width_from": rule_tw_source,
                     "layer": layer or "any copper layer",
                     "short_escape_mm": short_escape,
                     "not_claimed": "this is not a route: it says a pad CAN leave, "
                                    "not that the whole net can be connected"},
    )

def _cut_t_range(a, d, box):
    """The parameter interval where `a + t*d` is inside an axis-aligned box.

    Standard slab clipping. Returns None when the segment misses the box entirely,
    which is what keeps a 400-item layer from being sampled at a place it cannot
    possibly be.
    """
    t0, t1 = 0.0, 1.0
    for k in (0, 1):
        lo, hi = box[k], box[k + 2]
        if abs(d[k]) < 1e-12:
            if not (lo - 1e-12 <= a[k] <= hi + 1e-12):
                return None
            continue
        ta = (lo - a[k]) / d[k]
        tb = (hi - a[k]) / d[k]
        if ta > tb:
            ta, tb = tb, ta
        t0 = max(t0, ta)
        t1 = min(t1, tb)
    if t1 < t0 - 1e-12:
        return None
    return max(0.0, t0), min(1.0, t1)


def _cut_polygon_spans(poly, a, d, length, clr):
    """Where a line is covered by a polygon, widened by `clr`, WITHOUT sampling.

    A filled pour is one polygon with tens of thousands of vertices - 21 451 on the
    sky_star ground plane - and sampling it at 0.05 mm cost 8 s for a single line.
    Crossings are solved directly instead, so this is O(vertices) with no grid.

    Two details make it safe rather than merely fast:

    * the line is nudged 0.004 mm sideways first, so it does not run exactly
      THROUGH a vertex. A line through a vertex is a case where "did this edge
      cross?" has no right answer, and the answer moves by less than a sampling
      step anyway;
    * `clr` is applied by widening each interval end by clr/|sin(angle between the
      line and the edge)| - exact for an edge across the line, and LONGER than the
      true reach for an oblique one, so the pour can only be over-stated. A lane is
      never reported that the copper closes.
    """
    n = len(poly)
    if n < 3:
        return []
    ln = math.hypot(d[0], d[1])
    ux, uy = d[0] / ln, d[1] / ln
    # perpendicular nudge, deliberately not a round number so it cannot land on grid
    a2 = (a[0] - uy * 0.0037, a[1] + ux * 0.0037)

    def side(p):
        return ux * (p[1] - a2[1]) - uy * (p[0] - a2[0])

    cuts = []
    for i in range(n):
        p = poly[i]
        q = poly[(i + 1) % n]
        sp, sq = side(p), side(q)
        if (sp > 0) == (sq > 0):
            continue                     # no sign change: no crossing, vertex or not
        ex, ey = q[0] - p[0], q[1] - p[1]
        den = d[0] * ey - d[1] * ex
        if abs(den) < 1e-15:
            continue
        wx, wy = p[0] - a[0], p[1] - a[1]
        t = (wx * ey - wy * ex) / den
        sn = abs(den) / (ln * math.hypot(ex, ey))
        reach = clr / sn if sn > 1e-6 else 0.0
        cuts.append((t, reach))
    if len(cuts) < 2:
        return []
    cuts.sort()
    out = []
    for k in range(0, len(cuts) - 1, 2):
        t0, r0 = cuts[k]
        t1, r1 = cuts[k + 1]
        s0 = (t0 * length) - r0
        s1 = (t1 * length) + r1
        if s1 > s0:
            out.append((max(0.0, s0), min(length, s1)))
    return [(x, y) for x, y in out if y > x]


def _cut_obstacle_span(a, d, length, item, clr, step):
    """Where along the cut line this item stands in the way, in mm from the start.

    Sampled then bisected rather than solved: the items here are pads (possibly
    rotated), tracks (capsules) and vias (circles), and a closed form for each
    would be three separate pieces of geometry to get wrong. Sampling at `step`
    and bisecting each boundary to 0.002 mm gives the same number for every shape
    and one code path.
    """
    bx = _item_bbox(item)
    box = (bx[0] - clr, bx[1] - clr, bx[2] + clr, bx[3] + clr)
    got = _cut_t_range(a, d, box)
    if got is None:
        return None
    t0, t1 = got
    point = {"seg": None}

    def hits(t):
        p = (a[0] + d[0] * t, a[1] + d[1] * t)
        point["seg"] = (p, p, 0.0)
        return _dist(point, item) <= clr - 1e-9

    n = max(2, int(math.ceil((t1 - t0) * length / step)) + 1)
    lo = hi = None
    prev = None
    for i in range(n):
        t = t0 + (t1 - t0) * i / (n - 1.0)
        if hits(t):
            if lo is None:
                lo = t if prev is None else _cut_edge(hits, t, prev, 0.001, length)
            hi = t
        elif lo is not None:
            hi = _cut_edge(hits, prev, t, 0.001, length)
            break
        prev = t
    if lo is None:
        return None
    if hi is None:
        hi = t1
    return (lo * length, hi * length)


def _cut_edge(hits, s_hit, s_miss, res_mm, length):
    """The boundary between a point that hits and a point that misses, to `res_mm`.

    Both ends are given explicitly and in any order, because the two callers want
    opposite things: entering a blocked span the hit is the LATER sample, leaving
    one it is the EARLIER. An earlier version encoded that as a comparison of the
    two parameters and got one of the two cases backwards - the blocked span came
    back 0.10 mm short on one side, which is invisible until a test asks for the
    exact number.

    Returns the MIDPOINT of the final bracket, so the error is at most res_mm/2.
    """
    a, b = float(s_hit), float(s_miss)
    for _ in range(60):
        if abs(b - a) * length <= res_mm:
            break
        m = (a + b) / 2.0
        if hits(m):
            a = m
        else:
            b = m
    return (a + b) / 2.0


def cut_capacity(board, x=None, y=None, angle_deg=90.0, span_mm=None, layer=None,
                 track_mm=None, clearance_mm=None, obstacles=None, max_gaps=40):
    """How many tracks can cross this line, and how many must?

    Every other tool here answers about ONE object (can this pad leave, is this
    pour in pieces). A floorplan fails at a different level: a net with copper on
    both sides of a line has to get across it, and the crossings it needs cannot be
    fewer than the lanes the line offers. That makes a **necessary** condition - no
    router, no layer assignment and no amount of patience gets 12 nets across a pad
    row that leaves 5 lanes - and it is the condition the sky_star board actually
    died on: the header rows, turned into through-holes, cut B.Cu into a few gaps
    while 12 nets still had pads on both sides.

    What stands in the way is the caller's decision, stated in the answer:

    * pads, vias and tracks are counted by default - they are drawn copper, and a
      lane a track already occupies is not free;
    * POURS are not counted by default, because the filler re-cuts them around
      whatever crosses them. Counting the pour would report every line on a poured
      layer as fully blocked - the same false alarm `board_pad_obstacle_map` avoids
      by not treating footprint bodies as obstacles. `sensitivity` still reports the
      number WITH the pours, so the cost is visible instead of hidden.

    Obstacles are harvested ONCE per item and then unioned per subset, so the three
    sensitivity readings do not cost three scans.
    """
    if not board.outline:
        return err("this board has no outline", "NO_GEOMETRY",
                   hint="a cut line has to cross a board; without Edge.Cuts there is "
                        "no interior to measure and '0 lanes' would be an invention")
    rules = board.rules or {}
    classes = dict((k, v) for k, v in (rules.get("net_classes") or {}).items()
                   if isinstance(v, dict))
    default_cls = classes.get("Default") or {}
    rule_clr = default_cls.get("clearance")
    if rule_clr is None:
        clrs = [float(v.get("clearance")) for v in classes.values()
                if isinstance(v.get("clearance"), (int, float))]
        rule_clr = min(clrs) if clrs else None
    rule_tw = default_cls.get("track_width") or default_cls.get("trace_width")
    if rule_tw is None:
        tws = [float(v.get("track_width") or v.get("trace_width"))
               for v in classes.values()
               if isinstance(v.get("track_width") or v.get("trace_width"),
                             (int, float))]
        rule_tw = min(tws) if tws else None
    clr = float(clearance_mm) if clearance_mm is not None else float(rule_clr or 0.2)
    tw = float(track_mm) if track_mm is not None else float(rule_tw or 0.25)
    if clr < 0 or tw <= 0:
        return err("clearance_mm must be >= 0 and track_mm > 0", "ARG_RANGE")

    xs, ys = [], []
    for poly in (board.outline or []):
        for p in poly:
            xs.append(p[0])
            ys.append(p[1])
    if not xs:
        return err("the outline carries no points", "NO_GEOMETRY")
    bx0, bx1, by0, by1 = min(xs), max(xs), min(ys), max(ys)
    if x is None or y is None:
        x = (bx0 + bx1) / 2.0
        y = (by0 + by1) / 2.0
    x, y = float(x), float(y)
    ang = math.radians(float(angle_deg))
    dx, dy = math.cos(ang), math.sin(ang)
    half_span = (float(span_mm) / 2.0 if span_mm else
                 math.hypot(bx1 - bx0, by1 - by0) * 0.75)
    a = (x - dx * half_span, y - dy * half_span)
    d = (dx * 2.0 * half_span, dy * 2.0 * half_span)
    length = math.hypot(d[0], d[1])
    if length <= 0:
        return err("the cut line has zero length", "ARG_RANGE")

    # Clip to the board interior so 'free lanes' can never include the space off
    # the edge of the board, which is not a lane at all.
    rings = [p for p in (board.outline or []) if len(p) >= 3]

    def inside(t):
        p = (a[0] + d[0] * t, a[1] + d[1] * t)
        return any(G.point_in_polygon(p, r) for r in rings)

    m = 401
    first = last = None
    prev = None
    for i in range(m):
        t = i / (m - 1.0)
        if inside(t):
            if first is None:
                first = 0.0 if prev is None else _cut_edge(inside, t, prev, 0.001,
                                                           length)
            last = t
        elif first is not None:
            last = _cut_edge(inside, prev, t, 0.001, length)
            break
        prev = t
    if first is None:
        return err("the cut line does not cross the board outline", "OUTSIDE_BOARD",
                   hint="give x/y inside the board, or a shorter span_mm centred on it")
    s_in, s_out = first * length, last * length
    usable = s_out - s_in
    if usable <= 1e-9:
        return err("the cut line only touches the outline", "OUTSIDE_BOARD")

    wanted = obstacles or ["pads", "vias", "tracks"]
    kinds = set()
    for k in wanted:
        k = str(k).strip().lower()
        if k in ("pad", "pads"):
            kinds.add("pad")
        elif k in ("via", "vias"):
            kinds.add("via")
        elif k in ("track", "tracks"):
            kinds.add("track")
        elif k in ("zone", "zones", "pour", "pours"):
            kinds.add("zone")
        else:
            return err("unknown obstacle kind %r" % (k,), "ARG_INVALID",
                       hint="use pads / vias / tracks / zones")

    layers = [layer] if layer else list(_copper_layers(board))
    for name in layers:
        if name not in _copper_layers(board):
            return err("no such copper layer: %r (board has %s)"
                       % (name, ", ".join(_copper_layers(board))), "ARG_INVALID")

    step = max(0.01, min(0.05, tw / 4.0))
    spans = {}          # layer -> [(kind, net, ref, s0, s1)]
    crossings = {}      # net -> crossings already drawn on any scanned layer
    for name in layers:
        got = []
        items = [it for it in _items(board, layer=name)
                 if it.get("kind") in ("pad", "via", "track")]
        # Which of them can even reach the line? Without this prefilter every item
        # whose bbox the line merely grazes is sampled across its full extent - on a
        # 296-segment board that was 7.4 s for an answer about one line. The bucket
        # grid is built once per layer and walked at a quarter cell, collecting the
        # 2x2 block of cells around each sample so a line clipping a cell corner
        # cannot be missed.
        cand = set()
        if items:
            cell = max(1.0, clr + tw + 1.0)
            buckets = _bucketize(items, cell, pad=clr)
            n_walk = max(1, int(math.ceil(length / (cell / 4.0))))
            for i in range(n_walk + 1):
                t = i / float(n_walk)
                gx = int(math.floor((a[0] + d[0] * t) / cell))
                gy = int(math.floor((a[1] + d[1] * t) / cell))
                for ox in (0, 1):
                    for oy in (0, 1):
                        hit = buckets.get((gx + ox, gy + oy))
                        if hit:
                            cand.update(hit)
        for idx in sorted(cand):
            it = items[idx]
            span = _cut_obstacle_span(a, d, length, it, clr, step)
            if span is None:
                continue
            got.append((it.get("kind"), it.get("net"), it.get("ref"),
                        span[0], span[1]))
            if it.get("kind") == "track" and it.get("net"):
                # A track that merely runs CLOSE to the line is not a crossing - it
                # only occupies a lane. Counting proximity as a crossing would one
                # day retire a net from the demand that never got across.
                p0, p1 = it["seg"][0], it["seg"][1]
                if G.segments_intersect(p0, p1, a, (a[0] + d[0], a[1] + d[1])):
                    crossings[it["net"]] = crossings.get(it["net"], 0) + 1
        # Pours are harvested whatever the caller asked for, so `sensitivity` can
        # say what they would cost - but they only block the primary answer when
        # the caller put them in `obstacles`.
        for zi, zone in enumerate(board.zones or []):
            if name not in [str(l) for l in (zone.get("layers") or [])]:
                continue
            for pi, poly in enumerate(zone.get("filled_polygons") or []):
                for sp in _cut_polygon_spans(poly, a, d, length, clr):
                    got.append(("zone", zone.get("net"),
                                "zone%d.%d" % (zi, pi), sp[0], sp[1]))
        spans[name] = got

    def _free(cur, subset):
        blocks = sorted((s0, s1) for k, _n, _r, s0, s1 in cur if k in subset)
        merged = []
        for s0, s1 in blocks:
            if merged and s0 <= merged[-1][1] + 1e-9:
                merged[-1] = (merged[-1][0], max(merged[-1][1], s1))
            else:
                merged.append((s0, s1))
        out = []
        cur_s = s_in
        for s0, s1 in merged:
            s0c, s1c = max(s0, s_in), min(s1, s_out)
            if s1c <= s0c:
                continue
            if s0c - cur_s > 1e-6:
                out.append((cur_s, s0c))
            cur_s = max(cur_s, s1c)
        if s_out - cur_s > 1e-6:
            out.append((cur_s, s_out))
        return merged, out

    def _fits(width):
        # n tracks of `tw` with `clr` between them need n*tw + (n-1)*clr <= width
        if width < tw - 1e-9:
            return 0
        return int(math.floor((width + clr) / (tw + clr) + 1e-9))

    per_layer, total = {}, 0
    for name in layers:
        merged, free = _free(spans[name], kinds)
        gaps = []
        for s0, s1 in free:
            w = s1 - s0
            mid = (s0 + s1) / 2.0
            gaps.append({
                "width_mm": round(w, 3),
                "tracks_fit": _fits(w),
                "from": [round(a[0] + d[0] * (s0 / length), 3),
                         round(a[1] + d[1] * (s0 / length), 3)],
                "to": [round(a[0] + d[0] * (s1 / length), 3),
                       round(a[1] + d[1] * (s1 / length), 3)],
                "center": [round(a[0] + d[0] * (mid / length), 3),
                           round(a[1] + d[1] * (mid / length), 3)],
            })
        gaps.sort(key=lambda g: -g["width_mm"])
        cap = sum(g["tracks_fit"] for g in gaps)
        total += cap
        blockers = [{"kind": k, "net": n, "ref": r,
                     "covers_mm": round(s1 - s0, 3)}
                    for k, n, r, s0, s1 in spans[name]]
        blockers.sort(key=lambda b: -b["covers_mm"])
        per_layer[name] = {
            "gaps": gaps[:int(max_gaps)],
            "gap_count": len(gaps),
            "wide_enough": len([g for g in gaps if g["tracks_fit"] > 0]),
            "capacity": cap,
            "usable_mm": round(usable, 3),
            "blocked_mm": round(usable - sum(g["width_mm"] for g in gaps), 3),
            "blockers": blockers[:int(max_gaps)],
        }

    # Who HAS to cross: a net with pads on both sides of the line. A pad whose
    # centre sits on the line decides nothing, so it is excluded from the side
    # test and reported separately.
    side = {}
    on_line = set()
    for fp in board.footprints:
        for pad in fp.get("pads") or []:
            net = pad.get("net")
            if not net:
                continue
            px, py = _pad_xy(fp, pad)
            if px is None:
                continue
            off = (px - x) * dy - (py - y) * dx       # signed distance from the line
            if abs(off) <= 0.05:
                on_line.add(net)
                continue
            side.setdefault(net, set()).add(1 if off > 0 else -1)
    both = sorted(n for n, s in side.items() if len(s) == 2)
    already = [n for n in both if crossings.get(n)]
    must = [n for n in both if not crossings.get(n)]
    demand = len(must)

    # Three answers to "how much would ignoring this buy" - the same harvested
    # spans, unioned per subset, so the caller can see what the tracks and the pour
    # each cost instead of having to guess why a line reads as full.
    sens = {}
    for label, subset in (("pads_vias", {"pad", "via"}),
                          ("pads_vias_tracks", {"pad", "via", "track"}),
                          ("with_pours_too", set(kinds) | {"zone"})):
        sens[label] = sum(_fits(s1 - s0)
                          for name in layers
                          for s0, s1 in _free(spans[name], subset)[1])

    slack = total - demand
    verdict = "capacity_exceeded" if slack < 0 else "within_capacity"
    return ok(
        value={
            "verdict": verdict,
            "capacity": total,
            "must_cross": demand,
            "slack": slack,
            "angle_deg": float(angle_deg),
            "line": {"from": [round(a[0], 3), round(a[1], 3)],
                     "to": [round(a[0] + d[0], 3), round(a[1] + d[1], 3)],
                     "at": [round(x, 3), round(y, 3)],
                     "usable_mm": round(usable, 3)},
            "layers": per_layer,
            "must_cross_nets": must[:int(max_gaps)],
            "already_crossing": already[:int(max_gaps)],
            "pads_on_the_line": sorted(on_line)[:int(max_gaps)],
            "sensitivity": sens,
            "obstacles": sorted(kinds),
        },
        unit="count of tracks, mm",
        model="planar cut-line lane count vs nets with pads on both sides",
        basis="a net whose pads lie on both sides of a line must carry copper across "
              "it; every crossing occupies one lane on one copper layer, so the sum "
              "of the lanes the line offers over all scanned layers is an upper "
              "bound on the nets that can be connected. Lanes are measured as the "
              "free spans of the line inside the board outline, each grown by the "
              "clearance so that n tracks of `track_mm` fit when "
              "n*track + (n-1)*clearance <= span",
        precision="lane edges are sampled at %.3f mm then bisected to 0.002 mm; a "
                  "gap narrower than the sample step can be missed. Track crossings "
                  "already drawn are counted from centreline/line intersection, not "
                  "from copper overlap, so a track that ends ON the line is not a "
                  "crossing" % step,
        assumptions={"clearance_mm": clr, "track_mm": tw,
                     "clearance_from": ("caller" if clearance_mm is not None
                                        else "net class Default" if rule_clr
                                        else "nothing in the file, 0.2 mm assumed"),
                     "track_from": ("caller" if track_mm is not None
                                    else "net class Default" if rule_tw
                                    else "nothing in the file, 0.25 mm assumed"),
                     "obstacles": sorted(kinds),
                     "pours_counted": "zone" in kinds,
                     "not_claimed": "this is a lower bound on what is IN THE WAY, not "
                                    "a route: it does not say which net takes which "
                                    "lane, and a net may cross on a layer not scanned"},
    )


def _pour_mask(polys, qx0, qy0, step, nx, ny):
    """Which grid points lie INSIDE any of these pour polygons.

    Scanline conversion, bucketed per row: every edge is visited once and appended
    to the rows it spans, so the cost is O(edges + rows*cols) rather than
    O(edges*rows). The ground plane on a real board is one polygon of 21 451
    vertices, and asking `point_in_polygon` per candidate put 35 M edge tests in the
    way of an 8 mm search.
    """
    rows = [[] for _ in range(ny)]
    for poly in polys:
        n = len(poly)
        for i in range(n):
            x0, y0 = poly[i]
            x1, y1 = poly[(i + 1) % n]
            if y0 == y1:
                continue
            lo, hi = (y0, y1) if y0 < y1 else (y1, y0)
            i0 = max(0, int(math.ceil((lo - qy0) / step)))
            i1 = min(ny - 1, int(math.floor((hi - qy0) / step)))
            for iy in range(i0, i1 + 1):
                y = qy0 + iy * step
                t = (y - y0) / (y1 - y0)
                rows[iy].append(x0 + t * (x1 - x0))
    mask = []
    for iy in range(ny):
        xs = sorted(rows[iy])
        row = bytearray(nx)
        for k in range(0, len(xs) - 1, 2):
            xa, xb = xs[k], xs[k + 1]
            ia = max(0, int(math.ceil((xa - qx0) / step)))
            ib = min(nx - 1, int(math.floor((xb - qx0) / step)))
            for ix in range(ia, ib + 1):
                row[ix] = 1
        mask.append(row)
    return mask


def via_sites(board, near=None, radius_mm=None, region=None, grid_mm=0.2,
              diameter_mm=None, drill_mm=None, clearance_mm=None,
              edge_clearance_mm=None, hole_to_hole_mm=None, net=None,
              layers=None, max_sites=20, pours_block=False):
    """Where may a via legally go, and how much room has it got?

    Every via tool here could compute a via (current, resistance, thermal fence) or
    PLACE ground vias on a stitching grid; none could answer the question a route
    actually asks - "is there a legal hole anywhere near this point, and which one
    has the most room". The stitching round demonstrated the cost of not having it:
    of sixteen ground islands only four had room for a 0.45 hole, and two of those
    four put the hole into PD13/PD14's track, which the script only found by
    rebuilding the board and reading DRC.

    This is legality, not placement: it returns the sites and what is nearest to
    each, in this order and no other. `net` decides what counts as own copper - a
    via of net X may sit on X's track or pad (that is how it connects) but not
    inside another net's copper. Drill-to-drill is a fab rule and is never waived,
    so an existing hole blocks a candidate whatever its net.

    POURS ARE NOT OBSTACLES by default, for the same reason `board_cut_capacity`
    does not count them: the filler cuts the pour back around whatever is added, so
    a hole inside another net's plane is not a short - it costs a bite out of the
    plane. `inside_pours` reports which planes would be bitten, and
    `pours_block=True` restores the strict reading. The first version made pours
    hard obstacles and reported the +3V3 fan-out as having 2 legal points out of
    441 in a region that is, in fact, full of room - a confidently wrong answer
    caused by a rule the fabricator never applies.
    """
    if not board.outline:
        return err("this board has no outline", "NO_GEOMETRY",
                   hint="edge clearance cannot be measured without Edge.Cuts")
    rules = board.rules or {}
    viar = rules.get("via") or {}
    ann = float(viar.get("min_via_annular_width") or 0.1)
    floor_dia = float(viar.get("min_via_diameter") or 0.0) or None
    floor_hole = float(viar.get("min_through_hole_diameter") or 0.0) or None
    h2h = (float(hole_to_hole_mm) if hole_to_hole_mm is not None
           else float(viar.get("min_hole_to_hole") or 0.25))
    clr = (float(clearance_mm) if clearance_mm is not None
           else float((rules.get("clearance") or {}).get("min_clearance") or 0.2))
    edge = (float(edge_clearance_mm) if edge_clearance_mm is not None
            else float((rules.get("clearance") or {}).get("min_copper_edge_clearance")
                       or clr))

    if drill_mm is not None:
        drill = float(drill_mm)
    elif diameter_mm is not None:
        drill = max(0.1, float(diameter_mm) - 2.0 * ann)
    else:
        drill = 0.3
    if diameter_mm is not None:
        dia = float(diameter_mm)
    else:
        dia = drill + 2.0 * ann
    if floor_dia:
        dia = max(dia, floor_dia)
    if floor_hole:
        drill = max(drill, floor_hole)
    if dia <= drill:
        return err("diameter_mm must exceed drill_mm", "ARG_RANGE")
    step = float(grid_mm or 0.2)
    if step <= 0:
        return err("grid_mm must be > 0", "ARG_RANGE")

    coin = list(layers) if layers else list(_copper_layers(board))
    for name in coin:
        if name not in _copper_layers(board):
            return err("no such copper layer: %r (board has %s)"
                       % (name, ", ".join(_copper_layers(board))), "ARG_INVALID")
    reach = clr + dia / 2.0

    xs, ys = [], []
    for poly in (board.outline or []):
        for p in poly:
            xs.append(p[0])
            ys.append(p[1])
    bx0, bx1, by0, by1 = min(xs), max(xs), min(ys), max(ys)
    if region is not None:
        try:
            rx0, ry0, rx1, ry1 = [float(v) for v in region]
        except (TypeError, ValueError):
            return err("region must be [x0, y0, x1, y1] in mm", "ARG_INVALID")
        qx0, qx1 = max(bx0, min(rx0, rx1)), min(bx1, max(rx0, rx1))
        qy0, qy1 = max(by0, min(ry0, ry1)), min(by1, max(ry0, ry1))
    elif near is not None:
        try:
            cx, cy = float(near[0]), float(near[1])
        except (TypeError, ValueError, IndexError):
            return err("near must be [x, y] in mm", "ARG_INVALID")
        r = float(radius_mm) if radius_mm else 2.0
        qx0, qx1 = max(bx0, cx - r), min(bx1, cx + r)
        qy0, qy1 = max(by0, cy - r), min(by1, cy + r)
    else:
        qx0, qx1, qy0, qy1 = bx0, bx1, by0, by1
    if qx1 < qx0 - 1e-9 or qy1 < qy0 - 1e-9:
        return err("the search window is empty", "ARG_RANGE",
                   hint="the region / near point lies outside the board")

    # Other-net copper per layer, bucketed so one candidate is one lookup.
    cell = max(1.0, reach + step * 2.0)
    layer_buckets = {}
    pour_buckets = {}
    pours = []                     # [(net, polygon)] - one entry per filled piece
    for name in coin:
        items = [it for it in _items(board, layer=name)
                 if it.get("kind") in ("pad", "via", "track")
                 and not (net is not None and it.get("net") == net)]
        edges = []
        # A pour is carried as its EDGES when it counts as an obstacle, not as one
        # polygon: the bucket then only touches the edges nearby instead of all
        # 21 451 of them, and "inside the pour" comes from the scanline mask below.
        for zi, zone in enumerate(board.zones or []):
            if name not in [str(l) for l in (zone.get("layers") or [])]:
                continue
            if net is not None and zone.get("net") == net:
                continue
            for pi, poly in enumerate(zone.get("filled_polygons") or []):
                if len(poly) < 3:
                    continue
                pours.append((zone.get("net"), poly, "zone%d.%d" % (zi, pi)))
                n = len(poly)
                for i in range(n):
                    p0, p1 = poly[i], poly[(i + 1) % n]
                    if p0 == p1:
                        continue
                    edges.append({"kind": "zone", "net": zone.get("net"),
                                  "ref": "zone%d.%d" % (zi, pi), "layer": name,
                                  "seg": ((p0[0], p0[1]), (p1[0], p1[1]), 0.0)})
        layer_buckets[name] = (items, _bucketize(items, cell, pad=reach))
        pour_buckets[name] = (edges, _bucketize(edges, cell, pad=reach) if edges
                              else {})

    # Drills are a fab rule, not an electrical one: another net's hole blocks the
    # candidate exactly as hard as one of our own.
    holes = []
    for via in board.vias:
        holes.append((float(via["x"]), float(via["y"]),
                      float(via.get("drill") or via.get("diameter") or 0.3)))
    for fp in board.footprints:
        for pad in fp.get("pads") or []:
            hole = pad.get("hole_mm")
            if not hole:
                continue
            px, py = _pad_xy(fp, pad)
            if px is not None:
                holes.append((px, py, float(hole)))
    # A hole is a POINT plus a radius applied later, not a circle: measuring the
    # distance to a circle of radius drill/2 and then subtracting drill/2 again
    # counted the radius twice and refused a site 0.10 mm clear of the drill rule.
    hole_items = [{"kind": "hole", "net": None, "drill_mm": hd,
                   "seg": ((hx, hy), (hx, hy), 0.0),
                   "ref": "hole@(%.2f,%.2f)" % (hx, hy)}
                  for hx, hy, hd in holes]
    hole_buckets = _bucketize(hole_items, cell, pad=reach) if hole_items else {}

    rings = [p for p in (board.outline or []) if len(p) >= 3]

    def _lookup(items, buckets, px, py, rings=3):
        """The nearest item within `rings` cells (`_nearest_from`, same cell)."""
        return _nearest_from(items, buckets, cell, px, py, rings=rings)

    def _nearest(name, px, py):
        items, buckets = layer_buckets[name]
        return _lookup(items, buckets, px, py)

    def _nearest_hole(px, py):
        if not hole_buckets:
            return None
        return _lookup(hole_items, hole_buckets, px, py)

    nx = int(math.floor((qx1 - qx0) / step)) + 1
    ny = int(math.floor((qy1 - qy0) / step)) + 1
    # A whole 44 x 72 mm board at 0.2 mm is 79 200 candidates and took minutes; a
    # search that wide is the caller asking a question that has too many answers to
    # be an answer. Refuse and say what would fit, rather than time out.
    if nx * ny > 20000:
        fit = math.sqrt((qx1 - qx0) * (qy1 - qy0) / 20000.0)
        return err("%d candidates at %.3f mm is too many to scan" % (nx * ny, step),
                   "ARG_RANGE",
                   hint="give x/y with radius_mm, or a smaller region; a grid_mm of "
                        "about %.2f mm would fit this window" % max(step, fit))
    pour_masks = []
    for pnet, poly, pref in pours:
        pour_masks.append((pnet, pref,
                           _pour_mask([poly], qx0, qy0, step, nx, ny)))
    tried = legal = 0
    kill = {"off_board": 0, "edge_clearance": 0, "copper_clearance": 0,
            "pour_interior": 0, "hole_to_hole": 0}
    sites = []
    for iy in range(ny):
        py = qy0 + iy * step
        for ix in range(nx):
            px = qx0 + ix * step
            tried += 1
            if not any(G.point_in_polygon((px, py), r) for r in rings):
                kill["off_board"] += 1
                continue
            inside = [pn for pn, _pr, m in pour_masks if m[iy][ix]]
            if inside and pours_block:
                # Only when the caller says the plane is fixed copper. By default
                # the filler bites around the hole and this is not a short.
                kill["pour_interior"] += 1
                continue
            room = None
            src = "edge_clearance"
            why = None
            for r in rings:
                d = G.dist_point_polygon((px, py), r)
                slack = d - (edge + dia / 2.0)
                if room is None or slack < room:
                    room, src, why = slack, "edge_clearance", ("board edge", r)
            for name in coin:
                got = _nearest(name, px, py)
                if got is not None:
                    slack = got[0] - reach
                    if slack < room:
                        room, src = slack, "copper_clearance"
                        why = ("%s on %s" % (got[1].get("kind"), name), got[1])
                if not pours_block:
                    continue
                pedges, pbuckets = pour_buckets[name]
                got = _lookup(pedges, pbuckets, px, py)
                if got is not None:
                    slack = got[0] - reach
                    if slack < room:
                        room, src = slack, "copper_clearance"
                        why = ("%s on %s" % ("pour edge", name), got[1])
            if room < 0:
                kill[src] += 1
                continue
            got = _nearest_hole(px, py)
            if got is not None:
                slack = got[0] - drill / 2.0 - got[1].get("drill_mm", 0.0) / 2.0 - h2h
                if slack < room:
                    room, src = slack, "hole_to_hole"
                    why = ("existing hole", got[1])
                if room < 0:
                    kill["hole_to_hole"] += 1
                    continue
            legal += 1
            nearest = None
            if why and isinstance(why[1], dict):
                it = why[1]
                nearest = {"kind": it.get("kind"), "net": it.get("net"),
                           "ref": it.get("ref")}
            sites.append({"at": [round(px, 3), round(py, 3)],
                          "room_mm": round(room, 3),
                          "limited_by": why[0] if why else None,
                          "nearest": nearest,
                          "inside_pours": sorted(set(inside))})
    sites.sort(key=lambda s: -s["room_mm"])
    top = sites[:int(max_sites)]
    best = top[0]["room_mm"] if top else None
    return ok(
        value={
            "verdict": ("sites_exist" if legal else "no_legal_site"),
            "legal_sites": legal,
            "candidates_tried": tried,
            "best_room_mm": best,
            "sites": top,
            "killed_by": kill,
            "window": {"x0": round(qx0, 3), "y0": round(qy0, 3),
                       "x1": round(qx1, 3), "y1": round(qy1, 3),
                       "grid_mm": step},
            "via": {"diameter_mm": round(dia, 4), "drill_mm": round(drill, 4)},
            "layers": coin,
        },
        unit="mm, count",
        model="point-in-board + clearance distance field + drill keepout",
        basis="a via centre is legal when it is inside the outline by the edge "
              "clearance plus its radius, at least `clearance` plus its radius from "
              "every pad/track/via/pour of another net on every copper layer it "
              "passes through, and its drill is at least min_hole_to_hole from every "
              "existing drill. room_mm is the smallest of those margins, so a site "
              "with room 0.05 has 0.05 mm to spare on its tightest side",
        precision="distance is exact point-to-segment / point-to-polygon against the "
                  "same geometry DRC uses; the search is a %.3f mm grid, so a legal "
                  "spot between two grid points is not reported" % step,
        assumptions={"clearance_mm": clr, "edge_clearance_mm": edge,
                     "hole_to_hole_mm": h2h, "net": net or "(any - all copper blocks)",
                     "pours_block": bool(pours_block),
                     "own_copper_ignored": net is not None,
                     "not_claimed": "this is legality, not placement: it does not say "
                                    "the via is useful, reachable, or that the route "
                                    "needs one here"},
    )


def _cluster_points(points, join_mm):
    """Groups of points that hang together within `join_mm` (single linkage).

    Single linkage is the right rule here and not a shortcut: a power net's pads are
    a ROW whose two ends can be 15 mm apart while every pad is 0.5 mm from its
    neighbour, and the row is one place on the board. Grouping by distance to a
    centre instead would split it into as many clusters as it has pads, and the plan
    would then describe twenty corridors of length zero.
    """
    n = len(points)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    lim2 = float(join_mm) ** 2
    for i in range(n):
        xi, yi = points[i]
        for j in range(i + 1, n):
            dx, dy = xi - points[j][0], yi - points[j][1]
            if dx * dx + dy * dy > lim2:
                continue
            ri, rj = find(i), find(j)
            if ri != rj:
                parent[ri] = rj
    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())

def _prim_tree(points):
    """Prim's minimum spanning tree, returning the EDGES, not just the total.

    `_prim_mst` above answers "how short could this net be"; a plan needs to know
    WHICH pairs to look at the floor between, which is exactly the tree's edges.
    """
    n = len(points)
    if n < 2:
        return []
    inf = float("inf")
    best = [inf] * n
    prev = [-1] * n
    used = [False] * n
    best[0] = 0.0
    out = []
    for _ in range(n):
        k = -1
        for i in range(n):
            if not used[i] and (k < 0 or best[i] < best[k]):
                k = i
        used[k] = True
        if prev[k] >= 0:
            out.append((prev[k], k, best[k]))
        px, py = points[k]
        for i in range(n):
            if used[i]:
                continue
            d = math.hypot(points[i][0] - px, points[i][1] - py)
            if d < best[i]:
                best[i] = d
                prev[i] = k
    return out

def _widest_through(widths, nx, ny, starts, ends):
    """The largest width at which some route of cells joins `starts` to `ends`.

    This is the MAXIMUM-BOTTLENECK path, and it is what makes the answer useful: a
    straight line between two pad clusters usually crosses a row of other pads and
    reads as "nothing fits", while a track can step around them and be 0.35 wide the
    whole way. Sorting the cells from widest to narrowest and adding them one at a
    time, the moment a start and an end land in the same component, the width being
    added IS the answer - no bisection, one pass.

    Each component carries `has_start` / `has_end` flags so the join is detected on
    the union that creates it, not by re-walking the grid every step.

    Returns (width, cell_index) or (None, None) when even width 0 does not connect.
    """
    total = nx * ny
    parent = [-1] * total            # -1 = cell not added yet
    has_s = bytearray(total)
    has_e = bytearray(total)
    order = sorted(range(total), key=lambda c: -widths[c])
    for c in order:
        w = widths[c]
        if w < 0:
            break                    # everything from here on is inside copper
        parent[c] = c
        has_s[c] = 1 if c in starts else 0
        has_e[c] = 1 if c in ends else 0
        ix, iy = divmod(c, ny)
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            jx, jy = ix + dx, iy + dy
            if jx < 0 or jy < 0 or jx >= nx or jy >= ny:
                continue
            n = jx * ny + jy
            if parent[n] < 0:
                continue
            a, b = c, n
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            while parent[b] != b:
                parent[b] = parent[parent[b]]
                b = parent[b]
            if a == b:
                continue
            parent[a] = b
            has_s[b] = 1 if (has_s[a] or has_s[b]) else 0
            has_e[b] = 1 if (has_e[a] or has_e[b]) else 0
            if has_s[b] and has_e[b]:
                # The width that closed the circuit is the bottleneck; report the
                # cell that carries it so the caller knows where the throat is.
                return w, c
    return None, None

def trunk_plan(board, net, track_mm=None, clearance_mm=None, edge_clearance_mm=None,
               layer=None, cluster_mm=5.0, band_mm=4.0, grid_mm=0.25,
               max_edges=12, pours_block=False, max_cells=200000):
    """How wide a trunk of this net can join its pad clusters, and where does it
    get squeezed?

    The unfinished sky_star board has 38 unconnected +3V3 pads and every tool here
    could say so; none could say whether a 0.4 mm trunk between the pad groups is
    possible at all, or which millimetre of floor refuses it. That question decides
    whether the net gets finished or the floorplan moves, and it is a measurement,
    not a route:

    * the net's pads are grouped (single linkage at `cluster_mm`), and the cluster
      centres are joined by a minimum spanning tree - n clusters, n-1 segments;
    * around each segment a BAND of `band_mm` either side is rasterised at `grid_mm`,
      and at every cell the widest track whose centre sits there is computed: half of
      it must clear the nearest other-net copper by `clearance_mm`, and the board
      edge by the edge clearance;
    * the widest trunk from cluster i to cluster j is the maximum-bottleneck path
      through those cells, so stepping around a pad row is allowed while teleporting
      across one is not.

    The answer is therefore a width that CAN be routed, with the cell that limits it
    - not a claim that anyone has routed it. Returned per copper layer, because the
    question "which layer" is answered by the same raster.

    POURS are not obstacles, for the same reason `board_cut_capacity` and
    `via_sites` ignore them: the filler cuts a pour back around whatever is added, so
    another net's plane is not a wall. `pours_block=True` restores the strict
    reading.
    """
    if not net:
        # Without a name this would match every pad with no net at all - the
        # unconnected ones - and answer a question nobody asked.
        return err("which net? `net` is required and must not be empty",
                   "ARG_INVALID",
                   hint="pads with no net assignment are not a trunk; name the net "
                        "you want to join, e.g. +3V3")
    rules = board.rules or {}
    classes = dict((k, v) for k, v in (rules.get("net_classes") or {}).items()
                   if isinstance(v, dict))
    default_cls = classes.get("Default") or {}
    rule_clr = default_cls.get("clearance")
    if rule_clr is None:
        clrs = [float(v.get("clearance")) for v in classes.values()
                if isinstance(v.get("clearance"), (int, float))]
        rule_clr = min(clrs) if clrs else None
    rule_tw = default_cls.get("track_width") or default_cls.get("trace_width")
    if rule_tw is None:
        tws = [float(v.get("track_width") or v.get("trace_width"))
               for v in classes.values()
               if isinstance(v.get("track_width") or v.get("trace_width"),
                             (int, float))]
        rule_tw = min(tws) if tws else None
    clr = float(clearance_mm) if clearance_mm is not None else float(rule_clr or 0.2)
    tw = float(track_mm) if track_mm is not None else float(rule_tw or 0.25)
    edge = (float(edge_clearance_mm) if edge_clearance_mm is not None
            else float((rules.get("clearance") or {}).get("min_copper_edge_clearance")
                       or clr))
    if clr < 0 or tw <= 0:
        return err("clearance_mm must be >= 0 and track_mm > 0", "ARG_RANGE")
    step = float(grid_mm or 0.25)
    if step <= 0:
        return err("grid_mm must be > 0", "ARG_RANGE")
    band = float(band_mm or 0.0)
    if band <= 0:
        return err("band_mm must be > 0", "ARG_RANGE")
    join = float(cluster_mm if cluster_mm else 5.0)
    if join <= 0:
        return err("cluster_mm must be > 0", "ARG_RANGE")

    copper = _copper_layers(board)
    coin = [layer] if layer else list(copper)
    for name in coin:
        if name not in copper:
            return err("no such copper layer: %r (board has %s)"
                       % (name, ", ".join(copper)), "ARG_INVALID")
    if not board.outline:
        return err("this board has no outline", "NO_GEOMETRY",
                   hint="without Edge.Cuts there is no floor between the clusters "
                        "to measure, and every width would be an invention")

    pts, wires = [], []
    for fp in board.footprints:
        for pad in fp.get("pads") or []:
            if pad.get("net") != net:
                continue
            px, py = _pad_xy(fp, pad)
            if px is None:
                continue
            pts.append((px, py))
            wires.append("%s.%s" % (fp.get("ref") or "?", pad.get("number") or "?"))
    if not pts:
        return err("no pad on this board belongs to net %r" % (net,), "NO_GEOMETRY",
                   hint="this net has no pads, so there is nothing to join")
    if len(pts) == 1:
        return err("net %r has a single pad" % (net,), "NO_GEOMETRY",
                   hint="one pad needs no trunk; there is no second point to reach")

    groups = _cluster_points(pts, join)
    groups.sort(key=lambda g: -len(g))
    clusters = []
    for gi, g in enumerate(groups):
        xs = [pts[i][0] for i in g]
        ys = [pts[i][1] for i in g]
        clusters.append({"id": gi, "pads": len(g),
                         "center": [sum(xs) / len(xs), sum(ys) / len(ys)],
                         "bbox": [min(xs), min(ys), max(xs), max(ys)],
                         "pads_at": [[round(pts[i][0], 3), round(pts[i][1], 3)]
                                     for i in g[:6]]})
    centers = [c["center"] for c in clusters]
    tree = _prim_tree(centers)
    if not tree:
        return err("all pads of %r are in one cluster" % (net,), "NO_GEOMETRY",
                   hint="raise cluster_mm to group them, or the net needs no trunk")

    # Other-net copper per layer (own copper is never in the way), plus the pour
    # edges only when the caller insists the planes are fixed copper.
    # Bucket cell, and with it the REACH of the nearest-copper search: three rings
    # of a 2 mm cell is 6.3 mm of floor, i.e. a 12 mm aperture. Shrinking the cell to
    # 1 mm looked 15 % faster and quietly LOWERED a reported width on the real board
    # (3.53 -> 4.15), because beyond the last ring "no copper found" is an UPPER
    # BOUND and not a measurement. So the cell stays wide and the bound is reported
    # as a bound (`limited_by.kind = beyond_reach`).
    cell = max(1.0, 2.0 + clr)
    reach_mm = 3.0 * cell
    max_w = 2.0 * (reach_mm - clr)
    layer_items, layer_buckets, layer_bboxes = {}, {}, {}
    own_items, own_buckets = {}, {}
    pour_items, pour_buckets, pour_bboxes = {}, {}, {}
    for name in coin:
        its, own = [], []
        for it in _items(board, layer=name):
            if it.get("kind") not in ("pad", "via", "track"):
                continue
            (own if it.get("net") == net else its).append(it)
        layer_items[name] = its
        layer_bboxes[name] = _bbox_list(its)
        layer_buckets[name] = _bucketize(its, cell, pad=clr) if its else {}
        own_items[name] = own
        own_buckets[name] = _bucketize(own, cell) if own else {}
        segs = []
        if pours_block:
            for zi, zone in enumerate(board.zones or []):
                if name not in [str(l) for l in (zone.get("layers") or [])]:
                    continue
                if zone.get("net") == net:
                    continue
                for pi, poly in enumerate(zone.get("filled_polygons") or []):
                    n = len(poly)
                    for i in range(n):
                        p0, p1 = poly[i], poly[(i + 1) % n]
                        if p0 == p1:
                            continue
                        segs.append({"kind": "zone", "net": zone.get("net"),
                                     "ref": "zone%d.%d" % (zi, pi), "layer": name,
                                     "seg": ((p0[0], p0[1]), (p1[0], p1[1]), 0.0)})
        pour_items[name] = segs
        pour_bboxes[name] = _bbox_list(segs)
        pour_buckets[name] = _bucketize(segs, cell, pad=clr) if segs else {}
    rings = [p for p in (board.outline or []) if len(p) >= 3]

    def width_on(name, px, py, edge_w):
        """Widest track with its centre here on one layer, and what limits it.

        `edge_w` is the board-edge bound already computed for this point, which is
        the same on every layer - recomputing it per layer doubled the cost of a
        search that walks tens of thousands of cells.
        """
        w = None
        why = None
        got = _nearest_from(layer_items[name], layer_buckets[name], cell, px, py,
                            bboxes=layer_bboxes[name])
        if got is not None:
            d, it = got
            w = 2.0 * (d - clr)
            why = {"kind": it.get("kind"), "net": it.get("net"),
                   "ref": it.get("ref"), "on": name}
        else:
            # No copper within reach: an UPPER BOUND on the aperture, not a
            # measurement. Said so, so it can never be read as "measured 12 mm of
            # clear floor".
            w = max_w
            why = {"kind": "beyond_reach", "net": None,
                   "ref": "no copper within %.2f mm" % reach_mm, "on": name}
        if pours_block:
            got = _nearest_from(pour_items[name], pour_buckets[name], cell, px, py,
                                bboxes=pour_bboxes[name])
            if got is not None and (w is None or 2.0 * (got[0] - clr) < w):
                w = 2.0 * (got[0] - clr)
                why = {"kind": "pour_edge", "net": got[1].get("net"),
                       "ref": got[1].get("ref"), "on": name}
        if w is None or edge_w < w:
            w = edge_w
            why = {"kind": "board_edge", "net": None, "ref": "Edge.Cuts",
                   "on": name}
        return max(-1.0, w), why

    edges, throttled = [], []
    cells_used = 0
    for (i, j, length) in sorted(tree, key=lambda e: -e[2])[:int(max_edges)]:
        a, b = centers[i], centers[j]
        dx, dy = b[0] - a[0], b[1] - a[1]
        ln = math.hypot(dx, dy)
        ux, uy = dx / ln, dy / ln
        nxv, nyv = -uy, ux                       # perpendicular
        # The band starts where the cluster's own box ends: measuring inside a
        # package measures the fan-out, which is a different question.
        def _exit(c):
            bb = clusters[c]["bbox"]
            got = _cut_t_range(a, (dx, dy),
                               (bb[0] - 1e-6, bb[1] - 1e-6,
                                bb[2] + 1e-6, bb[3] + 1e-6))
            return got[1] if got else 0.0

        def _enter(c):
            bb = clusters[c]["bbox"]
            got = _cut_t_range(a, (dx, dy),
                               (bb[0] - 1e-6, bb[1] - 1e-6,
                                bb[2] + 1e-6, bb[3] + 1e-6))
            return got[0] if got else 1.0

        t0, t1 = _exit(i), _enter(j)
        if t1 <= t0:
            edges.append({"from": i, "to": j,
                          "centers": [[round(a[0], 3), round(a[1], 3)],
                                      [round(b[0], 3), round(b[1], 3)]],
                          "straight_mm": round(length, 3), "open_span_mm": 0.0,
                          "verdict": "clusters_overlap", "layers": {},
                          "best": None, "fits_on": []})
            continue
        s0, s1 = t0 * length, t1 * length
        ns = max(2, int(math.ceil((s1 - s0) / step)) + 1)
        nb = max(2, int(math.ceil(2.0 * band / step)) + 1)
        cells_used += ns * nb * len(coin)
        if cells_used > int(max_cells):
            return err("the corridor band needs %d cells at %.3f mm" % (cells_used, step),
                       "ARG_RANGE",
                       hint="raise grid_mm to about %.2f mm, narrow band_mm, raise "
                            "cluster_mm so there are fewer cluster pairs, or raise "
                            "max_cells if you meant that raster"
                            % (step * math.sqrt(cells_used / float(max_cells))
                               * 1.05))
        per_layer = {}
        fits_on = []
        widths = dict((name, []) for name in coin)
        limits = dict((name, []) for name in coin)
        for si in range(ns):
            s = s0 + si * step
            for bi in range(nb):
                off = -band + bi * step
                px = a[0] + ux * s + nxv * off
                py = a[1] + uy * s + nyv * off
                inside = any(G.point_in_polygon((px, py), r) for r in rings)
                edge_w = min(2.0 * (G.dist_point_polygon((px, py), r) - edge)
                             for r in rings)
                for name in coin:
                    if not inside:
                        widths[name].append(-1.0)
                        limits[name].append(None)
                        continue
                    w, why = width_on(name, px, py, edge_w)
                    widths[name].append(round(w, 3))
                    limits[name].append(why)
        for name in coin:
            # Start column = the cells nearest the first cluster, end column = the
            # last: a path only counts when it spans the whole corridor.
            starts = set(range(0, nb))
            ends = set((ns - 1) * nb + bi for bi in range(nb))
            wbest, cbest = _widest_through(widths[name], ns, nb, starts, ends)
            if wbest is None:
                per_layer[name] = {"widest_mm": None, "verdict": "no_path",
                                   "at": None, "limited_by": None}
                continue
            si, bi = divmod(cbest, nb)
            per_layer[name] = {
                "widest_mm": round(wbest, 3),
                "verdict": "fits" if wbest >= tw - 1e-9 else "throttled",
                "at": [round(a[0] + ux * (s0 + si * step) + nxv * (-band + bi * step), 3),
                       round(a[1] + uy * (s0 + si * step) + nyv * (-band + bi * step), 3)],
                "limited_by": limits[name][cbest]}
            if wbest >= tw - 1e-9:
                fits_on.append(name)
        best = None
        for name, r in per_layer.items():
            if r["widest_mm"] is not None and (best is None
                                               or r["widest_mm"] > best["widest_mm"]):
                best = {"layer": name, "widest_mm": r["widest_mm"],
                        "at": r["at"], "limited_by": r["limited_by"]}
        entry = {"from": i, "to": j,
                 "centers": [[round(a[0], 3), round(a[1], 3)],
                             [round(b[0], 3), round(b[1], 3)]],
                 "straight_mm": round(length, 3),
                 "open_span_mm": round(s1 - s0, 3),
                 "verdict": "fits" if fits_on else "throttled",
                 "best": best, "fits_on": fits_on, "layers": per_layer}
        edges.append(entry)
        if not fits_on:
            throttled.append(entry)

    verdict = "trunk_throttled" if throttled else "trunk_fits"
    return ok(
        value={
            "verdict": verdict,
            "net": net,
            "track_mm": tw,
            "pads": len(pts),
            "cluster_count": len(clusters),
            "clusters": [{"id": c["id"], "pads": c["pads"],
                          "center": [round(c["center"][0], 3),
                                     round(c["center"][1], 3)],
                          "bbox": [round(v, 3) for v in c["bbox"]],
                          "pads_at": c["pads_at"]} for c in clusters],
            "edges": edges,
            "throttled_edges": len(throttled),
            "layers": coin,
            "measured_up_to_mm": round(max_w, 3),
        },
        unit="mm, count",
        model="pad clustering + spanning tree + maximum-bottleneck width over a band",
        basis="copper of one net sits on clusters of near-touching pads, and a trunk "
              "has to run from cluster to cluster. In a band of band_mm either side of "
              "the straight line between two cluster centres, each grid cell is worth "
              "the widest track whose centre sits there: 2*min(distance to other-net "
              "copper - clearance, distance to the board edge - edge clearance). The "
              "widest trunk that joins the two clusters is the maximum-bottleneck path "
              "through those cells, so it may step around copper but not over it",
        precision="widths are exact distances to the nearest copper sampled on a "
                  "%.2f mm grid, so a lane narrower than the grid can be missed - a "
                  "no_path verdict or a width of 0 can be a grid artefact, and "
                  "re-running at a quarter of the width is the check; a real width "
                  "may be up to one grid step wider than reported. Apertures wider "
                  "than %.2f mm are NOT measured: the search reaches %.2f mm and a "
                  "cell with nothing in reach reports that bound with "
                  "limited_by.kind = beyond_reach. The path is 4-connected cells, so "
                  "a track along it bends within one grid step. Footprint bodies and "
                  "keepouts are not obstacles, and pours are not obstacles unless "
                  "pours_block. The width is what fits; no path is written"
                  % (step, max_w, reach_mm),
        assumptions={"clearance_mm": clr, "edge_clearance_mm": edge,
                     "cluster_mm": join, "band_mm": band, "grid_mm": step,
                     "clearance_from": ("caller" if clearance_mm is not None
                                        else "net class Default" if rule_clr
                                        else "nothing in the file, 0.2 mm assumed"),
                     "track_from": ("caller" if track_mm is not None
                                    else "net class Default" if rule_tw
                                    else "nothing in the file, 0.25 mm assumed"),
                     "pours_counted": bool(pours_block),
                     "edges_shown": min(len(tree), int(max_edges)),
                     "not_claimed": "this is not a route and does not choose one: it "
                                    "says a trunk of this width CAN join the clusters "
                                    "inside this band, and names the cell that limits "
                                    "it. Outside the band it says nothing"},
    )

def _clearance_rule(board, clearance_mm=None, edge_clearance_mm=None):
    """(clearance, edge clearance, where the clearance came from).

    One implementation, because a second copy of "which net class" drifts: the
    caller's argument wins, then the Default class, then the loosest class in the
    file, and only then an assumed 0.2 - and the assumption is reported, not
    silently applied.
    """
    rules = board.rules or {}
    classes = dict((k, v) for k, v in (rules.get("net_classes") or {}).items()
                   if isinstance(v, dict))
    default_cls = classes.get("Default") or {}
    rule_clr = default_cls.get("clearance")
    if rule_clr is None:
        clrs = [float(v.get("clearance")) for v in classes.values()
                if isinstance(v.get("clearance"), (int, float))]
        rule_clr = min(clrs) if clrs else None
    clr = float(clearance_mm) if clearance_mm is not None else float(rule_clr or 0.2)
    edge = (float(edge_clearance_mm) if edge_clearance_mm is not None
            else float((rules.get("clearance") or {}).get("min_copper_edge_clearance")
                       or clr))
    src = ("caller" if clearance_mm is not None
           else "net class Default" if rule_clr
           else "nothing in the file, 0.2 mm assumed")
    return clr, edge, src


def teardrop_room(board, length_mm=0.5, clearance_mm=None, edge_clearance_mm=None,
                  layers=None, region=None, limit=40, grid_mm=0.05,
                  max_length_mm=2.0, pours_block=False):
    """How much room has each pad/track corner got for a teardrop, and how long can
    that teardrop be before it breaks a clearance?

    The reference board has 762 teardrops and every board drawn here has none, so
    "why does mine look worse" has a measurable part and this is it. KiCad can
    always DRAW a teardrop; the question a layout actually asks is whether the
    corner has room - the added copper has to stay `clearance_mm` off other nets
    and `edge_clearance_mm` off the board edge, and in a 0.3 mm fan-out it often
    does not.

    A corner is a track endpoint on a pad of the same net whose OTHER end is off
    that pad; a track that stops inside its own pad is a stub, not a corner.

    The envelope measured: from the point where the track's CENTRELINE leaves the
    pad, a triangle whose half-width is the pad's chord there (perpendicular to the
    track), never less than the track, tapering to the track width at distance
    `length_mm`. Width is taken at the pad's boundary and not at the pad's farthest
    corner, because the copper a teardrop adds there is the pad itself - taking the
    diagonal instead made a 45-degree exit from a 0.3 x 1.6 pad read 1.6 wide. Its
    two slanted edges and its end cap are sampled at `grid_mm` and every sample must
    clear, so `room_mm` is the minimum over samples and `limited_by` names what set
    it. `max_length_mm` is the longest length, searched in 0.1 mm steps upward from
    `length_mm` (or downward when that already fails), whose whole envelope still
    clears - zero when nothing fits.

    POURS are not obstacles unless `pours_block=True`, for the same reason the
    other board tools ignore them: the filler cuts a plane back around new copper.
    """
    if not board.outline:
        return err("this board has no outline", "NO_GEOMETRY",
                   hint="a teardrop near the edge is measured against Edge.Cuts")
    clr, edge, clr_src = _clearance_rule(board, clearance_mm, edge_clearance_mm)
    L = float(length_mm)
    if L <= 0:
        return err("length_mm must be > 0", "ARG_RANGE")
    step = float(grid_mm or 0.05)
    if step <= 0:
        return err("grid_mm must be > 0", "ARG_RANGE")
    cap = max(float(max_length_mm or 2.0), L)
    all_layers = _copper_layers(board)
    coin = list(layers) if layers else list(all_layers)
    for name in coin:
        if name not in all_layers:
            return err("no such copper layer: %r (board has %s)"
                       % (name, ", ".join(all_layers)), "ARG_INVALID")
    qbox = None
    if region is not None:
        try:
            rx0, ry0, rx1, ry1 = [float(v) for v in region]
        except (TypeError, ValueError):
            return err("region must be [x0, y0, x1, y1] in mm", "ARG_INVALID")
        qbox = (min(rx0, rx1), min(ry0, ry1), max(rx0, rx1), max(ry0, ry1))
    rings = [poly for poly in (board.outline or []) if len(poly) >= 3]

    cell = max(1.0, 2.0 + clr + step)
    entries = []
    junctions = 0
    non_region = 0
    degenerate = 0
    for name in coin:
        items = [it for it in _items(board, layer=name)
                 if it.get("kind") in ("pad", "via", "track")]
        if pours_block:
            # A pour becomes its EDGES, not one 20k-vertex polygon: the bucket then
            # only touches the edges that are near a sample.
            for zi, zone in enumerate(board.zones or []):
                if name not in [str(l) for l in (zone.get("layers") or [])]:
                    continue
                for pi, poly in enumerate(zone.get("filled_polygons") or []):
                    npoly = len(poly)
                    for i in range(npoly):
                        e0, e1 = poly[i], poly[(i + 1) % npoly]
                        if e0 == e1:
                            continue
                        items.append({"kind": "zone", "net": zone.get("net"),
                                      "ref": "zone%d.%d" % (zi, pi), "layer": name,
                                      "seg": ((e0[0], e0[1]), (e1[0], e1[1]), 0.0)})
        # Obstacles are PER NET: a teardrop of net X may touch X's own copper (that
        # is where it lives), so the same-net items must not be in the search at
        # all. Filtering the single nearest result afterwards is not enough - the
        # nearest thing was the track the teardrop grows out of, and every junction
        # read as blocked by exactly one clearance.
        _obs = {}

        def _obstacles(for_net):
            key = for_net
            if key not in _obs:
                keep = [it for it in items if it.get("net") != for_net]
                _obs[key] = (keep, _bucketize(keep, cell, pad=clr + step),
                             _bbox_list(keep))
            return _obs[key]

        pads = [it for it in items if it.get("kind") == "pad"]
        pad_boxes = _bbox_list(pads)
        pad_buckets = _bucketize(pads, cell, pad=clr + step) if pads else {}
        for it in items:
            if it.get("kind") != "track":
                continue
            a, b, w = it["seg"]
            net = it.get("net")
            for p, q in ((a, b), (b, a)):
                # The pad the track lands on: same net, and the end point is in it
                # (or within a hair of it, because a track may stop on the pad's
                # boundary). Ring by ring, because a pad 0.3 mm away sits in the
                # next cell.
                pad = None
                gx = int(math.floor(p[0] / cell))
                gy = int(math.floor(p[1] / cell))
                for rr in (0, 1, 2):
                    for ox in range(-rr, rr + 1):
                        for oy in range(-rr, rr + 1):
                            if rr and max(abs(ox), abs(oy)) != rr:
                                continue
                            for idx in (pad_buckets.get((gx + ox, gy + oy)) or ()):
                                cand = pads[idx]
                                if net is not None and cand.get("net") != net:
                                    continue
                                if G.point_in_polygon(p, cand["poly"]) or \
                                        G.dist_point_polygon(p, cand["poly"]) <= 0.01:
                                    pad = cand
                                    break
                            if pad is not None:
                                break
                        if pad is not None:
                            break
                    if pad is not None:
                        break
                if pad is None:
                    continue
                poly = pad["poly"]
                # The FAR end has to be off the pad. A track that runs from inside
                # the pad to its own far edge (a stub inside a 1.6 mm pad does) gave
                # a direction INTO the pad, and the envelope then grew backwards
                # through the pad and out over its neighbours' pads.
                if G.point_in_polygon(q, poly) or \
                        G.dist_point_polygon(q, poly) <= 0.01:
                    continue
                nv = float(len(poly))
                cx = sum(v[0] for v in poly) / nv
                cy = sum(v[1] for v in poly) / nv
                # The direction is the TRACK's own, not centroid-to-far-end: a
                # track that stops a fraction of a millimetre from the pad's
                # centroid gave a direction of its own numerical noise (0.0002 mm
                # long, so direction (-1, 0)), and the envelope then grew out of
                # the pad's left edge across the neighbour's pad - that was seven of
                # the twenty worst corners on the real board.
                dx, dy = q[0] - p[0], q[1] - p[1]
                ln = math.hypot(dx, dy)
                if ln <= 1e-6:
                    degenerate += 1
                    continue
                u = (dx / ln, dy / ln)
                nrm = (-u[1], u[0])
                ts = [(v[0] - cx) * u[0] + (v[1] - cy) * u[1] for v in poly]
                ns = [(v[0] - cx) * nrm[0] + (v[1] - cy) * nrm[1] for v in poly]
                # Where the centreline LEAVES the pad. The copper a teardrop adds at
                # that boundary is the pad itself, so the envelope's wide end is the
                # pad's chord there - perpendicular to the track - and never
                # narrower than the track. Using the pad's extreme along the track
                # instead made a 45-degree exit from a rectangular pad 1.3x its own
                # width, which reported whole rows of tight pads as blocked.
                npoly = len(poly)
                ex_, ey_ = q[0] - p[0], q[1] - p[1]
                tmax = None
                for i in range(npoly):
                    v0, v1 = poly[i], poly[(i + 1) % npoly]
                    wx, wy = v1[0] - v0[0], v1[1] - v0[1]
                    den = ex_ * wy - ey_ * wx
                    if abs(den) < 1e-12:
                        continue
                    tt = ((v0[0] - p[0]) * wy - (v0[1] - p[1]) * wx) / den
                    ss = ((v0[0] - p[0]) * ey_ - (v0[1] - p[1]) * ex_) / den
                    if -1e-9 <= ss <= 1.0 + 1e-9 and -1e-9 <= tt <= 1.0 + 1e-9:
                        if tmax is None or tt > tmax:
                            tmax = tt
                if tmax is None:
                    # No crossing means the endpoint is already ON the pad and the
                    # track leaves from there, so that IS the exit point. Falling
                    # back to the pad's extreme along the track put two of U1's
                    # corners 0.4 mm outside their own pad.
                    p0 = p
                else:
                    p0 = (p[0] + ex_ * tmax, p[1] + ey_ * tmax)
                if qbox is not None and not (qbox[0] <= p0[0] <= qbox[2]
                                             and qbox[1] <= p0[1] <= qbox[3]):
                    non_region += 1
                    continue
                junctions += 1
                s_lo = s_hi = None
                for i in range(npoly):
                    v0, v1 = poly[i], poly[(i + 1) % npoly]
                    wx, wy = v1[0] - v0[0], v1[1] - v0[1]
                    den = nrm[0] * wy - nrm[1] * wx
                    if abs(den) < 1e-12:
                        continue
                    ss = ((v0[0] - p0[0]) * wy - (v0[1] - p0[1]) * wx) / den
                    tt = ((v0[0] - p0[0]) * nrm[1] - (v0[1] - p0[1]) * nrm[0]) / den
                    if -1e-9 <= tt <= 1.0 + 1e-9:
                        s_lo = ss if s_lo is None else min(s_lo, ss)
                        s_hi = ss if s_hi is None else max(s_hi, ss)
                if s_lo is None:
                    half_chord = (max(ns) - min(ns)) / 2.0
                else:
                    # HALF the chord: a teardrop is symmetric about its track, so
                    # one side of the chord is the pad and the other is the mirror
                    # of it - taking the whole chord doubled the pad on one side.
                    half_chord = (s_hi - s_lo) / 2.0
                hw0 = max(half_chord, w / 2.0)
                hw1 = w / 2.0

                obs_items, obs_buckets, obs_boxes = _obstacles(net)

                def _measure(Lv, _p0=p0, _u=u, _n=nrm, _h0=hw0, _h1=hw1,
                             _it=obs_items, _bu=obs_buckets, _bx=obs_boxes,
                             _on=name):
                    """(room_mm, limited_by, at) for an envelope of length Lv.

                    room < 0 means the envelope would be illegal by that much, so
                    the sign is a verdict and the magnitude is a distance - one
                    number, both readings.
                    """
                    npts = max(4, int(math.ceil(Lv / step)))
                    best = None
                    for k in range(npts + 1):
                        t = Lv * k / npts
                        hw = _h0 + (_h1 - _h0) * (t / Lv)
                        bx = _p0[0] + _u[0] * t
                        by = _p0[1] + _u[1] * t
                        pts = [(bx + _n[0] * hw, by + _n[1] * hw),
                               (bx - _n[0] * hw, by - _n[1] * hw)]
                        if k == npts:
                            pts.append((bx, by))      # the cap's midpoint
                        for pt in pts:
                            inside = any(G.point_in_polygon(pt, r) for r in rings)
                            de = min(G.dist_point_polygon(pt, r) for r in rings)
                            if not inside:
                                de = -de            # outside: illegal by de + edge
                            room = de - edge
                            near_mm = de
                            info = {"kind": "board_edge", "net": None,
                                    "ref": "Edge.Cuts", "on": _on}
                            near = _nearest_from(_it, _bu, cell, pt[0], pt[1],
                                                 rings=3, bboxes=_bx)
                            if near is not None:
                                d, obj = near
                                rc = d - clr
                                if rc < room:
                                    room = rc
                                    near_mm = d
                                    info = {"kind": obj.get("kind"),
                                            "net": obj.get("net"),
                                            "ref": obj.get("ref"), "on": _on}
                            if best is None or room < best[0]:
                                best = (room, dict(info, distance_mm=round(near_mm, 3)),
                                        (pt[0], pt[1]))
                    return best

                room, info, at = _measure(L)
                # The length search is the expensive part (a full re-sample per
                # 0.1 mm probe) and it is only ever reported for the corners
                # printed, so it is deferred until the sort has picked them.
                entries.append(({
                    "ref": pad.get("ref") or "?",
                    "net": net,
                    "layer": name,
                    "at": [round(p0[0], 3), round(p0[1], 3)],
                    "pad_width_mm": round(2.0 * hw0, 3),
                    "track_mm": round(w, 3),
                    "room_mm": round(room, 3),
                    "fits": room >= 0.0,
                    "max_length_mm": 0.0,
                    "limited_by": info,
                }, _measure, room))
    if junctions == 0:
        return err("no track ends on a pad of its own net", "NO_GEOMETRY",
                   hint="a teardrop lives in the corner where a track meets a pad; "
                        "this board has no such corner on the layers asked for")
    entries.sort(key=lambda t: (t[2], t[0]["ref"] or ""))
    blocked = [e for e in entries if not e[0]["fits"]]
    shown = entries[:max(1, int(limit))]
    for entry, measure, room in shown:
        longest = 0.0
        if room >= 0.0:
            longest = L
            probe = L + 0.1
            while probe <= cap + 1e-9:
                if measure(probe)[0] < 0.0:
                    break
                longest = probe
                probe += 0.1
        else:
            probe = L - 0.1
            while probe >= 0.1 - 1e-9:
                if measure(probe)[0] >= 0.0:
                    longest = probe
                    break
                probe -= 0.1
        entry["max_length_mm"] = round(longest, 3)
    entries = [t[0] for t in entries]
    return ok(
        value={
            "verdict": "teardrops_blocked" if blocked else "teardrops_fit",
            "length_mm": round(L, 3),
            "junction_count": junctions,
            "fits": len(entries) - len(blocked),
            "blocked": len(blocked),
            "worst": shown and [t[0] for t in shown] or [],
            "skipped": {"outside_region": non_region, "zero_length_track":
                        degenerate},
            "search_step_mm": 0.1,
            "max_length_mm": round(cap, 3),
        },
        unit="mm, count",
        model="pad/track corner envelope sampled against other-net copper and the "
              "board edge",
        basis="a teardrop is copper added in the corner where a track leaves a pad. "
              "Its envelope runs from the pad's far side along the track direction, "
              "its width at the pad is the pad's own width across the track and it "
              "tapers to the track width after length_mm. Every sample on its two "
              "slanted edges and its end cap has to clear other nets' copper by the "
              "clearance and the board edge by the edge clearance; the minimum over "
              "samples is room_mm, and a negative room is the distance by which the "
              "envelope would be illegal",
        precision="samples are taken at %.2f mm along the envelope, so room_mm is "
                  "exact to that sampling and a corner that is narrow by less than "
                  "one sample can be missed; `pad_width_mm` is the pad's chord at "
                  "the point where the track leaves it, doubled (the track width "
                  "when the pad is narrower there); copper farther than "
                  "three search cells (about %.2f mm) is treated as clear, which "
                  "makes room_mm an UPPER bound when the whole corner is empty; "
                  "max_length_mm is searched in 0.1 mm steps, so it is a lower bound "
                  "of the true limit"
                  % (step, 3.0 * cell),
        assumptions={"clearance_mm": clr, "edge_clearance_mm": edge,
                     "clearance_from": clr_src,
                     "layers": coin, "pours_counted": bool(pours_block),
                     "worst_shown": min(len(entries), max(1, int(limit))),
                     "not_claimed": "this is not a teardrop and not a KiCad setting: "
                                    "it says the corner has room for an envelope of "
                                    "this shape and how long it may be, not which "
                                    "curve to draw, not the hole, not silk or mask"},
    )


def _poly_bands(poly, band=0.5):
    """Edges of a polygon bucketed by the y bands they span (for point-in-polygon).

    Ray casting only ever tests the edges whose y-range straddles the query's y,
    so this index gives the SAME answer as a full scan while making the cost
    independent of the vertex count. That is the whole point: a GND fill on the
    reference board has 20 168 vertices, and the naive test would run once per
    track endpoint of that net.
    """
    bands = {}
    n = len(poly)
    for i in range(n):
        y1, y2 = poly[i][1], poly[(i + 1) % n][1]
        lo, hi = (y1, y2) if y1 <= y2 else (y2, y1)
        for k in range(int(math.floor(lo / band)), int(math.floor(hi / band)) + 1):
            bands.setdefault(k, []).append(i)
    return bands


def _poly_holds(poly, bands, band, px, py):
    """Is (px, py) inside the polygon? Same ray casting as G.point_in_polygon,
    restricted to the edges that can cross the ray.

    A point ON the boundary is not counted here: the caller's touch test already
    unions anything that overlaps, so a boundary point is never only-held.
    """
    edges = bands.get(int(math.floor(py / band)))
    if not edges:
        return False
    n = len(poly)
    inside = False
    for i in edges:
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > py) != (y2 > py):
            if x1 + (py - y1) * (x2 - x1) / (y2 - y1) > px:
                inside = not inside
    return inside


def _net_copper(board, net, pours_connect=True):
    """One net's copper as distance-comparable items, filled pours included.

    `_items` covers pads, tracks and vias but not the filled copper of a zone, and
    on a board with pours that omission is the largest piece of the net: the
    question 'why is this rail still in pieces' cannot even be asked without it.
    Each filled_polygon of a same-net zone becomes one item of kind "zone",
    tagged with the zone's index and its piece number so a report can name it.
    """
    items = [dict(it) for it in _items(board) if it.get("net") == net]
    pours = 0
    if pours_connect:
        for zi, z in enumerate(board.zones or []):
            if z.get("net") != net or z.get("is_keepout"):
                continue
            for layer in (z.get("layers") or []):
                for k, poly in enumerate(z.get("filled_polygons") or []):
                    if len(poly) < 3:
                        continue
                    items.append({"kind": "zone", "net": net, "layer": layer,
                                  "layers": [layer],
                                  "poly": [(float(x), float(y)) for x, y in poly],
                                  "ref": "zone%d.piece%d" % (zi, k),
                                  "zone": zi, "priority": z.get("priority")})
                    pours += 1
    return items, pours


def _copper_edges(items):
    """Every item as flat segments: a track is ONE segment with half its width, a
    polygon is its boundary with zero width.

    Connectivity is exact this way and cheap. A track with round ends is exactly
    the set of points within w/2 of its centreline, so two tracks touch when the
    centreline distance is under the sum of the half-widths, and a track touches a
    polygon when its centreline comes within w/2 of the boundary or one of its ends
    is inside. Distance between two polygon boundaries is a plain segment-pair
    minimum, which the grid makes local instead of quadratic in the vertex count
    (the 20 168-vertex fill against a 3 000-track net).
    """
    prims = []
    for idx, it in enumerate(items):
        if "seg" in it:
            a, b, w = it["seg"]
            prims.append((idx, (a[0], a[1]), (b[0], b[1]), w / 2.0))
        else:
            poly = it["poly"]
            n = len(poly)
            for i in range(n):
                p, q = poly[i], poly[(i + 1) % n]
                prims.append((idx, (p[0], p[1]), (q[0], q[1]), 0.0))
    return prims


def _edge_grid(prims, cell):
    """Segment index. Each segment is filed in every cell its inflated bbox covers,
    so two segments within (hw_a + hw_b) always share at least one cell."""
    grid = {}
    for k, (idx, a, b, hw) in enumerate(prims):
        x0, y0 = min(a[0], b[0]) - hw, min(a[1], b[1]) - hw
        x1, y1 = max(a[0], b[0]) + hw, max(a[1], b[1]) + hw
        for gx in range(int(math.floor(x0 / cell)), int(math.floor(x1 / cell)) + 1):
            for gy in range(int(math.floor(y0 / cell)), int(math.floor(y1 / cell)) + 1):
                grid.setdefault((gx, gy), []).append(k)
    return grid


def _copper_groups(items, prims, grid, cell=1.0, tol=1e-6):
    """Union-find over one net's copper: which pieces are actually one conductor.

    Two items are joined when their metal overlaps or touches - a segment pair
    closer than the sum of the half-widths - or when one of them lies inside the
    other (a via under a pour, a track inside a fill). Touching is judged on the
    geometry alone, at `tol`, which is the same rule KiCad's connectivity engine
    applies to stored copper; silk, mask and keepouts play no part.
    """
    n = len(items)
    par = list(range(n))

    def find(x):
        while par[x] != x:
            par[x] = par[par[x]]
            x = par[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            par[rb] = ra

    for bucket in grid.values():
        for i in range(len(bucket)):
            for j in range(i + 1, len(bucket)):
                pa, pb = prims[bucket[i]], prims[bucket[j]]
                ia, ib = pa[0], pb[0]
                if ia == ib or find(ia) == find(ib):
                    continue
                if not set(items[ia]["layers"]) & set(items[ib]["layers"]):
                    continue
                d = G.dist_segment_segment(pa[1], pa[2], pb[1], pb[2])
                if d <= pa[3] + pb[3] + tol:
                    union(ia, ib)

    # A piece wholly inside another leaves no boundary intersection (a via under a
    # pour, a pad under a solid-connected fill), so every segment end is also
    # tested against the polygons whose bbox covers it.
    poly_of = {}
    cover = {}
    for idx, it in enumerate(items):
        if "poly" not in it or len(it["poly"]) < 3:
            continue
        poly_of[idx] = (_poly_bands(it["poly"]), it["poly"])
    if poly_of:
        for idx in poly_of:
            x0, y0, x1, y1 = G.bbox_of(items[idx]["poly"])
            for gx in range(int(math.floor(x0 / cell)), int(math.floor(x1 / cell)) + 1):
                for gy in range(int(math.floor(y0 / cell)), int(math.floor(y1 / cell)) + 1):
                    cover.setdefault((gx, gy), []).append(idx)
    for (idx, a, b, _hw) in prims:
        if not cover:
            break
        for pt in (a, b):
            for pidx in cover.get((int(math.floor(pt[0] / cell)),
                                   int(math.floor(pt[1] / cell))), ()):
                if pidx == idx or find(pidx) == find(idx):
                    continue
                bands, poly = poly_of[pidx]
                if _poly_holds(poly, bands, 0.5, pt[0], pt[1]):
                    union(pidx, idx)
    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return list(groups.values()), find


def _piece_gap(prims_a, prims_b, grid_b, cell, limit_mm):
    """Smallest boundary distance between two pieces, or None if beyond limit_mm.

    Searched from the SMALLER piece outward, so a pour with 20 000 edges never
    drives the loop: the rings are walked only as far as limit_mm, and the answer
    is either the exact minimum inside that radius or an honest 'not within'.
    """
    rings = max(1, int(math.ceil(limit_mm / cell)))
    best = None
    for (idx, a, b, hw) in prims_a:
        gx = int(math.floor(min(a[0], b[0]) / cell))
        gy = int(math.floor(min(a[1], b[1]) / cell))
        hx = int(math.floor(max(a[0], b[0]) / cell))
        hy = int(math.floor(max(a[1], b[1]) / cell))
        seen = set()
        for cx in range(gx - rings, hx + rings + 1):
            for cy in range(gy - rings, hy + rings + 1):
                for k in grid_b.get((cx, cy), ()):
                    if k in seen:
                        continue
                    seen.add(k)
                    pb = prims_b[k]
                    d = G.dist_segment_segment(a, b, pb[1], pb[2]) - hw - pb[3]
                    if best is None or d < best[0]:
                        best = (d, (idx, a, b), pb)
    if best is None or best[0] > limit_mm:
        return None
    return best


def net_gaps(board, net=None, pours_connect=True, gap_limit_mm=5.0, limit=12,
             cell_mm=1.0):
    """Which pieces of one net's copper are still separate, and how far apart.

    DRC says 115 unconnected on the reference board and stops there: it counts
    ratsnest edges, it does not say that +3V3 alone is 38 of them, that they split
    into 39 pieces, or where the nearest two pieces of a piece are. This walks the
    net's own copper as geometry - pads, tracks, vias and the filled polygons of
    its pours - unions what touches, and reports the pieces and the shortest gap
    between each pair of neighbours.

    Pads COUNT as copper, which is a real decision and not a detail: a pour island
    held on only by the thermal spokes of a pad that the main pour also touches is
    one conductor to this walker. KiCad's DRC disagrees on exactly that case -
    GND on the reference board comes back as 4 pieces here and 15 in the DRC,
    while +3V3 (38), AGND (9), $1I8749/$2N1188 (6) and $1I8749/VDDA (5) agree
    exactly. Where the two disagree, KiCad decides whether the board passes; this
    tool tells you where the copper is apart and how far.

    It routes nothing and moves nothing: the numbers say which two clusters are
    worth joining and how far apart they are, the join stays a decision.

    With no `net`, every net is counted and only the counts come back - that is
    the cross-check against DRC (sum of pieces-1 should equal unconnected).
    """
    if not cell_mm or cell_mm <= 0:
        return err("cell_mm must be positive", "ARG_RANGE")
    if gap_limit_mm is None or gap_limit_mm <= 0:
        return err("gap_limit_mm must be positive", "ARG_RANGE")
    cell = float(cell_mm)

    nets = []
    if net is None:
        seen = set()
        for it in _items(board):
            if it.get("net") and it["net"] not in seen:
                seen.add(it["net"].__str__())
                nets.append(it["net"])
        for z in (board.zones or []):
            if z.get("net") and str(z["net"]) not in seen and not z.get("is_keepout"):
                seen.add(str(z["net"]))
                nets.append(z["net"])
    else:
        nets = [net]

    summary, keep, pours_total = [], {}, 0
    for name in nets:
        items, pours = _net_copper(board, name, pours_connect=pours_connect)
        pours_total += pours
        if not items:
            continue
        prims = _copper_edges(items)
        grid = _edge_grid(prims, cell)
        groups, find = _copper_groups(items, prims, grid, cell)
        summary.append({"net": name, "pieces": len(groups),
                        "items": len(items), "pours": pours,
                        "edges_needed": max(0, len(groups) - 1)})
        if net is not None:
            keep[name] = (items, prims, groups, find)

    if net is not None and not summary:
        return err("no copper of that net on this board", "NO_GEOMETRY",
                   hint="net names are the board's own; use board_nets to see them")

    value = {"nets": sorted(summary, key=lambda s: (-s["edges_needed"], str(s["net"]))),
             "edges_needed_total": sum(s["edges_needed"] for s in summary),
             "pours_counted_as_copper": int(pours_total)}
    assumptions = {"pours_connect": bool(pours_connect),
                   "cell_mm": cell,
                   "gap_limit_mm": None if net is None else float(gap_limit_mm),
                   "touch_tolerance_mm": 1e-6,
                   "silk_mask_keepout_ignored": True,
                   "pads_are_copper": True,
                   "not_claimed": "this routes nothing and joins nothing: it says "
                                  "which pieces of one net are apart, how far and "
                                  "where, and leaves the join, the layer and the "
                                  "width to you",
                   "cross_check": "edges_needed per net was measured against "
                                  "board_drc().unconnected on the reference board: "
                                  "+3V3 38/38, AGND 9/9, $1I8749/$2N1188 6/6, "
                                  "$1I8749/VDDA 5/5 agree; GND does not (4 here, 15 in "
                                  "the DRC) - the 11 spare edges are pour islands held "
                                  "on only by the thermal spokes of a pad the main pour "
                                  "also touches, so they are separate to KiCad and "
                                  "joined to this walker"}

    if net is None:
        return ok(value=value, unit="count",
                  model="union-find over one net's copper geometry (pads, tracks, "
                        "vias, filled pour pieces)",
                  basis="two pieces of the same net are joined when their copper "
                        "touches or overlaps on a shared copper layer, or when one "
                        "lies inside the other; every net is walked separately",
                  precision="touching is judged at %.0e mm, so copper stored with a "
                            "hairline gap reads as separate (DRC applies the same "
                            "rule); a zone that was never filled contributes no "
                            "copper at all and its net is under-counted" % 1e-6,
                  assumptions=assumptions)

    items, prims, groups, find = keep[net]
    groups.sort(key=lambda g: -len(g))
    pieces = []
    for gi, g in enumerate(groups):
        pads = [items[i] for i in g if items[i]["kind"] == "pad"]
        bxs = [G.bbox_of(items[i]["poly"]) if "poly" in items[i]
               else _item_bbox(items[i]) for i in g]
        pieces.append({
            "piece": gi,
            "items": len(g),
            "pads": len(pads),
            "refs": [p.get("ref") for p in pads][:12],
            "tracks": len([i for i in g if items[i]["kind"] == "track"]),
            "vias": len([i for i in g if items[i]["kind"] == "via"]),
            "pour_pieces": len([i for i in g if items[i]["kind"] == "zone"]),
            "bbox": [round(min(b[0] for b in bxs), 3), round(min(b[1] for b in bxs), 3),
                     round(max(b[2] for b in bxs), 3), round(max(b[3] for b in bxs), 3)],
        })

    # nearest gap between every pair of pieces that has one inside gap_limit_mm
    prims_of = [[p for p in prims if find(p[0]) == find(g[0])] for g in groups]
    grids = {gi: _edge_grid(ps, cell) for gi, ps in enumerate(prims_of)}
    gaps = []
    for gi in range(len(groups)):
        for gj in range(gi + 1, len(groups)):
            if len(prims_of[gi]) <= len(prims_of[gj]):
                small, big, grid_big = prims_of[gi], prims_of[gj], grids[gj]
                si, sj = gi, gj
            else:
                small, big, grid_big = prims_of[gj], prims_of[gi], grids[gi]
                si, sj = gj, gi
            hit = _piece_gap(small, big, grid_big, cell, float(gap_limit_mm))
            if hit is None:
                continue
            d, seg_a, seg_b = hit
            gaps.append({"from": si, "to": sj, "gap_mm": round(d, 3),
                         "on": sorted(set(items[seg_a[0]]["layers"]) &
                                      set(items[seg_b[0]]["layers"])),
                         "a": {"ref": items[seg_a[0]].get("ref"),
                               "seg": [[round(c, 3) for c in seg_a[1]],
                                       [round(c, 3) for c in seg_a[2]]]},
                         "b": {"ref": items[seg_b[0]].get("ref"),
                               "seg": [[round(c, 3) for c in seg_b[1]],
                                       [round(c, 3) for c in seg_b[2]]]}})
    gaps.sort(key=lambda g: g["gap_mm"])
    shown = gaps[:max(1, int(limit))]
    reachable = set()
    for g in gaps:
        reachable.add(g["from"])
        reachable.add(g["to"])
    value.update({
        "net": net,
        "pieces_detail": pieces[:max(1, int(limit))],
        "piece_count": len(groups),
        "edges_needed": max(0, len(groups) - 1),
        "gaps": shown,
        "gaps_found": len(gaps),
        "pairs_beyond_limit": max(0, len(groups) * (len(groups) - 1) // 2 - len(gaps)),
        "pieces_with_no_neighbour_within_limit": [p["piece"] for p in pieces
                                                  if p["piece"] not in reachable],
    })
    return ok(
        value=value,
        unit="mm, count",
        model="union-find over one net's copper geometry, then edge-to-edge distance "
              "between the pieces that came out",
        basis="two pieces of the same net are one conductor when their copper "
              "touches or overlaps on a shared copper layer, or when one lies "
              "inside the other; gap_mm is the smallest distance between the metal "
              "of two pieces, measured segment to segment on the pair printed in "
              "`a` and `b` - the join is yours to draw, this says how far apart "
              "they are and where",
        precision="touching is judged at 1e-6 mm and pours are counted only when "
                  "the zone stored a fill result, so gaps are exact to the stored "
                  "geometry but a pair farther apart than gap_limit_mm is not "
                  "measured (counted in pairs_beyond_limit instead); `seg` is the "
                  "closest pair of SEGMENTS, not the single closest pair of points",
        assumptions=assumptions,
    )


def _occupancy(board, items, keepouts, step, margin):
    """A per-layer grid where None = outside the board, False = free, a net name =
    copper owned by that net, True = blocked for everybody (two nets or a keepout).

    Storing WHICH net owns a cell is what makes an escape test meaningful: a pad is
    always surrounded by its own copper (itself, and its neighbours in the fan-out),
    so a grid of bare booleans reports every single pad as walled in - the first run
    on stm32f401re_core answered "180 of 180 pads blocked", which is not a finding
    about the board, it is the measurement counting the pad against itself.
    """
    xs, ys = [], []
    for poly in (board.outline or []):
        for p in poly:
            xs.append(p[0])
            ys.append(p[1])
    for it in items:
        bx = _item_bbox(it)
        xs.extend([bx[0], bx[2]])
        ys.extend([bx[1], bx[3]])
    if not xs:
        return {"x0": 0.0, "y0": 0.0, "step": step, "cols": 0, "rows": 0, "cells": []}
    pad = max(margin, 1.0) + step
    x0, y0 = min(xs) - pad, min(ys) - pad
    x1, y1 = max(xs) + pad, max(ys) + pad
    cols = max(1, int(math.ceil((x1 - x0) / step)))
    rows = max(1, int(math.ceil((y1 - y0) / step)))
    if cols * rows > 900000:
        cols = min(cols, 900)
        rows = min(rows, 1000)
        step = max((x1 - x0) / cols, (y1 - y0) / rows)
        cols = max(1, int(math.ceil((x1 - x0) / step)))
        rows = max(1, int(math.ceil((y1 - y0) / step)))
    cells = [[False] * cols for _ in range(rows)]

    def mark(cx, cy, half, net=True):
        gx0 = max(0, int(math.floor((cx - half - x0) / step)))
        gx1 = min(cols - 1, int(math.floor((cx + half - x0) / step)))
        gy0 = max(0, int(math.floor((cy - half - y0) / step)))
        gy1 = min(rows - 1, int(math.floor((cy + half - y0) / step)))
        for gy in range(gy0, gy1 + 1):
            row = cells[gy]
            for gx in range(gx0, gx1 + 1):
                cur = row[gx]
                if cur is False:
                    row[gx] = net
                elif cur is None or cur is True:
                    pass
                elif net is True or cur != net:
                    row[gx] = True

    for item in items:
        net = item.get("net") or True
        if "poly" in item:
            bx = _item_bbox(item)
            mark((bx[0] + bx[2]) / 2.0, (bx[1] + bx[3]) / 2.0,
                 max(bx[2] - bx[0], bx[3] - bx[1]) / 2.0 + margin, net)
        else:
            a, b, w = item["seg"]
            length = G.dist_point_point(a, b)
            steps = max(1, int(math.ceil(length / (step / 2.0))))
            for i in range(steps + 1):
                t = i / float(steps)
                mark(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t,
                     w / 2.0 + margin, net)
    for zone in keepouts:
        poly = zone.get("outline") or []
        if len(poly) < 3:
            continue
        zb = G.bbox_of(poly)
        for gy in range(rows):
            cy = y0 + (gy + 0.5) * step
            if not (zb[1] <= cy <= zb[3]):
                continue
            row = cells[gy]
            for gx in range(cols):
                cx = x0 + (gx + 0.5) * step
                if zb[0] <= cx <= zb[2] and G.point_in_polygon((cx, cy), poly):
                    row[gx] = True        # a keepout blocks every net
    if board.outline:
        for gy in range(rows):
            cy = y0 + (gy + 0.5) * step
            row = cells[gy]
            for gx in range(cols):
                cx = x0 + (gx + 0.5) * step
                if row[gx] is not False:
                    continue
                if not any(G.point_in_polygon((cx, cy), poly) for poly in board.outline):
                    row[gx] = None
    return {"x0": x0, "y0": y0, "step": step, "cols": cols, "rows": rows,
            "cells": cells}


def _poly_area(poly):
    """Shoelace area, mm^2, always positive."""
    a = 0.0
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        a += x1 * y2 - x2 * y1
    return abs(a) / 2.0


def _poly_bbox(poly):
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return (min(xs), min(ys), max(xs), max(ys))


def _sampled(poly, limit=90):
    """Evenly thinned vertex list, so a 600-vertex island costs the same as a small one."""
    if len(poly) <= limit:
        return poly
    step = max(1, len(poly) // limit)
    return poly[::step]


def _gap_between(a, b, limit=90):
    """Closest vertex pair between two polygons, plus its distance."""
    A, B = _sampled(a, limit), _sampled(b, limit)
    best = None
    for p in A:
        for q in B:
            d = math.hypot(p[0] - q[0], p[1] - q[1])
            if best is None or d < best[0]:
                best = (d, p, q)
    return best


def pour_fragments(board, net=None, layer=None, gap_limit=None):
    """How many separate pieces is this pour cut into, and how far apart are they?

    The filler will not tell you this and DRC only reports the pieces that happen
    to hold a pad. A shaped rail that is chopped by every signal that crosses it
    is *not* one conductor, and the only way to know is to count the pieces.

    Every number here comes from the stored `filled_polygon` geometry: area by
    the shoelace formula, gap by the closest vertex pair between two pieces
    (thinned to 90 vertices each, so the gap is an upper bound, never a guess).
    Whether a piece *can* be joined to its neighbour is not decided here - that
    depends on the tracks and pads between them, which is `board_routability`.
    """
    if not board.zones:
        return err("no zones in this board", "NO_GEOMETRY",
                   hint="a pour is a ZONE/POUR object; the fragments of a pour that "
                        "does not exist are not zero, they are undefined")
    rows = []
    for zi, zone in enumerate(board.zones):
        znet = zone.get("net")
        if net is not None and znet != net:
            continue
        zlayers = [str(x) for x in (zone.get("layers") or [])]
        if layer is not None and layer not in zlayers:
            continue
        polys = [p for p in (zone.get("filled_polygons") or []) if len(p) >= 3]
        if not polys:
            rows.append({"zone_index": zi, "net": znet, "layers": zlayers,
                         "fragments": None, "status": "pour_not_filled",
                         "detail": "the zone exists but carries no filled_polygon: the "
                                   "vendor engine has not poured it, so its fragment "
                                   "count is unknown, not zero"})
            continue
        frags = []
        for k, poly in enumerate(polys):
            bb = _poly_bbox(poly)
            frags.append({
                "index": k,
                "area_mm2": round(_poly_area(poly), 4),
                "bbox": [round(v, 3) for v in bb],
                "centroid": [round((bb[0] + bb[2]) / 2, 3), round((bb[1] + bb[3]) / 2, 3)],
                "vertex_count": len(poly),
            })
        frags.sort(key=lambda f: -f["area_mm2"])
        pairs = []
        for i in range(len(polys)):
            for j in range(i + 1, len(polys)):
                g = _gap_between(polys[i], polys[j])
                if g is None:
                    continue
                if gap_limit is not None and g[0] > float(gap_limit):
                    continue
                pairs.append({"a": i, "b": j,
                              "gap_mm": round(g[0], 4),
                              "closest_a": [round(g[1][0], 3), round(g[1][1], 3)],
                              "closest_b": [round(g[2][0], 3), round(g[2][1], 3)]})
        pairs.sort(key=lambda r: r["gap_mm"])
        rows.append({
            "zone_index": zi, "net": znet, "layers": zlayers,
            "fragments": len(frags), "detail": frags,
            "nearest_pairs": pairs[:24],
            "largest_share": (round(frags[0]["area_mm2"] /
                                    max(1e-9, sum(f["area_mm2"] for f in frags)), 4)
                              if frags else None),
        })
    tot = sum(r.get("fragments") or 0 for r in rows)
    return ok(
        value={"zones": rows, "fragment_total": tot,
               "read_it_like_this": (
                   "one fragment = one conductor piece. A net whose pour is in N pieces "
                   "is N pieces until a track, a via or a same-net plane joins them; "
                   "DRC only complains about the pieces that contain a pad"),
               "not_a_connectivity_verdict": (
                   "fragments are counted from filled_polygon geometry alone. Pieces "
                   "may still be joined by tracks or vias that this function does not "
                   "walk - board_drc().unconnected_total is the authoritative answer "
                   "for what is electrically disconnected"),
               },
        unit="count",
        model="shoelace area + nearest-vertex gap over stored filled_polygons",
        basis="filled_polygon vertex rings as written by the vendor fill engine; gap is "
              "the minimum distance between two vertex samples (90 per polygon), so it "
              "is an upper bound on the true minimum separation",
        precision="exact for area and vertex count; the gap is sampled, never smaller "
                  "than the true gap and shrinking as the sample grows",
        assumptions={"source": "the poured geometry stored in the file (filled_polygon); "
                               "a zone that was never filled reports pour_not_filled",
                     "net_filter": net, "layer_filter": layer},
    )


def pour_connectivity(board, zone_index=0):
    """Do the zone's net pads actually land inside the pour?

    A pour that looks right in the file can still be isolated: the outline is one
    thing, the thermals and the fill result are another. This reports, per pad of
    the zone net, whether the pad centre is inside the filled polygon (or the
    outline when the fill result was not stored).
    """
    if not board.zones:
        return err("no zones in this board", "NO_GEOMETRY",
                   hint="a pour is a ZONE/POUR object; if the board has none, there is "
                        "nothing to check")
    try:
        zone = board.zones[int(zone_index)]
    except (IndexError, ValueError, TypeError):
        return err("zone_index out of range (0..%d)" % (len(board.zones) - 1),
                   "ARG_RANGE")
    polygons = zone.get("filled_polygons") or []
    source = "filled_polygons"
    if not polygons:
        polygons = [zone.get("outline") or []]
        source = "outline_only (fill result not stored)"
    polygons = [p for p in polygons if len(p) >= 3]
    if not polygons:
        return err("zone has neither a filled polygon nor an outline", "NO_GEOMETRY")
    net = zone.get("net")
    inside, outside = [], []
    for fp in board.footprints:
        for pad in fp.get("pads") or []:
            if pad.get("net") != net:
                continue
            if pad.get("x") is None:
                continue
            p = (pad["x"], pad["y"])
            hit = any(G.point_in_polygon(p, poly) for poly in polygons)
            ref = "%s.%s" % (fp.get("ref") or "?", pad.get("number") or "?")
            (inside if hit else outside).append(ref)
    return ok(
        value={"zone_net": net, "layers": zone.get("layers"),
               "connected_pads": len(inside), "isolated_pads": len(outside),
               "isolated": outside[:40], "polygon_source": source,
               "authoritative": False,
               "this_is_not_a_connectivity_verdict": (
                   "the word 'isolated' here means 'this pad's CENTRE is not inside "
                   "the pour polygon'. It does not mean the pad is unconnected: the "
                   "pad's own copper, a thermal spoke, a via to a same-net plane on "
                   "another layer, and a track that leaves the pad are all invisible "
                   "to a centre-in-polygon test"),
               "measured_on_the_reference_board": (
                   "this test reported 10 GND and 3 +3V3 pads 'isolated' on a board "
                   "whose DRC had 0 unconnected items - every one of them was a false "
                   "alarm, not a defect"),
               "use_instead": ("board_drc().unconnected_total - KiCad's connectivity "
                               "engine. That is the authoritative answer"),
               },
        unit="count",
        model="point_in_polygon_per_pad (NOT a connectivity verdict)",
        basis="pad centre tested against the pour polygon(s) with ray casting; "
              "boundary counts as inside",
        precision="approximate: a pad partially covered by pour copper is treated as "
                  "connected by its centre only; thermal spoke count, isolated "
                  "islands inside the pour, and every other-layer connection are NOT "
                  "evaluated. `authoritative` is false: do not conclude 'this net is "
                  "broken' from `isolated`",
        assumptions={"zone_index": int(zone_index), "net": net,
                     "polygons": len(polygons),
                     "authoritative": False,
                     "verdict_available_from": "board_drc (unconnected_total)",
                     "why_not_authoritative": (
                         "pad-centre-in-polygon cannot see spokes, vias, tracks or "
                         "the pad's own copper; only the DRC connectivity engine can")},
    )


def cross_split(board, layer=None, signal_nets=None):
    """Does any signal cross a discontinuity in its reference plane?

    Needs the reference planes as zones. Without them the honest answer is "not
    answerable", because the split cannot be seen - and that is exactly the class
    of error where the DRC stays green.
    """
    if not board.zones:
        return err(
            "cannot answer: this board has no zone/plane objects, so reference-plane "
            "continuity is not observable",
            "FACT_ABSENT_IN_FORMAT",
            hint="if the board really has planes, they are zones/regions the adapter did "
                 "not map - run board_summary and check present.zones")
    planes = [z for z in board.zones if not z.get("is_keepout") and
              (z.get("filled_polygons") or z.get("outline"))]
    if not planes:
        return err("zones exist but none carries a polygon", "NO_GEOMETRY")
    tracks = board.tracks
    if layer:
        tracks = [t for t in tracks if t.get("layer") == layer]
    if signal_nets:
        tracks = [t for t in tracks if t.get("net") in set(signal_nets)]
    crossings = []
    for tr in tracks:
        pts = tr.get("_points") or [tr["start"], tr["end"]]
        for via_pt in pts:
            covered = 0
            for zone in planes:
                if zone.get("net") == tr.get("net"):
                    continue
                for poly in (zone.get("filled_polygons") or [zone.get("outline")]):
                    if len(poly) >= 3 and G.point_in_polygon(tuple(via_pt), poly):
                        covered += 1
                        break
            if covered == 0:
                crossings.append({"net": tr.get("net"), "layer": tr.get("layer"),
                                  "x_mm": round(via_pt[0], 3),
                                  "y_mm": round(via_pt[1], 3),
                                  "reason": "no plane polygon covers this point"})
                break
    return ok(
        value={"unreferenced_points": len(crossings), "samples": crossings[:40],
               "planes_considered": len(planes), "tracks_checked": len(tracks)},
        unit="count",
        model="plane_cover_point_test",
        basis="for each track vertex, test whether ANY plane polygon (a zone of a "
              "different net) contains it; a vertex outside every plane means the "
              "return current has no continuous path underneath it",
        precision="point-sampling only: a track can cross a narrow split between two "
                  "sampled vertices and be missed. Plane distance is not modelled - "
                  "the reference plane is assumed to be the nearest zone layer",
        assumptions={"layer": layer, "nets": signal_nets},
    )


def loop_area(board, net):
    """Estimate the signal/return loop area for one net.

    loop area ~ (signal extent) x (height to the reference plane). The height
    comes from the stackup when it is stored; otherwise this refuses, because a
    guessed dielectric height produces a guessed EMC answer.
    """
    tracks = [t for t in board.tracks if t.get("net") == net]
    if not tracks:
        return err("no tracks on net %r" % net, "NO_GEOMETRY")
    pts = []
    for tr in tracks:
        pts.extend(tr.get("_points") or [tr["start"], tr["end"]])
    x0, y0, x1, y1 = G.bbox_of(pts)
    if not board.stackup:
        return err(
            "cannot compute loop area: the file carries no stackup, so the height to "
            "the reference plane is unknown",
            "FACT_ABSENT_IN_FORMAT",
            hint="set the stackup in the EDA's layer manager (KiCad: Board Setup) or "
                 "pass the dielectric height explicitly")
    h = None
    for idx, layer in enumerate(board.stackup):
        if layer.get("type") == "copper":
            # distance to the next copper layer below, summing dielectric thickness
            total = 0.0
            for nxt in board.stackup[idx + 1:]:
                if nxt.get("type") == "copper":
                    h = total if total > 0 else None
                    break
                total += (nxt.get("thickness_mm") or 0.0)
            if h:
                break
    if not h:
        return err("stackup present but no dielectric thickness found between copper "
                   "layers", "FACT_ABSENT_IN_FORMAT")
    length = 0.0
    for tr in tracks:
        pts_list = tr.get("_points") or [tr["start"], tr["end"]]
        for i in range(len(pts_list) - 1):
            length += G.dist_point_point(pts_list[i], pts_list[i + 1])
    spread = max(x1 - x0, 0.0) * max(y1 - y0, 0.0)
    return ok(
        value={"total_length_mm": round(length, 3),
               "reference_height_mm": round(h, 4),
               "loop_area_mm2": round(length * h, 4),
               "bbox_spread_area_mm2": round(spread, 3)},
        unit="mm^2",
        model="routed_length_times_plane_height",
        basis="loop area of a microstrip trace over its reference plane approximated as "
              "(total routed length of the net) x (height to the nearest copper layer "
              "below); the return current is assumed to flow directly underneath",
        precision="order-of-magnitude only: it ignores return-current spreading, via "
                  "stubs, and the fact that a meander cancels part of its own loop. "
                  "bbox_spread_area_mm2 is reported separately for reference and is NOT "
                  "used in loop_area_mm2",
        assumptions={"net": net, "tracks": len(tracks),
                     "bbox": [round(x0, 3), round(y0, 3), round(x1, 3), round(y1, 3)]},
    )

# ------------------------------------------------------------- antenna keep-out
def _rect_poly_hit(rect, poly):
    """Does an axis-aligned rectangle touch a polygon? (any crossing or containment)"""
    x0, y0, x1, y1 = rect
    if len(poly) < 3:
        return False
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    for c in corners:
        if G.point_in_polygon(c, poly):
            return True
    for p in poly:
        if x0 <= p[0] <= x1 and y0 <= p[1] <= y1:
            return True
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        for j in range(4):
            if G.segments_intersect(a, b, corners[j], corners[(j + 1) % 4]):
                return True
    return False

def antenna_keepout(board, x_mm, y_mm, width_mm, length_mm, layer=None,
                    margin_mm=0.0, top=40):
    """What is inside the rectangle an antenna needs to keep empty?

    An antenna keep-out is not a design guideline, it is the antenna: copper under a
    chip antenna, a ground pour next to an IFA, or a via fence across a slot all
    shift the resonance and kill the efficiency, and the failure mode is "the radio
    works but the range is a third of the datasheet". So the answer is the list of
    what is actually there, with the pour polygons counted separately - a zone is
    invisible in a pad/track listing and is exactly the thing that should be gone.

    Coordinates are the board's own (same system as board_components), the rectangle
    is given by its lower-left corner and its size.
    """
    try:
        x = float(x_mm)
        y = float(y_mm)
        w = float(width_mm)
        ln = float(length_mm)
        margin = float(margin_mm or 0.0)
    except (TypeError, ValueError):
        return err("x_mm, y_mm, width_mm, length_mm must be numeric", "ARG_INVALID")
    if w <= 0 or ln <= 0:
        return err("width_mm and length_mm must be > 0", "ARG_RANGE")
    if margin < 0:
        return err("margin_mm must be >= 0", "ARG_RANGE")
    rect = (x - margin, y - margin, x + w + margin, y + ln + margin)

    items = _items(board)
    hits = []
    for it in items:
        if layer and layer not in (it.get("layers") or []):
            continue
        gap = G.bbox_gap(_item_bbox(it), rect)
        if gap > 0.0:
            continue
        hits.append({"kind": it["kind"], "ref": it["ref"], "net": it["net"],
                     "layer": it.get("layer")})
    # a pad/track listing never shows a pour, and a pour is the usual culprit
    zone_hits = []
    for zi, zone in enumerate(board.zones or []):
        if layer and layer not in (zone.get("layers") or []):
            continue
        polys = zone.get("filled_polygons") or []
        source = "filled_polygons"
        if not polys:
            polys = [zone.get("outline") or []]
            source = "outline_only"
        for poly in polys:
            if len(poly) >= 3 and _rect_poly_hit(rect, poly):
                zone_hits.append({"zone_index": zi, "net": zone.get("net"),
                                  "layers": zone.get("layers"), "source": source,
                                  "is_keepout": bool(zone.get("is_keepout"))})
                break

    kinds = {}
    nets = {}
    for h in hits:
        kinds[h["kind"]] = kinds.get(h["kind"], 0) + 1
        if h["net"]:
            nets[h["net"]] = nets.get(h["net"], 0) + 1

    # board outline: is any part of the rectangle off the board?
    off_board = None
    if board.outline:
        outline = [p for poly in board.outline for p in poly]
        if outline:
            ob = G.bbox_of(outline)
            if (rect[0] < ob[0] or rect[1] < ob[1]
                    or rect[2] > ob[2] or rect[3] > ob[3]):
                off_board = {"board_bbox": [round(v, 3) for v in ob],
                             "note": "part of the rectangle is outside the board "
                                     "outline bounding box: either the keep-out hangs "
                                     "over the edge (fine for many chip antennas, which "
                                     "want the ground edge) or the coordinates are wrong"}

    clear = (not hits) and (not zone_hits)
    return ok(
        value={
            "rect_mm": [round(v, 3) for v in rect],
            "layer": layer or "all",
            "margin_mm": margin,
            "clear": clear,
            "copper_items_inside": len(hits),
            "by_kind": kinds,
            "by_net": dict(sorted(nets.items(), key=lambda kv: -kv[1])[:12]),
            "items": sorted(hits, key=lambda h: h["ref"])[:int(top)],
            "zones_touching": zone_hits[:int(top) or 1] if zone_hits else [],
            "off_board": off_board,
            "verdict": ("nothing but bare substrate inside the rectangle" if clear else
                        "%d copper item(s) and %d zone(s) intersect the rectangle: "
                        "remove them (copper under an antenna lowers the resonant "
                        "frequency and absorbs radiated power)"
                        % (len(hits), len(zone_hits))),
        },
        unit="count",
        model="rect_vs_copper_and_zone_bbox",
        basis=("every pad / track / via / copper graphic whose bounding box intersects "
               "the rectangle, plus every ZONE whose outline or filled polygon touches "
               "it (a pour does not appear in a pad/track listing and is the usual "
               "cause of a detuned antenna); containment tests are exact "
               "point-in-polygon, segment crossing and bbox overlap"),
        precision=("conservative on purpose: a bounding box that merely overlaps the "
                   "rectangle is reported even if the copper itself misses it, so "
                   "'clear' is reliable and 'occupied' may include a false positive "
                   "worth checking. Keep-out RULES from a datasheet (which layers, which "
                   "direction, how much) are not in the file - this only measures what "
                   "the file contains"),
        assumptions={"x_mm": x, "y_mm": y, "width_mm": w, "length_mm": ln,
                     "coordinate_system": "same as board_components (board units, Y up)"},
    )


# --------------------------------------------------------------- IR drop
def _cu_um_by_layer(board):
    """Copper thickness per copper layer, as far as the stackup states it."""
    out = {}
    for s in board.stackup or []:
        name = str(s.get("name") or "")
        if not name.endswith(".Cu"):
            continue
        t = s.get("copper_um")
        try:
            t = float(t) if t is not None else None
        except (TypeError, ValueError):
            t = None
        if t and t > 0:
            out[name] = t
    return out


def _stack_thickness(board):
    """Board thickness for a via barrel: the stated total, or the stackup sum."""
    t = board.thickness_mm
    try:
        t = float(t) if t is not None else None
    except (TypeError, ValueError):
        t = None
    if t and t > 0:
        return t
    tot = 0.0
    for s in board.stackup or []:
        try:
            v = float(s.get("thickness_mm"))
        except (TypeError, ValueError):
            continue
        if v > 0:
            tot += v
    return tot if tot > 0 else None


def _pad_lookup(board, spec):
    """Find pads by "REF.PAD" (or by "REF" when the footprint has one pad)."""
    want_ref, _, want_pad = str(spec).partition(".")
    hits = []
    for fp in board.footprints:
        if str(fp.get("ref")) != want_ref:
            continue
        for pad in fp.get("pads") or []:
            num = str(pad.get("number") or "")
            if want_pad == "" or num == want_pad:
                hits.append((fp, pad))
    return hits


def _pad_xy(fp, pad):
    try:
        return float(pad["x"]), float(pad["y"])
    except (KeyError, TypeError, ValueError):
        return float(fp.get("x") or 0.0), float(fp.get("y") or 0.0)


# The conductance a pad/track-to-pour link uses. Mirrors calc.zonefield.LINK_OHM,
# and `tests/test_ir_drop_zones.py` asserts the two stay equal - a silent drift
# here would change the answer without changing any schema.
_ZF_LINK_OHM = 1e-6
# The gridding pitch is clamped to this window (see calc/zonefield.py). Kept here
# too so the error message a caller reads states the same numbers the code uses.
ZF_MIN_GRID_MM = 0.2
ZF_MAX_GRID_MM = 2.0

def board_ir_drop(board, net, source, sinks, current_a=None, copper_um=None,
                  plating_um=None, quantize_mm=0.01, temp_c=20.0,
                  max_nodes=1200, model_zones=False, zone_grid_mm=None,
                  max_zone_nodes=8000, zone_link_mm=0.5, frequency_hz=None,
                  faces="one", mu_r=1.0, roughness_rq_um=None,
                  thermal_relief=None):
    """DC voltage drop across ONE net, out of the copper the board actually carries.

    This is "will 2 A make it from the regulator to the MCU without browning it
    out", and the only honest way to answer is to build the resistor network out
    of the real tracks, vias and pads - not to multiply a length by a sheet
    resistance.

    Nodes are (x, y, layer): two layers only touch where a via or a plated pad
    joins them, which is exactly the thing a length-times-resistivity estimate
    cannot see and the usual reason a real drop is much worse than the estimate.

    source: "U1.1"  - the pad the current enters
    sinks:  ["U2.8", ...] or [{"pad": "U2.8", "current_a": 0.3}, ...]
    current_a: the total, split evenly over the sinks when they do not state
               their own share

    Refuses instead of guessing when a used layer has no copper thickness, when
    the net has vias and `plating_um` is missing, or when nothing connects.

    `model_zones=True` also grids the copper pours on this net as 2-D resistive
    sheets and solves the whole thing with conjugate gradients (see
    `calc/zonefield.py`). That is what turns "the pour is in parallel, so this is
    an upper bound" into a number, and on a plane-heavy board it is the
    difference between a number and `NOT_APPLICABLE / singular`: the two halves of
    such a net are often joined by NOTHING but the pour.

    `frequency_hz` turns the same graph into a one-frequency answer: at 1 MHz the
    current in 1 oz copper has already stopped filling the thickness, so a DC
    answer is optimistic exactly where a switching supply lives. Every track and
    every pour square keeps its width and gets the effective thickness the exact
    1-D slab solution gives (t_eff = t/(R_ac/R_dc), the same solve `skin`
    publishes), and a via barrel is solved as the tube it is (the same Bessel
    answer as `roundwire`). Only resistances change - the topology, the DC path
    and the meaning of every other field do not.

    `thermal_relief={"spokes": n, "spoke_width_mm": w, "gap_mm": g}` replaces the
    modelled pad-to-pour link with the thermal relief that is really there: n spokes
    w wide crossing a g gap, i.e. R = R_sheet * g / (n * w) per connection. Without
    it a pad that sits INSIDE the fill is joined at 1 uOhm (solid copper) and a pad
    near it is bridged with ONE sheet square - both are stand-ins, and on a board
    where the fill the file carries does not show the relief the first one is
    OPTIMISTIC. With it, both become the same printed geometry. Off by default, and
    the default answer stays digit for digit what it was.

    What is still missing at a frequency is said out loud rather than absorbed:
    no proximity effect from a neighbouring conductor, no lateral crowding along
    the width of a wide strip, no phase (this is a magnitude, not an impedance).
    Left out (the default) the answer is the plain DC number, digit for digit.
    """
    from .calc import power as PW
    if frequency_hz is not None:
        from .calc import skin as _SK
        from .calc import roundwire as _RW

    if not net:
        return err("net is required", "ARG_REQUIRED")
    try:
        q = float(quantize_mm or 0.01)
    except (TypeError, ValueError):
        return err("quantize_mm must be a number", "ARG_INVALID")
    if q <= 0:
        return err("quantize_mm must be > 0", "ARG_RANGE")

    # The pour knobs are validated here rather than inside the gridding code,
    # because a pitch of 0 would otherwise be silently clamped to the 0.2 mm
    # floor and the caller would never learn the number it passed meant nothing.
    if zone_grid_mm is not None:
        try:
            zone_grid_mm = float(zone_grid_mm)
        except (TypeError, ValueError):
            return err("zone_grid_mm must be a number", "ARG_INVALID")
        if zone_grid_mm <= 0:
            return err("zone_grid_mm must be > 0", "ARG_RANGE",
                       hint="omit it and the pitch is planned from the area the "
                            "fill covers; a number is clamped to [%.1f, %.1f] mm"
                            % (ZF_MIN_GRID_MM, ZF_MAX_GRID_MM))
    try:
        max_zone_nodes = int(max_zone_nodes)
    except (TypeError, ValueError):
        return err("max_zone_nodes must be an integer", "ARG_INVALID")
    if max_zone_nodes <= 0:
        return err("max_zone_nodes must be > 0", "ARG_RANGE")
    try:
        zone_link_mm = float(zone_link_mm)
    except (TypeError, ValueError):
        return err("zone_link_mm must be a number", "ARG_INVALID")
    if zone_link_mm < 0:
        return err("zone_link_mm must be >= 0", "ARG_RANGE")

    # ---- thermal relief (0.0.84). Three numbers, all required: the geometry is the
    # whole point, and defaulting any of them would be inventing the answer.
    tr_geo = None
    if thermal_relief is not None:
        if not isinstance(thermal_relief, dict):
            return err("thermal_relief must be an object {spokes, spoke_width_mm, "
                       "gap_mm} - the thermal relief that joins the pad to the pour",
                       "ARG_INVALID")
        unknown = [k for k in thermal_relief
                   if k not in ("spokes", "spoke_width_mm", "gap_mm")]
        if unknown:
            return err("thermal_relief has unknown key(s) %s; it takes spokes, "
                       "spoke_width_mm and gap_mm" % ", ".join(sorted(unknown)),
                       "ARG_INVALID",
                       hint="a silently ignored key would leave the caller thinking "
                            "the relief was modelled")
        for key in ("spokes", "spoke_width_mm", "gap_mm"):
            if thermal_relief.get(key) is None:
                return err("thermal_relief.%s is required - R = R_sheet*gap/"
                           "(n*width) needs all three, and there is no sane default "
                           "for any of them" % key, "ARG_REQUIRED")
        try:
            tr_n = int(thermal_relief["spokes"])
            tr_w = float(thermal_relief["spoke_width_mm"])
            tr_g = float(thermal_relief["gap_mm"])
        except (TypeError, ValueError):
            return err("thermal_relief values must be numbers", "ARG_INVALID")
        if tr_n < 1 or tr_n > 16:
            return err("thermal_relief.spokes must be 1..16 (a relief with more than "
                       "16 spokes is not a relief, it is a solid connection)",
                       "ARG_RANGE")
        if not (tr_w > 0.0):
            return err("thermal_relief.spoke_width_mm must be > 0", "ARG_RANGE")
        if not (tr_g > 0.0):
            return err("thermal_relief.gap_mm must be > 0", "ARG_RANGE",
                       hint="a gap of 0 IS a solid connection - leave thermal_relief "
                            "out and the pad is joined at 1 uOhm")
        if not model_zones:
            return err("thermal_relief describes how a pad meets the COPPER POUR, so "
                       "it needs model_zones=true: with the pour off there is no "
                       "pour to cross a gap to", "ARG_CONFLICT",
                       hint="add model_zones=true, or drop thermal_relief")
        tr_geo = {"spokes": tr_n, "spoke_width_mm": tr_w, "gap_mm": tr_g}

    # ---- AC knobs (0.0.70). Validated before any copper is touched, so a bad
    # value can never half-apply to a network that is already half built.
    freq = None
    if frequency_hz is not None:
        try:
            freq = float(frequency_hz)
        except (TypeError, ValueError):
            return err("frequency_hz must be a number", "ARG_INVALID")
        if not freq > 0.0:
            return err("frequency_hz must be > 0", "ARG_RANGE",
                       hint="the DC answer is this same call WITHOUT frequency_hz "
                            "- there is no skin depth at 0 Hz")
    if faces is None:
        faces = "one"
    if faces not in ("one", "two"):
        return err("faces must be 'one' or 'two', not %r - and it is not a "
                   "cosmetic choice: 'one' means the return current runs on ONE "
                   "side of this copper (a track over a plane), 'two' means it is "
                   "symmetric (a stripline, a plane between two returns). The "
                   "difference is a factor of two in the effective thickness."
                   % (faces,), "ARG_INVALID")
    try:
        mur = 1.0 if mu_r is None else float(mu_r)
    except (TypeError, ValueError):
        return err("mu_r must be a number", "ARG_INVALID")
    if not mur >= 1.0:
        return err("mu_r must be >= 1 (copper is 1.0; a ferromagnetic plating "
                   "would be given its real mu_r and also breaks the 1-D "
                   "assumption)", "ARG_RANGE")
    rq = None
    if roughness_rq_um is not None:
        try:
            rq = float(roughness_rq_um)
        except (TypeError, ValueError):
            return err("roughness_rq_um must be a number (RMS height Rq, not Rz)",
                       "ARG_INVALID")
        if rq < 0.0:
            return err("roughness_rq_um must be >= 0", "ARG_RANGE")
    if freq is None:
        # Accepting these and then answering at DC would be a wrong number told
        # by omission, which is the one thing this server does not do.
        stray = []
        if faces != "one":
            stray.append("faces=%r" % (faces,))
        if mur != 1.0:
            stray.append("mu_r=%g" % mur)
        if rq is not None:
            stray.append("roughness_rq_um=%g" % rq)
        if stray:
            return err("%s only mean something at a frequency, and frequency_hz "
                       "was not given" % (" and ".join(stray),), "ARG_INVALID",
                       hint="either pass frequency_hz, or leave these at their "
                            "defaults and this is the plain DC answer")

    cu = _cu_um_by_layer(board)
    missing = []

    # An explicitly passed copper_um WINS over the stackup (the caller may know
    # the real post-plating weight) - and if the two disagree the fact is said
    # out loud instead of one of them being silently used.
    explicit = None
    if copper_um is not None:
        try:
            explicit = float(copper_um)
        except (TypeError, ValueError):
            return err("copper_um must be a number", "ARG_INVALID")
        if explicit <= 0:
            return err("copper_um must be > 0", "ARG_RANGE")

    def thick(layer):
        if explicit is not None:
            return explicit
        if layer in cu:
            return cu[layer]
        missing.append(layer)
        return None

    nodes = {}
    names = []
    edges = []
    warnings = []

    def node(x, y, layer):
        key = (int(round(float(x) / q)), int(round(float(y) / q)), layer)
        if key not in nodes:
            nodes[key] = "n%d" % len(names)
            names.append({"id": nodes[key], "x_mm": key[0] * q,
                          "y_mm": key[1] * q, "layer": layer})
        return nodes[key]

    # ---- AC scaling (0.0.70): one exact 1-D solve per distinct thickness, cached.
    # A board has a handful of copper weights, not a thousand, and the solve is
    # the same one `skin`/`roundwire` already publish - so this cannot drift away
    # from the number those tools report for the same foil.
    ac_fail = []
    _slab_cache = {}
    _barrel_cache = {}
    ac_acc = {"track": [], "zone": [], "via": []}

    def _slab(t_um):
        """(r_ac_over_r_dc, skin_depth_um) for a slab of this thickness."""
        key = round(float(t_um), 12)
        if key not in _slab_cache:
            env = _SK.ac_sheet_resistance(
                freq, thickness_um=float(t_um),
                rho_ohm_mm=PW._rho(temp_c) * 1e3, temp_c=temp_c, mu_r=mur,
                faces=faces, roughness_rq_um=rq, profile_samples=9)
            if env.get("status") != "ok":
                ac_fail.append(env)
                _slab_cache[key] = None
            else:
                v = env["value"]
                k = v.get("roughness_k") or 1.0
                _slab_cache[key] = (v["r_ac_over_r_dc"] * k, v["skin_depth_um"])
        return _slab_cache[key]

    def _barrel(drill, plate_um):
        """(r_ac_over_r_dc, skin_depth_um) for a plated barrel at this frequency."""
        key = (round(float(drill), 12), round(float(plate_um), 12))
        if key not in _barrel_cache:
            env = _RW.ac_round_conductor(
                freq, drill_mm=float(drill), plating_um=float(plate_um),
                rho_ohm_mm=PW._rho(temp_c) * 1e3, temp_c=temp_c, mu_r=mur,
                roughness_rq_um=rq)
            if env.get("status") != "ok":
                ac_fail.append(env)
                _barrel_cache[key] = None
            else:
                v = env["value"]
                k = v.get("roughness_k") or 1.0
                _barrel_cache[key] = (v["ac_over_dc"] * k, v["skin_depth_um"])
        return _barrel_cache[key]

    def add(a, b, r, kind, label, **kw):
        if a == b or r <= 0:
            return None
        e = {"a": a, "b": b, "resistance_ohm": r, "kind": kind, "label": label}
        e.update(kw)
        edges.append(e)
        return e

    tracks = [t for t in board.tracks if t.get("net") == net]
    vias = [v for v in board.vias if v.get("net") == net]
    pads = []
    for fp in board.footprints:
        for pad in fp.get("pads") or []:
            if pad.get("net") == net:
                pads.append((fp, pad))

    if not tracks and not model_zones:
        return err("net %r has no tracks on this board" % (net,), "NOT_APPLICABLE",
                   hint="without tracks there is no path to measure; an IR drop "
                        "answered from pad positions alone would be fiction. With "
                        "model_zones=True a copper pour on this net is copper too")

    # ---- tracks
    for tr in tracks:
        layer = tr.get("layer")
        t_um = thick(layer)
        if t_um is None:
            continue
        try:
            w = float(tr.get("width") or 0.0)
        except (TypeError, ValueError):
            w = 0.0
        if w <= 0:
            return err("a track on net %r has width 0" % (net,), "INTERNAL")
        # A track keeps its width and loses thickness: R_ac = rho*L/(w*t_eff).
        # At DC t_eff IS t, so the expression below is the one it always was.
        t_eff_um = t_um
        t_ratio = 1.0
        if freq is not None:
            got = _slab(t_um)
            if got is None:
                return ac_fail[-1]
            t_ratio = got[0]
            t_eff_um = t_um / t_ratio
        pts = tr.get("_points") or [tr["start"], tr["end"]]
        for i in range(len(pts) - 1):
            x1, y1 = float(pts[i][0]), float(pts[i][1])
            x2, y2 = float(pts[i + 1][0]), float(pts[i + 1][1])
            L = math.hypot(x2 - x1, y2 - y1)
            if L <= 0:
                continue
            r = PW._rho(temp_c) * (L * 1e-3) / (w * 1e-3 * t_eff_um * 1e-6)
            add(node(x1, y1, layer), node(x2, y2, layer), r, "track",
                "%s %.4gmm x %.3gmm" % (layer, L, w),
                layer=layer, length_mm=L, width_mm=w, copper_um=t_um,
                **({"copper_eff_um": t_eff_um, "r_ac_over_rdc": t_ratio}
                   if freq is not None else {}))
            if freq is not None:
                ac_acc["track"].append((r / t_ratio, r, t_ratio))
    if missing:
        return err("no copper thickness for layer(s) %s: the stackup does not "
                   "carry it and copper_um was not given" %
                   ", ".join(sorted(set(missing))), "ARG_REQUIRED",
                   hint="pass copper_um (1 oz = ~35 um, 2 oz = ~70 um after "
                        "plating); guessing it moves the answer by a factor of two")

    layer_warnings = []
    if explicit is not None and cu:
        odd = {k: v for k, v in cu.items() if abs(v - explicit) > 1e-9}
        if odd:
            layer_warnings.append(
                "copper_um=%.6g was used for every layer and OVERRIDES the "
                "stackup, which says %s" %
                (explicit, ", ".join("%s=%.6gum" % (k, v)
                                     for k, v in sorted(odd.items()))))

    # ---- vias: the only place two layers touch
    board_th = _stack_thickness(board)
    for vi in vias:
        if plating_um is None:
            return err("net %r has %d via(s) and plating_um was not given" %
                       (net, len(vias)), "ARG_REQUIRED",
                       hint="a via barrel is a copper tube; without the wall "
                            "thickness (usually 20..25 um, thinner mid-barrel) "
                            "its resistance is not knowable")
        if board_th is None:
            return err("the board thickness is not stated, so the via barrel "
                       "length is unknown", "ARG_REQUIRED",
                       hint="pass a stackup with thickness_mm or set "
                            "board.thickness_mm")
        try:
            drill = float(vi.get("drill") or 0.0)
        except (TypeError, ValueError):
            drill = 0.0
        if drill <= 0:
            return err("a via on net %r has no drill size" % (net,), "INTERNAL")
        vr = PW.via_resistance(drill, float(plating_um), board_th, temp_c=temp_c)
        if vr.get("status") != "ok":
            return vr
        r_via = vr["value"]["single_via_ohm"]
        v_ratio = 1.0
        if freq is not None:
            # A barrel is a TUBE, not a flat slab - and 0.0.67 measured that
            # treating a round section as square is off by 5.26x at 1 GHz, so the
            # tube solution is used rather than the slab one with a fudge.
            got = _barrel(drill, float(plating_um))
            if got is None:
                return ac_fail[-1]
            v_ratio = got[0]
            r_via = r_via * v_ratio
        x, y = float(vi["x"]), float(vi["y"])
        layers = _layer_span(board, vi.get("from"), vi.get("to"))
        if len(layers) < 2:
            warnings.append("via at (%.3f, %.3f) spans a single layer - ignored"
                            % (x, y))
            continue
        if model_zones and len(layers) > 2:
            # A through via is a plated tube through EVERY layer it crosses, and a
            # plane on an inner layer is copper that tube touches. With the pour
            # modelled the barrel is therefore split one segment per layer
            # boundary instead of being one edge from the top to the bottom. With
            # the flag off the historical single edge is kept, so the default
            # answer does not move.
            n_seg = len(layers) - 1
            r_seg = r_via / n_seg
            for k in range(n_seg):
                add(node(x, y, layers[k]), node(x, y, layers[k + 1]), r_seg, "via",
                    "via %.2gmm/%.3gum %s" % (drill, float(plating_um),
                                              layers[k]),
                    layer=layers[k], drill_mm=drill,
                    plating_um=float(plating_um), barrel_segment=1,
                    **({"r_ac_over_rdc": v_ratio} if freq is not None else {}))
                if freq is not None:
                    ac_acc["via"].append((r_seg / v_ratio, r_seg, v_ratio))
        else:
            add(node(x, y, layers[0]), node(x, y, layers[-1]), r_via, "via",
                "via %.2gmm/%.3gum" % (drill, float(plating_um)),
                layer="|".join([layers[0], layers[-1]]), drill_mm=drill,
                plating_um=float(plating_um),
                **({"r_ac_over_rdc": v_ratio} if freq is not None else {}))
            if freq is not None:
                ac_acc["via"].append((r_via / v_ratio, r_via, v_ratio))

    # ---- pads: plated holes join their layers, SMD pads are just terminals
    pad_line = 1e-6
    pad_boxes = []
    for fp, pad in pads:
        px, py = _pad_xy(fp, pad)
        layers = _item_layers(board, pad.get("layers"))
        try:
            pw = float(pad.get("w") or 0.0)
            ph = float(pad.get("h") or 0.0)
            prot = math.radians(float(pad.get("rotation") or 0.0))
        except (TypeError, ValueError):
            pw = ph = prot = 0.0
        if pw > 0 and ph > 0:
            ca = abs(math.cos(prot))
            sa = abs(math.sin(prot))
            pad_boxes.append((px, py, (pw * ca + ph * sa) * 0.5,
                              (pw * sa + ph * ca) * 0.5, list(layers)))
        if not layers:
            continue
        for lay in layers:
            node(px, py, lay)
        if pad.get("hole_mm") and len(layers) > 1:
            for k in range(len(layers) - 1):
                add(node(px, py, layers[k]), node(px, py, layers[k + 1]),
                    pad_line, "pad_barrel", "plated hole %s" % (layers[k],))

    # ---- copper pours: a 2-D sheet of the same copper, in parallel with all of
    # the above. Off by default: turning it on changes the number.
    base_nodes = len(names)
    zone_summary = None
    zone_bridged = []
    zone_spoked = []
    tr_by_layer = {}
    zone_wide = []
    zone_skipped = []
    if model_zones:
        from .calc import zonefield as ZF

        def zthick(layer):
            if explicit is not None:
                return explicit
            return cu.get(layer)

        thick_by_layer = {}
        # The sheet builder only knows rho and t, so at a frequency it is handed
        # t_eff - but the PHYSICAL weight is what a current density is measured
        # against, so it is kept separately and never quietly replaced.
        phys_by_layer = {}
        ratio_by_layer = {}
        sheets = []
        for z in (board.zones or []):
            if z.get("net") != net or z.get("keepout") or z.get("is_keepout"):
                continue
            for lay in ZF.zone_layer_list(z):
                if lay in thick_by_layer or lay in [s["layer"] for s in sheets]:
                    continue
                t = zthick(lay)
                if t is None:
                    zone_skipped.append(lay)
                else:
                    phys_by_layer[lay] = t
                    if freq is None:
                        thick_by_layer[lay] = t
                        ratio_by_layer[lay] = 1.0
                    else:
                        got = _slab(t)
                        if got is None:
                            return ac_fail[-1]
                        ratio_by_layer[lay] = got[0]
                        thick_by_layer[lay] = t / got[0]
        if thick_by_layer:
            sheets = ZF.build_sheets(board.zones, net, PW._rho(temp_c) * 1e3,
                                     thick_by_layer, grid_mm=zone_grid_mm,
                                     max_cells_total=max_zone_nodes)["layers"]
        znodes = {}
        for info in sheets:
            lay = info["layer"]
            h = info["h_mm"]
            t_um = info["thick_um"]

            def znode(key, _lay=lay):
                k = (_lay,) + key
                if k not in znodes:
                    cx, cy = ZF.cell_xy(info, key)
                    znodes[k] = node(cx, cy, _lay)
                return znodes[k]

            z_ratio = ratio_by_layer.get(lay, 1.0)
            for (k1, k2, r) in ZF.sheet_edges(info):
                add(znode(k1), znode(k2), r, "zone_sheet",
                    "%s pour %.3gmm cell" % (lay, h),
                    layer=lay, length_mm=h, width_mm=h,
                    copper_um=phys_by_layer.get(lay, t_um),
                    **({"copper_eff_um": t_um, "r_ac_over_rdc": z_ratio}
                       if freq is not None else {}))
                if freq is not None:
                    ac_acc["zone"].append((r / z_ratio, r, z_ratio))
            # every node already in the graph that sits on this pour is joined to
            # it: same net, same layer, same metal. 1 uOhm, not 0, so the solver
            # has a finite conductance to work with.
            info["_zf"] = ZF
            # A pad's anchor point is also a node in the graph, so it would be
            # joined here AND again by the pad-box loop below. With a 1 uOhm link
            # the double join is harmless (two shorts in parallel are a short);
            # with a real spoke resistance it would halve the answer, so the anchor
            # is left to the pad-box loop when a relief is being modelled.
            tr_ohm = None
            if tr_geo is not None:
                tr_ohm = ZF.thermal_spoke_ohm(info["sheet_ohm_per_sq"],
                                             tr_geo["gap_mm"], tr_geo["spokes"],
                                             tr_geo["spoke_width_mm"])
                tr_by_layer[lay] = tr_ohm
            anchor_here = set()
            if tr_geo is not None:
                for (ax, ay, _hw2, _hh2, aplays) in pad_boxes:
                    if lay in aplays:
                        anchor_here.add((int(round(ax / q)), int(round(ay / q))))
            for rec in list(names)[:base_nodes]:
                if rec["layer"] != lay:
                    continue
                x, y = rec["x_mm"], rec["y_mm"]
                is_anchor = ((int(round(x / q)), int(round(y / q))) in anchor_here)
                if tr_ohm is not None and is_anchor:
                    continue
                key = ZF.cell_index(info, x, y)
                if key is not None:
                    add(node(x, y, lay), znode(key), ZF.LINK_OHM, "zone_link",
                        "in pour %s" % (lay,), layer=lay)
                    continue
                key, d = ZF.nearest_cell(info, x, y, float(zone_link_mm))
                if key is not None:
                    if tr_ohm is not None and is_anchor:
                        # The pad does not sit in the fill, so the connection really
                        # is across a gap. Modelled as the relief the caller states
                        # rather than as one sheet square.
                        add(node(x, y, lay), znode(key), tr_ohm, "zone_spoke",
                            "thermal relief %dx%.3gmm over %.3gmm to pour %s"
                            % (tr_geo["spokes"], tr_geo["spoke_width_mm"], tr_geo["gap_mm"], lay),
                            layer=lay, bridged_mm=round(d, 6),
                            spokes=tr_geo["spokes"],
                            spoke_width_mm=tr_geo["spoke_width_mm"],
                            relief_gap_mm=tr_geo["gap_mm"],
                            width_mm=tr_geo["spoke_width_mm"],
                            copper_um=phys_by_layer.get(lay, t_um),
                            **({"copper_eff_um": t_um,
                                "r_ac_over_rdc": ratio_by_layer.get(lay, 1.0)}
                               if freq is not None else {}))
                        if freq is not None:
                            _zr = ratio_by_layer.get(lay, 1.0)
                            ac_acc["zone"].append((tr_ohm / _zr, tr_ohm, _zr))
                        zone_bridged.append({"layer": lay, "x_mm": x, "y_mm": y,
                                             "gap_mm": round(d, 6),
                                             "relief_ohm": round(tr_ohm, 12)})
                        zone_spoked.append({"layer": lay, "x_mm": x, "y_mm": y,
                                            "cells": 1,
                                            "gap_mm": round(d, 6)})
                        continue
                    # One sheet square, not a short: the gap being crossed is a
                    # thermal-relief spoke or an unfilled sliver, and a spoke of
                    # length ~ width is one square. Small enough not to invent a
                    # drop, large enough not to wreck the conditioning of the
                    # matrix (a 1e-6 ohm link here costs CG a factor of ten).
                    add(node(x, y, lay), znode(key), info["sheet_ohm_per_sq"],
                        "zone_bridge", "bridged %.3gmm to pour %s" % (d, lay),
                        layer=lay, bridged_mm=round(d, 6),
                        width_mm=info["h_mm"],
                        copper_um=phys_by_layer.get(lay, t_um),
                        **({"copper_eff_um": t_um,
                            "r_ac_over_rdc": ratio_by_layer.get(lay, 1.0)}
                           if freq is not None else {}))
                    if freq is not None:
                        _zr = ratio_by_layer.get(lay, 1.0)
                        ac_acc["zone"].append((info["sheet_ohm_per_sq"] / _zr,
                                               info["sheet_ohm_per_sq"], _zr))
                    zone_bridged.append({"layer": lay, "x_mm": x, "y_mm": y,
                                         "gap_mm": round(d, 6)})
        # A wide pad is not a point: a plane's current enters across the whole
        # pad. Linking only the cell under the anchor would invent a spreading
        # resistance that is not there.
        for (px, py, hw, hh, plays) in pad_boxes:
            for info in sheets:
                lay = info["layer"]
                if lay not in plays:
                    continue
                h = info["h_mm"]
                i0 = int(math.floor((px - hw - info["x0"]) / h))
                i1 = int(math.floor((px + hw - info["x0"]) / h))
                j0 = int(math.floor((py - hh - info["y0"]) / h))
                j1 = int(math.floor((py + hh - info["y0"]) / h))
                n = (i1 - i0 + 1) * (j1 - j0 + 1)
                if n <= 0:
                    continue
                if n > 2000:
                    zone_wide.append((lay, px, py, n))
                    continue
                contacted = []
                for j in range(max(j0, 0), min(j1, info["ny"] - 1) + 1):
                    for i in range(max(i0, 0), min(i1, info["nx"] - 1) + 1):
                        if info["mask"][j * info["nx"] + i]:
                            contacted.append((i, j))
                if tr_by_layer.get(lay) is not None:
                    # The fill covers this pad, but the caller states the connection
                    # is a relief. The relief's gap is what separates the pad from
                    # the copper, so with that gap the pad cannot really be sitting
                    # in the fill: the fill the file carries is the OUTLINE, not the
                    # relief. The pad's whole connection is one relief resistance,
                    # split evenly over the cells it touches so the parallel
                    # combination is that number and not a fraction of it.
                    if contacted:
                        k_cells = float(len(contacted))
                        r_cell = tr_by_layer[lay] * k_cells
                        for (i, j) in contacted:
                            add(node(px, py, lay), znode((i, j)), r_cell,
                                "zone_spoke_node",
                                "thermal relief (1/%d of R) at pad %s"
                                % (len(contacted), lay),
                                layer=lay, spokes=tr_geo["spokes"],
                                spoke_width_mm=tr_geo["spoke_width_mm"],
                                relief_gap_mm=tr_geo["gap_mm"],
                                relief_share=len(contacted))
                        zone_spoked.append({"layer": lay, "x_mm": px, "y_mm": py,
                                            "cells": len(contacted), "gap_mm": None})
                    else:
                        # The pad does not overlap the fill at all, but its anchor
                        # was skipped in the node loop because a relief was being
                        # modelled - so the one connection it does have (the gap
                        # it can still be bridged across) has to be made here, or
                        # the pad would silently drop out of the network.
                        key, d = ZF.nearest_cell(info, px, py, float(zone_link_mm))
                        if key is not None:
                            add(node(px, py, lay), znode(key), tr_by_layer[lay],
                                "zone_spoke",
                                "thermal relief %dx%.3gmm over %.3gmm to pour %s"
                                % (tr_geo["spokes"], tr_geo["spoke_width_mm"],
                                   tr_geo["gap_mm"], lay),
                                layer=lay, bridged_mm=round(d, 6),
                                spokes=tr_geo["spokes"],
                                spoke_width_mm=tr_geo["spoke_width_mm"],
                                relief_gap_mm=tr_geo["gap_mm"],
                                width_mm=tr_geo["spoke_width_mm"],
                                copper_um=phys_by_layer.get(lay, t_um))
                            zone_spoked.append({"layer": lay, "x_mm": px, "y_mm": py,
                                                "cells": 1, "gap_mm": round(d, 6)})
                else:
                    for (i, j) in contacted:
                        add(node(px, py, lay), znode((i, j)),
                            ZF.LINK_OHM, "zone_link", "pad contact %s" % (lay,),
                            layer=lay)

        if sheets:
            sheet_rows = []
            for s_ in sheets:
                row = {"layer": s_["layer"], "grid_mm": round(s_["h_mm"], 6),
                       "cells": s_["cells"],
                       "sheet_ohm_per_sq": round(s_["sheet_ohm_per_sq"], 9),
                       "thickness_um": (phys_by_layer.get(s_["layer"],
                                                          s_["thick_um"])
                                        if freq is not None else s_["thick_um"]),
                       "from_filled_polygons": s_["fill_polygons"],
                       "from_outline_only": s_["outline_only"]}
                if freq is not None:
                    _r = ratio_by_layer.get(s_["layer"], 1.0)
                    row["copper_eff_um"] = s_["thick_um"]
                    row["r_ac_over_rdc"] = round(_r, 9)
                    row["sheet_ohm_per_sq_dc"] = round(
                        s_["sheet_ohm_per_sq"] / _r, 9)
                sheet_rows.append(row)
            zone_summary = {
                "zones_modelled": sum(1 for z in (board.zones or [])
                                      if z.get("net") == net
                                      and not (z.get("keepout") or z.get("is_keepout"))),
                "sheets": sheet_rows,
                "cells_total": sum(s["cells"] for s in sheets),
                "bridged_nodes": len(zone_bridged),
                "wide_pads_left_point_linked": len(zone_wide),
                "bridged_max_gap_mm": (max(b["gap_mm"] for b in zone_bridged)
                                       if zone_bridged else 0.0),
                "link_ohm": ZF.LINK_OHM,
                "layers_skipped_no_thickness": sorted(set(zone_skipped)),
            }
            if zone_skipped:
                warnings.append(
                    "a copper pour on net %r sits on layer(s) %s, which the "
                    "stackup does not give a copper thickness for - that pour is "
                    "NOT in the answer" %
                    (net, ", ".join(sorted(set(zone_skipped)))))
            if zone_wide:
                warnings.append(
                    "%d pad(s) cover more than 2000 pour cells each; only the cell "
                    "under their anchor was tied to the pour, so their contact "
                    "spreading is over-stated (self-check: pass a coarser "
                    "zone_grid_mm)" % len(zone_wide))
            # what the FILE says about reliefs, reported and never adopted: the
            # geometry that goes into the answer is the caller's (or the 1 uOhm
            # stand-in), so a file value can only inform, not decide.
            file_states = []
            for z in (board.zones or []):
                if z.get("net") != net or z.get("keepout") or z.get("is_keepout"):
                    continue
                state = (z.get("connect_pads_effective")
                         or z.get("connect_pads"))
                if state is None:
                    continue
                file_states.append({
                    "layers": list(ZF.zone_layer_list(z)),
                    "connect_pads": z.get("connect_pads"),
                    "connect_pads_effective": state,
                    "connect_pads_source": z.get("connect_pads_source"),
                    "thermal_gap_mm": z.get("thermal_gap_mm"),
                    "thermal_bridge_width_mm": z.get("thermal_bridge_width_mm"),
                })
            if file_states:
                zone_summary["board_file_states"] = file_states
                if tr_geo is None and any(f["connect_pads_effective"] in
                                          ("thermal", "thru_hole_only")
                                          for f in file_states):
                    warnings.append(
                        "KiCad reads a THERMAL RELIEF for a pour on this net "
                        "(connect_pads=%s, thermal_gap=%s mm, "
                        "thermal_bridge_width=%s mm); a zone that writes no mode "
                        "word IS the thermal spelling, which is how KiCad itself "
                        "saves one. thermal_relief was not passed, so those pads "
                        "are joined at 1 uOhm (solid) and this answer is "
                        "OPTIMISTIC there. Pass thermal_relief with the same three "
                        "numbers to model it"
                        % (", ".join(sorted(set(f["connect_pads_effective"]
                                                for f in file_states))),
                           ", ".join(sorted(set("%.3g" % f["thermal_gap_mm"]
                                                for f in file_states
                                                if f["thermal_gap_mm"] is not None)))
                           or "-",
                           ", ".join(sorted(set("%.3g" % f["thermal_bridge_width_mm"]
                                                for f in file_states
                                                if f["thermal_bridge_width_mm"]
                                                is not None))) or "-"))
            if tr_geo is not None:
                zone_summary["thermal_relief"] = {
                    "spokes": tr_geo["spokes"],
                    "spoke_width_mm": tr_geo["spoke_width_mm"],
                    "gap_mm": tr_geo["gap_mm"],
                    "connection_ohm_by_layer": {
                        k2: round(v2, 12) for k2, v2 in sorted(tr_by_layer.items())},
                    "formula": ("R = R_sheet * gap / (spokes * width): a spoke is "
                                "gap/width squares of the same foil the sheet is, and "
                                "n of them in parallel divide it by n. R_sheet is the "
                                "one in use for that layer, so at a frequency this "
                                "carries the skin-corrected thickness"),
                    "pad_connections_modelled": len(zone_spoked),
                    "pads_split_over_cells": sum(1 for r in zone_spoked
                                                 if r.get("cells", 1) > 1),
                    "connections": [dict(c) for c in zone_spoked],
                    "not_in_it": ["the pad's own spreading resistance (the pad is a "
                                  "node, here and everywhere else in this answer)",
                                  "current crowding where a spoke meets the pad "
                                  "corner (3-D at the pad edge)",
                                  "a spoke wider than the pad perimeter is accepted "
                                  "and reported, not clamped"],
                }
                if not zone_spoked:
                    warnings.append(
                        "thermal_relief was given but no pad needed it: every pad on "
                        "this net either sits in the fill without being an anchor "
                        "point or has no pour contact at all. The answer is what it "
                        "would have been without the relief - check model_zones and "
                        "the zone_link_mm window")
                else:
                    warnings.append(
                        "%d pad connection(s) were modelled as thermal reliefs (%d x "
                        "%.3g mm spokes over %.3g mm), not as solid copper: R = "
                        "R_sheet*gap/(n*width). Without this the answer was "
                        "OPTIMISTIC for those pads"
                        % (len(zone_spoked), tr_geo["spokes"], tr_geo["spoke_width_mm"],
                           tr_geo["gap_mm"]))
            if zone_bridged:
                warnings.append(
                    "%d node(s) do not sit inside the filled pour but are within "
                    "%.3g mm of it, so they were bridged to the nearest cell with "
                    "ONE SHEET SQUARE: that is a thermal-relief spoke (or an "
                    "unfilled sliver) modelled to order of magnitude, not measured "
                    "- it is the single crudest number in this answer" %
                    (len(zone_bridged), float(zone_link_mm)))

    # ---- terminals
    src_hits = [h for h in _pad_lookup(board, source) if h[1].get("net") == net]
    if not src_hits:
        return err("source pad %r is not on net %r" % (source, net), "ARG_INVALID",
                   hint="give the pad the current really enters, as REF.PAD")

    def terminal(fp, pad, spec):
        px, py = _pad_xy(fp, pad)
        layers = _item_layers(board, pad.get("layers")) or [None]
        return node(px, py, layers[0]), px, py

    src_node, sx, sy = terminal(*src_hits[0], source)

    if isinstance(sinks, str):
        sinks = [sinks]
    if not sinks:
        return err("sinks is empty", "ARG_REQUIRED")
    sink_list = []
    for s in sinks:
        if isinstance(s, dict):
            spec, cur = s.get("pad"), s.get("current_a")
        else:
            spec, cur = s, None
        if not spec:
            return err("a sink has no pad", "ARG_INVALID")
        hits = [h for h in _pad_lookup(board, spec) if h[1].get("net") == net]
        if not hits:
            return err("sink pad %r is not on net %r" % (spec, net), "ARG_INVALID")
        nd, gx, gy = terminal(*hits[0], spec)
        try:
            cur = None if cur is None else float(cur)
        except (TypeError, ValueError):
            return err("sink %r has a non-numeric current_a" % (spec,),
                       "ARG_INVALID")
        sink_list.append({"pad": spec, "node": nd, "current_a": cur,
                          "x_mm": gx, "y_mm": gy})
    if any(s["current_a"] is None for s in sink_list):
        if current_a is None:
            return err("every sink is missing current_a and no total current_a "
                       "was given", "ARG_REQUIRED",
                       hint="an IR drop needs a current: split a total evenly "
                            "with current_a, or state it per sink")
        try:
            share = float(current_a) / len(sink_list)
        except (TypeError, ValueError):
            return err("current_a must be a number", "ARG_INVALID")
        for s in sink_list:
            if s["current_a"] is None:
                s["current_a"] = share
    total_a = sum(s["current_a"] for s in sink_list)
    if total_a <= 0:
        return err("the sink currents add up to <= 0", "ARG_RANGE")

    # `max_nodes` guards the DENSE O(n^3) solve. With the pour modelled the same
    # system is solved by conjugate gradients, and a through via then contributes
    # one node per layer it crosses instead of two - so the ceiling moves from
    # 1200 to 4096 for that path and stays exactly 1200 without it.
    base_limit = max_nodes if not model_zones else max(max_nodes, 4096)
    if base_nodes > base_limit:
        return err("the network has %d nodes (limit %d)" % (base_nodes, base_limit),
                   "TOO_LARGE",
                   hint="raise quantize_mm - two nodes closer than that are "
                        "merged anyway, so a 0.01 mm mesh on a big net is mostly "
                        "noise")
    zone_cells = len(names) - base_nodes
    if zone_cells > max_zone_nodes:
        return err("the pour grid has %d cells (limit %d)" % (zone_cells,
                                                             max_zone_nodes),
                   "TOO_LARGE",
                   hint="raise max_zone_nodes or pass zone_grid_mm; the sheet "
                        "resistance does not depend on the pitch, only how finely "
                        "the current spreads near a contact does")

    # Nothing on this net is copper at all: no tracks, and not one pour cell
    # could be gridded. Then the honest answer is the one the track-only path
    # gives - NOT_APPLICABLE, "there is nothing to measure" - rather than
    # NOT_CONNECTED, which would blame the layout for a missing model.
    if model_zones and not tracks and zone_cells == 0:
        return err("net %r has no tracks on this board" % (net,), "NOT_APPLICABLE",
                   hint="without tracks there is no path to measure; an IR drop "
                        "answered from pad positions alone would be fiction. With "
                        "model_zones=True a copper pour on this net is copper too")

    # ---- solve
    injections = {src_node: total_a}
    for s in sink_list:
        injections[s["node"]] = injections.get(s["node"], 0.0) - s["current_a"]
    solver_info = None
    if model_zones:
        # A pour grid can leave floating islands (a sliver that touches nothing),
        # and an isolated node makes the Laplacian singular. So the graph is cut
        # down to what the source can actually reach FIRST, and a sink that falls
        # outside says so instead of surfacing as a linear-algebra error.
        adj = {}
        for e in edges:
            adj.setdefault(e["a"], []).append(e["b"])
            adj.setdefault(e["b"], []).append(e["a"])
        seen = {src_node}
        stack = [src_node]
        while stack:
            k = stack.pop()
            for nb in adj.get(k, ()):
                if nb not in seen:
                    seen.add(nb)
                    stack.append(nb)
        stranded = [s["pad"] for s in sink_list if s["node"] not in seen]
        if stranded:
            return err("sink(s) %s are not connected to %s on net %r, even with "
                       "the copper pours modelled - there is no copper path"
                       % (", ".join(stranded), source, net), "NOT_CONNECTED",
                       hint="check that the pour on this net actually overlaps "
                            "the sink's copper and that no keepout isolates it")
        dropped = len(names) - len(seen)
        if dropped:
            keep = seen
            names = [n for n in names if n["id"] in keep]
            edges = [e for e in edges if e["a"] in keep and e["b"] in keep]
            warnings.append("%d node(s) of the pour are not connected to the "
                            "source by any copper and were dropped: they carry no "
                            "current" % dropped)
        sol, why = PW.resistive_network_cg([n["id"] for n in names], edges,
                                           injections, src_node, tol=1e-9)
        if sol is not None:
            solver_info = {"method": "%s_preconditioned_cg" % sol.get("precond",
                                                                       "jacobi"),
                           "iterations": sol["iterations"],
                           "residual_rel": sol["residual_rel"],
                           "converged": sol["converged"], "tol": 1e-9}
            if not sol["converged"]:
                return err("the copper network did not converge: residual %.3g "
                           "after %d CG iterations" % (sol["residual_rel"],
                                                       sol["iterations"]),
                           "NOT_CONVERGED",
                           hint="coarsen the pour with zone_grid_mm; a reported "
                                "drop out of an unconverged solve is noise")
    else:
        sol, why = PW.resistive_network([n["id"] for n in names], edges,
                                        injections, src_node)
    if sol is None:
        return err("could not solve the copper network: %s" % why, "NOT_APPLICABLE",
                   hint="the usual cause is a sink that no track on this net "
                        "actually joins to the source")

    volts = sol["voltages_v"]
    v0 = volts[src_node]
    sink_out = []
    for s in sink_list:
        v = volts[s["node"]]
        sink_out.append({"pad": s["pad"], "current_a": s["current_a"],
                         "voltage_v": round(v - v0, 9),
                         "drop_mv": round((v0 - v) * 1e3, 6),
                         "x_mm": s["x_mm"], "y_mm": s["y_mm"]})
    if not any(abs(s["voltage_v"]) > 1e-12 for s in sink_out):
        return err("no sink shows any voltage relative to the source - the network "
                   "is not actually connected", "NOT_APPLICABLE",
                   hint="check that the tracks on net %r reach the source pad" %
                        (net,))

    by_key = {}
    for e in edges:
        by_key.setdefault((e["a"], e["b"]), []).append(e)
        by_key.setdefault((e["b"], e["a"]), []).append(e)
    edge_out = []
    worst_j = None
    for e in sol["edges"]:
        cand = by_key.get((e["a"], e["b"])) or []
        src = None
        for c in cand:
            if abs(c["resistance_ohm"] - e["resistance_ohm"]) <= 1e-15 * max(
                    1.0, abs(c["resistance_ohm"])):
                src = c
                break
        cur = abs(e["current_a"])
        w = float(src.get("width_mm") or 0.0) if src else 0.0
        t_um = float(src.get("copper_um") or 0.0) if src else 0.0
        j = cur / (w * t_um * 1e-3) if (w > 0 and t_um > 0) else None
        rec = {"kind": src.get("kind") if src else "?",
               "label": src.get("label") if src else "",
               "a": e["a"], "b": e["b"],
               "resistance_ohm": round(e["resistance_ohm"], 9),
               "current_a": round(cur, 9),
               "drop_mv": round(abs(e["drop_v"]) * 1e3, 6),
               "power_mw": round(e["power_w"] * 1e3, 6),
               "current_density_a_per_mm2": (round(j, 6) if j is not None else None)}
        edge_out.append(rec)
        if j is not None and (worst_j is None or j > worst_j["current_density_a_per_mm2"]):
            worst_j = rec

    warnings.extend(layer_warnings)
    pour = [z for z in (board.zones or [])
            if z.get("net") == net
            and not (z.get("keepout") or z.get("is_keepout"))]
    if pour and not model_zones:
        warnings.append("%d copper zone(s) on net %r are NOT modelled: they sit in "
                        "parallel with these tracks, so this drop is an UPPER "
                        "bound" % (len(pour), net))
    if pour and model_zones and zone_summary is None:
        warnings.append("%d copper zone(s) on net %r exist but none of them could "
                        "be gridded, so this drop is still an UPPER bound"
                        % (len(pour), net))

    max_drop = max(s["drop_mv"] for s in sink_out)
    worst_sink = max(sink_out, key=lambda s: s["drop_mv"])
    loss = sum(e["power_mw"] for e in edge_out) / 1e3

    # ---- AC bookkeeping (0.0.70). Every key below is added ONLY when a
    # frequency was given, so the DC answer keeps its exact shape too. The sums
    # are sums over segments ("how much copper does this net present at f"), not
    # the network resistance, and they are named that way.
    val = {"net": net,
           "source": source,
           "total_current_a": round(total_a, 9),
           "max_drop_mv": round(max_drop, 6),
           "worst_sink": worst_sink["pad"],
           # `max_drop` is millivolts; dividing it by 3.3 V and multiplying by 100
           # reported 2.94 mV as 89.19 % of 3.3 V for a year of releases - the
           # factor is 1000, and nothing in this module ever checked the two
           # fields against each other. The test `pct_agrees_with_mv` is that check.
           "worst_sink_drop_pct_of_3v3": round(100.0 * (max_drop / 1000.0) / 3.3, 6),
           "sinks": sink_out,
           "total_copper_loss_w": round(loss, 9),
           "worst_current_density": (worst_j or {}),
           "edges": sorted(edge_out, key=lambda r: -r["current_a"])[:40],
           "edge_count": len(edge_out),
           "node_count": len(names),
           "quantize_mm": q}
    assumptions = {"quantize_mm": q, "temp_c": float(temp_c),
                   "copper_um": explicit,
                   "copper_um_source": ("explicit" if explicit is not None
                                        else ("stackup" if cu else None)),
                   "plating_um": plating_um,
                   "net": net, "board_thickness_mm": board_th}
    ac_basis = ""
    ac_note = ""
    if freq is not None:
        depths = [v[1] for v in _slab_cache.values() if v]
        depths += [v[1] for v in _barrel_cache.values() if v]
        summary = {}
        max_ratio = 1.0
        for kind in ("track", "zone", "via"):
            rows = ac_acc[kind]
            if not rows:
                summary[kind] = {"segments": 0}
                continue
            ratios = [x[2] for x in rows]
            max_ratio = max(max_ratio, max(ratios))
            summary[kind] = {"segments": len(rows),
                             "sum_r_dc_ohm": round(sum(x[0] for x in rows), 9),
                             "sum_r_ac_ohm": round(sum(x[1] for x in rows), 9),
                             "ratio_min": round(min(ratios), 9),
                             "ratio_max": round(max(ratios), 9)}
        val["frequency_hz"] = freq
        val["ac_faces"] = faces
        val["ac_skin_depth_um"] = round(min(depths), 9) if depths else None
        val["ac_model"] = ("1-D exact skin solve per segment: a track or a pour "
                           "square keeps its width and gets an effective thickness "
                           "t_eff(f), a via barrel is solved as the tube it is, and "
                           "only resistances change - the topology and the DC path "
                           "do not. sum_r_* are sums over segments, not the network "
                           "resistance. No proximity effect, no lateral (edge) "
                           "crowding along a wide strip, no phase")
        val["ac_summary"] = summary
        ac_basis = (", and at %.6g Hz each of those resistances is multiplied by "
                    "the ratio its own exact 1-D solve gives - a track or a pour "
                    "square by the slab result (t becomes t_eff(f)), a barrel by "
                    "the Bessel tube result; the DC topology is unchanged"
                    % freq)
        assumptions["frequency_hz"] = freq
        assumptions["ac_faces"] = faces
        assumptions["mu_r"] = mur
        assumptions["roughness_rq_um"] = rq
        ac_note = ("a ONE-FREQUENCY answer at %.6g Hz (faces=%r): the skin effect "
                   "IS in it, but only through the THICKNESS of each conductor - "
                   "there is no proximity effect, no lateral crowding along a wide "
                   "strip, no connector contact and no phase, so the real |Z| at "
                   "this frequency is worse than this number. A current that has "
                   "both a DC part and a ripple needs two calls: the copper LOSS "
                   "adds (I_dc^2*R_dc + I_ac^2*R_ac), a voltage drop does not"
                   % (freq, faces))
        if worst_j:
            warnings.append(
                "current_density_a_per_mm2 stays the DC average over the PHYSICAL "
                "cross-section; at %.6g Hz the current rides near the surface, so "
                "the local peak density is up to %.3g times higher on this net "
                "(that is what r_ac_over_rdc in ac_summary says)" % (freq, max_ratio))

    if zone_summary:
        nc = ["pad and plane spreading resistance at the contact",
              "thermal-relief spokes on a pad joined to the pour "
              "(modelled as solid copper)",
              "temperature rise feedback (rho uses temp_c)",
              "current capacity / fusing (needs a "
              "temperature-rise criterion, not a current)"]
    else:
        nc = ["copper pour / plane in parallel",
              "pad and plane spreading resistance",
              "temperature rise feedback (rho uses temp_c)",
              "current capacity / fusing (needs a "
              "temperature-rise criterion, not a current)"]
    if freq is None:
        nc.insert(0 if zone_summary else 1, "skin effect")
    else:
        nc.append("proximity effect (eddy currents in a neighbouring trace, or "
                  "the return plane's own eddy currents)")
        nc.append("lateral current crowding: a wide strip's current piles up at "
                  "the edges, which a 1-D thickness solve cannot see")

    if freq is None:
        prec = ("a DC answer, and a bound in the direction of missing copper: "
                "skin effect, pad/plane spreading and connector contact are not "
                "in it (so the real drop is worse)")
    else:
        prec = ac_note
    prec = (prec
            + (". The pour IS in it, as a sheet gridded at the pitch "
               "in zone_summary - the sheet resistance does not depend "
               "on that pitch, the current spreading near a contact "
               "does. A pad joined to the pour is modelled as SOLID "
               "copper at %.0e ohm, so where a thermal-relief spoke "
               "is the real connection this drop is a LOWER bound. "
               "Zone priority, minimum width and the clearance ring to "
               "other nets are not solved for, they are taken from the "
               "fill the file already carries"
               % _ZF_LINK_OHM if zone_summary else
               ", while a pour on the same net is not in it either "
               "(so this drop is an upper bound)")
            + ". Nodes closer than quantize_mm are merged, so a hairline gap "
              "below that is invisible")

    basis = ("every track segment becomes rho(T)*L/(w*t) with t from the "
             "stackup copper weight, every via a barrel of "
             "pi*((d/2)^2 - (d/2-p)^2), plated pads join their layers"
             + (", and every copper pour on this net is a 2-D sheet of "
                "rho(T)/t per square gridded and four-connected, with the "
                "pitch stated per layer" if zone_summary else "")
             + ac_basis
             + "; the graph is solved by nodal analysis with the source "
               "pad at 0 V"
             + (" (conjugate gradients, residual reported)"
                if zone_summary else ""))

    return ok(value=val,
              unit="mV",
              model=("track_via_pad_zone_resistor_graph" if zone_summary
                     else "track_via_pad_resistor_graph"),
              basis=basis,
              precision=prec,
              assumptions=assumptions,
              extra={"warnings": warnings,
                     "zone_summary": zone_summary,
                     "solver": solver_info,
                     "not_computed": nc})

# ------------------------------------------------------------------------ DFM
# Design-for-manufacture: measure the facts a fab capability document talks
# about, and hand them to calc/dfm.py. The limits never come from here - this
# half of the job is only "what does the board actually carry".


def _hole_list(board):
    """Every drilled hole on the board, vias and pads together.

    One list on purpose: a fab's hole-to-hole rule does not care whether the two
    holes are vias, through-hole pads or mounting holes.
    """
    holes = []
    for via in board.vias:
        drill = via.get("drill")
        if drill is None:
            continue
        dia = via.get("diameter") or drill
        holes.append({"kind": "via",
                      "ref": "via@(%.3f,%.3f)" % (via["x"], via["y"]),
                      "x": float(via["x"]), "y": float(via["y"]),
                      "drill_mm": float(drill), "diameter_mm": float(dia),
                      "net": via.get("net"), "uuid": via.get("uuid")})
    for fp in board.footprints:
        for pad in fp.get("pads") or []:
            hole = pad.get("hole_mm")
            if hole is None:
                continue
            ref = "%s.%s" % (fp.get("ref") or "?", pad.get("number") or "?")
            holes.append({"kind": "pad", "ref": ref,
                          "x": float(pad["x"]), "y": float(pad["y"]),
                          "drill_mm": float(hole),
                          "diameter_mm": min(float(pad.get("w") or 0.0),
                                             float(pad.get("h") or 0.0)),
                          "net": pad.get("net"), "uuid": pad.get("uuid"),
                          # The pad that OWNS the hole is the annular ring, which is
                          # a different rule; kept here so the copper-to-hole sweep
                          # can leave it out without relying on net matching.
                          "owner_ref": ref})
    return holes

def _hole_web(holes, window_mm=5.0):
    """Smallest wall-to-wall gap between two holes, or a proven lower bound.

    Bucketed so a 2000-pad BGA does not become 2 million comparisons, but the
    answer stays exact whenever it matters: every pair whose gap is below
    `window_mm` is guaranteed to be compared (the buckets are walked with a
    one-cell halo, and the radius subtraction is folded into the test), so a
    found gap smaller than the window IS the global minimum. If nothing is
    found the caller is told "at least window_mm", not a number.
    """
    if len(holes) < 2:
        return {"not_applicable": "fewer than two drilled holes on this board"}
    cell = float(window_mm)
    grid = {}
    for idx, h in enumerate(holes):
        key = (int(math.floor(h["x"] / cell)), int(math.floor(h["y"] / cell)))
        grid.setdefault(key, []).append(idx)
    best = None
    seen = set()
    for (gx, gy), bucket in grid.items():
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                other = grid.get((gx + dx, gy + dy))
                if not other:
                    continue
                for i in bucket:
                    for j in other:
                        if i >= j:
                            continue
                        pair = (i, j)
                        if pair in seen:
                            continue
                        seen.add(pair)
                        a, b = holes[i], holes[j]
                        gap = (math.hypot(a["x"] - b["x"], a["y"] - b["y"])
                               - a["drill_mm"] / 2.0 - b["drill_mm"] / 2.0)
                        if gap <= window_mm and (best is None or gap < best[0]):
                            best = (gap, a, b)
    if best is None:
        return {"lower_bound": float(window_mm),
                "where": "no two holes are within %.2f mm of each other" % window_mm}
    return {"measured": max(0.0, best[0]),
            "where": "%s and %s (drill %.3f / %.3f mm)"
                     % (best[1]["ref"], best[2]["ref"],
                        best[1]["drill_mm"], best[2]["drill_mm"])}

def _edge_segments(board):
    """Board outline and cut-out contours as plain segments (arcs are already polylines)."""
    segs = []
    for kind, contours in (("outline", board.outline or []),
                           ("cutout", board.cutouts or [])):
        for contour in contours:
            n = len(contour)
            if n < 2:
                continue
            for i in range(n):
                a, b = contour[i], contour[(i + 1) % n]
                if a == b:
                    continue
                segs.append((tuple(a), tuple(b), kind))
    for chain in board.outline_open or []:
        n = len(chain)
        for i in range(n - 1):
            segs.append((tuple(chain[i]), tuple(chain[i + 1]), "outline_open"))
    return segs

def _dist_item_to_edge(item, a, b):
    if "poly" in item:
        return G.dist_polygon_polygon(item["poly"], [a, b])
    p1, p2, w = item["seg"]
    return max(0.0, G.dist_segment_segment(p1, p2, a, b) - w / 2.0)

def _min_copper_to_edge(board, items, segs, window_mm=5.0):
    if not segs:
        return {"why": "the board has no closed outline (or only open chains), so "
                       "there is no edge to measure against"}
    cell = float(window_mm)
    grid = {}
    for idx, (a, b, _kind) in enumerate(segs):
        x0, y0 = min(a[0], b[0]), min(a[1], b[1])
        x1, y1 = max(a[0], b[0]), max(a[1], b[1])
        for gx in range(int(math.floor(x0 / cell)), int(math.floor(x1 / cell)) + 1):
            for gy in range(int(math.floor(y0 / cell)), int(math.floor(y1 / cell)) + 1):
                grid.setdefault((gx, gy), []).append(idx)
    best = None
    for item in items:
        x0, y0, x1, y1 = _item_bbox(item)
        cand = set()
        for gx in range(int(math.floor((x0 - cell) / cell)),
                        int(math.floor((x1 + cell) / cell)) + 1):
            for gy in range(int(math.floor((y0 - cell) / cell)),
                            int(math.floor((y1 + cell) / cell)) + 1):
                cand.update(grid.get((gx, gy)) or ())
        for idx in cand:
            a, b, _kind = segs[idx]
            d = _dist_item_to_edge(item, a, b)
            if d <= window_mm and (best is None or d < best[0]):
                best = (d, item["ref"], idx)
    if best is None:
        return {"lower_bound": float(window_mm),
                "where": "no copper is within %.2f mm of a board edge" % window_mm}
    a, b, kind = segs[best[2]]
    return {"measured": max(0.0, best[0]),
            "where": "%s vs %s edge (%.3f,%.3f)-(%.3f,%.3f)"
                     % (best[1], kind, a[0], a[1], b[0], b[1])}

def _dist_item_to_point(item, pt):
    """Edge distance from a copper item to a POINT (a hole centre)."""
    if "poly" in item:
        return G.dist_point_polygon(pt, item["poly"])
    a, b, w = item["seg"]
    return max(0.0, G.dist_point_segment(pt, a, b) - w / 2.0)


def _silk_items(board):
    """Every silkscreen item, board-level and footprint-level, with its owner."""
    out = []
    for it in board.silk or []:
        item = dict(it)
        item.setdefault("ref", None)
        out.append(item)
    for fp in board.footprints:
        for it in fp.get("silk") or []:
            item = dict(it)
            item["ref"] = fp.get("ref") or "?"
            out.append(item)
    return out


def _silk_facts(board):
    """Narrowest silk stroke and smallest silk character height, both measured.

    An item that does not state a width is EXCLUDED and counted, never defaulted:
    KiCad's fallback belongs to the program, and inventing 0.15 mm for it would
    turn "I could not read this" into "I checked it".
    """
    items = _silk_items(board)
    facts = {}
    if not items:
        facts["min_silk_line_width"] = {"not_applicable":
                                        "no silkscreen item on this board"}
        facts["min_silk_text_height"] = {"not_applicable":
                                         "no silkscreen item on this board"}
        return facts

    def _num(v):
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) \
            and v > 0 else None

    widths = [(float(i["width_mm"]), i) for i in items if _num(i.get("width_mm"))]
    unknown_w = [i for i in items if not _num(i.get("width_mm"))]
    if widths:
        w, item = min(widths, key=lambda t: t[0])
        where = "%s %s on %s" % (item.get("ref") or "board-level",
                                 item.get("kind"), item.get("layer"))
        if item.get("kind") in ("fp_text", "gr_text"):
            where += " (%r)" % (item.get("text") or "")
        if unknown_w:
            where += ("; %d other silk item(s) state no line width and are excluded, "
                      "so the true minimum may be smaller" % len(unknown_w))
        facts["min_silk_line_width"] = {"measured": w, "where": where}
    else:
        facts["min_silk_line_width"] = {
            "why": "no silkscreen item states a line width (%d item(s) present, none "
                   "with a width)" % len(items)}

    texts = [i for i in items if i.get("kind") in ("fp_text", "gr_text")]
    heights = [(float(i["height_mm"]), i) for i in texts if _num(i.get("height_mm"))]
    unknown_h = [i for i in texts if not _num(i.get("height_mm"))]
    if heights:
        h, item = min(heights, key=lambda t: t[0])
        where = "%s silk text %r on %s" % (item.get("ref") or "board-level",
                                           item.get("text") or "", item.get("layer"))
        if unknown_h:
            where += ("; %d other text(s) state no size and are excluded"
                      % len(unknown_h))
        facts["min_silk_text_height"] = {"measured": h, "where": where}
    elif texts:
        facts["min_silk_text_height"] = {
            "why": "%d silkscreen text(s) present, none states a character size"
                   % len(texts)}
    else:
        facts["min_silk_text_height"] = {"not_applicable":
                                         "no silkscreen text on this board"}
    return facts


def _mask_apertures(board):
    """Every solder-mask opening, one per (item, side).

    A pad opening is the pad's own outline grown by its expansion; an opening drawn
    as a graphic is the drawn polygon (KiCad applies the pad expansion to pads only).
    Both sides are kept apart: a front opening and a back one are separated by the
    board thickness, not by a mask web.
    """
    from .adapters.canonical import pad_points
    out = []
    for fp in board.footprints:
        for pad in fp.get("pads") or []:
            sides = pad.get("mask_layers") or []
            if not sides:
                continue
            pts = pad_points(pad)
            if not pts:
                continue
            for side in sides:
                out.append({"layer": side, "poly": pts,
                            "margin_mm": float(pad.get("mask_margin") or 0.0),
                            "margin_source": pad.get("mask_margin_source"),
                            "ref": "%s.%s" % (fp.get("ref") or "?",
                                              pad.get("number") or "?")})
        for g in fp.get("mask") or []:
            out.append({"layer": g["layer"], "poly": g["poly"],
                        "margin_mm": float(g.get("margin_mm") or 0.0),
                        "margin_source": g.get("margin_source") or "graphic",
                        "ref": "%s.mask" % (fp.get("ref") or "?")})
    for g in board.mask_apertures or []:
        out.append({"layer": g["layer"], "poly": g["poly"],
                    "margin_mm": float(g.get("margin_mm") or 0.0),
                    "margin_source": g.get("margin_source") or "graphic",
                    "ref": "board mask graphic"})
    return out


def _mask_web(apertures, window_mm=5.0):
    """Smallest strip of mask between two openings on ONE side, or a bound.

    An opening is its item grown by the expansion the file states, so the web
    between two of them is (copper gap) - (margin a) - (margin b): growing a shape
    by a disc of radius r moves its boundary out by exactly r, so no polygon has to
    be offset (and no offset polygon is approximated). Pairs whose web is not
    positive are MERGED openings - no mask between them at all - and are counted
    rather than reported as a zero-width web, because "no web" is not "a web too
    thin to make".
    """
    by_layer = {}
    for a in apertures:
        by_layer.setdefault(a["layer"], []).append(a)
    best = None
    merged = 0
    for side in sorted(by_layer):
        items = by_layer[side]
        if len(items) < 2:
            continue
        reach = float(window_mm) + 2.0 * max(float(i.get("margin_mm") or 0.0)
                                             for i in items)
        rows = sorted(((G.bbox_of(i["poly"]), idx) for idx, i in enumerate(items)),
                      key=lambda t: t[0][0])
        for k, (bb, i) in enumerate(rows):
            for bb2, j in rows[k + 1:]:
                if bb2[0] - bb[2] > reach:
                    break
                if bb2[1] - bb[3] > reach or bb[1] - bb2[3] > reach:
                    continue
                a1, a2 = items[i], items[j]
                web = (G.dist_polygon_polygon(a1["poly"], a2["poly"])
                       - float(a1.get("margin_mm") or 0.0)
                       - float(a2.get("margin_mm") or 0.0))
                if web <= 1e-9:
                    merged += 1
                    continue
                if web <= window_mm and (best is None or web < best[0]):
                    best = (web, a1, a2, side)
    if best is None:
        if merged:
            return {"not_applicable":
                    "every pair of neighbouring openings on the same side merges "
                    "(%d pair(s)), so there is no mask web anywhere" % merged}
        if not any(len(v) >= 2 for v in by_layer.values()):
            return {"not_applicable":
                    "fewer than two solder-mask openings on any one side"}
        return {"lower_bound": float(window_mm),
                "where": "no two openings on one side are within %.2f mm of each other"
                         % window_mm}
    w, a1, a2, side = best
    # Rounded to 1e-6 mm (a nanometre): polygon distances carry float noise, and
    # 0.2999999999999986 against a 0.3 limit is a FAIL on an exact comparison -
    # a verdict decided by the last bit of a double, not by the board.
    return {"measured": round(float(w), 6),
            "where": "%s vs %s on %s: copper gap %.4f - %.4f - %.4f mm%s"
                     % (a1["ref"], a2["ref"], side,
                        w + float(a1.get("margin_mm") or 0.0)
                        + float(a2.get("margin_mm") or 0.0),
                        float(a1.get("margin_mm") or 0.0),
                        float(a2.get("margin_mm") or 0.0),
                        ("; %d pair(s) merge" % merged) if merged else "")}


def _mask_defined_pads(board):
    """Pads whose solder-mask opening is SMALLER than the copper it opens.

    A negative mask margin shrinks the opening inside the pad, so the exposed
    area is decided by the mask rather than by the copper - a mask-defined pad.
    Both numbers a capability document can talk about come out of ONE pass,
    because the two ways a fab refuses this are different questions: some state
    "no mask-defined pads" (a count of 0), others state the overlap they can hold
    (a minimum). A pad is counted ONCE even when it is masked on both sides: it is
    one pad, its two openings share one margin, and counting sides would make the
    limit depend on whether the part happens to be through-hole.

    Where the margin came from is carried along (`pad` / `footprint` / `setup` /
    `absent_treated_as_zero`), because a negative number that came from a file
    default is a different thing to argue with a fab about than one typed on a
    pad.
    """
    pads = []
    for fp in board.footprints or []:
        for pad in fp.get("pads") or []:
            if not (pad.get("mask_layers") or []):
                continue
            m = float(pad.get("mask_margin") or 0.0)
            if not (m < 0.0):
                continue
            pads.append({"ref": "%s.%s" % (fp.get("ref") or "?",
                                           pad.get("number") or "?"),
                         "overlap_mm": round(-m, 6),
                         "margin_source": pad.get("mask_margin_source") or "?"})
    if not pads:
        return ({"measured": 0.0,
                 "where": "no pad states a negative mask margin - every opening is "
                          "at least as large as the copper under it",
                 "pads": []},
                {"not_applicable":
                 "no pad has its solder-mask opening smaller than its copper, so "
                 "there is no overlap to measure"})
    pads.sort(key=lambda p: p["overlap_mm"])
    sources = {}
    for p in pads:
        sources[p["margin_source"]] = sources.get(p["margin_source"], 0) + 1
    count = {"measured": float(len(pads)),
             "where": "%d pad(s) with a negative mask margin; margin came from: %s"
                      % (len(pads), ", ".join("%s x%d" % (k, v)
                                              for k, v in sorted(sources.items()))),
             "pads": pads[:40],
             "list_truncated": len(pads) > 40,
             "widest_overlap_mm": pads[-1]["overlap_mm"]}
    least = pads[0]
    overlap = {"measured": least["overlap_mm"],
               "where": "smallest overlap %s mm at %s (mask margin -%s mm, from %s)"
                        % (least["overlap_mm"], least["ref"],
                           least["overlap_mm"], least["margin_source"]),
               "pads": pads[:40]}
    return count, overlap

def _copper_to_hole(items, holes, plating_mm, window_mm=5.0):
    """Another net's copper to the FINISHED hole wall: distance - drill/2 - plating.

    The pad that OWNS a hole is left out: its copper is the annular ring, which is
    a different rule (and is still measured to the drill wall - see the precision
    note). Same-net copper is left out too, because a via's own trace touching its
    pad is a connection, not a spacing.
    """
    if not holes:
        return {"not_applicable": "no drilled hole on this board"}
    if not items:
        return {"not_applicable": "no copper on this board"}
    reach = float(window_mm)
    best = None
    for h in holes:
        cx, cy = h["x"], h["y"]
        radius = h["drill_mm"] / 2.0
        owner = (h.get("owner_ref") or "").split("#")[0]
        hole_net = h.get("net")
        for item in items:
            x0, y0, x1, y1 = _item_bbox(item)
            if (x0 - reach > cx or cx > x1 + reach
                    or y0 - reach > cy or cy > y1 + reach):
                continue
            if item["ref"] == h["ref"] or item["ref"].split("#")[0] == owner:
                continue
            inet = item.get("net")
            if inet is not None and hole_net is not None and inet == hole_net:
                continue
            d = _dist_item_to_point(item, (cx, cy)) - radius - float(plating_mm)
            if d <= window_mm and (best is None or d < best[0]):
                best = (d, h, item)
    if best is None:
        return {"lower_bound": float(window_mm),
                "where": "no other-net copper is within %.2f mm of a finished hole "
                         "wall" % window_mm}
    d, h, item = best
    return {"measured": round(max(0.0, d), 6),
            "where": "%s vs %s hole (drill %.3f, plating %.1f um)"
                     % (item["ref"], h["ref"], h["drill_mm"],
                        float(plating_mm) * 1000.0)}


def _polygon_throat(points):
    """Narrowest distance across a simple polygon: min over vertices of the
    distance to a non-adjacent edge.

    This is the "slot width" a fab cares about. It is exact for a rectangle
    (both short edges give the width) and conservative-to-the-point for a
    curved slot, because the contour is already a polyline.
    """
    n = len(points)
    if n < 3:
        return None
    best = None
    for i, p in enumerate(points):
        for j in range(n):
            if j in ((i - 1) % n, i, (i + 1) % n):
                continue
            d = G.dist_point_segment(p, points[j], points[(j + 1) % n])
            if d > 1e-9 and (best is None or d < best):
                best = d
    return best

def _dfm_facts(board, rules, layer=None, window_mm=5.0, check_clearance=True,
               plating_mm=None):
    """Measure the facts a capability document can talk about.

    Each fact is `measured` (a number, or a proven bound) or explains why it is
    not: `not_applicable` when the board has no such feature, `why` when this
    server cannot measure it.

    `plating_mm` is the fab's hole plating build-up, taken from the capability
    document (`plating_um`). None means the document did not state it, and then
    copper-to-finished-hole is refused instead of being measured to the drill
    wall - the two differ by exactly this number.
    """
    facts = {}

    # -- narrowest track ---------------------------------------------------
    tracks = [t for t in board.tracks if t.get("width")]
    if layer:
        tracks = [t for t in tracks if t.get("layer") == layer]
    if tracks:
        t = min(tracks, key=lambda x: float(x["width"]))
        facts["narrowest_track"] = {
            "measured": float(t["width"]),
            "where": "%s track on %s" % ("%.4g mm wide" % float(t["width"]),
                                         t.get("layer"))}
    else:
        facts["narrowest_track"] = {"not_applicable": "no copper track on this board"}

    # -- copper-to-copper gap ---------------------------------------------
    if "min_clearance_mm" not in rules:
        facts["min_copper_gap"] = {"why": "the capability document does not state "
                                          "min_clearance_mm, so the (expensive) "
                                          "pairwise sweep was skipped"}
    elif not check_clearance:
        facts["min_copper_gap"] = {"why": "check_clearance=False; the pairwise sweep "
                                          "was not run"}
    else:
        limit = rules["min_clearance_mm"]
        res = clearance(board, threshold_mm=max(limit, 1e-3), layer=layer)
        if res.get("status") != "ok":
            facts["min_copper_gap"] = {"why": res.get("message") or "clearance failed"}
        else:
            val = res["value"]
            gap = val.get("min_gap_mm")
            cell = (res.get("assumptions") or {}).get("cell_mm") or 0.0
            if gap is None:
                facts["min_copper_gap"] = {
                    "not_applicable": "fewer than two comparable copper items on the "
                                      "requested layer"}
            elif cell and gap >= cell:
                facts["min_copper_gap"] = {
                    "lower_bound": float(cell),
                    "where": "no pair closer than the %.2f mm search window" % cell}
            else:
                btw = val.get("min_gap_between") or {}
                facts["min_copper_gap"] = {
                    "measured": float(gap),
                    "where": "%s vs %s (%d pair(s) below %.3f mm)"
                             % (btw.get("a"), btw.get("b"), val.get("violations", 0),
                                limit)}

    # -- holes -------------------------------------------------------------
    holes = _hole_list(board)
    if holes:
        vias = [h for h in holes if h["kind"] == "via"]
        if vias:
            v = min(vias, key=lambda h: h["drill_mm"])
            facts["min_via_drill"] = {"measured": v["drill_mm"],
                                      "where": "%s drill" % v["ref"]}
            h = min(vias, key=lambda x: (x["diameter_mm"] - x["drill_mm"]) / 2.0)
            raw = (h["diameter_mm"] - h["drill_mm"]) / 2.0
            facts["min_via_ring"] = {
                "measured": max(0.0, raw),
                "where": "%s: (%.3f - %.3f) / 2%s"
                         % (h["ref"], h["diameter_mm"], h["drill_mm"],
                            "" if raw >= 0 else " = NEGATIVE (drill wider than copper)")}
        else:
            facts["min_via_drill"] = {"not_applicable": "no via on this board"}
            facts["min_via_ring"] = {"not_applicable": "no via on this board"}
        pads = [h for h in holes if h["kind"] == "pad"]
        if pads:
            p = min(pads, key=lambda h: (h["diameter_mm"] - h["drill_mm"]) / 2.0)
            raw = (p["diameter_mm"] - p["drill_mm"]) / 2.0
            facts["min_pad_ring"] = {
                "measured": max(0.0, raw),
                "where": "%s: (%.3f - %.3f) / 2%s"
                         % (p["ref"], p["diameter_mm"], p["drill_mm"],
                            "" if raw >= 0 else " = NEGATIVE (drill wider than copper)")}
        else:
            facts["min_pad_ring"] = {"not_applicable":
                                     "no plated through-hole pad on this board"}
        facts["min_hole_web"] = _hole_web(holes, window_mm=window_mm)
    else:
        for k in ("min_via_drill", "min_via_ring", "min_pad_ring", "min_hole_web"):
            facts[k] = {"not_applicable": "no drilled hole on this board"}

    # -- copper to board edge ---------------------------------------------
    # Copper only: a footprint's silkscreen outline is an item too, and counting
    # silk as copper would invent a copper-to-edge violation nobody has.
    items = [it for it in _items(board)
             if any(str(l).endswith(".Cu") for l in (it.get("layers") or []))]
    facts["min_copper_to_edge"] = _min_copper_to_edge(board, items,
                                                      _edge_segments(board),
                                                      window_mm=window_mm)

    # -- internal cut-outs --------------------------------------------------
    cutouts = [c for c in (board.cutouts or []) if len(c) >= 3]
    if cutouts:
        widths = [(w, i) for i, c in enumerate(cutouts)
                  for w in [_polygon_throat(c)] if w]
        if widths:
            w, i = min(widths)
            facts["min_slot_width"] = {"measured": w,
                                       "where": "cut-out #%d (%d vertices)"
                                                % (i, len(cutouts[i]))}
        else:
            facts["min_slot_width"] = {"why": "no cut-out contour has enough vertices "
                                              "to measure a throat"}
    else:
        facts["min_slot_width"] = {"not_applicable": "no internal cut-out on this board"}

    # -- board size --------------------------------------------------------
    outline = [p for c in (board.outline or []) for p in c]
    if not outline:
        dims = None
    else:
        bbox = G.bbox_of(outline)
        dims = (bbox[2] - bbox[0], bbox[3] - bbox[1])
    if dims:
        facts["board_width"] = {"measured": dims[0],
                                "where": "outline bbox x extent"}
        facts["board_height"] = {"measured": dims[1],
                                 "where": "outline bbox y extent"}
    else:
        why = {"why": "no closed board outline, so there is no width to measure"}
        facts["board_width"] = dict(why)
        facts["board_height"] = dict(why)

    # -- copper layer count ------------------------------------------------
    cu = set()
    for it in items:
        for lname in it.get("layers") or []:
            if str(lname).endswith(".Cu"):
                cu.add(str(lname))
    if cu:
        facts["copper_layers"] = {"measured": float(len(cu)),
                                  "where": "distinct copper layers carrying copper: %s"
                                           % ", ".join(sorted(cu))}
    else:
        facts["copper_layers"] = {"why": "no copper item names a copper layer"}

    # -- silkscreen --------------------------------------------------------
    # Not copper, so never part of `items`; a separate measurement with its own
    # two limits (stroke width, character height).
    facts.update(_silk_facts(board))

    # -- solder-mask web ---------------------------------------------------
    if "min_mask_web_mm" not in rules:
        facts["min_mask_web"] = {"why": "the capability document does not state "
                                        "min_mask_web_mm, so the aperture sweep "
                                        "was skipped"}
    else:
        facts["min_mask_web"] = _mask_web(_mask_apertures(board),
                                          window_mm=window_mm)

    # -- mask-defined pads (an opening SMALLER than its copper) -------------
    # Gated on the rules the document states, like the mask web above: a sweep
    # nobody asked for is not a measurement, and its absence must read as
    # "not stated", not as "none found".
    if not [k for k in ("max_mask_defined_pads", "min_mask_defined_overlap_mm")
            if k in rules]:
        why = {"why": "the capability document states neither max_mask_defined_pads "
                      "nor min_mask_defined_overlap_mm, so the pads' mask margins "
                      "were not examined"}
        facts["mask_defined_pads"] = dict(why)
        facts["min_mask_defined_overlap"] = dict(why)
    else:
        (facts["mask_defined_pads"],
         facts["min_mask_defined_overlap"]) = _mask_defined_pads(board)

    # -- copper to the finished hole wall ----------------------------------
    if "min_copper_to_hole_mm" not in rules:
        facts["min_copper_to_hole"] = {"why": "the capability document does not "
                                              "state min_copper_to_hole_mm, so the "
                                              "hole-wall distances were not measured"}
    elif plating_mm is None:
        facts["min_copper_to_hole"] = {
            "why": "the capability document states no `plating_um`, so the finished "
                   "hole wall is unknown; measuring to the drill wall would answer a "
                   "different question"}
    else:
        facts["min_copper_to_hole"] = _copper_to_hole(items, holes, plating_mm,
                                                      window_mm=window_mm)
    return facts

def dfm_check(board, capability, layer=None, window_mm=5.0, check_clearance=True):
    """Measure this board and compare it against a SUPPPLIED capability document.

    The limits are the document's; every measurement is this board's; nothing is
    defaulted. Rules the document does not state are listed as unanswered, and
    rules this server cannot measure come back `not_checked` with the reason.

    `plating_um` in the document is the fab's hole plating build-up; it is a
    process figure that decides `min_copper_to_hole_mm`, so it is read from the
    same place as the limits and never assumed.
    """
    from .calc import dfm as _dfm
    rules, e = _dfm.read_capability(capability)
    if e:
        return e
    plating_mm, plating_err = _dfm.read_plating(capability)
    if plating_err:
        return plating_err
    facts = _dfm_facts(board, rules, layer=layer, window_mm=float(window_mm),
                       check_clearance=bool(check_clearance),
                       plating_mm=plating_mm)
    out = _dfm.evaluate(capability, facts)
    if out.get("status") == "ok":
        out["value"]["facts"] = {k: v for k, v in sorted(facts.items())}
        out["value"]["search_window_mm"] = float(window_mm)
    return out

# ------------------------------------------------------------- vibration
def board_vibration(board, mount="corners", material="fr4", thickness_mm=None,
                    n_modes=4, poly_degree=7, probe="components"):
    """Natural frequencies of THIS board, and where on it each component sits.

    The plate is the board's outline bounding box; the thickness is the stated
    board thickness or the stackup sum. The mode shapes are then sampled at the
    component positions, so the answer to "which part gets shaken hardest" is a
    number per reference designator rather than a picture.

    Two approximations are named in the envelope and are large enough to matter:
      * a non-rectangular outline is replaced by its bounding box, which is
        bigger and therefore softer - f1 comes out LOW;
      * component mass (which adds inertia and lowers f) and copper (which adds
        stiffness and raises it) are both ignored.
    """
    from .calc import vibration as _vib

    polys = [p for p in (board.outline or []) if p and len(p) >= 3]
    if not polys:
        return err("the board has no closed outline", "OUTLINE_MISSING",
                   hint="natural frequency needs a plate - there is no bbox to "
                        "measure without a closed edge contour")
    ring = polys[0]
    x0, y0, x1, y1 = G.bbox_of(ring)
    a_mm = abs(x1 - x0)
    b_mm = abs(y1 - y0)
    if a_mm <= 0.0 or b_mm <= 0.0:
        return err("the outline bbox has zero size", "OUTLINE_INVALID")

    poly_area = abs(G.polygon_area(ring))
    bbox_area = a_mm * b_mm
    fill = poly_area / bbox_area if bbox_area > 0 else 0.0

    th = _stack_thickness(board) if thickness_mm is None else None
    try:
        th = float(thickness_mm) if thickness_mm is not None else th
    except (TypeError, ValueError):
        return err("thickness_mm must be a number", "ARG_INVALID")
    if th is None or th <= 0.0:
        return err("board thickness unknown", "ARG_REQUIRED",
                   hint="the stackup carries no total thickness and no "
                        "thickness_mm was given; f ~ h, so this cannot be defaulted")

    # probe points: component origins, in bbox-relative plate coordinates
    probes = []
    if probe == "components":
        for fp in board.footprints or []:
            try:
                px = float(fp.get("x"))
                py = float(fp.get("y"))
            except (TypeError, ValueError):
                continue
            if not (x0 <= px <= x1 and y0 <= py <= y1):
                continue
            probes.append({"name": str(fp.get("ref") or "?"),
                           "x_mm": px - x0, "y_mm": py - y0})
    elif isinstance(probe, list):
        for pp in probe:
            probes.append(pp)
    elif probe is not None:
        return err("probe must be 'components' or a list of {name,x_mm,y_mm}",
                   "ARG_INVALID")

    res = _vib.plate_modes(a_mm, b_mm, th, material=material, mount=mount,
                           n_modes=n_modes, poly_degree=poly_degree,
                           probe_points=probes or None)
    if res.get("status") != "ok":
        return res
    val = res["value"]

    # re-origin the reported geometry into board coordinates
    modes = []
    for m in val["modes"]:
        m2 = dict(m)
        if "antinode_mm" in m2:
            m2["antinode_board_mm"] = [_sig2(m2["antinode_mm"][0] + x0),
                                       _sig2(m2["antinode_mm"][1] + y0)]
        modes.append(m2)

    warnings = list(val.get("warnings") or [])
    # ---- the same plate on the REAL outline (the bbox answer stays the primary
    # one so nothing that already worked changes); this is where the bbox bias
    # stops being an unquantified worry and becomes a second number.
    on_ring = None
    ring_hot = []
    if fill < 0.99:
        from .calc import vibration_poly as _vpb
        try:
            pr = _vpb.plate_modes_polygon(
                ring, th, material=material, mount=mount,
                poly_degree=max(8, int(poly_degree)), n_modes=n_modes,
                probe_points=([{"name": p["name"], "x_mm": p["x_mm"] + x0,
                                "y_mm": p["y_mm"] + y0} for p in probes]
                              or None))
        except Exception as exc:            # never lose the bbox answer
            pr = {"status": "error", "reason_code": "INTERNAL",
                  "message": str(exc)}
        if pr.get("status") == "ok":
            pv = pr["value"]
            f_bbox = val["modes"][0]["freq_hz"] if val["modes"] else None
            f_ring = pv["modes"][0]["freq_hz"] if pv["modes"] else None
            ratio = (f_ring / f_bbox) if (f_bbox and f_ring) else None
            on_ring = {
                "f1_hz": f_ring,
                "ratio_to_bbox": (_sig2(ratio) if ratio is not None else None),
                "modes": pv["modes"],
                "alpha_spread_pct": pv["basis"]["alpha_spread_pct"],
                "alpha_converged": pv["basis"]["alpha_converged"],
                "boundary_rms_mm": pv["basis"]["boundary_rms_mm"],
                "poly_degree": pv["basis"]["poly_degree"],
                "area_mm2": pv["area_mm2"],
                "perimeter_mm": pv["perimeter_mm"],
                "model": pr.get("model"),
                "warnings": pv.get("warnings") or [],
            }
            first = (pv["modes"][0] if pv["modes"] else {})
            for d in first.get("deflection_at_probes") or []:
                ring_hot.append({"ref": d["name"],
                                 "normalised_deflection":
                                     d["normalised_deflection"],
                                 "near_antinode":
                                     abs(d["normalised_deflection"]) >= 0.5})
            ring_hot.sort(key=lambda d: -abs(d["normalised_deflection"]))
            if ratio is not None:
                warnings.append(
                    "the outline fills only %.1f %% of its bounding box, so the "
                    "bbox plate is bigger and softer: its f1 is biased LOW. On "
                    "the real outline f1 = %.1f Hz, which is %+.1f %% of the "
                    "bbox number (penalty-ladder spread %.2f %%, %s)"
                    % (100.0 * fill, f_ring, 100.0 * (ratio - 1.0),
                       pv["basis"]["alpha_spread_pct"],
                       "converged" if pv["basis"]["alpha_converged"]
                       else "NOT converged - indicative only"))
        else:
            warnings.append(
                "the outline fills only %.1f %% of its bounding box, so the "
                "bbox plate is bigger and softer and f1 is biased LOW; the "
                "polygon solve that would quantify the bias failed (%s: %s)"
                % (100.0 * fill, pr.get("reason_code"), pr.get("message")))
    warnings.append("component mass is not added; a heavy part lowers f1 and "
                    "moves the mode")

    # which components sit on the antinodes of the first mode
    hot = []
    if probes and modes:
        first = modes[0]
        if first.get("deflection_at_probes"):
            ranked = sorted(first["deflection_at_probes"],
                            key=lambda d: -abs(d["normalised_deflection"]))
            for d in ranked:
                hot.append({"ref": d["name"],
                            "normalised_deflection": d["normalised_deflection"],
                            "near_antinode": abs(d["normalised_deflection"]) >= 0.5})

    out = ok(
        {
            "plate_mm": [_sig2(a_mm), _sig2(b_mm)],
            "thickness_mm": th,
            "outline_fill_fraction_of_bbox": _sig2(fill),
            "mount": mount,
            "modes": modes,
            "hotspots": hot[:12],
            "plate_on_outline": on_ring,
            "hotspots_on_outline": ring_hot[:12],
            "probe_count": len(probes),
            "warnings": warnings,
        },
        "Hz",
        res["model"],
        "Kirchhoff thin plate over the outline bounding box, mount=%r" % mount,
        "mode frequencies inherit the plate model's accuracy (exact for a "
        "rectangular SSSS/CCCC plate, 0.15 % for free edges) plus the mass "
        "approximation named in warnings; when the outline is not rectangular "
        "`plate_on_outline` re-solves the same plate on the real polygon, so "
        "the bbox bias is a number instead of a warning (its own penalty-ladder "
        "spread is reported next to it)",
        {"note": "use vibration_random_response / vibration_fatigue with modes[0]"
                 "['freq_hz'] and a PSD and an allowable deflection to get from "
                 "'it rings at N Hz' to 'it survives M hours'",
         "answer_source": "computed by this server from geometry",
         "authoritative": False,
         "verdict_available_from": "no vendor engine computes this - it is a "
                                   "design estimate, not a DRC rule"},
    )
    return out


def _sig2(x):
    try:
        return float("%.6g" % float(x))
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# Net geometry / pour audit. Both answer "is this copper sane?", which the
# existing tools could measure (counts, total length) but not judge.
# --------------------------------------------------------------------------

_POWERISH = ("+3V3", "+5V", "+12V", "VCC", "VDD", "VBUS", "VBAT", "VIN", "3V3",
             "5V", "AGND", "GND", "VREF")


def _is_powerish(name):
    n = (name or "").upper()
    return any(k in n for k in _POWERISH)


def _prim_mst(points):
    """MST length over 2-D points (Prim, O(n^2)). A LOWER BOUND on routing."""
    n = len(points)
    if n < 2:
        return 0.0
    inf = float("inf")
    best = [inf] * n
    used = [False] * n
    best[0] = 0.0
    total = 0.0
    for _ in range(n):
        k = -1
        for i in range(n):
            if not used[i] and (k < 0 or best[i] < best[k]):
                k = i
        used[k] = True
        total += best[k]
        px, py = points[k]
        for i in range(n):
            if used[i]:
                continue
            d = ((points[i][0] - px) ** 2 + (points[i][1] - py) ** 2) ** 0.5
            if d < best[i]:
                best[i] = d
    return total


def net_geometry_report(board, min_segments=4, top=40, detour_flag=1.8):
    """Per net: routed length, segments, vias, pads, and length / pad-MST.

    `detour_ratio` is the smell test for "this net took the scenic route". The
    denominator is the minimum spanning tree over the net's PAD CENTRES, so it is
    a lower bound on any tree that joins the pads: pad size, obstacle avoidance
    and multi-drop topology all push the real number up. A ratio near 1 means the
    copper goes roughly pad-to-pad; 2 means it covers twice the minimum.
    """
    nets = {}
    for t in board.tracks:
        n = t.get("net")
        if n is None:
            continue
        e = nets.setdefault(n, {"length": 0.0, "segs": 0, "vias": 0, "pads": [],
                                "widths": [], "layers": set()})
        st, en = t.get("start"), t.get("end")
        if st and en:
            e["length"] += math.hypot(en[0] - st[0], en[1] - st[1])
        e["segs"] += 1
        e["widths"].append(t.get("width") or 0)
        if t.get("layer"):
            e["layers"].add(t["layer"])
    for v in board.vias:
        n = v.get("net")
        if n in nets:
            nets[n]["vias"] += 1
    for fp in board.footprints:
        for pd in fp.get("pads") or []:
            n = pd.get("net")
            if n in nets and pd.get("x") is not None:
                nets[n]["pads"].append((pd["x"], pd["y"]))
    if not nets:
        return err("no routed copper on this board", "NO_GEOMETRY")

    rows = []
    for name, e in nets.items():
        if e["segs"] < int(min_segments):
            continue
        mst = _prim_mst(e["pads"])
        ratio = (e["length"] / mst) if mst > 0.001 else None
        widest = max(e["widths"]) if e["widths"] else 0.0
        flags = []
        if ratio is not None and ratio >= float(detour_flag) * 1.4:
            flags.append("very_long_detour")
        elif ratio is not None and ratio >= float(detour_flag):
            flags.append("long_detour")
        if _is_powerish(name) and widest < 0.25:
            flags.append("power_net_thin")
        if e["segs"] >= 8 and e["vias"] / float(e["segs"]) > 0.25:
            flags.append("via_heavy")
        rows.append({"net": name, "length_mm": round(e["length"], 2),
                     "segments": e["segs"], "vias": e["vias"],
                     "pads": len(e["pads"]),
                     "pad_mst_mm": round(mst, 2),
                     "detour_ratio": (round(ratio, 2) if ratio else None),
                     "widest_mm": round(widest, 3),
                     "layers": sorted(e["layers"]), "flags": flags})
    rows.sort(key=lambda r: (-(r["detour_ratio"] or 0), -r["length_mm"]))
    shown = rows[:int(top)] if top else rows
    return ok(
        value={"nets_measured": len(rows), "returned": len(shown),
               "nets": shown,
               "flagged": [r["net"] for r in rows if r["flags"]],
               "read_it_like_this": (
                   "detour_ratio = routed length / MST over pad centres. It is a "
                   "LOWER BOUND denominator: pads, obstacles and multi-drop nets "
                   "all legitimately push it above 1, and a net that must ring the "
                   "board because the parts sit on both edges is not 'routed badly'. "
                   "Use it to FIND the nets worth looking at, then look at the "
                   "picture (board_render) - a high ratio alone is not a defect")},
        unit="mm",
        model="routed length per net vs minimum spanning tree over its pad centres",
        basis="every track segment on the net, straight-line length; MST by Prim "
              "over pad ANCHOR points (pad size ignored)",
        precision="exact for the lengths; the ratio is a heuristic and its "
                  "denominator ignores pad geometry, keepouts and layer changes",
        assumptions={"min_segments": int(min_segments),
                     "detour_flag": float(detour_flag)})


def pour_audit(board, zone_index=None, min_island_mm2=0.0):
    """Per zone, per filled island: area, pads inside, same-net vias inside.

    An island that contains no pad and no via of the zone's net is dead copper:
    it is isolated from everything and exists only because the filler was told to
    keep islands. That is the defect this reports; it is NOT a connectivity
    verdict (the DRC is).
    """
    if not board.zones:
        return err("no zones in this board", "NO_GEOMETRY",
                   hint="there is nothing poured; board_zones lists what exists")
    idx = range(len(board.zones)) if zone_index is None else [int(zone_index)]
    pads_by_net = {}
    for fp in board.footprints:
        for pd in fp.get("pads") or []:
            if pd.get("x") is None:
                continue
            pads_by_net.setdefault(pd.get("net"), []).append(
                (pd["x"], pd["y"], "%s.%s" % (fp.get("ref") or "?",
                                             pd.get("number") or "?")))
    vias_by_net = {}
    for v in board.vias:
        if v.get("x") is None:
            continue
        vias_by_net.setdefault(v.get("net"), []).append((v["x"], v["y"]))

    zones = []
    dead_total = 0
    for i in idx:
        try:
            z = board.zones[i]
        except IndexError:
            return err("zone_index out of range (0..%d)" % (len(board.zones) - 1),
                       "ARG_RANGE")
        if z.get("keepout"):
            zones.append({"index": i, "net": z.get("net"),
                          "layers": z.get("layers"), "keepout": True,
                          "note": "a keepout has no copper by definition"})
            continue
        net = z.get("net")
        polys = [p for p in (z.get("filled_polygons") or []) if len(p) >= 3]
        pads = pads_by_net.get(net, [])
        vias = vias_by_net.get(net, [])
        islands = []
        signed_total = 0.0
        dead = 0
        covered = set()
        for j, poly in enumerate(polys):
            a = 0.0
            for k in range(len(poly)):
                x0, y0 = poly[k]
                x1, y1 = poly[(k + 1) % len(poly)]
                a += x0 * y1 - x1 * y0
            a /= 2.0
            signed_total += a
            hp = [p[2] for p in pads if G.point_in_polygon((p[0], p[1]), poly)]
            covered.update(hp)
            hv = sum(1 for v in vias if G.point_in_polygon(v, poly))
            isdead = (not hp) and hv == 0
            if isdead and abs(a) >= float(min_island_mm2):
                dead += 1
            islands.append({"island": j, "area_mm2": round(abs(a), 2),
                            "pads": len(hp), "pads_named": hp[:8],
                            "same_net_vias": hv,
                            "dead_copper": isdead})
        uncovered = [p[2] for p in pads if p[2] not in covered]
        dead_total += dead
        zones.append({"index": i, "net": net, "layers": z.get("layers"),
                      "priority": z.get("priority"),
                      "filled_polygons": len(polys),
                      "copper_area_mm2": round(abs(signed_total), 2),
                      "islands": len(polys),
                      "dead_islands": dead,
                      "pads_of_net": len(pads),
                      "pads_on_copper": len(covered),
                      "pads_off_copper": uncovered[:20],
                      "island_detail": islands[:60]})
    verdict = "pour_dead_copper" if dead_total else "pour_geometry_clean"
    return ok(
        value={"verdict": verdict,
               "zones": zones,
               "dead_islands_total": dead_total,
               "read_it_like_this": (
                   "dead_copper = a filled island that touches no pad and no "
                   "same-net via. `pads_off_copper` is NOT 'unconnected': a pad "
                   "reaches the pour through a track or a via that a "
                   "centre-in-polygon test cannot see. Only board_drc's "
                   "unconnected_total settles connectivity")},
        unit="mm^2",
        model="per filled island: shoelace area, pad-centre and via-centre "
              "in-polygon membership",
        basis="the filled_polygon rings stored in the file; nothing refilled",
        precision="a fill is a SNAPSHOT - if tracks moved after the pour, these "
                  "areas describe the old copper. Islands whose winding marks them "
                  "as holes still count as polygons here; copper_area_mm2 uses the "
                  "SIGNED sum so holes subtract",
        assumptions={"zone_index": zone_index,
                     "min_island_mm2": float(min_island_mm2),
                     "islands_listed_per_zone": 60})


# ---------------------------------------------------------------- angle audit
def _seg_ray(p, q):
    """Unit-ray angle (deg, 0..360) from point p towards q."""
    return math.degrees(math.atan2(q[1] - p[1], q[0] - p[0])) % 360.0


def _fold180(deg):
    """Fold an angle into [0, 180): a track has no direction, only a heading."""
    return deg % 180.0


def angle_audit(board, layer=None, angle_tol_deg=1.0, top_other=20):
    """Are the tracks actually H / V / 45, and where do the corners come from?

    Three things get measured, and they must not be mixed together:

    * **segment headings** - every track segment classified as H (0/180), V
      (90/270), 45 (45/135) or `other`. `other` is the only category that is a
      straight 45-degree-rule violation.
    * **corners** - at a vertex where two same-net same-layer segments meet, the
      angle BETWEEN THE TWO OUTGOING RAYS. A properly chamfered corner is 135
      (the route turns by 45). A 90 corner is a square corner, which is what the
      rule forbids. Under 90 is an acute corner (acid trap).
    * **tees** - an endpoint landing in the INTERIOR of another same-net
      segment. A tee is a branch, not a bend: 90 is the normal way to tee off a
      trunk, and bending a branch in at 45 creates a 45-degree wedge that is
      worse than the square tee. Reported separately so it cannot be counted as
      a routing defect.

    The three numbers answer three different questions, which is the point:
    "did the 45-degree rule get followed" is the first row, "why does the back
    copper look full of right angles" is the second and third.
    """
    tol = float(angle_tol_deg)
    cu = _copper_layers(board)
    vias = {((round(float(v["x"]), 3), round(float(v["y"]), 3)), v.get("net"))
            for v in (board.vias or [])}
    pad_pts = set()
    for fp in board.footprints:
        for pad in fp.get("pads") or []:
            pad_pts.add((round(float(pad["x"]), 3), round(float(pad["y"]), 3)))

    def _cls(heading):
        if abs(heading - 0.0) <= tol or abs(heading - 180.0) <= tol:
            return "H"
        if abs(heading - 90.0) <= tol:
            return "V"
        if abs(heading - 45.0) <= tol or abs(heading - 135.0) <= tol:
            return "45"
        return "other"

    per_layer = {}
    segged = {}
    for lay in cu:
        if layer and lay != layer:
            continue
        segs = []
        for tr in board.tracks or []:
            if tr.get("layer") != lay:
                continue
            pts = tr.get("_points") or [tr["start"], tr["end"]]
            for i in range(len(pts) - 1):
                a, b = tuple(pts[i]), tuple(pts[i + 1])
                L = math.hypot(b[0] - a[0], b[1] - a[1])
                if L < 1e-6:
                    continue
                hd = _fold180(_seg_ray(a, b))
                segs.append({"a": a, "b": b, "L": L, "hd": hd, "cls": _cls(hd),
                             "net": tr.get("net"),
                             "w": round(float(tr.get("width") or 0.0), 3)})
        cnt = {"H": 0, "V": 0, "45": 0, "other": 0}
        for s in segs:
            cnt[s["cls"]] += 1
        tot = max(1, sum(cnt.values()))
        other = sorted([s for s in segs if s["cls"] == "other"],
                       key=lambda s: -s["L"])[:int(top_other)]
        per_layer[lay] = {
            "segments": sum(cnt.values()),
            "H": cnt["H"], "V": cnt["V"], "diag45": cnt["45"],
            "other": cnt["other"],
            "other_pct": round(100.0 * cnt["other"] / tot, 2),
            "other_segments": [
                {"net": s["net"], "w": s["w"], "length_mm": round(s["L"], 2),
                 "heading_deg": round(s["hd"], 1),
                 "start": [round(s["a"][0], 2), round(s["a"][1], 2)],
                 "end": [round(s["b"][0], 2), round(s["b"][1], 2)]}
                for s in other],
        }
        segged[lay] = segs

    corners = {}
    tees = {}
    for lay, segs in segged.items():
        # ray table: vertex -> [(ray angle, other endpoint, net)]
        rays = {}
        for s in segs:
            for (p, q) in ((s["a"], s["b"]), (s["b"], s["a"])):
                key = (round(p[0], 3), round(p[1], 3))
                rays.setdefault((key, s["net"]), []).append(
                    (_seg_ray(p, q), (round(q[0], 3), round(q[1], 3)), s))
        bend = {"corner_135": 0, "corner_90": 0, "corner_acute": 0,
                "collinear": 0, "right_angle_list": [], "acute_list": []}
        # de-duplicate a pair of segments meeting at both ends (a 2-seg loop)
        seen_pairs = set()
        for (key, net), lst in rays.items():
            for i in range(len(lst)):
                for j in range(i + 1, len(lst)):
                    r1, r2 = lst[i][0], lst[j][0]
                    sig = (key, net, tuple(sorted([lst[i][1], lst[j][1]])))
                    if sig in seen_pairs:
                        continue
                    seen_pairs.add(sig)
                    d = abs((r1 - r2 + 180.0) % 360.0 - 180.0)
                    corner = 180.0 - abs(180.0 - d) if d > 90 else d
                    # corner = angle between the two outgoing rays, 0..180
                    if abs(corner - 180.0) <= tol:
                        bend["collinear"] += 1
                    elif abs(corner - 135.0) <= tol:
                        bend["corner_135"] += 1
                    elif abs(corner - 90.0) <= tol:
                        bend["corner_90"] += 1
                        if len(bend["right_angle_list"]) < int(top_other):
                            bend["right_angle_list"].append(
                                {"net": net, "x": round(key[0], 2), "y": round(key[1], 2),
                                 "w": lst[i][2]["w"]})
                    elif corner < 90.0 + tol:
                        bend["corner_acute"] += 1
                        if len(bend["acute_list"]) < int(top_other):
                            bend["acute_list"].append(
                                {"net": net, "x": round(key[0], 2), "y": round(key[1], 2),
                                 "corner_deg": round(corner, 1)})
                    else:
                        bend.setdefault("corner_other", 0)
                        bend["corner_other"] += 1
        # tees: endpoint strictly inside another same-net segment
        tee = {"count": 0, "square": 0, "list": []}
        for s in segs:
            for p in (s["a"], s["b"]):
                for u in segs:
                    if u is s or u["net"] != s["net"]:
                        continue
                    ax, ay = u["a"]; bx, by = u["b"]
                    dx, dy = bx - ax, by - ay
                    L2 = dx * dx + dy * dy
                    if L2 < 1e-12:
                        continue
                    t = ((p[0] - ax) * dx + (p[1] - ay) * dy) / L2
                    if not (0.02 < t < 0.98):
                        continue
                    qx, qy = ax + t * dx, ay + t * dy
                    if math.hypot(p[0] - qx, p[1] - qy) > 0.02:
                        continue
                    if tuple(p) in pad_pts:
                        continue
                    tee["count"] += 1
                    d = abs(_fold180(_seg_ray((ax, ay), (bx, by))) - s["hd"]) % 180.0
                    d = min(d, 180.0 - d)
                    if abs(d - 90.0) <= tol:
                        tee["square"] += 1
                    if len(tee["list"]) < int(top_other):
                        tee["list"].append({"net": s["net"], "tee_angle_deg": round(d, 1),
                                            "x": round(p[0], 2), "y": round(p[1], 2),
                                            "w": s["w"]})
        corners[lay] = bend
        tees[lay] = tee

    problems = []
    for lay, st in per_layer.items():
        if st["other"]:
            problems.append("other_heading@%s" % lay)
    for lay, c in corners.items():
        if c["corner_90"]:
            problems.append("right_angle_corner@%s" % lay)
        if c["corner_acute"]:
            problems.append("acute_corner@%s" % lay)
    verdict = "angles_clean" if not problems else "angles_" + "+".join(problems)

    return ok(
        value={
            "verdict": verdict,
            "problems": problems,
            "per_layer": per_layer,
            "corners": corners,
            "tees": tees,
            "read_it_like_this": (
                "per_layer.other is the only 45-degree-RULE violation: a segment "
                "that is neither H, V nor 45. corners measure the angle between "
                "the two outgoing rays at a vertex - 135 is a properly chamfered "
                "turn, 90 is a square corner, <90 is an acid trap. tees are "
                "branches (an endpoint inside another segment of the same net): a "
                "90 tee is the normal way to branch off a trunk and is NOT a "
                "routing defect, so it is reported apart from corners"),
        },
        unit="deg",
        model="atan2 heading per segment folded into [0,180); corner = 180 - "
              "|180 - delta| over the two outgoing rays; tee = point-to-segment "
              "projection with t in (0.02,0.98) and gap <= 0.02 mm",
        basis="the track list as stored in the file; a via-and-pad vertex is "
              "counted, an endpoint sitting on a PAD is suppressed from tees "
              "because a track landing on a pad is a connection, not a branch",
        precision="a corner is only seen when two segments share an endpoint to "
                  "0.001 mm - a corner split by a via in the middle is invisible "
                  "here, and so is a corner drawn as one polygon. Tolerance "
                  "%.2f deg" % tol,
        assumptions={"layer": layer or "all copper",
                     "angle_tol_deg": tol,
                     "top_other": int(top_other)})
