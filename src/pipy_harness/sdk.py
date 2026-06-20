"""Programmatic SDK surface for embedding the pipy harness.

This module is the named SDK entry point for callers that want to
drive `pipy-native` runs from Python without going through the
`pipy` CLI. Pi exposes the equivalent surface as a TypeScript SDK
in `pi-mono/packages/coding-agent/src/core/sdk.ts`; pipy slopforks
the useful subset — `RunRequest` in, `RunResult` out — through
pipy-owned Python boundaries.

The SDK is intentionally narrow today:

- `run_native(request)` runs one `pipy-native` turn through
  `PipyNativeAdapter` with the deterministic fake provider, returns
  a `RunResult`, and finalizes the session record like the CLI
  would. It is meant for tests, smoke checks, and library
  integrations; production callers may inject a real `ProviderPort`
  or compose `HarnessRunner` + a configured native adapter directly.
- `make_native_run_request(...)` is a small factory that fills
  pipy-native defaults so callers don't have to reach into
  `RunRequest` internals.
- The module re-exports the value objects callers need
  (`RunRequest`, `RunResult`, `HarnessStatus`, `CapturePolicy`,
  `HarnessRunner`, `ProviderPort`, `StreamChunkSink`).

The SDK is the in-process headless Python surface. It does not
introduce a new runtime dependency, does not spawn HTTP servers, and
does not perform any I/O at import time. Out-of-process JSON/RPC
automation is specified separately in `docs/automation-rpc.md`; see
`docs/sdk.md` for the embedding overview and current limits.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from pipy_harness.adapters import PipyNativeAdapter
from pipy_harness.capture import CapturePolicy
from pipy_harness.models import HarnessStatus, RunRequest, RunResult
from pipy_harness.native.fake import FakeNativeProvider
from pipy_harness.native.provider import ProviderPort, StreamChunkSink
from pipy_harness.native.workspace_context import (
    default_workspace_instruction_loader,
)
from pipy_harness.runner import HarnessRunner


__all__ = [
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
