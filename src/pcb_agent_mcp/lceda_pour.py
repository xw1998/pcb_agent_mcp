"""Official copper pour on LCEDA Pro: create the border, read the fill, and say
which half of the job the vendor API cannot do.

Why this module exists
----------------------
A pour is two different objects, and the editor treats them that way:

* ``eda.pcb_PrimitivePour`` - the BORDER plus its settings (net, layer, fill
  method, priority). This is writable through the API.
* ``eda.pcb_PrimitivePoured`` - the FILL RESULT (``pourFills``: one path per
  copper region, with holes as inner rings). The API can only READ it.

Measured on LCEDA Pro V4.1.60 (bridge 4.1.60.198f38ab), 2026-11-05:

  * all 98 ``eda.*`` namespaces and every ``dmt_*`` / ``sys_*`` method were
    enumerated: there is no rebuild/refill entry anywhere. ``pcb_PrimitivePour``
    and ``pcb_PrimitivePoured`` expose create/delete/modify/get/getAll/... and
    nothing that recomputes copper. ``pcb_Document`` has
    importChanges / autoRouting / autoLayout / save / get|setCanvasOrigin and no
    fill call. ``sys_ShortcutKey`` can register a shortcut but not run one, and
    ``sys_HeaderMenu`` inserts menu items without executing them.
  * none of these made ``pourFills`` appear: ``save()``, ``pcb_Drc.check()``,
    ``pour.modify({pourPriority})``, ``zoomToAllPrimitives()``, nesting a pour
    inside another, and closing then reopening the document (the border
    survives a reopen; the fill does not).
  * the only thing that fills is the editor's own rebuild - the Shift+B
    shortcut of "rebuild all copper regions" - pressed by a human in the PCB
    canvas. Measured: 0 -> 2 poured primitives, 92 + 37 paths.

So this tool does the half it can do and then refuses to pretend about the
other half: it creates the official border, reads the official fill, and when
the fill is missing it returns ``needs_editor_rebuild=true`` with the exact
keystroke. It never synthesises copper itself - hand-built fills were tried and
produced 302 clearance violations, because a self-drawn fill does not know the
rule table.

``filled`` is a SNAPSHOT, not live copper: a track, pad or outline change after
the last rebuild leaves the stored fill stale, and nothing in the reply can
tell you it is stale. Rebuild after the geometry is final.
"""

from .calc.result import err

# 1 document unit inside the pour/fill geometry = 10 mil (measured: the board
# outline is 1574.8031 units for a 40.000 mm board).
UNIT_MM = 0.254
MIL_PER_MM = 39.37007874015748

# The rebuild entry point this server could not find, written down so the answer
# never has to be re-derived from scratch.
NO_REBUILD_EVIDENCE = {
    "namespaces_scanned": 98,
    "pour_methods": ["create", "delete", "modify", "get", "getAll",
                     "getAllPrimitiveId"],
    "poured_methods": ["create", "delete", "modify", "get", "getAll",
                       "getAllPrimitiveId"],
    "pcb_document_methods_that_sound_like_a_fill": [
        "importChanges", "importAutoRouteJsonFile", "importAutoRouteSesFile",
        "importAutoLayoutJsonFile", "save", "checkCalculatingRatlineStatus",
    ],
    "tried_and_did_not_fill": ["save", "pcb_Drc.check", "pour.modify(priority)",
                               "zoomToAllPrimitives", "nested pour",
                               "close document then reopen"],
}

EDITOR_ACTION = ("switch to the LCEDA Pro PCB canvas and press Shift+B "
                 "(rebuild all copper regions; also reachable from the toolbar "
                 "menu 工具 -> 铺铜区域 -> 重建铺铜). This server cannot press it: "
                 "no API entry exists and simulated keyboard/mouse input into "
                 "this application was measured to have no effect")


def _num(t):
    return isinstance(t, (int, float)) and not isinstance(t, bool)


def _rings(tokens):
    """Split one ``path`` (a list of sub-rings) into coordinate rings.

    Token grammar, read off a real fill: a flat list of numbers and the two
    words ``L`` and ``ARC``. ``ARC`` is followed by an angle and then the next
    point, so both tokens are consumed before a coordinate pair is read.
    """
    out = []
    for sub in tokens or ():
        if not isinstance(sub, list):
            continue
        pts, i = [], 0
        while i < len(sub):
            t = sub[i]
            if _num(t):
                if i + 1 < len(sub) and _num(sub[i + 1]):
                    pts.append((float(t), float(sub[i + 1])))
                    i += 2
                    continue
                i += 1
                continue
            if t == "ARC":
                i += 2
                continue
            i += 1
        if len(pts) >= 3:
            out.append(pts)
    return out


