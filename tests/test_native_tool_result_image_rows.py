"""Tool result images in rows and the HTML export (MCP parity plan S39).

Pi ``getTextOutput`` and pi-tui ``imageFallback``: a row that cannot draw an
image shows ``[Image: [image/png] 640x480]`` after the result's text, live,
after a resume and in an export.
"""

from __future__ import annotations

import base64
import json
import struct
from pathlib import Path
from typing import Any

import pytest
from test_native_export_distribution import _decode_html_payload
from test_native_export_tool_rows import _rendered, _text
from test_native_session_history_render import _box, _Terminal

from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentToolCall,
    AgentToolResultMessage,
    AgentUserMessage,
    ProductContent,
)
from pipy_harness.native.agent.content import ImageContent, TextContent
from pipy_harness.native.agent.events import ToolCallCompleted
from pipy_harness.native.export_distribution import (
    export_native_session_to_html,
    redact_export_value,
)
from pipy_harness.native.session_tree import NativeSessionTree
from pipy_harness.native.tool_result_display import (
    image_dimensions,
    image_fallback,
    tool_result_display_text,
)
from pipy_harness.native.ui.state import RenderToolResult, UiState, reduce


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


PNG = _b64(
    b"\x89PNG\r\n\x1a\n" + struct.pack(">I4sII", 13, b"IHDR", 640, 480) + bytes(8)
)
GIF = _b64(b"GIF89a" + struct.pack("<HH", 320, 200) + bytes(8))
# A metadata segment before the frame header, as cameras write it.
JPEG = _b64(
    b"\xff\xd8"
    + b"\xff\xe1"
    + struct.pack(">H", 60_002)
    + bytes(60_000)
    + b"\xff\xc0"
    + struct.pack(">HBHH", 17, 8, 600, 800)
    + bytes(16)
)
WEBP_LOSSY = _b64(
    b"RIFF" + bytes(4) + b"WEBPVP8 " + bytes(10) + struct.pack("<HH", 100, 50)
)
WEBP_LOSSLESS = _b64(
    b"RIFF"
    + bytes(4)
    + b"WEBPVP8L"
    + bytes(4)
    + b"\x2f"
    + struct.pack("<I", (100 - 1) | ((50 - 1) << 14))
    + bytes(8)
)
WEBP_EXTENDED = _b64(
    b"RIFF"
    + bytes(4)
    + b"WEBPVP8X"
    + bytes(8)
    + (100 - 1).to_bytes(3, "little")
    + (50 - 1).to_bytes(3, "little")
)


@pytest.mark.parametrize(
    ("data", "mime_type", "size"),
    [
        (PNG, "image/png", (640, 480)),
        (GIF, "image/gif", (320, 200)),
        (JPEG, "image/jpeg", (800, 600)),
        (WEBP_LOSSY, "image/webp", (100, 50)),
        (WEBP_LOSSLESS, "image/webp", (100, 50)),
        (WEBP_EXTENDED, "image/webp", (100, 50)),
        # Wrapped base64, as some servers send it.
        (PNG[:20] + "\n" + PNG[20:], "image/png", (640, 480)),
        # Not the type it claims, cut short, not base64, or a type without a reader.
        (GIF, "image/png", None),
        (PNG[:16], "image/png", None),
        (_b64(b"\xff\xd8\xff\xe1\x00\x00" + bytes(32)), "image/jpeg", None),
        ("?", "image/png", None),
        (PNG, "image/svg+xml", None),
    ],
)
def test_the_size_is_read_from_the_images_header(
    data: str, mime_type: str, size: tuple[int, int] | None
) -> None:
    assert image_dimensions(data, mime_type) == size


def test_the_fallback_line_is_pi_tuis() -> None:
    assert image_fallback(ImageContent(PNG, "image/png")) == (
        "[Image: [image/png] 640x480]"
    )
    assert image_fallback(ImageContent("AAAA", "image/svg+xml")) == (
        "[Image: [image/svg+xml]]"
    )


def _result(*blocks: Any, name: str = "shot", corr: str = "c1") -> Any:
    text = "\n".join(b.text for b in blocks if isinstance(b, TextContent))
    return AgentToolResultMessage(
        tool_request_id=f"pipy-tool-{corr}",
        tool_name=name,
        content=ProductContent(text),
        provider_correlation_id=corr,
        blocks=blocks,
    )


