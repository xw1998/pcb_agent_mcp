"""Synthetic LCEDA Pro samples in the format that was **measured** on disk.

Nothing here is a guess about the format. Every record shape, arity, terminator
and unit below was copied from the dumps in `docs/13-立创实测-真实存储与记录格式.md`
(see `docs/13`), and `tests/test_lceda_records.py` re-checks the claims on the
bytes it produces:

    * a member is one compact JSON array per line, documents delimited by
      ``["DOCTYPE", kind, version]``;
    * ``.epcb`` members are LF, ``.efoo`` members are CRLF, the last line has no
      terminator, a literal ``[]`` line and leading ``\\t\\t`` lines do occur;
    * coordinates and sizes are **mil**;
    * ``project.json`` holds the human names: ``footprints[uuid].title`` and
      ``devices[uuid].title`` (the board document only carries uuids).

The point of a fixture is to be *wrong* in the same way a real file is wrong:
the outline in `epro_bytes(outline="open")` really does not close, so the reader
has to report open chains instead of inventing a contour.
"""
import io
import json
import os
import tempfile
import zipfile

MIL = 0.0254

PCB_UUID = "b2f4d387989c4c63ac68533b05f0eee3"
SCH_UUID = "5c1d0f5a1b7e4e2b9d3c6a8f0e2d4c6b"
FP_UUID = "0a1b2c3d4e5f60718293a4b5c6d7e8f9"
DEV_UUID = "9f8e7d6c5b4a39281706f5e4d3c2b1a0"


# ------------------------------------------------------------------ text lines
def _line(item):
    """A record (list) or a verbatim body (str) as the vendor would write it."""
    if isinstance(item, str):
        return item
    return json.dumps(item, separators=(",", ":"), ensure_ascii=False)


def stream(items, term="\n", trailing=False):
    """Join items into one member's text with an explicit terminator."""
    text = term.join(_line(it) for it in items)
    return text + term if trailing else text


