from __future__ import annotations

import ast
import gc
import io
import threading
import weakref
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import Any, cast

import pytest
from session_generation_test_support import build_test_projection

import pipy_harness.native.tool_loop_session as tool_loop_session
from pipy_harness.native import FakeNativeProvider, NativeToolReplSession
from pipy_harness.native.agent import AgentToolCall, AgentUserMessage, ProductContent
from pipy_harness.native.agent.active_input import AgentActiveInput
from pipy_harness.native.agent.loop_policy import AgentProviderRequestPolicyInput
from pipy_harness.native.agent.usage import AgentUsageAccumulator
from pipy_harness.native.coding.state import CodingSessionState
from pipy_harness.native.editor_state import EditorState
from pipy_harness.native.extension_chrome_state import ExtensionChromeSink
from pipy_harness.native.extension_hooks import (
    _compose_extension_runtime,
    dispatch_user_bash_hooks,
)
from pipy_harness.native.extension_runtime import (
    ExtensionCodingSessionControl,
    ExtensionModelRuntimeControl,
    ExtensionTool,
)
from pipy_harness.native.extensions.contracts import (
    RegisteredCommand,
    RegisteredShortcut,
    _ExtensionRuntime,
)
from pipy_harness.native.extensions.message_routing import GenerationMessageRouting
from pipy_harness.native.models import ProviderRequest
from pipy_harness.native.repl.collaborators import SessionCollaborators
from pipy_harness.native.repl.execution_projections import (
    SessionExecutionProjections,
)
from pipy_harness.native.repl.extension_operations import (
    ProviderHeaderRequestSnapshot,
    SessionExtensionOperations,
)
from pipy_harness.native.repl.reload import (
    ImplicitTrustState,
    ReloadCommandEffects,
)
from pipy_harness.native.resource_loading import RuntimeResourceOptions
from pipy_harness.native.resources import WorkspaceResources
from pipy_harness.native.session_generation import (
    ExtensionChromeHandle,
    SessionExtensionGeneration,
    SessionGenerationRef,
    SessionGenerationSnapshot,
)
from pipy_harness.native.session_tree import NativeSessionTree
from pipy_harness.native.settings import SettingsManager
from pipy_harness.native.tool_capabilities import (
    NativeToolCapabilities,
    ToolCapabilityState,
    ToolFilterOptions,
)
from pipy_harness.native.tools import (
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
    ToolRequest,
)
from pipy_harness.native.ui.autocomplete import AutocompleteComponent
from pipy_harness.native.ui.components.custom_editor import (
    CustomEditorEffects,
    CustomEditorOwner,
    CustomEditorState,
)
from pipy_harness.native.ui.paint_lock import PaintLock


def _runtime(lock: threading.RLock) -> _ExtensionRuntime:
    user_outbox: list[Any] = []
    custom_outbox: list[Any] = []
    routing = GenerationMessageRouting(user_outbox, custom_outbox, mutex=lock)
    return _compose_extension_runtime((), user_outbox, custom_outbox, routing)


def _generation(
    lock: threading.RLock,
    label: str,
    observations: list[tuple[str, str]],
    *,
    tool_result_hook: bool = True,
    retained: list[Any] | None = None,
) -> SessionExtensionGeneration:
    runtime = _runtime(lock)
    projection = build_test_projection(runtime, {"generation": label}, queue_mutex=lock)

    def observe(ctx: Any) -> None:
        observations.append((label, str(ctx.flags.get("generation", "absent"))))
        if retained is not None:
            retained.append(ctx)

    def command(ctx: Any, _args: str) -> None:
        observe(ctx)

    def hook(_event: object, ctx: Any) -> None:
        observe(ctx)

    hook_values: dict[str, Any] = {
        name: (hook,)
        for name in (
            "input before_agent_start before_provider_headers before_provider_request "
            "session_before_switch session_before_fork session_before_compact "
            "session_before_tree user_bash"
        ).split()
    }
    hook_values["tool_result"] = (hook,) if tool_result_hook else ()
    projection = replace(
        projection,
        commands=replace(
            projection.commands,
            commands=MappingProxyType(
                {"probe": RegisteredCommand("probe", "probe", command, label)}
            ),
            menu_names=(f"/{label}",),
            descriptions=MappingProxyType({f"/{label}": f"{label} description"}),
            shortcuts=MappingProxyType(
                {"ctrl-k": RegisteredShortcut("ctrl-k", command, label)}
            ),
        ),
        hooks=replace(projection.hooks, **hook_values),
    )
    return SessionExtensionGeneration(runtime, projection)


