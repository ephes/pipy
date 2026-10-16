"""Images in tool results (Pi ``ToolResultMessage.content``, MCP parity S39).

A tool result that has an image keeps Pi's ordered text and image blocks next
to its text. The executor copies them onto the result message, the session
file stores them, and the JSON and RPC modes emit them.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentToolCall,
    AgentToolResultMessage,
    ProductContent,
)
from pipy_harness.native.agent.content import (
    ImageContent,
    TextContent,
    tool_result_images,
    tool_result_text,
)
from pipy_harness.native.agent.history import estimate_message_tokens
from pipy_harness.native.agent.loop import _transform_tool_result
from pipy_harness.native.agent.loop_policy import AgentToolResultTransform
from pipy_harness.native.agent.request import (
    freeze_provider_request,
    validate_agent_tool_result_message,
)
from pipy_harness.native.agent.tools import ToolExecutor
from pipy_harness.native.automation.serialize import serialize_message
from pipy_harness.native.coding.compaction import _summary_message
from pipy_harness.native.coding.request_budget import estimate_request
from pipy_harness.native.coding.state import require_exact_agent_message
from pipy_harness.native.models import ProviderRequest
from pipy_harness.native.session_tree import (
    MessageEntry,
    NativeSessionTree,
    _entry_to_json,
    _UnresolvedToolResultMessage,
)
from pipy_harness.native.tools.base import (
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
    ToolRequest,
    make_tool_request_id,
)

PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
BLOCKS = (
    TextContent("before"),
    ImageContent(PNG, "image/png"),
    TextContent("after"),
)


def _result(
    blocks: tuple[Any, ...] = BLOCKS, text: str | None = None
) -> AgentToolResultMessage:
    return AgentToolResultMessage(
        tool_request_id="pipy-tool-1",
        tool_name="shot",
        content=ProductContent(tool_result_text(blocks) if text is None else text),
        provider_correlation_id="call-1",
        blocks=blocks,
    )


def test_an_image_holds_base64_data_and_a_mime_type_and_hides_them() -> None:
    image = ImageContent(PNG, "image/png")
    assert (image.data, image.mime_type) == (PNG, "image/png")
    assert PNG not in repr(image)
    for data, mime_type in (("", "image/png"), (PNG, ""), (b"x", "image/png")):
        with pytest.raises(TypeError):
            ImageContent(data, mime_type)  # type: ignore[arg-type]


def test_the_text_of_blocks_is_their_text_blocks_joined_by_newlines() -> None:
    assert tool_result_text(BLOCKS) == "before\nafter"
    assert tool_result_images(BLOCKS) == (ImageContent(PNG, "image/png"),)
    assert tool_result_text((ImageContent(PNG, "image/png"),)) == ""


def test_a_result_keeps_blocks_only_when_it_has_an_image() -> None:
    result = ToolExecutionResult(
        tool_request_id=make_tool_request_id(),
        output_text="before\nafter",
        blocks=BLOCKS,
    )
    assert result.blocks == BLOCKS
    text_only = ToolExecutionResult(
        tool_request_id=make_tool_request_id(),
        output_text="a\nb",
        blocks=(TextContent("a"), TextContent("b")),
    )
    assert text_only.blocks == ()
    assert _result((TextContent("a"),)).blocks == ()


@pytest.mark.parametrize(
    ("blocks", "error"),
    [
        (BLOCKS[:2], ValueError),  # the text is not the blocks' text
        (list(BLOCKS), TypeError),
        ((*BLOCKS, "text"), TypeError),
        ((TextContent("before\nafter", "signature"), BLOCKS[1]), TypeError),
    ],
)
def test_blocks_must_be_the_results_text_and_images(
    blocks: Any, error: type[Exception]
) -> None:
    with pytest.raises(error):
        ToolExecutionResult(
            tool_request_id=make_tool_request_id(),
            output_text="before\nafter",
            blocks=blocks,
        )
    with pytest.raises(error):
        _result(blocks, "before\nafter")


def test_images_take_part_in_history_equality() -> None:
    assert _result() == _result()
    assert _result() != _result((), "before\nafter")
    other = (BLOCKS[0], ImageContent(PNG, "image/jpeg"), BLOCKS[2])
    assert _result() != _result(other)


def test_a_result_changed_behind_its_constructor_is_refused() -> None:
    result = _result()
    object.__setattr__(result, "content", ProductContent("other"))
    with pytest.raises(ValueError, match="blocks"):
        validate_agent_tool_result_message(result)
    with pytest.raises(ValueError, match="blocks"):
        require_exact_agent_message(result, "message")
    text_only = _result((), "text")
    object.__setattr__(text_only, "blocks", (TextContent("text"),))
    with pytest.raises(ValueError, match="image"):
        validate_agent_tool_result_message(text_only)
    validate_agent_tool_result_message(_result())
    require_exact_agent_message(_result(), "message")


@pytest.mark.parametrize(
    ("index", "field", "value"),
    [(1, "data", []), (1, "data", ""), (1, "mime_type", None), (0, "text", 3)],
)
def test_a_block_changed_behind_its_constructor_is_refused(
    index: int, field: str, value: Any
) -> None:
    blocks = (
        TextContent(""),
        ImageContent(PNG, "image/png"),
    )
    result = _result(blocks)
    object.__setattr__(blocks[index], field, value)
    with pytest.raises(TypeError):
        validate_agent_tool_result_message(result)
    with pytest.raises(TypeError):
        require_exact_agent_message(result, "message")


class _ImageTool:
    definition = ToolDefinition("shot", "takes a picture", {"type": "object"})

    def invoke(self, request: ToolRequest, context: ToolContext) -> ToolExecutionResult:
        del context
        return ToolExecutionResult(
            tool_request_id=request.tool_request_id,
            output_text="before\nafter",
            blocks=BLOCKS,
        )


def test_the_executor_copies_the_blocks_onto_the_result_message(
    tmp_path: Path,
) -> None:
    call = AgentToolCall("call-1", "shot", ProductContent("{}"))
    outcome = ToolExecutor({"shot": _ImageTool()}).execute(
        call, ToolContext(workspace_root=tmp_path)
    )
    assert outcome.result.content == ProductContent("before\nafter")
    assert outcome.result.blocks == BLOCKS


class _Policy:
    def __init__(self, transformed: Any) -> None:
        self._transformed = transformed

    def before_execute(
        self, call: AgentToolCall, arguments: Any, parent_call_id: str | None, /
    ) -> Any:
        raise AssertionError("not used")

    def transform_result(
        self,
        call: AgentToolCall,
        result: AgentToolResultMessage,
        arguments: Any,
        parent_call_id: str | None,
        /,
    ) -> Any:
        del call
        return self._transformed(result)


def test_a_hook_that_replaces_the_content_replaces_the_images_too() -> None:
    call = AgentToolCall("call-1", "shot", ProductContent("{}"))
    result = _result()
    kept = _transform_tool_result(_Policy(lambda r: r.content), call, result, {}, None)
    assert kept is result
    replaced = _transform_tool_result(
        _Policy(lambda r: ProductContent("redacted")), call, result, {}, None
    )
    assert replaced.content == ProductContent("redacted")
    assert replaced.blocks == ()
    # A handler that only replaces the structured content keeps the images.
    structured = _transform_tool_result(
        _Policy(lambda r: AgentToolResultTransform(r.content, {"ok": True})),
        call,
        result,
        {},
        None,
    )
    assert structured.blocks == BLOCKS
    assert structured.structured_content == {"ok": True}


def test_a_summary_request_quotes_the_text_of_a_result_only() -> None:
    summarized = _summary_message(_result())
    assert isinstance(summarized, AgentToolResultMessage)
    assert summarized.content == ProductContent("before\nafter")
    assert summarized.blocks == ()


def test_an_image_counts_as_pis_estimate_in_token_estimates() -> None:
    with_image = _result()
    text_only = _result((), "before\nafter")
    assert estimate_message_tokens(text_only) == 3
    # Pi ``ESTIMATED_IMAGE_CHARS``: 4800 characters, 1200 tokens.
    assert estimate_message_tokens(with_image) == 1203

    def request(result: AgentToolResultMessage) -> ProviderRequest:
        call = AgentToolCall("call-1", "shot", ProductContent("{}"))
        return freeze_provider_request(
            ProviderRequest(
                system_prompt="",
                user_prompt="",
                provider_name="injected",
                model_id="unknown",
                cwd=Path("/unused"),
                messages=(AgentAssistantMessage(ProductContent(""), (call,)), result),
            )
        )

    plain = estimate_request(request(text_only), image_count=0, output_reserve=0)
    image = estimate_request(request(with_image), image_count=0, output_reserve=0)
    attached = estimate_request(request(text_only), image_count=1, output_reserve=0)
    assert image.image_tokens == attached.image_tokens > plain.image_tokens == 0
    assert image.total_tokens == attached.total_tokens


def test_json_and_rpc_messages_carry_the_ordered_blocks() -> None:
    assert serialize_message(_result())["content"] == [
        {"type": "text", "text": "before"},
        {"type": "image", "data": PNG, "mimeType": "image/png"},
        {"type": "text", "text": "after"},
    ]
    assert serialize_message(_result((), "plain"))["content"] == [
        {"type": "text", "text": "plain"}
    ]


def _path(tree: NativeSessionTree) -> Path:
    assert tree.path is not None
    return tree.path


def _tree(tmp_path: Path, *results: AgentToolResultMessage) -> NativeSessionTree:
    tree = NativeSessionTree.create(tmp_path, session_dir=tmp_path / "sessions")
    call = AgentToolCall("call-1", "shot", ProductContent("{}"))
    tree.append_message(AgentAssistantMessage(ProductContent(""), tool_calls=(call,)))
    for result in results:
        tree.append_message(result)
    return tree


def _stored_results(tree: NativeSessionTree) -> list[AgentToolResultMessage]:
    return [
        entry.message
        for entry in tree.get_entries()
        if isinstance(entry, MessageEntry)
        and isinstance(entry.message, AgentToolResultMessage)
    ]


def test_the_session_file_stores_and_restores_the_blocks(tmp_path: Path) -> None:
    tree = _tree(
        tmp_path,
        _result(),
        replace(_result((), "plain"), tool_request_id="pipy-tool-2"),
    )

    raw = [json.loads(line) for line in _path(tree).read_text().splitlines()]
    stored = [row["message"] for row in raw if row.get("type") == "message"]
    assert stored[1]["output_text"] == "before\nafter"
    assert stored[1]["blocks"] == [
        {"type": "text", "text": "before"},
        {"type": "image", "data": PNG, "mimeType": "image/png"},
        {"type": "text", "text": "after"},
    ]
    assert "blocks" not in stored[2]

    reopened = NativeSessionTree.open(_path(tree), strict=True)
    assert [result.blocks for result in _stored_results(reopened)] == [BLOCKS, ()]
    # The resumed provider history is the history that was stored.
    context = reopened.build_coding_context()
    assert context.messages[-2] == _result()


@pytest.mark.parametrize(
    "blocks",
    [
        "not a list",
        ["not an object"],
        [{"type": "audio", "data": "x"}],
        [{"type": "image", "data": PNG}],
        [{"type": "image", "data": "", "mimeType": "image/png"}],
        [{"type": "text", "text": 3}],
        # The blocks' text is not the stored text.
        [{"type": "image", "data": PNG, "mimeType": "image/png"}],
    ],
)
def test_stored_blocks_that_are_not_a_results_content_are_rejected(
    tmp_path: Path, blocks: Any
) -> None:
    tree = _tree(tmp_path, _result())
    lines = _path(tree).read_text().splitlines()
    row = json.loads(lines[-1])
    row["message"]["blocks"] = blocks
    lines[-1] = json.dumps(row)
    _path(tree).write_text("\n".join(lines) + "\n")
    with pytest.raises(ValueError):
        NativeSessionTree.open(_path(tree), strict=True)


def test_a_result_without_its_call_keeps_its_blocks_when_written_again(
    tmp_path: Path,
) -> None:
    tree = _tree(tmp_path, _result())
    lines = _path(tree).read_text().splitlines()
    row = json.loads(lines[-1])
    # No stored call has this id, so the result's tool name is unknown.
    row["message"]["provider_correlation_id"] = "call-unknown"
    lines[-1] = json.dumps(row)
    _path(tree).write_text("\n".join(lines) + "\n")

    reopened = NativeSessionTree.open(_path(tree))
    entry = reopened.get_entries()[-1]
    assert isinstance(entry, MessageEntry)
    assert isinstance(entry.message, _UnresolvedToolResultMessage)
    assert entry.message.blocks == BLOCKS
    assert _entry_to_json(entry)["message"]["blocks"] == row["message"]["blocks"]


def test_a_policy_transform_replaces_images_the_error_flag_and_details() -> None:
    # Goal-check findings M13 and T6, Pi ``afterToolCall``: a handler's
    # content replaces the whole content, ``[]`` included, and ``isError`` and
    # ``details`` replace the result's.
    call = AgentToolCall("call-1", "shot", ProductContent("{}"))
    image_only = _result((ImageContent(PNG, "image/png"),))
    assert image_only.content == ProductContent("")
    emptied = _transform_tool_result(
        _Policy(lambda r: AgentToolResultTransform(ProductContent(""), blocks=())),
        call,
        image_only,
        {},
        None,
    )
    assert emptied.blocks == () and emptied.content == ProductContent("")
    flagged = _transform_tool_result(
        _Policy(
            lambda r: AgentToolResultTransform(
                r.content, r.structured_content, is_error=True, details={"seen": 1}
            )
        ),
        call,
        _result(),
        {},
        None,
    )
    assert flagged.is_error and flagged.details == {"seen": 1}
    assert flagged.blocks == BLOCKS and flagged.content == _result().content
    swapped_blocks = (TextContent("new"), ImageContent(PNG, "image/png"))
    swapped = _transform_tool_result(
        _Policy(
            lambda r: AgentToolResultTransform(
                ProductContent("new"), blocks=swapped_blocks
            )
        ),
        call,
        _result(),
        {},
        None,
    )
    assert swapped.blocks == swapped_blocks
