"""Slice 7 tests for pure/read-only extension tool registration.

An extension registers a model-visible tool via `api.register_tool(...)`
with a JSON-schema input. The tool joins the bounded tool registry; the
model can call it, its `ToolResult(content, details)` flows back, a tool
exception becomes a bounded tool error, and the output is bounded. A tool
that shadows a built-in, declares an invalid schema, or duplicates another
extension tool disables that extension.
"""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime
from pathlib import Path

from pipy_harness.models import HarnessStatus
from pipy_harness.native.agent.messages import AgentToolResultMessage
from pipy_harness.native.coding.session import (
    CodingSession,
    production_tool_registry,
)
from pipy_harness.native.extensions.activation import activate_extensions
from pipy_harness.native.extensions.collectors import (
    extension_tools,
)
from pipy_harness.native.extensions.packages import discover_extensions
from pipy_harness.native.models import (
    ProviderRequest,
    ProviderResult,
    ProviderToolCall,
)


def _make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    return workspace


def _write(workspace: Path, name: str, body: str) -> None:
    directory = workspace / ".pipy" / "extensions"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.py").write_text(body, encoding="utf-8")


def _activate(workspace: Path, *, reserved_tools=()) -> list:
    return activate_extensions(
        discover_extensions(
            workspace,
            config_home_env={},
            home_dir=workspace,
            include_workspace_defaults=True,
        ),
        reserved_tool_names=reserved_tools,
    )


_ECHO_TOOL = (
    "from pipy_harness.extensions import ExtensionTool, ToolResult\n"
    "def activate(api):\n"
    "    def handler(ctx, params):\n"
    "        return ToolResult(content='echo:' + params['text'])\n"
    "    api.register_tool(ExtensionTool(\n"
    "        name='echo_tool',\n"
    "        description='Echo the input text.',\n"
    "        input_schema={'type': 'object', 'properties': {'text': {'type': 'string'}},\n"
    "                      'required': ['text']},\n"
    "        handler=handler,\n"
    "    ))\n"
)


