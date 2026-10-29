"""Make the vendor engine actually FILL the zones, then report what it built.

`add_zone` writes a zone *outline* and `(fill yes)`; it cannot know what copper
KiCad will pour, because pouring depends on clearance, thermals, islands and the
board outline. Until the zone is filled, every question about the pour
(`board_pour_connectivity`, `zone_fill` presence, Gerber copper) is answered from
an outline - which is the "intermediate layer says one thing, the real board says
another" failure this project exists to avoid.

So filling is delegated, exactly like DRC: KiCad ships `pcbnew` as a Python
module with `ZONE_FILLER`, and it computes the pour. Nothing here re-implements
the fill.

Two different writes, deliberately separate:

* **measure** (`fill(path)`): fill a *temp copy* and report the result - zone
  count, filled polygons, poured area, islands, per-zone connectivity. The input
  file is not touched. This answers "what would the pour be".
* **write** (`fill(path, in_place=True)`): transplant the `filled_polygon` nodes
  KiCad produced into OUR file, keeping the file's own version header and every
  other byte. KiCad's own SaveBoard would rewrite the whole board into KiCad-10
  layout and bump `(version 20221018)` to `(version 20260206)`; that silently
  re-serialises a user's file, so it is not what happens to the user's board.
  The transplanted polygons are KiCad's geometry, verbatim - we only move nodes.

Honesty notes that must survive into the tool surface:
  * the fill is ONLY valid for the exact copper it was computed on; after any
    track/pad move the polygons are stale (KiCad does the same thing, hence
    "refill" in its UI),
  * a zone with `(fill no)` or a keepout is not a pour and is reported as such,
  * `min_thickness`, island removal and thermal spokes are KiCad's decisions; the
    report says so instead of pretending we chose them.
"""
import json
import os
import shutil
import subprocess
import tempfile

from . import engine as _engine
from . import kicad_doc as _kdoc
from . import sexpr as S
from .. import geometry as G

_KNOWN_PY = (
    r"D:\KiCad\10.0\bin\python.exe",
    r"C:\Program Files\KiCad\10.0\bin\python.exe",
    r"C:\Program Files\KiCad\9.0\bin\python.exe",
    r"C:\Program Files\KiCad\8.0\bin\python.exe",
)

_CACHE = {}

# Only what is needed: load, fill, connect, save. `BuildConnectivity` before the
# save so the file carries the same connectivity KiCad would write.
_SCRIPT = r"""
import json, sys
import pcbnew

src, dst = sys.argv[1], sys.argv[2]
board = pcbnew.LoadBoard(src)
zones = board.Zones()
count = len(list(zones))
filler = pcbnew.ZONE_FILLER(board)
filler.Fill(zones)
board.BuildConnectivity()
pcbnew.SaveBoard(dst, board)
print(json.dumps({"zones": count, "build": pcbnew.GetBuildVersion()}))
"""


def find_kicad_python():
    """Path to a Python that can `import pcbnew`, or None. Proves it once."""
    if "py" in _CACHE:
        return _CACHE["py"]
    cands = []
    cli = _engine.find_kicad_cli()
    if cli:
        cands.append(os.path.join(os.path.dirname(cli), "python.exe"))
    cands.extend(_KNOWN_PY)
    who = shutil.which("python")
    if who:
        cands.append(who)
    found = None
    for cand in cands:
        if not cand or not os.path.isfile(cand):
            continue
        try:
            proc = subprocess.run(
                [cand, "-c", "import pcbnew; print(pcbnew.GetBuildVersion())"],
                capture_output=True, text=True, timeout=90,
                env=_clean_env())
        except Exception:
            continue
        if proc.returncode == 0 and (proc.stdout or "").strip():
            found = cand
            _CACHE["build"] = (proc.stdout or "").strip()
            break
    _CACHE["py"] = found
    return found


def _clean_env():
    """KiCad's python must not inherit a PYTHONPATH aimed at our sources."""
    env = dict(os.environ)
    for key in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"):
        env.pop(key, None)
    return env


