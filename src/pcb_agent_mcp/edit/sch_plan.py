"""The schematic write path: propose -> digest -> approve -> apply, with gates.

Same ceremony as the board path (`plan.py`), different referee. A board edit is
judged by DRC and by the plotted artwork; a schematic edit is judged by **the
netlist and ERC**, because a schematic's product is connectivity:

    schematic_propose  computes everything, writes nothing: the plan id, the
                       digest (what the sheet becomes) and - when KiCad is
                       installed - the netlist the sheet has *now*
    schematic_apply    needs that plan id AND confirm=true, takes a checkpoint,
                       writes atomically, then re-reads the file, re-runs the
                       netlister and ERC, and diffs both against the checkpoint

Four gates, none of which trusts the code that did the writing:

1. `bytes_written`  - the bytes on disk are the bytes that were planned
2. `token_identity` - re-parsing the file reproduces the planned tree
3. `postconditions` - each operation's own promise, re-read from the file
4. `engine`         - KiCad's netlister + ERC on both files:
                      a NEW ERC violation blocks;
                      a netlist change from a plan that could not have changed
                      connectivity blocks too (that would be silent corruption);
                      a plan that *claimed* to wire something and produced an
                      unchanged netlist is reported loudly (the wire missed).

Any blocking gate restores the checkpoint and says so.
"""
import collections
import json
import os
import time
import uuid as _uuid

from ..fidelity import engine as _engine
from ..fidelity import kicad_sch as _ksch
from ..fidelity import sexpr as S
from . import build as B                      # noqa: F401  (spelling helpers)
from . import plan as _plan
from . import sch_ops as _ops
from .fsutil import atomic_write as _atomic_write
from .fsutil import sha256 as _sha256
from .plan import EditError
from .plan import save_checkpoint

PLANS = _plan.PLANS
PLAN_TTL = _plan.PLAN_TTL
PLAN_MAX = _plan.PLAN_MAX

FRAME = _ops.FRAME

# --------------------------------------------------------------- digests
def digest(doc):
    """What the sheet *is*, in the terms a schematic is reviewed in."""
    syms = doc.symbols()
    refs = sorted(s["reference"] or "" for s in syms)
    labels = collections.Counter()
    for head, key in (("label", "local"), ("global_label", "global"),
                      ("hierarchical_label", "hierarchical")):
        for node in doc.items(head):
            labels[key] += 1
    names = collections.Counter()
    for node in doc.items("global_label"):
        names[S.arg_text(node, 1)] += 1
    return {
        "bytes": len(doc.to_bytes()),
        "version": doc.version(),
        "paper": doc.paper(),
        "symbols": len(syms),
        "references": refs,
        "unannotated": [r for r in refs if not r or r.endswith("?")],
        "wires": len(doc.items("wire")),
        "buses": len(doc.items("bus")),
        "bus_entries": len(doc.items("bus_entry")),
        "junctions": len(doc.items("junction")),
        "no_connects": len(doc.items("no_connect")),
        "labels": dict(labels),
        "global_label_names": dict(sorted(names.items())),
        "texts": len(doc.items("text")),
        "graphics": {h: len(doc.items(h)) for h in
                     ("polyline", "rectangle", "image", "table", "text_box")},
        "netclass_flags": len(doc.items("netclass_flag")),
        "sheets": len(doc.items("sheet")),
        "lib_symbols": len(doc.lib_symbol_names()),
    }

