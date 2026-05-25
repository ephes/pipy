"""Provider port for the native pipy runtime."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pipy_harness.native.models import ProviderRequest, ProviderResult


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

    def complete(self, request: ProviderRequest) -> ProviderResult:
        """Complete one native turn."""
