"""File helpers shared by both write paths.

`atomic_write` is deliberately the only place in this project that creates a file
next to a user's project: a same-volume temp file is the only way to replace a
board without ever exposing a half-written one. It lives for milliseconds, it is
removed in `finally`, and its name starts with a dot.
"""
import hashlib
import os
import shutil
import tempfile

def home():
    """Where this project keeps its own state (checkpoints). Never a project dir."""
    return os.environ.get("PCB_AGENT_HOME") or os.path.join(
        os.path.expanduser("~"), ".pcb_agent_mcp")

def sha256(data):
    return hashlib.sha256(data).hexdigest()

def read_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()

def atomic_write(path, data):
    """Replace `path` with `data` atomically (same volume), cleaning up after."""
    folder = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(prefix=".pcbagent-", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        if os.path.exists(path):
            try:
                shutil.copymode(path, tmp)
            except OSError:
                pass
        os.replace(tmp, path)
        tmp = None
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
