"""Fidelity layer: the PCB, converted - not summarised, not inferred.

Layer contract (this is the correction that matters):

    The middle representation must BE the board. Every node of the source
    document is carried, including nodes nobody has written a rule for. Facts
    the tool reports are read out of that carried tree; facts the board does not
    store are taken from the vendor engine (KiCad) or declared absent. Nothing is
    recomputed from a lossy model, nothing is defaulted.

Modules:

    sexpr      lossless S-expression reader/writer (atom spelling, quoting kept)
    kicad_doc  a whole `.kicad_pcb` as a tree + round-trip proof + coverage report
    lceda      one record stream (`.epcb`/`.efoo`/`.esch`) as lines that keep bytes
    epro_doc   a whole `.epro`: the ZIP, member by member, byte-repacking exactly
    eproj_doc  a whole `.eprj`/`.eprj2`: the SQLite project (documents/coppers)
    views      read facts out of the tree; every record keeps its `raw` node
    engine     ask kicad-cli (DRC / stats / Gerbers / positions) - the referee

Proofs, because "lossless" is a claim that needs payment:

    KiCadDocument.verify()   signature(parse(dumps(tree))) == signature(tree)
    EproDocument.verify()    byte identity per member + resolved-state idempotence
    engine.verify(doc)       KiCad plots both files; copper, stats and positions equal
"""
from . import engine, epro_doc, eproj_doc, kicad_doc, lceda, sexpr, views

__all__ = ["engine", "epro_doc", "eproj_doc", "kicad_doc", "lceda", "sexpr",
           "views", "sniff", "load", "FidelityError"]


class FidelityError(ValueError):
    pass


def sniff(path):
    """'kicad_pcb' | 'epro' | 'unknown' - by content, not by extension."""
    if epro_doc.sniff(path):
        return "epro"
    if kicad_doc.sniff(path):
        return "kicad_pcb"
    return "unknown"


def load(path, fmt=None):
    """Open a board as a fidelity document. Never writes, never creates."""
    fmt = fmt or sniff(path)
    if fmt == "kicad_pcb":
        return kicad_doc.KiCadDocument.from_path(path)
    if fmt == "epro":
        return epro_doc.EproDocument.from_path(path)
    raise FidelityError("unrecognised board container: %s" % path)
