"""Desktop layer: find, start, stop and point the EDA applications at a project.

Why an MCP server needs this at all: the model cannot click. Every question about
"is the tool open", "which project is loaded", "did my export show up" has to be
answered from the operating system, not from a claim. The failure mode being
protected against is the one mdk_agent hit with Keil: a *stale instance* keeps
serving commands, so the answer comes from a board that is no longer the one on
screen.

Design rules:

* **ctypes only.** No `tasklist`, no `wmic`, no `netstat`, no PowerShell: those
  are slow, localised, and unavailable in some sandboxes.
* **Never guess an answer.** If a window title does not contain the project name,
  the project is `unknown`, not "probably this one".
* **Never touch project files.** This module starts and stops processes and reads
  process/window/port state. It does not open project files for writing, does not
  move them, and does not delete anything. `close()` sends WM_CLOSE and only if
  explicitly asked calls TerminateProcess. A user's project is never an input to
  a destructive operation here.
"""
import ctypes
import os
import socket
import subprocess
import sys
import time
from ctypes import wintypes

IS_WINDOWS = sys.platform == "win32"

# ------------------------------------------------------------------ app table
# `open_argv` says how a project path is handed to the executable. `verified`
# records whether that argument form has been observed on a real install; an
# unverified form is still used (best effort) but the answer says so.
APPS = {
    "lceda-pro": {
        "display": "嘉立创EDA(专业版) / LCEDA Pro",
        "exe_names": ("lceda-pro.exe",),
        "candidate_subdirs": (
            r"LCEDApro\lceda-pro",
            r"LCEDA-Pro\lceda-pro",
            r"Program Files\LCEDA Pro",
            r"Program Files\lceda-pro",
            r"Program Files (x86)\LCEDA Pro",
            r"LCEDA Pro",
            r"lceda-pro",
        ),
        "project_exts": (".epro",),
        "title_regex": r"^\s*(?P<proj>[^-–|]+?)\s*[-–|]",
        "api_gateway_ports": (49620, 49621, 49622, 49623, 49624,
                              49625, 49626, 49627, 49628, 49629),
        "open_argv": "<exe> <project.epro>",
        "open_argv_verified": False,
        "notes": ("Two doors, measured 2026-10-28: the Pro API gateway listens on "
                  "49620-49629 only after '外部交互权限' is enabled (no listener here), "
                  "while the editor's own command-space pipe "
                  "(\\\\.\\pipe\\JLCEDAPro<8 hex>) is open whenever the app is "
                  "running and answers ping/open/session/invoke - see lceda_pro_*. "
                  "Launch, measured 2026-11-03: the earlier note here (rc 0 in 0.5 s from a "
                  "shell, fine via explorer) was the symptom of "
                  "ELECTRON_RUN_AS_NODE=1 being inherited: Electron then runs as a "
                  "bare node REPL and exits at once. With that variable cleared the "
                  "app starts and the pipe appears in about 1 s from any launcher - "
                  "including this one, which strips it. The argv form for attaching a "
                  "project is still unverified."),
    },
    "kicad": {
        "display": "KiCad (project manager)",
        "exe_names": ("kicad.exe",),
        "candidate_subdirs": (
            r"KiCad\{ver}\bin",
            r"Program Files\KiCad\{ver}\bin",
            r"Program Files (x86)\KiCad\{ver}\bin",
            r"KiCad\bin",
        ),
        "project_exts": (".kicad_pro",),
        "title_regex": r"(?P<proj>[^\\/:*?\"<>|]+\.kicad_pro)",
        # KiCad 7+ shows "<project> - KiCad" once a project is loaded and drops the
        # extension; the strict pattern above misses that, so fall back to the
        # separator form and reject the "no project" titles.
        "title_fallback_regex": r"^\s*(?P<proj>[^-\u2013\u2014|]+?)\s*[-\u2013\u2014]\s*(?:KiCad|kicad|pcbnew)\b",
        "title_reject": ("kicad", "kicad e.d.a.", "pcbnew", ""),
        "api_gateway_ports": (),
        "open_argv": "<exe> <project.kicad_pro>",
        "open_argv_verified": True,
        "notes": "KiCad opens the project given on the command line.",
    },
    "pcbnew": {
        "display": "KiCad PCB Editor (pcbnew)",
        "exe_names": ("pcbnew.exe",),
        "candidate_subdirs": (
            r"KiCad\{ver}\bin",
            r"Program Files\KiCad\{ver}\bin",
            r"Program Files (x86)\KiCad\{ver}\bin",
            r"KiCad\bin",
        ),
        "project_exts": (".kicad_pcb",),
        "title_regex": r"(?P<proj>[^\\/:*?\"<>|]+\.kicad_pcb)",
        "title_fallback_regex": r"^\s*(?P<proj>[^-\u2013\u2014|]+?)\s*[-\u2013\u2014]\s*(?:KiCad|kicad|pcbnew)\b",
        "title_reject": ("kicad", "kicad e.d.a.", "pcbnew", ""),
        "api_gateway_ports": (),
        "open_argv": "<exe> <board.kicad_pcb>",
        "open_argv_verified": True,
        "notes": "Opening a board directly gives a PCB editor window without the manager.",
    },
    "kicad-cli": {
        "display": "KiCad CLI (headless: DRC, gerbers, drill, step)",
        "exe_names": ("kicad-cli.exe",),
        "candidate_subdirs": (
            r"KiCad\{ver}\bin",
            r"Program Files\KiCad\{ver}\bin",
            r"Program Files (x86)\KiCad\{ver}\bin",
            r"KiCad\bin",
        ),
        "project_exts": (".kicad_pcb", ".kicad_pro"),
        "title_regex": None,
        "api_gateway_ports": (),
        "open_argv": "<exe> (one-shot, no window)",
        "open_argv_verified": True,
        "notes": "Not a GUI: use it for DRC/export so a machine-checkable result exists "
                 "instead of a screenshot.",
    },
}


