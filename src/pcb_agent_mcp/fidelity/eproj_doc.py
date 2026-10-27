"""The LCEDA Pro project database (`.eprj` / `.eprj2`) held with zero loss.

The working format is not a ZIP and not a text log: it is **SQLite**
(header `SQLite format 3\\0`). Measured on the example projects shipped with the
editor (see docs/13):

    documents(uuid, title, display_title, description, docType, dataStr,
              sheet_id, ticket, sort_ticket, created_at, updated_at,
              creator_uuid, schematic_uuid, project_uuid, image[, parent_uuid])
        docType 1 = schematic sheet, 3 = PCB.  `dataStr` is the *same* line-per-
        record stream a `.epcb` member holds, so `fidelity.lceda` reads it.
    coppers(path, project_uuid, dataStr, ticket)
        path is `<pcb_uuid>_<shard>` and shard is `0`, `e2001`, ... ; holds the
        `POURED` (poured copper fill) document.
    texts(path, project_uuid, dataStr, ticket)
        same path convention; holds the `FONT` document (glyph outlines).
    components(uuid, ..., docType, dataStr, ...)   docType 2 = library symbol /
        footprint definitions used by this project.
    resources(hash, dataStr, filename, owner_uuid, ticket)  binary blobs as
        `data:image/png;base64,...`.
    projects(uuid, name, boards, ticket, g_ticket)   `boards` is a JSON array of
        {"sch": uuid, "name": str, "pcb": uuid}.
    schematics, project_structures, branches, history_data, db_versions(key,value).

Facts that constrain the writer:

* The column set is **not** fixed across versions: `.eprj` (db_versions
  `0.0.3`) has no `documents.parent_uuid`, `.eprj2` (`25.10.31.1` /
  `26.08.19.1`) does. So every statement here builds its column list from
  `PRAGMA table_info`, and a missing expected column is reported, not assumed.
* `ticket` on a document is 3 for every document in every sample, and
  `projects.g_ticket` is a small integer (13, 17, 53, 2825). So `ticket` is a
  synchronisation counter, **not** a per-save sequence number. A writer cannot
  learn the application's bump rule without driving the GUI, so anything that
  bumps a ticket is reported as a declared convention with
  `verified_by_app: false`.
"""
import contextlib
import json
import os
import sqlite3

from . import lceda

SQLITE_HEADER = b"SQLite format 3\x00"

# docType values seen in real projects.
DOC_SCH = 1
DOC_LIBRARY = 2
DOC_PCB = 3


class EprjError(ValueError):
    pass


def looks_like_eproj(path):
    """True when the file starts with the SQLite magic. Content, not extension."""
    try:
        with open(path, "rb") as fh:
            return fh.read(len(SQLITE_HEADER)) == SQLITE_HEADER
    except (IOError, OSError):
        return False


def _uri(path, mode):
    return "file:%s?mode=%s" % (os.path.abspath(path).replace("\\", "/"), mode)


