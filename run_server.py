#!/usr/bin/env python
"""Launch the MCP server without installing the package."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from pcb_agent_mcp.server import main  # noqa: E402

if __name__ == "__main__":
    main()
