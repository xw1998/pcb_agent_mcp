"""The via gate: what the search is allowed to see before it pays for a via.

Two answers, one number apart. "raster" is the historical behaviour - the
occupancy raster alone, which knows a cell is not obviously taken but not that a
0.6 mm ring fits between two exactly-on-the-rule neighbours. "exact" runs the
emitter's own 64-gon test first, so a search on a restricted layer set stops
planning vias the write is going to refuse.

The failing twins are both directions of silent wrongness: an exact gate that
never asks the exact test (the knob would be a lie), and one that skips the cheap
raster test and therefore pays a geometric check on every blocked cell (the knob
would be unusably slow). The tests below pin both.
"""
import unittest

from pcb_agent_mcp.route import router


class _FakeGrid(object):
    """Only the surface `_via_gate` is allowed to touch."""

    layers = ["F.Cu", "In1.Cu"]

    def __init__(self, free=True, blockers=None):
        self._free = free
        self._blockers = blockers or []
        self.raster_calls = 0
        self.exact_calls = 0

    def free_via(self, i, j, layer, net):
        self.raster_calls += 1
        return self._free

    def xy(self, i, j):
        return (i * 0.1, j * 0.1)

    def via_blockers(self, x, y, net, span=None):
        self.exact_calls += 1
        return list(self._blockers)


class TestTheViaGate(unittest.TestCase):

    def test_the_raster_gate_never_runs_the_exact_test(self):
        grid = _FakeGrid(free=True, blockers=[{"reason": "clearance"}])
        self.assertTrue(router._via_gate(grid, "A")(3, 4, "A"))
        self.assertEqual(grid.exact_calls, 0,
                         "the default gate is the cheap one, on purpose")

    def test_the_exact_gate_refuses_a_via_the_emitter_will_refuse(self):
        grid = _FakeGrid(free=True, blockers=[{"reason": "clearance",
                                              "short_by_mm": 0.0}])
        self.assertFalse(router._via_gate(grid, "A", exact=True)(3, 4, "A"))
        self.assertEqual(grid.exact_calls, 1)

    def test_the_exact_gate_still_says_yes_when_the_exact_test_is_clean(self):
        grid = _FakeGrid(free=True, blockers=[])
        self.assertTrue(router._via_gate(grid, "A", exact=True)(3, 4, "A"))
        self.assertEqual(grid.exact_calls, 1)

    def test_a_blocked_cell_still_pays_the_cheap_test_first(self):
        grid = _FakeGrid(free=False, blockers=[{"reason": "clearance"}])
        self.assertFalse(router._via_gate(grid, "A", exact=True)(3, 4, "A"))
        self.assertEqual(grid.exact_calls, 0,
                         "the raster is the rejection; the exact test is the answer")


if __name__ == "__main__":
    unittest.main()
