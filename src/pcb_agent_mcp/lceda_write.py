"""The smallest honest write path into LCEDA Pro: run it, then READ IT BACK.

The bridge has only ever been verified on reads (see `lceda_live`), and that is
still true here: nothing in this module makes the app save, create or move
anything on its own. What it does is make a write *checkable*:

    probe (before) -> code -> probe (after) -> compare

A write whose effect nobody read back is a claim, not a fact, so `expect="change"`
REFUSES to run without a probe. When the probe comes back identical the answer is
`WRITE_HAD_NO_EFFECT` - the snippet ran, the app did not throw, and the design did
not move. That is the failure this module exists to catch, because it is the one
that looks like success from the caller's side.

Snippets are written by the caller on purpose. Generating "move footprint U1 by
(dx, dy)" out of thin air would need the vendor's method names and argument
shapes, and this build has not verified a single one of them; a generated snippet
that happens to be wrong is exactly the "plausible wrong answer" the whole project
refuses. What the caller gets instead is the discipline: a probe that must change,
and an answer that says what was and was not observed.

`verified_against_app` is `false` unless all three hold: the caller says which
bridge answered (`bridge_version`, the value `ping` returns), that exact version
is listed in `VERIFIED_WRITES`, and the op is one this project really ran on it.
The gate is a table of measurements rather than a claim about the vendor API in
general: a version nobody here touched still answers `false`, so does `write`
(a caller-defined snippet is never gated in), and so does any change op whose own
probe did not move.
"""

import json

# What each named shortcut means in terms of the comparison. `save` is the one
# write with nothing observable to compare - the app's answer is the only fact -
# and it says so instead of pretending a probe could confirm it.
OPS = {
    "save": {"expect": "no_error",
             "why": "a save has no observable state to compare; the only fact is "
                    "that the call returned without an error"},
    "move": {"expect": "change",
             "why": "a moved object must read back somewhere else"},
    "create": {"expect": "change",
               "why": "a created object must read back as existing (or as a bigger "
                      "count) - a create that changes nothing is a no-op"},
    "write": {"expect": "change", "why": "caller-defined write; the probe decides"},
}

# Versions of the editor on which a write has ACTUALLY been performed by this
# project, per op, with what was measured. A key that is not here answers
# `verified_against_app: false` - the honest default for every build nobody ran.
#
# The evidence below was taken on 2026-11-01 against LCEDA Pro V4.1.60, bridge
# `4.1.60.198f38ab`, on a project this server created itself
# (`dmt_Project.createProject` + `dmt_Pcb.createPcb`) - never on a user's project.
# What each line rules out is stated next to it, because "it worked once" is only
# worth what the read-back behind it is worth.
VERIFIED_WRITES = {
    "4.1.60.198f38ab": {
        "app": "LCEDA Pro V4.1.60",
        "measured_on": "2026-11-01",
        "ops": ["create", "delete", "move", "save"],
        "run": [
            "dmt_Project.createProject + openProject + dmt_Pcb.createPcb + "
            "dmt_EditorControl.openDocument - until openDocument ran, every PCB "
            "call answered null; that is the order, not an accident",
            "pcb_PrimitiveLine.create('', 1, 0, 0, 10, 0, 0.2) -> probe "
            "(getAllPrimitiveId()).length went 0 -> 1, id 62e0d3e03e906d83",
            "pcb_Document.save() -> the project file in the app's own store "
            "(pcb_agent_mcp_write_probe.eprj2) went 409600 -> 569344 bytes and its "
            "sha256 changed",
            "closeDocument(tabId) then openDocument -> the line read back with the "
            "same geometry (layer 1, 0,0 - 10,0, w 0.2)",
            "delete(id) -> probe 1 -> 0; a second save changed the sha256 again",
            "create('', 1, 1, 1, 21, 1, 0.25) -> save -> 585728 bytes",
            "the app was killed and restarted, the project reopened by uuid from "
            "disk, and getAll() returned layer 1, (1,1)-(21,1), w 0.25",
            "second round through the tools rather than a hand-written client call: "
            "lceda_write_snippet produced the create call, lceda_pcb_write ran it "
            "(probe 1 -> 2), and a move via "
            "pcb_PrimitiveLine.modify(id, {startX: 2}) - the (primitiveId, property) "
            "signature is the one doc.api reports - moved the line and kept it "
            "across a SECOND full restart: (2,1)-(21,1) w 0.25 plus the new "
            "(1,5)-(21,5) w 0.3",
            "save() returned before the file on disk moved in the first reading "
            "(0.1 s: unchanged) and the file HAD moved by the next check about 8 s "
            "later; a later save changed the sha256 within 0.5 s with the size "
            "unchanged - so a disk check must poll, a single stat is not the answer",
        ],
        "still_not_verified": [
            "that what an invoke() reads back is what a person sees on the screen; "
            "every reading above is the app's own return value",
            "that a snippet with a wrong argument is refused rather than silently "
            "ignored - a create that does nothing does move no probe, but a create "
            "that does the WRONG thing is not detectable from a count",
            "that the .epro archive write path reaches the app (only the live "
            "editor path was measured)",
        ],
    },
}

# Kept verbatim from the pre-measurement build so the unlisted case answers
# exactly what it used to.
_UNVERIFIED_BY = {
    True: "before/after probe comparison inside the app",
    False: "the app returned no error - nothing was read back",
}
_UNVERIFIED_NOT = {
    True: ["that the file on disk changed",
           "that what the probe read is what a human sees"],
    False: ["that the file on disk changed",
            "that the change survives a reload"],
}


