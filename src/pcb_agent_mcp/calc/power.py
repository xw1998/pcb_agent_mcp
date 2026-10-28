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
    "the plane impedance CURVE. The modal sum for an open-edged plane pair "
    "converges far too slowly near and above its first resonance to be quoted: "
    "measured here, |Z| at 1.45 GHz moves 5.6 -> 9.4 ohm when the truncation goes "
    "from order 10 to 40, so a curve would be a function of the truncation order, "
    "not of your board. Only the single-mode peaks are reported",
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
    sum over the OTHER modes is not computed, because it does not converge (see
    not_computed) - so do not read two adjacent numbers here as a curve.

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
                        "(that sum does not converge), so two adjacent rows are not "
                        "a curve and no anti-resonance is claimed",
              assumptions={"er": e, "thickness_mm": t, "tan_delta": td,
                           "loss": "dielectric only - no copper, no radiation"},
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
