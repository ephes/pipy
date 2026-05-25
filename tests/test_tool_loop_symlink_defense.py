"""Regression tests for the Tool-Loop Parity Track review (round 1).

Critical finding: `.git` default-deny was bypassable through workspace
symlinks. The model-driven tools now resolve the candidate path and
re-check `_is_ignored_or_generated` against the resolved relative label,
closing the gap for `read`, `ls`, `grep`, `find`, `write`, and `edit`.
"""

from __future__ import annotations

from pathlib import Path

from pipy_harness.native.tools import (
    EditTool,
    FindTool,
    GrepTool,
    LsTool,
    ReadTool,
    ToolContext,
    ToolRequest,
    WriteTool,
    make_tool_request_id,
)


def _git_workspace(tmp_path: Path) -> Path:
    """Create a workspace with a .git/config and a symlink at the root that
    points into the .git directory.
    """

    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "config").write_text(
        "[user]\n  name = secret\n", encoding="utf-8"
    )
    (tmp_path / "gitconfig_link").symlink_to(git_dir / "config")
    (tmp_path / "git_dir_link").symlink_to(git_dir, target_is_directory=True)
    return tmp_path


def _request(tool_name: str, arguments: dict[str, object]) -> ToolRequest:
    return ToolRequest(
        tool_request_id=make_tool_request_id(),
        tool_name=tool_name,
        arguments=arguments,
    )


def test_read_tool_refuses_symlink_into_dot_git(tmp_path: Path):
    workspace = _git_workspace(tmp_path)
    tool = ReadTool()
    context = ToolContext(workspace_root=workspace)

    result = tool.invoke(
        _request("read", {"path": "gitconfig_link"}), context
    )

    assert result.is_error is True
    assert "ignored or under .git" in result.output_text


def test_write_tool_refuses_symlinked_parent_into_dot_git(tmp_path: Path):
    workspace = _git_workspace(tmp_path)
    tool = WriteTool()
    context = ToolContext(workspace_root=workspace)

    result = tool.invoke(
        _request(
            "write",
            {"path": "git_dir_link/new.txt", "content": "ignored"},
        ),
        context,
    )

    assert result.is_error is True
    assert (
        "ignored or under .git" in result.output_text
        or "parent" in result.output_text
    )
    assert not (workspace / ".git" / "new.txt").exists()


def test_edit_tool_refuses_symlink_into_dot_git(tmp_path: Path):
    workspace = _git_workspace(tmp_path)
    tool = EditTool()
    context = ToolContext(workspace_root=workspace)

    result = tool.invoke(
        _request(
            "edit",
            {
                "path": "gitconfig_link",
                "old_string": "secret",
                "new_string": "compromised",
            },
        ),
        context,
    )

    assert result.is_error is True
    assert "ignored or under .git" in result.output_text
    assert (
        "secret"
        in (workspace / ".git" / "config").read_text(encoding="utf-8")
    )


def test_ls_tool_refuses_symlinked_dot_git_directory(tmp_path: Path):
    workspace = _git_workspace(tmp_path)
    tool = LsTool()
    context = ToolContext(workspace_root=workspace)

    result = tool.invoke(_request("ls", {"path": "git_dir_link"}), context)

    assert result.is_error is True
    assert "ignored or under .git" in result.output_text


def test_ls_tool_root_listing_skips_symlinks_into_dot_git(tmp_path: Path):
    workspace = _git_workspace(tmp_path)
    (workspace / "visible.txt").write_text("ok", encoding="utf-8")
    tool = LsTool()
    context = ToolContext(workspace_root=workspace)

    result = tool.invoke(_request("ls", {"path": "."}), context)

    assert result.is_error is False
    assert "visible.txt" in result.output_text
    assert "gitconfig_link" not in result.output_text
    assert "git_dir_link" not in result.output_text


def test_grep_tool_refuses_symlinked_dot_git_search_root(tmp_path: Path):
    workspace = _git_workspace(tmp_path)
    tool = GrepTool()
    context = ToolContext(workspace_root=workspace)

    result = tool.invoke(
        _request("grep", {"pattern": "secret", "path": "git_dir_link"}),
        context,
    )

    assert result.is_error is True
    assert "ignored or under .git" in result.output_text


def test_find_tool_refuses_symlinked_dot_git_search_root(tmp_path: Path):
    workspace = _git_workspace(tmp_path)
    tool = FindTool()
    context = ToolContext(workspace_root=workspace)

    result = tool.invoke(
        _request("find", {"pattern": "*", "path": "git_dir_link"}),
        context,
    )

    assert result.is_error is True
    assert "ignored or under .git" in result.output_text
