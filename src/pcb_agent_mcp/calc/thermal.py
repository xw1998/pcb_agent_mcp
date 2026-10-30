"""Thermal: how hot will it get, and through what.

Same discipline as the rest of `calc`: every number comes from a geometry or a
material property the caller states, never from a "typical" buried in the code.
Where a real datasheet value cannot be derived (convection coefficient, Rth of a
package, TIM thickness) the argument is required and the envelope says so.

Units are fixed: mm, um, K (or C for temperatures - the difference is what is
used), W, W/(m*K), W/(m^2*K). Nothing is silently converted.

The three questions this layer answers:
  * steady state through a stack of layers        -> thermal_stack
  * steady state through a field of vias          -> via_array_thermal
  * steady state in TWO dimensions (hot spot vs average, spreading)
                                                  -> plate_temperature
  * transient (does it survive a 10 s overload)   -> thermal_transient
  * what heatsink do I need                       -> heatsink_select
"""
import math

from .result import ok, err

K_CU = 385.0     # W/(m*K)
K_FR4 = 0.30     # W/(m*K), and it is a range (0.2..0.4 depending on resin/resin+glass)
K_AL = 200.0     # W/(m*K), 6063-ish
K_SAC305 = 58.0  # W/(m*K) solder

SIGMA_SB = 5.670374e-8   # W/(m^2*K^4)


def _num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v != v or v in (float("inf"), float("-inf")):
        return None
    return v


# ------------------------------------------------------------------ 1D stack

def thermal_stack(layers, area_mm2, power_w=None, t_ambient_c=None,
                  h_w_m2k=None):
    """1-D conduction through a stack of parallel layers, plus a single Rth.

    layers: list of {"name":..., "thickness_mm":..., "k_w_mk":...}
            (k may be "cu" / "fr4" / "al" / "sac305" as a shorthand)
    area_mm2: the heat-crossing area. This is the number people get wrong: for
              a via it is the barrel annulus, not the drill circle.

    Rth = sum(t_i / (k_i * A)). That is all a 1-D stack is - the value of this
    function is that it refuses to invent k, refuses a zero area, and states the
    spreading assumption it does not model.
    """
    if not layers:
        return err("layers is empty", "ARG_REQUIRED",
                   hint='each layer needs {"name", "thickness_mm", "k_w_mk"}')
    area = _num(area_mm2)
    if area is None:
        return err("area_mm2 must be a number", "ARG_INVALID")
    if area <= 0:
        return err("area_mm2 must be > 0", "ARG_RANGE")
    alias = {"cu": K_CU, "copper": K_CU, "fr4": K_FR4, "al": K_AL,
             "aluminium": K_AL, "aluminum": K_AL, "sac305": K_SAC305}
    a_m2 = area * 1e-6
    total = 0.0
    detail = []
    for k, lay in enumerate(layers):
        if not isinstance(lay, dict):
            return err("layer %d is not an object" % k, "ARG_INVALID")
        t = _num(lay.get("thickness_mm"))
        kk = lay.get("k_w_mk")
        if isinstance(kk, str):
            kk = alias.get(kk.lower().strip())
            if kk is None:
                return err("layer %d: unknown material %r - pass a number in "
                           "W/(m*K) instead of guessing" %
                           (k, lay.get("k_w_mk")), "ARG_INVALID")
        kk = _num(kk)
        if t is None or kk is None:
            return err("layer %d needs numeric thickness_mm and k_w_mk" % k,
                       "ARG_INVALID")
        if t <= 0 or kk <= 0:
            return err("layer %d: thickness_mm and k_w_mk must be > 0" % k,
                       "ARG_RANGE")
        r = (t * 1e-3) / (kk * a_m2)
        total += r
        detail.append({"index": k, "name": str(lay.get("name", "layer%d" % k)),
                       "thickness_mm": t, "k_w_mk": kk,
                       "rth_k_per_w": round(r, 9),
                       "share_pct": None})
    for d in detail:
        d["share_pct"] = round(100.0 * d["rth_k_per_w"] / total, 4) if total else None
    extra = {"not_computed": ["lateral heat spreading (this is strictly 1-D)",
                              "contact resistance between layers",
                              "temperature-dependent k"]}
    value = {"rth_k_per_w": round(total, 9),
             "g_thermal_w_per_k": round(1.0 / total, 9) if total else None,
             "area_mm2": area,
             "layers": detail}
    if power_w is not None:
        p = _num(power_w)
        if p is None or p < 0:
            return err("power_w must be a non-negative number", "ARG_INVALID")
        value["delta_t_k"] = round(p * total, 6)
        value["power_w"] = p
        if t_ambient_c is not None:
            ta = _num(t_ambient_c)
            if ta is None:
                return err("t_ambient_c must be a number", "ARG_INVALID")
            value["t_hot_side_c"] = round(ta + p * total, 6)
            value["t_ambient_c"] = ta
    if h_w_m2k is not None:
        h = _num(h_w_m2k)
        if h is None or h <= 0:
            return err("h_w_m2k must be > 0", "ARG_RANGE")
        value["convection_rth_k_per_w"] = round(1.0 / (h * 2.0 * a_m2), 9)
        value["note_convection"] = ("assuming BOTH faces see h over the same area; "
                                    "add it in series for a total, it is not "
                                    "already included in rth_k_per_w")
    return ok(value=value, unit="K/W", model="series_1d_conduction",
              basis="Rth = sum(t_i / (k_i * A)); dT = P * Rth",
              precision="exact for the 1-D geometry given. The error is the model: "
                        "spreading, contact and k(T) are not in it, and no real "
                        "stack is 1-D unless the area is much larger than the "
                        "thickness AND the heat really enters over that whole area",
              assumptions={"area_mm2": area, "layers": len(detail)},
              extra=extra)


