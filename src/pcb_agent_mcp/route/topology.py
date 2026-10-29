"""Which pairs of a net's pads to connect: a plain MST, or a Steiner tree.

`route.grid.mst_edges` answers "connect these points with the shortest tree that
joins every pair", which is the right question for two or three pads and a
strictly suboptimal one for a star of four or more: a minimum spanning tree of
pads is restricted to bend at pads, while copper is free to bend wherever it
likes.  The classic example is three pads at (0,0), (2,2), (4,0): the spanning
tree costs 4+4 = 8 mm, and a tree that branches at (2,0) - which is not a pad -
costs 2+2+2 = 6 mm.

This module adds that second answer.  What it is NOT:

  * not an exact rectilinear Steiner minimal tree.  Exact RSMT is NP-hard and
    the reference implementations are elaborate; what is here is the iterated
    1-Steiner heuristic of Kahng & Robins on the Hanan grid (every x of a
    terminal crossed with every y of a terminal), one candidate per round, and
    a round only happens when it strictly shortens the tree.  So the reported
    length is an upper bound on RSMT and a lower bound on MST: it can never be
    worse than what `mst` would have done, and it may leave savings on the
    table;
  * not the length the router will actually draw.  Lengths here are Manhattan
    (the search's own metric), measured pad-centre to pad-centre, before the
    search has avoided anything.  The router reports what it really laid down;
  * not free.  Each extra branch point is one more A* search, so the heuristic
    is only asked for when `topology="steiner"` is requested, and the saving it
    found is reported so a caller can decide whether it was worth the time.
"""
import math

DEFAULT_MAX_ROUNDS = 8
MAX_CANDIDATES = 400          # Hanan grid beyond this is sampled, and says so
_EPS = 1e-9


def _dist_mst(a, b):
    """Manhattan - the metric the A* search actually pays."""
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def mst_length(points, metric="manhattan"):
    """Prim over a small point set, returning the total tree length only."""
    n = len(points)
    if n < 2:
        return 0.0
    d = _dist_mst if metric == "manhattan" else (
        lambda a, b: math.hypot(a[0] - b[0], a[1] - b[1]))
    used = [False] * n
    used[0] = True
    best = [None] * n
    for k in range(1, n):
        best[k] = d(points[k], points[0])
    total = 0.0
    for _ in range(n - 1):
        pick, bd = None, None
        for k in range(n):
            if used[k] or best[k] is None:
                continue
            if bd is None or best[k] < bd:
                pick, bd = k, best[k]
        if pick is None:
            break
        total += bd
        used[pick] = True
        for k in range(n):
            if used[k]:
                continue
            dd = d(points[k], points[pick])
            if best[k] is None or dd < best[k]:
                best[k] = dd
    return total


def _spanning_edges(points):
    """Prim, returning `[(a, b), ...]` index pairs and the length."""
    n = len(points)
    if n < 2:
        return [], 0.0
    used = [False] * n
    used[0] = True
    best = [None] * n
    for k in range(1, n):
        best[k] = (_dist_mst(points[k], points[0]), 0)
    out = []
    total = 0.0
    while len(out) < n - 1:
        pick, bd = None, None
        for k in range(n):
            if used[k] or best[k] is None:
                continue
            if bd is None or best[k][0] < bd:
                pick, bd = k, best[k][0]
        if pick is None:
            break
        out.append((best[pick][1], pick))
        total += bd
        used[pick] = True
        for k in range(n):
            if used[k]:
                continue
            dd = _dist_mst(points[k], points[pick])
            if best[k] is None or dd < best[k][0]:
                best[k] = (dd, pick)
    return out, total


def hanan_candidates(terminals, limit=MAX_CANDIDATES):
    """Every (x of a terminal) x (y of a terminal) that is not a terminal.

    Returned sorted, and deterministically thinned when the grid is too big -
    the report carries the count that was actually tried, so a saving that was
    missed because the grid was sampled is visible rather than silent.
    """
    xs = sorted({round(p[0], 6) for p in terminals})
    ys = sorted({round(p[1], 6) for p in terminals})
    have = {(round(p[0], 6), round(p[1], 6)) for p in terminals}
    out = [(x, y) for x in xs for y in ys if (x, y) not in have]
    truncated = len(out) > limit
    if truncated:
        step = len(out) / float(limit)
        out = [out[int(k * step)] for k in range(limit)]
    return out, truncated


def steiner_points(terminals, max_rounds=DEFAULT_MAX_ROUNDS):
    """Iterated 1-Steiner: add the branch point that shortens the tree most.

    Returns `(points, mst_mm, steiner_mm, truncated)` where `points` are the
    chosen branch points (possibly none) in the order they were added.
    """
    base = [(float(p[0]), float(p[1])) for p in terminals]
    start = mst_length(base)
    chosen = []
    cur = start
    truncated = False
    for _ in range(int(max_rounds)):
        cands, cut = hanan_candidates(base + chosen)
        truncated = truncated or cut
        best = None
        for c in cands:
            trial = mst_length(base + chosen + [c])
            if trial < cur - _EPS and (best is None or trial < best[0] - _EPS):
                best = (trial, c)
        if best is None:
            break
        cur = best[0]
        chosen.append(best[1])
    return chosen, start, cur, truncated


def tree(points, mode="mst", max_rounds=DEFAULT_MAX_ROUNDS):
    """The connectivity plan for one net.

    `mode="mst"` is exactly the old behaviour: spanning tree over the pad
    centres, Euclidean lengths (that is what the caller's net ordering has
    always been based on).  `mode="steiner"` returns pad centres plus branch
    points, spanning tree over that enlarged set, Manhattan lengths.

    In both cases `edges` index into `points`, so a caller that treats indices
    >= len(input) as branch points routes to them like any other node - there is
    simply no pad there.
    """
    pts = [(float(p[0]), float(p[1])) for p in points]
    if mode not in ("mst", "steiner"):
        raise ValueError("unknown topology %r (mst or steiner)" % (mode,))
    if mode == "mst" or len(pts) < 3:
        edges, _ = _spanning_edges(pts)
        total = sum(math.hypot(pts[a][0] - pts[b][0], pts[a][1] - pts[b][1])
                    for a, b in edges)
        return {"mode": "mst" if mode == "mst" else "steiner",
                "points": pts, "edges": edges, "steiner": [],
                "length_mm": round(total, 4),
                "mst_length_mm": round(total, 4),
                "saved_mm": 0.0,
                "note": ("a tree of fewer than three pads has nothing to "
                         "branch, so mst was used"
                         if mode == "steiner" else None),
                "hanan_truncated": False}
    extra, mst_mm, st_mm, truncated = steiner_points(pts, max_rounds=max_rounds)
    allpts = pts + extra
    edges, total = _spanning_edges(allpts)
    return {"mode": "steiner", "points": allpts, "edges": edges,
            "steiner": extra,
            "length_mm": round(total, 4),
            "mst_length_mm": round(mst_mm, 4),
            "saved_mm": round(mst_mm - total, 4),
            "note": ("iterated 1-Steiner on the Hanan grid - an upper bound on "
                     "the exact rectilinear Steiner tree, never worse than mst"),
            "hanan_truncated": bool(truncated)}
