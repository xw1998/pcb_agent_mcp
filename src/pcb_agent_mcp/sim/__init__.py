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

`power` closes the loop back to heat: currents given per pad are solved on the
same graph, and each track segment's I^2R becomes a source in the 2-D plate
(board_electrothermal). A board file states no current, so that is the one
thing the caller has to bring.
"""

from . import board  # noqa: F401
from . import power  # noqa: F401

__all__ = ["board", "power"]
