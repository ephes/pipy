"""Slice 4 tests for the extension `tool_call` policy hook.

An extension may register `@api.on("tool_call")` (or
`api.on("tool_call", handler)`) to inspect a model-selected tool call's
live name and parsed input before execution and return
`ToolBlock(reason=...)` to block it. Hooks run in registration order;
the first `ToolBlock` wins. A crashing hook fails closed (blocks). A user
abort propagates. An invalid registration disables the extension.
"""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime
from pathlib import Path

from pipy_harness.models import HarnessStatus
from pipy_harness.native.extension_runtime import (
    ToolBlock,
    activate_extensions,
    dispatch_tool_call_hooks,
    extension_tool_call_hooks,
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


class _StubToolProvider:
    def __init__(self, results: list[ProviderResult]) -> None:
        self._results = list(results)
        self.name = "stub-tool"
        self.model_id = "stub-model"
        self.requests: list[ProviderRequest] = []

    @property
    def supports_tool_calls(self) -> bool:
        return True

    def complete(self, request: ProviderRequest, **_kwargs: object) -> ProviderResult:
        self.requests.append(request)
        return self._results.pop(0)


def _result(*, tool_calls=(), final_text=None) -> ProviderResult:
    now = datetime.now(UTC)
    return ProviderResult(
        status=HarnessStatus.SUCCEEDED,
        provider_name="stub-tool",
        model_id="stub-model",
        started_at=now,
        ended_at=now,
        final_text=final_text,
        tool_calls=tool_calls,
    )


def _make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    return workspace


def _ext_dir(workspace: Path) -> Path:
    directory = workspace / ".pipy" / "extensions"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _write(workspace: Path, name: str, body: str) -> None:
    (_ext_dir(workspace) / f"{name}.py").write_text(body, encoding="utf-8")


def _activate(workspace: Path) -> list:
    descriptors = discover_extensions(
        workspace, config_home_env={}, home_dir=workspace
    )
    return activate_extensions(descriptors)


def _hooks(workspace: Path) -> tuple:
    return extension_tool_call_hooks(_activate(workspace))


def _dispatch(workspace, tool_name, tool_input):
    return dispatch_tool_call_hooks(
        _hooks(workspace),
        tool_name=tool_name,
        tool_input=tool_input,
        cwd=str(workspace),
        has_ui=False,
    )


def test_hook_blocks_with_reason(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _write(
        workspace,
        "guard",
        "from pipy_harness.extensions import ToolBlock\n"
        "def activate(api):\n"
        "    @api.on('tool_call')\n"
        "    def gate(event, ctx):\n"
        "        if event.tool_name == 'bash' and 'rm -rf' in event.input.get('command', ''):\n"
        "            return ToolBlock(reason='dangerous command blocked')\n",
    )

    block = _dispatch(workspace, "bash", {"command": "rm -rf /"})

    assert isinstance(block, ToolBlock)
    assert block.reason == "dangerous command blocked"


def test_hook_allows_when_no_block(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _write(
        workspace,
        "guard",
        "from pipy_harness.extensions import ToolBlock\n"
        "def activate(api):\n"
        "    @api.on('tool_call')\n"
        "    def gate(event, ctx):\n"
        "        if 'rm -rf' in event.input.get('command', ''):\n"
        "            return ToolBlock(reason='no')\n",
    )

    assert _dispatch(workspace, "bash", {"command": "ls"}) is None


def test_direct_on_registration_form(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _write(
        workspace,
        "direct",
        "from pipy_harness.extensions import ToolBlock\n"
        "def gate(event, ctx):\n"
        "    return ToolBlock(reason='blocked')\n"
        "def activate(api):\n"
        "    api.on('tool_call', gate)\n",
    )

    block = _dispatch(workspace, "read", {})
    assert isinstance(block, ToolBlock)
    assert block.reason == "blocked"


def test_async_hook(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _write(
        workspace,
        "asyncguard",
        "from pipy_harness.extensions import ToolBlock\n"
        "def activate(api):\n"
        "    @api.on('tool_call')\n"
        "    async def gate(event, ctx):\n"
        "        return ToolBlock(reason='async-block')\n",
    )

    block = _dispatch(workspace, "read", {})
    assert isinstance(block, ToolBlock)
    assert block.reason == "async-block"


def test_first_block_wins(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _write(
        workspace,
        "aaa",
        "from pipy_harness.extensions import ToolBlock\n"
        "def activate(api):\n"
        "    @api.on('tool_call')\n"
        "    def gate(event, ctx):\n"
        "        return ToolBlock(reason='first')\n",
    )
    _write(
        workspace,
        "bbb",
        "from pipy_harness.extensions import ToolBlock\n"
        "def activate(api):\n"
        "    @api.on('tool_call')\n"
        "    def gate(event, ctx):\n"
        "        return ToolBlock(reason='second')\n",
    )

    block = _dispatch(workspace, "read", {})
    assert isinstance(block, ToolBlock)
    assert block.reason == "first"


def test_crashing_hook_fails_closed(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _write(
        workspace,
        "crashy",
        "def activate(api):\n"
        "    @api.on('tool_call')\n"
        "    def gate(event, ctx):\n"
        "        raise RuntimeError('/secret/leak-xyz')\n",
    )

    block = _dispatch(workspace, "read", {})
    assert isinstance(block, ToolBlock)
    # The reason is a safe label, not the raw exception message.
    assert "/secret" not in block.reason
    assert "leak-xyz" not in block.reason


def test_keyboard_interrupt_propagates(tmp_path: Path) -> None:
    import pytest

    workspace = _make_workspace(tmp_path)
    _write(
        workspace,
        "intr",
        "def activate(api):\n"
        "    @api.on('tool_call')\n"
        "    def gate(event, ctx):\n"
        "        raise KeyboardInterrupt()\n",
    )

    with pytest.raises(KeyboardInterrupt):
        _dispatch(workspace, "read", {})


def test_tool_call_hook_blocks_through_the_session(tmp_path, monkeypatch) -> None:
    # Product path: a registered tool_call hook blocks a real bash tool
    # call before execution; the command never runs and the model sees a
    # blocked observation.
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    ext = tmp_path / ".pipy" / "extensions"
    ext.mkdir(parents=True)
    (ext / "guard.py").write_text(
        "from pipy_harness.extensions import ToolBlock\n"
        "def activate(api):\n"
        "    @api.on('tool_call')\n"
        "    def gate(event, ctx):\n"
        "        cmd = event.input.get('command', '')\n"
        "        if event.tool_name == 'bash' and 'rm -rf' in cmd:\n"
        "            return ToolBlock(reason='dangerous command blocked')\n",
        encoding="utf-8",
    )
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me", encoding="utf-8")
    call = ProviderToolCall(
        provider_correlation_id="c1",
        tool_name="bash",
        arguments_json=json.dumps({"command": f"rm -rf {victim}"}),
    )
    provider = _StubToolProvider(
        [_result(tool_calls=(call,)), _result(final_text="done")]
    )
    session = NativeToolReplSession(
        provider=provider,
        tool_registry=production_tool_registry(),
        tool_budget=10,
    )
    error_stream = io.StringIO()

    result = session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("go\n"),
        output_stream=io.StringIO(),
        error_stream=error_stream,
    )

    # The dangerous command never executed: the file survives.
    assert victim.exists()
    # The block is surfaced and the tool was not invoked.
    assert "blocked by extension: dangerous command blocked" in error_stream.getvalue()
    assert result.tool_invocation_count == 0
    # The model continued (got a follow-up turn after the block).
    assert len(provider.requests) == 2


def test_blocked_calls_consume_the_tool_budget(tmp_path, monkeypatch) -> None:
    # A blocked tool call consumes the per-turn budget like a real call,
    # so repeated blocked calls cannot run unbounded.
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    ext = tmp_path / ".pipy" / "extensions"
    ext.mkdir(parents=True)
    (ext / "guard.py").write_text(
        "from pipy_harness.extensions import ToolBlock\n"
        "def activate(api):\n"
        "    @api.on('tool_call')\n"
        "    def gate(event, ctx):\n"
        "        return ToolBlock(reason='nope')\n",
        encoding="utf-8",
    )
    # One provider response with two blocked calls; budget is 1.
    calls = tuple(
        ProviderToolCall(
            provider_correlation_id=f"c{i}",
            tool_name="bash",
            arguments_json=json.dumps({"command": "echo hi"}),
        )
        for i in range(2)
    )
    provider = _StubToolProvider(
        [_result(tool_calls=calls), _result(final_text="done")]
    )
    session = NativeToolReplSession(
        provider=provider,
        tool_registry=production_tool_registry(),
        tool_budget=1,
    )
    error_stream = io.StringIO()

    session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("go\n"),
        output_stream=io.StringIO(),
        error_stream=error_stream,
    )

    # The first blocked call consumes the budget; the second hits the
    # budget-exhausted path instead of being processed unbounded.
    assert "tool budget exhausted" in error_stream.getvalue()


def test_invalid_hook_registration_disables_extension(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _write(
        workspace,
        "badhook",
        "def activate(api):\n"
        "    api.on('tool_call', 'not-callable')\n",
    )

    activated = _activate(workspace)
    badhook = next(a for a in activated if a.name == "badhook")
    assert badhook.status == "disabled"
    assert not extension_tool_call_hooks(activated)
