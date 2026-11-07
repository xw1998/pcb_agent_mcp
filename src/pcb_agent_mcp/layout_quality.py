"""Layout quality as numbers a search can climb, and the topology those numbers are
judged on.

Why this module exists - measured, not argued. `docs/117` §1 opens on a real board
where `board_drc` reported `clearance 0`, `isolated_copper 0` and the only right
angles left were the unavoidable ones on pads, while the two ground pours had
**zero** stitching vias between them. Every check passed; the board was wrong.

That is the whole problem with a check that only says "not violated": it is flat.
Across the wide region of "mostly fine" layouts every point scores the same, so
nothing can tell a good rearrangement from a bad one, and a generator that has
only that judgement has no gradient to climb - it stops wherever it happens to
land. The metrics here are continuous, each says which direction is better, and
when a reference set is given each one reports the percentile this board sits at.
"It looks wrong" becomes a reading with a denominator.

Two things are kept apart on purpose:

* `metrics()` - measurements of one board, plus percentiles against a reference
  set the caller names. With no reference there are no percentiles, and it says
  so rather than inventing a corpus.
* `topology()` - which parts belong to which functional group and how the groups
  are wired. This is the layer people work in before coordinates exist (partition
  -> flow -> cluster -> place). Going straight to coordinates is how a layout ends
  up "compliant and ugly".

Every threshold below is an assumption of a judgement, not a measurement of the
world, and every one is written into the answer with the number that used it.
"""
import math

#: Two parts whose centres are closer than this are "crowded" - closer than two
#: 0805 courtyards plus a routing channel. A judgement, not a measurement.
CROWDED_MM = 1.0
#: A part whose nearest neighbour is further than this is "lonely": it is either a
#: connector doing its job at the board edge or a part nobody placed on purpose.
LONELY_MM = 20.0
#: Two parts count as lined up when one axis is within this and the other within
#: `ALIGN_REACH_MM`. A quarter millimetre is under one 0.1 mm grid step of slack.
ALIGN_TOL_MM = 0.25
ALIGN_REACH_MM = 30.0
#: Density is counted on a grid whose cell is derived from the board and the part
#: count, clamped to this range. A fixed cell does not travel: measured 2026-11-07
#: on a 53x30 mm board with 32 parts, a 5 mm grid gives 77 cells and 0.42 parts per
#: cell, so 73 % of cells are empty by arithmetic and the CV reads "collapsed" for a
#: board whose parts are in fact spread evenly. The cell that makes the reading
#: mean something has about one part per cell.
GRID_MM_MIN = 4.0
GRID_MM_MAX = 20.0
#: A net is treated as local evidence of a cluster only when it touches this few
#: parts. A ground net touches everything and would merge the whole board into one
#: cluster - that is a fact about grounds, not about grouping.
CLUSTER_NET_MAX = 8
#: A part is "on axis" when its rotation is within this many degrees of 0/90/180/270.
ON_AXIS_TOL_DEG = 1.0
#: A misc part closer than this to a classified part is pulled into that group.
ADOPT_MM = 15.0

#: Functional groups, most specific first. The order is the tie-break: a part that
#: touches both a USB net and a rail is a USB part, not a power part.
FUNCTION_HINTS = (
    ("memory", ("QSPI", "FLASH", "SPI", "NCS", "MOSI", "MISO", "W25", "SSI",
                "NOR", "SDIO", "EMMC")),
    ("clock", ("XTAL", "OSC", "HSE", "LSE", "CLKIN", "XIN", "XOUT")),
    ("usb", ("D+", "D-", "USB", "CC1", "CC2", "SBU", "DP", "DM")),
    ("debug", ("SWD", "SWCLK", "SWDIO", "JTAG", "TCK", "TMS", "TRST")),
    ("uart", ("UART", "TXD", "RXD")),
)
#: Rails decide "power" only when a part touches nothing else.
RAIL_HINTS = ("GND", "VSS", "VCC", "VDD", "3V3", "5V", "12V", "VBAT", "VBUS",
              "VIN", "PWR", "AVDD", "DVDD")

GROUP_ORDER = ("mcu", "memory", "clock", "usb", "debug", "uart", "power", "misc")

