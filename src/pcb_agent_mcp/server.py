"""Minimal MCP server over stdio, standard library only.

Why not the official SDK: the Calc layer is pure arithmetic and the goal is
zero external components. This speaks newline-delimited JSON-RPC 2.0, which is
what the MCP stdio transport is. Handlers live in tools.py, so swapping in the
official SDK later is a thin adapter, not a rewrite.
"""
import json
import os
import sys
import zipfile

from . import __version__
from . import tools as _tools

SERVER_NAME = "pcb-agent-mcp"
SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
DEFAULT_PROTOCOL = "2025-06-18"


def _log(msg):
    # Never write to stdout: it carries the protocol.
    sys.stderr.write("[pcb-agent-mcp] %s\n" % msg)
    sys.stderr.flush()


def _ok(msg_id, result):
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id, code, message, data=None):
    e = {"code": code, "message": message}
    if data is not None:
        e["data"] = data
    return {"jsonrpc": "2.0", "id": msg_id, "error": e}


def handle(request):
    """Return a response dict, or None for notifications."""
    method = request.get("method")
    msg_id = request.get("id")
    params = request.get("params") or {}

    if msg_id is None:
        # notification: initialized / cancelled / anything else -> no response
        return None

    if method == "initialize":
        asked = params.get("protocolVersion")
        version = asked if asked in SUPPORTED_PROTOCOLS else DEFAULT_PROTOCOL
        return _ok(msg_id, {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": __version__},
            "instructions": (
                "Calc layer for PCB design. Every numeric answer carries model, "
                "basis, precision and assumptions. Some tools refuse to answer "
                "unless the governing standard is named or its data table is loaded - "
                "do not substitute your own memorised value when that happens."
            ),
        })

    if method == "ping":
        return _ok(msg_id, {})

    if method == "tools/list":
        return _ok(msg_id, {"tools": _tools.list_tools()})

    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        result = _tools.call_tool(name, args)
        is_error = result.get("status") == "error"
        return _ok(msg_id, {
            "content": [{"type": "text", "text": _tools.result_to_text(result)}],
            "isError": is_error,
        })

    if method in ("resources/list",):
        return _ok(msg_id, {"resources": []})
    if method in ("prompts/list",):
        return _ok(msg_id, {"prompts": []})

    return _error(msg_id, -32601, "method not found: %s" % method)


