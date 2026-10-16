"""Codemode, tool search and MCP in an embedded session.

The ``pipy`` command has ``codemode``, ``tool_search`` and MCP built in. A
session made with ``create_product_session`` has none of them until the
embedder asks: ``codemode=CodemodeOptions(...)``, ``tool_search=True`` and
``mcp=McpOptions(...)``.

``codemode`` and ``tool_search`` are then registered but not active. The
``defaultTools`` setting activates them (``+name`` adds to the default tools
instead of replacing them), and so does MCP: ``codemode`` for servers with
``codemode`` tools, ``tool_search`` for servers with ``deferred`` tools.

``McpOptions()`` reads ``mcp.json`` from the config home and the trusted
project, like the command. This example is offline and changes nothing
outside its temporary directories, so it passes its own configuration loader
and a transport to a server that runs in this process, and it uses
``FakeNativeProvider`` with a scripted answer in place of a model. Production
callers inject a real provider in the same place.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch

from pipy_harness.native.agent import (  # type: ignore[import-untyped]
    AgentToolResultMessage,
)
from pipy_harness.native.fake import FakeNativeProvider  # type: ignore[import-untyped]
from pipy_harness.native.mcp.config import (  # type: ignore[import-untyped]
    LoadedMcpConfig,
    McpServerEntry,
)
from pipy_harness.native.mcp.transport import (  # type: ignore[import-untyped]
    McpTransport,
    create_in_memory_transport_pair,
)
from pipy_harness.native.models import (  # type: ignore[import-untyped]
    ProviderToolCall,
)
from pipy_harness.native.resource_loading import (  # type: ignore[import-untyped]
    RuntimeResourceOptions,
)
from pipy_harness.native.settings import SettingsManager  # type: ignore[import-untyped]
from pipy_harness.sdk import (  # type: ignore[import-untyped]
    CodemodeOptions,
    CodingSessionResultSnapshot,
    McpOptions,
    create_product_session,
)

PROMPT = "Look up what the docs say about pipy."

_LOOKUP_TOOL = {
    "name": "lookup",
    "description": "Look a term up in the product docs.",
    "inputSchema": {
        "type": "object",
        "properties": {"term": {"type": "string"}},
        "required": ["term"],
    },
}


def load_config(
    *, config_home: Path, cwd: Path, project_trusted: bool
) -> LoadedMcpConfig:
    """One server whose tools the model finds with ``tool_search``.

    The default, ``pipy_harness.native.mcp.config.load_mcp_config``, takes
    the same arguments and reads the ``mcp.json`` files.
    """

    docs = McpServerEntry(
        name="docs",
        config={"command": "docs-server", "exposure": "deferred"},
        source="sdk_codemode_mcp.py",
    )
    return LoadedMcpConfig(servers=[docs], errors=[])


def create_transport(entry: McpServerEntry, cwd: str, auth: object) -> McpTransport:
    """Connect ``entry`` to a server in this process instead of starting its
    command. The default builds stdio and streamable HTTP transports."""

    client, server = create_in_memory_transport_pair()

    def answer(message: dict[str, Any]) -> None:
        if "id" not in message:
            return  # a notification
        method = message.get("method")
        params = message.get("params") or {}
        if method == "initialize":
            result: dict[str, Any] = {
                "protocolVersion": params["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": entry.name, "version": "1"},
            }
        elif method == "tools/list":
            result = {"tools": [_LOOKUP_TOOL]}
        elif method == "tools/call":
            term = params["arguments"]["term"]
            result = {"content": [{"type": "text", "text": f"{term}: a coding agent"}]}
        else:
            server.send(
                {
                    "jsonrpc": "2.0",
                    "id": message["id"],
                    "error": {"code": -32601, "message": "Method not found"},
                }
            )
            return
        server.send({"jsonrpc": "2.0", "id": message["id"], "result": result})

    server.on_message(answer)
    server.start()
    return client


@dataclass(frozen=True, slots=True)
class CodemodeMcpExampleResult:
    """What the session held after the prompt."""

    snapshot: CodingSessionResultSnapshot
    # The text of each tool result, in order.
    tool_results: tuple[str, ...]


def run_example(workspace: Path) -> CodemodeMcpExampleResult:
    """Run one prompt in a session that has all three."""

    # Stands in for a model: it searches for a tool, calls the MCP tool the
    # search loaded, and answers.
    provider = FakeNativeProvider(
        model_id="example-tool-capable-fake",
        final_text="The docs call pipy a coding agent.",
        supports_tool_calls=True,
        programmable_tool_calls=(
            (ProviderToolCall("search", "tool_search", '{"query": "lookup docs"}'),),
            (ProviderToolCall("lookup", "mcp__docs__lookup", '{"term": "pipy"}'),),
        ),
    )
    resources = RuntimeResourceOptions(
        no_extensions=True,
        no_skills=True,
        no_prompt_templates=True,
        no_themes=True,
    )
    with TemporaryDirectory(prefix="pipy-sdk-codemode-mcp-") as config_dir:
        settings = SettingsManager(
            global_path=Path(config_dir) / "settings.json",
            env={},
            project_trusted=False,
            # MCP would activate `tool_search` for the `deferred` server by
            # itself; `codemode` is active because of this setting. Where
            # the codemode runtime is not installed, pipy says so and the
            # session goes on without the tool.
            overrides={"defaultTools": ["+codemode", "+tool_search"]},
        )
        with patch.dict(os.environ, {"PIPY_CONFIG_HOME": config_dir}):
            with create_product_session(
                workspace=workspace,
                provider=provider,
                settings=settings,
                resources=resources,
                load_context_files=False,
                codemode=CodemodeOptions(mode="on"),
                tool_search=True,
                mcp=McpOptions(
                    load_config=load_config, create_transport=create_transport
                ),
            ) as session:
                # The servers start connecting when the session is created.
                # Closing the session closes them.
                snapshot = session.submit(PROMPT)

    results = tuple(
        message.content.value
        for message in snapshot.messages
        if isinstance(message, AgentToolResultMessage)
    )
    return CodemodeMcpExampleResult(snapshot, results)


def main() -> None:
    """Run in a temporary workspace so the repository is never modified."""

    with TemporaryDirectory(prefix="pipy-sdk-codemode-mcp-work-") as temporary_dir:
        result = run_example(Path(temporary_dir))
    for text in result.tool_results:
        print(text)


if __name__ == "__main__":
    main()