#: A part with at least this many pads is the controller itself - a 48/64 pin MCU,
#: not a decoupling cap. Identified by fan-out on purpose: net names on a real
#: board are "PA11", "W25_CS", "+3V3", and no keyword list can name them all.
CONTROLLER_PADS = 16


def _fps(board):
    out = []
    for fp in getattr(board, "footprints", []) or []:
        if fp.get("x") is None or fp.get("y") is None:
            continue
        out.append(fp)
    return out


def _net_of(fp):
    return set(str(p.get("net")) for p in (fp.get("pads") or []) if p.get("net"))


def _outline_bbox(board):
    rings = [ring for ring in (getattr(board, "outline", None) or []) if len(ring) >= 3]
    if not rings:
        return None
    xs = [p[0] for ring in rings for p in ring]
    ys = [p[1] for ring in rings for p in ring]
    return (min(xs), min(ys), max(xs), max(ys))


def _fp_bbox(fp):
    box = fp.get("bbox") or {}
    if all(box.get(k) is not None for k in ("x_min", "y_min", "x_max", "y_max")):
        return (float(box["x_min"]), float(box["y_min"]),
                float(box["x_max"]), float(box["y_max"]))
    x, y = float(fp["x"]), float(fp["y"])
    return (x, y, x, y)


def _median(values):
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _share(values, predicate):
    if not values:
        return None
    return sum(1 for v in values if predicate(v)) / float(len(values))


def _spread(values, name, unit, better, how, **extra):
    row = {"value": None, "unit": unit, "better_when": better, "how": how}
    if not values:
        row["why"] = "no measurement was possible"
        return row
    ordered = sorted(values)
    row.update({
        "value": round(_median(values), 4),
        "count": len(values),
        "min": round(ordered[0], 4),
        "p10": round(ordered[max(0, int(0.1 * (len(ordered) - 1)))], 4),
        "p90": round(ordered[min(len(ordered) - 1, int(0.9 * (len(ordered) - 1)))], 4),
        "max": round(ordered[-1], 4),
    })
    row.update(extra)
    return row


# ------------------------------------------------------------------ clusters
def groups(board):
    """Functional groups: which parts are doing the same job, by the nets they touch.

    A group is not a physical cluster and this does not pretend it is: it is the
    partition a person does first (power / MCU side / memory / clock / usb / debug),
    inferred from net names. Parts that touch nothing but rails are "power"; parts
    that match nothing are adopted into the nearest classified group within
    ADOPT_MM, and anything still alone stays in "unclassified" rather than being
    guessed into a group.
    """
    fps = _fps(board)
    membership = {}
    unclassified = []
    by_ref = {}
    for fp in fps:
        by_ref[str(fp.get("ref") or "")] = fp
    controller = None
    if fps:
        widest = max(fps, key=lambda fp: len(fp.get("pads") or []))
        if len(widest.get("pads") or []) >= CONTROLLER_PADS:
            controller = str(widest.get("ref") or "")
            membership[controller] = "mcu"
    for fp in fps:
        ref = str(fp.get("ref") or "")
        if ref == controller:
            continue
        nets = {n.upper() for n in _net_of(fp)}
        if not nets:
            unclassified.append(fp)
            continue
        votes = {}
        for name, hints in FUNCTION_HINTS:
            hits = sum(1 for net in nets for hint in hints if hint in net)
            if hits:
                votes[name] = hits
        if votes:
            # Most hits wins rather than first match: a flash part touches W25_CS,
            # W25_CLK, W25_IO0..3 - six votes, where a stray PA11 would be one.
            membership[ref] = sorted(votes.items(), key=lambda kv: -kv[1])[0][0]
            continue
        if all(any(rail in net for rail in RAIL_HINTS) for net in nets):
            membership[ref] = "power"
            continue
        unclassified.append(fp)

    adopted = []
    for fp in list(unclassified):
        x, y = float(fp["x"]), float(fp["y"])
        best = None
        for ref, name in membership.items():
            other = by_ref.get(ref)
            if other is None:
                continue
            distance = math.hypot(float(other["x"]) - x, float(other["y"]) - y)
            if best is None or distance < best[0]:
                best = (distance, name, ref)
        if best and best[0] <= ADOPT_MM:
            membership[str(fp.get("ref") or "")] = best[1]
            adopted.append({"ref": fp.get("ref"), "into": best[1],
                            "distance_mm": round(best[0], 3)})
            unclassified.remove(fp)

    out = {}
    for name in GROUP_ORDER:
        refs = sorted(ref for ref, group in membership.items() if group == name)
        if not refs:
            # An empty group is not a group. Keeping "misc" alive when nothing is in
            # it made the flow report an unreached group that does not exist.
            continue
        rows = [by_ref[r] for r in refs if r in by_ref]
        entry = {"name": name, "refs": refs, "count": len(refs),
                 "pads": sum(len(fp.get("pads") or []) for fp in rows)}
        if rows:
            xs = [float(fp["x"]) for fp in rows]
            ys = [float(fp["y"]) for fp in rows]
            entry["bbox"] = {"x_min": round(min(xs), 3), "y_min": round(min(ys), 3),
                             "x_max": round(max(xs), 3), "y_max": round(max(ys), 3)}
            entry["centroid"] = {"x_mm": round(sum(xs) / len(xs), 3),
                                 "y_mm": round(sum(ys) / len(ys), 3)}
        out[name] = entry
    return {"groups": [out[name] for name in GROUP_ORDER if name in out],
            "adopted": adopted,
            "unclassified": sorted(str(fp.get("ref") or "") for fp in unclassified),
            "how": ("the part with the most pads (>= %d) is mcu; then the net-name "
                    "hint with the most hits wins (%s); a part touching only rails "
                    "is power; unmatched parts are adopted into the nearest group "
                    "within %s mm"
                    % (CONTROLLER_PADS, ", ".join(n for n, _ in FUNCTION_HINTS),
                       ADOPT_MM)),
            "controller": controller}


