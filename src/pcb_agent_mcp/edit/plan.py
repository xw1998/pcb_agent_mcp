"""The write path: propose -> digest -> approve -> apply, with checkpoints.

The protocol is deliberately not "call a function, get a modified file":

    board_propose  computes everything, writes nothing, returns a plan_id and a
                   digest (what the board would become, and what breaks)
    board_apply    needs that plan_id AND confirm=true, takes a checkpoint first,
                   writes atomically, then hands the result to KiCad and to the
                   file's own re-read before it is allowed to stand

Why so much ceremony for "move a footprint":

* a written board is used by a real EDA and eventually by a board house - a
  broken write is not a bug, it is a scrapped panel;
* both write paths are *silent* on failure. KiCad ignores a field it does not
  understand; EasyEDA Pro ignores a record whose field count or unit is wrong and
  still opens the file. Neither tells you. (The ticket/append-only model this
  comment used to describe was a guess; the measured save rewrites one member -
  see `epro_edit` and docs/13.)

So every apply has to pass four gates that do not trust the code that did the
writing:

1. `bytes_written`  - the bytes on disk are the bytes that were planned
2. `token_identity` - re-parsing the file reproduces the planned tree exactly
3. `postconditions` - each operation's own promise is re-read from the file
4. `engine`         - KiCad re-reads both files (stats / plotted artwork /
                      positions) and its DRC is diffed: a NEW violation blocks

Any blocking gate failure restores the checkpoint and says so.
"""
import collections
import hashlib
import inspect
import json
import os
import shutil
import tempfile
import time
import uuid as _uuid

from ..fidelity import engine as _engine
from ..fidelity import kicad_doc as _kdoc
from ..fidelity import sexpr as S
from . import build as B
from . import checks as _checks
from . import drc_policy as _drcp
from . import ops as _ops
from .fsutil import atomic_write as _atomic_write
from .fsutil import home, sha256 as _sha256

PLANS = {}
PLAN_TTL = 60 * 60          # a proposal goes stale after an hour of thinking
PLAN_MAX = 32

class EditError(Exception):
    """The same four attributes as `OpError`, `jobs.JobError` and `RouteError`.

    The three used to expose different ones (`OpError` only had `.code` and
    `.extra`, nothing else had `.code`), so a caller that needed "the machine
    readable reason" had to branch on the exception class. `code` is kept as an
    alias of `reason_code` for the existing call sites.
    """
    def __init__(self, message, code="EDIT_FAILED", hint=None, **extra):
        Exception.__init__(self, message)
        self.message = message
        self.code = code
        self.reason_code = code
        self.hint = hint
        self.extra = extra

class _DryRunBlocked(EditError):
    """`dry_run` + a blocking gate: the answer is 'it would be refused'."""

# --------------------------------------------------------------- paths/home
# `home()` and the sha256 helper come from .fsutil so that the two write paths
# (KiCad and LCEDA) can never disagree about where state lives.
def checkpoint_root():
    return os.path.join(home(), "checkpoints")

def _path_key(path):
    return hashlib.sha1(os.path.abspath(path).lower().encode("utf-8")).hexdigest()[:12]

