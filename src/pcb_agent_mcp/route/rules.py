"""What the board's rules actually say, per net.

The one rule this module exists to enforce: **a number the file does not contain
is never silently replaced by a plausible one.** Every value that had to be
assumed is listed in `assumed` with the reason and the expression used, and the
caller decides whether to proceed. `resolve()` raises when the file carries no
clearance at all and the caller did not supply one, because a router that invents
its own clearance draws copper its own DRC will reject.

Where the numbers come from, in priority order:

1. an explicit argument from the caller (`clearance_mm`, `track_mm`, ...)
2. the net's own class - `(net_class ...)` inside the board file, or
   `net_settings.classes` in the `.kicad_pro` next to it, including the KiCad 7+
   split where the *assignment* lives in `netclass_assignments` /
   `netclass_patterns` and not in the class
3. the project's board-wide minimums (`rules.min_clearance` etc.), used as a
   FLOOR: a class may be looser than the floor on paper but the floor is what
   DRC enforces, so the stricter of the two wins
4. nothing - assume 0.2 mm / 0.25 mm, and say so
"""

import fnmatch

from ..calc import iec60664 as _iec
from ..calc import impedance as _impedance

# Values assumed when the file is silent. Kept as module constants so a test can
# point at the exact number the answer claims.
DEFAULT_CLEARANCE_MM = 0.2
DEFAULT_TRACK_MM = 0.25
DEFAULT_VIA_DIAMETER_MM = 0.6
DEFAULT_VIA_DRILL_MM = 0.3
# Copper-to-edge is the one rule that cannot be left "unchecked": the referee
# (kicad-cli DRC) applies KiCad's board-setup default 0.5 mm whether or not the
# project file says so. Measured, not guessed: routing the F401 core board with
# this rule absent put 6 LED1_A/LED2_A tracks 0.275 mm from Edge.Cuts and
# `kicad-cli pcb drc` reported exactly 6 `copper_edge_clearance` errors. So when
# the file is silent the router takes the referee's own default, in the
# pessimistic direction, and says it did.
DEFAULT_EDGE_CLEARANCE_MM = 0.5

_NO_CLASS = ""


class RulesError(Exception):
    """No usable rule set: the caller must supply one or pick another board."""

    def __init__(self, message, reason_code="RULES_ABSENT", hint=None):
        Exception.__init__(self, message)
        self.message = message
        self.reason_code = reason_code
        self.hint = hint


def _f(v):
    if v is None:
        return None
    try:
        out = float(v)
    except (TypeError, ValueError):
        return None
    return out


def _merge_class(dst, src):
    """Fill only the fields `dst` is missing. First source wins, except `nets`."""
    for key in ("clearance", "track_width", "via_diameter", "via_drill"):
        if dst.get(key) is None and _f(src.get(key)) is not None:
            dst[key] = _f(src.get(key))
    for net in src.get("nets") or []:
        if net not in dst["nets"]:
            dst["nets"].append(net)
    return dst


def classes(board):
    """Every named net class this board knows, merged from file + project.

    Both spellings occur in the wild and both are real: the board file carries
    `(net_class "Default" ... (add_net "GND"))`, and the `.kicad_pro` carries
    `net_settings.classes[]` with the same fields. A net may be listed by either.
    Neither is invented: a board with no rules at all returns `{}`.
    """
    rules = board.rules or {}
    out = {}
    for name, spec in (rules.get("net_classes") or {}).items():
        if not isinstance(spec, dict):
            continue
        entry = out.setdefault(str(name), {"clearance": None, "track_width": None,
                                           "via_diameter": None, "via_drill": None,
                                           "nets": []})
        _merge_class(entry, spec)
    # KiCad 7+: the class no longer lists its nets; the assignment is separate.
    for net, names in (rules.get("netclass_assignments") or {}).items():
        if isinstance(names, str):
            names = [names]
        for name in names or []:
            entry = out.setdefault(str(name), {"clearance": None, "track_width": None,
                                               "via_diameter": None, "via_drill": None,
                                               "nets": []})
            if net not in entry["nets"]:
                entry["nets"].append(net)
    for pat in rules.get("netclass_patterns") or []:
        if not isinstance(pat, dict):
            continue
        cls = str(pat.get("netclass") or pat.get("name") or "")
        pattern = pat.get("pattern")
        if not cls or not pattern:
            continue
        entry = out.setdefault(cls, {"clearance": None, "track_width": None,
                                     "via_diameter": None, "via_drill": None,
                                     "nets": []})
        # A wildcard is not expanded here: expansion needs the net list, and the
        # net list is per board. `_pattern_hits` does it where the nets are known.
        entry.setdefault("patterns", []).append(pattern)
    return out