def topology(board):
    """Groups, how they are wired to each other, and which way power flows."""
    got = groups(board)
    fps = _fps(board)
    group_of = {}
    for entry in got["groups"]:
        for ref in entry["refs"]:
            group_of[ref] = entry["name"]

    shared = {}
    for fp in fps:
        ref = str(fp.get("ref") or "")
        mine = group_of.get(ref)
        if not mine:
            continue
        for net in _net_of(fp):
            shared.setdefault(net, set()).add(mine)
    links = {}
    for net, sides in shared.items():
        if len(sides) < 2:
            continue
        for a in sides:
            for b in sides:
                if a >= b:
                    continue
                key = (a, b)
                row = links.setdefault(key, {"a": a, "b": b, "shared_nets": 0,
                                            "nets": []})
                row["shared_nets"] += 1
                if len(row["nets"]) < 8:
                    row["nets"].append(net)

    ordered = sorted(links.values(), key=lambda r: -r["shared_nets"])
    flow = _flow(got["groups"], ordered)
    return {"groups": got["groups"], "links": ordered,
            "flow": flow, "adopted": got["adopted"],
            "unclassified": got["unclassified"],
            "how": got["how"],
            "coordinate_system": "canonical (mm, Y up)",
            "next": ("place group by group against this flow: power entry first, "
                     "then the groups in flow order. Coordinates come after the "
                     "partition, which is the step a layout that 'looks wrong' "
                     "usually skipped")}


def _flow(group_rows, links):
    names = [g["name"] for g in group_rows]
    if not names:
        return {"source": None, "order": [], "note": "no groups"}
    source = "power" if "power" in names else names[0]
    adjacency = {}
    for row in links:
        adjacency.setdefault(row["a"], []).append((row["b"], row["shared_nets"]))
        adjacency.setdefault(row["b"], []).append((row["a"], row["shared_nets"]))
    seen = {source: 0}
    order = [source]
    frontier = [source]
    while frontier:
        nxt = []
        for name in frontier:
            for other, weight in sorted(adjacency.get(name, []),
                                        key=lambda pair: -pair[1]):
                if other in seen:
                    continue
                seen[other] = seen[name] + 1
                order.append(other)
                nxt.append(other)
        frontier = nxt
    missing = [n for n in names if n not in seen]
    return {"source": source, "order": order, "hops": seen,
            "unreached": missing,
            "note": ("breadth-first over the group links, strongest link first; a "
                     "group with no shared net to the rest is listed as unreached "
                     "rather than placed at hop 0")}


