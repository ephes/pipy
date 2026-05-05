"""Tool port for the native pipy runtime."""

from __future__ import annotations

from typing import Protocol

from pipy_harness.native.models import NativeToolRequest, NativeToolResult


class ToolPort(Protocol):
    """Minimal tool boundary used by the native runtime bootstrap."""

    @property
    def name(self) -> str:
        """Tool name stored as safe metadata."""

    def invoke(self, request: NativeToolRequest) -> NativeToolResult:
        """Invoke one native tool request."""
