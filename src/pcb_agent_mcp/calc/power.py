"""Current, resistance and PDN: the electrical side of "will this board work".

Everything here is steady-state or small-signal. The one thing that makes these
answers worth having is that they are built from the *geometry the board
actually carries* (see `oracle.board_ir_drop`) or from numbers the caller states
- never from a default that looks reasonable.

Units are fixed and stated: mm, um, A, V, ohm, K, W. Copper is 1.724e-8 ohm*m at
20 C with alpha = 0.00393 /K; the temperature you pass is used, not assumed.
"""
import math

from .result import ok, err
from .cavity import COPPER_SIGMA
from .roughness import roughness_factor, skin_depth_m

RHO_CU_20 = 1.724e-8      # ohm*m, annealed copper (IACS 100%)
ALPHA_CU = 0.00393        # 1/K, 0..100 C linear region
K_CU = 385.0              # W/(m*K) thermal conductivity
K_FR4 = 0.30              # W/(m*K) typical, and it is a RANGE (0.2..0.4)


def _sig(x, digits=6):
    """Six significant digits - the envelope's own reporting convention."""
    if x == 0 or x != x:
        return x
    return float("%.*e" % (digits - 1, x))


def _rho(temp_c):
    return RHO_CU_20 * (1.0 + ALPHA_CU * (float(temp_c) - 20.0))


# ------------------------------------------------------------------ conductors

def copper_resistance(length_mm, width_mm, thickness_um, temp_c=20.0,
                      layers=1):
    """Resistance of a rectangular copper bar (one trace, one layer).

    `layers` > 1 means the same trace is drawn on several layers and stitched -
    only correct when it really is stitched at both ends; the answer says so.
    """
    for name, v in (("length_mm", length_mm), ("width_mm", width_mm),
                    ("thickness_um", thickness_um)):
        try:
            if float(v) <= 0:
                return err("%s must be > 0" % name, "ARG_RANGE")
        except (TypeError, ValueError):
            return err("%s must be a number" % name, "ARG_INVALID")
    L = float(length_mm) * 1e-3
    w = float(width_mm) * 1e-3
    t = float(thickness_um) * 1e-6 * max(1, int(layers))
    rho = _rho(temp_c)
    r = rho * L / (w * t)
    return ok(value={"resistance_ohm": round(r, 9),
                     "sheet_resistance_ohm_per_sq": round(rho / t, 9),
                     "squares": round(L / w, 6),
                     "rho_ohm_m": round(rho, 12),
                     "parallel_layers": int(layers)},
              unit="ohm", model="rectangular_bar",
              basis="R = rho(T) * L / (w * t); rho(T) = rho20 * (1 + alpha*(T-20)), "
                    "rho20 = 1.724e-8 ohm*m, alpha = 0.00393 /K (IACS copper)",
              precision="exact for the geometry given; the ERROR is the geometry: "
                        "etch undercut and plating thickness variation are not "
                        "modelled, and a 35 um foil is typically 30..38 um after "
                        "etching. Also no skin effect - this is DC/low-frequency R",
              assumptions={"temp_c": float(temp_c), "layers": int(layers)},
              extra={"not_computed": ["skin effect / AC resistance",
                                      "etch undercut", "solder / HASL contribution",
                                      "contact resistance at the pad"]})


def via_resistance(drill_mm, plating_um, length_mm, temp_c=20.0, count=1):
    """Barrel resistance of a plated through via (or `count` of them in parallel)."""
    try:
        d = float(drill_mm) * 1e-3
        t = float(plating_um) * 1e-6
        L = float(length_mm) * 1e-3
    except (TypeError, ValueError):
        return err("drill_mm, plating_um and length_mm must be numbers", "ARG_INVALID")
    if d <= 0 or t <= 0 or L <= 0:
        return err("drill, plating and length must be > 0", "ARG_RANGE")
    if 2 * t >= d:
        return err("plating %.1f um closes a %.3f mm drill" % (plating_um, drill_mm),
                   "ARG_RANGE")
    a = math.pi * ((d / 2.0) ** 2 - (d / 2.0 - t) ** 2)
    rho = _rho(temp_c)
    one = rho * L / a
    n = max(1, int(count))
    return ok(value={"resistance_ohm": round(one / n, 9),
                     "single_via_ohm": round(one, 9),
                     "count": n,
                     "barrel_area_mm2": round(a * 1e6, 6),
                     "copper_length_mm": round(L * 1e3, 4),
                     "current_share_a_per_via": None},
              unit="ohm", model="plated_barrel",
              basis="R = rho(T) * L / A, A = pi*((d/2)^2 - (d/2 - t)^2) - the annular "
                    "copper cross-section of the barrel, no pad or dogbone",
              precision="first order. Real vias are 20..30% worse because the "
                        "plating is thinner at the middle of the barrel and the pad "
                        "transition is not a perfect node; treat the number as a "
                        "lower bound on resistance",
              assumptions={"temp_c": float(temp_c), "count": n},
              extra={"not_computed": ["non-uniform plating thickness",
                                      "pad / antipad spreading resistance",
                                      "via inductance (see power.trace_inductance)"]})


def trace_inductance(length_mm, width_mm, height_mm, temp_c=20.0):
    """Self-inductance of a straight rectangular ribbon above a plane.

    The ribbon formula (Grover) is the standard closed form for a flat bar; it
    is the right order for a PDN trace and wrong in the last 20%, which is what
    `precision` says. Returned because a decoupling answer without inductance is
    a DC answer to an AC question.
    """
    try:
        L = float(length_mm) * 1e-3
        w = float(width_mm) * 1e-3
        h = float(height_mm) * 1e-3
    except (TypeError, ValueError):
        return err("length_mm, width_mm and height_mm must be numbers", "ARG_INVALID")
    if L <= 0 or w <= 0 or h <= 0:
        return err("length, width and height must be > 0", "ARG_RANGE")
    mu0 = 4e-7 * math.pi
    # Grover's ribbon over a ground plane: partial self + image term
    self_term = mu0 * L / (2.0 * math.pi) * (math.log(2.0 * L / (w + h)) + 0.5)
    image_term = mu0 * L / (2.0 * math.pi) * math.log(1.0 + 2.0 * h / (w + h)) * 0.0
    return ok(value={"inductance_nh": round((self_term + image_term) * 1e9, 6),
                     "self_term_nh": round(self_term * 1e9, 6),
                     "per_mm_nh": round(self_term * 1e9 / (L * 1e3), 6),
                     "image_term_included": False},
              unit="nH", model="grover_ribbon",
              basis="Grover: L = mu0*L/(2*pi) * (ln(2L/(w+h)) + 0.5) for a rectangular "
                    "bar in free space; the ground-plane image term is computed as 0 "
                    "and reported as such rather than silently folded in",
              precision="order of magnitude +-20% for a straight trace; bends, vias "
                        "and plane cut-outs dominate in a real layout. Use it to rank "
                        "two layouts, not to predict a resonance",
              assumptions={"length_mm": length_mm, "width_mm": width_mm,
                           "height_mm": height_mm},
              extra={"not_computed": ["mutual inductance between parallel traces",
                                      "via inductance", "plane spreading inductance"]})


# ------------------------------------------------------------------ network solve

def _solve_linear(A, b):
    n = len(b)
    M = [list(map(float, A[i])) + [float(b[i])] for i in range(n)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(M[r][col]))
        if abs(M[piv][col]) < 1e-300:
            raise ZeroDivisionError("singular at %d" % col)
        if piv != col:
            M[col], M[piv] = M[piv], M[col]
        pv = M[col][col]
        for r in range(col + 1, n):
            f = M[r][col] / pv
            if not f:
                continue
            for c in range(col, n + 1):
                M[r][c] -= f * M[col][c]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        s = M[r][n]
        for c in range(r + 1, n):
            s -= M[r][c] * x[c]
        x[r] = s / M[r][r]
    return x


def resistive_network(nodes, edges, injections, ground):
    """Solve the DC network: G*V = I on a graph of resistors.

    nodes: list of names
    edges: list of {"a": node, "b": node, "resistance_ohm": r}
    injections: {node: amps} - positive means current ENTERS the network there
    ground: the node held at 0 V (one is required; without it the answer is only
            defined up to a constant, which is the single most common way to get
            a plausible wrong number out of a network solver)

    Returns voltages, per-edge currents and powers.
    """
    names = list(nodes)
    index = {n: i for i, n in enumerate(names)}
    if ground not in index:
        return None, "ground node %r is not in the node list" % (ground,)
    if ground not in injections:
        injections = dict(injections)
    n = len(names)
    A = [[0.0] * n for _ in range(n)]
    b = [0.0] * n
    for e in edges:
        if e["a"] not in index or e["b"] not in index:
            return None, "edge references an unknown node"
        ia, ib = index[e["a"]], index[e["b"]]
        g = 1.0 / e["resistance_ohm"]
        A[ia][ia] += g
        A[ib][ib] += g
        A[ia][ib] -= g
        A[ib][ia] -= g
    for k, amps in injections.items():
        if k in index:
            b[index[k]] += float(amps)
    # the ground node is the reference: replace its row with V = 0
    gi = index[ground]
    A[gi] = [0.0] * n
    A[gi][gi] = 1.0
    b[gi] = 0.0
    try:
        v = _solve_linear(A, b)
    except ZeroDivisionError as exc:
        return None, "singular: %s (a node with no conductive path to ground?)" % exc
    volts = {names[i]: v[i] for i in range(n)}
    cur = []
    for e in edges:
        dv = volts[e["a"]] - volts[e["b"]]
        i = dv / e["resistance_ohm"]
        cur.append({"a": e["a"], "b": e["b"], "resistance_ohm": e["resistance_ohm"],
                    "current_a": i, "power_w": dv * i,
                    "drop_v": dv})
    # KCL check: the sum of what leaves through the ground node must equal what
    # was injected, otherwise the mesh was built wrong
    total_in = sum(float(x) for x in injections.values() if float(x) > 0)
    total_out = sum(float(x) for x in injections.values() if float(x) < 0)
    return {"voltages_v": volts, "edges": cur,
            "injected_a": total_in, "drawn_a": -total_out,
            "net_a": total_in + total_out}, None


def _ic0_factor(n, diag, adju):
    """Incomplete Cholesky, zero fill-in, on the same sparsity pattern as A.

    Jacobi preconditioning is not enough on a copper sheet: the matrix mixes
    1e-3 ohm sheet squares with 1e-6 ohm contact links, and CG then needs
    thousands of iterations because the eigenvalues span eight decades. IC(0)
    keeps the pattern of A (about five entries a row on a grid) and cuts that to
    a few dozen iterations. It can break down - a negative pivot - on an
    indefinite or badly scaled matrix, and returns None so the caller can fall
    back instead of returning a wrong answer.
    """
    rows = []
    for i in range(n):
        rows.append(sorted(j for j, _g in adju[i] if j < i))
    Ld = [0.0] * n
    Lv = [None] * n
    cols = [[] for _ in range(n)]
    for i in range(n):
        ri = rows[i]
        gd = {}
        for jj, gg in adju[i]:
            gd[jj] = gg
        row = {}
        for j in ri:
            rj = rows[j]
            acc = 0.0
            lj = Lv[j]
            a = 0
            b = 0
            while a < len(ri) and b < len(rj):
                ka = ri[a]
                kb = rj[b]
                if ka == kb:
                    if ka < j:
                        va = row.get(ka)
                        if va is not None:
                            acc += va * lj.get(ka, 0.0)
                    a += 1
                    b += 1
                elif ka < kb:
                    a += 1
                else:
                    b += 1
            v = (-gd.get(j, 0.0) - acc) / Ld[j]
            row[j] = v
            cols[j].append((i, v))
        d = diag[i]
        for v in row.values():
            d -= v * v
        if d <= 0.0:
            return None
        Ld[i] = math.sqrt(d)
        Lv[i] = row
    return Ld, Lv, cols

def _ic0_solve(Ld, Lv, cols, r):
    n = len(Ld)
    y = [0.0] * n
    for i in range(n):
        s = r[i]
        for j, v in Lv[i].items():
            s -= v * y[j]
        y[i] = s / Ld[i]
    z = [0.0] * n
    for i in range(n - 1, -1, -1):
        s = y[i]
        for k, v in cols[i]:
            s -= v * z[k]
        z[i] = s / Ld[i]
    return z

def resistive_network_cg(nodes, edges, injections, ground, tol=1e-10,
                         max_iter=20000, precond="auto"):
    """Same answer as `resistive_network`, solved without forming the matrix.

    The dense Kirchhoff solve above is O(n^3) and allocates an n x n list of
    lists, which caps it at a few hundred nodes. A copper pour gridded at half a
    millimetre is tens of thousands of nodes, so the same system is solved here
    with Jacobi-preconditioned conjugate gradients on the ground-eliminated
    Laplacian (symmetric positive definite, which is the only case CG is valid
    for - and this network always is one).

    The residual is returned, not hidden: `residual_rel` is
    ||b - A x|| / ||b|| and `converged` says whether it made `tol`. A caller that
    reports a number out of a non-converged solve is reporting noise, so it must
    check `converged` itself.
    """
    names = list(nodes)
    index = {n: i for i, n in enumerate(names)}
    if ground not in index:
        return None, "ground node %r is not in the node list" % (ground,)
    n = len(names)
    gi = index[ground]
    diag = [0.0] * n
    pair_a = [0] * len(edges)
    pair_b = [0] * len(edges)
    pair_g = [0.0] * len(edges)
    adj = [[] for _ in range(n)]
    for k, e in enumerate(edges):
        ia = index.get(e["a"])
        ib = index.get(e["b"])
        if ia is None or ib is None:
            return None, "edge references an unknown node"
        r = e["resistance_ohm"]
        if not (r > 0.0):
            return None, "an edge has resistance %r" % (r,)
        g = 1.0 / r
        pair_a[k] = ia
        pair_b[k] = ib
        pair_g[k] = g
        diag[ia] += g
        diag[ib] += g
        adj[ia].append((ib, g))
        adj[ib].append((ia, g))
    # Parallel edges are legitimate (a pad is welded to forty pour cells, two
    # pours overlap) but they MUST be folded into one conductance first: the
    # incomplete-Cholesky pattern below assumes one entry per (i, j), and a
    # duplicated entry silently double-counted in the back substitution and cost
    # CG ten times the iterations it needed.
    for i in range(n):
        if len(adj[i]) > 1:
            fold = {}
            for j, g in adj[i]:
                fold[j] = fold.get(j, 0.0) + g
            adj[i] = list(fold.items())
    # unknown ordering: everything but the ground node, order preserved
    umap = {}
    unodes = []
    for i in range(n):
        if i == gi:
            continue
        umap[i] = len(unodes)
        unodes.append(i)
    N = len(unodes)
    for i in unodes:
        if diag[i] <= 0.0:
            return None, ("node %r has no conductive path to ground (it carries "
                          "no edge)" % (names[i],))
    b = [0.0] * N
    for k, amps in injections.items():
        i = index.get(k)
        if i is None or i == gi:
            continue
        b[umap[i]] += float(amps)
    # neighbour lists with the ground mapped out (its voltage is 0 and it never
    # appears as an unknown, so its contribution is already inside the diagonal)
    adju = [None] * N
    diagu = [0.0] * N
    for u, i in enumerate(unodes):
        diagu[u] = diag[i]
        lst = []
        for j, g in adj[i]:
            if j == gi:
                continue
            lst.append((umap[j], g))
        adju[u] = lst

    def matvec(x):
        y = [0.0] * N
        for u in range(N):
            s = diagu[u] * x[u]
            for v, g in adju[u]:
                s -= g * x[v]
            y[u] = s
        return y

    def dot(p, q):
        s = 0.0
        for u in range(N):
            s += p[u] * q[u]
        return s

    ic = None
    if precond in ("auto", "ic0"):
        ic = _ic0_factor(N, diagu, adju)
    precond_used = "jacobi"
    if ic is not None:
        Ld, Lv, Lcol = ic
        precond_used = "ic0"

    def zin(rr):
        if ic is not None:
            return _ic0_solve(Ld, Lv, Lcol, rr)
        return [rr[u] / diagu[u] for u in range(N)]

    x = [0.0] * N
    r = list(b)
    nb = math.sqrt(dot(b, b))
    if nb == 0.0:
        v = [0.0] * n
        return _cg_pack(names, n, pair_a, pair_b, pair_g, v, injections, 0, 0.0,
                        True, precond_used), None
    z = zin(r)
    p = list(z)
    rz = dot(r, z)
    it = 0
    rel = 1.0
    for it in range(1, max_iter + 1):
        Ap = matvec(p)
        pAp = dot(p, Ap)
        if pAp <= 0.0:
            break
        alpha = rz / pAp
        for u in range(N):
            x[u] += alpha * p[u]
            r[u] -= alpha * Ap[u]
        rel = math.sqrt(dot(r, r)) / nb
        if rel <= tol:
            break
        z = zin(r)
        rz_new = dot(r, z)
        if rz == 0.0:
            break
        beta = rz_new / rz
        rz = rz_new
        for u in range(N):
            p[u] = z[u] + beta * p[u]
    v = [0.0] * n
    for u, i in enumerate(unodes):
        v[i] = x[u]
    v[gi] = 0.0
    return _cg_pack(names, n, pair_a, pair_b, pair_g, v, injections, it, rel,
                    rel <= tol, precond_used), None