# ------------------------------------------------------------------ metrics
def _one(board):
    """Every reading, with the number that produced it. No percentiles here."""
    fps = _fps(board)
    refs = [str(fp.get("ref") or "") for fp in fps]
    out = {"board": (getattr(board, "source", None) or {}).get("path"),
           "footprints": len(fps)}

    if len(fps) < 2:
        out["spacing_median_mm"] = {"value": None, "unit": "mm",
                                    "why": "fewer than two placed footprints"}
    else:
        nearest = []
        for i, a in enumerate(fps):
            best = None
            for j, b in enumerate(fps):
                if i == j:
                    continue
                distance = math.hypot(float(a["x"]) - float(b["x"]),
                                      float(a["y"]) - float(b["y"]))
                if best is None or distance < best:
                    best = distance
            nearest.append(best)
        out["spacing_median_mm"] = _spread(
            nearest, "spacing", "mm", "no_direction",
            "distance from each footprint origin to its nearest neighbour origin",
            crowded_share=round(_share(nearest, lambda v: v < CROWDED_MM), 4),
            lonely_share=round(_share(nearest, lambda v: v > LONELY_MM), 4),
            thresholds={"crowded_mm": CROWDED_MM, "lonely_mm": LONELY_MM})
        if len(nearest) >= 2:
            mean = sum(nearest) / float(len(nearest))
            variance = sum((v - mean) ** 2 for v in nearest) / float(len(nearest))
            out["spacing_cv"] = {
                "value": round((variance ** 0.5) / mean, 4) if mean else None,
                "unit": "ratio", "better_when": "lower",
                "how": "coefficient of variation of the nearest-neighbour distances: "
                       "one part alone on a 200 mm board and one part alone in a "
                       "corner have the same median and a very different rhythm"}

    angles = [float(fp.get("rotation") or 0.0) % 360.0 for fp in fps]
    if angles:
        def _off_axis(angle):
            folded = angle % 180.0
            return min(abs(folded - 0.0), abs(folded - 90.0),
                       abs(folded - 180.0))
        on_axis = [a for a in angles if _off_axis(a) <= ON_AXIS_TOL_DEG]
        buckets = {}
        for a in angles:
            key = int(round((a % 180.0) / 90.0)) % 2
            buckets[key] = buckets.get(key, 0) + 1
        out["orientation_on_axis_share"] = {
            "value": round(len(on_axis) / float(len(angles)), 4),
            "unit": "share", "better_when": "higher",
            "count": len(angles),
            "tolerance_deg": ON_AXIS_TOL_DEG,
            "how": "share of footprints whose rotation is within tolerance of "
                   "0/90/180/270; a board where parts sit at 3.7 degrees looks "
                   "hand-nudged even when nothing violates clearance"}

    if len(fps) >= 2:
        aligned = set()
        for i, a in enumerate(fps):
            for j, b in enumerate(fps):
                if i >= j:
                    continue
                dx = abs(float(a["x"]) - float(b["x"]))
                dy = abs(float(a["y"]) - float(b["y"]))
                if (dx <= ALIGN_TOL_MM and dy <= ALIGN_REACH_MM) or \
                        (dy <= ALIGN_TOL_MM and dx <= ALIGN_REACH_MM):
                    aligned.add(i)
                    aligned.add(j)
        out["alignment_share"] = {
            "value": round(len(aligned) / float(len(fps)), 4),
            "unit": "share", "better_when": "higher",
            "tolerance_mm": ALIGN_TOL_MM, "reach_mm": ALIGN_REACH_MM,
            "how": "share of footprints that line up with at least one other on an "
                   "axis; people read 'lined up' long before they read a rule"}

    box = _outline_bbox(board)
    margins = []
    if box:
        x0, y0, x1, y1 = box
        for fp in fps:
            fx0, fy0, fx1, fy1 = _fp_bbox(fp)
            margins.append(min(fx0 - x0, fy0 - y0, x1 - fx1, y1 - fy1))
        out["edge_margin_median_mm"] = _spread(
            margins, "margin", "mm", "no_direction",
            "distance from each footprint bbox to the board outline, on its closest "
            "side; a negative number means the part sticks out",
            negative_share=round(_share(margins, lambda v: v < 0), 4))
    else:
        out["edge_margin_median_mm"] = {
            "value": None, "unit": "mm", "why": "the board has no closed outline"}

    if fps:
        spans = [(float(fp["x"]), float(fp["y"])) for fp in fps]
        xs = [p[0] for p in spans]
        ys = [p[1] for p in spans]
        x0, x1 = (box[0], box[2]) if box else (min(xs), max(xs))
        y0, y1 = (box[1], box[3]) if box else (min(ys), max(ys))
        area = max(1e-6, (x1 - x0) * (y1 - y0))
        # one part per cell, clamped: the grid has to be a measuring instrument for
        # this board, not a fixed ruler that calls every small board collapsed
        cell = max(GRID_MM_MIN, min(GRID_MM_MAX, 2.0 * math.sqrt(area / len(spans))))
        cols = max(1, int(math.ceil((x1 - x0) / cell)))
        rows = max(1, int(math.ceil((y1 - y0) / cell)))
        grid = [[0] * cols for _ in range(rows)]
        for x, y in spans:
            cx = min(cols - 1, max(0, int((x - x0) / cell)))
            cy = min(rows - 1, max(0, int((y - y0) / cell)))
            grid[cy][cx] += 1
        cells = [n for row in grid for n in row]
        mean = sum(cells) / float(len(cells)) if cells else 0.0
        if mean:
            variance = sum((c - mean) ** 2 for c in cells) / float(len(cells))
            out["density_cv"] = {
                "value": round((variance ** 0.5) / mean, 4),
                "unit": "ratio", "better_when": "lower",
                "grid_mm": round(cell, 3), "cells": len(cells),
                "parts_per_cell": round(mean, 3),
                "how": "coefficient of variation of footprint count over a grid "
                       "whose cell is sized to about one part per cell (2*sqrt(area/"
                       "parts), clamped to %s..%s mm): an empty half with everything "
                       "jammed in one corner scores high even when every clearance "
                       "passes" % (GRID_MM_MIN, GRID_MM_MAX)}
            out["density_empty_share"] = {
                "value": round(_share(cells, lambda c: c == 0), 4),
                "unit": "share", "better_when": "no_direction",
                "grid_mm": round(cell, 3),
                "how": "share of grid cells with no footprint on them, on the same "
                       "cell as density_cv"}

    got = groups(board)
    sizes = [len(g["refs"]) for g in got["groups"] if g["name"] != "misc"]
    if len(sizes) >= 2 and len(fps) >= 3:
        cohesion = []
        for g in got["groups"]:
            if g["name"] == "misc" or len(g["refs"]) < 2:
                continue
            rows = [fp for fp in fps if str(fp.get("ref") or "") in set(g["refs"])]
            for i, a in enumerate(rows):
                best = None
                for j, b in enumerate(rows):
                    if i == j:
                        continue
                    distance = math.hypot(float(a["x"]) - float(b["x"]),
                                          float(a["y"]) - float(b["y"]))
                    if best is None or distance < best:
                        best = distance
                if best is not None:
                    cohesion.append(best)
        inside = _median(cohesion)
        between = []
        for i, a in enumerate(got["groups"]):
            for b in got["groups"]:
                if a["name"] >= b["name"] or not a["refs"] or not b["refs"]:
                    continue
                pa = [fp for fp in fps if str(fp.get("ref") or "") in set(a["refs"])]
                pb = [fp for fp in fps if str(fp.get("ref") or "") in set(b["refs"])]
                best = None
                for one in pa:
                    for two in pb:
                        distance = math.hypot(float(one["x"]) - float(two["x"]),
                                              float(one["y"]) - float(two["y"]))
                        if best is None or distance < best:
                            best = distance
                if best is not None:
                    between.append(best)
        gap = _median(between)
        out["cluster_separation_ratio"] = {
            "value": (round(gap / inside, 4) if inside and gap else None),
            "unit": "ratio", "better_when": "higher",
            "inside_median_mm": round(inside, 3) if inside else None,
            "between_median_mm": round(gap, 3) if gap else None,
            "how": "nearest-neighbour distance inside a functional group against the "
                   "closest distance between groups: a large number means the groups "
                   "are tight and separated, which is what 'organised' looks like"}
    else:
        out["cluster_separation_ratio"] = {
            "value": None, "why": "fewer than two functional groups with two parts"}

    out["thresholds"] = {
        "crowded_mm": CROWDED_MM, "lonely_mm": LONELY_MM,
        "align_tol_mm": ALIGN_TOL_MM, "align_reach_mm": ALIGN_REACH_MM,
        "grid_mm_range": [GRID_MM_MIN, GRID_MM_MAX],
        "on_axis_tol_deg": ON_AXIS_TOL_DEG,
        "adopt_mm": ADOPT_MM,
        "note": "these are the assumptions of the judgements, not measurements of "
                "the world; changing one changes every share below it",
    }
    out["refs"] = refs
    return out


