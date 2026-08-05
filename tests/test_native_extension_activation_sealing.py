from __future__ import annotations

import ast
import threading
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import replace
from enum import Enum, StrEnum
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import Any, cast

import pytest
from session_generation_test_support import build_test_projection

import pipy_harness.native.extensions.activation as activation
from pipy_harness.extensions import (
    ExtensionCapabilityError,
    ExtensionFlag,
    ExtensionOAuthConfig,
    ExtensionProvider,
    ExtensionTool,
    ToolResult,
)
from pipy_harness.native import extension_hooks, extension_provider_catalog
from pipy_harness.native.coding.commands import (
    CodingCommandAction,
    CodingCommandFooterPolicy,
    CodingCommandOutcome,
    CodingCommandOutcomeKind,
)
from pipy_harness.native.extension_loader import _run_awaitable
from pipy_harness.native.extension_types import (
    QueuedCustomMessage,
    QueuedUserMessage,
    _ActivationError,
)
from pipy_harness.native.extensions import contracts as extension_contracts
from pipy_harness.native.extensions.activation import (
    _ACTIVATION_LIFECYCLE_TOKEN,
    _ActivationApi,
    _ActivationCleanup,
    _ExtensionCandidate,
    activate_extension_batch,
)
from pipy_harness.native.extensions.contracts import (
    ActivatedExtension,
    _ExtensionRuntime,
)
from pipy_harness.native.extensions.flag_tokens import (
    parse_extension_flag_tokens,
)
from pipy_harness.native.extensions.message_routing import GenerationMessageRouting
from pipy_harness.native.extensions.packages import discover_extensions
from pipy_harness.native.repl.reload import (
    ImplicitTrustState,
    ReloadCommandEffects,
)
from pipy_harness.native.session_generation import (
    ExtensionQueueProjection,
    GenerationQueueHandle,
    OrderedDeliveryGate,
    SessionExtensionGeneration,
    SessionGenerationRef,
)
from pipy_harness.native.tool_capabilities import (
    NativeToolCapabilities,
    ToolFilterOptions,
)


def _host(
    *,
    outbox: list | None = None,
    custom_outbox: list | None = None,
    guard: AbstractContextManager[object] | None = None,
    reserved: frozenset[str] = frozenset(),
    reserved_tools: frozenset[str] = frozenset(),
    taken_flags: frozenset[str] = frozenset(),
    message_routing: GenerationMessageRouting | None = None,
    boundary_observer: Callable[[str], None] | None = None,
) -> _ActivationApi:
    user = [] if outbox is None else outbox
    custom = [] if custom_outbox is None else custom_outbox
    routing = message_routing or GenerationMessageRouting(user, custom)
    return _ActivationApi(
        "sealed-test",
        reserved=reserved,
        taken=frozenset(),
        reserved_tools=reserved_tools,
        taken_flags=taken_flags,
        outbox=user,
        custom_outbox=custom,
        guard=guard,
        message_routing=routing,
        boundary_observer=boundary_observer,
    )


def _install_candidate_route(
    routing: GenerationMessageRouting, mutex: threading.RLock
) -> OrderedDeliveryGate:
    gate = OrderedDeliveryGate(mutex)
    ExtensionQueueProjection(
        GenerationQueueHandle(routing.user_outbox, mutex),
        GenerationQueueHandle(routing.custom_outbox, mutex),
        routing,
    ).install_candidate_route(gate)
    return gate


def _tool(name: str = "tool") -> ExtensionTool:
    return ExtensionTool(
        name=name,
        description="tool",
        input_schema={"type": "object"},
        handler=lambda _ctx, _params: ToolResult(content="ok"),
    )


def _provider(name: str = "provider") -> ExtensionProvider:
    return ExtensionProvider(
        name=name,
        default_model="model",
        models=("model",),
        factory=lambda _ctx: None,
    )


class _ActivationString(StrEnum):
    COMMAND = "command"
    SHORTCUT = "ctrl-k"
    TOOL = "tool"
    PROVIDER = "provider"
    MODEL = "model"
    OAUTH = "oauth"
    UNREGISTERED = "builtin"
    FLAG = "flag"
    FLAG_TYPE = "string"
    DEFAULT = "default"
    MESSAGE = "message"
    ENTRY = "entry"
    EVENT = "event"


class _DefaultEnumString(str, Enum):
    COMMAND = "command"
    SHORTCUT = "ctrl-k"
    TOOL = "tool"
    PROVIDER = "provider"
    MODEL = "model"
    OAUTH = "oauth"
    UNREGISTERED = "builtin"
    FLAG = "flag"
    FLAG_TYPE = "string"
    DEFAULT = "default"
    MESSAGE = "message"
    ENTRY = "entry"
    EVENT = "event"


class _HostileString(str):
    def __str__(self) -> str:
        return "wrong"


@pytest.mark.parametrize(
    "make_string",
    [
        pytest.param(lambda value: _ActivationString(value), id="StrEnum"),
        pytest.param(lambda value: _DefaultEnumString(value), id="str-Enum"),
        pytest.param(_HostileString, id="hostile-str-subclass"),
    ],
)
def test_every_activation_string_normalizer_accepts_and_detaches_subclasses(
    make_string: Callable[[str], str],
) -> None:
    api = _host()
    hook_calls: list[str] = []

    def handler(*_args: object) -> None:
        hook_calls.append("called")

    api.register_command(make_string("command"), "command", handler)
    api.register_shortcut(make_string("ctrl-k"), handler)
    api.register_tool(_tool(make_string("tool")))
    api.register_provider(
        ExtensionProvider(
            name=make_string("provider"),
            default_model=make_string("model"),
            models=(make_string("model"),),
            factory=lambda _ctx: None,
            oauth=ExtensionOAuthConfig(
                name=make_string("oauth"),
                login=lambda *_args: None,
                refresh_token=lambda *_args: None,
                get_api_key=lambda *_args: None,
            ),
        )
    )
    api.unregister_provider(make_string("builtin"))
    api.register_flag(
        ExtensionFlag(
            make_string("flag"),
            cast(Any, make_string("string")),
            default=make_string("default"),
        )
    )
    assert api.get_flag(make_string("flag")) == "default"
    api.register_message_renderer(make_string("message"), handler)
    api.register_entry_renderer(make_string("entry"), handler)
    api.on(make_string("event"), handler)

    snapshot = api._seal_and_freeze(_lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN)

    oauth = snapshot.providers[0].provider.oauth
    assert oauth is not None
    normalized = (
        snapshot.commands[0].name,
        snapshot.shortcuts[0].key,
        snapshot.tools[0].tool.name,
        snapshot.providers[0].provider.name,
        snapshot.providers[0].provider.models[0],
        snapshot.providers[0].provider.default_model,
        oauth.name,
        snapshot.unregistered_providers[0],
        snapshot.flags[0].flag.name,
        snapshot.flags[0].flag.flag_type,
        snapshot.flags[0].flag.default,
        snapshot.message_renderers[0].custom_type,
        snapshot.entry_renderers[0].custom_type,
        next(iter(snapshot.hooks)),
    )
    expected = (
        "command",
        "ctrl-k",
        "tool",
        "provider",
        "model",
        "model",
        "oauth",
        "builtin",
        "flag",
        "string",
        "default",
        "message",
        "entry",
        "event",
    )
    assert normalized == expected
    assert all(type(value) is str for value in normalized)

    activated = ActivatedExtension(
        name="string-normalization",
        version="1",
        path_label="string-normalization.py",
        status="activated",
        reason=None,
        commands=snapshot.commands,
        diagnostic=None,
        hooks=snapshot.hooks,
    )
    hooks = extension_hooks.extension_event_hooks((activated,), "event")
    assert hooks == (handler,)
    assert extension_hooks.extension_event_hooks((activated,), "wrong") == ()
    hooks[0]()
    assert hook_calls == ["called"]


@pytest.mark.parametrize("invalid", [42, None, "", " ", "/", "bad/name"])
def test_unregister_provider_rejects_invalid_values_and_records_the_failure(
    invalid: object,
) -> None:
    api = _host()

    with pytest.raises(_ActivationError) as raised:
        api.unregister_provider(cast(Any, invalid))

    assert raised.value.reason == "invalid_provider"
    snapshot = api._seal_and_freeze(_lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN)
    assert snapshot.failure == ("invalid_provider", None)
    assert snapshot.unregistered_providers == ()


def test_get_flag_exact_string_lookup_is_non_throwing_for_invalid_values() -> None:
    api = _host()
    api.register_flag(ExtensionFlag("flag", "string", default="value"))

    assert api.get_flag(_ActivationString.FLAG) == "value"
    assert api.get_flag(_DefaultEnumString.FLAG) == "value"
    assert api.get_flag(_HostileString("flag")) == "value"
    assert api.get_flag(cast(Any, 42)) is None


def test_unexpected_normalization_exception_records_bounded_first_failure() -> None:
    api = _host()

    class _ExplodingDescription:
        def __str__(self) -> str:
            raise RuntimeError("extension-controlled secret")

    # Model extension code catching the raised validation error and continuing.
    with pytest.raises(_ActivationError) as first_error:
        api.register_command(
            "command",
            cast(str, _ExplodingDescription()),
            lambda *_args: None,
        )
    assert first_error.value.reason == "invalid_command_name"
    assert first_error.value.diagnostic == "RuntimeError"

    with pytest.raises(_ActivationError) as later_error:
        api.register_provider(cast(Any, None))
    assert later_error.value.reason == "invalid_provider"

    snapshot = api._seal_and_freeze(_lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN)
    assert snapshot.failure == ("invalid_command_name", "RuntimeError")
    assert snapshot.commands == ()
    assert snapshot.providers == ()


