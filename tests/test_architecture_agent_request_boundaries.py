"""Focused dependency gates for the canonical provider-request policy seam."""

from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPO_ROOT / "src"
REQUEST_MODULE = "pipy_harness.native.agent.request"
REQUEST_PATH = SOURCE_ROOT / "pipy_harness/native/agent/request.py"
PRODUCT_REQUEST_MODULE = "pipy_harness.native.agent_request"
PRODUCT_REQUEST_PATH = SOURCE_ROOT / "pipy_harness/native/agent_request.py"

_REQUEST_ALLOWED_DIRECT_IMPORTS = frozenset(
    {
        "__future__",
        "__future__.annotations",
        "collections.abc",
        "collections.abc.Iterable",
        "collections.abc.Mapping",
        "dataclasses",
        "dataclasses.dataclass",
        "dataclasses.replace",
        "pathlib",
        "pathlib.Path",
        "pipy_harness.native.agent.content",
        "pipy_harness.native.agent.content.ProductContent",
        "pipy_harness.native.agent.messages",
        "pipy_harness.native.agent.messages.AgentAssistantMessage",
        "pipy_harness.native.agent.messages.AgentMessage",
        "pipy_harness.native.agent.messages.AgentToolCall",
        "pipy_harness.native.agent.messages.AgentToolResultMessage",
        "pipy_harness.native.agent.messages.AgentUserMessage",
        "pipy_harness.native.models",
        "pipy_harness.native.models.ProviderRequest",
        "pipy_harness.native.tools.base",
        "pipy_harness.native.tools.base.ToolDefinition",
        "sys",
    }
)

_PRODUCT_REQUEST_ALLOWED_DIRECT_IMPORTS = frozenset(
    {
        "__future__",
        "__future__.annotations",
        "collections.abc",
        "collections.abc.Awaitable",
        "collections.abc.Callable",
        "collections.abc.Mapping",
        "collections.abc.Sequence",
        "dataclasses",
        "dataclasses.dataclass",
        "dataclasses.replace",
        "inspect",
        "pipy_harness.native.agent.loop_policy",
        "pipy_harness.native.agent.loop_policy.AgentProviderRequestPolicyInput",
        "pipy_harness.native.agent.request",
        "pipy_harness.native.agent.request.AgentProviderRequestSnapshot",
        "pipy_harness.native.agent.request.snapshot_provider_request",
        "pipy_harness.native.extension_runtime",
        "pipy_harness.native.extension_runtime.ControlSetActiveToolsFn",
        "pipy_harness.native.extension_runtime.ControlSetModelFn",
        "pipy_harness.native.extension_runtime.ControlSetThinkingLevelFn",
        "pipy_harness.native.extension_runtime.ExtensionUiDriver",
        "pipy_harness.native.extension_runtime.HookHandler",
        "pipy_harness.native.extension_runtime.ProviderRequestTransform",
        "pipy_harness.native.extension_runtime.dispatch_before_provider_request_hooks",
        "pipy_harness.native.tools.base",
        "pipy_harness.native.tools.base.ToolDefinition",
        "typing",
        "typing.TYPE_CHECKING",
        "typing.cast",
    }
)

