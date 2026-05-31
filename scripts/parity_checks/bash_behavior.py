#!/usr/bin/env python
"""Behavior parity check for B7 (bash tool).

Drives the real product object: the ``bash`` tool resolved from
``production_tool_registry`` (the same registry the tool-loop REPL uses). It
proves the tool is registered AND that it executes through the shared command
sandbox with concrete runtime containment:

1. A safe command (``cat notes.txt``) runs and returns the file content as a
   non-error observation.
2. ``.git`` access through a direct path (``cat .git/config``) is refused —
   this carries no shell metacharacters, so a pass proves containment happens
   at execution-resolution time, not via a string blocklist.
3. Command substitution (``echo $(...)``) is refused.

A recreated, unregistered helper module cannot satisfy this check.

Exits 0 on success, 1 on any mismatch with a short diagnostic.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path


def _fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> int:
    from pipy_harness.native.tool_loop_session import production_tool_registry
    from pipy_harness.native.tools.base import (
        ToolContext,
        ToolRequest,
        make_tool_request_id,
    )

    registry = production_tool_registry()
    if "bash" not in registry:
        _fail("bash is not registered in production_tool_registry()")
    tool = registry["bash"]
    if tool.definition.name != "bash":
        _fail("registered bash tool has the wrong definition name")

    with tempfile.TemporaryDirectory() as raw:
        workspace = Path(raw).resolve()
        (workspace / "notes.txt").write_text("hello parity\n", encoding="utf-8")
        (workspace / ".git").mkdir()
        (workspace / ".git" / "config").write_text(
            "[core]\n\ttoken = AKIAIOSFODNN7EXAMPLE\n", encoding="utf-8"
        )
        context = ToolContext(workspace_root=workspace)

        def _run(command: str):
            return tool.invoke(
                ToolRequest(
                    tool_request_id=make_tool_request_id(),
                    tool_name="bash",
                    arguments={"command": command},
                    provider_correlation_id="parity",
                ),
                context,
            )

        ok = _run("cat notes.txt")
        if ok.is_error or "hello parity" not in ok.output_text:
            _fail("bash failed to run a safe allowed command")

        git_attempt = _run("cat .git/config")
        if not git_attempt.is_error or "AKIAIOSFODNN7EXAMPLE" in git_attempt.output_text:
            _fail("bash did not contain .git access through a direct path")

        subst = _run("echo $(cat .git/config)")
        if not subst.is_error:
            _fail("bash did not refuse command substitution")

    print("OK: bash tool is registered and runtime-contained")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
