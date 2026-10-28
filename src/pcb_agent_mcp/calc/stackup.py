"""Laminate choice and layer stack for RF / mixed-signal boards.

The material table is the one from rf-circuit-design / SKILL.md section 3.1
(dielectric constant, loss tangent, the band where the material is the normal
choice, relative cost). It is a SHORTLIST, not a datasheet: every entry's er
and tan_delta move with resin content, glass style and frequency, and the
laminate supplier's own stackup drawing is the authority.

Two things this module is careful about:

  * the dielectric breakdown of a stack is a PROPOSAL. A real impedance
    controlled stack is the fab's, with their prepreg build and their measured
    er; the split here is proportional and says so.
  * a width computed on 4.3 is not a width on 3.55. Every signal layer gets its
    own width, solved with the same feedline model the feedline tools use, so
    the number can be re-derived and disagreed with.
"""
from .result import ok, err
from . import feedline as _feedline
from . import rf as _rf

# er, tan_delta, practical upper band (GHz), relative cost tier, note
MATERIALS = {
    "fr4": {
        "name": "FR4 (standard epoxy/glass)",
        "er": 4.3, "tan_delta": 0.025, "band_ghz": 3.0,
        "cost": "low", "source": "rf-circuit-design SKILL 3.1",
        "note": "er tolerance is wide (+/-0.2 typical) and tan_delta is the "
                "loss driver above ~1 GHz; Dk also falls with frequency",
    },
    "ro4003c": {
        "name": "Rogers RO4003C",
        "er": 3.55, "tan_delta": 0.003, "band_ghz": 10.0,
        "cost": "medium", "source": "rf-circuit-design SKILL 3.1",
        "note": "hydrocarbon/ceramic, FR4-like processing, tight Dk",
    },
    "ro4350b": {
        "name": "Rogers RO4350B",
        "er": 3.66, "tan_delta": 0.004, "band_ghz": 20.0,
        "cost": "medium", "source": "rf-circuit-design SKILL 3.1",
        "note": "wider Dk tolerance than RO4003C, still low loss",
    },
    "rt5880": {
        "name": "Rogers RT/duroid 5880",
        "er": 2.2, "tan_delta": 0.0009, "band_ghz": 40.0,
        "cost": "high", "source": "rf-circuit-design SKILL 3.1",
        "note": "PTFE, soft and dimensionally fussy, needs special via/plating "
                "processes; the low er makes every 50 ohm line wide",
    },
    "megtron6": {
        "name": "Panasonic Megtron 6",
        "er": 3.4, "tan_delta": 0.004, "band_ghz": 10.0,
        "cost": "medium-high", "source": "rf-circuit-design SKILL 3.1",
        "note": "high-speed digital laminate; chosen for skew/loss, not for RF",
    },
}

COST_ORDER = ("low", "medium", "medium-high", "high")

# layer role, copper oz -- from rf-circuit-design SKILL 3.2 (4 and 6 layer)
STACKS = {
    4: [
        ("L1", "signal (RF / components)", 0.5),
        ("L2", "ground plane (solid)", 1.0),
        ("L3", "power plane", 1.0),
        ("L4", "signal (digital)", 0.5),
    ],
    6: [
        ("L1", "signal (RF / components)", 0.5),
        ("L2", "ground plane 1 (solid)", 1.0),
        ("L3", "sensitive signal", 0.5),
        ("L4", "power plane", 1.0),
        ("L5", "ground plane 2 (solid)", 1.0),
        ("L6", "signal (digital)", 0.5),
    ],
}

COPPER_MM_PER_OZ = 0.035


def materials_table():
    """The whole shortlist, for a caller that wants to choose itself."""
    rows = []
    for key, m in MATERIALS.items():
        rows.append({
            "id": key, "name": m["name"], "er": m["er"],
            "tan_delta": m["tan_delta"], "band_ghz": m["band_ghz"],
            "cost": m["cost"], "note": m["note"], "source": m["source"],
        })
    rows.sort(key=lambda r: (r["band_ghz"], COST_ORDER.index(r["cost"])))
    return rows