def _require_kicad_sch(path):
    """Refuse every other container BY CONTENT, and say which one it was.

    A `.eskch`/`.esch` naming, a `.epro` archive and a SQLite `.eprj` all end up
    here when an agent points at the wrong file. Naming the container it actually
    is (instead of a parse error) is the difference between "wrong tool" and
    "your file is broken".
    """
    if not path:
        raise EditError("path is required", "ARG_MISSING")
    if not os.path.isfile(path):
        raise EditError("schematic file does not exist: %r (nothing is created "
                        "here)" % path, "FILE_NOT_FOUND")
    with open(path, "rb") as fh:
        head = fh.read(2048)
    if head[:2] == b"PK":
        raise EditError(
            "this is a zip archive (LCEDA Pro .epro/.eprj container), not a KiCad "
            "schematic. The LCEDA Pro schematic (.esch) write path is NOT "
            "implemented: the sheet record layout was never measured against a real "
            "save, so this server refuses it rather than guessing",
            "WRITE_UNSUPPORTED_FORMAT")
    if head.startswith(b"SQLite format 3"):
        raise EditError(
            "this is an LCEDA Pro SQLite project (.eprj/.eprj2): the board/sheet "
            "rows sit beside projects.ticket and encrypted history_data, so a write "
            "that does not advance them is exactly the change the application can "
            "treat as stale", "WRITE_UNSUPPORTED_FORMAT")
    if head.lstrip().startswith(b"(kicad_pcb"):
        raise EditError(
            "this is a KiCad BOARD, not a schematic: use board_propose / board_apply "
            "for it (the two write paths are separate because their referees are - "
            "DRC for copper, the netlister + ERC for connectivity)",
            "FORMAT_NOT_SUPPORTED")
    if not head.lstrip().startswith(b"(kicad_sch"):
        raise EditError(
            "this file is not a KiCad schematic (it does not start with "
            "'(kicad_sch'): refusing to parse it as one", "NOT_A_SCHEMATIC")
    return path

def _netlist_keys(result):
    if not isinstance(result, dict) or result.get("status") != "ok":
        return None
    nets = {}
    for n in result.get("nets") or []:
        nets[n.get("name")] = tuple(tuple(x) for x in (n.get("nodes") or []))
    comps = {}
    for c in result.get("components") or []:
        comps[c.get("ref")] = (c.get("value"), c.get("footprint"))
    return {"nets": nets, "components": comps}

def netlist_diff(before, after, limit=20):
    """What the connectivity actually became. This is the schematic's DRC."""
    a, b = _netlist_keys(before), _netlist_keys(after)
    if a is None or b is None:
        return {"comparable": False,
                "reason": (before or {}).get("reason_code")
                          or (after or {}).get("reason_code")
                          or "the netlister did not run"}
    na, nb = a["nets"], b["nets"]
    added = sorted(set(nb) - set(na))
    removed = sorted(set(na) - set(nb))
    changed = []
    for name in sorted(set(na) & set(nb)):
        if na[name] != nb[name]:
            changed.append({"net": name,
                            "nodes_added": [list(x) for x in
                                            sorted(set(nb[name]) - set(na[name]))],
                            "nodes_removed": [list(x) for x in
                                              sorted(set(na[name]) - set(nb[name]))]})
    ca, cb = a["components"], b["components"]
    return {
        "comparable": True,
        "nets_before": len(na), "nets_after": len(nb),
        "nets_added": added[:limit], "nets_added_total": len(added),
        "nets_removed": removed[:limit], "nets_removed_total": len(removed),
        "nets_changed": changed[:limit], "nets_changed_total": len(changed),
        "components_added": sorted(set(cb) - set(ca)),
        "components_removed": sorted(set(ca) - set(cb)),
        "components_changed": sorted(r for r in set(ca) & set(cb) if ca[r] != cb[r]),
        "components_added_total": len(set(cb) - set(ca)),
        "components_removed_total": len(set(ca) - set(cb)),
        "components_changed_total": len([r for r in set(ca) & set(cb)
                                         if ca[r] != cb[r]]),
        # `changed` is CONNECTIVITY ONLY. A component value or footprint edit is
        # not a wiring change, so it shows up in components_changed (and in
        # metadata_changed) but must not be reported as a netlist change - that
        # would make every legitimate field edit look like silent damage.
        "changed": bool(added or removed or changed
                        or set(ca) != set(cb)),
        "metadata_changed": bool(any(ca[r] != cb[r] for r in set(ca) & set(cb))),
        "verdict_source": "KiCad netlister (connectivity, not this server's tracing)",
    }