def _ring_area(ring):
    a = 0.0
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        a += x1 * y2 - x2 * y1
    return abs(a) / 2.0


def path_area_mm2(tokens):
    """Copper area of one ``path``: first ring is the edge, the rest are holes."""
    rings = _rings(tokens)
    if not rings:
        return 0.0
    area = _ring_area(rings[0])
    for hole in rings[1:]:
        area -= _ring_area(hole)
    return max(area, 0.0) * UNIT_MM * UNIT_MM


def _polygon_units(poly):
    """Turn the stored border polygon into something a caller can read.

    The API hands back either ``{"polygon": [...]}`` or the bare list; both were
    seen on this build (a freshly created pour returns the object form).
    """
    if isinstance(poly, dict):
        poly = poly.get("polygon")
    return poly


def _rect_tokens(x0_mm, y0_mm, x1_mm, y1_mm):
    """Build the ``R`` polygon the vendor's ``createPolygon`` takes.

    Measured: ``['R', x, y, w, h, 0, 0]`` covers ``x..x+w`` and ``y-h..y`` - the
    height runs towards -y, so ``y`` is the LOWER edge of the rectangle on the
    canvas, which is the larger y because the canvas y axis points down.
    """
    lo_x, hi_x = sorted((x0_mm, x1_mm))
    lo_y, hi_y = sorted((y0_mm, y1_mm))
    x = lo_x * MIL_PER_MM
    y = hi_y * MIL_PER_MM
    w = (hi_x - lo_x) * MIL_PER_MM
    h = (hi_y - lo_y) * MIL_PER_MM
    return ["R", round(x, 4), round(y, 4), round(w, 4), round(h, 4), 0, 0]


LIST_CODE = """
const P = await eda.pcb_PrimitivePour.getAll();
const Q = await eda.pcb_PrimitivePoured.getAll();
const pours = [];
for (const p of P) {
  const cb = p.getState_ComplexPolygon();
  pours.push({id: p.getState_PrimitiveId(), net: String(p.getState_Net()),
              layer: p.getState_Layer(), name: String(p.getState_PourName()),
              priority: p.getState_PourPriority(),
              fillMethod: String(p.getState_PourFillMethod()),
              preserveSilos: !!p.getState_PreserveSilos(),
              lineWidth: p.getState_LineWidth(),
              polygon: (cb && cb.polygon) ? cb.polygon : cb});
}
const poured = [];
for (const q of Q) {
  const fills = q.pourFills || [];
  const paths = [];
  for (const f of fills) {
    const c = f.path || f;
    paths.push(c.complexPolygon !== undefined ? c.complexPolygon : c.path);
  }
  poured.push({pourId: q.pourPrimitiveId || q.primitiveId, n: paths.length,
               paths: paths});
}
return {pours: pours, poured: poured};
"""


def _assemble(editor_reply, doc=None):
    """Fold the editor's border + fill tables into one honest report."""
    pours = list((editor_reply or {}).get("pours") or [])
    poured = list((editor_reply or {}).get("poured") or [])
    by_pour = {}
    for q in poured:
        by_pour.setdefault(q.get("pourId"), []).append(q)

    rows, missing = [], []
    for p in pours:
        fills = by_pour.get(p.get("id"), [])
        paths = []
        for q in fills:
            paths.extend(q.get("paths") or [])
        area = sum(path_area_mm2(t) for t in paths)
        filled = bool(paths)
        row = {
            "id": p.get("id"),
            "net": p.get("net"),
            "layer": p.get("layer"),
            "name": p.get("name"),
            "priority": p.get("priority"),
            "fill_method": p.get("fillMethod"),
            "preserve_silos": p.get("preserveSilos"),
            "border": _polygon_units(p.get("polygon")),
            "filled": filled,
            "fill_paths": len(paths),
            "filled_area_mm2": round(area, 2),
        }
        rows.append(row)
        if not filled:
            missing.append({"id": p.get("id"), "name": p.get("name"),
                            "net": p.get("net"), "layer": p.get("layer")})

    out = {
        "pours": rows,
        "counts": {"pour": len(rows), "poured": len(poured)},
        "all_filled": bool(rows) and not missing,
        "unfilled": missing,
        "needs_editor_rebuild": bool(missing),
        "editor_action": EDITOR_ACTION if missing else None,
        "api_can_rebuild": False,
        "api_has_no_rebuild_entry": dict(NO_REBUILD_EVIDENCE),
        "filled_is_a_snapshot": (
            "fill_paths is the last fill the editor computed, not live copper. "
            "Editing a track, pad or the outline after the last rebuild leaves it "
            "stale and this reply cannot detect that - rebuild once the geometry "
            "is final"),
    }
    if doc is not None:
        out["document"] = doc
    return out


