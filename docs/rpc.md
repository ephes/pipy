# RPC Mode

`pipy repl --mode rpc` starts a long-lived Pi-style automation process. The
process reads JSONL requests on stdin, writes JSONL responses on stdout, and also
emits asynchronous session events while agent turns run.

```sh
uv run pipy repl --mode rpc
```

Use RPC mode when a controller needs process isolation plus mid-session control:
prompting, aborting, queueing follow-up input, inspecting state, running bash,
or switching session-related state without driving the terminal UI.

## Framing

- stdin and stdout are LF-delimited JSON objects.
- Each request carries an `id`; the matching response repeats that `id`.
- stdout may also contain asynchronous session events that are not direct
  responses to a request.
- stderr is for diagnostics that are not protocol messages.

The exact command and event contract is maintained in
[Automation & RPC](automation-rpc.md); this page is the user-facing overview.

## Command families

The shipped protocol accepts Pi-shaped commands for:

- prompting and asynchronous prompt execution;
- steering/follow-up queue control and abort;
- model, provider, thinking, and queue-mode controls;
- message, state, stats, and command introspection;
- compaction, retry, session naming, and session operations;
- bash execution through the same bounded shell tool boundary used by the
  product runtime;
- extension-UI request/response plumbing for headless extension integrations.

## Minimal client shape

A controller writes one JSON object per line and reads both responses and async
events:

```python
import json
import subprocess

proc = subprocess.Popen(
    ["uv", "run", "pipy", "repl", "--mode", "rpc"],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
)

# For a runnable state query, see docs/automation-rpc.md `get_state` and
# tests/test_native_automation_rpc.py. Real clients should send one documented
# request object per line, then keep reading because responses and async session
# events share stdout.
```

Use the protocol spec before depending on a specific command payload shape; this
page intentionally avoids duplicating the full RPC type table.

## Choosing JSON, RPC, print, or SDK

- Use [JSON Mode](json.md) for one prompt with a complete event stream.
- Use `--print`/`-p` for one prompt when only final assistant text is needed.
- Use RPC mode for a long-lived out-of-process controller.
- Use the [Python SDK](sdk.md) for in-process Python embedding without JSONL
  subprocess framing.

## Content and privacy

RPC mode is a full-content automation transport. Protocol events and responses
can include user prompts, assistant text, tool-call arguments, tool results, and
bash output. Treat stdout as transcript data. This is separate from the
summary-safe `pipy-session` metadata/catalog utility.
