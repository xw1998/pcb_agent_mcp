"""Dangling copper: the tails a board carries, and the ones it is safe to cut.

Mechanism borrowed from FreeRouting (`src/main/java/app/freerouting/autoroute/
pipeline/DanglingCopper.java`): a **tail** is copper attached at one end and free
at the other; the stub behind it is removed up to the next fork, pin or fixed
item; user-fixed copper stays; and the removal is guarded so it cannot open a
connection.

Three readings here are OURS and are stated as ours rather than dressed up as
the reference implementation:

* **"attached" is a claim in the same copper model the router uses.** Two pieces
  of one net are attached when one ends inside the other's width, inside a via's
  size, or inside a pad's reach. Copper that merely passes close is not attached,
  and a gap is a gap. Y is up, distances are millimetres, nothing is assumed.
* **a chain is cuttable when no piece is `(locked)` and no pad would be left with
  copper only through it.** That is what makes "this cannot open a connection" a
  proof instead of a hope: a cuttable chain hangs off exactly one attachment
  point and is the only copper of no pad, so no pad-to-pad path used it.
* **zones are honoured, not ignored:** a same-net `filled_polygon` is copper, and
  a point inside one is attached to the pour. A board that was never filled has
  no such copper, and the report says so rather than guessing what the pour would
  have done once filled.

What we do NOT do: FreeRouting's `removeTraceTails` cuts a piece out of the middle
of a trace. Our write path deletes whole `segment` / `arc` / `via` nodes, which is
what a KiCad file's copper pieces are, so the unit of the cut here is the node and
exactly the chain that was walked.
"""
import math

from . import geometry as G

TOL_MM = 0.002          # a gap smaller than this is a gap, not a connection


# --------------------------------------------------------------- the copper
class _Piece(object):
    """One node of copper: a track, a via or a pad."""

    __slots__ = ("kind", "uuid", "net", "layer", "pts", "width", "locked",
                 "layers", "at", "reach", "bbox")

    def __init__(self, kind, uuid, net):
        self.kind = kind
        self.uuid = uuid
        self.net = net
        self.layer = None
        self.pts = None
        self.width = 0.0
        self.locked = False
        self.layers = ()
        self.at = None
        self.reach = 0.0
        self.bbox = None


def _track_piece(tr):
    piece = _Piece("track", tr.get("uuid"), tr.get("net"))
    piece.layer = tr.get("layer")
    pts = tr.get("_points") or [tr.get("start"), tr.get("end")]
    piece.pts = [(float(p[0]), float(p[1])) for p in pts if p is not None]
    if len(piece.pts) < 2:
        return None
    piece.width = float(tr.get("width") or 0.0)
    piece.locked = bool(tr.get("locked"))
    piece.reach = piece.width / 2.0
    xs = [p[0] for p in piece.pts]
    ys = [p[1] for p in piece.pts]
    r = piece.reach + TOL_MM
    piece.bbox = (min(xs) - r, min(ys) - r, max(xs) + r, max(ys) + r)
    return piece


def _via_piece(via, order):
    piece = _Piece("via", via.get("uuid"), via.get("net"))
    piece.at = (float(via["x"]), float(via["y"]))
    piece.reach = float(via.get("diameter") or 0.0) / 2.0
    piece.locked = bool(via.get("locked"))
    piece.layers = _via_layers(via, order)
    x, y = piece.at
    r = piece.reach + TOL_MM
    piece.bbox = (x - r, y - r, x + r, y + r)
    return piece


def _via_layers(via, order):
    frm, to = via.get("from"), via.get("to")
    if not order:
        return tuple()
    if frm not in order or to not in order:
        return tuple(order)               # a through via, or a name we cannot place
    i, j = order.index(frm), order.index(to)
    lo, hi = min(i, j), max(i, j)
    return tuple(order[lo:hi + 1])


# KiCad's own copper order, so a via's span means the same thing on a board
# whose stackup was not readable.
_CANON_COPPER = (["F.Cu"] + ["In%d.Cu" % i for i in range(1, 31)] + ["B.Cu"])

def _copper_order(board):
    """The copper layers, top to bottom.

    A readable stackup settles it. Otherwise the names the copper itself uses
    are sorted into KiCad's order rather than the order they happened to appear
    in the file, because a via's `from`/`to` only means a span if the two ends
    are placed in the same sequence the board uses.
    """
    order = [s.get("name") for s in (board.stackup or [])
             if str(s.get("type") or "").lower() in ("copper", "signal", "power")]
    order = [l for l in order if l]
    if order:
        return order
    seen = []
    def note(name):
        if name and name not in seen:
            seen.append(name)
    for tr in board.tracks or []:
        note(tr.get("layer"))
    for via in board.vias or []:
        note(via.get("from"))
        note(via.get("to"))
    out = [l for l in _CANON_COPPER if l in seen]
    out += [l for l in seen if l not in _CANON_COPPER]
    return out


