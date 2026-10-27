"""The LCEDA Pro (`.epro`) write path: append to the log, never rewrite it.

`.epro` stores a board as an **append-only log** - one JSON object per line,
`{"type","id","ticket"}||{payload}`, where the highest ticket for a `(type, id)`
wins and an empty payload means "this object is gone". That shape decides how a
writer must behave, and it is not the same as KiCad's:

* a change is a **new line**, not an edited line. Rewriting the log in place
  ("just modify the object I care about") throws away history that the reader we
  ship in `fidelity/epro_doc.py` is built to preserve, and produces a file that
  differs in ways nobody can audit;
* the ticket must **increase**, and it must increase past every ticket already in
  the member. A ticket that is too small is silently ignored by the application -
  the file stays valid and the edit does not happen. There is no error message.
  That is the single most dangerous failure in this format, so the ticket is
  computed from the log itself and re-read after the write;
* **key names are guesses until proven.** This repository had no real `.epro` on
  disk when the writer was written, so `set_fields` refuses to touch a key that
  `key_inventory()` did not actually observe in that object type. A wrong key
  name in EasyEDA Pro is not an error either; it is simply ignored.

Same ceremony as the KiCad path, for the same reasons:

    epro_propose  ->  computes the new archive, writes nothing, returns a plan_id
    epro_apply    ->  confirm=true, checkpoint, atomic write, four gates

and the gates do not trust this file:

1. `bytes_written`        - the bytes on disk are the bytes that were planned
2. `append_only`          - the target log member STARTS with its old bytes, and
                            every appended byte parses as a record
3. `members_untouched`    - every other ZIP member is byte-identical to before
4. `postconditions`       - each operation's own promise is re-read from the
                            resolved state of the file that is now on disk

`unit_verified` is reported as a fact, not as a gate: it is `false` until a real
`.epro` from the user's installation confirms the 0.01-inch unit and the key
names.
"""
import collections
import io
import json
import os
import time
import uuid as _uuid
import zipfile

from ..fidelity import epro_doc as _ep
from . import plan as _plan
from .fsutil import atomic_write as _atomic_write
from .fsutil import sha256 as _sha256

EditError = _plan.EditError

UNIT_PER_MM = None      # not guessed; see `unit_note()`
UNIT_NOTE = ("payload lengths are in 0.01 inch (0.254 mm) according to the format "
             "notes, but no real .epro has confirmed it here - so no coordinate is "
             "ever written by this module without saying unit_verified=false")


# --------------------------------------------------------------- helpers
def _member_of(doc, requested=None):
    """The log member to append to: the PCB document unless told otherwise."""
    names = doc.names()
    if requested:
        if requested not in names:
            raise EditError("member %r is not in the archive; it has: %s"
                            % (requested, ", ".join(names[:12])), "MEMBER_NOT_FOUND",
                            members=names)
        return requested
    for name in names:
        if name.lower().endswith(".epcb"):
            return name
    for name in names:
        if name.lower().endswith(_ep.TEXT_EXTS):
            return name
    raise EditError("the archive has no text member to append a record to (only %s)"
                    % ", ".join(names[:12]), "NO_LOG_MEMBER", members=names)


def _max_ticket(doc, name):
    top = 0
    for _lineno, header, _payload, _raw in doc.records(name):
        if not header:
            continue
        try:
            ticket = int(header.get("ticket"))
        except (TypeError, ValueError):
            continue
        if ticket > top:
            top = ticket
    return top


def _record_line(otype, oid, ticket, payload):
    header = {"type": otype, "id": oid, "ticket": int(ticket)}
    return (json.dumps(header, sort_keys=True, separators=(",", ":")) + "||" +
            json.dumps(payload, sort_keys=True, separators=(",", ":")))


def _append_lines(blob, lines):
    """Append records to a member's bytes without touching a single old byte."""
    if not lines:
        return blob
    tail = b""
    if blob and not blob.endswith(b"\n"):
        tail = b"\n"
    body = ("\n".join(lines) + "\n").encode("utf-8", "surrogateescape")
    return blob + tail + body


def _inventory(doc, name):
    return doc.key_inventory(name)


