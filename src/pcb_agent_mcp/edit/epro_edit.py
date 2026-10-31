"""The LCEDA Pro write path: change the records that changed, in the member that
holds them, and prove nothing else moved.

What a real save looks like (measured, docs/13)
    Two consecutive saves of the same board, compared member by member:

        129 members byte-identical, 1 member changed
        (``PCB/<uuid>.epcb``, 609,392 -> 607,523 bytes), project.json identical

    So the vendor does **not** append revisions and does **not** add a member on a
    save - it rewrites the board document and leaves everything else alone. (The
    "new member + a new entry in ``project.json.pcbs``" case is what happens when
    the user *creates another PCB document*, which is a different action.)
    This module therefore rewrites one member and nothing else, and the gates
    below exist to prove the second half of that sentence.

Why a gate list rather than a diff
    Every failure mode here is silent. A record written with the wrong arity is
    simply ignored by the application; a number written in the wrong unit is a
    part placed at 1/25th of its intended size, and the file still opens. So an
    apply is only allowed to stand when it has been re-read from disk:

1. ``bytes_written``     the bytes on disk are the bytes that were planned
2. ``rows_untouched``    every line the plan did not name is byte-identical
3. ``members_untouched`` every other ZIP member is byte-identical
4. ``reparsed``          the adapter re-reads the written file and sees what the
                         plan promised (per operation, in millimetres)
5. ``diff_is_intended``  the set of ``(tag, id)`` records that changed on disk is
                         exactly the set the plan said it would change

Coordinates are **mil** in the file (0.0254 mm, see ``adapters/epro.py``); every
argument to an operation is **millimetres**, and the conversion happens in one
place (``_mil``).

What is refused instead of guessed
    ``.eprj`` / ``.eprj2`` (SQLite) is read-only here. In that container the board
    lives in a ``coppers``/``documents`` row next to ``projects.ticket``,
    ``projects.g_ticket``, per-document ``ticket``/``sort_ticket`` and encrypted
    ``history_data`` snapshots; a single ``UPDATE`` that does not also advance the
    ticket and write the history row is exactly the kind of change the application
    can silently treat as stale. Refusing says so; writing would look like success.
"""
import collections
import io
import json
import math
import os
import time
import uuid as _uuid
import zipfile

from ..fidelity import epro_doc as _ep
from ..fidelity import eproj_doc as _ejd
from ..fidelity import lceda
from . import plan as _plan
from .fsutil import atomic_write as _atomic_write
from .fsutil import sha256 as _sha256

EditError = _plan.EditError

PLANS = _plan.PLANS                      # one plan table for both write paths
PLAN_TTL = _plan.PLAN_TTL

SQLITE_REFUSED = (
    "this is a .eprj/.eprj2 SQLite project and this build only writes .epro "
    "containers: in SQLite the board row sits beside projects.ticket / "
    "projects.g_ticket, per-document ticket/sort_ticket and encrypted "
    "history_data snapshots, and an UPDATE that does not also advance the ticket "
    "and write the history row is exactly the change the application can silently "
    "treat as stale. Nothing was written. Export the project to .epro, or edit it "
    "in LCEDA Pro")

#: which layer code a layer name must resolve to; the LAYER records are the truth
ANY_LAYER = None

#: member suffixes that carry a record stream (the measured ``epro_doc.STREAM_DIRS``)
STREAM_EXTS = tuple(sorted(set(_ep.STREAM_DIRS.values())))

#: Archives this module wrote and then had the **application itself** re-open.
#: Keyed by container kind. An entry exists only where a run below was measured;
#: a container kind with no entry is one nobody has fed back to the app, and the
#: result then keeps saying exactly that (see ``_acceptance``).
#:
#: The judgement is not "import returned ok". Both ``extractProjectInfo`` and
#: ``importProjectByProjectFile`` return success for a zip whose PCB record
#: stream is garbage (run ``negative_control_a`` below) - only reading the
#: geometry back out of the opened document can fail, so that is the only
#: reading this table is willing to call evidence.
ACCEPTED_ARCHIVES = {
    "epro_zip": {
        "measured_on": "2026-11-01",
        "app": "LCEDA Pro V4.1.60",
        "bridge": "4.1.60.198f38ab",
        "door": ("eda.sys_FileManager.importProjectByProjectFile(File, 'JLCEDA "
                 "Pro', props, {operation:'New Project', ...}) -> "
                 "dmt_Project.openProject(uuid) -> dmt_Pcb.getAllPcbsInfo() -> "
                 "dmt_EditorControl.openDocument(pcbUuid) -> "
                 "eda.pcb_PrimitiveLine.getAll() (readings in mil)"),
        "unit": "mil",
        "run": [
            {"arm": "control",
             "what": "the app's own export, untouched by this module",
             "bytes": 108580,
             "sha256": "f339d7b09bd96a5ab6b2ab1c5255d8918f1fdaf70484471973cfbf611b9416d0",
             "import_returned": "ok (project d24397f2e42af86c97051ca5b1d936f69d8c50f276464f5a0778e60449c4f7ab)",
             "lines_read_back": [[2, 1, 21, 1, 0.25], [1, 5, 21, 5, 0.3]]},
            {"arm": "written",
             "what": "same archive after set_track id=e1 width_mm=0.5",
             "bytes": 108443,
             "sha256": "57d238c9a8fc9c94688c14bc329f0039356440e309efa26ff476452c17f444aa",
             "import_returned": "ok (project 0fe207c0793cbf1c06143fb61a96437346f27b052203bf2a746ed82caf652487)",
             "lines_read_back": [[2, 1, 21, 1, 19.685], [1, 5, 21, 5, 0.3]],
             "note": "19.685 mil = 0.499999 mm, the width the plan named; the "
                     "line the plan did not name still reads 0.3"},
            {"arm": "negative_control_a",
             "what": "valid zip, PCB record stream replaced with garbage "
                     "(['BOGUS',1,2,3] plus a broken LINE)",
             "bytes": 95966,
             "sha256": "e8ff189ad5a61784cb4b1a92e8e9f6ba1b6b205adc8d0d374a2c0ccd400f085e",
             "import_returned": "ok (project 41a715b1...) - so a successful import "
                                "proves nothing",
             "lines_read_back": [],
             "why": "this is the run that makes the geometry reading a real "
                    "criterion: it fails here while every import-level signal "
                    "still says success"},
            {"arm": "negative_control_b",
             "what": "not a zip at all (b'not a zip at all' * 100)",
             "import_returned": "undefined, no project created",
             "why": "shows the door does refuse something, so 'returns ok' is "
                    "not vacuous"},
        ],
        "limits": [
            "one application build only (V4.1.60 / bridge 4.1.60.198f38ab); a "
            "different build is a different instrument and says nothing",
            "ZIP container only: .eprj/.eprj2 (SQLite) is still refused, not "
            "covered by any run",
            "measured through the 'ImportDocument' + 'New Project' door only; "
            "the 'Existing Project' door merges into an existing project and "
            "the 'Offline Client Local Path' door needs an extension permission "
            "that was not enabled",
            "the screen was not inspected: acceptance is established by reading "
            "primitives back through the app's own API, not by looking at a "
            "window",
            "geometry read back is two LINE records of one PCB in one archive",
        ],
    },
}

