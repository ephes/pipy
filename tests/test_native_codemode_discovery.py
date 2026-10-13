"""Tool discovery from codemode scripts (MCP parity plan S07).

Rows CMD-02, CMD-06 to CMD-10 and CMD-17: a tool is ``tools.<identifier>`` and
``tools["<name>"]``; ``ALL_TOOLS`` lists the callable tools with their
declarations; ``search_tools``, ``describe_tool`` and ``describe_namespace``
answer from the parent's callable tools without running one; and a tool that
does not exist is reported with its close matches.

Layers, bottom up: the guest prelude's helpers (run here on the host), the
host runner against the fake worker, the discovery answers, and a session with
extension tools on the real CPython-on-WASI guest.
"""

from __future__ import annotations

import copy
import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from test_native_codemode_delivery import (
    runtime,  # noqa: F401 - the real-runtime fixture
)
from test_native_codemode_extension_tools import _output, _run_script
from test_native_codemode_host import (
    _no_tools,
    _sandbox_error,
    spawned,  # noqa: F401 - points the runner at the fake worker
)
from test_native_codemode_host import _run as _run_fake

from pipy_harness.native.agent import NestedToolCallStarted
from pipy_harness.native.codemode import prelude
from pipy_harness.native.codemode.declarations import to_codemode_identifier
from pipy_harness.native.codemode.outcome import (
    CallResult,
    DiscoveryResult,
    ScriptLimits,
    ScriptTool,
)
from pipy_harness.native.coding.codemode_discovery import (
    CodemodeDiscovery,
    is_namespace_name,
)
from pipy_harness.native.tools.base import ToolDefinition, ToolNamespace

# --- identifiers and the prelude's tool table (CMD-02) ------------------------------


def _records(*names: str) -> list[dict[str, str]]:
    return [
        {"name": name, "id": to_codemode_identifier(name), "description": f"<{name}>"}
        for name in names
    ]


def test_a_keyword_gets_a_trailing_underscore_as_its_identifier() -> None:
    assert to_codemode_identifier("class") == "class_"
    assert to_codemode_identifier("None") == "None_"
    # Soft keywords are ordinary attribute names.
    assert to_codemode_identifier("match") == "match"
    assert to_codemode_identifier("mcp__dev-radius__search") == (
        "mcp__dev_radius__search"
    )


def test_tool_table_maps_identifiers_and_raw_names_first_tool_wins() -> None:
    # Pi's prelude: `tools[jsName]` and `tools[name]`; the first tool wins an
    # identifier, and a tool that lost it keeps its own name as a key.
    keys, entries = prelude.tool_table(
        _records("read-file", "read_file", "class", "plain", "plain")
    )
    assert keys == {
        "read_file": "read-file",
        "read-file": "read-file",
        "class_": "class",
        "class": "class",
        "plain": "plain",
    }
    # `read_file` lost both of its keys to `read-file`: no key reaches it,
    # so it has no entry. An entry's name always reaches the entry's tool.
    assert entries == [
        {"name": "read_file", "description": "<read-file>"},
        {"name": "class_", "description": "<class>"},
        {"name": "plain", "description": "<plain>"},
    ]
    assert prelude.tool_key(keys, "read_file", "read_file") is None

    # In the other order both tools are reachable; the second one is listed
    # under its own name, which is the key that reaches it.
    keys, entries = prelude.tool_table(_records("read_file", "read-file"))
    assert keys == {"read_file": "read_file", "read-file": "read-file"}
    assert [entry["name"] for entry in entries] == ["read_file", "read-file"]
    assert prelude.tool_key(keys, "read-file", "read_file") == "read-file"


def _tools(*names: str) -> tuple[prelude.Tools, list[tuple[str, dict[str, Any]]]]:
    sent: list[tuple[str, dict[str, Any]]] = []

    def call(name: str, args: dict[str, Any]) -> str:
        sent.append((name, args))
        return "value"

    keys, entries = prelude.tool_table(_records(*names))
    return prelude.Tools(keys, [entry["name"] for entry in entries], call), sent