def _pattern_hits(inner, net, classes_):
    """Which class patterns match this net name (`USB*` -> `USB_D+`)."""
    for name, spec in classes_.items():
        for pattern in spec.get("patterns") or []:
            if pattern and fnmatch.fnmatchcase(net or "", pattern):
                return name
    return None


_DIEL_TYPES = ("core", "prepreg", "dielectric")

def _stackup_dielectric(board):
    """`(thickness_mm, er, copper_um, name)` - only when the stackup is unambiguous.

    A microstrip impedance depends on the dielectric between the trace and its
    reference plane, and on the copper weight. A real 4-layer board has several
    of each; picking one by guesswork would silently answer a different question,
    so a value is only offered when the whole stackup holds exactly one
    dielectric thickness and one copper thickness. Anything else returns `None`
    and the caller has to say which stackup the target was computed against.
    """
    layers = [l for l in (board.stackup or []) if isinstance(l, dict)]
    if not layers:
        return None, None, None, None
    diels = [l for l in layers
             if str(l.get("type") or "").lower() in _DIEL_TYPES
             and _f(l.get("thickness_mm"))]
    coppers = []
    for l in layers:
        cu = _f(l.get("copper_um"))
        if cu is None and str(l.get("type") or "").lower() == "copper":
            th = _f(l.get("thickness_mm"))
            cu = th * 1000.0 if th is not None else None
        if cu:
            coppers.append(cu)

    def _one(vals):
        uniq = sorted({round(v, 6) for v in vals})
        return uniq[0] if len(uniq) == 1 else None

    h = _one([_f(l["thickness_mm"]) for l in diels])
    er = _one([_f(l["er"]) for l in diels if _f(l.get("er"))])
    t = _one(coppers)
    name = diels[0].get("name") if (h is not None and len(diels) == 1) else None
    return h, er, t, name

