"""Tests for the `grep` tool, which follows Pi's `grep` (TOOLS1).

Most behaviour runs on both engines: `rg` (when it is installed) and the
stdlib fallback, which must give the same observable output.
"""

from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path

import pytest

from pipy_harness.native.tools import (
    ToolArgumentError,
    ToolContext,
    ToolPort,
    ToolRequest,
    make_tool_request_id,
)
from pipy_harness.native.tools.grep import GrepTool

_HAS_RG = shutil.which("rg") is not None


def _make_request(arguments: dict[str, object]) -> ToolRequest:
    return ToolRequest(
        tool_request_id=make_tool_request_id(),
        tool_name="grep",
        arguments=arguments,
    )


@pytest.fixture(params=["rg", "stdlib"])
def engine(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    if request.param == "rg":
        if not _HAS_RG:
            pytest.skip("rg is not installed")
    else:
        monkeypatch.setattr(
            "pipy_harness.native.tools.grep.shutil.which", lambda _name: None
        )
    return request.param


def _grep(root: Path, **arguments: object) -> str:
    result = GrepTool().invoke(
        _make_request(arguments), ToolContext(workspace_root=root)
    )
    assert result.is_error is False, result.output_text
    return result.output_text


def _write(root: Path, relative: str, text: str) -> None:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def test_grep_tool_satisfies_tool_port_protocol():
    assert isinstance(GrepTool(), ToolPort)


def test_grep_tool_schema_and_description_match_pi():
    definition = GrepTool().definition

    assert definition.description == (
        "Search file contents for a pattern. Returns matching lines with file "
        "paths and line numbers. Respects .gitignore. Output is truncated to 100 "
        "matches or 50KB (whichever is hit first). Long lines are truncated to "
        "500 chars."
    )
    schema = definition.input_schema
    assert schema["required"] == ["pattern"]
    assert schema["additionalProperties"] is False
    assert {
        name: (spec["type"], spec["description"])
        for name, spec in schema["properties"].items()
    } == {
        "pattern": ("string", "Search pattern (regex or literal string)"),
        "path": (
            "string",
            "Directory or file to search (default: current directory)",
        ),
        "glob": (
            "string",
            "Filter files by glob pattern, e.g. '*.ts' or '**/*.spec.ts'",
        ),
        "ignoreCase": ("boolean", "Case-insensitive search (default: false)"),
        "literal": (
            "boolean",
            "Treat pattern as literal string instead of regex (default: false)",
        ),
        "context": (
            "integer",
            "Number of lines to show before and after each match (default: 0)",
        ),
        "limit": ("integer", "Maximum number of matches to return (default: 100)"),
    }


def test_grep_regex_rows_relative_to_search_path(tmp_path: Path, engine: str):
    _write(tmp_path, "a.txt", "alpha\nneedle 1\nomega\n")
    _write(tmp_path, "sub/b.txt", "beta\nneedle 22\n")

    output = _grep(tmp_path, pattern=r"needle \d+")
    assert sorted(output.splitlines()) == [
        "a.txt:2: needle 1",
        "sub/b.txt:2: needle 22",
    ]
    assert _grep(tmp_path, pattern="needle", path="sub") == "b.txt:2: needle 22"


def test_grep_file_path_uses_the_basename(tmp_path: Path, engine: str):
    _write(tmp_path, "sub/b.txt", "needle\n")

    assert _grep(tmp_path, pattern="needle", path="sub/b.txt") == "b.txt:1: needle"


def test_grep_literal_and_ignore_case(tmp_path: Path, engine: str):
    _write(tmp_path, "a.txt", "a.c\nabc\nA.C\n")

    assert _grep(tmp_path, pattern="a.c", literal=True) == "a.txt:1: a.c"
    assert _grep(tmp_path, pattern="a.c", literal=True, ignoreCase=True) == (
        "a.txt:1: a.c\na.txt:3: A.C"
    )
    assert _grep(tmp_path, pattern="a.c") == "a.txt:1: a.c\na.txt:2: abc"


def test_grep_invalid_regex_is_an_error(tmp_path: Path, engine: str):
    _write(tmp_path, "a.txt", "x\n")
    result = GrepTool().invoke(
        _make_request({"pattern": "("}), ToolContext(workspace_root=tmp_path)
    )
    assert result.is_error is True
    assert result.output_text.startswith("grep error: ")
    assert "regex parse error" in result.output_text


def test_grep_glob_is_case_sensitive_like_rg(tmp_path: Path, engine: str):
    _write(tmp_path, "a.ts", "needle\n")
    _write(tmp_path, "B.TS", "needle\n")
    _write(tmp_path, "deep/c.ts", "needle\n")
    _write(tmp_path, "d.txt", "needle\n")

    output = _grep(tmp_path, pattern="needle", glob="*.ts")
    assert sorted(output.splitlines()) == ["a.ts:1: needle", "deep/c.ts:1: needle"]
    output = _grep(tmp_path, pattern="needle", glob="*.ts", ignoreCase=True)
    assert "B.TS" not in output


def test_grep_negated_glob(tmp_path: Path, engine: str):
    _write(tmp_path, "a.ts", "needle\n")
    _write(tmp_path, "b.txt", "needle\n")
    _write(tmp_path, "skip/c.md", "needle\n")

    output = _grep(tmp_path, pattern="needle", glob="!*.txt")
    assert sorted(output.splitlines()) == ["a.ts:1: needle", "skip/c.md:1: needle"]
    output = _grep(tmp_path, pattern="needle", glob="!skip")
    assert sorted(output.splitlines()) == ["a.ts:1: needle", "b.txt:1: needle"]


def test_grep_slash_glob_is_relative_to_the_root_not_the_path(
    tmp_path: Path, engine: str
):
    _write(tmp_path, "src/n.txt", "needle\n")
    _write(tmp_path, "src/a/n.txt", "needle\n")

    assert _grep(tmp_path, pattern="needle", path="src", glob="src/a/*.txt") == (
        "a/n.txt:1: needle"
    )
    assert _grep(tmp_path, pattern="needle", path="src", glob="a/*.txt") == (
        "No matches found"
    )


def test_grep_file_path_ignores_the_glob(tmp_path: Path, engine: str):
    _write(tmp_path, "n.txt", "needle\n")

    assert _grep(tmp_path, pattern="needle", path="n.txt", glob="*.md") == (
        "n.txt:1: needle"
    )


def test_grep_context_lines(tmp_path: Path, engine: str):
    _write(tmp_path, "a.txt", "one\ntwo\nneedle\nfour\n")

    assert _grep(tmp_path, pattern="needle", context=1) == (
        "a.txt-2- two\na.txt:3: needle\na.txt-4- four"
    )
    assert _grep(tmp_path, pattern="one", context=5) == (
        "a.txt:1: one\na.txt-2- two\na.txt-3- needle\na.txt-4- four\na.txt-5- "
    )


def test_grep_long_lines_are_cut_with_a_notice(tmp_path: Path, engine: str):
    _write(tmp_path, "a.txt", "needle " + "x" * 600 + "\n")

    output = _grep(tmp_path, pattern="needle")
    row, notice = output.split("\n\n")
    assert row == "a.txt:1: " + ("needle " + "x" * 600)[:500] + "... [truncated]"
    assert notice == (
        "[Some lines truncated to 500 chars. Use read tool to see full lines]"
    )


def test_grep_limit_notice(tmp_path: Path, engine: str):
    _write(tmp_path, "many.txt", "needle\n" * 50)

    output = _grep(tmp_path, pattern="needle", limit=5)
    rows, notice = output.split("\n\n")
    assert rows.splitlines() == [f"many.txt:{n}: needle" for n in range(1, 6)]
    assert notice == (
        "[5 matches limit reached. Use limit=10 for more, or refine pattern]"
    )
    # Pi: Math.max(1, limit).
    assert _grep(tmp_path, pattern="needle", limit=0).endswith(
        "[1 matches limit reached. Use limit=2 for more, or refine pattern]"
    )


def test_grep_reports_the_limit_when_the_count_reaches_it(tmp_path: Path, engine: str):
    _write(tmp_path, "a.txt", "needle\nneedle\n")

    # Pi stops rg at the limit and reports it even when nothing follows.
    assert _grep(tmp_path, pattern="needle", limit=2).endswith(
        "[2 matches limit reached. Use limit=4 for more, or refine pattern]"
    )
    assert (
        _grep(tmp_path, pattern="needle", limit=3) == "a.txt:1: needle\na.txt:2: needle"
    )


def test_grep_byte_cap_notice(tmp_path: Path, engine: str):
    _write(tmp_path, "a.txt", ("needle " + "y" * 400 + "\n") * 150)

    output = _grep(tmp_path, pattern="needle", limit=200)
    body, notice = output.rsplit("\n\n", 1)
    assert notice == "[50.0KB limit reached]"
    assert len(body.encode()) <= 50 * 1024


def test_grep_git_matches_do_not_count_toward_the_limit(tmp_path: Path, engine: str):
    (tmp_path / ".git").mkdir()
    for n in range(5):
        (tmp_path / ".git" / f"f{n}").write_text("needle\n", encoding="utf-8")
    _write(tmp_path, "z.txt", "needle\n")

    assert _grep(tmp_path, pattern="needle", limit=1).startswith("z.txt:1: needle")


def test_grep_skips_binary_files(tmp_path: Path, engine: str):
    (tmp_path / "bin.dat").write_bytes(b"needle\x00\x01")
    _write(tmp_path, "a.txt", "needle\n")

    assert _grep(tmp_path, pattern="needle") == "a.txt:1: needle"


def test_grep_no_matches(tmp_path: Path, engine: str):
    _write(tmp_path, "a.txt", "alpha\n")

    assert _grep(tmp_path, pattern="NEVER") == "No matches found"


def test_grep_tool_refuses_path_under_dot_git(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("NEEDLE_HERE\n", encoding="utf-8")
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


def test_grep_skips_outside_workspace_symlink(tmp_path: Path, engine: str):
    """A workspace symlink that resolves outside the workspace is not searched."""

    outside = tmp_path.parent / f"outside_for_grep_{tmp_path.name}"
    outside.mkdir(exist_ok=True)
    (outside / "marker.txt").write_text("OUTSIDE_HIT\n", encoding="utf-8")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "inside.txt").write_text("INSIDE_HIT\n", encoding="utf-8")
    (workspace / "outside_link").symlink_to(outside, target_is_directory=True)

    assert _grep(workspace, pattern="HIT") == "inside.txt:1: INSIDE_HIT"


def test_grep_fallback_backtracking_regex_is_cancellable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # Python `re` cannot be interrupted in-process; the fallback runs as a
    # child process that the tool kills on cancellation.
    monkeypatch.setattr(
        "pipy_harness.native.tools.grep.shutil.which", lambda _name: None
    )
    _write(tmp_path, "a.txt", "a" * 64 + "b\n")
    cancel = threading.Event()
    timer = threading.Timer(0.5, cancel.set)
    timer.start()
    started = time.monotonic()
    result = GrepTool().invoke(
        _make_request({"pattern": "(a+)+$"}),
        ToolContext(workspace_root=tmp_path, cancel_event=cancel),
    )
    timer.cancel()
    assert time.monotonic() - started < 5
    assert result.is_error is True
    assert result.output_text == "grep error: Operation aborted"


def test_grep_fallback_stops_a_file_at_a_late_nul_byte(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(
        "pipy_harness.native.tools.grep.shutil.which", lambda _name: None
    )
    body = b"needle 1\n" + b"x" * (70 * 1024) + b"\n\x00\nneedle 2\n"
    (tmp_path / "a.txt").write_bytes(body)

    assert _grep(tmp_path, pattern="needle") == "a.txt:1: needle 1"