class DesktopError(Exception):
    pass


# --------------------------------------------------------------- win helpers
def _kernel32():
    return ctypes.windll.kernel32


def _user32():
    return ctypes.windll.user32


def list_processes():
    """[(pid, exe_name)] via Toolhelp snapshot. Empty list off Windows."""
    if not IS_WINDOWS:
        return []
    TH32CS_SNAPPROCESS = 0x00000002
    k32 = _kernel32()

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(wintypes.ULONG)),
            ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    out = []
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap in (-1, ctypes.c_void_p(-1).value):
        return out
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            try:
                out.append((int(entry.th32ProcessID), str(entry.szExeFile)))
            except Exception:                                    # noqa: BLE001
                pass
            ok = k32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        k32.CloseHandle(snap)
    return out


def pids_of(exe_names):
    wanted = {n.lower() for n in exe_names}
    return [pid for pid, name in list_processes() if name.lower() in wanted]


def process_path(pid):
    """Full image path of a pid (works for same-user processes without elevation)."""
    if not IS_WINDOWS:
        return None
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    k32 = _kernel32()
    handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return None
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if not k32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return None
        return buf.value
    finally:
        k32.CloseHandle(handle)


def process_created(pid):
    if not IS_WINDOWS:
        return None
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    k32 = _kernel32()

    class FILETIME(ctypes.Structure):
        _fields_ = [("dwLowDateTime", wintypes.DWORD),
                    ("dwHighDateTime", wintypes.DWORD)]

    handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return None
    try:
        created, exited, kernel, user = FILETIME(), FILETIME(), FILETIME(), FILETIME()
        if not k32.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                   ctypes.byref(kernel), ctypes.byref(user)):
            return None
        ticks = (int(created.dwHighDateTime) << 32) | int(created.dwLowDateTime)
        return ticks / 1e7 - 11644473600.0
    finally:
        k32.CloseHandle(handle)


def process_parent(pid):
    """Parent pid of `pid`, or None. Used to tell an app's main process from its
    own children without reading command lines.

    Measured 2026-11-07 on the LCEDA install: 7 processes named `lceda-pro.exe` are
    alive for ONE editor - the main process plus crashpad-handler, gpu-process,
    NetworkService and three renderers, and every child's parent is the main pid.
    Counting the name therefore counts 7 editors that do not exist; the only sound
    definition of "an instance" is "a process of this exe whose parent is not".
    """
    if not IS_WINDOWS:
        return None

    class PROCESS_BASIC_INFORMATION(ctypes.Structure):
        _fields_ = [("Reserved1", ctypes.c_void_p),
                    ("PebBaseAddress", ctypes.c_void_p),
                    ("Reserved2", ctypes.c_void_p * 2),
                    ("UniqueProcessId", ctypes.c_void_p),
                    ("InheritedFromUniqueProcessId", ctypes.c_void_p)]

    k32 = _kernel32()
    handle = k32.OpenProcess(0x1000, False, int(pid))     # QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        info = PROCESS_BASIC_INFORMATION()
        written = ctypes.c_ulong(0)
        status = ctypes.windll.ntdll.NtQueryInformationProcess(
            handle, 0, ctypes.byref(info), ctypes.sizeof(info), ctypes.byref(written))
        if status != 0:
            return None
        return int(info.InheritedFromUniqueProcessId or 0) or None
    except Exception:                                        # noqa: BLE001
        return None
    finally:
        k32.CloseHandle(handle)