# ------------------------------------------------------------------ vias

def _barrel_profile_rth(drill_mm, segments, length_mm, k_cu):
    """Series resistance of a barrel whose wall thickness varies along it.

    R = sum over segments of L_i / (k_cu * A_i), A_i = pi*((d/2)^2 - (d/2-t_i)^2).

    Returns (rth, detail, equivalent_uniform_plating_um) on success and
    (None, None, None, message, code) on failure - a 5-tuple either way, so the
    caller checks the first element only.
    """
    r_out = drill_mm / 2.0
    detail = []
    total_r = 0.0
    total_len = 0.0
    for idx, seg in enumerate(segments):
        if not isinstance(seg, dict):
            return (None, None, None, "barrel_profile segment %d is not an object "
                    '{"length_mm", "plating_um"}' % idx, "ARG_INVALID")
        li = _num(seg.get("length_mm"))
        ti = _num(seg.get("plating_um"))
        if li is None or ti is None:
            return (None, None, None,
                    "barrel_profile segment %d needs numeric length_mm and "
                    "plating_um" % idx, "ARG_INVALID")
        if li <= 0 or ti <= 0:
            return (None, None, None,
                    "barrel_profile segment %d: length_mm and plating_um must be "
                    "> 0" % idx, "ARG_RANGE")
        ti_mm = ti / 1000.0
        if 2.0 * ti_mm >= drill_mm:
            return (None, None, None,
                    "barrel_profile segment %d: plating_um is not smaller than the "
                    "drill radius - that segment would be solid copper and the "
                    "annulus formula stops meaning anything" % idx, "ARG_RANGE")
        r_in = r_out - ti_mm
        a_mm2 = math.pi * (r_out ** 2 - r_in ** 2)
        r_seg = (li * 1e-3) / (float(k_cu) * a_mm2 * 1e-6)
        total_r += r_seg
        total_len += li
        detail.append({"index": idx, "length_mm": li, "plating_um": ti,
                       "barrel_area_mm2": a_mm2, "rth_k_per_w": r_seg})
    if abs(total_len - length_mm) > 1e-6:
        return (None, None, None,
                "barrel_profile lengths add up to %.12g mm but length_mm is "
                "%.12g mm - the profile has to span the whole barrel, otherwise "
                "the missing or extra copper is silently free" %
                (total_len, length_mm), "ARG_RANGE")
    # the uniform wall that would conduct the same: the same total copper area is
    # not the same resistance (R is a series sum), so solve for the area instead
    a_eq_mm2 = (total_len * 1e-3) / (float(k_cu) * total_r) * 1e6
    r_in_eq = math.sqrt(max(0.0, r_out ** 2 - a_eq_mm2 / math.pi))
    t_eq_um = (r_out - r_in_eq) * 1000.0
    return (total_r, detail, t_eq_um, None, None)


def _interface_rth(interface):
    """One series layer at the end of the field, from stated material numbers.

    R = interfaces * thickness / (k * area). Returns (rth, echo, msg, code).
    """
    if not isinstance(interface, dict):
        return (None, None, "interface must be an object "
                '{"area_mm2", "thickness_um", "k_w_mk", "interfaces"?}',
                "ARG_INVALID")
    a = _num(interface.get("area_mm2"))
    th = _num(interface.get("thickness_um"))
    k = _num(interface.get("k_w_mk"))
    for name, v in (("area_mm2", a), ("thickness_um", th), ("k_w_mk", k)):
        if v is None:
            return (None, None, "interface needs numeric %s (a TIM pad is a "
                    "thickness, a conductivity and an area; there is no table of "
                    "TIMs in this file)" % name, "ARG_INVALID")
        if v <= 0:
            return (None, None, "interface: %s must be > 0" % name, "ARG_RANGE")
    faces = interface.get("interfaces")
    if faces is None:
        faces = 1
    try:
        nf = int(faces)
    except (TypeError, ValueError):
        return (None, None, "interface: interfaces must be an integer 1..2",
                "ARG_INVALID")
    if nf != float(faces) or nf < 1 or nf > 2:
        return (None, None, "interface: interfaces must be the INTEGER 1 or 2 "
                "(one pad under the sink, or two - one at each end of the "
                "barrel)", "ARG_RANGE")
    r = nf * (th * 1e-6) / (k * a * 1e-6)
    echo = {"area_mm2": a, "thickness_um": th, "k_w_mk": k, "interfaces": nf,
            "rth_k_per_w": round(r, 9)}
    return (r, echo, None, None)


