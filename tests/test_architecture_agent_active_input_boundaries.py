"""Focused dependency gates for the canonical active-input seam."""

from __future__ import annotations

import ast
from pathlib import Path
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPO_ROOT / "src"
ACTIVE_INPUT_MODULE = "pipy_harness.native.agent.active_input"
ACTIVE_INPUT_PATH = SOURCE_ROOT / "pipy_harness/native/agent/active_input.py"
MESSAGES_MODULE = "pipy_harness.native.agent.messages"

_ALLOWED_DIRECT_IMPORTS = frozenset(
    {
        "__future__",
        "__future__.annotations",
        "collections.abc",
        "collections.abc.Sequence",
        "dataclasses",
        "dataclasses.dataclass",
        "dataclasses.replace",
        "pipy_harness.native.agent.content",
        "pipy_harness.native.agent.content.ProductContent",
        "pipy_harness.native.agent.messages",
        "pipy_harness.native.agent.messages.AgentMessage",
        "pipy_harness.native.agent.messages.AgentUserMessage",
    }
)

_FORBIDDEN_PREFIXES = (
    "pipy_harness.adapters",
    "pipy_harness.capture",
    "pipy_harness.cli",
    "pipy_harness.models",
    "pipy_harness.runner",
    "pipy_session",
    "pipy_harness.native._provider_helpers",
    "pipy_harness.native.agent_adapters",
    "pipy_harness.native.agent_request",
    "pipy_harness.native.anthropic_provider",
    "pipy_harness.native.automation",
    "pipy_harness.native.providers.azure_openai_responses",
    "pipy_harness.native.bedrock_provider",
    "pipy_harness.native.chrome",
    "pipy_harness.native.cloudflare_provider",
    "pipy_harness.native.deferred_tools",
    "pipy_harness.native.providers.ds4",
    "pipy_harness.native.extension_runtime",
    "pipy_harness.native.extensions",
    "pipy_harness.native.fake",
    "pipy_harness.native.google_provider",
    "pipy_harness.native.google_vertex_provider",
    "pipy_harness.native.providers.mistral",
    "pipy_harness.native.models",
    "pipy_harness.native.openai_codex_provider",
    "pipy_harness.native.providers.openai_completions",
    "pipy_harness.native.providers.openai_responses",
    "pipy_harness.native.providers.openrouter",
    "pipy_harness.native.provider",
    "pipy_harness.native.provider_construction",
    "pipy_harness.native.provider_registry",
    "pipy_harness.native.providers",
    "pipy_harness.native.session",
    "pipy_harness.native.session_resume",
    "pipy_harness.native.session_tree",
    "pipy_harness.native.session_tree_commands",
    "pipy_harness.native.terminal_compare",
    "pipy_harness.native.terminal_input",
    "pipy_harness.native.terminal_screen",
    "pipy_harness.native.themes",
    "pipy_harness.native.tool_loop_session",
    "pipy_harness.native.tool_renderers",
    "pipy_harness.native.tools",
    "pipy_harness.native.tui",
    "pipy_harness.native.ui",
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


def _module_path(module: str) -> Path | None:
    relative = Path(*module.split("."))
    module_path = SOURCE_ROOT / relative.with_suffix(".py")
    if module_path.is_file():
        return module_path
    package_path = SOURCE_ROOT / relative / "__init__.py"
    return package_path if package_path.is_file() else None


def _agent_dependency_closure(module: str) -> tuple[tuple[str, str], ...]:
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


def test_active_input_has_an_exact_minimal_direct_import_allowlist() -> None:
    assert frozenset(_import_references(ACTIVE_INPUT_PATH)) == _ALLOWED_DIRECT_IMPORTS


def test_active_input_recursive_closure_stays_in_the_canonical_agent_layer() -> None:
    edges = _agent_dependency_closure(ACTIVE_INPUT_MODULE)
    forbidden = sorted(
        f"{source} -> {imported}"
        for source, imported in edges
        if _is_forbidden(imported)
    )
    outer_project = sorted(
        f"{source} -> {imported}"
        for source, imported in edges
        if imported.startswith("pipy_harness.")
        and not imported.startswith("pipy_harness.native.agent.")
    )
    assert forbidden == []
    assert outer_project == []


def test_agent_root_does_not_eagerly_export_or_import_active_input() -> None:
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
native_package = namespace_package("pipy_harness.native", {str(native_root)!r})
pipy_package.native = native_package

agent = importlib.import_module("pipy_harness.native.agent")
assert "AgentActiveInput" not in agent.__dict__
assert {ACTIVE_INPUT_MODULE!r} not in sys.modules
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_active_input_import_is_isolated_and_order_independent() -> None:
    package_root = SOURCE_ROOT / "pipy_harness"
    native_root = package_root / "native"
    for first, second in (
        (ACTIVE_INPUT_MODULE, MESSAGES_MODULE),
        (MESSAGES_MODULE, ACTIVE_INPUT_MODULE),
    ):
        code = f"""
import importlib
import importlib.abc
import sys
import types

PREFIXES = {_FORBIDDEN_PREFIXES!r}

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
        if any(
            fullname == prefix or fullname.startswith(prefix + ".")
            for prefix in PREFIXES
        ):
            raise AssertionError("forbidden import: " + fullname)
        return None

sys.meta_path.insert(0, BlockForbidden())
importlib.import_module({first!r})
importlib.import_module({second!r})
active_input = importlib.import_module({ACTIVE_INPUT_MODULE!r})
messages = importlib.import_module({MESSAGES_MODULE!r})
assert active_input.AgentActiveInput.__module__ == {ACTIVE_INPUT_MODULE!r}
assert messages.AgentUserMessage.__module__ == {MESSAGES_MODULE!r}
unexpected = sorted(
    name
    for name in sys.modules
    if any(name == prefix or name.startswith(prefix + ".") for prefix in PREFIXES)
)
assert unexpected == [], unexpected
"""
        completed = subprocess.run(
            [sys.executable, "-c", code],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
