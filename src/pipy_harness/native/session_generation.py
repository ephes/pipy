"""Session-owned extension generations and detached candidate projections.

The live :class:`SessionExtensionGeneration` remains the runtime-plus-flags
value consumed by production.  R3a adds a separate immutable construction value
for later transactional reload slices; no startup or reload path installs or
reads that projection yet.

See ``docs/specs/2026-07-25-transactional-extension-reload-rebuild.md`` for the
concurrency contract.
"""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Generic, NewType, TypeVar

if TYPE_CHECKING:
    from pipy_harness.native.catalog_state import ProviderCatalogReloadState
    from pipy_harness.native.coding.state import (
        CodingReloadBindingValue,
        CodingReloadHistoryValue,
    )
    from pipy_harness.native.repl_state import (
        ReplPendingDefaultReloadValue,
        ReplSelectionReloadValue,
    )
    from pipy_harness.native.tui import ExtensionChromePrepareInput

from pipy_harness.native.extension_chrome_state import ExtensionChromeSink
from pipy_harness.native.extension_runtime import (
    HookHandler,
    RegisteredCommand,
    RegisteredEntryRenderer,
    RegisteredMessageRenderer,
    RegisteredShortcut,
    RegisteredTool,
    _ExtensionRuntime,
)
from pipy_harness.native.extension_types import (
    ExtensionTool,
    QueuedCustomMessage,
    QueuedUserMessage,
    RegisteredFlag,
    RegisteredProvider,
)
from pipy_harness.native.tool_capabilities import ToolCapabilityState
from pipy_harness.native.tools import ToolPort


ProjectionStepObserver = Callable[[str], None]
ReloadPreparationObserver = Callable[[str], None]
ToolPortBuilder = Callable[[RegisteredTool, Mapping[str, object]], ToolPort]
ToolCapabilityBuilder = Callable[[Mapping[str, ToolPort]], ToolCapabilityState]

PROJECTION_BUILD_STEPS = (
    "runtime_flags",
    "commands_menu_shortcuts",
    "lifecycle_request_hooks",
    "tool_ports_capability",
    "renderer_mappings",
    "provider_contributions",
    "queue_handles",
    "chrome_handle",
)

PREPARED_RELOAD_BUILD_STEPS = (
    "activation_inputs",
    "projection",
    "provider_catalog",
    "provider_factory",
    "provider_refresh",
    "provider_fallback",
    "coding_binding",
    "coding_history",
    "coding_usage",
    "coding_compaction",
    "unavailable_default",
    "capability",
    "temporary_legacy",
    "presentation_persistence",
    "chrome_prepare_input",
)

_T = TypeVar("_T")
_K = TypeVar("_K")
_V = TypeVar("_V")
_RLOCK_TYPE = type(threading.RLock())


@dataclass(frozen=True, slots=True)
class GenerationQueueHandle(Generic[_T]):
    """Unconsumed R3a handle for one candidate generation outbox.

    The list identity intentionally remains shared with the candidate runtime so
    R4a can move append/drain/close atomically without changing delivery
    semantics.  R3a defines no queue operation and no production caller reaches
    this handle.
    """

    storage: list[_T]
    mutex: threading.RLock

    def __post_init__(self) -> None:
        if not isinstance(self.storage, list):
            raise TypeError("generation queue storage must be a list")
        if not isinstance(self.mutex, _RLOCK_TYPE):
            raise TypeError("generation queue mutex must be an RLock")


@dataclass(frozen=True, slots=True)
class ExtensionRuntimeFlagProjection:
    flags: tuple[RegisteredFlag, ...]
    values: Mapping[str, object]
    custom_messages: tuple[QueuedCustomMessage, ...]


@dataclass(frozen=True, slots=True)
class ExtensionCommandProjection:
    commands: Mapping[str, RegisteredCommand]
    menu_names: tuple[str, ...]
    descriptions: Mapping[str, str]
    shortcuts: Mapping[str, RegisteredShortcut]


