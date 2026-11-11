"""Prioritized design rules: what to fix first, and why that order is not a taste.

A rule catalogue answers "is this board wrong". This package answers the question
that decides whether an agent can finish a board on its own: **in what order**.
Levels P0..P4 are a priority, not a severity - a P0 item can look cosmetic and
still stop the board, and a P3 item can be the whole point of the product and
still be the last thing to fix, because a P0 violation makes the board
unbuildable and a P3 one makes it imperfect.

The rule that gives the levels their teeth:

    a lower level may never be bought with a higher one.

so the audit marks every rule below the first failing level as `deferred` - not
`skipped`, not `passed` - and refuses to present it as the thing to do next.
"""
from .audit import audit, catalog, LEVELS, LEVEL_NAMES  # noqa: F401
