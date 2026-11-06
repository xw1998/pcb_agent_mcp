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
import sqlite3
import time

APP_NAME = "JLCEDAPro"
PIPE_ROOT = "\\\\.\\pipe\\"
MAX_REQUEST_BYTES = 4 * 1024 * 1024
MAX_REPLY_BYTES = 16 * 1024 * 1024
DEFAULT_TIMEOUT_MS = 60000
MAX_TIMEOUT_MS = 60000

# Runs inside the injected command space. The namespaces are reachable as
# `eda.dmt_*` (measured on 4.1.60: bare `dmt_Project` is undefined, `eda.dmt_Project`
# is a function). An unloaded editor answers with an *empty object*, not an error,
# so "loaded" has to be read from the object being non-empty - and `document` is not
# available in here at all, which is why a modal dialog cannot be clicked from here.
# Every `eda.dmt_*` getter that answers with data is ASYNC: measured on 4.1.60,
# `eda.dmt_Project.getCurrentProjectInfo()` returns `[object Promise]` and an
# un-awaited `Object.keys(...)` is therefore always empty. That reads exactly like
# "no project loaded" - a false negative with the same shape as the false positive
# this probe exists to kill. `invoke` runs the snippet as an async function body,
# so `await` is the correct form and is used everywhere below.
# What is asked, and why NOT getCurrentProjectInfo: measured 2026-11-07 on a project
# that had certainly opened (window title "F412RG 核心板 LCEDA | 嘉立创EDA(专业版)",
# file on disk), that getter never settled - 45 s, no value and no rejection - while
# getAllPcbsInfo() answered 2 PCBs and getAllSchematicsInfo() answered 1 schematic.
# A gate that waits on it can only ever say "not loaded", so it is not asked at all.
PROBE_JS = (
    # Each call is raced against a 5 s timer INSIDE the snippet. Measured 2026-11-07:
    # in one window getAllPcbsInfo() never returned (30 s) while getAllSchematicsInfo()
    # answered in the same render, so asking them in sequence would throw away the
    # answer that exists. A per-call window keeps one hanging call from being the
    # whole reading.
    "function __to(p,ms){return Promise.race(["
    "Promise.resolve(p).catch(function(e){return {__error:String(e&&e.message||e)};}),"
    "new Promise(function(r){setTimeout(function(){r({__timeout:true});},ms);})]);}"
    "var o={loaded:false,pcbCount:0,schCount:0,pcbs:[],schematics:[],projectUuid:null};"
    "function __n(x){return (typeof x.length==='number')?x.length:Object.keys(x||{}).length;}"
    "var b=await __to(eda.dmt_Pcb.getAllPcbsInfo(),5000);"
    "if(b&&b.__timeout){o.pcbError='timeout after 5000ms';}"
    "else if(b&&b.__error){o.pcbError=b.__error;}"
    "else{b=b||[];o.pcbCount=__n(b);"
    "o.pcbs=(Array.isArray(b)?b:[]).map(function(x){return {name:x&&x.name,uuid:x&&x.uuid};});"
    "if(b&&b[0]&&b[0].parentProjectUuid){o.projectUuid=b[0].parentProjectUuid;}}"
    "var s=await __to(eda.dmt_Schematic.getAllSchematicsInfo(),5000);"
    "if(s&&s.__timeout){o.schError='timeout after 5000ms';}"
    "else if(s&&s.__error){o.schError=s.__error;}"
    "else{s=s||[];o.schCount=__n(s);"
    "o.schematics=(Array.isArray(s)?s:[]).map(function(x){return {name:x&&x.name,"
    "uuid:x&&x.uuid,pages:(x&&x.page)?x.page.length:0};});"
    "if(!o.projectUuid&&s&&s[0]&&s[0].parentProjectUuid)"
    "{o.projectUuid=s[0].parentProjectUuid;}}"
    "var u=await __to(eda.dmt_Project.getAllProjectsUuid(),5000);"
    "if(u&&!u.__timeout&&!u.__error){u=u||[];o.projectCount=__n(u);}"
    "var w=await __to(eda.dmt_Workspace.getCurrentWorkspaceInfo(),5000);"
    "if(w&&!w.__timeout&&!w.__error){o.workspace=w||null;}"
    "o.loaded=(o.pcbCount>0)||(o.schCount>0);"
    "return o;"
)
# Fallback when the full probe does not answer in budget. Both calls here are
# answered on every measured state (including the empty one), which is exactly why
# they are worth keeping: they say whether the render is alive and which workspace
# is selected even when a document list cannot be read.
CHEAP_PROBE_JS = (
    "var o={};"
    "try{var u=await eda.dmt_Project.getAllProjectsUuid();u=u||[];"
    "o.projectCount=(typeof u.length==='number')?u.length:Object.keys(u).length;"
    "}catch(e){o.error=String(e&&e.message||e);}"
    "try{var w=await eda.dmt_Workspace.getCurrentWorkspaceInfo();o.workspace=w||null;"
    "}catch(e){}"
    "return o;"
)