def test_tools_are_reached_by_identifier_and_by_raw_name() -> None:
    tools, sent = _tools("mcp__dev-radius__search", "class", "9lives", "read")

    assert tools.mcp__dev_radius__search(query="a") == "value"
    assert tools["mcp__dev-radius__search"]({"query": "b"}) == "value"
    assert tools["mcp__dev_radius__search"](query="c") == "value"
    assert getattr(tools, "mcp__dev-radius__search")(query="d") == "value"
    assert tools.class_() == "value"
    assert tools["class"]() == "value"
    # An identifier that starts with `_` is a tool like any other.
    assert tools._lives() == "value"
    # The host always receives the tool's own name.
    assert sent == [
        ("mcp__dev-radius__search", {"query": "a"}),
        ("mcp__dev-radius__search", {"query": "b"}),
        ("mcp__dev-radius__search", {"query": "c"}),
        ("mcp__dev-radius__search", {"query": "d"}),
        ("class", {}),
        ("class", {}),
        ("9lives", {}),
    ]
    assert tools.class_.__name__ == "class_"

    assert "class" in tools and "class_" in tools and "klass" not in tools
    assert 3 not in tools
    assert list(tools) == ["mcp__dev_radius__search", "class_", "_lives", "read"]
    assert len(tools) == 4
    assert dir(tools) == ["_lives", "class_", "mcp__dev_radius__search", "read"]
    assert repr(tools) == "<tools: mcp__dev_radius__search, class_, _lives, read>"


def test_a_missing_tool_names_close_matches() -> None:
    # CMD-17, Pi's `guard`: the same name but for case and punctuation, else
    # names that contain the wanted one or are contained in it.
    tools, sent = _tools("mcp__dev-radius__search", "mcp__dev-radius__fetch", "read")

    with pytest.raises(AttributeError) as punctuation:
        tools.mcp_dev_radius_search  # noqa: B018 - the access is under test
    assert str(punctuation.value) == (
        "tools.mcp_dev_radius_search does not exist. Did you mean "
        "tools.mcp__dev_radius__search? ALL_TOOLS lists every tool; "
        "search_tools(query) finds tools by topic. Check for a tool with "
        '"mcp_dev_radius_search" in tools.'
    )
    assert punctuation.value.name is None

    with pytest.raises(KeyError) as contained:
        tools["radius"]
    assert (
        "Did you mean tools.mcp__dev_radius__search, tools.mcp__dev_radius__fetch?"
        in (contained.value.args[0])
    )
    with pytest.raises(AttributeError, match=r"Did you mean tools\.read\?"):
        tools.reed  # noqa: B018
    with pytest.raises(AttributeError, match=r"does not exist\. Available: mcp__"):
        tools._private  # noqa: B018
    # The object's own state is not an attribute a script can mistake for a tool.
    with pytest.raises(AttributeError, match=r"tools\._state does not exist"):
        tools._state  # noqa: B018
    with pytest.raises(TypeError, match="takes a tool name, not int"):
        tools[0]
    assert sent == []

    names = [f"tool{index:02d}" for index in range(8)]
    message = prelude.unknown_tool_message("tool", names)
    # At most five close matches are named.
    assert message.count("tools.tool0") == 5
    assert prelude.unknown_tool_message("x", []) == (
        "tools.x does not exist. No tools are available."
    )
    # Punctuation alone is contained in no name.
    assert "Did you mean" not in prelude.unknown_tool_message("__", ["read", "ls"])


def test_interpreter_probes_and_copies_do_not_look_like_tools() -> None:
    tools, _ = _tools("read")
    with pytest.raises(AttributeError, match="__wrapped__"):
        tools.__wrapped__  # noqa: B018
    # An instance made without __init__ has no slots set; looking one up must
    # end in AttributeError, not recurse.
    bare = prelude.Tools.__new__(prelude.Tools)
    with pytest.raises(AttributeError):
        bare.read  # noqa: B018
    assert copy.copy(tools).read() == "value"


def test_no_tool_name_is_shadowed_by_the_tools_object() -> None:
    names = ("_state", "_Tools__keys", "_bound_tool", "items", "__len__")
    tools, sent = _tools(*names)
    assert tools._state() == tools._Tools__keys() == tools._bound_tool() == "value"
    assert tools.items() == "value"
    # A dunder name stays the interpreter's; its tool is reached by key.
    assert len(tools) == 5
    assert tools["__len__"]() == "value"
    assert [name for name, _ in sent] == list(names[:4]) + ["__len__"]


# --- the prelude's discovery helpers and their wire message --------------------------


def test_discovery_helpers_send_their_arguments_by_name() -> None:
    sent: list[tuple[str, dict[str, Any]]] = []

    def discover(name: str, args: dict[str, Any]) -> str:
        sent.append((name, args))
        return "answer"

    helpers = prelude.discovery_helpers(discover)
    assert sorted(helpers) == sorted(prelude.DISCOVERY_NAMES)
    assert helpers["search_tools"]("docs") == "answer"
    assert helpers["search_tools"]("docs", 3, "mcp__docs") == "answer"
    assert helpers["search_tools"](query="docs", namespace="docs", limit=None)
    assert helpers["describe_tool"]("read") == "answer"
    assert helpers["describe_namespace"]("mcp__docs") == "answer"
    assert sent == [
        ("search_tools", {"query": "docs", "limit": 8, "namespace": None}),
        ("search_tools", {"query": "docs", "limit": 3, "namespace": "mcp__docs"}),
        ("search_tools", {"query": "docs", "limit": None, "namespace": "docs"}),
        ("describe_tool", {"name": "read"}),
        ("describe_namespace", {"name": "mcp__docs"}),
    ]
    with pytest.raises(TypeError):
        helpers["search_tools"]()
    with pytest.raises(TypeError):
        helpers["describe_tool"](name="read")