def _cg_pack(names, n, pair_a, pair_b, pair_g, volts_list, injections,
             iterations, residual_rel, converged, precond_used="jacobi"):
    volts = {names[i]: volts_list[i] for i in range(n)}
    cur = []
    for k in range(len(pair_g)):
        ia = pair_a[k]
        ib = pair_b[k]
        dv = volts_list[ia] - volts_list[ib]
        i = dv * pair_g[k]
        cur.append({"a": names[ia], "b": names[ib],
                    "resistance_ohm": 1.0 / pair_g[k],
                    "current_a": i, "power_w": dv * i, "drop_v": dv})
    total_in = sum(float(x) for x in injections.values() if float(x) > 0)
    total_out = sum(float(x) for x in injections.values() if float(x) < 0)
    return {"voltages_v": volts, "edges": cur, "injected_a": total_in,
            "drawn_a": -total_out, "net_a": total_in + total_out,
            "iterations": iterations, "residual_rel": residual_rel,
            "converged": converged, "precond": precond_used}

def ir_drop(segments, current_a, temp_c=20.0):
    """Series IR drop along an ordered path of copper segments.

    The simple case that is nevertheless the one an AI gets wrong: a chain of
    three segments of different width is not one segment of the average width.
    """
    if not segments:
        return err("segments is empty", "ARG_REQUIRED",
                   hint="each segment needs length_mm, width_mm, thickness_um")
    total = 0.0
    detail = []
    for k, seg in enumerate(segments):
        try:
            L = float(seg["length_mm"])
            w = float(seg["width_mm"])
            t = float(seg["thickness_um"])
        except (KeyError, TypeError, ValueError):
            return err("segment %d needs numeric length_mm, width_mm, thickness_um" % k,
                       "ARG_INVALID")
        if L <= 0 or w <= 0 or t <= 0:
            return err("segment %d has a non-positive dimension" % k, "ARG_RANGE")
        r = _rho(temp_c) * (L * 1e-3) / (w * 1e-3 * t * 1e-6)
        total += r
        detail.append({"index": k, "resistance_ohm": round(r, 9),
                       "drop_v": round(r * float(current_a), 9),
                       "power_w": round(r * float(current_a) ** 2, 9)})
    i = float(current_a)
    return ok(value={"total_resistance_ohm": round(total, 9),
                     "total_drop_v": round(total * i, 9),
                     "total_power_w": round(total * i * i, 9),
                     "segments": detail,
                     "current_a": i},
              unit="V", model="series_resistive_chain",
              basis="R = sum over segments of rho(T)*L/(w*t); V = I*R",
              precision="exact for the chain given, and a LOWER bound on the real "
                        "drop: spreading resistance under the pads, via barrels and "
                        "the source's own output impedance are not in the chain",
              assumptions={"temp_c": float(temp_c), "segments": len(detail)},
              extra={"not_computed": ["via barrels (add them as segments)",
                                      "pad / plane spreading resistance",
                                      "temperature rise feedback into rho"]})


# ------------------------------------------------------------------ PDN / decoupling

def cap_impedance(freq_hz, c_farad, esr_ohm=0.0, esl_h=0.0):
    """|Z| of one real capacitor (series R-L-C)."""
    if c_farad <= 0 or freq_hz < 0:
        return None
    w = 2.0 * math.pi * freq_hz
    if w == 0:
        return None
    z = complex(esr_ohm, w * esl_h - 1.0 / (w * c_farad))
    return z


def decoupling_self_resonance(c_farad, esl_h, esr_ohm=0.0):
    """Self-resonant frequency of one capacitor and its impedance there (= ESR)."""
    if c_farad <= 0 or esl_h <= 0:
        return err("capacitance and ESL must be > 0", "ARG_RANGE",
                   hint="a capacitor with no ESL has no self resonance, so this "
                        "question has no answer; pass the package ESL")
    f0 = 1.0 / (2.0 * math.pi * math.sqrt(c_farad * esl_h))
    zmin = float(esr_ohm)
    return ok(value={"self_resonant_hz": round(f0, 3),
                     "impedance_at_resonance_ohm": round(zmin, 9),
                     "above_resonance_inductive": True,
                     "esl_h": float(esl_h)},
              unit="Hz", model="series_rlc",
              basis="f0 = 1/(2*pi*sqrt(L*C)); at f0 the reactances cancel and only ESR "
                    "remains",
              precision="exact for the R-L-C model. The model is the limit: a real "
                        "0402 has a distributed resonance and a second parasitic "
                        "above it, and the mounting inductance (vias, plane spread) is "
                        "usually LARGER than the package ESL - so the effective f0 on "
                        "a board is lower than this number",
              assumptions={"c_farad": c_farad, "esl_h": esl_h, "esr_ohm": esr_ohm},
              extra={"not_computed": ["mounting/via inductance (add it to esl_h)",
                                      "dielectric absorption", "DC bias derating"]})


MU0 = 4e-7 * math.pi
EPS0 = 8.8541878128e-12
C0 = 299792458.0

_PLANE_NOT_COMPUTED = (
    "the plane impedance CURVE, from THIS function. Each row here is one "
    "single-mode peak; the tails of the other modes are not summed. For the "
    "curve use pdn_plane_impedance, which sums the whole series with a finite "
    "pad and refuses to answer until the truncation order has stopped moving",
    "via barrel / antipad inductance and the capacitance of the gaps around them",
    "split planes, moats and return-path discontinuities",
    "copper loss and radiation: the only damping here is the loss tangent the "
    "caller gives",
    "open-edge detuning of the mode frequencies (a real plane rings a few percent "
    "LOW of the PMC value)",
)


def pdn_plane_modes(width_mm, height_mm, er, thickness_mm, tan_delta,
                    probe_mm=None, port_mm=None, band_hz=None, max_order=6):
    """Peak |Z| at each parallel-plate mode, from the one mode that rings.

    A plane pair is a cavity; at f_mn the "plane" is a short in the transfer
    impedance sense, i.e. |Z| peaks. The single-mode term of the PMC Green's
    function

        Z(f) = j*w*mu0*t * sum_mn  psi_mn(r) psi_mn(r') / (k_mn^2 - k^2(1 - j/Q))

    evaluated AT resonance is what this returns:

        |Z_peak| = w*mu0*t*psi_mn(r)*psi_mn(r')*Q / k_mn^2,   Q = 1/tan_delta

    with psi_mn = c_m c_n / sqrt(a b) * cos(m pi x/a) cos(n pi y/b) and
    c_i = 1 for i = 0 else sqrt(2). The same expansion, summed, reproduces
    1/(j*w*C_plane) to 0.02 % at 1 MHz - that is the check that the constants are
    right; see tests/test_pdn_plane.py, which does exactly that.

    Only the peaks are reported, and each peak is the single-mode estimate. The
    sum over the OTHER modes is not computed here - see not_computed, and note
    that the summed CURVE now exists as pdn_plane_impedance (a finite pad is
    what gives that sum a limit) - so do not read two adjacent numbers here as
    a curve.

    tan_delta is required: with no loss the peak is infinite and "R ohm" would be
    a made-up number. Q = 1/tan_delta is the ONLY damping in the model, so the
    peak scales straight with the loss tangent you state - FR4 is 0.015..0.025.
    """
    def num(x):
        try:
            v = float(x)
        except (TypeError, ValueError):
            return None
        return v if v == v else None

    a = num(width_mm)
    b = num(height_mm)
    e = num(er)
    t = num(thickness_mm)
    td = num(tan_delta)
    for name, v in (("width_mm", a), ("height_mm", b), ("er", e),
                    ("thickness_mm", t)):
        if v is None:
            return err("%s is required and must be a number" % name, "ARG_REQUIRED")
        if v <= 0:
            return err("%s must be > 0" % name, "ARG_RANGE",
                       extra={"offending": name})
    if e < 1.0:
        return err("er must be >= 1", "ARG_RANGE", extra={"er": e})
    if td is None:
        return err("tan_delta is required: with no loss the peak impedance at a "
                   "plane mode is infinite, so there would be no number to report",
                   "ARG_REQUIRED",
                   hint="FR4 is 0.015..0.025, and Q = 1/tan_delta is the only "
                        "damping in this model, so the peak scales with what you "
                        "state")
    if td <= 0.0 or td >= 1.0:
        return err("tan_delta must be in (0, 1)", "ARG_RANGE",
                   extra={"tan_delta": td})
    try:
        order = int(max_order)
    except (TypeError, ValueError):
        return err("max_order must be an integer", "ARG_INVALID")
    if order < 1 or order > 20:
        return err("max_order must be in 1..20", "ARG_RANGE")

    def point(p, name, default=None):
        if p is None:
            return None
        if isinstance(p, dict):
            x, y = num(p.get("x_mm")), num(p.get("y_mm"))
        else:
            try:
                x, y = num(p[0]), num(p[1])
            except (TypeError, IndexError):
                return "bad"
        if x is None or y is None:
            return "bad"
        if x < 0 or y < 0 or x > a or y > b:
            return "out"
        return (x, y)

    r1 = point(port_mm, "port_mm")
    if r1 in ("bad", "out"):
        return err("port_mm must be [x_mm, y_mm] inside the plane", "ARG_RANGE")
    r2 = point(probe_mm, "probe_mm")
    if r2 in ("bad", "out"):
        return err("probe_mm must be [x_mm, y_mm] inside the plane", "ARG_RANGE")
    if r1 is None:
        r1 = (0.5 * a, 0.5 * b)          # the middle: where a mode has its antinode
    if r2 is None:
        r2 = r1

    a_m, b_m, t_m = a * 1e-3, b * 1e-3, t * 1e-3
    q = 1.0 / td
    # f = c/(2*sqrt(er)) * sqrt((m/a)^2 + (n/b)^2) = c * k_mn / (2*pi*sqrt(er)),
    # with k_mn = pi*sqrt((m/a)^2+(n/b)^2) the transverse wavenumber in rad/m
    k_plane = C0 / math.sqrt(e)

    rows = []
    unexcited = 0
    for m in range(0, order + 1):
        for n in range(0, order + 1):
            if m == 0 and n == 0:
                continue
            kmn2 = (math.pi * m / a_m) ** 2 + (math.pi * n / b_m) ** 2
            k_mn = math.sqrt(kmn2)
            f = k_plane * k_mn / (2.0 * math.pi)
            cm = 1.0 if m == 0 else math.sqrt(2.0)
            cn = 1.0 if n == 0 else math.sqrt(2.0)
            # psi_mn(r1) * psi_mn(r2) with the normalised eigenfunction
            # psi_mn = c_m c_n / sqrt(a b) * cos(m pi x/a) cos(n pi y/b)
            psi = ((cm * cn) ** 2 / (a_m * b_m)
                   * math.cos(math.pi * m * r1[0] / a) * math.cos(math.pi * n * r1[1] / b)
                   * math.cos(math.pi * m * r2[0] / a) * math.cos(math.pi * n * r2[1] / b))
            w = 2.0 * math.pi * f
            # a null at either point is exact (cos of 90 deg), so test the coupling
            # against the mode's own scale rather than against an absolute epsilon
            scale = (cm * cn) ** 2 / (a_m * b_m)
            coupled = abs(psi) > 1e-9 * scale
            z_peak = abs(w * MU0 * t_m * psi * q / kmn2) if coupled else 0.0
            row = {"m": m, "n": n, "f_hz": _sig(f), "z_peak_ohm": _sig(z_peak),
                   "coupled": coupled}
            if band_hz:
                try:
                    lo, hi = float(band_hz[0]), float(band_hz[1])
                except (TypeError, ValueError, IndexError):
                    return err("band_hz must be [f_lo_hz, f_hi_hz]", "ARG_INVALID")
                row["in_band"] = bool(lo <= f <= hi)
            if not row["coupled"]:
                unexcited += 1
                row["why_not"] = ("this mode has a voltage null at one of the "
                                  "points you gave, so it does not show up in the "
                                  "transfer impedance between them")
            rows.append(row)
    rows.sort(key=lambda r: r["f_hz"])

    # Integrity gate. The very same constants, summed, must reproduce the
    # parallel-plate capacitance far below the first mode: the (0,0) term is that
    # capacitor, so a wrong normalisation shows up here as a factor, and a factor
    # is exactly the silent error this module cannot afford.
    f_chk = rows[0]["f_hz"] / 100.0
    k_chk = 2.0 * math.pi * f_chk * math.sqrt(EPS0 * e * MU0)
    k2_chk = k_chk * k_chk * (1.0 - 1j / q)
    tot = 0j
    for m in range(0, order + 1):
        for n in range(0, order + 1):
            cm = 1.0 if m == 0 else math.sqrt(2.0)
            cn = 1.0 if n == 0 else math.sqrt(2.0)
            kmn2 = (math.pi * m / a_m) ** 2 + (math.pi * n / b_m) ** 2
            psi = ((cm * cn) ** 2 / (a_m * b_m)
                   * math.cos(math.pi * m * r1[0] / a) * math.cos(math.pi * n * r1[1] / b)
                   * math.cos(math.pi * m * r2[0] / a) * math.cos(math.pi * n * r2[1] / b))
            tot += psi / (kmn2 - k2_chk)
    z_chk = abs(2.0 * math.pi * f_chk * MU0 * t_m * tot)
    z_cap = 1.0 / (2.0 * math.pi * f_chk * EPS0 * e * a_m * b_m / t_m)
    if abs(z_chk / z_cap - 1.0) > 0.01:
        return err("the modal constants do not reproduce the plane capacitance "
                   "(%s ohm vs %s ohm at %s Hz): the peak numbers below would be "
                   "wrong by the same factor" % (z_chk, z_cap, f_chk),
                   "INTERNAL_CHECK_FAILED")

    coupled = [r for r in rows if r["coupled"]]
    worst_any = max(coupled, key=lambda r: r["z_peak_ohm"]) if coupled else None
    worst = worst_any
    if band_hz and coupled:
        # the caller asked about a band, so the band is the answer - including
        # when it is empty, because "no mode in your band" is a finding
        inband = [r for r in coupled if r.get("in_band")]
        worst = max(inband, key=lambda r: r["z_peak_ohm"]) if inband else None

    extra = {
        "mode_count": len(rows),
        "uncoupled_modes": unexcited,
        "first_coupled_hz": coupled[0]["f_hz"] if coupled else None,
        "worst_mode": worst,
        "worst_mode_any_band": worst_any,
        "not_computed": list(_PLANE_NOT_COMPUTED),
    }
    return ok({"modes": rows,
               "port_mm": [r1[0], r1[1]], "probe_mm": [r2[0], r2[1]],
               "q_from_loss_tangent": _sig(q),
               "plane_capacitance_f": _sig(EPS0 * e * a_m * b_m / t_m),
               "worst_z_peak_ohm": worst["z_peak_ohm"] if worst else None,
               "worst_z_peak_hz": worst["f_hz"] if worst else None,
               "worst_z_peak_any_band_ohm": (worst_any["z_peak_ohm"]
                                             if worst_any else None)},
              unit="ohm", model="PMC_cavity_single_mode_peak",
              basis="|Z_peak_mn| = w*mu0*t*psi_mn(r1)*psi_mn(r2)*Q/k_mn^2 at "
                    "f_mn = c/(2*sqrt(er))*sqrt((m/a)^2+(n/b)^2), Q = 1/tan_delta",
              precision="single-mode peak. The other modes' tails are NOT summed "
                        "here, so two adjacent rows are not a curve and no "
                        "anti-resonance is claimed; the summed curve is "
                        "pdn_plane_impedance (finite pad, gated on order)",
              assumptions={"er": e, "thickness_mm": t, "tan_delta": td,
                           "loss": "dielectric only - no copper, no radiation"},
              extra=extra)


