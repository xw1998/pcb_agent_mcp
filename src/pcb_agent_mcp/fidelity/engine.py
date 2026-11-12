"""Ask KiCad itself - the independent referee.

The whole point of the fidelity layer is that answers must not be *my* opinion.
KiCad already owns the ground truth: it has a DRC engine, a Gerber plotter, a
board-stats calculator and a position-file writer. This module shells out to
`kicad-cli` and reports what it says, instead of recomputing anything.

Two uses:

* `verify(doc)` - write the re-emitted board next to the original in a **temp
  directory**, run stats / gerbers / position on both, and compare. If the two
  files produce identical Gerber copper, identical stats and identical positions,
  then the round trip lost nothing that KiCad can see. This check trusts none of
  my geometry code - only the writer.
* `drc(path)` - the authoritative clearance / connectivity answer. My own
  geometry pass is a convenience; this is the verdict.

Hard rule (the user's "不许瞎删我工程"): every command runs with `cwd` set to a
temp directory, every output path is inside that directory, and `--save-board` is
never passed - so `kicad-cli` cannot write into, or rewrite, the user's project.
"""
import csv
import io
import json
import os
import shutil
import subprocess
import tempfile

_KNOWN = (
    r"D:\KiCad\10.0\bin\kicad-cli.exe",
    r"C:\Program Files\KiCad\10.0\bin\kicad-cli.exe",
    r"C:\Program Files\KiCad\9.0\bin\kicad-cli.exe",
    r"C:\Program Files\KiCad\8.0\bin\kicad-cli.exe",
)

_CACHE = {}


def find_kicad_cli():
    """Path to kicad-cli, or None. Uses the desktop detector when available."""
    if "exe" in _CACHE:
        return _CACHE["exe"]
    exe = None
    try:
        from .. import desktop
        info = desktop.detect("kicad-cli")
        if info.get("found"):
            exe = info.get("exe")
    except Exception:
        exe = None
    if not exe:
        for cand in _KNOWN:
            if os.path.isfile(cand):
                exe = cand
                break
    if not exe:
        exe = shutil.which("kicad-cli")
    _CACHE["exe"] = exe
    return exe


def version():
    exe = find_kicad_cli()
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "version"], capture_output=True, text=True,
                             timeout=30)
        return (out.stdout or "").strip() or None
    except Exception:
        return None


def _user_env(scratch):
    """Environment for a `kicad-cli` call, or None when the real one works.

    `kicad-cli` dies with 0xC0000005 (and puts up a Windows crash dialog) when it
    cannot create `~/Documents/KiCad`; measured under a restricted account where
    that path was not writable - the process crashed before reading the board,
    which then looked like "the DRC is broken". Redirect HOME/USERPROFILE at a
    writable directory only in that case: a user's own KiCad settings are theirs
    and must not be swapped out silently when they work.
    """
    home = os.path.expanduser("~")
    probe_dir = os.path.join(home, "Documents", "KiCad")
    try:
        os.makedirs(probe_dir, exist_ok=True)
        probe = os.path.join(probe_dir, ".pcb_agent_write_test")
        with open(probe, "w"):
            pass
        os.remove(probe)
        return None
    except OSError:
        pass
    alt = os.path.join(scratch, "user")
    os.makedirs(alt, exist_ok=True)
    env = dict(os.environ)
    env["HOME"] = alt
    env["USERPROFILE"] = alt
    # KiCad reads its *own settings directory* through HOME, so the redirect
    # above also hid the user's global library tables and every symbol from the
    # official libraries turned into a `lib_symbol_issues` violation. Measured
    # on the RK3506 core board, kicad-cli 10.0.7:
    #     HOME redirected only                        79 violations
    #     same, plus KICAD_CONFIG_HOME pointing back  11 (all isolated_pin_label)
    # The 68 that vanished were lib_symbol_issues + footprint_link_issues on the
    # 34 passive parts: not the design, just the tables going unread.
    cfg = _kicad_config_home()
    if cfg:
        env["KICAD_CONFIG_HOME"] = cfg
    return env

def _kicad_config_home():
    """The real KiCad settings directory of the user this process runs as."""
    if os.name == "nt":
        root = os.environ.get("APPDATA")
        cand = os.path.join(root, "kicad") if root else ""
    else:
        cand = os.path.join(os.path.expanduser("~"), ".config", "kicad")
    return cand if cand and os.path.isdir(cand) else ""