def test_combined_invalid_registrations_keep_each_family_historical_precedence() -> (
    None
):
    reserved_command = _host(reserved=frozenset(("reserved",)))
    with pytest.raises(_ActivationError) as command_error:
        reserved_command.register_command("reserved", "reserved", cast(Any, None))
    assert command_error.value.reason == "reserved_command"

    reserved_tool = _host(reserved_tools=frozenset(("reserved",)))
    invalid_schema_tool = ExtensionTool(
        name="reserved",
        description="reserved",
        input_schema=cast(Any, None),
        handler=lambda _ctx, _params: ToolResult(content="unused"),
    )
    with pytest.raises(_ActivationError) as tool_error:
        reserved_tool.register_tool(invalid_schema_tool)
    assert tool_error.value.reason == "reserved_tool"

    duplicate_provider = _host()
    duplicate_provider.register_provider(_provider("duplicate"))
    invalid_duplicate_provider = ExtensionProvider(
        name="duplicate",
        default_model="model",
        models=("model",),
        factory=cast(Any, None),
    )
    with pytest.raises(_ActivationError) as provider_error:
        duplicate_provider.register_provider(invalid_duplicate_provider)
    assert provider_error.value.reason == "invalid_provider"

    reserved_shortcut = _host()
    with pytest.raises(_ActivationError) as shortcut_error:
        reserved_shortcut.register_shortcut("ctrl-g", cast(Any, None))
    assert shortcut_error.value.reason == "invalid_shortcut"

    duplicate_flag = _host(taken_flags=frozenset(("duplicate",)))
    with pytest.raises(_ActivationError) as flag_error:
        duplicate_flag.register_flag(
            ExtensionFlag("duplicate", cast(Any, "invalid-type"))
        )
    assert flag_error.value.reason == "duplicate_flag"

    duplicate_message = _host()
    duplicate_message.register_message_renderer("duplicate", lambda: None)
    with pytest.raises(_ActivationError) as message_error:
        duplicate_message.register_message_renderer("duplicate", cast(Any, None))
    assert message_error.value.reason == "invalid_message_renderer"

    duplicate_entry = _host()
    duplicate_entry.register_entry_renderer("duplicate", lambda: None)
    with pytest.raises(_ActivationError) as entry_error:
        duplicate_entry.register_entry_renderer("duplicate", cast(Any, None))
    assert entry_error.value.reason == "invalid_entry_renderer"

    for api, reason in (
        (reserved_command, "reserved_command"),
        (reserved_tool, "reserved_tool"),
        (duplicate_provider, "invalid_provider"),
        (reserved_shortcut, "invalid_shortcut"),
        (duplicate_flag, "duplicate_flag"),
        (duplicate_message, "invalid_message_renderer"),
        (duplicate_entry, "invalid_entry_renderer"),
    ):
        snapshot = api._seal_and_freeze(_lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN)
        assert snapshot.failure == (reason, None)


def test_dataclass_normalization_preserves_callbacks_renderers_and_defaults() -> None:
    def tool_handler(*_args: object) -> ToolResult:
        return ToolResult(content="ok")

    def render_call(_ctx: object) -> str:
        return "call"

    def render_result(_ctx: object) -> str:
        return "result"

    def factory(_ctx: object) -> None:
        return None

    def oauth_callback(*_args: object) -> None:
        return None

    api = _host()
    api.register_tool(
        ExtensionTool(
            name="tool",
            description="tool",
            input_schema={"type": "object"},
            handler=tool_handler,
            render_call=render_call,
            render_result=render_result,
        )
    )
    api.register_provider(
        ExtensionProvider(
            name="provider",
            default_model="model",
            models=("model",),
            factory=factory,
            oauth=ExtensionOAuthConfig(
                name="oauth",
                login=oauth_callback,
                refresh_token=oauth_callback,
                get_api_key=oauth_callback,
                modify_models=oauth_callback,
            ),
        )
    )
    api.register_flag(ExtensionFlag("flag", "string", default="kept"))

    snapshot = api._seal_and_freeze(_lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN)
    tool = snapshot.tools[0].tool
    provider = snapshot.providers[0].provider
    oauth = provider.oauth
    flag = snapshot.flags[0].flag

    assert tool.handler is tool_handler
    assert tool.render_call is render_call
    assert tool.render_result is render_result
    assert provider.factory is factory
    assert oauth is not None
    assert oauth.login is oauth_callback
    assert oauth.refresh_token is oauth_callback
    assert oauth.get_api_key is oauth_callback
    assert oauth.modify_models is oauth_callback
    assert provider.default_model == "model"
    assert flag.default == "kept"


def _class_d_calls(api: _ActivationApi) -> tuple[tuple[str, Callable[[], object]], ...]:
    def handler(*_args: object) -> None:
        return None

    return (
        ("command", lambda: api.register_command("late", "late", handler)),
        ("shortcut", lambda: api.register_shortcut("ctrl-k", handler)),
        ("tool", lambda: api.register_tool(_tool("late_tool"))),
        ("provider", lambda: api.register_provider(_provider("late_provider"))),
        ("unregister", lambda: api.unregister_provider("builtin")),
        ("flag", lambda: api.register_flag(ExtensionFlag("late_flag", "boolean"))),
        (
            "message_renderer",
            lambda: api.register_message_renderer("late_message", handler),
        ),
        ("entry_renderer", lambda: api.register_entry_renderer("late_entry", handler)),
        ("on_direct", lambda: api.on("late_direct", handler)),
        ("on_decorator_factory", lambda: api.on("late_decorator")),
    )


@pytest.mark.parametrize("terminal_state", ["success", "rejection"])
def test_every_late_class_d_return_shape_refuses_success_and_rejection(
    terminal_state: str,
) -> None:
    api = _host()
    retained_decorator = api.on("retained")
    if terminal_state == "success":
        snapshot = api._seal_and_freeze(_lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN)
        assert not hasattr(snapshot, "activated")
    else:
        api._dispose()

    for label, call in _class_d_calls(api):
        with pytest.raises(ExtensionCapabilityError):
            call()

    with pytest.raises(ExtensionCapabilityError):
        cast(Callable[[Callable[..., object]], object], retained_decorator)(
            lambda: None
        )


@pytest.mark.parametrize(
    "hostile_statement",
    [
        "api.seal_and_freeze(activate=True)",
        "api.commit_activation()",
        "api.dispose()",
        "api._seal_and_freeze()",
        "api._commit_activation()",
        "api._dispose()",
        "api.send_user_message('must-drop'); api._seal_and_freeze()",
        (
            "api._seal_and_freeze(); "
            "api._commit_activation(); api._transition_locked('published')"
        ),
        "api._state = 'corrupt'",
        "api.send_user_message('must-drop'); api._state = 'published'",
    ],
)
def test_lifecycle_seams_are_private_and_hostile_state_changes_disable_only_owner(
    hostile_statement: str,
    tmp_path: Path,
) -> None:
    extension_dir = tmp_path / ".pipy" / "extensions"
    extension_dir.mkdir(parents=True)
    (extension_dir / "a_bad.py").write_text(
        f"def activate(api):\n    {hostile_statement}\n",
        encoding="utf-8",
    )
    (extension_dir / "z_good.py").write_text(
        "def activate(api):\n"
        "    api.register_command('good', 'good', lambda _ctx, _args: None)\n",
        encoding="utf-8",
    )

    descriptors = discover_extensions(tmp_path, include_workspace_defaults=True)
    batch = activate_extension_batch(descriptors)
    by_name = {item.name: item for item in batch.activated}

    assert by_name["a_bad"].status == "disabled"
    assert by_name["a_bad"].reason == "activation_error"
    assert by_name["z_good"].status == "activated"
    assert [item.name for item in by_name["z_good"].commands] == ["good"]
    assert batch.message_outbox == []
    for item in batch.activated:
        if item._activation_host is not None:
            item._activation_host._dispose()


def test_one_frozen_snapshot_contains_every_staged_family_not_live_state() -> None:
    outbox: list = []
    api = _host(outbox=outbox)

    def handler(*_args: object) -> None:
        return None

    api.register_command("command", "command", handler)
    api.register_shortcut("ctrl-k", handler)
    api.register_tool(_tool())
    api.register_provider(_provider())
    api.unregister_provider("builtin")
    api.register_flag(ExtensionFlag("flag", "boolean", default=True))
    api.register_message_renderer("message", handler)
    api.register_entry_renderer("entry", handler)
    api.on("direct", handler)
    decorator = cast(Callable[[Callable[..., object]], object], api.on("decorated"))
    decorator(handler)
    api.send_user_message("user")
    api.send_message({"customType": "custom", "content": "message"})

    snapshot = api._seal_and_freeze(_lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN)

    assert [item.name for item in snapshot.commands] == ["command"]
    assert [item.key for item in snapshot.shortcuts] == ["ctrl-k"]
    assert [item.tool.name for item in snapshot.tools] == ["tool"]
    assert [item.provider.name for item in snapshot.providers] == ["provider"]
    assert snapshot.unregistered_providers == ("builtin",)
    assert [item.flag.name for item in snapshot.flags] == ["flag"]
    assert [item.custom_type for item in snapshot.message_renderers] == ["message"]
    assert [item.custom_type for item in snapshot.entry_renderers] == ["entry"]
    assert set(snapshot.hooks) == {"direct", "decorated"}
    assert [item.content for item in snapshot.user_messages] == ["user"]
    assert [item.custom_type for item in snapshot.custom_messages] == ["custom"]
    assert snapshot.failure is None
    assert not hasattr(snapshot, "activated")
    with api._guard:
        assert api._activated is False
    assert outbox == []

    committed_custom = api._commit_activation(
        _lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN
    )
    assert [item.content for item in outbox] == ["user"]
    assert [item.custom_type for item in committed_custom] == ["custom"]
    with api._guard:
        assert api._activated is True
    with pytest.raises(ExtensionCapabilityError):
        api._commit_activation(_lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN)
    with pytest.raises(ExtensionCapabilityError):
        api._seal_and_freeze(_lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN)
    assert [item.content for item in outbox] == ["user"]
    assert [item.custom_type for item in committed_custom] == ["custom"]


def test_seal_snapshot_is_authoritative_for_late_user_and_custom_messages() -> None:
    outbox: list = []
    custom_outbox: list = []
    api = _host(outbox=outbox, custom_outbox=custom_outbox)
    api.send_user_message("frozen-user")
    api.send_message({"customType": "frozen", "content": "frozen-custom"})

    snapshot = api._seal_and_freeze(_lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN)
    api._accept_message_route()
    api.send_user_message("late-user")
    api.send_message({"customType": "late", "content": "late-custom"})

    committed_custom = api._commit_activation(
        _lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN
    )
    assert [message.content for message in snapshot.user_messages] == ["frozen-user"]
    assert [message.content for message in outbox] == ["frozen-user"]
    assert [message.content for message in committed_custom] == ["frozen-custom"]
    assert custom_outbox == []

    runtime = _empty_runtime(api)
    assert _ExtensionCandidate(runtime).publish() is True
    api.send_user_message("live-user")
    api.send_message({"customType": "live", "content": "live-custom"})
    assert [message.content for message in outbox] == ["frozen-user", "live-user"]
    assert [message.content for message in custom_outbox] == ["live-custom"]


def test_activated_extension_hooks_are_uniformly_immutable() -> None:
    disabled = ActivatedExtension(
        name="disabled",
        version="1",
        path_label="disabled.py",
        status="disabled",
        reason="activation_error",
        commands=(),
        diagnostic=None,
    )
    activated = ActivatedExtension(
        name="active",
        version="1",
        path_label="active.py",
        status="activated",
        reason=None,
        commands=(),
        diagnostic=None,
        hooks={"event": (lambda: None,)},
    )

    for result in (disabled, activated):
        assert isinstance(result.hooks, Mapping)
        assert isinstance(result.hooks, MappingProxyType)
        with pytest.raises(TypeError):
            cast(Any, result.hooks)["late"] = ()


