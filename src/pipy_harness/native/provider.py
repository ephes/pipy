"""Provider port for the native pipy runtime."""

from __future__ import annotations

from typing import Protocol

from pipy_harness.native.models import ProviderRequest, ProviderResult


class ProviderPort(Protocol):
    """Minimal provider boundary used by the native runtime bootstrap."""

    @property
    def name(self) -> str:
        """Provider name stored as safe metadata."""

    @property
    def model_id(self) -> str:
        """Model identifier stored as safe metadata."""

    def complete(self, request: ProviderRequest) -> ProviderResult:
        """Complete one native turn."""
