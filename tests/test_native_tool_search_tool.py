"""The `tool_search` tool (MCP parity plan S09).

Rows TSR-01 to TSR-05, TSR-09 and TSR-11, with extension tools in the place
of Pi's MCP tools. The Pi cases mirrored are "registers codemode and
tool_search inactive until they are named"
(``agent-session-tool-orchestration.test.ts``), "activates tool_search for
deferred MCP tools and keeps loaded tools declared on the branch" and "finds
nothing to load when every matching tool is already declared"
(``agent-session-mcp.test.ts``).
"""

from __future__ import annotations

import io
import json
import sys
import threading
from collections.abc import Sequence
from pathlib import Path

import pytest
from test_native_extension_tool_metadata import (
    _result,
    _StubProvider,
    _tool_names,
    _tool_results,
)

from pipy_harness.models import HarnessStatus
from pipy_harness.native.agent import (
    AGENT_TOOL_REQUEST_ID_PREFIX,
    AgentSystemMessage,
    AgentToolCall,
    AgentToolResultMessage,
    AgentUserMessage,
    ProductContent,
)
from pipy_harness.native.coding.session import CodingSession
from pipy_harness.native.models import ProviderToolCall
from pipy_harness.native.session_tree import MessageEntry, NativeSessionTree
from pipy_harness.native.settings import SettingsManager
from pipy_harness.native.system_prompt_sections import BUILTIN_TOOL_PROMPTS
from pipy_harness.native.tool_capabilities import (
    NativeToolCapabilities,
    ToolFilterOptions,
)
from pipy_harness.native.tool_search import (
    DEFAULT_TOOL_SEARCH_LIMIT,
    TOOL_SEARCH_DESCRIPTION,
    TOOL_SEARCH_TOOL_NAME,
    ToolSearchTool,
    tool_search_definition,
)
from pipy_harness.native.tools import (
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
    ToolPort,
    ToolRequest,
)
from pipy_harness.native.tools.base import ToolExposure, ToolNamespace
from pipy_harness.native.tools.registry import (
    ProductionToolRegistry,
    production_tool_registry,
)

_BUILTINS = ("read", "ls", "grep", "find", "write", "edit", "bash")
_OBJECT = {"type": "object"}


class _Tool:
    def __init__(
        self,
        name: str,
        description: str,
        exposure: ToolExposure = "deferred",
        namespace: ToolNamespace | None = None,
    ) -> None:
        self._definition = ToolDefinition(
            name=name,
            description=description,
            input_schema=_OBJECT,
            exposure=exposure,
            namespace=namespace,
            schema_mode="foreign",
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    def invoke(self, request: ToolRequest, context: ToolContext) -> ToolExecutionResult:
        return ToolExecutionResult(
            tool_request_id=request.tool_request_id,
            output_text=f"ran {self._definition.name}",
        )


def _capabilities(
    tmp_path: Path,
    extensions: Sequence[ToolPort] = (),
    *,
    options: ToolFilterOptions | None = None,
    default_tools: tuple[str, ...] | None = None,
) -> NativeToolCapabilities:
    tools = NativeToolCapabilities(
        ProductionToolRegistry(
            {name: _Tool(name, name, "direct") for name in _BUILTINS}
        ),
        {},
        workspace_root=tmp_path,
        filter_options=options or ToolFilterOptions.empty(),
        cancel_join_timeout_seconds=0.1,
        default_tools=lambda: default_tools,
    )
    tools.publish(
        tools.prepare_extensions({tool.definition.name: tool for tool in extensions})
    )
    return tools


def _declared(tools: NativeToolCapabilities) -> tuple[str, ...]:
    return tuple(definition.name for definition in tools.definitions())


def _search(
    tools: NativeToolCapabilities, arguments: dict[str, object]
) -> AgentToolResultMessage:
    """Call `tool_search` through the executor, as a model call runs."""

    outcome = tools.execute(
        AgentToolCall(
            provider_correlation_id="provider-search",
            tool_name=TOOL_SEARCH_TOOL_NAME,
            arguments_json=ProductContent(json.dumps(arguments)),
        )
    )
    assert outcome.result is not None
    return outcome.result


_DOCS = ToolNamespace(name="mcp__docs", description="Product documentation")


def _deferred_fixture() -> tuple[_Tool, ...]:
    return (
        _Tool("docs_search", "Search the docs.\nSecond line.", namespace=_DOCS),
        _Tool("docs_fetch", "  Fetch one page.\r\nMore.", "codemode", _DOCS),
        _Tool("issue_create", "Create an issue.", "deferred"),
        _Tool("shown", "Search the docs directly.", "direct"),
        _Tool("orchestrate", "Search the docs with a plan.", "model-only"),
        _Tool("withdrawn", "Search the docs no more.", "hidden"),
    )


# -- TSR-01, TSR-02: the declaration ---------------------------------------


def test_definition_is_the_fixed_pi_declaration() -> None:
    definition = tool_search_definition()

    assert definition.name == "tool_search"
    assert definition.description == TOOL_SEARCH_DESCRIPTION
    assert TOOL_SEARCH_DESCRIPTION == (
        "# Tool discovery\n\nSearches over deferred tool metadata with BM25 and "
        "exposes matching tools for the next model call.\n\nSome of the tools, "
        "such as tools of MCP servers, may not have been provided to you "
        "upfront, and you should use this tool (`tool_search`) to search for "
        "the required tools. For MCP tool discovery, always use `tool_search`."
    )
    assert definition.input_schema == {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query for deferred tools.",
            },
            "limit": {
                "type": "number",
                "description": "Maximum number of tools to return. Defaults to 8.",
            },
        },
        "required": ["query"],
    }
    assert DEFAULT_TOOL_SEARCH_LIMIT == 8
    assert definition.exposure == "model-only"
    assert definition.default_active is False
    assert definition.reaches_tools is not None
    assert definition.reaches_tools({"query": "anything"}) == "all"
    assert BUILTIN_TOOL_PROMPTS["tool_search"].snippet == (
        "Search for tools that are not loaded yet and load the matches"
    )
    assert BUILTIN_TOOL_PROMPTS["tool_search"].guidelines == ()