def test_every_registration_family_uses_the_single_staging_seam() -> None:
    syntax = ast.parse(Path(activation.__file__ or "").read_text(encoding="utf-8"))
    activation_api = next(
        node
        for node in syntax.body
        if isinstance(node, ast.ClassDef) and node.name == "_ActivationApi"
    )
    methods = {
        node.name: node
        for node in activation_api.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    registration_families = {
        "register_command",
        "register_shortcut",
        "register_tool",
        "register_provider",
        "register_flag",
        "register_message_renderer",
        "register_entry_renderer",
    }

    for name in registration_families:
        calls = {
            node.func.attr
            for node in ast.walk(methods[name])
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        assert "_stage_registration" in calls, name


class _CountingGuard(AbstractContextManager[object]):
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.entries = 0
        self.depth = 0

    def __enter__(self) -> object:
        self._lock.acquire()
        self.entries += 1
        self.depth += 1
        return self

    def __exit__(self, *_args: object) -> None:
        self.depth -= 1
        self._lock.release()


def test_every_candidate_reader_and_writer_uses_the_host_guard() -> None:
    guard = _CountingGuard()
    api = _host(guard=guard)

    def handler(*_args: object) -> None:
        return None

    calls: tuple[tuple[int, Callable[[], object]], ...] = (
        (3, lambda: api.register_command("command", "command", handler)),
        (3, lambda: api.register_shortcut("ctrl-k", handler)),
        (3, lambda: api.register_tool(_tool())),
        (2, lambda: api.register_provider(_provider())),
        (2, lambda: api.unregister_provider("builtin")),
        (3, lambda: api.register_flag(ExtensionFlag("flag", "boolean", default=False))),
        (2, lambda: api.register_message_renderer("message", handler)),
        (2, lambda: api.register_entry_renderer("entry", handler)),
        (3, lambda: api.on("hook", handler)),
        (1, lambda: api.send_user_message("user")),
        (1, lambda: api.send_message({"customType": "custom", "content": "message"})),
        (1, lambda: api.get_flag("flag")),
    )
    for expected_entries, call in calls:
        before = guard.entries
        call()
        assert guard.entries == before + expected_entries
        assert guard.depth == 0

    before = guard.entries
    snapshot = api._seal_and_freeze(_lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN)
    assert guard.entries == before + 1
    registered = snapshot.flags[0]
    assert not hasattr(registered, "values")

    before = guard.entries
    values, error = parse_extension_flag_tokens((registered,), ("--flag",))
    assert error is None and values == {"flag": True}
    assert guard.entries == before + 1

    before = guard.entries
    assert registered.get_value() is True
    assert guard.entries == before + 1

    before = guard.entries
    api._commit_activation(_lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN)
    assert guard.entries == before + 1

    before = guard.entries
    api._dispose()
    assert guard.entries == before + 1


class _PausingGuard(_CountingGuard):
    def __init__(self, pause_name: str, *, pause_entry: int) -> None:
        super().__init__()
        self.pause_name = pause_name
        self.pause_entry = pause_entry
        self.named_entries = 0
        self.entered = threading.Event()
        self.release = threading.Event()

    def __enter__(self) -> object:
        value = super().__enter__()
        if threading.current_thread().name == self.pause_name:
            self.named_entries += 1
            if self.named_entries == self.pause_entry:
                self.entered.set()
                assert self.release.wait(2)
        return value


def test_registration_that_wins_the_guard_is_in_the_sealed_snapshot() -> None:
    # The registrar pauses while holding its final commit acquisition. The
    # sealer therefore cannot win an incidental scheduling race.
    guard = _PausingGuard("registrar", pause_entry=3)
    api = _host(guard=guard)
    snapshot: list = []

    registrar = threading.Thread(
        target=lambda: api.register_command("before", "before", lambda *_args: None),
        name="registrar",
    )
    sealer = threading.Thread(
        target=lambda: snapshot.append(
            api._seal_and_freeze(_lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN)
        ),
        name="sealer",
    )
    registrar.start()
    assert guard.entered.wait(2)
    sealer.start()
    guard.release.set()
    registrar.join(2)
    sealer.join(2)

    assert not registrar.is_alive() and not sealer.is_alive()
    assert [item.name for item in snapshot[0].commands] == ["before"]


def test_extension_string_callback_runs_unlocked_and_seal_wins_before_commit() -> None:
    guard = _CountingGuard()
    api = _host(guard=guard)
    validating = threading.Event()
    release = threading.Event()
    failures: list[BaseException] = []

    class _HostileDescription:
        def __str__(self) -> str:
            assert guard.depth == 0
            validating.set()
            assert release.wait(2)
            return "description"

    def register() -> None:
        try:
            api.register_command(
                "barrier",
                cast(str, _HostileDescription()),
                lambda *_args: None,
            )
        except BaseException as exc:  # noqa: BLE001 - thread outcome transport
            failures.append(exc)

    thread = threading.Thread(target=register, name="hostile-validator")
    thread.start()
    assert validating.wait(2)
    snapshot = api._seal_and_freeze(_lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN)
    release.set()
    thread.join(2)

    assert not thread.is_alive()
    assert snapshot.commands == ()
    assert len(failures) == 1
    assert isinstance(failures[0], ExtensionCapabilityError)
    assert guard.depth == 0


def test_abandoned_worker_cannot_add_any_late_staged_family() -> None:
    outbox: list = []
    custom_outbox: list = []
    api = _host(outbox=outbox, custom_outbox=custom_outbox)
    release = threading.Event()
    finished = threading.Event()
    refused: list[str] = []

    def worker() -> None:
        assert release.wait(2)
        for label, call in _class_d_calls(api):
            try:
                call()
            except ExtensionCapabilityError:
                refused.append(label)
        api.send_user_message("late user")
        api.send_message({"customType": "late_custom", "content": "late"})
        finished.set()

    thread = threading.Thread(target=worker, name="abandoned-activation")
    thread.start()
    api._dispose()
    release.set()
    assert finished.wait(2)
    thread.join(2)

    assert refused == [label for label, _call in _class_d_calls(api)]
    assert outbox == []
    assert custom_outbox == []
    assert api.get_flag("late_flag") is None


class _LockFamilyState:
    def __init__(self) -> None:
        self.active: str | None = None
        self.depth = 0
        self.log: list[str] = []


class _FamilyLock(AbstractContextManager[object]):
    def __init__(self, family: str, state: _LockFamilyState) -> None:
        self.family = family
        self.state = state
        self._lock = threading.RLock()

    def acquire(self) -> bool:
        self.__enter__()
        return True

    def release(self) -> None:
        self.__exit__()

    def __enter__(self) -> object:
        self._lock.acquire()
        if self.state.active not in (None, self.family):
            self._lock.release()
            raise AssertionError(f"nested {self.state.active} -> {self.family}")
        self.state.active = self.family
        self.state.depth += 1
        self.state.log.append(f"enter:{self.family}")
        return self

    def __exit__(self, *_args: object) -> None:
        self.state.log.append(f"exit:{self.family}")
        self.state.depth -= 1
        if self.state.depth == 0:
            self.state.active = None
        self._lock.release()


def test_installed_route_is_per_host_and_refuses_ineligible_siblings() -> None:
    outbox: list[QueuedUserMessage] = []
    custom_outbox: list[QueuedCustomMessage] = []
    mutex = threading.RLock()
    routing = GenerationMessageRouting(outbox, custom_outbox, mutex=mutex)

    def sibling() -> _ActivationApi:
        return _host(
            outbox=outbox,
            custom_outbox=custom_outbox,
            message_routing=routing,
        )

    (
        open_host,
        pending_host,
        committed_host,
        rejected_host,
        disposed_host,
        accepted_host,
    ) = (sibling() for _ in range(6))
    for host in (pending_host, committed_host, disposed_host, accepted_host):
        host._seal_and_freeze(_lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN)
    committed_host._commit_activation(_lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN)
    assert rejected_host._dispose() and disposed_host._dispose()
    accepted_host._accept_message_route()

    gate = _install_candidate_route(routing, mutex)
    with gate.reserve() as token:
        open_host.send_user_message("open-staged")
        for refused in (pending_host, committed_host, rejected_host, disposed_host):
            refused.send_user_message("refused")
        accepted_host.send_user_message("accepted-user")
        accepted_host.send_message({"customType": "named", "content": "accepted"})
        accepted_host.sendMessage({"customType": "alias", "content": "alias"})
        assert outbox == [] and custom_outbox == []
        assert routing.release_pending() == 3
        gate.release(token)
        assert gate.drain(token)

    open_snapshot = open_host._seal_and_freeze(
        _lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN
    )
    assert [message.content for message in open_snapshot.user_messages] == [
        "open-staged"
    ]
    assert [message.content for message in outbox] == ["accepted-user"]
    assert [message.content for message in custom_outbox] == ["accepted", "alias"]


@pytest.mark.parametrize("custom_message", (False, True))
def test_reservation_precedes_disposal_and_retirement_drops_only_the_tail(
    custom_message: bool,
) -> None:
    state = _LockFamilyState()
    mutex = threading.RLock()
    outbox: list[QueuedUserMessage] = []
    custom: list[QueuedCustomMessage] = []
    reservation_ready = threading.Event()
    release_reservation = threading.Event()
    submission_entered = threading.Event()
    release_submission = threading.Event()

    def boundary(event: str) -> None:
        if event == "host_guard_exit" and not reservation_ready.is_set():
            reservation_ready.set()
            assert release_reservation.wait(1)
        elif event == "detached_batch_submission":
            assert state.active is None
            submission_entered.set()
            assert release_submission.wait(1)

    routing = GenerationMessageRouting(
        outbox, custom, mutex=mutex, boundary_observer=boundary
    )
    host = _host(
        outbox=outbox,
        custom_outbox=custom,
        guard=_FamilyLock("candidate", state),
        message_routing=routing,
        boundary_observer=boundary,
    )
    host._seal_and_freeze(_lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN)
    host._accept_message_route()
    host._commit_activation(_lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN)
    send = (
        (lambda: host.send_message({"customType": "bound", "content": "direct"}))
        if custom_message
        else (lambda: host.send_user_message("direct"))
    )
    sender = threading.Thread(target=send)
    sender.start()
    assert reservation_ready.wait(1)
    assert host._dispose()
    release_reservation.set()
    sender.join(1)
    send()
    target = custom if custom_message else outbox
    assert [message.content for message in target] == ["direct"]

    successor = _host(
        outbox=outbox,
        custom_outbox=custom,
        guard=_FamilyLock("candidate", state),
        message_routing=routing,
        boundary_observer=boundary,
    )
    successor._seal_and_freeze(_lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN)
    successor._accept_message_route()
    successor._commit_activation(_lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN)
    gate = _install_candidate_route(routing, mutex)
    with gate.reserve() as token:
        successor.send_user_message("detached-before-retire")
        released: list[int] = []
        releaser = threading.Thread(
            target=lambda: released.append(routing.release_pending())
        )
        releaser.start()
        assert submission_entered.wait(1)
        successor.send_user_message("attached-drop")
        retired = routing.retire()
        assert len(retired) == 3  # attached tail, gate, and closed queue entry
        assert outbox == custom == []
        successor.send_user_message("retirement-first-drop")
        release_submission.set()
        releaser.join(1)
        assert released == [1]
        gate.release(token)
        assert gate.drain(token)

    assert [message.content for message in outbox][-1] == "detached-before-retire"


def _reload_owners(ref: SessionGenerationRef, emitter: Any = None):
    capabilities = NativeToolCapabilities(
        {},
        {},
        workspace_root=Path.cwd(),
        reference_roots=(),
        stderr_sink=lambda _text: None,
        filter_options=ToolFilterOptions.empty(),
        cancel_join_timeout_seconds=1.0,
        state_lock=ref.lock,
    )
    mutation = SimpleNamespace(coding_state=None)
    mutation.extension_notify = lambda *_args: None
    mutation.extension_set_active_tools = lambda _generation_id, _names: True
    lifecycle = emitter or SimpleNamespace()
    if emitter is None:
        lifecycle.fire_candidate_session_start = lambda *_args, **_kwargs: None
    return capabilities, mutation, lifecycle, SimpleNamespace(_tool_renderers={})


def _empty_runtime(
    host: _ActivationApi, *additional_hosts: _ActivationApi
) -> _ExtensionRuntime:
    message_routing = host._message_routing
    return _ExtensionRuntime(
        commands={},
        menu_names=(),
        descriptions={},
        tool_call_hooks=(),
        lifecycle_hooks={},
        input_hooks=(),
        before_agent_start_hooks=(),
        tool_result_hooks=(),
        user_bash_hooks=(),
        before_provider_headers_hooks=(),
        before_provider_request_hooks=(),
        session_before_switch_hooks=(),
        session_before_fork_hooks=(),
        session_before_compact_hooks=(),
        session_before_tree_hooks=(),
        outbox=message_routing.user_outbox,
        custom_outbox=message_routing.custom_outbox,
        tools=(),
        shortcuts={},
        flags=(),
        providers=(),
        unregistered_providers=(),
        message_renderers={},
        entry_renderers={},
        custom_messages=(),
        activation_hosts=(host, *additional_hosts),
        message_routing=message_routing,
    )


def test_candidate_and_session_guards_never_nest_and_callbacks_run_unlocked() -> None:
    state = _LockFamilyState()
    candidate_guard = _FamilyLock("candidate", state)
    session_lock = _FamilyLock("session", state)
    session_mutex = threading.RLock()
    flush_unlocked = threading.Event()

    def boundary(event: str) -> None:
        if event == "frozen_commit_flush":
            assert state.active is None

            def acquire() -> None:
                with session_mutex:
                    flush_unlocked.set()

            probe = threading.Thread(target=acquire)
            probe.start()
            probe.join(1)
            assert flush_unlocked.is_set()

    host = _host(outbox=[], guard=candidate_guard, boundary_observer=boundary)
    runtime = _empty_runtime(host)
    generation = SessionExtensionGeneration(
        runtime,
        build_test_projection(runtime, {}, queue_mutex=session_mutex),
    )
    ref = SessionGenerationRef(generation, lock=session_mutex)

    with ref.publishing():
        assert state.active is None
        host.send_user_message("staged")
        phase_start = len(state.log)
        snapshot = host._seal_and_freeze(_lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN)
        assert not hasattr(snapshot, "activated")
        host._commit_activation(_lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN)
        assert state.active is None and flush_unlocked.is_set()
        ref.publish(generation)
        assert state.active is None
        assert state.log[phase_start:] == [
            "enter:candidate",
            "exit:candidate",
            "enter:candidate",
            "exit:candidate",
        ]

    publish_host = _host(guard=candidate_guard)
    publish_host._seal_and_freeze(_lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN)
    publish_host._commit_activation(_lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN)
    publish_candidate = _ExtensionCandidate(_empty_runtime(publish_host))
    with session_lock, pytest.raises(AssertionError, match="session -> candidate"):
        publish_candidate.publish()
    publish_cleanup = publish_candidate.dispose()
    assert publish_cleanup.disposed == 1
    assert publish_cleanup.anomaly_diagnostic is None

    dispose_host = _host(guard=candidate_guard)
    dispose_host._seal_and_freeze(_lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN)
    dispose_host._commit_activation(_lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN)
    dispose_candidate = _ExtensionCandidate(_empty_runtime(dispose_host))
    with session_lock:
        first_cleanup = dispose_candidate.dispose()
    assert first_cleanup.failed == 1
    assert first_cleanup.anomaly_diagnostic == (
        "pipy: extension candidate cleanup skipped "
        "0 published and 1 inaccessible activation host(s)."
    )
    retry_cleanup = dispose_candidate.dispose()
    assert retry_cleanup.disposed == 1
    assert retry_cleanup.anomaly_diagnostic is None

    abandoned = _host(guard=candidate_guard)

    async def failing_extension() -> None:
        assert state.active is None
        raise RuntimeError("activation failed")

    cleaned: list[bool] = []

    def cleanup() -> None:
        assert state.active is None
        abandoned._dispose()
        cleaned.append(True)

    with ref.publishing(), pytest.raises(RuntimeError):
        _run_awaitable(failing_extension(), abandon=cleanup)
    assert cleaned == [True]


def test_rejected_runtime_disposes_hosts_through_the_composition_seam() -> None:
    outbox: list = []
    custom_outbox: list = []
    host = _host(outbox=outbox, custom_outbox=custom_outbox)
    host.register_command("candidate", "candidate", lambda *_args: None)
    host._seal_and_freeze(_lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN)
    host._commit_activation(_lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN)
    runtime = _empty_runtime(host)

    _ExtensionCandidate(runtime).dispose()
    host.send_user_message("late")
    host.send_message({"customType": "late", "content": "late"})

    with pytest.raises(ExtensionCapabilityError):
        host.register_command("later", "later", lambda *_args: None)
    assert outbox == []
    assert custom_outbox == []


def test_activation_host_reader_inventory_has_one_meaning_per_field() -> None:
    """Pin every private host reader so the old sentinel cannot return."""

    def readers(path: Path, attribute: str) -> set[str]:
        syntax = ast.parse(path.read_text(encoding="utf-8"))
        found: set[str] = set()

        def visit(node: ast.AST, scope: str = "<module>") -> None:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                scope = node.name
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.ctx, ast.Load)
                and node.attr == attribute
            ):
                found.add(scope)
            for child in ast.iter_child_nodes(node):
                visit(child, scope)

        visit(syntax)
        return found

    runtime_path = Path(activation.__file__ or "")
    contracts_path = Path(extension_contracts.__file__ or "")
    hooks_path = Path(extension_hooks.__file__ or "")
    native_root = runtime_path.parents[1]
    assert not any(
        readers(path, "_activation_api") for path in native_root.rglob("*.py")
    )
    assert readers(contracts_path, "_pending_activation") == {
        "_activation_message_routings"
    }
    assert readers(runtime_path, "_pending_activation") == {
        "_dispose_activation_results",
        "_finalize_preloaded_extension",
    }
    assert readers(contracts_path, "_activation_host") == {
        "_activation_message_routings"
    }
    assert readers(runtime_path, "_activation_host") == {
        "_dispose_activation_results",
        "_finalize_provider_catalog_results",
    }
    assert readers(hooks_path, "_activation_host") == {"_compose_extension_bundle"}
    assert readers(contracts_path, "activation_hosts") == {"__post_init__"}
    assert readers(runtime_path, "activation_hosts") == {
        "adopt",
        "dispose",
        "publish",
    }


