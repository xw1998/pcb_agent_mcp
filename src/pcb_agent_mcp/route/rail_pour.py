"""Pour a rail where its own copper already runs, then measure the copper KiCad poured.

`route/rail_plan.py` ends every failing rail with `pour_needed` and a
`needs_outline: true` action, because it refuses to invent a shape. That refusal
is right - a pour nobody drew is a pour nobody can review - but it left the next
question unanswerable from this server: *if* a pour were drawn over this rail's
layer, would the copper that comes out be usable? On the RK3506 SOM the six
rails that need a pour (`+1V8_ANA`, `+0V9_ANA`, `+0V9_PMU`, `+0V9_LOGIC`,
`+0V9_CPU`, `+0V675_REF`) run 19-91 segments of 0.1 mm copper on In2.Cu/In4.Cu,
so "pour it" and "pour it and it comes back as 8 islands 0.18 mm apart" are
different answers and only one of them is worth doing.

Two facts decide this module's shape:

* **The filler is not ours.** Copper comes from KiCad's `ZONE_FILLER`
  (`fidelity/fill.py`), on a temp copy, and every polygon below is a
  `filled_polygon` KiCad wrote - nothing here rasterises or approximates a pour.
* **The shape is the board's own outline.** The candidate outline is the board
  outline itself: a power plane is the layer, and KiCad applies the edge
  clearance while pouring. Choosing that outline is not a design decision this
  module invents - it removes the one thing rail_plan could not supply without
  guessing, and it is the same shape the board already has.

What it then measures, per (net, layer):

1. **Coverage along the rail.** The rail's own tracks are sampled and each sample
   is tested against the poured polygons. A sample outside every polygon means
   the pour did not cover the copper it exists to widen; the uncovered length is
   reported in mm, not as a boolean.
2. **Width along the rail (a LOWER BOUND).** At a covered sample the distance
   `d` from the sample to the poured polygon's boundary gives `2*d` as a lower
   bound on the local copper width in every direction (`2*d` <= any chord
   through the point, because `d` is the minimum over all directions). So
   `min(2*d)` is a bound that can PROVE the pour carries the demand, and can
   never prove it does not - exactly the asymmetry `min_thickness` already has
   in rail_plan. The report says which one happened.
3. **Fragmentation.** How many separate pieces the filler produced and the
   closest gap between pieces, because a rail chopped by every crossing signal
   is not one conductor even when it is wide where it survives.

What it will NOT do: write to the board it was given. The pour happens on a
temp copy and the file's bytes are not touched; turning a measured pour into
copper on the user's board is `board_add_zone` with a shape the caller approves.
"""
import math
import os
import shutil
import tempfile

from .. import geometry as G

#: Verdicts, strongest evidence first. `width_proven` is the only one that says
#: the pour is enough; everything else is a fact about the copper that was
#: poured, not a promise about the current.
VERDICTS = (
    "no_copper",
    "engine_refused",
    "pour_not_filled",
    "rail_not_covered",
    "width_unproven",
    "width_proven",
)

#: Sample spacing along the rail, mm. Half the narrowest demand on this board
#: (0.116 mm) would put a sample in every 0.058 mm; 0.05 mm is finer than that
#: and still ~400 samples per net, which is the trade this constant records.
SAMPLE_MM = 0.05


def copper_layers(board):
    """Copper layer names in stackup order, outermost first."""
    names = [str(s.get("name")) for s in (board.stackup or [])
             if str(s.get("name") or "").endswith(".Cu")]
    return names or ["F.Cu", "B.Cu"]


def _inner_layers(board):
    cu = copper_layers(board)
    return list(cu[1:-1]) if len(cu) > 2 else []


def _pad_layers(pad, cu):
    raw = [str(l) for l in (pad.get("layers") or [])]
    if "*.Cu" in raw:
        return set(cu)
    if "F&B.Cu" in raw:
        return {cu[0], cu[-1]} if len(cu) > 1 else {cu[0]}
    return set(l for l in raw if l.endswith(".Cu"))


