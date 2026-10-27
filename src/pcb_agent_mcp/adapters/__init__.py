"""Adapters: one per file format, all landing in `canonical.Board`.

Dispatch is by content, not by extension - a `.epro` renamed to `.kicad_pcb` or a
project exported with the wrong suffix must not silently parse as the wrong kind.
"""
import zipfile

from . import canonical, epro, kicad_pcb
from ..fidelity import eproj_doc

FORMATS = {
    "kicad_pcb": {
        "extensions": [".kicad_pcb"],
        "kind": "file",
        "writable": False,
        "facts": ["outline", "cutouts", "footprints", "pads", "tracks", "vias",
                  "zones", "nets", "stackup (when Board Setup was used)"],
        "not_carried_in_format": ["clearance matrix (in .kicad_pro)",
                                  "net classes (in .kicad_pro)",
                                  "custom DRC rules (in .kicad_dru, text only)"],
    },
    "easyeda_epro": {
        "extensions": [".epro", ".eprj", ".eprj2"],
        "kind": "zip container (.epro) or sqlite project (.eprj/.eprj2)",
        "writable": False,
        "facts": ["footprints", "pads (shape, hole, corner radius, polygon)",
                  "tracks", "vias", "nets", "pours and fills",
                  "keepout regions", "rules", "stackup", "texts",
                  "library footprint definitions"],
        "not_carried_in_format": ["per-pad mask/paste expansion (mapping unverified)",
                                  "3D bodies (SHELL/BOSS records, not evaluated)"],
        "caveats": ["record stream: one JSON array per line, documents split by DOCTYPE",
                    "coordinates and sizes are mil (0.0254 mm); CANVAS/RULE unit "
                    "labels are display units only",
                    "y is not inverted in the file",
                    "a hand-drawn board edge may not close: reported as open chains"],
    },
}


def detect_format(path):
    """Sniff the real format. Returns 'kicad_pcb' | 'easyeda_epro' | 'unknown'."""
    try:
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as zf:
                if any(n.lower().endswith(".epcb") for n in zf.namelist()):
                    return "easyeda_epro"
            return "unknown"
    except (OSError, zipfile.BadZipFile):
        return "unknown"
    try:
        with open(path, "rb") as fh:
            head = fh.read(4096).lstrip()
    except OSError:
        return "unknown"
    if head.startswith(b"(kicad_pcb"):
        return "kicad_pcb"
    if eproj_doc.looks_like_eproj(path):
        return "easyeda_epro"      # same format, sqlite container
    return "unknown"


def open_board(path, member=None, project=None):
    """Parse any supported board file into the canonical model. Never writes."""
    fmt = detect_format(path)
    if fmt == "kicad_pcb":
        return kicad_pcb.parse_board(path, project_path=project)
    if fmt == "easyeda_epro":
        return epro.parse_board(path, member=member)
    raise ValueError(
        "unsupported board file: %s (sniffed as %r). Supported: %s"
        % (path, fmt, ", ".join(sorted(FORMATS))))
