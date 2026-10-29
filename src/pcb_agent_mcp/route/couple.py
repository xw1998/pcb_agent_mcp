"""Coupled diff-pair routing: build one net's copper as the other's, offset by the gap.

The maze search routes one net at a time and knows nothing about pairs, so until
now the pair could only promise "these two are at least `gap` apart" and then
measure what came out - on the F401 core board that was 1.65 mm where 0.2 mm was
asked for, reported honestly as `coupled: false`. This module does the actual
thing: it takes the reference net's finished path and builds the second net as
that path offset to one side by `gap + width`, corners mitered.

Four deliberate properties:

* **The reference net is not re-routed.** Whatever the search produced (its vias,
  its layer changes) stays exactly as it is; only the second net is replaced.
  Re-routing both would be a different, far larger algorithm, and it would also
  make "the pair failed to couple" un-attributable.
* **Every offset segment goes back through the exact check.** An offset that lands
  on somebody else's copper is not written; the pair then keeps the independent
  routing it already had and reports the blocker by name.
* **A miter corner is exactly the gap away.** The offset of two lines meeting at a
  corner is their intersection, which is equidistant from both, so the gap holds
  through the corner and not only on straight runs. That is why the tolerance in
  `tune.GAP_TOL_MM` can be 0.02 mm rather than "about right". A corner too sharp
  for a miter (the intersection runs off as the angle closes) is bevelled, which
  is *wider* than the gap, never narrower.
* **A reference net that branches is refused, not guessed at.** See `_chain`.

Still not here: no impedance solve on the pair, and the fanout from each pad to
the coupled run is a plain straight dump (which is what a person does too).
"""

import math

from .. import geometry as G

# A miter longer than this many times the offset becomes a bevelled corner. At 90
# degrees the miter is sqrt(2)*offset; the limit only bites past ~40 degrees, where
# a miter is a spike.
MITRE_LIMIT = 3.0
# Coplanar offset arithmetic is exact to floating point; this only absorbs the
# difference between "distance to a centreline" and the required edge-to-edge gap.
EPS_MM = 1e-4
# How far each pad may be from the end of the coupled run it has to join. The
# fanout is a dead-straight dump (that is what a person does too), so a pair whose
# coupled run starts 12 mm away from the pad is not a coupled pair - it is two
# nets being joined by a diagonal nobody asked for. Past this, coupling is refused
# and the independent routing stands, which is the honest answer.
COUPLE_MAX_FANOUT_MM = 5.0


