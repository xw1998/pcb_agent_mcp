"""The whole board as ONE circuit: pads, tracks, vias, segment L, segment C.

Why this exists
---------------
`sim_spice` has always been able to solve a netlist. What this server could not
do - and what "simulate the board" actually needs - is PRODUCE one from a
`.kicad_pcb`. A deck typed by hand knows nothing about the copper: it cannot
tell a 0.15 mm escape from a 2 mm trunk, it does not know a via is a plated
barrel, and it cannot answer "what does THIS layout do".

So the extraction is the deliverable, and everything here is in service of it:

* R comes from `oracle.board_ir_drop(..., return_network=True)` - the SAME
  builder that answers the IR-drop question. A second extractor would have been
  the obvious way to write this and the certain way to end up with a deck and a
  report that disagree on the same board.
* L and C come from the transmission line each segment actually forms over its
  reference plane: Z0 and eps_eff from `calc.feedline`, then
  L' = Z0*sqrt(ee)/c0 and C' = sqrt(ee)/(c0*Z0). Both are one physical model,
  so L'*C' = ee/c0^2 holds by construction, which is also the test.
* A via gets the barrel's own inductance; a pad is a NODE, never a capacitor.

What is stated and not modelled
-------------------------------
The reference plane is reached at the NEAREST NODE OF THE RETURN NET, not as a
gridded sheet: the plane's own spreading resistance and inductance are missing
(use `board_ir_drop(model_zones=True)` for the resistance). Segment L is the
transmission-line value, which already carries the image of its own return; the
MUTUAL inductance of two neighbouring traces is not in it. A bend is two
segments sharing a node, so the corner's excess capacitance is not in it
either.

None of that is hidden: every answer carries `not_modelled`.
"""

import math

from .. import oracle as _O
from ..calc import feedline as _FL

C0 = 299792458.0
MU0 = 4e-7 * math.pi

# A segment shorter than this contributes nothing worth a node pair in a deck.
_MIN_SEGMENT_MM = 1e-4

_SKIN_WORDS = ("silk", "paste", "mask", "adhesive", "stiffener")


def _tag(text):
    """A SPICE-safe token that still reads like the thing it names."""
    s = "".join(ch if ch.isalnum() else "_" for ch in str(text or ""))
    s = s.strip("_")
    if not s:
        s = "N"
    if not s[0].isalpha():
        s = "N" + s
    return s