def _erc_items(result):
    """ERC violations as dicts, with the identity used for diffing.

    Identity deliberately EXCLUDES the marker position. Measured on a real demo
    sheet: a field edit that touches no geometry made KiCad report the very same
    `net_not_bus_member` warning at a different coordinate (it picks a different
    item of the same violation to point at). Keying on the coordinate turned that
    into "1 new violation" + "1 resolved" out of thin air, and the gate then
    blocked a legitimate edit. Counting by (sheet, type, severity, description)
    loses nothing that matters: a duplicate at a new place still raises the
    count, and a violation that merely moves stays invisible.
    """
    if not isinstance(result, dict) or result.get("status") != "ok":
        return None
    items = []
    for v in result.get("violations") or []:
        pos = None
        for it in v.get("items") or []:
            if it.get("pos"):
                pos = it["pos"]
                break
        items.append({"sheet": v.get("sheet"), "type": v.get("type"),
                      "severity": v.get("severity"),
                      "description": v.get("description"),
                      "pos": [round(pos["x"], 2), round(pos["y"], 2)]
                              if pos else None})
    return items

def _erc_key(item):
    return (item["sheet"], item["type"], item["severity"], item["description"])

def erc_diff(before, after, limit=20):
    a, b = _erc_items(before), _erc_items(after)
    if a is None or b is None:
        return {"comparable": False,
                "reason": (before or {}).get("reason_code")
                          or (after or {}).get("reason_code")
                          or "ERC did not run"}
    ca = collections.Counter(_erc_key(x) for x in a)
    cb = collections.Counter(_erc_key(x) for x in b)
    extra = cb - ca
    gone = ca - cb
    # attach the positions actually seen, but never let them decide identity
    new = []
    for item in b:
        k = _erc_key(item)
        if extra.get(k):
            extra[k] -= 1
            new.append(item)
    resolved = []
    for item in a:
        k = _erc_key(item)
        if gone.get(k):
            gone[k] -= 1
            resolved.append(item)
    return {"comparable": True,
            "new": [dict(x, key=list(_erc_key(x))) for x in new[:limit]],
            "new_total": len(new),
            "resolved": [{"sheet": x["sheet"], "type": x["type"],
                          "severity": x["severity"],
                          "description": x["description"]} for x in resolved[:limit]],
            "resolved_total": len(resolved),
            "before_total": len(a), "after_total": len(b),
            "identity": "(sheet, type, severity, description); position is "
                        "reported but never used as identity - KiCad re-points "
                        "the same violation at different items between runs",
            "verdict_source": "KiCad ERC engine"}

# --------------------------------------------------------------- summary
def summary(path, engine=True):
    """Read-only: what this sheet is, in the terms the write path uses."""
    path = _require_kicad_sch(path)
    doc = _ksch.KiCadSchematic.from_path(path)
    out = {
        "status": "ok",
        "path": os.path.abspath(path),
        "read_only": "this call parses and measures; it never writes",
        "frame": FRAME,
        "digest": digest(doc),
        "project": doc.project(),
        "file_stats": doc.stats(),
        "symbols": doc.symbols(),
        "self_check": {"token_identity": doc.verify().get("token_identity"),
                       "bytes_identical": doc.verify().get("bytes_identical")},
    }
    if engine:
        out["netlist"] = _engine.sch_netlist(path)
        out["erc"] = _engine.sch_erc(path)
    return out

