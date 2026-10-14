"""Tests for the model-driven `read` tool (Pi `read`, READ1).

These tests pin the read tool's definition, schema, workspace path safety,
Pi's truncation notices and offset/limit paging. They reuse the existing
`pipy_harness.native.read_only_tool` validation; the test set covers the
contract of the new tool, not the legacy `/read` slash command (already
covered by `tests/test_native_explicit_file_excerpt_tool.py`).
"""

from __future__ import annotations

import base64
import zlib
from pathlib import Path

import pytest

from pipy_harness.native.agent.content import (
    NO_STRUCTURED_CONTENT,
    ImageContent,
    TextContent,
)
from pipy_harness.native.tools import (
    ToolArgumentError,
    ToolContext,
    ToolExecutionResult,
    ToolPort,
    ToolRequest,
    make_tool_request_id,
)
from pipy_harness.native.tools.read import READ_OUTPUT_SCHEMA, ReadTool


def _make_request(arguments: dict[str, object]) -> ToolRequest:
    return ToolRequest(
        tool_request_id=make_tool_request_id(),
        tool_name="read",
        arguments=arguments,
    )


def test_read_tool_satisfies_tool_port_protocol():
    tool = ReadTool()

    assert isinstance(tool, ToolPort)


def test_read_tool_definition_is_object_schema_with_required_path():
    tool = ReadTool()

    definition = tool.definition

    assert definition.name == "read"
    assert definition.description == (
        "Read the contents of a file. Supports text files and images (jpg, png, "
        "gif, webp, bmp). Images are sent as attachments. For text "
        "files, output is truncated to 2000 lines or 50KB (whichever is hit "
        "first). Use offset/limit for large files. When you need the full "
        "file, continue with offset until complete."
    )
    schema = definition.input_schema
    assert schema == {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the file to read (relative or absolute)",
            },
            "offset": {
                "type": "integer",
                "description": "Line number to start reading from (1-indexed)",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of lines to read",
            },
        },
        "required": ["path"],
        "additionalProperties": False,
    }
    # Pi ``readOutputSchema``.
    assert (
        definition.output_schema
        == READ_OUTPUT_SCHEMA
        == {
            "anyOf": [
                {"type": "string"},
                {
                    "type": "object",
                    "properties": {
                        "type": {"const": "image", "type": "string"},
                        "data": {"type": "string"},
                        "mimeType": {"type": "string"},
                        "note": {"type": "string"},
                    },
                    "required": ["type", "data", "mimeType", "note"],
                },
            ]
        }
    )


def test_read_tool_returns_bounded_text_for_workspace_file(tmp_path: Path):
    target = tmp_path / "notes.txt"
    target.write_text("hello\nworld\n", encoding="utf-8")
    tool = ReadTool()
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request({"path": "notes.txt"})

    result = tool.invoke(request, context)

    assert result.is_error is False
    assert result.output_text == "hello\nworld\n"
    assert result.tool_request_id == request.tool_request_id


def _read_in(workspace: Path, path: str) -> ToolExecutionResult:
    return ReadTool().invoke(
        _make_request({"path": path}), ToolContext(workspace_root=workspace)
    )


