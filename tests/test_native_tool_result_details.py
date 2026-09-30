"""Tool-result ``details`` (Pi ``ToolResultMessage.details``, TOOLS3).

The built-in tools return Pi's detail objects, the executor copies them onto
the result message, the session file stores them, and they never change
history equality or a provider request.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentToolCall,
    AgentToolResultMessage,
    ProductContent,
)
from pipy_harness.native.session_tree import MessageEntry, NativeSessionTree
from pipy_harness.native.tools import ToolContext, ToolRequest, make_tool_request_id
from pipy_harness.native.tools.base import ToolExecutionResult, json_safe_details
from pipy_harness.native.tools.find import FindTool
from pipy_harness.native.tools.grep import GrepTool
from pipy_harness.native.tools.ls import LsTool
from pipy_harness.native.tools.read import ReadTool
from pipy_harness.native.tools.truncate import truncate_head


def _run(tool: Any, workspace: Path, **arguments: Any) -> ToolExecutionResult:
    return tool.invoke(
        ToolRequest(
            tool_request_id=make_tool_request_id(),
            tool_name=tool.definition.name,
            arguments=arguments,
        ),
        ToolContext(workspace_root=workspace),
    )


def test_truncation_details_use_pis_field_names() -> None:
    details = truncate_head("a\nb\nc", max_lines=2).to_details()
    assert details == {
        "content": "a\nb",
        "truncated": True,
        "truncatedBy": "lines",
        "totalLines": 3,
        "totalBytes": 5,
        "outputLines": 2,
        "outputBytes": 3,
        "lastLinePartial": False,
        "firstLineExceedsLimit": False,
        "maxLines": 2,
        "maxBytes": 51200,
    }


def test_read_details_only_when_truncated(tmp_path: Path) -> None:
    (tmp_path / "small.txt").write_text("one\ntwo\n")
    assert _run(ReadTool(), tmp_path, path="small.txt").details is None
    (tmp_path / "big.txt").write_text("".join(f"{n}\n" for n in range(2500)))
    details = _run(ReadTool(), tmp_path, path="big.txt").details
    assert details is not None
    assert details["truncation"]["truncatedBy"] == "lines"
    assert details["truncation"]["outputLines"] == 2000
    # A user limit that stops early is not a truncation (Pi: no details).
    limited = _run(ReadTool(), tmp_path, path="big.txt", limit=3)
    assert limited.details is None


def test_search_tools_report_their_limits(tmp_path: Path) -> None:
    for n in range(4):
        (tmp_path / f"f{n}.py").write_text("def x(): pass\n")
    grep = _run(GrepTool(), tmp_path, pattern="def", limit=2)
    assert grep.details == {"matchLimitReached": 2}
    assert _run(GrepTool(), tmp_path, pattern="nothing-here").details is None
    find = _run(FindTool(), tmp_path, pattern="*.py", limit=2)
    assert find.details == {"resultLimitReached": 2}
    assert _run(FindTool(), tmp_path, pattern="*.md").details is None
    ls = _run(LsTool(), tmp_path, limit=2)
    assert ls.details == {"entryLimitReached": 2}
    assert _run(LsTool(), tmp_path).details is None


def test_bash_details_carry_the_truncation_and_full_output_path(tmp_path: Path) -> None:
    from pipy_harness.native.tools.bash import BashTool

    short = _run(BashTool(), tmp_path, command="echo hi")
    assert short.details is None
    long = _run(BashTool(), tmp_path, command="seq 1 3000")
    assert long.details is not None
    assert long.details["truncation"]["truncated"] is True
    assert long.details["truncation"]["totalLines"] == 3000
    path = long.details["fullOutputPath"]
    assert isinstance(path, str) and path in long.output_text
    failing = _run(BashTool(), tmp_path, command="seq 1 3000; exit 3")
    assert failing.is_error is True
    assert failing.details is not None and "fullOutputPath" in failing.details
    for result in (long, failing):
        assert result.details is not None
        Path(result.details["fullOutputPath"]).unlink(missing_ok=True)


def test_execution_results_only_take_json_objects() -> None:
    with pytest.raises(ValueError, match="details"):
        ToolExecutionResult(
            tool_request_id=make_tool_request_id(),
            output_text="x",
            details={"a": object()},
        )
    with pytest.raises(ValueError, match="details"):
        ToolExecutionResult(
            tool_request_id=make_tool_request_id(),
            output_text="x",
            details=cast(Any, {1: "a"}),
        )
    assert json_safe_details({"a": (1, 2)}) == {"a": [1, 2]}
    assert json_safe_details({"a": float("nan")}) is None
    assert json_safe_details("text") is None


def _path(tree: NativeSessionTree) -> Path:
    assert tree.path is not None
    return tree.path


def _result(details: Any) -> AgentToolResultMessage:
    return AgentToolResultMessage(
        tool_request_id="pipy-tool-1",
        tool_name="grep",
        content=ProductContent("a.py:1: x"),
        provider_correlation_id="call-1",
        details=details,
    )


def test_details_take_no_part_in_history_equality() -> None:
    assert _result({"matchLimitReached": 5}) == _result(None)


def test_the_session_file_stores_and_restores_details(tmp_path: Path) -> None:
    tree = NativeSessionTree.create(tmp_path, session_dir=tmp_path / "sessions")
    call = AgentToolCall("call-1", "grep", ProductContent("{}"))
    tree.append_message(AgentAssistantMessage(ProductContent(""), tool_calls=(call,)))
    tree.append_message(_result({"matchLimitReached": 5, "linesTruncated": True}))
    tree.append_message(
        AgentToolResultMessage(
            tool_request_id="pipy-tool-2",
            tool_name="grep",
            content=ProductContent("none"),
            provider_correlation_id="call-1",
        )
    )

    raw = [json.loads(line) for line in _path(tree).read_text().splitlines()]
    stored = [row["message"] for row in raw if row.get("type") == "message"]
    assert stored[1]["details"] == {"matchLimitReached": 5, "linesTruncated": True}
    assert "details" not in stored[2]

    reopened = NativeSessionTree.open(_path(tree), strict=True)
    results = [
        entry.message
        for entry in reopened.get_entries()
        if isinstance(entry, MessageEntry)
        and isinstance(entry.message, AgentToolResultMessage)
    ]
    assert [result.details for result in results] == [
        {"matchLimitReached": 5, "linesTruncated": True},
        None,
    ]


def test_a_stored_non_object_details_is_rejected(tmp_path: Path) -> None:
    tree = NativeSessionTree.create(tmp_path, session_dir=tmp_path / "sessions")
    call = AgentToolCall("call-1", "grep", ProductContent("{}"))
    tree.append_message(AgentAssistantMessage(ProductContent(""), tool_calls=(call,)))
    tree.append_message(_result({"ok": True}))
    lines = _path(tree).read_text().splitlines()
    row = json.loads(lines[-1])
    row["message"]["details"] = ["not", "an", "object"]
    lines[-1] = json.dumps(row)
    _path(tree).write_text("\n".join(lines) + "\n")
    with pytest.raises(ValueError):
        NativeSessionTree.open(_path(tree), strict=True)
