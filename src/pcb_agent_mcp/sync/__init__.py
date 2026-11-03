"""Schematic netlist vs board: what a "Update PCB from Schematic" would do.

KiCad's GUI has one button for this and no scriptable equivalent, so the
comparison is rebuilt from the two authorities that already exist:

* the **schematic side** is KiCad's own netlister (`kicad-cli sch export netlist
  --format kicadsexpr`), i.e. the same engine that decides connectivity when the
  schematic is opened;
* the **board side** is the board file itself, read through the same `Editor`
  the write path uses, so a pad's net is whatever the file says.

The diff is deliberately *pin level*, not net level: "C7 pin 2 is on GND in the
schematic and has no net on the board" is actionable, "the GND nets differ" is
not. Nothing here writes; a sync comes back as operations for
`schematic_pcb_sync` to put through the normal propose/apply gates.

What is *not* invented:

* a missing footprint has no position on a board, so it is reported as
  `needs_placement` and only becomes an `add_footprint` operation when the
  caller supplies one (there is no honest default);
* a footprint id comparison is exact string equality after normalising case of
  the library nickname, because KiCad resolves library nicknames
  case-insensitively but a *different* name is a different footprint.
"""
import os

from ..edit import ops as O
from ..fidelity import engine as ENGINE
from ..fidelity import kicad_doc as K
from ..fidelity import sexpr as S

__all__ = ["board_index", "netlist_index", "compare", "operations_for",
           "write_plan", "SyncError"]


class SyncError(ValueError):
    def __init__(self, message, code="SYNC_INVALID", **extra):
        ValueError.__init__(self, message)
        self.code = code
        self.extra = extra


# ------------------------------------------------------------------ board side
def board_index(path):
    """Everything a netlist comparison needs to know about a board file."""
    if not os.path.isfile(path):
        raise SyncError("no such board: %r" % path, "PROJECT_NOT_FOUND")
    doc = K.KiCadDocument.from_path(path)
    ed = O.Editor(doc)
    parts = {}
    for fp in ed.footprints():
        ref = ed.footprint_ref(fp)
        pads = {}
        for pad in S.children(fp, "pad"):
            number = S.arg_text(pad, 1) or ""
            net = S.child(pad, "net")
            name = ""
            if net is not None:
                name = S.arg_text(net, 2) or S.arg_text(net, 1) or ""
            pads[number] = name
        layer = ""
        lay = S.child(fp, "layer")
        if lay is not None:
            layer = S.arg_text(lay, 1) or ""
        at = S.child(fp, "at")
        parts[ref or ""] = {
            "lib_id": S.arg_text(fp, 1) or "",
            "layer": layer,
            "at": [S.arg_num(at, 1, 0.0), -S.arg_num(at, 2, 0.0),
                   S.arg_num(at, 3)] if at is not None else None,
            "pads": pads,
        }
    return {
        "path": os.path.abspath(path),
        "name_only_nets": bool(ed.name_only),
        "nets": sorted(n for n in ed.net_by_name if n),
        "footprints": parts,
        "counts": {"footprints": len(parts),
                   "pads": sum(len(p["pads"]) for p in parts.values()),
                   "nets": len([n for n in ed.net_by_name if n])},
    }


# -------------------------------------------------------------- schematic side
def netlist_index(netlist):
    """Normalise `engine.sch_netlist()` output into per-pin and per-net views."""
    if not isinstance(netlist, dict) or netlist.get("status") != "ok":
        raise SyncError("the schematic side is not a netlist result", "NETLIST_INVALID")
    comps = {}
    for comp in netlist.get("components") or []:
        ref = comp.get("ref") or ""
        if not ref:
            continue
        comps[ref] = {"value": comp.get("value") or "",
                      "footprint": comp.get("footprint") or ""}
    pin_net = {}
    net_nodes = {}
    for net in netlist.get("nets") or []:
        name = net.get("name") or ""
        nodes = []
        for node in net.get("nodes") or []:
            ref, pin = node[0], node[1]
            pins = pin_net.setdefault(ref, {})
            if pin in pins and pins[pin] != name:
                # two nets claiming one pin means the netlist itself is
                # inconsistent; keep the first and let the caller see it
                pass
            pins[pin] = name
            nodes.append([ref, pin])
        net_nodes[name] = sorted(nodes)
    aliases = {}
    seen = {}
    for name in net_nodes:
        alias = _alias_of(name)
        aliases[name] = alias
        seen.setdefault(alias, set()).add(name)
    ambiguous = set(alias for alias, names in seen.items() if len(names) > 1)
    return {"components": comps, "pin_net": pin_net, "nets": net_nodes,
            "aliases": aliases, "ambiguous_aliases": ambiguous}