def resolve(board, clearance_mm=None, track_mm=None, via_diameter_mm=None,
            via_drill_mm=None, edge_clearance_mm=None, creepage_mm=None,
            creepage_pairs=None, diff_pairs=None, impedance_targets=None,
            working_voltage_v=None, pollution_degree=2, material_group="IIIa",
            insulation="basic", coating=False, altitude_m=0):
    """Every rule the router needs, with where each number came from.

    Returns a dict; see the module docstring for the priority order. Numbers that
    are `None` could not be read and were NOT assumed (only `via_*` and
    `edge_clearance_mm` may be `None` - a router can work without them, a router
    cannot work without a clearance and a track width).

    `creepage_mm` / `creepage_pairs` have no fallback on purpose: they are a
    standard lookup, not a property of the board file, so `creepage_mm is None`
    means "no creepage constraint was requested" and never "creepage is 0".

    `impedance_targets` is the same kind of thing: "this net is 50 ohm" is a
    design intent, not a board-file fact. Each target is reverse-solved to a
    track width and then becomes that net's `track_mm` - which is the only way
    an impedance requirement can reach the search at all, because the search
    knows about widths and spacings, not about ohms.
    """
    rules = board.rules or {}
    cls = classes(board)
    default_cls = cls.get("Default") or {}

    by_net_clr, by_net_tw, by_net_vd, by_net_vdrill = {}, {}, {}, {}
    for name, spec in cls.items():
        for net in spec.get("nets") or []:
            if spec.get("clearance") is not None:
                by_net_clr[net] = spec["clearance"]
            if spec.get("track_width") is not None:
                by_net_tw[net] = spec["track_width"]
            if spec.get("via_diameter") is not None:
                by_net_vd[net] = spec["via_diameter"]
            if spec.get("via_drill") is not None:
                by_net_vdrill[net] = spec["via_drill"]
    for net in (board.nets or {}).values():
        name = _pattern_hits(None, net, cls)
        if name is None:
            continue
        spec = cls[name]
        if spec.get("clearance") is not None:
            by_net_clr.setdefault(net, spec["clearance"])
        if spec.get("track_width") is not None:
            by_net_tw.setdefault(net, spec["track_width"])

    floor_clr = _f((rules.get("clearance") or {}).get("min_clearance"))
    floor_tw = _f((rules.get("track_width") or {}).get("min_track_width"))
    floor_ec = _f((rules.get("clearance") or {}).get("min_copper_edge_clearance"))
    floor_vd = _f((rules.get("via") or {}).get("min_via_diameter"))
    floor_vdrill = _f((rules.get("via") or {}).get("min_through_hole_diameter"))

    assumed = []
    sources = {}

    # ---- clearance ----------------------------------------------------
    if clearance_mm is not None:
        clr = float(clearance_mm)
        sources["clearance"] = "caller"
    elif default_cls.get("clearance") is not None:
        clr = float(default_cls["clearance"])
        sources["clearance"] = "net class Default"
    elif by_net_clr:
        clr = min(by_net_clr.values())
        sources["clearance"] = "net class (tightest found)"
    elif floor_clr is not None:
        clr = floor_clr
        sources["clearance"] = "project minimum clearance"
    else:
        raise RulesError(
            "this board carries no clearance anywhere (no net class, no "
            "min_clearance) so a router would have to invent one",
            "RULES_ABSENT",
            hint="pass clearance_mm explicitly if you know the rule the board is "
                 "meant to be built to; the router will not guess")
    if floor_clr is not None and clearance_mm is None:
        clr = max(clr, floor_clr)

    # ---- track width --------------------------------------------------
    tw_src = None
    track = None
    if track_mm is not None:
        track, tw_src = float(track_mm), "caller"
    elif default_cls.get("track_width") is not None:
        track, tw_src = float(default_cls["track_width"]), "net class Default"
    elif by_net_tw:
        track, tw_src = min(by_net_tw.values()), "net class (thinnest found)"
    elif floor_tw is not None:
        track, tw_src = floor_tw, "project minimum track width"
    else:
        track = DEFAULT_TRACK_MM
        tw_src = "nothing in the file"
        assumed.append({"fact": "track_width", "value": DEFAULT_TRACK_MM,
                        "why": "no net class and no min_track_width; used %s mm"
                               % DEFAULT_TRACK_MM})
    if floor_tw is not None and track_mm is None:
        track = max(track, floor_tw)
    sources["track_width"] = tw_src

    # ---- via ----------------------------------------------------------
    via_d = via_drill = None
    if via_diameter_mm is not None:
        via_d, sources["via_diameter"] = float(via_diameter_mm), "caller"
    elif default_cls.get("via_diameter") is not None:
        via_d, sources["via_diameter"] = float(default_cls["via_diameter"]), \
            "net class Default"
    elif floor_vd is not None:
        via_d, sources["via_diameter"] = floor_vd, "project minimum via diameter"
    if via_drill_mm is not None:
        via_drill, sources["via_drill"] = float(via_drill_mm), "caller"
    elif default_cls.get("via_drill") is not None:
        via_drill, sources["via_drill"] = float(default_cls["via_drill"]), \
            "net class Default"
    elif floor_vdrill is not None:
        via_drill, sources["via_drill"] = floor_vdrill, \
            "project minimum through-hole diameter"
    if via_d is None:
        # A via needs a pad wider than the track it carries plus its own annular
        # ring; deriving it from the track is honest as long as it is declared.
        via_d = max(DEFAULT_VIA_DIAMETER_MM, round(track + 2 * clr, 2))
        sources["via_diameter"] = "derived: max(%s, track+2*clearance)" \
            % DEFAULT_VIA_DIAMETER_MM
        assumed.append({"fact": "via_diameter", "value": via_d,
                        "why": "no via diameter in the file; used "
                               "max(%s, track+2*clearance)" % DEFAULT_VIA_DIAMETER_MM})
    if via_drill is None:
        via_drill = min(DEFAULT_VIA_DRILL_MM, round(via_d / 2.0, 2))
        sources["via_drill"] = "derived: min(%s, via_diameter/2)" \
            % DEFAULT_VIA_DRILL_MM
        assumed.append({"fact": "via_drill", "value": via_drill,
                        "why": "no via drill in the file; used "
                               "min(%s, via_diameter/2)" % DEFAULT_VIA_DRILL_MM})

    # ---- edge ---------------------------------------------------------
    if edge_clearance_mm is not None:
        edge, sources["edge_clearance"] = float(edge_clearance_mm), "caller"
    elif floor_ec is not None:
        edge, sources["edge_clearance"] = floor_ec, "project min_copper_edge_clearance"
    else:
        edge = DEFAULT_EDGE_CLEARANCE_MM
        sources["edge_clearance"] = "assumed: KiCad board-setup default"
        assumed.append({"fact": "edge_clearance", "value": edge,
                        "why": "no min_copper_edge_clearance in the board or the "
                               "project, but the referee applies one anyway - "
                               "KiCad's board-setup default %.2f mm. Leaving it "
                               "unchecked wrote 6 tracks 0.275 mm from Edge.Cuts on "
                               "the F401 core board and kicad-cli DRC called every "
                               "one of them an error. Pass edge_clearance_mm if this "
                               "board is built to something else."
                               % DEFAULT_EDGE_CLEARANCE_MM})

    # ---- creepage -----------------------------------------------------
    # Creepage is not a clearance with another name. It is the shortest path
    # along the surface between two conductors at different potential, and its
    # value comes from a standard table - IEC 60664-1 / IPC-2221 key it to the
    # working voltage, the pollution degree, the material group and whether the
    # surface is coated. The table in this project is empty (nothing here has
    # been verified against a real standard), so there is exactly one honest
    # source for the number: the caller. Silence means "this run applies no
    # creepage constraint", which is stated rather than filled in with the
    # electrical clearance - 0.2 mm of clearance is not 0.2 mm of creepage and
    # pretending otherwise is the one answer this module exists to refuse.
    crp = None
    if creepage_mm is not None:
        crp = _f(creepage_mm)
        if crp is None or crp <= 0:
            raise RulesError(
                "creepage_mm=%r is not a usable distance" % (creepage_mm,),
                "RULES_INVALID",
                hint="creepage is a positive distance in mm, looked up for the "
                     "working voltage, pollution degree and coating - the router "
                     "will not invent it, so passing a non-number is refused")
        sources["creepage"] = "caller"
    else:
        sources["creepage"] = (
            "not supplied: creepage comes from a standard table keyed to working "
            "voltage / pollution degree / coating and is never invented here")

    # The lookup the sentence above is talking about, done on request. The table
    # is IEC 60664-1 Table F.5 (see calc/iec60664.py for how it was obtained and
    # what it is not); it is a table, so out-of-range inputs are a refusal, never
    # a straight-line extension past the last step.
    lookup = None
    if working_voltage_v is not None:
        got = _iec.creepage_mm(working_voltage_v,
                               pollution_degree=pollution_degree,
                               material_group=material_group,
                               insulation=insulation, coating=coating,
                               altitude_m=altitude_m)
        if got.get("status") != "ok":
            raise RulesError(
                "cannot look up creepage for %r V rms: %s"
                % (working_voltage_v, got.get("message")),
                got.get("reason_code") or "SAFETY_TABLE_MISSING",
                hint=got.get("hint") or (
                    "the table has no step for this input; pass creepage_mm "
                    "explicitly from the standard the board is built to - the "
                    "router will not extrapolate a ladder"))
        lookup = got
        table_mm = float(got["value"])
        how = ("IEC 60664-1 Table F.5: %g V rms, PD%s, material group %s, %s "
               "insulation -> %.4f mm (ladder %s)"
               % (float(working_voltage_v), pollution_degree, material_group,
                  insulation, table_mm, got.get("ladder")))
        if crp is None:
            crp = table_mm
            sources["creepage"] = how
        elif crp >= table_mm:
            sources["creepage"] = ("%s; the caller's %.4f mm is stricter and wins"
                                   % ("caller", crp))
            sources["creepage_table"] = how
        else:
            sources["creepage"] = ("%s; the IEC table value %.4f mm is stricter "
                                   "and wins" % ("caller %.4f mm" % crp, table_mm))
            sources["creepage_table"] = how
            crp = table_mm
    pairs = []
    for item in (creepage_pairs or []):
        if not isinstance(item, dict):
            raise RulesError("creepage_pairs entries must be {a, b, mm} objects, "
                             "got %r" % (item,), "RULES_INVALID")
        a, b, mm = item.get("a"), item.get("b"), _f(item.get("mm"))
        if not a or not b or a == b or mm is None or mm <= 0:
            raise RulesError(
                "creepage_pairs entry %r needs two different nets and a positive "
                "mm" % (item,), "RULES_INVALID",
                hint='e.g. {"a": "L", "b": "N", "mm": 1.0}')
        pairs.append({"a": str(a), "b": str(b), "mm": mm})
    if pairs and crp is None:
        sources["creepage"] = "caller (per pair only)"

    # ---- diff pairs ---------------------------------------------------
    # A diff pair reaches the search as two facts: the spacing the two nets must
    # keep from each other, and the width both must have. Neither can come from
    # the net class - a class says "0.2 mm from everything", which is a clearance,
    # not a coupling gap - so both are caller-declared, and neither is invented.
    dpairs = []
    for item in (diff_pairs or []):
        if not isinstance(item, dict):
            raise RulesError("diff_pairs entries must be {p, n, gap_mm} objects, "
                             "got %r" % (item,), "RULES_INVALID")
        pn, nn = item.get("p"), item.get("n")
        gap = _f(item.get("gap_mm"))
        width = _f(item.get("width_mm"))
        skew = _f(item.get("max_skew_mm"))
        if not pn or not nn or str(pn) == str(nn):
            raise RulesError("diff_pairs entry %r needs two different nets" % (item,),
                             "RULES_INVALID",
                             hint='e.g. {"p": "USB_D+", "n": "USB_D-"}; which of '
                                  'them is "p" only decides the sign of the skew')
        if gap is None or gap <= 0:
            raise RulesError("diff_pairs entry %r needs a positive gap_mm - the "
                             "edge-to-edge spacing the pair must keep" % (item,),
                             "RULES_INVALID")
        if width is not None and width <= 0:
            raise RulesError("diff_pairs entry %r has a non-positive width_mm"
                             % (item,), "RULES_INVALID")
        if skew is not None and skew <= 0:
            raise RulesError("diff_pairs entry %r has a non-positive max_skew_mm"
                             % (item,), "RULES_INVALID")
        # `couple` decides whether the pair is DRAWN as a pair (the second net
        # built as the first's path offset by the gap) or only held apart. It is
        # not a quality setting: a pair the caller wants routed independently is
        # a different board, and which one was built has to be in the answer.
        couple = item.get("couple", True)
        if not isinstance(couple, bool):
            raise RulesError("diff_pairs entry %r has a non-boolean couple"
                             % (item,), "RULES_INVALID",
                             hint="couple is true (draw them as a pair) or false "
                                  "(route them independently, gap as a minimum)")
        dpairs.append({"p": str(pn), "n": str(nn), "gap_mm": gap,
                       "width_mm": width, "max_skew_mm": skew,
                       "couple": couple})

    # ---- impedance targets --------------------------------------------
    # "This net is 50 ohm" is a design intent, not a board-file fact. The search
    # only knows widths and spacings, so the honest way to honour it is to
    # reverse-solve the width and make that the net's track width - and to say
    # which stackup the solve assumed. When the stackup does not pin the
    # dielectric down, the answer is an error, not a plausible number.
    imp = []
    imp_nets = set()
    for item in (impedance_targets or []):
        if not isinstance(item, dict):
            raise RulesError("impedance_targets entries must be {net|p/n, ohm} "
                             "objects, got %r" % (item,), "RULES_INVALID")
        names = []
        if item.get("net"):
            names = [str(item["net"])]
        else:
            pn, nn = item.get("p"), item.get("n")
            if pn and nn and str(pn) != str(nn):
                names = [str(pn), str(nn)]
        if not names:
            raise RulesError("impedance_targets entry %r names no net" % (item,),
                             "RULES_INVALID",
                             hint='e.g. {"net": "USB_DP", "ohm": 90} or '
                                  '{"p": "USB_D+", "n": "USB_D-", "ohm": 90}')
        ohm = _f(item.get("ohm"))
        if ohm is None or ohm <= 0:
            raise RulesError("impedance_targets entry %r needs a positive ohm"
                             % (item,), "RULES_INVALID",
                             hint="the target is a characteristic impedance in "
                                  "ohm, e.g. 50 for a single-ended line")
        kind = str(item.get("kind") or "microstrip").strip().lower()
        h = _f(item.get("dielectric_mm"))
        er = _f(item.get("er"))
        t_um = _f(item.get("copper_um"))
        derived = []
        if h is None or er is None or t_um is None:
            st_h, st_er, st_t, st_name = _stackup_dielectric(board)
            if h is None and st_h is not None:
                h = st_h
                derived.append("dielectric_mm %.4f from stackup layer %r"
                               % (h, st_name))
            if er is None and st_er is not None:
                er = st_er
                derived.append("er %.2f from stackup layer %r" % (er, st_name))
            if t_um is None and st_t is not None:
                t_um = st_t
                derived.append("copper_um %.1f from stackup" % (t_um,))
        if h is None:
            raise RulesError(
                "impedance target for %s needs dielectric_mm: the board carries "
                "no unambiguous dielectric thickness to derive it from"
                % (" / ".join(names),),
                "IMPEDANCE_NEEDS_STACKUP",
                hint="pass dielectric_mm (and er / copper_um if they differ "
                     "from 4.3 / 35) with the target; the router will not guess "
                     "the stackup a 50 ohm line was computed against")
        if er is None:
            er = 4.3
            derived.append("er 4.3 (formula default, not read from the board)")
        if t_um is None:
            t_um = 35.0
            derived.append("copper_um 35 (1 oz, formula default, not read from "
                           "the board)")
        got = _impedance.width_for_impedance(ohm, h, t_um, er, kind)
        if got.get("status") != "ok":
            raise RulesError(
                "cannot reach %g ohm on this stackup: %s"
                % (ohm, got.get("message")),
                "IMPEDANCE_UNREACHABLE",
                hint=got.get("hint") or (
                    "reachable range %s..%s ohm; change dielectric_mm / er / kind "
                    "or pick another target"
                    % (got.get("reachable_min_ohm"),
                       got.get("reachable_max_ohm"))))
        width = float(got["value"])
        imp_nets.update(names)
        imp.append({"nets": names, "ohm": ohm, "kind": kind,
                    "dielectric_mm": h, "er": er, "copper_um": t_um,
                    "width_mm": width, "basis": got["basis"],
                    "precision": got["precision"],
                    "z0_recomputed_ohm": got.get("z0_recomputed"),
                    "derived": derived})

    per_net = {}
    for net, per in by_net_clr.items():
        per_net[net] = {
            "clearance_mm": max(per, floor_clr) if floor_clr is not None else per,
            "track_mm": max(by_net_tw[net], floor_tw)
            if (net in by_net_tw and floor_tw is not None)
            else by_net_tw.get(net, track),
            "via_diameter_mm": by_net_vd.get(net, via_d),
            "via_drill_mm": by_net_vdrill.get(net, via_drill),
            "source": "net class of %r" % net,
        }
    for net, tw_ in by_net_tw.items():
        if net in per_net:
            continue
        per_net[net] = {
            "clearance_mm": max(by_net_clr.get(net, clr), floor_clr)
            if floor_clr is not None else by_net_clr.get(net, clr),
            "track_mm": max(tw_, floor_tw) if floor_tw is not None else tw_,
            "via_diameter_mm": via_d, "via_drill_mm": via_drill,
            "source": "net class of %r" % net,
        }

    # An impedance target outranks the class width: it is the number the board is
    # built to, and the class only says what the fab can manufacture.
    for target in imp:
        for net in target["nets"]:
            entry = per_net.get(net)
            if entry is None:
                entry = {"clearance_mm": clr,
                         "via_diameter_mm": via_d,
                         "via_drill_mm": via_drill,
                         "source": "impedance target"}
                per_net[net] = entry
            entry["track_mm"] = target["width_mm"]
            entry["source"] = ("impedance: %g ohm -> %.4f mm (%s)"
                              % (target["ohm"], target["width_mm"],
                                 target["kind"]))
        sources.setdefault("impedance", []).append(
            "%s: %g ohm -> %.4f mm (%s, h=%.4f mm, er=%.2f, t=%.0f um)"
            % (" / ".join(target["nets"]), target["ohm"], target["width_mm"],
               target["kind"], target["dielectric_mm"], target["er"],
               target["copper_um"]))

    # Both members of a pair are the same width, declared or not.
    for pair in dpairs:
        w = pair.get("width_mm")
        if w is None:
            continue
        for net in (pair["p"], pair["n"]):
            got = (per_net.get(net) or {}).get("track_mm")
            if got is not None and abs(got - w) > 1e-4 and net in imp_nets:
                raise RulesError(
                    "net %r is declared %.4f mm wide by its diff_pairs entry but "
                    "%.4f mm by its impedance target - two widths for one net is "
                    "a contradiction, not a fallback" % (net, w, got),
                    "RULES_INVALID",
                    hint="drop width_mm from the diff_pairs entry and let the "
                         "impedance target set it, or raise/lower the target")
            entry = per_net.get(net)
            if entry is None:
                entry = {"clearance_mm": clr, "track_mm": w,
                         "via_diameter_mm": via_d, "via_drill_mm": via_drill,
                         "source": "diff pair %s/%s" % (pair["p"], pair["n"])}
                per_net[net] = entry
            entry["track_mm"] = w

    board_default = {"clearance_mm": clr, "track_mm": track,
                     "via_diameter_mm": via_d, "via_drill_mm": via_drill,
                     "source": sources.get("clearance")}
    return {
        "clearance_mm": clr,
        "track_mm": track,
        "via_diameter_mm": via_d,
        "via_drill_mm": via_drill,
        "edge_clearance_mm": edge,
        "creepage_mm": crp,
        "creepage_pairs": pairs,
        "creepage_lookup": lookup,
        "diff_pairs": dpairs,
        "impedance_targets": imp,
        "default": board_default,
        "per_net": per_net,
        "classes": cls,
        "sources": sources,
        "assumed": assumed,
        "by_net_clearance": by_net_clr,
        "by_net_track": by_net_tw,
        "min_clearance_floor": floor_clr,
    }