# ------------------------------------------------------------------ documents
def pcb_records(outline="closed", fill_records=1, four_layers=False):
    """The `PCB` document body, minus the DOCTYPE line.

    `outline` picks the board edge shape:
        closed - four edges that meet exactly, plus a closed cutout inside
        open   - four edges with one corner left 5 mil short (a real file does
                 this; the reader must not fit a contour over it)
        rect   - a single ``["R", ...]`` rectangle, as in the quick-start project

    `fill_records` is how many ``POURED`` records the GND pour has (real projects
    write up to 4: one poured outline per disjoint island). `four_layers` swaps
    the 2-layer table for a 4-layer one whose two inner layers are both literally
    called "SIGNAL", which is what the vendor writes.
    """
    recs = [
        ["HEAD", {"editorVersion": "2.2.36.7", "importFlag": 0, "uuid": PCB_UUID}],
        ["CANVAS", 0, 0, "mm", 1, 1, 1, 1, 0.1, 0.1, 2],
        ["LAYER", 1, "TOP", "#ff0000"],
        ["LAYER", 2, "BOTTOM", "#0000ff"],
        ["LAYER", 3, "TOP_SILK", "#ffffff"],
        ["LAYER", 11, "OUTLINE", "#ffff00"],
    ] + ([["LAYER", 15, "SIGNAL", "#00ff00"],
          ["LAYER", 16, "SIGNAL", "#00ff00"]] if four_layers else []) + [
        ["NET", "GND", None, None, 1, None, 0, None],
        ["NET", "VCC", None, None, 1, None, 0, None],
        # id, group, layer, x, y, rotation, meta, locked
        ["COMPONENT", "e1", 0, 1, 400, 400, 0,
         {"Unique ID": "t1", "Reuse Block": "", "Group ID": "",
          "Channel ID": ""}, 0],
        # ATTR[3] = parent component id, ATTR[7] = key, ATTR[8] = value.
        ["ATTR", "e1f", 0, "e1", 3, None, None, "Footprint", FP_UUID,
         0, 0, "default", 45, 6, 0, 0, 3, 0, 0, 0, 0, 0],
        ["ATTR", "e1d", 0, "e1", 3, 400, 400, "Designator", "R1",
         0, 1, "default", 45, 6, 0, 0, 3, 0, 0, 0, 0, 0],
        ["ATTR", "e1v", 0, "e1", 3, None, None, "Device", DEV_UUID,
         0, 0, "default", 45, 6, 0, 0, 3, 0, 0, 0, 0, 0],
        ["PAD_NET", "e1", "1", "GND", "p1"],
        ["PAD_NET", "e1", "2", "VCC", "p2"],
        # LINE: id, ?, net, layer, x1, y1, x2, y2, ?, width, ?
        ["LINE", "l1", 0, "GND", 1, 0, 0, 100, 0, 19.685, 0],
        # ARC: id, ?, net, layer, x1, y1, x2, y2, ?, sweep, width, ?
        ["ARC", "a1", 0, "VCC", 1, 100, 0, 200, 100, -90, 19.685, 0],
        # VIA: id, ?, net, ?, x, y, drill, diameter, ...
        ["VIA", "v1", 0, "VCC", "", 400, 500, 12.0078, 24.0158, 0, None, None,
         0, []],
        # POUR: id, ?, net, layer, <flag 0.2|1>, name, ?, [contours], [mode, n], ..
        # (measured: 12 fields, the contours are nested one level)
        ["POUR", "p1", 0, "GND", 1, 0.2, "GND-pour", 0,
         [[50, 50, "L", 900, 50, 900, 700, 50, 700, 50, 50]], ["SOLID", 6], 0, 0],
        # POURED: id, pour id, ?, filled?, [contours]
        # coordinates are **10 mil**, not mil: measured, the fill bbox is the
        # pour bbox / 10 (see `epro.FILL_UNIT_MIL`). Same shape, one decade in.
        ["POURED", "pd1", "p1", 0, True,
         [[5, 5, "L", 90, 5, 90, 70, 5, 70, 5, 5]]]
    ] + ([["POURED", "pd2", "p1", 0, True,
           [[10, 10, "L", 40, 10, 40, 40, 10, 40, 10, 10]]]]
         if fill_records > 1 else []) + [
        # FILL: id, ?, net, layer, <flag>, ?, [["R", x, y, w, h, rot, r]], ..
        ["FILL", "f1", 0, "VCC", 1, 0.2, 0, [["R", 100, 600, 200, 100, 0, 0]], 0],
        # REGION: id, ?, layer, <flag>, keepout flags, [contours], ..
        ["REGION", "rg1", 0, 11, 0.2, [2, 7],
         [["CIRCLE", 3000, 3000, 100]], 0, None],
        ["LAYER_PHYS", 1, None, 1.379, None, None, 1],
        ["LAYER_PHYS", 101, "FR4", 60.236, 4.5, 0, 1],
    ] + ([["LAYER_PHYS", 15, None, 1.379, None, None, 1],
          ["LAYER_PHYS", 102, "FR4", 60.236, 4.5, 0, 1],
          ["LAYER_PHYS", 16, None, 1.378, None, None, 1]] if four_layers else []) + [
        ["LAYER_PHYS", 2, None, 1.378, None, None, 1],
        ["RULE", "r1", "safeClearance", True, ["mm", 6]],
        ["STRING", "s1", 0, 3, 100, 50, "REV A", "default", 78.7402,
         0, 0, 0, 0, -270, 0, 0, 0, 0, 0],
    ]
    if outline == "rect":
        # measured convention: `["R", x, y, w, h]` has `y` as the UPPER edge,
        # so 2362.2047 (60 mm) is the top of the board, not its bottom
        recs.append(["POLY", "e10", 0, "", 11, 10,
                     ["R", 0, 2362.2047, 3149.6063, 2362.2047, 0, 0], 0])
    else:
        short = 5 if outline == "open" else 0       # leave the corner 5 mil short
        recs.append(["POLY", "e10", 0, "", 11, 10,
                     [0, 0, "L", 1000, 0, 1000, 800, 0, 800, 0, short], 0])
        if outline != "open":       # an open edge has no container, so no cutout
            recs.append(["POLY", "e10b", 0, "", 11, 10,
                         [200, 200, "L", 200, 400, 400, 400, 400, 200, 200, 200], 0])
    return recs