@dataclass(frozen=True, slots=True)
class ExtensionHookProjection:
    tool_call: tuple[HookHandler, ...]
    lifecycle: Mapping[str, tuple[HookHandler, ...]]
    input: tuple[HookHandler, ...]
    before_agent_start: tuple[HookHandler, ...]
    tool_result: tuple[HookHandler, ...]
    user_bash: tuple[HookHandler, ...]
    before_provider_headers: tuple[HookHandler, ...]
    before_provider_request: tuple[HookHandler, ...]
    session_before_switch: tuple[HookHandler, ...]
    session_before_fork: tuple[HookHandler, ...]
    session_before_compact: tuple[HookHandler, ...]
    session_before_tree: tuple[HookHandler, ...]


@dataclass(frozen=True, slots=True)
class ExtensionToolProjection:
    registered: tuple[RegisteredTool, ...]
    ports: Mapping[str, ToolPort]
    capability_state: ToolCapabilityState


@dataclass(frozen=True, slots=True)
class ExtensionRendererProjection:
    tools: Mapping[str, ExtensionTool]
    messages: Mapping[str, RegisteredMessageRenderer]
    entries: Mapping[str, RegisteredEntryRenderer]


@dataclass(frozen=True, slots=True)
class ExtensionProviderProjection:
    providers: tuple[RegisteredProvider, ...]
    unregistered: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ExtensionQueueProjection:
    user: GenerationQueueHandle[QueuedUserMessage]
    custom: GenerationQueueHandle[QueuedCustomMessage]


@dataclass(frozen=True, slots=True)
class ExtensionChromeHandle:
    """The exact candidate-owned R2 retained-chrome sidecar handle."""

    sink: ExtensionChromeSink

    def __post_init__(self) -> None:
        if not isinstance(self.sink, ExtensionChromeSink):
            raise TypeError("chrome sink must be an ExtensionChromeSink")


@dataclass(frozen=True, slots=True)
class ExtensionProjection:
    """Detached contribution value; R1 activation ownership stays external."""

    runtime_flags: ExtensionRuntimeFlagProjection
    commands: ExtensionCommandProjection
    hooks: ExtensionHookProjection
    tools: ExtensionToolProjection
    renderers: ExtensionRendererProjection
    providers: ExtensionProviderProjection
    queues: ExtensionQueueProjection
    chrome: ExtensionChromeHandle | None


def _freeze_mapping(value: Mapping[_K, _V]) -> Mapping[_K, _V]:
    return MappingProxyType(dict(value))


def _freeze_tuple(value: Iterable[_T]) -> tuple[_T, ...]:
    return tuple(item for item in value)


def _freeze_custom_message(message: QueuedCustomMessage) -> QueuedCustomMessage:
    """Copy/freeze the options mapping without transforming opaque payloads.

    Nested option values and ``details`` retain their established shallow,
    caller-defined semantics; R3a does not recursively freeze arbitrary objects.
    """

    return replace(message, options=_freeze_mapping(message.options))


def _validate_projection_inputs(
    runtime: _ExtensionRuntime,
    flag_values: Mapping[str, object],
    *,
    queue_mutex: threading.RLock,
    reference_mutex: threading.RLock,
    build_tool_port: ToolPortBuilder,
    build_tool_capability: ToolCapabilityBuilder,
    chrome: ExtensionChromeHandle | None,
    step_observer: ProjectionStepObserver | None,
) -> None:
    if not isinstance(runtime, _ExtensionRuntime):
        raise TypeError("runtime must be an _ExtensionRuntime")
    if not isinstance(flag_values, Mapping) or not all(
        isinstance(name, str) for name in flag_values
    ):
        raise TypeError("flag_values must be a string-keyed mapping")
    if not isinstance(queue_mutex, _RLOCK_TYPE) or not isinstance(
        reference_mutex, _RLOCK_TYPE
    ):
        raise TypeError("projection queue/reference mutexes must be RLocks")
    if queue_mutex is not reference_mutex:
        raise ValueError("projection queues and reference must share one mutex")
    if not callable(build_tool_port):
        raise TypeError("build_tool_port must be callable")
    if not callable(build_tool_capability):
        raise TypeError("build_tool_capability must be callable")
    if chrome is not None and not isinstance(chrome, ExtensionChromeHandle):
        raise TypeError("chrome must be an ExtensionChromeHandle or None")
    if step_observer is not None and not callable(step_observer):
        raise TypeError("step_observer must be callable or None")