def via_array_thermal(drill_mm, plating_um, length_mm, count=1,
                      pitch_mm=None, k_cu=K_CU, k_board=K_FR4,
                      barrel_profile=None, interface=None):
    """Thermal resistance through a field of plated vias, copper only.

    A via is a thin tube of copper: the cross-section is the ANNULUS
    pi*((d/2)^2 - (d/2 - t)^2), not the drill area. Using the drill area is the
    classic factor-of-10 error that makes a 0.3 mm via look like a heat pipe.

    If `pitch_mm` is given, the board's own FR4 in the cell around each via is
    added in PARALLEL - that is the honest picture of a via field, because the
    epoxy between the barrels also conducts (badly).

    The default answer is deliberately OPTIMISTIC: one uniform wall, no pad
    interfaces. The two optional arguments let the caller say by how much
    instead of just being warned.

    * `barrel_profile` = [{"length_mm": .., "plating_um": ..}, ...] walking along
      the barrel from one end to the other. Plating really is thinner mid-barrel
      (that is why IPC-4761 via structures exist at all), so a 3-segment profile
      models the wall the way it comes out of the plating bath instead of one
      uniform number. The segments are in SERIES: R = sum L_i/(k_cu*A_i), so the
      thinnest segment dominates no matter how short it is. The lengths have to
      add up to `length_mm`. Passing nothing keeps the single uniform barrel,
      bit for bit. With a profile, `plating_um` becomes optional and is only
      used for the comparison "what if it had been uniform".
    * `interface` = {"area_mm2": .., "thickness_um": .., "k_w_mk": ..,
      "interfaces": 1|2} is a series layer after the field: a TIM pad under a
      heatsink, a gap filler, thermal tape. It is computed from the three
      material numbers you state - this file still ships no TIM table.
    """
    d = _num(drill_mm)
    t = _num(plating_um)
    L = _num(length_mm)
    try:
        n = int(count)
    except (TypeError, ValueError):
        return err("count must be an integer", "ARG_INVALID")
    if d is None or L is None:
        return err("drill_mm and length_mm must be numbers", "ARG_INVALID")
    if plating_um is not None and t is None:
        return err("plating_um must be a number (or leave it out and give a "
                   "barrel_profile instead)", "ARG_INVALID")
    if t is None and barrel_profile is None:
        return err("no wall: give plating_um, or barrel_profile with a "
                   "plating_um on every segment", "ARG_INVALID")
    if d <= 0 or L <= 0:
        return err("drill_mm and length_mm must be > 0", "ARG_RANGE")
    if t is not None and t <= 0:
        return err("plating_um must be > 0", "ARG_RANGE")
    if n < 1:
        return err("count must be >= 1", "ARG_RANGE")
    if t is not None:
        t_mm = t / 1000.0
        if 2.0 * t_mm >= d:
            return err("plating_um is not smaller than the drill radius - the "
                       "barrel would be solid and the annulus formula stops "
                       "meaning anything", "ARG_RANGE")
    r_out = d / 2.0
    a_mm2 = None
    if t is not None:
        r_in = r_out - t / 1000.0
        a_mm2 = math.pi * (r_out ** 2 - r_in ** 2)
    # ---- the barrel itself
    r_prof = None
    seg_detail = None
    t_eq_um = None
    if barrel_profile is not None:
        if not isinstance(barrel_profile, (list, tuple)) or not barrel_profile:
            return err("barrel_profile must be a non-empty list of "
                       '{"length_mm", "plating_um"} segments', "ARG_INVALID",
                       hint="a 1.6 mm barrel plated 25 um at the ends and 15 um in "
                            "the middle is [{\"length_mm\": 0.55, \"plating_um\": "
                            "25}, {\"length_mm\": 0.5, \"plating_um\": 15}, "
                            "{\"length_mm\": 0.55, \"plating_um\": 25}]")
        r_prof, seg_detail, t_eq_um, msg, code = _barrel_profile_rth(
            d, list(barrel_profile), L, k_cu)
        if r_prof is None:
            return err(msg, code)
    if barrel_profile is None:
        r_one = (L * 1e-3) / (float(k_cu) * a_mm2 * 1e-6)
    else:
        r_one = r_prof
        if a_mm2 is None:               # no nominal plating: report the thinnest
            t_thin = min(s["plating_um"] for s in seg_detail)
            r_in = r_out - t_thin / 1000.0
            a_mm2 = math.pi * (r_out ** 2 - r_in ** 2)
    value = {"rth_one_via_k_per_w": round(r_one, 9),
             "rth_array_k_per_w": round(r_one / n, 9),
             "count": n,
             "barrel_area_mm2": round(a_mm2, 9),
             "copper_length_mm": L,
             "fill_ratio_area_pct": round(100.0 * a_mm2 / (math.pi * r_out ** 2), 4),
             "drill_area_mm2": round(math.pi * r_out ** 2, 9)}
    basis = ("Rth = 1/(sum 1/R_i), R_i = L/(k_cu * A), "
             "A = pi*((d/2)^2 - (d/2 - t)^2)")
    not_computed = ["spreading into the pad / plane at both ends",
                    "solder wicking into the barrel (makes it better)"]
    if barrel_profile is not None:
        for s in seg_detail:
            s["barrel_area_mm2"] = round(s["barrel_area_mm2"], 9)
            s["rth_k_per_w"] = round(s["rth_k_per_w"], 9)
            s["share_of_barrel_pct"] = round(100.0 * s["rth_k_per_w"] / r_one, 6)
        value["barrel_profile"] = seg_detail
        value["plating_equivalent_um"] = round(t_eq_um, 6)
        value["barrel_area_mm2_source"] = ("plating_um" if t is not None
                                           else "thinnest barrel_profile segment")
        if t is not None:
            r_uni = (L * 1e-3) / (float(k_cu) * a_mm2 * 1e-6)
            value["rth_if_uniform_k_per_w"] = round(r_uni, 9)
            value["plating_thinning_effect_pct"] = round(
                100.0 * (r_one / r_uni - 1.0), 6)
        basis += ("; with barrel_profile the segments are in series, "
                  "R = sum L_i/(k_cu*A_i)")
        not_computed.insert(0, "any wall profile other than the one you gave - the "
                               "numbers are yours, not a plating-bath model")
    else:
        not_computed.insert(0, "barrel plating thinner at mid-board (makes it "
                               "worse) - pass barrel_profile instead of guessing")
    # ---- the FR4 cell around each barrel
    r_base = r_one / n
    if pitch_mm is not None:
        p = _num(pitch_mm)
        if p is None or p <= 0:
            return err("pitch_mm must be > 0", "ARG_RANGE")
        cell_area = p * p
        if cell_area <= math.pi * r_out ** 2:
            return err("pitch_mm is smaller than the drill - vias would overlap",
                       "ARG_RANGE")
        r_fr4 = (L * 1e-3) / (float(k_board) * cell_area * 1e-6)
        r_cell = 1.0 / (1.0 / r_one + 1.0 / r_fr4)
        value["rth_per_cell_with_board_k_per_w"] = round(r_cell, 9)
        value["rth_array_with_board_k_per_w"] = round(r_cell / n, 9)
        value["rth_pure_fr4_same_area_k_per_w"] = round(r_fr4 / n, 9)
        value["copper_share_of_cell_pct"] = round(
            100.0 * a_mm2 / cell_area, 6)
        basis += ("; with pitch: the FR4 cell p^2 is put in parallel with one "
                  "barrel, then the cells are put in parallel")
        not_computed.append("thermal interaction between neighbouring barrels")
        r_base = r_cell / n
    # ---- one series layer after the field
    if interface is not None:
        r_if, iface_echo, msg, code = _interface_rth(interface)
        if r_if is None:
            return err(msg, code)
        value["interface"] = iface_echo
        value["rth_interface_k_per_w"] = round(r_if, 9)
        value["rth_array_total_k_per_w"] = round(r_base + r_if, 9)
        value["interface_share_of_total_pct"] = round(
            100.0 * r_if / (r_base + r_if), 6)
        basis += ("; the interface layer is in series with the whole field, "
                  "R = interfaces * t/(k*A)")
        not_computed.append("voiding / pump-out in the interface layer, and the "
                            "pressure it is mounted at")
    assumptions = {"drill_mm": d, "plating_um": t, "length_mm": L,
                   "count": n, "k_cu": float(k_cu),
                   "pitch_mm": (_num(pitch_mm) if pitch_mm is not None else None),
                   "barrel_profile_segments": (len(barrel_profile)
                                               if barrel_profile is not None
                                               else None),
                   "interface": (interface is not None)}
    prec = ("first order lower bound on Rth: "
            + ("the wall is the profile you gave, but " if barrel_profile is not None
               else "the plating is uniform and thinner mid-barrel in reality, so ")
            + "the two pad interfaces "
            + ("are the interface layer you stated" if interface is not None
               else "are not modelled")
            + ". Do not use this to claim a via field is sufficient")
    return ok(value=value, unit="K/W", model="via_barrel_array",
              basis=basis, precision=prec,
              assumptions=assumptions,
              extra={"not_computed": not_computed})

