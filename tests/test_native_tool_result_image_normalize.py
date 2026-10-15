"""Tool result images are normalized and can be blocked, as in Pi (G7).

Pi ``normalizeToolResultImages`` (``utils/tool-result-images.ts``) runs every
tool result image through ``processImage`` after the ``tool_result`` hooks
(``AgentSession._afterToolCall``); ``convertToLlmWithBlockImages``
(``core/sdk.ts``) replaces tool result images with a text when
``images.blockImages`` is set.
"""

from __future__ import annotations

import base64
import io
import json
import random
import sys
from pathlib import Path
from typing import Any

import pytest
from PIL import Image
from test_native_agent_loop import (
    _EventSink,
    _provider_call,
    _provider_result,
    _ProviderTurn,
    _QueuedInputs,
    _RequestSource,
    _run_input,
    _StatusPolicy,
    _tool_result,
    _UsagePublisher,
)
from test_native_agent_nested_calls import _NestedTools, _Policy, _Runner

from pipy_harness.native.agent.content import (
    ImageContent,
    ProductContent,
    TextContent,
)
from pipy_harness.native.agent.loop import AgentLoop
from pipy_harness.native.agent.messages import (
    BLOCKED_IMAGE_PLACEHOLDER,
    NON_VISION_TOOL_IMAGE_PLACEHOLDER,
    AgentToolCall,
    AgentToolResultMessage,
    AgentUserMessage,
    with_user_images_blocked,
    without_blocked_images,
)
from pipy_harness.native.agent.nested_calls import NestedToolCallService
from pipy_harness.native.agent.provider_turn import ProviderTurnOutcome
from pipy_harness.native.agent.tools import ToolExecutionOutcome
from pipy_harness.native.image_process import (
    ImageResizeOptions,
    ResizedImage,
    decode_base64,
    format_dimension_note,
    process_image,
)
from pipy_harness.native.models import ProviderRequest
from pipy_harness.native.settings import SettingsManager
from pipy_harness.native.tool_result_images import (
    normalize_tool_result,
    normalize_tool_result_images,
    tool_result_normalizer,
)
from pipy_harness.native.tools.base import (
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
    ToolRequest,
)


def _image_bytes(
    width: int, height: int, image_format: str = "PNG", **params: Any
) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (200, 30, 30)).save(
        buffer, format=image_format, **params
    )
    return buffer.getvalue()


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _size(block: ImageContent) -> tuple[int, int]:
    with Image.open(io.BytesIO(base64.b64decode(block.data))) as image:
        return image.size


def _result(*blocks: TextContent | ImageContent) -> AgentToolResultMessage:
    return AgentToolResultMessage(
        tool_request_id="pipy-tool-call-1",
        tool_name="shot",
        content=ProductContent(
            "\n".join(b.text for b in blocks if isinstance(b, TextContent))
        ),
        provider_correlation_id="call-1",
        blocks=blocks,
    )


WIDE = ImageContent(_b64(_image_bytes(3000, 1000)), "image/png")
SMALL = ImageContent(_b64(_image_bytes(4, 4)), "image/png")
WIDE_NOTE = (
    "[Image: original 3000x1000, displayed at 2000x667. Multiply coordinates "
    "by 1.50 to map to original image.]"
)


def test_an_oversized_image_is_resized_and_followed_by_its_note() -> None:
    blocks = (TextContent("shot"), WIDE, TextContent("after"))
    normalized = normalize_tool_result_images(blocks)
    assert len(normalized) == 4
    assert normalized[0] is blocks[0] and normalized[3] is blocks[2]
    image = normalized[1]
    assert isinstance(image, ImageContent) and image.mime_type == "image/png"
    assert _size(image) == (2000, 667)
    assert normalized[2] == TextContent(WIDE_NOTE)


def test_an_image_within_the_limits_leaves_the_blocks_as_they_are() -> None:
    blocks = (TextContent("shot"), SMALL)
    assert normalize_tool_result_images(blocks) is blocks
    result = _result(*blocks)
    assert normalize_tool_result(result) is result