def process_alive(pid):
    return any(p == int(pid) for p, _ in list_processes())


# Measured 2026-11-07 on LCEDA 4.1.60: ONE editor exposes TWO visible top-level
# windows, same title, same rect - the frame plus Chromium's helper window, which is
# marked WS_EX_TOOLWINDOW (0x80). Counting both makes one editor read as two, which
# is exactly how "you opened too many" gets reported. Tool windows are therefore
# listed separately and never counted as editor windows.
WS_EX_TOOLWINDOW = 0x00000080

def windows_of(pids, include_tool=False):
    """Visible top-level windows owned by pids: [{pid, hwnd, title, tool, rect}]."""
    if not IS_WINDOWS or not pids:
        return []
    owners = {int(p) for p in pids}
    u32 = _user32()
    out = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _cb(hwnd, _lparam):
        try:
            if not u32.IsWindowVisible(hwnd):
                return True
            pid = wintypes.DWORD(0)
            u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if int(pid.value) not in owners:
                return True
            buf = ctypes.create_unicode_buffer(512)
            u32.GetWindowTextW(hwnd, buf, 512)
            ex = u32.GetWindowLongW(hwnd, -20)
            tool = bool((ex or 0) & WS_EX_TOOLWINDOW)
            if tool and not include_tool:
                return True

            class _RECT(ctypes.Structure):
                _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                            ("right", wintypes.LONG), ("bottom", wintypes.LONG)]
            rect = _RECT()
            area = 0
            if u32.GetWindowRect(hwnd, ctypes.byref(rect)):
                area = max(0, rect.right - rect.left) * max(0, rect.bottom - rect.top)
            out.append({"pid": int(pid.value), "hwnd": int(hwnd), "title": buf.value,
                        "tool": tool, "area": area})
        except Exception:                                        # noqa: BLE001
            pass
        return True

    u32.EnumWindows(_cb, 0)
    return out


def close_window(hwnd, timeout_s=6.0):
    """Ask one window to close (WM_CLOSE), then wait until it is gone.

    WM_CLOSE, not TerminateProcess: the app decides what to do about unsaved state,
    and a window that refuses to close stays visible (reported as still_open) instead
    of being killed behind the user's back.
    """
    if not IS_WINDOWS or not hwnd:
        return {"status": "error", "reason_code": "ARG_INVALID",
                "message": "no window handle to close"}
    WM_CLOSE = 0x0010
    u32 = _user32()
    hwnd = int(hwnd)
    if not u32.IsWindow(hwnd):
        return {"status": "ok", "hwnd": hwnd, "closed": True, "still_open": False,
                "why": "the window was already gone"}
    u32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
    waited = 0.0
    step = 0.1
    while waited < float(timeout_s):
        if not u32.IsWindow(hwnd):
            return {"status": "ok", "hwnd": hwnd, "closed": True, "still_open": False,
                    "waited_s": round(waited, 2)}
        time.sleep(step)
        waited += step
    return {"status": "ok", "hwnd": hwnd, "closed": False, "still_open": True,
            "waited_s": round(waited, 2),
            "why": "WM_CLOSE was posted but the window is still there - the app is "
                   "asking something, answer it in the window"}

def focus_window(hwnd):
    if not IS_WINDOWS or not hwnd:
        return False
    try:
        u32 = _user32()
        u32.ShowWindow(int(hwnd), 9)                 # SW_RESTORE
        return bool(u32.SetForegroundWindow(int(hwnd)))
    except Exception:                                            # noqa: BLE001
        return False


_TCP_TABLE_OWNER_PID_LISTENER = 3
_AF_INET = 2


