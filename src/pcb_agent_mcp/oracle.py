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


# ------------------------------------------------------------------- queries
def summary(board):
    data = board.as_dict(items=False)
    return ok(
        value=data["counts"],
        unit="counts",
        model="canonical_model_summary",
        basis="counts and presence flags read directly from the parsed "
              "canonical model (no estimation)",
        precision="exact for counts; outline bbox is the bounding box of the "
                  "outline vertices, not the copper extents",
        assumptions={"format": board.source.get("format"),
                     "path": board.source.get("path")},
        extra={
            "source": board.source,
            "outline_bbox": data["outline_bbox"],
            "thickness_mm": board.thickness_mm,
            "stackup": board.stackup,
            "present": board.present,
            "absent": board.absent,
            "rules": board.rules,
        },
    )


def components(board, layer=None):
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
    return ok(
        value=rows,
        unit="list",
        model="canonical_component_table",
        basis="footprint placement and pad-to-net assignment read from the file",
        precision="exact for positions/rotations; bbox is derived from pad extents "
                  "(courtyard and 3D height are not included)",
        assumptions={"count": len(rows), "layer_filter": layer},
    )


def nets(board):
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
    return ok(
        value=stats,
        unit="per-net counts",
        model="canonical_net_table",
        basis="pads/tracks/vias/zones grouped by net name; length is the sum of "
              "centreline segment lengths",
        precision="exact counts; track_length ignores arc true length (arcs are "
                  "polyline-approximated by the adapter)",
        assumptions={"nets": len(stats)},
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
               "isolated": outside[:40], "polygon_source": source},
        unit="count",
        model="point_in_polygon_per_pad",
        basis="pad centre tested against the pour polygon(s) with ray casting; "
              "boundary counts as inside",
        precision="approximate: a pad partially covered by pour copper is treated as "
                  "connected by its centre only; thermal spoke count and isolated "
                  "islands inside the pour are NOT evaluated",
        assumptions={"zone_index": int(zone_index), "net": net,
                     "polygons": len(polygons)},
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
