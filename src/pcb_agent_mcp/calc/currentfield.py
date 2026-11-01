"""Two-dimensional steady-state current density field in a copper sheet.

Why this module exists
----------------------
Everything else in calc/ answers "how much current" with a ONE-dimensional
model: a track is a segment of known width, so J = I/(w*t) and that is the
answer.  On a real board the place that fails is not the straight part, it is
the geometry change - a 90 degree bend, a via neck, a width step, a
pad-to-track junction.  There the current does not spread evenly and the local
density can be several times the 1-D number.  That is a 2-D field problem,

    div(sigma * grad V) = 0      on the region the copper occupies,

and this module solves it in pure arithmetic (no numpy: dependencies must stay
empty).

Method
------
The copper region - the union of the polygons and of the round-capped polylines
the caller gives - is rasterised onto a square grid.  Two adjacent copper cells
are joined by a conductance G = t/rho, which is the sheet conductance of one
square and is independent of the cell size; that is exactly the classical
resistor-mesh model of a thin conducting sheet, and it reproduces the analytic
strip resistance rho/t * L/W without any fudge factor.  Contacts are Dirichlet
islands (source 0 V, sink 1 V); the rest of the copper boundary is insulating -
it is simply the edge of the mesh, so nothing leaves (Neumann, naturally).

The mesh is solved by SOR, each level started from a prolongation of the next
coarser level (cascadic multigrid).  Every level rasterises the ORIGINAL
geometry at its own spacing rather than merging a coarse mask, so what the
level table measures is a genuine grid refinement and not an artefact of mask
coarsening.

Scale invariance gives R and J for any current from a single solve: apply 1 V,
measure the total current I_1V, then R = 1/I_1V and J(I) = J_1V * I / I_1V.
Both follow exactly from linearity.

What must NOT be read off it
----------------------------
At a reentrant corner - the inside of a 90 degree bend is a 270 degree interior
angle of metal - the potential behaves as r^(pi/theta) with theta = 270 deg, so
V ~ r^(2/3), J ~ r^(-1/3) and the current density DIVERGES.  The peak there is
not a physical number and it is not grid convergent; commercial solvers have
the same property and the honest thing to do is what is done here: report the
peak, report peak_growth = peak(h)/peak(2h) from the last two levels (1.0 for a
resolved hot spot, ~2^(1/3) = 1.2599 for a 270 degree corner), say it in words
when it is above 1.02, and also report the max J inside 1h / 2h / 4h of the peak
so the caller has a reading that does not move with the refinement.

Symmetrically, an OUTER corner of metal is a 90 degree interior angle, V ~ r^2,
J -> 0: the hot spot of a bend is on the inside of the corner, never outside.
"""
import math

from .result import ok, err

# 1.724e-8 ohm*m = 1.724e-5 ohm*mm.  Kept in ohm*mm because that is what makes
# J = E/rho come out in A/mm^2 with E in V/mm, no 1e-3 littering the code.
CU_RHO_20_OHM_MM = 1.724e-5
# Pure-Python SOR: wall clock grows roughly as N^1.5 (sweeps grow like N, cost per
# sweep like N). Measured on this machine: a 20.8 x 2.8 mm strip at h = 0.1 mm
# (4000 cells) 2.8 s, an L-bend at h = 0.05 mm (23k cells) 4.8 s, the same bend at
# h = 0.025 mm (93k cells) 55 s. 40k cells keeps the tool inside a minute.
DEFAULT_MAX_UNKNOWNS = 40000
# theory: J ~ r^(pi/theta - 1); theta = 270 deg -> exponent -1/3 -> doubling the
# resolution (halving h) multiplies the measured peak by 2^(1/3)
REENTRANT_GROWTH_PER_HALVING = 2.0 ** (1.0 / 3.0)
PEAK_GROWTH_WARN = 1.02
# Optimal SOR over-relaxation for the mixed Dirichlet/Neumann 5-point mesh used
# here; measured on a 216 x 36 strip, tol 1e-8: omega 1.85 -> 7.0 s, 1.90 -> 6.8 s,
# 1.93 -> 5.6 s, 1.95 -> 4.5 s, 1.97 -> 3.1 s. (The textbook formula for a mesh
# with DIRICHLET sides returns ~1.60 here and is slower, because most of this
# boundary is insulating, and omega -> 2 oscillates.) The answer does not depend on
# it, only the wall clock.
OMEGA_DEFAULT = 1.97


# --------------------------------------------------------------- geometry
def _bbox(pts, pad=0.0):
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)


def _prep_objs(paths, polygons):
    """Normalise the two ways of describing copper into one list of shapes."""
    objs = []
    for k, poly in enumerate(polygons or []):
        if poly is None:
            continue
        pts = [(float(p[0]), float(p[1])) for p in poly]
        if len(pts) < 3:
            raise ValueError("polygons[%d] has %d points, a filled polygon needs >= 3"
                             % (k, len(pts)))
        objs.append(("poly", pts, _bbox(pts)))
    for k, pth in enumerate(paths or []):
        if pth is None:
            continue
        raw = pth.get("points") if isinstance(pth, dict) else pth
        if raw is None:
            raise ValueError("paths[%d] has no 'points'" % k)
        pts = [(float(p[0]), float(p[1])) for p in raw]
        if len(pts) < 2:
            raise ValueError("paths[%d] has %d points, a path needs >= 2" % (k, len(pts)))
        w = pth.get("width_mm") if isinstance(pth, dict) else None
        if w is None:
            raise ValueError("paths[%d] has no width_mm: a polyline is not copper "
                             "until it is given a width" % k)
        w = float(w)
        if not (w > 0):
            raise ValueError("paths[%d].width_mm must be > 0 (got %r)" % (k, w))
        objs.append(("path", (pts, w), _bbox(pts, w * 0.5)))
    if not objs:
        raise ValueError("no copper given: pass at least one entry in 'paths' or "
                         "'polygons'")
    return objs


