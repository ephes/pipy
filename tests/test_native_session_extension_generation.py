from __future__ import annotations

import ast
import gc
import inspect
import subprocess
import sys
import weakref
import threading
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import FrozenInstanceError, fields, replace
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import Any, TypeVar, cast, get_type_hints

import pytest

from pipy_harness.extensions import ToolResult
from pipy_harness.native.agent.usage import (
    AgentProviderUsageSample,
    AgentUsageAccumulator,
    AgentUsageRefreshValue,
    AgentUsageReloadValue,
)
from pipy_harness.native.auth_store import AuthStore
from pipy_harness.native.catalog_state import (
    ProviderCatalogRefreshValue,
    ProviderCatalogReloadState,
    ProviderCatalogState,
)
from pipy_harness.native.coding import CodingInputQueue
from pipy_harness.native.coding.state import (
    CodingReloadBindingValue,
    CodingReloadHistoryValue,
    CodingSessionState,
)
from pipy_harness.native.extension_chrome_state import ExtensionChromeSink
from pipy_harness.native.extension_types import ProviderContext
from pipy_harness.native.extension_hooks import (
    _activate_workspace_extensions,
    deliver_accepted_staged_batch,
)
from pipy_harness.native.extension_runtime import (
    ActivatedExtension,
    ExtensionActivationBatch,
    GenerationMessageRouting,
    QueuedCustomMessage,
    QueuedUserMessage,
    _ExtensionRuntime,
    activate_extensions,
    dispatch_extension_command,
)
from pipy_harness.native.extensions import discover_extensions
from pipy_harness.native.package_resources import PackageResourceRoots
from pipy_harness.native.resources import WorkspaceResources
from pipy_harness.native.session_generation import (
    PREPARED_RELOAD_BUILD_STEPS,
    PROJECTION_BUILD_STEPS,
    ActivationInputsValue,
    CodingCompactionValue,
    DetachedReloadEffect,
    ExtensionChromeHandle,
    ExtensionProjection,
    ExtensionQueueProjection,
    FrozenStagedDeliveryBatch,
    GenerationQueueHandle,
    OrderedDeliveryGate,
    OrderedDeliveryToken,
    PreparedReloadEffects,
    PresentationPersistenceValue,
    ProviderFactoryValue,
    ReloadEffectPreparationPorts,
    SessionExtensionGeneration,
    SessionGenerationRef,
    SessionGenerationSnapshot,
    TemporaryLegacyValue,
    build_extension_projection,
    prepare_provider_reload_values,
)
from pipy_harness.native.provider_construction import (
    build_provider,
    try_build_extension_provider_port,
)
from pipy_harness.native.repl_state import (
    ModelRuntime,
    NativeModelSelection,
    NativeReplProviderState,
    ReplPendingDefaultReloadValue,
    ReplSelectionReloadValue,
)
from pipy_harness.native.session_tree import NativeSessionTree
from pipy_harness.native.tool_capabilities import (
    ToolCapabilityState,
    ToolFilterOptions,
)
from pipy_harness.native.tool_loop_session import (
    _ExtensionCustomEntryRunState,
    _RunControlState,
    _build_candidate_extension_projection,
    _build_detached_reload_effects,
    _build_legacy_extension_tool_port,
    _build_projected_extension_tool_port,
)
from pipy_harness.native.tool_renderers import _extension_tool_renderer_map
from pipy_harness.native.tui import (
    AcceptedCustomMessageSinks,
    ExtensionChromePrepareInput,
    _CustomEntryRenderer,
)
from pipy_harness.native.tools import (
    ToolContext,
    ToolPort,
    ToolRequest,
    make_tool_request_id,
)
from session_generation_test_support import build_test_projection


def _empty_resources() -> WorkspaceResources:
    return WorkspaceResources((), (), (), False, False, False)


def _runtime_from_batch(
    tmp_path: Path,
    *,
    message_outbox: list[QueuedUserMessage],
    custom_message_outbox: list[QueuedCustomMessage],
    activated: tuple[ActivatedExtension, ...] = (),
) -> _ExtensionRuntime:
    return _activate_workspace_extensions(
        tmp_path,
        _empty_resources(),
        activation_batch=ExtensionActivationBatch(
            activated=activated,
            message_outbox=message_outbox,
            custom_message_outbox=custom_message_outbox,
        ),
    )


def test_run_control_holds_one_generation_reference_and_no_mirrors() -> None:
    """Extension state has exactly one owner, reached through the session ref."""

    field_names = {field.name for field in fields(_RunControlState)}

    assert "_ext_runtime" not in field_names
    # The generation itself is no longer a bare field: it is reached through
    # the reference that owns the session mutex.
    assert "extension_generation" not in field_names
    assert "generation_ref" in field_names
    assert {name for name in field_names if name.startswith("extension_")} == {
        "extension_in_agent_turn",
    }


def test_generation_preserves_outbox_identity_and_ui_adapter_late_binding(
    tmp_path: Path,
) -> None:
    extension_dir = tmp_path / ".pipy" / "extensions"
    extension_dir.mkdir(parents=True)
    (extension_dir / "sender.py").write_text(
        "def activate(api):\n"
        "    def send(ctx, args):\n"
        "        api.send_user_message(args)\n"
        "    api.register_command('send', 'send', send)\n",
        encoding="utf-8",
    )
    first_outbox: list[QueuedUserMessage] = []
    first_custom_outbox: list[QueuedCustomMessage] = []
    first_routing = GenerationMessageRouting(first_outbox, first_custom_outbox)
    activated = tuple(
        activate_extensions(
            discover_extensions(
                tmp_path,
                config_home_env={},
                home_dir=tmp_path,
                include_workspace_defaults=True,
            ),
            message_outbox=first_outbox,
            custom_message_outbox=first_custom_outbox,
            message_routing=first_routing,
        )
    )
    first_runtime = _runtime_from_batch(
        tmp_path,
        activated=activated,
        message_outbox=first_outbox,
        custom_message_outbox=first_custom_outbox,
    )
    first_flags: dict[str, object] = {"mode": "first"}
    first_generation = SessionExtensionGeneration(first_runtime, first_flags)
    ctl = _RunControlState(
        session_tree=NativeSessionTree.create(tmp_path, persist=False),
        tree_filter_mode="default",
        pending_prefill=None,
        package_roots=PackageResourceRoots.empty(),
        workspace_resources=_empty_resources(),
        generation_ref=SessionGenerationRef(first_generation),
        agent_settled_pending=False,
        extension_in_agent_turn=False,
    )
    adapter = _ExtensionCustomEntryRunState(ctl=ctl)

    assert first_generation.runtime.outbox is first_outbox
    assert first_generation.runtime.custom_outbox is first_custom_outbox
    assert first_generation.flag_values is first_flags
    assert adapter.extension_message_outbox is first_outbox
    assert adapter.extension_custom_message_outbox is first_custom_outbox

    dispatched = dispatch_extension_command(
        "/send queued-late",
        first_generation.runtime.commands,
        cwd=str(tmp_path),
        has_ui=False,
        flags=first_generation.flag_values,
    )
    assert dispatched is not None and dispatched.ran
    assert [message.content for message in adapter.extension_message_outbox] == [
        "queued-late"
    ]

    second_outbox: list[QueuedUserMessage] = []
    second_custom_outbox: list[QueuedCustomMessage] = []
    second_runtime = _runtime_from_batch(
        tmp_path,
        message_outbox=second_outbox,
        custom_message_outbox=second_custom_outbox,
    )
    ctl.extension_generation = SessionExtensionGeneration(second_runtime, {})

    assert adapter.extension_message_outbox is second_outbox
    assert adapter.extension_custom_message_outbox is second_custom_outbox

    stale_dispatch = dispatch_extension_command(
        "/send old-after-swap",
        first_generation.runtime.commands,
        cwd=str(tmp_path),
        has_ui=False,
        flags=first_generation.flag_values,
    )
    assert stale_dispatch is not None and stale_dispatch.ran
    assert adapter.extension_message_outbox == []
    assert [message.content for message in first_outbox] == [
        "queued-late",
        "old-after-swap",
    ]


def _generation(tmp_path: Path, label: str) -> SessionExtensionGeneration:
    return SessionExtensionGeneration(
        _runtime_from_batch(tmp_path, message_outbox=[], custom_message_outbox=[]),
        {"mode": label},
    )


def test_generation_ref_publishes_a_new_identity_and_returns_the_retired_value(
    tmp_path: Path,
) -> None:
    first = _generation(tmp_path, "first")
    second = _generation(tmp_path, "second")
    ref = SessionGenerationRef(first)

    before = ref.snapshot()
    assert before.generation is first

    retired = ref.publish(second)

    after = ref.snapshot()
    assert retired is first
    assert after.generation is second
    assert after.generation_id != before.generation_id


def test_a_snapshot_does_not_follow_a_later_publication(tmp_path: Path) -> None:
    """An operation reads one generation for its whole duration."""

    first = _generation(tmp_path, "first")
    ref = SessionGenerationRef(first)
    held = ref.snapshot()

    ref.publish(_generation(tmp_path, "second"))

    assert held.generation is first
    assert held.generation.flag_values == {"mode": "first"}
    assert ref.snapshot().generation is not first


def test_capabilities_and_the_generation_share_one_session_mutex(
    tmp_path: Path,
) -> None:
    """Two locks would not serialize a reload against a worker's mutation."""

    from pipy_harness.native.tool_capabilities import (
        NativeToolCapabilities,
        ToolFilterOptions,
    )

    ref = SessionGenerationRef(_generation(tmp_path, "first"))
    capabilities = NativeToolCapabilities(
        {},
        {},
        workspace_root=tmp_path,
        reference_roots=(),
        stderr_sink=lambda _text: None,
        filter_options=ToolFilterOptions.empty(),
        cancel_join_timeout_seconds=1.0,
        state_lock=ref.lock,
    )

    assert capabilities._state_lock is ref.lock


def test_publication_gate_opens_and_closes_around_a_publication(
    tmp_path: Path,
) -> None:
    ref = SessionGenerationRef(_generation(tmp_path, "first"))

    assert ref.publication_pending is False
    with ref.publishing():
        assert ref.publication_pending is True
    assert ref.publication_pending is False


def test_the_gate_closes_even_when_candidate_preparation_raises(
    tmp_path: Path,
) -> None:
    """A failed reload must not refuse every later mutation for the session."""

    ref = SessionGenerationRef(_generation(tmp_path, "first"))

    with pytest.raises(RuntimeError):
        with ref.publishing():
            raise RuntimeError("candidate build failed")

    assert ref.publication_pending is False


def test_the_gate_stays_open_across_the_pointer_swap(tmp_path: Path) -> None:
    """A reload publishes the pointer partway; the gate must outlast it.

    Provider selection, tool visibility, and renderer projections are
    republished *after* the generation pointer swaps. Reopening mutations at
    the swap would let a change be accepted and then overwritten by those
    later projections.
    """

    ref = SessionGenerationRef(_generation(tmp_path, "first"))
    second = _generation(tmp_path, "second")

    with ref.publishing():
        ref.publish(second)
        assert ref.publication_pending is True
        assert ref.snapshot().generation is second

    assert ref.publication_pending is False
    assert ref.snapshot().generation is second


