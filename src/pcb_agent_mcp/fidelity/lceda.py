"""The LCEDA Pro record stream: what a `.epcb` / `.esch` / `.efoo` / `.esym`
member actually is, measured against the files on disk.

Measured facts this module encodes (no format document was available, so every
statement below was read off real projects; see docs/13):

1. A member is **one JSON array per line**, in document order. There is no ZIP
   sub-structure and no `||`-separated payload.
2. Line terminators are **not uniform inside an archive**: `.esym` members are
   written with CRLF while `.epcb` / `.efoo` / `.esch` use LF, and the last line
   of a member usually has *no* terminator at all. A blank line occurs at a fixed
   position in PCB documents; a literal `[]` line occurs in FOOTPRINT documents.
   Therefore every line is kept as `(body, terminator)` and a rebuild is
   byte-identical by construction, not by re-serialising.
3. Documents inside a member are delimited by a `["DOCTYPE", kind, version]`
   line. One `.epcb` normally holds a `FOOTPRINT` document followed by a `PCB`
   document.
4. `json.dumps(rec, separators=(",", ":"), ensure_ascii=False)` reproduces the
   original line text for 921,298 of 921,403 lines across 20 real projects
   (99.989%). The 105 exceptions are lines that carry leading `\\t\\t`
   indentation - a formatting quirk, not a serializer difference. So the compact
   form is the right form to *write*, and unchanged lines are still copied
   verbatim rather than regenerated.
5. **Coordinates and sizes are always in mil (0.001 inch = 0.0254 mm)**, whatever
   `CANVAS[3]` or `RULE[4][0]` say. Seen in real data: track widths 19.685 /
   39.3701 / 78.7402 / 98.4252 mil are exactly 0.5 / 1.0 / 2.0 / 2.5 mm; via
   drill 12.0078 mil is 0.305 mm; `LAYER_PHYS` copper thickness 1.379 mil is
   1 oz; a board outline of 7480.5421 units is 190 mm. Reading those numbers as
   mm gives metres. `CANVAS[3]` is the user's *display* unit, and `RULE[4][0]`
   is the display unit of that one rule - both leave the stored numbers alone
   (a project whose tag says `mm` still stores 5.9843 for a 0.152 mm clearance,
   because 0.152 / 0.0254 = 5.9843).
"""
import collections
import json

# Coordinates/sizes in the record stream are mil. This is the only conversion
# constant the reader/writer needs, and it is constant for every document.
MM_PER_COORD = 0.0254

# Fields that carry their own unit label next to their numbers (RULE, DIMENSION).
DECLARED_UNITS = {"mm": 1.0, "mil": 0.0254, "inch": 25.4}

# Separators the vendor writes. The compact form covers 99.989% of real lines;
# the spaced form shows up in a few SQLite `coppers` payloads.
COMPACT = (",", ":")
SPACED = (", ", ": ")

# Tags this project has a reading rule for. Everything else is still carried.
KNOWN_TAGS = frozenset((
    "HEAD", "CANVAS", "ACTIVE_LAYER", "LAYER", "LAYER_PHYS", "PREFERENCE",
    "SILK_OPTS", "RULE", "RULE_SELECTOR", "RULE_TEMPLATE", "PRIMITIVE",
    "NET", "COMPONENT", "ATTR", "PAD", "PAD_NET", "LINE", "ARC", "VIA",
    "POLY", "FILL", "POUR", "POURED", "TEARDROP", "REGION", "STRING", "TEXT",
    "FONT", "FONTSTYLE", "LINESTYLE", "IMAGE", "OBJ", "GROUP", "DIMENSION",
    "CIRCLE", "RECT", "ELLIPSE", "CONNECT", "PANELIZE", "PANELIZE_SIDE",
    "PANELIZE_STAMP", "WIRE", "PIN", "PART", "SHEET", "BLOB", "DOCTYPE",
))


class LcedaError(ValueError):
    """Raised instead of guessing: an unreadable line, an unknown tag layout."""


def dumps(rec):
    """The vendor's compact serialisation of one record."""
    return json.dumps(rec, separators=COMPACT, ensure_ascii=False)


def dumps_spaced(rec):
    return json.dumps(rec, separators=SPACED, ensure_ascii=False)


def to_mm(value):
    if value is None:
        return None
    return value * MM_PER_COORD


def from_mm(mm):
    if mm is None:
        return None
    return mm / MM_PER_COORD


def split_lines(text):
    """-> [(body, terminator)]. terminator is '\\r\\n', '\\n', or '' (last line)."""
    out = []
    i = 0
    while i < len(text):
        j = text.find("\n", i)
        if j < 0:
            out.append((text[i:], ""))
            break
        body = text[i:j]
        term = "\n"
        if body.endswith("\r"):
            body = body[:-1]
            term = "\r\n"
        out.append((body, term))
        i = j + 1
    return out


