"""The state a board has to be in before a write may touch it.

The rule this module implements is deliberately NOT "no write until a human says
yes" - a confirmation box is a question, and a machine that has to ask is a
machine that cannot finish a board on its own. The rule is:

    state wrong, no write.

Every check below is decidable right now, from the board file and the plan, with
no human, no network and no guessing:

    rules_baseline     the rule set loads and every blocking rule carries a source
    drc_engine         the DRC diff can actually run, and is not switched off
    outline_known      the board shape is present and closed, so "too close to the
                       edge" means something
    mech_unchanged     this plan does not quietly move the mechanical layer
    clearance_no_worse this plan does not land copper closer than the floor to
                       another net when it was not already that close
    process_declared   (reported, never a pass) the P0 limits are judged against a
                       declared process, or they are not judged at all

A check that cannot be decided is `ok: None` with a reason. It is never a pass.
Only `ok: False` blocks; `severity` says whether that block is `block` (refuse
the write) or `report` (say it, carry on, and do not pretend it passed).

`clearance_no_worse` is deliberately a DELTA, not an absolute: a real board can
already carry a 0.1 mm pair somewhere nobody is editing, and refusing every edit
until that pair is moved would make the gate useless on exactly the boards that
need editing. What it refuses is a plan that CREATES such a pair.

Nothing here re-measures the board: the numbers come from the same functions the
plan already ran (``edit.checks``), so the gate cannot disagree with the proposal
it is gating.
"""

BLOCKING_LEVELS = ("P0", "P1", "P2")

#: The copper-to-copper floor this gate uses when a plan lands copper. It is a
#: *planning* floor, not a fab limit: the fab limit is graded from the capability
#: library by `board_rule_audit`, with its quote. 0.2 mm is JLC's 1 oz minimum
#: (0.1 mm) with the usual routing margin.
CLEARANCE_FLOOR_MM = 0.2


class PreconditionError(Exception):
    """A precondition said no. Same shape as the other errors in this package."""

    def __init__(self, message, code="PRECONDITION_NOT_MET", **extra):
        Exception.__init__(self, message)
        self.code = code
        self.extra = extra
        self.message = message


def _row(name, ok, why, how_to_clear=None, severity="block"):
    return {"check": name, "ok": ok, "why": why,
            "how_to_clear": how_to_clear, "severity": severity}


# ------------------------------------------------------------------ the checks
def rules_baseline():
    """Is there a rule set, and does every blocking rule say where its number is from?

    A baseline that loads but has a blocking rule with no source is worse than no
    baseline: it looks authoritative and cannot be argued with. So the test is not
    "the files exist", it is "every blocking rule can be traced".
    """
    # `from . import audit` would bind the FUNCTION that `ruleset/__init__`
    # re-exports, not the module - and then `.catalog` does not exist.
    from importlib import import_module
    _audit = import_module(".audit", __package__)
    from .. import jlc as _jlc_pkg          # the sub-package, for the same reason
    _jlc = import_module(".api", _jlc_pkg.__name__)
    try:
        cat = _audit.catalog()
        caps = _jlc.capabilities("pcb")
    except Exception as exc:                       # noqa: BLE001
        return _row("rules_baseline", False,
                    "the rule set does not load: %s: %s" % (type(exc).__name__, exc),
                    "repair the install: a write judged by no rules is an opinion")

    blocking = [r for r in cat["rules"] if r["level"] in BLOCKING_LEVELS]
    unsourced = [r["id"] for r in blocking if not (r.get("source") or "").strip()]

    def _cite(r):
        # A rule with a hard `limit` must quote the limit; a recommendation-only
        # rule (7 of the 29: TH.DRILL_REC, the mask bridges, silk-to-pad, ...) has
        # no limit to quote, and quoting the recommendation is what makes it
        # traceable. `audit._fab_checks` grades those under source_kind=recommended.
        return (r.get("limit") or {}).get("src") or (r.get("recommended") or {}).get("src")

    def _quote(r):
        return ((r.get("limit") or {}).get("quote")
                or (r.get("recommended") or {}).get("quote") or "")

    no_quote = [r["id"] for r in caps["rules"] if not _quote(r).strip() or not _cite(r)]
    stray = [r["id"] for r in caps["rules"]
             if _cite(r) and _cite(r) not in caps["sources"]]

    if unsourced or no_quote or stray:
        return _row("rules_baseline", False,
                    "the rule set loads, but %d blocking rule(s) carry no source, %d "
                    "fab limit(s) carry no verbatim quote, %d name a source that does "
                    "not exist (%s)"
                    % (len(unsourced), len(no_quote), len(stray),
                       ", ".join((unsourced + no_quote + stray)[:4])),
                    "restore the quote/source on those rules: an unsourced number "
                    "must not be allowed to block anything")
    return _row("rules_baseline", True,
                "%d blocking rules (P0/P1/P2), all sourced; %d fab limits, each with "
                "a verbatim quote from %d published sources"
                % (len(blocking), caps["count"], len(caps["sources"])))