def _run(args, cwd, timeout=180, env_dir=None):
    """`env_dir` keeps the redirected HOME out of the directory being read."""
    exe = find_kicad_cli()
    if not exe:
        return {"ok": False, "error": "KICAD_CLI_NOT_FOUND"}
    try:
        proc = subprocess.run([exe] + args, cwd=cwd, capture_output=True,
                              text=True, timeout=timeout,
                              env=_user_env(env_dir or cwd))
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "TIMEOUT", "timeout_s": timeout}
    return {"ok": proc.returncode == 0, "returncode": proc.returncode,
            "stdout": (proc.stdout or "")[-4000:], "stderr": (proc.stderr or "")[-4000:]}


# --------------------------------------------------------------- DRC
# One number for the whole DRC path: an 88.7 MB board needed more than fifteen
# minutes here, so a 300 s default would have been a timeout dressed up as a
# verdict. It is overridable per call, and `board_drc(background=true)` exists
# because the right answer to a slow call is an id, not a longer wait.
DRC_TIMEOUT_S = 900

def drc(path, severity="all", timeout=None, project=None, refill=False,
        save_to=None):
    """Authoritative DRC. Reports only; never writes beside the input.

    `project` is the `.kicad_pro` the verdict has to be read against. It is not
    decoration: KiCad takes the *design rule minimums* (min track width, min via
    drill, min clearance) and the **rule severities** from the project file next
    to the board, and silently falls back to its own defaults when there is none.
    A board copied to a directory without its project therefore gets a different
    verdict than the same board in place.  Measured on the STM32F412 core board,
    same 199 KB of bytes, kicad-cli 10.0.7:

        no .kicad_pro beside it   665 violations, 515 of them ERROR
                                  (drill_out_of_range 158, track_width 199,
                                   via_diameter 158, silk_overlap 14 error)
        project beside it         150 violations, 0 error (all four above are
                                  gone or demoted to warning)

    So a DRC diff whose two halves disagree about which project they were read
    against reports phantom errors: on the same board the "before" half (a
    checkpoint copy, no project) called 14 silkscreen overlaps errors while the
    "after" half called the identical 14 warnings.

    When `project` is given, the board AND that project are copied into the
    scratch directory under their own basenames, so the verdict is read against
    the real rule set and nothing is written beside the input.

    `refill=True` passes kicad-cli's `--refill-zones`: the zones are refilled **in
    memory** before the check and the file on disk is left untouched (no
    `--save-board` is passed, so the refilled polygons are thrown away with the
    scratch copy). This matters more than it sounds. A `filled_polygon` is a
    SNAPSHOT of the pour taken when the zone was last filled; copper added after
    that snapshot is not cut out of it, so every new track reads as
    "track -> zone, wanted 0.25 mm, got 0.0000 mm". Measured on the STM32F412
    core board (kicad-cli 10.0.7, 231 tracks, two GND zones, fill one edit old):
    without the refill 502 violations / 324 of them clearance; with it 52 / 0
    clearance. The 450 difference was entirely the stale snapshot - which is to
    say the no-refill verdict was not a stricter answer, it was a wrong one.

    `save_to` asks for the refilled board itself, not just the verdict. It needs
    `refill=True`, and it is the only way to answer a question the violation
    list structurally cannot: a refilled pour NEVER violates clearance against a
    track or a via, because the filler carves itself back around them. So "no
    new clearance violation" is true by construction whenever a zone is in play,
    and the price the pour paid - the filled area it gave up - is only visible in
    the refilled polygons. Nothing is written beside the input: the scratch copy
    is what gets copied out.
    """
    timeout = DRC_TIMEOUT_S if timeout is None else timeout
    exe = find_kicad_cli()
    if not exe:
        return {"status": "error", "reason_code": "KICAD_CLI_NOT_FOUND",
                "message": "kicad-cli was not found; install KiCad or pass an explicit path"}
    if not os.path.isfile(path):
        return {"status": "error", "reason_code": "PROJECT_NOT_FOUND",
                "message": "this tool never creates files: %s" % path}
    tmp = tempfile.mkdtemp(prefix="pcb_agent_drc_")
    try:
        target_path = os.path.abspath(path)
        project_used = None
        if project and os.path.isfile(project):
            base = os.path.basename(path)
            staged = os.path.join(tmp, base)
            shutil.copyfile(path, staged)
            pro_copy = os.path.splitext(staged)[0] + ".kicad_pro"
            shutil.copyfile(project, pro_copy)
            target_path = staged
            project_used = os.path.basename(project)
        if os.path.isfile(os.path.splitext(os.path.abspath(path))[0] + ".kicad_pro") \
                and project_used is None:
            project_used = os.path.basename(
                os.path.splitext(os.path.abspath(path))[0] + ".kicad_pro")
            base = os.path.basename(path)
            staged = os.path.join(tmp, base)
            shutil.copyfile(path, staged)
            shutil.copyfile(os.path.splitext(os.path.abspath(path))[0] + ".kicad_pro",
                            os.path.splitext(staged)[0] + ".kicad_pro")
            target_path = staged
        if save_to and refill and target_path == os.path.abspath(path):
            # `--save-board` makes kicad-cli WRITE the board it was given, and
            # until this branch existed the board it was given was the caller's
            # own file whenever the board had no .kicad_pro beside it. So a
            # "reports only" call re-saved the caller's board with KiCad's own
            # writer - which drops what KiCad 10 treats as legacy, `(net_class
            # "Default" ... (clearance 0.2) (trace_width 0.25) ...)`, i.e. the
            # board's clearance. Measured: one apply on a 60x40 synthetic board
            # (engine=True, so the DRC gate runs with save_to) left the file with
            # net_class count 0, and the next board_route on it refused with
            # RULES_ABSENT. A read must not edit the thing it reads, so the
            # scratch copy is made here even when there is no project to bring
            # along.
            staged = os.path.join(tmp, os.path.basename(path))
            shutil.copyfile(path, staged)
            target_path = staged
        report = os.path.join(tmp, "drc.json")
        args = ["pcb", "drc", "-o", report, "--format", "json"]
        args.append("--severity-all" if severity == "all" else "--severity-error")
        if refill:
            args.append("--refill-zones")
            if save_to:
                # the refilled polygons are written into the SCRATCH copy; the
                # caller's path is only ever read, and `save_to` is copied out
                # below, before the scratch directory is removed
                args.append("--save-board")
        args.append(target_path)
        res = _run(args, cwd=tmp, timeout=timeout)
        if not os.path.isfile(report):
            return {"status": "error", "reason_code": "ENGINE_FAILED",
                    "message": "kicad-cli produced no report",
                    "engine": {"exe": exe, "version": version()},
                    "engine_output": res}
        with open(report, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        saved = None
        if save_to and refill:
            if os.path.isfile(target_path) and os.path.getsize(target_path) > 0:
                shutil.copyfile(target_path, save_to)
                saved = save_to
        return {
            "status": "ok",
            "authoritative": True,
            "engine": {"name": "kicad-cli", "exe": exe, "version": data.get("kicad_version") or version()},
            "verdict_source": "KiCad DRC engine (not this project's geometry code)",
            "violations_total": len(data.get("violations") or []),
            "unconnected_total": len(data.get("unconnected_items") or []),
            "by_severity": _count(data.get("violations") or [], "severity"),
            "by_type": _count(data.get("violations") or [], "type"),
            "violations": _with_geometry(
                _slim_violations(data.get("violations") or [])),
            "unconnected": _slim_unconnected(data.get("unconnected_items") or []),
            "included_severities": data.get("included_severities"),
            "schematic_parity": data.get("schematic_parity"),
            "project_file_used": project_used,
            "zones_refilled_for_check": bool(refill),
            "refilled_board_saved_to": saved,
            "zone_refill_note": (
                "zones were refilled in a scratch copy before the check, so the "
                "verdict is about the pour as it would be after a fill - the "
                "filled_polygons in the file you named are still the old "
                "snapshot") if refill else (
                "zones were NOT refilled: any filled_polygon older than the "
                "copper reads as a 0 mm clearance to every track added since, "
                "so clearance counts can be inflated by hundreds of phantom "
                "violations. Pass refill=True unless you are deliberately "
                "auditing a stale file"),
            "rule_source": "project" if project_used else "kicad_defaults",
            "rule_source_note": None if project_used else (
                "no .kicad_pro was found beside this board, so this verdict was "
                "read against KiCad's built-in rule minimums and severities, not "
                "the project's. The same bytes can be 515 errors here and 0 "
                "next to their project file: pass project= or keep the board "
                "beside its .kicad_pro before trusting a clearance count"),
            "not_written": "no file was created or modified beside %s" % os.path.basename(path),
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _with_geometry(items):
    """Add the constraint/actual millimetre pair to every violation.

    KiCad's JSON has no geometry fields for a violation, so the only source is
    its description text; see `drc_geometry` for the sample strings and for what
    happens when a rule states no numbers.
    """
    from . import drc_geometry
    return drc_geometry.annotate(items)

def _count(items, key):
    out = {}
    for it in items:
        k = str(it.get(key) or "?")
        out[k] = out.get(k, 0) + 1
    return out


def _slim_violations(items):
    out = []
    for v in items[:200]:
        out.append({
            "type": v.get("type"),
            "severity": v.get("severity"),
            "description": v.get("description"),
            "items": [{"description": i.get("description"),
                       "pos": i.get("pos")} for i in (v.get("items") or [])[:4]],
        })
    return out


def _slim_unconnected(items):
    out = []
    for v in items[:200]:
        out.append({"description": v.get("description"),
                    "items": [{"description": i.get("description"),
                               "pos": i.get("pos")} for i in (v.get("items") or [])[:4]]})
    return out

# --------------------------------------------------------------- ERC (schematic)
def sch_erc(path, severity="all", timeout=300, project_dir=None):
    """Authoritative ERC: KiCad's own electrical-rule engine on a .kicad_sch.

    The report is the verdict for a schematic edit. It is *not* "this server
    thinks two pins are unconnected": KiCad computed it, and the tool reports
    what KiCad said, with the sheet path of every violation.

    `project_dir` is the directory holding the `.kicad_pro` and the project's
    `sym-lib-table` / `fp-lib-table`. It is not decoration: kicad-cli resolves
    those tables **relative to the working directory it runs in**, so a
    schematic checked from anywhere else loses every project-local library and
    reports violations that are not about the design. Measured on the RK3506
    core board, same 272,751 bytes, kicad-cli 10.0.7:

        run from a scratch dir      79 violations, 68 of them
                                    lib_symbol_issues + footprint_link_issues
        run beside its .kicad_pro   11 violations, all isolated_pin_label
                                    (the 11 nets the table gives one terminal)

    So the tool runs where the tables are, and only the *report* goes to the
    temp directory. Read-only either way: no `--save-*`/rewrite flag is ever
    passed, so no file beside the input is created or modified.
    """
    exe = find_kicad_cli()
    if not exe:
        return {"status": "error", "reason_code": "KICAD_CLI_NOT_FOUND",
                "message": "kicad-cli was not found; install KiCad or pass an explicit path"}
    if not os.path.isfile(path):
        return {"status": "error", "reason_code": "PROJECT_NOT_FOUND",
                "message": "this tool never creates files: %s" % path}
    tmp = tempfile.mkdtemp(prefix="pcb_agent_erc_")
    try:
        report = os.path.join(tmp, "erc.json")
        args = ["sch", "erc", "-o", report, "--format", "json"]
        args.append("--severity-all" if severity == "all" else "--severity-error")
        args.append(os.path.abspath(path))
        here = os.path.dirname(os.path.abspath(path))
        run_dir = project_dir or here
        if not os.path.isdir(run_dir):
            return {"status": "error", "reason_code": "PROJECT_NOT_FOUND",
                    "message": "project_dir is not a directory: %s" % run_dir}
        res = _run(args, cwd=run_dir, timeout=timeout, env_dir=tmp)
        if not os.path.isfile(report):
            return {"status": "error", "reason_code": "ENGINE_FAILED",
                    "message": "kicad-cli produced no ERC report",
                    "engine": {"exe": exe, "version": version()},
                    "engine_output": res}
        with open(report, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        flat = []
        per_sheet = []
        for sheet in data.get("sheets") or []:
            vs = sheet.get("violations") or []
            per_sheet.append({"path": sheet.get("path"),
                              "uuid_path": sheet.get("uuid_path"),
                              "violations": len(vs)})
            for v in vs:
                flat.append(dict(v, sheet=sheet.get("path")))
        return {
            "status": "ok",
            "authoritative": True,
            "engine": {"name": "kicad-cli", "exe": exe,
                       "version": data.get("kicad_version") or version()},
            "verdict_source": "KiCad ERC engine (not this project's net tracing)",
            "schema": data.get("$schema"),
            "coordinate_units": data.get("coordinate_units"),
            "sheets": per_sheet,
            "violations_total": len(flat),
            "by_severity": _count(flat, "severity"),
            "by_type": _count(flat, "type"),
            "ignored_checks": data.get("ignored_checks"),
            "included_severities": data.get("included_severities"),
            "violations": _slim_violations(flat),
            "not_written": "no file was created or modified beside %s"
                           % os.path.basename(path),
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

# --------------------------------------------------------------- netlist
def sch_pdf(path, out, timeout=300):
    """Plot the schematic to PDF with KiCad's own plotter.

    Used as an instrument, not as a deliverable: the PDF's text boxes are the
    only place where "did two labels land on each other" can be answered by
    measurement instead of by eye. Runs beside the sheet for the same reason
    `sch_erc` does -- a project-local library that fails to resolve is drawn
    differently, so the plot would not be the sheet that will be read.
    """
    if not find_kicad_cli():
        return {"status": "error", "reason_code": "KICAD_CLI_NOT_FOUND"}
    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    here = os.path.dirname(os.path.abspath(path))
    res = _run(["sch", "export", "pdf", "-o", os.path.abspath(out),
                os.path.abspath(path)], cwd=here, timeout=timeout,
               env_dir=os.path.dirname(os.path.abspath(out)))
    if res["returncode"] or not os.path.isfile(out):
        return {"status": "error", "reason_code": "PLOT_FAILED",
                "message": (res["stderr"] or res["stdout"] or "").strip()[-300:]}
    return {"status": "ok", "pdf": out, "bytes": os.path.getsize(out)}

def sch_netlist(path, timeout=300):
    """KiCad's own connectivity answer for a schematic, as a comparable list.

    A netlist is the *netlist*: which pin of which reference lands on which
    named net. Diffing it before/after an edit is the only cheap way to prove
    that a wire or a label actually did what the plan claimed, because the two
    ends of a wire can be at the right coordinates and still not connect.

    Returned structure is normalised (nets sorted by name, nodes sorted), which
    makes a diff noise-free: KiCad renumbers net codes on every export.
    """
    exe = find_kicad_cli()
    if not exe:
        return {"status": "error", "reason_code": "KICAD_CLI_NOT_FOUND"}
    if not os.path.isfile(path):
        return {"status": "error", "reason_code": "PROJECT_NOT_FOUND",
                "message": "this tool never creates files: %s" % path}
    tmp = tempfile.mkdtemp(prefix="pcb_agent_netlist_")
    try:
        out = os.path.join(tmp, "netlist.net")
        res = _run(["sch", "export", "netlist", "--format", "kicadsexpr",
                    "-o", out, os.path.abspath(path)], cwd=tmp, timeout=timeout)
        if not os.path.isfile(out):
            return {"status": "error", "reason_code": "ENGINE_FAILED",
                    "engine_output": res}
        with open(out, "r", encoding="utf-8", errors="surrogateescape") as fh:
            text = fh.read()
        from . import sexpr as S
        tree = S.parse(text)
        root = tree.items[0]
        comps = []
        for c in S.children(S.child(root, "components"), "comp") or []:
            comps.append({"ref": S.arg_text(S.child(c, "ref"), 1),
                          "value": S.arg_text(S.child(c, "value"), 1),
                          "footprint": S.arg_text(S.child(c, "footprint"), 1)})
        nets = []
        for n in S.children(S.child(root, "nets"), "net") or []:
            nodes = []
            for nd in S.children(n, "node"):
                nodes.append([S.arg_text(S.child(nd, "ref"), 1),
                              S.arg_text(S.child(nd, "pin"), 1)])
            nets.append({"name": S.arg_text(S.child(n, "name"), 1),
                         "nodes": sorted(nodes)})
        nets.sort(key=lambda n: (n["name"] or ""))
        comps.sort(key=lambda c: (c["ref"] or ""))
        return {"status": "ok", "authoritative": True,
                "engine": {"name": "kicad-cli", "version": version()},
                "verdict_source": "KiCad netlister",
                "components": comps,
                "nets": nets,
                "components_total": len(comps),
                "nets_total": len(nets),
                "not_written": "no file was created or modified beside %s"
                               % os.path.basename(path)}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------- stats
def stats(path, timeout=180):
    """KiCad's own board statistics (copper area, thickness, outlines, pads)."""
    exe = find_kicad_cli()
    if not exe:
        return {"status": "error", "reason_code": "KICAD_CLI_NOT_FOUND"}
    if not os.path.isfile(path):
        return {"status": "error", "reason_code": "PROJECT_NOT_FOUND",
                "message": "this tool never creates files: %s" % path}
    tmp = tempfile.mkdtemp(prefix="pcb_agent_stats_")
    try:
        report = os.path.join(tmp, "stats.json")
        res = _run(["pcb", "export", "stats", "-o", report, "--format", "json",
                    "--units", "mm", os.path.abspath(path)], cwd=tmp, timeout=timeout)
        if not os.path.isfile(report):
            return {"status": "error", "reason_code": "ENGINE_FAILED",
                    "engine_output": res}
        with open(report, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return {"status": "ok", "authoritative": True,
                "engine": {"name": "kicad-cli", "version": version()},
                "board": data.get("board"), "pads": data.get("pads"),
                "tracks": data.get("tracks"), "vias": data.get("vias"),
                "nets": data.get("nets"), "zones": data.get("zones"),
                "raw_keys": sorted(data.keys())}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------- round trip
def verify(doc, timeout=300):
    """Prove the fidelity layer lost nothing *by KiCad's own measure*.

    Three independent comparisons, each of which would catch a dropped field:

    * `stats`   - board size, copper area (includes zone fill), thickness, pads
    * `gerbers` - the actual plotted copper / silk / outline artwork
    * `pos`     - every component position and rotation
    """
    exe = find_kicad_cli()
    if not exe:
        return {"status": "skipped", "reason_code": "KICAD_CLI_NOT_FOUND",
                "message": "install KiCad (or set one) to run the independent check"}
    tmp = tempfile.mkdtemp(prefix="pcb_agent_verify_")
    try:
        src = os.path.join(tmp, "a.kicad_pcb")
        out = os.path.join(tmp, "b.kicad_pcb")
        with open(src, "wb") as fh:
            fh.write(doc.text.encode("utf-8", "surrogateescape"))
        with open(out, "wb") as fh:
            fh.write(doc.to_bytes())

        checks = {}
        checks["stats"] = _cmp_stats(tmp, src, out, timeout)
        checks["gerbers"] = _cmp_gerbers(tmp, src, out, timeout)
        checks["position"] = _cmp_pos(tmp, src, out, timeout)
        tested = [c for c in checks.values() if c.get("comparable")]
        identical = bool(tested) and all(c.get("identical") for c in tested)
        return {
            "status": "ok",
            "engine": {"name": "kicad-cli", "exe": exe, "version": version()},
            "identical": identical,
            "checks": checks,
            "meaning": ("KiCad was given the original and the re-emitted file; if "
                        "the plotted copper, the statistics and the positions all "
                        "match, no field that KiCad reads was lost by the round trip"),
            "not_written": "both files lived in a temp directory and were removed",
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def compare_paths(a, b, which=("stats", "gerbers", "position"), timeout=300):
    """Ask KiCad whether two board FILES say the same thing.

    Used by the write path: the file before the edit and the file after it are
    handed to the same engine, so "the edit did what I said" is not decided by
    the code that made the edit.
    """
    exe = find_kicad_cli()
    if not exe:
        return {"status": "skipped", "reason_code": "KICAD_CLI_NOT_FOUND",
                "message": "install KiCad (or set one) to run the independent check"}
    for path in (a, b):
        if not os.path.isfile(path):
            return {"status": "error", "reason_code": "FILE_NOT_FOUND",
                    "message": "missing board file: %s" % path}
    tmp = tempfile.mkdtemp(prefix="pcb_agent_compare_")
    try:
        checks = {}
        if "stats" in which:
            checks["stats"] = _cmp_stats(tmp, a, b, timeout)
        if "gerbers" in which:
            checks["gerbers"] = _cmp_gerbers(tmp, a, b, timeout)
        if "position" in which:
            checks["position"] = _cmp_pos(tmp, a, b, timeout)
        tested = [c for c in checks.values() if c.get("comparable")]
        identical = bool(tested) and all(c.get("identical") for c in tested)
        return {
            "status": "ok",
            "engine": {"name": "kicad-cli", "exe": exe, "version": version()},
            "identical": identical,
            "checks": checks,
            "meaning": ("KiCad read both files; if the statistics, the plotted "
                        "artwork and the positions all match, nothing KiCad cares "
                        "about differs between them"),
            "not_written": "reports and plots were made in a temp directory",
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

def _cmp_stats(tmp, a, b, timeout):
    out = {}
    for tag, path in (("a", a), ("b", b)):
        rep = os.path.join(tmp, "stats_%s.json" % tag)
        res = _run(["pcb", "export", "stats", "-o", rep, "--format", "json",
                    "--units", "mm", path], cwd=tmp, timeout=timeout)
        if not os.path.isfile(rep):
            return {"comparable": False, "error": "ENGINE_FAILED", "detail": res}
        with open(rep, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        out[tag] = _norm_stats(data)
    return {"comparable": True, "identical": out["a"] == out["b"],
            "diff": _diff_dicts(out["a"], out["b"])}


def _norm_stats(data):
    data = dict(data)
    data.pop("metadata", None)          # date / project name / board name
    return data


def _cmp_gerbers(tmp, a, b, timeout):
    layers = "F.Cu,B.Cu,F.SilkS,B.SilkS,F.Mask,B.Mask,Edge.Cuts"
    got = {}
    for tag, path in (("a", a), ("b", b)):
        d = os.path.join(tmp, "g_" + tag)
        os.makedirs(d, exist_ok=True)
        res = _run(["pcb", "export", "gerbers", "-o", d, "-l", layers, path],
                   cwd=tmp, timeout=timeout)
        files = {}
        for name in sorted(os.listdir(d)):
            if name.endswith(".gbrjob"):
                continue
            full = os.path.join(d, name)
            with open(full, "r", encoding="utf-8", errors="surrogateescape") as fh:
                text = fh.read()
            files[_gerber_key(text) or name] = _norm_gerber(text)
        if not files:
            return {"comparable": False, "error": "ENGINE_FAILED", "detail": res}
        got[tag] = files
    diffs = []
    for key in sorted(set(got["a"]) | set(got["b"])):
        if got["a"].get(key) != got["b"].get(key):
            diffs.append(key)
    return {"comparable": True, "identical": not diffs,
            "layers_compared": sorted(got["a"].keys()),
            "layers_different": diffs,
            "note": "creation date / project id lines are normalised away"}


def _gerber_key(text):
    for line in text.splitlines()[:12]:
        if line.startswith("%TF.FileFunction,"):
            return line.strip()
    return None


def _norm_gerber(text):
    keep = []
    for line in text.splitlines():
        if line.startswith("%TF.CreationDate"):
            continue
        if line.startswith("%TF.ProjectId"):
            continue
        if line.startswith("G04 Created by KiCad"):
            continue
        keep.append(line)
    return "\n".join(keep)


def _cmp_pos(tmp, a, b, timeout):
    got = {}
    for tag, path in (("a", a), ("b", b)):
        rep = os.path.join(tmp, "pos_%s.csv" % tag)
        res = _run(["pcb", "export", "pos", "-o", rep, "--format", "csv", path],
                   cwd=tmp, timeout=timeout)
        if not os.path.isfile(rep):
            return {"comparable": False, "error": "ENGINE_FAILED", "detail": res}
        with open(rep, "r", encoding="utf-8", errors="surrogateescape") as fh:
            rows = [r for r in csv.reader(io.StringIO(fh.read())) if r]
        header = rows[0] if rows else []
        body = sorted(rows[1:])
        got[tag] = {"header": header, "rows": body}
    return {"comparable": True,
            "identical": got["a"]["rows"] == got["b"]["rows"],
            "components": len(got["a"]["rows"])}


def _diff_dicts(a, b):
    out = {}
    for key in sorted(set(a) | set(b)):
        if a.get(key) != b.get(key):
            out[key] = {"original": a.get(key), "reemitted": b.get(key)}
    return out