class _Index(object):
    """Bucketed lookup by cell. A cell holds every piece whose bbox covers it."""

    def __init__(self, cell=2.0):
        self.cell = float(cell)
        self.map = {}

    def _keys(self, bbox):
        c = self.cell
        i0, j0 = int(math.floor(bbox[0] / c)), int(math.floor(bbox[1] / c))
        i1, j1 = int(math.floor(bbox[2] / c)), int(math.floor(bbox[3] / c))
        # a piece that covers a hundred cells is still one piece; the bound keeps a
        # pathological bbox (a 20 mm track on a 2 mm grid) affordable
        if (i1 - i0) * (j1 - j0) > 4096:
            return None
        for i in range(i0, i1 + 1):
            for j in range(j0, j1 + 1):
                yield (i, j)

    def add(self, piece):
        keys = self._keys(piece.bbox)
        if keys is None:
            self.wide = getattr(self, "wide", [])
            self.wide.append(piece)
            return
        for k in keys:
            self.map.setdefault(k, []).append(piece)

    def near(self, p):
        c = self.cell
        k = (int(math.floor(p[0] / c)), int(math.floor(p[1] / c)))
        out = list(self.map.get(k, ()))
        out.extend(getattr(self, "wide", ()))
        return out


def _touch(piece, p, layer, tol=TOL_MM):
    """How many attachments the point `p` makes to `piece` on `layer`.

    0 = not attached, 1 = the point is one of its ends (or inside a via/pad),
    2 = the point lies INSIDE the piece, i.e. the copper runs on in both
    directions - a T junction, which is a fork, not a continuation.
    """
    if piece.kind == "track":
        if piece.layer != layer:
            return 0
        if not (piece.bbox[0] - tol <= p[0] <= piece.bbox[2] + tol and
                piece.bbox[1] - tol <= p[1] <= piece.bbox[3] + tol):
            return 0
        half = piece.reach + tol
        best, at_end = None, False
        pts = piece.pts
        for k in range(len(pts) - 1):
            d = G.dist_point_segment(p, pts[k], pts[k + 1])
            if best is None or d < best:
                best = d
        if best is None or best > half:
            return 0
        if (G.dist_point_point(p, pts[0]) <= half or
                G.dist_point_point(p, pts[-1]) <= half):
            at_end = True
        return 1 if at_end else 2
    if piece.kind == "via":
        if layer not in piece.layers:
            return 0
        return 1 if G.dist_point_point(p, piece.at) <= piece.reach + tol else 0
    if piece.kind == "pad":
        if layer not in piece.layers:
            return 0
        return 1 if G.dist_point_point(p, piece.at) <= piece.reach + tol else 0
    return 0


def _in_pour(pours, p, layer):
    for poly in pours:
        if layer not in poly[0]:
            continue
        if G.point_in_polygon(p, poly[1]):
            return True
    return False


def _pours(board, net, copper):
    """The filled polygons of this net's real copper (keep-outs are not copper)."""
    out = []
    for z in board.zones or []:
        if z.get("is_keepout"):
            continue
        if z.get("net") != net:
            continue
        layers = tuple(l for l in (z.get("layers") or []) if l in copper)
        if not layers:
            continue
        for poly in z.get("filled_polygons") or ():
            if len(poly) >= 3:
                out.append((layers, poly))
    return out


# --------------------------------------------------------- pad trust ----
def pad_trust(board):
    """Can a pad's *position* be believed on this board?

    A tail is cut by deciding "this copper reaches nothing". That decision
    rests on where the pads are. On a LCEDA-Pro board this reader places pads
    by a transform it measured but cannot verify (`verified_by_app: False`,
    54.1% of 9874 pads land exactly on a copper endpoint) - so a track that
    arrives on a pad someone else placed may simply be one we misplaced.

    Trusting a board is therefore a two-part fact, not a default:

    * the adapter actually read pad geometry (`present["pads"]["present"]`),
    * and the placement is either exact by construction (KiCad) or was
      verified against the app (`source["pad_transform"]["verified_by_app"]`).

    When either part fails, every tail is reported as a candidate and none is
    called cuttable: `removal_ops` then returns nothing.
    """
    pads = (board.present or {}).get("pads") or {}
    present = bool(pads.get("present"))
    transform = (board.source or {}).get("pad_transform") or {}
    verified = transform.get("verified_by_app")
    if not present:
        return {"trusted": False, "reason": "pad_geometry_absent",
                "why": "the file carried no pad geometry, so 'this copper "
                       "reaches no pad' cannot be decided"}
    if verified is False:
        return {"trusted": False, "reason": "pad_placement_unverified",
                "why": "pad positions come from a transform this adapter "
                       "measured but has not verified against the application",
                "evidence": {k: v for k, v in transform.items()
                             if k in ("verified_against", "top_side_hit_rate",
                                      "bottom_side_hit_rate")}}
    return {"trusted": True, "reason": "pads_read_and_placement_verified",
            "why": ("pad positions are exact by construction (KiCad) or were "
                    "verified against the application")}