# ------------------------------------------------------------- reading zones
def _zone_rows(board_node):
    rows = []
    for node in S.children(board_node, "zone"):
        net_name = S.arg_text(S.child(node, "net_name"), 1, "") or ""
        if not net_name:
            code = S.arg_text(S.child(node, "net"), 1)
            net_name = "" if code in (None, "", "0") else str(code)
        layer = S.arg_text(S.child(node, "layer"), 1, "") or \
            S.arg_text(S.child(node, "layers"), 1, "")
        keepout = S.child(node, "keepout") is not None
        fill_atom = S.arg_text(S.child(node, "fill"), 1, "")
        polys = []
        for poly in S.children(node, "filled_polygon"):
            pts = S.child(poly, "pts")
            if pts is None:
                continue
            poly_layer = S.arg_text(S.child(poly, "layer"), 1, "") or layer
            pts_list = [(S.arg_num(p, 1), S.arg_num(p, 2))
                        for p in S.children(pts, "xy")]
            pts_list = [p for p in pts_list if p[0] is not None]
            if len(pts_list) >= 3:
                polys.append({"layer": poly_layer, "points": pts_list,
                              "area_mm2": round(abs(G.polygon_area(pts_list)), 3),
                              "island": S.child(poly, "island") is not None})
        outline = None
        poly_node = S.child(node, "polygon")
        if poly_node is not None:
            pts = S.child(poly_node, "pts")
            if pts is not None:
                pts_list = [(S.arg_num(p, 1), S.arg_num(p, 2))
                            for p in S.children(pts, "xy")]
                outline = [p for p in pts_list if p[0] is not None]
        rows.append({
            "net": net_name or None,
            "layer": layer or None,
            "keepout": keepout,
            "fill_flag": fill_atom or None,
            "outline_area_mm2": (round(abs(G.polygon_area(outline)), 3)
                                 if outline and len(outline) >= 3 else None),
            "filled_polygons": len(polys),
            "fill_area_mm2": round(sum(p["area_mm2"] for p in polys), 3),
            "_node": node,
            "_polys": polys,
        })
    return rows


def _summary_row(row):
    return {k: v for k, v in row.items() if not k.startswith("_")}


def zones(path):
    """What the file currently pours, read-only. No fill is attempted."""
    doc = _kdoc.KiCadDocument.from_path(path)
    return _zones_ok(_zone_rows(doc.board()))


def _zones_ok(rows):
    from ..calc.result import ok
    poured = [r for r in rows if r["filled_polygons"]]
    return ok(
        value={"zones": len(rows), "poured_zones": len(poured),
               "pour_area_mm2": round(sum(r["fill_area_mm2"] for r in poured), 3),
               "per_zone": [_summary_row(r) for r in rows]},
        unit="zones / mm2",
        model="zone_outline_and_filled_polygon_reader",
        basis="zone nodes and their filled_polygon children, read from the file "
              "as written by whatever engine last filled it",
        precision="areas are polygon shoelace sums of the polygon vertices; "
                  "islands and thermal spokes were baked in by the filling engine",
        assumptions={
            "fill_is_a_snapshot": True,
            "why_stale": "the polygons describe the copper at the moment the "
                         "engine filled it; any later track/pad change makes them "
                         "stale until the zones are filled again",
            "unfillable": [r["layer"] for r in rows if r["keepout"]],
        },
    )


