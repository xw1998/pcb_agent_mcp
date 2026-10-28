"""KiCad board document held with zero loss.

The rule this module implements is the one the whole rewrite is about:

    the middle layer must BE the PCB, not a summary of it.

So a `.kicad_pcb` is read into a complete S-expression tree (every node, every
atom, in order), and the tree is the source of truth. Facts are then read *out
of that tree* - never recomputed from a lossy model, never defaulted.

Three things are checked and reported, because "lossless" is a claim that has to
be paid for with a measurement:

1. `token_identity` - `signature(parse(dumps(tree))) == signature(tree)`.
   A bijection on the token stream. If any field were dropped, this fails.
2. `coverage` - how many nodes are covered by a known reading rule, and the
   *list of heads nobody has interpreted yet*. Those nodes are still carried;
   they are simply not yet explained. Reporting them is the difference between
   "converted" and "guessed".
3. `engine_identity` (optional, in `engine.py`) - hand the re-emitted file to
   KiCad itself and compare what KiCad says about both files. That check does
   not trust any of my code except the writer.
"""
import collections

from . import sexpr as S

SCHEMA = 1


class KiCadDocument(object):
    def __init__(self, text, tree, path="<memory>"):
        self.path = path
        self.text = text
        self.tree = tree

    # ---------------------------------------------------------- loading
    @classmethod
    def from_text(cls, text, path="<memory>"):
        return cls(text, S.parse(text), path)

    @classmethod
    def from_path(cls, path):
        with open(path, "rb") as fh:
            raw = fh.read()
        # surrogateescape: bytes that are not valid UTF-8 survive the round trip
        # instead of being replaced by U+FFFD (which is loss).
        text = raw.decode("utf-8", "surrogateescape")
        return cls(text, S.parse(text), path)

    # ---------------------------------------------------------- writing
    def to_text(self):
        """Serialise with the source's own whitespace.

        Because every node carries the trivia that preceded it, re-emiting an
        unmodified document reproduces the file **byte for byte** - indentation,
        line endings, trailing newline, all of it. Nodes inserted by the write
        path are indented like their siblings (`sexpr.reformat`), so a diff of
        the result shows only the objects that actually changed."""
        return S.dumps_document(self.tree)

    def to_bytes(self):
        return self.to_text().encode("utf-8", "surrogateescape")

    # ---------------------------------------------------------- reading
    def board(self):
        for item in self.tree.items:
            if S.head(item) == "kicad_pcb":
                return item
        raise ValueError("no (kicad_pcb ...) node at top level")

    def items(self, name=None):
        """Direct children of the board node, optionally filtered by head."""
        board = self.board()
        out = []
        for item in board.items[1:]:
            if not isinstance(item, S.Seq):
                continue
            if name is None or S.head(item) == name:
                out.append(item)
        return out

    def head_counts(self):
        c = collections.Counter()
        stack = [self.tree]
        while stack:
            cur = stack.pop()
            if isinstance(cur, S.Seq):
                h = S.head(cur)
                if h is not None:
                    c[h] += 1
                stack.extend(cur.items)
        return c

    def stats(self):
        n = S.count_nodes(self.tree)
        return {
            "path": self.path,
            "format": "kicad_pcb",
            "bytes": len(self.text.encode("utf-8", "surrogateescape")),
            "atoms": n["atoms"],
            "seqs": n["seqs"],
            "distinct_heads": len(self.head_counts()),
            "version": S.arg_text(S.child(self.board(), "version"), 1),
            "generator": S.arg_text(S.child(self.board(), "generator"), 1),
        }

    # ---------------------------------------------------------- proof
    def verify(self):
        """Token-level round-trip proof + formatting report (informational)."""
        emitted = self.to_text()
        try:
            reparsed = S.parse(emitted)
        except S.SexprError as exc:                       # pragma: no cover
            return {"token_identity": False, "error": str(exc)}
        same = S.signature(reparsed) == S.signature(self.tree)
        canon_same = S.canonical_text(emitted) == S.canonical_text(self.text)
        bytes_same = emitted == self.text
        return {
            "token_identity": bool(same),
            "bytes_identical": bool(bytes_same),
            "canonical_text_equal": bool(canon_same),
            "method": "signature(parse(dumps(tree))) == signature(tree)",
            "meaning": ("every atom, in order, with its original spelling and "
                        "quoting - no field can be dropped without this failing"),
            "note": ("bytes_identical is the strong check: it is True only if every "
                     "byte - including tab indentation and CRLF - came back. "
                     "canonical_text_equal ignores whitespace and is kept for "
                     "diagnostics when bytes_identical is False"),
        }

    def coverage(self, interpreted):
        """Which heads are explained by a reading rule, which are only carried."""
        counts = self.head_counts()
        known = {}
        unknown = {}
        for h, c in counts.items():
            # `(0 "F.Cu" signal)` in the layer table has a number as its
            # head; that is the layers rule, not an unknown node.
            understood = h in interpreted or h.isdigit()
            (known if understood else unknown)[h] = c
        total = sum(counts.values()) or 1
        return {
            "heads_total": len(counts),
            "nodes_total": sum(counts.values()),
            "nodes_with_a_reading_rule": sum(known.values()),
            "share_with_a_reading_rule": round(sum(known.values()) / float(total), 4),
            "carried_but_not_yet_interpreted": dict(
                sorted(unknown.items(), key=lambda kv: -kv[1])),
            "meaning": ("carried_but_not_yet_interpreted nodes are in the tree and "
                        "are returned verbatim by board_view(kind='raw'); they are "
                        "not silently dropped and not guessed at"),
        }


def looks_like_kicad(text):
    head = S.canonical_text(text)[:16]
    return head.startswith("(kicad_pcb") or head.startswith("(kicad_sch")


def sniff(path):
    """True if the file starts with a KiCad S-expression header."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(64)
    except OSError:
        return False
    return head.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"(kicad_")