_PROBE_JS = PROBE_JS

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


PROJECT_EXTS = (".eprj2", ".eprj", ".epro2", ".epro")
CONFIG_NAME = "config.json"

def user_data_dir():
    """Where the editor keeps its configuration.

    Measured on this machine: OneDrive has redirected Documents, so the real
    directory is `~/OneDrive/Documents/LCEDA-Pro` and a bare `~/Documents` lookup
    finds nothing. Candidates are tried in order and the first one holding a
    config.json wins; the plain path is kept as the fallback so a caller always
    gets an answer it can print. `LCEDA_PRO_USER_DIR` overrides everything.
    """
    override = os.environ.get("LCEDA_PRO_USER_DIR")
    if override:
        return override
    home = os.path.expanduser("~")
    candidates = [
        os.path.join(home, "Documents", "LCEDA-Pro"),
        os.path.join(home, "OneDrive", "Documents", "LCEDA-Pro"),
        os.path.join(home, "OneDrive", "文档", "LCEDA-Pro"),
        os.path.join(home, "文档", "LCEDA-Pro"),
    ]
    for path in candidates:
        if os.path.isfile(os.path.join(path, CONFIG_NAME)):
            return path
    return candidates[0]

def sqlite_version(path):
    """(db_version, tables) of a project database, read-only. (None, []) if not one.

    Read through SQLite itself rather than by scanning strings: `db_versions` is the
    editor's own answer to "what version wrote this file", and it is the only
    discriminator that survived measurement - the container (SQLite vs ZIP) does
    NOT separate old from current files.
    """
    try:
        con = sqlite3.connect("file:%s?mode=ro" % os.path.abspath(path).replace("\\", "/"),
                              uri=True)
    except Exception:
        return None, []
    try:
        tables = [str(row[0]) for row in
                  con.execute("select name from sqlite_master where type='table'")]
    except Exception:
        tables = []
    version = None
    if "db_versions" in tables:
        try:
            for row in con.execute("select * from db_versions"):
                if len(row) >= 2 and str(row[0]).lower() == "sqlite":
                    version = str(row[1])
        except Exception:
            version = None
    try:
        con.close()
    except Exception:
        pass
    return version, tables

def project_container(path):
    """What the file on disk is: container, format version, whether an upgrade ask is due.

    Measured on this install (2026-11-07), and the earlier rule here was wrong:

        D:\\lcedapro\\project\\F412RG 核心板 LCEDA.eprj2   SQLite  26.08.19.1  current
        C:\\...\\example-projects\\示例工程_3D外壳设计.eprj    SQLite  0.0.3        legacy
        ...\\ProPrj_*.epro2                                  ZIP     -            current

    So "SQLite = legacy" would refuse every project the editor is actually using.
    The version string is the discriminator; the extension is the tie-break when a
    file cannot be opened as a database.
    """
    extension = os.path.splitext(path)[1].lower()
    try:
        with open(path, "rb") as handle:
            head = handle.read(16)
    except OSError as exc:
        return {"container": "unreadable", "extension": extension, "db_version": None,
                "needs_upgrade": False, "why": "cannot read the file: %s" % exc}
    if head.startswith(b"SQLite format 3"):
        version, tables = sqlite_version(path)
        if version is not None:
            legacy = version.startswith("0.0.")
            why = "SQLite database, db_versions=sqlite %s -> %s" % (
                version, "legacy: 4.1.60 asks before opening it" if legacy
                else "current format, no upgrade ask expected")
        else:
            legacy = extension == ".eprj"
            why = ("SQLite database with no readable db_versions; judged by the "
                   "extension %s (legacy only if .eprj)" % (extension or "?"))
        return {"container": "sqlite", "extension": extension,
                "db_version": version,
                "legacy_projects_table": "projects" in tables,
                "needs_upgrade": bool(legacy), "why": why}
    if head.startswith(b"PK\x03\x04"):
        return {"container": "zip", "extension": extension, "db_version": None,
                "needs_upgrade": False,
                "why": "ZIP archive (.epro2/.epro); current format, no upgrade ask "
                       "expected"}
    return {"container": "unknown", "extension": extension, "db_version": None,
            "needs_upgrade": False,
            "why": "neither SQLite nor ZIP; the format is not known here"}

