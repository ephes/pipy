"""Slice 6 tests: the `ls` tool."""

from __future__ import annotations

from pathlib import Path

import pytest

from pipy_harness.native.tools import (
    LsTool,
    ToolArgumentError,
    ToolContext,
    ToolPort,
    ToolRequest,
    make_tool_request_id,
)
from pipy_harness.native.tools.ls import TRUNCATION_MARKER


def _make_request(arguments: dict[str, object]) -> ToolRequest:
    return ToolRequest(
        tool_request_id=make_tool_request_id(),
        tool_name="ls",
        arguments=arguments,
    )


def test_ls_tool_satisfies_tool_port_protocol():
    tool = LsTool()

    assert isinstance(tool, ToolPort)


def test_ls_tool_definition_is_object_schema_with_required_path():
    tool = LsTool()

    schema = tool.definition.input_schema

    assert schema["type"] == "object"
    assert schema["properties"]["path"]["type"] == "string"
    assert schema["required"] == ["path"]
    assert schema["additionalProperties"] is False


def test_ls_tool_lists_workspace_root_with_dot(tmp_path: Path):
    (tmp_path / "a.py").write_text("x", encoding="utf-8")
    (tmp_path / "subdir").mkdir()
    tool = LsTool()
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request({"path": "."})

    result = tool.invoke(request, context)

    assert result.is_error is False
    assert "file a.py" in result.output_text
    assert "directory subdir" in result.output_text


def test_ls_tool_lists_subdirectory(tmp_path: Path):
    (tmp_path / "subdir").mkdir()
    (tmp_path / "subdir" / "nested.txt").write_text("y", encoding="utf-8")
    tool = LsTool()
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request({"path": "subdir"})

    result = tool.invoke(request, context)

    assert result.is_error is False
    assert "file subdir/nested.txt" in result.output_text


def test_ls_tool_refuses_dot_git(tmp_path: Path):
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "config").write_text("x", encoding="utf-8")
    tool = LsTool()
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request({"path": ".git"})

    result = tool.invoke(request, context)

    assert result.is_error is True
    assert "ignored or under .git" in result.output_text


def test_ls_tool_skips_ignored_children_when_listing_root(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("x", encoding="utf-8")
    (tmp_path / "visible.txt").write_text("y", encoding="utf-8")
    tool = LsTool()
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request({"path": "."})

    result = tool.invoke(request, context)

    assert result.is_error is False
    assert "visible.txt" in result.output_text
    assert ".git" not in result.output_text


def test_ls_tool_refuses_absolute_path():
    tool = LsTool()
    context = ToolContext(workspace_root=Path("/tmp").resolve())
    request = _make_request({"path": "/etc"})

    with pytest.raises(ToolArgumentError):
        tool.invoke(request, context)


def test_ls_tool_refuses_parent_traversal(tmp_path: Path):
    tool = LsTool()
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request({"path": "../etc"})

    with pytest.raises(ToolArgumentError):
        tool.invoke(request, context)


def test_ls_tool_missing_directory_is_error_observation(tmp_path: Path):
    tool = LsTool()
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request({"path": "missing"})

    result = tool.invoke(request, context)

    assert result.is_error is True
    assert "does not exist" in result.output_text


def test_ls_tool_truncates_with_deterministic_marker(tmp_path: Path):
    for i in range(10):
        (tmp_path / f"f{i}.txt").write_text("x", encoding="utf-8")
    tool = LsTool(max_entries=3)
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request({"path": "."})

    result = tool.invoke(request, context)

    assert result.is_error is False
    assert TRUNCATION_MARKER in result.output_text
    assert result.output_text.count("file f") == 3


def test_ls_tool_empty_directory_reports_safely(tmp_path: Path):
    (tmp_path / "empty").mkdir()
    tool = LsTool()
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request({"path": "empty"})

    result = tool.invoke(request, context)

    assert result.is_error is False
    assert result.output_text == "(empty directory)"


def test_ls_tool_rejects_invalid_max_entries():
    with pytest.raises(ValueError, match="max_entries"):
        LsTool(max_entries=0)
    with pytest.raises(ValueError, match="max_entries"):
        LsTool(max_entries=LsTool.HARD_MAX_ENTRIES + 1)


def test_production_tool_registry_holds_read_and_ls():
    from pipy_harness.native import production_tool_registry

    registry = production_tool_registry()

    assert set(registry.keys()) == {"read", "ls"}