# ---------------------------------------------------------------------------
# The plane impedance CURVE: the modal sum, with the aperture that makes it sum
# ---------------------------------------------------------------------------
_PLANE_CURVE_NOT_COMPUTED = (
    "die package, via barrel and antipad inductance, and the capacitance of the "
    "gap around a via - the ports here are area-averaged pads on an ideal "
    "cavity, not a via inside a real pad/antipad stack",
    "split planes, moats and every return-path discontinuity: the model is one "
    "unbroken rectangle with PMC side walls",
    "radiation Q, glass-weave Dk inhomogeneity, and the open-edge detuning of "
    "the mode frequencies (a real plane rings a few percent LOW of the PMC "
    "values used here)",
    "the capacitors and packages mounted across the planes. They are usually "
    "the DOMINANT damping of a real PDN resonance and they are not in this sum, "
    "so the Q here is an upper bound and the peaks come out too high and too "
    "narrow - this curve is the planes alone",
    "surface roughness beyond the one-number Hammerstad-Jensen multiplier: no "
    "Huray nodule model, no foil profile, no anisotropic roughness - pass "
    "roughness_um to turn the multiplier on (without it the copper is smooth)",
    "any verdict: a resonance matters only together with an excitation and a "
    "coupling, and neither is in this number",
)

# 1-D aperture factor: the pad's own average of one eigenfunction
def _ap_sinc(u):
    if u == 0.0:
        return 1.0
    return math.sin(u) / u

def _plane_q_of(f_hz, h_m, tan_delta, sigma, rq_m=None):
    """Q of one cavity mode: copper (Qc = h/delta_s) and dielectric (Qd = 1/tand).

    Returns (q_total, q_copper, q_dielectric). At f = 0, or with sigma None
    (ideal copper), there is no conductor loss: a uniform field has H = 0
    between the plates, so the (0,0) mode is damped by the dielectric only.

    rq_m is the RMS surface roughness: it multiplies Rs by the Hammerstad-Jensen
    factor K(rq/delta_s), i.e. Qc = h/(delta_s*K), and NOTHING else - the
    dielectric side is unaffected, which is the negative control the tests use
    (a roughness that leaked into Qd would change q_dielectric and fail).
    rq_m = None is smooth copper, bit for bit.
    """
    qd = 1.0 / tan_delta
    if f_hz <= 0.0 or sigma is None:
        return qd, None, qd
    delta_s = math.sqrt(1.0 / (math.pi * f_hz * MU0 * sigma))
    qc = h_m / delta_s
    if rq_m:
        qc = qc / roughness_factor(rq_m, delta_s)
    return 1.0 / (1.0 / qc + 1.0 / qd), qc, qd

def _plane_modal_z(f_hz, a_m, b_m, h_m, er, tan_delta, sigma, order, p1, p2,
                   ap_x_m=0.0, ap_y_m=0.0, ap2_x_m=None, ap2_y_m=None,
                   rq_m=None):
    """Transfer impedance between two ports, modes m,n = 0..order.

        Z = j*w*mu0*h * sum_mn  A_mn / (k_mn^2 - kap^2),   kap^2 = k^2*(1 - j/Q)
        A_mn = (c_m c_n)^2/(a b) * chi_m(x1) chi_m(x2) chi_n(y1) chi_n(y2)
               * S_x(m)^2 * S_y(n)^2
        chi_i = cos(i*pi*x/a),  S = sin(u)/u averaged over the pad,
        u = i*pi*s/(2a),  Q = Q(f) = 1/(1/Qc + 1/Qd),  Qc = h/delta_s

    Why Q is evaluated at f and not at f_mn: the loss is put into the medium
    (one complex wavenumber for the whole cavity), which is the classical lossy
    cavity and needs no per-mode bookkeeping. At resonance the two conventions
    are identical, and off resonance the difference is that a high mode gets a
    little more damping than its own frequency would give - which changes |Z|
    where that mode is not what you are looking at.

    The aperture factor S is the whole reason this converges. With a point port
    (S = 1) the tail of the double sum is 1/(m^2+n^2) and the sum has no limit -
    a point source in a 2-D sheet has a logarithmically infinite reactance.
    Averaging the eigenfunction over a pad of side s multiplies the tail by
    about (2a/(pi s m))^2 (2b/(pi s n))^2, so the sum is finite once the
    truncation passes m ~ 2a/(pi s). Physically: a pad cannot couple to a mode
    whose half-wavelength is shorter than the pad.

    ap_x_m/ap_y_m = 0 is a point port: the divergent case. It is kept because
    the negative control has to be able to run it.

    ap2_x_m/ap2_y_m is the SECOND port's own pad. Left as None it means "same
    pad as the first port", which keeps every one-port answer bit for bit. Two
    different pads matter as soon as the ports are not interchangeable: a
    decoupling cap site is a small pad, the injection point may be a big one,
    and Z_ij then carries S_i*S_j rather than S^2.
    """
    if f_hz <= 0.0:
        return None
    w = 2.0 * math.pi * f_hz
    k2 = w * w * MU0 * EPS0 * er
    q, _qc, _qd = _plane_q_of(f_hz, h_m, tan_delta, sigma, rq_m)
    kap2 = k2 * (1.0 - 1j / q)
    n_i = order + 1
    pi = math.pi
    # per-index tables: eigenfunction at each port, pad average, normalisation
    cx1 = [math.cos(pi * m * p1[0] / a_m) for m in range(n_i)]
    cx2 = [math.cos(pi * m * p2[0] / a_m) for m in range(n_i)]
    cy1 = [math.cos(pi * n * p1[1] / b_m) for n in range(n_i)]
    cy2 = [math.cos(pi * n * p2[1] / b_m) for n in range(n_i)]
    sx1 = [1.0] * n_i
    sy1 = [1.0] * n_i
    if ap2_x_m is None:
        ap2_x_m = ap_x_m
    if ap2_y_m is None:
        ap2_y_m = ap_y_m
    sx2 = [1.0] * n_i
    sy2 = [1.0] * n_i
    if ap_x_m > 0.0:
        for m in range(1, n_i):
            sx1[m] = _ap_sinc(pi * m * ap_x_m / (2.0 * a_m))
    if ap_y_m > 0.0:
        for n in range(1, n_i):
            sy1[n] = _ap_sinc(pi * n * ap_y_m / (2.0 * b_m))
    if ap2_x_m > 0.0:
        for m in range(1, n_i):
            sx2[m] = _ap_sinc(pi * m * ap2_x_m / (2.0 * a_m))
    if ap2_y_m > 0.0:
        for n in range(1, n_i):
            sy2[n] = _ap_sinc(pi * n * ap2_y_m / (2.0 * b_m))
    # ax[m] = (c_m^2/2) * S^2 * chi(x1) chi(x2), folded so that the 2/(a b)
    # of the eigenfunction normalisation comes out of the loop
    scale = 1.0 / (a_m * b_m)
    ax = [((1.0 if m == 0 else 2.0) * sx1[m] * sx2[m] * cx1[m] * cx2[m])
          for m in range(n_i)]
    ay = [((1.0 if n == 0 else 2.0) * sy1[n] * sy2[n] * cy1[n] * cy2[n])
          for n in range(n_i)]
    kx2 = [pi * m / a_m for m in range(n_i)]
    kx2 = [v * v for v in kx2]
    tot = 0j
    for m in range(n_i):
        base = kx2[m]
        am = ax[m] * scale
        if am == 0.0:
            continue
        inner = 0j
        for n in range(n_i):
            kn = pi * n / b_m
            inner += ay[n] / (base + kn * kn - kap2)
        tot += am * inner
    return 1j * w * MU0 * h_m * tot

def _plane_aperture(v, a, b):
    """port_size_mm given as one number (square) or [x, y]."""
    if v is None:
        return None, None
    if isinstance(v, (list, tuple)):
        try:
            sx, sy = float(v[0]), float(v[1])
        except (TypeError, ValueError, IndexError):
            return "bad", "bad"
    else:
        try:
            sx = sy = float(v)
        except (TypeError, ValueError):
            return "bad", "bad"
    if sx <= 0.0 or sy <= 0.0:
        return "bad", "bad"
    if sx >= min(a, b) * 0.5 or sy >= min(a, b) * 0.5:
        return "wide", "wide"
    return sx, sy

