#!/usr/bin/env python3
"""RK3506 SOM board, step by step: outline, layer kinds, castellation, ground planes.

Nothing here is a route. The planes are poured by KiCad's own ZONE_FILLER
(`pcb_agent_mcp.fidelity.fill.add_zone(..., fill_it=True)`), never by this file;
the outline is 4 lines + 4 arcs because a router-cut corner is a radius, not a
sharp corner. Component placement and copper routing are NOT here - they are the
next step, and no automatic placement or routing is allowed in this repository.

    python tools/gen_som_board.py --create          # new board (refuses to overwrite)
    python tools/gen_som_board.py --castellation    # J1: 140 half-holes, nets from CSV
    python tools/gen_som_board.py --planes          # GND on In1/In4/B.Cu, KiCad pours
    python tools/gen_som_board.py --check           # stats + DRC + zone report
"""
import argparse
import csv
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "src"))

EXAMPLES = os.path.join(REPO, "examples", "rk3506_som")
BOARD = os.path.join(EXAMPLES, "rk3506_som.kicad_pcb")
LIB = os.path.join(EXAMPLES, "lib", "RK3506.pretty")

# ---- the frozen design (docs/143): 45 x 45, 1.6 mm, 6 layers, single side SMD
SIZE = 45.0
CORNER_R = 2.0
THICKNESS = 1.6
COPPER = 6
# L1 F.Cu signal / L2 In1 GND plane / L3 In2 signal / L4 In3 power / L5 In4 GND / L6 B.Cu GND
INNER_KINDS = ["power", "signal", "power", "power"]
GND_PLANE_LAYERS = ["In1.Cu", "In4.Cu", "B.Cu"]
# Measured 2026-11-13, four layers on the SAME file, SAME polygon, SAME net,
# each run on a copy with every existing zone stripped first:
#   In1.Cu -> ok, fill 1922.987 mm^2, 1 filled polygon
#   In3.Cu -> ENGINE_FAILED, kimathLogOverflow (Overflow converting value
#             -2147493567.024006 to int)
#   In4.Cu -> ENGINE_FAILED, the same kimathLogOverflow
#   F.Cu   -> TIMEOUT, "the engine did not finish filling in 300s"
# So "already carries a filled polygon" is NOT the discriminator (the strips
# prove it), and neither is polygon shape or vertex count. In1.Cu is the only
# layer that pours; In3/In4 overflow at the same value they did before y_flip
# was added, and the outer layer does not return at all. Until that is resolved
# only In1.Cu is poured; In4/B.Cu stay the design target, not a result.
PLANE_LAYERS_NOW = ["In1.Cu"]

ZONE_INSET = 0.5        # plane edge stays 0.5 mm inside the board edge
ZONE_R = 1.5           # plane corner radius (board corner is 2.0, so the plane is
                       # strictly inside it: 2.0 - 0.5)
ZONE_ARC_STEP = 15.0   # degrees per straight in the plane's corner arc