def _wire(reply: dict[str, Any]) -> tuple[prelude.Wire, int, list[int]]:
    read_fd, write_fd = os.pipe()
    out_read, out_write = os.pipe()
    os.write(write_fd, (json.dumps({"type": "result", **reply}) + "\n").encode())
    return (
        prelude.Wire(read_fd, out_write),
        out_read,
        [
            read_fd,
            write_fd,
            out_read,
            out_write,
        ],
    )


def test_wire_discover_returns_the_value_or_raises_value_error() -> None:
    for value in ([{"name": "read", "description": "d"}], None, "text"):
        wire, out_read, fds = _wire({"id": 1, "ok": True, "value": value})
        try:
            assert wire.discover("describe_tool", {"name": "read"}) == value
            assert json.loads(os.read(out_read, 4096)) == {
                "type": "discover",
                "id": 1,
                "name": "describe_tool",
                "args": {"name": "read"},
            }
        finally:
            for fd in fds:
                os.close(fd)

    wire, _, fds = _wire({"id": 1, "ok": False, "error": "expects a query string"})
    try:
        with pytest.raises(ValueError, match="expects a query string"):
            wire.discover("search_tools", {"query": 3})
    finally:
        for fd in fds:
            os.close(fd)


def test_wire_discover_refuses_arguments_that_are_not_json() -> None:
    wire, out_read, fds = _wire({"id": 1, "ok": True, "value": 1})
    try:
        with pytest.raises(TypeError, match=r"^search_tools\(\) arguments must be"):
            wire.discover("search_tools", {"query": {1, 2}})
        # Nothing was sent and no id was used.
        assert wire.discover("search_tools", {"query": "q"}) == 1
        assert json.loads(os.read(out_read, 4096))["id"] == 1
    finally:
        for fd in fds:
            os.close(fd)


# --- the host runner (fake worker) -------------------------------------------------


def test_host_answers_discovery_without_a_call_record(
    monkeypatch: pytest.MonkeyPatch,
    spawned: list[int],  # noqa: F811
) -> None:
    # Plan 4.9 item 3: discovery never executes a tool and is not a call.
    seen: list[tuple[str, str, dict[str, Any]]] = []

    def discover(name: str, args: dict[str, Any]) -> DiscoveryResult:
        seen.append((threading.current_thread().name, name, args))
        if name == "describe_tool":
            return DiscoveryResult(None)
        if name == "describe_namespace":
            return DiscoveryResult(
                error="describe_namespace() expects a namespace name"
            )
        return DiscoveryResult([{"name": "read", "description": "ü"}])

    scenario = (
        "start()\n"
        "replies = [discover(1, 'search_tools', {'query': 'q'}),\n"
        "           call(2, 'read', {}),\n"
        "           discover(3, 'describe_tool', {'name': 'x'}),\n"
        "           discover(4, 'describe_namespace', {'name': 3})]\n"
        "send({'type': 'text', 'value': json.dumps(replies)})\n"
        "done()\n"
        "exit_status()\n"
    )
    outcome = _run_fake(
        monkeypatch,
        scenario,
        call_tool=lambda name, arguments: CallResult(ok=True, text="body"),
        discover=discover,
    )

    assert outcome.ok, outcome
    assert json.loads(outcome.texts[0]) == [
        {
            "type": "result",
            "id": 1,
            "ok": True,
            "value": [{"name": "read", "description": "ü"}],
        },
        {"type": "result", "id": 2, "ok": True, "text": "body"},
        {"type": "result", "id": 3, "ok": True, "value": None},
        {
            "type": "result",
            "id": 4,
            "ok": False,
            "error": "describe_namespace() expects a namespace name",
        },
    ]
    # The callback ran on the calling thread, with the guest's arguments.
    assert seen == [
        (threading.current_thread().name, "search_tools", {"query": "q"}),
        (threading.current_thread().name, "describe_tool", {"name": "x"}),
        (threading.current_thread().name, "describe_namespace", {"name": 3}),
    ]
    assert [record.name for record in outcome.calls] == ["read"]


