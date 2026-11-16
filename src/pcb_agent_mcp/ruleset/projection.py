"""Which of the 27 layout rules can reach the GENERATOR, and as what shape.

The question this module answers is the one 0.1.20 could not: a rule that has a
source and a limit is still only a *measurement of a board that already exists*.
`calc/rules.py` writes predicates over a finished board ("is any copper under an
inductor"), `ruleset/audit.py` evaluates them, and that arrow never pointed the
other way. Handing a router a number needs a different object: permission ("this
cell is closed") or a floor ("this net is 0.508 mm wide and 3 widths from
anything else").

So every rule is put in one of four buckets, and the bucket is a claim that can
be checked in code, not a label:

``rect_keepout``
    The rule forbids copper inside a region. Shape: an axis-aligned rectangle in
    file coordinates. Consumer: `ruleset.design_constraints.keepout_rects` ->
    `route.router.plan(keepout_rects=...)` -> `route.grid.Grid`, i.e. the same
    raster AND the same exact ring check a board keepout gets.

``net_geometry``
    The rule is a floor on one net's copper (width, drill, distance to other
    nets). Consumer: `route.rules.apply_overrides`, which merges it into the
    resolved bundle and refuses any number that would loosen the board's own DRC.

``paired``
    The rule is a relation between two nets (skew, constant gap) or an absolute
    impedance. Consumer: `route.rules.resolve(diff_pairs=, impedance_targets=)`
    plus `route.tune.finish`. These need the board to *declare* the pair; a name
    is not a declaration and this module will not invent one.

``layer_policy``
    The rule is a property of a whole layer or of the placement, not of a
    candidate segment (a continuous reference plane, stitching pitch, decoupling
    distance). No channel in the router; the audit still judges it.

Each entry also carries a `status`, because "this rule has a shape the generator
could take" and "the generator takes it today" are two different claims:

``projected``          code in this package consumes it now.
``by_construction``    the generator cannot produce the violation at all (no 90
                       degree corner exists in `route.corners`, no layer change
                       without a return via in `route.return_path`), so the rule
                       holds without being read. This is honest only because the
                       claim is about the emitter, which is in this repo.
``not_wired``          shape known, channel missing; `why` names what is missing.
``audit_only``         nothing but the audit can judge it here.

Numbers are never duplicated from the catalogue: `limit_of()` reads the rule's own
`limit` (and `limit_fn` where the rule supplies one) so a change there is a change
here.
"""
import json
import re
import sys

from ..calc.rule_sources import scope_of

SCHEMA_VERSION = 1

# 20 mil: 《PCB设计秘籍》p44 "这些网络的走线建议至少为 20mil 宽" (power nets).
POWER_TRACE_WIDTH_MM = 0.508
# "推荐最小间隙为走线宽度的 3 倍" (p48) and the 3-W rule for RF.
RF_SPACING_FACTOR = 3.0

# A net is an RF net when its name says so. Deliberately narrow: a false positive
# widens that net's clearance everywhere and hides a real routing failure behind
# "not enough room", which is a wrong answer rather than a strict one.
RF_NET = re.compile(r"(^|[_/+\-])(RF|ANT|ANTENNA|LNA|PA)([_/+\-]|$)", re.I)

# KiCad writes a net as "/+3V3": a sheet path, then the name. Strip the path
# before deciding anything, or every rail on a hierarchical schematic is missed
# and `power_trace_width` silently applies to nothing - which looks exactly like
# a board where the rule was satisfied.
def bare_net(name):
    """The net's own name, without KiCad's sheet path (``"/+3V3"`` -> ``"+3V3"``).

    One call, not a loop: the first draft stripped leading slashes in a
    ``while text.startswith(...)`` and an empty needle made ``startswith`` return
    True forever, so the whole projection hung on the first net. A helper that
    cannot terminate is worse than a helper that is wrong.
    """
    text = str(name or "").strip()
    if "/" in text:
        text = text.rsplit("/", 1)[-1]
    return text.strip()

# Power rails, and only rails: GND is a pour question (audit), not a track width.
# "/+0V9_CPU", "/+1V35_DDR", "/+3V3", "/+5V_IN" are the shapes this board uses;
# "DDR_A0", "SDMMC_D0" and "USB0_VBUSDET" must NOT match, so the digits have to
# sit directly before the V and there has to be no letter run in front of them.
POWER_NET = re.compile(
    r"^(?:\+?[0-9]+V[0-9]*(?:_?[A-Z0-9_]*)?"
    r"|V(?:CC|DD|BUS|BAT|IN|REF|RAIL)[A-Z0-9_]*"
    r"|PWR[A-Z0-9_]*)$", re.I)