_PRODUCT_FORBIDDEN_PREFIXES = (
    "pipy_harness.capture",
    "pipy_session",
    "pipy_harness.adapters",
    "pipy_harness.cli",
    "pipy_harness.native.agent_adapters",
    "pipy_harness.native.automation",
    "pipy_harness.native.tool_loop_session",
    "pipy_harness.native.session",
    "pipy_harness.native.session_resume",
    "pipy_harness.native.session_tree",
    "pipy_harness.native.session_tree_commands",
    "pipy_harness.native.provider_construction",
    "pipy_harness.native.provider_registry",
    "pipy_harness.native.providers",
    "pipy_harness.native._provider_helpers",
    "pipy_harness.native.deferred_tools",
    "pipy_harness.native.fake",
    "pipy_harness.native.ui",
    "pipy_harness.native.tui",
    "pipy_harness.native.terminal_compare",
    "pipy_harness.native.terminal_input",
    "pipy_harness.native.terminal_screen",
    "pipy_harness.native.providers.anthropic_messages",
    "pipy_harness.native.providers.azure_openai_responses",
    "pipy_harness.native.providers.bedrock",
    "pipy_harness.native.providers.cloudflare",
    "pipy_harness.native.providers.ds4",
    "pipy_harness.native.google_provider",
    "pipy_harness.native.google_vertex_provider",
    "pipy_harness.native.providers.mistral",
    "pipy_harness.native.openai_codex_provider",
    "pipy_harness.native.providers.openai_completions",
    "pipy_harness.native.providers.openai_responses",
    "pipy_harness.native.providers.openrouter",
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

_FORBIDDEN_PREFIXES = (
    "pipy_harness.capture",
    "pipy_session",
    "pipy_harness.adapters",
    "pipy_harness.cli",
    "pipy_harness.native.agent_adapters",
    "pipy_harness.native.automation",
    "pipy_harness.native.extension_runtime",
    "pipy_harness.native.extensions",
    "pipy_harness.native.tool_loop_session",
    "pipy_harness.native.session",
    "pipy_harness.native.session_resume",
    "pipy_harness.native.session_tree",
    "pipy_harness.native.session_tree_commands",
    "pipy_harness.native.provider",
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
    "pipy_harness.native.providers.anthropic_messages",
    "pipy_harness.native.providers.azure_openai_responses",
    "pipy_harness.native.providers.bedrock",
    "pipy_harness.native.providers.cloudflare",
    "pipy_harness.native.providers.ds4",
    "pipy_harness.native.google_provider",
    "pipy_harness.native.google_vertex_provider",
    "pipy_harness.native.providers.mistral",
    "pipy_harness.native.openai_codex_provider",
    "pipy_harness.native.providers.openai_completions",
    "pipy_harness.native.providers.openai_responses",
    "pipy_harness.native.providers.openrouter",
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

_PRODUCT_EAGER_FORBIDDEN_PREFIXES = (
    *_FORBIDDEN_PREFIXES,
    "pipy_harness.native.image_attachment",
    "pipy_harness.native.read_only_tool",
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
    """Collect module-initialization imports while excluding deferred scopes."""

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
        type_checking = (
            isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING"
        )
        if type_checking:
            for statement in node.orelse:
                self.visit(statement)
            return
        self.generic_visit(node)


def _runtime_import_references(path: Path) -> tuple[str, ...]:
    """Return only imports executed while the module body is initialized."""

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


def _is_forbidden(module: str) -> bool:
    return any(
        module == prefix or module.startswith(f"{prefix}.")
        for prefix in _FORBIDDEN_PREFIXES
    )


def _agent_dependency_closure(module: str) -> tuple[tuple[str, str], ...]:
    """Follow canonical-agent imports so an internal helper cannot launder deps."""

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
            if imported.startswith("pipy_harness.native.agent."):
                candidate = imported
                while _module_path(candidate) is None and "." in candidate:
                    candidate = candidate.rsplit(".", 1)[0]
                if candidate not in visited and _module_path(candidate) is not None:
                    pending.append(candidate)
    return tuple(edges)


def _runtime_dependency_closure(module: str) -> tuple[tuple[str, str], ...]:
    """Follow project imports that execute while importing ``module``."""

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


def _class_node(tree: ast.Module, name: str) -> ast.ClassDef:
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == name
    )


def _decorator_keywords(node: ast.ClassDef) -> dict[str, object]:
    decorator = next(
        decorator
        for decorator in node.decorator_list
        if isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Name)
        and decorator.func.id == "dataclass"
    )
    return {
        keyword.arg: ast.literal_eval(keyword.value)
        for keyword in decorator.keywords
        if keyword.arg is not None
    }


def _annotated_fields(nodes: Iterable[ast.stmt]) -> tuple[str, ...]:
    return tuple(
        node.target.id
        for node in nodes
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    )


def test_request_layer_has_an_exact_minimal_direct_import_allowlist() -> None:
    assert (
        frozenset(_import_references(REQUEST_PATH)) == _REQUEST_ALLOWED_DIRECT_IMPORTS
    )


def test_request_layer_forbids_product_effect_and_transport_dependencies() -> None:
    violations = sorted(
        imported
        for imported in _import_references(REQUEST_PATH)
        if _is_forbidden(imported)
    )
    assert violations == []


def test_request_layer_agent_dependency_closure_cannot_launder_imports() -> None:
    violations = sorted(
        f"{source} -> {imported}"
        for source, imported in _agent_dependency_closure(REQUEST_MODULE)
        if _is_forbidden(imported)
    )
    assert violations == []


def test_request_layer_contract_is_statically_frozen_and_narrow() -> None:
    tree = ast.parse(
        REQUEST_PATH.read_text(encoding="utf-8"), filename=str(REQUEST_PATH)
    )
    snapshot = _class_node(tree, "AgentProviderRequestSnapshot")

    assert _decorator_keywords(snapshot) == {"frozen": True, "slots": True}
    assert _annotated_fields(snapshot.body) == (
        "request",
        "advertised_tool_names",
    )
    assert [
        node.name for node in snapshot.body if isinstance(node, ast.FunctionDef)
    ] == ["__post_init__", "authorizes"]
    assert any(
        isinstance(node, ast.FunctionDef) and node.name == "snapshot_provider_request"
        for node in tree.body
    )


def test_product_request_adapter_has_an_exact_direct_import_allowlist() -> None:
    assert (
        frozenset(_import_references(PRODUCT_REQUEST_PATH))
        == _PRODUCT_REQUEST_ALLOWED_DIRECT_IMPORTS
    )


def test_product_request_adapter_forbids_unowned_effect_dependencies() -> None:
    violations = sorted(
        imported
        for imported in _import_references(PRODUCT_REQUEST_PATH)
        if any(
            imported == prefix or imported.startswith(f"{prefix}.")
            for prefix in _PRODUCT_FORBIDDEN_PREFIXES
        )
    )
    assert violations == []


def test_product_request_adapter_runtime_closure_has_no_eager_effect_imports() -> None:
    violations = sorted(
        f"{source} -> {imported}"
        for source, imported in _runtime_dependency_closure(PRODUCT_REQUEST_MODULE)
        if any(
            imported == prefix or imported.startswith(f"{prefix}.")
            for prefix in _PRODUCT_EAGER_FORBIDDEN_PREFIXES
        )
    )
    assert violations == []


def test_product_request_adapter_is_not_an_eager_native_root_export() -> None:
    code = """
import pipy_harness.native as native

assert not hasattr(native, 'NativeProviderRequestInput')
assert not hasattr(native, 'NativeProviderRequestHookContext')
assert not hasattr(native, 'prepare_provider_request')
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_product_and_canonical_request_imports_are_order_independent() -> None:
    package_root = SOURCE_ROOT / "pipy_harness"
    native_root = package_root / "native"
    for first, second in (
        (REQUEST_MODULE, PRODUCT_REQUEST_MODULE),
        (PRODUCT_REQUEST_MODULE, REQUEST_MODULE),
    ):
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

first = importlib.import_module({first!r})
second = importlib.import_module({second!r})
request = importlib.import_module({REQUEST_MODULE!r})
loop_policy = importlib.import_module("pipy_harness.native.agent.loop_policy")
product = importlib.import_module({PRODUCT_REQUEST_MODULE!r})
assert request.AgentProviderRequestSnapshot.__module__ == {REQUEST_MODULE!r}
assert loop_policy.AgentProviderRequestPolicyInput.__module__ == (
    "pipy_harness.native.agent.loop_policy"
)
assert not hasattr(product, "NativeProviderRequestInput")
assert product.prepare_provider_request.__module__ == {PRODUCT_REQUEST_MODULE!r}
"""
        completed = subprocess.run(
            [sys.executable, "-c", code],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr


def test_agent_root_does_not_eagerly_export_or_import_request_policy() -> None:
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
assert 'AgentProviderRequestSnapshot' not in agent.__dict__
assert 'snapshot_provider_request' not in agent.__dict__
assert 'pipy_harness.native.agent.request' not in sys.modules
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_request_layer_imports_in_fresh_process_with_forbidden_modules_blocked() -> (
    None
):
    prefixes = repr(_FORBIDDEN_PREFIXES)
    package_root = SOURCE_ROOT / "pipy_harness"
    native_root = package_root / "native"
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
request = importlib.import_module("pipy_harness.native.agent.request")
AgentProviderRequestSnapshot = request.AgentProviderRequestSnapshot
snapshot_provider_request = request.snapshot_provider_request
assert AgentProviderRequestSnapshot.__module__ == '{REQUEST_MODULE}'
assert snapshot_provider_request.__module__ == '{REQUEST_MODULE}'
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_product_request_adapter_imports_without_eager_effect_modules() -> None:
    prefixes = repr(_PRODUCT_EAGER_FORBIDDEN_PREFIXES)
    package_root = SOURCE_ROOT / "pipy_harness"
    native_root = package_root / "native"
    preseeded = (
        "pipy_harness.capture",
        "pipy_harness.native.image_attachment",
        "pipy_harness.native.read_only_tool",
    )
    code = f"""
import importlib
import importlib.abc
import sys
import types

PREFIXES = {prefixes}
PRESEEDED = {preseeded!r}

def namespace_package(name, path):
    module = types.ModuleType(name)
    module.__package__ = name
    module.__path__ = [path]
    sys.modules[name] = module
    return module

pipy_package = namespace_package("pipy_harness", {str(package_root)!r})
native_package = namespace_package("pipy_harness.native", {str(native_root)!r})
pipy_package.native = native_package

for name in PRESEEDED:
    sys.modules[name] = None

class BlockForbidden(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        del path, target
        if any(fullname == prefix or fullname.startswith(prefix + '.') for prefix in PREFIXES):
            raise AssertionError('forbidden import: ' + fullname)
        return None

sys.meta_path.insert(0, BlockForbidden())
product = importlib.import_module({PRODUCT_REQUEST_MODULE!r})
assert not hasattr(product, "NativeProviderRequestInput")
assert product.prepare_provider_request.__module__ == {PRODUCT_REQUEST_MODULE!r}
assert all(sys.modules[name] is None for name in PRESEEDED)
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
