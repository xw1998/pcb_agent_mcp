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

# Copper foil, handbook values for rolled/electrodeposited sheet: E 110 GPa,
# nu 0.34, rho 8960. Electroplated copper on a board is softer (annealed, 60..90
# GPa) and a plated barrel is not a foil at all - this is the SHEET the outer
# layers are made of, and the stiffening it brings scales with sqrt(E), so a
# softer copper changes the answer by a few percent, not by a factor.
E_CU = 110.0e9
NU_CU = 0.34
RHO_CU = 8960.0

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


def _rank1_whiten(stiff, s_vec, beta):
    """Whiten a symmetric stiffness by a mass matrix I + beta*s*s^T.

    Returns (stiff2, apply) where stiff2 = A K A and apply(y) = A y, with A the
    inverse square root of the mass. Closed form, no iteration: with u = s/|s|
    and c = sqrt(1 + beta |s|^2) - 1, the Cholesky factor of the mass is
    I + c u u^T, so A = I + d u u^T with d = -c/(1+c). |s| = 0 returns the input
    untouched, which is what makes a mass sitting on a node a no-op.
    """
    n = len(stiff)
    sn2 = sum(v * v for v in s_vec)
    if sn2 <= 0.0:
        return stiff, None
    cc = math.sqrt(1.0 + beta * sn2) - 1.0
    if cc == 0.0:
        return stiff, None
    dd = -cc / (1.0 + cc)
    u_vec = [v / math.sqrt(sn2) for v in s_vec]
    ku = [sum(stiff[i][j] * u_vec[j] for j in range(n)) for i in range(n)]
    uku = sum(u_vec[i] * ku[i] for i in range(n))
    out = [[0.0] * n for _ in range(n)]
    for i in range(n):
        row = stiff[i]
        dst = out[i]
        kui = ku[i]
        ui = u_vec[i]
        for j in range(n):
            dst[j] = (row[j] + dd * (kui * u_vec[j] + ui * ku[j])
                      + dd * dd * uku * ui * u_vec[j])

    def apply(y):
        d = 0.0
        for i in range(len(y)):
            d += u_vec[i] * y[i]
        d *= dd
        return [y[i] + d * u_vec[i] for i in range(len(y))]

    return out, apply


# ------------------------------------------- laminate section (0.0.57)
def _q_terms(e, nu):
    """Plane-stress reduced stiffnesses Q11, Q12, Q22, Q66 of one isotropic layer."""
    f = e / (1.0 - nu * nu)
    return (f, nu * f, f, e / (2.0 * (1.0 + nu)))


def _layer_z_integrals(za, zb):
    """[ int 1 dz, int z dz, int z^2 dz ] over [za, zb] - exact, no quadrature."""
    return (zb - za,
            0.5 * (zb * zb - za * za),
            (zb ** 3 - za ** 3) / 3.0)


def _inv3(m):
    """3x3 inverse by the adjugate (exact formula, no pivoting needed for a
    stiffness matrix built out of Q's). None if singular."""
    a, b, c = m[0]
    d, e, f = m[1]
    g, h, i = m[2]
    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    if det == 0.0:
        return None
    return [[(e * i - f * h) / det, (c * h - b * i) / det, (b * f - c * e) / det],
            [(f * g - d * i) / det, (a * i - c * g) / det, (c * d - a * f) / det],
            [(d * h - e * g) / det, (b * g - a * h) / det, (a * e - b * d) / det]]


def _mat3_mul(x, y):
    out = [[0.0] * 3 for _ in range(3)]
    for r in range(3):
        for c in range(3):
            s = 0.0
            for k in range(3):
                s += x[r][k] * y[k][c]
            out[r][c] = s
    return out


def _laminate_section(h_m, e, nu, t_top, t_bot):
    """A / B / D of laminate + copper skins, plus D* of the traction-free plate.

    Layers bottom to top, z measured from the LAMINATE mid-plane:

        copper bottom [-h/2 - t_bot, -h/2], laminate [-h/2, h/2],
        copper top    [ h/2, h/2 + t_top]

    A = int Q dz, B = int Q z dz, D = int Q z^2 dz (3x3, plane stress). For a
    SYMMETRIC stack B is exactly the zero matrix and D is what a transformed
    section gives - that is the 0.0.56 model, unchanged.

    For an UNSYMMETRIC stack B != 0: bending and stretching are coupled. A plate
    free to extend in-plane (no in-plane tractions, which is a board on standoffs)
    answers with the REDUCED bending stiffness

        D* = D - B A^-1 B

    and its mid-plane strain under a curvature k is eps0 = -A^-1 B k. Both are
    exact for the traction-free case. The same asymmetry also makes the flat
    state not an equilibrium (the board bows); the frequencies here are those
    about the FLAT state, i.e. as if the plate were held flat. That assumption is
    reported, never hidden.
    """
    layers = []
    if t_bot > 0.0:
        layers.append((-0.5 * h_m - t_bot, -0.5 * h_m, E_CU, NU_CU))
    layers.append((-0.5 * h_m, 0.5 * h_m, e, nu))
    if t_top > 0.0:
        layers.append((0.5 * h_m, 0.5 * h_m + t_top, E_CU, NU_CU))
    A = [[0.0] * 3 for _ in range(3)]
    B = [[0.0] * 3 for _ in range(3)]
    D = [[0.0] * 3 for _ in range(3)]
    for (za, zb, le, lnu) in layers:
        q11, q12, q22, q66 = _q_terms(le, lnu)
        qm = ((q11, q12, 0.0), (q12, q22, 0.0), (0.0, 0.0, q66))
        i0, i1, i2 = _layer_z_integrals(za, zb)
        for r in range(3):
            for c in range(3):
                A[r][c] += qm[r][c] * i0
                B[r][c] += qm[r][c] * i1
                D[r][c] += qm[r][c] * i2
    ainv = _inv3(A)
    if ainv is None:
        return None
    dstar = _mat3_mul(_mat3_mul(B, ainv), B)
    for r in range(3):
        for c in range(3):
            dstar[r][c] = D[r][c] - dstar[r][c]
    eps0 = [[-v for v in row] for row in _mat3_mul(ainv, B)]
    z0 = B[0][0] / A[0][0] if A[0][0] else 0.0
    asym = abs(B[0][0]) > 0.0 or abs(B[2][2]) > 0.0
    return {"a": A, "b": B, "d": D, "dstar": dstar, "eps0": eps0,
            "z0": z0, "asym": asym,
            "d11": dstar[0][0], "d22": dstar[1][1],
            "d12": dstar[0][1], "d66": dstar[2][2],
            "d11_sym": D[0][0], "d12_sym": D[0][1], "d66_sym": D[2][2],
            "b11": B[0][0], "a11": A[0][0],
            "t_top": t_top, "t_bot": t_bot,
            "z_top_surface": 0.5 * h_m + t_top,
            "z_bot_surface": -0.5 * h_m - t_bot}


def _poly_moments(xa, xb, k_deg):
    """Exact int_{xa}^{xb} xi^p xi^q dxi, p+q <= 2 k_deg."""
    out = [[0.0] * (k_deg + 1) for _ in range(k_deg + 1)]
    for p_i in range(k_deg + 1):
        for q_i in range(k_deg + 1):
            n_exp = p_i + q_i + 1
            out[p_i][q_i] = (xb ** n_exp - xa ** n_exp) / n_exp
    return out


