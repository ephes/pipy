from __future__ import annotations

import ast
from pathlib import Path
from types import ModuleType

import pipy_harness.extensions as public_extensions
import pipy_harness.native.extensions.activation as activation_module
from pipy_harness.native.extensions import (
    collectors,
    contracts,
    flag_tokens,
    provider_normalization,
)

REPO_ROOT = Path(__file__).parents[1]
LEAF_MODULES = (contracts, collectors, flag_tokens, provider_normalization)
MOVED_OWNERS = {
    contracts: {
        "CommandHandler",
        "HookHandler",
        "ActivationStatus",
        "PipyExtensionAPI",
        "RegisteredCommand",
        "RegisteredMessageRenderer",
        "RegisteredEntryRenderer",
        "RegisteredShortcut",
        "_EMPTY_HOOKS",
        "ActivatedExtension",
        "ExtensionActivationBatch",
        "_ExtensionRuntime",
        "_activation_message_routings",
    },
    collectors: {
        "extension_providers",
        "extension_oauth_providers",
        "extension_unregistered_providers",
        "extension_tools",
        "extension_flags",
        "extension_message_renderers",
        "extension_entry_renderers",
        "drain_user_messages",
        "drain_custom_messages",
    },
    flag_tokens: {
        "_ParsedExtensionFlagToken",
        "_parse_boolean_flag_value",
        "_parse_string_flag_value",
        "_parse_extension_flag_token",
        "parse_extension_flag_tokens",
    },
    provider_normalization: {
        "_coerce_activation_string",
        "_normalize_provider_name",
        "_normalize_provider_models",
        "_normalize_default_model",
        "_normalize_provider_oauth",
    },
}
PUBLIC_MOVED_OWNERS = {
    "PipyExtensionAPI": contracts,
    "RegisteredCommand": contracts,
    "RegisteredMessageRenderer": contracts,
    "RegisteredEntryRenderer": contracts,
    "RegisteredShortcut": contracts,
    "ActivatedExtension": contracts,
    "extension_providers": collectors,
    "extension_oauth_providers": collectors,
    "extension_unregistered_providers": collectors,
    "extension_tools": collectors,
    "extension_flags": collectors,
    "extension_message_renderers": collectors,
    "extension_entry_renderers": collectors,
    "drain_user_messages": collectors,
    "drain_custom_messages": collectors,
}


def _source(module: ModuleType) -> str:
    return Path(module.__file__ or "").read_text(encoding="utf-8")


def _top_level_members(module: ModuleType) -> set[str]:
    return {
        node.name
        for node in ast.parse(_source(module)).body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef))
    }


def _top_level_definitions(module: ModuleType) -> set[str]:
    names: set[str] = set()
    for node in ast.parse(_source(module)).body:
        if isinstance(node, ast.Assign):
            names.update(
                target.id for target in node.targets if isinstance(target, ast.Name)
            )
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.ClassDef, ast.FunctionDef)):
            names.add(node.name)
    return names


def test_extension_leaf_members_have_one_definition_site() -> None:
    moved_names = set().union(*MOVED_OWNERS.values())
    for module, expected in MOVED_OWNERS.items():
        assert _top_level_definitions(module) & moved_names == expected
    assert _top_level_definitions(activation_module).isdisjoint(moved_names)


def test_extension_leaves_have_no_activation_back_edge() -> None:
    for module in LEAF_MODULES:
        assert "extensions.activation" not in _source(module)


def test_retired_runtime_paths_are_absent_and_public_facade_identity_is_exact() -> None:
    retired_path = "src/pipy_harness/native/extension_" + "runtime.py"
    assert not (REPO_ROOT / retired_path).exists()
    activation_definitions = _top_level_definitions(activation_module)
    for name, owner in PUBLIC_MOVED_OWNERS.items():
        assert name not in activation_definitions
        assert getattr(public_extensions, name) is getattr(owner, name)


def test_direct_importers_use_moved_definition_sites() -> None:
    expected_modules = {
        name: owner.__name__ for owner, names in MOVED_OWNERS.items() for name in names
    }
    offenders: list[tuple[str, int, str, str | None]] = []
    for root in (REPO_ROOT / "src", REPO_ROOT / "tests", REPO_ROOT / "scripts"):
        for path in root.rglob("*.py"):
            syntax = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(syntax):
                if not isinstance(node, ast.ImportFrom):
                    continue
                for imported in node.names:
                    expected = expected_modules.get(imported.name)
                    if expected is not None and node.module not in (
                        expected,
                        "pipy_harness.extensions",
                    ):
                        offenders.append(
                            (
                                str(path.relative_to(REPO_ROOT)),
                                node.lineno,
                                imported.name,
                                node.module,
                            )
                        )
    assert offenders == []


def test_activation_owns_exact_state_machine_surface() -> None:
    assert _top_level_members(activation_module) == {
        "_FrozenActivation",
        "_NormalizedFlagRegistration",
        "_ProviderCatalogFinalization",
        "_PendingActivation",
        "_ActivationCleanup",
        "_report_activation_cleanup",
        "_ActivationApi",
        "_publish_activation_hosts_atomically",
        "_dispose_activation_hosts",
        "_dispose_activation_host_with_diagnostic",
        "activate_extensions",
        "_dispose_activation_results",
        "_finalize_provider_catalog_results",
        "_report_provider_catalog_finalization",
        "activate_extension_batch",
        "_descriptor_activation_key",
        "_activated_contribution_names",
        "_staged_contribution_names",
        "_finalize_preloaded_extension",
        "_ResolvedActivationEntry",
        "_FailedActivationEntry",
        "_resolve_activation_entry",
        "_execute_activation_entry",
        "_activate_one",
        "_ExtensionCandidate",
        "_passthrough_disabled",
        "_disabled",
        "safe_activation_metadata",
    }