def install_config(user_data=None):
    """The editor's own install configuration, read only.

    This is where the project locations actually live. `eda.dmt_Project
    .getAllProjectsUuid()` measured [] on a machine holding 7 current projects, so
    the configuration (and the disk behind it) is the authority, not that API.
    """
    base = user_data or user_data_dir()
    path = os.path.join(base, CONFIG_NAME)
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        return {"path": path, "found": False, "error": str(exc)}
    def _list(key):
        value = data.get(key)
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            return [str(v) for v in value if v]
        return []
    return {
        "path": path,
        "found": True,
        "type": data.get("type"),
        "online_service": data.get("onlineService"),
        "project_dirs": _list("APP_PROJECT_DIR"),
        "library_dirs": _list("LIB_DIR"),
        "recovery_dir": (data.get("TMP_BACKUP_DIR") or None),
        "online_backup_dir": (data.get("ONLINE_BACKUP_DIR") or None),
    }

def project_roots(user_data=None):
    """[{role, path, exists}] - every directory the editor stores projects in."""
    config = install_config(user_data)
    roots = []
    for path in config.get("project_dirs") or []:
        roots.append({"role": "project", "path": path})
    for key, role in (("recovery_dir", "recovery"),
                      ("online_backup_dir", "online-backup")):
        path = config.get(key)
        if path:
            roots.append({"role": role, "path": path})
    for item in roots:
        item["exists"] = os.path.isdir(item["path"])
    return roots

def projects_on_disk(user_data=None, limit=200, deep=True, max_depth=2):
    """The projects that exist on this machine, from disk, with their real format.

    Separate from the editor on purpose: this answers "is there anything to open"
    without asking an API that may be sitting on an unanswered dialog. `source`
    says where each root came from, so a caller can tell a live project directory
    (the ones the editor shows) from a recovery/online backup.
    """
    config = install_config(user_data)
    roots = project_roots(user_data)
    found, errors = [], []
    for root in roots:
        base = root["path"]
        if not root.get("exists"):
            errors.append({"path": base, "role": root["role"],
                           "error": "directory does not exist"})
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            depth = os.path.relpath(dirpath, base).count(os.sep)
            if depth >= max_depth:
                dirnames[:] = []
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for name in filenames:
                if os.path.splitext(name)[1].lower() not in PROJECT_EXTS:
                    continue
                full = os.path.join(dirpath, name)
                try:
                    stat = os.stat(full)
                except OSError as exc:
                    errors.append({"path": full, "error": str(exc)})
                    continue
                entry = {
                    "name": os.path.splitext(name)[0],
                    "file": name,
                    "path": full,
                    "source": root["role"],
                    "size_mb": round(stat.st_size / (1024.0 * 1024.0), 2),
                    "mtime": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
                }
                if deep:
                    try:
                        container = project_container(full)
                    except Exception as exc:
                        container = {"container": "unreadable", "why": str(exc),
                                     "needs_upgrade": False, "db_version": None}
                    entry["extension"] = container.get("extension")
                    entry["container"] = container.get("container")
                    entry["db_version"] = container.get("db_version")
                    entry["needs_upgrade"] = container.get("needs_upgrade")
                found.append(entry)
                if len(found) >= limit:
                    errors.append({"path": base, "error": "stopped at limit %d" % limit})
                    break
    found.sort(key=lambda item: item.get("mtime") or "", reverse=True)
    return {"config": config, "roots": roots, "projects": found,
            "total": len(found), "errors": errors}