def _rich_runtime(
    tmp_path: Path,
    name: str,
    *,
    mutex: threading.RLock | None = None,
    boundary_observer: Callable[[str], None] | None = None,
) -> _ExtensionRuntime:
    extension_dir = tmp_path / name / ".pipy" / "extensions"
    extension_dir.mkdir(parents=True)
    (extension_dir / "projection.py").write_text(
        "from pipy_harness.extensions import (\n"
        "    ExtensionFlag, ExtensionProvider, ExtensionTool, ToolResult,\n"
        ")\n"
        "def _handler(*_args): return None\n"
        "def activate(api):\n"
        "    api.register_command('projected', 'Projected command', _handler)\n"
        "    api.register_shortcut('ctrl-k', _handler)\n"
        "    api.register_flag(ExtensionFlag('projection-mode', 'string', default='base'))\n"
        "    api.on('input', _handler)\n"
        "    api.on('before_provider_request', _handler)\n"
        "    api.on('tool_call', _handler)\n"
        "    api.on('session_start', _handler)\n"
        "    api.register_tool(ExtensionTool(\n"
        "        name='projected_tool', description='Projected tool',\n"
        "        input_schema={'type': 'object'},\n"
        "        handler=lambda ctx, _params: ToolResult(content=str(ctx.flags['projection-mode'])),\n"
        "        render_call=lambda _ctx: None))\n"
        "    api.register_provider(ExtensionProvider(\n"
        "        name='projected-provider', default_model='m', models=('m',),\n"
        "        factory=lambda _ctx: None))\n"
        "    api.unregister_provider('legacy-provider')\n"
        "    api.register_message_renderer('card', _handler)\n"
        "    api.register_entry_renderer('entry', _handler)\n"
        "    api.send_user_message('queued-user')\n"
        "    api.send_message({'customType': 'card', 'content': 'queued-custom',\n"
        "                      'options': {'nested': 'copied'}})\n",
        encoding="utf-8",
    )
    root = tmp_path / name
    outbox: list[QueuedUserMessage] = []
    custom_outbox: list[QueuedCustomMessage] = []
    message_routing = GenerationMessageRouting(
        outbox,
        custom_outbox,
        mutex=mutex or threading.RLock(),
        boundary_observer=boundary_observer,
    )
    activated = tuple(
        activate_extensions(
            discover_extensions(
                root,
                config_home_env={},
                home_dir=root,
                include_workspace_defaults=True,
            ),
            message_outbox=outbox,
            custom_message_outbox=custom_outbox,
            message_routing=message_routing,
        )
    )
    return _runtime_from_batch(
        root,
        activated=activated,
        message_outbox=outbox,
        custom_message_outbox=custom_outbox,
    )


def _projection(
    runtime: _ExtensionRuntime,
    *,
    lock: threading.RLock | None = None,
    chrome: ExtensionChromeSink | None = None,
    step_observer: Any = None,
    flag_values: Mapping[str, object] | None = None,
) -> ExtensionProjection:
    owner_mutex = runtime.message_routing.mutex
    if owner_mutex is None:
        raise AssertionError("projected test runtimes require an installable owner")
    mutex = owner_mutex if lock is None else lock
    return build_test_projection(
        runtime,
        {"projection-mode": "candidate"} if flag_values is None else flag_values,
        queue_mutex=mutex,
        chrome=chrome,
        step_observer=step_observer,
    )


def test_live_generation_shape_and_reference_remain_the_legacy_value() -> None:
    assert [field.name for field in fields(SessionExtensionGeneration)] == [
        "runtime",
        "flag_values",
        "projection",
        "chrome_token",
    ]


def test_every_runtime_contribution_field_has_an_exact_projection_disposition() -> None:
    projection_family_by_runtime_field = {
        "commands": "commands",
        "menu_names": "commands",
        "descriptions": "commands",
        "tool_call_hooks": "hooks",
        "lifecycle_hooks": "hooks",
        "input_hooks": "hooks",
        "before_agent_start_hooks": "hooks",
        "tool_result_hooks": "hooks",
        "user_bash_hooks": "hooks",
        "before_provider_headers_hooks": "hooks",
        "before_provider_request_hooks": "hooks",
        "session_before_switch_hooks": "hooks",
        "session_before_fork_hooks": "hooks",
        "session_before_compact_hooks": "hooks",
        "session_before_tree_hooks": "hooks",
        "outbox": "queues",
        "custom_outbox": "queues",
        "message_routing": "queues",
        "tools": "tools",
        "shortcuts": "commands",
        "flags": "runtime_flags",
        "providers": "providers",
        "unregistered_providers": "providers",
        "message_renderers": "renderers",
        "entry_renderers": "renderers",
        "custom_messages": "runtime_flags",
    }
    omitted_runtime_fields = {
        "activation_hosts": "R1 mutable activation ownership state"
    }
    runtime_fields = {field.name for field in fields(_ExtensionRuntime)}

    assert set(projection_family_by_runtime_field).isdisjoint(omitted_runtime_fields)
    assert set(projection_family_by_runtime_field) | set(omitted_runtime_fields) == (
        runtime_fields
    )
    assert omitted_runtime_fields == {
        "activation_hosts": "R1 mutable activation ownership state"
    }


def test_runtime_flag_projection_matches_the_legacy_source(tmp_path: Path) -> None:
    runtime = _rich_runtime(tmp_path, "runtime-flags")
    caller_values: dict[str, object] = {"projection-mode": "candidate"}
    nested_option: dict[str, object] = {"nested": "caller-owned"}
    details: dict[str, object] = {"opaque": "caller-owned"}
    caller_options: dict[str, object] = {"payload": nested_option}
    source_message = replace(
        runtime.custom_messages[0], options=caller_options, details=details
    )
    runtime = replace(runtime, custom_messages=(source_message,))

    projected = _projection(runtime, flag_values=caller_values).runtime_flags
    projected_message = projected.custom_messages[0]

    assert projected.flags == runtime.flags
    assert projected.values == {"projection-mode": "candidate"}
    assert projected.values is not caller_values
    assert projected.custom_messages == runtime.custom_messages
    assert projected.custom_messages is not runtime.custom_messages
    assert isinstance(projected_message.options, MappingProxyType)
    assert projected_message.options is not caller_options
    assert projected_message.options["payload"] is nested_option
    assert projected_message.details is details

    caller_values["projection-mode"] = "caller-mutated"
    caller_options["late"] = "caller-mutated"
    assert projected.values == {"projection-mode": "candidate"}
    assert "late" not in projected_message.options


def test_command_menu_description_shortcut_projection_matches_legacy_source(
    tmp_path: Path,
) -> None:
    runtime = _rich_runtime(tmp_path, "commands")
    projected = _projection(runtime).commands

    assert projected.commands == runtime.commands
    assert projected.menu_names == runtime.menu_names
    assert projected.descriptions == runtime.descriptions
    assert projected.shortcuts == runtime.shortcuts
    assert projected.commands is not runtime.commands
    assert projected.descriptions is not runtime.descriptions
    assert projected.shortcuts is not runtime.shortcuts


def test_lifecycle_request_hook_projection_matches_legacy_source(
    tmp_path: Path,
) -> None:
    runtime = _rich_runtime(tmp_path, "hooks")
    projected = _projection(runtime).hooks

    assert projected.tool_call == runtime.tool_call_hooks
    assert projected.lifecycle == runtime.lifecycle_hooks
    assert projected.input == runtime.input_hooks
    assert projected.before_agent_start == runtime.before_agent_start_hooks
    assert projected.tool_result == runtime.tool_result_hooks
    assert projected.user_bash == runtime.user_bash_hooks
    assert projected.before_provider_headers == runtime.before_provider_headers_hooks
    assert projected.before_provider_request == runtime.before_provider_request_hooks
    assert projected.session_before_switch == runtime.session_before_switch_hooks
    assert projected.session_before_fork == runtime.session_before_fork_hooks
    assert projected.session_before_compact == runtime.session_before_compact_hooks
    assert projected.session_before_tree == runtime.session_before_tree_hooks
    assert projected.lifecycle is not runtime.lifecycle_hooks
    assert all(isinstance(handlers, tuple) for handlers in projected.lifecycle.values())


def _legacy_tool_port(registered: Any, flags: Mapping[str, object]) -> ToolPort:
    return _build_legacy_extension_tool_port(
        registered,
        has_ui=False,
        notify_sink=lambda *_args: None,
        set_active_tools=lambda _names: True,
        flags=flags,
        render_details={},
        project_trusted=True,
    )


def test_tool_ports_and_capability_match_the_legacy_adapter(tmp_path: Path) -> None:
    runtime = _rich_runtime(tmp_path, "tools")
    projected = _projection(runtime).tools
    source_flags = {"projection-mode": "candidate"}
    legacy_ports = {
        registered.tool.name: _legacy_tool_port(registered, source_flags)
        for registered in runtime.tools
    }
    legacy_state = ToolCapabilityState.build(
        {},
        legacy_ports,
        filter_options=ToolFilterOptions.empty(),
        cancel_join_timeout_seconds=1.0,
    )

    assert projected.registered == runtime.tools
    assert tuple(projected.ports) == tuple(legacy_state.extension_registry)
    assert [port.definition for port in projected.ports.values()] == [
        port.definition for port in legacy_state.extension_registry.values()
    ]
    assert (
        projected.capability_state.active_tool_names == legacy_state.active_tool_names
    )


def test_renderer_projection_matches_every_legacy_renderer_map(tmp_path: Path) -> None:
    runtime = _rich_runtime(tmp_path, "renderers")
    projected = _projection(runtime).renderers

    assert projected.tools == _extension_tool_renderer_map(runtime.tools)
    assert projected.messages == runtime.message_renderers
    assert projected.entries == runtime.entry_renderers
    assert projected.messages is not runtime.message_renderers
    assert projected.entries is not runtime.entry_renderers


def test_provider_projection_matches_legacy_catalog_inputs(tmp_path: Path) -> None:
    runtime = _rich_runtime(tmp_path, "providers")
    projected = _projection(runtime).providers

    assert projected.providers == runtime.providers
    assert projected.unregistered == runtime.unregistered_providers
    assert projected.providers is not runtime.providers
    assert projected.unregistered is not runtime.unregistered_providers


def test_production_runtime_composes_exact_queue_owner_mutex_and_outboxes(
    tmp_path: Path,
) -> None:
    extension_dir = tmp_path / ".pipy/extensions"
    extension_dir.mkdir(parents=True)
    (extension_dir / "routing.py").write_text("def activate(api):\n    pass\n")
    runtime = _activate_workspace_extensions(
        tmp_path,
        _empty_resources(),
        explicit_extension_paths=(extension_dir,),
        include_default_extensions=False,
    )
    lock = threading.RLock()
    ref = SessionGenerationRef(SessionExtensionGeneration(runtime, {}), lock=lock)
    projected = _projection(runtime).queues
    owner = projected.message_routing

    assert ref.lock is projected.user.mutex is projected.custom.mutex is lock
    assert owner.mutex is lock
    assert ref.current.runtime.message_routing is owner is runtime.message_routing
    assert owner is runtime.activation_hosts[0].message_routing
    assert owner.user_outbox is projected.user.storage is runtime.outbox
    assert owner.custom_outbox is projected.custom.storage is runtime.custom_outbox
    assert not hasattr(projected.user, "close")
    assert not hasattr(projected.custom, "drain")