def _acceptance(container):
    """What was measured about this container kind, or None.

    None is not "rejected": it is "nobody has fed an archive of this kind back
    to the application", and every caller must word it that way.
    """
    return ACCEPTED_ARCHIVES.get(container)

def _acceptance_report(container):
    """The ``app_acceptance`` block of an apply result."""
    measured = _acceptance(container)
    if measured is None:
        return {"container": container, "accepted_by_app": None,
                "measured": None,
                "note": ("no archive of this container kind has been re-opened "
                         "in LCEDA Pro by this project, so nothing is claimed")}
    written = [r for r in measured["run"] if r["arm"] == "written"]
    control = [r for r in measured["run"] if r["arm"] == "control"]
    return {
        "container": container,
        "accepted_by_app": True,
        "measured": measured,
        "read_back": written[0]["lines_read_back"] if written else None,
        "control_read_back": control[0]["lines_read_back"] if control else None,
    }

def _not_verified_text(container, verified_by_app):
    """The honest sentence about what an apply does *not* establish."""
    measured = _acceptance(container)
    if measured is not None:
        written = [r for r in measured["run"] if r["arm"] == "written"]
        control = [r for r in measured["run"] if r["arm"] == "control"]
        w = written[0]["lines_read_back"] if written else []
        c = control[0]["lines_read_back"] if control else []
        return ("an archive written by this module (sha256 %s) was imported into "
                "%s (bridge %s) on %s and the application read the geometry back "
                "as %s where the untouched control archive (sha256 %s) reads %s - "
                "so 'the application accepts a written archive' is claimed for "
                "this container on that one build. NOT established: any other "
                "application build, any .eprj/.eprj2 (SQLite) container, any door "
                "other than 'ImportDocument' into a new project, that a human "
                "looking at the window would agree, or that this particular "
                "write is accepted (a write that appends a record the vendor was "
                "never seen writing is still reported as verified_by_app=%s)"
                % (written[0]["sha256"][:16] + "...", measured["app"],
                   measured["bridge"], measured["measured_on"], w,
                   control[0]["sha256"][:16] + "..." if control else "?",
                   c, "true" if verified_by_app else "false"))
    return ("this archive was never opened in LCEDA Pro after the "
            "write, so 'the application accepts it' is not claimed"
            if verified_by_app else
            "a record was appended that the vendor was not observed "
            "writing there: the application may ignore it")


