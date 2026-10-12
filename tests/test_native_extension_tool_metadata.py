"""Extension tool metadata (MCP parity plan S02, rows EXT-01 to EXT-05).

An extension tool declares `exposure`, `namespace`, `annotations`,
`output_schema` and `reaches_tools`; its result can carry
`structured_content` and `is_error`; `ctx.get_all_tools()` reports every
registered tool with that metadata; and registering one of the extension's own
tool names again replaces the earlier registration, so `exposure="hidden"`
withdraws a tool.
"""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pipy_harness.extensions import (
    ExtensionCapabilityError,
    ExtensionTool,
    ToolAnnotations,
    ToolInfo,
    ToolNamespace,
    ToolResult,
)
from pipy_harness.models import HarnessStatus
from pipy_harness.native.agent import AgentToolResultMessage
from pipy_harness.native.coding.session import (
    CodingSession,
    production_tool_registry,
)
from pipy_harness.native.extension_types import (
    REASON_INVALID_TOOL,
    ExtensionModelRuntimeControl,
    RegisteredTool,
)
from pipy_harness.native.extensions.activation import activate_extensions
from pipy_harness.native.extensions.collectors import extension_tools
from pipy_harness.native.extensions.command_context import make_extension_context
from pipy_harness.native.extensions.packages import discover_extensions
from pipy_harness.native.extensions.tool_port import _ExtensionToolPort
from pipy_harness.native.models import (
    ProviderRequest,
    ProviderResult,
    ProviderToolCall,
)
from pipy_harness.native.tool_capabilities import (
    NativeToolCapabilities,
    ToolFilterOptions,
)
from pipy_harness.native.tools import (
    ToolContext,
    ToolRequest,
    make_tool_request_id,
)
from pipy_harness.native.tools.codemode import CodemodeTool, script_reach

_HEADER = (
    "import json\n"
    "from pathlib import Path\n"
    "from pipy_harness.extensions import (\n"
    "    ExtensionTool, ToolAnnotations, ToolBlock, ToolNamespace, ToolResult,\n"
    ")\n"
)


def _write(workspace: Path, name: str, body: str) -> None:
    directory = workspace / ".pipy" / "extensions"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.py").write_text(_HEADER + body, encoding="utf-8")


def _activate(workspace: Path) -> list:
    return activate_extensions(
        discover_extensions(
            workspace,
            config_home_env={},
            home_dir=workspace,
            include_workspace_defaults=True,
        )
    )


class _StubProvider:
    name = "stub"
    model_id = "stub-model"
    supports_mid_convo_system_messages = True

    def __init__(self, results: list[ProviderResult]) -> None:
        self._results = list(results)
        self.requests: list[ProviderRequest] = []

    @property
    def supports_tool_calls(self) -> bool:
        return True

    def complete(self, request: ProviderRequest, **_kwargs: object) -> ProviderResult:
        self.requests.append(request)
        return self._results.pop(0)


def _result(*, tool_calls=(), final_text=None) -> ProviderResult:
    now = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
    return ProviderResult(
        status=HarnessStatus.SUCCEEDED,
        provider_name="stub",
        model_id="stub-model",
        started_at=now,
        ended_at=now,
        final_text=final_text,
        tool_calls=tool_calls,
    )


def _run(workspace: Path, provider: _StubProvider, prompts: str):
    session = CodingSession(
        provider=provider, tool_registry=production_tool_registry(), tool_budget=5
    )
    return session.run(
        workspace_root=workspace,
        input_stream=io.StringIO(prompts),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )


def _tool_names(request: ProviderRequest) -> list[str]:
    return [tool.name for tool in request.available_tools]


def _tool_results(request: ProviderRequest) -> list[AgentToolResultMessage]:
    return [
        message
        for message in request.messages
        if isinstance(message, AgentToolResultMessage)
    ]


# -- EXT-01, EXT-05: declaration ------------------------------------------

