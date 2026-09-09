"""Public synchronous product embedding and one-shot harness compatibility.

``create_product_session`` constructs the full-content native coding lifetime
with an explicitly supplied tool-capable provider. ``open_product_session``
reopens one exact durable native-session file under an explicit workspace. Their
construction-thread APIs support repeated submissions, canonical observation,
idle immutable snapshots, cross-thread cancellation and explicit disposal
without a workflow archive.

``run_native`` and ``make_native_run_request`` retain their separate one-shot,
metadata-first compatibility semantics, including a default fake provider and
finalized archive record. See ``docs/sdk.md`` for both surfaces and ownership.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from pipy_harness.adapters import PipyNativeAdapter
from pipy_harness.capture import CapturePolicy
from pipy_harness.models import HarnessStatus, RunRequest, RunResult
from pipy_harness.native.agent import AgentEvent, AgentEventSink
from pipy_harness.native.coding.state import CodingSessionResultSnapshot
from pipy_harness.native.fake import FakeNativeProvider
from pipy_harness.native.provider import ProviderPort, StreamChunkSink
from pipy_harness.native.workspace_context import (
    default_workspace_instruction_loader,
)
from pipy_harness.product_api import (
    ProductSession,
    create_product_session,
    open_product_session,
)
from pipy_harness.runner import HarnessRunner

__all__ = [
    "AgentEvent",
    "AgentEventSink",
    "CodingSessionResultSnapshot",
    "ProductSession",
    "create_product_session",
    "open_product_session",
    "CapturePolicy",
    "DEFAULT_NATIVE_AGENT",
    "DEFAULT_NATIVE_SLUG",
    "HarnessRunner",
    "HarnessStatus",
    "ProviderPort",
    "RunRequest",
    "RunResult",
    "StreamChunkSink",
    "make_native_run_request",
    "run_native",
]


DEFAULT_NATIVE_AGENT: Final[str] = "pipy-native"
DEFAULT_NATIVE_SLUG: Final[str] = "sdk-native"


def make_native_run_request(
    *,
    goal: str,
    cwd: Path,
    slug: str = DEFAULT_NATIVE_SLUG,
    root: Path | None = None,
    native_provider: str | None = None,
    native_model: str | None = None,
    record_file_paths: bool = False,
) -> RunRequest:
    """Build a `RunRequest` pre-filled with pipy-native defaults."""

    if not goal:
        raise ValueError("pipy-native run requests require a non-empty goal")
    if not isinstance(cwd, Path):
        raise TypeError("cwd must be a Path")
    return RunRequest(
        agent=DEFAULT_NATIVE_AGENT,
        slug=slug,
        command=[],
        cwd=cwd,
        goal=goal,
        root=root,
        capture_policy=CapturePolicy(record_file_paths=record_file_paths),
        native_provider=native_provider,
        native_model=native_model,
    )


def run_native(
    request: RunRequest,
    *,
    provider: ProviderPort | None = None,
    stream_sink: StreamChunkSink | None = None,
) -> RunResult:
    """Run one pipy-native turn and return the finalized `RunResult`.

    `provider` defaults to a deterministic `FakeNativeProvider`
    suitable for tests; supply a real adapter for production use.
    `stream_sink`, when given, is threaded through to the provider's
    `complete(...)` call and the resulting buffered final text is
    not re-printed by the adapter — see the Streaming Output Parity
    Track for the contract.
    """

    if request.agent != DEFAULT_NATIVE_AGENT:
        raise ValueError(
            f"run_native requires --agent {DEFAULT_NATIVE_AGENT}; got {request.agent}"
        )
    chosen_provider: ProviderPort = provider or FakeNativeProvider(
        model_id=request.native_model or "fake-native-bootstrap"
    )
    adapter = PipyNativeAdapter(
        provider=chosen_provider,
        instruction_loader=default_workspace_instruction_loader,
        stream_sink=stream_sink,
    )
    return HarnessRunner(adapter=adapter).run(request)
