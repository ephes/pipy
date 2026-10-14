"""Extension tools that present the loadout (MCP parity plan S37, EXT-12).

A declared tool with ``prepare_loadout`` replaces the descriptions the model
sees and names declarations that requests leave out. ``default_active=False``
registers a tool without activating it.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from test_native_extension_tool_metadata import (
    _result,
    _StubProvider,
    _tool_names,
    _tool_results,
)

from pipy_harness.models import HarnessStatus
from pipy_harness.native.coding.session import CodingSession
from pipy_harness.native.models import ProviderToolCall
from pipy_harness.native.tool_capabilities import (
    NativeToolCapabilities,
    NativeToolCapabilitySnapshot,
    ToolFilterOptions,
)
from pipy_harness.native.tools import (
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
    ToolRequest,
)
from pipy_harness.native.tools.loadout import (
    ToolLoadout,
    ToolLoadoutChanges,
    checked_loadout_changes,
)
from pipy_harness.native.tools.registry import production_tool_registry

_ORCHESTRATOR = """from pipy_harness.extensions import (
    ExtensionTool, ToolLoadout, ToolLoadoutChanges, ToolResult,
)

_NONE = {'type': 'object', 'properties': {}, 'additionalProperties': False}
_TEXT = {'type': 'object', 'properties': {'text': {'type': 'string'}},
         'required': ['text'], 'additionalProperties': False}


def present(loadout):
    assert isinstance(loadout, ToolLoadout)
    extension = ('echo', 'helper', 'run_tools', 'quiet', 'broken')
    callable_names = [t.name for t in loadout.callable_tools if t.name in extension]
    registered = [t.name for t in loadout.registered_tools if t.name in extension]
    declared = [t.name for t in loadout.declared_tools if t.name in extension]
    exposure = {t.name: t.exposure for t in loadout.registered_tools}
    return ToolLoadoutChanges(
        descriptions={
            'run_tools': 'Runs tools: ' + ', '.join(callable_names)
            + ' / registered: ' + ', '.join(registered)
            + ' / declared: ' + ', '.join(declared)
            + ' / helper is ' + exposure['helper'],
            'echo': 'Echo text (also callable from run_tools).',
        },
        hidden_declarations=['echo'],
    )


def activate(api):
    def run_tools(ctx, params):
        return ToolResult(content=ctx.execute_tool('echo', {'text': 'hi'}).result.content)

    def wake(ctx, params):
        assert ctx.set_active_tools(['run_tools', 'echo', 'quiet'])
        return ToolResult(content='woke')

    api.register_tool(ExtensionTool(
        name='echo', description='Echo text.', input_schema=_TEXT,
        handler=lambda ctx, params: ToolResult(content='echo: ' + params['text'])))
    api.register_tool(ExtensionTool(
        name='helper', description='Only reachable from other tools.',
        input_schema=_NONE, exposure='codemode',
        handler=lambda ctx, params: ToolResult(content='helped')))
    api.register_tool(ExtensionTool(
        name='quiet', description='Inactive until named.', input_schema=_NONE,
        default_active=False,
        handler=lambda ctx, params: ToolResult(content='quiet')))
    api.register_tool(ExtensionTool(
        name='run_tools', description='Runs tools.', input_schema=_NONE,
        handler=run_tools, exposure='model-only', prepare_loadout=present))
    api.register_tool(ExtensionTool(
        name='wake', description='Activates quiet.', input_schema=_NONE,
        handler=wake))
BROKEN
"""

_BROKEN = """
    class Unprintable(RuntimeError):
        def __str__(self):
            raise ValueError('no text either')

    def broken(loadout):
        raise Unprintable('secret-token-123')

    api.register_tool(ExtensionTool(
        name='broken', description='Its hook fails.', input_schema=_NONE,
        handler=lambda ctx, params: ToolResult(content='ok'),
        prepare_loadout=broken))
    api.register_tool(ExtensionTool(
        name='wrong', description='Its hook returns a dict.', input_schema=_NONE,
        handler=lambda ctx, params: ToolResult(content='ok'),
        prepare_loadout=lambda loadout: {'descriptions': {'wrong': 'x'}}))
