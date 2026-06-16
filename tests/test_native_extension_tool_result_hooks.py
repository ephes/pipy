"""Slice 8 tests for the tool_result hook.

After a tool (built-in or extension) runs, an extension
`@api.on("tool_result")` handler may observe or transform the bounded
result content before the next model turn. Hooks chain in order, are
fail-safe (a crash or non-string transform keeps the current content),
and the transformed observation is bounded. KeyboardInterrupt propagates.
"""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime
from pathlib import Path

from pipy_harness.models import HarnessStatus
from pipy_harness.native.extension_runtime import (
    activate_extensions,
    dispatch_tool_result_hooks,
    extension_event_hooks,
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


def _hooks(workspace: Path) -> tuple:
    activated = activate_extensions(
        discover_extensions(workspace, config_home_env={}, home_dir=workspace)
    )
    return extension_event_hooks(activated, "tool_result")


def _dispatch(workspace, tool_name, content, is_error=False):
    return dispatch_tool_result_hooks(
        _hooks(workspace),
        tool_name=tool_name,
        content=content,
        is_error=is_error,
        cwd=str(workspace),
        has_ui=False,
    )


def test_tool_result_transform(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _write(
        workspace,
        "wrap",
        "from pipy_harness.extensions import ToolResultTransform\n"
        "def activate(api):\n"
        "    @api.on('tool_result')\n"
        "    def t(event, ctx):\n"
        "        return ToolResultTransform(content='[' + event.tool_name + '] ' + event.content)\n",
    )

    assert _dispatch(workspace, "bash", "output") == "[bash] output"


def test_tool_result_observe_only(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _write(
        workspace,
        "obs",
        "def activate(api):\n"
        "    @api.on('tool_result')\n"
        "    def t(event, ctx):\n"
        "        return None\n",
    )

    assert _dispatch(workspace, "bash", "keep") == "keep"


def test_tool_result_hooks_chain(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    for name, suffix in (("a", "-a"), ("b", "-b")):
        _write(
            workspace,
            name,
            "from pipy_harness.extensions import ToolResultTransform\n"
            "def activate(api):\n"
            "    @api.on('tool_result')\n"
            "    def t(event, ctx):\n"
            f"        return ToolResultTransform(content=event.content + '{suffix}')\n",
        )

    assert _dispatch(workspace, "read", "x") == "x-a-b"


def test_crashing_tool_result_hook_keeps_content(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _write(
        workspace,
        "boom",
        "def activate(api):\n"
        "    @api.on('tool_result')\n"
        "    def t(event, ctx):\n"
        "        raise RuntimeError('x')\n",
    )

    assert _dispatch(workspace, "read", "keepme") == "keepme"


def test_non_string_transform_is_ignored(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _write(
        workspace,
        "bad",
        "from pipy_harness.extensions import ToolResultTransform\n"
        "def activate(api):\n"
        "    @api.on('tool_result')\n"
        "    def t(event, ctx):\n"
        "        return ToolResultTransform(content=object())\n",
    )

    assert _dispatch(workspace, "read", "keep") == "keep"


def test_keyboard_interrupt_propagates(tmp_path: Path) -> None:
    import pytest

    workspace = _make_workspace(tmp_path)
    _write(
        workspace,
        "intr",
        "def activate(api):\n"
        "    @api.on('tool_result')\n"
        "    def t(event, ctx):\n"
        "        raise KeyboardInterrupt()\n",
    )

    with pytest.raises(KeyboardInterrupt):
        _dispatch(workspace, "read", "x")


# -- product path: transform a real built-in tool result ------------------


class _Stub:
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


def test_tool_result_hook_transforms_through_session(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    (tmp_path / "note.txt").write_text("hello-note\n", encoding="utf-8")
    ext = tmp_path / ".pipy" / "extensions"
    ext.mkdir(parents=True)
    (ext / "wrap.py").write_text(
        "from pipy_harness.extensions import ToolResultTransform\n"
        "def activate(api):\n"
        "    @api.on('tool_result')\n"
        "    def t(event, ctx):\n"
        "        return ToolResultTransform(content='WRAPPED::' + event.content)\n",
        encoding="utf-8",
    )
    call = ProviderToolCall(
        provider_correlation_id="c1",
        tool_name="bash",
        arguments_json=json.dumps({"command": "cat note.txt"}),
    )
    provider = _Stub([_result(tool_calls=(call,)), _result(final_text="ok")])
    session = NativeToolReplSession(
        provider=provider, tool_registry=production_tool_registry(), tool_budget=5
    )

    session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("go\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    second = provider.requests[1]
    joined = " ".join(
        str(getattr(m, "content", "") or getattr(m, "output_text", ""))
        for m in second.messages
    )
    assert "WRAPPED::" in joined
    assert "hello-note" in joined