ENTRIES = [
    # ---- rect_keepout ----------------------------------------------------
    ("layout.magnetic_keepout", "rect_keepout", "projected",
     "no copper under a magnetic part at all: the grown pad-hull rectangle is "
     "fed to Grid.extra_keepouts, which blocks every layer and every net"),
    ("layout.magnetic_keepout_deep", "rect_keepout", "projected",
     "the same rectangle covers inner layers too, so the deep form is the strict "
     "union of this rule and magnetic_keepout"),
    ("layout.magnetic_via_keepout", "rect_keepout", "projected",
     "the rectangle blocks vias as well as tracks (Grid marks both maps)"),
    ("layout.magnetic_keepout_graze", "rect_keepout", "not_wired",
     "grazing copper is a distance from the part's body, which is the audit's "
     "exact measurement; the rectangle only blocks the hull, so projecting it as "
     "a block would report this rule as honoured when it is not"),
    ("layout.crystal_keepout_adjacent", "rect_keepout", "projected",
     "crystal keep-out rectangle, same channel as the magnetic ones"),
    ("layout.crystal_keepout", "rect_keepout", "projected",
     "the crystal's own rectangle is the same geometry grown by the same 0.1 mm"),
    ("layout.crystal_guard", "rect_keepout", "not_wired",
     "a guard ring is copper that must be ADDED around the crystal; the rectangle "
     "says where copper may not go, so it cannot express this one"),
    # ---- net_geometry ----------------------------------------------------
    ("layout.min_trace_width", "net_geometry", "projected",
     "global floor on track_mm via route.rules.apply_overrides"),
    ("layout.min_via_hole", "net_geometry", "projected",
     "global floor on via drill; apply_overrides also refuses a drill that would "
     "leave no annular ring"),
    ("layout.power_trace_width", "net_geometry", "projected",
     "per-net floor of 20 mil on every net whose name is a power rail"),
    ("layout.rf_spacing_3w", "net_geometry", "projected",
     "clearance_factor 3.0 for RF nets: apply_overrides turns it into 3 x that "
     "net's width, because the rule is written as a multiple and the router needs "
     "a distance"),
    ("layout.rf_to_board_edge", "net_geometry", "not_wired",
     "the router has ONE copper-to-edge number for every net; raising it for RF "
     "alone needs a per-net edge rule that route.rules does not have"),
    ("layout.rf_right_angle_bend", "net_geometry", "by_construction",
     "route.corners emits 0/45/90/135 and never an arc, so a right-angle RF bend "
     "cannot be produced; the rule is satisfied by the emitter, not read by it"),
    ("layout.layer_change_return_via", "net_geometry", "by_construction",
     "route.return_path adds a return via at every layer change while "
     "plan(return_path=True) is the default"),
    # ---- paired ----------------------------------------------------------
    ("layout.diffpair_skew", "paired", "not_wired",
     "route.tune.finish matches lengths when the board DECLARES diff_pairs; "
     "nothing derives p/n pairs from net names, and inventing them would be a "
     "guess dressed as a constraint"),
    ("layout.diffpair_gap_constant", "paired", "not_wired",
     "same channel as skew: the gap is only checkable on a declared pair"),
    ("layout.rf_z0_tolerance", "paired", "not_wired",
     "route.rules.resolve takes impedance_targets and derives the width from the "
     "stackup, but an ohm target is a design input (which net, how many ohm) and "
     "no rule in the catalogue carries it"),
    ("layout.rf_bend_radius", "paired", "not_wired",
     "a bend radius is an ARC; this project forbids arcs on copper, so the rule "
     "is out of scope by standing instruction rather than by missing code"),
    # ---- layer_policy ----------------------------------------------------
    ("layout.reference_plane_continuous", "layer_policy", "audit_only",
     "a continuous return plane is decided by the stackup and the pours, not by a "
     "candidate segment; the audit measures splits per net"),
    ("layout.stitching_via_pitch", "layer_policy", "audit_only",
     "stitching is a post-route fill over the whole plane; route.stitch exists but "
     "is driven by a caller-supplied net, not by the catalogue"),
    ("layout.decoupling_distance", "layer_policy", "audit_only",
     "a distance from a cap to an IC pin is a PLACEMENT property; the router "
     "cannot move a part"),
    ("layout.decoupling_same_layer", "layer_policy", "audit_only",
     "same: which layer a cap sits on is placement"),
    ("layout.bulk_per_ic", "layer_policy", "audit_only",
     "a bulk cap per IC is placement, and the catalogue's own source for this rule "
     "is this project (the book argues against it): it is never cited as external"),
    ("layout.hf_per_rail", "layer_policy", "audit_only",
     "whether a rail carries a 100 nF AT ALL is a bill-of-materials decision: the layout can place one close and the router can wire it, neither can add "
     "the part"),
    ("layout.power_pad_thermal_vias", "layer_policy", "audit_only",
     "the via array under a thermal pad is a footprint/pour decision; the router "
     "places vias to reach a layer, not to move heat"),
    ("layout.antenna_keepout", "layer_policy", "audit_only",
     "no rule channel: the antenna region is a board-level area decided with the "
     "module vendor's drawing, not a rectangle this module can derive"),
    # ---- assembly --------------------------------------------------------
    ("layout.silkscreen_over_pad", "audit_only", "audit_only",
     "silkscreen is not copper; the router never emits it"),
    ("layout.test_points_present", "audit_only", "audit_only",
     "a test point is a placement/naming decision"),
]