def _region_metric(W, k_deg, xa, xb):
    """int_region s_i s_k dxi for the whitened 1-D basis s = W^T [1, xi, ...].

    Exact: the basis is a polynomial, so the integral is a monomial moment.
    """
    n = len(W[0]) if W else 0
    p_mom = _poly_moments(xa, xb, k_deg)
    out = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for k in range(i, n):
            s = 0.0
            for p_i in range(k_deg + 1):
                wi = W[p_i][i]
                if wi == 0.0:
                    continue
                for q_i in range(k_deg + 1):
                    s += wi * p_mom[p_i][q_i] * W[q_i][k]
            out[i][k] = s
            out[k][i] = s
    return out


def _sym_eig_with_mass(stiff, mass):
    """Generalised symmetric eigenproblem K v = lambda M v with M SPD.

    M = L L^T (Cholesky), u = L^T v, so L^-1 K L^-T u = lambda u; the returned
    eigenvectors are v = L^-T u, mass-orthonormal as before (v^T M v = 1).
    """
    n = len(stiff)
    L = _cholesky(mass)
    if L is None:
        return None, None
    Linv = _lower_inverse(L)
    # A = Linv K Linv^T
    tmp = [[0.0] * n for _ in range(n)]
    for i in range(n):
        li = Linv[i]
        for j in range(n):
            if li[j] == 0.0:
                continue
            lj_ = li[j]
            row = stiff[j]
            dst = tmp[i]
            for k in range(n):
                dst[k] += lj_ * row[k]
    a_mat = [[0.0] * n for _ in range(n)]
    for i in range(n):
        ti = tmp[i]
        for j in range(i, n):
            s = 0.0
            lj = Linv[j]
            for k in range(n):
                if lj[k]:
                    s += ti[k] * lj[k]
            a_mat[i][j] = s
            a_mat[j][i] = s
    evals, evecs = _jacobi(a_mat)
    vecs = []
    for u in evecs:
        v = [0.0] * n
        for i in range(n):
            s = 0.0
            li = Linv[i]
            for j in range(n):
                if li[j]:
                    s += li[j] * u[j]
            v[i] = s
        vecs.append(v)
    return evals, vecs