def rail_layers(board, net):
    """Per copper layer: how much of this rail already runs there.

    The length is what picks the pour layer, not the segment count: a layer with
    one long run is a better carrier than one with ten stubs, and the vias/pads
    counts say whether the pour would have anything of its own to connect to.
    """
    cu = copper_layers(board)
    per = {}
    for t in board.tracks:
        if t.get("net") != net:
            continue
        lay = str(t.get("layer"))
        a, b = t["start"], t["end"]
        row = per.setdefault(lay, {"length_mm": 0.0, "segments": 0,
                                   "widths": [], "vias": 0, "pads": 0})
        row["length_mm"] += math.hypot(b[0] - a[0], b[1] - a[1])
        row["segments"] += 1
        row["widths"].append(float(t.get("width") or 0.0))
    for v in board.vias:
        if v.get("net") != net:
            continue
        for lay in (v.get("layers") or []):
            per.setdefault(str(lay), {"length_mm": 0.0, "segments": 0,
                                      "widths": [], "vias": 0, "pads": 0})["vias"] += 1
    for fp in board.footprints:
        for pd in fp.get("pads") or []:
            if pd.get("net") != net:
                continue
            for lay in _pad_layers(pd, cu):
                per.setdefault(lay, {"length_mm": 0.0, "segments": 0,
                                     "widths": [], "vias": 0, "pads": 0})["pads"] += 1
    for row in per.values():
        row["length_mm"] = round(row["length_mm"], 3)
        row["widths"] = sorted(set(round(w, 4) for w in row["widths"]))
    return per


def pick_layer(board, net, layer=None):
    """Where the pour goes: the inner layer the rail already runs on most.

    `layer` wins when given (validated). Otherwise an INNER layer is chosen by
    track length - a plane belongs inside - and an outer layer is only used when
    the rail has no inner copper at all, which is reported as `outer_layer: true`
    because an outer pour is ground fill, not a reference plane (`docs/206`).
    """
    cu = copper_layers(board)
    per = rail_layers(board, net)
    if layer is not None:
        layer = str(layer)
        if layer not in cu:
            return None, {"reason": "layer_not_on_board", "layers": cu}
        return layer, {"why": "caller named it", "outer_layer": layer in (cu[0], cu[-1])}
    if not per:
        return None, {"reason": "no_copper"}
    inner = [l for l in _inner_layers(board) if l in per and per[l]["length_mm"] > 0]
    pool = inner or [l for l in per if per[l]["length_mm"] > 0]
    if not pool:
        return None, {"reason": "no_copper"}
    pool.sort(key=lambda l: (-per[l]["length_mm"], l))
    best = pool[0]
    return best, {"why": "most of this rail's copper already runs on it",
                  "length_mm": per[best]["length_mm"],
                  "outer_layer": best in (cu[0], cu[-1])}


def zone_polygons(board, net, layer):
    """The `filled_polygon` pieces KiCad poured for this net on this layer."""
    out = []
    for z in (board.zones or []):
        if z.get("net") != net:
            continue
        if str(layer) not in [str(x) for x in (z.get("layers") or [])]:
            continue
        for poly in (z.get("filled_polygons") or []):
            if len(poly) >= 3:
                out.append([(float(p[0]), float(p[1])) for p in poly])
    return out


def _samples(a, b, step):
    d = math.hypot(b[0] - a[0], b[1] - a[1])
    n = max(1, int(math.ceil(d / step)))
    return [(a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n)
            for i in range(n + 1)], d


def _piece_of(polys, p):
    for i, poly in enumerate(polys):
        if G.point_in_polygon(p, poly):
            return i
    return None