_FULL_TOOL = (
    "def activate(api):\n"
    "    api.register_tool(ExtensionTool(\n"
    "        name='lookup',\n"
    "        description='Look a record up.',\n"
    "        input_schema={'type': 'object'},\n"
    "        handler=lambda ctx, params: ToolResult(content='ok'),\n"
    "        exposure='codemode',\n"
    "        namespace=ToolNamespace(\n"
    "            name='records', aliases=('rec',),\n"
    "            description='Record store', instructions='Search first.'),\n"
    "        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),\n"
    "        output_schema={'type': 'object', 'properties': {\n"
    "            'score': {'type': 'number'},\n"
    "            'tags': {'anyOf': [{'type': 'null'}, {'type': 'array'}]}}},\n"
    "        reaches_tools=lambda params: ('read', str(params.get('name'))),\n"
    "    ))\n"
)


def test_activation_accepts_every_metadata_field(tmp_path: Path) -> None:
    _write(tmp_path, "full", _FULL_TOOL)

    (registered,) = extension_tools(_activate(tmp_path))
    tool = registered.tool

    assert tool.exposure == "codemode"
    assert tool.namespace == ToolNamespace(
        name="records",
        aliases=("rec",),
        description="Record store",
        instructions="Search first.",
    )
    assert tool.annotations == ToolAnnotations(
        read_only_hint=True, open_world_hint=False
    )
    # An output schema is any JSON Schema object, not pipy's strict subset.
    assert tool.output_schema == {
        "type": "object",
        "properties": {
            "score": {"type": "number"},
            "tags": {"anyOf": [{"type": "null"}, {"type": "array"}]},
        },
    }
    assert tool.reaches_tools is not None
    assert tool.reaches_tools({"name": "grep"}) == ("read", "grep")

    definition = _ExtensionToolPort(registered, has_ui=False).definition
    assert definition.exposure == "codemode"
    assert definition.namespace == tool.namespace
    assert definition.annotations == tool.annotations
    assert definition.output_schema == tool.output_schema
    assert definition.reaches_tools is tool.reaches_tools
    assert definition.schema_mode == "strict"


def test_metadata_defaults_describe_a_plain_tool(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "plain",
        "def activate(api):\n"
        "    api.register_tool(ExtensionTool(\n"
        "        name='plain', description='plain', input_schema={'type': 'object'},\n"
        "        handler=lambda ctx, params: ToolResult(content='ok')))\n",
    )

    (registered,) = extension_tools(_activate(tmp_path))

    assert registered.tool.exposure == "direct"
    assert registered.tool.namespace is None
    assert registered.tool.annotations is None
    assert registered.tool.output_schema is None
    assert registered.tool.reaches_tools is None


def test_output_schema_is_detached_at_registration(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "detached",
        "SCHEMA = {'type': 'object', 'properties': {'n': {'type': 'integer'}}}\n"
        "def activate(api):\n"
        "    api.register_tool(ExtensionTool(\n"
        "        name='detached', description='d', input_schema={'type': 'object'},\n"
        "        handler=lambda ctx, params: ToolResult(content='ok'),\n"
        "        output_schema=SCHEMA))\n"
        "    SCHEMA['properties']['n']['type'] = 'string'\n",
    )

    (registered,) = extension_tools(_activate(tmp_path))

    assert registered.tool.output_schema == {
        "type": "object",
        "properties": {"n": {"type": "integer"}},
    }


@pytest.mark.parametrize(
    "field",
    [
        "exposure='public'",
        "exposure=None",
        "exposure=['direct']",
        "namespace={'name': 'records'}",
        "namespace='records'",
        "annotations={'readOnlyHint': True}",
        "annotations=True",
        "output_schema='object'",
        "output_schema=['object']",
        "output_schema={'type': 'object', 'default': {1, 2}}",
        "output_schema={'type': 'object', 'maximum': float('nan')}",
        "reaches_tools='all'",
        "reaches_tools=('read',)",
    ],
)
def test_activation_rejects_a_wrong_metadata_shape(tmp_path: Path, field: str) -> None:
    _write(
        tmp_path,
        "bad",
        "def activate(api):\n"
        "    api.register_tool(ExtensionTool(\n"
        "        name='bad', description='bad', input_schema={'type': 'object'},\n"
        "        handler=lambda ctx, params: ToolResult(content='ok'),\n"
        f"        {field}))\n",
    )

    (activated,) = _activate(tmp_path)

    assert activated.status == "disabled"
    assert activated.reason == REASON_INVALID_TOOL
    assert not extension_tools([activated])


