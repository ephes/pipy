"""The production tool set: exactly the seven tools an agent turn can call.

Kept beside the tools themselves rather than in the session that happens to
build it. The set is a property of what this harness ships, not of how a REPL
run is wired, and three of its four callers sit below the session.

Imports are function-local so that naming the registry does not drag seven tool
implementations -- and the shell, filesystem and search machinery behind them --
into the import graph of anything that merely wants to know the set exists.

`bash` is a real shell, matching Pi: it runs an arbitrary command in the
workspace and returns combined, bounded stdout/stderr to the model. See
`pipy_harness.native.tools.bash.BashTool`.
"""

from __future__ import annotations

from pipy_harness.native.tools.base import ToolPort


def production_tool_registry() -> dict[str, ToolPort]:
    """Return the current production tool registry."""

    from pipy_harness.native.tools.bash import BashTool
    from pipy_harness.native.tools.edit import EditTool
    from pipy_harness.native.tools.find import FindTool
    from pipy_harness.native.tools.grep import GrepTool
    from pipy_harness.native.tools.ls import LsTool
    from pipy_harness.native.tools.read import ReadTool
    from pipy_harness.native.tools.write import WriteTool

    return {
        "read": ReadTool(),
        "ls": LsTool(),
        "grep": GrepTool(),
        "find": FindTool(),
        "write": WriteTool(),
        "edit": EditTool(),
        "bash": BashTool(),
    }
