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
        # Fields the file does not state are reported as None AND listed in
        # `not_stated_in_file`, so a summary can say "I do not know" instead of
        # answering with something that merely looks like a default. The one
        # value that is not the file's is the pad connection: KiCad spells a
        # thermal relief by writing no mode word at all, so `connect_pads` stays
        # None (the file said nothing) while `connect_pads_effective` says how
        # KiCad reads it.
        conn = S.child(node, "connect_pads")
        fat_node = S.child(node, "filled_areas_thickness")
        fat = S.arg_text(fat_node, 1) if fat_node is not None else None
        words = ([w.text for w in conn.items[1:] if isinstance(w, S.Atom)]
                 if conn is not None else [])
        written = None
        for word, mode in (("yes", "solid"), ("no", "none"),
                           ("thru_hole_only", "thru_hole_only")):
            if word in words:
                written = mode
        fill_node = S.child(node, "fill")
        thermal_gap = (S.arg_num(S.child(fill_node, "thermal_gap"), 1)
                       if fill_node is not None else None)
        thermal_bridge = (S.arg_num(S.child(fill_node, "thermal_bridge_width"), 1)
                          if fill_node is not None else None)
        not_stated = [name for name, value in (
            ("min_thickness", S.arg_num(S.child(node, "min_thickness"), 1)),
            ("connect_pads", written),
            ("filled_areas_thickness", fat),
            ("thermal_gap", thermal_gap),
            ("thermal_bridge_width", thermal_bridge)) if value is None]
        rows.append({
            "net": net_name or None,
            "layer": layer or None,
            "keepout": keepout,
            "fill_flag": fill_atom or None,
            "min_thickness_mm": S.arg_num(S.child(node, "min_thickness"), 1),
            "connect_pads": written,
            "connect_pads_effective": (written if written is not None
                                       else ("thermal" if conn is not None
                                             else None)),
            "connect_pads_source": ("file" if written is not None else
                                    "kicad_default" if conn is not None else
                                    "absent"),
            "filled_areas_thickness": fat,
            "thermal_gap_mm": thermal_gap,
            "thermal_bridge_width_mm": thermal_bridge,
            "not_stated_in_file": not_stated,
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
            "not_stated_in_file": {
                "meaning": "a field listed in a zone's `not_stated_in_file` is "
                           "ABSENT from the file: the answer for it is null, not a "
                           "default this server believes in",
                "fields": sorted({k for r in rows
                                  for k in r["not_stated_in_file"]}),
            },
            "connect_pads": (
                "`connect_pads` is what the file writes and may be null: a thermal "
                "relief is spelled by writing NO mode word, which is how KiCad "
                "itself saves one. `connect_pads_effective` is how pcbnew reads it "
                "(verified on 10.0.7: the word-less form comes back as "
                "ZONE_CONNECTION_THERMAL, `yes` as SOLID, `no` as NONE)"),
            "filled_areas_thickness": (
                "when the file states it, `no` means the filled polygons ARE the "
                "copper edge and `yes` means they are a centreline that KiCad "
                "strokes by min_thickness/2 - on a file this server writes the "
                "token is always present, because a pre-v7 zone without it made "
                "DRC report 386 clearance errors on a real board"),
        },
    )


def _without_previous_fill(doc):
    """Drop every `filled_polygon` from the zones. Returns how many were dropped.

    Why this is not a cosmetic step: a `filled_polygon` is the filler's own
    OUTPUT, never its input - KiCad recomputes it from `(polygon)` plus the
    clearance rules every time it fills. But pcbnew 10's `LoadBoard` was measured
    to hang (no return in 300 s, `faulthandler` inside `LoadBoard`) on the boards
    this project writes once they carry a filled polygon, while `kicad-cli` reads
    the very same file without complaint. Handing the engine a board that carries
    its own previous output therefore buys nothing and can cost the whole call,
    so the copy it fills has the stale polygons removed. The polygons that end up
    in the answer are the ones it just produced, and the transplant still writes
    them into the caller's file.
    """
    board = doc.board()
    dropped = 0
    for zone in S.children(board, "zone"):
        for poly in list(S.children(zone, "filled_polygon")):
            S.remove_child(zone, poly)
            dropped += 1
    return dropped