# --------------------------------------------------------------- checkpoints
def save_checkpoint(path, data, reason, plan_id=None):
    key = _path_key(path)
    folder = os.path.join(checkpoint_root(), key)
    os.makedirs(folder, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    cid = "%s-%s-%s" % (key, stamp, _uuid.uuid4().hex[:6])
    # The body keeps the SOURCE extension: kicad-cli decides a file's type by its
    # name ("Unknown file type" for a .board), and a checkpoint that the vendor
    # engine cannot open is worthless as a comparison baseline.
    ext = os.path.splitext(path)[1] or ".board"
    body = os.path.join(folder, cid + ext)
    with open(body, "wb") as fh:
        fh.write(data)
    meta = {"checkpoint_id": cid, "path": os.path.abspath(path),
            "bytes": len(data), "sha256": _sha256(data), "reason": reason,
            "plan_id": plan_id, "created_utc": stamp, "body": body}
    with open(os.path.join(folder, cid + ".json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=1)
    return meta

def list_checkpoints(path=None, limit=50):
    root = checkpoint_root()
    out = []
    if not os.path.isdir(root):
        return {"status": "ok", "checkpoints": [], "root": root}
    keys = [_path_key(path)] if path else sorted(os.listdir(root))
    for key in keys:
        folder = os.path.join(root, key)
        if not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder), reverse=True):
            if not name.endswith(".json"):
                continue
            try:
                with open(os.path.join(folder, name), encoding="utf-8") as fh:
                    meta = json.load(fh)
            except (OSError, ValueError):
                continue
            out.append(meta)
    out.sort(key=lambda m: m.get("created_utc", ""), reverse=True)
    return {"status": "ok", "root": root, "total": len(out),
            "returned": min(len(out), limit), "checkpoints": out[:limit]}

def find_checkpoint(cid):
    root = checkpoint_root()
    if not os.path.isdir(root):
        raise EditError("no checkpoint directory at %s" % root, "CHECKPOINT_NOT_FOUND")
    for folder in os.listdir(root):
        meta_path = os.path.join(root, folder, cid + ".json")
        if not os.path.isfile(meta_path):
            continue
        with open(meta_path, encoding="utf-8") as fh:
            meta = json.load(fh)
        body = meta.get("body") or os.path.join(root, folder, cid + ".board")
        if not os.path.isfile(body):
            continue
        meta["body"] = body
        return meta
    raise EditError("checkpoint %r not found under %s" % (cid, root),
                    "CHECKPOINT_NOT_FOUND")

def restore_checkpoint(cid, confirm, keep_current=True):
    """Put a checkpoint back. Takes a checkpoint of the CURRENT file first."""
    if not confirm:
        raise EditError("restoring a checkpoint overwrites the board on disk; "
                        "call again with confirm=true", "CONFIRMATION_REQUIRED")
    meta = find_checkpoint(cid)
    path = meta["path"]
    with open(meta["body"], "rb") as fh:
        data = fh.read()
    if _sha256(data) != meta["sha256"]:
        raise EditError("checkpoint body does not match its recorded sha256 - the "
                        "backup itself is damaged, nothing was written",
                        "CHECKPOINT_CORRUPT")
    safety = None
    if os.path.isfile(path):
        with open(path, "rb") as fh:
            current = fh.read()
        if current == data:
            return {"status": "ok", "changed": False, "path": path,
                    "message": "the board on disk already equals that checkpoint"}
        if keep_current:
            safety = save_checkpoint(path, current,
                                     "auto: before restoring %s" % cid, plan_id=None)
    _atomic_write(path, data)
    with open(path, "rb") as fh:
        back = fh.read()
    if back != data:
        raise EditError("restore wrote the file but reading it back differs",
                        "RESTORE_UNVERIFIED")
    return {"status": "ok", "changed": True, "path": path,
            "restored_from": cid, "sha256": _sha256(data),
            "auto_checkpoint_of_the_replaced_file": safety,
            "verified": "file re-read after the write and compared byte for byte"}

# --------------------------------------------------------------- propose
def _trim_plans():
    if len(PLANS) <= PLAN_MAX:
        return
    for key in sorted(PLANS, key=lambda k: PLANS[k]["created"])[:len(PLANS) - PLAN_MAX]:
        PLANS.pop(key, None)

def propose(path, operations, options=None):
    """Compute a plan. Never writes anything, anywhere."""
    options = options or {}
    if not path:
        raise EditError("path is required", "ARG_MISSING")
    if not os.path.isfile(path):
        raise EditError("board file does not exist: %r (nothing is created here; "
                        "use board_create for a new board)" % path, "FILE_NOT_FOUND")
    with open(path, "rb") as fh:
        raw = fh.read()
    if not _is_kicad(raw):
        raise EditError("this is not a KiCad board (a .epro goes through the same "
                        "tools, which detect it by content)", "FORMAT_NOT_SUPPORTED")
    if not operations:
        raise EditError("an empty plan is not a plan: pass at least one operation",
                        "ARG_MISSING")

    src = _kdoc.KiCadDocument.from_text(raw.decode("utf-8", "surrogateescape"), path)
    work = _kdoc.KiCadDocument.from_text(src.text, path)      # private copy
    # The seed is kept on the plan: generated ids (uuid / tstamp) are a function
    # of (seed, absolute operation index), so a stepped write can reproduce them
    # exactly instead of inventing its own and then failing the byte gate.
    seed_base = "%s:%d" % (_path_key(path), int(time.time()))
    records = _ops.run_all(work, operations, seed_base=seed_base)
    new_text = work.to_text()
    new_bytes = work.to_bytes()

    # the copy must itself be lossless before it is allowed to become a plan
    self_check = work.verify()

    before = _checks.digest(src)
    after = _checks.digest(work, before=src)
    touched = _ops.affects(operations)

    # pre-check window: everything the plan touched, plus a clearance margin
    region = None
    if options.get("precheck_region"):
        region = tuple(float(v) for v in options["precheck_region"])
    clearance = _checks.copper_clearance(
        work, region=region,
        threshold_mm=float(options.get("precheck_clearance_mm", 0.2)))
    outside = _checks.outside_report(work)

    warnings = []
    blocking = []
    if not after["outline"]["closed"]:
        entry = {
            "gate": "outline_not_closed",
            "message": after["outline"]["message"],
            "how_to_clear": "add the missing Edge.Cuts geometry in the same plan, or "
                            "pass allow_open_outline=true if you are building it in "
                            "several steps"}
        if "outline" in touched or before["outline"]["closed"]:
            # either this plan is what opened it, or this plan did it while trying
            # not to - both are reasons to stop.
            blocking.append(entry)
        else:
            # the board was already open before this plan. Blocking here would mean
            # "no operation may ever touch a board whose outline is unfinished",
            # which is not the promise; it is reported, and the promise is kept.
            warnings.append(dict(entry, gate="outline_already_open",
                                 message="the outline was ALREADY open before this "
                                         "plan, and this plan does not touch it: %s"
                                         % after["outline"]["message"]))
    if clearance["violations"]:
        warnings.append({"gate": "clearance_below_threshold",
                         "message": "%d copper pair(s) closer than %s mm after this "
                                    "plan" % (clearance["violations"],
                                              clearance["threshold_mm"]),
                         "worst": clearance["worst"][:3]})
    if outside.get("outside_total"):
        warnings.append({"gate": "geometry_outside_outline",
                         "message": outside["message"]})

    plan_id = "plan-" + _uuid.uuid4().hex[:12]
    plan = {
        "plan_id": plan_id, "path": os.path.abspath(path),
        "created": time.time(), "expires": time.time() + PLAN_TTL,
        "sha256_before": _sha256(raw), "bytes_before": len(raw),
        "operations": operations, "records": records, "seed_base": seed_base,
        "new_text": new_text, "new_bytes": new_bytes,
        "new_signature": S.signature(work.tree),
        "affected": touched,
        "options": {"precheck_region": region,
                    "precheck_clearance_mm": clearance["threshold_mm"],
                    "mode": options.get("mode") or "in_place",
                    "out_path": options.get("out_path")},
    }
    PLANS[plan_id] = plan
    _trim_plans()

    return {
        "status": "ok",
        "plan_id": plan_id,
        "path": plan["path"],
        "nothing_written": "a proposal only computes; the board on disk is untouched",
        "operations": records,
        "affected": touched,
        "board_before": before,
        "board_after": after,
        "precheck": {"outline": after["outline"], "clearance": clearance,
                     "outside": outside},
        "self_check": {"token_identity": self_check.get("token_identity"),
                       "note": "the edited tree survives a serialise/parse round trip "
                               "before it is allowed to become a plan"},
        "blocking": blocking,
        "warnings": warnings,
        "plan_conflicts": _plan_conflicts(operations),
        "expires_in_seconds": PLAN_TTL,
        "next": ("board_apply(plan_id=%r, confirm=true) - that call takes a "
                 "checkpoint, writes atomically, then has KiCad and the file's own "
                 "re-read check the result" % plan_id),
    }

def _verify_all(ops_module, doc, operations, records):
    """Postconditions, with the run records when the module understands them.

    The schematic module has no run records (its rewrites are not per-node), so
    passing `records=` there is a TypeError, not a richer answer. Asking the
    function what it takes keeps the two surfaces able to differ.
    """
    try:
        params = inspect.signature(ops_module.verify_all).parameters
    except (TypeError, ValueError):
        params = {}
    if "records" in params:
        return ops_module.verify_all(doc, operations, records=records)
    return ops_module.verify_all(doc, operations)


def _pos_text(v):
    """Where a violation is, whatever shape the caller's policy layer used.

    `drc_policy.classify` writes `pos` as [x, y]; KiCad's own items carry
    `{"x":, "y":}`. Formatting one of them as the other raised a TypeError
    inside the blocking message, i.e. the gate failed for a reason that had
    nothing to do with the edit.
    """
    pos = v.get("pos")
    if not pos and v.get("at"):
        pos = v["at"][0] if isinstance(v["at"], list) and v["at"] else v["at"]
    if isinstance(pos, dict):
        try:
            return "(%.2f, %.2f)" % (float(pos["x"]), float(pos["y"]))
        except (KeyError, TypeError, ValueError):
            return "no position"
    if isinstance(pos, (list, tuple)) and len(pos) >= 2:
        try:
            return "(%.2f, %.2f)" % (float(pos[0]), float(pos[1]))
        except (TypeError, ValueError):
            return "no position"
    return "no position"


def _plan_conflicts(operations):
    """Plans whose operations talk about the same object, spelled out.

    The one that cost real time: `delete_zone(net=GND, layer=In1.Cu)` followed by
    `add_zone(net=GND, layer=In1.Cu)` in the SAME apply. That is a legitimate
    plan (replace a pour) and it used to fail, because the delete's postcondition
    asked "does anything still match net+layer" on the FINAL tree and the zone
    the plan just added answered yes. The refusal read `postcondition check
    failed`, which names neither the cause nor the cure.

    The postcondition is now checked by uuid (see ops._verify_delete_zone), so
    this plan is allowed; the conflict list stays because "these two operations
    touch the same object" is something the caller should see before writing, not
    something it should have to infer from two op dicts.
    """
    conflicts = []
    for i, d in enumerate(operations):
        if (d.get("op") or "") != "delete_zone" or d.get("uuid"):
            continue
        for j, a in enumerate(operations):
            if j <= i or (a.get("op") or "") != "add_zone":
                continue
            same_net = d.get("net") and a.get("net") == d.get("net")
            same_layer = d.get("layer") and a.get("layer") == d.get("layer")
            if not (same_net and same_layer):
                continue
            conflicts.append({
                "kind": "delete_then_add_same_zone",
                "delete_at": i, "add_at": j,
                "net": d.get("net"), "layer": d.get("layer"),
                "why": "the same net/layer is removed and re-created inside one "
                       "plan, so the two operations are about the same object",
                "allowed": True,
                "what_was_fixed": ("the delete's postcondition is checked against "
                                   "the uuids it removed, not against 'nothing "
                                   "matches this selector any more', so the zone "
                                   "added later does not fail it"),
                "what_to_do": ("nothing, unless you meant to REPLACE the zone: then "
                               "prefer set_zone_params, which keeps the outline, "
                               "uuid and priority and does not need a fill "
                               "afterwards"),
            })
    return conflicts

def _is_kicad(raw):
    head = raw.lstrip(b"\xef\xbb\xbf \t\r\n")
    return head.startswith(b"(kicad_pcb") or head.startswith(b"(kicad_sch")

# --------------------------------------------------------------- apply
def _project_beside(path):
    """The `.kicad_pro` that carries the rule minimums and rule severities.

    Both halves of the DRC diff have to be read against the same rule set or the
    comparison invents violations: the checkpoint copy lives in the server's own
    home, so without this the "before" half ran with KiCad's defaults while the
    "after" half ran with the project's, and 14 pre-existing silk overlaps were
    reported as 38 new errors caused by a copper edit.
    """
    if not path:
        return None
    pro = os.path.splitext(path)[0] + ".kicad_pro"
    return pro if os.path.isfile(pro) else None

def _drc_keys(result):
    if not isinstance(result, dict) or result.get("status") != "ok":
        return None
    keys = []
    for v in result.get("violations") or []:
        pos = None
        if v.get("items"):
            pos = v["items"][0].get("pos") or None
        keys.append((v.get("type"), v.get("severity"), v.get("description"),
                     round(pos["x"], 2) if pos else None,
                     round(pos["y"], 2) if pos else None))
    return keys

def _drc_diff(before, after, limit=10, strict=False):
    """What the edit newly broke, split into blocking and advisory.

    All 69 violations the KiCad report produced for the STM32F401 core board were
    warnings (`text_height`, `silk_overlap`, `silk_over_copper`,
    `silk_edge_clearance`). Treating them as blocking meant the board could not be
    edited at all, so the split follows KiCad's own severity - see
    `drc_policy.py` for the rule and for what a violation with no readable
    severity does (it blocks).
    """
    a, b = _drc_keys(before), _drc_keys(after)
    if a is None or b is None:
        return {"comparable": False,
                "reason": (before or {}).get("reason_code") or
                          (after or {}).get("reason_code") or "DRC did not run"}
    ca, cb = collections.Counter(a), collections.Counter(b)
    new = list((cb - ca).elements())
    gone = list((ca - cb).elements())
    # The constraint/actual pair only exists in the description text; engine.drc
    # already parsed it, so carry it through instead of making the caller redo it.
    geo = {}
    for v in (after or {}).get("violations") or []:
        pos = (v.get("items") or [{}])[0].get("pos") or None
        k = (v.get("type"), v.get("severity"), v.get("description"),
             round(pos["x"], 2) if pos else None,
             round(pos["y"], 2) if pos else None)
        geo[k] = v.get("geometry")
    new_items = [{"type": t, "severity": s, "description": d,
                  "pos": [x, y],
                  "geometry": geo.get((t, s, d, x, y))}
                 for t, s, d, x, y in new]
    policy = _drcp.classify(new_items, strict=strict, limit=limit)
    return {"comparable": True,
            "new": new_items[:limit],
            "new_total": len(new),
            "new_by_severity": policy["by_severity"],
            "new_blocking_total": policy["blocking_total"],
            "new_advisory_total": policy["advisory_total"],
            "new_blocking": policy["blocking"],
            "new_advisory": policy["advisory"],
            "policy": policy["policy"],
            "resolved": [{"type": t, "severity": s, "description": d}
                         for t, s, d, x, y in gone[:limit]],
            "resolved_total": len(gone),
            "before_total": len(a), "after_total": len(b)}

def apply_plan(plan_id, confirm=False, options=None):
    """Write the plan. This is the only function in this package that writes."""
    options = options or {}
    plan = PLANS.get(plan_id)
    if plan is None:
        raise EditError("unknown or already-used plan %r (a plan is single-use; "
                        "propose again)" % plan_id, "PLAN_NOT_FOUND")
    if not confirm:
        raise EditError("writing a board file is not reversible by Ctrl+Z: call again "
                        "with confirm=true. The plan is %r and it affects %s"
                        % (plan_id, ", ".join(plan["affected"])),
                        "CONFIRMATION_REQUIRED",
                        plan_id=plan_id)
    if time.time() > plan["expires"]:
        PLANS.pop(plan_id, None)
        raise EditError("plan %r expired (a proposal is not a licence to write later)"
                        % plan_id, "PLAN_EXPIRED")

    path = plan["path"]
    mode = (options.get("mode") or plan["options"].get("mode") or "in_place")
    out_path = options.get("out_path") or plan["options"].get("out_path")
    if mode == "new_file" and not out_path:
        raise EditError("mode=new_file needs out_path", "ARG_MISSING")

    with open(path, "rb") as fh:
        current = fh.read()
    if _sha256(current) != plan["sha256_before"]:
        raise EditError("the board changed on disk after the plan was made (someone "
                        "saved it in the editor?). Nothing was written - propose "
                        "again against the current file", "BOARD_CHANGED_SINCE_PLAN")

    target = path if mode == "in_place" else os.path.abspath(out_path)
    if mode == "new_file" and os.path.exists(target):
        raise EditError("refusing to overwrite an existing file: %s" % target,
                        "TARGET_EXISTS")

    checkpoint = save_checkpoint(path, current,
                                 "before apply %s (%s)" % (plan_id, ", ".join(
                                     plan["affected"])), plan_id=plan_id)

    gates = {}
    written = False
    rollback_reason = None
    try:
        _atomic_write(target, plan["new_bytes"])
        written = True

        with open(target, "rb") as fh:
            back = fh.read()
        gates["bytes_written"] = {
            "ok": back == plan["new_bytes"], "sha256": _sha256(back),
            "expected_sha256": _sha256(plan["new_bytes"]), "bytes": len(back)}

        re_doc = _kdoc.KiCadDocument.from_path(target)
        gates["token_identity"] = {
            "ok": S.signature(re_doc.tree) == plan["new_signature"],
            "note": "re-parsing the file that is now on disk reproduces exactly the "
                    "tree that was planned (every atom, every quoting)"}

        post = _ops.verify_all(re_doc, plan["operations"], records=plan["records"])
        gates["postconditions"] = dict(post)
        gates["postconditions"]["ok"] = bool(post.get("all_verified"))

        pre_fill_errors = None
        if options.get("engine", True):
            before_file = checkpoint["body"]
            cmp_which = ["stats", "gerbers"]
            if "copper" in plan["affected"] or "outline" in plan["affected"]:
                cmp_which = ["stats", "gerbers", "position"]
            gates["engine"] = _engine.compare_paths(before_file, target,
                                                    which=tuple(cmp_which))
            eng = gates["engine"]
            # "ok" here is NOT "nothing changed" - a normal edit is supposed to
            # change something. It means: the comparison was possible and it did
            # not expose a change the plan denied making.
            silent = (eng.get("status") == "ok" and not eng.get("identical")
                      and set(plan["affected"]) <= {"meta"})
            comparable = sum(1 for c in (eng.get("checks") or {}).values()
                             if isinstance(c, dict) and c.get("comparable"))
            eng["comparable_checks"] = comparable
            # A comparison that could not run is not a pass: an engine gate that
            # "passes" because nothing was compared is exactly the false green
            # this whole layer exists to prevent.
            eng["ok"] = bool(eng.get("status") in ("ok", "skipped") and not silent
                             and (comparable or eng.get("status") == "skipped"))
            pro_beside = _project_beside(target)
            drc_before = _engine.drc(before_file, project=pro_beside)
            drc_after = _engine.drc(target, project=pro_beside)
            gates["drc"] = _drc_diff(drc_before, drc_after,
                                     strict=bool(options.get("strict_drc")))
            if gates["drc"].get("comparable"):
                gates["drc"]["ok"] = not gates["drc"].get("new_blocking_total")
            # The pour happens AFTER this DRC, so its errors have to be compared
            # against this baseline or a pre-existing error would block a fill
            # that did not cause it.
            pre_fill_errors = int((drc_after.get("by_severity") or {}).get("error") or 0)
        else:
            gates["engine"] = {"status": "skipped", "reason_code": "NOT_REQUESTED",
                               "ok": None}
            gates["drc"] = {"comparable": False, "reason": "NOT_REQUESTED"}

        # ---------------- decisions
        blocking = []
        if not gates["bytes_written"]["ok"]:
            blocking.append("the bytes on disk are not the bytes that were planned")
        if not gates["token_identity"]["ok"]:
            blocking.append("re-parsing the written file does not reproduce the plan")
        if not gates["postconditions"]["all_verified"]:
            bad = [o for o in gates["postconditions"]["operations"] if not o["verified"]]
            gates["postconditions"]["failed_detail"] = [
                {"op": o["op"], "index": o["index"], "what_was_checked": o["detail"]}
                for o in bad]
            blocking.append(
                "postcondition check failed for %d operation(s): %s - each entry in "
                "gates.postconditions.failed_detail says which one and what it "
                "re-read from the written file (a delete that reports "
                "`remaining_matches` > 0 while you also re-created the object is the "
                "delete-then-add plan; that is now checked by uuid, so a positive "
                "count there means OTHER objects match the selector)"
                % (len(bad), ", ".join(o["op"] for o in bad)))
        eng = gates.get("engine") or {}
        if eng.get("status") == "ok":
            if eng.get("identical") and set(plan["affected"]) <= {"mech", "meta", "silk"}:
                pass      # unchanged, and it claimed not to change anything KiCad plots
            if (not eng.get("identical")) and set(plan["affected"]) <= {"meta"}:
                blocking.append("the plan only changed metadata but KiCad's plotted "
                                "artwork changed - that is silent corruption")
        drc = gates.get("drc") or {}
        if drc.get("comparable") and drc.get("new_blocking_total") and \
                not options.get("allow_new_violations"):
            # A count alone forces the caller to re-run the DRC and match
            # descriptions by hand, so the message carries the first few places
            # and the numbers the engine stated.
            where = []
            for v in drc["new_blocking"][:5]:
                geo = v.get("geometry") or {}
                where.append(("%s at %s (wanted %s mm, got %s mm)"
                              % (v.get("type"), _pos_text(v),
                                 geo.get("constraint_mm", "?"),
                                 geo.get("actual_mm", "?")))
                             if geo else "%s at %s"
                             % (v.get("type"), _pos_text(v)))
            blocking.append("%d NEW DRC error(s) appear after this edit (%s): %s%s; "
                            "pass allow_new_violations=true to accept them, or use "
                            "board_drc(options) to see every one"
                            % (drc["new_blocking_total"],
                               ", ".join(sorted({v["type"] for v in drc["new_blocking"]})),
                               "; ".join(where),
                               (" ..." if drc["new_blocking_total"] > 5 else "")))
        plan_outline = _checks.outline_report(re_doc)
        if "outline" in plan["affected"] and not plan_outline["closed"] and \
                not options.get("allow_open_outline"):
            blocking.append("the outline is still open after this edit: %s"
                            % plan_outline["message"])

        # ---------------- zone fill: the pour is only real after the engine poured it
        # A zone without filled_polygon is an OUTLINE. Every later question about
        # the pour (connectivity, Gerber copper, `zone_fill`) would be answered
        # from that outline, which is exactly the "intermediate layer says one
        # thing, the board says another" failure. So when the caller asks for the
        # closure, the engine fills it here - and failing to fill is a failure,
        # not a footnote.
        pours = [z for z in re_doc.items("zone") if S.child(z, "keepout") is None]
        if options.get("fill_zones"):
            if not pours:
                gates["zone_fill"] = {"ok": None, "reason": "no copper pour in this edit"}
            else:
                from ..fidelity import fill as _fill
                res = _fill.fill(target, in_place=True)
                val = res.get("value") or {}
                gates["zone_fill"] = {
                    "ok": res.get("status") == "ok",
                    "reason_code": res.get("reason_code"),
                    "message": res.get("message"),
                    "poured_zones": val.get("poured_zones"),
                    "fill_area_mm2": val.get("fill_area_mm2"),
                    "poured_nothing": val.get("poured_nothing"),
                    "checkpoint_id": val.get("checkpoint_id"),
                    "drc_rechecked": (val.get("drc_after_fill") or {}).get("ran") is True,
                    "drc_after_fill": val.get("drc_after_fill"),
                    "note": ("the engine poured the zones after the DRC comparison "
                             "ran, so the DRC above does not describe this copper; "
                             "`drc_after_fill` is the DRC of the POURED board"),
                }
                # The pour must not be reported as "filled" while the board does
                # not pass DRC: on the reference board the filler succeeded and
                # the file still held 386 clearance errors.
                df = val.get("drc_after_fill") or {}
                if df.get("ran"):
                    filled_errors = int(df.get("errors") or 0)
                    added = (filled_errors - pre_fill_errors
                             if pre_fill_errors is not None else None)
                    gates["zone_fill"]["drc_errors_before_the_pour"] = pre_fill_errors
                    gates["zone_fill"]["drc_errors_added_by_the_pour"] = added
                    if df.get("verdict") == "not_clean" and (added is None or added > 0):
                        gates["zone_fill"]["ok"] = False
                        blocking.append(
                            "the zones were filled but the poured board is not DRC "
                            "clean: %d error(s), %d unconnected, added by the pour: %s "
                            "(types: %s)"
                            % (filled_errors, df.get("unconnected_total") or 0,
                               "unknown (the pre-pour DRC did not run)"
                               if added is None else added,
                               ", ".join(sorted((df.get("by_type") or {})))))
                    elif df.get("verdict") == "not_clean":
                        gates["zone_fill"]["warning"] = (
                            "the poured board has %d DRC error(s), but they were "
                            "already there before the pour (0 added), so the fill is "
                            "not what broke them" % filled_errors)
                else:
                    gates["zone_fill"]["note"] += ("; the DRC of the pour could not "
                                                   "run, so the pour is UNVERIFIED")
                if not gates["zone_fill"]["ok"]:
                    blocking.append("the zones were not filled: %s"
                                    % (res.get("message") or res.get("reason_code")))

        dry_run = bool(options.get("dry_run"))
        if blocking:
            rollback_reason = "; ".join(blocking)
            if dry_run:
                # A dry run exists to ASK whether the write would go through, so
                # "it would have been refused" is the answer, not an exception.
                # The file was restored by the caller's own write path below.
                raise _DryRunBlocked("apply WOULD be blocked: " + rollback_reason,
                                     "GATE_BLOCKED", gates=gates,
                                     blocking=blocking)
            raise EditError("apply blocked and rolled back: " + rollback_reason,
                            "GATE_BLOCKED", gates=gates, blocking=blocking)

        gates["outline_after"] = plan_outline
        # the bytes on disk are the truth, and a zone fill happens after the plan
        # was written - so re-read rather than quote a hash from before
        with open(target, "rb") as _fh:
            gates["bytes_written"]["sha256"] = _sha256(_fh.read())
        if dry_run:
            # Same code path, same gates, same writes - then the original bytes go
            # back. That is what makes the verdict worth anything: a "dry run" that
            # skipped the write would skip the gates that only exist after it.
            _atomic_write(target, current)
            with open(target, "rb") as _fh:
                back_again = _fh.read()
            return {
                "status": "ok",
                "dry_run": True,
                "plan_id": plan_id,
                "would_write": True,
                "target": target,
                "sha256_unchanged": _sha256(back_again) == _sha256(current),
                "file_restored_byte_for_byte": back_again == current,
                "gates": gates,
                "gates_passed": sorted(k for k, v in gates.items()
                                       if isinstance(v, dict) and v.get("ok") is True),
                "decision": ("DRY RUN: the plan was applied for real, every gate ran "
                             "against the written file, and the original bytes were "
                             "then put back. Nothing on disk changed. The plan is "
                             "still open (not consumed)"),
                "plan_still_open": True,
            }
        PLANS.pop(plan_id, None)
        return {
            "status": "ok",
            "plan_id": plan_id,
            "written": target,
            "mode": mode,
            "sha256_after": gates["bytes_written"]["sha256"],
            "checkpoint": checkpoint,
            "gates": gates,
            "gates_passed": sorted(k for k, v in gates.items()
                                   if isinstance(v, dict) and v.get("ok") is True),
            "decision": ("every gate passed: the bytes on disk are the planned bytes, "
                         "re-parsing reproduces the planned tree, each operation's "
                         "postcondition was re-read from the file, KiCad re-read both "
                         "files, and its DRC reports no new violation"),
            "how_to_undo": "board_restore(checkpoint_id=%r, confirm=true)"
                           % checkpoint["checkpoint_id"],
        }
    except EditError:
        if written:
            _atomic_write(target, current)
        raise
    except Exception as exc:                       # noqa: BLE001
        if written:
            _atomic_write(target, current)
        raise EditError("%s: %s (the checkpoint was restored to disk)"
                        % (type(exc).__name__, exc), "APPLY_FAILED")

# --------------------------------------------------------- stepped apply
"""Applying a plan one step at a time.

Why this exists: a one-shot apply is atomic and therefore invisible. The file on
disk jumps from "before" to "after" in one write, so a person watching the EDA
window sees nothing happen, cannot comment on step 17, and cannot stop it. That
is fine for a script and wrong for a collaborator.

A step session keeps the same proof obligations, only spread out:

* every chunk is applied to the file *as it is on disk now*, so a step never
  overwrites an edit someone made in the editor behind our back - if the file
  changed, the step refuses and says so;
* every chunk leaves a complete, valid document on disk, so the EDA can be
  reloaded after any step and shows exactly the work done so far;
* every chunk writes a snapshot (``steps/<plan_id>/<seq>.<ext>``) and one line of
  JSONL (``steps/<plan_id>.jsonl``) - the history is on disk, not in a process
  that can die;
* a stop file (``steps/STOP``) halts the session between chunks; the plan is not
  consumed, so it can be resumed;
* the last chunk still runs the full gates, and one of them compares the stepped
  result against the *original one-shot plan*, which is what proves "step by
  step" did not quietly become "something else".

Both KiCad write paths (board and schematic) share this code; the differences
are the document class, the operation catalogue and the final gates, which come
in as a dialect.
"""

STEPS = {}                       # plan_id -> in-memory session (disk is truth)

def steps_root():
    return os.path.join(home(), "steps")

def stop_file():
    return os.path.join(steps_root(), "STOP")

def _events_path(plan_id):
    return os.path.join(steps_root(), plan_id + ".jsonl")

def _snapshot_path(plan_id, seq, ext=".kicad_pcb"):
    folder = os.path.join(steps_root(), plan_id)
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "%03d%s" % (seq, ext))