FORMS = ("rect_keepout", "net_geometry", "paired", "layer_policy", "audit_only")
STATUSES = ("projected", "by_construction", "not_wired", "audit_only")


class ProjectionError(Exception):
    """The projection table and the rule catalogue have drifted apart."""


def _rules():
    from ..calc.rules import LAYOUT_RULES
    return {r["id"]: r for r in LAYOUT_RULES}


def limit_of(rule_id, facts=None):
    """The rule's own number, so this module never holds a second copy of it.

    A rule whose limit is computed (`limit_fn`) needs facts, and answers `None`
    when it does not have them - `power_trace_width` cannot give millimetres
    without a current. Returning None is the honest answer here: substituting a
    default would make the projection look complete on a board where it is not.
    """
    rule = _rules().get(rule_id)
    if rule is None:
        raise ProjectionError("no rule %r in the catalogue" % (rule_id,))
    fn = rule.get("limit_fn")
    if fn is not None:
        try:
            got = fn(dict(facts or {}))
        except Exception:                       # noqa: BLE001 - no facts, no number
            return None
        if isinstance(got, tuple):
            got = got[0]
        if got is None:
            return None
        return float(got)
    got = rule.get("limit")
    if got is None or isinstance(got, bool):
        return None
    return float(got)


def table():
    """The three dictionaries, with the drift check that makes them trustworthy.

    A rule added to `calc/rules.py` and not classified here is an ERROR, not a
    silent `unprojected`: the whole point of 0.1.21 is that "which rules reach the
    generator" is a number this package knows, and a rule nobody classified would
    make that number wrong in the flattering direction.
    """
    cat = _rules()
    by_id = {}
    for rule_id, form, status, why in ENTRIES:
        if form not in FORMS:
            raise ProjectionError("rule %r has unknown form %r" % (rule_id, form))
        if status not in STATUSES:
            raise ProjectionError("rule %r has unknown status %r"
                                  % (rule_id, status))
        if rule_id in by_id:
            raise ProjectionError("rule %r classified twice" % (rule_id,))
        by_id[rule_id] = {"rule": rule_id, "form": form, "status": status,
                          "why": why}
    missing = sorted(set(cat) - set(by_id))
    extra = sorted(set(by_id) - set(cat))
    if missing:
        raise ProjectionError(
            "rule(s) in the catalogue with no projection entry: %s"
            % ", ".join(missing))
    if extra:
        raise ProjectionError(
            "projection entr(y/ies) with no rule in the catalogue: %s"
            % ", ".join(extra))
    for rule_id, row in by_id.items():
        rule = cat[rule_id]
        row["category"] = rule.get("category")
        row["unit"] = rule.get("unit")
        row["scope"], row["cited"] = _provenance(rule_id)
    return by_id


def _provenance(rule_id):
    """(scope, cited) straight from the ledger: never a second opinion."""
    from ..calc import rule_sources as RS
    entry, err = RS.citation(rule_id)
    if err or entry is None:
        return "uncited", False
    return scope_of(entry), True

def _net_names(board):
    names = set()
    for fp in (board.footprints or ()):
        for pad in (fp.get("pads") or ()):
            if pad.get("net"):
                names.add(str(pad["net"]))
    for t in (board.tracks or ()):
        if t.get("net"):
            names.add(str(t["net"]))
    return names


def rf_nets(board):
    return sorted(n for n in _net_names(board) if RF_NET.search(bare_net(n)))


def power_nets(board):
    return sorted(n for n in _net_names(board)
                  if POWER_NET.match(bare_net(n)))


def keepout_rects(board, grow_mm=None):
    """Rule-derived rectangles in the router's shape, each with its owner.

    The geometry comes from `design_constraints.keepout_rects` - one derivation,
    not two - and the rule id travels with the rectangle so a block can always be
    traced back to the sentence that put it there.
    """
    from . import design_constraints as DC
    if grow_mm is None:
        rows, skipped = DC.keepout_rects(board)
    else:
        rows, skipped = DC.keepout_rects(board, grow_mm)
    out = []
    for r in rows:
        x0, y0, x1, y1 = [float(v) for v in r["bbox"]]
        out.append({"x0": x0, "y0": y0, "x1": x1, "y1": y1,
                    "rule": r["rule"], "owner": str(r.get("ref")),
                    "kind": r["kind"]})
    return out, skipped


