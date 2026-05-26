"""Provider port for the native pipy runtime."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, runtime_checkable

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
        """
