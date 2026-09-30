"""Tests for the `ls` tool, which follows Pi's `ls` (TOOLS1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from pipy_harness.native.tools import (
    ToolContext,
    ToolPort,
    ToolRequest,
    make_tool_request_id,
)
from pipy_harness.native.tools.ls import LsTool


def _make_request(arguments: dict[str, object]) -> ToolRequest:
    return ToolRequest(
        tool_request_id=make_tool_request_id(),
        tool_name="ls",
        arguments=arguments,
    )


def test_ls_tool_satisfies_tool_port_protocol():
    tool = LsTool()

    assert isinstance(tool, ToolPort)


def test_ls_tool_schema_and_description_match_pi():
    definition = LsTool().definition

    assert definition.description == (
        "List directory contents. Returns entries sorted alphabetically, with '/' "
        "suffix for directories. Includes dotfiles. Output is truncated to 500 "
        "entries or 50KB (whichever is hit first)."
    )
    schema = definition.input_schema
    assert schema["type"] == "object"
    assert schema["properties"] == {
        "path": {
            "type": "string",
            "description": "Directory to list (default: current directory)",
        },
        "limit": {
            "type": "integer",
            "description": "Maximum number of entries to return (default: 500)",
        },
    }
    assert "required" not in schema
    assert schema["additionalProperties"] is False


def test_ls_tool_defaults_to_the_workspace_root(tmp_path: Path):
    (tmp_path / "a.py").write_text("x", encoding="utf-8")
    result = LsTool().invoke(_make_request({}), ToolContext(workspace_root=tmp_path))

    assert result.is_error is False
    assert result.output_text == "a.py"


def test_ls_tool_sorts_case_insensitively_with_dir_suffix_and_dotfiles(
    tmp_path: Path,
):
    for name in ("b.txt", "A.txt", ".env", "C"):
        (tmp_path / name).write_text("x", encoding="utf-8")
    (tmp_path / "dir").mkdir()
    (tmp_path / "broken").symlink_to(tmp_path / "missing")
    (tmp_path / "dirlink").symlink_to(tmp_path / "dir")

    result = LsTool().invoke(
        _make_request({"path": "."}), ToolContext(workspace_root=tmp_path)
    )

    # The broken symlink cannot be stat'ed and is skipped, as in Pi; a link
    # to a directory is listed as a directory (stat follows it).
    assert result.output_text == ".env\nA.txt\nb.txt\nC\ndir/\ndirlink/"


def test_ls_tool_limit_notice(tmp_path: Path):
    for i in range(10):
        (tmp_path / f"f{i}.txt").write_text("x", encoding="utf-8")

    result = LsTool().invoke(
        _make_request({"limit": 3}), ToolContext(workspace_root=tmp_path)
    )

    assert result.is_error is False
    assert result.output_text == (
        "f0.txt\nf1.txt\nf2.txt\n\n[3 entries limit reached. Use limit=6 for more]"
    )


def test_ls_tool_exact_limit_has_no_notice(tmp_path: Path):
    for i in range(3):
        (tmp_path / f"f{i}.txt").write_text("x", encoding="utf-8")

    result = LsTool().invoke(
        _make_request({"limit": 3}), ToolContext(workspace_root=tmp_path)
    )

    assert result.output_text == "f0.txt\nf1.txt\nf2.txt"


def test_ls_tool_byte_cap_notice(tmp_path: Path):
    # 520 names of 104 bytes each pass 50 KB before the entry limit.
    for i in range(520):
        (tmp_path / f"{i:04d}{'x' * 100}").write_text("", encoding="utf-8")

    result = LsTool().invoke(
        _make_request({"limit": 600}), ToolContext(workspace_root=tmp_path)
    )

    body, notice = result.output_text.rsplit("\n\n", 1)
    assert notice == "[50.0KB limit reached]"
    assert len(body.encode()) <= 50 * 1024
    # 104 + 486 * 105 = 51134 bytes; one more row would pass 51200.
    assert len(body.splitlines()) == 487


def test_ls_tool_lists_workspace_root_with_dot(tmp_path: Path):
    (tmp_path / "a.py").write_text("x", encoding="utf-8")
    (tmp_path / "subdir").mkdir()
    tool = LsTool()
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request({"path": "."})

    result = tool.invoke(request, context)

    assert result.is_error is False
    assert result.output_text == "a.py\nsubdir/"


def test_ls_tool_lists_subdirectory(tmp_path: Path):
    (tmp_path / "subdir").mkdir()
    (tmp_path / "subdir" / "nested.txt").write_text("y", encoding="utf-8")
    tool = LsTool()
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request({"path": "subdir"})

    result = tool.invoke(request, context)

    assert result.is_error is False
    assert result.output_text == "nested.txt"


def test_ls_tool_lists_dot_git_like_pi(tmp_path: Path):
    # READ2: Pi's ls has no deny list.
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "config").write_text("x", encoding="utf-8")
    (git_dir / "objects").mkdir()

    result = LsTool().invoke(
        _make_request({"path": ".git"}), ToolContext(workspace_root=tmp_path)
    )

    assert result.is_error is False
    assert result.output_text == "config\nobjects/"


def test_ls_tool_lists_git_ignored_and_generated_children(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    (tmp_path / "ignored.txt").write_text("i", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "yarn.lock").write_text("l", encoding="utf-8")
    (tmp_path / "visible.txt").write_text("y", encoding="utf-8")

    result = LsTool().invoke(
        _make_request({"path": "."}), ToolContext(workspace_root=tmp_path)
    )

    assert result.output_text == (
        ".git/\n.gitignore\nignored.txt\nnode_modules/\nvisible.txt\nyarn.lock"
    )


def test_ls_tool_lists_absolute_path_outside_cwd(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    (outside / "sub").mkdir(parents=True)
    (outside / "b.txt").write_text("b", encoding="utf-8")

    result = LsTool().invoke(
        _make_request({"path": str(outside)}), ToolContext(workspace_root=workspace)
    )

    assert result.is_error is False
    assert result.output_text == "b.txt\nsub/"


def test_ls_tool_resolves_parent_traversal_and_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (tmp_path / "sibling.txt").write_text("s", encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path))
    context = ToolContext(workspace_root=workspace)

    parent = LsTool().invoke(_make_request({"path": ".."}), context)
    home = LsTool().invoke(_make_request({"path": "~"}), context)

    assert parent.output_text == "sibling.txt\nworkspace/"
    assert home.output_text == parent.output_text


def test_ls_tool_skips_entries_whose_stat_fails(tmp_path: Path):
    (tmp_path / "dangling").symlink_to(tmp_path / "missing-target")
    (tmp_path / "linked").symlink_to(tmp_path)
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")

    result = LsTool().invoke(
        _make_request({"path": "."}), ToolContext(workspace_root=tmp_path)
    )

    # Pi stats each entry (following symlinks) and skips failures.
    assert result.output_text == "a.txt\nlinked/"


def test_ls_tool_missing_directory_is_error_observation(tmp_path: Path):
    tool = LsTool()
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request({"path": "missing"})

    result = tool.invoke(request, context)

    assert result.is_error is True
    assert "does not exist" in result.output_text


def test_ls_tool_counts_dot_git_toward_the_row_cap(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    tool = LsTool()
    context = ToolContext(workspace_root=tmp_path)

    result = tool.invoke(_make_request({"path": ".", "limit": 1}), context)

    assert result.is_error is False
    assert result.output_text == (
        ".git/\n\n[1 entries limit reached. Use limit=2 for more]"
    )


def test_ls_tool_empty_directory_reports_safely(tmp_path: Path):
    (tmp_path / "empty").mkdir()
    tool = LsTool()
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request({"path": "empty"})

    result = tool.invoke(request, context)

    assert result.is_error is False
    assert result.output_text == "(empty directory)"


def test_production_tool_registry_includes_ls():
    from pipy_harness.native import production_tool_registry

    registry = production_tool_registry()

    assert "ls" in registry
