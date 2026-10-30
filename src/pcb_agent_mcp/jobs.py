"""Long calls that outlive one request: submit now, ask later.

The reason this exists is measured, not imagined: DRC on an 88.7 MB board did not
finish inside the per-call budget, and a tool that blocks for fifteen minutes is a
tool nobody can use on that board. The answer to a slow call is not a bigger
timeout and certainly not a partial verdict - it is to hand back an id and let the
caller ask again.

What a job is:

    submit(kind, fn)  -> an id, immediately; `fn` runs on a worker thread
    get(id)           -> state 'running' | 'done' | 'failed' | 'cancelled',
                         plus the fn's own return value once it is done

The rules this module keeps, because each of them is a way to be plausible and
wrong:

  * **a running job has NO result**. `get` answers `state: running` and nothing
    else - there is no "latest known" verdict, no last-run cache that could be
    mistaken for this run;
  * **a job is not a queue**. Submitting the same work twice is two jobs, and the
    second one gets no credit for the first;
  * **cancelling is a request, not a kill**. The work already handed to a
    subprocess keeps running to its own end; `cancel` marks the job so its result
    is discarded and says so, rather than pretending it stopped the process;
  * **jobs die with the process**. They live in memory: a restart loses every one
    of them, which is why `get` on an unknown id is `JOB_NOT_FOUND` and never an
    empty answer;
  * **a failing job keeps the failure**. The exception's type and message are
    stored, so a crash is reported as a crash instead of being re-raised at
    whoever polls second.

Nothing here retries, resumes or persists. A resume would need the work to be
restartable at a known point, and none of the current slow calls is.
"""

import threading
import time
import uuid

# A per-kind cap: two DRC runs at once is already 1.8 GB of KiCad; more is a
# machine that stops answering rather than a faster answer.
MAX_RUNNING = {"default": 2}

# How long a finished job is kept so a poller that was away for a while still
# finds its answer. After this the id is gone and `get` says so.
KEEP_S = 3600.0

_LOCK = threading.Lock()
_JOBS = {}
_SEQ = [0]

class JobError(Exception):
    """A refusal from this module rather than from the work."""

    def __init__(self, message, reason_code, hint=None, extra=None):
        super().__init__(message)
        self.message = message
        self.reason_code = reason_code
        self.hint = hint
        self.extra = extra or {}

def _new_id(kind):
    _SEQ[0] += 1
    return "%s-%d-%s" % (kind, _SEQ[0], uuid.uuid4().hex[:6])

def _prune():
    """Forget finished jobs older than KEEP_S. Called under the lock."""
    now = time.time()
    for jid in [k for k, v in _JOBS.items()
                if v["state"] != "running"
                and now - (v.get("finished_at") or now) > KEEP_S]:
        _JOBS.pop(jid, None)

def submit(kind, fn, label=None, timeout_hint_s=None):
    """Start `fn()` on a worker thread and return the job record."""
    if not callable(fn):
        raise JobError("fn must be callable", "ARG_INVALID")
    cap = MAX_RUNNING.get(kind, MAX_RUNNING["default"])
    with _LOCK:
        _prune()
        running = sum(1 for v in _JOBS.values()
                      if v["state"] == "running" and v["kind"] == kind)
        if running >= cap:
            raise JobError(
                "%d %s job(s) are already running and this build allows %d"
                % (running, kind, cap), "JOB_BUSY",
                hint="wait for one with board_job(action='list'), or finish it with "
                     "action='wait'; a wider cap means more of the machine per run, "
                     "not a faster one")
        jid = _new_id(kind)
        job = {"id": jid, "kind": kind, "label": label,
               "state": "running", "submitted_at": round(time.time(), 3),
               "finished_at": None, "result": None, "error": None,
               "cancel_requested": False, "timeout_hint_s": timeout_hint_s,
               "survives_a_restart": False}
        _JOBS[jid] = job
    # The answer describes the job AS OF SUBMISSION, not as of one thread switch
    # later: a fast job could otherwise come back as 'done' on some runs and
    # 'running' on others, which is a race in the ANSWER and not in the work.
    accepted = dict(job)
    accepted.pop("result", None)
    accepted["state_is_as_of_submission"] = True
    thread = threading.Thread(target=_work, args=(jid, fn),
                              name="pcb-job-" + jid, daemon=True)
    thread.start()
    return accepted

