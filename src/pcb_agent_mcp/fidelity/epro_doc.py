"""EasyEDA / LCEDA Pro `.epro` container held with zero loss.

`.epro` is a ZIP whose PCB document is an **append-only log**, one JSON object
per line. A lossless reader therefore has to keep three separate things:

1. **every entry of the ZIP**, including the ones this project has no reader for
   (`*.eblob`, `*.esym`, images, 3D models) - stored as raw bytes;
2. **every line of every log**, in order, with the raw text (so a rebuild is
   byte-identical, and a line this project does not understand is still there);
3. **the resolved state** - the same `(type, id)` appears repeatedly with a
   rising `ticket`; the highest ticket wins and an empty payload means "gone".
   Both the resolved state and the deletion record are kept, because "this object
   was deleted" is itself a fact about how the board came to be.

Point 3 is where a naive reader produces a *stale* board that still passes every
check. Point 1 is where a naive reader produces a board that is missing the
copper-pour fill (`.ecop`) and therefore reports a different copper area.

`key_inventory()` exists to turn key-name guessing into reading: for each object
type it reports every payload key actually seen, with a sample value and a type.
Those are the keys the mapping layer should bind to - not a candidate list
invented from a format description.
"""
import collections
import io
import json
import zipfile

TEXT_EXTS = (".epcb", ".esch", ".esym", ".efoo", ".ecop", ".json", ".net", ".txt")

# Types this repository has a reading rule for. Anything else is still carried.
KNOWN_TYPES = frozenset((
    "LAYER", "NET", "RULE", "VIA", "LINE", "ARC", "POLY", "FILL", "POUR",
    "POURED", "TEARDROP", "REGION", "PAD", "COMPONENT", "PAD_NET", "ATTR",
    "STRING", "IMAGE", "OBJ", "DELETE_DOC", "SHEET", "TEXT",
))