class _PublishingSnapshotRef(SessionGenerationRef):
    __slots__ = ("replacement", "snapshot_calls")

    def __init__(
        self,
        generation: SessionExtensionGeneration,
        replacement: SessionExtensionGeneration,
        lock: threading.RLock,
    ) -> None:
        super().__init__(generation, lock=lock)
        self.replacement = replacement
        self.snapshot_calls = 0

    def snapshot(self) -> SessionGenerationSnapshot:
        snapshot = super().snapshot()
        self.snapshot_calls += 1
        if self.snapshot_calls == 1:
            self.publish(self.replacement)
        return snapshot


def _request_input(tmp_path: Path) -> AgentProviderRequestPolicyInput:
    message = AgentUserMessage(ProductContent("prompt"))
    return AgentProviderRequestPolicyInput(
        baseline=ProviderRequest(
            system_prompt="system",
            user_prompt="prompt",
            provider_name="fake",
            model_id="model",
            cwd=tmp_path,
            messages=(message,),
            available_tools=(),
        ),
        active_input=AgentActiveInput(message),
    )


@pytest.mark.parametrize(
    "family",
    "command shortcut input before-agent before-provider-request "
    "before-provider-headers tool-result tool-result-identity session-gate".split(),
)
def test_r4a_dispatch_uses_one_old_or_new_published_snapshot(
    tmp_path: Path,
    family: str,
) -> None:
    observations: list[tuple[str, str]] = []
    lock = threading.RLock()
    identity = family == "tool-result-identity"
    old = _generation(lock, "old", observations, tool_result_hook=not identity)
    new = _generation(lock, "new", observations)
    generation_ref = _PublishingSnapshotRef(old, new, lock)
    model_runtime = ExtensionModelRuntimeControl()
    operations = SessionExtensionOperations(
        generation_ref=generation_ref,
        cwd=str(tmp_path),
        has_ui=False,
        notify_sink=None,
        ui_driver=None,
        project_trusted=True,
        model_runtime_factory=lambda _generation_id, _allow_model: model_runtime,
    )
    if family == "command":
        operations.dispatch_command(
            "/probe",
            coding_session=ExtensionCodingSessionControl(),
            ui_custom_driver=None,
        )
    elif family == "shortcut":
        operations.dispatch_shortcut(
            "ctrl-k",
            coding_session=ExtensionCodingSessionControl(),
            ui_custom_driver=None,
        )
    elif family == "input":
        assert operations.dispatch_input("prompt") == "prompt"
    elif family == "before-agent":
        operations.dispatch_before_agent_start("system")
    elif family == "before-provider-request":
        operations.prepare_provider_request(_request_input(tmp_path))
    elif family == "before-provider-headers":
        callback = operations.provider_header_callback(
            NativeSessionTree.create(tmp_path, persist=False)
        )
        assert callback is not None
        callback({})
    elif family in {"tool-result", "tool-result-identity"}:
        content = ProductContent("result")
        transformed = operations.transform_tool_result(
            tool_name="tool",
            content=content,
            is_error=False,
        )
        assert transformed.value == "result"
        if identity:
            assert transformed is content
    else:
        assert operations.session_allows(
            "switch",
            operation="switch",
            target="new",
        ).allow
    assert generation_ref.snapshot_calls == 1
    assert generation_ref.current is new
    expected = [] if identity else [("old", "absent" if family == "input" else "old")]
    assert observations == expected


