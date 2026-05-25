"""Slice 5 tests: the model-driven `read` tool.

These tests pin the read tool's definition, schema, workspace path safety,
content-policy behavior, and bounded output. They reuse the existing
`pipy_harness.native.read_only_tool` validation; the test set covers the
contract of the new tool, not the legacy `/read` slash command (already
covered by `tests/test_native_explicit_file_excerpt_tool.py`).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pipy_harness.native.tools import (
    ReadTool,
    ToolArgumentError,
    ToolContext,
    ToolPort,
    ToolRequest,
    make_tool_request_id,
)


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
    schema = definition.input_schema
    assert schema["type"] == "object"
    assert schema["properties"]["path"]["type"] == "string"
    assert schema["required"] == ["path"]
    assert schema["additionalProperties"] is False


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


def test_read_tool_refuses_path_under_dot_git(tmp_path: Path):
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "config").write_text("[core]\n", encoding="utf-8")
    tool = ReadTool()
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request({"path": ".git/config"})

    result = tool.invoke(request, context)

    assert result.is_error is True
    assert "ignored or under .git" in result.output_text


def test_read_tool_refuses_absolute_path_via_argument_error(tmp_path: Path):
    tool = ReadTool()
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request({"path": "/etc/passwd"})

    with pytest.raises(ToolArgumentError) as info:
        tool.invoke(request, context)

    assert info.value.field_path == ("path",)


def test_read_tool_refuses_parent_traversal_via_argument_error(tmp_path: Path):
    tool = ReadTool()
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request({"path": "../secret.txt"})

    with pytest.raises(ToolArgumentError):
        tool.invoke(request, context)


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


def test_read_tool_reports_binary_content(tmp_path: Path):
    target = tmp_path / "binary.bin"
    target.write_bytes(b"hello\x00world")
    tool = ReadTool()
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request({"path": "binary.bin"})

    result = tool.invoke(request, context)

    assert result.is_error is True
    assert "binary" in result.output_text


def test_read_tool_truncates_to_byte_limit(tmp_path: Path):
    target = tmp_path / "long.txt"
    target.write_text("a" * 4096, encoding="utf-8")
    tool = ReadTool(byte_limit=64, line_limit=100)
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request({"path": "long.txt"})

    result = tool.invoke(request, context)

    assert result.is_error is False
    assert len(result.output_text.encode("utf-8")) <= 64


def test_read_tool_truncates_to_line_limit(tmp_path: Path):
    target = tmp_path / "multi.txt"
    target.write_text("\n".join(f"line {i}" for i in range(50)) + "\n", encoding="utf-8")
    tool = ReadTool(byte_limit=4096, line_limit=5)
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request({"path": "multi.txt"})

    result = tool.invoke(request, context)

    assert result.is_error is False
    assert result.output_text.count("\n") <= 5


def test_read_tool_rejects_invalid_limits():
    with pytest.raises(ValueError, match="byte_limit"):
        ReadTool(byte_limit=0)
    with pytest.raises(ValueError, match="byte_limit"):
        ReadTool(byte_limit=ReadTool.MAX_BYTE_LIMIT + 1)
    with pytest.raises(ValueError, match="line_limit"):
        ReadTool(line_limit=0)
    with pytest.raises(ValueError, match="line_limit"):
        ReadTool(line_limit=ReadTool.MAX_LINE_LIMIT + 1)
