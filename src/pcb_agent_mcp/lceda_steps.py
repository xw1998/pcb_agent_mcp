"""Record a run of editor steps that already happened, then replay it and COMPARE.

`lceda_pro_steps` runs a list of snippets one by one and answers with each step's
value. That answer is a record of one particular run of one particular design, and
until now it evaporated: the second project needed the steps rewritten from memory.

This module keeps the three things a replay needs, and keeps them apart:

    the SCRIPT    the steps (label, code, fn, js_args, timeout_ms) - yours
    the RECORD    what each step RETURNED on the run that worked - the app's
    the REPLAY    a second run that compares value by value against the record

The comparison is the whole point. A replay that "ran all 8 steps without an error"
is not evidence that it reproduced anything: a move onto the position it already
has, a create that silently no-ops, a snippet that targeted a stale uuid - all of
them return without an error. So every replayed step reports

    matches_recording: true | false | null

where `null` means the step has no recorded value to compare against (it was
recorded without one, or it is the first run) and that is said out loud rather
than counted as a pass.

Three things this module will not do:

  * it will not run anything by itself - `record` and `save`/`load` touch no app;
    `replay` takes a client, and the caller (a tool) owns the connection;
  * it will not call a replay idempotent. A `create` run twice creates two things.
    Whether a script is safe to re-run is a property of the snippets, which only
    their author knows, so `idempotent` is reported as `"unknown"` and never as
    `true`;
  * it will not turn a mismatch into an error of the run. A differing value is
    `status: "differs"` on that step with both values attached: the replay did what
    it was told, and what came back is a measurement, not a verdict.

The script file is JSON with its own sha256 over a canonical form, so "this is the
script that produced that log" is checkable instead of asserted.
"""

import hashlib
import io
import json
import os
import time

FORMAT = "pcb-agent-mcp/step-script/1"

# A replay step's outcome. `matches_recording` is deliberately tri-state.
MATCH = "match"
DIFFERS = "differs"
NO_RECORD = "no_recorded_value"
FAILED = "failed"

class StepError(Exception):
    """A refusal from this module rather than from the application."""

    def __init__(self, message, reason_code, hint=None, extra=None):
        super().__init__(message)
        self.message = message
        self.reason_code = reason_code
        self.hint = hint
        self.extra = extra or {}

def home():
    """Where scripts live when the caller does not name a path.

    The same server-owned directory the run ledger uses (`PCB_AGENT_HOME`, else
    `~/.pcb_agent_mcp`), never beside a project: a generated file next to the
    user's board is exactly what this project refuses to do.
    """
    from . import ledger
    return os.path.join(ledger.home(), "step-scripts")

def _text(value):
    return value if isinstance(value, str) else ""

def _canon(value):
    """One comparable spelling of a value, same rule as the write discipline."""
    from .lceda_write import _canon as canon
    return canon(value)

def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def _normalise_step(step, index):
    """One step of a script -> (clean dict). A step without code is a refusal."""
    if isinstance(step, str):
        step = {"code": step}
    if not isinstance(step, dict):
        raise StepError("step %d is not an object or a string" % (index + 1,),
                        "ARG_INVALID",
                        hint="a step is {label, code, js_args, fn, timeout_ms} or a "
                             "bare code string")
    code = _text(step.get("code"))
    if not code.strip():
        raise StepError("step %d has no code" % (index + 1,), "ARG_INVALID",
                        hint="a step with nothing to run cannot be recorded or "
                             "replayed; there is nothing to substitute for it")
    out = {"label": _text(step.get("label")) or "step %d" % (index + 1,),
           "code": code}
    if step.get("fn"):
        out["fn"] = step["fn"]
    if step.get("js_args") is not None:
        out["js_args"] = step["js_args"]
    if step.get("timeout_ms") is not None:
        out["timeout_ms"] = step["timeout_ms"]
    return out

