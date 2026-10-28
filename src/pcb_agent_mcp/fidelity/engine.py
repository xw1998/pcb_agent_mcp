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


def _run(args, cwd, timeout=180):
    exe = find_kicad_cli()
    if not exe:
        return {"ok": False, "error": "KICAD_CLI_NOT_FOUND"}
    try:
        proc = subprocess.run([exe] + args, cwd=cwd, capture_output=True,
                              text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "TIMEOUT", "timeout_s": timeout}
    return {"ok": proc.returncode == 0, "returncode": proc.returncode,
            "stdout": (proc.stdout or "")[-4000:], "stderr": (proc.stderr or "")[-4000:]}


# --------------------------------------------------------------- DRC
def drc(path, severity="all", timeout=300):
    """Authoritative DRC. Reports only; never writes beside the input."""
    exe = find_kicad_cli()
    if not exe:
        return {"status": "error", "reason_code": "KICAD_CLI_NOT_FOUND",
                "message": "kicad-cli was not found; install KiCad or pass an explicit path"}
    if not os.path.isfile(path):
        return {"status": "error", "reason_code": "PROJECT_NOT_FOUND",
                "message": "this tool never creates files: %s" % path}
    tmp = tempfile.mkdtemp(prefix="pcb_agent_drc_")
    try:
        report = os.path.join(tmp, "drc.json")
        args = ["pcb", "drc", "-o", report, "--format", "json"]
        args.append("--severity-all" if severity == "all" else "--severity-error")
        args.append(os.path.abspath(path))
        res = _run(args, cwd=tmp, timeout=timeout)
        if not os.path.isfile(report):
            return {"status": "error", "reason_code": "ENGINE_FAILED",
                    "message": "kicad-cli produced no report",
                    "engine": {"exe": exe, "version": version()},
                    "engine_output": res}
        with open(report, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return {
            "status": "ok",
            "authoritative": True,
            "engine": {"name": "kicad-cli", "exe": exe, "version": data.get("kicad_version") or version()},
            "verdict_source": "KiCad DRC engine (not this project's geometry code)",
            "violations_total": len(data.get("violations") or []),
            "unconnected_total": len(data.get("unconnected_items") or []),
            "by_severity": _count(data.get("violations") or [], "severity"),
            "by_type": _count(data.get("violations") or [], "type"),
            "violations": _slim_violations(data.get("violations") or []),
            "unconnected": _slim_unconnected(data.get("unconnected_items") or []),
            "included_severities": data.get("included_severities"),
            "schematic_parity": data.get("schematic_parity"),
            "not_written": "no file was created or modified beside %s" % os.path.basename(path),
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


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
def sch_erc(path, severity="all", timeout=300):
    """Authoritative ERC: KiCad's own electrical-rule engine on a .kicad_sch.

    The report is the verdict for a schematic edit. It is *not* "this server
    thinks two pins are unconnected": KiCad computed it, and the tool reports
    what KiCad said, with the sheet path of every violation.

    Read-only: the JSON report is written into a temp directory, and no
    `--save-*`/rewrite flag is ever passed, so the project is untouched.
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
        res = _run(args, cwd=tmp, timeout=timeout)
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
