"""Minimal MCP server over stdio, standard library only.

Why not the official SDK: the Calc layer is pure arithmetic and the goal is
zero external components. This speaks newline-delimited JSON-RPC 2.0, which is
what the MCP stdio transport is. Handlers live in tools.py, so swapping in the
official SDK later is a thin adapter, not a rewrite.
"""
import json
import sys

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
