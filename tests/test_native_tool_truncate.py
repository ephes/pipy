"""Tests for the `truncate` pure-transformation tool."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from pipy_harness.native.tools import (
    ToolArgumentError,
    ToolContext,
    ToolPort,
    ToolRequest,
    make_tool_request_id,
)
from pipy_harness.native.tools.truncate import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_LINES,
    MAX_MAX_BYTES,
    MAX_MAX_LINES,
    MIN_MAX_BYTES,
    MIN_MAX_LINES,
    TruncateTool,
)


def _make_request(arguments: dict[str, object]) -> ToolRequest:
    return ToolRequest(
        tool_request_id=make_tool_request_id(),
        tool_name="truncate",
        arguments=arguments,
    )


def _context(tmp_path: Path) -> ToolContext:
    return ToolContext(workspace_root=tmp_path)


def test_truncate_tool_satisfies_tool_port_protocol() -> None:
    tool = TruncateTool()

    assert isinstance(tool, ToolPort)


def test_truncate_definition_requires_text_only() -> None:
    tool = TruncateTool()

    schema = tool.definition.input_schema

    assert tool.definition.name == "truncate"
    assert schema["type"] == "object"
    assert schema["required"] == ["text"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["text"]["type"] == "string"
    assert schema["properties"]["max_bytes"]["type"] == "integer"
    assert schema["properties"]["max_bytes"]["minimum"] == MIN_MAX_BYTES
    assert schema["properties"]["max_bytes"]["maximum"] == MAX_MAX_BYTES
    assert schema["properties"]["max_lines"]["type"] == "integer"
    assert schema["properties"]["max_lines"]["minimum"] == MIN_MAX_LINES
    assert schema["properties"]["max_lines"]["maximum"] == MAX_MAX_LINES


def test_short_input_passes_through_unchanged(tmp_path: Path) -> None:
    tool = TruncateTool()
    text = "hello\nworld\n"
    request = _make_request({"text": text})

    result = tool.invoke(request, _context(tmp_path))

    assert result.is_error is False
    assert result.output_text == text


def test_default_caps_pass_through_when_under_limits(tmp_path: Path) -> None:
    tool = TruncateTool()
    # 10 lines, well under default caps
    text = "\n".join(f"line {i}" for i in range(10))
    request = _make_request({"text": text})

    result = tool.invoke(request, _context(tmp_path))

    assert result.output_text == text
    assert result.is_error is False


def test_long_input_is_truncated_with_marker(tmp_path: Path) -> None:
    tool = TruncateTool()
    text = "\n".join(f"line-{i:04d}" for i in range(500))
    request = _make_request({"text": text, "max_lines": 50})

    result = tool.invoke(request, _context(tmp_path))

    assert result.is_error is False
    assert result.output_text != text
    assert "--- omitted" in result.output_text
    assert "lines," in result.output_text
    assert "bytes ---" in result.output_text
    # Confirm head and tail are both present
    assert result.output_text.startswith("line-0000")
    assert result.output_text.rstrip().endswith("line-0499")


def test_marker_reports_exact_omitted_counts(tmp_path: Path) -> None:
    tool = TruncateTool()
    # Build a 20-line text with predictable bytes
    lines = [f"L{i:02d}" for i in range(20)]  # each line is 3 bytes
    text = "\n".join(lines)
    # max_lines=5 -> head=2, tail=2, omitted=20-4=16 lines
    request = _make_request({"text": text, "max_lines": 5})

    result = tool.invoke(request, _context(tmp_path))

    match = re.search(
        r"--- omitted (\d+) lines, (\d+) bytes ---", result.output_text
    )
    assert match is not None, result.output_text
    omitted_lines = int(match.group(1))
    omitted_bytes = int(match.group(2))
    # 20 source lines - 2 head - 2 tail = 16
    assert omitted_lines == 16
    # Bytes elided: middle slice "L02\nL03\n...\nL17" (16 lines, each 3 bytes,
    # 15 internal newlines) = 48 + 15 = 63, plus the two boundary newlines = 65.
    assert omitted_bytes == 65
    # Sanity-check head + tail content
    assert result.output_text.splitlines()[:2] == ["L00", "L01"]
    assert result.output_text.splitlines()[-2:] == ["L18", "L19"]


def test_byte_cap_enforced(tmp_path: Path) -> None:
    tool = TruncateTool()
    # 1000 lines of 50-byte content each -> ~51 KB total
    line = "x" * 50
    text = "\n".join(line for _ in range(1000))
    cap = 2048
    request = _make_request(
        {"text": text, "max_bytes": cap, "max_lines": 5000}
    )

    result = tool.invoke(request, _context(tmp_path))

    assert result.is_error is False
    assert len(result.output_text.encode("utf-8")) <= cap
    assert "--- omitted" in result.output_text


def test_line_cap_enforced(tmp_path: Path) -> None:
    tool = TruncateTool()
    text = "\n".join(f"row{i}" for i in range(300))
    request = _make_request(
        {"text": text, "max_lines": 20, "max_bytes": MAX_MAX_BYTES}
    )

    result = tool.invoke(request, _context(tmp_path))

    assert result.is_error is False
    # Output line count should be <= max_lines (head + marker + tail)
    assert result.output_text.count("\n") + 1 <= 20
    assert "--- omitted" in result.output_text


def test_rejects_non_string_text(tmp_path: Path) -> None:
    tool = TruncateTool()
    request = _make_request({"text": 42})

    with pytest.raises(ToolArgumentError) as excinfo:
        tool.invoke(request, _context(tmp_path))

    assert "text" in str(excinfo.value)


def test_rejects_missing_text(tmp_path: Path) -> None:
    tool = TruncateTool()
    request = _make_request({})

    with pytest.raises(ToolArgumentError) as excinfo:
        tool.invoke(request, _context(tmp_path))

    assert "text" in str(excinfo.value)


def test_rejects_out_of_range_max_bytes(tmp_path: Path) -> None:
    tool = TruncateTool()
    with pytest.raises(ToolArgumentError):
        tool.invoke(
            _make_request({"text": "x", "max_bytes": 0}),
            _context(tmp_path),
        )
    with pytest.raises(ToolArgumentError):
        tool.invoke(
            _make_request({"text": "x", "max_bytes": MAX_MAX_BYTES + 1}),
            _context(tmp_path),
        )
    with pytest.raises(ToolArgumentError):
        tool.invoke(
            _make_request({"text": "x", "max_bytes": "1024"}),
            _context(tmp_path),
        )
    with pytest.raises(ToolArgumentError):
        tool.invoke(
            _make_request({"text": "x", "max_bytes": True}),
            _context(tmp_path),
        )


def test_rejects_out_of_range_max_lines(tmp_path: Path) -> None:
    tool = TruncateTool()
    with pytest.raises(ToolArgumentError):
        tool.invoke(
            _make_request({"text": "x", "max_lines": 0}),
            _context(tmp_path),
        )
    with pytest.raises(ToolArgumentError):
        tool.invoke(
            _make_request({"text": "x", "max_lines": MAX_MAX_LINES + 1}),
            _context(tmp_path),
        )
    with pytest.raises(ToolArgumentError):
        tool.invoke(
            _make_request({"text": "x", "max_lines": -5}),
            _context(tmp_path),
        )


def test_truncation_is_deterministic(tmp_path: Path) -> None:
    tool = TruncateTool()
    text = "\n".join(f"item-{i:05d}" for i in range(2000))
    args = {"text": text, "max_bytes": 1500, "max_lines": 80}

    first = tool.invoke(_make_request(args), _context(tmp_path))
    second = tool.invoke(_make_request(args), _context(tmp_path))

    assert first.output_text == second.output_text


def test_byte_cap_dominates_when_lines_fit(tmp_path: Path) -> None:
    """If line count fits but bytes do not, truncation still happens."""

    tool = TruncateTool()
    # 5 lines, very long -> easily under default max_lines but over byte cap
    text = "\n".join("x" * 2000 for _ in range(5))
    request = _make_request({"text": text, "max_bytes": 256})

    result = tool.invoke(request, _context(tmp_path))

    assert result.is_error is False
    assert len(result.output_text.encode("utf-8")) <= 256
    assert "--- omitted" in result.output_text


def test_default_bounds_round_trip(tmp_path: Path) -> None:
    """Omitting both caps uses the published defaults."""

    tool = TruncateTool()
    text = "\n".join("a" for _ in range(DEFAULT_MAX_LINES + 50))
    request = _make_request({"text": text})

    result = tool.invoke(request, _context(tmp_path))

    assert result.is_error is False
    # Confirm we actually truncated using DEFAULT_MAX_LINES
    assert result.output_text.count("\n") + 1 <= DEFAULT_MAX_LINES
    assert "--- omitted" in result.output_text
    assert len(result.output_text.encode("utf-8")) <= DEFAULT_MAX_BYTES
