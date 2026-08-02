"""Headless mutable state for one synchronous product coding session."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from math import isfinite

import pipy_harness.native.agent.usage as agent_usage
from pipy_harness.native.agent.content import ProductContent
from pipy_harness.native.agent.identity import AGENT_TOOL_REQUEST_ID_PREFIX
from pipy_harness.native.agent.loop_policy import (
    MAX_AGENT_TOOL_BUDGET,
    AgentToolPolicyState,
)
from pipy_harness.native.agent.messages import (
    AgentAssistantMessage,
    AgentMessage,
    AgentToolCall,
    AgentToolResultMessage,
    AgentUserMessage,
)
from pipy_harness.native.agent.results import AgentFailure, AgentUsage
from pipy_harness.native.agent.usage import (
    AgentProviderUsageSample,
    AgentUsageAccumulator,
)
from pipy_harness.native.provider import ProviderPort

_EXACT_AGENT_MESSAGE_TYPES = (
    AgentUserMessage,
    AgentAssistantMessage,
    AgentToolResultMessage,
)


@dataclass(frozen=True, slots=True)
class CodingProviderBinding:
    """One atomic provider port and its explicit product-facing labels."""

    provider: ProviderPort
    provider_name: str
    model_id: str

    def __post_init__(self) -> None:
        _require_provider(self.provider)
        _require_non_empty_string(self.provider_name, "provider_name")
        _require_non_empty_string(self.model_id, "model_id")


@dataclass(frozen=True, slots=True)
class CodingModelMutation:
    """Prepared provider/history/usage replacement for one model switch."""

    expected_binding: CodingProviderBinding
    replacement_binding: CodingProviderBinding
    replacement_usage: AgentUsageAccumulator


@dataclass(frozen=True, slots=True)
class CodingReloadBindingValue:
    expected: CodingProviderBinding
    replacement: CodingProviderBinding


@dataclass(frozen=True, slots=True)
class CodingReloadHistoryValue:
    messages: tuple[AgentMessage, ...]

    def __post_init__(self) -> None:
        require_exact_agent_messages(self.messages)


@dataclass(frozen=True, slots=True)
class CodingReloadRebindState:
    """Detached binding and immutable replacement history for fallback."""

    binding: CodingReloadBindingValue
    history: CodingReloadHistoryValue


@dataclass(frozen=True, slots=True)
class CodingSessionUsageSnapshot:
    """Immutable presentation view of the session usage accumulator."""

    usage: AgentUsage
    last_total_tokens: int
    cache_hit_percent: float | None

    def __post_init__(self) -> None:
        _require_agent_usage(self.usage, "usage")
        _require_non_negative_int(self.last_total_tokens, "last_total_tokens")
        if self.cache_hit_percent is not None:
            if type(self.cache_hit_percent) is not float:
                raise TypeError("cache_hit_percent must be an exact float or None")
            if not isfinite(self.cache_hit_percent) or self.cache_hit_percent < 0:
                raise ValueError("cache_hit_percent must be finite and nonnegative")


@dataclass(frozen=True, slots=True)
class CodingSessionResultSnapshot:
    """Immutable projection of the live coding-session state."""

    provider_name: str
    model_id: str
    messages: tuple[AgentMessage, ...]
    usage: AgentUsage
    user_turn_count: int = 0
    tool_invocation_count: int = 0
    resource_invocation_count: int = 0
    malformed_argument_count: int = 0
    consecutive_malformed_streak: int = 0
    budget_exhausted_count: int = 0
    file_reference_count: int = 0
    file_reference_loaded_count: int = 0
    file_reference_failed_count: int = 0
    image_attachment_count: int = 0
    image_attachment_loaded_count: int = 0
    image_attachment_failed_count: int = 0
    compaction_suffix: str = ""
    compaction_count: int = 0
    compaction_dropped_group_count: int = 0
    provider_failure: AgentFailure | None = None

    def __post_init__(self) -> None:
        _require_non_empty_string(self.provider_name, "provider_name")
        _require_non_empty_string(self.model_id, "model_id")
        require_exact_agent_messages(self.messages)
        _require_agent_usage(self.usage, "usage")
        for field_name in _COUNTER_FIELD_NAMES:
            _require_non_negative_int(getattr(self, field_name), field_name)
        if self.consecutive_malformed_streak > self.malformed_argument_count:
            raise ValueError(
                "consecutive_malformed_streak must not exceed malformed_argument_count"
            )
        _require_resolution_totals(
            self.file_reference_count,
            self.file_reference_loaded_count,
            self.file_reference_failed_count,
            prefix="file_reference",
        )
        _require_resolution_totals(
            self.image_attachment_count,
            self.image_attachment_loaded_count,
            self.image_attachment_failed_count,
            prefix="image_attachment",
        )
        if type(self.compaction_suffix) is not str:
            raise TypeError("compaction_suffix must be an exact string")
        if self.provider_failure is not None:
            _require_agent_failure(self.provider_failure, "provider_failure")


_COUNTER_FIELD_NAMES = (
    "user_turn_count",
    "tool_invocation_count",
    "resource_invocation_count",
    "malformed_argument_count",
    "consecutive_malformed_streak",
    "budget_exhausted_count",
    "file_reference_count",
    "file_reference_loaded_count",
    "file_reference_failed_count",
    "image_attachment_count",
    "image_attachment_loaded_count",
    "image_attachment_failed_count",
    "compaction_count",
    "compaction_dropped_group_count",
)


class CodingSessionState:
    """Synchronous mutable owner of product coding-session state.

    Provider construction, pricing lookup, persistence, rendering, and agent-loop
    invocation remain composition concerns. A supplied usage accumulator transfers
    to this object; callers interact with it through typed state transitions.

    **Synchronization.** The provider binding, canonical history, usage
    accumulator, and compaction state are guarded state: an extension handler
    on a detached worker thread reaches them through ``set_model``, which
    rebinds the provider, clears live history, and resets usage. Every reader
    and writer of that group therefore takes ``state_lock`` — the session
    thread included, since a lock only one side takes excludes nobody.

    The remaining counters (turn, tool, resource, file-reference, and
    image-attachment tallies, plus the provider-failure slot) are written only
    by the agent loop on the session thread and are documented as
    session-thread-owned; they are reset inside the same critical section as a
    ``begin_run`` rebind so a snapshot never mixes generations. ``state_lock``
    is injectable so the composition root can pass the one session mutex.
    """

    __slots__ = (
        "_binding",
        "_state_lock",
        "_budget_exhausted_count",
        "_compaction_count",
        "_compaction_dropped_group_count",
        "_compaction_suffix",
        "_consecutive_malformed_streak",
        "_file_reference_count",
        "_file_reference_failed_count",
        "_file_reference_loaded_count",
        "_image_attachment_count",
        "_image_attachment_failed_count",
        "_image_attachment_loaded_count",
        "_malformed_argument_count",
        "_messages",
        "_provider_failure",
        "_resource_invocation_count",
        "_tool_invocation_count",
        "_usage_accumulator",
        "_user_turn_count",
    )

    def __init__(
        self,
        *,
        provider: ProviderPort,
        provider_name: str,
        model_id: str,
        usage_accumulator: AgentUsageAccumulator | None = None,
        messages: tuple[AgentMessage, ...] = (),
        state_lock: "threading.RLock | None" = None,
    ) -> None:
        self._state_lock = state_lock if state_lock is not None else threading.RLock()
        self._binding = CodingProviderBinding(provider, provider_name, model_id)
        self._usage_accumulator = (
            AgentUsageAccumulator()
            if usage_accumulator is None
            else _require_usage_accumulator(usage_accumulator)
        )
        require_exact_agent_messages(messages)
        self._messages = messages
        self._user_turn_count = 0
        self._tool_invocation_count = 0
        self._resource_invocation_count = 0
        self._malformed_argument_count = 0
        self._consecutive_malformed_streak = 0
        self._budget_exhausted_count = 0
        self._file_reference_count = 0
        self._file_reference_loaded_count = 0
        self._file_reference_failed_count = 0
        self._image_attachment_count = 0
        self._image_attachment_loaded_count = 0
        self._image_attachment_failed_count = 0
        self._compaction_suffix = ""
        self._compaction_count = 0
        self._compaction_dropped_group_count = 0
        self._provider_failure: AgentFailure | None = None

    def bind_state_lock(self, lock: "threading.RLock") -> None:
        """Adopt the session's mutex, keeping this object's identity.

        **Composition-time only**: call while the session is still being
        assembled, before any worker thread can reach this state.
        """

        with self._state_lock:
            if lock is self._state_lock:
                return
            self._state_lock = lock

    @property
    def provider(self) -> ProviderPort:
        with self._state_lock:
            return self._binding.provider

    @property
    def provider_binding(self) -> CodingProviderBinding:
        with self._state_lock:
            return self._binding

    @property
    def provider_name(self) -> str:
        with self._state_lock:
            return self._binding.provider_name

    @property
    def model_id(self) -> str:
        with self._state_lock:
            return self._binding.model_id

    @property
    def messages(self) -> tuple[AgentMessage, ...]:
        with self._state_lock:
            return self._messages

    @property
    def compaction_suffix(self) -> str:
        with self._state_lock:
            return self._compaction_suffix

    @property
    def provider_failure(self) -> AgentFailure | None:
        return self._provider_failure

    @property
    def user_turn_count(self) -> int:
        return self._user_turn_count

    @property
    def tool_invocation_count(self) -> int:
        return self._tool_invocation_count

    @property
    def resource_invocation_count(self) -> int:
        return self._resource_invocation_count

    @property
    def malformed_argument_count(self) -> int:
        return self._malformed_argument_count

    @property
    def consecutive_malformed_streak(self) -> int:
        return self._consecutive_malformed_streak

    @property
    def budget_exhausted_count(self) -> int:
        return self._budget_exhausted_count

    @property
    def file_reference_count(self) -> int:
        return self._file_reference_count

    @property
    def file_reference_loaded_count(self) -> int:
        return self._file_reference_loaded_count

    @property
    def file_reference_failed_count(self) -> int:
        return self._file_reference_failed_count

    @property
    def image_attachment_count(self) -> int:
        return self._image_attachment_count

    @property
    def image_attachment_loaded_count(self) -> int:
        return self._image_attachment_loaded_count

    @property
    def image_attachment_failed_count(self) -> int:
        return self._image_attachment_failed_count

    @property
    def compaction_count(self) -> int:
        with self._state_lock:
            return self._compaction_count

    @property
    def compaction_dropped_group_count(self) -> int:
        with self._state_lock:
            return self._compaction_dropped_group_count

    @property
    def usage(self) -> AgentUsage:
        with self._state_lock:
            return self._usage_accumulator.agent_usage()

    def usage_snapshot(self) -> CodingSessionUsageSnapshot:
        """Return immutable footer/status inputs without exposing mutation.

        Read under the lock so a rebind replacing the accumulator cannot land
        between the fields of one snapshot.
        """

        with self._state_lock:
            return CodingSessionUsageSnapshot(
                usage=self._usage_accumulator.agent_usage(),
                last_total_tokens=self._usage_accumulator.last_total_tokens,
                cache_hit_percent=self._usage_accumulator.cache_hit_percent,
            )

    def begin_run(
        self,
        *,
        provider_name: str,
        model_id: str,
        usage_accumulator: AgentUsageAccumulator,
    ) -> None:
        """Reset run-lifetime state while retaining the current provider port."""

        binding = CodingProviderBinding(
            self._binding.provider,
            provider_name,
            model_id,
        )
        accumulator = _require_usage_accumulator(usage_accumulator)
        with self._state_lock:
            self._binding = binding
            self._usage_accumulator = accumulator
            self._messages = ()
            self._user_turn_count = 0
            self._tool_invocation_count = 0
            self._resource_invocation_count = 0
            self._malformed_argument_count = 0
            self._consecutive_malformed_streak = 0
            self._budget_exhausted_count = 0
            self._file_reference_count = 0
            self._file_reference_loaded_count = 0
            self._file_reference_failed_count = 0
            self._image_attachment_count = 0
            self._image_attachment_loaded_count = 0
            self._image_attachment_failed_count = 0
            self._compaction_suffix = ""
            self._compaction_count = 0
            self._compaction_dropped_group_count = 0
            self._provider_failure = None

    def prepare_reload_refresh(
        self, provider: ProviderPort
    ) -> CodingReloadBindingValue:
        """Prepare the expected and replacement same-context binding."""

        _require_provider(provider)
        with self._state_lock:
            expected = self._binding
            return CodingReloadBindingValue(
                expected=expected,
                replacement=CodingProviderBinding(
                    provider,
                    expected.provider_name,
                    expected.model_id,
                ),
            )

    def prepare_reload_rebind(
        self,
        provider: ProviderPort,
        *,
        provider_name: str,
        model_id: str,
    ) -> CodingReloadRebindState:
        """Prepare fallback binding and immutable empty history off to the side."""

        replacement = CodingProviderBinding(provider, provider_name, model_id)
        with self._state_lock:
            binding = CodingReloadBindingValue(
                expected=self._binding,
                replacement=replacement,
            )
        return CodingReloadRebindState(
            binding=binding,
            history=CodingReloadHistoryValue(()),
        )

    def prepare_model_mutation(
        self,
        provider: ProviderPort,
        *,
        expected_binding: CodingProviderBinding,
        provider_name: str,
        model_id: str,
        usage_accumulator: AgentUsageAccumulator,
    ) -> CodingModelMutation:
        """Prepare a complete model rebind without reading or changing live state."""

        if type(expected_binding) is not CodingProviderBinding:
            raise TypeError("expected_binding must be an exact CodingProviderBinding")
        return CodingModelMutation(
            expected_binding=expected_binding,
            replacement_binding=CodingProviderBinding(
                provider, provider_name, model_id
            ),
            replacement_usage=_require_usage_accumulator(usage_accumulator),
        )

    def model_mutation_matches_expected(self, prepared: CodingModelMutation) -> bool:
        """Check exact binding identity under the shared session mutex."""

        with self._state_lock:
            return self._binding is prepared.expected_binding

    def publish_model_mutation(self, prepared: CodingModelMutation) -> None:
        """Publish the prevalidated binding/history/usage by assignments only."""

        with self._state_lock:
            self._binding = prepared.replacement_binding
            self._messages = ()
            self._usage_accumulator = prepared.replacement_usage

    def prepare_reload_usage_refresh(self) -> agent_usage.AgentUsageRefreshValue:
        """Prepare exact retained usage through its accumulator owner."""

        with self._state_lock:
            return self._usage_accumulator.prepare_reload_value_refresh()

    def prepare_reload_usage_fallback(
        self, replacement_prototype: AgentUsageAccumulator
    ) -> agent_usage.AgentUsageFallbackValue:
        """Prepare an owner-built replacement from a cleared pricing prototype."""

        accumulator = _require_usage_accumulator(replacement_prototype)
        with self._state_lock:
            return self._usage_accumulator.prepare_reload_value_fallback(accumulator)

    def reload_binding_matches_expected(
        self, binding: CodingReloadBindingValue
    ) -> bool:
        """Report whether the prepared binding still matches live owner state."""

        with self._state_lock:
            return self._binding == binding.expected

    def publish_reload_refresh(self, binding: CodingReloadBindingValue) -> None:
        """Publish only the binding owned by a same-context refresh."""

        with self._state_lock:
            self._binding = binding.replacement

    def publish_reload_rebind(
        self,
        *,
        binding: CodingReloadBindingValue,
        history: CodingReloadHistoryValue,
    ) -> None:
        """Publish only the binding and history owned by fallback rebind."""

        with self._state_lock:
            self._binding = binding.replacement
            self._messages = history.messages

    def reload_usage_matches_expected(
        self, value: agent_usage.AgentUsageReloadValue
    ) -> bool:
        """Check fallback owner identity and replacement clearedness, not counters."""

        with self._state_lock:
            return self._usage_accumulator.reload_value_matches_expected(value)

    def publish_reload_usage_refresh(
        self, value: agent_usage.AgentUsageRefreshValue
    ) -> None:
        """Retain live usage through its owner under the shared session mutex."""

        with self._state_lock:
            self._usage_accumulator.publish_reload_value_refresh(value)

    def publish_reload_usage_fallback(
        self, value: agent_usage.AgentUsageFallbackValue
    ) -> None:
        """Install the owner-built fallback accumulator under the shared mutex."""

        with self._state_lock:
            self._usage_accumulator = value.replacement

    def refresh_provider(self, provider: ProviderPort) -> None:
        """Replace a same-context provider port while retaining all state."""

        with self._state_lock:
            self._binding = CodingProviderBinding(
                provider,
                self._binding.provider_name,
                self._binding.model_id,
            )

    def mark_provider_unavailable(self, provider: ProviderPort) -> None:
        """Bind an unavailable port while retaining labels and live context."""

        with self._state_lock:
            self._binding = CodingProviderBinding(
                provider,
                self._binding.provider_name,
                self._binding.model_id,
            )

    def rebind_provider(
        self,
        provider: ProviderPort,
        *,
        provider_name: str,
        model_id: str,
        usage_accumulator: AgentUsageAccumulator,
    ) -> None:
        """Atomically switch context, clearing history and resetting usage.

        The compaction suffix deliberately survives this transition to preserve
        the product's currently characterized provider/auth/reload behavior.
        """

        binding = CodingProviderBinding(provider, provider_name, model_id)
        accumulator = _require_usage_accumulator(usage_accumulator)
        # Binding, usage, and history move together or not at all: a reader
        # must never see the new provider paired with the old usage.
        with self._state_lock:
            self._binding = binding
            self._usage_accumulator = accumulator
            self._messages = ()

    def append_message(self, message: AgentMessage) -> None:
        """Append the exact canonical message object to live history."""

        require_exact_agent_message(message, "message")
        with self._state_lock:
            self._messages += (message,)

    def mirror_history(self, messages: tuple[AgentMessage, ...]) -> None:
        """Mirror an agent-loop history without changing compaction metadata."""

        require_exact_agent_messages(messages)
        with self._state_lock:
            self._messages = messages

    def clear_history(self) -> None:
        """Clear live history without changing compaction metadata."""

        with self._state_lock:
            self._messages = ()

    def rebuild_history(self, messages: tuple[AgentMessage, ...]) -> None:
        """Replace history from product persistence and clear its live suffix."""

        require_exact_agent_messages(messages)
        with self._state_lock:
            self._messages = messages
            self._compaction_suffix = ""

    def sync_tool_policy(self, state: AgentToolPolicyState) -> None:
        """Mirror the exact reusable-loop cumulative tool counters."""

        (
            tool_invocation_count,
            malformed_argument_count,
            consecutive_malformed_streak,
            budget_exhausted_count,
        ) = _validated_tool_policy_counters(state)
        self._tool_invocation_count = tool_invocation_count
        self._malformed_argument_count = malformed_argument_count
        self._consecutive_malformed_streak = consecutive_malformed_streak
        self._budget_exhausted_count = budget_exhausted_count

    def record_input_accepted(self) -> None:
        self._user_turn_count += 1

    def record_resource_invocation(self) -> None:
        self._resource_invocation_count += 1

    def record_file_references(
        self,
        *,
        reference_count: int,
        loaded_count: int,
        failed_count: int,
    ) -> None:
        """Accumulate one file-reference resolution result."""

        _validate_resolution_counts(
            reference_count,
            loaded_count,
            failed_count,
            prefix="file_reference",
        )
        self._file_reference_count += reference_count
        self._file_reference_loaded_count += loaded_count
        self._file_reference_failed_count += failed_count

    def record_image_attachments(
        self,
        *,
        attachment_count: int,
        loaded_count: int,
        failed_count: int,
    ) -> None:
        """Accumulate one image-attachment resolution result."""

        _validate_resolution_counts(
            attachment_count,
            loaded_count,
            failed_count,
            prefix="image_attachment",
        )
        self._image_attachment_count += attachment_count
        self._image_attachment_loaded_count += loaded_count
        self._image_attachment_failed_count += failed_count

    def absorb_usage(self, sample: AgentProviderUsageSample) -> None:
        if type(sample) is not AgentProviderUsageSample:
            raise TypeError("sample must be an exact AgentProviderUsageSample")
        for field_name in (
            "input_tokens",
            "output_tokens",
            "reasoning_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
            "total_tokens",
        ):
            _require_non_negative_int(
                getattr(sample, field_name),
                f"sample.{field_name}",
            )
        _require_non_negative_int(
            sample.effective_total_tokens,
            "sample.effective_total_tokens",
        )
        with self._state_lock:
            self._usage_accumulator.absorb(sample)

    def apply_compaction(
        self,
        messages: tuple[AgentMessage, ...],
        *,
        summary_suffix: str,
        dropped_group_count: int,
    ) -> None:
        """Apply one changed compaction result as a single state transition."""

        require_exact_agent_messages(messages)
        if type(summary_suffix) is not str:
            raise TypeError("summary_suffix must be an exact string")
        if not summary_suffix:
            raise ValueError("summary_suffix must not be empty")
        _require_non_negative_int(dropped_group_count, "dropped_group_count")
        if dropped_group_count == 0:
            raise ValueError("dropped_group_count must be positive")
        with self._state_lock:
            self._messages = messages
            self._compaction_suffix = summary_suffix
            self._compaction_count += 1
            self._compaction_dropped_group_count += dropped_group_count

    def record_provider_failure(self, failure: AgentFailure) -> None:
        _require_agent_failure(failure, "failure")
        self._provider_failure = failure

    def clear_provider_failure(self) -> None:
        self._provider_failure = None

    def result_snapshot(self) -> CodingSessionResultSnapshot:
        """Return a recursively validated immutable projection.

        Built under the lock so binding, history, and usage in one snapshot
        always come from the same generation.
        """

        with self._state_lock:
            return self._result_snapshot_locked()

    def _result_snapshot_locked(self) -> CodingSessionResultSnapshot:
        return CodingSessionResultSnapshot(
            provider_name=self._binding.provider_name,
            model_id=self._binding.model_id,
            messages=self._messages,
            usage=self._usage_accumulator.agent_usage(),
            user_turn_count=self._user_turn_count,
            tool_invocation_count=self._tool_invocation_count,
            resource_invocation_count=self._resource_invocation_count,
            malformed_argument_count=self._malformed_argument_count,
            consecutive_malformed_streak=self._consecutive_malformed_streak,
            budget_exhausted_count=self._budget_exhausted_count,
            file_reference_count=self._file_reference_count,
            file_reference_loaded_count=self._file_reference_loaded_count,
            file_reference_failed_count=self._file_reference_failed_count,
            image_attachment_count=self._image_attachment_count,
            image_attachment_loaded_count=self._image_attachment_loaded_count,
            image_attachment_failed_count=self._image_attachment_failed_count,
            compaction_suffix=self._compaction_suffix,
            compaction_count=self._compaction_count,
            compaction_dropped_group_count=self._compaction_dropped_group_count,
            provider_failure=self._provider_failure,
        )


def _require_provider(provider: object) -> None:
    if not isinstance(provider, ProviderPort):
        raise TypeError("provider must implement ProviderPort")


def _require_usage_accumulator(
    accumulator: AgentUsageAccumulator,
) -> AgentUsageAccumulator:
    if not isinstance(accumulator, AgentUsageAccumulator):
        raise TypeError("usage_accumulator must be an AgentUsageAccumulator")
    return accumulator


def _require_non_empty_string(value: object, field_name: str) -> None:
    if type(value) is not str:
        raise TypeError(f"{field_name} must be an exact string")
    if not value:
        raise ValueError(f"{field_name} must not be empty")


def _require_non_negative_int(value: object, field_name: str) -> None:
    if type(value) is not int:
        raise TypeError(f"{field_name} must be an exact integer")
    if value < 0:
        raise ValueError(f"{field_name} must not be negative")


def _validated_tool_policy_counters(
    state: AgentToolPolicyState,
) -> tuple[int, int, int, int]:
    if type(state) is not AgentToolPolicyState:
        raise TypeError("state must be an exact AgentToolPolicyState")
    tool_budget = state.tool_budget
    malformed_limit = state.malformed_limit
    invocations_this_turn = state.invocations_this_turn
    tool_invocation_count = state.tool_invocation_count
    malformed_argument_count = state.malformed_argument_count
    consecutive_malformed_streak = state.consecutive_malformed_streak
    budget_exhausted_count = state.budget_exhausted_count
    for field_name, value in (
        ("tool_budget", tool_budget),
        ("malformed_limit", malformed_limit),
        ("invocations_this_turn", invocations_this_turn),
        ("tool_invocation_count", tool_invocation_count),
        ("malformed_argument_count", malformed_argument_count),
        ("consecutive_malformed_streak", consecutive_malformed_streak),
        ("budget_exhausted_count", budget_exhausted_count),
    ):
        _require_non_negative_int(value, f"state.{field_name}")
    if not 1 <= tool_budget <= MAX_AGENT_TOOL_BUDGET:
        raise ValueError(
            f"state.tool_budget must be between 1 and {MAX_AGENT_TOOL_BUDGET}"
        )
    if malformed_limit != 3:
        raise ValueError("state.malformed_limit must be 3")
    if invocations_this_turn > tool_budget:
        raise ValueError(
            "state.invocations_this_turn must not exceed state.tool_budget"
        )
    if consecutive_malformed_streak > malformed_argument_count:
        raise ValueError(
            "state.consecutive_malformed_streak must not exceed "
            "state.malformed_argument_count"
        )
    return (
        tool_invocation_count,
        malformed_argument_count,
        consecutive_malformed_streak,
        budget_exhausted_count,
    )


def require_exact_agent_message(message: object, field_name: str) -> None:
    """Reject noncanonical or recursively mutable product messages."""

    if type(message) not in _EXACT_AGENT_MESSAGE_TYPES:
        raise TypeError(f"{field_name} must be an exact canonical AgentMessage")
    if type(message) is AgentUserMessage:
        _require_user_message(message, field_name)
        return
    if type(message) is AgentAssistantMessage:
        _require_assistant_message(message, field_name)
        return
    if not isinstance(message, AgentToolResultMessage):
        raise TypeError(f"{field_name} must be an exact canonical AgentMessage")
    _require_tool_result_message(message, field_name)


def _require_user_message(message: AgentUserMessage, field_name: str) -> None:
    _require_product_content(message.content, f"{field_name}.content")
    if len(message.content.value) > message.CONTENT_MAX_LENGTH:
        raise ValueError(f"{field_name}.content exceeds its maximum length")


def _require_assistant_message(
    message: AgentAssistantMessage,
    field_name: str,
) -> None:
    _require_product_content(message.content, f"{field_name}.content")
    if len(message.content.value) > message.CONTENT_MAX_LENGTH:
        raise ValueError(f"{field_name}.content exceeds its maximum length")
    if type(message.tool_calls) is not tuple:
        raise TypeError(f"{field_name}.tool_calls must be an exact tuple")
    for index, call in enumerate(message.tool_calls):
        _require_tool_call(call, f"{field_name}.tool_calls[{index}]")


def _require_tool_result_message(
    message: AgentToolResultMessage,
    field_name: str,
) -> None:
    _require_non_empty_string(message.tool_request_id, f"{field_name}.tool_request_id")
    if not message.tool_request_id.startswith(AGENT_TOOL_REQUEST_ID_PREFIX):
        raise ValueError(f"{field_name}.tool_request_id must be pipy-owned")
    _require_non_empty_string(message.tool_name, f"{field_name}.tool_name")
    _require_product_content(message.content, f"{field_name}.content")
    if len(message.content.value) > message.CONTENT_MAX_LENGTH:
        raise ValueError(f"{field_name}.content exceeds its maximum length")
    _require_non_empty_string(
        message.provider_correlation_id,
        f"{field_name}.provider_correlation_id",
    )
    if type(message.is_error) is not bool:
        raise TypeError(f"{field_name}.is_error must be an exact bool")
    if type(message.added_tool_names) is not tuple:
        raise TypeError(f"{field_name}.added_tool_names must be an exact tuple")
    for index, name in enumerate(message.added_tool_names):
        _require_non_empty_string(name, f"{field_name}.added_tool_names[{index}]")


def _require_tool_call(call: object, field_name: str) -> None:
    if type(call) is not AgentToolCall:
        raise TypeError(f"{field_name} must be an exact AgentToolCall")
    _require_non_empty_string(
        call.provider_correlation_id,
        f"{field_name}.provider_correlation_id",
    )
    _require_non_empty_string(call.tool_name, f"{field_name}.tool_name")
    _require_product_content(call.arguments_json, f"{field_name}.arguments_json")


def _require_product_content(content: object, field_name: str) -> None:
    if type(content) is not ProductContent:
        raise TypeError(f"{field_name} must be an exact ProductContent")
    if type(content.value) is not str:
        raise TypeError(f"{field_name}.value must be an exact string")


def _require_agent_usage(usage: object, field_name: str) -> None:
    if type(usage) is not AgentUsage:
        raise TypeError(f"{field_name} must be an exact AgentUsage")
    for counter_name in (
        "input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
    ):
        _require_non_negative_int(
            getattr(usage, counter_name),
            f"{field_name}.{counter_name}",
        )
    if type(usage.cost_usd) not in (int, float):
        raise TypeError(f"{field_name}.cost_usd must be an exact numeric value")
    if not isfinite(usage.cost_usd) or usage.cost_usd < 0:
        raise ValueError(f"{field_name}.cost_usd must be finite and nonnegative")


def _require_agent_failure(failure: object, field_name: str) -> None:
    if type(failure) is not AgentFailure:
        raise TypeError(f"{field_name} must be an exact AgentFailure or None")
    _require_non_empty_string(failure.error_type, f"{field_name}.error_type")
    _require_product_content(failure.message, f"{field_name}.message")
    if type(failure.retryable) is not bool:
        raise TypeError(f"{field_name}.retryable must be an exact bool")


def require_exact_agent_messages(
    messages: object,
    field_name: str = "messages",
) -> None:
    """Reject non-tuple or recursively invalid canonical history."""

    if type(messages) is not tuple:
        raise TypeError(f"{field_name} must be an exact tuple")
    for index, message in enumerate(messages):
        require_exact_agent_message(message, f"{field_name}[{index}]")


def _validate_resolution_counts(
    total_count: int,
    loaded_count: int,
    failed_count: int,
    *,
    prefix: str,
) -> None:
    _require_non_negative_int(total_count, f"{prefix}_count")
    _require_non_negative_int(loaded_count, f"{prefix}_loaded_count")
    _require_non_negative_int(failed_count, f"{prefix}_failed_count")
    _require_resolution_totals(
        total_count,
        loaded_count,
        failed_count,
        prefix=prefix,
    )


def _require_resolution_totals(
    total_count: int,
    loaded_count: int,
    failed_count: int,
    *,
    prefix: str,
) -> None:
    if loaded_count + failed_count > total_count:
        raise ValueError(f"{prefix} loaded and failed counts must not exceed its total")