def _objs_bbox(objs):
    return (min(o[2][0] for o in objs), min(o[2][1] for o in objs),
            max(o[2][2] for o in objs), max(o[2][3] for o in objs))


def _fill_poly(mask, nx, ny, x0, y0, h, pts):
    """Scanline fill, even-odd rule inside one polygon (union across polygons)."""
    n = len(pts)
    ys = [p[1] for p in pts]
    jlo = int(math.floor((min(ys) - y0) / h - 0.5))
    jhi = int(math.ceil((max(ys) - y0) / h - 0.5))
    if jlo < 0:
        jlo = 0
    if jhi > ny - 1:
        jhi = ny - 1
    for j in range(jlo, jhi + 1):
        y = y0 + (j + 0.5) * h
        xs = []
        for k in range(n):
            xa, ya = pts[k]
            xb, yb = pts[(k + 1) % n]
            if (ya > y) != (yb > y):
                xs.append(xa + (y - ya) * (xb - xa) / (yb - ya))
        if len(xs) < 2:
            continue
        xs.sort()
        base = j * nx
        m = 0
        while m + 1 < len(xs):
            i0 = int(math.ceil((xs[m] - x0) / h - 0.5))
            i1 = int(math.floor((xs[m + 1] - x0) / h - 0.5))
            if i1 >= 0 and i0 <= nx - 1:
                if i0 < 0:
                    i0 = 0
                if i1 > nx - 1:
                    i1 = nx - 1
                for i in range(i0, i1 + 1):
                    mask[base + i] = 1
            m += 2


def _seg_dist2(px, py, ax, ay, bx, by):
    dx = bx - ax
    dy = by - ay
    l2 = dx * dx + dy * dy
    if l2 <= 0.0:
        return (px - ax) ** 2 + (py - ay) ** 2
    t = ((px - ax) * dx + (py - ay) * dy) / l2
    if t < 0.0:
        t = 0.0
    elif t > 1.0:
        t = 1.0
    qx = ax + t * dx
    qy = ay + t * dy
    return (px - qx) ** 2 + (py - qy) ** 2


def _fill_path(mask, nx, ny, x0, y0, h, pts, w):
    r = w * 0.5
    r2 = r * r
    for k in range(len(pts) - 1):
        ax, ay = pts[k]
        bx, by = pts[k + 1]
        i0 = int(math.floor((min(ax, bx) - r - x0) / h - 0.5))
        i1 = int(math.ceil((max(ax, bx) + r - x0) / h - 0.5))
        j0 = int(math.floor((min(ay, by) - r - y0) / h - 0.5))
        j1 = int(math.ceil((max(ay, by) + r - y0) / h - 0.5))
        if i0 < 0:
            i0 = 0
        if j0 < 0:
            j0 = 0
        if i1 > nx - 1:
            i1 = nx - 1
        if j1 > ny - 1:
            j1 = ny - 1
        for j in range(j0, j1 + 1):
            py = y0 + (j + 0.5) * h
            base = j * nx
            for i in range(i0, i1 + 1):
                px = x0 + (i + 0.5) * h
                if _seg_dist2(px, py, ax, ay, bx, by) <= r2:
                    mask[base + i] = 1


def _rasterize(objs, nx, ny, x0, y0, h):
    mask = bytearray(nx * ny)
    for kind, data, _bb in objs:
        if kind == "poly":
            _fill_poly(mask, nx, ny, x0, y0, h, data)
        else:
            pts, w = data
            _fill_path(mask, nx, ny, x0, y0, h, pts, w)
    return mask


def _contact_cells(mask, nx, ny, x0, y0, h, spec, name):
    """Cells the contact region covers.

    A cell is part of the contact when the SQUARE that the cell represents
    ([xc-h/2, xc+h/2] x [yc-h/2, yc+h/2]) intersects the contact region.  Using
    the square and not the centre is what makes "put a pad at the end of this
    trace" behave the way a caller expects: a contact rectangle that reaches the
    copper edge still picks up the whole boundary column instead of missing it by
    half a cell.
    """
    cells = []
    # 1 nm of slack: a contact that names the copper edge exactly ("x1_mm = 0" on a
    # polygon starting at x = 0) must not miss the boundary column because
    # x0 + (i+0.5)*h - h/2 came out 4e-17 past the edge in floating point.
    eps = 1e-6
    if "radius_mm" in spec:
        cx = float(spec["x_mm"])
        cy = float(spec["y_mm"])
        r = float(spec["radius_mm"])
        if not (r > 0):
            raise ValueError("%s.radius_mm must be > 0" % name)
        # square-vs-disc overlap, so the reach is r plus half a cell diagonal at most
        r2 = (r + 0.5 * h * 1.41421356237) ** 2
        i0 = max(0, int(math.floor((cx - r - h - x0) / h)))
        i1 = min(nx - 1, int(math.ceil((cx + r + h - x0) / h)))
        j0 = max(0, int(math.floor((cy - r - h - y0) / h)))
        j1 = min(ny - 1, int(math.ceil((cy + r + h - y0) / h)))
        for j in range(j0, j1 + 1):
            py = y0 + (j + 0.5) * h
            base = j * nx
            for i in range(i0, i1 + 1):
                if not mask[base + i]:
                    continue
                px = x0 + (i + 0.5) * h
                if (px - cx) ** 2 + (py - cy) ** 2 <= r2:
                    cells.append(base + i)
        return cells, {"kind": "circle", "x_mm": cx, "y_mm": cy, "radius_mm": r}
    if "x0_mm" in spec:
        xa = float(spec["x0_mm"])
        ya = float(spec["y0_mm"])
        xb = float(spec["x1_mm"])
        yb = float(spec["y1_mm"])
        if xa > xb:
            xa, xb = xb, xa
        if ya > yb:
            ya, yb = yb, ya
        half = 0.5 * h
        i0 = max(0, int(math.floor((xa - half - x0) / h - 0.5)))
        i1 = min(nx - 1, int(math.ceil((xb + half - x0) / h - 0.5)))
        j0 = max(0, int(math.floor((ya - half - y0) / h - 0.5)))
        j1 = min(ny - 1, int(math.ceil((yb + half - y0) / h - 0.5)))
        for j in range(j0, j1 + 1):
            py = y0 + (j + 0.5) * h
            if py + half < ya - eps or py - half > yb + eps:
                continue
            base = j * nx
            for i in range(i0, i1 + 1):
                px = x0 + (i + 0.5) * h
                if px + half < xa - eps or px - half > xb + eps:
                    continue
                if mask[base + i]:
                    cells.append(base + i)
        return cells, {"kind": "rect", "x0_mm": xa, "y0_mm": ya,
                       "x1_mm": xb, "y1_mm": yb}
    raise ValueError("%s needs either radius_mm (circle) or x0_mm/y0_mm/x1_mm/y1_mm "
                     "(rectangle)" % name)


