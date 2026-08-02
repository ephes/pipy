from __future__ import annotations

import ast
import gc
import io
import threading
import weakref
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest

import pipy_harness.native.tool_loop_session as tool_loop_session
from pipy_harness.native import FakeNativeProvider, NativeToolReplSession
from pipy_harness.native.agent import AgentUserMessage, ProductContent
from pipy_harness.native.agent.active_input import AgentActiveInput
from pipy_harness.native.agent.loop_policy import AgentProviderRequestPolicyInput
from pipy_harness.native.extension_hooks import _compose_extension_runtime
from pipy_harness.native.extension_runtime import (
    ExtensionCodingSessionControl,
    ExtensionModelRuntimeControl,
    GenerationMessageRouting,
    RegisteredCommand,
    RegisteredShortcut,
    _ExtensionRuntime,
)
from pipy_harness.native.models import ProviderRequest
from pipy_harness.native.session_generation import (
    SessionExtensionGeneration,
    SessionGenerationRef,
    SessionGenerationSnapshot,
)
from pipy_harness.native.session_tree import NativeSessionTree
from pipy_harness.native.settings import SettingsManager
from pipy_harness.native.tool_loop_session import (
    _ProviderHeaderRequestSnapshot,
    _SessionExtensionOperations,
)
from session_generation_test_support import build_test_projection


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
) -> SessionExtensionGeneration:
    runtime = _runtime(lock)
    projection = build_test_projection(runtime, {"generation": label}, queue_mutex=lock)

    def observe(ctx: Any) -> None:
        observations.append((label, str(ctx.flags.get("generation", "absent"))))

    def command(ctx: Any, _args: str) -> None:
        observe(ctx)

    def hook(_event: object, ctx: Any) -> None:
        observe(ctx)

    hook_values: dict[str, Any] = {
        name: (hook,)
        for name in (
            "input before_agent_start before_provider_headers before_provider_request "
            "session_before_switch session_before_fork session_before_compact "
            "session_before_tree"
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
            shortcuts=MappingProxyType(
                {"ctrl-k": RegisteredShortcut("ctrl-k", command, label)}
            ),
        ),
        hooks=replace(projection.hooks, **hook_values),
    )
    return SessionExtensionGeneration(
        runtime, {"generation": f"legacy-{label}"}, projection
    )


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
    operations = _SessionExtensionOperations(
        generation_ref=generation_ref,
        cwd=str(tmp_path),
        has_ui=False,
        notify_sink=None,
        ui_driver=None,
        project_trusted=True,
    )
    model_runtime = ExtensionModelRuntimeControl()
    if family == "command":
        operations.dispatch_command(
            "/probe",
            coding_session=ExtensionCodingSessionControl(),
            ui_custom_driver=None,
            model_runtime=model_runtime,
        )
    elif family == "shortcut":
        operations.dispatch_shortcut(
            "ctrl-k",
            coding_session=ExtensionCodingSessionControl(),
            ui_custom_driver=None,
            model_runtime=model_runtime,
        )
    elif family == "input":
        assert (
            operations.dispatch_input("prompt", model_runtime=model_runtime) == "prompt"
        )
    elif family == "before-agent":
        operations.dispatch_before_agent_start("system", model_runtime=model_runtime)
    elif family == "before-provider-request":
        operations.prepare_provider_request(
            _request_input(tmp_path), model_runtime=model_runtime
        )
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
            model_runtime=model_runtime,
        )
        assert transformed.value == "result"
        if identity:
            assert transformed is content
    else:
        assert operations.session_allows(
            "switch",
            operation="switch",
            target="new",
            model_runtime=model_runtime,
        ).allow
    assert generation_ref.snapshot_calls == 1
    assert generation_ref.current is new
    expected = [] if identity else [("old", "absent" if family == "input" else "old")]
    assert observations == expected


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
    assert isinstance(callback, _ProviderHeaderRequestSnapshot)
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
        "_build_candidate_extension_projection",
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
        for name in "extension_runtime extension_hooks session_generation tool_loop_session tui".split()
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
        (path.relative_to(root).as_posix(), ast.unparse(call.args[2]))
        for path in root.rglob("*.py")
        for call in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(call, ast.Call)
        and ast.unparse(call.func).rsplit(".", 1)[-1] == "SessionExtensionGeneration"
    )
    assert constructions == [
        ("tool_loop_session.py", "projection"),
        ("tool_loop_session.py", "startup_projection"),
    ]


def test_r4a_production_source_has_no_direct_converted_family_dispatch_reads() -> None:
    path = Path(__file__).parents[1] / "src/pipy_harness/native/tool_loop_session.py"
    source = path.read_text(encoding="utf-8")
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
    assert source.count("extension_generation.runtime.shortcuts") == 1
