"""Dependency gates for canonical and product agent runtime ports."""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPO_ROOT / "src"
RUNTIME_PORTS_MODULE = "pipy_harness.native.agent.runtime_ports"
RUNTIME_PORTS_PATH = SOURCE_ROOT / "pipy_harness/native/agent/runtime_ports.py"
PRODUCT_RUNTIME_MODULE = "pipy_harness.native.agent_runtime"
PRODUCT_RUNTIME_PATH = SOURCE_ROOT / "pipy_harness/native/agent_runtime.py"

_RUNTIME_PORTS_DIRECT_IMPORTS = frozenset(
    {
        "__future__",
        "__future__.annotations",
        "dataclasses",
        "dataclasses.dataclass",
        "enum",
        "enum.StrEnum",
        "typing",
        "typing.Protocol",
        "typing.runtime_checkable",
        "pipy_harness.native.agent.content",
        "pipy_harness.native.agent.content.ProductContent",
        "pipy_harness.native.agent.results",
        "pipy_harness.native.agent.results.AgentUsage",
        "pipy_harness.native.agent.usage",
        "pipy_harness.native.agent.usage.AgentProviderUsageSample",
    }
)

_PRODUCT_RUNTIME_DIRECT_IMPORTS = frozenset(
    {
        "__future__",
        "__future__.annotations",
        "collections.abc",
        "collections.abc.Callable",
        "pipy_harness.native.agent.events",
        "pipy_harness.native.agent.events.UsageUpdated",
        "pipy_harness.native.agent.ports",
        "pipy_harness.native.agent.ports.AgentEventSink",
        "pipy_harness.native.agent.runtime_ports",
        "pipy_harness.native.agent.runtime_ports.AgentQueuedInput",
        "pipy_harness.native.agent.runtime_ports.AgentUsagePublication",
        "pipy_harness.native.agent.usage",
        "pipy_harness.native.agent.usage.AgentProviderUsageSample",
    }
)

_FORBIDDEN_PREFIXES = (
    "pipy_harness.capture",
    "pipy_session",
    "pipy_harness.adapters",
    "pipy_harness.cli",
    "pipy_harness.native.agent_adapters",
    "pipy_harness.native.automation",
    "pipy_harness.native.extension_runtime",
    "pipy_harness.native.provider_construction",
    "pipy_harness.native.provider_registry",
    "pipy_harness.native.providers",
    "pipy_harness.native.session",
    "pipy_harness.native.session_resume",
    "pipy_harness.native.session_tree",
    "pipy_harness.native.coding.session",
    "pipy_harness.native.tui",
    "pipy_harness.native.tools",
)


def _import_references(path: Path) -> frozenset[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    references: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            references.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            references.add(node.module)
            references.update(f"{node.module}.{alias.name}" for alias in node.names)
    return frozenset(references)


def _module_path(module: str) -> Path | None:
    relative = Path(*module.split("."))
    module_path = SOURCE_ROOT / relative.with_suffix(".py")
    if module_path.is_file():
        return module_path
    package_path = SOURCE_ROOT / relative / "__init__.py"
    return package_path if package_path.is_file() else None


def _recursive_project_imports(root_module: str) -> frozenset[str]:
    pending = [root_module]
    visited: set[str] = set()
    while pending:
        module = pending.pop()
        if module in visited:
            continue
        path = _module_path(module)
        if path is None:
            continue
        visited.add(module)
        for reference in _import_references(path):
            if reference.startswith("pipy_harness.") and _module_path(reference):
                pending.append(reference)
    return frozenset(visited)


def _matches_any(module: str, prefixes: Iterable[str]) -> bool:
    return any(
        module == prefix or module.startswith(f"{prefix}.") for prefix in prefixes
    )


def test_runtime_port_modules_have_exact_direct_dependencies() -> None:
    assert _import_references(RUNTIME_PORTS_PATH) == _RUNTIME_PORTS_DIRECT_IMPORTS
    assert _import_references(PRODUCT_RUNTIME_PATH) == _PRODUCT_RUNTIME_DIRECT_IMPORTS


def test_runtime_ports_recursive_closure_is_canonical_and_dependency_neutral() -> None:
    closure = _recursive_project_imports(RUNTIME_PORTS_MODULE)

    assert closure == frozenset(
        {
            "pipy_harness.native.agent._validation",
            "pipy_harness.native.agent.content",
            "pipy_harness.native.agent.identity",
            "pipy_harness.native.agent.messages",
            "pipy_harness.native.agent.results",
            "pipy_harness.native.agent.runtime_ports",
            "pipy_harness.native.agent.usage",
        }
    )
    assert not any(_matches_any(module, _FORBIDDEN_PREFIXES) for module in closure)


def test_product_runtime_recursive_closure_stays_on_canonical_adapter_seam() -> None:
    closure = _recursive_project_imports(PRODUCT_RUNTIME_MODULE)

    assert closure == frozenset(
        {
            "pipy_harness.native.agent._validation",
            "pipy_harness.native.agent.content",
            "pipy_harness.native.agent.events",
            "pipy_harness.native.agent.identity",
            "pipy_harness.native.agent.messages",
            "pipy_harness.native.agent.ports",
            "pipy_harness.native.agent.results",
            "pipy_harness.native.agent.runtime_ports",
            "pipy_harness.native.agent.usage",
            "pipy_harness.native.agent_runtime",
        }
    )
    assert not any(_matches_any(module, _FORBIDDEN_PREFIXES) for module in closure)


def _run_isolated_imports(import_order: tuple[str, ...]) -> dict[str, object]:
    package_root = SOURCE_ROOT / "pipy_harness"
    native_root = package_root / "native"
    script = f"""
import importlib.abc
import json
import sys
import types

forbidden = {list(_FORBIDDEN_PREFIXES)!r}

def namespace_package(name, path):
    module = types.ModuleType(name)
    module.__package__ = name
    module.__path__ = [path]
    sys.modules[name] = module
    return module

pipy_package = namespace_package("pipy_harness", {str(package_root)!r})
native_package = namespace_package("pipy_harness.native", {str(native_root)!r})
pipy_package.native = native_package

class Blocker(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        del path, target
        if any(fullname == prefix or fullname.startswith(prefix + '.') for prefix in forbidden):
            raise RuntimeError('forbidden eager import: ' + fullname)
        return None

sys.meta_path.insert(0, Blocker())
for module in {list(import_order)!r}:
    __import__(module)
loaded = sorted(
    module
    for module in sys.modules
    if any(module == prefix or module.startswith(prefix + '.') for prefix in forbidden)
)
print(json.dumps({{'loaded_forbidden': loaded}}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_runtime_ports_import_in_fresh_process_without_eager_product_dependencies() -> (
    None
):
    assert _run_isolated_imports((RUNTIME_PORTS_MODULE,)) == {"loaded_forbidden": []}


def test_runtime_port_import_order_is_stable_and_non_eager() -> None:
    expected: dict[str, object] = {"loaded_forbidden": []}

    assert (
        _run_isolated_imports((RUNTIME_PORTS_MODULE, PRODUCT_RUNTIME_MODULE))
        == expected
    )
    assert (
        _run_isolated_imports((PRODUCT_RUNTIME_MODULE, RUNTIME_PORTS_MODULE))
        == expected
    )