@pytest.mark.parametrize(
    "family",
    "command shortcut input before-agent before-provider-request tool-result "
    "session-gate user-bash".split(),
)
def test_retained_operation_context_selection_controls_capture_generation(
    tmp_path: Path, family: str
) -> None:
    observations: list[tuple[str, str]] = []
    retained: list[Any] = []
    selected_ids: list[int] = []
    lock = threading.RLock()
    old = _generation(lock, "old", observations, retained=retained)
    new = _generation(lock, "new", observations)
    generation_ref = _PublishingSnapshotRef(old, new, lock)

    def controls(generation_id: int, allow_model: bool) -> ExtensionModelRuntimeControl:
        def selected(_value: object) -> bool:
            selected_ids.append(generation_id)
            return True

        return ExtensionModelRuntimeControl(
            set_active_tools_fn=selected,
            set_model_fn=selected if allow_model else lambda _value: False,
            set_thinking_level_fn=selected,
        )

    operations = SessionExtensionOperations(
        generation_ref=generation_ref,
        cwd=str(tmp_path),
        has_ui=False,
        notify_sink=None,
        ui_driver=None,
        project_trusted=True,
        model_runtime_factory=controls,
    )
    if family == "command":
        operations.dispatch_command(
            "/probe",
            coding_session=ExtensionCodingSessionControl(),
            ui_custom_driver=None,
        )
    elif family == "shortcut":
        operations.dispatch_shortcut(
            "ctrl-k",
            coding_session=ExtensionCodingSessionControl(),
            ui_custom_driver=None,
        )
    elif family == "input":
        operations.dispatch_input("prompt")
    elif family == "before-agent":
        operations.dispatch_before_agent_start("system")
    elif family == "before-provider-request":
        operations.prepare_provider_request(_request_input(tmp_path))
    elif family == "tool-result":
        operations.transform_tool_result(
            tool_name="tool",
            content=ProductContent("result"),
            is_error=False,
        )
    elif family == "session-gate":
        operations.session_allows("switch", operation="switch", target="new")
    else:
        hooks, flags, ui_driver, model_runtime = operations.user_bash_inputs()
        dispatch_user_bash_hooks(
            hooks,
            command="pwd",
            exclude_from_context=False,
            cwd=str(tmp_path),
            has_ui=False,
            ui_driver=ui_driver,
            model_runtime=model_runtime,
            flags=flags,
        )

    assert generation_ref.current is new
    assert len(retained) == 1
    assert retained[0].set_active_tools(()) is True
    assert retained[0].set_thinking_level("low") is True
    model_allowed = family not in {"before-provider-request", "tool-result"}
    assert retained[0].set_model("fake/fake-tools") is model_allowed
    assert selected_ids == ([0, 0, 0] if model_allowed else [0, 0])


def test_r4c_operation_routes_retained_chrome_to_its_snapshotted_generation(
    tmp_path: Path,
) -> None:
    observations: list[tuple[str, str]] = []
    lock = threading.RLock()
    old = _generation(lock, "old", observations)
    new = _generation(lock, "new", observations)
    old_sink, new_sink = ExtensionChromeSink(), ExtensionChromeSink()
    assert old.projection is not None and new.projection is not None
    old = replace(
        old,
        projection=replace(old.projection, chrome=ExtensionChromeHandle(old_sink)),
    )
    new = replace(
        new,
        projection=replace(new.projection, chrome=ExtensionChromeHandle(new_sink)),
    )
    routed: dict[ExtensionChromeSink, object] = {
        old_sink: "old-chrome",
        new_sink: "new-chrome",
    }

    class UiRouter:
        def generation_driver(self, sink: ExtensionChromeSink) -> object:
            return routed[sink]

    seen: list[object] = []

    def command(ctx: Any, _args: str) -> None:
        seen.append(getattr(ctx.ui, "_ui_driver"))  # noqa: SLF001

    old_projection = old.projection
    assert old_projection is not None
    old = replace(
        old,
        projection=replace(
            old_projection,
            commands=replace(
                old_projection.commands,
                commands=MappingProxyType(
                    {"probe": RegisteredCommand("probe", "probe", command, "old")}
                ),
            ),
        ),
    )
    generation_ref = _PublishingSnapshotRef(old, new, lock)
    operations = SessionExtensionOperations(
        generation_ref=generation_ref,
        cwd=str(tmp_path),
        has_ui=True,
        notify_sink=None,
        ui_driver=cast(Any, UiRouter()),
        project_trusted=True,
        model_runtime_factory=lambda _generation_id, _allow_model: (
            ExtensionModelRuntimeControl()
        ),
    )

    operations.dispatch_command(
        "/probe",
        coding_session=ExtensionCodingSessionControl(),
        ui_custom_driver=None,
    )

    assert generation_ref.snapshot_calls == 1
    assert generation_ref.current is new
    assert seen == ["old-chrome"]


