"""Vibration: will the board ring, and how hard.

Four questions, four levels of model:

  * what are the natural frequencies and mode shapes  -> plate_modes
        Kirchhoff thin plate, solved by Rayleigh-Ritz with a monomial basis
        plus a boundary-constraint null space. No closed-form mode table is
        used, so the same code covers simply supported / clamped / free /
        cantilever / four-corner-point support, and asymmetric a:b.
  * how hard does random vibration shake it            -> miles_response
        single-mode Miles for a flat PSD (closed form) and for a measured
        PSD curve (numerical integral of |H|^2), so the two agree inside 1 %.
  * how long until the solder/lead cracks              -> steinberg_fatigue
        Steinberg's three-band method + Miner's rule. z_allow is REQUIRED -
        this file does not own a "typical allowable deflection".
  * what does a drop / shock pulse do                  -> shock_sdof
        Newmark integration of a single-DOF oscillator (unconditionally
        stable), which reproduces the analytic step-pulse DLF of 2 exactly.

Plus transmissibility() for the "is my isolator actually isolating" question.

Everything is stdlib. The eigen-solver is a Jacobi sweep on a symmetric matrix
after mass-metric whitening, so no generalized eigenproblem is needed.
"""
import math

from .result import ok, err

G0 = 9.80665  # m/s^2

# ------------------------------------------------------------------ materials

# These presets are TYPICAL values with a stated spread, not supplier data.
# FR-4 is the one that matters: E runs 18..30 GPa with resin content and glass
# weave direction, and f ~ sqrt(E), so a single number is +-29 % wide. Use
# fr4_low / fr4_high to bracket it, or pass e_gpa yourself.
MATERIALS = {
    "fr4":       {"e_gpa": 24.0,  "nu": 0.15, "rho_kg_m3": 1850.0},
    "fr4_low":   {"e_gpa": 18.0,  "nu": 0.15, "rho_kg_m3": 1850.0},
    "fr4_high":  {"e_gpa": 30.0,  "nu": 0.15, "rho_kg_m3": 1850.0},
    "polyimide": {"e_gpa": 3.5,   "nu": 0.34, "rho_kg_m3": 1420.0},
    "al6061":    {"e_gpa": 69.0,  "nu": 0.33, "rho_kg_m3": 2700.0},
    "steel":     {"e_gpa": 200.0, "nu": 0.29, "rho_kg_m3": 7850.0},
    "alumina":   {"e_gpa": 300.0, "nu": 0.22, "rho_kg_m3": 3900.0},
}

MATERIAL_NOTE = ("preset = typical value, not a supplier datasheet; FR-4 E spans "
                 "18..30 GPa with resin content and glass weave, and f ~ sqrt(E)")

MOUNTS = ("edges", "ssss", "clamped", "cccc", "free", "cantilever", "corners",
          "custom")

# The maximum number of Ritz degrees of freedom. 44 keeps one 4-mode solve at
# ~20 ms of pure-Python Jacobi; above that we lower the polynomial degree and
# say so in the envelope rather than silently getting slow.
MAX_DOF = 44


def _num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v != v or v in (float("inf"), float("-inf")):
        return None
    return v


def _pos(x):
    v = _num(x)
    if v is None or v <= 0.0:
        return None
    return v


# ------------------------------------------------------- linear algebra bits

def _moments(K):
    """Moments of the monomials psi_k(xi) = xi**k on [0, 1], k = 0..K.

    Returns (M0, D1, I2, IC, I1):
      M0[k][l] = int psi_k psi_l
      D1[k][l] = int psi_k' psi_l'
      I2[k][l] = int psi_k'' psi_l''
      IC[k][l] = int psi_k'' psi_l
      I1[k][l] = int psi_k psi_l''            (= IC[l][k], kept apart on purpose)
    """
    n = K + 1
    M0 = [[0.0] * n for _ in range(n)]
    D1 = [[0.0] * n for _ in range(n)]
    I2 = [[0.0] * n for _ in range(n)]
    IC = [[0.0] * n for _ in range(n)]
    I1 = [[0.0] * n for _ in range(n)]
    for k in range(n):
        for l in range(n):
            s = k + l
            M0[k][l] = 1.0 / (s + 1)
            # the numerators vanish exactly on the denominators' singular
            # points (s=1 for the first derivatives, s=3 for the second), so
            # guarding on s keeps every entry finite and correct.
            if s >= 2:
                D1[k][l] = (k * l) / float(s - 1)
                IC[k][l] = (k * (k - 1)) / float(s - 1)
                I1[k][l] = (l * (l - 1)) / float(s - 1)
            if s >= 4:
                I2[k][l] = (k * (k - 1) * l * (l - 1)) / float(s - 3)
    return M0, D1, I2, IC, I1


def _bc_rows(K, left, right):
    """Constraint rows (coefficients on the monomials) for one axis.

    xi = 0 end:  simply supported -> w(0)=0 and w''(0)=0, and on the monomials
                 that is exactly a_0 = 0 and 2*a_2 = 0.
                 clamped        -> w(0)=0 and w'(0)=0 -> a_0 = 0 and a_1 = 0.
    xi = 1 end:  simply supported -> sum a_k = 0 and sum k(k-1) a_k = 0.
                 clamped        -> sum a_k = 0 and sum k a_k = 0.
    free end:    nothing.
    """
    n = K + 1
    rows = []
    if left == "ss":
        rows.append([1.0] + [0.0] * (n - 1))
        r = [0.0] * n
        if n > 2:
            r[2] = 2.0
        rows.append(r)
    elif left == "cc":
        rows.append([1.0] + [0.0] * (n - 1))
        r = [0.0] * n
        if n > 1:
            r[1] = 1.0
        rows.append(r)
    elif left != "free":
        return None
    if right == "ss":
        rows.append([1.0] * n)
        rows.append([float(k * (k - 1)) for k in range(n)])
    elif right == "cc":
        rows.append([1.0] * n)
        rows.append([float(k) for k in range(n)])
    elif right != "free":
        return None
    return rows


def _orth_nullspace(rows, n):
    """Orthonormal basis of the null space of `rows` (m x n), as n x (n-m).

    Modified Gram-Schmidt on the constraint rows to get an orthonormal Q, then
    project the identity and re-orthonormalise the surviving columns. Dependent
    constraint rows are dropped instead of producing an ill-conditioned basis.
    """
    q = []
    for r in rows:
        v = r[:]
        for qq in q:
            d = sum(a * b for a, b in zip(qq, v))
            v = [x - d * y for x, y in zip(v, qq)]
        nv = math.sqrt(sum(x * x for x in v))
        if nv < 1e-9:
            continue
        q.append([x / nv for x in v])
    m = len(q)
    if m >= n:
        return None, 0
    cols = []
    for k in range(n):
        v = [0.0] * n
        v[k] = 1.0
        for qq in q:
            d = qq[k]
            v = [x - d * y for x, y in zip(v, qq)]
        for cc in cols:
            d = sum(a * b for a, b in zip(cc, v))
            v = [x - d * y for x, y in zip(v, cc)]
        nv = math.sqrt(sum(x * x for x in v))
        if nv < 1e-8:
            continue
        cols.append([x / nv for x in v])
    if len(cols) != n - m:
        return None, 0
    return cols, m


def _basis(K, left, right):
    """(K+1 x nfree) monomial coefficients of an admissible 1-D basis."""
    rows = _bc_rows(K, left, right)
    if rows is None:
        return None
    cols, _m = _orth_nullspace(rows, K + 1)
    if cols is None:
        return None
    n = K + 1
    # cols is a list of column vectors; B[k][c] is the monomial coefficient
    B = [[cols[c][k] for c in range(len(cols))] for k in range(n)]
    return B


def _matmul(A, B):
    """A (m x k) times B (k x n)."""
    m = len(A)
    k = len(B)
    n = len(B[0]) if k else 0
    out = [[0.0] * n for _ in range(m)]
    for i in range(m):
        Ai = A[i]
        Oi = out[i]
        for p in range(k):
            a = Ai[p]
            if a == 0.0:
                continue
            Bp = B[p]
            for j in range(n):
                Oi[j] += a * Bp[j]
    return out


