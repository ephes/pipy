"""Data-classification marker for the canonical agent boundary.

Agent events carry product content: prompts, model output, reasoning, tool
arguments, and tool results. No summary-safe archive DTO or generic
event-to-archive serializer belongs here; a later workflow adapter must
explicitly allowlist metadata at that boundary.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProductContent:
    """Full-content product/automation data that must not enter the archive."""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str):
            raise TypeError("ProductContent.value must be a string")

    def __repr__(self) -> str:
        """Keep full-content payloads out of diagnostics and assertion diffs."""

        return "ProductContent(<redacted>)"