def test_a_jpg_type_becomes_jpeg_without_a_hint() -> None:
    raw = _image_bytes(4, 4, "JPEG")
    normalized = normalize_tool_result_images((ImageContent(_b64(raw), "image/jpg"),))
    assert normalized == (ImageContent(_b64(raw), "image/jpeg"),)


def test_another_type_is_converted_to_png_with_a_hint() -> None:
    raw = _image_bytes(4, 4, "BMP")
    normalized = normalize_tool_result_images((ImageContent(_b64(raw), "image/bmp"),))
    image, hint = normalized
    assert isinstance(image, ImageContent) and image.mime_type == "image/png"
    assert hint == TextContent("[Image converted from image/bmp to image/png.]")


def test_an_image_that_does_not_decode_is_kept() -> None:
    block = ImageContent(_b64(b"not an image"), "image/png")
    blocks = (block,)
    assert normalize_tool_result_images(blocks) is blocks
    other = (ImageContent(_b64(b"not an image"), "application/x-thing"),)
    assert normalize_tool_result_images(other) is other


def test_without_auto_resize_an_oversized_image_is_kept() -> None:
    blocks = (WIDE,)
    assert normalize_tool_result_images(blocks, auto_resize=False) is blocks


def test_data_that_decodes_to_nothing_is_kept_without_auto_resize() -> None:
    blocks = (ImageContent("!", "image/png"), ImageContent("!", "image/jpg"))
    assert normalize_tool_result_images(blocks, auto_resize=False) is blocks


def test_without_pillow_every_image_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    blocks = (WIDE, ImageContent(_b64(_image_bytes(4, 4, "BMP")), "image/bmp"))
    monkeypatch.setitem(sys.modules, "PIL", None)
    assert normalize_tool_result_images(blocks) is blocks


def test_an_image_over_the_byte_limit_is_encoded_smaller() -> None:
    noise = Image.frombytes("RGB", (300, 300), random.Random(0).randbytes(270000))
    buffer = io.BytesIO()
    noise.save(buffer, format="PNG")
    raw = buffer.getvalue()
    limit = len(_b64(raw)) // 2
    processed = process_image(
        raw, "image/png", options=ImageResizeOptions(max_bytes=limit)
    )
    assert processed is not None
    assert len(processed.data) < limit
    assert processed.hints and processed.hints[0].startswith(
        "[Image: original 300x300, displayed at "
    )


def test_an_image_that_never_fits_fails() -> None:
    raw = _image_bytes(8, 8)
    assert (
        process_image(raw, "image/png", options=ImageResizeOptions(max_bytes=1)) is None
    )


def test_a_jpeg_is_turned_by_its_exif_orientation_first() -> None:
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90 degrees clockwise
    raw = _image_bytes(3000, 1000, "JPEG", exif=exif.tobytes())
    processed = process_image(raw, "image/jpeg")
    assert processed is not None
    assert processed.hints == (
        "[Image: original 1000x3000, displayed at 667x2000. Multiply coordinates "
        "by 1.50 to map to original image.]",
    )


def test_the_scale_is_rounded_as_javascript_to_fixed_rounds() -> None:
    resized = ResizedImage("", "image/png", 2250, 100, 2000, 89, was_resized=True)
    assert format_dimension_note(resized) == (
        "[Image: original 2250x100, displayed at 2000x89. Multiply coordinates "
        "by 1.13 to map to original image.]"
    )
    unchanged = ResizedImage("", "image/png", 10, 10, 10, 10, was_resized=False)
    assert format_dimension_note(unchanged) is None


def test_base64_is_decoded_as_node_decodes_it() -> None:
    assert decode_base64("aGVs bG8") == b"hello"
    assert decode_base64("aGVsbG8=trailing") == b"hello"
    assert decode_base64("-_8") == base64.b64decode("+/8=")