def _components(mask, nx, ny):
    """Label the connected components of the copper mask (4-connected)."""
    label = [-1] * (nx * ny)
    sizes = []
    n = nx * ny
    for start in range(n):
        if not mask[start] or label[start] >= 0:
            continue
        lab = len(sizes)
        size = 0
        stack = [start]
        label[start] = lab
        while stack:
            c = stack.pop()
            size += 1
            i = c % nx
            j = c // nx
            if i > 0:
                d = c - 1
                if mask[d] and label[d] < 0:
                    label[d] = lab
                    stack.append(d)
            if i < nx - 1:
                d = c + 1
                if mask[d] and label[d] < 0:
                    label[d] = lab
                    stack.append(d)
            if j > 0:
                d = c - nx
                if mask[d] and label[d] < 0:
                    label[d] = lab
                    stack.append(d)
            if j < ny - 1:
                d = c + nx
                if mask[d] and label[d] < 0:
                    label[d] = lab
                    stack.append(d)
        sizes.append(size)
    return label, sizes


# ------------------------------------------------------------------ solve
def _build_system(mask, nx, ny, src, snk):
    """state: 0 = not copper, 1 = interior, 2 = source (0 V), 3 = sink (1 V)."""
    n = nx * ny
    state = bytearray(n)
    V = [0.0] * n
    for c in src:
        state[c] = 2
    for c in snk:
        if state[c] == 2:
            raise ValueError("source and sink overlap")
        state[c] = 3
        V[c] = 1.0
    for c in range(n):
        if mask[c] and state[c] == 0:
            state[c] = 1
    interior = []
    nb = []
    for c in range(n):
        if state[c] != 1:
            continue
        i = c % nx
        j = c // nx
        nbs = []
        if i > 0:
            d = c - 1
            if state[d]:
                nbs.append(d)
        if i < nx - 1:
            d = c + 1
            if state[d]:
                nbs.append(d)
        if j > 0:
            d = c - nx
            if state[d]:
                nbs.append(d)
        if j < ny - 1:
            d = c + nx
            if state[d]:
                nbs.append(d)
        interior.append(c)
        nb.append(nbs)
    return state, V, interior, nb


def _sor(V, interior, nb, omega, tol, max_sweeps):
    # flat index list + the degree, so the inner loop does no attribute lookups and
    # no division; 'ours' keeps the original node ids of the ordered unknowns
    idx = []
    nbs_list = []
    for k in range(len(interior)):
        nbs = nb[k]
        if not nbs:
            continue
        idx.append(interior[k])
        nbs_list.append((tuple(nbs), 1.0 / len(nbs)))
    chg = 0.0
    sweeps = 0
    tol = float(tol)
    for sweeps in range(1, int(max_sweeps) + 1):
        chg = 0.0
        for k in range(len(idx)):
            i = idx[k]
            nbs, inv = nbs_list[k]
            s = 0.0
            for d in nbs:
                s += V[d]
            nv = V[i] + omega * (s * inv - V[i])
            d2 = nv - V[i]
            if d2 < 0.0:
                d2 = -d2
            if d2 > chg:
                chg = d2
            V[i] = nv
        if chg <= tol:
            return sweeps, chg, True
    return sweeps, chg, False


def _residual(V, interior, nb):
    """max |V - neighbour average| over interior nodes, i.e. how far from discrete
    equilibrium the returned field is.  Same quantity SOR drives to 'tol'."""
    worst = 0.0
    for k in range(len(interior)):
        nbs = nb[k]
        if not nbs:
            continue
        s = 0.0
        for d in nbs:
            s += V[d]
        r = s / len(nbs) - V[interior[k]]
        if r < 0.0:
            r = -r
        if r > worst:
            worst = r
    return worst


