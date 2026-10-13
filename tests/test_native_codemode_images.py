"""Codemode ``image()`` (MCP parity plan S39; CMD-28 to CMD-30).

Pi ``packages/codemode`` prelude ``image`` and
``extensions/codemode/execute.ts`` ``saveImages``/``truncateOutput``: a script
shows an image with ``image(value)``; the result attaches it after a line
with the path it was saved to, and images are never cut by the text budget.
"""

from __future__ import annotations

import base64
import json
import os
import re
import stat
from pathlib import Path
from typing import Any

import pytest
from codemode_driver import real_runtime_skip_reason
from test_native_codemode_delivery import (
    runtime,  # noqa: F401 - the real-runtime fixture
)
from test_native_codemode_host import _run as run_fake
from test_native_codemode_host import _sandbox_error, spawned  # noqa: F401
from test_native_codemode_host_runtime import _run as run_real
from test_native_codemode_host_runtime import paths, pids  # noqa: F401
from test_native_extension_tool_metadata import _result, _tool_results
from test_native_mcp_session import _Provider, _server, _write_mcp_json
from test_sdk_codemode_mcp import _config, _submit, _workspace

from pipy_harness.native.agent.content import ImageContent, TextContent
from pipy_harness.native.codemode import prelude
from pipy_harness.native.codemode.outcome import (
    ErrorKind,
    ScriptError,
    ScriptImage,
    ScriptLimits,
    ScriptOutcome,
)
from pipy_harness.native.codemode.result import format_result
from pipy_harness.native.codemode.runtime import CodemodePaths
from pipy_harness.native.coding.codemode_runner import (
    result_content,
    save_image_file,
)
from pipy_harness.native.mcp.options import McpOptions
from pipy_harness.native.models import ProviderToolCall
from pipy_harness.native.session_builtins import CodemodeOptions

PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
JPEG = base64.b64encode(b"\xff\xd8\xff\xe0" + bytes(8)).decode()
GIF = base64.b64encode(b"GIF89a" + bytes(6)).decode()
WEBP = base64.b64encode(b"RIFF" + bytes(4) + b"WEBPVP8 " + bytes(8)).decode()
PNG_URL = f"data:image/png;base64,{PNG}"
HEADER = "Script completed\nWall time 0.0 seconds\nOutput:\n"
LABEL = re.compile(r"^\[Image saved to (\S+\.png) \(image/png, \d+B\)\]$")


# -- CMD-28: what image() accepts and rejects ----------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (PNG_URL, (PNG, "image/png")),
        ({"image_url": PNG_URL}, (PNG, "image/png")),
        # An image block as MCP tools return it.
        ({"type": "image", "data": PNG, "mimeType": "image/png"}, (PNG, "image/png")),
        ({"type": "image", "data": PNG_URL}, (PNG, "image/png")),
        # The detected type wins over the declared one.
        (f"data:image/jpeg;base64,{PNG}", (PNG, "image/png")),
        (
            {"type": "image", "data": JPEG, "mimeType": "image/png"},
            (JPEG, "image/jpeg"),
        ),
        (f"DATA:image/gif;charset=x;BASE64,{GIF}", (GIF, "image/gif")),
        (f"data:;base64,{WEBP}", (WEBP, "image/webp")),
        # Line breaks of wrapped base64 are dropped.
        (f"data:image/png;base64,{PNG[:20]}\n{PNG[20:]}\r\n", (PNG, "image/png")),
    ],
)
def test_image_accepts_data_urls_image_url_objects_and_image_blocks(
    value: Any, expected: tuple[str, str]
) -> None:
    assert prelude.checked_image(value) == expected


