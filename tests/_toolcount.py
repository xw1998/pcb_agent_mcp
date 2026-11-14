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

Nothing here is a routing or placement tool: board_route and board_fanout are
gone (TOOL_RETIRED) and stay gone, so they are not in the count below.
"""

EXPECTED_TOOL_COUNT = 254

# calc_capabilities answers for the subset of tools that are pure calculators:
# they take numbers and return numbers, so "available" is a shorter list than
# TOOLS (the file-touching and job tools are not in it). It drifts the same way
# TOOLS did - three copies of "204" survived long past 204 - so it is pinned here
# too. 204 -> 222 across the 0.1xx additions.
EXPECTED_AVAILABLE_COUNT = 222
