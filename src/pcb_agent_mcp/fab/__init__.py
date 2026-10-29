"""Manufacturing output - delegated to KiCad's own exporters, never recomputed.

Why delegate: a Gerber is not something I can "mostly get right". KiCad owns the
plotter, the Excellon writer, the position-file writer and the STEP exporter, and
it also owns the board's stored plot settings. Anything I produced myself would
be a second opinion about a format that a fabricator's CAM tool will judge. So
this module only:

  1. picks the arguments (units, formats, sides, layers),
  2. runs `kicad-cli` with `cwd` in a temp directory and an explicit absolute
     output path (so it can never write beside the user's project),
  3. inventories what actually landed on disk (bytes + sha256 + sha of the source),
  4. re-reads each artefact and checks the property that makes it usable - a
     Gerber that does not end in `M02*`, an Excellon with no tool table, a
     position file with no rows, a STEP that is not ISO-10303-21, is a **failed**
     export and is reported as such,
  5. writes `manifest.json` and (optionally) `fab.zip` beside the artefacts.

Hard rules carried over from the rest of the project:

* the user's project directory is never written to. The default output is a fresh
  temp directory; an explicit `out_dir` that sits inside the source directory is
  refused unless `allow_beside_source=true`.
* no runtime dependency: only stdlib + the external `kicad-cli` binary, which is
  an instrument, not a dependency. If it is missing the answer is
  `KICAD_CLI_NOT_FOUND`, not a silent fallback to my own writer.
"""
import csv
import hashlib
import io
import json
import os
import re
import shutil
import tempfile
import zipfile

from ..fidelity import engine

__all__ = ["package", "graphics", "threed", "schematic_export", "bom_rows",
           "FabError", "FORMATS"]

FORMATS = {
    "gerbers": "copper + mask + silk + Edge_Cuts, from the board's stored plot settings",
    "drill": "Excellon, mm, decimal zeros, PTH and NPTH in separate files, plus a map and a report",
    "pos": "component position file (CSV, mm, both sides) for the pick-and-place machine",
    "step": "STEP AP214 solid model (board + 3D component models that resolve)",
    "ipc2581": "IPC-2581C single-file manufacturing description",
    "odb": "ODB++ (zipped) manufacturing data",
    "ipcd356": "IPC-D-356 bare-board netlist for electrical test",
    "gencad": "GenCAD for older CAM flows",
}

_POS_EXTS = (".csv", ".pos", ".txt")


class FabError(Exception):
    def __init__(self, message, reason_code, hint=None):
        Exception.__init__(self, message)
        self.reason_code = reason_code
        self.hint = hint


# ------------------------------------------------------------------ helpers
def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _snapshot(root):
    """Every file under `root` -> {relpath: (size, sha256)}."""
    out = {}
    for base, _dirs, files in os.walk(root):
        for name in files:
            full = os.path.join(base, name)
            rel = os.path.relpath(full, root).replace("\\", "/")
            try:
                out[rel] = (os.path.getsize(full), _sha256_file(full))
            except OSError:
                continue
    return out


def _is_inside(child, parent):
    child = os.path.normcase(os.path.abspath(child))
    parent = os.path.normcase(os.path.abspath(parent))
    if child == parent:
        return True
    return child.startswith(parent.rstrip(os.sep) + os.sep)


def _resolve_out(path, out_dir, allow_beside):
    """Refuse to write anywhere inside the source directory tree by default."""
    src_dir = os.path.dirname(os.path.abspath(path))
    if out_dir:
        tgt = os.path.abspath(out_dir)
        if _is_inside(tgt, src_dir) and not allow_beside:
            raise FabError(
                "refusing to write into the source directory tree: %s is inside %s"
                % (tgt, src_dir),
                "OUT_DIR_BESIDE_SOURCE",
                "this project never writes beside the user's design; pick an output "
                "directory outside it, or pass allow_beside_source=true if you really "
                "want the artefacts next to the board")
    else:
        tgt = tempfile.mkdtemp(prefix="pcb_fab_")
    os.makedirs(tgt, exist_ok=True)
    return tgt