def _validate_capability_projection(
    ports: Mapping[str, ToolPort], capability_state: ToolCapabilityState
) -> None:
    if not isinstance(capability_state, ToolCapabilityState):
        raise TypeError("build_tool_capability must return ToolCapabilityState")
    capability_ports = capability_state.extension_registry
    if tuple(capability_ports) != tuple(ports) or any(
        capability_ports[name] is not port for name, port in ports.items()
    ):
        raise ValueError("candidate capability state must contain projected ports")


def build_extension_projection(
    runtime: _ExtensionRuntime,
    flag_values: Mapping[str, object],
    *,
    queue_mutex: threading.RLock,
    reference_mutex: threading.RLock,
    build_tool_port: ToolPortBuilder,
    build_tool_capability: ToolCapabilityBuilder,
    chrome: ExtensionChromeHandle | None,
    step_observer: ProjectionStepObserver | None = None,
) -> ExtensionProjection:
    """Build and validate every detached projection family.

    The observer is a deterministic unit-test seam.  A raised exception from an
    observer or adapter prevents a value from being returned; this function has
    no live reference, activation-host transfer, publication, or effect port to
    mutate.  ``queue_mutex`` and ``reference_mutex`` remain separate inputs even
    though the value stores only the former: validating their identity before
    construction explicitly proves the future R3c ownership contract.
    """

    _validate_projection_inputs(
        runtime,
        flag_values,
        queue_mutex=queue_mutex,
        reference_mutex=reference_mutex,
        build_tool_port=build_tool_port,
        build_tool_capability=build_tool_capability,
        chrome=chrome,
        step_observer=step_observer,
    )

    def step(name: str) -> None:
        if step_observer is not None:
            step_observer(name)

    step("runtime_flags")
    values = _freeze_mapping(flag_values)
    runtime_flags = ExtensionRuntimeFlagProjection(
        flags=_freeze_tuple(runtime.flags),
        values=values,
        custom_messages=tuple(
            _freeze_custom_message(message) for message in runtime.custom_messages
        ),
    )

    step("commands_menu_shortcuts")
    commands = ExtensionCommandProjection(
        commands=_freeze_mapping(runtime.commands),
        menu_names=_freeze_tuple(runtime.menu_names),
        descriptions=_freeze_mapping(runtime.descriptions),
        shortcuts=_freeze_mapping(runtime.shortcuts),
    )

    step("lifecycle_request_hooks")
    hooks = ExtensionHookProjection(
        tool_call=_freeze_tuple(runtime.tool_call_hooks),
        lifecycle=_freeze_mapping(
            {
                name: _freeze_tuple(handlers)
                for name, handlers in runtime.lifecycle_hooks.items()
            }
        ),
        input=_freeze_tuple(runtime.input_hooks),
        before_agent_start=_freeze_tuple(runtime.before_agent_start_hooks),
        tool_result=_freeze_tuple(runtime.tool_result_hooks),
        user_bash=_freeze_tuple(runtime.user_bash_hooks),
        before_provider_headers=_freeze_tuple(runtime.before_provider_headers_hooks),
        before_provider_request=_freeze_tuple(runtime.before_provider_request_hooks),
        session_before_switch=_freeze_tuple(runtime.session_before_switch_hooks),
        session_before_fork=_freeze_tuple(runtime.session_before_fork_hooks),
        session_before_compact=_freeze_tuple(runtime.session_before_compact_hooks),
        session_before_tree=_freeze_tuple(runtime.session_before_tree_hooks),
    )

    step("tool_ports_capability")
    ports: dict[str, ToolPort] = {}
    for registered in runtime.tools:
        port = build_tool_port(registered, values)
        if not isinstance(port, ToolPort):
            raise TypeError("build_tool_port must return ToolPort")
        ports[port.definition.name] = port
    capability_state = build_tool_capability(ports)
    _validate_capability_projection(ports, capability_state)
    tools = ExtensionToolProjection(
        registered=_freeze_tuple(runtime.tools),
        ports=_freeze_mapping(ports),
        capability_state=capability_state,
    )

    step("renderer_mappings")
    renderers = ExtensionRendererProjection(
        tools=_freeze_mapping(
            {
                registered.tool.name: registered.tool
                for registered in runtime.tools
                if registered.tool.render_call is not None
            }
        ),
        messages=_freeze_mapping(runtime.message_renderers),
        entries=_freeze_mapping(runtime.entry_renderers),
    )

    step("provider_contributions")
    providers = ExtensionProviderProjection(
        providers=_freeze_tuple(runtime.providers),
        unregistered=_freeze_tuple(runtime.unregistered_providers),
    )

    step("queue_handles")
    queues = ExtensionQueueProjection(
        user=GenerationQueueHandle(runtime.outbox, queue_mutex),
        custom=GenerationQueueHandle(runtime.custom_outbox, queue_mutex),
    )

    step("chrome_handle")
    return ExtensionProjection(
        runtime_flags=runtime_flags,
        commands=commands,
        hooks=hooks,
        tools=tools,
        renderers=renderers,
        providers=providers,
        queues=queues,
        chrome=chrome,
    )


