"""A tiny append-only journal, so a session that dies can pick up in ~1 KiB.

The failure this exists for is measured, not imagined. On 2026-11-04 a long
board turn hit the host's context compactor (`original_tokens=88078`,
`compacted_tokens=59054`), the request that carried the compaction took
260388 ms, and the client came back with a brand-new session. Nothing on disk
was lost; what was lost was the conversation's own memory of what had been
drawn, and paying for that back cost a multi-page handover summary.

A journal line is about 100 bytes. So the handover becomes "read the tail",
and the summary nobody likes writing can stay short.

What this module will NOT do: it never records work as done. It stores exactly
what a caller wrote, and `tail` returns it with the byte cost shown. An entry
that says "g4n5 passed" proves a string was stored, not that a board was
written - the board file and its DRC are the only witnesses to that.
"""

import io
import json
import os
import time

from . import ledger

FILENAME = "journal.jsonl"
MAX_TEXT = 400                 # one entry is a note, not a report
MAX_ENTRIES = 4000             # then it refuses and asks for a clear, never drops
MAX_FILE_BYTES = 2 * 1024 * 1024
KINDS = ("board", "route", "drc", "apply", "note", "handover")


def path():
    return os.path.join(ledger.home(), FILENAME)


def enabled():
    got = os.environ.get("PCB_AGENT_JOURNAL")
    return (got is None) or (str(got).strip().lower() not in ("0", "false", "off", "no"))


def _dialect():
    return {"kind": "one of %s" % " / ".join(KINDS),
            "text": "at most %d characters; this is a note, not a report" % MAX_TEXT,
            "journal file": path(),
            "what it proves": "that this string was stored. Not that any board "
                              "was written or any DRC passed"}


class JournalError(Exception):
    def __init__(self, code, message, hint=None):
        Exception.__init__(self, message)
        self.code = code
        self.message = message
        self.hint = hint

    def as_dict(self):
        out = {"status": "error", "reason_code": self.code, "message": self.message}
        if self.hint:
            out["hint"] = self.hint
        out["expected"] = _dialect()
        return out


def _count():
    """(entries, bytes) without loading the whole file."""
    p = path()
    try:
        size = os.path.getsize(p)
    except OSError:
        return 0, 0
    n = 0
    try:
        with io.open(p, encoding="utf-8", newline="") as fh:
            for line in fh:
                if line.strip():
                    n += 1
    except OSError:
        return 0, size
    return n, size


def append(kind, text, board=None, data=None):
    """Append one entry. Returns the receipt (never the whole journal)."""
    if not enabled():
        raise JournalError("JOURNAL_DISABLED",
                           "PCB_AGENT_JOURNAL is off, so nothing was written",
                           "unset PCB_AGENT_JOURNAL to turn the journal back on")
    kind = (kind or "note").strip().lower()
    if kind not in KINDS:
        raise JournalError("ARG_INVALID", "kind %r is not one of %s"
                           % (kind, " / ".join(KINDS)))
    text = (text or "").strip()
    if not text:
        raise JournalError("ARG_REQUIRED", "text is required: an empty note "
                           "would be a line that says nothing")
    if len(text) > MAX_TEXT:
        raise JournalError("ARG_TOO_LONG",
                           "text is %d characters; the cap is %d"
                           % (len(text), MAX_TEXT),
                           "keep the note to the fact the next session needs "
                           "(what was drawn, what is left, which file)")
    entries, size = _count()
    if entries >= MAX_ENTRIES or size >= MAX_FILE_BYTES:
        raise JournalError("JOURNAL_FULL",
                           "the journal already holds %d entries / %d bytes"
                           % (entries, size),
                           "board_journal(action='clear', confirm=true) archives it "
                           "to %s.bak-<stamp> and starts fresh; nothing is deleted"
                           % FILENAME)
    entry = {"t": int(time.time()), "at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
             "kind": kind, "text": text}
    if board:
        entry["board"] = str(board)
    if data:
        entry["data"] = data
    line = json.dumps(entry, ensure_ascii=False, sort_keys=True)
    try:
        os.makedirs(ledger.home(), exist_ok=True)
        with io.open(path(), "a", encoding="utf-8", newline="") as fh:
            fh.write(line + "\n")
    except OSError as exc:
        raise JournalError("JOURNAL_UNWRITABLE", "cannot append to %s: %s"
                           % (path(), exc))
    total, now = _count()
    return {"status": "ok", "action": "add", "journal": path(),
            "entries_total": total, "bytes": now, "line_bytes": len(line.encode("utf-8")),
            "entry": entry,
            "what_this_proves": "that the text above is on disk. Not that the "
                                "board work it describes happened"}


def tail(limit=8, kind=None):
    """The last `limit` entries, plus the cost of having them."""
    limit = max(1, min(int(limit or 8), 50))
    total, size = _count()
    out = {"status": "ok", "action": "tail", "journal": path(),
           "entries_total": total, "bytes": size, "entries": [],
           "what_this_proves": "what a previous session wrote down. Re-read the "
                               "board file and run the DRC before trusting it"}
    if not enabled():
        out["status"] = "disabled"
        out["message"] = "PCB_AGENT_JOURNAL is off; an empty list here means the " \
                         "journal is disabled, not that no work was done"
        return out
    if not total:
        out["message"] = ("the journal is empty: nothing has been written yet, "
                          "which is not the same as nothing having been done")
        return out
    keep = []
    try:
        with io.open(path(), encoding="utf-8", newline="") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except ValueError:
                    keep.append({"kind": "unreadable", "text": line[:MAX_TEXT]})
                    continue
                if kind and item.get("kind") != kind:
                    continue
                keep.append(item)
    except OSError as exc:
        raise JournalError("JOURNAL_UNREADABLE", "cannot read %s: %s" % (path(), exc))
    out["entries"] = keep[-limit:]
    out["returned"] = len(out["entries"])
    out["reply_bytes"] = len(json.dumps(out, ensure_ascii=False).encode("utf-8"))
    return out


def clear(confirm):
    if confirm is not True:
        raise JournalError("CONFIRM_REQUIRED",
                           "clear moves the journal aside; pass confirm=true",
                           "nothing is destroyed: the file is renamed to "
                           "%s.bak-<stamp>" % FILENAME)
    p = path()
    if not os.path.isfile(p):
        return {"status": "ok", "action": "clear", "cleared": False,
                "journal": p, "message": "there was no journal file to archive"}
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dst = "%s.bak-%s" % (p, stamp)
    try:
        os.replace(p, dst)
    except OSError as exc:
        raise JournalError("JOURNAL_UNWRITABLE", "cannot archive %s: %s" % (p, exc))
    return {"status": "ok", "action": "clear", "cleared": True, "journal": p,
            "archived_to": dst,
            "message": "the old journal was renamed, not deleted"}
