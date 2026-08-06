"""Final tests-only audit for god-file decomposition retry slice 49."""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import sys
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPO_ROOT / "src"
PRODUCTION_ROOT = SOURCE_ROOT / "pipy_harness"

TARGET_OWNERS = {
    "TerminalUi": "pipy_harness.native.tui",
    "RunControlState": "pipy_harness.native.repl.loop_scope",
    "CodingSession": "pipy_harness.native.coding.session",
}

# This is the member list, not a line-number proxy. The analyzer below derives
# the declaration inventory and every typed production receiver access from AST.
MEMBER_LIST = {
    "TerminalUi": (
        "available_provider_count",
        "components",
        "cwd",
        "footer_lines",
        "include_workspace_defaults",
        "input_stream",
        "keybindings_manager",
        "runtime_label",
        "terminal_stream",
    ),
    "RunControlState": (
        "_session_tree",
        "agent_settled_pending",
        "coding_effects",
        "extension_in_agent_turn",
        "generation_ref",
        "line",
        "package_roots",
        "pending_prefill",
        "tree_filter_mode",
        "workspace_resources",
    ),
    "CodingSession": (
        "_coding_state",
        "abort_event",
        "agent_event_sink",
        "automation_observer",
        "clipboard_copy",
        "clipboard_image_read",
        "implicit_trust",
        "initial_extension_batch",
        "initial_messages",
        "input_runtime",
        "keybindings_manager",
        "native_session",
        "prompt_history_store",
        "provider_state",
        "reference_roots",
        "resource_options",
        "resume_branch_label",
        "resume_context",
        "settings_manager",
        "tool_budget",
        "tool_filter_options",
        "tool_registry",
        "verbose_startup",
        "workspace_root",
    ),
}

EXPECTED_ACCESSED_FIELDS = {
    target: frozenset(fields) for target, fields in MEMBER_LIST.items()
}
# A constructor-facing label is retained as dataclass state but is not read by
# production after initialization. Keep that measured distinction explicit.
EXPECTED_ACCESSED_FIELDS["TerminalUi"] -= {"runtime_label"}


@dataclass(frozen=True)
class _Module:
    name: str
    path: Path
    source: str
    tree: ast.Module
    imports: dict[str, str]
    is_package: bool
    function_returns: dict[str, ast.expr | None]


@dataclass(frozen=True)
class _ClassInfo:
    canonical: str
    module: str
    node: ast.ClassDef
    members: frozenset[str]
    fields: tuple[str, ...]
    member_types: dict[str, frozenset[str]]
    is_dataclass: bool
    field_inventory_complete: bool


@dataclass(frozen=True)
class _ClassDeclaration:
    module: _Module
    node: ast.ClassDef
    own_members: frozenset[str]
    own_fields: tuple[str, ...]
    own_member_types: dict[str, frozenset[str]]
    declared_dataclass: bool
    bases: tuple[str | None, ...]


@dataclass(frozen=True, order=True)
class _Access:
    target: str
    field: str
    module: str
    mode: str


@dataclass(frozen=True)
class _InventoryResult:
    fields: dict[str, tuple[str, ...]]
    accesses: frozenset[_Access]
    violations: tuple[str, ...]


def _module_name(path: Path, root: Path) -> str:
    relative = path.relative_to(root).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _lexical_body_nodes(scope: ast.AST) -> list[ast.AST]:
    """Return a lexical body's descendants without entering nested scopes."""

    nodes: list[ast.AST] = []
    pending = list(ast.iter_child_nodes(scope))
    while pending:
        node = pending.pop()
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        nodes.append(node)
        pending.extend(ast.iter_child_nodes(node))
    return nodes


def _import_from_base(node: ast.ImportFrom, module_name: str, is_package: bool) -> str:
    if not node.level:
        return node.module or ""
    package = module_name if is_package else module_name.rpartition(".")[0]
    parts = package.split(".") if package else []
    drop = node.level - 1
    base_parts = parts[: max(0, len(parts) - drop)]
    if node.module:
        base_parts.extend(node.module.split("."))
    return ".".join(base_parts)