ReloadFamilyPayload = tuple[object, ...]
ActivationInputsValue = NewType("ActivationInputsValue", ReloadFamilyPayload)
ProviderFactoryValue = NewType("ProviderFactoryValue", ReloadFamilyPayload)
ProviderRefreshValue = NewType("ProviderRefreshValue", ReloadFamilyPayload)
CodingUsageValue = NewType("CodingUsageValue", ReloadFamilyPayload)
CodingCompactionValue = NewType("CodingCompactionValue", ReloadFamilyPayload)
TemporaryLegacyValue = NewType("TemporaryLegacyValue", ReloadFamilyPayload)
PresentationPersistenceValue = NewType(
    "PresentationPersistenceValue", ReloadFamilyPayload
)


@dataclass(frozen=True, slots=True)
class DetachedReloadEffect(Generic[_T]):
    """One typed detached preparation and its refusal cleanup port."""

    value: _T
    dispose: Callable[[], None]

    def __post_init__(self) -> None:
        if not callable(self.dispose):
            raise TypeError("detached reload disposer must be callable")


ReloadEffectBuilder = Callable[[], DetachedReloadEffect[_T]]


@dataclass(frozen=True, slots=True, kw_only=True)
class ReloadEffectPreparationPorts:
    """Family-distinct builders in the exact R3b preparation order."""

    activation_inputs: ReloadEffectBuilder[ActivationInputsValue]
    projection: ReloadEffectBuilder[ExtensionProjection]
    provider_catalog: ReloadEffectBuilder[ProviderCatalogReloadState]
    provider_factory: ReloadEffectBuilder[ProviderFactoryValue]
    provider_refresh: ReloadEffectBuilder[ProviderRefreshValue]
    provider_fallback: ReloadEffectBuilder[ReplSelectionReloadValue]
    coding_binding: ReloadEffectBuilder[CodingReloadBindingValue]
    coding_history: ReloadEffectBuilder[CodingReloadHistoryValue]
    coding_usage: ReloadEffectBuilder[CodingUsageValue]
    coding_compaction: ReloadEffectBuilder[CodingCompactionValue]
    unavailable_default: ReloadEffectBuilder[ReplPendingDefaultReloadValue]
    capability: ReloadEffectBuilder[ToolCapabilityState]
    temporary_legacy: ReloadEffectBuilder[TemporaryLegacyValue]
    presentation_persistence: ReloadEffectBuilder[PresentationPersistenceValue]
    chrome_prepare_input: ReloadEffectBuilder["ExtensionChromePrepareInput"]

    def __post_init__(self) -> None:
        for name in PREPARED_RELOAD_BUILD_STEPS:
            if not callable(getattr(self, name)):
                raise TypeError(f"{name} reload preparation port must be callable")


