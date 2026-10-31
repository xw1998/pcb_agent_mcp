"""The live, in-process side of LCEDA Pro: its own command-space pipe.

Every other LCEDA module in this repository talks to FILES (the .epro/.eprj
archive, the record layout, the WebSocket gateway). This one talks to the
running application, and it is the only one that can be checked against the
application itself.

Measured on 2026-10-28 against LCEDA Pro 4.1.60.198f38ab (Windows), by starting
the app from the shell and then speaking its protocol:

  * transport   named pipe ``\\\\.\\pipe\\JLCEDAPro<8 hex>``. The prefix is the
                application name with whitespace removed; the 8 hex characters
                are a digest of the editor's user-data path (the app hashes it
                with sha256, first 8 hex, per its own code). We could NOT
                reproduce THIS install's 8 hex characters from any candidate
                user-data path, so the suffix is treated as opaque: endpoints
                are found by enumerating ``\\\\.\\pipe\\`` and confirmed by a
                ``ping`` that has to answer with a bridge version.
  * framing     one JSON object per line, ``{"id", "op", "timeoutMs", ...args}``
                in, ``{"id", "ok", "value" | "error", "logs", "durationMs"}``
                out. Request frame <= 4 MiB, reply <= 16 MiB, default 60 s.
  * operations  ping, stop, open, session.list, session.close, invoke, plus the
                reference queries doc.api / doc.external / doc.format.
  * ``invoke`` runs a source snippet as an async function body INSIDE the
                editor render process, with the vendor's ``eda`` object in
                scope. That is what makes this an in-process bridge: no file
                format, no WebSocket gateway, no guessing what a save means.

Worked end to end on that build:

  * ping              -> {endpoint, bridgeVersion: "4.1.60.198f38ab",
                          resultChannel: true}
  * open (headless)   -> a copied 32.5 MB .eprj answered in 849 ms with
                          {sessionId, renderId: 3, status: "alive"}
  * session.list      -> that session, plus its renderId and headless flag
  * session.close     -> {sessionId, status: "closed"}; with destroy=true the
                          entry STAYS in session.list with status "closed", it
                          does not disappear, so "closed" is the fact to read
  * doc.api           -> {namespace} alone gives
                          {types: [{name, comment}] (378 entries on "eda"),
                           callConventions: {entry, classToProperty}}
                        {namespace, className} gives
                          {title, remarks, methods: [{name, comment}], callPath}
                          (PCB_Document: 21 methods, vendor's Chinese comments)
  * invoke            -> await eda.dmt_Pcb.getAllPcbsInfo() returned both PCBs of
                          the opened project by name and uuid;
                          await eda.dmt_Project.getCurrentProjectInfo() returned
                          the project record; a thrown Error came back as
                          EXECUTION_ERROR with the app's own message.

Two traps worth knowing before writing a snippet: the document name is NOT the
runtime property name (doc.api's callConventions.classToProperty maps
PCB_Document -> pcb_Document, DMT_Pcb -> dmt_Pcb), and the tree classes expose
getAllPcbsInfo / getPcbInfo rather than list(). A document-level read such as
eda.pcb_Document.getCanvasOrigin() answered null when no PCB document was the
active one - that is not proof the call is wrong, only that it needs the right
document open.

NOT verified, and therefore never claimed here:

  * any WRITE. This module is still the READ side only; writes go through
    ``lceda_write``, which keeps the measurement table that decides when a
    write claim may say ``verified_against_app: true``. As of 2026-11-01 that
    table has one entry - LCEDA Pro V4.1.60 on a project this server created
    itself - and nothing was ever written to a user's project here.
  * the vendor's own MCP transport (``--mcp stdio|http``, 127.0.0.1:3030,
    protocol 2025-06-18). Its code is in the shipped app.js and its
    ``<prefix>.help / .discover / .call`` wrapper is real, but this build's
    executable rejects ``--mcp`` before the app sees it (node's own option
    parser answers ``bad option: --mcp``), so it was never started. This
    module speaks the pipe instead, which needs no extra flag.

Zero dependencies: hashlib, json, os, socket-free file IO on the pipe, time.
"""

import hashlib
import json
import os
import time

APP_NAME = "JLCEDAPro"
PIPE_ROOT = "\\\\.\\pipe\\"
MAX_REQUEST_BYTES = 4 * 1024 * 1024
MAX_REPLY_BYTES = 16 * 1024 * 1024
DEFAULT_TIMEOUT_MS = 60000
MAX_TIMEOUT_MS = 60000