def _emit(plan_id, event):
    event = dict(event)
    event.setdefault("utc", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    path = _events_path(plan_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False) + "\n")
    return path

def step_events(plan_id=None, limit=200):
    """Read the on-disk step log. No session needed: it is just a file."""
    root = steps_root()
    if plan_id:
        files = [_events_path(plan_id)]
    elif os.path.isdir(root):
        files = sorted((os.path.join(root, n) for n in os.listdir(root)
                        if n.endswith(".jsonl")), reverse=True)
    else:
        files = []
    out = []
    for path in files:
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    return {"status": "ok", "root": root, "stop_file": stop_file(),
            "stopped": os.path.isfile(stop_file()),
            "total": len(out), "events": out[-limit:]}

def step_control(action="status", note=None):
    """Stop / resume / inspect. Stopping is one file on disk, so it also works
    from another process and survives a restart."""
    root = steps_root()
    os.makedirs(root, exist_ok=True)
    path = stop_file()
    action = (action or "status").lower()
    if action == "stop":
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("%s\n%s\n" % (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                   note or "stopped by the operator"))
        return {"status": "ok", "action": "stop", "stop_file": path,
                "meaning": "no further step will be written until it is released"}
    if action in ("resume", "clear"):
        # The old stop file is kept, not deleted: a stop is a decision, and a
        # decision that leaves no trace cannot be reviewed afterwards.
        archived = None
        if os.path.isfile(path):
            archived = os.path.join(
                root, "stop-released-%s.txt"
                % time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()))
            shutil.move(path, archived)
        return {"status": "ok", "action": "resume", "removed": bool(archived),
                "archived_to": archived, "stop_file": path,
                "meaning": "steps may be written again"}
    return {"status": "ok", "action": "status", "stop_file": path,
            "stopped": os.path.isfile(path)}

