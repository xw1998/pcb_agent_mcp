"""Three kinds of knowledge that are not rules about a board: a verified recipe, a
reusable layout, and a pitfall (a symptom, its cause and the fix).

`experience.py` stores *claims* that a measurement can contradict ("no via ring
under 0.10 mm"). This module stores *artefacts* that a person can apply again:

* **recipe** - a solution that was already made to work, with the numbers that
  made it work and the reading that proves it worked ("USB-C 16P: CC1/CC2 get
  5.1k to GND because the sink has to advertise 5 V/3 A; verified by ...").
* **layout** - a relative arrangement of parts, in millimetres from one anchor
  part, so a second board can place the same cluster without re-deriving it
  ("MCU cluster: HSE 2.3 mm from the pins it serves, load caps on the same side,
  USB connector on the board edge").

Three things are deliberately refused:

1. **A number with no source.** Every value in `parameters` has to be written as
   `{"value": .., "unit": .., "source": ..}`. A bare `4.7` is a guess wearing a
   number's clothes, and a guess that gets reused is worse than no library.
2. **A promotion without a person.** `add` only ever writes `candidate`.
   `verify(confirm=True, reason=...)` is the only way up, and it also needs at
   least one evidence item that names where the reading came from and what it
   read.
3. **A layout written onto a board.** `plan_layout` returns coordinates and the
   conflicts it found; it never edits a file. Placement stays a decision, and
   auto-placement is banned in this server on purpose.

Storage is one JSON file under `$PCB_AGENT_HOME` (default `~/.pcb_agent_mcp`),
next to `experience.json`, holding `{"schema": 1, "recipes": [...], "layouts":
[...]}`. Nothing ships as a seed: an empty library with a documented way to fill
it is honest, a seed full of undocumented numbers would not be. `uses` counts
every recall-by-id and every `use()` outcome, so "is this library actually used"
is a reading rather than a feeling.
"""
import json
import math as _math
import os
import time

FILENAME = "patterns.json"
SCHEMA = 1
KINDS = ("recipe", "layout", "pitfall")
STATUSES = ("candidate", "verified", "retired")
SIDES = ("top", "bottom")
OUTCOMES = ("worked", "failed")


def enabled():
    """`PCB_AGENT_PATTERNS=0|off` turns the module into named refusals."""
    return os.environ.get("PCB_AGENT_PATTERNS", "1") not in (
        "0", "false", "off", "no")


def home():
    got = os.environ.get("PCB_AGENT_HOME")
    if got:
        return got
    return os.path.join(os.path.expanduser("~"), ".pcb_agent_mcp")


def path():
    return os.path.join(home(), FILENAME)


# ------------------------------------------------------------------ storage
STORES = {"recipe": "recipes", "layout": "layouts", "pitfall": "pitfalls"}


def _read():
    empty = {"recipes": [], "layouts": [], "pitfalls": []}
    try:
        with open(path(), encoding="utf-8") as fh:
            blob = json.load(fh)
    except (OSError, ValueError):
        return empty
    if not isinstance(blob, dict):
        return empty
    out = {"recipes": [], "layouts": [], "pitfalls": []}
    for key, ident in (("recipes", "recipe"), ("layouts", "layout"),
                       ("pitfalls", "pitfall")):
        for entry in blob.get(key) or []:
            if isinstance(entry, dict):
                item = dict(entry)
                item.setdefault("kind", ident)
                out[key].append(item)
    return out


def _write(store):
    target = path()
    directory = os.path.dirname(target)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    tmp = target + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"schema": SCHEMA, "recipes": store["recipes"],
                   "layouts": store["layouts"],
                   "pitfalls": store.get("pitfalls") or []}, fh, ensure_ascii=False,
                  indent=1, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, target)


