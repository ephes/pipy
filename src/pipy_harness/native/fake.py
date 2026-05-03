"""Deterministic fake provider for native-runtime tests and smoke runs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from pipy_harness.models import HarnessStatus
from pipy_harness.native.models import ProviderRequest, ProviderResult


@dataclass(frozen=True, slots=True)
class FakeNativeProvider:
    """A deterministic non-AI provider used to exercise the native boundary."""

    model_id: str = "fake-native-bootstrap"
    final_text: str = "pipy native fake provider completed."
    status: HarnessStatus = HarnessStatus.SUCCEEDED

    @property
    def name(self) -> str:
        return "fake"

    def complete(self, request: ProviderRequest) -> ProviderResult:
        started_at = datetime.now(UTC)
        ended_at = datetime.now(UTC)
        final_text = self.final_text if self.status == HarnessStatus.SUCCEEDED else None
        return ProviderResult(
            status=self.status,
            provider_name=self.name,
            model_id=self.model_id,
            started_at=started_at,
            ended_at=ended_at,
            final_text=final_text,
            usage={
                "input_characters": len(request.system_prompt) + len(request.user_prompt),
                "output_characters": len(final_text or ""),
            },
        )
