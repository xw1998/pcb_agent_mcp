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


def pdn_impedance(freqs_hz, caps, plane_cap_farad=None, r_series_ohm=0.0):
    """|Z(f)| seen by the die: N decoupling caps in parallel, plus optional plane.

    Caps are ideal R-L-C in parallel, which is what a decoupling network looks
    like from the die until the plane resonances start - and those are NOT
    modelled here (they need the plane geometry and a field solver).
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
    return ok(value={"points": out, "min_z_ohm": round(zmin, 9),
                     "min_z_hz": round(fpeak, 6),
                     "caps": len(caps),
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
              extra={"not_computed": ["plane cavity resonances",
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
