"""The REPL tier: the loop that drives one interactive coding session.

This package is being carved out of `native/tool_loop_session.py`, which is the
composition root and the apex of the import DAG. Modules here may reach the
terminal UI and the session's collaborators; nothing they own may be reached
*from* the tiers below, and the boundary tests enforce both directions.
"""

from __future__ import annotations