# --------------------------------------------------------------- propose
def propose(path, operations, options=None):
    """Compute a schematic plan. Writes nothing, anywhere."""
    options = options or {}
    path = _require_kicad_sch(path)
    with open(path, "rb") as fh:
        raw = fh.read()
    head = raw.lstrip(b"\xef\xbb\xbf \t\r\n")
    if not head.startswith(b"(kicad_sch"):
        raise EditError("this is not a KiCad schematic (expected '(kicad_sch' at "
                        "the head); the board write path handles .kicad_pcb and the "
                        "record path handles .epro", "FORMAT_NOT_SUPPORTED")
    if not operations:
        raise EditError("an empty plan is not a plan: pass at least one operation",
                        "ARG_MISSING")

    src = _ksch.KiCadSchematic.from_text(
        raw.decode("utf-8", "surrogateescape"), path)
    work = _ksch.KiCadSchematic.from_text(src.text, path)     # private copy
    # the seed travels with the plan so a stepped write can reproduce every
    # generated id instead of inventing its own (see _plan.steps_apply)
    seed_base = "sch:%s:%d" % (_plan._path_key(path), int(time.time()))
    records = _ops.run_all(work, operations, seed_base=seed_base)
    new_text = work.to_text()
    new_bytes = work.to_bytes()
    self_check = work.verify()

    before = digest(src)
    after = digest(work)
    touched = _ops.affects(operations)

    warnings = []
    blocking = []

    box = _ops.page_box(work)
    outside = _outside_page(work, box)
    if outside:
        warnings.append({"gate": "outside_page",
                         "message": "%d object(s) sit outside the %s sheet" %
                                    (len(outside), (work.paper() or {}).get("size")),
                         "objects": outside[:8]})

    # An operation is judged when it runs, so a stub drawn before the label that
    # will land on its far end looks danging at that moment. Re-check every
    # flagged point against the FINISHED tree: two or more objects there (the
    # object itself plus something else) means it is attached after all.
    finisher = _ops.SchEditor(work)
    dangling = []
    for rec in records:
        for ep in (rec.get("detail") or {}).get("endpoints_unattached") or []:
            pt = ep["point"]
            if len(finisher.objects_near(pt[0], pt[1])) >= 2:
                continue
            dangling.append({"op_index": rec["index"], "op": rec["op"],
                             "point": pt})
    if dangling:
        warnings.append({"gate": "endpoint_not_attached",
                         "message": "%d wire/label endpoint(s) touch nothing else on "
                                    "the sheet - legally drawn, electrically open "
                                    "(ERC usually says 'not connected')"
                                    % len(dangling),
                         "endpoints": dangling[:8]})

    netlist_now = None
    erc_now = None
    if options.get("engine", True):
        netlist_now = _engine.sch_netlist(path)
        erc_now = _engine.sch_erc(path)
        if netlist_now.get("status") != "ok":
            warnings.append({"gate": "netlister_unavailable",
                             "message": "the netlist gate will not run: %s"
                                        % netlist_now.get("reason_code")})
        if erc_now.get("status") != "ok":
            warnings.append({"gate": "erc_unavailable",
                             "message": "the ERC gate will not run: %s"
                                        % erc_now.get("reason_code")})

    plan_id = "schplan-" + _uuid.uuid4().hex[:12]
    plan = {
        "plan_id": plan_id, "format": "kicad_sch", "path": os.path.abspath(path),
        "created": time.time(), "expires": time.time() + PLAN_TTL,
        "sha256_before": _sha256(raw), "bytes_before": len(raw),
        "operations": operations, "records": records, "seed_base": seed_base,
        "new_text": new_text, "new_bytes": new_bytes,
        "new_signature": S.signature(work.tree),
        "affected": touched,
        "options": {"precheck_engine": options.get("engine", True),
                    "mode": options.get("mode") or "in_place",
                    "out_path": options.get("out_path")},
    }
    PLANS[plan_id] = plan
    _plan._trim_plans()

    return {
        "status": "ok",
        "plan_id": plan_id,
        "path": plan["path"],
        "frame": FRAME,
        "nothing_written": "a proposal only computes; the schematic on disk is "
                           "untouched",
        "operations": records,
        "affected": touched,
        "schematic_before": before,
        "schematic_after": after,
        "self_check": {"token_identity": self_check.get("token_identity"),
                       "note": "the edited tree survives a serialise/parse round "
                               "trip before it is allowed to become a plan"},
        "netlist_before": _netlist_brief(netlist_now),
        "erc_before": _erc_brief(erc_now),
        "blocking": blocking,
        "warnings": warnings,
        "expires_in_seconds": PLAN_TTL,
        "next": ("schematic_apply(plan_id=%r, confirm=true) - that call takes a "
                 "checkpoint, writes atomically, then has KiCad's netlister and ERC "
                 "re-judge the result" % plan_id),
    }

