"""What the boards that were actually built look like - as a target, not a verdict.

The audit answers "may this board be manufactured". It does not answer the other
half of the same question: "is this board like the ones that came out well". A
rule set can only say no; a designer needs a number to aim at.

So this module measures a corpus of finished boards and reports, per metric, the
10th / 50th / 90th percentile. A board compared against it gets three words per
metric - below, inside, above - and nothing else:

    nothing here blocks a write, and nothing here is a pass

That boundary is deliberate. A percentile of 18 public demo boards is evidence
about what good boards look like; it is not evidence that a value outside it is
wrong (a 0.15 mm track is a legitimate choice on a 1 oz board with room to
spare). Anything that mattered for manufacturing is a P0 rule with a vendor
limit and a quote, and lives in `ruleset/audit.py`.

Corpus discovery never guesses silently: if no demos are installed the answer is
`unavailable` with the paths that were tried, because a band computed from two
boards would look exactly like a band computed from eighteen.
"""
from __future__ import annotations

import glob
import os

#: Metrics worth aiming at. Each is a `measure()` row; a board that cannot be
#: measured on one of them is `unknown` there, never 0.
METRICS = (
    ("min_track_width", "narrowest track (mm)"),
    ("min_track_space", "closest different-net copper (mm)"),
    ("min_via_outer", "smallest via diameter (mm)"),
    ("min_plated_drill", "smallest plated drill (mm)"),
    ("min_via_outer_minus_drill", "via copper collar, outer - drill (mm)"),
    ("min_copper_to_edge", "copper to board edge (mm)"),
    ("min_board_dim", "smaller board side (mm)"),
)

DEFAULT_DEMO_DIRS = (
    "D:/KiCad/10.0/share/kicad/demos",
    "C:/Program Files/KiCad/10.0/share/kicad/demos",
    "C:/Program Files/KiCad/9.0/share/kicad/demos",
)

def demo_dir():
    """The first demos directory that exists, or None. Never invented."""
    env = os.environ.get("PCB_AGENT_DEMO_DIR")
    if env and os.path.isdir(env):
        return env
    for d in DEFAULT_DEMO_DIRS:
        if os.path.isdir(d):
            return d
    return None

def corpus_paths(directory=None):
    """Every demo board under `directory` (default: the installed KiCad demos)."""
    d = directory or demo_dir()
    if not d or not os.path.isdir(d):
        return []
    found = glob.glob(os.path.join(d, "*", "*.kicad_pcb"))
    found += glob.glob(os.path.join(d, "*.kicad_pcb"))
    return sorted(set(found))

def _pct(values, q):
    """Percentile by nearest rank - no interpolation between two real boards."""
    if not values:
        return None
    v = sorted(values)
    if len(v) == 1:
        return v[0]
    k = int(round(q * (len(v) - 1)))
    return v[max(0, min(k, len(v) - 1))]

_BANDS_CACHE = {}

#: Bumped whenever the shape of `bands()` changes. Without it a disk cache written
#: by an older version would keep answering with fields this version no longer
#: produces, and the answer would look valid while missing what was just added.
_CACHE_VERSION = 2

def _disk_cache_path():
    import tempfile
    return os.path.join(tempfile.gettempdir(), "pcb_agent_design_bands.json")

def _disk_read(path):
    import json
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:                                   # noqa: BLE001
        return None

def _disk_write(path, payload):
    import json
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        os.replace(tmp, path)
    except Exception:                                   # noqa: BLE001
        pass                                            # a cache that cannot write is still correct