_REMOTE = "remote image URLs are not supported in tool outputs. Pass a base64 data URI instead"
_NOT_DATA = "invalid image output. Pass a base64 data URI instead"
_NOT_BASE64 = (
    "invalid image output. The image data is not valid base64 (truncated or corrupted?)"
)
_NOT_IMAGE = (
    "invalid image output. The image data is not a PNG, JPEG, GIF, or WebP image"
)


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("https://example.com/a.png", _REMOTE),
        ("HTTP://example.com/a.png", _REMOTE),
        ("", prelude.IMAGE_HELPER_EXPECTS),
        (None, prelude.IMAGE_HELPER_EXPECTS),
        (3, prelude.IMAGE_HELPER_EXPECTS),
        ([PNG_URL], prelude.IMAGE_HELPER_EXPECTS),
        ({"image_url": 3}, prelude.IMAGE_HELPER_EXPECTS),
        ({"image_url": None}, prelude.IMAGE_HELPER_EXPECTS),
        ({"data": PNG}, prelude.IMAGE_HELPER_EXPECTS),
        ({"type": 3, "data": PNG}, prelude.IMAGE_HELPER_EXPECTS),
        ({"type": "text", "text": "x"}, 'image only accepts image blocks, got "text"'),
        ({"type": "image"}, "image expected image block data"),
        ({"type": "image", "data": ""}, "image expected image block data"),
        ({"type": "image", "data": 3}, "image expected image block data"),
        ("shot.png", _NOT_DATA),
        ("file:///tmp/shot.png", _NOT_DATA),
        (f"data:image/png,{PNG}", _NOT_DATA),
        ("data:image/png;base64", _NOT_DATA),
        ("data:image/png;base64,", _NOT_BASE64),
        (f"data:image/png;base64,{PNG[:-3]}", _NOT_BASE64),
        (f"data:image/png;base64,{PNG[:-4]}!!!!", _NOT_BASE64),
        (f"data:image/png;base64,{PNG[:-4]}====", _NOT_BASE64),
        ({"type": "image", "data": "aGVsbG8gd29ybGQh"}, _NOT_IMAGE),
        # JPEG-LS is not a JPEG a provider takes.
        (
            f"data:image/jpeg;base64,{base64.b64encode(bytes([255, 216, 255, 247]) + bytes(8)).decode()}",
            _NOT_IMAGE,
        ),
        ("data:image/svg+xml;base64,PHN2Zy8+", _NOT_IMAGE),
    ],
)
def test_image_rejects_everything_else_with_pis_messages(
    value: Any, message: str
) -> None:
    with pytest.raises(TypeError) as raised:
        prelude.checked_image(value)
    assert str(raised.value) == message


def test_the_guests_image_cap_is_the_hosts() -> None:
    assert prelude.MAX_IMAGE_CHARS == ScriptLimits().image_bytes == 16 * 1024 * 1024


def test_the_image_that_passes_the_cap_raises_and_is_not_emitted() -> None:
    emitted: list[tuple[str, str]] = []
    image = prelude.image_helper(
        lambda data, mime_type: emitted.append((data, mime_type)), 2 * len(PNG)
    )
    image(PNG_URL)
    image({"type": "image", "data": PNG})
    with pytest.raises(ValueError, match=f"exceeded {2 * len(PNG)} characters"):
        image(PNG_URL)
    with pytest.raises(TypeError):
        image("https://example.com/a.png")
    assert emitted == [(PNG, "image/png")] * 2


# -- the host's side of the image message ---------------------------------------


def _image_line(data: str = PNG, mime_type: str = "image/png") -> bytes:
    # As the prelude encodes it: compact, ``type`` first.
    return prelude.encode({"type": "image", "data": data, "mimeType": mime_type})


def test_the_host_keeps_images_in_output_order(monkeypatch, spawned) -> None:  # noqa: F811
    scenario = (
        "start()\n"
        "send({'type': 'text', 'value': 'before'})\n"
        f"raw({_image_line()!r})\n"
        "send({'type': 'text', 'value': 'after'})\n"
        f"send({{'type': 'image', 'data': {JPEG!r}, 'mimeType': 'image/jpeg'}})\n"
        "done()\nexit_status()\n"
    )
    outcome = run_fake(monkeypatch, scenario)
    assert outcome.ok, outcome
    assert outcome.output == (
        "before",
        ScriptImage(PNG, "image/png"),
        "after",
        ScriptImage(JPEG, "image/jpeg"),
    )
    assert PNG not in repr(outcome)


@pytest.mark.parametrize(
    "message",
    [
        {"type": "image", "data": PNG},
        {"type": "image", "data": 3, "mimeType": "image/png"},
        {"type": "image", "data": PNG[:-3], "mimeType": "image/png"},
        {"type": "image", "data": "aGVsbG8gd29ybGQh", "mimeType": "image/png"},
        # Not the type the data shows.
        {"type": "image", "data": PNG, "mimeType": "image/jpeg"},
        {"type": "image", "data": PNG, "mimeType": "text/html"},
    ],
)
def test_an_image_message_the_prelude_would_not_send_is_a_violation(
    monkeypatch,
    spawned,  # noqa: F811
    message: dict[str, Any],
) -> None:
    scenario = f"start()\nsend({message!r})\nhang()\n"
    outcome = run_fake(monkeypatch, scenario)
    _sandbox_error(outcome, "an image message that is not an image")
    assert outcome.output == ()