def pdn_plane_impedance(width_mm, height_mm, er, thickness_mm, tan_delta=None,
                        port_mm=None, port_size_mm=None, probe_mm=None,
                        f_start_hz=None, f_stop_hz=None, points=100,
                        freqs_hz=None, max_order=256, tolerance=0.01,
                        conductivity_s_m=COPPER_SIGMA, roughness_um=None):
    """|Z(f)| of one plane pair, as a CURVE: the modal sum, with a convergence gate.

    pdn_plane_modes reports the single-mode peaks and refuses the curve, because
    a POINT port makes the modal sum diverge (measured here on a 50x50 mm pair
    at 2 GHz: 5.7 -> 19.2 ohm between order 8 and 256, still climbing, no
    limit). The divergence is a property of the point
    source, not of the physics: a real pad has a finite size, and averaging the
    eigenfunction over that pad turns the sum into a convergent one. So the one
    argument that was missing is the pad size - and it is required here, because
    with a smaller pad the answer is a function of the truncation order and
    nothing else.

    What you get back:
      * the curve, sampled on your grid PLUS every cavity mode inside the band
        (the peaks are narrow - Q is 20..100 - so a grid alone would step over
        them; the mode frequencies are inserted, and the tool says so);
      * the peak |Z| and where it is, and the deepest DIP between modes, which
        is the number a capacitor bank has to beat;
      * the truncation order it settled on, the relative change that bought it,
        and the whole order scan - so the number can be audited rather than
        trusted.

    Damping. Two mechanisms, both from the same geometry: Qc = h/delta_s (which
    drops a, b and m,n - see plane_q) and Qd = 1/tan_delta. They add as
    reciprocals. Pass conductivity_s_m=None for ideal copper (that is also how
    the earlier point-port measurement is reproduced, which is why the negative
    control is exact rather than approximate).

    Not modelled, and these can dominate: everything in not_computed, first of
    which is the decoupling capacitors themselves - they are usually the biggest
    loss across a plane pair, so the peaks here are too high and too narrow.
    """
    def num(x):
        try:
            v = float(x)
        except (TypeError, ValueError):
            return None
        if v != v or v in (float("inf"), float("-inf")):
            return None
        return v

    a = num(width_mm)
    b = num(height_mm)
    e = num(er)
    t = num(thickness_mm)
    td = num(tan_delta)
    for name, v in (("width_mm", a), ("height_mm", b), ("er", e),
                    ("thickness_mm", t)):
        if v is None:
            return err("%s is required and must be a number" % name, "ARG_REQUIRED")
        if v <= 0.0:
            return err("%s must be > 0" % name, "ARG_RANGE", extra={"offending": name})
    if e < 1.0:
        return err("er must be >= 1", "ARG_RANGE", extra={"er": e})
    if td is None:
        return err("tan_delta is required: with no loss every mode peak is "
                   "infinite and the curve has no value at a resonance",
                   "ARG_REQUIRED",
                   hint="FR4 is 0.015..0.025. Copper loss cannot stand in for "
                        "it - Qd = 1/tan_delta is the only damping a dielectric "
                        "with no stated loss tangent has")
    if td <= 0.0 or td >= 1.0:
        return err("tan_delta must be in (0, 1)", "ARG_RANGE",
                   extra={"tan_delta": td})
    # an explicit null means ideal copper: no conductor loss. That switch is not
    # decoration - it is how the point-port measurement of pdn_plane_modes is
    # reproduced bit for bit, so the negative control is exact.
    sg = None if conductivity_s_m is None else num(conductivity_s_m)
    if sg is not None and sg <= 0.0:
        return err("conductivity_s_m must be > 0, or null for ideal copper",
                   "ARG_RANGE", extra={"conductivity_s_m": sg})

    rq = None
    if roughness_um is not None:
        rq_um = num(roughness_um)
        if rq_um is None or rq_um < 0.0:
            return err("roughness_um must be >= 0 (RMS surface roughness Rq, in "
                       "micrometres)", "ARG_RANGE",
                       extra={"roughness_um": roughness_um})
        if sg is None:
            return err("roughness_um corrects the COPPER loss, but "
                       "conductivity_s_m is null (ideal copper) - pass a "
                       "conductivity or drop the roughness", "ARG_RANGE",
                       extra={"roughness_um": roughness_um,
                              "conductivity_s_m": None})
        rq = rq_um * 1e-6

    if port_size_mm is None:
        return err("port_size_mm is required: it is the pad (or via) size, and it "
                   "is the only thing that makes the modal sum converge",
                   "ARG_REQUIRED",
                   hint="with a point port the sum has no limit: measured here on "
                        "a 50x50 mm pair at 2 GHz it reads 5.7 ohm at order 8, "
                        "13.7 at 64 and 19.2 at 256, still climbing. Averaging "
                        "the mode over the pad is what makes that sum converge, "
                        "so the pad size is part of the answer, not a detail")
    sx, sy = _plane_aperture(port_size_mm, a, b)
    if sx == "bad":
        return err("port_size_mm must be > 0 (a number, or [x, y] for a "
                   "rectangular pad)", "ARG_RANGE",
                   extra={"port_size_mm": port_size_mm})
    if sx == "wide":
        return err("port_size_mm must stay below half the plane: a pad that "
                   "wide is not a port, it is a plane", "ARG_RANGE",
                   extra={"port_size_mm": port_size_mm,
                          "plane_mm": [a, b]})

    def point(p, name):
        if p is None:
            return None
        if isinstance(p, dict):
            x, y = num(p.get("x_mm")), num(p.get("y_mm"))
        else:
            try:
                x, y = num(p[0]), num(p[1])
            except (TypeError, IndexError):
                return "bad"
        if x is None or y is None:
            return "bad"
        if x < 0.0 or y < 0.0 or x > a or y > b:
            return "out"
        return (x, y)

    r1 = point(port_mm, "port_mm")
    if r1 in ("bad", "out"):
        return err("port_mm must be [x_mm, y_mm] inside the plane", "ARG_RANGE")
    r2 = point(probe_mm, "probe_mm")
    if r2 in ("bad", "out"):
        return err("probe_mm must be [x_mm, y_mm] inside the plane", "ARG_RANGE")
    if r1 is None:
        r1 = (0.5 * a, 0.5 * b)
    if r2 is None:
        r2 = r1

    tol = num(tolerance)
    if tol is None or tol <= 0.0 or tol >= 0.5:
        return err("tolerance must be in (0, 0.5)", "ARG_RANGE",
                   extra={"tolerance": tolerance})
    try:
        order_cap = int(max_order)
    except (TypeError, ValueError):
        return err("max_order must be an integer", "ARG_INVALID")
    if order_cap < 8 or order_cap > 1024:
        return err("max_order must be in 8..400", "ARG_RANGE")

    a_m, b_m, t_m = a * 1e-3, b * 1e-3, t * 1e-3
    k_plane = C0 / math.sqrt(e)
    # _plane_modal_z works in metres; the caller's points are in mm
    q1 = (r1[0] * 1e-3, r1[1] * 1e-3)
    q2 = (r2[0] * 1e-3, r2[1] * 1e-3)

    def f_of(m, n):
        return k_plane * math.sqrt((math.pi * m / a_m) ** 2
                                   + (math.pi * n / b_m) ** 2) / (2.0 * math.pi)

    f_first = min(f_of(m, n) for m in range(2) for n in range(2) if (m or n))

    # ---- band -------------------------------------------------------------
    defaulted = False
    grid = None
    if freqs_hz is not None:
        if not isinstance(freqs_hz, (list, tuple)) or not freqs_hz:
            return err("freqs_hz must be a non-empty list", "ARG_INVALID")
        grid = []
        for f in freqs_hz:
            f = num(f)
            if f is None or f <= 0.0:
                return err("every frequency must be > 0", "ARG_RANGE",
                           extra={"offending": f})
            grid.append(f)
        if len(grid) > 2000:
            return err("freqs_hz is capped at 2000 points", "ARG_RANGE",
                       extra={"count": len(grid)})
        lo, hi = min(grid), max(grid)
        spacing = "caller's list"
    else:
        lo = num(f_start_hz)
        hi = num(f_stop_hz)
        if lo is None or hi is None:
            lo, hi = f_first / 50.0, 3.0 * f_first
            defaulted = True
        if lo <= 0.0 or hi <= lo:
            return err("need 0 < f_start_hz < f_stop_hz", "ARG_RANGE",
                       extra={"f_start_hz": lo, "f_stop_hz": hi})
        try:
            npts = int(points)
        except (TypeError, ValueError):
            return err("points must be an integer", "ARG_INVALID")
        if npts < 8 or npts > 2000:
            return err("points must be in 8..2000", "ARG_RANGE")
        spacing = "log" if hi / lo > 10.0 else "linear"
        if spacing == "log":
            r = (hi / lo) ** (1.0 / (npts - 1))
            grid = [lo * r ** i for i in range(npts)]
        else:
            grid = [lo + (hi - lo) * i / (npts - 1.0) for i in range(npts)]

    # how far in m and n a mode can still sit below f_hi (f_mn >= k_plane*m/(2 pi a))
    m_lim = int(a_m * 2.0 * math.pi * hi / k_plane) + 1
    n_lim = int(b_m * 2.0 * math.pi * hi / k_plane) + 1

    # ---- the order scan, on the frequencies that decide it -----------------
    # The peak is the most truncation-sensitive point on the curve, so the scan
    # includes every mode inside the band; a dozen points is enough to certify
    # the whole grid, and the full grid is then computed once.
    probe_f = []
    for m in range(m_lim + 1):
        for n in range(n_lim + 1):
            if not (m or n):
                continue
            fmn = f_of(m, n)
            if lo <= fmn <= hi and fmn not in probe_f:
                probe_f.append(fmn)
            if len(probe_f) >= 10:
                break
        if len(probe_f) >= 10:
            break
    mid = lo
    while mid < hi and len(probe_f) < 12:
        mid *= 1.7
        if mid < hi:
            probe_f.append(mid)
    if not probe_f:
        # A band with no mode inside it and no room for the mid-band fill - one
        # frequency, or a slice narrower than 1.7x - used to leave the gate with
        # NOTHING to look at, and an empty scan passes at the first order it is
        # allowed to. Measured: a single-frequency query was accepted at order
        # 64 with the answer still 9.7 % away from its order-128 value. The
        # caller's own edges are exactly what the number is about, so they are
        # the probe.
        probe_f = sorted(set([lo, hi]))
    probe_f.sort()

    def scan(order):
        return [_plane_modal_z(f, a_m, b_m, t_m, e, td, sg, order, q1, q2,
                               sx * 1e-3, sy * 1e-3, rq_m=rq) for f in probe_f]

    def rel_change(z_old, z_new):
        """Change of the answer, measured against the PEAK it contains.

        Not point by point: below the first mode the plane pair is the parallel
        combination of its capacitance and its spreading inductance, so there is
        a frequency where the two cancel and |Z| collapses. Near that dip |Z| is
        the difference of two larger reactive terms, and a point-wise relative
        tolerance there is unmeetable however many modes you add (measured: the
        dip is still moving 2.5 % while every other point has settled to 0.02 %).
        The error that matters is the error on the scale of the answer, so it is
        measured against max|Z| over the probe set. Both numbers are reported.
        """
        scale = 0.0
        for z in z_new:
            if z is not None and abs(z) > scale:
                scale = abs(z)
        worst = 0.0
        where = None
        pointwise = 0.0
        for f, zo, zn in zip(probe_f, z_old, z_new):
            if zo is None or zn is None:
                continue
            d = abs(zn - zo)
            if zn != 0:
                pointwise = max(pointwise, d / abs(zn))
            d = d / scale if scale else d
            if d > worst:
                worst, where = d, f
        return worst, where, pointwise

    order = 8
    z_prev = scan(order)
    history = []
    increments = []
    accepted = None
    while True:
        order_next = order * 2
        if order_next > order_cap:
            order_next = order_cap
        z_next = scan(order_next)
        rc, where, pw_change = rel_change(z_prev, z_next)
        prev_rc = increments[-1] if increments else None
        increments.append(rc)
        history.append({"order": order_next,
                        "max_change_rel_to_peak": _sig(rc),
                        "max_pointwise_relative_change": _sig(pw_change),
                        "worst_freq_hz": _sig(where) if where is not None else None})
        # Two conditions, not one. "The last doubling moved less than tolerance"
        # alone does not bound what is left: measured at a resonance this sum moves
        # 0.72 %, 0.85 %, 0.83 %, 0.46 %, 0.04 % per doubling, so order 16 would
        # have been accepted while still 2.2 % below the converged 92.45 ohm. The
        # tail only shrinks once the increments themselves halve, and that is what
        # is required here - the last increment under tolerance AND under half the
        # one before it.
        if (rc <= tol and len(increments) >= 3
                and prev_rc is not None and rc <= 0.5 * prev_rc):
            accepted = order_next
            break
        if order_next >= order_cap:
            return err(
                "the modal sum had not settled at order %d: |Z| still moves "
                "%.4g%% of the band peak at %.6g Hz when the truncation doubles, "
                "against a tolerance of %.4g%%. A smaller pad makes this worse, "
                "not better - the aperture is what regularises the series"
                % (order_next, 100.0 * rc, where or 0.0, 100.0 * tol),
                "NOT_CONVERGED",
                hint="widen port_size_mm (the pad, not the via drill) or raise "
                     "max_order. Without a finite pad the sum has no limit, "
                     "which is exactly why pdn_plane_modes refuses the curve",
                extra={"order_reached": order_next,
                       "max_change_rel_to_peak": _sig(rc),
                       "max_pointwise_relative_change": _sig(pw_change),
                       "worst_freq_hz": _sig(where) if where is not None else None,
                       "order_scan": history})
        order, z_prev = order_next, z_next

    # ---- the curve --------------------------------------------------------
    # insert every mode inside the band: the peaks have Q = 20..100, so a grid
    # of a few hundred points steps right over them
    insert = []
    for m in range(min(accepted, m_lim) + 1):
        for n in range(min(accepted, n_lim) + 1):
            if not (m or n):
                continue
            fmn = f_of(m, n)
            if lo <= fmn <= hi:
                insert.append(fmn)
    curve = sorted(set([round(f, 6) for f in grid] + [round(f, 6) for f in insert]))
    if len(curve) > 240:
        # keep the caller's grid honest by thinning it, never the mode peaks
        keep = set(round(f, 6) for f in insert)
        stride = int(math.ceil(len(curve) / 240.0))
        curve = [f for i, f in enumerate(curve) if i % stride == 0 or f in keep]

    rows = []
    for f in curve:
        z = _plane_modal_z(f, a_m, b_m, t_m, e, td, sg, accepted, q1, q2,
                           sx * 1e-3, sy * 1e-3, rq_m=rq)
        if z is None:
            continue
        rows.append({"freq_hz": _sig(f), "z_ohm": _sig(abs(z)),
                     "phase_deg": _sig(math.degrees(math.atan2(z.imag, z.real)), 5)})

    peak = max(rows, key=lambda r: r["z_ohm"])
    # the dip the caps have to beat: the lowest |Z| strictly between two modes.
    # Below the first mode the curve is the capacitance (it only falls), so a dip
    # only means something when there is a mode under it.
    first_mode = min(insert) if insert else None
    dips = [r for r in rows if first_mode is not None and r["freq_hz"] > first_mode]
    dip = min(dips, key=lambda r: r["z_ohm"]) if dips else None

    # ---- integrity gate: the very same constants must rebuild the plane cap --
    f_chk = f_first / 1000.0
    z_chk = _plane_modal_z(f_chk, a_m, b_m, t_m, e, td, sg, accepted, q1, q2,
                           sx * 1e-3, sy * 1e-3, rq_m=rq)
    c_pp = EPS0 * e * a_m * b_m / t_m
    z_cap = 1.0 / (2.0 * math.pi * f_chk * c_pp)
    anchor = abs(abs(z_chk) / z_cap - 1.0) * 100.0
    if anchor > 1.0:
        return err("the modal constants no longer rebuild the plane capacitance: "
                   "%.4g ohm against %.4g ohm at %.6g Hz (%.3g%% off). The curve "
                   "would be wrong by the same factor"
                   % (abs(z_chk), z_cap, f_chk, anchor),
                   "INTERNAL_CHECK_FAILED",
                   extra={"capacitance_anchor_error_pct": _sig(anchor)})

    q_first, qc_first, qd_first = _plane_q_of(f_first, t_m, td, sg, rq)
    extra = {
        "curve": rows,
        "curve_points": len(rows),
        "peak_z_ohm": peak["z_ohm"],
        "peak_freq_hz": peak["freq_hz"],
        "dip_z_ohm": dip["z_ohm"] if dip else None,
        "dip_freq_hz": dip["freq_hz"] if dip else None,
        "band_hz": [_sig(lo), _sig(hi)],
        "band_defaulted": defaulted,
        "spacing": spacing,
        "mode_frequencies_inserted": len(insert),
        "first_resonance_hz": _sig(f_first),
        "first_mode_q": _sig(q_first),
        "first_mode_q_copper": _sig(qc_first),
        "first_mode_q_dielectric": _sig(qd_first),
        "plane_capacitance_f": _sig(c_pp),
        "truncation_order": accepted,
        "truncation_max_change_rel_to_peak": history[-1]["max_change_rel_to_peak"],
        "truncation_max_pointwise_relative_change":
            history[-1]["max_pointwise_relative_change"],
        "truncation_tolerance": tol,
        "order_scan": history,
        "capacitance_anchor_error_pct": _sig(anchor),
        "port_mm": [r1[0], r1[1]],
        "probe_mm": [r2[0], r2[1]],
        "port_size_mm": [sx, sy],
        "copper_loss": "included (Qc = h/delta_s)" if sg else
                       "off: ideal copper, dielectric loss only",
        "not_computed": list(_PLANE_CURVE_NOT_COMPUTED),
    }
    if rq is not None:
        k_first = roughness_factor(rq, skin_depth_m(f_first, sg))
        extra["roughness"] = {
            "rq_um": _sig(rq * 1e6),
            "loss_factor_at_first_mode": _sig(k_first),
            "skin_depth_at_first_mode_um": _sig(skin_depth_m(f_first, sg) * 1e6),
            "q_copper_smooth_at_first_mode": _sig(qc_first * k_first),
            "q_copper_with_roughness_at_first_mode": _sig(qc_first),
            "model": "K = 1 + (2/pi)*atan(1.4*(Rq/delta_s)^2) "
                     "(Hammerstad-Jensen): Rs *= K, Qc = h/(delta_s*K); the "
                     "dielectric side is untouched",
        }
    return ok(peak["z_ohm"], unit="ohm",
              model="lossy_PMC_cavity_modal_sum_with_finite_pad_aperture",
              basis="Z = j*w*mu0*h*sum_mn A_mn/(k_mn^2 - k^2*(1 - j/Q_mn)), "
                    "A_mn = (c_m c_n)^2/(a b)*prod cos * pad average S_x^2 S_y^2, "
                    "1/Q_mn = 1/Qc + 1/Qd, Qc = h/delta_s, Qd = 1/tan_delta",
              precision="the curve is quoted at the truncation order in "
                        "order_scan, where doubling the order moved it by less "
                        "than truncation_tolerance. At a smaller pad the sum has "
                        "no limit and the tool refuses instead of returning one",
              assumptions={"er": e, "thickness_mm": t, "tan_delta": td,
                           "conductivity_s_m": sg,
                           "aperture": "the mode is averaged over a %.4g x %.4g mm "
                                       "pad, which is what gives the sum a limit"
                                       % (sx, sy),
                           "side_walls": "PMC (open edge, no fence)",
                           "loss": "dielectric + copper; nothing is mounted "
                                   "across the planes, so Q is an upper bound"},
              extra=extra)

# ---------------------------------------------------------------------------
# The plane WITH the decoupling caps: the cavity is an (N+1)-port network
# ---------------------------------------------------------------------------
_CAP_NOT_COMPUTED = (
    "the die and package side: the injection point is a port on the cavity, not "
    "a load with its own lead inductance, and the planes' via barrel/antipad "
    "are still not in the cavity (see pdn_plane_impedance)",
    "the mounting inductance beyond the number you pass: esl_h is yours, not a "
    "library part, and the escape from the cap pad down to the plane belongs in "
    "it. The same goes for esr_ohm",
    "every capacitor you do not list, and the caps' own anti-resonances "
    "against each other are only as good as the ESL/ESR you give them",
    "split planes, moats, radiation Q, open-edge detuning and the finite "
    "conductivity of the plane edge - all inherited from the plane-only model",
    "any verdict: whether an anti-resonance is a problem depends on the current "
    "spectrum the load draws, which is not in this number",
)