def library(kind=None, status=None):
    """Every entry, newest first. `kind` and `status` filter, both optional."""
    store = _read()
    rows = []
    for key in ("recipes", "layouts", "pitfalls"):
        rows.extend(store[key])
    rows.sort(key=lambda item: (item.get("updated_at") or "", item.get("id") or ""),
              reverse=True)
    if kind:
        rows = [r for r in rows if r.get("kind") == kind]
    if status:
        rows = [r for r in rows if r.get("status") == status]
    counts = {"total": len(rows)}
    for name in KINDS:
        counts["kind_" + name] = sum(1 for r in rows if r.get("kind") == name)
    for name in STATUSES:
        counts["status_" + name] = sum(1 for r in rows if r.get("status") == name)
    return {"entries": rows, "counts": counts, "store": path(),
            "enabled": enabled()}


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def _slug(text):
    keep = []
    for ch in str(text or ""):
        keep.append(ch.lower() if ch.isalnum() else "-")
    out = "".join(keep).strip("-")
    while "--" in out:
        out = out.replace("--", "-")
    return out[:40] or "part"


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


# -------------------------------------------------------------------- shape
def _check_parameters(parameters):
    """Every number carries its source. Returns the cleaned dict or raises."""
    if parameters is None:
        return {}
    if not isinstance(parameters, dict):
        raise ValueError("parameters must be an object of name -> {value, unit, "
                         "source}")
    clean = {}
    for name, item in parameters.items():
        if not isinstance(item, dict) or "value" not in item:
            raise ValueError(
                "parameter %r is not {value, unit, source}: a bare number cannot be "
                "reused later because nobody can tell where it came from" % name)
        if not item.get("source"):
            raise ValueError(
                "parameter %r has no source: a number with no source is a guess, "
                "and this library does not store guesses" % name)
        value = item.get("value")
        if not (_is_number(value) or isinstance(value, str)):
            raise ValueError("parameter %r must be a number or a string, not %r"
                             % (name, type(value).__name__))
        clean[str(name)] = {"value": value, "unit": item.get("unit"),
                            "source": str(item["source"])}
    return clean


def _check_evidence(evidence):
    if not evidence:
        return []
    if not isinstance(evidence, list):
        raise ValueError("evidence must be a list")
    clean = []
    for index, item in enumerate(evidence):
        if not isinstance(item, dict):
            raise ValueError("evidence[%d] must be an object" % index)
        where = item.get("from")
        reading = item.get("reading")
        if not where or not reading:
            raise ValueError(
                "evidence[%d] needs both `from` (a path or a command) and `reading` "
                "(what it returned): 'it works' is not evidence" % index)
        row = {"from": str(where), "reading": str(reading),
               "what": item.get("what"), "date": item.get("date")}
        # A path is checked for existence and the answer is carried with the
        # evidence. It is not a refusal: the reading may have been taken on
        # another machine, and saying "not here" is more honest than pretending
        # to have verified it.
        text = str(where)
        looks_like_path = ("/" in text or "\\" in text) and "://" not in text
        if looks_like_path:
            row["exists_here"] = os.path.exists(text)
        clean.append(row)
    return clean