class _MIB_TCPROW_OWNER_PID(ctypes.Structure):
    _fields_ = [("dwState", wintypes.DWORD), ("dwLocalAddr", wintypes.DWORD),
                ("dwLocalPort", wintypes.DWORD), ("dwRemoteAddr", wintypes.DWORD),
                ("dwRemotePort", wintypes.DWORD), ("dwOwningPid", wintypes.DWORD)]


def tcp_listeners():
    """[(port, pid)] from the OS IPv4 listener table. Empty when unavailable."""
    if not IS_WINDOWS:
        return []
    try:
        iphlpapi = ctypes.windll.iphlpapi
        size = wintypes.DWORD(0)
        ret = iphlpapi.GetExtendedTcpTable(None, ctypes.byref(size), False, _AF_INET,
                                           _TCP_TABLE_OWNER_PID_LISTENER, 0)
        if ret not in (0, 122) or not size.value:
            return []
        buf = ctypes.create_string_buffer(size.value)
        ret = iphlpapi.GetExtendedTcpTable(buf, ctypes.byref(size), False, _AF_INET,
                                           _TCP_TABLE_OWNER_PID_LISTENER, 0)
        if ret != 0:
            return []
        count = ctypes.cast(buf, ctypes.POINTER(wintypes.DWORD))[0]
        rows = ctypes.cast(ctypes.addressof(buf) + ctypes.sizeof(wintypes.DWORD),
                           ctypes.POINTER(_MIB_TCPROW_OWNER_PID))
        out = []
        for i in range(int(count)):
            row = rows[i]
            out.append((socket.ntohs(int(row.dwLocalPort) & 0xFFFF),
                        int(row.dwOwningPid)))
        return out
    except Exception:                                            # noqa: BLE001
        return []


def port_listening(port, timeout=0.3):
    """Listener table first (OS bookkeeping beats a connect probe), then connect."""
    if IS_WINDOWS:
        rows = tcp_listeners()
        if rows:
            return any(p == int(port) for p, _ in rows)
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def port_owner(port):
    return sorted({pid for p, pid in tcp_listeners() if p == int(port)})


def file_version(path):
    """`a.b.c.d` from the PE version resource, or None."""
    if not IS_WINDOWS or not path:
        return None
    try:
        version = ctypes.windll.version
        size = version.GetFileVersionInfoSizeW(path, None)
        if not size:
            return None
        buf = ctypes.create_string_buffer(size)
        if not version.GetFileVersionInfoW(path, 0, size, buf):
            return None
        ptr = ctypes.c_void_p()
        length = wintypes.UINT()
        if not version.VerQueryValueW(buf, "\\", ctypes.byref(ptr), ctypes.byref(length)):
            return None

        class VS_FIXEDFILEINFO(ctypes.Structure):
            _fields_ = [("dwSignature", wintypes.DWORD),
                        ("dwStrucVersion", wintypes.DWORD),
                        ("dwFileVersionMS", wintypes.DWORD),
                        ("dwFileVersionLS", wintypes.DWORD),
                        ("dwProductVersionMS", wintypes.DWORD),
                        ("dwProductVersionLS", wintypes.DWORD),
                        ("dwFileFlagsMask", wintypes.DWORD),
                        ("dwFileFlags", wintypes.DWORD),
                        ("dwFileOS", wintypes.DWORD),
                        ("dwFileType", wintypes.DWORD),
                        ("dwFileSubtype", wintypes.DWORD),
                        ("dwFileDateMS", wintypes.DWORD),
                        ("dwFileDateLS", wintypes.DWORD)]

        info = ctypes.cast(ptr, ctypes.POINTER(VS_FIXEDFILEINFO)).contents
        ms, ls = int(info.dwFileVersionMS), int(info.dwFileVersionLS)
        return "%d.%d.%d.%d" % (ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF)
    except Exception:                                            # noqa: BLE001
        return None


def logical_drives():
    if not IS_WINDOWS:
        return []
    return ["%s:\\" % ch for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
            if os.path.isdir("%s:\\" % ch)]