@pytest.mark.parametrize(
    ("message", "fragment"),
    [
        ("{'type': 'discover', 'id': 1, 'name': 'eval', 'args': {}}", "unknown helper"),
        (
            "{'type': 'discover', 'id': 1, 'name': 'search_tools', 'args': []}",
            "a discovery call was malformed",
        ),
        (
            "{'type': 'discover', 'id': 2, 'name': 'search_tools', 'args': {}}",
            "a discovery call had an unexpected id",
        ),
    ],
)
def test_a_malformed_discovery_message_is_a_protocol_violation(
    monkeypatch: pytest.MonkeyPatch,
    spawned: list[int],  # noqa: F811
    message: str,
    fragment: str,
) -> None:
    calls: list[str] = []

    def discover(name: str, args: dict[str, Any]) -> DiscoveryResult:
        calls.append(name)
        return DiscoveryResult(None)

    outcome = _run_fake(
        monkeypatch, f"start()\nsend({message})\nhang()\n", discover=discover
    )
    _sandbox_error(outcome, fragment)
    assert calls == []


def test_a_second_request_while_discovery_is_pending_is_a_violation(
    monkeypatch: pytest.MonkeyPatch,
    spawned: list[int],  # noqa: F811
) -> None:
    scenario = (
        "start()\n"
        "send({'type': 'discover', 'id': 1, 'name': 'describe_tool', 'args': {}})\n"
        "send({'type': 'call', 'id': 2, 'name': 'read', 'args': {}})\n"
        "hang()\n"
    )
    outcome = _run_fake(monkeypatch, scenario, call_tool=_no_tools)
    _sandbox_error(outcome, "while a tool call was pending")
    assert outcome.calls == ()


def test_discovery_failures_become_errors_in_the_script_or_the_sandbox(
    monkeypatch: pytest.MonkeyPatch,
    spawned: list[int],  # noqa: F811
) -> None:
    scenario = (
        "start()\n"
        "reply = discover(1, 'search_tools', {'query': 'q'})\n"
        "send({'type': 'text', 'value': json.dumps(reply)})\n"
        "done()\n"
        "exit_status()\n"
    )

    # Without a callback the helpers raise in the script.
    outcome = _run_fake(monkeypatch, scenario)
    assert outcome.ok, outcome
    assert json.loads(outcome.texts[0]) == {
        "type": "result",
        "id": 1,
        "ok": False,
        "error": "search_tools() is not available",
    }

    # An answer that is not JSON, or an answer or error larger than one reply
    # may be.
    for result, expected in (
        (
            DiscoveryResult(float("nan")),
            "search_tools() returned a value that is not JSON: ValueError",
        ),
        (
            DiscoveryResult({"rows": "x" * 70_000}),
            "more than the 65536-byte limit for one result",
        ),
        (
            DiscoveryResult(error="e" * 70_000),
            "more than the 65536-byte limit for one result",
        ),
    ):

        def answer(
            name: str, args: dict[str, Any], result: DiscoveryResult = result
        ) -> DiscoveryResult:
            return result

        outcome = _run_fake(
            monkeypatch,
            scenario,
            discover=answer,
            limits=ScriptLimits(wall_seconds=5.0, reply_bytes=65536),
        )
        assert outcome.ok, outcome
        reply = json.loads(outcome.texts[0])
        assert reply["ok"] is False and expected in reply["error"]
        assert len(outcome.texts[0]) < 1000

    # A callback that raises, or returns something else, fails the sandbox.
    def broken(name: str, args: dict[str, Any]) -> DiscoveryResult:
        raise RuntimeError("no index")

    outcome = _run_fake(monkeypatch, scenario, discover=broken)
    _sandbox_error(
        outcome, "the search_tools discovery callback failed: RuntimeError: no index"
    )
    outcome = _run_fake(monkeypatch, scenario, discover=lambda name, args: [])  # type: ignore[arg-type, return-value]
    _sandbox_error(outcome, "returned list, not DiscoveryResult")


def test_run_request_keeps_the_first_tool_of_a_name_and_the_given_order(
    monkeypatch: pytest.MonkeyPatch,
    spawned: list[int],  # noqa: F811
) -> None:
    scenario = (
        "run = start()\n"
        "send({'type': 'text', 'value': json.dumps(run['tools'])})\n"
        "reply = call(1, 'mcp_docs_search', {})\n"
        "send({'type': 'text', 'value': reply['error']})\n"
        "done()\n"
        "exit_status()\n"
    )
    monkeypatch.setattr(
        "test_native_codemode_host.ScriptTool",
        lambda name: ScriptTool(name, f"about {name}"),
    )
    outcome = _run_fake(
        monkeypatch,
        scenario,
        tool_names=("mcp__docs-search", "class", "mcp__docs-search"),
    )
    assert outcome.ok, outcome
    assert json.loads(outcome.texts[0]) == [
        {
            "name": "mcp__docs-search",
            "id": "mcp__docs_search",
            "description": "about mcp__docs-search",
        },
        {"name": "class", "id": "class_", "description": "about class"},
    ]
    # The host's own unknown-tool reply speaks in identifiers too.
    assert outcome.texts[1].startswith(
        "tools.mcp_docs_search does not exist. Did you mean tools.mcp__docs_search?"
    )


