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
SCHEMATIC = os.path.join(EXAMPLES, "rk3506_som.kicad_sch")

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


def schematic_pad_names():
    """{(ref, pin): net name} straight out of the schematic netlist.

    The schematic, not the CSV, is the authority for a net's *name*: it is one
    root sheet, so KiCad writes every net as "/NAME". The CSV writes plain
    "NAME". Feeding the CSV names straight to the pad made two nets out of one
    rail -- measured on the 0.1.10 board, /+5V_IN (12 pads) and +5V_IN (4 stamp
    pads), with the same split on +3V3. Eight stamp pads were islanded.
    """
    from pcb_agent_mcp.fidelity import engine
    res = engine.sch_netlist(SCHEMATIC)
    if str(res.get("status")) != "ok":
        raise SystemExit("the schematic will not netlist: %s" % res.get("message"))
    out = {}
    for net in res["nets"]:
        for ref, pin in net["nodes"]:
            out[(ref, str(pin))] = net["name"]
    return out

def stamp_pad_nets():
    """{pad number: net} for J1: the CSV says *which* net, the schematic says
    what it is called. A name that no schematic net backs is refused, not
    prefixed by guesswork."""
    sch = schematic_pad_names()
    out = {}
    for row in read_csv("netmap_terminals.csv"):
        if row["device"] != "STAMP":
            continue
        pin, plain = row["pin"], row["net"]
        named = [n for (r, p), n in sch.items()
                 if r == "J1" and p == pin]
        if not named:
            raise SystemExit("stamp pin %s (%s) is not in the schematic netlist"
                             % (pin, plain))
        if len(named) > 1:
            raise SystemExit("stamp pin %s is on %d schematic nets (%s)"
                             % (pin, len(named), ", ".join(sorted(named))))
        if named[0].lstrip("/") != plain:
            raise SystemExit("stamp pin %s: CSV says %r, the schematic says %r"
                             % (pin, plain, named[0]))
        out[pin] = named[0]
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


def ground_net_name():
    """The name the schematic gives ground. A pour on the un-prefixed "GND"
    would be a *different net* from every pad the schematic placed (measured:
    the In1 pour sat on net "GND" while all 228 ground pads were on "/GND"), so
    the plane is named from the netlist, not from a constant."""
    sch = schematic_pad_names()
    names = sorted({n for n in sch.values() if n.lstrip("/") == "GND"})
    if len(names) != 1:
        raise SystemExit("the schematic names ground %r, expected exactly one"
                         % (names,))
    return names[0]

