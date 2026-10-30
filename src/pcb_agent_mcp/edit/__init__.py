"""The write path.

Two very different file formats, one protocol:

    create_board(path, spec)          a brand-new KiCad board, gated before you get it
    propose(path, operations)         compute a change; writes nothing (both formats)
    apply_plan(plan_id, confirm)      checkpoint -> atomic write -> the gates
    list_checkpoints / restore_checkpoint / find_checkpoint

The format is decided by *content*, never by the file name: `PK` is an `.epro`
(a ZIP container whose member ``PCB/<uuid>.epcb`` holds the board's record
stream), `SQLite format 3`` is an `.eprj`/`.eprj2` project (read-only here) and
`(kicad_pcb` / `(kicad_sch` is KiCad. A rename therefore cannot smuggle one
format in as another.

Nothing in here ever deletes a file, and nothing ever writes beside a project
except the same-volume temp file inside `fsutil.atomic_write`, which lives for
milliseconds and is cleaned up in `finally`.

Import surface is kept deliberately small: `describe_ops()` / `describe_epro_ops()`
is what the MCP tool layer advertises, so the operation list and the
implementation cannot drift.
"""
import os

from . import build
from . import checks
from . import epro_edit
from . import fsutil
from . import ops
from . import plan
from . import sch_ops
from . import sch_plan

from .ops import OpError
from .plan import EditError
from .plan import apply_steps
from .plan import checkpoint_root
from .plan import create_board
from .plan import find_checkpoint
from .plan import list_checkpoints
from .plan import restore_checkpoint
from .plan import save_checkpoint
from .plan import step_control
from .plan import step_events
from .plan import steps_root

__all__ = [
    "build", "checks", "epro_edit", "fsutil", "ops", "plan", "sch_ops", "sch_plan",
    "EditError", "OpError", "detect_format",
    "apply_plan", "apply_steps", "checkpoint_root", "create_board", "describe_ops",
    "describe_epro_ops", "describe_sch_ops", "find_checkpoint",
    "list_checkpoints", "propose", "propose_schematic", "restore_checkpoint",
    "new_schematic_file", "save_checkpoint", "schematic_hierarchy",
    "schematic_summary", "schematic_view",
    "step_control", "step_events", "steps_root",
]


def describe_ops():
    """The KiCad operation catalogue as the MCP tool layer sees it."""
    return ops.describe()


def describe_epro_ops():
    """The `.epro` operation catalogue (record-stream operations)."""
    return epro_edit.describe()

def describe_sch_ops():
    """The KiCad *schematic* operation catalogue."""
    return sch_ops.describe()

def propose_schematic(path, operations, options=None):
    """Plan a `.kicad_sch` change. Writes nothing."""
    return sch_plan.propose(path, operations, options)

def schematic_summary(path, engine=True):
    return sch_plan.summary(path, engine=engine)

def schematic_view(path, kind="summary", limit=200):
    return sch_plan.view(path, kind=kind, limit=limit)

def schematic_hierarchy(path, text=None):
    """Read-only: the sheet tree this file belongs to (pages, files, issues)."""
    return sch_plan.hierarchy(path, text=text)

def new_schematic_file(path, paper="A4", title=None, confirm=False):
    """Create an empty `.kicad_sch` page. Refuses to touch anything existing."""
    return sch_plan.new_file(path, paper=paper, title=title, confirm=confirm)


def detect_format(path):
    """'kicad' | 'epro' | None, decided by the bytes at the head of the file.

    ``epro`` covers both LCEDA Pro containers: the ZIP (``.epro``) and the
    SQLite project (``.eprj``/``.eprj2``). The SQLite one is readable but the
    write path refuses it (``WRITE_UNSUPPORTED_FORMAT``) - see
    ``epro_edit.SQLITE_REFUSED`` for why.
    """
    try:
        with open(path, "rb") as fh:
            head = fh.read(4096)
    except OSError:
        return None
    if head[:2] == b"PK":
        return "epro"
    stripped = head.lstrip(b"\xef\xbb\xbf \t\r\n")
    if stripped.startswith(b"(kicad_pcb") or stripped.startswith(b"(kicad_sch"):
        return "kicad"
    if head.startswith(b"SQLite format 3"):
        return "epro"
    return None


def propose(path, operations, options=None):
    """Plan a change to either format. Writes nothing, in either branch."""
    options = options or {}
    fmt = options.get("format")
    if fmt is None and path:
        fmt = detect_format(path)
    if fmt == "epro":
        return epro_edit.propose(path, operations, options)
    if fmt is None and path and os.path.isfile(path):
        raise EditError("this file is neither a KiCad board (starts with '(kicad_pcb') "
                        "nor an .epro container (starts with 'PK'); refusing to guess a "
                        "format", "FORMAT_NOT_SUPPORTED")
    return plan.propose(path, operations, options)


def apply_plan(plan_id, confirm=False, options=None):
    """Write a plan, whichever format made it.

    A plan is single-use and names its own format, so the caller never has to
    remember which write path produced the id.

    ``options={"step": N}`` switches to the stepped write: N operations per call,
    each one leaving a complete board file on disk so the change can be watched
    in the EDA and stopped between steps (see ``plan.apply_steps``).
    """
    options = options or {}
    record = plan.PLANS.get(plan_id) or {}
    fmt = record.get("format")
    stepped = options.get("step") is not None
    if fmt == "epro":
        if stepped:
            # Refuse instead of silently falling back to the one-shot write: the
            # caller asked to watch the change, and a .epro is a record stream whose
            # per-record write was never verified. In-app stepping goes through
            # lceda_pro_run, where each snippet is a visible action.
            raise EditError(
                "a stepped apply is not implemented for .epro files (its record "
                "stream has no verified per-record write). Nothing was written; "
                "call board_apply without options.step, or drive the open document "
                "step by step with lceda_pro_run", "STEP_NOT_SUPPORTED")
        return epro_edit.apply_plan(plan_id, confirm=confirm, options=options)
    if fmt == "kicad_sch":
        if stepped:
            return sch_plan.apply_steps(plan_id, confirm=confirm, options=options)
        return sch_plan.apply_plan(plan_id, confirm=confirm, options=options)
    if stepped:
        return apply_steps(plan_id, confirm=confirm, options=options)
    return plan.apply_plan(plan_id, confirm=confirm, options=options)