def test_images_count_against_their_own_byte_cap_and_the_message_cap(
    monkeypatch,
    spawned,  # noqa: F811
) -> None:
    flood = f"start()\nwhile True:\n    raw({_image_line()!r})\n"
    by_bytes = run_fake(
        monkeypatch,
        flood,
        limits=ScriptLimits(wall_seconds=5.0, image_bytes=3 * len(PNG)),
    )
    assert by_bytes.error is not None and by_bytes.error.kind is ErrorKind.SCRIPT
    assert f"exceeded {3 * len(PNG)} bytes of images" in by_bytes.error.message
    assert by_bytes.output == (ScriptImage(PNG, "image/png"),) * 3

    by_count = run_fake(
        monkeypatch, flood, limits=ScriptLimits(wall_seconds=5.0, output_messages=5)
    )
    assert by_count.error is not None
    assert "exceeded 5 messages" in by_count.error.message
    assert len(by_count.output) == 5
    # Text does not use up the image cap, nor images the text cap.
    mixed = run_fake(
        monkeypatch,
        "start()\n"
        "send({'type': 'text', 'value': 'x' * 900})\n"
        f"raw({_image_line()!r})\n"
        "done()\nexit_status()\n",
        limits=ScriptLimits(wall_seconds=5.0, output_bytes=1000, image_bytes=len(PNG)),
    )
    assert mixed.ok, mixed


def test_only_an_image_line_may_be_longer_than_the_line_limit(
    monkeypatch,
    spawned,  # noqa: F811
) -> None:
    big = PNG[:-4] + "A" * 100_000 + PNG[-4:]
    assert prelude.is_base64(big) and prelude.image_type(big) == "image/png"
    limits = ScriptLimits(wall_seconds=5.0, line_bytes=64 * 1024)
    scenario = f"start()\nraw({_image_line(big)!r})\ndone()\nexit_status()\n"
    outcome = run_fake(monkeypatch, scenario, limits=limits)
    assert outcome.ok, outcome
    assert outcome.output == (ScriptImage(big, "image/png"),)

    # A long line that only starts like an image is still measured, by the
    # image cap, and is refused when it turns out not to be one.
    tight = ScriptLimits(wall_seconds=5.0, line_bytes=64 * 1024, image_bytes=80_000)
    too_long = run_fake(monkeypatch, scenario, limits=tight)
    _sandbox_error(too_long, "a guest line exceeded 65536 bytes")
    # A second ``type`` key wins in JSON: such a line is another message and
    # has the limit of every other message, before anything acts on it.
    called: list[str] = []

    def call_tool(name: str, arguments_json: str) -> Any:
        called.append(name)
        raise AssertionError("an oversized call reached its tool")

    smuggled = (
        "start()\n"
        'raw(b\'{"type":"image","type":"call","id":1,"name":"read","args":{"x":"\''
        ' + b"x" * 100_000 + b\'"}}\\n\')\n'
        "hang()\n"
    )
    _sandbox_error(
        run_fake(monkeypatch, smuggled, limits=limits, call_tool=call_tool),
        "a guest line exceeded 65536 bytes",
    )
    assert called == []
    smuggled_text = smuggled.replace(
        '"type":"call","id":1,"name":"read","args":{"x":"', '"type":"text","value":"'
    ).replace('"}}', '"}')
    flooded = run_fake(monkeypatch, smuggled_text, limits=limits)
    _sandbox_error(flooded, "a guest line exceeded 65536 bytes")
    assert flooded.output == ()
    disguised = (
        "start()\n"
        'raw(b\'{"type":"image","data":"\' + b"x" * 100_000 + b\'"}\\n\')\n'
        "hang()\n"
    )
    _sandbox_error(
        run_fake(monkeypatch, disguised, limits=limits),
        "an image message that is not an image",
    )
    text = (
        "start()\n"
        'raw(b\'{"type":"text","value":"\' + b"x" * 100_000 + b\'"}\\n\')\n'
        "hang()\n"
    )
    _sandbox_error(
        run_fake(monkeypatch, text, limits=limits), "a guest line exceeded 65536 bytes"
    )


# -- CMD-29, CMD-30: the result ---------------------------------------------------


def _outcome(*output: str | ScriptImage, error: ScriptError | None = None) -> Any:
    return ScriptOutcome(error, output=output)