class EprjDocument(object):
    """Read-only view of a project database."""

    def __init__(self, path):
        self.path = path
        self._con = None
        self._cache = {}

    # ---------------------------------------------------------- opening
    @classmethod
    def from_path(cls, path):
        if not looks_like_eproj(path):
            raise EprjError("%s is not an LCEDA project database (no SQLite header)"
                            % path)
        doc = cls(path)
        doc._columns()          # forces the file open, fails loudly if unreadable
        return doc

    def close(self):
        if self._con is not None:
            self._con.close()
            self._con = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    @contextlib.contextmanager
    def read_only(self):
        """A read-only connection. Never writes, never creates - `mode=ro`."""
        con = sqlite3.connect(_uri(self.path, "ro"), uri=True)
        try:
            yield con
        finally:
            con.close()

    def _ro(self):
        if self._con is None:
            self._con = sqlite3.connect(_uri(self.path, "ro"), uri=True)
        return self._con

    def _query(self, sql, args=()):
        cur = self._ro().cursor()
        cur.execute(sql, args)
        return cur.fetchall()

    def _columns(self, table="documents"):
        cols = [r[1] for r in self._query("PRAGMA table_info(%s)" % table)]
        if not cols:
            raise EprjError("%s has no `%s` table - not an LCEDA project database"
                            % (self.path, table))
        return cols

    def tables(self):
        return [r[0] for r in self._query(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]

    # ---------------------------------------------------------- metadata
    def db_versions(self):
        if "db_versions" not in self.tables():
            return {}
        return dict(self._query("SELECT key, value FROM db_versions"))

    def project(self):
        """The single `projects` row: uuid, name, board count, tickets."""
        cols = self._columns("projects")
        row = self._query("SELECT %s FROM projects%s"
                          % (",".join(cols), " LIMIT 1"))[0] if \
            self._query("SELECT count(*) FROM projects")[0][0] else None
        if row is None:
            return None
        rec = dict(zip(cols, row))
        try:
            rec["boards_declared"] = json.loads(rec.get("boards") or "{}")
        except ValueError:
            rec["boards_declared"] = None
        return rec

    def boards(self):
        """[{name, pcb_uuid, sch_uuid}] from `projects.boards` + the documents.

        Falls back to "every PCB document is a board" when `boards` is `{}`,
        because several sample projects ship an empty boards array while still
        holding PCB documents.
        """
        proj = self.project() or {}
        declared = proj.get("boards_declared")
        out = []
        if isinstance(declared, list) and declared:
            for b in declared:
                if not isinstance(b, dict):
                    continue
                out.append({"name": b.get("name") or "",
                            "pcb_uuid": b.get("pcb") or "",
                            "sch_uuid": b.get("sch") or "",
                            "source": "projects.boards"})
            return out
        for d in self.board_documents():
            out.append({"name": d["title"], "pcb_uuid": d["uuid"],
                        "sch_uuid": d.get("schematic_uuid") or "",
                        "source": "documents(docType=3)"})
        return out

    # ---------------------------------------------------------- documents
    def documents(self, doc_type=None):
        cols = self._columns("documents")
        keep = [c for c in ("uuid", "title", "display_title", "docType", "ticket",
                            "sort_ticket", "schematic_uuid", "parent_uuid",
                            "project_uuid", "updated_at")
                if c in cols]
        sql = "SELECT %s FROM documents" % ",".join(keep)
        args = ()
        if doc_type is not None:
            sql += " WHERE docType=?"
            args = (doc_type,)
        return [dict(zip(keep, r)) for r in self._query(sql, args)]

    def board_documents(self):
        return self.documents(DOC_PCB)

    def document_text(self, uuid):
        """The record stream of one document, as text (never None silently)."""
        rows = self._query("SELECT dataStr FROM documents WHERE uuid=?", (uuid,))
        if not rows:
            raise EprjError("no document with uuid %s" % uuid)
        text = rows[0][0]
        if text is None:
            raise EprjError("document %s has a NULL dataStr" % uuid)
        return text

    def document_member(self, uuid):
        """A `lceda.Member` for one document, cached."""
        key = ("doc", uuid)
        if key not in self._cache:
            self._cache[key] = lceda.Member(uuid, self.document_text(uuid))
        return self._cache[key]

    #: Alias so the adapter can drive a ZIP container and a SQLite container
    #: through one call (`container.member(name)`): a document uuid is the
    #: SQLite equivalent of an `.epro` member name.
    member = document_member

    # ---------------------------------------------------------- side payloads
    def side_payloads(self, pcb_uuid):
        """{table: [{path, text, ticket}]} for coppers/texts of one PCB.

        The path is `<uuid>_<shard>`, and a board may have several shards, so the
        match is a prefix test on `"<uuid>_"` rather than an equality on
        `"<uuid>_0"`.
        """
        out = {}
        for table in ("coppers", "texts"):
            if table not in self.tables():
                out[table] = []
                continue
            rows = self._query(
                "SELECT path, dataStr, ticket FROM %s WHERE path LIKE ?" % table,
                ("%s\\_%%" % pcb_uuid,))
            out[table] = [{"path": r[0], "text": r[1], "ticket": r[2]}
                          for r in rows if r[1] is not None]
        return out

    def library_rows(self):
        """Every `components` row that carries a record stream (never filtered).

        `docType` is not a reliable type tag across versions - in the samples
        `4` is a FOOTPRINT and `2/18/19/20` are SYMBOL documents - so the rows
        are handed over as they are and the caller splits them on the document's
        own `DOCTYPE` line.
        """
        cols = self._columns("components")
        if "dataStr" not in cols:
            return []
        rows = self._query('SELECT uuid, title, docType, dataStr FROM components')
        return [{"uuid": r[0], "title": r[1], "doc_type": r[2], "text": r[3]}
                for r in rows if r[3]]

    def table_titles(self, table):
        """[{uuid, title}] for a title-only table (`devices`, `components`)."""
        cols = self._columns(table)
        if "uuid" not in cols or "title" not in cols:
            return []
        rows = self._query('SELECT uuid, title FROM "%s"' % table)
        return [{"uuid": r[0], "title": r[1]} for r in rows if r[1]]

    def library_members(self):
        """docType 2 rows from `components`: symbol/footprint definitions."""
        cols = self._columns("components")
        if "docType" not in cols or "dataStr" not in cols:
            return []
        rows = self._query("SELECT uuid, title, dataStr FROM components "
                           "WHERE docType=2")
        return [{"uuid": r[0], "title": r[1], "text": r[2]} for r in rows
                if r[2] is not None]

    # ---------------------------------------------------------- proofs
    def verify(self, uuids=None):
        """Re-parse every document and prove the stream is byte-reproducible.

        This does not modify the file. It answers "can this project be read
        without loss", which is the precondition for being allowed to write it.
        """
        docs = []
        targets = uuids or [d["uuid"] for d in self.documents()]
        for uuid in targets:
            ent = {"uuid": uuid}
            try:
                m = self.document_member(uuid)
            except lceda.LcedaError as exc:
                ent["error"] = str(exc)
                docs.append(ent)
                continue
            rt = m.roundtrip()
            ent.update({"kinds": [d.kind for d in m.documents],
                        "n_lines": len(m.lines),
                        "verbatim": rt["verbatim"],
                        "canonical": rt["canonical"],
                        "indented": rt["indented"],
                        "n_other": rt["n_other"]})
            if rt["n_other"]:
                ent["other"] = rt["other"]
            docs.append(ent)
        ok = all(d.get("verbatim") and not d.get("n_other") for d in docs)
        return {"path": self.path, "ok": ok, "documents": docs}

    def integrity_check(self):
        return self._query("PRAGMA integrity_check")[0][0]

    def stats(self):
        proj = self.project() or {}
        out = {
            "path": self.path,
            "container": "sqlite",
            "bytes": os.path.getsize(self.path),
            "db_versions": self.db_versions(),
            "project": {k: proj.get(k) for k in ("uuid", "name", "ticket", "g_ticket")},
            "boards": self.boards(),
            "n_tables": len(self.tables()),
            "documents": [],
            "coord_unit": "mil",
            "mm_per_coord": lceda.MM_PER_COORD,
        }
        for d in self.documents():
            ent = dict(d)
            try:
                ent["bytes"] = len(self.document_text(d["uuid"]).encode("utf-8"))
            except EprjError as exc:
                ent["error"] = str(exc)
            out["documents"].append(ent)
        for b in out["boards"]:
            if b["pcb_uuid"]:
                side = self.side_payloads(b["pcb_uuid"])
                b["side_payloads"] = {k: [{"path": x["path"], "bytes": len(x["text"])}
                                          for x in v] for k, v in side.items()}
        return out
