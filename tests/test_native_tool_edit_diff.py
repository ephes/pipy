"""Tests for the `edit_diff` tool.

Hermetic, stdlib-only tests covering schema, success, and refusal paths
for the unified-diff-driven workspace edit tool.
"""

from __future__ import annotations

import io
import stat
from pathlib import Path

import pytest

from pipy_harness.native.tools import (
    ToolArgumentError,
    ToolContext,
    ToolPort,
    ToolRequest,
    make_tool_request_id,
)
from pipy_harness.native.tools.edit_diff import EditDiffTool


def _make_request(arguments: dict[str, object]) -> ToolRequest:
    return ToolRequest(
        tool_request_id=make_tool_request_id(),
        tool_name="edit_diff",
        arguments=arguments,
    )


def _stderr_capture(buffer: io.StringIO):
    def _sink(text: str) -> None:
        buffer.write(text)

    return _sink


def test_edit_diff_tool_satisfies_tool_port_protocol():
    tool = EditDiffTool()

    assert isinstance(tool, ToolPort)


def test_edit_diff_definition_requires_path_and_unified_diff():
    tool = EditDiffTool()

    schema = tool.definition.input_schema

    assert tool.definition.name == "edit_diff"
    assert schema["type"] == "object"
    assert set(schema["required"]) == {"path", "unified_diff"}
    assert schema["additionalProperties"] is False
    assert schema["properties"]["path"]["type"] == "string"
    assert schema["properties"]["unified_diff"]["type"] == "string"


def test_applies_simple_single_hunk_diff(tmp_path: Path):
    target = tmp_path / "config.py"
    target.write_text(
        "alpha\nbeta\ngamma\ndelta\n", encoding="utf-8"
    )
    diff = (
        "--- a/config.py\n"
        "+++ b/config.py\n"
        "@@ -1,4 +1,4 @@\n"
        " alpha\n"
        "-beta\n"
        "+BETA\n"
        " gamma\n"
        " delta\n"
    )
    tool = EditDiffTool()
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request({"path": "config.py", "unified_diff": diff})

    result = tool.invoke(request, context)

    assert result.is_error is False
    assert target.read_text(encoding="utf-8") == (
        "alpha\nBETA\ngamma\ndelta\n"
    )
    assert "applied 1 hunk(s)" in result.output_text
    assert "(1+ / 1-)" in result.output_text
    assert "config.py" in result.output_text


def test_applies_multi_hunk_diff(tmp_path: Path):
    target = tmp_path / "multi.txt"
    target.write_text(
        "one\ntwo\nthree\nfour\nfive\nsix\nseven\neight\n",
        encoding="utf-8",
    )
    diff = (
        "--- a/multi.txt\n"
        "+++ b/multi.txt\n"
        "@@ -1,3 +1,3 @@\n"
        " one\n"
        "-two\n"
        "+TWO\n"
        " three\n"
        "@@ -6,3 +6,3 @@\n"
        " six\n"
        "-seven\n"
        "+SEVEN\n"
        " eight\n"
    )
    tool = EditDiffTool()
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request({"path": "multi.txt", "unified_diff": diff})

    result = tool.invoke(request, context)

    assert result.is_error is False
    assert target.read_text(encoding="utf-8") == (
        "one\nTWO\nthree\nfour\nfive\nsix\nSEVEN\neight\n"
    )
    assert "applied 2 hunk(s)" in result.output_text
    assert "(2+ / 2-)" in result.output_text