def material_for(freq_ghz):
    """Cheapest laminate whose practical band covers this frequency."""
    try:
        f = float(freq_ghz)
    except (TypeError, ValueError):
        return err("freq_ghz must be numeric", "ARG_INVALID")
    if f <= 0:
        return err("freq_ghz must be > 0", "ARG_RANGE")

    fits = [r for r in materials_table() if r["band_ghz"] >= f]
    if not fits:
        return err("%g GHz is above every laminate in the table" % f, "OUT_OF_VALIDITY",
                   hint="the table stops at RT/duroid 5880 (40 GHz); above that "
                        "the choice is waveguide / specialised PTFE and this "
                        "table has nothing to say",
                   extra={"table_max_ghz": max(r["band_ghz"] for r in materials_table())})
    # cheapest first, then the lowest er (wider lines, easier etch tolerance)
    fits.sort(key=lambda r: (COST_ORDER.index(r["cost"]), r["er"]))
    best = fits[0]
    fr4 = MATERIALS["fr4"]
    out = {
        "recommended": best["id"],
        "why": "%s is the cheapest entry whose practical band (%.1f GHz) covers "
               "%g GHz" % (best["name"], best["band_ghz"], f),
        "alternatives": [r["id"] for r in fits[1:]],
        "narrower_band_entries": [r["id"] for r in materials_table()
                                  if r["band_ghz"] < f],
        "what_the_table_cannot_see": [
            "Dk vs frequency and vs glass style (a datasheet plot, not a number)",
            "Dk tolerance and lot-to-lot variation (this decides whether the "
            "50 ohm line is 48 or 52 ohm before you tune anything)",
            "process compatibility: PTFE needs plasma treatment and special "
            "plating, and thin PTFE warps",
            "assembly temperature: some high-speed laminates are lead-free "
            "sensitive",
        ],
    }
    if f > fr4["band_ghz"]:
        out["fr4_verdict"] = ("do not use plain FR4 here: tan_delta %.3f at %g GHz "
                              "costs real insertion loss and the Dk tolerance "
                              "moves the match" % (fr4["tan_delta"], f))
    return ok(
        value=out, unit="recommendation", model="laminate_band_shortlist",
        basis="rf-circuit-design SKILL 3.1 material table (er, tan_delta, band, cost)",
        precision="a band limit is an engineering convention, not a measured "
                  "threshold - the loss at the band edge depends on the actual "
                  "line length and the tan_delta curve",
        assumptions={"frequency_is_the_highest_of_interest": True},
    )


def _split(layers, total_mm):
    """Proportional dielectric split. A proposal, never a fab drawing."""
    gaps = layers - 1
    # equal gaps, with the LAST one absorbing the rounding residual so the stack
    # adds up to the finished thickness exactly instead of 0.9999
    each = round(total_mm / float(gaps), 4)
    out = [each] * gaps
    out[-1] = round(total_mm - each * (gaps - 1), 4)
    return out