# -- EXT-02: result fields ------------------------------------------------


def _port(handler) -> _ExtensionToolPort:
    tool = ExtensionTool(
        name="probe",
        description="probe",
        input_schema={"type": "object"},
        handler=handler,
    )
    return _ExtensionToolPort(RegisteredTool(tool=tool, extension="t"), has_ui=False)


def _invoke(port: _ExtensionToolPort, tmp_path: Path):
    return port.invoke(
        ToolRequest(
            tool_request_id=make_tool_request_id(), tool_name="probe", arguments={}
        ),
        ToolContext(workspace_root=tmp_path),
    )


def test_result_carries_structured_content_and_is_error(tmp_path: Path) -> None:
    data = {"rows": [1, 2.5, None], "ok": False}
    port = _port(
        lambda ctx, params: ToolResult(
            content="partial failure",
            details={"attempt": 1},
            structured_content=data,
            is_error=True,
        )
    )

    result = _invoke(port, tmp_path)

    assert result.output_text == "partial failure"
    assert result.is_error is True
    assert result.details == {"attempt": 1}
    assert result.structured_content == data
    # The result keeps a copy, not the handler's own object.
    assert result.structured_content is not data


@pytest.mark.parametrize("value", [0, False, "", [], {}, "text", 3.5, [1, {"a": None}]])
def test_structured_content_is_any_json_value(tmp_path: Path, value: object) -> None:
    port = _port(lambda ctx, params: ToolResult(content="x", structured_content=value))

    result = _invoke(port, tmp_path)

    assert result.structured_content == value
    assert type(result.structured_content) is type(value)
    assert result.is_error is False


@pytest.mark.parametrize(
    "value", [{1: "a"}, {"a": {1, 2}}, float("nan"), object(), b"bytes"]
)
def test_structured_content_that_is_not_json_is_dropped(
    tmp_path: Path, value: object
) -> None:
    port = _port(lambda ctx, params: ToolResult(content="x", structured_content=value))

    result = _invoke(port, tmp_path)

    assert result.output_text == "x"
    assert result.structured_content is None


def test_plain_results_stay_successes_without_structured_content(
    tmp_path: Path,
) -> None:
    for returned in (ToolResult(content="x"), "x"):
        result = _invoke(_port(lambda ctx, params, value=returned: value), tmp_path)

        assert result.is_error is False
        assert result.structured_content is None