# ------------------------------------------------------------------ 2D plate

def plate_temperature(width_mm, height_mm, thickness_mm, k_w_mk, h_w_m2k,
                      sources, t_ambient_c=25.0, cell_mm=None,
                      edge_temp_c=None, max_cells=20000, max_iter=60000,
                      tol_k=1e-4):
    """Steady-state 2-D temperature of a plate with point heat sources.

    This is the only function here that answers the question people actually
    care about - "how hot is the FET, and how much hotter than the average" -
    because a 1-D Rth through the board is blind to spreading: a 3 mm pad
    dumping 5 W into a 100 mm board is nothing like 5 W spread over the board.

    Model: k*t*lap(T) - 2*h*(T - Tamb) + q'' = 0 on a rectangular plate, top and
    bottom faces convecting with the same h, edges either adiabatic or held at
    edge_temp_c. Discretised on a uniform grid and solved by SOR. The energy
    balance (power in vs power out) is computed and reported, not assumed.

    Sources are {"x_mm", "y_mm", "power_w"} and land in the one cell containing
    that point - so keep cell_mm small enough to resolve the pad, and remember a
    point source on a coarse grid overstates the hot spot.
    """
    W = _num(width_mm)
    H = _num(height_mm)
    th = _num(thickness_mm)
    k = _num(k_w_mk)
    h = _num(h_w_m2k)
    ta = _num(t_ambient_c)
    for name, v in (("width_mm", W), ("height_mm", H), ("thickness_mm", th),
                    ("k_w_mk", k), ("t_ambient_c", ta)):
        if v is None:
            return err("%s must be a number" % name, "ARG_INVALID")
    for name, v in (("width_mm", W), ("height_mm", H), ("thickness_mm", th),
                    ("k_w_mk", k)):
        if v <= 0:
            return err("%s must be > 0" % name, "ARG_RANGE")
    if h is None or h <= 0:
        return err("h_w_m2k must be > 0 and stated", "ARG_REQUIRED",
                   hint="natural convection in still air is ~5..10 W/(m^2*K) on "
                        "the exposed face, ~2x if both faces see open air; forced "
                        "air is 20..100. This is not guessed here")
    if not sources:
        return err("sources is empty", "ARG_REQUIRED",
                   hint='each source is {"x_mm", "y_mm", "power_w"}')
    if edge_temp_c is not None:
        edge = _num(edge_temp_c)
        if edge is None:
            return err("edge_temp_c must be a number", "ARG_INVALID")
    else:
        edge = None

    step = _num(cell_mm) if cell_mm is not None else min(W, H) / 40.0
    if step is None or step <= 0:
        return err("cell_mm must be > 0", "ARG_RANGE")
    nx = max(3, int(round(W / step)))
    ny = max(3, int(round(H / step)))
    if nx * ny > max_cells:
        return err("grid would be %dx%d = %d cells (limit %d)" %
                   (nx, ny, nx * ny, max_cells), "TOO_LARGE",
                   hint="raise cell_mm; a hot-spot estimate does not need a fine "
                        "grid everywhere, but it does need the source pads "
                        "resolved")
    dx = (W / nx) * 1e-3
    dy = (H / ny) * 1e-3
    t_m = th * 1e-3
    gp = float(k) * t_m                  # W/K per square (per edge of a square)
    gx = gp * dy / dx
    gy = gp * dx / dy
    ac = dx * dy
    hc = 2.0 * float(h) * ac             # W/K per cell, both faces

    q = [[0.0] * ny for _ in range(nx)]
    placed = []
    total_in = 0.0
    for s in sources:
        if not isinstance(s, dict):
            return err("a source is not an object", "ARG_INVALID")
        sx = _num(s.get("x_mm"))
        sy = _num(s.get("y_mm"))
        sp = _num(s.get("power_w"))
        if sx is None or sy is None or sp is None:
            return err("a source needs numeric x_mm, y_mm, power_w", "ARG_INVALID")
        if sp < 0:
            return err("a source power_w must be >= 0", "ARG_RANGE")
        if not (0.0 <= sx <= W and 0.0 <= sy <= H):
            return err("source at (%.6g, %.6g) mm is outside the plate %.6g x "
                       "%.6g mm" % (sx, sy, W, H), "ARG_RANGE")
        size = _num(s.get("size_mm", 0.0) or 0.0)
        if size is None or size < 0:
            return err("a source size_mm must be >= 0", "ARG_RANGE")
        cells = []
        if size > 0:
            rad = size / 2.0
            for i in range(nx):
                cx = (i + 0.5) * dx * 1e3
                if abs(cx - sx) > rad:
                    continue
                for j in range(ny):
                    cy = (j + 0.5) * dy * 1e3
                    if (cx - sx) ** 2 + (cy - sy) ** 2 <= rad * rad:
                        cells.append((i, j))
        if not cells:
            i = min(nx - 1, int(sx / W * nx))
            j = min(ny - 1, int(sy / H * ny))
            cells = [(i, j)]
        share = sp / len(cells)
        for (i, j) in cells:
            q[i][j] += share
        total_in += sp
        placed.append({"x_mm": sx, "y_mm": sy, "power_w": sp,
                       "size_mm": size, "cells": cells,
                       "cell": cells[0]})
    if total_in <= 0:
        return err("the sources carry no power", "ARG_RANGE",
                   hint="everything is at t_ambient_c, there is nothing to solve")
    if edge is None and hc <= 0:
        return err("no way for heat to leave: h and edge_temp_c are both unset",
                   "ARG_REQUIRED")

    # initial guess: the 1-D local answer, which is already the right size.
    # Starting from t_ambient everywhere costs a factor of 3 in sweeps because
    # the slowest mode of this operator is the whole-plate average.
    guess = ta + total_in / (hc * nx * ny)
    temp = [[guess] * ny for _ in range(nx)]
    if edge is not None:
        for i in range(nx):
            temp[i][0] = edge
            temp[i][ny - 1] = edge
        for j in range(ny):
            temp[0][j] = edge
            temp[nx - 1][j] = edge
    fixed = edge is not None

    omega = 1.85
    it = 0
    resid = None
    converged = False
    while it < max_iter:
        it += 1
        dmax = 0.0
        for i in range(nx):
            for j in range(ny):
                if fixed and (i == 0 or j == 0 or i == nx - 1 or j == ny - 1):
                    continue
                num = hc * ta + q[i][j]
                den = hc
                if i > 0:
                    num += gx * temp[i - 1][j]
                    den += gx
                if i < nx - 1:
                    num += gx * temp[i + 1][j]
                    den += gx
                if j > 0:
                    num += gy * temp[i][j - 1]
                    den += gy
                if j < ny - 1:
                    num += gy * temp[i][j + 1]
                    den += gy
                new = num / den
                old = temp[i][j]
                d = omega * (new - old)
                temp[i][j] = old + d
                ad = abs(d)
                if ad > dmax:
                    dmax = ad
        resid = dmax
        if dmax < tol_k:
            converged = True
            break
    if not converged:
        return err("the 2-D solve did not converge: largest update still %.3g K "
                   "after %d sweeps" % (resid, it), "NOT_CONVERGED",
                   hint="this is reported instead of the current field because a "
                        "half-solved field looks exactly like a real one")

    # energy balance, computed rather than assumed
    out_conv = 0.0
    out_edge = 0.0
    for i in range(nx):
        for j in range(ny):
            out_conv += hc * (temp[i][j] - ta)
    if fixed:
        for i in range(nx):
            for j in range(ny):
                if i not in (0, nx - 1) and j not in (0, ny - 1):
                    continue
                s = 0.0
                if i > 0:
                    s += gx * (temp[i][j] - temp[i - 1][j])
                if i < nx - 1:
                    s += gx * (temp[i][j] - temp[i + 1][j])
                if j > 0:
                    s += gy * (temp[i][j] - temp[i][j - 1])
                if j < ny - 1:
                    s += gy * (temp[i][j] - temp[i][j + 1])
                out_edge -= s
    residual = total_in - out_conv - out_edge
    resid_pct = 100.0 * residual / total_in
    if abs(resid_pct) > 0.5:
        return err("energy does not balance: %.3f%% of the input power is "
                   "unaccounted for" % resid_pct, "INTERNAL",
                   hint="the field is not a solution of the stated model, so it is "
                        "not reported")

    singles = [p for p in placed if len(p["cells"]) == 1]
    spread_warn = []
    if singles:
        spread_warn.append(
            "%d source(s) landed in a single cell: the hot spot of a POINT source "
            "diverges as the cell shrinks, so that number is a property of "
            "cell_mm, not of the board. Pass size_mm (the pad diameter) to spread "
            "the power over the real area" % len(singles))
    tmax = max(max(row) for row in temp)
    tmin = min(min(row) for row in temp)
    tavg = sum(sum(row) for row in temp) / (nx * ny)
    src_temps = []
    for p in placed:
        ts = [temp[i][j] for (i, j) in p["cells"]]
        src_temps.append({"x_mm": p["x_mm"], "y_mm": p["y_mm"],
                          "power_w": p["power_w"], "size_mm": p["size_mm"],
                          "cells_spread": len(p["cells"]),
                          "t_c": round(max(ts), 6),
                          "t_avg_c": round(sum(ts) / len(ts), 6)})
    # a coarse ASCII map so the caller can see the shape without the grid
    rows = []
    nc = 32
    nr = 16
    for r in range(nr):
        line = ""
        for c in range(nc):
            i = min(nx - 1, int((c + 0.5) / nc * nx))
            j = min(ny - 1, int((r + 0.5) / nr * ny))
            span = (tmax - ta) or 1.0
            f = (temp[i][j] - ta) / span
            line += " .:-=+*#%@"[max(0, min(9, int(f * 9.999)))]
        rows.append(line)
    return ok(value={"t_max_c": round(tmax, 6),
                     "t_min_c": round(tmin, 6),
                     "t_avg_c": round(tavg, 6),
                     "t_ambient_c": ta,
                     "delta_t_max_k": round(tmax - ta, 6),
                     "spread_k": round(tmax - tavg, 6),
                     "sources": src_temps,
                     "grid": [nx, ny],
                     "cell_mm": [round(dx * 1e3, 9), round(dy * 1e3, 9)],
                     "iterations": it,
                     "energy_balance": {
                         "power_in_w": round(total_in, 9),
                         "power_out_convection_w": round(out_conv, 9),
                         "power_out_fixed_edges_w": round(out_edge, 9),
                         "residual_pct": round(resid_pct, 6)},
                     "effective_rth_k_per_w": round((tmax - ta) / total_in, 9),
                     "map": rows,
                     "map_legend": "row 0 is y=0; '@' is the hottest cell"},
              unit="C", model="plate_2d_fdm_sor",
              basis="k*t*lap(T) - 2*h*(T-Tamb) + q'' = 0 on a uniform grid, "
                    "adiabatic or fixed-temperature edges, solved by SOR (omega "
                    "1.85) to a max update of %.1e K; the reported Rth is "
                    "(T_max - T_amb)/P, i.e. the hot spot not the average" % tol_k,
              precision="as good as h and k. The hot-spot value is grid dependent: "
                        "a point source on a fine grid is worse than on a coarse "
                        "one, so compare two layouts only at the same cell_mm. "
                        "Radiation, thermal vias and the copper distribution of the "
                        "real board are all outside this model",
              assumptions={"width_mm": W, "height_mm": H, "thickness_mm": th,
                           "k_w_mk": float(k), "h_w_m2k": float(h),
                           "t_ambient_c": ta, "edge_temp_c": edge,
                           "cells": nx * ny},
              extra={"warnings": spread_warn + (
                          [] if abs(resid_pct) <= 1e-3 else
                          ["energy residual %.3g%% is the SOR tolerance, not an "
                           "error" % resid_pct]),
                     "not_computed": ["radiation (matters above ~60 C)",
                                      "thermal vias / copper distribution",
                                      "temperature-dependent k and h",
                                      "convection on the side faces"]})