def _drive_candidates(subdirs, exe_names):
    """Every existing exe match under the well-known install subdirs of each drive."""
    out = []
    for root in logical_drives():
        for sub in subdirs:
            if "{ver}" in sub:
                head, tail = sub.split("{ver}")
                parent = os.path.join(root, head.rstrip("\\/"))
                try:
                    versions = sorted(os.listdir(parent), reverse=True)
                except OSError:
                    continue
                for ver in versions:
                    for exe in exe_names:
                        cand = os.path.join(parent, ver, tail.lstrip("\\/"), exe)
                        if os.path.isfile(cand):
                            out.append(cand)
            else:
                for exe in exe_names:
                    cand = os.path.join(root, sub, exe)
                    if os.path.isfile(cand):
                        out.append(cand)
    return out


def _registry_candidates(needles, exe_names):
    """InstallLocation from Uninstall keys whose DisplayName matches a needle."""
    if not IS_WINDOWS:
        return []
    try:
        import winreg
    except ImportError:
        return []
    needles = [n.lower() for n in needles if n]
    out = []
    roots = [
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER,
         r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    for hive, sub in roots:
        try:
            key = winreg.OpenKey(hive, sub, 0, winreg.KEY_READ)
        except OSError:
            continue
        try:
            count = winreg.QueryInfoKey(key)[0]
            for i in range(count):
                try:
                    sub_name = winreg.EnumKey(key, i)
                except OSError:
                    continue
                try:
                    with winreg.OpenKey(hive, sub + "\\" + sub_name, 0,
                                        winreg.KEY_READ) as entry:
                        try:
                            display = str(winreg.QueryValueEx(entry, "DisplayName")[0])
                        except OSError:
                            continue
                        if not any(n in display.lower() for n in needles):
                            continue
                        try:
                            location = str(winreg.QueryValueEx(entry, "InstallLocation")[0])
                        except OSError:
                            continue
                        if not location:
                            continue
                        for exe in exe_names:
                            for depth in ("", "bin"):
                                cand = os.path.join(location, depth, exe)
                                if os.path.isfile(cand):
                                    out.append(cand)
                except OSError:
                    continue
        finally:
            key.Close()
    return out


# ------------------------------------------------------------------ detection
def detect(app, explicit=None):
    """Locate an application. Returns where it was found and everything tried.

    Order: explicit path -> running process image -> drive candidates -> registry.
    A running process is the strongest evidence: its image path cannot be wrong.
    """
    spec = APPS.get(app)
    if spec is None:
        raise DesktopError("unknown app: %r (known: %s)" % (app, ", ".join(APPS)))
    tried = []
    if explicit:
        if os.path.isfile(explicit):
            return _found(app, explicit, "explicit path", spec, tried)
        tried.append("explicit path %s: not a file" % explicit)

    for pid in pids_of(spec["exe_names"]):
        path = process_path(pid)
        if path and os.path.isfile(path):
            return _found(app, path, "running process (pid %d)" % pid, spec, tried)

    for cand in _drive_candidates(spec["candidate_subdirs"], spec["exe_names"]):
        return _found(app, cand, "drive scan", spec, tried)

    needles = [spec["display"].split("/")[0].strip()]
    if app == "lceda-pro":
        needles += ["嘉立创EDA", "LCEDA", "立创EDA"]
    elif app.startswith("kicad") or app == "pcbnew":
        needles += ["KiCad"]
    for cand in _registry_candidates(needles, spec["exe_names"]):
        return _found(app, cand, "registry Uninstall key", spec, tried)

    tried.append("drive scan: no %s under %s"
                 % ("/".join(spec["exe_names"]), ", ".join(spec["candidate_subdirs"])))
    tried.append("registry: no matching Uninstall key")
    return {"app": app, "display": spec["display"], "found": False, "exe": None,
            "version": None, "source": None, "tried": tried,
            "notes": spec.get("notes")}


def _found(app, exe, source, spec, tried):
    return {"app": app, "display": spec["display"], "found": True, "exe": exe,
            "version": file_version(exe), "source": source,
            "install_dir": os.path.dirname(exe), "tried": tried,
            "notes": spec.get("notes")}


def detect_all(explicit=None):
    explicit = explicit or {}
    return {app: detect(app, explicit.get(app)) for app in APPS}


def parse_project_from_title(app, title):
    """Pull the project name out of a window title. Empty string when absent.

    Empty is the honest answer for "a window is open but no project is loaded" -
    it is not the same as "this window shows project X", and the caller must not
    treat the two as equivalent.
    """
    spec = APPS.get(app) or {}
    if not title:
        return ""
    import re
    reject = tuple(x.lower() for x in (spec.get("title_reject") or ()))
    for key in ("title_regex", "title_fallback_regex"):
        regex = spec.get(key)
        if not regex:
            continue
        match = re.search(regex, title)
        if not match:
            continue
        proj = (match.groupdict().get("proj") or "").strip()
        if proj.lower() in reject:
            return ""
        return proj
    return ""


def _instance_groups(app):
    """{main_pid: [pids]} for one app, or {} if it is not running.

    An Electron app is one instance spread over many processes. Grouping by parent
    is what makes `instances_total` mean what a user means by "how many editors are
    open"; anything counting exe names reports the child processes as extra editors.
    """
    spec = APPS[app]
    pids = pids_of(spec["exe_names"])
    if not pids:
        return {}
    known = set(pids)
    parent = {pid: process_parent(pid) for pid in pids}
    mains = [pid for pid in pids if parent.get(pid) not in known]
    groups = {pid: [pid] for pid in mains}
    # A child whose parent has already exited belongs to nobody: attach it to the
    # oldest main process so it is still reported (and never counted as an instance).
    for pid in pids:
        if pid in groups:
            continue
        owner = None
        walk = parent.get(pid)
        seen = set()
        while walk and walk not in seen:
            if walk in groups:
                owner = walk
                break
            seen.add(walk)
            walk = parent.get(walk)
        groups.setdefault(owner or mains[0], []).append(pid)
    return groups

def editor_windows(app):
    """Titled, visible, non-helper frames of an app - what a user calls a window."""
    out = []
    for inst in _instances(app):
        pids = inst.get("processes") or [inst["pid"]]
        for win in windows_of(pids, include_tool=True):
            if not win.get("title") or win.get("tool") or not win.get("area"):
                continue
            row = dict(win)
            row["instance_pid"] = inst["pid"]
            out.append(row)
    out.sort(key=lambda w: (w["pid"], w["hwnd"]))
    return out

def _instances(app):
    spec = APPS[app]
    groups = _instance_groups(app)
    if not groups:
        return []
    all_pids = [pid for members in groups.values() for pid in members]
    by_pid = {}
    for win in windows_of(all_pids, include_tool=True):
        # Only frames a user would call "an editor window": visible, titled, not a
        # Chromium helper. Measured: one editor showed 2 titled frames, so windows -
        # not processes, and titled frames - not raw HWNDs, is the unit that matters.
        if not win.get("title") or win.get("tool") or not win.get("area"):
            continue
        by_pid.setdefault(win["pid"], []).append(win)
    out = []
    for main_pid, members in groups.items():
        wins, titles = [], []
        for pid in members:
            for win in by_pid.get(pid) or []:
                wins.append(win)
                if win.get("title"):
                    titles.append(win["title"])
        title, hwnd = "", 0
        for win in wins:
            if win.get("title"):
                title, hwnd = win["title"], win["hwnd"]
                break
        out.append({
            "pid": main_pid,
            "created": process_created(main_pid),
            "title": title,
            "titles": titles,
            "window_count": len(wins),
            "hwnd": hwnd,
            "has_window": bool(wins),
            "project": parse_project_from_title(app, title),
            "exe": process_path(main_pid),
            "processes": members,
            "process_count": len(members),
        })
    out.sort(key=lambda x: (x["created"] is None, x["created"] or 0.0, x["pid"]))
    return out


def status(app, probe_ports=True):
    """Everything knowable about one application's live state."""
    spec = APPS.get(app)
    if spec is None:
        raise DesktopError("unknown app: %r" % app)
    inst = _instances(app)
    ports = {}
    if probe_ports:
        for port in spec.get("api_gateway_ports") or ():
            listening = port_listening(port)
            ports[str(port)] = {"listening": listening,
                                "owner_pids": port_owner(port) if listening else []}
    gateway_ready = any(v["listening"] for v in ports.values()) if ports else None
    return {
        "status": "ok",
        "app": app,
        "display": spec["display"],
        "running": bool(inst),
        "instances_total": len(inst),
        "processes_total": sum(i.get("process_count") or 1 for i in inst),
        "instances": inst,
        "projects_open": sorted({i["project"] for i in inst if i["project"]}),
        "api_gateway_ports": ports,
        "api_gateway_ready": gateway_ready,
        "notes": spec.get("notes"),
        "trust": ("instances/projects come from process + window enumeration; a project "
                  "name is reported only when a window title contains it"),
    }


# -------------------------------------------------------------------- launch
# Popen objects are held here for the life of the server: an unreferenced Popen
# that is still running is garbage collected with a ResourceWarning, and the whole
# point of launch() is to hand the process over to the OS, not to reap it.
_SPAWNED = []


# Measured 2026-11-03: with ELECTRON_RUN_AS_NODE=1 in the environment (this server
# was started by an Electron host, so the variable is inherited - not set by us),
# every Electron app becomes a plain Node interpreter: `--version` prints node's
# own version, a bare argument is looked up as a script, and with no arguments it
# starts a REPL that exits 0 the moment stdin is at EOF. The window never appears,
# no pipe is created, nothing is written to the app's log, and the caller sees a
# clean exit - the failure mode is a silent no-op, which is why it read as "the app
# will not start from a shell". Dropping the key is harmless for a non-Electron exe.
_DROP_FROM_CHILD_ENV = ("ELECTRON_RUN_AS_NODE",)

def _child_env():
    """(env, dropped) - the environment an EDA child should get, made explicit."""
    env = dict(os.environ)
    dropped = [k for k in _DROP_FROM_CHILD_ENV if env.pop(k, None) is not None]
    return env, dropped

def launch(exe, args=None, app=None, allow_second=False):
    """Start an exe detached from this process's job, so it outlives the MCP call.

    `app` is optional but should always be given for a known application: it turns
    this into a one-instance launcher. Measured failure this exists for: calling
    launch() for an editor that was already up produced a second window that shared
    no state with the first and competed for the same pipe name, and the original
    window silently stopped being the one being driven. Starting another process is
    therefore a decision, not a default - `allow_second` is the way to say it on
    purpose.
    """
    if not exe or not os.path.isfile(exe):
        return {"status": "error", "reason_code": "EXE_NOT_FOUND",
                "message": "executable not found: %r" % exe}
    running = []
    if app and not allow_second:
        running = [i for i in _instances(app) if i.get("has_window")]
        if running:
            return {"status": "error", "reason_code": "ALREADY_RUNNING",
                    "message": "%s is already running (%d window(s): %s) - a second "
                               "instance would share nothing with it"
                               % (app, len(running),
                                  ", ".join("pid %s %r" % (i["pid"], i["title"])
                                            for i in running[:3])),
                    "hint": "use the running window (app_open_project reuses it when "
                            "the title already names the project), or close it first; "
                            "pass allow_second=true only if two copies is what you want",
                    "instances": [{"pid": i["pid"], "title": i["title"],
                                   "project": i["project"]} for i in running]}
    cmd = [exe] + list(args or [])
    env, dropped = _child_env()
    base = 0
    if IS_WINDOWS:
        base = (getattr(subprocess, "DETACHED_PROCESS", 0x00000008) |
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200))
    flags = base | getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0x01000000)
    devnull = subprocess.DEVNULL
    try:
        proc = subprocess.Popen(cmd, creationflags=flags, close_fds=True, env=env,
                                stdin=devnull, stdout=devnull, stderr=devnull)
        _SPAWNED.append(proc)
        return {"status": "ok", "pid": proc.pid, "command": cmd,
                "creationflags": hex(flags), "breakaway": True,
                "env_cleared": dropped}
    except OSError as exc:
        try:
            proc = subprocess.Popen(cmd, creationflags=base, close_fds=True, env=env,
                                    stdin=devnull, stdout=devnull, stderr=devnull)
            _SPAWNED.append(proc)
            return {"status": "ok", "pid": proc.pid, "command": cmd,
                    "creationflags": hex(base), "breakaway": False,
                    "breakaway_error": str(exc), "env_cleared": dropped}
        except Exception as exc2:                                # noqa: BLE001
            return {"status": "error", "reason_code": "LAUNCH_FAILED",
                    "message": "cannot start %s: %s (breakaway attempt: %s)"
                               % (exe, exc2, exc)}