def test_r4c_reload_menu_uses_one_published_command_projection(tmp_path: Path) -> None:
    observations: list[tuple[str, str]] = []
    lock = threading.RLock()
    old = _generation(lock, "old", observations)
    new = _generation(lock, "new", observations)
    generation_ref = _PublishingSnapshotRef(old, new, lock)

    class LegacyCtl:
        workspace_resources = WorkspaceResources((), (), (), False, False, False)

        def __init__(self) -> None:
            self.reads = 0
            self.generation_ref = generation_ref

        @property
        def extension_generation(self) -> SessionExtensionGeneration:
            self.reads += 1
            if self.reads == 2:
                generation_ref.publish(new)
            return generation_ref.current

    editor = EditorState()

    def noop() -> None:
        return None

    custom_editor = CustomEditorOwner(
        CustomEditorState(),
        editor,
        PaintLock(),
        noop,
        host=object(),
        theme=lambda: object(),
        keybindings_manager=lambda: None,
        effects=CustomEditorEffects(
            restore_input_text=lambda _text: None,
            clear_initial_text=noop,
            enqueue_follow_up=lambda _text: None,
            restore_pending=noop,
            paste_clipboard_image=noop,
            external_editor=lambda _text: None,
            autocomplete_provider=lambda: None,
        ),
    )
    autocomplete = AutocompleteComponent(
        editor,
        cwd=tmp_path,
        repaint=noop,
        custom_editor=custom_editor,
    )
    terminal_ui = SimpleNamespace(autocomplete=autocomplete)
    effect = SimpleNamespace(
        settings=SimpleNamespace(
            get_theme=lambda: None,
            get_autocomplete_max_visible=lambda: 5,
            load_errors=lambda: {},
            get_quiet_startup=lambda: True,
            project_trusted=True,
        ),
        terminal_ui=terminal_ui,
        ctl=LegacyCtl(),
        redraw_custom_entries_for_active_branch=lambda: None,
        verbose_startup=False,
        implicit_trust=ImplicitTrustState(),
        error_stream=io.StringIO(),
        cwd=tmp_path,
        resource_options=RuntimeResourceOptions(),
    )

    assert (
        ReloadCommandEffects._refresh_presentation_and_persistence(  # noqa: SLF001
            cast(Any, effect)
        )
        is False
    )

    assert "/old" in autocomplete.command_names
    assert "/new" not in autocomplete.command_names
    assert autocomplete.command_descriptions["/old"] == "old description"
    assert autocomplete.shortcut_keys == frozenset({"ctrl-k"})
    assert autocomplete.max_visible == 5
    assert generation_ref.snapshot_calls == 1