def test_is_error_result_reaches_the_model_as_an_error(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    _write(
        tmp_path,
        "failing",
        "def activate(api):\n"
        "    api.register_tool(ExtensionTool(\n"
        "        name='lookup', description='look up',\n"
        "        input_schema={'type': 'object'},\n"
        "        handler=lambda ctx, params: ToolResult(\n"
        "            content='record 7 is locked', is_error=True,\n"
        "            structured_content={'locked': [7]}),\n"
        "        output_schema={'type': 'object'}))\n",
    )
    call = ProviderToolCall("c1", "lookup", "{}")
    provider = _StubProvider([_result(tool_calls=(call,)), _result(final_text="ok")])

    result = _run(tmp_path, provider, "look it up\n")

    assert result.status is HarnessStatus.SUCCEEDED
    (tool_result,) = _tool_results(provider.requests[1])
    assert tool_result.is_error is True
    assert tool_result.content.value == "record 7 is locked"
    assert tool_result.structured_content == {"locked": [7]}


# -- EXT-03: get_all_tools ------------------------------------------------

_REPORTING = (
    "def _dump(ctx, name):\n"
    "    rows = [\n"
    "        {'name': t.name, 'exposure': t.exposure,\n"
    "         'namespace': t.namespace.name if t.namespace else None,\n"
    "         'read_only': t.annotations.read_only_hint if t.annotations else None,\n"
    "         'reaches': t.reaches_tools is not None}\n"
    "        for t in ctx.get_all_tools()\n"
    "    ]\n"
    "    (Path(ctx.cwd) / name).write_text(json.dumps(rows))\n"
    "def activate(api):\n"
    "    api.register_command('dump', 'dump tools', lambda ctx, args: _dump(ctx, 'command.json'))\n"
    "    def reporter(ctx, params):\n"
    "        _dump(ctx, 'tool.json')\n"
    "        return ToolResult(content='dumped')\n"
    "    api.register_tool(ExtensionTool(\n"
    "        name='reporter', description='report tools',\n"
    "        input_schema={'type': 'object'}, handler=reporter))\n"
    "    api.register_tool(ExtensionTool(\n"
    "        name='lookup', description='look up',\n"
    "        input_schema={'type': 'object'},\n"
    "        handler=lambda ctx, params: ToolResult(content='ok'),\n"
    "        exposure='deferred',\n"
    "        namespace=ToolNamespace(name='records'),\n"
    "        annotations=ToolAnnotations(read_only_hint=True),\n"
    "        reaches_tools=lambda params: 'all'))\n"
    "    api.register_tool(ExtensionTool(\n"
    "        name='withdrawn', description='gone',\n"
    "        input_schema={'type': 'object'},\n"
    "        handler=lambda ctx, params: ToolResult(content='ok'),\n"
    "        exposure='hidden'))\n"
)


def test_get_all_tools_reports_every_registered_tool(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    _write(tmp_path, "reporting", _REPORTING)
    call = ProviderToolCall("c1", "reporter", "{}")
    provider = _StubProvider([_result(tool_calls=(call,)), _result(final_text="ok")])

    result = _run(tmp_path, provider, "/dump\nreport\n")

    assert result.status is HarnessStatus.SUCCEEDED
    from_command = json.loads((tmp_path / "command.json").read_text())
    from_tool = json.loads((tmp_path / "tool.json").read_text())
    # A command context and a tool handler context read the same registry.
    assert from_command == from_tool
    by_name = {row["name"]: row for row in from_command}
    assert [row["name"] for row in from_command] == [
        "read",
        "ls",
        "grep",
        "find",
        "write",
        "edit",
        "bash",
        "reporter",
        "lookup",
        "withdrawn",
    ]
    assert by_name["read"] == {
        "name": "read",
        "exposure": "direct",
        "namespace": None,
        "read_only": None,
        "reaches": False,
    }
    assert by_name["lookup"] == {
        "name": "lookup",
        "exposure": "deferred",
        "namespace": "records",
        "read_only": True,
        "reaches": True,
    }
    # A hidden tool is reported, although no request declares it.
    assert by_name["withdrawn"]["exposure"] == "hidden"
    assert "withdrawn" not in _tool_names(provider.requests[0])


def test_a_permission_hook_decides_by_annotations(tmp_path, monkeypatch) -> None:
    # Pi docs/extensions.md "Tool exposure": the tool_call handler that reads
    # `getAllTools()` annotations to decide which calls to confirm.
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    _write(
        tmp_path,
        "permissions",
        "def activate(api):\n"
        "    for name, hints in (\n"
        "        ('peek', ToolAnnotations(read_only_hint=True)),\n"
        "        ('wipe', ToolAnnotations(destructive_hint=True)),\n"
        "    ):\n"
        "        api.register_tool(ExtensionTool(\n"
        "            name=name, description=name, input_schema={'type': 'object'},\n"
        "            handler=lambda ctx, params, name=name: ToolResult(content=name + ' ran'),\n"
        "            annotations=hints))\n"
        "    @api.on('tool_call')\n"
        "    def gate(event, ctx):\n"
        "        hints = next(\n"
        "            (t.annotations for t in ctx.get_all_tools()\n"
        "             if t.name == event.tool_name), None)\n"
        "        if hints is not None and hints.destructive_hint is True:\n"
        "            return ToolBlock(reason=event.tool_name + ' was not approved')\n",
    )
    calls = (
        ProviderToolCall("c1", "peek", "{}"),
        ProviderToolCall("c2", "wipe", "{}"),
    )
    provider = _StubProvider([_result(tool_calls=calls), _result(final_text="ok")])

    result = _run(tmp_path, provider, "go\n")

    assert result.status is HarnessStatus.SUCCEEDED
    peek, wipe = _tool_results(provider.requests[1])
    assert (peek.is_error, peek.content.value) == (False, "peek ran")
    assert wipe.is_error is True
    assert "wipe was not approved" in wipe.content.value
    assert "wipe ran" not in wipe.content.value


def test_all_tools_reads_the_live_registry_with_detached_schemas(
    tmp_path: Path,
) -> None:
    capabilities = NativeToolCapabilities(
        {"codemode": CodemodeTool()},
        {},
        workspace_root=tmp_path,
        filter_options=ToolFilterOptions.empty(),
        cancel_join_timeout_seconds=1.0,
    )

    (info,) = capabilities.all_tools()

    assert isinstance(info, ToolInfo)
    assert info.name == "codemode"
    assert info.description == CodemodeTool().definition.description
    # Declared to the model, never callable from a script.
    assert info.exposure == "model-only"
    # EXT-05: codemode declares which tools a script may reach.
    assert info.reaches_tools is script_reach
    # A caller that edits the reported schema does not edit the registered one.
    info.input_schema["properties"]["code"]["type"] = "integer"  # type: ignore[index]
    (again,) = capabilities.all_tools()
    assert again.input_schema["properties"]["code"]["type"] == "string"

    port = _port(lambda ctx, params: ToolResult(content="x"))
    capabilities.publish(capabilities.prepare_extensions({"probe": port}))
    assert [tool.name for tool in capabilities.all_tools()] == ["codemode", "probe"]


def test_get_all_tools_is_a_capability_of_the_context() -> None:
    bare = make_extension_context("/tmp", False)
    with pytest.raises(ExtensionCapabilityError):
        bare.get_all_tools()

    info = ToolInfo(
        name="x", description="x", input_schema={"type": "object"}, exposure="direct"
    )
    wired = make_extension_context(
        "/tmp",
        False,
        model_runtime=ExtensionModelRuntimeControl(get_all_tools_fn=lambda: [info]),
    )
    assert wired.get_all_tools() == (info,)


# -- EXT-05: codemode reach -----------------------------------------------


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        # Pi `getScriptReach`: the words of the source name the tools.
        (
            "text(tools.mcp__docs__search({'q': 'x'}))",
            ("text", "tools", "mcp__docs__search", "q", "x"),
        ),
        (
            "tools['mcp__docs__' + name]()",
            ("tools", "mcp__docs__", "name"),
        ),
        ("", ()),
        # A discovery global lets the script call any tool by a computed name.
        ("for t in ALL_TOOLS: text(t)", "all"),
        ("search_tools('issue')", "all"),
        ("describe_tool('read')", "all"),
        ("describe_namespace('mcp__docs')", "all"),
        # Only whole words count.
        ("my_search_tools_helper()", ("my_search_tools_helper",)),
    ],
)
def test_codemode_reach_follows_the_script(code: str, expected: object) -> None:
    assert script_reach({"code": code}) == expected