def test_default_active_must_be_a_bool() -> None:
    with pytest.raises(ValueError, match="default_active"):
        ToolDefinition(
            name="x",
            description="x",
            input_schema=_OBJECT,
            default_active=0,  # type: ignore[arg-type]
        )


def test_registered_inactive_until_named(tmp_path: Path) -> None:
    # Pi: "registers codemode and tool_search inactive until they are named".
    plain = _capabilities(tmp_path)
    assert "tool_search" in [tool.name for tool in plain.all_tools()]
    assert _declared(plain) == _BUILTINS
    assert plain.unknown_filter_names == ()

    # --tools and the defaultTools setting name it explicitly.
    allowed = _capabilities(
        tmp_path, options=ToolFilterOptions(allow=("read", "tool_search"))
    )
    assert _declared(allowed) == ("read", "tool_search")
    modified = _capabilities(
        tmp_path, options=ToolFilterOptions(modifiers=("+tool_search", "-bash"))
    )
    assert _declared(modified) == (*_BUILTINS[:-1], "tool_search")
    configured = _capabilities(tmp_path, default_tools=("read", "tool_search"))
    assert _declared(configured) == ("read", "tool_search")

    # A filter that does not name it leaves it inactive.
    excluded = _capabilities(tmp_path, options=ToolFilterOptions(exclude=("bash",)))
    assert _declared(excluded) == _BUILTINS[:-1]

    # An extension names it with set_active_tools.
    assert plain.set_active_tools(["read", "tool_search"])
    assert _declared(plain) == ("read", "tool_search")


def test_stays_inactive_and_registered_across_a_reload(tmp_path: Path) -> None:
    tools = _capabilities(tmp_path)
    port = tools.state.registry["tool_search"]

    tools.publish(tools.prepare_extensions({"new": _Tool("new", "New.", "direct")}))

    assert _declared(tools) == (*_BUILTINS, "new")
    assert tools.state.registry["tool_search"] is port


def test_scripts_cannot_call_it(tmp_path: Path) -> None:
    tools = _capabilities(
        tmp_path, options=ToolFilterOptions(modifiers=("+tool_search",))
    )

    assert "tool_search" in _declared(tools)
    assert "tool_search" not in tools.callable_snapshot().callable_names()