# ------------------------------------------------------------------ transient

def thermal_transient(rth_k_per_w, cth_j_per_k, power_w, t_end_s,
                      t_ambient_c=25.0, points=40):
    """Step response of a thermal RC network (Foster form).

    rth_k_per_w and cth_j_per_k are each a number or a list of the same length:
    a single pair is one time constant (tau = R*C) and is honest only for a
    lumped object. A real package is 3-4 time constants (die, die-attach,
    package, board, ambient) and a single tau will both under-predict the peak
    of a short pulse and over-predict nothing useful later.

    T(t) = Tamb + P * sum_i Rth_i * (1 - exp(-t/tau_i))
    """
    def as_list(x, name):
        if isinstance(x, (list, tuple)):
            vals = [_num(v) for v in x]
            if any(v is None for v in vals):
                return None, err("%s contains a non-number" % name, "ARG_INVALID")
            return list(vals), None
        v = _num(x)
        if v is None:
            return None, err("%s must be a number or a list of numbers" % name,
                             "ARG_INVALID")
        return [v], None

    rs, e = as_list(rth_k_per_w, "rth_k_per_w")
    if e:
        return e
    cs, e = as_list(cth_j_per_k, "cth_j_per_k")
    if e:
        return e
    if len(rs) != len(cs):
        return err("rth and cth must have the same number of stages (%d vs %d) - "
                   "the stages are pairs (R, C), so a mismatch is a malformed "
                   "input, not an out-of-range value" % (len(rs), len(cs)),
                   "ARG_INVALID")
    p = _num(power_w)
    te = _num(t_end_s)
    ta = _num(t_ambient_c)
    if p is None or te is None or ta is None:
        return err("power_w, t_end_s and t_ambient_c must be numbers", "ARG_INVALID")
    if te <= 0:
        return err("t_end_s must be > 0", "ARG_RANGE")
    for v in rs:
        if v <= 0:
            return err("every Rth must be > 0", "ARG_RANGE")
    for v in cs:
        if v <= 0:
            return err("every Cth must be > 0", "ARG_RANGE")
    try:
        n = max(2, int(points))
    except (TypeError, ValueError):
        return err("points must be an integer", "ARG_INVALID")
    taus = [rs[i] * cs[i] for i in range(len(rs))]
    curve = []
    for k in range(n):
        t = te * k / (n - 1)
        d = sum(rs[i] * (1.0 - math.exp(-t / taus[i])) for i in range(len(rs)))
        curve.append({"t_s": round(t, 9), "delta_t_k": round(p * d, 9),
                      "t_c": round(ta + p * d, 9)})
    final = curve[-1]["delta_t_k"]
    return ok(value={"steady_delta_t_k": round(p * sum(rs), 9),
                     "delta_t_at_end_k": final,
                     "pct_of_steady_at_end": round(100.0 * final /
                                                   (p * sum(rs)), 6),
                     "t_max_c": curve[-1]["t_c"],
                     "time_constants_s": [round(x, 9) for x in taus],
                     "curve": curve},
              unit="C", model="foster_rc_ladder",
              basis="T(t) = Tamb + P * sum_i Rth_i*(1 - exp(-t/(Rth_i*Cth_i)))",
              precision="exact for the R-C ladder given, and the ladder is the "
                        "assumption: with one stage the answer can be wrong by "
                        "more than 2x early in a pulse. Cth must come from a "
                        "datasheet transient curve, not from a guess",
              assumptions={"rth_k_per_w": rs, "cth_j_per_k": cs,
                           "power_w": p, "t_ambient_c": ta},
              extra={"not_computed": ["temperature-dependent Cth",
                                      "constant-power vs constant-current load",
                                      "the self-heating feedback into the loss"]})


