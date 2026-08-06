"""Attach one activated extension generation to a native REPL session.

Startup and ``/reload`` used to own separate copies of the projection, routing,
publication, staged-delivery, and cleanup transaction.  This module is the one
owner of that transaction.  ``predecessor=None`` is the explicit startup edge;
a supplied predecessor is the reload edge and may remain live after refusal.

All fallible extension callbacks, chrome work, staged sinks, diagnostics, and
prepared-effect disposal run outside the session mutex.  The mutex is used only
for the initial generation/capability publication and by the established reload
publication contract.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, cast

from pipy_harness.native import extension_hooks as _extension_hooks
from pipy_harness.native.agent.usage import AgentUsageAccumulator
from pipy_harness.native.coding.state import (
    CodingReloadHistoryValue,
    CodingSessionState,
)
from pipy_harness.native.extension_chrome_state import (
    ExtensionChromeCommitToken,
    ExtensionChromePrepareInput,
    ExtensionChromeRetirement,
    ExtensionChromeSink,
    ExtensionChromeSnapshot,
)
from pipy_harness.native.extension_types import QueuedCustomMessage
from pipy_harness.native.extensions.activation import _ExtensionCandidate
from pipy_harness.native.extensions.contracts import _ExtensionRuntime
from pipy_harness.native.extensions.tool_port import ToolRenderDetailsWriter
from pipy_harness.native.provider import ProviderPort
from pipy_harness.native.repl.execution_projections import (
    build_candidate_extension_projection,
)
from pipy_harness.native.repl.turn_leaves import (
    finish_chrome_retirement,
    raise_first,
)
from pipy_harness.native.repl_state import (
    NativeModelSelection,
    NativeReplProviderState,
)
from pipy_harness.native.session_generation import (
    ExtensionChromeHandle,
    ExtensionProjection,
    FrozenStagedDeliveryBatch,
    OrderedDeliveryGate,
    OrderedDeliveryToken,
    PreparedReloadEffects,
    SessionExtensionGeneration,
    SessionGenerationRef,
    prepare_production_reload,
    publish_candidate_ownership,
    with_tool_capability,
)
from pipy_harness.native.session_state_lock import SessionStateLock
from pipy_harness.native.tool_capabilities import (
    NativeToolCapabilities,
    ToolCapabilityState,
)


class ChromeAcceptance(Protocol):
    """The fields attachment reads from a chrome handoff result."""

    @property
    def accepted(self) -> bool: ...

    @property
    def diagnostic(self) -> str | None: ...

    @property
    def retired_sink(self) -> ExtensionChromeSink | None: ...

    @property
    def candidate_closed(self) -> bool: ...


class ReloadChromePort(Protocol):
    """Narrow reload chrome ownership port; terminal implementation stays outside."""

    def prepare_candidate(
        self, prepared: ExtensionChromePrepareInput
    ) -> ExtensionChromeCommitToken | None: ...

    def accept_candidate(
        self,
        candidate: ExtensionChromeSink,
        *,
        rollback_snapshot: ExtensionChromeSnapshot | None = None,
    ) -> ChromeAcceptance: ...

    def owns_sink(self, sink: ExtensionChromeSink) -> bool: ...

    def dispose_retired_sink(self, retired: ExtensionChromeSink) -> str | None: ...


@dataclass(frozen=True, slots=True, kw_only=True)
class ExtensionAttachInput:
    """Common candidate inputs used by startup and reload attachment."""

    candidate: _ExtensionCandidate
    runtime: _ExtensionRuntime
    flag_values: Mapping[str, object]
    state_lock: SessionStateLock
    has_ui: bool
    notify_sink: Callable[[str, str], None]
    set_active_tools: Callable[[int, Sequence[str]], bool]
    render_details: ToolRenderDetailsWriter
    project_trusted: bool
    tool_capabilities: NativeToolCapabilities
    chrome_sink: ExtensionChromeSink | None


@dataclass(frozen=True, slots=True, kw_only=True)
class StartupAttachPorts:
    """Startup-only preparation that must finish before host publication."""

    before_publish: Callable[[SessionGenerationRef, ExtensionProjection], None]


@dataclass(frozen=True, slots=True, kw_only=True)
class ReloadAttachPorts:
    """Reload-only owners and callbacks at the shared transaction's edges."""

    provider_state: NativeReplProviderState | None
    coding_state: CodingSessionState
    unavailable_provider: Callable[[str], ProviderPort]
    usage_prototype: Callable[[NativeModelSelection], AgentUsageAccumulator]
    empty_history: CodingReloadHistoryValue
    candidate_session_start: Callable[[], None]
    chrome: ReloadChromePort | None
    custom_sink: Callable[[QueuedCustomMessage], None]
    diagnostic: Callable[[str], None]
    report_presentation: Callable[["ReloadPresentation"], None]