class Line(object):
    """One physical line: its bytes and, when it parses to a record, the record."""

    __slots__ = ("body", "term", "record", "parsed")

    def __init__(self, body, term, record=None, parsed=False):
        self.body = body
        self.term = term
        self.record = record
        self.parsed = parsed

    @property
    def is_blank(self):
        return not self.body.strip()

    @property
    def is_empty_array(self):
        return self.parsed and self.record == []

    @property
    def tag(self):
        if self.parsed and isinstance(self.record, list) and self.record:
            return self.record[0]
        return None

    def to_text(self):
        return self.body + self.term

    def __repr__(self):
        return "Line(%r)" % (self.body[:60],)


class Document(object):
    """One `["DOCTYPE", kind, version]`-delimited document inside a member."""

    def __init__(self, kind, version, lines):
        self.kind = kind
        self.version = version
        self.lines = lines            # includes the DOCTYPE line itself

    @property
    def records(self):
        return [ln.record for ln in self.lines
                if ln.parsed and isinstance(ln.record, list) and ln.record]

    def by_tag(self, tag):
        return [ln for ln in self.lines if ln.tag == tag]

    def first(self, tag):
        for ln in self.lines:
            if ln.tag == tag:
                return ln.record
        return None

    def canvas(self):
        """The CANVAS record, or None. [3] is the user's display unit."""
        return self.first("CANVAS")

    def display_unit(self):
        cv = self.canvas()
        if cv and len(cv) > 3 and isinstance(cv[3], str):
            return cv[3]
        return None

    def arity(self, tag):
        """{arity: count} for one tag inside this document."""
        return dict(collections.Counter(
            len(ln.record) for ln in self.lines if ln.tag == tag))

    def unique_arity(self, tag):
        """The arity of `tag` in this document, or raise if it is not unique.

        Real FOOTPRINT documents do carry two shapes of PAD (27 and 22 fields),
        so "the arity of PAD" is not a global constant and must never be assumed.
        """
        a = self.arity(tag)
        if not a:
            raise LcedaError("no %s record in %s document" % (tag, self.kind))
        if len(a) > 1:
            raise LcedaError("%s records in this %s document have %d different "
                             "field counts %s - refusing to guess which shape a "
                             "write should produce"
                             % (tag, self.kind, len(a), sorted(a)))
        return list(a)[0]

    def find(self, tag, ident):
        """The line whose record is `[tag, ident, ...]` (first match)."""
        for ln in self.lines:
            r = ln.record
            if ln.tag == tag and len(r) > 1 and r[1] == ident:
                return ln
        return None

    def find_all(self, tag, ident):
        return [ln for ln in self.lines if ln.tag == tag and len(ln.record) > 1
                and ln.record[1] == ident]

    def index_of(self, line):
        for i, ln in enumerate(self.lines):
            if ln is line:
                return i
        raise LcedaError("line does not belong to this document")


