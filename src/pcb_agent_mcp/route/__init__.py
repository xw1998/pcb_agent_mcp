"""Routing: turn "connect these pads" into copper, honestly.

Three modules, in the order data flows through them:

* `rules`  - what the board's net classes actually say (clearance, track, via).
             Nothing here is assumed unless it is marked `assumed` in the answer.
* `grid`   - a raster occupancy map used to *search* fast, plus the exact
             geometry check used to *accept* a move. The raster is allowed to be
             conservative (block a legal move); it is never allowed to be
             permissive, because acceptance is decided by the exact check.
* `router` - the search: a per-net minimum spanning tree, routed longest net
             first, with a bounded rip-up round for the nets that lose.

The router never writes. It returns ops in the same shape every other writer
uses (`add_track` / `add_via`), so copper only reaches the file through
`edit.propose` + `edit.apply_plan` and therefore through the same postcondition
checks and the same DRC referee as a hand edit.
"""

__all__ = ["rules", "grid", "router"]
