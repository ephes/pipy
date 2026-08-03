"""Self-contained overlay components for the native terminal UI.

Each module here owns one interactive component: its own state, its `render`,
and its `handle_input`. A component never reaches the terminal-UI shell -- it is
handed a `done` callback at construction and reports through that, so the shell
decides what a result means and the component stays testable without a terminal.
"""

from __future__ import annotations