def for_net(bundle, net):
    """One net's numbers out of a resolved bundle (falls back to the board)."""
    got = (bundle.get("per_net") or {}).get(net)
    if got is None:
        got = bundle["default"]
    return got


def pair_clearance(bundle, a, b):
    """Clearance required between net `a` and net `b`.

    The stricter of the two classes wins: a 0.15 mm class next to a 0.3 mm class
    lives 0.3 mm apart in real life, because the stricter part is what DRC fails.
    """
    ca = for_net(bundle, a)["clearance_mm"]
    cb = for_net(bundle, b)["clearance_mm"]
    return max(ca, cb)


def creepage(bundle, a, b):
    """Surface distance the caller asked for between nets `a` and `b` (0: none).

    Three ways to ask, and the strictest one wins: a board-wide `creepage_mm`, a
    per-pair entry in `creepage_pairs`, or nothing at all (0.0 - no creepage
    constraint was requested, which is not the same as "creepage is 0").
    """
    need = float(bundle.get("creepage_mm") or 0.0)
    for item in bundle.get("creepage_pairs") or []:
        if (item["a"] == a and item["b"] == b) or (item["a"] == b
                                                   and item["b"] == a):
            need = max(need, float(item["mm"]))
    return need


def creepage_max(bundle):
    """The largest creepage any pair on this board asked for (0: none).

    Used for the raster, which is one number for every net: a raster that is
    pessimistic for the widest creepage is safe for all of them, and the exact
    check in `grid.seg_blockers` applies each pair's own number afterwards.
    """
    need = float(bundle.get("creepage_mm") or 0.0)
    for item in bundle.get("creepage_pairs") or []:
        need = max(need, float(item["mm"]))
    return need