def test_candidate_lifetime_call_sites_are_exhaustive_across_native_package() -> None:
    runtime_path = Path(activation.__file__ or "")
    native_root = runtime_path.parents[1]
    constructions: set[tuple[str, str, int]] = set()
    candidate_calls: set[tuple[str, str, str]] = set()
    activation_calls: set[tuple[str, str, str, bool]] = set()

    for path in native_root.rglob("*.py"):
        syntax = ast.parse(path.read_text(encoding="utf-8"))
        definitions = [
            node
            for node in ast.walk(syntax)
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and node.end_lineno is not None
        ]

        def scope(call: ast.Call) -> str:
            containing = [
                node
                for node in definitions
                if node.lineno <= call.lineno <= cast(int, node.end_lineno)
            ]
            function = min(
                (
                    node
                    for node in containing
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                ),
                key=lambda node: cast(int, node.end_lineno) - node.lineno,
            )
            owner = min(
                (
                    node
                    for node in containing
                    if isinstance(node, ast.ClassDef)
                    and node.lineno <= function.lineno
                    and cast(int, function.end_lineno) <= cast(int, node.end_lineno)
                ),
                key=lambda node: cast(int, node.end_lineno) - node.lineno,
                default=None,
            )
            return f"{owner.name}.{function.name}" if owner else function.name

        relative = path.relative_to(native_root).as_posix()
        for call in (node for node in ast.walk(syntax) if isinstance(node, ast.Call)):
            if (
                isinstance(call.func, ast.Name)
                and call.func.id == "_ExtensionCandidate"
            ):
                constructions.add((relative, scope(call), len(call.args)))
            if (
                isinstance(call.func, ast.Attribute)
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id == "candidate"
                and call.func.attr in {"adopt", "publish", "dispose"}
            ):
                candidate_calls.add((relative, scope(call), call.func.attr))
            if isinstance(call.func, ast.Name) and call.func.id in {
                "_activate_workspace_extensions",
                "_compose_extension_bundle",
            }:
                activation_calls.add(
                    (
                        relative,
                        scope(call),
                        call.func.id,
                        any(keyword.arg == "diagnostic" for keyword in call.keywords),
                    )
                )

    assert constructions == {
        ("repl/reload.py", "ReloadCommandEffects.execute", 0),
        ("session_generation.py", "guarded", 0),
    }
    assert candidate_calls == {
        ("repl/reload.py", "ReloadCommandEffects.execute", "dispose"),
        (
            "repl/reload.py",
            "ReloadCommandEffects._activate_reload_candidate",
            "adopt",
        ),
        ("session_generation.py", "publish_candidate_ownership", "publish"),
        ("session_generation.py", "guarded", "dispose"),
        ("repl/wiring.py", "_prepare_startup", "adopt"),
    }
    assert activation_calls == {
        (
            "extension_hooks.py",
            "_activate_workspace_extensions",
            "_compose_extension_bundle",
            False,
        ),
        (
            "repl/reload.py",
            "ReloadCommandEffects._activate_reload_candidate",
            "_activate_workspace_extensions",
            True,
        ),
        (
            "repl/wiring.py",
            "_prepare_startup",
            "_activate_workspace_extensions",
            True,
        ),
    }