def serve(stdin=None, stdout=None):
    stdin = stdin or sys.stdin.buffer
    stdout = stdout or sys.stdout.buffer
    for raw in stdin:
        line = raw.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            payload = _error(None, -32700, "parse error: %s" % exc)
            stdout.write((json.dumps(payload) + "\n").encode("utf-8"))
            stdout.flush()
            continue
        response = handle(request)
        if response is not None:
            stdout.write((json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8"))
            stdout.flush()


def selftest():
    """Protocol-level smoke test without any client."""
    checks = []

    def check(name, cond, detail=""):
        checks.append((name, bool(cond), detail))

    r = handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-06-18"}})
    check("initialize", r["result"]["protocolVersion"] == "2025-06-18")
    check("serverInfo", r["result"]["serverInfo"]["name"] == SERVER_NAME)

    r = handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
    check("notification has no response", r is None)

    r = handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = [t["name"] for t in r["result"]["tools"]]
    check("tools/list count >= 10", len(names) >= 10, ",".join(names))

    r = handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {"name": "width_for_current",
                           "arguments": {"current_a": 1, "standard": "ipc2221"}}})
    payload = json.loads(r["result"]["content"][0]["text"])
    check("width_for_current 1A ~ 0.30mm", abs(payload["value"] - 0.302) < 0.005,
          str(payload["value"]))
    check("value carries basis", bool(payload.get("basis")))

    r = handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                "params": {"name": "width_for_current", "arguments": {"current_a": 1}}})
    payload = json.loads(r["result"]["content"][0]["text"])
    check("missing standard refused", payload["status"] == "error"
          and r["result"]["isError"] is True, payload.get("reason_code"))

    r = handle({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                "params": {"name": "creepage_required",
                           "arguments": {"voltage_v": 250,
                                         "safety_standard": "IEC 60664-1"}}})
    payload = json.loads(r["result"]["content"][0]["text"])
    check("creepage fails closed", payload["status"] == "error"
          and payload["reason_code"] == "SAFETY_TABLE_MISSING")

    r = handle({"jsonrpc": "2.0", "id": 6, "method": "tools/call",
                "params": {"name": "diff_impedance",
                           "arguments": {"width_mm": 0.2, "spacing_mm": 0.2,
                                         "dielectric_mm": 0.2}}})
    payload = json.loads(r["result"]["content"][0]["text"])
    check("diff_impedance 0.2/0.2/0.2 ~ 107.8 ohm", abs(payload["value"] - 107.8) < 0.2,
          str(payload.get("value")))

    r = handle({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                "params": {"name": "diff_width",
                           "arguments": {"target_ohm": 100, "spacing_mm": 0.2,
                                         "dielectric_mm": 0.2}}})
    payload = json.loads(r["result"]["content"][0]["text"])
    check("diff_width round-trips 100 ohm",
          abs(payload.get("zdiff_recomputed", 0) - 100.0) < 0.2,
          str(payload.get("value")))

    # --- antenna / feedline / link. Four numbers that are checked against the
    # world rather than against the code: a 50 ohm trace on FR4, a 2.4 GHz patch,
    # a free-space loss value whose constant is easy to get wrong by 60 dB, and a
    # match that has to recombine to 50 ohm.
    r = handle({"jsonrpc": "2.0", "id": 70, "method": "tools/call",
                "params": {"name": "feedline_width",
                           "arguments": {"target_ohm": 50, "kind": "microstrip",
                                         "dielectric_mm": 1.6, "er": 4.3}}})
    payload = json.loads(r["result"]["content"][0]["text"])
    check("50 ohm microstrip on 1.6mm FR4 is ~3.1 mm wide",
          payload.get("status") == "ok" and abs(payload["value"]["width_mm"] - 3.1367) < 0.01,
          str((payload.get("value") or {}).get("width_mm")))

    r = handle({"jsonrpc": "2.0", "id": 71, "method": "tools/call",
                "params": {"name": "antenna_patch",
                           "arguments": {"freq_ghz": 2.45, "er": 4.4,
                                         "height_mm": 1.6, "tan_delta": 0.02}}})
    payload = json.loads(r["result"]["content"][0]["text"])
    v = payload.get("value") or {}
    check("2.45 GHz FR4 patch is ~37.2 x 28.8 mm",
          payload.get("status") == "ok" and abs(v.get("width_mm", 0) - 37.234) < 0.05
          and abs(v.get("length_mm", 0) - 28.808) < 0.05,
          "%s x %s" % (v.get("width_mm"), v.get("length_mm")))
    _nc = payload.get("not_computed") or v.get("not_computed") or []
    check("an antenna answer lists what it cannot compute",
          isinstance(_nc, list) and "pattern" in " ".join(_nc).lower(),
          str(_nc[:2]))

    r = handle({"jsonrpc": "2.0", "id": 72, "method": "tools/call",
                "params": {"name": "link_friis",
                           "arguments": {"tx_dbm": 0, "freq_mhz": 2400,
                                         "distance_m": 1}}})
    payload = json.loads(r["result"]["content"][0]["text"])
    _fspl = (payload.get("value") or {}).get("path_loss_db")
    check("free-space loss at 2400 MHz / 1 m is 40.05 dB (not 100.05)",
          _fspl is not None and abs(_fspl - 40.052) < 0.01,
          str(_fspl))

    r = handle({"jsonrpc": "2.0", "id": 73, "method": "tools/call",
                "params": {"name": "match_l",
                           "arguments": {"z_load": "25+0j", "freq_ghz": 2.4}}})
    payload = json.loads(r["result"]["content"][0]["text"])
    sols = ((payload.get("value") or {}).get("solutions") or [])
    check("every L-match solution recombines to 50 ohm",
          bool(sols) and all(abs(s["resulting_z_ohm"]["real"] - 50.0) < 0.05
                             and s["residual_vswr"] < 1e-3 for s in sols),
          str([s.get("residual_vswr") for s in sols]))

    # --- simulation layer. These four numbers are checked against the textbook
    # (not against this code): a diode drop from the Shockley equation, a copper
    # bar, a 1-D thermal resistance, and a chain drop that must NOT be computed
    # from an averaged width.
    r = handle({"jsonrpc": "2.0", "id": 74, "method": "tools/call",
                "params": {"name": "sim_spice",
                           "arguments": {
                               "netlist": "* selftest\nV1 1 0 5\nR1 1 2 1k\n"
                                          "D1 2 0 dmod\n.model dmod D(IS=1e-14)\n"
                                          ".op\n.end\n"}}})
    payload = json.loads(r["result"]["content"][0]["text"])
    _vd = ((payload.get("value") or {}).get("nodes_v") or {}).get("2")
    check("SPICE diode at 4.3 mA sits at 0.6925 V",
          payload.get("status") == "ok" and _vd is not None
          and abs(_vd - 0.692543633) < 1e-5, str(_vd))

    r = handle({"jsonrpc": "2.0", "id": 75, "method": "tools/call",
                "params": {"name": "trace_resistance",
                           "arguments": {"length_mm": 1, "width_mm": 0.2,
                                         "thickness_um": 35}}})
    payload = json.loads(r["result"]["content"][0]["text"])
    _r = ((payload.get("value") or {}).get("resistance_ohm"))
    check("1 mm of 0.2 mm 35 um copper is 2.4629 mOhm (5 squares)",
          payload.get("status") == "ok" and _r is not None
          and abs(_r - 0.002462857) < 1e-9, str(_r))

    r = handle({"jsonrpc": "2.0", "id": 76, "method": "tools/call",
                "params": {"name": "thermal_stack",
                           "arguments": {"layers": [{"thickness_mm": 1.6,
                                                     "k_w_mk": 0.3}],
                                         "area_mm2": 100, "power_w": 0.5}}})
    payload = json.loads(r["result"]["content"][0]["text"])
    _rth = ((payload.get("value") or {}).get("rth_k_per_w"))
    check("1.6 mm FR4 over 100 mm2 is 53.333 K/W and 0.5 W raises it 26.667 K",
          payload.get("status") == "ok" and _rth is not None
          and abs(_rth - 53.333333333) < 1e-6
          and abs(payload["value"]["delta_t_k"] - 26.666667) < 1e-5, str(_rth))

    r = handle({"jsonrpc": "2.0", "id": 77, "method": "tools/call",
                "params": {"name": "ir_drop_chain",
                           "arguments": {
                               "segments": [{"length_mm": 10, "width_mm": 0.3,
                                             "thickness_um": 35},
                                            {"length_mm": 5, "width_mm": 0.2,
                                             "thickness_um": 35}],
                               "current_a": 1}}})
    payload = json.loads(r["result"]["content"][0]["text"])
    _v = ((payload.get("value") or {}).get("total_drop_v"))
    # hand: 1.724e-8*(10e-3/(0.3e-3*35e-6) + 5e-3/(0.2e-3*35e-6)) = 28.7333 mV
    check("a chain of two widths is 28.733 mV at 1 A, not an average",
          payload.get("status") == "ok" and _v is not None
          and abs(_v - 0.028733333) < 1e-7, str(_v))

    r = handle({"jsonrpc": "2.0", "id": 8, "method": "tools/call",
                "params": {"name": "board_summary",
                           "arguments": {"path": "__NO_SUCH_BOARD__.kicad_pcb"}}})
    payload = json.loads(r["result"]["content"][0]["text"])
    check("missing board refused, not created",
          payload["status"] == "error" and payload["reason_code"] == "FILE_NOT_FOUND")

    r = handle({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                "params": {"name": "app_status",
                           "arguments": {"app": "lceda-pro", "probe_ports": False}}})
    payload = json.loads(r["result"]["content"][0]["text"])
    check("app_status well formed",
          payload.get("status") == "ok" and "running" in payload
          and "instances" in payload)

    r = handle({"jsonrpc": "2.0", "id": 10, "method": "tools/call",
                "params": {"name": "calc_capabilities", "arguments": {}}})
    payload = json.loads(r["result"]["content"][0]["text"])
    check("capabilities lists the board tools",
          "board_summary" in payload["available"] and "app_open_project" in payload["available"])

    sample = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "tests", "data", "sample.kicad_pcb")
    if os.path.isfile(sample):
        r = handle({"jsonrpc": "2.0", "id": 11, "method": "tools/call",
                    "params": {"name": "board_fidelity",
                               "arguments": {"path": sample, "engine": False}}})
        payload = json.loads(r["result"]["content"][0]["text"])
        check("fidelity round trip is a token bijection",
              bool((payload.get("roundtrip") or {}).get("token_identity")),
              str((payload.get("roundtrip") or {}).get("token_identity")))
        check("fidelity coverage is complete",
              (payload.get("coverage") or {}).get("share_with_a_reading_rule") == 1.0,
              str((payload.get("coverage") or {}).get("share_with_a_reading_rule")))

        r = handle({"jsonrpc": "2.0", "id": 12, "method": "tools/call",
                    "params": {"name": "board_view",
                               "arguments": {"path": sample, "kind": "zones"}}})
        payload = json.loads(r["result"]["content"][0]["text"])
        filled = [z for z in payload.get("items", []) if z.get("filled_polygon_count")]
        check("zone fill is read from the file, not recomputed", bool(filled))

        r = handle({"jsonrpc": "2.0", "id": 13, "method": "tools/call",
                    "params": {"name": "board_drc", "arguments": {"path": sample}}})
        payload = json.loads(r["result"]["content"][0]["text"])
        check("drc returns a vendor verdict or a refusal",
              payload.get("status") in ("ok", "error"),
              payload.get("reason_code") or ("violations=%s" % payload.get("violations_total")))
    else:
        check("sample board present for the fidelity check", False, sample)

    r = handle({"jsonrpc": "2.0", "id": 14, "method": "tools/call",
                "params": {"name": "board_ops", "arguments": {}}})
    payload = json.loads(r["result"]["content"][0]["text"])
    by_format = payload.get("by_format") or {}
    check("write catalogue covers both formats",
          len(by_format.get("kicad", {}).get("operations") or []) >= 20
          and len(by_format.get("epro", {}).get("operations") or []) >= 3,
          "kicad=%s epro=%s" % (len(by_format.get("kicad", {}).get("operations") or []),
                                 len(by_format.get("epro", {}).get("operations") or [])))

    # write path, end to end, without the engine (kicad-cli costs ~90 s per DRC):
    # create -> propose -> apply -> restore, in a temp dir that is removed again.
    import shutil as _shutil
    import tempfile as _tempfile
    tmp = _tempfile.mkdtemp(prefix="pcbagent-selftest-")
    try:
        board = os.path.join(tmp, "selftest.kicad_pcb")
        r = handle({"jsonrpc": "2.0", "id": 15, "method": "tools/call",
                    "params": {"name": "board_create", "arguments": {
                        "path": board,
                        "spec": {"width_mm": 30, "height_mm": 20, "engine": False}}}})
        created = json.loads(r["result"]["content"][0]["text"])
        check("board_create writes a board with a closed outline",
              created.get("status") == "ok"
              and (created.get("digest") or {}).get("outline", {}).get("closed") is True,
              str(created.get("status")))

        r = handle({"jsonrpc": "2.0", "id": 16, "method": "tools/call",
                    "params": {"name": "board_propose", "arguments": {
                        "path": board,
                        "operations": [{"op": "add_board_text", "text": "SELFTEST",
                                        "x": 15, "y": 10, "layer": "F.SilkS",
                                        "height": 1.2, "thickness": 0.15}]}}})
        prop = json.loads(r["result"]["content"][0]["text"])
        with open(board, "rb") as fh:
            untouched = fh.read()
        check("board_propose writes nothing",
              prop.get("status") == "ok" and bool(prop.get("plan_id"))
              and len(untouched) == (created.get("bytes") or -1),
              str(prop.get("status")))

        r = handle({"jsonrpc": "2.0", "id": 17, "method": "tools/call",
                    "params": {"name": "board_apply", "arguments": {
                        "plan_id": prop.get("plan_id"), "confirm": True,
                        "options": {"engine": False}}}})
        applied = json.loads(r["result"]["content"][0]["text"])
        passed = applied.get("gates_passed") or []
        check("board_apply passes bytes/token/postcondition gates",
              applied.get("status") == "ok"
              and {"bytes_written", "token_identity", "postconditions"} <= set(passed),
              ",".join(passed))

        r = handle({"jsonrpc": "2.0", "id": 18, "method": "tools/call",
                    "params": {"name": "board_apply", "arguments": {
                        "plan_id": prop.get("plan_id"), "confirm": True,
                        "options": {"engine": False}}}})
        replay = json.loads(r["result"]["content"][0]["text"])
        check("a plan is single use",
              replay.get("status") == "error"
              and replay.get("reason_code") == "PLAN_NOT_FOUND",
              str(replay.get("reason_code")))

        cid = (applied.get("checkpoint") or {}).get("checkpoint_id")
        r = handle({"jsonrpc": "2.0", "id": 19, "method": "tools/call",
                    "params": {"name": "board_restore", "arguments": {
                        "checkpoint_id": cid, "confirm": True}}})
        back = json.loads(r["result"]["content"][0]["text"])
        with open(board, "rb") as fh:
            restored = fh.read()
        check("board_restore returns the exact bytes",
              back.get("status") == "ok" and restored == untouched,
              str(back.get("status")))

        r = handle({"jsonrpc": "2.0", "id": 20, "method": "tools/call",
                    "params": {"name": "board_ops", "arguments": {}}})
        catalogue = json.loads(r["result"]["content"][0]["text"])
        epro_ops = ((catalogue.get("by_format") or {}).get("epro") or {}).get(
            "operations") or []
        check("board_ops advertises the 6 .epro operations",
              len(epro_ops) == 6, ",".join(o["op"] for o in epro_ops))

        # ---- schematic write path (no engine: this must stay fast)
        sch_text = (
            '(kicad_sch\n'
            '\t(version 20250114)\n'
            '\t(generator "eeschema")\n'
            '\t(uuid "11111111-1111-4111-8111-111111111111")\n'
            '\t(paper "A4")\n'
            '\t(lib_symbols)\n'
            '\t(sheet_instances\n\t\t(path "/" (page "1"))\n\t)\n'
            '\t(embedded_fonts no)\n'
            ')\n')
        sch = os.path.join(tmp, "selftest.kicad_sch")
        with open(sch, "wb") as fh:
            fh.write(sch_text.encode("utf-8"))
        with open(sch, "rb") as fh:
            sch_before = fh.read()
        r = handle({"jsonrpc": "2.0", "id": 22, "method": "tools/call",
                    "params": {"name": "schematic_propose", "arguments": {
                        "path": sch,
                        "operations": [{"op": "add_wire",
                                        "points": [[20, 20], [40, 20]]},
                                       {"op": "add_label", "text": "SELFTEST",
                                        "x": 20, "y": 20}],
                        "options": {"engine": False}}}})
        sprop = json.loads(r["result"]["content"][0]["text"])
        with open(sch, "rb") as fh:
            sch_untouched = fh.read()
        check("schematic_propose writes nothing",
              sprop.get("status") == "ok" and bool(sprop.get("plan_id"))
              and sch_untouched == sch_before, str(sprop.get("status")))

        r = handle({"jsonrpc": "2.0", "id": 23, "method": "tools/call",
                    "params": {"name": "schematic_apply", "arguments": {
                        "plan_id": sprop.get("plan_id"), "confirm": True,
                        "options": {"engine": False}}}})
        sapplied = json.loads(r["result"]["content"][0]["text"])
        check("schematic_apply passes bytes/token/postcondition gates",
              sapplied.get("status") == "ok"
              and {"bytes_written", "token_identity", "postconditions"}
              <= set(sapplied.get("gates_passed") or []),
              ",".join(sapplied.get("gates_passed") or []))

        r = handle({"jsonrpc": "2.0", "id": 24, "method": "tools/call",
                    "params": {"name": "schematic_view",
                               "arguments": {"path": sch, "kind": "objects"}}})
        objs = json.loads(r["result"]["content"][0]["text"])
        check("schematic_view reads the write back as objects",
              objs.get("status") == "ok" and (objs.get("total") or 0) == 2
              and "DOWNWARDS" in (objs.get("frame") or ""),
              "total=%s" % objs.get("total"))

        esch = os.path.join(tmp, "proj.epro")
        with zipfile.ZipFile(esch, "w") as zf:
            zf.writestr("project.json", "{}")
        r = handle({"jsonrpc": "2.0", "id": 25, "method": "tools/call",
                    "params": {"name": "schematic_propose", "arguments": {
                        "path": esch,
                        "operations": [{"op": "add_wire",
                                        "points": [[0, 0], [1, 1]]}]}}})
        eref = json.loads(r["result"]["content"][0]["text"])
        check("the LCEDA Pro schematic writer refuses instead of guessing",
              eref.get("status") == "error"
              and eref.get("reason_code") == "WRITE_UNSUPPORTED_FORMAT",
              str(eref.get("reason_code")))

        junk = os.path.join(tmp, "not-a-project.epcb")
        with open(junk, "wb") as fh:
            fh.write(b"neither a zip nor kicad\n")
        r = handle({"jsonrpc": "2.0", "id": 21, "method": "tools/call",
                    "params": {"name": "board_propose", "arguments": {
                        "path": junk,
                        "operations": [{"op": "set_text", "id": "s1",
                                        "text": "SELFTEST"}]}}})
        refused = json.loads(r["result"]["content"][0]["text"])
        check("an unreadable file is refused, not guessed",
              refused.get("status") == "error"
              and refused.get("reason_code") == "FORMAT_NOT_SUPPORTED",
              str(refused.get("reason_code")))

        # DFM: a capability document is the fab's promise, never this server's
        # default. Both directions are checked - a typo'd rule key is refused,
        # and the same board passes one limit and fails the next.
        dfm_board = os.path.join(tmp, "dfm.kicad_pcb")
        with open(dfm_board, "wb") as fh:
            fh.write(b'(kicad_pcb (version 20221018) (generator pcbnew)\n'
                     b'  (layers (0 "F.Cu" signal) (31 "B.Cu" signal)'
                     b' (44 "Edge.Cuts" user))\n'
                     b'  (net 0 "") (net 1 "VCC")\n'
                     b'  (gr_rect (start 0 0) (end 50 30) (layer "Edge.Cuts")'
                     b' (width 0.1))\n'
                     b'  (footprint "T:A" (layer "F.Cu") (at 5 5)\n'
                     b'    (fp_text reference "U1" (at 0 -2) (layer "F.SilkS"))\n'
                     b'    (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu")'
                     b' (net 1 "VCC")))\n'
                     b'  (segment (start 5 5) (end 40 5) (width 0.2) (layer "F.Cu")'
                     b' (net 1))\n'
                     b'  (via (at 20 5) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu")'
                     b' (net 1))\n)\n')

        r = handle({"jsonrpc": "2.0", "id": 78, "method": "tools/call",
                    "params": {"name": "dfm_capability", "arguments": {
                        "capability": {"source": "selftest",
                                       "rules": {"min_track_wdith_mm": 0.2}}}}})
        payload = json.loads(r["result"]["content"][0]["text"])
        check("dfm refuses a misspelled rule key instead of reading it as unstated",
              payload.get("status") == "error"
              and payload.get("reason_code") == "CAPABILITY_KEY_UNKNOWN",
              str(payload.get("reason_code")))

        _dfm_cap = {"fab": "selftest", "source": "selftest fixture",
                    "rules": {"min_track_width_mm": 0.15, "min_via_drill_mm": 0.3}}
        r = handle({"jsonrpc": "2.0", "id": 79, "method": "tools/call",
                    "params": {"name": "dfm_check", "arguments": {
                        "path": dfm_board, "capability": _dfm_cap}}})
        payload = json.loads(r["result"]["content"][0]["text"])
        check("dfm passes 0.2 mm track / 0.3 mm drill against a 0.15 / 0.3 limit",
              payload.get("status") == "ok"
              and (payload.get("value") or {}).get("overall") == "pass"
              and abs(((payload["value"].get("facts") or {})
                       .get("narrowest_track") or {}).get("measured", 0) - 0.2) < 1e-9,
              str((payload.get("value") or {}).get("overall")))

        _dfm_cap["rules"] = dict(_dfm_cap["rules"], min_track_width_mm=0.25)
        r = handle({"jsonrpc": "2.0", "id": 80, "method": "tools/call",
                    "params": {"name": "dfm_check", "arguments": {
                        "path": dfm_board, "capability": _dfm_cap}}})
        payload = json.loads(r["result"]["content"][0]["text"])
        check("dfm fails the same board against a 0.25 mm limit and names the rule",
              (payload.get("value") or {}).get("failed_rules") == ["min_track_width_mm"],
              str((payload.get("value") or {}).get("failed_rules")))

        # --- vibration. The four numbers below are the closed forms, so a
        # mistake in the Ritz assembly cannot pass them by agreeing with itself.
        r = handle({"jsonrpc": "2.0", "id": 81, "method": "tools/call",
                    "params": {"name": "vibration_plate_modes", "arguments": {
                        "a_mm": 100, "b_mm": 100, "thickness_mm": 1.0,
                        "material": "fr4", "mount": "edges", "n_modes": 1}}})
        payload = json.loads(r["result"]["content"][0]["text"])
        _f = ((payload.get("value") or {}).get("modes") or [{}])[0].get("freq_hz", 0)
        # pi^2 sqrt(D/rho h) / a^2 / 2 pi with E=24 GPa, nu=0.15, rho=1850
        check("vibration SSSS square f11 = 330.385 Hz (analytic)",
              abs(_f - 330.385) < 0.05, str(_f))

        r = handle({"jsonrpc": "2.0", "id": 82, "method": "tools/call",
                    "params": {"name": "vibration_plate_modes", "arguments": {
                        "a_mm": 100, "b_mm": 100, "thickness_mm": 1.0,
                        "material": "fr4", "mount": "clamped", "n_modes": 1}}})
        payload = json.loads(r["result"]["content"][0]["text"])
        mode = ((payload.get("value") or {}).get("modes") or [{}])[0]
        check("vibration CCCC square f11 = 602.382 Hz (Leissa 35.99) and the "
              "antinode is at the centre",
              abs(mode.get("freq_hz", 0) - 602.382) < 0.05
              and abs(mode.get("antinode_mm", [0, 0])[0] - 50.0) < 5.0,
              str(mode.get("freq_hz")))

        r = handle({"jsonrpc": "2.0", "id": 83, "method": "tools/call",
                    "params": {"name": "vibration_shock", "arguments": {
                        "f_n_hz": 100, "zeta": 0.1,
                        "pulse": {"type": "step", "amplitude_g": 10}}}})
        payload = json.loads(r["result"]["content"][0]["text"])
        _dlf = (payload.get("value") or {}).get("displacement_amplification", 0)
        check("vibration step shock DLF = 1+exp(-zeta pi/sqrt(1-zeta^2)) = 1.72925",
              abs(_dlf - 1.72925) < 0.005, str(_dlf))

        r = handle({"jsonrpc": "2.0", "id": 84, "method": "tools/call",
                    "params": {"name": "vibration_random_response", "arguments": {
                        "f_n_hz": 300, "q_factor": 10,
                        "psd": {"points": [[10, 0.1], [3000, 0.1]]}}}})
        payload = json.loads(r["result"]["content"][0]["text"])
        _g = (payload.get("value") or {}).get("g_rms", 0)
        check("vibration Miles integral matches the closed form 21.708 g inside 1 %",
              abs(_g - 21.708) < 0.217, str(_g))

        r = handle({"jsonrpc": "2.0", "id": 85, "method": "tools/call",
                    "params": {"name": "vibration_fatigue", "arguments": {
                        "f_n_hz": 300, "z_3sigma_mm": 0.5}}})
        payload = json.loads(r["result"]["content"][0]["text"])
        check("vibration fatigue refuses without z_allow_mm",
              payload.get("status") == "error"
              and payload.get("reason_code") == "ARG_REQUIRED",
              str(payload.get("reason_code")))

        # --- cavity / electro-thermal. Again only closed forms and hand
        # arithmetic: f10 for a 100 mm plane at er=4.3 is c/(2 sqrt(er) * 0.1 m),
        # which is also the definition of a half guided wavelength.
        r = handle({"jsonrpc": "2.0", "id": 86, "method": "tools/call",
                    "params": {"name": "cavity_resonance", "arguments": {
                        "a_mm": 100, "b_mm": 80, "er": 4.3, "m_max": 1,
                        "n_max": 1}}})
        payload = json.loads(r["result"]["content"][0]["text"])
        _m = (payload.get("value") or [{}])[0]
        check("cavity (1,0) mode of a 100 mm plane pair at er=4.3 = 722.864 MHz",
              abs(_m.get("f_hz", 0) - 722864000.0) < 2000.0
              and abs(_m.get("lambda_g_mm", 0) - 200.0) < 0.01,
              str(_m.get("f_hz")))

        r = handle({"jsonrpc": "2.0", "id": 87, "method": "tools/call",
                    "params": {"name": "cavity_via_fence", "arguments": {
                        "f_resonance_hz": 722864000.0, "er": 4.3,
                        "pitch_mm": 15.0, "max_pitch_ratio": 0.05}}})
        payload = json.loads(r["result"]["content"][0]["text"])
        # 0.05 * 200 mm = 10 mm limit, the 15 mm pitch is over it
        check("cavity via fence fails a 15 mm pitch against a lambda_g/20 criterion",
              (payload.get("value") or {}).get("verdict") == "fail",
              str((payload.get("value") or {}).get("verdict")))

        r = handle({"jsonrpc": "2.0", "id": 88, "method": "tools/call",
                    "params": {"name": "cavity_via_fence", "arguments": {
                        "f_resonance_hz": 722864000.0, "er": 4.3,
                        "pitch_mm": 15.0}}})
        payload = json.loads(r["result"]["content"][0]["text"])
        check("cavity via fence refuses to judge without a criterion",
              (payload.get("value") or {}).get("verdict") == "indeterminate",
              str((payload.get("value") or {}).get("verdict")))

        r = handle({"jsonrpc": "2.0", "id": 89, "method": "tools/call",
                    "params": {"name": "electrothermal_conductor", "arguments": {
                        "r_ohm_at_20c": 0.01, "current_a": 1.0,
                        "rth_c_per_w": 50.0}}})
        payload = json.loads(r["result"]["content"][0]["text"])
        # A = 0.01*50*(1+0.00393*5) = 0.5098250, G = 0.001965
        # dT = A/(1-G) = 0.510829 -> T = 25.5108
        _t = payload.get("value", 0)
        check("electrothermal fixed point = A/(1-G) = 25.5108 C (hand arithmetic)",
              abs(_t - 25.510829) < 1e-3, str(_t))

        r = handle({"jsonrpc": "2.0", "id": 90, "method": "tools/call",
                    "params": {"name": "electrothermal_conductor", "arguments": {
                        "r_ohm_at_20c": 1.0, "current_a": 2.0,
                        "rth_c_per_w": 300.0}}})
        payload = json.loads(r["result"]["content"][0]["text"])
        check("electrothermal refuses a runaway (dP/dT*Rth = 4.716 > 1)",
              payload.get("status") == "error"
              and payload.get("reason_code") == "THERMAL_RUNAWAY",
              str(payload.get("reason_code")))
    finally:
        _shutil.rmtree(tmp, ignore_errors=True)

    failed = [c for c in checks if not c[1]]
    for name, good, detail in checks:
        _log(("PASS  " if good else "FAIL  ") + name + (("  [" + detail + "]") if detail else ""))
    _log("%d/%d passed" % (len(checks) - len(failed), len(checks)))
    return 1 if failed else 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--selftest" in argv:
        raise SystemExit(selftest())
    if "--version" in argv:
        print("%s %s" % (SERVER_NAME, __version__))
        return
    _log("serving on stdio (protocol %s)" % DEFAULT_PROTOCOL)
    serve()


if __name__ == "__main__":
    main()