def _transpose(A):
    return [list(col) for col in zip(*A)]


def _quad_form(B, Br):
    """B^T Br B, with B (nmono x nfree) and Br (nmono x nmono)."""
    return _matmul(_matmul(_transpose(B), Br), B)


def _cholesky(A):
    n = len(A)
    L = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1):
            s = A[i][j]
            for k in range(j):
                s -= L[i][k] * L[j][k]
            if i == j:
                if s <= 0.0:
                    return None
                L[i][i] = math.sqrt(s)
            else:
                L[i][j] = s / L[j][j]
    return L


def _lower_inverse(L):
    """L^-1 for a lower-triangular L (also lower triangular)."""
    n = len(L)
    X = [[0.0] * n for _ in range(n)]
    for i in range(n):
        X[i][i] = 1.0 / L[i][i]
        for j in range(i):
            s = 0.0
            for k in range(j, i):
                s += L[i][k] * X[k][j]
            X[i][j] = -s / L[i][i]
    return X


def _jacobi(A, tol=1e-13, max_sweeps=60):
    """Cyclic Jacobi for a symmetric matrix.

    Returns (eigenvalues ascending, eigenvectors as columns of a list-of-lists).
    A small dense symmetric problem is all this layer ever produces (<= 44), so a
    sweep-based rotation method is both shorter and more accurate than anything
    iterative.
    """
    n = len(A)
    a = [row[:] for row in A]
    v = [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]
    scale = 0.0
    for i in range(n):
        scale += abs(a[i][i])
    if scale <= 0.0:
        scale = 1.0
    for _sweep in range(max_sweeps):
        off = 0.0
        for i in range(n):
            for j in range(i + 1, n):
                off += a[i][j] * a[i][j]
        if math.sqrt(off) <= tol * scale:
            break
        for p in range(n - 1):
            for q in range(p + 1, n):
                apq = a[p][q]
                if abs(apq) < 1e-300:
                    continue
                theta = (a[q][q] - a[p][p]) / (2.0 * apq)
                if theta >= 0.0:
                    t = 1.0 / (theta + math.sqrt(theta * theta + 1.0))
                else:
                    t = -1.0 / (-theta + math.sqrt(theta * theta + 1.0))
                c = 1.0 / math.sqrt(t * t + 1.0)
                s = t * c
                for k in range(n):
                    akp = a[k][p]
                    akq = a[k][q]
                    a[k][p] = c * akp - s * akq
                    a[k][q] = s * akp + c * akq
                for k in range(n):
                    apk = a[p][k]
                    aqk = a[q][k]
                    a[p][k] = c * apk - s * aqk
                    a[q][k] = s * apk + c * aqk
                for k in range(n):
                    vkp = v[k][p]
                    vkq = v[k][q]
                    v[k][p] = c * vkp - s * vkq
                    v[k][q] = s * vkp + c * vkq
    idx = sorted(range(n), key=lambda i: a[i][i])
    vals = [a[i][i] for i in idx]
    # v[r][c] is component r of eigenvector c, so the MODE c is column c:
    # return modes as rows (vecs[c][r]) - getting this the other way round keeps
    # the eigenvalues right and silently mixes up the mode shapes.
    return vals, [[v[r][i] for r in range(n)] for i in idx]


def _sig(x, digits=6):
    """Round for output, keeping tiny rigid-body eigenvalues readable."""
    if x == 0.0:
        return 0.0
    return float("%.*e" % (digits - 1, x))


# ------------------------------------------------------------- plate modes

_BC_PAIRS = {
    "edges": (("ss", "ss"), ("ss", "ss")),
    "ssss": (("ss", "ss"), ("ss", "ss")),
    "clamped": (("cc", "cc"), ("cc", "cc")),
    "cccc": (("cc", "cc"), ("cc", "cc")),
    "free": (("free", "free"), ("free", "free")),
    "cantilever": (("cc", "free"), ("free", "free")),
    "corners": (("free", "free"), ("free", "free")),
}


def _normalise_material(material, e_gpa, nu, rho_kg_m3):
    mats = None
    if material is not None:
        key = str(material).strip().lower()
        if key not in MATERIALS:
            return None, err(
                "unknown material preset %r" % (material,), "MATERIAL_UNKNOWN",
                hint="known presets: %s; or pass e_gpa + nu + rho_kg_m3 directly"
                     % (", ".join(sorted(MATERIALS)),))
        mats = MATERIALS[key]
    e = _num(e_gpa)
    if e is not None:
        e = e * 1e9          # the argument is named e_GPA - convert here, once,
                             # or a preset (Pa internally) and an explicit value
                             # (GPa) would differ by 1e9 and look like a stiff
                             # board rather than a unit bug
    n = _num(nu)
    r = _num(rho_kg_m3)
    if mats:
        if e is None:
            e = mats["e_gpa"] * 1e9
        if n is None:
            n = mats["nu"]
        if r is None:
            r = mats["rho_kg_m3"]
    if e is None or n is None or r is None:
        return None, err(
            "material properties incomplete",
            "ARG_REQUIRED",
            hint="give material='fr4' (a preset) or all of e_gpa, nu, rho_kg_m3")
    if e <= 0.0 or r <= 0.0:
        return None, err("e_gpa and rho_kg_m3 must be > 0", "ARG_INVALID")
    if not (-0.99 < n < 0.5):
        return None, err("nu = %r is outside (-1, 0.5), which no isotropic solid is"
                         % (nu,), "ARG_INVALID")
    return {"e": e, "nu": n, "rho": r}, None


def _pick_degree(n_pairs, poly_degree, max_dof):
    """Lower the polynomial degree until the DOF budget is met."""
    K = int(poly_degree)
    while K >= 3:
        Bx = _basis(K, n_pairs[0][0], n_pairs[0][1])
        By = _basis(K, n_pairs[1][0], n_pairs[1][1])
        if Bx is not None and By is not None:
            if len(Bx[0]) * len(By[0]) <= max_dof:
                return K, Bx, By
        K -= 1
    return None, None, None