def drc_engine(engine_on=True):
    """Can the DRC diff run? Is the engine even installed?

    The write path already diffs DRC before/after and refuses NEW violations. That
    gate is worth nothing if the engine is missing, so a missing kicad-cli blocks
    the write before the write instead of after it.

    `options.engine=false` is different: it is a switch the caller already owns and
    the API documents, so this gate does not overrule it. It reports the write as
    undecided and says so - deliberately-off is not the same fact as cannot-run,
    and a gate that quietly treated them alike would be changing a contract it was
    not asked to change.
    """
    from ..fidelity import engine as _engine
    if not engine_on:
        return _row("drc_engine", None,
                    "the engine gate is switched off for this write "
                    "(options.engine=false), so nothing compares the board before and "
                    "after - that is NOT a pass",
                    "leave options.engine on and a DRC diff runs; with it off the "
                    "write is unchecked and this answer is undecided",
                    severity="report")
    try:
        cli = _engine.find_kicad_cli()
        why = None
    except Exception as exc:                        # noqa: BLE001
        cli = None
        why = "%s: %s" % (type(exc).__name__, exc)
    if not cli:
        return _row("drc_engine", False,
                    "kicad-cli was not found, so the DRC diff cannot run"
                    + (" (%s)" % why if why else ""),
                    "install KiCad 9/10, or point PCB_AGENT_KICAD_CLI at kicad-cli")
    return _row("drc_engine", True, "the DRC diff will run through %s" % cli)


def outline_known(outline, copper_write=True):
    """Is there a closed board shape? Without one, "too close to the edge" is not a fact.

    An open outline is not automatically a defect (boards are sometimes drawn in
    pieces), but copper laid on a board whose shape is unknown cannot be checked
    against `PCB.EDGE.ROUTED`, which is a P0 limit. So copper waits; mechanical
    work does not.
    """
    outline = outline or {}
    closed = bool(outline.get("closed"))
    if not outline.get("present"):
        return _row("outline_known", False if copper_write else None,
                    "this board has no Edge.Cuts geometry at all: there is no board "
                    "shape, so every edge limit is undecidable",
                    "draw the outline first (an edge operation in this plan), then "
                    "lay copper")
    if closed:
        return _row("outline_known", True,
                    "the board shape is present and closed (%s primitive(s), %s loop(s))"
                    % (outline.get("primitives") or 0, outline.get("loops") or 0))
    gaps = outline.get("gaps_mm") or []
    return _row("outline_known", False if copper_write else None,
                "the board shape is open: %s unjoined end(s), worst gap %s mm"
                % (len(outline.get("open_ends") or []),
                   (gaps[0] if gaps else "?")),
                "close the outline in this same plan, or pass "
                "allow_open_outline=true if you are building the shape in steps")


def mech_unchanged(affected, allow_edge_changes=False):
    """Does this plan touch the mechanical layer without saying so?"""
    touched = set(affected or ())
    if "outline" in touched and not allow_edge_changes:
        return _row("mech_unchanged", False,
                    "this plan touches Edge.Cuts, which changes the board shape - a "
                    "P0-relevant fact (edge clearance, board size, panel)",
                    "pass allow_edge_changes=true when the shape really is the subject "
                    "of the edit")
    return _row("mech_unchanged", True,
                "the plan touches %s; Edge.Cuts is untouched"
                % (", ".join(sorted(touched)) or "nothing"))


