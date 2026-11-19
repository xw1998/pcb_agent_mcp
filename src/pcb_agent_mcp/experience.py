"""What this server has learned from boards it did not draw, and how it refuses
to turn that into a guess.

The design is `docs/02` §B, written before there was any code; `docs/94` is what
changed when it became code. Two chains live here and they are not the same
thing:

* **hard** - a rule with a failure side that can be *measured on a board*
  ("no via has an annular ring below 0.10 mm", "a drilled pad is on every copper
  layer"). A violation is a defect or a defect-in-waiting, and the check can
  fail, which is the only reason it is allowed to exist.
* **soft** - a habit the good boards share ("power traces are wider than signal
  traces", "one via geometry per board"). It is evidence about what people do,
  not a rule about what is wrong. Reporting it never rewrites anything.

The one red line, kept from the design and enforced in code:

    a `candidate` never becomes `active` on its own.

A candidate carries an observation someone made; promoting it needs
`confirm=True` *and* a human-supplied reason. `experience_import` therefore
merges candidates only - an imported file cannot promote anything either.

Storage is one JSON file under `$PCB_AGENT_HOME` (default `~/.pcb_agent_mcp`),
holding **only** entries learned at runtime. The seed that ships with the
package (`data/experience_seed.json`) is read-only: it is evidence we measured
once and checked in, and a local edit to it would make the shipped numbers and
the running numbers disagree. `PCB_AGENT_EXPERIENCE=0|off` turns every tool in
this module into a named refusal instead of a silent empty list.

Every entry's claim about the corpus is a number with a denominator. When a
number has not been measured it is `null` and says so; nothing here is a
folk rule dressed as a measurement.
"""
import json
import math as _math
import os
import time

from . import datafiles as _datafiles

FILENAME = "experience.json"
# The seed ships with the repository, not with the module: `data/` is the one
# directory every table in this package lives in (creepage_tables.json,
# ipc2152_tables.json, ...). `datafiles.resolve` finds it in a git clone, an
# editable install and a wheel alike, and a path it could not find is still a
# *path* here - `_read_seed` reports the miss instead of pretending the library
# is empty.
SEED = _datafiles.resolve("experience_seed.json")
SCHEMA = 1
KINDS = ("hard", "soft")
STATUSES = ("candidate", "active", "retired")
ORIGINS = ("seed", "local", "imported")

#: Every metric this module can actually evaluate. An entry whose trigger names
#: anything else is reported as `not_checkable_here` rather than "passed": a
#: check that cannot run must never look like a pass.
METRICS = (
    "tracks_off_45_grid",
    "via_annular_ring_below",
    "distinct_via_geometries",
    "power_wider_than_signal",
    "distinct_track_widths",
    "through_hole_pads_single_layer",
    "pad_placement_verified",
)

#: How much below the floor a ring has to be before it is below the floor, in mm.
#: One micron: three orders of magnitude under any fabrication tolerance and two
#: orders over the error of a double holding 0.1.
RING_EPS_MM = 1e-6

#: Net names counted as power. Substring match, upper-cased, and declared here
#: rather than guessed per board: the classification is an assumption of the
#: `power_wider_than_signal` metric and is reported with every answer.
POWER_NET_HINTS = ("VCC", "VDD", "VIN", "VBUS", "VBAT", "GND", "VSS", "+5", "+3",
                   "+12", "3V3", "5V", "12V", "VCCIO", "AVDD", "DVDD", "PWR",
                   "V+", "V-")


# --------------------------------------------------------------------- switch
def enabled():
    """`PCB_AGENT_EXPERIENCE` unset means on: recall is read-only by nature.

    The switch exists because a library that cannot be turned off is not a
    library, it is a policy. What stays off by default is *acting* - no existing
    tool consults this module, so nothing here can change an answer.
    """
    return os.environ.get("PCB_AGENT_EXPERIENCE", "1") not in (
        "0", "false", "off", "no")


def home():
    got = os.environ.get("PCB_AGENT_HOME")
    if got:
        return got
    return os.path.join(os.path.expanduser("~"), ".pcb_agent_mcp")


def path():
    return os.path.join(home(), FILENAME)