def plate_modes(a_mm, b_mm, thickness_mm, material=None, e_gpa=None, nu=None,
                rho_kg_m3=None, mount="corners", bc_x=None, bc_y=None,
                n_modes=4, poly_degree=7, probe_points=None, max_dof=MAX_DOF,
                stress_amplitude_mm=None):
    """Natural frequencies and mode shapes of a rectangular thin plate.

    Rayleigh-Ritz on a monomial basis (psi = xi^k eta^l) restricted to the null
    space of the boundary constraints, whitened by the mass metric, then a plain
    Jacobi eigendecomposition of the (symmetric) stiffness matrix.

    mount:
      edges / ssss   four edges simply supported
      clamped / cccc four edges clamped
      free           no support (3 rigid-body modes appear at 0 Hz)
      cantilever     x=0 clamped, every other edge free
      corners        free edges with w=0 at the four corners (point support)
      custom         use bc_x / bc_y, each a (left, right) pair of
                     "ss" | "cc" | "free"

    The Kirchhoff strain energy is
        U = D/2 * int[ w_xx^2 + w_yy^2 + 2 nu w_xx w_yy + 2(1-nu) w_xy^2 ]
    with D = E h^3 / (12 (1 - nu^2)) and mass per area rho*h.

    Not modelled, and said so in the envelope: added mass and stiffening from
    the components and copper, the compliance of the mounting itself, shear
    deformation and rotary inertia (thin-plate limit), and the error of
    rectangularising a non-rectangular outline.
    """
    a = _pos(a_mm)
    b = _pos(b_mm)
    h_mm = _pos(thickness_mm)
    if a is None or b is None or h_mm is None:
        return err("a_mm, b_mm and thickness_mm must all be > 0", "ARG_INVALID",
                   hint="plate_modes needs the three dimensions of the plate")
    props, e = _normalise_material(material, e_gpa, nu, rho_kg_m3)
    if e is not None:
        return e
    key = str(mount).strip().lower()
    if key == "custom":
        if not (bc_x and bc_y):
            return err("mount='custom' needs bc_x and bc_y", "ARG_REQUIRED",
                       hint='e.g. bc_x=["ss","free"], bc_y=["free","free"]')
        try:
            pairs = ((str(bc_x[0]).lower(), str(bc_x[1]).lower()),
                     (str(bc_y[0]).lower(), str(bc_y[1]).lower()))
        except (IndexError, TypeError):
            return err("bc_x / bc_y must be two-element sequences", "ARG_INVALID")
    elif key in _BC_PAIRS:
        pairs = _BC_PAIRS[key]
    else:
        return err("unknown mount %r" % (mount,), "MOUNT_UNKNOWN",
                   hint="known mounts: %s" % (", ".join(MOUNTS),))
    for p in pairs:
        for side in p:
            if side not in ("ss", "cc", "free"):
                return err("boundary %r is not one of ss / cc / free" % (side,),
                           "ARG_INVALID")

    nmodes = int(n_modes)
    if nmodes < 1 or nmodes > 12:
        return err("n_modes must be in 1..12", "ARG_INVALID")
    amp_mm = None if stress_amplitude_mm is None else _num(stress_amplitude_mm)
    if stress_amplitude_mm is not None:
        if amp_mm is None or amp_mm <= 0:
            return err("stress_amplitude_mm must be a number > 0", "ARG_RANGE",
                       hint="the peak out-of-plane deflection of THIS mode, in mm - "
                            "take it from miles_response' z_3sigma_mm or from a "
                            "measurement. A mode shape on its own has no amplitude, "
                            "and a stress computed from an unnormalised eigenvector "
                            "is a number with no plate behind it")
    K, Bx, By = _pick_degree(pairs, poly_degree, max_dof)
    if K is None:
        return err("no polynomial degree >= 3 fits the DOF budget", "ARG_INVALID",
                   hint="raise max_dof or pick a more constrained mount")

    M0, D1, I2, IC, I1 = _moments(K)
    nx = len(Bx[0])
    ny = len(By[0])

    # --- mass-metric whitening: M1 = L L^T, W = B L^-T  =>  W^T M0 W = I
    def whiten(B):
        M1 = _quad_form(B, M0)
        L = _cholesky(M1)
        if L is None:
            return None
        X = _lower_inverse(L)
        nmono = len(B)
        W = [[0.0] * len(X) for _ in range(nmono)]
        for k in range(nmono):
            for j in range(len(X)):
                s = 0.0
                for i in range(len(X)):
                    s += B[k][i] * X[j][i]
                W[k][j] = s
        return W

    Wx = whiten(Bx)
    Wy = whiten(By)
    if Wx is None or Wy is None:
        return err("mass matrix is not positive definite for this basis",
                   "INTERNAL", hint="try a lower poly_degree")

    def wq(W, Br):
        return _quad_form(W, Br)

    M0xw = wq(Wx, M0)
    D1xw = wq(Wx, D1)
    I2xw = wq(Wx, I2)
    ICxw = wq(Wx, IC)
    I1xw = wq(Wx, I1)
    M0yw = wq(Wy, M0)
    D1yw = wq(Wy, D1)
    I2yw = wq(Wy, I2)
    ICyw = wq(Wy, IC)
    I1yw = wq(Wy, I1)

    am = a * 1e-3
    bm = b * 1e-3
    hm = h_mm * 1e-3
    E = props["e"]
    nu_v = props["nu"]
    rho = props["rho"]
    Dflex = E * hm ** 3 / (12.0 * (1.0 - nu_v * nu_v))
    mpa = rho * hm  # kg/m^2

    ndof = nx * ny
    stiff = [[0.0] * ndof for _ in range(ndof)]
    a4 = am ** 4
    b4 = bm ** 4
    a2b2 = am * am * bm * bm
    for i in range(nx):
        for j in range(ny):
            I = i * ny + j
            for k in range(nx):
                for l in range(ny):
                    J = k * ny + l
                    val = (
                        I2xw[i][k] * M0yw[j][l] / a4
                        + M0xw[i][k] * I2yw[j][l] / b4
                        + nu_v * (ICxw[i][k] * I1yw[j][l]
                                  + I1xw[i][k] * ICyw[j][l]) / a2b2
                        + (1.0 - nu_v) * (D1xw[i][k] * D1yw[j][l]
                                          + D1xw[i][k] * D1yw[j][l]) / a2b2
                    )
                    # U = D/2 * int[...] is a quadratic form, so d2U gives the
                    # stiffness as D * int[...] - the 1/2 cancels. Writing 0.5
                    # here halves every eigenvalue and drops every frequency by
                    # exactly sqrt(2), which looks plausible and is wrong.
                    stiff[I][J] += Dflex * val

    # corners: point supports at the four corners of the [0,a] x [0,b] rectangle
    point_fix = (key == "corners")
    if point_fix:
        pts = [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (1.0, 1.0)]
        rows = []
        for xi, eta in pts:
            row = []
            for i in range(nx):
                fx = sum(Wx[k][i] * (xi ** k) for k in range(K + 1))
                for j in range(ny):
                    fy = sum(Wy[k][j] * (eta ** k) for k in range(K + 1))
                    row.append(fx * fy)
            rows.append(row)

        def shape(xi, eta):
            out = []
            for i in range(nx):
                fx = sum(Wx[k][i] * (xi ** k) for k in range(K + 1))
                for j in range(ny):
                    fy = sum(Wy[k][j] * (eta ** k) for k in range(K + 1))
                    out.append(fx * fy)
            return out
    else:
        rows = []

        def shape(xi, eta):
            out = []
            for i in range(nx):
                fx = sum(Wx[k][i] * (xi ** k) for k in range(K + 1))
                for j in range(ny):
                    fy = sum(Wy[k][j] * (eta ** k) for k in range(K + 1))
                    out.append(fx * fy)
            return out

    def d2vec(vec, xi, eta):
        """[w_xx, w_yy, w_xy] in 1/m^2 for a mode whose coefficients are `vec`.

        The Ritz basis is separable - X_i(xi) * Y_j(eta) with xi = x/a - so the
        curvatures are (1/a^2) * sum c_ij X_i'' Y_j, (1/b^2) * sum c_ij X_i Y_j''
        and (1/(ab)) * sum c_ij X_i' Y_j'. The eigenvector carries an ARBITRARY
        scale (it is mass-orthonormal), so these are shape curvatures, not the
        curvatures of a plate that is actually deflecting by anything.
        """
        xs = []
        for i in range(nx):
            x0 = x1 = x2 = 0.0
            for k in range(K + 1):
                c = Wx[k][i]
                if c == 0.0:
                    continue
                x0 += c * (xi ** k)
                if k >= 1:
                    x1 += k * c * (xi ** (k - 1))
                if k >= 2:
                    x2 += k * (k - 1) * c * (xi ** (k - 2))
            xs.append((x0, x1, x2))
        ys = []
        for j in range(ny):
            y0 = y1 = y2 = 0.0
            for k in range(K + 1):
                c = Wy[k][j]
                if c == 0.0:
                    continue
                y0 += c * (eta ** k)
                if k >= 1:
                    y1 += k * c * (eta ** (k - 1))
                if k >= 2:
                    y2 += k * (k - 1) * c * (eta ** (k - 2))
            ys.append((y0, y1, y2))
        wxx = wyy = wxy = 0.0
        for i in range(nx):
            x0, x1, x2 = xs[i]
            for j in range(ny):
                cij = vec[i * ny + j]
                if cij == 0.0:
                    continue
                y0, y1, y2 = ys[j]
                wxx += cij * x2 * y0
                wyy += cij * x0 * y2
                wxy += cij * x1 * y1
        return (wxx / (am * am), wyy / (bm * bm), wxy / (am * bm))

    if rows:
        cols, _m = _orth_nullspace(rows, ndof)
        if cols is None:
            return err("the point supports leave no admissible mode", "ARG_INVALID",
                       hint="K=%d gives %d DOF; four corner constraints ate them all"
                            % (K, ndof))
        # cols is a list of COLUMNS: q[p] is basis vector p, of length ndof.
        q = cols
        ndof2 = len(q)
        stiff2 = [[0.0] * ndof2 for _ in range(ndof2)]
        for p in range(ndof2):
            qp = q[p]
            for r in range(ndof2):
                qr = q[r]
                s = 0.0
                for I2i in range(ndof):
                    aq = qp[I2i]
                    if aq == 0.0:
                        continue
                    rowI = stiff[I2i]
                    for J in range(ndof):
                        s += aq * rowI[J] * qr[J]
                stiff2[p][r] = s
        stiff = stiff2
        base_cols = q
        ndof = ndof2
    else:
        base_cols = None

    evals, evecs = _jacobi(stiff)

    # omega^2 = lambda / mass_per_area   (the mass matrix is the identity)
    freqs = []
    for lam in evals:
        if lam <= 0.0:
            freqs.append(0.0)
        else:
            freqs.append(math.sqrt(lam / mpa) / (2.0 * math.pi))

    # mode shapes in the full monomial space for the requested DOF
    def full_vec(col):
        if base_cols is None:
            return col
        nfull = len(base_cols[0]) if base_cols else 0
        out = [0.0] * nfull
        for p in range(len(col)):
            c = col[p]
            if c == 0.0:
                continue
            qp = base_cols[p]
            for I2i in range(nfull):
                out[I2i] += qp[I2i] * c
        return out

    probes_out = None
    if probe_points:
        probes_out = []
        for pp in probe_points:
            try:
                px = float(pp.get("x_mm"))
                py = float(pp.get("y_mm"))
            except (TypeError, ValueError, AttributeError):
                return err("each probe point needs numeric x_mm and y_mm",
                           "ARG_INVALID")
            probes_out.append({"name": pp.get("name"), "x_mm": px, "y_mm": py,
                               "xi": px / a, "eta": py / b})

    modes = []
    rid = []
    NG = 41
    lam_max = max(evals) if evals else 1.0
    for mi, lam in enumerate(evals):
        if mi >= nmodes:
            break
        vec = full_vec(evecs[mi])
        shp = shape
        # grid scan for the antinode and for the quiet-area fraction
        best = -1.0
        best_xy = (0.0, 0.0)
        vals = []
        for gi in range(NG):
            xi = gi / float(NG - 1)
            for gj in range(NG):
                eta = gj / float(NG - 1)
                ph = sum(vec[I2i] * v for I2i, v in enumerate(shp(xi, eta)))
                vals.append(ph)
                if abs(ph) > best:
                    best = abs(ph)
                    best_xy = (xi * a, eta * b)
        peak = best if best > 0.0 else 1.0
        quiet = sum(1 for v in vals if abs(v) < 0.1 * peak) / float(len(vals))
        is_rigid = lam <= 1e-9 * lam_max
        entry = {
            "index": mi + 1,
            "freq_hz": _sig(freqs[mi]),
            "period_ms": _sig(1000.0 / freqs[mi]) if freqs[mi] > 0 else None,
            "lambda": _sig(lam),
            "rigid_body": is_rigid,
        }
        if not is_rigid:
            # a rigid-body mode has no antinode and no quiet area; reporting
            # one would be a number with no meaning behind it
            entry["antinode_mm"] = [_sig(best_xy[0]), _sig(best_xy[1])]
            entry["quiet_area_fraction"] = _sig(quiet, 3)
        if amp_mm is not None and not is_rigid and "stress" not in entry:
            # 21 x 21 is enough for a peak: the curvature field is smooth and the
            # second derivative of a degree-K polynomial is degree K-2.
            ng = 21
            scale = (amp_mm * 1e-3) / peak      # metres per unit coefficient
            ez = E * (hm / 2.0) / (1.0 - nu_v * nu_v)
            g1 = E / (2.0 * (1.0 + nu_v))
            # Two different questions, two different peaks. von Mises is largest
            # where the TWISTING curvature w_xy is largest (a corner, for a
            # sinusoid-like mode); the largest principal stress is where the
            # bending curvatures w_xx/w_yy are largest (the middle of a bay).
            # Reporting one point for both is how a "max stress" ends up 36 %
            # below the real one.
            best_vm = None
            best_s = None
            best_t = None
            for gi in range(ng):
                xi = gi / float(ng - 1)
                for gj in range(ng):
                    eta = gj / float(ng - 1)
                    kx, ky, kxy = d2vec(vec, xi, eta)
                    kx *= scale
                    ky *= scale
                    kxy *= scale
                    sx = -ez * (kx + nu_v * ky)
                    sy = -ez * (ky + nu_v * kx)
                    txy = -2.0 * g1 * (hm / 2.0) * kxy
                    mid = 0.5 * (sx + sy)
                    rr = math.sqrt(0.25 * (sx - sy) ** 2 + txy * txy)
                    hi = mid + rr
                    lo = mid - rr
                    vm = math.sqrt(hi * hi - hi * lo + lo * lo)
                    smax = max(abs(hi), abs(lo))
                    rec = {"vm": vm, "smax": smax, "x": xi * a, "y": eta * b,
                           "sx": sx, "sy": sy, "t": txy, "hi": hi, "lo": lo,
                           "kx": kx, "ky": ky, "kxy": kxy,
                           "corner": (xi * (1.0 - xi) < 1e-12
                                      and eta * (1.0 - eta) < 1e-12)}
                    if best_vm is None or vm > best_vm["vm"]:
                        best_vm = rec
                    if best_s is None or smax > best_s["smax"]:
                        best_s = rec
                    at = abs(txy)
                    if best_t is None or at > best_t[0]:
                        best_t = (at, rec)
            sp = best_s
            entry["stress"] = {
                "amplitude_mm": _sig(amp_mm),
                "surface_z_mm": _sig(h_mm / 2.0),
                # ---- extremes, each at its own location
                "von_mises_mpa": _sig(best_vm["vm"] / 1e6),
                "von_mises_at_mm": [_sig(best_vm["x"]), _sig(best_vm["y"])],
                "sigma_max_mpa": _sig(sp["smax"] / 1e6),
                "sigma_max_at_mm": [_sig(sp["x"]), _sig(sp["y"])],
                "tau_xy_max_mpa": _sig(best_t[0] / 1e6),
                "tau_xy_max_at_mm": [_sig(best_t[1]["x"]), _sig(best_t[1]["y"])],
                # ---- the full tensor AT the sigma_max point
                "at_mm": [_sig(sp["x"]), _sig(sp["y"])],
                "sigma_1_mpa": _sig(sp["hi"] / 1e6),
                "sigma_2_mpa": _sig(sp["lo"] / 1e6),
                "sigma_x_mpa": _sig(sp["sx"] / 1e6),
                "sigma_y_mpa": _sig(sp["sy"] / 1e6),
                "tau_xy_mpa": _sig(sp["t"] / 1e6),
                "curvature_per_m": [_sig(sp["kx"]), _sig(sp["ky"])],
                "surface_strain_ue": _sig(1e6 * abs(sp["hi"]) / E),
                "peak_locations_note":
                    ("the reported extremes sit in symmetry-equivalent sets (the "
                     "four corners share one value, so does each bay centre); one "
                     "point of each set is listed")
                    if (best_vm["corner"] and sp["corner"]) else
                    ("von Mises peaks where w_xy does, sigma_max where w_xx/w_yy do: "
                     "they are different points and both are reported"),
                "formula": "sigma_x = -E z/(1-nu^2) (w_xx + nu w_yy), "
                           "sigma_y = -E z/(1-nu^2) (w_yy + nu w_xx), "
                           "tau_xy = -E z/(1+nu) w_xy, z = h/2",
            }
        if probes_out is not None:
            d = []
            for pp in probes_out:
                ph = sum(vec[I2i] * v
                         for I2i, v in enumerate(shp(pp["xi"], pp["eta"])))
                d.append({"name": pp["name"], "normalised_deflection": _sig(ph / peak, 4)})
            entry["deflection_at_probes"] = d
        modes.append(entry)

    # rigid-body modes: only possible with a free edge pair, and they land at 0
    for mi, lam in enumerate(evals):
        if lam <= 1e-9 * lam_max:
            rid.append(mi + 1)

    warnings = []
    if "fr4" == str(material or "").strip().lower():
        warnings.append(
            "material preset 'fr4' pins E=24 GPa; the real spread is 18..30 GPa, "
            "so f1 is +-13 % around this number - rerun with fr4_low / fr4_high")
    if key in ("free", "corners"):
        warnings.append(
            "point/free support is singular at the support: f1 converges slowly "
            "with the polynomial degree, treat the first mode as +-10 %")
    if abs(a - b) > 0.2 * max(a, b):
        pass
    warnings.append(
        "component mass, copper stiffening, mounting compliance and shear "
        "deformation are not modelled")

    return ok(
        {
            "a_mm": a, "b_mm": b, "thickness_mm": h_mm,
            "material": {
                "e_gpa": _sig(E / 1e9), "nu": nu_v, "rho_kg_m3": rho,
                "preset": material,
            },
            "flexural_rigidity_n_m": _sig(Dflex),
            "mass_per_area_kg_m2": _sig(mpa),
            "bc": {"x": list(pairs[0]), "y": list(pairs[1]),
                   "mount": key, "point_supports": 4 if point_fix else 0},
            "basis": {"poly_degree": K, "n_x": nx, "n_y": ny, "dof": ndof,
                      "auto_reduced": K != int(poly_degree)},
            "rigid_body_modes": rid,
            "modes": modes,
            "stress_not_computed": (None if amp_mm is None else [
                "stress concentrations: a hole, a notch, a connector footprint and "
                "a solder fillet all concentrate stress, and none of them is modelled",
                "solder-joint fatigue and creep (the joint is the usual failure site "
                "on a vibrating board, and it is not this plate)",
                "interlaminar shear and the copper/laminate interface",
                "geometric nonlinearity: past roughly half the plate thickness "
                "membrane action stiffens the plate and linear theory overstates "
                "the stress",
                "damping, and therefore the real amplitude - the amplitude is the "
                "caller's, not something this solve produces",
            ]),
            "warnings": warnings,
        },
        "Hz",
        "Kirchhoff thin plate, Rayleigh-Ritz (monomial basis + boundary null "
        "space + mass-metric whitening + Jacobi)",
        "Kirchhoff-Love plate theory (U = D/2 int[w_xx^2 + w_yy^2 + 2 nu w_xx w_yy "
        "+ 2(1-nu) w_xy^2]); no standard number (no mode table is used)",
        "converged in poly_degree: verified within 0.2 % of the analytic SSSS and "
        "CCCC eigenvalues at K=7; thin-plate limit only (h/a << 1)",
        {
            "note": MATERIAL_NOTE,
            "probe_points": probes_out,
        },
    )