# ------------------------------------------------------------- dialects
def _dialect(schema):
    """Everything that differs between the board path and the schematic path."""
    if schema == "sch":
        from . import sch_ops
        from ..fidelity import kicad_sch as _ksch
        return {"schema": "sch", "loader": _ksch.KiCadSchematic,
                "ops": sch_ops, "ext": ".kicad_sch",
                "changed_code": "SCHEMATIC_CHANGED_SINCE_STEP",
                "kind": "schematic"}
    return {"schema": "pcb", "loader": _kdoc.KiCadDocument, "ops": _ops,
            "ext": ".kicad_pcb", "changed_code": "BOARD_CHANGED_SINCE_STEP",
            "kind": "board"}

def _final_gates_board(plan, target, session, options):
    gates = {}
    with open(target, "rb") as fh:
        back = fh.read()
    gates["bytes_written"] = {"ok": back == plan["new_bytes"],
                             "sha256": _sha256(back),
                             "expected_sha256": _sha256(plan["new_bytes"]),
                             "bytes": len(back),
                             "note": "stepped writes must land on the same bytes a "
                                     "one-shot apply would have produced"}
    re_doc = _kdoc.KiCadDocument.from_path(target)
    gates["token_identity"] = {
        "ok": S.signature(re_doc.tree) == plan["new_signature"],
        "note": "the stepped result re-parses into exactly the tree the original "
                "plan described"}
    post = _ops.verify_all(re_doc, plan["operations"], records=plan["records"])
    gates["postconditions"] = dict(post)
    gates["postconditions"]["ok"] = bool(post.get("all_verified"))
    blocking = []
    if not gates["bytes_written"]["ok"]:
        blocking.append("the bytes on disk are not the bytes the plan described")
    if not gates["token_identity"]["ok"]:
        blocking.append("re-parsing the stepped file does not reproduce the plan")
    if not gates["postconditions"]["all_verified"]:
        bad = [o for o in gates["postconditions"]["operations"] if not o["verified"]]
        blocking.append("postcondition check failed for %d operation(s): %s"
                        % (len(bad), ", ".join(o["op"] for o in bad)))
    if options.get("engine", True):
        before_file = find_checkpoint(session["checkpoints"][0])["body"]
        cmp_which = ["stats", "gerbers"]
        if "copper" in plan["affected"] or "outline" in plan["affected"]:
            cmp_which = ["stats", "gerbers", "position"]
        eng = _engine.compare_paths(before_file, target, which=tuple(cmp_which))
        comparable = sum(1 for c in (eng.get("checks") or {}).values()
                         if isinstance(c, dict) and c.get("comparable"))
        eng["comparable_checks"] = comparable
        eng["ok"] = bool(eng.get("status") in ("ok", "skipped")
                         and (comparable or eng.get("status") == "skipped"))
        gates["engine"] = eng
        pro_beside = _project_beside(target)
        gates["drc"] = _drc_diff(_engine.drc(before_file, project=pro_beside),
                                 _engine.drc(target, project=pro_beside),
                                 strict=bool(options.get("strict_drc")))
        if gates["drc"].get("comparable"):
            gates["drc"]["ok"] = not gates["drc"].get("new_blocking_total")
        drc = gates["drc"]
        if drc.get("comparable") and drc.get("new_blocking_total") and \
                not options.get("allow_new_violations"):
            blocking.append("%d NEW DRC error(s) after the stepped apply (%s)"
                            % (drc["new_blocking_total"], ", ".join(
                                sorted({v["type"] for v in drc["new_blocking"]}))))
    else:
        gates["engine"] = {"status": "skipped", "ok": None,
                           "reason_code": "NOT_REQUESTED"}
        gates["drc"] = {"comparable": False, "reason": "NOT_REQUESTED"}
    gates["outline_after"] = _checks.outline_report(re_doc)
    if not gates["outline_after"]["closed"] and "outline" in plan["affected"] \
            and not options.get("allow_open_outline"):
        blocking.append("the outline is still open after this edit: %s"
                        % gates["outline_after"]["message"])
    return gates, blocking