def test_uninstalled_retire_is_a_direct_fallback_noop_and_mismatches_refuse(
    tmp_path: Path,
) -> None:
    runtime = _rich_runtime(tmp_path, "routing-identity")
    runtime.outbox.clear()
    runtime.custom_outbox.clear()
    owner = runtime.message_routing
    assert owner.retire() == owner.retire() == ()
    runtime.activation_hosts[0].send_user_message("still-direct")
    runtime.activation_hosts[0].send_message(
        {"customType": "direct", "content": "still-custom"}
    )
    assert (
        owner.route_drain(lambda: pytest.fail("uninstalled route handled drain"))
        is False
    )
    assert [message.content for message in runtime.outbox] == ["still-direct"]
    assert [message.content for message in runtime.custom_outbox] == ["still-custom"]
    host = runtime.activation_hosts[0]
    result = ActivatedExtension(
        name="identity",
        version="1",
        path_label="identity.py",
        status="activated",
        reason=None,
        commands=(),
        diagnostic=None,
        _activation_host=host,
    )
    batch = ExtensionActivationBatch(
        activated=(result,),
        message_outbox=runtime.outbox,
        custom_message_outbox=runtime.custom_outbox,
    )
    assert batch.message_routing is runtime.message_routing is host.message_routing

    foreign = GenerationMessageRouting([], [], mutex=runtime.message_routing.mutex)
    with pytest.raises(ValueError, match="owner must match every host"):
        ExtensionActivationBatch(
            activated=(result,),
            message_outbox=runtime.outbox,
            custom_message_outbox=runtime.custom_outbox,
            message_routing=foreign,
        )
    with pytest.raises(ValueError, match="runtime routing must own"):
        replace(runtime, message_routing=foreign)
    lock = threading.RLock()
    with pytest.raises(ValueError, match="preserve exact queue storage"):
        ExtensionQueueProjection(
            GenerationQueueHandle(runtime.outbox, lock),
            GenerationQueueHandle(runtime.custom_outbox, lock),
            foreign,
        )
    with pytest.raises(ValueError, match="share the generation queue mutex"):
        _projection(runtime).queues.install_candidate_route(
            OrderedDeliveryGate(threading.RLock())
        )


class _FailingAppendGate(OrderedDeliveryGate):
    def __init__(self, mutex: threading.RLock, phase: int | None) -> None:
        super().__init__(mutex)
        self.phase = phase
        self.calls = 0
        self.prefix_submitted = threading.Event()
        self.continue_prefix = threading.Event()

    def append_reserved(self, deliveries: Any) -> None:
        self.calls += 1
        if self.phase in (None, 2) and self.calls == 1:
            super().append_reserved(deliveries)
            self.prefix_submitted.set()
            assert self.continue_prefix.wait(1)
            return
        if self.calls == self.phase:
            raise RuntimeError(f"phase {self.phase} append failure")
        super().append_reserved(deliveries)


@pytest.mark.parametrize("phase", (None, 1, 2), ids=("success", "phase-1", "phase-2"))
def test_release_is_two_batches_or_failure_terminalizes_without_successor_effect(
    tmp_path: Path, phase: int | None
) -> None:
    mutex = threading.RLock()
    runtime = _rich_runtime(tmp_path, f"release-{phase}", mutex=mutex)
    runtime.outbox.clear()
    queues = _projection(runtime).queues
    gate = _FailingAppendGate(mutex, phase)
    queues.install_candidate_route(gate)
    host = runtime.activation_hosts[0]
    host.send_user_message("prefix")
    failures: list[BaseException] = []
    released: list[int] = []

    with gate.reserve() as token:

        def release() -> None:
            try:
                released.append(queues.release_pending_route())
            except BaseException as exc:  # deterministic injected invariant failure
                failures.append(exc)

        releaser = threading.Thread(target=release)
        releaser.start()
        if phase in (None, 2):
            assert gate.prefix_submitted.wait(1)
            host.send_user_message("tail")
            gate.continue_prefix.set()
        releaser.join(1)
        assert not releaser.is_alive()
        if phase is None:
            assert failures == [] and released == [2] and gate.calls == 2
            host.send_user_message("live")
            gate.release(token)
            assert gate.drain(token)
            assert [message.content for message in runtime.outbox] == [
                "prefix",
                "tail",
                "live",
            ]
            return
        assert len(failures) == 1 and released == [] and gate.calls == phase
        with mutex:
            assert runtime.message_routing._state == "retired"
            assert runtime.message_routing._pending is None
            assert runtime.message_routing._gate is None

        host.send_user_message("late-drop")
        assert queues.release_pending_route() == 0
        assert runtime.message_routing.route_drain(
            lambda: pytest.fail("retired drain callback ran")
        )
        assert queues.retire_route() == queues.retire_route() == ()
        assert mutex.acquire(timeout=1)
        mutex.release()

        successor = _activate_workspace_extensions(
            tmp_path / f"successor-{phase}",
            _empty_resources(),
            include_default_extensions=False,
        )
        ref = SessionGenerationRef(SessionExtensionGeneration(runtime, {}), lock=mutex)
        ref.publish(SessionExtensionGeneration(successor, {}))
        host.send_user_message("post-publish-drop")
        assert runtime.message_routing.route_drain(lambda: None)
        assert runtime.message_routing.retire() == ()
        gate.release(token)
        assert gate.drain(token)

    assert successor.outbox == successor.custom_outbox == []
    assert [message.content for message in runtime.outbox] == (
        ["prefix"] if phase == 2 else []
    )


def test_renderer_uses_one_snapshot_while_direct_custom_stays_unconditional(
    tmp_path: Path,
) -> None:
    mutex = threading.RLock()
    first = _rich_runtime(tmp_path, "renderer-first", mutex=mutex)
    first.outbox[:] = [QueuedUserMessage("first", {})]
    ref = SessionGenerationRef(SessionExtensionGeneration(first, {}), lock=mutex)
    queues = _projection(first).queues
    gate = OrderedDeliveryGate(mutex)
    queues.install_candidate_route(gate)
    snapshots: list[SessionGenerationSnapshot] = []

    def snapshot() -> SessionGenerationSnapshot:
        snapshots.append(ref.snapshot())
        return snapshots[-1]

    state = SimpleNamespace(
        session_tree=SimpleNamespace(
            append_custom_message=lambda *_args, **_kwargs: SimpleNamespace(id="entry")
        ),
        extension_renderer_map={},
        extension_entry_renderer_map={},
        extension_message_outbox=first.outbox,
        extension_custom_message_outbox=first.custom_outbox,
        extension_in_agent_turn=False,
    )
    renderer = _CustomEntryRenderer(
        session=SimpleNamespace(_emit_diagnostic=lambda *_args: None),
        ctl=state,
        terminal_ui=None,
        coding_input_queue=CodingInputQueue(),
        error_stream=sys.stderr,
        generation_snapshot=snapshot,
    )

    with gate.reserve() as token:
        assert renderer.extension_send_message("direct", "direct", False, {}) == "entry"
        renderer.drain_extension_outboxes()
        assert queues.release_pending_route() == 1
        gate.release(token)
        assert gate.drain(token) and first.outbox == []
    queues.retire_route()
    assert renderer.extension_send_message("retired", "direct", False, {}) == "entry"
    second = _rich_runtime(tmp_path, "renderer-second", mutex=mutex)
    ref.publish(SessionExtensionGeneration(second, {}))
    renderer.drain_extension_outboxes()
    assert len(snapshots) == 2


def test_chrome_projection_carries_the_exact_r2_handle(tmp_path: Path) -> None:
    runtime = _rich_runtime(tmp_path, "chrome")
    chrome = ExtensionChromeSink()
    projected = _projection(runtime, chrome=chrome)

    assert projected.chrome is not None
    assert projected.chrome.sink is chrome


@pytest.mark.parametrize(
    ("has_ui", "project_trusted"),
    ((True, False), (False, True)),
    ids=("ui-only", "trusted-headless"),
)
def test_production_projection_tool_port_matches_legacy_behavior_without_aliases(
    tmp_path: Path, has_ui: bool, project_trusted: bool
) -> None:
    runtime = _rich_runtime(tmp_path, "composition-adapter")
    observations: list[dict[str, object]] = []

    def handler(ctx: Any, params: Mapping[str, object]) -> ToolResult:
        side = str(params["side"])
        phase = str(params["phase"])
        flags_before = dict(ctx.flags)
        active_result = ctx.set_active_tools(("projected_tool", "secondary"))
        ctx.ui.notify(f"{side}-{phase}", "warning")
        observations.append(
            {
                "side": side,
                "phase": phase,
                "has_ui": ctx.has_ui,
                "project_trusted": ctx.is_project_trusted(),
                "flags": flags_before,
                "active_result": active_result,
            }
        )
        # A handler receives a mutable snapshot. Mutating it must affect neither
        # a later invocation nor the other adapter's private flag snapshot.
        ctx.flags["projection-mode"] = f"{side}-handler-mutated"
        return ToolResult(
            content=(
                f"ui={ctx.has_ui};trusted={ctx.is_project_trusted()};"
                f"flag={flags_before['projection-mode']};active={active_result}"
            ),
            details={"side": side, "phase": phase},
        )

    registered = runtime.tools[0]
    registered = replace(
        registered,
        tool=replace(
            registered.tool,
            handler=handler,
            render_result=lambda _ctx: None,
        ),
    )
    runtime = replace(runtime, tools=(registered,))
    lock = runtime.message_routing.mutex
    assert lock is not None
    source_flags: dict[str, object] = {"projection-mode": "candidate"}
    notices: list[tuple[str, str]] = []
    active_calls: list[tuple[str, ...]] = []
    render_details: dict[str, object | None] = {"preexisting": {"sink": "preserved"}}

    def notify(kind: str, text: str) -> None:
        notices.append((kind, text))

    def set_active_tools(names: Sequence[str]) -> bool:
        active_calls.append(tuple(names))
        return False

    def prepare(ports: Mapping[str, ToolPort]) -> ToolCapabilityState:
        return ToolCapabilityState.build(
            {},
            ports,
            filter_options=ToolFilterOptions.empty(),
            cancel_join_timeout_seconds=1.0,
        )

    projected = _build_candidate_extension_projection(
        runtime,
        source_flags,
        queue_mutex=lock,
        reference_mutex=lock,
        has_ui=has_ui,
        notify_sink=notify,
        set_active_tools=set_active_tools,
        render_details=render_details,
        project_trusted=project_trusted,
        prepare_capability=prepare,
        chrome=None,
    )
    projected_port = projected.tools.ports["projected_tool"]
    legacy_port = _build_legacy_extension_tool_port(
        registered,
        has_ui=has_ui,
        notify_sink=notify,
        set_active_tools=set_active_tools,
        flags=source_flags,
        render_details=render_details,
        project_trusted=project_trusted,
    )

    assert projected_port.definition == legacy_port.definition
    source_flags["projection-mode"] = "caller-mutated"
    context = ToolContext(workspace_root=tmp_path, stderr_sink=lambda _text: None)
    outcomes: dict[str, list[tuple[str, bool]]] = {
        "projected": [],
        "legacy": [],
    }
    for side, port in (("projected", projected_port), ("legacy", legacy_port)):
        for phase in ("mutate", "probe"):
            result = port.invoke(
                ToolRequest(
                    make_tool_request_id(),
                    "projected_tool",
                    {"side": side, "phase": phase},
                    provider_correlation_id=f"{side}-{phase}",
                ),
                context,
            )
            outcomes[side].append((result.output_text, result.is_error))

    expected_output = (
        f"ui={has_ui};trusted={project_trusted};flag=candidate;active=False"
    )
    assert outcomes == {
        "projected": [(expected_output, False), (expected_output, False)],
        "legacy": [(expected_output, False), (expected_output, False)],
    }
    expected_invocations = [
        (side, phase)
        for side in ("projected", "legacy")
        for phase in ("mutate", "probe")
    ]
    assert observations == [
        {
            "side": side,
            "phase": phase,
            "has_ui": has_ui,
            "project_trusted": project_trusted,
            "flags": {"projection-mode": "candidate"},
            "active_result": False,
        }
        for side, phase in expected_invocations
    ]
    assert notices == [
        ("warning", f"{side}-{phase}") for side, phase in expected_invocations
    ]
    assert active_calls == [
        ("projected_tool", "secondary") for _ in expected_invocations
    ]
    assert render_details == {
        "preexisting": {"sink": "preserved"},
        **{
            f"{side}-{phase}": {"side": side, "phase": phase}
            for side, phase in expected_invocations
        },
    }
    assert projected.runtime_flags.values == {"projection-mode": "candidate"}


