"""Focused dependency gates for canonical and product loop-policy seams."""

from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPO_ROOT / "src"
CANONICAL_MODULE = "pipy_harness.native.agent.loop_policy"
CANONICAL_PATH = SOURCE_ROOT / "pipy_harness/native/agent/loop_policy.py"
PRODUCT_MODULE = "pipy_harness.native.agent_loop_policy"
PRODUCT_PATH = SOURCE_ROOT / "pipy_harness/native/agent_loop_policy.py"
NATIVE_MODELS_PATH = SOURCE_ROOT / "pipy_harness/native/models.py"

_CANONICAL_ALLOWED_DIRECT_IMPORTS = frozenset(
    {
        "__future__",
        "__future__.annotations",
        "collections.abc",
        "collections.abc.Mapping",
        "dataclasses",
        "dataclasses.dataclass",
        "dataclasses.replace",
        "enum",
        "enum.StrEnum",
        "pipy_harness.native.agent._validation",
        "pipy_harness.native.agent._validation.require_bool",
        "pipy_harness.native.agent._validation.require_non_negative_int",
        "pipy_harness.native.agent.active_input",
        "pipy_harness.native.agent.active_input.AgentActiveInput",
        "pipy_harness.native.agent.content",
        "pipy_harness.native.agent.content.ProductContent",
        "pipy_harness.native.agent.messages",
        "pipy_harness.native.agent.messages.AgentToolCall",
        "pipy_harness.native.agent.messages.AgentToolResultMessage",
        "pipy_harness.native.agent.messages.AgentUserMessage",
        "pipy_harness.native.agent.request",
        "pipy_harness.native.agent.request.AgentProviderRequestSnapshot",
        "pipy_harness.native.agent.request.freeze_provider_request",
        "pipy_harness.native.agent.request.validate_agent_tool_call",
        "pipy_harness.native.agent.request.validate_agent_tool_result_message",
        "pipy_harness.native.agent.request.validate_product_content",
        "pipy_harness.native.agent.results",
        "pipy_harness.native.agent.results.AgentFailure",
        "pipy_harness.native.agent.tools",
        "pipy_harness.native.agent.tools.ToolExecutionInterruption",
        "pipy_harness.native.agent.tools.ToolExecutionOutcome",
        "pipy_harness.native.models",
        "pipy_harness.native.models.ProviderRequest",
        "pipy_harness.native.models.ProviderResult",
        "pipy_harness.status",
        "pipy_harness.status.HarnessStatus",
        "typing",
        "typing.Protocol",
        "typing.runtime_checkable",
    }
)

