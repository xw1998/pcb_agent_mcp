"""Minimal MCP server over stdio, standard library only.

Why not the official SDK: the Calc layer is pure arithmetic and the goal is
zero external components. This speaks newline-delimited JSON-RPC 2.0, which is
what the MCP stdio transport is. Handlers live in tools.py, so swapping in the
official SDK later is a thin adapter, not a rewrite.
"""
import json
import math as _math
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
    check("creepage answers IEC 60664-1 (250 V / PD2 / IIIa = 2.5 mm)",
          payload["status"] == "ok" and abs(payload.get("value", 0) - 2.5) < 1e-9,
          str(payload.get("value")))

    r = handle({"jsonrpc": "2.0", "id": 55, "method": "tools/call",
                "params": {"name": "creepage_required",
                           "arguments": {"voltage_v": 250,
                                         "safety_standard": "IEC 62368-1"}}})
    payload = json.loads(r["result"]["content"][0]["text"])
    check("creepage fails closed for a standard this build has no table for",
          payload["status"] == "error"
          and payload["reason_code"] == "SAFETY_TABLE_MISSING",
          str(payload.get("reason_code")))

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

        # ---- multi-page (hierarchical) schematic: the tree, and a dead sheet box
        child_sch = os.path.join(tmp, "shelftest_child.kicad_sch")
        root_sch = os.path.join(tmp, "shelftest_root.kicad_sch")
        with open(child_sch, "wb") as fh:
            fh.write(('(kicad_sch\n\t(version 20250114)\n'
                      '\t(generator "eeschema")\n'
                      '\t(uuid "22222222-2222-4222-8222-222222222222")\n'
                      '\t(paper "A4")\n\t(lib_symbols)\n)\n').encode("utf-8"))
        root_text = (
            '(kicad_sch\n\t(version 20250114)\n\t(generator "eeschema")\n'
            '\t(uuid "11111111-1111-4111-8111-111111111111")\n\t(paper "A4")\n'
            '\t(lib_symbols)\n'
            '\t(sheet\n\t\t(at 40 40)\n\t\t(size 40 25)\n'
            '\t\t(uuid "33333333-3333-4333-8333-333333333333")\n'
            '\t\t(property "Sheetname" "child"\n\t\t\t(at 40 39.2 0)\n'
            '\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1.524 1.524)\n'
            '\t\t\t\t)\n\t\t\t\t(justify left bottom)\n\t\t\t)\n\t\t)\n'
            '\t\t(property "Sheetfile" "shelftest_child.kicad_sch"\n'
            '\t\t\t(at 40 65.6 0)\n'
            '\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1.524 1.524)\n'
            '\t\t\t\t)\n\t\t\t\t(justify left top)\n\t\t\t)\n\t\t)\n'
            '\t\t(pin "VCC" input\n\t\t\t(at 80 52.5 0)\n'
            '\t\t\t(uuid "44444444-4444-4444-8444-444444444444")\n'
            '\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1.27 1.27)\n'
            '\t\t\t\t)\n\t\t\t\t(justify left)\n\t\t\t)\n\t\t)\n'
            '\t\t(instances\n\t\t\t(project "selftest"\n'
            '\t\t\t\t(path "/11111111-1111-4111-8111-111111111111"\n'
            '\t\t\t\t\t(page "2")\n\t\t\t\t)\n\t\t\t)\n\t\t)\n\t)\n'
            '\t(sheet_instances\n\t\t(path "/"\n\t\t\t(page "1")\n\t\t)\n\t)\n'
            '\t(embedded_fonts no)\n)\n')
        with open(root_sch, "wb") as fh:
            fh.write(root_text.encode("utf-8"))
        r = handle({"jsonrpc": "2.0", "id": 151, "method": "tools/call",
                    "params": {"name": "schematic_hierarchy",
                               "arguments": {"path": root_sch}}})
        hier = json.loads(r["result"]["content"][0]["text"])
        hh = hier.get("hierarchy") or {}
        check("schematic_hierarchy reads a two-file project as a tree",
              hier.get("status") == "ok" and hh.get("sheets") == 1
              and hh.get("file_count") == 2
              and [p.get("page") for p in hh.get("pages") or []] == ["2"]
              and hh.get("issues_total") == 0,
              "sheets=%s files=%s pages=%s issues=%s"
              % (hh.get("sheets"), hh.get("file_count"),
                 [p.get("page") for p in hh.get("pages") or []],
                 hh.get("issues_total")))

        with open(root_sch, "rb") as fh:
            root_before = fh.read()
        r = handle({"jsonrpc": "2.0", "id": 152, "method": "tools/call",
                    "params": {"name": "schematic_propose", "arguments": {
                        "path": root_sch,
                        "operations": [{"op": "add_sheet", "name": "ghost",
                                        "file": "nowhere.kicad_sch",
                                        "x": 40, "y": 150}],
                        "options": {"engine": False}}}})
        ghost = json.loads(r["result"]["content"][0]["text"])
        blocked = bool(ghost.get("blocking")) and any(
            "missing_file" in b for b in ghost.get("blocking") or [])
        with open(root_sch, "rb") as fh:
            root_same = fh.read() == root_before
        refused = False
        if ghost.get("plan_id"):
            r = handle({"jsonrpc": "2.0", "id": 152, "method": "tools/call",
                        "params": {"name": "schematic_apply", "arguments": {
                            "plan_id": ghost.get("plan_id"), "confirm": True,
                            "options": {"engine": False}}}})
            ap = json.loads(r["result"]["content"][0]["text"])
            refused = ap.get("status") == "error"
        check("a sheet box pointing at a file that is not there is refused, not written",
              blocked and refused and root_same,
              "blocked=%s refused=%s untouched=%s" % (blocked, refused, root_same))

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

        # --- a polygonal outline must reduce to the rectangular answer when the
        # polygon IS a rectangle, and must be stiffer than the bbox plate when it
        # is not. Both are cross-path checks, not the polygon agreeing with itself.
        _sq_ring = [[0, 0], [100, 0], [100, 100], [0, 100]]
        r = handle({"jsonrpc": "2.0", "id": 851, "method": "tools/call",
                    "params": {"name": "vibration_plate_outline", "arguments": {
                        "ring_mm": _sq_ring, "thickness_mm": 1.6,
                        "e_gpa": 24, "nu": 0.33, "rho_kg_m3": 1850,
                        "mount": "edges", "n_modes": 1}}})
        payload = json.loads(r["result"]["content"][0]["text"])
        _pf = ((payload.get("value") or {}).get("modes") or [{}])[0].get("freq_hz", 0)
        _pb = (payload.get("value") or {}).get("basis") or {}
        check("polygon outline reduces to the rectangle on a square (SSSS 553.65 Hz)",
              payload.get("status") == "ok" and abs(_pf - 553.65) < 1.0
              and _pb.get("alpha_converged") is True, str(_pf))

        r = handle({"jsonrpc": "2.0", "id": 852, "method": "tools/call",
                    "params": {"name": "vibration_plate_outline", "arguments": {
                        "ring_mm": _sq_ring, "thickness_mm": 1.6,
                        "e_gpa": 24, "nu": 0.33, "rho_kg_m3": 1850,
                        "mount": "clamped", "n_modes": 1, "poly_degree": 6}}})
        payload = json.loads(r["result"]["content"][0]["text"])
        _pf6 = ((payload.get("value") or {}).get("modes") or [{}])[0].get("freq_hz", 0)
        _ws = " ".join(((payload.get("value") or {}).get("warnings") or []))
        check("polygon degree 6 + clamped is wildly high AND says so",
              payload.get("status") == "ok" and _pf6 > 1500.0
              and "may contain no function" in _ws, str(_pf6))

        r = handle({"jsonrpc": "2.0", "id": 853, "method": "tools/call",
                    "params": {"name": "vibration_plate_outline", "arguments": {
                        "ring_mm": [[0, 0], [100, 100], [100, 0], [0, 100]],
                        "thickness_mm": 1.6}}})
        payload = json.loads(r["result"]["content"][0]["text"])
        check("a self-crossing outline is refused, not solved",
              payload.get("status") == "error"
              and payload.get("reason_code") == "OUTLINE_INVALID",
              str(payload.get("reason_code")))

        # --- 0.0.57: unsymmetric copper. One skin couples bending and stretching,
        # so the in-plane-free stiffness is D - B A^-1 B (SOFTER than D) and the
        # plate must land between the bare board and the double-sided one. The
        # three checks below can all fail: a section that kept D, or that put the
        # second skin on, changes every one of them.
        r = handle({"jsonrpc": "2.0", "id": 855, "method": "tools/call",
                    "params": {"name": "vibration_plate_modes", "arguments": {
                        "a_mm": 100, "b_mm": 80, "thickness_mm": 1.6,
                        "material": "fr4", "mount": "ssss", "n_modes": 1,
                        "copper_um": 35, "copper_um_bottom": 0}}})
        payload = json.loads(r["result"]["content"][0]["text"])
        _v = payload.get("value") or {}
        _asym = (_v.get("composite") or {}).get("asymmetric") or {}
        _f1 = ((_v.get("modes") or [{}])[0]).get("freq_hz", 0)
        check("one-sided copper reports B != 0, D* below D (2.65 %), and the "
              "neutral plane moved to +0.0816 mm",
              payload.get("status") == "ok"
              and abs((_asym.get("b11_n") or 0.0) - 3558.77) < 1.0
              and abs((_asym.get("stiffness_lost_pct") or 0.0) - 2.65) < 0.05
              and abs((_asym.get("neutral_plane_mm") or 0.0) - 0.0815537) < 1e-5,
              str(_asym))
        check("one-sided copper f11 = 737.558 Hz, i.e. between the bare board "
              "(677.29) and the double-sided one (800.849)",
              abs(_f1 - 737.558) < 0.02, str(_f1))

        r = handle({"jsonrpc": "2.0", "id": 856, "method": "tools/call",
                    "params": {"name": "vibration_plate_modes", "arguments": {
                        "a_mm": 100, "b_mm": 80, "thickness_mm": 1.6,
                        "material": "fr4", "mount": "ssss", "n_modes": 1,
                        "added_mass_regions": [{"mass_g": 20.0}]}}})
        payload = json.loads(r["result"]["content"][0]["text"])
        _v = payload.get("value") or {}
        _dm = _v.get("added_mass_distributed") or {}
        _beta = _dm.get("added_over_plate") or 0.0
        _fd = ((_v.get("modes") or [{}])[0]).get("freq_hz", 0)
        # a mass spread over the WHOLE plate scales f by 1/sqrt(1+beta); the
        # point-mass path would not (a mass at a node leaves a mode alone)
        check("a whole-plate 20 g spread drops f11 by 1/sqrt(1+beta) = 498.682 Hz",
              abs(_beta - 0.844595) < 1e-5
              and abs(_fd - 677.29 / _math.sqrt(1.0 + _beta)) < 0.02
              and abs(_fd - 498.682) < 0.02, "%.4f beta=%.6f" % (_fd, _beta))

        # --- the write snippet is built from the APP's OWN table: the method name
        # and the receiver both come out of a document that the editor itself
        # produces, and the class name is never lower-cased into a property name.
        _dump = {"title": "PCB_Document", "callPath": "eda.pcb_Document.",
                 "methods": [{"name": "save", "comment": "保存文档"},
                             {"name": "saveAs", "comment": "另存为"},
                             {"name": "refresh", "comment": "刷新"}]}
        r = handle({"jsonrpc": "2.0", "id": 854, "method": "tools/call",
                    "params": {"name": "lceda_write_snippet", "arguments": {
                        "op": "save", "api_dump": _dump}}})
        payload = json.loads(r["result"]["content"][0]["text"])
        _v = payload.get("value") or {}
        check("write snippet names the app's own method and its own receiver",
              payload.get("status") == "ok"
              and _v.get("code") == "return await eda.pcb_Document.save();"
              and _v.get("receiver_from") == "callPath"
              and _v.get("evidence") == "exact_name",
              str(_v.get("code")))

        r = handle({"jsonrpc": "2.0", "id": 855, "method": "tools/call",
                    "params": {"name": "lceda_write_snippet", "arguments": {
                        "op": "move", "api_dump": _dump,
                        "argsv": ["'U1'"]}}})
        payload = json.loads(r["result"]["content"][0]["text"])
        check("a table with no matching method is refused, not approximated",
              payload.get("status") == "error"
              and payload.get("reason_code") == "SNIPPET_NO_API",
              str(payload.get("reason_code")))

        # --- a run becomes a script whose digest is recomputable, and the replay
        # tool refuses to invent what to replay.
        r = handle({"jsonrpc": "2.0", "id": 856, "method": "tools/call",
                    "params": {"name": "lceda_step_record", "arguments": {
                        "steps": [{"label": "save",
                                   "code": "return await eda.pcb_Document.save();",
                                   "value": True}]}}})
        payload = json.loads(r["result"]["content"][0]["text"])
        _v = payload.get("value") or {}
        check("a recorded run keeps its values and never claims idempotence",
              payload.get("status") == "ok"
              and len(_v.get("sha256") or "") == 64
              and _v.get("recorded_values") == 1
              and (_v.get("script") or {}).get("idempotent") == "unknown",
              str(_v.get("recorded_values")))

        r = handle({"jsonrpc": "2.0", "id": 857, "method": "tools/call",
                    "params": {"name": "board_job", "arguments": {
                        "action": "get", "id": "drc-selftest-none"}}})
        payload = json.loads(r["result"]["content"][0]["text"])
        check("an unknown job id is JOB_NOT_FOUND, not an empty verdict",
              payload.get("status") == "error"
              and payload.get("reason_code") == "JOB_NOT_FOUND",
              str(payload.get("reason_code")))

        # --- copper as the heat spreader. The anchor is hand arithmetic: one
        # 35 um layer at 400 W/(m*K) on a 1.6 mm board adds
        # 35e-6/1.6e-3*400 = 8.75 W/(m*K) to the sheet, so a pour that covers
        # the whole board must equal running with k = 0.3 + 8.75 = 9.05.
        _et = {"width_mm": 100, "height_mm": 80, "thickness_mm": 1.6,
               "k_w_mk": 0.3, "h_w_m2k": 10, "uniform_power_w": 3.0,
               "cell_mm": 4.0}
        r = handle({"jsonrpc": "2.0", "id": 858, "method": "tools/call",
                    "params": {"name": "board_electrothermal", "arguments":
                               dict(_et, copper_pours=[
                                   {"x1_mm": 0, "y1_mm": 0, "x2_mm": 100,
                                    "y2_mm": 80, "thickness_um": 35}])}})
        payload = json.loads(r["result"]["content"][0]["text"])
        _cs = payload.get("copper_spreading") or {}
        r2 = handle({"jsonrpc": "2.0", "id": 858, "method": "tools/call",
                     "params": {"name": "board_electrothermal",
                                "arguments": dict(_et, k_w_mk=9.05)}})
        _direct = json.loads(r2["result"]["content"][0]["text"])
        check("a full-board pour equals the same k passed as a scalar (9.05)",
              payload.get("status") == "ok"
              and abs((_cs.get("k_eff_w_mk") or {}).get("area_avg", 0) - 9.05)
              < 1e-9
              and abs(payload.get("t_max_c", 0) - _direct.get("t_max_c", -1))
              < 1e-6,
              "%s vs %s" % (payload.get("t_max_c"), _direct.get("t_max_c")))

        # The copper has to be WHERE THE HEAT IS: same area, same thickness,
        # moved off the trace, must stop mattering.
        _trace = {"x1_mm": 10, "y1_mm": 40, "x2_mm": 90, "y2_mm": 40,
                  "width_mm": 3.0, "thickness_um": 35.0, "current_a": 4.0}
        _base = {"width_mm": 100, "height_mm": 80, "thickness_mm": 1.6,
                 "k_w_mk": 0.3, "h_w_m2k": 10, "cell_mm": 2.0,
                 "conductors": [_trace]}
        def _tmax(pours):
            args = dict(_base)
            if pours is not None:
                args["copper_pours"] = pours
            rr = handle({"jsonrpc": "2.0", "id": 859, "method": "tools/call",
                         "params": {"name": "board_electrothermal",
                                    "arguments": args}})
            return json.loads(rr["result"]["content"][0]["text"]).get("t_max_c")
        _bare = _tmax(None)
        _under = _tmax([{"x1_mm": 0, "y1_mm": 35, "x2_mm": 100, "y2_mm": 45,
                         "thickness_um": 35}])
        _away = _tmax([{"x1_mm": 0, "y1_mm": 0, "x2_mm": 100, "y2_mm": 10,
                        "thickness_um": 35}])
        check("copper under the hot trace cools it; the same copper elsewhere "
              "does not",
              _bare is not None and _under is not None and _away is not None
              and _bare - _under > 5.0 and 0.0 <= _bare - _away < 0.1,
              "bare %s under %s away %s" % (_bare, _under, _away))

        r = handle({"jsonrpc": "2.0", "id": 860, "method": "tools/call",
                    "params": {"name": "board_electrothermal", "arguments":
                               dict(_base, copper_pours=[
                                   {"x1_mm": 0, "y1_mm": 0, "x2_mm": 200,
                                    "y2_mm": 80, "thickness_um": 35}])}})
        payload = json.loads(r["result"]["content"][0]["text"])
        check("a pour that leaves the board is refused, not clipped",
              payload.get("status") == "error"
              and payload.get("reason_code") == "ARG_RANGE",
              str(payload.get("reason_code")))

        # Radiation is Stefan-Boltzmann with an EXACT rewrite, so it is
        # checkable two ways: h_rad has a hand formula, and eps = 0 must be the
        # run that never mentioned radiation, bit for bit.
        def _tel(**kw):
            args = dict(_base)
            args.update(kw)
            rr = handle({"jsonrpc": "2.0", "id": 861, "method": "tools/call",
                         "params": {"name": "board_electrothermal",
                                    "arguments": args}})
            return json.loads(rr["result"]["content"][0]["text"])
        _bare_p = _tel()
        _rad = _tel(emissivity=0.9)
        _rad0 = _tel(emissivity=0.0)
        # h_rad = eps*sigma*(T+Ta)*(T^2+Ta^2) in kelvin, hand-computed at t_max
        _tm = _rad.get("t_max_c") or 0.0
        _t1 = _tm + 273.15
        _t2 = 25.0 + 273.15
        _h_want = 0.9 * 5.670374419e-8 * (_t1 + _t2) * (_t1 * _t1 + _t2 * _t2)
        _h_got = ((_rad.get("radiation") or {}).get("h_rad_w_m2k") or {}).get(
            "at_t_max")
        check("radiation cools the plate and h_rad is the hand identity",
              _bare_p.get("t_max_c") is not None and _rad.get("t_max_c") is not None
              and _bare_p["t_max_c"] - _rad["t_max_c"] > 2.5
              and _h_got is not None and abs(_h_got - _h_want) < 1e-4,
              "bare %s rad %s h %s vs %s" % (_bare_p.get("t_max_c"),
                                             _rad.get("t_max_c"), _h_got,
                                             round(_h_want, 6)))

        check("emissivity 0 is bit-identical to asking for no radiation",
              _rad0.get("t_max_c") == _bare_p.get("t_max_c")
              and _rad0.get("t_avg_c") == _bare_p.get("t_avg_c")
              and not (_rad0.get("radiation") or {}).get("modelled"),
              "%s vs %s" % (_rad0.get("t_max_c"), _bare_p.get("t_max_c")))

        # A via array is a total conductance, so it must equal the run with h
        # raised by G/(2*A_cell*N_cell). Hand arithmetic, not one pass twice.
        _vb = {"width_mm": 20, "height_mm": 20, "thickness_mm": 1.6,
               "k_w_mk": 0.3, "h_w_m2k": 10, "cell_mm": 2.0,
               "uniform_power_w": 0.02, "conductors": None}
        _vias = [{"x1_mm": 1, "y1_mm": 1, "x2_mm": 19, "y2_mm": 19,
                  "pitch_mm": 2.0, "d_mm": 0.3, "plating_um": 25.0}]
        def _vt(**kw):
            args = dict(_vb)
            args.update(kw)
            rr = handle({"jsonrpc": "2.0", "id": 862, "method": "tools/call",
                         "params": {"name": "board_electrothermal",
                                    "arguments": args}})
            return json.loads(rr["result"]["content"][0]["text"])
        _vn = _vt(thermal_vias=_vias)
        _g1 = ((_vn.get("thermal_vias") or {}).get("arrays") or [{}])[0].get(
            "conductance_per_via_w_per_k") or 0.0
        _gt = (_vn.get("thermal_vias") or {}).get(
            "total_conductance_w_per_k") or 0.0
        _ac = (2e-3) ** 2
        _h_eq = 10.0 + _gt / (2.0 * _ac * 100.0)
        _ve = _vt(h_w_m2k=_h_eq)
        # one barrel: k_cu*pi*(r_out^2-r_in^2)/span, hand arithmetic
        _g_want = 400.0 * (3.141592653589793 * (0.15 ** 2 - 0.125 ** 2)
                           * 1e-6) / 1.6e-3
        check("a 100-barrel via field equals h + G/(2*A*N), and G is the annulus",
              (_vn.get("thermal_vias") or {}).get("vias_counted") == 100
              and _ve.get("t_max_c") is not None
              and abs(_ve["t_max_c"] - _vn["t_max_c"]) < 1e-4
              and abs(_g1 - _g_want) < 1e-7,
              "via %s eq %s G %s vs %s" % (_vn.get("t_max_c"),
                                           _ve.get("t_max_c"), _g1,
                                           round(_g_want, 9)))

        check("a via field under the hot trace cools it; the same one elsewhere "
              "does not",
              _tel(thermal_vias=[{"x1_mm": 0, "y1_mm": 34, "x2_mm": 100,
                                  "y2_mm": 46, "pitch_mm": 2.0,
                                  "d_mm": 0.3, "plating_um": 25.0}
                                 ]).get("t_max_c") is not None
              and _bare_p.get("t_max_c") is not None
              and (_bare_p["t_max_c"]
                   - _tel(thermal_vias=[{"x1_mm": 0, "y1_mm": 34,
                                         "x2_mm": 100, "y2_mm": 46,
                                         "pitch_mm": 2.0, "d_mm": 0.3,
                                         "plating_um": 25.0}])["t_max_c"]) > 5.0
              and 0.0 <= (_bare_p["t_max_c"]
                          - _tel(thermal_vias=[{"x1_mm": 0, "y1_mm": 0,
                                                "x2_mm": 100, "y2_mm": 12,
                                                "pitch_mm": 2.0, "d_mm": 0.3,
                                                "plating_um": 25.0}
                                               ])["t_max_c"]) < 0.01,
              "bare %s" % (_bare_p.get("t_max_c"),))

        # A barrel whose wall varies along it is a SERIES sum, so it is checkable
        # by hand segment by segment - and a uniform profile must reproduce the
        # number the plain call gives, because it is the same physics.
        def _tv(**kw):
            rr = handle({"jsonrpc": "2.0", "id": 863, "method": "tools/call",
                         "params": {"name": "thermal_via_array",
                                    "arguments": kw}})
            return json.loads(rr["result"]["content"][0]["text"])
        _uni = _tv(drill_mm=0.3, plating_um=25.0, length_mm=1.6)
        _prof = _tv(drill_mm=0.3, plating_um=25.0, length_mm=1.6,
                    barrel_profile=[{"length_mm": 0.55, "plating_um": 25.0},
                                    {"length_mm": 0.5, "plating_um": 15.0},
                                    {"length_mm": 0.55, "plating_um": 25.0}])
        _flat = _tv(drill_mm=0.3, length_mm=1.6,
                    barrel_profile=[{"length_mm": 1.6, "plating_um": 25.0}])
        _r_out = 0.15
        _r_hand = 0.0
        for _L, _t in ((0.55, 25.0), (0.5, 15.0), (0.55, 25.0)):
            _a = 3.141592653589793 * (_r_out ** 2 - (_r_out - _t / 1000.0) ** 2)
            _r_hand += (_L * 1e-3) / (385.0 * _a * 1e-6)
        check("a barrel thinned mid-span costs the series sum of its segments",
              (_uni.get("value") or {}).get("rth_one_via_k_per_w") is not None
              and abs((_prof.get("value") or {}).get("rth_one_via_k_per_w", 0)
                      - _r_hand) < 1e-6,
              "%s vs %s" % ((_prof.get("value") or {}).get(
                  "rth_one_via_k_per_w"), round(_r_hand, 6)))
        check("a one-segment barrel_profile is the uniform barrel, to the last digit",
              abs((_flat.get("value") or {}).get("rth_one_via_k_per_w", 0)
                  - (_uni.get("value") or {}).get("rth_one_via_k_per_w", 0)) < 1e-9
              and (_flat.get("value") or {}).get("rth_array_k_per_w") ==
              (_uni.get("value") or {}).get("rth_array_k_per_w"),
              "%s vs %s" % ((_flat.get("value") or {}).get("rth_one_via_k_per_w"),
                            (_uni.get("value") or {}).get("rth_one_via_k_per_w")))

        # The interface layer is t/(k*A) in series with the field: two numbers to
        # check, the layer itself and the array total that includes it.
        _tim = _tv(drill_mm=0.3, plating_um=25.0, length_mm=1.6, count=9,
                   pitch_mm=1.0,
                   interface={"area_mm2": 25.0, "thickness_um": 100.0,
                              "k_w_mk": 3.0})
        _vv = _tim.get("value") or {}
        _r_tim = (100.0 * 1e-6) / (3.0 * 25.0 * 1e-6)
        check("a TIM layer is t/(k*A) in series with the via field",
              abs(_vv.get("rth_interface_k_per_w", 0) - _r_tim) < 1e-9
              and abs(_vv.get("rth_array_total_k_per_w", 0)
                      - (_vv.get("rth_array_with_board_k_per_w", 0) + _r_tim))
              < 1e-9,
              "%s vs %s" % (_vv.get("rth_interface_k_per_w"), round(_r_tim, 9)))

        # Crosstalk: the modal algebra and the time loop, on synthetic
        # capacitances. No field solve here on purpose - one microstrip pair
        # costs 30 s to 76 s, and what has to be checked at start-up is the
        # algebra around it. The two structural facts that must hold for ANY
        # per-unit-length numbers are (1) no coupling means a silent victim, and
        # (2) a homogeneous dielectric means the two modal velocities are equal,
        # so far-end crosstalk cancels arithmetically and not by assumption.
        from .calc import crosstalk as _xt

        def _xt_modes(c_even, c_odd, er):
            return _xt.modal_from_caps(c_odd * er, c_even * er, c_odd, c_even)

        def _xt_peaks(m):
            wf, _bad = _xt._waveforms(m, 50.0, 0.2e-9, 1.0,
                                      m["z_isolated_ohm"], m["z_isolated_ohm"])
            assert _bad is None, _bad
            return (_xt._peak(wf["t"], wf["victim_near"])[0],
                    _xt._peak(wf["t"], wf["victim_far"])[0],
                    wf["aggressor_far"][-1])

        _xt_flat = _xt_peaks(_xt_modes(5.0, 5.0, 4.3))
        check("a pair with no coupling leaks nothing onto its neighbour",
              _xt_flat[0] == 0.0 and _xt_flat[1] == 0.0
              and abs(_xt_flat[2] - 0.5) < 1e-12,
              "%s %s %s" % (_xt_flat[0], _xt_flat[1], _xt_flat[2]))

        # k_c == k_l == 0.0476 here: er_eff is 4.3 on BOTH modes, the two
        # velocities agree to 1e-16, and the far end is exactly zero while the
        # near end is not (otherwise this would pass by being trivially dead).
        _xt_hom = _xt_peaks(_xt_modes(5.0, 5.5, 4.3))
        _xt_hom_m = _xt_modes(5.0, 5.5, 4.3)
        check("a homogeneous dielectric has no far-end crosstalk at all",
              abs(_xt_hom[1]) < 1e-12 and abs(_xt_hom[0]) > 1e-2
              and abs(_xt_hom_m["er_eff_odd"] - 4.3) < 1e-12
              and abs(_xt_hom_m["er_eff_even"] - 4.3) < 1e-12
              and abs(_xt_hom_m["v_odd_m_s"] - _xt_hom_m["v_even_m_s"])
              / _xt_hom_m["v_even_m_s"] < 1e-14,
              "%s %s" % (_xt_hom[0], _xt_hom[1]))

        # Weak coupling: NEXT is a quarter of the coupling coefficients times the
        # wave that ARRIVES (amp/2 into a matched pair), not times the source.
        _xt_weak = _xt_modes(5.0, 5.02, 4.3)
        _xt_n = _xt_peaks(_xt_weak)[0]
        _xt_want = 0.25 * (_xt_weak["k_l"] + _xt_weak["k_c"]) * 0.5
        check("near-end crosstalk is Kb times the incident wave, not the source",
              abs(_xt_n / _xt_want - 1.0) < 2e-5,
              "%s vs %s" % (round(_xt_n, 9), round(_xt_want, 9)))

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

        # --- the ngspice delegation: what can be asserted without the engine
        r = handle({"jsonrpc": "2.0", "id": 91, "method": "tools/call",
                    "params": {"name": "sim_spice", "arguments": {
                        "netlist": "* div\nV1 1 0 10\nR1 1 2 1k\nR2 2 0 2k\n.op\n",
                        "engine": "builtin"}}})
        payload = json.loads(r["result"]["content"][0]["text"])
        check("engine=builtin is labelled as such (10 V 1k/2k -> 6.6667 V)",
              payload.get("status") == "ok"
              and payload.get("engine_used") == "builtin"
              and abs(((payload.get("value") or {}).get("nodes_v") or {})
                      .get("2", 0) - 6.6666667) < 1e-6,
              str(payload.get("engine_used")))

        from .calc import spice as _spice_mod
        _saved = _spice_mod.NGSPICE_CMD
        _spice_mod.NGSPICE_CMD = None
        try:
            _found = _spice_mod.find_ngspice()
        finally:
            _spice_mod.NGSPICE_CMD = _saved
        r = handle({"jsonrpc": "2.0", "id": 92, "method": "tools/call",
                    "params": {"name": "sim_spice", "arguments": {
                        "netlist": "* div\nV1 1 0 10\nR1 1 2 1k\nR2 2 0 2k\n.op\n",
                        "engine": "ngspice"}}})
        payload = json.loads(r["result"]["content"][0]["text"])
        if _found:
            check("engine=ngspice runs the installed binary (%s)" % _found[0],
                  payload.get("status") == "ok"
                  and payload.get("engine_used") == "ngspice",
                  str(payload.get("engine_used")))
        else:
            check("engine=ngspice REFUSES with no binary (no silent fall back)",
                  payload.get("status") == "error"
                  and payload.get("reason_code") == "ENGINE_NOT_FOUND",
                  str(payload.get("reason_code")))

        # the default must not depend on what happens to be installed on the
        # machine: same netlist, same model set, every time.
        r = handle({"jsonrpc": "2.0", "id": 93, "method": "tools/call",
                    "params": {"name": "sim_spice", "arguments": {
                        "netlist": "* div\nV1 1 0 10\nR1 1 2 1k\nR2 2 0 2k\n.op\n",
                        "cross_check": False}}})
        payload = json.loads(r["result"]["content"][0]["text"])
        check("the default engine is builtin (machine independent), cross_check off",
              payload.get("status") == "ok"
              and "cross_check" not in payload
              and payload.get("engine_used") == "builtin",
              str(payload.get("engine_used")))

        r = handle({"jsonrpc": "2.0", "id": 94, "method": "tools/call",
                    "params": {"name": "sim_spice", "arguments": {
                        "netlist": "* div\nV1 1 0 10\nR1 1 2 1k\nR2 2 0 2k\n.op\n",
                        "engine": "auto"}}})
        payload = json.loads(r["result"]["content"][0]["text"])
        check("engine=auto prefers the engine when present, else builtin",
              payload.get("status") == "ok"
              and payload.get("engine_used") == ("ngspice" if _found else "builtin"),
              str(payload.get("engine_used")))

        r = handle({"jsonrpc": "2.0", "id": 95, "method": "tools/call",
                    "params": {"name": "sim_spice", "arguments": {
                        "netlist": "* div\nV1 1 0 10\nR1 1 2 1k\nR2 2 0 2k\n.op\n",
                        "engine": "ngspice", "cross_check": True}}})
        payload = json.loads(r["result"]["content"][0]["text"])
        if _found:
            _cc = payload.get("cross_check") or {}
            _d = abs(_cc.get("max_abs_diff_v") or 0.0)
            check("cross_check agrees with builtin (<1e-6 V) and the engine is verified",
                  payload.get("status") == "ok"
                  and _d < 1e-6
                  and payload.get("verified_against_engine") is True,
                  "%.3g V" % _d)
        else:
            check("cross_check is skipped when there is no engine to compare with",
                  payload.get("status") == "ok"
                  and "cross_check" not in payload
                  and payload.get("verified_against_engine") is False,
                  str(payload.get("verified_against_engine")))
        # The engine used to be the only thing that could solve a card this module
        # refused by name. T (the LOSSLESS line) moved INTO the builtin set in
        # 0.0.30, O/U (the lossy ones) in 0.0.31, S/W (the two switches) in 0.0.35
        # and J (the JFET) in 0.0.36, so no analogue card is left that only the
        # engine can run for ANALOGUE reasons - the one remaining refusal, A, is an
        # XSPICE code model, and 0.0.37 ships an engine that runs it (see id 155).
        # (0.0.36 claimed here that this ngspice build could not even load the code
        # models; that was wrong - the file ngspice needed was missing, not the
        # feature. docs/43 records the correction.) What still has teeth is the
        # migration itself: the same JFET deck must now be PARSED HERE, read
        # 0.267949 V at the divider tap (BETA=1m / VTO=-1 with VGS=0 sinks
        # 0.366 mA out of the 1 V / 1k tap, where a skipped card reads 0.500 V),
        # and agree with the engine.
        _JFET_DIV = ("* j\nV1 1 0 1\nR1 1 2 1k\nVg 3 0 0\nJ1 2 3 0 jm\n"
                     "R2 2 0 1k\n.model jm NJF(VTO=-1 BETA=1m)\n.op\n")
        r = handle({"jsonrpc": "2.0", "id": 96, "method": "tools/call",
                    "params": {"name": "sim_spice", "arguments": {
                        "netlist": _JFET_DIV, "engine": "builtin"}}})
        payload = json.loads(r["result"]["content"][0]["text"])
        _n = (payload.get("value") or {}).get("nodes_v") or {}
        # 2 - sqrt(3) = 0.2679491924: the tap solves (2*Vd - Vd^2) = 1 - 2*Vd,
        # i.e. the device is in its LINEAR region here, which is why the answer is
        # not the 0.500 V of a divider with the card skipped
        _j1 = [e for e in ((payload.get("value") or {}).get("elements") or [])
               if e.get("name") == "J1"]
        check("builtin J (a JFET) reads 0.267949 V at the divider tap, not the "
              "0.500 V a skipped card gives, and reports the device in its linear "
              "region at 0.464102 mA",
              payload.get("status") == "ok"
              and payload.get("engine_used") == "builtin"
              and abs(_n.get("2", 0.0) - 0.267949192) < 1e-8
              and bool(_j1)
              and abs(_j1[0].get("current_a", {}).get("id", 9.9)
                      - 0.464101615e-3) < 1e-9
              and _j1[0].get("region") == "linear",
              "%r / %r" % (_n, _j1 and _j1[0].get("current_a")))
        if _found:
            r = handle({"jsonrpc": "2.0", "id": 96, "method": "tools/call",
                        "params": {"name": "sim_spice", "arguments": {
                            "netlist": _JFET_DIV, "engine": "ngspice",
                            "cross_check": True}}})
            payload = json.loads(r["result"]["content"][0]["text"])
            _cc = payload.get("cross_check") or {}
            _d = abs(_cc.get("max_abs_diff_v") or 0.0)
            check("the engine agrees with the builtin on that same JFET deck "
                  "(<1e-6 V), so the square law is the engine's equation and not "
                  "this module's own arithmetic",
                  payload.get("status") == "ok"
                  and payload.get("verified_against_engine") is True
                  and _d < 1e-6,
                  "%.3g V" % _d)
        else:
            check("engine='ngspice' refuses when no binary is installed instead of "
                  "quietly answering with the subset",
                  payload.get("status") == "error"
                  and payload.get("reason_code") == "ENGINE_NOT_FOUND",
                  str(payload.get("reason_code")))

        # 0.0.36: the JFET square law is NOT the MOSFET's. BETA=1m at Vov=1 must
        # read 1.000 mA - the 1/2 everybody expects here reads 0.500 mA - and
        # below the knee at Vds=0.3 the linear form reads BETA*(2*Vov*Vds - Vds^2)
        # = 0.510 mA, with gm = 2*BETA*Vds = 0.600 mA/V (which is how the report
        # shows the two REGIONS apart without a smoothing term).
        for _vd, _want_id, _want_gm, _want_region in (
                (5.0, 1.0e-3, 2.0e-3, "saturation"),
                (0.3, 0.51e-3, 0.6e-3, "linear")):
            r = handle({"jsonrpc": "2.0", "id": 151, "method": "tools/call",
                        "params": {"name": "sim_spice", "arguments": {
                            "netlist": "* jf\nVd 1 0 %g\nVg 2 0 0\n"
                                       "J1 1 2 0 jm\n"
                                       ".model jm NJF(VTO=-1 BETA=1m)\n.op\n" % _vd,
                            "engine": "builtin"}}})
            payload = json.loads(r["result"]["content"][0]["text"])
            _e = [e for e in ((payload.get("value") or {}).get("elements") or [])
                  if e.get("name") == "J1"]
            _ok = (payload.get("status") == "ok" and bool(_e)
                   and abs(_e[0].get("current_a", {}).get("id", 9.9)
                           - _want_id) < 1e-12
                   and abs(_e[0].get("gm_s", 9.9) - _want_gm) < 1e-9
                   and _e[0].get("region") == _want_region)
            if _vd < 1.0:
                _lin_ok = _ok
            else:
                _sat_ok = _ok
        check("builtin J: BETA=1m at Vov=1 reads 1.000 mA in saturation (a MOSFET "
              "1/2 would read 0.500 mA) and 0.510 mA at Vds=0.3 in the linear "
              "region, with the two gm values 2*BETA*Vov and 2*BETA*Vds",
              _sat_ok and _lin_ok, "%r / %r" % (_sat_ok, _lin_ok))

        # 0.0.36: AREA on the card REPLACES the model's AREA (measured: model 2
        # with card 3 reads 3x, not 6x), and the gate junction this subset does
        # not stamp is flagged instead of being silently assumed away.
        r = handle({"jsonrpc": "2.0", "id": 152, "method": "tools/call",
                    "params": {"name": "sim_spice", "arguments": {
                        "netlist": "* jf\nVd 1 0 9\nVg 2 0 0\n"
                                   "J1 1 2 0 jm AREA=3\n"
                                   ".model jm NJF(VTO=-1 BETA=1m AREA=2)\n.op\n",
                        "engine": "builtin"}}})
        payload = json.loads(r["result"]["content"][0]["text"])
        _e = [e for e in ((payload.get("value") or {}).get("elements") or [])
              if e.get("name") == "J1"]
        _area_ok = (payload.get("status") == "ok" and bool(_e)
                    and abs(_e[0].get("current_a", {}).get("id", 9.9) - 3e-3) < 1e-12
                    and abs(_e[0].get("model_params", {}).get("BETA_eff", 9.9)
                            - 3e-3) < 1e-15)
        r = handle({"jsonrpc": "2.0", "id": 152, "method": "tools/call",
                    "params": {"name": "sim_spice", "arguments": {
                        "netlist": "* jf\nVd 1 0 5\nVg 2 0 1\n"
                                   "J1 1 2 0 jm\n"
                                   ".model jm NJF(VTO=-1 BETA=1m)\n.op\n",
                        "engine": "builtin"}}})
        payload = json.loads(r["result"]["content"][0]["text"])
        _e = [e for e in ((payload.get("value") or {}).get("elements") or [])
              if e.get("name") == "J1"]
        _fwd = _e[0].get("gate_junction_forward_v", 0.0) if _e else 0.0
        check("builtin J: AREA on the card replaces the model's (3x, not 6x) and a "
              "gate forward-biased by 1 V is reported as outside_model instead of "
              "being modelled as an open circuit",
              _area_ok
              and bool(_e) and abs(_fwd - 1.0) < 1e-9
              and "outside_model" in _e[0]
              and bool(payload.get("warnings")),
              "%r / %r" % (_area_ok, _fwd))

        # 0.0.36: the parameters that would change the answer are refused BY NAME
        # (RD/RS are internal nodes, B is a different equation) - an ignored
        # parameter is a netlist that runs and lies.
        # (KF= moved to the .noise analysis in 0.0.54: at .op it changes nothing,
        # exactly as in SPICE, and the refusal now sits where the lie would be
        # told - see the NOT_MODELED check further down.)
        _msgs = []
        for _m in ("RD=100", "RS=100", "B=2", "CGS=1p"):
            r = handle({"jsonrpc": "2.0", "id": 153, "method": "tools/call",
                        "params": {"name": "sim_spice", "arguments": {
                            "netlist": "* jf\nVd 1 0 5\nVg 2 0 0\n"
                                       "J1 1 2 0 jm\n.model jm NJF(VTO=-1 BETA=1m "
                                       "%s)\n.op\n" % _m,
                            "engine": "builtin"}}})
            _p = json.loads(r["result"]["content"][0]["text"])
            _msgs.append((_p.get("reason_code") == "NETLIST_INVALID"
                          and _m.split("=")[0] in (_p.get("message") or "")))
        check("builtin J refuses RD/RS/B/CGS by name (every one of them changes "
              "the answer, measured) instead of stamping a number nobody chose",
              len(_msgs) == 4 and all(_msgs), str(_msgs))

        # 0.0.37: the engine this repository ships (vendor/ngspice, an unmodified
        # upstream build called as a separate process) is reachable ONLY by asking
        # for engine='ngspice'. If it leaked into find_ngspice() the meaning of
        # engine='auto' would change on every existing installation, so the two
        # lookups are asserted apart.
        _bundled = _spice_mod.find_bundled_ngspice()
        _default = _spice_mod.find_ngspice()
        _asked = _spice_mod.find_ngspice(allow_bundled=True)
        if _bundled:
            check("the bundled engine is found only when asked for: find_ngspice() "
                  "still reports the machine's own copy (%s), "
                  "find_ngspice(allow_bundled=True) reports the bundle"
                  % (_default[0] if _default else "none"),
                  _bundled[0] not in (_default or [])
                  and _asked == _bundled
                  and _bundled[0].endswith("ngspice_con.exe"),
                  str(_asked))
        else:
            check("no bundle in this checkout, so allow_bundled changes nothing",
                  _asked == _default, str(_asked))

        # 0.0.37: the A card is the whole reason for the bundle. This is an
        # XSPICE `gain` block: 1 V in, gain=2, so node 10 must read exactly 2 V -
        # and the envelope must admit that the deck was NOT parsed here.
        _GAIN = ("* xspice\nV1 1 0 1\nA1 1 10 g1\n.model g1 gain(gain=2.0)\n"
                 "R1 10 0 1k\n.op\n")
        r = handle({"jsonrpc": "2.0", "id": 155, "method": "tools/call",
                    "params": {"name": "sim_spice", "arguments": {
                        "netlist": _GAIN, "engine": "ngspice"}}})
        payload = json.loads(r["result"]["content"][0]["text"])
        if _bundled:
            _n = (payload.get("value") or {}).get("nodes_v") or {}
            check("the bundled engine RUNS an XSPICE A card: gain(gain=2) on 1 V "
                  "reads exactly 2 V, and (0.0.39) this module parses that deck "
                  "itself, so the envelope no longer needs the XSPICE excuse",
                  payload.get("status") == "ok"
                  and abs(_n.get("10", 0.0) - 2.0) < 1e-9
                  and payload.get("netlist_parsed") is True
                  and payload.get("verified_against_engine") is True,
                  "%r / %r" % (_n, payload.get("netlist_parsed")))
        else:
            check("with no bundle, engine='ngspice' still refuses the A deck and "
                  "names the bundle among the places it looked",
                  payload.get("status") in ("error", None),
                  str(payload.get("reason_code")))

        # 0.0.37: a SECOND code model, because one lucky model proves nothing -
        # limit() clips 5 V to out_upper_limit=1 and reads 1.0 V.
        _LIMIT = ("* lim\nV1 1 0 5\nA1 1 2 lim1\n"
                  ".model lim1 limit(in_offset=0 gain=1 out_lower_limit=-1 "
                  "out_upper_limit=1)\nR1 2 0 1k\n.op\n")
        r = handle({"jsonrpc": "2.0", "id": 156, "method": "tools/call",
                    "params": {"name": "sim_spice", "arguments": {
                        "netlist": _LIMIT, "engine": "ngspice"}}})
        payload = json.loads(r["result"]["content"][0]["text"])
        if _bundled:
            _n = (payload.get("value") or {}).get("nodes_v") or {}
            check("a second code model behaves: limit(out_upper_limit=1) on 5 V "
                  "reads 1.0 V, so the bundle loaded the model set rather than one "
                  "model that happened to work",
                  payload.get("status") == "ok" and abs(_n.get("2", 0.0) - 1.0) < 1e-9,
                  str(_n))
        else:
            check("no bundle: the limit deck is refused for the same reason",
                  payload.get("status") != "ok", str(payload.get("reason_code")))

        # 0.0.39: gain/limit are algebra, so the builtin answers this deck itself.
        # The control is now on the OTHER side: it must land on the same 2 V the
        # engine lands on, and it must say which region it clamped in.
        r = handle({"jsonrpc": "2.0", "id": 157, "method": "tools/call",
                    "params": {"name": "sim_spice", "arguments": {
                        "netlist": _GAIN, "engine": "builtin"}}})
        payload = json.loads(r["result"]["content"][0]["text"])
        _v = (payload.get("value") or {}).get("nodes_v") or {}
        _a = [e for e in ((payload.get("value") or {}).get("elements") or [])
              if e.get("type") == "A"]
        check("the builtin SOLVES gain(gain=2) itself and lands on the engine's "
              "answer (2 V), reporting the block's own input/output/region instead "
              "of pretending it is a two-terminal part",
              payload.get("status") == "ok" and abs(_v.get("10", 0.0) - 2.0) < 1e-9
              and len(_a) == 1 and _a[0].get("kind") == "GAIN"
              and _a[0].get("region") == "linear"
              and abs(_a[0].get("output_v", 0.0) - 2.0) < 1e-12,
              str(_v) + str(_a)[:120])

        # ... and the refusals that must survive: a code model with STATE is still
        # refused by name, and so is a parameter that moves the corner.
        r = handle({"jsonrpc": "2.0", "id": 158, "method": "tools/call",
                    "params": {"name": "sim_spice", "arguments": {
                        "netlist": "* i\nV1 1 0 1\nA1 1 2 i1\n"
                                   ".model i1 integrator(gain=1)\nR1 2 0 1k\n.op\n",
                        "engine": "builtin"}}})
        payload = json.loads(r["result"]["content"][0]["text"])
        _m = payload.get("message") or ""
        check("a code model that carries STATE is still refused by name, and the "
              "refusal says which four models this solver does have (integrator "
              "is not silently turned into a wire)",
              payload.get("status") == "error"
              and payload.get("reason_code") == "NETLIST_INVALID"
              and "INTEGRATOR" in _m
              and "only gain, limit, summer and mult" in _m,
              _m[:80])

        r = handle({"jsonrpc": "2.0", "id": 159, "method": "tools/call",
                    "params": {"name": "sim_spice", "arguments": {
                        "netlist": "* q\nV1 1 0 0.9\nA1 1 2 q1\n"
                                   ".model q1 limit(gain=1 out_lower_limit=-1 "
                                   "out_upper_limit=1 limit_range=0.5)\n"
                                   "R1 2 0 1k\n.op\n",
                        "engine": "builtin"}}})
        payload = json.loads(r["result"]["content"][0]["text"])
        _m = payload.get("message") or ""
        check("limit_range is refused instead of hard-clamped: the engine settles "
              "0.9 V in at 0.82 V with limit_range=0.5, so a hard clamp would "
              "report a number exactly where the caller is asking about the corner",
              payload.get("status") == "error" and "limit_range" in _m,
              _m[:80])

        # 0.0.40: the MULTI-PORT algebraic models. Two input nodes come in as an
        # array connection on the card and as VECTORS in the .model; the builtin
        # must land on the same 53 V the bundled engine reads (2*(3+1)+3*(4+1),
        # then *2 + 7), and it must say which nodes it summed.
        _SUMMER = ("* summer\nV1 1 0 3\nV2 2 0 4\nA1 [1 2] 10 s1\n"
                   ".model s1 summer(in_gain=[2 3] in_offset=[1 1] "
                   "out_gain=2 out_offset=7)\nR1 10 0 1k\n.op\n")
        _MULT = ("* mult\nV1 1 0 3\nV2 2 0 4\nA1 [1 2] 10 m1\n"
                 ".model m1 mult(in_gain=[2 3] in_offset=[1 1] "
                 "out_gain=2 out_offset=7)\nR1 10 0 1k\n.op\n")
        r = handle({"jsonrpc": "2.0", "id": 160, "method": "tools/call",
                    "params": {"name": "sim_spice", "arguments": {
                        "netlist": _SUMMER, "engine": "builtin"}}})
        payload = json.loads(r["result"]["content"][0]["text"])
        _v = (payload.get("value") or {}).get("nodes_v") or {}
        _a = [e for e in ((payload.get("value") or {}).get("elements") or [])
              if e.get("type") == "A"]
        check("0.0.40: the builtin SUMS a multi-port block itself - summer with "
              "in_gain=[2 3] in_offset=[1 1] out_gain=2 out_offset=7 on 3 V,4 V "
              "reads 53 V, the same number the bundled engine reads",
              payload.get("status") == "ok" and abs(_v.get("10", 0.0) - 53.0) < 1e-9
              and len(_a) == 1 and _a[0].get("kind") == "SUMMER"
              and _a[0].get("in_gain") == [2.0, 3.0]
              and _a[0].get("inputs") == ["1", "2"],
              str(_v) + str(_a)[:160])

        # ... and a PRODUCT is not a sum: same shape, 247 V. The pair is the
        # control for each other, since either formula summing would leave the
        # other's assertion standing.
        r = handle({"jsonrpc": "2.0", "id": 161, "method": "tools/call",
                    "params": {"name": "sim_spice", "arguments": {
                        "netlist": _MULT, "engine": "builtin"}}})
        payload = json.loads(r["result"]["content"][0]["text"])
        _v = (payload.get("value") or {}).get("nodes_v") or {}
        _a = [e for e in ((payload.get("value") or {}).get("elements") or [])
              if e.get("type") == "A"]
        check("0.0.40: mult MULTIPLIES the same two ports - 2*(3+1) * 3*(4+1) * 2 "
              "+ 7 = 247 V, so the two multi-port models are not one code path",
              payload.get("status") == "ok" and abs(_v.get("10", 0.0) - 247.0) < 1e-9
              and len(_a) == 1 and _a[0].get("kind") == "MULT",
              str(_v) + str(_a)[:160])

        # the refusal that has to survive: a vector whose length does not match
        # the port count is an engine error, so it must not be padded here.
        r = handle({"jsonrpc": "2.0", "id": 162, "method": "tools/call",
                    "params": {"name": "sim_spice", "arguments": {
                        "netlist": "* s\nV1 1 0 3\nV2 2 0 4\nA1 [1 2] 10 s1\n"
                                   ".model s1 summer(in_gain=[2 3 4])\n"
                                   "R1 10 0 1k\n.op\n",
                        "engine": "builtin"}}})
        payload = json.loads(r["result"]["content"][0]["text"])
        _m = payload.get("message") or ""
        check("a vector parameter whose length does not match the port count is "
              "refused, not padded or truncated (the engine refuses that deck "
              "outright), so no made-up port count reaches the answer",
              payload.get("status") == "error"
              and payload.get("reason_code") == "NETLIST_INVALID"
              and "3 value(s) for 2 input node(s)" in _m,
              _m[:90])

        # 0.0.41: the two bias-dependent models in .ac. Until 0.0.40 a limit or
        # mult block was REFUSED in a sweep because its small-signal gain depends on
        # the operating point; now the sweep linearises at that point, and the pair
        # below is the whole point - the SAME block passes its gain inside the band
        # and nothing outside it, both numbers measured on the bundled engine first.
        _LIM = (".model lim1 limit(gain=2 out_lower_limit=-1 out_upper_limit=1)"
                "\nR1 10 0 1k\n.ac dec 1 1k 2k\n")
        _reads = []
        for _dc in (0.2, 2.0):
            r = handle({"jsonrpc": "2.0", "id": 163, "method": "tools/call",
                        "params": {"name": "sim_spice", "arguments": {
                            "netlist": ("* l\nV1 1 0 DC %g AC 1\nA1 1 10 lim1\n"
                                        % _dc) + _LIM,
                            "engine": "builtin"}}})
            payload = json.loads(r["result"]["content"][0]["text"])
            _pts = ((payload.get("value") or {}).get("points") or [{}])
            _reads.append((payload.get("status"),
                           (_pts[0].get("nodes_v") or {}).get("10") or {}))
            if _dc == 2.0:
                _warn = " ".join(payload.get("warnings") or [])
        check("0.0.41: a limit block IS swept, linearised at its bias point - 0.2 V "
              "in (0.4 V out, inside the band) passes the full gain 2.0, while 2 V "
              "in is clipped and passes 0.0; the clipped case says so instead of "
              "looking like a broken deck",
              _reads[0][0] == "ok" and abs(_reads[0][1].get("re", 0.0) - 2.0) < 1e-9
              and _reads[1][0] == "ok"
              and abs(_reads[1][1].get("re", 9.0)) < 1e-12
              and "small-signal gain is 0" in _warn,
              str(_reads) + _warn[:80])

        # ... and a multiplier's small-signal gain is the OTHER input's bias value,
        # which no constant could reproduce: 2*2 * 3*(4+1) = 60, then *2 + 7's bias
        # is a DC offset and contributes nothing to the slope.
        r = handle({"jsonrpc": "2.0", "id": 164, "method": "tools/call",
                    "params": {"name": "sim_spice", "arguments": {
                        "netlist": "* m\nV1 1 0 DC 2 AC 1\nV2 2 0 DC 4\n"
                                   "A1 [1 2] 10 mul1\n"
                                   ".model mul1 mult(in_gain=[2 3] "
                                   "in_offset=[1 1] out_gain=2 out_offset=7)\n"
                                   "R1 10 0 1k\n.ac dec 1 1k 2k\n",
                        "engine": "builtin"}}})
        payload = json.loads(r["result"]["content"][0]["text"])
        _pts = ((payload.get("value") or {}).get("points") or [{}])
        _v10 = (_pts[0].get("nodes_v") or {}).get("10") or {}
        check("0.0.41: a mult block is swept at its bias too - the slope is the "
              "other input's value (2*2 * 3*(4+1) = 60 V per volt on node 1), not a "
              "gain somebody picked",
              payload.get("status") == "ok"
              and abs(_v10.get("re", 0.0) - 60.0) < 1e-9,
              str(_v10))

        # 0.0.30: the lossless line in the builtin. DC is a wire, so the matched
        # 3 V / 50 ohm divider must read 1.500 V at BOTH ports and +/-30 mA.
        r = handle({"jsonrpc": "2.0", "id": 145, "method": "tools/call",
                    "params": {"name": "sim_spice", "arguments": {
                        "netlist": "* t\nV1 1 0 3\nR1 1 2 50\n"
                                   "T1 2 0 3 0 Z0=50 TD=1n\nR2 3 0 50\n.op\n",
                        "engine": "builtin"}}})
        payload = json.loads(r["result"]["content"][0]["text"])
        _n = (payload.get("value") or {}).get("nodes_v") or {}
        _el = [e for e in ((payload.get("value") or {}).get("elements") or [])
               if e.get("name") == "T1"]
        check("builtin T at DC is a wire: the far port reads the same 1.500 V as "
              "the near one, and the card reports +/-30 mA",
              payload.get("status") == "ok"
              and abs(_n.get("2", 0.0) - 1.5) < 1e-6
              and abs(_n.get("3", 0.0) - 1.5) < 1e-6
              and bool(_el)
              and abs(_el[0]["port1"]["current_a"] - 0.03) < 1e-9
              and abs(_el[0]["port2"]["current_a"] + 0.03) < 1e-9,
              str(_n))

        # 0.0.30: and the delay is an observable. Before TD the far end has not
        # seen the step; after it, it has - which a line implemented as a plain
        # wire could not show.
        r = handle({"jsonrpc": "2.0", "id": 146, "method": "tools/call",
                    "params": {"name": "sim_spice", "arguments": {
                        "netlist": "* t\nV1 1 0 PULSE(0 3 0 1p)\nR1 1 2 50\n"
                                   "T1 2 0 3 0 Z0=50 TD=1n\nR2 3 0 50\n"
                                   ".tran 0.1n 5n\n",
                        "engine": "builtin"}}})
        payload = json.loads(r["result"]["content"][0]["text"])
        _pts = (payload.get("value") or {}).get("points") or []
        _before = [p["nodes_v"]["3"] for p in _pts if p["t"] <= 0.9e-9]
        check("builtin T in .tran delays the step: the far end is 0 V before TD "
              "and 1.500 V after it",
              payload.get("status") == "ok"
              and bool(_pts)
              and bool(_before)
              and max(abs(v) for v in _before) < 1e-6
              and abs(_pts[-1]["nodes_v"]["3"] - 1.5) < 1e-6,
              "before=%r after=%r" % (_before[-1] if _before else None,
                                      _pts[-1]["nodes_v"]["3"] if _pts else None))

        # 0.0.31: the lossy line O becomes a lumped ladder of the model's own
        # r/l/g/c at parse time, so the section count is a MODEL parameter and it
        # is disclosed. With no shunt conductance at DC the ladder IS a chain of
        # resistors: 1 ohm inside the same 3 V / 50 ohm divider reads the exact
        # divider (3*51/101, 3*50/101), independently of the section count - a
        # ladder built with r*len per SECTION would scale with it instead.
        r = handle({"jsonrpc": "2.0", "id": 147, "method": "tools/call",
                    "params": {"name": "sim_spice", "arguments": {
                        "netlist": "* o\nV1 1 0 3\nR1 1 2 50\nO1 2 0 3 0 ltra1\n"
                                   "R2 3 0 50\n"
                                   ".model ltra1 LTRA(r=1 l=250n g=0 c=100p len=1)\n"
                                   ".op\n",
                        "engine": "builtin"}}})
        payload = json.loads(r["result"]["content"][0]["text"])
        _n = (payload.get("value") or {}).get("nodes_v") or {}
        _el = [e for e in ((payload.get("value") or {}).get("elements") or [])
               if e.get("name") == "O1"]
        _dflt = (payload.get("assumptions") or {}).get("defaulted_model_params") or {}
        check("builtin O at DC is its series resistance: 1 ohm in the 3 V/50 ohm "
              "divider reads 1.514851/1.485149 V, the defaulted 20 sections are "
              "disclosed, and the ladder's own nodes are not reported as nodes",
              payload.get("status") == "ok"
              and abs(_n.get("2", 0.0) - 3.0 * 51.0 / 101.0) < 1e-6
              and abs(_n.get("3", 0.0) - 3.0 * 50.0 / 101.0) < 1e-6
              and sorted(_n) == ["1", "2", "3"]
              and _dflt.get("O1.lump") == 20
              and bool(_el) and _el[0].get("segments") == 20
              and abs(_el[0]["port1"]["current_a"] - 3.0 / 101.0) < 1e-9,
              "%r / lump %r" % (_n, _dflt))

        # 0.0.31: the uniform distributed RC line U. Its ladder must converge to
        # the DISTRIBUTED answer, not to a resistor: r=1 kohm/m, c=1 nF/m, 1 m,
        # far end open, 50 ohm source. Z_in = Z0c*coth(gL) gives |V(2)| =
        # 0.909627 at 1 MHz; 60 sections read 0.909951 (measured).
        r = handle({"jsonrpc": "2.0", "id": 148, "method": "tools/call",
                    "params": {"name": "sim_spice", "arguments": {
                        "netlist": "* u\nV1 1 0 AC 1\nR1 1 2 50\nU1 2 3 0 urc1\n"
                                   ".model urc1 URC(r=1000 c=1n len=1 lump=60)\n"
                                   ".ac lin 2 1meg 2meg\n",
                        "engine": "builtin"}}})
        payload = json.loads(r["result"]["content"][0]["text"])
        _pts = (payload.get("value") or {}).get("points") or []
        _m = ((_pts[0]["nodes_v"] if _pts else {}) or {}).get("2", {}).get("mag")
        check("builtin U (distributed RC) at 1 MHz reads 0.909951 V on a 1 V source, "
              "i.e. the distributed Z0c*coth(gL) divider 0.909627 to 0.04 %",
              payload.get("status") == "ok"
              and _m is not None and abs(_m - 0.909627) < 1e-3,
              str(_m))

        # 0.0.32: .noise at the DC bias, one adjoint solve per frequency. The
        # numbers below are the closed forms, not the solver's own output: at 1 Hz
        # a 1 kohm / 1 uF low-pass must read 4kT*R/(1+(2*pi*f*R*C)^2) and the band
        # integral 1 Hz..1 MHz must read the arctan form. sqrt(kT/C) is the whole
        # band, so the swept band has to come in under it.
        r = handle({"jsonrpc": "2.0", "id": 149, "method": "tools/call",
                    "params": {"name": "sim_spice", "arguments": {
                        "netlist": "* rc\nV1 1 0 AC 1\nR1 1 2 1k\nC1 2 0 1u\n"
                                   ".noise V(2) V1 dec 10 1 1meg\n",
                        "engine": "builtin"}}})
        payload = json.loads(r["result"]["content"][0]["text"])
        _v = payload.get("value") or {}
        _p0 = (_v.get("points") or [{}])[0]
        _int = _v.get("integrated") or {}
        _src = _v.get("sources") or []
        _kT = 1.380649e-23 * 300.15
        _want1 = (4.0 * _kT * 1000.0 / (1.0 + (2.0 * 3.141592653589793 * 1.0
                                              * 1000.0 * 1e-6) ** 2)) ** 0.5
        _RC = 1000.0 * 1e-6
        _k = 4.0 * _kT * 1000.0 / (2.0 * 3.141592653589793 * _RC)
        _atan = lambda f: _math.atan(2.0 * 3.141592653589793 * f * _RC)
        _band = (_k * (_atan(1e6) - _atan(1.0))) ** 0.5
        _total = (1.380649e-23 * 300.15 / 1e-6) ** 0.5
        check("builtin .noise: a 1 k / 1 uF low-pass reads 4.071291874e-09 V/sqrt(Hz) "
              "at 1 Hz and 6.424132e-08 V rms over 1 Hz..1 MHz, i.e. the arctan band "
              "integral to 0.01 % and under sqrt(kT/C)=6.437405e-08",
              payload.get("status") == "ok"
              and len(_v.get("points") or []) == 61
              and abs(_p0.get("onoise_v_per_sqrt_hz", 0.0) / _want1 - 1.0) < 1e-6
              and abs(_int.get("onoise_v_rms", 0.0) / _band - 1.0) < 1e-4
              and 0.0 < _int.get("onoise_v_rms", 0.0) < _total
              and _int.get("band_hz") == [1.0, 1000000.0]
              and len(_src) == 1 and _src[0].get("element") == "R1"
              and abs(_src[0].get("psd_a2_per_hz", 0.0) / (4.0 * _kT / 1000.0)
                      - 1.0) < 1e-12,
              "%r / %r / %r" % (_p0.get("onoise_v_per_sqrt_hz"),
                                _int.get("onoise_v_rms"), _src))

        # 0.0.32: the two refusals that keep the number above from being an
        # accident - a node nothing connects (a typo would come back beautifully
        # quiet) and a circuit with no noise source at all (which must read 0 with
        # an empty source list, not a default density).
        r = handle({"jsonrpc": "2.0", "id": 150, "method": "tools/call",
                    "params": {"name": "sim_spice", "arguments": {
                        "netlist": "* bad\nV1 1 0 AC 1\nR1 1 0 1k\n"
                                   ".noise V(9) V1 dec 2 1 10\n",
                        "engine": "builtin"}}})
        _bad = json.loads(r["result"]["content"][0]["text"])
        r = handle({"jsonrpc": "2.0", "id": 150, "method": "tools/call",
                    "params": {"name": "sim_spice", "arguments": {
                        "netlist": "* silent\nV1 1 0 AC 1\nC1 1 0 1u\n"
                                   ".noise V(1) V1 dec 10 1 100\n",
                        "engine": "builtin"}}})
        _sil = json.loads(r["result"]["content"][0]["text"])
        _sv = _sil.get("value") or {}
        check("builtin .noise refuses an unconnected output node by name and reports "
              "0 V with an empty source list when nothing makes noise",
              _bad.get("status") == "error"
              and _bad.get("reason_code") == "NETLIST_INVALID"
              and "'9'" in (_bad.get("message") or "")
              and _sil.get("status") == "ok"
              and (_sv.get("sources") or []) == []
              and (_sv.get("integrated") or {}).get("onoise_v_rms") == 0.0,
              "%r / %r" % (_bad.get("reason_code"), _sv.get("sources")))

        # filter synthesis: the prototype table is a textbook value, and the
        # lumped bandpass is scored against the closed form it must agree with.
        r = handle({"jsonrpc": "2.0", "id": 97, "method": "tools/call",
                    "params": {"name": "filter_prototype", "arguments": {
                        "order": 3, "response": "chebyshev", "ripple_db": 0.5}}})
        payload = json.loads(r["result"]["content"][0]["text"])
        _g = ((payload.get("value") or {}).get("g") or [])
        check("filter_prototype: 0.5 dB Chebyshev n=3 gives 1.5963/1.0967/1.5963/1",
              payload.get("status") == "ok" and len(_g) == 4
              and abs(_g[0] - 1.596331) < 1e-5 and abs(_g[1] - 1.096681) < 1e-5
              and abs(_g[3] - 1.0) < 1e-9,
              str(_g))

        r = handle({"jsonrpc": "2.0", "id": 98, "method": "tools/call",
                    "params": {"name": "filter_lumped_bandpass", "arguments": {
                        "order": 3, "f0_hz": 2.4e9, "fbw": 0.1}}})
        payload = json.loads(r["result"]["content"][0]["text"])
        _be = ((payload.get("value") or {}).get("band_edges") or {})
        _sc = ((payload.get("value") or {}).get("self_check") or {})
        check("filter_lumped_bandpass: 0 dB at f0, -0.5 dB at both edges, "
              "ABCD and closed form agree <0.02 dB",
              payload.get("status") == "ok"
              and abs(_be.get("f_center_il_db", 9.0)) < 1e-6
              and abs(_be.get("f_lower_il_db", 9.0) - 0.5) < 0.01
              and abs(_be.get("f_upper_il_db", 9.0) - 0.5) < 0.01
              and _sc.get("max_abs_deviation_db", 9.0) < 0.02,
              "%.3g dB" % (_sc.get("max_abs_deviation_db", 9.0),))

        # TE101 in WR-90 at 10 GHz: length is closed form, and Qc is computed
        # twice (bracket vs wall integral) and must agree.
        r = handle({"jsonrpc": "2.0", "id": 99, "method": "tools/call",
                    "params": {"name": "filter_waveguide_cavity", "arguments": {
                        "f0_hz": 10e9, "a_mm": 22.86, "b_mm": 10.16}}})
        payload = json.loads(r["result"]["content"][0]["text"])
        _v = payload.get("value") or {}
        check("filter_waveguide_cavity: TE101 length 19.8536 mm and Qc agrees "
              "two ways (7821.5)",
              payload.get("status") == "ok"
              and abs(_v.get("length_mm", 0.0) - 19.8536) < 0.01
              and abs(_v.get("q_conductor", 0.0) - 7821.5) < 60.0
              and abs(_v.get("q_agreement_pct", 9.0)) < 2.0,
              "%.4g mm / Q %.6g" % (_v.get("length_mm", 0.0),
                                    _v.get("q_conductor", 0.0)))

        # cross-coupled coupling matrix: three independent audits in one answer -
        # the lossless inline network against the prototype polynomial, unitarity,
        # and the trisection null against its cofactor formula.  A quarter-wave
        # scaling error in any coupling breaks at least one of them.
        r = handle({"jsonrpc": "2.0", "id": 119, "method": "tools/call",
                    "params": {"name": "filter_coupling_matrix", "arguments": {
                        "order": 3, "f0_hz": 2.6e9, "fbw": 0.05, "ripple_db": 0.1}}})
        payload = json.loads(r["result"]["content"][0]["text"])
        _v = payload.get("value") or {}
        _sc = _v.get("self_check") or {}
        _in = _v.get("inband") or {}
        check("filter_coupling_matrix: inline lossless reproduces the prototype "
              "polynomial <1e-6 dB and is unitary <1e-9",
              payload.get("status") == "ok"
              and _sc.get("closed_form_max_deviation_db", 9.0) < 1e-6
              and _sc.get("unitarity_max_error", 9.0) < 1e-9
              and abs((_sc.get("effective_ripple_db") or 9.0) - 0.1) < 1e-3,
              "dev %.3g dB / |S|^2-1 %.3g" % (_sc.get("closed_form_max_deviation_db", 9.0),
                                              _sc.get("unitarity_max_error", 9.0)))
        check("filter_coupling_matrix: the in-band ripple is read off its own 401-point "
              "grid and comes back as the requested 0.1 dB",
              _in.get("sampled_points_in_band", 0) == 401
              and abs(_in.get("ripple_actual_db", 0.0) - 0.1) < 1e-3,
              "%.4g dB over %d points" % (_in.get("ripple_actual_db", 0.0),
                                          _in.get("sampled_points_in_band", 0) or 0))

        r = handle({"jsonrpc": "2.0", "id": 120, "method": "tools/call",
                    "params": {"name": "filter_coupling_matrix", "arguments": {
                        "order": 3, "f0_hz": 2.6e9, "fbw": 0.05, "ripple_db": 0.1,
                        "zero_hz": 2.86e9, "span": 4.0}}})
        payload = json.loads(r["result"]["content"][0]["text"])
        _v = payload.get("value") or {}
        _z = ((_v.get("transmission_zeros") or {}).get("zeros") or [{}])[0]
        check("filter_coupling_matrix: the trisection null sits where the cofactor puts "
              "it (Omega_z = m12^2/m13 = 3.818182) and is a true null",
              payload.get("status") == "ok"
              and abs(_z.get("measured_omega", 0.0) - 3.8181818181818183) < 1e-6
              and _z.get("depth_db", 0.0) > 100.0,
              "Omega %.9g / depth %.6g dB" % (_z.get("measured_omega", 0.0),
                                              _z.get("depth_db", 0.0)))

        # --- IPC-2152, now a closed-form fit instead of an empty table. The gold
        # value is hand-computed from the published fit; the second check asserts a
        # physical property (internal is NOT derated 2x) that a wrong coefficient set
        # cannot satisfy; the third asserts the validator would notice.
        r = handle({"jsonrpc": "2.0", "id": 100, "method": "tools/call",
                    "params": {"name": "width_for_current", "arguments": {
                        "current_a": 1, "delta_t_c": 10, "copper_oz": 1,
                        "layer": "external", "standard": "ipc2152"}}})
        payload = json.loads(r["result"]["content"][0]["text"])
        check("ipc2152 1A/10C/1oz external is 0.2773 mm (Brooks & Adam fit)",
              payload.get("status") == "ok" and abs(payload.get("value", 0) - 0.2773) < 0.002,
              str(payload.get("value")))

        _ext = payload.get("value", 0.0)
        r = handle({"jsonrpc": "2.0", "id": 101, "method": "tools/call",
                    "params": {"name": "width_for_current", "arguments": {
                        "current_a": 1, "delta_t_c": 10, "copper_oz": 1,
                        "layer": "internal", "standard": "ipc2152"}}})
        payload = json.loads(r["result"]["content"][0]["text"])
        _int = payload.get("value") or 0.0
        check("ipc2152 internal is not derated 2x (%.4f < %.4f mm)" % (_int, _ext),
              0.8 * _ext < _int < _ext)

        r = handle({"jsonrpc": "2.0", "id": 102, "method": "tools/call",
                    "params": {"name": "safety_table_check",
                               "arguments": {"which": "ipc2152"}}})
        _st = json.loads(r["result"]["content"][0]["text"])
        check("safety_table_check(ipc2152) is usable and names its model checks",
              _st.get("usable") is True
              and "model_reproduces_anchors" in [c["check"] for c in _st["checks"]],
              str(_st.get("failed")))

        # --- IEC 60664-1, recovered from KiCad 10.0's compiled implementation.
        # 250 V rms / PD2 / material group IIIa / basic is a plain Table F.5 step;
        # the spacing tool then has to apply the "creepage >= clearance" rule.
        r = handle({"jsonrpc": "2.0", "id": 103, "method": "tools/call",
                    "params": {"name": "creepage_required", "arguments": {
                        "voltage_v": 250, "pollution_degree": 2,
                        "material_group": "IIIa", "safety_standard": "IEC 60664-1"}}})
        _cp = json.loads(r["result"]["content"][0]["text"])
        check("creepage 250 V / PD2 / IIIa / basic is 2.5 mm (Table F.5)",
              _cp.get("status") == "ok" and abs(_cp.get("value", 0) - 2.5) < 1e-9,
              str(_cp.get("value")))

        r = handle({"jsonrpc": "2.0", "id": 104, "method": "tools/call",
                    "params": {"name": "spacing_required", "arguments": {
                        "voltage_v": 400, "transient_v": 2500,
                        "pollution_degree": 2, "material_group": "IIIa",
                        "safety_standard": "IEC 60664-1"}}})
        _sp = json.loads(r["result"]["content"][0]["text"])
        check("spacing at 400 V takes the larger of creepage 4.0 / clearance 1.5",
              _sp.get("status") == "ok"
              and abs(_sp.get("value", 0) - 4.0) < 1e-9
              and _sp.get("governing") == "creepage"
              and _sp.get("creepage_mm", 0) >= _sp.get("clearance_mm", 0),
              "%s / %s" % (_sp.get("value"), _sp.get("governing")))

        r = handle({"jsonrpc": "2.0", "id": 105, "method": "tools/call",
                    "params": {"name": "creepage_required", "arguments": {
                        "voltage_v": 250, "pollution_degree": 4,
                        "safety_standard": "IEC 60664-1"}}})
        _pd4 = json.loads(r["result"]["content"][0]["text"])
        check("PD4 creepage is refused as out of scope, not answered 1.6 mm",
              _pd4.get("status") == "error"
              and _pd4.get("reason_code") == "STANDARD_SCOPE",
              str(_pd4.get("reason_code")))

        r = handle({"jsonrpc": "2.0", "id": 106, "method": "tools/call",
                    "params": {"name": "safety_table_check",
                               "arguments": {"which": "iec60664"}}})
        _iec = json.loads(r["result"]["content"][0]["text"])
        check("safety_table_check(iec60664) is usable over 32 ladders / 808 steps",
              _iec.get("usable") is True and _iec.get("ladders") == 32
              and _iec.get("steps") == 808,
              "%s / %s / %s" % (_iec.get("usable"), _iec.get("ladders"),
                                _iec.get("steps")))

        # --- board-level 2-D electro-thermal. A uniform load has a closed form,
        # so the field solve is scored against arithmetic, not against itself.
        r = handle({"jsonrpc": "2.0", "id": 107, "method": "tools/call",
                    "params": {"name": "board_electrothermal", "arguments": {
                        "width_mm": 50, "height_mm": 50, "thickness_mm": 1.6,
                        "k_w_mk": 0.3, "h_w_m2k": 10, "uniform_power_w": 2.0,
                        "cell_mm": 2.0}}})
        _be = json.loads(r["result"]["content"][0]["text"])
        _hca = 2 * 10 * (50e-3 / 25) ** 2 * 625
        _ana = (_hca * 25 + 2.0 * (1 - 0.00393 * 20)) / (_hca - 2.0 * 0.00393)
        check("2-D electro-thermal matches the closed form (%.4f C, %.4f C)"
              % (_be.get("t_max_c", -1), _ana),
              _be.get("status") == "ok"
              and abs(_be.get("t_max_c", 0) - _ana) < 0.01,
              str(_be.get("t_max_c")))

        r = handle({"jsonrpc": "2.0", "id": 108, "method": "tools/call",
                    "params": {"name": "board_electrothermal", "arguments": {
                        "width_mm": 50, "height_mm": 50, "thickness_mm": 1.6,
                        "k_w_mk": 0.3, "h_w_m2k": 10, "uniform_power_w": 2.0,
                        "cell_mm": 2.0, "alpha_per_k": 0.0,
                        "transient": {"t_end_s": 5000, "points": 26,
                                      "cp_j_kg_k": 1150, "rho_kg_m3": 1850}}}})
        _bt = json.loads(r["result"]["content"][0]["text"])
        _hist = ((_bt.get("transient") or {}).get("history") or [])
        # 1e-3 K of slack is the SOR tolerance, not physics: the field sits on its
        # fixed point to within tol_k, so the last few samples can wobble by that.
        _mono = all(_hist[k + 1]["t_max_c"] >= _hist[k]["t_max_c"] - 1e-3
                    for k in range(len(_hist) - 1))
        check("2-D transient rises monotonically to 25 + 2 W / 0.05 W/K = 65 C",
              _bt.get("status") == "ok" and _mono and _hist
              and abs(_hist[-1]["t_max_c"] - 65.0) < 0.01
              and abs(_hist[-1]["t_s"] - 5000.0) < 1e-9,
              "%s over %s s (%d samples)"
              % (_hist[-1]["t_max_c"] if _hist else None,
                 _hist[-1]["t_s"] if _hist else None, len(_hist)))

        r = handle({"jsonrpc": "2.0", "id": 109, "method": "tools/call",
                    "params": {"name": "board_electrothermal", "arguments": {
                        "width_mm": 50, "height_mm": 50, "thickness_mm": 1.6,
                        "k_w_mk": 0.3, "h_w_m2k": 10, "uniform_power_w": 13.0,
                        "cell_mm": 2.0}}})
        _br = json.loads(r["result"]["content"][0]["text"])
        check("2-D runaway is refused before the solve when 13 W * alpha > 2hA",
              _br.get("status") == "error"
              and _br.get("reason_code") == "THERMAL_RUNAWAY",
              str(_br.get("reason_code")))

        # --- the through-thickness stack (0.0.58). Two levels with a uniform
        # source on the top level have an exact answer with ONE convection face
        # per surface level, so this scores the layered solve against arithmetic.
        _w, _hh, _th = 50.0, 50.0, 1.6
        _a = (_w * 1e-3) * (_hh * 1e-3)
        _stack = {"copper_layers": [{"z_mm": 0.0, "thickness_um": 0.0},
                                    {"z_mm": _th, "thickness_um": 0.0}]}
        r = handle({"jsonrpc": "2.0", "id": 1091, "method": "tools/call",
                    "params": {"name": "board_electrothermal", "arguments": {
                        "width_mm": _w, "height_mm": _hh, "thickness_mm": _th,
                        "k_w_mk": 0.3, "h_w_m2k": 10, "cell_mm": 2.0,
                        "alpha_per_k": 0.0, "layer_stack": _stack,
                        "conductors": [{"x1_mm": 0.0, "y1_mm": _hh / 2,
                                        "x2_mm": _w, "y2_mm": _hh / 2,
                                        "width_mm": _hh, "thickness_um": 35,
                                        "current_a": 20.0, "z_mm": _th}]}}})
        _ls = json.loads(r["result"]["content"][0]["text"])
        _hA = 10.0 * _a
        _G = 0.3 * _a / (_th * 1e-3)
        _q = _ls.get("total_power_at_20c_w") or 0.0
        _ut = _q * (_hA + _G) / (_hA * (_hA + 2 * _G))
        _ub = _q * _G / (_hA * (_hA + 2 * _G))
        _lvs = ((_ls.get("layer_stack") or {}).get("levels") or [])
        check("layered stack matches the 2-level closed form (%.6f/%.6f C)"
              % (25 + _ut, 25 + _ub),
              _ls.get("status") == "ok" and len(_lvs) == 2
              and abs(_lvs[1]["t_max_c"] - (25 + _ut)) < 1e-3
              and abs(_lvs[0]["t_max_c"] - (25 + _ub)) < 1e-3
              and _lvs[0]["role"] == "bottom face"
              and _lvs[1]["role"] == "top face"
              and ((_ls.get("energy_balance") or {})
                   .get("residual_allowance_w") or 0) > 0,
              "%s %s" % (_lvs[1]["t_max_c"] if len(_lvs) > 1 else None,
                         _lvs[0]["t_max_c"] if _lvs else None))

        # k_z -> infinity means the levels are shorted, and then the stack MUST
        # reproduce the single plate: the regression anchor for the whole model.
        _tr = [{"x1_mm": 0.0, "y1_mm": _hh / 2, "x2_mm": 45.0, "y2_mm": _hh / 2,
                "width_mm": 2.0, "thickness_um": 35, "current_a": 3.0,
                "z_mm": _th}]
        _plate = json.loads(handle(
            {"jsonrpc": "2.0", "id": 1092, "method": "tools/call",
             "params": {"name": "board_electrothermal", "arguments": {
                 "width_mm": _w, "height_mm": _hh, "thickness_mm": _th,
                 "k_w_mk": 0.3, "h_w_m2k": 10, "cell_mm": 2.0,
                 "conductors": _tr}}})["result"]["content"][0]["text"])
        _short = json.loads(handle(
            {"jsonrpc": "2.0", "id": 1093, "method": "tools/call",
             "params": {"name": "board_electrothermal", "arguments": {
                 "width_mm": _w, "height_mm": _hh, "thickness_mm": _th,
                 "k_w_mk": 0.3, "h_w_m2k": 10, "cell_mm": 2.0,
                 "conductors": _tr, "tol_k": 1e-6,
                 "layer_stack": dict(_stack, k_z_w_mk=1e6)}}})
            ["result"]["content"][0]["text"])
        check("k_z -> infinity reproduces the single plate (%.6f vs %.6f C)"
              % (_short.get("t_max_c") or -1, _plate.get("t_max_c") or -1),
              _plate.get("status") == "ok" and _short.get("status") == "ok"
              and abs(_short.get("t_max_c") - _plate.get("t_max_c")) < 0.01,
              "%s vs %s" % (_short.get("t_max_c"), _plate.get("t_max_c")))

        # --- plate stress against the exact mode. A plate simply supported on all
        # four edges has w = A sin(pi x/a) sin(pi y/b) as its (1,1) mode, so the
        # surface stress at the centre is arithmetic, and the largest von Mises
        # stress is at a CORNER (the twisting curvature peaks there).
        r = handle({"jsonrpc": "2.0", "id": 110, "method": "tools/call",
                    "params": {"name": "vibration_plate_stress", "arguments": {
                        "a_mm": 100, "b_mm": 80, "thickness_mm": 1.6,
                        "amplitude_mm": 0.2, "material": "fr4", "mount": "ssss"}}})
        _psw = json.loads(r["result"]["content"][0]["text"])
        _ps = (_psw.get("value") or {})
        _E, _nu, _z = 24e9, 0.15, 0.8e-3
        _kxn = -(3.141592653589793 / 0.1) ** 2 * 0.2e-3
        _kyn = -(3.141592653589793 / 0.08) ** 2 * 0.2e-3
        _ez = _E * _z / (1 - _nu * _nu)
        _sxn = -_ez * (_kxn + _nu * _kyn)
        _syn = -_ez * (_kyn + _nu * _kxn)
        check("plate sigma_max sits at the bay centre and matches the sine "
              "(%.5f MPa, %.5f MPa)" % (_ps.get("sigma_max_mpa", -1), max(_sxn, _syn) / 1e6),
              _psw.get("status") == "ok"
              and _ps.get("sigma_max_at_mm") == [50.0, 40.0]
              and abs(_ps.get("sigma_max_mpa", 0) - max(_sxn, _syn) / 1e6) < 0.05,
              str(_ps.get("sigma_max_at_mm")))

        # the exact corner tensor is sigma_x = sigma_y = 0, tau = E z/(1+nu) w_xy,
        # so vm there is sqrt(3)*|tau| - a different point from sigma_max
        _kxy = (3.141592653589793 / 0.1) * (3.141592653589793 / 0.08) * 0.2e-3
        _taun = _E * _z / (1 + _nu) * _kxy
        check("plate von Mises peaks at a corner via tau_xy (%.4f MPa vs %s)"
              % (_ps.get("von_mises_mpa", -1), 3 ** 0.5 * _taun / 1e6),
              abs(_ps.get("von_mises_mpa", 0) - 3 ** 0.5 * _taun / 1e6) < 0.15
              and _ps.get("sigma_max_at_mm") != _ps.get("von_mises_at_mm"),
              str(_ps.get("von_mises_at_mm")))

        r = handle({"jsonrpc": "2.0", "id": 111, "method": "tools/call",
                    "params": {"name": "vibration_plate_stress", "arguments": {
                        "a_mm": 100, "b_mm": 80, "thickness_mm": 1.6,
                        "amplitude_mm": 0.2, "material": "fr4", "mount": "corners"}}})
        _pcw = json.loads(r["result"]["content"][0]["text"])
        _pc = (_pcw.get("value") or {})
        check("corner-supported plate reports NO error bar rather than a small one",
              _pcw.get("status") == "ok" and _pc.get("converged") is None
              and _pc.get("degree_sensitivity_pct") is None,
              str(_pc.get("converged")))

        # a plane pair is a cavity: |Z| of the (1,0) mode closed form
        #   w*mu0*t*psi(r)psi(r')*Q/k^2, psi = 2/(ab)*cos^2(pi/4) = 0.5*2/(ab)
        r = handle({"jsonrpc": "2.0", "id": 112, "method": "tools/call",
                    "params": {"name": "pdn_plane_modes", "arguments": {
                        "width_mm": 50, "height_mm": 50, "er": 4.4,
                        "thickness_mm": 1.6, "tan_delta": 0.02,
                        "port_mm": [12.5, 25.0]}}})
        _pw = json.loads(r["result"]["content"][0]["text"])
        _pv = (_pw.get("value") or {})
        _row = [m for m in (_pv.get("modes") or [])
                if (m.get("m"), m.get("n")) == (1, 0)]
        _a, _b, _t = 0.05, 0.05, 1.6e-3
        _k2 = (3.141592653589793 / _a) ** 2
        _f = 299792458.0 / (2.0 * _a * (4.4 ** 0.5))
        _want = (2 * 3.141592653589793 * _f * 4e-7 * 3.141592653589793
                 * _t * (2.0 / (_a * _b) * 0.5) * 50.0 / _k2)
        _got = _row[0].get("z_peak_ohm") if _row else None
        check("plane (1,0) peak matches the single-mode closed form (%.4f ohm vs %.4f)"
              % (_got or -1, _want),
              _pw.get("status") == "ok" and _got is not None
              and abs(_got - _want) < 0.001 * _want,
              str(_got))

        r = handle({"jsonrpc": "2.0", "id": 113, "method": "tools/call",
                    "params": {"name": "pdn_impedance", "arguments": {
                        "freqs_hz": [1e6, 1e8, 1e9, 3e9],
                        "caps": [{"c_farad": 1e-6, "esr_ohm": 0.01, "esl_h": 1e-9}],
                        "plane": {"width_mm": 50, "height_mm": 50, "er": 4.4,
                                  "thickness_mm": 1.6, "tan_delta": 0.02,
                                  "port_mm": [12.5, 25.0]}}}})
        _iw = json.loads(r["result"]["content"][0]["text"])
        _iv = (_iw.get("value") or {})
        _pm = _iv.get("plane_modes") or {}
        check("a plane mode inside the band outranks the lumped curve",
              _iw.get("status") == "ok"
              and bool(_pm.get("modes_in_band"))
              and _pm.get("worst_mode_in_band") is not None
              and "NOT the PDN" in str(_pm.get("verdict")),
              str((_pm.get("worst_mode_in_band") or {}).get("f_hz")))

        # --- 0.0.49, the plane CURVE. The sum used to be refused because a point
        # port makes it diverge; a finite pad is what gives it a limit. Three
        # independent readings, and the divergence itself as the counter-test.
        from .calc import power as _pwm
        _a = _b = 0.05
        _t = 1.6e-3
        _cpp = 8.8541878128e-12 * 4.4 * _a * _b / _t
        _f10 = 299792458.0 / (2.0 * _a * 4.4 ** 0.5)
        _flo = _f10 / 1000.0
        r = handle({"jsonrpc": "2.0", "id": 114, "method": "tools/call",
                    "params": {"name": "pdn_plane_impedance", "arguments": {
                        "width_mm": 50, "height_mm": 50, "er": 4.4,
                        "thickness_mm": 1.6, "tan_delta": 0.02,
                        "port_mm": [12.5, 25.0], "port_size_mm": 0.5,
                        "freqs_hz": [_flo, _f10]}}})
        _cw = json.loads(r["result"]["content"][0]["text"])
        _rows = _cw.get("curve") or []
        _lo = min(_rows, key=lambda q: abs(q["freq_hz"] - _flo)) if _rows else {}
        _zcap = 1.0 / (2.0 * 3.141592653589793 * _flo * _cpp)
        check("curve at f10/1000 IS the parallel-plate capacitance (%.4f vs %.4f ohm)"
              % (_lo.get("z_ohm", -1), _zcap),
              _cw.get("status") == "ok"
              and abs(_lo.get("z_ohm", 0.0) / _zcap - 1.0) < 0.01,
              str(_lo.get("freq_hz")))
        _q = _pwm._plane_q_of(_f10, _t, 0.02, _pwm.COPPER_SIGMA)[0]
        _psi = 2.0 / (_a * _b) * 0.5
        _w = 2.0 * 3.141592653589793 * _f10
        _peak = _w * 4e-7 * 3.141592653589793 * _t * _psi * 50.0 / (
            (3.141592653589793 / _a) ** 2)
        _peak *= _q * 0.02
        _hi = min(_rows, key=lambda q: abs(q["freq_hz"] - _f10)) if _rows else {}
        check("curve at f10 equals the single-mode peak x Q_total*tan_delta "
              "(%.4f vs %.4f ohm)" % (_hi.get("z_ohm", -1), _peak),
              abs(_hi.get("z_ohm", 0.0) / _peak - 1.0) < 0.01,
              str(_hi.get("freq_hz")))
        _p = (12.5e-3, 25.0e-3)
        _pt8 = abs(_pwm._plane_modal_z(2e9, _a, _b, _t, 4.4, 0.02,
                                      _pwm.COPPER_SIGMA, 8, _p, _p))
        _pt256 = abs(_pwm._plane_modal_z(2e9, _a, _b, _t, 4.4, 0.02,
                                        _pwm.COPPER_SIGMA, 256, _p, _p))
        _pad256 = abs(_pwm._plane_modal_z(2e9, _a, _b, _t, 4.4, 0.02,
                                         _pwm.COPPER_SIGMA, 256, _p, _p,
                                         0.5e-3, 0.5e-3))
        _pad384 = abs(_pwm._plane_modal_z(2e9, _a, _b, _t, 4.4, 0.02,
                                         _pwm.COPPER_SIGMA, 384, _p, _p,
                                         0.5e-3, 0.5e-3))
        check("point port still diverges (%.3f -> %.3f ohm, x%.2f) while the same "
              "sum with a 0.5 mm pad has a limit (%.3f -> %.3f ohm, x%.1f %%)"
              % (_pt8, _pt256, _pt256 / _pt8, _pad256, _pad384,
                 100.0 * (_pad384 / _pad256 - 1.0)),
              _pt256 > 2.0 * _pt8 and abs(_pad384 / _pad256 - 1.0) < 0.01,
              "%.3f/%.3f" % (_pt256, _pt8))

        # --- 0.0.50, the caps ON the planes. Four readings, each of which can
        # fail: the empty-cap limit is the plane tool, a cap on a mode null must
        # NOT damp that mode, and the anti-resonance must follow the cap's own
        # inductance down as the inductance goes up.
        _base = {"width_mm": 50, "height_mm": 50, "er": 4.4,
                 "thickness_mm": 1.6, "tan_delta": 0.02,
                 "port_mm": [12.5, 25.0], "port_size_mm": 0.5}
        _args = dict(_base)
        _args["caps"] = []
        _args["freqs_hz"] = [_flo, _f10]
        _nc = json.loads(handle({"jsonrpc": "2.0", "id": 1601,
                                 "method": "tools/call",
                                 "params": {"name": "pdn_plane_with_caps",
                                            "arguments": _args}})
                         ["result"]["content"][0]["text"])
        _nc_rows = _nc.get("curve") or []
        _nc_hi = min(_nc_rows, key=lambda q: abs(q["freq_hz"] - _f10)) if _nc_rows else {}
        check("with no caps the loaded answer IS the plane curve (%.4f vs %.4f ohm)"
              % (_nc_hi.get("z_ohm", -1), _hi.get("z_ohm", -1)),
              _nc.get("status") == "ok"
              and abs(_nc_hi.get("z_ohm", 0.0) / _hi.get("z_ohm", 1.0) - 1.0) < 1e-9,
              str(_nc_hi.get("freq_hz")))
        _mode2 = 299792458.0 / (2.0 * _a * 4.4 ** 0.5) * 2.0   # the (0,2) mode
        _z_mode = {}
        for _tag, _at in (("antinode", [25.0, 25.0]), ("null", [25.0, 12.5])):
            _a2 = dict(_base)
            _a2["caps"] = [{"at_mm": _at, "c_farad": 100e-9, "pad_mm": 1.0,
                            "esr_ohm": 0.01, "esl_h": 1e-9}]
            _a2["freqs_hz"] = [_mode2]
            _res = json.loads(handle({"jsonrpc": "2.0", "id": 1602,
                                      "method": "tools/call",
                                      "params": {"name": "pdn_plane_with_caps",
                                                 "arguments": _a2}})
                              ["result"]["content"][0]["text"])
            _row = (_res.get("curve") or [{}])[0]
            _z_mode[_tag] = (_row.get("z_ohm"), _row.get("plane_only_z_ohm"))
        check("a cap on the (0,2) antinode damps it (%.3f of %.3f ohm) while the same "
              "cap on its null does not (%.3f ohm)" % (
                  _z_mode.get("antinode", (0, 1))[0], _z_mode.get("antinode", (0, 1))[1],
                  _z_mode.get("null", (0, 0))[0]),
              _z_mode["antinode"][0] < 0.8 * _z_mode["antinode"][1]
              and abs(_z_mode["null"][0] / _z_mode["null"][1] - 1.0) < 0.01,
              str(_z_mode))
        _grid = [279e6, 440e6]
        _z_esl = {}
        for _esl in (1e-9, 4e-9):
            _a3 = dict(_base)
            _a3["caps"] = [{"at_mm": [25.0, 25.0], "c_farad": 100e-9, "pad_mm": 1.0,
                            "esr_ohm": 0.01, "esl_h": _esl}]
            _a3["freqs_hz"] = _grid
            _res = json.loads(handle({"jsonrpc": "2.0", "id": 1603,
                                      "method": "tools/call",
                                      "params": {"name": "pdn_plane_with_caps",
                                                 "arguments": _a3}})
                              ["result"]["content"][0]["text"])
            _z_esl[_esl] = [q["z_ohm"] for q in (_res.get("curve") or [])
                            if q["freq_hz"] in _grid]
        check("the cap/plane anti-resonance follows the cap inductance DOWN: 1 nH peaks "
              "at 440 MHz (%.2f ohm, 279 MHz reads %.2f) and 4 nH at 279 MHz "
              "(%.2f ohm, 440 MHz reads %.2f)"
              % (_z_esl[1e-9][1], _z_esl[1e-9][0], _z_esl[4e-9][0], _z_esl[4e-9][1]),
              _z_esl[1e-9][1] > 10.0 * _z_esl[1e-9][0]
              and _z_esl[4e-9][0] > 10.0 * _z_esl[4e-9][1],
              str(_z_esl))
        # --- 0.0.51, surface roughness. K is ONE formula, so the three readings
        # here are the ones that can fail: its anchors (a wrong exponent, a wrong
        # 1.4 or a missing square all move them), the sigma_eff identity (the same
        # loss reached through a second code path, sigma/K^2), and the dielectric
        # side staying bit-identical (a coefficient that leaked into Qd fails).
        from .calc.roughness import skin_depth_m as _skin_m
        from .calc.power import COPPER_SIGMA as _cu_sigma

        def _pq(_args, _id):
            _env = json.loads(handle({"jsonrpc": "2.0", "id": _id,
                                      "method": "tools/call",
                                      "params": {"name": "cavity_plane_q",
                                                 "arguments": _args}})
                               ["result"]["content"][0]["text"])
            _flat = dict(_env.get("value") or {})
            _flat.update({k: v for k, v in _env.items() if k != "value"})
            return _flat

        _sd_um = _skin_m(1e9, _cu_sigma) * 1e6      # one skin depth at 1 GHz, um
        _smooth = _pq({"f_hz": 1e9, "h_mm": 1.6, "tan_delta": 0.02}, 1604)
        _anchors = {}
        for _i, _mult in enumerate((1.0, 2.0, 10.0)):
            _r = _pq({"f_hz": 1e9, "h_mm": 1.6, "tan_delta": 0.02,
                      "roughness_um": _sd_um * _mult}, 1604 + _i)
            _anchors[_mult] = (_r.get("roughness") or {}).get("loss_factor")
        check("roughness K(delta_s) = %.5f, K(2 delta_s) = %.5f, K(10 delta_s) = "
              "%.5f and never reaches 2 (delta_s = %.4f um at 1 GHz in copper)"
              % (_anchors[1.0], _anchors[2.0], _anchors[10.0], _sd_um),
              abs(_anchors[1.0] - 1.60514) < 5e-5
              and abs(_anchors[2.0] - 1.88750) < 5e-5
              and abs(_anchors[10.0] - 1.99545) < 5e-5
              and 1.995 < _anchors[10.0] < 2.0,
              str(_anchors))

        # the same loss, said as a worse conductor: Rs*K == 1/(sigma_eff*delta_s)
        # with sigma_eff = sigma/K^2. Two calls, one identity - if K were applied
        # to the wrong quantity one of these numbers moves.
        _via_eff = _pq({"f_hz": 1e9, "h_mm": 1.6,
                        "conductivity_s_m": _cu_sigma / _anchors[2.0] ** 2}, 1607)
        _rough2 = _pq({"f_hz": 1e9, "h_mm": 1.6, "tan_delta": 0.02,
                       "roughness_um": 2.0 * _sd_um}, 1608)
        # qc goes as sqrt(sigma), so sigma -> sigma/K^2 gives qc -> qc/K: the
        # same loss as Rs *= K, reached through a conductivity instead. The K
        # (not K^2) on the Qc side is the whole point - an implementation that
        # divided by K^2 here would land on 214.9 instead of 405.6.
        check("sigma_eff = sigma/K^2 reproduces the roughened Qc through a second "
              "code path (%.6f vs %.6f, K = %.5f)"
              % (_via_eff.get("q_conductor"),
                 _smooth.get("q_conductor") / _anchors[2.0], _anchors[2.0]),
              abs(_via_eff.get("q_conductor", 0.0)
                  / (_smooth.get("q_conductor", 1.0) / _anchors[2.0]) - 1.0) < 5e-6,
              "%.9f" % (_via_eff.get("q_conductor", 0.0)
                        / (_smooth.get("q_conductor", 1.0) / _anchors[2.0])))

        check("roughness moves q_conductor only: q_dielectric is unchanged "
              "(%.6f vs %.6f) and the roughness block appears only when asked for"
              % (_rough2.get("q_dielectric", -1), _smooth.get("q_dielectric", -1)),
              _rough2.get("q_dielectric") == _smooth.get("q_dielectric")
              and "roughness" not in _smooth and "roughness" in _rough2
              and _rough2.get("q_conductor", 1e9) < 0.75 * _smooth.get("q_conductor", 0),
              "%s/%s" % (_rough2.get("q_conductor"), _smooth.get("q_conductor")))

        # --- 0.0.52, the cavity side: any TE_mnp / TM_mnp, the mode chart, and
        # roughness on Qc. The gates that can actually fail: (1) the general
        # closed form must reproduce the TE101 bracket formula EXACTLY - same
        # physics, two derivations, and a wrong sine/cosine pair in either one
        # moves it; (2) every mode must balance U_e/U_m = 1, which is the check
        # that caught a field expression with a missing factor during the
        # rewrite (the ratio is u_e/u_m, NOT u_e/(u_e+u_m): the latter is 0.5 for
        # every balanced mode and would be a gate with no teeth); (3) the chart
        # must contain the neighbours at the right places with the right
        # degeneracy, because "my filter is fine" is a claim about them.
        from .calc.filters import _cavity_q_numeric as _cqnum

        def _wc(_args, _id):
            _env = json.loads(handle({"jsonrpc": "2.0", "id": _id,
                                      "method": "tools/call",
                                      "params": {"name": "filter_waveguide_cavity",
                                                 "arguments": _args}})
                               ["result"]["content"][0]["text"])
            _flat = dict(_env.get("value") or {})
            _flat.update({k: v for k, v in _env.items() if k != "value"})
            return _flat

        _wr = [22.86e-3, 10.16e-3]                     # WR-90
        _t101 = _wc({"f0_hz": 10e9, "a_mm": 22.86, "b_mm": 10.16,
                     "modes_upto_hz": 20e9}, 1609)
        _chart = {q["mode"]: q for q in (_t101.get("mode_chart") or [])}
        _gen101 = (_chart.get("TE_1_0_1") or {}).get("q_conductor")
        check("the general closed form reproduces the TE101 bracket formula exactly "
              "(%.1f vs %.1f, length %.4f mm, R/Q %.4f ohm)"
              % (_gen101 or -1, _t101.get("q_conductor", -1),
                 _t101.get("length_mm", -1), _t101.get("r_over_q_ohm") or -1),
              _gen101 is not None
              and abs(_gen101 - _t101.get("q_conductor", 0.0)) <= 0.11
              and abs(_t101.get("length_mm", 0.0) - 19.8536) <= 1e-4
              and abs((_t101.get("r_over_q_ohm") or 0.0) - 160.9572) <= 1e-4,
              str(_t101.get("q_conductor")))

        _t011 = _wc({"f0_hz": 16.573220e9, "a_mm": 22.86, "b_mm": 10.16,
                     "mode": "TE_0_1_1"}, 1610)
        _bal = []
        for _fam, _m, _n, _p, _f in (("TE", 0, 1, 1, 16.573220e9),
                                     ("TE", 1, 1, 1, 17.823e9),
                                     ("TM", 1, 1, 1, 17.823e9),
                                     ("TM", 2, 1, 1, 25.6615e9)):
            _kx, _ky = _m * _math.pi / _wr[0], _n * _math.pi / _wr[1]
            _kc2 = _kx * _kx + _ky * _ky
            _k0 = 2.0 * _math.pi * _f / 299792458.0
            _d = _p * _math.pi / _math.sqrt(_k0 * _k0 - _kc2)
            _bal.append(_cqnum(_fam, _m, _n, _p, _wr[0], _wr[1], _d, _f, 0.026)[1])
        check("every mode balances stored energy (u_e/u_m = %s) and TE_0_1_1 answers "
              "at d = %.4f mm with a %.1f Qc that its own field integral confirms"
              % (", ".join("%.9f" % b for b in _bal), _t011.get("length_mm", -1),
                 _t011.get("q_conductor", -1)),
              all(abs(b - 1.0) <= 1e-6 for b in _bal)
              and abs(_t011.get("length_mm", 0.0) - 19.8536) <= 1e-4
              and _t011.get("q_agreement_pct") == 0.0
              and abs(_t011.get("q_conductor", 0.0) - 8828.7) <= 0.05,
              str(_bal))

        _deg = (_chart.get("TE_1_1_1") or {}).get("degeneracy")
        _tm110 = (_chart.get("TM_1_1_0") or {}).get("f_hz")
        check("the mode chart below 20 GHz holds %d modes, names TE_2_0_1 as the next "
              "one (x%.4f), puts TM_1_1_0 at %.4f GHz and shows TE_1_1_1/TM_1_1_1 as a "
              "degenerate pair (count %s)"
              % (_t101.get("mode_chart_count", -1),
                 ((_t101.get("next_mode") or {}).get("f_over_f0") or -1),
                 (_tm110 or 0.0) / 1e9, _deg),
              _t101.get("mode_chart_count") == 9
              and (_t101.get("next_mode") or {}).get("mode") == "TE_2_0_1"
              and abs((_tm110 or 0.0) - 16.1451e9) <= 1e6
              and _deg == 2,
              str(_t101.get("mode_chart_count")))

        _rsm = _wc({"f0_hz": 10e9, "a_mm": 22.86, "b_mm": 10.16,
                    "roughness_um": 2.0}, 1611)
        _k52 = (_rsm.get("roughness") or {}).get("loss_factor") or 1.0
        check("roughness divides this Qc by K = %.6f and says so: the reported smooth "
              "Qc (%.1f) IS the smooth call's answer (%.1f), and rough*K returns "
              "there (%.2f); K stays below 2"
              % (_k52, (_rsm.get("roughness") or {}).get("q_conductor_smooth", -1),
                 _t101.get("q_conductor", -1),
                 _rsm.get("q_conductor", 0.0) * _k52),
              abs((_rsm.get("roughness") or {}).get("q_conductor_smooth", 0.0)
                  - _t101.get("q_conductor", 1.0)) <= 0.05
              and 1.5 < _k52 < 2.0
              and abs(_rsm.get("q_conductor", 0.0) * _k52
                      / _t101.get("q_conductor", 1.0) - 1.0) <= 1e-4,
              str((_rsm.get("roughness") or {}).get("q_conductor_smooth")))

        # a filled cavity (er, mu_r) is where 0.0.51 refused: its field integral used
        # vacuum eps/mu while the closed form did not, so the two disagreed and the
        # tool withheld the answer. The agreement must now be exact, not merely small.
        _med = _wc({"f0_hz": 12e9, "a_mm": 30.0, "b_mm": 15.0,
                    "er": 2.2, "mu_r": 1.2}, 1612)
        check("a dielectric/magnetic fill (er 2.2, mu_r 1.2) answers instead of "
              "refusing: d = %.4f mm, Qc = %.1f, the two derivations agree to %.4f %%"
              % (_med.get("length_mm", -1), _med.get("q_conductor", -1),
                 _med.get("q_agreement_pct", -1)),
              _med.get("status", "ok") != "error"
              and abs(_med.get("length_mm", 0.0) - 7.9535) <= 1e-4
              and _med.get("q_agreement_pct") == 0.0,
              str(_med.get("message"))[:120])

        # --- 0.0.53, copper skins and a point mass on the plate solve. Five
        # readings, each of which can fail: (1) the transformed section, checked
        # through the per-layer identity d12/d11 == 1 - 2 d66/d11 that holds for
        # every isotropic layer separately and therefore for the sum; (2) the
        # exact sqrt(D11/mass) scaling plus the NEGATIVE control - mass alone
        # would predict 615 Hz, so an implementation that only grew the mass
        # cannot land on 800.849; (3) the single-mode anchor for a centre mass
        # (323.7 Hz) against the full rank-one solve (316.0 Hz); (4) a mass on a
        # node, which must leave every mode bit-identical because that is what
        # separates a point mass from a smear; (5) the default path - no copper,
        # no mass - must be the bare plate and must say so.
        def _vp(_args, _id):
            _env = json.loads(handle({"jsonrpc": "2.0", "id": _id,
                                      "method": "tools/call",
                                      "params": {"name": "vibration_plate_modes",
                                                 "arguments": _args}})
                               ["result"]["content"][0]["text"])
            _flat = dict(_env.get("value") or {})
            _flat.update({k: v for k, v in _env.items() if k != "value"})
            return _flat

        _pbase = {"a_mm": 100.0, "b_mm": 80.0, "thickness_mm": 1.6,
                  "material": "fr4", "mount": "custom",
                  "bc_x": ["ss", "ss"], "bc_y": ["ss", "ss"]}
        _pf = {}
        for _i, _cu in enumerate((None, 35.0, 70.0)):
            _a = dict(_pbase)
            if _cu is not None:
                _a["copper_um"] = _cu
            _pf[_i] = _vp(_a, 1613 + _i)
        _plain_f = ((_pf[0].get("modes") or [{}])[0]).get("freq_hz", 0.0)
        _c1 = _pf[1].get("composite") or {}
        _c2 = _pf[2].get("composite") or {}
        _nu_eff = (_c1.get("d12_n_m", 0.0) / _c1.get("d11_n_m", 1.0))
        check("copper skins are a transformed section: 1 oz gives D11 %.4f N m and "
              "D66 %.4f (2 oz: %.4f / %.4f), mass per area %.4f, and the per-layer "
              "identity d12/d11 = 1-2 d66/d11 holds to %.1e"
              % (_c1.get("d11_n_m", -1), _c1.get("d66_n_m", -1),
                 _c2.get("d11_n_m", -1), _c2.get("d66_n_m", -1),
                 _pf[1].get("mass_per_area_kg_m2", -1),
                 abs(_nu_eff - (1.0 - 2.0 * _c1.get("d66_n_m", 0.0)
                                / _c1.get("d11_n_m", 1.0)))),
              abs(_c1.get("d11_n_m", 0.0) - 14.2) < 1e-4
              and abs(_c1.get("d66_n_m", 0.0) - 5.48217) < 1e-4
              and abs(_c2.get("d11_n_m", 0.0) - 20.5284) < 1e-4
              and abs(_c2.get("d66_n_m", 0.0) - 7.57053) < 1e-4
              and abs(_pf[1].get("mass_per_area_kg_m2", 0.0) - 3.5872) < 1e-4
              and abs(_nu_eff - (1.0 - 2.0 * _c1.get("d66_n_m", 0.0)
                                 / _c1.get("d11_n_m", 1.0))) < 3e-5,
              str(_c1))

        # f ~ sqrt(D11 / mass per area) EXACTLY, because the two skin terms
        # collapse onto one effective nu (the identity checked above) and the
        # mode shape never moves. And the mass on its own points the other way:
        # 677.29/sqrt(3.5872/2.96) = 615.2 Hz, so landing on 800.849 is only
        # possible if the stiffness is really in there.
        _mass_only = _plain_f / _math.sqrt(
            _pf[1].get("mass_per_area_kg_m2", 1.0)
            / _pf[0].get("mass_per_area_kg_m2", 1.0))
        _base_ratio = (_pf[0].get("flexural_rigidity_n_m", 1.0)
                       / _pf[0].get("mass_per_area_kg_m2", 1.0))
        _scale_err = 0.0
        for _i in (1, 2):
            _ratio = (_pf[_i].get("flexural_rigidity_n_m", 1.0)
                      / _pf[_i].get("mass_per_area_kg_m2", 1.0))
            _pred = _plain_f * _math.sqrt(_ratio / _base_ratio)
            _got = ((_pf[_i].get("modes") or [{}])[0]).get("freq_hz", 0.0)
            _scale_err = max(_scale_err, abs(_got / _pred - 1.0))
        check("copper raises f1 by exactly sqrt(D11/mass): 1 oz %.3f Hz and 2 oz "
              "%.3f Hz against a bare plate at %.3f (scaling error %.1e), while "
              "the MASS ALONE would have predicted %.2f Hz downwards"
              % (((_pf[1].get("modes") or [{}])[0]).get("freq_hz", 0.0),
                 ((_pf[2].get("modes") or [{}])[0]).get("freq_hz", 0.0),
                 _plain_f, _scale_err, _mass_only),
              abs(((_pf[1].get("modes") or [{}])[0]).get("freq_hz", 0.0)
                  - 800.849) < 1e-3
              and abs(((_pf[2].get("modes") or [{}])[0]).get("freq_hz", 0.0)
                      - 888.369) < 1e-3
              and _scale_err < 1e-6
              and _mass_only < 0.95 * _plain_f
              and ((_pf[1].get("modes") or [{}])[0]).get("freq_hz", 0.0)
              > 1.2 * _mass_only,
              str(_mass_only))

        # A point mass at the centre of the (1,1) mode: the single-mode anchor
        # f0/sqrt(1 + 4 m/M_plate) is an OUTSIDE reference (plate theory, using
        # phi_max = 2 at the centre of a SSSS plate). The full rank-one solve
        # must land within a few percent of it and strictly below it, because
        # the higher modes the anchor ignores pull the answer down a little.
        _pm = _vp(dict(_pbase, added_mass_g=20.0), 1616)
        _pm_rep = _pm.get("added_mass") or {}
        _pm_f = ((_pm.get("modes") or [{}])[0]).get("freq_hz", 0.0)
        _anchor = _plain_f / _math.sqrt(
            1.0 + 4.0 * _pm_rep.get("mass_ratio", 0.0))
        check("a 20 g mass at the plate centre drops f1 from %.3f Hz to %.3f Hz, "
              "%.2f %% below the single-mode anchor %.3f Hz (mass ratio %.4f)"
              % (_plain_f, _pm_f, 100.0 * (_anchor - _pm_f) / _anchor, _anchor,
                 _pm_rep.get("mass_ratio", -1)),
              _pm_rep.get("mass_ratio") is not None
              and abs(_pm_rep.get("mass_ratio", 0.0) - 0.844595) < 1e-5
              and 0.0 < (_anchor - _pm_f) / _anchor < 0.05,
              str(_pm_f))

        # The negative control for 'point, not smear': a mass sitting on a nodal
        # line (x = 0) must leave EVERY mode bit-identical, because the basis
        # vanishes there and the rank-one update is the identity. A smear would
        # move all of them.
        _pn = _vp(dict(_pbase, added_mass_g=0.001, added_mass_at_mm=[0.0, 40.0]),
                  1617)
        _pn_rep = _pn.get("added_mass") or {}
        _pfull = _vp(dict(_pbase, n_modes=4), 1618)
        _node_ok = _pn_rep.get("whitening_c") == 0.0
        for _got, _ref in zip(_pn.get("modes") or [], _pfull.get("modes") or []):
            _node_ok = _node_ok and _got.get("freq_hz") == _ref.get("freq_hz")
        check("a mass on a nodal line is a true no-op (whitening c = %s): all %d "
              "modes come back bit-identical to the bare plate"
              % (_pn_rep.get("whitening_c"), len(_pfull.get("modes") or [])),
              _node_ok and len(_pfull.get("modes") or []) >= 4,
              str(_pn_rep.get("whitening_c")))

        # Default unchanged: copper_um = 0 is the same code path as omitting it,
        # and the bare call reports both new blocks as absent rather than as
        # an empty dict, so a reader can tell 'not asked' from 'asked, got zero'.
        _pz = _vp(dict(_pbase, copper_um=0.0), 1619)
        check("copper_um = 0 answers exactly as omitting it (%.3f Hz, composite %s, "
              "added_mass %s) - the default path is the bare plate"
              % (((_pz.get("modes") or [{}])[0]).get("freq_hz", 0.0),
                 _pz.get("composite"), _pz.get("added_mass")),
              _pz.get("composite") is None
              and _pz.get("added_mass") is None
              and ((_pz.get("modes") or [{}])[0]).get("freq_hz")
              == ((_pf[0].get("modes") or [{}])[0]).get("freq_hz")
              and abs(_plain_f - 677.29) < 1e-3,
              str((_pz.get("modes") or [{}])[0].get("freq_hz")))
    finally:
        _shutil.rmtree(tmp, ignore_errors=True)

    # The LCEDA Pro bridge must say "not running" out loud instead of hanging,
    # and it must never rewrite a vendor error code as our own. Both are checked
    # without a live editor: a pipe name that cannot exist, and the wire-level
    # reply checker.
    r = handle({"jsonrpc": "2.0", "id": 114, "method": "tools/call",
                "params": {"name": "lceda_pro_status", "arguments": {
                    "endpoint": "JLCEDAPro_selftest_no_such_pipe", "timeout_s": 1}}})
    _lw = json.loads(r["result"]["content"][0]["text"])
    check("a pipe that does not exist is APP_NOT_RUNNING, not a hang",
          _lw.get("status") == "error" and _lw.get("reason_code") == "APP_NOT_RUNNING",
          str(_lw.get("reason_code")))

    _live_mod = _tools._live
    _kept = None
    try:
        _live_mod.PipeTransport._check(
            {"ok": False, "error": {"code": "EXECUTION_ERROR", "message": "boom"}})
    except _live_mod.LiveError as _exc:
        _kept = (_exc.code, _exc.message)
    _ours = None
    try:
        _live_mod.PipeTransport._check({"ok": False, "error": {"code": "WHO_KNOWS"}})
    except _live_mod.LiveError as _exc:
        _ours = (_exc.code, _exc.extra.get("app_code"))
    check("vendor code kept, unknown code becomes APP_ERROR (not invented)",
          _kept == ("EXECUTION_ERROR", "boom") and _ours == ("APP_ERROR", "WHO_KNOWS"),
          str((_kept, _ours)))

    # A ball grid array has to be fanned out by geometry, and the one thing a
    # fan-out must never do is claim a ball came out when it did not: the 1.27 mm
    # part here fits a default net class, and the same part with a 0.4 mm track
    # does not - the second call exists to see the refusal.
    _vtmp = _tempfile.mkdtemp(prefix="pcbagent-fanout-")
    try:
        from . import edit as _edit
        from . import fanout as _fanout

        _vb = os.path.join(_vtmp, "fan.kicad_pcb")
        _edit.create_board(_vb, {"width_mm": 40, "height_mm": 40,
                                "copper_layers": 2, "engine": False})
        _vlib = os.path.join(_vtmp, "lib.pretty")
        os.makedirs(_vlib)
        _mod = ['(footprint "SYN:BGA25" (version 20221018) (generator pcbnew)',
                '  (layer "F.Cu") (attr smd)',
                '  (fp_text reference "REF**" (at 0 -10) (layer "F.SilkS")',
                '    (effects (font (size 0.7 0.7) (thickness 0.12))))',
                '  (fp_text value "BGA25" (at 0 10) (layer "F.Fab")',
                '    (effects (font (size 0.7 0.7) (thickness 0.12))))']
        _num = 1
        for _row in range(5):
            for _col in range(5):
                _mod.append('  (pad "%d" smd circle (at %.4f %.4f) (size 0.6 0.6)'
                            % (_num, _col * 1.27, _row * 1.27))
                _mod.append('    (layers "F.Cu" "F.Paste" "F.Mask") '
                            '(tstamp 5%07d-1111-1111-1111-111111111111))' % _num)
                _num += 1
        _mod.append(")\n")
        with open(os.path.join(_vlib, "BGA25.kicad_mod"), "w", encoding="utf-8",
                  newline="\n") as _fh:
            _fh.write("\n".join(_mod))
        _vplan = _edit.propose(_vb, [{
            "op": "add_footprint", "ref": "U1", "x": 20.0, "y": 20.0,
            "library": _vlib, "name": "BGA25", "create_nets": True,
            "pad_nets": dict((str(k), "N%d" % k) for k in range(1, 26))}])
        _edit.apply_plan(_vplan["plan_id"], confirm=True, options={"engine": False})
        _vboard, _verr = _tools._load_board({"path": _vb})
        if _verr:
            check("the fan-out self-test board loads", False, str(_verr))
        else:
            _fres = _fanout.fanout(_vboard)
            check("a 1.27 mm 5x5 ball array is fully escaped (16 direct + 9 dogbone)",
                  _fres["escaped"] == 25 and _fres["skipped"] == []
                  and _fres["styles"] == {"direct": 16, "dogbone": 9},
                  str((_fres["escaped"], _fres["styles"])))
            _fres2 = _fanout.fanout(_vboard, clearance_mm=1.0)
            check("a 1.0 mm clearance cannot fit a 1.27 mm array, and says so",
                  _fres2["escaped"] < 25 and bool(_fres2["advice"])
                  and bool(_fres2["skipped"]),
                  str((_fres2["escaped"], len(_fres2["skipped"]))))
    finally:
        _shutil.rmtree(_vtmp, ignore_errors=True)

    # A router that answers "no_path" leaves the caller with nothing to act on.
    # `blockers` says what is in the way, tags each object with what it would take
    # to move it, and - the part that has to be right - does not call a pad with
    # an open side "boxed in". And the run ledger must be able to record a run
    # without ever being able to change an answer.
    _btmp = _tempfile.mkdtemp(prefix="pcbagent-blockers-")
    try:
        from . import ledger as _ledger
        from .route import blockers as _blockers
        from .route import grid as _grid
        from .route import rules as _rules

        _bb = os.path.join(_btmp, "blk.kicad_pcb")
        _edit.create_board(_bb, {"width_mm": 40, "height_mm": 30,
                                 "copper_layers": 2, "engine": False})
        _blib = os.path.join(_btmp, "lib.pretty")
        os.makedirs(_blib)
        _bmod = ['(footprint "SYN:Wall" (version 20221018) (generator pcbnew)',
                 '  (layer "F.Cu") (attr smd)',
                 '  (fp_text reference "REF**" (at 0 -6) (layer "F.SilkS")',
                 '    (effects (font (size 0.7 0.7) (thickness 0.12))))',
                 '  (fp_text value "Wall" (at 0 6) (layer "F.Fab")',
                 '    (effects (font (size 0.7 0.7) (thickness 0.12))))',
                 '  (pad "1" smd rect (at 0 0) (size 0.6 0.6)',
                 '    (layers "F.Cu" "F.Paste" "F.Mask") '
                 '(tstamp 50000001-1111-1111-1111-111111111111))',
                 '  (pad "2" smd rect (at -1.0 0) (size 0.4 0.4)',
                 '    (layers "F.Cu" "F.Paste" "F.Mask") '
                 '(tstamp 50000002-1111-1111-1111-111111111111))',
                 ')\n']
        with open(os.path.join(_blib, "Wall.kicad_mod"), "w", encoding="utf-8",
                  newline="\n") as _fh:
            _fh.write("\n".join(_bmod))
        _bplan = _edit.propose(_bb, [{
            "op": "add_footprint", "ref": "U1", "x": 20.0, "y": 15.0,
            "library": _blib, "name": "Wall", "create_nets": True,
            "pad_nets": {"1": "SIG", "2": "GND"}}])
        _edit.apply_plan(_bplan["plan_id"], confirm=True, options={"engine": False})
        _bboard, _berr = _tools._load_board({"path": _bb})
        if _berr:
            check("the blockers self-test board loads", False, str(_berr))
        else:
            _bgrid = _grid.Grid(_bboard, ["F.Cu"], 0.05, _rules.resolve(_bboard))
            _bres = _blockers.blockers(_bgrid, "SIG", (20.0, 15.0), "F.Cu",
                                       radius_mm=2.0)
            check("blockers leaves a wall on one side open on the other seven",
                  "W" not in _bres["open_sectors"]
                  and len(_bres["open_sectors"]) == 7
                  and not _bres["corridors"][4]["open"],
                  str(_bres["open_sectors"]))
            check("a mutable pad is tagged movable and the answer says the tag is "
                  "not this tool's decision",
                  _bres["blockers"]
                  and _bres["blockers"][0]["mutability"] == "movable"
                  and "not a decision made here" in _bres["mutability_rule"])

        _bhome = os.environ.get("PCB_AGENT_HOME")
        os.environ["PCB_AGENT_HOME"] = os.path.join(_btmp, "home")
        try:
            _bdigest = _ledger.digest(_bb)
            _bbefore = len(_ledger.entries(kind="route"))
            _bwrote = _ledger.record("route", {"board": _bdigest,
                                               "verdict": "routed"})
            check("the run ledger records one line keyed by the board digest",
                  _bwrote and len(_ledger.entries(kind="route")) == _bbefore + 1
                  and _ledger.entries(kind="route")[0]["payload"]["board"] == _bdigest,
                  str(_bwrote))
            os.environ["PCB_AGENT_LEDGER"] = "0"
            check("the ledger's one switch off writes nothing at all",
                  _ledger.record("route", {"verdict": "routed"}) is False
                  and len(_ledger.entries(kind="route")) == _bbefore + 1)
        finally:
            os.environ.pop("PCB_AGENT_LEDGER", None)
            if _bhome is None:
                os.environ.pop("PCB_AGENT_HOME", None)
            else:
                os.environ["PCB_AGENT_HOME"] = _bhome
    finally:
        _shutil.rmtree(_btmp, ignore_errors=True)

    # The skill-derived calculators: each one is checked against a number that can
    # be re-derived by hand, and the rule engine is checked for the one property
    # that matters - a missing measurement must never come back as a pass.
    r = handle({"jsonrpc": "2.0", "id": 115, "method": "tools/call",
                "params": {"name": "magnetics_inductor", "arguments": {
                    "topology": "boost", "vin_v": 5, "vout_v": 12,
                    "f_sw_hz": 500000, "iout_a": 1.0, "efficiency": 0.9}}})
    _mg = json.loads(r["result"]["content"][0]["text"])
    _mgv = _mg.get("value") or {}
    # D = 1 - 0.9*5/12 = 0.625; Iin = 12/(0.9*5) = 2.6667 A;
    # L = 5*0.625/(0.3*2.6667*500e3) = 7.8125e-6 H
    check("boost duty and inductance match volt-seconds by hand",
          _mg.get("status") == "ok" and abs(_mgv.get("duty", 0) - 0.625) < 1e-9
          and abs(_mgv.get("inductor_uh", 0) - 7.8125) < 1e-3,
          str(_mgv.get("inductor_uh")))
    check("saturation is undetermined, not passed, without a part Isat",
          (_mgv.get("saturation") or {}).get("verdict") == "undetermined")

    r = handle({"jsonrpc": "2.0", "id": 116, "method": "tools/call",
                "params": {"name": "rf_chain", "arguments": {"stages": [
                    {"name": "LNA1", "gain_db": 15, "nf_db": 0.8},
                    {"name": "LNA2", "gain_db": 20, "nf_db": 1.2},
                    {"name": "LNA3", "gain_db": 10, "nf_db": 2.0}]}}})
    _ch = (json.loads(r["result"]["content"][0]["text"]).get("value") or {})
    # the worked example in rf-circuit-design 4.4 gives 0.83 dB
    check("Friis cascade reproduces the worked example (0.83 dB)",
          abs(_ch.get("nf_total_db", 0) - 0.8369) < 5e-3,
          str(_ch.get("nf_total_db")))
    r = handle({"jsonrpc": "2.0", "id": 117, "method": "tools/call",
                "params": {"name": "rf_chain",
                           "arguments": {"stages": [{"name": "x", "gain_db": 10}]}}})
    _ch1 = (json.loads(r["result"]["content"][0]["text"]).get("value") or {})
    check("cascade NF with a missing stage nf is None, not a partial number",
          _ch1.get("nf_total_db") is None
          and "nf_db" in str(_ch1.get("answers_only_when_every_stage_is_known")))

    r = handle({"jsonrpc": "2.0", "id": 118, "method": "tools/call",
                "params": {"name": "stackup_design", "arguments": {
                    "layers": 4, "material": "fr4", "total_mm": 1.0,
                    "freq_ghz": 2.4}}})
    _su = (json.loads(r["result"]["content"][0]["text"]).get("value") or {})
    _given = sum(row.get("dielectric_to_next_mm", 0.0) for row in _su.get("stack", []))
    check("stackup dielectric sums to the finished thickness",
          _su.get("layers") == 4 and abs(_given - 1.0) < 1e-6, str(_given))
    check("stackup solves a width on every signal layer that has a plane below",
          len([w for w in _su.get("widths", []) if w.get("width_mm")]) == 1)

    r = handle({"jsonrpc": "2.0", "id": 119, "method": "tools/call",
                "params": {"name": "layout_review",
                           "arguments": {"facts": {"min_via_hole_mm": 0.2}}}})
    _lr = (json.loads(r["result"]["content"][0]["text"]).get("value") or {})
    check("a rule with no measurement is undetermined, never pass",
          _lr.get("counts", {}).get("pass") == 0
          and _lr.get("counts", {}).get("undetermined", 0) > 0,
          str(_lr.get("counts")))
    check("a measured violation is a fail with its limit attached",
          [f for f in _lr.get("fails", [])
           if f.get("id") == "layout.min_via_hole" and f.get("limit") == 0.3])

    r = handle({"jsonrpc": "2.0", "id": 120, "method": "tools/call",
                "params": {"name": "schematic_review", "arguments": {"facts": {}}}})
    check("an empty fact set is refused (ARG_MISSING), not graded as clean",
          json.loads(r["result"]["content"][0]["text"]).get("reason_code")
          == "ARG_MISSING")

    # The full-wave seam: the one thing it must never do is answer with closed-form
    # numbers when the caller asked for a solver. `meep` has no install directory
    # in DETECTORS, so blanking PATH and pointing its env var at nothing makes the
    # "not installed" case reproducible on any machine - including one that has it.
    r = handle({"jsonrpc": "2.0", "id": 121, "method": "tools/call",
                "params": {"name": "fullwave_solver", "arguments": {"probe": False}}})
    _fw = (json.loads(r["result"]["content"][0]["text"]))
    _fwv = _fw.get("value") or {}
    check("the solver probe reports every solver and claims no run evidence",
          _fw.get("status") == "ok" and len(_fwv.get("solvers", [])) == 3
          and all(row.get("verified_here") is False
                  for row in _fwv.get("solvers", []))
          and _fwv.get("found") == [row["solver"] for row in _fwv.get("solvers", [])
                                    if row.get("found")],
          str(_fwv.get("found")))

    _fw_path = os.environ.get("PATH")
    _fw_env = os.environ.get("PCB_AGENT_MEEP")
    os.environ["PATH"] = ""
    os.environ["PCB_AGENT_MEEP"] = os.path.join(_tempfile.gettempdir(),
                                               "no_such_meep.exe")
    try:
        r = handle({"jsonrpc": "2.0", "id": 122, "method": "tools/call",
                    "params": {"name": "fullwave_solver",
                               "arguments": {"solver": "meep", "probe": False}}})
        _fwm = json.loads(r["result"]["content"][0]["text"])
        check("a named solver that is not installed is SOLVER_NOT_FOUND, never a "
              "closed-form substitute",
              _fwm.get("status") == "error"
              and _fwm.get("reason_code") == "SOLVER_NOT_FOUND"
              and "meep" in str(_fwm.get("message"))
              and "not installed" in str(_fwm.get("message")),
              str(_fwm.get("message")))
    finally:
        if _fw_path is None:
            os.environ.pop("PATH", None)
        else:
            os.environ["PATH"] = _fw_path
        if _fw_env is None:
            os.environ.pop("PCB_AGENT_MEEP", None)
        else:
            os.environ["PCB_AGENT_MEEP"] = _fw_env

    # Reliability: the two things that must not be fudged. The Profil-Engelmaier
    # identities have to close exactly, and a drop without a critical strain must
    # come back as a strain with judged=false, never as a verdict.
    r = handle({"jsonrpc": "2.0", "id": 123, "method": "tools/call",
                "params": {"name": "solder_joint_fatigue",
                           "arguments": {"joint_height_mm": 0.5, "body_a_mm": 15.0,
                                         "body_b_mm": 15.0,
                                         "alpha_component_ppm": 6.0,
                                         "alpha_board_ppm": 16.0,
                                         "delta_t_k": 100.0,
                                         "mean_joint_temp_c": 50.0,
                                         "dwell_min": 15.0}}})
    _sj = (json.loads(r["result"]["content"][0]["text"]).get("value") or {})
    _sc = _sj.get("self_check") or {}
    _row = (_sj.get("conditions") or [{}])[0]
    _c_lim = _sc.get("c_at_zero_temp_infinite_dwell")
    _slope = _sc.get("log_log_slope_minus_1_over_c")
    check("Engelmaier's identities close exactly (c limit, Nf at unit strain, "
          "log-log slope vs 1/c)",
          _c_lim is not None and abs(_c_lim) < 1e-9
          and abs((_sc.get("nf_at_unit_strain_ratio") or 0.0) - 0.5) < 1e-12
          and _slope is not None and abs(_slope) < 1e-9,
          str(_sc))
    # DNP is the diagonal corner of the 15x15 body: hypot(7.5, 7.5) = 10.6066 mm,
    # so the strain is 10.6066 * 10e-6 * 100 / 0.5, not 12.5 * ...
    check("solder strain is the mismatch displacement over the standoff",
          abs(_row.get("shear_strain_range") - 0.0212132) < 1e-6,
          str(_row.get("shear_strain_range")))

    r = handle({"jsonrpc": "2.0", "id": 124, "method": "tools/call",
                "params": {"name": "vibration_shock_spectrum",
                           "arguments": {"pulse": {"type": "step",
                                                   "amplitude_g": 30.0},
                                         "f_n_hz": [10.0, 100.0, 1000.0],
                                         "zeta": 1e-9}}})
    _ss = (json.loads(r["result"]["content"][0]["text"]).get("value") or {})
    _pts = _ss.get("points") or []
    check("a step at near-zero damping gives exactly twice the pulse peak, at every "
          "frequency",
          len(_pts) == 3 and all(abs(p["zpa_g"] / 30.0 - 2.0) < 1e-3 for p in _pts),
          str([p["zpa_g"] for p in _pts]))

    r = handle({"jsonrpc": "2.0", "id": 125, "method": "tools/call",
                "params": {"name": "drop_board_strain",
                           "arguments": {"a_mm": 100.0, "b_mm": 80.0,
                                         "thickness_mm": 1.6, "material": "fr4",
                                         "mount": "corners", "q_factor": 15.0,
                                         "pulse": {"type": "half_sine",
                                                   "amplitude_g": 1500.0,
                                                   "duration_ms": 0.5}}}})
    _db = (json.loads(r["result"]["content"][0]["text"]).get("value") or {})
    check("a drop with no critical strain is a strain, not a verdict",
          _db.get("judged") is False and _db.get("verdict") is None
          and (_db.get("surface_strain_ue") or 0.0) > 0.0
          and any("no critical_strain_ue" in w for w in (_db.get("warnings") or [])),
          str(_db.get("surface_strain_ue")))

    # Coplanar: the two exact invariants, plus the direction of the agreement with
    # the conformal map. The solver must read LOW of the map (every entry in the
    # validation table does), and the two ground wings must split the return
    # current bit for bit - that equality is what the centred control volume buys.
    r = handle({"jsonrpc": "2.0", "id": 126, "method": "tools/call",
                "params": {"name": "coplanar_field",
                           "arguments": {"width_mm": 0.3, "gap_mm": 0.2,
                                         "dielectric_mm": 0.2, "kind": "cpw",
                                         "levels": 1}}})
    _cf = json.loads(r["result"]["content"][0]["text"])
    check("coplanar cpw 0.3/0.2 on 0.2 mm er=4.3 solves to 89.7 ohm and reads low "
          "of the conformal map",
          _cf.get("status") == "ok" and abs(_cf.get("value", 0.0) - 89.67) < 0.9
          and (_cf.get("closed_form_minus_solver_pct") or 1.0) < 0.0,
          "%s vs %s" % (_cf.get("value"), _cf.get("closed_form_ohm")))

    r = handle({"jsonrpc": "2.0", "id": 127, "method": "tools/call",
                "params": {"name": "feedline_impedance",
                           "arguments": {"kind": "cpw", "width_mm": 0.3,
                                         "gap_mm": 0.2, "dielectric_mm": 0.2,
                                         "freq_ghz": 2.4, "tan_delta": 0.02}}})
    _cl = ((json.loads(r["result"]["content"][0]["text"]).get("value") or {})
           .get("loss") or {})
    _sp = _cl.get("shape_factor_split") or {}
    check("the coplanar return current splits evenly between the two wings and "
          "nothing at all onto a backside plane that is not there",
          _cl.get("conductor_model") == "field_surface_charge"
          and (_cl.get("conductor_db_per_100mm") or 0.0) > 0.0
          and _sp.get("return_left") == _sp.get("return_right")
          and _sp.get("return_back") == 0.0,
          str(_sp))

    # Coplanar PAIR: no closed form exists for this geometry, so the checks are
    # the invariants instead. The odd drive must put exactly -1 times the charge
    # on the second strip (a free second strip is what a broken assembly looks
    # like: both modes then come out identical, and the impedance still looks
    # plausible), and the two ground wings must still split bit for bit.
    r = handle({"jsonrpc": "2.0", "id": 128, "method": "tools/call",
                "params": {"name": "coplanar_pair_field",
                           "arguments": {"width_mm": 0.3, "spacing_mm": 0.2,
                                         "gap_mm": 0.2, "dielectric_mm": 0.2,
                                         "kind": "cpw", "levels": 1}}})
    _cp = json.loads(r["result"]["content"][0]["text"])
    check("an edge-coupled coplanar pair solves to 129.9 ohm differential and puts "
          "exactly the opposite charge on the second strip",
          _cp.get("status") == "ok" and abs(_cp.get("value", 0.0) - 129.86) < 1.5
          and _cp.get("charge_balance_odd") == -1.0
          and _cp.get("z_odd_ohm", 0.0) < _cp.get("z_even_ohm", 0.0),
          "%s / balance %s" % (_cp.get("value"), _cp.get("charge_balance_odd")))

    r = handle({"jsonrpc": "2.0", "id": 129, "method": "tools/call",
                "params": {"name": "coplanar_pair_field",
                           "arguments": {"width_mm": 0.3, "spacing_mm": 0.2,
                                         "gap_mm": 0.2, "dielectric_mm": 0.2,
                                         "kind": "cpw", "levels": 1,
                                         "frequency_ghz": 2.4, "tan_delta": 0.02}}})
    _cq = json.loads(r["result"]["content"][0]["text"])
    _sf = _cq.get("shape_factor_odd") or {}
    check("the differential conductor loss is solved, its two strips and two wings "
          "carry identical shares, and nothing lands on a backside plane",
          _cq.get("status") == "ok"
          and (_cq.get("conductor_db_per_100mm") or 0.0) > 0.0
          and (_cq.get("dielectric_db_per_100mm") or 0.0) > 0.0
          and _sf.get("strip_left_share") == _sf.get("strip_right_share")
          and _sf.get("ground_share_left") == _sf.get("ground_share_right")
          and _sf.get("ground_share_back") == 0.0,
          "%s dB/100mm, S=%s" % (_cq.get("conductor_db_per_100mm"),
                                 _sf.get("shape_factor_per_mm")))

    # Coplanar with a FINITE ground: the historical default is an effectively
    # infinite wing, so this is a different structure, not a mesh setting. The
    # numbers are pinned because nothing external can check them - what CAN be
    # checked is that a finite wing raises Z0 (the return current has further to
    # spread), that ground_mm = 0 is bit-for-bit the old behaviour, and that the
    # two tools disagree about how much it matters, which is the physics.
    r = handle({"jsonrpc": "2.0", "id": 130, "method": "tools/call",
                "params": {"name": "coplanar_field",
                           "arguments": {"width_mm": 0.3, "gap_mm": 0.2,
                                         "dielectric_mm": 0.2, "kind": "cpw",
                                         "levels": 1, "ground_mm": 0.1}}})
    _gf = json.loads(r["result"]["content"][0]["text"])
    _ginf = json.loads(handle({"jsonrpc": "2.0", "id": 1300, "method": "tools/call",
                               "params": {"name": "coplanar_field",
                                          "arguments": {"width_mm": 0.3,
                                                        "gap_mm": 0.2,
                                                        "dielectric_mm": 0.2,
                                                        "kind": "cpw",
                                                        "levels": 1}}}
                       )["result"]["content"][0]["text"])
    check("a finite coplanar wing of gap/2 raises Z0 by 13.7 %, and ground_mm = 0 "
          "is exactly the infinite-wing answer",
          _gf.get("status") == "ok" and abs(_gf.get("value", 0.0) - 101.97) < 1.0
          and _gf.get("ground_width_mm") == 0.1
          and bool(_gf.get("ground_note"))
          and abs(_ginf.get("value", 0.0) - 89.67) < 0.9
          and _ginf.get("ground_width_mm") is None,
          "%s with a wing, %s without" % (_gf.get("value"), _ginf.get("value")))

    r = handle({"jsonrpc": "2.0", "id": 131, "method": "tools/call",
                "params": {"name": "coplanar_pair_field",
                           "arguments": {"width_mm": 0.3, "spacing_mm": 0.2,
                                         "gap_mm": 0.2, "dielectric_mm": 0.2,
                                         "kind": "cpw", "levels": 1,
                                         "ground_mm": 0.1}}})
    _gp = json.loads(r["result"]["content"][0]["text"])
    _odd_d = 100.0 * (_gp.get("z_odd_ohm", 0.0) - 64.930) / 64.930
    _even_d = 100.0 * (_gp.get("z_even_ohm", 0.0) - 146.202) / 146.202
    check("the same narrow wing moves the pair's odd mode 1 % but its even mode "
          "22 %: a narrow ground is a common-mode problem, and the two modes "
          "disagreeing is what proves the wing is really finite",
          _gp.get("status") == "ok"
          and abs(_gp.get("z_odd_ohm", 0.0) - 65.616) < 0.3
          and abs(_gp.get("z_even_ohm", 0.0) - 178.222) < 1.0
          and 0.5 < _odd_d < 2.0 and 15.0 < _even_d < 30.0,
          "odd %+0.2f %%, even %+0.2f %%" % (_odd_d, _even_d))

    # The edge-coupled pair as a FEEDLINE kind: the point of this kind is that
    # feedline_impedance and coplanar_pair_field must agree bit for bit (the kind
    # adds no model, it routes to the same solve), and that the differential
    # meaning is stated rather than left to the caller to guess.
    r = handle({"jsonrpc": "2.0", "id": 132, "method": "tools/call",
                "params": {"name": "feedline_impedance",
                           "arguments": {"kind": "cpw_diff", "width_mm": 0.3,
                                         "spacing_mm": 0.2, "gap_mm": 0.2,
                                         "dielectric_mm": 0.2}}})
    _fd = json.loads(r["result"]["content"][0]["text"])
    _dv = _fd.get("value") or {}
    _dd = _dv.get("differential") or {}
    check("the pair feedline kind returns the same 129.86 ohm differential the pair "
          "solver does, and says out loud that the number is Zdiff",
          _fd.get("status") == "ok" and abs(_dv.get("z0_ohm", 0.0) - 129.86) < 0.2
          and abs(_dd.get("z_odd_ohm", 0.0) - 64.930) < 0.2
          and str(_dv.get("z0_meaning", "")).startswith("DIFFERENTIAL")
          and _dd.get("charge_balance_odd") == -1.0
          and _dd.get("z_odd_ohm", 0.0) < _dd.get("z_even_ohm", 0.0)
          and abs(_dd.get("z_common_ohm", 0.0)
                  - 0.5 * _dd.get("z_even_ohm", 0.0)) < 1e-3,
          "%s ohm, odd %s / even %s" % (_dv.get("z0_ohm"), _dd.get("z_odd_ohm"),
                                        _dd.get("z_even_ohm")))

    r = handle({"jsonrpc": "2.0", "id": 133, "method": "tools/call",
                "params": {"name": "feedline_impedance",
                           "arguments": {"kind": "cpw_diff", "width_mm": 0.3,
                                         "spacing_mm": 0.2, "gap_mm": 0.2,
                                         "dielectric_mm": 0.2, "freq_ghz": 2.4,
                                         "tan_delta": 0.02}}})
    _fl = json.loads(r["result"]["content"][0]["text"])
    _lo = (_fl.get("value") or {}).get("loss") or {}
    _sp2 = _lo.get("shape_factor_split") or {}
    check("the pair's conductor loss is normalised to the differential current: both "
          "strips and both wings carry equal shares, nothing on a backside plane, "
          "and the field figure stays below the sheet bound because a sheet bound "
          "gives the grounds nothing",
          _fl.get("status") == "ok"
          and abs(_lo.get("conductor_db_per_100mm", 0.0) - 0.4312) < 0.02
          and abs(_lo.get("dielectric_db_per_100mm", 0.0) - 0.5157) < 0.005
          and abs(_sp2.get("strips_each", 0.0)
                  - 0.5 * _sp2.get("strips", 0.0)) < 2e-5
          and _sp2.get("return_left") == _sp2.get("return_right")
          and _sp2.get("return_back") == 0.0
          and (_lo.get("field_over_sheet_ratio") or 1.0) < 1.0,
          "alpha_c %s, split %s" % (_lo.get("conductor_db_per_100mm"), _sp2))

    # A pair kind without a spacing is not a defaulted parameter, it is a missing
    # one - and a single-ended kind must not have become sensitive to it.
    r = handle({"jsonrpc": "2.0", "id": 134, "method": "tools/call",
                "params": {"name": "feedline_impedance",
                           "arguments": {"kind": "cpw_diff", "width_mm": 0.3,
                                         "gap_mm": 0.2, "dielectric_mm": 0.2}}})
    _nf = json.loads(r["result"]["content"][0]["text"])
    _ok1 = _nf.get("status") == "error" and _nf.get("reason_code") == "ARG_REQUIRED"
    _ok2 = False
    try:
        from .calc import feedline as _fe
        _e1 = _fe.feedline_impedance("cpw", 0.3, 0.2, gap_mm=0.2)
        _e2 = _fe.feedline_impedance("cpw", 0.3, 0.2, gap_mm=0.2, spacing_mm=0.6)
        _ok2 = (_e1.get("status") == "ok" and _e2.get("status") == "ok"
                and abs(_e1["value"]["z0_ohm"] - _e2["value"]["z0_ohm"]) < 1e-9)
    except Exception as exc:                                # noqa: BLE001
        _ok1 = False
        _lo_note = "feedline import failed: %s" % exc
    check("a pair kind without spacing_mm is refused as a missing argument, and a "
          "single-ended kind stays bit-identical when spacing_mm is passed anyway",
          _ok1 and _ok2, str(_nf.get("reason_code")))



    # .subckt / X: the builtin parser flattens hierarchy itself since 0.0.27. The
    # checks are (a) the answer is the hand-computed divider, (b) the internal node
    # keeps the name ngspice prints, and (c) NOT_COMPUTED no longer claims
    # subcircuits are missing - the last one is the honesty half.
    _sub = ("* div\n.subckt div a b\nR1 a m 1k\nR2 m b 1k\n.ends\n"
            "V1 1 0 10\nX1 1 0 div\n.op\n.end\n")
    r = handle({"jsonrpc": "2.0", "id": 135, "method": "tools/call",
                "params": {"name": "sim_spice", "arguments": {"netlist": _sub}}})
    _sb = json.loads(r["result"]["content"][0]["text"])
    _sn = (_sb.get("value") or {}).get("nodes_v") or {}
    _nc = " ".join(_sb.get("not_computed") or [])
    check("a .subckt divider is flattened by the builtin solver: the tap lands on "
          "5.000 V at the node name ngspice itself prints (x1.m), and the honesty "
          "list no longer claims hierarchy is missing",
          _sb.get("status") == "ok"
          and abs(_sn.get("x1.m", 0.0) - 5.0) < 1e-6
          and abs(_sn.get("1", 0.0) - 10.0) < 1e-9
          and "subcircuits (.subckt), hierarchical netlists" not in _nc
          and "parameters that need a function" in _nc,
          "x1.m %s" % _sn.get("x1.m"))

    # A parameter that is parsed but never reaches the element is the failure this
    # pair is built to catch: the override must MOVE the answer, and two instances
    # of one subcircuit must stay two separate networks.
    _sub2 = ("* div with params\n.subckt div a b params: r2=1k\nR1 a m 1k\n"
             "R2 m b {r2}\n.ends\nV1 1 0 9\nX1 1 0 div\nX2 1 0 div r2=3k\n"
             ".op\n.end\n")
    r = handle({"jsonrpc": "2.0", "id": 136, "method": "tools/call",
                "params": {"name": "sim_spice", "arguments": {"netlist": _sub2}}})
    _p = json.loads(r["result"]["content"][0]["text"])
    _pn = (_p.get("value") or {}).get("nodes_v") or {}
    _v1 = _pn.get("x1.m", 0.0)
    _v2 = _pn.get("x2.m", 0.0)
    check("a subcircuit parameter reaches the element (9/2 = 4.5 V default vs "
          "9*3/4 = 6.75 V overridden) and the two instances are separate networks",
          _p.get("status") == "ok"
          and abs(_v1 - 4.5) < 1e-6 and abs(_v2 - 6.75) < 1e-6,
          "default %s, override %s" % (_v1, _v2))

    # Refusals: a recursive definition and a circular .param are errors with the
    # reason named - not a deck that "runs" with the card quietly skipped.
    _cyc = ("* cycle\n.subckt a p q\nX1 p q b\n.ends\n.subckt b p q\nX1 p q a\n"
            ".ends\nV1 1 0 1\nX9 1 0 a\n.op\n.end\n")
    r = handle({"jsonrpc": "2.0", "id": 137, "method": "tools/call",
                "params": {"name": "sim_spice", "arguments": {"netlist": _cyc}}})
    _cy = json.loads(r["result"]["content"][0]["text"])
    r = handle({"jsonrpc": "2.0", "id": 138, "method": "tools/call",
                "params": {"name": "sim_spice",
                           "arguments": {"netlist": ".param a={b+1} b={a+1}\n"
                                                   "V1 1 0 1\nR1 1 0 {a}\n"
                                                   ".op\n.end\n"}}})
    _pp = json.loads(r["result"]["content"][0]["text"])
    check("a recursive .subckt and a circular .param are refused with the reason "
          "named (NETLIST_INVALID), never run with the card skipped",
          _cy.get("status") == "error" and _cy.get("reason_code") == "NETLIST_INVALID"
          and "instantiates itself" in (_cy.get("message") or "")
          and _pp.get("status") == "error"
          and _pp.get("reason_code") == "NETLIST_INVALID"
          and "circular" in (_pp.get("message") or ""),
          str(_pp.get("reason_code")))

    # A .param card is a number that must reach the element: 9*3/4 lands on the
    # tap, and the same deck with the value inlined must agree bit for bit.
    _pc = ("* param divider\n.param r1=1k r2={3*r1}\nV1 1 0 9\nR1 1 2 {r1}\n"
           "R2 2 0 {r2}\n.op\n.end\n")
    r = handle({"jsonrpc": "2.0", "id": 139, "method": "tools/call",
                "params": {"name": "sim_spice", "arguments": {"netlist": _pc}}})
    _pm = json.loads(r["result"]["content"][0]["text"])
    _pn2 = (_pm.get("value") or {}).get("nodes_v") or {}
    _inline = ("V1 1 0 9\nR1 1 2 1k\nR2 2 0 3k\n.op\n.end\n")
    r = handle({"jsonrpc": "2.0", "id": 140, "method": "tools/call",
                "params": {"name": "sim_spice", "arguments": {"netlist": _inline}}})
    _in = json.loads(r["result"]["content"][0]["text"])
    _in_n = (_in.get("value") or {}).get("nodes_v") or {}
    check("a top-level .param reaches the element (9*3/4 = 6.750 V on the tap) and "
          "the same deck with the numbers inlined agrees bit for bit",
          _pm.get("status") == "ok"
          and abs(_pn2.get("2", 0.0) - 6.75) < 1e-6
          and abs(_pn2.get("2", 0.0) - _in_n.get("2", -1.0)) < 1e-12,
          "param %s, inline %s" % (_pn2.get("2"), _in_n.get("2")))

    # B sources (0.0.29): the expression is compiled into a tree at parse time, so a
    # deck that reads a node nobody connects is refused with the line number instead
    # of turning a typo into a silent 0 V - and the card is no longer in the
    # unsupported list that the honesty half of this selftest checks.
    _bsrc = ("* bsrc\nV1 1 0 10\nB1 2 0 V=V(1)*0.5\nR1 2 0 1k\n.op\n.end\n")
    r = handle({"jsonrpc": "2.0", "id": 141, "method": "tools/call",
                "params": {"name": "sim_spice", "arguments": {"netlist": _bsrc}}})
    _bsrc_out = json.loads(r["result"]["content"][0]["text"])
    _bsrc_n = (_bsrc_out.get("value") or {}).get("nodes_v") or {}
    _bsrc_nc = " ".join(_bsrc_out.get("not_computed") or [])
    check("a behaviour source is solved by the builtin solver: V(2) = 0.5*V(1) lands "
          "on 5.000 V, and the honesty list no longer claims behaviour sources are "
          "delegation-only",
          _bsrc_out.get("status") == "ok"
          and abs(_bsrc_n.get("2", 0.0) - 5.0) < 1e-6
          and abs(_bsrc_n.get("1", 0.0) - 10.0) < 1e-9
          and "behavioural sources" not in _bsrc_nc
          and "Laplace blocks" in _bsrc_nc,
          "node2 %s" % _bsrc_n.get("2"))

    # The two failures that must not be confused: a deck-level refusal (the card
    # names something this solver will not do) and an expression with no value
    # (the netlist is fine, the arithmetic is not).
    r = handle({"jsonrpc": "2.0", "id": 142, "method": "tools/call",
                "params": {"name": "sim_spice",
                           "arguments": {"netlist": "* b\nB1 2 0 V=laplace(1/(1+s))\n"
                                                   "R1 2 0 1k\n.op\n.end\n"}}})
    _lap = json.loads(r["result"]["content"][0]["text"])
    r = handle({"jsonrpc": "2.0", "id": 143, "method": "tools/call",
                "params": {"name": "sim_spice",
                           "arguments": {"netlist": "* b\nB1 1 0 V=1/0\n"
                                                   "R1 1 0 1k\n.op\n.end\n"}}})
    _zdiv = json.loads(r["result"]["content"][0]["text"])
    check("laplace() is refused by name (NETLIST_INVALID) and an expression with no "
          "value is EXPRESSION_INVALID rather than NOT_CONVERGED - the caller must "
          "not be sent looking for a floating node",
          _lap.get("status") == "error"
          and _lap.get("reason_code") == "NETLIST_INVALID"
          and "laplace() is not implemented here" in (_lap.get("message") or "")
          and _zdiv.get("status") == "error"
          and _zdiv.get("reason_code") == "EXPRESSION_INVALID"
          and "division by zero" in (_zdiv.get("message") or ""),
          "%s / %s" % (_lap.get("reason_code"), _zdiv.get("reason_code")))

    # Per-row Newton residual: with a 4000 V node in the same circuit, a GLOBAL
    # scale let a 1 mA error on the 1k branch read as converged (node 1 came back
    # as -3998.999996 instead of -4000). The divider must still be right to 1e-6.
    r = handle({"jsonrpc": "2.0", "id": 144, "method": "tools/call",
                "params": {"name": "sim_spice",
                           "arguments": {"netlist": "V1 1 0 4000\nR1 1 2 1k\n"
                                                   "R2 2 0 1k\n.op\n.end\n"}}})
    _big = json.loads(r["result"]["content"][0]["text"])
    _big_n = (_big.get("value") or {}).get("nodes_v") or {}
    check("a 4000 V rail does not loosen the convergence test on a 1k branch: the "
          "divider reads 2000.000 V and the rail 4000.000 V to 10 mV on a 4 kV "
          "rail (a GLOBAL "
          "residual scale accepts a 1 mA error on the 1k branch as converged, which "
          "is a plausible-looking answer that is off by volts)",
          _big.get("status") == "ok"
          and abs(_big_n.get("2", 0.0) - 2000.0) < 1e-2
          and abs(_big_n.get("1", 0.0) - 4000.0) < 1e-9,
          "node1 %s node2 %s" % (_big_n.get("1"), _big_n.get("2")))

    # 0.0.34: the two current-controlled sources and mutual inductance. The tool
    # must hand back the textbook number, not merely "solved": behind a 1 V source
    # and 1 k the branch current is -1 mA, so F1 2 0 V1 2 drives -2 mA into node 2
    # (2 V across 1 k) and H1 3 0 V1 2 is -2 mV - a card read positionally (V1
    # taken as a node pair) would give 0 V on both and look solved.
    _fh = ("* fh\nV1 1 0 1\nR1 1 0 1k\nF1 2 0 V1 2\nR2 2 0 1k\n"
           "H1 3 0 V1 2\nR3 3 0 1k\n.op\n.end\n")
    r = handle({"jsonrpc": "2.0", "id": 153, "method": "tools/call",
                "params": {"name": "sim_spice", "arguments": {"netlist": _fh}}})
    _fh_out = json.loads(r["result"]["content"][0]["text"])
    _fh_n = (_fh_out.get("value") or {}).get("nodes_v") or {}
    _fh_els = {e.get("name"): e for e in ((_fh_out.get("value") or {}).get("elements") or [])}
    check("a CCCS and a CCVS are solved, not rejected: F1 reads -1 mA and drives "
          "2.000 V into 1 k, H1 makes -0.002 V (both are 0 V if the controlling NAME "
          "is misread as a node pair)",
          _fh_out.get("status") == "ok"
          and abs(_fh_n.get("2", 0.0) - 2.0) < 1e-6
          and abs(_fh_n.get("3", 0.0) + 0.002) < 1e-9
          and abs((_fh_els.get("F1") or {}).get("control_current_a", 0.0) + 1e-3) < 1e-9,
          "V2 %s V3 %s" % (_fh_n.get("2"), _fh_n.get("3")))

    # K is a statement about two inductors, so the honest pair of answers is: an
    # impossible one is refused by name, and a real one is a transformer. With
    # L2/L1 = 4 and k = 1 an open secondary must sit at exactly twice the primary
    # voltage; |k| > 1 would put M^2 above L1*L2 and is refused, not clamped.
    _kk = ("* k\nV1 1 0 AC 1\nR1 1 3 1\nL1 3 0 100u\nL2 2 0 400u\n"
           "K1 L1 L2 1\nR2 2 0 1meg\n.op\n.ac lin 2 1k 2k\n.end\n")
    r = handle({"jsonrpc": "2.0", "id": 154, "method": "tools/call",
                "params": {"name": "sim_spice", "arguments": {"netlist": _kk,
                                                              "analysis": "ac"}}})
    _kk_out = json.loads(r["result"]["content"][0]["text"])
    _kk_p = ((_kk_out.get("value") or {}).get("points") or [{}])[0].get("nodes_v") or {}
    _ratio = ((_kk_p.get("2") or {}).get("mag", 0.0)
              / max((_kk_p.get("3") or {}).get("mag", 1.0), 1e-30))
    r = handle({"jsonrpc": "2.0", "id": 155, "method": "tools/call",
                "params": {"name": "sim_spice",
                           "arguments": {"netlist": "* k\nV1 1 0 1\nL1 1 0 1m\n"
                                                   "L2 2 0 1m\nK1 L1 L2 1.5\n"
                                                   "R2 2 0 1k\n.op\n.end\n"}}})
    _bad = json.loads(r["result"]["content"][0]["text"])
    check("a K card is a transformer, not a discarded line: k=1 with L2/L1=4 puts the "
          "open secondary at 2.000x the primary, and |k|=1.5 is refused by name with "
          "the range in the message instead of being clamped to 1",
          _kk_out.get("status") == "ok" and abs(_ratio - 2.0) < 1e-6
          and _bad.get("status") == "error"
          and _bad.get("reason_code") == "NETLIST_INVALID"
          and "outside [-1, 1]" in (_bad.get("message") or ""),
          "ratio %s, bad %s" % (round(_ratio, 9), _bad.get("reason_code")))

    # 0.0.35: the two switch cards. A switch is a conductance with TWO values and a
    # state, so the two honest questions are "which state did it settle in" and
    # "did the control pay for it". A card stamped as a plain wire answers 5.000 V
    # to both; a card whose control pair is read as an element name reads 0 V
    # forever. Neither shows up as a status error.
    _sw = ("* s\nV1 1 0 5\nVc 3 0 %s\nS1 1 2 3 0 smod\nR1 2 0 1k\n"
           ".model smod SW(VT=1 VH=0 RON=10 ROFF=1meg)\n.op\n.end\n")
    _sw_on = json.loads(handle({"jsonrpc": "2.0", "id": 156,
                                "method": "tools/call",
                                "params": {"name": "sim_spice", "arguments": {
                                    "netlist": _sw % "5"}}})["result"]["content"][0]["text"])
    _sw_off = json.loads(handle({"jsonrpc": "2.0", "id": 1560,
                                 "method": "tools/call",
                                 "params": {"name": "sim_spice", "arguments": {
                                     "netlist": _sw % "0"}}})["result"]["content"][0]["text"])
    _sw_on_n = (_sw_on.get("value") or {}).get("nodes_v") or {}
    _sw_off_n = (_sw_off.get("value") or {}).get("nodes_v") or {}
    _sw_on_e = {e.get("name"): e for e in ((_sw_on.get("value") or {}).get("elements") or [])}
    _sw_off_e = {e.get("name"): e for e in ((_sw_off.get("value") or {}).get("elements") or [])}
    check("a voltage-controlled switch is two states, not a wire: 5 V of control "
          "closes it (4.950 V at the load, state=on, 10 ohm) and 0 V opens it "
          "(4.995 mV through 1 M, state=off) - while the control pair draws no "
          "current at all",
          _sw_on.get("status") == "ok" and _sw_off.get("status") == "ok"
          and abs(_sw_on_n.get("2", 0.0) - 4.9504950495) < 1e-6
          and abs(_sw_off_n.get("2", 0.0) - 0.004995004995) < 1e-9
          and (_sw_on_e.get("S1") or {}).get("state") == "on"
          and (_sw_off_e.get("S1") or {}).get("state") == "off"
          and abs((_sw_on_e.get("S1") or {}).get("resistance_ohm", 0.0) - 10.0) < 1e-9
          and abs((_sw_off_e.get("S1") or {}).get("resistance_ohm", 0.0) - 1e6) < 1e3
          and abs((_sw_on_e.get("Vc") or {}).get("current_a", 1.0)) < 1e-9,
          "on %s %s, off %s %s" % (_sw_on_n.get("2"),
                                   (_sw_on_e.get("S1") or {}).get("state"),
                                   _sw_off_n.get("2"),
                                   (_sw_off_e.get("S1") or {}).get("state")))

    # The negative control for hysteresis is the SAME ramp twice: it stops at
    # 1.2 V, which is past VT=1.0 but short of VT+VH=1.5, so the no-hysteresis arm
    # must close and the VH=0.5 arm must not. And a switch wired so that closing it
    # removes its own control has no consistent state, which is an error with a
    # cause, not "the state it happened to stop in".
    _ramp = ("* ramp\nV1 1 0 5\nVc 3 0 PULSE(0 1.2 1m 2m 0 50m 60m)\n"
             "S1 1 2 3 0 smod\nR1 2 0 1k\n"
             ".model smod SW(VT=1 %s RON=10 ROFF=1meg)\n.tran 50u 5m\n.end\n")
    _hy0 = json.loads(handle({"jsonrpc": "2.0", "id": 157,
                              "method": "tools/call",
                              "params": {"name": "sim_spice", "arguments": {
                                  "netlist": _ramp % "VH=0"}}})["result"]["content"][0]["text"])
    _hy5 = json.loads(handle({"jsonrpc": "2.0", "id": 1570,
                              "method": "tools/call",
                              "params": {"name": "sim_spice", "arguments": {
                                  "netlist": _ramp % "VH=0.5"}}})["result"]["content"][0]["text"])
    _hy0_v = _hy0.get("value") or {}
    _hy5_v = _hy5.get("value") or {}
    _hy0_pts = _hy0_v.get("points") or [{}]
    _hy5_pts = _hy5_v.get("points") or [{}]
    _bi = json.loads(handle({"jsonrpc": "2.0", "id": 1571,
                             "method": "tools/call",
                             "params": {"name": "sim_spice", "arguments": {
                                 "netlist": "* bistable\nV1 1 0 5\nR1 1 3 10k\n"
                                            "R2 3 0 1k\nS1 3 0 3 0 smod\n"
                                            ".model smod SW(VT=0.4 VH=0 RON=1 ROFF=1meg)"
                                            "\n.op\n.end\n"}}})["result"]["content"][0]["text"])
    check("hysteresis is a band, not decoration: the SAME 1.2 V ramp closes the "
          "VH=0 switch (flips>=1, 4.950 V) and leaves the VH=0.5 one untouched "
          "(0 flips), because closing needs VT+VH=1.5 V; and a switch that shorts "
          "its own control is NOT_CONVERGED with a bistable hint",
          _hy0.get("status") == "ok" and _hy5.get("status") == "ok"
          and _hy0_v.get("switch_flips", 0) >= 1
          and _hy5_v.get("switch_flips", 1) == 0
          and abs(_hy0_pts[-1]["nodes_v"]["2"] - 4.9504950495) < 1e-6
          and abs(_hy5_pts[-1]["nodes_v"]["2"] - 0.004995004995) < 1e-9
          and _bi.get("status") == "error"
          and _bi.get("reason_code") == "NOT_CONVERGED"
          and "bistable" in (_bi.get("hint") or ""),
          "flips %s/%s, last %s/%s, bistable %s" % (
              _hy0_v.get("switch_flips"), _hy5_v.get("switch_flips"),
              _hy0_pts[-1]["nodes_v"].get("2"), _hy5_pts[-1]["nodes_v"].get("2"),
              _bi.get("reason_code")))

    # ON/OFF is the state the deck STARTS in, not a latch - the reading a reader
    # would assume, and the one ngspice-47 contradicts. Three decks pin it: a seed
    # the control contradicts is overthrown (0 V + ON settles open, 5 V + OFF
    # settles closed), and a seed inside a hysteresis band IS the answer (1.2 V
    # with VT=1 VH=0.5: closed with ON, open without).
    def _seed_net(ctl, init, vh):
        return ("* seed\nV1 1 0 5\nVc 3 0 %s\nS1 1 2 3 0 smod%s\nR1 2 0 1k\n"
                ".model smod SW(VT=1 VH=%s RON=10 ROFF=1meg)\n.op\n.end\n"
                % (ctl, init, vh))
    _seeds = {}
    for _key, _args in (("on0", ("0", " ON", "0")),
                        ("off5", ("5", " OFF", "0")),
                        ("band_on", ("1.2", " ON", "0.5")),
                        ("band_none", ("1.2", "", "0.5"))):
        _r = json.loads(handle({"jsonrpc": "2.0", "id": 158, "method": "tools/call",
                                "params": {"name": "sim_spice", "arguments": {
                                    "netlist": _seed_net(*_args)}}})["result"]["content"][0]["text"])
        _seeds[_key] = (_r.get("value") or {}).get("nodes_v") or {}
    check("ON/OFF only SEEDS the state (it is not a latch): 0 V + ON settles open "
          "(4.995 mV) and 5 V + OFF settles closed (4.950 V), while inside a "
          "VT=1/VH=0.5 band the seed is the whole answer (1.2 V: closed with ON, "
          "open without) - three readings a 're-read the iterate' DC solve loses",
          abs(_seeds["on0"].get("2", 0.0) - 0.004995004995) < 1e-9
          and abs(_seeds["off5"].get("2", 0.0) - 4.9504950495) < 1e-6
          and abs(_seeds["band_on"].get("2", 0.0) - 4.9504950495) < 1e-6
          and abs(_seeds["band_none"].get("2", 0.0) - 0.004995004995) < 1e-9,
          "on@0 %s, off@5 %s, band %s/%s" % (
              _seeds["on0"].get("2"), _seeds["off5"].get("2"),
              _seeds["band_on"].get("2"), _seeds["band_none"].get("2")))

    # --- 0.0.54: the three decks that used to answer a different question ---
    # (a) a diode's KF= was ignored, so the noise report was 1/f-free while the
    #     card said otherwise. The check is against ngspice-47's own two readings
    #     on the same deck, and the negative control is the same deck with KF=0:
    #     if the term is real, 1 Hz has to fall by an order of magnitude.
    _fl = "* flicker\nV1 2 0 dc 0.6 ac 1\nR1 2 1 1k\nD1 1 0 DM\n" \
          ".model DM D(KF=%s AF=1)\n.noise v(1) V1 dec 1 1 10\n.end\n"
    def _noise_of(kf):
        return json.loads(handle({"jsonrpc": "2.0", "id": 159, "method": "tools/call",
                                  "params": {"name": "sim_spice", "arguments": {
                                      "netlist": _fl % kf}}})["result"]["content"][0]["text"])
    _n_kf = _noise_of("1e-16")
    _n_no = _noise_of("0")
    _n_pts = [p["onoise_v_per_sqrt_hz"] for p in (_n_kf.get("value") or {}).get("points") or []]
    _n_no_pts = [p["onoise_v_per_sqrt_hz"] for p in (_n_no.get("value") or {}).get("points") or []]
    check("a diode's KF= is a density, not decoration: 1 Hz and 10 Hz reproduce "
          "ngspice-47's own 2.534181e-08 / 8.302515e-09 within 1 %%, and the same "
          "deck with KF=0 drops 1 Hz by more than 3x (shot+thermal only)",
          _n_kf.get("status") == "ok" and _n_no.get("status") == "ok"
          and len(_n_pts) == 2 and len(_n_no_pts) == 2
          and abs(_n_pts[0] / 2.534181e-08 - 1.0) < 0.01
          and abs(_n_pts[1] / 8.302515e-09 - 1.0) < 0.01
          and _n_pts[0] / _n_no_pts[0] > 3.0
          and abs(_n_no_pts[0] / _n_no_pts[1] - 1.0) < 1e-9,
          "%s / %s vs %s / %s" % (_n_pts[:1], _n_pts[1:], _n_no_pts[:1],
                                  _n_no_pts[1:]))

    # (b) KF on a JFET/BJT/MOSFET has no reference model to copy (measured: the
    #     bundled ngspice-47 moved by 0.0 % for KF 1e-15..1e-6), so the .noise
    #     analysis REFUSES the deck by name instead of reporting thermal+shot as
    #     if it were the answer. The negative control is the same deck without KF.
    _jf_kf = ("* jf\nVd 1 0 5\nVg 2 0 0\nJ1 1 2 0 jm%s\n"
              ".model jm NJF(VTO=-1 BETA=1m%s)\n.noise V(1) Vd lin 3 100 300\n.end\n")
    def _jf_noise(extra):
        return json.loads(handle({"jsonrpc": "2.0", "id": 1590, "method": "tools/call",
                                  "params": {"name": "sim_spice", "arguments": {
                                      "netlist": _jf_kf % ("", extra)}}})["result"]["content"][0]["text"])
    _jk = _jf_noise(" KF=1e-16 AF=1")
    _jn = _jf_noise("")
    check("a KF= this subset cannot model is REFUSED at .noise, not answered "
          "without it: NOT_MODELED names J1 and carries the measured 0.0 % fact, "
          "while the same deck without KF= answers",
          _jk.get("status") == "error" and _jk.get("reason_code") == "NOT_MODELED"
          and "J1" in (_jk.get("message") or "")
          and "0.0 %" in (_jk.get("hint") or "")
          and _jn.get("status") == "ok",
          "%s / %s" % (_jk.get("reason_code"), _jn.get("status")))

    # (c) `.temp 25 100` used to be read as "take 25, drop 100". It is a sweep: one
    #     independent solve per temperature, and each case must BE the deck run at
    #     that one temperature - the negative control that a copied result fails.
    _tdeck = ("* t\nV1 1 0 5\nR1 1 2 1k\nD1 2 0 DM\n.model DM D(IS=1e-14)\n"
              ".temp %s\n.op\n.end\n")
    def _run(*args):
        return json.loads(handle({"jsonrpc": "2.0", "id": 1591, "method": "tools/call",
                                  "params": {"name": "sim_spice",
                                             "arguments": dict(args[0])}})["result"]["content"][0]["text"])
    _sweep = _run({"netlist": _tdeck % "25 100"})
    _sv = _sweep.get("value") or {}
    _cases = _sv.get("cases") or []
    _solo = [_run({"netlist": _tdeck % ("%g" % t)}) for t in (25.0, 100.0)]
    _solo_v = [(x.get("value") or {}).get("nodes_v") or {} for x in _solo]
    check("`.temp 25 100` is a SWEEP, not a first-value: two cases, each with its "
          "own temperature, and each one bit-identical to the deck run at that one "
          "temperature (25 C and 100 C differ, so a copied case fails)",
          _sweep.get("status") == "ok" and _sv.get("analysis") == "temperature_sweep"
          and len(_cases) == 2
          and [c.get("temp_c") for c in _cases] == [25.0, 100.0]
          and (_cases[0].get("result") or {}).get("value", {}).get("nodes_v") == _solo_v[0]
          and (_cases[1].get("result") or {}).get("value", {}).get("nodes_v") == _solo_v[1]
          and _solo_v[0].get("2") != _solo_v[1].get("2"),
          "%s cases, %s vs %s" % (len(_cases),
                                  (_cases[0].get("result") or {}).get("value", {}).get("nodes_v", {}).get("2"),
                                  (_cases[1].get("result") or {}).get("value", {}).get("nodes_v", {}).get("2")))

    # (d) `mc(1)` / `agauss(...)` used to parse as the nominal number and come back
    #     as a confident deterministic answer. Refused by name, and the negative
    #     control is the same deck with a plain value.
    _mc = ("V1 1 0 5\nR1 1 0 %s\n.op\n.end\n")
    _mc_bad = json.loads(handle({"jsonrpc": "2.0", "id": 1592, "method": "tools/call",
                                 "params": {"name": "sim_spice", "arguments": {
                                     "netlist": _mc % "1k mc(1)"}}})["result"]["content"][0]["text"])
    _mc_ok = json.loads(handle({"jsonrpc": "2.0", "id": 1593, "method": "tools/call",
                                "params": {"name": "sim_spice", "arguments": {
                                    "netlist": _mc % "1k"}}})["result"]["content"][0]["text"])
    check("a Monte-Carlo value is REFUSED by name (it used to read as its nominal "
          "number): 'mc(1)' is NETLIST_INVALID with the reason spelled out, and "
          "'1k' in the same deck answers",
          _mc_bad.get("status") == "error"
          and _mc_bad.get("reason_code") == "NETLIST_INVALID"
          and "Monte-Carlo" in (_mc_bad.get("message") or "")
          and _mc_ok.get("status") == "ok",
          "%s / %s" % (_mc_bad.get("reason_code"), _mc_ok.get("status")))

    # (e) an argument no handler reads used to produce a complete answer computed
    #     at the default. It is named now, and a correct call gains nothing.
    _unk = json.loads(handle({"jsonrpc": "2.0", "id": 1594, "method": "tools/call",
                              "params": {"name": "sim_spice", "arguments": {
                                  "netlist": _mc % "1k", "temperature_c": 50}}})["result"]["content"][0]["text"])
    _unk_ok = json.loads(handle({"jsonrpc": "2.0", "id": 1595, "method": "tools/call",
                                 "params": {"name": "sim_spice", "arguments": {
                                     "netlist": _mc % "1k"}}})["result"]["content"][0]["text"])
    check("an argument the handler never reads is NAMED instead of silently "
          "ignored ('temperature_c' vs 'temp_c'), and a correct call adds no such "
          "warning",
          _unk.get("status") == "ok"
          and any("temperature_c" in w and "IGNORED" in w
                  for w in (_unk.get("warnings") or []))
          and not any("IGNORED" in w for w in (_unk_ok.get("warnings") or [])),
          str((_unk.get("warnings") or [])[:1]))

    # --- 0.0.55: a core table that is data, and every missing number named ---
    # (a) the turns satisfy BOTH relations, recomputed here from the table's rows,
    #     and the peak is judged on Amin where the vendor publishes one;
    # (b) a material's published Bsat is only used at the temperature it was
    #     published at - 3C90 at 100 C must come back UNJUDGED, not interpolated;
    # (c) a measured loss point is a point: the same part at another frequency is
    #     "unknown", not the same watts;
    # (d) 1000x the inductance is 1000x the flux turns - the guard against an H/mH
    #     slip being invisible.
    def _sel(**kw):
        rr = handle({"jsonrpc": "2.0", "id": 1600, "method": "tools/call",
                     "params": {"name": "magnetics_select_core", "arguments": kw}})
        return json.loads(rr["result"]["content"][0]["text"])

    _sc = _sel(inductance_h=100e-6, i_peak_a=3.0, b_max_t=0.3, limit=3)
    _sc_rows = (_sc.get("value") or {}).get("candidates") or []
    _sc_ok = bool(_sc_rows)
    for _row in _sc_rows:
        _area = (_row.get("flux_area_mm2") or _row["ae_mm2"]) * 1e-6
        if abs(100e-6 * 3.0 / (_row["turns"] * _area) - _row["b_peak_t"]) > 1e-6:
            _sc_ok = False
        if _row["b_peak_t"] > 0.3 * (1 + 1e-12):
            _sc_ok = False
    _sc_sorted = [r["ve_mm3"] for r in _sc_rows] == sorted(r["ve_mm3"] for r in _sc_rows)
    check("select_core returns cores whose reported peak flux IS L*Ipk/(N*A) "
          "recomputed from the vendor rows, never above the limit, smallest "
          "effective volume first",
          _sc.get("status") == "ok" and _sc_ok and _sc_sorted,
          "%d rows, sorted %s" % (len(_sc_rows), _sc_sorted))

    _cold = _sel(inductance_h=100e-6, i_peak_a=3.0, material="3C90",
                 temperature_c=25)
    _hot = _sel(inductance_h=100e-6, i_peak_a=3.0, material="3C90",
                temperature_c=100)
    check("a material's Bsat is used only at the temperature it was published at: "
          "3C90 at 25 C is judged, at 100 C every part comes back "
          "saturation_unknown with the reason spelled out - no interpolation",
          _cold.get("status") == "ok"
          and (_cold.get("value") or {}).get("feasible_count", 0) > 0
          and _hot.get("status") == "ok"
          and (_hot.get("value") or {}).get("feasible_count") == 0
          and (_hot.get("unjudged") or [{}])[0].get("reason") == "saturation_unknown",
          "cold %s / hot %s: %s" % (
              (_cold.get("value") or {}).get("feasible_count"),
              (_hot.get("value") or {}).get("feasible_count"),
              ((_hot.get("unjudged") or [{}])[0].get("because") or "")[:48]))

    _at_ref = _sel(inductance_h=200e-6, i_peak_a=2.0, material="3C90",
                   temperature_c=25, freq_hz=25e3, b_ac_t=0.2, shape="U 20/16/7")
    _off_ref = _sel(inductance_h=200e-6, i_peak_a=2.0, material="3C90",
                    temperature_c=25, freq_hz=100e3, b_ac_t=0.2, shape="U 20/16/7")
    _ref_row = ((_at_ref.get("value") or {}).get("candidates") or [{}])[0]
    _off_row = ((_off_ref.get("value") or {}).get("candidates") or [{}])[0]
    check("a vendor's measured loss point is reproduced at its own condition "
          "(1.9 W at 200 mT / 25 kHz) and the SAME part at 100 kHz is 'unknown, "
          "give me loss_exponents' - the condition is data, not decoration",
          _ref_row.get("core_loss_w") == 1.9
          and _off_row.get("core_loss_w") is None
          and "loss_exponents" in (_off_row.get("core_loss_basis") or ""),
          "%s W at 25 kHz / %s at 100 kHz" % (_ref_row.get("core_loss_w"),
                                              _off_row.get("core_loss_w")))

    _small = _sel(inductance_h=100e-6, i_peak_a=3.0, b_max_t=0.3,
                  material="N87", shape="PQ 20/16")
    _big = _sel(inductance_h=100e-3, i_peak_a=3.0, b_max_t=0.3,
                material="N87", shape="PQ 20/16")
    _n_small = (((_small.get("value") or {}).get("candidates") or [{}])[0]
                .get("turns")) or 0
    _n_big = (((_big.get("value") or {}).get("candidates") or [{}])[0]
              .get("turns")) or 0
    _browse = json.loads(handle({
        "jsonrpc": "2.0", "id": 1601, "method": "tools/call",
        "params": {"name": "magnetics_data",
                   "arguments": {"kind": "cores", "name": "B66325G0000X187"}}}
    )["result"]["content"][0]["text"])
    _browse_row = ((_browse.get("value") or {}).get("rows") or [{}])[0]
    _nope = _sel(inductance_h=100e-6, i_peak_a=1.0, material="unicorn")
    check("1000x the inductance is ~1000x the flux turns (an H-vs-mH slip cannot "
          "hide), the browse tool returns the same vendor row select_core used, "
          "and an unknown material is NOT_FOUND with the list of real ones",
          _n_small > 0 and 950 < _n_big / _n_small < 1050
          and _browse_row.get("ae_mm2") == 178.0
          and _browse_row.get("aw_mm2") == 178.0
          and _nope.get("reason_code") == "NOT_FOUND"
          and "materials_in_table" in _nope,
          "%s -> %s turns, Ae %s, N87? %s" % (_n_small, _n_big,
                                              _browse_row.get("ae_mm2"),
                                              "N87" in str(_nope.get(
                                                  "materials_in_table"))))

    # --- repeating pulse trains: three readings, no field solver ---
    # (a) with the cooling deleted the model must BE Onderdonk, not nearly it. The
    #     two routes share no constant: ours is rho20/rho_m/c_p, its is the 33.
    # (b) the train's DC equivalent is I*sqrt(D) by an energy balance, an identity
    #     that fails the moment the mean temperature or the resistance law moves.
    # (c) above the runaway threshold there is no fixed point; below it there is -
    #     and "no fixed point" must not be reported as "a very hot fixed point".
    def _pulse(**kw):
        rr = handle({"jsonrpc": "2.0", "id": 159, "method": "tools/call",
                     "params": {"name": "repetitive_pulse_rating", "arguments": kw}})
        return json.loads(rr["result"]["content"][0]["text"])

    _p_adi = _pulse(width_mm=1.0, t_on_s=1e-3, duty_cycle=0.1, h_w_m2k=0.0,
                    current_a=5.0)
    _p_dc = _pulse(width_mm=1.0, t_on_s=1e-3, duty_cycle=0.25, h_w_m2k=10.0,
                   current_a=5.0)
    _p_gate = _p_adi.get("adiabatic_limit_reproduced_pct")
    # the total on-time the train takes to melt must equal the single-pulse fusing
    # time for the same geometry at the same current
    _p_total = (_p_adi.get("pulses_to_melt") or 0) * 1e-3
    _p_single = json.loads(handle({
        "jsonrpc": "2.0", "id": 1590, "method": "tools/call",
        "params": {"name": "fusing_current",
                   "arguments": {"width_mm": 1.0, "t_s": _p_total}}})["result"][
        "content"][0]["text"])
    check("a pulse train with no cooling melts in the same total on-time the "
          "single-pulse fusing formula gives (4.01 s at 5 A), and the model's own "
          "adiabatic gate reports it (<1 %)",
          _p_adi.get("status") == "ok"
          and _p_gate is not None and abs(_p_gate) < 1.0
          and abs(_p_total - 4.01318) < 5e-3
          and abs((_p_single.get("value") or 0) - 5.0) < 0.15,
          "gate %s %%, total %s s, single %s A" % (_p_gate, _p_total,
                                                   _p_single.get("value")))
    check("the pulse train's DC equivalent is exactly I*sqrt(D) (5 A at 25 % = "
          "2.5 A) - an energy identity, not a fitted correction",
          _p_dc.get("status") == "ok"
          and abs((_p_dc.get("dc_equivalent_current_a") or 0) - 2.5) < 1e-3,
          str(_p_dc.get("dc_equivalent_current_a")))

    # h = 10 W/(m^2K) on 1 oz / 1 mm puts the threshold at 10.313 A
    _p_below = _pulse(width_mm=1.0, t_on_s=1e-3, duty_cycle=0.1, h_w_m2k=10.0,
                      current_a=10.30)
    _p_above = _pulse(width_mm=1.0, t_on_s=1e-3, duty_cycle=0.1, h_w_m2k=10.0,
                      current_a=10.32)
    check("the runaway boundary is an existence question: 10.30 A has a fixed "
          "point (a very hot one), 10.32 A has none, and the threshold reported "
          "for 0.1 duty is 10.313 A",
          _p_below.get("status") == "ok" and _p_above.get("status") == "ok"
          and _p_below.get("runs_away") is False
          and "steady_peak_c" in _p_below
          and _p_above.get("runs_away") is True
          and "steady_peak_c" not in _p_above
          and abs((_p_below.get("max_repetitive_current_a") or 0) - 10.313011) < 1e-4,
          "below %s K, above %s, crit %s A" % (
              _p_below.get("steady_peak_c"), _p_above.get("runs_away"),
              _p_below.get("max_repetitive_current_a")))

    _ABSENT_SELFTEST = object()
    # --- 0.0.56: three quantities that used to be "not checked by design" ---
    # (a) the narrowest SILK stroke is a file fact: the board-level gr_line says
    #     0.12 and a text at (size 1 1) says 1.0 high - reading the FIRST number
    #     of (size w h) would report the cell width instead of the glyph height;
    # (b) the mask WEB between two openings is (copper gap - margin - margin):
    #     1.4 mm pitch, 1.0 mm pads, 0.05 each side = 0.300;
    # (c) copper to the FINISHED hole wall is (distance - drill/2 - plating), so
    #     the SAME board passes at 0 um plating and fails at 100 um - and with no
    #     plating_um at all it must refuse, because measuring to the drill wall
    #     answers a different question.
    # `tmp` is removed by the time this block runs, so this fixture gets its own
    # directory (removed once the checks below have read it).
    _sm_tmp = _tempfile.mkdtemp(prefix="pcbagent-silkmask-")
    _silkmask_board = os.path.join(_sm_tmp, "silkmask.kicad_pcb")
    with open(_silkmask_board, "wb") as fh:
        fh.write(b'(kicad_pcb (version 20221018) (generator pcbnew)\n'
                 b'  (general (thickness 1.6))\n'
                 b'  (layers (0 "F.Cu" signal) (31 "B.Cu" signal)'
                 b' (44 "Edge.Cuts" user))\n'
                 b'  (setup (pad_to_mask_clearance 0.05))\n'
                 b'  (net 0 "") (net 1 "VCC") (net 2 "SIG")\n'
                 b'  (gr_rect (start 0 0) (end 50 30) (layer "Edge.Cuts")'
                 b' (width 0.1))\n'
                 b'  (gr_line (start 5 28) (end 15 28)'
                 b' (stroke (width 0.12) (type solid)) (layer "F.SilkS"))\n'
                 b'  (footprint "T:H" (layer "F.Cu") (at 20 10)\n'
                 b'    (fp_text reference "J1" (at 0 -2) (layer "F.SilkS")'
                 b' (effects (font (size 1 1) (thickness 0.15))))\n'
                 b'    (pad "1" thru_hole circle (at 0 0) (size 1.2 1.2)'
                 b' (drill 0.6) (layers "*.Cu" "*.Mask") (net 1 "VCC")))\n'
                 b'  (segment (start 21.5 8) (end 21.5 12) (width 0.2)'
                 b' (layer "F.Cu") (net 2))\n'
                 b'  (footprint "T:P" (layer "F.Cu") (at 35 20)\n'
                 b'    (fp_text reference "U2" (at 0 -2) (layer "F.SilkS")'
                 b' (effects (font (size 1 1) (thickness 0.15))))\n'
                 b'    (pad "1" smd rect (at -0.7 0) (size 1 1)'
                 b' (layers "F.Cu" "F.Mask") (net 1 "VCC"))\n'
                 b'    (pad "2" smd rect (at 0.7 0) (size 1 1)'
                 b' (layers "F.Cu" "F.Mask") (net 1 "VCC")))\n)\n')

    def _sm(rules, plating=_ABSENT_SELFTEST):
        cap = {"fab": "selftest", "source": "selftest fixture", "rules": dict(rules)}
        if plating is not _ABSENT_SELFTEST:
            cap["plating_um"] = plating
        rr = handle({"jsonrpc": "2.0", "id": 1700, "method": "tools/call",
                     "params": {"name": "dfm_check", "arguments": {
                         "path": _silkmask_board, "capability": cap}}})
        return json.loads(rr["result"]["content"][0]["text"])

    _sm_out = _sm({"min_silk_line_width_mm": 0.10,
                   "min_silk_text_height_mm": 0.8,
                   "min_mask_web_mm": 0.25})
    _sm_facts = (_sm_out.get("value") or {}).get("facts") or {}
    check("0.0.56 measures silk and the mask web: the 0.12 mm board-level silk "
          "line is the narrowest stroke, the 1.0 mm glyph height is read from the "
          "SECOND number of (size w h), and 1.4 mm pitch - 1.0 mm pads - 2 x 0.05 "
          "leaves a 0.300 mm mask web that passes a 0.25 limit",
          _sm_out.get("status") == "ok"
          and (_sm_out["value"].get("overall")) == "pass"
          and abs((_sm_facts.get("min_silk_line_width") or {})
                  .get("measured", -1) - 0.12) < 1e-9
          and abs((_sm_facts.get("min_silk_text_height") or {})
                  .get("measured", -1) - 1.0) < 1e-9
          and abs((_sm_facts.get("min_mask_web") or {})
                  .get("measured", -1) - 0.3) < 1e-9,
          "overall %s, silk %s, height %s, web %s" % (
              (_sm_out.get("value") or {}).get("overall"),
              (_sm_facts.get("min_silk_line_width") or {}).get("measured"),
              (_sm_facts.get("min_silk_text_height") or {}).get("measured"),
              (_sm_facts.get("min_mask_web") or {}).get("measured")))

    # 1.5 mm to a 0.2 mm track's centreline, less half the track = 1.4;
    # less the 0.3 mm drill radius = 1.1. The plating then decides.
    _hole_rules = {"min_copper_to_hole_mm": 1.05}
    _hole_0 = _sm(_hole_rules, plating=0)
    _hole_100 = _sm(_hole_rules, plating=100)
    _hole_none = _sm(_hole_rules)
    check("copper to the FINISHED hole wall subtracts the plating the document "
          "states: 1.1 mm at 0 um passes a 1.05 limit and 1.0 mm at 100 um fails "
          "it - same board, same limit, the plating decides",
          abs(((_hole_0.get("value") or {}).get("facts") or {})
              .get("min_copper_to_hole", {}).get("measured", -1) - 1.1) < 1e-9
          and abs(((_hole_100.get("value") or {}).get("facts") or {})
                  .get("min_copper_to_hole", {}).get("measured", -1) - 1.0) < 1e-9
          and [c["verdict"] for c in (_hole_0.get("value") or {})["checks"]
               if c["rule"] == "min_copper_to_hole_mm"] == ["pass"]
          and [c["verdict"] for c in (_hole_100.get("value") or {})["checks"]
               if c["rule"] == "min_copper_to_hole_mm"] == ["fail"],
          "0 um -> %s, 100 um -> %s" % (
              ((_hole_0.get("value") or {}).get("facts") or {})
              .get("min_copper_to_hole", {}).get("measured"),
              ((_hole_100.get("value") or {}).get("facts") or {})
              .get("min_copper_to_hole", {}).get("measured")))

    _hole_nc = [c for c in (_hole_none.get("value") or {}).get("checks") or []
                if c["rule"] == "min_copper_to_hole_mm"]
    check("without plating_um the hole-wall rule is NOT_CHECKED with the reason, "
          "never passed - measuring to the drill wall is a different quantity",
          _hole_none.get("status") == "ok" and len(_hole_nc) == 1
          and _hole_nc[0]["verdict"] == "not_checked"
          and "plating_um" in (_hole_nc[0].get("note") or ""),
          str(_hole_nc[0].get("verdict") if _hole_nc else "missing"))
    _shutil.rmtree(_sm_tmp, ignore_errors=True)

    # --- fusing along a taper (0.0.59). A UNIFORM trace with insulated ends has
    # identical neighbours, so every axial conduction term cancels in pairs and the
    # 1-D model must reproduce the lumped adiabatic answer - i.e. Onderdonk's, to
    # the 0.33 % residue between Onderdonk's 33 and today's rho_m*cp/rho20.
    _i_ond = json.loads(handle(
        {"jsonrpc": "2.0", "id": 1800, "method": "tools/call",
         "params": {"name": "fusing_current", "arguments": {
             "width_mm": 2.0, "t_s": 1.0, "thickness_um": 35,
             "t_ambient_c": 25.0}}})["result"]["content"][0]["text"])["value"]
    _t_uni = json.loads(handle(
        {"jsonrpc": "2.0", "id": 1801, "method": "tools/call",
         "params": {"name": "fusing_current_taper", "arguments": {
             "segments": [{"length_mm": 100.0, "width_mm": 2.0}],
             "thickness_um": 35, "current_a": _i_ond, "t_end_s": 1.5}}})
        ["result"]["content"][0]["text"])
    check("a uniform taper segment melts when Onderdonk says (%s s at %s A)"
          % (_t_uni.get("t_melt_s"), _i_ond),
          _t_uni.get("status") == "ok" and _t_uni.get("melted") is True
          and abs(_t_uni["t_melt_s"] - 1.0) < 0.01
          and _t_uni["adiabatic_control"]["conduction_delay_pct"] == 0.0,
          "%s s, delay %s" % (_t_uni.get("t_melt_s"),
                              (_t_uni.get("adiabatic_control") or {})
                              .get("conduction_delay_pct")))

    _neck = [{"length_mm": 20.0, "width_mm": 2.0},
             {"length_mm": 2.0, "width_mm": 0.5},
             {"length_mm": 20.0, "width_mm": 2.0}]
    _t_nk = json.loads(handle(
        {"jsonrpc": "2.0", "id": 1802, "method": "tools/call",
         "params": {"name": "fusing_current_taper", "arguments": {
             "segments": _neck, "thickness_um": 35, "current_a": 30.0,
             "t_end_s": 1.0}}})["result"]["content"][0]["text"])
    _t_um = json.loads(handle(
        {"jsonrpc": "2.0", "id": 1803, "method": "tools/call",
         "params": {"name": "fusing_current_taper", "arguments": {
             "segments": [{"length_mm": 42.0, "width_mm": 1.5}],
             "thickness_um": 35, "current_a": 30.0,
             "t_end_s": 1.0}}})["result"]["content"][0]["text"])
    check("a neck melts in the narrow segment, before the uniform mean-width trace "
          "(%s vs %s s) with the arms' delay measured"
          % (_t_nk.get("t_melt_s"), _t_um.get("t_melt_s")),
          _t_nk.get("melt_segment") == 1
          and abs(_t_nk.get("melt_at_mm") - 21.0) < 1e-6
          and _t_nk["t_melt_s"] < _t_um["t_melt_s"]
          and (_t_nk["adiabatic_control"]["conduction_delay_pct"] or 0) > 5.0,
          "delay %s %%" % ((_t_nk.get("adiabatic_control") or {})
                           .get("conduction_delay_pct")))

    _t_low = json.loads(handle(
        {"jsonrpc": "2.0", "id": 1804, "method": "tools/call",
         "params": {"name": "fusing_current_taper", "arguments": {
             "segments": [{"length_mm": 100.0, "width_mm": 2.0}],
             "thickness_um": 35, "current_a": 2.0, "t_end_s": 1.0}}})
        ["result"]["content"][0]["text"])
    check("a current that does not melt returns NO time, only the peak and margin",
          _t_low.get("status") == "ok" and _t_low.get("melted") is False
          and _t_low.get("value") is None and _t_low.get("t_melt_s") is None
          and _t_low.get("margin_to_melt_k") > 100.0,
          str(_t_low.get("peak_c")))

    # --- plane edges and the Huray surface (0.0.60). Three numbers, each with a
    # negative control that a blanket implementation would fail.
    def _call(tool, args, rid):
        return json.loads(handle(
            {"jsonrpc": "2.0", "id": rid, "method": "tools/call",
             "params": {"name": tool, "arguments": args}})
        ["result"]["content"][0]["text"])

    _hu = _call("roughness_huray", {"f_hz": 1e9, "nodule_radius_um": 0.5,
                                    "surface_ratio": 1.0}, 1805)
    _hu_v = _hu.get("value") or {}
    _hu0 = _call("roughness_huray", {"f_hz": 1e9, "nodule_radius_um": 0.5,
                                     "surface_ratio": 0.0}, 1806)
    check("Huray: r=0.5 um at 1 GHz is 11.95 % above smooth copper, and the "
          "reported knee is the frequency where delta = r/2 (F = 1/sqrt(5))",
          _hu.get("status") == "ok"
          and abs(_hu_v.get("loss_factor_K", -1) - 1.11953) < 1e-4
          and abs((_hu_v.get("surface_ratio_dc") or 0) - 2.5) < 1e-12
          and abs(_hu_v.get("knee_skin_depth_um", -1) - 0.25) < 1e-6
          and (_hu0.get("value") or {}).get("loss_factor_K") == 1.0,
          "K %s, knee %s Hz at delta %s um, flat K %s" % (
              _hu_v.get("loss_factor_K"), _hu_v.get("knee_frequency_hz"),
              _hu_v.get("knee_skin_depth_um"),
              (_hu0.get("value") or {}).get("loss_factor_K")))
    _hu_big = _call("roughness_huray", {"f_hz": 1e20, "nodule_radius_um": 0.5,
                                        "surface_ratio": 3.0}, 1807)
    check("Huray refuses an optical frequency instead of clamping: the snowball "
          "model is quasi-static and 1e20 Hz puts the nodule above a wavelength",
          _hu_big.get("status") == "error"
          and _hu_big.get("reason_code") == "MODEL_SCOPE",
          str(_hu_big.get("reason_code")))

    _rq = _call("plane_radiation_q", {"a_mm": 50.0, "b_mm": 50.0, "h_mm": 0.2,
                                      "er": 4.3}, 1808)
    _rq_v = _rq.get("value") or {}
    check("radiation Q of a 50 mm square, 0.2 mm FR4, (1,0): er*a/(6h) = 179.17, "
          "and it is the copper that still dominates at 1.45 GHz",
          _rq.get("status") == "ok"
          and abs(_rq_v.get("q_radiation", -1) - 4.3 * 50.0 / (6.0 * 0.2)) < 0.01
          and _rq_v.get("which_dominates") == "copper"
          and abs(_rq_v.get("k0_h", -1) - 0.00606) < 1e-5,
          "Q_rad %s, Qc %s, Q_tot %s, k0h %s" % (
              _rq_v.get("q_radiation"), _rq_v.get("q_conductor"),
              _rq_v.get("q_total"), _rq_v.get("k0_h")))
    _rq_big = _call("plane_radiation_q", {"a_mm": 50.0, "b_mm": 50.0,
                                          "h_mm": 100.0, "er": 4.3}, 1809)
    check("radiation Q refuses k0*h >= 1: a 100 mm gap cannot be a narrow slot, "
          "the aperture would radiate more than is incident on it",
          _rq_big.get("status") == "error"
          and _rq_big.get("reason_code") == "MODEL_SCOPE",
          str(_rq_big.get("reason_code")))

    _oe = _call("plane_open_end", {"a_mm": 50.0, "b_mm": 50.0, "h_mm": 0.2,
                                   "er": 4.3}, 1810)
    _oe_v = _oe.get("value") or {}
    _oe_y = _call("plane_open_end", {"a_mm": 50.0, "b_mm": 30.0, "h_mm": 0.2,
                                     "er": 4.3, "m": 1, "n": 0,
                                     "open_edges": ["y0", "y1"]}, 1811)
    _oe_y_v = _oe_y.get("value") or {}
    check("an open edge adds delta_l = (h/2pi)(ln(2pi*a/h)+1) = 0.2661 mm, i.e. "
          "-1.053 % on a 1.45 GHz mode, and a (1,0) mode does NOT move when only "
          "the y edges open",
          _oe.get("status") == "ok"
          and abs((_oe_v.get("edge_extension_mm") or {}).get("x", -1)
                  - 0.266086) < 1e-5
          and abs(_oe_v.get("delta_f_over_f_pct", 0) + 1.05313) < 1e-4
          and _oe_y_v.get("delta_f_over_f") == 0.0,
          "dl %s mm, shift %s %%, y-only shift %s" % (
              (_oe_v.get("edge_extension_mm") or {}).get("x"),
              _oe_v.get("delta_f_over_f_pct"), _oe_y_v.get("delta_f_over_f")))

    _cf_strip = _call("current_density_field", {
        "polygons": [[[0.0, 0.0], [20.0, 0.0], [20.0, 2.0], [0.0, 2.0]]],
        "source": {"x0_mm": -0.01, "y0_mm": -0.01, "x1_mm": 0.0, "y1_mm": 2.01},
        "sink": {"x0_mm": 20.0, "y0_mm": -0.01, "x1_mm": 20.01, "y1_mm": 2.01},
        "h_mm": 0.1, "levels": 2}, 1812)
    _cf_strip_v = _cf_strip.get("value") or {}
    check("a uniform 20x2 mm strip conducts as rho/t*L/W = 4.9257 mOhm (measured "
          "4.9011, the 0.5 % is the contact sitting at the cell centre) and the "
          "field is flat: peak/median 1.0000, crowding 1.0000",
          _cf_strip.get("status") == "ok"
          and abs(_cf_strip_v.get("resistance_ohm", -1) / 0.004925714 - 1.0) < 0.01
          and abs(_cf_strip_v.get("peak_growth", -1) - 1.0) < 1e-3
          and abs(_cf_strip_v.get("crowding_factor", -1) - 1.0) < 0.01
          and not _cf_strip.get("warnings"),
          "R %s, growth %s, crowding %s" % (
              _cf_strip_v.get("resistance_ohm"), _cf_strip_v.get("peak_growth"),
              _cf_strip_v.get("crowding_factor")))

    _cf_bend = _call("current_density_field", {
        "polygons": [[[0.0, 10.5], [10.5, 10.5], [10.5, 0.0], [9.5, 0.0],
                      [9.5, 9.5], [0.0, 9.5]]],
        "source": {"x0_mm": -0.01, "y0_mm": 9.49, "x1_mm": 0.0, "y1_mm": 10.51},
        "sink": {"x0_mm": 9.49, "y0_mm": -0.01, "x1_mm": 10.51, "y1_mm": 0.0},
        "h_mm": 0.05, "levels": 3}, 1813)
    _cf_bend_v = _cf_bend.get("value") or {}
    check("the 270 degree inner corner of a bend is singular: the peak grows by "
          "2^(1/3) = 1.2599 per halving of h (measured 1.2660), so the tool says "
          "peak_is_grid_convergent=false and points at the ring decay 0.7963 "
          "(theory 0.7937) instead",
          _cf_bend.get("status") == "ok"
          and abs(_cf_bend_v.get("peak_growth", -1) - 2 ** (1.0 / 3.0)) < 0.05
          and _cf_bend_v.get("peak_is_grid_convergent") is False
          and any("NOT GRID CONVERGENT" in w for w in (_cf_bend.get("warnings") or []))
          and abs((_cf_bend_v.get("rings") or {}).get(
              "decay_2h_4h_over_0.5h_1h", -1) - 2 ** (-1.0 / 3.0)) < 0.05,
          "growth %s, decay %s" % (
              _cf_bend_v.get("peak_growth"),
              (_cf_bend_v.get("rings") or {}).get("decay_2h_4h_over_0.5h_1h")))

    _cf_split = _call("current_density_field", {
        "paths": [{"layer_mm": [0, 0], "width_mm": 1.0,
                   "points": [[0.0, 0.0], [2.0, 0.0]]},
                  {"layer_mm": [0, 0], "width_mm": 1.0,
                   "points": [[5.0, 0.0], [7.0, 0.0]]}],
        "source": {"x0_mm": -0.01, "y0_mm": -0.6, "x1_mm": 0.0, "y1_mm": 0.6},
        "sink": {"x0_mm": 7.0, "y0_mm": -0.6, "x1_mm": 7.01, "y1_mm": 0.6},
        "h_mm": 0.1, "levels": 2}, 1814)
    check("two islands that do not touch are refused instead of being solved as "
          "whatever the mesh happens to connect: NOT_CONNECTED names the "
          "component count, so a missing pour is diagnosable",
          _cf_split.get("status") == "error"
          and _cf_split.get("reason_code") == "NOT_CONNECTED"
          and "Components found: 2" in (_cf_split.get("hint") or ""),
          str(_cf_split.get("reason_code")))

    # ---- 0.0.64: a copper pour is copper too. The fixture carries NO tracks at
    # all - two 1x10 mm pads 20 mm apart joined only by a 30x10 mm pour - so the
    # whole answer is a sheet resistance and nothing else, and it can be counted
    # by hand. Two boards: one where the pour reaches both pads, one where it
    # stops at the source, because "the pour is there" and "the pour connects
    # these two" are different facts and only the second one is copper.
    _pz_tmp = _tempfile.mkdtemp(prefix="pcbagent-pour-")

    def _pour_board(name, x1):
        path = os.path.join(_pz_tmp, name)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write((
                '(kicad_pcb (version 20221018) (generator pcbnew)\n'
                '  (general (thickness 1.6))\n'
                '  (layers (0 "F.Cu" signal) (31 "B.Cu" signal)'
                ' (44 "Edge.Cuts" user))\n'
                '  (setup (stackup\n'
                '      (layer "F.Cu" (type "copper") (thickness 0.035))\n'
                '      (layer "dielectric 1" (type "core") (thickness 1.53))\n'
                '      (layer "B.Cu" (type "copper") (thickness 0.035))))\n'
                '  (net 0 "") (net 1 "VCC")\n'
                '  (gr_rect (start 0 0) (end 50 30) (layer "Edge.Cuts")'
                ' (width 0.1))\n'
                '  (footprint "T:A" (layer "F.Cu") (at 10 10)\n'
                '    (fp_text reference "U1" (at 0 -6) (layer "F.SilkS"))\n'
                '    (pad "1" smd rect (at 0 0) (size 1 10) (layers "F.Cu")'
                ' (net 1 "VCC")))\n'
                '  (footprint "T:B" (layer "F.Cu") (at 30 10)\n'
                '    (fp_text reference "U2" (at 0 -6) (layer "F.SilkS"))\n'
                '    (pad "1" smd rect (at 0 0) (size 1 10) (layers "F.Cu")'
                ' (net 1 "VCC")))\n'
                '  (zone (net 1) (net_name "VCC") (layer "F.Cu") (tstamp 0)\n'
                '    (hatch edge 0.508) (priority 0)\n'
                '    (connect_pads (clearance 0.2))\n'
                '    (min_thickness 0.25)\n'
                '    (fill yes (thermal_gap 0.5) (thermal_bridge_width 0.5))\n'
                '    (polygon (pts (xy 5 5) (xy %.6g 5) (xy %.6g 15)'
                ' (xy 5 15)))\n'
                '    (filled_polygon (layer "F.Cu")\n'
                '      (pts (xy 5 5) (xy %.6g 5) (xy %.6g 15) (xy 5 15)))))\n'
                ) % (x1, x1, x1, x1))
        return path

    _pz_full = _pour_board("pour_full.kicad_pcb", 35)
    _pz_half = _pour_board("pour_half.kicad_pcb", 15)
    _pz_off = _call("board_ir_drop", {"path": _pz_full, "net": "VCC",
                                      "source": "U1.1", "sinks": ["U2.1"],
                                      "current_a": 1.0}, 1815)
    check("a net joined by nothing but its pour is still refused with the switch "
          "OFF: the track-only graph is two halves that never meet, so 'no tracks "
          "on this board' is the honest answer and the default path is unchanged",
          _pz_off.get("status") == "error"
          and _pz_off.get("reason_code") == "NOT_APPLICABLE",
          str(_pz_off.get("reason_code")))

    _pz_on = _call("board_ir_drop", {"path": _pz_full, "net": "VCC",
                                     "source": "U1.1", "sinks": ["U2.1"],
                                     "current_a": 1.0, "model_zones": True,
                                     "zone_grid_mm": 0.5, "max_zone_nodes": 20000},
                   1816)
    _pz_v = _pz_on.get("value") or {}
    check("with model_zones the pour IS the answer: 1.9 squares of rho/t = "
          "0.9359 mV at 1 A, measured 0.935986 (0.01 %), while the naive "
          "20 mm/10 mm reading of 2 squares = 0.9851 mV is the UPPER bound at "
          "+5.3 % - the pads are 1 mm wide, so the current spreads",
          _pz_on.get("status") == "ok"
          and abs((_pz_v.get("max_drop_mv") or 0) - 0.935896) < 0.002
          and (_pz_on.get("solver") or {}).get("converged") is True
          and [x["layer"] for x in (_pz_on.get("zone_summary") or {}).get("sheets", [])]
              == ["F.Cu"],
          "drop %s mV, solver %s" % (_pz_v.get("max_drop_mv"),
                                     (_pz_on.get("solver") or {}).get("iterations")))

    _pz_cut = _call("board_ir_drop", {"path": _pz_half, "net": "VCC",
                                      "source": "U1.1", "sinks": ["U2.1"],
                                      "current_a": 1.0, "model_zones": True,
                                      "zone_grid_mm": 0.5, "max_zone_nodes": 20000},
                    1817)
    check("a pour that stops 15 mm short does NOT make the net connected: the "
          "answer is NOT_CONNECTED naming the sink, not a small drop from the "
          "pour that happens to be nearby",
          _pz_cut.get("status") == "error"
          and _pz_cut.get("reason_code") == "NOT_CONNECTED"
          and "U2.1" in (_pz_cut.get("message") or ""),
          str(_pz_cut.get("reason_code")))
    _shutil.rmtree(_pz_tmp, ignore_errors=True)

    # ---- 0.0.65: the same network, but stepped. |Z| and a load transient are
    # different questions, so the checks are different too: a DC anchor the curve
    # has to land on, a charge identity that only closes if every branch made it
    # into the node equation, and the one thing the frequency domain cannot say -
    # the same caps 30 mm away are worse.
    _st_a = _call("pdn_step_response",
                  {"caps": [{"c_farad": 100e-6, "esr_ohm": 0.01}],
                   "step_a": 2.0, "r_series_ohm": 0.02}, 1818)
    _st_v = _st_a.get("value") or {}
    check("a 2 A step on 100 uF is checked against DC, not against a picture: the "
          "tail lands on -I*R = -40.000 mV (dc_check_rel 1.2e-06, settled 12.7 us "
          "inside 40 us), and the charge the caps gave up (-C*du) equals what went "
          "past the regulator to within 8e-15 of it - an identity that only closes "
          "if every branch is in the node equation",
          _st_a.get("status") == "ok"
          and abs((_st_v.get("v_final_v") or 0) + 0.04) < 1e-6
          and (_st_v.get("dc_check_rel") or 1) < 1e-4
          and (_st_v.get("charge_balance_rel") or 1) < 1e-9
          and _st_v.get("tail_settled") is True,
          "tail %s V, charge %s, settled %s" % (_st_v.get("v_final_v"),
                                                _st_v.get("charge_balance_rel"),
                                                _st_v.get("tail_settled")))

    _st_b = _call("pdn_step_response",
                  {"caps": [{"c_farad": 1e-6, "esl_h": 1e-9}],
                   "r_series_ohm": 0.01}, 1819)
    check("an IDEAL current step into 1 nH is refused, not answered: V = L*dI/dt "
          "would be set by the time step (a 20 A step at 10 ns is 2000 V), so the "
          "tool asks for rise_ns instead of reporting a number that is really the "
          "grid",
          _st_b.get("status") == "error"
          and _st_b.get("reason_code") == "ARG_REQUIRED"
          and "rise_ns" in (_st_b.get("message") or ""),
          str(_st_b.get("reason_code")))

    _st_o = {"step_a": 10.0, "r_series_ohm": 0.001, "plane_r_ohm": 0.002,
             "plane_l_h": 1e-9, "rise_ns": 400.0}
    _st_die = (_call("pdn_step_response",
                     {"caps": [{"c_farad": 100e-6, "esr_ohm": 0.002,
                                "at": "die"}], **_st_o}, 1820).get("value") or {})
    _st_bulk = (_call("pdn_step_response",
                      {"caps": [{"c_farad": 100e-6, "esr_ohm": 0.002,
                                 "at": "bulk"}], **_st_o}, 1821).get("value") or {})
    check("where the caps sit is in the answer, and it is in the PEAK, not in the "
          "tail: the same 100 uF behind 2 mohm + 1 nH of board peaks at -52.94 mV "
          "against -32.52 mV with the cap at the die (1.6x), while both tails land "
          "on the same -30.00 mV because at DC the cap is open and the load current "
          "crosses that plane either way",
          abs((_st_die.get("peak_v") or 0) + 0.03252) < 3e-4
          and abs((_st_bulk.get("peak_v") or 0) + 0.05294) < 3e-4
          and abs((_st_die.get("v_final_v") or 0) + 0.03) < 1e-6
          and abs((_st_bulk.get("v_final_v") or 0) + 0.03) < 1e-6,
          "die %s V, bulk %s V" % (_st_die.get("peak_v"), _st_bulk.get("peak_v")))

    _st_bare = (_call("pdn_step_response",
                      {"caps": [{"c_farad": 100e-6, "esr_ohm": 0.002}],
                       "step_a": 10.0, "r_series_ohm": 0.001,
                       "rise_ns": 400.0}, 1822).get("value") or {})
    check("the plane is NOT silently dropped when every cap says at='die': the same "
          "network with no plane answers -10.00 mV and with 2 mohm of plane answers "
          "-30.00 mV, because the regulator current has to cross that plane either "
          "way - dropping it would have answered a 3x-too-small droop",
          abs((_st_bare.get("v_final_v") or 0) + 0.01) < 1e-6
          and abs((_st_die.get("v_final_v") or 0) + 0.03) < 1e-6
          and _st_bare.get("nodes") == _st_die.get("nodes"),
          "no plane %s V, plane %s V" % (_st_bare.get("v_final_v"),
                                         _st_die.get("v_final_v")))

    # ---- 0.0.66: DC copper is a low-frequency limit, not the answer. 1 oz at
    # 1 MHz is already 0.70 % high in R and 10 MHz is 54 % high; the closed form
    # is R/Rdc = s*(sinh2s+sin2s)/(cosh2s-cos2s) with s = t/delta, and the two
    # limits (1+4s^4/45 and s) are the only reason to believe it.
    _sk = (_call("ac_sheet_resistance", {"frequency_hz": 1.0e7,
                                        "thickness_um": 35.0}, 1823).get("value") or {})
    check("1 oz copper at 10 MHz is 1.5408x its DC resistance (delta 20.90 um, "
          "t/delta 1.675, so it is NOT yet in the sqrt(f) regime) and it starts "
          "being 1 % off at 1.1983 MHz",
          abs((_sk.get("r_ac_over_r_dc") or 0) - 1.5408001) < 1e-6
          and abs((_sk.get("skin_depth_um") or 0) - 20.90) < 0.02
          and abs((_sk.get("thickness_over_skin_depth") or 0) - 1.6749) < 1e-3
          and abs((_sk.get("f_at_r_1pct_over_dc_hz") or 0) - 1198257.2) < 5.0,
          "R/Rdc %s, delta %s um, s %s, f_1%% %s Hz" % (
              _sk.get("r_ac_over_r_dc"), _sk.get("skin_depth_um"),
              _sk.get("thickness_over_skin_depth"),
              _sk.get("f_at_r_1pct_over_dc_hz")))

    # The face rule is a factor of two on the EFFECTIVE thickness, nothing more:
    # a sheet with both faces free and thickness t is the same problem as a sheet
    # against a plane with thickness t/2. If these two disagree the parametrisation
    # is wrong somewhere.
    _sk_two = (_call("ac_sheet_resistance", {"frequency_hz": 1.0e7,
                                            "thickness_um": 35.0,
                                            "faces": "two"}, 1824).get("value") or {})
    _sk_half = (_call("ac_sheet_resistance", {"frequency_hz": 1.0e7,
                                             "thickness_um": 17.5}, 1825).get("value") or {})
    check("faces='two' on 35 um IS faces='one' on 17.5 um (a symmetric sheet sees "
          "half the thickness), so the factor of two is in the geometry and not "
          "bolted onto the answer",
          abs((_sk_two.get("r_ac_over_r_dc") or 0)
              - (_sk_half.get("r_ac_over_r_dc") or 0)) < 1e-12
          and abs((_sk_two.get("x_ac_over_r_dc") or 0)
                  - (_sk_half.get("x_ac_over_r_dc") or 0)) < 1e-12
          and (_sk_two.get("effective_thickness_um") or 0) < 35.0,
          "two %s vs half %s" % (_sk_two.get("r_ac_over_r_dc"),
                                 _sk_half.get("r_ac_over_r_dc")))

    # The default path must not move. Same arguments as the 0.0.63 check above,
    # but read for bit equality against the no-frequency call, and the frequency
    # call must differ from it by EXACTLY r_ac_over_r_dc (the solve is linear, so
    # the pattern is untouched and only the conductance scale changes).
    _cf_dc2 = _call("current_density_field", {
        "polygons": [[[0.0, 0.0], [20.0, 0.0], [20.0, 2.0], [0.0, 2.0]]],
        "source": {"x0_mm": -0.01, "y0_mm": -0.01, "x1_mm": 0.0, "y1_mm": 2.01},
        "sink": {"x0_mm": 20.0, "y0_mm": -0.01, "x1_mm": 20.01, "y1_mm": 2.01},
        "h_mm": 0.1, "levels": 2}, 1826).get("value") or {}
    _cf_ac = _call("current_density_field", {
        "polygons": [[[0.0, 0.0], [20.0, 0.0], [20.0, 2.0], [0.0, 2.0]]],
        "source": {"x0_mm": -0.01, "y0_mm": -0.01, "x1_mm": 0.0, "y1_mm": 2.01},
        "sink": {"x0_mm": 20.0, "y0_mm": -0.01, "x1_mm": 20.01, "y1_mm": 2.01},
        "h_mm": 0.1, "levels": 2, "frequency_hz": 1.0e7}, 1827).get("value") or {}
    check("asking current_density_field for 10 MHz scales the conductance by "
          "t_eff/t and nothing else: R is exactly DC x 1.5408, peak J is exactly "
          "DC x the same factor (it is the density in the EFFECTIVE layer, with "
          "peak_j_gross = peak/r as the through-thickness mean), and crowding - a "
          "pure geometry ratio - does not move at all",
          _cf_dc2.get("frequency_hz") is None
          and _cf_ac.get("r_ac_over_r_dc") is not None
          and abs(_cf_ac.get("resistance_ohm", 0)
                  / _cf_dc2.get("resistance_ohm", 1) - 1.5408001) < 1e-6
          and abs(_cf_ac.get("peak_j_a_per_mm2", 0)
                  / _cf_dc2.get("peak_j_a_per_mm2", 1) - 1.5408001) < 1e-6
          and abs(_cf_ac.get("peak_j_gross_a_per_mm2", 0)
                  - _cf_dc2.get("peak_j_a_per_mm2", 0)) < 1e-9
          and abs(_cf_ac.get("crowding_factor", 0)
                  - _cf_dc2.get("crowding_factor", 1)) < 1e-12,
          "R %s -> %s, crowding %s -> %s" % (
              _cf_dc2.get("resistance_ohm"), _cf_ac.get("resistance_ohm"),
              _cf_dc2.get("crowding_factor"), _cf_ac.get("crowding_factor")))

    # A frequency the model cannot mean, and a face count that does not exist,
    # are REFUSED with a reason code instead of being clamped to DC / to 'one'.
    _sk_zero = _call("ac_sheet_resistance", {"frequency_hz": 0.0,
                                            "thickness_um": 35.0}, 1828)
    _sk_face = _call("ac_sheet_resistance", {"frequency_hz": 1.0e6,
                                            "thickness_um": 35.0,
                                            "faces": "three"}, 1829)
    _sk_thick = _call("ac_sheet_resistance", {"frequency_hz": 1.0e6}, 1830)
    check("ac_sheet_resistance refuses f=0 (it would silently BE the DC answer), "
          "a face count other than one/two (they differ by 2x in effective "
          "thickness) and a call with no thickness at all",
          _sk_zero.get("reason_code") == "BAD_PARAMETER"
          and "frequency" in (_sk_zero.get("message") or "").lower()
          and _sk_face.get("reason_code") == "BAD_PARAMETER"
          and "one" in (_sk_face.get("message") or "").lower()
          and "two" in (_sk_face.get("message") or "").lower()
          and _sk_thick.get("reason_code") == "ARG_REQUIRED",
          "%s / %s / %s" % (_sk_zero.get("reason_code"),
                            _sk_face.get("reason_code"),
                            _sk_thick.get("reason_code")))

    # ---- 0.0.67: a ROUND conductor is not a flat sheet, and a tube is not a
    # mirror half of a solid wire.  A 0.15 mm drill with 25 um of plating at
    # 1 GHz is 10.58x R_dc of the annulus, and the DC number is off by an order
    # of magnitude.
    _rw = (_call("ac_round_conductor", {"frequency_hz": 1.0e9, "drill_mm": 0.15,
                                       "plating_um": 25.0}, 1831).get("value") or {})
    _rwc = (_call("ac_round_conductor", {"frequency_hz": 1.0e9, "drill_mm": 0.15,
                                        "plating_um": 25.0}, 1832).get("checks") or {})
    check("a 0.15 mm via with 25 um of plating at 1 GHz is 10.5781x its DC annulus "
          "(delta 2.09 um, x = 47.85) - the barrel is a surface, not a wire",
          abs((_rw.get("ac_over_dc") or 0) - 10.5781254) < 1e-6
          and abs((_rw.get("skin_depth_um") or 0) - 2.0897) < 1e-3
          and abs((_rw.get("x_outer") or 0) - 47.8532) < 1e-3
          and "skin-dominated" in (_rw.get("regime") or ""),
          "R/Rdc %s, delta %s um, x %s" % (_rw.get("ac_over_dc"),
                                           _rw.get("skin_depth_um"),
                                           _rw.get("x_outer")))

    # The one place this could go wrong and still LOOK right: the tube's Bessel
    # closed form is J0/J1/Y0/Y1 in cancellation and above x ~ 15 it returns the
    # SOLID answer digit for digit.  At x = 40 with a1/a2 = 0.9 that is 20.2523
    # against a truth of 3.8501 - a factor of 5.26, with no warning.
    # ...and the answer it gives at exactly that geometry, end to end.
    _cf = (_call("ac_round_conductor", {"frequency_hz": 698710882.3975614,
                                       "diameter_mm": 0.2, "wall_um": 10.0},
                 1833).get("value") or {})
    check("the tube closed form is REFUSED above x = 10 and the answer says so: at "
          "x = 40, a1/a2 = 0.9 that formula gives 20.2523 (the solid wire) where "
          "the truth is 3.8501, and the tool answers 3.8501 with closed_form_skipped "
          "in the envelope rather than a number that would look like agreement",
          abs((_cf.get("ac_over_dc") or 0) - 3.8500959) < 1e-6
          and abs((_cf.get("x_outer") or 0) - 40.0) < 1e-9
          and _rwc.get("closed_form_skipped") is not None
          and _rwc.get("closed_form_vs_riccati_rel") is None
          and "20.2523" in (_rwc.get("closed_form_skipped") or ""),
          "R/Rdc %s (the collapsed closed form would say 20.2523)" % (
              _cf.get("ac_over_dc"),))

    # The answer path must reproduce eight mpmath pins (80-900 dps, independent
    # integrator).  Two of them are enough in a self test: the hardest (x = 71,
    # a1/a2 = 0.845, which needs ~70 dps before the closed form gives 15 digits)
    # and the DC anchor mu/(8 pi) = 50.0 nH/m, which does not depend on diameter.
    _rwdc = (_call("ac_round_conductor", {"frequency_hz": 1.0e6,
                                        "diameter_mm": 1.0}, 1834).get("checks") or {})
    _rwsolid = (_call("ac_round_conductor", {"frequency_hz": 1.0e6,
                                           "diameter_mm": 1.0},
                      1835).get("value") or {})
    check("the round wire's DC internal inductance is mu/(8*pi) = 50.0 nH/m at ANY "
          "diameter (2.1e-11 here) and its DC series is 1 + x^4/48 - a flat sheet "
          "would be 1 + 4x^4/45, so the two geometries cannot be confused",
          abs((_rwdc.get("dc_internal_inductance_nh_per_m") or 0) - 50.0) < 1e-6
          and (_rwdc.get("dc_series_r_rel_error") or 1) < 1e-12
          and (_rwdc.get("dc_series_x_rel_error") or 1) < 1e-9,
          "Ldc %s nH/m" % (_rwdc.get("dc_internal_inductance_nh_per_m"),))

    # A wall thinner than one skin depth is not skin-limited at all: 0.5 um of
    # plating at 1 GHz is 1.0003x R_dc, because the current uses all the copper
    # there is.  This is the case where "use the skin depth" answers nonsense.
    _rww = (_call("ac_round_conductor", {"frequency_hz": 1.0e9, "drill_mm": 0.1,
                                       "plating_um": 0.5}, 1836).get("value") or {})
    check("a 0.5 um plating at 1 GHz is 1.0003x R_dc and is REPORTED as "
          "wall-limited (the current fills the wall), not as skin-dominated - "
          "thickening the plating is what helps there, not widening the drill",
          abs((_rww.get("ac_over_dc") or 0) - 1.0002898) < 1e-5
          and "wall-limited" in (_rww.get("regime") or ""),
          "R/Rdc %s, regime %s" % (_rww.get("ac_over_dc"),
                                   (_rww.get("regime") or "")[:30]))

    # The radial profile is a picture, and a plausible wrong picture is worse
    # than an error: the closed form reports a FLAT 1.0 at x = 72.  The answer
    # path must show a real decay, and the envelope must say which one it used.
    _rwp = (_call("ac_round_conductor",
                  {"frequency_hz": 2.263823e9, "diameter_mm": 0.2,
                   "wall_um": 31.0}, 1837))
    _pf = (_rwp.get("value") or {}).get("profile") or []
    check("at x = 72 the radial profile decays over five decades inward instead of "
          "reporting a flat 1.0 (which is what the Bessel evaluation returns there, "
          "and why it is only used as a cross-check below x = 5)",
          len(_pf) == 9 and (_pf[0].get("j_rel") or 1) < 1e-8
          and abs((_pf[-1].get("j_rel") or 0) - 1.0) < 1e-12
          and (_rwp.get("checks") or {}).get("profile_vs_closed_skipped") is not None,
          "j(0)=%s" % (_pf[0].get("j_rel") if _pf else None,))

    # Two hole conventions exist and they are 18 % apart. Neither is silently
    # assumed: the AC tool's bore is the FINISHED hole (copper outside), and
    # via_resistance gains a flag for the same drawing while its DEFAULT answer
    # stays bit-identical.
    _vo = (_call("via_resistance", {"drill_mm": 0.3, "plating_um": 25.0,
                                    "length_mm": 1.6}, 1838).get("value") or {})
    _vi = (_call("via_resistance", {"drill_mm": 0.3, "plating_um": 25.0,
                                    "length_mm": 1.6,
                                    "plating_outside": True}, 1839).get("value") or {})
    check("the two via hole conventions are both available and 18 % apart "
          "(0.021598 vs 0.025525 mm2 on a 0.3 mm hole with 25 um), the default is "
          "unchanged, and each answer names the convention it used",
          abs((_vo.get("barrel_area_mm2") or 0) - 0.021598) < 1e-6
          and abs((_vi.get("barrel_area_mm2") or 0) - 0.025525) < 1e-6,
          "inside %s mm2 / outside %s mm2" % (_vo.get("barrel_area_mm2"),
                                              _vi.get("barrel_area_mm2")))

    # A guessed bore is the way a via answer becomes wrong, so it is refused.
    _rw_no = _call("ac_round_conductor", {"frequency_hz": 1.0e6,
                                         "drill_mm": 0.3}, 1840)
    _rw_both = _call("ac_round_conductor", {"frequency_hz": 1.0e6,
                                           "diameter_mm": 1.0,
                                           "drill_mm": 0.3}, 1841)
    _rw_f0 = _call("ac_round_conductor", {"frequency_hz": 0.0,
                                         "diameter_mm": 1.0}, 1842)
    check("ac_round_conductor refuses a drill with no plating, a diameter AND a "
          "drill (two descriptions of one outer diameter), and f = 0 (which would "
          "silently BE the DC answer)",
          _rw_no.get("reason_code") == "ARG_REQUIRED"
          and _rw_both.get("reason_code") == "ARG_INVALID"
          and _rw_f0.get("reason_code") == "BAD_PARAMETER"
          and "via_resistance" in (_rw_f0.get("message") or ""),
          "%s / %s / %s" % (_rw_no.get("reason_code"), _rw_both.get("reason_code"),
                            _rw_f0.get("reason_code")))

    # ---- 0.0.68: copper at 1 GHz has a RESISTANCE, and a resistance times a
    # current times a lump Rth is a TEMPERATURE - but the loop is not linear in
    # dT once the current lives in a shell, so it is solved as a root, not
    # iterated to a fixed point. 35 um of copper at 3 A settles at 26.82 C at
    # 100 Hz and at 56.77 C at 1 GHz.
    _sh = {"shape": "sheet", "length_mm": 20.0, "width_mm": 1.0,
           "thickness_um": 35.0, "current_a": 3.0, "rth_c_per_w": 20.0}
    _ah_dc = _call("ac_self_heating", dict(_sh, frequency_hz=100.0), 1843)
    check("at 100 Hz the AC loop IS the DC loop: 26.8208 C (dT 1.82079 K) against "
          "electrothermal_conductor's own closed form to 3.0e-7 relative, and the "
          "measured dlnR/dlnrho is exactly 1",
          abs(((_ah_dc.get("value") or {}).get("t_final_c") or 0) - 26.8208) < 1e-4
          and abs(((_ah_dc.get("value") or {}).get("delta_t_c") or 0) - 1.82079) < 1e-5
          and (((_ah_dc.get("value") or {}).get("checks") or {})
               .get("dc_linear_model_rel_diff") or 1) < 1e-6
          and ((_ah_dc.get("value") or {}).get("dlnr_dlnrho") or 0) == 1.0,
          "T %s C, DC rel %s" % ((_ah_dc.get("value") or {}).get("t_final_c"),
                                 ((_ah_dc.get("value") or {}).get("checks") or {})
                                 .get("dc_linear_model_rel_diff")))

    # The same trace at 1 GHz: the copper is a shell, R goes up 16.6x, and the
    # exponent is 1/2 - which is the single number that decides what a runaway
    # MEANS. The DC model of this conductor is 7.9 % cold here, and that gap is
    # reported, not hidden.
    _ah_rf = _call("ac_self_heating", dict(_sh, frequency_hz=1.0e9), 1844)
    _ahv = _ah_rf.get("value") or {}
    check("the same 35 um trace at 1 GHz answers 56.7733 C with R_cold 0.166611 "
          "ohm (16.5865x R_dc), R_hot 0.176518, dlnR/dlnrho = 0.5 measured, and "
          "the DC model is off by 7.905 % - reported as a gap, not as an error bar",
          abs((_ahv.get("t_final_c") or 0) - 56.7733) < 1e-4
          and abs((_ahv.get("resistance_cold_ohm") or 0) - 0.166611) < 1e-6
          and abs((_ahv.get("resistance_hot_ohm") or 0) - 0.176518) < 1e-6
          and abs((_ahv.get("dlnr_dlnrho") or 0) - 0.5) < 1e-9
          and abs((( _ahv.get("checks") or {}).get("dc_linear_model_rel_diff")
                   or 0) - 0.07905) < 5e-5
          and "skin-dominated" in (_ahv.get("regime_hot") or ""),
          "T %s C, R_cold %s, DC rel %s" % (_ahv.get("t_final_c"),
                                            _ahv.get("resistance_cold_ohm"),
                                            (_ahv.get("checks") or {})
                                            .get("dc_linear_model_rel_diff")))

    # Two runaways exist and they are NOT the same answer. 12 A on a 0.1 mm
    # trace at DC is algebra: the gain never falls below 1, so no temperature is
    # a fixed point anywhere, and the answer says exactly that.
    _nr = {"shape": "sheet", "length_mm": 20.0, "width_mm": 0.1,
           "thickness_um": 35.0, "rth_c_per_w": 20.0}
    _ah_no = _call("ac_self_heating", dict(_nr, frequency_hz=100.0,
                                          current_a=12.0), 1845)
    check("12 A on a 0.1 mm trace at 100 Hz is refused as THERMAL_RUNAWAY with "
          "'no steady state at any temperature' (cold gain 1.11502, the unit-gain "
          "current is 11.3642 A and Rth would have to be <= 14.4088 K/W), and it "
          "does NOT hand back a made-up root",
          _ah_no.get("reason_code") == "THERMAL_RUNAWAY"
          and "no steady state at any temperature" in (_ah_no.get("message") or "")
          and "root_beyond_melt_t_c" not in _ah_no
          and abs((_ah_no.get("runaway_gain_cold") or 0) - 1.11502) < 1e-5
          and abs((_ah_no.get("unit_gain_current_a") or 0) - 11.3642) < 1e-4,
          "gain %s, I_unit %s" % (_ah_no.get("runaway_gain_cold"),
                                  _ah_no.get("unit_gain_current_a")))

    # Same copper at 1 GHz: the exponent is 1/2, so the fixed point EXISTS and
    # saying "no steady state" would be a lie. It is at 37.3 kK, which is the
    # honest way to say the design is impossible.
    _ah_rb = _call("ac_self_heating", dict(_nr, frequency_hz=1.0e9,
                                          current_a=10.0), 1846)
    check("the same trace at 10 A and 1 GHz has exponent 0.5 and therefore DOES "
          "have a fixed point - it is reported at 37325.8 C (dT 37300.8 K) as an "
          "extrapolation, with 'ordering, not a reading' in the hint, because "
          "'no steady state' and 'no buildable steady state' are different claims",
          _ah_rb.get("reason_code") == "THERMAL_RUNAWAY"
          and "above the melting point" in (_ah_rb.get("message") or "")
          and abs((_ah_rb.get("root_beyond_melt_t_c") or 0) - 37325.8) < 0.05
          and "ordering, not a reading" in (_ah_rb.get("hint") or "")
          and "no steady state at any temperature"
          not in (_ah_rb.get("message") or ""),
          "root %s C" % (_ah_rb.get("root_beyond_melt_t_c"),))

    # The negative control for the pair above, and the bug this release fixed:
    # 11 A is BELOW the 11.3642 A unit-gain current, so an exponent-1 loop has a
    # root (at dT 3854 K). The uncapped search used to be gated on exponent < 1,
    # so this current came back as "no steady state at any temperature" about a
    # loop that demonstrably settles somewhere.
    _ah_edge = _call("ac_self_heating", dict(_nr, frequency_hz=100.0,
                                            current_a=11.0), 1847)
    check("11 A on the same trace is under the unit-gain current (cold gain "
          "0.93693 < 1) and is therefore NOT 'no steady state at any temperature': "
          "the root is found at dT 3854.27 K and reported as an extrapolation - "
          "the exact wrong answer 0.0.67's gate would have produced",
          _ah_edge.get("reason_code") == "THERMAL_RUNAWAY"
          and "no steady state at any temperature"
          not in (_ah_edge.get("message") or "")
          and abs((_ah_edge.get("root_beyond_melt_dt_c") or 0) - 3854.27) < 0.05
          and (_ah_edge.get("runaway_gain_cold") or 0) < 1.0,
          "dT %s K, gain %s" % (_ah_edge.get("root_beyond_melt_dt_c"),
                                _ah_edge.get("runaway_gain_cold")))

    # Frequency does not make a thin wall into a skin. 0.5 um of plating at
    # 1 GHz is wall-limited with an exponent of 0.9995 - the DC answer, which is
    # the case "use the skin depth" gets wrong.
    _ah_wall = _call("ac_self_heating", {"shape": "round", "frequency_hz": 1.0e9,
                                        "current_a": 0.5, "rth_c_per_w": 200.0,
                                        "length_mm": 1.6, "drill_mm": 0.3,
                                        "plating_um": 0.5}, 1848).get("value") or {}
    check("a 0.5 um plating at 1 GHz is REPORTED as wall-limited with "
          "dlnR/dlnrho = 0.999454 and answers 28.0147 C - the current fills the "
          "wall, so the exponent is the DC one even at a gigahertz",
          "wall-limited" in (_ah_wall.get("regime_hot") or "")
          and abs((_ah_wall.get("dlnr_dlnrho") or 0) - 0.999454) < 1e-6
          and abs((_ah_wall.get("t_final_c") or 0) - 28.0147) < 1e-4,
          "regime %s, exponent %s" % (_ah_wall.get("regime_hot"),
                                      _ah_wall.get("dlnr_dlnrho")))

    # Two faces is one face divided by two, but only where the far side cannot
    # be seen - the surface term, not a factor of 2 applied blindly.
    _ah_two = _call("ac_self_heating", dict(_sh, frequency_hz=1.0e9,
                                           faces="two"), 1849).get("value") or {}
    check("faces='two' halves the skin-dominated resistance (0.0833056 vs "
          "0.0833055 for one face) and halves dT (15.4346 vs 31.7733 K) - the "
          "surface term cannot see the far side, which is the only reason the "
          "factor is exact",
          abs((_ah_two.get("resistance_cold_ohm") or 0) - 0.0833056) < 1e-6
          and abs((_ah_two.get("delta_t_c") or 0) - 15.4346) < 1e-4,
          "R_cold %s, dT %s" % (_ah_two.get("resistance_cold_ohm"),
                                _ah_two.get("delta_t_c")))

    # Roughness depends on the skin depth, so it drags the exponent below 1/2 and
    # there is no longer a prediction to compare against. Saying nothing is the
    # only honest option; failing a check that was never valid there is not.
    _ah_rq = _call("ac_self_heating", dict(_sh, frequency_hz=1.0e9,
                                          roughness_rq_um=1.0), 1850).get("value") or {}
    _ah_rqc = _ah_rq.get("checks") or {}
    check("1 um Rq drops the exponent to 0.361116 (not 0.5) and the module sets "
          "beta_expectation to null with a note instead of failing a comparison "
          "that cannot apply once the roughness factor itself moves with rho",
          abs((_ah_rq.get("dlnr_dlnrho") or 0) - 0.361116) < 1e-6
          and _ah_rqc.get("beta_expectation") is None
          and _ah_rqc.get("beta_deviation") is None
          and "roughness" in (_ah_rqc.get("beta_expectation_note") or ""),
          "exponent %s, expectation %s" % (_ah_rq.get("dlnr_dlnrho"),
                                           _ah_rqc.get("beta_expectation")))

    # ---- 0.0.69: a pulse train is not one frequency. Each harmonic dissipates
    # I_k^2*R(f_k) in the SAME copper, so the POWERS add - and the DC term sees
    # the whole cross-section while the top harmonic sees a skin. One harmonic
    # must still be exactly the one-frequency answer, which is the check that
    # says the new branch is the same arithmetic and not a re-derivation.
    _h1 = _call("ac_self_heating", {"shape": "sheet", "length_mm": 20.0,
                                   "width_mm": 1.0, "thickness_um": 35.0,
                                   "frequency_hz": 1.0e6, "current_a": 3.0,
                                   "rth_c_per_w": 20.0}, 1851).get("value") or {}
    _h1s = _call("ac_self_heating", {"shape": "sheet", "length_mm": 20.0,
                                    "width_mm": 1.0, "thickness_um": 35.0,
                                    "harmonics": [{"frequency_hz": 1.0e6,
                                                   "current_a": 3.0}],
                                    "rth_c_per_w": 20.0}, 1852).get("value") or {}
    check("one harmonic IS the one-frequency answer: dT 1.83292 K, P 0.0916456 W "
          "and dlnR/dlnrho 0.986895 come out bit for bit identical, because the "
          "sum uses the same (I^2*Rth)*R grouping and takes the exponent straight "
          "from _beta_at when there is one term",
          _h1.get("delta_t_c") == _h1s.get("delta_t_c")
          and _h1.get("power_w") == _h1s.get("power_w")
          and _h1.get("dlnr_dlnrho") == _h1s.get("dlnr_dlnrho")
          and abs((_h1s.get("delta_t_c") or 0) - 1.83292) < 1e-5,
          "dT %s vs %s, P %s vs %s"
          % (_h1.get("delta_t_c"), _h1s.get("delta_t_c"),
             _h1.get("power_w"), _h1s.get("power_w")))

    # A buck's ripple on a 20 mm trace: 1 A DC carrying 84 % of the power plus
    # four harmonics. Both simplifications are near the truth here, which is the
    # control that keeps the two checks below from being "always large".
    _buck = _call("ac_self_heating", {
        "shape": "sheet", "length_mm": 20.0, "width_mm": 1.0,
        "thickness_um": 35.0, "rth_c_per_w": 60.0, "dc_current_a": 1.0,
        "harmonics": [{"frequency_hz": 5.0e5, "current_a": 0.4},
                      {"frequency_hz": 1.5e6, "current_a": 0.13},
                      {"frequency_hz": 2.5e6, "current_a": 0.07},
                      {"frequency_hz": 3.5e6, "current_a": 0.04}]}, 1853)
    _bv = _buck.get("value") or {}
    _bc = _bv.get("checks") or {}
    _bdc = [t for t in (_bv.get("terms") or []) if t.get("kind") == "dc"]
    check("a buck ripple (1 A DC + 4 harmonics, 5 terms) answers 25.7157 C from "
          "P = 0.0119286 W at 1.08784 A RMS; the DC term carries 84.44 % of the "
          "power and sees R/R_dc = 1 exactly, while the 3.5 MHz term sees 1.07919",
          _buck.get("status") == "ok"
          and abs((_bv.get("t_final_c") or 0) - 25.7157) < 1e-3
          and abs((_bv.get("power_w") or 0) - 0.0119286) < 1e-8
          and abs((_bv.get("rms_current_a") or 0) - 1.08784) < 1e-4
          and len(_bv.get("terms") or []) == 5
          and len(_bdc) == 1 and _bdc[0].get("r_over_rdc_hot") == 1.0
          and abs((_bdc[0].get("power_share") or 0) - 0.8444) < 1e-4,
          "T %s C, P %s W, terms %s"
          % (_bv.get("t_final_c"), _bv.get("power_w"),
             len(_bv.get("terms") or [])))

    # The same current all above 1 GHz: the exponent becomes exactly 1/2, and
    # both simplifications are now badly wrong - 95 % for the DC model and
    # 10 % LOW for one frequency, because the 2 GHz term is charged 1 GHz's R.
    _hf = _call("ac_self_heating", {
        "shape": "sheet", "length_mm": 20.0, "width_mm": 1.0,
        "thickness_um": 35.0, "rth_c_per_w": 40.0,
        "harmonics": [{"frequency_hz": 1.0e9, "current_a": 1.0},
                      {"frequency_hz": 2.0e9, "current_a": 0.6}]}, 1854)
    _hf = _hf.get("value") or {}
    _hfc = _hf.get("checks") or {}
    check("an all-skin spectrum (1 GHz 1 A + 2 GHz 0.6 A) gives dlnR/dlnrho = 0.5 "
          "with beta_deviation 8e-11 against the power-weighted expectation, and "
          "the two simpler models are 94.66 % off (all DC, and it UNDERSTATES) "
          "and 10.05 % off (one frequency, also UNDERSTATES)",
          abs((_hf.get("dlnr_dlnrho") or 0) - 0.5) < 1e-9
          and _hfc.get("beta_expectation") == 0.5
          and abs((_hfc.get("beta_deviation") or 1) - 0.0) < 1e-6
          and abs((_hfc.get("all_dc_model_rel_diff") or 0) - 0.9466) < 5e-4
          and abs((_hfc.get("single_frequency_model_rel_diff") or 0) - 0.1005) < 5e-4
          and _hfc.get("single_frequency_model_direction") == "under",
          "exponent %s, dev %s, dc %s, one-f %s (%s)"
          % (_hf.get("dlnr_dlnrho"), _hfc.get("beta_deviation"),
             _hfc.get("all_dc_model_rel_diff"),
             _hfc.get("single_frequency_model_rel_diff"),
             _hfc.get("single_frequency_model_direction")))

    # The negative control for the whole branch: 9 A of DC and 9 A of 100 Hz
    # current, EACH alone below the runaway threshold (gain 0.6272, settles at
    # 436.5 K), together above it. Anything that looked at the largest term, or
    # at the RMS current at one frequency, would call this safe.
    _nar = {"shape": "sheet", "length_mm": 20.0, "width_mm": 0.1,
            "thickness_um": 35.0, "rth_c_per_w": 20.0}
    _one9 = _call("ac_self_heating", dict(_nar, frequency_hz=100.0,
                                         current_a=9.0), 1855)
    _both9 = _call("ac_self_heating", dict(_nar, dc_current_a=9.0,
                                          harmonics=[{"frequency_hz": 100.0,
                                                      "current_a": 9.0}]), 1856)
    check("9 A DC plus 9 A at 100 Hz runs away (cold gain 1.2544) although EACH "
          "alone settles (gain 0.6272 at 436.506 K): the loop gain of a spectrum "
          "ADDS, and the message says g(dT) = dT - Rth*P(dT) instead of the "
          "one-frequency formula",
          abs((((_one9.get("value") or {}).get("delta_t_c")) or 0) - 436.506) < 1e-2
          and _both9.get("reason_code") == "THERMAL_RUNAWAY"
          and abs((_both9.get("runaway_gain_cold") or 0) - 1.2544) < 1e-4
          and "Rth*P(dT)" in (_both9.get("message") or ""),
          "alone dT %s, both %s gain %s"
          % (((_one9.get("value") or {}).get("delta_t_c")),
             _both9.get("reason_code"), _both9.get("runaway_gain_cold")))

    # A spectrum is refused where it would have to be guessed at, and the
    # exponent of the sum is an identity with the exponents of its parts - not a
    # fit, so any drift there is a bug in the measurement and not physics.
    _sp_both = _call("ac_self_heating", {"shape": "sheet", "length_mm": 20.0,
                                        "width_mm": 1.0, "thickness_um": 35.0,
                                        "rth_c_per_w": 20.0, "current_a": 1.0,
                                        "frequency_hz": 1.0e6,
                                        "harmonics": [{"frequency_hz": 1.0e6,
                                                       "current_a": 1.0}]}, 1857)
    _sp_dup = _call("ac_self_heating", {"shape": "sheet", "length_mm": 20.0,
                                       "width_mm": 1.0, "thickness_um": 35.0,
                                       "rth_c_per_w": 20.0,
                                       "harmonics": [{"frequency_hz": 1.0e6,
                                                      "current_a": 1.0},
                                                     {"frequency_hz": 1.0e6,
                                                      "current_a": 2.0}]}, 1858)
    _sp_dc = _call("ac_self_heating", {"shape": "sheet", "length_mm": 20.0,
                                      "width_mm": 1.0, "thickness_um": 35.0,
                                      "rth_c_per_w": 20.0,
                                      "dc_current_a": 3.0}, 1859)
    check("a spectrum refuses current_a AND harmonics together, two entries at "
          "one frequency (phasors, and this model has no phase) and a DC-only "
          "call; the measured exponent of the sum equals the power-weighted mean "
          "of its parts to 1e-9",
          _sp_both.get("reason_code") == "ARG_INVALID"
          and _sp_dup.get("reason_code") == "ARG_INVALID"
          and _sp_dc.get("reason_code") == "ARG_INVALID"
          and _hfc.get("dlnr_dlnrho") == _hfc.get("dlnP_dlnrho_weighted_check"),
          "%s / %s / %s, weighted %s vs %s"
          % (_sp_both.get("reason_code"), _sp_dup.get("reason_code"),
             _sp_dc.get("reason_code"), _hfc.get("dlnr_dlnrho"),
             _hfc.get("dlnP_dlnrho_weighted_check")))

    # ---- 0.0.70: copper is not one thickness. Give board_ir_drop a frequency
    # and every conductor is scaled by the ratio its OWN 1-D solve gives - a
    # 20 mm x 0.5 mm track plus the pads, 35 um, one face. The first check is the
    # one that matters most: the DC answer must not move by a single digit,
    # because a frequency that was never given cannot have changed anything.
    _ac_tmp = _tempfile.mkdtemp(prefix="pcbagent-ac-")
    _ac_path = os.path.join(_ac_tmp, "ac.kicad_pcb")
    with open(_ac_path, "w", encoding="utf-8", newline="\n") as _fh:
        _fh.write((
            '(kicad_pcb (version 20221018) (generator pcbnew)\n'
            '  (general (thickness 1.6))\n'
            '  (layers (0 "F.Cu" signal) (31 "B.Cu" signal)'
            ' (44 "Edge.Cuts" user))\n'
            '  (setup (stackup\n'
            '      (layer "F.Cu" (type "copper") (thickness 0.035))\n'
            '      (layer "dielectric 1" (type "core") (thickness 1.53))\n'
            '      (layer "B.Cu" (type "copper") (thickness 0.035))))\n'
            '  (net 0 "") (net 1 "VCC")\n'
            '  (gr_rect (start 0 0) (end 30 20) (layer "Edge.Cuts")'
            ' (width 0.1))\n'
            '  (footprint "T:A" (layer "F.Cu") (at 5 10)\n'
            '    (fp_text reference "U1" (at 0 -3) (layer "F.SilkS"))\n'
            '    (pad "1" smd rect (at 0 0) (size 0.5 0.5) (layers "F.Cu")'
            ' (net 1 "VCC")))\n'
            '  (footprint "T:B" (layer "F.Cu") (at 25 10)\n'
            '    (fp_text reference "U2" (at 0 -3) (layer "F.SilkS"))\n'
            '    (pad "1" smd rect (at 0 0) (size 0.5 0.5) (layers "F.Cu")'
            ' (net 1 "VCC")))\n'
            '  (segment (start 5 10) (end 25 10) (width 0.5) (layer "F.Cu")'
            ' (net 1) (tstamp 1)))\n'
        ))
    _ac_base = {"path": _ac_path, "net": "VCC", "source": "U1.1",
                "sinks": ["U2.1"], "current_a": 1.0}
    _ac_dc = _call("board_ir_drop", dict(_ac_base), 1860)
    _ac_dcv = _ac_dc.get("value") or {}
    _ac_none = _call("board_ir_drop", dict(_ac_base, frequency_hz=None), 1860)
    _ac_def = _call("board_ir_drop", dict(_ac_base, frequency_hz=None,
                                         faces="one", mu_r=1.0,
                                         roughness_rq_um=None), 1860)
    _ac_nv = _ac_none.get("value") or {}
    _ac_fv = _ac_def.get("value") or {}
    check("leaving frequency_hz out is DC to the digit: 19.702857 mV / 0.019702857 W"
          " come out identical whether the argument is absent, explicitly null, or"
          " handed the defaults (faces='one', mu_r=1.0, Rq null), the"
          " not_computed list stays the five-item DC list that still names the"
          " skin effect, and no ac_summary key appears at all - the shape of the"
          " DC answer is part of the DC answer",
          _ac_dc.get("status") == "ok"
          and _ac_dcv.get("max_drop_mv") == _ac_nv.get("max_drop_mv")
          == _ac_fv.get("max_drop_mv")
          and _ac_dcv.get("total_copper_loss_w")
          == _ac_nv.get("total_copper_loss_w")
          == _ac_fv.get("total_copper_loss_w")
          and abs((_ac_dcv.get("max_drop_mv") or 0) - 19.702857) < 1e-6
          and _ac_dc.get("not_computed") == _ac_none.get("not_computed")
          == _ac_def.get("not_computed")
          and "skin effect" in (_ac_dc.get("not_computed") or [])
          and "ac_summary" not in _ac_dcv and "ac_summary" not in _ac_nv,
          "drop %s / %s / %s mV" % (_ac_dcv.get("max_drop_mv"),
                                    _ac_nv.get("max_drop_mv"),
                                    _ac_fv.get("max_drop_mv")))

    _ac_v = _call("board_ir_drop", dict(_ac_base, frequency_hz=1.0e6),
                  1861).get("value") or {}
    _ac_s = (_ac_v.get("ac_summary") or {}).get("track") or {}
    check("1 MHz on 1 oz copper: 19.702857 -> 19.840259 mV (x1.006973727), skin "
          "depth 66.0828 um against a 35 um foil, and the ratio in ac_summary is "
          "the same number to nine places - the per-segment factor is the module "
          "skin.ac_sheet_resistance's own answer, not a second implementation "
          "that could drift",
          _ac_v.get("frequency_hz") == 1.0e6
          and abs((_ac_v.get("max_drop_mv") or 0) - 19.840259) < 1e-6
          and abs((_ac_v.get("ac_skin_depth_um") or 0) - 66.082849628) < 1e-6
          and _ac_s.get("segments") == 1
          and abs((_ac_s.get("ratio_max") or 0) - 1.006973727) < 1e-9
          and abs((_ac_s.get("sum_r_ac_ohm") or 0) - 0.019840259) < 1e-9,
          "drop %s mV, depth %s um, ratio %s"
          % (_ac_v.get("max_drop_mv"), _ac_v.get("ac_skin_depth_um"),
             _ac_s.get("ratio_max")))

    _ac_z = _call("board_ir_drop", dict(_ac_base, frequency_hz=0.0), 1862)
    check("frequency_hz = 0 is refused with ARG_RANGE, not silently treated as "
          "DC: a caller who typed 0 wanted a frequency answer and there is none "
          "at zero",
          _ac_z.get("status") == "error"
          and _ac_z.get("reason_code") == "ARG_RANGE"
          and "frequency_hz" in (_ac_z.get("message") or ""),
          str(_ac_z.get("reason_code")))

    _ac_t = _call("board_ir_drop", dict(_ac_base, frequency_hz=1.0e6,
                                        faces="three"), 1862)
    check("faces is refused unless it is 'one' or 'two': the factor of two is "
          "the whole reason the argument exists, so a typo must not fall back to "
          "one face",
          _ac_t.get("status") == "error"
          and _ac_t.get("reason_code") == "ARG_INVALID"
          and "factor of two" in (_ac_t.get("message") or ""),
          (str(_ac_t.get("reason_code")) + " / "
           + (_ac_t.get("message") or "")[:60]))

    _ac_o = _call("board_ir_drop", dict(_ac_base, faces="two"), 1862)
    check("faces without a frequency is refused instead of ignored: accepting it "
          "and answering the DC number would be lying by omission, and the "
          "message names frequency_hz so the fix is obvious",
          _ac_o.get("status") == "error"
          and _ac_o.get("reason_code") == "ARG_INVALID"
          and "frequency_hz" in (_ac_o.get("message") or ""),
          str(_ac_o.get("reason_code")))
    _shutil.rmtree(_ac_tmp, ignore_errors=True)

    # ---- 0.0.71: the flux does not have to be a sine. The Steinmetz triple is
    # the same one a sine would use; what changes is the SHAPE the period
    # integrates over. The first check is the zero-change one: a call that does
    # not name a waveform must still be the plain closed form, digit for digit.
    _mag = {"f_hz": 1e5, "b_peak_t": 0.1, "k": 100.0, "alpha": 1.4, "beta": 2.7}
    _ig_def = _call("magnetics_core_loss", dict(_mag), 1863).get("value") or {}
    _ig_sin = _call("magnetics_core_loss", dict(_mag, waveform="sine"),
                    1863).get("value") or {}
    check("waveform is not an argument until you use it: the default answer is"
          " 1995260 W/m^3 / 1995.262315 mW/cm^3 either way, the two value"
          " dictionaries are equal key for key, no iGSE block appears, and"
          " not_modelled still names the non-sinusoidal flux - the shape of the"
          " sine answer is part of the sine answer",
          _ig_def == _ig_sin
          and abs((_ig_def.get("P_v_w_m3") or 0) - 1995260.0) < 1e-6
          and abs((_ig_def.get("P_v_mw_cm3") or 0) - 1995.262315) < 1e-9
          and "igse" not in _ig_def and "waveform" not in _ig_def
          and any("non-sinusoidal" in n for n in (_ig_def.get("not_modelled") or [])),
          "P_v %s W/m^3" % _ig_def.get("P_v_w_m3"))

    _ig_tri = _call("magnetics_core_loss", dict(_mag, waveform="triangle"),
                    1864).get("value") or {}
    _ig_pw = _call("magnetics_core_loss",
                   {"f_hz": 1e5, "k": 100.0, "alpha": 1.4, "beta": 2.7,
                    "waveform": "piecewise",
                    "segments": [[0.2, 0.5], [-0.2, 0.5]]}, 1864).get("value") or {}
    _ig_ig = _ig_tri.get("igse") or {}
    check("a symmetric triangle is 1859840 W/m^3 - 0.932129 of what the sine"
          " formula would have said, so the plain Steinmetz number was an"
          " OVER-estimate here - the swing is 0.2 T peak-to-peak, k_i comes out"
          " 5.435667 from the sine-reduction identity (I_alpha 3.582087), the same"
          " triangle given as two piecewise rows lands on the same number, and the"
          " shortcut is printed next to the answer instead of replacing it",
          _ig_tri.get("waveform") == "triangle"
          and abs((_ig_tri.get("P_v_w_m3") or 0) - 1859840.0) < 1e-6
          and abs((_ig_tri.get("ratio_to_sine") or 0) - 0.932129) < 1e-9
          and abs((_ig_tri.get("P_v_sine_equiv_w_m3") or 0) - 1995260.0) < 1e-6
          and _ig_tri.get("sine_equiv_is") == "overestimate"
          and abs((_ig_tri.get("flux_swing_pp_t") or 0) - 0.2) < 1e-12
          and abs((_ig_ig.get("k_i") or 0) - 5.435667) < 1e-9
          and abs((_ig_ig.get("i_alpha") or 0) - 3.582087) < 1e-9
          and len(_ig_tri.get("segments_used") or []) == 2
          and abs(((_ig_tri.get("segments_used") or [{}])[0].get("dB_dt_t_s") or 0)
                  - 4e4) < 1e-6
          and abs((_ig_pw.get("P_v_w_m3") or 0) - (_ig_tri.get("P_v_w_m3") or -1))
          < 1e-6 * (_ig_tri.get("P_v_w_m3") or 1),
          "ratio %s, k_i %s, piecewise %s"
          % (_ig_tri.get("ratio_to_sine"), _ig_ig.get("k_i"),
             _ig_pw.get("P_v_w_m3")))

    _ig_d3 = _call("magnetics_core_loss", dict(_mag, waveform="triangle",
                                               duty=0.3), 1865).get("value") or {}
    _ig_am = _call("magnetics_core_loss",
                   {"f_hz": 2e5, "b_peak_t": 0.05, "k": 100.0, "alpha": 1.4,
                    "beta": 2.7, "waveform": "triangle"}, 1865).get("value") or {}
    check("a 30% duty triangle is 1953560 W/m^3 (ratio 0.979098) - an unbalanced"
          " ramp is steeper on one side and therefore worse - while halving the"
          " amplitude and doubling the frequency leaves the RATIO untouched at"
          " 0.932129, which is what makes it a shape correction rather than a"
          " second fit",
          abs((_ig_d3.get("P_v_w_m3") or 0) - 1953560.0) < 1e-6
          and abs((_ig_d3.get("ratio_to_sine") or 0) - 0.979098) < 1e-9
          and abs((_ig_d3.get("duty") or 0) - 0.3) < 1e-12
          and abs((_ig_am.get("ratio_to_sine") or 0) - 0.932129) < 1e-9,
          "duty .3 ratio %s, other-point ratio %s"
          % (_ig_d3.get("ratio_to_sine"), _ig_am.get("ratio_to_sine")))

    _ig_bad = [
        ("unknown shape", dict(_mag, waveform="sawtooth"), "ARG_INVALID"),
        ("duty on a sine", dict(_mag, duty=0.5), "ARG_INVALID"),
        ("segments on a triangle", dict(_mag, waveform="triangle",
                                        segments=[[0.2, 0.5], [-0.2, 0.5]]),
         "ARG_INVALID"),
        ("duty = 0", dict(_mag, waveform="triangle", duty=0.0), "ARG_RANGE"),
        ("time does not sum to 1", {"f_hz": 1e5, "k": 100.0, "alpha": 1.4,
                                    "beta": 2.7, "waveform": "piecewise",
                                    "segments": [[0.1, 0.4], [-0.1, 0.4]]},
         "ARG_RANGE"),
        ("trajectory does not close", {"f_hz": 1e5, "k": 100.0, "alpha": 1.4,
                                       "beta": 2.7, "waveform": "piecewise",
                                       "segments": [[0.1, 0.5], [-0.05, 0.5]]},
         "ARG_RANGE"),
    ]
    _ig_got = [(label, (_call("magnetics_core_loss", args, 1866) or {}).get(
        "reason_code")) for label, args, _want in _ig_bad]
    check("every shape argument is refused when it cannot be honoured, rather"
          " than ignored: an unknown waveform, a duty handed to a sine, segments"
          " handed to a triangle, duty = 0, dt_fractions that do not add up, and a"
          " trajectory that never returns to its starting flux",
          all(got == want for (_l, _a, want), (_l2, got) in zip(_ig_bad, _ig_got)),
          "; ".join("%s=%s" % (l, g) for l, g in _ig_got))

    _sc_req = {"inductance_h": 100e-6, "i_peak_a": 3.0, "freq_hz": 1e5,
               "b_ac_t": 0.1, "loss_exponents": [1.3, 2.5], "material": "N87"}
    _sc_sin = _call("magnetics_select_core", dict(_sc_req), 1867).get("value") or {}
    _sc_tri = _call("magnetics_select_core", dict(_sc_req,
                                                 flux_waveform="triangle"),
                    1867).get("value") or {}
    _sc_noexp_args = {k: v for k, v in _sc_req.items() if k != "loss_exponents"}
    _sc_noexp_args["flux_waveform"] = "triangle"
    _sc_noexp = _call("magnetics_select_core", _sc_noexp_args,
                      1867).get("value") or {}
    _sc_a = (_sc_sin.get("candidates") or [{}])[0]
    _sc_b = (_sc_tri.get("candidates") or [{}])[0]
    _sc_c = (_sc_noexp.get("candidates") or [{}])[0]
    check("select_core can judge the core loss on the shape an inductor actually"
          " carries: the triangle scales core_loss_w by the iGSE ratio and leaves"
          " the turns, the peak flux and the gap alone, and with no exponents to"
          " take alpha from it refuses that one number (naming loss_exponents)"
          " instead of quoting the sine one as if it were the answer",
          "flux_waveform" not in _sc_sin
          and (_sc_tri.get("flux_waveform") or {}).get("waveform") == "triangle"
          and _sc_a.get("turns") == _sc_b.get("turns")
          and _sc_a.get("b_peak_t") == _sc_b.get("b_peak_t")
          and _sc_a.get("core_loss_w") is not None
          and abs((_sc_b.get("core_loss_w") or 0)
                  / (_sc_a.get("core_loss_w") or 1)
                  - 0.950643) < 1e-5
          and "iGSE shape ratio" in (_sc_b.get("core_loss_basis") or "")
          and _sc_c.get("core_loss_w") is None
          and "loss_exponents" in (_sc_c.get("core_loss_basis") or ""),
          "%s -> %s W (sine %s W), no-exponent -> %s"
          % (_sc_b.get("part"), _sc_b.get("core_loss_w"), _sc_a.get("core_loss_w"),
             _sc_c.get("core_loss_w")))

    # --- the field that leaks out of the gap (0.0.72). Dowell's 1-D stack
    # cannot see it: the two face the model is built on, five independent
    # checks of the field itself, and one number that must not move.
    _fr_args = {"freq_hz": 1e5, "gap_mm": 1.0, "leg_width_mm": 10.0,
                "leg_depth_mm": 10.0, "layers": 4, "thickness_mm": 0.05,
                "winding_height_mm": 20.0, "standoff_mm": 0.2,
                "ampere_turns": 100.0, "resistivity_ohm_m": 1.724e-8}
    _fr = _call("magnetics_fringing_loss", dict(_fr_args), 1868)
    _fr_v = _fr.get("value") or {}

    def _fr_tot(**kw):
        a = dict(_fr_args)
        a.update(kw)
        return ((_call("magnetics_fringing_loss", a, 1868).get("value") or {})
                .get("total_loss_w"))

    check("fringing: a 10 mm leg, 1 mm gap, 4 x 0.05 mm foil at 100 kHz and 100 "
          "A-t loses 0.0653932 W in the foil, 3.58 % of its own DC loss - with "
          "93.68 % of the MMF dropped across the gap and the pole density 6.75 % "
          "above the uniform 1/B=mu0 value because the field also spreads",
          _fr.get("status") == "ok"
          and abs((_fr_v.get("total_loss_w") or 0) - 0.0653932) < 1e-7
          and abs((_fr_v.get("gap_mmf_fraction") or 0) - 0.936756) < 1e-6
          and abs((_fr_v.get("pole_concentration") or 0) - 1.067514) < 1e-6
          and abs((_fr_v.get("loss_over_dc") or 0) - 0.035784) < 1e-6
          and abs((_fr_v.get("radial_over_axial_at_0p2g") or 0) - 0.3213) < 1e-4
          and len(_fr_v.get("layers") or []) == 4,
          "P %s W, gap share %s, q ratio %s, P/Pdc %s"
          % (_fr_v.get("total_loss_w"), _fr_v.get("gap_mmf_fraction"),
             _fr_v.get("pole_concentration"), _fr_v.get("loss_over_dc")))

    _fr_20k = _fr_tot(freq_hz=2e4)
    _fr_std = [_fr_tot(standoff_mm=x) for x in (0.0, 0.2, 0.5, 1.0, 2.0)]
    _fr_h = [_fr_tot(winding_height_mm=x) for x in (6.0, 40.0)]
    check("fringing obeys three things it was not fitted to: a foil far thinner "
          "than delta scales as f^2 (20 kHz / 100 kHz = 1/25), moving the foil "
          "away from the gap kills the loss monotonically (2 mm is under a "
          "quarter of 0 mm), and the winding height barely matters (6 mm vs "
          "40 mm differ under 3 %) because the leakage falls off as 1/z^4",
          _fr_20k is not None
          and abs(_fr_20k / 0.0653932 - 0.04) < 4e-4
          and all(_fr_std[i] > _fr_std[i + 1] for i in range(4))
          and _fr_std[4] / _fr_std[0] < 0.25
          and _fr_h[1] / _fr_h[0] - 1.0 < 0.03,
          "20k/100k %s, standoff 0->2 mm %s -> %s, h 6->40 mm %s -> %s"
          % (_fr_20k / 0.0653932 if _fr_20k else None, _fr_std[0], _fr_std[4],
             _fr_h[0], _fr_h[1]))

    _wa = _call("magnetics_winding_ac",
                {"freq_hz": 1e5, "layers": 4, "shape": "foil",
                 "thickness_mm": 0.05, "turns": 4, "mean_length_mm": 100.0,
                 "resistivity_ohm_m": 1.724e-8}, 1869).get("value") or {}
    check("the Dowell stack still answers exactly what it answered in 0.0.71 "
          "(fr 1.00575, own field 1.00029, proximity 0.00546): the new tool adds "
          "a term beside it instead of quietly changing the old one",
          abs((_wa.get("fr") or 0) - 1.00575) < 1e-5
          and abs((_wa.get("fr_own_field") or 0) - 1.00029) < 1e-5
          and abs((_wa.get("fr_proximity") or 0) - 0.00546) < 1e-5,
          "fr %s = %s + %s" % (_wa.get("fr"), _wa.get("fr_own_field"),
                               _wa.get("fr_proximity")))

    _fr_bad = [
        ("current and A-t both", dict(_fr_args, current_rms_a=100.0),
         "ARG_MISSING"),
        ("neither current nor A-t", {k: v for k, v in _fr_args.items()
                                     if k != "ampere_turns"}, "ARG_MISSING"),
        ("rho and temperature", dict(_fr_args, temperature_c=20.0),
         "ARG_INVALID"),
        ("201 layers", dict(_fr_args, layers=201), "ARG_RANGE"),
        ("negative standoff", dict(_fr_args, standoff_mm=-0.1), "ARG_RANGE"),
    ]
    _fr_got = [(lab, (_call("magnetics_fringing_loss", a, 1870) or {})
               .get("reason_code")) for lab, a, _w in _fr_bad]
    _fr_wide = _call("magnetics_fringing_loss",
                     dict(_fr_args, gap_mm=8.0), 1870).get("value") or {}
    check("fringing refuses instead of guessing: one of current / ampere-turns "
          "and not both, resistivity and temperature are not both, and it warns "
          "when the gap is wider than half the leg (the 2-D model is then "
          "describing a gap, not a slot)",
          all(got == want for (_l, _a, want), (_l2, got)
              in zip(_fr_bad, _fr_got))
          and any("half the leg width" in w
                  for w in (_fr_wide.get("warnings") or [])),
          "; ".join("%s=%s" % (l, g) for l, g in _fr_got))

    _si_ref_w_m3 = ((_call("magnetics_core_loss",
                           {"f_hz": 100e3, "b_peak_t": 0.1, "k": 1.3,
                            "alpha": 1.5, "beta": 2.5, "units": "si"}, 1876)
                     .get("value") or {}).get("P_v_w_m3") or 0.0)
    _cap = _tools.TOOLS["calc_capabilities"]["handler"]({})
    check("fringing is reachable from the capability list as well as the tool "
          "table (178 tools now), so an agent that asks what can be computed is "
          "told about it",
          len(_tools.TOOLS) == 178
          and "magnetics_fringing_loss" in (_cap.get("available") or [])
          and "fringing" in (_tools.TOOLS["magnetics_fringing_loss"]
                             ["description"].lower()),
          "%d tools, listed %s" % (
              len(_tools.TOOLS),
              "magnetics_fringing_loss" in (_cap.get("available") or [])))

    # ---- 0.0.74: the vendor's own printed loss fit -------------------------
    _lf_base = {"material": "Kool Mu", "freq_hz": 100e3, "permeability": 60,
                "shape": "toroid"}
    _lf_want = [(0.014, 11.0), (0.055, 168.0), (0.092, 466.0)]
    _lf_got = [(b, (_call("magnetics_material_core_loss",
                          dict(_lf_base, b_peak_t=b), 1873).get("value") or {})
               .get("P_v_mw_cm3")) for b, _w in _lf_want]
    check("the powder-core catalog's own three worked examples come back to the "
          "digits it prints (11.0 / 168 / 466 mW/cm3 at Bpk 0.014 / 0.055 / 0.092 T, "
          "100 kHz, Kool Mu 60u toroid) - which also pins WHICH printed exponent is "
          "the frequency one",
          all(g is not None and abs(g - w) <= 5e-3 * w
              for (_b, w), (_b2, g) in zip(_lf_want, _lf_got)),
          "; ".join("%g T -> %s" % (b, g) for b, g in _lf_got))

    _lf_f = [_call("magnetics_material_core_loss",
                   dict(_lf_base, b_peak_t=0.05, freq_hz=f), 1874).get("value") or {}
             for f in (10e3, 100e3)]
    _lf_dec = (_lf_f[1].get("P_v_mw_cm3") or 0.0) / (_lf_f[0].get("P_v_mw_cm3") or 1.0)
    # (parenthesised on purpose: `x or {}.get(k)` binds .get to the literal {})
    _lf_hi = ((_call("magnetics_material_core_loss",
                     dict(_lf_base, b_peak_t=0.10), 1875).get("value") or {})
              .get("P_v_mw_cm3") or 0.0)
    _lf_amp = _lf_hi / ((_call("magnetics_material_core_loss",
                               dict(_lf_base, b_peak_t=0.05), 1875)
                         .get("value") or {"P_v_mw_cm3": 1.0})["P_v_mw_cm3"])
    check("the printed triple is read the right way round: c (1.541) is the "
          "FREQUENCY exponent and b (1.988) the flux one, so a decade of frequency "
          "multiplies the loss by 10^1.541 and a doubled flux amplitude by 2^1.988 - "
          "swapping them would land on 10^1.988 / 2^1.541, a 2.8x error that still "
          "looks like an answer",
          abs(_lf_dec / 10.0 ** 1.541 - 1.0) < 1e-6
          and abs(_lf_amp / 2.0 ** 1.988 - 1.0) < 1e-6
          and abs(_lf_dec / 10.0 ** 1.988 - 1.0) > 0.5,
          "decade ratio %.6f (10^1.541 = %.6f), 2x flux ratio %.6f"
          % (_lf_dec, 10.0 ** 1.541, _lf_amp))

    _lf_si = (_call("magnetics_core_loss",
                    {"f_hz": 100e3, "b_peak_t": 0.05, "k": 1.0553675249259014,
                     "alpha": 1.541, "beta": 1.988, "units": "si"}, 1876)
              .get("value") or {})
    _lf_cmp = (_call("magnetics_material_core_loss",
                     dict(_lf_base, b_peak_t=0.05), 1876).get("value") or {})
    check("the mW/cm3 + tesla + kHz convention is converted by arithmetic, not by "
          "hand: the same row through the si path (k = a*10^(3-3c) = 1.0553675) "
          "gives the identical W/m3, and the unchanged si answer for an unrelated "
          "triple is still exactly 130000 W/m3",
          abs((_lf_cmp.get("P_v_w_m3") or 0.0)
              / max(_lf_si.get("P_v_w_m3") or 1e-30, 1e-30) - 1.0) < 1e-9
          and abs(130000.0 / _si_ref_w_m3 - 1.0) < 1e-12,
          "%r vs %r" % (_lf_cmp.get("P_v_w_m3"), _lf_si.get("P_v_w_m3")))

    _lf_bad = [
        ("a ferrite has no vendor fit in the table",
         {"material": "N87", "freq_hz": 100e3, "b_peak_t": 0.05}, "FIT_NOT_RESOLVED"),
        ("the question is narrower than the fit (no permeability, no shape)",
         {"material": "Kool Mu", "freq_hz": 100e3, "b_peak_t": 0.05},
         "FIT_NOT_RESOLVED"),
        ("a shape the catalog prints no fit for",
         dict(_lf_base, b_peak_t=0.05, shape="pot"), "FIT_NOT_RESOLVED"),
        ("exactly ON the 100 kHz boundary the Ultra fit is split at (neither side "
         "claims the boundary)",
         {"material": "Kool Mu", "freq_hz": 100e3, "b_peak_t": 0.05,
          "permeability": 26, "shape": "toroid", "variant": "Ultra"},
         "FIT_NOT_RESOLVED"),
        ("a material name that is not in the table",
         {"material": "3F4", "freq_hz": 100e3, "b_peak_t": 0.05}, "NOT_FOUND"),
        ("a negative frequency",
         dict(_lf_base, b_peak_t=0.05, freq_hz=-1.0), "ARG_RANGE"),
    ]
    _lf_bad_got = [(lab, (_call("magnetics_material_core_loss", a, 1877) or {})
                   .get("reason_code")) for lab, a, _w in _lf_bad]
    check("the lookup refuses instead of picking: six under-specified or impossible "
          "requests each come back with their own reason code, and none of them "
          "silently lands on a neighbouring row",
          all(got == want for (_l, _a, want), (_l2, got)
              in zip(_lf_bad, _lf_bad_got)),
          "; ".join("%s" % g for _l, g in _lf_bad_got))

    # ---- 0.0.75: XFlux / Edge, and the High DC Bias lines ------------------
    _x_args = {
        "xflux": {"material": "XFlux", "freq_hz": 50e3, "b_peak_t": 0.1,
                  "permeability": 60, "shape": "toroid"},
        "edge": {"material": "Edge", "freq_hz": 50e3, "b_peak_t": 0.1,
                 "permeability": 60, "shape": "toroid"},
        "xflux_hdb": {"material": "XFlux", "freq_hz": 100e3, "b_peak_t": 0.1,
                      "permeability": 60, "shape": "toroid",
                      "variant": "High DC Bias"},
        "edge_hdb": {"material": "Edge", "freq_hz": 100e3, "b_peak_t": 0.1,
                     "permeability": 60, "shape": "toroid",
                     "variant": "High DC Bias"},
    }
    _x_val = {}
    for _k in sorted(_x_args):
        _x_val[_k] = (_call("magnetics_material_core_loss", dict(_x_args[_k]),
                            1879).get("value") or {})
    # the catalog's own summary table (printed p.10) gives the 60u toroid loss at
    # 50 kHz / 1000 G for four materials; two of them are below. If Bpk were read
    # as peak-to-peak these would be ~4x low, which is what makes this a check on
    # the convention and not just on the arithmetic.
    _x_pr = {"xflux": 575.0, "edge": 150.0}
    check("the 0.0.75 materials reproduce the catalog's OWN summary table (printed "
          "p.10, 60u toroid, 50 kHz / 1000 gauss): XFlux 575 and Edge 150 mW/cm3 - an "
          "independent check on the transcription and on Bpk being HALF the swing, "
          "since a peak-to-peak reading lands 4x low",
          all(abs((_x_val[_k].get("P_v_mw_cm3") or 0.0) / _w - 1.0) < 5e-3
              for _k, _w in _x_pr.items()),
          ", ".join("%s %.2f vs printed %g" % (_k, _x_val[_k].get("P_v_mw_cm3") or 0.0,
                                               _w) for _k, _w in sorted(_x_pr.items())))

    _x_base = (_call("magnetics_material_core_loss",
                     dict(_x_args["xflux_hdb"], variant=None), 1880)
               .get("value") or {})
    check("the High DC Bias product lines are their OWN printed rows, not an "
          "interpolation of the base row: at the same 60u toroid / 100 kHz / 0.1 T "
          "the High DC Bias XFlux row is 15 % above the base one and carries page 12 "
          "(where it is printed) rather than page 109",
          (_x_val["xflux_hdb"].get("P_v_mw_cm3") or 0.0)
          / max(_x_base.get("P_v_mw_cm3") or 1e-30, 1e-30) > 1.10
          and _x_val["xflux_hdb"].get("fit_printed_page") == 12
          and _x_val["edge_hdb"].get("fit_printed_page") == 14
          and (_x_val["edge_hdb"].get("P_v_mw_cm3") or 0.0) > 400.0,
          "HDB XFlux %.1f vs base %.1f mW/cm3, pages %s / %s"
          % (_x_val["xflux_hdb"].get("P_v_mw_cm3") or 0.0,
             _x_base.get("P_v_mw_cm3") or 0.0,
             _x_val["xflux_hdb"].get("fit_printed_page"),
             _x_val["edge_hdb"].get("fit_printed_page")))

    _x_mat = {}
    for _n in ("XFlux", "Edge", "MPP"):
        _r = _tools.TOOLS["magnetics_data"]["handler"]({"kind": "materials",
                                                        "name": _n})
        _x_mat[_n] = ((_r.get("value") or {}).get("rows") or [{}])[0]
    _x_all = _tools.TOOLS["magnetics_data"]["handler"]({"kind": "materials"})
    check("the material constants added in 0.0.75 are the vendor's printed ones: "
          "XFlux 700 C / 6.9 g/cm3 / 1.6 T and Edge 500 C / 7.8 g/cm3 / 1.5 T "
          "(printed p.16 and p.10), the table is 14 materials, 93 printed loss rows - "
          "and MPP's move from 8700 to 7800 kg/m3 is recorded as a change with the "
          "alloy density kept beside it instead of being swapped out",
          _x_mat["XFlux"].get("tc_min_c") == 700
          and _x_mat["XFlux"].get("density_kg_m3") == 6900
          and _x_mat["XFlux"].get("bsat_mt_25c") == 1600
          and _x_mat["Edge"].get("tc_min_c") == 500
          and _x_mat["Edge"].get("density_kg_m3") == 7800
          and _x_mat["Edge"].get("bsat_mt_25c") == 1500
          and _x_mat["MPP"].get("density_kg_m3") == 7800
          and _x_mat["MPP"].get("density_alloy_kg_m3") == 8700
          and "CHANGED" in (_x_mat["MPP"].get("density_note") or "")
          and (_x_all.get("value") or {}).get("count") == 14,
          "%d materials, XFlux %s C %s kg/m3, MPP %s/%s"
          % ((_x_all.get("value") or {}).get("count") or 0,
             _x_mat["XFlux"].get("tc_min_c"), _x_mat["XFlux"].get("density_kg_m3"),
             _x_mat["MPP"].get("density_kg_m3"),
             _x_mat["MPP"].get("density_alloy_kg_m3")))

    _lf_wide = _call("magnetics_material_core_loss",
                     {"material": "High Flux", "freq_hz": 3e3, "b_peak_t": 0.6,
                      "permeability": 26, "shape": "toroid"}, 1878).get("value") or {}
    check("a frequency below the vendor's own chart span and a flux above the top of "
          "those charts are both flagged (the fit is a curve fit of charts, and "
          "outside them it is extrapolation)",
          len(_lf_wide.get("warnings") or []) == 2
          and any("outside the 5-2000 kHz" in w for w in _lf_wide["warnings"])
          and any("above the 0.5 T" in w for w in _lf_wide["warnings"]),
          "%d warnings" % len(_lf_wide.get("warnings") or []))

    check("the vendor fit is reachable from the capability list as well as the tool "
          "table (178 tools now)",
          len(_tools.TOOLS) == 178
          and "magnetics_material_core_loss" in (_cap.get("available") or [])
          and "vendor" in (_tools.TOOLS["magnetics_material_core_loss"]
                           ["description"].lower()),
          "%d tools" % len(_tools.TOOLS))

    # ---- 0.0.76: the powder-core toroid geometry ---------------------------
    _pw = _call("magnetics_data", {"kind": "powder_cores"}, 1881).get("value") or {}
    _pw_sizes = _pw.get("sizes") or []
    _pw_rows = sum(len(b.get("rows") or []) for b in _pw_sizes)
    _pw_parts = (_call("magnetics_data", {"kind": "powder_parts", "limit": 1},
                       1882).get("value") or {})
    check("the powder table is the printed grid and nothing else: 43 toroid sizes, "
          "500 permeability rows over nine material columns, 1923 purchasable part "
          "numbers, five vendor printing anomalies kept verbatim - all readable "
          "through magnetics_data",
          _pw.get("count") == 43 and _pw_rows == 500
          and _pw_parts.get("total_matching") == 1923
          and len(_pw.get("printing_anomalies") or []) == 5,
          "%s sizes, %d rows, %s parts, %d anomalies"
          % (_pw.get("count"), _pw_rows, _pw_parts.get("total_matching"),
             len(_pw.get("printing_anomalies") or [])))

    _pbad = []
    for _b in _pw_sizes:
        _g = _b.get("geometry") or {}
        if _g.get("ae_mm2") and _g.get("le_mm") and _g.get("ve_mm3"):
            _r = _g["ve_mm3"] / (_g["ae_mm2"] * _g["le_mm"])
            if abs(_r - 1.0) > 0.02:
                _pbad.append((_b.get("printed_page"), round(_r, 4)))
    check("the printed geometry checks itself: Ve = Ae x Le on 42 of the 43 sizes, "
          "and the single page that disagrees is printed page 193, where the vendor's "
          "own 81,500 mm3 is 6.5 % off its own 492 x 177.2 - kept as printed, and "
          "named in printing_anomalies rather than smoothed",
          _pbad == [(193, 0.9348)], str(_pbad))

    _pdef = _call("magnetics_select_core", {"inductance_h": 100e-6,
                                           "i_peak_a": 5.0, "limit": 1}, 1883)
    _psel = _call("magnetics_select_core",
                  {"inductance_h": 100e-6, "i_peak_a": 5.0,
                   "include_powder_cores": True, "material": "MPP",
                   "permeability": 60, "freq_hz": 100e3, "b_ac_t": 0.05,
                   "limit": 1}, 1884)
    _pdef_v = _pdef.get("value") or {}
    _psel_v = _psel.get("value") or {}
    _psel_c = ((_psel_v.get("candidates") or [{}])[0])
    check("select_core can now pick a toroid, and the default path is untouched: "
          "without include_powder_cores the pool is still the 16 solid cores with no "
          "core_pool key; with it, 100 uH / 5 A on MPP 60u answers 0055141A2 at 601 "
          "turns, 0.64 T of the 0.8 T printed for that column, no gap length (the gap "
          "is distributed) and 1.1 mW of the vendor's own toroid fit at 100 kHz",
          _pdef_v.get("table_rows") == 16
          and _pdef_v.get("feasible_count") == 9
          and "core_pool" not in _pdef_v
          and _psel_v.get("core_pool", {}).get("parts") == 41
          and _psel_c.get("part") == "0055141A2"
          and _psel_c.get("turns") == 601
          and _psel_c.get("b_limit_t") == 0.64
          and _psel_c.get("saturation") == "ok"
          and _psel_c.get("gap_mm") is None
          and abs((_psel_c.get("core_loss_w") or 0.0) - 0.0011) < 1e-9
          and "vendor printed fit" in (_psel_c.get("core_loss_basis") or ""),
          "default rows %s / feasible %s; powder %s turns %s B %s loss %s"
          % (_pdef_v.get("table_rows"), _pdef_v.get("feasible_count"),
             _psel_c.get("part"), _psel_c.get("turns"),
             _psel_c.get("b_limit_t"), _psel_c.get("core_loss_w")))

    # ---- 0.0.77: the vendor's DC-bias fit (% mu_i = 1/(a + b*H^c)) ---------
    _dc_disp = {"\u00b5": "u", "\u03bc": "u", "\u0192": "f"}

    def _dcf(text):
        folded = "".join(_dc_disp.get(ch, ch) for ch in str(text or ""))
        return "".join(ch for ch in folded.lower() if ch.isalnum())

    _dc = _call("magnetics_data", {"kind": "dc_bias_fit"}, 1885).get("value") or {}
    _dc_rows = _dc.get("rows") or []
    _dc_groups = sorted({(r.get("material"), r.get("shape_family")) for r in _dc_rows})
    _dc_pages = sorted({r.get("printed_page") for r in _dc_rows})
    check("the DC-bias fit table is the printed one and is refused if it is not: 29 "
          "material x core-shape groups, 125 rows, printed pages 63-65, every row "
          "carrying a = 0.01 - the constant that pins the fit to 100 % at zero bias, "
          "so a table whose columns slipped is rejected instead of answered from",
          _dc.get("count") == 125 and _dc.get("total_matching") == 125
          and len(_dc_groups) == 29 and _dc_pages == [63, 64, 65]
          and all(r.get("a") == 0.01 and (r.get("b") or 0) > 0 and (r.get("c") or 0) > 0
                  for r in _dc_rows),
          "%d rows, %d groups, pages %s"
          % (len(_dc_rows), len(_dc_groups), _dc_pages))

    _dc_fit = {(_dcf(r.get("material")), _dcf(r.get("shape_family")),
                int(r.get("permeability"))): (r.get("a"), r.get("b"), r.get("c"))
               for r in _dc_rows}
    _dc_p10 = ((_pw.get("material_comparison_printed_page_10") or {}).get("rows") or {})
    _dc_err = []
    for _name, _entry in _dc_p10.items():
        _abc = _dc_fit.get((_dcf(_name), _dcf("Toroids"), 60))
        if not _abc:
            continue
        for _pct, _key in ((80.0, "perm_vs_dc_bias_oe_at_80pct_mu_i"),
                           (50.0, "perm_vs_dc_bias_oe_at_50pct_mu_i")):
            _printed = _entry.get(_key)
            if not _printed:
                continue
            _hh = ((1.0 / _pct - _abc[0]) / _abc[1]) ** (1.0 / _abc[2])
            _dc_err.append((abs(_hh - _printed) / float(_printed), _name, _pct,
                            _hh, _printed))
    _dc_worst = max(_dc_err) if _dc_err else (9.9, "", 0.0, 0.0, 0)
    check("the fit reproduces the catalog's OWN printed 80 %/50 % summary table: all "
          "18 numbers for the nine 60u materials, worst 0.96 % (MPP at 50 %: 106.0 Oe "
          "against a printed 105). This is the check that missed by a factor of 2-3.5 "
          "on the previous attempt, which took its fit from a different printed page",
          len(_dc_err) == 18 and _dc_worst[0] <= 0.01 and _dc_worst[2] == 50.0,
          "%d numbers, worst %.4f %s at %g %% (%.1f vs %s)"
          % (len(_dc_err), _dc_worst[0], _dc_worst[1], _dc_worst[2], _dc_worst[3],
             _dc_worst[4]))

    _dc_ex2 = _call("magnetics_dc_bias", {"material": "Kool Mu", "permeability": 60,
                                          "shape_family": "Toroids", "turns": 20,
                                          "dc_current_a": 20, "le_mm": 63.5},
                    1886)
    _dc_ex3 = _call("magnetics_dc_bias", {"material": "Kool Mu", "permeability": 60,
                                          "shape_family": "Toroids", "h_oe": 15.8},
                    1887)
    _dc_v2 = _dc_ex2.get("value") or {}
    _dc_v3 = _dc_ex3.get("value") or {}
    check("both worked examples printed in the catalog come out: Example 2 (60u Kool "
          "Mu, 20 turns, 20 A, le 63.5 mm) prints 'from curve fit equation, % mu_i = "
          "0.58' and the fit gives 58.4 % at 79.16 Oe; Example 3 prints B = 0.092 T at "
          "15.8 Oe and integrating the fit gives 0.0936 T (1.7 % high - at that H the "
          "curve is still flat, so this example pins the magnitude and not the integral "
          "form; the 95 Oe point, where the chord is 35 % low, is the one that does)",
          abs((_dc_v2.get("h_oe") or 0.0) - 79.16) < 0.02
          and abs((_dc_v2.get("percent_initial_permeability") or 0.0) - 58.4) < 0.5
          and abs((_dc_v3.get("b_t") or 0.0) - 0.092) < 0.002,
          "ex2 %s Oe -> %s %%, ex3 B %s T (%s)"
          % (_dc_v2.get("h_oe"), _dc_v2.get("percent_initial_permeability"),
             _dc_v3.get("b_t"), _dc_v3.get("h_source")))

    _dc_hi = _call("magnetics_dc_bias", {"material": "Kool Mu", "permeability": 60,
                                         "shape_family": "Toroids", "h_oe": 2000.0},
                   1888).get("value") or {}
    _dc_bad = _call("magnetics_dc_bias", {"material": "MPP", "permeability": 55,
                                          "shape_family": "Toroids", "h_oe": 10.0},
                    1889)
    check("an H outside the 10-800 Oe band the graphs plot is flagged and NOT clamped, "
          "and a permeability the vendor never printed is refused with the list of the "
          "fourteen it did print rather than interpolated",
          any("outside" in w for w in (_dc_hi.get("warnings") or []))
          and (_dc_hi.get("percent_initial_permeability") or 9.9) < 1.0
          and _dc_bad.get("reason_code") == "NOT_FOUND"
          and (_dc_bad.get("permeabilities_printed") or []) ==
          [14, 19, 26, 40, 60, 75, 90, 125, 147, 160, 173, 200, 300, 550],
          "hi %s %% with %d warnings; MPP 55u -> %s"
          % (_dc_hi.get("percent_initial_permeability"),
             len(_dc_hi.get("warnings") or []), _dc_bad.get("reason_code")))

    # ---- 0.0.78: the DC-bias loop inside select_core ---------------------
    from .calc import magnetics as _mg

    _dcb_args = {"inductance_h": 1e-4, "i_peak_a": 10.0, "permeability": 60,
                 "include_powder_cores": True}
    _dcb_free = _call("magnetics_select_core", dict(_dcb_args), 1890)
    _dcb_8a = _call("magnetics_select_core",
                    dict(_dcb_args, dc_bias_current_a=8.0, limit=3), 1891)
    _dcb_v = _dcb_8a.get("value") or {}
    _dcb_c = (_dcb_v.get("candidates") or [{}])[0]
    _dcb_d = _dcb_c.get("dc_bias") or {}
    _dcb_fit = _dcb_d.get("fit") or {}
    _dcb_hand = _mg.dc_bias_percent(
        _dcb_fit.get("a", 0.01), _dcb_fit.get("b", 0.0), _dcb_fit.get("c", 0.0),
        _dcb_d.get("h_oe", 0.0)) if _dcb_fit else -1.0
    check("the DC-bias loop closes inside select_core: 100 uH / 10 A on the 60u "
          "powder pool at an 8 A bias lands on XFlux Ultra 0074121A7 at 82 turns "
          "against 54 at zero bias, H = 200.09 Oe where the printed fit leaves "
          "42.6005 % of mu_i - and that percentage is re-derived here from the same "
          "row's own (a, b, c), so a fit read from any other page fails this",
          _dcb_8a.get("status") == "ok"
          and _dcb_c.get("part") == "0074121A7"
          and _dcb_c.get("turns") == 82
          and _dcb_d.get("turns_without_bias") == 54
          and abs(_dcb_d.get("h_oe", 0.0) - 200.085901) < 0.01
          and abs(_dcb_d.get("percent_initial_permeability", 0.0) - _dcb_hand) < 1e-6
          and abs(_dcb_d.get("percent_initial_permeability", 0.0) - 42.600515) < 1e-4
          and (_dcb_d.get("inductance_h") or 0.0) >= 1e-4
          and (_dcb_d.get("b_peak_t") or 9.9) <= (_dcb_d.get("b_limit_t") or 0.0)
          and abs((_dcb_c.get("al_ratio") or 0.0) - 1.0) < 0.01
          and _dcb_d.get("h_within_printed_band") is True
          and (_dcb_free.get("value") or {}).get("feasible_count") == 288,
          "%s at %s turns (%s at zero bias), H %s Oe, mu %s %% vs fit %s %%, "
          "L %s H, B %s T of %s"
          % (_dcb_c.get("part"), _dcb_c.get("turns"),
             _dcb_d.get("turns_without_bias"), _dcb_d.get("h_oe"),
             _dcb_d.get("percent_initial_permeability"), "%.6f" % _dcb_hand,
             _dcb_d.get("inductance_h"), _dcb_d.get("b_peak_t"),
             _dcb_d.get("b_limit_t")))

    _dcb_k = 0.4 * _math.pi * 8.0 / 4.12          # oersteds per turn on that part
    _dcb_cap = 1000
    _dcb_pct_cap = _mg.dc_bias_percent(0.01, _dcb_fit.get("b", 0.0),
                                       _dcb_fit.get("c", 0.0), _dcb_k * _dcb_cap)
    _dcb_l_cap = 35e-9 * _dcb_cap * _dcb_cap * _dcb_pct_cap / 100.0
    _dcb_out_ = [r for r in (_dcb_v.get("rejected") or [])
                 if r.get("reason") == "dc_bias_unreachable" and r.get("part") == "0077141A7"]
    _dcb_out_d = (_dcb_out_[0].get("dc_bias") or {}) if _dcb_out_ else {}
    check("L(N) is not monotone and the search does not pretend it is: on that same "
          "part (c = 2.815 > 2) 82 turns reach 100.26 uH while the 1000-turn cap "
          "reaches only 22.73 uH, so this is a bracketed search and not a fixed "
          "point - 86 of the 125 printed rows have c > 2; and a part that cannot "
          "reach L is refused by name with the maximum it does reach rather than "
          "with a number that is not there",
          abs(_dcb_l_cap - 2.2734148978434586e-05) / 2.2734148978434586e-05 < 1e-6
          and _dcb_l_cap < (_dcb_d.get("inductance_h") or 0.0)
          and "86 of the 125" in (_dcb_v.get("dc_bias") or {}).get("found_by", "")
          and bool(_dcb_out_)
          and (_dcb_out_[0].get("turns")) is None
          and _dcb_out_[0].get("verdict") == "dc_bias_unreachable"
          and _dcb_out_d.get("turns_at_maximum") == 1000
          and abs((_dcb_out_d.get("maximum_inductance_h") or 0.0) - 1.53129e-06) < 1e-12
          and abs((_dcb_out_d.get("percent_at_maximum") or 0.0) - 0.011779) < 1e-6
          and "0077141A7" in ((_dcb_v.get("dc_bias") or {}).get("unreachable") or []),
          "L(1000 turns) %s H vs L(82) %s H; 0077141A7 max %s H at %s turns, "
          "mu %s %%, top-level unreachable %d parts"
          % ("%.6g" % _dcb_l_cap, _dcb_d.get("inductance_h"),
             _dcb_out_d.get("maximum_inductance_h"),
             _dcb_out_d.get("turns_at_maximum"),
             _dcb_out_d.get("percent_at_maximum"),
             len((_dcb_v.get("dc_bias") or {}).get("unreachable") or [])))

    _dcb_wide = _call("magnetics_select_core",
                      dict(_dcb_args, dc_bias_current_a=8.0, limit=400), 1892)
    _dcb_wv = _dcb_wide.get("value") or {}
    _dcb_wd = _dcb_wv.get("dc_bias") or {}
    _dcb_band = [c for c in (_dcb_wv.get("candidates") or [])
                 if (c.get("dc_bias") or {}).get("h_within_printed_band") is False]
    _dcb_one = (_dcb_band[0].get("dc_bias") or {}) if _dcb_band else {}
    check("an H outside the 10-800 Oe the fit pages are drawn over is flagged, not "
          "clamped and not presented as a reading: across the whole 232-candidate "
          "pool at 8 A seven parts land out of band, the first at 1743.5 Oe, and "
          "the note says EXTRAPOLATION",
          len(_dcb_wd.get("outside_the_printed_band") or []) == 7
          and "0077381A7" in (_dcb_wd.get("outside_the_printed_band") or [])
          and _mg.DC_BIAS_PLOTTED_OE == (10.0, 800.0)
          and len(_dcb_band) == 7
          and "EXTRAPOLATION" in _dcb_one.get("h_band_note", "")
          and (_dcb_one.get("h_oe") or 0.0) > 1743.0,
          "%d out of band, first %s at %s Oe, mu %s %%"
          % (len(_dcb_wd.get("outside_the_printed_band") or []),
             (_dcb_band[0].get("part") if _dcb_band else None),
             _dcb_one.get("h_oe"), _dcb_one.get("percent_initial_permeability")))

    def _dcb_refuse(args, rid):
        return _call("magnetics_select_core", args, rid)

    _dcb_r1 = _dcb_refuse({"inductance_h": 1e-4, "i_peak_a": 10.0,
                           "permeability": 60, "dc_bias_current_a": 8.0}, 1893)
    _dcb_r2 = _dcb_refuse(dict(_dcb_args, dc_bias_current_a=12.0), 1894)
    _dcb_r3 = _dcb_refuse(dict(_dcb_args, dc_bias_current_a=0.0), 1895)
    _dcb_r4 = _dcb_refuse(dict(_dcb_args, dc_bias_current_a="x"), 1896)
    _dcb_r5 = _dcb_refuse(dict(_dcb_args, dc_bias_current_a=8.0,
                               dc_bias_data_path="data/no_such_fit.json"), 1897)
    check("every way to ask for a bias that cannot be solved is its own refusal, "
          "never a quiet guess: a bias with only solid cores ARG_INVALID, a bias "
          "above the peak current ARG_RANGE, a zero bias ARG_RANGE whose hint "
          "points at the unchanged 0.0.77 answer, a non-numeric bias ARG_INVALID, "
          "and a missing fit table DATA_UNAVAILABLE - and the tool count is 178, "
          "because 0.0.78 added arguments and no tool, 0.0.79 added exactly one "
          "(magnetics_toroid_mlt) and 0.0.80 adds exactly one "
          "(magnetics_powder_curve)",
          _dcb_r1.get("reason_code") == "ARG_INVALID"
          and "include_powder_cores" in json.dumps(_dcb_r1, ensure_ascii=False)
          and _dcb_r2.get("reason_code") == "ARG_RANGE"
          and _dcb_r3.get("reason_code") == "ARG_RANGE"
          and "0.0.77" in json.dumps(_dcb_r3, ensure_ascii=False)
          and _dcb_r4.get("reason_code") == "ARG_INVALID"
          and _dcb_r5.get("reason_code") == "DATA_UNAVAILABLE"
          and len(_tools.TOOLS) == 178,
          "powder %s, above-peak %s, zero %s, string %s, missing %s, tools %d"
          % (_dcb_r1.get("reason_code"), _dcb_r2.get("reason_code"),
             _dcb_r3.get("reason_code"), _dcb_r4.get("reason_code"),
             _dcb_r5.get("reason_code"), len(_tools.TOOLS)))

    # ---- 0.0.79: the toroid mean turn length and the copper it buys ------
    from .calc import magnetics as _mg

    _tm_free = _call("magnetics_toroid_mlt",
                     {"od_mm": 26.92, "id_mm": 14.48, "ht_mm": 11.10}, 1900)
    _tm_v = _tm_free.get("value") or {}
    _tm_mlt = _tm_v.get("mlt") or {}
    _tm_geo = _tm_v.get("geometry") or {}
    _tm_printed = 43.9        # the T106-1 data sheet's own printed mlt, in mm
    check("a toroid's mean turn length is geometry times a MEASURED band, never a "
          "single number: T106-1 (OD 26.92 / ID 14.48 / H 11.10) gives the flat "
          "section 34.64 mm, a band of 43.218817 .. 50.590750 mm about the median "
          "1.365936 of 13 printed ratios, and the vendor's own printed 43.9 mm falls "
          "INSIDE that band - which is the only way this may be quoted as an estimate",
          _tm_free.get("status") == "ok"
          and abs(_tm_mlt.get("flat_rect_mm", 0) - 34.64) < 1e-9
          and abs(_tm_mlt.get("estimate_mm", 0) - 47.31602) < 1e-4
          and _tm_mlt.get("range_mm") == [43.218817, 50.59075]
          and _tm_mlt.get("ratio", {}).get("n") == 13
          and _tm_mlt.get("is_estimate") is True
          and _tm_mlt["range_mm"][0] <= _tm_printed <= _tm_mlt["range_mm"][1]
          and abs(_tm_geo.get("le_mm", 0) - 65.030968) < 1e-5
          and abs(_tm_geo.get("ae_mm2", 0) - 69.042) < 1e-5,
          "flat %s estimate %s range %s n %s"
          % (_tm_mlt.get("flat_rect_mm"), _tm_mlt.get("estimate_mm"),
             _tm_mlt.get("range_mm"), _tm_mlt.get("ratio", {}).get("n")))

    _tm_fake = _mg._toroid_mlt_band({"rows": [
        {"model": "made-up", "od_mm": 10.0, "id_mm": 5.0, "ht_mm": 2.0,
         "mlt_mm": 27.0}]})
    check("the band is MEASURED from the rows on every call rather than stored in "
          "the file: one fabricated row (flat 4 + 5 = 9 mm against a 27 mm turn) "
          "moves the median to 3.0 and the count to 1, so a wrong table would move "
          "the answer instead of hiding behind a cached summary",
          _tm_fake is not None and _tm_fake["n"] == 1
          and abs(_tm_fake["ratio_median"] - 3.0) < 1e-12
          and abs(_tm_fake["ratio_min"] - 3.0) < 1e-12,
          "n %s median %s" % ((_tm_fake or {}).get("n"),
                              (_tm_fake or {}).get("ratio_median")))

    _tm_w = _call("magnetics_toroid_mlt",
                  {"od_mm": 26.92, "id_mm": 14.48, "ht_mm": 11.10, "turns": 54,
                   "wire_diameter_mm": 1.0, "i_rms_a": 5.0,
                   "fill_factor": 0.4}, 1901)
    _tm_wv = (_tm_w.get("value") or {}).get("winding") or {}
    # independent arithmetic, written here and not read from the answer:
    # rho = 1.724e-8 * (1 + 0.00393*80), R = rho*N*MLT/A_cu
    _tm_rho = 1.724e-8 * (1.0 + 0.00393 * 80.0)
    _tm_r = _tm_rho * 54 * 47.31602e-3 / (_math.pi * 0.5 * 0.5 * 1e-6)
    check("the copper follows from the estimated length and nothing else: 54 turns of "
          "1.0 mm wire on that core give N1=42 through the bore, 2 layers (flagged as "
          "a LOW bias), R_dc 0.073719 ohm against an independent rho(100 C)*N*MLT/A of "
          "%.6f, and 1.842964 W at 5 A rms" % _tm_r,
          abs(_tm_r - 0.073719) < 2e-6
          and abs(_tm_wv.get("r_dc_ohm", 0) - 0.073719) < 1e-6
          and _tm_wv.get("single_layer_max_turns") == 42
          and _tm_wv.get("layers") == 2
          and "biased LOW" in (_tm_wv.get("multilayer_note") or "")
          and abs(_tm_wv.get("p_copper_w", 0) - 1.842964) < 1e-5
          and _tm_wv.get("window_fits") is True
          and _tm_wv["r_dc_range_ohm"][0] < _tm_wv["r_dc_ohm"]
          < _tm_wv["r_dc_range_ohm"][1],
          "r %s independent %.7f N1 %s layers %s p %s"
          % (_tm_wv.get("r_dc_ohm"), _tm_r, _tm_wv.get("single_layer_max_turns"),
             _tm_wv.get("layers"), _tm_wv.get("p_copper_w")))

    _tm_cold = _call("magnetics_toroid_mlt",
                     {"od_mm": 26.92, "id_mm": 14.48, "ht_mm": 11.10,
                      "turns": 54, "wire_diameter_mm": 1.0,
                      "temperature_c": 20.0}, 1902)
    _tm_rc = ((_tm_cold.get("value") or {}).get("winding") or {}).get("r_dc_ohm") or 0
    check("temperature enters as copper does and not as a fudge: the same winding at "
          "20 C is 0.056085 ohm, exactly 1/1.3144 of the 100 C answer",
          abs(_tm_rc - 0.056085) < 1e-6
          and abs((_tm_wv.get("r_dc_ohm") or 0) / _tm_rc - (1.0 + 0.00393 * 80.0))
          < 1e-3,
          "20 C %s, ratio %.6f" % (_tm_rc, (_tm_wv.get("r_dc_ohm") or 0) / _tm_rc))

    def _tm_refuse(args, rid):
        return _call("magnetics_toroid_mlt", args, rid)

    _tm_bore = _tm_refuse({"od_mm": 14.48, "id_mm": 26.92, "ht_mm": 11.10}, 1903)
    _tm_wire = _tm_refuse({"od_mm": 26.92, "id_mm": 14.48, "ht_mm": 11.10,
                           "wire_diameter_mm": 20.0}, 1904)
    _tm_nowire = _tm_refuse({"od_mm": 26.92, "id_mm": 14.48, "ht_mm": 11.10,
                             "turns": 10}, 1905)
    _tm_nan = _tm_refuse({"od_mm": "big", "id_mm": 14.48, "ht_mm": 11.10}, 1906)
    _tm_half = _tm_refuse({"od_mm": 26.92, "id_mm": 14.48, "ht_mm": 11.10,
                           "turns": 10.5, "wire_diameter_mm": 1.0}, 1907)
    _tm_noi = _tm_refuse({"od_mm": 26.92, "id_mm": 14.48, "ht_mm": 11.10,
                          "wire_diameter_mm": 1.0, "i_rms_a": 5.0}, 1908)
    _tm_notab = _tm_refuse({"od_mm": 26.92, "id_mm": 14.48, "ht_mm": 11.10,
                            "mlt_data_path": "data/no_such_mlt.json"}, 1909)
    check("every way to ask an impossible toroid question is its own refusal, never a "
          "quiet number: ID>=OD ARG_RANGE, a wire wider than the bore ARG_RANGE, turns "
          "without a wire ARG_INVALID, a non-numeric diameter ARG_INVALID, half a turn "
          "ARG_RANGE, a current without turns ARG_INVALID, a missing band table "
          "DATA_UNAVAILABLE",
          _tm_bore.get("reason_code") == "ARG_RANGE"
          and _tm_wire.get("reason_code") == "ARG_RANGE"
          and _tm_nowire.get("reason_code") == "ARG_INVALID"
          and _tm_nan.get("reason_code") == "ARG_INVALID"
          and _tm_half.get("reason_code") == "ARG_RANGE"
          and _tm_noi.get("reason_code") == "ARG_INVALID"
          and _tm_notab.get("reason_code") == "DATA_UNAVAILABLE",
          "bore %s wire %s nowire %s nan %s half %s noi %s notab %s"
          % tuple(_tm.get("reason_code") for _tm in
                  (_tm_bore, _tm_wire, _tm_nowire, _tm_nan, _tm_half, _tm_noi,
                   _tm_notab)))

    _tm_args = {"inductance_h": 1e-4, "i_peak_a": 5.0, "i_rms_a": 1.0,
                "current_density_a_mm2": 4.0, "include_powder_cores": True,
                "material": "MPP", "permeability": 60, "limit": 2}
    _tm_off = _call("magnetics_select_core", dict(_tm_args), 1910)
    _tm_omitted = _call("magnetics_select_core", dict(_tm_args), 1911)
    _tm_on = _call("magnetics_select_core", dict(_tm_args, toroid_mlt="estimate"),
                   1912)
    _tm_bad = _call("magnetics_select_core", dict(_tm_args, toroid_mlt="maybe"), 1913)
    _tm_row_off = (_tm_off.get("value") or {}).get("candidates", [{}])[0]
    _tm_row_on = (_tm_on.get("value") or {}).get("candidates", [{}])[0]
    _tm_woff = _tm_row_off.get("winding") or {}
    _tm_won = _tm_row_on.get("winding") or {}
    check("select_core(toroid_mlt='estimate') is the 0.0.78 answer plus a winding and "
          "changes nothing else: passing 'off' is byte-identical to omitting the "
          "argument, the off answer still has p_copper_w=null with the old sentence "
          "naming the missing length, and the on answer adds an estimated MLT with its "
          "range while the part, the turns and the flux are untouched",
          json.dumps(_tm_off, sort_keys=True) == json.dumps(_tm_omitted, sort_keys=True)
          and _tm_woff.get("p_copper_w") is None
          and "no mean turn length" in (_tm_woff.get("missing_wire") or "")
          and _tm_won.get("mlt_is_estimate") is True
          and _tm_won.get("r_dc_ohm") is not None
          and _tm_won["mlt_range_mm"][0] < _tm_won["mlt_mm"] < _tm_won["mlt_range_mm"][1]
          and _tm_row_on.get("part") == _tm_row_off.get("part")
          and _tm_row_on.get("turns") == _tm_row_off.get("turns")
          and _tm_bad.get("reason_code") == "ARG_INVALID"
          and len(_tools.TOOLS) == 178,
          "off/omitted identical %s, off copper %s, on mlt %s, part %s/%s, tools %d"
          % (json.dumps(_tm_off, sort_keys=True) == json.dumps(_tm_omitted,
                                                              sort_keys=True),
             _tm_woff.get("p_copper_w"), _tm_won.get("mlt_mm"),
             _tm_row_off.get("part"), _tm_row_on.get("part"), len(_tools.TOOLS)))

    # ---- 0.0.80: the printed magnetisation and mu-vs-T curves -------------
    _pc_we = _call("magnetics_powder_curve",
                   {"material": "Kool Mu", "permeability": 60,
                    "shape_family": "Toroids", "h_oe": 95.0}, 1914)
    _pc_wev = _pc_we.get("value") or {}
    check("the catalog's own worked example comes out of the transcription: 60u Kool Mu "
          "at 95 Oe is 0.441 T printed on p.25 and 0.44126 T here, and the same row's "
          "DC-bias fit integrated over 0..95 Oe gives 0.44214 T, so the two printed "
          "fits agree to 0.20 %% on this part",
          _pc_we.get("status") == "ok"
          and abs((_pc_wev.get("b_t") or 0) - 0.44126) < 5e-5
          and abs((_pc_wev.get("b_t") or 0) - 0.441) / 0.441 < 1e-2
          and abs((_pc_wev.get("b_t_from_dc_bias_integral") or 0) - 0.442137) < 5e-5
          and abs((_pc_wev.get("delta_pct_vs_dc_bias_integral") or 0) + 0.198) < 0.02
          and _pc_wev.get("fits_disagree") is False
          and _pc_wev.get("inside_plotted_band") is True,
          "b %s, integral %s, delta %s %%" % (_pc_wev.get("b_t"),
                                              _pc_wev.get("b_t_from_dc_bias_integral"),
                                              _pc_wev.get("delta_pct_vs_dc_bias_integral")))

    _pc_mats = ["Kool Mu", "Kool Mu MAX", "Kool Mu HF", "Kool Mu Ultra", "XFlux",
                "XFlux Ultra", "High Flux", "Edge", "MPP"]
    _pc_gaps = []
    _pc_bad = []
    for _pc_i, _pc_m in enumerate(_pc_mats):
        _pc_r = _call("magnetics_powder_curve",
                      {"material": _pc_m, "permeability": 60,
                       "temperature_c": 100.0}, 1915 + _pc_i)
        _pc_v = _pc_r.get("value") or {}
        _g = _pc_v.get("printed_stability_gap_pp")
        if _g is None:
            _pc_bad.append(_pc_m)
        else:
            _pc_gaps.append(_g)
    check("the temperature fit is checked against a number the catalog prints "
          "separately: its predicted dynamic range over -50..+100 C matches the p.15 "
          "stability column on all nine materials, worst gap %.3f pp (0.045 on 60u "
          "Kool Mu)" % (max(_pc_gaps) if _pc_gaps else -1),
          len(_pc_gaps) == 9 and not _pc_bad and max(_pc_gaps) < 0.05,
          "gaps %s, missing %s" % (["%.3f" % g for g in _pc_gaps], _pc_bad))

    _pc_int = []
    with open(_tools._curve_table_path(), "r", encoding="utf-8") as _fh:
        _json_data = json.load(_fh)
    for _pc_g in (_json_data.get("magnetization") or {}).get("groups") or []:
        if _pc_g.get("shape_family") != "Toroids":
            continue
        for _pc_row in _pc_g.get("rows") or []:
            _pc_r = _call("magnetics_powder_curve",
                          {"material": _pc_g.get("material"),
                           "permeability": _pc_row.get("permeability"),
                           "shape_family": "Toroids", "h_oe": 100.0}, 1930)
            _pc_v = _pc_r.get("value") or {}
            _d = _pc_v.get("delta_pct_vs_dc_bias_integral")
            if _d is not None:
                _pc_int.append(abs(_d))
    _pc_int.sort()
    _pc_med = _pc_int[len(_pc_int) // 2] if _pc_int else -1
    check("the magnetisation curve and the DC-bias fit are two independent routes to "
          "the same flux, and they agree over the whole toroid set: %d rows at 100 Oe, "
          "median disagreement %.3f %% (the two are transcribed from different pages "
          "of the same catalog)" % (len(_pc_int), _pc_med),
          len(_pc_int) == 61 and _pc_med < 2.0,
          "%d rows, median %.3f %%, worst %.3f %%" % (len(_pc_int), _pc_med,
                                                      _pc_int[-1] if _pc_int else -1))

    _pc_mpp = _call("magnetics_powder_curve",
                    {"material": "MPP", "permeability": 550,
                     "shape_family": "Toroids", "h_oe": 100.0}, 1940)
    _pc_mppv = _pc_mpp.get("value") or {}
    check("a vendor printing two fits that disagree is reported, not reconciled: MPP "
          "550u at 100 Oe is 0.2341 T from the magnetisation curve against 0.5128 T "
          "from the integrated DC-bias fit (54.3 %), fits_disagree is true, and the "
          "row's printed turnover at 23.8 Oe is carried so a query past it is marked "
          "past_fit_turnover instead of returning a falling branch as a flux",
          _pc_mpp.get("status") == "ok"
          and _pc_mppv.get("fits_disagree") is True
          and abs((_pc_mppv.get("delta_pct_vs_dc_bias_integral") or 0) + 54.34) < 0.05
          and abs((_pc_mppv.get("turns_over_at_h_oe") or 0) - 23.8) < 0.05
          and _pc_mppv.get("past_fit_turnover") is True
          and _pc_mppv.get("mu_r_differential") is None
          and any("stops rising at H = 23.8 Oe" in w
                  for w in _pc_mppv.get("warnings") or []),
          "b %s, integral %s, delta %s, turnover %s" % (
              _pc_mppv.get("b_t"), _pc_mppv.get("b_t_from_dc_bias_integral"),
              _pc_mppv.get("delta_pct_vs_dc_bias_integral"),
              _pc_mppv.get("turns_over_at_h_oe")))

    def _pc_refuse(args, rid):
        return _call("magnetics_powder_curve", args, rid)

    _pc_h0 = _pc_refuse({"material": "Kool Mu", "permeability": 60,
                         "shape_family": "Toroids", "h_oe": 0.0}, 1941)
    _pc_shp = _pc_refuse({"material": "Kool Mu", "permeability": 60, "h_oe": 10.0},
                         1942)
    _pc_perm = _pc_refuse({"material": "Kool Mu", "permeability": 33,
                           "shape_family": "Toroids", "h_oe": 10.0}, 1943)
    _pc_mat = _pc_refuse({"material": "Sendust", "permeability": 60, "h_oe": 10.0},
                         1944)
    _pc_none = _pc_refuse({"material": "Kool Mu", "permeability": 60,
                           "shape_family": "Toroids"}, 1945)
    _pc_tlo = _pc_refuse({"material": "Kool Mu", "permeability": 60,
                          "temperature_c": -51.0}, 1946)
    _pc_thi = _pc_refuse({"material": "Kool Mu", "permeability": 60,
                          "temperature_c": 201.0}, 1947)
    _pc_ntab = _pc_refuse({"material": "Kool Mu", "permeability": 60,
                           "h_oe": 10.0,
                           "curve_data_path": "data/no_such_curve.json"}, 1948)
    check("every way to ask a question the printed curves cannot answer is its own "
          "refusal with the list that would answer it: H = 0 ARG_INVALID (and it names "
          "B(0) = a^x = 0.00529 T, so the fit is not a small-signal model), a material "
          "printed in four shapes ARG_INVALID listing them, an unprinted permeability "
          "NOT_FOUND listing the printed ones, an unprinted material NOT_FOUND listing "
          "the materials, no h and no temperature ARG_INVALID, -51 C and +201 C "
          "OUT_OF_VALIDITY against a -50..200 C range, and a missing that "
          "DATA_UNAVAILABLE",
          _pc_h0.get("reason_code") == "ARG_INVALID"
          and "0.00529" in json.dumps(_pc_h0, ensure_ascii=False)
          and _pc_shp.get("reason_code") == "ARG_INVALID"
          and len(_pc_shp.get("shape_families_printed") or []) == 4
          and _pc_perm.get("reason_code") == "NOT_FOUND"
          and 75 in (_pc_perm.get("permeabilities_printed") or [])
          and _pc_mat.get("reason_code") == "NOT_FOUND"
          and len(_pc_mat.get("materials_printed") or []) == 12
          and _pc_none.get("reason_code") == "ARG_INVALID"
          and _pc_tlo.get("reason_code") == "OUT_OF_VALIDITY"
          and _pc_thi.get("reason_code") == "OUT_OF_VALIDITY"
          and _pc_ntab.get("reason_code") == "DATA_UNAVAILABLE",
          "h0 %s shape %s perm %s mat %s none %s tlo %s thi %s tab %s"
          % (_pc_h0.get("reason_code"), _pc_shp.get("reason_code"),
             _pc_perm.get("reason_code"), _pc_mat.get("reason_code"),
             _pc_none.get("reason_code"), _pc_tlo.get("reason_code"),
             _pc_thi.get("reason_code"), _pc_ntab.get("reason_code")))

    _pc_edge = _call("magnetics_powder_curve",
                     {"material": "Kool Mu", "permeability": 60,
                      "shape_family": "Toroids", "temperature_c": 150.0}, 1949)
    _pc_edgev = _pc_edge.get("value") or {}
    _pc_end = _call("magnetics_powder_curve",
                    {"material": "Kool Mu", "permeability": 60,
                     "temperature_c": 200.0}, 1950)
    _pc_far = _call("magnetics_powder_curve",
                    {"material": "Kool Mu", "permeability": 60,
                     "shape_family": "Toroids", "h_oe": 1000.0}, 1951)
    check("the answered-but-flagged cases stay answered and flagged: 150 C is inside "
          "the range this tool evaluates and OUTSIDE the -50..100 C band the catalog "
          "tabulates, so it returns a number with inside_printed_dynamic_range=false; "
          "200 C is accepted as the printed rating; and 1000 Oe is outside the "
          "1-300 Oe the graphs plot, so inside_plotted_band is false rather than the "
          "H being clamped",
          _pc_edge.get("status") == "ok"
          and _pc_edgev.get("inside_printed_dynamic_range") is False
          and any("outside the -50..100 C band" in w
                  for w in _pc_edgev.get("warnings") or [])
          and _pc_end.get("status") == "ok"
          and ((_pc_end.get("value") or {}).get("inside_printed_dynamic_range")
               is False)
          and ((_pc_far.get("value") or {}).get("inside_plotted_band") is False),
          "150 C %s, 200 C %s, 1000 Oe inside %s"
          % (_pc_edgev.get("inside_printed_dynamic_range"),
             ((_pc_end.get("value") or {}).get("inside_printed_dynamic_range")),
             ((_pc_far.get("value") or {}).get("inside_plotted_band"))))

    _pc_var = _call("magnetics_powder_curve",
                    {"material": "High DC Bias XFlux", "permeability": 26,
                     "h_oe": 50.0}, 1952)
    _pc_turn = _call("magnetics_powder_curve",
                     {"material": "Kool Mu", "permeability": 60,
                      "shape_family": "Toroids", "turns": 40,
                      "dc_current_a": 2.0, "le_mm": 50.0}, 1953)
    _pc_mix = _pc_refuse({"material": "Kool Mu", "permeability": 60,
                          "shape_family": "Toroids", "h_oe": 10.0,
                          "turns": 40, "dc_current_a": 2.0, "le_mm": 50.0}, 1954)
    check("the winding route gives the same H the catalog's own relation gives "
          "(0.4*pi*N*I/le = 20.106 Oe for 40 turns, 2 A, 50 mm) and is refused when "
          "both routes are supplied; the High DC Bias variant blocks are reachable "
          "without a shape family and are not cross-checked against the standard "
          "grade's DC-bias fit, which is a different curve",
          abs(((_pc_turn.get("value") or {}).get("h_oe") or 0) - 20.1062) < 1e-3
          and _pc_mix.get("reason_code") == "ARG_INVALID"
          and _pc_var.get("status") == "ok"
          and (_pc_var.get("value") or {}).get("material") == "High DC Bias XFlux"
          and (_pc_var.get("value") or {}).get("b_t_from_dc_bias_integral") is None
          and "different curve" in ((_pc_var.get("value") or {})
                                    .get("cross_check_note") or ""),
          "H %s, mix %s, variant %s" % ((_pc_turn.get("value") or {}).get("h_oe"),
                                        _pc_mix.get("reason_code"),
                                        _pc_var.get("status")))

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