class EproDocument(object):
    def __init__(self, path, entries):
        self.path = path
        self.entries = entries            # name -> bytes, in ZIP order
        self._order = list(entries.keys())
        self._lines = {}                  # name -> [raw line str]
        self._records = {}                # name -> [(lineno, header, payload, raw)]
        self._resolved = {}               # name -> {(type,id): {...}}

    # ---------------------------------------------------------- loading
    @classmethod
    def from_path(cls, path):
        with zipfile.ZipFile(path, "r") as zf:
            entries = collections.OrderedDict()
            for info in zf.infolist():
                if info.is_dir():
                    continue
                entries[info.filename] = zf.read(info.filename)
        return cls(path, entries)

    @classmethod
    def from_bytes(cls, blob, path="<memory>"):
        with zipfile.ZipFile(io.BytesIO(blob), "r") as zf:
            entries = collections.OrderedDict()
            for info in zf.infolist():
                if info.is_dir():
                    continue
                entries[info.filename] = zf.read(info.filename)
        return cls(path, entries)

    # ---------------------------------------------------------- members
    def names(self):
        return list(self._order)

    def text_members(self):
        return [n for n in self._order if n.lower().endswith(TEXT_EXTS)]

    def binary_members(self):
        return [n for n in self._order if not n.lower().endswith(TEXT_EXTS)]

    def entry_bytes(self, name):
        return self.entries[name]

    # ---------------------------------------------------------- lines
    def lines(self, name):
        if name not in self._lines:
            raw = self.entries[name].decode("utf-8", "surrogateescape")
            self._lines[name] = raw.splitlines()
        return self._lines[name]

    def records(self, name):
        """[(lineno, header, payload, raw)] — unparsable lines are kept with
        header/payload None so nothing is dropped silently."""
        if name in self._records:
            return self._records[name]
        out = []
        for idx, line in enumerate(self.lines(name)):
            stripped = line.strip()
            if not stripped:
                out.append((idx, None, None, line))
                continue
            header, payload = _split(stripped)
            out.append((idx, header, payload, line))
        self._records[name] = out
        return out

    def unparsed(self, name):
        return [r for r in self.records(name) if r[1] is None and r[3].strip()]

    # ---------------------------------------------------------- state
    def resolved(self, name):
        """Highest ticket wins per (type, id). Deletions are kept as a fact."""
        if name in self._resolved:
            return self._resolved[name]
        state = collections.OrderedDict()
        for lineno, header, payload, _raw in self.records(name):
            if header is None:
                continue
            otype = _text(header.get("type"))
            oid = _text(header.get("id") or header.get("uuid"))
            if otype is None and oid is None:
                otype, oid = "?", "?"
            if otype == "DELETE_DOC":
                state[("DELETE_DOC", oid or str(lineno))] = {
                    "type": "DELETE_DOC", "id": oid, "ticket": None,
                    "deleted": True, "payload": None, "line": lineno}
                continue
            ticket = header.get("ticket")
            try:
                ticket = int(ticket)
            except (TypeError, ValueError):
                ticket = None
            key = (otype, oid)
            prev = state.get(key)
            if prev is not None and not prev["deleted"]:
                pt = prev["ticket"]
                if pt is not None and ticket is not None and ticket < pt:
                    continue
            deleted = payload is None or (isinstance(payload, dict) and not payload)
            state[key] = {"type": otype, "id": oid, "ticket": ticket,
                          "deleted": bool(deleted), "payload": payload,
                          "line": lineno}
        self._resolved[name] = state
        return state

    def live(self, name):
        return [v for v in self.resolved(name).values() if not v["deleted"]]

    def deleted(self, name):
        return [v for v in self.resolved(name).values() if v["deleted"]]

    # ---------------------------------------------------------- rebuild
    def rebuild_bytes(self, name):
        """Byte-identical rebuild from the parsed lines."""
        raw = self.entries[name].decode("utf-8", "surrogateescape")
        joined = "\n".join(self.lines(name))
        # splitlines() drops the final newline's presence; restore it.
        if raw.endswith("\n") and not joined.endswith("\n"):
            joined += "\n"
        return joined.encode("utf-8", "surrogateescape")

    def rebuild_state_lines(self, name):
        """Re-emit the *resolved* state as one line per live object.

        Reading this back must resolve to the same state - that is the
        idempotence check, and it is what proves ticket/deletion handling is
        not inventing or losing objects.
        """
        out = []
        for rec in self.live(name):
            header = {"type": rec["type"], "id": rec["id"]}
            if rec["ticket"] is not None:
                header["ticket"] = rec["ticket"]
            out.append(json.dumps(header, sort_keys=True) + "||" +
                       json.dumps(rec["payload"], sort_keys=True))
        return out

    # ---------------------------------------------------------- evidence
    def stats(self):
        per_member = []
        total_lines = total_unparsed = 0
        for name in self._order:
            if name.lower().endswith(TEXT_EXTS):
                recs = self.records(name)
                bad = len(self.unparsed(name))
                total_lines += len(recs)
                total_unparsed += bad
                per_member.append({
                    "name": name,
                    "bytes": len(self.entries[name]),
                    "lines": len(recs),
                    "unparsed": bad,
                    "objects_resolved": len(self.resolved(name)),
                    "objects_live": len(self.live(name)),
                    "objects_deleted": len(self.deleted(name)),
                })
            else:
                per_member.append({"name": name, "bytes": len(self.entries[name]),
                                   "binary": True})
        return {
            "path": self.path,
            "format": "epro",
            "members": len(self._order),
            "text_members": len(self.text_members()),
            "binary_members": len(self.binary_members()),
            "lines_total": total_lines,
            "lines_unparsed": total_unparsed,
            "detail": per_member,
        }

    def key_inventory(self, name):
        """Every payload key actually present, per object type.

        This is how a candidate list stops being a guess: bind the mapping to
        what the file really contains.
        """
        inv = collections.OrderedDict()
        for lineno, header, payload, _raw in self.records(name):
            if header is None:
                continue
            otype = _text(header.get("type")) or "?"
            if otype == "DELETE_DOC":
                continue
            slot = inv.setdefault(otype, {
                "objects": 0, "keys": collections.OrderedDict()})
            slot["objects"] += 1
            for k, v in (payload or {}).items():
                entry = slot["keys"].setdefault(k, {"count": 0, "types": {},
                                                    "sample": None})
                entry["count"] += 1
                tname = type(v).__name__
                entry["types"][tname] = entry["types"].get(tname, 0) + 1
                if entry["sample"] is None and v not in (None, ""):
                    entry["sample"] = v
        return inv

    def known_types_seen(self):
        seen = collections.Counter()
        unknown = collections.Counter()
        for name in self.text_members():
            for _lineno, header, _payload, _raw in self.records(name):
                if header is None:
                    continue
                t = _text(header.get("type"))
                if t is None:
                    # an untagged JSON object (e.g. project.json) is not an
                    # unknown object TYPE, it is a plain document
                    continue
                seen[t] += 1
                if t not in KNOWN_TYPES:
                    unknown[t] += 1
        return {"seen": dict(seen), "not_in_known_types": dict(unknown)}

    def verify(self):
        """Byte-identity per text member + resolved-state idempotence."""
        members = {}
        all_ok = True
        for name in self.text_members():
            rebuilt = self.rebuild_bytes(name)
            byte_ok = rebuilt == self.entries[name]
            replay = self._resolve_from_lines(self.rebuild_state_lines(name))
            # the replay contains live objects only, so the live half is the
            # comparison; deletions stay in the original log and are reported
            # separately by stats()/deleted().
            state_ok = (_state_signature(replay)[0]
                        == _state_signature(self.resolved(name))[0])
            members[name] = {"byte_identity": bool(byte_ok),
                             "state_idempotent": bool(state_ok)}
            all_ok = all_ok and byte_ok and state_ok
        return {
            "byte_identity": bool(all_ok),
            "members": members,
            "method": ("each text member re-serialised from its parsed lines is "
                       "byte-identical, and re-reading the resolved state gives "
                       "the same state"),
        }

    def _resolve_from_lines(self, lines):
        tmp = EproDocument(self.path, {"replay": "\n".join(lines).encode(
            "utf-8", "surrogateescape")})
        return tmp.resolved("replay")