IMAGE = ScriptImage(PNG, "image/png")
PHOTO = ScriptImage(JPEG, "image/jpeg")


def test_a_result_without_images_is_the_text_it_was() -> None:
    formatted = format_result(_outcome("one\n", "two"))
    assert formatted.text == HEADER + "one\ntwo"
    assert (formatted.header, formatted.items) == (HEADER, ("one\ntwo",))
    assert result_content(formatted.header, formatted.items) == (
        formatted.text,
        (TextContent(formatted.text),),
    )
    empty = format_result(_outcome())
    assert result_content(empty.header, empty.items)[0] == HEADER == empty.text


def test_images_stay_in_output_order_each_after_its_saved_path() -> None:
    saved: list[tuple[bytes, str]] = []

    def save(data: bytes, extension: str) -> str:
        saved.append((data, extension))
        return f"/tmp/image-{len(saved)}{extension}"

    formatted = format_result(_outcome("captured", IMAGE, IMAGE, "after\n", PHOTO))
    assert formatted.items == ("captured", IMAGE, IMAGE, "after\n", PHOTO)
    assert formatted.text == HEADER + "captured\nafter\n"

    text, blocks = result_content(formatted.header, formatted.items, save)

    png_label = "[Image saved to /tmp/image-1.png (image/png, 70B)]"
    jpeg_label = "[Image saved to /tmp/image-2.jpg (image/jpeg, 12B)]"
    assert blocks == (
        TextContent(f"{HEADER}captured\n{png_label}"),
        ImageContent(PNG, "image/png"),
        # The same image again: the same file.
        TextContent(png_label),
        ImageContent(PNG, "image/png"),
        TextContent(f"after\n{jpeg_label}"),
        ImageContent(JPEG, "image/jpeg"),
    )
    assert text == "\n".join(
        [HEADER + "captured", png_label, png_label, "after", jpeg_label]
    )
    assert saved == [(base64.b64decode(PNG), ".png"), (base64.b64decode(JPEG), ".jpg")]


def test_a_failed_save_is_named_and_keeps_the_image() -> None:
    def save(data: bytes, extension: str) -> str:
        raise OSError("No space left on device")

    formatted = format_result(_outcome(IMAGE))
    text, blocks = result_content(formatted.header, formatted.items, save)
    label = "[Image (image/png, 70B) could not be saved: No space left on device]"
    assert blocks == (TextContent(HEADER + label), ImageContent(PNG, "image/png"))
    assert text == HEADER + label


def test_a_saved_image_is_a_file_only_its_user_can_read(tmp_path: Path) -> None:
    data = base64.b64decode(PNG)
    path = Path(save_image_file(data, ".png"))
    try:
        assert path.name.startswith("pipy-codemode-") and path.suffix == ".png"
        assert path.read_bytes() == data
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    finally:
        path.unlink()


def test_images_follow_a_truncated_text_and_are_never_cut() -> None:
    # CMD-30; Pi "moves images after the truncated text".
    rows = [f"row {index}\n" for index in range(100)]
    outcome = _outcome(*rows[:50], IMAGE, *rows[50:], PHOTO)
    formatted = format_result(outcome, max_output_tokens=30)
    shown, *images = formatted.items
    assert isinstance(shown, str)
    assert shown.startswith("Warning: truncated output (original token count: ")
    assert "tokens truncated…" in shown
    assert images == [IMAGE, PHOTO]
    # The budget is for the text of the items joined by newlines.
    assert formatted.full_output == "".join(rows[:50]) + "\n" + "".join(rows[50:])
    assert formatted.text == HEADER + shown

    text, blocks = result_content(
        formatted.header, formatted.items, lambda data, extension: f"/t/x{extension}"
    )
    assert [type(block) for block in blocks] == [
        TextContent,
        ImageContent,
        TextContent,
        ImageContent,
    ]
    assert blocks[1] == ImageContent(PNG, "image/png")
    assert blocks[3] == ImageContent(JPEG, "image/jpeg")
    # An image alone is not text to cut.
    only = format_result(_outcome(IMAGE), max_output_tokens=1)
    assert only.items == (IMAGE,) and only.full_output is None


def test_a_failed_run_keeps_its_images_before_the_error() -> None:
    error = ScriptError(ErrorKind.SCRIPT, "boom")
    formatted = format_result(_outcome("partial", IMAGE, error=error))
    assert formatted.is_error is True
    assert formatted.items[:2] == ("partial", IMAGE)
    assert isinstance(formatted.items[2], str)
    assert formatted.items[2].startswith("Script error:\nboom")


