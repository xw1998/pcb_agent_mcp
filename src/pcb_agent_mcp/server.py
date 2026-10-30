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
        # the point of having an engine at all: a card this module refuses by name
        # still runs, and the envelope says the deck was not parsed here. T (the
        # LOSSLESS line) moved INTO the builtin set in 0.0.30, and O/U (the lossy
        # ones) in 0.0.31, so the probe is now the voltage-controlled switch S -
        # a card that still has no implementation here but that ngspice solves
        # exactly: 1 V on the control node against VT=0.5 closes the switch, so the
        # divider reads 0.500 V, not the 0.333 V an open switch would give.
        r = handle({"jsonrpc": "2.0", "id": 96, "method": "tools/call",
                    "params": {"name": "sim_spice", "arguments": {
                        "netlist": "* sw\nV1 1 0 1\nR1 1 2 1k\n"
                                   "S1 2 0 1 0 sw\n"
                                   ".model sw SW(VT=0.5 VH=0 RON=1k ROFF=10meg)\n"
                                   ".op\n",
                        "engine": "ngspice"}}})
        payload = json.loads(r["result"]["content"][0]["text"])
        if _found:
            _n = (payload.get("value") or {}).get("nodes_v") or {}
            check("a switch the builtin refuses by name does run under ngspice "
                  "(1 V against VT=0.5 closes it, 0.500 V) and the envelope says "
                  "this module did not parse the deck",
                  payload.get("status") == "ok"
                  and payload.get("netlist_parsed") is False
                  and "S1" in (payload.get("netlist_parse_note") or "")
                  and abs(_n.get("2", 0.0) - 0.5) < 1e-6,
                  str(_n))
        else:
            check("engine='ngspice' refuses when no binary is installed instead of "
                  "quietly answering with the subset",
                  payload.get("status") == "error"
                  and payload.get("reason_code") == "ENGINE_NOT_FOUND",
                  str(payload.get("reason_code")))

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