def make_script(steps, note=None, source=None, session=None):
    """A script dict from steps that already ran (or from steps you just wrote).

    `expected` is filled from a step's own `value` when the caller passes back what
    `lceda_pro_steps` answered, which is what makes a later replay comparable. A
    step with no value is recorded as having no expected value - not as expecting
    `null`, which would compare equal to a missing answer.
    """
    if not isinstance(steps, (list, tuple)) or not steps:
        raise StepError("steps must be a non-empty list", "ARG_INVALID")
    clean, recorded, missing = [], 0, 0
    for i, raw in enumerate(steps):
        step = _normalise_step(raw, i)
        has = isinstance(raw, dict) and "value" in raw
        if has:
            step["expected"] = raw["value"]
            recorded += 1
        else:
            missing += 1
        clean.append(step)
    body = {"format": FORMAT, "steps": clean}
    return {
        "format": FORMAT,
        "at": round(time.time(), 3),
        "note": _text(note) or None,
        "source": source or {},
        "session": session,
        "steps": clean,
        "sha256": _sha(json.dumps(body, sort_keys=True, ensure_ascii=False,
                                 default=str)),
        "recorded_values": recorded,
        "steps_without_a_recorded_value": missing,
        "idempotent": "unknown",
        "why_idempotence_is_unknown":
            "whether these snippets are safe to run a second time is a property of "
            "the snippets (a create run twice creates two things), and this module "
            "cannot know it - replay therefore compares values instead of assuming "
            "they will match",
    }

def save(script, path=None, overwrite=False):
    """Write the script as JSON. Never overwrites unless told to."""
    if not isinstance(script, dict) or not script.get("steps"):
        raise StepError("script has no steps", "ARG_INVALID")
    target = path or os.path.join(home(), "steps-%s.json" % script["sha256"][:12])
    target = os.path.abspath(os.path.expanduser(target))
    if os.path.exists(target) and not overwrite:
        raise StepError("%r already exists" % (target,), "FILE_EXISTS",
                        hint="pass overwrite=true, or let the path default to the "
                             "one named after the script's own sha256")
    directory = os.path.dirname(target)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    text = json.dumps(script, sort_keys=True, ensure_ascii=False, indent=1,
                      default=str) + "\n"
    tmp = target + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    os.replace(tmp, target)
    return {"path": target, "bytes": len(text.encode("utf-8")),
            "sha256": script.get("sha256")}

def load(path):
    """Read a script back, and CHECK it is the script its sha256 names."""
    target = os.path.abspath(os.path.expanduser(path))
    if not os.path.isfile(target):
        raise StepError("no such script: %r" % (target,), "FILE_NOT_FOUND")
    with io.open(target, "r", encoding="utf-8") as fh:
        got = json.load(fh)
    if not isinstance(got, dict) or got.get("format") != FORMAT:
        raise StepError("%r is not a %s script (format %r)"
                        % (target, FORMAT, got.get("format")
                           if isinstance(got, dict) else None),
                        "FORMAT_UNKNOWN")
    steps = got.get("steps") or []
    if not steps:
        raise StepError("%r holds no steps" % (target,), "ARG_INVALID")
    body = {"format": FORMAT, "steps": steps}
    recomputed = _sha(json.dumps(body, sort_keys=True, ensure_ascii=False,
                                 default=str))
    got["sha256_recomputed"] = recomputed
    got["sha256_matches"] = (recomputed == got.get("sha256"))
    got["path"] = target
    if not got["sha256_matches"]:
        got["why_it_can_differ"] = (
            "the file was edited after it was written, or written by another "
            "version of this module; a replay compares against THESE steps either "
            "way, and the flag is here so a changed script is never mistaken for "
            "the one that was recorded")
    return got