class _GenerationTool:
    def __init__(self, label: str) -> None:
        self.label = label
        self.calls = 0

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="probe",
            description=f"{self.label} generation",
            input_schema={"type": "object", "additionalProperties": False},
        )

    def invoke(self, request: ToolRequest, context: ToolContext) -> ToolExecutionResult:
        del context
        self.calls += 1
        return ToolExecutionResult(
            tool_request_id=request.tool_request_id,
            output_text=self.label,
            provider_correlation_id=request.provider_correlation_id,
        )


def _execution_generation(
    lock: threading.RLock,
    label: str,
    capability: ToolCapabilityState,
    renderer: object,
    *,
    tool_call_hooks: tuple[Any, ...] = (),
) -> SessionExtensionGeneration:
    runtime = _runtime(lock)
    projection = build_test_projection(runtime, {"generation": label}, queue_mutex=lock)
    projection = replace(
        projection,
        tools=replace(
            projection.tools,
            ports=capability.extension_registry,
            capability_state=capability,
        ),
        renderers=replace(
            projection.renderers,
            tools=MappingProxyType({"probe": cast(ExtensionTool, renderer)}),
        ),
        hooks=replace(projection.hooks, tool_call=tool_call_hooks),
    )
    return SessionExtensionGeneration(runtime, projection)


def test_r4b_provider_turn_retains_one_tool_renderer_and_provider_generation(
    tmp_path: Path,
) -> None:
    lock = threading.RLock()
    old_tool = _GenerationTool("old-tool")
    new_tool = _GenerationTool("new-tool")
    tool_capabilities = NativeToolCapabilities(
        {},
        {"probe": old_tool},
        workspace_root=tmp_path,
        reference_roots=(),
        stderr_sink=lambda _text: None,
        filter_options=ToolFilterOptions.empty(),
        cancel_join_timeout_seconds=0.1,
        state_lock=lock,
    )
    old_capability = tool_capabilities.state
    new_capability = ToolCapabilityState.build(
        {},
        {"probe": new_tool},
        filter_options=ToolFilterOptions.empty(),
        cancel_join_timeout_seconds=0.1,
    )
    old_renderer = object()
    new_renderer = object()
    old_generation = _execution_generation(lock, "old", old_capability, old_renderer)
    new_generation = _execution_generation(lock, "new", new_capability, new_renderer)
    generation_ref = SessionGenerationRef(old_generation, lock=lock)
    old_provider = FakeNativeProvider(model_id="old-provider", supports_tool_calls=True)
    new_provider = FakeNativeProvider(model_id="new-provider", supports_tool_calls=True)
    coding_state = CodingSessionState(
        provider=old_provider,
        provider_name=old_provider.name,
        model_id=old_provider.model_id,
        usage_accumulator=AgentUsageAccumulator(),
        state_lock=lock,
    )
    execution = SessionExecutionProjections(
        generation_ref=generation_ref,
        tool_capabilities=tool_capabilities,
        coding_state=coding_state,
        ui_driver=None,
    )

    assert [item.description for item in execution.definitions()] == [
        "old-tool generation"
    ]
    assert execution.tool_renderers(("probe",)) == {"probe": old_renderer}
    assert execution.provider is old_provider

    with lock:
        generation_ref.publish_locked(new_generation)
        tool_capabilities.publish(new_capability)
        coding_state.refresh_provider(new_provider)

    call = AgentToolCall(
        provider_correlation_id="provider-call",
        tool_name="probe",
        arguments_json=ProductContent("{}"),
    )
    assert execution.execute(call).result.content == ProductContent("old-tool")
    assert execution.tool_renderers(("probe",)) == {"probe": old_renderer}
    assert execution.provider is old_provider
    assert (old_tool.calls, new_tool.calls) == (1, 0)

    assert [item.description for item in execution.definitions()] == [
        "new-tool generation"
    ]
    assert execution.tool_renderers(("probe",)) == {"probe": new_renderer}
    assert execution.provider is new_provider
    assert execution.execute(call).result.content == ProductContent("new-tool")
    assert (old_tool.calls, new_tool.calls) == (1, 1)