# ------------------------------------------------------------ random / Miles

def _sdof_zeta(q_factor, zeta):
    if zeta is not None:
        z = _num(zeta)
        if z is None or not (0.0 < z < 1.0):
            return None, err("zeta must be in (0, 1) for a single-DOF mode",
                             "ARG_INVALID")
        return z, None
    if q_factor is not None:
        q = _num(q_factor)
        if q is None or q <= 0.0:
            return None, err("q_factor must be > 0", "ARG_INVALID")
        z = 1.0 / (2.0 * q)
        if z >= 1.0:
            return None, err("q_factor = %r gives zeta >= 1 (over-damped)" % (q,),
                             "ARG_INVALID")
        return z, None
    return None, err("give either zeta or q_factor = 1/(2 zeta)",
                     "ARG_REQUIRED",
                     hint="a bare Q=10 (zeta=0.05) is the usual first guess for a "
                          "PCB mode, but say which one you mean")


def _psd_at(points, f):
    """Linear interpolation, clamped at the ends."""
    if f <= points[0][0]:
        return points[0][1]
    if f >= points[-1][0]:
        return points[-1][1]
    lo, hi = 0, len(points) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if points[mid][0] <= f:
            lo = mid
        else:
            hi = mid
    f0, w0 = points[lo]
    f1, w1 = points[hi]
    if f1 == f0:
        return w0
    return w0 + (w1 - w0) * (f - f0) / (f1 - f0)