# -- the real guest -----------------------------------------------------------------

needs_runtime = pytest.mark.skipif(
    real_runtime_skip_reason() is not None, reason=str(real_runtime_skip_reason())
)


@needs_runtime
def test_a_real_script_shows_images_and_gets_errors_for_what_is_not_one(
    paths: CodemodePaths,  # noqa: F811
    pids: list[int],  # noqa: F811
) -> None:
    code = f"""
png = {PNG!r}
text("first")
image("data:image/png;base64," + png)
image({{"image_url": "data:image/png;base64," + png}})
image({{"type": "image", "data": png, "mimeType": "image/png"}})
for bad in ("https://example.com/a.png", "data:image/png;base64,aGVsbG8gd29ybGQh",
            {{"type": "text", "text": "x"}}, "data:image/png;base64," + png[:-3]):
    try:
        image(bad)
    except TypeError as error:
        text(str(error))
text("last")
"""
    outcome = run_real(paths, code)
    assert outcome.ok, outcome
    shown = ScriptImage(PNG, "image/png")
    assert outcome.output == (
        "first",
        shown,
        shown,
        shown,
        _REMOTE,
        _NOT_IMAGE,
        'image only accepts image blocks, got "text"',
        _NOT_BASE64,
        "last",
    )


@needs_runtime
def test_an_uncaught_image_error_fails_the_script_with_its_message(
    paths: CodemodePaths,  # noqa: F811
    pids: list[int],  # noqa: F811
) -> None:
    outcome = run_real(paths, "image('https://example.com/a.png')\n")
    assert outcome.error is not None and outcome.error.kind is ErrorKind.SCRIPT
    assert _REMOTE in outcome.error.message
    assert outcome.output == ()


_SHOT = {
    "name": "shot",
    "description": "Take a screenshot.",
    "inputSchema": {"type": "object", "properties": {}},
}
_SHOT_RESULT = {
    "content": [
        {"type": "text", "text": "captured"},
        {"type": "image", "data": PNG, "mimeType": "image/png"},
    ]
}
_SCRIPT = f"""
shot = tools.mcp__docs__shot({{}})
text(shot["content"][0]["text"])
image(shot["content"][1])
image("data:image/png;base64,{PNG}")
text("after")
"""


def test_a_session_attaches_only_the_images_the_script_shows(
    tmp_path: Path,
    monkeypatch,
    runtime,  # noqa: F811
) -> None:
    """Pi: "attaches only the images the script passes to image(), in output
    order, each after its saved path" (``agent-session-codemode.test.ts``),
    with an MCP tool's image block as the image."""

    config = _config(tmp_path, monkeypatch, defaultTools=["+codemode"])
    workspace = _workspace(tmp_path)
    entry, _record = _server(
        tmp_path / "servers", tools=[_SHOT], toolResults={"shot": _SHOT_RESULT}
    )
    _write_mcp_json(config / "mcp.json", {"docs": {**entry, "exposure": "direct"}})
    provider = _Provider(
        [
            _result(
                tool_calls=(
                    ProviderToolCall("c1", "codemode", json.dumps({"code": _SCRIPT})),
                )
            ),
            _result(final_text="done"),
        ]
    )

    _submit(workspace, provider, mcp=McpOptions(), codemode=CodemodeOptions())

    (result,) = _tool_results(provider.requests[1])
    assert result.is_error is False, result.content.value
    lines = result.content.value.split("\n")
    assert lines[:3] == ["Script completed", lines[1], "Output:"]
    # The same image shown twice is saved once, so both labels name one file.
    assert lines[3:] == ["captured", lines[4], lines[4], "after"]
    match = LABEL.match(lines[4])
    assert match is not None, lines[4]
    saved = Path(match.group(1))
    try:
        assert saved.read_bytes() == base64.b64decode(PNG)
        assert stat.S_IMODE(os.stat(saved).st_mode) == 0o600
    finally:
        saved.unlink()
    head = "\n".join(lines[:5])
    assert result.blocks == (
        TextContent(head),
        ImageContent(PNG, "image/png"),
        TextContent(lines[4]),
        ImageContent(PNG, "image/png"),
        TextContent("after"),
    )
    # The nested call's own image went to the script, not to the model.
    assert result.details is not None
    assert [call["name"] for call in result.details["nestedCalls"]["calls"]] == [
        "mcp__docs__shot"
    ]
