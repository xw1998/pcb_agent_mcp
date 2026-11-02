"""A run ledger: what this server was asked to do to which board, and what it said.

The point is not logging. It is that the second attempt at a board should not
have to re-derive what the first attempt learned: which pitch was searched,
which nets failed and why, how many balls stayed buried. One JSON line per run,
keyed by the sha256 of the board file, so `board_run_ledger` can answer "has this
board been tried before, and what happened".

Rules this module keeps:

* **it never changes an answer** - recording is best effort, swallows its own
  errors, and nothing it returns reaches the tool's value;
* **it is not a cache** - a ledger entry is a record of what was said, never
  reused as a result;
* **it holds no design data it does not need** - a digest, the arguments that
  change behaviour, the verdict, and nothing that could reconstruct the board;
* **default on, one switch off** - `PCB_AGENT_LEDGER=0` writes nothing at all;
* **a run names its owner** - when the work ran inside a background job the line
  carries that job's id, so `board_run_ledger(owner=...)` can answer "what did
  *that* job do" without matching on timestamps. A run made directly in a request
  has no owner and says `null` rather than borrowing the nearest id.
"""
import hashlib
import json
import os
import time

FILENAME = "runs.jsonl"
MAX_BYTES = 4 * 1024 * 1024          # one old ledger is rotated, never deleted
KINDS = ("route", "fanout", "drc", "fab", "apply")

def home():
    got = os.environ.get("PCB_AGENT_HOME")
    if got:
        return got
    return os.path.join(os.path.expanduser("~"), ".pcb_agent_mcp")

def path():
    return os.path.join(home(), FILENAME)

def enabled():
    return os.environ.get("PCB_AGENT_LEDGER", "1") not in ("0", "false", "off", "")

def digest(board_path):
    """sha256 of the board file, or None when it cannot be read."""
    try:
        h = hashlib.sha256()
        with open(board_path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except (OSError, TypeError):
        return None

def record(kind, payload, owner=None):
    """Append one run. Returns True when it was written; never raises.

    A failure to record must never fail the tool that was asked to do real work,
    so every exception here ends as `False`. `owner` is the job id the work ran
    under, or None for work done directly in a request.
    """
    if not enabled():
        return False
    if kind not in KINDS:
        raise ValueError("unknown ledger kind %r (know: %s)"
                         % (kind, ", ".join(KINDS)))
    try:
        line = {"at": round(time.time(), 3),
                "kind": kind,
                "version": _version(),
                "owner": owner or None,
                "payload": payload}
        text = json.dumps(line, ensure_ascii=False, sort_keys=True, default=str)
        target = path()
        directory = os.path.dirname(target)
        if directory and not os.path.isdir(directory):
            os.makedirs(directory)
        try:
            if os.path.getsize(target) > MAX_BYTES:
                os.replace(target, target + ".1")
        except OSError:
            pass
        with open(target, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(text + "\n")
        return True
    except Exception:                      # noqa: BLE001 - recording is not the job
        return False

def _version():
    try:
        from .. import __version__
        return __version__
    except Exception:                      # noqa: BLE001
        return None

def entries(kind=None, digest_value=None, limit=50, owner=None):
    """Newest first. A malformed line is skipped, never fatal."""
    target = path()
    if not os.path.isfile(target):
        return []
    out = []
    try:
        with open(target, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if kind and row.get("kind") != kind:
                    continue
                if digest_value and (row.get("payload") or {}).get("board") != digest_value:
                    continue
                if owner and row.get("owner") != owner:
                    continue
                out.append(row)
    except OSError:
        return []
    out.reverse()
    return out[:int(limit)] if limit else out

def summary(kind=None, digest_value=None, limit=50, owner=None):
    """A compact view: one row per run, plus the totals worth knowing."""
    rows = entries(kind=kind, digest_value=digest_value, limit=limit, owner=owner)
    compact = []
    for row in rows:
        p = row.get("payload") or {}
        compact.append({"at": row.get("at"), "kind": row.get("kind"),
                        "version": row.get("version"), "owner": row.get("owner"),
                        "board": (p.get("board") or "")[:12],
                        "verdict": p.get("verdict"), "note": p.get("note")})
    totals = {}
    for row in rows:
        key = "%s:%s" % (row.get("kind"), (row.get("payload") or {}).get("verdict"))
        totals[key] = totals.get(key, 0) + 1
    return {"file": path(), "count": len(rows), "runs": compact, "totals": totals}
