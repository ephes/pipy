"""Symlinks into `.git` for the model-driven tools.

Every tool follows Pi (READ2, READ2b): no deny list, so `read`, `ls`,
`grep` and `find` read through such a symlink and `write` and `edit` write
through it like any other path, as Pi's tools (and rg/fd for a symlinked
search root) do. `bash` is a real shell with the same reach.
"""

from __future__ import annotations

from pathlib import Path

from pipy_harness.native.tools import (
    ToolContext,
    ToolRequest,
    make_tool_request_id,
)
from pipy_harness.native.tools.edit import EditTool
from pipy_harness.native.tools.find import FindTool
from pipy_harness.native.tools.grep import GrepTool
from pipy_harness.native.tools.ls import LsTool
from pipy_harness.native.tools.read import ReadTool
from pipy_harness.native.tools.write import WriteTool


def _git_workspace(tmp_path: Path) -> Path:
    """Create a workspace with a .git/config and a symlink at the root that
    points into the .git directory.
    """

    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "config").write_text("[user]\n  name = secret\n", encoding="utf-8")
    (tmp_path / "gitconfig_link").symlink_to(git_dir / "config")
    (tmp_path / "git_dir_link").symlink_to(git_dir, target_is_directory=True)
    return tmp_path


def _request(tool_name: str, arguments: dict[str, object]) -> ToolRequest:
    return ToolRequest(
        tool_request_id=make_tool_request_id(),
        tool_name=tool_name,
        arguments=arguments,
    )


def test_read_tool_reads_through_a_symlink_into_dot_git(tmp_path: Path):
    workspace = _git_workspace(tmp_path)
    tool = ReadTool()
    context = ToolContext(workspace_root=workspace)

    result = tool.invoke(_request("read", {"path": "gitconfig_link"}), context)

    assert result.is_error is False
    assert result.output_text == "[user]\n  name = secret\n"


def test_write_tool_writes_through_a_symlinked_parent_like_pi(tmp_path: Path):
    workspace = _git_workspace(tmp_path)
    tool = WriteTool()
    context = ToolContext(workspace_root=workspace)

    result = tool.invoke(
        _request("write", {"path": "git_dir_link/new.txt", "content": "x"}),
        context,
    )

    assert result.is_error is False
    assert result.output_text == "Successfully wrote to git_dir_link/new.txt"
    assert (workspace / ".git" / "new.txt").read_text(encoding="utf-8") == "x"


def test_edit_tool_edits_through_a_symlink_like_pi(tmp_path: Path):
    workspace = _git_workspace(tmp_path)
    tool = EditTool()
    context = ToolContext(workspace_root=workspace)

    result = tool.invoke(
        _request(
            "edit",
            {
                "path": "gitconfig_link",
                "edits": [{"oldText": "secret", "newText": "changed"}],
            },
        ),
        context,
    )

    assert result.is_error is False
    assert (workspace / ".git" / "config").read_text(encoding="utf-8") == (
        "[user]\n  name = changed\n"
    )
    # The link itself stays a link.
    assert (workspace / "gitconfig_link").is_symlink()


def test_ls_tool_lists_a_symlinked_dot_git_directory(tmp_path: Path):
    workspace = _git_workspace(tmp_path)
    tool = LsTool()
    context = ToolContext(workspace_root=workspace)

    result = tool.invoke(_request("ls", {"path": "git_dir_link"}), context)

    assert result.is_error is False
    assert result.output_text == "config"


def test_ls_tool_root_listing_includes_symlinks_into_dot_git(tmp_path: Path):
    workspace = _git_workspace(tmp_path)
    (workspace / "visible.txt").write_text("ok", encoding="utf-8")
    tool = LsTool()
    context = ToolContext(workspace_root=workspace)

    result = tool.invoke(_request("ls", {"path": "."}), context)

    assert result.output_text == ".git/\ngit_dir_link/\ngitconfig_link\nvisible.txt"


def test_grep_tool_searches_a_symlinked_dot_git_search_root(tmp_path: Path):
    workspace = _git_workspace(tmp_path)
    tool = GrepTool()
    context = ToolContext(workspace_root=workspace)

    result = tool.invoke(
        _request("grep", {"pattern": "secret", "path": "git_dir_link"}),
        context,
    )

    assert result.is_error is False
    assert result.output_text == "config:2:   name = secret"


def test_find_tool_walks_a_symlinked_dot_git_search_root(tmp_path: Path):
    workspace = _git_workspace(tmp_path)
    tool = FindTool()
    context = ToolContext(workspace_root=workspace)

    result = tool.invoke(
        _request("find", {"pattern": "*", "path": "git_dir_link"}),
        context,
    )

    assert result.is_error is False
    assert result.output_text == "config"
