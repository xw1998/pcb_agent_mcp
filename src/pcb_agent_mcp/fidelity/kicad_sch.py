"""KiCad *schematic* document held with zero loss.

Same contract as `kicad_doc` for boards - an unmodified schematic is re-emitted
byte for byte - but the top-level node is `(kicad_sch ...)` and the accessors
answer schematic questions: which library symbols are embedded, which symbols
are placed, what the project/root path is (that is what `(instances ...)` needs
before a new symbol can carry a reference), and which wires/labels exist.

Why a class instead of `if root == "kicad_sch"` inside the board document: the
board document's `board()` is used in 40 places that all mean *copper*. A
separate type means a schematic can never be handed to a board operation by
accident - the call fails loudly instead of measuring the wrong tree.
"""
import collections

from . import kicad_doc as _kdoc
from . import sexpr as S

ROOT = "kicad_sch"

# Heads that carry an identity at the top level of a schematic, in the order
# KiCad itself writes them. New nodes are inserted after the last existing node
# of their own kind (see edit/sch_ops.py), so this list is documentation, not a
# writer rule.
OBJECT_HEADS = (
    "junction", "no_connect", "bus_entry", "wire", "bus", "polyline",
    "rectangle", "text", "text_box", "label", "global_label",
    "hierarchical_label", "netclass_flag", "symbol", "sheet", "image", "table",
)

class KiCadSchematic(_kdoc.KiCadDocument):
    # ---------------------------------------------------------- loading
    @classmethod
    def from_text(cls, text, path="<memory>"):
        doc = cls(text, S.parse(text), path)
        doc.root()                      # validate: raises if this is not a schematic
        return doc

    @classmethod
    def from_path(cls, path):
        with open(path, "rb") as fh:
            raw = fh.read()
        return cls.from_text(raw.decode("utf-8", "surrogateescape"), path)

    # ---------------------------------------------------------- structure
    def root(self):
        for item in self.tree.items:
            if S.head(item) == ROOT:
                return item
        raise ValueError("no (kicad_sch ...) node at top level - this is not a "
                         "KiCad schematic")

    def board(self):
        """Alias so the inherited `items()` / `head_counts()` helpers work."""
        return self.root()

    def version(self):
        return S.arg_text(S.child(self.root(), "version"), 1)

    def uuid(self):
        return S.arg_text(S.child(self.root(), "uuid"), 1)

    def paper(self):
        node = S.child(self.root(), "paper")
        if node is None:
            return None
        return {"size": S.arg_text(node, 1),
                "portrait": bool(S.child(node, "portrait"))}

    def items(self, name=None):
        return _kdoc.KiCadDocument.items(self, name)

    def lib_symbols(self):
        return S.child(self.root(), "lib_symbols")

    def lib_symbol_names(self):
        node = self.lib_symbols()
        if node is None:
            return []
        return [S.arg_text(s, 1) for s in S.children(node, "symbol")]

    def lib_symbol(self, lib_id):
        node = self.lib_symbols()
        if node is None:
            return None
        for s in S.children(node, "symbol"):
            if S.arg_text(s, 1) == lib_id:
                return s
        return None

    # ---------------------------------------------------------- instances
    def project(self):
        """`(project <name>)` and the root instance path, read from the file.

        A placed symbol carries
            (instances (project "ecc83-pp" (path "/<root-uuid>" (reference "R1"))))
        so the first placed symbol *tells* us both the project name and the
        sheet path a new symbol must use. `sheet_instances (path "/" (page "1"))`
        is the fallback for the path and the file name for the name. Nothing is
        guessed: `source` says which of the two answered.
        """
        for sym in self.items("symbol"):
            inst = S.child(sym, "instances")
            if inst is None:
                continue
            for proj in S.children(inst, "project"):
                name = S.arg_text(proj, 1)
                path = S.child(proj, "path")
                if path is not None:
                    return {"project": name, "root_path": S.arg_text(path, 1),
                            "source": "instances of a placed symbol"}
        si = S.child(self.root(), "sheet_instances")
        base = "/"
        if si is not None:
            p = S.child(si, "path")
            if p is not None:
                base = S.arg_text(p, 1) or "/"
        name = None
        if self.path and self.path != "<memory>":
            import os
            name = os.path.splitext(os.path.basename(self.path))[0]
        if base == "/" and self.uuid():
            base = "/" + self.uuid()
        return {"project": name, "root_path": base,
                "source": "sheet_instances + file name (no placed symbol to read)"}

    # ---------------------------------------------------------- reading
    def symbol_entry(self, node):
        """One placed symbol as facts (never as a guess about the library)."""
        ref = value = footprint = None
        for prop in S.children(node, "property"):
            key = S.arg_text(prop, 1)
            if key == "Reference":
                ref = S.arg_text(prop, 2)
            elif key == "Value":
                value = S.arg_text(prop, 2)
            elif key == "Footprint":
                footprint = S.arg_text(prop, 2)
        at = S.child(node, "at")
        return {
            "reference": ref,
            "value": value,
            "lib_id": S.arg_text(S.child(node, "lib_id"), 1),
            "footprint": footprint or None,
            "unit": S.arg_num(S.child(node, "unit"), 1, 1),
            "x": S.arg_num(at, 1),
            "y": S.arg_num(at, 2),
            "rotation": S.arg_num(at, 3, 0),
            "mirror": S.arg_text(S.child(node, "mirror"), 1),
            "dnp": S.arg_text(S.child(node, "dnp"), 1),
            "uuid": _id(node),
            "pins": [S.arg_text(p, 1) for p in S.children(node, "pin")],
        }

    def symbols(self):
        return [self.symbol_entry(n) for n in self.items("symbol")]

    def object_count(self):
        c = collections.Counter()
        for item in self.root().items[1:]:
            h = S.head(item)
            if h:
                c[h] += 1
        return dict(sorted(c.items(), key=lambda kv: -kv[1]))

    def by_uuid(self, uid):
        """(head, node) of the top-level object with this uuid, or (None, None)."""
        for item in self.root().items[1:]:
            if isinstance(item, S.Seq) and _id(item) == uid:
                return S.head(item), item
        return None, None

    def stats(self):
        n = S.count_nodes(self.tree)
        return {
            "path": self.path,
            "format": "kicad_sch",
            "bytes": len(self.text.encode("utf-8", "surrogateescape")),
            "atoms": n["atoms"],
            "seqs": n["seqs"],
            "version": self.version(),
            "generator": S.arg_text(S.child(self.root(), "generator"), 1),
            "uuid": self.uuid(),
            "paper": self.paper(),
            "object_counts": self.object_count(),
            "lib_symbols": len(self.lib_symbol_names()),
            "placed_symbols": len(self.items("symbol")),
        }

def _id(node):
    for name in ("uuid", "tstamp"):
        n = S.child(node, name)
        if n is not None:
            v = S.arg(n, 1)
            if isinstance(v, S.Atom):
                return v.text
    return None

def sniff(path):
    """True if the file starts with `(kicad_sch`."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(64)
    except OSError:
        return False
    return head.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"(kicad_sch")