# ------------------------------------------------------------------ storage
def _read_seed(diag=None):
    """Seed entries, and - when the file is not there - the reason, in `diag`.

    A missing seed used to return `[]`, which made `experience_status` read as
    "the shipped library is empty" when the truth was "the file is not on this
    machine". Those are different answers and only one of them is true, so the
    miss now travels back with the entries. The entry list is unchanged: a
    caller that passes no `diag` sees exactly what it saw before.
    """
    try:
        with open(SEED, encoding="utf-8") as fh:
            blob = json.load(fh)
    except (OSError, ValueError) as exc:
        if isinstance(diag, dict):
            diag["seed_found"] = False
            diag["seed_note"] = ("the seed file that ships with the package was "
                                 "not readable at %s (%s: %s); the entries "
                                 "below are local ones only"
                                 % (SEED, type(exc).__name__, exc))
        return []
    if isinstance(diag, dict):
        diag["seed_found"] = True
    entries = []
    for entry in blob.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        item = dict(entry)
        item["origin"] = "seed"
        entries.append(item)
    return entries


def _read_blob():
    try:
        with open(path(), encoding="utf-8") as fh:
            blob = json.load(fh)
    except (OSError, ValueError):
        return {}
    return blob if isinstance(blob, dict) else {}


def _read_local():
    out = []
    for entry in _read_blob().get("entries") or []:
        if isinstance(entry, dict):
            item = dict(entry)
            item.setdefault("origin", "local")
            out.append(item)
    return out


def _read_observations():
    """Boards that were measured against the library, one row each.

    Separate from `entries` on purpose: an observation is evidence that a board
    was looked at, never a rule. It is what lets the corpus grow without letting
    anything change an answer - the red line still runs through `entries`.
    """
    out = []
    for row in _read_blob().get("observations") or []:
        if isinstance(row, dict) and row.get("metric"):
            out.append(row)
    return out


def record_observations(rows):
    """Append measurement rows. Returns how many were stored."""
    rows = [dict(row) for row in (rows or []) if row.get("metric")]
    if not rows:
        return 0
    _write_local(_read_local(), _read_observations() + rows)
    return len(rows)


def _write_local(entries, observations=None):
    target = path()
    directory = os.path.dirname(target)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    if observations is None:
        observations = _read_observations()
    tmp = target + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"schema": SCHEMA, "entries": entries,
                   "observations": observations}, fh,
                  ensure_ascii=False, indent=1, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, target)


def library():
    """Seed entries plus local ones. A local id shadows the seed id of the same
    name (that is how a shipped rule gets retired locally), and the shadowing is
    reported rather than hidden."""
    by_id = {}
    diag = {}
    for entry in _read_seed(diag):
        by_id[entry["id"]] = entry
    shadowed = []
    for entry in _read_local():
        if entry["id"] in by_id:
            shadowed.append(entry["id"])
        by_id[entry["id"]] = entry
    out = {"entries": [by_id[k] for k in sorted(by_id)],
           "counts": _counts(by_id.values()),
           "seed": SEED, "store": path(), "enabled": enabled(),
           "shadowed_seed_ids": sorted(shadowed)}
    # Only the miss changes the shape of the answer: a healthy install returns
    # the same keys it always did, so nothing downstream has to learn a new one.
    if diag.get("seed_found") is False:
        out["seed_found"] = False
        out["seed_note"] = diag["seed_note"]
    return out


def _counts(entries):
    out = {"total": 0}
    for kind in KINDS:
        out["kind_" + kind] = 0
    for status in STATUSES:
        out["status_" + status] = 0
    for entry in entries:
        out["total"] += 1
        if entry.get("kind") in KINDS:
            out["kind_" + entry["kind"]] += 1
        if entry.get("status") in STATUSES:
            out["status_" + entry["status"]] += 1
    return out


# --------------------------------------------------------------------- shape
def normalise(entry, origin="local"):
    """Fill an entry's required shape, refusing anything that cannot be checked.

    The refusals here are the reason a candidate is worth writing down: an entry
    with no trigger is an opinion, and an opinion cannot be promoted later.
    """
    if not isinstance(entry, dict):
        raise ValueError("an experience entry must be an object")
    out = {}
    ident = str(entry.get("id") or "").strip()
    if not ident:
        raise ValueError("an experience entry needs an id")
    out["id"] = ident
    kind = str(entry.get("kind") or "").strip()
    if kind not in KINDS:
        raise ValueError("kind must be one of %s (got %r)" % (", ".join(KINDS), kind))
    out["kind"] = kind
    status = str(entry.get("status") or "candidate").strip()
    if status not in STATUSES:
        raise ValueError("status must be one of %s (got %r)"
                         % (", ".join(STATUSES), status))
    out["status"] = status
    trigger = entry.get("trigger")
    if not isinstance(trigger, dict) or not trigger.get("board_metric"):
        raise ValueError("an experience entry needs a trigger with a board_metric")
    metric = str(trigger["board_metric"])
    if metric not in METRICS:
        raise ValueError("unknown board_metric %r (this module can evaluate: %s)"
                         % (metric, ", ".join(METRICS)))
    out["trigger"] = dict(trigger)
    out["trigger"]["board_metric"] = metric
    out["scope"] = str(entry.get("scope") or "general")
    out["claim"] = str(entry.get("claim") or "").strip()
    out["action"] = str(entry.get("action") or "").strip()
    if not out["claim"]:
        raise ValueError("an experience entry needs a claim")
    evidence = entry.get("evidence")
    if not isinstance(evidence, dict):
        evidence = {}
    out["evidence"] = {
        "hits": _int_or_none(evidence.get("hits")),
        "violated": _int_or_none(evidence.get("violated")),
        "sources": [str(s) for s in (evidence.get("sources") or [])],
    }
    out["confidence"] = str(entry.get("confidence") or "unstated")
    out["owner"] = str(entry.get("owner") or "")
    out["origin"] = origin
    out["updated_at"] = entry.get("updated_at") or _now()
    if entry.get("promoted_by"):
        out["promoted_by"] = str(entry["promoted_by"])
    return out