def test_retained_tool_call_context_captures_active_provider_turn_generation(
    tmp_path: Path,
) -> None:
    lock = threading.RLock()
    retained: list[Any] = []

    def hook(_event: object, ctx: Any) -> None:
        retained.append(ctx)

    tool = _GenerationTool("tool")
    capabilities = NativeToolCapabilities(
        {},
        {"probe": tool},
        workspace_root=tmp_path,
        reference_roots=(),
        stderr_sink=lambda _text: None,
        filter_options=ToolFilterOptions.empty(),
        cancel_join_timeout_seconds=0.1,
        state_lock=lock,
    )
    capability = capabilities.state
    old = _execution_generation(
        lock, "old", capability, object(), tool_call_hooks=(hook,)
    )
    new = _execution_generation(lock, "new", capability, object())
    generation_ref = SessionGenerationRef(old, lock=lock)
    provider = FakeNativeProvider(supports_tool_calls=True)
    coding = CodingSessionState(
        provider=provider,
        provider_name=provider.name,
        model_id=provider.model_id,
        usage_accumulator=AgentUsageAccumulator(),
        state_lock=lock,
    )
    execution = SessionExecutionProjections(
        generation_ref=generation_ref,
        tool_capabilities=capabilities,
        coding_state=coding,
        ui_driver=None,
    )
    execution.definitions()
    generation_ref.publish(new)
    selected_ids: list[int] = []

    class Mutation:
        def model_runtime_control(
            self, generation_id: int, *, allow_model: bool
        ) -> ExtensionModelRuntimeControl:
            assert allow_model is False

            def selected(_value: object) -> bool:
                selected_ids.append(generation_id)
                return True

            return ExtensionModelRuntimeControl(
                set_active_tools_fn=selected,
                set_thinking_level_fn=selected,
            )

    collaborator = cast(
        SessionCollaborators,
        SimpleNamespace(
            execution_projections=execution,
            provider_mutation=Mutation(),
            cwd=tmp_path,
            terminal_ui=None,
            extension_notify=lambda _kind, _message: None,
            settings=SimpleNamespace(project_trusted=True),
        ),
    )
    call = AgentToolCall(
        provider_correlation_id="call",
        tool_name="probe",
        arguments_json=ProductContent("{}"),
    )
    SessionCollaborators.apply_extension_tool_policy(collaborator, call)

    assert len(retained) == 1
    assert retained[0].set_active_tools(()) is True
    assert retained[0].set_thinking_level("low") is True
    assert selected_ids == [0, 0]


def test_provider_header_callback_is_request_local_across_switch_and_reload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "config"))
    extension_dir = tmp_path / ".pipy/extensions"
    extension_dir.mkdir(parents=True)
    (extension_dir / "headers.py").write_text(
        "from pathlib import Path\n"
        "STATE = Path(__file__).with_name('header-state.txt')\n"
        "VALUE = STATE.read_text(encoding='utf-8') if STATE.exists() else 'before'\n"
        "def activate(api):\n"
        "    @api.on('before_provider_headers')\n"
        "    def before(event, ctx):\n"
        "        event.headers['X-Generation'] = VALUE\n"
        "        event.headers['X-Session'] = ctx.session_manager.get_session_id()\n"
        "    def flip(ctx, args): STATE.write_text('after', encoding='utf-8')\n"
        "    api.register_command('flip-header', 'flip header', flip)\n",
        encoding="utf-8",
    )
    settings = SettingsManager.for_workspace(tmp_path, project_trusted=True)
    callbacks: list[object] = []
    headers: list[dict[str, str | None]] = []
    fake_complete = FakeNativeProvider.complete

    def complete(provider: Any, request: Any, **kwargs: Any) -> Any:
        callback = request.provider_header_callback
        assert callback is not None
        callbacks.append(callback)
        header: dict[str, str | None] = {}
        callback(header)
        headers.append(header)
        return fake_complete(provider, request, **kwargs)

    monkeypatch.setattr(FakeNativeProvider, "complete", complete)
    session = NativeToolReplSession(
        provider=FakeNativeProvider(supports_tool_calls=True),
        settings_manager=settings,
        tool_registry={},
    )
    session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO(
            "first\n/new\nsecond\n/flip-header\n/reload\nthird\n/exit\n"
        ),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )
    assert len(callbacks) == len({id(callback) for callback in callbacks}) == 3
    generations = [header["X-Generation"] for header in headers]
    assert generations == ["before", "before", "after"]
    session_ids = [header["X-Session"] for header in headers]
    assert session_ids[0] != session_ids[1] == session_ids[2]
    callback = callbacks[-1]
    assert isinstance(callback, ProviderHeaderRequestSnapshot)
    assert callback.session_tree.session_id == session_ids[-1]
    retained = weakref.ref(settings)
    del settings, session
    gc.collect()
    assert retained() is None
    callback({})