def _gate(report, op, bridge_version, expect):
    """Turn `verified_against_app` on only where a measurement backs it.

    Three conditions, all of which must hold, and each of which is a way to fail:

      * the caller names the bridge (`bridge_version`) - an unnamed bridge is
        never assumed to be the measured one;
      * that exact version string is a key in `VERIFIED_WRITES`;
      * the op is one this project ran on it, and THIS call showed its effect:
        a change op must have moved its probe, a no_error op must have returned.
    """
    entry = VERIFIED_WRITES.get(bridge_version) if bridge_version else None
    if entry is None or op not in entry.get("ops", ()):
        return report
    if expect == "change" and report.get("changed") is not True:
        return report
    report["verified_against_app"] = True
    where = "%s (bridge %s) on %s" % (entry["app"], bridge_version,
                                      entry["measured_on"])
    if op == "save":
        report["verified_by"] = (
            "a save of this kind was performed on %s and the project file on disk "
            "changed size and sha256; THIS call's save was not itself read back" % (where,))
    else:
        report["verified_by"] = (
            "a %s of this kind was performed and read back on %s; the probe above "
            "is yours, the discipline is this tool's" % (op, where))
    report["not_verified"] = list(entry.get("still_not_verified") or [])
    report["measured"] = {"app": entry["app"], "measured_on": entry["measured_on"],
                          "run": list(entry.get("run") or [])}
    return report


class WriteError(Exception):
    """A refusal from this module rather than from the application."""

    def __init__(self, message, reason_code, hint=None, extra=None):
        super().__init__(message)
        self.message = message
        self.reason_code = reason_code
        self.hint = hint
        self.extra = extra or {}


def _canon(value):
    """One comparable spelling of a read-back value.

    The app returns whatever the snippet returned; two probes of the same object
    can differ only by key order or by an integer that came back as a float. The
    comparison is deliberately on the canonical text so "it changed" is a claim
    about the value, not about JSON formatting.

    Integers become floats ONLY while a double can hold them exactly (2**53). A
    larger id - a uuid, a timestamp in microseconds - is left as it is: two
    neighbouring ids rounded onto the same double would read as "no change", and
    that is the wrong way to be wrong here.
    """
    try:
        return json.dumps(_canon_number(value), sort_keys=True, default=str)
    except (TypeError, ValueError):
        return repr(value)

def _canon_number(value):
    """Ints as floats where exact, recursively, so 1 and 1.0 compare equal."""
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        return float(value) if abs(value) <= 2 ** 53 else value
    if isinstance(value, dict):
        return dict((k, _canon_number(v)) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return [_canon_number(v) for v in value]
    return value


def run(client, session, code, probe=None, expect="change", timeout_ms=None,
        label=None, op=None, bridge_version=None):
    """Run one write snippet with a before/after probe.

    Returns a report dict; raises `WriteError` when the discipline is violated
    (no code, no probe where one is required) and returns `status: error` with
    `WRITE_HAD_NO_EFFECT` when the probe came back identical.

    `op` and `bridge_version` only feed the gate in `_gate`: with them the report
    can say a write of this kind really ran on this build; without them - the
    defaults - the answer is exactly what it was before any measurement existed.
    """
    if not session:
        raise WriteError("session is required", "ARG_REQUIRED",
                         hint="open the project first (lceda_pro_open)")
    if not code or not str(code).strip():
        raise WriteError("a write needs code to run", "ARG_REQUIRED",
                         hint="this tool never synthesises the snippet: the vendor's "
                              "write method names are not verified in this build")
    expect = expect or "change"
    if expect not in ("change", "no_error"):
        raise WriteError("expect must be 'change' or 'no_error', not %r" % (expect,),
                         "ARG_INVALID")
    if expect == "change" and not probe:
        raise WriteError(
            "expect='change' needs a probe", "ARG_REQUIRED",
            hint="pass `probe`: a snippet that returns the state you expect to "
                 "change (e.g. the object's position or a count). Without it this "
                 "tool cannot tell a successful write from a no-op, and it will not "
                 "guess")

    report = {"label": label, "op": op, "expect": expect, "probe": bool(probe),
              "before": None, "after": None, "changed": None,
              "verified_against_app": False,
              "verified_by": _UNVERIFIED_BY[bool(probe)],
              "not_verified": list(_UNVERIFIED_NOT[bool(probe)])}
    if probe:
        report["before"] = client.invoke(session, probe, timeout_ms=timeout_ms)["value"]
    out = client.invoke(session, code, timeout_ms=timeout_ms)
    report["ran"] = {"value": out["value"], "duration_ms": out["duration_ms"],
                     "logs": out["logs"]}
    if probe:
        report["after"] = client.invoke(session, probe, timeout_ms=timeout_ms)["value"]
        report["changed"] = _canon(report["before"]) != _canon(report["after"])
    # Only now, with the effect known, can the measurement table be consulted:
    # a create whose probe did not move is not "verified" even on a measured build.
    _gate(report, op, bridge_version, expect)
    if expect == "change" and report["changed"] is False:
        return {"status": "error", "reason_code": "WRITE_HAD_NO_EFFECT",
                "message": "the snippet ran without an error and the probe read the "
                           "same value before and after",
                "hint": "read `before` and `after`: either the snippet targeted "
                        "something else, or the app silently ignored it. Nothing "
                        "here claims the design changed",
                "report": report}
    return {"status": "ok", "report": report}