# ------------------------------------------------------------------ the stack
def stack(board):
    """Copper layers top to bottom, with the dielectric gap to each neighbour.

    The stackup is the only place the file says how far a plane is, so when it
    is absent the answer is `ok: False` with the reason - a capacitance invented
    from the board thickness alone would be a dielectric constant nobody wrote
    down. Silkscreen / paste / solder-mask rows are NOT part of the copper-to-
    copper geometry and are dropped, which matters: a 0.01 mm mask row between
    two copper layers would otherwise be read as the dielectric.
    """
    entries = []
    for s in (board.stackup or []):
        typ = str(s.get("type") or "").lower()
        if any(w in typ for w in _SKIN_WORDS):
            continue
        role = "copper" if "copper" in typ else "dielectric"
        t = s.get("thickness_mm")
        try:
            t = float(t) if t is not None else None
        except (TypeError, ValueError):
            t = None
        if not t or t <= 0:
            return {"ok": False,
                    "why": ("the stackup has a %s layer (%r) with no thickness, so "
                            "the distance from a trace to its plane is not stated"
                            % (role, s.get("name")))}
        entries.append({"name": str(s.get("name") or ""), "role": role,
                        "thickness_mm": t, "er": s.get("er"),
                        "copper_um": s.get("copper_um")})
    if not entries:
        return {"ok": False,
                "why": ("the board carries no usable (stackup ...) block: no "
                        "dielectric thickness and no epsilon_r, so a segment's L and "
                        "C cannot be computed. R does not need it")}

    z = 0.0
    for e in entries:
        e["z_top_mm"] = z
        e["z_bottom_mm"] = z + e["thickness_mm"]
        z += e["thickness_mm"]
    layers = [e for e in entries if e["role"] == "copper"]
    if len(layers) < 2:
        return {"ok": False,
                "why": "the stackup declares %d copper layer(s); a reference plane "
                       "needs at least two" % len(layers)}

    for i, cu in enumerate(layers):
        for key, j in (("to_above", i - 1), ("to_below", i + 1)):
            cu[key] = None
            if j < 0 or j >= len(layers):
                continue
            other = layers[j]
            hi, lo = (cu, other) if j > i else (other, cu)
            z0, z1 = hi["z_bottom_mm"], lo["z_top_mm"]
            h = 0.0
            t_er = 0.0
            s_t_over_er = 0.0
            for e in entries:
                if e["role"] != "dielectric":
                    continue
                if e["z_top_mm"] < z0 - 1e-12 or e["z_bottom_mm"] > z1 + 1e-12:
                    continue
                h += e["thickness_mm"]
                er = e.get("er")
                if er:
                    t_er += e["thickness_mm"]
                    s_t_over_er += e["thickness_mm"] / float(er)
            if h <= 0:
                continue
            er_eff = round(t_er / s_t_over_er, 6) if s_t_over_er > 0 else None
            cu[key] = {"layer": other["name"], "h_mm": h, "er": er_eff}
    return {"ok": True, "layers": layers,
            "copper": [l["name"] for l in layers], "thickness_mm": z,
            "z_of": {l["name"]: l["z_top_mm"] for l in layers}}


def _pour_layers(board, net):
    """Copper layers that actually carry filled copper of `net`."""
    out = set()
    for z in (board.zones or []):
        if z.get("is_keepout") or z.get("net") != net:
            continue
        if not (z.get("filled_polygons") or []):
            continue
        for l in (z.get("layers") or []):
            out.add(str(l))
    return out


def return_net(board):
    """The net the board pours the most copper of - its return, named by area.

    Asking by NAME fails on a board whose ground is `PGND`, `0V` or `AGND`.
    The rule is imported from the measurement layer rather than re-written, so
    "which net is the return" cannot mean two things in two answers.
    """
    from ..ruleset import measure as _M
    areas = _M._pour_area_by_net(board)
    if not areas:
        return None, "the board pours copper of no net at all"
    net = max(areas, key=lambda k: areas[k])
    return net, None


# --------------------------------------------------------------- transmission
class _Lines(object):
    """Per (kind, w, h, t, er) cache of the transmission-line solve.

    A board has a handful of distinct widths and one or two dielectric gaps, so
    this turns thousands of segments into tens of field solves.
    """

    def __init__(self):
        self._cache = {}
        self.failures = []
        self.solves = 0

    def per_mm(self, kind, w, h, t_um, er):
        key = (kind, round(float(w), 6), round(float(h), 6),
               round(float(t_um), 6), round(float(er), 6))
        if key in self._cache:
            return self._cache[key]
        self.solves += 1
        env = _FL.feedline_impedance(kind, float(w), float(h),
                                     copper_um=float(t_um), er=float(er))
        if env.get("status") != "ok":
            self._cache[key] = None
            self.failures.append({"kind": kind, "width_mm": w, "h_mm": h,
                                  "er": er, "reason": env.get("message")})
            return None
        v = env["value"]
        z0, ee = float(v["z0_ohm"]), float(v["eps_eff"])
        if z0 <= 0 or ee < 1.0:
            self._cache[key] = None
            self.failures.append({"kind": kind, "width_mm": w, "h_mm": h,
                                  "er": er, "reason": "non-physical solve"})
            return None
        out = {"z0_ohm": z0, "eps_eff": ee,
               "l_per_mm_h": z0 * math.sqrt(ee) / C0 / 1000.0,
               "c_per_mm_f": math.sqrt(ee) / (C0 * z0) / 1000.0}
        self._cache[key] = out
        return out