def test_projectionless_startup_fails_without_install_or_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(
        tool_loop_session,
        "build_candidate_extension_projection",
        lambda *_args, **_kwargs: None,
    )
    for name in ("SessionGenerationRef", "publish_candidate_ownership"):
        monkeypatch.setattr(tool_loop_session, name, pytest.fail)
    errors = io.StringIO()
    result = NativeToolReplSession(
        provider=FakeNativeProvider(supports_tool_calls=True), tool_registry={}
    ).run(
        workspace_root=tmp_path,
        input_stream=io.StringIO(),
        output_stream=io.StringIO(),
        error_stream=errors,
    )
    message = "extension generation projection is unavailable"
    assert (result.status.value, result.exit_code) == ("failed", 2)
    assert (result.error_type, result.error_message) == (
        "ExtensionActivationError",
        message,
    )
    assert errors.getvalue() == f"pipy: {message}\n"


def test_r4a_writer_drain_and_staged_delivery_inventory_is_complete() -> None:
    root = Path(__file__).parents[1] / "src/pipy_harness/native"
    definitions = [
        node.name
        for name in "extension_runtime extension_hooks session_generation tool_loop_session tui ui/components/custom_entry_renderer".split()
        for node in ast.walk(
            ast.parse((root / f"{name}.py").read_text(encoding="utf-8"))
        )
        if isinstance(node, (ast.ClassDef, ast.FunctionDef))
    ]
    required = "OrderedDeliveryToken OrderedDeliveryGate FrozenStagedDeliveryBatch deliver_accepted_staged_batch deliver_staged_custom AcceptedCustomMessageSinks".split()
    assert all(definitions.count(name) == 1 for name in required)


def test_all_production_generation_constructions_supply_projection() -> None:
    root = Path(__file__).parents[1] / "src/pipy_harness/native"
    constructions = sorted(
        (path.relative_to(root).as_posix(), ast.unparse(call.args[1]))
        for path in root.rglob("*.py")
        for call in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(call, ast.Call)
        and ast.unparse(call.func).rsplit(".", 1)[-1] == "SessionExtensionGeneration"
    )
    assert constructions == [
        ("repl/reload.py", "projection"),
        ("tool_loop_session.py", "startup_projection"),
    ]