def _require_keys(inv, otype, fields, allow_new_keys):
    """Key names are read, not invented: refuse a key the file never showed."""
    slot = inv.get(otype) or {}
    known = set((slot.get("keys") or {}).keys())
    unknown = sorted(set(fields) - known)
    if not unknown:
        return []
    if not known:
        if allow_new_keys:
            return ["object type %r appears nowhere in this log, so every key in it "
                    "(%s) is a guess" % (otype, ", ".join(sorted(fields)))]
        raise EditError(
            "object type %r appears nowhere in this archive, so the key names %s "
            "cannot be checked (pass allow_new_keys=true only if you accept writing "
            "a guess)" % (otype, ", ".join(sorted(fields))),
            "UNKNOWN_OBJECT_TYPE", type=otype, keys=sorted(fields),
            known_types=sorted(inv.keys()))
    if not allow_new_keys:
        raise EditError(
            "key(s) %s were never observed on a %r object in this file (it has: %s). "
            "EasyEDA Pro ignores an unknown key silently, so writing it would look "
            "like success and change nothing. Pass allow_new_keys=true to override."
            % (", ".join(unknown), otype, ", ".join(sorted(known))),
            "KEY_NOT_IN_FILE", type=otype, unknown=unknown, known=sorted(known))
    return ["key(s) %s are new for object type %r" % (", ".join(unknown), otype)]


def _live_object(doc, name, otype, oid):
    state = doc.resolved(name)
    rec = state.get((otype, oid))
    if rec is None:
        raise EditError("no %r object with id %r in %s (it would be a change to "
                        "something that is not there)" % (otype, oid, name),
                        "OBJECT_NOT_FOUND", type=otype, id=oid)
    if rec["deleted"]:
        raise EditError("%r %r is deleted in this log; set_fields does not resurrect "
                        "objects (add it as a new object instead)" % (otype, oid),
                        "OBJECT_DELETED", type=otype, id=oid)
    return rec


# --------------------------------------------------------------- operations
OPS = {}

def _register(name, params, summary):
    def deco(fn):
        OPS[name] = {"op": name, "params": params, "summary": summary, "fn": fn}
        return fn
    return deco


@_register("set_fields",
           {"target": {"type": "<object type>", "id": "<object id>"},
            "fields": {"<key>": "<new value>"},
            "allow_new_keys": False},
           "append a new record for an existing object with some fields changed; "
           "the fields must be keys this file actually uses for that type")
def _op_set_fields(ctx, spec):
    target = spec.get("target") or {}
    otype, oid = target.get("type"), target.get("id")
    if not otype or not oid:
        raise EditError("set_fields needs target {type, id}", "ARG_MISSING")
    fields = spec.get("fields")
    if not isinstance(fields, dict) or not fields:
        raise EditError("set_fields needs a non-empty fields object", "ARG_MISSING")
    rec = _live_object(ctx["doc"], ctx["member"], otype, oid)
    warnings = _require_keys(ctx["inventory"], otype, fields,
                             bool(spec.get("allow_new_keys")))
    payload = dict(rec["payload"] or {})
    payload.update(fields)
    return {"type": otype, "id": oid, "payload": payload,
            "before": {k: (rec["payload"] or {}).get(k) for k in fields},
            "changed_keys": sorted(fields), "warnings": warnings}


@_register("add_object",
           {"type": "<object type>", "id": "<new id, optional>",
            "fields": {"<key>": "<value>"}, "allow_new_keys": False},
           "append a brand-new object line; the id must not already be live under "
           "that type")
def _op_add_object(ctx, spec):
    otype = spec.get("type")
    if not otype:
        raise EditError("add_object needs type", "ARG_MISSING")
    fields = spec.get("fields") or {}
    if not isinstance(fields, dict):
        raise EditError("add_object fields must be an object", "ARG_MISSING")
    oid = str(spec.get("id") or _uuid.uuid4())
    state = ctx["doc"].resolved(ctx["member"])
    if (otype, oid) in state and not state[(otype, oid)]["deleted"]:
        raise EditError("%r %r already exists; use set_fields to change it"
                        % (otype, oid), "OBJECT_EXISTS", type=otype, id=oid)
    warnings = _require_keys(ctx["inventory"], otype, fields,
                             bool(spec.get("allow_new_keys")))
    return {"type": otype, "id": oid, "payload": fields,
            "changed_keys": sorted(fields), "warnings": warnings}


@_register("delete_object",
           {"type": "<object type>", "id": "<object id>"},
           "append an empty-payload record: the log's own way of saying 'gone'")
def _op_delete_object(ctx, spec):
    otype, oid = spec.get("type"), spec.get("id")
    if not otype or not oid:
        raise EditError("delete_object needs type and id", "ARG_MISSING")
    _live_object(ctx["doc"], ctx["member"], otype, oid)
    return {"type": otype, "id": oid, "payload": {}, "changed_keys": [],
            "warnings": [], "deleting": True}


