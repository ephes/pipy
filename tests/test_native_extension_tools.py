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
from pipy_harness.native.extension_runtime import (
    activate_extensions,
    extension_tools,
)
from pipy_harness.native.extensions import discover_extensions
from pipy_harness.native.models import (
    ProviderRequest,
    ProviderResult,
    ProviderToolCall,
)
from pipy_harness.native.tool_loop_session import (
    NativeToolReplSession,
    production_tool_registry,
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
        discover_extensions(workspace, config_home_env={}, home_dir=workspace),
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
    session = NativeToolReplSession(
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
    joined = " ".join(
        str(getattr(m, "content", "") or getattr(m, "output_text", ""))
        for m in second.messages
    )
    assert "echo:hi there" in joined


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
    session = NativeToolReplSession(
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
    joined = " ".join(
        str(getattr(m, "content", "") or getattr(m, "output_text", ""))
        for m in second.messages
    )
    assert "/secret" not in joined and "leak-xyz" not in joined
