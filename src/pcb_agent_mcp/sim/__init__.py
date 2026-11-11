"""Whole-board simulation: the .kicad_pcb turned into a circuit, not a report.

Every other module here answers a question about a PIECE of copper - one net's
drop, one trace's impedance, one plate's temperature. This package builds the
one object a simulator needs and this server never produced: a SPICE deck whose
elements are the board's own copper, so `sim_spice` can be asked a BOARD question
instead of a netlist the caller typed by hand.

`board` is the extractor. It adds no physics of its own for resistance: the
graph comes out of `oracle.board_ir_drop(..., return_network=True)`, the same
builder that answers "how much does 2 A drop", so a deck and an IR-drop report
on the same board cannot disagree. What is new here is the rest of the circuit -
L and C of each segment, taken from the transmission line that copper actually
forms over its reference plane, and the via barrel's inductance.
"""

from . import board  # noqa: F401

__all__ = ["board"]