# Vendor error codes this module passes through unchanged (they are facts about
# the application, not our vocabulary), so a caller can branch on them.
PASS_THROUGH_CODES = frozenset(
    (
        "UNKNOWN_OP",
        "INVALID_ARGS",
        "EXECUTION_ERROR",
        "SYNTAX_ERROR",
        "TIMEOUT",
        "SESSION_NOT_FOUND",
        "SESSION_CLOSED",
        "SESSION_DEAD",
        "SESSION_QUEUE_FULL",
        "SESSION_LIMIT_EXCEEDED",
        "FUNCTION_NOT_FOUND",
        "EXTENSION_NOT_FOUND",
        "RESULT_NOT_SERIALIZABLE",
        "RESULT_TOO_LARGE",
        "FRAME_TOO_LARGE",
        "PROTOCOL_ERROR",
        "HELP_QUERY_FAILED",
        "DOC_QUERY_FAILED",
        "DOC_CLI_NOT_FOUND",
        "INTERNAL",
    )
)


class LiveError(Exception):
    """A failure that came from the bridge, with the code it was reported under."""

    def __init__(self, code, message, extra=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.extra = extra or {}


def endpoint_name(user_data_dir, app_name=APP_NAME):
    """The deterministic part of the pipe name.

    This reproduces the application's own derivation (whitespace-stripped name +
    sha256(user_data_dir)[:8]) and is deliberately NOT used to guess a live
    endpoint: the derivation was checked against the code, not against this
    install, so callers enumerate instead.
    """
    digest = hashlib.sha256(str(user_data_dir).encode("utf-8")).hexdigest()[:8]
    return app_name.replace(" ", "") + digest


def available_endpoints(prefix=APP_NAME):
    """Named pipes that look like this application's command space."""
    try:
        names = os.listdir(PIPE_ROOT.rstrip("\\"))
    except OSError:
        return []
    out = []
    for name in names:
        if name.startswith(prefix):
            out.append(name)
    return sorted(out)


class Transport(object):
    """One request/response frame exchange. Fake transports subclass this."""

    live = False
    endpoint = None

    def call(self, op, payload, timeout_s):  # pragma: no cover - interface
        raise NotImplementedError

    def close(self):
        pass


class PipeTransport(Transport):
    """The real one: newline-delimited JSON over a Windows named pipe."""

    live = True

    def __init__(self, endpoint, open_path=None):
        self.endpoint = endpoint
        self._path = open_path or (PIPE_ROOT + endpoint)
        self._fh = None
        self._next_id = 0
        self._buffer = b""

    def _open(self):
        if self._fh is None:
            try:
                self._fh = open(self._path, "r+b", buffering=0)
            except OSError as exc:
                raise LiveError(
                    "APP_UNREACHABLE",
                    "cannot open %s: %s" % (self._path, exc),
                    {"endpoint": self.endpoint},
                )
        return self._fh

    def call(self, op, payload, timeout_s):
        fh = self._open()
        self._next_id += 1
        frame = {"id": self._next_id, "op": op}
        timeout_ms = int(min(max(timeout_s * 1000.0, 1.0), MAX_TIMEOUT_MS))
        frame["timeoutMs"] = timeout_ms
        for key, value in (payload or {}).items():
            if value is not None:
                frame[key] = value
        wire = json.dumps(frame, ensure_ascii=False).encode("utf-8") + b"\n"
        if len(wire) > MAX_REQUEST_BYTES:
            raise LiveError(
                "FRAME_TOO_LARGE",
                "request frame is %d bytes, limit is %d" % (len(wire), MAX_REQUEST_BYTES),
            )
        try:
            fh.write(wire)
        except OSError as exc:
            self.close()
            raise LiveError(
                "APP_UNREACHABLE",
                "write failed on %s: %s" % (self.endpoint, exc),
                {"endpoint": self.endpoint},
            )

        deadline = time.time() + timeout_s + 1.0
        while True:
            newline = self._buffer.find(b"\n")
            if newline >= 0:
                line, self._buffer = self._buffer[:newline], self._buffer[newline + 1 :]
                if not line.strip():
                    continue
                try:
                    reply = json.loads(line.decode("utf-8"))
                except ValueError as exc:
                    raise LiveError(
                        "PROTOCOL_ERROR", "reply is not JSON: %s" % exc
                    )
                return self._check(reply)
            if time.time() > deadline:
                self.close()
                raise LiveError(
                    "APP_TIMEOUT",
                    "no reply to '%s' within %.1f s on %s"
                    % (op, timeout_s, self.endpoint),
                    {"endpoint": self.endpoint, "op": op},
                )
            try:
                chunk = fh.read(65536)
            except OSError as exc:
                self.close()
                raise LiveError(
                    "APP_UNREACHABLE",
                    "read failed on %s: %s" % (self.endpoint, exc),
                    {"endpoint": self.endpoint},
                )
            if chunk:
                self._buffer += chunk
                if len(self._buffer) > MAX_REPLY_BYTES:
                    self.close()
                    raise LiveError(
                        "RESULT_TOO_LARGE",
                        "reply exceeded %d bytes" % MAX_REPLY_BYTES,
                    )
            else:
                time.sleep(0.02)

    @staticmethod
    def _check(reply):
        if not isinstance(reply, dict):
            raise LiveError("PROTOCOL_ERROR", "reply is not an object")
        if reply.get("ok") is True:
            return reply
        error = reply.get("error") or {}
        code = error.get("code")
        # A failure with no code is NOT reported as INTERNAL: that name is one of
        # the vendor's, and handing it back would claim the app said something it
        # did not. Unknown or missing codes become our own APP_ERROR, with the
        # app's own string kept in extra.app_code.
        if not code or code not in PASS_THROUGH_CODES:
            code = "APP_ERROR"
        raise LiveError(code, error.get("message") or "the application reported an error", {
            "app_code": error.get("code"),
            "app_message": error.get("message"),
            "logs": reply.get("logs") or [],
        })

    def close(self):
        if self._fh is not None:
            try:
                self._fh.close()
            except OSError:
                pass
            self._fh = None
        self._buffer = b""


class Client(object):
    """Thin, honest wrapper over the command space.

    Every method returns the application's own value (a dict/list as it came
    over the wire) or raises LiveError. It never invents a field the
    application did not send, and it never retries a request that may have
    side effects (open/invoke/stop) - a retry needs the caller's decision.
    """

    def __init__(self, transport):
        self.transport = transport
        self.endpoint = getattr(transport, "endpoint", None)
        self.identity = None

    @property
    def verified(self):
        return bool(self.transport.live)

    def _call(self, op, payload=None, timeout_s=DEFAULT_TIMEOUT_MS / 1000.0):
        return self.transport.call(op, payload or {}, timeout_s)

    def ping(self, timeout_s=10.0):
        reply = self._call("ping", {}, timeout_s)
        value = reply.get("value") or {}
        self.identity = value
        return value

    def open_project(self, path, headless=False, timeout_s=45.0):
        if not path:
            raise LiveError("ARG_REQUIRED", "path is required")
        path = os.path.abspath(path)
        if not os.path.exists(path):
            raise LiveError("ARG_INVALID", "no such project file: %s" % path)
        reply = self._call("open", {"path": path, "headless": bool(headless)}, timeout_s)
        return reply.get("value") or {}

    def sessions(self, timeout_s=10.0):
        reply = self._call("session.list", {}, timeout_s)
        return reply.get("value") or []

    def close_session(self, session, destroy=False, timeout_s=20.0):
        if not session:
            raise LiveError("ARG_REQUIRED", "session is required")
        reply = self._call(
            "session.close", {"session": session, "destroy": bool(destroy)}, timeout_s
        )
        return reply.get("value")

    def invoke(self, session, code, fn="run", args=None, timeout_ms=None):
        if not session:
            raise LiveError("ARG_REQUIRED", "session is required")
        if not code or not str(code).strip():
            raise LiveError("ARG_REQUIRED", "code is required")
        timeout_s = min(
            (timeout_ms or DEFAULT_TIMEOUT_MS) / 1000.0, MAX_TIMEOUT_MS / 1000.0
        )
        payload = {"session": session, "code": str(code), "fn": fn}
        if args is not None:
            payload["args"] = args
        reply = self._call("invoke", payload, timeout_s + 5.0)
        return {
            "value": reply.get("value"),
            "logs": reply.get("logs") or [],
            "duration_ms": reply.get("durationMs"),
        }

    def api(self, namespace="eda", class_name=None, method_name=None, timeout_s=30.0):
        payload = {"namespace": namespace}
        if class_name is not None:
            payload["className"] = class_name
        if method_name is not None:
            payload["methodName"] = method_name
        reply = self._call("doc.api", payload, timeout_s)
        return reply.get("value")

    def stop(self, timeout_s=10.0):
        return self._call("stop", {}, timeout_s).get("value")

    def close(self):
        self.transport.close()


def connect(endpoint=None, timeout_s=10.0):
    """Connect to a running editor, or raise APP_NOT_RUNNING with the reason.

    With no endpoint given this enumerates the pipes and keeps the first one
    whose ping answered; "a pipe with the right name" alone is not accepted,
    because any process can create one.
    """
    candidates = [endpoint] if endpoint else available_endpoints()
    if not candidates:
        raise LiveError(
            "APP_NOT_RUNNING",
            "no %s* command-space pipe is open" % APP_NAME,
            {"pipes_checked": 0, "pipe_root": PIPE_ROOT},
        )
    failures = []
    for name in candidates:
        client = Client(PipeTransport(name))
        try:
            client.ping(timeout_s)
            return client
        except LiveError as exc:
            failures.append({"endpoint": name, "code": exc.code, "message": exc.message})
            client.close()
    raise LiveError(
        "APP_NOT_RUNNING",
        "none of the %d command-space pipes answered a ping" % len(candidates),
        {"failures": failures, "pipe_root": PIPE_ROOT},
    )
