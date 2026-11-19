"""Where the shipped JSON tables live, and how a miss becomes a named error.

Eleven tables travel with this server - the safety tables, the IPC charts, the
powder-core catalog, the rule ledger and the experience seed. They are *data*,
not code, and they sit in a `data/` directory next to the package rather than
inside it, because every document in `docs/` names them by that path and because
a table you can open in an editor is a table you can check against the page it
was transcribed from.

That placement has one consequence worth spelling out: the directory is not
importable, so *where* it is depends on how the server was installed.

    git clone            <repo>/data/                       (docs/ talks about this one)
    wheel with data      <sys.prefix>/share/pcb_agent_mcp/data/
    pip install -e .     <repo>/data/                       (editable: nothing is copied)
    tables copied in     <package>/data/                    (the package's own directory)

`find()` walks those in a fixed order and returns the first file that actually
exists, so an install layout can never make a tool read the wrong table. A file
that no candidate carries comes back as `None` from `find()` - the caller decides
what to say - and `require()` turns it into a `MissingTable` whose message names
every place that was searched. Nothing here ever returns an empty table: a
missing table that reads as "the table says nothing" is exactly the kind of
silent wrong answer this project refuses.
"""
import os
import sys

#: Every table that must travel with the server. One list so a new table cannot
#: be added without the packaging and the diagnostics noticing; a test compares it
#: against the directory itself.
TABLES = (
    "creepage_tables.json",
    "iec60664_tables.json",
    "ipc2152_tables.json",
    "magnetic_materials.json",
    "layout-rule-sources.json",
    "material_loss_anchors.json",
    "powder_cores.json",
    "powder_curve_fits.json",
    "toroid_mlt_reference.json",
    "dc_bias_fit.json",
    "experience_seed.json",
)


def package_dir():
    """The directory this module was imported from."""
    return os.path.dirname(os.path.abspath(__file__))


def repo_data_dir():
    """`<repo>/data` for a source checkout: the path docs/ names."""
    return os.path.normpath(os.path.join(package_dir(), os.pardir, os.pardir,
                                         "data"))


def candidates():
    """Every directory a table may live in, most specific first.

    `PCB_AGENT_DATA` wins when it is set: an operator pointing the server at a
    corrected table must not be overruled by a stale copy in the checkout.
    """
    out = []
    env = os.environ.get("PCB_AGENT_DATA")
    if env:
        out.append(os.path.abspath(os.path.expanduser(env)))
    out.append(repo_data_dir())
    out.append(os.path.join(package_dir(), "data"))
    for prefix in (getattr(sys, "prefix", None), getattr(sys, "base_prefix", None)):
        if prefix:
            out.append(os.path.join(prefix, "share", "pcb_agent_mcp", "data"))
    seen, uniq = set(), []
    for path in out:
        key = os.path.normcase(os.path.normpath(path))
        if key not in seen:
            seen.add(key)
            uniq.append(os.path.normpath(path))
    return uniq


def find(name):
    """Absolute path of the first candidate that carries `name`, else None."""
    for folder in candidates():
        path = os.path.join(folder, name)
        if os.path.isfile(path):
            return path
    return None


class MissingTable(IOError):
    """A shipped table is not on this machine, and the message says where we looked."""

    def __init__(self, name, searched=None):
        self.name = name
        self.searched = list(searched if searched is not None else candidates())
        IOError.__init__(self, self.message())

    def message(self):
        return ("the shipped data table %r was not found. Searched: %s. "
                "A git clone carries it in <repo>/data/; a wheel installs it under "
                "<prefix>/share/pcb_agent_mcp/data/. Set PCB_AGENT_DATA to the "
                "directory that holds it if it lives somewhere else. This is a "
                "missing file, not an empty table: nothing is answered from it."
                % (self.name, "; ".join(self.searched)))


def require(name):
    """Like find(), but a miss raises MissingTable with every path searched."""
    path = find(name)
    if path is None:
        raise MissingTable(name)
    return path


def resolve(name, default_dir=None):
    """A path string for module-level constants that must not raise at import.

    Returns the found path, or `<default_dir>/<name>` when nothing was found.
    The fallback is a *path*, not an opening: the loader that consumes it still
    fails closed on a file that is not there, and `report()` says so up front.
    """
    found = find(name)
    if found is not None:
        return found
    folder = default_dir if default_dir is not None else repo_data_dir()
    return os.path.join(folder, name)


def report():
    """What is on disk right now: the directory used, and what is missing.

    Read-only and cheap (a stat per candidate). Tools that claim "call me first"
    put this in their answer so "are the tables there" is a fact, not an
    assumption.
    """
    folders = candidates()
    found, missing = {}, []
    for name in TABLES:
        path = find(name)
        if path is None:
            missing.append(name)
        else:
            found[name] = path
    used = []
    for path in found.values():
        folder = os.path.dirname(path)
        if folder not in used:
            used.append(folder)
    return {
        "searched": folders,
        "dirs_used": used,
        "found": len(found),
        "missing": missing,
        "override": os.environ.get("PCB_AGENT_DATA") or None,
    }
