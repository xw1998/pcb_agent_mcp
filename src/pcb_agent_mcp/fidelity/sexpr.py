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
* **Every byte of whitespace is kept too.** Each node carries the trivia that
  preceded it (`lead`) and each list carries the trivia before its `)`
  (`tail`), so `dumps_document(parse(text)) == text` byte for byte. KiCad
  writes tab-indented, one-child-per-line files; re-emiting them squashed
  changes 15% of the bytes and makes every later diff unreadable, which for a
  file the user has in git is a real loss, not "just formatting".

Two equivalences are offered, because they answer different questions:

* `signature(parse(dumps(x))) == signature(x)` - token identity. Survives
  reformatting; catches a dropped field.
* `dumps_document(parse(x)) == x` - byte identity. Catches that too, plus
  whitespace and line endings; this is what the write path gates on.

`dumps()` (compact) is still available for building a brand-new document and
for readable diffs; `reformat()` gives any node the indentation of its siblings
so an inserted node looks like KiCad wrote it.
"""
import re

_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"'}
_UNESCAPES = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\t": "\\t", "\r": "\\r"}

class Atom(object):
    """A leaf token.

    `quoted` records whether the source had quotes around it; `lead` is the raw
    whitespace that preceded it; `raw` is the source spelling **between** the
    quotes (None when built in code). `raw` exists because a string may contain
    a literal tab or a literal backslash-sequence: KiCad wrote a real TAB into
    `(property "Part Description" "<TAB>100 Position Connector...")`, and a
    writer that re-escapes it as `\t` changes the bytes and may change the text
    KiCad shows. The original spelling wins as long as it still decodes to the
    same string."""

    __slots__ = ("text", "quoted", "lead", "raw")

    def __init__(self, text, quoted=False, lead="", raw=None):
        self.text = text
        self.quoted = bool(quoted)
        self.lead = lead
        self.raw = raw

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
    """A parenthesised list. `items` holds Atom / Seq in source order.

    `lead` is the trivia before the `(`, `tail` the trivia before the `)`."""

    __slots__ = ("items", "lead", "tail")

    def __init__(self, items=None, lead="", tail=""):
        self.items = list(items or ())
        self.lead = lead
        self.tail = tail

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

_WS = " \t\r\n"

def decode_quoted(raw):
    """Decode the characters between two quotes, exactly as the reader does."""
    out = []
    i, n = 0, len(raw)
    while i < n:
        ch = raw[i]
        if ch == "\\" and i + 1 < n:
            nxt = raw[i + 1]
            if nxt in _ESCAPES:
                out.append(_ESCAPES[nxt])
            else:
                out.append("\\" + nxt)
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)

# --------------------------------------------------------------- reading
def _linecol(text, pos):
    """1-based (line, column) of a byte-independent character offset."""
    line = text.count("\n", 0, pos) + 1
    last = text.rfind("\n", 0, pos)
    return line, pos - last


def tokenize(text):
    """Yield (kind, value, offset) with kind in ws/open/close/atom.

    The atom objects yielded here have no `lead`; `parse` attaches it. The
    offset is only used to say *where* a broken file is broken."""
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in _WS:
            j = i
            while j < n and text[j] in _WS:
                j += 1
            yield ("ws", text[i:j], i)
            i = j
            continue
        if c == "(":
            yield ("open", None, i)
            i += 1
            continue
        if c == ")":
            yield ("close", None, i)
            i += 1
            continue
        if c == '"':
            i += 1
            start = i
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
            yield ("atom", Atom("".join(buf), True, raw=text[start:i - 1]), start - 1)
            continue
        j = i
        while j < n and text[j] not in ' \t\r\n()"':
            j += 1
        yield ("atom", Atom(text[i:j], False), i)
        i = j

def parse(text):
    """Parse the whole text into a Seq of top-level nodes.

    Whitespace is attached to the node that follows it (`lead`), and the trivia
    before a closing paren becomes that list's `tail`, so the text can be put
    back together byte for byte by `dumps_document`."""
    top = Seq()
    stack = [top]
    opened = [0]                     # offset of each '(' still waiting for its ')'
    pending = ""
    for kind, val, pos in tokenize(text):
        if kind == "ws":
            pending += val
        elif kind == "open":
            node = Seq(lead=pending)
            pending = ""
            stack[-1].items.append(node)
            stack.append(node)
            opened.append(pos)
        elif kind == "close":
            if len(stack) == 1:
                line, col = _linecol(text, pos)
                raise SexprError("unbalanced ')' at line %d, column %d" % (line, col))
            stack[-1].tail = pending
            pending = ""
            stack.pop()
            opened.pop()
        else:
            val.lead = pending
            pending = ""
            stack[-1].items.append(val)
    if len(stack) != 1:
        line, col = _linecol(text, opened[1])
        raise SexprError("unbalanced '(' - %d list(s) never closed, the outermost "
                         "starts at line %d, column %d"
                         % (len(stack) - 1, line, col))
    top.tail = pending
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
    if a.raw is not None and decode_quoted(a.raw) == a.text:
        return '"' + a.raw + '"'
    out = []
    for ch in a.text:
        out.append(_UNESCAPES.get(ch, ch))
    return '"' + "".join(out) + '"'

def dumps(node):
    """Serialise back to text. One space between tokens, no indentation."""
    if isinstance(node, Atom):
        return _dump_atom(node)
    return "(" + " ".join(dumps(i) for i in node.items) + ")"

def dumps_verbatim(node):
    """Serialise with the trivia recorded during parsing.

    A node built in code has empty trivia, and gluing tokens together would
    produce `(start26-15.6)` - still parseable only by accident. So an empty
    `lead` on anything but the head of a list falls back to a single space:
    identical bytes for everything that came from a file, valid text for
    everything that did not."""
    if isinstance(node, Atom):
        return node.lead + _dump_atom(node)
    parts = [node.lead, "("]
    for i, item in enumerate(node.items):
        if i and not item.lead:
            parts.append(" ")          # built in code: keep tokens apart
        parts.append(dumps_verbatim(item))
    parts.append((node.tail or "") + ")")
    return "".join(parts)

def dumps_document(top):
    """Serialise a whole parsed document (the pseudo-list `parse` returns)."""
    return "".join(dumps_verbatim(i) for i in top.items) + getattr(top, "tail", "")

def canonical_text(text):
    """Whitespace-squashed form, for reporting a readable diff."""
    return re.sub(r"[ \t\r\n]+", " ", text).strip()

# --------------------------------------------------------------- formatting
def reformat(node, lead="", step="\t", tail=None):
    """Give `node` (and everything inside it) the indentation KiCad would use.

    `lead` is what goes before the `(`; children after the first get
    `lead + step`; the list's own `tail` becomes `lead` unless given. Used when
    inserting a freshly built node into a pretty-printed file so the result does
    not read as one long line."""
    if isinstance(node, Atom):
        node.lead = lead
        return node
    node.lead = lead
    for i, item in enumerate(node.items):
        if i == 0:
            reformat(item, "", step)                    # head sits right after "("
        elif isinstance(item, Atom):
            reformat(item, " ", step)                   # atoms stay on the line
        else:
            reformat(item, lead + step, step)           # nested lists go on their own line
    if tail is None:
        # KiCad closes a list on its own line only when it has nested lists;
        # `(at 0 -5.25 0)` keeps its ")" on the same line as the numbers.
        tail = lead if any(isinstance(i, Seq) for i in node.items) else ""
    node.tail = tail
    return node

def child_lead(parent, step="\t"):
    """Indentation to give a new child of `parent`, copied from an existing one."""
    last = None
    for item in parent.items[1:]:
        if isinstance(item, Seq):
            last = item.lead
    if last:
        return last
    return (parent.lead or "") + step

def append_child(parent, node, step="\t"):
    """Append `node` as the last child of `parent`, indented like its siblings."""
    reformat(node, child_lead(parent, step), step)
    parent.items.append(node)
    return node

def insert_child(parent, index, node, step="\t"):
    """Insert `node` at `index` among `parent.items`, indented like its siblings."""
    reformat(node, child_lead(parent, step), step)
    parent.items.insert(index, node)
    return node

def remove_child(parent, node):
    """Drop `node` from `parent.items`; raises if it is not a direct child."""
    for i, item in enumerate(parent.items):
        if item is node:
            del parent.items[i]
            return node
    raise ValueError("node is not a child of that list")

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