def _ray_t(p, d, poly):
    """Smallest positive t where the ray p + t*d crosses this polygon's boundary.

    A sample sitting exactly on the fill's boundary returns `0.0`, not `None`: it
    is the common case (a signal routed 0.1 mm from the rail clips the rail's
    edge) and "the pour reaches 0 mm past this edge" is the true reading.
    `None` means the ray found no edge ahead at all - an unclosed ring - which is
    reported as no added copper rather than as an infinity. Only the FIRST
    crossing counts: a non-convex fill can be crossed twice, and the copper ends
    at the first one.
    """
    best = None
    n = len(poly)
    for i in range(n):
        a = poly[i]
        b = poly[(i + 1) % n]
        ex, ey = b[0] - a[0], b[1] - a[1]
        den = d[0] * ey - d[1] * ex
        if abs(den) < 1e-15:
            continue
        t = ((a[0] - p[0]) * ey - (a[1] - p[1]) * ex) / den
        u = ((a[0] - p[0]) * d[1] - (a[1] - p[1]) * d[0]) / den
        if u < -1e-12 or u > 1.0 + 1e-12:
            continue
        if t < -1e-9:
            continue
        t = max(0.0, t)  # a sample sitting ON the edge reaches copper 0 mm away
        if best is None or t < best:
            best = t
    return best


