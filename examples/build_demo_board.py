"""Build a complete demo board through the write path, with KiCad as the judge.

Nothing here is hand-edited: the board is created by `create_board`, then every
feature (slot, mounting hole, mechanical outline, silkscreen, nets, tracks, via,
keep-out, copper pour) is added by `propose` -> `apply_plan`. Each apply has to
pass the four gates, and the engine gate means KiCad itself re-read both files.

Run from the repo root:

    PYTHONPATH=src python examples/build_demo_board.py

The output lands in `examples/demo_board/` - never beside a user's project.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import pcb_agent_mcp.edit as E

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "demo_board")
BOARD = os.path.join(OUT, "demo60x40.kicad_pcb")

SPEC = {
    "width_mm": 60.0,
    "height_mm": 40.0,
    "copper_layers": 4,
    "thickness_mm": 1.6,
    "clearance_mm": 0.2,
    "trace_width_mm": 0.25,
    "via_dia_mm": 0.6,
    "via_drill_mm": 0.3,
    "title": "pcb_agent_mcp demo 60x40 4-layer",
    "overwrite": True,
}

# One proposal per stage so that a failure names the stage rather than a line
# number in a 30-operation list.
STAGES = [
    ("mechanical", [
        {"op": "add_mech_rect", "x0": 2, "y0": 2, "x1": 58, "y1": 38,
         "layer": "Dwgs.User", "width": 0.15},
        {"op": "add_board_text", "text": "DEMO-60x40-4L", "x": 12.5, "y": 34.5,
         "layer": "F.SilkS", "height": 1.6, "thickness": 0.16},
        {"op": "add_board_text", "text": "REV A", "x": 44, "y": 34.5,
         "layer": "F.SilkS", "height": 1.6, "thickness": 0.16},
    ]),
    ("holes", [
        {"op": "add_slot", "x1": 8, "y1": 14, "x2": 26, "y2": 14, "slot_width": 3.2},
        {"op": "add_cutout_circle", "cx": 48, "cy": 10, "r": 2.5},
    ]),
    ("copper", [
        {"op": "add_net", "name": "VCC"},
        # A closed loop, because a track end that lands on nothing is a real
        # `track_dangling` violation and KiCad's DRC blocks the apply for it.
        # A via alone does not terminate an end on the other layer: the loop
        # needs a return path. (A copper pour does NOT count either - its fill
        # is computed at open/plot time, so DRC still sees a dangling end.)
        {"op": "add_via", "x": 20, "y": 20, "size": 0.6, "drill": 0.3, "net": "VCC"},
        {"op": "add_via", "x": 40, "y": 20, "size": 0.6, "drill": 0.3, "net": "VCC"},
        {"op": "add_track", "x1": 20, "y1": 20, "x2": 40, "y2": 20,
         "width": 0.4, "layer": "F.Cu", "net": "VCC"},
        {"op": "add_track", "x1": 40, "y1": 20, "x2": 20, "y2": 20,
         "width": 0.4, "layer": "B.Cu", "net": "VCC"},
        {"op": "add_keepout", "x0": 8, "y0": 4, "x1": 16, "y1": 12,
         "layer": "F.Cu", "rules": ["tracks", "vias"]},
        {"op": "add_zone", "net": "VCC", "layer": "B.Cu",
         "points": [[2, 2], [58, 2], [58, 38], [2, 38]], "clearance": 0.25},
    ]),
]


def main():
    if not os.path.isdir(OUT):
        os.makedirs(OUT, exist_ok=True)
    created = E.create_board(BOARD, SPEC)
    report = {"board": BOARD, "create": {"bytes": created["bytes"],
                                         "gates": created["gates"]}, "stages": []}

    for name, ops in STAGES:
        t0 = time.time()
        prop = E.propose(BOARD, ops)
        applied = E.apply_plan(prop["plan_id"], confirm=True)
        report["stages"].append({
            "stage": name,
            "ops": [o["op"] for o in ops],
            "blocking": prop["blocking"],
            "warnings": prop["warnings"],
            "gates": applied["gates"],
            "gates_passed": applied["gates_passed"],
            "decision": applied["decision"],
            "seconds": round(time.time() - t0, 2),
            "written": applied["written"],
            "bytes": applied["gates"]["bytes_written"]["bytes"],
            "sha256_after": applied["sha256_after"],
        })
        print("[stage] %-12s ops=%d gates_passed=%s"
              % (name, len(ops), ",".join(applied["gates_passed"])), flush=True)

    # Final independent check: the digest of the finished board.
    from pcb_agent_mcp.fidelity import kicad_doc as K
    from pcb_agent_mcp.fidelity import views as V
    doc = K.KiCadDocument.from_path(BOARD)
    report["final"] = {"stats": doc.stats(),
                       "digest": E.checks.digest(doc),
                       "coverage": doc.coverage(V.INTERPRETED_HEADS),
                       "verify": doc.verify()}
    with open(os.path.join(OUT, "build-report.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)
    print(json.dumps({"counts": report["final"]["digest"]["counts"],
                      "outline": report["final"]["digest"]["outline"]["message"],
                      "share_with_a_reading_rule":
                          report["final"]["coverage"]["share_with_a_reading_rule"],
                      "token_identity": report["final"]["verify"]["token_identity"],
                      "seconds": [s.get("seconds") for s in report["stages"]]},
                     ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
