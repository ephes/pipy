"""Provider port for the native pipy runtime."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from pipy_harness.native.cancellation import CancelToken
from pipy_harness.native.models import ProviderRequest, ProviderResult

StreamChunkSink = Callable[[str], None]
"""Synchronous text chunk sink used by the Streaming Output Parity Track.

Real adapters that advertise streaming push provider-emitted assistant
text deltas through this callable as they arrive, before the final
buffered `ProviderResult` is returned. The sink owns its own
backpressure, encoding, and buffering; the provider is only responsible
for forwarding the delta string. See `docs/pi-parity.md`
(`Streaming Output Parity Track`) for the parity bar and the
opt-in/opt-out semantics.
"""


@dataclass(frozen=True, slots=True)
class ProviderAttemptAllowance:
    """The caller-owned ordinal and fixed bound for one logical attempt."""

    attempt: int
    max_attempts: int

    def __post_init__(self) -> None:
        for name, value in (
            ("attempt", self.attempt),
            ("max_attempts", self.max_attempts),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"ProviderAttemptAllowance.{name} must be an int")
            if not 1 <= value <= 10:
                raise ValueError(
                    f"ProviderAttemptAllowance.{name} must be between 1 and 10"
                )
        if self.attempt > self.max_attempts:
            raise ValueError(
                "ProviderAttemptAllowance.attempt must not exceed max_attempts"
            )


@runtime_checkable
class PreparedProviderCompletion(Protocol):
    """Request-local handle that executes one logical provider attempt."""

    def complete_attempt(self, allowance: ProviderAttemptAllowance) -> ProviderResult:
        """Execute exactly one logical attempt under ``allowance``."""


@runtime_checkable
class PreparedProviderPort(Protocol):
    """Optional capability for preparation shared by caller-managed attempts."""

    def prepare_completion(
        self,
        request: ProviderRequest,
        *,
        stream_sink: StreamChunkSink | None = None,
        reasoning_sink: StreamChunkSink | None = None,
        cancel_token: CancelToken | None = None,
    ) -> PreparedProviderCompletion | None:
        """Prepare one request-local completion, or decline the capability."""


def apply_provider_headers(
    request: ProviderRequest,
    headers: Mapping[str, str],
) -> dict[str, str]:
    """Apply one request-local extension header callback to copied headers.

    ``None`` is the Pi-compatible deletion sentinel. Non-string values cannot
    cross pipy's HTTP boundary and are dropped defensively. With no callback,
    this is only a shallow copy of the adapter's existing header mapping.
    """

    mutable_headers: dict[str, str | None] = dict(headers)
    callback = request.provider_header_callback
    if callback is not None:
        callback(mutable_headers)
    return {
        name: value
        for name, value in mutable_headers.items()
        if isinstance(name, str) and isinstance(value, str)
    }


@runtime_checkable
class ProviderPort(Protocol):
    """Minimal provider boundary used by the native runtime bootstrap."""

    @property
    def name(self) -> str:
        """Provider name stored as safe metadata."""

    @property
    def model_id(self) -> str:
        """Model identifier stored as safe metadata."""

    @property
    def supports_tool_calls(self) -> bool:
        """Whether this provider can emit model-driven tool calls.

        Real adapters (`openai`, `openai-codex`, `openrouter`) start the
        Tool-Loop Parity Track inert: the value is `False` and they never put
        `ProviderToolCall` values on `ProviderResult.tool_calls`. Later
        slices flip this per adapter as the matching response parser lands.
        """

    def complete(
        self,
        request: ProviderRequest,
        *,
        stream_sink: StreamChunkSink | None = None,
        reasoning_sink: StreamChunkSink | None = None,
        cancel_token: CancelToken | None = None,
    ) -> ProviderResult:
        """Complete one native turn.

        When `stream_sink` is supplied and the provider has flipped on
        streaming, the provider invokes it once per emitted assistant
        text delta before returning the buffered `ProviderResult`.
        Providers that have not yet wired streaming accept the keyword
        and ignore it; their existing buffered behavior is unchanged.

        ``reasoning_sink`` mirrors ``stream_sink`` for the model's
        reasoning-summary text (Pi-equivalent to the italic "thinking"
        text the user sees between tool calls). Providers that do not
        expose reasoning summaries ignore the keyword. The reasoning
        text never reaches the metadata archive; only the renderer sees
        it.

        ``cancel_token`` is supplied by the native tool loop during an
        active-turn Escape / Ctrl-C abort. Adapters check it before issuing
        the request and thread it into their HTTP boundary so the in-flight
        connection is closed and :class:`ProviderCancelledError` is raised
        instead of the request running to completion. Adapters that ignore
        the keyword keep their existing behavior but cannot be cancelled
        mid-flight.
        """