def close(app, force=False, timeout=15.0, only_pid=None):
    """Close the app: WM_CLOSE to its windows, wait, then (only if asked) kill.

    A project on disk is never touched. Unsaved edits: WM_CLOSE is the same signal
    as clicking X, so the app decides and may show a save dialog that blocks. That
    is intentional - silently killing an EDA would discard the user's work.
    """
    spec = APPS.get(app)
    if spec is None:
        raise DesktopError("unknown app: %r" % app)
    pids = [only_pid] if only_pid else pids_of(spec["exe_names"])
    if not pids:
        return {"status": "ok", "closed": 0, "was_running": False,
                "message": "not running"}
    u32 = _user32() if IS_WINDOWS else None
    posted = 0
    if u32:
        for win in windows_of(pids):
            try:
                u32.PostMessageW(int(win["hwnd"]), 0x0010, 0, 0)   # WM_CLOSE
                posted += 1
            except Exception:                                    # noqa: BLE001
                pass
    deadline = time.monotonic() + float(timeout)
    while time.monotonic() < deadline:
        if not any(process_alive(p) for p in pids):
            break
        time.sleep(0.2)
    remaining = [p for p in pids if process_alive(p)]
    killed = []
    if remaining and force:
        k32 = _kernel32()
        for pid in remaining:
            handle = k32.OpenProcess(0x0001, False, pid)          # PROCESS_TERMINATE
            if handle:
                try:
                    k32.TerminateProcess(handle, 1)
                    killed.append(pid)
                finally:
                    k32.CloseHandle(handle)
        time.sleep(0.3)
        remaining = [p for p in pids if process_alive(p)]
    out = {"status": "ok", "was_running": True, "pids": pids,
           "wm_close_posted": posted, "closed": len(pids) - len(remaining),
           "remaining": remaining, "forced": bool(killed), "killed": killed}
    if remaining:
        out["note"] = ("still running: a modal dialog (unsaved changes?) is the usual "
                       "reason WM_CLOSE does not take. Resolve it in the UI, or call "
                       "close(force=true) - that discards unsaved work.")
    return out