@dataclass(frozen=True, slots=True)
class ReloadPresentation:
    """Accepted reload reporting values detached before prepared disposal."""

    diagnostic: str | None
    persist_default: bool
    provider_state: NativeReplProviderState | None


@dataclass(frozen=True, slots=True)
class AttachGenerationRefusal:
    """A bounded attachment refusal; a predecessor, when supplied, stays live."""

    reason: str
    predecessor: SessionGenerationRef | None


@dataclass(slots=True)
class _StartupAttachmentLifecycle:
    state: str = "attached"


@dataclass(frozen=True, slots=True)
class StartupGenerationAttachment:
    """A startup attachment whose staged sink binds later in composition."""

    generation_ref: SessionGenerationRef
    projection: ExtensionProjection
    gate: OrderedDeliveryGate
    staged: FrozenStagedDeliveryBatch
    _lifecycle: _StartupAttachmentLifecycle

    def deliver_staged(
        self, custom_sink: Callable[[QueuedCustomMessage], None]
    ) -> None:
        _deliver_startup_staged(self, custom_sink)

    def abort(self) -> None:
        raise_first((_abort_startup_attachment(self),))


@dataclass(frozen=True, slots=True)
class ReloadGenerationAttachment:
    """Successful reload attachment; the supplied predecessor is now current."""

    generation_ref: SessionGenerationRef


@dataclass(slots=True)
class _AttachAttempt:
    projection: ExtensionProjection | None = None
    prepared: PreparedReloadEffects | None = None
    chrome_retirement: ExtensionChromeRetirement | None = None
    published: bool = False

    def require_projection(self) -> ExtensionProjection:
        if self.projection is None:
            raise RuntimeError("extension attachment has not staged a projection")
        return self.projection


@dataclass(frozen=True, slots=True)
class _PreparedReloadGeneration:
    generation: SessionExtensionGeneration
    prepared: PreparedReloadEffects
    expected_capability: ToolCapabilityState


def attach_generation(
    inputs: ExtensionAttachInput,
    predecessor: SessionGenerationRef | None = None,
    *,
    startup_ports: StartupAttachPorts | None = None,
    reload_ports: ReloadAttachPorts | None = None,
) -> StartupGenerationAttachment | ReloadGenerationAttachment | AttachGenerationRefusal:
    """Attach one generation, retaining ``predecessor`` on reload refusal."""

    _validate_attach_mode(inputs, predecessor, startup_ports, reload_ports)
    attempt = _AttachAttempt()
    result: (
        StartupGenerationAttachment
        | ReloadGenerationAttachment
        | AttachGenerationRefusal
    )
    try:
        gate = _build_projection_and_route(inputs, attempt)
        if gate is None:
            result = _refuse_unavailable_projection(predecessor, reload_ports)
        elif predecessor is None:
            assert startup_ports is not None
            result = _attach_startup_generation(inputs, startup_ports, attempt, gate)
        else:
            assert reload_ports is not None
            result = _attach_reload_generation(
                inputs, predecessor, reload_ports, attempt, gate
            )
    except BaseException as error:
        if predecessor is None:
            cleanup_error = _retire_projection_and_chrome(
                attempt.projection, inputs.chrome_sink
            )
            raise_first((error, cleanup_error))
        raise
    finally:
        if predecessor is not None:
            assert reload_ports is not None
            _retire_reload_attempt(inputs.chrome_sink, reload_ports, attempt)
    if predecessor is None and isinstance(result, AttachGenerationRefusal):
        raise_first(
            (_retire_projection_and_chrome(attempt.projection, inputs.chrome_sink),)
        )
    return result