def test_only_the_production_registry_registers_it(tmp_path: Path) -> None:
    tools = NativeToolCapabilities(
        {"read": _Tool("read", "read", "direct")},
        {},
        workspace_root=tmp_path,
        filter_options=ToolFilterOptions.empty(),
        cancel_join_timeout_seconds=0.1,
    )
    tools.publish(tools.prepare_extensions({}))

    assert tools.registered_names == ("read",)


# -- TSR-03: what a search may load ----------------------------------------


def test_loadable_tools_are_the_inactive_codemode_and_deferred_ones(
    tmp_path: Path,
) -> None:
    tools = _capabilities(tmp_path, _deferred_fixture())

    assert [tool.name for tool in tools.loadable_tools()] == [
        "docs_search",
        "docs_fetch",
        "issue_create",
    ]

    assert tools.load_tools(["issue_create", "shown", "absent", "issue_create"]) == (
        "issue_create",
    )
    assert [tool.name for tool in tools.loadable_tools()] == [
        "docs_search",
        "docs_fetch",
    ]
    # Loading adds to the declared tools and keeps the others.
    assert _declared(tools) == (*_BUILTINS, "issue_create", "shown", "orchestrate")
    assert tools.load_tools(["issue_create"]) == ()


def test_a_tool_the_filter_does_not_keep_is_not_loadable(tmp_path: Path) -> None:
    tools = _capabilities(
        tmp_path,
        _deferred_fixture(),
        options=ToolFilterOptions(exclude=("docs_search",)),
    )

    assert [tool.name for tool in tools.loadable_tools()] == [
        "docs_fetch",
        "issue_create",
    ]
    assert tools.load_tools(["docs_search"]) == ()
    assert "docs_search" not in _declared(tools)


def test_loading_adds_to_the_selection_that_is_live(tmp_path: Path) -> None:
    # A selection made after the tools were ranked is kept, not overwritten.
    tools = _capabilities(tmp_path, _deferred_fixture())
    ranked = [tool.name for tool in tools.loadable_tools()]

    assert tools.set_active_tools(["read", "docs_fetch"])
    assert tools.load_tools(ranked) == ("docs_search", "issue_create")

    assert _declared(tools) == ("read", "docs_search", "docs_fetch", "issue_create")


def test_loading_does_not_stop_a_reload_from_activating_new_tools(
    tmp_path: Path,
) -> None:
    fixture = {tool.definition.name: tool for tool in _deferred_fixture()}
    tools = _capabilities(
        tmp_path,
        tuple(fixture.values()),
        options=ToolFilterOptions(modifiers=("+tool_search",)),
    )
    assert tools.load_tools(["docs_search"]) == ("docs_search",)

    tools.publish(
        tools.prepare_extensions({**fixture, "new": _Tool("new", "New.", "direct")})
    )

    declared = _declared(tools)
    assert "new" in declared and "docs_search" in declared


