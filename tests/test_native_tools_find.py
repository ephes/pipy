"""Tests for the `find` tool, which follows Pi's `find` (TOOLS1).

Pi runs `fd --glob --hidden`; pipy walks in Python with fd's observable
rules, measured on fd 10.5.0 (see the module docstring).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pipy_harness.native.tools import (
    ToolArgumentError,
    ToolContext,
    ToolPort,
    ToolRequest,
    make_tool_request_id,
)
from pipy_harness.native.tools.find import FindTool


def _make_request(arguments: dict[str, object]) -> ToolRequest:
    return ToolRequest(
        tool_request_id=make_tool_request_id(),
        tool_name="find",
        arguments=arguments,
    )


def test_find_tool_satisfies_tool_port_protocol():
    tool = FindTool()

    assert isinstance(tool, ToolPort)


def test_find_tool_schema_and_description_match_pi():
    definition = FindTool().definition

    assert definition.description == (
        "Search for files by glob pattern. Returns matching file paths relative "
        "to the search directory. Respects .gitignore. Output is truncated to "
        "1000 results or 50KB (whichever is hit first)."
    )
    schema = definition.input_schema
    assert schema["type"] == "object"
    assert schema["required"] == ["pattern"]
    assert schema["properties"] == {
        "pattern": {
            "type": "string",
            "description": (
                "Glob pattern to match files, e.g. '*.ts', '**/*.json', or "
                "'src/**/*.spec.ts'"
            ),
        },
        "path": {
            "type": "string",
            "description": "Directory to search in (default: current directory)",
        },
        "limit": {
            "type": "integer",
            "description": "Maximum number of results (default: 1000)",
        },
    }
    assert schema["additionalProperties"] is False


def _tree(root: Path) -> ToolContext:
    for relative in (
        "README.md",
        "readme.txt",
        "Foo.TS",
        ".hid/h.ts",
        "src/x.ts",
        "src/a/y.ts",
        "src/a/b/z.spec.ts",
        "pkg/src/w.ts",
    ):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("", encoding="utf-8")
    return ToolContext(workspace_root=root)


def _find(context: ToolContext, **arguments: object) -> str:
    result = FindTool().invoke(_make_request(arguments), context)
    assert result.is_error is False, result.output_text
    return result.output_text


def test_find_basename_pattern_matches_at_any_depth_with_smart_case(tmp_path: Path):
    context = _tree(tmp_path)

    # No uppercase letter: case-insensitive, so Foo.TS matches; hidden
    # directories are searched (fd --hidden).
    assert _find(context, pattern="*.ts").splitlines() == [
        "Foo.TS",
        ".hid/h.ts",
        "pkg/src/w.ts",
        "src/x.ts",
        "src/a/y.ts",
        "src/a/b/z.spec.ts",
    ]
    # An uppercase letter makes the pattern case-sensitive.
    assert _find(context, pattern="*.TS") == "Foo.TS"
    assert _find(context, pattern="readme*").splitlines() == ["README.md", "readme.txt"]


def test_find_path_pattern_gets_a_leading_double_star(tmp_path: Path):
    context = _tree(tmp_path)

    # `src/*.ts` becomes `**/src/*.ts`: any `src` directory, `*` not crossing `/`.
    assert _find(context, pattern="src/*.ts").splitlines() == [
        "pkg/src/w.ts",
        "src/x.ts",
    ]
    assert _find(context, pattern="src/**/*.spec.ts") == "src/a/b/z.spec.ts"


def test_find_lists_directories_with_a_trailing_slash(tmp_path: Path):
    context = _tree(tmp_path)

    assert _find(context, pattern="a") == "src/a/"
    assert _find(context, pattern="src/**").splitlines() == [
        "pkg/src/w.ts",
        "src/a/",
        "src/x.ts",
        "src/a/b/",
        "src/a/y.ts",
        "src/a/b/z.spec.ts",
    ]


def test_find_braces_and_relative_search_path(tmp_path: Path):
    context = _tree(tmp_path)

    assert _find(context, pattern="{x,y}.ts", path="src").splitlines() == [
        "x.ts",
        "a/y.ts",
    ]


def test_find_limit_notice_and_early_stop(tmp_path: Path):
    context = _tree(tmp_path)

    assert _find(context, pattern="*.ts", limit=2) == (
        "Foo.TS\n.hid/h.ts\n\n[2 results limit reached. Use limit=4 for more, "
        "or refine pattern]"
    )


def test_find_limit_zero_is_unlimited_with_pi_notice(tmp_path: Path):
    context = _tree(tmp_path)

    output = _find(context, pattern="*.ts", limit=0)
    body, notice = output.rsplit("\n\n", 1)
    assert len(body.splitlines()) == 6
    assert (
        notice == "[0 results limit reached. Use limit=0 for more, or refine pattern]"
    )


def test_find_negative_limit_is_an_error(tmp_path: Path):
    result = FindTool().invoke(
        _make_request({"pattern": "*", "limit": -1}),
        ToolContext(workspace_root=tmp_path),
    )
    assert result.is_error is True
    assert result.output_text == "find error: invalid limit -1"


def test_find_invalid_glob_is_an_error(tmp_path: Path):
    result = FindTool().invoke(
        _make_request({"pattern": "["}), ToolContext(workspace_root=tmp_path)
    )
    assert result.is_error is True
    assert result.output_text == (
        "find error: error parsing glob '[': unclosed character class; missing ']'"
    )


def test_find_byte_cap_notice(tmp_path: Path):
    for i in range(520):
        (tmp_path / f"{i:04d}{'x' * 100}.txt").write_text("", encoding="utf-8")

    output = _find(ToolContext(workspace_root=tmp_path), pattern="*.txt")
    body, notice = output.rsplit("\n\n", 1)
    assert notice == "[50.0KB limit reached]"
    assert len(body.encode()) <= 50 * 1024


def test_find_tool_matches_simple_glob(tmp_path: Path):
    (tmp_path / "a.py").write_text("", encoding="utf-8")
    (tmp_path / "b.py").write_text("", encoding="utf-8")
    (tmp_path / "c.txt").write_text("", encoding="utf-8")
    tool = FindTool()
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request({"pattern": "*.py"})

    result = tool.invoke(request, context)

    assert result.is_error is False
    assert "a.py" in result.output_text
    assert "b.py" in result.output_text
    assert "c.txt" not in result.output_text


def test_find_tool_matches_recursive_glob(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "nested.py").write_text("", encoding="utf-8")
    tool = FindTool()
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request({"pattern": "**/*.py"})

    result = tool.invoke(request, context)

    assert result.is_error is False
    assert result.output_text == "src/nested.py"


def test_find_tool_skips_dot_git(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "hidden.py").write_text("", encoding="utf-8")
    (tmp_path / "visible.py").write_text("", encoding="utf-8")
    tool = FindTool()
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request({"pattern": "**/*.py"})

    result = tool.invoke(request, context)

    assert result.is_error is False
    assert "visible.py" in result.output_text
    assert ".git" not in result.output_text


def test_find_tool_projects_sorted_reference_root_results(tmp_path: Path):
    workspace = tmp_path / "workspace"
    reference = tmp_path / "reference"
    outside = tmp_path / "outside"
    workspace.mkdir()
    reference.mkdir()
    outside.mkdir()
    (reference / "b.py").write_text("", encoding="utf-8")
    (reference / "a.py").write_text("", encoding="utf-8")
    (reference / ".git").mkdir()
    (reference / ".git" / "hidden.py").write_text("", encoding="utf-8")
    (outside / "leaked.py").write_text("", encoding="utf-8")
    (reference / "leak.py").symlink_to(outside / "leaked.py")
    context = ToolContext(
        workspace_root=workspace,
        reference_roots=(reference,),
    )

    result = FindTool().invoke(
        _make_request({"pattern": "**/*.py", "path": str(reference)}),
        context,
    )

    assert result.is_error is False
    # Relative to the search directory, as in Pi; the symlink out of the
    # reference root and `.git` stay hidden (pipy path policy, READ2).
    assert result.output_text == "a.py\nb.py"


def test_find_tool_validates_pattern_before_search_root(tmp_path: Path):
    request = _make_request({"pattern": "../*.py", "path": "/outside"})

    with pytest.raises(ToolArgumentError) as info:
        FindTool().invoke(request, ToolContext(workspace_root=tmp_path))

    assert info.value.field_path == ("pattern",)
    assert str(info.value) == "find.pattern: pattern must not contain '..'"


def test_find_tool_rejects_absolute_pattern():
    tool = FindTool()
    context = ToolContext(workspace_root=Path("/tmp").resolve())
    request = _make_request({"pattern": "/etc/*"})

    with pytest.raises(ToolArgumentError):
        tool.invoke(request, context)


def test_find_tool_rejects_parent_traversal_in_pattern(tmp_path: Path):
    tool = FindTool()
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request({"pattern": "../*.py"})

    with pytest.raises(ToolArgumentError):
        tool.invoke(request, context)


def test_find_tool_rejects_unsafe_search_root(tmp_path: Path):
    tool = FindTool()
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request({"pattern": "*.py", "path": "/etc"})

    with pytest.raises(ToolArgumentError):
        tool.invoke(request, context)


def test_find_tool_no_matches_reports_safely(tmp_path: Path):
    tool = FindTool()
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request({"pattern": "*.nonexistent"})

    result = tool.invoke(request, context)

    assert result.is_error is False
    assert result.output_text == "No files found matching pattern"


def test_find_tool_search_root_must_be_directory(tmp_path: Path):
    (tmp_path / "file.txt").write_text("", encoding="utf-8")
    tool = FindTool()
    context = ToolContext(workspace_root=tmp_path)
    request = _make_request({"pattern": "*", "path": "file.txt"})

    result = tool.invoke(request, context)

    assert result.is_error is True
    assert "not a directory" in result.output_text


def test_production_tool_registry_includes_find():
    from pipy_harness.native import production_tool_registry

    registry = production_tool_registry()

    assert "find" in registry