def via_inductance_h(drill_mm, span_mm):
    """Barrel inductance, L = mu0*h/(2*pi) * (ln(2h/r) + 1).

    The textbook long-barrel form. It is the right order for a via and it is
    WRONG by a few tens of percent once the return current's own path is close
    enough to matter, which is why the answer says so.
    """
    h = float(span_mm) * 1e-3
    r = float(drill_mm) * 5e-4
    if h <= 0 or r <= 0:
        return None
    return MU0 * h / (2.0 * math.pi) * (math.log(2.0 * h / r) + 1.0)


def _plane_for(cu, ret_layers, st):
    """The transmission line a trace on this layer forms, if the file says so."""
    up, dn = cu.get("to_above"), cu.get("to_below")
    cands = []
    if up and up["layer"] in ret_layers and up["h_mm"] > 0:
        cands.append(up)
    if dn and dn["layer"] in ret_layers and dn["h_mm"] > 0:
        cands.append(dn)
    if not cands:
        return None
    both = len(cands) == 2
    pick = min(cands, key=lambda c: c["h_mm"])
    outer = cu is st["layers"][0] or cu is st["layers"][-1]
    return {"layer": pick["layer"], "h_mm": pick["h_mm"], "er": pick.get("er"),
            "kind": ("stripline" if (both and not outer) else "microstrip"),
            "both_sides": both, "outer": outer}


class _Names(object):
    def __init__(self):
        self.used = set()

    def take(self, base):
        name = base
        k = 2
        while name in self.used:
            name = "%s_%d" % (base, k)
            k += 1
        self.used.add(name)
        return name