def miles_response(f_n_hz, psd=None, zeta=None, q_factor=None, gamma=3.0):
    """Single-mode random response: g_rms, and the displacement that follows.

    Flat PSD -> Miles' closed form  g_rms = sqrt(pi/2 * f_n * Q * W).
    PSD curve -> the same quantity by numerically integrating
                 g_rms^2 = int W(f) |H(f)|^2 df, with |H| the relative
                 displacement transmissibility of the single-DOF mode.
    The two agree to well under 1 % on a flat PSD, which is the selftest.

    This is a SINGLE-MODE estimate: it ignores the contribution of the other
    modes, of which the second one is often only 1.5x away, and it treats the
    input as applied to the base. It also says nothing about stress - give the
    result to steinberg_fatigue() for that.
    """
    f_n = _pos(f_n_hz)
    if f_n is None:
        return err("f_n_hz must be > 0", "ARG_INVALID",
                   hint="take it from plate_modes()['modes'][0]['freq_hz']")
    z, e = _sdof_zeta(q_factor, zeta)
    if e is not None:
        return e
    g = _num(gamma)
    if g is None or g <= 0.0:
        return err("gamma (peak factor) must be > 0", "ARG_INVALID")
    if not isinstance(psd, dict):
        return err("psd must be an object", "ARG_REQUIRED",
                   hint='either {"g2_per_hz": 0.1} or '
                        '{"points": [[10, 0.01], [100, 0.1], [2000, 0.01]]}')

    warnings = []
    method = None
    if "g2_per_hz" in psd:
        W = _num(psd.get("g2_per_hz"))
        if W is None or W <= 0.0:
            return err("psd.g2_per_hz must be > 0", "ARG_INVALID")
        q = 1.0 / (2.0 * z)
        grms = math.sqrt(0.5 * math.pi * f_n * q * W)
        method = "closed_form_miles (flat PSD)"
        integral = None
    elif "points" in psd:
        pts = []
        try:
            for p in psd["points"]:
                fv = _pos(p[0])
                wv = _num(p[1])
                if fv is None or wv is None or wv < 0.0:
                    raise ValueError
                pts.append((fv, wv))
        except (TypeError, IndexError, ValueError):
            return err("psd.points must be [[hz, g2/Hz], ...] with hz > 0",
                       "ARG_INVALID")
        if len(pts) < 2:
            return err("psd.points needs at least two breakpoints", "ARG_INVALID")
        if pts[-1][0] < 3.0 * f_n:
            warnings.append("PSD ends at %.0f Hz, below 3*f_n = %.0f Hz; the "
                            "integral is clamped to the last value there"
                            % (pts[-1][0], 3.0 * f_n))
        # three panels: coarse below, fine across the resonance, coarse above.
        # A uniform grid cannot resolve a Q=100 peak (width 1 %) without
        # hundreds of thousands of points.
        delta = max(12.0 * z, 0.02)
        segs = ((0.0, max(0.0, 1.0 - delta), 800),
                (max(0.0, 1.0 - delta), 1.0 + delta, 3000),
                (1.0 + delta, 3.0, 800))
        total = 0.0
        c = 2.0 * z
        for r0, r1, nsteps in segs:
            if r1 <= r0:
                continue
            hstep = (r1 - r0) / nsteps
            prev = None
            for i in range(nsteps + 1):
                r = r0 + i * hstep
                fr = r * f_n
                amp = 1.0 / ((1.0 - r * r) ** 2 + (c * r) ** 2)
                val = _psd_at(pts, fr) * amp
                if prev is not None:
                    total += 0.5 * (prev + val) * hstep
                prev = val
        grms = math.sqrt(max(0.0, total * f_n))   # df = f_n dr
        method = "numerical integral of W(f)|H(f)|^2 (piecewise-linear PSD)"
        integral = {"segments": len(segs), "relative_peak_width": delta}
        if pts[-1][1] > 2.0 * pts[0][1] or pts[0][1] > 2.0 * pts[-1][1]:
            warnings.append("PSD varies by more than 2x across the band; "
                            "single-mode Miles assumes the mode sits in a band "
                            "where W is roughly flat - check the number against "
                            "a full modal summation")
    else:
        return err("psd has neither g2_per_hz nor points", "ARG_REQUIRED")

    omega = 2.0 * math.pi * f_n
    disp_rms = grms * G0 / (omega * omega)      # metres
    return ok(
        {
            "f_n_hz": f_n,
            "zeta": _sig(z, 6),
            "q_factor": _sig(1.0 / (2.0 * z), 6),
            "g_rms": _sig(grms),
            "g_peak": _sig(g * grms),
            "peak_factor": g,
            "displacement_rms_mm": _sig(disp_rms * 1000.0),
            "displacement_peak_mm": _sig(disp_rms * 1000.0 * g),
            "method": method,
            "integral": integral,
            "warnings": warnings,
        },
        "g",
        "single-DOF (Miles) random vibration",
        "Miles' equation g_rms = sqrt(pi/2 * f_n * Q * W) for a flat PSD; "
        "otherwise the defining integral int W(f)|H(f)|^2 df",
        "single mode only; Q and the PSD are inputs, not derived here; "
        "closed form and numerical integral agree inside 1 % on a flat PSD",
        {
            "note": "g_peak = gamma * g_rms with gamma the peak factor (2 sigma = "
                    "95 %, 3 sigma = 99.7 % for a Gaussian response)",
        },
    )