def clearance_no_worse(after, before=None, copper_write=True):
    """Did this plan create an unusable gap, or only inherit one?

    `after` and `before` are `edit.checks.copper_clearance` results (different-net
    track/via pairs below the threshold). The gate refuses a plan that increases
    the count of such pairs; it does not refuse a plan that leaves a pre-existing
    pair where it was.
    """
    if not copper_write:
        return _row("clearance_no_worse", None,
                    "this plan lands no copper, so there is no clearance to judge",
                    severity="report")
    if not isinstance(after, dict):
        return _row("clearance_no_worse", None,
                    "clearance was not measured for this plan", severity="report")
    floor = float(after.get("threshold_mm") or CLEARANCE_FLOOR_MM)
    n_after = int(after.get("violations") or 0)
    n_before = int((before or {}).get("violations") or 0) if isinstance(before, dict) else 0
    if n_after <= n_before:
        return _row("clearance_no_worse", True,
                    "%d different-net pair(s) closer than %s mm after this plan, %d "
                    "before - this plan created none of them"
                    % (n_after, floor, n_before))
    worst = (after.get("worst") or [])
    w = worst[0] if worst else {}
    where = w.get("at")
    return _row("clearance_no_worse", False,
                "this plan takes the board from %d to %d different-net pair(s) closer "
                "than %s mm; tightest is %s mm%s"
                % (n_before, n_after, floor, w.get("gap_mm"),
                   (" at %s" % (where,)) if where else ""),
                "move the copper so the new pair is at least %s mm apart, or narrow "
                "the check window with precheck_region" % floor)


def process_declared(process, copper_write=True):
    """P0 is only judged against a declared process. Reported, never a pass."""
    if process:
        return _row("process_declared", True,
                    "the P0 limits are judged against %s"
                    % ", ".join("%s=%s" % (k, process[k]) for k in sorted(process)),
                    severity="report")
    if not copper_write:
        return _row("process_declared", None,
                    "no process declared; this plan lands no copper, so no P0 limit "
                    "depends on it", severity="report")
    return _row("process_declared", None,
                "no process declared (options.process), so no P0 limit is judged for "
                "this write - that is NOT a pass",
                "pass options.process={\"layers\":2,\"copper_oz\":1} (or run "
                "board_rule_audit with the real process) to have P0 judged",
                severity="report")


# ------------------------------------------------------------------ the verdict
def assess(path=None, *, affected=(), outline=None, clearance_after=None,
           clearance_before=None, process=None, engine_on=True,
           allow_edge_changes=False, allow_open_outline=False, copper_adds=None):
    """Run every check and resolve them into one answer.

    Returns ``{"ok", "verdict", "blocking", "reported", "checks"}``. `ok` is True
    when no blocking check failed; a check that could not run is neither.

    ``copper_adds`` says whether this plan PUTS copper down or only takes it away.
    Removing copper cannot create a spacing pair and cannot land anything near an
    unknown board edge, so a cut is judged on its own terms; when the caller does
    not say, the old reading (`"copper" in affected`) is kept, which is the
    conservative one.
    """
    copper_write = (bool(copper_adds) if copper_adds is not None
                    else "copper" in set(affected or ()))
    checks = [
        rules_baseline(),
        drc_engine(engine_on=engine_on),
        outline_known(outline, copper_write=copper_write),
        mech_unchanged(affected, allow_edge_changes=allow_edge_changes),
        clearance_no_worse(clearance_after, clearance_before,
                           copper_write=copper_write),
        process_declared(process, copper_write=copper_write),
    ]
    if allow_open_outline:
        for c in checks:
            if c["check"] == "outline_known" and c["ok"] is False:
                c["ok"] = None
                c["why"] += " (allow_open_outline=true: reported, does not block)"
                c["severity"] = "report"
    blocking = [c for c in checks if c["ok"] is False and c["severity"] == "block"]
    reported = [c for c in checks if c["ok"] is None or c["severity"] == "report"]
    if blocking:
        verdict = "refused_at_" + blocking[0]["check"]
    elif any(c["ok"] is None for c in checks):
        verdict = "writable_with_%d_undecided" % sum(1 for c in checks if c["ok"] is None)
    else:
        verdict = "writable"
    return {"ok": not blocking, "verdict": verdict,
            "blocking": [c["check"] for c in blocking],
            "reported": [c["check"] for c in reported],
            "checks": checks, "path": path,
            "rule": ("state wrong, no write: a machine that has to ask a human before "
                     "every track cannot finish a board, so the gate is state the tool "
                     "can read, not a question")}


def message(result):
    """One sentence, for the error a refused apply raises."""
    bad = [c for c in result["checks"] if c["ok"] is False and c["severity"] == "block"]
    return "; ".join("%s: %s" % (c["check"], c["why"]) for c in bad)


__all__ = ["assess", "message", "PreconditionError", "CLEARANCE_FLOOR_MM",
           "rules_baseline", "drc_engine", "outline_known", "mech_unchanged",
           "clearance_no_worse", "process_declared"]
