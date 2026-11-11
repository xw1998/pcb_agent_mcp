"""The five priority levels and the strict resolution between them."""

LEVELS = ("P0", "P1", "P2", "P3", "P4")

# Only these three can stop a board. A P3 miss (a right-angle corner, a diff
# pair with skew) is a quality finding, not a blocker: the board still builds,
# still assembles and still works. Keeping them out of `blocking_level` is what
# makes 'attack the highest priority first' mean something on a finished board.
BLOCKING_LEVELS = ("P0", "P1", "P2")

LEVEL_NAMES = {
    "P0": "manufacturability",
    "P1": "electrical integrity",
    "P2": "assembly & mechanical",
    "P3": "signal quality",
    "P4": "readability",
}

LEVEL_MEANING = {
    "P0": "the fab either cannot make it or will refuse/repriced it: track width and "
          "spacing below the process limit, a drill or annular ring below it, copper "
          "inside the edge keep-out, a slot narrower than the process can mill",
    "P1": "the circuit will not work as drawn: a net left in pieces, a signal over a "
          "plane split, a decoupling cap too far from its pin, a power trace too thin "
          "for its current, a layer change with no return path",
    "P2": "it cannot be built or fitted reliably: courtyards overlapping, a part off "
          "the board, silk on a pad, a crystal with copper under it, no thermal path "
          "under a hot pad, no test point on a rail",
    "P3": "it works but misses its own signal target: a non-45 corner, a diff pair "
          "with skew, 3W/spacing on an RF line, a stitching pitch above lambda/20, a "
          "bend tighter than the line can take",
    "P4": "a human can assemble and debug it: silk legibility, polarity marks, guard "
          "rings around sensitive nodes",
}

_TERMINAL = ("fail", "undetermined", "pass", "pass_with_warning", "not_applicable")


def resolve(checks):
    """Order the checks by priority and decide what may be reported as next.

    `checks` is a list of dicts with at least `level` (P0..P4) and `state`
    (fail / undetermined / pass / pass_with_warning / not_applicable).

    Returns the per-level roll-up plus `blocking_level` (the highest level that
    holds a failure) and `first_action`. Everything at a level BELOW the
    blocking one is marked `deferred`: its own grade is kept in `graded_state`
    and its `state` becomes `deferred`, because doing it first would mean paying
    for a lower priority with a higher one.
    """
    for c in checks:
        if c.get("level") not in LEVELS:
            raise ValueError("check %r has no priority level" % c.get("id"))
        if c.get("state") not in _TERMINAL:
            raise ValueError("check %r has an unknown state %r"
                             % (c.get("id"), c.get("state")))

    blocking = None
    for lvl in BLOCKING_LEVELS:
        if any(c["level"] == lvl and c["state"] == "fail" for c in checks):
            blocking = lvl
            break

    deferred = 0
    for c in checks:
        c["graded_state"] = c["state"]
        if blocking is not None and LEVELS.index(c["level"]) > LEVELS.index(blocking):
            if c["state"] in ("fail", "pass", "pass_with_warning"):
                c["state"] = "deferred"
                deferred += 1

    levels = {}
    for lvl in LEVELS:
        rows = [c for c in checks if c["level"] == lvl]
        counts = {}
        for c in rows:
            counts[c["state"]] = counts.get(c["state"], 0) + 1
        if not rows:
            status = "not_evaluated"
        elif blocking is not None and LEVELS.index(lvl) > LEVELS.index(blocking):
            status = "deferred"
        elif counts.get("fail") and lvl in BLOCKING_LEVELS:
            status = "blocked"
        elif counts.get("fail"):
            status = "flagged"
        elif counts.get("undetermined"):
            status = "incomplete"
        elif counts.get("pass_with_warning"):
            status = "pass_with_warnings"
        else:
            status = "clear"
        levels[lvl] = {
            "name": LEVEL_NAMES[lvl], "status": status, "counts": counts,
            "checks": rows,
        }

    first = None
    if blocking is not None:
        pool = [c for c in checks if c["level"] == blocking
                and c["graded_state"] == "fail"]
        pool.sort(key=lambda c: (c.get("margin") is None, c.get("margin") or 0.0))
        if pool:
            c = pool[0]
            first = {"level": blocking, "rule": c["id"], "title": c.get("title"),
                     "measured": c.get("measured"), "limit": c.get("limit"),
                     "where": c.get("where"), "fix": c.get("fix")}

    # What is left when nothing blocks: the P3/P4 findings, named but never
    # promoted to 'the next step' while any higher level is unfinished.
    advisory = []
    if blocking is None:
        for lvl in ("P3", "P4"):
            for c in checks:
                if c["level"] == lvl and c["graded_state"] == "fail":
                    advisory.append({"level": lvl, "rule": c["id"],
                                     "title": c.get("title"),
                                     "measured": c.get("measured"),
                                     "limit": c.get("limit"),
                                     "where": c.get("where"),
                                     "fix": c.get("fix")})

    return {
        "order": list(LEVELS),
        "blocking_levels": list(BLOCKING_LEVELS),
        "blocking_level": blocking,
        "flagged": advisory,
        "deferred": deferred,
        "levels": levels,
        "first_action": first,
        "do_not_trade": ("a lower level is never the next step while a higher one "
                         "fails: a P1 net left open is not improved by a P3 corner "
                         "chamfer, and 'fixed 12 of 15' below a blocker is zero"),
    }