def _alias_of(name):
    """How a schematic net name can appear on a board.

    KiCad's netlister writes a root-sheet net as `/VBUS`; the board a real
    "Update PCB" produced carries `VBUS`. Only that one leading slash is
    dropped: `/power/GND` is a net in a sub-sheet and keeps its path, because
    dropping it would merge two sheets' local nets into one board net.
    """
    if not name or not name.startswith("/"):
        return name
    rest = name.lstrip("/")
    if "/" in rest:
        return name
    return rest


def _norm_lib_id(value):
    """`Lib:Name` compared the way KiCad resolves it: nickname case-insensitive."""
    if not value:
        return ""
    if ":" in value:
        nick, name = value.split(":", 1)
        return nick.lower() + ":" + name
    return value


def _power_ref(ref):
    return (ref or "").startswith("#")


# -------------------------------------------------------------------- compare
def compare(board, netlist):
    """Pin-level differences between a board file and a schematic netlist."""
    board_side = board_index(board) if isinstance(board, str) else board
    sch_side = netlist_index(netlist) if not isinstance(netlist, dict) \
        or "pin_net" not in netlist else netlist
    parts = board_side["footprints"]
    comps = sch_side["components"]
    issues = []

    def add(kind, **fields):
        row = {"kind": kind}
        row.update(fields)
        issues.append(row)

    # 1. a component with a footprint that the board does not have
    for ref in sorted(comps):
        comp = comps[ref]
        if _power_ref(ref):
            continue                      # power symbols never become footprints
        if ref in parts:
            got = parts[ref]["lib_id"]
            want = comp["footprint"]
            if want and _norm_lib_id(got) != _norm_lib_id(want):
                add("footprint_id_mismatch", ref=ref, schematic=want, board=got)
            continue
        if not comp["footprint"]:
            add("component_without_footprint", ref=ref, value=comp["value"],
                note="the symbol has no footprint field, so a board cannot place it")
            continue
        add("missing_on_board", ref=ref, value=comp["value"],
            footprint=comp["footprint"], needs_placement=True)

    # 2. a footprint the schematic does not know
    for ref in sorted(parts):
        if ref and ref not in comps:
            add("on_board_not_in_schematic", ref=ref, lib_id=parts[ref]["lib_id"])

    # 3. pin level: every pin the schematic connects, per pad
    for ref in sorted(comps):
        if _power_ref(ref) or ref not in parts:
            continue
        pads = parts[ref]["pads"]
        pins = sch_side["pin_net"].get(ref, {})
        aliases = sch_side.get("aliases") or {}
        ambiguous = sch_side.get("ambiguous_aliases") or set()
        for pin in sorted(pins):
            raw = pins[pin]
            want = raw
            if raw.startswith("unconnected-"):
                # KiCad names a net with one pin after the pin itself. Such a
                # "net" says the schematic connects nothing there, so the board
                # pad being untied agrees with it rather than disagreeing.
                want = ""
            elif raw not in ambiguous:
                want = aliases.get(raw, raw)
            if pin not in pads:
                add("pin_not_on_footprint", ref=ref, pin=pin, net=want,
                    schematic_net=raw, board_pads=sorted(pads))
                continue
            got = pads[pin]
            if got.startswith("unconnected-"):
                # the mirror image of the case above: KiCad names a pad with no
                # net after the pad itself, so an untied pad and a schematic pin
                # that connects nothing are the same statement, not a mismatch
                got = ""
            if got != want and (not want or got != raw):
                add("pad_net_mismatch", ref=ref, pin=pin, pad=pin,
                    schematic=want, board=got)
        for pad in sorted(pads):
            if pads[pad] and pad not in pins:
                add("pad_net_not_in_schematic_baseline", ref=ref, pad=pad,
                    board=pads[pad])

    # 4. net level: a net the schematic has that no pad on the board carries
    carried = set()
    for part in parts.values():
        for name in part["pads"].values():
            if name:
                carried.add(name)
    aliases = sch_side.get("aliases") or {}
    ambiguous = sch_side.get("ambiguous_aliases") or set()
    for name in sorted(sch_side["nets"]):
        nodes = [n for n in sch_side["nets"][name]
                 if not _power_ref(n[0])]
        if len(nodes) < 2:
            continue                      # a one-pin net is not a connection
        alias = name if name in ambiguous else aliases.get(name, name)
        if name not in carried and alias not in carried:
            add("net_not_on_board", net=alias, nodes=len(nodes),
                schematic_net=name)

    counts = {}
    for row in issues:
        counts[row["kind"]] = counts.get(row["kind"], 0) + 1
    blocking = [k for k in counts
                if k in ("missing_on_board", "pin_not_on_footprint",
                         "pad_net_mismatch", "net_not_on_board",
                         "footprint_id_mismatch")]
    return {
        "status": "ok",
        "board": board_side["path"],
        "board_counts": board_side["counts"],
        "schematic_counts": {"components": len(comps),
                             "nets": len(sch_side["nets"]),
                             "pins": sum(len(v) for v in
                                         sch_side["pin_net"].values())},
        "issues": issues,
        "issue_counts": counts,
        "in_sync": not blocking,
        "blocking_kinds": sorted(blocking),
        "meaning": "in_sync=true means no pin the schematic connects disagrees with "
                   "the board; it is not a DRC pass and not a placement check",
    }


