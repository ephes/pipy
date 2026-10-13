"""MCP resources in a session (MCP parity plan S23).

A coding session with a scripted provider and the fake stdio server as a real
child process. Mirrors of Pi's "lists and reads resources with Codex's
resource tools" and "makes the resource tools callable from codemode for
codemode servers" (``agent-session-mcp.test.ts``), and the session level of
SES-09 for the resource tools: a call waits for every enabled server.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from test_native_codemode_delivery import (
    runtime,  # noqa: F401 - the real-runtime fixture
)
from test_native_codemode_delivery import settings as codemode_settings
from test_native_extension_tool_metadata import (
    _result,
    _tool_names,
    _tool_results,
)
from test_native_mcp_session import (
    _LATE,
    _WAITER,
    _codemode_output,
    _extension,
    _Provider,
    _records,
    _run,
    _server,
    _write_mcp_json,
)

from pipy_harness.models import HarnessStatus
from pipy_harness.native.models import ProviderToolCall

_RESOURCE_TOOLS = [
    "list_mcp_resources",
    "list_mcp_resource_templates",
    "read_mcp_resource",
]
_RESOURCES: dict[str, Any] = {
    "capabilities": {"tools": {}, "resources": {}},
    "resources": [
        {
            "uri": "docs://intro",
            "name": "intro",
            "mimeType": "text/markdown",
            "_meta": {"x": 1},
        },
        # MCP App user interfaces are left out.
        {
            "uri": "ui://docs/viewer",
            "name": "viewer",
            "mimeType": "text/html;profile=mcp-app",
        },
    ],
    "resourceTemplates": [
        {
            "uriTemplate": "docs://pages/{slug}",
            "name": "page",
            "icons": [{"src": "data:image/png;base64,AAAA"}],
        }
    ],
    "resourceContents": {
        uri: [{"uri": uri, "mimeType": "text/markdown", "text": f"# {uri}"}]
        for uri in ("docs://intro", "docs://pages/setup")
    },
}


def _reads(record: Path) -> list[str]:
    return [
        line["received"]["params"]["uri"]
        for line in _records(record)
        if line.get("received", {}).get("method") == "resources/read"
    ]


def test_lists_and_reads_resources_with_the_resource_tools(
    tmp_path: Path, monkeypatch
) -> None:
    # Pi: "lists and reads resources with Codex's resource tools".
    config = tmp_path / "config"
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(config))
    workspace = tmp_path / "work"
    workspace.mkdir()
    entry, record = _server(tmp_path / "servers", **_RESOURCES)
    _write_mcp_json(config / "mcp.json", {"docs": {**entry, "exposure": "direct"}})
    provider = _Provider(
        [
            _result(
                tool_calls=(
                    ProviderToolCall("c1", "list_mcp_resources", "{}"),
                    ProviderToolCall(
                        "c2", "list_mcp_resource_templates", '{"server": "docs"}'
                    ),
                    ProviderToolCall(
                        "c3",
                        "read_mcp_resource",
                        '{"server": "docs", "uri": "docs://pages/setup"}',
                    ),
                )
            ),
            _result(
                tool_calls=(
                    ProviderToolCall(
                        "c4",
                        "read_mcp_resource",
                        '{"server": "nope", "uri": "docs://x"}',
                    ),
                )
            ),
            _result(final_text="done"),
        ]
    )

    status, errors = _run(workspace, provider)

    assert status is HarnessStatus.SUCCEEDED, errors
    # The resource tools take the exposure of the servers they reach: the
    # first prompt waited for the direct server and declares them.
    assert _tool_names(provider.requests[0])[-3:] == _RESOURCE_TOOLS
    declared = provider.requests[0].available_tools[-1]
    assert declared.description == (
        "Read a specific resource from an MCP server given the server name "
        "and resource URI."
    )
    assert declared.input_schema["required"] == ["server", "uri"]
    listed, templates, read = _tool_results(provider.requests[1])[-3:]
    assert json.loads(listed.content.value) == {
        "resources": [
            {
                "server": "docs",
                "uri": "docs://intro",
                "name": "intro",
                "mimeType": "text/markdown",
            }
        ]
    }
    assert json.loads(templates.content.value) == {
        "server": "docs",
        "resourceTemplates": [
            {"server": "docs", "uriTemplate": "docs://pages/{slug}", "name": "page"}
        ],
    }
    assert (read.is_error, read.content.value) == (False, "# docs://pages/setup")
    unknown = _tool_results(provider.requests[2])[-1]
    assert unknown.is_error
    assert unknown.content.value == (
        'MCP server "nope" has no resources. Servers with resources: docs'
    )
    assert _reads(record) == ["docs://pages/setup"]


def test_the_resource_tools_are_callable_from_codemode_for_codemode_servers(
    tmp_path: Path,
    monkeypatch,
    runtime,  # noqa: F811
) -> None:
    # Pi: "makes the resource tools callable from codemode for codemode
    # servers".
    workspace = tmp_path / "work"
    workspace.mkdir()
    _extension(workspace, "waiter", _WAITER)
    entry, record = _server(tmp_path / "servers", **_RESOURCES)
    _write_mcp_json(workspace / "mcp.json", {"docs": entry})
    code = """listed = tools.list_mcp_resources(server='docs')
