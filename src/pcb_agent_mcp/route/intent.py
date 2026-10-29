"""One intent object, several consumers.

Routing arguments are a flat dictionary of numbers, and every consumer used to
parse them again on its own. That is how two tools end up answering different
questions from the same words: `board_route` is told `clearance_mm=0.2` while
`board_routability` measured with the net-class value, and both are "right".

This module is the single object both sides read. It is deliberately small:

    {"version": 1,
     "rationale": "why this board is built this way (free text, echoed back)",
     "nets": {"route": ["SPI1_CLK", ...], "layers": ["F.Cu", "In1.Cu"]},
     "constraints": {"clearance_mm": 0.2, "track_mm": 0.25, ...},
     "diff_pairs": [...], "length_groups": [...], "impedance_targets": [...],
     "keepouts": [{"x0","y0","x1","y1","layers","rules"}],
     "placement": {"regions": [{"ref","region":[x0,y0,x1,y1],"order","adjacency"}]}}

Three rules, all fail-closed:

* an unknown key is refused (a typo silently becoming "no constraint requested"
  is the failure mode this module exists to prevent);
* what the CALLER passed wins over the file, and every key where the two
  disagree is listed in `conflicts` - the disagreement is reported, never
  resolved by silence;
* nothing here invents a number. A key that is absent from both stays absent,
  which downstream means "not requested" (see `route.rules`).

The placement section is checked, not executed: this module can say a region is
off the board, overlaps a pad or that the footprint is not in it yet, but moving
a part is `edit`'s job and is not done here.
"""

import json
import os

INTENT_VERSION = 1

# The whole vocabulary. Anything else is a refusal, not a comment.
KNOWN = frozenset((
    "version", "rationale", "nets", "constraints", "diff_pairs",
    "length_groups", "impedance_targets", "keepouts", "placement",
))

# `nets.route` / `nets.layers` are the two selectors a consumer may need; they
# are the same words `board_route` already accepts.
NETS_KEYS = frozenset(("route", "layers"))

# Which constraint keys the router understands. Kept as a list instead of
# "whatever is a number" so a misspelt key is refused here rather than ignored
# by `router.plan`.
CONSTRAINT_KEYS = (
    "pitch_mm", "clearance_mm", "track_mm", "via_diameter_mm", "via_drill_mm",
    "edge_clearance_mm", "creepage_mm", "working_voltage_v", "pollution_degree",
    "material_group", "insulation", "coating", "altitude_m",
    "return_path", "return_path_net", "rip_up_rounds", "max_nets",
    "incremental", "allow_vias", "ignore_zone_areas", "second_sweep",
    "congestion_rounds", "congestion_max_rips", "congestion_weight",
)

PLACEMENT_KEYS = frozenset(("regions",))
REGION_KEYS = frozenset(("ref", "region", "order", "adjacency", "why"))


class IntentError(Exception):
    """A refusal with a reason code, so a caller can branch instead of guess."""

    def __init__(self, message, reason_code, hint=None):
        super().__init__(message)
        self.message = message
        self.reason_code = reason_code
        self.hint = hint


def load(spec):
    """A file path, a dict, or a JSON string -> a validated intent dict.

    A path that does not exist is `INTENT_FILE_MISSING`; a version this build
    does not know is `INTENT_VERSION`; anything structurally wrong is
    `INTENT_INVALID` - never a partially honoured intent.
    """
    if spec is None:
        raise IntentError("no intent given", "INTENT_INVALID")
    if isinstance(spec, dict):
        return check(spec)
    if not isinstance(spec, str):
        raise IntentError("intent must be a path or an object, got %s"
                          % (type(spec).__name__,), "INTENT_INVALID")
    text = spec.strip()
    if text.startswith("{"):
        try:
            return check(json.loads(text))
        except ValueError as exc:
            raise IntentError("intent is not valid JSON: %s" % (exc,),
                              "INTENT_INVALID",
                              hint="an intent file is one JSON object; a broken "
                                   "one is refused rather than half-read")
    path = os.path.abspath(os.path.expanduser(text))
    if not os.path.isfile(path):
        raise IntentError("intent file not found: %s" % (path,),
                          "INTENT_FILE_MISSING",
                          hint="pass the intent as a dict/JSON string instead, or "
                               "check the path")
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except ValueError as exc:
        raise IntentError("intent file %s is not valid JSON: %s" % (path, exc),
                          "INTENT_INVALID")
    out = check(data)
    out["_source"] = path
    return out


