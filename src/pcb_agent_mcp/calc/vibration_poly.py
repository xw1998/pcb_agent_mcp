"""Kirchhoff plate modes on an ARBITRARY outline (any simple polygon).

Why this module exists
---------------------
`vibration.plate_modes` solves a rectangle exactly (Rayleigh-Ritz on a separable
monomial basis, boundary conditions projected out of the basis). A real board
outline is not a rectangle, and replacing it with its bounding box makes the
plate bigger and softer: f1 comes out low by an amount nobody bounded.

Three constructions were measured before this one was written, all in the
`_scratch` prototypes, and two were rejected on numbers rather than on taste:

  * a distance envelope `psi = E(x,y) x^i y^j` with E vanishing on the boundary
    reproduces the square to +0.000 % (ss) / +0.003 % (cc) at degree 6, but E is
    a product over ALL edges, so with more than a few edges the basis becomes
    numerically degenerate: an inscribed 16-gon read +88 %, a 64-gon +105 %.
    Replacing the product by a min / softmin does not help - a min-type E is
    piecewise linear, so a simply supported square reads +85 %.
  * a Morley or 10-DOF cubic triangle finite element passes its patch test, but
    in pure Python only ~150 DOF are affordable and it is still 5..13 % low
    there (O(h) convergence): slow AND wrong.

What is used instead: Rayleigh-Ritz on a plain monomial basis over the polygon,
with the boundary condition enforced by a PENALTY integral on the boundary
(`alpha D/L^3 int w^2 ds`, plus `alpha D/L int (dw/dn)^2 ds` on a clamped
edge). It is the standard penalised variational form, with one caveat that
became fact 6 below: bigger alpha is only closer to the constrained answer
while the basis is still able to comply with the constraint.

Seven numerical facts had to be got right; all seven were measured the hard way:

  1. The interior integrals are done EXACTLY, by Green's theorem on the polygon
     ring, so no mesh and no quadrature error enter - only the boundary penalty
     is integrated numerically (1-D Gauss per edge).
  2. The mass matrix must be factored FIRST and the penalty added in the
     whitened basis. Adding a large penalty to a raw stiffness matrix whose
     entries are O(D/L^2) beside a boundary Gram of O(L) cost the whole answer:
     a clamped square read +6600 % at alpha=1e6 before whitening.
  3. Whitening by Cholesky of the mass matrix is not usable (a degree-8 monomial
     Gram on a unit square is Vandermonde-like; Cholesky fails outright), and
     whitening by the eigendecomposition of M down to a tiny eigenvalue is worse
     - 1/sqrt(lambda) then amplifies a direction with no stiffness at all and
     manufactures a spurious zero-frequency mode. Modified Gram-Schmidt in the
     mass inner product keeps the largest-mass directions and drops the rest.
  4. The space must contain a non-trivial function that satisfies the
     constraints, or the penalised eigenvalue diverges instead of converging.
     A clamped rectangle needs w = 0 and dw/dn = 0 on four edges, which needs a
     degree-8 polynomial: a degree-6 basis read +120 %, degree 8 read +0.009 %.
  5. The masses carry rho*h, so the whitened eigenvalue IS omega^2. Dividing by
     rho*h again is a 1/sqrt(rho h) error - measured as exactly -41.88 % of the
     rectangular answer on every geometry, sqrt(1/2.96) = 0.5811.
  6. A bigger alpha is NOT always closer to the constrained answer. For the
     penalty to buy compliance the basis must be able to satisfy the constraint;
     when it cannot (a 64-gon wants w = 0 on 64 chords, which no degree-10
     polynomial can do) every extra factor of ten past the knee only adds
     spurious stiffness: that plate read 554 Hz (knife edge), 560 Hz at 1e5,
     562 at 1e6 and 585 at 1e7 against a near-circular truth of 553.7. So the
     `reported_alpha` is the rung where the ladder FLATTENS, and the step size
     there is the answer's error bar.
  7. Because the basis runs out of shapes before it runs out of modes, each
     reported mode carries `penalty_share = alpha v^T C v / v^T A v`: the
     fraction of its omega^2 that is penalty rather than bending. A real mode
     reads < 1e-3, a mode the basis cannot represent reads 0.9..0.99, so no
     threshold has to be invented - and a clamped square really does read
     1009.55 Hz (0.000402) then 21.4 kHz (0.901) and 28.2 kHz (0.988) against a
     closed form of about 3 kHz. Those are flagged, never printed as modes.

Measured accuracy at poly_degree=8 (24 GPa / 0.33 / 1850, h = 1.6 mm), f1:

    figure                     this module   rectangle path   analytic
    square 100 mm, ss              -0.009 %        553.651      2 pi^2
    square 100 mm, clamped         +0.009 %       1009.460      35.985
    square 100 mm, 4 corners       -0.011 %        199.253      -
    square 100 mm, free (mode 4)    370.147        370.144      -
    square 100 mm, cantilever       96.773         97.183       -
    circle r=50 (64-gon), ss       +1.06 %            -        4.9351
    circle r=50 (64-gon), clamped  +1.14 %            -        10.2158
    square rotated 15..45 deg      +0.00 %        553.651      2 pi^2

Squares are the easy case (four edges, a degree-4 product vanishes on all of
 them) and rotate to within 0.05 %; a finely faceted or re-entrant outline is
the hard one, where the ladder's own spread (reported, never hidden, with a
warning above 3 %) is the honest uncertainty. Nothing here replaces the
rectangular answer silently - the caller decides what to do with two numbers.

Not modelled (the same gaps as the rectangular path, plus two more): component
mass, copper stiffening, the compliance of the mounting, shear deformation and
rotary inertia; and - new here - holes are ignored (only the outer ring is
solved, a cutout is named in the warnings rather than meshed) and stress is not
computed at all.
"""
import math