# ------------------------------------------------------------------- filling
def fill(path, in_place=False, timeout=300, verify_drc=True):
    """Ask the vendor engine to pour the zones; measure it, optionally keep it.

    `in_place=False` (default): the input file is never touched. The fill runs on
    a temp copy and the reply describes the copper KiCad would pour.

    `in_place=True`: the polygons KiCad produced are transplanted into the file,
    which keeps its own version header and every other byte. A checkpoint is
    written first, so the previous bytes can be restored.

    `verify_drc=True`: once the file carries the new copper, run the vendor DRC
    on it and report the verdict as `drc_after_fill`. This exists because a
    poured zone is not the same thing as a legal pour: on the reference board
    the engine reported "filled" and the file still held 386 clearance errors
    (a pre-v7 zone with no `filled_areas_thickness` is read as a stroked
    centreline, so DRC measures min_thickness/2 closer than the copper really
    is). Measured there: 386 errors absent the token, 0 with `(filled_areas_thickness no)`.
    Leaving that discovery to the caller is how a "fill ok" reply becomes a
    wrong conclusion, so the gate is on by default.
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
        engine_input = _kdoc.KiCadDocument.from_path(path)
        stale_dropped = _without_previous_fill(engine_input)
        with open(src_copy, "wb") as fh:
            fh.write(engine_input.to_bytes())
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
        result = _fill_ok(after_rows, rows_before, build, written, checkpoint,
                          path, stale_dropped,
                          _format_generation_note(before, after_rows)
                          if written else None)
        if verify_drc:
            # Measuring mode writes nothing, so `path` still holds an OUTLINE:
            # running the DRC on it would describe the board before the pour and
            # blame nothing. Check the file that actually carries the copper.
            _attach_drc_gate(result, path,
                             None if written else dst,
                             "the written file" if written
                             else "a temporary copy (this call wrote nothing)")
        return result

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
    # Pair our zones with the engine's by (net, layer), not by file position:
    # pcbnew re-orders zones when it saves (a zone appended last in the file
    # came back in the middle), and a positional pairing then refuses a pour
    # that is perfectly fine. Duplicates of the same net/layer stay greedy in
    # source order, so two pour islands of one net keep their own polygons.
    def _key(row):
        return (row["net"], row["layer"])

    pool = {}
    for row in rows_after:
        pool.setdefault(_key(row), []).append(row)
    pairs = []
    for ours in rows_ours:
        bucket = pool.get(_key(ours)) or []
        if not bucket:
            return (False, None, _err(
                "ENGINE_MISMATCH",
                "the engine returned no zone for %r/%r although the file has one; "
                "no polygon may be moved" % _key(ours)))
        pairs.append((ours, bucket.pop(0)))
    if any(bucket for bucket in pool.values()):
        extra = [k for k, bucket in pool.items() if bucket]
        return (False, None, _err(
            "ENGINE_MISMATCH",
            "the engine returned zones the file does not have (%s); the pour "
            "would land on the wrong net" % extra))

    transplanted = 0
    for ours, theirs in pairs:
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


def _format_generation_note(doc, rows):
    """Warn when a pre-v7 file was given polygons computed by a modern KiCad.

    Measured, not guessed. On the reference pour (40x30 mm, one GND zone,
    min_thickness 0.25, zone clearance 0.35) the transplanted vertices are
    **identical** to the ones `kicad-cli pcb drc --refill-zones --save-board`
    produces (254 vertices, same set), and yet:

    * this file (format 20221018): KiCad 10 DRC reports `clearance 0.35 实际
      0.2255` three times - 0.2255 = 0.35 - 0.1245, i.e. exactly min_thickness/2;
    * the same polygons in a pcbnew-saved copy of it: no clearance violation;
    * this file with the zone's min_thickness set to 0: no clearance violation.

    So the copper is right and the *interpretation* differs: a pre-v7
    `filled_polygon` is read as the CENTRELINE of a `min_thickness`-wide stroke,
    and KiCad 10's DRC measures to that stroke's outer edge. A v7+ file says the
    same thing the other way round, which is why pcbnew's own save is clean.
    """
    version = S.arg_text(S.child(doc.board(), "version"), 1) or ""
    try:
        generation = int(version)
    except (TypeError, ValueError):
        return None
    if generation >= 20230000:
        return None
    half = []
    for row in rows:
        node = S.child(row["_node"], "min_thickness")
        value = S.arg_num(node, 1) if node is not None else None
        if value and value > 0:
            half.append(round(value / 2.0, 6))
    if not half:
        return None
    return {
        "file_version": version,
        "stroke_halfwidth_mm": max(half),
        "what_is_measured": (
            "the transplanted vertices are identical to `kicad-cli pcb drc "
            "--refill-zones --save-board`'s (254 vertices on the reference "
            "pour), yet KiCad 10's DRC flags this file's pour at 0.2255 mm "
            "against a declared 0.35 - exactly min_thickness/2 closer - while "
            "a pcbnew-saved copy of the same board is clean"),
        "why": (
            "a pre-v7 `filled_polygon` is read as the centreline of a "
            "min_thickness-wide stroke, so DRC measures its outer edge; a v7+ "
            "file reads it as the copper outline. The copper is the engine's "
            "either way - this file just declares an older format generation "
            "than the polygons it now carries"),
        "remedy": ("save/convert the board with KiCad (pcbnew) before running "
                   "DRC, or lower the zone's min_thickness, or raise the zone's "
                   "clearance by min_thickness/2 if a pre-v7 reader must pass"),
    }

def _fill_ok(rows, rows_before, build, written, checkpoint, path,
             stale_dropped=0, format_note=None):
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
            "stale_polygons_dropped_before_the_engine": stale_dropped,
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
            "engine_saw_no_stale_polygons": (stale_dropped > 0),
            "why_the_polygons_were_dropped": (
                "a filled_polygon is the filler's own output, so it is removed "
                "from the copy handed to the engine and recomputed; on these "
                "boards pcbnew's LoadBoard was measured to hang when the file "
                "still carried one"),
            "format_generation": format_note,
            "path": path,
        },
    )


def _attach_drc_gate(result, path, drc_path=None, measured_on=None):
    """Run the vendor DRC on the poured file and say whether the pour is legal.

    Two different questions, deliberately kept apart:
      * did the filler produce copper?      -> `written_to_file` / `poured_zones`
      * is that copper a legal pour?        -> `drc_after_fill.verdict`
    A board can answer yes to the first and no to the second (386 errors on the
    reference pour), so a single boolean would be a lie.
    """
    from ..edit import plan as _plan          # `_project_beside` lives there
    from . import engine as _engine
    value = result.get("value") or {}
    notes = result.setdefault("assumptions", {})
    pro = _plan._project_beside(path)
    drc = _engine.drc(drc_path or path, severity="all", project=pro)
    if drc.get("status") != "ok":
        value["drc_after_fill"] = {
            "ran": False,
            "verdict": "unknown",
            "reason_code": drc.get("reason_code"),
            "message": drc.get("message"),
            "why_it_matters": ("the pour was produced but never checked, so this "
                               "reply says nothing about whether it is legal"),
        }
        notes["fill_is_not_a_drc_pass"] = (
            "the DRC could not run, so `written_to_file` reports the filler's "
            "output only; it is not a statement that the pour passes DRC")
        return
    by_sev = drc.get("by_severity") or {}
    errors = int(by_sev.get("error") or 0)
    warnings = int(by_sev.get("warning") or 0)
    unconnected = int(drc.get("unconnected_total") or 0)
    clean = errors == 0 and unconnected == 0
    top = [v for v in (drc.get("violations") or [])
           if str(v.get("severity")).lower() == "error"][:10]
    value["drc_after_fill"] = {
        "ran": True,
        "verdict": "clean" if clean else "not_clean",
        "errors": errors,
        "warnings": warnings,
        "unconnected_total": unconnected,
        "violations_total": drc.get("violations_total"),
        "by_type": drc.get("by_type") or {},
        "top_errors": top,
        "rule_source": drc.get("rule_source"),
        "project_file_used": drc.get("project_file_used"),
        "authoritative": True,
        "verdict_source": drc.get("verdict_source"),
        "measured_on": measured_on or "the written file",
    }
    notes["fill_success_is_not_pour_quality"] = (
        "`written_to_file` answers 'did the engine pour'; `drc_after_fill` "
        "answers 'is that pour legal'. They are different questions and this "
        "call keeps them apart on purpose")
    if not clean:
        value["drc_after_fill"]["what_to_do"] = (
            "read `top_errors[].description` (it carries the constraint and the "
            "measured value), fix the zone header first (clearance / "
            "min_thickness / `filled_areas_thickness`) before touching geometry - "
            "on the reference board every one of the 386 clearance errors was a "
            "pre-v7 stroke reading, not missing copper")

def _err(code, message):
    from ..calc.result import err
    return err(message, code)


# ------------------------------------------------------- shaped pour outlines
_COPPER_LAYERS = ("F.Cu", "B.Cu")


def _fmt_mm(value):
    """KiCad writes millimetres; keep 3 decimals and never an exponent."""
    text = ("%.3f" % float(value)).rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def _available_nets(board):
    """Net names this board actually has, gathered from the copper that uses them.

    KiCad 10 writes no top-level net table, so the only honest source is the
    `(net "...")` atoms inside pads and zones - which is also the set a new
    zone can legally name."""
    names = set()
    for _path, item in S.walk(board):
        if S.head(item) in ("pad", "zone", "segment", "via", "arc", "gr_line"):
            node = S.child(item, "net")
            if node is not None:
                text = S.arg_text(node, 1)
                if text:
                    names.add(str(text))
    return sorted(names)


def add_zone(path, net, layer, points, holes=None, priority=9,
             clearance=0.25, thermal_gap=0.5, thermal_bridge_width=0.5,
             min_thickness=0.25, connect="thermal", fill_it=True,
             verify_drc=True):
    """Give a zone a SHAPED outline, then let KiCad pour it.

    This exists because a pour is not a set of rectangles: a power pour follows
    the copper it serves, with 45-degree transitions at the jogs, and a zone
    block dropped on the middle of a plane cuts that plane into islands. The
    caller supplies the polygon; nothing here invents geometry, rounds corners
    or "optimises" the outline - a shape this server made up would be a shape
    nobody can review.

    What it will not do:
      * name a net the board does not have (a zone on a phantom net pours
        nothing and reads as copper in every later summary);
      * accept a polygon with fewer than 3 points or zero area;
      * report success off its own say-so - `fill_it` asks KiCad's own
        ZONE_FILLER to pour it, and `verify_drc` asks KiCad's own DRC whether
        the copper it poured is legal.

    A checkpoint is written before the file changes; if the engine refuses the
    pour the original bytes are restored.
    """
    from ..edit import plan as _plan
    import uuid as _uuid

    path = os.path.abspath(path)
    if not os.path.isfile(path):
        return _err("PROJECT_NOT_FOUND", "no such board: %s" % path)
    layer = str(layer or "")
    if layer not in _COPPER_LAYERS:
        return _err("BAD_LAYER",
                    "a pour needs a copper layer (%s), got %r"
                    % (" / ".join(_COPPER_LAYERS), layer))
    pts = []
    for p in (points or []):
        try:
            x, y = float(p[0]), float(p[1])
        except Exception:
            return _err("BAD_POINTS", "every point must be [x, y] in mm; got %r" % (p,))
        pts.append((x, y))
    if len(pts) < 3:
        return _err("BAD_POINTS",
                    "a polygon needs at least 3 points, got %d" % len(pts))
    if len(set(pts)) != len(pts):
        return _err("BAD_POINTS", "the outline repeats a point; KiCad reads that as "
                                  "a degenerate contour")
    area = abs(G.polygon_area(pts))
    if area < 0.01:
        return _err("BAD_POINTS", "the outline encloses %.6f mm^2; nothing to pour"
                    % area)
    hole_pts = []
    for h in (holes or []):
        hp = [(float(q[0]), float(q[1])) for q in h]
        if len(hp) < 3:
            return _err("BAD_POINTS", "a hole needs at least 3 points")
        hole_pts.append(hp)

    doc = _kdoc.KiCadDocument.from_path(path)
    board = doc.board()
    nets = _available_nets(board)
    if net not in nets:
        return _err("NET_NOT_FOUND",
                    "this board has no net %r; a zone on it would pour nothing. "
                    "%d nets exist, e.g. %s"
                    % (net, len(nets), ", ".join(nets[:12])))
    mode = {"solid": "yes", "thermal": None, "none": "no",
            "thru_hole_only": "thru_hole_only"}.get(str(connect))
    if str(connect) not in ("solid", "thermal", "none", "thru_hole_only"):
        return _err("BAD_ARGS", "connect must be solid / thermal / none / "
                                "thru_hole_only")

    def _pts_block(points_mm):
        return " ".join("(xy %s %s)" % (_fmt_mm(x), _fmt_mm(y))
                        for x, y in points_mm)

    text = ("(zone (net %s) (layer %s) (uuid %s) (hatch edge 0.5) (priority %d) "
            "(connect_pads %s(clearance %s)) (min_thickness %s) "
            "(filled_areas_thickness no) "
            "(fill yes (thermal_gap %s) (thermal_bridge_width %s) "
            "(island_removal_mode 0)) "
            "(polygon (pts %s))%s)"
            % ('"%s"' % net, '"%s"' % layer, '"%s"' % _uuid.uuid4(),
               int(priority), (mode + " ") if mode else "",
               _fmt_mm(clearance), _fmt_mm(min_thickness),
               _fmt_mm(thermal_gap), _fmt_mm(thermal_bridge_width),
               _pts_block(pts),
               "".join(" (polygon (pts %s))" % _pts_block(h) for h in hole_pts)))
    try:
        node = S.parse_single(text)
        S.append_child(board, node)
    except Exception as exc:  # noqa: BLE001
        return _err("BAD_POINTS", "could not build the zone: %s" % exc)

    with open(path, "rb") as fh:
        raw = fh.read()
    checkpoint = _plan.save_checkpoint(path, raw, "before adding a shaped zone")
    _plan._atomic_write(path, doc.to_bytes())

    outline_mm2 = round(area, 3)
    value = {
        "net": net, "layer": layer, "points": len(pts), "holes": len(hole_pts),
        "priority": int(priority), "connect": str(connect),
        "outline_area_mm2": outline_mm2,
        "checkpoint": checkpoint,
        "file_written": True,
        "poured": False,
        "filled": None,
    }
    if not fill_it:
        from ..calc.result import ok
        return ok(value=value, unit="zone outline / mm2",
                  model="zone_created_but_not_filled",
                  basis="the outline was written; KiCad has not been asked to pour "
                        "it yet",
                  assumptions={"fill_is_a_snapshot": True,
                               "next_step": "call board_fill_zones or this tool "
                                            "with fill_it=true before judging "
                                            "connectivity"})
    poured = fill(path, in_place=True, verify_drc=verify_drc)
    if isinstance(poured, dict) and str(poured.get("status")) == "error":
        _plan._atomic_write(path, raw)
        poured["hint"] = ("the zone outline was rolled back to the bytes before "
                          "this call (%s still holds them)" % checkpoint)
        return poured
    body = poured.get("value", {}) if isinstance(poured, dict) else {}
    value["poured"] = True
    value["filled"] = {"zones": body.get("zones"),
                       "poured_zones": body.get("poured_zones"),
                       "fill_area_mm2": body.get("fill_area_mm2"),
                       "written_to_file": body.get("written_to_file"),
                       "drc_after_fill": body.get("drc_after_fill")}
    result = dict(poured) if isinstance(poured, dict) else {}
    value["outline"] = {"net": net, "layer": layer, "points": len(pts),
                        "holes": len(hole_pts), "outline_area_mm2": outline_mm2}
    merged = dict(result.get("value") or {})
    merged.update(value)
    result["value"] = merged
    return result