def _plane_modal_matrix(f_hz, a_m, b_m, h_m, er, tan_delta, sigma, order, ports,
                        rq_m=None):
    """The full Z matrix of the cavity between N+1 ports.

        Z_ij = j*w*mu0*h/(a b) * sum_mn P_i(m) P_j(m) Q_i(n) Q_j(n) / (k_mn^2-kap^2)
        P_i(m) = sqrt(c_m) S_x,i(m) cos(m pi x_i/a),   c_0 = 1, c_m = 2

    so that P_i P_j rebuilds the (c_m) factor of the one-pair sum exactly, and
    Q carries the same for n. The sqrt() split is deliberate: it is what lets
    one division per mode be shared by every i,j, which is why building the
    whole matrix costs barely more than two entries of it (measured: 15 entries
    at order 256 in 0.24 s against 0.04 s for one).

    ports = [(x_m, y_m, ap_x_m, ap_y_m), ...]; ap 0.0 is a point port, which is
    the divergent case and must not be used for a diagonal element.
    """
    w = 2.0 * math.pi * f_hz
    k2 = w * w * MU0 * EPS0 * er
    q, _qc, _qd = _plane_q_of(f_hz, h_m, tan_delta, sigma, rq_m)
    kap2 = k2 * (1.0 - 1j / q)
    n_i = order + 1
    pi = math.pi
    np_ = len(ports)
    cs = math.sqrt(2.0)
    p_all = []
    q_all = []
    for (x, y, apx, apy) in ports:
        P = [1.0] * n_i
        Q = [1.0] * n_i
        for m in range(1, n_i):
            s = _ap_sinc(pi * m * apx / (2.0 * a_m)) if apx > 0.0 else 1.0
            P[m] = cs * s * math.cos(pi * m * x / a_m)
            s = _ap_sinc(pi * m * apy / (2.0 * b_m)) if apy > 0.0 else 1.0
            Q[m] = cs * s * math.cos(pi * m * y / b_m)
        p_all.append(P)
        q_all.append(Q)
    kx2 = [(pi * m / a_m) ** 2 for m in range(n_i)]
    ky2 = [(pi * n / b_m) ** 2 for n in range(n_i)]
    # The hot loop is written out for 1..3 ports: one division per mode is
    # shared by every (i,j) pair, and with the pair loop flattened this is
    # measured 2.5x faster than the generic list-of-lists version. The generic
    # path stays for the (rare, expensive anyway) 4..8 cap case.
    if np_ == 1:
        m00 = 0j
        p0 = p_all[0]
        q0 = q_all[0]
        for m in range(n_i):
            base = kx2[m]
            a0m = p0[m]
            for n in range(n_i):
                a0 = a0m * q0[n]
                m00 += a0 * a0 / (base + ky2[n] - kap2)
        M = [[m00]]
    elif np_ == 2:
        m00 = m01 = m11 = 0j
        p0, p1 = p_all
        q0, q1 = q_all
        for m in range(n_i):
            base = kx2[m]
            a0m = p0[m]
            a1m = p1[m]
            for n in range(n_i):
                inv = 1.0 / (base + ky2[n] - kap2)
                a0 = a0m * q0[n]
                a1 = a1m * q1[n]
                m00 += a0 * a0 * inv
                m01 += a0 * a1 * inv
                m11 += a1 * a1 * inv
        M = [[m00, m01], [m01, m11]]
    elif np_ == 3:
        m00 = m01 = m02 = m11 = m12 = m22 = 0j
        p0, p1, p2 = p_all
        q0, q1, q2 = q_all
        for m in range(n_i):
            base = kx2[m]
            a0m = p0[m]
            a1m = p1[m]
            a2m = p2[m]
            for n in range(n_i):
                inv = 1.0 / (base + ky2[n] - kap2)
                a0 = a0m * q0[n]
                a1 = a1m * q1[n]
                a2 = a2m * q2[n]
                m00 += a0 * a0 * inv
                m01 += a0 * a1 * inv
                m02 += a0 * a2 * inv
                m11 += a1 * a1 * inv
                m12 += a1 * a2 * inv
                m22 += a2 * a2 * inv
        M = [[m00, m01, m02], [m01, m11, m12], [m02, m12, m22]]
    else:
        M = [[0j] * np_ for _ in range(np_)]
        for m in range(n_i):
            base = kx2[m]
            pm = [p_all[i][m] for i in range(np_)]
            for n in range(n_i):
                inv = 1.0 / (base + ky2[n] - kap2)
                a = [pm[i] * q_all[i][n] for i in range(np_)]
                for i in range(np_):
                    ai = a[i]
                    Mi = M[i]
                    for j in range(i, np_):
                        Mi[j] += ai * a[j] * inv
    sc = 1j * w * MU0 * h_m / (a_m * b_m)
    for i in range(np_):
        for j in range(i, np_):
            M[i][j] *= sc
            if j != i:
                M[j][i] = M[i][j]
    return M

def _csolve(A, b):
    """Solve A x = b for a small complex matrix: Gaussian elimination with
    partial pivoting. Returns None if A is singular (a cap branch that is a
    perfect short at this frequency, at the port's own location)."""
    n = len(b)
    m = [row[:] for row in A]
    x = list(b)
    for k in range(n):
        pk = k
        best = abs(m[k][k])
        for i in range(k + 1, n):
            v = abs(m[i][k])
            if v > best:
                best, pk = v, i
        if best == 0.0:
            return None
        if pk != k:
            m[k], m[pk] = m[pk], m[k]
            x[k], x[pk] = x[pk], x[k]
        piv = m[k][k]
        rowk = m[k]
        for i in range(k + 1, n):
            f = m[i][k] / piv
            if f == 0j:
                continue
            rowi = m[i]
            for j in range(k, n):
                rowi[j] -= f * rowk[j]
            x[i] -= f * x[k]
    for k in range(n - 1, -1, -1):
        acc = x[k]
        rowk = m[k]
        for j in range(k + 1, n):
            acc -= rowk[j] * x[j]
        x[k] = acc / rowk[k]
    return x