def router_inputs(board, grow_mm=None):
    """What `route.router.plan` can be handed right now, plus what is deferred.

    Only floors are emitted. A rule whose number the board already exceeds is not
    dropped silently - `route.rules.apply_overrides` reports it as `not_applied`
    with both numbers, so "the rule was fed in and made no difference" stays
    distinguishable from "the rule was never fed in".
    """
    rects, skipped = keepout_rects(board, grow_mm)
    overrides = {"track_mm": {}, "clearance_factor": {}, "notes": []}

    w_min = limit_of("layout.min_trace_width")
    if w_min:
        overrides["track_mm"]["*"] = w_min
        overrides["notes"].append(
            "track floor %.4f mm from layout.min_trace_width" % w_min)
    drill = limit_of("layout.min_via_hole")
    if drill:
        overrides["via_drill_mm"] = drill
        overrides["notes"].append(
            "via drill floor %.3f mm from layout.min_via_hole" % drill)
    pwr = power_nets(board)
    if pwr:
        pw = max(POWER_TRACE_WIDTH_MM, limit_of("layout.power_trace_width") or 0.0)
        for net in pwr:
            overrides["track_mm"][net] = pw
        overrides["notes"].append(
            "%d power net(s) get a %.3f mm floor (layout.power_trace_width, "
            "20 mil): %s" % (len(pwr), pw, ", ".join(pwr[:6])
                             + ("..." if len(pwr) > 6 else "")))
    rfs = rf_nets(board)
    if rfs:
        for net in rfs:
            overrides["clearance_factor"][net] = RF_SPACING_FACTOR
        overrides["notes"].append(
            "%d RF net(s) get a %.0f x width spacing (layout.rf_spacing_3w): %s"
            % (len(rfs), RF_SPACING_FACTOR, ", ".join(rfs[:6])))
    return {"keepout_rects": rects, "keepout_skipped": skipped,
            "net_overrides": overrides,
            "deferred": sorted(rule_id for rule_id, row in table().items()
                               if row["form"] == "net_geometry"
                               and row["status"] == "not_wired")}


def project(board, grow_mm=None):
    """The whole ledger: every rule, its form, its status, and the live payload."""
    rows = table()
    counts = {}
    for status in STATUSES:
        counts[status] = sum(1 for r in rows.values() if r["status"] == status)
    forms = {}
    for form in FORMS:
        forms[form] = sum(1 for r in rows.values() if r["form"] == form)
    payload = router_inputs(board, grow_mm)
    return {
        "schema_version": SCHEMA_VERSION,
        "n_rules": len(rows),
        "counts": counts,
        "forms": forms,
        "rules": [rows[k] for k in sorted(rows)],
        "keepout_rects": payload["keepout_rects"],
        "keepout_skipped": payload["keepout_skipped"],
        "net_overrides": payload["net_overrides"],
        "deferred": payload["deferred"],
        "note": "status=projected means code in this package consumes the rule "
                "today; by_construction means the emitter cannot produce the "
                "violation; not_wired names the missing channel in `why`; "
                "audit_only means only the audit can judge it.",
    }


def _main(argv):
    from ..adapters.kicad_pcb import parse_board
    if not argv:
        print("usage: python -m pcb_agent_mcp.ruleset.projection BOARD.kicad_pcb "
              "[--json OUT.json]")
        return 2
    board = parse_board(argv[0])
    rep = project(board)
    print("%d rules: %s" % (rep["n_rules"], " / ".join(
        "%s %d" % (k, rep["counts"][k]) for k in STATUSES)))
    for row in rep["rules"]:
        print("  %-12s %-11s %-15s %s" % (row["status"], row["form"],
                                          row["rule"], row["why"]))
    print("keep-out rects: %d (%d skipped)"
          % (len(rep["keepout_rects"]), len(rep["keepout_skipped"])))
    for r in rep["keepout_rects"]:
        print("  %-4s %-9s x %7.3f..%7.3f  y %7.3f..%7.3f  [%s]"
              % (r["owner"], r["kind"], r["x0"], r["x1"], r["y0"], r["y1"],
                 r["rule"]))
    print("net overrides:")
    for line in rep["net_overrides"]["notes"]:
        print("  " + line)
    if "--json" in argv:
        path = argv[argv.index("--json") + 1]
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(rep, fh, indent=1, ensure_ascii=False)
        print("wrote", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