def stackup_design(layers=4, material="fr4", total_mm=1.0, target_ohm=50.0,
                   copper_um=None, freq_ghz=None, er=None):
    """Layer table + the width that gives `target_ohm` on each signal layer."""
    try:
        n = int(layers)
        total = float(total_mm)
        target = float(target_ohm)
    except (TypeError, ValueError):
        return err("layers, total_mm and target_ohm must be numeric", "ARG_INVALID")
    if n not in STACKS:
        return err("only 4 and 6 layer stacks are defined here", "ARG_INVALID",
                   hint="the two stacks are the ones written down in "
                        "rf-circuit-design SKILL 3.2; other counts need their own "
                        "reference-plane argument")
    if total <= 0 or target <= 0:
        return err("total_mm and target_ohm must be > 0", "ARG_RANGE")

    key = str(material or "fr4").strip().lower()
    if key not in MATERIALS:
        if er is None:
            return err("unknown material %r" % material, "ARG_INVALID",
                       hint="known: " + ", ".join(MATERIALS),
                       extra={"known_materials": list(MATERIALS)})
        mat = {"name": "caller-supplied", "er": None, "tan_delta": None,
               "cost": "unknown", "band_ghz": None,
               "note": "caller supplied er; loss not known", "source": "caller"}
    else:
        mat = MATERIALS[key]

    try:
        eps = float(er) if er is not None else float(mat["er"])
    except (TypeError, ValueError):
        return err("er must be numeric", "ARG_INVALID")
    if eps < 1.0:
        return err("er must be >= 1", "ARG_RANGE")

    gaps = _split(n, total)
    rows = []
    copper = copper_um
    for i, (name, role, oz) in enumerate(STACKS[n]):
        t_um = copper_um if copper_um is not None else oz * COPPER_MM_PER_OZ * 1000.0
        row = {"layer": name, "role": role, "copper_oz": oz,
               "copper_um": round(t_um, 2)}
        if i < len(gaps):
            row["dielectric_to_next_mm"] = gaps[i]
        rows.append(row)

    # 50 ohm width on every signal layer, over the dielectric directly below it
    widths = []
    for i, row in enumerate(rows):
        if "signal" not in row["role"] and "sensitive" not in row["role"]:
            continue
        if i >= len(gaps):
            widths.append({"layer": row["layer"], "width_mm": None,
                           "why_not": "the bottom layer has no plane below it; "
                                      "use the layer above as the reference and "
                                      "solve it as a microstrip on that gap"})
            continue
        h = gaps[i]
        r = _feedline.feedline_width(target, "microstrip", h,
                                     copper_um=row["copper_um"], er=eps)
        if r.get("status") == "ok":
            widths.append({"layer": row["layer"], "reference": "L%d" % (i + 2),
                           "dielectric_mm": h,
                           "width_mm": round(r["value"]["width_mm"], 4),
                           "z0_check_ohm": round(r["value"]["z0_ohm"], 3)})
        else:
            widths.append({"layer": row["layer"], "reference": "L%d" % (i + 2),
                           "dielectric_mm": h, "width_mm": None,
                           "why_not": r.get("message")})

    out = {
        "layers": n,
        "material": key,
        "material_name": mat["name"],
        "er_used": eps,
        "total_thickness_mm": total,
        "stack": rows,
        "target_ohm": target,
        "widths": widths,
        "copper_balance": _balance(rows),
        "checks_you_still_owe": [
            "get the fab's impedance-controlled stack drawing and rebuild the "
            "widths on ITS er and prepreg thickness",
            "the split here is proportional, not a real build",
            "keep every signal layer adjacent to a solid plane; a split in that "
            "plane is a return-path break, which is worse than a wrong width",
        ],
    }
    if freq_ghz is not None:
        v = _rf.lambda_over_20(freq_ghz, eps)
        if v.get("status") == "ok":
            out["via_pitch_mm"] = v["value"]
            out["via_pitch_rule"] = "lambda_g/20 at %g GHz in er=%.2f" % (
                float(freq_ghz), eps)
        else:
            out["via_pitch_mm"] = None
            out["via_pitch_rule"] = v.get("message")
    else:
        out["via_pitch_mm"] = None
        out["via_pitch_rule"] = ("no frequency given, so no stitching pitch: "
                                 "the rule is lambda_g/20 and without f it is "
                                 "undefined, not 1 mm")

    return ok(
        value=out, unit="stackup", model="proportional_stack_plus_microstrip_solve",
        basis=("layer roles from rf-circuit-design SKILL 3.2; width solved with "
               "the same microstrip model as feedline_width; via pitch = "
               "lambda/(20 sqrt(er))"),
        precision=("geometry proposal: dielectric split is proportional and the "
                   "er is a single number, so the widths are a starting point "
                   "that an impedance coupon must confirm"),
        assumptions={
            "dielectric_split": "equal gaps (see checks_you_still_owe)",
            "width_model": "microstrip over the next plane, zero solder mask",
            "plating": "copper thickness is the finished base copper, not the "
                       "plated-up value",
        },
    )


def _balance(rows):
    oz = [r["copper_oz"] for r in rows]
    sym = oz == list(reversed(oz))
    return {
        "copper_oz_by_layer": oz,
        "symmetric": sym,
        "note": ("symmetric copper and dielectric keeps the board flat through "
                 "reflow; an asymmetric build warps and bows the assembly"
                 if not sym else
                 "symmetric build: bow and twist stay small after reflow"),
    }