def pcb_member_text(outline="closed", **opts):
    """One `.epcb` member: a FOOTPRINT document, then the PCB document (LF)."""
    items = [
        ["DOCTYPE", "FOOTPRINT", "1.9"],
        ["HEAD", {"editorVersion": "2.2.36.7", "uuid": PCB_UUID}],
        ["DOCTYPE", "PCB", "1.9"],
    ] + pcb_records(outline, **opts) + [[]]    # a literal `[]` line really occurs
    return stream(items, term="\n", trailing=True)


def footprint_member_text():
    """One `FOOTPRINT/<uuid>.efoo` member (CRLF, last line unterminated)."""
    pad1 = ["PAD", "ie1", 0, "", 1, "1", -50, 0, 0, None, ["RECT", 100, 60, 0],
            0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
    pad2 = ["PAD", "ie2", 0, "", 1, "2", 50, 0, 0, ["ROUND", 30, 1],
            ["OVAL", 100, 60], 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
    # pad 3 exists in the footprint and carries no PAD_NET row in the board -
    # the case an append has to handle (measured: not every pad has a row)
    pad3 = ["PAD", "ie3", 0, "", 1, "3", 0, -50, 0, None, ["RECT", 40, 40, 0],
            0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
    attr = ["ATTR", "ie9", 0, "", 3, None, None, "Footprint", "R0603",
            0, 0, "default", 45, 6, 0, 0, 3, 0, 0, 0, 0, 0]
    items = [["DOCTYPE", "FOOTPRINT", "1.9"], [], pad1, pad2, pad3,
             "\t\t" + _line(attr)]
    assert len(pad1) == 22 and len(pad2) == 22 and len(pad3) == 22
    return stream(items, term="\r\n", trailing=False)


def project_json(pcb_uuid=PCB_UUID, sch_uuid=SCH_UUID, fp_uuid=FP_UUID,
                 dev_uuid=DEV_UUID):
    return json.dumps({
        "schematics": {sch_uuid: "SCH1_1"},
        "pcbs": {pcb_uuid: "PCB1_1"},
        "panels": {},
        "symbols": {},
        "footprints": {fp_uuid: {"title": "R0603"}},
        "devices": {dev_uuid: {"title": "RES-10k"}},
        "boards": {"Board1_1": {"schematic": sch_uuid, "pcb": pcb_uuid}},
        "config": {"unit": "mm"},
    }, ensure_ascii=False)


# ------------------------------------------------------------------ archive
def epro_bytes(outline="closed", **opts):
    """A readable `.epro`: ZIP with the measured member layout."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("project.json", project_json())
        zf.writestr("PCB/", b"")
        zf.writestr("FOOTPRINT/", b"")
        zf.writestr("SHEET/", b"")
        zf.writestr("PCB/%s.epcb" % PCB_UUID, pcb_member_text(outline, **opts))
        zf.writestr("FOOTPRINT/%s.efoo" % FP_UUID, footprint_member_text())
        zf.writestr("SHEET/%s/1.esch" % SCH_UUID,
                    stream([["DOCTYPE", "SCH", "1.9"],
                            ["HEAD", {"editorVersion": "2.2.36.7"}],
                            ["WIRE", "w1", 0, 1, 0, 0, 100, 0]], term="\n",
                           trailing=True))
    return buf.getvalue()


def write_epro(directory=None, name="sample.epro", **opts):
    """Write a fixture to disk (a real path is needed: the rebuild is byte-level)."""
    directory = directory or tempfile.mkdtemp(prefix="lceda_fx_")
    path = os.path.join(directory, name)
    with open(path, "wb") as fh:
        fh.write(epro_bytes(**opts))
    return path


def mil(value):
    """mil -> mm with the reader's rounding, so tests do not restate constants."""
    return round(float(value) * MIL, 6)
