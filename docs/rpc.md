# RPC Mode

`pipy repl --mode rpc` starts a long-lived Pi-style automation process. The
process reads JSONL requests on stdin, writes JSONL responses on stdout, and also
emits asynchronous session events while agent turns run.

```sh
uv run pipy repl --mode rpc
```

Use RPC mode when a controller needs process isolation plus mid-session control:
prompting, aborting, queueing follow-up input, inspecting state, running bash,
or naming the current session without driving the terminal UI.

## Framing

- stdin and stdout are LF-delimited JSON objects.
- Requests may carry an `id`; known-command responses repeat it. Unknown-command
  errors omit the input `id`.
- stdout may also contain asynchronous session events that are not direct
  responses to a request.
- stderr is for diagnostics that are not protocol messages.

The exact command and event contract is maintained in
[Automation & RPC](automation-rpc.md); this page is the user-facing overview.

## Command families

The shipped protocol implements:

- prompting and asynchronous prompt execution;
- steering/follow-up queue control and abort;
- live model/thinking controls and reported queue-mode settings;
- message/state introspection and message/tool counters;
- current-session naming;
- bash execution with bounded output through `command_sandbox.run_command`
  and its direct-command policy, separate from the model's `BashTool` policy.

Recognized command names do not imply implemented behavior:

- `compact`, `new_session`, `switch_session`, `fork`, `clone`, and `export_html` return
  correlated not-yet-implemented errors.
- `set_auto_compaction` and `set_auto_retry` only record RPC flags; they do not
  enable or change the coding session's compaction/retry policy. `abort_retry`
  is a no-op.
- Steering and follow-up delivery remains one message per turn boundary, steering
  first, regardless of the reported queue mode.
- Model and thinking controls are idle-only owner operations. Available models
  are locally available, tool-capable catalog rows plus the active custom
  selection. Model switches rebuild the next provider request and reset coding
  history/usage; thinking-only refreshes retain them. `enabledModels` scopes
  model cycling when effective, and model/thinking no-ops emit no event. An
  injected provider remains a static singleton with `off` thinking.
- `get_commands` returns an empty command list. `get_session_stats` counts
  messages and tool calls/results, but token totals and cost are zero placeholders.
- The extension-UI channel is unwired: no `extension_ui_request` is emitted,
  and received `extension_ui_response` lines are accepted and ignored.
- Direct RPC `bash` cannot be externally aborted. `abort_bash` returns an error
  while a command runs and succeeds without action when idle. Timeouts currently
  map to `cancelled: true`; this is not evidence of explicit-abort support.

See [Automation & RPC](automation-rpc.md) for exact response and event contracts.

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
- Use the [Python SDK](sdk.md) for persistent product sessions or one-shot
  compatibility runs without
  JSONL subprocess framing.

## Content and privacy

RPC mode is a full-content automation transport. Protocol events and responses
can include user prompts, assistant text, tool-call arguments, tool results, and
bash output. Treat stdout as transcript data. This is separate from the
summary-safe `pipy-session` metadata/catalog utility.

## Active-run cancellation

`abort` reaches the active provider, semantic-summary or model-tool worker.
Model-driven tools use canonical cancellation and bounded cleanup; a completed
tool result and its effects remain recorded if completion wins the race.
Cancellation discards late success and suppresses new output after retirement,
but cannot undo effects or synchronously stop an uncooperative extension tool.
Idle abort remains harmless and the next accepted prompt can run normally.
The separate RPC `bash` / `abort_bash` command behavior is unchanged: direct bash
still uses the command sandbox and is not externally cancellable.