@dataclass(frozen=True, slots=True, kw_only=True)
class PreparedReloadEffects:
    """One frozen, family-typed assembly of unpublished reload effects."""

    activation_inputs: DetachedReloadEffect[ActivationInputsValue]
    projection: DetachedReloadEffect[ExtensionProjection]
    provider_catalog: DetachedReloadEffect[ProviderCatalogReloadState]
    provider_factory: DetachedReloadEffect[ProviderFactoryValue]
    provider_refresh: DetachedReloadEffect[ProviderRefreshValue]
    provider_fallback: DetachedReloadEffect[ReplSelectionReloadValue]
    coding_binding: DetachedReloadEffect[CodingReloadBindingValue]
    coding_history: DetachedReloadEffect[CodingReloadHistoryValue]
    coding_usage: DetachedReloadEffect[CodingUsageValue]
    coding_compaction: DetachedReloadEffect[CodingCompactionValue]
    unavailable_default: DetachedReloadEffect[ReplPendingDefaultReloadValue]
    capability: DetachedReloadEffect[ToolCapabilityState]
    temporary_legacy: DetachedReloadEffect[TemporaryLegacyValue]
    presentation_persistence: DetachedReloadEffect[PresentationPersistenceValue]
    chrome_prepare_input: DetachedReloadEffect["ExtensionChromePrepareInput"]

    def dispose(self) -> None:
        """Attempt every disposer in reverse order, then report every failure."""

        failures: list[BaseException] = []
        for name in reversed(PREPARED_RELOAD_BUILD_STEPS):
            try:
                getattr(self, name).dispose()
            except BaseException as error:  # complete cleanup, including interrupts
                failures.append(error)
        _raise_collected("detached reload disposal failures", failures)


def _raise_collected(label: str, failures: list[BaseException]) -> None:
    if not failures:
        return
    if len(failures) == 1:
        raise failures[0]
    exceptions = [error for error in failures if isinstance(error, Exception)]
    if len(exceptions) == len(failures):
        raise ExceptionGroup(label, exceptions)
    raise BaseExceptionGroup(label, failures)


def _dispose_completed_reload_effects(
    completed: list[DetachedReloadEffect[Any]],
) -> None:
    """Best-effort rollback; cleanup errors never replace the builder failure."""

    for effect in reversed(completed):
        try:
            effect.dispose()
        except BaseException:
            pass