def test_r4c_deletes_all_converted_legacy_sources_and_equivalence_arms() -> None:
    root = Path(__file__).parents[1]
    session_source = (root / "src/pipy_harness/native/session_generation.py").read_text(
        encoding="utf-8"
    )
    loop_source = (root / "src/pipy_harness/native/tool_loop_session.py").read_text(
        encoding="utf-8"
    )
    renderer_source = (root / "src/pipy_harness/native/tool_renderers.py").read_text(
        encoding="utf-8"
    )
    hooks_source = (root / "src/pipy_harness/native/extension_hooks.py").read_text(
        encoding="utf-8"
    )
    projection_tests = ast.parse(
        (root / "tests/test_native_session_extension_generation.py").read_text(
            encoding="utf-8"
        )
    )

    assert "_build_legacy_extension_tool_port" not in loop_source
    assert "_extension_tool_renderer_map" not in renderer_source
    assert "renderer._tool_renderers" not in session_source
    assert ".runtime.providers" not in loop_source
    assert ".runtime.unregistered_providers" not in loop_source
    assert ".runtime.message_renderers" not in loop_source
    assert ".runtime.entry_renderers" not in loop_source
    assert "extension_renderer_map" not in loop_source
    assert "extension_entry_renderer_map" not in loop_source
    assert "TemporaryLegacyValue" not in session_source
    assert "temporary_legacy" not in session_source
    assert "flag_values: dict" not in session_source
    assert "._lifecycle_hooks" not in hooks_source
    assert "._lifecycle_flags" not in hooks_source
    for (
        field
    ) in "menu_names descriptions shortcuts lifecycle_hooks user_bash_hooks".split():
        assert f"extension_generation.runtime.{field}" not in loop_source
        assert f"ctl.extension_generation.runtime.{field}" not in loop_source
    removed_arms = {
        "test_tool_ports_and_capability_match_the_legacy_adapter",
        "test_renderer_projection_matches_every_legacy_renderer_map",
        "test_provider_projection_matches_legacy_catalog_inputs",
        "test_production_projection_tool_port_matches_legacy_behavior_without_aliases",
        "test_runtime_flag_projection_matches_the_legacy_source",
        "test_command_menu_description_shortcut_projection_matches_legacy_source",
        "test_lifecycle_request_hook_projection_matches_legacy_source",
    }
    remaining_tests = {
        node.name for node in projection_tests.body if isinstance(node, ast.FunctionDef)
    }
    assert removed_arms.isdisjoint(remaining_tests)

    # `SessionExecutionProjections` moved to the module that owns the frozen
    # per-turn views; the invariant below is about that class, not about which
    # file it sits in.
    loop_tree = ast.parse(
        (root / "src/pipy_harness/native/repl/execution_projections.py").read_text(
            encoding="utf-8"
        )
    )
    execution_owner = next(
        node
        for node in loop_tree.body
        if isinstance(node, ast.ClassDef) and node.name == "SessionExecutionProjections"
    )
    begin = next(
        node
        for node in execution_owner.body
        if isinstance(node, ast.FunctionDef) and node.name == "_begin_provider_turn"
    )
    calls = {
        ast.unparse(node.func).rsplit(".", 1)[-1]
        for node in ast.walk(begin)
        if isinstance(node, ast.Call)
    }
    assert calls.isdisjoint(
        {"build_provider", "current_provider", "try_build_extension_provider_port"}
    )


def test_r4a_production_source_has_no_direct_converted_family_dispatch_reads() -> None:
    # The eight dispatchers moved to the module that owns per-operation effects;
    # `tool_loop_session.py` is checked too, so a dispatch call reappearing at the
    # composition root fails the count rather than passing unnoticed.
    root = Path(__file__).parents[1] / "src/pipy_harness/native"
    source = "\n".join(
        (root / name).read_text(encoding="utf-8")
        for name in ("repl/extension_operations.py", "tool_loop_session.py")
    )
    dispatchers = set(
        "dispatch_extension_command dispatch_extension_shortcut dispatch_input_hooks dispatch_before_agent_start_hooks prepare_provider_request dispatch_before_provider_headers_hooks dispatch_tool_result_hooks dispatch_session_before_hooks".split()
    )
    calls = [
        ast.unparse(node)
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in dispatchers
    ]
    assert len(calls) == len(dispatchers) and all(
        "extension_generation.runtime" not in call for call in calls
    )
    fields = "commands input_hooks before_agent_start_hooks before_provider_request_hooks before_provider_headers_hooks tool_result_hooks session_before_switch_hooks session_before_fork_hooks session_before_compact_hooks session_before_tree_hooks".split()
    assert all(
        f"extension_generation.runtime.{field}" not in source for field in fields
    )
    assert "extension_generation.runtime.shortcuts" not in source
