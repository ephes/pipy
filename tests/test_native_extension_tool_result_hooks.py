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

from pipy_harness.extensions import NO_STRUCTURED_CONTENT
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
    result = dispatch_tool_result_hooks(
        _hooks(workspace),
        tool_name=tool_name,
        content=content,
        is_error=is_error,
        cwd=str(workspace),
        has_ui=False,
    )
    # ``None``: no hook changed the result.
    return content if result is None else result.content


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
    """The result as the hooks leave it, as a transform of every field."""
    result = dispatch_tool_result_hooks(
        hooks,
        tool_name="stats",
        content="2 files",
        is_error=False,
        cwd=".",
        has_ui=False,
        structured_content=structured_content,
    )
    if result is None:
        return _final("2 files", structured_content)
    return result


def _final(content, structured_content=NO_STRUCTURED_CONTENT):
    return ToolResultTransform(
        content=content,
        structured_content=structured_content,
        blocks=(),
        is_error=False,
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
    assert _dispatch_structured((observe,), original) == _final(
        "2 files", structured_content=original
    )
    # A later hook that returns nothing keeps what an earlier one set.
    assert _dispatch_structured((replace_both, observe, crash), original) == _final(
        "0 files", structured_content={"files": 0, "names": []}
    )
    assert seen == [original, {"files": 0, "names": []}]
    # Replacing the content alone drops structured content that may not match.
    assert _dispatch_structured((content_only,), original) == _final("redacted")
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
        assert dropped == _final("ok")


def test_null_structured_content_is_a_value_hooks_see_and_keep() -> None:
    # Goal-check finding M6, Pi ``emitToolResult``: only ``undefined`` is no
    # structured content; ``null`` is a value a hook sees, keeps or sets.
    seen: list[object] = []

    def observe(event, ctx):
        seen.append(event.structured_content)

    def set_null(event, ctx):
        return ToolResultTransform(content="none", structured_content=None)

    kept = _dispatch_structured((observe,), None)
    assert kept.structured_content is None and seen == [None]
    assert _dispatch_structured((set_null,), {"files": 2}) == _final(
        "none", structured_content=None
    )
    # Without structured content the event says so, and a transform that
    # leaves it out has none.
    seen.clear()
    absent = _dispatch_structured((observe,), NO_STRUCTURED_CONTENT)
    assert seen == [NO_STRUCTURED_CONTENT]
    assert absent.structured_content is NO_STRUCTURED_CONTENT
    assert ToolResultTransform("x").structured_content is NO_STRUCTURED_CONTENT


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

    assert result == _final("ok")


_PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="


def test_a_tool_result_hook_sees_the_call_and_changes_every_field() -> None:
    # Goal-check findings M8, M13, E8 and T6, Pi ``emitToolResult``: the
    # event has the call's id, its parent's id, the arguments, the details
    # and the content blocks, and a handler may replace the content (images
    # included), ``details`` and ``isError``.
    from pipy_harness.extensions import ImageContent, TextContent

    image = ImageContent(_PNG, "image/png")
    seen: list[object] = []

    def observe(event, ctx):
        seen.append(
            (
                event.tool_call_id,
                event.parent_tool_call_id,
                dict(event.input),
                event.details,
                event.blocks,
                event.is_error,
            )
        )

    def flag(event, ctx):
        return ToolResultTransform(is_error=True, details={"checked": True})

    def drop_images(event, ctx):
        return ToolResultTransform(content="")

    result = dispatch_tool_result_hooks(
        (observe, flag, observe, drop_images, observe),
        tool_name="shot",
        content="caption",
        is_error=False,
        cwd=".",
        has_ui=False,
        structured_content={"ok": True},
        tool_call_id="p1/2",
        parent_tool_call_id="p1",
        tool_input={"path": "a.png"},
        details={"server": "s"},
        blocks=(TextContent("caption"), image),
    )

    assert seen == [
        (
            "p1/2",
            "p1",
            {"path": "a.png"},
            {"server": "s"},
            (TextContent("caption"), image),
            False,
        ),
        (
            "p1/2",
            "p1",
            {"path": "a.png"},
            {"checked": True},
            (TextContent("caption"), image),
            True,
        ),
        ("p1/2", "p1", {"path": "a.png"}, {"checked": True}, (), True),
    ]
    # Only the content was replaced last, so the flag and details stay; the
    # structured content goes with the replaced content.
    assert result == ToolResultTransform(
        content="", blocks=(), is_error=True, details={"checked": True}
    )


def test_a_tool_result_hook_replaces_the_content_with_blocks() -> None:
    from pipy_harness.extensions import ImageContent, TextContent

    image = ImageContent(_PNG, "image/png")

    def swap(event, ctx):
        return ToolResultTransform(blocks=(TextContent("a"), image, TextContent("b")))

    def text_blocks(event, ctx):
        return ToolResultTransform(blocks=(TextContent("x"), TextContent("y")))

    def invalid(event, ctx):
        return ToolResultTransform(blocks=("not a block",))  # type: ignore[arg-type]

    def bad_flag(event, ctx):
        return ToolResultTransform(is_error="yes")  # type: ignore[arg-type]

    swapped = _dispatch_structured((swap,), {"n": 1})
    assert swapped.content == "a\nb"
    assert swapped.blocks == (TextContent("a"), image, TextContent("b"))
    assert swapped.structured_content is NO_STRUCTURED_CONTENT
    # Blocks without an image are their text.
    texts = _dispatch_structured((text_blocks,), None)
    assert (texts.content, texts.blocks) == ("x\ny", ())
    # A transform with a field of the wrong kind changes nothing.
    assert _dispatch_structured((invalid, bad_flag), {"n": 1}) == _final(
        "2 files", {"n": 1}
    )


def test_invalid_details_change_nothing_and_later_hooks_run() -> None:
    cycle: dict[str, object] = {}
    cycle["cycle"] = cycle

    def too_deep(event, ctx):
        return ToolResultTransform(content="lost", details=cycle)

    def not_an_object(event, ctx):
        return ToolResultTransform(content="lost", details={"invalid": object()})

    def later(event, ctx):
        return ToolResultTransform(content=event.content + "!", details={"ok": 1})

    result = _dispatch_structured((too_deep, not_an_object, later), None)
    assert result.content == "2 files!" and result.details == {"ok": 1}