def check(intent):
    """Validate the shape. Unknown keys are refusals, not comments."""
    if not isinstance(intent, dict):
        raise IntentError("intent must be a JSON object", "INTENT_INVALID")
    extra = sorted(set(intent) - KNOWN - {"_source"})
    if extra:
        raise IntentError("intent has keys this build does not know: %s"
                          % (", ".join(extra),), "INTENT_INVALID",
                          hint="the vocabulary is %s; an unknown key is refused so "
                               "a typo cannot silently mean 'not requested'"
                               % (", ".join(sorted(KNOWN)),))
    ver = intent.get("version", INTENT_VERSION)
    if ver != INTENT_VERSION:
        raise IntentError("intent version %r is not %d" % (ver, INTENT_VERSION),
                          "INTENT_VERSION")
    out = dict(intent)
    out["version"] = INTENT_VERSION
    nets = out.get("nets")
    if nets is not None:
        if not isinstance(nets, dict):
            raise IntentError("intent.nets must be an object", "INTENT_INVALID")
        bad = sorted(set(nets) - NETS_KEYS)
        if bad:
            raise IntentError("intent.nets has unknown keys: %s" % (", ".join(bad),),
                              "INTENT_INVALID")
        for key, val in nets.items():
            if val is not None and not isinstance(val, (list, tuple, str)):
                raise IntentError("intent.nets.%s must be a list or a "
                                  "comma-separated string" % (key,),
                                  "INTENT_INVALID")
    cons = out.get("constraints")
    if cons is not None:
        if not isinstance(cons, dict):
            raise IntentError("intent.constraints must be an object",
                              "INTENT_INVALID")
        bad = sorted(set(cons) - set(CONSTRAINT_KEYS))
        if bad:
            raise IntentError("intent.constraints has unknown keys: %s"
                              % (", ".join(bad),), "INTENT_INVALID",
                              hint="known: %s" % (", ".join(CONSTRAINT_KEYS),))
    for key in ("diff_pairs", "length_groups", "impedance_targets", "keepouts"):
        val = out.get(key)
        if val is not None and not isinstance(val, list):
            raise IntentError("intent.%s must be a list" % (key,), "INTENT_INVALID")
    place = out.get("placement")
    if place is not None:
        if not isinstance(place, dict):
            raise IntentError("intent.placement must be an object",
                              "INTENT_INVALID")
        bad = sorted(set(place) - PLACEMENT_KEYS)
        if bad:
            raise IntentError("intent.placement has unknown keys: %s"
                              % (", ".join(bad),), "INTENT_INVALID")
        regions = place.get("regions") or []
        if not isinstance(regions, list):
            raise IntentError("intent.placement.regions must be a list",
                              "INTENT_INVALID")
        for item in regions:
            if not isinstance(item, dict):
                raise IntentError("placement regions must be objects",
                                  "INTENT_INVALID")
            bad = sorted(set(item) - REGION_KEYS)
            if bad:
                raise IntentError("placement region has unknown keys: %s"
                                  % (", ".join(bad),), "INTENT_INVALID")
            if not item.get("ref"):
                raise IntentError("a placement region needs a footprint ref",
                                  "INTENT_INVALID")
            box = item.get("region")
            if not (isinstance(box, (list, tuple)) and len(box) == 4):
                raise IntentError("placement region %r needs region=[x0,y0,x1,y1]"
                                  % (item.get("ref"),), "INTENT_INVALID")
    return out


def _as_list(val):
    if val is None:
        return []
    if isinstance(val, str):
        return [x.strip() for x in val.split(",") if x.strip()]
    return list(val)


def flatten(intent):
    """The intent as the flat keyword dictionary `router.plan` takes.

    Only what the intent DECLARES comes out: a key it does not mention is not in
    the result, which downstream means "not requested" rather than "zero".
    """
    kw = {}
    cons = intent.get("constraints") or {}
    for key in CONSTRAINT_KEYS:
        if key in cons and cons[key] is not None:
            kw[key] = cons[key]
    nets = intent.get("nets") or {}
    route = _as_list(nets.get("route"))
    layers = _as_list(nets.get("layers"))
    if route:
        kw["nets"] = route
    if layers:
        kw["layers"] = layers
    for key in ("diff_pairs", "length_groups", "impedance_targets"):
        val = intent.get(key)
        if val:
            kw[key] = [dict(x) for x in val]
    return kw


def resolve(caller_kw, intent):
    """Caller arguments win; every key the two disagree on is named.

    Returns `(kwargs, report)`. `report` carries the source of every key that was
    decided, so an answer can say where a number came from instead of implying
    that the intent file was obeyed.
    """
    from_intent = flatten(intent)
    merged = dict(from_intent)
    conflicts, source = [], {}
    for key, val in (caller_kw or {}).items():
        if val is None:
            continue
        if key in from_intent and from_intent[key] != val:
            conflicts.append({"key": key, "intent": from_intent[key],
                              "caller": val, "used": "caller"})
        merged[key] = val
        source[key] = "caller"
    for key in from_intent:
        source.setdefault(key, "intent")
    report = {
        "source": intent.get("_source", "inline"),
        "version": intent.get("version", INTENT_VERSION),
        "declared": sorted(from_intent),
        "from_caller": sorted(k for k, v in source.items() if v == "caller"),
        "conflicts": conflicts,
        "rationale": intent.get("rationale"),
    }
    return merged, report