def _validate_attach_mode(
    inputs: ExtensionAttachInput,
    predecessor: SessionGenerationRef | None,
    startup_ports: StartupAttachPorts | None,
    reload_ports: ReloadAttachPorts | None,
) -> None:
    startup = predecessor is None
    if startup != (startup_ports is not None) or startup != (reload_ports is None):
        raise TypeError("exactly one matching startup/reload port must be supplied")
    if predecessor is not None and predecessor.lock is not inputs.state_lock:
        raise ValueError("extension attachment must preserve the session state lock")


def _build_projection_and_route(
    inputs: ExtensionAttachInput, attempt: _AttachAttempt
) -> OrderedDeliveryGate | None:
    projection = build_candidate_extension_projection(
        inputs.runtime,
        inputs.flag_values,
        queue_mutex=inputs.state_lock,
        reference_mutex=inputs.state_lock,
        has_ui=inputs.has_ui,
        notify_sink=inputs.notify_sink,
        set_active_tools=inputs.set_active_tools,
        render_details=inputs.render_details,
        project_trusted=inputs.project_trusted,
        prepare_capability=inputs.tool_capabilities.prepare_extensions,
        chrome=(
            ExtensionChromeHandle(inputs.chrome_sink)
            if inputs.chrome_sink is not None
            else None
        ),
    )
    if not isinstance(projection, ExtensionProjection):
        return None
    attempt.projection = projection
    gate = OrderedDeliveryGate(inputs.state_lock)
    projection.queues.install_candidate_route(gate)
    return gate


def _refuse_unavailable_projection(
    predecessor: SessionGenerationRef | None,
    reload_ports: ReloadAttachPorts | None,
) -> AttachGenerationRefusal:
    reason = "extension generation projection is unavailable"
    if reload_ports is not None:
        _diagnose_reload_refusal(reload_ports, reason)
    return AttachGenerationRefusal(reason, predecessor)


def _attach_startup_generation(
    inputs: ExtensionAttachInput,
    ports: StartupAttachPorts,
    attempt: _AttachAttempt,
    gate: OrderedDeliveryGate,
) -> StartupGenerationAttachment | AttachGenerationRefusal:
    projection = attempt.require_projection()
    staged = FrozenStagedDeliveryBatch.freeze((), inputs.runtime.custom_messages)
    generation = SessionExtensionGeneration(inputs.runtime, projection)
    with inputs.state_lock:
        generation_ref = SessionGenerationRef(generation, lock=inputs.state_lock)
        inputs.tool_capabilities.publish(projection.tools.capability_state)
    ports.before_publish(generation_ref, projection)
    if not publish_candidate_ownership(inputs.candidate):
        return AttachGenerationRefusal(
            "extension candidate ownership is unavailable", None
        )
    attempt.published = True
    return StartupGenerationAttachment(
        generation_ref,
        projection,
        gate,
        staged,
        _StartupAttachmentLifecycle(),
    )


def _attach_reload_generation(
    inputs: ExtensionAttachInput,
    predecessor: SessionGenerationRef,
    ports: ReloadAttachPorts,
    attempt: _AttachAttempt,
    gate: OrderedDeliveryGate,
) -> ReloadGenerationAttachment | AttachGenerationRefusal:
    ports.candidate_session_start()
    with predecessor.publishing():
        prepared_generation = _prepare_reload_generation(inputs, ports, attempt)
        if prepared_generation is None:
            reason = "extension chrome candidate is unavailable"
            _diagnose_reload_refusal(ports, reason)
            return AttachGenerationRefusal(reason, predecessor)
        failure = _accept_reload_generation(
            inputs, predecessor, ports, attempt, gate, prepared_generation
        )
        if failure is not None:
            _diagnose_reload_refusal(ports, failure)
            return AttachGenerationRefusal(failure, predecessor)
    _report_reload_presentation(ports, prepared_generation.prepared)
    return ReloadGenerationAttachment(predecessor)