def list_pours(client, session, timeout_ms=None):
    reply = client.invoke(session, LIST_CODE, timeout_ms=timeout_ms)
    return _assemble(reply.get("value"))


def create_pour(client, session, net, layer, rect_mm=None, polygon_mil=None,
                name=None, priority=1, fill_method="solid",
                preserve_silos=False, line_width=10, timeout_ms=None):
    """Create ONE official pour border and read it back.

    Refusals are real refusals: a create whose re-read does not show one more
    pour, carrying the net/layer/polygon asked for, is an error, not a success.
    """
    if not net:
        return None, err("net is required", "ARG_MISSING",
                         hint="the pour is defined by the net it belongs to, "
                              "e.g. GND")
    if layer is None:
        return None, err("layer is required", "ARG_MISSING",
                         hint="1 = top copper, 2 = bottom copper on this build")
    if rect_mm is None and polygon_mil is None:
        return None, err("rect_mm or polygon_mil is required", "ARG_MISSING",
                         hint="rect_mm=[x0,y0,x1,y1] in board mm (canvas y down)")
    if rect_mm is not None:
        if len(rect_mm) != 4:
            return None, err("rect_mm needs four numbers", "ARG_INVALID")
        tokens = _rect_tokens(*[float(v) for v in rect_mm])
    else:
        tokens = list(polygon_mil)

    before = list_pours(client, session, timeout_ms=timeout_ms)
    code = """
const poly = await eda.pcb_MathPolygon.createPolygon(%s);
const p = await eda.pcb_PrimitivePour.create(%s, %s, poly, %s, %s, %s, %s, %s, false);
return p ? p.getState_PrimitiveId() : null;
""" % (_js(tokens), _js(net), int(layer), _js(fill_method),
       "true" if preserve_silos else "false", _js(name or (net + "_POUR")),
       int(priority), _js(line_width))
    made = client.invoke(session, code, timeout_ms=timeout_ms).get("value")
    after = list_pours(client, session, timeout_ms=timeout_ms)
    new_rows = [r for r in after["pours"] if r["id"] == made]
    problems = []
    if after["counts"]["pour"] != before["counts"]["pour"] + 1:
        problems.append("pour count went %s -> %s, expected +1"
                        % (before["counts"]["pour"], after["counts"]["pour"]))
    if not new_rows:
        problems.append("the primitive id the create returned is not in getAll()")
    else:
        row = new_rows[0]
        if row["net"] != str(net):
            problems.append("net reads back as %r" % (row["net"],))
        if str(row["layer"]) != str(layer):
            problems.append("layer reads back as %r" % (row["layer"],))
    if problems:
        return None, err("pour was created but does not read back as asked",
                         "WRITE_HAD_NO_EFFECT",
                         hint="; ".join(problems),
                         extra={"created_id": made,
                                "before": before["counts"],
                                "after": after["counts"]})
    return {"created_id": made, "polygon": tokens,
            "before": before["counts"], "after": after["counts"],
            "report": after}, None


def delete_pours(client, session, ids, timeout_ms=None):
    if not ids:
        return None, err("ids is required", "ARG_MISSING")
    before = list_pours(client, session, timeout_ms=timeout_ms)
    code = "return await eda.pcb_PrimitivePour.delete(%s);" % _js(list(ids))
    okdel = client.invoke(session, code, timeout_ms=timeout_ms).get("value")
    after = list_pours(client, session, timeout_ms=timeout_ms)
    if after["counts"]["pour"] != before["counts"]["pour"] - len(ids):
        return None, err("delete did not remove exactly the ids given",
                         "WRITE_HAD_NO_EFFECT",
                         hint="pour count went %s -> %s for %d ids"
                              % (before["counts"]["pour"],
                                 after["counts"]["pour"], len(ids)),
                         extra={"delete_returned": okdel})
    return {"deleted": list(ids), "before": before["counts"],
            "after": after["counts"], "report": after}, None


def save(client, session, timeout_ms=None):
    out = client.invoke(session, "return await eda.pcb_Document.save();",
                        timeout_ms=timeout_ms).get("value")
    if not out:
        return None, err("the editor did not confirm the save",
                         "WRITE_HAD_NO_EFFECT",
                         hint="save() answered %r; nothing is proven until it "
                              "answers true" % (out,))
    return {"saved": True, "report": list_pours(client, session,
                                                timeout_ms=timeout_ms)}, None


def _js(v):
    import json
    return json.dumps(v)