def normalise(payload, kind):
    """Fill and validate an entry. Raises ValueError with the reason."""
    if kind not in KINDS:
        raise ValueError("kind must be one of %s" % ", ".join(KINDS))
    if not isinstance(payload, dict):
        raise ValueError("an entry must be an object")
    ident = (payload.get("id") or "").strip()
    if not ident:
        raise ValueError("id is required, e.g. pat.%s.usb-c-16p-cc" % kind)
    if not ident.startswith("pat."):
        raise ValueError("id must start with 'pat.' so a pattern is never mistaken "
                         "for an experience entry (experience ids start with 'exp.')")
    title = (payload.get("title") or "").strip()
    if len(title) < 6:
        raise ValueError("title is required and has to name the artefact, not 'x'")
    entry = {
        "id": ident,
        "kind": kind,
        "title": title,
        "problem": payload.get("problem"),
        "summary": payload.get("summary"),
        "applies_to": payload.get("applies_to") or {},
        "status": "candidate",
        "created_at": _now(),
        "updated_at": _now(),
        "verified_by": None,
        "evidence": _check_evidence(payload.get("evidence")),
        "uses": {"count": 0, "worked": 0, "failed": 0, "last_used_at": None,
                 "notes": []},
        "origin": payload.get("origin") or "local",
    }
    if kind == "pitfall":
        symptom = (payload.get("symptom") or "").strip()
        cause = (payload.get("cause") or "").strip()
        fix = (payload.get("fix") or "").strip()
        for name, value in (("symptom", symptom), ("cause", cause), ("fix", fix)):
            if len(value) < 8:
                raise ValueError(
                    "a pitfall needs %s in at least 8 characters: 'looks wrong' is "
                    "what this record exists to replace" % name)
        entry["symptom"] = symptom
        entry["cause"] = cause
        entry["fix"] = fix
        metric = payload.get("metric")
        if metric:
            from . import layout_quality as _lq
            if metric not in _lq.PERCENTILE_KEYS:
                raise ValueError(
                    "metric %r is not one board_layout_metrics reports (known: %s): a "
                    "pitfall tied to a reading nobody can take again is a story"
                    % (metric, ", ".join(_lq.PERCENTILE_KEYS)))
            entry["metric"] = metric
            threshold = payload.get("below_percentile")
            if threshold is not None and not _is_number(threshold):
                raise ValueError("below_percentile must be a number 0-100")
            entry["below_percentile"] = threshold
        entry["saw_on"] = payload.get("saw_on")
    elif kind == "recipe":
        procedure = payload.get("procedure") or []
        if not isinstance(procedure, list) or len(procedure) < 2:
            raise ValueError("a recipe needs a procedure of at least two steps; a "
                             "one-liner is a note, not a recipe")
        for index, step in enumerate(procedure):
            if not isinstance(step, str) or len(step.strip()) < 8:
                raise ValueError("procedure[%d] is too short to be repeatable" % index)
        entry["procedure"] = [str(s) for s in procedure]
        entry["parameters"] = _check_parameters(payload.get("parameters"))
    else:
        elements = payload.get("elements")
        if not isinstance(elements, list) or not elements:
            raise ValueError("a layout needs elements")
        anchors = [e for e in elements if isinstance(e, dict) and e.get("anchor")]
        if len(anchors) != 1:
            raise ValueError(
                "a layout needs exactly one element with anchor=true (the part the "
                "offsets are measured from); found %d" % len(anchors))
        clean = []
        for index, item in enumerate(elements):
            if not isinstance(item, dict):
                raise ValueError("elements[%d] must be an object" % index)
            role = (item.get("role") or "").strip()
            if not role:
                raise ValueError("elements[%d] needs a role, e.g. 'MCU' or "
                                 "'HSE crystal'" % index)
            for axis in ("dx_mm", "dy_mm"):
                if not _is_number(item.get(axis)):
                    raise ValueError(
                        "elements[%d] (%s) needs %s as a number: offsets are relative "
                        "to the anchor, and a missing offset is not zero" % (index, role, axis))
            rotation = item.get("rotation_deg")
            clean.append({
                "role": role,
                "ref": item.get("ref"),
                "dx_mm": float(item["dx_mm"]),
                "dy_mm": float(item["dy_mm"]),
                "rotation_deg": float(rotation) if _is_number(rotation) else 0.0,
                "side": item.get("side") if item.get("side") in SIDES else "top",
                "anchor": bool(item.get("anchor")),
                "must": item.get("must", True),
                "notes": item.get("notes"),
            })
        constraints = payload.get("constraints") or {}
        if not isinstance(constraints, dict):
            raise ValueError("constraints must be an object")
        min_gap = constraints.get("min_gap_mm")
        if min_gap is not None and not _is_number(min_gap):
            raise ValueError("constraints.min_gap_mm must be a number in mm")
        clean_constraints = {"min_gap_mm": (float(min_gap) if min_gap is not None
                                            else None),
                             "keepout_from_edge_mm": constraints.get(
                                 "keepout_from_edge_mm"),
                             "notes": constraints.get("notes")}
        entry["elements"] = clean
        entry["constraints"] = clean_constraints
        entry["class"] = payload.get("class") or payload.get("klass")
    return entry


