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
from pipy_harness.native.coding.session import (
    CodingSession,
    production_tool_registry,
)
from pipy_harness.native.extension_hooks import (
    dispatch_tool_result_hooks,
    extension_event_hooks,
)
from pipy_harness.native.extension_types import ToolResultTransform
from pipy_harness.native.extensions.activation import activate_extensions
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


def _hooks(workspace: Path) -> tuple:
    activated = activate_extensions(
        discover_extensions(
            workspace,
            config_home_env={},
            home_dir=workspace,
            include_workspace_defaults=True,
        )
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
    ).content


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
    session = CodingSession(
        provider=provider, tool_registry=production_tool_registry(), tool_budget=5
    )

    session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("go\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    second = provider.requests[1]
    joined = " ".join(message.content.value for message in second.messages)
    assert "WRAPPED::" in joined
    assert "hello-note" in joined


def _dispatch_structured(hooks, structured_content):
    return dispatch_tool_result_hooks(
        hooks,
        tool_name="stats",
        content="2 files",
        is_error=False,
        cwd=".",
        has_ui=False,
        structured_content=structured_content,
    )


def test_tool_result_hooks_see_and_decide_the_structured_content() -> None:
    # MCP parity plan S04 (CMD-25), Pi ``emitToolResult``: a transform
    # replaces the content and decides the structured content with it.
    # Pi: "keeps structured content that tool_result handlers replace along
    # with the content", at the hook dispatcher.
    original = {"files": 2, "names": ["a", "b"]}
    seen: list[object] = []

    def observe(event, ctx):
        seen.append(event.structured_content)

    def replace_both(event, ctx):
        return ToolResultTransform(
            content="0 files", structured_content={"files": 0, "names": []}
        )

    def content_only(event, ctx):
        return ToolResultTransform(content="redacted")

    def pass_through(event, ctx):
        return ToolResultTransform(
            content=event.content + "!", structured_content=event.structured_content
        )

    def crash(event, ctx):
        raise RuntimeError("boom")

    # No hook and an observing hook keep the tool's own value.
    assert _dispatch_structured((), original).structured_content is original
    assert _dispatch_structured((observe,), original) == ToolResultTransform(
        content="2 files", structured_content=original
    )
    # A later hook that returns nothing keeps what an earlier one set.
    assert _dispatch_structured(
        (replace_both, observe, crash), original
    ) == ToolResultTransform(
        content="0 files", structured_content={"files": 0, "names": []}
    )
    assert seen == [original, {"files": 0, "names": []}]
    # Replacing the content alone drops structured content that may not match.
    assert _dispatch_structured((content_only,), original) == ToolResultTransform(
        content="redacted"
    )
    # Handing the event's value back keeps it, without a copy.
    kept = _dispatch_structured((pass_through,), original)
    assert kept.content == "2 files!" and kept.structured_content is original


def test_structured_content_from_a_tool_result_hook_is_a_detached_json_copy() -> None:
    mutable = {"files": [1]}
    result = _dispatch_structured(
        (lambda event, ctx: ToolResultTransform("ok", structured_content=mutable),),
        None,
    )
    mutable["files"].append(2)
    assert result.structured_content == {"files": [1]}
    # A value that is not JSON is dropped, like one a tool returns.
    nested: list[object] = []
    for _ in range(500):
        nested = [nested]
    cyclic: list[object] = []
    cyclic.append(cyclic)
    # Nesting the encoder accepts is kept, whatever Python's frame limit.
    kept = _dispatch_structured(
        (lambda event, ctx: ToolResultTransform("ok", {"deep": nested}),), None
    )
    assert json.dumps(kept.structured_content).count("[") == 501
    for bad in ({1, 2}, float("nan"), {"k": object()}, {1: "a", "1": "b"}, cyclic):
        dropped = _dispatch_structured(
            (lambda event, ctx, bad=bad: ToolResultTransform("ok", bad),),
            {"files": 2},
        )
        assert dropped == ToolResultTransform(content="ok")


def test_a_structured_value_the_json_round_trip_refuses_is_dropped(
    monkeypatch,
) -> None:
    # How deep the encoder and decoder go differs between Python versions.
    from types import SimpleNamespace

    from pipy_harness.native.tools import base

    def too_deep(text: str) -> object:
        raise RecursionError("maximum recursion depth exceeded")

    monkeypatch.setattr(base, "json", SimpleNamespace(dumps=json.dumps, loads=too_deep))
    result = _dispatch_structured(
        (lambda event, ctx: ToolResultTransform("ok", {"files": 0}),), {"files": 2}
    )

    assert result == ToolResultTransform(content="ok")