def replay(client, session, script, compare=True, timeout_ms=None, should_stop=None):
    """Run the script again and compare each step with what was recorded.

    `should_stop` is an optional zero-argument callable consulted before each step
    (the tool wires `board_progress(action='stop')` into it). A step that raises
    ends the replay there: continuing after a failure would run the rest of the
    script against a design that is not in the state the script assumes.
    """
    if not session:
        raise StepError("session is required", "ARG_REQUIRED",
                        hint="open the project inside the editor first "
                             "(lceda_pro_open), or pass a session id")
    steps = (script or {}).get("steps") or []
    if not steps:
        raise StepError("script has no steps", "ARG_INVALID")
    rows, stopped_at, failed_at = [], None, None
    for i, step in enumerate(steps):
        if should_stop is not None and should_stop():
            stopped_at = i
            break
        row = {"index": i, "label": step.get("label") or "step %d" % (i + 1,),
               "code": step.get("code")}
        try:
            out = client.invoke(session, step["code"], fn=step.get("fn") or "run",
                                args=step.get("js_args"),
                                timeout_ms=step.get("timeout_ms") or timeout_ms)
        except Exception as exc:                      # noqa: BLE001
            code = getattr(exc, "code", None) or type(exc).__name__
            row.update({"outcome": FAILED, "matches_recording": False,
                        "error": str(getattr(exc, "message", None) or exc),
                        "error_code": code})
            rows.append(row)
            _from_ledger(row)
            failed_at = i
            break
        value = (out or {}).get("value")
        row["value"] = value
        row["duration_ms"] = (out or {}).get("duration_ms")
        if not compare:
            row["outcome"] = NO_RECORD
            row["matches_recording"] = None
            row["why"] = "compare was switched off for this replay"
        elif "expected" not in step:
            row["outcome"] = NO_RECORD
            row["matches_recording"] = None
            row["why"] = ("this step was recorded without a value, so there is "
                          "nothing to compare - that is not a pass")
        elif _canon(value) == _canon(step["expected"]):
            row["outcome"] = MATCH
            row["matches_recording"] = True
        else:
            row["outcome"] = DIFFERS
            row["matches_recording"] = False
            row["expected"] = step["expected"]
            row["why"] = ("the step returned a different value than the recorded "
                          "run. That is a measurement, not a failure: re-running a "
                          "create on a design that already has the object looks "
                          "exactly like this")
        rows.append(row)
        _from_ledger(row)
    ran = [r for r in rows if r["outcome"] != FAILED]
    counts = {MATCH: 0, DIFFERS: 0, NO_RECORD: 0, FAILED: 0}
    for r in rows:
        counts[r["outcome"]] = counts.get(r["outcome"], 0) + 1
    report = {
        "steps_planned": len(steps),
        "steps_run": len(ran),
        "stopped_before_step": stopped_at,
        "failed_at_step": failed_at,
        "counts": counts,
        "rows": rows,
        "idempotent": "unknown",
        "verified_against_app": ("each step's value is the editor's own reply, and "
                                 "the comparison is against the recorded run"
                                 if rows else False),
        "not_verified": [
            "that the recorded run and this one started from the same design state",
            "that a matching value means the same edit happened (a value can match "
            "for the wrong reason)",
            "that the project file on disk was written - only the snippet decides "
            "whether to save",
        ],
    }
    if stopped_at is not None:
        report["not_run"] = [s.get("label") for s in steps[stopped_at:]]
    if failed_at is not None:
        report["not_run"] = [s.get("label") for s in steps[failed_at + 1:]]
    if counts[DIFFERS] or counts[FAILED]:
        report["status"] = "differs"
    elif counts[MATCH]:
        report["status"] = "reproduced"
    else:
        report["status"] = "inconclusive"
    return report

def _from_ledger(row):
    """Mirror the replay into the same step log `board_progress` reads."""
    try:
        from . import edit as _edit
        _edit.plan._emit("lceda-replay", {
            "kind": "step", "seq": row["index"] + 1, "ops": [row["label"]],
            "duration_ms": row.get("duration_ms"),
            "sha256": None,
            "value_preview": str(row.get("value", row.get("error")))[:200]})
    except Exception:                                  # noqa: BLE001
        pass                          # a log that cannot be written is not a failure
