"""Views: read facts *out of* the fidelity tree.

A view is a projection, not a reimplementation. Every record it returns carries

* the values read from the file (nothing computed, nothing defaulted), and
* `raw` - the complete S-expression of the node it came from.

So the view is a convenience for the model, while the truth stays in `raw` and in
the document tree. If a view has not been taught about a node, the node is not
lost: it shows up under `uninterpreted` with its full text, and
`KiCadDocument.coverage()` counts it.

Coordinate convention: KiCad stores millimetres with Y growing downwards; views
report `x_mm` / `y_mm` in a Y-up frame (exact negation - no rounding, no
clamping). The raw node keeps the source form.
"""
from . import sexpr as S

# Heads this module knows how to read. Everything else is carried, not dropped.
INTERPRETED_HEADS = frozenset((
    # document skeleton
    "kicad_pcb", "version", "generator", "generator_version", "general",
    "thickness", "paper", "title_block", "title", "date", "rev", "company",
    "comment", "layers", "setup", "stackup", "layer",
    # connectivity identity
    "net", "net_name", "net_class", "add_net",
    # placement and copper
    "footprint", "module", "pad", "segment", "arc", "via", "zone", "filled_polygon",
    "polygon", "pts", "xy", "keepout", "fill", "connect_pads", "hatch",
    "min_thickness", "priority", "island_removal_mode", "copperpour", "tracks",
    "vias", "pads", "type",
    # board graphics and annotation
    "gr_line", "gr_arc", "gr_rect", "gr_circle", "gr_poly", "gr_curve", "gr_text",
    "gr_text_box", "fp_text", "fp_text_box", "fp_line", "fp_arc", "fp_rect",
    "fp_circle", "fp_poly", "fp_curve", "dimension", "group", "image", "target",
    "property", "attr", "model", "effects", "font", "justify", "stroke",
    # per-object knobs (read verbatim by pads()/zones()/setup())
    "at", "start", "end", "mid", "center", "size", "drill", "width", "options",
    "primitives", "chamfer", "roundrect_rratio", "teardrop", "solder_mask_margin",
    "solder_paste_margin", "solder_paste_ratio", "clearance", "thermal_gap",
    "thermal_bridge_width", "zone_connect", "pad_to_mask_clearance",
    "pad_to_paste_clearance", "aux_axis_origin", "grid_origin", "plot_params",
    "pcbplotparams", "diff_pair_gap", "diff_pair_width", "trace_width",
    "track_width", "via_dia", "via_diameter", "via_drill", "uvia_dia",
    "uvia_drill", "epsilon_r", "loss_tangent", "material", "remove_unused_layers",
    "keep_end_layers", "die_length", "locked", "uuid", "tstamp", "path",
    "allow_soldermask_bridges_in_footprints", "embedded_fonts", "format",
    "descr", "tags", "placed", "tedit", "variant", "autoplace_cost90",
    "autoplace_cost180", "embedded_files", "component_classes",
))


