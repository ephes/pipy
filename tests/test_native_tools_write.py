"""Slice 9 tests: the `write` tool.

These tests pin the create-only mutation, the stderr diff stream, and
the archive-untouched invariant. The pipy_session.recorder boundary is
not invoked from inside the tool; a sanity check guards against future
regressions.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from pipy_harness.native.tools import (
    ToolArgumentError,
    ToolContext,
    ToolPort,
    ToolRequest,
    WriteTool,
    make_tool_request_id,
)


def _make_request(arguments: dict[str, object]) -> ToolRequest:
    return ToolRequest(
        tool_request_id=make_tool_request_id(),
        tool_name="write",
        arguments=arguments,
    )


def _stderr_capture(buffer: io.StringIO):
    def _sink(text: str) -> None:
        buffer.write(text)

    return _sink


def test_write_tool_satisfies_tool_port_protocol():
    tool = WriteTool()

    assert isinstance(tool, ToolPort)


def test_write_tool_definition_requires_path_and_content():
    tool = WriteTool()

    schema = tool.definition.input_schema

    assert schema["type"] == "object"
    assert set(schema["required"]) == {"path", "content"}
    assert schema["additionalProperties"] is False


def test_write_tool_creates_new_file_and_streams_diff_to_stderr(tmp_path: Path):
    buffer = io.StringIO()
    tool = WriteTool()
    context = ToolContext(
        workspace_root=tmp_path,
        stderr_sink=_stderr_capture(buffer),
    )
    request = _make_request({"path": "new.txt", "content": "hello\n"})

    result = tool.invoke(request, context)

    assert result.is_error is False
    assert (tmp_path / "new.txt").read_text(encoding="utf-8") == "hello\n"
    diff = buffer.getvalue()
    assert diff.startswith("--- a/new.txt")
    assert "+++ b/new.txt" in diff
    assert "+hello" in diff


def test_write_tool_refuses_existing_file(tmp_path: Path):
    (tmp_path / "exists.txt").write_text("already", encoding="utf-8")
    buffer = io.StringIO()
    tool = WriteTool()
    context = ToolContext(
        workspace_root=tmp_path,
        stderr_sink=_stderr_capture(buffer),
    )
    request = _make_request({"path": "exists.txt", "content": "new"})

    result = tool.invoke(request, context)

    assert result.is_error is True
    assert "already exists" in result.output_text
    assert buffer.getvalue() == ""


def test_write_tool_refuses_dot_git(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    tool = WriteTool()
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request({"path": ".git/HEAD", "content": "ref"})

    result = tool.invoke(request, context)

    assert result.is_error is True
    assert "ignored or under .git" in result.output_text


def test_write_tool_refuses_absolute_path(tmp_path: Path):
    tool = WriteTool()
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request({"path": "/etc/passwd", "content": ""})

    with pytest.raises(ToolArgumentError):
        tool.invoke(request, context)


def test_write_tool_refuses_parent_traversal(tmp_path: Path):
    tool = WriteTool()
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request({"path": "../escape.txt", "content": ""})

    with pytest.raises(ToolArgumentError):
        tool.invoke(request, context)


def test_write_tool_refuses_missing_parent_directory(tmp_path: Path):
    tool = WriteTool()
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request({"path": "missing/dir/file.txt", "content": "x"})

    result = tool.invoke(request, context)

    assert result.is_error is True
    assert "parent directory does not exist" in result.output_text


def test_write_tool_handles_empty_content(tmp_path: Path):
    buffer = io.StringIO()
    tool = WriteTool()
    context = ToolContext(
        workspace_root=tmp_path,
        stderr_sink=_stderr_capture(buffer),
    )
    request = _make_request({"path": "empty.txt", "content": ""})

    result = tool.invoke(request, context)

    assert result.is_error is False
    assert (tmp_path / "empty.txt").read_text(encoding="utf-8") == ""


def test_write_tool_does_not_invoke_archive_recorder(tmp_path: Path, monkeypatch):
    """The write tool must never call into pipy_session.recorder.

    Mutation must be archive-safe: diffs flow only to stderr_sink and
    never reach the metadata archive.
    """

    import pipy_session.recorder as recorder

    sentinel: dict[str, int] = {"calls": 0}

    original_append = recorder.append_event

    def _trap(*args, **kwargs):
        sentinel["calls"] += 1
        return original_append(*args, **kwargs)

    monkeypatch.setattr(recorder, "append_event", _trap)

    tool = WriteTool()
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request({"path": "trap.txt", "content": "x"})

    tool.invoke(request, context)

    assert sentinel["calls"] == 0


def test_write_tool_source_does_not_import_recorder():
    source = (
        Path(__file__).parents[1]
        / "src/pipy_harness/native/tools/write.py"
    ).read_text(encoding="utf-8")

    assert "import pipy_session" not in source
    assert "from pipy_session" not in source


def test_write_tool_rejects_invalid_max_content_bytes():
    with pytest.raises(ValueError, match="max_content_bytes"):
        WriteTool(max_content_bytes=0)
    with pytest.raises(ValueError, match="max_content_bytes"):
        WriteTool(max_content_bytes=WriteTool.HARD_MAX_CONTENT_BYTES + 1)


def test_production_tool_registry_holds_write():
    from pipy_harness.native import production_tool_registry

    assert "write" in production_tool_registry()