def _run(args, timeout=600):
    exe = engine.find_kicad_cli()
    if not exe:
        raise FabError("kicad-cli was not found; install KiCad or put it on PATH",
                       "KICAD_CLI_NOT_FOUND",
                       "every manufacturing artefact here comes from KiCad's own "
                       "exporter; there is no fallback writer")
    tmp = tempfile.mkdtemp(prefix="pcb_fab_cwd_")
    try:
        import subprocess
        try:
            proc = subprocess.run([exe] + args, cwd=tmp, capture_output=True,
                                  text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise FabError("kicad-cli %s did not finish in %ss" % (args[0], timeout),
                           "ENGINE_TIMEOUT",
                           "a large board or a missing 3D model library can make the "
                           "export slow; the timeout is a wall-clock limit, not a result")
        if proc.returncode != 0:
            raise FabError("kicad-cli %s failed (exit %d): %s"
                           % (" ".join(args[:2]), proc.returncode,
                              (proc.stderr or proc.stdout or "").strip()[-600:]),
                           "ENGINE_FAILED")
        return proc.stdout or ""
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------------ checks
_GERBER_END = re.compile(r"M0?2\*")
_FLASH = re.compile(r"D0?3\*")
_TOOL = re.compile(r"^T(\d+)C([0-9.]+)", re.M)
_HIT = re.compile(r"^X-?[0-9.]+Y-?[0-9.]+", re.M)


def _check_gerber(path):
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    flashes = len(_FLASH.findall(text))
    ended = bool(_GERBER_END.search(text[-4096:]))
    x2 = "%TF.FileFunction" in text
    problems = []
    if not ended:
        problems.append("no M02* terminator (a truncated plot)")
    if not text.lstrip().startswith("%"):
        problems.append("no %FS header")
    return {"flashes": flashes, "terminated": ended, "x2_attributes": x2,
            "problems": problems}


def _check_drill(path):
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    tools = sorted({float(m.group(2)) for m in _TOOL.finditer(text)})
    hits = len(_HIT.findall(text))
    problems = []
    header = "M48" in text
    # KiCad writes "; #@! TF.FileFunction,NonPlated,1,2,NPTH"; other writers use
    # NON_PLATED, so both spellings count.
    lowtext = text.lower()
    nonplated = ("nonplated" in lowtext or "non_plated" in lowtext
                 or "npth" in lowtext)
    # An empty NPTH file is a *fact about the board* (it has no non-plated holes),
    # not a broken export: KiCad still writes a valid M48 header with no tools.
    empty_slot = header and not tools and not hits
    if not header:
        problems.append("no M48 header")
    elif not tools and not empty_slot:
        problems.append("no tool table (T<nn>C<dia>)")
    if not empty_slot and hits == 0:
        problems.append("no drill hits")
    return {"tools_mm": tools, "hits": hits, "empty_slot": empty_slot,
            "non_plated": nonplated, "problems": problems}


def _check_pos(path):
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    rows = [r for r in csv.reader(io.StringIO(text))]
    rows = [r for r in rows if any((c or "").strip() for c in r)]
    header = rows[0] if rows else []
    problems = []
    if len(rows) < 2:
        problems.append("no component rows")
    cols = [c.strip().lower() for c in header]
    for need in ("ref", "posx", "posy", "rot", "side"):
        if cols and not any(c.startswith(need) for c in cols):
            problems.append("missing column %s" % need)
    return {"rows": max(0, len(rows) - 1), "columns": header, "problems": problems}


def _check_step(path):
    with open(path, "rb") as fh:
        head = fh.read(2048)
    problems = []
    if b"ISO-10303-21" not in head:
        problems.append("not an ISO-10303-21 file")
    return {"iso_10303_21": b"ISO-10303-21" in head, "problems": problems}


def _check_xml(path, needle):
    with open(path, "rb") as fh:
        head = fh.read(4096)
    ok = needle in head
    return {"header_ok": ok, "problems": [] if ok else ["missing %s header" % needle]}


def _check_text(path, needles):
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    problems = [("missing %s" % n) for n in needles if n not in text]
    return {"bytes": len(text), "problems": problems}


# ------------------------------------------------------------------ package
def package(path, out_dir=None, formats=("gerbers", "drill", "pos"),
            allow_beside_source=False, zip_it=True, timeout=900):
    """A complete manufacturing package: plot, drill, place, optional STEP/2581."""
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        raise FabError("this tool never creates files: %s" % path, "PROJECT_NOT_FOUND")
    formats = [f.lower() for f in (formats or [])]
    unknown = [f for f in formats if f not in FORMATS]
    if unknown:
        raise FabError("unknown format(s): %s" % ", ".join(unknown), "ARG_INVALID",
                       "known formats: %s" % ", ".join(sorted(FORMATS)))
    if not formats:
        raise FabError("no formats asked for", "ARG_INVALID")
    out = _resolve_out(path, out_dir, allow_beside_source)
    before = _snapshot(out)

    checks = {}
    runs = []
    for fmt in formats:
        if fmt == "gerbers":
            runs.append(("gerbers", ["pcb", "export", "gerbers",
                                     "--board-plot-params", "--check-zones",
                                     "-o", out, path]))
        elif fmt == "drill":
            runs.append(("drill", ["pcb", "export", "drill", "-o", out,
                                   "--format", "excellon", "--excellon-units", "mm",
                                   "--excellon-zeros-format", "decimal",
                                   "--excellon-oval-format", "route",
                                   "--excellon-separate-th",
                                   "--generate-map", "--map-format", "pdf",
                                   "--generate-report", path]))
        elif fmt == "pos":
            runs.append(("pos", ["pcb", "export", "pos",
                                 "-o", os.path.join(out, "positions.csv"),
                                 "--format", "csv", "--units", "mm",
                                 "--side", "both", path]))
        elif fmt == "step":
            runs.append(("step", ["pcb", "export", "step", "--force",
                                  "-o", os.path.join(out, "board.step"), path]))
        elif fmt == "ipc2581":
            runs.append(("ipc2581", ["pcb", "export", "ipc2581",
                                     "-o", os.path.join(out, "board.xml"), path]))
        elif fmt == "odb":
            runs.append(("odb", ["pcb", "export", "odb",
                                 "-o", os.path.join(out, "board-odb.zip"), path]))
        elif fmt == "ipcd356":
            runs.append(("ipcd356", ["pcb", "export", "ipcd356",
                                     "-o", os.path.join(out, "board.d356"), path]))
        elif fmt == "gencad":
            runs.append(("gencad", ["pcb", "export", "gencad",
                                    "-o", os.path.join(out, "board.gencad"), path]))

    for name, args in runs:
        try:
            _run(args, timeout=timeout)
            checks[name] = {"status": "ok"}
        except FabError as exc:
            if exc.reason_code == "KICAD_CLI_NOT_FOUND":
                raise
            checks[name] = {"status": "error", "reason_code": exc.reason_code,
                            "message": str(exc)}

    after = _snapshot(out)
    fresh = {rel: v for rel, v in after.items() if before.get(rel) != v}

    # per-artefact re-read: a file that exists is not a file that works
    files = []
    problems = []
    for rel in sorted(fresh):
        size, digest = fresh[rel]
        low = rel.lower()
        entry = {"name": rel, "bytes": size, "sha256": digest}
        try:
            if low.endswith((".gbr", ".gto", ".gbo", ".gtp", ".gbp", ".gts", ".gbs",
                             ".gko", ".gm1", ".gtl", ".gbl", ".gbrjob")):
                if low.endswith(".gbrjob"):
                    entry["check"] = {"job_file": True, "problems": []}
                else:
                    entry["check"] = _check_gerber(os.path.join(out, rel))
            elif low.endswith((".drl", ".xln", ".drd")):
                entry["check"] = _check_drill(os.path.join(out, rel))
            elif low.endswith(".csv") and "position" in low:
                entry["check"] = _check_pos(os.path.join(out, rel))
            elif low.endswith(".step") or low.endswith(".stp"):
                entry["check"] = _check_step(os.path.join(out, rel))
            elif low.endswith(".xml"):
                entry["check"] = _check_xml(os.path.join(out, rel), b"IPC-2581")
            elif low.endswith(".d356"):
                entry["check"] = _check_text(os.path.join(out, rel), ["317", "327"])
            elif low.endswith(".zip"):
                entry["check"] = {"zip": True,
                                  "entries": len(zipfile.ZipFile(os.path.join(out, rel)).namelist())}
        except Exception as exc:                       # noqa: BLE001
            entry["check"] = {"problems": ["could not re-read: %s" % exc]}
        if entry.get("check", {}).get("problems"):
            problems.append({"file": rel, "problems": entry["check"]["problems"]})
        files.append(entry)

    by_kind = {}
    for entry in files:
        ext = os.path.splitext(entry["name"])[1].lower()
        by_kind[ext] = by_kind.get(ext, 0) + 1

    manifest = {
        "schema": "pcb_agent_mcp/fab/1",
        "generated_by": "pcb_agent_mcp %s" % _version(),
        "kicad_cli": engine.version(),
        "source": {"path": path, "bytes": os.path.getsize(path),
                   "sha256": _sha256_file(path)},
        "output_dir": out,
        "formats_asked": formats,
        "formats_ok": sorted(k for k, v in checks.items() if v["status"] == "ok"),
        "files": files,
        "by_extension": dict(sorted(by_kind.items())),
        "problems": problems,
        "note": "every artefact is produced by KiCad's own exporter and re-read "
                "here; a file that fails its own check is listed under problems",
    }
    manifest_path = os.path.join(out, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(manifest, fh, indent=1, ensure_ascii=False)

    zipped = None
    if zip_it and files:
        zipped = os.path.join(out, "fab.zip")
        with zipfile.ZipFile(zipped, "w", zipfile.ZIP_DEFLATED) as zf:
            for entry in files + [{"name": "manifest.json"}]:
                full = os.path.join(out, entry["name"])
                if os.path.isfile(full):
                    zf.write(full, entry["name"])

    basename = os.path.basename(path)
    return {
        "status": "ok" if not problems else "partial",
        "out_dir": out,
        "zip": zipped,
        "manifest": manifest_path,
        "formats": checks,
        "files": files,
        "by_extension": dict(sorted(by_kind.items())),
        "problems": problems,
        "counts": {
            "gerber_layers": sum(1 for f in files
                                 if f["name"].lower().endswith((".gbr", ".gtl", ".gbl",
                                                                ".gts", ".gbs", ".gto",
                                                                ".gbo", ".gtp", ".gbp",
                                                                ".gm1"))),
            "drill_files": sum(1 for f in files if f["name"].lower().endswith((".drl", ".xln"))),
            "pos_rows": next((f["check"].get("rows") for f in files
                              if f["name"].lower().endswith(".csv")
                              and "check" in f and "rows" in f["check"]), None),
        },
        "assumptions": {
            "who_wrote_the_files": "kicad-cli (%s) - not this project" % engine.version(),
            "source_untouched": "kicad-cli runs with cwd in a temp dir and every "
                                "output path is inside out_dir; the design file is only "
                                "ever read",
            "gerber_layers": "whatever the board's stored plot settings ask for; pass "
                             "formats=('gerbers',) and check by_extension if you need "
                             "the exact layer count",
            "step_models": "components whose 3D model path does not resolve are "
                           "silently absent from the STEP; KiCad does not warn per part",
            "not_included": "panelisation / V-cut / stencil / impedance report / "
                            "stackup drawing are not produced here",
        },
        "source": {"path": path, "sha256": manifest["source"]["sha256"]},
    }


def _version():
    from .. import __version__
    return __version__


# ------------------------------------------------------------------ graphics
_GRAPHIC_FMT = {
    "pdf": ("pcb", "export", "pdf", "board.pdf"),
    "svg": ("pcb", "export", "svg", "board.svg"),
    "dxf": ("pcb", "export", "dxf", "board.dxf"),
    "ps": ("pcb", "export", "ps", "board.ps"),
    "3dpdf": ("pcb", "export", "3dpdf", "board-3d.pdf"),
}


def board_layers(path):
    """The layer names the board file itself declares, in file order.

    `kicad-cli` refuses a plot with no `-l` ("at least one layer"), so the layer
    list has to come from the design, not from a guess.
    """
    from ..adapters import kicad_pcb as KD
    with open(path, "rb") as fh:
        text = fh.read().decode("utf-8", errors="replace")
    tree = KD.parse_sexpr(text)
    node = KD.child(tree, "layers") if tree else None
    names = []
    if node:
        for item in node[1:]:
            if isinstance(item, list) and len(item) >= 2 and isinstance(item[1], str):
                names.append(item[1])
    if not names:
        raise FabError("the board file declares no layers", "PARSE_FAILED")
    return names


def _default_layers(path):
    names = board_layers(path)
    copper = [n for n in names if n.endswith(".Cu")]
    keep = copper + [n for n in ("F.SilkS", "B.SilkS", "Edge.Cuts") if n in names]
    return keep or names


def graphics(path, fmt="pdf", layers=None, out_dir=None, allow_beside_source=False,
             black_and_white=False, timeout=600):
    """Layer plot for humans (pdf / svg / dxf / ps / 3d pdf).

    `layers` defaults to every copper layer plus silk + Edge.Cuts, read from the
    board file - `kicad-cli` refuses to plot without an explicit layer list.
    """
    fmt = (fmt or "pdf").lower()
    if fmt not in _GRAPHIC_FMT:
        raise FabError("unknown graphic format: %s" % fmt, "ARG_INVALID",
                       "known: %s" % ", ".join(sorted(_GRAPHIC_FMT)))
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        raise FabError("this tool never creates files: %s" % path, "PROJECT_NOT_FOUND")
    out = _resolve_out(path, out_dir, allow_beside_source)
    before = _snapshot(out)
    used = list(layers) if layers else _default_layers(path)
    cmd, target = _GRAPHIC_FMT[fmt][0], _GRAPHIC_FMT[fmt][3]
    # dxf always writes one file per layer into a directory; the others take a file
    # path when mode-single is in force.
    dest = out if fmt == "dxf" else os.path.join(out, target)
    args = [cmd, "export", _GRAPHIC_FMT[fmt][2]]
    if fmt in ("pdf", "svg"):
        args.append("--mode-single")
    args += ["-l", ",".join(used), "-o", dest]
    if black_and_white and fmt in ("pdf", "svg", "ps"):
        args.append("--black-and-white")
    args.append(path)
    _run(args, timeout=timeout)
    after = _snapshot(out)
    fresh = [{"name": rel, "bytes": v[0], "sha256": v[1]}
             for rel, v in sorted(after.items()) if before.get(rel) != v]
    return {"status": "ok" if fresh else "error",
            "out_dir": out, "format": fmt, "layers": used, "files": fresh,
            "assumptions": {"who_wrote_the_files": "kicad-cli (%s)" % engine.version(),
                            "layers": "given explicitly" if layers else
                                      "every copper layer + silk + Edge.Cuts, read "
                                      "from the board file's (layers) node",
                            "dxf": "one file per layer, named <board>-<Layer>.dxf",
                            "pdf_svg": "one file, mode-single, layers overlaid"}}


# ------------------------------------------------------------------ 3D
_3D_FMT = {
    "step": ("step", "board.step"),
    "stpz": ("stpz", "board.stpz"),
    "brep": ("brep", "board.brep"),
    "glb": ("glb", "board.glb"),
    "stl": ("stl", "board.stl"),
    "ply": ("ply", "board.ply"),
    "vrml": ("vrml", "board.wrl"),
    "xao": ("xao", "board.xao"),
    "u3d": ("u3d", "board.u3d"),
}


def threed(path, fmt="step", out_dir=None, allow_beside_source=False,
           board_only=False, timeout=900):
    """3D solid export (STEP / GLB / STL / ...)."""
    fmt = (fmt or "step").lower()
    if fmt not in _3D_FMT:
        raise FabError("unknown 3D format: %s" % fmt, "ARG_INVALID",
                       "known: %s" % ", ".join(sorted(_3D_FMT)))
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        raise FabError("this tool never creates files: %s" % path, "PROJECT_NOT_FOUND")
    out = _resolve_out(path, out_dir, allow_beside_source)
    before = _snapshot(out)
    target = os.path.join(out, _3D_FMT[fmt][1])
    args = ["pcb", "export", _3D_FMT[fmt][0], "-o", target]
    if fmt in ("step", "stpz", "brep"):
        args.append("--force")
    if board_only:
        args.append("--board-only")
    args.append(path)
    _run(args, timeout=timeout)
    after = _snapshot(out)
    fresh = [{"name": rel, "bytes": v[0], "sha256": v[1]}
             for rel, v in sorted(after.items()) if before.get(rel) != v]
    checks = {}
    if fmt in ("step", "stpz") and fresh:
        for rel in [f["name"] for f in fresh]:
            full = os.path.join(out, rel)
            if rel.endswith(".step") or rel.endswith(".stp"):
                checks[rel] = _check_step(full)
    return {"status": "ok" if fresh else "error",
            "out_dir": out, "format": fmt, "files": fresh, "checks": checks,
            "assumptions": {
                "who_wrote_the_files": "kicad-cli (%s)" % engine.version(),
                "missing_models": "a component whose 3D model path does not resolve "
                                  "is silently absent; there is no per-part warning",
                "board_only": board_only,
            }}


# ------------------------------------------------------------------ schematic
_SCH_FMT = {
    "pdf": ("pdf", "schematic.pdf"),
    "svg": ("svg", "schematic.svg"),
    "bom": ("bom", "bom.csv"),
    "python-bom": ("python-bom", "bom.xml"),
    "netlist": ("netlist", "netlist.net"),
    "dxf": ("dxf", "schematic.dxf"),
    "ps": ("ps", "schematic.ps"),
}


def schematic_export(path, fmt="pdf", out_dir=None, allow_beside_source=False,
                     fields=None, group_by=None, netlist_format=None,
                     exclude_dnp=False, timeout=600):
    """Schematic output: BOM, netlist, PDF/SVG/DXF, legacy XML BOM."""
    fmt = (fmt or "pdf").lower()
    if fmt not in _SCH_FMT:
        raise FabError("unknown schematic format: %s" % fmt, "ARG_INVALID",
                       "known: %s" % ", ".join(sorted(_SCH_FMT)))
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        raise FabError("this tool never creates files: %s" % path, "PROJECT_NOT_FOUND")
    out = _resolve_out(path, out_dir, allow_beside_source)
    before = _snapshot(out)
    target = os.path.join(out, _SCH_FMT[fmt][1])
    args = ["sch", "export", _SCH_FMT[fmt][0], "-o", target]
    if fmt == "bom":
        ordered = fields or ["Reference", "Value", "Footprint", "QUANTITY", "DNP",
                             "Description", "LCSC"]
        args += ["--fields", ",".join(ordered)]
        args += ["--labels", ",".join(ordered)]
        if group_by:
            args += ["--group-by", group_by]
        if exclude_dnp:
            args.append("--exclude-dnp")
    if fmt == "netlist":
        args += ["--format", netlist_format or "kicadsexpr"]
    args.append(path)
    _run(args, timeout=timeout)
    after = _snapshot(out)
    fresh = [{"name": rel, "bytes": v[0], "sha256": v[1]}
             for rel, v in sorted(after.items()) if before.get(rel) != v]
    result = {"status": "ok" if fresh else "error", "out_dir": out, "format": fmt,
              "files": fresh,
              "assumptions": {"who_wrote_the_files": "kicad-cli (%s)" % engine.version()}}
    if fmt == "bom" and fresh:
        rows = bom_rows(os.path.join(out, fresh[0]["name"]))
        result["rows"] = rows
        result["counts"] = {"lines": len(rows),
                            "placements": sum(int(r.get("Qty") or r.get("QUANTITY") or 0)
                                              for r in rows)}
    if fmt == "netlist" and fresh:
        full = os.path.join(out, fresh[0]["name"])
        result["counts"] = netlist_summary(full, netlist_format or "kicadsexpr")
    return result


def bom_rows(csv_path):
    """Parse a BOM CSV into dicts, keeping the header exactly as KiCad wrote it."""
    with open(csv_path, "r", encoding="utf-8", errors="replace", newline="") as fh:
        reader = csv.DictReader(fh)
        rows = []
        for row in reader:
            clean = {}
            for k, v in row.items():
                if k is None:
                    continue
                clean[(k or "").strip()] = (v or "").strip()
            if any(clean.values()):
                rows.append(clean)
        return rows


def netlist_summary(path, fmt="kicadsexpr"):
    """Count nets / components / pins by parsing the netlist, not by grepping it.

    `kicadsexpr` nests `(net (code ..) (name ..) (node ..))`, so a substring count
    is wrong the moment the writer reformats; this walks the tree instead.
    """
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    out = {"format": fmt, "bytes": len(text)}
    if fmt in ("kicadsexpr",):
        from ..adapters import kicad_pcb as KD
        try:
            tree = KD.parse_sexpr(text)
        except Exception:                              # noqa: BLE001
            tree = None
        if tree and tree[0] == "export":
            nets_node = _find_node(tree, "nets")
            comps_node = _find_node(tree, "components")
            nets = _children(nets_node, "net")
            comps = _children(comps_node, "comp")
            nodes = 0
            named = []
            for net in nets:
                nodes += len(_children(net, "node"))
                name = _atom1(_child(net, "name"))
                if name:
                    named.append(name)
            refs = [_atom1(_child(c, "ref")) for c in comps]
            out.update({"nets": len(nets), "nodes": nodes, "components": len(comps),
                        "net_names": named[:200], "refs": [r for r in refs if r][:400],
                        "parsed": True})
            return out
        out["parsed"] = False
        return out
    if fmt == "kicadxml":
        # `<net/>`, `<net code=...>` and `<net\n` all occur; a boundary regex is the
        # only form that does not silently return 0 on a reformatted file.
        out["nets"] = len(re.findall(r"<net[\s/>]", text))
        out["components"] = len(re.findall(r"<comp[\s/>]", text))
        out["parsed"] = "rough string count (format is XML, not walked)"
        return out
    out["lines"] = len([l for l in text.splitlines() if l.strip()])
    out["parsed"] = "line count (format %s is not a netlist tree)" % fmt
    return out


# The netlist tree from KiCad 10 does not put `nets` where a fixed path expects it
# (it can end up nested under another node), so the lookup is a search, not a path.
def _find_node(node, key, depth=0):
    if not isinstance(node, list) or depth > 40:
        return None
    if node and node[0] == key:
        return node
    for item in node:
        found = _find_node(item, key, depth + 1)
        if found is not None:
            return found
    return None


def _child(node, key):
    if not isinstance(node, list):
        return None
    for item in node[1:]:
        if isinstance(item, list) and item and item[0] == key:
            return item
    return None


def _children(node, key):
    if not isinstance(node, list):
        return []
    return [item for item in node[1:]
            if isinstance(item, list) and item and item[0] == key]


def _atom1(node):
    if isinstance(node, list) and len(node) > 1 and isinstance(node[1], str):
        return node[1]
    return None
