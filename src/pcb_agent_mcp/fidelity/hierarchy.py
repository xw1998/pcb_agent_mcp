"""Sheet hierarchy: which `.kicad_sch` files make up one schematic, and how.

A KiCad schematic is not one file. A `(sheet ...)` node on a page *points at
another file* (`(property "Sheetfile" "ampli_ht.kicad_sch")`), and KiCad resolves
the whole tree before it can answer any question at all - the netlist of the
root contains the components of every page, a symbol inside a reused sub-sheet
appears once per instance with its own reference (`R210` / `R310`), and ERC runs
per sheet path.

That has one consequence for this server that is easy to miss: **an engine run
on a lone copy of one file is not the same question as an engine run on the
project.** Before this module the apply gate copied the file under test into a
temp directory and asked KiCad about it there; for a single-page sheet that is
exactly right, and for a hierarchical one it silently answered about a tree with
its children missing. So:

* `walk()`   reads the tree from the files on disk (page numbers, files, pins,
             cycles, missing children) without touching anything;
* `materialise()` copies the *whole* tree into one temp directory, with the file
  under test replaced by the text that is being asked about, so both sides of a
  before/after diff are answered about the same set of files.

Everything here is read-only. The one writer in this area is `edit/sch_ops.py`.
"""
import os
import shutil
import tempfile

from . import kicad_sch as _ksch
from . import sexpr as S

SHEET_HEAD = "sheet"
MAX_DEPTH = 16

# --------------------------------------------------------------- one sheet
def _same_path(a, b):
    """Do two instance paths name the same page? (`/uuid/uuid`, case-blind)"""
    def norm(x):
        return (x or "").replace("\\", "/").rstrip("/").lower()
    return norm(a) == norm(b)

def _instance_page(node, project=None, instance_path=None):
    """The page number a `(sheet)` node occupies, out of `(instances ...)`.

    KiCad writes one `(path "/<parent-instance-path>" (page "2"))` per place
    the sheet is instantiated in, and the page numbers are **project-wide** (a
    project with 70 pages numbers them 1..70, not 1..5 per sheet - see the
    `vme-wren` demo). A sheet that hangs under a reused sub-sheet therefore
    carries one entry per instance, each with its own page. Which entry is
    *this* instance's is decided by the instance path of the page the sheet sits
    on, so that is the argument that matters; the project name is only the
    fallback for a reader that has no instance path.
    """
    inst = S.child(node, "instances")
    if inst is None:
        return None, None
    pages = []
    for proj in S.children(inst, "project"):
        name = S.arg_text(proj, 1)
        for path in S.children(proj, "path"):
            pages.append({"project": name, "path": S.arg_text(path, 1),
                          "page": S.arg_text(S.child(path, "page"), 1)})
    if not pages:
        return None, None
    if instance_path is not None:
        for p in pages:
            if _same_path(p["path"], instance_path):
                return p["page"], p
    if project is not None:
        for p in pages:
            if p["project"] == project:
                return p["page"], p
    return pages[0]["page"], pages[0]

def sheet_facts(node, project=None, instance_path=None):
    """One `(sheet)` node as facts - never as a guess about the child file.

    `instance_path` is the instance path of the page the node sits on; it picks
    the page number out of the node's `(instances ...)` block when the page is
    instantiated more than once.
    """
    props = {}
    for prop in S.children(node, "property"):
        props[S.arg_text(prop, 1)] = S.arg_text(prop, 2)
    at = S.child(node, "at")
    size = S.child(node, "size")
    page, inst = _instance_page(node, project, instance_path)
    pins = []
    for pin in S.children(node, "pin"):
        a = S.child(pin, "at")
        pins.append({"name": S.arg_text(pin, 1),
                     "shape": S.arg_text(pin, 2),
                     "x": S.arg_num(a, 1), "y": S.arg_num(a, 2),
                     "rotation": S.arg_num(a, 3),
                     "uuid": _id(pin)})
    return {
        "name": props.get("Sheetname"),
        "file": props.get("Sheetfile"),
        "page": page,
        "instance": inst,
        "x": S.arg_num(at, 1), "y": S.arg_num(at, 2),
        "width": S.arg_num(size, 1), "height": S.arg_num(size, 2),
        "uuid": _id(node),
        "fields_autoplaced": S.child(node, "fields_autoplaced") is not None,
        "pins": sorted(pins, key=lambda p: (p["name"] or "")),
        "properties": props,
    }

