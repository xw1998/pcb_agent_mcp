"""Turn the rule catalogue into input that the *generator* can consume.

Before 0.1.19 the arrows only pointed one way:

    board  ->  audit()  ->  52 verdicts        (rules read the board)
    rules  ->  a human   ->  keepout .json     (a human read the rules)

The second arrow is where "the rules were learned but not obeyed" lives: the
router only ever saw a rectangle list somebody typed by hand, and that list was
never checked against the board it described. This module closes the arrow by
deriving the same geometry the audit uses (``measure._is_magnetic`` /
``_is_crystal`` / ``_pad_hull``) and emitting the router's own input format.

What it does NOT do: it does not place parts and it does not route. A placement
proposal needs a cost over the whole floorplan, and pretending a rectangle list
is one would be the same mistake in the other direction. What it does give the
placement step is the *legal window* of every part carrying a keep-out, which is
measurable and therefore checkable.

Coordinate convention, because it bit the hand-made file: ``.kicad_pcb`` stores
y downwards, the maze router (``_mazeH.py``) negates y on load so its world is
y *up* in file numbers -- i.e. maze coords == file coordinates. The router's
``KEEPOUT`` reader treats each row as ``[cx, cy, hx, hy]`` (**centre**, not
corner). The hand-written ``_ko.json`` of 0.1.17 carried ``[x_min, y_min, hx,
hy]``, so every keep-out it injected sat ``(hx, hy)`` away from the part it was
supposed to protect. ``rect_is_centre_based`` exists so that claim stays
testable instead of being a paragraph.
"""
import json
import math
import os

from .measure import _is_magnetic, _is_crystal, _pad_hull

SCHEMA_VERSION = 1

# The audit judges copper-to-part clearance; the router needs a hard block. The
# growth is the same 0.1 mm the 0.1.17 hand file used, kept a constant so a
# change is a change in one place rather than in a number copied five times.
DEFAULT_GROW_MM = 0.1


def _parts(board):
    """(footprint, kind) for every part that carries a keep-out, sorted by ref."""
    out = []
    for fp in (board.footprints or ()):
        if _is_magnetic(fp):
            out.append((fp, "magnetic"))
        elif _is_crystal(fp):
            out.append((fp, "crystal"))
    out.sort(key=lambda kv: str(kv[0].get("ref") or ""))
    return out


def keepout_rects(board, grow_mm=DEFAULT_GROW_MM):
    """Axis-aligned keep-out boxes, in file coordinates, centre + half-extent.

    Every row carries the evidence that produced it (``ref``/``kind``/``rule``),
    because a rectangle with no owner cannot be re-derived and therefore cannot
    be audited later. A part with no pad position is skipped and named in
    ``skipped`` -- skipping silently would report a cleaner board than exists.
    """
    rows, skipped = [], []
    for fp, kind in _parts(board):
        hull = _pad_hull(fp.get("pads"), grow_mm)
        if hull is None:
            skipped.append({"ref": fp.get("ref"), "why": "no pad position"})
            continue
        x0, y0, x1, y1 = hull
        rows.append({
            "ref": fp.get("ref"),
            "kind": kind,
            "rule": ("layout.magnetic_keepout" if kind == "magnetic"
                     else "layout.crystal_keepout_adjacent"),
            "cx": round((x0 + x1) / 2.0, 6),
            "cy": round((y0 + y1) / 2.0, 6),
            "hx": round((x1 - x0) / 2.0, 6),
            "hy": round((y1 - y0) / 2.0, 6),
            "bbox": [round(v, 6) for v in (x0, y0, x1, y1)],
        })
    return rows, skipped


def maze_keepout(board, grow_mm=DEFAULT_GROW_MM):
    """The router's ``KEEPOUT`` payload: ``[[cx, cy, hx, hy], ...]``."""
    rows, _ = keepout_rects(board, grow_mm)
    return [[r["cx"], r["cy"], r["hx"], r["hy"]] for r in rows]


def write_maze_keepout(board, path, grow_mm=DEFAULT_GROW_MM):
    """Write the router payload and report what was written."""
    payload = maze_keepout(board, grow_mm)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    return {"path": os.path.abspath(path), "n_rects": len(payload),
            "payload": payload}