from . import vibration as _vib

_DEFAULT_P = 8
_MAX_DOF = 66            # degree 10; above this the dense sweeps get slow
_ALPHAS = (1.0e3, 1.0e4, 1.0e5, 1.0e6, 1.0e7)
_NQ1 = 8                 # boundary Gauss points per edge


# ------------------------------------------------------------------ quadrature
def _gauss(n):
    """Legendre nodes/weights on [-1, 1] (Newton on the recurrence)."""
    xs, ws = [], []
    for i in range(n):
        z = math.cos(math.pi * (i + 0.75) / (n + 0.5))
        pp = 1.0
        for _ in range(100):
            p0, p1 = 1.0, z
            for k in range(2, n + 1):
                p0, p1 = p1, ((2 * k - 1) * z * p1 - (k - 1) * p0) / k
            pp = n * (z * p1 - p0) / (z * z - 1.0)
            dz = -p1 / pp
            z += dz
            if abs(dz) < 1e-16:
                break
        xs.append(z)
        ws.append(2.0 / ((1 - z * z) * pp * pp))
    return xs, ws


# ----------------------------------------------------------------- polygon bits
def _signed_area(ring):
    s = 0.0
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return 0.5 * s


def _is_simple(ring, tol=1e-12):
    """True when no two non-adjacent edges cross (a proper polygon)."""
    n = len(ring)

    def seg(i):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        return x1, y1, x2, y2

    def cross(ax, ay, bx, by):
        return ax * by - ay * bx

    for i in range(n):
        ax1, ay1, ax2, ay2 = seg(i)
        for j in range(i + 1, n):
            if j == i or (j + 1) % n == i or (i + 1) % n == j:
                continue
            bx1, by1, bx2, by2 = seg(j)
            d1 = cross(bx2 - bx1, by2 - by1, ax1 - bx1, ay1 - by1)
            d2 = cross(bx2 - bx1, by2 - by1, ax2 - bx1, ay2 - by1)
            d3 = cross(ax2 - ax1, ay2 - ay1, bx1 - ax1, by1 - ay1)
            d4 = cross(ax2 - ax1, ay2 - ay1, bx2 - ax1, by2 - ay1)
            sc = max(abs(d1), abs(d2), abs(d3), abs(d4), 1.0)
            if (d1 * d2 < -tol * sc and d3 * d4 < -tol * sc):
                return False
    return True


