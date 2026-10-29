"""Two-dimensional electrostatic field solver (finite volume + SOR).

Why this module exists: every impedance answer this server gave before came from
a closed form, and a closed form cannot tell you how wrong it is. This module
solves Laplace's equation in the actual cross-section instead, so the closed
form can be *checked against something that shares none of its approximations*,
and the disagreement can be reported as a measured number rather than quoted as
"+/-15%".

What is solved
--------------
The cross-section of a printed line is a two-dimensional electrostatic problem
(TEM/quasi-TEM assumption): conductor potentials are fixed, and the potential
elsewhere obeys div(eps * grad V) = 0. Discretised with a finite-volume stencil
on a graded tensor grid (fine near the copper, geometrically coarser far away,
because the field decays with distance and a uniform fine grid over a 10h box
would cost millions of nodes for nothing).

From the total flux leaving the excited conductor, C = Q/V per unit length.
For a homogeneous dielectric Z = 1/(c0 * sqrt(C * C_air)); for a mixed
dielectric (microstrip: substrate below, air above) the same expression is the
standard quasi-static approximation, and is stated as such in the answer.

Edge-coupled pairs are solved in *half* the domain: the symmetry plane at
x = 0 is an electric wall for the odd mode (both traces at +V/-V, so Zdiff =
2*Z_odd) and a magnetic wall for the even mode.

Coplanar lines (cpw / gcpw) are a different problem and get their own section
class: the return conductor is on the same layer as the signal, the field wraps
the slot edges, and the substrate has a finite thickness with either air below
(cpw) or a ground plane below (gcpw). The symmetry plane at x = 0 is NOT
exploited there, on purpose - the field-solved capacitance is compared against
the conformal map as an independent check, and the pair must not share an
assumption that the check is supposed to test.

Conductor LOSS is derived from the same solution, not modelled separately:
`conductor_shape_factor` normalises the computed surface charge to the signal
current and returns INT g(s)^2 ds, which is the geometry factor in
alpha_c = Rs/(2*Z0) * INT g^2 ds. That is a first-order perturbation on a
static solution, valid while the skin depth is far below the copper, and it
inherits every limitation below.

What this is not: a mode solver. It assumes TEM, has no dispersion, no
radiation and no finite-conductivity penetration beyond that perturbation, and
the surface charge it reports is only as good as the mesh at a conductor
corner (a zero-thickness edge is log-divergent). It is a much better
cross-check than a second closed form, not a replacement for the fabricator's
impedance coupon.
"""
import math

from .result import ok, err

C0 = 299792458.0
ETA0 = 376.730313668
EPS0 = 8.8541878128e-12

KINDS = ("microstrip", "stripline")

# Coplanar topologies. Not in KINDS because everything that takes a `kind`
# argument up to here (pairs, shape factors, the pair tool) means an
# edge-coupled pair of traces on one dielectric, and a coplanar line is not
# that: its return conductor is on the same layer.
COPLANAR_KINDS = ("cpw", "gcpw")

# How far the outer boundary sits from the copper, in substrate heights. The
# solver reports the C it obtains at two boxes so that this truncation is a
# measured quantity rather than a silent assumption.
FAR_X = 8.0
FAR_Y = 8.0


# --------------------------------------------------------------- exact check
def _ellip_K(k):
    """Complete elliptic integral of the first kind, via the AGM."""
    if k >= 1.0:
        return float("inf")
    a, b = 1.0, math.sqrt(1.0 - k * k)
    for _ in range(60):
        a, b = 0.5 * (a + b), math.sqrt(a * b)
        if abs(a - b) < 1e-15:
            break
    return math.pi / (2.0 * a)


def stripline_zero_thickness_z0(w, b, er=1.0):
    """Exact zero-thickness stripline impedance (conformal mapping).

    Z0 = eta0 / (4*sqrt(er)) * K(k) / K(k'),  k = sech(pi*w/(2*b))

    Used only to validate the solver: it is the one geometry here with a closed
    solution that the field solver does not share any approximation with.
    """
    k = 1.0 / math.cosh(math.pi * w / (2.0 * b))
    kp = math.sqrt(max(0.0, 1.0 - k * k))
    return ETA0 / (4.0 * math.sqrt(er)) * _ellip_K(k) / _ellip_K(kp)


# ------------------------------------------------------------------- meshing
def _axis(anchors, far=None):
    """One grid axis from (coordinate, step) anchors, then geometric growth.

    The step is per *segment* (the step given on an anchor applies to the
    segment that ends at it), because a uniform fine grid across a 10*h gap
    would spend tens of thousands of nodes on empty dielectric: the resolution
    is only needed where the field is.
    """
    pts = sorted(anchors, key=lambda p: p[0])
    out = [pts[0][0]]
    for (a, _), (b, step) in zip(pts, pts[1:]):
        n = max(1, int(math.ceil((b - a) / step - 1e-9)))
        for i in range(1, n + 1):
            out.append(a + (b - a) * i / n)
    if far is not None and far > out[-1]:
        step = pts[-1][1]
        x = out[-1]
        while x < far - 1e-12:
            step *= 1.4
            x += step
            out.append(min(x, far))
    clean = [out[0]]
    for x in out[1:]:
        if x - clean[-1] > 1e-12:
            clean.append(x)
    return clean


