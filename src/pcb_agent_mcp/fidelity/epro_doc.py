"""The LCEDA Pro `.epro` archive held with zero loss.

`.epro` is a **ZIP**. Measured on 20 recovery archives in
`Documents/LCEDA-Pro/projects-recovery` (see docs/13):

    project.json                         the project index (same shape KiCad's
                                         project file plays)
    PCB/<uuid>.epcb                      one member per *revision* of a board
    SHEET/<uuid>/<n>.esch                schematic sheets
    SYMBOL/<uuid>.esym                   symbol definitions
    FOOTPRINT/<uuid>.efoo                footprint definitions
    PCB/  SHEET/  ... (0-byte members)   directory markers, no content

Each of those members is a record stream (see `fidelity.lceda`), not a
serialised object graph. One `.epcb` usually holds two documents: a `FOOTPRINT`
document (the library shapes this board uses) followed by a `PCB` document.

What a save looks like, measured by diffing 15 timestamped snapshots of one
project (2025-03-04 .. 2025-03-14):

* an edit produces a **new member with a new uuid** (`PCB/<new-uuid>.epcb`) and
  adds its uuid to `project.json.pcbs`; the previous revision's member is
  byte-for-byte unchanged;
* three consecutive snapshots taken 5 minutes apart, with no edit in between,
  are byte-identical member-for-member (130 of 130).

So `.epro` is a **revision container**, not an append-only log: there is no
ticket line header, no tombstone record, and deleting an object simply means the
line is not in the new member. Anything in this project that used to say
otherwise was a guess and is gone.
"""
import collections
import json
import struct
import zipfile
import zlib

from . import lceda

ZIP_MAGIC = b"PK\x03\x04"
PROJECT_JSON = "project.json"

# Top-level member folders that carry record streams, and the doctype each holds.
STREAM_DIRS = {"PCB": ".epcb", "SHEET": ".esch", "SYMBOL": ".esym",
               "FOOTPRINT": ".efoo"}


# ---------------------------------------------------------------- raw ZIP parts
# A rebuild must reproduce the archive byte for byte, and `zipfile` cannot do
# that: it rewrites `external_attr` (0 -> 0o600<<16) and `extract_version`
# (10 -> 20) on every member it writes. So unchanged members are copied as the
# exact byte ranges they occupied in the source file, and only a changed member
# gets a freshly packed header. Measured on all 194 archives in
# `LCEDA-Pro/projects-recovery`: this reproduces every file byte for byte.

_LOCAL = struct.Struct("<4sHHHHHIIIHH")        # signature .. extra length
_CENTRAL = struct.Struct("<4sHHHHHHIIIHHHHHII")
EOCD_SIG = b"PK\x05\x06"
_CENTRAL_OFFSET_AT = 42                        # byte offset of the local-offset field

def _compress(blob, method):
    if method == 0:
        return blob
    if method == 8:
        co = zlib.compressobj(9, zlib.DEFLATED, -15)
        return co.compress(blob) + co.flush()
    raise EproError("member uses ZIP compression method %d, which this writer "
                    "does not reproduce" % method)

def _raw_parts(path):
    """{name: {local, central, lfields, cfields, lname, lextra, cname, cextra,
    ccomment}} plus the end-of-central-directory record.

    Read straight out of the file rather than from `zipfile`, because the point
    is to keep byte ranges the library would otherwise normalise.
    """
    with open(path, "rb") as fh:
        raw = fh.read()
    i = raw.rfind(EOCD_SIG)
    if i < 0:
        raise EproError("%s has no end-of-central-directory record" % path)
    (_disk, _cd_disk, _n_this, n_total, _cd_size, cd_off,
     _clen) = struct.unpack("<HHHHIIH", raw[i + 4:i + 22])
    if n_total == 0xFFFF or cd_off == 0xFFFFFFFF:
        raise EproError("%s uses ZIP64, which this writer does not handle" % path)
    parts = {}
    p = cd_off
    for _ in range(n_total):
        cf = _CENTRAL.unpack(raw[p:p + _CENTRAL.size])
        nlen, xlen, mlen = cf[10], cf[11], cf[12]
        body = p + _CENTRAL.size
        cname = raw[body:body + nlen]
        cextra = raw[body + nlen:body + nlen + xlen]
        ccomment = raw[body + nlen + xlen:body + nlen + xlen + mlen]
        off = cf[16]
        lf = _LOCAL.unpack(raw[off:off + _LOCAL.size])
        lnlen, lxlen = lf[9], lf[10]
        lname = raw[off + _LOCAL.size:off + _LOCAL.size + lnlen]
        lextra = raw[off + _LOCAL.size + lnlen:off + _LOCAL.size + lnlen + lxlen]
        csize = cf[8]
        parts[cname.decode("utf-8", "surrogateescape")] = {
            "central": raw[p:body + nlen + xlen + mlen],
            "local": raw[off:off + _LOCAL.size + lnlen + lxlen + csize],
            "lfields": lf, "cfields": cf,
            "lname": lname, "lextra": lextra,
            "cname": cname, "cextra": cextra, "ccomment": ccomment,
        }
        p = body + nlen + xlen + mlen
    return parts, raw[i:]

