"""Seal a release: freeze the Gerbers / BOM / position file together with the evidence and a hash manifest.

Ported from jlceda-pcb-ai-workflow (MIT) ``scripts/release.py``. Preconditions, all refused
rather than assumed: the process declaration is complete, G1-G5 are all CONFIRMED and still
fresh, every sealed file sits inside the project and was bound by G5 at its current hash, and
the guards are **re-run now** instead of trusting an old report.

Never uploads, never places an order. Local copy + manifest only.
"""
import hashlib
import json
import os
import re
import shutil
from datetime import datetime

from . import bomcpl, gates, gerber

VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _sha(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def _rel_in_project(project, path):
    try:
        rel = os.path.relpath(os.path.realpath(path), os.path.realpath(project))
    except ValueError:
        return None
    if rel.startswith(".."):
        return None
    return rel.replace(os.sep, "/")


def seal(project, version, gerber_path, bom=None, cpl=None, extra=None, apply=False):
    if not VERSION_RE.match(version or ""):
        raise ValueError("version may only use letters, digits and . _ -")
    doc = gates.load(project)
    process_path = os.path.join(project, doc["files"]["process"])
    try:
        with open(process_path, encoding="utf-8") as handle:
            process = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("process.json is unreadable: %s" % exc)
    missing = [k for k in ("layers", "copper_oz") if process.get(k) is None]
    if missing:
        raise ValueError("process declaration lacks %s (never guessed)" % missing)

    states = gates.gate_status(doc, project)
    not_ok = [(g["gate"], g["state"]) for g in states if g["state"] != "CONFIRMED"]
    if not_ok:
        raise ValueError("gates not all confirmed: %s. Sealing needs G1-G5 CONFIRMED (a dialogue "
                         "confirmation, not a technical pass)" % not_ok)
    g5 = [g for g in states if g["gate"] == "G5"][-1]

    if bool(bom) != bool(cpl):
        raise ValueError("BOM and position file must be given as a pair (designator sets are "
                         "cross-checked)")
    inputs = [("gerber", gerber_path)] + [(k, v) for k, v in (("bom", bom), ("cpl", cpl)) if v] \
             + [("extra", x) for x in (extra or [])]
    rels = {}
    for kind, path in inputs:
        full = path if os.path.isabs(path) else os.path.join(project, path)
        if not os.path.isfile(full):
            raise ValueError("file does not exist: %s" % full)
        rel = _rel_in_project(project, full)
        if rel is None:
            raise ValueError("%s is outside the project, so no confirmation can bind it; move it "
                             "into the project first" % full)
        if rel not in g5.get("bound", {}):
            raise ValueError("G5 did not bind %s - reconfirm G5 with this file (you must seal the "
                             "version the user actually reviewed)" % rel)
        if g5["bound"][rel] != _sha(full):
            raise ValueError("%s changed after G5 was confirmed - reconfirm it" % rel)
        rels[rel] = (kind, full)

    reports = {}
    guard_result = gerber.guard(rels[[r for r, (k, _p) in rels.items() if k == "gerber"][0]][1],
                                process)
    reports["gerber_guard.json"] = guard_result
    bom_code = 0
    if bom and cpl:
        board = None
        try:
            board = bomcpl.board_from_gerber(rels[[r for r, (k, _p) in rels.items() if k == "gerber"][0]][1])
        except bomcpl.Unsupported:
            board = None
        bom_result = bomcpl.run_checks(rels[[r for r, (k, _p) in rels.items() if k == "bom"][0]][1],
                                       rels[[r for r, (k, _p) in rels.items() if k == "cpl"][0]][1],
                                       board)
        reports["bom_cpl_guard.json"] = bom_result
        bom_code = bom_result["exit"]
    codes = (guard_result["exit"], bom_code)
    if any(code != 0 for code in codes):
        raise ValueError("re-check refused the seal: gerber_guard=%s bom_cpl_guard=%s (both must be 0)"
                         % codes)

    dest = os.path.join(project, "release", version)
    if os.path.exists(dest):
        raise ValueError("%s already exists - refusing to overwrite; use a new version" % dest)
    plan = {"dest": dest, "files": {r: k for r, (k, _p) in rels.items()},
            "reports": list(reports), "applied": False}
    if not apply:
        plan["hint"] = "preview only; call again with apply=true to write"
        return plan

    os.makedirs(os.path.join(dest, "reports"))
    files = {}
    for rel, (kind, full) in rels.items():
        target = os.path.join(dest, os.path.basename(rel))
        shutil.copy2(full, target)
        files[os.path.basename(rel)] = {"from": rel, "kind": kind,
                                       "bytes": os.path.getsize(target), "sha256": _sha(target)}
    shutil.copy2(process_path, os.path.join(dest, "process.json"))
    with open(os.path.join(dest, "workflow.snapshot.json"), "w", encoding="utf-8") as handle:
        json.dump(doc, handle, ensure_ascii=False, indent=2)
    for name, payload in reports.items():
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        with open(os.path.join(dest, "reports", name), "w", encoding="utf-8") as handle:
            handle.write(text)
        reports[name] = text
    manifest = {
        "version": version, "project": doc.get("project"),
        "sealed_at": datetime.now().isoformat(timespec="seconds"), "process": process,
        "files": files,
        "evidence": {n: {"sha256": hashlib.sha256(t.encode("utf-8")).hexdigest()}
                     for n, t in reports.items() if isinstance(t, str)},
        "gates": [{"gate": g["gate"], "name": g["name"], "state": g["state"],
                   "user_reply": g.get("user_reply"), "at": g.get("at"), "bound": g.get("bound")}
                  for g in states],
        "statement": "reviewed inside the declared scope and confirmed by the user in dialogue; "
                     "nothing was uploaded and no order was placed. This does not prove the circuit "
                     "works, that part numbers match the components, or that the board is ready for "
                     "volume - check the fab's DFM result before ordering.",
    }
    with open(os.path.join(dest, "manifest.json"), "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
    plan.update({"applied": True, "manifest": os.path.join(dest, "manifest.json"),
                 "file_count": len(files)})
    return plan