def test_register_tool_is_collected(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _write(workspace, "echoext", _ECHO_TOOL)

    tools = extension_tools(_activate(workspace))

    assert [t.tool.name for t in tools] == ["echo_tool"]


def test_invalid_schema_disables_extension(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _write(
        workspace,
        "badschema",
        "from pipy_harness.extensions import ExtensionTool, ToolResult\n"
        "def activate(api):\n"
        "    api.register_tool(ExtensionTool(\n"
        "        name='bad',\n"
        "        description='x',\n"
        "        input_schema={'type': 'not-a-type'},\n"
        "        handler=lambda ctx, p: ToolResult(content='x'),\n"
        "    ))\n",
    )

    activated = next(a for a in _activate(workspace) if a.name == "badschema")
    assert activated.status == "disabled"
    assert not extension_tools([activated])


def test_reserved_tool_name_disables_extension(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _write(
        workspace,
        "shadow",
        "from pipy_harness.extensions import ExtensionTool, ToolResult\n"
        "def activate(api):\n"
        "    api.register_tool(ExtensionTool(\n"
        "        name='bash',\n"
        "        description='x',\n"
        "        input_schema={'type': 'object'},\n"
        "        handler=lambda ctx, p: ToolResult(content='x'),\n"
        "    ))\n",
    )

    activated = next(
        a for a in _activate(workspace, reserved_tools=("bash",)) if a.name == "shadow"
    )
    assert activated.status == "disabled"


def test_duplicate_tool_name_disables_second(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _write(workspace, "aaa", _ECHO_TOOL)
    _write(workspace, "bbb", _ECHO_TOOL)

    activated = _activate(workspace)
    aaa = next(a for a in activated if a.name == "aaa")
    bbb = next(a for a in activated if a.name == "bbb")

    assert aaa.status == "activated"
    assert bbb.status == "disabled"
    assert [t.tool.name for t in extension_tools(activated)] == ["echo_tool"]


# -- product path: the model calls an extension tool ----------------------


class _StubProvider:
    name = "stub"
    model_id = "stub-model"
    # Accepts later system messages, so requests carry the transcript's.
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
    now = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)
    return ProviderResult(
        status=HarnessStatus.SUCCEEDED,
        provider_name="stub",
        model_id="stub-model",
        started_at=now,
        ended_at=now,
        final_text=final_text,
        tool_calls=tool_calls,
    )


def test_model_can_call_an_extension_tool(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    ext = tmp_path / ".pipy" / "extensions"
    ext.mkdir(parents=True)
    (ext / "echoext.py").write_text(_ECHO_TOOL, encoding="utf-8")
    call = ProviderToolCall(
        provider_correlation_id="c1",
        tool_name="echo_tool",
        arguments_json=json.dumps({"text": "hi there"}),
    )
    provider = _StubProvider([_result(tool_calls=(call,)), _result(final_text="ok")])
    session = CodingSession(
        provider=provider, tool_registry=production_tool_registry(), tool_budget=5
    )

    result = session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("use the tool\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    assert result.tool_invocation_count == 1
    # The tool's result content flowed back into the next provider request.
    second = provider.requests[1]
    joined = " ".join(message.content.value for message in second.messages)
    assert "echo:hi there" in joined


def test_extension_tool_additive_activation_is_declared_at_the_next_turn(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    ext = tmp_path / ".pipy" / "extensions"
    ext.mkdir(parents=True)
    (ext / "dynamic.py").write_text(
        "from pipy_harness.extensions import ExtensionTool, ToolResult\n"
        "def activate(api):\n"
        "    def prepare(ctx, _args):\n"
        "        assert ctx.set_active_tools(['loader'])\n"
        "    api.register_command('prepare', 'prepare dynamic tools', prepare)\n"
        "    def loader(ctx, _params):\n"
        "        assert ctx.set_active_tools(['loader', 'late_tool'])\n"
        "        return ToolResult(content='late tool loaded')\n"
        "    api.register_tool(ExtensionTool(\n"
        "        name='loader', description='load another tool',\n"
        "        input_schema={'type': 'object'}, handler=loader))\n"
        "    api.register_tool(ExtensionTool(\n"
        "        name='late_tool', description='late tool',\n"
        "        input_schema={'type': 'object'},\n"
        "        handler=lambda _ctx, _params: ToolResult(content='late')))\n",
        encoding="utf-8",
    )
    call = ProviderToolCall(
        provider_correlation_id="loader-call",
        tool_name="loader",
        arguments_json="{}",
    )
    provider = _StubProvider([_result(tool_calls=(call,)), _result(final_text="ok")])
    session = CodingSession(
        provider=provider, tool_registry=production_tool_registry(), tool_budget=5
    )

    result = session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("/prepare\nload one\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    assert result.status is HarnessStatus.SUCCEEDED
    assert [tool.name for tool in provider.requests[0].available_tools] == ["loader"]
    assert [tool.name for tool in provider.requests[1].available_tools] == [
        "loader",
        "late_tool",
    ]
    # Pi 9e05370b2: the next turn's system message declares the new tool,
    # anchored after the loader's result.
    second = provider.requests[1]
    later = second.system_messages[1:]
    assert [anchor.position for anchor in later] == [len(second.messages)]
    assert [tool.name for tool in later[0].message.tools_added] == ["late_tool"]
    assert later[0].message.tools_removed == ()


def test_extension_tool_replacement_and_failure_declare_the_tool_delta(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    # Pi declares every change to the executable tools at the next turn start,
    # whatever the tool returned: a replacement removes and adds, a failed
    # call that widened the set still adds.
    cases = (
        (
            "replacement",
            "['late_tool']",
            "return ToolResult(content='replaced')",
            ["late_tool"],
            (["late_tool"], ("loader",)),
        ),
        (
            "failure",
            "['loader', 'late_tool']",
            "raise RuntimeError('boom')",
            ["loader", "late_tool"],
            (["late_tool"], ()),
        ),
    )
    for case_name, active_expression, outcome, expected_tools, delta in cases:
        workspace = tmp_path / case_name
        ext = workspace / ".pipy" / "extensions"
        ext.mkdir(parents=True)
        (ext / "dynamic.py").write_text(
            "from pipy_harness.extensions import ExtensionTool, ToolResult\n"
            "def activate(api):\n"
            "    def prepare(ctx, _args):\n"
            "        assert ctx.set_active_tools(['loader'])\n"
            "    api.register_command('prepare', 'prepare dynamic tools', prepare)\n"
            "    def loader(ctx, _params):\n"
            f"        assert ctx.set_active_tools({active_expression})\n"
            f"        {outcome}\n"
            "    api.register_tool(ExtensionTool(\n"
            "        name='loader', description='load another tool',\n"
            "        input_schema={'type': 'object'}, handler=loader))\n"
            "    api.register_tool(ExtensionTool(\n"
            "        name='late_tool', description='late tool',\n"
            "        input_schema={'type': 'object'},\n"
            "        handler=lambda _ctx, _params: ToolResult(content='late')))\n",
            encoding="utf-8",
        )
        call = ProviderToolCall("loader-call", "loader", "{}")
        provider = _StubProvider(
            [_result(tool_calls=(call,)), _result(final_text="ok")]
        )
        session = CodingSession(
            provider=provider,
            tool_registry=production_tool_registry(),
            tool_budget=5,
        )

        result = session.run(
            workspace_root=workspace,
            input_stream=io.StringIO("/prepare\nload one\n"),
            output_stream=io.StringIO(),
            error_stream=io.StringIO(),
        )

        assert result.status is HarnessStatus.SUCCEEDED
        assert [
            tool.name for tool in provider.requests[1].available_tools
        ] == expected_tools
        later = provider.requests[1].system_messages[-1].message
        assert (
            [tool.name for tool in later.tools_added],
            later.tools_removed,
        ) == delta


def test_extension_tool_exception_is_bounded(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    ext = tmp_path / ".pipy" / "extensions"
    ext.mkdir(parents=True)
    (ext / "boom.py").write_text(
        "from pipy_harness.extensions import ExtensionTool, ToolResult\n"
        "def activate(api):\n"
        "    def handler(ctx, params):\n"
        "        raise RuntimeError('/secret/leak-xyz')\n"
        "    api.register_tool(ExtensionTool(\n"
        "        name='boom_tool',\n"
        "        description='boom',\n"
        "        input_schema={'type': 'object'},\n"
        "        handler=handler,\n"
        "    ))\n",
        encoding="utf-8",
    )
    call = ProviderToolCall(
        provider_correlation_id="c1",
        tool_name="boom_tool",
        arguments_json=json.dumps({}),
    )
    provider = _StubProvider([_result(tool_calls=(call,)), _result(final_text="ok")])
    session = CodingSession(
        provider=provider, tool_registry=production_tool_registry(), tool_budget=5
    )

    result = session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("use it\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    # The run survived and the raw exception message did not leak.
    assert result.status is HarnessStatus.SUCCEEDED
    second = provider.requests[1]
    joined = " ".join(message.content.value for message in second.messages)
    assert "/secret" not in joined and "leak-xyz" not in joined


# -- Pi-style schemas and prepare_arguments (goal check r2, S18 and S19) ----


_PI_STYLE_TOOL = (
    "import json\n"
    "from pipy_harness.extensions import ExtensionTool, ToolResult\n"
    "def activate(api):\n"
    "    def handler(ctx, params):\n"
    "        return ToolResult(content='got:' + json.dumps(params, sort_keys=True))\n"
    "    def prepare(arguments):\n"
    "        if isinstance(arguments, dict) and 'old_count' in arguments:\n"
    "            arguments = dict(arguments)\n"
    "            arguments['count'] = arguments.pop('old_count')\n"
    "        if isinstance(arguments, dict) and arguments.get('count') == 'boom':\n"
    "            raise RuntimeError('secret detail')\n"
    "        return arguments\n"
    "    api.register_tool(ExtensionTool(\n"
    "        name='measure',\n"
    "        description='Measure something.',\n"
    "        input_schema={\n"
    "            'type': 'object',\n"
    "            '$defs': {'unit': {'enum': ['cm', 'in']}},\n"
    "            'properties': {\n"
    "                'count': {'type': 'number'},\n"
    "                'label': {'anyOf': [{'type': 'string'}, {'type': 'null'}]},\n"
    "                'unit': {'$ref': '#/$defs/unit'},\n"
    "            },\n"
    "            'required': ['count'],\n"
    "        },\n"
    "        handler=handler,\n"
    "        prepare_arguments=prepare,\n"
    "    ))\n"
)


def test_a_pi_style_schema_keeps_the_extension_enabled(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _write(workspace, "measure", _PI_STYLE_TOOL)

    (activated,) = _activate(workspace)

    assert (activated.status, activated.reason) == ("activated", None)
    (registered,) = activated.tools
    assert registered.tool.input_schema["properties"]["count"] == {"type": "number"}


def test_a_non_callable_prepare_arguments_disables_the_extension(
    tmp_path: Path,
) -> None:
    workspace = _make_workspace(tmp_path)
    _write(
        workspace,
        "badprepare",
        "from pipy_harness.extensions import ExtensionTool, ToolResult\n"
        "def activate(api):\n"
        "    api.register_tool(ExtensionTool(\n"
        "        name='bad', description='x', input_schema={'type': 'object'},\n"
        "        handler=lambda ctx, p: ToolResult(content='x'),\n"
        "        prepare_arguments='not callable'))\n",
    )

    (activated,) = _activate(workspace)

    assert (activated.status, activated.reason) == ("disabled", "invalid_tool")


def test_the_model_calls_a_pi_style_tool_through_prepare_arguments(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    _write(tmp_path, "measure", _PI_STYLE_TOOL)
    calls = (
        ProviderToolCall("c1", "measure", json.dumps({"count": 2.5, "label": None})),
        ProviderToolCall("c3", "measure", json.dumps({"count": "boom"})),
        ProviderToolCall("c2", "measure", json.dumps({"old_count": 3, "unit": "cm"})),
        ProviderToolCall("c4", "measure", json.dumps({"count": [1]})),
        ProviderToolCall("c5", "measure", json.dumps({"count": 1, "unit": "mm"})),
    )
    # One call per turn, a valid one between failures: three malformed calls
    # in a row end the session.
    provider = _StubProvider(
        [
            *(_result(tool_calls=(call,)) for call in calls),
            _result(final_text="ok"),
        ]
    )
    session = CodingSession(
        provider=provider, tool_registry=production_tool_registry(), tool_budget=10
    )

    result = session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("measure\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    assert result.status is HarnessStatus.SUCCEEDED
    results = [
        message
        for message in provider.requests[-1].messages
        if isinstance(message, AgentToolResultMessage)
        and message.tool_name == "measure"
    ]
    outcomes = [(message.is_error, message.content.value) for message in results]
    # A number and a null pass the schema; the callback renames an older
    # argument before validation, and the `$ref` enum is checked.
    assert outcomes[0] == (False, 'got:{"count": 2.5, "label": null}')
    assert outcomes[2] == (False, 'got:{"count": 3, "unit": "cm"}')
    # A callback that raises ends the call with the exception type only.
    assert outcomes[1] == (True, "measure: prepare_arguments raised RuntimeError")
    assert outcomes[3][0] is True and "count" in outcomes[3][1]
    assert outcomes[4][0] is True and "unit" in outcomes[4][1]


_COUNTING_PREPARE = (
    "import json\n"
    "from pathlib import Path\n"
    "from pipy_harness.extensions import ExtensionTool, ToolResult\n"
    "LOG = Path(__file__).with_name('log.jsonl')\n"
    "COUNT = [0]\n"
    "def _log(*row):\n"
    "    with LOG.open('a') as handle:\n"
    "        handle.write(json.dumps(list(row)) + '\\n')\n"
    "def activate(api):\n"
    "    def prepare(arguments):\n"
    "        COUNT[0] += 1\n"
    "        return {'text': arguments['text'] + ':' + str(COUNT[0])}\n"
    "    def echo(ctx, params):\n"
    "        _log('ran', params['text'])\n"
    "        return ToolResult(content=params['text'])\n"
    "    def relay(ctx, params):\n"
    "        return ToolResult(content=ctx.execute_tool(\n"
    "            'echo', {'text': params['text']}).result.content)\n"
    "    schema = {'type': 'object', 'properties': {'text': {'type': 'string'}}}\n"
    "    api.register_tool(ExtensionTool(name='echo', description='Echo.',\n"
    "        input_schema=schema, handler=echo, prepare_arguments=prepare))\n"
    "    api.register_tool(ExtensionTool(name='relay', description='Relay.',\n"
    "        input_schema=schema, handler=relay))\n"
    "    @api.on('tool_call')\n"
    "    def seen(event, ctx):\n"
    "        _log('hook', event.tool_name, dict(event.input))\n"
)


def test_prepare_arguments_runs_once_per_call(tmp_path, monkeypatch) -> None:
    # The model's call runs as a parent (it may call other tools): the
    # handler runs with the arguments the hooks saw, prepared once.
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    _write(tmp_path, "counting", _COUNTING_PREPARE)
    calls = (
        ProviderToolCall("c1", "echo", json.dumps({"text": "hello"})),
        ProviderToolCall("c2", "relay", json.dumps({"text": "nested"})),
    )
    provider = _StubProvider(
        [*(_result(tool_calls=(call,)) for call in calls), _result(final_text="ok")]
    )
    session = CodingSession(
        provider=provider, tool_registry=production_tool_registry(), tool_budget=10
    )

    result = session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("echo\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    assert result.status is HarnessStatus.SUCCEEDED
    log = (tmp_path / ".pipy" / "extensions" / "log.jsonl").read_text()
    rows = [json.loads(line) for line in log.splitlines()]
    assert rows == [
        ["hook", "echo", {"text": "hello:1"}],
        ["ran", "hello:1"],
        ["hook", "relay", {"text": "nested"}],
        ["hook", "echo", {"text": "nested:2"}],
        ["ran", "nested:2"],
    ]