def test_duplicate_projected_definition_names_preserve_legacy_last_wins(
    tmp_path: Path,
) -> None:
    runtime = _rich_runtime(tmp_path, "duplicate-definition")
    first = runtime.tools[0]
    second = replace(first, tool=replace(first.tool, description="last definition"))
    projected = _projection(replace(runtime, tools=(first, second))).tools

    assert tuple(projected.ports) == ("projected_tool",)
    assert len(projected.registered) == 2
    assert projected.ports["projected_tool"].definition.description == "last definition"


@pytest.mark.parametrize("failed_step", PROJECTION_BUILD_STEPS)
def test_each_projection_builder_failure_returns_no_candidate_and_changes_no_live_state(
    tmp_path: Path, failed_step: str
) -> None:
    live_runtime = _rich_runtime(tmp_path, "failure-live")
    live_generation = SessionExtensionGeneration(
        live_runtime, {"projection-mode": "live"}
    )
    live_mutex = live_runtime.message_routing.mutex
    assert live_mutex is not None
    ref = SessionGenerationRef(live_generation, lock=live_mutex)
    before = ref.snapshot()
    live_flag_values = live_generation.flag_values
    before_flag_values = dict(live_flag_values)
    legacy_runtime_field_refs = {
        field.name: getattr(live_runtime, field.name)
        for field in fields(_ExtensionRuntime)
    }
    legacy_container_refs = {
        name: value
        for name, value in legacy_runtime_field_refs.items()
        if isinstance(value, (dict, list))
    }
    assert set(legacy_container_refs) == {
        "commands",
        "descriptions",
        "lifecycle_hooks",
        "outbox",
        "custom_outbox",
        "shortcuts",
        "message_renderers",
        "entry_renderers",
    }
    before_container_values = {
        name: value.copy() for name, value in legacy_container_refs.items()
    }
    candidate_runtime = _rich_runtime(
        tmp_path, f"failure-{failed_step}", mutex=ref.lock
    )

    def fail(name: str) -> None:
        if name == failed_step:
            raise RuntimeError(f"injected {name}")

    with pytest.raises(RuntimeError, match=f"injected {failed_step}"):
        _projection(candidate_runtime, lock=ref.lock, step_observer=fail)

    after = ref.snapshot()
    assert after.generation is before.generation
    assert after.generation is live_generation
    assert after.generation_id == before.generation_id
    assert after.generation.runtime is live_runtime
    assert live_generation.flag_values is live_flag_values
    assert live_generation.flag_values == before_flag_values
    for name, legacy_adapter_or_container in legacy_runtime_field_refs.items():
        assert getattr(live_runtime, name) is legacy_adapter_or_container
    for name, before_contents in before_container_values.items():
        assert getattr(live_runtime, name) == before_contents


def test_foreign_reference_mutex_fails_before_projection_construction(
    tmp_path: Path,
) -> None:
    runtime = _rich_runtime(tmp_path, "foreign-reference")
    observed: list[str] = []
    with pytest.raises(ValueError, match="share one mutex"):
        build_test_projection(
            runtime,
            {"projection-mode": "candidate"},
            queue_mutex=threading.RLock(),
            reference_mutex=threading.RLock(),
            step_observer=observed.append,
        )
    assert observed == []


def test_invalid_builder_results_fail_before_returning_a_projection(
    tmp_path: Path,
) -> None:
    runtime = _rich_runtime(tmp_path, "invalid-builder")
    lock = runtime.message_routing.mutex
    assert lock is not None

    def port(registered: Any, flags: Mapping[str, object]) -> ToolPort:
        return _build_projected_extension_tool_port(
            registered,
            has_ui=False,
            notify_sink=lambda *_args: None,
            set_active_tools=lambda _names: True,
            flags=flags,
            render_details={},
            project_trusted=True,
        )

    def wrong_capability(_ports: Mapping[str, ToolPort]) -> ToolCapabilityState:
        return ToolCapabilityState.build(
            {},
            {},
            filter_options=ToolFilterOptions.empty(),
            cancel_join_timeout_seconds=1.0,
        )

    with pytest.raises(ValueError, match="must contain projected ports"):
        build_extension_projection(
            runtime,
            {"projection-mode": "candidate"},
            queue_mutex=lock,
            reference_mutex=lock,
            build_tool_port=port,
            build_tool_capability=wrong_capability,
            chrome=None,
        )

    with pytest.raises(TypeError, match="storage must be a list"):
        GenerationQueueHandle(cast(Any, ()), lock)
    with pytest.raises(TypeError, match="mutex must be an RLock"):
        GenerationQueueHandle([], cast(Any, object()))
    with pytest.raises(TypeError, match="chrome sink"):
        ExtensionChromeHandle(cast(Any, object()))

    with pytest.raises(TypeError, match="string-keyed mapping"):
        build_extension_projection(
            runtime,
            cast(Any, {1: "candidate"}),
            queue_mutex=lock,
            reference_mutex=lock,
            build_tool_port=port,
            build_tool_capability=wrong_capability,
            chrome=None,
        )
    foreign_object = cast(Any, object())
    with pytest.raises(TypeError, match="must be RLocks"):
        build_extension_projection(
            runtime,
            {"projection-mode": "candidate"},
            queue_mutex=foreign_object,
            reference_mutex=foreign_object,
            build_tool_port=port,
            build_tool_capability=wrong_capability,
            chrome=None,
        )
    with pytest.raises(TypeError, match="step_observer"):
        build_extension_projection(
            runtime,
            {"projection-mode": "candidate"},
            queue_mutex=lock,
            reference_mutex=lock,
            build_tool_port=port,
            build_tool_capability=wrong_capability,
            chrome=None,
            step_observer=cast(Any, object()),
        )


def test_successor_projections_share_no_mutable_mapping_or_list(
    tmp_path: Path,
) -> None:
    old_runtime = _rich_runtime(tmp_path, "isolated-old")
    candidate_runtime = _rich_runtime(tmp_path, "isolated-candidate")
    old_chrome = ExtensionChromeSink()
    candidate_chrome = ExtensionChromeSink()
    old = _projection(old_runtime, chrome=old_chrome)
    candidate = _projection(candidate_runtime, chrome=candidate_chrome)
    old_mappings = (
        old.runtime_flags.values,
        old.commands.commands,
        old.commands.descriptions,
        old.commands.shortcuts,
        old.hooks.lifecycle,
        old.tools.ports,
        old.tools.capability_state.extension_registry,
        old.renderers.tools,
        old.renderers.messages,
        old.renderers.entries,
    )
    candidate_mappings = (
        candidate.runtime_flags.values,
        candidate.commands.commands,
        candidate.commands.descriptions,
        candidate.commands.shortcuts,
        candidate.hooks.lifecycle,
        candidate.tools.ports,
        candidate.tools.capability_state.extension_registry,
        candidate.renderers.tools,
        candidate.renderers.messages,
        candidate.renderers.entries,
    )

    assert all(isinstance(value, MappingProxyType) for value in old_mappings)
    assert all(isinstance(value, MappingProxyType) for value in candidate_mappings)
    assert not {id(value) for value in old_mappings}.intersection(
        id(value) for value in candidate_mappings
    )
    assert old.queues.user.storage is old_runtime.outbox
    assert candidate.queues.user.storage is candidate_runtime.outbox
    assert old_runtime.outbox is not candidate_runtime.outbox
    assert old_runtime.custom_outbox is not candidate_runtime.custom_outbox
    assert old.chrome is not None and candidate.chrome is not None
    assert old.chrome is not candidate.chrome
    assert old.chrome.sink is old_chrome
    assert candidate.chrome.sink is candidate_chrome

    names = tuple(old.commands.commands)
    hook_names = tuple(old.hooks.lifecycle)
    renderer_names = tuple(old.renderers.messages)
    old_runtime.commands.clear()
    old_runtime.lifecycle_hooks.clear()
    old_runtime.message_renderers.clear()
    assert tuple(old.commands.commands) == names
    assert tuple(old.hooks.lifecycle) == hook_names
    assert tuple(old.renderers.messages) == renderer_names

    with pytest.raises(TypeError):
        cast(dict[str, Any], candidate.commands.commands)["late"] = next(
            iter(candidate.commands.commands.values())
        )


