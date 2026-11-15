"""Make `pcb_agent_mcp` importable without an install, for every test file.

Why this file exists (measured, not reasoned): the package lives in `src/`, and
about sixty test files import it at module level without any `sys.path` work of
their own. They only ever worked because the files that DO insert `src/`
happened to be collected first - pytest imports test modules in directory order,
so `tests/test_45_corners.py` ... won the race and every later module inherited
the working `sys.path`. Run one of the others on its own and it fails:

    $ python -m pytest tests/test_board_ops_footprint.py
    E   ModuleNotFoundError: No module named 'pcb_agent_mcp'
    !!! Interrupted: 1 error during collection !!!
    1 error in 0.46s

A collection error is the worst kind of green: the file's tests never ran, and a
`pytest tests` run reports the same numbers as if they had. Measured on
`tests/test_blockers.py` (23 cases) and `tests/test_board_ops_footprint.py`
(collection error alone) before this file existed.

`conftest.py` is imported by pytest before any test module, so one line here
covers all of them and no test file has to carry its own copy.
"""
import os
import sys

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)