# ------------------------------------------------------------- the walk
def _pad_piece(pad, copper):
    """A pad as copper: the one thing a track can attach to and stay alive."""
    piece = _Piece("pad", pad.get("uuid"), pad.get("net"))
    if pad.get("x") is None or pad.get("y") is None:
        return None
    piece.at = (float(pad["x"]), float(pad["y"]))
    w = float(pad.get("w") or 0.0)
    h = float(pad.get("h") or w)
    piece.reach = math.hypot(w, h) / 2.0
    raw = [str(l) for l in (pad.get("layers") or [])]
    if "*.Cu" in raw:
        piece.layers = tuple(copper)
    elif "F&B.Cu" in raw:
        piece.layers = tuple(l for l in ("F.Cu", "B.Cu") if l in copper)
    else:
        piece.layers = tuple(l for l in raw if l in copper)
    if not piece.layers:
        return None
    x, y = piece.at
    r = piece.reach + TOL_MM
    piece.bbox = (x - r, y - r, x + r, y + r)
    return piece

def _far_end(piece, node):
    """The other end of a track, given the end we arrived at."""
    if G.dist_point_point(node, piece.pts[0]) <= G.dist_point_point(node, piece.pts[-1]):
        return piece.pts[-1]
    return piece.pts[0]


def _via_is_a_dead_end(via, index, skip, tol):
    """Is this via the end of the road - no other copper of the net on it?

    `skip` holds `id()` of the pieces already in the chain (plus the one the
    walk started from): the stub that leads INTO the via is not "other copper",
    or every via would look alive because of the wire that ends on it.
    """
    for layer in via.layers:
        for other in index.near(via.at):
            if other is via or id(other) in skip:
                continue
            if _touch(other, via.at, layer, tol):
                return False
    return True


def _absorb_dead_end_vias(chain, taken, seen, index, point, layer, tol):
    """Take the dead-end vias sitting on `point` into the chain.

    A tail can end at a via that carries nothing on its other layers. The via is
    then part of the tail, not a place the copper attaches to: `_free_at` let the
    walk start there for exactly that reason, so the walk has to take it along.
    """
    for other in index.near(point):
        if other.kind != "via" or id(other) in taken:
            continue
        if not _touch(other, point, layer, tol):
            continue
        if not _via_is_a_dead_end(other, index, taken, tol):
            continue
        chain.append(other)
        taken.add(id(other))
        if other.uuid:
            seen.add(other.uuid)

def _walk(index, pours, start, free_end, tol):
    """Follow the chain from a free end to a fork, a pin, a pour or nothing.

    Returns `(chain, terminal)`. `chain` is the copper that hangs off this free
    end; the piece it attaches to is NOT in it (that is the trunk).
    """
    chain = [start]
    seen = set([start.uuid]) if start.uuid else set()
    taken = set([id(start)])
    cur = start
    _absorb_dead_end_vias(chain, taken, seen, index, free_end, start.layer, tol)
    node = _far_end(start, free_end)
    terminal = "open"
    while True:
        hits = []
        for other in index.near(node):
            if other is cur or (other.uuid and other.uuid in seen):
                continue
            m = _touch(other, node, cur.layer, tol)
            if m:
                hits.append((other, m))
        if _in_pour(pours, node, cur.layer):
            hits.append((None, 1))          # the pour
        total = sum(m for _o, m in hits)
        if total == 0:
            # copper that ends in mid-air still takes its dead-end vias with it
            _absorb_dead_end_vias(chain, taken, seen, index, node, cur.layer, tol)
            terminal = "open"
            break
        if total > 1:
            terminal = "fork"
            break
        nxt = hits[0][0]
        if nxt is None:
            terminal = "pour"
            break
        if nxt.kind == "via" and _via_is_a_dead_end(nxt, index, taken, tol):
            # a via carrying copper on one layer only is the end of this stub,
            # so it goes with it; leaving it behind would leave a hole nobody
            # drew on purpose (FreeRouting removes exactly this via)
            chain.append(nxt)
            taken.add(id(nxt))
            if nxt.uuid:
                seen.add(nxt.uuid)
            terminal = "dead_end_via"
            break
        if nxt.kind != "track":
            terminal = "via" if nxt.kind == "via" else "pad"
            break
        if nxt.uuid and nxt.uuid in seen:
            terminal = "loop"
            break
        chain.append(nxt)
        taken.add(id(nxt))
        if nxt.uuid:
            seen.add(nxt.uuid)
        node = _far_end(nxt, node)
        cur = nxt
    return chain, terminal


