# Nothing here is a router.  This tool reads a finished board and reports copper
# length per DDR net, grouped by the four rules in priority order:
#   1. intra-pair skew (DQS0/DQS1/CLK P-N)          - tightest
#   2. within-byte-lane (DQ0-7+DM0 vs DQS0, ...)    - next
#   3. address/command/control vs CLK               - next
#   4. inter-lane (lane0 vs lane1)                  - loosest
# A meander can only add copper, so the reference of a group is its longest
# member: a net that is too long is a re-route, not a tweak.
import json, sys
from pcb_agent_mcp.fidelity import load
from pcb_agent_mcp.edit import checks

def main(argv):
    if len(argv) < 2:
        print("usage: check_length.py <board.kicad_pcb> [tol_pair tol_lane tol_cmd]")
        return 2
    board = argv[1]
    tp = float(argv[2]) if len(argv) > 2 else 0.25
    tl = float(argv[3]) if len(argv) > 3 else 0.5
    tc = float(argv[4]) if len(argv) > 4 else 1.0
    doc = load(board, fmt="kicad_pcb")
    res = checks.length_match(doc, tol_pair=tp, tol_lane=tl, tol_cmd=tc)
    print(json.dumps(res, indent=1, ensure_ascii=False))
    return 0 if res["ok"] else 1

if __name__ == "__main__":
    sys.exit(main(sys.argv))