class _Section(object):
    """A cross-section: graded grids, fixed potentials, dielectric map."""

    def __init__(self, kind, w, s, h, t, er, fine, far_x=None, far_y=None):
        self.kind = kind
        self.w, self.s, self.h, self.t, self.er = w, s, h, t, er
        b = 2.0 * h                                     # stripline: two planes
        self.b = b
        # copper features decide the fine step; empty dielectric next to them
        # only needs to be resolved well enough to let the potential relax
        edge = min(w, s) / 4.0
        near = max(3.0 * t, w / 2.0) if t > 0 else w / 2.0
        slow = max(fine * 8.0, near / 6.0)
        x_anchor = [(0.0, slow),
                    (max(s / 2.0 - near, 0.0), slow),
                    (s / 2.0, fine),
                    (s / 2.0 + w, fine)]
        if t > 0:
            y_anchor = [(0.0, min(slow, h / 6.0)), (h, fine), (h + t, fine)]
        else:
            y_anchor = [(0.0, min(slow, h / 8.0)), (h, fine)]
        if kind == "stripline":
            y_anchor.append((b, min(slow, h / 6.0)))
        fx = (far_x if far_x is not None else FAR_X) * h
        fy = (far_y if far_y is not None else FAR_Y) * h
        far_x = max(x_anchor[-1][0] + fx, x_anchor[-1][0] * 1.2)
        far_y = (y_anchor[-1][0] + (fy if kind == "microstrip" else 0.0))
        self.xs = _axis(x_anchor, far_x)
        self.ys = _axis(y_anchor, far_y)
        self.nx, self.ny = len(self.xs), len(self.ys)
        # conductor masks
        self.ground = []        # nodes fixed at 0 V
        self.live = []          # nodes fixed at 1 V
        x0, x1 = s / 2.0, s / 2.0 + w
        eps = 1e-12
        for j, y in enumerate(self.ys):
            on_ground = (abs(y) < eps) or (kind == "stripline" and abs(y - b) < eps)
            on_strip = (y >= h - eps) and (y <= h + t + eps)
            for i, x in enumerate(self.xs):
                n = j * self.nx + i
                if on_ground:
                    self.ground.append(n)
                elif on_strip and (x >= x0 - eps) and (x <= x1 + eps):
                    self.live.append(n)

    # -- dielectric of a face ------------------------------------------------
    def _eps_x(self, y):
        """dielectric seen by an in-plane (x) face at height y."""
        if self.kind == "stripline":
            return self.er
        if abs(y - self.h) < 1e-12:
            return 0.5 * (self.er + 1.0)      # the strip sits on the interface
        return self.er if y < self.h else 1.0

    def _eps_y(self, y_mid):
        if self.kind == "stripline":
            return self.er
        return self.er if y_mid < self.h else 1.0

    def _cell_w(self, i):
        xs = self.xs
        return (xs[i + 1] - xs[i]) if i + 1 < self.nx else (xs[i] - xs[i - 1])

    def _cell_h(self, j):
        ys = self.ys
        return (ys[j + 1] - ys[j]) if j + 1 < self.ny else (ys[j] - ys[j - 1])

    def build(self, odd=True):
        """Assemble the finite-volume stencil over the free nodes.

        Face conductance a = eps * face_length / distance, where the face
        length is the control-volume edge crossed by the flux and the eps is
        the one the face actually sits in (so the substrate/air interface is a
        property of the face, not of a node).
        """
        nx, ny, xs, ys = self.nx, self.ny, self.xs, self.ys
        fixed = {}
        for n in self.ground:
            fixed[n] = 0.0
        for n in self.live:
            fixed[n] = 1.0
        if odd:
            for j in range(ny):
                fixed.setdefault(j * nx, 0.0)
        free, rows = [], []
        for j in range(ny):
            y = ys[j]
            hy, hx = self._cell_h(j), None
            for i in range(nx):
                n = j * nx + i
                if n in fixed:
                    continue
                hx = self._cell_w(i)
                nb = []
                if i > 0:
                    dx = xs[i] - xs[i - 1]
                    nb.append((n - 1, self._eps_x(y) * hy / dx))
                if i + 1 < nx:
                    dx = xs[i + 1] - xs[i]
                    nb.append((n + 1, self._eps_x(y) * hy / dx))
                if j > 0:
                    dy = y - ys[j - 1]
                    nb.append((n - nx, self._eps_y(0.5 * (y + ys[j - 1])) * hx / dy))
                if j + 1 < ny:
                    dy = ys[j + 1] - y
                    nb.append((n + nx, self._eps_y(0.5 * (y + ys[j + 1])) * hx / dy))
                free.append(n)
                rows.append((n, nb, sum(c for _, c in nb)))
        self._fixed = fixed
        self._free = free
        self._rows = rows
        # conductor rows use the same coefficients, so the charge is the exact
        # discrete flux and not a differently-discretised second estimate
        self._conductor_rows = []
        live_set = set(self.live)
        ends = set()
        for n in self.live:
            i, j = n % nx, n // nx
            left = (i > 0) and (n - 1 in live_set)
            right = (i + 1 < nx) and (n + 1 in live_set)
            if not (left and right):
                ends.add(n)
        for n in self.live:
            i, j = n % nx, n // nx
            y = ys[j]
            hx, hy = self._cell_w(i), self._cell_h(j)
            nb = []
            w_out = 0.5 if n in ends else 1.0
            if i > 0:
                nb.append((n - 1, w_out * self._eps_x(y) * hy / (xs[i] - xs[i - 1])))
            if i + 1 < nx:
                nb.append((n + 1, w_out * self._eps_x(y) * hy / (xs[i + 1] - xs[i])))
            if j > 0:
                nb.append((n - nx, self._eps_y(0.5 * (y + ys[j - 1])) * hx / (y - ys[j - 1])))
            if j + 1 < ny:
                nb.append((n + nx, self._eps_y(0.5 * (y + ys[j + 1])) * hx / (ys[j + 1] - y)))
            self._conductor_rows.append((n, nb))

    def solve(self, tol=1e-8, max_sweeps=4000, omega=1.95, init=None):
        """Red-black SOR on the assembled stencil. Returns V (flat list).

        `init` (a coarser solution interpolated onto this grid) is what keeps
        the sweep count proportional to the *refinement*, not to the grid: the
        open-boundary microstrip converges from zero in ~600 sweeps and from a
        coarse solution in ~100.
        """
        nx, ny = self.nx, self.ny
        V = list(init) if init is not None else [0.0] * (nx * ny)
        for n, v in self._fixed.items():
            V[n] = v
        black, red = [], []
        for row in self._rows:
            (black if ((row[0] // nx) + (row[0] % nx)) % 2 == 0 else red).append(row)
        self.sweeps = 0
        self.last_delta = float("inf")
        for sweep in range(max_sweeps):
            delta = 0.0
            for group in (black, red):
                for n, nb, s in group:
                    acc = 0.0
                    for m, c in nb:
                        acc += c * V[m]
                    new = (1.0 - omega) * V[n] + omega * acc / s
                    d = new - V[n]
                    if d < 0.0:
                        d = -d
                    if d > delta:
                        delta = d
                    V[n] = new
            self.sweeps = sweep + 1
            self.last_delta = delta
            if sweep > 20 and delta < tol:
                break
        return V

    def charge(self, V):
        """Total flux leaving the live conductor at V = 1: Q per unit length.

        Kept for the record (it is the definition), but the solver reports the
        energy estimate: flux through a one-cell-thick conductor converges like
        h^(1/2) because of the edge singularity, while the energy functional is
        stationary, so its error is second order in the potential error.
        """
        q = 0.0
        for n, nb in self._conductor_rows:
            for m, c in nb:
                q += c * (V[n] - V[m])
        return q

    def energy(self, V):
        """C/eps0 from the field energy: W = 0.5*sum(a*(dV)^2), C = 2W/V^2.

        Every face is counted once (the stencil rows only look forward for the
        faces that are not shared, and the shared ones cancel by symmetry when
        summed over free rows), so the sum walks the node grid faces directly.
        """
        nx, ny, xs, ys = self.nx, self.ny, self.xs, self.ys
        total = 0.0
        for j in range(ny):
            y = ys[j]
            hx, hy = self._cell_w(min(1, nx - 1)) if False else None, None
            for i in range(nx):
                n = j * nx + i
                hx = (xs[i + 1] - xs[i]) if i + 1 < nx else (xs[i] - xs[i - 1] if i else 1.0)
                hy = (ys[j + 1] - ys[j]) if j + 1 < ny else (ys[j] - ys[j - 1] if j else 1.0)
                if i + 1 < nx:                       # x-face
                    a = self._eps_x(y) * hy / (xs[i + 1] - xs[i])
                    d = V[n] - V[n + 1]
                    total += a * d * d
                if j + 1 < ny:                       # y-face
                    a = self._eps_y(0.5 * (y + ys[j + 1])) * hx / (ys[j + 1] - y)
                    d = V[n] - V[n + nx]
                    total += a * d * d
        return total


def _interp(coarse, fine):
    """Bilinear transfer of a coarse solution onto the finer grid."""
    import bisect
    cxs, cys = coarse.xs, coarse.ys
    nx, ny, xs, ys = fine.nx, fine.ny, fine.xs, fine.ys
    out = [0.0] * (nx * ny)
    for j, y in enumerate(ys):
        j0 = min(max(bisect.bisect_right(cys, y) - 1, 0), len(cys) - 1)
        if j0 == len(cys) - 1 or abs(cys[j0] - y) < 1e-15:
            j1, ty = j0, 0.0
        else:
            j1 = j0 + 1
            spany = cys[j1] - cys[j0]
            ty = (y - cys[j0]) / spany if spany > 0 else 0.0
        for i, x in enumerate(xs):
            i0 = min(max(bisect.bisect_right(cxs, x) - 1, 0), len(cxs) - 1)
            if i0 == len(cxs) - 1 or abs(cxs[i0] - x) < 1e-15:
                i1, tx = i0, 0.0
            else:
                i1 = i0 + 1
                spanx = cxs[i1] - cxs[i0]
                tx = (x - cxs[i0]) / spanx if spanx > 0 else 0.0
            v00 = coarse._V[j0 * coarse.nx + i0]
            v10 = coarse._V[j0 * coarse.nx + i1]
            v01 = coarse._V[j1 * coarse.nx + i0]
            v11 = coarse._V[j1 * coarse.nx + i1]
            out[j * nx + i] = ((1 - tx) * (1 - ty) * v00 + tx * (1 - ty) * v10
                               + (1 - tx) * ty * v01 + tx * ty * v11)
    return out


def _cap(kind, w, s, h, t, er, fine, odd=True, far_x=None, far_y=None, coarse=4.0,
         sec_for_shape=None):
    """Capacitance per unit length shape factor, with a coarse-to-fine start."""
    init = None
    if sec_for_shape is not None:
        # same mesh, other symmetry condition: the coarse solution of this
        # geometry is the only cheap start there is
        sec = _Section(kind, w, s, h, t, er, fine, far_x=far_x, far_y=far_y)
        sec.build(odd=odd)
        V = sec.solve(init=None)
        sec._V = V
        return sec.charge(V), sec
    if coarse and fine * coarse < max(w, s, h):
        csec = _Section(kind, w, s, h, t, er, fine * coarse, far_x=far_x, far_y=far_y)
        csec.build(odd=odd)
        csec._V = csec.solve()
        sec = _Section(kind, w, s, h, t, er, fine, far_x=far_x, far_y=far_y)
        sec.build(odd=odd)
        init = _interp(csec, sec)
    else:
        sec = _Section(kind, w, s, h, t, er, fine, far_x=far_x, far_y=far_y)
        sec.build(odd=odd)
    V = sec.solve(init=init)
    sec._V = V
    # charge, not energy: the two agree exactly on the discrete solution, but the
    # charge sum is where the half-cell end weighting lives, and that weighting is
    # worth ~2% against the exact stripline answer (see tests/test_field2d.py)
    return sec.charge(V), sec


def _cap_modes(kind, w, s, h, t, er, fine, far_x=None, far_y=None):
    """Odd- and even-mode capacitance shape factors, and the isolated-line one.

    C_odd (electric wall at the symmetry plane) and C_even (magnetic wall) are
    the two eigen-excitations of a symmetric pair. Their mean is the capacitance
    of ONE line on its own, and that identity is what makes the single-ended
    answer independent of the gap - which is exactly why it can be compared with
    a single-line closed form.
    """
    cd_odd, sec = _cap(kind, w, s, h, t, er, fine, odd=True, far_x=far_x, far_y=far_y)
    cd_even, _ = _cap(kind, w, s, h, t, er, fine, odd=False, far_x=far_x, far_y=far_y,
                      sec_for_shape=sec)
    ca_odd, _ = _cap(kind, w, s, h, t, 1.0, fine, odd=True,
                     far_x=sec.xs[-1] / h, far_y=(sec.ys[-1] - h) / h
                     if kind == "microstrip" else None)
    ca_even, _ = _cap(kind, w, s, h, t, 1.0, fine, odd=False,
                      far_x=sec.xs[-1] / h, far_y=(sec.ys[-1] - h) / h
                      if kind == "microstrip" else None)
    return {"c_odd_d": cd_odd, "c_even_d": cd_even, "c_odd_a": ca_odd,
            "c_even_a": ca_even, "sec": sec}


def solve_single(kind, w_mm, h_mm, t_mm, er, fine_div=12.0, gap_h=1.5):
    """Isolated-line impedance from the mean of the odd and even modes."""
    fine = max(min(w_mm, h_mm, t_mm if t_mm > 0 else h_mm) / fine_div, 1e-6)
    s = gap_h * h_mm
    m = _cap_modes(kind, w_mm, s, h_mm, t_mm, er, fine)
    c_d = 0.5 * (m["c_odd_d"] + m["c_even_d"])
    c_a = 0.5 * (m["c_odd_a"] + m["c_even_a"])
    z = 1.0 / (C0 * math.sqrt(c_d * EPS0 * c_a * EPS0))
    return z, m["sec"]


def _section_solution(kind, w, s, h, t, er, fine, odd=False, coarse=4.0):
    """Solve one cross-section, warm-starting from a coarser mesh.

    Same trick as `_cap`: the sweep count then tracks the refinement and not the
    grid size, which is what makes the second (finer) mesh affordable.
    """
    init = None
    if coarse and fine * coarse < max(w, s, h):
        csec = _Section(kind, w, s, h, t, er, fine * coarse)
        csec.build(odd=odd)
        csec._V = csec.solve()
        sec = _Section(kind, w, s, h, t, er, fine)
        sec.build(odd=odd)
        init = _interp(csec, sec)
    else:
        sec = _Section(kind, w, s, h, t, er, fine)
        sec.build(odd=odd)
    sec._V = sec.solve(init=init)
    return sec

def _surface_charge_faces(sec, nodeset):
    """Per-face flux leaving a conductor, with the face length it is spread over.

    A face is only counted when the other side is free space: a face onto another
    fixed node is interior copper (or a conductor pair the solver was not asked
    about) and carries no surface charge of its own.

    Returns [(q, L, axis)], q = eps_r * (L/d) * dV in volts (the eps0 is factored
    out: it cancels in the normalisation), L in whatever the grid unit is.
    """
    nx, xs, ys, ny = sec.nx, sec.xs, sec.ys, sec.ny
    fixed = set(sec.live) | set(sec.ground)
    V = sec._V
    out = []
    for n in nodeset:
        i, j = n % nx, n // nx
        y = ys[j]
        hx, hy = sec._cell_w(i), sec._cell_h(j)
        if i > 0 and (n - 1) not in fixed:
            q = sec._eps_x(y) * hy / (xs[i] - xs[i - 1]) * (V[n] - V[n - 1])
            out.append((q, hy, "x"))
        if i + 1 < nx and (n + 1) not in fixed:
            q = sec._eps_x(y) * hy / (xs[i + 1] - xs[i]) * (V[n] - V[n + 1])
            out.append((q, hy, "x"))
        if j > 0 and (n - nx) not in fixed:
            q = sec._eps_y(0.5 * (y + ys[j - 1])) * hx / (y - ys[j - 1]) * (V[n] - V[n - nx])
            out.append((q, hx, "y"))
        if j + 1 < ny and (n + nx) not in fixed:
            q = sec._eps_y(0.5 * (y + ys[j + 1])) * hx / (ys[j + 1] - y) * (V[n] - V[n + nx])
            out.append((q, hx, "y"))
    return out

class _Coplanar(object):
    """Coplanar waveguide cross-section: one centre strip, two coplanar grounds.

    Geometry, in the grid unit (mm): the copper plane sits at y = h and occupies
    h .. h+t; the centre strip spans |x| <= w/2 and the grounds start at
    |x| >= w/2 + g. The substrate fills 0 .. h. For "gcpw" the plane y = 0 is a
    ground plane; for "cpw" it is the far side of a finite-thickness substrate
    and everything below is air - which is exactly the geometry the k1 = sinh
    map of the conformal solution describes.

    The symmetry plane x = 0 is meshed, not mirrored: see the module docstring.
    The two behaviours this has to get right, and both are easy to get wrong in
    a way that still looks plausible:

      * the eps seen by an in-plane face at the copper level is the half-and-half
        average, because that face sits across the substrate/air interface;
      * below y = 0 an unbacked line has air, so both directional eps functions
        have to look at the sign of y and not only at the substrate height.
    """

    def __init__(self, kind, w, g, h, t, er, fine, far_span=8.0):
        self.kind = kind
        self.w, self.g, self.h, self.t, self.er = w, g, h, t, er
        self.backed = (kind == "gcpw")
        near = max(3.0 * t, min(w, g) / 2.0)
        slow = max(fine * 8.0, near / 6.0)
        a, b = w / 2.0, w / 2.0 + g
        x_anchor = [(0.0, slow), (max(a - near, 0.0), slow), (a, fine), (b, fine)]
        # The truncation scale is the IN-PLANE geometry, not the substrate
        # height. Measured on w=0.3 g=0.2: at h=0.2 the answer is still moving by
        # 0.3 % between 16h and 24h and settles around 6-7*(w+2g); at h=2.0 the
        # same 6-7*(w+2g) is already within 0.15 % of the 24h answer while the
        # 24h box (48 mm, 4000 sweeps, no convergence) costs 7x more nodes and
        # does not even reach the tolerance. Scaling the box with h is what made
        # a thick substrate take minutes.
        span = w + 2.0 * g
        far = max(far_span * span, span * 1.2)
        far_x = b + far
        half_x = _axis(x_anchor, far_x)
        # mirrored, so the two slots are discretised identically: an asymmetric
        # mesh would put a bias on the left/right split and the split is one of
        # the numbers this returns
        self.xs = [-v for v in reversed(half_x[1:])] + half_x
        # The fine band is only needed where the field is: the interface carries
        # a singularity at the slot edges, the bulk of a thick substrate does
        # not. Without the h - 8*fine anchor a 2 mm substrate spent 120 cells on
        # itself and the run took minutes for nothing.
        y_pos = [(0.0, min(slow, h / 6.0)),
                 (max(h - 8.0 * fine, 0.0), fine),
                 (h, fine)]
        if t > 0:
            y_pos.append((h + t, fine))
        above = _axis(y_pos, h + far)
        if self.backed:
            self.ys = above
        else:
            below = [-v for v in reversed(_axis([(0.0, min(slow, h / 6.0))], far))]
            self.ys = [v for v in below if v < -1e-12] + above
        self.nx, self.ny = len(self.xs), len(self.ys)
        self.ground, self.live = [], []
        eps = 1e-12
        for j, y in enumerate(self.ys):
            on_plane = (h - eps) <= y <= (h + t + eps)
            on_back = self.backed and abs(y) < eps
            for i, x in enumerate(self.xs):
                n = j * self.nx + i
                ax = abs(x)
                if on_back:
                    self.ground.append(n)
                elif on_plane:
                    if ax <= a + eps:
                        self.live.append(n)
                    elif ax >= b - eps:
                        self.ground.append(n)

    def _eps_x(self, y):
        if abs(y - self.h) < 1e-12:
            return 0.5 * (self.er + 1.0)
        if y < self.h:
            return self.er if (y >= 0.0 or self.backed) else 1.0
        return 1.0

    def _eps_y(self, y_mid):
        if not self.backed and y_mid < 0.0:
            return 1.0
        return self.er if y_mid < self.h else 1.0

    # Centred control volumes, not the forward-difference widths the pair
    # section uses. The pair section's choice is a legacy of it being written
    # first; here the symmetric answer is a hypothesis the tests check (the two
    # return wings must split the current evenly, exactly), and a half-shifted
    # control volume breaks that symmetry by ~10 % while giving the same Z0.
    def _cell_w(self, i):
        xs, n = self.xs, len(self.xs)
        if i == 0:
            return xs[1] - xs[0]
        if i == n - 1:
            return xs[n - 1] - xs[n - 2]
        return 0.5 * (xs[i + 1] - xs[i - 1])

    def _cell_h(self, j):
        ys, n = self.ys, len(self.ys)
        if j == 0:
            return ys[1] - ys[0]
        if j == n - 1:
            return ys[n - 1] - ys[n - 2]
        return 0.5 * (ys[j + 1] - ys[j - 1])

    build = _Section.build
    solve = _Section.solve
    charge = _Section.charge
    energy = _Section.energy


def _copl_solution(kind, w, g, h, t, er, fine, coarse=4.0, max_nodes=None):
    """Solve one coplanar cross-section, warm-starting from a coarser mesh."""
    init = None
    if coarse and fine * coarse < max(w, g, h):
        csec = _Coplanar(kind, w, g, h, t, er, fine * coarse)
        csec.build(odd=False)
        _check_nodes(csec, max_nodes)
        csec._V = csec.solve()
        sec = _Coplanar(kind, w, g, h, t, er, fine)
        sec.build(odd=False)
        _check_nodes(sec, max_nodes)
        init = _interp(csec, sec)
    else:
        sec = _Coplanar(kind, w, g, h, t, er, fine)
        sec.build(odd=False)
        _check_nodes(sec, max_nodes)
    sec._V = sec.solve(init=init)
    return sec

def _check_nodes(sec, max_nodes):
    if max_nodes and sec.nx * sec.ny > int(max_nodes):
        raise ValueError("section needs %d mesh nodes, over the %d-node limit"
                         % (sec.nx * sec.ny, int(max_nodes)))

def _copl_caps(kind, w, g, h, t, er, fine, coarse=4.0, max_nodes=None):
    """Dimensionless C/eps0 of the coplanar section, dielectric and vacuum."""
    sec = _copl_solution(kind, w, g, h, t, er, fine, coarse=coarse,
                         max_nodes=max_nodes)
    c_d = sec.charge(sec._V)
    sea = _copl_solution(kind, w, g, h, t, 1.0, fine, coarse=coarse,
                         max_nodes=max_nodes)
    c_a = sea.charge(sea._V)
    return c_d, c_a, sec

def coplanar_solve_pair(kind, w_mm, g_mm, h_mm, t_mm=0.0, er=4.3,
                        levels=(1.0, 0.65), fine_div=10.0, max_nodes=None):
    """Z0 at two mesh densities, with the drift between them. Returns (z, drift, runs).

    Z0 = eta0/sqrt(C_d*C_a) with both C in eps0 units: the inductance of a TEM
    line does not care about the dielectric, so sqrt(L/C) = eta0/sqrt(c_d*c_a)
    and writing 1/(c0*C) instead is the classic factor-of-sqrt(eps_eff) error.
    Same expression as `_z_from_caps`, on a section that is not a pair.
    """
    kind = str(kind or "").strip().lower()
    if kind not in COPLANAR_KINDS:
        raise ValueError("coplanar_solve_pair: kind must be cpw or gcpw")
    w, g, h, t, er = float(w_mm), float(g_mm), float(h_mm), float(t_mm), float(er)
    if w <= 0 or g <= 0 or h <= 0 or t < 0 or er < 1.0:
        raise ValueError("coplanar_solve_pair: bad geometry")
    fine0 = _fine_step(w, g, h, t, fine_div)
    runs = []
    for factor in levels:
        fine = fine0 * factor
        c_d, c_a, sec = _copl_caps(kind, w, g, h, t, er, fine,
                                   max_nodes=max_nodes)
        if c_d <= 0.0 or c_a <= 0.0:
            raise ValueError("coplanar_solve_pair: non-positive capacitance")
        runs.append({"fine_mm": fine, "c_dielectric": c_d, "c_air": c_a,
                     "z0": ETA0 / math.sqrt(c_d * c_a), "eps_eff": c_d / c_a,
                     "nodes": sec.nx * sec.ny, "sweeps": sec.sweeps,
                     "residual": sec.last_delta})
    main = runs[0]["z0"]
    drift = None
    if len(runs) > 1:
        ratio = runs[0]["fine_mm"] / runs[1]["fine_mm"]
        drift = (runs[1]["z0"] - main) / (ratio - 1.0)
    return main, drift, runs

def coplanar_shape_factor(kind, w_mm, g_mm, h_mm, t_mm=0.0, er=4.3,
                          fine_div=8.0, refine=1.5, max_nodes=None):
    """INT g(s)^2 ds for a coplanar line: the geometry factor in alpha_c.

    Same quantity, same code path and the same normalisation as
    `conductor_shape_factor` (the surface charge normalised to the signal
    current), so the reading of the number is identical. What differs is which
    nodes are conductors, and that the return current has two wings.

    Read the caveats, because for a coplanar line they are not decoration:

      * at zero thickness BOTH the strip edges and the ground edges are
        r^-1/2 singularities, so INT g^2 ds has a logarithmic mesh dependence
        and no converged value exists. At t = 0 the number returned is the value
        on this mesh and `mesh_drift_pct` is the honest uncertainty;
      * there is no independent exact reference for this integral on a coplanar
        section in this repo (the microstrip/stripline paths have two: the
        parallel-plate limit and the incremental-inductance rule). What IS
        checked independently is the geometry: the capacitance behind the same
        solution reproduces the conformal map to a few percent, see VALIDATION;
      * the ground planes are truncated at far_x, so the far tail of the return
        current is cut. It decays with distance from the slot, and the reported
        `ground_share` moves by the truncation, so compare `ground_share` between
        the two meshes before trusting the return split.
    """
    kind = str(kind or "").strip().lower()
    if kind not in COPLANAR_KINDS:
        raise ValueError("coplanar_shape_factor: kind must be cpw or gcpw")
    w, g, h, t, er = float(w_mm), float(g_mm), float(h_mm), float(t_mm), float(er)
    if w <= 0 or g <= 0 or h <= 0 or t < 0 or er < 1.0:
        raise ValueError("coplanar_shape_factor: bad geometry")
    fine = _fine_step(w, g, h, t, fine_div)
    meshes = []
    for factor in ((1.0, refine) if refine and refine > 1.0 else (1.0,)):
        sec = _copl_solution(kind, w, g, h, t, er, fine * factor,
                             max_nodes=max_nodes)
        live = _surface_charge_faces(sec, set(sec.live))
        ground = _surface_charge_faces(sec, set(sec.ground))
        q_live = sum(q for q, _, _ in live)
        q_ground = sum(q for q, _, _ in ground)
        if q_live <= 0.0:
            raise ValueError("coplanar_shape_factor: the centre strip carries no "
                             "positive charge; the mesh is degenerate")
        integ = 0.0
        share = {"live": 0.0, "ground": 0.0}
        by_axis = {"x": 0.0, "y": 0.0}
        for tag, faces in (("live", live), ("ground", ground)):
            for q, L, axis in faces:
                v = q * q / (L * q_live * q_live)
                integ += v
                share[tag] += v
                by_axis[axis] += v
        # Three return paths, not one: the two coplanar wings and - for gcpw -
        # the backside plane. Partitioned by position, with no node left over
        # (the x = 0 column belongs to the backside plane, never to a wing,
        # because the wing rows have the strip at their centre). A mesh that
        # came out asymmetric anywhere shows up as an unequal left/right split.
        wing = {"left": 0.0, "right": 0.0, "back": 0.0}
        for tag in ("left", "right", "back"):
            nodes = []
            for n in sec.ground:
                x = sec.xs[n % sec.nx]
                y = sec.ys[n // sec.nx]
                if abs(y) < 1e-12 and sec.backed:
                    where = "back"
                else:
                    where = "left" if x < 0.0 else "right"
                if where == tag:
                    nodes.append(n)
            for q, L, _ in _surface_charge_faces(sec, set(nodes)):
                wing[tag] += q * q / (L * q_live * q_live)
        meshes.append({
            "fine_mm": fine * factor,
            "shape_factor_per_mm": integ,
            "live_share": share["live"],
            "ground_share": share["ground"],
            "side_share": by_axis["x"],
            "face_share": by_axis["y"],
            "faces_live": len(live),
            "faces_ground": len(ground),
            "q_live": q_live,
            "ground_to_live_charge_ratio": (None if not q_live else q_ground / q_live),
            "ground_share_left": wing["left"],
            "ground_share_right": wing["right"],
            "ground_share_back": wing["back"],
            "nodes": sec.nx * sec.ny,
            "sweeps": sec.sweeps,
            "residual": sec.last_delta,
        })
    best = meshes[0]
    drift = None
    if len(meshes) > 1:
        coarse, fine_run = meshes[1], meshes[0]
        if fine_run["shape_factor_per_mm"]:
            drift = 100.0 * (fine_run["shape_factor_per_mm"]
                             - coarse["shape_factor_per_mm"]) \
                / fine_run["shape_factor_per_mm"]
    out = dict(best)
    out["mesh_drift_pct"] = drift
    out["coarser"] = meshes[1] if len(meshes) > 1 else None
    out["kind"] = kind
    out["gap_mm"] = g
    out["er"] = er
    out["zero_thickness"] = (t <= 0.0)
    return out

def conductor_shape_factor(kind, w_mm, h_mm, t_mm=0.0, er=1.0, fine_div=10.0,
                           gap_h=4.0, refine=1.6, max_nodes=None):
    """Shape factor of the current distribution: the number that turns Rs/Z0 into loss.

    For a quasi-TEM line the skin-effect attenuation is

        alpha_c = Rs / (2*Z0) * INT g(s)^2 ds,      INT g ds = 1 over one conductor

    where g(s) is the surface current density normalised to the signal current.
    The integral is a pure geometry number (1/length); this function solves the
    electrostatics and reads it off the discrete surface charge, because in TEM
    the surface charge and the longitudinal current are the same distribution.

    This is the honest replacement for the Rs/(Z0*w) sheet estimate: it carries
    the edge crowding that the sheet estimate misses, and on stripline it carries
    the current split between the two reference planes, which the sheet estimate
    does not even try.

    Two things it is NOT:
      * with t = 0 the strip edge is a 180-degree corner, where the surface
        density diverges as r^-1/2 and INT g^2 ds diverges logarithmically. A
        converged number therefore does not exist for zero-thickness copper; the
        value returned is the value on the mesh it was computed on, and drift
        between the two meshes is reported so that the dependence is visible.
        Ask for finite thickness when the answer matters.
      * it is a 2-D electrostatic solution, so the face of the trace that the
        current actually turns around on (the far side of a bend) is not here.

    Returns a dict (units: the grid is in mm, so the shape factor is 1/mm).
    """
    kind = str(kind or "").strip().lower()
    if kind not in KINDS:
        raise ValueError("conductor_shape_factor: kind must be microstrip or stripline")
    w, h, t, er = float(w_mm), float(h_mm), float(t_mm), float(er)
    if w <= 0 or h <= 0 or t < 0 or er < 1.0:
        raise ValueError("conductor_shape_factor: bad geometry")
    s = max(float(gap_h), 1.0) * h
    fine = _fine_step(w, s, h, t, fine_div)
    if max_nodes:
        # the grid is cheap to build and expensive to solve, so the size check has
        # to happen before the first SOR sweep, not after
        probe = _Section(kind, w, s, h, t, er, fine)
        if probe.nx * probe.ny > int(max_nodes):
            raise ValueError("section needs %d mesh nodes, over the %d-node limit"
                             % (probe.nx * probe.ny, int(max_nodes)))
    meshes = []
    for factor in ((1.0, refine) if refine and refine > 1.0 else (1.0,)):
        sec = _section_solution(kind, w, s, h, t, er, fine * factor)
        live = _surface_charge_faces(sec, sec.live)
        ground = _surface_charge_faces(sec, sec.ground)
        q_live = sum(q for q, _, _ in live)
        if q_live <= 0.0:
            raise ValueError("conductor_shape_factor: the excited conductor carries "
                             "no positive charge; the mesh is degenerate")
        integ = 0.0
        share = {"live": 0.0, "ground": 0.0}
        by_axis = {"x": 0.0, "y": 0.0}
        for tag, faces in (("live", live), ("ground", ground)):
            for q, L, axis in faces:
                v = q * q / (L * q_live * q_live)
                integ += v
                share[tag] += v
                by_axis[axis] += v
        meshes.append({
            "fine_mm": fine * factor,
            "shape_factor_per_mm": integ,
            "live_share": share["live"],
            "ground_share": share["ground"],
            "side_share": by_axis["x"],
            "face_share": by_axis["y"],
            "faces_live": len(live),
            "faces_ground": len(ground),
            "q_live": q_live,
            "ground_to_live_charge_ratio": (
                None if not q_live else
                sum(q for q, _, _ in ground) / q_live),
            "nodes": sec.nx * sec.ny,
            "sweeps": sec.sweeps,
            "residual": sec.last_delta,
        })
    best = meshes[0]
    drift = None
    if len(meshes) > 1:
        coarse, fine_run = meshes[1], meshes[0]
        if fine_run["shape_factor_per_mm"]:
            drift = 100.0 * (fine_run["shape_factor_per_mm"]
                             - coarse["shape_factor_per_mm"]) \
                / fine_run["shape_factor_per_mm"]
    out = dict(best)
    out["mesh_drift_pct"] = drift
    out["coarser"] = meshes[1] if len(meshes) > 1 else None
    out["gap_h"] = gap_h
    out["er"] = er
    out["zero_thickness"] = (t <= 0.0)
    return out

def _z_from_caps(c_shape_d, c_shape_a, odd):
    """Zdiff (odd) or Zcommon (even) from the two dimensionless shape factors.

    `charge()` returns the discrete flux with lengths in whatever unit the grid
    uses; the flux is invariant under that scaling (a derivative in 1/length
    times a length), so the quantity is exactly the dimensionless C/(eps0) of
    the cross-section.
    """
    c_d = c_shape_d * EPS0
    c_a = c_shape_a * EPS0
    z_line = 1.0 / (C0 * math.sqrt(c_d * c_a))
    return 2.0 * z_line if odd else 0.5 * z_line


# Every number below is reproduced by tests/test_field2d.py against the same code
# path the tools use, so this table cannot drift away from the solver silently.
VALIDATION = {
    "stripline_vs_conformal": {
        "case": "zero-thickness homogeneous stripline, single conductor, against its "
                "exact conformal-mapping answer (K(k)/K(k'), k = sech(pi*w/(2*b))); "
                "taken through solve_single, i.e. the odd/even mean that loses the far "
                "conductor",
        "error_pct_by_w_over_b": {"0.5": 1.20, "1.0": 0.71, "2.0": 0.42, "4.0": 0.23},
    },
    "stripline_pair_far_gap": {
        "case": "the coupled path the tool actually runs (solve_pair, Zdiff = 2*Z_odd) "
                "with the gap pushed to s/h=8, where 2x the isolated exact answer is a "
                "legitimate reference: what is left is the finite far boundary and the "
                "edge discretisation",
        "error_pct_by_w_over_b": {"0.5": 2.71, "1.0": 1.15},
    },
    "microstrip_vs_hammerstad": {
        "case": "isolated microstrip (mean of the odd and even modes, which is the "
                "single-line capacitance) against Hammerstad-Jensen, itself good to "
                "about 1% on the same range",
        "error_pct": {"w/h=0.5 er=4.3": -0.35, "w/h=1 er=4.3": -1.93,
                      "w/h=2 er=4.3": -2.22, "w/h=4 er=4.3": -1.72,
                      "w/h=1 er=1": -2.83, "w/h=2 er=9.6": -1.80},
    },
    "coplanar_vs_conformal": {
        "case": "zero-thickness cpw/gcpw against the quasi-static conformal map of "
                "feedline._cpw, which is the model the feedline tool ships. The "
                "solver runs a FULL-domain mesh (no mirror at x = 0), so the two "
                "numbers do not share the symmetry assumption - that is the point of "
                "the comparison, because a half-domain mesh with the face length "
                "taken as a whole cell reads 2 % high on its own",
        "error_pct_by_case": {
            "cpw w=0.3 g=0.2 h=0.2 er=4.3": -1.19,
            "gcpw w=0.3 g=0.2 h=0.2 er=4.3": -2.28,
            "cpw w=0.5 g=0.3 h=0.5 er=4.3": -1.90,
            "gcpw w=0.2 g=0.15 h=0.2 er=4.3": -3.12,
            "cpw w=1.0 g=0.5 h=1.0 er=2.2": -2.07,
            "gcpw w=0.5 g=0.25 h=0.8 er=4.3": -2.87,
            "cpw w=0.3 g=0.2 h=2.0 er=4.3": -3.39,
        },
        "note": "the solver reads LOW of the closed form on every case, i.e. the "
                "conformal map is the optimistic end and the bias grows where the "
                "slot is narrow relative to the substrate (h/g = 0.67 -> -1.19 %, "
                "h/g = 13.3 -> -3.39 %). The box is 8*(w+2g) from the slot outwards, "
                "not 24*h: scaling it with h made a 2 mm substrate need 48 mm of "
                "ground, 4000 sweeps and no convergence, while the measured "
                "truncation error at 6-7*(w+2g) is 0.1 %. Truncation is one-sided "
                "and logarithmic, so it does not average out with mesh refinement: "
                "at w=0.3 g=0.2 h=0.2 the answer still moves 0.09 % between "
                "16h and 24h",
    },
    "worst_seen_pct": 3.39,
    "note": "all four checks land inside 3.4%. The stripline bias is positive and grows "
            "as the strip narrows (the edge singularity is under-resolved), the "
            "microstrip bias is negative and flat - the two directions differ, which is "
            "what you expect from two different references rather than from one shared "
            "discretisation error",
    "correction": "a first draft of this table read -7.05% at w/b=0.5. That number "
                  "compared a COUPLED pair (s/h=1, Zdiff) against 2x the ISOLATED exact "
                  "answer, so it was measuring coupling, not solver error. The same "
                  "geometry now reads -6.38% at s/h=1 and +2.41% at s/h=4: the tight-gap "
                  "entry was never a solver accuracy figure. There is no independent "
                  "exact reference for the coupled case in this table at all",
}


def _fine_step(w_mm, s_mm, h_mm, t_mm, fine_div):
    """The finest cell in the mesh.

    It used to be min(w, s, h, t)/fine_div, which made a 35 um foil demand a 0.003 mm
    cell on a 0.2 mm dielectric: tens of thousands of nodes spent on the thickness
    alone, and a run that did not finish in minutes. The strip edge is what needs
    resolving, so the step now comes from the IN-PLANE geometry and the copper only
    guarantees a few cells across itself.
    """
    fine = min(w_mm, s_mm, h_mm) / fine_div
    if t_mm > 0:
        fine = min(fine, t_mm / 4.0)
    return max(fine, 1e-6)

def solve_pair(kind, w_mm, s_mm, h_mm, t_mm, er, levels=(1.0, 0.6), fine_div=12.0):
    """Solve the cross-section at two mesh densities; return both and the drift."""
    fine0 = _fine_step(w_mm, s_mm, h_mm, t_mm, fine_div)
    runs = []
    for factor in levels:
        fine = fine0 * factor
        cd, sec = _cap(kind, w_mm, s_mm, h_mm, t_mm, er, fine, odd=True)
        ca, _ = _cap(kind, w_mm, s_mm, h_mm, t_mm, 1.0, fine, odd=True,
                     far_x=sec.xs[-1] / h_mm, far_y=(sec.ys[-1] - h_mm) / h_mm
                     if kind == "microstrip" else None)
        runs.append({"fine_mm": fine, "c_odd_dielectric": cd, "c_odd_air": ca,
                     "z_diff": _z_from_caps(cd, ca, True),
                     "nodes": sec.nx * sec.ny, "sweeps": sec.sweeps,
                     "residual": sec.last_delta})
    main = runs[0]["z_diff"]
    if len(runs) > 1:
        fine_ratio = runs[0]["fine_mm"] / runs[1]["fine_mm"]
        # first-order Richardson: the error is halved-per-refinement only
        # approximately, so the drift is reported as-is, not silently applied
        drift = (runs[1]["z_diff"] - main) / (fine_ratio - 1.0)
    else:
        drift = None
    return main, drift, runs


def z_diff(kind, w_mm, s_mm, h_mm, t_mm, er, levels=(1.0, 0.6)):
    return solve_pair(kind, w_mm, s_mm, h_mm, t_mm, er, levels=levels)


# ------------------------------------------------------------------ the tool
def field_impedance(width_mm, spacing_mm, dielectric_mm, copper_um=35.0, er=4.3,
                    kind="microstrip", levels=2):
    """Differential impedance from a 2-D electrostatic field solution."""
    kind = str(kind or "").strip().lower()
    if kind not in KINDS:
        return err("kind must be microstrip or stripline", "ARG_INVALID")
    try:
        w = float(width_mm)
        s = float(spacing_mm)
        h = float(dielectric_mm)
        t = float(copper_um) / 1000.0
        er = float(er)
    except (TypeError, ValueError):
        return err("numeric arguments required", "ARG_INVALID")
    if w <= 0 or s <= 0 or h <= 0 or t < 0 or er < 1.0:
        return err("width, spacing and dielectric must be > 0; copper_um >= 0; er >= 1",
                   "ARG_RANGE")
    if s / h < 0.05:
        return err("spacing below 0.05*h: the half-domain symmetry plane is closer to the "
                   "trace than one mesh cell can resolve", "OUT_OF_VALIDITY")
    spec = (1.0, 0.6) if int(levels or 2) > 1 else (1.0,)
    z, drift, runs = solve_pair(kind, w, s, h, t, er, levels=spec)

    # the closed form the rest of the server used, on the same geometry: the
    # whole point of the solver is that this number is now checkable
    from . import diffpair as _dp
    closed = _dp.diff_impedance(w, s, h, copper_um=float(copper_um), er=er, kind=kind)
    closed_z = closed.get("value") if closed.get("status") == "ok" else None
    gap_pct = None if not closed_z else 100.0 * (closed_z - z) / z

    extra = {
        "mesh": runs,
        "mesh_drift_ohm": None if drift is None else round(drift, 3),
        "closed_form_ohm": closed_z,
        "closed_form_minus_solver_pct": None if gap_pct is None else round(gap_pct, 2),
        "validated_against": VALIDATION,
        "method_note": "the drift between the two meshes is self-consistency, not a "
                       "total error bound: it cannot see the TEM assumption, and on "
                       "narrow strips it stays small while the real error does not",
    }
    w_over_h = w / h
    if w_over_h < 0.5:
        extra["validity_note"] = (
            "w/h = %.2f is a narrow strip, the solver's weakest case: measured +1.2%% "
            "high on the zero-thickness stripline at the default mesh (the strip-edge "
            "singularity is under-resolved, and the bias grows as the strip narrows). "
            "Raise levels if the answer decides a stackup" % w_over_h)
    if kind == "stripline" and s / h < 2.0:
        extra["reference_note"] = (
            "s/h = %.2f is close enough that the two traces couple: there is no "
            "independent exact answer in this server's validation table for the coupled "
            "case, so this number is the solver's own opinion and not a checked one"
            % (s / h))
    if t <= 0:
        extra["zero_thickness_note"] = (
            "copper_um=0: the copper is a line with no thickness. Real foil adds "
            "capacitance at the edges and this solver does not model the etch trapezoid "
            "either way")

    return ok(
        value=round(z, 2),
        unit="ohm",
        model="field2d_finite_volume_laplace",
        basis=("2-D quasi-TEM electrostatic solution of div(eps*grad V) = 0 in the actual "
               "cross-section, finite-volume stencil on a graded tensor grid, red-black "
               "SOR; Z = 1/(c0*sqrt(C_dielectric*C_air)), half-domain odd mode (Zdiff = "
               "2*Z_odd)"),
        precision=("measured against answers it shares no approximation with, and the "
                   "table is re-checked by tests/test_field2d.py on every run: within "
                   "%.1f%% of the exact conformal stripline (w/b 0.5..4, uncoupled), "
                   "within %.1f%% of Hammerstad on isolated microstrip, and within %.1f%% "
                   "of the same exact answer on the coupled path at s/h=8. The coupled "
                   "case at tight gaps is NOT covered by an independent reference. Still "
                   "a 2-D quasi-TEM model: solder mask, etch trapezoid, copper loss and "
                   "dispersion are not in it"
                   % (max(abs(v) for v in VALIDATION["stripline_vs_conformal"]
                          ["error_pct_by_w_over_b"].values()),
                      max(abs(v) for v in VALIDATION["microstrip_vs_hammerstad"]
                          ["error_pct"].values()),
                      max(abs(v) for v in VALIDATION["stripline_pair_far_gap"]
                          ["error_pct_by_w_over_b"].values()))),
        assumptions={
            "width_mm": w, "spacing_mm": s, "dielectric_mm": h,
            "copper_um": float(copper_um), "er": er, "kind": kind,
            "tem_assumption": True,
            "solder_mask_ignored": True,
            "conductor_loss_ignored": True,
            "etch_trapezoid_ignored": True,
            "outer_boundary_distance_h": FAR_X,
        },
        extra=extra,
    )

# ------------------------------------------------------------------ the tool
def coplanar_field(width_mm, gap_mm, dielectric_mm, copper_um=0.0, er=4.3,
                   kind="cpw", levels=2, max_nodes=40000):
    """Coplanar (cpw / gcpw) impedance from an actual 2-D field solution.

    Same solver, same grid machinery and the same envelope as `field_impedance`;
    what changes is the cross-section - one centre strip with a ground plane on
    each side, air or a ground plane below, and a FULL domain because the
    conformal map this is checked against assumes a mirror at x = 0.
    """
    kind = str(kind or "").strip().lower()
    if kind not in COPLANAR_KINDS:
        return err("kind must be cpw or gcpw", "ARG_INVALID",
                   hint="cpw = unbacked (air below the substrate), "
                        "gcpw = grounded coplanar (ground plane below)")
    try:
        w = float(width_mm)
        g = float(gap_mm)
        h = float(dielectric_mm)
        t = float(copper_um or 0.0) / 1000.0
        er = float(er)
    except (TypeError, ValueError):
        return err("numeric arguments required", "ARG_INVALID")
    if w <= 0 or g <= 0 or h <= 0 or t < 0 or er < 1.0:
        return err("width, gap and dielectric must be > 0; copper_um >= 0; er >= 1",
                   "ARG_RANGE")
    spec = (1.0, 0.65) if int(levels or 2) > 1 else (1.0,)
    try:
        z, drift, runs = coplanar_solve_pair(kind, w, g, h, t, er, levels=spec,
                                            max_nodes=max_nodes)
    except ValueError as exc:
        return err(str(exc), "OUT_OF_VALIDITY",
                   hint="lower fine_div, solve with copper_um = 0, or raise "
                        "max_nodes; the coplanar mesh has no symmetry plane so it "
                        "costs twice the nodes of a pair section")

    from . import feedline as _fl
    closed_z = _fl._cpw(w, g, h, t, er, kind == "gcpw")[0]
    gap_pct = None if not closed_z else 100.0 * (closed_z - z) / z

    extra = {
        "kind": kind,
        "eps_eff": round(runs[0]["eps_eff"], 5),
        "mesh": runs,
        "mesh_drift_ohm": None if drift is None else round(drift, 4),
        "closed_form_ohm": round(closed_z, 3) if closed_z else None,
        "closed_form_minus_solver_pct": None if gap_pct is None else round(gap_pct, 2),
        "slot_mm": g,
        "gap_over_substrate": round(g / h, 3),
        "validated_against": VALIDATION["coplanar_vs_conformal"],
        "method_note": "the drift between the two meshes is self-consistency, not a "
                       "total error bound; the number that IS independent is "
                       "closed_form_minus_solver_pct, and the coplanar case is the "
                       "one where the solver reads LOW of the closed form every time",
    }
    if t > 0:
        extra["thickness_note"] = (
            "copper_um = %.1f was meshed, not modelled: the coplanar section has no "
            "thickness term, the solver just sees a rectangle. It is also the "
            "expensive way to ask - the fine step is capped at copper_um/4, so a "
            "35 um foil multiplies the node count by roughly ten and the sweep count "
            "by five. Solve at copper_um = 0 for the geometry, and read the "
            "thickness slack off `feedline_impedance`'s note" % (t * 1000.0))
    if g / h < 0.2:
        extra["validity_note"] = (
            "gap/dielectric = %.3f: the slot is narrow compared with the substrate, "
            "which is where the coplanar agreement is worst (measured -3.4 %% at "
            "g/h = 0.1). Raise levels if the answer decides a stackup" % (g / h))

    return ok(
        value=round(z, 2),
        unit="ohm",
        model="field2d_finite_volume_laplace",
        basis=("2-D quasi-TEM electrostatic solution of div(eps*grad V) = 0 in the "
               "coplanar cross-section (centre strip, two coplanar grounds, substrate "
               "of finite thickness, air or a ground plane below), finite-volume "
               "stencil on a graded tensor grid, red-black SOR; Z0 = eta0/"
               "sqrt(C_dielectric*C_air) with both C in eps0 units, full domain, no "
               "symmetry plane"),
        precision=("measured against the quasi-static conformal map over seven "
                   "geometries (w 0.2-1.0 mm, g 0.15-0.5 mm, h 0.2-2.0 mm, er 2.2-4.3): "
                   "the solver reads -1.19 %% to -3.39 %% below it, i.e. the map is "
                   "the optimistic end and the gap widens as the slot narrows "
                   "relative to the substrate. The mesh drift reported alongside is "
                   "0.006-0.098 ohm, which is NOT the error: it is only "
                   "self-consistency. Still a 2-D quasi-TEM model - solder mask, etch "
                   "trapezoid, dispersion and radiation are not in it, and the "
                   "thickness term that a real foil adds is meshed but not modelled"),
        assumptions={
            "width_mm": w, "gap_mm": g, "dielectric_mm": h,
            "copper_um": float(copper_um), "er": er, "kind": kind,
            "backside_ground": (kind == "gcpw"),
            "tem_assumption": True,
            "solder_mask_ignored": True,
            "conductor_loss_ignored": True,
            "etch_trapezoid_ignored": True,
            "outer_boundary_note": "8*(width+2*gap) from the slot outwards",
        },
        extra=extra,
    )