def test_codemode_reach_of_a_call_without_source_is_empty() -> None:
    assert script_reach({}) == ()
    assert script_reach({"code": 7}) == ()
    assert CodemodeTool().definition.reaches_tools is script_reach


# -- EXT-04: re-registration and hidden -----------------------------------


def test_registering_a_name_again_replaces_the_earlier_tool(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "twice",
        "def activate(api):\n"
        "    for description in ('first', 'second'):\n"
        "        api.register_tool(ExtensionTool(\n"
        "            name='alpha', description=description,\n"
        "            input_schema={'type': 'object'},\n"
        "            handler=lambda ctx, params: ToolResult(content='ok')))\n"
        "        api.register_tool(ExtensionTool(\n"
        "            name='beta', description='beta',\n"
        "            input_schema={'type': 'object'},\n"
        "            handler=lambda ctx, params: ToolResult(content='ok')))\n",
    )

    (activated,) = _activate(tmp_path)

    assert activated.status == "activated"
    # The replacement keeps the place of the first registration.
    assert [(t.tool.name, t.tool.description) for t in activated.tools] == [
        ("alpha", "second"),
        ("beta", "beta"),
    ]


def test_another_extensions_tool_name_is_still_a_duplicate(tmp_path: Path) -> None:
    body = (
        "def activate(api):\n"
        "    api.register_tool(ExtensionTool(\n"
        "        name='shared', description='shared',\n"
        "        input_schema={'type': 'object'},\n"
        "        handler=lambda ctx, params: ToolResult(content='ok'),\n"
        "        exposure='hidden'))\n"
    )
    _write(tmp_path, "aaa", body)
    _write(tmp_path, "bbb", body)

    statuses = {a.name: (a.status, a.reason) for a in _activate(tmp_path)}

    assert statuses["aaa"] == ("activated", None)
    assert statuses["bbb"] == ("disabled", "duplicate_tool")