# --- the discovery answers (CMD-06 to CMD-10) ---------------------------------------


@dataclass(frozen=True)
class _Tool:
    """A callable tool as discovery reads it; names here need not be the
    alphanumeric ones `ToolDefinition` accepts today."""

    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None = None
    namespace: ToolNamespace | None = None


_QUERY = {
    "type": "object",
    "properties": {"query": {"type": "string", "description": "What to look for."}},
    "required": ["query"],
}
_RADIUS = ToolNamespace(
    name="mcp__dev-radius",
    aliases=("dev-radius",),
    description="Product docs",
    instructions="Always search before reading.",
)
_OTHER = ToolNamespace(name="mcp__other")


def _discovery() -> CodemodeDiscovery:
    tools: Any = (
        ToolDefinition("read", "Read a file from disk.", {"type": "object"}),
        _Tool(
            "mcp__dev-radius__search",
            "Search the docs.",
            _QUERY,
            output_schema={
                "type": "object",
                "properties": {"hits": {"type": "array", "items": {"type": "string"}}},
            },
            namespace=_RADIUS,
        ),
        _Tool("mcp__dev-radius__fetch", "Fetch one page.", _QUERY, namespace=_RADIUS),
        _Tool("mcp__other__ping", "Ping the other server.", _QUERY, namespace=_OTHER),
        _Tool("class", "Classify a text.", _QUERY),
    )
    return CodemodeDiscovery(tools)


def _value(discovery: CodemodeDiscovery, helper: str, **arguments: Any) -> Any:
    result = discovery(helper, arguments)
    assert result.error is None, result.error
    return result.value


def _error(discovery: CodemodeDiscovery, helper: str, **arguments: Any) -> str:
    result = discovery(helper, arguments)
    assert result.error is not None and result.value is None
    return result.error


def test_script_tools_carry_the_description_and_the_declaration() -> None:
    # CMD-06: Pi `renderToolSample(toCodemodeDeclaration(tool))`.
    tools = _discovery().script_tools()

    assert [tool.name for tool in tools] == [
        "read",
        "mcp__dev-radius__search",
        "mcp__dev-radius__fetch",
        "mcp__other__ping",
        "class",
    ]
    by_name = {tool.name: tool.description for tool in tools}
    # A tool without an output schema returns its text (Pi TEXT_OUTPUT_SCHEMA).
    assert by_name["read"] == (
        "Read a file from disk.\n\ncodemode tool declaration:\n"
        "```python\ntools.read(args: dict[str, Any]) -> str\n```"
    )
    assert by_name["mcp__dev-radius__search"] == (
        "Search the docs.\n\ncodemode tool declaration:\n```python\n"
        "tools.mcp__dev_radius__search(args: {\n"
        "  # What to look for.\n"
        '  "query": str,\n'
        '}) -> {"hits"?: list[str]}\n```'
    )
    assert "tools.class_(args: " in by_name["class"]
    # Namespace instructions are part of no tool description (CMD-10).
    assert all("Always search" not in text for text in by_name.values())


def test_search_tools_ranks_callable_tools_and_returns_all_tools_entries() -> None:
    # CMD-07, mirror of Pi "finds tools from scripts with searchTools() and
    # describeTool()".
    discovery = _discovery()
    entries = {
        to_codemode_identifier(tool.name): tool.description
        for tool in discovery.script_tools()
    }

    (match,) = _value(discovery, "search_tools", query="search the docs", limit=1)
    assert match == {
        "name": "mcp__dev_radius__search",
        "description": entries["mcp__dev_radius__search"],
    }
    # The default limit is 8, also for an explicit None (Pi `??`).
    everything = _value(discovery, "search_tools", query="docs page file ping text")
    assert len(everything) == 5
    assert _value(discovery, "search_tools", query="docs", limit=None) == _value(
        discovery, "search_tools", query="docs"
    )
    assert _value(discovery, "search_tools", query="zebra") == []
    # The namespace description is searched with each of its tools.
    assert [
        entry["name"] for entry in _value(discovery, "search_tools", query="product")
    ] == ["mcp__dev_radius__search", "mcp__dev_radius__fetch"]


