"""Tool result images in each provider adapter family's request (S39).

Pi sends a tool result's image blocks in order to Anthropic and Bedrock, and
as its text followed by its images everywhere else. A model without image
input gets a placeholder text instead (Pi ``transformMessages``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentMessage,
    AgentSystemMessage,
    AgentToolCall,
    AgentToolResultMessage,
    AgentUserMessage,
    ProductContent,
)
from pipy_harness.native.agent.content import ImageContent, TextContent
from pipy_harness.native.agent.messages import (
    NON_VISION_TOOL_IMAGE_PLACEHOLDER,
    without_tool_result_images,
)
from pipy_harness.native.http import ProviderHTTPError
from pipy_harness.native.models import ProviderRequest, ProviderSystemMessage
from pipy_harness.native.provider import copilot_dynamic_headers
from pipy_harness.native.providers.anthropic_messages_wire import messages_payload
from pipy_harness.native.providers.chat_completions_wire import (
    ChatTranscriptOptions,
    chat_transcript,
)
from pipy_harness.native.providers.google_generate_content_wire import (
    gemini_contents,
)
from pipy_harness.native.providers.openai_responses_wire import responses_input
from pipy_harness.native.tools.base import (
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
    ToolRequest,
)

PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
JPEG = "/9j/4AAQSkZJRgABAQ=="
PNG_URL = f"data:image/png;base64,{PNG}"
JPEG_URL = f"data:image/jpeg;base64,{JPEG}"
IMAGE = ImageContent(PNG, "image/png")
PHOTO = ImageContent(JPEG, "image/jpeg")


def _result(
    correlation_id: str, *blocks: TextContent | ImageContent, text: str | None = None
) -> AgentToolResultMessage:
    joined = "\n".join(b.text for b in blocks if isinstance(b, TextContent))
    return AgentToolResultMessage(
        tool_request_id=f"pipy-tool-{correlation_id}",
        tool_name="shot",
        content=ProductContent(joined if text is None else text),
        provider_correlation_id=correlation_id,
        blocks=blocks,
    )


def _request(
    *results: AgentToolResultMessage,
    model_id: str = "model",
    system_prompt: str = "",
    **fields: Any,
) -> ProviderRequest:
    calls = tuple(
        AgentToolCall(result.provider_correlation_id, "shot", ProductContent("{}"))
        for result in results
    )
    messages: tuple[AgentMessage, ...] = (
        AgentUserMessage(ProductContent("look")),
        AgentAssistantMessage(ProductContent(""), calls),
        *results,
    )
    return ProviderRequest(
        system_prompt=system_prompt,
        user_prompt="look",
        provider_name="provider",
        model_id=model_id,
        cwd=Path("/unused"),
        messages=messages,
        **fields,
    )


MIXED = _result("call-1", TextContent("before"), IMAGE, TextContent("after"), PHOTO)
ONLY_IMAGE = _result("call-2", IMAGE)
PLAIN = _result("call-3", text="plain")


def test_anthropic_sends_the_blocks_in_order() -> None:
    payload: Any = messages_payload(
        _request(MIXED, ONLY_IMAGE, PLAIN),
        parse_error_class=ProviderHTTPError,
        coalesce_tool_results=True,
    )
    mixed, only_image, plain = payload[-1]["content"]
    assert mixed["content"] == [
        {"type": "text", "text": "before"},
        {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": PNG},
        },
        {"type": "text", "text": "after"},
        {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": JPEG},
        },
    ]
    # Pi: a result that is images only is led by a placeholder text.
    assert only_image["content"][0] == {"type": "text", "text": "(see attached image)"}
    assert only_image["content"][1]["type"] == "image"
    assert plain["content"] == "plain"
    # The Bedrock adapter uses the same converter without coalescing.
    separate: Any = messages_payload(
        _request(MIXED), parse_error_class=ProviderHTTPError
    )
    assert separate[-1]["content"][0]["content"] == mixed["content"]


def test_openai_responses_sends_the_text_then_the_images() -> None:
    items: Any = responses_input(
        _request(MIXED, ONLY_IMAGE, PLAIN), parse_error_class=ProviderHTTPError
    )
    outputs = [
        item["output"] for item in items if item.get("type") == "function_call_output"
    ]
    assert outputs == [
        [
            {"type": "input_text", "text": "before\nafter"},
            {"type": "input_image", "detail": "auto", "image_url": PNG_URL},
            {"type": "input_image", "detail": "auto", "image_url": JPEG_URL},
        ],
        [{"type": "input_image", "detail": "auto", "image_url": PNG_URL}],
        "plain",
    ]


def test_gemini_3_takes_the_images_inside_the_function_response() -> None:
    contents = gemini_contents(
        _request(MIXED, ONLY_IMAGE, PLAIN, model_id="gemini-3-pro-preview"),
        parse_error_class=ProviderHTTPError,
    )
    responses = [content["parts"][0]["functionResponse"] for content in contents[2:]]
    assert len(responses) == 3
    assert responses[0]["response"] == {"result": "before\nafter"}
    assert responses[0]["parts"] == [
        {"inlineData": {"mimeType": "image/png", "data": PNG}},
        {"inlineData": {"mimeType": "image/jpeg", "data": JPEG}},
    ]
    assert responses[1]["response"] == {"result": "(see attached image)"}
    assert responses[1]["parts"] == [
        {"inlineData": {"mimeType": "image/png", "data": PNG}}
    ]
    assert responses[2]["response"] == {"result": "plain"}
    assert "parts" not in responses[2]


@pytest.mark.parametrize("model_id", ["gemini-2.5-pro", "gemini-live-2.5-flash"])
def test_gemini_before_3_gets_the_images_in_a_user_turn_after_the_result(
    model_id: str,
) -> None:
    contents = gemini_contents(
        _request(MIXED, PLAIN, model_id=model_id), parse_error_class=ProviderHTTPError
    )
    mixed, images, plain = contents[2:]
    assert mixed["parts"] == [
        {
            "functionResponse": {
                "id": "call-1",
                "name": "shot",
                "response": {"result": "before\nafter"},
            }
        }
    ]
    assert images == {
        "role": "user",
        "parts": [
            {"text": "Tool result image:"},
            {"inlineData": {"mimeType": "image/png", "data": PNG}},
            {"inlineData": {"mimeType": "image/jpeg", "data": JPEG}},
        ],
    }
    assert "functionResponse" in plain["parts"][0]


def test_a_model_that_is_not_gemini_takes_the_images_inside_the_response() -> None:
    contents = gemini_contents(
        _request(ONLY_IMAGE, model_id="claude-sonnet-4-5"),
        parse_error_class=ProviderHTTPError,
    )
    assert len(contents) == 3
    assert "parts" in contents[2]["parts"][0]["functionResponse"]


def test_chat_completions_sends_the_images_of_a_run_of_results_in_one_user_message() -> (
    None
):
    messages = chat_transcript(_request(MIXED, ONLY_IMAGE, PLAIN)).messages
    assert messages[2:] == [
        {"role": "tool", "tool_call_id": "call-1", "content": "before\nafter"},
        {"role": "tool", "tool_call_id": "call-2", "content": "(see attached image)"},
        {"role": "tool", "tool_call_id": "call-3", "content": "plain"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Attached image(s) from tool result:"},
                {"type": "image_url", "image_url": {"url": PNG_URL}},
                {"type": "image_url", "image_url": {"url": JPEG_URL}},
                {"type": "image_url", "image_url": {"url": PNG_URL}},
            ],
        },
    ]
    assert chat_transcript(_request(PLAIN)).messages[-1]["role"] == "tool"


def test_chat_completions_keeps_a_later_system_message_out_of_a_run_of_results() -> (
    None
):
    request = _request(
        ONLY_IMAGE,
        PLAIN,
        system_prompt="SYS\n\nnew rules",
        system_messages=(
            ProviderSystemMessage(
                0, AgentSystemMessage(sections=(("preamble", "SYS"),))
            ),
            ProviderSystemMessage(
                3, AgentSystemMessage(sections=(("rules", "new rules"),))
            ),
        ),
    )
    messages = chat_transcript(
        request, ChatTranscriptOptions(supports_mid_convo_system_messages=True)
    ).messages
    roles = [message["role"] for message in messages]
    # Results stay together; the images follow the last of them.
    assert roles[-4:] == ["tool", "system", "tool", "user"]
    assert messages[-1]["content"][1]["image_url"]["url"] == PNG_URL


def test_a_model_without_image_input_gets_a_placeholder_per_run_of_images() -> None:
    two_in_a_row = _result("call-4", IMAGE, PHOTO, TextContent("after"), IMAGE)
    history = _request(MIXED, two_in_a_row, PLAIN).messages
    downgraded = without_tool_result_images(history)
    assert downgraded[:2] == history[:2]
    assert downgraded[-1] is history[-1]
    texts = [message.content.value for message in downgraded[2:4]]
    assert texts == [
        f"before\n{NON_VISION_TOOL_IMAGE_PLACEHOLDER}\nafter\n"
        f"{NON_VISION_TOOL_IMAGE_PLACEHOLDER}",
        f"{NON_VISION_TOOL_IMAGE_PLACEHOLDER}\nafter\n"
        f"{NON_VISION_TOOL_IMAGE_PLACEHOLDER}",
    ]
    assert NON_VISION_TOOL_IMAGE_PLACEHOLDER == (
        "(tool image omitted: model does not support images)"
    )
    for message in downgraded[2:4]:
        assert isinstance(message, AgentToolResultMessage)
        assert message.blocks == ()
    # Nothing to drop keeps the caller's tuple; the stored history is untouched.
    plain_history = _request(PLAIN).messages
    assert without_tool_result_images(plain_history) is plain_history
    assert history[2] is MIXED and MIXED.blocks


def test_a_downgraded_request_carries_no_image_in_any_family() -> None:
    request = _request(MIXED, ONLY_IMAGE)
    downgraded = ProviderRequest(
        system_prompt="",
        user_prompt="look",
        provider_name="provider",
        model_id="model",
        cwd=Path("/unused"),
        messages=without_tool_result_images(request.messages),
    )
    wires: list[Any] = [
        messages_payload(downgraded, parse_error_class=ProviderHTTPError),
        responses_input(downgraded, parse_error_class=ProviderHTTPError),
        gemini_contents(downgraded, parse_error_class=ProviderHTTPError),
        chat_transcript(downgraded).messages,
    ]
    for wire in wires:
        text = repr(wire)
        assert PNG not in text and JPEG not in text
        assert NON_VISION_TOOL_IMAGE_PLACEHOLDER in text


def test_copilot_requests_with_a_tool_result_image_are_vision_requests() -> None:
    with_image = copilot_dynamic_headers(_request(MIXED), images_sent=False)
    assert with_image["Copilot-Vision-Request"] == "true"
    plain = copilot_dynamic_headers(_request(PLAIN), images_sent=False)
    assert "Copilot-Vision-Request" not in plain
    downgraded = ProviderRequest(
        system_prompt="",
        user_prompt="look",
        provider_name="provider",
        model_id="model",
        cwd=Path("/unused"),
        messages=without_tool_result_images(_request(MIXED).messages),
    )
    assert "Copilot-Vision-Request" not in copilot_dynamic_headers(
        downgraded, images_sent=False
    )


class _ShotTool:
    definition = ToolDefinition("shot", "Takes a picture.", {"type": "object"})

    def invoke(self, request: ToolRequest, context: ToolContext) -> ToolExecutionResult:
        del context
        return ToolExecutionResult(
            tool_request_id=request.tool_request_id,
            output_text="saved",
            blocks=(TextContent("saved"), IMAGE),
        )


@pytest.mark.parametrize(
    ("provider_name", "model_id", "takes_images"),
    [
        ("openai", "gpt-5.5", True),
        # The row lists text only (Pi ``model.input``).
        ("mistral", "mistral-large-latest", False),
        # No row describes this model: nothing says it cannot.
        ("mistral", "a-model-without-a-row", True),
    ],
)
def test_a_session_sends_tool_result_images_to_models_that_take_them(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider_name: str,
    model_id: str,
    takes_images: bool,
) -> None:
    import io
    from dataclasses import replace

    from test_native_coding_effects import _provider_mutation_fixture
    from test_native_semantic_compaction import _result as provider_result

    from pipy_harness.native.coding.session import CodingSession
    from pipy_harness.native.models import ProviderToolCall
    from pipy_harness.native.repl_state import NativeModelSelection
    from pipy_harness.native.settings import SettingsManager

    monkeypatch.setenv("MISTRAL_API_KEY", "test-only")
    _effects, provider_state, *_rest = _provider_mutation_fixture(
        tmp_path, selection=NativeModelSelection(provider_name, model_id)
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
    CodingSession(
        provider=provider,
        provider_state=provider_state,
        tool_registry={"shot": _ShotTool()},
        settings_manager=SettingsManager(
            global_path=tmp_path / "settings.json", env={}
        ),
    ).run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("look\n/exit\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    assert len(requests) == 2
    sent = requests[1].messages[-1]
    assert isinstance(sent, AgentToolResultMessage)
    if takes_images:
        assert sent.blocks == (TextContent("saved"), IMAGE)
    else:
        assert sent.blocks == ()
        assert sent.content.value == f"saved\n{NON_VISION_TOOL_IMAGE_PLACEHOLDER}"