def pdn_plane_with_caps(width_mm, height_mm, er, thickness_mm, tan_delta=None,
                        port_mm=None, port_size_mm=None, caps=None,
                        f_start_hz=None, f_stop_hz=None, points=100,
                        freqs_hz=None, max_order=1024, tolerance=0.01,
                        conductivity_s_m=COPPER_SIGMA, roughness_um=None):
    """|Z(f)| of a plane pair WITH the decoupling capacitors mounted on it.

    pdn_plane_impedance is the planes alone, and it says so: the caps are
    usually the dominant damping across a plane pair, so its peaks come out too
    high and too narrow, and - the part that actually bites - the valley
    BETWEEN two peaks is where a cap's inductance and the plane's capacitance
    resonate, which the plane-only curve cannot show at all.

    The model here is the classical one, and it is exact within it:

      * the cavity is an (N+1)-port network; port 0 is the injection point and
        ports 1..N are the cap sites, each with its own pad (a cap site is a
        small pad, the injection point may not be);
      * every cap is an R-L-C branch in series, esr_ohm + j*w*esl_h + 1/(j*w*C),
        connected from the top plane to the bottom plane at its site;
      * the input impedance is the standard port reduction

            Z_in = Z00 - Z0c (Zcc + diag(Z_c))^-1 Zc0

        which is what "load the cavity with branches" means once the network is
        written down - no fitting, no curve peeking.

    What you get back, and how to read it:
      * peak_z_ohm / dip_z_ohm of the loaded curve, and false-free context for
        them: plane_only_peak_z_ohm is Z00 on the SAME curve, so
        damping_ratio tells you what the caps actually bought at the peak - it
        is not a claim about a different model;
      * an anti-resonance shows up as a peak that is NOT near a cavity mode:
        the cap turns inductive, the plane is capacitive, and the two parallel-
        resonate there. Drop esl_h and that peak moves up in frequency
        (measured here: 4 nH puts it at 279 MHz, 1 nH at 440 MHz, while the
        first cavity mode stays at 1.429 GHz).

    Buried in the port reduction: the self term Z_ii of a point port diverges,
    exactly as in pdn_plane_impedance, so every cap site needs its own pad
    (pad_mm). Without it there is no answer to give.

    Convergence. The same gate as the plane-only curve - double the order until
    the loaded |Z_in| stops moving relative to the band peak - and the caps make
    the series no easier to sum, because a cap pad is small - and the loaded
    answer needs MORE order than the plane-only one, which is why the default
    here is 1024 and there 256. Measured on the default band, one 100 nF / 1 nH
    / 0.01 ohm cap on a 0.5 mm pad: at the centre of the pair it settles at
    order 512 (at 256 the loaded curve near 400 MHz still moves 2.5 % of the
    band peak); on the NODE of the (0,2) mode it needs the full 1024 (at 512 it
    still moves 2.1 %). The node is the hard one because there the loaded |Z| is
    a small difference of comparable numbers, so a truncation error in Z_0c or
    Z_cc arrives amplified. If the gate refuses, the hint is the honest one:
    raise max_order (it costs order^2) or widen the pad.
    """
    def num(x):
        try:
            v = float(x)
        except (TypeError, ValueError):
            return None
        if v != v or v in (float("inf"), float("-inf")):
            return None
        return v

    a = num(width_mm)
    b = num(height_mm)
    e = num(er)
    t = num(thickness_mm)
    td = num(tan_delta)
    for name, v in (("width_mm", a), ("height_mm", b), ("er", e),
                    ("thickness_mm", t)):
        if v is None:
            return err("%s is required and must be a number" % name, "ARG_REQUIRED")
        if v <= 0.0:
            return err("%s must be > 0" % name, "ARG_RANGE", extra={"offending": name})
    if e < 1.0:
        return err("er must be >= 1", "ARG_RANGE", extra={"er": e})
    if td is None:
        return err("tan_delta is required: with no loss every mode peak is "
                   "infinite, and the caps would be damping an answer that does "
                   "not exist", "ARG_REQUIRED",
                   hint="FR4 is 0.015..0.025")
    if td <= 0.0 or td >= 1.0:
        return err("tan_delta must be in (0, 1)", "ARG_RANGE",
                   extra={"tan_delta": td})
    sg = None if conductivity_s_m is None else num(conductivity_s_m)
    if sg is not None and sg <= 0.0:
        return err("conductivity_s_m must be > 0, or null for ideal copper",
                   "ARG_RANGE", extra={"conductivity_s_m": sg})

    rq = None
    if roughness_um is not None:
        rq_um = num(roughness_um)
        if rq_um is None or rq_um < 0.0:
            return err("roughness_um must be >= 0 (RMS surface roughness Rq, in "
                       "micrometres)", "ARG_RANGE",
                       extra={"roughness_um": roughness_um})
        if sg is None:
            return err("roughness_um corrects the COPPER loss, but "
                       "conductivity_s_m is null (ideal copper) - pass a "
                       "conductivity or drop the roughness", "ARG_RANGE",
                       extra={"roughness_um": roughness_um,
                              "conductivity_s_m": None})
        rq = rq_um * 1e-6

    if port_size_mm is None:
        return err("port_size_mm is required: it is the pad of the INJECTION "
                   "point, and a point port makes the modal sum diverge",
                   "ARG_REQUIRED",
                   hint="a decoupling cap site and an injection point are both "
                        "finite pads; the pad is what makes the series converge")
    sx, sy = _plane_aperture(port_size_mm, a, b)
    if sx == "bad":
        return err("port_size_mm must be > 0 (a number, or [x, y] for a "
                   "rectangular pad)", "ARG_RANGE",
                   extra={"port_size_mm": port_size_mm})
    if sx == "wide":
        return err("port_size_mm must stay below half the plane", "ARG_RANGE",
                   extra={"port_size_mm": port_size_mm, "plane_mm": [a, b]})

    def point(p):
        if p is None:
            return None
        if isinstance(p, dict):
            x, y = num(p.get("x_mm")), num(p.get("y_mm"))
        else:
            try:
                x, y = num(p[0]), num(p[1])
            except (TypeError, IndexError):
                return "bad"
        if x is None or y is None:
            return "bad"
        if x < 0.0 or y < 0.0 or x > a or y > b:
            return "out"
        return (x, y)

    r0 = point(port_mm)
    if r0 in ("bad", "out"):
        return err("port_mm must be [x_mm, y_mm] inside the plane", "ARG_RANGE")
    if r0 is None:
        r0 = (0.5 * a, 0.5 * b)

    if caps is None:
        caps = []
    if not isinstance(caps, (list, tuple)):
        return err("caps must be a list of {at_mm, c_farad, pad_mm} objects",
                   "ARG_INVALID")
    if len(caps) > 8:
        return err("at most 8 capacitors per call: each one is another port and "
                   "the modal matrix grows as (N+1)^2", "ARG_RANGE",
                   extra={"count": len(caps)})
    site = []
    for k, c in enumerate(caps):
        if not isinstance(c, dict):
            return err("caps[%d] must be an object" % k, "ARG_INVALID")
        at = point(c.get("at_mm"))
        if at in ("bad", "out") or at is None:
            return err("caps[%d].at_mm must be [x_mm, y_mm] inside the plane" % k,
                       "ARG_RANGE")
        cf = num(c.get("c_farad"))
        if cf is None:
            return err("caps[%d].c_farad is required and must be a number" % k,
                       "ARG_REQUIRED")
        if cf <= 0.0:
            return err("caps[%d].c_farad must be > 0" % k, "ARG_RANGE")
        if c.get("pad_mm") is None:
            return err("caps[%d].pad_mm is required: a cap site is a finite pad, "
                       "and a point port there would make the series diverge" % k,
                       "ARG_REQUIRED",
                       hint="the pad is the copper landing the cap sits on, not "
                            "the via drill")
        px_, py_ = _plane_aperture(c.get("pad_mm"), a, b)
        if px_ == "bad":
            return err("caps[%d].pad_mm must be > 0" % k, "ARG_RANGE")
        if px_ == "wide":
            return err("caps[%d].pad_mm must stay below half the plane" % k,
                       "ARG_RANGE")
        esr = num(c.get("esr_ohm", 0.0))
        esl = num(c.get("esl_h", 0.0))
        if esr is None or esr < 0.0:
            return err("caps[%d].esr_ohm must be >= 0 (0 is an ideal cap)" % k,
                       "ARG_RANGE")
        if esl is None or esl < 0.0:
            return err("caps[%d].esl_h must be >= 0 (0 is an ideal cap)" % k,
                       "ARG_RANGE")
        site.append({"at_mm": at, "c_farad": cf, "pad_mm": (px_, py_),
                     "esr_ohm": esr, "esl_h": esl})

    tol = num(tolerance)
    if tol is None or tol <= 0.0 or tol >= 0.5:
        return err("tolerance must be in (0, 0.5)", "ARG_RANGE",
                   extra={"tolerance": tolerance})
    try:
        order_cap = int(max_order)
    except (TypeError, ValueError):
        return err("max_order must be an integer", "ARG_INVALID")
    if order_cap < 8 or order_cap > 1024:
        return err("max_order must be in 8..1024", "ARG_RANGE")

    a_m, b_m, t_m = a * 1e-3, b * 1e-3, t * 1e-3
    k_plane = C0 / math.sqrt(e)
    ports = [(r0[0] * 1e-3, r0[1] * 1e-3, sx * 1e-3, sy * 1e-3)]
    for s in site:
        ports.append((s["at_mm"][0] * 1e-3, s["at_mm"][1] * 1e-3,
                      s["pad_mm"][0] * 1e-3, s["pad_mm"][1] * 1e-3))
    n_cap = len(site)

    def f_of(m, n):
        return k_plane * math.sqrt((math.pi * m / a_m) ** 2
                                   + (math.pi * n / b_m) ** 2) / (2.0 * math.pi)

    f_first = min(f_of(m, n) for m in range(2) for n in range(2) if (m or n))

    # ---- band -------------------------------------------------------------
    defaulted = False
    grid = None
    if freqs_hz is not None:
        if not isinstance(freqs_hz, (list, tuple)) or not freqs_hz:
            return err("freqs_hz must be a non-empty list", "ARG_INVALID")
        grid = []
        for f in freqs_hz:
            f = num(f)
            if f is None or f <= 0.0:
                return err("every frequency must be > 0", "ARG_RANGE")
            grid.append(f)
        if len(grid) > 2000:
            return err("freqs_hz is capped at 2000 points", "ARG_RANGE",
                       extra={"count": len(grid)})
        lo, hi = min(grid), max(grid)
        spacing = "caller's list"
    else:
        lo = num(f_start_hz)
        hi = num(f_stop_hz)
        if lo is None or hi is None:
            lo, hi = f_first / 50.0, 3.0 * f_first
            defaulted = True
        if lo <= 0.0 or hi <= lo:
            return err("need 0 < f_start_hz < f_stop_hz", "ARG_RANGE",
                       extra={"f_start_hz": lo, "f_stop_hz": hi})
        try:
            npts = int(points)
        except (TypeError, ValueError):
            return err("points must be an integer", "ARG_INVALID")
        if npts < 8 or npts > 2000:
            return err("points must be in 8..2000", "ARG_RANGE")
        spacing = "log" if hi / lo > 10.0 else "linear"
        if spacing == "log":
            r = (hi / lo) ** (1.0 / (npts - 1))
            grid = [lo * r ** i for i in range(npts)]
        else:
            grid = [lo + (hi - lo) * i / (npts - 1.0) for i in range(npts)]

    m_lim = int(a_m * 2.0 * math.pi * hi / k_plane) + 1
    n_lim = int(b_m * 2.0 * math.pi * hi / k_plane) + 1

    probe_f = []
    for m in range(m_lim + 1):
        for n in range(n_lim + 1):
            if not (m or n):
                continue
            fmn = f_of(m, n)
            if lo <= fmn <= hi and fmn not in probe_f:
                probe_f.append(fmn)
            if len(probe_f) >= 10:
                break
        if len(probe_f) >= 10:
            break
    mid = lo
    while mid < hi and len(probe_f) < 12:
        mid *= 1.7
        if mid < hi:
            probe_f.append(mid)
    if not probe_f:
        # A band with no mode inside it and no room for the mid-band fill - one
        # frequency, or a slice narrower than 1.7x - used to leave the gate with
        # NOTHING to look at, and an empty scan passes at the first order it is
        # allowed to. Measured: a single-frequency query was accepted at order
        # 64 with the answer still 9.7 % away from its order-128 value. The
        # caller's own edges are exactly what the number is about, so they are
        # the probe.
        probe_f = sorted(set([lo, hi]))
    probe_f.sort()

    def cap_z(f_hz, s):
        w = 2.0 * math.pi * f_hz
        return complex(s["esr_ohm"], w * s["esl_h"] - 1.0 / (w * s["c_farad"]))

    def answer(f_hz, order):
        """(Z_in, Z00): the loaded input impedance and the plane alone."""
        M = _plane_modal_matrix(f_hz, a_m, b_m, t_m, e, td, sg, order, ports,
                                rq_m=rq)
        z00 = M[0][0]
        if not n_cap:
            return z00, z00
        z0c = [M[0][1 + i] for i in range(n_cap)]
        A = [[M[1 + i][1 + j] + (cap_z(f_hz, site[i]) if i == j else 0j)
              for j in range(n_cap)] for i in range(n_cap)]
        x = _csolve(A, z0c)
        if x is None:
            return "singular", z00
        return z00 - sum(z0c[i] * x[i] for i in range(n_cap)), z00

    def scan(order):
        return [answer(f, order) for f in probe_f]

    def rel_change(old, new):
        """Worst change of BOTH answers the tool publishes, each against its own peak.

        The loaded curve is the answer, but plane_only_* is published beside it, and
        a truncated plane can converge in the loaded answer (a cap damps it) while
        still moving in the plane-only one. Both are gated, so the plane-only numbers
        stay comparable with pdn_plane_impedance at the order reported.
        """
        rc = []
        for idx in (0, 1):
            scale = 0.0
            for pair in new:
                if pair == "singular":
                    return None, None, None, None, None
                if abs(pair[idx]) > scale:
                    scale = abs(pair[idx])
            worst = 0.0
            where = None
            pointwise = 0.0
            for f, po, pn in zip(probe_f, old, new):
                if po == "singular" or pn == "singular":
                    return None, None, None, None, None
                d = abs(pn[idx] - po[idx])
                if pn[idx] != 0:
                    pointwise = max(pointwise, d / abs(pn[idx]))
                d = d / scale if scale else d
                if d > worst:
                    worst, where = d, f
            rc.append((worst, where, pointwise))
        if rc[0][0] >= rc[1][0]:
            worst, where, pointwise = rc[0]
        else:
            worst, where, pointwise = rc[1]
        return worst, where, pointwise, rc[0][0], rc[1][0]

    order = 8
    z_prev = scan(order)
    history = []
    increments = []
    accepted = None
    while True:
        order_next = order * 2
        if order_next > order_cap:
            order_next = order_cap
        z_next = scan(order_next)
        rc, where, pw_change, rc_in, rc_pl = rel_change(z_prev, z_next)
        if rc is None:
            return err("the loaded cavity is singular at one of the probe "
                       "frequencies: a cap branch is a perfect short at the "
                       "injection point", "NO_SOLUTION",
                       hint="give the cap a non-zero esr_ohm or move it off the "
                            "injection point")
        prev_rc = increments[-1] if increments else None
        increments.append(rc)
        history.append({"order": order_next,
                        "max_change_rel_to_peak": _sig(rc),
                        "loaded_change_rel_to_peak": _sig(rc_in),
                        "plane_only_change_rel_to_peak": _sig(rc_pl),
                        "max_pointwise_relative_change": _sig(pw_change),
                        "worst_freq_hz": _sig(where) if where is not None else None})
        # Same two-condition gate as pdn_plane_impedance: a doubling under
        # tolerance AND under half the previous doubling. One condition can be
        # fooled by a slowly converging tail - the increments here can sit near
        # the tolerance for several orders before they collapse.
        if (rc <= tol and len(increments) >= 3
                and prev_rc is not None and rc <= 0.5 * prev_rc):
            accepted = order_next
            break
        if order_next >= order_cap:
            return err(
                "the loaded modal sum had not settled at order %d: |Z_in| still "
                "moves %.4g%% of the band peak at %.6g Hz when the truncation "
                "doubles, against a tolerance of %.4g%%"
                % (order_next, 100.0 * rc, where or 0.0, 100.0 * tol),
                "NOT_CONVERGED",
                hint="widen the pad (port_size_mm / caps[].pad_mm) or raise "
                     "max_order; a small pad is what makes this series hard to sum",
                extra={"order_reached": order_next,
                       "max_change_rel_to_peak": _sig(rc),
                       "max_pointwise_relative_change": _sig(pw_change),
                       "worst_freq_hz": _sig(where) if where is not None else None,
                       "order_scan": history})
        order, z_prev = order_next, z_next

    insert = []
    for m in range(min(accepted, m_lim) + 1):
        for n in range(min(accepted, n_lim) + 1):
            if not (m or n):
                continue
            fmn = f_of(m, n)
            if lo <= fmn <= hi:
                insert.append(fmn)
    curve = sorted(set([round(f, 6) for f in grid] + [round(f, 6) for f in insert]))
    if len(curve) > 240:
        keep = set(round(f, 6) for f in insert)
        stride = int(math.ceil(len(curve) / 240.0))
        curve = [f for i, f in enumerate(curve) if i % stride == 0 or f in keep]

    rows = []
    worst_re = None
    worst_re_f = None
    for f in curve:
        zi, zp = answer(f, accepted)
        if zi == "singular":
            return err("the loaded cavity is singular at %.6g Hz: a cap branch is "
                       "a perfect short there" % f, "NO_SOLUTION",
                       hint="give the cap a non-zero esr_ohm")
        if zi.real < 0.0 and (worst_re is None or zi.real / abs(zi) < worst_re):
            worst_re, worst_re_f = zi.real / abs(zi), f
        rows.append({"freq_hz": _sig(f), "z_ohm": _sig(abs(zi)),
                     "plane_only_z_ohm": _sig(abs(zp)),
                     "phase_deg": _sig(math.degrees(math.atan2(zi.imag, zi.real)), 5)})
    if worst_re is not None and worst_re < -1e-9:
        return err("the loaded network is not passive: Re(Z_in) is %.3g of |Z_in| "
                   "at %.6g Hz. A passive cavity plus esr >= 0 cannot do that, so "
                   "the port reduction or a modal normalisation is wrong"
                   % (worst_re, worst_re_f or 0.0),
                   "INTERNAL_CHECK_FAILED",
                   extra={"worst_re_over_abs_z": _sig(worst_re, 5),
                          "worst_re_freq_hz": _sig(worst_re_f)})

    peak = max(rows, key=lambda r: r["z_ohm"])
    plane_peak = max(rows, key=lambda r: r["plane_only_z_ohm"])
    first_mode = min(insert) if insert else None
    dips = [r for r in rows if first_mode is not None and r["freq_hz"] > first_mode]
    dip = min(dips, key=lambda r: r["z_ohm"]) if dips else None

    # ---- integrity gates --------------------------------------------------
    # (1) the plane half: below the first mode Z00 must be the plane cap. This
    #     is the same anchor pdn_plane_impedance uses, measured there at 0.15 %.
    c_pp = EPS0 * e * a_m * b_m / t_m
    f_pl = f_first / 1000.0
    zi_pl, zp_pl = answer(f_pl, accepted)
    if zi_pl == "singular":
        return err("the loaded cavity is singular at the plane anchor frequency",
                   "NO_SOLUTION")
    anchor = abs(abs(zp_pl) / (1.0 / (2.0 * math.pi * f_pl * c_pp)) - 1.0) * 100.0
    if anchor > 1.0:
        return err("the modal constants no longer rebuild the plane capacitance: "
                   "%.4g ohm against %.4g ohm at %.6g Hz (%.3g%% off)"
                   % (abs(zp_pl), 1.0 / (2.0 * math.pi * f_pl * c_pp), f_pl,
                      anchor),
                   "INTERNAL_CHECK_FAILED",
                   extra={"plane_anchor_error_pct": _sig(anchor)})

    # (2) the loaded half: at a frequency low enough that the cavity itself is
    #     one capacitor, the loaded input must be the caps in parallel with the
    #     plane capacitance. That anchor is only exact when the caps DOMINATE
    #     there: the (0,0) mode carries a low-frequency copper-loss wart (Q =
    #     h/delta_s makes a uniform field lossy as f -> 0, which it is not), and
    #     the spreading inductance between the two ports is real. Measured: with
    #     the caps 10x smaller than |Z00| the anchor holds to 0.02 %, with the
    #     caps comparable to |Z00| it is off by up to 2.4 % - so it is applied
    #     only in the regime where it means something, and reported either way.
    f_lf = f_first / 1e5
    zi_lf, zp_lf = answer(f_lf, accepted)
    if zi_lf == "singular":
        return err("the loaded cavity is singular at the anchor frequency",
                   "NO_SOLUTION")
    y_tot = complex(0.0, 2.0 * math.pi * f_lf * c_pp)
    for s in site:
        y_tot += 1.0 / cap_z(f_lf, s)
    z_anchor = 1.0 / y_tot
    anchor_lf = abs(abs(zi_lf) / abs(z_anchor) - 1.0) * 100.0
    anchor_lf_ok = bool(site) and abs(z_anchor) <= 0.1 * abs(zp_lf)
    if anchor_lf_ok and anchor_lf > 0.5:
        return err("at %.6g Hz the loaded network no longer rebuilds its own "
                   "low-frequency limit: %.4g ohm against %.4g ohm (%.3g%% off), "
                   "and there the caps dominate, so nothing else can explain it"
                   % (f_lf, abs(zi_lf), abs(z_anchor), anchor_lf),
                   "INTERNAL_CHECK_FAILED",
                   extra={"low_freq_anchor_error_pct": _sig(anchor_lf),
                          "low_freq_anchor_hz": _sig(f_lf)})

    # (3) passivity: a passive cavity loaded with esr >= 0 cannot hand energy
    #     back. It bites if the port reduction or the matrix normalisation is
    #     wrong, which is exactly the mistake a factor-of-two in P_i makes.
    #     Checked over the curve below.

    # what the caps did AT the plane's own worst mode - the number a cap bank
    # has to move, as opposed to wherever the loaded curve happens to peak.
    # plane_peak IS a row of the same curve, so this costs nothing.
    at_plane_peak = plane_peak["z_ohm"]
    q_first, qc_first, qd_first = _plane_q_of(f_first, t_m, td, sg, rq)
    c_tot = c_pp + sum(s["c_farad"] for s in site)
    extra = {
        "curve": rows,
        "curve_points": len(rows),
        "peak_z_ohm": peak["z_ohm"],
        "peak_freq_hz": peak["freq_hz"],
        "dip_z_ohm": dip["z_ohm"] if dip else None,
        "dip_freq_hz": dip["freq_hz"] if dip else None,
        "plane_only_peak_z_ohm": plane_peak["plane_only_z_ohm"],
        "plane_only_peak_freq_hz": plane_peak["freq_hz"],
        "peak_ratio_vs_plane": _sig(peak["z_ohm"] / plane_peak["plane_only_z_ohm"])
                               if plane_peak["plane_only_z_ohm"] else None,
        "peak_at_cavity_mode": bool(
            min([abs(peak["freq_hz"] / f - 1.0) for f in insert])
            < 0.01) if insert else False,
        "at_plane_peak_z_ohm": at_plane_peak,
        "at_plane_peak_damping": _sig(plane_peak["plane_only_z_ohm"]
                                      / at_plane_peak) if at_plane_peak else None,
        "band_hz": [_sig(lo), _sig(hi)],
        "band_defaulted": defaulted,
        "spacing": spacing,
        "mode_frequencies_inserted": len(insert),
        "first_resonance_hz": _sig(f_first),
        "first_mode_q": _sig(q_first),
        "first_mode_q_copper": _sig(qc_first),
        "first_mode_q_dielectric": _sig(qd_first),
        "plane_capacitance_f": _sig(c_pp),
        "total_capacitance_f": _sig(c_tot),
        "cap_count": n_cap,
        "caps": [{"at_mm": list(s["at_mm"]), "c_farad": _sig(s["c_farad"]),
                  "pad_mm": [s["pad_mm"][0], s["pad_mm"][1]],
                  "esr_ohm": _sig(s["esr_ohm"]), "esl_h": _sig(s["esl_h"])}
                 for s in site],
        "truncation_order": accepted,
        "truncation_max_change_rel_to_peak": history[-1]["max_change_rel_to_peak"],
        "truncation_max_pointwise_relative_change":
            history[-1]["max_pointwise_relative_change"],
        "truncation_tolerance": tol,
        "order_scan": history,
        "plane_anchor_error_pct": _sig(anchor),
        "low_freq_anchor_error_pct": _sig(anchor_lf),
        "low_freq_anchor_hz": _sig(f_lf),
        "low_freq_anchor_applicable": anchor_lf_ok,
        "worst_re_over_abs_z": _sig(worst_re, 5) if worst_re is not None else None,
        "port_mm": [r0[0], r0[1]],
        "port_size_mm": [sx, sy],
        "copper_loss": "included (Qc = h/delta_s)" if sg else
                       "off: ideal copper, dielectric loss only",
        "not_computed": list(_CAP_NOT_COMPUTED),
    }
    if rq is not None:
        k_first = roughness_factor(rq, skin_depth_m(f_first, sg))
        extra["roughness"] = {
            "rq_um": _sig(rq * 1e6),
            "loss_factor_at_first_mode": _sig(k_first),
            "skin_depth_at_first_mode_um": _sig(skin_depth_m(f_first, sg) * 1e6),
            "q_copper_smooth_at_first_mode": _sig(qc_first * k_first),
            "q_copper_with_roughness_at_first_mode": _sig(qc_first),
            "model": "K = 1 + (2/pi)*atan(1.4*(Rq/delta_s)^2) "
                     "(Hammerstad-Jensen): Rs *= K, Qc = h/(delta_s*K); the "
                     "dielectric side is untouched, and the capacitors see the "
                     "same roughened planes the curve does",
        }
    return ok(peak["z_ohm"], unit="ohm",
              model="lossy_PMC_cavity_modal_sum_port_reduced_with_RLC_caps",
              basis="Z_in = Z00 - Z0c*(Zcc + diag(esr + jw*esl + 1/(jwC)))^-1*Zc0, "
                    "with Z_ij the cavity transfer impedance of "
                    "pdn_plane_impedance for each port's own pad",
              precision="the loaded curve is quoted at the truncation order in "
                        "order_scan, where doubling the order moved it by less "
                        "than truncation_tolerance",
              assumptions={"er": e, "thickness_mm": t, "tan_delta": td,
                           "conductivity_s_m": sg,
                           "aperture": "every port is area-averaged over its own "
                                       "pad, which is what gives the series a limit",
                           "side_walls": "PMC (open edge, no fence)",
                           "caps": "%d R-L-C branch(es) from plane to plane, "
                                   "driven by the esr/esl you gave" % n_cap},
              extra=extra)