def diff_gap(bundle, a, b):
    """The coupling gap a declared diff pair asked for (0: not a declared pair)."""
    for item in bundle.get("diff_pairs") or []:
        if (item["p"] == a and item["n"] == b) or (item["p"] == b
                                                   and item["n"] == a):
            return float(item["gap_mm"])
    return 0.0


def spacing_max(bundle):
    """The widest net-to-net distance any rule on this board asked for.

    Clearance is per net class, creepage and diff-pair gaps are per pair, but the
    RASTER has one number for every net. Taking the widest is the pessimistic
    direction: the search plans outside the band the exact check then rejects, and
    `grid.seg_blockers` still judges each pair by its own `pair_spacing`.
    """
    widest = creepage_max(bundle)
    for item in bundle.get("diff_pairs") or []:
        widest = max(widest, float(item["gap_mm"]))
    return widest


def pair_spacing(bundle, a, b):
    """The distance two nets' copper must keep: electrical clearance *or* creepage.

    They are separate rules that happen to be measured with the same ruler: a
    0.15 mm class may legally sit 0.15 mm from its neighbour electrically and
    still fail creepage at 1.0 mm. Taking the stricter of the two is the only
    direction that cannot produce copper the referee rejects.
    """
    return max(pair_clearance(bundle, a, b), creepage(bundle, a, b),
               diff_gap(bundle, a, b))