# ------------------------------------------------------------------- extract
def build(board, nets=None, plating_um=None, copper_um=None, quantize_mm=0.01,
          temp_c=20.0, model_zones=False, include_lc=True,
          max_nodes=60000, max_edges=60000, keep_pads=None):
    """Every net's copper as one SPICE deck, or a refusal per net.

    `nets` defaults to every net that has a track on it. A net that cannot be
    extracted is NAMED with its reason in `skipped` - silently dropping it would
    make a board with one unlucky net look like a smaller board.

    `keep_pads` is the list of pads the caller is going to wire. A net is not
    always one piece of copper: a pad that no track touches, or two islands that
    only a pour would have joined, both appear on finished boards. Every island
    without a wired pad would float, and a floating island makes the matrix
    singular - "singular at column 181" is a linear-algebra fact about the deck,
    not an answer about the board. So with `keep_pads` given, only the islands
    that contain a wired pad go into the deck, and every dropped island is listed
    in `skipped` (ISLAND_NOT_WIRED) and in `islands` with its pads. With
    `keep_pads=None` nothing is dropped and `islands` still reports the splits,
    because a deck with disconnected pieces will not solve either.
    """
    st = stack(board) if include_lc else {"ok": False,
                                          "why": "include_lc=false was asked for"}
    ret, ret_why = return_net(board)
    ret_layers = _pour_layers(board, ret) if ret else set()
    geom = {cu["name"]: cu for cu in st["layers"]} if st.get("ok") else {}

    all_nets = sorted(set(str(t.get("net")) for t in board.tracks
                          if t.get("net")))
    want = [str(n) for n in nets] if nets else all_nets
    missing = [n for n in want if n not in all_nets]
    if missing:
        return _error("net(s) not found on this board's tracks: %s"
                      % ", ".join(sorted(missing)), "ARG_INVALID",
                      hint="nets with tracks on this board: %s"
                           % ", ".join(all_nets[:40]))

    skipped = []
    graphs = {}
    for net in want:
        r = _O.board_ir_drop(board, net, None, None, return_network=True,
                             copper_um=copper_um, plating_um=plating_um,
                             quantize_mm=quantize_mm, temp_c=temp_c,
                             model_zones=model_zones)
        if r.get("status") != "ok":
            skipped.append({"net": net, "reason_code": r.get("reason_code"),
                            "reason": r.get("message")})
            continue
        graphs[net] = r["value"]

    # ---- one net, one piece of copper? measured, not assumed
    wired = None if keep_pads is None else set(str(x) for x in keep_pads)
    islands = []
    # A pad every node of which sits on a piece of copper with no edge at all has
    # no copper connected to it: no track, no via, and (without model_zones) no
    # pour. A pad NAME can stand for several nodes on finished boards (a through
    # hole pad on two layers, two footprints sharing a reference - 19 such names
    # on tiny_tapeout), so this is decided per pad over ALL of its nodes, not by
    # "this name appeared in an edgeless island".
    copperless = set()
    for net in list(graphs):
        g = graphs[net]
        adj = {}
        for e in g["edges"]:
            adj.setdefault(e["a"], []).append(e["b"])
            adj.setdefault(e["b"], []).append(e["a"])
        comp, nc = {}, 0
        for n in g["nodes"]:
            if n["id"] in comp:
                continue
            nc += 1
            comp[n["id"]] = nc
            todo = [n["id"]]
            while todo:
                k = todo.pop()
                for nb in adj.get(k, ()):
                    if nb not in comp:
                        comp[nb] = nc
                        todo.append(nb)
        pads_at = {}
        for t in g.get("terminals") or []:
            pads_at.setdefault(t["node"], []).append(t["pad"])
        groups = {}
        for n in g["nodes"]:
            groups.setdefault(comp[n["id"]], []).append(n)
        ecnt = {}
        for e in g["edges"]:
            ecnt[comp[e["a"]]] = ecnt.get(comp[e["a"]], 0) + 1
        rows = []
        for c, ns in sorted(groups.items()):
            pads = []
            for n in ns:
                pads.extend(pads_at.get(n["id"], []))
            keep = True if wired is None else any(p in wired for p in pads)
            rows.append({"component": c, "nodes": len(ns),
                         "edges": ecnt.get(c, 0), "pads": pads, "kept": keep})
        if wired is not None or nc > 1:
            pad_edges = {}
            for n in g["nodes"]:
                for _p in pads_at.get(n["id"], ()):
                    pad_edges.setdefault(_p, []).append(ecnt.get(comp[n["id"]], 0))
            for _p, _es in pad_edges.items():
                if not any(_es):
                    copperless.add(_p)
        if nc > 1:
            for row in rows:
                islands.append({"net": net, "component": row["component"],
                                "nodes": row["nodes"], "edges": row["edges"],
                                "pads": row["pads"][:20],
                                "pad_count": len(row["pads"]),
                                "kept": row["kept"]})
        if wired is None:
            continue
        gone = [row for row in rows if not row["kept"]]
        if not gone:
            continue
        keeps = set()
        for row in rows:
            if row["kept"]:
                for n in groups[row["component"]]:
                    keeps.add(n["id"])
        if not keeps:
            # nothing on this net was wired: the whole net floats, and a floating
            # network has no unique solution to contribute
            skipped.append({"net": net, "reason_code": "FLOATING_NET",
                            "reason": "no driven or grounded pad of this net was "
                                      "named, so every node of it would float; name "
                                      "one in drives/loads/ground and it is included"})
            del graphs[net]
            continue
        for row in gone:
            skipped.append({"net": net,
                            "reason_code": ("ISLAND_NOT_WIRED" if row["edges"]
                                            else "PAD_WITHOUT_COPPER"),
                            "reason": ("%d node(s) of this net are a separate island "
                                       "that no wired pad touches (%s)%s"
                                       % (row["nodes"],
                                          ", ".join(row["pads"][:6]) or "no pads",
                                          "" if row["edges"] else
                                          ": no track reaches these pads at all")),
                            "pads": row["pads"][:20]})
        g["nodes"] = [n for n in g["nodes"] if n["id"] in keeps]
        g["edges"] = [e for e in g["edges"]
                      if e["a"] in keeps and e["b"] in keeps]
        g["terminals"] = [t for t in (g.get("terminals") or [])
                          if t["node"] in keeps]

    # ---- nearest node of the return net, for the shunt capacitors
    ret_pts = None
    if ret in graphs:
        ret_pts = [(n["id"], float(n["x_mm"]), float(n["y_mm"]))
                   for n in graphs[ret]["nodes"]] or None
    ret_cell = {}
    if ret_pts:
        for _id, px, py in ret_pts:
            ret_cell.setdefault((int(math.floor(px / 2.0)),
                                 int(math.floor(py / 2.0))), []).append(
                (_id, px, py))

    def nearest_ret(x, y):
        if not ret_pts:
            return None
        cx, cy = int(math.floor(x / 2.0)), int(math.floor(y / 2.0))
        best = None
        r = 1
        while best is None and r <= 64:
            for i in range(cx - r, cx + r + 1):
                for j in range(cy - r, cy + r + 1):
                    for _id, px, py in ret_cell.get((i, j), ()):
                        d = (px - x) ** 2 + (py - y) ** 2
                        if best is None or d < best[0]:
                            best = (d, _id)
            r *= 2
        if best is None:
            for _id, px, py in ret_pts:
                d = (px - x) ** 2 + (py - y) ** 2
                if best is None or d < best[0]:
                    best = (d, _id)
        return best[1] if best else None

    names = _Names()
    deck = []
    net_rows = []
    terminals = []
    ref_report = {}
    n_nodes = n_edges = 0
    counts = {"R": 0, "L": 0, "C": 0}
    lc_missing = []
    lines = _Lines()
    _cache = {}

    # A node id is unique only WITHIN one net's graph ('n0' exists in every
    # net). Reading the return net's node through the current net's map named a
    # node of the wrong net, so a segment's shunt C could land on an unrelated
    # node (or be dropped when the ids happened to map to the segment's own
    # ends). Measured on the two-net fixture: ref 'n0' resolved to the SEGMENT
    # ITSELF, and the deck said C=0. Every id is therefore namespaced by its net.
    node_names = {}
    for net in want:
        g = graphs.get(net)
        if g is None:
            continue
        pads_at = {}
        for t in g.get("terminals") or []:
            pads_at.setdefault(t["node"], []).append(t["pad"])
        for n in g["nodes"]:
            here = pads_at.get(n["id"])
            node_names[(net, n["id"])] = names.take(
                _tag(here[0]) if here else "%s_%s" % (_tag(net), n["id"]))

    for net in want:
        g = graphs.get(net)
        if g is None:
            continue
        nodes = g["nodes"]
        if n_nodes + len(nodes) > int(max_nodes):
            skipped.append({"net": net, "reason_code": "TOO_LARGE",
                            "reason": "adding this net would pass max_nodes=%d"
                                      % int(max_nodes)})
            continue
        if n_edges + len(g["edges"]) > int(max_edges):
            skipped.append({"net": net, "reason_code": "TOO_LARGE",
                            "reason": "adding this net would pass max_edges=%d"
                                      % int(max_edges)})
            continue
        pos = {n["id"]: (float(n["x_mm"]), float(n["y_mm"])) for n in nodes}
        nmap = {n["id"]: node_names[(net, n["id"])] for n in nodes}
        for t in g.get("terminals") or []:
            terminals.append({"pad": t["pad"], "node": nmap.get(t["node"]),
                              "net": net, "x_mm": t["x_mm"], "y_mm": t["y_mm"],
                              "layer": (t["layers"] or [None])[0]})
        n_nodes += len(nodes)
        n_edges += len(g["edges"])
        row = {"net": net, "nodes": len(nodes), "edges": len(g["edges"]),
               "terminals": len(g.get("terminals") or []),
               "elements": {"R": 0, "L": 0, "C": 0}}
        thick_mm = st.get("thickness_mm") if st.get("ok") else None

        def _lh_of(e):
            """This segment's own inductance in H, or None when it is not modelled.

            Each reason is recorded once in `lc_missing`, so the answer says what
            it left out instead of silently being a different circuit. The
            transmission-line result is left in `_cache` for the shunt C, because
            the C is the OTHER half of the same solve.
            """
            _cache.clear()
            if not include_lc:
                return None
            kind = e.get("kind")
            if kind == "via":
                if not thick_mm:
                    if not lc_missing:
                        lc_missing.append(
                            "via inductance is not modelled: the board states no "
                            "stackup, so the barrel's length is unknown")
                    return None
                span = e.get("layer") or ""
                za, _, zb = str(span).partition("|")
                zof = st.get("z_of") or {}
                if za in zof and zb in zof:
                    span_mm = abs(zof[zb] - zof[za]) + (thick_mm / (len(zof) or 1))
                else:
                    span_mm = thick_mm
                return via_inductance_h(e.get("drill_mm") or 0.0, span_mm)
            if kind != "track":
                return None
            lay = e.get("layer")
            L = float(e.get("length_mm") or 0.0)
            W = float(e.get("width_mm") or 0.0)
            t_um = float(e.get("copper_eff_um") or e.get("copper_um") or 0.0)
            if L < _MIN_SEGMENT_MM or W <= 0 or t_um <= 0:
                return None
            cu = geom.get(lay)
            if cu is None:
                if not lc_missing:
                    lc_missing.append(
                        "segment L and C are not modelled: %s"
                        % ("the board states no stackup" if not st.get("ok")
                           else "layer %r has no stackup entry" % lay))
                return None
            plane = _plane_for(cu, ret_layers, st)
            if plane is None:
                if not lc_missing:
                    lc_missing.append(
                        "segment L and C are not modelled: layer %r has no adjacent "
                        "layer carrying return-net %r copper, so there is no plane "
                        "to form a transmission line against" % (lay, ret))
                return None
            if plane.get("er") is None:
                if not lc_missing:
                    lc_missing.append(
                        "segment L and C are not modelled: the dielectric between %r "
                        "and %r states no epsilon_r" % (lay, plane["layer"]))
                return None
            got = lines.per_mm(plane["kind"], W, plane["h_mm"], t_um, plane["er"])
            if got is None:
                return None
            _cache["got"] = got
            _cache["plane"] = plane
            _cache["lay"] = lay
            _cache["L"] = L
            return got["l_per_mm_h"] * L

        for e in g["edges"]:
            a, b = nmap.get(e["a"]), nmap.get(e["b"])
            if not a or not b or a == b:
                continue
            r = float(e.get("resistance_ohm") or 0.0)
            # A piece of copper's R and its L are the SAME copper, so they are in
            # SERIES. Written between the same two nodes they become a PARALLEL
            # pair, and at DC an ideal inductor wins every time: every segment
            # shorted itself out and the whole board answered 0.000000 mV while
            # 100 % of the current sat in the L elements (measured on the 20 mm /
            # 0.5 mm strip before this line existed, where the answer should have
            # been 19.7029 mV). The mid node is where the segment's shunt C hangs.
            #
            # The mid node exists ONLY when there is an L to reach the far end.
            # Writing `R a mid` and then deciding not to model the L (include_lc
            # false, no stackup, no plane) leaves the far end of every segment
            # dangling - measured: the resistive-only deck of a two-pad net
            # stopped at the mid node, and the run was refused as floating copper.
            # So the L is decided FIRST, and with no L the R goes straight across.
            lh = _lh_of(e)
            mid = None
            if r > 0:
                counts["R"] += 1
                row["elements"]["R"] += 1
                if lh:
                    mid = names.take("%s_r%d" % (a, counts["R"]))
                    deck.append("R%d %s %s %.12g" % (counts["R"], a, mid, r))
                else:
                    deck.append("R%d %s %s %.12g" % (counts["R"], a, b, r))
            if lh:
                counts["L"] += 1
                row["elements"]["L"] += 1
                deck.append("L%d %s %s %.12g" % (counts["L"], mid or a, b, lh))
            got = _cache.get("got")
            if got is None:
                continue
            plane = _cache["plane"]
            lay = _cache["lay"]
            L = _cache["L"]
            tail = mid or a
            pa, pb = pos.get(e["a"]), pos.get(e["b"])
            ref = nearest_ret((pa[0] + pb[0]) / 2.0,
                              (pa[1] + pb[1]) / 2.0) if (pa and pb) else None
            ref_node = node_names.get((ret, ref)) if ref else None
            cf = got["c_per_mm_f"] * L
            if ref_node and ref_node not in (a, b) and cf > 0:
                counts["C"] += 1
                row["elements"]["C"] += 1
                deck.append("C%d %s %s %.12g" % (counts["C"], tail, ref_node, cf))
                key = (lay, plane["layer"], plane["kind"])
                rr = ref_report.setdefault(key, {
                    "layer": lay, "plane_layer": plane["layer"],
                    "plane_net": ret, "line": plane["kind"],
                    "h_mm": plane["h_mm"], "er": plane["er"],
                    "eps_eff": got["eps_eff"], "z0_ohm": got["z0_ohm"],
                    "segments": 0})
                rr["segments"] += 1
        net_rows.append(row)

    deck_text = "\n".join(
        ["* pcb_agent_mcp whole-board deck",
         "* %d net(s), %d node(s), %d edge(s): R=%d L=%d C=%d"
         % (len(net_rows), n_nodes, n_edges, counts["R"], counts["L"],
            counts["C"])] + deck)
    if st.get("ok") and lines.failures:
        lc_missing.extend("a transmission-line solve refused: %s"
                          % (f.get("reason") or "?") for f in lines.failures[:3])

    not_modelled = [
        "the reference plane SHEET as a network: each shunt C returns at the "
        "nearest node of the return net's own copper, so the plane's spreading "
        "resistance and inductance are absent "
        "(board_ir_drop(model_zones=true) has the resistance)",
        "mutual inductance between neighbouring traces (segment L is the "
        "transmission-line self value; its image of its own return is inside it)",
        "the excess capacitance of a bend (two segments, one shared node)",
        "pad capacitance, connector contact resistance, and every component: the "
        "deck is the BOARD - capacitors, ICs and sources are the caller's to attach",
        "copper temperature rise feeding back into rho(T) (rho is taken at temp_c)",
        "a copper pour on a SIMULATED net as a conductor: without model_zones=true "
        "a pour is not in the network, so a net whose only path is a plane is "
        "reported as skipped rather than joined by an invented link",
    ]
    if not include_lc:
        not_modelled.append("segment L and C (include_lc=false): the deck is "
                            "resistive ONLY, so it answers DC and nothing that "
                            "depends on time or frequency")
    not_modelled.extend(lc_missing)

    return {"status": "ok",
            "value": {
                "deck": deck_text,
                "nets": net_rows,
                "counts": {"nets": len(net_rows), "nodes": n_nodes,
                           "edges": n_edges, "elements": dict(counts),
                           "transmission_line_solves": lines.solves},
                "skipped": skipped,
                "islands": islands,
                "copperless_pads": sorted(copperless),
                "terminals": terminals,
                "references": sorted(ref_report.values(),
                                     key=lambda r: -r["segments"]),
                "return_net": ret,
                "return_net_why": ret_why,
                "include_lc": bool(include_lc),
                "stackup_used": ({"copper": st.get("copper"),
                                  "thickness_mm": st.get("thickness_mm")}
                                 if st.get("ok") else None),
                "stackup_refusal": (None if st.get("ok") else st.get("why")),
                "model": "track_via_pad_RLC_graph",
                "not_modelled": not_modelled,
            }}


def _error(message, code, hint=None):
    return {"status": "error", "reason_code": code, "message": message,
            "hint": hint}