# ------------------------------------------------------------------- filling
def fill(path, in_place=False, timeout=300):
    """Ask the vendor engine to pour the zones; measure it, optionally keep it.

    `in_place=False` (default): the input file is never touched. The fill runs on
    a temp copy and the reply describes the copper KiCad would pour.

    `in_place=True`: the polygons KiCad produced are transplanted into the file,
    which keeps its own version header and every other byte. A checkpoint is
    written first, so the previous bytes can be restored.
    """
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        return _err("PROJECT_NOT_FOUND", "no such board: %s" % path)
    py = find_kicad_python()
    if not py:
        return _err("KICAD_PYTHON_NOT_FOUND",
                    "no Python with the `pcbnew` module was found (looked next to "
                    "kicad-cli and in the usual install dirs); the fill cannot be "
                    "computed by anything but KiCad")
    before = _kdoc.KiCadDocument.from_path(path)
    rows_before = _zone_rows(before.board())
    if not rows_before:
        return _err("NO_GEOMETRY", "this board has no zone to fill")

    tmp = tempfile.mkdtemp(prefix="pcb_agent_fill_")
    try:
        src_copy = os.path.join(tmp, "src.kicad_pcb")
        dst = os.path.join(tmp, "filled.kicad_pcb")
        shutil.copyfile(path, src_copy)
        env = _clean_env()
        try:
            proc = subprocess.run([py, "-c", _SCRIPT, src_copy, dst],
                                  capture_output=True, text=True,
                                  timeout=timeout, cwd=tmp, env=env)
        except subprocess.TimeoutExpired:
            return _err("TIMEOUT", "the engine did not finish filling in %ds"
                        % timeout)
        if proc.returncode != 0 or not os.path.isfile(dst):
            return _err("ENGINE_FAILED",
                        "pcbnew refused to fill the zones: %s"
                        % ((proc.stderr or proc.stdout or "").strip()[-600:] or
                           "no message"))
        build = None
        try:
            build = json.loads((proc.stdout or "").strip().splitlines()[-1]).get("build")
        except Exception:
            build = _CACHE.get("build")

        filled_doc = _kdoc.KiCadDocument.from_path(dst)
        rows_after = _zone_rows(filled_doc.board())

        written = False
        checkpoint = None
        if in_place:
            written, checkpoint, transplant_err = _transplant(path, rows_before,
                                                             rows_after)
            if transplant_err:
                return transplant_err

        after_rows = _zone_rows(_kdoc.KiCadDocument.from_path(path).board()) \
            if written else rows_after
        return _fill_ok(after_rows, rows_before, build, written, checkpoint,
                        path)

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _transplant(path, rows_before, rows_after):
    """Copy KiCad's `filled_polygon` nodes into our file, byte-preserving."""
    from ..edit import plan as _plan          # atomic write + checkpoints live there
    if len(rows_before) != len(rows_after):
        return (False, None, _err(
            "ENGINE_MISMATCH",
            "the engine read %d zones where the file has %d; refusing to transplant"
            % (len(rows_after), len(rows_before))))
    with open(path, "rb") as fh:
        raw = fh.read()
    checkpoint = _plan.save_checkpoint(path, raw, "before zone fill")
    doc = _kdoc.KiCadDocument.from_path(path)
    rows_ours = _zone_rows(doc.board())
    if len(rows_ours) != len(rows_before):
        return (False, None, _err("ENGINE_MISMATCH",
                                  "zone count changed under our feet"))
    transplanted = 0
    for ours, theirs in zip(rows_ours, rows_after):
        if ours["net"] != theirs["net"] or ours["layer"] != theirs["layer"]:
            return (False, None, _err(
                "ENGINE_MISMATCH",
                "zone %r/%r came back as %r/%r; the engine reordered the zones, "
                "so no polygon may be moved by index"
                % (ours["net"], ours["layer"], theirs["net"], theirs["layer"])))
        node = ours["_node"]
        for old in list(S.children(node, "filled_polygon")):
            S.remove_child(node, old)
        for poly in S.children(theirs["_node"], "filled_polygon"):
            S.append_child(node, poly)
            transplanted += 1
    data = doc.to_bytes()
    _plan._atomic_write(path, data)
    # Prove it: re-parse and demand the polygons are all there.
    check = _zone_rows(_kdoc.KiCadDocument.from_path(path).board())
    got = sum(r["filled_polygons"] for r in check)
    if got != transplanted:
        _plan._atomic_write(path, raw)
        return (False, checkpoint, _err(
            "VERIFY_FAILED",
            "wrote %d filled polygons but read back %d; original bytes restored"
            % (transplanted, got)))
    return (True, checkpoint, None)


def _fill_ok(rows, rows_before, build, written, checkpoint, path):
    from ..calc.result import ok
    poured = [r for r in rows if r["filled_polygons"]]
    empty = [{"net": r["net"], "layer": r["layer"], "why":
              "keepout" if r["keepout"] else "the engine poured nothing here"}
             for r in rows if not r["filled_polygons"]]
    area = round(sum(r["fill_area_mm2"] for r in poured), 3)
    return ok(
        value={
            "zones": len(rows),
            "poured_zones": len(poured),
            "fill_area_mm2": area,
            "written_to_file": bool(written),
            "checkpoint_id": (checkpoint or {}).get("checkpoint_id"),
            "per_zone": [_summary_row(r) for r in rows],
            "poured_nothing": empty,
        },
        unit="zones / mm2",
        model="vendor_zone_filler (pcbnew ZONE_FILLER) + transplant",
        basis=("KiCad's own pcbnew filled the zones on a copy of this file; the "
               "polygons reported are exactly the ones it produced (%s)"
               % (build or "build unknown")),
        precision=("areas are shoelace sums of the filled_polygon vertices KiCad "
                   "wrote, so they are the engine's geometry, not an estimate; "
                   "thermal spokes, islands and `min_thickness` are KiCad's "
                   "decisions and are already baked in"),
        assumptions={
            "fill_is_a_snapshot": True,
            "refill_needed_after": "any track, pad or outline change",
            "file_kept_its_own_format": True,
            "why": ("the file was NOT re-serialised by pcbnew; only its "
                    "filled_polygon nodes were replaced"
                    if written else
                    "nothing was written: this call measured what a fill would "
                    "produce, on a copy"),
            "zones_before": len(rows_before),
            "path": path,
        },
    )


def _err(code, message):
    from ..calc.result import err
    return err(message, code)
