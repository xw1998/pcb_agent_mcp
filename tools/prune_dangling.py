#!/usr/bin/env python3
"""Delete the copper stubs that KiCad itself calls dangling.  Nothing here is a
route.

Deleting duplicate copper leaves the odd short survivor: two pieces were drawn on
top of each other, the redundant one goes, and what is left is a 0.05 mm speck
whose far end touches nothing.  It is not a clearance problem and it does not
change the net, but it is copper that goes nowhere, DRC reports `track_dangling`,
and on screen it reads as a burr sitting a hair off the joint it used to
continue.

The judgement of *which* end is dangling is not made here.  It is read out of a
`kicad-cli pcb drc --format json` report, because the board runs a great many
0.05 mm staircase pieces whose centrelines never meet while their copper
overlaps -- an audit that judged connectivity on centrelines alone flagged 80 of
them on this board and deleting all 80 took the unconnected count from 8 to 56.
The engine already knows the difference; this tool only acts on what it says.

A flagged track is still only pruned when all of the following hold:

  * its length is at most --max-len (default 0.25 mm): a long run with a dead end
    is a routing decision, not a leftover,
  * it is a track, not an arc or a via,
  * the board's unconnected count does not go up when it is removed.

    kicad-cli pcb drc --severity-all --format json --output drc.json BOARD
    python tools/prune_dangling.py BOARD --drc drc.json [--apply OUT]
"""
import argparse
import json
import math
import sys

import pcbnew


def _uuid(obj):
    for getter in ("GetUuid", "m_Uuid"):
        try:
            u = getattr(obj, getter)
            u = u() if callable(u) else u
            return str(u.AsString())
        except Exception:
            continue
    return None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("board")
    ap.add_argument("--drc", required=True, metavar="JSON")
    ap.add_argument("--apply", metavar="OUT", default=None)
    ap.add_argument("--max-len", type=float, default=0.25)
    a = ap.parse_args(argv)

    report = json.load(open(a.drc))
    flagged = {}
    for v in report.get("violations", []):
        if v.get("type") != "track_dangling":
            continue
        for it in v.get("items", []):
            u = it.get("uuid")
            if u:
                flagged[u] = it.get("description", "")

    b = pcbnew.LoadBoard(a.board)
    b.BuildConnectivity()
    uc0 = b.GetConnectivity().GetUnconnectedCount(False)

    drop = []
    for t in b.GetTracks():
        if t.Type() != pcbnew.PCB_TRACE_T:
            continue
        u = _uuid(t)
        if u not in flagged:
            continue
        st, en = t.GetStart(), t.GetEnd()
        L = math.hypot((en.x - st.x) / 1e6, (en.y - st.y) / 1e6)
        if L > a.max_len:
            print("kept   %-16s %-7s %.3f mm : DRC flags it but it is longer "
                  "than %.2f mm, so the dead end is a routing decision"
                  % (t.GetNetname(), b.GetLayerName(t.GetLayer()), L, a.max_len))
            continue
        drop.append(t)
        print("prune  %-16s %-7s %.4f mm  (%.3f,%.3f)->(%.3f,%.3f)"
              % (t.GetNetname(), b.GetLayerName(t.GetLayer()), L,
                 st.x / 1e6, st.y / 1e6, en.x / 1e6, en.y / 1e6))

    print("DRC-dangling items %d   to prune %d   unconnected %d"
          % (len(flagged), len(drop), uc0))
    if not a.apply:
        print("measured only; pass --apply OUT to write")
        return 0
    if not drop:
        print("nothing to prune; board left untouched")
        return 0
    for t in drop:
        b.Remove(t)
    b.BuildConnectivity()
    uc1 = b.GetConnectivity().GetUnconnectedCount(False)
    print("unconnected %d -> %d" % (uc0, uc1))
    if uc1 > uc0:
        raise SystemExit("REFUSED: pruning made the board less connected "
                         "(%d -> %d); the stub was carrying something"
                         % (uc0, uc1))
    b.Save(a.apply)
    print("saved %s  (pruned %d stubs)" % (a.apply, len(drop)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