def _int_or_none(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ------------------------------------------------------------------ recall
def recall(scope=None, kind=None, status="active", query=None, limit=50):
    """Entries that apply. `status=None` means every status.

    A candidate is *not* recalled by default: the whole point of the two-step is
    that an observation only starts shaping work after someone confirmed it.
    """
    lib = library()
    out = []
    for entry in lib["entries"]:
        if status is not None and entry.get("status") != status:
            continue
        if kind is not None and entry.get("kind") != kind:
            continue
        if scope:
            got = str(entry.get("scope") or "")
            if not (got == scope or got.startswith(scope + ".") or scope in got):
                continue
        if query:
            hay = " ".join(str(entry.get(k) or "")
                           for k in ("id", "claim", "action", "scope"))
            if query.lower() not in hay.lower():
                continue
        out.append(entry)
    out.sort(key=lambda e: (e.get("kind") != "hard", e.get("id")))
    return {"entries": out[:limit], "matched": len(out), "library": lib["counts"],
            "status_filter": status, "store": lib["store"],
            "seed": lib["seed"]}


# ------------------------------------------------------------------ report
def _copper_layers(board):
    names = [str(s.get("name")) for s in (board.stackup or [])
             if str(s.get("type", "copper")) == "copper" and s.get("name")]
    if names:
        return names
    seen = []
    for track in board.tracks or []:
        name = track.get("layer")
        if name and name not in seen:
            seen.append(name)
    return seen


def _is_power(net):
    up = str(net or "").upper()
    return any(hint in up for hint in POWER_NET_HINTS)


def observe(board, metric, trigger=None):
    """Measure one metric on `board`. Returns (observed, note).

    `observed` is a number where the metric is a number, a bool where it is a
    yes/no, and None when this board cannot answer - which is different from
    zero and is reported as such.
    """
    trigger = trigger or {}
    if metric == "tracks_off_45_grid":
        tol = float(trigger.get("tolerance_deg", 0.5))
        tracks = [t for t in (board.tracks or [])
                  if t.get("start") and t.get("end")]
        if not tracks:
            return None, "the board carries no tracks"
        off, worst, zero = 0, 0.0, 0
        devs = []
        for track in tracks:
            (x1, y1), (x2, y2) = track["start"], track["end"]
            dx, dy = x2 - x1, y2 - y1
            if abs(dx) < 1e-12 and abs(dy) < 1e-12:
                zero += 1
                continue
            # 180 degrees is the period of an undirected segment, and the
            # subtraction - not a modulo - is what keeps 179.9 deg from being
            # counted as 179.9 deg away from 0 deg.
            angle = _math.degrees(_math.atan2(dy, dx)) % 180.0
            dev = angle - round(angle / 45.0) * 45.0
            if abs(dev) > tol:
                off += 1
                devs.append(abs(dev))
                worst = max(worst, abs(dev))
        devs.sort()
        return ({"tracks": len(tracks), "off_grid": off, "zero_length": zero,
                 "worst_deviation_deg": round(worst, 4),
                 "off_grid_median_deg": round(
                     devs[len(devs) // 2], 4) if devs else None,
                 "off_grid_p90_deg": round(
                     devs[min(len(devs) - 1, int(0.9 * len(devs)))], 4)
                 if devs else None},
                ("a segment is on the 45 degree grid when its angle is within %s "
                 "deg of a multiple of 45" % tol))
    if metric == "via_annular_ring_below":
        floor = float(trigger.get("min_mm", 0.1))
        vias = [v for v in (board.vias or [])
                if v.get("diameter") is not None and v.get("drill") is not None]
        if not vias:
            return None, "the board carries no vias with both a diameter and a drill"
        # 0.1 mm has no exact binary representation, so (0.5 - 0.3) / 2 comes out
        # 0.09999999999999998. Comparing that against 0.1 reports 1497 vias on one
        # real board as too thin - which is a property of floats, not of the board.
        # A ring counts as below the floor only when it is below by more than the
        # precision the answer is reported at.
        bad = [(v.get("uuid"), round((v["diameter"] - v["drill"]) / 2.0, 4))
               for v in vias
               if (v["diameter"] - v["drill"]) / 2.0 < floor - RING_EPS_MM]
        return {"vias": len(vias), "below": len(bad), "floor_mm": floor,
                "worst": (min(b[1] for b in bad) if bad else None),
                "examples": bad[:10]}, "ring = (diameter - drill) / 2"
    if metric == "distinct_via_geometries":
        seen = {}
        for via in board.vias or []:
            if via.get("diameter") is None or via.get("drill") is None:
                continue
            key = (round(via["diameter"], 3), round(via["drill"], 3))
            seen[key] = seen.get(key, 0) + 1
        if not seen:
            return None, "the board carries no vias"
        top = sorted(seen.items(), key=lambda kv: -kv[1])
        return {"distinct": len(seen),
                "most_common": [[list(k), v] for k, v in top[:5]]}, \
            ("counted as (diameter, drill) pairs rounded to 0.001 mm: the same "
             "24 mil via is written 0.6096 mm in one file and 0.61 mm in another, "
             "and a 0.0004 mm difference is a display rounding, not a geometry")
    if metric == "power_wider_than_signal":
        widths = {}
        for track in board.tracks or []:
            if track.get("width") is None:
                continue
            widths.setdefault(track.get("net"), []).append(track["width"])
        power, signal = [], []
        for net, values in widths.items():
            (power if _is_power(net) else signal).extend(values)
        if not power or not signal:
            return None, ("this board has no net that matches the power-net "
                          "patterns, or no signal nets at all")
        power.sort()
        signal.sort()
        pm, sm = power[len(power) // 2], signal[len(signal) // 2]
        return {"power_median_mm": round(pm, 4),
                "signal_median_mm": round(sm, 4),
                "power_tracks": len(power), "signal_tracks": len(signal),
                "wider": pm > sm}, \
            ("power nets are recognised by substring: %s"
             % ", ".join(POWER_NET_HINTS))
    if metric == "distinct_track_widths":
        seen = {}
        for track in board.tracks or []:
            if track.get("width") is None:
                continue
            key = round(track["width"], 4)
            seen[key] = seen.get(key, 0) + 1
        if not seen:
            return None, "the board carries no tracks"
        return {"distinct": len(seen),
                "widths": sorted(seen)[:20]}, "widths rounded to 1e-4 mm"
    if metric == "through_hole_pads_single_layer":
        copper = set(_copper_layers(board))
        pads = through = bad = 0
        for fp in board.footprints or []:
            for pad in fp.get("pads") or []:
                # The two adapters state a hole differently - the LCEDA-Pro reader
                # sets `hole_kind`, the KiCad reader sets `hole_mm` plus `smd` - so
                # the rule is written on whichever of the two the pad carries. A
                # metric that only spoke one adapter's dialect would call every
                # KiCad board `not_checkable_here`, which looks like "no data" when
                # it is really "this check has a blind half".
                if not (pad.get("hole_kind")
                        or (pad.get("hole_mm") is not None
                            and pad.get("smd") is not True)):
                    continue
                through += 1
                layers = [str(l) for l in (pad.get("layers") or [])]
                spans = ("*.Cu" in layers) or ("F&B.Cu" in layers) or \
                        (len([l for l in layers if l in copper]) > 1)
                if not spans:
                    bad += 1
                pads += 1
        if not through:
            return None, "the board carries no drilled pads"
        return {"drilled_pads": through, "single_layer": bad}, \
            "a drilled pad spans every copper layer, or its layer list says so"
    if metric == "pad_placement_verified":
        transform = (board.source or {}).get("pad_transform") or {}
        if not transform:
            return True, ("no declared transform: positions come straight from the "
                          "file (KiCad), so there is nothing to verify")
        return transform.get("verified_by_app") is True, \
            "the adapter's own statement about whether the app confirmed it"
    return None, "this module has no implementation for metric %r" % metric


def _holds(entry, observed):
    """Does the claim hold on this board? True / False, or None when the metric
    cannot be evaluated here. One place decides, so no verdict can be born from
    a comparison written twice."""
    metric = entry["trigger"]["board_metric"]
    if observed is None:
        return None
    if metric == "tracks_off_45_grid":
        if not isinstance(observed, dict):
            return None
        share = observed["off_grid"] / float(observed["tracks"] or 1)
        limit = float(entry["trigger"].get("max_off_grid_share", 0.0))
        return share <= limit
    if metric == "via_annular_ring_below":
        return observed["below"] == 0
    if metric == "distinct_via_geometries":
        return observed["distinct"] <= int(entry["trigger"].get("max_distinct", 1))
    if metric == "power_wider_than_signal":
        return bool(observed["wider"])
    if metric == "distinct_track_widths":
        limit = entry["trigger"].get("max_distinct")
        if limit is None:
            return True                 # nothing to compare against: it is a fact
        return observed["distinct"] <= int(limit)
    if metric == "through_hole_pads_single_layer":
        return observed["single_layer"] == 0
    if metric == "pad_placement_verified":
        return observed is True
    return None

def _verdict(entry, observed):
    """Turn a measurement into a verdict. Never guesses, and never lets a habit
    borrow the word a defect uses.

    `passes` / `violated` belong to `hard`: a violated hard rule is a finding.
    `outside_habit` belongs to `soft`: it says this board does something the good
    boards mostly do not, which is a question, not a defect. `verified` /
    `unverified` belong to whether the reader could certify the input at all -
    a fact about the reader, deliberately outside that polarity.
    """
    metric = entry["trigger"]["board_metric"]
    if observed is None:
        return "not_checkable_here"
    if metric == "pad_placement_verified":
        return "verified" if observed is True else "unverified"
    holds = _holds(entry, observed)
    if holds is None:
        return "not_checkable_here"
    if holds:
        return "passes"
    return "violated" if entry.get("kind") == "hard" else "outside_habit"

def report(board, scope=None, status="active"):
    """Check a board against the active library. Reads the board; writes nothing.

    A `hard` entry that comes back `violated` is a finding. A `soft` one that
    comes back `outside_habit` is a fact about this board, not a defect, and one
    that comes back `unverified` is a fact about the READER - the verdict names
    are different on purpose, so that a list of "violations" never mixes a defect
    with an admission.
    """
    picked = recall(scope=scope, status=status, limit=10000)["entries"]
    boards_seen = {}
    observations = _read_observations()
    for obs in observations:
        boards_seen[obs["metric"]] = boards_seen.get(obs["metric"], 0) + 1
    rows = []
    for entry in picked:
        observed, note = observe(board, entry["trigger"]["board_metric"],
                                 entry["trigger"])
        rows.append({
            "id": entry["id"],
            "kind": entry.get("kind"),
            "scope": entry.get("scope"),
            "claim": entry.get("claim"),
            "metric": entry["trigger"]["board_metric"],
            "verdict": _verdict(entry, observed),
            "observed": observed,
            "how_measured": note,
            "confidence": entry.get("confidence"),
            "origin": entry.get("origin"),
            "boards_observed": boards_seen.get(entry["trigger"]["board_metric"], 0),
        })
    hard = [r for r in rows if r["kind"] == "hard"]
    source = getattr(board, "source", None) or {}
    return {"board": (getattr(board, "path", None) or source.get("path")
                      or None),
            "rows": rows,
            "checked": len(rows),
            "hard_violations": [r["id"] for r in hard
                                if r["verdict"] == "violated"],
            "outside_habit": [r["id"] for r in rows
                              if r["verdict"] == "outside_habit"],
            "unverified": [r["id"] for r in rows
                           if r["verdict"] == "unverified"],
            "not_checkable_here": [r["id"] for r in rows
                                   if r["verdict"] == "not_checkable_here"],
            "observations": len(observations),
            "writes_nothing": True}


# ------------------------------------------------------------------ the loop
def _seed_ids():
    return set(e.get("id") for e in _read_seed())

def _refuse_seed_id(ident):
    """A seed id is not a place to write.

    Letting a local entry take a seed's id would shadow it, and a shadowing
    CANDIDATE would switch off an active rule with nobody confirming anything -
    the exact move the red line forbids, arriving through the back door. So the
    id is refused at every door that writes, and the way to differ from a shipped
    entry is to copy it under a new id.
    """
    if ident in _seed_ids():
        raise ValueError(
            "%r is a seed entry: it ships with the package and is read-only. Copy "
            "it to a new id to change or retire it" % ident)

def candidate(entry, note=None):
    """Write an observation down as a candidate. Never active, never a rule."""
    out = normalise(entry, origin="local")
    _refuse_seed_id(out["id"])
    out["status"] = "candidate"
    if note:
        out["action"] = (out["action"] + " " + str(note)).strip()
    items = [e for e in _read_local() if e.get("id") != out["id"]]
    items.append(out)
    _write_local(items)
    return out


def promote(ident, confirm=False, reason=None):
    """Promote a candidate to active. `confirm=True` is required, not decorative.

    The alternative - promoting on evidence counts alone - is exactly the failure
    mode the design forbids: a number that crosses a threshold is not a person
    deciding that the claim is true.
    """
    if confirm is not True:
        raise PermissionError("promotion needs confirm=True: a candidate does not "
                              "become a rule because its counters moved")
    items = _read_local()
    hit = None
    for entry in items:
        if entry.get("id") == ident:
            hit = entry
            break
    if hit is None:
        seed = [e for e in _read_seed() if e.get("id") == ident]
        if seed:
            raise ValueError("%r is a seed entry: it is read-only in this package. "
                             "Copy it to a new id to change its status" % ident)
        raise KeyError("no entry with id %r in %s" % (ident, path()))
    if hit.get("status") != "candidate":
        raise ValueError("%r is %r, not a candidate" % (ident, hit.get("status")))
    hit["status"] = "active"
    hit["promoted_by"] = str(reason or "")
    hit["updated_at"] = _now()
    _write_local(items)
    return hit


def outcome(ident, hit=True, note=None):
    """Record that a recall did or did not hold on a board. Counters only."""
    items = _read_local()
    for entry in items:
        if entry.get("id") == ident:
            ev = entry.setdefault("evidence", {})
            key = "hits" if hit else "violated"
            ev[key] = (_int_or_none(ev.get(key)) or 0) + 1
            ev.setdefault("sources", [])
            if note:
                ev["sources"].append(str(note))
            entry["updated_at"] = _now()
            _write_local(items)
            return entry
    raise KeyError("no local entry with id %r (the seed is read-only, so its "
                   "counters would not survive a package update)" % ident)


def export(entries=None, status=None):
    """A plain, self-contained JSON blob - what a human or another agent reads."""
    lib = library()
    picked = lib["entries"]
    if status is not None:
        picked = [e for e in picked if e.get("status") == status]
    if entries is not None:
        wanted = set(entries)
        picked = [e for e in picked if e.get("id") in wanted]
    return {"schema": SCHEMA, "exported_at": _now(),
            "counts": _counts(picked),
            "entries": picked,
            "note": ("seed entries carry the corpus measurement they were taken "
                     "from; a number with no denominator is not in here")}


def import_entries(blob):
    """Merge another library in - candidates only.

    An imported file is not a promotion channel: entries claiming `active` land
    as candidates and say so, because the confirmation belonged to whoever wrote
    the file, not to this store.
    """
    if not isinstance(blob, dict):
        raise ValueError("an import must be a JSON object with an `entries` list")
    incoming = blob.get("entries")
    if not isinstance(incoming, list):
        raise ValueError("an import must carry an `entries` list")
    items = _read_local()
    by_id = {e.get("id"): e for e in items}
    added, demoted, skipped = [], [], []
    for raw in incoming:
        try:
            entry = normalise(raw, origin="imported")
            _refuse_seed_id(entry["id"])
        except ValueError as exc:
            skipped.append({"id": (raw or {}).get("id"), "why": str(exc)})
            continue
        if entry["status"] != "candidate":
            entry["status"] = "candidate"
            demoted.append(entry["id"])
        by_id[entry["id"]] = entry
        added.append(entry["id"])
    _write_local(list(by_id.values()))
    return {"added": sorted(added), "demoted_to_candidate": sorted(demoted),
            "skipped": skipped, "store": path()}


def retire(ident, reason=None):
    items = _read_local()
    for entry in items:
        if entry.get("id") == ident:
            entry["status"] = "retired"
            entry["retired_because"] = str(reason or "")
            entry["updated_at"] = _now()
            _write_local(items)
            return entry
    raise KeyError("no local entry with id %r" % ident)

# --------------------------------------------------------------- harvesting
#: The metrics a single board can honestly teach. Everything else here is a rule
#: with a failure side - a via ring floor, a drilled pad spanning the copper, a
#: pad placement the reader cannot certify - and a rule of that sort comes from a
#: fab's capability or a spec, never from one board. A board can only violate
#: those, and a violation is a finding, not a lesson.
HARVEST_METRICS = ("tracks_off_45_grid", "distinct_via_geometries",
                   "distinct_track_widths", "power_wider_than_signal")

HARVEST_SCOPES = {"tracks_off_45_grid": "route.geometry",
                  "distinct_via_geometries": "route.via",
                  "distinct_track_widths": "route.width",
                  "power_wider_than_signal": "route.width"}


def _slug(text):
    keep = []
    for ch in str(text or ""):
        keep.append(ch.lower() if ch.isalnum() else "-")
    out = "".join(keep).strip("-")
    while "--" in out:
        out = out.replace("--", "-")
    return out[:40] or "board"


def _trigger_from(metric, observed):
    """The machine-checkable half, built from what this board actually read.

    Building it from the reading is the point: the threshold then means "this is
    what one real board does", which a second board can be compared against. It
    does NOT mean "good boards do this" - that needs a corpus, and `claim` says
    so in words every time.
    """
    if metric == "tracks_off_45_grid":
        share = observed["off_grid"] / float(observed["tracks"] or 1)
        return {"board_metric": metric, "tolerance_deg": 0.5,
                "max_off_grid_share": round(share, 6)}
    if metric == "distinct_via_geometries":
        return {"board_metric": metric, "max_distinct": int(observed["distinct"])}
    if metric == "distinct_track_widths":
        return {"board_metric": metric, "max_distinct": int(observed["distinct"])}
    if metric == "power_wider_than_signal":
        return {"board_metric": metric}
    return {"board_metric": metric}


def _sentence(metric, observed):
    if metric == "tracks_off_45_grid":
        return ("%d of %d track segments sit more than 0.5 deg off the 45 degree "
                "grid (share %.4f)"
                % (observed["off_grid"], observed["tracks"],
                   observed["off_grid"] / float(observed["tracks"] or 1)))
    if metric == "distinct_via_geometries":
        return "%d distinct via geometries are used" % observed["distinct"]
    if metric == "distinct_track_widths":
        return "%d distinct track widths are used" % observed["distinct"]
    if metric == "power_wider_than_signal":
        return ("power-net median width is %.3f mm against %.3f mm on signal nets"
                % (observed["power_median_mm"], observed["signal_median_mm"]))
    return metric


def harvest(board, dry_run=False, metric=None):
    """Read one board and write down what it says, as CANDIDATES.

    This is the loop that was missing: without it the library only grows when
    someone remembers to type a claim by hand, which is why it sat at two entries.
    What it will not do:

    * write a `hard` entry - those come from a fab's capability, and a board can
      only violate them;
    * overwrite an id that is already there - a second harvest of the same board
      is a no-op, not a silent revision;
    * promote anything. Everything written here is a candidate, and stays one
      until `promote(confirm=True, reason=...)`.

    A board that disagrees with an ACTIVE habit writes a counterexample candidate
    instead of a new habit: the library has to be able to hear "the good boards
    mostly do this, and this one does not".
    """
    chosen = [metric] if metric else list(HARVEST_METRICS)
    for name in chosen:
        if name not in HARVEST_METRICS:
            raise ValueError(
                "metric %r cannot be learned from a single board (learnable: %s). "
                "The rest are rules with a failure side: they come from a fab "
                "capability or a spec, and a board can only violate them."
                % (name, ", ".join(HARVEST_METRICS)))
    lib = library()
    active_by_metric = {}
    for entry in lib["entries"]:
        if entry.get("status") == "active":
            active_by_metric.setdefault(entry["trigger"]["board_metric"],
                                        []).append(entry)
    known_ids = set(e.get("id") for e in lib["entries"])
    source = getattr(board, "source", None) or {}
    board_path = (getattr(board, "path", None) or source.get("path")
                  or source.get("file") or "board")
    board_name = os.path.splitext(os.path.basename(str(board_path)))[0]
    slug = _slug(board_name)
    rows, written, skipped = [], [], []
    for name in chosen:
        observed, how = observe(board, name)
        row = {"metric": name, "observed": observed, "how_measured": how}
        if observed is None:
            row["verdict"] = "not_measured"
            rows.append(row)
            continue
        clashes = active_by_metric.get(name) or []
        counterexample = False
        if clashes:
            verdicts = [_verdict(entry, observed) for entry in clashes]
            if "violated" in verdicts or "outside_habit" in verdicts:
                counterexample = True
            else:
                row["verdict"] = "consistent_with_active"
                rows.append(row)
                continue
        if counterexample:
            ident = "exp.harvest.%s.counterexample.%s" % (name, slug)
            claim = ("On %s, %s - while the active library on this metric says "
                     "otherwise. Recorded as a counterexample so the corpus can be "
                     "corrected, not as a new habit to follow."
                     % (board_name, _sentence(name, observed)))
        else:
            ident = "exp.harvest.%s.%s" % (name, slug)
            claim = ("Single-board observation on %s: %s. This is one board, not a "
                     "corpus, so it can be used as a comparison point and must not "
                     "be read as a habit other boards follow."
                     % (board_name, _sentence(name, observed)))
        kind = "soft"
        row["id"] = ident
        if ident in known_ids:
            row["verdict"] = "already_there"
            skipped.append({"id": ident, "why": "an entry with this id already "
                                                "exists; harvesting again does not "
                                                "rewrite it"})
            rows.append(row)
            continue
        entry = {"id": ident, "kind": kind, "scope": HARVEST_SCOPES.get(name),
                 "claim": claim,
                 "action": ("compare another board against this reading before "
                            "treating it as a habit; promote with a human reason "
                            "and a corpus, never on the strength of this one board"),
                 "trigger": _trigger_from(name, observed),
                 "evidence": {"hits": 1, "violated": None,
                              "sources": [str(board_path)]},
                 "confidence": "single board (1/1)",
                 "owner": "experience_harvest"}
        row["verdict"] = ("counterexample_written" if counterexample
                          else "written")
        if counterexample:
            row["against"] = [e.get("id") for e in clashes]
        if dry_run:
            row["verdict"] = "would_be_" + row["verdict"]
            rows.append(row)
            written.append(ident)
            continue
        try:
            candidate(entry, note=None)
        except ValueError as exc:
            row["verdict"] = "refused"
            row["why"] = str(exc)
            skipped.append({"id": ident, "why": str(exc)})
            rows.append(row)
            continue
        known_ids.add(ident)
        written.append(ident)
        rows.append(row)
    observations = []
    for row in rows:
        if row.get("observed") is None:
            continue
        observations.append({"metric": row["metric"], "board": board_path,
                             "at": _now(), "verdict": row.get("verdict"),
                             "reading": row["observed"]})
    recorded = 0 if dry_run else record_observations(observations)
    return {"board": board_path, "rows": rows,
            "written": written, "skipped": skipped, "dry_run": bool(dry_run),
            "observations": {"measured": len(observations), "stored": recorded},
            "store": path(),
            "hard_not_learned_here": ("a hard rule needs a fab capability or a spec "
                                      "behind it; a board can only violate one, and "
                                      "a violation belongs in a report, not here"),
            "promotion": "every id above is a candidate; promote needs confirm=true "
                         "and a reason"}


# ------------------------------------------------------------------- health
def status():
    """Is this library alive, or filled once and abandoned?

    The reading that matters is not the total: it is how many entries are ACTIVE
    (so they can shape an answer) versus how many candidates are waiting for a
    person who never came. `store_written_at` is the file's own mtime, so "when
    did this last learn anything" cannot be answered by memory.
    """
    lib = library()
    entries = lib["entries"]
    by_origin, by_status, by_kind = {}, {}, {}
    for entry in entries:
        by_origin[entry.get("origin")] = by_origin.get(entry.get("origin"), 0) + 1
        by_status[entry.get("status")] = by_status.get(entry.get("status"), 0) + 1
        by_kind[entry.get("kind")] = by_kind.get(entry.get("kind"), 0) + 1
    waiting = [e.get("id") for e in entries if e.get("status") == "candidate"]
    try:
        written_at = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                   time.gmtime(os.path.getmtime(path())))
    except OSError:
        written_at = None
    observations = _read_observations()
    observed_by_metric = {}
    for obs in observations:
        observed_by_metric[obs["metric"]] = observed_by_metric.get(obs["metric"], 0) + 1
    active = by_status.get("active", 0)
    parts = ["%d active entr%s can shape an answer"
             % (active, "y" if active == 1 else "ies")]
    if waiting:
        parts.append("%d candidate(s) are waiting for a person who has not "
                     "confirmed them" % len(waiting))
    seed_share = by_origin.get("seed", 0)
    if seed_share:
        parts.append("%d of the %d entries ship with the package (measured once, "
                     "then checked in)" % (seed_share, len(entries)))
    if observations:
        parts.append("%d board measurement(s) are recorded, so the corpus behind "
                     "the rules can be checked against what was actually seen here"
                     % len(observations))
    else:
        parts.append("no board has ever been measured against the library by this "
                     "machine (the numbers in the seed come from its own survey)")
    if lib.get("seed_found") is False:
        # Otherwise `total: 0` reads as "the shipped library is empty", which is a
        # different claim from "the file is not on this machine".
        parts.append("the shipped seed was NOT found at %s, so every number here "
                     "is local-only" % lib["seed"])
    elif not entries:
        parts.append("nothing has ever been written down")
    out = {"store": path(), "seed": lib["seed"], "enabled": enabled(),
           "total": len(entries), "by_origin": by_origin,
           "by_status": by_status, "by_kind": by_kind,
           "candidates_waiting": waiting,
           "observations": {"total": len(observations),
                            "by_metric": observed_by_metric},
           "store_written_at": written_at,
           "shadowed_seed_ids": lib["shadowed_seed_ids"],
           "message": "; ".join(parts)}
    if lib.get("seed_found") is False:
        out["seed_found"] = False
        out["seed_note"] = lib["seed_note"]
    return out