def check_against_board(intent, board):
    """What the intent says about THIS board: keepouts and placement regions.

    Nothing is written and nothing is moved. The point is to answer, before ten
    minutes of routing, whether the intent can even be true here: a keepout that
    swallows a pad, a region off the board, a ref that does not exist, a
    footprint that is not inside the region it was assigned.
    """
    out = {"keepouts": [], "placement": [], "problems": []}
    bbox = board_bbox(board)
    for k, box in enumerate(intent.get("keepouts") or []):
        try:
            x0, y0, x1, y1 = (float(box["x0"]), float(box["y0"]),
                              float(box["x1"]), float(box["y1"]))
        except (KeyError, TypeError, ValueError):
            out["problems"].append({"what": "keepout", "index": k,
                                    "problem": "needs numeric x0/y0/x1/y1"})
            continue
        rec = {"index": k, "box": [x0, y0, x1, y1],
               "layers": box.get("layers"), "rules": box.get("rules")}
        rec["inside_board"] = bbox is None or (
            x0 >= bbox[0] - 1e-6 and y0 >= bbox[1] - 1e-6
            and x1 <= bbox[2] + 1e-6 and y1 <= bbox[3] + 1e-6)
        rec["pads_inside"] = _pads_in(board, x0, y0, x1, y1)
        if not rec["inside_board"]:
            out["problems"].append({"what": "keepout", "index": k,
                                    "problem": "extends past the board edge"})
        if rec["pads_inside"]:
            out["problems"].append({"what": "keepout", "index": k,
                                    "problem": "%d pad(s) sit inside it"
                                               % (len(rec["pads_inside"]),),
                                    "pads": rec["pads_inside"][:12]})
        out["keepouts"].append(rec)
    regions = ((intent.get("placement") or {}).get("regions") or [])
    fps = _footprints(board)
    for k, item in enumerate(regions):
        ref = item.get("ref")
        x0, y0, x1, y1 = [float(v) for v in item["region"]]
        fp = fps.get(ref)
        rec = {"ref": ref, "region": [x0, y0, x1, y1],
               "order": item.get("order"), "exists": fp is not None}
        if fp is None:
            out["problems"].append({"what": "placement", "ref": ref,
                                    "problem": "no such footprint on this board"})
        else:
            rec["at"] = list(fp)
            rec["inside"] = (x0 - 1e-6 <= fp[0] <= x1 + 1e-6
                             and y0 - 1e-6 <= fp[1] <= y1 + 1e-6)
            if not rec["inside"]:
                out["problems"].append({"what": "placement", "ref": ref,
                                        "problem": "footprint is outside its "
                                                   "declared region"})
        if bbox is not None and (x0 < bbox[0] - 1e-6 or y0 < bbox[1] - 1e-6
                                 or x1 > bbox[2] + 1e-6 or y1 > bbox[3] + 1e-6):
            rec["inside_board"] = False
            out["problems"].append({"what": "placement", "ref": ref,
                                    "problem": "region extends past the board edge"})
        out["placement"].append(rec)
    return out


def board_bbox(board):
    """The board's extents in board space, or `None` when there is no edge.

    `footprints` is a LIST of dicts (`ref/x/y/rotation/pads`) and the edge lives
    in `outline` + `outline_open` as polygons - both facts are read from
    `adapters/canonical.Board`, not assumed.
    """
    xs, ys = [], []
    for poly in list(getattr(board, "outline", None) or []) + \
            list(getattr(board, "outline_open", None) or []):
        for pt in poly or []:
            try:
                xs.append(float(pt[0]))
                ys.append(float(pt[1]))
            except (TypeError, IndexError, ValueError):
                continue
    if not xs:
        return None
    return [min(xs), min(ys), max(xs), max(ys)]


def _footprints(board):
    out = {}
    for fp in getattr(board, "footprints", None) or []:
        if not isinstance(fp, dict):
            continue
        ref = fp.get("ref")
        if ref and fp.get("x") is not None:
            out[ref] = (float(fp["x"]), float(fp["y"]))
    return out


def _pads_in(board, x0, y0, x1, y1):
    """Pads whose position is inside the box, as 'REF.pad' strings.

    A pad an intent box swallows is a design error worth naming: the keepout
    would make the pad unreachable and the router would report `no_path` for a
    reason that was declared here, not found there.
    """
    out = []
    for fp in getattr(board, "footprints", None) or []:
        if not isinstance(fp, dict):
            continue
        ref = fp.get("ref") or "?"
        for pad in fp.get("pads") or []:
            if not isinstance(pad, dict):
                continue
            try:
                px, py = float(pad["x"]), float(pad["y"])
            except (KeyError, TypeError, ValueError):
                continue
            if x0 - 1e-6 <= px <= x1 + 1e-6 and y0 - 1e-6 <= py <= y1 + 1e-6:
                out.append("%s.%s" % (ref, pad.get("number", "?")))
    return out