def test_concurrent_loads_and_selections_lose_no_load(tmp_path: Path) -> None:
    names = [f"tool_{index}" for index in range(40)]
    tools = _capabilities(tmp_path, [_Tool(name, "A tool.") for name in names])
    start = threading.Barrier(5)

    def load(part: list[str]) -> None:
        start.wait()
        for name in part:
            assert tools.load_tools([name]) == (name,)

    workers = [
        threading.Thread(target=load, args=(names[index::5],)) for index in range(5)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()

    assert set(names) <= set(_declared(tools))
    assert tools.loadable_tools() == ()


# -- TSR-03, TSR-04, TSR-11: the call --------------------------------------


def test_loads_the_matches_and_reports_first_description_lines(
    tmp_path: Path,
) -> None:
    tools = _capabilities(
        tmp_path,
        _deferred_fixture(),
        options=ToolFilterOptions(modifiers=("+tool_search",)),
    )
    before = tools.definitions()

    result = _search(tools, {"query": "documentation"})

    assert not result.is_error
    # The namespace description ranks both tools of the namespace.
    assert result.content.value == (
        "Loaded 2 tools. They are available from your next call:\n"
        "- docs_search: Search the docs.\n"
        "- docs_fetch: Fetch one page."
    )
    assert result.details == {"loaded": ["docs_search", "docs_fetch"]}
    after = tools.definitions()
    assert {tool.name for tool in after} == {
        *(tool.name for tool in before),
        "docs_search",
        "docs_fetch",
    }
    # The description does not depend on what is registered or loaded.
    assert [tool for tool in after if tool.name == "tool_search"] == [
        tool for tool in before if tool.name == "tool_search"
    ]


def test_limit_bounds_the_loaded_tools(tmp_path: Path) -> None:
    tools = _capabilities(
        tmp_path,
        _deferred_fixture(),
        options=ToolFilterOptions(modifiers=("+tool_search",)),
    )

    # A JSON number that is an integer is one, also when it arrives as 1.0.
    result = _search(tools, {"query": "search the docs", "limit": 1.0})

    assert result.content.value == (
        "Loaded 1 tool. They are available from your next call:\n"
        "- docs_search: Search the docs."
    )
    assert result.details == {"loaded": ["docs_search"]}
    assert "docs_fetch" not in _declared(tools)


def test_finds_nothing_when_every_match_is_already_declared(tmp_path: Path) -> None:
    # Pi: "finds nothing to load when every matching tool is already declared".
    tools = _capabilities(
        tmp_path,
        [_Tool("docs_search", "Search the docs.", "direct")],
        options=ToolFilterOptions(modifiers=("+tool_search",)),
    )

    result = _search(tools, {"query": "docs"})

    assert not result.is_error
    assert result.content.value == "No matching tools found."
    assert result.details == {"loaded": []}

    # A loaded tool is declared, so a second search does not find it again.
    loading = _capabilities(
        tmp_path,
        [_Tool("docs_search", "Search the docs.")],
        options=ToolFilterOptions(modifiers=("+tool_search",)),
    )
    first = _search(loading, {"query": "docs"})
    second = _search(loading, {"query": "docs"})
    assert first.details == {"loaded": ["docs_search"]}
    assert second.content.value == "No matching tools found."


def test_never_loads_direct_model_only_or_hidden_tools(tmp_path: Path) -> None:
    tools = _capabilities(
        tmp_path,
        _deferred_fixture(),
        options=ToolFilterOptions(modifiers=("+tool_search",)),
    )
    assert tools.set_active_tools(["tool_search"])

    result = _search(tools, {"query": "search docs plan directly more", "limit": 50})

    assert result.details == {"loaded": ["docs_search", "docs_fetch"]}
    assert _declared(tools) == ("tool_search", "docs_search", "docs_fetch")


def test_without_session_tools_it_finds_nothing() -> None:
    request = ToolRequest(
        tool_request_id=f"{AGENT_TOOL_REQUEST_ID_PREFIX}1",
        tool_name="tool_search",
        arguments={"query": "docs"},
    )

    result = ToolSearchTool().invoke(request, ToolContext(workspace_root=Path.cwd()))

    assert result.output_text == "No matching tools found."
    assert result.details == {"loaded": []}


def test_a_listing_longer_than_a_result_is_counted_not_cut(tmp_path: Path) -> None:
    line = "x" * 30_000
    names = [f"big_{index}" for index in range(4)]
    tools = _capabilities(
        tmp_path,
        [_Tool(name, f"{line} match") for name in names],
        options=ToolFilterOptions(modifiers=("+tool_search",)),
    )

    result = _search(tools, {"query": "match"})

    assert not result.is_error
    text = result.content.value
    assert len(text) <= ToolExecutionResult.OUTPUT_TEXT_MAX_LENGTH
    assert text.startswith("Loaded 4 tools. They are available from your next call:\n")
    assert text.count("\n- big_") == 2
    assert text.endswith("\n(and 2 more)")
    assert result.details == {"loaded": names}
    assert set(names) <= set(_declared(tools))


# -- TSR-05: argument errors -----------------------------------------------


@pytest.mark.parametrize("query", ["", "   ", "\n\t", "﻿ "])
def test_an_empty_query_fails(tmp_path: Path, query: str) -> None:
    tools = _capabilities(tmp_path, _deferred_fixture())

    result = _search(tools, {"query": query})

    assert result.is_error
    assert result.content.value == "query must not be empty"
    assert tools.loadable_tools() != ()


@pytest.mark.parametrize("limit", [0, -1, 2.5, -3.0, 0.0])
def test_a_limit_that_is_not_a_positive_integer_fails(
    tmp_path: Path, limit: float
) -> None:
    tools = _capabilities(tmp_path, _deferred_fixture())

    result = _search(tools, {"query": "docs", "limit": limit})

    assert result.is_error
    assert result.content.value == "limit must be a positive integer"
    assert len(tools.loadable_tools()) == 3


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"query": [3]},
        {"query": "docs", "limit": "three"},
        {"query": "docs", "limit": [1]},
    ],
)
def test_arguments_of_the_wrong_type_are_refused_by_the_schema(
    tmp_path: Path, arguments: dict[str, object]
) -> None:
    tools = _capabilities(tmp_path, _deferred_fixture())

    result = _search(tools, arguments)

    assert result.is_error
    assert len(tools.loadable_tools()) == 3


