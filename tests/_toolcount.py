"""The one number the tool-surface ratchet compares against.

Why this file exists: twelve feature tests used to each carry their own copy of
`self.assertEqual(len(tools.TOOLS), 229)`. That is a ratchet per feature - "adding
a tool must bump this number" - but twelve copies drift, and they did: by 0.0.144
the surface was at 250 while every copy still said 229, so all twelve were failing
and the ratchet had stopped guarding anything (an always-red number is no number).

So the number lives here once. Bumping it is still a deliberate edit; it is just
an edit in one place instead of twelve. The history below is the audit trail that
used to be spread across the twelve files.

    162 -> 229   the long climb through 0.0.6x..0.0.9x (magnetics, plane,
                 current-density, PDN, fabrication gates, ...)
    229 -> 250   0.1xx: the routing-discipline, lceda, and analysis tools that
                 were added after the per-file copies stopped being maintained
    250 -> 252   0.0.145: board_plane_resonance (a board-level cavity solve) and
                 board_crosstalk (a board-level coupling solve) - two new
                 questions, not two flags on existing tools
    252 -> 253   0.1.21: pcb_rule_projection - which of the 27 rules the
                 generator can consume, as what shape, and the live payload.
                 A new question ("why was the rule not used"), not a flag.
    253 -> 254   0.1.22: board_tune_length - length matching as a POST-PASS on
                 copper that is already routed (board_route can only tune the
                 nets it routes in the same run). A new question ("tune this
                 board"), not a flag.

    256 -> 259   0.1.40: three readings that sit between "the copper is wrong"
                 and "here is how much room you have to fix it" -
                 board_length_budget (can this group still be length-matched, and
                 does it fail for want of band or want of straight run),
                 board_group_consistency (same length / parallel / even gap, three
                 separate limits) and board_reservations (read back the named rule
                 areas). New questions, not flags. The four editing primitives
                 that came with them (move_cluster, swap_pad_nets,
                 reserve_region, release_region) are board_edit ops, not tools.

    259 -> 260   0.1.41: board_aesthetics - the missing question between "both of
                 these boards are legal" and "which one is better to look at".
                 Readings of routing SHAPE (piece length, direction changes per
                 100 mm and per pad, how much copper runs alongside another net),
                 ordered by dominance with the disagreeing readings named. Not a
                 gate, not a score with tuned weights; it refuses to rank fewer
                 than two boards and never uses corpus numbers as thresholds.

    260 -> 261   0.1.42: board_decap_sites - "where does the bypass capacitor
                 go, and what makes that site legal", answered per rail or
                 serially per board. A new question (placement planning against
                 FILLED copper), not a flag on board_place_gate: that gate reports
                 a distance, this one returns a legal site with both legs of the
                 loop closed, or a refusal with the number that refused it.

    261 -> 262   0.1.43: board_power_loop - "the copper one switching cell owes,
                 leg by leg, before anything is written". A new question (the load
                 side of the loop board_decap_sites closes on the supply side), and
                 the first tool that keeps three outcomes apart for one leg:
                 closed, over_budget (legal copper at a placement price - a 13 mm
                 switch node is measured and NOT emitted) and blocked (named
                 blocker, no copper). It also refuses to answer "the flood is
                 0.33 mm away" when every holeable point of that flood is under a
                 magnetic: the refusal names the body instead of a distance that
                 would send the reader to draw a bus that cannot help.

    262 -> 263   0.1.44: board_return_path - the question the router never asked
                 about its own copper: "does this layer change keep its return
                 current in the same conductor". Two readings on purpose
                 (proximity vs reference plane), because they disagree for a
                 reason worth reading, and one classification no existing tool
                 could produce: reference_changes_net - a defect no via fixes.
                 New question, not a flag.

    263 -> 264   (no version bump - docs/202 is a stage note): board_power_copper -
                 "what carries this rail, plane or width". board_net_report's
                 power_net_thin could only see width, so a rail backed by a filled
                 plane read as thin through its 0.1 mm escape stubs. This one
                 separates plane_area_mm2 from trace_area_mm2, gives IPC-2221
                 ampacity for the narrowest segment on external and internal
                 copper, and lists the zone owners per layer with their priority
                 (the only 'plane split' this format expresses). New question, and
                 the older flag is now plane-aware too. Not added to the
                 `available` list: it reads a board, so it is not a pure
                 calculator, and that count stays 222.

    264 -> 265   (no version bump - docs/203 is a stage note): board_rail_plan -
                 the four questions a power rail needs answered in the order that
                 decides what "done" means: coverage (which pads touch copper of
                 their own net), width (IPC demand vs measured traces AND how much
                 wider the narrowest may legally get), plane (pads/vias inside each
                 fill, min_thickness, connect mode - and a plane PROVES capacity
                 only when min_thickness >= the demand; otherwise the answer is
                 "unmeasured", because the corridor width is a shape fact this
                 tool does not invent), and failure (why, per row). It also names
                 a stale pour (a same-layer foreign track inside a fill) and two
                 nets tied at the same zone priority. board_power_copper could
                 only say WHICH carrier was failing; this says what to do about it
                 and refuses to guess a current: a net listed with no current
                 comes back no_demand with geometry and nothing judged. Also not
                 in the `available` list, for the same reason as board_power_copper.

Nothing here is a routing or placement tool: board_route and board_fanout are
gone (TOOL_RETIRED) and stay gone, so they are not in the count below.
"""

EXPECTED_TOOL_COUNT = 265

# calc_capabilities answers for the subset of tools that are pure calculators:
# they take numbers and return numbers, so "available" is a shorter list than
# TOOLS (the file-touching and job tools are not in it). It drifts the same way
# TOOLS did - three copies of "204" survived long past 204 - so it is pinned here
# too. 204 -> 222 across the 0.1xx additions.
EXPECTED_AVAILABLE_COUNT = 222