def plate_modes(a_mm, b_mm, thickness_mm, material=None, e_gpa=None, nu=None,
                rho_kg_m3=None, mount="corners", bc_x=None, bc_y=None,
                n_modes=4, poly_degree=7, probe_points=None, max_dof=MAX_DOF,
                stress_amplitude_mm=None, copper_um=None, added_mass_g=None,
                added_mass_at_mm=None, copper_um_bottom=None,
                added_mass_regions=None, participation=False):
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

    Four additions on top of the bare laminate (all OFF by default, and with
    all off every number is bit-identical to the bare solve):

    copper_um
        copper on BOTH faces, thickness in um per face. It enters twice and the
        two effects point in opposite directions: the skins raise the flexural
        rigidity (a transformed section, D11 / D12 / D22 / D66 summed layer by
        layer with each layer's own E and nu) and they raise the mass per area.
        Because the stack is symmetric the neutral axis stays at mid-plane, so a
        single set of D's is enough.

    copper_um_bottom
        copper on the BOTTOM face alone, in um, when it differs from copper_um.
        Leave it out and copper_um means "the same on both faces" (the 0.0.56
        behaviour, arithmetic unchanged). Give it and the stack is unsymmetric:
        A, B and D are formed layer by layer and B is no longer zero, so bending
        and stretching are coupled. An in-plane-free plate answers with the
        reduced stiffness D* = D - B A^-1 B and mid-plane strain
        eps0 = -A^-1 B kappa; both are used, and the envelope reports the
        coupling term, the neutral-plane shift and how much D* fell below D.
        The unsymmetric plate also BOWS out of flat, and these frequencies are
        the ones about the flat state (as if held flat) - stated, not hidden.

    added_mass_g / added_mass_at_mm
        a point mass (a connector, a can, a battery) at a point, default the
        plate centre. It is a rank-one addition to the mass matrix,
        M = I + beta s s^T with s the mode basis sampled at that point, so the
        solve stays a plain symmetric eigenproblem: the whitening factor of
        I + beta |s|^2 u u^T is (1 + c) with c = sqrt(1 + beta |s|^2) - 1, in
        closed form. No iteration, no single-mode truncation, and a mass placed
        at a node of a mode leaves that mode exactly alone.

    added_mass_regions
        distributed mass: a list of {mass_g, center_mm, size_mm} patches, each a
        uniform areal mass over a rectangle (omit size_mm = the whole plate, e.g.
        potting or a conformal coat). The mass matrix gains
        sum_m beta_m R_m with R_m the exact polynomial integral of s s^T over
        that patch - no quadrature, no lumping. The mass matrix is then no longer
        the identity, so the solve becomes a generalised one: Cholesky of M, then
        the same symmetric Jacobi, and the eigenvectors are mapped back so the
        mode shapes (and therefore the stress) stay physical. Unlike the point
        mass this one CANNOT leave a mode alone - a patch overlaps every mode.

    Still not modelled, and still said so in the envelope: component stiffness,
    mounting compliance, shear deformation and rotary inertia (thin-plate
    limit), the warped equilibrium of an unsymmetric stack, and the error of
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
    # ---- modal participation (0.0.83) ------------------------------------
    # For uniform base acceleration the modal force is a_base * int rho w dA, so
    # the factor a mode carries is Gamma = int w dA / int w^2 dA. The Ritz basis
    # is separable, X_i(xi) Y_j(eta), so both integrals are monomial moments and
    # the two numbers below are exact - no quadrature, no lumping, no fitting.
    part = bool(participation)
    ix1 = [sum(Wx[k][i] / (k + 1.0) for k in range(K + 1)) for i in range(nx)]
    iy1 = [sum(Wy[k][j] / (k + 1.0) for k in range(K + 1)) for j in range(ny)]
    ix2 = [[sum(Wx[k][i] * Wx[k2][i2] / (k + k2 + 1.0)
                for k in range(K + 1) for k2 in range(K + 1))
            for i2 in range(nx)] for i in range(nx)]
    iy2 = [[sum(Wy[k][j] * Wy[k2][j2] / (k + k2 + 1.0)
                for k in range(K + 1) for k2 in range(K + 1))
            for j2 in range(ny)] for j in range(ny)]
    part_rows = [] if part else None

    def _moments_of(vec_c):
        """(int w dA, int w^2 dA) on the unit square, w = sum vec_c X_i Y_j."""
        s1 = 0.0
        for i in range(nx):
            fx = ix1[i]
            if fx == 0.0:
                continue
            base = i * ny
            for j in range(ny):
                c = vec_c[base + j]
                if c != 0.0:
                    s1 += c * fx * iy1[j]
        s2 = 0.0
        for i in range(nx):
            base = i * ny
            for j in range(ny):
                c = vec_c[base + j]
                if c == 0.0:
                    continue
                row = 0.0
                for i2 in range(nx):
                    f = ix2[i][i2]
                    if f == 0.0:
                        continue
                    base2 = i2 * ny
                    t = 0.0
                    for j2 in range(ny):
                        c2 = vec_c[base2 + j2]
                        if c2 != 0.0:
                            t += c2 * iy2[j][j2]
                    row += f * t
                s2 += c * row
        return s1, s2

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

    # ---- copper skins: a transformed section, not a fudge factor -----------
    # D11 = sum_i E_i/(1-nu_i^2) * int z^2 dz over layer i, and D12 / D66 split
    # the same way. Symmetric skins keep the neutral axis at mid-plane, so the
    # laminate's own nu can no longer be used for the coupling term - each layer
    # brings its own nu and the D12 sum is what carries it.
    cu_t = 0.0
    if copper_um is not None:
        cu = _num(copper_um)
        if cu is None or cu < 0.0:
            return err("copper_um must be a number >= 0 (thickness per face)",
                       "ARG_RANGE")
        cu_t = cu * 1e-6
    cu_bot_t = 0.0
    sect = None
    if copper_um_bottom is not None:
        cb = _num(copper_um_bottom)
        if cb is None or cb < 0.0:
            return err("copper_um_bottom must be a number >= 0 (thickness on the "
                       "bottom face, in um)", "ARG_RANGE",
                       hint="give copper_um_bottom only when the two faces differ; "
                            "copper_um alone already means the same on both faces")
        cu_bot_t = cb * 1e-6
        if cu_bot_t == cu_t:
            cu_bot_t = 0.0          # identical faces: the symmetric path, unchanged
        else:
            if (cu_t >= (hm / 2.0) * 0.5) or (cu_bot_t >= (hm / 2.0) * 0.5):
                return err("copper is %.3f um top / %.3f um bottom on a %.3f mm "
                           "laminate - that is a copper plate with a laminate "
                           "core, not a PCB" % (cu_t * 1e6, cu_bot_t * 1e6, h_mm),
                           "OUT_OF_VALIDITY",
                           hint="the transformed section is still arithmetic, but "
                                "the thin-plate limit this solve rests on is gone")
            sect = _laminate_section(hm, E, nu_v, cu_t, cu_bot_t)
            if sect is None:
                return err("the laminate section is singular (A is not invertible)",
                           "OUT_OF_VALIDITY")
    cu_any = sect is not None or cu_t > 0.0
    if cu_t > 0.0:
        hz = hm / 2.0
        iz_cu = ((hz + cu_t) ** 3 - hz ** 3) / 3.0   # int z^2 dz, one skin
        d11 = (E * hm ** 3 / (12.0 * (1.0 - nu_v * nu_v))
               + 2.0 * E_CU / (1.0 - NU_CU * NU_CU) * iz_cu)
        d22 = d11
        d12 = (nu_v * E * hm ** 3 / (12.0 * (1.0 - nu_v * nu_v))
               + 2.0 * NU_CU * E_CU / (1.0 - NU_CU * NU_CU) * iz_cu)
        d66 = (E * hm ** 3 / (24.0 * (1.0 + nu_v))
               + 2.0 * E_CU / (2.0 * (1.0 + NU_CU)) * iz_cu)
        if cu_t >= hz * 0.5:
            return err("copper_um is %.3f mm per face on a %.3f mm laminate - "
                       "that is a copper plate with a laminate core, not a PCB"
                       % (cu * 1e-3, h_mm), "OUT_OF_VALIDITY",
                       hint="the transformed section is still arithmetic, but the "
                            "thin-plate limit this solve rests on is gone")
        mpa = rho * hm + 2.0 * RHO_CU * cu_t
        Dflex = d11
    if sect is not None:
        # unsymmetric: D* = D - B A^-1 B is the stiffness an in-plane-free plate
        # shows; D itself would be stiffer than the board can be, because the
        # membrane forces that would make it stiffer are exactly the ones a free
        # plate cannot develop.
        d11 = sect["d11"]
        d22 = sect["d22"]
        d12 = sect["d12"]
        d66 = sect["d66"]
        mpa = rho * hm + RHO_CU * (cu_t + cu_bot_t)
        Dflex = d11

    ndof = nx * ny
    nfull = ndof            # the (i, j) grid never shrinks; `ndof` can
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
                    # U = D/2 * int[...] is a quadratic form, so d2U gives the
                    # stiffness as D * int[...] - the 1/2 cancels. Writing 0.5
                    # here halves every eigenvalue and drops every frequency by
                    # exactly sqrt(2), which looks plausible and is wrong.
                    if cu_any:
                        txx = I2xw[i][k] * M0yw[j][l] / a4
                        tyy = M0xw[i][k] * I2yw[j][l] / b4
                        tmx = (ICxw[i][k] * I1yw[j][l]
                               + I1xw[i][k] * ICyw[j][l]) / a2b2
                        ttw = (D1xw[i][k] * D1yw[j][l]
                               + D1xw[i][k] * D1yw[j][l]) / a2b2
                        stiff[I][J] += (d11 * txx + d22 * tyy + d12 * tmx
                                        + 2.0 * d66 * ttw)
                    else:
                        val = (
                            I2xw[i][k] * M0yw[j][l] / a4
                            + M0xw[i][k] * I2yw[j][l] / b4
                            + nu_v * (ICxw[i][k] * I1yw[j][l]
                                      + I1xw[i][k] * ICyw[j][l]) / a2b2
                            + (1.0 - nu_v) * (D1xw[i][k] * D1yw[j][l]
                                              + D1xw[i][k] * D1yw[j][l]) / a2b2
                        )
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

    # ---- a point mass is a rank-one addition to the mass matrix -----------
    # M = I + beta s s^T, with s the whitened basis sampled at the mass and
    # beta = m / (mass_per_area * a * b). Its square root is closed form: for
    # M = I + beta |s|^2 u u^T (u = s/|s|) the Cholesky factor is I + c u u^T
    # with (1+c)^2 = 1 + beta |s|^2, so the whitened stiffness is A K A with
    # A = L^-1 = I + d u u^T, d = -c/(1+c). Both c and d vanish when the mass
    # sits on a node of the basis (|s| = 0), which is the negative control.
    apply_mass = None
    mass_report = None
    mass_mat = None
    point_beta = None
    point_svec = None
    if added_mass_g is not None:
        mg = _num(added_mass_g)
        if mg is None or mg <= 0.0:
            return err("added_mass_g must be a number > 0", "ARG_RANGE",
                       hint="a mass of zero is not a mass; leave the argument out "
                            "to say 'no component mass'")
        m_kg = mg * 1e-3
        if added_mass_at_mm is not None:
            try:
                px = float(added_mass_at_mm[0])
                py = float(added_mass_at_mm[1])
            except (TypeError, ValueError, IndexError):
                return err("added_mass_at_mm must be [x_mm, y_mm]", "ARG_INVALID")
            if not (0.0 <= px <= a and 0.0 <= py <= b):
                return err("added_mass_at_mm (%.3f, %.3f) is outside the plate "
                           "(%.3f x %.3f mm)" % (px, py, a, b), "ARG_RANGE",
                           hint="a mass off the plate is not carried by the plate")
            x0, y0 = px / a, py / b
        else:
            x0 = y0 = 0.5
        s_full = shape(x0, y0)
        if base_cols is not None:
            s_vec = [sum(base_cols[p][i] * s_full[i] for i in range(len(s_full)))
                     for p in range(len(base_cols))]
        else:
            s_vec = s_full
        snorm2 = sum(v * v for v in s_vec)
        beta = m_kg / (mpa * am * bm)
        cc = math.sqrt(1.0 + beta * snorm2) - 1.0
        point_beta = beta
        point_svec = s_vec
        if added_mass_regions is None:
            stiff, apply_mass = _rank1_whiten(stiff, s_vec, beta)
        mass_report = {
            "added_mass_g": _sig(mg),
            "at_mm": [_sig(x0 * a), _sig(y0 * b)],
            "plate_mass_g": _sig(mpa * am * bm * 1e3),
            "mass_ratio": _sig(m_kg / (mpa * am * bm)),
            "mode_basis_at_point": _sig(snorm2, 6),
            "whitening_c": _sig(cc, 6),
            "note": ("s^2 is the whitened basis norm at the mass point; a point "
                     "mass on a node (s^2 = 0) leaves every mode untouched, and "
                     "the plate's own modal mass is its full mass because the "
                     "basis is mass-orthonormal"),
        }

    # ---- distributed mass: exact patch integrals, then a generalised solve --
    # A patch is not a point: its mass matrix is int_patch s s^T dA, which for a
    # polynomial basis is a monomial moment - exact, no quadrature, no lumping.
    # With a non-identity mass matrix a plain Jacobi no longer applies, so M is
    # Cholesky-factored and the same symmetric solver runs on L^-1 K L^-T.
    regions_report = None
    if added_mass_regions is not None:
        try:
            raw_regs = list(added_mass_regions)
        except TypeError:
            return err("added_mass_regions must be a list of {mass_g, center_mm, "
                       "size_mm}", "ARG_INVALID")
        if not raw_regs:
            return err("added_mass_regions is empty", "ARG_INVALID",
                       hint="leave it out to say 'no distributed mass'")
        parsed = []
        for ii, reg in enumerate(raw_regs):
            if not isinstance(reg, dict):
                return err("added_mass_regions[%d] is not an object" % ii,
                           "ARG_INVALID")
            mg_r = _num(reg.get("mass_g"))
            if mg_r is None or mg_r <= 0.0:
                return err("added_mass_regions[%d].mass_g must be a number > 0" % ii,
                           "ARG_RANGE")
            ctr = reg.get("center_mm")
            if ctr is None:
                cx, cy = a / 2.0, b / 2.0
            else:
                try:
                    cx, cy = float(ctr[0]), float(ctr[1])
                except (TypeError, ValueError, IndexError):
                    return err("added_mass_regions[%d].center_mm must be "
                               "[x_mm, y_mm]" % ii, "ARG_INVALID")
                if not (0.0 <= cx <= a and 0.0 <= cy <= b):
                    return err("added_mass_regions[%d].center_mm (%.3f, %.3f) is "
                               "outside the plate (%.3f x %.3f mm)"
                               % (ii, cx, cy, a, b), "ARG_RANGE")
            size = reg.get("size_mm")
            if size is None:
                xa, xb_, ya, yb_ = 0.0, a, 0.0, b
            else:
                try:
                    sw, sh = float(size[0]), float(size[1])
                except (TypeError, ValueError, IndexError):
                    return err("added_mass_regions[%d].size_mm must be [w_mm, "
                               "h_mm]" % ii, "ARG_INVALID")
                if sw <= 0.0 or sh <= 0.0:
                    return err("added_mass_regions[%d].size_mm must be > 0" % ii,
                               "ARG_RANGE")
                xa, xb_, ya, yb_ = cx - sw / 2.0, cx + sw / 2.0, cy - sh / 2.0, cy + sh / 2.0
                if xa < -1e-9 or ya < -1e-9 or xb_ > a + 1e-9 or yb_ > b + 1e-9:
                    return err("added_mass_regions[%d] spills off the plate: "
                               "[%.3f, %.3f] x [%.3f, %.3f] against a %.3f x %.3f "
                               "mm plate" % (ii, xa, xb_, ya, yb_, a, b),
                               "ARG_RANGE",
                               hint="a patch half off the plate would carry mass "
                                    "the plate does not have; shrink it or move it")
            parsed.append({"mass_g": mg_r, "center_mm": [cx, cy],
                           "size_mm": [xb_ - xa, yb_ - ya],
                           "xa": xa, "xb": xb_, "ya": ya, "yb": yb_})
        nred = len(stiff)
        mred = [[1.0 if i == j else 0.0 for j in range(nred)] for i in range(nred)]
        per_region = []
        qcols = base_cols                          # None => the full grid basis
        for reg in parsed:
            m_kg = reg["mass_g"] * 1e-3
            beta_r = m_kg / (mpa * am * bm)
            xi_a, xi_b = reg["xa"] / a, reg["xb"] / a
            et_a, et_b = reg["ya"] / b, reg["yb"] / b
            Xm = _region_metric(Wx, K, xi_a, xi_b)
            Ym = _region_metric(Wy, K, et_a, et_b)
            # The patch carries `mass_g` no matter how small it is, so what enters
            # the mass matrix is the MEAN of s s^T over the patch, not its
            # integral: a patch's areal density is m/area, and
            #   int_patch (m/area) s s^T dA = m * mean_patch(s s^T).
            # Skipping this division makes a shrinking patch weigh nothing - a
            # 20 g part on a 0.5 mm square then reads as 0 g - which is the one
            # way a distributed mass can quietly become a no-op. For the whole
            # plate the divisor is exactly 1, so that case is untouched.
            den = (xi_b - xi_a) * (et_b - et_a)
            if den <= 0.0:
                return err("a mass patch has zero area", "ARG_RANGE")
            # Kernel in the FULL (i, j) grid, which is the space the patch
            # integral lives in - it is not the reduced DOF count.
            # R[(i,j)][(k,l)] = int_patch s_i(x) s_k(x) dx * int_patch s_j(y) s_l(y) dy
            rq = [[0.0] * nfull for _ in range(nfull)]
            for i in range(nx):
                base_i = i * ny
                Xi = Xm[i]
                for k in range(nx):
                    xik = Xi[k]
                    if xik == 0.0:
                        continue
                    base_k = k * ny
                    row = rq[base_i]
                    dst = row
                    for j in range(ny):
                        Yj = Ym[j]
                        off_i = base_i + j
                        off_k = base_k
                        r = rq[off_i]
                        for l in range(ny):
                            yjl = Yj[l]
                            if yjl != 0.0:
                                r[off_k + l] += xik * yjl
            if qcols is None:
                for p in range(nred):
                    row = mred[p]
                    rp = rq[p]
                    for rr in range(nred):
                        row[rr] += beta_r * rp[rr] / den
            else:
                for p in range(nred):
                    qp = qcols[p]
                    row = mred[p]
                    for rr in range(nred):
                        qr = qcols[rr]
                        val = 0.0
                        for I2 in range(nfull):
                            c = qp[I2]
                            if c == 0.0:
                                continue
                            rI = rq[I2]
                            sv = 0.0
                            for J2 in range(nfull):
                                if qr[J2] != 0.0:
                                    sv += rI[J2] * qr[J2]
                            if sv != 0.0:
                                val += c * sv
                        row[rr] += beta_r * val / den
            per_region.append({"mass_g": _sig(reg["mass_g"]),
                               "center_mm": [_sig(reg["center_mm"][0]),
                                             _sig(reg["center_mm"][1])],
                               "size_mm": [_sig(reg["size_mm"][0]),
                                           _sig(reg["size_mm"][1])],
                               "mass_ratio": _sig(m_kg / (mpa * am * bm))})
        if point_beta is not None:
            for p in range(nred):
                for rr in range(nred):
                    mred[p][rr] += point_beta * point_svec[p] * point_svec[rr]
        mass_mat = mred
        total_added = sum(r["mass_g"] for r in parsed) + (
            0.0 if added_mass_g is None else float(added_mass_g))
        regions_report = {
            "regions": per_region,
            "added_mass_total_g": _sig(total_added),
            "plate_mass_g": _sig(mpa * am * bm * 1e3),
            "added_over_plate": _sig(total_added * 1e-3 / (mpa * am * bm)),
            "mass_matrix": "M = I + sum_m beta_m R_m + (point term), R_m = exact "
                           "integral of s s^T over that patch",
            "note": ("a patch overlaps every mode, so unlike a point mass on a node "
                     "it cannot leave one alone; omitting size_mm means the whole "
                     "plate (potting, conformal coat, a big can)"),
        }

    if mass_mat is None:
        evals, evecs = _jacobi(stiff)
    else:
        evals, evecs = _sym_eig_with_mass(stiff, mass_mat)
        if evals is None:
            return err("the mass matrix is not positive definite", "INTERNAL",
                       hint="a negative patch mass should have been rejected "
                            "earlier; this is a bug, not an input problem")

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
        col = evecs[mi] if apply_mass is None else apply_mass(evecs[mi])
        vec = full_vec(col)
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
            if part:
                sh1, sh2 = _moments_of([c / peak for c in vec])
                if sh2 > 0.0:
                    gam = sh1 / sh2
                    entry["participation"] = _sig(gam, 6)
                    entry["modal_mass_g"] = _sig(mpa * am * bm * sh2 * 1e3, 6)
                    entry["effective_mass_g"] = _sig(
                        mpa * am * bm * sh2 * gam * gam * 1e3, 6)
                    part_rows.append((mi + 1, freqs[mi], gam, sh2))
        if amp_mm is not None and not is_rigid and "stress" not in entry:
            # 21 x 21 is enough for a peak: the curvature field is smooth and the
            # second derivative of a degree-K polynomial is degree K-2.
            ng = 21
            scale = (amp_mm * 1e-3) / peak      # metres per unit coefficient
            # With copper the OUTERMOST material is copper, and the bending stress
            # at a given curvature is E/(1-nu^2) * z * kappa - so the surface
            # stress has to use the surface layer's E and nu at that layer's z,
            # not the laminate's at h/2. Using copper's E next to the laminate's
            # nu (what 0.0.56 did) mixes two materials inside one formula, and on
            # a 1 oz board that is a 10 % error on the copper stress; 0.0.57 uses
            # each layer's own (E, nu) pair and, when the two faces differ, solves
            # BOTH surfaces and reports the worse one. With no copper the old
            # arithmetic is kept verbatim, so that answer is bit-identical.
            surfaces = []
            eps0 = sect["eps0"] if sect is not None else None
            if not cu_any:
                z_s = hm / 2.0
                e_s, nu_s = E, nu_v
                surfaces.append({"name": "laminate", "z": z_s, "e": e_s,
                                 "nu": nu_s, "legacy": True,
                                 "ez": e_s * z_s / (1.0 - nu_s * nu_s),
                                 "g1": e_s / (2.0 * (1.0 + nu_s))})
            else:
                def _face(name, z, t_face):
                    e_f = E_CU if t_face > 0.0 else E
                    nu_f = NU_CU if t_face > 0.0 else nu_v
                    return {"name": name, "z": z, "e": e_f, "nu": nu_f,
                            "legacy": False, "q": _q_terms(e_f, nu_f),
                            "material": "copper" if t_face > 0.0 else "laminate"}
                z_top = sect["z_top_surface"] if sect is not None else hm / 2.0 + cu_t
                z_bot = sect["z_bot_surface"] if sect is not None else -(hm / 2.0 + cu_t)
                # copper_um ALONE means both faces, so in the symmetric case the
                # bottom face is copper too even though cu_bot_t was normalised
                # to 0 to select the symmetric stiffness path. Reading the face
                # off cu_bot_t there would put the LAMINATE's nu on a copper
                # surface and read a symmetric board as if its two faces
                # differed by a factor of nu_cu/nu_lam.
                cu_bot_face = cu_t if sect is None else cu_bot_t
                surfaces.append(_face("top", z_top, cu_t))
                surfaces.append(_face("bottom", z_bot, cu_bot_face))

            def stress_at(sf, kx, ky, kxy):
                """(sigma_x, sigma_y, tau_xy) at that face, for these curvatures."""
                if sf["legacy"]:
                    # verbatim 0.0.56 arithmetic - same expression, same order
                    return (-sf["ez"] * (kx + sf["nu"] * ky),
                            -sf["ez"] * (ky + sf["nu"] * kx),
                            -2.0 * sf["g1"] * sf["z"] * kxy)
                q11, q12, q22, q66 = sf["q"]
                z = sf["z"]
                # kappa_xy in CLT is the ENGINEERING twist, twice w_xy: the
                # shear strain is gamma_xy = eps0_xy + z * 2 w_xy. Using w_xy
                # itself halves tau_xy AND contaminates the principal stresses,
                # because sigma_max is picked through sqrt(((sx-sy)/2)^2 + tau^2).
                kxy2 = 2.0 * kxy
                ex = z * kx
                ey = z * ky
                exy = z * kxy2
                if eps0 is not None:
                    # CLT: eps0 = -A^-1 B kappa, so the surface strain is
                    # eps0 + z kappa - an unsymmetric stack stretches as it bends
                    ex += eps0[0][0] * kx + eps0[0][1] * ky + eps0[0][2] * kxy2
                    ey += eps0[1][0] * kx + eps0[1][1] * ky + eps0[1][2] * kxy2
                    exy += eps0[2][0] * kx + eps0[2][1] * ky + eps0[2][2] * kxy2
                return (-(q11 * ex + q12 * ey),
                        -(q12 * ex + q22 * ey),
                        -(q66 * exy))

            # Two different questions, two different peaks. von Mises is largest
            # where the TWISTING curvature w_xy is largest (a corner, for a
            # sinusoid-like mode); the largest principal stress is where the
            # bending curvatures w_xx/w_yy are largest (the middle of a bay).
            # Reporting one point for both is how a "max stress" ends up 36 %
            # below the real one.
            best_vm = None
            best_s = None
            best_t = None
            face_vm = {}
            for gi in range(ng):
                xi = gi / float(ng - 1)
                for gj in range(ng):
                    eta = gj / float(ng - 1)
                    kx, ky, kxy = d2vec(vec, xi, eta)
                    kx *= scale
                    ky *= scale
                    kxy *= scale
                    for sf in surfaces:
                        sx, sy, txy = stress_at(sf, kx, ky, kxy)
                        mid = 0.5 * (sx + sy)
                        rr = math.sqrt(0.25 * (sx - sy) ** 2 + txy * txy)
                        hi = mid + rr
                        lo = mid - rr
                        vm = math.sqrt(hi * hi - hi * lo + lo * lo)
                        smax = max(abs(hi), abs(lo))
                        prev = face_vm.get(sf["name"])
                        if prev is None or vm > prev:
                            face_vm[sf["name"]] = vm
                        rec = {"vm": vm, "smax": smax, "x": xi * a, "y": eta * b,
                               "sx": sx, "sy": sy, "t": txy, "hi": hi, "lo": lo,
                               "kx": kx, "ky": ky, "kxy": kxy,
                               "surface": sf["name"], "z": sf["z"], "e": sf["e"],
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
            e_s = sp["e"]
            z_s = sp["z"]
            sp = best_s
            entry["stress"] = {
                "amplitude_mm": _sig(amp_mm),
                "surface_z_mm": _sig(z_s * 1e3),
                "surface_face": sp["surface"],
                "surface_material": ("copper" if (cu_t > 0.0 or cu_bot_t > 0.0)
                                     else "laminate"),
                "surfaces_checked": len(surfaces),
                "von_mises_by_surface_mpa": {k: _sig(v / 1e6)
                                             for k, v in face_vm.items()},
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
                "surface_strain_ue": _sig(1e6 * abs(sp["hi"]) / e_s),
                "peak_locations_note":
                    ("the reported extremes sit in symmetry-equivalent sets (the "
                     "four corners share one value, so does each bay centre); one "
                     "point of each set is listed")
                    if (best_vm["corner"] and sp["corner"]) else
                    ("von Mises peaks where w_xy does, sigma_max where w_xx/w_yy do: "
                     "they are different points and both are reported"),
                "formula": "sigma = Q(z) (eps0 + z kappa) with Q built from the "
                           "E and nu of the layer AT that surface (copper when "
                           "the face is copper, else the laminate), z measured "
                           "from the laminate mid-plane, and eps0 = -A^-1 B kappa "
                           "the mid-plane strain an unsymmetric stack picks up as "
                           "it bends (exactly zero for a symmetric stack, which "
                           "is why a symmetric board needs no eps0 term)",
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
    if sect is not None:
        warnings.append(
            "the stack is UNSYMMETRIC (%.0f um top / %.0f um bottom): B = %.4g N is "
            "not zero, so D* = D - B A^-1 B is %.2f %% below D and the mid-plane "
            "stretches as it bends. Those frequencies are the ones about the FLAT "
            "state - a board free to warp sits in a bowed equilibrium instead, and "
            "that is a different (lower) stiffness this solve does not do"
            % (cu_t * 1e6, cu_bot_t * 1e6, sect["b11"],
               100.0 * (1.0 - sect["d11"] / sect["d11_sym"])))
    elif cu_t > 0.0:
        warnings.append(
            "copper_um is a SYMMETRIC skin pair (%g um per face); real 1/2 oz "
            "outer layers with inner planes break that symmetry - pass "
            "copper_um_bottom when the two faces differ" % (cu_t * 1e6))
    else:
        warnings.append(
            "copper stiffening is not modelled: pass copper_um and the transformed "
            "section raises D11, which is what makes f1 read high on a 2 oz board")
    if added_mass_g is None and added_mass_regions is None:
        warnings.append(
            "component mass is not modelled: pass added_mass_g (and its point) for "
            "one heavy part, or added_mass_regions for a distributed one, or f1 "
            "reads high")
    if not cu_any or (added_mass_g is None and added_mass_regions is None):
        warnings.append(
            "mounting compliance and shear deformation are not modelled either")

    part_report = None
    if part:
        plate_mass_g = mpa * am * bm * 1e3
        modal_sum = sum(r[3] for r in part_rows)
        eff_sum = sum(r[3] * r[2] * r[2] for r in part_rows)
        part_report = {
            "modes_included": len(part_rows),
            "modes": [{"index": r[0], "freq_hz": _sig(r[1]),
                       "participation": _sig(r[2], 6),
                       "modal_mass_g": _sig(mpa * am * bm * r[3] * 1e3, 6),
                       "effective_mass_g": _sig(mpa * am * bm * r[3] * r[2] * r[2]
                                                * 1e3, 6)}
                      for r in part_rows],
            "plate_mass_g": _sig(plate_mass_g, 6),
            "modal_mass_sum_g": _sig(mpa * am * bm * modal_sum * 1e3, 6),
            "effective_mass_sum_g": _sig(mpa * am * bm * eff_sum * 1e3, 6),
            "mass_capture_fraction": _sig(eff_sum, 6),
            "participation_note":
                ("Gamma = int w dA / int w^2 dA with w the shape normalised to peak "
                 "1; both integrals are exact monomial moments of the Ritz basis. A "
                 "mode with no net deflection has Gamma = 0 and uniform base "
                 "acceleration does not excite it at all - on an SSSS square the "
                 "(1,2) and (2,1) modes are exactly that. effective_mass = "
                 "Gamma^2 * modal_mass, and its sum over ALL modes is the plate "
                 "mass, so over a truncated basis mass_capture_fraction is below 1 "
                 "and rises with poly_degree: that is the check that it is a "
                 "participation and not a fitted weight"),
        }
        if added_mass_g is not None or added_mass_regions is not None:
            warnings.append(
                "the participation factors are integrals of the BARE plate mass "
                "(rho*h): an added part is not in int rho w dA, so the modal masses "
                "here are the plate alone and Gamma moves once the part is included")

    return ok(
        {
            "a_mm": a, "b_mm": b, "thickness_mm": h_mm,
            "material": {
                "e_gpa": _sig(E / 1e9), "nu": nu_v, "rho_kg_m3": rho,
                "preset": material,
            },
            "flexural_rigidity_n_m": _sig(Dflex),
            "mass_per_area_kg_m2": _sig(mpa),
            "composite": (None if not cu_any else {
                "copper_um_per_face": (None if sect is not None
                                       else _sig(cu_t * 1e6)),
                "copper_um_top": _sig(cu_t * 1e6),
                "copper_um_bottom": _sig((cu_bot_t if sect is not None else cu_t)
                                         * 1e6),
                "d11_n_m": _sig(d11), "d12_n_m": _sig(d12), "d66_n_m": _sig(d66),
                "laminate_d11_n_m": _sig(
                    E * hm ** 3 / (12.0 * (1.0 - nu_v * nu_v))),
                "mass_share_copper": _sig(
                    RHO_CU * (cu_t + (cu_t if sect is None else cu_bot_t)) / mpa, 4),
                "e_cu_gpa": _sig(E_CU / 1e9), "nu_cu": NU_CU,
                "symmetric": sect is None,
                "asymmetric": (None if sect is None else {
                    "a11_n_per_m": _sig(sect["a11"]),
                    "b11_n": _sig(sect["b11"]),
                    "b_over_a_mm": _sig(sect["b11"] / sect["a11"] * 1e3),
                    "d11_before_reduction_n_m": _sig(sect["d11_sym"]),
                    "d11_reduced_n_m": _sig(sect["d11"]),
                    "stiffness_lost_pct": _sig(
                        100.0 * (1.0 - sect["d11"] / sect["d11_sym"]), 3),
                    "neutral_plane_mm": _sig(sect["z0"] * 1e3),
                    "z_top_surface_mm": _sig(sect["z_top_surface"] * 1e3),
                    "z_bottom_surface_mm": _sig(sect["z_bot_surface"] * 1e3),
                    "note": ("B != 0 couples bending and stretching. For a plate "
                             "free to extend in-plane (no in-plane traction) the "
                             "stiffness it actually shows is the REDUCED "
                             "D* = D - B A^-1 B; D alone would be stiffer than "
                             "the board can be. The same coupling bows the flat "
                             "state, so these are the frequencies about the FLAT "
                             "state: a board free to warp is a different problem "
                             "and this solve does not do it"),
                }),
                "formula": "A = int Q dz, B = int Q z dz, D = int Q z^2 dz summed "
                           "over the layers (laminate + skins), then "
                           "D* = D - B A^-1 B for the in-plane-free plate; "
                           "Q = plane-stress stiffness of each isotropic layer",
            }),
            "added_mass": mass_report,
            "added_mass_distributed": regions_report,
            "bc": {"x": list(pairs[0]), "y": list(pairs[1]),
                   "mount": key, "point_supports": 4 if point_fix else 0},
            "basis": {"poly_degree": K, "n_x": nx, "n_y": ny, "dof": ndof,
                      "auto_reduced": K != int(poly_degree)},
            "rigid_body_modes": rid,
            "modes": modes,
            "participation": part_report,
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


def _modal_response(psd, modes, zeta=None, q_factor=None, gamma=3.0):
    """Random response summed over the modes you supply (0.0.83).

    Steady state, uniform base acceleration a, classical (viscous, proportional)
    damping. With w_i(p) the mode-shape value at the point being watched and
    Gamma_i the participation factor (int w dA / int w^2 dA), the displacement of
    that point relative to the base is

        z_p(f) = -a(f) * sum_i w_i(p) Gamma_i / (w_i^2 - w^2 + 2 j zeta_i w w_i)

    and the absolute acceleration of that point is a - w^2 z_p, again exact at
    every frequency rather than "the peak value in the middle". Both are
    integrated against the PSD, so the cross terms between modes are in the
    number - not dropped and not approximated by a sum of squares.

    Miles' closed form is the special case of ONE mode with w(p) Gamma = 1 on a
    flat PSD, which is what the selftest compares against.
    """
    if not isinstance(modes, list) or len(modes) == 0:
        return err("modes must be a non-empty list", "ARG_REQUIRED",
                   hint='each entry is {"freq_hz": ..., "participation": ..., '
                        '"shape_at_point": ...}; plate_modes(participation=true) '
                        'gives the first two per mode and probe_points the third')
    if len(modes) > 200:
        return err("modes carries %d entries; 200 is the ceiling" % len(modes),
                   "TOO_LARGE")
    g = _num(gamma)
    if g is None or g <= 0.0:
        return err("gamma (peak factor) must be > 0", "ARG_INVALID")
    z0, e0 = _sdof_zeta(q_factor, zeta)
    terms = []
    for i, m in enumerate(modes):
        if not isinstance(m, dict):
            return err("modes[%d] must be an object" % i, "ARG_INVALID")
        fr = _pos(m.get("freq_hz"))
        if fr is None:
            return err("modes[%d].freq_hz must be > 0" % i, "ARG_INVALID",
                       hint="take it from the 'freq_hz' of each mode")
        gam = _num(m.get("participation"))
        if gam is None:
            return err("modes[%d].participation must be a number" % i, "ARG_INVALID",
                       hint="plate_modes(participation=true) computes it as "
                            "int w dA / int w^2 dA; a mode with no net deflection "
                            "has 0 and is not excited by base motion")
        shp = m.get("shape_at_point")
        if shp is None:
            shp = m.get("shape")
        shp = _num(shp)
        if shp is None:
            return err("modes[%d].shape_at_point must be a number" % i, "ARG_INVALID",
                       hint="the mode shape at the point you are watching, peak "
                            "normalised to 1 (plate_modes probe_points gives it as "
                            "normalised_deflection)")
        if m.get("zeta") is not None or m.get("q_factor") is not None:
            zi, ei = _sdof_zeta(m.get("q_factor"), m.get("zeta"))
            if ei is not None:
                return ei
        else:
            if e0 is not None:
                return e0
            zi = z0
        terms.append((fr, zi, shp * gam))

    if not isinstance(psd, dict):
        return err("psd must be an object", "ARG_REQUIRED",
                   hint='either {"g2_per_hz": 0.1} or '
                        '{"points": [[10, 0.01], [100, 0.1], [2000, 0.01]]}')
    warnings = []
    pts = None
    wflat = None
    if "g2_per_hz" in psd:
        wflat = _num(psd.get("g2_per_hz"))
        if wflat is None or wflat <= 0.0:
            return err("psd.g2_per_hz must be > 0", "ARG_INVALID")
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
    else:
        return err("psd has neither g2_per_hz nor points", "ARG_REQUIRED")

    f_hi_mode = max(t[0] for t in terms)
    if pts is None:
        f_lo, f_hi = 0.0, 3.0 * f_hi_mode

        def wfun(f):
            return wflat
    else:
        f_lo = pts[0][0]
        f_hi = max(pts[-1][0], 3.0 * f_hi_mode)
        if pts[-1][0] < 3.0 * f_hi_mode:
            warnings.append("PSD ends at %.0f Hz, below 3*the highest mode = %.0f "
                            "Hz; the integral is clamped to the last value there"
                            % (pts[-1][0], 3.0 * f_hi_mode))

        def wfun(f):
            return _psd_at(pts, f)

    # ---- grid: a coarse pass over the whole band plus a fine pass over every
    # modal peak. A uniform grid cannot resolve a Q=100 peak without hundreds of
    # thousands of points, and the cross terms need the peaks resolved too.
    grid = []
    n_coarse = 600
    for i in range(n_coarse + 1):
        grid.append(f_lo + (f_hi - f_lo) * i / float(n_coarse))
    peak_bands = []
    for fr, zi, _c in terms:
        d = max(12.0 * zi, 0.02)
        a0 = max(f_lo, fr * (1.0 - d))
        a1 = min(f_hi, fr * (1.0 + d))
        peak_bands.append((a0, a1, 1200))
        if a1 <= a0:
            continue
        for i in range(1201):
            grid.append(a0 + (a1 - a0) * i / 1200.0)
    grid.sort()

    two_pi = 2.0 * math.pi
    n = len(terms)
    # Three integrals, all convergent, none of them band-truncated:
    #   z_disp   int W |sum_i D_i|^2 df      displacement of the point (coherent)
    #   z_diag   int W |D_k|^2 df            each mode on its own
    #   g_abs    int W |1 - w^2 sum_i D_i|^2 df   what a bonded sensor reads
    # The acceleration of one mode is w_k^2 times ITS displacement (the response
    # is narrow-band at w_k), which is Miles' own step and stays exact here. The
    # relative-acceleration integrand |w^2 D|^2 is NOT used: it tends to 1/c at
    # high frequency, so any band integral of it would be dominated by a term
    # that is not resonant at all.
    z_disp = 0.0
    g_abs = 0.0
    z_diag = [0.0] * n
    prev = None
    for f in grid:
        w = two_pi * f
        tot = 0j
        per = []
        for fr, zi, c in terms:
            wi = two_pi * fr
            den = (wi * wi - w * w) + 1j * (2.0 * zi * w * wi)
            D = c / den
            tot += D
            per.append(D)
        wf = wfun(f)
        wt = w * w * tot
        vals = (wf * abs(tot) ** 2, wf * abs(1.0 - wt) ** 2,
                [wf * abs(D) ** 2 for D in per])
        if prev is not None:
            h = f - prev[0]
            if h > 0.0:
                z_disp += 0.5 * (prev[1][0] + vals[0]) * h
                g_abs += 0.5 * (prev[1][1] + vals[1]) * h
                pd = prev[1][2]
                for k in range(n):
                    z_diag[k] += 0.5 * (pd[k] + vals[2][k]) * h
        prev = (f, vals)

    z_diag_m = [G0 * math.sqrt(max(0.0, v)) for v in z_diag]
    z_rms_m = G0 * math.sqrt(max(0.0, z_disp))
    g_own = [(two_pi * terms[k][0]) ** 2 * z_diag_m[k] / G0 for k in range(n)]
    g_rms = math.sqrt(sum(v * v for v in g_own))
    g_abs_rms = math.sqrt(max(0.0, g_abs))

    diag_var = sum(v * v for v in z_diag_m)
    total_var = z_rms_m * z_rms_m
    coherent = total_var - diag_var
    shares = [(100.0 * (v * v) / diag_var if diag_var > 0.0 else 0.0)
              for v in z_diag_m]

    strongest = 0
    best = -1.0
    for k, v in enumerate(g_own):
        if v > best:
            best = v
            strongest = k

    if diag_var <= 0.0:
        warnings.append(
            "every mode has shape_at_point * participation ~ 0: uniform base "
            "acceleration does not excite any of them, so this response is ~0 by "
            "symmetry rather than by calculation")

    per_mode = []
    for k, (fr, zi, c) in enumerate(terms):
        per_mode.append({
            "index": k + 1,
            "freq_hz": _sig(fr),
            "zeta": _sig(zi, 6),
            "q_factor": _sig(1.0 / (2.0 * zi), 6),
            "participation_times_shape": _sig(c, 6),
            "g_rms_own": _sig(g_own[k]),
            "displacement_rms_own_mm": _sig(z_diag_m[k] * 1000.0),
            "share_of_resonant_variance_pct": _sig(shares[k], 4),
        })

    coherent_pct = (100.0 * coherent / total_var) if total_var > 0.0 else 0.0
    if abs(coherent_pct) > 5.0:
        warnings.append(
            "the modes overlap: the coherent sum moves the displacement variance "
            "by %+.1f %% against summing them as independent, and that term is in "
            "displacement_rms_mm (it is not dropped). The per-mode accelerations "
            "are still summed as independent, which is the standard modal "
            "superposition - a coherent acceleration number would need the phase "
            "of every mode at the point, which plate_modes does not carry"
            % coherent_pct)

    miles = None
    miles_ratio = None
    miles_z_mm = None
    z_ratio = None
    if wflat is not None:
        fr, zi, c = terms[0]
        miles = abs(c) * math.sqrt(0.5 * math.pi * fr * (1.0 / (2.0 * zi)) * wflat)
        if miles > 0.0:
            miles_ratio = g_rms / miles
            miles_z_mm = G0 * miles / ((two_pi * fr) ** 2) * 1000.0
            if miles_z_mm > 0.0:
                z_ratio = z_rms_m * 1000.0 / miles_z_mm

    return ok(
        {
            "modes_included": n,
            "mode_1_freq_hz": _sig(terms[0][0]),
            "zeta": _sig(terms[0][1], 6),
            "g_rms": _sig(g_rms),
            "g_peak": _sig(g * g_rms),
            "peak_factor": g,
            "g_rms_absolute_including_quasistatic": _sig(g_abs_rms),
            "g_rms_if_only_the_strongest_mode": _sig(g_own[strongest]),
            "displacement_rms_mm": _sig(z_rms_m * 1000.0),
            "displacement_peak_mm": _sig(z_rms_m * 1000.0 * g),
            "z_3sigma_mm": _sig(z_rms_m * 1000.0 * g),
            "per_mode": per_mode,
            "strongest_mode": strongest + 1,
            "resonant_variance_independent_g2": _sig(sum(v * v for v in g_own)),
            "coherent_displacement_variance_share_pct": _sig(coherent_pct, 3),
            "miles_single_mode_g_rms": (None if miles is None else _sig(miles)),
            "ratio_to_miles": (None if miles_ratio is None else _sig(miles_ratio, 4)),
            "miles_single_mode_displacement_rms_mm":
                (None if miles_z_mm is None else _sig(miles_z_mm, 6)),
            "displacement_ratio_to_miles":
                (None if z_ratio is None else _sig(z_ratio, 4)),
            "integration_band_hz": [_sig(f_lo), _sig(f_hi)],
            "grid_points": len(grid),
            "method": ("modal summation of %d modes, steady state, exact complex "
                       "sum for the displacement (cross terms included) and w_k^2 "
                       "times each mode's own displacement for its acceleration, "
                       "then trapezoid on a grid with a fine pass over every peak"
                       % n),
            "warnings": warnings,
        },
        "g",
        "modal summation (steady state) over the modes supplied",
        "z_p(f) = -a sum_i w_i(p) Gamma_i / (w_i^2 - w^2 + 2 j zeta_i w w_i); "
        "g_i = w_i^2 z_i (narrow-band at w_i), g_rms = sqrt(sum g_i^2); absolute "
        "acceleration = a - w^2 z_p, integrated as its own quantity",
        "single-mode limit reproduces Miles' closed form: displacement to the "
        "digits the closed form carries, g_rms inside the trapezoid error "
        "(measured in the selftest and in docs/85)",
        {
            "note": "g_rms is the resonant acceleration, sqrt(sum w_i^4 z_i^2), "
                    "which is the quantity Miles' closed form gives for one mode "
                    "and the one Steinberg's bands are defined against; "
                    "displacement_rms_mm is the coherent sum over modes (cross "
                    "terms in) and is the number to hand to Steinberg; "
                    "g_rms_absolute_including_quasistatic is the full |1 - w^2 z| "
                    "integral, i.e. what a sensor bonded to the board reads at low "
                    "frequency as well",
            "input": "uniform base acceleration; a point force, a shaker through "
                     "one standoff or acoustic excitation is a different problem",
            "damping": "classical (viscous) damping per mode; no joint slip, no "
                       "material damping model, no coupling through the damper",
            "modes": "Gamma and the shape come from plate_modes(participation=true) "
                     "and are exact for the Ritz basis used there; this function "
                     "does not re-derive them",
        },
    )


def miles_response(f_n_hz, psd=None, zeta=None, q_factor=None, gamma=3.0,
                   modes=None):
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

    Pass `modes` (from plate_modes(participation=true)) instead of a single
    f_n_hz and the same PSD is summed over every mode, cross terms included;
    pass neither and nothing about this function changes (0.0.83).
    """
    if modes is not None:
        return _modal_response(psd, modes, zeta=zeta, q_factor=q_factor,
                               gamma=gamma)
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
                 degrees=(5, 7, 9), copper_um=None, copper_um_bottom=None):
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

    `copper_um` / `copper_um_bottom` are passed straight through to
    `plate_modes`, so the stress is recovered at the real outer surface (copper's
    own z, E and nu) instead of at the laminate's h/2. On a one-sided stack the
    two faces are no longer mirror images: both are solved and the worse one is
    reported, with `von_mises_by_surface_mpa` giving each.
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
                        max_dof=max_dof, stress_amplitude_mm=amp,
                        copper_um=copper_um,
                        copper_um_bottom=copper_um_bottom)
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
                    max_dof=max_dof, stress_amplitude_mm=amp,
                    copper_um=copper_um, copper_um_bottom=copper_um_bottom)
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
        "surface_face": st.get("surface_face"),
        "surfaces_checked": st.get("surfaces_checked"),
        "von_mises_by_surface_mpa": st.get("von_mises_by_surface_mpa"),
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
        "z": ("outer surface of the section at that face (copper skin included "
              "in z, and each face uses its own layer's E and nu)" if
              (st.get("surfaces_checked") or 1) > 1 else
              "surface (h/2) - the laminate mid-plane carries none in this model"),
        "ladder": "same mode resolved at several polynomial degrees; a spread over "
                  "1 % means not converged"},
       {"material": val["material"], "bc": val["bc"], "basis": val["basis"],
        "thickness_mm": val["thickness_mm"],
        "formula": st["formula"]})