_PRODUCT_ALLOWED_DIRECT_IMPORTS = frozenset(
    {
        "__future__",
        "__future__.annotations",
        "collections.abc",
        "collections.abc.Callable",
        "dataclasses",
        "dataclasses.replace",
        "pipy_harness.native.agent.content",
        "pipy_harness.native.agent.content.ProductContent",
        "pipy_harness.native.agent.loop_policy",
        "pipy_harness.native.agent.loop_policy.AgentProviderRequestPolicyInput",
        "pipy_harness.native.agent.loop_policy.AgentToolPolicyDecision",
        "pipy_harness.native.agent.loop_policy.validate_agent_tool_policy_decision",
        "pipy_harness.native.agent.messages",
        "pipy_harness.native.agent.messages.AgentToolCall",
        "pipy_harness.native.agent.messages.AgentToolResultMessage",
        "pipy_harness.native.agent.request",
        "pipy_harness.native.agent.request.AgentProviderRequestSnapshot",
        "pipy_harness.native.agent.request.validate_product_content",
        "pipy_harness.native.agent.request.validate_provider_request_snapshot",
        "pipy_harness.native.models",
        "pipy_harness.native.models.ProviderRequest",
        "pipy_harness.native.tools.base",
        "pipy_harness.native.tools.base.ToolDefinition",
        "pipy_harness.native.tools.base.materialize_tool_input_schema",
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
    "pipy_harness.native.extensions",
    "pipy_harness.native.tool_loop_session",
    "pipy_harness.native.session",
    "pipy_harness.native.session_resume",
    "pipy_harness.native.session_tree",
    "pipy_harness.native.session_tree_commands",
    "pipy_harness.native.persistence",
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
    "pipy_harness.native.providers.azure_openai_responses",
    "pipy_harness.native.bedrock_provider",
    "pipy_harness.native.providers.cloudflare",
    "pipy_harness.native.providers.ds4",
    "pipy_harness.native.google_provider",
    "pipy_harness.native.google_vertex_provider",
    "pipy_harness.native.providers.mistral",
    "pipy_harness.native.openai_codex_provider",
    "pipy_harness.native.providers.openai_completions",
    "pipy_harness.native.providers.openai_responses",
    "pipy_harness.native.providers.openrouter",
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
    """Collect initialization imports, excluding annotations and deferred code."""

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


def _is_forbidden(module: str) -> bool:
    return any(
        module == prefix or module.startswith(f"{prefix}.")
        for prefix in _FORBIDDEN_PREFIXES
    )


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


def _method_names(node: ast.ClassDef) -> tuple[str, ...]:
    return tuple(
        child.name for child in node.body if isinstance(child, ast.FunctionDef)
    )


def _enum_members(node: ast.ClassDef) -> tuple[str, ...]:
    return tuple(
        child.targets[0].id
        for child in node.body
        if isinstance(child, ast.Assign)
        and len(child.targets) == 1
        and isinstance(child.targets[0], ast.Name)
    )


def test_loop_policy_layers_have_exact_direct_import_allowlists() -> None:
    assert frozenset(_import_references(CANONICAL_PATH)) == (
        _CANONICAL_ALLOWED_DIRECT_IMPORTS
    )
    assert frozenset(_import_references(PRODUCT_PATH)) == (
        _PRODUCT_ALLOWED_DIRECT_IMPORTS
    )


def test_loop_policy_layers_have_no_direct_forbidden_dependencies() -> None:
    for path in (CANONICAL_PATH, PRODUCT_PATH):
        violations = sorted(
            imported for imported in _import_references(path) if _is_forbidden(imported)
        )
        assert violations == [], path


def test_loop_policy_runtime_closures_have_no_laundered_dependencies() -> None:
    for module in (CANONICAL_MODULE, PRODUCT_MODULE):
        violations = sorted(
            f"{source} -> {imported}"
            for source, imported in _runtime_dependency_closure(module)
            if _is_forbidden(imported)
        )
        assert violations == [], module


def test_runtime_closure_ignores_native_models_type_checking_imports() -> None:
    references = _runtime_import_references(NATIVE_MODELS_PATH)

    assert "pipy_harness.status" in references
    assert "pipy_harness.native.agent" not in references
    assert "pipy_harness.native.conversation" not in references
    assert "pipy_harness.native.image_attachment" not in references
    assert "pipy_harness.native.tools.base" not in references


def test_canonical_loop_policy_has_only_narrow_typed_surfaces() -> None:
    tree = ast.parse(
        CANONICAL_PATH.read_text(encoding="utf-8"), filename=str(CANONICAL_PATH)
    )
    constant = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "MAX_AGENT_TOOL_BUDGET"
            for target in node.targets
        )
    )
    assert ast.literal_eval(constant.value) == 200
    fields = {
        "AgentProviderRequestPolicyInput": ("baseline", "active_input"),
        "AgentToolPolicyDecision": ("blocked_reason",),
        "AgentToolPolicyState": (
            "tool_budget",
            "malformed_limit",
            "invocations_this_turn",
            "tool_invocation_count",
            "malformed_argument_count",
            "consecutive_malformed_streak",
            "budget_exhausted_count",
        ),
        "AgentToolPolicyTransition": (
            "action",
            "state",
            "failure",
            "interruption",
        ),
        "AgentProviderStatusDecision": (
            "action",
            "failure",
            "response_status",
            "will_retry",
        ),
    }
    for name, expected_fields in fields.items():
        node = _class_node(tree, name)
        assert _decorator_keywords(node) == {"frozen": True, "slots": True}
        assert _annotated_fields(node.body) == expected_fields

    request_protocol = _class_node(tree, "AgentProviderRequestPolicy")
    tool_protocol = _class_node(tree, "AgentToolPolicy")
    for protocol in (request_protocol, tool_protocol):
        assert [base.id for base in protocol.bases if isinstance(base, ast.Name)] == [
            "Protocol"
        ]
        assert any(
            isinstance(decorator, ast.Name) and decorator.id == "runtime_checkable"
            for decorator in protocol.decorator_list
        )
    assert _method_names(request_protocol) == ("prepare",)
    assert _method_names(tool_protocol) == ("before_execute", "transform_result")

    assert _enum_members(_class_node(tree, "AgentToolPolicyAction")) == (
        "EXECUTE",
        "BUDGET_EXHAUSTED",
        "UNAUTHORIZED",
        "BLOCKED",
        "SETTLED",
        "MALFORMED",
        "INTERRUPTED",
    )
    assert _enum_members(_class_node(tree, "AgentProviderStatusAction")) == (
        "SUCCEEDED",
        "FAILED",
    )
    assert tuple(
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")
    ) == (
        "validate_agent_tool_policy_decision",
        "decide_tool_admission",
        "apply_tool_policy_decision",
        "settle_tool_execution",
        "normalize_provider_status",
    )