def _moments(ring, pmax):
    """I[i][j] = int_Om xi^i eta^j dxi deta for i+j <= pmax, EXACTLY.

    Green's theorem turns the area integral into a ring integral,
        int int xi^i eta^j = oint xi^(i+1) eta^j/(i+1) d eta,
    and each edge is a straight line, so one Gauss rule per edge (exact for the
    degree i+j+1 polynomial it produces) makes the result exact up to roundoff.
    This is why the interior integrals carry no discretisation error at all.
    """
    n = len(ring)
    # a rule exact to degree pmax+1 on [0, 1]
    ng = max(2, (pmax + 2 + 1) // 2 + 1)
    xs, ws = _gauss(ng)
    out = [[0.0] * (pmax + 1) for _ in range(pmax + 1)]
    for e in range(n):
        x1, y1 = ring[e]
        x2, y2 = ring[(e + 1) % n]
        dx, dy = x2 - x1, y2 - y1
        if dy == 0.0:
            continue
        for (g, wg) in zip(xs, ws):
            t = 0.5 * (g + 1.0)
            xi = x1 + t * dx
            eta = y1 + t * dy
            wt = 0.5 * wg * dy
            pk = [1.0] * (pmax + 2)
            for k in range(1, pmax + 2):
                pk[k] = pk[k - 1] * xi
            qk = [1.0] * (pmax + 1)
            for k in range(1, pmax + 1):
                qk[k] = qk[k - 1] * eta
            for i in range(pmax + 1):
                fac = wt / (i + 1.0) * pk[i + 1]
                row = out[i]
                for j in range(pmax + 1 - i):
                    row[j] += fac * qk[j]
    return out


# --------------------------------------------------------------- boundary types
_EDGE_BC = ("ss", "cc", "free")


def _edge_bcs(mount, ring, x0, y0, x1, y1):
    """Per-edge boundary condition, or (None, error message)."""
    n = len(ring)
    key = str(mount).strip().lower()
    if key in ("ssss", "edges"):
        return ["ss"] * n, None
    if key in ("cccc", "clamped"):
        return ["cc"] * n, None
    if key == "free":
        return ["free"] * n, None
    if key == "cantilever":
        # the edge(s) on the bbox x-min side are clamped, the rest free. A
        # polygon has no "the whole side", so an edge counts when BOTH of its
        # endpoints sit on that line.
        tol = 1e-6 * max(abs(x1 - x0), abs(y1 - y0), 1.0)
        out = []
        for i in range(n):
            ax, ay = ring[i]
            bx, by = ring[(i + 1) % n]
            if abs(ax - x0) <= tol and abs(bx - x0) <= tol:
                out.append("cc")
            else:
                out.append("free")
        if "cc" not in out:
            return None, ("mount='cantilever' needs an edge on the bbox x-min "
                          "side and this outline has none")
        return out, None
    if key == "corners":
        return ["free"] * n, None
    return None, ("unknown mount %r for a polygonal outline - use ssss / "
                  "clamped / free / cantilever / corners, or edges_bc" % (mount,))


def _rigid_count(bcs, pts):
    """How many rigid-body modes survive the given supports.

    A plate's rigid modes are span{1, x, y}. An edge that is ss or cc removes all
    three; point supports remove only the translations/rotations that move them,
    so ONE point support leaves two (rotations about that point) and TWO leave
    one. Getting this count wrong is not cosmetic: it is the health test that
    tells a spurious zero-frequency mode (penalty too strong) from a real one.
    """
    if any(b != "free" for b in bcs):
        return 0
    rows = [[1.0, float(sx), float(sy)] for (sx, sy) in pts]
    if not rows:
        return 3
    scale = max([abs(v) for r in rows for v in r[1:]] + [1.0])
    tol = 1e-9 * scale
    rank = 0
    used = [False] * len(rows)
    for col in range(3):
        piv = -1
        best = tol
        for i, r in enumerate(rows):
            if not used[i] and abs(r[col]) > best:
                best, piv = abs(r[col]), i
        if piv < 0:
            continue
        used[piv] = True
        rank += 1
        pr = rows[piv]
        for i, r in enumerate(rows):
            if i != piv and r[col] != 0.0:
                f = r[col] / pr[col]
                for c in range(3):
                    r[c] -= f * pr[c]
    return max(0, 3 - rank)

def _corner_supports(ring, x0, y0, x1, y1):
    """The ring vertex nearest each bbox corner: the polygon's point supports."""
    wants = [(x0, y0), (x1, y0), (x0, y1), (x1, y1)]
    out = []
    for (tx, ty) in wants:
        best, bi = None, 0
        for i, (px, py) in enumerate(ring):
            d = (px - tx) ** 2 + (py - ty) ** 2
            if best is None or d < best:
                best, bi = d, i
        out.append(ring[bi])
    return out


# ---------------------------------------------------------------------- solver
def _solve_once(ring_mm, x0, y0, a_mm, b_mm, h_mm, props, bcs, p, pts,
                alphas, n_need):
    """Assemble once, run the penalty ladder. Returns (runs, meta) or (None, err)."""
    n = len(ring_mm)
    am = a_mm * 1e-3
    bm = b_mm * 1e-3
    hm = h_mm * 1e-3
    E = props["e"]
    nu = props["nu"]
    rho = props["rho"]
    D = E * hm ** 3 / (12.0 * (1.0 - nu * nu))
    mpa = rho * hm

    nring = [((x - x0) / a_mm, (y - y0) / b_mm) for (x, y) in ring_mm]
    monos = [(d - j, j) for d in range(p + 1) for j in range(d + 1)]
    nb = len(monos)
    if nb > _MAX_DOF:
        return None, ("poly_degree %d needs %d monomials, over the %d budget"
                      % (p, nb, _MAX_DOF))

    I = _moments(nring, 2 * p)
    jac = am * bm
    # d/dxi = 1/a dx, so w_xx = w_xixi/a^2 and dA = jac dxi deta
    K = [[0.0] * nb for _ in range(nb)]
    M = [[0.0] * nb for _ in range(nb)]
    ijk = []
    for (i, j) in monos:
        ijk.append((i, j, i * (i - 1.0), j * (j - 1.0), i * j))
    for ai in range(nb):
        i1, j1, x1_, y1_, z1_ = ijk[ai]
        for bi in range(ai, nb):
            i2, j2, x2_, y2_, z2_ = ijk[bi]
            M[ai][bi] = mpa * jac * I[i1 + i2][j1 + j2]
            st = 0.0
            if x1_ and x2_:
                st += x1_ * x2_ * I[i1 + i2 - 4][j1 + j2] / (am ** 4)
            if y1_ and y2_:
                st += y1_ * y2_ * I[i1 + i2][j1 + j2 - 4] / (bm ** 4)
            if x1_ and y2_:
                st += nu * x1_ * y2_ * I[i1 + i2 - 2][j1 + j2 - 2] / (am * am * bm * bm)
            if x2_ and y1_:
                st += nu * x2_ * y1_ * I[i1 + i2 - 2][j1 + j2 - 2] / (am * am * bm * bm)
            if z1_ and z2_:
                st += 2.0 * (1.0 - nu) * z1_ * z2_ * I[i1 + i2 - 2][j1 + j2 - 2] / (am * am * bm * bm)
            K[ai][bi] = D * jac * st
    for ai in range(nb):
        for bi in range(ai + 1, nb):
            K[bi][ai] = K[ai][bi]
            M[bi][ai] = M[ai][bi]

    # ---- boundary Gram matrices, in PHYSICAL units (metres)
    xs, ws = _gauss(_NQ1)
    Gw = [[0.0] * nb for _ in range(nb)]
    Gn = [[0.0] * nb for _ in range(nb)]
    have_ss = have_cc = False
    for ei in range(n):
        if bcs[ei] == "free":
            continue
        have_ss = have_ss or bcs[ei] == "ss"
        have_cc = have_cc or bcs[ei] == "cc"
        ax, ay = nring[ei]
        bx, by = nring[(ei + 1) % n]
        ex, ey = (bx - ax) * am, (by - ay) * bm
        Ls = math.hypot(ex, ey)
        if Ls <= 0.0:
            continue
        nxp, nyp = ey / Ls, -ex / Ls
        for (g, wg) in zip(xs, ws):
            s = 0.5 * (g + 1.0)
            xi = ax + s * (bx - ax)
            eta = ay + s * (by - ay)
            wt = 0.5 * wg * Ls
            mv = []; vn = []
            for (i, j) in monos:
                mv.append(xi ** i * eta ** j)
                mx = i * xi ** (i - 1) * eta ** j if i else 0.0
                my = j * xi ** i * eta ** (j - 1) if j else 0.0
                vn.append(mx * nxp / am + my * nyp / bm)
            for ai in range(nb):
                for bi in range(ai, nb):
                    Gw[ai][bi] += mv[ai] * mv[bi] * wt
                    if have_cc:
                        Gn[ai][bi] += vn[ai] * vn[bi] * wt
    for ai in range(nb):
        for bi in range(ai + 1, nb):
            Gw[bi][ai] = Gw[ai][bi]
            Gn[bi][ai] = Gn[ai][bi]

    # ---- mass-metric whitening: modified Gram-Schmidt in <a,b> = a^T M b
    # The inner product is carried through M v instead of v, so each projection
    # costs a dot product: <q, v>_M = q . (M v). M q is cached per retained
    # vector, so subtracting one direction only costs a vector axpy.
    Q = []
    MQ = []
    for k in range(nb):
        v = [0.0] * nb
        v[k] = 1.0
        Mv = [M[i][k] for i in range(nb)]      # M e_k is column k of M
        n0 = M[k][k]
        if n0 <= 0.0:
            continue
        for _pass in range(2):          # re-orthogonalise: monomials are far
            for mi in range(len(Q)):    # from orthogonal to start with
                q = Q[mi]
                d = sum(q[i] * Mv[i] for i in range(nb))
                if d == 0.0:
                    continue
                mq = MQ[mi]
                v = [v[i] - d * q[i] for i in range(nb)]
                Mv = [Mv[i] - d * mq[i] for i in range(nb)]
        nv = sum(v[i] * Mv[i] for i in range(nb))
        if nv <= 1e-14 * n0:
            continue
        sc = 1.0 / math.sqrt(nv)
        Q.append([x * sc for x in v])
        MQ.append([x * sc for x in Mv])
    rk = len(Q)
    if rk < 3:
        return None, ("only %d independent functions survive in this basis, "
                      "which cannot describe a plate mode" % rk)

    def whiten(A):
        # B = Q^T A Q, formed as (A Q) then Q^T (A Q): O(nb^2 rk), not O(nb^4).
        AQ = [[sum(A[i][j] * Q[bi][j] for j in range(nb))
               for bi in range(rk)] for i in range(nb)]
        B = [[sum(Q[ai][i] * AQ[i][bi] for i in range(nb))
              for bi in range(rk)] for ai in range(rk)]
        for i in range(rk):
            for j in range(i + 1, rk):
                B[i][j] = B[j][i] = 0.5 * (B[i][j] + B[j][i])
        return B

    Kw = whiten(K)
    Gww = whiten(Gw) if (have_ss or have_cc) else None
    Gnw = whiten(Gn) if have_cc else None
    Lref = max(am, bm)
    qp = []
    for (sx, sy) in pts:
        pc = [(sx - x0) / a_mm, (sy - y0) / b_mm]
        mono = [pc[0] ** i * pc[1] ** j for (i, j) in monos]
        qp.append([sum(Q[k][i] * mono[i] for i in range(nb)) for k in range(rk)])

    runs = []
    for al in alphas:
        # C is the whole penalty term, kept separately so each mode can say how
        # much of its eigenvalue came from the constraint rather than from
        # bending. A mode that is mostly penalty is NOT a plate mode - it is the
        # basis failing to represent that shape, pushed up by spurious stiffness.
        Ct = [[0.0] * rk for _ in range(rk)]
        A = [row[:] for row in Kw]
        if Gww is not None:
            cw = al * D / Lref ** 3
            for i in range(rk):
                row = A[i]
                gi = Gww[i]
                ci = Ct[i]
                for j in range(rk):
                    row[j] += cw * gi[j]
                    ci[j] += cw * gi[j]
        if Gnw is not None:
            cn = al * D / Lref
            for i in range(rk):
                row = A[i]
                gi = Gnw[i]
                ci = Ct[i]
                for j in range(rk):
                    row[j] += cn * gi[j]
                    ci[j] += cn * gi[j]
        if qp:
            cp = al * D / Lref ** 2
            for i in range(rk):
                row = A[i]
                ci = Ct[i]
                for pc in qp:
                    cj = cp * pc[i]
                    if cj == 0.0:
                        continue
                    for j in range(rk):
                        t = cj * pc[j]
                        row[j] += t
                        ci[j] += t
        evals, evecs = _vib._jacobi(A)
        nkeep = min(len(evals), n_need)
        coef = [[sum(Q[i][k] * evecs[m][i] for i in range(rk)) for k in range(nb)]
                for m in range(nkeep)]
        penf = []
        for m in range(nkeep):
            ev_m = evecs[m]
            ce = [sum(Ct[i][j] * ev_m[j] for j in range(rk)) for i in range(rk)]
            pen = sum(ev_m[i] * ce[i] for i in range(rk))
            lam = evals[m]
            penf.append(0.0 if lam <= 0.0 else min(1.0, max(0.0, pen / lam)))
        runs.append((al, evals, coef, penf))
    meta = {"nb": nb, "rank": rk, "monos": monos, "D": D, "mpa": mpa,
            "have_ss": have_ss, "have_cc": have_cc}
    return runs, meta


def plate_modes_polygon(ring_mm, thickness_mm, material=None, e_gpa=None,
                        nu=None, rho_kg_m3=None, mount="ssss", edges_bc=None,
                        poly_degree=_DEFAULT_P, n_modes=4, penalty_alphas=None,
                        probe_points=None, supports=None):
    """Natural frequencies of a thin plate whose outline is `ring_mm`.

    ring_mm          list of (x_mm, y_mm) - any vertex order, any position
    mount            ssss / edges, clamped / cccc, free, cantilever, corners
    edges_bc         per-edge list of "ss" / "cc" / "free" (overrides mount)
    poly_degree      monomial total degree (8 by default; a clamped outline
                     needs >= 8 before the basis can satisfy w = dw/dn = 0)
    penalty_alphas   the penalty ladder to sweep; the alpha where the ladder
                     flattens is reported, and the step size there is the
                     honest uncertainty of the answer (see `basis.alpha_scan`)
    supports         extra point supports [(x_mm, y_mm)]

    Same envelope as `plate_modes` (status / value / unit / model / precision).
    """
    try:
        ring = [(float(x), float(y)) for (x, y) in ring_mm]
    except (TypeError, ValueError):
        return _vib.err("ring_mm must be a sequence of (x_mm, y_mm) pairs",
                        "ARG_INVALID")
    dedup = []
    for pt in ring:
        if (not dedup or abs(pt[0] - dedup[-1][0]) > 1e-12
                or abs(pt[1] - dedup[-1][1]) > 1e-12):
            dedup.append(pt)
    if len(dedup) >= 3 and abs(dedup[0][0] - dedup[-1][0]) < 1e-12 \
            and abs(dedup[0][1] - dedup[-1][1]) < 1e-12:
        dedup.pop()
    ring = dedup
    if len(ring) < 3:
        return _vib.err("a polygon needs at least 3 distinct points, got %d"
                        % len(ring), "OUTLINE_INVALID")
    if not _is_simple(ring):
        return _vib.err("the outline crosses itself, so it is not a plate",
                        "OUTLINE_INVALID",
                        hint="a self-intersecting edge loop has no inside; "
                             "check the vertex order")
    area = _signed_area(ring)
    if abs(area) < 1e-9:
        return _vib.err("the outline has zero area", "OUTLINE_INVALID")
    if area < 0.0:
        ring.reverse()
        area = -area

    h_mm = _vib._pos(thickness_mm)
    if h_mm is None:
        return _vib.err("thickness_mm must be > 0", "ARG_INVALID",
                        hint="f ~ h, so the thickness cannot be defaulted")
    props, e = _vib._normalise_material(material, e_gpa, nu, rho_kg_m3)
    if e is not None:
        return e
    p = int(poly_degree)
    if p < 2 or p > 10:
        return _vib.err("poly_degree must be in 2..10", "ARG_INVALID")
    nmodes = int(n_modes)
    if nmodes < 1 or nmodes > 12:
        return _vib.err("n_modes must be in 1..12", "ARG_INVALID")
    alphas = []
    for a in (penalty_alphas if penalty_alphas is not None else _ALPHAS):
        try:
            av = float(a)
        except (TypeError, ValueError):
            return _vib.err("penalty_alphas must be numbers", "ARG_INVALID")
        if av <= 0.0:
            return _vib.err("penalty_alphas must be > 0", "ARG_INVALID")
        alphas.append(av)
    if not alphas:
        return _vib.err("penalty_alphas is empty", "ARG_INVALID")

    n = len(ring)
    xs = [q[0] for q in ring]
    ys = [q[1] for q in ring]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    a_mm = x1 - x0
    b_mm = y1 - y0
    if a_mm <= 0.0 or b_mm <= 0.0:
        return _vib.err("the outline bbox has zero size", "OUTLINE_INVALID")

    if edges_bc is None:
        bcs, msg = _edge_bcs(mount, ring, x0, y0, x1, y1)
        if bcs is None:
            return _vib.err(msg, "MOUNT_UNKNOWN")
    else:
        try:
            bcs = [str(b).strip().lower() for b in edges_bc]
        except TypeError:
            return _vib.err("edges_bc must be a sequence of strings", "ARG_INVALID")
        if len(bcs) != n:
            return _vib.err("edges_bc has %d entries but the ring has %d edges"
                            % (len(bcs), n), "ARG_INVALID")
        for b in bcs:
            if b not in _EDGE_BC:
                return _vib.err("edge boundary condition %r is not one of "
                                "ss / cc / free" % (b,), "ARG_INVALID")

    pts = []
    if supports:
        for s in supports:
            try:
                pts.append((float(s[0]), float(s[1])))
            except (TypeError, ValueError, IndexError):
                return _vib.err("supports must be a sequence of (x_mm, y_mm)",
                                "ARG_INVALID")
    if str(mount).strip().lower() == "corners" and edges_bc is None:
        for cpt in _corner_supports(ring, x0, y0, x1, y1):
            if cpt not in pts:
                pts.append(cpt)

    constrained = any(b != "free" for b in bcs) or bool(pts)
    expect_rigid = _rigid_count(bcs, pts)

    runs, meta = _solve_once(ring, x0, y0, a_mm, b_mm, h_mm, props, bcs, p,
                             pts, alphas, max(nmodes, 6))
    if runs is None:
        return _vib.err(meta, "INTERNAL", hint="try a lower poly_degree")
    mpa = meta["mpa"]

    def freqs_of(ev):
        """Whitened eigenvalue IS omega^2 (the masses carry rho*h)."""
        lam_max = max(ev) if ev else 1.0
        out = []
        for lam in ev:
            if lam <= 1e-9 * max(abs(lam_max), 1e-30):
                out.append(0.0)
            else:
                out.append(math.sqrt(lam) / (2.0 * math.pi))
        return out

    # ---- penalty continuation: take the largest alpha that still yields a
    # healthy spectrum (rigid-body count as expected, no spurious zeros), and
    # report the change across the last step as the answer's uncertainty.
    healthy = []
    for (al, ev, cf, pf) in runs:
        ff = freqs_of(ev)
        zeros = sum(1 for x in ff if x == 0.0)
        if zeros == expect_rigid and (ff and ff[-1] > 0.0):
            healthy.append((al, ev, cf, ff, pf))
    if not healthy:
        return _vib.err(
            "no penalty strength in the ladder produced a usable spectrum",
            "NO_CONVERGENCE",
            hint="the basis cannot satisfy this boundary condition; try a "
                 "higher poly_degree or a simpler outline")
    # The alpha to report is the one where the ladder has FLATTENED, not simply
    # the largest healthy one. On an outline whose basis can satisfy the
    # constraints the ladder keeps improving as alpha grows, so the flattest
    # step is the last one; on a finely faceted outline the basis cannot make
    # the boundary values vanish at all, and past a knee every further factor of
    # ten just adds spurious stiffness (a 64-gon simply supported read 562 Hz
    # at 1e6 and 585 Hz at 1e7 against a near-circular truth of 554 Hz).
    def f_ref(ff):
        """First elastic mode: a free plate's first three are rigid-body zeros,
        so mode 0 says nothing about whether the penalty ladder has settled."""
        for x in ff:
            if x > 0.0:
                return x
        return 0.0

    best_i = 0
    best_d = None
    for i in range(1, len(healthy)):
        f_lo = f_ref(healthy[i - 1][3])
        f_hi = f_ref(healthy[i][3])
        if f_hi <= 0.0 or f_lo <= 0.0:
            continue
        d = abs(f_hi - f_lo) / f_hi
        if best_d is None or d <= best_d:      # ties go to the larger alpha
            best_d, best_i = d, i
    chosen = healthy[best_i]
    spread = 0.0 if best_d is None else best_d * 100.0

    al_used, evals, coefs, freqs, penf = chosen

    # ---- anti-node / quiet area, over points inside the polygon only
    NG = 41
    grid = []
    for gi in range(NG):
        xi = gi / float(NG - 1)
        for gj in range(NG):
            eta = gj / float(NG - 1)
            if _vib_inside(ring, x0 + xi * a_mm, y0 + eta * b_mm):
                grid.append((xi, eta))
    if not grid:
        return _vib.err("the outline bbox contains no interior sample",
                        "OUTLINE_INVALID")

    probes_out = None
    if probe_points:
        probes_out = []
        for pp in probe_points:
            try:
                px = float(pp.get("x_mm"))
                py = float(pp.get("y_mm"))
            except (TypeError, ValueError, AttributeError):
                return _vib.err("each probe point needs numeric x_mm and y_mm",
                                "ARG_INVALID")
            probes_out.append({"name": pp.get("name"), "x_mm": px, "y_mm": py,
                               "xi": (px - x0) / a_mm, "eta": (py - y0) / b_mm})

    def eval_coef(c, xi, eta):
        s = 0.0
        for (i, j), cc in zip(meta["monos"], c):
            if cc != 0.0:
                s += cc * xi ** i * eta ** j
        return s

    # how badly the first mode violates the boundary condition it was given
    bc_res_mm = None
    if constrained and coefs:
        amp = max([abs(eval_coef(coefs[0], xi, eta)) for (xi, eta) in grid] or [0.0])
        if amp > 0.0:
            acc = 0.0
            cnt = 0
            for ei in range(n):
                if bcs[ei] == "free":
                    continue
                ax, ay = ring[ei]
                bx, by = ring[(ei + 1) % n]
                for step in range(5):
                    s = (step + 0.5) / 5.0
                    acc += eval_coef(coefs[0], (ax + s * (bx - ax) - x0) / a_mm,
                                     (ay + s * (by - ay) - y0) / b_mm) ** 2
                    cnt += 1
            for (sx, sy) in pts:
                acc += eval_coef(coefs[0], (sx - x0) / a_mm, (sy - y0) / b_mm) ** 2
                cnt += 1
            if cnt:
                bc_res_mm = _vib._sig(math.sqrt(acc / cnt) / amp * max(a_mm, b_mm), 3)

    modes = []
    penalty_limited = 0
    lam_max = max(evals) if evals else 1.0
    for mi, lam in enumerate(evals):
        if mi >= nmodes:
            break
        is_rigid = lam <= 1e-9 * max(abs(lam_max), 1e-30)
        entry = {
            "index": mi + 1,
            "freq_hz": _vib._sig(freqs[mi]),
            "period_ms": (_vib._sig(1000.0 / freqs[mi]) if freqs[mi] > 0 else None),
            "lambda": _vib._sig(lam * mpa),
            "rigid_body": is_rigid,
        }
        if not is_rigid:
            # how much of this mode's omega^2 came from the penalty and not
            # from bending the plate: > 0.5 means the frequency is spurious
            ps = penf[mi] if mi < len(penf) else 0.0
            entry["penalty_share"] = _vib._sig(ps, 3)
            entry["physical"] = bool(ps <= 0.5)
            if not entry["physical"]:
                penalty_limited += 1
        if not is_rigid and mi < len(coefs):
            c = coefs[mi]
            best = -1.0
            best_xy = (0.0, 0.0)
            vals = []
            for (xi, eta) in grid:
                ph = eval_coef(c, xi, eta)
                vals.append(ph)
                if abs(ph) > best:
                    best = abs(ph)
                    best_xy = (xi, eta)
            peak = best if best > 0.0 else 1.0
            quiet = sum(1 for v in vals if abs(v) < 0.1 * peak) / float(len(vals))
            entry["antinode_mm"] = [_vib._sig(x0 + best_xy[0] * a_mm),
                                    _vib._sig(y0 + best_xy[1] * b_mm)]
            entry["quiet_area_fraction"] = _vib._sig(quiet, 3)
            if probes_out:
                dp = []
                for pp in probes_out:
                    v = eval_coef(c, pp["xi"], pp["eta"]) / peak
                    dp.append({"name": pp["name"],
                               "normalised_deflection": _vib._sig(v, 4)})
                entry["deflection_at_probes"] = dp
        modes.append(entry)

    warnings = []
    if penalty_limited:
        warnings.append(
            "%d of the %d reported modes carry more than half of their "
            "omega^2 in the boundary penalty (penalty_share > 0.5), so their "
            "frequencies are spurious stiffness and not plate modes: this "
            "basis cannot represent that many shapes under this boundary "
            "condition - read only the modes flagged physical=True"
            % (penalty_limited, min(nmodes, len(evals))))
    if spread > 3.0:
        warnings.append(
            "the penalty ladder is still moving f1 by %.1f %% even at its "
            "flattest step (alpha=%.0e was used): treat this number as "
            "indicative only - a finely faceted or re-entrant outline is the "
            "hard case for a global polynomial basis, because no polynomial of "
            "this degree can make the boundary deflection vanish on every edge"
            % (spread, al_used))
    if len(healthy) == 1:
        warnings.append(
            "only one penalty strength (%.0e) gave a healthy spectrum, so the "
            "answer carries no penalty-continuation evidence at all" % al_used)
    if len(healthy) < len(runs):
        warnings.append(
            "%d of %d penalty strengths were rejected because the spectrum was "
            "not healthy (a spurious zero-frequency mode appeared)"
            % (len(runs) - len(healthy), len(runs)))
    warnings.append("component mass and copper are not added, and the mounting "
                    "itself is treated as rigid")
    warnings.append("outline holes / cutouts are not meshed - only the outer "
                    "ring is solved")
    if p < 8 and (meta["have_cc"] or pts):
        warnings.append(
            "poly_degree=%d may contain no function that satisfies a clamped "
            "edge at all (w = dw/dn = 0 needs degree 8 on a rectangle), so this "
            "result can be far too high" % p)
    warnings.append("stress is not computed on a polygonal outline; use the "
                    "rectangular path for stress")

    return _vib.ok(
        {
            "outline_mm": [[_vib._sig(x), _vib._sig(y)] for (x, y) in ring],
            "bbox_mm": [_vib._sig(a_mm), _vib._sig(b_mm)],
            "area_mm2": _vib._sig(abs(area)),
            "perimeter_mm": _vib._sig(sum(math.hypot(ring[(i + 1) % n][0] - ring[i][0],
                                                     ring[(i + 1) % n][1] - ring[i][1])
                                          for i in range(n))),
            "thickness_mm": h_mm,
            "material": {"e_gpa": _vib._sig(props["e"] / 1e9), "nu": props["nu"],
                         "rho_kg_m3": props["rho"], "preset": material},
            "flexural_rigidity_n_m": _vib._sig(meta["D"]),
            "mass_per_area_kg_m2": _vib._sig(mpa),
            "bc": {"edges": [str(b) for b in bcs],
                   "point_supports_mm": [[_vib._sig(sx), _vib._sig(sy)]
                                         for (sx, sy) in pts],
                   "mount": str(mount).strip().lower()},
            "basis": {"kind": "monomial + boundary penalty",
                      "poly_degree": p,
                      "dof": meta["nb"],
                      "independent_dof": meta["rank"],
                      "penalty_alphas": alphas,
                      "reported_alpha": al_used,
                      "healthy_alphas": [h[0] for h in healthy],
                      "alpha_scan": [[h[0], _vib._sig(f_ref(h[3]))]
                                     for h in healthy],
                      "alpha_spread_pct": _vib._sig(spread, 3),
                      "alpha_converged": bool(spread <= 1.0),
                      "boundary_rms_mm": bc_res_mm,
                      "interior_integral": "exact (Green's theorem on the ring)"},
            "modes": modes,
            "warnings": warnings,
        },
        "Hz",
        "Kirchhoff thin plate on the real outline (%d edges), Rayleigh-Ritz on "
        "a monomial basis with the boundary condition imposed weakly by a "
        "penalty integral" % n,
        "Kirchhoff-Love plate theory (U = D/2 int[w_xx^2 + w_yy^2 + 2 nu w_xx "
        "w_yy + 2(1-nu) w_xy^2]) over the polygon; essential boundary "
        "conditions imposed weakly, interior integrals exact by Green's "
        "theorem on the ring (no mesh, no interior quadrature error)",
        "verified against the rectangular path on a square (ss -0.01 %, clamped "
        "+0.01 %, four corners -0.01 %, free mode 4 +0.001 %, cantilever -0.4 %) "
        "and against the circular plate eigenvalues within 1.1 % (ss) and 1.2 % "
        "(clamped); the reported alpha spread is the honest uncertainty",
        {"note": "polygonal outline: only the outer ring is solved"})


def _vib_inside(ring, x, y):
    """Even-odd point-in-polygon test."""
    n = len(ring)
    c = False
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            xx = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < xx:
                c = not c
    return c