def bands(paths=None, directory=None):
    """Measure the corpus and return per-metric percentiles.

    Cached per exact path list plus file mtimes, so a second call is free and a
    changed board is never answered from a stale measurement.
    """
    from ..adapters import open_board
    from . import measure as _measure

    paths = list(paths or corpus_paths(directory))
    if not paths:
        return {"status": "error", "reason_code": "CORPUS_NOT_FOUND",
                "message": "no demo boards found; tried %s"
                           % (list(DEFAULT_DEMO_DIRS),),
                "tried": list(DEFAULT_DEMO_DIRS), "boards": 0}
    key = (_CACHE_VERSION,) + tuple((p, os.path.getmtime(p)) for p in paths)
    if key in _BANDS_CACHE:
        return _BANDS_CACHE[key]
    # Measuring eighteen finished boards is a couple of minutes of pair search, and
    # that is too long to pay on every call. The answer is a pure function of the
    # files, so it is kept on disk under a key that changes when any of them does.
    disk = _disk_cache_path()
    cached = _disk_read(disk)
    import json as _json
    key_json = _json.loads(_json.dumps(key))        # (2, (path, mtime)) -> JSON form
    if cached and cached.get("key") == key_json:
        _BANDS_CACHE[key] = cached["bands"]
        return cached["bands"]

    values = dict((name, []) for name, _ in METRICS)
    floors = {}
    used, skipped = [], []
    for p in paths:
        try:
            m = _measure.measure(open_board(p))
        except Exception as exc:                      # noqa: BLE001
            skipped.append({"path": p, "why": "%s: %s" % (type(exc).__name__, exc)})
            continue
        used.append(p)
        for name, _ in METRICS:
            row = m.get(name) or {}
            v = row.get("value")
            from_floor = v is None
            if from_floor:
                v = row.get("lower_bound")      # "nothing closer than the window"
            if isinstance(v, (int, float)):
                values[name].append(float(v))
                if from_floor:
                    floors[name] = floors.get(name, 0) + 1

    out_bands = {}
    for name, title in METRICS:
        vals = values[name]
        out_bands[name] = {
            "title": title, "n": len(vals),
            "p10": _pct(vals, 0.10), "p50": _pct(vals, 0.50),
            "p90": _pct(vals, 0.90),
            "min": (min(vals) if vals else None),
            "max": (max(vals) if vals else None),
            # how many of the n values are floors ("nothing closer than the 2 mm
            # window") rather than measured distances. A band built entirely from
            # floors is a bounded-below reading, not a distribution of distances.
            "floor_values": floors.get(name, 0),
        }
    result = {"status": "ok", "boards": len(used), "skipped": skipped,
              "bands": out_bands,
              "corpus": [os.path.basename(p) for p in used],
              "reads_as": ("percentiles of public, finished boards - a target, not "
                           "a limit; nothing here blocks or passes anything"),
              "caveat": ("a `lower_bound` row means no pair was closer than the "
                         "search window, so its value is a floor on the true "
                         "spacing, not the spacing itself")}
    _BANDS_CACHE[key] = result
    _disk_write(disk, {"key": key_json, "bands": result})
    return result

def compare(board, bands_result=None, paths=None, directory=None):
    """This board against the bands. Three words per metric, no verdict."""
    from . import measure as _measure

    b = bands_result or bands(paths=paths, directory=directory)
    if b.get("status") != "ok":
        return {"status": "error", "reason_code": b.get("reason_code"),
                "message": b.get("message"), "blocks_nothing": True}
    m = _measure.measure(board)
    rows = []
    for name, title in METRICS:
        band = b["bands"][name]
        row = m.get(name) or {}
        v = row.get("value")
        if v is None:
            v = row.get("lower_bound")
        if not isinstance(v, (int, float)):
            rows.append({"metric": name, "title": title, "this": None,
                         "position": "unknown",
                         "why": row.get("unmeasurable") or "not measurable here",
                         "p10": band["p10"], "p50": band["p50"], "p90": band["p90"]})
            continue
        v = float(v)
        if band["p10"] is None:
            pos = "no_band"
        elif v < band["p10"]:
            pos = "below"
        elif v > band["p90"]:
            pos = "above"
        else:
            pos = "inside"
        rows.append({"metric": name, "title": title, "this": round(v, 3),
                     "position": pos, "p10": band["p10"], "p50": band["p50"],
                     "p90": band["p90"],
                     "n": band["n"],
                     "lower_bound": ("lower_bound" in row)})
    return {"status": "ok", "rows": rows, "boards_in_corpus": b["boards"],
            "suggested": _suggest(b), "blocks_nothing": True,
            "reads_as": ("`below` / `above` says this board is outside the middle "
                         "80% of the corpus on that metric - it does NOT say the "
                         "value is wrong, and it is not a rule result")}

def _suggest(band_result):
    """Numbers to start a design with: the corpus median of each *minimum*.

    The median of the corpus minima is the value a finished board tends to bottom
    out at - so it is the right thing to aim a new design's floor at, not its
    typical value. It is a starting point, and three things outrank it: the P0
    vendor limits (quoted, in `board_rule_audit`), the current the copper has to
    carry, and the fab's own process. So it is labelled `starting_point`, and a
    metric whose band is missing stays missing instead of being filled in.
    """
    band = band_result.get("bands") or {}
    caveats = {}
    def mid(name):
        row = band.get(name) or {}
        if row.get("p50") is None:
            return None
        if row.get("floor_values") and row["floor_values"] == row.get("n"):
            # every value in this band is "no closer than the search window", so
            # the median is a floor on the true spacing, not a spacing.
            caveats[name] = ("all %d values are lower bounds (nothing closer than "
                             "the pair-search window); the real spacing may be larger"
                             % row["n"])
        return round(row["p50"], 3)
    return {
        "narrowest_track_mm": mid("min_track_width"),
        "net_to_net_clearance_mm": mid("min_track_space"),
        "via_diameter_mm": mid("min_via_outer"),
        "via_drill_mm": mid("min_plated_drill"),
        "copper_to_edge_mm": mid("min_copper_to_edge"),
        "caveats": caveats,
        "reads_as": ("starting_point: the corpus median of each board's MINIMUM, i.e. "
                     "where finished boards bottom out. Three things outrank it - the "
                     "P0 vendor limits, the current the copper must carry, and the "
                     "actual fab process"),
        "outranked_by": ["board_rule_audit (P0 limits, quoted)",
                         "current density / voltage drop",
                         "the declared fab process"],
    }

__all__ = ["METRICS", "bands", "compare", "corpus_paths", "demo_dir"]
