"""Dependency gates for the headless single-run agent loop."""

from __future__ import annotations

import ast
from pathlib import Path
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPO_ROOT / "src"
LOOP_MODULE = "pipy_harness.native.agent.loop"
LOOP_PATH = SOURCE_ROOT / "pipy_harness/native/agent/loop.py"
TOOL_LOOP_SESSION_PATH = SOURCE_ROOT / "pipy_harness/native/tool_loop_session.py"
RPC_PATH = SOURCE_ROOT / "pipy_harness/native/automation/rpc.py"

_FORBIDDEN_PREFIXES = (
    "pipy_harness.capture",
    "pipy_session",
    "pipy_harness.adapters",
    "pipy_harness.cli",
    "pipy_harness.runner",
    "pipy_harness.native.agent_adapters",
    "pipy_harness.native.agent_loop_policy",
    "pipy_harness.native.automation",
    "pipy_harness.native.coding",
    "pipy_harness.native.extension_runtime",
    "pipy_harness.native.extensions",
    "pipy_harness.native.tool_loop_session",
    "pipy_harness.native.session",
    "pipy_harness.native.session_resume",
    "pipy_harness.native.session_tree",
    "pipy_harness.native.session_tree_commands",
    "pipy_harness.native.persistence",
    "pipy_harness.native.prompt_history",
    "pipy_harness.native.provider_construction",
    "pipy_harness.native.provider_registry",
    "pipy_harness.native.providers",
    "pipy_harness.native._provider_helpers",
    "pipy_harness.native.deferred_tools",
    "pipy_harness.native.fake",
    "pipy_harness.native.ui",
    "pipy_harness.native.tui",
    "pipy_harness.native.chrome",
    "pipy_harness.native.tool_renderers",
    "pipy_harness.native.themes",
    "pipy_harness.native.theme_files",
    "pipy_harness.native.terminal_compare",
    "pipy_harness.native.terminal_input",
    "pipy_harness.native.terminal_screen",
    "pipy_harness.native.anthropic_provider",
    "pipy_harness.native.azure_openai_provider",
    "pipy_harness.native.bedrock_provider",
    "pipy_harness.native.cloudflare_provider",
    "pipy_harness.native.ds4_provider",
    "pipy_harness.native.google_provider",
    "pipy_harness.native.google_vertex_provider",
    "pipy_harness.native.mistral_provider",
    "pipy_harness.native.openai_codex_provider",
    "pipy_harness.native.openai_completions_provider",
    "pipy_harness.native.openai_provider",
    "pipy_harness.native.openrouter_provider",
    "pipy_harness.native.tools.bash",
    "pipy_harness.native.tools.edit",
    "pipy_harness.native.tools.edit_diff",
    "pipy_harness.native.tools.find",
    "pipy_harness.native.tools.grep",
    "pipy_harness.native.tools.ls",
    "pipy_harness.native.tools.read",
    "pipy_harness.native.tools.truncate",
    "pipy_harness.native.tools.write",
)