def _imports(tree: ast.Module, module_name: str, is_package: bool) -> dict[str, str]:
    result: dict[str, str] = {}
    for node in _lexical_body_nodes(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                result[item.asname or item.name.split(".")[0]] = (
                    item.name if item.asname else item.name.split(".")[0]
                )
        elif isinstance(node, ast.ImportFrom):
            base = _import_from_base(node, module_name, is_package)
            for item in node.names:
                if item.name != "*":
                    result[item.asname or item.name] = f"{base}.{item.name}".strip(".")
    return result


def _load_modules(root: Path) -> dict[str, _Module]:
    modules: dict[str, _Module] = {}
    for path in sorted(root.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        name = _module_name(path, SOURCE_ROOT if root == PRODUCTION_ROOT else root)
        tree = ast.parse(source, filename=str(path))
        is_package = path.name == "__init__.py"
        function_returns = {
            node.name: node.returns
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        modules[name] = _Module(
            name,
            path,
            source,
            tree,
            _imports(tree, name, is_package),
            is_package,
            function_returns,
        )
    return modules


def _symbol(expr: ast.expr, module: _Module) -> str | None:
    if isinstance(expr, ast.Name):
        return module.imports.get(expr.id, f"{module.name}.{expr.id}")
    if isinstance(expr, ast.Attribute):
        base = _symbol(expr.value, module)
        return f"{base}.{expr.attr}" if base else None
    return None


def _annotation_expr(annotation: ast.expr | None) -> ast.expr | None:
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        try:
            return ast.parse(annotation.value, mode="eval").body
        except SyntaxError:
            return None
    return annotation


_SymbolAliases = dict[str, str | None]
_AliasSnapshots = dict[int, _SymbolAliases]
_FunctionReturnTypes = dict[str, frozenset[str]]
_TypeAliases = dict[str, str | None]
_FinalAnnotationAliases = dict[str, _SymbolAliases]
_CYCLIC_TYPE_ALIAS = "<cyclic-type-alias>"
_UNRESOLVED_TYPE_ALIAS = "<unresolved-type-alias>"
_ALIAS_FAILURES = frozenset({_CYCLIC_TYPE_ALIAS, _UNRESOLVED_TYPE_ALIAS})


def _canonical_alias_target(
    canonical: str | None,
    type_aliases: _TypeAliases,
    terminals: frozenset[str],
) -> str | None:
    seen: set[str] = set()
    while canonical is not None:
        if canonical in terminals:
            return canonical
        if canonical in seen:
            return _CYCLIC_TYPE_ALIAS
        if canonical not in type_aliases:
            return _UNRESOLVED_TYPE_ALIAS if seen else canonical
        seen.add(canonical)
        canonical = type_aliases[canonical]
    return _UNRESOLVED_TYPE_ALIAS if seen else None


def _resolved_type_alias(
    canonical: str | None,
    known_classes: frozenset[str],
    type_aliases: _TypeAliases,
) -> frozenset[str]:
    resolved = _canonical_alias_target(canonical, type_aliases, known_classes)
    if resolved in known_classes or resolved in _ALIAS_FAILURES:
        return frozenset({resolved})
    return frozenset()


def _annotation_types(
    annotation: ast.expr | None,
    module: _Module,
    known_classes: frozenset[str],
    aliases: _SymbolAliases,
    type_aliases: _TypeAliases,
) -> frozenset[str]:
    expression = _annotation_expr(annotation)
    if expression is None:
        return frozenset()
    found: set[str] = set()
    for node in ast.walk(expression):
        if not isinstance(node, (ast.Name, ast.Attribute)):
            continue
        canonical = _resolved_symbol(node, module, aliases)
        if canonical in known_classes:
            found.add(canonical)
        elif _annotation_alias_is_bound(node, aliases):
            found.update(_resolved_type_alias(canonical, known_classes, type_aliases))
    return frozenset(found)


def _annotation_alias_is_bound(expression: ast.expr, aliases: _SymbolAliases) -> bool:
    root: ast.expr = expression
    while isinstance(root, ast.Attribute):
        root = root.value
    return isinstance(root, ast.Name) and root.id in aliases


def _declared_field(node: ast.AnnAssign) -> bool:
    annotation = ast.unparse(node.annotation)
    return "ClassVar" not in annotation and "InitVar" not in annotation


def _class_assigned_names(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Assign):
        return {target.id for target in node.targets if isinstance(target, ast.Name)}
    if isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
        return {node.target.id}
    return set()


def _decorator_symbol(
    decorator: ast.expr, module: _Module, aliases: _SymbolAliases
) -> str | None:
    expression = decorator.func if isinstance(decorator, ast.Call) else decorator
    return _resolved_symbol(expression, module, aliases)


def _base_symbol(
    base: ast.expr, module: _Module, aliases: _SymbolAliases
) -> str | None:
    expression = base.value if isinstance(base, ast.Subscript) else base
    if isinstance(expression, ast.Name) and expression.id == "object":
        return "builtins.object"
    return _resolved_symbol(expression, module, aliases)


def _class_declaration(
    module: _Module,
    node: ast.ClassDef,
    known: frozenset[str],
    aliases: _SymbolAliases,
    annotation_aliases: _SymbolAliases,
    type_aliases: _TypeAliases,
) -> _ClassDeclaration:
    members = {
        item.name
        for item in node.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    own_fields: list[str] = []
    member_types: dict[str, frozenset[str]] = {}
    for item in node.body:
        members.update(_class_assigned_names(item))
        if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
            members.add(item.target.id)
            member_types[item.target.id] = _annotation_types(
                item.annotation, module, known, annotation_aliases, type_aliases
            )
            if _declared_field(item):
                own_fields.append(item.target.id)
        elif isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            member_types[item.name] = _annotation_types(
                item.returns, module, known, annotation_aliases, type_aliases
            )
    declared_dataclass = any(
        _decorator_symbol(decorator, module, aliases) == "dataclasses.dataclass"
        for decorator in node.decorator_list
    )
    return _ClassDeclaration(
        module=module,
        node=node,
        own_members=frozenset(members),
        own_fields=tuple(own_fields),
        own_member_types=member_types,
        declared_dataclass=declared_dataclass,
        bases=tuple(_base_symbol(base, module, aliases) for base in node.bases),
    )


def _cyclic_class_info(canonical: str, declaration: _ClassDeclaration) -> _ClassInfo:
    fields = declaration.own_fields if declaration.declared_dataclass else ()
    return _ClassInfo(
        canonical,
        declaration.module.name,
        declaration.node,
        declaration.own_members,
        tuple(sorted(fields)),
        declaration.own_member_types,
        declaration.declared_dataclass,
        False,
    )


def _resolve_class(
    canonical: str,
    declarations: dict[str, _ClassDeclaration],
    result: dict[str, _ClassInfo],
    resolving: set[str],
) -> _ClassInfo:
    cached = result.get(canonical)
    if cached is not None:
        return cached
    declaration = declarations[canonical]
    if canonical in resolving:
        return _cyclic_class_info(canonical, declaration)

    resolving.add(canonical)
    members = set(declaration.own_members)
    fields: set[str] = set()
    member_types: dict[str, frozenset[str]] = {}
    inherited_dataclass = False
    complete = True
    for base in declaration.bases:
        if base == "builtins.object":
            continue
        if base not in declarations:
            complete = False
            continue
        base_info = _resolve_class(base, declarations, result, resolving)
        members.update(base_info.members)
        member_types.update(base_info.member_types)
        complete &= base_info.field_inventory_complete
        if base_info.is_dataclass:
            inherited_dataclass = True
            fields.update(base_info.fields)
    member_types.update(declaration.own_member_types)
    if declaration.declared_dataclass:
        fields.update(declaration.own_fields)
    resolving.remove(canonical)
    info = _ClassInfo(
        canonical=canonical,
        module=declaration.module.name,
        node=declaration.node,
        members=frozenset(members),
        fields=tuple(sorted(fields)),
        member_types=member_types,
        is_dataclass=declaration.declared_dataclass or inherited_dataclass,
        field_inventory_complete=complete,
    )
    result[canonical] = info
    return info


def _build_classes(
    modules: dict[str, _Module],
    alias_snapshots_by_module: dict[str, _AliasSnapshots] | None = None,
    type_aliases: _TypeAliases | None = None,
    final_annotation_aliases: _FinalAnnotationAliases | None = None,
) -> dict[str, _ClassInfo]:
    class_nodes = {
        f"{module.name}.{node.name}": (module, node)
        for module in modules.values()
        for node in module.tree.body
        if isinstance(node, ast.ClassDef)
    }
    known = frozenset(class_nodes)
    snapshots_by_module = alias_snapshots_by_module or {
        module.name: _symbol_alias_snapshots(module) for module in modules.values()
    }
    resolved_type_aliases = type_aliases or _type_alias_targets(modules)
    annotation_aliases = final_annotation_aliases or _final_annotation_aliases(modules)
    declarations = {
        canonical: _class_declaration(
            module,
            node,
            known,
            snapshots_by_module[module.name].get(id(node), {}),
            annotation_aliases.get(
                module.name, snapshots_by_module[module.name].get(id(node), {})
            ),
            resolved_type_aliases,
        )
        for canonical, (module, node) in class_nodes.items()
    }
    result: dict[str, _ClassInfo] = {}
    resolving: set[str] = set()
    for canonical in declarations:
        _resolve_class(canonical, declarations, result, resolving)
    return result


def _function_return_types(
    modules: dict[str, _Module],
    classes: dict[str, _ClassInfo],
    alias_snapshots_by_module: dict[str, _AliasSnapshots],
    type_aliases: _TypeAliases,
    final_annotation_aliases: _FinalAnnotationAliases,
) -> _FunctionReturnTypes:
    known = frozenset(classes)
    return {
        f"{module.name}.{node.name}": _annotation_types(
            node.returns,
            module,
            known,
            final_annotation_aliases.get(
                module.name,
                alias_snapshots_by_module[module.name].get(id(node), {}),
            ),
            type_aliases,
        )
        for module in modules.values()
        for node in module.tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _member_result_types(
    receiver: ast.expr,
    member: str,
    module: _Module,
    env: dict[str, frozenset[str]],
    classes: dict[str, _ClassInfo],
    function_returns: _FunctionReturnTypes,
    aliases: _SymbolAliases,
    type_aliases: _TypeAliases,
) -> frozenset[str]:
    return frozenset(
        result_type
        for receiver_type in _expression_types(
            receiver,
            module,
            env,
            classes,
            function_returns,
            aliases,
            type_aliases,
        )
        if receiver_type in classes
        for result_type in classes[receiver_type].member_types.get(member, ())
    )


def _expression_types(
    expression: ast.expr,
    module: _Module,
    env: dict[str, frozenset[str]],
    classes: dict[str, _ClassInfo],
    function_returns: _FunctionReturnTypes,
    symbol_aliases: _SymbolAliases,
    type_aliases: _TypeAliases,
) -> frozenset[str]:
    terminals = frozenset(classes) | frozenset(function_returns)
    if isinstance(expression, ast.Await):
        return _expression_types(
            expression.value,
            module,
            env,
            classes,
            function_returns,
            symbol_aliases,
            type_aliases,
        )
    if isinstance(expression, ast.Name):
        canonical = _canonical_expression_symbol(
            expression, module, symbol_aliases, type_aliases, terminals
        )
        inferred = (
            frozenset({canonical})
            if canonical in classes or canonical in _ALIAS_FAILURES
            else frozenset()
        )
        return env.get(expression.id, inferred)
    if isinstance(expression, ast.Attribute):
        canonical = _canonical_expression_symbol(
            expression, module, symbol_aliases, type_aliases, terminals
        )
        if canonical in classes or canonical in _ALIAS_FAILURES:
            return frozenset({canonical})
        return _member_result_types(
            expression.value,
            expression.attr,
            module,
            env,
            classes,
            function_returns,
            symbol_aliases,
            type_aliases,
        )
    if isinstance(expression, ast.Call):
        canonical = _canonical_expression_symbol(
            expression.func, module, symbol_aliases, type_aliases, terminals
        )
        if canonical in classes or canonical in _ALIAS_FAILURES:
            return frozenset({canonical})
        if canonical in function_returns:
            return function_returns[canonical]
        if isinstance(expression.func, ast.Attribute):
            return _member_result_types(
                expression.func.value,
                expression.func.attr,
                module,
                env,
                classes,
                function_returns,
                symbol_aliases,
                type_aliases,
            )
    if isinstance(expression, (ast.IfExp, ast.BoolOp)):
        values = (
            (expression.body, expression.orelse)
            if isinstance(expression, ast.IfExp)
            else expression.values
        )
        return frozenset(
            item
            for value in values
            for item in _expression_types(
                value,
                module,
                env,
                classes,
                function_returns,
                symbol_aliases,
                type_aliases,
            )
        )
    return frozenset()


def _canonical_expression_symbol(
    expression: ast.expr,
    module: _Module,
    aliases: _SymbolAliases,
    type_aliases: _TypeAliases,
    terminals: frozenset[str],
) -> str | None:
    canonical = _resolved_symbol(expression, module, aliases)
    if canonical in terminals or not _annotation_alias_is_bound(expression, aliases):
        return canonical
    return _canonical_alias_target(canonical, type_aliases, terminals)


def _resolved_symbol(
    expression: ast.expr, module: _Module, aliases: _SymbolAliases
) -> str | None:
    if isinstance(expression, ast.Name):
        if expression.id in aliases:
            return aliases[expression.id]
        if expression.id in {"getattr", "setattr"}:
            return f"builtins.{expression.id}"
        return f"{module.name}.{expression.id}"
    if isinstance(expression, ast.Attribute):
        base = _resolved_symbol(expression.value, module, aliases)
        return f"{base}.{expression.attr}" if base else None
    return None


def _position(node: ast.AST) -> tuple[int, int]:
    positioned = node.context_expr if isinstance(node, ast.withitem) else node
    return (getattr(positioned, "lineno", 0), getattr(positioned, "col_offset", 0))


def _end_position(node: ast.AST) -> tuple[int, int]:
    positioned = (
        node.optional_vars or node.context_expr
        if isinstance(node, ast.withitem)
        else node
    )
    return (
        getattr(positioned, "end_lineno", getattr(positioned, "lineno", 0)),
        getattr(positioned, "end_col_offset", getattr(positioned, "col_offset", 0)),
    )


def _bound_names(target: ast.AST) -> list[str]:
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        return [name for item in target.elts for name in _bound_names(item)]
    return []


def _direct_nested_scopes(scope: ast.AST) -> list[_AuditScope]:
    nested: list[_AuditScope] = []
    pending = list(ast.iter_child_nodes(scope))
    while pending:
        node = pending.pop()
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            nested.append(node)
        else:
            pending.extend(ast.iter_child_nodes(node))
    return nested


def _simple_alias_value(
    value: ast.expr | None, module: _Module, aliases: _SymbolAliases
) -> str | None:
    if isinstance(value, (ast.Name, ast.Attribute)):
        return _resolved_symbol(value, module, aliases)
    return None


def _assignment_alias_updates(
    node: ast.AST, module: _Module, aliases: _SymbolAliases
) -> _SymbolAliases:
    if isinstance(node, ast.Assign):
        canonical = _simple_alias_value(node.value, module, aliases)
        return {
            name: canonical for target in node.targets for name in _bound_names(target)
        }
    if isinstance(node, (ast.AnnAssign, ast.NamedExpr)):
        canonical = _simple_alias_value(node.value, module, aliases)
        return {name: canonical for name in _bound_names(node.target)}
    return {}


def _rebound_names(node: ast.AST) -> list[str]:
    if isinstance(node, ast.AugAssign):
        return _bound_names(node.target)
    if isinstance(node, (ast.For, ast.AsyncFor)):
        return _bound_names(node.target)
    if isinstance(node, ast.withitem) and node.optional_vars is not None:
        return _bound_names(node.optional_vars)
    if isinstance(node, ast.ExceptHandler) and node.name is not None:
        return [node.name]
    return []


def _alias_updates(
    node: ast.AST,
    module: _Module,
    scope: _AuditScope,
    aliases: _SymbolAliases,
) -> _SymbolAliases:
    if isinstance(node, ast.Import):
        return {
            item.asname or item.name.split(".")[0]: (
                item.name if item.asname else item.name.split(".")[0]
            )
            for item in node.names
        }
    if isinstance(node, ast.ImportFrom):
        base = _import_from_base(node, module.name, module.is_package)
        return {
            item.asname or item.name: f"{base}.{item.name}".strip(".")
            for item in node.names
            if item.name != "*"
        }
    assignments = _assignment_alias_updates(node, module, aliases)
    if assignments:
        return assignments
    rebound = _rebound_names(node)
    if rebound:
        return {name: None for name in rebound}
    if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
        canonical = (
            f"{module.name}.{node.name}" if isinstance(scope, ast.Module) else None
        )
        return {node.name: canonical}
    return {}


_ALIAS_BINDING_NODES = (
    ast.Import,
    ast.ImportFrom,
    ast.Assign,
    ast.AnnAssign,
    ast.NamedExpr,
    ast.AugAssign,
    ast.For,
    ast.AsyncFor,
    ast.withitem,
    ast.ExceptHandler,
    ast.ClassDef,
    ast.FunctionDef,
    ast.AsyncFunctionDef,
)
_ALIAS_SNAPSHOT_NODES = (
    ast.Assign,
    ast.AnnAssign,
    ast.NamedExpr,
    ast.Call,
    ast.Attribute,
)
_AliasEvent = tuple[tuple[int, int], _SymbolAliases]


def _alias_events(
    nodes: Sequence[ast.AST],
    module: _Module,
    scope: _AuditScope,
    initial: _SymbolAliases,
) -> tuple[list[_AliasEvent], _SymbolAliases]:
    aliases = dict(initial)
    events: list[_AliasEvent] = []
    for binding in sorted(nodes, key=_position):
        updates = _alias_updates(binding, module, scope, aliases)
        if updates:
            events.append((_end_position(binding), updates))
            aliases.update(updates)
    return events, aliases


def _store_alias_snapshots(
    nodes: Sequence[ast.AST],
    events: list[_AliasEvent],
    initial: _SymbolAliases,
    snapshots: _AliasSnapshots,
) -> None:
    events.sort(key=lambda item: item[0])
    visible = dict(initial)
    event_index = 0
    for node in sorted(nodes, key=_position):
        while event_index < len(events) and events[event_index][0] <= _position(node):
            visible.update(events[event_index][1])
            event_index += 1
        snapshots[id(node)] = dict(visible)


def _nested_alias_context(
    scope: _AuditScope,
    nested: _AuditScope,
    inherited: _SymbolAliases,
    visible_at_definition: _SymbolAliases,
    final_aliases: _SymbolAliases,
    class_noncapture: _SymbolAliases | None,
) -> tuple[_SymbolAliases, _SymbolAliases | None]:
    if isinstance(scope, ast.Module):
        if isinstance(nested, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return final_aliases, None
        return visible_at_definition, final_aliases
    if isinstance(scope, ast.ClassDef):
        noncaptured = class_noncapture or inherited
        nested_noncapture = noncaptured if isinstance(nested, ast.ClassDef) else None
        return noncaptured, nested_noncapture
    if isinstance(nested, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return final_aliases, None
    return visible_at_definition, final_aliases


def _alias_binding_nodes(scope: _AuditScope) -> list[ast.AST]:
    candidates = [*_scope_nodes(scope), *_direct_nested_scopes(scope)]
    return [node for node in candidates if isinstance(node, _ALIAS_BINDING_NODES)]


def _scope_alias_declarations(
    scope: _AuditScope,
) -> tuple[set[str], set[str]]:
    body_nodes = _scope_nodes(scope)
    globals_ = {
        name
        for node in body_nodes
        if isinstance(node, ast.Global)
        for name in node.names
    }
    nonlocals = {
        name
        for node in body_nodes
        if isinstance(node, ast.Nonlocal)
        for name in node.names
    }
    return globals_, nonlocals


def _function_local_alias_names(
    scope: ast.FunctionDef | ast.AsyncFunctionDef,
    binding_nodes: Sequence[ast.AST],
    module: _Module,
    globals_: set[str],
    nonlocals: set[str],
) -> set[str]:
    bound = {
        name
        for node in binding_nodes
        for name in _alias_updates(node, module, scope, {})
    }
    return bound - globals_ - nonlocals


def _nested_runtime_globals(
    scope: _AuditScope,
    runtime_globals: _SymbolAliases | None,
    final_aliases: _SymbolAliases,
    declared_globals: set[str],
) -> _SymbolAliases:
    if isinstance(scope, ast.Module):
        return dict(final_aliases)
    nested_globals = dict(runtime_globals or {})
    nested_globals.update({name: final_aliases.get(name) for name in declared_globals})
    return nested_globals


def _collect_alias_snapshots(
    scope: _AuditScope,
    inherited: _SymbolAliases,
    module: _Module,
    snapshots: _AliasSnapshots,
    class_noncapture: _SymbolAliases | None = None,
    runtime_globals: _SymbolAliases | None = None,
) -> None:
    initial = dict(inherited)
    binding_nodes = _alias_binding_nodes(scope)
    globals_, nonlocals = _scope_alias_declarations(scope)
    if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
        local_names = _function_local_alias_names(
            scope, binding_nodes, module, globals_, nonlocals
        )
        local_names.update(argument.arg for argument in _function_arguments(scope))
        initial.update(dict.fromkeys(local_names))
        initial.update({name: (runtime_globals or {}).get(name) for name in globals_})

    body_nodes = _scope_nodes(scope)
    nested_scopes = _direct_nested_scopes(scope)
    events, final_aliases = _alias_events(binding_nodes, module, scope, initial)
    interesting: list[ast.AST] = [
        node for node in body_nodes if isinstance(node, _ALIAS_SNAPSHOT_NODES)
    ]
    interesting.extend(nested_scopes)
    _store_alias_snapshots(interesting, events, initial, snapshots)
    nested_globals = _nested_runtime_globals(
        scope, runtime_globals, final_aliases, globals_
    )

    for nested in nested_scopes:
        nested_inherited, nested_noncapture = _nested_alias_context(
            scope,
            nested,
            inherited,
            snapshots[id(nested)],
            final_aliases,
            class_noncapture,
        )
        _collect_alias_snapshots(
            nested,
            nested_inherited,
            module,
            snapshots,
            nested_noncapture,
            nested_globals,
        )


def _symbol_alias_snapshots(module: _Module) -> _AliasSnapshots:
    """Resolve aliases by execution point, including late closure cells."""

    snapshots: _AliasSnapshots = {}
    _collect_alias_snapshots(module.tree, {}, module, snapshots)
    return snapshots


def _module_final_aliases(module: _Module) -> _SymbolAliases:
    return _alias_events(_alias_binding_nodes(module.tree), module, module.tree, {})[1]


def _uses_future_annotations(module: _Module) -> bool:
    return any(
        isinstance(node, ast.ImportFrom)
        and node.module == "__future__"
        and any(item.name == "annotations" for item in node.names)
        for node in module.tree.body
    )


def _final_annotation_aliases(
    modules: dict[str, _Module],
) -> _FinalAnnotationAliases:
    return {
        module.name: _module_final_aliases(module)
        for module in modules.values()
        if _uses_future_annotations(module)
    }


def _type_alias_targets(modules: dict[str, _Module]) -> _TypeAliases:
    targets: _TypeAliases = {}
    for module in modules.values():
        final_aliases = _module_final_aliases(module)
        targets.update(
            {
                f"{module.name}.{name}": target
                for name, target in final_aliases.items()
                if target is not None
            }
        )
    return targets


def _function_arguments(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[ast.arg]:
    arguments = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
    arguments.extend(
        argument
        for argument in (node.args.vararg, node.args.kwarg)
        if argument is not None
    )
    return arguments


def _assignment_parts(
    item: ast.AST,
) -> tuple[list[ast.expr], ast.expr | None, ast.expr | None] | None:
    if isinstance(item, ast.Assign):
        return item.targets, None, item.value
    if isinstance(item, ast.AnnAssign):
        return [item.target], item.annotation, item.value
    if isinstance(item, ast.NamedExpr):
        return [item.target], None, item.value
    return None


_AuditScope = ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef


def _scope_nodes(scope: _AuditScope) -> list[ast.AST]:
    """Return one lexical body's nodes without entering nested definitions."""

    return _lexical_body_nodes(scope)


def _initial_scope_env(
    node: _AuditScope,
    class_info: _ClassInfo | None,
    module: _Module,
    known: frozenset[str],
    aliases: _SymbolAliases,
    type_aliases: _TypeAliases,
    inherited: dict[str, frozenset[str]],
) -> dict[str, frozenset[str]]:
    arguments = (
        _function_arguments(node)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        else []
    )
    env = dict(inherited)
    for argument in arguments:
        types = _annotation_types(
            argument.annotation, module, known, aliases, type_aliases
        )
        if types:
            env[argument.arg] = types
        else:
            env.pop(argument.arg, None)
    if class_info is not None and arguments:
        env.setdefault(arguments[0].arg, frozenset({class_info.canonical}))
    return env


def _scope_env(
    node: _AuditScope,
    class_info: _ClassInfo | None,
    module: _Module,
    classes: dict[str, _ClassInfo],
    function_returns: _FunctionReturnTypes,
    alias_snapshots: dict[int, _SymbolAliases],
    type_aliases: _TypeAliases,
    final_annotation_aliases: _FinalAnnotationAliases,
    inherited: dict[str, frozenset[str]],
) -> dict[str, frozenset[str]]:
    known = frozenset(classes)
    env = _initial_scope_env(
        node,
        class_info,
        module,
        known,
        final_annotation_aliases.get(module.name, alias_snapshots.get(id(node), {})),
        type_aliases,
        inherited,
    )
    scope_nodes = _scope_nodes(node)
    changed = True
    while changed:
        changed = False
        for item in scope_nodes:
            parts = _assignment_parts(item)
            if parts is None:
                continue
            targets, annotation, value = parts
            aliases = alias_snapshots.get(id(item), {})
            annotation_aliases = final_annotation_aliases.get(module.name, aliases)
            types = _annotation_types(
                annotation, module, known, annotation_aliases, type_aliases
            )
            if value is not None:
                types |= _expression_types(
                    value,
                    module,
                    env,
                    classes,
                    function_returns,
                    aliases,
                    type_aliases,
                )
            for target in targets:
                if not isinstance(target, ast.Name):
                    continue
                merged = env.get(target.id, frozenset()) | types
                if merged != env.get(target.id, frozenset()):
                    env[target.id] = merged
                    changed = True
    return env


def _access_mode(node: ast.Attribute) -> str:
    if isinstance(node.ctx, (ast.Store, ast.Del)):
        return "write"
    return "read"


def _target_fields(
    classes: dict[str, _ClassInfo],
    canonicals: dict[str, str],
) -> tuple[dict[str, tuple[str, ...]], list[str]]:
    fields: dict[str, tuple[str, ...]] = {}
    violations: list[str] = []
    for target, canonical in canonicals.items():
        owners = sorted(
            info.module for info in classes.values() if info.node.name == target
        )
        if owners != [TARGET_OWNERS[target]]:
            violations.append(f"{target}: definition owners {owners!r}")
        else:
            info = classes[canonical]
            fields[target] = info.fields
            if not info.field_inventory_complete:
                violations.append(
                    f"{target}: dataclass field inventory has an unresolved base"
                )
    return fields, violations


def _module_scopes(
    module: _Module,
    classes: dict[str, _ClassInfo],
) -> list[tuple[_AuditScope, _ClassInfo | None]]:
    class_by_node = {id(info.node): info for info in classes.values()}
    scopes: list[tuple[_AuditScope, _ClassInfo | None]] = [(module.tree, None)]

    def collect(node: ast.AST, class_info: _ClassInfo | None) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                nested_class = class_by_node.get(id(child))
                scopes.append((child, nested_class))
                collect(child, nested_class)
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                scopes.append((child, class_info))
                collect(child, None)
            else:
                collect(child, class_info)

    collect(module.tree, None)
    return scopes


def _record_attribute_access(
    node: ast.Attribute,
    module: _Module,
    receiver_types: frozenset[str],
    classes: dict[str, _ClassInfo],
    canonical_to_target: dict[str, str],
    accesses: set[_Access],
    violations: list[str],
) -> None:
    for receiver_type in receiver_types:
        if receiver_type in {_CYCLIC_TYPE_ALIAS, _UNRESOLVED_TYPE_ALIAS}:
            if any(node.attr in fields for fields in MEMBER_LIST.values()):
                state = receiver_type.removeprefix("<").removesuffix(">")
                violations.append(f"{module.name}:{node.lineno}: {state}")
            continue
        target = canonical_to_target.get(receiver_type)
        if target is None:
            continue
        if node.attr not in classes[receiver_type].members:
            violations.append(
                f"{module.name}:{node.lineno}: {target}.{node.attr} is unowned"
            )
        if node.attr in MEMBER_LIST[target]:
            accesses.add(_Access(target, node.attr, module.name, _access_mode(node)))


_DYNAMIC_FUNCTIONS = frozenset({"builtins.getattr", "builtins.setattr"})


def _dynamic_function(
    node: ast.Call,
    module: _Module,
    aliases: _SymbolAliases,
    type_aliases: _TypeAliases,
) -> str | None:
    function = _canonical_expression_symbol(
        node.func, module, aliases, type_aliases, _DYNAMIC_FUNCTIONS
    )
    if function in _DYNAMIC_FUNCTIONS:
        return function
    return None


def _record_dynamic_access(
    node: ast.Call,
    function: str,
    module: _Module,
    receiver_types: frozenset[str],
    classes: dict[str, _ClassInfo],
    canonical_to_target: dict[str, str],
    accesses: set[_Access],
    violations: list[str],
) -> None:
    literal = node.args[1] if len(node.args) >= 2 else None
    for receiver_type in receiver_types:
        if receiver_type in {_CYCLIC_TYPE_ALIAS, _UNRESOLVED_TYPE_ALIAS}:
            if (
                isinstance(literal, ast.Constant)
                and isinstance(literal.value, str)
                and any(literal.value in fields for fields in MEMBER_LIST.values())
            ):
                state = receiver_type.removeprefix("<").removesuffix(">")
                violations.append(f"{module.name}:{node.lineno}: {state}")
            continue
        target = canonical_to_target.get(receiver_type)
        if target is None:
            continue
        if not isinstance(literal, ast.Constant) or not isinstance(literal.value, str):
            violations.append(
                f"{module.name}:{node.lineno}: unknown dynamic access on {target}"
            )
            continue
        field = literal.value
        if field not in classes[receiver_type].members:
            violations.append(
                f"{module.name}:{node.lineno}: {target}.{field} is unowned"
            )
        if field in MEMBER_LIST[target]:
            mode = "write" if function.endswith("setattr") else "read"
            accesses.add(_Access(target, field, module.name, mode))


def _audit_scope(
    scope: _AuditScope,
    class_info: _ClassInfo | None,
    module: _Module,
    classes: dict[str, _ClassInfo],
    function_returns: _FunctionReturnTypes,
    alias_snapshots: dict[int, _SymbolAliases],
    type_aliases: _TypeAliases,
    final_annotation_aliases: _FinalAnnotationAliases,
    inherited: dict[str, frozenset[str]],
    canonical_to_target: dict[str, str],
    accesses: set[_Access],
    violations: list[str],
) -> dict[str, frozenset[str]]:
    env = _scope_env(
        scope,
        class_info,
        module,
        classes,
        function_returns,
        alias_snapshots,
        type_aliases,
        final_annotation_aliases,
        inherited,
    )
    for node in _scope_nodes(scope):
        aliases = alias_snapshots.get(id(node), {})
        if isinstance(node, ast.Attribute):
            receiver_types = _expression_types(
                node.value,
                module,
                env,
                classes,
                function_returns,
                aliases,
                type_aliases,
            )
            _record_attribute_access(
                node,
                module,
                receiver_types,
                classes,
                canonical_to_target,
                accesses,
                violations,
            )
        if not isinstance(node, ast.Call) or not node.args:
            continue
        function = _dynamic_function(node, module, aliases, type_aliases)
        if function is None:
            continue
        receiver_types = _expression_types(
            node.args[0],
            module,
            env,
            classes,
            function_returns,
            aliases,
            type_aliases,
        )
        _record_dynamic_access(
            node,
            function,
            module,
            receiver_types,
            classes,
            canonical_to_target,
            accesses,
            violations,
        )
    return env


def _audit_inventory(modules: dict[str, _Module]) -> _InventoryResult:
    alias_snapshots_by_module = {
        module.name: _symbol_alias_snapshots(module) for module in modules.values()
    }
    type_aliases = _type_alias_targets(modules)
    annotation_aliases = _final_annotation_aliases(modules)
    classes = _build_classes(
        modules, alias_snapshots_by_module, type_aliases, annotation_aliases
    )
    function_returns = _function_return_types(
        modules,
        classes,
        alias_snapshots_by_module,
        type_aliases,
        annotation_aliases,
    )
    canonicals = {name: f"{owner}.{name}" for name, owner in TARGET_OWNERS.items()}
    fields, violations = _target_fields(classes, canonicals)
    canonical_to_target = {value: key for key, value in canonicals.items()}
    accesses: set[_Access] = set()
    class_by_node = {id(info.node): info for info in classes.values()}
    for module in modules.values():
        alias_snapshots = alias_snapshots_by_module[module.name]

        def audit_nested(
            scope: _AuditScope,
            class_info: _ClassInfo | None,
            closure_env: dict[str, frozenset[str]],
        ) -> None:
            env = _audit_scope(
                scope,
                class_info,
                module,
                classes,
                function_returns,
                alias_snapshots,
                type_aliases,
                annotation_aliases,
                closure_env,
                canonical_to_target,
                accesses,
                violations,
            )
            nested_closure = env if not isinstance(scope, ast.ClassDef) else closure_env
            for nested in _direct_nested_scopes(scope):
                nested_class = (
                    class_by_node.get(id(nested))
                    if isinstance(nested, ast.ClassDef)
                    else class_info
                    if isinstance(scope, ast.ClassDef)
                    else None
                )
                audit_nested(nested, nested_class, nested_closure)

        audit_nested(module.tree, None, {})
    return _InventoryResult(fields, frozenset(accesses), tuple(sorted(set(violations))))


# External writes are deliberately explicit. RunControlState is the one shared
# mutable protocol, so its measured writers are listed by member and module;
# TerminalUi and CodingSession retain mutations only in their definition owner.
ALLOWED_WRITES = {
    _Access("TerminalUi", "components", "pipy_harness.native.tui", "write"),
    _Access(
        "RunControlState",
        "_session_tree",
        "pipy_harness.native.repl.loop_scope",
        "write",
    ),
    _Access(
        "RunControlState",
        "agent_settled_pending",
        "pipy_harness.native.repl.loop_step",
        "write",
    ),
    _Access(
        "RunControlState",
        "extension_in_agent_turn",
        "pipy_harness.native.repl.loop_scope",
        "write",
    ),
    _Access(
        "RunControlState",
        "extension_in_agent_turn",
        "pipy_harness.native.repl.loop_step",
        "write",
    ),
    _Access("RunControlState", "line", "pipy_harness.native.repl.loop_step", "write"),
    _Access(
        "RunControlState",
        "line",
        "pipy_harness.native.repl.provider_config_commands",
        "write",
    ),
    _Access(
        "RunControlState", "package_roots", "pipy_harness.native.repl.reload", "write"
    ),
    _Access(
        "RunControlState",
        "pending_prefill",
        "pipy_harness.native.repl.loop_step",
        "write",
    ),
    _Access(
        "RunControlState",
        "pending_prefill",
        "pipy_harness.native.repl.session_commands",
        "write",
    ),
    _Access(
        "RunControlState",
        "tree_filter_mode",
        "pipy_harness.native.repl.session_commands",
        "write",
    ),
    _Access(
        "RunControlState",
        "workspace_resources",
        "pipy_harness.native.repl.reload",
        "write",
    ),
    _Access(
        "CodingSession", "_coding_state", "pipy_harness.native.coding.session", "write"
    ),
    _Access(
        "CodingSession", "implicit_trust", "pipy_harness.native.coding.session", "write"
    ),
}


def _assert_inventory(result: _InventoryResult) -> None:
    assert result.violations == ()
    assert result.fields == MEMBER_LIST
    writes = {access for access in result.accesses if access.mode == "write"}
    assert writes == ALLOWED_WRITES, (
        "field mutation moved outside its measured owner set; do not add a "
        f"second owner module: {sorted(writes ^ ALLOWED_WRITES)!r}"
    )
    for target, expected in EXPECTED_ACCESSED_FIELDS.items():
        accessed = {
            access.field for access in result.accesses if access.target == target
        }
        assert accessed == expected, f"{target} access inventory drifted"


def test_final_field_inventory_and_typed_production_accesses_are_exact() -> None:
    _assert_inventory(_audit_inventory(_load_modules(PRODUCTION_ROOT)))


def _synthetic_modules(tmp_path: Path, files: dict[str, str]) -> dict[str, _Module]:
    root = tmp_path / "synthetic"
    for relative, source in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return _load_modules(root)


def _synthetic_base() -> dict[str, str]:
    return {
        "pipy_harness/native/tui.py": """
from dataclasses import dataclass
@dataclass
class TerminalUi:
    input_stream: object
    terminal_stream: object
    cwd: object
    include_workspace_defaults: bool
    runtime_label: str
    footer_lines: tuple[str, str]
    components: object
    available_provider_count: int
    keybindings_manager: object

    def replace_components(self) -> None:
        receiver = self
        setattr(receiver, "components", object())
""",
        "pipy_harness/native/repl/loop_scope.py": """
from dataclasses import dataclass
@dataclass
class RunControlState:
    coding_effects: object
    _session_tree: object
    tree_filter_mode: str
    pending_prefill: str | None
    package_roots: object
    workspace_resources: object
    generation_ref: object
    agent_settled_pending: bool
    extension_in_agent_turn: bool
    line: str
""",
        "pipy_harness/native/coding/session.py": """
from dataclasses import dataclass
@dataclass
class CodingSession:
    tool_registry: object
    tool_budget: int
    workspace_root: object
    input_runtime: str
    reference_roots: tuple[object, ...]
    provider_state: object
    clipboard_copy: object
    clipboard_image_read: object
    prompt_history_store: object
    keybindings_manager: object
    settings_manager: object
    resume_context: object
    resume_branch_label: str | None
    native_session: object
    automation_observer: object
    agent_event_sink: object
    abort_event: object
    resource_options: object
    initial_messages: tuple[str, ...]
    tool_filter_options: object
    verbose_startup: bool
    initial_extension_batch: object
    _coding_state: object
    implicit_trust: object
""",
    }


def test_field_inventory_resolves_relative_import_from_package_initializer(
    tmp_path: Path,
) -> None:
    files = _synthetic_base()
    files["pipy_harness/native/__init__.py"] = """
from .tui import TerminalUi as Shell

value: Shell
value.cwd
"""
    result = _audit_inventory(_synthetic_modules(tmp_path, files))

    assert result.violations == ()
    assert (
        _Access("TerminalUi", "cwd", "pipy_harness.native", "read") in result.accesses
    )


def test_field_inventory_resolves_synthetic_import_and_receiver_aliases(
    tmp_path: Path,
) -> None:
    files = _synthetic_base()
    files["pipy_harness/native/consumer.py"] = """
from pipy_harness.native.tui import TerminalUi as Shell

def inspect(value: Shell) -> tuple[object, object]:
    receiver = value
    return receiver.cwd, getattr(receiver, "runtime_label")
"""
    result = _audit_inventory(_synthetic_modules(tmp_path, files))
    assert result.violations == ()
    assert {
        (access.target, access.field, access.mode)
        for access in result.accesses
        if access.module == "pipy_harness.native.consumer"
    } == {
        ("TerminalUi", "cwd", "read"),
        ("TerminalUi", "runtime_label", "read"),
    }


def test_field_inventory_resolves_direct_assigned_type_alias_in_source_order(
    tmp_path: Path,
) -> None:
    files = _synthetic_base()
    files["pipy_harness/native/direct_alias.py"] = """
from pipy_harness.native.tui import TerminalUi

def before_assignment(value: Shell) -> object:
    return value.footer_lines

Shell = TerminalUi

def after_assignment(value: Shell) -> object:
    return value.cwd
"""
    result = _audit_inventory(_synthetic_modules(tmp_path, files))
    module = "pipy_harness.native.direct_alias"

    assert result.violations == ()
    assert _Access("TerminalUi", "cwd", module, "read") in result.accesses
    assert _Access("TerminalUi", "footer_lines", module, "read") not in result.accesses


def test_field_inventory_resolves_one_hop_imported_type_reexport(
    tmp_path: Path,
) -> None:
    files = _synthetic_base()
    files["pipy_harness/native/ui_alias.py"] = """
from pipy_harness.native.tui import TerminalUi as Shell
"""
    files["pipy_harness/native/reexport_consumer.py"] = """
from pipy_harness.native.ui_alias import Shell

def inspect(value: Shell) -> object:
    return value.cwd
"""
    result = _audit_inventory(_synthetic_modules(tmp_path, files))

    assert result.violations == ()
    assert (
        _Access(
            "TerminalUi",
            "cwd",
            "pipy_harness.native.reexport_consumer",
            "read",
        )
        in result.accesses
    )


@pytest.mark.parametrize("reexport_count", [1, 2])
def test_field_inventory_resolves_constructor_result_through_reexports(
    tmp_path: Path, reexport_count: int
) -> None:
    files = _synthetic_base()
    files["pipy_harness/native/ui_alias.py"] = """
from pipy_harness.native.tui import TerminalUi as Shell
"""
    import_module = "ui_alias"
    if reexport_count == 2:
        files["pipy_harness/native/ui_alias_reexport.py"] = """
from pipy_harness.native.ui_alias import Shell
"""
        import_module = "ui_alias_reexport"
    files["pipy_harness/native/constructor_consumer.py"] = f"""
from pipy_harness.native.{import_module} import Shell

value = Shell()
value.cwd
"""

    result = _audit_inventory(_synthetic_modules(tmp_path, files))

    assert result.violations == ()
    assert (
        _Access(
            "TerminalUi",
            "cwd",
            "pipy_harness.native.constructor_consumer",
            "read",
        )
        in result.accesses
    )


def test_field_inventory_resolves_transitive_imported_type_reexport(
    tmp_path: Path,
) -> None:
    files = _synthetic_base()
    files["pipy_harness/native/ui_alias.py"] = """
from pipy_harness.native.tui import TerminalUi as Shell
"""
    files["pipy_harness/native/ui_alias_reexport.py"] = """
from pipy_harness.native.ui_alias import Shell
"""
    files["pipy_harness/native/transitive_consumer.py"] = """
from pipy_harness.native.ui_alias_reexport import Shell

def inspect(value: Shell) -> object:
    return value.cwd
"""
    result = _audit_inventory(_synthetic_modules(tmp_path, files))

    assert result.violations == ()
    assert (
        _Access(
            "TerminalUi",
            "cwd",
            "pipy_harness.native.transitive_consumer",
            "read",
        )
        in result.accesses
    )


@pytest.mark.parametrize(
    ("case", "alias_files", "expected"),
    [
        (
            "cyclic",
            {
                "cycle_a.py": "from pipy_harness.native.cycle_b import Shell\n",
                "cycle_b.py": "from pipy_harness.native.cycle_a import Shell\n",
            },
            "cyclic-type-alias",
        ),
        (
            "broken",
            {"broken_alias.py": "Shell = MissingShell\n"},
            "unresolved-type-alias",
        ),
    ],
)
def test_field_inventory_fails_closed_for_target_relevant_invalid_type_alias(
    tmp_path: Path,
    case: str,
    alias_files: dict[str, str],
    expected: str,
) -> None:
    files = _synthetic_base()
    for relative, source in alias_files.items():
        files[f"pipy_harness/native/{relative}"] = source
    export_module = "cycle_a" if case == "cyclic" else "broken_alias"
    files[f"pipy_harness/native/{case}_alias_consumer.py"] = f"""
from pipy_harness.native.{export_module} import Shell

def inspect(value: Shell) -> object:
    return value.cwd
"""
    result = _audit_inventory(_synthetic_modules(tmp_path, files))

    assert any(expected in violation for violation in result.violations)


@pytest.mark.parametrize(
    ("future_import", "expected_fields"),
    [
        ("", set()),
        ("from __future__ import annotations\n", {"cwd", "footer_lines"}),
    ],
)
def test_field_inventory_late_annotation_alias_depends_on_future_annotations(
    tmp_path: Path, future_import: str, expected_fields: set[str]
) -> None:
    files = _synthetic_base()
    files["pipy_harness/native/late_annotation.py"] = f"""
{future_import}from pipy_harness.native.tui import TerminalUi

before: Shell
before.footer_lines

def inspect(value: Shell) -> object:
    return value.cwd

Shell = TerminalUi
"""
    result = _audit_inventory(_synthetic_modules(tmp_path, files))
    module = "pipy_harness.native.late_annotation"

    assert result.violations == ()
    assert {
        access.field for access in result.accesses if access.module == module
    } == expected_fields


def test_field_inventory_fails_closed_for_cyclic_constructor_reexport(
    tmp_path: Path,
) -> None:
    files = _synthetic_base()
    files["pipy_harness/native/cycle_a.py"] = (
        "from pipy_harness.native.cycle_b import Shell\n"
    )
    files["pipy_harness/native/cycle_b.py"] = (
        "from pipy_harness.native.cycle_a import Shell\n"
    )
    files["pipy_harness/native/cyclic_constructor.py"] = """
from pipy_harness.native.cycle_a import Shell

value = Shell()
value.cwd
"""
    result = _audit_inventory(_synthetic_modules(tmp_path, files))

    assert any("cyclic-type-alias" in violation for violation in result.violations)


def test_field_inventory_ignores_unrelated_unresolved_annotation_alias(
    tmp_path: Path,
) -> None:
    files = _synthetic_base()
    files["pipy_harness/native/ordinary_alias.py"] = "Ordinary = MissingType\n"
    files["pipy_harness/native/ordinary_consumer.py"] = """
from pipy_harness.native.ordinary_alias import Ordinary

def inspect(value: Ordinary) -> object:
    return value.payload
"""
    result = _audit_inventory(_synthetic_modules(tmp_path, files))

    assert result.violations == ()
    assert not any(
        access.module == "pipy_harness.native.ordinary_consumer"
        for access in result.accesses
    )


@pytest.mark.parametrize("container", ["module", "class"])
def test_field_inventory_audits_synthetic_module_and_class_bodies(
    tmp_path: Path, container: str
) -> None:
    files = _synthetic_base()
    body = """
value: Shell
value.cwd
value.components = object()
value.rogue
getattr(value, dynamic_name)
"""
    source = "from pipy_harness.native.tui import TerminalUi as Shell\n"
    if container == "module":
        source += body
    else:
        source += "class Bypass:\n" + "".join(
            f"    {line}\n" for line in body.strip().splitlines()
        )
    module_name = f"pipy_harness.native.{container}_body_bypass"
    files[f"pipy_harness/native/{container}_body_bypass.py"] = source

    result = _audit_inventory(_synthetic_modules(tmp_path, files))

    assert {
        (access.field, access.mode)
        for access in result.accesses
        if access.module == module_name
    } == {("components", "write"), ("cwd", "read")}
    assert any("TerminalUi.rogue is unowned" in item for item in result.violations)
    assert any(
        "unknown dynamic access on TerminalUi" in item for item in result.violations
    )


def test_field_inventory_partitions_nested_function_bodies_once(
    tmp_path: Path,
) -> None:
    files = _synthetic_base()
    files["pipy_harness/native/nested_scope.py"] = """
from pipy_harness.native.tui import TerminalUi as Shell

def outer() -> None:
    def inspect(value: Shell) -> object:
        return value.cwd
"""
    modules = _synthetic_modules(tmp_path, files)
    module = modules["pipy_harness.native.nested_scope"]
    classes = _build_classes(modules)

    visits = [
        node
        for scope, _ in _module_scopes(module, classes)
        for node in _scope_nodes(scope)
        if isinstance(node, ast.Attribute) and node.attr == "cwd"
    ]
    assert len(visits) == 1
    result = _audit_inventory(modules)
    assert (
        _Access("TerminalUi", "cwd", "pipy_harness.native.nested_scope", "read")
        in result.accesses
    )


def test_field_inventory_resolves_builtin_dynamic_function_aliases(
    tmp_path: Path,
) -> None:
    files = _synthetic_base()
    files["pipy_harness/native/dynamic_aliases.py"] = """
from pipy_harness.native.tui import TerminalUi as Shell
read = getattr
write = setattr

def inspect(value: Shell, name: str) -> None:
    read(value, "cwd")
    write(value, "components", object())
    read(value, name)
    write(value, "rogue", object())
"""
    result = _audit_inventory(_synthetic_modules(tmp_path, files))

    assert {
        (access.field, access.mode)
        for access in result.accesses
        if access.module == "pipy_harness.native.dynamic_aliases"
    } == {("components", "write"), ("cwd", "read")}
    assert any("TerminalUi.rogue is unowned" in item for item in result.violations)
    assert any(
        "unknown dynamic access on TerminalUi" in item for item in result.violations
    )


@pytest.mark.parametrize("reexport_count", [1, 2])
def test_field_inventory_resolves_dynamic_functions_through_reexports(
    tmp_path: Path, reexport_count: int
) -> None:
    files = _synthetic_base()
    files["pipy_harness/native/dynamic_exports.py"] = """
reader = getattr
writer = setattr
"""
    import_module = "dynamic_exports"
    if reexport_count == 2:
        files["pipy_harness/native/dynamic_reexports.py"] = """
from pipy_harness.native.dynamic_exports import reader, writer
"""
        import_module = "dynamic_reexports"
    files["pipy_harness/native/dynamic_reexport_consumer.py"] = f"""
from pipy_harness.native.{import_module} import reader, writer
from pipy_harness.native.coding.session import CodingSession
from pipy_harness.native.repl.loop_scope import RunControlState
from pipy_harness.native.tui import TerminalUi

def inspect(
    ui: TerminalUi, control: RunControlState, session: CodingSession
) -> None:
    reader(ui, "cwd")
    reader(control, "line")
    reader(session, "tool_budget")
    writer(session, "implicit_trust", object())
"""
    result = _audit_inventory(_synthetic_modules(tmp_path, files))
    module = "pipy_harness.native.dynamic_reexport_consumer"
    external_write = _Access("CodingSession", "implicit_trust", module, "write")

    assert result.violations == ()
    assert {access for access in result.accesses if access.module == module} == {
        _Access("TerminalUi", "cwd", module, "read"),
        _Access("RunControlState", "line", module, "read"),
        _Access("CodingSession", "tool_budget", module, "read"),
        external_write,
    }
    assert external_write not in ALLOWED_WRITES


def test_field_inventory_dynamic_function_reexport_cycle_is_not_a_false_positive(
    tmp_path: Path,
) -> None:
    files = _synthetic_base()
    files["pipy_harness/native/dynamic_cycle_a.py"] = """
from pipy_harness.native.dynamic_cycle_b import reader, writer
"""
    files["pipy_harness/native/dynamic_cycle_b.py"] = """
from pipy_harness.native.dynamic_cycle_a import reader, writer
"""
    files["pipy_harness/native/dynamic_cycle_consumer.py"] = """
from pipy_harness.native.dynamic_cycle_a import reader, writer
from pipy_harness.native.tui import TerminalUi

def inspect(value: TerminalUi) -> None:
    reader(value, "cwd")
    writer(value, "terminal_stream", object())
"""
    result = _audit_inventory(_synthetic_modules(tmp_path, files))
    module = "pipy_harness.native.dynamic_cycle_consumer"

    assert result.violations == ()
    assert not any(access.module == module for access in result.accesses)


def test_field_inventory_uses_late_bound_closure_dynamic_aliases(
    tmp_path: Path,
) -> None:
    files = _synthetic_base()
    files["pipy_harness/native/late_closure_dynamic.py"] = """
from pipy_harness.native.tui import TerminalUi as Shell

def enclosing(value: Shell) -> None:
    def inspect() -> None:
        reader(value, "cwd")
        writer(value, "components", object())

    reader = getattr
    writer = setattr
    inspect()
"""
    result = _audit_inventory(_synthetic_modules(tmp_path, files))
    module = "pipy_harness.native.late_closure_dynamic"

    assert {
        (access.field, access.mode)
        for access in result.accesses
        if access.module == module
    } == {("components", "write"), ("cwd", "read")}


def test_field_inventory_with_alias_rebinding_preserves_source_order(
    tmp_path: Path,
) -> None:
    files = _synthetic_base()
    files["pipy_harness/native/with_alias_order.py"] = """
from pipy_harness.native.tui import TerminalUi as Shell

before = Shell()
before.cwd
with object() as Shell:
    inside = Shell()
    inside.components

after = Shell()
after.footer_lines
"""
    result = _audit_inventory(_synthetic_modules(tmp_path, files))
    module = "pipy_harness.native.with_alias_order"

    assert result.violations == ()
    assert {access.field for access in result.accesses if access.module == module} == {
        "cwd"
    }


def test_field_inventory_keeps_dynamic_alias_timing_and_scope_semantics(
    tmp_path: Path,
) -> None:
    files = _synthetic_base()
    files["pipy_harness/native/dynamic_alias_scope.py"] = """
from pipy_harness.native.tui import TerminalUi as Shell

value: Shell
reader = object
reader(value, "footer_lines")
reader = getattr
reader(value, "cwd")
reader = object

def local_shadow(value: Shell) -> None:
    reader(value, "runtime_label")
    reader = getattr

def enclosing(value: Shell) -> None:
    reader = getattr

    def global_reader() -> None:
        global reader
        reader(value, "available_provider_count")

    def nonlocal_rebind() -> None:
        nonlocal reader

        def inspect() -> None:
            reader(value, "components")

        reader = getattr
        inspect()
"""
    result = _audit_inventory(_synthetic_modules(tmp_path, files))
    module = "pipy_harness.native.dynamic_alias_scope"

    assert {access.field for access in result.accesses if access.module == module} == {
        "components",
        "cwd",
    }


@pytest.mark.parametrize(
    ("case", "extra", "message"),
    [
        (
            "unowned-field",
            "def bad(value: TerminalUi) -> None:\n    value.rogue\n",
            "is unowned",
        ),
        (
            "external-mutation-through-alias",
            "def bad(value: TerminalUi) -> None:\n    alias = value\n    alias.cwd = object()\n",
            "",
        ),
        (
            "unknown-dynamic-access",
            "def bad(value: TerminalUi, name: str) -> object:\n    return getattr(value, name)\n",
            "unknown dynamic access",
        ),
    ],
)
def test_field_inventory_rejects_synthetic_access_bypasses(
    tmp_path: Path, case: str, extra: str, message: str
) -> None:
    files = _synthetic_base()
    files[f"pipy_harness/native/{case.replace('-', '_')}.py"] = (
        "from pipy_harness.native.tui import TerminalUi as Shell\n"
        + extra.replace("TerminalUi", "Shell")
    )
    result = _audit_inventory(_synthetic_modules(tmp_path, files))
    if case == "external-mutation-through-alias":
        writes = {access for access in result.accesses if access.mode == "write"}
        assert (
            _Access(
                "TerminalUi",
                "cwd",
                f"pipy_harness.native.{case.replace('-', '_')}",
                "write",
            )
            in writes
        )
        assert {
            access
            for access in writes
            if access.module.endswith(case.replace("-", "_"))
        } == {
            _Access(
                "TerminalUi",
                "cwd",
                "pipy_harness.native.external_mutation_through_alias",
                "write",
            )
        }
    else:
        assert any(message in violation for violation in result.violations)


def test_field_inventory_rejects_module_function_return_receiver_mutation(
    tmp_path: Path,
) -> None:
    files = _synthetic_base()
    files["pipy_harness/native/function_receiver.py"] = """
from pipy_harness.native.tui import TerminalUi as Shell

def get_ui() -> Shell:
    raise RuntimeError

def bypass() -> None:
    get_ui().components = object()
"""
    result = _audit_inventory(_synthetic_modules(tmp_path, files))

    write = _Access(
        "TerminalUi",
        "components",
        "pipy_harness.native.function_receiver",
        "write",
    )
    assert {
        access
        for access in result.accesses
        if access.module == "pipy_harness.native.function_receiver"
        and access.mode == "write"
    } == {write}


def test_field_inventory_inherits_closure_types_without_leaking_inner_locals(
    tmp_path: Path,
) -> None:
    files = _synthetic_base()
    files["pipy_harness/native/closure_receiver.py"] = """
from pipy_harness.native.tui import TerminalUi as Shell

def outer(value: Shell) -> None:
    captured: Shell = value

    def bypass() -> None:
        inner_only: Shell
        captured.components = object()
        captured.rogue
        inner_only.cwd

    inner_only.footer_lines
"""
    result = _audit_inventory(_synthetic_modules(tmp_path, files))
    module = "pipy_harness.native.closure_receiver"

    assert _Access("TerminalUi", "components", module, "write") in result.accesses
    assert _Access("TerminalUi", "cwd", module, "read") in result.accesses
    assert not any(
        access.module == module and access.field == "footer_lines"
        for access in result.accesses
    )
    assert any(
        f"{module}:10: TerminalUi.rogue is unowned" == violation
        for violation in result.violations
    )


def test_field_inventory_exposes_typed_module_globals_to_functions_only(
    tmp_path: Path,
) -> None:
    files = _synthetic_base()
    files["pipy_harness/native/module_global_receiver.py"] = """
from pipy_harness.native.tui import TerminalUi as Shell

ui: Shell

def mutate() -> None:
    ui.components = object()

class ClassScopeDoesNotClose:
    class_only: Shell

    def inspect(self) -> object:
        return class_only.runtime_label

def outer() -> None:
    def inspect() -> object:
        inner_only: Shell
        return ui.cwd, inner_only.cwd

    inner_only.footer_lines
"""
    result = _audit_inventory(_synthetic_modules(tmp_path, files))
    module = "pipy_harness.native.module_global_receiver"

    assert _Access("TerminalUi", "components", module, "write") in result.accesses
    assert _Access("TerminalUi", "cwd", module, "read") in result.accesses
    assert not any(
        access.module == module and access.field in {"footer_lines", "runtime_label"}
        for access in result.accesses
    )
    assert {
        access
        for access in result.accesses
        if access.module == module and access.mode == "write"
    } == {_Access("TerminalUi", "components", module, "write")}


def test_field_inventory_propagates_chained_receiver_assignments(
    tmp_path: Path,
) -> None:
    files = _synthetic_base()
    files["pipy_harness/native/chained_receiver.py"] = """
from pipy_harness.native.tui import TerminalUi as Shell

def bypass(value: Shell) -> None:
    first = second = value
    second.components = object()
    first.rogue
"""
    result = _audit_inventory(_synthetic_modules(tmp_path, files))
    module = "pipy_harness.native.chained_receiver"

    assert _Access("TerminalUi", "components", module, "write") in result.accesses
    assert any(
        f"{module}:7: TerminalUi.rogue is unowned" == violation
        for violation in result.violations
    )


def test_field_inventory_resolves_imported_function_return_receivers(
    tmp_path: Path,
) -> None:
    files = _synthetic_base()
    files["pipy_harness/native/ui_factory.py"] = """
from pipy_harness.native.tui import TerminalUi

def get_ui() -> TerminalUi:
    raise RuntimeError
"""
    files["pipy_harness/native/imported_return_receiver.py"] = """
from pipy_harness.native.ui_factory import get_ui

def bypass() -> None:
    get_ui().components = object()
    get_ui().rogue
"""
    result = _audit_inventory(_synthetic_modules(tmp_path, files))
    module = "pipy_harness.native.imported_return_receiver"

    assert _Access("TerminalUi", "components", module, "write") in result.accesses
    assert any(
        f"{module}:6: TerminalUi.rogue is unowned" == violation
        for violation in result.violations
    )


def test_field_inventory_resolves_awaited_async_factory_receiver(
    tmp_path: Path,
) -> None:
    files = _synthetic_base()
    files["pipy_harness/native/state_factory.py"] = """
from pipy_harness.native.repl.loop_scope import RunControlState

async def make_state() -> RunControlState:
    raise RuntimeError
"""
    files["pipy_harness/native/awaited_factory_receiver.py"] = """
from pipy_harness.native.state_factory import make_state

async def bypass() -> None:
    state = await make_state()
    state.line = "outside owner"
"""
    result = _audit_inventory(_synthetic_modules(tmp_path, files))
    module = "pipy_harness.native.awaited_factory_receiver"
    external_write = _Access("RunControlState", "line", module, "write")

    assert result.violations == ()
    assert {access for access in result.accesses if access.module == module} == {
        external_write
    }
    assert external_write not in ALLOWED_WRITES


def test_field_inventory_resolves_direct_awaited_async_method_receiver(
    tmp_path: Path,
) -> None:
    files = _synthetic_base()
    files["pipy_harness/native/awaited_method_receiver.py"] = """
from pipy_harness.native.repl.loop_scope import RunControlState

class StateScope:
    async def state(self) -> RunControlState:
        raise RuntimeError

async def bypass(scope: StateScope) -> str | None:
    (await scope.state()).pending_prefill = "outside owner"
    return (await scope.state()).pending_prefill
"""
    result = _audit_inventory(_synthetic_modules(tmp_path, files))
    module = "pipy_harness.native.awaited_method_receiver"
    external_write = _Access("RunControlState", "pending_prefill", module, "write")

    assert result.violations == ()
    assert {access for access in result.accesses if access.module == module} == {
        _Access("RunControlState", "pending_prefill", module, "read"),
        external_write,
    }
    assert external_write not in ALLOWED_WRITES


def test_field_inventory_treats_unannotated_class_assignments_as_members_only(
    tmp_path: Path,
) -> None:
    files = _synthetic_base()
    files["pipy_harness/native/tui.py"] = files["pipy_harness/native/tui.py"].replace(
        "class TerminalUi:\n",
        "class TerminalUi:\n"
        "    class_constant = 'terminal'\n"
        "    access_count = 0\n"
        "    access_count += 1\n",
    )
    files["pipy_harness/native/class_constant_consumer.py"] = """
from pipy_harness.native.tui import TerminalUi

TerminalUi.class_constant
TerminalUi.access_count
"""

    result = _audit_inventory(_synthetic_modules(tmp_path, files))

    assert result.violations == ()
    assert result.fields == MEMBER_LIST


def test_field_inventory_rejects_a_second_definition_owner(tmp_path: Path) -> None:
    files = _synthetic_base()
    files["pipy_harness/native/ui/shadow.py"] = "class TerminalUi:\n    pass\n"
    result = _audit_inventory(_synthetic_modules(tmp_path, files))
    assert any("TerminalUi: definition owners" in item for item in result.violations)


@pytest.mark.parametrize(
    ("target", "relative", "expected_count"),
    [
        ("TerminalUi", "pipy_harness/native/tui.py", 9),
        ("RunControlState", "pipy_harness/native/repl/loop_scope.py", 10),
        ("CodingSession", "pipy_harness/native/coding/session.py", 24),
    ],
)
def test_field_inventory_resolves_inherited_dataclass_fields_and_rejects_drift(
    tmp_path: Path, target: str, relative: str, expected_count: int
) -> None:
    files = _synthetic_base()
    files["pipy_harness/native/inventory_base.py"] = """
from dataclasses import dataclass

class OrdinaryBase:
    annotation_is_not_a_dataclass_field: object

@dataclass
class InventoryBase(OrdinaryBase):
    inherited_dataclass_field: object
"""
    files[relative] = (
        "from pipy_harness.native.inventory_base import InventoryBase\n"
        + files[relative].replace(f"class {target}:", f"class {target}(InventoryBase):")
    )

    result = _audit_inventory(_synthetic_modules(tmp_path, files))

    assert result.violations == ()
    assert len(result.fields[target]) == expected_count + 1
    assert "inherited_dataclass_field" in result.fields[target]
    assert "annotation_is_not_a_dataclass_field" not in result.fields[target]
    with pytest.raises(AssertionError):
        _assert_inventory(result)


def test_field_inventory_fails_closed_for_unresolved_dataclass_base(
    tmp_path: Path,
) -> None:
    files = _synthetic_base()
    files["pipy_harness/native/tui.py"] = (
        "from unavailable_inventory import MaybeDataclassBase\n"
        + files["pipy_harness/native/tui.py"].replace(
            "class TerminalUi:", "class TerminalUi(MaybeDataclassBase):"
        )
    )

    result = _audit_inventory(_synthetic_modules(tmp_path, files))

    assert any(
        "TerminalUi: dataclass field inventory has an unresolved base" == violation
        for violation in result.violations
    )
    with pytest.raises(AssertionError):
        _assert_inventory(result)


def _call_sites(
    modules: dict[str, _Module],
    canonical: str,
    type_aliases: _TypeAliases | None = None,
) -> list[tuple[_Module, ast.Call]]:
    calls: list[tuple[_Module, ast.Call]] = []
    resolved_aliases = type_aliases or _type_alias_targets(modules)
    for module in modules.values():
        alias_snapshots = _symbol_alias_snapshots(module)
        calls.extend(
            (module, node)
            for node in ast.walk(module.tree)
            if isinstance(node, ast.Call)
            and _canonical_expression_symbol(
                node.func,
                module,
                alias_snapshots.get(id(node), {}),
                resolved_aliases,
                frozenset({canonical}),
            )
            == canonical
        )
    return calls


def _assert_unique_lock_construction(modules: dict[str, _Module]) -> None:
    type_aliases = _type_alias_targets(modules)
    cases = (
        (
            "pipy_harness.native.ui.paint_lock.PaintLock",
            "pipy_harness.native.ui.screen",
            "PaintLock",
        ),
        (
            "pipy_harness.native.session_state_lock.SessionStateLock",
            "pipy_harness.native.repl.wiring",
            "SessionStateLock",
        ),
    )
    for wrapper, owner, label in cases:
        wrapper_calls = _call_sites(modules, wrapper, type_aliases)
        assert len(wrapper_calls) == 1, (
            f"{label} must have exactly one construction in {owner}; "
            f"found {[(module.name, call.lineno) for module, call in wrapper_calls]!r}"
        )
        assert wrapper_calls[0][0].name == owner, (
            f"{label} construction must be owned by {owner}, not "
            f"{wrapper_calls[0][0].name}"
        )
        module, call = wrapper_calls[0]
        assert len(call.args) == 1 and not call.keywords
        wrapped = call.args[0]
        assert isinstance(wrapped, ast.Call)
        alias_snapshots = _symbol_alias_snapshots(module)
        assert (
            _canonical_expression_symbol(
                wrapped.func,
                module,
                alias_snapshots.get(id(wrapped), {}),
                type_aliases,
                frozenset({"threading.RLock"}),
            )
            == "threading.RLock"
        ), f"{label} must wrap one explicit threading.RLock()"
        assert not wrapped.args and not wrapped.keywords
        rlock_calls = [
            node
            for node in ast.walk(module.tree)
            if isinstance(node, ast.Call)
            and _canonical_expression_symbol(
                node.func,
                module,
                alias_snapshots.get(id(node), {}),
                type_aliases,
                frozenset({"threading.RLock"}),
            )
            == "threading.RLock"
        ]
        assert rlock_calls == [wrapped]


def test_named_locks_have_one_alias_resistant_explicit_construction() -> None:
    modules = _load_modules(PRODUCTION_ROOT)
    _assert_unique_lock_construction(modules)

    paint_source = (PRODUCTION_ROOT / "native/ui/paint_lock.py").read_text(
        encoding="utf-8"
    )
    paint_class = next(
        node
        for node in ast.parse(paint_source).body
        if isinstance(node, ast.ClassDef) and node.name == "PaintLock"
    )
    paint_init = next(
        node
        for node in paint_class.body
        if isinstance(node, ast.FunctionDef) and node.name == "__init__"
    )
    assert [argument.arg for argument in paint_init.args.args] == ["self", "lock"]
    assert paint_init.args.defaults == []

    session_source = (PRODUCTION_ROOT / "native/session_state_lock.py").read_text(
        encoding="utf-8"
    )
    assignment = next(
        node
        for node in ast.parse(session_source).body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "SessionStateLock"
            for target in node.targets
        )
    )
    assert isinstance(assignment.value, ast.Call)
    assert len(assignment.value.args) == 2
    assert ast.unparse(assignment.value.args[1]) == "threading.RLock"

    paint_lock = getattr(
        importlib.import_module("pipy_harness.native.ui.paint_lock"), "PaintLock"
    )
    session_state_lock = getattr(
        importlib.import_module("pipy_harness.native.session_state_lock"),
        "SessionStateLock",
    )
    with pytest.raises(TypeError):
        paint_lock()
    with pytest.raises(TypeError):
        session_state_lock()


def test_lock_construction_audit_rejects_import_and_call_alias_bypasses(
    tmp_path: Path,
) -> None:
    files = {
        "pipy_harness/native/ui/paint_lock.py": "class PaintLock:\n    pass\n",
        "pipy_harness/native/session_state_lock.py": "SessionStateLock = object()\n",
        "pipy_harness/native/ui/screen.py": """
import threading as threads
from pipy_harness.native.ui.paint_lock import PaintLock as Guard
make_guard = Guard
make_rlock = threads.RLock
first = make_guard(make_rlock())

def nested_aliases_do_not_leak() -> None:
    make_guard = object
    make_rlock = object
""",
        "pipy_harness/native/repl/wiring.py": """
import threading as threads
from pipy_harness.native.session_state_lock import SessionStateLock as Guard
make_guard = Guard
make_rlock = threads.RLock
first = make_guard(make_rlock())

def nested_aliases_do_not_leak() -> None:
    make_guard = object
    make_rlock = object
""",
    }
    _assert_unique_lock_construction(_synthetic_modules(tmp_path, files))

    files["pipy_harness/native/ui/bypass.py"] = """
import threading as threads
from pipy_harness.native.ui.paint_lock import PaintLock as Guard
again = Guard
lock_factory = threads.RLock
second = again(lock_factory())
again = object
lock_factory = object
"""
    files["pipy_harness/native/repl/bypass.py"] = """
import threading as threads
from pipy_harness.native.session_state_lock import SessionStateLock as Guard
again = Guard
lock_factory = threads.RLock
second = again(lock_factory())
again = object
lock_factory = object
"""
    with pytest.raises(AssertionError, match="exactly one construction"):
        _assert_unique_lock_construction(_synthetic_modules(tmp_path, files))


def _reexported_lock_files() -> dict[str, str]:
    return {
        "pipy_harness/native/ui/paint_lock.py": "class PaintLock:\n    pass\n",
        "pipy_harness/native/session_state_lock.py": ("SessionStateLock = object()\n"),
        "pipy_harness/native/lock_exports.py": """
from pipy_harness.native.ui.paint_lock import PaintLock as Paint
from pipy_harness.native.session_state_lock import SessionStateLock as State
from threading import RLock as Raw
""",
        "pipy_harness/native/lock_reexports.py": """
from pipy_harness.native.lock_exports import Paint, Raw, State
""",
        "pipy_harness/native/ui/screen.py": """
from pipy_harness.native.lock_reexports import Paint, Raw
paint_lock = Paint(Raw())
""",
        "pipy_harness/native/repl/wiring.py": """
from pipy_harness.native.lock_reexports import Raw, State
session_lock = State(Raw())
""",
    }


def test_lock_construction_audit_resolves_transitive_reexports(
    tmp_path: Path,
) -> None:
    _assert_unique_lock_construction(
        _synthetic_modules(tmp_path, _reexported_lock_files())
    )


@pytest.mark.parametrize(
    ("relative", "source", "message"),
    [
        (
            "pipy_harness/native/ui/paint_bypass.py",
            """
from pipy_harness.native.lock_exports import Paint, Raw
second = Paint(Raw())
""",
            "exactly one construction",
        ),
        (
            "pipy_harness/native/repl/state_bypass.py",
            """
from pipy_harness.native.lock_reexports import Raw, State
second = State(Raw())
""",
            "exactly one construction",
        ),
        (
            "pipy_harness/native/ui/screen.py",
            """
from pipy_harness.native.lock_reexports import Paint, Raw
paint_lock = Paint(Raw())
second_raw_lock = Raw()
""",
            None,
        ),
    ],
)
def test_lock_construction_audit_rejects_reexported_second_construction(
    tmp_path: Path, relative: str, source: str, message: str | None
) -> None:
    files = _reexported_lock_files()
    files[relative] = source

    with pytest.raises(AssertionError, match=message):
        _assert_unique_lock_construction(_synthetic_modules(tmp_path, files))


def test_lock_construction_audit_uses_execution_time_function_globals(
    tmp_path: Path,
) -> None:
    files = {
        "pipy_harness/native/ui/paint_lock.py": "class PaintLock:\n    pass\n",
        "pipy_harness/native/session_state_lock.py": "SessionStateLock = object()\n",
        "pipy_harness/native/ui/screen.py": """
import threading
from pipy_harness.native.ui.paint_lock import PaintLock
paint_lock = PaintLock(threading.RLock())
""",
        "pipy_harness/native/repl/wiring.py": """
import threading
from pipy_harness.native.session_state_lock import SessionStateLock
session_lock = SessionStateLock(threading.RLock())
""",
        "pipy_harness/native/ui/late_bypass.py": """
def construct_later() -> object:
    local_guard = Guard
    local_rlock = make_rlock
    return local_guard(local_rlock())

from pipy_harness.native.ui.paint_lock import PaintLock as Guard
from threading import RLock as make_rlock
""",
        "pipy_harness/native/repl/late_bypass.py": """
def construct_later() -> object:
    local_guard = Guard
    local_rlock = make_rlock
    return local_guard(local_rlock())

from pipy_harness.native.session_state_lock import SessionStateLock as Guard
from threading import RLock as make_rlock
""",
    }

    with pytest.raises(AssertionError, match="exactly one construction"):
        _assert_unique_lock_construction(_synthetic_modules(tmp_path, files))


def test_lock_construction_audit_uses_late_bound_closure_aliases(
    tmp_path: Path,
) -> None:
    files = {
        "pipy_harness/native/ui/paint_lock.py": "class PaintLock:\n    pass\n",
        "pipy_harness/native/session_state_lock.py": "SessionStateLock = object()\n",
        "pipy_harness/native/ui/screen.py": """
import threading
from pipy_harness.native.ui.paint_lock import PaintLock
paint_lock = PaintLock(threading.RLock())
""",
        "pipy_harness/native/repl/wiring.py": """
import threading
from pipy_harness.native.session_state_lock import SessionStateLock
session_lock = SessionStateLock(threading.RLock())
""",
        "pipy_harness/native/ui/closure_bypass.py": """
from pipy_harness.native.ui.paint_lock import PaintLock
from threading import RLock

def enclosing() -> None:
    def construct() -> object:
        return Lock(make_lock())

    Lock = PaintLock
    make_lock = RLock
    construct()
""",
        "pipy_harness/native/repl/closure_bypass.py": """
from pipy_harness.native.session_state_lock import SessionStateLock
from threading import RLock

def enclosing() -> None:
    def construct() -> object:
        return Lock(make_lock())

    Lock = SessionStateLock
    make_lock = RLock
    construct()
""",
    }

    with pytest.raises(AssertionError, match="exactly one construction"):
        _assert_unique_lock_construction(_synthetic_modules(tmp_path, files))


def test_lock_construction_audit_keeps_call_timing_and_scope_semantics(
    tmp_path: Path,
) -> None:
    files = {
        "pipy_harness/native/ui/paint_lock.py": "class PaintLock:\n    pass\n",
        "pipy_harness/native/session_state_lock.py": "SessionStateLock = object()\n",
        "pipy_harness/native/ui/screen.py": """
import threading
from pipy_harness.native.ui.paint_lock import PaintLock
paint_lock = PaintLock(threading.RLock())
""",
        "pipy_harness/native/repl/wiring.py": """
import threading
from pipy_harness.native.session_state_lock import SessionStateLock
session_lock = SessionStateLock(threading.RLock())
""",
        "pipy_harness/native/ui/scope_non_bypass.py": """
from pipy_harness.native.ui.paint_lock import PaintLock
from threading import RLock

Lock = object
make_lock = object
not_a_lock = Lock(make_lock())
Lock = PaintLock
make_lock = RLock

def local_shadow() -> None:
    Lock(make_lock())
    Lock = PaintLock
    make_lock = RLock
""",
        "pipy_harness/native/ui/global_scope_non_bypass.py": """
from pipy_harness.native.ui.paint_lock import PaintLock
from threading import RLock

Lock = object
make_lock = object

def enclosing() -> None:
    Lock = PaintLock
    make_lock = RLock

    def globals_do_not_capture_locals() -> object:
        global Lock, make_lock
        return Lock(make_lock())
""",
    }

    _assert_unique_lock_construction(_synthetic_modules(tmp_path, files))


_FORBIDDEN_UI_MODULES = (
    "pipy_harness.native.tui",
    "pipy_harness.native.repl",
    "pipy_harness.native.tool_" + "loop_session",
    "pipy_harness.native.coding.session",
)
_FORBIDDEN_UI_NAMES = ("TerminalUi", "RunControlState", "CodingSession")


def _imported_modules(module: _Module) -> set[str]:
    imported: set[str] = set()
    for node in ast.walk(module.tree):
        if isinstance(node, ast.Import):
            imported.update(item.name for item in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = _import_from_base(node, module.name, module.is_package)
            imported.add(base)
            imported.update(
                f"{base}.{item.name}".strip(".")
                for item in node.names
                if item.name != "*"
            )
    return imported


def _ui_backedge_offenders(modules: dict[str, _Module]) -> list[str]:
    ui_modules = [
        module
        for module in modules.values()
        if module.name == "pipy_harness.native.ui"
        or module.name.startswith("pipy_harness.native.ui.")
    ]
    offenders: list[str] = []
    for module in ui_modules:
        for imported in _imported_modules(module):
            if any(
                imported == forbidden or imported.startswith(f"{forbidden}.")
                for forbidden in _FORBIDDEN_UI_MODULES
            ):
                offenders.append(f"{module.name}: import {imported}")
        for node in ast.walk(module.tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if any(forbidden in node.value for forbidden in _FORBIDDEN_UI_MODULES):
                    offenders.append(f"{module.name}:{node.lineno}: string back-edge")
            if isinstance(node, ast.Name) and node.id in _FORBIDDEN_UI_NAMES:
                offenders.append(f"{module.name}:{node.lineno}: name {node.id}")
    return offenders


def test_recursive_ui_audit_rejects_relative_aliased_backedge(
    tmp_path: Path,
) -> None:
    files = _synthetic_base()
    files["pipy_harness/native/ui/relative_backedge.py"] = (
        "from ..tui import TerminalUi as Shell\nvalue: Shell\n"
    )
    modules = _synthetic_modules(tmp_path, files)
    module = modules["pipy_harness.native.ui.relative_backedge"]

    assert _imported_modules(module) == {
        "pipy_harness.native.tui",
        "pipy_harness.native.tui.TerminalUi",
    }
    alias_name = next(
        node
        for node in ast.walk(module.tree)
        if isinstance(node, ast.Name) and node.id == "Shell"
    )
    assert _symbol(alias_name, module) == "pipy_harness.native.tui.TerminalUi"
    assert any(
        "import pipy_harness.native.tui" in offender
        for offender in _ui_backedge_offenders(modules)
    )


def _load_architecture_boundary_helper() -> object:
    path = REPO_ROOT / "tests/test_architecture_import_boundaries.py"
    spec = importlib.util.spec_from_file_location("_final_boundary_helper", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_complete_repository_boundary_audit_remains_strict() -> None:
    boundary = _load_architecture_boundary_helper()
    planned = getattr(boundary, "_PLANNED_IMPORT_PREFIXES")
    assert planned == frozenset(
        {
            "pipy_harness.native.agent",
            "pipy_harness.native.coding",
            "pipy_harness.native.coding.session",
            "pipy_harness.native.extensions",
            "pipy_harness.native.providers",
            "pipy_harness.native.ui",
        }
    )
    rules = boundary.ARCHITECTURE_RULES  # type: ignore[attr-defined]
    normalized_rules = [
        (rule.source_package, tuple(sorted(rule.forbidden_imports))) for rule in rules
    ]
    assert len(normalized_rules) == 41
    boundary_hash = hashlib.sha256(
        json.dumps(normalized_rules, separators=(",", ":")).encode()
    ).hexdigest()
    expected_hash = "dd2d0c1bb117809f3db0baf2aef725ac0163a3265deda78129c9275d7f211b96"
    assert boundary_hash == expected_hash, (
        "architecture rule inventory drifted after canonical sorting: "
        f"expected {expected_hash}, got {boundary_hash}"
    )

    unresolved = boundary._unresolved_forbidden_imports(  # type: ignore[attr-defined]
        SOURCE_ROOT,
        rules,
        planned_imports=planned,
    )
    assert unresolved == []
    violations = [
        violation
        for rule in rules
        for violation in boundary._evaluate_rule(SOURCE_ROOT, rule)  # type: ignore[attr-defined]
    ]
    assert violations == [], "architecture boundary violations:\n" + "\n".join(
        violation.format(SOURCE_ROOT) for violation in violations
    )


def test_final_measured_shape_and_complexity_pin_are_exact() -> None:
    relative_counts = {
        path.relative_to(REPO_ROOT).as_posix(): len(
            path.read_text(encoding="utf-8").splitlines()
        )
        for path in (PRODUCTION_ROOT / "native").rglob("*.py")
    }
    assert relative_counts["src/pipy_harness/native/tui.py"] == 580
    assert relative_counts["src/pipy_harness/native/coding/session.py"] <= 336
    assert relative_counts["src/pipy_harness/native/extensions/activation.py"] == 1807
    assert max(relative_counts.values()) == 2488
    assert sorted(path for path, count in relative_counts.items() if count == 2488) == [
        "src/pipy_harness/native/session.py"
    ]

    tui_source = (PRODUCTION_ROOT / "native/tui.py").read_text(encoding="utf-8")
    tui_class = next(
        node
        for node in ast.parse(tui_source).body
        if isinstance(node, ast.ClassDef) and node.name == "TerminalUi"
    )
    first_line = min(
        [tui_class.lineno, *(item.lineno for item in tui_class.decorator_list)]
    )
    assert tui_class.end_lineno is not None
    assert tui_class.end_lineno - first_line + 1 == 230
    retained_defs = tuple(
        item.name
        for item in tui_class.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
    )
    assert retained_defs == (
        "__post_init__",
        "is_supported",
        "start",
        "read_line",
        "wait_for_active_turn_interrupt",
    )
    assert len(MEMBER_LIST["TerminalUi"]) == 9

    config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    ignores = config["tool"]["ruff"]["lint"]["per-file-ignores"]
    assert (
        not {
            "src/pipy_harness/native/tui.py",
            "src/pipy_harness/native/coding/session.py",
            "src/pipy_harness/native/tool_" + "loop_session.py",
        }
        & ignores.keys()
    )


def test_recursive_ui_and_retired_surface_audit_has_no_hidden_backedge() -> None:
    modules = _load_modules(PRODUCTION_ROOT)
    assert _ui_backedge_offenders(modules) == []

    retired_modules = (
        "pipy_harness.native.tool_" + "loop_session",
        "pipy_harness.native.extension_" + "runtime",
    )
    retired_names = (
        "ToolLoop" + "TerminalUi",
        "NativeTool" + "ReplSession",
        "NativeTool" + "ReplResult",
        "PipyNativeTool" + "ReplAdapter",
    )
    assert not (PRODUCTION_ROOT / ("native/tool_" + "loop_session.py")).exists()
    assert not (PRODUCTION_ROOT / ("native/extension_" + "runtime.py")).exists()
    assert not {
        module.name: token
        for module in modules.values()
        for token in (*retired_modules, *retired_names)
        if token in module.source
    }

    ui_init = modules["pipy_harness.native.ui"]
    exported = next(
        node.value
        for node in ui_init.tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "__all__"
            for target in node.targets
        )
    )
    assert isinstance(exported, ast.List)
    assert tuple(
        item.value for item in exported.elts if isinstance(item, ast.Constant)
    ) == (
        "AgentEventRenderer",
        "CancelAssistantMessage",
        "CompleteAssistantMessage",
        "FailAssistantMessage",
        "RenderBufferedAssistantText",
        "RenderDecision",
        "RenderToolCall",
        "RenderToolResult",
        "RenderingAgentEventAdapter",
        "StartAssistantMessage",
        "StreamAssistantReasoning",
        "StreamAssistantText",
        "StreamToolOutput",
        "UiState",
        "reduce",
    )
    assert _imported_modules(ui_init) == {
        "__future__",
        "__future__.annotations",
        "pipy_harness.native.ui.rendering",
        "pipy_harness.native.ui.rendering.AgentEventRenderer",
        "pipy_harness.native.ui.rendering.RenderingAgentEventAdapter",
        "pipy_harness.native.ui.state",
        "pipy_harness.native.ui.state.CancelAssistantMessage",
        "pipy_harness.native.ui.state.CompleteAssistantMessage",
        "pipy_harness.native.ui.state.FailAssistantMessage",
        "pipy_harness.native.ui.state.RenderBufferedAssistantText",
        "pipy_harness.native.ui.state.RenderDecision",
        "pipy_harness.native.ui.state.RenderToolCall",
        "pipy_harness.native.ui.state.RenderToolResult",
        "pipy_harness.native.ui.state.StartAssistantMessage",
        "pipy_harness.native.ui.state.StreamAssistantReasoning",
        "pipy_harness.native.ui.state.StreamAssistantText",
        "pipy_harness.native.ui.state.StreamToolOutput",
        "pipy_harness.native.ui.state.UiState",
        "pipy_harness.native.ui.state.reduce",
    }