def sheets(doc, instance_path=None):
    """Every `(sheet)` node on one file, in file order."""
    project = (doc.project() or {}).get("project")
    return [sheet_facts(n, project, instance_path)
            for n in doc.items(SHEET_HEAD)]

def _root_page(doc):
    """The page number of the root page (`sheet_instances (path "/" (page "1")))`)."""
    si = S.child(doc.root(), "sheet_instances")
    if si is None:
        return None
    for path in S.children(si, "path"):
        if (S.arg_text(path, 1) or "/").rstrip("/") in ("", "/"):
            return S.arg_text(S.child(path, "page"), 1)
    return None

def _id(node):
    for name in ("uuid", "tstamp"):
        n = S.child(node, name)
        if n is not None:
            v = S.arg(n, 1)
            if isinstance(v, S.Atom):
                return v.text
    return None

# --------------------------------------------------------------- resolution
def resolve_sheetfile(containing_file, sheetfile, base_dir=None):
    """Where a `Sheetfile` property actually points, and whether it exists.

    KiCad resolves the path **relative to the project directory**; a path that
    is absolute is used as-is. This server does not know the project directory
    when it is handed a single file, so it tries, in order: the directory of the
    file that carries the node, then the root directory of the walk, then the
    absolute path. `resolved_by` says which one answered - a fact about how we
    found the file, not a claim about how KiCad found it.
    """
    if not sheetfile:
        return {"path": None, "exists": False, "resolved_by": "no Sheetfile property"}
    cand = [sheetfile] if os.path.isabs(sheetfile) else []
    if not os.path.isabs(sheetfile):
        cand.append(os.path.join(os.path.dirname(os.path.abspath(containing_file)), sheetfile))
        if base_dir:
            cand.append(os.path.join(base_dir, sheetfile))
    for c in cand:
        if os.path.isfile(c):
            return {"path": os.path.abspath(c), "exists": True,
                    "resolved_by": "absolute" if os.path.isabs(sheetfile) else "relative"}
    first = os.path.abspath(cand[0]) if cand else sheetfile
    return {"path": first, "exists": False,
            "resolved_by": "path tried, file does not exist"}

