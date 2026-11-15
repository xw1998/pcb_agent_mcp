#!/usr/bin/env python3
"""One edit, then the same five readings, compared to the previous ones.

Every time this repository changed a board by hand, the same sequence was run by
hand afterwards: refill the pours with KiCad's own ZONE_FILLER, run
`kicad-cli pcb drc --severity-all`, count unconnected items, count via-in-pad
strictly, and check the two gates.  Running it by hand is not just slow, it is
where the mistakes came from: a reading taken on a board whose pours had not been
refilled since the edit, or a DRC count taken in a different working directory
(the 11 `lib_footprint_issues` artefact is a function of cwd, not of the board).

This file makes that sequence one command with a machine-readable answer, and it
can roll the board back when a change makes something worse.  It is a loop, not a
designer: it never draws copper, never moves a part, and has no opinion about
what the edit should be.

MUST run under KiCad's own Python (pcbnew is not importable elsewhere):

    env -u PYTHONPATH -u PYTHONHOME "D:/KiCad/10.0/bin/python.exe" -u \
        tools/board_loop.py --board _w.kicad_pcb --baseline _w.base.json \
        --revert-on-regression

The first run with no baseline writes the baseline; later runs read it and report
`verdict` in {ok, regressed, improved}.  A regression restores `--board` from the
backup made at entry, so a batch of edits cannot leave the board worse than the
last known state.

Watched keys, and why each one is watched:

  drc_error        every real violation, i.e. all types except the cwd artefact
  unconnected      nets that are actually not connected (7 on the delivery board)
  via_in_pad       strict geometry: the drill barrel cutting same-net pad copper
  shape           board_shape_gate verdict (arcs/45/duplicate/loop/dead end)
  place           board_place_gate verdict (hole in pad / courtyard / edge / ...)
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "src"))

#: Violation types that are an artefact of running kicad-cli from the repo
#: directory (no fp-lib-table / lib/ there).  Counted separately so a cwd change
#: cannot look like a board change.
CWD_ARTEFACT = ("lib_footprint_issues",)

KICAD_CLI = os.environ.get("KICAD_CLI", "D:/KiCad/10.0/bin/kicad-cli.exe")


def refill(board_path):
    import pcbnew
    b = pcbnew.LoadBoard(board_path)
    zones = b.Zones()
    if len(zones):
        pcbnew.ZONE_FILLER(b).Fill(zones)
        b.Save(board_path)
    return len(zones)


def count_unconnected(board_path):
    """Ratsnest edges that are not connected.  In KiCad 10 the method lives on
    CONNECTIVITY_DATA and takes `aVisibileOnly` (sic) - calling it on the BOARD
    raises AttributeError, which is what this function used to do."""
    import pcbnew
    b = pcbnew.LoadBoard(board_path)
    b.BuildConnectivity()
    return int(b.GetConnectivity().GetUnconnectedCount(False))


def drc(board_path, report):
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    cmd = [KICAD_CLI, "pcb", "drc", "--severity-all", "--format", "json",
           "--output", report, board_path]
    got = subprocess.run(cmd, capture_output=True, text=True, env=env,
                         cwd=os.path.dirname(os.path.abspath(report)) or ".")
    if got.returncode not in (0, 5):  # 5 = violations found
        raise SystemExit("kicad-cli drc failed (%d): %s" % (got.returncode, got.stderr[:400]))
    with open(report, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    by_type = {}
    for v in data.get("violations") or []:
        t = str(v.get("type") or "?")
        by_type[t] = by_type.get(t, 0) + 1
    real = sum(n for t, n in by_type.items() if t not in CWD_ARTEFACT)
    return {"by_type": by_type, "real": real,
            "artefact": sum(by_type.get(t, 0) for t in CWD_ARTEFACT)}


def gates(board_path):
    from pcb_agent_mcp.adapters import open_board
    from pcb_agent_mcp import shape_gate, place_gate
    b = open_board(board_path)
    # A gate returns the envelope {status, value, unit, model, basis, ...}; the
    # verdict and the rule table live inside `value`, not at the top.  Reading
    # shape.get("verdict") is how this function first reported `None` for both
    # gates on a board that was actually place_fail.
    def verdict(got, rule_id=None):
        val = got.get("value") if isinstance(got.get("value"), dict) else {}
        measured = None
        if rule_id:
            for rule in (val.get("rules") or []):
                if rule.get("id") == rule_id or rule.get("name") == rule_id:
                    measured = int(rule.get("measured") or 0)
        return val.get("verdict"), measured, val.get("blocking")

    s_verdict, _, s_block = verdict(shape_gate.gate(b))
    p_verdict, holes, p_block = verdict(place_gate.gate(b), rule_id="hole_in_pad")
    return {"shape": s_verdict, "place": p_verdict,
            "shape_blocking": s_block, "place_blocking": p_block,
            "via_in_pad": holes or 0}


def counts(board_path):
    import pcbnew
    b = pcbnew.LoadBoard(board_path)
    return {"tracks": len(b.GetTracks()), "zones": len(b.Zones()),
            "footprints": len(b.GetFootprints())}


def read(board_path, report):
    out = {"board": os.path.abspath(board_path)}
    out["zones_filled"] = refill(board_path)
    out.update(counts(board_path))
    out["unconnected"] = count_unconnected(board_path)
    got = drc(board_path, report)
    out["drc"] = got["by_type"]
    out["drc_error"] = got["real"]
    out["drc_artefact"] = got["artefact"]
    out.update(gates(board_path))
    return out


WATCH = ("drc_error", "unconnected", "via_in_pad")


def compare(now, base):
    moved = {}
    for k in WATCH:
        a, b = base.get(k), now.get(k)
        if a is None:
            continue
        if a != b:
            moved[k] = {"from": a, "to": b, "delta": (b - a) if isinstance(a, int) else None}
    worse = [k for k, v in moved.items() if isinstance(v["delta"], int) and v["delta"] > 0]
    better = [k for k, v in moved.items() if isinstance(v["delta"], int) and v["delta"] < 0]
    for key in ("shape", "place"):
        if base.get(key) and now.get(key) and base[key] != now[key]:
            moved[key] = {"from": base[key], "to": now[key]}
    verdict = "regressed" if worse else ("improved" if better else "ok")
    return verdict, moved


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--board", required=True)
    ap.add_argument("--baseline", required=True, help="json of the last accepted readings")
    ap.add_argument("--report", default=None, help="kicad-cli drc json (default: beside board)")
    ap.add_argument("--revert-on-regression", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    report = a.report or (os.path.splitext(a.board)[0] + ".drc.json")
    backup = a.board + ".loop-backup"
    shutil.copy2(a.board, backup)

    now = read(a.board, report)
    if not os.path.exists(a.baseline):
        with open(a.baseline, "w", encoding="utf-8") as fh:
            json.dump(now, fh, indent=2, ensure_ascii=False)
        if a.json:
            print(json.dumps({"verdict": "baseline_written", "now": now}, indent=2,
                             ensure_ascii=False))
        else:
            print("baseline written: %s" % a.baseline)
            _print(now)
        return 0

    with open(a.baseline, "r", encoding="utf-8") as fh:
        base = json.load(fh)
    verdict, moved = compare(now, base)
    reverted = False
    if verdict == "regressed" and a.revert_on_regression:
        shutil.copy2(backup, a.board)
        reverted = True
    elif verdict != "regressed":
        with open(a.baseline, "w", encoding="utf-8") as fh:
            json.dump(now, fh, indent=2, ensure_ascii=False)

    out = {"verdict": verdict, "reverted": reverted, "moved": moved,
           "now": now, "baseline": base}
    if a.json:
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0
    print("verdict: %s%s" % (verdict, "  (board rolled back)" if reverted else ""))
    print()
    _print(now)
    if moved:
        print()
        print("moved:")
        for k, v in moved.items():
            print("  %-12s %s -> %s" % (k, v.get("from"), v.get("to")))
    return 0


def _print(row):
    print("track+via objects %d  zones %d  footprints %d"
          % (row["tracks"], row["zones"], row["footprints"]))
    print("drc real %d (artefact %d)  unconnected %d  via_in_pad %d"
          % (row["drc_error"], row["drc_artefact"], row["unconnected"], row["via_in_pad"]))
    print("shape %s  place %s" % (row["shape"], row["place"]))
    if row["drc"]:
        print("drc by type: %s" % ", ".join("%s=%d" % kv for kv in sorted(row["drc"].items())))


if __name__ == "__main__":
    raise SystemExit(main())