# ------------------------------------------------------------------ heatsink

def heatsink_select(power_w, t_j_max_c, t_ambient_c, rth_jc_k_per_w,
                    rth_cs_k_per_w=0.0, safety_margin=0.8):
    """What Rth(junction-to-air) do I need, and therefore what heatsink?

    Rth_sa_required = (Tj_max - Tamb)/P - Rth_jc - Rth_cs
    then the selection target is `safety_margin` times that, because a heatsink
    datasheet number is for a flat plate in free air, on a lab bench, at one
    orientation - none of which is the inside of the user's enclosure.

    The answer is the REQUIRED number. This function deliberately does not ship
    a table of heatsinks: a table would be the one thing in this file not derived
    from a stated input.
    """
    p = _num(power_w)
    tj = _num(t_j_max_c)
    ta = _num(t_ambient_c)
    rjc = _num(rth_jc_k_per_w)
    rcs = _num(rth_cs_k_per_w)
    for name, v in (("power_w", p), ("t_j_max_c", tj), ("t_ambient_c", ta),
                    ("rth_jc_k_per_w", rjc), ("rth_cs_k_per_w", rcs)):
        if v is None:
            return err("%s must be a number" % name, "ARG_INVALID")
    if p <= 0:
        return err("power_w must be > 0", "ARG_RANGE")
    if rjc < 0 or rcs < 0:
        return err("thermal resistances cannot be negative", "ARG_RANGE")
    try:
        margin = float(safety_margin)
    except (TypeError, ValueError):
        return err("safety_margin must be a number", "ARG_INVALID")
    if not (0.0 < margin <= 1.0):
        return err("safety_margin must be in (0, 1]", "ARG_RANGE")
    headroom = tj - ta
    if headroom <= 0:
        return err("t_j_max_c is not above t_ambient_c - there is no cooling that "
                   "fixes this", "ARG_RANGE")
    allowed_total = headroom / p
    r_sa = allowed_total - rjc - rcs
    if r_sa <= 0:
        return err("the junction-to-case and case-to-sink resistances alone use "
                   "%.3f of the %.3f K/W budget: no heatsink can help, the "
                   "package or the power has to change" %
                   (rjc + rcs, allowed_total), "INFEASIBLE")
    target = safety_margin * r_sa
    return ok(value={"required_rth_total_k_per_w": round(allowed_total, 9),
                     "rth_sa_budget_k_per_w": round(r_sa, 9),
                     "rth_sa_target_k_per_w": round(target, 9),
                     "safety_margin": margin,
                     "headroom_k": round(headroom, 6),
                     "power_w": p},
              unit="K/W", model="thermal_budget",
              basis="Tj = Tamb + P*(Rth_jc + Rth_cs + Rth_sa); solve for Rth_sa "
                    "and multiply the budget by the safety margin",
              precision="an accounting identity - it is exact given the four "
                        "numbers, and those four numbers are the problem: Rth_jc "
                        "is a datasheet number for a specific pad, Rth_cs depends "
                        "on TIM area and pressure, and P must be the real loss "
                        "(switching included), not V*I of the wrong state",
              assumptions={"t_j_max_c": tj, "t_ambient_c": ta,
                           "rth_jc_k_per_w": rjc, "rth_cs_k_per_w": rcs},
              extra={"not_computed": ["heatsink part selection (no table here)",
                                      "airflow derating",
                                      "altitude", "radiation"],
                     "next_step": "compare rth_sa_target_k_per_w against the "
                                  "heatsink datasheet value measured at the same "
                                  "airflow and mounting orientation"})