class EproError(ValueError):
    pass


def looks_like_epro(path):
    """True when the file is a ZIP. Content, not extension."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(4)
    except (IOError, OSError):
        return False
    return head[:2] == b"PK"


def sniff(path):
    """Kept for compatibility with the old API: True when this is an `.epro`."""
    if not looks_like_epro(path):
        return False
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
    except (zipfile.BadZipfile, IOError, OSError):
        return False
    return PROJECT_JSON in names or any(n.startswith("PCB/") for n in names)


class EproDocument(object):
    """A whole `.epro` archive: every member, every line, order preserved."""

    def __init__(self, path, entries, order):
        self.path = path
        self.entries = entries            # name -> bytes, decompressed
        self._order = order
        self._members = {}                # name -> lceda.Member
        self._project = None

    # ---------------------------------------------------------- loading
    @classmethod
    def from_path(cls, path):
        try:
            with zipfile.ZipFile(path) as zf:
                order = zf.namelist()
                entries = {n: zf.read(n) for n in order}
        except (zipfile.BadZipfile, IOError, OSError) as exc:
            raise EproError("%s is not a readable ZIP: %s" % (path, exc))
        return cls(path, entries, order)

    # ---------------------------------------------------------- members
    def names(self):
        return list(self._order)

    def entry_bytes(self, name):
        if name not in self.entries:
            raise EproError("no member %r in %s" % (name, self.path))
        return self.entries[name]

    def stream_names(self):
        """Members that hold a record stream (the 0-byte markers are excluded)."""
        return [n for n in self._order if n in self.entries
                and lceda.member_names_with_stream([n])]

    def member(self, name):
        """A `lceda.Member` for one stream member, cached."""
        if name not in self._members:
            if name not in self.entries:
                raise EproError("no member %r in %s" % (name, self.path))
            raw = self.entries[name]
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise EproError("%s is not UTF-8 text: %s" % (name, exc))
            self._members[name] = lceda.Member(name, text)
        return self._members[name]

    def documents(self):
        """[(member name, lceda.Document)] over every stream member."""
        out = []
        for name in self.stream_names():
            for d in self.member(name).documents:
                out.append((name, d))
        return out

    # ---------------------------------------------------------- project index
    def project(self):
        """`project.json` as a dict, or None. Not interpreted beyond structure."""
        if self._project is not None:
            return self._project
        if PROJECT_JSON not in self.entries:
            return None
        raw = self.entries[PROJECT_JSON]
        try:
            self._project = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise EproError("%s/project.json is not valid JSON: %s"
                            % (self.path, exc))
        return self._project

    def boards(self):
        """[{name, pcb_uuid, schematic_uuid}] joined from `boards` + `pcbs`.

        `project.json` keeps two independent maps and only the `boards` entries
        know which pcb belongs to which schematic, so both are needed. A pcb with
        no `boards` entry is still reported (it is a revision nobody named).
        """
        proj = self.project() or {}
        pcbs = proj.get("pcbs") or {}
        boards = proj.get("boards") or {}
        named = {}
        for board_name, ref in boards.items():
            if not isinstance(ref, dict):
                continue
            pcb = ref.get("pcb") or ""
            named[pcb] = {"name": board_name,
                          "pcb_uuid": pcb,
                          "schematic_uuid": ref.get("schematic") or ref.get("sch") or ""}
        out = []
        for uuid, title in pcbs.items():
            ent = named.get(uuid) or {"name": title, "pcb_uuid": uuid,
                                      "schematic_uuid": ""}
            ent["title"] = title
            ent["member"] = "PCB/%s.epcb" % uuid
            ent["in_project_index"] = uuid in named
            out.append(ent)
        return out

    # ---------------------------------------------------------- proofs
    def verify(self):
        """Byte-identical rebuild of every member, plus the serializer check."""
        members = []
        for name in self.stream_names():
            m = self.member(name)
            rt = m.roundtrip()
            ent = {"member": name, "bytes": len(self.entries[name]),
                   "kinds": [d.kind for d in m.documents],
                   "verbatim": rt["verbatim"], "canonical": rt["canonical"],
                   "spaced": rt["spaced"], "indented": rt["indented"],
                   "n_other": rt["n_other"]}
            if rt["n_other"]:
                ent["other"] = rt["other"]
            members.append(ent)
        ok = all(m["verbatim"] and not m["n_other"] for m in members)
        return {"path": self.path, "ok": ok, "members": members,
                "uninterpreted_members": [n for n in self._order
                                          if n not in self.stream_names()]}

    def tag_layout(self):
        """Per (doctype, tag) field counts and position types, from these bytes."""
        merged = collections.defaultdict(lambda: {"count": 0, "arity": collections.Counter(),
                                                  "positions": {}})
        for name in self.stream_names():
            for key, entry in self.member(name).tag_layout().items():
                dst = merged[key]
                dst["count"] += entry["count"]
                dst["arity"].update(entry["arity"])
                for pos, types in entry["positions"].items():
                    dst["positions"].setdefault(pos, collections.Counter()).update(types)
        return {k: {"count": v["count"], "arity": dict(v["arity"]),
                    "positions": {p: dict(c) for p, c in sorted(
                        v["positions"].items(), key=lambda kv: int(kv[0]))}}
                for k, v in merged.items()}

    def stats(self):
        proj = self.project() or {}
        members = collections.Counter()
        for n in self._order:
            base = n.rsplit("/", 1)[-1]
            ext = ("." + base.rsplit(".", 1)[1]) if "." in base else "<directory>"
            members[ext] += 1
        docs = []
        for name in self.stream_names():
            m = self.member(name)
            docs.append({"member": name, "bytes": len(self.entries[name]),
                         "documents": [
                             {"kind": d.kind, "version": d.version,
                              "display_unit": d.display_unit(),
                              "n_lines": len(d.lines),
                              "canvas": d.canvas()}
                             for d in m.documents]})
        return {
            "path": self.path,
            "container": "epro_zip",
            "bytes": sum(len(v) for v in self.entries.values()),
            "n_members": len(self._order),
            "member_kinds": dict(members),
            "config": proj.get("config") or {},
            "boards": self.boards(),
            "documents": docs,
            "coord_unit": "mil",
            "mm_per_coord": lceda.MM_PER_COORD,
        }

    # ---------------------------------------------------------- writing
    def to_bytes(self, member_texts=None):
        """Rebuild the archive, copying every untouched member byte for byte.

        `member_texts` maps member name -> new text (or bytes) for the members
        that changed. Everything else is emitted as the exact byte range it
        occupied in the source file, so `to_bytes()` with no arguments returns
        the input file byte for byte (sha256 equal, checked on all 194 `.epro`
        files in `LCEDA-Pro/projects-recovery`).

        A changed member keeps its name, timestamp, method, flags, version and
        extra fields; only its CRC, sizes and payload are recomputed. Central
        directory offsets are re-patched for the shift, and any member listed in
        `member_texts` that the archive did not already hold is appended - which
        is how the editor itself adds a revision (`PCB/<new-uuid>.epcb`).
        """
        member_texts = member_texts or {}
        parts, eocd = _raw_parts(self.path)

        unknown = [n for n in member_texts if n not in parts]
        order = list(self._order) + [n for n in member_texts if n not in parts]

        out = bytearray()
        centrals = []
        for name in order:
            data = member_texts.get(name, None)
            blob = None
            if name in member_texts:
                blob = (data.encode("utf-8") if isinstance(data, str) else data)
            part = parts.get(name)
            if part is None:
                # A new member: the editor writes these deflated, "2.0", LF.
                template = parts.get(PROJECT_JSON) or next(iter(parts.values()))
                cf = list(template["cfields"])
                idx = order.index(name)
                seed = next((parts[n] for n in order[idx + 1:] if n in parts),
                            template)
                lfields = list(seed["lfields"])
                cfields = list(seed["cfields"])
                lname = cname = name.encode("utf-8")
                lextra = cextra = ccomment = b""
                method = 8
                lfields[3] = method
                cfields[3] = method
                cfields[16] = 0          # local header offset, patched below
                part = {"lfields": lfields, "cfields": cfields,
                        "lname": lname, "cname": cname,
                        "lextra": lextra, "cextra": cextra, "ccomment": ccomment}
            offset = len(out)
            if blob is None:
                local = bytearray(part["local"])
                central = bytearray(part["central"])
            else:
                method = part["lfields"][3]
                comp = _compress(blob, method)
                crc = zlib.crc32(blob) & 0xFFFFFFFF
                lf = list(part["lfields"])
                lf[6], lf[7], lf[8] = crc, len(comp), len(blob)
                lf[9], lf[10] = len(part["lname"]), len(part["lextra"])
                local = bytearray(_LOCAL.pack(*lf) + part["lname"] +
                                  part["lextra"] + comp)
                cf = list(part["cfields"])
                cf[7], cf[8], cf[9] = crc, len(comp), len(blob)
                cf[10], cf[11], cf[12] = (len(part["cname"]), len(part["cextra"]),
                                          len(part["ccomment"]))
                central = bytearray(_CENTRAL.pack(*cf) + part["cname"] +
                                    part["cextra"] + part["ccomment"])
            central[_CENTRAL_OFFSET_AT:_CENTRAL_OFFSET_AT + 4] = struct.pack(
                "<I", offset)
            out += local
            centrals.append(bytes(central))

        cd_off = len(out)
        for c in centrals:
            out += c
        (_disk, _cd_disk, n_this, n_total, _cd_size, _off,
         clen) = struct.unpack("<HHHHIIH", eocd[4:22])
        comment = eocd[22:22 + clen]
        out += struct.pack("<4sHHHHIIH", EOCD_SIG, 0, 0, len(centrals),
                           len(centrals), len(out) - cd_off, cd_off,
                           clen) + comment
        return bytes(out)