class BoardView(object):
    """Read-only projection of one KiCad board document."""

    def __init__(self, doc):
        self.doc = doc
        self._board = doc.board()

    # ------------------------------------------------------------ helpers
    def _wrap(self, node, path, **fields):
        out = dict(fields)
        out["src"] = path
        out["raw"] = S.dumps(node)
        return out

    def _xy(self, node):
        return (S.arg_num(node, 1, 0.0), -(S.arg_num(node, 2, 0.0)))

    def _at(self, node):
        """(x_mm, y_mm, rotation_deg) from an `(at x y [rot])` node."""
        if node is None:
            return (None, None, None)
        return (S.arg_num(node, 1, 0.0), -(S.arg_num(node, 2, 0.0)),
                S.arg_num(node, 3, 0.0))

    def _walk_board(self, index=None):
        """Yield (path, node) for children of the board node, with stable paths."""
        for idx, item in enumerate(self._board.items[1:], start=1):
            if isinstance(item, S.Seq):
                yield ("%d" % idx), item

    # ------------------------------------------------------------ facts
    def nets(self):
        out = []
        for path, node in self._walk_board():
            if S.head(node) == "net":
                out.append(self._wrap(node, path,
                                      code=S.arg_text(node, 1),
                                      name=S.arg_text(node, 2)))
        return out

    def net_classes(self):
        out = []
        for path, node in self._walk_board():
            if S.head(node) != "net_class":
                continue
            fields = {"name": S.arg_text(node, 1),
                      "description": S.arg_text(node, 2)}
            # KiCad 7 spells it `trace_width` / `via_dia`; 8+ uses
            # `track_width` / `via_diameter`. Read both, never default.
            aliases = {
                "clearance": ("clearance",),
                "trace_width": ("trace_width", "track_width"),
                "via_dia": ("via_dia", "via_diameter"),
                "via_drill": ("via_drill",),
                "uvia_dia": ("uvia_dia", "uvia_diameter"),
                "uvia_drill": ("uvia_drill",),
                "diff_pair_gap": ("diff_pair_gap",),
                "diff_pair_width": ("diff_pair_width",),
            }
            for knob, names in aliases.items():
                for name in names:
                    n = S.child(node, name)
                    if n is not None and S.arg_num(n, 1) is not None:
                        fields[knob] = S.arg_num(n, 1)
                        fields[knob + "_key"] = name
                        break
            fields["nets_in_class"] = [
                S.arg_text(n, 1) for n in S.children(node, "add_net")]
            fields["is_default"] = S.child(node, "add_net") is None and \
                (S.arg_text(node, 1) or "").lower() == "default"
            out.append(self._wrap(node, path, **fields))
        return out

    def stackup(self):
        """Layer table exactly as stored (thickness, epsilon_r, loss_tangent...)."""
        out = []
        setup = S.child(self._board, "setup")
        if setup is None:
            return out
        su = S.child(setup, "stackup")
        if su is None:
            return out
        for idx, layer in enumerate(S.children(su, "layer")):
            fields = {"name": S.arg_text(layer, 1),
                      "type": S.arg_text(layer, 2)}
            for key in ("thickness", "epsilon_r", "loss_tangent", "material",
                        "copper_finish", "dielectric_constraints"):
                n = S.child(layer, key)
                if n is None:
                    continue
                val = S.arg_text(n, 1)
                try:
                    fields[key] = float(val)
                except (TypeError, ValueError):
                    fields[key] = val
            out.append(self._wrap(layer, "setup/stackup/%d" % idx, **fields))
        return out

    def setup(self):
        setup = S.child(self._board, "setup")
        if setup is None:
            return None
        fields = {}
        for child in setup.items[1:]:
            if not isinstance(child, S.Seq):
                continue
            h = S.head(child)
            if h in (None, "stackup"):
                continue
            vals = [S.dumps(v) for v in child.items[1:]]
            fields[h] = vals[0] if len(vals) == 1 else vals
        general = S.child(self._board, "general")
        thickness = S.child(general, "thickness") if general is not None else None
        return {"fields": fields,
                "board_thickness_mm": S.arg_num(thickness, 1),
                "src": "setup",
                "raw": S.dumps(setup)}

    def layers(self):
        node = S.child(self._board, "layers")
        if node is None:
            return []
        out = []
        for child in node.items[1:]:
            if isinstance(child, S.Seq) and len(child.items) >= 3:
                out.append({"number": S.arg_text(child, 0),
                            "name": S.arg_text(child, 1),
                            "type": S.arg_text(child, 2),
                            "user_name": S.arg_text(child, 3),
                            "src": "layers",
                            "raw": S.dumps(child)})
        return out

    def footprints(self):
        out = []
        for path, node in self._walk_board():
            if S.head(node) not in ("footprint", "module"):
                continue
            at = self._at(S.child(node, "at"))
            layer = S.arg_text(S.child(node, "layer"), 1) if S.child(node, "layer") else None
            fields = {
                "lib_id": S.arg_text(node, 1),
                "x_mm": at[0], "y_mm": at[1], "rotation_deg": at[2],
                "layer": layer,
                "reference": self._property(node, "Reference"),
                "value": self._property(node, "Value"),
                "dnp": S.child(node, "attr") is not None and
                       "dnp" in S.dumps(S.child(node, "attr")),
                "locked": S.child(node, "locked") is not None,
                "pads": len(S.children(node, "pad")),
                "courtyard": self._has_graphics(node, "F.CrtYd", "B.CrtYd"),
                "silk": self._has_graphics(node, "F.SilkS", "B.SilkS"),
                "model": [S.arg_text(m, 1) for m in S.children(node, "model")],
            }
            out.append(self._wrap(node, path, **fields))
        return out

    def _property(self, node, name):
        for prop in S.children(node, "property"):
            if S.arg_text(prop, 1) == name:
                return S.arg_text(prop, 2)
        for text in S.children(node, "fp_text"):
            if (S.arg_text(text, 1) or "").lower() == name.lower():
                return S.arg_text(text, 2)
        return None

    def _has_graphics(self, node, *layer_names):
        for name in layer_names:
            for key in ("fp_line", "fp_arc", "fp_rect", "fp_circle", "fp_poly",
                        "fp_curve"):
                for g in S.children(node, key):
                    lay = S.child(g, "layer")
                    if lay is not None and S.arg_text(lay, 1) == name:
                        return True
        return False

    def pads(self):
        out = []
        for fpath, fp in self._walk_board():
            if S.head(fp) not in ("footprint", "module"):
                continue
            ref = self._property(fp, "Reference")
            fat = self._at(S.child(fp, "at"))
            for pidx, pad in enumerate(S.children(fp, "pad")):
                fields = {
                    "footprint": ref,
                    "number": S.arg_text(pad, 1),
                    "type": S.arg_text(pad, 2),
                    "shape": S.arg_text(pad, 3),
                }
                pat = S.child(pad, "at")
                local = self._at(pat)
                fields["local_x_mm"] = local[0]
                fields["local_y_mm"] = local[1]
                fields["local_rotation_deg"] = local[2]
                px, py = _rotate(local[0], local[1], fat[2] or 0.0)
                fields["x_mm"] = None if fat[0] is None else fat[0] + px
                fields["y_mm"] = None if fat[1] is None else fat[1] + py
                size = S.child(pad, "size")
                fields["w_mm"] = S.arg_num(size, 1)
                fields["h_mm"] = S.arg_num(size, 2)
                drill = S.child(pad, "drill")
                if drill is not None:
                    fields["drill_mm"] = S.arg_num(drill, 1)
                    fields["drill_oval"] = "oval" in S.dumps(drill)
                net = S.child(pad, "net")
                if net is not None:
                    fields["net_code"] = S.arg_text(net, 1)
                    fields["net"] = S.arg_text(net, 2)
                for knob in ("layers", "roundrect_rratio", "chamfer",
                             "solder_mask_margin", "solder_paste_margin",
                             "solder_paste_ratio", "zone_connect", "clearance",
                             "thermal_gap", "thermal_bridge_width", "die_length",
                             "remove_unused_layers", "keep_end_layers"):
                    n = S.child(pad, knob)
                    if n is not None:
                        vals = [v.text if isinstance(v, S.Atom) else S.dumps(v)
                                for v in n.items[1:]]
                        if vals:
                            fields[knob] = vals[0] if len(vals) == 1 else vals
                pr = S.child(pad, "primitives")
                if pr is not None:
                    fields["primitives"] = S.dumps(pr)
                out.append(self._wrap(pad, "%s/pad[%d]" % (fpath, pidx), **fields))
        return out

    def tracks(self):
        out = []
        for path, node in self._walk_board():
            h = S.head(node)
            if h not in ("segment", "arc"):
                continue
            st = self._xy(S.child(node, "start"))
            en = self._xy(S.child(node, "end"))
            fields = {
                "kind": h,
                "net": S.arg_text(S.child(node, "net"), 2) or
                       S.arg_text(S.child(node, "net"), 1),
                "layer": S.arg_text(S.child(node, "layer"), 1),
                "width_mm": S.arg_num(S.child(node, "width"), 1),
                "x1_mm": st[0], "y1_mm": st[1],
                "x2_mm": en[0], "y2_mm": en[1],
                # The file's own claim that this copper may not be moved
                # (Specctra's `(type fix)`). Absent means movable.
                "locked": S.child(node, "locked") is not None,
            }
            if h == "arc":
                mid = self._xy(S.child(node, "mid"))
                fields["xmid_mm"], fields["ymid_mm"] = mid
            out.append(self._wrap(node, path, **fields))
        return out

    def vias(self):
        out = []
        for path, node in self._walk_board():
            if S.head(node) != "via":
                continue
            at = self._xy(S.child(node, "at"))
            fields = {
                "net": S.arg_text(S.child(node, "net"), 2) or
                       S.arg_text(S.child(node, "net"), 1),
                "x_mm": at[0], "y_mm": at[1],
                "size_mm": S.arg_num(S.child(node, "size"), 1),
                "drill_mm": S.arg_num(S.child(node, "drill"), 1),
                "layers": [S.arg_text(S.child(node, "layers"), 1),
                           S.arg_text(S.child(node, "layers"), 2)],
                "kind": S.arg_text(node, 1) or "through",
                "locked": S.child(node, "locked") is not None,
            }
            out.append(self._wrap(node, path, **fields))
        return out

    def zones(self):
        out = []
        for path, node in self._walk_board():
            if S.head(node) != "zone":
                continue
            keepout = S.child(node, "keepout")
            fill = S.child(node, "fill")
            fields = {
                "net": S.arg_text(S.child(node, "net_name"), 1) or
                       S.arg_text(S.child(node, "net"), 2),
                "layers": [S.arg_text(l, 1) for l in S.children(node, "layer")],
                "priority": S.arg_num(S.child(node, "priority"), 1),
                "keepout": keepout is not None,
                "keepout_rules": (_keepout_rules(keepout) if keepout is not None else None),
                "fill_mode": S.arg_text(fill, 1) if fill is not None else None,
                "island_removal": S.arg_text(S.child(fill, "island_removal_mode"), 1)
                                  if fill is not None else None,
                "outline_points": _pts(S.child(node, "polygon")),
                "filled_polygons": [S.dumps(fp) for fp in S.children(node, "filled_polygon")],
                "filled_polygon_count": len(S.children(node, "filled_polygon")),
                "min_thickness_mm": S.arg_num(S.child(node, "min_thickness"), 1),
                "connect_pads": S.dumps(S.child(node, "connect_pads"))
                                if S.child(node, "connect_pads") is not None else None,
            }
            out.append(self._wrap(node, path, **fields))
        return out

    def outline(self):
        """Everything on Edge.Cuts, verbatim geometry (no polygon shaping)."""
        out = []
        for path, node in self._walk_board():
            h = S.head(node)
            if h not in ("gr_line", "gr_arc", "gr_rect", "gr_circle", "gr_poly",
                         "gr_curve"):
                continue
            lay = S.child(node, "layer")
            if lay is None or S.arg_text(lay, 1) != "Edge.Cuts":
                continue
            fields = {"kind": h}
            for knob, key in (("start", "start"), ("end", "end"), ("mid", "mid"),
                              ("center", "center")):
                n = S.child(node, knob)
                if n is not None:
                    x, y = self._xy(n)
                    fields[key + "_mm"] = [x, y]
            if h == "gr_rect":
                st = self._xy(S.child(node, "start"))
                en = self._xy(S.child(node, "end"))
                fields["corners_mm"] = [st, en]
            if h == "gr_circle":
                fields["end_mm"] = list(self._xy(S.child(node, "end")))
            if h in ("gr_poly", "gr_curve"):
                fields["points"] = _pts(node if h == "gr_poly" else S.child(node, "pts"))
            out.append(self._wrap(node, path, **fields))
        return out

    def graphics(self, layers=None):
        """All board-level graphics/texts, so silk, fab notes and dimensions exist."""
        out = []
        for path, node in self._walk_board():
            h = S.head(node)
            if h not in ("gr_line", "gr_arc", "gr_rect", "gr_circle", "gr_poly",
                         "gr_curve", "gr_text", "gr_text_box", "dimension", "image",
                         "group", "target"):
                continue
            lay = S.child(node, "layer")
            layer = S.arg_text(lay, 1) if lay is not None else None
            if layers and layer not in layers:
                continue
            fields = {"kind": h, "layer": layer}
            if h in ("gr_text", "gr_text_box"):
                fields["text"] = S.arg_text(node, 1)
                at = self._at(S.child(node, "at"))
                fields["x_mm"], fields["y_mm"], fields["rotation_deg"] = at
            if h == "dimension":
                fields["kind_of_dimension"] = S.arg_text(node, 1)
                fields["text"] = S.arg_text(S.child(node, "gr_text"), 1)
            out.append(self._wrap(node, path, **fields))
        return out

    def uninterpreted(self):
        """Nodes whose head has no reading rule yet - returned, not dropped."""
        out = []
        for path, node in self._walk_board():
            h = S.head(node)
            if h is None or h in INTERPRETED_HEADS or h.isdigit():
                continue
            out.append({"head": h, "src": path, "raw": S.dumps(node)})
        return out

    # ------------------------------------------------------------ bundle
    def facts(self):
        return {
            "source": {"format": "kicad_pcb", "path": self.doc.path,
                       "schema": 1, "x_unit": "mm", "y_direction": "up (source is down, negated)"},
            "nets": self.nets(),
            "net_classes": self.net_classes(),
            "layers": self.layers(),
            "stackup": self.stackup(),
            "setup": self.setup(),
            "footprints": self.footprints(),
            "pads": self.pads(),
            "tracks": self.tracks(),
            "vias": self.vias(),
            "zones": self.zones(),
            "outline": self.outline(),
            "graphics": self.graphics(),
            "uninterpreted": self.uninterpreted(),
        }


# --------------------------------------------------------------- helpers
def _rotate(x, y, deg):
    import math
    if not deg:
        return (x, y)
    # KiCad rotates counter-clockwise in a Y-down frame, which in the Y-up frame
    # we report is a clockwise rotation, hence the sign.
    a = math.radians(-deg)
    c, s = math.cos(a), math.sin(a)
    return (x * c - y * s, x * s + y * c)


def _pts(node):
    out = []
    if node is None:
        return out
    target = node
    if S.head(node) != "pts":
        found = S.child(node, "pts")
        if found is None:
            return out
        target = found
    for xy in S.children(target, "xy"):
        out.append([S.arg_num(xy, 1, 0.0), -(S.arg_num(xy, 2, 0.0))])
    return out


def _keepout_rules(node):
    if node is None:
        return None
    out = {}
    for child in node.items[1:]:
        if isinstance(child, S.Seq) and child.items:
            h = S.head(child)
            rest = [S.dumps(v) for v in child.items[1:]]
            out[h] = rest[0] if len(rest) == 1 else rest
    return out