# ------------------------------------------------------- Steinberg / Miner

# Steinberg's three-band split for a Gaussian narrow-band response.
STEINBERG_BANDS = ((3.0, 0.3333), (2.0, 0.2713), (1.0, 0.3954))


def steinberg_fatigue(f_n_hz, z_3sigma_mm, z_allow_mm=None, exponent=6.4,
                      n0_cycles=2.0e7, target_hours=None):
    """Steinberg three-band fatigue + Miner's rule.

    Split the 3-sigma deflection into three ranges (3s / 2s / 1s at the stated
    cycle fractions), turn each into an allowable cycle count
        N_i = N0 * (z_allow / delta_i) ** b
    and accumulate damage D = f_n * sum(fraction_i / N_i) per second.

    z_allow_mm is REQUIRED and is never inferred: it is the deflection the
    solder joint or lead can take at the number of cycles you are designing for
    (N0). Steinberg's own numbers for leaded components are ~+/-1.5 mm at
    20e6 cycles, but that is a class of part, not this part - pass the value
    from your component's own qualification report.

    b = 6.4 is Steinberg's slope for lead wires; solder joints and BGA balls use
    other slopes and other allowables. Only the mechanism you supply a number
    for is covered.
    """
    f_n = _pos(f_n_hz)
    z3 = _pos(z_3sigma_mm)
    za = _pos(z_allow_mm)
    b = _num(exponent)
    n0 = _pos(n0_cycles)
    if f_n is None:
        return err("f_n_hz must be > 0", "ARG_INVALID")
    if z3 is None:
        return err("z_3sigma_mm must be > 0", "ARG_INVALID",
                   hint="miles_response(...)['displacement_peak_mm'] with gamma=3")
    if za is None:
        return err("z_allow_mm is REQUIRED", "ARG_REQUIRED",
                   hint="this file owns no 'typical allowable deflection'; use the "
                        "deflection your component's own qualification allows at "
                        "n0_cycles (Steinberg's leaded-parts class value is ~1.5 mm "
                        "at 2e7 cycles)")
    if b is None or b <= 2.0:
        return err("exponent must be > 2 (Steinberg uses 6.4 for lead wires)",
                   "ARG_INVALID")
    if n0 is None:
        return err("n0_cycles must be > 0", "ARG_INVALID")

    s_rms = z3 / 3.0
    bands = []
    rate = 0.0   # damage per second
    for mult, frac in STEINBERG_BANDS:
        amp = mult * s_rms
        cycles = n0 * (za / amp) ** b
        if cycles <= 0.0:
            return err("damage model degenerate at band %g sigma" % mult, "INTERNAL")
        rate += frac / cycles
        bands.append({
            "band_sigma": mult,
            "amplitude_mm": _sig(amp),
            "cycle_fraction": frac,
            "allowable_cycles": _sig(cycles),
        })
    damage_per_second = f_n * rate
    damage_per_hour = damage_per_second * 3600.0
    if damage_per_hour <= 0.0:
        return err("damage rate is zero, which cannot be right", "INTERNAL")

    life_s = 1.0 / damage_per_second
    out = {
        "f_n_hz": f_n,
        "z_3sigma_mm": z3,
        "z_allow_mm": za,
        "exponent": b,
        "n0_cycles": n0,
        "bands": bands,
        "damage_per_hour": _sig(damage_per_hour),
        "life_hours": _sig(life_s / 3600.0),
        "life_years": _sig(life_s / 3600.0 / 8766.0),
        "warnings": [
            "single mode, narrow-band Gaussian response assumed; wide-band or "
            "multi-mode response needs a modal summation and a different damage "
            "rule",
            "z_allow and the exponent are your inputs - the answer is only as "
            "good as the qualification data behind them",
        ],
    }
    th = _num(target_hours)
    if th is not None:
        if th <= 0.0:
            return err("target_hours must be > 0", "ARG_INVALID")
        out["target_hours"] = th
        out["margin"] = _sig((life_s / 3600.0) / th)   # >1 means it survives
        out["pass"] = bool(life_s / 3600.0 >= th)
    elif target_hours is not None:
        return err("target_hours must be a number", "ARG_INVALID")
    return ok(
        out, "h",
        "Steinberg three-band fatigue + Miner's rule",
        "Steinberg, 'Vibration Analysis for Electronic Equipment' (3-band split "
        "0.3333/0.2713/0.3954, N = N0 (z_allow/z)^b) and Miner's linear damage",
        "single mode, narrow band; b and z_allow are caller inputs; the band "
        "split is the textbook Gaussian one",
        {
            "note": "life = 1 / (f_n * sum(fraction_i / N_i)); nothing here says "
                    "WHICH joint fails, only how fast the one you described",
        },
    )


# ----------------------------------------------------------------- shock

def _pulse_value(kind, amp, dur, t):
    if kind == "step":
        return amp
    if t < 0.0:
        return 0.0
    if kind == "rect":
        return amp if t <= dur else 0.0
    if kind == "half_sine":
        if t >= dur:
            return 0.0
        return amp * math.sin(math.pi * t / dur)
    return None


