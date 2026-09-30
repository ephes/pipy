"""The `edit` tool follows Pi's `edit.ts` and `edit-diff.ts` (TOOLS2/READ2b).

`tests/fixtures/pi_tools2/edit_diff_cases.json` holds Pi's own results: the
cases were run through ``generateDiffString``, jsdiff ``diffLines`` and
``applyEditsToNormalizedContent`` from pi-mono ``1b347794e`` on Node.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest

from pipy_harness.native.agent.tools import ToolExecutor
from pipy_harness.native.tools import (
    ToolContext,
    ToolPort,
    ToolRequest,
    make_tool_request_id,
    validate_arguments,
)
from pipy_harness.native.tools.edit import EDIT_TOOL_DESCRIPTION, EditTool
from pipy_harness.native.tools.edit_diff import (
    Edit,
    EditError,
    apply_edits_to_normalized_content,
    detect_line_ending,
    diff_lines,
    generate_diff_string,
    normalize_to_lf,
)

_FIXTURES = json.loads(
    (
        Path(__file__).parent / "fixtures" / "pi_tools2" / "edit_diff_cases.json"
    ).read_text()
)


def _invoke(
    workspace: Path,
    arguments: dict[str, object],
    *,
    cancel: threading.Event | None = None,
):
    tool = EditTool()
    prepared = tool.prepare_arguments(arguments)
    assert isinstance(prepared, dict)
    validated = validate_arguments(
        tool_name="edit", schema=tool.definition.input_schema, arguments=prepared
    )
    return tool.invoke(
        ToolRequest(
            tool_request_id=make_tool_request_id(),
            tool_name="edit",
            arguments=validated,
        ),
        ToolContext(
            workspace_root=workspace,
            cancel_event=cancel,
        ),
    )


def _one(old: str, new: str) -> dict[str, object]:
    return {"edits": [{"oldText": old, "newText": new}]}


def test_definition_matches_pi() -> None:
    tool = EditTool()
    assert isinstance(tool, ToolPort)
    assert tool.definition.description == EDIT_TOOL_DESCRIPTION
    assert EDIT_TOOL_DESCRIPTION.startswith(
        "Edit a single file using exact text replacement. Every edits[].oldText"
    )
    schema = tool.definition.input_schema
    assert schema["required"] == ["path", "edits"]
    item = schema["properties"]["edits"]["items"]
    assert item["required"] == ["oldText", "newText"]
    assert item["properties"]["newText"]["description"] == (
        "Replacement text for this targeted edit."
    )
    assert "old_string" not in schema["properties"]


@pytest.mark.parametrize(
    "entry",
    [entry for entry in _FIXTURES if entry["case"]["kind"] == "diff"],
    ids=lambda entry: repr(entry["case"]["new"][:12]),
)
def test_generate_diff_string_matches_pi(entry: dict) -> None:
    case, pi = entry["case"], entry["pi"]
    assert generate_diff_string(case["old"], case["new"]) == (
        pi["diff"],
        pi.get("firstChangedLine"),
    )


@pytest.mark.parametrize(
    "entry", [entry for entry in _FIXTURES if entry["case"]["kind"] == "lines"]
)
def test_diff_lines_matches_jsdiff(entry: dict) -> None:
    case = entry["case"]
    parts = diff_lines(case["old"], case["new"])
    assert [[p.value, p.added, p.removed, p.count] for p in parts] == entry["pi"]


@pytest.mark.parametrize(
    "entry",
    [entry for entry in _FIXTURES if entry["case"]["kind"] == "apply"],
    ids=lambda entry: json.dumps(entry["case"]["edits"])[:40],
)
def test_apply_edits_matches_pi(entry: dict) -> None:
    case = entry["case"]
    edits = [Edit(e["oldText"], e["newText"]) for e in case["edits"]]
    try:
        base, new = apply_edits_to_normalized_content(
            normalize_to_lf(case["content"]), edits, case["path"]
        )
    except EditError as exc:
        assert entry["pi"] == {"error": str(exc)}
    else:
        assert entry["pi"] == {"baseContent": base, "newContent": new}


def test_edits_a_file(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\ny = 2\n")

    result = _invoke(tmp_path, {"path": "a.py", **_one("y = 2", "y = 3")})

    assert result.is_error is False
    assert result.output_text == "Successfully replaced 1 block(s) in a.py."
    assert (tmp_path / "a.py").read_text() == "x = 1\ny = 3\n"
    # Pi `details`: the display diff and the first changed line (TOOLS3).
    assert result.details == {
        "diff": " 1 x = 1\n-2 y = 2\n+2 y = 3",
        "firstChangedLine": 2,
    }


def test_multiple_disjoint_edits_apply_together(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("one\ntwo\nthree\n")

    result = _invoke(
        tmp_path,
        {
            "path": "a.txt",
            "edits": [
                {"oldText": "three", "newText": "3"},
                {"oldText": "one", "newText": "1"},
            ],
        },
    )

    assert result.output_text == "Successfully replaced 2 block(s) in a.txt."
    assert (tmp_path / "a.txt").read_text() == "1\ntwo\n3\n"


def test_crlf_and_bom_survive(tmp_path: Path) -> None:
    target = tmp_path / "w.txt"
    target.write_bytes(b"\xef\xbb\xbfalpha\r\nbeta\r\n")

    result = _invoke(tmp_path, {"path": "w.txt", **_one("alpha\nbeta", "gamma\ndelta")})

    assert result.is_error is False
    assert target.read_bytes() == b"\xef\xbb\xbfgamma\r\ndelta\r\n"


def test_fuzzy_match_keeps_unchanged_lines(tmp_path: Path) -> None:
    target = tmp_path / "q.txt"
    target.write_text("say “hi”  \nkeep’ \nend\n")

    result = _invoke(tmp_path, {"path": "q.txt", **_one('say "hi"', "said")})

    assert result.is_error is False
    # Only the touched line takes the normalized text.
    assert target.read_text() == "said\nkeep’ \nend\n"


def test_detect_line_ending() -> None:
    assert detect_line_ending("a\r\nb\n") == "\r\n"
    assert detect_line_ending("a\nb\r\n") == "\n"
    assert detect_line_ending("a") == "\n"


@pytest.mark.parametrize(
    ("content", "arguments", "expected"),
    [
        (
            "a\n",
            _one("zzz", "b"),
            "Could not find the exact text in f.txt. The old text must match "
            "exactly including all whitespace and newlines.",
        ),
        (
            "a\na\n",
            _one("a", "b"),
            "Found 2 occurrences of the text in f.txt. The text must be unique. "
            "Please provide more context to make it unique.",
        ),
        ("a\n", _one("", "b"), "oldText must not be empty in f.txt."),
        (
            "a\n",
            _one("a", "a"),
            "No changes made to f.txt. The replacement produced identical content. "
            "This might indicate an issue with special characters or the text not "
            "existing as expected.",
        ),
        (
            "a\n",
            {"edits": []},
            "Edit tool input is invalid. edits must contain at least one replacement.",
        ),
    ],
)
def test_error_texts_match_pi(
    tmp_path: Path, content: str, arguments: dict[str, object], expected: str
) -> None:
    (tmp_path / "f.txt").write_text(content)

    result = _invoke(tmp_path, {"path": "f.txt", **arguments})

    assert result.is_error is True
    assert result.output_text == expected
    assert (tmp_path / "f.txt").read_text() == content


def test_a_missing_file_is_pis_access_error(tmp_path: Path) -> None:
    result = _invoke(tmp_path, {"path": "nope.txt", **_one("a", "b")})

    assert result.is_error is True
    assert result.output_text == "Could not edit file: nope.txt. Error code: ENOENT."


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores permissions")
def test_a_read_only_file_is_pis_access_error(tmp_path: Path) -> None:
    target = tmp_path / "ro.txt"
    target.write_text("a\n")
    target.chmod(0o400)

    result = _invoke(tmp_path, {"path": "ro.txt", **_one("a", "b")})

    assert result.output_text == "Could not edit file: ro.txt. Error code: EACCES."


def test_a_directory_is_nodes_eisdir_read_error(tmp_path: Path) -> None:
    (tmp_path / "d").mkdir()

    result = _invoke(tmp_path, {"path": "d", **_one("a", "b")})

    assert result.is_error is True
    assert result.output_text == "EISDIR: illegal operation on a directory, read"


def test_resolves_paths_like_pi_without_a_deny_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "ws"
    (workspace / ".git").mkdir(parents=True)
    (workspace / ".git" / "config").write_text("[core]\n")
    outside = tmp_path / "outside.txt"
    outside.write_text("old\n")
    monkeypatch.setenv("HOME", str(tmp_path))

    assert (
        _invoke(workspace, {"path": ".git/config", **_one("core", "x")}).is_error
        is False
    )
    assert (
        _invoke(workspace, {"path": "~/outside.txt", **_one("old", "new")}).is_error
        is False
    )

    assert (workspace / ".git" / "config").read_text() == "[x]\n"
    assert outside.read_text() == "new\n"


def test_an_aborted_call_changes_nothing(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("a\n")
    cancel = threading.Event()
    cancel.set()

    result = _invoke(tmp_path, {"path": "a.txt", **_one("a", "b")}, cancel=cancel)

    assert result.output_text == "Operation aborted"
    assert (tmp_path / "a.txt").read_text() == "a\n"


@pytest.mark.parametrize(
    "raw",
    [
        {"path": "f", "edits": '[{"oldText": "a", "newText": "b"}]'},
        {"path": "f", "edits": '{"oldText": "a", "newText": "b"}'},
        {"path": "f", "edits": {"oldText": "a", "newText": "b"}},
        {"path": "f", "oldText": "a", "newText": "b"},
    ],
)
def test_prepare_arguments_accepts_pis_legacy_shapes(raw: dict[str, object]) -> None:
    assert EditTool.prepare_arguments(raw) == {
        "path": "f",
        "edits": [{"oldText": "a", "newText": "b"}],
    }


def test_prepare_arguments_appends_legacy_fields_to_edits() -> None:
    prepared = EditTool.prepare_arguments(
        {
            "path": "f",
            "edits": [{"oldText": "x", "newText": "y"}],
            "oldText": "a",
            "newText": "b",
        }
    )
    assert prepared == {
        "path": "f",
        "edits": [{"oldText": "x", "newText": "y"}, {"oldText": "a", "newText": "b"}],
    }
    assert EditTool.prepare_arguments("not an object") == "not an object"


def test_the_executor_prepares_arguments_before_validation(tmp_path: Path) -> None:
    from pipy_harness.native.agent import AgentToolCall
    from pipy_harness.native.agent.content import ProductContent

    (tmp_path / "a.txt").write_text("a\n")
    executor = ToolExecutor({"edit": EditTool()})
    call = AgentToolCall(
        "c1",
        "edit",
        ProductContent(json.dumps({"path": "a.txt", "oldText": "a", "newText": "b"})),
    )

    outcome = executor.execute(call, ToolContext(workspace_root=tmp_path))

    assert outcome.result.content.value == "Successfully replaced 1 block(s) in a.txt."
    assert (tmp_path / "a.txt").read_text() == "b\n"
