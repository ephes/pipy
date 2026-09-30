"""The `write` tool follows Pi's `write.ts` (TOOLS2/READ2b).

Pi resolves the path with `resolveToCwd` (no deny list), creates parent
directories, creates or overwrites the file, answers `Successfully wrote to
{path}`, and lets filesystem errors through as Node's messages.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest

from pipy_harness.native.tools import (
    ToolContext,
    ToolPort,
    ToolRequest,
    make_tool_request_id,
)
from pipy_harness.native.tools.write import (
    WRITE_TOOL_DESCRIPTION,
    WriteTool,
)


def _invoke(
    workspace: Path,
    arguments: dict[str, object],
    *,
    cancel: threading.Event | None = None,
):
    return WriteTool().invoke(
        ToolRequest(
            tool_request_id=make_tool_request_id(),
            tool_name="write",
            arguments=arguments,
        ),
        ToolContext(
            workspace_root=workspace,
            cancel_event=cancel,
        ),
    )


def test_definition_matches_pi() -> None:
    tool = WriteTool()
    assert isinstance(tool, ToolPort)
    assert tool.definition.description == (
        "Write content to a file. Creates the file if it doesn't exist, "
        "overwrites if it does. Automatically creates parent directories."
    )
    assert tool.definition.description == WRITE_TOOL_DESCRIPTION
    schema = tool.definition.input_schema
    assert schema["required"] == ["path", "content"]
    assert schema["properties"] == {
        "path": {
            "type": "string",
            "description": "Path to the file to write (relative or absolute)",
        },
        "content": {"type": "string", "description": "Content to write to the file"},
    }


def test_creates_a_new_file(tmp_path: Path) -> None:
    result = _invoke(tmp_path, {"path": "new.txt", "content": "hello\n"})

    assert result.is_error is False
    assert result.output_text == "Successfully wrote to new.txt"
    assert (tmp_path / "new.txt").read_text() == "hello\n"


def test_overwrites_an_existing_file(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("old content that is longer\n")

    result = _invoke(tmp_path, {"path": "a.txt", "content": "new\n"})

    assert result.output_text == "Successfully wrote to a.txt"
    assert (tmp_path / "a.txt").read_text() == "new\n"


def test_creates_parent_directories(tmp_path: Path) -> None:
    result = _invoke(tmp_path, {"path": "a/b/c.txt", "content": "x"})

    assert result.is_error is False
    assert (tmp_path / "a" / "b" / "c.txt").read_text() == "x"


def test_writes_empty_content_and_keeps_line_endings(tmp_path: Path) -> None:
    _invoke(tmp_path, {"path": "empty.txt", "content": ""})
    _invoke(tmp_path, {"path": "crlf.txt", "content": "a\r\nb\r\n"})

    assert (tmp_path / "empty.txt").read_bytes() == b""
    assert (tmp_path / "crlf.txt").read_bytes() == b"a\r\nb\r\n"


def test_resolves_absolute_parent_and_home_paths_without_a_deny_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    outside = tmp_path / "outside.txt"
    assert (
        _invoke(workspace, {"path": str(outside), "content": "abs"}).is_error is False
    )
    assert (
        _invoke(workspace, {"path": "../rel.txt", "content": "rel"}).is_error is False
    )
    assert _invoke(workspace, {"path": "~/h.txt", "content": "home"}).is_error is False
    assert _invoke(workspace, {"path": "@at.txt", "content": "at"}).is_error is False
    assert _invoke(workspace, {"path": ".git/config", "content": "g"}).is_error is False

    assert outside.read_text() == "abs"
    assert (tmp_path / "rel.txt").read_text() == "rel"
    assert (home / "h.txt").read_text() == "home"
    assert (workspace / "at.txt").read_text() == "at"
    assert (workspace / ".git" / "config").read_text() == "g"


def test_the_success_text_names_the_raw_path(tmp_path: Path) -> None:
    result = _invoke(tmp_path, {"path": "./x/../y.txt", "content": ""})

    assert result.output_text == "Successfully wrote to ./x/../y.txt"
    assert (tmp_path / "y.txt").exists()


def test_writing_a_directory_is_nodes_eisdir_error(tmp_path: Path) -> None:
    (tmp_path / "dir").mkdir()

    result = _invoke(tmp_path, {"path": "dir", "content": "x"})

    assert result.is_error is True
    assert result.output_text == (
        f"EISDIR: illegal operation on a directory, open '{tmp_path / 'dir'}'"
    )


def test_a_file_in_the_parent_chain_is_a_mkdir_error(tmp_path: Path) -> None:
    (tmp_path / "file").write_text("")

    result = _invoke(tmp_path, {"path": "file/child.txt", "content": "x"})

    assert result.is_error is True
    assert result.output_text.endswith(f", mkdir '{tmp_path / 'file'}'")
    assert result.output_text.split(":", 1)[0] in {"EEXIST", "ENOTDIR"}


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores permissions")
def test_permission_denied_is_nodes_eacces_error(tmp_path: Path) -> None:
    locked = tmp_path / "locked.txt"
    locked.write_text("")
    locked.chmod(0o400)

    result = _invoke(tmp_path, {"path": "locked.txt", "content": "x"})

    assert result.is_error is True
    assert result.output_text == f"EACCES: permission denied, open '{locked}'"


def test_an_invalid_file_url_is_an_error_result(tmp_path: Path) -> None:
    result = _invoke(tmp_path, {"path": "file://host/x", "content": "x"})

    assert result.is_error is True
    assert "File URL host" in result.output_text


def test_an_aborted_call_writes_nothing(tmp_path: Path) -> None:
    cancel = threading.Event()
    cancel.set()

    result = _invoke(tmp_path, {"path": "a.txt", "content": "x"}, cancel=cancel)

    assert result.is_error is True
    assert result.output_text == "Operation aborted"
    assert not (tmp_path / "a.txt").exists()


def test_a_write_has_no_details(tmp_path: Path) -> None:
    """Pi `write` returns `details: undefined`; its row draws the arguments."""

    result = _invoke(tmp_path, {"path": "a.py", "content": "a\r\n\tb\n"})

    assert result.is_error is False
    assert result.details is None