def test_projection_omits_settings_keybindings_resources_and_reverse_adapters() -> None:
    projection_fields = {field.name for field in fields(ExtensionProjection)}
    forbidden = {"settings", "keybindings", "resources", "workspace_resources"}
    assert projection_fields.isdisjoint(forbidden)

    source = (
        Path(__file__).parents[1] / "src/pipy_harness/native/session_generation.py"
    ).read_text(encoding="utf-8")
    imports = {
        alias.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert imports.isdisjoint(
        {
            "SettingsManager",
            "SettingsState",
            "KeybindingsManager",
            "KeybindingsState",
            "WorkspaceResources",
            "PackageResourceRoots",
        }
    )
    assert "settings_adapter" not in source
    extension_boundary = (
        Path(__file__).parents[1] / "src/pipy_harness/native/extension_runtime.py"
    ).read_text(encoding="utf-8")
    assert "session_generation" not in extension_boundary
    assert "extension_chrome_state" not in extension_boundary
    assert "tool_capabilities" not in extension_boundary


def test_production_projection_and_port_adapter_callers_are_exactly_bounded() -> None:
    target_names = {
        "build_extension_projection",
        "_build_candidate_extension_projection",
        "_build_projected_extension_tool_port",
        "_build_legacy_extension_tool_port",
    }
    calls: dict[str, list[tuple[str, tuple[str, ...]]]] = {
        name: [] for name in target_names
    }

    class CallInventory(ast.NodeVisitor):
        def __init__(self, path: str) -> None:
            self.path = path
            self.owners: list[str] = []

        def _visit_owner(self, label: str, node: ast.AST) -> None:
            self.owners.append(label)
            self.generic_visit(node)
            self.owners.pop()

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            self._visit_owner(f"class:{node.name}", node)

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self._visit_owner(f"function:{node.name}", node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self._visit_owner(f"async-function:{node.name}", node)

        def visit_Call(self, node: ast.Call) -> None:
            name = (
                node.func.id
                if isinstance(node.func, ast.Name)
                else node.func.attr
                if isinstance(node.func, ast.Attribute)
                else ""
            )
            if name in calls:
                calls[name].append((self.path, tuple(self.owners)))
            self.generic_visit(node)

    src_root = Path(__file__).parents[1] / "src"
    package_root = src_root / "pipy_harness"
    for path in sorted(package_root.rglob("*.py")):
        relative_path = path.relative_to(src_root).as_posix()
        CallInventory(relative_path).visit(ast.parse(path.read_text(encoding="utf-8")))

    loop_path = "pipy_harness/native/tool_loop_session.py"
    reload_owner = (
        "class:_ReloadCommandEffects",
        "function:_reload_extension_generation",
    )
    startup_owner = ("class:NativeToolReplSession", "function:run")
    assert calls == {
        "_build_candidate_extension_projection": [
            (loop_path, reload_owner),
            (loop_path, startup_owner),
        ],
        "build_extension_projection": [
            (
                "pipy_harness/native/tool_loop_session.py",
                ("function:_build_candidate_extension_projection",),
            )
        ],
        "_build_projected_extension_tool_port": [
            (
                "pipy_harness/native/tool_loop_session.py",
                (
                    "function:_build_candidate_extension_projection",
                    "function:build_tool_port",
                ),
            )
        ],
        "_build_legacy_extension_tool_port": [],
    }


_FamilyValue = TypeVar("_FamilyValue")


def _reload_preparation_ports(  # noqa: C901 - fixture exercises all 15 families
    tmp_path: Path,
    *,
    failed_step: str | None = None,
    trace: list[str] | None = None,
    disposed: list[str] | None = None,
    disposal_failures: frozenset[str] = frozenset(),
) -> tuple[Any, ...]:
    observed = [] if trace is None else trace
    cleaned = [] if disposed is None else disposed
    payload_sources: dict[str, list[object]] = {
        name: [name, {"source": name}]
        for name in PREPARED_RELOAD_BUILD_STEPS
        if name not in {"projection", "chrome_prepare_input"}
    }
    usage_source = AgentUsageAccumulator()
    usage_source.absorb(AgentProviderUsageSample(input_tokens=7, total_tokens=7))
    payload_sources["coding_usage"] = [usage_source]
    projection_source = _projection(_rich_runtime(tmp_path, "prepared-source"))
    chrome_source = ExtensionChromePrepareInput(ExtensionChromeSink())

    def finish(name: str, value: _FamilyValue) -> DetachedReloadEffect[_FamilyValue]:
        observed.append(f"{name}:finish")

        def dispose() -> None:
            cleaned.append(name)
            if name in disposal_failures:
                raise RuntimeError(f"cleanup {name}")

        return DetachedReloadEffect(value, dispose)

    def payload_builder(
        name: str, constructor: Callable[[tuple[object, ...]], _FamilyValue]
    ) -> Callable[[], DetachedReloadEffect[_FamilyValue]]:
        def build() -> DetachedReloadEffect[_FamilyValue]:
            observed.append(f"{name}:start")
            if name == failed_step:
                raise RuntimeError(f"injected {name}")
            return finish(name, constructor(tuple(payload_sources[name])))

        return build

    def projection_builder() -> DetachedReloadEffect[ExtensionProjection]:
        observed.append("projection:start")
        if failed_step == "projection":
            raise RuntimeError("injected projection")
        return finish("projection", replace(projection_source))

    def chrome_builder() -> DetachedReloadEffect[ExtensionChromePrepareInput]:
        observed.append("chrome_prepare_input:start")
        if failed_step == "chrome_prepare_input":
            raise RuntimeError("injected chrome_prepare_input")
        return finish("chrome_prepare_input", replace(chrome_source))

    def owner_value(value: tuple[object, ...]) -> Any:
        return value

    def provider_refresh_value(
        _value: tuple[object, ...],
    ) -> ProviderCatalogRefreshValue:
        state = ProviderCatalogState(
            models_json_path=tmp_path / "prepared-models.json",
            auth_store=AuthStore(path=tmp_path / "prepared-auth.json"),
            env={},
        )
        return state.prepare_catalog_auth_refresh()

    def usage_value(value: tuple[object, ...]) -> AgentUsageReloadValue:
        accumulator = cast(AgentUsageAccumulator, value[0])
        return accumulator.prepare_reload_value_refresh()

    constructors = (
        ActivationInputsValue,
        owner_value,
        ProviderFactoryValue,
        provider_refresh_value,
        owner_value,
        owner_value,
        owner_value,
        usage_value,
        CodingCompactionValue,
        owner_value,
        owner_value,
        TemporaryLegacyValue,
        PresentationPersistenceValue,
    )
    builders: dict[str, Any] = {
        name: payload_builder(name, constructor)
        for name, constructor in zip(payload_sources, constructors, strict=True)
    }
    builders.update(projection=projection_builder, chrome_prepare_input=chrome_builder)
    return (
        ReloadEffectPreparationPorts(**cast(Any, builders)),
        payload_sources,
        projection_source,
        chrome_source,
    )


@pytest.mark.parametrize("failed_step", PREPARED_RELOAD_BUILD_STEPS)
def test_each_detached_reload_preparation_failure_isolated_from_typed_sources(
    failed_step: str, tmp_path: Path
) -> None:
    disposed: list[str] = []
    ports, sources, projection_source, chrome_source = _reload_preparation_ports(
        tmp_path,
        failed_step=failed_step,
        disposed=disposed,
        disposal_failures=frozenset({"activation_inputs"}),
    )
    source_contents = {name: list(value) for name, value in sources.items()}
    projection_before = replace(projection_source)
    chrome_before = chrome_source.candidate.snapshot()

    with pytest.raises(RuntimeError, match=f"injected {failed_step}"):
        _build_detached_reload_effects(ports)

    failed_index = PREPARED_RELOAD_BUILD_STEPS.index(failed_step)
    assert disposed == list(reversed(PREPARED_RELOAD_BUILD_STEPS[:failed_index]))
    assert sources == source_contents
    assert projection_source == projection_before
    assert chrome_source.candidate.snapshot() == chrome_before


def test_mutable_reload_builders_finish_before_one_frozen_prepared_assembly(
    tmp_path: Path,
) -> None:
    trace: list[str] = []
    ports, sources, projection_source, chrome_source = _reload_preparation_ports(
        tmp_path, trace=trace
    )
    prepared = _build_detached_reload_effects(
        ports, step_observer=lambda name: trace.append(f"observed:{name}")
    )

    expected: list[str] = []
    for name in PREPARED_RELOAD_BUILD_STEPS:
        expected.extend((f"{name}:start", f"{name}:finish", f"observed:{name}"))
    expected.append("observed:prepared_reload_effects")
    assert trace == expected
    assert isinstance(prepared, PreparedReloadEffects)
    assert [field.name for field in fields(PreparedReloadEffects)] == [
        *PREPARED_RELOAD_BUILD_STEPS
    ]
    with pytest.raises(FrozenInstanceError):
        prepared.activation_inputs = cast(Any, None)  # type: ignore[misc]
    for name, source_value in sources.items():
        effect = cast(Any, getattr(prepared, name))
        if name == "provider_refresh":
            assert isinstance(effect.value, ProviderCatalogRefreshValue)
        elif name == "coding_usage":
            usage_value = cast(AgentUsageRefreshValue, effect.value)
            usage_source = cast(AgentUsageAccumulator, source_value[0])
            assert usage_value.retained.input_tokens == 7
            usage_source.absorb(
                AgentProviderUsageSample(input_tokens=5, total_tokens=5)
            )
            assert usage_value.retained.input_tokens == 7
        else:
            assert effect.value == tuple(source_value)
        assert effect.value is not source_value
    assert prepared.projection.value is not projection_source
    assert prepared.chrome_prepare_input.value is not chrome_source
    assert prepared.chrome_prepare_input.value.candidate is chrome_source.candidate


def test_provider_construction_inventory_excludes_catalog_and_auth_owners() -> None:
    assert list(inspect.signature(build_provider).parameters) == [
        "resolved",
        "spec",
        "thinking_level",
        "options",
        "http_client",
    ]
    assert list(inspect.signature(try_build_extension_provider_port).parameters) == [
        "registered",
        "model_id",
    ]
    assert [field.name for field in fields(ProviderContext)] == [
        "provider_name",
        "default_model",
        "model_id",
    ]


def test_builtin_models_json_disappearance_is_shadow_only_and_publishable(
    tmp_path: Path,
) -> None:
    models = tmp_path / "models.json"
    models.write_text(
        '{"providers":{"temporary":{"baseUrl":"https://example.invalid/v1","apiKey":"key","api":"openai-completions","models":[{"id":"active"}]}}}',
        encoding="utf-8",
    )
    catalog = ProviderCatalogState(
        models, AuthStore(path=tmp_path / "auth.json"), {"OPENAI_API_KEY": "x"}
    )
    selection = NativeModelSelection("temporary", "active")
    state = NativeReplProviderState(
        selection, ModelRuntime(catalog), persist_defaults=False
    )
    coding = CodingSessionState(
        provider=state.current_provider(),
        provider_name=selection.provider_name,
        model_id=selection.model_id,
    )
    live_rows, live_binding = catalog.catalog.rows, coding.provider_binding
    models.unlink()
    _, refresh, shadow, binding, _, _, strategy, diagnostic = (
        prepare_provider_reload_values(
            state,
            coding,
            _projection(_rich_runtime(tmp_path, "models-shadow")),
            unavailable_provider=lambda _message: pytest.fail("fallback must exist"),
            usage_prototype=lambda _selection: AgentUsageAccumulator(),
            empty_history=CodingReloadHistoryValue(()),
        )
    )
    assert catalog.catalog.rows is live_rows and coding.provider_binding is live_binding
    assert state.selection is selection and state.pending_default is None
    assert strategy == "fallback" and shadow.selection != selection
    ref = shadow.selection.reference
    assert diagnostic == f"pipy: active model disappeared on reload; selected {ref}."
    shadow_catalog = weakref.ref(shadow.model_runtime.catalog.catalog)
    shadow_auth = weakref.ref(cast(AuthStore, shadow.model_runtime.catalog.auth_store))
    accepted_provider = binding.replacement.provider
    assert catalog.catalog_auth_refresh_matches_expected(refresh)
    catalog.publish_catalog_auth_refresh(refresh)
    cast(AuthStore, catalog.auth_store).set("rotated", {"type": "api_key", "key": "x"})
    catalog.catalog.refresh()
    del shadow
    gc.collect()
    assert shadow_catalog() is shadow_auth() is None
    assert binding.replacement.provider is accepted_provider
    assert catalog.find("temporary", "active") is None


def test_prepared_disposal_attempts_all_in_reverse_and_groups_errors(
    tmp_path: Path,
) -> None:
    disposed: list[str] = []
    ports, *_rest = _reload_preparation_ports(
        tmp_path,
        disposed=disposed,
        disposal_failures=frozenset({"provider_factory", "capability"}),
    )
    prepared = _build_detached_reload_effects(ports)
    with pytest.raises(ExceptionGroup) as raised:
        prepared.dispose()
    assert disposed == list(reversed(PREPARED_RELOAD_BUILD_STEPS))
    assert [str(error) for error in raised.value.exceptions] == [
        "cleanup capability",
        "cleanup provider_factory",
    ]


def test_prepared_reload_owner_families_use_concrete_owner_values() -> None:
    localns = {
        "AgentUsageReloadValue": AgentUsageReloadValue,
        "CodingReloadBindingValue": CodingReloadBindingValue,
        "CodingReloadHistoryValue": CodingReloadHistoryValue,
        "ExtensionChromePrepareInput": ExtensionChromePrepareInput,
        "ProviderCatalogRefreshValue": ProviderCatalogRefreshValue,
        "ProviderCatalogReloadState": ProviderCatalogReloadState,
        "ReplPendingDefaultReloadValue": ReplPendingDefaultReloadValue,
        "ReplSelectionReloadValue": ReplSelectionReloadValue,
    }
    port_hints = get_type_hints(ReloadEffectPreparationPorts, localns=localns)
    prepared_hints = get_type_hints(PreparedReloadEffects, localns=localns)
    expected = {
        "provider_catalog": ProviderCatalogReloadState,
        "provider_refresh": ProviderCatalogRefreshValue,
        "provider_fallback": ReplSelectionReloadValue,
        "coding_binding": CodingReloadBindingValue,
        "coding_history": CodingReloadHistoryValue,
        "coding_usage": AgentUsageReloadValue,
        "unavailable_default": ReplPendingDefaultReloadValue,
        "capability": ToolCapabilityState,
    }
    for name, value_type in expected.items():
        port_args = cast(Any, port_hints[name]).__args__
        assert value_type in cast(Any, port_args[-1]).__args__
        assert value_type in cast(Any, prepared_hints[name]).__args__
    opaque = {"coding_compaction": CodingCompactionValue}
    for name, value_type in opaque.items():
        port_args = cast(Any, port_hints[name]).__args__
        assert value_type in cast(Any, port_args[-1]).__args__
        assert value_type in cast(Any, prepared_hints[name]).__args__


def test_session_generation_runtime_dependency_closure_excludes_owner_stacks() -> None:
    """Prove the module's own closure with synthetic parents, not package init."""

    package_root = Path(__file__).parents[1] / "src/pipy_harness"
    native_root = package_root / "native"
    script = f"""\
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

importlib.import_module("pipy_harness.native.session_generation")
forbidden = (
    "pipy_harness.native.auth_store",
    "pipy_harness.native.catalog_state",
    "pipy_harness.native.coding",
    "pipy_harness.native.repl_state",
)
loaded = sorted(
    name
    for name in sys.modules
    if any(name == prefix or name.startswith(prefix + ".") for prefix in forbidden)
)
assert loaded == [], loaded
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_r3c1_owner_apis_are_installed_only_by_session_generation() -> None:
    expected_definitions = {
        "prepare_extension_provider_contributions": "native/catalog_state.py",
        "publish_extension_provider_contributions": "native/catalog_state.py",
        "prepare_reload_refresh": "native/coding/state.py",
        "prepare_reload_rebind": "native/coding/state.py",
        "reload_binding_matches_expected": "native/coding/state.py",
        "publish_reload_refresh": "native/coding/state.py",
        "publish_reload_rebind": "native/coding/state.py",
        "prepare_reload_usage_refresh": "native/coding/state.py",
        "prepare_reload_usage_fallback": "native/coding/state.py",
        "reload_usage_matches_expected": "native/coding/state.py",
        "publish_reload_usage_refresh": "native/coding/state.py",
        "publish_reload_usage_fallback": "native/coding/state.py",
        "prepare_reload_state": "native/repl_state.py",
        "reload_state_matches_expected": "native/repl_state.py",
        "publish_reload_state": "native/repl_state.py",
        "publish_catalog_auth_refresh": "native/catalog_state.py",
    }
    expected_preparation_arity = {
        "prepare_reload_refresh": (["self", "provider"], []),
        "prepare_reload_rebind": (
            ["self", "provider"],
            ["provider_name", "model_id"],
        ),
        "prepare_reload_usage_refresh": (["self"], []),
        "prepare_reload_usage_fallback": (["self", "replacement_prototype"], []),
        "prepare_reload_state": (
            ["self"],
            ["selection", "pending_default"],
        ),
    }
    definitions: dict[str, list[str]] = {name: [] for name in expected_definitions}
    calls: dict[str, list[Any]] = {name: [] for name in expected_definitions}
    source_root = Path(__file__).parents[1] / "src/pipy_harness"
    source_paths = sorted(source_root.rglob("*.py"))
    assert source_paths
    for path in source_paths:
        relative = path.relative_to(source_root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name in definitions:
                    definitions[node.name].append(relative)
                if node.name in expected_preparation_arity:
                    positional = [arg.arg for arg in node.args.args]
                    keyword_only = [arg.arg for arg in node.args.kwonlyargs]
                    assert (positional, keyword_only) == expected_preparation_arity[
                        node.name
                    ]
                    assert node.args.posonlyargs == []
                    assert node.args.vararg is None
                    assert node.args.kwarg is None
                    assert node.args.defaults == []
                    assert all(value is None for value in node.args.kw_defaults)
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in expected_definitions
            ):
                owners = tuple(
                    f"{type(owner).__name__.removesuffix('Def').lower()}:{owner.name}"
                    for owner in ast.walk(tree)
                    if isinstance(owner, (ast.ClassDef, ast.FunctionDef))
                    and owner.lineno <= node.lineno <= (owner.end_lineno or -1)
                )
                calls[node.func.attr].append((relative, owners))
    assert definitions
    assert sum(map(len, definitions.values())) == len(expected_definitions)
    assert definitions == {
        name: [expected_path] for name, expected_path in expected_definitions.items()
    }
    sg = "native/session_generation.py"
    acceptance = (sg, ("class:SessionGenerationRef", "function:accept_prepared_reload"))
    preparation = (sg, ("function:prepare_provider_reload_values",))
    expected_calls = {
        name: [preparation if name.startswith("prepare_") else acceptance]
        for name in expected_definitions
    }
    production = (sg, ("function:prepare_production_reload",))
    shadow = (sg, ("function:build_provider_reload_shadow",))
    expected_calls["prepare_reload_state"] = [production]
    expected_calls["publish_extension_provider_contributions"] = [shadow, acceptance]
    expected_calls["publish_catalog_auth_refresh"] = [shadow, acceptance]
    assert calls == expected_calls


def test_catalog_auth_api_is_installed_and_phase_b_is_comparison_only() -> None:
    root = Path(__file__).parents[1] / "src/pipy_harness"
    api_groups = (
        (
            "native/auth_store.py",
            "capture_reload_expected prepare_reload_data_from_snapshot prepare_reload_data "
            "validate_prepared_reload_data reload_data_matches_expected publish_reload_data",
        ),
        (
            "native/models_json.py",
            "capture_catalog_reload_expected prepare_catalog_reload_from_snapshot "
            "prepare_catalog_reload validate_prepared_catalog_reload "
            "catalog_reload_matches_expected publish_catalog_reload",
        ),
        (
            "native/catalog_state.py",
            "prepare_catalog_auth_refresh validate_prepared_catalog_auth_refresh "
            "catalog_auth_refresh_matches_expected publish_catalog_auth_refresh",
        ),
    )
    owner_apis = {name: path for path, names in api_groups for name in names.split()}
    publisher_targets = {
        "publish_reload_data": (
            "self._data self._reload_identity prepared.data prepared.validated_data "
            "prepared.expected_owner_token prepared.replacement_owner_token"
        ),
        "publish_catalog_reload": (
            "self.rows self.error self.provider_request_configs self._config "
            "self._reload_identity prepared.rows prepared.error "
            "prepared.provider_request_configs prepared.config prepared.replacement_rows "
            "prepared.replacement_provider_request_configs "
            "prepared.replacement_config prepared.expected_owner_token "
            "prepared.replacement_owner_token"
        ),
    }
    definitions: dict[str, list[tuple[str, ast.FunctionDef]]] = {
        name: [] for name in owner_apis
    }
    calls: dict[str, list[str]] = {name: [] for name in owner_apis}
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.FunctionDef) and node.name in definitions:
                definitions[node.name].append((relative, node))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in calls:
                    calls[node.func.attr].append(relative)
                assert node.func.attr not in {
                    "_capture_reload_expected",
                    "_prepare_reload_data",
                    "_capture_catalog_reload_expected",
                    "_prepare_catalog_reload",
                }

    assert {
        name: [path for path, _ in found] for name, found in definitions.items()
    } == {name: [path] for name, path in owner_apis.items()}
    assert {name for name, paths in calls.items() if not paths} == set(
        "prepare_reload_data prepare_catalog_reload".split()
    )
    installed = "prepare_catalog_auth_refresh catalog_auth_refresh_matches_expected publish_catalog_auth_refresh".split()
    assert {name: calls[name] for name in installed} == {
        name: ["native/session_generation.py"]
        * (2 if name == "publish_catalog_auth_refresh" else 1)
        for name in installed
    }
    for name, targets in publisher_targets.items():
        publisher = definitions[name][0][1]
        assert [
            ast.unparse(node.targets[0])
            for node in publisher.body
            if isinstance(node, ast.Assign)
        ] == targets.split()
        assert not any(isinstance(node, ast.Call) for node in ast.walk(publisher))
        guards = [node for node in publisher.body if isinstance(node, ast.If)]
        assert len(guards) == 1 and len(guards[0].body) == 1
        assert isinstance(guards[0].body[0], ast.Return)
    aggregate_publisher = definitions["publish_catalog_auth_refresh"][0][1]
    assert [
        node.func.attr
        for node in ast.walk(aggregate_publisher)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    ] == ["publish_catalog_reload", "publish_reload_data"]
    assert not any(
        isinstance(node, (ast.Assert, ast.Raise))
        for node in ast.walk(aggregate_publisher)
    )
    aggregate_guards = [
        node for node in aggregate_publisher.body if isinstance(node, ast.If)
    ]
    assert len(aggregate_guards) == 1
    assert isinstance(aggregate_guards[0].body[0], ast.Return)
    aggregate_match = definitions["catalog_auth_refresh_matches_expected"][0][1]
    assert [
        node.func.attr
        for node in ast.walk(aggregate_match)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    ] == ["catalog_reload_matches_expected", "reload_data_matches_expected"]
    allowed = {
        "reload_data_matches_expected": {"type"},
        "catalog_reload_matches_expected": {"type"},
        "catalog_auth_refresh_matches_expected": {
            "type",
            "catalog_reload_matches_expected",
            "reload_data_matches_expected",
        },
    }
    for name in allowed:
        method = definitions[name][0][1]
        assert not any(
            isinstance(
                node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
            )
            for node in ast.walk(method)
        )
        assert {
            node.func.id if isinstance(node.func, ast.Name) else node.func.attr
            for node in ast.walk(method)
            if isinstance(node, ast.Call)
            and isinstance(node.func, (ast.Name, ast.Attribute))
        } <= allowed[name]


def _external_owner_writes(node, path, owner, aliases, fields) -> set[str]:
    if not isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
        return set()
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    writes: set[str] = set()
    for target in targets:
        while isinstance(target, ast.Subscript):
            target = target.value
        if not isinstance(target, ast.Attribute):
            continue
        receiver = ast.unparse(target.value)
        target_owner = (
            owner if receiver == "self" else aliases.get(path, {}).get(receiver, "")
        )
        if target.attr in fields.get(target_owner, set()) and owner != target_owner:
            writes.add(ast.unparse(target))
    return writes


def test_catalog_auth_known_alias_call_and_external_write_inventory() -> None:
    root = Path(__file__).parents[1] / "src/pipy_harness"
    sources = [
        (path.relative_to(root).as_posix(), ast.parse(path.read_text(encoding="utf-8")))
        for path in sorted(root.rglob("*.py"))
    ]
    methods = {
        node.name: {
            item.name for item in node.body if isinstance(item, ast.FunctionDef)
        }
        for _, tree in sources
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name in {"AuthStore", "ModelCatalog"}
    }
    owned_fields = {
        "AuthStore": set("path _data _reload_identity".split()),
        "ModelCatalog": set(
            "builtin models_json_path extra_providers rows error provider_request_configs "
            "_config _registered _oauth_modifiers _reload_identity".split()
        ),
    }
    calls: set[tuple[str, str, str]] = set()
    writes: set[tuple[str, str, str]] = set()
    aliases = {
        "native/auth_store.py": {"store": "AuthStore"},
        "native/catalog_state.py": {
            "self.auth_store": "AuthStore",
            "auth_owner": "AuthStore",
            "prepared.expected_auth_owner": "AuthStore",
            "self.catalog": "ModelCatalog",
            "catalog_owner": "ModelCatalog",
            "prepared.expected_catalog_owner": "ModelCatalog",
        },
        "native/repl_state.py": {"store": "AuthStore"},
    }
    for path, tree in sources:
        classes = {
            child: owner.name
            for owner in ast.walk(tree)
            if isinstance(owner, ast.ClassDef)
            for child in ast.walk(owner)
        }
        functions = {
            child: function
            for function in ast.walk(tree)
            if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))
            for child in ast.walk(function)
        }
        for node in ast.walk(tree):
            function = functions.get(node)
            owner = classes.get(node, "")
            scope = function.name if function else "<module>"
            writes.update(
                (path, scope, target)
                for target in _external_owner_writes(
                    node, path, owner, aliases, owned_fields
                )
            )
            if not isinstance(node, ast.Call):
                continue
            name = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else (node.func.id if isinstance(node.func, ast.Name) else "")
            )
            if isinstance(node.func, ast.Attribute) and function is not None:
                receiver = ast.unparse(node.func.value)
                target_owner = (
                    owner
                    if receiver == "self"
                    else aliases.get(path, {}).get(receiver, "")
                )
                if name in methods.get(target_owner, set()):
                    calls.add((path, function.name, f"{target_owner}.{name}"))
    assert {path for path, _, _ in calls} == set(aliases) | {"native/models_json.py"}
    assert writes == set()


def test_usage_accumulator_reload_api_callers_are_exactly_coding_owner_adapters() -> (
    None
):
    expected = {
        "prepare_reload_value_refresh": "prepare_reload_usage_refresh",
        "prepare_reload_value_fallback": "prepare_reload_usage_fallback",
        "reload_value_matches_expected": "reload_usage_matches_expected",
        "publish_reload_value_refresh": "publish_reload_usage_refresh",
    }
    definitions: dict[str, list[str]] = {name: [] for name in expected}
    calls: dict[str, list[tuple[str, tuple[str, ...]]]] = {
        name: [] for name in expected
    }

    def inventory(node: ast.AST, path: str, owners: tuple[str, ...] = ()) -> None:
        if isinstance(node, ast.ClassDef):
            owners += (f"class:{node.name}",)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            owners += (f"function:{node.name}",)
            if node.name in definitions:
                definitions[node.name].append(path)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in calls
        ):
            calls[node.func.attr].append((path, owners))
        for child in ast.iter_child_nodes(node):
            inventory(child, path, owners)

    source_root = Path(__file__).parents[1] / "src/pipy_harness"
    for path in sorted(source_root.rglob("*.py")):
        relative = path.relative_to(source_root).as_posix()
        inventory(ast.parse(path.read_text(encoding="utf-8")), relative)

    assert definitions == {name: ["native/agent/usage.py"] for name in expected}
    assert calls == {
        name: [
            (
                "native/coding/state.py",
                ("class:CodingSessionState", f"function:{adapter}"),
            )
        ]
        for name, adapter in expected.items()
    }


def test_uninstalled_gate_flushes_all_users_then_customs_then_fifo_unlocked() -> None:
    mutex = threading.RLock()
    gate = OrderedDeliveryGate(mutex)
    events: list[str] = []
    foreign_errors: list[str] = []

    def record_unlocked(label: str) -> None:
        acquired = threading.Event()

        def probe() -> None:
            with mutex:
                acquired.set()
            if label == "user:user-1":
                try:
                    gate.drain(OrderedDeliveryToken())
                except RuntimeError as error:
                    foreign_errors.append(str(error))

        worker = threading.Thread(target=probe)
        worker.start()
        worker.join(timeout=1.0)
        assert acquired.is_set(), f"{label} ran under the session mutex"
        events.append(label)

    staged = FrozenStagedDeliveryBatch.freeze(
        (QueuedUserMessage("user-1", {}), QueuedUserMessage("user-2", {})),
        (
            QueuedCustomMessage("status", "custom-1", True, None, {}),
            QueuedCustomMessage("status", "custom-2", True, None, {}),
        ),
    )
    with gate.reserve() as token:
        gate.submit(lambda: record_unlocked("candidate-send"))
        gate.submit(lambda: record_unlocked("live-send"))
        assert gate.drain(token) is False
        deliver_accepted_staged_batch(
            staged,
            gate=gate,
            token=token,
            user_sink=lambda message: record_unlocked(f"user:{message.content}"),
            custom_sink=lambda message: record_unlocked(f"custom:{message.content}"),
        )
    assert ",".join(events) == (
        "user:user-1,user:user-2,custom:custom-1,custom:custom-2,"
        "candidate-send,live-send"
    )
    assert foreign_errors == ["ordered delivery token is not active"]
    gate.submit(lambda: record_unlocked("idle-direct"))
    assert events[-1] == "idle-direct"


def test_direct_interrupt_preserves_pending_reservation_and_queued_send() -> None:
    gate = OrderedDeliveryGate(threading.RLock())
    direct_entered = threading.Event()
    direct_release = threading.Event()
    events: list[str] = []
    interrupts: list[type[BaseException]] = []

    def direct() -> None:
        direct_entered.set()
        assert direct_release.wait(2)
        events.append("direct")
        raise KeyboardInterrupt

    def submit_direct() -> None:
        try:
            gate.submit(direct)
        except BaseException as error:
            interrupts.append(type(error))

    submitter = threading.Thread(target=submit_direct)
    submitter.start()
    assert direct_entered.wait(2)

    def reserve_and_drain() -> None:
        with gate.reserve() as token:
            events.append("reserved")
            gate.release(token)
            assert gate.drain(token)

    reserver = threading.Thread(target=reserve_and_drain)
    reserver.start()
    for _attempt in range(1000):
        with gate._mutex:  # noqa: SLF001 - deterministic admission barrier
            pending = gate._reservation_pending  # noqa: SLF001
        if pending:
            break
        threading.Event().wait(0.001)
    else:
        pytest.fail("reservation did not become pending")
    gate.submit(lambda: events.append("queued"))
    assert events == []
    direct_release.set()
    submitter.join(2)
    reserver.join(2)
    assert not submitter.is_alive() and not reserver.is_alive()
    assert events == ["direct", "reserved", "queued"]
    assert interrupts == [KeyboardInterrupt]


def test_reservation_exception_and_explicit_abort_discard_queued_sends() -> None:
    gate = OrderedDeliveryGate(threading.RLock())
    events: list[str] = []
    with pytest.raises(RuntimeError, match="before sequencer"):
        with gate.reserve():
            gate.submit(lambda: events.append("stranded"))
            raise RuntimeError("before sequencer")
    with gate.reserve() as token:
        gate.submit(lambda: events.append("aborted"))
        assert gate.abort(token)
    gate.submit(lambda: events.append("later"))
    assert events == ["later"]


def _leaf_exception_messages(error: BaseException) -> list[str]:
    if not isinstance(error, BaseExceptionGroup):
        return [str(error)]
    return sum((_leaf_exception_messages(item) for item in error.exceptions), [])


def test_staged_and_queued_ordinary_failures_are_all_preserved() -> None:
    gate = OrderedDeliveryGate(threading.RLock())
    events: list[str] = []
    batch = FrozenStagedDeliveryBatch.freeze(
        (QueuedUserMessage("u1", {}), QueuedUserMessage("u2", {})),
        (
            QueuedCustomMessage("x", "c1", True, None, {}),
            QueuedCustomMessage("x", "c2", True, None, {}),
        ),
    )

    def fail(label: str) -> None:
        events.append(label)
        raise RuntimeError(label)

    with gate.reserve() as token:
        gate.submit(lambda: fail("q1"))
        gate.submit(lambda: fail("q2"))
        with pytest.raises(ExceptionGroup) as raised:
            deliver_accepted_staged_batch(
                batch,
                gate=gate,
                token=token,
                user_sink=lambda message: fail(message.content),
                custom_sink=lambda message: fail(message.content),
                release_route=lambda: fail("route-release"),
            )
    expected = ["u1", "u2", "c1", "c2", "route-release", "q1", "q2"]
    assert events == expected
    assert _leaf_exception_messages(raised.value) == expected
    assert events.count("route-release") == 1
    gate.submit(lambda: events.append("reset"))
    assert events[-1] == "reset"


def test_reserve_interrupt_preserves_successor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = OrderedDeliveryGate(threading.RLock())
    successor: list[tuple[Any, OrderedDeliveryToken]] = []

    def interrupt_wait() -> None:
        gate._active_direct = 0  # noqa: SLF001 - synthetic direct send
        gate.abort(cast(OrderedDeliveryToken, gate._token))  # noqa: SLF001
        reservation = gate.reserve()
        successor.append((reservation, reservation.__enter__()))
        raise KeyboardInterrupt

    gate._active_direct = 1  # noqa: SLF001 - force Condition.wait
    monkeypatch.setattr(gate._condition, "wait", interrupt_wait)  # noqa: SLF001
    with pytest.raises(KeyboardInterrupt), gate.reserve():
        pytest.fail("interrupted reservation must not enter")
    assert gate._token is successor[0][1]  # noqa: SLF001


@pytest.mark.parametrize("interrupt", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("phase", ["staged", "queued"])
def test_interrupt_stops_delivery_and_release_failure_preserves_original(
    interrupt: type[BaseException], phase: str, tmp_path: Path
) -> None:
    mutex = threading.RLock()
    runtime = _rich_runtime(tmp_path, f"interrupt-{phase}", mutex=mutex)
    runtime.outbox.clear()
    queues = _projection(runtime).queues
    gate = OrderedDeliveryGate(mutex)
    if phase == "staged":
        gate = _FailingAppendGate(mutex, 1)
        queues.install_candidate_route(gate)
    events: list[str] = []
    successor: list[tuple[Any, OrderedDeliveryToken]] = []
    batch = FrozenStagedDeliveryBatch.freeze(
        (QueuedUserMessage("u1", {}), QueuedUserMessage("u2", {})),
        (QueuedCustomMessage("x", "c1", True, None, {}),),
    )

    def staged(message: QueuedUserMessage) -> None:
        events.append(message.content)
        if phase == "staged":
            raise interrupt()

    def queued(label: str) -> None:
        events.append(label)
        if label == "q1" and phase == "queued":
            gate.abort(token)
            reservation = gate.reserve()
            successor.append((reservation, reservation.__enter__()))
            raise interrupt()

    with pytest.raises(interrupt):
        with gate.reserve() as token:
            gate.submit(lambda: queued("q1"))
            gate.submit(lambda: queued("q2"))
            deliver_accepted_staged_batch(
                batch,
                gate=gate,
                token=token,
                user_sink=staged,
                custom_sink=lambda message: events.append(message.content),
                release_route=queues.release_pending_route
                if phase == "staged"
                else None,
            )

    assert events == (["u1"] if phase == "staged" else ["u1", "u2", "c1", "q1"])
    if phase == "queued":
        assert gate.abort(successor[0][1])
        successor[0][0].__exit__(None, None, None)
    gate.submit(lambda: events.append("later"))
    assert events[-1] == "later"


def test_stale_sequencer_preserves_released_successor_and_its_queue() -> None:
    gate = OrderedDeliveryGate(threading.RLock())
    events: list[str] = []
    successor: list[tuple[Any, OrderedDeliveryToken]] = []

    with gate.reserve() as stale_token:

        def transfer_normally() -> None:
            assert gate.abort(stale_token)
            reservation = gate.reserve()
            successor.append((reservation, reservation.__enter__()))
            gate.submit(lambda: events.append("successor-queued"))

        gate.submit(transfer_normally)
        gate.release(stale_token)
        assert gate.drain(stale_token)

    gate.release(successor[0][1])
    batch = FrozenStagedDeliveryBatch.freeze(
        (QueuedUserMessage("staged-user", {}),),
        (QueuedCustomMessage("x", "staged-custom", True, None, {}),),
    )
    with pytest.raises(RuntimeError, match="token is not active"):
        deliver_accepted_staged_batch(
            batch,
            gate=gate,
            token=stale_token,
            user_sink=lambda message: events.append(message.content),
            custom_sink=lambda message: events.append(message.content),
        )
    assert events == []
    assert gate.drain(successor[0][1])
    assert events == ["successor-queued"]


@pytest.mark.parametrize(
    ("options", "in_turn", "display", "route"),
    (
        ({"deliverAs": "nextTurn"}, False, True, "next"),
        ({"deliver_as": "steer"}, False, True, "steer"),
        ({"deliverAs": "followUp"}, False, True, "follow"),
        ({"deliverAs": "follow_up"}, False, True, "follow"),
        ({"triggerTurn": True}, False, False, "prompt"),
        ({"trigger_turn": True}, False, True, "prompt"),
        ({"triggerTurn": True}, True, True, None),
        ({"deliverAs": "unknown", "triggerTurn": True}, False, True, "prompt"),
        ({"deliverAs": "nextTurn", "triggerTurn": True}, False, True, "next"),
        ({"deliverAs": "unknown", "deliver_as": "nextTurn"}, False, True, None),
    ),
)
def test_accepted_custom_sink_matches_established_routing_and_sink_order(
    options: Mapping[str, object], in_turn: bool, display: bool, route: str | None
) -> None:
    calls: list[str] = []

    def append_durable(message: QueuedCustomMessage) -> object:
        calls.append(f"tree:{message.content}")
        return "entry"

    callbacks: dict[str, Any] = {
        "append_durable": append_durable,
        "render_or_diagnose": lambda message, entry: calls.append(
            f"render:{message.content}:{entry}"
        ),
        "enqueue_next_turn": lambda content: calls.append(f"next:{content.value}"),
        "enqueue_steering": lambda content: calls.append(f"steer:{content.value}"),
        "enqueue_follow_up": lambda content: calls.append(f"follow:{content.value}"),
        "enqueue_prompt": lambda content: calls.append(f"prompt:{content.value}"),
        "in_agent_turn": lambda: in_turn,
    }
    sinks = AcceptedCustomMessageSinks(**callbacks)
    assert [field.name for field in fields(AcceptedCustomMessageSinks)] == list(
        callbacks
    )
    assert all(getattr(sinks, name) is callback for name, callback in callbacks.items())

    sinks.deliver(QueuedCustomMessage("note", "payload", display, None, options))

    expected = ["tree:payload"]
    if display:
        expected.append("render:payload:entry")
    if route is not None:
        expected.append(f"{route}:payload")
    assert calls == expected


def _base_name(node: ast.expr) -> str:
    if isinstance(node, ast.Subscript):
        return _base_name(node.value)
    return node.id if isinstance(node, ast.Name) else getattr(node, "attr", "")


def test_r3c2_forbids_registries_identity_discovery_and_list_magic() -> None:
    root = Path(__file__).parents[1] / "src/pipy_harness/native"
    texts = {
        name: (root / name).read_text()
        for name in (
            "extension_runtime.py",
            "session_generation.py",
            "extension_hooks.py",
            "tui.py",
        )
    }
    combined = "".join(texts.values())
    route = (
        texts["extension_runtime.py"]
        .split("class GenerationMessageRouting:", 1)[1]
        .split("_RegistrationValue", 1)[0]
    )
    assert not any(
        token in combined for token in ("WeakValueDictionary", "current_for")
    )
    assert not any(
        token in route
        for token in "id( _registry Condition .wait( __dict__ setattr(".split()
    )
    assert not any(
        _base_name(base) == "list"
        for text in texts.values()
        for node in ast.walk(ast.parse(text))
        if isinstance(node, ast.ClassDef)
        for base in node.bases
    )


def test_r3c2_production_authority_and_renderer_wiring_inventory_is_exact() -> None:
    root = Path(__file__).parents[1] / "src/pipy_harness/native"
    trees = {
        path.relative_to(root).as_posix(): ast.parse(path.read_text())
        for path in root.rglob("*.py")
    }
    watched = set(
        "accept submit route_drain _bind_session_mutex _install_candidate_route install_candidate_route release_pending release_pending_route retire retire_route _accept_message_route _commit_activation".split()
    )
    constructors = set(
        "GenerationMessageRouting SessionGenerationRef _CustomEntryRenderer".split()
    )
    entries = [(path, node) for path, tree in trees.items() for node in ast.walk(tree)]
    calls = [
        node.func.attr
        for _, node in entries
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in watched
    ]
    references = [
        node.attr
        for _, node in entries
        if isinstance(node, ast.Attribute)
        and node.attr in watched
        and isinstance(node.ctx, ast.Load)
    ]
    assert Counter(references) - Counter(calls) == Counter({"release_pending_route": 2})
    actual_calls = " ".join(f"{name}={calls.count(name)}" for name in sorted(watched))
    assert actual_calls == (
        "_accept_message_route=2 _bind_session_mutex=3 _commit_activation=2 "
        "_install_candidate_route=1 accept=2 install_candidate_route=2 release_pending=1 "
        "release_pending_route=0 retire=2 retire_route=3 route_drain=1 submit=2"
    )
    built = {
        name: [
            (path, node)
            for path, node in entries
            if isinstance(node, ast.Call) and _base_name(node.func) == name
        ]
        for name in constructors
    }
    assert not any(
        isinstance(node, ast.alias)
        and node.name in constructors
        and node.asname is not None
        or isinstance(node, (ast.Assign, ast.AnnAssign))
        and node.value is not None
        and _base_name(node.value) in constructors
        or isinstance(node, ast.Call)
        and any(
            isinstance(value, ast.Name) and value.id in constructors
            for argument in (*node.args, *(kw.value for kw in node.keywords))
            for value in ast.walk(argument)
        )
        for _, node in entries
    )
    assert {name: len(found) for name, found in built.items()} == dict(
        GenerationMessageRouting=2, SessionGenerationRef=1, _CustomEntryRenderer=1
    )
    ref_path, ref_call = built["SessionGenerationRef"][0]
    assert ref_path == "tool_loop_session.py"
    assert {kw.arg: ast.unparse(kw.value) for kw in ref_call.keywords}[
        "lock"
    ] == "session_state_lock"
    path, renderer_call = built["_CustomEntryRenderer"][0]
    assert (path, renderer_call.args) == ("tool_loop_session.py", [])
    assert {kw.arg for kw in renderer_call.keywords} == set(
        "session ctl terminal_ui coding_input_queue error_stream generation_snapshot".split()
    )
    assert not any(
        isinstance(node, ast.Attribute)
        and node.attr == "generation_snapshot"
        and isinstance(node.ctx, ast.Store)
        or isinstance(node, ast.Call)
        and _base_name(node.func) in {"setattr", "__setattr__", "replace"}
        and "generation_snapshot" in ast.unparse(node)
        for _, node in entries
    )

    def owner(file: str, name: str) -> ast.ClassDef:
        return next(
            node
            for node in trees[file].body
            if isinstance(node, ast.ClassDef) and node.name == name
        )

    release = next(
        node
        for node in owner("extension_runtime.py", "GenerationMessageRouting").body
        if isinstance(node, ast.FunctionDef) and node.name == "release_pending"
    )
    assert not any(
        isinstance(node, (ast.While, ast.Await, ast.Yield))
        for node in ast.walk(release)
    )
    assert ast.unparse(release).count("append_reserved(") == 2
    leaf = next(
        node
        for node in owner("session_generation.py", "OrderedDeliveryGate").body
        if isinstance(node, ast.FunctionDef) and node.name == "append_reserved"
    )
    assert not any(
        isinstance(node, (ast.For, ast.While, ast.Await, ast.Yield))
        for node in ast.walk(leaf)
    )
    assert ast.unparse(leaf).count("self._queued.extend(deliveries)") == 1
    direct = next(
        node
        for node in owner("tui.py", "_CustomEntryRenderer").body
        if isinstance(node, ast.FunctionDef) and node.name == "extension_send_message"
    )
    assert "_snapshot" not in ast.unparse(direct)
    api = owner("extension_runtime.py", "_ActivationApi")
    assert [
        method.name
        for method in api.body
        if isinstance(method, ast.FunctionDef)
        if any(
            isinstance(node, ast.Attribute)
            and node.attr == "_message_route_authority"
            and isinstance(node.ctx, ast.Store)
            for node in ast.walk(method)
        )
    ] == ["__init__", "_accept_message_route", "_clear_terminal_storage_locked"]
    assert "generation_ref.publish(" in ast.unparse(trees["tool_loop_session.py"])


def test_r3b_call_inventory_is_complete_and_installed_across_package() -> None:
    names = {
        "ReloadEffectPreparationPorts",
        "PreparedReloadEffects",
        "DetachedReloadEffect",
        "FrozenStagedDeliveryBatch",
        "OrderedDeliveryGate",
        "OrderedDeliveryToken",
        "AcceptedCustomMessageSinks",
        "ExtensionChromePrepareInput",
        "ExtensionChromeCommitToken",
        "ExtensionChromePreparePort",
        "ActivationInputsValue",
        "ProviderFactoryValue",
        "CodingCompactionValue",
        "TemporaryLegacyValue",
        "PresentationPersistenceValue",
        "build_prepared_reload_effects",
        "_build_detached_reload_effects",
        "deliver_accepted_staged_batch",
        "_route_legacy_custom_message_input",
    }
    methods = set("freeze deliver dispose reserve validate release drain abort".split())
    calls: dict[str, list[tuple[str, tuple[str, ...]]]] = {
        name: [] for name in names | methods
    }

    def inventory(node: ast.AST, path: str, owners: tuple[str, ...] = ()) -> None:
        if isinstance(node, ast.ClassDef):
            owners += (f"class:{node.name}",)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            owners += (f"function:{node.name}",)
        if isinstance(node, ast.Call):
            name = (
                node.func.id
                if isinstance(node.func, ast.Name)
                else node.func.attr
                if isinstance(node.func, ast.Attribute)
                else ""
            )
            if name in names:
                calls[name].append((path, owners))
            if isinstance(node.func, ast.Attribute) and name in methods:
                calls[name].append((path, owners))
        for child in ast.iter_child_nodes(node):
            inventory(child, path, owners)

    source_root = Path(__file__).parents[1] / "src/pipy_harness"
    for path in source_root.rglob("*.py"):
        relative = path.relative_to(source_root).as_posix()
        inventory(ast.parse(path.read_text(encoding="utf-8")), relative)

    nonempty = {name: owners for name, owners in calls.items() if owners}
    actual = {
        (name, path, owners)
        for name, found in nonempty.items()
        for path, owners in found
    }
    assert sum(map(len, nonempty.values())) == len(actual)
    sg, hooks = "native/session_generation.py", "native/extension_hooks.py"
    loop = "native/tool_loop_session.py"
    reload_owner = "class:_ReloadCommandEffects"
    reload_generation = (reload_owner, "function:_reload_extension_generation")
    startup = ("class:NativeToolReplSession", "function:run")
    startup_guard = ("function:balance_startup_candidate", "function:guarded")
    chrome_prepare = ("class:_LiveExtensionUiDriver", "function:prepare_candidate")
    prepare = ("function:prepare_production_reload",)
    sequencer = ("function:deliver_accepted_staged_batch",)
    reserve = ("class:OrderedDeliveryGate", "function:reserve")
    assert actual == {
        ("PreparedReloadEffects", sg, ("function:build_prepared_reload_effects",)),
        ("OrderedDeliveryToken", sg, reserve),
        (
            "build_prepared_reload_effects",
            "native/tool_loop_session.py",
            ("function:_build_detached_reload_effects",),
        ),
        (
            "_route_legacy_custom_message_input",
            "native/tui.py",
            ("class:AcceptedCustomMessageSinks", "function:deliver"),
        ),
        (
            "_route_legacy_custom_message_input",
            "native/tui.py",
            ("class:_CustomEntryRenderer", "function:_deliver_custom_message"),
        ),
        (
            "dispose",
            "native/extension_runtime.py",
            ("function:_dispose_activation_results",),
        ),
        ("dispose", loop, ("class:_ReloadCommandEffects", "function:execute")),
        ("dispose", loop, reload_generation),
        ("dispose", sg, startup_guard),
        ("dispose", sg, ("class:PreparedReloadEffects", "function:dispose")),
        ("dispose", sg, ("function:_dispose_completed_reload_effects",)),
        ("validate", hooks, sequencer),
        ("release", hooks, sequencer),
        ("drain", hooks, sequencer),
        ("abort", sg, reserve),
        *{
            (name, sg, prepare)
            for name in "ActivationInputsValue ProviderFactoryValue CodingCompactionValue "
            "TemporaryLegacyValue PresentationPersistenceValue ReloadEffectPreparationPorts "
            "build_prepared_reload_effects freeze".split()
        },
        ("ExtensionChromePrepareInput", loop, reload_generation),
        ("ExtensionChromeCommitToken", loop, reload_generation),
        ("ExtensionChromeCommitToken", "native/tui.py", chrome_prepare),
        *{
            (name, loop, owner)
            for name in "OrderedDeliveryGate deliver_accepted_staged_batch reserve".split()
            for owner in (reload_generation, startup)
        },
        ("freeze", loop, startup),
    }
