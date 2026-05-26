"""Slice 10 tests: the `edit` tool."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from pipy_harness.native.tools import (
    EditTool,
    ToolArgumentError,
    ToolContext,
    ToolPort,
    ToolRequest,
    make_tool_request_id,
)


def _make_request(arguments: dict[str, object]) -> ToolRequest:
    return ToolRequest(
        tool_request_id=make_tool_request_id(),
        tool_name="edit",
        arguments=arguments,
    )


def _stderr_capture(buffer: io.StringIO):
    def _sink(text: str) -> None:
        buffer.write(text)

    return _sink


def test_edit_tool_satisfies_tool_port_protocol():
    tool = EditTool()

    assert isinstance(tool, ToolPort)


def test_edit_tool_definition_requires_path_old_new():
    tool = EditTool()

    schema = tool.definition.input_schema

    assert schema["type"] == "object"
    assert set(schema["required"]) == {"path", "old_string", "new_string"}
    assert schema["additionalProperties"] is False
    assert schema["properties"]["replace_all"]["type"] == "boolean"


def test_edit_tool_replaces_unique_match_and_streams_diff(tmp_path: Path):
    target = tmp_path / "config.py"
    target.write_text("DEBUG = False\n", encoding="utf-8")
    buffer = io.StringIO()
    tool = EditTool()
    context = ToolContext(
        workspace_root=tmp_path,
        stderr_sink=_stderr_capture(buffer),
    )
    request = _make_request(
        {
            "path": "config.py",
            "old_string": "DEBUG = False",
            "new_string": "DEBUG = True",
        }
    )

    result = tool.invoke(request, context)

    assert result.is_error is False
    assert target.read_text(encoding="utf-8") == "DEBUG = True\n"
    diff = buffer.getvalue()
    assert "-DEBUG = False" in diff
    assert "+DEBUG = True" in diff


def test_edit_tool_rejects_duplicate_when_replace_all_false(tmp_path: Path):
    target = tmp_path / "dup.py"
    target.write_text("X = 1\nX = 1\n", encoding="utf-8")
    tool = EditTool()
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request(
        {"path": "dup.py", "old_string": "X = 1", "new_string": "X = 2"}
    )

    result = tool.invoke(request, context)

    assert result.is_error is True
    assert "not unique" in result.output_text
    assert target.read_text(encoding="utf-8") == "X = 1\nX = 1\n"


def test_edit_tool_replace_all_replaces_every_occurrence(tmp_path: Path):
    target = tmp_path / "dup.py"
    target.write_text("X = 1\nX = 1\n", encoding="utf-8")
    tool = EditTool()
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request(
        {
            "path": "dup.py",
            "old_string": "X = 1",
            "new_string": "X = 2",
            "replace_all": True,
        }
    )

    result = tool.invoke(request, context)

    assert result.is_error is False
    assert target.read_text(encoding="utf-8") == "X = 2\nX = 2\n"


def test_edit_tool_rejects_empty_old_string(tmp_path: Path):
    (tmp_path / "f.py").write_text("x", encoding="utf-8")
    tool = EditTool()
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request(
        {"path": "f.py", "old_string": "", "new_string": "y"}
    )

    with pytest.raises(ToolArgumentError) as info:
        tool.invoke(request, context)

    assert info.value.field_path == ("old_string",)


def test_edit_tool_reports_missing_file(tmp_path: Path):
    tool = EditTool()
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request(
        {"path": "missing.py", "old_string": "x", "new_string": "y"}
    )

    result = tool.invoke(request, context)

    assert result.is_error is True
    assert "does not exist" in result.output_text


def test_edit_tool_refuses_dot_git(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("x", encoding="utf-8")
    tool = EditTool()
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request(
        {"path": ".git/config", "old_string": "x", "new_string": "y"}
    )

    result = tool.invoke(request, context)

    assert result.is_error is True
    assert "ignored or under .git" in result.output_text


def test_edit_tool_refuses_absolute_path(tmp_path: Path):
    tool = EditTool()
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request(
        {"path": "/etc/passwd", "old_string": "x", "new_string": "y"}
    )

    with pytest.raises(ToolArgumentError):
        tool.invoke(request, context)


def test_edit_tool_reports_no_match(tmp_path: Path):
    target = tmp_path / "f.py"
    target.write_text("hello\n", encoding="utf-8")
    tool = EditTool()
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request(
        {"path": "f.py", "old_string": "missing", "new_string": "x"}
    )

    result = tool.invoke(request, context)

    assert result.is_error is True
    assert "not found" in result.output_text


def test_edit_tool_does_not_invoke_archive_recorder(tmp_path: Path, monkeypatch):
    target = tmp_path / "f.py"
    target.write_text("x", encoding="utf-8")
    import pipy_session.recorder as recorder

    sentinel: dict[str, int] = {"calls": 0}
    original_append = recorder.append_event

    def _trap(*args, **kwargs):
        sentinel["calls"] += 1
        return original_append(*args, **kwargs)

    monkeypatch.setattr(recorder, "append_event", _trap)

    tool = EditTool()
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request(
        {"path": "f.py", "old_string": "x", "new_string": "y"}
    )

    tool.invoke(request, context)

    assert sentinel["calls"] == 0


def test_edit_tool_source_does_not_import_recorder():
    source = (
        Path(__file__).parents[1]
        / "src/pipy_harness/native/tools/edit.py"
    ).read_text(encoding="utf-8")

    assert "import pipy_session" not in source
    assert "from pipy_session" not in source


def test_production_tool_registry_holds_edit():
    from pipy_harness.native import production_tool_registry

    registry = production_tool_registry()
    assert "edit" in registry
    expected = {"read", "ls", "grep", "find", "write", "edit", "bash"}
    assert expected.issubset(set(registry.keys()))