def walk(path, text=None, max_depth=MAX_DEPTH, edited=None):
    """The sheet tree under `path`, read from disk.

    `text` lets the caller ask the question about a *proposed* version of one
    file - the file named by `edited` (defaults to `path`, i.e. the entry). That
    distinction matters: a plan can edit a CHILD page, and applying its text to
    the root instead would be a silently different question. Everything else is
    read from disk, because a plan edits one file.

    A file used by two sheets is walked twice and reported twice - that is not a
    duplicate, it is one file with two instance paths (and two sets of
    references). A file that appears in its own ancestor chain is a cycle: the
    walk stops there and says so, because KiCad would refuse to open it.
    """
    path = os.path.abspath(path)
    base = os.path.dirname(path)
    issues = []
    seen_files = {}
    edited_real = os.path.normcase(os.path.realpath(edited or path))

    def node_for(this_path, depth, chain, inst_path, page):
        override = None
        if text is not None and os.path.normcase(
                os.path.realpath(this_path)) == edited_real:
            override = text
        try:
            doc = (_ksch.KiCadSchematic.from_text(override, this_path)
                   if override is not None
                   else _ksch.KiCadSchematic.from_path(this_path))
        except Exception as exc:                       # noqa: BLE001
            issues.append({"issue": "unreadable", "file": this_path,
                           "message": "%s: %s" % (type(exc).__name__, exc)})
            return {"file": this_path, "name": os.path.basename(this_path),
                    "page": page, "pins": [], "children": [], "depth": depth,
                    "instance_path": inst_path, "readable": False}
        # the page a sheet node sits on is part of the question: a sheet under a
        # reused sub-sheet has one `(instances ...)` entry per instance, and only
        # the entry that matches this branch's instance path gives its page
        nodes = doc.items(SHEET_HEAD)
        project = (doc.project() or {}).get("project")
        facts = [(n, sheet_facts(n, project, inst_path)) for n in nodes]
        me = {"file": this_path, "name": os.path.basename(this_path),
              "page": page, "pins": [], "children": [], "depth": depth,
              "instance_path": inst_path,
              "readable": True, "sheet_count": len(facts),
              "object_counts": doc.object_count()}
        if len(facts) > 1:
            pages = [f["page"] for _, f in facts if f["page"]]
            if len(set(pages)) != len(pages):
                issues.append({"issue": "duplicate_page", "file": this_path,
                               "pages": pages})
        for _node, f in facts:
            r = resolve_sheetfile(this_path, f["file"], base)
            child_inst = "%s/%s" % ((inst_path or "/").rstrip("/"),
                                    f["uuid"] or "")
            child = {"file": r["path"], "name": f["name"], "page": f["page"],
                     "pins": [p["name"] for p in f["pins"]],
                     "sheet_uuid": f["uuid"], "resolved_by": r["resolved_by"],
                     "instance_path": child_inst,
                     "exists": r["exists"], "children": [], "depth": depth + 1}
            me["children"].append(child)
            if not r["exists"]:
                issues.append({"issue": "missing_file", "file": this_path,
                               "sheet": f["name"], "sheetfile": f["file"],
                               "tried": r["path"]})
                continue
            if depth + 1 > max_depth:
                issues.append({"issue": "too_deep", "file": r["path"],
                               "depth": depth + 1})
                continue
            real = os.path.normcase(os.path.realpath(r["path"]))
            if real in chain:
                issues.append({"issue": "cycle", "file": r["path"],
                               "sheet": f["name"],
                               "chain": [os.path.basename(c) for c in chain]})
                continue
            seen_files[real] = seen_files.get(real, 0) + 1
            sub = node_for(r["path"], depth + 1, chain + (real,),
                           child_inst, f["page"])
            # the sheet-level facts (name, page, pins) stay the sheet's own; only
            # the subtree comes from the walked file - one file used twice is two
            # sheet nodes with two pages, not one node that overwrote itself
            child["children"] = sub.get("children") or []
            child["readable"] = sub.get("readable", True)
            child["sheet_count"] = sub.get("sheet_count")
            child["object_counts"] = sub.get("object_counts")
        return me

    real_root = os.path.normcase(os.path.realpath(path))
    # the root page's own identity: its instance path is "/<file uuid>" (that is
    # what every child sheet writes as its parent path) and its page number lives
    # in `sheet_instances`, not in a `(sheet)` node
    root_inst, root_page = "/", None
    try:
        # `text` belongs to ONE file (`edited`); reading the root's identity out
        # of it when the root is a different file is how the instance paths of
        # the whole tree become someone else's uuids
        root_text = text if real_root == edited_real else None
        doc_root = (_ksch.KiCadSchematic.from_text(root_text, path)
                    if root_text is not None
                    else _ksch.KiCadSchematic.from_path(path))
        root_inst = "/" + (doc_root.uuid() or "")
        root_page = _root_page(doc_root)
    except Exception:                                  # noqa: BLE001
        pass                                           # node_for reports it
    tree = node_for(path, 0, (real_root,), root_inst, root_page)
    seen_files.setdefault(real_root, 0)
    seen_files[real_root] += 1
    return {
        "root": path,
        "tree": tree,
        "issues": issues,
        "files": [{"file": f, "used_by_sheets": n} for f, n in sorted(seen_files.items())],
        "file_count": len(seen_files),
        "sheet_count": _count_sheets(tree),
        "max_depth": _depth(tree),
    }

def _count_sheets(node):
    return len(node.get("children") or []) + sum(_count_sheets(c)
                                                for c in node.get("children") or [])

def _depth(node):
    children = node.get("children") or []
    return 0 if not children else 1 + max(_depth(c) for c in children)

def flat(tree):
    """Every node of a tree in visit order (root first)."""
    out = [tree]
    for c in tree.get("children") or []:
        out.extend(flat(c))
    return out

def _pages_of(tree):
    """(sheet name, page) for every sheet node in a tree, in visit order."""
    out = []
    for node in flat(tree)[1:]:
        out.append((node.get("name"), node.get("page")))
    return out