def test_hidden_re_registration_withdraws_the_tool(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    _write(
        tmp_path,
        "withdraw",
        "def activate(api):\n"
        "    def register(exposure):\n"
        "        api.register_tool(ExtensionTool(\n"
        "            name='lookup', description='look up',\n"
        "            input_schema={'type': 'object'},\n"
        "            handler=lambda ctx, params: ToolResult(content='looked up'),\n"
        "            exposure=exposure))\n"
        "    register('direct')\n"
        "    register('hidden')\n"
        "    api.register_command(\n"
        "        'activate', 'activate the hidden tool',\n"
        "        lambda ctx, args: ctx.ui.notify(\n"
        "            str(ctx.set_active_tools(['read', 'lookup']))))\n",
    )
    call = ProviderToolCall("c1", "lookup", "{}")
    provider = _StubProvider([_result(tool_calls=(call,)), _result(final_text="ok")])

    result = _run(tmp_path, provider, "/activate\nlook it up\n")

    assert result.status is HarnessStatus.SUCCEEDED
    # Naming a hidden tool as active has no effect: it is never declared.
    assert _tool_names(provider.requests[0]) == ["read"]
    # A model that calls it anyway is told the tool does not exist.
    (tool_result,) = _tool_results(provider.requests[1])
    assert tool_result.is_error is True
    assert tool_result.content.value == "unknown tool: lookup"


def test_hidden_re_registration_removes_the_tool_from_the_next_request(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    _write(
        tmp_path,
        "reloaded",
        "MARKER = Path(__file__).with_name('withdrawn.marker')\n"
        "def activate(api):\n"
        "    api.register_tool(ExtensionTool(\n"
        "        name='lookup', description='look up',\n"
        "        input_schema={'type': 'object'},\n"
        "        handler=lambda ctx, params: ToolResult(content='ok')))\n"
        "    if MARKER.exists():\n"
        "        api.register_tool(ExtensionTool(\n"
        "            name='lookup', description='look up',\n"
        "            input_schema={'type': 'object'},\n"
        "            handler=lambda ctx, params: ToolResult(content='ok'),\n"
        "            exposure='hidden'))\n"
        "    api.register_command(\n"
        "        'withdraw', 'withdraw the tool', lambda ctx, args: MARKER.write_text('x'))\n",
    )
    provider = _StubProvider([_result(final_text="one"), _result(final_text="two")])

    result = _run(tmp_path, provider, "first\n/withdraw\n/reload\nsecond\n")

    assert result.status is HarnessStatus.SUCCEEDED
    first, second = provider.requests
    assert "lookup" in _tool_names(first)
    assert "lookup" not in _tool_names(second)
    # The transcript tells the model that the tool is gone.
    assert second.system_messages[-1].message.tools_removed == ("lookup",)