def test_refuses_path_under_dot_git(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("x\n", encoding="utf-8")
    diff = (
        "--- a/.git/config\n"
        "+++ b/.git/config\n"
        "@@ -1,1 +1,1 @@\n"
        "-x\n"
        "+y\n"
    )
    tool = EditDiffTool()
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request({"path": ".git/config", "unified_diff": diff})

    result = tool.invoke(request, context)

    assert result.is_error is True
    assert "ignored or under .git" in result.output_text
    assert (tmp_path / ".git" / "config").read_text(encoding="utf-8") == "x\n"


def test_refuses_absolute_path(tmp_path: Path):
    diff = (
        "--- a//etc/passwd\n"
        "+++ b//etc/passwd\n"
        "@@ -1,1 +1,1 @@\n"
        "-x\n"
        "+y\n"
    )
    tool = EditDiffTool()
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request({"path": "/etc/passwd", "unified_diff": diff})

    with pytest.raises(ToolArgumentError):
        tool.invoke(request, context)


def test_refuses_missing_file(tmp_path: Path):
    diff = (
        "--- a/missing.py\n"
        "+++ b/missing.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-x\n"
        "+y\n"
    )
    tool = EditDiffTool()
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request({"path": "missing.py", "unified_diff": diff})

    result = tool.invoke(request, context)

    assert result.is_error is True
    assert "does not exist" in result.output_text


def test_refuses_oversized_file_before_read(tmp_path: Path):
    target = tmp_path / "large.py"
    target.write_bytes(b"x" * 65)
    diff = (
        "--- a/large.py\n"
        "+++ b/large.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-x\n"
        "+y\n"
    )
    tool = EditDiffTool(max_content_bytes=64)
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request({"path": "large.py", "unified_diff": diff})

    result = tool.invoke(request, context)

    assert result.is_error is True
    assert "max_content_bytes" in result.output_text
    assert target.read_bytes() == b"x" * 65


def test_refuses_context_mismatch(tmp_path: Path):
    target = tmp_path / "mismatch.py"
    original = "alpha\nbeta\ngamma\n"
    target.write_text(original, encoding="utf-8")
    diff = (
        "--- a/mismatch.py\n"
        "+++ b/mismatch.py\n"
        "@@ -1,3 +1,3 @@\n"
        " alpha\n"
        "-NOT_THE_REAL_LINE\n"
        "+replacement\n"
        " gamma\n"
    )
    tool = EditDiffTool()
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request({"path": "mismatch.py", "unified_diff": diff})

    result = tool.invoke(request, context)

    assert result.is_error is True
    assert "hunk failed to apply" in result.output_text
    assert target.read_text(encoding="utf-8") == original


def test_refuses_malformed_diff_missing_plus_header(tmp_path: Path):
    target = tmp_path / "f.py"
    target.write_text("x\n", encoding="utf-8")
    diff = (
        "--- a/f.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-x\n"
        "+y\n"
    )
    tool = EditDiffTool()
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request({"path": "f.py", "unified_diff": diff})

    result = tool.invoke(request, context)

    assert result.is_error is True
    assert "malformed diff" in result.output_text
    assert target.read_text(encoding="utf-8") == "x\n"


def test_refuses_malformed_diff_invalid_hunk_header(tmp_path: Path):
    target = tmp_path / "f.py"
    target.write_text("x\n", encoding="utf-8")
    diff = (
        "--- a/f.py\n"
        "+++ b/f.py\n"
        "@@ not-a-valid-header @@\n"
        "-x\n"
        "+y\n"
    )
    tool = EditDiffTool()
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request({"path": "f.py", "unified_diff": diff})

    result = tool.invoke(request, context)

    assert result.is_error is True
    assert "malformed diff" in result.output_text
    assert target.read_text(encoding="utf-8") == "x\n"


def test_writes_unified_diff_to_stderr_sink(tmp_path: Path):
    target = tmp_path / "stream.py"
    target.write_text("hello\nworld\n", encoding="utf-8")
    diff = (
        "--- a/stream.py\n"
        "+++ b/stream.py\n"
        "@@ -1,2 +1,2 @@\n"
        " hello\n"
        "-world\n"
        "+WORLD\n"
    )
    buffer = io.StringIO()
    tool = EditDiffTool()
    context = ToolContext(
        workspace_root=tmp_path,
        stderr_sink=_stderr_capture(buffer),
    )
    request = _make_request({"path": "stream.py", "unified_diff": diff})

    result = tool.invoke(request, context)

    assert result.is_error is False
    streamed = buffer.getvalue()
    assert streamed != ""
    assert "--- a/stream.py" in streamed
    assert "+++ b/stream.py" in streamed
    assert "-world" in streamed
    assert "+WORLD" in streamed


def test_atomic_write_preserves_existing_file_mode(tmp_path: Path):
    target = tmp_path / "script.sh"
    target.write_text("#!/bin/sh\necho old\n", encoding="utf-8")
    target.chmod(0o755)
    diff = (
        "--- a/script.sh\n"
        "+++ b/script.sh\n"
        "@@ -1,2 +1,2 @@\n"
        " #!/bin/sh\n"
        "-echo old\n"
        "+echo new\n"
    )
    tool = EditDiffTool()
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request({"path": "script.sh", "unified_diff": diff})

    result = tool.invoke(request, context)

    assert result.is_error is False
    assert stat.S_IMODE(target.stat().st_mode) == 0o755


def test_atomic_failure_does_not_partially_write(tmp_path: Path):
    target = tmp_path / "atomic.txt"
    original = "one\ntwo\nthree\nfour\nfive\nsix\nseven\neight\n"
    target.write_text(original, encoding="utf-8")
    # First hunk would apply cleanly; second hunk has bad context, so the
    # whole patch must fail before any bytes are written.
    diff = (
        "--- a/atomic.txt\n"
        "+++ b/atomic.txt\n"
        "@@ -1,3 +1,3 @@\n"
        " one\n"
        "-two\n"
        "+TWO\n"
        " three\n"
        "@@ -6,3 +6,3 @@\n"
        " six\n"
        "-NOT_SEVEN\n"
        "+SEVEN\n"
        " eight\n"
    )
    tool = EditDiffTool()
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request({"path": "atomic.txt", "unified_diff": diff})

    result = tool.invoke(request, context)

    assert result.is_error is True
    assert "hunk failed to apply" in result.output_text
    assert target.read_text(encoding="utf-8") == original
    # No leftover temp files from the failed apply.
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.startswith(".edit_diff.")]
    assert leftovers == []


def test_edit_diff_tool_source_does_not_import_recorder():
    source = (
        Path(__file__).parents[1]
        / "src/pipy_harness/native/tools/edit_diff.py"
    ).read_text(encoding="utf-8")

    assert "import pipy_session" not in source
    assert "from pipy_session" not in source