def _prepare_reload_generation(
    inputs: ExtensionAttachInput,
    ports: ReloadAttachPorts,
    attempt: _AttachAttempt,
) -> _PreparedReloadGeneration | None:
    with inputs.state_lock:
        expected_capability = inputs.tool_capabilities._state
    projection = attempt.require_projection()
    capability = inputs.tool_capabilities.prepare_extensions(projection.tools.ports)
    projection = with_tool_capability(projection, capability)
    attempt.projection = projection
    chrome_sink = inputs.chrome_sink or ExtensionChromeSink()
    prepared = prepare_production_reload(
        inputs.runtime,
        projection,
        ExtensionChromePrepareInput(chrome_sink),
        state=ports.provider_state,
        coding=ports.coding_state,
        lock=inputs.state_lock,
        unavailable_provider=ports.unavailable_provider,
        usage_prototype=ports.usage_prototype,
        empty_history=ports.empty_history,
        capability=capability,
    )
    attempt.prepared = prepared
    if inputs.chrome_sink is None:
        chrome_sink.close()
    chrome_input = prepared.chrome_prepare_input.value
    chrome_token = (
        ports.chrome.prepare_candidate(chrome_input)
        if ports.chrome is not None
        else ExtensionChromeCommitToken(chrome_input)
    )
    if chrome_token is None:
        return None
    return _PreparedReloadGeneration(
        SessionExtensionGeneration(inputs.runtime, projection, chrome_token),
        prepared,
        expected_capability,
    )


def _accept_reload_generation(
    inputs: ExtensionAttachInput,
    predecessor: SessionGenerationRef,
    ports: ReloadAttachPorts,
    attempt: _AttachAttempt,
    gate: OrderedDeliveryGate,
    prepared_generation: _PreparedReloadGeneration,
) -> str | None:
    projection = attempt.require_projection()
    with gate.reserve() as token:
        failure, retired_chrome = predecessor.accept_prepared_reload(
            prepared_generation.generation,
            prepared_generation.prepared,
            candidate=inputs.candidate,
            provider_state=ports.provider_state,
            coding_state=ports.coding_state,
            tool_capabilities=inputs.tool_capabilities,
            expected_capability=prepared_generation.expected_capability,
        )
        if failure is not None:
            return failure
        attempt.published = True
        attempt.chrome_retirement, chrome_close_error = (
            retired_chrome.close_nonraising() if retired_chrome else (None, None)
        )
        delivery_error = _deliver_reload_staged(
            prepared_generation.prepared,
            projection,
            gate,
            token,
            ports.custom_sink,
        )
        cleanup_error = finish_chrome_retirement(attempt.chrome_retirement)
        raise_first((delivery_error, cleanup_error, chrome_close_error))
    return None


def _deliver_reload_staged(
    prepared: PreparedReloadEffects,
    projection: ExtensionProjection,
    gate: OrderedDeliveryGate,
    token: OrderedDeliveryToken,
    custom_sink: Callable[[QueuedCustomMessage], None],
) -> BaseException | None:
    try:
        _extension_hooks.deliver_accepted_staged_batch(
            cast(FrozenStagedDeliveryBatch, prepared.activation_inputs.value[0]),
            gate=gate,
            token=token,
            user_sink=lambda _message: None,
            custom_sink=custom_sink,
            release_route=projection.queues.release_pending_route,
        )
    except BaseException as error:  # noqa: BLE001 - chrome retirement must still run
        return error
    return None