def build_prepared_reload_effects(
    ports: ReloadEffectPreparationPorts,
    *,
    step_observer: ReloadPreparationObserver | None = None,
) -> PreparedReloadEffects:
    """Finish all builders before the sole assembly; never invoke chrome prepare.

    On build failure, completed effects are disposed in reverse order and any
    cleanup failures are suppressed so the original builder failure propagates.
    """

    if not isinstance(ports, ReloadEffectPreparationPorts):
        raise TypeError("ports must be ReloadEffectPreparationPorts")
    if step_observer is not None and not callable(step_observer):
        raise TypeError("step_observer must be callable or None")
    completed: list[DetachedReloadEffect[Any]] = []

    def complete(
        builder: ReloadEffectBuilder[_T], name: str
    ) -> DetachedReloadEffect[_T]:
        effect = builder()
        if not isinstance(effect, DetachedReloadEffect):
            raise TypeError(f"{name} must return DetachedReloadEffect")
        completed.append(effect)
        if step_observer is not None:
            step_observer(name)
        return effect

    try:
        activation_inputs = complete(ports.activation_inputs, "activation_inputs")
        projection = complete(ports.projection, "projection")
        provider_catalog = complete(ports.provider_catalog, "provider_catalog")
        provider_factory = complete(ports.provider_factory, "provider_factory")
        provider_refresh = complete(ports.provider_refresh, "provider_refresh")
        provider_fallback = complete(ports.provider_fallback, "provider_fallback")
        coding_binding = complete(ports.coding_binding, "coding_binding")
        coding_history = complete(ports.coding_history, "coding_history")
        coding_usage = complete(ports.coding_usage, "coding_usage")
        coding_compaction = complete(ports.coding_compaction, "coding_compaction")
        unavailable_default = complete(ports.unavailable_default, "unavailable_default")
        capability = complete(ports.capability, "capability")
        temporary_legacy = complete(ports.temporary_legacy, "temporary_legacy")
        presentation_persistence = complete(
            ports.presentation_persistence, "presentation_persistence"
        )
        chrome_prepare_input = complete(
            ports.chrome_prepare_input, "chrome_prepare_input"
        )
        prepared = PreparedReloadEffects(
            activation_inputs=activation_inputs,
            projection=projection,
            provider_catalog=provider_catalog,
            provider_factory=provider_factory,
            provider_refresh=provider_refresh,
            provider_fallback=provider_fallback,
            coding_binding=coding_binding,
            coding_history=coding_history,
            coding_usage=coding_usage,
            coding_compaction=coding_compaction,
            unavailable_default=unavailable_default,
            capability=capability,
            temporary_legacy=temporary_legacy,
            presentation_persistence=presentation_persistence,
            chrome_prepare_input=chrome_prepare_input,
        )
        if step_observer is not None:
            step_observer("prepared_reload_effects")
        return prepared
    except BaseException:
        _dispose_completed_reload_effects(completed)
        raise


@dataclass(frozen=True, slots=True)
class FrozenStagedDeliveryBatch:
    """R1-frozen batch: all users in order, then all customs in order."""

    user_messages: tuple[QueuedUserMessage, ...]
    custom_messages: tuple[QueuedCustomMessage, ...]

    @classmethod
    def freeze(
        cls,
        user_messages: Iterable[QueuedUserMessage],
        custom_messages: Iterable[QueuedCustomMessage],
    ) -> "FrozenStagedDeliveryBatch":
        return cls(
            tuple(
                replace(message, options=_freeze_mapping(message.options))
                for message in user_messages
            ),
            tuple(_freeze_custom_message(message) for message in custom_messages),
        )


@dataclass(frozen=True, slots=True, eq=False)
class OrderedDeliveryToken:
    """Single-use authority yielded only by a reservation context."""