@pytest.mark.parametrize(
    "name", ["mcp__dev-radius", "mcp__dev_radius", "dev-radius", "dev_radius"]
)
def test_namespace_is_matched_by_name_or_alias_as_written_or_as_identifier(
    name: str,
) -> None:
    # CMD-08, CMD-10: the four forms of Pi `isNamespaceName`.
    discovery = _discovery()
    assert is_namespace_name(_RADIUS, name)

    found = _value(discovery, "search_tools", query="ping docs", namespace=name)
    assert [entry["name"] for entry in found] == [
        "mcp__dev_radius__search",
        "mcp__dev_radius__fetch",
    ]
    assert _value(discovery, "describe_namespace", name=name) == {
        "name": "mcp__dev-radius",
        "description": "Product docs",
        "instructions": "Always search before reading.",
        "tools": ["mcp__dev_radius__search", "mcp__dev_radius__fetch"],
    }


def test_namespace_filter_and_description_for_other_names() -> None:
    discovery = _discovery()
    assert not is_namespace_name(_RADIUS, "radius")
    # A namespace no callable tool belongs to matches nothing.
    assert _value(discovery, "search_tools", query="docs", namespace="mcp__nope") == []
    assert _value(discovery, "describe_namespace", name="mcp__nope") is None
    # An empty namespace is no filter (Pi `namespace &&`).
    assert _value(discovery, "search_tools", query="read", namespace="") == _value(
        discovery, "search_tools", query="read"
    )
    # Absent description and instructions are left out.
    assert _value(discovery, "describe_namespace", name="mcp__other") == {
        "name": "mcp__other",
        "tools": ["mcp__other__ping"],
    }


def test_discovery_names_a_tool_by_the_key_that_reaches_it() -> None:
    # Two names with one identifier: what discovery returns for a tool must
    # call that tool, not the one that won the identifier.
    tools: Any = (
        _Tool("read_file", "Read with underscores.", _QUERY, namespace=_OTHER),
        _Tool("read-file", "Read with dashes.", _QUERY, namespace=_OTHER),
        _Tool("class", "Classify a text.", _QUERY),
        # `class_` is the identifier of `class`: no key reaches this tool.
        _Tool("class_", "Shadowed entirely.", _QUERY, namespace=_OTHER),
    )
    discovery = CodemodeDiscovery(tools)
    script_tools = discovery.script_tools()
    keys, entries = prelude.tool_table(
        {
            "name": tool.name,
            "id": to_codemode_identifier(tool.name),
            "description": tool.description,
        }
        for tool in script_tools
    )

    assert [tool.name for tool in script_tools] == ["read_file", "read-file", "class"]
    (dashed,) = _value(discovery, "search_tools", query="dashes")
    assert dashed["name"] == "read-file" and keys[dashed["name"]] == "read-file"
    # The declaration spells the call that reaches the tool.
    assert '```python\ntools["read-file"](args: {' in dashed["description"]
    (underscored,) = _value(discovery, "search_tools", query="underscores")
    assert keys[underscored["name"]] == "read_file"
    assert "```python\ntools.read_file(args: {" in underscored["description"]
    assert _value(discovery, "search_tools", query="shadowed") == []
    # The guest's ALL_TOOLS entries are the ones discovery returns.
    assert dashed in entries and underscored in entries
    assert _value(discovery, "describe_namespace", name="mcp__other")["tools"] == [
        "read_file",
        "read-file",
    ]
    # describe_tool describes what `tools[name]` calls.
    assert (
        _value(discovery, "describe_tool", name="read-file") == (dashed["description"])
    )
    assert (
        _value(discovery, "describe_tool", name="read_file")
        == (underscored["description"])
    )
    assert "Classify" in _value(discovery, "describe_tool", name="class_")


def test_every_declared_call_syntax_reaches_its_tool() -> None:
    # What a declaration tells the model to write must call that tool, also
    # for names the `tools` object cannot offer as attributes.
    names = ("read", "__len__", "__custom__", "class", "9lives", "_state", "items")
    definitions = tuple(
        ToolDefinition(name, f"About {name}.", _QUERY) for name in names
    )
    discovery = CodemodeDiscovery(definitions)
    script_tools = discovery.script_tools()
    keys, entries = prelude.tool_table(
        {
            "name": tool.name,
            "id": to_codemode_identifier(tool.name),
            "description": tool.description,
        }
        for tool in script_tools
    )
    called: list[str] = []
    tools = prelude.Tools(
        keys,
        [entry["name"] for entry in entries],
        lambda name, args: called.append(name),
    )

    for tool in script_tools:
        declaration = tool.description.rpartition("```python\n")[2]
        call_syntax = declaration.partition("(args:")[0]
        eval(call_syntax, {"tools": tools})(query="q")  # noqa: S307 - test input
    assert called == list(names)
    assert _value(discovery, "describe_tool", name="__len__").endswith(
        '```python\ntools["__len__"](args: {\n  # What to look for.\n'
        '  "query": str,\n}) -> str\n```'
    )
    # ALL_TOOLS and search results name the tool by a key that calls it.
    for entry in entries:
        tools[entry["name"]]()
    assert called[len(names) :] == list(names)


