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


def board_ir_drop(board, net, source, sinks, current_a=None, copper_um=None,
                  plating_um=None, quantize_mm=0.01, temp_c=20.0,
                  max_nodes=1200):
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
    the net has vias and `plating_um` is missing, or when nothing connects. A
    copper pour on the same net is reported as a warning: it is in parallel, so
    the drop is then an upper bound.
    """
    from .calc import power as PW

    if not net:
        return err("net is required", "ARG_REQUIRED")
    try:
        q = float(quantize_mm or 0.01)
    except (TypeError, ValueError):
        return err("quantize_mm must be a number", "ARG_INVALID")
    if q <= 0:
        return err("quantize_mm must be > 0", "ARG_RANGE")

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

    if not tracks:
        return err("net %r has no tracks on this board" % (net,), "NOT_APPLICABLE",
                   hint="without tracks there is no path to measure; an IR drop "
                        "answered from pad positions alone would be fiction")

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
        pts = tr.get("_points") or [tr["start"], tr["end"]]
        for i in range(len(pts) - 1):
            x1, y1 = float(pts[i][0]), float(pts[i][1])
            x2, y2 = float(pts[i + 1][0]), float(pts[i + 1][1])
            L = math.hypot(x2 - x1, y2 - y1)
            if L <= 0:
                continue
            r = PW._rho(temp_c) * (L * 1e-3) / (w * 1e-3 * t_um * 1e-6)
            add(node(x1, y1, layer), node(x2, y2, layer), r, "track",
                "%s %.4gmm x %.3gmm" % (layer, L, w),
                layer=layer, length_mm=L, width_mm=w, copper_um=t_um)
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
        x, y = float(vi["x"]), float(vi["y"])
        layers = _layer_span(board, vi.get("from"), vi.get("to"))
        if len(layers) < 2:
            warnings.append("via at (%.3f, %.3f) spans a single layer - ignored"
                            % (x, y))
            continue
        add(node(x, y, layers[0]), node(x, y, layers[-1]), r_via, "via",
            "via %.2gmm/%.3gum" % (drill, float(plating_um)),
            layer="|".join([layers[0], layers[-1]]), drill_mm=drill,
            plating_um=float(plating_um))

    # ---- pads: plated holes join their layers, SMD pads are just terminals
    pad_line = 1e-6
    for fp, pad in pads:
        px, py = _pad_xy(fp, pad)
        layers = _item_layers(board, pad.get("layers"))
        if not layers:
            continue
        for lay in layers:
            node(px, py, lay)
        if pad.get("hole_mm") and len(layers) > 1:
            for k in range(len(layers) - 1):
                add(node(px, py, layers[k]), node(px, py, layers[k + 1]),
                    pad_line, "pad_barrel", "plated hole %s" % (layers[k],))

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

    if len(names) > max_nodes:
        return err("the network has %d nodes (limit %d)" % (len(names), max_nodes),
                   "TOO_LARGE",
                   hint="raise quantize_mm - two nodes closer than that are "
                        "merged anyway, so a 0.01 mm mesh on a big net is mostly "
                        "noise")

    # ---- solve
    injections = {src_node: total_a}
    for s in sink_list:
        injections[s["node"]] = injections.get(s["node"], 0.0) - s["current_a"]
    sol, why = PW.resistive_network([n["id"] for n in names], edges, injections,
                                    src_node)
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
            if z.get("net") == net and not z.get("keepout")]
    if pour:
        warnings.append("%d copper zone(s) on net %r are NOT modelled: they sit in "
                        "parallel with these tracks, so this drop is an UPPER "
                        "bound" % (len(pour), net))

    max_drop = max(s["drop_mv"] for s in sink_out)
    worst_sink = max(sink_out, key=lambda s: s["drop_mv"])
    loss = sum(e["power_mw"] for e in edge_out) / 1e3
    return ok(value={"net": net,
                     "source": source,
                     "total_current_a": round(total_a, 9),
                     "max_drop_mv": round(max_drop, 6),
                     "worst_sink": worst_sink["pad"],
                     "worst_sink_drop_pct_of_3v3": round(100.0 * max_drop / 3.3, 6),
                     "sinks": sink_out,
                     "total_copper_loss_w": round(loss, 9),
                     "worst_current_density": (worst_j or {}),
                     "edges": sorted(edge_out, key=lambda r: -r["current_a"])[:40],
                     "edge_count": len(edge_out),
                     "node_count": len(names),
                     "quantize_mm": q},
              unit="mV", model="track_via_pad_resistor_graph",
              basis="every track segment becomes rho(T)*L/(w*t) with t from the "
                    "stackup copper weight, every via a barrel of "
                    "pi*((d/2)^2 - (d/2-p)^2), plated pads join their layers; the "
                    "graph is solved by nodal analysis with the source pad at 0 V",
              precision="a DC answer, and a bound in the direction of missing "
                        "copper: skin effect, pad/plane spreading and connector "
                        "contact are not in it (so the real drop is worse), while "
                        "a pour on the same net is not in it either (so this drop "
                        "is an upper bound). Nodes closer than quantize_mm are "
                        "merged, so a hairline gap below that is invisible",
              assumptions={"quantize_mm": q, "temp_c": float(temp_c),
                           "copper_um": explicit,
                           "copper_um_source": ("explicit" if explicit is not None
                                                else ("stackup" if cu else None)),
                           "plating_um": plating_um,
                           "net": net, "board_thickness_mm": board_th},
              extra={"warnings": warnings,
                     "not_computed": ["copper pour / plane in parallel",
                                      "skin effect",
                                      "pad and plane spreading resistance",
                                      "temperature rise feedback (rho uses temp_c)",
                                      "current capacity / fusing (needs a "
                                      "temperature-rise criterion, not a current)"]})