def _free_at(index, pours, piece, end_idx, tol):
    """Is this end of `piece` attached to nothing that keeps it?

    A via that carries copper on one layer only does not keep it: the copper
    ends at the via, and the via is the tail's last piece. Every other
    attachment - a track end or interior, a pad, a pour, a live via - does.
    """
    p = piece.pts[0] if end_idx == 0 else piece.pts[-1]
    if _in_pour(pours, p, piece.layer):
        return False
    hits = []
    for other in index.near(p):
        if other is piece:
            continue
        if _touch(other, p, piece.layer, tol):
            hits.append(other)
    for other in hits:
        if other.kind != "via":
            return False
        if not _via_is_a_dead_end(other, index, set([id(piece)]), tol):
            return False
    return True


def _chain_length(chain):
    total = 0.0
    for piece in chain:
        if piece.kind != "track":
            continue
        pts = piece.pts
        for k in range(len(pts) - 1):
            total += G.dist_point_point(pts[k], pts[k + 1])
    return total


def _pad_reach(board, net, copper):
    """Pads of this net, as (x, y, reach, layers)."""
    out = []
    for fp in board.footprints or []:
        for pad in fp.get("pads") or []:
            if pad.get("net") != net:
                continue
            if pad.get("x") is None:
                continue
            w = float(pad.get("w") or 0.0)
            h = float(pad.get("h") or w)
            raw = [str(l) for l in (pad.get("layers") or [])]
            if "*.Cu" in raw:
                layers = tuple(copper)
            elif "F&B.Cu" in raw:
                layers = tuple(l for l in ("F.Cu", "B.Cu") if l in copper)
            else:
                layers = tuple(l for l in raw if l in copper)
            out.append((float(pad["x"]), float(pad["y"]),
                        math.hypot(w, h) / 2.0, layers))
    return out


def _pad_lost(pads, chain, index, tol):
    """Would a pad of this net be left with copper only through the chain?

    True only when a pad is inside the reach of a chain piece AND inside the
    reach of no piece that stays. That is the case the cut must refuse.
    """
    chain_ids = set(p.uuid for p in chain if p.uuid)
    chain_objs = set(id(p) for p in chain)
    for (x, y, reach, layers) in pads:
        for piece in chain:
            if piece.kind != "track" or piece.layer not in layers:
                continue
            hit = False
            pts = piece.pts
            for k in range(len(pts) - 1):
                if G.dist_point_segment((x, y), pts[k], pts[k + 1]) \
                        <= reach + piece.reach + tol:
                    hit = True
                    break
            if not hit:
                continue
            kept = False
            for other in index.near((x, y)):
                if other.kind != "track":
                    continue
                if id(other) in chain_objs or other.uuid in chain_ids:
                    continue
                if other.layer not in layers:
                    continue
                pts = other.pts
                for k in range(len(pts) - 1):
                    if G.dist_point_segment((x, y), pts[k], pts[k + 1]) \
                            <= reach + other.reach + tol:
                        kept = True
                        break
                if kept:
                    break
            if not kept:
                return True
    return False