def read_csv(name):
    with open(os.path.join(EXAMPLES, name), encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def stamp_pad_nets():
    """{pad number: net} for J1, straight out of the frozen netlist."""
    out = {}
    for row in read_csv("netmap_terminals.csv"):
        if row["device"] == "STAMP":
            out[row["pin"]] = row["net"]
    if len(out) != 140:
        raise SystemExit("the netlist has %d stamp terminals, the footprint has 140"
                         % len(out))
    return out


def plane_outline(size=SIZE, inset=ZONE_INSET, r=ZONE_R, step=ZONE_ARC_STEP):
    """A rounded-rectangle polygon, sampled on the arcs. Explicit, reviewable."""
    lo = inset
    hi = size - inset
    pts = []
    corners = [(hi - r, lo + r, 270.0), (hi - r, hi - r, 0.0),
               (lo + r, hi - r, 90.0), (lo + r, lo + r, 180.0)]
    # bottom edge, then CCW round the four corners
    pts.append((lo + r, lo))
    pts.append((hi - r, lo))
    for cx, cy, a0 in corners:
        n = int(round(90.0 / step))
        # k = 0..n INCLUDING both tangent points: without them the polygon
        # joins one corner's last sample to the next corner's first sample and
        # chamfers across the straight edge that should be there.
        for k in range(0, n + 1):
            a = math.radians(a0 + k * step)
            pts.append((round(cx + r * math.cos(a), 4),
                        round(cy + r * math.sin(a), 4)))
    # (the two corner tangents on the left edge are already the last arc sample)
    # drop consecutive duplicates, and never repeat the first point
    clean = []
    for p in pts:
        if clean and abs(p[0] - clean[-1][0]) < 1e-9 and abs(p[1] - clean[-1][1]) < 1e-9:
            continue
        clean.append(p)
    if clean and clean[0] == clean[-1]:
        clean.pop()
    return [[x, y] for x, y in clean]


# ----------------------------------------------------------------------- steps
def create(path=BOARD):
    from pcb_agent_mcp.edit import plan as P

    if os.path.exists(path):
        raise SystemExit("%s exists; --create refuses to overwrite it (move it "
                         "aside first)" % path)
    spec = {"width_mm": SIZE, "height_mm": SIZE, "copper_layers": COPPER,
            "thickness_mm": THICKNESS, "corner_radius_mm": CORNER_R,
            "edge_width_mm": 0.1, "clearance_mm": 0.2, "trace_width_mm": 0.2,
            "via_dia_mm": 0.6, "via_drill_mm": 0.3,
            "inner_kinds": INNER_KINDS, "title": "RK3506 core board"}
    res = P.create_board(path, spec)
    print(json.dumps({k: res["gates"][k] for k in res["gates"]}, ensure_ascii=False))
    return res


def castellation(path=BOARD):
    from pcb_agent_mcp.edit import plan as P

    nets = stamp_pad_nets()
    op = {"op": "add_footprint", "ref": "J1",
          "library": LIB, "name": "SOM_CASTELLATION_140",
          "lib_id": "RK3506:SOM_CASTELLATION_140",
          "x": SIZE / 2.0, "y": SIZE / 2.0, "rot": 0.0,
          "layer": "F.Cu",
          "value": "RK3506 SOM 140 pin",
          "pad_nets": nets,
          "create_nets": True}
    plan = P.propose(path, [op])
    if plan["blocking"]:
        raise SystemExit("castellation is blocking: %s"
                         % [b.get("problem") or b for b in plan["blocking"]])
    res = P.apply_plan(plan["plan_id"], confirm=True,
                       options={"allow_new_violations": True})
    print("J1 placed: %s" % json.dumps(res.get("gates_passed"), ensure_ascii=False))
    return res


def planes(path=BOARD):
    """Ground planes, poured by KiCad's own ZONE_FILLER (PLANE_LAYERS_NOW)."""
    from pcb_agent_mcp.fidelity import fill as F

    pts = plane_outline()
    out = {}
    for layer in PLANE_LAYERS_NOW:
        res = F.add_zone(path, "GND", layer, pts, priority=0, clearance=0.25,
                         min_thickness=0.25, connect="thermal",
                         fill_it=True, verify_drc=True)
        if str(res.get("status")) != "ok":
            # Report the refusal and carry on: one layer that cannot be poured
            # must not hide the reading for the layer that can.
            out[layer] = {"error": str(res.get("reason_code")),
                          "message": str(res.get("message"))[:200]}
            print("%s REFUSED: %s" % (layer, json.dumps(out[layer], ensure_ascii=False)))
            continue
        body = res.get("value") or {}
        out[layer] = {"outline_mm2": body.get("outline_area_mm2"),
                      "fill_area_mm2": (body.get("filled") or {}).get("fill_area_mm2"),
                      "poured_zones": (body.get("filled") or {}).get("poured_zones"),
                      "drc": (body.get("filled") or {}).get("drc_after_fill")}
        print("%s poured: %s" % (layer, json.dumps(out[layer], ensure_ascii=False)))
    return out


def check(path=BOARD):
    """Everything KiCad can say about the file, plus the frozen facts."""
    from pcb_agent_mcp.fidelity import engine as E
    from pcb_agent_mcp.fidelity import kicad_doc as KD
    from pcb_agent_mcp.fidelity import fill as F

    doc = KD.KiCadDocument.from_path(path)
    board = doc.board()
    st = E.stats(path)
    drc = E.drc(path)
    copper = F.board_copper_layers(board)
    report = {
        "path": path,
        "bytes": os.path.getsize(path),
        "layers": len(board.layers or []) if hasattr(board, "layers") else None,
        "stats": {k: (st.get("board") or {}).get(k)
                  for k in ("width", "height", "board_thickness", "has_outline")},
        "drc_violations": drc.get("violations_total"),
        "drc_by_type": drc.get("by_type"),
        "zones": len(doc.items("zone")),
        "footprints": len(doc.items("footprint")),
        "nets": len(doc.items("net")) - 1,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def main(argv=None):
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--create", action="store_true")
    g.add_argument("--castellation", action="store_true")
    g.add_argument("--planes", action="store_true")
    g.add_argument("--check", action="store_true")
    ap.add_argument("--path", default=BOARD)
    a = ap.parse_args(argv)
    if a.create:
        create(a.path)
    elif a.castellation:
        castellation(a.path)
    elif a.planes:
        planes(a.path)
    else:
        check(a.path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