def legal_window(board, ref, bbox, grow_mm=DEFAULT_GROW_MM):
    """Where a keep-out part could legally sit: does any *other* part conflict?

    Returns the part's own box and the list of other keep-out owners whose
    grown box already overlaps it. This is deliberately only part-to-part: the
    copper question ("is any trace under it") is `measure`'s job and answering
    it here with a second implementation would give two answers to one question.
    """
    rows, _ = keepout_rects(board, grow_mm)
    mine = [r for r in rows if str(r["ref"]) == str(ref)]
    if not mine:
        return {"ref": ref, "unmeasurable": "not a keep-out owner"}
    a = mine[0]
    if bbox is not None:
        x0, y0, x1, y1 = [float(v) for v in bbox]
        a = dict(a, bbox=[x0, y0, x1, y1])
        a["cx"], a["cy"] = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        a["hx"], a["hy"] = (x1 - x0) / 2.0, (y1 - y0) / 2.0
    hits = []
    for r in rows:
        if str(r["ref"]) == str(ref):
            continue
        if (abs(r["cx"] - a["cx"]) <= r["hx"] + a["hx"] + 1e-9 and
                abs(r["cy"] - a["cy"]) <= r["hy"] + a["hy"] + 1e-9):
            hits.append(r["ref"])
    return {"ref": ref, "bbox": a["bbox"], "overlaps": hits,
            "method": "grown pad-hull boxes, same geometry as the audit"}


def placement_clearances(board):
    """Where a PLACEMENT step must move a part, with the distance it is short by.

    The router never sees this constraint, and it must not: forbidding a 3 mm
    halo of copper around every high-speed pad would forbid the memory's own
    escape routing. The rule is about a PART beside copper, so the only actor
    that can honour it is whatever moves parts. This function gives that actor
    the same number the audit grades, derived from the same scan
    (`measure.magnet_digital_pairs`), so placement and audit cannot disagree.

    ``limit_mm`` is read from the rule catalogue, not repeated here: a change to
    the limit is a change in one place.
    """
    from .measure import magnet_digital_pairs
    from ..calc.rules import LAYOUT_RULES
    limit, rule_id = None, "layout.magnetic_to_hs_digital"
    for r in LAYOUT_RULES:
        if r.get("id") == rule_id:
            limit = float(r.get("limit"))
            break
    if limit is None:
        raise KeyError("%s is not in the catalogue" % rule_id)
    pairs = magnet_digital_pairs(board)
    rows = []
    for p in pairs:
        gap = float(p["gap_mm"])
        rows.append({"magnet": p["magnet"], "part": p["part"],
                     "gap_mm": round(gap, 3), "limit_mm": limit,
                     "short_mm": round(max(0.0, limit - gap), 3),
                     "action": "move the magnet away" if gap < limit else "ok"})
    bad = [r for r in rows if r["gap_mm"] < limit]
    return {
        "rule": rule_id,
        "limit_mm": limit,
        "pairs": rows,
        "violations": bad,
        "method": "pad-edge to pad-edge, switching magnetic vs high-speed part "
                  "(same scan as measure.magnetic_to_hs_digital_min_mm)",
    }

def net_binding(board):
    """Prove that copper is bound to the schematic net, not placed by geometry.

    A track that belongs to no net, or to a net no pad declares, is the
    signature of "geometry only" routing. Both counts are returned so the claim
    can be checked on a real board instead of asserted.
    """
    pad_nets = set()
    pads_total = 0
    for fp in (board.footprints or ()):
        for p in (fp.get("pads") or ()):
            pads_total += 1
            n = p.get("net")
            if n:
                pad_nets.add(str(n))
    tracks = list(board.tracks or ())
    tr_named = [t for t in tracks if t.get("net")]
    tr_unknown = [t for t in tr_named if str(t["net"]) not in pad_nets]
    return {
        "pads": pads_total,
        "pads_with_net": sum(1 for fp in (board.footprints or ())
                             for p in (fp.get("pads") or ()) if p.get("net")),
        "nets_on_pads": len(pad_nets),
        "tracks": len(tracks),
        "tracks_with_net": len(tr_named),
        "tracks_on_unknown_net": len(tr_unknown),
        "unknown_examples": [str(t["net"]) for t in tr_unknown[:5]],
    }