def disk_project_count(user_data=None, live_only=True):
    """How many project files exist on disk - a cheap, never-raising count.

    Used to keep `project_state()` honest: a zero from the in-app API cannot be
    reported as "nothing to load" without this number next to it.
    """
    try:
        listing = projects_on_disk(user_data, deep=False)
        items = listing["projects"]
    except Exception:
        return 0
    if live_only:
        items = [item for item in items if item.get("source") == "project"]
    return len(items)

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
        self.last_open = None
        self.open_session = None

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

    def project_state(self, session=None, timeout_s=15.0, scan_others=True):
        """Whether a project is loaded, read from the documents the editor lists.

        Measured 2026-11-07 against a project that had certainly opened (window title
        "F412RG 核心板 LCEDA | 嘉立创EDA(专业版)", file on disk):

            eda.dmt_Pcb.getAllPcbsInfo()              answered: 2 PCB documents
            eda.dmt_Schematic.getAllSchematicsInfo()   answered: 1 schematic (1 page)
            eda.dmt_Project.getCurrentProjectInfo()    NEVER settled (45 s at a time)
            eda.dmt_Project.getAllProjectsUuid()       answered [] - always

        Two corrections follow from that. The gate is no longer a non-empty
        getCurrentProjectInfo (it can never be one), and a session is not the editor:
        a session is ONE document window, and a second open can create a render that
        never answers while an earlier render still holds the project. So every live
        session is read, with a small per-session budget, and the answer says which
        one holds the documents instead of reporting the newest and stopping.
        """
        live = []
        try:
            live = [s for s in self.sessions(timeout_s) if s.get("status") == "alive"]
        except LiveError:
            live = []
        named = session or self.open_session
        ids = [s.get("sessionId") for s in live]
        if not ids:
            # No listing (or none alive): still try the one session we were given or
            # that this client opened, because a listing failure is not proof that no
            # document can be read.
            ids = [named] if named else []
        if not ids:
            return {"loaded": False, "session": None, "session_loaded": False,
                    "pcb_count": None, "sch_count": None, "project_uuid": None,
                    "pcbs": [], "schematics": [], "project_count": None,
                    "workspace": None, "projects_on_disk": disk_project_count(),
                    "why": "the editor has no live session"}
        ordered = ([named] if named in ids else []) + [i for i in ids if i != named]
        if not scan_others:
            ordered = ordered[:1]
        per = max(8.0, min(timeout_s, 15.0))
        probe_error = None
        chosen = None
        checked = []
        for sid in ordered:
            entry = {"session": sid, "loaded": False}
            try:
                value = (self.invoke(sid, _PROBE_JS, timeout_ms=int(per * 1000))
                         .get("value") or {})
            except LiveError as exc:
                entry["error"] = exc.code
                probe_error = "%s: %s" % (exc.code, exc.message)
                checked.append(entry)
                continue
            pcb_count = value.get("pcbCount") or 0
            sch_count = value.get("schCount") or 0
            entry.update({"pcb_count": pcb_count, "sch_count": sch_count,
                          "loaded": bool(pcb_count or sch_count),
                          "pcb_error": value.get("pcbError"),
                          "sch_error": value.get("schError")})
            checked.append(entry)
            if entry["loaded"] and chosen is None:
                chosen = (sid, value, entry)
            if chosen is not None and sid == ordered[0]:
                break
        state = {"loaded": chosen is not None,
                 "session": (chosen[0] if chosen else ordered[0]),
                 "session_loaded": bool(chosen and ordered and chosen[0] == ordered[0]),
                 "sessions_checked": checked,
                 "pcb_count": None, "sch_count": None, "project_uuid": None,
                 "pcbs": [], "schematics": [], "project_count": None,
                 "workspace": None, "projects_on_disk": disk_project_count()}
        if chosen:
            sid, value, entry = chosen
            state.update({
                "pcb_count": entry["pcb_count"], "sch_count": entry["sch_count"],
                "project_uuid": value.get("projectUuid"),
                "pcbs": value.get("pcbs") or [],
                "schematics": value.get("schematics") or [],
                "project_count": value.get("projectCount"),
                "workspace": value.get("workspace"),
                "pcb_error": value.get("pcbError"),
                "sch_error": value.get("schError")})
            if len(checked) > 1 and sid != ordered[0]:
                state["held_by"] = sid
                state["why"] = ("session %s exposes no document, but session %s holds "
                                "%d PCB(s) and %d schematic(s)"
                                % (ordered[0], sid, entry["pcb_count"],
                                   entry["sch_count"]))
            else:
                state["why"] = ("the editor lists %d PCB document(s) and %d "
                                "schematic(s) for the current project"
                                % (entry["pcb_count"], entry["sch_count"]))
            partial = [name for name, key in (("PCB", "pcb_error"),
                                              ("schematic", "sch_error"))
                       if state.get(key)]
            if partial:
                state["why"] += ("; the %s list could not be read (%s), so that half "
                                 "of the count is unmeasured, not zero"
                                 % (" and ".join(partial),
                                    state.get("pcb_error") or state.get("sch_error")))
            return state
        # Nothing loaded anywhere. Say which failure it was, because "empty project"
        # and "the probe could not run" are different facts.
        answered_empty = [e for e in checked if "error" not in e]
        if answered_empty:
            first = answered_empty[0]
            state["pcb_count"] = first.get("pcb_count") or 0
            state["sch_count"] = first.get("sch_count") or 0
            state["why"] = ("every live session lists no PCB and no schematic "
                            "document - the editor holds an empty project, or the one "
                            "being opened has not finished")
        else:
            state["why"] = ("no session could be read (%s); getAllProjectsUuid() is "
                            "not a project list on this build and says nothing about "
                            "the %d project file(s) on disk"
                            % (probe_error or "no live session",
                               disk_project_count()))
            state["probe_error"] = probe_error
            try:
                cheap = (self.invoke(ordered[0], CHEAP_PROBE_JS,
                                     timeout_ms=int(per * 1000)).get("value") or {})
                state["project_count"] = cheap.get("projectCount")
                state["workspace"] = cheap.get("workspace")
            except LiveError:
                pass
        return state

    def open_project(self, path, headless=False, timeout_s=45.0, canary_s=3.0,
                     verify_content=True, allow_upgrade=False):
        """Open a project, then prove the editor is still able to answer.

        Opening a project is the one call on this pipe that can take the editor's
        own render process down: measured once on 4.1.60 with a 32 MB .eprj opened
        headless, the processes were gone and the window sat on "未找到 Bridge 服务，
        3 秒后重试" while the pipe stayed open. The mechanism is NOT known, so this
        method does not claim to prevent it - it refuses to hide it:

          * a ping BEFORE the open, so a half-dead editor is not asked to open
            anything (that state is how the pipe gets wedged in the first place),
          * a ping AFTER the reply, so a session id is never handed back as if it
            were usable when the app that owns it has stopped answering.

        `last_open` keeps the timing and the canary result; nothing is added to the
        application's own reply.
        """
        if not path:
            raise LiveError("ARG_REQUIRED", "path is required")
        path = os.path.abspath(path)
        if not os.path.exists(path):
            raise LiveError("ARG_INVALID", "no such project file: %s" % path)
        size = os.path.getsize(path)
        container = project_container(path)
        if container["needs_upgrade"] and not allow_upgrade:
            raise LiveError(
                "PROJECT_NEEDS_UPGRADE",
                "%s is a legacy container (%s), so the editor will answer 'open' with "
                "a session id and then sit on '当前工程为V3以下版本 ... 是否确认打开该工"
                "程?' - nothing after that point would touch a project, and the dialog "
                "cannot be answered from this bridge (`document` is undefined inside "
                "it)" % (os.path.basename(path), container["why"]),
                {
                    "path": path,
                    "size_mb": round(size / (1024.0 * 1024.0), 1),
                    "container": container,
                    "next": "convert it once by opening it in the editor by hand and "
                            "saving as .epro2, or pass allow_upgrade=true and answer "
                            "the dialog in the window yourself",
                },
            )
        before = self.ping(min(canary_s, 5.0))
        started = time.time()
        reply = self._call("open", {"path": path, "headless": bool(headless)}, timeout_s)
        value = reply.get("value") or {}
        elapsed = time.time() - started
        try:
            after = self.ping(canary_s)
        except LiveError as exc:
            raise LiveError(
                "APP_DIED_DURING_OPEN",
                "the editor answered 'open' in %.1f s and then stopped answering "
                "(%s: %s)" % (elapsed, exc.code, exc.message),
                {
                    "path": path,
                    "size_bytes": size,
                    "size_mb": round(size / (1024.0 * 1024.0), 1),
                    "headless": bool(headless),
                    "open_seconds": round(elapsed, 2),
                    "open_reply": value,
                    "bridge_before": {"code": "OK",
                                      "bridge_version": (before or {}).get("bridgeVersion")},
                    "bridge_after": {"code": exc.code, "message": exc.message,
                                     "endpoint": self.endpoint},
                    "app_state": "the window shows '未找到 Bridge 服务，3 秒后重试' "
                                  "and this app does not rebuild the pipe on its own",
                    "next": "lceda_pro_recover reports the pipe state and, with "
                            "confirm=true, restarts the editor and waits for the "
                            "pipe to answer again",
                },
            )
        self.last_open = {
            "path": path,
            "size_bytes": size,
            "headless": bool(headless),
            "open_seconds": round(elapsed, 2),
            "canary_before": (before or {}).get("bridgeVersion"),
            "canary_after": (after or {}).get("bridgeVersion"),
        }
        session = value.get("sessionId")
        self.open_session = session
        if verify_content:
            state = self.project_state(session, max(canary_s, 5.0))
            self.last_open["content"] = state
            if not state.get("session_loaded"):
                held = state.get("held_by")
                if held:
                    message = (
                        "the editor answered 'open' with session %s, but that session "
                        "exposes no document while session %s already holds the "
                        "project (%s) - this open added no loaded document"
                        % (session, held, state.get("why")))
                    app_state = ("a project is open in the editor, but not in the "
                                 "session this call created")
                    nxt = ("lceda_pro_sessions lists every session with its render id, "
                           "so the document can be driven by naming that session; "
                           "lceda_pro_verify gives the project-wide answer")
                else:
                    message = ("the editor answered 'open' with session %s and still "
                               "answers ping, but that session exposes no PCB and no "
                               "schematic (%s)" % (session, state.get("why")))
                    app_state = ("no document in the session this open created; that "
                                 "is what a blocking dialog or an unfinished load "
                                 "looks like from here")
                    nxt = ("look at the window: a dialog can only be clicked there "
                           "(the injected command space has no `document`); then "
                           "lceda_pro_verify re-reads the state")
                raise LiveError(
                    "OPEN_NOT_CONFIRMED", message,
                    {
                        "path": path,
                        "size_mb": round(size / (1024.0 * 1024.0), 1),
                        "open_seconds": round(elapsed, 2),
                        "open_reply": value,
                        "content_probe": state,
                        "app_state": app_state,
                        "next": nxt,
                    },
                )
        return value

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