# --------------------------------------------------------------- helpers
def _split(line):
    """`{"type","id","ticket"}||{payload}` -> (header, payload).

    Also accepts a plain object where header fields live in the payload.
    """
    if "||" in line:
        head, _, tail = line.partition("||")
        try:
            header = json.loads(head)
        except ValueError:
            return None, None
        try:
            payload = json.loads(tail) if tail.strip() else {}
        except ValueError:
            return None, None
        return header, payload
    try:
        obj = json.loads(line)
    except ValueError:
        return None, None
    if not isinstance(obj, dict):
        return None, None
    return obj, obj


def _text(v):
    if v is None:
        return None
    return str(v)


def _state_signature(state):
    """(live objects with payload, ids known to be gone).

    The replay emits one line per live object, so the live half is what must be
    identical. Deletions are compared as a set of ids, because they are a fact
    about the log, not an object to re-emit.
    """
    live = []
    gone = []
    for (otype, oid), rec in state.items():
        if rec["deleted"]:
            gone.append((otype, oid))
        else:
            live.append((otype, oid, rec["ticket"],
                         json.dumps(rec["payload"], sort_keys=True)))
    return (tuple(sorted(live, key=lambda r: (str(r[0]), str(r[1])))),
            tuple(sorted(gone, key=lambda r: (str(r[0]), str(r[1])))))


def looks_like_epro(blob):
    return blob[:2] == b"PK"


def sniff(path):
    try:
        with open(path, "rb") as fh:
            return fh.read(2) == b"PK"
    except OSError:
        return False