def _prolong(V_c, nx_c, ny_c, x0, y0, h_c, mask_c,
             nx_f, ny_f, h_f, mask_f, V_f):
    """Bilinear from the coarse level; cells with no coarse support get 0.5 V."""
    filled = 0
    missed = 0
    for j in range(ny_f):
        yf = y0 + (j + 0.5) * h_f
        fy = (yf - y0) / h_c - 0.5
        j0 = int(math.floor(fy))
        ty = fy - j0
        base_f = j * nx_f
        for i in range(nx_f):
            if not mask_f[base_f + i]:
                continue
            xf = x0 + (i + 0.5) * h_f
            fx = (xf - x0) / h_c - 0.5
            i0 = int(math.floor(fx))
            tx = fx - i0
            acc = 0.0
            wsum = 0.0
            for (ii, jj, w) in ((i0, j0, (1.0 - tx) * (1.0 - ty)),
                                (i0 + 1, j0, tx * (1.0 - ty)),
                                (i0, j0 + 1, (1.0 - tx) * ty),
                                (i0 + 1, j0 + 1, tx * ty)):
                if w <= 0.0 or ii < 0 or jj < 0 or ii >= nx_c or jj >= ny_c:
                    continue
                c = jj * nx_c + ii
                if not mask_c[c]:
                    continue
                acc += w * V_c[c]
                wsum += w
            if wsum > 1e-9:
                V_f[base_f + i] = acc / wsum
                filled += 1
            else:
                V_f[base_f + i] = 0.5
                missed += 1
    return filled, missed


def _currents(state, V, nx, ny, G):
    """Injected current at the source, arriving current at the sink, and the
    Joule sum over every edge.

    Written as a sweep over EDGES (each edge taken once, to its right or below)
    rather than over contact nodes, because a contact that sits on the right edge
    of the grid has no edge with a larger index and would be missed by the latter
    - which is exactly how a perfectly good solve once reported I_out = 0.
    """
    n = nx * ny
    i_in = 0.0
    i_out = 0.0
    power = 0.0
    for c in range(n):
        sc = state[c]
        i = c % nx
        j = c // nx
        for d, ok in ((c + 1, i < nx - 1), (c + nx, j < ny - 1)):
            if not ok:
                continue
            sd = state[d]
            if not sc or not sd:
                continue
            iedge = (V[c] - V[d]) * G          # current from c towards d
            power += iedge * iedge / G
            if sc == 2 and sd != 2:
                i_in -= iedge
            elif sd == 2 and sc != 2:
                i_in += iedge
            if sc == 3 and sd != 3:
                i_out += iedge
            elif sd == 3 and sc != 3:
                i_out -= iedge
    return i_in, i_out, power


def _fields(state, V, nx, ny, h, rho):
    """Current density magnitude at every copper cell, from (V,rho,h) only.

    Jx at a cell is the central difference of V; cells on the copper edge use the
    one-sided difference, which is the same statement as "no flux through the
    missing face" that the solve imposed.
    """
    n = nx * ny
    jx = [0.0] * n
    jy = [0.0] * n
    for c in range(n):
        if not state[c]:
            continue
        i = c % nx
        j = c // nx
        left = c - 1 if (i > 0 and state[c - 1]) else -1
        right = c + 1 if (i < nx - 1 and state[c + 1]) else -1
        down = c - nx if (j > 0 and state[c - nx]) else -1
        up = c + nx if (j < ny - 1 and state[c + nx]) else -1
        if left >= 0 and right >= 0:
            jx[c] = (V[left] - V[right]) / (2.0 * h)
        elif left >= 0:
            jx[c] = (V[left] - V[c]) / h
        elif right >= 0:
            jx[c] = (V[c] - V[right]) / h
        if down >= 0 and up >= 0:
            jy[c] = (V[down] - V[up]) / (2.0 * h)
        elif down >= 0:
            jy[c] = (V[down] - V[c]) / h
        elif up >= 0:
            jy[c] = (V[c] - V[up]) / h
    return jx, jy