class OrderedDeliveryGate:
    """Linearizable uninstalled staged-first delivery gate."""

    __slots__ = (
        "_active_direct",
        "_condition",
        "_draining",
        "_mutex",
        "_queued",
        "_released",
        "_reservation_pending",
        "_token",
    )

    def __init__(self, mutex: threading.RLock) -> None:
        if not isinstance(mutex, _RLOCK_TYPE):
            raise TypeError("ordered delivery mutex must be an RLock")
        self._mutex = mutex
        self._condition = threading.Condition(mutex)
        self._queued: deque[Callable[[], None]] = deque()
        self._token: OrderedDeliveryToken | None = None
        self._reservation_pending = False
        self._released = False
        self._draining = False
        self._active_direct = 0

    @contextmanager
    def reserve(self) -> Iterator[OrderedDeliveryToken]:
        """Reserve after admitted direct sends finish; abort on every exit."""

        token = OrderedDeliveryToken()
        with self._condition:
            if self._token is not None:
                raise RuntimeError("ordered delivery gate is already active")
            self._token = token
            self._reservation_pending = True
            try:
                while self._active_direct:
                    self._condition.wait()
            except BaseException:
                if self._token is token:
                    self._reset_locked()
                raise
            if self._token is not token:
                raise RuntimeError("ordered delivery reservation was aborted")
            self._reservation_pending = False
        try:
            yield token
        finally:
            self.abort(token, missing_ok=True)

    def submit(self, delivery: Callable[[], None]) -> None:
        """Atomically queue behind a reservation or deliver unlocked now."""

        if not callable(delivery):
            raise TypeError("delivery must be callable")
        with self._condition:
            if self._token is not None:
                self._queued.append(delivery)
                return
            self._active_direct += 1
        try:
            delivery()
        finally:
            with self._condition:
                self._active_direct -= 1
                self._condition.notify_all()

    def validate(self, token: OrderedDeliveryToken) -> None:
        """Require token authority without changing reservation state."""

        with self._condition:
            self._validate_active_locked(token)

    def release(self, token: OrderedDeliveryToken) -> None:
        with self._condition:
            self._validate_active_locked(token)
            self._released = True

    def drain(self, token: OrderedDeliveryToken) -> bool:
        """Drain FIFO unlocked; aggregate Exceptions and abort on interrupts."""

        with self._condition:
            if self._token is not token:
                raise RuntimeError("ordered delivery token is not active")
            if not self._released or self._draining:
                return False
            self._draining = True
        failures: list[BaseException] = []
        try:
            while True:
                with self._condition:
                    if self._token is not token:
                        break
                    if not self._queued:
                        self._reset_locked()
                        break
                    delivery = self._queued.popleft()
                try:
                    delivery()
                except Exception as error:
                    failures.append(error)
                except BaseException:
                    self.abort(token, missing_ok=True)
                    raise
        finally:
            with self._condition:
                if self._token is token and self._draining:
                    self._reset_locked()
        _raise_collected("ordered delivery failures", failures)
        return True

    def abort(self, token: OrderedDeliveryToken, *, missing_ok: bool = False) -> bool:
        """Abandon one reservation and discard every send queued behind it."""

        with self._condition:
            if self._token is not token:
                if missing_ok:
                    return False
                raise RuntimeError("ordered delivery token is not active")
            self._reset_locked()
            return True

    def _validate_active_locked(self, token: OrderedDeliveryToken) -> None:
        if self._token is not token or self._reservation_pending or self._released:
            raise RuntimeError("ordered delivery token is not active")

    def _reset_locked(self) -> None:
        self._queued.clear()
        self._token = None
        self._reservation_pending = False
        self._released = False
        self._draining = False
        self._condition.notify_all()


@dataclass(frozen=True, slots=True)
class SessionExtensionGeneration:
    """Canonical live extension state for one session generation.

    The activated runtime owns every registered contribution and the outbox
    lists extensions retain after activation. Parsed flag values belong beside
    that runtime because every command, hook, provider, tool, renderer, and UI
    dispatch must observe flags from the same live generation.
    """

    runtime: _ExtensionRuntime
    flag_values: dict[str, object]


@dataclass(frozen=True, slots=True)
class SessionGenerationSnapshot:
    """One operation's consistent view of the live generation.

    An operation reads this once at its start and reads extension-owned state
    from it for its whole duration, so a reload landing mid-operation cannot
    show it a mixture of two generations. ``generation_id`` identifies which
    generation the view came from; it is what later slices compare to reject a
    stale mutation.
    """

    generation: SessionExtensionGeneration
    generation_id: int