def test_describe_tool_takes_a_raw_name_or_an_identifier() -> None:
    # CMD-09.
    discovery = _discovery()
    sample = _value(discovery, "describe_tool", name="mcp__dev-radius__search")
    assert "codemode tool declaration:" in sample
    assert _value(discovery, "describe_tool", name="mcp__dev_radius__search") == sample
    assert _value(discovery, "describe_tool", name="class") == _value(
        discovery, "describe_tool", name="class_"
    )
    assert _value(discovery, "describe_tool", name="nope") is None


def test_discovery_argument_errors_use_pis_messages() -> None:
    # CMD-07: Pi's messages with the Python names.
    discovery = _discovery()
    for query in (None, 3, ["docs"]):
        assert _error(discovery, "search_tools", query=query) == (
            "search_tools() expects a query string"
        )
    assert _error(discovery, "search_tools") == "search_tools() expects a query string"
    for limit in (0, -1, 2.0, "3", True, [1]):
        assert _error(discovery, "search_tools", query="q", limit=limit) == (
            "search_tools() limit must be a positive integer"
        )
    for namespace in (3, ["docs"], False):
        assert _error(discovery, "search_tools", query="q", namespace=namespace) == (
            "search_tools() namespace must be a string"
        )
    assert _error(discovery, "describe_tool", name=None) == (
        "describe_tool() expects a tool name"
    )
    assert _error(discovery, "describe_namespace", name=3) == (
        "describe_namespace() expects a namespace name"
    )
    assert _error(discovery, "eval") == "eval() is not available"


# --- a session with extension tools on the real guest --------------------------------

_EXTENSION = """from pipy_harness.extensions import ExtensionTool, ToolNamespace, ToolResult

_QUERY = {'type': 'object',
          'properties': {'query': {'type': 'string',
                                   'description': 'What to look for.'}},
          'required': ['query'], 'additionalProperties': False}
_HITS = {'type': 'object',
         'properties': {'hits': {'type': 'array', 'items': {'type': 'string'}}}}
DOCS = ToolNamespace(name='mcp__dev-radius', aliases=('dev-radius',),
                     description='Product docs',
                     instructions='Always search before reading.')


def activate(api):
    def search(ctx, params):
        query = params['query']
        return ToolResult(content='2 hits', structured_content={
            'hits': [query + ' guide', query + ' faq']})

    def echo(name):
        return lambda ctx, params: ToolResult(content=name + ':' + params['query'])

    api.register_tool(ExtensionTool(
        name='mcp__dev_radius__search', description='Search the docs.',
        input_schema=_QUERY, handler=search, exposure='codemode',
        namespace=DOCS, output_schema=_HITS))
    api.register_tool(ExtensionTool(
        name='mcp__dev_radius__fetch', description='Fetch one page.',
        input_schema=_QUERY, handler=echo('fetch'), exposure='deferred',
        namespace=DOCS))
    api.register_tool(ExtensionTool(
        name='class', description='Classify a text.', input_schema=_QUERY,
        handler=echo('class'), exposure='codemode'))
    api.register_tool(ExtensionTool(
        name='9lives', description='Count the lives left.', input_schema=_QUERY,
        handler=echo('lives'), exposure='codemode'))
    api.register_tool(ExtensionTool(
        name='orchestrate', description='Search the docs with other tools.',
        input_schema=_QUERY, handler=echo('orchestrate'), exposure='model-only'))
"""


def _write_extension(workspace: Path) -> None:
    directory = workspace / ".pipy" / "extensions"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "tools.py").write_text(_EXTENSION, encoding="utf-8")