# --------------------------------------------------------------- the answer
def survey(board, tol_mm=TOL_MM, max_report=200):
    """Find every tail and every piece of floating copper on `board`.

    Returns a plain dict; the tool layer wraps it in an envelope. Nothing is
    written here and nothing is inferred: a net with no copper, no pads and no
    pour is simply absent.
    """
    copper = _copper_order(board)
    copper_set = set(copper)
    trust = pad_trust(board)
    untrusted = trust["trusted"] is not True

    groups = {}
    for tr in board.tracks or []:
        net = tr.get("net")
        if not net:
            continue
        piece = _track_piece(tr)
        if piece is None:
            continue
        groups.setdefault(net, {"tracks": [], "vias": []})["tracks"].append(piece)
    for via in board.vias or []:
        net = via.get("net")
        if not net:
            continue
        piece = _via_piece(via, copper)
        if not piece.layers:
            continue                       # a via we cannot place is not measured
        groups.setdefault(net, {"tracks": [], "vias": []})["vias"].append(piece)

    nets = []
    tails_total = 0
    floating_total = 0
    removable_total = 0
    removable_length = 0.0
    poured = 0

    for net in sorted(groups):
        group = groups[net]
        index = _Index()
        for piece in group["tracks"] + group["vias"]:
            index.add(piece)
        pours = _pours(board, net, copper_set)
        pads = _pad_reach(board, net, copper_set)
        for x, y, reach, layers in pads:
            pad = _Piece("pad", None, net)
            pad.at = (x, y)
            pad.reach = reach
            pad.layers = layers
            pad.bbox = (x - reach - TOL_MM, y - reach - TOL_MM,
                        x + reach + TOL_MM, y + reach + TOL_MM)
            index.add(pad)
        if pours:
            poured += 1
        if not group["tracks"]:
            continue
        tails, floating = [], []
        claimed = set()
        for piece in group["tracks"]:
            for end_idx in (0, 1):
                if piece.uuid and piece.uuid in claimed:
                    continue
                if not _free_at(index, pours, piece, end_idx, tol_mm):
                    continue
                free_end = piece.pts[0] if end_idx == 0 else piece.pts[-1]
                chain, terminal = _walk(index, pours, piece, free_end, tol_mm)
                ids = [p.uuid for p in chain if p.uuid]
                if set(ids) & claimed:
                    continue
                claimed |= set(ids)
                entry = {
                    "net": net,
                    "layer": piece.layer,
                    "free_end_mm": [round(free_end[0], 4), round(free_end[1], 4)],
                    "length_mm": round(_chain_length(chain), 4),
                    "pieces": len(chain),
                    "tracks": sum(1 for p in chain if p.kind == "track"),
                    "vias": sum(1 for p in chain if p.kind == "via"),
                    "uuids": ids,
                    "track_uuids": [p.uuid for p in chain
                                    if p.kind == "track" and p.uuid],
                    "via_uuids": [p.uuid for p in chain
                                  if p.kind == "via" and p.uuid],
                    "ends_at": terminal,
                }
                if terminal == "open":
                    if untrusted:
                        entry["cuttable"] = False
                        entry["why_not"] = "pad_placement_untrusted"
                    else:
                        entry["cuttable"] = True
                    floating.append(entry)
                    continue
                locked = [p.uuid for p in chain if p.locked]
                if untrusted:
                    entry["cuttable"] = False
                    entry["why_not"] = "pad_placement_untrusted"
                elif locked:
                    entry["cuttable"] = False
                    entry["why_not"] = "locked"
                    entry["locked"] = locked
                elif _pad_lost(pads, chain, index, tol_mm):
                    entry["cuttable"] = False
                    entry["why_not"] = "would_open_a_pad"
                else:
                    entry["cuttable"] = True
                tails.append(entry)
        if not tails and not floating:
            continue
        tails.sort(key=lambda t: -t["length_mm"])
        floating.sort(key=lambda t: -t["length_mm"])
        tails_total += len(tails)
        floating_total += len(floating)
        for t in tails:
            if t["cuttable"]:
                removable_total += 1
                removable_length += t["length_mm"]
        nets.append({
            "net": net,
            "tails": tails[:max_report],
            "tails_total": len(tails),
            "floating": floating[:max_report],
            "floating_total": len(floating),
            "has_pour": bool(pours),
        })

    return {
        "nets": nets,
        "nets_with_dangling": len(nets),
        "tails": tails_total,
        "floating": floating_total,
        "cuttable": removable_total,
        "cuttable_length_mm": round(removable_length, 4),
        "nets_with_pour": poured,
        "tolerance_mm": tol_mm,
        "pad_trust": trust,
        "zones_as_copper": ("a same-net filled_polygon counts as copper; a board "
                            "that was never filled has none, and no pour was "
                            "assumed"),
    }


def removal_ops(report, include_floating=False):
    """The ops that cut every cuttable chain (and, on request, floating copper).

    Floating pieces are a different finding from a tail - copper with no
    attachment at either end connects nothing - so they are only cut when the
    caller says so; a test point someone placed on purpose is not a defect this
    function gets to decide about.
    """
    out = []
    for net in report["nets"]:
        wanted = list(net["tails"])
        if include_floating:
            wanted += list(net["floating"])
        for entry in wanted:
            if entry.get("cuttable") is False:
                continue
            for uid in entry.get("track_uuids") or []:
                out.append({"op": "delete_track", "uuid": uid})
            for uid in entry.get("via_uuids") or []:
                out.append({"op": "delete_via", "uuid": uid})
    return out
