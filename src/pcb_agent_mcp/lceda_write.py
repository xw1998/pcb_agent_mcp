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

`verified_against_app` is therefore `false` in every answer from this module. The
read paths that WERE run against the application say so themselves in
`lceda_live`; a write is not one of them.
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
        label=None):
    """Run one write snippet with a before/after probe.

    Returns a report dict; raises `WriteError` when the discipline is violated
    (no code, no probe where one is required) and returns `status: error` with
    `WRITE_HAD_NO_EFFECT` when the probe came back identical.
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

    report = {"label": label, "expect": expect, "probe": bool(probe),
              "before": None, "after": None, "changed": None,
              "verified_against_app": False,
              "verified_by": ("before/after probe comparison inside the app"
                              if probe else
                              "the app returned no error - nothing was read back"),
              "not_verified": (["that the file on disk changed",
                                "that the change survives a reload"]
                               if not probe else
                               ["that the file on disk changed",
                                "that what the probe read is what a human sees"])}
    if probe:
        report["before"] = client.invoke(session, probe, timeout_ms=timeout_ms)["value"]
    out = client.invoke(session, code, timeout_ms=timeout_ms)
    report["ran"] = {"value": out["value"], "duration_ms": out["duration_ms"],
                     "logs": out["logs"]}
    if probe:
        report["after"] = client.invoke(session, probe, timeout_ms=timeout_ms)["value"]
        report["changed"] = _canon(report["before"]) != _canon(report["after"])
    if expect == "change" and report["changed"] is False:
        return {"status": "error", "reason_code": "WRITE_HAD_NO_EFFECT",
                "message": "the snippet ran without an error and the probe read the "
                           "same value before and after",
                "hint": "read `before` and `after`: either the snippet targeted "
                        "something else, or the app silently ignored it. Nothing "
                        "here claims the design changed",
                "report": report}
    return {"status": "ok", "report": report}