def test_activation_producer_and_cleanup_reporting_inventories() -> None:
    runtime_path = Path(activation.__file__ or "")
    package_root = runtime_path.parents[2]
    calls: set[tuple[str, str, str, bool]] = set()
    cleanup_reporters: set[tuple[str, str]] = set()
    catalog_finalizers: set[tuple[str, str]] = set()
    host_cleanup_delegates: set[tuple[str, str]] = set()

    for path in package_root.rglob("*.py"):
        syntax = ast.parse(path.read_text(encoding="utf-8"))
        functions = [
            node
            for node in ast.walk(syntax)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.end_lineno is not None
        ]
        relative = path.relative_to(package_root).as_posix()
        for call in (node for node in ast.walk(syntax) if isinstance(node, ast.Call)):
            if not isinstance(call.func, ast.Name):
                continue
            if call.func.id not in {
                "_dispose_activation_host_with_diagnostic",
                "_report_activation_cleanup",
                "_report_provider_catalog_finalization",
                "activate_extension_batch",
                "activate_extensions",
            }:
                continue
            containing = [
                function
                for function in functions
                if function.lineno <= call.lineno <= cast(int, function.end_lineno)
            ]
            function = min(
                containing,
                key=lambda node: cast(int, node.end_lineno) - node.lineno,
            )
            if call.func.id == "_report_activation_cleanup":
                cleanup_reporters.add((relative, function.name))
                continue
            if call.func.id == "_report_provider_catalog_finalization":
                catalog_finalizers.add((relative, function.name))
                continue
            if call.func.id == "_dispose_activation_host_with_diagnostic":
                host_cleanup_delegates.add((relative, function.name))
                continue
            calls.add(
                (
                    relative,
                    function.name,
                    call.func.id,
                    any(keyword.arg == "diagnostic" for keyword in call.keywords),
                )
            )

    assert calls == {
        (
            "cli.py",
            "_build_extension_activation_batch",
            "activate_extension_batch",
            True,
        ),
        (
            "native/extension_hooks.py",
            "_activate_workspace_extensions",
            "activate_extensions",
            True,
        ),
        (
            "native/extension_provider_catalog.py",
            "load_extension_provider_contributions",
            "activate_extensions",
            True,
        ),
        (
            "native/extensions/activation.py",
            "activate_extensions",
            "activate_extension_batch",
            True,
        ),
    }
    assert cleanup_reporters == {
        ("native/extension_hooks.py", "_activate_workspace_extensions"),
        (
            "native/extension_provider_catalog.py",
            "load_extension_provider_contributions",
        ),
        ("native/extensions/activation.py", "_dispose_activation_host_with_diagnostic"),
        ("native/extensions/activation.py", "activate_extension_batch"),
        ("native/extensions/activation.py", "adopt"),
        ("native/session_generation.py", "guarded"),
        ("native/repl/reload.py", "execute"),
    }
    assert catalog_finalizers == {
        (
            "native/extension_provider_catalog.py",
            "load_extension_provider_contributions",
        )
    }
    assert host_cleanup_delegates == {
        ("native/extensions/activation.py", "_activate_one"),
        ("native/extensions/activation.py", "_execute_activation_entry"),
        ("native/extensions/activation.py", "_finalize_preloaded_extension"),
    }