def _find(store, ident):
    for key in ("recipes", "layouts", "pitfalls"):
        for entry in store[key]:
            if entry.get("id") == ident:
                return key, entry
    return None, None


def add(payload, kind):
    """Write a CANDIDATE. Same entry id is refused rather than overwritten."""
    if not enabled():
        raise ValueError("pattern library is off (PCB_AGENT_PATTERNS=0)")
    entry = normalise(payload, kind)
    store = _read()
    key, existing = _find(store, entry["id"])
    if existing is not None:
        raise ValueError("%s already exists with status %r; add a new id instead of "
                         "overwriting a record of something that was verified"
                         % (entry["id"], existing.get("status")))
    store[STORES[kind]].append(entry)
    _write(store)
    return entry


def verify(ident, confirm=False, reason=None, evidence=None):
    """candidate -> verified. Needs a person and at least one reading."""
    if not enabled():
        raise ValueError("pattern library is off (PCB_AGENT_PATTERNS=0)")
    if confirm is not True:
        raise ValueError("confirm=true is required: nothing here is promoted by a "
                         "tool on its own")
    if not reason or len(str(reason).strip()) < 8:
        raise ValueError("a reason of at least 8 characters is required: 'ok' does "
                         "not record why this is now trusted")
    store = _read()
    key, entry = _find(store, ident)
    if entry is None:
        raise ValueError("no pattern with id %r" % ident)
    if entry.get("status") == "verified":
        raise ValueError("%s is already verified" % ident)
    if entry.get("status") == "retired":
        raise ValueError("%s is retired; restore it under a new id instead of "
                         "silently resurrecting it" % ident)
    merged = list(entry.get("evidence") or [])
    merged.extend(_check_evidence(evidence))
    if not merged:
        raise ValueError("at least one evidence item is required: a verified recipe "
                         "with no reading behind it is indistinguishable from a "
                         "candidate someone liked")
    entry["evidence"] = merged
    entry["status"] = "verified"
    entry["verified_by"] = {"reason": str(reason), "at": _now()}
    entry["updated_at"] = _now()
    store[key] = [entry if e.get("id") == ident else e for e in store[key]]
    _write(store)
    return entry


def retire(ident, reason=None):
    store = _read()
    key, entry = _find(store, ident)
    if entry is None:
        raise ValueError("no pattern with id %r" % ident)
    if not reason:
        raise ValueError("a reason is required to retire an entry")
    entry["status"] = "retired"
    entry["retired_reason"] = str(reason)
    entry["updated_at"] = _now()
    store[key] = [entry if e.get("id") == ident else e for e in store[key]]
    _write(store)
    return entry


def get(ident, touch=False):
    """One entry by id. `touch` counts this as a use - only a caller that actually
    applies it may ask for that."""
    store = _read()
    key, entry = _find(store, ident)
    if entry is None:
        raise ValueError("no pattern with id %r" % ident)
    if touch:
        uses = entry.setdefault("uses", {"count": 0, "worked": 0, "failed": 0,
                                         "last_used_at": None, "notes": []})
        uses["count"] = int(uses.get("count") or 0) + 1
        uses["last_used_at"] = _now()
        store[key] = [entry if e.get("id") == ident else e for e in store[key]]
        _write(store)
    return entry


def use(ident, outcome, note=None):
    """Record what happened when the artefact was applied. `failed` is worth as
    much as `worked`: it is the only thing that stops a recipe being applied
    forever on the strength of one good day."""
    if outcome not in OUTCOMES:
        raise ValueError("outcome must be one of %s" % ", ".join(OUTCOMES))
    store = _read()
    key, entry = _find(store, ident)
    if entry is None:
        raise ValueError("no pattern with id %r" % ident)
    uses = entry.setdefault("uses", {"count": 0, "worked": 0, "failed": 0,
                                     "last_used_at": None, "notes": []})
    uses["count"] = int(uses.get("count") or 0) + 1
    uses[outcome] = int(uses.get(outcome) or 0) + 1
    uses["last_used_at"] = _now()
    if note:
        uses.setdefault("notes", []).append({"at": _now(), "outcome": outcome,
                                             "note": str(note)})
    entry["updated_at"] = _now()
    store[key] = [entry if e.get("id") == ident else e for e in store[key]]
    _write(store)
    return entry