def test_a_limit_sent_as_a_string_is_converted(tmp_path: Path) -> None:
    # Pi ``validateToolArguments`` coerces ``"1"`` to ``1`` for the foreign
    # schema of ``tool_search`` (DEV-10 since G6).
    tools = _capabilities(tmp_path, _deferred_fixture())

    result = _search(tools, {"query": "docs", "limit": "1"})

    assert not result.is_error
    assert len(tools.loadable_tools()) == 2


# -- TSR-09 and the session ------------------------------------------------

_EXTENSION = """from pipy_harness.extensions import ExtensionTool, ToolNamespace, ToolResult

_QUERY = {'type': 'object', 'properties': {'query': {'type': 'string'}},
          'required': ['query'], 'additionalProperties': False}
_DOCS = ToolNamespace(name='mcp__docs', description='Product documentation')


def activate(api):
    api.register_tool(ExtensionTool(
        name='docs_search', description='Search the docs.\\nSecond line.',
        input_schema=_QUERY, namespace=_DOCS, exposure='deferred',
        handler=lambda ctx, params: ToolResult(content='found ' + params['query'])))
    api.register_tool(ExtensionTool(
        name='docs_fetch', description='Fetch one page of the docs.',
        input_schema=_QUERY, namespace=_DOCS, exposure='deferred',
        handler=lambda ctx, params: ToolResult(content='page')))
"""


def _settings(root: Path, default_tools: list[str] | None) -> SettingsManager:
    overrides: dict[str, object] = {"theme": "pi"}
    if default_tools is not None:
        overrides["defaultTools"] = default_tools
    return SettingsManager(
        global_path=root / "global.json",
        project_path=root / "project.json",
        overrides=overrides,
    )


def _session_run(
    workspace: Path,
    provider: _StubProvider,
    prompts: str,
    *,
    default_tools: list[str] | None,
    tree: NativeSessionTree | None = None,
):
    session = CodingSession(
        provider=provider,
        tool_registry=production_tool_registry(),
        settings_manager=_settings(workspace, default_tools),
        native_session=tree,
        tool_budget=5,
    )
    return session.run(
        workspace_root=workspace,
        input_stream=io.StringIO(prompts),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )


def _write_extension(workspace: Path) -> None:
    directory = workspace / ".pipy" / "extensions"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "docs.py").write_text(_EXTENSION, encoding="utf-8")


