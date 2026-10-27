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

from .ops import OpError
from .plan import EditError
from .plan import checkpoint_root
from .plan import create_board
from .plan import find_checkpoint
from .plan import list_checkpoints
from .plan import restore_checkpoint
from .plan import save_checkpoint

__all__ = [
    "build", "checks", "epro_edit", "fsutil", "ops", "plan",
    "EditError", "OpError", "detect_format",
    "apply_plan", "checkpoint_root", "create_board", "describe_ops",
    "describe_epro_ops", "find_checkpoint", "list_checkpoints",
    "propose", "restore_checkpoint", "save_checkpoint",
]


def describe_ops():
    """The KiCad operation catalogue as the MCP tool layer sees it."""
    return ops.describe()


def describe_epro_ops():
    """The `.epro` operation catalogue (record-stream operations)."""
    return epro_edit.describe()


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
    """Write a plan, whichever format made it."""
    record = plan.PLANS.get(plan_id) or {}
    if record.get("format") == "epro":
        return epro_edit.apply_plan(plan_id, confirm=confirm, options=options)
    return plan.apply_plan(plan_id, confirm=confirm, options=options)
