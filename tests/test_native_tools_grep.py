"""Slice 7 tests: the `grep` tool with `rg` and stdlib fallback."""

from __future__ import annotations

from pathlib import Path

import pytest

from pipy_harness.native.tools import (
    GrepTool,
    ToolArgumentError,
    ToolContext,
    ToolPort,
    ToolRequest,
    make_tool_request_id,
)
from pipy_harness.native.tools.grep import TRUNCATION_MARKER


def _make_request(arguments: dict[str, object]) -> ToolRequest:
    return ToolRequest(
        tool_request_id=make_tool_request_id(),
        tool_name="grep",
        arguments=arguments,
    )


def test_grep_tool_satisfies_tool_port_protocol():
    tool = GrepTool()

    assert isinstance(tool, ToolPort)


def test_grep_tool_definition_requires_pattern_only():
    tool = GrepTool()

    schema = tool.definition.input_schema

    assert schema["type"] == "object"
    assert schema["required"] == ["pattern"]
    assert "path" in schema["properties"]
    assert schema["additionalProperties"] is False


def test_grep_tool_matches_literal_strings_across_files(tmp_path: Path):
    (tmp_path / "a.txt").write_text(
        "alpha\nNEEDLE_HERE\nomega\n", encoding="utf-8"
    )
    (tmp_path / "b.txt").write_text(
        "beta\nNEEDLE_HERE\n", encoding="utf-8"
    )
    tool = GrepTool()
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request({"pattern": "NEEDLE_HERE"})

    result = tool.invoke(request, context)

    assert result.is_error is False
    assert "a.txt:2:NEEDLE_HERE" in result.output_text
    assert "b.txt:2:NEEDLE_HERE" in result.output_text


def test_grep_tool_refuses_path_under_dot_git(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text(
        "NEEDLE_HERE\n", encoding="utf-8"
    )
    tool = GrepTool()
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request({"pattern": "NEEDLE_HERE", "path": ".git"})

    result = tool.invoke(request, context)

    assert result.is_error is True
    assert "ignored or under .git" in result.output_text


def test_grep_tool_refuses_absolute_path_via_argument_error(tmp_path: Path):
    tool = GrepTool()
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request({"pattern": "x", "path": "/etc"})

    with pytest.raises(ToolArgumentError):
        tool.invoke(request, context)


def test_grep_tool_refuses_parent_traversal(tmp_path: Path):
    tool = GrepTool()
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request({"pattern": "x", "path": "../etc"})

    with pytest.raises(ToolArgumentError):
        tool.invoke(request, context)


def test_grep_tool_caps_results_with_truncation_marker(tmp_path: Path):
    target = tmp_path / "many.txt"
    target.write_text(
        "\n".join("NEEDLE_HERE" for _ in range(50)) + "\n", encoding="utf-8"
    )
    tool = GrepTool(max_results=5)
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request({"pattern": "NEEDLE_HERE"})

    result = tool.invoke(request, context)

    assert result.is_error is False
    assert TRUNCATION_MARKER in result.output_text
    assert result.output_text.count("many.txt:") <= 5


def test_grep_tool_uses_stdlib_fallback_when_rg_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    (tmp_path / "a.txt").write_text("alpha\nFALLBACK_HIT\n", encoding="utf-8")
    monkeypatch.setattr(
        "pipy_harness.native.tools.grep.shutil.which", lambda _name: None
    )
    tool = GrepTool()
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request({"pattern": "FALLBACK_HIT"})

    result = tool.invoke(request, context)

    assert result.is_error is False
    assert "a.txt:2:FALLBACK_HIT" in result.output_text


def test_grep_stdlib_fallback_skips_files_above_scan_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    (tmp_path / "small.txt").write_text("SMALL_HIT\n", encoding="utf-8")
    (tmp_path / "huge.txt").write_bytes(b"HUGE_HIT\n" + b"x" * 128)
    monkeypatch.setattr(
        "pipy_harness.native.tools.grep.shutil.which", lambda _name: None
    )
    tool = GrepTool(max_scan_file_bytes=64)
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request({"pattern": "HIT"})

    result = tool.invoke(request, context)

    assert result.is_error is False
    assert "small.txt:1:SMALL_HIT" in result.output_text
    assert "HUGE_HIT" not in result.output_text


def test_grep_tool_no_matches_reports_safely(tmp_path: Path):
    (tmp_path / "a.txt").write_text("alpha\nbeta\n", encoding="utf-8")
    tool = GrepTool()
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request({"pattern": "NEVER_PRESENT_NEEDLE"})

    result = tool.invoke(request, context)

    assert result.is_error is False
    assert result.output_text == "(no matches)"


def test_grep_tool_rejects_invalid_max_results():
    with pytest.raises(ValueError, match="max_results"):
        GrepTool(max_results=0)
    with pytest.raises(ValueError, match="max_results"):
        GrepTool(max_results=GrepTool.HARD_MAX_RESULTS + 1)


def test_grep_tool_rejects_invalid_timeout():
    with pytest.raises(ValueError, match="timeout_seconds"):
        GrepTool(timeout_seconds=0)
    with pytest.raises(ValueError, match="timeout_seconds"):
        GrepTool(timeout_seconds=120)


def test_grep_tool_rejects_invalid_scan_file_cap():
    with pytest.raises(ValueError, match="max_scan_file_bytes"):
        GrepTool(max_scan_file_bytes=0)
    with pytest.raises(ValueError, match="max_scan_file_bytes"):
        GrepTool(max_scan_file_bytes=GrepTool.HARD_MAX_SCAN_FILE_BYTES + 1)


def test_grep_stdlib_fallback_skips_outside_workspace_symlink(
    tmp_path, monkeypatch
):
    """Regression for the second review: with `rg` unavailable, a workspace
    symlink that resolves outside the workspace must not raise; the walker
    skips it via `_resolved_relative_label`.
    """

    outside = tmp_path.parent / "outside_for_grep_fallback"
    outside.mkdir(exist_ok=True)
    (outside / "marker.txt").write_text("OUTSIDE_HIT\n", encoding="utf-8")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "inside.txt").write_text("INSIDE_HIT\n", encoding="utf-8")
    (workspace / "outside_link").symlink_to(outside, target_is_directory=True)

    monkeypatch.setattr(
        "pipy_harness.native.tools.grep.shutil.which", lambda _name: None
    )
    tool = GrepTool()
    context = ToolContext(workspace_root=workspace)
    request = _make_request({"pattern": "HIT"})

    result = tool.invoke(request, context)

    assert result.is_error is False
    assert "inside.txt:1:INSIDE_HIT" in result.output_text
    assert "OUTSIDE_HIT" not in result.output_text


def test_production_tool_registry_includes_grep():
    from pipy_harness.native import production_tool_registry

    registry = production_tool_registry()

    assert "grep" in registry