def test_product_loop_policy_adapters_are_narrow_slotted_callbacks() -> None:
    tree = ast.parse(
        PRODUCT_PATH.read_text(encoding="utf-8"), filename=str(PRODUCT_PATH)
    )
    request_adapter = _class_node(tree, "NativeAgentProviderRequestPolicy")
    tool_adapter = _class_node(tree, "NativeAgentToolPolicy")

    assert _method_names(request_adapter) == ("__init__", "prepare")
    assert _method_names(tool_adapter) == (
        "__init__",
        "before_execute",
        "transform_result",
    )
    assert _annotated_fields(request_adapter.body) == ()
    assert _annotated_fields(tool_adapter.body) == ()
    request_slots = next(
        node for node in request_adapter.body if isinstance(node, ast.Assign)
    )
    tool_slots = next(
        node for node in tool_adapter.body if isinstance(node, ast.Assign)
    )
    assert ast.literal_eval(request_slots.value) == ("_prepare",)
    assert ast.literal_eval(tool_slots.value) == (
        "_before_execute",
        "_transform_result",
    )


def test_canonical_and_product_loop_policy_imports_are_order_independent() -> None:
    package_root = SOURCE_ROOT / "pipy_harness"
    native_root = package_root / "native"
    prefixes = repr(_FORBIDDEN_PREFIXES)
    for first, second in (
        (CANONICAL_MODULE, PRODUCT_MODULE),
        (PRODUCT_MODULE, CANONICAL_MODULE),
    ):
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
first = importlib.import_module({first!r})
second = importlib.import_module({second!r})
canonical = importlib.import_module({CANONICAL_MODULE!r})
product = importlib.import_module({PRODUCT_MODULE!r})
assert canonical.AgentToolPolicyState.__module__ == {CANONICAL_MODULE!r}
assert product.NativeAgentToolPolicy.__module__ == {PRODUCT_MODULE!r}
"""
        completed = subprocess.run(
            [sys.executable, "-c", code],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr


def test_loop_policy_surfaces_are_not_eager_package_root_exports() -> None:
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
for name in (
    "AgentProviderRequestPolicy",
    "AgentProviderRequestPolicyInput",
    "AgentToolPolicy",
    "AgentToolPolicyState",
    "NativeAgentProviderRequestPolicy",
    "NativeAgentToolPolicy",
):
    assert name not in agent.__dict__
    assert name not in native_package.__dict__
assert {CANONICAL_MODULE!r} not in sys.modules
assert {PRODUCT_MODULE!r} not in sys.modules
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

for name in (
    "AgentProviderRequestPolicy",
    "AgentProviderRequestPolicyInput",
    "AgentToolPolicy",
    "AgentToolPolicyState",
    "NativeAgentProviderRequestPolicy",
    "NativeAgentToolPolicy",
):
    assert not hasattr(agent, name)
    assert not hasattr(native, name)
"""
    root_completed = subprocess.run(
        [sys.executable, "-c", root_code],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert root_completed.returncode == 0, root_completed.stderr