def constraints(board, grow_mm=DEFAULT_GROW_MM):
    """Everything a generator needs, in one dict, with its provenance."""
    rows, skipped = keepout_rects(board, grow_mm)
    return {
        "schema_version": SCHEMA_VERSION,
        "grow_mm": grow_mm,
        "rule_ids": ["layout.magnetic_keepout",
                     "layout.magnetic_keepout_deep",
                     "layout.crystal_keepout_adjacent",
                     "layout.crystal_keepout"],
        "keepout": rows,
        "keepout_skipped": skipped,
        "net_binding": net_binding(board),
        "placement_clearances": placement_clearances(board),
        "note": "rects are centre + half-extent in file coordinates, i.e. what "
                "_mazeH.py KEEPOUT reads; corners are recoverable as "
                "cx-hx .. cx+hx",
    }


def classify_row(row, rect, tol_mm=1e-3):
    """Say what a router row's first two numbers are, relative to a known rect.

    ``rect`` is this module's dict (``cx``/``cy``/``hx``/``hy``). The 0.1.17
    hand file wrote the box's minimum corner; the router reads a centre. A row
    is only accepted as ``centre`` when its numbers are the centre -- silently
    tolerating a corner would put the block one half-extent away from the part
    and still look plausible, which is exactly how the defect survived a whole
    version.
    """
    if not row or len(row) != 4:
        return "malformed"
    cx, cy, hx, hy = [float(v) for v in row]
    if abs(hx - rect["hx"]) > tol_mm or abs(hy - rect["hy"]) > tol_mm:
        return "wrong_size"
    if abs(cx - rect["cx"]) <= tol_mm and abs(cy - rect["cy"]) <= tol_mm:
        return "centre"
    if (abs(cx - (rect["cx"] - rect["hx"])) <= tol_mm and
            abs(cy - (rect["cy"] - rect["hy"])) <= tol_mm):
        return "corner_min"
    return "unrelated"


def classify_rows(rows, rects):
    """Pair every row with the rect it claims to describe and classify it.

    Pairing is by half-extent (the only field a corner-form row gets right), so
    a row that matches no rect is reported as such instead of being forced onto
    the nearest part.
    """
    out = []
    for row in rows or ():
        same = [r for r in rects
                if len(row) == 4 and abs(abs(float(row[2])) - r["hx"]) <= 1e-3
                and abs(abs(float(row[3])) - r["hy"]) <= 1e-3]
        if not same:
            out.append({"row": row, "ref": None, "kind": "unmatched"})
            continue
        best = min(same, key=lambda r: abs(float(row[0]) - r["cx"]) +
                   abs(float(row[1]) - r["cy"]))
        out.append({"row": row, "ref": best["ref"],
                    "kind": classify_row(row, best)})
    return out


def _main(argv):
    from ..adapters.kicad_pcb import parse_board
    if not argv:
        print("usage: python -m pcb_agent_mcp.ruleset.design_constraints "
              "BOARD.kicad_pcb [OUT.json]")
        return 2
    board = parse_board(argv[0])
    rows, skipped = keepout_rects(board)
    print("keep-out owners: %d (%d skipped)" % (len(rows), len(skipped)))
    for r in rows:
        print("  %-4s %-9s centre %8.3f %8.3f  half %6.3f %6.3f" %
              (r["ref"], r["kind"], r["cx"], r["cy"], r["hx"], r["hy"]))
    nb = net_binding(board)
    print("net binding: %(pads_with_net)d/%(pads)d pads, %(tracks_with_net)d/"
          "%(tracks)d tracks on net, %(tracks_on_unknown_net)d on unknown net" % nb)
    if len(argv) > 1:
        info = write_maze_keepout(board, argv[1])
        print("wrote", info["path"], "n_rects", info["n_rects"])
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(__import__("sys").argv[1:]))