# --------------------------------------------------------- from a real board
def from_board(board, anchor_ref, refs=None, radius_mm=None, ident=None,
               title=None, klass=None):
    """Turn a cluster that already exists on a board into a LAYOUT payload.

    The offsets come from the parsed board, not from somebobdy reading a screenshot:
    `dx = ref.x - anchor.x` in the canonical view (mm, Y up). That is a fact about
    this one board, so the payload is a CANDIDATE by construction - it still has to
    go through `pattern_verify` with a reading before anything can plan from it.

    Returns the payload plus what was left out and why. A part with no position
    is skipped by name rather than placed at (0, 0), because a missing coordinate
    that silently becomes zero is how a template quietly turns into a pile.
    """
    footprints = list(getattr(board, "footprints", []) or [])
    by_ref = {}
    for fp in footprints:
        ref = str(fp.get("ref") or "").strip()
        if ref:
            by_ref[ref] = fp
    anchor = by_ref.get(str(anchor_ref or "").strip())
    if anchor is None:
        raise ValueError("no footprint with reference %r on this board (it carries: "
                         "%s)" % (anchor_ref,
                                   ", ".join(sorted(by_ref)[:12]) or "none"))
    if anchor.get("x") is None or anchor.get("y") is None:
        raise ValueError("the anchor %r has no position in the file" % anchor_ref)
    ax, ay = float(anchor["x"]), float(anchor["y"])
    wanted = [str(r).strip() for r in (refs or []) if str(r).strip()]
    missing = [r for r in wanted if r not in by_ref]
    elements, skipped = [], []
    for fp in footprints:
        ref = str(fp.get("ref") or "").strip()
        if not ref or (wanted and ref not in wanted):
            continue
        if fp.get("x") is None or fp.get("y") is None:
            skipped.append({"ref": ref, "why": "no position in the file"})
            continue
        dx = float(fp["x"]) - ax
        dy = float(fp["y"]) - ay
        if radius_mm is not None and _math.hypot(dx, dy) > float(radius_mm):
            continue
        layer = str(fp.get("layer") or "")
        side = "bottom" if layer.startswith("B") else "top"
        elements.append({
            "role": (fp.get("value") or ref),
            "ref": ref,
            "dx_mm": round(dx, 4),
            "dy_mm": round(dy, 4),
            "rotation_deg": float(fp.get("rotation") or 0.0),
            "side": side,
            "anchor": ref == str(anchor_ref).strip(),
        })
    if len(elements) < 2:
        raise ValueError("the cluster has %d element(s): a layout needs the anchor and "
                         "at least one more part (refs=%s)"
                         % (len(elements), ", ".join(wanted) or "radius only"))
    name = ident or ("pat.layout.%s-cluster" % _slug(anchor_ref))
    payload = {
        "id": name,
        "title": title or ("%s cluster taken from %s"
                           % (anchor_ref, os.path.basename(str(
                               (getattr(board, "source", None) or {}).get("path")
                               or "a board")))),
        "class": klass or "cluster",
        "applies_to": {"anchor_ref": str(anchor_ref)},
        "elements": elements,
        "status": "candidate",
        "constraints": {"min_gap_mm": None,
                        "notes": "min_gap_mm was not measured; set it before "
                                 "trusting the plan's collision report"},
    }
    return {"entry": payload, "skipped": skipped, "missing": missing,
            "coordinate_system": "canonical (mm, Y up)",
            "read_from": (getattr(board, "source", None) or {}).get("path"),
            "status": "candidate",
            "why_a_candidate": ("these offsets are a fact about one board; they "
                                "become a template only after pattern_verify with a "
                                "reading behind it")}


