#!/usr/bin/env python
"""Behavior parity check for B7 (bash tool).

Drives the real product object: the ``bash`` tool resolved from
``production_tool_registry`` (the same registry the tool-loop REPL uses). It
proves the tool is registered AND that it is a real shell matching Pi's bash
tool (not a recreated, unregistered helper):

1. A plain command (``cat notes.txt``) runs and returns the file content as a
   non-error observation.
2. A real-shell pipeline (``echo hi | tr a-z A-Z``) runs — proving pipes work,
   not an allowlisted no-shell boundary.
3. ``.git`` is readable through the shell (``cat .git/config``), matching Pi —
   the bash tool has no ``.git`` default-deny.
4. A non-zero exit is a normal, non-error observation that reports the exit
   code, so the model can react to a failing command.

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
            _fail("bash failed to run a plain command")

        pipeline = _run("echo hi | tr a-z A-Z")
        if pipeline.is_error or "HI" not in pipeline.output_text:
            _fail("bash did not run a real-shell pipeline")

        git_read = _run("cat .git/config")
        if git_read.is_error or "[core]" not in git_read.output_text:
            _fail("bash could not read .git through the shell (Pi parity)")

        failing = _run("echo boom; exit 3")
        if failing.is_error or "exit code: 3" not in failing.output_text:
            _fail("bash did not surface a non-zero exit as a normal observation")

    print("OK: bash tool is a registered real shell matching Pi")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
