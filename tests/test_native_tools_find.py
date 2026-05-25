"""Slice 8 tests: the `find` tool."""

from __future__ import annotations

from pathlib import Path

import pytest

from pipy_harness.native.tools import (
    FindTool,
    ToolArgumentError,
    ToolContext,
    ToolPort,
    ToolRequest,
    make_tool_request_id,
)
from pipy_harness.native.tools.find import TRUNCATION_MARKER


def _make_request(arguments: dict[str, object]) -> ToolRequest:
    return ToolRequest(
        tool_request_id=make_tool_request_id(),
        tool_name="find",
        arguments=arguments,
    )


def test_find_tool_satisfies_tool_port_protocol():
    tool = FindTool()

    assert isinstance(tool, ToolPort)


def test_find_tool_definition_requires_pattern_only():
    tool = FindTool()

    schema = tool.definition.input_schema

    assert schema["type"] == "object"
    assert schema["required"] == ["pattern"]
    assert "path" in schema["properties"]
    assert schema["additionalProperties"] is False


def test_find_tool_matches_simple_glob(tmp_path: Path):
    (tmp_path / "a.py").write_text("", encoding="utf-8")
    (tmp_path / "b.py").write_text("", encoding="utf-8")
    (tmp_path / "c.txt").write_text("", encoding="utf-8")
    tool = FindTool()
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request({"pattern": "*.py"})

    result = tool.invoke(request, context)

    assert result.is_error is False
    assert "a.py" in result.output_text
    assert "b.py" in result.output_text
    assert "c.txt" not in result.output_text


def test_find_tool_matches_recursive_glob(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "nested.py").write_text("", encoding="utf-8")
    tool = FindTool()
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request({"pattern": "**/*.py"})

    result = tool.invoke(request, context)

    assert result.is_error is False
    assert "src/nested.py" in result.output_text


def test_find_tool_skips_dot_git(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "hidden.py").write_text("", encoding="utf-8")
    (tmp_path / "visible.py").write_text("", encoding="utf-8")
    tool = FindTool()
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request({"pattern": "**/*.py"})

    result = tool.invoke(request, context)

    assert result.is_error is False
    assert "visible.py" in result.output_text
    assert ".git" not in result.output_text


def test_find_tool_rejects_absolute_pattern():
    tool = FindTool()
    context = ToolContext(workspace_root=Path("/tmp").resolve())
    request = _make_request({"pattern": "/etc/*"})

    with pytest.raises(ToolArgumentError):
        tool.invoke(request, context)


def test_find_tool_rejects_parent_traversal_in_pattern(tmp_path: Path):
    tool = FindTool()
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request({"pattern": "../*.py"})

    with pytest.raises(ToolArgumentError):
        tool.invoke(request, context)


def test_find_tool_rejects_unsafe_search_root(tmp_path: Path):
    tool = FindTool()
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request({"pattern": "*.py", "path": "/etc"})

    with pytest.raises(ToolArgumentError):
        tool.invoke(request, context)


def test_find_tool_caps_results_with_truncation_marker(tmp_path: Path):
    for i in range(10):
        (tmp_path / f"f{i}.txt").write_text("", encoding="utf-8")
    tool = FindTool(max_results=3)
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request({"pattern": "*.txt"})

    result = tool.invoke(request, context)

    assert result.is_error is False
    assert TRUNCATION_MARKER in result.output_text
    assert result.output_text.count("\n") == 3


def test_find_tool_no_matches_reports_safely(tmp_path: Path):
    tool = FindTool()
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request({"pattern": "*.nonexistent"})

    result = tool.invoke(request, context)

    assert result.is_error is False
    assert result.output_text == "(no matches)"


def test_find_tool_search_root_must_be_directory(tmp_path: Path):
    (tmp_path / "file.txt").write_text("", encoding="utf-8")
    tool = FindTool()
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request({"pattern": "*", "path": "file.txt"})

    result = tool.invoke(request, context)

    assert result.is_error is True
    assert "not a directory" in result.output_text


def test_find_tool_rejects_invalid_max_results():
    with pytest.raises(ValueError, match="max_results"):
        FindTool(max_results=0)
    with pytest.raises(ValueError, match="max_results"):
        FindTool(max_results=FindTool.HARD_MAX_RESULTS + 1)


def test_production_tool_registry_holds_read_ls_grep_and_find():
    from pipy_harness.native import production_tool_registry

    registry = production_tool_registry()

    assert set(registry.keys()) == {"read", "ls", "grep", "find"}
