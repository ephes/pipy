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

# Unique leak canaries: must never reach the proof file or the metadata
# archive (the rendered body is live-only; entry data is archive-excluded).
# Module-level so both the renderer and the command handler closure reference
# the same values. The custom_type "conformance-card" is safe metadata.
_MSG_BODY_SENTINEL = "PIPY_MSGBODY_9f3a2c"
_MSG_DATA_SENTINEL = "PIPY_MSGDATA_7b1e44"


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

    @api.on("session_start")
    def _chrome(event, ctx):
        ctx.ui.set_widget("conf", ["conformance widget"], placement="above_editor")
        _proof("set_widget", placement="above_editor")
        ctx.ui.set_header(lambda theme: lines_component(["conformance header"]))
        _proof("set_header")
        ctx.ui.set_footer(lambda theme, fd: lines_component([f"branch={fd.git_branch}"]))
        _proof("set_footer")
        ctx.ui.set_title("pipy conformance")
        _proof("set_title")
        ctx.ui.set_working_indicator(["*"], interval_ms=120)
        _proof("set_working_indicator")

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

    def _render_card(data, ctx):
        # Metadata-only marker: records THAT a rich (context-aware) renderer ran
        # and whether a theme was available -- never the rendered body or data.
        _proof("message_renderer_component", styled=ctx.theme is not None)
        body = (
            ctx.theme.fg("accent", _MSG_BODY_SENTINEL)
            if ctx.theme
            else _MSG_BODY_SENTINEL
        )
        return lines_component([body])

    api.register_message_renderer("conformance-card", _render_card)

    def _command(ctx, args):
        _proof("command_handler")
        ctx.ui.notify("conformance command ran")
        # Append a custom entry whose data carries a unique sentinel; the
        # registered rich renderer runs synchronously and emits the body
        # sentinel live-only. Neither sentinel may reach proof/archive.
        ctx.append_entry("conformance-card", {"sentinel": _MSG_DATA_SENTINEL})
        api.send_user_message("run conformance probe")

    api.register_command(
        "pipy-extension-conformance",
        "Run the pipy extension API conformance probe.",
        _command,
    )