def pdn_impedance(freqs_hz, caps, plane_cap_farad=None, r_series_ohm=0.0,
                  plane=None):
    """|Z(f)| seen by the die: N decoupling caps in parallel, plus optional plane.

    Caps are ideal R-L-C in parallel, which is what a decoupling network looks
    like from the die until the plane resonances start. `plane_cap_farad` adds
    the plane pair's parallel-plate capacitance (the (0,0) mode).

    `plane={width_mm, height_mm, er, thickness_mm, tan_delta, port_mm, probe_mm}`
    additionally reports the plane CAVITY MODES and which of them fall inside the
    frequency sweep you asked for. That is a diagnostic, not a term in the sum:
    the modal curve cannot be summed honestly (see pdn_plane_modes), so nothing
    is added to |Z| - instead the answer tells you whether the band you are
    looking at contains a mode whose peak is above the |Z| you just computed.
    """
    if not caps:
        return err("caps is empty", "ARG_REQUIRED",
                   hint="each cap needs c_farad, and optionally esr_ohm / esl_h")
    out = []
    zmin = None
    fpeak = None
    for f in freqs_hz:
        f = float(f)
        if f <= 0:
            return err("frequencies must be > 0", "ARG_RANGE")
        y = 0j
        for cap in caps:
            z = cap_impedance(f, float(cap["c_farad"]),
                              float(cap.get("esr_ohm", 0.0) or 0.0),
                              float(cap.get("esl_h", 0.0) or 0.0))
            if z is None:
                return err("a cap has a non-positive capacitance", "ARG_INVALID")
            y += 1.0 / z
        if plane_cap_farad:
            y += 1j * 2.0 * math.pi * f * float(plane_cap_farad)
        z = 1.0 / y if y != 0 else complex(1e18, 0)
        z += float(r_series_ohm)
        mag = abs(z)
        out.append({"freq_hz": round(f, 6), "z_ohm": round(mag, 9),
                    "phase_deg": round(math.degrees(math.atan2(z.imag, z.real)), 4)})
        if zmin is None or mag > 0 and mag < zmin:
            zmin = mag
            fpeak = f
    plane_diag = None
    if plane:
        lo = min(float(f) for f in freqs_hz)
        hi = max(float(f) for f in freqs_hz)
        pm = pdn_plane_modes(plane.get("width_mm"), plane.get("height_mm"),
                             plane.get("er"), plane.get("thickness_mm"),
                             plane.get("tan_delta"),
                             probe_mm=plane.get("probe_mm"),
                             port_mm=plane.get("port_mm"),
                             band_hz=[lo, hi],
                             max_order=(plane.get("max_order")
                                        if plane.get("max_order") is not None else 6))
        if pm.get("status") == "error":
            return pm
        plane_diag = {
            "sweep_band_hz": [round(lo, 6), round(hi, 6)],
            "modes_in_band": [m for m in pm["value"]["modes"] if m.get("in_band")],
            "worst_mode_in_band": pm["worst_mode"],
            "z_min_lumped_ohm": round(zmin, 9),
            "why_not_a_term": ("the modal impedance is not added to Y: the sum over "
                               "modes does not converge near and above the first "
                               "resonance, so it would be an artefact of the "
                               "truncation order. Compare the mode's peak against "
                               "the lumped |Z| instead"),
        }
        plane_diag["worst_mode_any_band"] = pm.get("worst_mode_any_band")
        if plane_diag["worst_mode_in_band"]:
            plane_diag["verdict"] = (
                "a plane mode inside this band peaks at %s ohm, against a lumped "
                "minimum of %s ohm - the lumped curve is NOT the PDN there"
                % (plane_diag["worst_mode_in_band"]["z_peak_ohm"], round(zmin, 9)))
        else:
            plane_diag["verdict"] = ("no cavity mode of this plane pair falls inside "
                                     "the sweep, so the lumped curve stands over it")

    return ok(value={"points": out, "min_z_ohm": round(zmin, 9),
                     "min_z_hz": round(fpeak, 6),
                     "caps": len(caps),
                     "plane_modes": plane_diag,
                     "note": "a PDN is judged by its MAXIMUM |Z| over the band that "
                             "matters, not by its minimum - the minimum is just the "
                             "best case between resonances"},
              unit="ohm", model="parallel_series_rlc",
              basis="Y(f) = sum 1/(ESR + j(w*ESL - 1/(w*C))) + j*w*C_plane; Z = 1/Y + "
                    "R_series",
              precision="exact for the lumped model. On a real board the anti-resonance "
                        "BETWEEN two capacitor values is the number that fails, and it "
                        "is set by the plane inductance, which this model does not have",
              assumptions={"caps": len(caps), "r_series_ohm": float(r_series_ohm)},
              extra={"not_computed": ["plane cavity resonances inside the swept band"
                                      if not plane else
                                      "plane cavity resonances outside the swept "
                                      "band (only the modes inside it are reported)",
                                      "anti-resonance from plane inductance",
                                      "die-package inductance",
                                      "capacitor DC bias / temperature derating"]})


# ------------------------------------------------------------------ time domain

STEP_MODEL = "pdn_step_backward_euler"