def _work(jid, fn):
    try:
        value = fn()
        with _LOCK:
            job = _JOBS.get(jid)
            if job is None:
                return
            if job["cancel_requested"]:
                job["state"] = "cancelled"
                job["result"] = None
                job["error"] = ("the work finished after a cancel was requested; its "
                                "result is discarded rather than half-used")
            else:
                job["state"] = "done"
                job["result"] = value
            job["finished_at"] = round(time.time(), 3)
    except BaseException as exc:                       # noqa: BLE001
        with _LOCK:
            job = _JOBS.get(jid)
            if job is None:
                return
            job["state"] = "cancelled" if job["cancel_requested"] else "failed"
            job["error"] = {"type": type(exc).__name__, "message": str(exc)}
            job["finished_at"] = round(time.time(), 3)

def snapshot(jid):
    """One job as it currently stands. Never invents a result."""
    with _LOCK:
        job = _JOBS.get(jid)
        if job is None:
            raise JobError("no job with id %r (jobs live in memory and are lost "
                           "when the server restarts)" % (jid,), "JOB_NOT_FOUND",
                           hint="board_job(action='list') shows the ids that exist")
        out = dict(job)
    if out["state"] == "running":
        out["elapsed_s"] = round(time.time() - out["submitted_at"], 1)
        out.pop("result", None)
        out["why_there_is_no_result"] = (
            "the work is still running; this module never reports a partial or "
            "previous verdict as if it were this one")
    elif out["state"] == "done":
        out["elapsed_s"] = round((out["finished_at"] or 0) - out["submitted_at"], 1)
    return out

def get(jid):
    return snapshot(jid)

def list_jobs(kind=None, state=None, limit=50):
    with _LOCK:
        _prune()
        jobs = [dict(v) for v in _JOBS.values()
                if (kind is None or v["kind"] == kind)
                and (state is None or v["state"] == state)]
    jobs.sort(key=lambda j: j["submitted_at"], reverse=True)
    for j in jobs:
        j["elapsed_s"] = round(((j.get("finished_at") or time.time())
                                - j["submitted_at"]), 1)
    return jobs[:limit]

def cancel(jid):
    """Ask the job to be discarded. Returns the job; never claims a process died."""
    with _LOCK:
        job = _JOBS.get(jid)
        if job is None:
            raise JobError("no job with id %r" % (jid,), "JOB_NOT_FOUND")
        job["cancel_requested"] = True
        if job["state"] == "running":
            job["note"] = ("cancel requested: the result will be discarded when the "
                           "work returns. The subprocess it already started is not "
                           "killed, so the machine is still busy until it exits")
        elif job["state"] == "done":
            job["state"] = "cancelled"
            job["result"] = None
            job["note"] = ("it had already finished; the result was dropped, not "
                           "returned")
        else:
            job["note"] = "nothing was running"
        return dict(job)

def wait(jid, timeout_s=30.0, poll_s=0.2):
    """Block until the job leaves 'running' or the budget runs out.

    The budget is per call by design: a caller that wants to wait five minutes
    calls this four times and sees the elapsed time in between, instead of one call
    that looks hung.
    """
    deadline = time.time() + max(0.0, float(timeout_s or 0))
    while True:
        job = snapshot(jid)
        if job["state"] != "running":
            return job
        if time.time() >= deadline:
            job["waited_s"] = round(float(timeout_s or 0), 1)
            job["still_running"] = True
            return job
        time.sleep(poll_s)

def reset():
    """Forget every job. Only used by tests and by a deliberate restart."""
    with _LOCK:
        _JOBS.clear()