"""


def _write(workspace: Path, *, broken: bool = False) -> None:
    directory = workspace / ".pipy" / "extensions"
    directory.mkdir(parents=True, exist_ok=True)
    source = _ORCHESTRATOR.replace("BROKEN", _BROKEN if broken else "")
    (directory / "orchestrator.py").write_text(source, encoding="utf-8")


def _run(workspace: Path, provider, prompts: str = "go\n"):
    errors = io.StringIO()
    session = CodingSession(
        provider=provider, tool_registry=production_tool_registry(), tool_budget=20
    )
    result = session.run(
        workspace_root=workspace,
        input_stream=io.StringIO(prompts),
        output_stream=io.StringIO(),
        error_stream=errors,
    )
    return result, errors.getvalue()


def _description(request, name: str) -> str:
    return next(
        tool.description for tool in request.available_tools if tool.name == name
    )


def test_a_tool_presents_the_loadout_while_it_is_declared(
    tmp_path, monkeypatch
) -> None:
    # Pi: "supports tools that call other tools under any name through the
    # extension API" (prepareLoadout: descriptions and hidden declarations).
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    _write(tmp_path)
    provider = _StubProvider(
        [
            _result(tool_calls=(ProviderToolCall("p1", "run_tools", "{}"),)),
            _result(final_text="done"),
        ]
    )

    result, _ = _run(tmp_path, provider)

    assert result.status is HarnessStatus.SUCCEEDED
    first = provider.requests[0]
    # echo stays active and callable, but requests leave its declaration out.
    assert "run_tools" in _tool_names(first)
    assert "echo" not in _tool_names(first)
    assert _description(first, "run_tools") == (
        "Runs tools: echo, helper / registered: echo, helper, quiet, run_tools"
        " / declared: echo, run_tools / helper is codemode"
    )
    (parent,) = _tool_results(provider.requests[1])
    assert parent.content.value == "echo: hi"


def test_a_tool_registered_inactive_is_declared_once_it_is_named(
    tmp_path, monkeypatch
) -> None:
    # Pi `defaultActive: false`.
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    _write(tmp_path)
    provider = _StubProvider(
        [
            _result(tool_calls=(ProviderToolCall("p1", "wake", "{}"),)),
            _result(final_text="done"),
        ]
    )

    result, _ = _run(tmp_path, provider)

    assert result.status is HarnessStatus.SUCCEEDED
    assert "quiet" not in _tool_names(provider.requests[0])
    assert "wake" in _tool_names(provider.requests[0])
    assert "quiet" in _tool_names(provider.requests[1])
    # The hidden declaration follows the selection the handler made.
    assert "echo" not in _tool_names(provider.requests[1])


def test_a_failing_loadout_function_is_reported_once_and_changes_nothing(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    _write(tmp_path, broken=True)
    provider = _StubProvider(
        [
            _result(tool_calls=(ProviderToolCall("p1", "run_tools", "{}"),)),
            _result(final_text="done"),
            _result(final_text="again"),
        ]
    )

    result, errors = _run(tmp_path, provider, "go\nmore\n")

    assert result.status is HarnessStatus.SUCCEEDED
    assert len(provider.requests) == 3
    assert errors.count('prepare_loadout of tool "broken" failed') == 1
    # Only the type of the error: its text is the extension's.
    assert 'prepare_loadout of tool "broken" failed: Unprintable\n' in errors
    assert "secret-token-123" not in errors
    assert errors.count('prepare_loadout of tool "wrong" failed') == 1
    for request in provider.requests:
        # The other tool's changes still apply.
        assert "echo" not in _tool_names(request)
        assert _description(request, "run_tools").startswith("Runs tools: echo")
        assert _description(request, "broken") == "Its hook fails."
        assert _description(request, "wrong") == "Its hook returns a dict."


# -- the capability value ----------------------------------------------------


class _Port:
    def __init__(self, name: str, **metadata) -> None:
        self.definition = ToolDefinition(
            name=name, description=name, input_schema={"type": "object"}, **metadata
        )

    def invoke(self, request: ToolRequest, context: ToolContext) -> ToolExecutionResult:
        raise AssertionError("not called")


def _snapshot(*ports: _Port, tmp_path: Path) -> NativeToolCapabilitySnapshot:
    capabilities = NativeToolCapabilities(
        {},
        {port.definition.name: port for port in ports},
        workspace_root=tmp_path,
        filter_options=ToolFilterOptions.empty(),
        cancel_join_timeout_seconds=0.1,
    )
    return NativeToolCapabilitySnapshot(capabilities, capabilities.state)


def test_the_changes_of_several_tools_merge_in_declared_order(tmp_path) -> None:
    seen: list[ToolLoadout] = []

    def first(loadout: ToolLoadout) -> ToolLoadoutChanges:
        seen.append(loadout)
        return ToolLoadoutChanges(
            {"a": "a by first", "b": "b by first"}, frozenset("a")
        )

    snapshot = _snapshot(
        _Port("a"),
        _Port("b"),
        _Port("first", prepare_loadout=first),
        _Port(
            "second",
            prepare_loadout=lambda loadout: ToolLoadoutChanges(
                {"b": "b by second"}, frozenset({"b"})
            ),
        ),
        _Port("silent", prepare_loadout=lambda loadout: None),
        _Port("inactive", default_active=False, prepare_loadout=lambda loadout: 1 / 0),
        tmp_path=tmp_path,
    )

    described = {tool.name: tool.description for tool in snapshot.definitions()}

    assert described == {
        "a": "a by first",
        "b": "b by second",
        "first": "first",
        "second": "second",
        "silent": "silent",
    }
    assert snapshot.hidden_declarations() == frozenset({"a", "b"})
    loadout = seen[0]
    assert [tool.description for tool in loadout.declared_tools[:2]] == ["a", "b"]
    assert [tool.name for tool in loadout.registered_tools][-1] == "inactive"
    assert {tool.name for tool in loadout.callable_tools} == {
        "a",
        "b",
        "first",
        "second",
        "silent",
    }


@pytest.mark.parametrize(
    "value",
    [
        {"descriptions": {}},
        ToolLoadoutChanges({"a": 1}),  # type: ignore[dict-item]
        ToolLoadoutChanges({1: "a"}),  # type: ignore[dict-item]
        ToolLoadoutChanges({}, "echo"),  # type: ignore[arg-type]
        ToolLoadoutChanges({}, [1]),  # type: ignore[arg-type]
        ToolLoadoutChanges({}, 3),  # type: ignore[arg-type]
    ],
)
def test_changes_that_are_not_names_and_texts_are_refused(value) -> None:
    with pytest.raises(ValueError):
        checked_loadout_changes(value)


def test_changes_are_copied_and_a_list_of_hidden_names_is_accepted() -> None:
    descriptions = {"a": "text"}
    changes = checked_loadout_changes(
        ToolLoadoutChanges(descriptions, ["a", "a"])  # type: ignore[arg-type]
    )
    descriptions["a"] = "changed"

    assert changes == ToolLoadoutChanges({"a": "text"}, frozenset({"a"}))
    assert checked_loadout_changes(None) is None