read = tools.read_mcp_resource(server='docs', uri=listed['resources'][0]['uri'])
try:
    tools.read_mcp_resource(server='nope', uri='docs://x')
except ToolError as error:
    failed = str(error)
text({'uris': [r['uri'] for r in listed['resources']],
      'text': read['contents'][0]['text'], 'failed': failed})
"""
    provider = _Provider(
        [
            _result(
                tool_calls=(ProviderToolCall("wait", "wait_for_mcp", '{"count": 2}'),)
            ),
            _result(
                tool_calls=(
                    ProviderToolCall("c1", "codemode", json.dumps({"code": code})),
                )
            ),
            _result(final_text="done"),
        ]
    )

    status, errors = _run(workspace, provider, settings=codemode_settings(workspace))

    assert status is HarnessStatus.SUCCEEDED, errors
    # Not declared to the model.
    assert not set(_RESOURCE_TOOLS) & set(_tool_names(provider.requests[1]))
    output = _codemode_output(_tool_results(provider.requests[2])[-1])
    assert output["uris"] == ["docs://intro"]
    assert output["text"] == "# docs://intro"
    assert 'MCP server "nope" has no resources' in output["failed"]
    assert _reads(record) == ["docs://intro"]


def test_a_resource_tool_waits_for_every_enabled_server(
    tmp_path: Path, monkeypatch
) -> None:
    # SES-09: the first prompt waits for the direct server only; the listing
    # then waits for the server that is still connecting.
    config = tmp_path / "config"
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(config))
    workspace = tmp_path / "work"
    workspace.mkdir()
    fast, _ = _server(tmp_path / "servers", "docs", **_RESOURCES)
    slow, _ = _server(
        tmp_path / "servers",
        "slow",
        **{
            **_RESOURCES,
            "resources": [{"uri": "slow://late", "name": "late"}],
            "methods": _LATE,
        },
    )
    _write_mcp_json(
        config / "mcp.json", {"docs": {**fast, "exposure": "direct"}, "slow": slow}
    )
    provider = _Provider(
        [
            _result(tool_calls=(ProviderToolCall("c1", "list_mcp_resources", "{}"),)),
            _result(final_text="done"),
        ]
    )

    status, errors = _run(workspace, provider)

    assert status is HarnessStatus.SUCCEEDED, errors
    listed = json.loads(_tool_results(provider.requests[1])[-1].content.value)
    # Without the wait the slow server would not have resources yet.
    assert [item["uri"] for item in listed["resources"]] == [
        "docs://intro",
        "slow://late",
    ]
    assert "errors" not in listed
