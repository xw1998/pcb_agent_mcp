"""Calibrate the rule set against a corpus of real, finished boards.

The rule set in `pcb_agent_mcp.ruleset` claims that P0 rules decide whether a
board can be built.  A claim like that is only worth what its corpus says, so
this harness runs the audit over boards that are known to have been built and
whose files are public, and reports every P0 failure with the measured value
and the limit it was compared against.

Two questions, both answered on every board:

* ``no_process``   the fab process is not declared -> P0 must come back
  ``undetermined``.  A guessed number here is how 6 official demo boards got
  called unbuildable in the first calibration pass.
* ``with_process`` the same board with a declared process -> P0 must be
  judged on a number, not deferred forever.

Usage::

    python tools/calibrate_rules.py                       # all demos
    python tools/calibrate_rules.py --only pic_programmer
    python tools/calibrate_rules.py --markdown docs/x.md

The JSON is written after every board, so a killed run keeps its evidence and
a rerun skips what is already there.

Nothing here edits a board, and nothing here produces geometry.  It reads
files, calls the audit, and writes a report.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))

from pcb_agent_mcp.adapters import open_board          # noqa: E402
from pcb_agent_mcp.ruleset import audit               # noqa: E402

DEFAULT_DEMOS = "D:/KiCad/10.0/share/kicad/demos"
DEFAULT_PROCESS = {"layers": 2, "copper_oz": 1}


def _level_rows(result):
    """Verdict summary for one audit call, with the numbers behind each fail."""
    levels = result["levels"]
    out = {"verdict": result["verdict"],
           "blocking_level": result["priority"]["blocking_level"],
           "flagged": [f["rule"] for f in result["priority"]["flagged"]],
           "levels": {}}
    for name, lvl in levels.items():
        checks = lvl.get("checks") or []
        row = {"status": lvl["status"],
               "graded": lvl.get("graded_state"),
               "failed": [], "undetermined": []}
        for c in checks:
            if c.get("state") == "fail":
                item = {"rule": c["id"], "level": c.get("level")}
                for k in ("measured", "limit", "recommended", "margin",
                          "op", "key", "measured_from", "evidence"):
                    if c.get(k) is not None:
                        item[k] = c[k]
                if c.get("where"):
                    item["where"] = c["where"]
                row["failed"].append(item)
            elif c.get("state") == "undetermined":
                row["undetermined"].append(c["id"])
        out["levels"][name] = row
    return out


def _boards(demos, only):
    pages = {}
    for p in sorted(glob.glob(os.path.join(demos, "*", "*.kicad_pcb"))):
        name = os.path.basename(os.path.dirname(p))
        if only and only.lower() not in (name + os.path.basename(p)).lower():
            continue
        pages[name + "/" + os.path.basename(p)] = p
    return pages


def run(demos, out_path, process, only=None):
    done = {}
    if os.path.isfile(out_path):
        try:
            done = {r["board"]: r for r in json.load(open(out_path,
                                                         encoding="utf-8"))}
        except ValueError:
            done = {}
    for board, path in _boards(demos, only).items():
        if board in done and done[board].get("detail"):
            continue
        t = time.time()
        try:
            b = open_board(path)
            rec = {"board": board, "ok": True, "detail": True,
                   "no_process": _level_rows(audit(b)),
                   "with_process": _level_rows(audit(b, process=dict(process)))}
        except Exception as exc:                              # noqa: BLE001
            rec = {"board": board, "ok": False, "detail": True,
                   "err": "%s: %s" % (type(exc).__name__, exc)}
        rec["sec"] = round(time.time() - t, 1)
        done[board] = rec
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(list(done.values()), fh, ensure_ascii=False, indent=1)
        print("%-56s %-34s %-30s %6.1fs"
              % (board, rec["no_process"]["verdict"] if rec["ok"] else rec["err"],
                 rec["with_process"]["verdict"] if rec["ok"] else "",
                 rec["sec"]), flush=True)
    return done


def _md(done, process):
    lines = ["| board | no process declared | process declared "
             "(2 layers, 1 oz) | P0 fail (rule, measured / limit) |",
             "|---|---|---|---|"]
    for rec in sorted(done.values(), key=lambda r: r["board"]):
        if not rec.get("ok"):
            lines.append("| `%s` | run failed: %s | — | — |"
                         % (rec["board"], rec.get("err")))
            continue
        p0 = rec["with_process"]["levels"].get("P0", {})
        fails = p0.get("failed") or []
        cells = []
        for f in fails:
            n = "%s %s / %s" % (f["rule"], f.get("measured"), f.get("limit"))
            if f.get("measured_from"):
                n += " (%s)" % f["measured_from"]
            cells.append(n)
        lines.append("| `%s` | %s | %s | %s |"
                     % (rec["board"],
                        rec["no_process"]["verdict"],
                        rec["with_process"]["verdict"],
                        "<br>".join(cells) or "—"))
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--demos", default=DEFAULT_DEMOS)
    ap.add_argument("--out", default="_calib_detail.json")
    ap.add_argument("--markdown", help="also write the summary table here")
    ap.add_argument("--only", help="substring match on board name")
    ap.add_argument("--layers", type=int, default=DEFAULT_PROCESS["layers"])
    ap.add_argument("--oz", type=float, default=DEFAULT_PROCESS["copper_oz"])
    a = ap.parse_args(argv)
    process = {"layers": a.layers, "copper_oz": a.oz}
    done = run(a.demos, a.out, process, a.only)
    table = _md(done, process)
    if a.markdown:
        with open(a.markdown, "a", encoding="utf-8") as fh:
            fh.write(table + "\n")
    else:
        print(table)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