def corridor(board, net, layer, sample_mm=SAMPLE_MM, polys=None):
    """Copper width ACROSS the rail, measured on the poured polygons.

    The rail's own tracks are sampled; at each sample the perpendicular is cast
    both ways and the first fill boundary it meets gives how far the pour reaches
    past the track edge. The local cross-section is

        width = track_width + max(0, left - w/2) + max(0, right - w/2)

    i.e. the track's own copper plus whatever copper the pour adds on each side.
    That is not a bound in the abstract: it is the width of the copper that is
    there, along the one direction current must pass. `min` over the whole rail
    is the bottleneck, and `at` names where it is. `width_p50_mm` / `width_p90_mm`
    say what the pour does *everywhere else*: a pour whose median is wide but
    whose minimum is the bare track has not widened the rail where it matters,
    and one whose median equals its minimum is uniformly boxed in.

    Why not `2*distance(point, boundary)`: a rail is not centred in its pour. A
    sample sitting on the fill's edge (the common case where a signal runs 0.1 mm
    from the rail) gets distance 0 and would read as "zero width" even when the
    pour extends 3 mm on the other side. Measured on the RK3506 SOM's own `+3V3`
    In3 plane, that formulation reported `0.0000 mm` on a 206.72 mm² plane, which
    is a wrong answer about a real plane, not a conservative one.
    """
    sample_mm = float(sample_mm or SAMPLE_MM)
    if polys is None:
        polys = zone_polygons(board, net, layer)
    tracks = [t for t in board.tracks
              if t.get("net") == net and str(t.get("layer")) == str(layer)]
    base = {"net": net, "layer": layer, "pieces": len(polys),
            "filled": bool(polys),
            "samples": 0, "covered": 0,
            "rail_length_mm": 0.0, "uncovered_length_mm": 0.0,
            "min_width_mm": None, "at": None, "piece": None,
            "bare_side_samples": 0, "one_side_samples": 0,
            "both_sides_samples": 0,
            "method": ("perpendicular cross-section at samples every %.3f mm along "
                       "this net's own tracks on this layer: track width + how far "
                       "the poured copper reaches past each track edge" % sample_mm),
            "caveat": ("the perpendicular is the direction current must pass, so "
                       "the minimum over the rail is the bottleneck; a pour that "
                       "only touches the track edge adds nothing and reads as the "
                       "track's own width"),
            "pieces_detail": []}
    if not polys or not tracks:
        return base
    base["pieces_detail"] = sorted(
        ({"area_mm2": round(abs(G.polygon_area(poly)), 3)} for poly in polys),
        key=lambda r: -r["area_mm2"])[:8]
    best = None
    widths = []
    for t in tracks:
        w = float(t.get("width") or 0.0)
        a, b = t["start"], t["end"]
        dx, dy = b[0] - a[0], b[1] - a[1]
        ln = math.hypot(dx, dy)
        if ln <= 1e-9:
            continue
        nx, ny = -dy / ln, dx / ln
        pts, _ = _samples(a, b, sample_mm)
        step_len = ln / max(1, len(pts) - 1)
        base["rail_length_mm"] += ln
        for p in pts:
            base["samples"] += 1
            i = _piece_of(polys, (p[0], p[1]))
            if i is None:
                base["uncovered_length_mm"] += step_len
                continue
            base["covered"] += 1
            poly = polys[i]
            left = _ray_t(p, (nx, ny), poly)
            right = _ray_t(p, (-nx, -ny), poly)
            add_l = max(0.0, left - w / 2.0) if left is not None else 0.0
            add_r = max(0.0, right - w / 2.0) if right is not None else 0.0
            l_adds = add_l > 1e-9
            r_adds = add_r > 1e-9
            if l_adds and r_adds:
                base["both_sides_samples"] += 1
            elif l_adds or r_adds:
                base["one_side_samples"] += 1
            else:
                base["bare_side_samples"] += 1
            width = w + add_l + add_r
            widths.append(width)
            if best is None or width < best[0]:
                best = (width, (round(p[0], 4), round(p[1], 4)), i, w,
                        (None if left is None else round(left, 4)),
                        (None if right is None else round(right, 4)))
    base["rail_length_mm"] = round(base["rail_length_mm"], 3)
    base["uncovered_length_mm"] = round(base["uncovered_length_mm"], 3)
    base["uncovered_share"] = (round(base["uncovered_length_mm"] /
                                     base["rail_length_mm"], 4)
                               if base["rail_length_mm"] else None)
    if widths:
        widths.sort()
        n = len(widths)
        base["width_p50_mm"] = round(widths[n // 2], 4)
        base["width_p90_mm"] = round(widths[min(n - 1, int(n * 0.9))], 4)
        base["width_max_mm"] = round(widths[-1], 4)
    if best is not None:
        base["min_width_mm"] = round(best[0], 4)
        base["at"] = list(best[1])
        base["piece"] = best[2]
        base["track_width_there_mm"] = round(best[3], 4)
        base["reach_left_mm"] = best[4]
        base["reach_right_mm"] = best[5]
    return base


def judge(row, demand_mm):
    """Verdict for one measured row. `demand_mm` is the demanded copper width."""
    if not row.get("filled"):
        return ("pour_not_filled",
                "the zone carries no filled_polygon: KiCad has not poured it, so "
                "its width is unknown rather than adequate", [])
    cor = row.get("corridor") or {}
    flags = []
    if not cor.get("samples"):
        return ("no_copper", "this net has no track on this layer to measure", flags)
    if cor.get("uncovered_share") and cor["uncovered_share"] > 0.0:
        flags.append("rail_partly_outside_pour")
    if cor.get("bare_side_samples"):
        flags.append("pour_adds_no_copper_on_part_of_the_rail")
    lower = cor.get("min_width_mm")
    if lower is None:
        return ("rail_not_covered",
                "not one sampled point of this rail landed inside the poured "
                "copper, so there is nothing for the pour to carry", flags)
    if demand_mm is None:
        return ("width_unproven",
                "no current was given, so the measured lower bound %.4f mm was not "
                "compared with any demand: 2*d is a bound, and a bound without a "
                "requirement is not a verdict" % lower, flags)
    if lower + 1e-9 >= float(demand_mm):
        return ("width_proven",
                "the lower bound on the copper width along this rail (%.4f mm) is "
                "at least the %.4f mm this current needs; because 2*d is never "
                "larger than the true local width, this pour carries the rail"
                % (lower, float(demand_mm)), flags)
    return ("width_unproven",
            "the lower bound along this rail is %.4f mm, below the %.4f mm the "
            "current needs. That is NOT proof the pour is too narrow: 2*d "
            "understates the true corridor. It is proof this measurement cannot "
            "certify the pour" % (lower, float(demand_mm)), flags)


def board_zone_params(board):
    """Clearance and min_thickness this BOARD already pours with.

    They are read, not chosen: the same dense neighbours that make a rail's neck
    thin also decide whether any copper can exist around it, so a pour judged at
    an assumed 0.25 mm clearance would report "the fill never reached the rail"
    about a board whose own rule is 0.1 mm. Measured on the RK3506 SOM: the four
    existing zones state `min_thickness 0.1` and inherit `clearance 0.1` from the
    default netclass, and pouring `+1V8_ANA` on In4.Cu at 0.25 mm clearance left
    375 of 505 rail samples outside the pour - at 0.1 mm most of them are inside.
    """
    rules = getattr(board, "rules", None) or {}
    clr = (rules.get("clearance") or {}).get("min_clearance")
    thick = None
    for z in (board.zones or []):
        t = z.get("min_thickness")
        if t:
            thick = float(t) if thick is None else min(thick, float(t))
    return {"clearance_mm": (float(clr) if clr is not None else None),
            "clearance_source": ("board.rules.min_clearance" if clr is not None
                                 else "default"),
            "min_thickness_mm": thick,
            "min_thickness_source": ("the thinnest min_thickness on this board's "
                                     "own zones" if thick is not None else "default")}


def measure(board, net, layer=None, demand_mm=None, sample_mm=SAMPLE_MM,
            clearance_mm=None, min_thickness_mm=None, priority=0):
    """Pour this rail on `layer` on a TEMP COPY and measure what KiCad built.

    `board` must be the parsed board of the same file (`board.source["path"]`);
    the caller already has it (`tools._load_board`) and re-parsing would double
    the cost of the only slow step. The pour never touches that file.
    """
    path = (board.source or {}).get("path")
    layer, picked = pick_layer(board, net, layer)
    row = {"net": net, "layer": layer, "layer_pick": picked,
           "demand_mm": demand_mm}
    if layer is None:
        row.update({"verdict": "no_copper", "pieces": 0,
                    "why": "this net has no copper on any layer"})
        return row
    ring = None
    for r in (board.outline or []):
        if len(r) >= 3:
            ring = [(float(p[0]), float(p[1])) for p in r]
            break
    if ring is None:
        row.update({"verdict": "no_copper", "pieces": 0,
                    "why": "the board has no outline to pour inside"})
        return row

    from ..fidelity import fill as FILL
    from ..adapters import open_board as _open

    tmpdir = tempfile.mkdtemp(prefix="pcb_agent_railpour_")
    try:
        work = os.path.join(tmpdir, "pour.kicad_pcb")
        shutil.copyfile(path, work)
        bp = board_zone_params(board)
        clr = bp["clearance_mm"] if clearance_mm is None else float(clearance_mm)
        thick = bp["min_thickness_mm"] if min_thickness_mm is None else float(
            min_thickness_mm)
        row["pour_params"] = {
            "clearance_mm": (0.2 if clr is None else clr),
            "clearance_source": (bp["clearance_source"] if clearance_mm is None
                                 else "argument"),
            "min_thickness_mm": (0.25 if thick is None else thick),
            "min_thickness_source": (bp["min_thickness_source"]
                                     if min_thickness_mm is None else "argument"),
            "connect": "solid",
            "priority": priority,
            "outline": "board outline (KiCad applies its own edge clearance)",
        }
        out = FILL.add_zone(work, net, layer, ring, priority=priority,
                            clearance=row["pour_params"]["clearance_mm"],
                            min_thickness=row["pour_params"]["min_thickness_mm"],
                            connect="solid", fill_it=True, verify_drc=False)
        row["outline"] = {"source": "board outline", "points": len(ring)}
        if str(out.get("status")) != "ok":
            row.update({"verdict": "engine_refused", "pieces": 0,
                        "why": "KiCad refused the pour: %s"
                               % (out.get("reason") or out.get("message")
                                  or out.get("code") or "no message"),
                        "engine": {k: out.get(k) for k in
                                   ("status", "code", "reason", "message")}})
            return row
        v = out.get("value") or {}
        row["filled"] = (v.get("filled") or {}).get("fill_area_mm2")
        poured = _open(work)
        polys = zone_polygons(poured, net, layer)
        cor = corridor(poured, net, layer, sample_mm=sample_mm, polys=polys)
        row["pieces"] = cor["pieces"]
        row["pieces_detail"] = cor["pieces_detail"][:8]
        row["corridor"] = cor
        row["filled"] = bool(polys)
        verdict, why, flags = judge(row, demand_mm)
        row.update({"verdict": verdict, "why": why, "flags": flags})
        return row
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