def _unit(a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = math.hypot(dx, dy)
    if n < 1e-12:
        return None
    return dx / n, dy / n


def offset_polyline(pts, dist, side=1):
    """`pts` shifted `dist` to the left (`side=1`) or right (`side=-1`).

    Corners are mitered; a miter longer than `MITRE_LIMIT * dist` becomes two
    points (a bevel). `None` when the polyline has a repeated point, because a
    zero-length segment has no normal and guessing one puts copper nowhere in
    particular.
    """
    if len(pts) < 2 or dist <= 0:
        return None
    offs = []
    for k in range(len(pts) - 1):
        u = _unit(pts[k], pts[k + 1])
        if u is None:
            return None
        nx, ny = -u[1] * side * dist, u[0] * side * dist
        offs.append(((pts[k][0] + nx, pts[k][1] + ny),
                     (pts[k + 1][0] + nx, pts[k + 1][1] + ny), u))
    out = [offs[0][0]]
    for k in range(len(offs) - 1):
        a1, b1, d1 = offs[k]
        a2, b2, d2 = offs[k + 1]
        den = d1[0] * d2[1] - d1[1] * d2[0]
        mitre = None
        if abs(den) > 1e-9:
            t = ((a2[0] - a1[0]) * d2[1] - (a2[1] - a1[1]) * d2[0]) / den
            cand = (a1[0] + d1[0] * t, a1[1] + d1[1] * t)
            if math.hypot(cand[0] - pts[k + 1][0],
                          cand[1] - pts[k + 1][1]) <= MITRE_LIMIT * dist:
                mitre = cand
        if mitre is not None:
            out.append(mitre)
        else:
            out.append(b1)
            out.append(a2)
    out.append(offs[-1][1])
    # Collapse duplicates a collinear join can produce.
    clean = [out[0]]
    for p in out[1:]:
        if math.hypot(p[0] - clean[-1][0], p[1] - clean[-1][1]) > 1e-9:
            clean.append(p)
    return clean if len(clean) >= 2 else None


def _chain(ops, net, start_xy, tol_mm):
    """Walk one net's copper end to end: `[(layer, [pt, pt, ...]), ...]`.

    A run is a stretch on one layer. Between runs the net used a via, and the walk
    records that by starting a new run.

    Returns `(runs, None)` or `(None, reason)`. A branch, a loop, or copper the
    walk cannot account for is a refusal: coupling off a path we cannot walk is a
    guess, and that guess puts a second net's copper in the wrong place while
    claiming it is coupled.
    """
    tracks = [o for o in ops if o.get("op") == "add_track" and o.get("net") == net]
    vias = [o for o in ops if o.get("op") == "add_via" and o.get("net") == net]
    if not tracks:
        return None, "reference_net_has_no_copper"

    def near(p, q):
        return math.hypot(p[0] - q[0], p[1] - q[1]) <= tol_mm

    best = None
    for k, tr in enumerate(tracks):
        for end in (0, 1):
            a = (tr["x1"], tr["y1"]) if end == 0 else (tr["x2"], tr["y2"])
            d = math.hypot(a[0] - start_xy[0], a[1] - start_xy[1])
            if best is None or d < best[0]:
                best = (d, k, end)
    k, end = best[1], best[2]
    tr = tracks[k]
    used = {k}
    cur = ((tr["x1"], tr["y1"]) if end == 0 else (tr["x2"], tr["y2"]))
    far = ((tr["x2"], tr["y2"]) if end == 0 else (tr["x1"], tr["y1"]))
    layer = tr["layer"]
    runs = [[layer, [cur, far]]]
    cur = far
    via_used = set()
    while True:
        step = None
        for j, cand in enumerate(tracks):
            if j in used or cand["layer"] != layer:
                continue
            if near((cand["x1"], cand["y1"]), cur):
                step = (j, (cand["x2"], cand["y2"]))
                break
            if near((cand["x2"], cand["y2"]), cur):
                step = (j, (cand["x1"], cand["y1"]))
                break
        if step is not None:
            used.add(step[0])
            cur = step[1]
            runs[-1][1].append(cur)
            continue
        hop = None
        for j, v in enumerate(vias):
            if j in via_used or not near((v["x"], v["y"]), cur):
                continue
            for cand in tracks:
                if cand["layer"] == layer:
                    continue
                if near((cand["x1"], cand["y1"]), cur) or near((cand["x2"], cand["y2"]), cur):
                    hop = (j, cand["layer"])
                    break
            if hop:
                break
        if hop is None:
            break
        via_used.add(hop[0])
        layer = hop[1]
        runs.append([layer, [cur]])
    if len(used) != len(tracks):
        return None, "reference_net_branches"
    for r in runs:
        if len(r[1]) < 2:
            return None, "reference_net_has_a_zero_length_run"
    return runs, None


def coupled_path(runs, dist, side):
    """Offset every run and join the runs into one sequence of track pieces.

    Returns `(pieces, vias, None)` with `pieces = [(layer, a, b), ...]` and
    `vias = [(x, y, from_layer, to_layer)]`, or `(None, None, reason)`.
    """
    offs = []
    for layer, pts in runs:
        off = offset_polyline(pts, dist, side)
        if off is None:
            return None, None, "offset_degenerate"
        offs.append((layer, off))
    pieces = []
    vias = []
    for layer, off in offs:
        for k in range(len(off) - 1):
            pieces.append((layer, off[k], off[k + 1]))
    for k in range(len(offs) - 1):
        la, oa = offs[k]
        lb, ob = offs[k + 1]
        vias.append((oa[-1][0], oa[-1][1], la, lb))
        # The next run starts where its own offset puts it, not where the previous
        # one ended, so a short jog on the new layer closes the gap. Dropping this
        # would leave N's copper in two pieces with a via touching neither.
        if math.hypot(ob[0][0] - oa[-1][0], ob[0][1] - oa[-1][1]) > 1e-9:
            pieces.append((lb, oa[-1], ob[0]))
    return pieces, vias, None


def coupled_route(em, bundle, pair, terms_p, terms_n, tol_mm=0.05):
    """Replace the second net's copper with the first's, offset by the gap.

    On success the caller's `em` is modified: the second net's old ops are gone and
    the coupled copper is in. On failure the old copper is restored byte for byte
    (`em.ops` + `em.rebuild()`), so a refused coupling cannot silently delete a
    routed net.
    """
    from . import rules as R

    p, n = pair.get("p"), pair.get("n")
    gap = pair.get("gap_mm")
    width = pair.get("width_mm") or R.for_net(bundle, n)["track_mm"]
    out = {"p": p, "n": n, "gap_mm": gap, "width_mm": width, "ok": False,
           "coupled": False}
    if not p or not n or gap is None:
        out["reason"] = "pair_incomplete"
        return out
    if len(terms_p or []) != 2 or len(terms_n or []) != 2:
        out["reason"] = "pair_needs_two_pads_each"
        out["detail"] = {"p_pads": len(terms_p or []), "n_pads": len(terms_n or [])}
        return out

    runs, why = _chain(em.ops, p, tuple(terms_p[0]["at"]), tol_mm)
    if runs is None:
        out["reason"] = why
        return out

    # Centre distance that makes the EDGE distance equal the declared gap. Both
    # members carry the same width (rules forced that), so this is gap + width.
    dist = float(gap) + float(width) + EPS_MM

    saved = [op for op in em.ops if op.get("net") == n]
    saved_len = em.length.get(n, 0.0)

    cands = []
    too_long = []
    for side in (1, -1):
        pieces, vias, why = coupled_path(runs, dist, side)
        if pieces is None:
            continue
        # Two ways to join the pads to the coupled run (which end goes to which
        # pad); take the cheaper one, then check it is short enough to be a fanout
        # at all.
        fan_a = (math.hypot(pieces[0][1][0] - terms_n[0]["at"][0],
                            pieces[0][1][1] - terms_n[0]["at"][1]),
                 math.hypot(pieces[-1][2][0] - terms_n[1]["at"][0],
                            pieces[-1][2][1] - terms_n[1]["at"][1]))
        fan_b = (math.hypot(pieces[0][1][0] - terms_n[1]["at"][0],
                            pieces[0][1][1] - terms_n[1]["at"][1]),
                 math.hypot(pieces[-1][2][0] - terms_n[0]["at"][0],
                            pieces[-1][2][1] - terms_n[0]["at"][1]))
        fan = fan_a if sum(fan_a) <= sum(fan_b) else fan_b
        if max(fan) > COUPLE_MAX_FANOUT_MM:
            too_long.append({"side": side,
                             "fanout_mm": [round(fan[0], 4), round(fan[1], 4)]})
            continue
        cands.append((sum(fan), side, pieces, vias))
    if not cands:
        if too_long:
            out["reason"] = "fanout_too_long"
            out["detail"] = {"limit_mm": COUPLE_MAX_FANOUT_MM,
                             "sides": too_long}
            return out
        out["reason"] = "offset_degenerate"
        return out
    cands.sort(key=lambda c: c[0])

    # The second net's own copper must not be in the map while its replacement is
    # being checked, or the check answers about the copper being replaced.
    for op in saved:
        em.ops.remove(op)
    em.rebuild()
    em.length[n] = 0.0

    tried = []
    for _span, side, pieces, vias in cands:
        ok, info = _emit(em, n, pieces, vias, width, terms_n)
        if ok:
            out.update({"ok": True, "coupled": True, "side": side,
                        "coupled_length_mm": round(info["length"], 4),
                        "fanout_mm": [round(x, 4) for x in info["fanout"]],
                        "segments": info["segments"], "vias": info["vias"],
                        "replaced_segments": len(saved)})
            return out
        info = dict(info)
        info["side"] = side
        tried.append(info)

    for op in saved:
        em.ops.append(op)
    em.rebuild()
    em.length[n] = saved_len
    out["reason"] = "offset_blocked"
    # Both sides are reported: which side is blocked and by whom is the whole
    # instruction ("move U2's pad 1", not "coupling failed"). Keeping only the
    # last attempt names the wrong blocker half the time.
    out["detail"] = {"sides": tried}
    return out


def _emit(em, net, pieces, vias, width, terms_n):
    """Write the offset pieces + fanouts, but only if every piece passes exactly."""
    # Fanout: pad -> first offset point, last offset point -> pad. Both are on the
    # layer of the run they attach to, so a pad that does not sit on that layer is
    # a refusal rather than a silent via-less corner.
    first_layer = pieces[0][0]
    last_layer = pieces[-1][0]
    if first_layer not in terms_n[0]["layers"] or last_layer not in terms_n[1]["layers"]:
        return False, {"reason": "fanout_layer_mismatch",
                       "pad_layers": [list(terms_n[0]["layers"]),
                                      list(terms_n[1]["layers"])],
                       "path_layers": [first_layer, last_layer]}
    # Normalise to `(label, layer, a, b)`: `coupled_path` hands back
    # `(layer, a, b)` because it does not care which piece is a fanout, and the
    # fanouts are the only pieces that need a label.
    all_pieces = ([("fanout", first_layer, tuple(terms_n[0]["at"]), pieces[0][1])]
                  + [("body",) + tuple(pc) for pc in pieces]
                  + [("fanout", last_layer, pieces[-1][2], tuple(terms_n[1]["at"]))])

    for _label, layer, a, b in all_pieces:
        if math.hypot(b[0] - a[0], b[1] - a[1]) < 1e-9:
            continue
        bad = em.grid.seg_blockers(a, b, layer, net, width)
        if bad:
            return False, {"reason": "blocked", "layer": layer,
                           "at": [round(a[0], 4), round(a[1], 4)],
                           "detail": bad[0]}
    for x, y, _la, _lb in vias:
        if em.grid.via_blockers(x, y, net):
            return False, {"reason": "via_blocked",
                           "at": [round(x, 4), round(y, 4)]}

    # Written straight into the map, the same way the length-matching pass writes
    # its meanders: this runs on finished copper, so there is no transaction to
    # keep and `_Emitter`'s buffer (which commits per net) would hold the ops
    # forever.
    from . import rules as R

    spec = R.for_net(em.bundle, net)
    via_dia = spec["via_diameter_mm"]
    length = 0.0
    nseg = 0
    for _label, layer, a, b in all_pieces:
        if math.hypot(b[0] - a[0], b[1] - a[1]) < 1e-9:
            continue
        em.grid.add_track(net, layer, a, b, width)
        em.ops.append({"op": "add_track", "x1": round(a[0], 6),
                       "y1": round(a[1], 6), "x2": round(b[0], 6),
                       "y2": round(b[1], 6), "width": width, "layer": layer,
                       "net": net})
        nseg += 1
        length += math.hypot(b[0] - a[0], b[1] - a[1])
    for x, y, _la, _lb in vias:
        em.grid.add_via(net, x, y, via_dia)
        em.ops.append({"op": "add_via", "x": round(x, 6), "y": round(y, 6),
                       "net": net, "size": spec["via_diameter_mm"],
                       "drill": spec["via_drill_mm"]})
    em.length[net] = em.length.get(net, 0.0) + length
    fanout = [math.hypot(pieces[0][1][0] - terms_n[0]["at"][0],
                         pieces[0][1][1] - terms_n[0]["at"][1]),
              math.hypot(pieces[-1][2][0] - terms_n[1]["at"][0],
                         pieces[-1][2][1] - terms_n[1]["at"][1])]
    return True, {"length": length, "segments": nseg, "vias": len(vias),
                  "fanout": fanout}


def coupled_fraction(ops, p, n, gap, tol=0.05):
    """How much of the second net really runs at the declared gap from the first.

    A pair can be "coupled" at the fanout and nowhere else, and reporting only the
    closest approach would call that coupled. This walks the second net's segments
    and asks, for each, how far it is from the reference net's copper: the fraction
    of length within `tol` of `gap` is the number that means something.
    """
    segs_p = [((o["x1"], o["y1"]), (o["x2"], o["y2"]), float(o["width"]))
              for o in ops if o.get("op") == "add_track" and o.get("net") == p]
    segs_n = [((o["x1"], o["y1"]), (o["x2"], o["y2"]), float(o["width"]))
              for o in ops if o.get("op") == "add_track" and o.get("net") == n]
    if not segs_p or not segs_n:
        return None
    total = 0.0
    at_gap = 0.0
    for a, b, wa in segs_n:
        length = math.hypot(b[0] - a[0], b[1] - a[1])
        if length < 1e-9:
            continue
        total += length
        g = None
        for c, d, wb in segs_p:
            v = G.dist_segment_segment(a, b, c, d) - (wa + wb) / 2.0
            if g is None or v < g:
                g = v
        if g is not None and abs(g - gap) <= tol:
            at_gap += length
    if total <= 0:
        return None
    return at_gap / total
