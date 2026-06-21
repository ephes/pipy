"""Golden conformance extension for the pipy Python extension API.

Copy this file to `<workspace>/.pipy/extensions/pipy-extension-conformance.py`
(or the global extension dir) and set `PIPY_EXTENSION_CONFORMANCE_PROOF` to a
writable path. Running the `/pipy-extension-conformance` slash command then
exercises the whole extension API end to end and writes one metadata-only
feature marker per line to the proof file.

The flow is deterministic and does not rely on a real model choosing the tool:

1. `/pipy-extension-conformance` dispatches the command (no provider turn),
   records `command_handler`, and `api.send_user_message("run conformance
   probe")` enqueues a deterministic prompt.
2. That prompt runs a normal turn: the `input` hook records `input`,
   `before_agent_start` injects `CONFORMANCE_CONTEXT`, and the lifecycle
   observers record `agent_start` / `turn_start`.
3. The (test-driven) provider calls the registered `conformance_probe` tool:
   `tool_call` records the call, the tool executes (records `tool_execute`,
   returns Pi-shaped `content` + `details`), and `tool_result` patches the
   observation.
4. `turn_end` / `agent_end` / `session_shutdown` record the lifecycle close.

Every marker is metadata-only: no prompt bodies, tool arguments, tool result
content, UI text, provider payloads, secrets, or proof-file contents are ever
written to the proof file or the default session archive.
"""

from __future__ import annotations

import json
import os

from pipy_harness.extensions import (
    BeforeAgentStartResult,
    ExtensionTool,
    ToolResult,
    ToolResultTransform,
    lines_component,
)

PROOF_ENV = "PIPY_EXTENSION_CONFORMANCE_PROOF"


def _proof(feature: str, **fields: object) -> None:
    """Append one metadata-only feature marker to the proof file."""

    path = os.environ.get(PROOF_ENV)
    if not path:
        return
    record = {"feature": feature, "ok": True, **fields}
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
    except OSError:
        pass


def activate(api):
    @api.on("session_start")
    def _session_start(event, ctx):
        _proof("session_start", reason=event.reason or "")

    @api.on("session_shutdown")
    def _session_shutdown(event, ctx):
        _proof("session_shutdown")

    @api.on("agent_start")
    def _agent_start(event, ctx):
        _proof("agent_start")

    @api.on("turn_start")
    def _turn_start(event, ctx):
        _proof("turn_start")

    @api.on("turn_end")
    def _turn_end(event, ctx):
        _proof("turn_end")

    @api.on("agent_end")
    def _agent_end(event, ctx):
        _proof("agent_end")

    @api.on("input")
    def _input(event, ctx):
        _proof("input")
        return None

    @api.on("before_agent_start")
    def _before_agent_start(event, ctx):
        _proof("before_agent_start", system_prompt_modified=True)
        return BeforeAgentStartResult(append_system_prompt="CONFORMANCE_CONTEXT")

    @api.on("tool_call")
    def _tool_call(event, ctx):
        if event.tool_name == "conformance_probe":
            _proof("tool_call", tool_name=event.tool_name)
        return None

    @api.on("tool_result")
    def _tool_result(event, ctx):
        if event.tool_name == "conformance_probe":
            _proof("tool_result", patched=True)
            return ToolResultTransform(content="PATCHED::" + event.content)
        return None

    def _probe(ctx, params):
        _proof("tool_execute", details_written=True)
        ctx.ui.notify("conformance probe ran")
        return ToolResult(content="probe-output", details={"ran": True})

    def _render_call(ctx):
        _proof("render_call", tool_name=ctx.tool_name)
        return lines_component([f"probe call: {sorted(ctx.args)}"])

    def _render_result(ctx):
        _proof("render_result", has_details=bool(ctx.details), is_result=ctx.is_result)
        return lines_component([ctx.theme.fg("success", "probe ok")])

    api.register_tool(
        ExtensionTool(
            name="conformance_probe",
            description="Run the deterministic conformance probe.",
            input_schema={
                "type": "object",
                "properties": {"probe_arg": {"type": "string"}},
            },
            handler=_probe,
            render_call=_render_call,
            render_result=_render_result,
        )
    )

    def _command(ctx, args):
        _proof("command_handler")
        ctx.ui.notify("conformance command ran")
        api.send_user_message("run conformance probe")

    api.register_command(
        "pipy-extension-conformance",
        "Run the pipy extension API conformance probe.",
        _command,
    )