def test_script_finds_tools_with_search_tools_and_describe_tool(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runtime: object,  # noqa: F811
) -> None:
    # Pi: "finds tools from scripts with searchTools() and describeTool()".
    _write_extension(tmp_path)
    code = """match = search_tools("search the docs", limit=1)[0]
none = search_tools("docs", namespace="mcp__other")
declaration = describe_tool(match["name"])
result = tools[match["name"]](query="found")
listed = {tool["name"]: tool["description"] for tool in ALL_TOOLS}
text({
    "name": match["name"],
    "sameAsAllTools": listed[match["name"]] == match["description"],
    "none": len(none),
    "declared": "codemode tool declaration:" in declaration,
    "missing": describe_tool("nope") is None,
    "hits": result["hits"],
})
"""

    parent, sink, _ = _run_script(tmp_path, code, monkeypatch)

    assert not parent.is_error, parent.content.value
    assert json.loads(_output(parent)) == {
        "name": "mcp__dev_radius__search",
        "sameAsAllTools": True,
        "none": 0,
        "declared": True,
        "missing": True,
        "hits": ["found guide", "found faq"],
    }
    # Discovery adds no nested call: one record and one event, for the call.
    assert [
        (call["name"], call["arguments"], call["status"])
        for call in parent.details["nestedCalls"]["calls"]
    ] == [("mcp__dev_radius__search", {"query": "found"}, "ok")]
    started = [e for e in sink.events if isinstance(e, NestedToolCallStarted)]
    assert [e.call.provider_correlation_id for e in started] == ["parent/1"]


def test_script_lists_identifiers_namespaces_and_argument_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runtime: object,  # noqa: F811
) -> None:
    _write_extension(tmp_path)
    code = """errors = []
for attempt in (lambda: search_tools(3), lambda: search_tools("q", limit=0),
                lambda: search_tools("q", namespace=3), lambda: describe_tool(None),
                lambda: describe_namespace(1), lambda: search_tools({1}),
                lambda: tools.mcp_dev_radius_search, lambda: tools["nope"]):
    try:
        attempt()
    except (ValueError, TypeError, AttributeError, KeyError) as error:
        errors.append(type(error).__name__ + ": " + str(error))
forms = ("mcp__dev-radius", "mcp__dev_radius", "dev-radius", "dev_radius")
text({
    "ids": [tool["name"] for tool in ALL_TOOLS],
    "errors": errors,
    "namespaces": [describe_namespace(form) for form in forms],
    "filtered": [[tool["name"] for tool in search_tools("docs page", namespace=form)]
                 for form in forms],
    "noNamespace": describe_namespace("mcp__nope"),
    "keyword": [tools.class_(query="a"), tools["class"](query="b")],
    "underscore": tools._lives(query="c"),
    "contains": ["class" in tools, "orchestrate" in tools, "codemode" in tools],
    "modelOnly": describe_tool("orchestrate"),
    "read": describe_tool("read").splitlines()[-2],
})
"""

    parent, _, _ = _run_script(tmp_path, code, monkeypatch)

    assert not parent.is_error, parent.content.value
    output = json.loads(_output(parent))
    # ALL_TOOLS: every callable tool by identifier, in registration order;
    # not `codemode` itself and not the model-only tool (CMD-01, CMD-06).
    assert output["ids"] == [
        "read",
        "ls",
        "grep",
        "find",
        "write",
        "edit",
        "bash",
        "mcp__dev_radius__search",
        "mcp__dev_radius__fetch",
        "class_",
        "_lives",
    ]
    assert output["errors"][:6] == [
        "ValueError: search_tools() expects a query string",
        "ValueError: search_tools() limit must be a positive integer",
        "ValueError: search_tools() namespace must be a string",
        "ValueError: describe_tool() expects a tool name",
        "ValueError: describe_namespace() expects a namespace name",
        "TypeError: search_tools() arguments must be JSON serializable: "
        "Object of type set is not JSON serializable",
    ]
    assert output["errors"][6].startswith(
        "AttributeError: tools.mcp_dev_radius_search does not exist. "
        "Did you mean tools.mcp__dev_radius__search?"
    )
    assert output["errors"][7].startswith("KeyError: 'tools.nope does not exist. ")
    described = {
        "name": "mcp__dev-radius",
        "description": "Product docs",
        "instructions": "Always search before reading.",
        "tools": ["mcp__dev_radius__search", "mcp__dev_radius__fetch"],
    }
    assert output["namespaces"] == [described] * 4
    # "page" is in the description of fetch only, so it ranks first.
    assert (
        output["filtered"]
        == [["mcp__dev_radius__fetch", "mcp__dev_radius__search"]] * 4
    )
    assert output["noNamespace"] is None
    assert output["keyword"] == ["class:a", "class:b"]
    assert output["underscore"] == "lives:c"
    assert output["contains"] == [True, False, False]
    assert output["modelOnly"] is None
    # A built-in returns its text: the declaration says `-> str`.
    assert output["read"] == "}) -> str"
    # Server instructions reach a script only through describe_namespace.
    assert "Always search" not in json.dumps(output["ids"])