# ------------------------------------------------------------------- planning
def operations_for(diff, placement=None, library=None, remove_extra=False,
                   clear_extra_pad_nets=False, create_nets=True):
    """Turn a diff into board operations. Only what the caller can justify."""
    placement = placement or {}
    ops = []
    unresolved = []
    for row in diff.get("issues") or []:
        kind = row["kind"]
        if kind == "pad_net_mismatch":
            ops.append({"op": "set_pad_net", "ref": row["ref"], "pad": row["pad"],
                        "net": row["schematic"], "create_nets": create_nets})
        elif kind == "missing_on_board":
            where = placement.get(row["ref"])
            if not where:
                unresolved.append({"ref": row["ref"], "why": "needs_placement",
                                   "footprint": row["footprint"]})
                continue
            op = {"op": "add_footprint", "ref": row["ref"],
                  "x": where.get("x"), "y": where.get("y"),
                  "rot": where.get("rot", 0), "layer": where.get("layer", "F.Cu"),
                  "create_nets": create_nets}
            if where.get("footprint_file"):
                op["footprint_file"] = where["footprint_file"]
            else:
                op["library"] = where.get("library") or library
                op["name"] = where.get("name") or row["footprint"].split(":")[-1]
            if where.get("lib_id"):
                op["lib_id"] = where["lib_id"]
            if where.get("pad_nets"):
                op["pad_nets"] = where["pad_nets"]
            elif where.get("library") or library:
                unresolved.append({"ref": row["ref"],
                                   "why": "pad_nets_not_given (the pads will be "
                                          "untied and DRC will say so)"})
            ops.append(op)
        elif kind == "on_board_not_in_schematic" and remove_extra:
            ops.append({"op": "delete_footprint", "ref": row["ref"]})
        elif kind == "pad_net_not_in_schematic_baseline" and clear_extra_pad_nets:
            ops.append({"op": "set_pad_net", "ref": row["ref"], "pad": row["pad"],
                        "net": ""})
    return {"operations": ops, "unresolved": unresolved}