def shock_sdof(f_n_hz, zeta=None, q_factor=None, pulse=None):
    """Single-DOF response to a base acceleration pulse (Newmark).

    u'' + 2 zeta w u' + w^2 u = -a_base(t)

    Newmark with beta=1/4, gamma=1/2 (average acceleration) is unconditionally
    stable and, for a linear system, gives the step-pulse DLF of exactly 2 with
    no damping - that is the selftest.

    pulse:
      {"type": "step",      "amplitude_g": 30}
      {"type": "rect",      "amplitude_g": 30, "duration_ms": 2}
      {"type": "half_sine", "amplitude_g": 30, "duration_ms": 2}
    A step never ends; use rect / half_sine for a real shock.
    """
    f_n = _pos(f_n_hz)
    if f_n is None:
        return err("f_n_hz must be > 0", "ARG_INVALID")
    z, e = _sdof_zeta(q_factor, zeta)
    if e is not None:
        return e
    if not isinstance(pulse, dict):
        return err("pulse must be an object", "ARG_REQUIRED",
                   hint='{"type": "half_sine", "amplitude_g": 30, "duration_ms": 2}')
    kind = str(pulse.get("type", "")).strip().lower()
    if kind not in ("step", "rect", "half_sine"):
        return err("unknown pulse type %r" % (pulse.get("type"),), "ARG_INVALID",
                   hint="step / rect / half_sine")
    amp_g = _num(pulse.get("amplitude_g"))
    if amp_g is None or amp_g <= 0.0:
        return err("pulse.amplitude_g must be > 0", "ARG_INVALID")
    dur_ms = None
    if kind != "step":
        dur_ms = _pos(pulse.get("duration_ms"))
        if dur_ms is None:
            return err("pulse.duration_ms is required for %r" % kind, "ARG_REQUIRED")
        dur = dur_ms * 1e-3
    else:
        dur = 1.0

    a0 = amp_g * G0
    omega = 2.0 * math.pi * f_n
    Tn = 1.0 / f_n
    dt = min(Tn / 24.0, (dur / 60.0) if kind != "step" else Tn / 24.0)
    t_end = max(3.0 * Tn, 2.0 * dur)
    nsteps = int(math.ceil(t_end / dt))
    if nsteps > 200000:
        return err("pulse needs %d steps at dt=%.3g s; the pulse is too long "
                   "relative to f_n" % (nsteps, dt), "ARG_INVALID",
                   hint="reduce duration_ms or split the problem")

    c = 2.0 * z * omega
    k = omega * omega
    beta = 0.25
    gam = 0.5
    u = 0.0
    v = 0.0
    # u''(0) = -a_base(0) - c*v - k*u
    acc = -_pulse_value(kind, a0, dur, 0.0)

    peak_u = 0.0
    peak_acc_abs = abs(acc + _pulse_value(kind, a0, dur, 0.0))
    peak_time = 0.0
    c0 = 1.0 / (beta * dt * dt)
    c1 = gam / (beta * dt)
    c2 = 1.0 / (beta * dt)
    c3 = 1.0 / (2.0 * beta) - 1.0
    k_eff = c0 + c1 * c + k
    t = 0.0
    for step in range(nsteps):
        t_next = t + dt
        p_next = -_pulse_value(kind, a0, dur, t_next)
        p_eff = (p_next + c0 * u + c2 * v + c3 * acc
                 + c * (c1 * u + (gam / beta - 1.0) * v
                        + dt * (gam / (2.0 * beta) - 1.0) * acc))
        u_next = p_eff / k_eff
        acc_next = c0 * (u_next - u) - c2 * v - c3 * acc
        v_next = v + dt * ((1.0 - gam) * acc + gam * acc_next)
        u, v, acc = u_next, v_next, acc_next
        t = t_next
        if abs(u) > peak_u:
            peak_u = abs(u)
            peak_time = t
        tot = abs(acc + _pulse_value(kind, a0, dur, t))
        if tot > peak_acc_abs:
            peak_acc_abs = tot

    static_u = a0 / k
    dlf_disp = peak_u / static_u if static_u > 0.0 else None
    return ok(
        {
            "f_n_hz": f_n,
            "zeta": _sig(z, 6),
            "pulse": {"type": kind, "amplitude_g": amp_g,
                      "duration_ms": dur_ms},
            "peak_relative_displacement_mm": _sig(peak_u * 1000.0),
            "static_displacement_mm": _sig(static_u * 1000.0),
            "displacement_amplification": _sig(dlf_disp, 6),
            "peak_absolute_acceleration_g": _sig(peak_acc_abs / G0),
            "acceleration_amplification": _sig(peak_acc_abs / a0, 6),
            "peak_time_ms": _sig(peak_time * 1000.0),
            "integration": {"dt_s": _sig(dt), "steps": nsteps,
                            "method": "Newmark beta=1/4 gamma=1/2"},
        },
        "mm",
        "single-DOF shock response, Newmark average-acceleration integration",
        "u'' + 2 zeta w u' + w^2 u = -a_base(t) integrated by Newmark "
        "(beta=1/4, gamma=1/2, unconditionally stable)",
        "step response reproduces the analytic DLF of 2 for zeta=0 exactly and "
        "1+exp(-zeta pi/sqrt(1-zeta^2)) with damping; dt <= T_n/24",
        {
            "note": "relative displacement is what stresses the mount; the "
                    "absolute acceleration is what the component feels",
        },
    )


def transmissibility(f_n_hz, zeta=None, q_factor=None, freq_hz=None,
                     freq_range=None):
    """|T| of a single-DOF mount: is the isolator isolating, or amplifying?

    |T| = sqrt((1 + (2 zeta r)^2) / ((1 - r^2)^2 + (2 zeta r)^2)),  r = f/f_n

    Isolation starts at r = sqrt(2) - below that the mount makes things worse,
    which is the whole point of reporting isolation_onset_hz.

    Give either freq_hz (a list) or freq_range = [f_start, f_stop, n_points].
    """
    f_n = _pos(f_n_hz)
    if f_n is None:
        return err("f_n_hz must be > 0", "ARG_INVALID")
    z, e = _sdof_zeta(q_factor, zeta)
    if e is not None:
        return e

    def amp(r):
        num = 1.0 + (2.0 * z * r) ** 2
        den = (1.0 - r * r) ** 2 + (2.0 * z * r) ** 2
        return math.sqrt(num / den)

    pts = []
    if freq_hz is not None:
        try:
            for fv in freq_hz:
                f = _pos(fv)
                if f is None:
                    raise ValueError
                pts.append({"freq_hz": _sig(f), "ratio": _sig(f / f_n, 6),
                            "transmissibility": _sig(amp(f / f_n), 6)})
        except (TypeError, ValueError):
            return err("freq_hz must be a list of positive numbers", "ARG_INVALID")
    elif freq_range is not None:
        try:
            f0 = _pos(freq_range[0])
            f1 = _pos(freq_range[1])
            n = int(freq_range[2])
        except (TypeError, IndexError, ValueError):
            return err("freq_range must be [f_start, f_stop, n_points]", "ARG_INVALID")
        if f0 is None or f1 is None or f1 <= f0 or not (2 <= n <= 5000):
            return err("freq_range needs 0 < f_start < f_stop and 2..5000 points",
                       "ARG_INVALID")
        for i in range(n):
            f = f0 + (f1 - f0) * i / float(n - 1)
            pts.append({"freq_hz": _sig(f), "ratio": _sig(f / f_n, 6),
                        "transmissibility": _sig(amp(f / f_n), 6)})
    else:
        return err("give freq_hz or freq_range", "ARG_REQUIRED")

    r_peak = math.sqrt(max(0.0, 1.0 - 2.0 * z * z))
    return ok(
        {
            "f_n_hz": f_n,
            "zeta": _sig(z, 6),
            "q_factor": _sig(1.0 / (2.0 * z), 6),
            "peak_transmissibility": _sig(amp(r_peak) if r_peak > 0 else 1.0, 6),
            "peak_freq_hz": _sig(r_peak * f_n),
            "isolation_onset_hz": _sig(f_n * math.sqrt(2.0)),
            "points": pts,
            "isolation_band": "r > sqrt(2): the mount attenuates; below it, it amplifies",
        },
        "1",
        "single-DOF transmissibility",
        "|T| = sqrt((1+(2 zeta r)^2)/((1-r^2)^2+(2 zeta r)^2)) for base "
        "excitation of a rigid mass on a linear mount",
        "exact closed form for the stated single-DOF model; zeta is an input",
        {
            "note": "this is the idealised 1-D mount; a real isolator has "
                    "frequency-dependent stiffness and its own resonances",
        },
    )

# ------------------------------------------------------------ stress / strain

_STRESS_NOT_COMPUTED = [
    "stress concentrations: a hole, a notch, a connector footprint and a solder "
    "fillet all concentrate stress, and none of them is in this model",
    "solder-joint fatigue and creep - the joint is usually where a vibrating board "
    "actually fails, and it is not this plate",
    "interlaminar shear and the copper/laminate interface (peel and barrel stress)",
    "geometric nonlinearity: past roughly half the plate thickness membrane action "
    "stiffens the plate, so linear theory OVERSTATES the stress there",
    "damping and therefore the real amplitude; the amplitude is the caller's, and "
    "this module will not invent one",
    "residual stress from lamination and soldering, which adds to (or relieves) "
    "whatever vibration produces",
]