def open_project(app, path, reuse=True, wait=25.0):
    """Open a project/board file in the app, then verify by window title.

    `opened` is 'confirmed' only when a window title of the resulting instance
    contains the file stem. Anything else is 'unconfirmed' plus what was seen:
    launching a process is not the same as opening a project.
    """
    spec = APPS.get(app)
    if spec is None:
        raise DesktopError("unknown app: %r" % app)
    if not path or not os.path.isfile(path):
        return {"status": "error", "reason_code": "PROJECT_NOT_FOUND",
                "message": "project file does not exist: %r (this tool never creates "
                           "files)" % path}
    ext = os.path.splitext(path)[1].lower()
    if ext not in spec["project_exts"]:
        return {"status": "error", "reason_code": "EXT_MISMATCH",
                "message": "%s opens %s, not %s" % (app, spec["project_exts"], ext)}
    stem = os.path.splitext(os.path.basename(path))[0]
    if reuse:
        for inst in _instances(app):
            if inst["project"] and stem.lower() in inst["project"].lower():
                focus_window(inst["hwnd"])
                return {"status": "ok", "action": "focused_existing", "pid": inst["pid"],
                        "title": inst["title"], "opened": "confirmed",
                        "why": "an instance already has this project in its title - no "
                               "second window was started"}
    found = detect(app)
    if not found.get("found"):
        return {"status": "error", "reason_code": "APP_NOT_INSTALLED",
                "message": "%s not found on this machine" % spec["display"],
                "tried": found.get("tried")}
    started = launch(found["exe"], [path], app=app)
    if started.get("status") != "ok":
        return started
    deadline = time.monotonic() + float(wait)
    confirmed = None
    while time.monotonic() < deadline:
        for inst in _instances(app):
            if stem.lower() in (inst.get("title") or "").lower():
                confirmed = inst
                break
        if confirmed:
            break
        time.sleep(0.4)
    out = {"status": "ok", "action": "launched", "exe": found["exe"],
           "pid": started.get("pid"), "project": path,
           "open_argv_verified": spec.get("open_argv_verified"),
           "opened": "confirmed" if confirmed else "unconfirmed",
           "wait_s": float(wait)}
    if confirmed:
        focus_window(confirmed["hwnd"])
        out["title"] = confirmed["title"]
        out["instance_pid"] = confirmed["pid"]
    else:
        out["note"] = ("process started but no window title contains %r - the app may "
                       "require opening the project from its start page, or it is still "
                       "loading. Check app_status before assuming it is loaded." % stem)
        out["instances_after"] = [{"pid": i["pid"], "title": i["title"]}
                                  for i in _instances(app)]
    return out