def _netlist_brief(result):
    if not isinstance(result, dict) or result.get("status") != "ok":
        return {"status": (result or {}).get("status"),
                "reason_code": (result or {}).get("reason_code"),
                "message": "no netlist was produced; the connectivity gate will be "
                           "reported as not comparable, which is not a pass"}
    nets = result.get("nets") or []
    return {"status": "ok", "engine": result.get("engine"),
            "components": result.get("components_total"),
            "nets": result.get("nets_total"),
            "nets_sample": [n.get("name") for n in nets[:20]],
            "read_it_all_from": "schematic_view(kind='netlist')"}

def _erc_brief(result):
    if not isinstance(result, dict) or result.get("status") != "ok":
        return {"status": (result or {}).get("status"),
                "reason_code": (result or {}).get("reason_code")}
    return {"status": "ok", "engine": result.get("engine"),
            "violations_total": result.get("violations_total"),
            "by_type": result.get("by_type"),
            "by_severity": result.get("by_severity")}

def _outside_page(doc, box, margin=0.0):
    if not box:
        return []
    w, h = box
    out = []

    def near(x, y, what, uid):
        if x is None or y is None:
            return
        if x < -margin or y < -margin or x > w + margin or y > h + margin:
            out.append({"kind": what, "uuid": uid,
                        "at": [round(x, 3), round(y, 3)]})

    for item in doc.root().items[1:]:
        head = S.head(item)
        if head is None:
            continue
        a = S.child(item, "at")
        if a is not None:
            near(S.arg_num(a, 1), S.arg_num(a, 2), head, _ops._id(item))
        p = S.child(item, "pts")
        if p is not None:
            for xy in S.children(p, "xy"):
                near(S.arg_num(xy, 1), S.arg_num(xy, 2), head, _ops._id(item))
    return out