def plate_stress(a_mm, b_mm, thickness_mm, amplitude_mm, mode=1, material=None,
                 e_gpa=None, nu=None, rho_kg_m3=None, mount="corners",
                 bc_x=None, bc_y=None, poly_degree=None, max_dof=MAX_DOF,
                 degrees=(5, 7, 9)):
    """Peak plate stress for a given mode at a given peak deflection.

    A mode shape on its own has no amplitude, so a stress needs one supplied: pass
    the 3-sigma deflection from `miles_response`, a measured value, or an allowed
    deflection. With that, Kirchhoff theory gives the surface stress straight from
    the mode's curvatures:

        sigma_x = -E z/(1-nu^2) (w_xx + nu w_yy)
        sigma_y = -E z/(1-nu^2) (w_yy + nu w_xx)
        tau_xy  = -E z/(1+nu)   w_xy                z = h/2

    The second derivative of a polynomial fit converges SLOWER than the fit
    itself, so a frequency that looks converged says nothing about the stress.
    Rather than claim an accuracy, this runs the same mode at several polynomial
    degrees and reports the spread: `degree_sensitivity_pct` is the honest error
    bar, and it is measured on your geometry, not quoted.

    Note the cap: with MAX_DOF=44 a point-supported ("corners") plate can only
    reach degree 5, so on that mount the ladder collapses to one rung and the
    sensitivity is UNKNOWN rather than small. That is reported, not hidden.
    """
    amp = _num(amplitude_mm)
    if amp is None or amp <= 0:
        return err("amplitude_mm is required and must be > 0", "ARG_REQUIRED",
                   hint="the peak out-of-plane deflection of the mode, in mm - from "
                        "miles_response' z_3sigma_mm, a measured value, or the "
                        "deflection you are willing to allow. Zero amplitude is not "
                        "a stress, and there is no default")
    try:
        midx = int(mode)
    except (TypeError, ValueError):
        return err("mode must be an integer", "ARG_INVALID")
    if midx < 1 or midx > 12:
        return err("mode must be in 1..12", "ARG_INVALID")
    if midx < 1:
        return err("mode must be >= 1", "ARG_INVALID")

    # poly_degree CAPS the ladder; it does not replace it. Dropping it to a single
    # rung would leave the stress with no error bar at all, which is exactly the
    # number a caller cannot audit.
    top = None
    if poly_degree is not None:
        try:
            top = int(poly_degree)
        except (TypeError, ValueError):
            return err("poly_degree must be an integer", "ARG_INVALID")
        if top < 3:
            return err("poly_degree must be >= 3", "ARG_INVALID")
    try:
        ladder = [int(d) for d in (degrees or (5, 7, 9))]
    except (TypeError, ValueError):
        return err("degrees must be a list of integers", "ARG_INVALID")
    if top is not None:
        ladder = [d for d in ladder if d <= top]
        if top not in ladder:
            ladder.append(top)
    ladder = [d for d in ladder if d >= 3]
    ladder = sorted(set(ladder))
    if not ladder:
        return err("no usable polynomial degree (need >= 3)", "ARG_INVALID",
                   hint="the Ritz basis needs at least degree 3 to carry any "
                        "curvature")

    rows = []
    seen_k = set()
    for d in ladder:
        r = plate_modes(a_mm, b_mm, thickness_mm, material=material, e_gpa=e_gpa,
                        nu=nu, rho_kg_m3=rho_kg_m3, mount=mount, bc_x=bc_x,
                        bc_y=bc_y, n_modes=max(2, midx), poly_degree=d,
                        max_dof=max_dof, stress_amplitude_mm=amp)
        if r.get("status") == "error":
            return r
        val = r["value"]
        k_used = val["basis"]["poly_degree"]
        if k_used in seen_k:
            continue                     # the DOF cap clamped it: same answer twice
        seen_k.add(k_used)
        if len(val["modes"]) < midx:
            return err("mode %d does not exist for this plate (only %d modes)"
                       % (midx, len(val["modes"])), "ARG_RANGE")
        md = val["modes"][midx - 1]
        if md.get("rigid_body"):
            return err("mode %d is a rigid-body mode: it has no curvature and therefore "
                       "no stress. Ask for a flexible mode." % midx, "ARG_RANGE",
                       hint="a free or corner-supported plate has up to 3 modes at "
                            "0 Hz; start at the first non-zero frequency")
        st = md.get("stress")
        if not st:
            return err("mode %d carries no stress result" % midx, "INTERNAL")
        rows.append({"poly_degree": k_used, "dof": val["basis"]["dof"],
                     "freq_hz": md["freq_hz"],
                     "von_mises_mpa": st["von_mises_mpa"],
                     "sigma_max_mpa": st["sigma_max_mpa"],
                     "at_mm": st["sigma_max_at_mm"]})
    if not rows:
        return err("no degree produced a usable mode", "INTERNAL")

    best = rows[-1]
    sens = None
    if len(rows) >= 2:
        prev = rows[-2]
        if best["von_mises_mpa"]:
            sens = abs(best["von_mises_mpa"] - prev["von_mises_mpa"]) / \
                abs(best["von_mises_mpa"]) * 100.0

    # re-run the finest rung once more to take the full stress record (the ladder
    # only kept the summary rows)
    r = plate_modes(a_mm, b_mm, thickness_mm, material=material, e_gpa=e_gpa, nu=nu,
                    rho_kg_m3=rho_kg_m3, mount=mount, bc_x=bc_x, bc_y=bc_y,
                    n_modes=max(2, midx), poly_degree=best["poly_degree"],
                    max_dof=max_dof, stress_amplitude_mm=amp)
    if r.get("status") == "error":
        return r
    val = r["value"]
    md = val["modes"][midx - 1]
    st = dict(md["stress"])

    warnings = []
    if sens is None:
        warnings.append(
            "the degree ladder collapsed to one rung (poly_degree=%d). The DOF cap "
            "max_dof=%d will not let this mount go higher, so there is NO measured "
            "error bar on the curvature - on a point-supported plate the stress is "
            "the least trustworthy number here"
            % (best["poly_degree"], max_dof))
    elif sens > 1.0:
        warnings.append(
            "the stress still moves %.2f %% between degree %d and %d, so it is not "
            "converged: raise max_dof and poly_degree before trusting the absolute "
            "value" % (sens, rows[-2]["poly_degree"], best["poly_degree"]))
    if st["amplitude_mm"] > 0.5 * val["thickness_mm"]:
        warnings.append(
            "amplitude %.4g mm is over half the plate thickness %.4g mm: linear plate "
            "theory is out of its range there and OVERSTATES the stress"
            % (st["amplitude_mm"], val["thickness_mm"]))

    return ok({
        "mode": midx,
        "freq_hz": md["freq_hz"],
        "amplitude_mm": st["amplitude_mm"],
        "von_mises_mpa": st["von_mises_mpa"],
        "von_mises_at_mm": st["von_mises_at_mm"],
        "sigma_max_mpa": st["sigma_max_mpa"],
        "sigma_max_at_mm": st["sigma_max_at_mm"],
        "tau_xy_max_mpa": st["tau_xy_max_mpa"],
        "tau_xy_max_at_mm": st["tau_xy_max_at_mm"],
        "peak_locations_note": st["peak_locations_note"],
        "sigma_1_mpa": st["sigma_1_mpa"],
        "sigma_2_mpa": st["sigma_2_mpa"],
        "sigma_x_mpa": st["sigma_x_mpa"],
        "sigma_y_mpa": st["sigma_y_mpa"],
        "tau_xy_mpa": st["tau_xy_mpa"],
        "at_mm": st["at_mm"],
        "curvature_per_m": st["curvature_per_m"],
        "surface_strain_ue": st["surface_strain_ue"],
        "surface_z_mm": st["surface_z_mm"],
        "poly_degree": best["poly_degree"],
        "dof": best["dof"],
        "degree_ladder": rows,
        "degree_sensitivity_pct": None if sens is None else _sig(sens, 3),
        "converged": (None if sens is None else sens <= 1.0),
        "stress_not_computed": list(_STRESS_NOT_COMPUTED),
        "warnings": (warnings + val.get("warnings", [])),
    }, "MPa",
       "Kirchhoff plate stress from Rayleigh-Ritz mode curvatures "
       "(sigma = -E z/(1-nu^2)(w_xx + nu w_yy))",
       "Kirchhoff-Love thin plate, plane stress at the surface z = h/2; the "
       "amplitude is the caller's, so this is a stress PER unit deflection scaled "
       "by what the caller says the deflection is",
       "the error bar is MEASURED (degree ladder on your geometry), not quoted: "
       "curvature converges slower than frequency, and a point-supported plate caps "
       "at degree 5 with max_dof=44; stress concentrations, solder joints, "
       "interlaminar shear and residual stress are all outside this model",
       {"amplitude_source": "caller-supplied peak deflection of this mode",
        "z": "surface (h/2) - the laminate mid-plane carries none in this model",
        "ladder": "same mode resolved at several polynomial degrees; a spread over "
                  "1 % means not converged"},
       {"material": val["material"], "bc": val["bc"], "basis": val["basis"],
        "thickness_mm": val["thickness_mm"],
        "formula": st["formula"]})
