"""G1-G5 dialogue gates: what the user confirmed, on which file version, and when it went stale.

Ported from jlceda-pcb-ai-workflow (MIT) ``scripts/workflow.py``. Three rules are the whole
value and none of them is negotiable:

* a confirmation must carry the **user's own words** - the agent never fills them in;
* a confirmation binds the sha256 of the files it was given for, so editing a file silently
  invalidates the confirmation instead of leaving a stale "approved" behind;
* gates run in order - a broken earlier gate invalidates the later ones.

``CONFIRMED`` means "the user said yes to this version", never "the board is technically
correct". :mod:`seal` refuses to release unless all five are CONFIRMED *and* still fresh.
"""
import hashlib
import json
import os
from datetime import datetime

GATES = [("G1", "schematic"), ("G2", "library/footprint"), ("G3", "placement"),
         ("G4", "PCB"), ("G5", "export release")]
CHOICES = {"1": "confirmed", "2": "revise and re-review", "3": "paused"}
WORKFLOW_NAME = "workflow.json"


def _now():
    return datetime.now().isoformat(timespec="seconds")


def sha256_file(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def workflow_path(project):
    return os.path.join(project, WORKFLOW_NAME)


def load(project):
    path = workflow_path(project)
    if not os.path.exists(path):
        raise FileNotFoundError("%s does not exist - run the gate tool with action=init first" % path)
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def save(project, doc):
    with open(workflow_path(project), "w", encoding="utf-8") as handle:
        json.dump(doc, handle, ensure_ascii=False, indent=2)


def template(name):
    return {"schema_version": 1, "mode": "dialogue", "created_at": _now(), "project": name,
            "files": {"process": "process.json"},
            "confirmations": [],
            "note": "dialogue mode: a confirmation records the user's own words and the sha256 of "
                    "the files it was given for. It is not a technical pass and not a signature."}


def init(project, name=None, apply=False):
    if not os.path.isdir(project):
        raise ValueError("project directory does not exist: %s" % project)
    path = workflow_path(project)
    if os.path.exists(path):
        raise ValueError("%s already exists - refusing to overwrite" % path)
    process_path = os.path.join(project, "process.json")
    from . import api
    process_template = api.process_template()
    out = {"workflow": path, "process": process_path,
           "process_existed": os.path.exists(process_path)}
    if not apply:
        out["applied"] = False
        out["hint"] = "preview only; call again with apply=true to write"
        return out
    if not os.path.exists(process_path):
        with open(process_path, "w", encoding="utf-8") as handle:
            json.dump(process_template, handle, ensure_ascii=False, indent=2)
    save(project, template(name or os.path.basename(os.path.normpath(project))))
    out["applied"] = True
    out["next"] = "fill in process.json (layers and copper_oz are required and are never defaulted)"
    return out


def bound_state(confirmation, project):
    changed, missing = [], []
    for rel, digest in confirmation.get("bound", {}).items():
        path = os.path.join(project, rel)
        if not os.path.exists(path):
            missing.append(rel)
        elif sha256_file(path) != digest:
            changed.append(rel)
    if missing:
        return "MISSING", missing
    return ("STALE", changed) if changed else ("OK", [])


def gate_status(doc, project):
    out, previous_ok = [], True
    for gid, name in GATES:
        confirmations = [c for c in doc["confirmations"] if c["gate"] == gid and c["choice"] == "1"]
        if not confirmations:
            out.append({"gate": gid, "name": name, "state": "PENDING", "message": "not confirmed yet"})
            previous_ok = False
            continue
        confirmation = confirmations[-1]
        state, why = bound_state(confirmation, project)
        if state != "OK":
            out.append({"gate": gid, "name": name, "state": "STALE",
                        "message": "bound file changed or missing: %s - reconfirm" % (why,),
                        "at": confirmation.get("at")})
            previous_ok = False
        elif not previous_ok:
            out.append({"gate": gid, "name": name, "state": "STALE",
                        "message": "an earlier gate went stale - reconfirm this one too",
                        "at": confirmation.get("at")})
        else:
            out.append({"gate": gid, "name": name, "state": "CONFIRMED",
                        "message": "%s | user said: %r" % (confirmation["at"], confirmation["user_reply"]),
                        "at": confirmation["at"], "user_reply": confirmation["user_reply"],
                        "bound": confirmation["bound"]})
    return out


def status(project):
    doc = load(project)
    process_path = os.path.join(project, doc["files"]["process"])
    process_state = "missing"
    if os.path.exists(process_path):
        try:
            with open(process_path, encoding="utf-8") as handle:
                process = json.load(handle)
            missing = [k for k in ("layers", "copper_oz") if process.get(k) is None]
            process_state = "complete" if not missing else "incomplete (missing %s; the process is " \
                                                        "never guessed)" % missing
        except json.JSONDecodeError:
            process_state = "process.json is not valid JSON"
    gates = gate_status(doc, project)
    return {"project": doc.get("project"), "mode": doc.get("mode"), "process": process_state,
            "gates": gates,
            "ready_to_seal": all(g["state"] == "CONFIRMED" for g in gates),
            "caveat": "CONFIRMED means the user accepted that version, not that the board is correct"}


def confirm(project, gate, choice, user_reply, bind=None, note=None):
    doc = load(project)
    ids = [gid for gid, _name in GATES]
    if gate not in ids:
        raise ValueError("unknown gate %r; choose from %s" % (gate, ids))
    if not (user_reply or "").strip():
        raise ValueError("user_reply is mandatory: it must be the user's own words. The agent must "
                         "never answer a gate on the user's behalf")
    if str(choice) not in CHOICES:
        raise ValueError("choice must be 1 (pass) / 2 (revise and re-review) / 3 (pause)")
    choice = str(choice)
    states = {g["gate"]: g["state"] for g in gate_status(doc, project)}
    if choice == "1":
        index = ids.index(gate)
        if index > 0 and states[ids[index - 1]] != "CONFIRMED":
            raise ValueError("previous gate %s is %s - gates run in order" % (ids[index - 1], states[ids[index - 1]]))
        if not bind:
            raise ValueError("a pass (choice=1) must bind at least one file (bind=[...]) so that editing "
                             "it later invalidates this confirmation")
    bound = {}
    for rel in bind or []:
        path = os.path.join(project, rel)
        if not os.path.isfile(path):
            raise ValueError("bound file does not exist: %s" % path)
        bound[rel.replace(os.sep, "/")] = sha256_file(path)
    record = {"gate": gate, "choice": choice, "user_reply": user_reply, "note": note or "",
              "bound": bound, "at": _now()}
    doc["confirmations"].append(record)
    save(project, doc)
    return {"gate": gate, "choice": CHOICES[choice], "bound": bound, "at": record["at"],
            "recorded": True}