PERCENTILE_KEYS = ("spacing_median_mm", "spacing_cv", "orientation_on_axis_share",
                   "alignment_share", "edge_margin_median_mm", "density_cv",
                   "density_empty_share", "cluster_separation_ratio")


def metrics(board, reference=None):
    """One board's readings, and - only when a reference set is given - its place.

    A percentile without a corpus is a number with nothing behind it, so with no
    reference this returns absolute readings and says the comparison was not made.
    With fewer than five reference boards it still reports a percentile but marks
    it as not usable for a judgement.
    """
    one = _one(board)
    out = {"board": one.get("board"), "footprints": one.get("footprints"),
           "metrics": one, "reference": None}
    boards = [b for b in (reference or []) if b is not None]
    if not boards:
        out["why_no_percentile"] = ("no reference set was given: the readings are "
                                   "absolute, and 'is this far from normal' was not "
                                   "answered")
        return out
    series = {key: [] for key in PERCENTILE_KEYS}
    own_refs = set(one.get("refs") or [])
    clones = 0
    for other in boards:
        reading = _one(other)
        if set(reading.get("refs") or []) == own_refs:
            clones += 1
        for key in PERCENTILE_KEYS:
            row = reading.get(key) or {}
            value = row.get("value")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                series[key].append(float(value))
    n = len(boards)
    # A reference set made of copies of the same board (a backup folder) has the
    # same parts and nearly the same numbers: a percentile against it is arithmetic
    # about copies, so it is reported but never called usable.
    all_clones = bool(boards) and clones == len(boards)
    reliable = n >= 5 and not all_clones
    filled = {}
    for key in PERCENTILE_KEYS:
        row = one.get(key)
        if not isinstance(row, dict):
            continue
        value = row.get("value")
        values = series[key]
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not values:
            row["reference_n"] = len(values)
            continue
        value = float(value)
        better = row.get("better_when", "no_direction")
        values_sorted = sorted(values)
        ref_median = _median(values)
        if better in ("higher", "lower"):
            if better == "higher":
                share = sum(1 for v in values if v <= value) / float(len(values))
            else:
                share = sum(1 for v in values if v >= value) / float(len(values))
            row["percentile"] = round(100.0 * share, 1)
            filled[key] = row["percentile"]
        else:
            # No direction means no quality claim: this reports where the board sits
            # in the distribution and does NOT appear in the "at the edge" lists.
            share = sum(1 for v in values if v <= value) / float(len(values))
            row["position_pct"] = round(100.0 * share, 1)
            row["reference_median"] = round(ref_median, 4) if ref_median else None
            if ref_median:
                row["ratio_to_reference_median"] = round(value / ref_median, 4)
        row["reference_n"] = len(values)
        if not reliable:
            row["percentile_note"] = (
                "not usable for a judgement: %s"
                % ("every reference board carries the same references as this one - "
                   "a backup folder is copies, not a corpus"
                   if all_clones else "fewer than five reference boards"))
    out["reference"] = {"boards": n, "usable_for_judgement": reliable,
                       "same_reference_set_as_this_board": clones,
                       "note": ("percentile = share of the reference set this board "
                                "is at least as good as, in the direction the metric "
                                "declares; with 'no_direction' it is only the "
                                "position in the distribution")}
    out["below_reference_p10"] = sorted(key for key in filled if filled[key] <= 10.0)
    out["above_reference_p90"] = sorted(key for key in filled if filled[key] >= 90.0)
    return out
