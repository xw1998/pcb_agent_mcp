"""Lossless S-expression reader/writer.

Nothing in this module understands a board. It exists so the layer above can hold
**every** node of a `.kicad_pcb` - including the nodes nobody has written a rule
for yet - and hand them back unchanged.

Why this is not the same as "just parse it":

* An atom keeps its **original spelling**. `1.0000` stays `1.0000`, `-0` stays
  `-0`, `1e-3` stays `1e-3`. Parsing to float and re-printing is silent loss:
  the value survives, the information ("how it was written, to what precision")
  does not.
* Quoted vs bare is kept. `"R1"` is a string, `R1` is a token; a round trip that
  loses the quotes changes the file.
* Escapes are decoded on read and re-encoded on write with the same table. An
  escape outside the table keeps its backslash instead of quietly eating it.
* Node order and duplication are kept. Two `(net 1 ...)` nodes are two nodes.

The equivalence this buys is *token identity*: `signature(parse(dumps(x))) ==
signature(x)` is a bijection on the token stream, so no field can be dropped
without the test failing. Whitespace/indentation is deliberately NOT preserved
(that is formatting, not information); `canonical_text` squashes it for a
human-readable diff.
"""
import re

_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"'}
_UNESCAPES = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\t": "\\t", "\r": "\\r"}


class Atom(object):
    """A leaf token. `quoted` records whether the source had quotes around it."""

    __slots__ = ("text", "quoted")

    def __init__(self, text, quoted=False):
        self.text = text
        self.quoted = bool(quoted)

    def __repr__(self):
        return "Atom(%r%s)" % (self.text, ", q" if self.quoted else "")

    def __eq__(self, other):
        return (isinstance(other, Atom) and other.text == self.text
                and other.quoted == self.quoted)

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return hash((self.text, self.quoted))


class Seq(object):
    """A parenthesised list. `items` holds Atom / Seq in source order."""

    __slots__ = ("items",)

    def __init__(self, items=None):
        self.items = list(items or ())

    def __repr__(self):
        return "Seq(%d)" % len(self.items)

    def __iter__(self):
        return iter(self.items)

    def __len__(self):
        return len(self.items)

    def __getitem__(self, i):
        return self.items[i]


class SexprError(ValueError):
    pass


# --------------------------------------------------------------- reading
def tokenize(text):
    """Yield ('open', None) / ('close', None) / ('atom', Atom)."""
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in " \t\r\n":
            i += 1
            continue
        if c == "(":
            yield ("open", None)
            i += 1
            continue
        if c == ")":
            yield ("close", None)
            i += 1
            continue
        if c == '"':
            i += 1
            buf = []
            while i < n:
                ch = text[i]
                if ch == "\\" and i + 1 < n:
                    nxt = text[i + 1]
                    if nxt in _ESCAPES:
                        buf.append(_ESCAPES[nxt])
                    else:
                        buf.append("\\" + nxt)   # keep it, do not eat it
                    i += 2
                    continue
                if ch == '"':
                    i += 1
                    break
                buf.append(ch)
                i += 1
            else:
                raise SexprError("unterminated string at end of input")
            yield ("atom", Atom("".join(buf), True))
            continue
        j = i
        while j < n and text[j] not in ' \t\r\n()"':
            j += 1
        yield ("atom", Atom(text[i:j], False))
        i = j


def parse(text):
    """Parse the whole text into a Seq of top-level nodes."""
    top = Seq()
    stack = [top]
    for kind, val in tokenize(text):
        if kind == "open":
            node = Seq()
            stack[-1].items.append(node)
            stack.append(node)
        elif kind == "close":
            if len(stack) == 1:
                raise SexprError("unbalanced ')'")
            stack.pop()
        else:
            stack[-1].items.append(val)
    if len(stack) != 1:
        raise SexprError("unbalanced '(' (%d unclosed)" % (len(stack) - 1))
    return top


def parse_single(text):
    """Parse text that must contain exactly one top-level node."""
    top = parse(text)
    if len(top.items) != 1:
        raise SexprError("expected exactly 1 top-level node, got %d" % len(top.items))
    node = top.items[0]
    if not isinstance(node, Seq):
        raise SexprError("top-level node is an atom, not a list")
    return node


# --------------------------------------------------------------- writing
def _dump_atom(a):
    if not a.quoted:
        return a.text
    out = []
    for ch in a.text:
        out.append(_UNESCAPES.get(ch, ch))
    return '"' + "".join(out) + '"'


def dumps(node):
    """Serialise back to text. One space between tokens, no indentation."""
    if isinstance(node, Atom):
        return _dump_atom(node)
    return "(" + " ".join(dumps(i) for i in node.items) + ")"


def canonical_text(text):
    """Whitespace-squashed form, for reporting a readable diff."""
    return re.sub(r"[ \t\r\n]+", " ", text).strip()


# --------------------------------------------------------------- structure
def head(node):
    """Leading bare atom of a Seq, or None."""
    if isinstance(node, Seq) and node.items:
        first = node.items[0]
        if isinstance(first, Atom) and not first.quoted:
            return first.text
    return None


def children(node, name):
    out = []
    if isinstance(node, Seq):
        for item in node.items[1:]:
            if isinstance(item, Seq) and head(item) == name:
                out.append(item)
    return out


def child(node, name):
    for item in (children(node, name) or ()):
        return item
    return None


def arg(node, i, default=None):
    """i-th item of a Seq (0 is the head). Returns Atom or Seq."""
    if isinstance(node, Seq) and len(node.items) > i:
        return node.items[i]
    return default


def arg_text(node, i, default=None):
    v = arg(node, i)
    if isinstance(v, Atom):
        return v.text
    return default


def arg_num(node, i, default=None):
    v = arg_text(node, i)
    if v is None:
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def walk(node, path=""):
    """Yield (path, node) for every node, depth first, in source order."""
    yield path, node
    if isinstance(node, Seq):
        for idx, item in enumerate(node.items):
            sub = "%s/%d" % (path, idx) if path else "%d" % idx
            for pair in walk(item, sub):
                yield pair


def signature(node):
    """Nested tuple that is equal iff the token stream is equal."""
    if isinstance(node, Atom):
        return ("a", node.text, node.quoted)
    return tuple(signature(i) for i in node.items)


def count_nodes(node):
    atoms = seqs = 0
    stack = [node]
    while stack:
        cur = stack.pop()
        if isinstance(cur, Atom):
            atoms += 1
        else:
            seqs += 1
            stack.extend(cur.items)
    return {"atoms": atoms, "seqs": seqs}