def _report_reload_presentation(
    ports: ReloadAttachPorts, prepared: PreparedReloadEffects
) -> None:
    diagnostic, persist_default = prepared.presentation_persistence.value
    ports.report_presentation(
        ReloadPresentation(
            cast(str | None, diagnostic),
            bool(persist_default),
            ports.provider_state,
        )
    )


def _diagnose_reload_refusal(ports: ReloadAttachPorts, reason: str) -> None:
    ports.diagnostic(f"pipy: {reason}")
    ports.diagnostic("pipy: keeping the previous extensions.")


def _deliver_startup_staged(
    attachment: StartupGenerationAttachment,
    custom_sink: Callable[[QueuedCustomMessage], None],
) -> None:
    with attachment.generation_ref.lock:
        if attachment._lifecycle.state != "attached":
            raise RuntimeError("startup extension attachment is not deliverable")
        attachment._lifecycle.state = "delivering"
    try:
        with attachment.gate.reserve() as token:
            _extension_hooks.deliver_accepted_staged_batch(
                attachment.staged,
                gate=attachment.gate,
                token=token,
                user_sink=lambda _message: None,
                custom_sink=custom_sink,
                release_route=attachment.projection.queues.release_pending_route,
            )
    except BaseException as error:  # noqa: BLE001 - preserve staged interrupts
        cleanup_error = _abort_startup_attachment(attachment)
        raise_first((error, cleanup_error))
    with attachment.generation_ref.lock:
        if attachment._lifecycle.state == "delivering":
            attachment._lifecycle.state = "delivered"


def _abort_startup_attachment(
    attachment: StartupGenerationAttachment,
) -> BaseException | None:
    with attachment.generation_ref.lock:
        if attachment._lifecycle.state == "retired":
            return None
        attachment._lifecycle.state = "retired"
    chrome = attachment.projection.chrome
    return _retire_projection_and_chrome(
        attachment.projection,
        None if chrome is None else chrome.sink,
    )


def _retire_reload_attempt(
    chrome_candidate: ExtensionChromeSink | None,
    ports: ReloadAttachPorts,
    attempt: _AttachAttempt,
) -> None:
    try:
        if attempt.published:
            diagnostic = _finish_candidate_chrome(
                ports.chrome, chrome_candidate, attempt.chrome_retirement
            )
            if diagnostic is not None:
                ports.diagnostic(diagnostic)
        else:
            cleanup_error = _retire_projection_and_chrome(
                attempt.projection, chrome_candidate
            )
    finally:
        if attempt.prepared is not None:
            attempt.prepared.dispose()
    if not attempt.published:
        raise_first((cleanup_error,))


def _finish_candidate_chrome(
    chrome: ReloadChromePort | None,
    candidate: ExtensionChromeSink | None,
    retirement: ExtensionChromeRetirement | None,
) -> str | None:
    if chrome is None or candidate is None:
        return None
    owned = True
    try:
        acceptance = chrome.accept_candidate(
            candidate,
            rollback_snapshot=retirement.snapshot if retirement is not None else None,
        )
        if not acceptance.accepted:
            if not acceptance.candidate_closed:
                candidate.close()
            owned = False
            return acceptance.diagnostic
        owned = False
        cleanup = (
            chrome.dispose_retired_sink(acceptance.retired_sink)
            if acceptance.retired_sink is not None
            else None
        )
        return cleanup or acceptance.diagnostic
    finally:
        if owned and not chrome.owns_sink(candidate):
            candidate.close()


def _retire_projection_and_chrome(
    projection: ExtensionProjection | None,
    chrome_sink: ExtensionChromeSink | None,
) -> BaseException | None:
    route_error: BaseException | None = None
    chrome_error: BaseException | None = None
    try:
        if projection is not None:
            projection.queues.retire_route()
    except BaseException as error:  # noqa: BLE001 - cleanup continues through every owner
        route_error = error
    try:
        if chrome_sink is not None:
            chrome_sink.close()
    except BaseException as error:  # noqa: BLE001 - cleanup continues through every owner
        chrome_error = error
    return route_error or chrome_error