class Member(object):
    """One member of an archive (or one SQLite payload) as a document stream."""

    def __init__(self, name, text):
        self.name = name
        self.original = text
        self.lines = []
        self.documents = []
        self._parse()

    # ---------------------------------------------------------- parsing
    def _parse(self):
        self.lines = []
        for body, term in split_lines(self.original):
            rec = None
            parsed = False
            if body.strip():
                try:
                    rec = json.loads(body)
                except ValueError as exc:
                    raise LcedaError("%s: line is not JSON (%s): %r"
                                     % (self.name, exc, body[:120]))
                parsed = True
            self.lines.append(Line(body, term, rec, parsed))

        self.documents = []
        cur = None
        for ln in self.lines:
            if ln.tag == "DOCTYPE":
                cur = Document(ln.record[1], ln.record[2], [])
                self.documents.append(cur)
            if cur is None:
                # bytes before the first DOCTYPE are carried but not interpreted
                continue
            cur.lines.append(ln)
        if not self.documents:
            raise LcedaError("%s: no [\"DOCTYPE\",...] line found" % self.name)

    # ---------------------------------------------------------- output
    def to_text(self):
        return "".join(ln.to_text() for ln in self.lines)

    def to_bytes(self):
        return self.to_text().encode("utf-8")

    def is_verbatim(self):
        return self.to_text() == self.original

    # ---------------------------------------------------------- editing
    def set_record(self, line, record):
        """Replace one line's record. Only this line is re-serialised.

        Everything else keeps the exact bytes it arrived with - that is the whole
        point of holding the stream instead of a resolved object graph.
        """
        parent = None
        for doc in self.documents:
            if any(ln is line for ln in doc.lines):
                parent = doc
                break
        if parent is None:
            raise LcedaError("%s: line is not inside a document" % self.name)
        old = line.record
        if not isinstance(old, list) or not old:
            raise LcedaError("%s: cannot replace a line that is not a record"
                             % self.name)
        if len(record) != len(old):
            raise LcedaError("refusing to change field count %d -> %d on %s; "
                             "the vendor's own records keep a fixed shape"
                             % (len(old), len(record), old[0]))
        line.record = record
        line.body = dumps(record)
        line.parsed = True

    def dominant_term(self):
        """The line terminator this stream actually uses (CRLF or LF)."""
        crlf = self.original.count("\r\n")
        lf = self.original.count("\n") - crlf
        return "\r\n" if crlf and crlf >= lf else ("\n" if lf else "\r\n")

    def append_record(self, record):
        """Append one new record line at the end of the stream.

        The new line uses the terminator the stream already uses, and the last
        line (which may be a bare terminator-less line) is given one first, so
        the appended record does not merge into it.
        """
        body = dumps(record)
        term = self.dominant_term()
        if self.lines and not self.lines[-1].term:
            last = self.lines[-1]
            if last.is_blank:
                # reuse the dangling blank line instead of adding a second one
                last.body, last.term = body, term
                last.record, last.parsed = list(record), True
                return last
            last.term = term
        line = Line(body, term, list(record), True)
        self.lines.append(line)
        if self.documents:
            self.documents[-1].lines.append(line)
        return line

    def remove_line(self, line):
        for doc in self.documents:
            for i, ln in enumerate(doc.lines):
                if ln is line:
                    del doc.lines[i]
                    self.lines.remove(line)
                    return
        raise LcedaError("%s: line is not inside a document" % self.name)

    # ---------------------------------------------------------- proofs
    def roundtrip(self):
        """Byte identity plus how many lines the compact form reproduces.

        `verbatim` is the claim that matters (we rebuild from stored text).
        `canonical` says whether a *regenerated* line would still be identical,
        which is what a writer has to be able to do for the lines it edits.
        """
        canonical = 0
        spaced = 0
        indented = []
        other = []
        for ln in self.lines:
            if not ln.parsed or ln.is_empty_array:
                continue
            c = dumps(ln.record)
            if c == ln.body:
                canonical += 1
            elif dumps_spaced(ln.record) == ln.body:
                spaced += 1
            elif ln.body.lstrip() and c == ln.body.lstrip():
                indented.append(ln.body[:80])
            else:
                other.append({"line": ln.body[:160], "canonical": c[:160]})
        return {
            "verbatim": self.is_verbatim(),
            "canonical": canonical,
            "spaced": spaced,
            "indented": len(indented),
            "other": other[:5],
            "n_other": len(other),
        }

    def tag_layout(self):
        """Per (document kind, tag): field counts and the type at each position.

        This is how the field map in `adapters/epro.py` earns its keep: it is
        read from the bytes in front of us, not from a candidate list.
        """
        layout = {}
        for doc in self.documents:
            tags = collections.Counter(ln.tag for ln in doc.lines if ln.tag)
            for tag in tags:
                key = "%s/%s" % (doc.kind, tag)
                entry = layout.setdefault(key, {"count": 0, "arity": collections.Counter(),
                                                "positions": {}})
                entry["count"] += tags[tag]
                for ln in doc.lines:
                    if ln.tag != tag or not isinstance(ln.record, list):
                        continue
                    entry["arity"][len(ln.record)] += 1
                    for i, v in enumerate(ln.record):
                        pos = entry["positions"].setdefault(i, collections.Counter())
                        pos[type(v).__name__] += 1
        # freeze for JSON
        out = {}
        for key, entry in layout.items():
            out[key] = {
                "count": entry["count"],
                "arity": dict(entry["arity"]),
                "positions": {str(i): dict(c) for i, c in sorted(entry["positions"].items())},
            }
        return out

    def stats(self):
        doc_stats = []
        for doc in self.documents:
            tags = collections.Counter(ln.tag for ln in doc.lines if ln.tag)
            doc_stats.append({
                "kind": doc.kind,
                "version": doc.version,
                "display_unit": doc.display_unit(),
                "n_lines": len(doc.lines),
                "n_records": sum(1 for ln in doc.lines if ln.parsed and ln.record),
                "tags": dict(tags),
            })
        return {
            "name": self.name,
            "bytes": len(self.original.encode("utf-8")),
            "n_lines": len(self.lines),
            "terminators": dict(collections.Counter(ln.term for ln in self.lines)),
            "documents": doc_stats,
            "unknown_tags": sorted(set(ln.tag for ln in self.lines
                                       if ln.tag and ln.tag not in KNOWN_TAGS)),
        }


def member_names_with_stream(names):
    """Which archive members are record streams (vs 0-byte directory markers)."""
    out = []
    for n in names:
        base = n.rsplit("/", 1)[-1]
        if not base or "." not in base:
            continue
        ext = "." + base.rsplit(".", 1)[1]
        if ext in (".epcb", ".esch", ".efoo", ".esym"):
            out.append(n)
    return out