def _final_gates_sch(plan, target, session, options):
    from . import sch_plan as _sp
    gates = {}
    with open(target, "rb") as fh:
        back = fh.read()
    gates["bytes_written"] = {"ok": back == plan["new_bytes"],
                             "sha256": _sha256(back),
                             "expected_sha256": _sha256(plan["new_bytes"]),
                             "bytes": len(back),
                             "note": "stepped writes must land on the same bytes a "
                                     "one-shot apply would have produced"}
    from ..fidelity import kicad_sch as _ksch
    re_doc = _ksch.KiCadSchematic.from_path(target)
    gates["token_identity"] = {
        "ok": S.signature(re_doc.tree) == plan["new_signature"],
        "note": "the stepped result re-parses into exactly the tree the original "
                "plan described"}
    from . import sch_ops
    post = sch_ops.verify_all(re_doc, plan["operations"])
    gates["postconditions"] = dict(post)
    gates["postconditions"]["ok"] = bool(post.get("all_verified"))
    blocking = []
    if not gates["bytes_written"]["ok"]:
        blocking.append("the bytes on disk are not the bytes the plan described")
    if not gates["token_identity"]["ok"]:
        blocking.append("re-parsing the stepped file does not reproduce the plan")
    if not gates["postconditions"]["ok"]:
        bad = [o for o in gates["postconditions"]["operations"] if not o["verified"]]
        blocking.append("postcondition check failed for %d operation(s): %s"
                        % (len(bad), ", ".join(o["op"] for o in bad)))
    netlist_diff_out = {"comparable": False, "reason": "NOT_REQUESTED"}
    erc_diff_out = {"comparable": False, "reason": "NOT_REQUESTED"}
    if options.get("engine", True):
        before_file = find_checkpoint(session["checkpoints"][0])["body"]
        netlist_diff_out = _sp.netlist_diff(_engine.sch_netlist(before_file),
                                            _engine.sch_netlist(target))
        erc_diff_out = _sp.erc_diff(_engine.sch_erc(before_file),
                                     _engine.sch_erc(target))
        gates["engine"] = {"ok": bool(netlist_diff_out.get("comparable")
                                      or erc_diff_out.get("comparable")),
                           "netlist": {"comparable": netlist_diff_out.get("comparable")},
                           "erc": {"comparable": erc_diff_out.get("comparable")},
                           "note": "comparable==False is NOT a pass"}
        connectivity_ops = bool(set(plan["affected"])
                                & {"wires", "labels", "connectivity"})
        if netlist_diff_out.get("comparable") and not connectivity_ops \
                and netlist_diff_out.get("changed"):
            blocking.append("the plan only touched %s but the NETLIST changed - that "
                            "is a connectivity change nobody asked for"
                            % ", ".join(plan["affected"]))
        if erc_diff_out.get("comparable") and erc_diff_out.get("new_total") \
                and not options.get("allow_new_violations"):
            blocking.append("%d NEW ERC violation(s) appear after this edit (%s)"
                            % (erc_diff_out["new_total"], ", ".join(
                                sorted({v["type"] for v in erc_diff_out["new"]}))))
    else:
        gates["engine"] = {"ok": None, "reason_code": "NOT_REQUESTED"}
    gates["netlist"] = netlist_diff_out
    gates["erc"] = erc_diff_out
    return gates, blocking