# ----------------------------------------------------------------- geometry
def rotate(dx, dy, degrees):
    """Rotate an offset about the origin. Positive degrees turn the same way
    KiCad's positive footprint angle turns on screen (x right, y down in the
    file's own coordinates). One definition, used by every plan."""
    theta = _math.radians(float(degrees or 0.0))
    cos, sin = _math.cos(theta), _math.sin(theta)
    return (dx * cos - dy * sin, dx * sin + dy * cos)


def plan_layout(ident, x_mm=None, y_mm=None, rotation_deg=0.0, mirror=False):
    """Where every element would sit if this layout were anchored at (x, y).

    Returns coordinates, not a board. Nothing is written; the caller decides.
    """
    entry = get(ident)
    if entry.get("kind") != "layout":
        raise ValueError("%s is a %s, not a layout" % (ident, entry.get("kind")))
    if entry.get("status") != "verified":
        raise ValueError(
            "%s is %r: a candidate layout has not been checked against a board yet, "
            "so planning from it would produce coordinates nobody vouched for"
            % (ident, entry.get("status")))
    # Counted as a use only after the refusal above: a plan that was rejected
    # never placed anything, and counting it would make "used N times" a count of
    # attempts rather than of applications.
    entry = get(ident, touch=True)
    if not _is_number(x_mm) or not _is_number(y_mm):
        raise ValueError("x_mm and y_mm are required: the plan needs the place the "
                         "anchor really goes")
    placed = []
    for element in entry["elements"]:
        dx, dy = element["dx_mm"], element["dy_mm"]
        if mirror:
            dx = -dx
        rx, ry = rotate(dx, dy, rotation_deg)
        side = element.get("side") or "top"
        if mirror:
            side = "bottom" if side == "top" else "top"
        placed.append({
            "role": element["role"],
            "ref": element.get("ref"),
            "x_mm": round(float(x_mm) + rx, 4),
            "y_mm": round(float(y_mm) + ry, 4),
            "rotation_deg": round((float(element.get("rotation_deg") or 0.0)
                                   + float(rotation_deg or 0.0)) % 360.0, 4),
            "side": side,
            "anchor": bool(element.get("anchor")),
            "must": element.get("must", True),
            "notes": element.get("notes"),
        })
    return {"id": ident, "title": entry.get("title"), "elements": placed,
            "constraints": entry.get("constraints") or {},
            "coordinate_system": "canonical (mm, Y up)",
            "y_note": ("a KiCad file stores Y downward, so a write path that takes "
                       "file coordinates has to negate Y; this answer never converts "
                       "silently"),
            "writes_nothing": True}


def status():
    """Is this library actually alive: how many entries, how many verified, how
    many were ever applied, and which verified ones never were."""
    store = _read()
    rows = store["recipes"] + store["layouts"] + store.get("pitfalls", [])
    by_status = {name: 0 for name in STATUSES}
    by_kind = {name: 0 for name in KINDS}
    uses = worked = failed = 0
    never_used = []
    for entry in rows:
        by_status[entry.get("status")] = by_status.get(entry.get("status"), 0) + 1
        by_kind[entry.get("kind")] = by_kind.get(entry.get("kind"), 0) + 1
        used = entry.get("uses") or {}
        count = int(used.get("count") or 0)
        uses += count
        worked += int(used.get("worked") or 0)
        failed += int(used.get("failed") or 0)
        if entry.get("status") == "verified" and count == 0:
            never_used.append(entry.get("id"))
    return {"store": path(), "enabled": enabled(), "total": len(rows),
            "by_status": by_status, "by_kind": by_kind,
            "uses": {"count": uses, "worked": worked, "failed": failed},
            "verified_never_used": never_used,
            "message": ("the library is empty: nothing has been written down yet, "
                        "so there is nothing to reuse" if not rows
                        else ("%d verified entries have never been applied: a library "
                              "nobody uses is a document" % len(never_used))
                        if never_used
                        else "every verified entry has been used at least once")}
