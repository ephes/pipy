# Python SDK and Headless Embedding

Pipy is designed to run both as a CLI/TUI application and as an embeddable
Python agent runtime. If a Python program needs an agentic workflow, it should
link pipy in-process through `pipy_harness.sdk` rather than shelling out to the
`pipy` CLI.

## Current SDK Surface

The current public SDK is intentionally small and one-shot:

- `make_native_run_request(...)` builds a `RunRequest` with `pipy-native`
  defaults.
- `run_native(request, provider=..., stream_sink=...)` runs one native turn,
  finalizes the normal pipy record, and returns a `RunResult`.
- `HarnessRunner`, `ProviderPort`, `StreamChunkSink`, `CapturePolicy`,
  `RunRequest`, `RunResult`, and `HarnessStatus` are re-exported for callers
  that need lower-level composition.

Example:

```python
from pathlib import Path

from pipy_harness.sdk import make_native_run_request, run_native

request = make_native_run_request(
    goal="Summarize the current repository state",
    cwd=Path.cwd(),
)

result = run_native(request)
print(result.status)
```

`run_native(...)` defaults to the deterministic fake provider so tests and smoke
checks do not need network access. Production embeddings should inject a real
`ProviderPort` or compose `HarnessRunner` with a configured native adapter. The
catalog-backed auth/base-URL/header/routing setup used by the CLI and REPL lives
at the provider-construction boundary, so callers that want catalog behavior
should construct or inject the provider through that boundary rather than expect
`run_native(...)` to resolve catalog settings by itself.

## Intended Use

Use the Python SDK when:

- a Python application wants to drive pipy's native runtime directly;
- the caller wants normal Python objects instead of subprocess stdout parsing;
- tests or higher-level orchestrators need to inject fake providers, stream
  sinks, tools, or capture policy;
- process isolation is not required.

The SDK is the in-process headless surface. It is separate from the terminal UI
and should not depend on interactive input streams.

## Current Limits

The stable SDK surface currently covers a single native run. Richer embedding
features are design goals but not yet stabilized as public API:

- multi-turn session objects;
- native session-tree resume, fork, clone, naming, and compaction controls;
- mid-turn steering and cancellation;
- full event-stream callbacks beyond `StreamChunkSink`;
- extension UI bridging.

For now, advanced callers can compose lower-level ports directly, but should
treat those shapes as less stable than the named `pipy_harness.sdk` exports.

## Relationship to JSON/RPC Automation

The shipped Pi-style `--mode json` and `--mode rpc` transports are the
out-of-process headless surfaces. They are specified in
[Automation & RPC](automation-rpc.md) and are intended for non-Python callers or
callers that want process isolation, JSONL framing, asynchronous events, and
mid-turn control.

The SDK and JSON/RPC modes should reuse the same native runtime. JSON/RPC must
not fork a separate product path.

## Privacy and Storage

Embedding pipy through the SDK finalizes the normal pipy record just like the
CLI one-shot path. The metadata-first `pipy-session` archive remains
privacy-conscious by default and does not store prompts, assistant content, tool
payloads, stdout/stderr, or secrets unless a future explicit full-content
surface says otherwise.

The JSON/RPC live transports are different: they are full-content automation
streams by design, while archive privacy remains unchanged.