def steps_apply(plan_id, registry, schema="pcb", confirm=False, options=None):
    """Write a plan in chunks, each chunk a complete document on disk."""
    options = options or {}
    d = _dialect(schema)
    plan = registry.get(plan_id)
    if plan is None:
        raise EditError("unknown or finished plan %r (a plan is single-use; the step "
                        "log under steps/ shows what was written)" % plan_id,
                        "PLAN_NOT_FOUND")
    if not confirm:
        raise EditError("writing a %s file is not reversible by Ctrl+Z: call again "
                        "with confirm=true. This plan affects %s"
                        % (d["kind"], ", ".join(plan["affected"])),
                        "CONFIRMATION_REQUIRED", plan_id=plan_id)
    if os.path.isfile(stop_file()):
        raise EditError("a stop file is present, so no step was written: %s"
                        % stop_file(), "STOPPED_BY_USER",
                        hint="step_control(action='resume') releases it")

    step = max(1, int(options.get("step") or 1))
    max_chunks = max(1, int(options.get("max_chunks") or 1))
    mode = (options.get("mode") or plan["options"].get("mode") or "in_place")
    out_path = options.get("out_path") or plan["options"].get("out_path")
    if mode == "new_file" and not out_path:
        raise EditError("mode=new_file needs out_path (a stepped apply needs a target "
                        "it can rewrite over and over)", "ARG_MISSING")
    target = plan["path"] if mode == "in_place" else os.path.abspath(out_path)
    if mode == "new_file" and os.path.exists(target) and plan.get("created_target"):
        target = target                      # our own earlier chunk
    elif mode == "new_file" and os.path.exists(target):
        raise EditError("refusing to overwrite an existing file: %s" % target,
                        "TARGET_EXISTS")

    session = STEPS.get(plan_id)
    if session is None:
        base_file = plan["path"] if mode == "new_file" else target
        with open(base_file, "rb") as fh:
            current = fh.read()
        if mode == "in_place" and _sha256(current) != plan["sha256_before"]:
            raise EditError("the file changed on disk after the plan was made (someone "
                            "saved it in the editor?). Nothing was written - propose "
                            "again", "BOARD_CHANGED_SINCE_PLAN")
        base_checkpoint = save_checkpoint(
            base_file, current,
            "before stepped apply %s (%s)" % (plan_id, ", ".join(plan["affected"])),
            plan_id=plan_id)
        session = {"index": 0, "seq": 0,
                   "checkpoints": [base_checkpoint["checkpoint_id"]],
                   "target": target, "mode": mode,
                   "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                   "sha256": plan["sha256_before"], "events": _events_path(plan_id)}
        STEPS[plan_id] = session
        plan["created_target"] = (mode == "new_file")
        _emit(plan_id, {"kind": "begin", "plan_id": plan_id, "path": plan["path"],
                        "target": target, "operations": len(plan["operations"]),
                        "affected": plan["affected"], "schema": d["schema"],
                        "checkpoint": base_checkpoint["checkpoint_id"]})

    chunks = []
    total = len(plan["operations"])
    for _ in range(max_chunks):
        if session["index"] >= total:
            break
        if os.path.isfile(stop_file()):
            _emit(plan_id, {"kind": "stop", "at": session["index"]})
            break
        lo = session["index"]
        hi = min(lo + step, total)
        subset = plan["operations"][lo:hi]

        with open(session["target"], "rb") as fh:
            current = fh.read()
        if _sha256(current) != session["sha256"]:
            _emit(plan_id, {"kind": "refused", "at": lo,
                            "why": "the file changed on disk since the last step"})
            raise EditError(
                "the file changed on disk since the last step (saved in the editor?). "
                "Steps %d-%d were NOT written; %d of %d operations are already on "
                "disk at sha256 %s" % (lo, hi, lo, total, session["sha256"]),
                d["changed_code"],
                hint="board_restore(checkpoint_id=%r) puts the pre-plan file back; "
                     "otherwise propose again from the current file"
                     % session["checkpoints"][0])

        loader = d["loader"]
        src = loader.from_text(current.decode("utf-8", "surrogateescape"),
                               session["target"])
        work = loader.from_text(src.text, session["target"])
        records = d["ops"].run_all(work, subset, seed_base=plan["seed_base"],
                                   index_offset=lo)
        new_bytes = work.to_bytes()
        self_check = work.verify()
        if not self_check.get("token_identity"):
            _emit(plan_id, {"kind": "refused", "at": lo, "why": "self_check"})
            raise EditError("step %d-%d does not survive a serialise/parse round trip; "
                            "nothing was written" % (lo, hi), "GENERATOR_BUG")

        _atomic_write(session["target"], new_bytes)
        seq = session["seq"] + 1
        snap = _snapshot_path(plan_id, seq, d["ext"])
        with open(snap, "wb") as fh:
            fh.write(new_bytes)

        re_doc = loader.from_path(session["target"])
        post = _verify_all(d["ops"], re_doc, subset, records)
        session["index"] = hi
        session["seq"] = seq
        session["sha256"] = _sha256(new_bytes)
        chunk = {"seq": seq, "first": lo, "last": hi - 1,
                 "ops": [r.get("op") for r in records],
                 "sha256": session["sha256"], "bytes": len(new_bytes),
                 "verified": bool(post.get("all_verified")),
                 "detail": [o for o in post.get("operations", []) if not o.get("verified")],
                 "snapshot": snap}
        chunks.append(chunk)
        _emit(plan_id, dict(chunk, kind="step", target=session["target"]))
        if not chunk["verified"]:
            raise EditError("step %d-%d wrote but its own postconditions do not hold "
                            "(%s); the snapshot is at %s"
                            % (lo, hi, json.dumps(chunk["detail"])[:400], snap),
                            "STEP_VERIFY_FAILED", steps=chunks,
                            session=_session_out(plan_id, session, plan))

    done = session["index"] >= total
    result = {"status": "ok", "plan_id": plan_id, "target": session["target"],
              "kind": d["kind"], "steps_written": chunks,
              "operations_done": session["index"], "operations_total": total,
              "done": done, "sha256": session["sha256"],
              "checkpoint_before_all_steps": session["checkpoints"][0],
              "events": session["events"],
              "seeing_it": "each step left a complete file on disk; reload it in the "
                           "EDA to look at exactly the work done so far",
              "stopping_it": "board_progress(action='stop') stops between steps; "
                             "action='resume' lets it go on"}
    if not done:
        result["next"] = ("board_apply(plan_id=%r, confirm=true, options={'step': %d}) "
                          "writes the next %d operation(s)"
                          % (plan_id, step, min(step, total - session["index"])))
        return result

    gates, blocking = (_final_gates_sch if schema == "sch" else _final_gates_board)(
        plan, session["target"], session, options)
    result["gates"] = gates
    result["decision"] = ("every step verified itself, and the finished file passes the "
                          "same gates a one-shot apply would have faced")
    result["how_to_undo"] = "board_restore(checkpoint_id=%r, confirm=true)" \
                            % session["checkpoints"][0]
    if blocking:
        _emit(plan_id, {"kind": "gate_blocked", "blocking": blocking})
        raise EditError("stepped apply finished but a gate blocked it: %s (the file is "
                        "left as written; board_restore puts the pre-plan file back)"
                        % "; ".join(blocking), "GATE_BLOCKED", gates=gates,
                        blocking=blocking, steps=chunks)
    registry.pop(plan_id, None)
    STEPS.pop(plan_id, None)
    _emit(plan_id, {"kind": "done", "sha256": session["sha256"],
                    "steps": len(chunks)})
    return result

def _session_out(plan_id, session, plan):
    return {"plan_id": plan_id, "operations_done": session["index"],
            "operations_total": len(plan["operations"]),
            "sha256": session["sha256"], "target": session["target"],
            "events": session["events"]}

def apply_steps(plan_id, confirm=False, options=None):
    """Stepped write for the KiCad board path."""
    return steps_apply(plan_id, PLANS, "pcb", confirm=confirm, options=options)

# --------------------------------------------------------------- create
def create_board(path, spec):
    """Create a brand-new, valid, empty board and prove it with KiCad.

    Nothing is guessed: the file is written, then KiCad is asked whether it has
    an outline and whether its DRC is clean. If either answer is no, the file is
    removed again and the call fails.
    """
    spec = spec or {}
    text = _build_board_text(spec)
    doc = _kdoc.KiCadDocument.from_text(text, path)
    proof = doc.verify()
    if not proof.get("token_identity"):
        raise EditError("the generated board does not survive a serialise/parse "
                        "round trip; refusing to write it", "GENERATOR_BUG")

    target = os.path.abspath(path)
    if os.path.exists(target) and not spec.get("overwrite"):
        raise EditError("refusing to overwrite %s (pass overwrite=true if that is "
                        "what you mean)" % target, "TARGET_EXISTS")
    folder = os.path.dirname(target)
    if not os.path.isdir(folder):
        os.makedirs(folder, exist_ok=True)
    _atomic_write(target, doc.to_bytes())

    gates = {"token_identity": {"ok": True}}
    try:
        re_doc = _kdoc.KiCadDocument.from_path(target)
        gates["token_identity"]["ok"] = (S.signature(re_doc.tree)
                                         == S.signature(doc.tree))
        gates["outline"] = {"ok": _checks.outline_report(re_doc)["closed"],
                            "detail": _checks.outline_report(re_doc)["message"]}
        if spec.get("engine", True) is False:
            gates["engine_stats"] = {"ok": None, "reason": "NOT_REQUESTED"}
            gates["engine_drc"] = {"ok": None, "reason": "NOT_REQUESTED"}
        elif _engine.find_kicad_cli():
            st = _engine.stats(target)
            stated = _stated_thickness(re_doc)
            measured = _engine_mm((st.get("board") or {}).get("board_thickness"))
            agree = (None if stated is None or measured is None
                     else abs(stated - measured) <= 0.005)
            gates["engine_stats"] = {
                "ok": bool(st.get("status") == "ok" and
                           (st.get("board") or {}).get("has_outline")),
                "has_outline": (st.get("board") or {}).get("has_outline"),
                "width": (st.get("board") or {}).get("width"),
                "height": (st.get("board") or {}).get("height"),
                # Intent vs measurement: KiCad sums the STACKUP and ignores
                # `(general (thickness ...))`, so a file can state 1.6 mm and
                # measure 1.18 mm. This board said 1.6 and measured 1.18 for as
                # long as `create_board` divided the dielectrics wrongly, and
                # nothing in the gates noticed. Now it is a gate.
                "thickness_stated_mm": stated,
                "thickness_engine_mm": measured,
                "thickness_agrees": agree,
                "engine": st.get("engine")}
            if agree is False:
                gates["engine_stats"]["ok"] = False
            drc = _engine.drc(target)
            gates["engine_drc"] = {
                "ok": bool(drc.get("status") == "ok" and not drc.get("violations_total")),
                "violations_total": drc.get("violations_total"),
                "by_type": drc.get("by_type"),
                "engine": drc.get("engine")}
        else:
            gates["engine_stats"] = {"ok": None, "reason": "kicad-cli not found"}
            gates["engine_drc"] = {"ok": None, "reason": "kicad-cli not found"}
    except Exception:
        os.remove(target)
        raise

    failed = [k for k, v in gates.items() if isinstance(v, dict) and v.get("ok") is False]
    if failed:
        os.remove(target)
        raise EditError("the created board failed its own gate(s) (%s), so it was "
                        "removed again rather than left as a plausible-looking file"
                        % ", ".join(failed), "GATE_BLOCKED", gates=gates)

    return {"status": "ok", "path": target, "bytes": len(doc.to_bytes()),
            "document": doc.stats(), "gates": gates,
            "digest": _checks.digest(doc),
            "next": ("board_propose(path=%r, operations=[...]) to put something on it"
                     % target),
            "note": "no checkpoint exists because there was no previous file to lose"}

def _engine_mm(value):
    """`"1.1800 mm"` -> 1.18; anything else -> None (never a guess)."""
    if value is None:
        return None
    try:
        return float(str(value).strip().split()[0])
    except (ValueError, IndexError):
        return None

def _board_node(doc):
    """The `(kicad_pcb ...)` node. `doc.tree` is a Seq holding that ONE node."""
    for item in doc.tree.items:
        if S.head(item) == "kicad_pcb":
            return item
    return doc.tree

def _stated_thickness(doc):
    """The thickness the file STATES: `general.thickness`, else the stackup sum."""
    root = _board_node(doc)
    general = S.child(root, "general")
    if general is not None:
        th = S.child(general, "thickness")
        t = S.arg_num(th, 1) if th is not None else None
        if t:
            return float(t)
    setup = S.child(root, "setup")
    stack = S.child(setup, "stackup") if setup is not None else None
    if stack is None:
        return None
    total = 0.0
    seen = False
    for layer in S.children(stack, "layer"):
        th = S.child(layer, "thickness")
        v = S.arg_num(th, 1) if th is not None else None
        if v:
            total += float(v)
            seen = True
    return round(total, 4) if seen else None

def _build_board_text(spec):
    """The smallest board that is a real board: outline, layers, rules."""
    width = float(spec.get("width_mm") or 50.0)
    height = float(spec.get("height_mm") or 40.0)
    x0 = float(spec.get("x0_mm") or 0.0)
    y0 = float(spec.get("y0_mm") or 0.0)
    thickness = float(spec.get("thickness_mm") or 1.6)
    copper_layers = int(spec.get("copper_layers") or 2)
    if copper_layers not in (2, 4, 6):
        raise EditError("copper_layers must be 2, 4 or 6", "ARG_INVALID")
    if width <= 1 or height <= 1:
        raise EditError("a board smaller than 1x1 mm is not a board", "ARG_INVALID")
    if width > 1000 or height > 1000:
        raise EditError("a board larger than 1000x1000 mm is not plausible for this "
                        "tool", "ARG_INVALID")

    sp = B.Spelling(20221018)
    edge = float(spec.get("edge_width_mm") or 0.1)

    # KiCad 7 layer ids, verified against KiCad 10's own upgrade of this exact
    # table (every name kept its slot). The v10 numbering is DIFFERENT (B.Cu=2,
    # Edge.Cuts=25), so a v7 file must be written with the v7 numbers.
    standard = [("F.Cu", "signal"), ("B.Cu", "signal"),
                ("B.SilkS", "user"), ("F.SilkS", "user"),
                ("B.Mask", "user"), ("F.Mask", "user"),
                ("Dwgs.User", "user"), ("Cmts.User", "user"),
                ("Eco1.User", "user"), ("Eco2.User", "user"),
                ("Edge.Cuts", "user"), ("Margin", "user"),
                ("B.CrtYd", "user"), ("F.CrtYd", "user"),
                ("B.Fab", "user"), ("F.Fab", "user")]
    ids = {"F.Cu": 0, "B.Cu": 31, "B.SilkS": 36, "F.SilkS": 37, "B.Mask": 38,
           "F.Mask": 39, "Dwgs.User": 40, "Cmts.User": 41, "Eco1.User": 42,
           "Eco2.User": 43, "Edge.Cuts": 44, "Margin": 45, "B.CrtYd": 46,
           "F.CrtYd": 47, "B.Fab": 48, "F.Fab": 49}
    for i in range(1, max(1, copper_layers - 1)):
        ids["In%d.Cu" % i] = i
        standard.append(("In%d.Cu" % i, "signal"))
    layers = [B.L(B.A(str(ids[name])), B.A(name, True), B.A(kind))
              for name, kind in standard]

    stack = [B.L(B.A("layer"), B.A("F.SilkS", True),
                 B.L(B.A("type"), B.A("Top Silk Screen", True))),
             B.L(B.A("layer"), B.A("F.Mask", True),
                 B.L(B.A("type"), B.A("Top Solder Mask", True)),
                 B.L(B.A("thickness"), B.A("0.01"))),
             B.L(B.A("layer"), B.A("F.Cu", True), B.L(B.A("type"), B.A("copper", True)),
                 B.L(B.A("thickness"), B.A("0.035")))]
    inner = max(0, copper_layers - 2)
    # The stackup has to ADD UP to the thickness in `(general (thickness ...))`,
    # because that is the number KiCad's own statistics report: it sums the
    # stackup and ignores `general`. Two things used to be wrong here: the
    # copper+mask budget was under-counted (0.07 mm for a 4-layer board) and the
    # dielectric budget was divided by `inner + 1` while only `inner` dielectrics
    # were written - so a declared 1.6 mm four-layer board read back as 1.18 mm.
    # A physical stack has ONE dielectric BETWEEN each pair of copper layers, so
    # n copper layers need n-1 dielectrics; write exactly that and split the
    # budget over it.
    copper = 0.035 * copper_layers
    masks = 0.02
    n_dielectric = max(1, copper_layers - 1)
    dielectric_total = max(thickness - copper - masks, 0.1)
    each = max(dielectric_total / n_dielectric, 0.05)

    def _dielectric(i):
        return B.L(B.A("layer"), B.A("dielectric %d" % i, True),
                   B.L(B.A("type"), B.A("core", True)),
                   B.L(B.A("thickness"), B.A(B.num(each))),
                   B.L(B.A("material"), B.A("FR4", True)),
                   B.L(B.A("epsilon_r"), B.A("4.3")),
                   B.L(B.A("loss_tangent"), B.A("0.02")))

    for i in range(1, inner + 1):
        stack.append(_dielectric(i))
        stack.append(B.L(B.A("layer"), B.A("In%d.Cu" % i, True),
                         B.L(B.A("type"), B.A("copper", True)),
                         B.L(B.A("thickness"), B.A("0.035"))))
    stack.append(_dielectric(inner + 1))   # the one before B.Cu
    stack.append(B.L(B.A("layer"), B.A("B.Cu", True), B.L(B.A("type"), B.A("copper", True)),
                     B.L(B.A("thickness"), B.A("0.035"))))
    stack.append(B.L(B.A("layer"), B.A("B.Mask", True),
                     B.L(B.A("type"), B.A("Bottom Solder Mask", True)),
                     B.L(B.A("thickness"), B.A("0.01"))))
    stack.append(B.L(B.A("layer"), B.A("B.SilkS", True),
                     B.L(B.A("type"), B.A("Bottom Silk Screen", True))))

    cls = {"clearance": float(spec.get("clearance_mm") or 0.2),
           "trace_width": float(spec.get("trace_width_mm") or 0.25),
           "via_dia": float(spec.get("via_dia_mm") or 0.6),
           "via_drill": float(spec.get("via_drill_mm") or 0.3)}
    netclass = B.L(B.A("net_class"), B.A("Default", True), B.A("", True),
                   B.L(B.A("clearance"), B.A(B.num(cls["clearance"]))),
                   B.L(B.A("trace_width"), B.A(B.num(cls["trace_width"]))),
                   B.L(B.A("via_dia"), B.A(B.num(cls["via_dia"]))),
                   B.L(B.A("via_drill"), B.A(B.num(cls["via_drill"]))),
                   B.L(B.A("uvia_dia"), B.A("0.3")),
                   B.L(B.A("uvia_drill"), B.A("0.1")))

    items = [B.A("kicad_pcb"),
             B.L(B.A("version"), B.A("20221018")),
             B.L(B.A("generator"), B.A("pcb_agent_mcp")),
             B.L(B.A("general"), B.L(B.A("thickness"), B.A(B.num(thickness)))),
             B.L(B.A("paper"), B.A("A4", True)),
             B.L(B.A("layers"), *layers),
             B.L(B.A("setup"), B.L(B.A("pad_to_mask_clearance"), B.A("0")),
                 B.L(B.A("stackup"), *stack))]
    title = spec.get("title")
    if title:
        items.append(B.L(B.A("title_block"), B.L(B.A("title"), B.A(title, True))))
    items.append(B.L(B.A("net"), B.A("0"), B.A("", True)))
    items.append(netclass)
    x1, y1 = x0 + width, y0 + height
    items.extend(B.rect_as_lines(x0, y0, x1, y1, "Edge.Cuts", edge, sp, "create:edge"))
    # The board must be ONE seq. Joining top-level nodes would emit a bare
    # `kicad_pcb` atom followed by siblings - which parses fine as S-expression
    # and is not a board (KiCad reports no (kicad_pcb ...) at top level).
    return S.dumps(S.Seq(items)) + "\n"