def test_read_tool_reads_git_ignored_and_generated_paths_like_pi(tmp_path: Path):
    # READ2: Pi's read has no deny list: .git, .gitignore matches, generated
    # directories and suffixes all read.
    files = {
        ".git/config": "[core]\n",
        "ignored.txt": "ignored body\n",
        "node_modules/pkg/index.js": "module.exports = 1\n",
        "dist/bundle.min.js": "min\n",
        "types.d.ts": "declare const x: number\n",
        "yarn.lock": "lock\n",
        "app.js.map": "{}\n",
        ".pipy/cache.txt": "pipy cache\n",
        ".venv/pyvenv.cfg": "home = /usr\n",
        "secret_token.txt": "plain\n",
    }
    for relative, body in files.items():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    (tmp_path / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")

    for relative, body in files.items():
        result = _read_in(tmp_path, relative)
        assert result.is_error is False, relative
        assert result.output_text == body


def test_read_tool_reads_absolute_path_outside_cwd(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "elsewhere" / "notes.md"
    outside.parent.mkdir()
    outside.write_text("outside body\n", encoding="utf-8")

    result = _read_in(workspace, str(outside))

    assert result.is_error is False
    assert result.output_text == "outside body\n"


def test_read_tool_resolves_parent_traversal_against_cwd(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (tmp_path / "sibling.txt").write_text("sibling body\n", encoding="utf-8")

    result = _read_in(workspace, "../sibling.txt")

    assert result.is_error is False
    assert result.output_text == "sibling body\n"


def test_read_tool_expands_home_and_strips_at_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    home = tmp_path / "home"
    home.mkdir()
    (home / "notes.txt").write_text("home body\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "local.txt").write_text("local body\n", encoding="utf-8")

    assert _read_in(workspace, "~/notes.txt").output_text == "home body\n"
    assert _read_in(workspace, "@local.txt").output_text == "local body\n"
    assert _read_in(workspace, "@~/notes.txt").output_text == "home body\n"


def test_read_tool_finds_macos_screenshot_name_variant(tmp_path: Path):
    # macOS names screenshots with a narrow no-break space before AM/PM; the
    # model types a plain space (Pi tryMacOSScreenshotPath).
    name = "Screenshot 2026-09-30 at 10.15.00\u202fAM.txt"
    (tmp_path / name).write_text("screenshot text\n", encoding="utf-8")

    result = _read_in(tmp_path, "Screenshot 2026-09-30 at 10.15.00 AM.txt")

    assert result.is_error is False
    assert result.output_text == "screenshot text\n"


def test_read_tool_reads_the_bash_full_output_temp_file(tmp_path: Path):
    # TOOLS1: a truncated bash result names its full-output temp file in the
    # system temp directory; READ2 lets read open it, as in Pi.
    import re

    from pipy_harness.native.tools.bash import BashTool

    bash = BashTool().invoke(
        ToolRequest(
            tool_request_id=make_tool_request_id(),
            tool_name="bash",
            arguments={"command": "seq 1 3000"},
        ),
        ToolContext(workspace_root=tmp_path),
    )
    match = re.search(r"Full output: (\S+)\]$", bash.output_text)
    assert match is not None, bash.output_text
    full_path = Path(match.group(1))
    try:
        assert not full_path.is_relative_to(tmp_path)
        result = ReadTool().invoke(
            _make_request({"path": str(full_path), "offset": 1, "limit": 3}),
            ToolContext(workspace_root=tmp_path),
        )
        assert result.is_error is False
        assert result.output_text == (
            "1\n2\n3\n\n[2998 more lines in file. Use offset=4 to continue.]"
        )
    finally:
        full_path.unlink(missing_ok=True)


def test_read_tool_reports_an_invalid_file_url_as_an_error(tmp_path: Path):
    result = _read_in(tmp_path, "file://[broken/x.txt")

    assert result.is_error is True
    assert result.output_text == "read error: Invalid URL: file://[broken/x.txt"


def test_read_tool_rejects_non_string_path(tmp_path: Path):
    with pytest.raises(ToolArgumentError) as info:
        ReadTool().invoke(
            _make_request({"path": 7}), ToolContext(workspace_root=tmp_path)
        )

    assert info.value.field_path == ("path",)


def test_read_tool_reports_missing_file(tmp_path: Path):
    tool = ReadTool()
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request({"path": "no-such-file.txt"})

    result = tool.invoke(request, context)

    assert result.is_error is True
    assert "does not exist" in result.output_text


def test_read_tool_reports_directory_target(tmp_path: Path):
    (tmp_path / "subdir").mkdir()
    tool = ReadTool()
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request({"path": "subdir"})

    result = tool.invoke(request, context)

    assert result.is_error is True
    assert "not a regular file" in result.output_text


def _read(tmp_path: Path, name: str, **arguments: object) -> ToolExecutionResult:
    return ReadTool().invoke(
        _make_request({"path": name, **arguments}),
        ToolContext(workspace_root=tmp_path),
    )


def _numbered(count: int, *, trailing_newline: bool = True) -> str:
    body = "\n".join(f"line {i}" for i in range(1, count + 1))
    return body + "\n" if trailing_newline else body


def test_read_tool_returns_901_line_file_whole_without_notice(tmp_path: Path):
    (tmp_path / "rows.csv").write_text(_numbered(901), encoding="utf-8")

    result = _read(tmp_path, "rows.csv")

    assert result.is_error is False
    assert result.output_text == _numbered(901)
    assert "[Showing" not in result.output_text


def test_read_tool_line_truncation_notice_and_offset_continuation(tmp_path: Path):
    (tmp_path / "long.txt").write_text(_numbered(2500), encoding="utf-8")

    first = _read(tmp_path, "long.txt")
    rest = _read(tmp_path, "long.txt", offset=2001)

    # Pi counts the empty element after the trailing newline: 2501 lines.
    assert first.output_text == (
        _numbered(2000, trailing_newline=False)
        + "\n\n[Showing lines 1-2000 of 2501. Use offset=2001 to continue.]"
    )
    assert rest.is_error is False
    assert rest.output_text == "\n".join(f"line {i}" for i in range(2001, 2501)) + "\n"


def test_read_tool_byte_truncation_notice(tmp_path: Path):
    row = "x" * 99
    (tmp_path / "wide.csv").write_text((row + "\n") * 900, encoding="utf-8")

    result = _read(tmp_path, "wide.csv")

    # 100 bytes per line: 512 lines fit in 51200 bytes (511 separators).
    kept = "\n".join([row] * 512)
    assert result.output_text == (
        kept
        + "\n\n[Showing lines 1-512 of 901 (50.0KB limit). Use offset=513 to continue.]"
    )
    last = _read(tmp_path, "wide.csv", offset=513)
    assert last.output_text == (row + "\n") * 388
    assert "[Showing" not in last.output_text


def test_read_tool_first_line_over_limit_points_at_bash(tmp_path: Path):
    (tmp_path / "min.js").write_text("a" * 60000 + "\nshort\n", encoding="utf-8")

    result = _read(tmp_path, "min.js")

    assert result.is_error is False
    assert result.output_text == (
        "[Line 1 is 58.6KB, exceeds 50.0KB limit. Use bash: sed -n '1p' min.js "
        "| head -c 51200]"
    )
    second = _read(tmp_path, "min.js", offset=2)
    assert second.output_text == "short\n"


def test_read_tool_limit_stopping_early_reports_remaining_lines(tmp_path: Path):
    (tmp_path / "f.txt").write_text(_numbered(10, trailing_newline=False))

    result = _read(tmp_path, "f.txt", offset=3, limit=4)
    to_end = _read(tmp_path, "f.txt", offset=7, limit=4)
    past_end = _read(tmp_path, "f.txt", offset=9, limit=5)

    assert result.output_text == (
        "line 3\nline 4\nline 5\nline 6\n\n"
        "[4 more lines in file. Use offset=7 to continue.]"
    )
    assert to_end.output_text == "line 7\nline 8\nline 9\nline 10"
    assert past_end.output_text == "line 9\nline 10"


def test_read_tool_offset_beyond_end_is_an_error(tmp_path: Path):
    (tmp_path / "f.txt").write_text(_numbered(3))

    result = _read(tmp_path, "f.txt", offset=5)
    last_empty = _read(tmp_path, "f.txt", offset=4)

    assert result.is_error is True
    assert result.output_text == (
        "read error: Offset 5 is beyond end of file (4 lines total)"
    )
    # Line 4 is the empty element after the trailing newline, as in Pi.
    assert last_empty.is_error is False
    assert last_empty.output_text == ""


def test_read_tool_zero_and_negative_offset_and_limit_follow_pi(tmp_path: Path):
    (tmp_path / "f.txt").write_text(_numbered(10, trailing_newline=False))

    zero_offset = _read(tmp_path, "f.txt", offset=0)
    negative_offset = _read(tmp_path, "f.txt", offset=-5, limit=2)
    zero_limit = _read(tmp_path, "f.txt", limit=0)
    negative_limit = _read(tmp_path, "f.txt", limit=-3)

    assert zero_offset.output_text == _numbered(10, trailing_newline=False)
    assert negative_offset.output_text == (
        "line 1\nline 2\n\n[8 more lines in file. Use offset=3 to continue.]"
    )
    assert zero_limit.output_text == (
        "\n\n[10 more lines in file. Use offset=1 to continue.]"
    )
    # JS slice(0, -3) drops the last three lines; Pi's notice arithmetic then
    # reports 10 - (0 + -3) = 13 more lines and offset -2.
    assert negative_limit.output_text == (
        _numbered(7, trailing_newline=False)
        + "\n\n[13 more lines in file. Use offset=-2 to continue.]"
    )


def test_read_tool_rejects_non_integer_offset(tmp_path: Path):
    (tmp_path / "f.txt").write_text("x\n")

    with pytest.raises(ToolArgumentError) as info:
        _read(tmp_path, "f.txt", offset="2")

    assert info.value.field_path == ("offset",)


def test_read_tool_reads_large_files_whole(tmp_path: Path):
    (tmp_path / "big.txt").write_text(_numbered(40000), encoding="utf-8")
    assert (tmp_path / "big.txt").stat().st_size > 256 * 1024

    tail = _read(tmp_path, "big.txt", offset=39999)

    assert tail.is_error is False
    assert tail.output_text == "line 39999\nline 40000\n"


def test_read_tool_returns_secret_shaped_content(tmp_path: Path):
    body = "api_key=sk-live-0123456789abcdef0123456789abcdef\n"
    (tmp_path / "config.env").write_text(body, encoding="utf-8")

    result = _read(tmp_path, "config.env")

    assert result.is_error is False
    assert result.output_text == body


def test_read_tool_decodes_binary_and_invalid_utf8_with_replacement(tmp_path: Path):
    (tmp_path / "blob.bin").write_bytes(b"hello\x00wor\xffld\x07\n")

    result = _read(tmp_path, "blob.bin")

    assert result.is_error is False
    assert result.output_text == "hello\x00wor�ld\x07\n"


def _png(*chunks: bytes) -> bytes:
    ihdr = (13).to_bytes(4, "big") + b"IHDR" + b"\x00" * 13 + b"\x00" * 4
    return b"\x89PNG\r\n\x1a\n" + ihdr + b"".join(chunks)


def _chunk(kind: bytes, data: bytes = b"") -> bytes:
    crc = zlib.crc32(kind + data).to_bytes(4, "big")
    return len(data).to_bytes(4, "big") + kind + data + crc


def _bmp() -> bytes:
    header = bytearray(54)
    header[0:2] = b"BM"
    header[2:6] = (58).to_bytes(4, "little")
    header[10:14] = (54).to_bytes(4, "little")
    header[14:18] = (40).to_bytes(4, "little")
    header[26:28] = (1).to_bytes(2, "little")
    header[28:30] = (24).to_bytes(2, "little")
    return bytes(header) + b"\x00" * 4


# Small real images, each decoded by an image library before it was added.
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAgAAAAGCAIAAABxZ0isAAAACXBIWXMAAAABAAAAAQBPJcTW"
    "AAAAEklEQVR4nGP8w4AdsOAQp4sEAJm0ARInmA0VAAAAAElFTkSuQmCC"
)
_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAgAAAQABAAD//gAPTGF2YzYzLjEuMTAyAP/bAEMACAoKCwoLDQ0NDQ0N"
    "EA8QEBAQEBAQEBAQEBISEhUVFRISEhAQEhIUFBUVFxcXFRUVFRcXGRkZHh4cHCMjJCsrM//E"
    "AE0AAQEAAAAAAAAAAAAAAAAAAAAGAQEBAQAAAAAAAAAAAAAAAAAABgcQAQAAAAAAAAAAAAAA"
    "AAAAAAARAQAAAAAAAAAAAAAAAAAAAAD/wAARCAAGAAgDARIAAhIAAxIA/9oADAMBAAIRAxEA"
    "PwCKEoN+H//Z"
)
_GIF = base64.b64decode("R0lGODlhAQABAIAAAP///wAAACH5BAEAAAAALAAAAAABAAEAAAICRAEAOw==")
_WEBP = base64.b64decode(
    "UklGRjwAAABXRUJQVlA4IDAAAADQAQCdASoIAAYAAgA0JaACdLoB+AADsAD+8Oj3/yC5YXXI"
    "1/8gP+MqfGVP+PIAAAA="
)
_WEBP_LOSSLESS = base64.b64decode(
    "UklGRiAAAABXRUJQVlA4TBQAAAAvB0ABAAcQ5Y/+BwCC8L9tIqL/IQ=="
)
_WEBP_ANIMATED = base64.b64decode(
    "UklGRvIAAABXRUJQVlA4WAoAAAACAAAABwAABQAAQU5JTQYAAAAAbPz/AABBTk1GagAAAAAA"
    "AAAAAAcAAAUAAPQBAAJWUDhMUgAAAC8HQAEAb6CgbRSoPIKRhxTEIMRs2jZK2//pwRkEAgT/"
    "KSLNf0D2gBSsAR3MbZu/+LRBNW0rlkUkGWTw/s7gWvSfRwajNw4R/Q9IK4jrn7cH4gJBTk1G"
    "VAAAAAAAAAIAAAcAAAAAAPQBAABWUDhMOwAAAC8HAAAAP6CobRuoF//X4A1KgBBmGw15eAdz"
    "KHsQZNtskKc85OY/gAsiXDPsjvkEoQAac0gmg3YR/Q8PAA=="
)


def _png_of(width: int, height: int, idat: bytes | None = None, pad: int = 0) -> bytes:
    """A grayscale PNG; its rows are zeros unless ``idat`` replaces them,
    and ``pad`` adds an ancillary chunk of that many bytes."""

    ihdr = (
        width.to_bytes(4, "big") + height.to_bytes(4, "big") + b"\x08\x00\x00\x00\x00"
    )
    if idat is None:
        idat = zlib.compress(b"\x00" * (height * (width + 1)))
    extra = _chunk(b"teXt", b"\x00" * pad) if pad else b""
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + extra
        + _chunk(b"IDAT", idat)
        + _chunk(b"IEND")
    )


def _interlaced_png(width: int, height: int) -> bytes:
    """An Adam7-interlaced 8-bit RGB PNG of zero pixels."""

    rows = b""
    for x0, y0, dx, dy in (
        (0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4),
        (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2),
    ):  # fmt: skip
        columns = len(range(x0, width, dx))
        if columns:
            rows += (b"\x00" + b"\x00" * 3 * columns) * len(range(y0, height, dy))
    ihdr = (
        width.to_bytes(4, "big") + height.to_bytes(4, "big") + b"\x08\x02\x00\x00\x01"
    )
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(rows))
        + _chunk(b"IEND")
    )