# --------------------------------------------------------------- small helpers
def _num(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _mil(mm_value):
    """mm -> the number to store. Integral values stay ints, like the vendor."""
    v = lceda.from_mm(float(mm_value))
    r = round(v, 4)                       # files carry 4 decimals (0.0073, 3935.6691)
    if abs(r - round(r)) < 1e-9:
        return int(round(r))
    return r


def _mm(value):
    return lceda.to_mm(_num(value))


def _stream_member(container, folder, ident, ext):
    """The member that holds one document, by ``folder/id+ext`` (measured naming)."""
    want = ("%s/%s%s" % (folder, ident, ext)).lower()
    for name in container.names():
        if name.lower() == want:
            return name
    return None


# --------------------------------------------------------------- containers
def _open_container(path):
    with open(path, "rb") as fh:
        head = fh.read(16)
    if head[:2] == b"PK":
        return _ep.EproDocument.from_path(path), "epro_zip"
    if _ejd.looks_like_eproj(path):
        return _ejd.EprjDocument.from_path(path), "eprj_sqlite"
    raise EditError("neither a ZIP container (.epro) nor a SQLite project "
                    "(.eprj/.eprj2): %r" % path, "FORMAT_NOT_SUPPORTED")


def _pick_member(container, requested=None):
    """The member holding the PCB document. Chosen by document, not by suffix."""
    names = container.names()
    if requested:
        if requested not in names:
            raise EditError("member %r is not in the archive; it has %d members, "
                            "among them: %s" % (requested, len(names),
                                               ", ".join(names[:8])),
                            "MEMBER_NOT_FOUND", members=names[:40])
        return requested
    candidates = []
    for name in names:
        if not name.lower().endswith(STREAM_EXTS):
            continue
        try:
            member = lceda.Member(name, container.entry_bytes(name).decode("utf-8"))
        except (lceda.LcedaError, UnicodeDecodeError):
            continue
        if any(d.kind == "PCB" for d in member.documents):
            candidates.append(name)
    if not candidates:
        raise EditError("no member in this archive holds a PCB document (members "
                        "with a record stream: %s)"
                        % ", ".join(n for n in names
                                   if n.lower().endswith(STREAM_EXTS)),
                        "NO_PCB_MEMBER")
    if len(candidates) > 1:
        raise EditError("this archive holds %d PCB documents (%s); name the one to "
                        "edit with member=..."
                        % (len(candidates), ", ".join(candidates)),
                        "MEMBER_AMBIGUOUS", members=candidates)
    return candidates[0]


# --------------------------------------------------------------- lookups
def _pcb_document(member):
    for doc in member.documents:
        if doc.kind == "PCB":
            return doc
    raise EditError("%s holds no PCB document (it has: %s)"
                    % (member.name, ", ".join(d.kind for d in member.documents)),
                    "NO_PCB_MEMBER")


def _record(doc, tag, ident):
    hits = [ln for ln in doc.by_tag(tag) if str(ln.record[1]) == str(ident)]
    if not hits:
        raise EditError("no %s record with id %r in this board (a change to "
                        "something that is not there is not a change)"
                        % (tag, ident), "RECORD_NOT_FOUND", tag=tag, id=ident,
                        available=[ln.record[1] for ln in doc.by_tag(tag)][:20])
    if len(hits) > 1:
        raise EditError("%d %s records share the id %r; the file is not what this "
                        "writer was measured against, refusing to pick one"
                        % (len(hits), tag, ident), "RECORD_AMBIGUOUS",
                        tag=tag, id=ident)
    return hits[0]


# --------------------------------------------------------------- operations
OPS = {}


def _change(tag, ident):
    return [{"tag": str(tag), "id": str(ident), "action": "change"}]


def _register(name, params, summary):
    def deco(fn):
        OPS[name] = {"op": name, "params": params, "summary": summary, "fn": fn}
        return fn
    return deco


def _net_name(ctx, net):
    if net is None:
        return None
    name = str(net)
    if name not in ctx["nets"]:
        raise EditError("net %r is not in this board's NET table (it has %d nets, "
                        "among them: %s) - naming a net that does not exist would "
                        "create a second spelling of the same net"
                        % (name, len(ctx["nets"]), ", ".join(sorted(ctx["nets"])[:8])),
                        "NET_NOT_FOUND", net=name, nets=sorted(ctx["nets"]))
    return name


def _layer_name(ctx, layer):
    if layer is None:
        return None
    want = str(layer).upper()
    if want not in ctx["layer_by_name"]:
        raise EditError("layer %r is not one of this board's LAYER names (%s)"
                        % (layer, ", ".join(sorted(ctx["layer_by_name"]))),
                        "LAYER_NOT_FOUND", layer=layer)
    return want


@_register("move_component",
           {"component": "<COMPONENT id>", "x_mm": 0.0, "y_mm": 0.0},
           "move a placed component to an absolute position. The component's own "
           "ATTR children that sit on the component's anchor are moved with it; "
           "rotation is not changed")
def _op_move_component(ctx, spec):
    line = _record(ctx["doc"], "COMPONENT", spec.get("component"))
    rec = list(line.record)
    old_x, old_y = _num(rec[4]), _num(rec[5])
    new_x = _mil(spec["x_mm"])
    new_y = _mil(spec["y_mm"])
    rec[4], rec[5] = new_x, new_y
    dx, dy = new_x - old_x, new_y - old_y
    moved = []
    for attr in ctx["doc"].by_tag("ATTR"):
        a = attr.record
        if str(a[3]) != str(rec[1]):
            continue
        ax, ay = _num(a[5]), _num(a[6])
        if ax is None or ay is None:
            continue
        if abs(ax - old_x) < 1e-6 and abs(ay - old_y) < 1e-6:
            ar = list(a)
            ar[5], ar[6] = ax + dx, ay + dy
            ctx["member"].set_record(attr, ar)
            moved.append({"attr": a[1], "key": a[7]})
    ctx["member"].set_record(line, rec)
    return {"op": "move_component", "tag": "COMPONENT", "id": rec[1],
            "before": {"x_mm": _mm(old_x), "y_mm": _mm(old_y)},
            "after": {"x_mm": _mm(new_x), "y_mm": _mm(new_y)},
            "touches": _change("COMPONENT", rec[1])
                       + sum((_change("ATTR", m["attr"]) for m in moved), []),
            "attrs_moved": moved,
            "warning": ("the component's ATTR children that did not sit on the "
                        "anchor were left where they are; the editor may re-flow "
                        "them on the next save" if moved else None)}


@_register("set_track",
           {"id": "<LINE or ARC id>", "x1_mm": None, "y1_mm": None,
            "x2_mm": None, "y2_mm": None, "width_mm": None, "net": None},
           "move/resize one track segment (LINE or ARC) and/or change its width "
           "and net")
def _op_set_track(ctx, spec):
    doc = ctx["doc"]
    hits = [ln for ln in doc.by_tag("LINE") + doc.by_tag("ARC")
            if str(ln.record[1]) == str(spec.get("id"))]
    if not hits:
        raise EditError("no LINE or ARC record with id %r" % (spec.get("id"),),
                        "RECORD_NOT_FOUND", id=spec.get("id"))
    if len(hits) > 1:
        raise EditError("%d records share the id %r" % (len(hits), spec.get("id")),
                        "RECORD_AMBIGUOUS")
    line = hits[0]
    rec = list(line.record)
    before = {"start_mm": [_mm(rec[5]), _mm(rec[6])],
              "end_mm": [_mm(rec[7]), _mm(rec[8])],
              "width_mm": _mm(rec[9] if rec[0] == "LINE" else rec[10]),
              "net": rec[3] or None}
    for i, key in ((5, "x1_mm"), (6, "y1_mm"), (7, "x2_mm"), (8, "y2_mm")):
        if spec.get(key) is not None:
            rec[i] = _mil(spec[key])
    if spec.get("width_mm") is not None:
        rec[9 if rec[0] == "LINE" else 10] = _mil(spec["width_mm"])
    if spec.get("net") is not None:
        rec[3] = _net_name(ctx, spec["net"]) or ""
    ctx["member"].set_record(line, rec)
    return {"op": "set_track", "tag": rec[0], "id": rec[1], "before": before,
            "touches": _change(rec[0], rec[1]),
            "after": {"start_mm": [_mm(rec[5]), _mm(rec[6])],
                      "end_mm": [_mm(rec[7]), _mm(rec[8])],
                      "width_mm": _mm(rec[9] if rec[0] == "LINE" else rec[10]),
                      "net": rec[3] or None}}


@_register("set_via",
           {"id": "<VIA id>", "net": None, "x_mm": None, "y_mm": None,
            "drill_mm": None, "diameter_mm": None},
           "change a via's net, position, drill or pad diameter. The drill must "
           "stay smaller than the pad")
def _op_set_via(ctx, spec):
    line = _record(ctx["doc"], "VIA", spec.get("id"))
    rec = list(line.record)
    before = {"net": rec[3] or None, "x_mm": _mm(rec[5]), "y_mm": _mm(rec[6]),
              "drill_mm": _mm(rec[7]), "diameter_mm": _mm(rec[8])}
    if spec.get("net") is not None:
        rec[3] = _net_name(ctx, spec["net"]) or ""
    if spec.get("x_mm") is not None:
        rec[5] = _mil(spec["x_mm"])
    if spec.get("y_mm") is not None:
        rec[6] = _mil(spec["y_mm"])
    if spec.get("drill_mm") is not None:
        rec[7] = _mil(spec["drill_mm"])
    if spec.get("diameter_mm") is not None:
        rec[8] = _mil(spec["diameter_mm"])
    if _num(rec[7]) is None or _num(rec[8]) is None or float(rec[7]) >= float(rec[8]):
        raise EditError("a via whose drill (%s) is not smaller than its pad (%s) is "
                        "physically impossible; the file keeps that pair 1:2"
                        % (rec[7], rec[8]), "ARG_INVALID")
    ctx["member"].set_record(line, rec)
    return {"op": "set_via", "tag": "VIA", "id": rec[1], "before": before,
            "touches": _change("VIA", rec[1]),
            "after": {"net": rec[3] or None, "x_mm": _mm(rec[5]),
                      "y_mm": _mm(rec[6]), "drill_mm": _mm(rec[7]),
                      "diameter_mm": _mm(rec[8])}}


@_register("set_text",
           {"id": "<STRING id>", "text": None, "x_mm": None, "y_mm": None,
            "layer": None},
           "change a board text: its content, position or layer")
def _op_set_text(ctx, spec):
    line = _record(ctx["doc"], "STRING", spec.get("id"))
    rec = list(line.record)
    before = {"text": rec[6], "x_mm": _mm(rec[4]), "y_mm": _mm(rec[5]),
              "layer": ctx["layer_by_code"].get(rec[3])}
    if spec.get("text") is not None:
        rec[6] = str(spec["text"])
    if spec.get("x_mm") is not None:
        rec[4] = _mil(spec["x_mm"])
    if spec.get("y_mm") is not None:
        rec[5] = _mil(spec["y_mm"])
    if spec.get("layer") is not None:
        rec[3] = ctx["layer_by_name"][_layer_name(ctx, spec["layer"])]
    ctx["member"].set_record(line, rec)
    return {"op": "set_text", "tag": "STRING", "id": rec[1], "before": before,
            "touches": _change("STRING", rec[1]),
            "after": {"text": rec[6], "x_mm": _mm(rec[4]), "y_mm": _mm(rec[5]),
                      "layer": ctx["layer_by_code"].get(rec[3])}}


@_register("set_pad_net",
           {"component": "<COMPONENT id>", "pad": "<pad number>", "net": "<net>",
            "allow_new_record": False},
           "assign a net to one pad of a placed component (the PAD_NET record). "
           "If the pad has no PAD_NET record yet, one is appended only when "
           "allow_new_record=true")
def _op_set_pad_net(ctx, spec):
    comp = str(spec.get("component"))
    pad = str(spec.get("pad"))
    net = _net_name(ctx, spec.get("net"))
    _record(ctx["doc"], "COMPONENT", comp)
    hits = [ln for ln in ctx["doc"].by_tag("PAD_NET")
            if str(ln.record[1]) == comp and str(ln.record[2]) == pad]
    if len(hits) > 1:
        raise EditError("%d PAD_NET records for pad %s of component %r"
                        % (len(hits), pad, comp), "RECORD_AMBIGUOUS")
    if hits:
        line = hits[0]
        rec = list(line.record)
        before = rec[3] or None
        rec[3] = net or ""
        ctx["member"].set_record(line, rec)
        return {"op": "set_pad_net", "tag": "PAD_NET", "id": rec[1],
                "before": {"pad": pad, "net": before},
                "after": {"pad": pad, "net": net}, "appended": False,
                "touches": _change("PAD_NET", comp)}
    if not spec.get("allow_new_record"):
        raise EditError("component %r has no PAD_NET record for pad %s, and this "
                        "build will not invent a record shape it has never seen "
                        "written for that pad. Pass allow_new_record=true if you "
                        "accept appending one (it is then reported as "
                        "verified_by_app=false)" % (comp, pad), "RECORD_NOT_FOUND",
                        component=comp, pad=pad)
    # The fifth field is a pad *object* id inside the footprint document, and a
    # pad number the footprint holds more than once (a through-hole pad is
    # stored top+bottom) gets one row per object. Measured over the 194
    # archives: 8159/8159 PAD_NET rows point at an object that carries that pad
    # number, but only 8149 point at the first one - every object gets a row.
    object_ids = _pad_object_ids(ctx, comp, pad)
    rows = []
    for pad_id in object_ids:
        rec = ["PAD_NET", comp, pad, net or "", pad_id]
        ctx["member"].append_record(rec)
        rows.append({"pad_object": pad_id, "net": rec[3]})
    return {"op": "set_pad_net", "tag": "PAD_NET", "id": comp,
            "before": {"pad": pad, "net": None},
            "after": {"pad": pad, "net": net}, "appended": True,
            "touches": [{"tag": "PAD_NET", "id": comp, "action": "append"}],
            "appended_rows": rows, "verified_by_app": False}


@_register("delete_record",
           {"tag": "<record tag>", "id": "<record id>", "allow_delete": False,
            "cascade": False},
           "delete one record line. Needs allow_delete=true; for a COMPONENT the "
           "caller must also pass cascade=true, which removes that component's "
           "ATTR and PAD_NET children in the same plan")
def _op_delete_record(ctx, spec):
    if not spec.get("allow_delete"):
        raise EditError("deleting geometry is not reversible with Ctrl+Z in every "
                        "editor version: pass allow_delete=true to mean it",
                        "CONFIRMATION_REQUIRED")
    tag = spec.get("tag")
    line = _record(ctx["doc"], tag, spec.get("id"))
    rec = line.record
    children = []
    if str(tag).upper() == "COMPONENT":
        children = [ln for ln in ctx["doc"].by_tag("ATTR")
                    if str(ln.record[3]) == str(rec[1])]
        children += [ln for ln in ctx["doc"].by_tag("PAD_NET")
                     if str(ln.record[1]) == str(rec[1])]
        if children and not spec.get("cascade"):
            raise EditError("component %r still owns %d ATTR/PAD_NET records; "
                            "deleting only the COMPONENT would leave them behind. "
                            "Pass cascade=true to remove them in the same plan"
                            % (rec[1], len(children)), "CASCADE_REQUIRED",
                            children=len(children))
    for ln in children:
        ctx["member"].remove_line(ln)
    ctx["member"].remove_line(line)
    touches = [{"tag": str(tag), "id": str(rec[1]), "action": "delete"}]
    for ln in children:
        touches.append({"tag": str(ln.record[0]), "id": str(ln.record[1]),
                        "action": "delete"})
    return {"op": "delete_record", "tag": tag, "id": rec[1],
            "deleted_children": len(children), "touches": touches,
            "before": {"record": rec[:8]}}


def describe():
    return [{"op": name, "params": OPS[name]["params"],
             "summary": OPS[name]["summary"]} for name in sorted(OPS)]


# --------------------------------------------------------------- pad objects
def _footprint_uuid(doc, component_id):
    for ln in doc.by_tag("ATTR"):
        rec = ln.record
        if (len(rec) > 8 and str(rec[3]) == str(component_id)
                and str(rec[7]) == "Footprint"):
            return str(rec[8])
    return None


def _pad_object_ids(ctx, component_id, pad):
    """Pad object ids inside the component's footprint document, by pad number.

    Read, not guessed: the footprint document is looked up by the uuid the
    component's own ``ATTR Footprint`` names, and its pad objects are the
    ``PAD`` records that carry that number. If either is missing this refuses
    instead of writing a row that points at nothing.
    """
    fp_uuid = _footprint_uuid(ctx["doc"], component_id)
    ext = _ep.STREAM_DIRS["FOOTPRINT"]
    if not fp_uuid:
        raise EditError("component %r carries no ATTR Footprint, so no footprint "
                        "document names its pad objects; refusing to append a "
                        "PAD_NET row that would point at nothing"
                        % (component_id,), "PAD_OBJECT_UNKNOWN",
                        component=component_id)
    name = _stream_member(ctx["container"], "FOOTPRINT", fp_uuid, ext)
    if name is None:
        raise EditError("component %r names footprint %r but this archive holds no "
                        "FOOTPRINT/%s member, so its pad objects cannot be read; "
                        "refusing to append a PAD_NET row"
                        % (component_id, fp_uuid, fp_uuid + ext),
                        "PAD_OBJECT_UNKNOWN", component=component_id,
                        footprint=fp_uuid)
    member = lceda.Member(name, ctx["container"].entry_bytes(name).decode("utf-8"))
    ids = []
    for doc in member.documents:
        for ln in doc.by_tag("PAD"):
            rec = ln.record
            if len(rec) > 5 and str(rec[5]) == str(pad):
                ids.append(str(rec[1]))
    if not ids:
        raise EditError("footprint %r (component %r) has no PAD object numbered "
                        "%r, so there is no pad to name"
                        % (fp_uuid, component_id, pad), "PAD_NOT_FOUND",
                        component=component_id, pad=pad, footprint=fp_uuid)
    return ids


# --------------------------------------------------------------- propose
def _context(container, member, doc):
    """The tables an operation is allowed to talk in: real nets, real layers.

    Both layer directions come from the ``LAYER`` records in *this* document
    (``["LAYER", 1, "TOP", "Top Layer", ...]``); no code is invented here, so a
    name that is not in the file is refused instead of resolved to a guess.
    """
    nets = {}
    for ln in doc.by_tag("NET"):
        rec = ln.record
        if len(rec) > 1 and str(rec[1]):
            nets[str(rec[1])] = str(rec[1])
    layer_by_code, layer_by_name = {}, {}
    for ln in doc.by_tag("LAYER"):
        rec = ln.record
        if len(rec) > 2 and isinstance(rec[1], int):
            layer_by_code[rec[1]] = str(rec[2])
            layer_by_name[str(rec[2]).upper()] = rec[1]
    return {"container": container, "member": member, "doc": doc, "nets": nets,
            "layer_by_code": layer_by_code, "layer_by_name": layer_by_name}


def propose(path, operations, options=None):
    """Compute the archive that *would* be written. Nothing touches the disk."""
    options = options or {}
    if not path:
        raise EditError("path is required", "ARG_MISSING")
    if not os.path.isfile(path):
        raise EditError("archive does not exist: %r" % path, "FILE_NOT_FOUND")
    if not operations:
        raise EditError("an empty plan is not a plan: pass at least one operation",
                        "ARG_MISSING")

    container, container_kind = _open_container(path)
    if container_kind != "epro_zip":
        raise EditError(SQLITE_REFUSED, "WRITE_UNSUPPORTED_FORMAT",
                        container=container_kind)

    member_name = _pick_member(container, options.get("member"))
    old_text = container.entry_bytes(member_name).decode("utf-8")
    member = lceda.Member(member_name, old_text)
    doc = _pcb_document(member)
    ctx = _context(container, member, doc)
    before_lines = [ln.to_text() for ln in member.lines]

    records = []
    for idx, spec in enumerate(operations):
        if not isinstance(spec, dict):
            raise EditError("operation %d is not an object" % idx, "ARG_INVALID")
        name = spec.get("op")
        entry = OPS.get(name)
        if entry is None:
            raise EditError("unknown .epro operation %r; known: %s"
                            % (name, ", ".join(sorted(OPS))), "OP_UNKNOWN",
                            known=sorted(OPS))
        try:
            result = entry["fn"](ctx, spec)
        except EditError as exc:
            raise EditError("operation %d (%s): %s" % (idx, name, exc), exc.code,
                            **exc.extra)
        records.append(dict(result, index=idx))

    new_text = member.to_text()
    new_lines = [ln.to_text() for ln in member.lines]
    planned = _planned_sets(records)
    counts = _planned_counts(records)

    other_members = collections.OrderedDict()
    for name in container.names():
        if name != member_name:
            other_members[name] = _sha256(container.entry_bytes(name))

    new_bytes = container.to_bytes({member_name: new_text})
    reread = _reread(new_bytes, member_name, other_members, before_lines,
                     planned)
    blocking = [{"gate": k, "message": v} for k, v in reread.items()
                if v.get("ok") is False]

    warnings = []
    for rec in records:
        if rec.get("warning"):
            warnings.append({"op": rec["op"], "message": rec["warning"]})
        if rec.get("verified_by_app") is False:
            warnings.append({"op": rec["op"],
                             "message": "a record was appended that the vendor was "
                                        "never observed writing in this position: "
                                        "verified_by_app=false"})

    plan_id = "plan-" + _uuid.uuid4().hex[:12]
    with open(path, "rb") as fh:
        sha_before = _sha256(fh.read())
    plan = {
        "plan_id": plan_id, "format": "epro", "path": os.path.abspath(path),
        "created": time.time(), "expires": time.time() + PLAN_TTL,
        "sha256_before": sha_before,
        "container": container_kind, "member": member_name,
        "new_bytes": new_bytes,
        "new_member_sha256": _sha256(new_text.encode("utf-8")),
        "operations": records, "records": records,
        "before_lines": before_lines, "planned": planned,
        "planned_counts": counts,
        "other_members": other_members,
        "affected": sorted({r["op"] for r in records}),
        "options": {"mode": options.get("mode") or "in_place",
                    "out_path": options.get("out_path")},
        "verified_by_app": all(r.get("verified_by_app") is not False
                               for r in records),
    }
    PLANS[plan_id] = plan
    _plan._trim_plans()

    return {
        "status": "ok" if not blocking else "blocked",
        "plan_id": plan_id,
        "path": plan["path"],
        "container": container_kind,
        "member": member_name,
        "nothing_written": "a proposal only computes; the archive on disk is untouched",
        "unit": "arguments are millimetres; the file stores mil (0.0254 mm)",
        "operations": records,
        "lines_changed": counts["change"],
        "lines_added": counts["append"],
        "lines_deleted": counts["delete"],
        "members_untouched": len(other_members),
        "self_check": reread,
        "blocking": blocking,
        "warnings": warnings,
        "verified_by_app": plan["verified_by_app"],
        "app_acceptance": _acceptance_report(container_kind),
        "expires_in_seconds": PLAN_TTL,
        "next": ("edit_apply(plan_id=%r, confirm=true) - that call takes a "
                 "checkpoint, writes atomically, then re-reads the file from disk "
                 "before the change is allowed to stand" % plan_id),
    }


def _reread(blob, member_name, other_members, before_lines, planned):
    """Judge the archive that would be written, from its own bytes."""
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            names = zf.namelist()
            same = all(_sha256(zf.read(n)) == digest for n, digest in
                       other_members.items() if n in names)
            missing = [n for n in other_members if n not in names]
            new_text = zf.read(member_name).decode("utf-8")
    except Exception as exc:                                  # noqa: BLE001
        return {"archive_readable": {"ok": False, "message": "%s: %s"
                                     % (type(exc).__name__, exc)}}
    out = {"archive_readable": {"ok": True, "members": len(other_members) + 1},
           "members_untouched": {
               "ok": same and not missing,
               "note": "%d other member(s) keep their exact bytes"
                       % len(other_members), "missing": missing}}
    try:
        member = lceda.Member(member_name, new_text)
    except lceda.LcedaError as exc:
        out["stream_parses"] = {"ok": False, "message": str(exc)}
        return out
    out["stream_parses"] = {"ok": member.is_verbatim(),
                            "note": "the rewritten member still round-trips"}
    out.update(_line_gates(before_lines,
                           [ln.to_text() for ln in member.lines], planned))
    return out


# --------------------------------------------------------------- apply
def _planned_sets(records):
    """What the operations said they would touch, as {action: {(tag, id)}}.

    A set, because two rows can share a (tag, id) - two ``PAD_NET`` rows of one
    component are both "PAD_NET e1" - and the gate is about identity, not count.
    ``_planned_counts`` keeps the line counts, which are not deduplicated.
    """
    out = {"change": set(), "append": set(), "delete": set()}
    for rec in records:
        for touch in rec.get("touches") or []:
            out[touch["action"]].add((str(touch["tag"]), str(touch["id"])))
    return out

def _planned_counts(records):
    counts = {"change": 0, "append": 0, "delete": 0}
    for rec in records:
        for touch in rec.get("touches") or []:
            counts[touch["action"]] += 1
    return counts

def _line_key(text):
    try:
        rec = json.loads(text.strip())
    except ValueError:
        return None
    if isinstance(rec, list) and len(rec) > 1:
        return (str(rec[0]), str(rec[1]))
    return None

def _line_diff(before_lines, after_lines):
    """-> (removed, added, order_kept), as multisets of line texts.

    Comparing index by index would be wrong the moment a record is deleted:
    every line after it shifts and would look "changed" while keeping its exact
    bytes. So the comparison is by content, and the surviving lines are checked
    to still be in the same order.
    """
    before = collections.Counter(before_lines)
    after = collections.Counter(after_lines)
    removed = list((before - after).elements())
    added = list((after - before).elements())

    def strip(seq, drop):
        pool = collections.Counter(drop)
        kept = []
        for item in seq:
            if pool[item]:
                pool[item] -= 1
            else:
                kept.append(item)
        return kept

    return removed, added, strip(after, added) == strip(before, removed)

def _line_gates(before_lines, after_lines, planned):
    """The two claims a diff can make: nothing else moved, and this is the move."""
    removed, added, order_kept = _line_diff(before_lines, after_lines)
    unplanned = []
    for text, action, allowed in (
            [(t, "removed", planned["change"] | planned["delete"]) for t in removed]
            + [(t, "added", planned["change"] | planned["append"]) for t in added]):
        key = _line_key(text)
        if key is None or key not in allowed:
            unplanned.append({"action": action, "key": list(key) if key else None,
                              "line": text[:120]})
    return {
        "rows_untouched": {
            "ok": not unplanned and order_kept,
            "removed": len(removed), "added": len(added),
            "unplanned": unplanned,
            "note": ("every line whose bytes changed is a line the plan named, "
                     "and every other line kept its bytes in its place")},
        "diff_is_intended": {
            "ok": not unplanned,
            "planned": {k: sorted(v) for k, v in planned.items()},
            "unplanned": unplanned,
            "note": ("a record changed on disk that the plan did not name would "
                     "be silent corruption")},
    }


def apply_plan(plan_id, confirm=False, options=None):
    """Write the plan. This is the only function in this module that writes."""
    options = options or {}
    plan = PLANS.get(plan_id)
    if plan is None or plan.get("format") != "epro":
        raise EditError("unknown or already-used plan %r (propose again)"
                        % plan_id, "PLAN_NOT_FOUND")
    if not confirm:
        raise EditError("writing a project archive is not reversible by Ctrl+Z: call "
                        "again with confirm=true. Plan %r changes %d line(s) of %s"
                        % (plan_id, plan["planned_counts"]["change"],
                           plan["member"]),
                        "CONFIRMATION_REQUIRED", plan_id=plan_id)
    if time.time() > plan["expires"]:
        PLANS.pop(plan_id, None)
        raise EditError("plan %r expired" % plan_id, "PLAN_EXPIRED")

    path = plan["path"]
    mode = options.get("mode") or plan["options"].get("mode") or "in_place"
    out_path = options.get("out_path") or plan["options"].get("out_path")
    if mode == "new_file" and not out_path:
        raise EditError("mode=new_file needs out_path", "ARG_MISSING")

    with open(path, "rb") as fh:
        current = fh.read()
    if _sha256(current) != plan["sha256_before"]:
        raise EditError("the archive changed on disk after the plan was made (the "
                        "editor saved it?). Nothing was written - propose again",
                        "BOARD_CHANGED_SINCE_PLAN")

    target = path if mode == "in_place" else os.path.abspath(out_path)
    if mode == "new_file" and os.path.exists(target):
        raise EditError("refusing to overwrite an existing file: %s" % target,
                        "TARGET_EXISTS")

    checkpoint = _plan.save_checkpoint(
        path, current, "before apply %s (%s)" % (plan_id, ", ".join(plan["affected"])),
        plan_id=plan_id)

    gates = {}
    written = False
    try:
        _atomic_write(target, plan["new_bytes"])
        written = True

        with open(target, "rb") as fh:
            back = fh.read()
        gates["bytes_written"] = {
            "ok": back == plan["new_bytes"], "sha256": _sha256(back),
            "expected_sha256": _sha256(plan["new_bytes"]), "bytes": len(back)}

        with zipfile.ZipFile(target) as zf:
            names = zf.namelist()
            same = True
            for name, digest in plan["other_members"].items():
                if name not in names or _sha256(zf.read(name)) != digest:
                    same = False
                    break
            new_text = zf.read(plan["member"]).decode("utf-8")
        gates["members_untouched"] = {
            "ok": same, "count": len(plan["other_members"]),
            "note": "every member except %s is byte-identical to before"
                    % plan["member"]}

        member = lceda.Member(plan["member"], new_text)
        gates["stream_parses"] = {"ok": member.is_verbatim()}
        gates.update(_line_gates(plan["before_lines"],
                                 [ln.to_text() for ln in member.lines],
                                 plan["planned"]))

        post = _postconditions(target, plan, plan["member"])
        gates["reparsed"] = post

        blocking = [k for k, v in gates.items()
                    if isinstance(v, dict) and v.get("ok") is False]
        if blocking:
            raise EditError("apply blocked and rolled back: %s" % ", ".join(blocking),
                            "GATE_BLOCKED", gates=gates, blocking=blocking)

        PLANS.pop(plan_id, None)
        return {
            "status": "ok",
            "plan_id": plan_id,
            "written": target,
            "mode": mode,
            "container": plan["container"],
            "member": plan["member"],
            "sha256_after": gates["bytes_written"]["sha256"],
            "checkpoint": checkpoint,
            "gates": gates,
            "gates_passed": sorted(k for k, v in gates.items()
                                   if isinstance(v, dict) and v.get("ok") is True),
            "verified_by_app": plan["verified_by_app"],
            "app_acceptance": _acceptance_report(plan["container"]),
            "decision": ("every gate passed: the bytes on disk are the planned "
                         "bytes, every other member and every unnamed line kept "
                         "its bytes, and the adapter re-read the written file and "
                         "saw the promised geometry"),
            "not_verified": _not_verified_text(plan["container"],
                                               plan["verified_by_app"]),
            "how_to_undo": "board_restore(checkpoint_id=%r, confirm=true)"
                           % checkpoint["checkpoint_id"],
        }
    except EditError:
        if written:
            _atomic_write(target, current)
        raise
    except Exception as exc:                                  # noqa: BLE001
        if written:
            _atomic_write(target, current)
        raise EditError("%s: %s (the checkpoint was restored to disk)"
                        % (type(exc).__name__, exc), "APPLY_FAILED")


def _postconditions(target, plan, member_name):
    """Re-read the written archive and check each operation's promise."""
    from ..adapters import epro as adapter          # local: avoids an import cycle
    board = adapter.parse_board(target, member=plan["member"])
    checks = []

    def close(a, b, tol=0.001):
        return a is not None and b is not None and abs(float(a) - float(b)) <= tol

    pads = {}
    for fp in board.footprints:
        for pad in fp.get("pads") or []:
            pads[(fp.get("ref"), str(pad.get("number")))] = pad

    for rec in plan["operations"]:
        ok = None
        detail = {}
        if rec["op"] == "move_component":
            ref = _designator(target, member_name, rec["id"])
            hit = [fp for fp in board.footprints if ref and fp.get("ref") == ref]
            if hit:
                ok = (close(hit[0]["x"], rec["after"]["x_mm"]) and
                      close(hit[0]["y"], rec["after"]["y_mm"]))
                detail = {"x_mm": hit[0]["x"], "y_mm": hit[0]["y"]}
        elif rec["op"] == "set_track":
            hit = [t for t in board.tracks if str(t.get("id")) == str(rec["id"])]
            if hit:
                ok = close(hit[0]["width"], rec["after"]["width_mm"])
                detail = {"width_mm": hit[0]["width"]}
        elif rec["op"] == "set_via":
            hit = [v for v in board.vias if str(v.get("id")) == str(rec["id"])]
            if hit:
                ok = close(hit[0]["drill"], rec["after"]["drill_mm"])
                detail = {"drill_mm": hit[0]["drill"]}
        elif rec["op"] == "set_text":
            hit = [t for t in board.source.get("texts") or []
                   if str(t.get("id")) == str(rec["id"])]
            if hit:
                ok = (str(hit[0].get("text")) == str(rec["after"]["text"])
                      and close(hit[0]["x"], rec["after"]["x_mm"])
                      and close(hit[0]["y"], rec["after"]["y_mm"]))
                detail = {"text": hit[0].get("text"),
                          "x_mm": hit[0]["x"], "y_mm": hit[0]["y"],
                          "layer": hit[0].get("layer")}
        elif rec["op"] == "set_pad_net":
            pad = pads.get((None, str(rec["after"]["pad"])))
            if pad is None:
                for (ref, number), candidate in pads.items():
                    if number == str(rec["after"]["pad"]):
                        pad = candidate
                        break
            if pad is not None:
                ok = pad.get("net") == rec["after"]["net"]
                detail = {"net": pad.get("net")}
        elif rec["op"] == "delete_record":
            still = _record_present(target, member_name, rec["tag"], rec["id"])
            ok = still is False
            detail = {"tag": rec["tag"], "id": rec["id"], "still_present": still}
        if ok is None:
            checks.append({"op": rec["op"], "id": rec["id"], "verified": False,
                           "detail": "the written file does not expose this record "
                                     "to the adapter, so the promise cannot be "
                                     "re-read (reported, not assumed)"})
            continue
        checks.append({"op": rec["op"], "id": rec["id"], "verified": bool(ok),
                       "detail": detail})
    verified = [c for c in checks if c["verified"]]
    return {"ok": len(verified) == len(checks),
            "all_verified": len(verified) == len(checks),
            "operations": checks,
            "note": "each promise re-read from the archive that is now on disk"}


def _record_present(path, member_name, tag, ident):
    """Is that (tag, id) still in the written file? ``None`` if unreadable."""
    try:
        container, kind = _open_container(path)
        if kind != "epro_zip":
            return None
        member = lceda.Member(member_name,
                             container.entry_bytes(member_name).decode("utf-8"))
        doc = _pcb_document(member)
    except (EditError, lceda.LcedaError, UnicodeDecodeError, KeyError):
        return None
    return any(str(ln.record[1]) == str(ident) for ln in doc.by_tag(tag))


def _designator(path, member_name, component_id):
    """The reference of a COMPONENT id, read back out of a written file.

    A component record carries no name; the only path to it is the
    ``ATTR`` of that component whose key is ``Designator`` (measured layout:
    ``[3] owner id [7] key [8] value``).
    """
    try:
        container, kind = _open_container(path)
        if kind != "epro_zip":
            return None
        member = lceda.Member(member_name,
                             container.entry_bytes(member_name).decode("utf-8"))
        doc = _pcb_document(member)
    except (EditError, lceda.LcedaError, UnicodeDecodeError, KeyError):
        return None
    for ln in doc.by_tag("ATTR"):
        rec = ln.record
        if (len(rec) > 8 and str(rec[3]) == str(component_id)
                and str(rec[7]) == "Designator"):
            return str(rec[8])
    return None