def test_provider_catalog_finalization_retains_factory_default_flag_reads(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    retained: list[_ActivationApi] = []
    monkeypatch.setattr(activation, "_r1_catalog_hosts", retained, raising=False)
    extension_dir = tmp_path / ".pipy" / "extensions"
    extension_dir.mkdir(parents=True)
    (extension_dir / "catalog.py").write_text(
        "from pipy_harness.extensions import ExtensionFlag, ExtensionProvider\n"
        "from pipy_harness.native.extensions import activation\n"
        "def activate(api):\n"
        "    activation._r1_catalog_hosts.append(api)\n"
        "    api.register_flag(ExtensionFlag('ready', 'boolean', default=True))\n"
        "    api.register_flag(ExtensionFlag('mode', 'string', default='default'))\n"
        "    api.register_provider(ExtensionProvider(name='catalog-provider',\n"
        "        default_model='model', models=('model',),\n"
        "        factory=lambda _ctx: (api.get_flag('ready'), api.get_flag('mode'))))\n"
        "    api.unregister_provider('builtin')\n"
        "    api.send_user_message('harvested')\n",
        encoding="utf-8",
    )
    diagnostics: list[str] = []

    providers, unregistered = (
        extension_provider_catalog.load_extension_provider_contributions(
            tmp_path,
            include_workspace_defaults=True,
            diagnostic=diagnostics.append,
        )
    )

    host = retained[0]
    assert diagnostics == []
    assert host._state == "catalog_finalized"
    assert host._reserved == frozenset()
    assert host._taken == frozenset()
    assert host._taken_flags == frozenset()
    assert host.get_flag("ready") is True
    assert host.get_flag("mode") == "default"
    assert [registered.provider.name for registered in providers] == [
        "catalog-provider"
    ]
    assert providers[0].provider.factory(None) == (True, "default")
    assert unregistered == ("builtin",)
    assert host._staged == {}
    assert host._staged_shortcuts == {}
    assert host._staged_tools == {}
    assert host._staged_providers == {}
    assert host._staged_unregistered == []
    assert host._staged_flags == {}
    assert host._staged_message_renderers == {}
    assert host._staged_entry_renderers == {}
    assert host._hooks == {}
    assert host._staged_messages == []
    assert host._staged_custom_messages == []
    assert host._frozen_activation is None

    host.send_user_message("late")
    host.send_user_message("later")
    host.send_message({"customType": "late", "content": "late"})
    assert host._outbox == []
    assert host._custom_outbox == []
    with pytest.raises(ExtensionCapabilityError):
        host.register_provider(_provider("late"))
    with pytest.raises(ExtensionCapabilityError):
        host.unregister_provider("late")
    candidate = _ExtensionCandidate(_empty_runtime(host))
    assert candidate.publish() is False
    assert candidate.dispose().disposed == 0
    assert host._dispose() is False
    assert providers[0].provider.factory(None) == (True, "default")

    rejected = _host()
    rejected.register_flag(ExtensionFlag("rejected", "boolean", default=True))
    assert rejected.get_flag("rejected") is True
    assert rejected._dispose() is True
    assert rejected.get_flag("rejected") is None


def test_provider_catalog_cleanup_anomaly_reaches_its_required_caller_sink(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    api = _host()
    api.register_provider(_provider("catalog-provider"))
    snapshot = api._seal_and_freeze(_lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN)
    api._commit_activation(_lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN)
    activated = ActivatedExtension(
        name="catalog",
        version="1",
        path_label="catalog.py",
        status="activated",
        reason=None,
        commands=(),
        diagnostic=None,
        providers=snapshot.providers,
        _activation_host=api,
    )
    original_guard = api._guard

    class _InaccessibleGuard(AbstractContextManager[object]):
        def __enter__(self) -> object:
            raise RuntimeError("inaccessible")

        def __exit__(self, *_args: object) -> None:
            return None

    api._guard = _InaccessibleGuard()
    monkeypatch.setattr(
        extension_provider_catalog,
        "discover_extensions",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        extension_provider_catalog,
        "activate_extensions",
        lambda *_args, **_kwargs: [activated],
    )
    diagnostics: list[str] = []

    providers, unregistered = (
        extension_provider_catalog.load_extension_provider_contributions(
            tmp_path,
            diagnostic=diagnostics.append,
        )
    )

    assert [registered.provider.name for registered in providers] == [
        "catalog-provider"
    ]
    assert unregistered == ()
    assert diagnostics == [
        "pipy: extension provider catalog finalization anomalies: "
        "0 refused host(s) disposed, 0 published host(s) skipped live, "
        "0 refused host(s) already terminal, and "
        "1 inaccessible/failing host guard(s)."
    ]
    api._guard = original_guard
    assert api._dispose() is True


def test_provider_catalog_refusal_disposes_nonlive_hosts_and_skips_live_hosts() -> None:
    committed = _host()
    committed._seal_and_freeze(_lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN)
    committed._commit_activation(_lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN)

    open_host = _host()
    corrupt_host = _host()
    corrupt_host._state = cast(Any, "corrupt")

    live_outbox: list[QueuedUserMessage] = []
    published = _host(outbox=live_outbox)
    published._seal_and_freeze(_lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN)
    published._accept_message_route()
    published._commit_activation(_lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN)
    assert _ExtensionCandidate(_empty_runtime(published)).publish() is True

    inaccessible = _host()
    inaccessible._seal_and_freeze(_lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN)
    inaccessible._commit_activation(_lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN)
    original_guard = inaccessible._guard

    class _InaccessibleGuard(AbstractContextManager[object]):
        def __enter__(self) -> object:
            raise RuntimeError("inaccessible")

        def __exit__(self, *_args: object) -> None:
            return None

    inaccessible._guard = _InaccessibleGuard()

    def result(name: str, host: _ActivationApi) -> ActivatedExtension:
        return ActivatedExtension(
            name=name,
            version="1",
            path_label=f"{name}.py",
            status="activated",
            reason=None,
            commands=(),
            diagnostic=None,
            _activation_host=host,
        )

    finalization = activation._finalize_provider_catalog_results(
        (
            result("committed", committed),
            result("open", open_host),
            result("corrupt", corrupt_host),
            result("published", published),
            result("inaccessible", inaccessible),
        )
    )

    assert finalization.finalized == 1
    assert finalization.refused_disposed == 2
    assert finalization.refused_published == 1
    assert finalization.refused_already_terminal == 0
    assert finalization.inaccessible == 1
    assert finalization.anomaly_diagnostic == (
        "pipy: extension provider catalog finalization anomalies: "
        "2 refused host(s) disposed, 1 published host(s) skipped live, "
        "0 refused host(s) already terminal, and "
        "1 inaccessible/failing host guard(s)."
    )
    assert committed._state == "catalog_finalized"
    assert open_host._state == "disposed"
    assert corrupt_host._state == "disposed"
    assert published._state == "published"
    published.send_user_message("still-live")
    assert [message.content for message in live_outbox] == ["still-live"]
    assert inaccessible._state == "committed"
    inaccessible._guard = original_guard
    assert inaccessible._dispose() is True


def test_omitted_pending_activation_is_abandoned_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    retained: list[_ActivationApi] = []
    monkeypatch.setattr(activation, "_r1_omitted_hosts", retained, raising=False)
    extension_dir = tmp_path / ".pipy" / "extensions"
    extension_dir.mkdir(parents=True)
    (extension_dir / "omitted.py").write_text(
        "from pipy_harness.native.extensions import activation\n"
        "def activate(api):\n"
        "    activation._r1_omitted_hosts.append(api)\n",
        encoding="utf-8",
    )
    descriptors = discover_extensions(tmp_path, include_workspace_defaults=True)
    pending = activate_extension_batch(descriptors, pending=True)
    host = retained[0]
    disposals: list[_ActivationApi] = []
    original_dispose_locked = _ActivationApi._dispose_locked

    def count_dispose(api: _ActivationApi) -> bool:
        if api is host:
            disposals.append(api)
        return original_dispose_locked(api)

    monkeypatch.setattr(_ActivationApi, "_dispose_locked", count_dispose)

    activate_extension_batch((), preloaded=pending)
    activate_extension_batch((), preloaded=pending)

    assert disposals == [host]
    assert host._state == "disposed"


def test_pending_abandonment_anomaly_reaches_the_finalization_sink(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    retained: list[_ActivationApi] = []
    monkeypatch.setattr(activation, "_r1_anomaly_hosts", retained, raising=False)
    extension_dir = tmp_path / ".pipy" / "extensions"
    extension_dir.mkdir(parents=True)
    (extension_dir / "anomaly.py").write_text(
        "from pipy_harness.native.extensions import activation\n"
        "def activate(api):\n"
        "    activation._r1_anomaly_hosts.append(api)\n",
        encoding="utf-8",
    )
    descriptors = discover_extensions(tmp_path, include_workspace_defaults=True)
    pending = activate_extension_batch(descriptors, pending=True)
    host = retained[0]
    original_guard = host._guard

    class _InaccessibleGuard(AbstractContextManager[object]):
        def __enter__(self) -> object:
            raise RuntimeError("inaccessible")

        def __exit__(self, *_args: object) -> None:
            return None

    host._guard = _InaccessibleGuard()
    diagnostics: list[str] = []

    activate_extension_batch((), preloaded=pending, diagnostic=diagnostics.append)

    assert diagnostics == [
        "pipy: extension candidate cleanup skipped "
        "0 published and 1 inaccessible activation host(s)."
    ]
    host._guard = original_guard
    activate_extension_batch((), preloaded=pending, diagnostic=diagnostics.append)
    assert host._state == "disposed"
    assert len(diagnostics) == 1


def test_preloaded_finalize_is_one_shot_and_live_host_cannot_be_disposed(
    tmp_path: Path,
) -> None:
    extension_dir = tmp_path / ".pipy" / "extensions"
    extension_dir.mkdir(parents=True)
    (extension_dir / "preloaded.py").write_text(
        "from pipy_harness.extensions import ExtensionFlag\n"
        "def activate(api):\n"
        "    api.register_flag(ExtensionFlag('ready', 'boolean', default=True))\n"
        "    api.send_user_message('staged')\n"
        "    api.send_message({'customType': 'proof', 'content': 'kept'})\n",
        encoding="utf-8",
    )
    descriptors = discover_extensions(tmp_path, include_workspace_defaults=True)

    pending = activate_extension_batch(descriptors, pending=True)
    final = activate_extension_batch(descriptors, preloaded=pending)
    activated = next(item for item in final.activated if item.name == "preloaded")
    host = activated._activation_host
    assert host is not None
    assert [message.content for message in final.message_outbox] == ["staged"]
    assert [message.content for message in activated.custom_messages] == ["kept"]

    repeated = activate_extension_batch(descriptors, preloaded=pending)
    repeated_item = next(
        item for item in repeated.activated if item.name == "preloaded"
    )
    assert repeated_item.status == "disabled"
    assert repeated_item.reason == "activation_error"
    assert [message.content for message in final.message_outbox] == ["staged"]
    assert [message.content for message in activated.custom_messages] == ["kept"]

    runtime = _empty_runtime(cast(_ActivationApi, host))
    assert _ExtensionCandidate(runtime).publish() is True
    cleanup = _ExtensionCandidate(runtime).dispose()
    assert cleanup.skipped_published == 1
    assert cleanup.disposed == 0
    assert host.get_flag("ready") is True
    host.send_user_message("live")
    assert [message.content for message in final.message_outbox] == [
        "staged",
        "live",
    ]
    with pytest.raises(ExtensionCapabilityError):
        host.register_command("late", "late", lambda *_args: None)


def test_candidate_lifetime_disposes_a_runtime_rejected_at_the_adoption_seam() -> None:
    owned_host = _host()
    owned_host._seal_and_freeze(_lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN)
    owned_host._commit_activation(_lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN)
    rejected_host = _host()
    rejected_host.register_flag(ExtensionFlag("rejected", "boolean", default=True))
    rejected_host._seal_and_freeze(_lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN)
    rejected_host._commit_activation(_lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN)
    lifetime = _ExtensionCandidate(_empty_runtime(owned_host))

    with pytest.raises(ExtensionCapabilityError):
        lifetime.adopt(_empty_runtime(rejected_host), pytest.fail)

    assert rejected_host.get_flag("rejected") is None
    assert owned_host._state == "committed"
    assert lifetime.dispose().disposed == 1


def test_candidate_publication_refuses_an_open_host_and_remains_disposable() -> None:
    committed = _host()
    committed.register_flag(ExtensionFlag("first", "boolean", default=True))
    committed._seal_and_freeze(_lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN)
    committed._commit_activation(_lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN)
    routing = committed._message_routing
    open_host = _host(
        outbox=routing.user_outbox,
        custom_outbox=routing.custom_outbox,
        message_routing=routing,
    )
    open_host.register_flag(ExtensionFlag("open", "boolean", default=True))

    candidate = _ExtensionCandidate(_empty_runtime(committed, open_host))
    assert candidate.publish() is False
    assert committed._state == "committed"
    assert open_host._state == "open"
    assert committed.get_flag("first") is True
    assert open_host.get_flag("open") is True

    cleanup = candidate.dispose()
    assert cleanup.disposed == 2
    assert cleanup.failed == 0
    assert committed.get_flag("first") is None
    assert open_host.get_flag("open") is None


def test_mixed_corrupted_candidate_disposes_unpublished_siblings_only() -> None:
    live_outbox: list = []
    published = _host(outbox=live_outbox)
    published._seal_and_freeze(_lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN)
    published._accept_message_route()
    published._commit_activation(_lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN)
    published_runtime = _empty_runtime(published)
    assert _ExtensionCandidate(published_runtime).publish() is True

    routing = published._message_routing
    sibling = _host(
        outbox=routing.user_outbox,
        custom_outbox=routing.custom_outbox,
        message_routing=routing,
    )
    sibling.register_flag(ExtensionFlag("sibling", "boolean", default=True))
    sibling._seal_and_freeze(_lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN)
    sibling._commit_activation(_lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN)
    corrupted = _ExtensionCandidate(_empty_runtime(published, sibling))

    cleanup = corrupted.dispose()
    assert cleanup.disposed == 1
    assert cleanup.skipped_published == 1
    assert cleanup.anomaly_diagnostic == (
        "pipy: extension candidate cleanup skipped "
        "1 published and 0 inaccessible activation host(s)."
    )

    assert published._state == "published"
    published.send_user_message("still-live")
    assert [message.content for message in live_outbox] == ["still-live"]
    assert sibling._state == "disposed"
    assert sibling.get_flag("sibling") is None
    with pytest.raises(ExtensionCapabilityError):
        sibling.register_command("late", "late", lambda *_args: None)


OWNERSHIP_FAILURE = "pipy: extension candidate ownership is unavailable"


@pytest.mark.parametrize(
    ("failure", "diagnostic"),
    (
        ("expected-owner-mismatch", "pipy: prepared reload owner state changed"),
        (
            "projectionless",
            "pipy: extension generation projection is unavailable",
        ),
        ("publish-returned-false", OWNERSHIP_FAILURE),
        ("publish-raised", OWNERSHIP_FAILURE),
    ),
)
def test_reload_acceptance_failure_keeps_previous_generation(
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    diagnostic: str,
) -> None:
    import pipy_harness.native.repl.reload as reload_module
    from pipy_harness.native.resource_loading import RuntimeResourceOptions

    live_host = _host()
    live_host._seal_and_freeze(_lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN)
    live_host._commit_activation(_lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN)
    live_runtime = _empty_runtime(live_host)
    assert _ExtensionCandidate(live_runtime).publish() is True
    lock = threading.RLock()
    live_generation = SessionExtensionGeneration(
        live_runtime,
        build_test_projection(live_runtime, {}, queue_mutex=lock),
    )
    ref = SessionGenerationRef(live_generation, lock=lock)

    candidate_host = _host()
    candidate_host._seal_and_freeze(_lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN)
    candidate_host._commit_activation(_lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN)
    rejected_runtime = _empty_runtime(candidate_host)
    monkeypatch.setattr(
        reload_module,
        "_activate_workspace_extensions",
        lambda *_args, **_kwargs: rejected_runtime,
    )
    diagnostics: list[str] = []
    ctl = SimpleNamespace(
        generation_ref=ref,
        extension_generation=live_generation,
        workspace_resources=object(),
    )
    capabilities, mutation, emitter, renderer = _reload_owners(ref)
    retained_renderers = {"old": object()}
    renderer._tool_renderers = retained_renderers
    before = ref.snapshot()
    effects = ReloadCommandEffects(
        implicit_trust=ImplicitTrustState(),
        provider_state=None,
        tool_registry={},
        verbose_startup=False,
        ctl=cast(Any, ctl),
        settings=cast(
            Any,
            SimpleNamespace(
                project_trusted=True,
                get_extensions_patterns=lambda: (),
            ),
        ),
        keybindings=cast(Any, None),
        terminal_ui=None,
        renderer=renderer,
        error_stream=cast(Any, None),
        emitter=emitter,
        provider_mutation=mutation,
        cwd=Path("."),
        resource_options=RuntimeResourceOptions(no_extensions=True),
        tool_capabilities=capabilities,
        diag=diagnostics.append,
        redraw_custom_entries_for_active_branch=lambda: None,
        extension_send_message=lambda *_args: None,
        extension_render_details=cast(Any, lambda *_args: None),
    )
    candidate = _ExtensionCandidate()
    newer_capability = None
    if failure == "expected-owner-mismatch":
        publish = _ExtensionCandidate.publish
        newer_capability = capabilities.prepare_extensions({})

        def publish_then_mutate_expected_owner(lifetime: _ExtensionCandidate) -> bool:
            published = publish(lifetime)
            with ref.lock:
                capabilities._state = newer_capability
            return published

        monkeypatch.setattr(
            _ExtensionCandidate, "publish", publish_then_mutate_expected_owner
        )
    elif failure == "projectionless":
        monkeypatch.setattr(
            reload_module,
            "SessionExtensionGeneration",
            lambda runtime, _projection, chrome: SessionExtensionGeneration(
                runtime, None, chrome
            ),
        )
        monkeypatch.setattr(
            _ExtensionCandidate,
            "publish",
            lambda _candidate: pytest.fail("projectionless candidate was published"),
        )
    elif failure == "publish-returned-false":
        monkeypatch.setattr(_ExtensionCandidate, "publish", lambda _candidate: False)
    else:

        def raise_publish(_candidate: _ExtensionCandidate) -> bool:
            raise RuntimeError("injected candidate publication failure")

        monkeypatch.setattr(_ExtensionCandidate, "publish", raise_publish)
    effects._reload_extension_generation(candidate)

    assert diagnostics == [diagnostic, "pipy: keeping the previous extensions."]
    assert ctl.extension_generation is live_generation
    assert ref.current is before.generation and ref.snapshot().generation_id == 0
    assert renderer._tool_renderers is retained_renderers
    if failure == "expected-owner-mismatch":
        assert capabilities._state is newer_capability
        assert candidate_host._state == "published"
        assert rejected_runtime.message_routing._state == "retired"
        candidate_host.send_user_message("published-but-unowned-drop")
        assert rejected_runtime.outbox == []
        assert candidate.dispose() == _ActivationCleanup()
    else:
        assert candidate_host._state == "committed"
        assert candidate.dispose().disposed == 1
        assert candidate_host._state == "disposed"
    assert live_host.get_flag("missing") is None


def test_reload_failure_before_semantic_commit_disposes_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pipy_harness.native.repl.reload as reload_module
    from pipy_harness.native.resource_loading import RuntimeResourceOptions

    host = _host()
    host._seal_and_freeze(_lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN)
    host._commit_activation(_lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN)
    runtime = _empty_runtime(host)
    monkeypatch.setattr(
        reload_module,
        "_activate_workspace_extensions",
        lambda *_args, **_kwargs: runtime,
    )
    monkeypatch.setattr(
        reload_module,
        "SessionExtensionGeneration",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("build failed")),
    )
    live = SessionExtensionGeneration(_empty_runtime(_host()))
    ref = SessionGenerationRef(live)
    capabilities, mutation, emitter, renderer = _reload_owners(ref)
    effects = ReloadCommandEffects(
        implicit_trust=ImplicitTrustState(),
        provider_state=None,
        tool_registry={},
        verbose_startup=False,
        ctl=cast(
            Any,
            SimpleNamespace(
                generation_ref=ref,
                extension_generation=live,
                workspace_resources=object(),
            ),
        ),
        settings=cast(
            Any,
            SimpleNamespace(project_trusted=True, get_extensions_patterns=lambda: ()),
        ),
        keybindings=cast(Any, None),
        terminal_ui=None,
        renderer=renderer,
        error_stream=cast(Any, None),
        emitter=emitter,
        provider_mutation=mutation,
        cwd=Path("."),
        resource_options=RuntimeResourceOptions(no_extensions=True),
        tool_capabilities=capabilities,
        diag=lambda _message: None,
        redraw_custom_entries_for_active_branch=lambda: None,
        extension_send_message=lambda *_args: None,
        extension_render_details=cast(Any, lambda *_args: None),
    )
    candidate = _ExtensionCandidate()

    with pytest.raises(RuntimeError, match="build failed"):
        effects._reload_extension_generation(candidate)

    assert ref.current is live
    assert candidate.dispose().disposed == 1
    assert host._state == "disposed"


def test_reload_interrupt_releases_route_and_preserves_base_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pipy_harness.native.repl.reload as reload_module
    from pipy_harness.native.resource_loading import RuntimeResourceOptions

    live = SessionExtensionGeneration(_empty_runtime(_host()))
    ref = SessionGenerationRef(live)

    class _Ctl:
        workspace_resources = object()

        @property
        def generation_ref(self) -> SessionGenerationRef:
            return ref

        @property
        def extension_generation(self) -> SessionExtensionGeneration:
            return ref.current

        @extension_generation.setter
        def extension_generation(self, generation: SessionExtensionGeneration) -> None:
            ref.publish(generation)

    host = _host()
    host._seal_and_freeze(_lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN)
    host._commit_activation(_lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN)
    runtime = replace(
        _empty_runtime(host),
        custom_messages=(QueuedCustomMessage("interrupt", "now", True, None, {}),),
    )
    monkeypatch.setattr(
        reload_module,
        "_activate_workspace_extensions",
        lambda *_args, **_kwargs: runtime,
    )

    capabilities, mutation, emitter, renderer = _reload_owners(ref)

    def interrupt_message(*_args: object) -> None:
        raise KeyboardInterrupt("post-commit delivery interrupted")

    effects = ReloadCommandEffects(
        implicit_trust=ImplicitTrustState(),
        provider_state=None,
        tool_registry={},
        verbose_startup=False,
        ctl=cast(Any, _Ctl()),
        settings=cast(
            Any,
            SimpleNamespace(project_trusted=True, get_extensions_patterns=lambda: ()),
        ),
        keybindings=cast(Any, None),
        terminal_ui=None,
        renderer=renderer,
        error_stream=cast(Any, None),
        emitter=emitter,
        provider_mutation=mutation,
        cwd=Path("."),
        resource_options=RuntimeResourceOptions(no_extensions=True),
        tool_capabilities=capabilities,
        diag=lambda _message: None,
        redraw_custom_entries_for_active_branch=lambda: None,
        extension_send_message=interrupt_message,
        extension_render_details=cast(Any, lambda *_args: None),
    )
    candidate = _ExtensionCandidate()

    with pytest.raises(KeyboardInterrupt, match="post-commit delivery interrupted"):
        effects._reload_extension_generation(candidate)

    installed = ref.current
    assert installed.runtime is runtime and installed.projection is not None
    routing = runtime.message_routing
    assert routing._state == "live" and routing._pending is None
    assert host._state == "published"
    assert candidate.dispose().disposed == 0


def test_reload_host_transfer_and_retired_slot_layout_are_static() -> None:
    path = Path(__file__).parents[1] / "src/pipy_harness/native/session_generation.py"
    syntax = ast.parse(path.read_text(encoding="utf-8"))
    owner = next(
        node
        for node in syntax.body
        if isinstance(node, ast.ClassDef) and node.name == "SessionGenerationRef"
    )
    commit = next(
        node
        for node in owner.body
        if isinstance(node, ast.FunctionDef) and node.name == "accept_prepared_reload"
    )
    host_publications = [
        node
        for node in ast.walk(commit)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "publish_candidate_ownership"
    ]
    generation_installations = [
        node
        for node in ast.walk(commit)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "publish_locked"
    ]
    assert len(host_publications) == len(generation_installations) == 1
    source = ast.unparse(commit)
    session_sections = [
        node
        for node in ast.walk(commit)
        if isinstance(node, ast.With)
        and ast.unparse(node.items[0].context_expr) == "self._lock"
    ]
    assert len(session_sections) == 1
    section = session_sections[0]
    assert host_publications[0].lineno < section.lineno
    assert source.index("if generation.projection is None:") < source.index(
        "publish_candidate_ownership(candidate)"
    )
    assert "if not matches:" in source
    assert "return ('prepared reload owner state changed', None)" in source
    assert source.index("if not matches:") < source.index(
        "retired_generation = self.publish_locked(generation)"
    )
    assert "_boundary_observer" not in source
    section_source = ast.unparse(section)
    assert "mark_route_retired_locked" not in section_source
    assert section_source.count("owner.mark_retired_locked(route_retirement)") == 1
    assert "finalize_retirement" not in section_source
    assert source.count("route_retirement.finalize_retirement()") == 1
    assert "retired: list[object | None] = [None] * 17" in source
    assert source.count("retired[") == 17
    retained_inventory = (
        "retired[0] = retired_generation",
        "retired[1] = route_retirement.finalize_retirement()",
        "retired[2] = catalog.extension_providers",
        "retired[3] = catalog.extension_unregistered_providers",
        "retired[4] = catalog._extension_provider_map",
        "retired[5] = catalog.extension_oauth_provider_map",
        "retired[6] = catalog.catalog.rows",
        "retired[7] = catalog.catalog.error",
        "retired[8] = catalog.catalog.provider_request_configs",
        "retired[9] = catalog.catalog._config",
        "retired[10] = auth_store._data",
        "retired[11] = coding_state._binding",
        "retired[12] = coding_state._messages",
        "retired[13] = coding_state._usage_accumulator",
        "retired[14] = provider_state.selection",
        "retired[15] = provider_state.pending_default",
        "retired[16] = tool_capabilities._state",
    )
    assert all(source.count(item) == 1 for item in retained_inventory)
    assert all(
        item in section_source
        for item in retained_inventory
        if item != retained_inventory[1]
    )
    assert "retired_chrome.close()" not in source
    finalizer = next(
        node for node in commit.body if ast.unparse(node) == retained_inventory[1]
    )
    release = next(node for node in commit.body if ast.unparse(node) == "del retired")
    assert section.end_lineno is not None
    assert finalizer.end_lineno is not None
    assert (
        section.end_lineno < finalizer.lineno <= finalizer.end_lineno < release.lineno
    )


def test_startup_installs_generation_reference_before_host_publication() -> None:
    path = Path(__file__).parents[1] / "src/pipy_harness/native/repl/wiring.py"
    syntax = ast.parse(path.read_text(encoding="utf-8"))
    run = next(
        node
        for node in syntax.body
        if isinstance(node, ast.FunctionDef) and node.name == "_attach_extensions"
    )
    assignments = [
        node
        for node in ast.walk(run)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "generation_ref"
            for target in node.targets
        )
    ]
    publishes = [
        node
        for node in ast.walk(run)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "publish_candidate_ownership"
    ]

    assert len(assignments) == 1
    assert len(publishes) == 1
    assert assignments[0].lineno < publishes[0].lineno


def test_disposed_preload_finalize_is_bounded_and_commit_is_one_way(
    tmp_path: Path,
) -> None:
    extension_dir = tmp_path / ".pipy" / "extensions"
    extension_dir.mkdir(parents=True)
    (extension_dir / "disposed.py").write_text(
        "def activate(api):\n    api.send_user_message('drop-me')\n",
        encoding="utf-8",
    )
    descriptors = discover_extensions(tmp_path, include_workspace_defaults=True)
    pending = activate_extension_batch(descriptors, pending=True)
    activated = next(item for item in pending.activated if item.name == "disposed")
    token = activated._pending_activation
    assert token is not None and token._host is not None
    token._host._dispose()

    final = activate_extension_batch(descriptors, preloaded=pending)
    finalized = next(item for item in final.activated if item.name == "disposed")
    assert finalized.status == "disabled"
    assert finalized.reason == "activation_error"
    assert final.message_outbox == []

    api = _host()
    api._seal_and_freeze(_lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN)
    assert api._dispose() is True
    with pytest.raises(ExtensionCapabilityError):
        api._commit_activation(_lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN)


def test_preloaded_finalization_exception_disables_only_owner_and_disposes_host(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    retained: list[_ActivationApi] = []
    monkeypatch.setattr(activation, "_r1_retained", retained, raising=False)
    extension_dir = tmp_path / ".pipy" / "extensions"
    extension_dir.mkdir(parents=True)
    (extension_dir / "exceptional.py").write_text(
        "from pipy_harness.native.extensions import activation\n"
        "from pipy_harness.extensions import ExtensionFlag\n"
        "def activate(api):\n"
        "    activation._r1_retained.append(api)\n"
        "    api.register_flag(ExtensionFlag('owned', 'boolean', default=True))\n"
        "    api.send_user_message('must-not-leak')\n",
        encoding="utf-8",
    )
    descriptors = discover_extensions(tmp_path, include_workspace_defaults=True)
    pending = activate_extension_batch(descriptors, pending=True)
    monkeypatch.setattr(
        activation,
        "replace",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("replace failed")),
    )

    final = activate_extension_batch(descriptors, preloaded=pending)

    finalized = next(item for item in final.activated if item.name == "exceptional")
    assert finalized.status == "disabled"
    assert finalized.reason == "activation_error"
    assert finalized.diagnostic == "RuntimeError"
    assert final.message_outbox == []
    assert len(retained) == 1
    host = retained[0]
    assert host.get_flag("owned") is None
    with pytest.raises(ExtensionCapabilityError):
        host.register_command("late", "late", lambda *_args: None)


def test_preloaded_name_failure_has_no_outbox_or_reservation_effect(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    retained: list[_ActivationApi] = []
    monkeypatch.setattr(activation, "_r1_name_hosts", retained, raising=False)
    extension_dir = tmp_path / ".pipy" / "extensions"
    extension_dir.mkdir(parents=True)
    source = (
        "from pipy_harness.native.extensions import activation\n"
        "def activate(api):\n"
        "    activation._r1_name_hosts.append(api)\n"
        "    api.register_command('shared', 'shared', lambda *_args: None)\n"
    )
    (extension_dir / "a_hostile.py").write_text(
        source + "    api.send_user_message('must-not-leak')\n",
        encoding="utf-8",
    )
    (extension_dir / "b_good.py").write_text(source, encoding="utf-8")
    descriptors = discover_extensions(tmp_path, include_workspace_defaults=True)
    pending = activate_extension_batch(descriptors, pending=True)

    class _HostileName:
        def __hash__(self) -> int:
            raise AssertionError("hostile name must be rejected before hashing")

        def __eq__(self, _other: object) -> bool:
            raise AssertionError("hostile name must be rejected before equality")

    first = next(item for item in pending.activated if item.name == "a_hostile")
    object.__setattr__(first.commands[0], "name", _HostileName())

    final = activate_extension_batch(descriptors, preloaded=pending)
    by_name = {item.name: item for item in final.activated}

    assert by_name["a_hostile"].status == "disabled"
    assert by_name["a_hostile"].diagnostic == "TypeError"
    assert by_name["b_good"].status == "activated"
    assert [command.name for command in by_name["b_good"].commands] == ["shared"]
    assert final.message_outbox == []
    assert retained[0]._state == "disposed"


def test_reload_exception_disposes_candidate_only_after_the_gate_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live_host = _host()
    ref = SessionGenerationRef(SessionExtensionGeneration(_empty_runtime(live_host)))
    candidate_host = _host()
    candidate_host.register_flag(ExtensionFlag("candidate", "boolean", default=True))
    candidate_host._seal_and_freeze(_lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN)
    candidate_host._commit_activation(_lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN)
    candidate_runtime = _empty_runtime(candidate_host)
    disposal_gate_states: list[bool] = []
    original_dispose_locked = _ActivationApi._dispose_locked

    def observe_dispose_locked(host: _ActivationApi) -> bool:
        disposal_gate_states.append(ref.publication_pending)
        return original_dispose_locked(host)

    def raise_after_activation(
        _self: ReloadCommandEffects,
        lifetime: _ExtensionCandidate,
    ) -> None:
        lifetime.adopt(candidate_runtime, pytest.fail)
        raise RuntimeError("reload preparation failed")

    monkeypatch.setattr(_ActivationApi, "_dispose_locked", observe_dispose_locked)
    monkeypatch.setattr(
        ReloadCommandEffects,
        "_reload_configuration_and_resources",
        lambda _self: None,
    )
    monkeypatch.setattr(
        ReloadCommandEffects,
        "_reload_extension_generation",
        raise_after_activation,
    )
    effects = ReloadCommandEffects(
        implicit_trust=ImplicitTrustState(),
        provider_state=None,
        tool_registry={},
        verbose_startup=False,
        ctl=cast(Any, SimpleNamespace(generation_ref=ref)),
        settings=cast(Any, None),
        keybindings=cast(Any, None),
        terminal_ui=None,
        renderer=cast(Any, None),
        error_stream=cast(Any, None),
        emitter=cast(Any, None),
        provider_mutation=cast(Any, None),
        cwd=Path("."),
        resource_options=cast(Any, None),
        tool_capabilities=cast(Any, None),
        diag=lambda _message: None,
        redraw_custom_entries_for_active_branch=lambda: None,
        extension_send_message=lambda *_args: None,
        extension_render_details=cast(Any, lambda *_args: None),
    )
    outcome = CodingCommandOutcome(
        CodingCommandOutcomeKind.CONTINUE,
        CodingCommandAction.RELOAD,
        CodingCommandFooterPolicy.STANDARD,
    )

    with pytest.raises(RuntimeError, match="reload preparation failed"):
        effects.execute(outcome)

    assert disposal_gate_states == [False]
    assert ref.publication_pending is False
    assert candidate_host.get_flag("candidate") is None
    assert live_host._dispose() is True


def test_composition_exception_disposes_every_unpublished_host(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    outbox: list = []
    api = _host(outbox=outbox)
    api.register_flag(ExtensionFlag("candidate", "boolean", default=True))
    api._seal_and_freeze(_lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN)
    api._commit_activation(_lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN)
    activated = ActivatedExtension(
        name="candidate",
        version="1",
        path_label="candidate.py",
        status="activated",
        reason=None,
        commands=(),
        diagnostic=None,
        flags=(),
        _activation_key="candidate",
        _activation_host=api,
    )
    monkeypatch.setattr(extension_hooks, "discover_extensions", lambda *_a, **_k: [])
    monkeypatch.setattr(
        extension_hooks,
        "activate_extensions",
        lambda *_a, **_k: [activated],
    )
    monkeypatch.setattr(
        extension_hooks,
        "extension_command_map",
        lambda _activated: (_ for _ in ()).throw(RuntimeError("compose failed")),
    )
    resources = SimpleNamespace(custom_command_slash_names=lambda: ())

    with pytest.raises(RuntimeError, match="compose failed"):
        extension_hooks._activate_workspace_extensions(tmp_path, cast(Any, resources))

    assert api.get_flag("candidate") is None
    api.send_user_message("dropped")
    assert outbox == []
    with pytest.raises(ExtensionCapabilityError):
        api.register_command("late", "late", lambda *_args: None)


def test_pending_workspace_batch_failure_disposes_every_host_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    retained: list[_ActivationApi] = []
    monkeypatch.setattr(activation, "_r1_pending_hosts", retained, raising=False)
    extension_dir = tmp_path / ".pipy" / "extensions"
    extension_dir.mkdir(parents=True)
    for name in ("first", "second"):
        (extension_dir / f"{name}.py").write_text(
            "from pipy_harness.native.extensions import activation\n"
            "def activate(api):\n"
            "    activation._r1_pending_hosts.append(api)\n",
            encoding="utf-8",
        )
    pending = activate_extension_batch(
        discover_extensions(tmp_path, include_workspace_defaults=True),
        pending=True,
    )
    reports: list[tuple[_ActivationCleanup, Callable[[str], None] | None]] = []
    report = activation._report_activation_cleanup

    def record_report(
        cleanup: _ActivationCleanup,
        diagnostic: Callable[[str], None] | None,
    ) -> None:
        reports.append((cleanup, diagnostic))
        report(cleanup, diagnostic)

    monkeypatch.setattr(extension_hooks, "_report_activation_cleanup", record_report)
    resources = SimpleNamespace(custom_command_slash_names=lambda: ())
    diagnostics: list[str] = []

    for _attempt in range(2):
        with pytest.raises(
            ValueError, match="initial extension activation batch must be finalized"
        ):
            extension_hooks._activate_workspace_extensions(
                tmp_path,
                cast(Any, resources),
                activation_batch=pending,
                diagnostic=diagnostics.append,
            )

    assert len(retained) == 2
    assert all(host._state == "disposed" for host in retained)
    assert [cleanup.disposed for cleanup, _sink in reports] == [2, 0]
    assert all(sink == diagnostics.append for _cleanup, sink in reports)
    assert diagnostics == []


def test_composition_reports_recurring_cleanup_anomalies_through_its_sink(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    api = _host()
    api._seal_and_freeze(_lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN)
    api._commit_activation(_lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN)

    class _InaccessibleGuard(AbstractContextManager[object]):
        def __enter__(self) -> object:
            raise RuntimeError("inaccessible")

        def __exit__(self, *_args: object) -> None:
            return None

    api._guard = _InaccessibleGuard()
    activated = ActivatedExtension(
        name="candidate",
        version="1",
        path_label="candidate.py",
        status="activated",
        reason=None,
        commands=(),
        diagnostic=None,
        _activation_host=api,
    )
    monkeypatch.setattr(extension_hooks, "discover_extensions", lambda *_a, **_k: [])
    monkeypatch.setattr(
        extension_hooks,
        "activate_extensions",
        lambda *_a, **_k: [activated],
    )
    monkeypatch.setattr(
        extension_hooks,
        "extension_command_map",
        lambda _activated: (_ for _ in ()).throw(RuntimeError("compose failed")),
    )
    resources = SimpleNamespace(custom_command_slash_names=lambda: ())
    diagnostics: list[str] = []

    for _attempt in range(2):
        with pytest.raises(RuntimeError, match="compose failed"):
            extension_hooks._activate_workspace_extensions(
                tmp_path,
                cast(Any, resources),
                diagnostic=diagnostics.append,
            )

    assert diagnostics == [
        "pipy: extension candidate cleanup skipped "
        "0 published and 1 inaccessible activation host(s).",
        "pipy: extension candidate cleanup skipped "
        "0 published and 1 inaccessible activation host(s).",
    ]