def describe():
    out = []
    for name in sorted(OPS):
        out.append({"op": name, "params": OPS[name]["params"],
                    "summary": OPS[name]["summary"]})
    return out


# --------------------------------------------------------------- propose
def propose(path, operations, options=None):
    """Compute the archive that *would* be written. Nothing touches the disk."""
    options = options or {}
    if not path:
        raise EditError("path is required", "ARG_MISSING")
    if not os.path.isfile(path):
        raise EditError("archive does not exist: %r" % path, "FILE_NOT_FOUND")
    with open(path, "rb") as fh:
        raw = fh.read()
    if not _ep.looks_like_epro(raw):
        raise EditError("this is not a .epro (a ZIP container starting with 'PK')",
                        "FORMAT_NOT_SUPPORTED")
    if not operations:
        raise EditError("an empty plan is not a plan: pass at least one operation",
                        "ARG_MISSING")

    doc = _ep.EproDocument.from_bytes(raw, path)
    member = _member_of(doc, options.get("member"))
    inventory = _inventory(doc, member)
    base_ticket = _max_ticket(doc, member)
    ctx = {"doc": doc, "member": member, "inventory": inventory}

    records, plans, warnings = [], [], []
    ticket = base_ticket
    live_ids = set((k[0], k[1]) for k, v in doc.resolved(member).items()
                   if not v["deleted"])
    for idx, spec in enumerate(operations):
        if not isinstance(spec, dict):
            raise EditError("operation %d is not an object" % idx, "ARG_INVALID")
        name = spec.get("op")
        entry = OPS.get(name)
        if entry is None:
            raise EditError("unknown .epro operation %r; known: %s"
                            % (name, ", ".join(sorted(OPS))), "OP_UNKNOWN",
                            known=sorted(OPS))
        result = entry["fn"](ctx, spec)
        ticket += 1
        resolved_ticket = max(ticket, base_ticket + idx + 1)
        line = _record_line(result["type"], result["id"], resolved_ticket,
                            result["payload"])
        plans.append({"op": name, "record": result, "ticket": resolved_ticket,
                      "line": line})
        warnings.extend(result.get("warnings") or [])
        key = (result["type"], result["id"])
        if result.get("deleting"):
            live_ids.discard(key)
        else:
            live_ids.add(key)
        records.append({"index": idx, "op": name, "type": result["type"],
                        "id": result["id"], "ticket": resolved_ticket,
                        "changed_keys": result.get("changed_keys") or []})

    old_member = doc.entry_bytes(member)
    new_member = _append_lines(old_member, [p["line"] for p in plans])
    entries = collections.OrderedDict((n, doc.entry_bytes(n)) for n in doc.names())
    entries[member] = new_member
    new_bytes = _rebuild_zip(entries)

    # replay the archive we would write, so the plan is judged, not hoped
    replay = _ep.EproDocument.from_bytes(new_bytes, path)
    self_check = replay.verify()
    post = _verify_ops(replay, member, plans)

    blocking = []
    if not self_check.get("byte_identity"):
        blocking.append({"gate": "log_not_replayable",
                         "message": "the archive this plan would write does not "
                                    "re-serialise byte-identically from its own lines"})
    if not post["all_verified"]:
        blocking.append({"gate": "postcondition_unreachable",
                         "message": "replaying the planned append does not produce the "
                                    "planned state: %s" % post["failed"]})
    if not new_member.startswith(old_member):
        blocking.append({"gate": "not_append_only",
                         "message": "the planned member does not start with the old "
                                    "bytes; an append-only log must not be rewritten"})

    plan_id = "epro-plan-" + _uuid.uuid4().hex[:12]
    plan = {
        "format": "epro",
        "plan_id": plan_id, "path": os.path.abspath(path),
        "created": time.time(), "expires": time.time() + _plan.PLAN_TTL,
        "sha256_before": _sha256(raw), "bytes_before": len(raw),
        "member": member,
        "operations": operations, "records": records,
        "new_bytes": new_bytes,
        "new_member_sha256": _sha256(new_member),
        "appended_lines": [p["line"] for p in plans],
        "ticket_base": base_ticket,
        "options": {"member": member, "mode": options.get("mode") or "in_place",
                    "out_path": options.get("out_path")},
    }
    _plan.PLANS[plan_id] = plan
    _plan._trim_plans()

    return {
        "status": "ok",
        "format": "epro",
        "plan_id": plan_id,
        "path": plan["path"],
        "nothing_written": "a proposal only computes; the .epro on disk is untouched",
        "member": member,
        "operations": records,
        "appended_lines": plan["appended_lines"],
        "ticket": {"from": base_ticket, "to": records[-1]["ticket"] if records else base_ticket,
                   "rule": "each new line takes a ticket above every ticket already in "
                           "the member; a smaller one would be ignored in silence"},
        "key_evidence": {t: {"objects": v["objects"],
                             "keys": sorted((v.get("keys") or {}).keys())}
                         for t, v in inventory.items()},
        "self_check": {"log_replayable": self_check.get("byte_identity"),
                       "postconditions": post},
        "blocking": blocking,
        "warnings": warnings,
        "unverified_claims": {"unit_verified": False, "note": UNIT_NOTE},
        "expires_in_seconds": _plan.PLAN_TTL,
        "next": ("board_apply(plan_id=%r, confirm=true) - checkpoint, atomic write, "
                 "then four gates on the file that lands on disk" % plan_id),
    }