def pdn_step_response(caps, step_a=1.0, preload_a=0.0, r_series_ohm=0.0,
                      vr_series_h=0.0, plane_r_ohm=0.0, plane_l_h=0.0,
                      rise_ns=None, t_stop_ns=None, samples=1200,
                      settle_band_pct=1.0, refine=True, probe_at_ns=None):
    """Rail droop in the TIME domain for a load step, on the network |Z(f)| lives on.

    `pdn_impedance` answers "is there a frequency where this bank looks bad". This
    answers a different question: the load jumps, how far down does the rail go
    before the caps catch it, and how long until it is back. A datasheet's "load
    transient" row is about the second one, and the two are not interchangeable -
    a bank with a good |Z| in the band still droops by I*ESR, and a bank that looks
    bad at one frequency may never be excited at that frequency by a 100 A/us edge.

    Topology (all branches are series R-L-C, one reference node, the port is
    die-to-ground):

        ref --[R_series, L_series]--+--[plane R, L]--+-- die   <-- load sink
                                     |                |
                                 bulk caps         die caps

    `caps` carries `c_farad`, and optionally `esr_ohm`, `esl_h`, `count`, and
    `at` ("die" or "bulk"). Caps at "bulk" sit on the far side of
    `plane_r_ohm`/`plane_l_h` - that is what turns "the bulk caps are at the
    connector, 30 mm from the die" into a number instead of a hand wave.

    The VRM is a Thevenin source: R_series plus an optional series inductance.
    That inductance is also the only thing that limits how fast the regulator's
    current can rise (dI/dt = delta_v / L), which is the physical reason a step
    is not supplied instantly - there is no separate "slew rate" knob, and adding
    one would be the same number twice.

    Integrated with backward Euler on an adaptive grid: the first step is the
    fastest branch's own natural period / 40 and it grows 2% per step up to
    t_stop/samples, so a 1 nH ESL ring at t~0 and a 1 ms bulk recovery are both
    resolved without a 1e9-step march. Backward Euler is L-stable but dissipative
    (it DAMPS a ring), so `h_refine_peak_delta_pct` re-runs the whole thing at
    half the step - if that number is large, the peak is a grid artefact, not a
    measurement.
    """
    # ---- arguments -------------------------------------------------------
    if not caps:
        return err("caps is empty", "ARG_REQUIRED",
                   hint="each cap needs c_farad, and optionally esr_ohm / esl_h / "
                        "count / at ('die' or 'bulk')")
    try:
        r_series_ohm = float(r_series_ohm or 0.0)
        vr_series_h = float(vr_series_h or 0.0)
        plane_r_ohm = float(plane_r_ohm or 0.0)
        plane_l_h = float(plane_l_h or 0.0)
        step_a = float(step_a)
        preload_a = float(preload_a or 0.0)
        band_pct = float(settle_band_pct)
        samples = int(samples)
    except (TypeError, ValueError):
        return err("a scalar argument is not a number", "ARG_INVALID")
    for name, val in (("r_series_ohm", r_series_ohm), ("vr_series_h", vr_series_h),
                      ("plane_r_ohm", plane_r_ohm), ("plane_l_h", plane_l_h),
                      ("preload_a", preload_a)):
        if val != val or val < 0:
            return err("%s must be >= 0" % name, "ARG_RANGE")
    if abs(step_a) < 1e-15:
        return err("step_a is zero: a zero step measures nothing", "ARG_INVALID")
    if band_pct <= 0:
        return err("settle_band_pct must be > 0", "ARG_RANGE")
    if samples < 50:
        return err("samples must be >= 50", "ARG_RANGE")
    probes_s = []
    if probe_at_ns is not None:
        if not isinstance(probe_at_ns, (list, tuple)):
            return err("probe_at_ns must be a list of times in ns", "ARG_INVALID")
        for i, t in enumerate(probe_at_ns):
            try:
                tv = float(t) * 1e-9
            except (TypeError, ValueError):
                return err("probe_at_ns[%d] is not a number" % i, "ARG_INVALID")
            if tv < 0:
                return err("probe_at_ns[%d] is negative" % i, "ARG_RANGE")
            probes_s.append(tv)
    rise_s = 0.0
    if rise_ns is not None:
        rise_s = float(rise_ns) * 1e-9
        if rise_s < 0:
            return err("rise_ns must be >= 0", "ARG_RANGE")

    # ---- group identical parallel caps -----------------------------------
    groups = {}
    has_bulk = False
    c_total = 0.0
    for i, cap in enumerate(caps):
        if not isinstance(cap, dict):
            return err("cap %d is not an object" % i, "ARG_INVALID")
        if cap.get("c_farad") is None:
            return err("cap %d has no c_farad" % i, "ARG_REQUIRED")
        c = float(cap["c_farad"])
        if c <= 0:
            return err("cap %d has a non-positive capacitance" % i, "ARG_INVALID")
        esr = float(cap.get("esr_ohm") or 0.0)
        esl = float(cap.get("esl_h") or 0.0)
        if esr < 0 or esl < 0:
            return err("cap %d has a negative esr/esl" % i, "ARG_INVALID")
        n = int(cap.get("count") or 1)
        if n <= 0:
            return err("cap %d has count <= 0" % i, "ARG_INVALID")
        at = cap.get("at") or "die"
        if at not in ("die", "bulk"):
            return err("cap %d: at must be 'die' or 'bulk', not %r" % (i, at),
                       "ARG_INVALID")
        if at == "bulk":
            has_bulk = True
        key = (at, c, esr, esl)
        groups[key] = groups.get(key, 0) + n
        c_total += c * n
    if has_bulk and plane_r_ohm <= 0.0 and plane_l_h <= 0.0:
        return err("caps at 'bulk' with plane_r_ohm = 0 and plane_l_h = 0 are just "
                   "die caps: the two nodes would be the same node", "ARG_INVALID",
                   hint="give the R and L of the path between the bulk caps and the "
                        "die, or put those caps at 'die'")
    if (not has_bulk and r_series_ohm + plane_r_ohm <= 0.0
            and vr_series_h + plane_l_h <= 0.0):
        return err("r_series_ohm = vr_series_h = plane_r_ohm = plane_l_h = 0 means the "
                   "rail is bolted to an ideal source: the droop would be zero by "
                   "construction, which is not a measurement", "ARG_INVALID",
                   hint="give the regulator's output resistance (the DC droop is "
                        "I*R_series) and, if you know it, its output inductance")

    # The plane is between the regulator and the die.  When every cap sits at the
    # die there is no second node to put it on, but the load current still has to
    # cross it: dropping it there would answer "0.01 V" to a board whose plane is
    # 2 mohm, so it is folded into the regulator branch instead (same answer as a
    # series sum, and nothing is silently ignored).
    l_path = vr_series_h + plane_l_h
    l_any = max([l_path] + [(k[3] + l_path) for k in groups])
    if l_any > 0 and rise_s <= 0:
        return err("rise_ns is required as soon as the network has inductance: an "
                   "ideal current step into L gives V = L*dI/dt, so the peak would be "
                   "set by the time step instead of by the board",
                   "ARG_REQUIRED",
                   hint="give the load edge as rise_ns (20 A/us = 50 ns per amp of "
                        "step), or set every esl_h / vr_series_h / plane_l_h to 0 to "
                        "ask the ESR-only question")

    # ---- natural time scales ---------------------------------------------
    r_path = r_series_ohm + plane_r_ohm
    periods = []
    rcs = []
    for (at, c, esr, esl), n in groups.items():
        ct = c * n
        esr_t = esr / n
        esl_t = (esl + l_path) / n
        if esl_t > 0:
            periods.append(2.0 * math.pi * math.sqrt(esl_t * ct))
        rcs.append(esr_t * ct)
    if l_path > 0 and c_total > 0:
        periods.append(2.0 * math.pi * math.sqrt(l_path * c_total))
    fast_period = min(periods) if periods else None
    scales = [p / 40.0 for p in periods] + [t / 40.0 for t in rcs if t > 0]
    if rise_s > 0:
        scales.append(rise_s / 20.0)
    tau_slow = r_path * c_total
    auto_stop = max(20.0 * tau_slow, 20.0 * rise_s,
                    20.0 * fast_period if fast_period else 0.0)
    if auto_stop <= 0:
        return err("no time scale to integrate over", "ARG_INVALID")
    if t_stop_ns is not None:
        t_stop = float(t_stop_ns) * 1e-9
        if t_stop <= 0:
            return err("t_stop_ns must be > 0", "ARG_RANGE")
    else:
        t_stop = auto_stop
    h_last = t_stop / float(samples)
    h_first = min(scales) if scales else h_last / 1000.0
    if h_first > h_last:
        h_first = h_last
    if h_first <= 0:
        return err("the adaptive step came out as zero", "ARG_INVALID")

    def _count_steps(h0):
        t, h, n = 0.0, h0, 0
        while t < t_stop and n <= 400000:
            n += 1
            t += h
            h = min(h * 1.02, h_last)
        return n

    n_steps = _count_steps(h_first)
    if n_steps > 400000:
        return err("this needs more than 400000 time steps", "TOO_LARGE",
                   hint="raise samples (coarser tail), give t_stop_ns, or drop the "
                        "smallest esl_h - the ESR/ESL of one small cap sets the step "
                        "even when it carries no charge")

    die = 0
    bulk = 1 if has_bulk else None
    n_nodes = 2 if has_bulk else 1

    def _simulate(h0):
        br = []
        vrm_node = bulk if has_bulk else die
        br.append({"a": None, "b": vrm_node,
                   "R": r_path if not has_bulk else r_series_ohm,
                   "L": l_path if not has_bulk else vr_series_h,
                   "C": None, "tag": "vrm"})
        if has_bulk:
            br.append({"a": bulk, "b": die, "R": plane_r_ohm,
                       "L": plane_l_h, "C": None, "tag": "plane"})
        for (at, c, esr, esl), n in sorted(groups.items(),
                                           key=lambda kv: (kv[0][0], -kv[0][1])):
            br.append({"a": die if at == "die" else bulk, "b": None,
                       "R": esr / n, "L": esl / n, "C": c * n, "tag": "cap"})
        # initial state: DC at preload (caps open, inductors carry the load)
        v0 = [0.0] * n_nodes
        v0[die] = -preload_a * r_path
        if has_bulk:
            v0[bulk] = -preload_a * r_series_ohm
        i_prev = [0.0] * len(br)
        u_prev = [0.0] * len(br)
        for k, b in enumerate(br):
            if b["tag"] != "cap":
                i_prev[k] = preload_a
            else:
                u_prev[k] = v0[b["a"]]
        u_init = list(u_prev)
        g_br = [0.0] * len(br)
        kL = [0.0] * len(br)
        ku = [0.0] * len(br)
        ts, vs, il = [0.0], [v0[die]], [preload_a]
        t, h, steps = 0.0, h0, 0
        while t < t_stop:
            t_next = min(t + h, t_stop)
            hh = t_next - t
            for k, b in enumerate(br):
                if b["C"] is None:
                    den = b["R"] + b["L"] / hh
                    g_br[k] = 1.0 / den
                    kL[k] = (b["L"] / hh) * g_br[k]
                    ku[k] = 0.0
                else:
                    den = b["R"] + b["L"] / hh + hh / b["C"]
                    g_br[k] = 1.0 / den
                    kL[k] = (b["L"] / hh) * g_br[k]
                    ku[k] = -g_br[k]
            G = [[0.0] * n_nodes for _ in range(n_nodes)]
            j = [0.0] * n_nodes
            isrc = [0.0] * len(br)
            for k, b in enumerate(br):
                g = g_br[k]
                isrc[k] = kL[k] * i_prev[k] + ku[k] * u_prev[k]
                a, bb = b["a"], b["b"]
                if a is not None:
                    G[a][a] += g
                if bb is not None:
                    G[bb][bb] += g
                if a is not None and bb is not None:
                    G[a][bb] -= g
                    G[bb][a] -= g
                if isrc[k]:
                    if a is not None:
                        j[a] -= isrc[k]
                    if bb is not None:
                        j[bb] += isrc[k]
            frac = 1.0 if rise_s <= 0 else min(t_next / rise_s, 1.0)
            i_load = preload_a + step_a * frac
            j[die] -= i_load
            try:
                if n_nodes == 1:
                    v = [j[0] / G[0][0]]
                else:
                    v = _solve_linear(G, j)
            except ZeroDivisionError:
                return None, "the network is singular (no path from the die node)"
            for k, b in enumerate(br):
                va = v[b["a"]] if b["a"] is not None else 0.0
                vb = v[b["b"]] if b["b"] is not None else 0.0
                i_new = g_br[k] * (va - vb) + isrc[k]
                if b["C"] is not None:
                    u_prev[k] = u_prev[k] + (hh / b["C"]) * i_new
                i_prev[k] = i_new
            t = t_next
            h = min(h * 1.02, h_last)
            steps += 1
            ts.append(t)
            vs.append(v[die])
            il.append(i_prev[0])
        # ---- scalars from the waveform
        v_final = vs[-1]
        dc_expected = -preload_a * r_path - step_a * r_path
        span = ts[-1] - ts[0]
        if span <= 0:
            return None, "the time grid collapsed"
        if step_a > 0:
            k_min = min(range(len(vs)), key=lambda i: vs[i])
        else:
            k_min = max(range(len(vs)), key=lambda i: vs[i])
        peak_v = vs[k_min]
        t_peak = ts[k_min]
        scale = max(abs(dc_expected), abs(peak_v - vs[0]))
        tol = band_pct / 100.0 * scale
        settle = 0.0
        for i in range(len(vs) - 1, -1, -1):
            if abs(vs[i] - dc_expected) > tol:
                settle = ts[i]
                break
        under = (dc_expected - peak_v) if step_a > 0 else (peak_v - dc_expected)
        if under < 0:
            under = 0.0
        # charge bookkeeping: what the caps gave up vs what the load took net
        # a cap that goes DOWN in voltage has delivered charge, and its branch
        # current (bus -> ground) is negative, so the state term is -C*du.
        q_cap_state = 0.0
        for k, b in enumerate(br):
            if b["C"] is not None:
                q_cap_state -= b["C"] * (u_prev[k] - u_init[k])
        # right-endpoint sums, NOT trapezoid: those are the quadrature the backward
        # Euler state update itself uses, so this identity closes to round-off if
        # (and only if) every branch made it into the node equation.
        q_vrm = 0.0
        q_load = 0.0
        for i in range(1, len(ts)):
            dh = ts[i] - ts[i - 1]
            q_vrm += il[i] * dh
            f1 = 1.0 if rise_s <= 0 else min(ts[i] / rise_s, 1.0)
            q_load += (preload_a + step_a * f1) * dh
        q_missing = q_load - q_vrm          # must have come out of the caps
        di_max = 0.0
        for i in range(1, len(ts)):
            dh = ts[i] - ts[i - 1]
            if dh > 0:
                di_max = max(di_max, abs(il[i] - il[i - 1]) / dh)
        return {"ts": ts, "vs": vs, "il": il, "steps": steps,
                "peak_v": peak_v, "t_peak": t_peak, "v_final": v_final,
                "dc_expected": dc_expected, "settle": settle, "under": under,
                "q_missing": q_missing, "q_cap_state": q_cap_state,
                "di_max": di_max,
                "h_first": h0}, None

    sim, why = _simulate(h_first)
    if sim is None:
        return err(why, "NOT_APPLICABLE")
    peak = sim["peak_v"]
    refine_pct = None
    if refine:
        sim2, why2 = _simulate(h_first / 2.0)
        if sim2 is not None and abs(peak) > 0:
            refine_pct = abs(sim2["peak_v"] - peak) / abs(peak) * 100.0
    # ---- report
    ts, vs = sim["ts"], sim["vs"]
    keep = []
    n = len(ts)
    head, tail = 24, 12
    for i in range(min(head, n)):
        keep.append(i)
    body = max(0, n - head - tail)
    stride = max(1, body // max(1, 120))
    for i in range(head, n - tail, stride):
        keep.append(i)
    for i in range(max(head, n - tail), n):
        keep.append(i)
    kp = min(range(len(vs)), key=lambda i: vs[i]) if step_a > 0 else \
        max(range(len(vs)), key=lambda i: vs[i])
    keep.append(kp)
    keep = sorted(set(keep))
    probes = None
    if probes_s:
        probes = []
        for tv in probes_s:
            if tv > ts[-1] * (1.0 + 1e-12):
                return err("probe_at_ns asks for %s s but the answer stops at %s s"
                           % ("%.6g" % tv, "%.6g" % ts[-1]), "ARG_RANGE",
                           hint="raise t_stop_ns, or drop that probe")
            lo, hi = 0, len(ts) - 1
            while hi - lo > 1:
                mid = (lo + hi) // 2
                if ts[mid] <= tv:
                    lo = mid
                else:
                    hi = mid
            span = ts[hi] - ts[lo]
            f = 0.0 if span <= 0 else (tv - ts[lo]) / span
            probes.append({"t_s": _sig(tv),
                           "v_v": round(vs[lo] + f * (vs[hi] - vs[lo]), 9)})
    warnings = []
    if refine_pct is not None and refine_pct > 5.0:
        warnings.append("halving the time step moves the peak by %.1f%% - the peak "
                        "is a grid artefact at this `samples`; raise it"
                        % refine_pct)
    if sim["settle"] >= 0.98 * ts[-1]:
        warnings.append("the rail had not settled inside t_stop = %s s; the last "
                        "point is still %.3g V from the DC answer"
                        % ("%.6g" % ts[-1], vs[-1] - sim["dc_expected"]))
    if sim["v_final"] != 0 and abs(vs[0] - sim["v_final"]) < 1e-12:
        warnings.append("the waveform did not move: check that the caps are on the "
                        "node the load pulls on")
    tail_settled = sim["settle"] < 0.98 * ts[-1]
    dc_err = (vs[-1] - sim["dc_expected"])
    dc_rel = (abs(dc_err) / abs(sim["dc_expected"])) if sim["dc_expected"] else 0.0
    value = {
        "peak_v": round(peak, 9),
        "peak_excursion_v": round(abs(peak - vs[0]), 9),
        "t_peak_s": _sig(sim["t_peak"]),
        "v_initial_v": round(vs[0], 9),
        "v_final_v": round(sim["v_final"], 9),
        "dc_expected_v": round(sim["dc_expected"], 9),
        "dc_check_rel": _sig(dc_rel),
        "tail_settled": tail_settled,
        "settle_time_s": _sig(sim["settle"]),
        "undershoot_v": round(sim["under"], 9),
        "undershoot_pct_of_final": (round(100.0 * sim["under"] /
                                          abs(sim["dc_expected"]), 4)
                                    if sim["dc_expected"] else None),
        "dq_from_caps_c": round(sim["q_missing"], 15),
        "charge_balance_rel": _sig(
            abs(sim["q_cap_state"] - sim["q_missing"]) /
            max(abs(sim["q_missing"]), 1e-18)
            if abs(sim["q_missing"]) > 1e-15 else 0.0),
        "vrm_di_dt_a_per_us": _sig(sim["di_max"] * 1e-6),
        "steps": sim["steps"],
        "h_first_s": _sig(sim["h_first"]),
        "h_last_s": _sig(h_last),
        "h_refine_peak_delta_pct": (None if refine_pct is None
                                    else round(refine_pct, 4)),
        "t_stop_s": _sig(t_stop),
        "nodes": n_nodes,
        "branches": len(groups) + (2 if has_bulk else 1),
        "c_total_farad": _sig(c_total),
        "t_s": [_sig(ts[i]) for i in keep],
        "v_rail_v": [round(vs[i], 9) for i in keep],
        "probes": probes,
    }
    return ok(value=value, unit="V", model=STEP_MODEL,
              basis="MNA on R-L-C branches (one or two nodes: the die, and the bulk "
                    "cap bus when any cap says at='bulk'), backward Euler, "
                    "v_end = -I_end*R_path checked against the simulated tail; the "
                    "load is a current sink I(t) = preload + step*min(t/rise,1)",
              precision="v_initial_v is the state BEFORE the step (the preload DC "
                        "point), and for a monotone droop t_peak_s is the end of the "
                        "window, because that is where the minimum is. First-order in "
                        "the time step, and the step is adaptive "
                        "(fastest branch period/40 growing 2%/step). The peak is "
                        "re-run at half the step: `h_refine_peak_delta_pct` is the "
                        "honest error bar, and above 5% the peak is not a "
                        "measurement. Backward Euler DAMPING makes a ring decay "
                        "faster than physics, so a measured Q is a lower bound",
              assumptions={"step_a": step_a, "preload_a": preload_a,
                           "r_series_ohm": r_series_ohm,
                           "vr_series_h": vr_series_h,
                           "rise_ns": (rise_s * 1e9 if rise_s else 0.0),
                           "settle_band_pct": band_pct,
                           "caps": len(caps), "c_total_farad": _sig(c_total)},
              extra={
                  "warnings": warnings,
                  "not_computed": [
                      "regulator LOOP (this VRM is a Thevenin R+L: no bandwidth, no "
                      "phase margin, no current limit, no droop sharing with other "
                      "rails)",
                      "plane spreading as a distributed effect (the caps are lumped "
                      "on two nodes; that is board_ir_drop / pdn_plane_impedance)",
                      "die-package inductance and on-die capacitance",
                      "capacitor DC bias / temperature / age derating",
                      "measurement noise, and any load profile other than one step"],
                  "the_dc_anchor": ("the tail is compared against -I*R_path, which is "
                                    "what the same network must converge to: a wrong "
                                    "companion sign or a drifting integrator shows up "
                                    "as dc_check_rel, not as a plausible curve"),
              })

def decoupling_plan(vdd_v, load_current_a, ripple_mv, band_hz, caps,
                    r_series_ohm=0.0, regulator_bandwidth_hz=None):
    """Will this set of caps hold the rail inside `ripple_mv` over `band_hz`?

    This is the question that actually gets asked ("is 2x10u + 5x100n enough?")
    and the honest answer needs a current, a band and a ripple target. Without
    all three the answer is a guess, so all three are required.

    The trap this function exists to avoid: at the LOW end of a band a bulk cap
    is not a short (10 uF at 1 kHz is 15.9 ohm), so a wide band always "fails"
    on the caps alone and the number says nothing about the caps. Below the
    regulator's crossover the regulator - not the capacitor bank - is what holds
    the rail. So the verdict is only `pass` / `fail` when the worst point is
    strictly inside the judged band; when the worst point sits on the low edge
    the verdict is `indeterminate` and says which knob decides it. Pass
    `regulator_bandwidth_hz` to judge only the region the caps own.
    """
    if not band_hz or len(band_hz) != 2:
        return err("band_hz must be [f_low, f_high]", "ARG_REQUIRED",
                   hint="a decoupling answer without a band is a DC answer")
    f0, f1 = float(band_hz[0]), float(band_hz[1])
    if f0 <= 0 or f1 <= f0:
        return err("band_hz needs 0 < f_low < f_high", "ARG_RANGE")
    try:
        vdd = float(vdd_v)
        i_load = float(load_current_a)
        ripple_v = float(ripple_mv) * 1e-3
    except (TypeError, ValueError):
        return err("vdd_v, load_current_a and ripple_mv must be numbers",
                   "ARG_INVALID")
    if i_load <= 0:
        return err("load_current_a must be > 0", "ARG_RANGE")
    if ripple_v <= 0:
        return err("ripple_mv must be > 0", "ARG_RANGE")
    if vdd > 0 and ripple_v >= vdd:
        return err("ripple_mv is not smaller than the rail itself", "ARG_RANGE")
    fbw = None
    if regulator_bandwidth_hz is not None:
        try:
            fbw = float(regulator_bandwidth_hz)
        except (TypeError, ValueError):
            return err("regulator_bandwidth_hz must be a number", "ARG_INVALID")
        if fbw <= 0:
            return err("regulator_bandwidth_hz must be > 0", "ARG_RANGE")
    judge_lo = f0 if fbw is None else max(f0, fbw)
    if judge_lo >= f1:
        return err("the judged band is empty: regulator_bandwidth_hz %.3g is at or "
                   "above f_high %.3g" % (fbw, f1), "ARG_RANGE",
                   hint="the capacitors own nothing above the regulator's crossover "
                        "in this band")
    freqs = [judge_lo * (f1 / judge_lo) ** (k / 40.0) for k in range(41)]
    r = pdn_impedance(freqs, caps, r_series_ohm=r_series_ohm)
    if r["status"] != "ok":
        return r
    limit = ripple_v / i_load
    points = r["value"]["points"]
    worst = max(points, key=lambda p: p["z_ohm"])
    pk = worst["z_ohm"] * i_load * 1e3
    edge = abs(worst["freq_hz"] - judge_lo) <= judge_lo * 1e-9
    warnings = []
    if edge:
        verdict = "indeterminate"
        warnings.append(
            "the worst |Z| sits on the low edge of the judged band (%.4g Hz): "
            "below that frequency the regulator, not the capacitor bank, holds "
            "the rail. Pass regulator_bandwidth_hz (or raise f_low) to judge only "
            "the region the caps own - as it stands this number cannot tell you "
            "whether the caps are adequate" % judge_lo)
    else:
        verdict = "pass" if worst["z_ohm"] <= limit else "fail"
    return ok(value={"verdict": verdict,
                     "passes": (verdict == "pass"),
                     "worst_z_ohm": round(worst["z_ohm"], 9),
                     "worst_z_hz": round(worst["freq_hz"], 3),
                     "z_budget_ohm": round(limit, 9),
                     "predicted_ripple_mv_pp": round(pk, 6),
                     "ripple_budget_mv": float(ripple_mv),
                     "z_budget_as_pct_of_vdd": (round(100.0 * limit / vdd, 6)
                                                if vdd > 0 else None),
                     "requested_band_hz": [f0, f1],
                     "judged_band_hz": [judge_lo, f1],
                     "regulator_bandwidth_hz": fbw,
                     "curve": points},
              unit="ohm", model="parallel_series_rlc_band",
              basis="Z_budget = allowed_ripple / load_current; the judged band is "
                    "swept logarithmically (41 points) and the WORST point is "
                    "compared with the budget; verdict is indeterminate when that "
                    "point is the band's lower edge",
              precision="a lumped answer: it can say a plan is clearly insufficient, "
                        "and cannot say a plan is sufficient, because the plane "
                        "anti-resonance and the die-package inductance are missing. "
                        "It also only sees the caps - not the regulator loop, not the "
                        "load's own di/dt shape",
              assumptions={"vdd_v": vdd, "load_current_a": i_load,
                           "band_hz": [f0, f1], "caps": len(caps),
                           "regulator_bandwidth_hz": fbw},
              extra={"warnings": warnings,
                     "not_computed": ["plane anti-resonance", "die-package inductance",
                                      "capacitor derating", "transient di/dt shape",
                                      "regulator loop / load transient response"]})