IMAGE = ImageContent(PNG, "image/png")
ANIMATION = ImageContent(GIF, "image/gif")


def test_a_rows_text_is_the_results_text_then_a_line_per_image() -> None:
    mixed = _result(TextContent("before"), IMAGE, TextContent("after"), ANIMATION)
    assert tool_result_display_text(mixed) == (
        "before\nafter\n[Image: [image/png] 640x480]\n[Image: [image/gif] 320x200]"
    )
    assert tool_result_display_text(_result(IMAGE)) == "[Image: [image/png] 640x480]"
    plain = AgentToolResultMessage(
        "pipy-tool-c1", "shot", ProductContent("plain"), "c1"
    )
    assert tool_result_display_text(plain) == "plain"


def test_a_live_row_shows_the_image_lines() -> None:
    start = UiState(assistant_active=True)
    _state, decisions = reduce(
        start, ToolCallCompleted(0, _result(TextContent("saved"), IMAGE))
    )
    assert decisions == (
        RenderToolResult(
            output_text="saved\n[Image: [image/png] 640x480]",
            is_error=False,
            duration_seconds=None,
        ),
    )


def test_a_resumed_session_draws_the_image_lines(tmp_path: Path) -> None:
    call = AgentToolCall("c1", "mcp__browser__shot", ProductContent("{}"))
    terminal = _Terminal(tmp_path)
    terminal.tree.append_message(AgentUserMessage(ProductContent("look")))
    terminal.tree.append_message(AgentAssistantMessage(ProductContent(""), (call,)))
    terminal.tree.append_message(
        _result(TextContent("saved"), IMAGE, name="mcp__browser__shot")
    )
    terminal.history.render_active_branch()

    boxes = [_box(lines) for kind, lines in terminal.rows() if kind == "tool_box"]
    assert boxes == [
        [
            ("success", "browser/shot"),
            ("success", "saved"),
            ("success", "[Image: [image/png] 640x480]"),
        ]
    ]


def _tree(tmp_path: Path, name: str, *blocks: Any) -> NativeSessionTree:
    cwd = tmp_path / "workspace"
    cwd.mkdir(parents=True, exist_ok=True)
    tree = NativeSessionTree.create(cwd, session_dir=tmp_path / "sessions")
    tree.append_message(AgentUserMessage(ProductContent("look")))
    call = AgentToolCall("c1", name, ProductContent("{}"))
    tree.append_message(AgentAssistantMessage(ProductContent(""), (call,)))
    tree.append_message(_result(*blocks, name=name))
    return tree


def test_an_exported_mcp_row_shows_the_image_line(tmp_path: Path) -> None:
    tree = _tree(tmp_path, "mcp__browser__shot", TextContent("saved"), IMAGE)
    rendered = _rendered(tree)
    assert _text(rendered["c1"]["resultHtmlExpanded"]) == [
        "saved",
        "[Image: [image/png] 640x480]",
    ]


def test_an_export_keeps_the_image_and_the_page_shows_it(tmp_path: Path) -> None:
    # Data that ends like an assignment a text redaction would cut.
    data = PNG[:-8] + "/token=="
    assert redact_export_value(data) != data
    tree = _tree(
        tmp_path, "shot", TextContent("saved"), ImageContent(data, "image/png")
    )

    output = export_native_session_to_html(tree, tmp_path / "out.html")

    payload = _decode_html_payload(output)
    stored = payload["entries"][-1]["message"]
    assert stored["output_text"] == "saved"
    assert stored["blocks"] == [
        {"type": "text", "text": "saved"},
        {"type": "image", "data": data, "mimeType": "image/png"},
    ]
    page = output.read_text(encoding="utf-8")
    assert "appendImages(msg.blocks)" in page and "tool-image" in page


def test_only_base64_image_data_is_exempt_from_redaction() -> None:
    block = {"type": "image", "data": "api_key=sk-12345678", "mimeType": "image/png"}
    assert "sk-12345678" not in json.dumps(redact_export_value(block))
    other = {"type": "text", "data": PNG[:-8] + "/token=="}
    assert redact_export_value(other)["data"] != other["data"]
