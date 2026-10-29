"""Geometry primitives shared by the adapters and the Oracle.

Everything here is 2D, millimetres, Y up (the canonical frame). No numpy, no
shapely: the operations the Oracle needs are point/segment/polygon distances and
an occupancy grid, and each one is short enough to be verifiable by reading it.

Every function is exact for what it claims. Where an approximation is used the
caller is told so by the Oracle's `precision` field, not by a comment here.
"""
import math

EPS = 1e-9


# ----------------------------------------------------------------- segments
def dist_point_point(p, q):
    return math.hypot(p[0] - q[0], p[1] - q[1])


def dist_point_segment(p, a, b):
    """Distance from point p to the segment a-b."""
    ax, ay = a
    bx, by = b
    px, py = p
    dx, dy = bx - ax, by - ay
    den = dx * dx + dy * dy
    if den <= EPS:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / den
    if t < 0.0:
        t = 0.0
    elif t > 1.0:
        t = 1.0
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _orient(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def segments_intersect(a, b, c, d):
    """True when the closed segments a-b and c-d share at least one point."""
    o1 = _orient(a, b, c)
    o2 = _orient(a, b, d)
    o3 = _orient(c, d, a)
    o4 = _orient(c, d, b)
    if ((o1 > 0 and o2 < 0) or (o1 < 0 and o2 > 0)) and \
       ((o3 > 0 and o4 < 0) or (o3 < 0 and o4 > 0)):
        return True
    # collinear/touching cases
    if abs(o1) <= EPS and _on_segment(a, b, c):
        return True
    if abs(o2) <= EPS and _on_segment(a, b, d):
        return True
    if abs(o3) <= EPS and _on_segment(c, d, a):
        return True
    if abs(o4) <= EPS and _on_segment(c, d, b):
        return True
    return False


def _on_segment(a, b, p):
    return (min(a[0], b[0]) - EPS <= p[0] <= max(a[0], b[0]) + EPS and
            min(a[1], b[1]) - EPS <= p[1] <= max(a[1], b[1]) + EPS)


def dist_segment_segment(a, b, c, d):
    """Minimum distance between two segments (0 when they intersect)."""
    if segments_intersect(a, b, c, d):
        return 0.0
    return min(dist_point_segment(a, c, d), dist_point_segment(b, c, d),
               dist_point_segment(c, a, b), dist_point_segment(d, a, b))


# ----------------------------------------------------------------- polygons
def polygon_area(points):
    """Signed area (positive = counter-clockwise). Absolute value = area."""
    n = len(points)
    if n < 3:
        return 0.0
    s = 0.0
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return 0.5 * s


def bbox_of(points):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def bbox_union(a, b):
    if a is None:
        return b
    if b is None:
        return a
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def bbox_gap(a, b):
    """Distance between two axis-aligned boxes; 0 when they overlap."""
    dx = max(0.0, max(a[0], b[0]) - min(a[2], b[2]))
    dy = max(0.0, max(a[1], b[1]) - min(a[3], b[3]))
    return math.hypot(dx, dy)


def point_in_polygon(p, points):
    """Ray casting. Boundary counts as inside (needed for pad-in-zone tests)."""
    n = len(points)
    if n < 3:
        return False
    for i in range(n):
        if dist_point_segment(p, points[i], points[(i + 1) % n]) <= 1e-9:
            return True
    inside = False
    x, y = p
    j = n - 1
    for i in range(n):
        xi, yi = points[i]
        xj, yj = points[j]
        if (yi > y) != (yj > y):
            xint = (xj - xi) * (y - yi) / (yj - yi) + xi
            if x < xint:
                inside = not inside
        j = i
    return inside


def dist_point_polygon(p, points):
    """Distance from a point to a polygon boundary (0 if p is on the boundary)."""
    n = len(points)
    if n == 0:
        return float("inf")
    if n == 1:
        return dist_point_point(p, points[0])
    best = float("inf")
    for i in range(n):
        best = min(best, dist_point_segment(p, points[i], points[(i + 1) % n]))
    return best


def dist_polygon_polygon(pa, pb):
    """Boundary-to-boundary distance between two polygons (0 if they overlap).

    Overlap detection is done by testing one vertex of each polygon against the
    other polygon plus a segment-crossing scan; that is exact for simple
    polygons and is all the Oracle needs (zones and board outlines are simple).
    """
    if not pa or not pb:
        return float("inf")
    for p in pa:
        if point_in_polygon(p, pb):
            return 0.0
    for p in pb:
        if point_in_polygon(p, pa):
            return 0.0
    best = float("inf")
    na, nb = len(pa), len(pb)
    for i in range(na):
        a1 = pa[i]
        a2 = pa[(i + 1) % na]
        for j in range(nb):
            b1 = pb[j]
            b2 = pb[(j + 1) % nb]
            if segments_intersect(a1, a2, b1, b2):
                return 0.0
            best = min(best, dist_point_segment(a1, b1, b2),
                       dist_point_segment(b1, a1, a2))
    return best


def arc_to_points(center, radius, start_deg, sweep_deg, segments=None,
                   chord_tol_mm=None):
    """Approximate an arc as a polyline.

    Chord error for `segments` pieces is r*(1-cos(theta/2)); callers that care
    pass a `segments` count derived from a target chord error, or the error
    itself as `chord_tol_mm` and let this function work the count out. The Oracle
    states this approximation in its `precision` field rather than hiding it.

    The default (one piece per 7.5 degrees) bounds the error by r*0.00214, which
    is 2.1 um on a 1 mm radius but 10.7 um on a 5 mm one - the chord error is
    proportional to the radius, so a fixed angular step is not a fixed accuracy.
    `chord_tol_mm` is the honest way to ask for an absolute one:
    theta_max = 2*acos(1 - tol/r), capped at 720 pieces so a huge radius cannot
    turn a pad outline into a million-point polygon.
    """
    if segments is None and chord_tol_mm is not None and radius > 0 \
            and chord_tol_mm < radius:
        import math as _m
        step = 2.0 * _m.degrees(_m.acos(1.0 - chord_tol_mm / radius))
        if step > 0:
            segments = max(4, min(720, int(_m.ceil(abs(sweep_deg) / step))))
    if segments is None:
        segments = max(4, int(abs(sweep_deg) / 7.5) + 1)
    out = []
    for i in range(segments + 1):
        t = start_deg + sweep_deg * (i / float(segments))
        rad = math.radians(t)
        out.append((center[0] + radius * math.cos(rad),
                    center[1] + radius * math.sin(rad)))
    return out


def rotate(px, py, cx, cy, angle_deg):
    if not angle_deg:
        return (px, py)
    a = math.radians(angle_deg)
    ca, sa = math.cos(a), math.sin(a)
    dx, dy = px - cx, py - cy
    return (cx + dx * ca - dy * sa, cy + dx * sa + dy * ca)


def rect_points(cx, cy, w, h, angle_deg=0.0):
    hw, hh = w / 2.0, h / 2.0
    pts = [(cx - hw, cy - hh), (cx + hw, cy - hh),
           (cx + hw, cy + hh), (cx - hw, cy + hh)]
    if angle_deg:
        pts = [rotate(x, y, cx, cy, angle_deg) for x, y in pts]
    return pts


def circle_points(cx, cy, r, segments=16):
    return [(cx + r * math.cos(2 * math.pi * i / segments),
             cy + r * math.sin(2 * math.pi * i / segments))
            for i in range(segments)]


def polygon_bbox(points):
    return bbox_of(points) if points else None