def instance_paths(path, max_depth=MAX_DEPTH):
    """Where one file lives inside its project, and which pages it holds.

    A page number is **project-wide** and every sheet node carries one entry per
    place it is instantiated (`vme-wren` numbers its 70-odd pages 1..73, and a
    sub-sheet used four times holds four entries). Two consequences this returns
    for a caller that wants to add a sheet:

    * `instances` - one entry per instance of this file, each with the instance
      path a new `(path ... (page ...))` entry has to name. Writing only the
      first one is how a sheet ends up with the same page number in every
      instance;
    * `pages` / `max_page` - every page number already in use in the project, so
      a new page can be picked to collide with nothing.

    The walk is read-only and starts from the top of the hierarchy, because the
    page numbers of a child are only meaningful as part of the whole project.
    """
    path = os.path.abspath(path)
    root = find_root(path)
    tree = walk(root["root"], max_depth=max_depth)
    real = os.path.normcase(os.path.realpath(path))
    instances = []
    for node in flat(tree["tree"]):
        try:
            same = os.path.normcase(os.path.realpath(node.get("file") or "")) == real
        except OSError:
            same = False
        if same:
            instances.append({"path": node.get("instance_path"),
                              "page": node.get("page"),
                              "depth": node.get("depth")})
    pages = [p for _, p in _pages_of(tree["tree"]) if p]
    numbers = []
    for p in pages:
        try:
            numbers.append(int(p))
        except (TypeError, ValueError):
            pass
    return {
        "file": path,
        "root": root["root"],
        "root_source": root["source"],
        "is_root": os.path.normcase(os.path.realpath(root["root"])) == real,
        "instances": instances,
        "pages": pages,
        "max_page": max(numbers) if numbers else 0,
        "issues": tree["issues"],
        "ambiguous": root["source"].startswith("ambiguous"),
    }

# --------------------------------------------------------------- who is root
def parents_of(path, limit=200):
    """Sibling schematics that name this file as a `Sheetfile`.

    This is a **scan**, not knowledge: KiCad's `.kicad_pro` does not record
    which sheet is the root, so the only evidence is another file pointing at
    this one. Zero candidates means "nothing points at it" (usually the root);
    two means the file is used by two different projects in the same folder and
    this server will not pick one.
    """
    path = os.path.abspath(path)
    base = os.path.basename(path)
    folder = os.path.dirname(path)
    out = []
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return out
    for name in names[:limit]:
        if not name.lower().endswith(".kicad_sch") or name == base:
            continue
        full = os.path.join(folder, name)
        try:
            with open(full, "r", encoding="utf-8", errors="surrogateescape") as fh:
                text = fh.read()
        except OSError:
            continue
        if '"Sheetfile"' not in text:
            continue
        try:
            doc = _ksch.KiCadSchematic.from_text(text, full)
        except Exception:                              # noqa: BLE001
            continue
        for f in sheets(doc):
            if os.path.basename((f["file"] or "").replace("\\", "/")) == base:
                out.append({"file": full, "sheet": f["name"], "page": f["page"]})
                break
    return out

def find_root(path, max_depth=MAX_DEPTH):
    """The top of the hierarchy `path` belongs to, and how that was decided."""
    path = os.path.abspath(path)
    chain = []
    cur = path
    for _ in range(max_depth):
        parents = parents_of(cur)
        if not parents:
            return {"root": cur, "source": "no sibling names this file as a Sheetfile"
                                          if cur == path else
                                          "walked up %d level(s) of Sheetfile references"
                                          % len(chain),
                    "candidates": [], "walked": chain}
        if len(parents) > 1:
            return {"root": cur,
                    "source": "ambiguous: %d siblings name this file; refusing to "
                              "pick one" % len(parents),
                    "candidates": parents, "walked": chain}
        chain.append(cur)
        cur = parents[0]["file"]
    return {"root": cur, "source": "max depth reached", "candidates": [],
            "walked": chain}