def planes(path=BOARD):
    """Ground planes, poured by KiCad's own ZONE_FILLER (PLANE_LAYERS_NOW)."""
    from pcb_agent_mcp.fidelity import fill as F

    pts = plane_outline()
    ground = ground_net_name()
    out = {}
    for layer in PLANE_LAYERS_NOW:
        res = F.add_zone(path, ground, layer, pts, priority=0, clearance=0.25,
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


def netnames(path=BOARD):
    """Re-point any pad whose net name the schematic spells differently.

    This is the repair for the split described in schematic_pad_names(): only
    pads whose current name is the un-prefixed twin of a schematic name move.
    Anything else is reported and left alone -- a rename this step cannot back
    with the netlist is exactly the kind of quiet edit that hides a defect.
    """
    from pcb_agent_mcp.edit import plan as P
    from pcb_agent_mcp.fidelity import engine
    S = __import__("pcb_agent_mcp.fidelity", fromlist=["sexpr"]).sexpr

    doc = S.parse(open(path, encoding="utf-8").read())
    board = doc.items[0]
    names = {}
    for n in S.children(board, "net"):
        names[S.arg_num(n, 1)] = S.arg_text(n, 2)
    have = set(names.values())

    sch = schematic_pad_names()
    ops, moved, skipped = [], [], []
    for fp in S.children(board, "footprint"):
        ref = None
        for t in S.children(fp, "fp_text"):
            if S.arg_text(t, 1) == "reference":
                ref = S.arg_text(t, 2)
        if ref is None:
            continue
        for pad in S.children(fp, "pad"):
            num = S.arg_text(pad, 1)
            nn = S.child(pad, "net")
            if nn is None:
                continue
            cur = names.get(S.arg_num(nn, 1), "")
            want = sch.get((ref, str(num)))
            if want is None or want == cur:
                continue
            if want.lstrip("/") != cur:
                skipped.append("%s.%s keeps %r (schematic %r)" % (ref, num, cur, want))
                continue
            if want not in have:
                skipped.append("%s.%s wants %r, which is not a board net"
                               % (ref, num, want))
                continue
            ops.append({"op": "set_pad_net", "ref": ref, "pad": str(num),
                        "net": want, "create_nets": False})
            moved.append({"ref": ref, "pad": str(num), "from": cur, "to": want})

    print(json.dumps({"moved": moved, "skipped": skipped}, ensure_ascii=False,
                     indent=2))
    if not ops:
        return {"moved": [], "skipped": skipped}
    plan = P.propose(path, ops)
    if plan.get("blocking"):
        raise SystemExit("blocking: %s" % json.dumps(plan["blocking"])[:300])
    # A rename moves no copper: the pad sits where it sat. The "new violation"
    # counter still fires because a violation is keyed by the offending net
    # pair, and the pair's name changed -- so it is accepted here and the DRC
    # total is compared before/after instead of trusted on its own.
    before = engine.drc(path).get("violations_total")
    res = P.apply_plan(plan["plan_id"], confirm=True,
                       options={"allow_new_violations": True})
    after = engine.drc(path).get("violations_total")
    print("DRC before/after", before, after)
    print("applied", len(ops), "pad net repairs; gates",
          json.dumps(res.get("gates_passed"), ensure_ascii=False))
    return res

def prune_nets(path=BOARD):
    """Drop net-table rows nothing references, and renumber what is left.

    Why this is a table rewrite and not `delete_net`: deleting one net makes the
    editor renumber the rest, and the pads that were written by an earlier step
    keep their old code -- measured, 66 phantom clearance errors at stamp pads
    that were fine before. Here the whole table is rebuilt at once and every
    reference is remapped in the same pass, so there is no moment where the file
    is half-renumbered. Codes must stay sequential: a table with gaps makes
    kicad-cli produce no report at all (measured).

    The unused rows are the un-prefixed names the castellation step used to
    write (see schematic_pad_names). Nothing that carries copper is touched: a
    code is kept when any pad/zone/track/via/arc names it.
    """
    from pcb_agent_mcp.fidelity import engine
    S = __import__("pcb_agent_mcp.fidelity", fromlist=["sexpr"]).sexpr

    before_drc = engine.drc(path).get("violations_total")
    before_pads = _pad_net_names(path)
    doc = S.parse(open(path, encoding="utf-8").read())
    board = doc.items[0]

    used = set()

    def walk(n, depth):
        if isinstance(n, S.Seq):
            if S.head(n) == "net" and depth > 1:
                used.add(S.arg_num(n, 1))
            for it in n.items:
                walk(it, depth + 1)

    walk(board, 0)

    rows = S.children(board, "net")
    keep = [r for r in rows if S.arg_num(r, 1) in used or S.arg_text(r, 2) == ""]
    dropped = [S.arg_text(r, 2) for r in rows if r not in keep]
    remap = {S.arg_num(r, 1): i for i, r in enumerate(keep)}
    drop_ids = set(id(r) for r in rows if r not in keep)
    board.items = [it for it in board.items
                   if not (isinstance(it, S.Seq) and S.head(it) == "net"
                           and id(it) in drop_ids)]

    def renumber(n, depth):
        if isinstance(n, S.Seq):
            if S.head(n) == "net" and depth > 1:
                old = S.arg_num(n, 1)
                if old in remap:
                    n.items[1] = S.Atom("%d" % remap[old])
            for it in n.items:
                renumber(it, depth + 1)

    for r in keep:
        r.items[1] = S.Atom("%d" % remap[S.arg_num(r, 1)])
    for it in board.items:
        renumber(it, 1)

    open(path, "w", encoding="utf-8", newline="\n").write(
        S.dumps(doc.items[0]) + "\n")

    after_drc = engine.drc(path).get("violations_total")
    after_pads = _pad_net_names(path)
    changed = {k: (before_pads.get(k), after_pads.get(k))
               for k in before_pads if before_pads.get(k) != after_pads.get(k)}
    report = {"dropped": len(dropped), "kept": len(keep),
              "drc_before": before_drc, "drc_after": after_drc,
              "pad_net_names_changed": len(changed)}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if after_drc != before_drc or changed:
        raise SystemExit("prune changed the board: %s"
                         % json.dumps(list(changed.items())[:5], ensure_ascii=False))
    return report

def _pad_net_names(path):
    from pcb_agent_mcp.fidelity import sexpr as S
    doc = S.parse(open(path, encoding="utf-8").read())
    names = {S.arg_num(n, 1): S.arg_text(n, 2)
             for n in S.children(doc.items[0], "net")}
    out = {}
    for fp in S.children(doc.items[0], "footprint"):
        ref = None
        for t in S.children(fp, "fp_text"):
            if S.arg_text(t, 1) == "reference":
                ref = S.arg_text(t, 2)
        for pad in S.children(fp, "pad"):
            nn = S.child(pad, "net")
            key = "%s.%s" % (ref, S.arg_text(pad, 1))
            out[key] = names.get(S.arg_num(nn, 1), "") if nn is not None else ""
    return out

def main(argv=None):
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--create", action="store_true")
    g.add_argument("--castellation", action="store_true")
    g.add_argument("--planes", action="store_true")
    g.add_argument("--netnames", action="store_true")
    g.add_argument("--prune-nets", action="store_true")
    g.add_argument("--check", action="store_true")
    ap.add_argument("--path", default=BOARD)
    a = ap.parse_args(argv)
    if a.create:
        create(a.path)
    elif a.castellation:
        castellation(a.path)
    elif a.planes:
        planes(a.path)
    elif a.netnames:
        netnames(a.path)
    elif a.prune_nets:
        prune_nets(a.path)
    else:
        check(a.path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