def _rebuild_zip(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, blob in entries.items():
            zf.writestr(name, blob)
    return buf.getvalue()


# --------------------------------------------------------------- verify
def _verify_ops(doc, member, plans):
    """Read the promise back out of the resolved state."""
    state = doc.resolved(member)
    out = []
    for item in plans:
        rec = item["record"]
        key = (rec["type"], rec["id"])
        got = state.get(key)
        ok = got is not None
        detail = {}
        if ok and rec.get("deleting"):
            ok = bool(got["deleted"])
            detail["deleted"] = got["deleted"]
        elif ok:
            payload = got["payload"] or {}
            missing = {k: v for k, v in rec["payload"].items()
                       if payload.get(k) != v}
            ok = not got["deleted"] and not missing
            detail["ticket"] = got["ticket"]
            detail["mismatch"] = missing
        out.append({"op": item["op"], "type": rec["type"], "id": rec["id"],
                    "ticket": item["ticket"], "verified": bool(ok),
                    "detail": detail})
    failed = [o for o in out if not o["verified"]]
    return {"all_verified": not failed, "operations": out,
            "failed": ", ".join("%s %s/%s" % (o["op"], o["type"], o["id"])
                                for o in failed)}


# --------------------------------------------------------------- apply
def apply_plan(plan_id, confirm=False, options=None):
    """Write the proposed archive. The only function here that writes."""
    options = options or {}
    plan = _plan.PLANS.get(plan_id)
    if plan is None:
        raise EditError("unknown or already-used plan %r (a plan is single-use; "
                        "propose again)" % plan_id, "PLAN_NOT_FOUND")
    if plan.get("format") != "epro":
        raise EditError("plan %r is not an .epro plan" % plan_id, "PLAN_KIND_MISMATCH")
    if not confirm:
        raise EditError("writing an archive is not reversible by Ctrl+Z: call again "
                        "with confirm=true. The plan affects %s in %s"
                        % (plan["member"], os.path.basename(plan["path"])),
                        "CONFIRMATION_REQUIRED", plan_id=plan_id)
    if time.time() > plan["expires"]:
        _plan.PLANS.pop(plan_id, None)
        raise EditError("plan %r expired" % plan_id, "PLAN_EXPIRED")

    path = plan["path"]
    mode = options.get("mode") or plan["options"].get("mode") or "in_place"
    out_path = options.get("out_path") or plan["options"].get("out_path")
    if mode == "new_file" and not out_path:
        raise EditError("mode=new_file needs out_path", "ARG_MISSING")

    with open(path, "rb") as fh:
        current = fh.read()
    if _sha256(current) != plan["sha256_before"]:
        raise EditError("the archive changed on disk after the plan was made. Nothing "
                        "was written - propose again against the current file",
                        "BOARD_CHANGED_SINCE_PLAN")
    target = path if mode == "in_place" else os.path.abspath(out_path)
    if mode == "new_file" and os.path.exists(target):
        raise EditError("refusing to overwrite an existing file: %s" % target,
                        "TARGET_EXISTS")

    checkpoint = _plan.save_checkpoint(path, current,
                                       "before epro apply %s (%s)"
                                       % (plan_id, plan["member"]), plan_id=plan_id)
    before_doc = _ep.EproDocument.from_bytes(current, path)
    gates = {}
    written = False
    try:
        _atomic_write(target, plan["new_bytes"])
        written = True

        with open(target, "rb") as fh:
            back = fh.read()
        gates["bytes_written"] = {
            "ok": back == plan["new_bytes"],
            "sha256": _sha256(back), "expected_sha256": _sha256(plan["new_bytes"]),
            "bytes": len(back)}

        after_doc = _ep.EproDocument.from_bytes(back, target)
        member = plan["member"]
        old_member = before_doc.entry_bytes(member)
        new_member = after_doc.entry_bytes(member)
        suffix = new_member[len(old_member):]
        parsed = 0
        bad_lines = []
        for line in suffix.decode("utf-8", "surrogateescape").splitlines():
            if not line.strip():
                continue
            head, payload = _ep._split(line)
            if head is None:
                bad_lines.append(line[:120])
            else:
                parsed += 1
        gates["append_only"] = {
            "ok": bool(new_member.startswith(old_member)) and not bad_lines
                  and parsed == len(plan["appended_lines"]),
            "starts_with_old_bytes": bool(new_member.startswith(old_member)),
            "appended_bytes": len(new_member) - len(old_member),
            "records_parsed": parsed,
            "records_planned": len(plan["appended_lines"]),
            "bad_lines": bad_lines[:5]}

        same, diff = {}, []
        for name in before_doc.names():
            a = before_doc.entry_bytes(name)
            b = after_doc.entry_bytes(name) if name in after_doc.names() else None
            if name == member:
                continue
            same[name] = (a == b)
            if a != b:
                diff.append(name)
        missing = sorted(set(after_doc.names()) - set(before_doc.names()))
        extra = sorted(set(before_doc.names()) - set(after_doc.names()))
        gates["members_untouched"] = {
            "ok": not diff and not missing and not extra,
            "members": len(same), "changed": diff,
            "added": missing, "removed": extra}

        post = _verify_ops(after_doc, member, [
            {"op": r["op"], "record": p["record"], "ticket": r["ticket"]}
            for r, p in zip(plan["records"], _replan(plan))])
        gates["postconditions"] = dict(post)
        gates["postconditions"]["ok"] = bool(post.get("all_verified"))

        replay = after_doc.verify()
        gates["log_idempotent"] = {
            "ok": bool(replay.get("byte_identity")),
            "note": "every text member re-serialised from its own parsed lines is "
                    "byte-identical - the appended log is a log, not a rewrite",
            "members": replay.get("members")}

        gates["unit"] = {
            "ok": None,
            "unit_verified": False,
            "note": UNIT_NOTE + "; nothing in this write depended on a length unit, "
                    "no coordinate was converted"}

        blocking = []
        for name in ("bytes_written", "append_only", "members_untouched",
                     "postconditions", "log_idempotent"):
            if not gates[name]["ok"]:
                blocking.append("%s failed" % name)
        if blocking:
            raise EditError("apply blocked and rolled back: " + ", ".join(blocking),
                            "GATE_BLOCKED", gates=gates, blocking=blocking)

        _plan.PLANS.pop(plan_id, None)
        return {
            "status": "ok",
            "format": "epro",
            "plan_id": plan_id,
            "written": target,
            "mode": mode,
            "member": member,
            "sha256_after": gates["bytes_written"]["sha256"],
            "checkpoint": checkpoint,
            "gates": gates,
            "gates_passed": sorted(k for k, v in gates.items()
                                   if isinstance(v, dict) and v.get("ok") is True),
            "unit_verified": False,
            "decision": ("every gate passed: the archive on disk is the planned "
                         "archive, the target log only grew, every other member is "
                         "byte-identical, and each operation's promise was re-read "
                         "from the resolved state"),
            "how_to_undo": "board_restore(checkpoint_id=%r, confirm=true)"
                           % checkpoint["checkpoint_id"],
        }
    except EditError:
        if written:
            _atomic_write(target, current)
        raise
    except Exception as exc:                        # noqa: BLE001
        if written:
            _atomic_write(target, current)
        raise EditError("%s: %s (the checkpoint was restored to disk)"
                        % (type(exc).__name__, exc), "APPLY_FAILED")


def _replan(plan):
    """Rebuild the planned records from the stored lines for verification."""
    out = []
    for idx, line in enumerate(plan["appended_lines"]):
        head, payload = _ep._split(line)
        rec = {"op": plan["records"][idx]["op"], "type": head["type"],
               "id": head["id"], "payload": payload,
               "deleting": not payload}
        out.append({"record": rec})
    return out