def convection_coefficient(airflow_m_s, length_m=None, mode="forced"):
    """h from airflow, so that the caller does not have to invent 10.

    Natural convection on a horizontal plate: h = 1.32*(dT/L)^0.25 in still air
    with dT in K and L in m gives W/(m^2*K) only with the right constant; the
    form used here is the common engineering correlation
        natural:  h = 1.42*(dT/L)^0.25      (dT in K, L in m)
        forced:   h = 10.45 - v + 10*sqrt(v)  (v in m/s) for a flat plate
    Both are +-25% correlations. That is the point of returning them with a
    precision note instead of a round number.
    """
    v = _num(airflow_m_s)
    if v is None:
        return err("airflow_m_s must be a number (0 for still air)", "ARG_INVALID")
    if v < 0:
        return err("airflow_m_s must be >= 0", "ARG_RANGE")
    if mode == "forced":
        if v <= 0:
            return err("forced convection needs airflow_m_s > 0", "ARG_RANGE",
                       hint="use mode='natural' for still air")
        h = 10.45 - v + 10.0 * math.sqrt(v)
        basis = "h = 10.45 - v + 10*sqrt(v) W/(m^2*K), v in m/s (flat plate)"
        note = "for v < 5 m/s this is the published flat-plate form; above that " \
               "the real h grows more slowly with v than 10*sqrt(v)"
    elif mode == "natural":
        L = _num(length_m)
        if L is None or L <= 0:
            return err("natural convection needs length_m > 0", "ARG_REQUIRED",
                       hint="the vertical/horizontal extent of the surface sets "
                            "the boundary layer thickness")
        dT = 40.0
        h = 1.42 * (dT / L) ** 0.25
        basis = "h = 1.42*(dT/L)^0.25, dT assumed %.0f K, L in m" % dT
        note = "dT is assumed, and h depends on it weakly (fourth root), so this " \
               "is good to about a factor of 1.5 across 10..100 K"
    else:
        return err("mode must be 'forced' or 'natural'", "ARG_INVALID")
    return ok(value={"h_w_m2k": round(h, 6), "mode": mode,
                     "airflow_m_s": v},
              unit="W/(m^2*K)", model="flat_plate_correlation",
              basis=basis,
              precision="an engineering correlation, +-25% at best. Use it to "
                        "compare two airflow assumptions, never to prove a design "
                        "is safe",
              assumptions={"mode": mode, "airflow_m_s": v},
              extra={"note": note,
                     "not_computed": ["fin efficiency", "radiation",
                                      "enclosure effects"]})