def test_a_result_gets_the_note_in_its_text() -> None:
    result = _result(TextContent("shot"), WIDE)
    normalized = normalize_tool_result(result)
    assert normalized.content.value == f"shot\n{WIDE_NOTE}"
    assert normalized.blocks[2] == TextContent(WIDE_NOTE)


def test_notes_that_overflow_the_text_cap_are_left_out() -> None:
    text = "x" * (AgentToolResultMessage.CONTENT_MAX_LENGTH - 10)
    normalized = normalize_tool_result(_result(TextContent(text), WIDE))
    assert normalized.content.value == text
    first, image = normalized.blocks
    assert first == TextContent(text)
    assert isinstance(image, ImageContent) and _size(image) == (2000, 667)


def test_the_normalizer_reads_auto_resize_for_every_result(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    settings = SettingsManager(global_path=path, env={})
    normalize = tool_result_normalizer(settings)
    result = _result(WIDE)
    assert normalize(result) is not result
    path.write_text(json.dumps({"images": {"autoResize": False}}))
    settings.reload()
    assert normalize(result) is result


def test_blocked_images_become_one_text_per_run() -> None:
    result = _result(
        TextContent("a"),
        WIDE,
        SMALL,
        TextContent(BLOCKED_IMAGE_PLACEHOLDER),
        TextContent(BLOCKED_IMAGE_PLACEHOLDER),
        TextContent("b"),
        SMALL,
    )
    plain = _result()
    (blocked, kept) = without_blocked_images((result, plain))
    assert kept is plain
    assert isinstance(blocked, AgentToolResultMessage) and blocked.blocks == ()
    assert blocked.content.value == "\n".join(
        ["a", BLOCKED_IMAGE_PLACEHOLDER, "b", BLOCKED_IMAGE_PLACEHOLDER]
    )
    history = (plain,)
    assert without_blocked_images(history) is history


def test_the_loop_normalizes_every_result_after_the_hooks() -> None:
    order: list[str] = []
    tools = _NestedTools(order)
    seen: list[tuple[str, str]] = []

    def normalize(result: AgentToolResultMessage) -> AgentToolResultMessage:
        seen.append((result.provider_correlation_id, result.content.value))
        return result

    def run(
        call: AgentToolCall, service: NestedToolCallService
    ) -> ToolExecutionOutcome:
        service.call("read", "{}")
        return ToolExecutionOutcome(_tool_result(call))

    loop = AgentLoop(
        request_source=_RequestSource(order),
        provider_turn=_ProviderTurn(
            order,
            [
                ProviderTurnOutcome(
                    result=_provider_result(
                        calls=(_provider_call("parent", name="codemode"),)
                    )
                ),
                ProviderTurnOutcome(result=_provider_result()),
            ],
        ),
        tool_capabilities=tools,
        tool_policy=_Policy(order),
        event_sink=_EventSink(order),
        usage_publisher=_UsagePublisher(order),
        queued_input_port=_QueuedInputs(),
        status_policy=_StatusPolicy(order),
        composite_runner=_Runner(run),
        nested_eligibility=tools,
        normalize_result=normalize,
    )
    loop.run(_run_input())
    assert seen == [
        ("parent/1", "tool result transformed"),
        ("parent", "tool result transformed"),
    ]


def test_a_normalizer_must_be_callable() -> None:
    order: list[str] = []
    with pytest.raises(TypeError, match="normalize_result"):
        AgentLoop(
            request_source=_RequestSource(order),
            provider_turn=_ProviderTurn(order, []),
            tool_capabilities=_NestedTools(order),
            tool_policy=_Policy(order),
            event_sink=_EventSink(order),
            usage_publisher=_UsagePublisher(order),
            queued_input_port=_QueuedInputs(),
            status_policy=_StatusPolicy(order),
            normalize_result="no",  # type: ignore[arg-type]
        )


class _WideShotTool:
    definition = ToolDefinition("shot", "Takes a picture.", {"type": "object"})

    def invoke(self, request: ToolRequest, context: ToolContext) -> ToolExecutionResult:
        del context
        return ToolExecutionResult(
            tool_request_id=request.tool_request_id,
            output_text="saved",
            blocks=(TextContent("saved"), WIDE),
        )


def _session_requests(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    settings: dict[str, object],
    prompt: str = "look",
) -> list[ProviderRequest]:
    from dataclasses import replace

    from test_native_coding_effects import _provider_mutation_fixture
    from test_native_semantic_compaction import _result as provider_result

    from pipy_harness.native.coding.session import CodingSession
    from pipy_harness.native.models import ProviderToolCall
    from pipy_harness.native.repl_state import NativeModelSelection

    _effects, provider_state, *_rest = _provider_mutation_fixture(
        tmp_path, selection=NativeModelSelection("openai", "gpt-5.5")
    )
    provider = provider_state.current_provider()
    requests: list[ProviderRequest] = []

    def complete(self: Any, request: ProviderRequest, **kwargs: Any) -> Any:
        requests.append(request)
        first = len(requests) == 1
        return replace(
            provider_result("" if first else "done"),
            provider_name=request.provider_name,
            model_id=request.model_id,
            tool_calls=(ProviderToolCall("call-1", "shot", "{}"),) if first else (),
        )

    monkeypatch.setattr(type(provider), "complete", complete)
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(settings))
    CodingSession(
        provider=provider,
        provider_state=provider_state,
        tool_registry={"shot": _WideShotTool()},
        settings_manager=SettingsManager(global_path=path, env={}),
    ).run(
        workspace_root=tmp_path,
        input_stream=io.StringIO(f"{prompt}\n/exit\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )
    assert len(requests) == 2
    return requests


def test_a_session_sends_the_resized_tool_result_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests = _session_requests(tmp_path, monkeypatch, {})
    sent = requests[1].messages[-1]
    assert isinstance(sent, AgentToolResultMessage)
    first, image, note = sent.blocks
    assert first == TextContent("saved") and note == TextContent(WIDE_NOTE)
    assert isinstance(image, ImageContent) and _size(image) == (2000, 667)
    assert sent.content.value == f"saved\n{WIDE_NOTE}"


def test_a_session_with_blocked_images_sends_the_text_instead(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests = _session_requests(
        tmp_path, monkeypatch, {"images": {"blockImages": True, "autoResize": False}}
    )
    sent = requests[1].messages[-1]
    assert isinstance(sent, AgentToolResultMessage)
    assert sent.blocks == ()
    assert sent.content.value == f"saved\n{BLOCKED_IMAGE_PLACEHOLDER}"
    assert NON_VISION_TOOL_IMAGE_PLACEHOLDER not in sent.content.value


@pytest.mark.parametrize("blocked", [False, True])
def test_blocked_images_also_keep_the_users_attachments_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, blocked: bool
) -> None:
    # Pi `convertToLlmWithBlockImages` replaces the images of a user
    # message as well: the `@image` attachments are not sent, and the user
    # message gains one placeholder for them.
    (tmp_path / "one.png").write_bytes(_image_bytes(4, 4))
    (tmp_path / "two.png").write_bytes(_image_bytes(6, 6))
    requests = _session_requests(
        tmp_path,
        monkeypatch,
        {"images": {"blockImages": blocked, "autoResize": False}},
        prompt="compare @image:one.png @image:two.png",
    )

    first = requests[0]
    (user,) = [
        message for message in first.messages if isinstance(message, AgentUserMessage)
    ]
    if not blocked:
        assert len(first.attachments) == 2
        assert BLOCKED_IMAGE_PLACEHOLDER not in user.content.value
        return
    assert first.attachments == ()
    assert user.content.value.endswith(f"\n{BLOCKED_IMAGE_PLACEHOLDER}")
    assert user.content.value.count(BLOCKED_IMAGE_PLACEHOLDER) == 1
    assert first.user_prompt == "compare @image:one.png @image:two.png"
    # The history keeps what the user wrote.
    stored = requests[1].messages[0]
    assert isinstance(stored, AgentUserMessage)
    assert BLOCKED_IMAGE_PLACEHOLDER not in stored.content.value


def test_a_blocked_attachment_does_not_count_toward_the_context_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The estimate measures the request as it is sent, so an attachment
    # that blockImages keeps back cannot push it past the context window.
    (tmp_path / "one.png").write_bytes(_image_bytes(4, 4))
    budget = {"enabled": False, "contextWindow": 3000, "reserveTokens": 0}
    requests = _session_requests(
        tmp_path,
        monkeypatch,
        {"images": {"blockImages": True}, "compaction": budget},
        prompt="compare @image:one.png",
    )
    assert requests[0].attachments == ()


def test_blocking_user_images_marks_the_latest_user_message_or_the_prompt() -> None:
    older = AgentUserMessage(ProductContent("before"))
    latest = AgentUserMessage(ProductContent("see"))
    messages = (older, latest)

    blocked, prompt = with_user_images_blocked(messages, "see")

    assert blocked[0] is older
    assert blocked[1] == AgentUserMessage(
        ProductContent(f"see\n{BLOCKED_IMAGE_PLACEHOLDER}")
    )
    assert prompt == "see"
    assert messages[1].content.value == "see"
    assert with_user_images_blocked((), "") == ((), BLOCKED_IMAGE_PLACEHOLDER)
    full = AgentUserMessage(ProductContent("x" * AgentUserMessage.CONTENT_MAX_LENGTH))
    ((capped,), _prompt) = with_user_images_blocked((full,), "x")
    assert len(capped.content.value) == AgentUserMessage.CONTENT_MAX_LENGTH


def test_a_rows_inline_images_are_the_normalized_ones() -> None:
    # The terminal draws a row's images (ResultImages) from the completed
    # event, which carries the result after the hooks and the normalizer,
    # so an oversized image is drawn as the model sees it.
    from dataclasses import replace

    from pipy_harness.native.agent.events import ToolCallCompleted
    from pipy_harness.native.ui.state import RenderToolResult, UiState, reduce

    order: list[str] = []
    events = _EventSink(order)

    def run(
        call: AgentToolCall, service: NestedToolCallService
    ) -> ToolExecutionOutcome:
        del service
        shot = _result(TextContent("shot"), WIDE)
        return ToolExecutionOutcome(
            replace(
                shot,
                tool_request_id=f"pipy-tool-{call.provider_correlation_id}",
                tool_name=call.tool_name,
                provider_correlation_id=call.provider_correlation_id,
            )
        )

    class _KeepingPolicy(_Policy):
        def transform_result(
            self,
            call: AgentToolCall,
            result: AgentToolResultMessage,
            arguments: Any,
            parent_call_id: str | None,
            /,
        ) -> ProductContent:
            super().transform_result(call, result, arguments, parent_call_id)
            return result.content

    tools = _NestedTools(order)
    AgentLoop(
        request_source=_RequestSource(order),
        provider_turn=_ProviderTurn(
            order,
            [
                ProviderTurnOutcome(
                    result=_provider_result(
                        calls=(_provider_call("parent", name="codemode"),)
                    )
                ),
                ProviderTurnOutcome(result=_provider_result()),
            ],
        ),
        tool_capabilities=tools,
        tool_policy=_KeepingPolicy(order),
        event_sink=events,
        usage_publisher=_UsagePublisher(order),
        queued_input_port=_QueuedInputs(),
        status_policy=_StatusPolicy(order),
        composite_runner=_Runner(run),
        nested_eligibility=tools,
        normalize_result=normalize_tool_result,
    ).run(_run_input())
    (completed,) = (e for e in events.events if isinstance(e, ToolCallCompleted))
    _state, decisions = reduce(UiState(assistant_active=True), completed)
    (row,) = (d for d in decisions if isinstance(d, RenderToolResult))
    assert row.images is not None
    (image,) = row.images.images
    assert _size(image) == (2000, 667)
    assert WIDE_NOTE in row.images.text