class SessionGenerationRef:
    """The session's single synchronization boundary and generation pointer.

    One `NativeToolReplSession.run()` owns exactly one of these. Its ``lock``
    is *the* session mutex: every field that a detached worker can reach — the
    live generation pointer here, and the tool-capability state pointer that is
    constructed with this same lock — is read and written under it. A lock that
    only one side of a race takes excludes nobody, so both sides take this one.

    The lock is reentrant because a mutation port may be invoked from inside
    another port on the session thread.

    Nothing slow runs inside the critical sections here: they read or assign a
    pointer and nothing else. Publication also hands back the value it replaced
    rather than letting it die under the lock, so a finalizer or weakref
    callback on a retired generation cannot run inside the critical section.
    """

    __slots__ = ("_lock", "_generation", "_generation_id", "_publication_pending")

    def __init__(
        self,
        generation: SessionExtensionGeneration,
        *,
        lock: "threading.RLock | None" = None,
    ) -> None:
        # The session creates the mutex before the first guarded owner exists
        # and hands the same object to each of them. Accepting it here keeps
        # this reference one *user* of the boundary rather than its owner.
        self._lock = lock if lock is not None else threading.RLock()
        self._generation = generation
        self._generation_id = 0
        self._publication_pending = False

    @property
    def lock(self) -> "threading.RLock":
        """The session mutex, shared with every owner of guarded state."""

        return self._lock

    @property
    def current(self) -> SessionExtensionGeneration:
        """The live generation. Prefer :meth:`snapshot` inside an operation."""

        with self._lock:
            return self._generation

    def snapshot(self) -> SessionGenerationSnapshot:
        """Take one consistent view for the whole of an operation.

        Not yet adopted by the composition root, which still reads
        :attr:`current` per access. That is sound *today* only because the sole
        publisher is `/reload`, which runs on the session thread: there is no
        concurrent writer for a multi-read operation to interleave with. It
        stops being sound the moment a detached worker can publish, so the
        consumer conversion is required by the slice that introduces
        generation-bound mutation ports, not deferred past it.
        """

        with self._lock:
            return SessionGenerationSnapshot(self._generation, self._generation_id)

    def publish(
        self, generation: SessionExtensionGeneration
    ) -> SessionExtensionGeneration:
        """Make ``generation`` live and return the one it replaced.

        Non-fallible by construction: a pointer assignment and an integer
        increment. The retired generation is returned rather than dropped so
        the caller holds it until after the lock is released.

        Publishing deliberately does **not** close the publication gate. A
        reload swaps this pointer partway through — before the provider
        selection, tool visibility, and renderer projections derived from it
        are republished — so clearing the gate here would reopen mutations for
        the rest of the reload and let an accepted change be overwritten by the
        projections still to come. :meth:`publishing` owns the gate for the
        whole publication.
        """

        with self._lock:
            retired = self._generation
            self._generation = generation
            self._generation_id += 1
        return retired

    @property
    def publication_pending(self) -> bool:
        """Whether a reload is between reading live state and publishing it."""

        with self._lock:
            return self._publication_pending

    @contextmanager
    def publishing(self) -> "Iterator[None]":
        """Open the publication gate for the duration of a reload.

        Generation-bound mutation ports fail closed while this is open. The
        window exists because a reload reads live provider selection, thinking
        level, and tool visibility, then republishes values derived from them
        some time later; a mutation accepted in between would be silently
        overwritten at the swap. Refusing it instead is the fail-closed
        direction, and the only callers that can hit the refusal are stragglers
        from an already-cancelled operation.

        The gate is opened and closed under the lock but is **not** held across
        the body, so no fallible or slow work runs inside a critical section.
        Closing is guaranteed even if the body raises: a reload whose candidate
        preparation fails must not leave every extension mutation refused for
        the rest of the session.
        """

        with self._lock:
            self._publication_pending = True
        try:
            yield
        finally:
            with self._lock:
                self._publication_pending = False