def pipe_report(prefix=APP_NAME, ping_timeout_s=3.0):
    """What the command space looks like right now - one read, no repair.

    Three states, and the difference matters because only the first two are
    recoverable by starting the editor again:

      healthy      a pipe answered a ping (bridge_version is filled in)
      bridge_dead  a pipe with the right name exists but nothing answers it. This
                   is the state behind the editor's own "未找到 Bridge 服务，3 秒后
                   重试" dialog; the app never rebuilds the pipe without a restart,
                   and a restart is a NEW process, so anything unsaved in the old
                   one is gone whatever we do next.
      not_running  no pipe with that name at all
    """
    names = available_endpoints(prefix)
    out = {
        "pipe_root": PIPE_ROOT,
        "pipes": names,
        "answering": None,
        "bridge_version": None,
        "failures": [],
    }
    for name in names:
        client = Client(PipeTransport(name))
        try:
            identity = client.ping(ping_timeout_s)
        except LiveError as exc:
            out["failures"].append(
                {"endpoint": name, "code": exc.code, "message": exc.message}
            )
            continue
        finally:
            client.close()
        out["answering"] = name
        out["bridge_version"] = (identity or {}).get("bridgeVersion")
        out["identity"] = identity
        break
    out["state"] = (
        "healthy" if out["answering"] else ("bridge_dead" if names else "not_running")
    )
    return out

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