# --------------------------------------------------------------- materialise
def materialise(path, text=None, root=None, max_depth=MAX_DEPTH):
    """Copy the whole hierarchy into one temp directory, for an engine run.

    The engine (kicad-cli) resolves `Sheetfile` relative to the project
    directory, so a lone file in a temp directory answers a *different* question
    than the project does. This copies every reachable `.kicad_sch` into a temp
    tree that keeps the same relative layout, and - when `text` is given -
    replaces the entry file with that text. Nothing is written outside the temp
    directory, and nothing in the project is touched.

    A child that resolves outside the entry file's directory is **not** copied
    (copying it would invent a layout); it is listed in `skipped` and the
    netlist gate is told that the tree it saw was partial.
    """
    here = os.path.dirname(os.path.abspath(path))
    # The engine resolves `Sheetfile` downward from whatever it is given, so
    # copying a *child* file on its own would answer about that child and not
    # about the project it lives in. The default entry is therefore the project
    # root (found by walking Sheetfile references up), and `text`/`path` still
    # name which file inside the copy gets replaced. The copy keeps the relative
    # layout, so the base is the folder both files sit in (or below).
    entry = root or find_root(os.path.abspath(path))["root"]
    base = os.path.commonpath([os.path.dirname(entry), here])
    tmp = tempfile.mkdtemp(prefix="pcb_agent_hier_")
    state = {"dir": tmp, "entry": None, "files": [], "skipped": [],
             "missing": [], "outside": [], "edited": entry}
    edited = os.path.normcase(os.path.realpath(path))
    done = {}

    def put(src, override):
        rel = os.path.relpath(src, base)
        if rel.startswith(".."):
            state["outside"].append(src)
            return None
        dst = os.path.join(tmp, rel)
        d = os.path.dirname(dst)
        if d and not os.path.isdir(d):
            os.makedirs(d)
        if override is not None:
            data = override.encode("utf-8", "surrogateescape")
        else:
            with open(src, "rb") as fh:
                data = fh.read()
        with open(dst, "wb") as fh:
            fh.write(data)
        state["files"].append({"file": src, "copy": dst, "bytes": len(data),
                               "replaced": override is not None})
        return dst

    def visit(src, override, chain):
        real = os.path.normcase(os.path.realpath(src))
        if real == edited:
            override = text
        if real in done:
            # one file with two sheet instances is copied once; walking it twice
            # would copy the same bytes again under the same name
            state["skipped"].append({"file": src, "why": "already copied (the file "
                                                        "is used by more than one sheet)"})
            return
        done[real] = True
        dst = put(src, override)
        if dst is None:
            return
        if override is not None:
            doc = _ksch.KiCadSchematic.from_text(override, src)
        else:
            doc = _ksch.KiCadSchematic.from_path(src)
        for f in sheets(doc):
            r = resolve_sheetfile(src, f["file"], base)
            if not r["exists"]:
                state["missing"].append({"sheet": f["name"], "sheetfile": f["file"],
                                         "from": src})
                continue
            real = os.path.normcase(os.path.realpath(r["path"]))
            if real in chain or len(chain) > max_depth:
                state["skipped"].append({"sheet": f["name"], "file": r["path"],
                                         "why": "cycle" if real in chain
                                                else "too deep"})
                continue
            visit(r["path"], None, chain + (real,))

    real_entry = os.path.normcase(os.path.realpath(entry))
    visit(entry, text if os.path.normcase(os.path.realpath(path)) == real_entry
          else None, (real_entry,))
    state["entry"] = os.path.join(tmp, os.path.relpath(entry, base))
    return state

def cleanup(state):
    """Remove a materialised tree (`materialise` creates one per engine call)."""
    if state and state.get("dir"):
        shutil.rmtree(state["dir"], ignore_errors=True)

# --------------------------------------------------------------- reporting
def describe(path, text=None, max_depth=MAX_DEPTH):
    """The read-only answer: the tree, its files, its pages and its problems."""
    path = os.path.abspath(path)
    root = find_root(path)
    entry = root["root"]
    tree = walk(entry, text=text, max_depth=max_depth, edited=path)
    pages = [{"sheet": n.get("name"), "file": os.path.basename(n.get("file") or ""),
              "page": n.get("page"), "pins": n.get("pins"),
              "depth": n.get("depth"), "exists": n.get("exists", True)}
             for n in flat(tree["tree"])[1:]]
    seen = {}
    for p in pages:
        if p["page"]:
            seen.setdefault(p["page"], []).append(p["sheet"])
    duplicates = {k: v for k, v in seen.items() if len(v) > 1}
    return {
        "entry": path,
        "root": entry,
        "root_source": root["source"],
        "root_candidates": root["candidates"],
        "sheets": tree["sheet_count"],
        "files": tree["files"],
        "file_count": tree["file_count"],
        "depth": tree["max_depth"],
        "pages": pages,
        "duplicate_pages": duplicates,
        "issues": tree["issues"],
        "notes": ["page numbers come from each sheet's (instances ...) node; a "
                  "sheet used twice has two instance paths and therefore two "
                  "pages",
                  "a sheet whose Sheetfile is missing is listed as an issue - the "
                  "netlist of the project cannot be computed without it"],
        "read_only": "this call parses and measures; it never writes",
    }
