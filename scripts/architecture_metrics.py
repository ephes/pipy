#!/usr/bin/env python3
"""Report deterministic repository architecture metrics without product imports."""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
import re
import subprocess
from typing import TypeAlias


JsonValue: TypeAlias = (
    None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
)


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_LOOP_SESSION = Path("src/pipy_harness/native/tool_loop_session.py")
TUI = Path("src/pipy_harness/native/tui.py")
TYPE_IGNORE_RE = re.compile(r"#\s*type:\s*ignore\b")
RUFF_C901_COMMAND = (
    "ruff",
    "check",
    ".",
    "--select",
    "C901",
    "--output-format",
    "json",
    "--no-cache",
    "--exit-zero",
    "--config",
    "lint.per-file-ignores = {}",
)


def _physical_lines(path: Path) -> int:
    """Count newline-delimited physical lines, matching the recorded ``wc -l`` baseline."""

    return path.read_bytes().count(b"\n")


def _python_physical_lines(root: Path) -> int:
    return sum(_physical_lines(path) for path in sorted(root.rglob("*.py")))


def _annotation_contains_classvar(annotation: ast.expr) -> bool:
    if isinstance(annotation, ast.Name):
        return annotation.id == "ClassVar"
    if isinstance(annotation, ast.Attribute):
        return annotation.attr == "ClassVar"
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        try:
            parsed = ast.parse(annotation.value, mode="eval")
        except SyntaxError:
            return False
        return _annotation_contains_classvar(parsed.body)
    return any(
        _annotation_contains_classvar(child)
        for child in ast.iter_child_nodes(annotation)
        if isinstance(child, ast.expr)
    )


class _SelfStateCollector(ast.NodeVisitor):
    """Collect ``self.<name>`` stores/deletes without entering nested scopes."""

    def __init__(self) -> None:
        self.names: set[str] = set()

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if (
            isinstance(node.value, ast.Name)
            and node.value.id == "self"
            and isinstance(node.ctx, (ast.Store, ast.Del))
        ):
            self.names.add(node.attr)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return


def class_state_fields(source: str, class_name: str) -> tuple[str, ...]:
    """Return the plan-defined state fields for one top-level class.

    Fields are unique class-body annotated names plus attributes assigned or
    deleted as ``self.<name>`` in direct methods. ``ClassVar`` declarations and
    assignments in nested function/class scopes are excluded.
    """

    tree = ast.parse(source)
    classes = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == class_name
    ]
    if len(classes) != 1:
        raise ValueError(f"expected exactly one top-level class named {class_name!r}")

    class_node = classes[0]
    names: set[str] = set()
    for statement in class_node.body:
        if (
            isinstance(statement, ast.AnnAssign)
            and isinstance(statement.target, ast.Name)
            and not _annotation_contains_classvar(statement.annotation)
        ):
            names.add(statement.target.id)
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            collector = _SelfStateCollector()
            for method_statement in statement.body:
                collector.visit(method_statement)
            names.update(collector.names)
    return tuple(sorted(names))


def _ruff_c901_counts(repo_root: Path) -> dict[str, JsonValue]:
    completed = subprocess.run(
        RUFF_C901_COMMAND,
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    findings: object = json.loads(completed.stdout)
    if not isinstance(findings, list):
        raise RuntimeError("Ruff C901 output was not a JSON list")

    src_root = (repo_root / "src").resolve()
    src_count = 0
    for finding in findings:
        if not isinstance(finding, dict) or not isinstance(finding.get("filename"), str):
            raise RuntimeError("Ruff C901 output contained an invalid finding")
        finding_path = Path(finding["filename"])
        if not finding_path.is_absolute():
            finding_path = repo_root / finding_path
        if finding_path.resolve().is_relative_to(src_root):
            src_count += 1
    return {
        "command": list(RUFF_C901_COMMAND),
        "repository": len(findings),
        "src": src_count,
    }


def collect_metrics(repo_root: Path = REPO_ROOT) -> dict[str, JsonValue]:
    """Collect all metrics from source text and Ruff's static checker."""

    repo_root = repo_root.resolve()
    src_root = repo_root / "src"
    tests_root = repo_root / "tests"
    tool_loop_path = repo_root / TOOL_LOOP_SESSION
    tui_path = repo_root / TUI
    tui_fields = class_state_fields(
        tui_path.read_text(encoding="utf-8"), "ToolLoopTerminalUi"
    )
    type_ignore_count = sum(
        len(TYPE_IGNORE_RE.findall(path.read_text(encoding="utf-8")))
        for path in sorted(src_root.rglob("*.py"))
    )

    return {
        "c901": _ruff_c901_counts(repo_root),
        "physical_lines": {
            "src_python": _python_physical_lines(src_root),
            "tests_python": _python_physical_lines(tests_root),
            "tool_loop_session": _physical_lines(tool_loop_path),
            "tui": _physical_lines(tui_path),
        },
        "src_type_ignores": type_ignore_count,
        "tool_loop_terminal_ui_state_fields": {
            "count": len(tui_fields),
            "names": list(tui_fields),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit sorted JSON")
    args = parser.parse_args()
    if not args.json:
        parser.error("--json is required")
    print(json.dumps(collect_metrics(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