def _import_references(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    references: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            references.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            references.append(node.module)
            references.extend(f"{node.module}.{alias.name}" for alias in node.names)
    return tuple(references)


class _RuntimeImportVisitor(ast.NodeVisitor):
    """Collect initialization imports while excluding deferred scopes."""

    def __init__(self) -> None:
        self.references: list[str] = []

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        self.references.extend(alias.name for alias in node.names)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        if node.module is None:
            return
        self.references.append(node.module)
        self.references.extend(f"{node.module}.{alias.name}" for alias in node.names)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        del node

    def visit_AsyncFunctionDef(  # noqa: N802
        self, node: ast.AsyncFunctionDef
    ) -> None:
        del node

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        del node

    def visit_If(self, node: ast.If) -> None:  # noqa: N802
        if isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING":
            for statement in node.orelse:
                self.visit(statement)
            return
        self.generic_visit(node)


def _runtime_import_references(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    visitor = _RuntimeImportVisitor()
    visitor.visit(tree)
    return tuple(visitor.references)


def _module_path(module: str) -> Path | None:
    relative = Path(*module.split("."))
    module_path = SOURCE_ROOT / relative.with_suffix(".py")
    if module_path.is_file():
        return module_path
    package_path = SOURCE_ROOT / relative / "__init__.py"
    return package_path if package_path.is_file() else None


def _runtime_dependency_closure(module: str) -> tuple[tuple[str, str], ...]:
    pending = [module]
    visited: set[str] = set()
    edges: list[tuple[str, str]] = []
    while pending:
        current = pending.pop()
        if current in visited:
            continue
        visited.add(current)
        path = _module_path(current)
        assert path is not None, current
        for imported in _runtime_import_references(path):
            edges.append((current, imported))
            if not imported.startswith("pipy_harness."):
                continue
            candidate = imported
            while _module_path(candidate) is None and "." in candidate:
                candidate = candidate.rsplit(".", 1)[0]
            if candidate not in visited and _module_path(candidate) is not None:
                pending.append(candidate)
    return tuple(edges)


def _agent_dependency_closure(module: str) -> tuple[tuple[str, str], ...]:
    """Follow every canonical-agent import, including deferred references."""

    pending = [module]
    visited: set[str] = set()
    edges: list[tuple[str, str]] = []
    while pending:
        current = pending.pop()
        if current in visited:
            continue
        visited.add(current)
        path = _module_path(current)
        assert path is not None, current
        for imported in _import_references(path):
            edges.append((current, imported))
            if not imported.startswith("pipy_harness.native.agent."):
                continue
            candidate = imported
            while _module_path(candidate) is None and "." in candidate:
                candidate = candidate.rsplit(".", 1)[0]
            if candidate not in visited and _module_path(candidate) is not None:
                pending.append(candidate)
    return tuple(edges)


def _is_forbidden(module: str) -> bool:
    return any(
        module == prefix or module.startswith(f"{prefix}.")
        for prefix in _FORBIDDEN_PREFIXES
    )


def test_agent_loop_has_no_direct_product_ui_or_concrete_dependencies() -> None:
    violations = sorted(
        imported
        for imported in _import_references(LOOP_PATH)
        if _is_forbidden(imported)
    )
    assert violations == []


def test_agent_loop_runtime_closure_cannot_launder_product_dependencies() -> None:
    violations = sorted(
        f"{source} -> {imported}"
        for source, imported in _runtime_dependency_closure(LOOP_MODULE)
        if _is_forbidden(imported)
    )
    assert violations == []


def test_agent_loop_agent_closure_cannot_launder_product_dependencies() -> None:
    violations = sorted(
        f"{source} -> {imported}"
        for source, imported in _agent_dependency_closure(LOOP_MODULE)
        if _is_forbidden(imported)
    )
    assert violations == []


def test_agent_loop_imports_in_a_fresh_process_without_product_layers() -> None:
    package_root = SOURCE_ROOT / "pipy_harness"
    native_root = package_root / "native"
    prefixes = repr(_FORBIDDEN_PREFIXES)
    code = f"""
import importlib
import importlib.abc
import sys
import types

PREFIXES = {prefixes}

def namespace_package(name, path):
    module = types.ModuleType(name)
    module.__package__ = name
    module.__path__ = [path]
    sys.modules[name] = module
    return module

pipy_package = namespace_package("pipy_harness", {str(package_root)!r})
native_package = namespace_package("pipy_harness.native", {str(native_root)!r})
pipy_package.native = native_package

class BlockForbidden(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        del path, target
        if any(fullname == prefix or fullname.startswith(prefix + '.') for prefix in PREFIXES):
            raise AssertionError('forbidden import: ' + fullname)
        return None

sys.meta_path.insert(0, BlockForbidden())
loop = importlib.import_module({LOOP_MODULE!r})
assert loop.AgentLoop.__module__ == {LOOP_MODULE!r}
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_agent_loop_is_not_an_eager_package_root_export() -> None:
    package_root = SOURCE_ROOT / "pipy_harness"
    native_root = package_root / "native"
    code = f"""
import importlib
import sys
import types

def namespace_package(name, path):
    module = types.ModuleType(name)
    module.__package__ = name
    module.__path__ = [path]
    sys.modules[name] = module
    return module

pipy_package = namespace_package("pipy_harness", {str(package_root)!r})
native = namespace_package("pipy_harness.native", {str(native_root)!r})
pipy_package.native = native
agent = importlib.import_module("pipy_harness.native.agent")

assert {LOOP_MODULE!r} not in sys.modules
assert not hasattr(agent, 'AgentLoop')
assert not hasattr(native, 'AgentLoop')
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr

    root_code = """
import pipy_harness.native as native
import pipy_harness.native.agent as agent

assert not hasattr(agent, 'AgentLoop')
assert not hasattr(native, 'AgentLoop')
"""
    root_completed = subprocess.run(
        [sys.executable, "-c", root_code],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert root_completed.returncode == 0, root_completed.stderr


def test_product_session_composes_agent_loop_without_inline_policy_cycle() -> None:
    references = set(_import_references(TOOL_LOOP_SESSION_PATH))
    assert "pipy_harness.native.agent.loop.AgentLoop" in references
    assert references.isdisjoint(
        {
            "pipy_harness.native.agent.loop_policy.apply_tool_policy_decision",
            "pipy_harness.native.agent.loop_policy.decide_tool_admission",
            "pipy_harness.native.agent.loop_policy.normalize_provider_status",
            "pipy_harness.native.agent.loop_policy.settle_tool_execution",
        }
    )

    tree = ast.parse(
        TOOL_LOOP_SESSION_PATH.read_text(encoding="utf-8"),
        filename=str(TOOL_LOOP_SESSION_PATH),
    )
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "AgentLoop"
        for node in ast.walk(tree)
    )


def test_queued_input_handoff_has_no_split_kind_side_channel() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (LOOP_PATH, TOOL_LOOP_SESSION_PATH, RPC_PATH)
    )
    for obsolete_symbol in (
        "_QueuedDeliverySource",
        "_last_delivery_kind",
        "take_delivery_kind",
        "delivery_content",
    ):
        assert obsolete_symbol not in source