def _percentile(sorted_vals, q):
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = q * (len(sorted_vals) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = pos - lo
    return sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac


def _narrowest_section(state, V, nx, ny, h, t, G):
    """Narrowest cross-section that actually carries the current.

    Counting copper cells per column and taking the minimum is wrong wherever the
    copper has a stub or a tapered end: a round-capped polyline has a tip column
    one cell wide that carries almost nothing, and it would be reported as "the
    narrowest section", making the crowding factor look BELOW one (measured: 0.435
    on a 2 mm capsule). So a cut only qualifies when the current crossing it is at
    least 90% of the largest current any parallel cut carries; among those the
    minimum area wins.

    Axis-projected: a column of k cells is k*h*t. A trace running at an angle to
    the axes therefore reports a section that is too large by up to 1/cos(angle),
    which makes the crowding factor it produces a lower bound.
    """
    n = nx * ny
    cut_x = [0.0] * nx          # |current| crossing the vertical cut at column i
    cut_y = [0.0] * ny
    for c in range(n):
        if not state[c]:
            continue
        i = c % nx
        j = c // nx
        if i < nx - 1:
            d = c + 1
            if state[d]:
                cut_x[i] += abs((V[c] - V[d]) * G)
        if j < ny - 1:
            d = c + nx
            if state[d]:
                cut_y[j] += abs((V[c] - V[d]) * G)
    area_x = [0] * nx
    area_y = [0] * ny
    for c in range(n):
        if state[c]:
            area_x[c % nx] += 1
            area_y[c // nx] += 1
    best = None
    where = None
    for src_axis, cut, area, ncut in (("x", cut_x, area_x, nx),
                                      ("y", cut_y, area_y, ny)):
        peak_cut = max(cut) if cut else 0.0
        if peak_cut <= 0:
            continue
        for k in range(ncut):
            if cut[k] < 0.9 * peak_cut or not area[k]:
                continue
            a = area[k] * h * t
            if best is None or a < best:
                best = a
                where = {"axis": src_axis, "index": k,
                         "distance_mm": (k + 0.5) * h,
                         "current_fraction": cut[k] / peak_cut}
    return best, where


# -------------------------------------------------------------------- main
def current_density_field(paths=None, polygons=None, thickness_mm=0.035,
                          rho_ohm_mm=CU_RHO_20_OHM_MM, source=None, sink=None,
                          current_a=1.0, h_mm=None,
                          max_unknowns=DEFAULT_MAX_UNKNOWNS, levels=4,
                          tol=1e-8, max_sweeps=30000, omega=None,
                          frequency_hz=None, skin_faces="one"):
    """2-D current density field on a rasterised copper region.

    DC unless `frequency_hz` is given.  With a frequency the sheet conductance is
    reduced to the AC value of the same slab (skin.slab_surface_impedance, the
    exact 1-D solution through the thickness), so R and the power come back as
    the AC ones and the lateral field is unchanged - a uniform thickness
    correction, which is what a 2-D sheet can honestly carry without solving a
    complex-valued field.  The J values stay thickness-averaged so that they are
    directly comparable with the DC answer; the surface density is that times
    r_ac_over_r_dc.
    """
    try:
        objs = _prep_objs(paths, polygons)
    except ValueError as e:
        return err(str(e), "BAD_GEOMETRY",
                   hint="copper is given as polygons=[[[x,y],...]] (filled) and/or "
                        "paths=[{points:[[x,y],...], width_mm:w}] (round-capped "
                        "polyline). Coordinates in mm.")

    t = float(thickness_mm)
    rho = float(rho_ohm_mm)
    cur = float(current_a)
    if not (t > 0):
        return err("thickness_mm must be > 0", "BAD_PARAMETER")
    if not (rho > 0):
        return err("rho_ohm_mm must be > 0", "BAD_PARAMETER",
                   hint="copper at 20 C is 1.724e-5 ohm*mm; that is the default")
    if not (cur > 0):
        return err("current_a must be > 0 (the field is scaled linearly, so a "
                   "negative current is the same field with the sign flipped)",
                   "BAD_PARAMETER")
    if source is None or sink is None:
        return err("both 'source' and 'sink' are required: the field is driven by a "
                   "potential difference between two contact regions",
                   "BAD_PARAMETER",
                   hint="contact = {x_mm,y_mm,radius_mm} or "
                        "{x0_mm,y0_mm,x1_mm,y1_mm}, in mm")
    if not isinstance(source, dict) or not isinstance(sink, dict):
        return err("source and sink must be objects", "BAD_PARAMETER")

    bx0, by0, bx1, by1 = _objs_bbox(objs)
    w_bbox = bx1 - bx0
    h_bbox = by1 - by0
    if not (w_bbox > 0 and h_bbox > 0):
        return err("the copper region is degenerate (zero extent)", "BAD_GEOMETRY")

    # --- grid plan: finest spacing from the unknown budget unless given
    if h_mm is not None:
        h_fine = float(h_mm)
        if not (h_fine > 0):
            return err("h_mm must be > 0", "BAD_PARAMETER")
    else:
        h_fine = math.sqrt(w_bbox * h_bbox / float(max_unknowns))
    lv = int(levels) if levels else 1
    if lv < 1:
        return err("levels must be >= 1", "BAD_PARAMETER")
    margin = 2.0 * h_fine * (2 ** (lv - 1))
    x0 = bx0 - margin
    y0 = by0 - margin
    span_x = (bx1 - bx0) + 2.0 * margin
    span_y = (by1 - by0) + 2.0 * margin

    hs = [h_fine * (2.0 ** k) for k in range(lv - 1, -1, -1)]
    t_eff = t
    skin = None
    if frequency_hz is not None:
        from .skin import slab_surface_impedance
        try:
            f_hz = float(frequency_hz)
        except (TypeError, ValueError):
            return err("frequency_hz must be a number", "BAD_PARAMETER")
        if not (f_hz > 0):
            return err("frequency_hz must be > 0: leave it out for the DC answer, "
                       "which is what every earlier release returned", "BAD_PARAMETER")
        if skin_faces not in ("one", "two"):
            return err("skin_faces must be 'one' (return on one side) or 'two' "
                       "(symmetric return), not %r" % (skin_faces,), "BAD_PARAMETER")
        st = slab_surface_impedance(f_hz, t, rho, 1.0, skin_faces)
        t_eff = st["t_eff_mm"]
        skin = {
            "frequency_hz": f_hz,
            "faces": skin_faces,
            "skin_depth_um": st["delta_mm"] * 1e3,
            "thickness_over_skin_depth": st["s"],
            "r_ac_over_r_dc": st["r_over_rdc"],
            "x_ac_over_r_dc": st["x_over_rdc"],
            "effective_thickness_um": t_eff * 1e3,
            "thickness_um": t * 1e3,
        }

    G = t_eff / rho   # sheet conductance of one square, siemens

    level_rows = []
    have_prev = False
    V_prev = nx_prev = ny_prev = h_prev = mask_prev = None
    prev_peak = None
    peak_growth = None
    finest = None
    prev_h = None

    for li, h in enumerate(hs):
        nx = int(math.ceil(span_x / h))
        ny = int(math.ceil(span_y / h))
        if nx < 3 or ny < 3:
            continue
        mask = _rasterize(objs, nx, ny, x0, y0, h)
        ncell = sum(mask)
        if not ncell:
            if li == len(hs) - 1:
                return err("the copper region vanished on the grid: no cell was "
                           "filled at h = %g mm" % h, "NO_COPPER",
                           hint="the geometry may be thinner than the grid; pass a "
                                "smaller h_mm or a larger max_unknowns")
            continue
        try:
            src, src_desc = _contact_cells(mask, nx, ny, x0, y0, h, source, "source")
            snk, snk_desc = _contact_cells(mask, nx, ny, x0, y0, h, sink, "sink")
        except (ValueError, KeyError, TypeError) as e:
            return err("contact definition: %s" % e, "BAD_PARAMETER")
        if not src or not snk:
            if li == len(hs) - 1:
                which = "source" if not src else "sink"
                spec = source if not src else sink
                return err("the %s contact touches no copper cell at h = %g mm "
                           "(cells whose centre lies inside the %s region were "
                           "checked)" % (which, h, src_desc["kind"]),
                           "CONTACT_NOT_ON_COPPER",
                           hint="put the contact centre on the copper, or give it a "
                                "radius that reaches the copper; %r" % (spec,))
            continue
        try:
            state, V, interior, nb = _build_system(mask, nx, ny, src, snk)
        except ValueError as e:
            return err(str(e), "CONTACT_NOT_ON_COPPER",
                       hint="the source and sink regions share cells - move one of "
                            "them, or shrink the radius")

        # connectivity, with a diagnosis instead of a bare 'singular'
        label, sizes = _components(mask, nx, ny)
        lab_src = label[src[0]]
        labs_snk = set(label[c] for c in snk)
        if lab_src not in labs_snk:
            return err("source and sink are on disconnected copper", "NOT_CONNECTED",
                       hint="nothing conducts between them, so no current can flow. "
                            "On a real board this normally means the connection is a "
                            "zone/pour or a plane the mask does not include: add it "
                            "as a polygon, or use board_ir_drop with its own zone "
                            "handling. Components found: %d (source component holds "
                            "%d cells, sink components hold %s)"
                            % (len(sizes), sizes[lab_src],
                               sorted(sizes[l] for l in labs_snk)))

        if not have_prev:
            V = list(V)
        else:
            # start from the coarse solution, then restore the Dirichlet values
            V = [0.0] * (nx * ny)
            _prolong(V_prev, nx_prev, ny_prev, x0, y0, h_prev, mask_prev,
                     nx, ny, h, mask, V)
            for c in src:
                V[c] = 0.0
            for c in snk:
                V[c] = 1.0

        om = OMEGA_DEFAULT if omega is None else float(omega)
        sweeps, chg, converged = _sor(V, interior, nb, om, float(tol),
                                      int(max_sweeps))
        if not converged:
            return err("the linear solve did not converge: after %d SOR sweeps at "
                       "h = %g mm the largest potential change per sweep was %.3g V "
                       "(tolerance %.3g V)" % (sweeps, h, chg, float(tol)),
                       "NOT_CONVERGED",
                       hint="raise max_sweeps, raise omega towards 1.95, or coarsen "
                            "the grid. A field that is not converged is not an "
                            "answer, so nothing is returned.",
                       extra={"levels": level_rows,
                              "failed_level": {"h_mm": h, "nx": nx, "ny": ny,
                                               "sweeps": sweeps,
                                               "max_change_v": chg}})
        resid = _residual(V, interior, nb)

        i_in, i_out, power = _currents(state, V, nx, ny, G)
        if i_in <= 0:
            return err("no current enters at the source: the source region does not "
                       "touch the conducting path at h = %g mm" % h, "NO_CURRENT",
                       hint="this is a geometry problem, not a numerics problem - "
                            "the source island is isolated from the sink island")
        resist = 1.0 / i_in
        scale = cur / i_in

        jx, jy = _fields(state, V, nx, ny, h, rho)
        jval = []
        peak = -1.0
        peak_c = -1
        for c in range(nx * ny):
            if not state[c]:
                continue
            m = math.sqrt(jx[c] * jx[c] + jy[c] * jy[c]) / rho * scale
            jval.append(m)
            if m > peak:
                peak = m
                peak_c = c
        jval.sort()
        ncell_state = len(jval)
        median_j = _percentile(jval, 0.5)
        p99_j = _percentile(jval, 0.99)
        mean_j = sum(jval) / ncell_state if ncell_state else None

        pj = peak_c // nx
        pi = peak_c % nx
        peak_xy = [x0 + (pi + 0.5) * h, y0 + (pj + 0.5) * h]

        # ring readings around the peak.  A disc ("max J within r") is useless
        # because it always contains the peak itself; what carries information is
        # the ANNULUS, whose max falls like r^(-1/3) at a corner and hardly at all
        # for a resolved hot spot.  This is the reading that does not move when the
        # grid is refined, so it is the one to quote.
        rings = {}
        ring_defs = ((0.5, 1.0, "0.5h_to_1h"), (1.0, 2.0, "1h_to_2h"),
                     (2.0, 4.0, "2h_to_4h"))
        for (a, b, key) in ring_defs:
            ra = a * h
            rb = b * h
            best = 0.0
            cnt = 0
            for c in range(nx * ny):
                if not state[c]:
                    continue
                dx = (x0 + (c % nx + 0.5) * h) - peak_xy[0]
                dy = (y0 + (c // nx + 0.5) * h) - peak_xy[1]
                d2 = dx * dx + dy * dy
                # 1e-9 of slack on the annulus bounds: an axis neighbour sits at
                # EXACTLY 1h, and h = 0.05 comes out of the coordinate arithmetic
                # as 0.05000000000000004, which would drop it out of the ring.
                if d2 <= ra * ra * (1.0 - 1e-9) or d2 > rb * rb * (1.0 + 1e-9):
                    continue
                cnt += 1
                m = math.sqrt(jx[c] * jx[c] + jy[c] * jy[c]) / rho * scale
                if m > best:
                    best = m
            rings["max_j_" + key] = best
            rings["cells_" + key] = cnt
        r1 = rings["max_j_0.5h_to_1h"]
        r3 = rings["max_j_2h_to_4h"]
        rings["decay_2h_4h_over_0.5h_1h"] = (r3 / r1) if r1 else None
        rings["ring_radius_note"] = ("annuli measured from the peak cell, in units "
                                     "of the finest grid spacing h")

        if li > 0 and prev_peak:
            peak_growth = peak / prev_peak

        level_rows.append({
            "h_mm": h,
            "grid": [nx, ny],
            "copper_cells": ncell,
            "unknowns": len(interior),
            "sweeps": sweeps,
            "max_change_v": chg,
            "residual_v": resid,
            "converged": True,
            "resistance_ohm": resist,
            "peak_j_a_per_mm2": peak,
            "median_j_a_per_mm2": median_j,
            "peak_growth_vs_previous_level": peak_growth,
        })

        finest = {
            "h": h, "nx": nx, "ny": ny, "mask": mask, "state": state, "V": V,
            "interior": interior, "nb": nb, "i_in": i_in, "i_out": i_out,
            "power": power, "resist": resist, "scale": scale,
            "jval": jval, "peak": peak, "peak_xy": peak_xy,
            "median": median_j, "p99": p99_j, "mean": mean_j,
            "rings": rings, "src": src, "snk": snk,
            "cells": ncell_state,
        }
        prev_peak = peak
        have_prev = True
        # keep the level for the next prolongation
        V_prev, nx_prev, ny_prev, h_prev, mask_prev = V, nx, ny, h, mask

    if finest is None:
        return err("no usable grid level: every level either had no copper or "
                   "lost the contacts", "NO_COPPER")

    # ---- checks that can fail
    cons = abs(finest["i_in"] - finest["i_out"]) / finest["i_in"]
    power_ref = finest["i_in"] * finest["i_in"] * finest["resist"]
    pbal = abs(finest["power"] - power_ref) / power_ref if power_ref else None
    # t_eff, not t, so that the section area is the copper that actually carries
    # the current: with it the uniform density and the peak share one definition
    # and crowding_factor stays a pure geometry number at any frequency.
    sec, sec_where = _narrowest_section(finest["state"], finest["V"], finest["nx"],
                                       finest["ny"], finest["h"], t_eff, G)
    j_uniform = cur / sec if sec else None
    crowding = finest["peak"] / j_uniform if j_uniform else None

    warnings = []
    if peak_growth is not None and peak_growth > PEAK_GROWTH_WARN:
        warnings.append(
            "peak_growth = %.4f over the last refinement (1.0 would mean a resolved "
            "hot spot): the peak sits on a geometric singularity - a reentrant "
            "corner such as the inside of a bend has J ~ r^(-1/3) at a 270 degree "
            "interior angle - so THE PEAK IS NOT GRID CONVERGENT and must not be "
            "quoted as a physical current density. Read the ring values instead, or "
            "round the corner and re-run. For reference a pure 270 degree corner "
            "grows by 2^(1/3) = %.4f per halving."
            % (peak_growth, REENTRANT_GROWTH_PER_HALVING))
    thin = []
    for kind, data, _bb in objs:
        if kind == "path":
            _pts, w = data
            if w < 2.0 * finest["h"]:
                thin.append(w)
    if thin:
        warnings.append(
            "polyline width %s mm is under two cells at h = %g mm: the rasterised "
            "mask is a staircase on that feature and R/peak carry a rasterisation "
            "error of order one cell. Refine h_mm for a number you intend to act on."
            % (", ".join("%g" % w for w in thin), finest["h"]))
    # Both checks are bounded by the solve tolerance, not by the physics: with 200 A
    # through a cell and 5 mV between cells, a 1e-8 V residual per node shows up as
    # ~1e-5 of relative flux error. The threshold below is therefore a statement
    # about the solve, and it says so when it fires.
    if cons > 1e-4:
        warnings.append("current conservation is off by %.3g (I_in %.9g A, I_out "
                        "%.9g A): with tol = %g V this is a solve accuracy problem, "
                        "not a field problem - lower tol" %
                        (cons, finest["i_in"], finest["i_out"], float(tol)))
    if pbal is not None and pbal > 1e-4:
        warnings.append("the edge-sum power and I^2 R differ by %.3g - the field and "
                        "the network disagree, treat both with suspicion" % pbal)

    value = {
        "resistance_ohm": finest["resist"],
        "current_a": cur,
        "dvolt_v": 1.0,
        "power_w": cur * cur * finest["resist"],
        "peak_j_a_per_mm2": finest["peak"],
        "peak_at_mm": finest["peak_xy"],
        "median_j_a_per_mm2": finest["median"],
        "p99_j_a_per_mm2": finest["p99"],
        "mean_j_a_per_mm2": finest["mean"],
        "narrowest_section_mm2": sec,
        "narrowest_section_at": sec_where,
        "uniform_j_at_narrowest_a_per_mm2": j_uniform,
        "crowding_factor": crowding,
        "peak_growth": peak_growth,
        "peak_growth_if_270deg_corner": REENTRANT_GROWTH_PER_HALVING,
        "peak_is_grid_convergent": (peak_growth is not None
                                    and peak_growth <= PEAK_GROWTH_WARN),
        "rings": finest["rings"],
        "h_mm": finest["h"],
        "grid": [finest["nx"], finest["ny"]],
        "copper_cells": finest["cells"],
        "sor_sweeps": level_rows[-1]["sweeps"],
        "residual_v": level_rows[-1]["residual_v"],
    }

    model = ("2-D steady conduction: div(sigma*grad V) = 0 on the copper region, "
             "solved as a resistor mesh (sheet conductance G = t/rho per square) by "
             "SOR, each level prolonged from the next coarser one (cascadic "
             "multigrid). Contacts are Dirichlet islands, everything else Neumann.")
    basis = ("Ohm's law in a 2-D conducting sheet: one square of copper of thickness "
             "t has resistance rho/t, independent of the square size, which is the "
             "classical sheet-resistance mesh; R = 1/I at 1 V applied and "
             "J = J_1V * I/I_1V follow exactly from linearity. No standard document "
             "is involved - this is the field equation, not a table.")
    precision = (
        "Numerical, from a rasterised mask at h = %g mm (%dx%d, %d copper cells, %d "
        "unknowns, %d SOR sweeps, residual %.2e V). The resistance is grid "
        "converged to the level table shown; the PEAK current density is not, "
        "whenever peak_growth > %.2f, because a reentrant corner makes J ~ "
        "r^(-1/3) there - see the ring readings for a resolution-independent "
        "number. The model is a 2-D sheet: it assumes uniform thickness and rho "
        "(no temperature rise, no current crowding through vias, no 3-D spreading "
        "into a plane), and a contact is treated as an equipotential island with a "
        "hard edge, which is why the density right at a contact edge is itself "
        "singular and must not be read as a failure point."
        % (finest["h"], finest["nx"], finest["ny"], finest["cells"],
           level_rows[-1]["unknowns"], level_rows[-1]["sweeps"],
           level_rows[-1]["residual_v"], PEAK_GROWTH_WARN))

    assumptions = {
        "thickness_mm": t,
        "rho_ohm_mm": rho,
        "sheet_conductance_s_per_square": G,
        "grid_h_mm": finest["h"],
        "grid_origin_mm": [x0, y0],
        "contact_model": "Dirichlet equipotential island; the contact edge is a hard "
                         "step in potential, so the density at the edge is singular "
                         "by construction",
        "boundary": "insulating everywhere else (Neumann) - the mesh simply ends",
        "temperature": "uniform; rho is a single number, no I^2R feedback "
                       "(see board_electrothermal for that loop)",
        "current_source": "DC; no inductance, no skin effect, no displacement current",
    }
    if skin is not None:
        # Everything above is unchanged by the uniform thickness correction: the
        # potential field does not move at all when G is scaled, so the LATERAL
        # pattern and the crowding factor are the DC ones, exactly as documented.
        value["frequency_hz"] = skin["frequency_hz"]
        value["skin_faces"] = skin["faces"]
        value["skin_depth_um"] = skin["skin_depth_um"]
        value["thickness_over_skin_depth"] = skin["thickness_over_skin_depth"]
        value["effective_thickness_um"] = skin["effective_thickness_um"]
        value["r_ac_over_r_dc"] = skin["r_ac_over_r_dc"]
        value["x_ac_over_r_dc"] = skin["x_ac_over_r_dc"]
        value["sheet_conductance_s_per_square"] = G
        # The potential field is bit-identical; the scaling to the caller's
        # current is what moves.  With G = t_eff/rho the solver's J is the
        # density in the layer that actually carries the current (I / (w*t_eff)),
        # so at AC it IS the surface value, and the gross-thickness average -
        # the number the DC answer gives - is that divided by the ratio.
        value["peak_j_surface_a_per_mm2"] = finest["peak"]
        value["peak_j_gross_a_per_mm2"] = (finest["peak"]
                                          / skin["r_ac_over_r_dc"])
        assumptions["sheet_conductance_s_per_square"] = G
        assumptions["thickness_mm"] = t
        assumptions["effective_thickness_mm"] = t_eff
        assumptions["current_source"] = (
            "AC at %g Hz, rms: the thickness direction is the exact 1-D diffusion "
            "solution (r_ac/r_dc = %.6g), applied as a UNIFORM reduction of the "
            "sheet conductance. The lateral pattern is therefore still the DC one "
            "- no edge crowding, no proximity effect, no complex-valued field. The "
            "J values are the density in the EFFECTIVE layer (I/(w*t_eff)), i.e. "
            "the surface value; divide by r_ac_over_r_dc for the gross-thickness "
            "average, which is the number the DC answer gives (peak_j_gross)."
            % (skin["frequency_hz"], skin["r_ac_over_r_dc"]))
        model += (" At %g Hz the sheet conductance is that of an effective "
                  "thickness t/(r_ac/r_dc) = %.4g um (1-D skin effect through the "
                  "thickness, faces=%s); the 2-D lateral solution is unchanged by "
                  "it, and that is the model's limit, not its accuracy."
                  % (skin["frequency_hz"], t_eff * 1e3, skin["faces"]))
        basis += (" The AC sheet conductance comes from the exact 1-D slab solution "
                  "(skin.slab_surface_impedance); R and the power are the AC ones, "
                  "J stays thickness-averaged.")
        precision += (" The skin correction is 1-D: it changes HOW MUCH copper "
                      "carries the current, not WHERE along the width it goes.")
        warnings.append(
            "frequency_hz=%g is a UNIFORM thickness correction: the 2-D pattern "
            "and the crowding factor are the DC ones, so a wide strip whose current "
            "really crowds to its edges at this frequency is not modelled - use "
            "r_ac_over_r_dc for the resistance and read the crowding as a DC "
            "statement. The reported J and the narrowest section are the "
            "effective-layer (surface) quantities, so crowding_factor stays a "
            "geometry number" % skin["frequency_hz"])
    return ok(value, "mixed (see keys: ohm, A/mm^2, mm, mm^2, W)", model, basis,
              precision, assumptions,
              extra={
                  "levels": level_rows,
                  "checks": {
                      "current_conservation_rel_error": cons,
                      "power_balance_rel_error": pbal,
                      "power_edge_sum_w": finest["power"],
                      "power_i2r_w": power_ref,
                  },
                  "contacts": {"source_cells": len(finest["src"]),
                               "sink_cells": len(finest["snk"])},
                  "warnings": warnings,
                  "h_mm": finest["h"],
                  "grid": [finest["nx"], finest["ny"]],
                  "peak_growth": peak_growth,
              })