def _with_size(data: bytes, mime: str, width: int, height: int) -> bytes:
    """A real image whose header declares another size."""

    if mime == "image/jpeg":
        sof = data.index(b"\xff\xc0")
        size = height.to_bytes(2, "big") + width.to_bytes(2, "big")
        return data[: sof + 5] + size + data[sof + 9 :]
    # The animated WebP's canvas.
    size = (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little")
    return data[:24] + size + data[30:]


@pytest.mark.parametrize(
    ("data", "mime"),
    [
        (_PNG, "image/png"),
        (_png_of(2000, 1), "image/png"),
        (_interlaced_png(5, 3), "image/png"),
        (_JPEG, "image/jpeg"),
        (_GIF, "image/gif"),
        (_WEBP, "image/webp"),
        (_WEBP_LOSSLESS, "image/webp"),
        (_WEBP_ANIMATED, "image/webp"),
    ],
)
def test_read_tool_returns_an_image_as_an_attachment(
    tmp_path: Path, data: bytes, mime: str
):
    # Goal-check finding M16, Pi read.ts: "Read image file [<type>]" and the
    # image; the structured result is the image block codemode's image()
    # shows. An image within Pi's limits is sent as it is.
    (tmp_path / "pic").write_bytes(data)

    result = _read(tmp_path, "pic")

    encoded = base64.b64encode(data).decode("ascii")
    assert result.is_error is False
    assert result.output_text == f"Read image file [{mime}]"
    assert result.blocks == (
        TextContent(f"Read image file [{mime}]"),
        ImageContent(encoded, mime),
    )
    assert result.structured_content == {
        "type": "image",
        "data": encoded,
        "mimeType": mime,
        "note": f"Read image file [{mime}]",
    }


def _replace_chunk(png: bytes, kind: bytes, body: bytes) -> bytes:
    start = png.index(kind) - 4
    end = start + 12 + int.from_bytes(png[start : start + 4], "big")
    return png[:start] + _chunk(kind, body) + png[end:]


@pytest.mark.parametrize(
    ("data", "mime"),
    [
        # Over 2000 pixels: Pi resizes it, pipy has no codec.
        (_png_of(2001, 1), "image/png"),
        (_with_size(_JPEG, "image/jpeg", 10, 2001), "image/jpeg"),
        (_with_size(_WEBP_ANIMATED, "image/webp", 4000, 3000), "image/webp"),
        # 4.5 MB of base64 or more.
        (_png_of(10, 10, pad=9 * 1024 * 1024 * 3 // 8), "image/png"),
        # A file that is not a complete image cannot be decoded: cut short,
        # a broken checksum, no or broken pixel data, no frame.
        (_PNG[:33], "image/png"),
        (_PNG[:-1], "image/png"),
        (_PNG.replace(b"IDAT", b"IDAt"), "image/png"),
        (_replace_chunk(_PNG, b"IDAT", b""), "image/png"),
        (_replace_chunk(_PNG, b"IDAT", b"not deflate data"), "image/png"),
        (_png_of(10, 10, idat=zlib.compress(b"\x00" * 50)), "image/png"),
        (_png_of(1, 2, idat=zlib.compress(b"\x00\x00\x07\x00")), "image/png"),
        # All the rows, but the zlib stream is cut before its end.
        (_png_of(1, 1, idat=zlib.compress(b"\x00\x00")[:-5]), "image/png"),
        (_png_of(0, 10), "image/png"),
        # The rows of a 5 x 3 RGB image without interlacing: 48 bytes, where
        # its seven passes take 52.
        (
            _replace_chunk(_interlaced_png(5, 3), b"IDAT", zlib.compress(bytes(48))),
            "image/png",
        ),
        (_JPEG[:-2], "image/jpeg"),
        (_JPEG[:30], "image/jpeg"),
        (b"\xff\xd8\xff\xe0" + b"\x00" * 16, "image/jpeg"),
        (_GIF[:-1], "image/gif"),
        (_GIF[:-6], "image/gif"),
        (_GIF.replace(b"\x02\x02\x44\x01\x00", b"\x02\x00"), "image/gif"),
        (_WEBP[:-1], "image/webp"),
        (_WEBP_ANIMATED[:-1], "image/webp"),
        # The canvas and the animation chunk, then a frame without image.
        (
            b"RIFF"
            + (36 + 24).to_bytes(4, "little")
            + _WEBP_ANIMATED[8:44]
            + b"ANMF\x10\x00\x00\x00"
            + b"\x00" * 16,
            "image/webp",
        ),
    ],
)
def test_read_tool_omits_an_image_it_would_have_to_resize(
    tmp_path: Path, data: bytes, mime: str
):
    (tmp_path / "pic").write_bytes(data)

    result = _read(tmp_path, "pic")

    note = (
        f"Read image file [{mime}]\n"
        "[Image omitted: could not be resized below the inline image size limit.]"
    )
    assert result.is_error is False
    assert result.output_text == note
    assert result.blocks == ()
    assert result.structured_content == note


def test_read_tool_omits_a_bmp_it_would_have_to_convert(tmp_path: Path):
    (tmp_path / "pic_bmp").write_bytes(_bmp())

    result = _read(tmp_path, "pic_bmp")

    note = (
        "Read image file [image/bmp]\n[Image omitted: could not be converted to "
        "a supported inline image format.]"
    )
    assert (result.is_error, result.output_text) == (False, note)
    assert result.structured_content == note


def test_a_text_only_reader_refuses_an_image(tmp_path: Path):
    # @file references keep only text, so an image is not read there.
    (tmp_path / "pic").write_bytes(_PNG)
    (tmp_path / "pic_bmp").write_bytes(_bmp())
    tool = ReadTool(attach_images=False)

    png = tool.invoke(
        _make_request({"path": "pic"}), ToolContext(workspace_root=tmp_path)
    )
    bmp = tool.invoke(
        _make_request({"path": "pic_bmp"}), ToolContext(workspace_root=tmp_path)
    )

    assert (png.is_error, png.output_text) == (
        True,
        "read error: image files cannot be read as text (image/png). "
        "Attach it with @image:<path>.",
    )
    assert bmp.output_text == (
        "read error: image files cannot be read as text (image/bmp)."
    )


def test_read_tool_structured_result_of_a_text_file_is_its_text(tmp_path: Path):
    (tmp_path / "a.txt").write_text("one\ntwo\n", encoding="utf-8")

    result = _read(tmp_path, "a.txt", limit=1)

    assert result.structured_content == result.output_text
    assert result.output_text.startswith("one\n\n[2 more lines")
    assert _read(tmp_path, "missing").structured_content is NO_STRUCTURED_CONTENT


def test_read_tool_reads_non_images_with_image_like_prefixes_as_text(tmp_path: Path):
    # Pi does not treat animated PNG or `ff d8 ff f7` as supported images.
    (tmp_path / "anim").write_bytes(_png(_chunk(b"acTL", b"\x00" * 8)))
    (tmp_path / "jpegls").write_bytes(b"\xff\xd8\xff\xf7rest")

    anim = _read(tmp_path, "anim")
    jpeg_ls = _read(tmp_path, "jpegls")

    assert anim.is_error is False
    assert anim.output_text.startswith("�PNG")
    assert jpeg_ls.is_error is False
    assert jpeg_ls.output_text.endswith("rest")