def test_session_does_not_declare_it_until_it_is_named(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    _write_extension(tmp_path)

    plain = _StubProvider([_result(final_text="done")])
    _session_run(tmp_path, plain, "go\n", default_tools=None)
    assert _tool_names(plain.requests[0]) == list(_BUILTINS)

    named = _StubProvider([_result(final_text="done")])
    _session_run(tmp_path, named, "go\n", default_tools=["+tool_search"])
    assert _tool_names(named.requests[0]) == [*_BUILTINS, "tool_search"]
    declared = named.requests[0].available_tools[-1]
    assert declared.description == TOOL_SEARCH_DESCRIPTION
    assert declared.input_schema == tool_search_definition().input_schema


@pytest.mark.parametrize("selection", ["none", "cli", "settings"])
def test_cli_names_it_and_the_prompt_lists_it(
    tmp_path: Path, monkeypatch, selection: str
) -> None:
    from pipy_harness import cli
    from pipy_harness.native.fake import AutomationFakeProvider

    config = tmp_path / "config"
    config.mkdir()
    if selection == "settings":
        (config / "settings.json").write_text(
            json.dumps({"defaultTools": ["+tool_search"]})
        )
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(config))
    monkeypatch.setenv("PIPY_NATIVE_DEFAULTS_PATH", str(tmp_path / "defaults.json"))
    monkeypatch.setenv("PIPY_AUTH_DIR", str(tmp_path / "auth"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("PIPY_OFFLINE", "1")
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    requests = []
    original = AutomationFakeProvider.complete

    def complete(self, request, **kwargs):
        requests.append(request)
        return original(self, request, **kwargs)

    monkeypatch.setattr(AutomationFakeProvider, "complete", complete)
    args = ["repl", "--cwd", str(tmp_path), "--native-provider", "fake"]
    args += ["--native-model", "fake-tools", "--no-session", "--offline"]
    if selection == "cli":
        args += ["--tools", "+tool_search"]

    assert cli.main([*args, "--print", "hello"]) == 0

    named = selection != "none"
    assert ("tool_search" in _tool_names(requests[0])) == named
    line = (
        "- tool_search: Search for tools that are not loaded yet and load the matches\n"
    )
    assert (line in requests[0].system_prompt) == named


def test_session_loads_a_deferred_tool_and_keeps_it_declared_on_the_branch(
    tmp_path: Path, monkeypatch
) -> None:
    # Pi: "activates tool_search for deferred MCP tools and keeps loaded tools
    # declared on the branch".
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    _write_extension(tmp_path)
    tree = NativeSessionTree.create(tmp_path, session_dir=tmp_path / "saved")
    provider = _StubProvider(
        [
            _result(
                tool_calls=(
                    ProviderToolCall(
                        "c1", "tool_search", '{"query":"search the docs","limit":1}'
                    ),
                )
            ),
            _result(
                tool_calls=(
                    ProviderToolCall("c2", "docs_search", '{"query":"loaded"}'),
                )
            ),
            _result(final_text="done"),
        ]
    )

    result = _session_run(
        tmp_path,
        provider,
        "find a docs tool\n",
        default_tools=["tool_search"],
        tree=tree,
    )

    assert result.status is HarnessStatus.SUCCEEDED
    first, second, third = provider.requests
    assert _tool_names(first) == ["tool_search"]
    assert _tool_names(second) == ["tool_search", "docs_search"]
    assert _tool_names(third) == ["tool_search", "docs_search"]
    # The description does not depend on the registered or loaded tools.
    assert {
        tool.description
        for request in provider.requests
        for tool in request.available_tools
        if tool.name == "tool_search"
    } == {TOOL_SEARCH_DESCRIPTION}

    search, loaded = _tool_results(third)
    assert not search.is_error
    assert search.content.value == (
        "Loaded 1 tool. They are available from your next call:\n"
        "- docs_search: Search the docs."
    )
    assert search.details == {"loaded": ["docs_search"]}
    assert not loaded.is_error and loaded.content.value == "found loaded"

    # Only the loaded tool is added; earlier declarations are not repeated.
    entries = [entry for entry in tree.get_branch() if isinstance(entry, MessageEntry)]
    loads = [
        entry.message
        for entry in entries
        if isinstance(entry.message, AgentSystemMessage)
        and any(tool.name == "docs_search" for tool in entry.message.tools_added)
    ]
    assert len(loads) == 1
    assert [tool.name for tool in loads[0].tools_added] == ["docs_search"]
    assert loads[0].tools_removed == ()

    # Loads are recorded in the transcript: navigating back before the load
    # drops the tool, navigating to a later entry restores it.
    first_user = next(
        entry for entry in entries if isinstance(entry.message, AgentUserMessage)
    )
    last = entries[-1]
    reopened = NativeSessionTree.open(next((tmp_path / "saved").glob("*.jsonl")))
    navigated = _StubProvider(
        [
            _result(final_text="before"),
            _result(final_text="after"),
            _result(final_text="cloned"),
        ]
    )
    _session_run(
        tmp_path,
        navigated,
        f"/tree select {first_user.id}\nagain\n/tree select {last.id}\nnext\n/clone\nmore\n/exit\n",
        default_tools=["tool_search"],
        tree=reopened,
    )
    before, after, cloned = navigated.requests
    assert _tool_names(before) == ["tool_search"]
    assert _tool_names(after) == ["tool_search", "docs_search"]
    # A clone of the branch is a new session file with the same loadout.
    assert len(list((tmp_path / "saved").glob("*.jsonl"))) == 2
    assert _tool_names(cloned) == ["tool_search", "docs_search"]