# --------------------------------------------------------------- apply
def apply_plan(plan_id, confirm=False, options=None):
    """Write the schematic plan. The only function in this module that writes."""
    options = options or {}
    plan = PLANS.get(plan_id)
    if plan is None or plan.get("format") != "kicad_sch":
        raise EditError("unknown or already-used schematic plan %r (a plan is "
                        "single-use; propose again)" % plan_id, "PLAN_NOT_FOUND")
    if not confirm:
        raise EditError("writing a schematic is not reversible by Ctrl+Z: call again "
                        "with confirm=true. The plan is %r and it affects %s"
                        % (plan_id, ", ".join(plan["affected"])),
                        "CONFIRMATION_REQUIRED", plan_id=plan_id)
    if time.time() > plan["expires"]:
        PLANS.pop(plan_id, None)
        raise EditError("plan %r expired (a proposal is not a licence to write "
                        "later)" % plan_id, "PLAN_EXPIRED")

    path = plan["path"]
    mode = (options.get("mode") or plan["options"].get("mode") or "in_place")
    out_path = options.get("out_path") or plan["options"].get("out_path")
    if mode == "new_file" and not out_path:
        raise EditError("mode=new_file needs out_path", "ARG_MISSING")

    with open(path, "rb") as fh:
        current = fh.read()
    if _sha256(current) != plan["sha256_before"]:
        raise EditError("the schematic changed on disk after the plan was made "
                        "(someone saved it in the editor?). Nothing was written - "
                        "propose again against the current file",
                        "SCHEMATIC_CHANGED_SINCE_PLAN")

    target = path if mode == "in_place" else os.path.abspath(out_path)
    if mode == "new_file" and os.path.exists(target):
        raise EditError("refusing to overwrite an existing file: %s" % target,
                        "TARGET_EXISTS")

    checkpoint = save_checkpoint(path, current,
                                 "before schematic apply %s (%s)"
                                 % (plan_id, ", ".join(plan["affected"])),
                                 plan_id=plan_id)
    gates = {}
    written = False
    try:
        _atomic_write(target, plan["new_bytes"])
        written = True

        with open(target, "rb") as fh:
            back = fh.read()
        gates["bytes_written"] = {
            "ok": back == plan["new_bytes"], "sha256": _sha256(back),
            "expected_sha256": _sha256(plan["new_bytes"]), "bytes": len(back)}

        re_doc = _ksch.KiCadSchematic.from_path(target)
        gates["token_identity"] = {
            "ok": S.signature(re_doc.tree) == plan["new_signature"],
            "note": "re-parsing the file that is now on disk reproduces exactly the "
                    "tree that was planned (every atom, every quoting)"}

        post = _ops.verify_all(re_doc, plan["operations"])
        gates["postconditions"] = dict(post)
        gates["postconditions"]["ok"] = bool(post.get("all_verified"))

        netlist_diff_out = {"comparable": False, "reason": "NOT_REQUESTED"}
        erc_diff_out = {"comparable": False, "reason": "NOT_REQUESTED"}
        if options.get("engine", True):
            before_file = checkpoint["body"]
            nl_before = _engine.sch_netlist(before_file)
            nl_after = _engine.sch_netlist(target)
            netlist_diff_out = netlist_diff(nl_before, nl_after)
            erc_before = _engine.sch_erc(before_file)
            erc_after = _engine.sch_erc(target)
            erc_diff_out = erc_diff(erc_before, erc_after)
            gates["engine"] = {
                "ok": bool(netlist_diff_out.get("comparable")
                           or erc_diff_out.get("comparable")),
                "netlist": {"comparable": netlist_diff_out.get("comparable")},
                "erc": {"comparable": erc_diff_out.get("comparable")},
                "note": ("comparable==False is NOT a pass: it means the engine could "
                         "not be asked, so nothing was proven about connectivity")}
        else:
            gates["engine"] = {"ok": None, "reason_code": "NOT_REQUESTED"}
        gates["netlist"] = netlist_diff_out
        gates["erc"] = erc_diff_out

        # ---------------- decisions
        blocking = []
        connectivity_ops = bool(set(plan["affected"])
                                & {"wires", "labels", "connectivity"})
        if not gates["bytes_written"]["ok"]:
            blocking.append("the bytes on disk are not the bytes that were planned")
        if not gates["token_identity"]["ok"]:
            blocking.append("re-parsing the written file does not reproduce the plan")
        if not gates["postconditions"]["ok"]:
            bad = [o for o in gates["postconditions"]["operations"] if not o["verified"]]
            blocking.append("postcondition check failed for %d operation(s): %s"
                            % (len(bad), ", ".join(o["op"] for o in bad)))
        if netlist_diff_out.get("comparable") and not connectivity_ops:
            if netlist_diff_out.get("changed"):
                blocking.append("the plan only touched %s but the NETLIST changed - "
                                "that is a connectivity change nobody asked for"
                                % ", ".join(plan["affected"]))
        if erc_diff_out.get("comparable") and erc_diff_out.get("new_total") \
                and not options.get("allow_new_violations"):
            blocking.append("%d NEW ERC violation(s) appear after this edit (%s); "
                            "pass allow_new_violations=true to accept them"
                            % (erc_diff_out["new_total"], ", ".join(
                                sorted({v["type"] for v in erc_diff_out["new"]}))))
        notes = []
        if netlist_diff_out.get("comparable") and netlist_diff_out.get("metadata_changed") \
                and not netlist_diff_out.get("changed"):
            # a value/footprint edit is not wiring, so it must not block - but it
            # does travel through the netlist, so it is stated rather than hidden
            notes.append("component metadata changed (%s) and the connectivity did "
                         "not - a field edit, not a wiring change"
                         % ", ".join(netlist_diff_out.get("components_changed") or []))
        if netlist_diff_out.get("comparable") and connectivity_ops \
                and not netlist_diff_out.get("changed"):
            # not blocking: deleting a stub wire that connected nothing is a
            # legitimate edit. But it is the single most likely way a plan lies to
            # itself, so it is said out loud.
            notes.append("the plan claimed to change connectivity and KiCad's "
                         "netlist is unchanged - the objects exist but connect "
                         "nothing")
        if notes:
            gates["netlist"]["warning"] = "; ".join(notes)

        if blocking:
            raise EditError("apply blocked and rolled back: " + "; ".join(blocking),
                            "GATE_BLOCKED", gates=gates, blocking=blocking)

        PLANS.pop(plan_id, None)
        return {
            "status": "ok",
            "plan_id": plan_id,
            "written": target,
            "mode": mode,
            "frame": FRAME,
            "sha256_after": gates["bytes_written"]["sha256"],
            "checkpoint": checkpoint,
            "gates": gates,
            "gates_passed": sorted(k for k, v in gates.items()
                                   if isinstance(v, dict) and v.get("ok") is True),
            "decision": ("every gate passed: the bytes on disk are the planned "
                         "bytes, re-parsing reproduces the planned tree, each "
                         "operation's postcondition was re-read from the file, "
                         "KiCad's netlister and ERC were run on both files and no "
                         "new violation appeared"),
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
def apply_steps(plan_id, confirm=False, options=None):
    """Write the schematic plan in chunks, each chunk a complete file on disk.

    Same contract as the board path: watch it in EDA, stop it between steps with
    board_progress, and the finished file still has to pass the same gates.
    """
    return _plan.steps_apply(plan_id, PLANS, "sch", confirm=confirm,
                             options=options)

# --------------------------------------------------------------- view
def view(path, kind="summary", limit=200):
    """Read-only schematic views for the model: symbols, nets, objects, raw."""
    path = _require_kicad_sch(path)
    doc = _ksch.KiCadSchematic.from_path(path)
    if kind == "symbols":
        return {"status": "ok", "frame": FRAME, "symbols": doc.symbols()}
    if kind == "objects":
        out = []
        bookkeeping = ("version", "generator", "generator_version", "uuid", "paper",
                       "title_block", "lib_symbols", "sheet_instances",
                       "embedded_fonts", "bus_alias")
        for item in doc.root().items[1:]:
            head = S.head(item)
            if head is None or head in bookkeeping:
                continue
            rec = {"kind": head, "uuid": _ops._id(item)}
            a = S.child(item, "at")
            if a is not None:
                rec["at"] = [S.arg_num(a, 1), S.arg_num(a, 2)]
            text = S.arg_text(item, 1)
            if text and head in ("label", "global_label", "hierarchical_label",
                                 "text", "text_box"):
                rec["text"] = text
            p = S.child(item, "pts")
            if p is not None:
                rec["points"] = [[S.arg_num(xy, 1), S.arg_num(xy, 2)]
                                 for xy in S.children(p, "xy")]
            out.append(rec)
        return {"status": "ok", "frame": FRAME, "total": len(out),
                "objects": out[:limit]}
    if kind == "netlist":
        r = _engine.sch_netlist(path)
        if r.get("status") != "ok":
            return r
        nets = r.pop("nets")
        return dict(r, status="ok", nets=nets[:limit], nets_shown=len(nets[:limit]))
    if kind == "erc":
        return _engine.sch_erc(path)
    if kind == "lib_symbols":
        return {"status": "ok",
                "names": doc.lib_symbol_names(),
                "note": "the embedded definitions; a lib_id here needs no `library` "
                        "argument when placing a symbol"}
    if kind == "raw":
        return {"status": "ok", "text": doc.to_text()[:400000]}
    return {"status": "ok", "frame": FRAME, "digest": digest(doc),
            "project": doc.project(), "file_stats": doc.stats(),
            "symbols": doc.symbols()[:limit]}
