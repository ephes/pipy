"""Tests for the model-driven `read` tool (Pi `read`, READ1).

These tests pin the read tool's definition, schema, workspace path safety,
Pi's truncation notices and offset/limit paging. They reuse the existing
`pipy_harness.native.read_only_tool` validation; the test set covers the
contract of the new tool, not the legacy `/read` slash command (already
covered by `tests/test_native_explicit_file_excerpt_tool.py`).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pipy_harness.native.tools import (
    ToolArgumentError,
    ToolContext,
    ToolExecutionResult,
    ToolPort,
    ToolRequest,
    make_tool_request_id,
)
from pipy_harness.native.tools.read import ReadTool


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
        "Read the contents of a file. Supports text files; images (jpg, png, "
        "gif, webp, bmp) are not supported yet and return an error. For text "
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
    return len(data).to_bytes(4, "big") + kind + data + b"\x00" * 4


def _bmp() -> bytes:
    header = bytearray(54)
    header[0:2] = b"BM"
    header[2:6] = (58).to_bytes(4, "little")
    header[10:14] = (54).to_bytes(4, "little")
    header[14:18] = (40).to_bytes(4, "little")
    header[26:28] = (1).to_bytes(2, "little")
    header[28:30] = (24).to_bytes(2, "little")
    return bytes(header) + b"\x00" * 4


@pytest.mark.parametrize(
    ("data", "mime"),
    [
        (_png(_chunk(b"IDAT")), "image/png"),
        (b"\xff\xd8\xff\xe0" + b"\x00" * 16, "image/jpeg"),
        (b"GIF89a" + b"\x00" * 16, "image/gif"),
        (b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 8, "image/webp"),
    ],
)
def test_read_tool_refuses_attachable_images_with_image_hint(
    tmp_path: Path, data: bytes, mime: str
):
    (tmp_path / "pic").write_bytes(data)

    result = _read(tmp_path, "pic")

    assert result.is_error is True
    assert result.output_text == (
        f"read error: image files are not supported by read yet ({mime}). "
        "Ask the user to attach it with @image:<path>."
    )


def test_read_tool_refuses_bmp_without_attach_hint(tmp_path: Path):
    (tmp_path / "pic_bmp").write_bytes(_bmp())

    result = _read(tmp_path, "pic_bmp")

    assert result.is_error is True
    assert result.output_text == (
        "read error: image files are not supported by read yet (image/bmp)."
    )


def test_read_tool_reads_non_images_with_image_like_prefixes_as_text(tmp_path: Path):
    # Pi does not treat animated PNG or `ff d8 ff f7` as supported images.
    # The files have no suffix because pipy's shared path policy still refuses
    # image suffixes such as `.png` (READ2).
    (tmp_path / "anim").write_bytes(_png(_chunk(b"acTL", b"\x00" * 8)))
    (tmp_path / "jpegls").write_bytes(b"\xff\xd8\xff\xf7rest")

    anim = _read(tmp_path, "anim")
    jpeg_ls = _read(tmp_path, "jpegls")

    assert anim.is_error is False
    assert anim.output_text.startswith("�PNG")
    assert jpeg_ls.is_error is False
    assert jpeg_ls.output_text.endswith("rest")
