# `--mode json` `agent_settled` event — design + plan

Status: shipped 2026-07-14 through the parity loop as a single gap. Gap source:
`docs/pi-mono-gap-audit.md` priority 3 and
`docs/backlog.md` ("JSON-mode emission ... remain separate follow-ons"), plus the
explicit deferral in the shipped RPC slice
(`docs/specs/2026-07-14-rpc-agent-settled-plan.md` "Deferred:
`--mode json` `agent_settled`"). This is the immediate follow-on to the shipped
`--mode rpc` `agent_settled` (commit `4bb4792`).

Reference checkout: `/Users/jochen/src/pi-mono` at `b084d2fb`.

## Scope (one reviewable slice)

Emit the Pi `agent_settled` session event on the pipy `--mode json` one-shot
event stream once the single prompt run settles into true idle, i.e. as the
final line after the run's `agent_end`. No new event fields, no new command, no
RPC change. `--print`/`-p` text output is unchanged (it renders only the final
assistant text and never rendered lifecycle events), and the extension-surface
`agent_settled` hook stays a separate follow-on.

## What Pi does (pinned reference facts)

- **Event shape.** `agent_settled` carries **no payload fields**. The session
  union member is `{ type: "agent_settled" }`
  (`packages/coding-agent/src/core/agent-session.ts:134`).
- **When it fires.** `AgentSession._runAgentPrompt` sets `_isAgentRunActive =
  true`, runs `agent.prompt(...)`, loops `while (_handlePostAgentRun())
  agent.continue()`, and in its `finally` calls `_emitAgentSettled()`
  (`agent-session.ts:1022-1034`). `_emitAgentSettled` sets `_isAgentRunActive =
  false`, emits an extension `agent_settled` then a session `agent_settled`, and
  resolves the idle-wait (`agent-session.ts:534-541`). So **exactly one
  `agent_settled` per top-level agent run**, emitted from a `finally` (so it
  fires even when the run errors), after the run's final `agent_end`, once the
  agent is idle.
- **`--mode json` forwarding.** Pi's print mode subscribes to the session and
  writes **every** event verbatim in json mode:
  `unsubscribe = session.subscribe((event) => { if (mode === "json")
  writeRawStdout(JSON.stringify(event) + "\n"); ... })`
  (`packages/coding-agent/src/modes/print-mode.ts:104-106`). Because the core
  session `_emit`s `agent_settled`, Pi's `--mode json` stream carries it as the
  final line after `agent_end`. (Text/`--print` mode subscribes to the same
  stream but renders only the final assistant text, so `agent_settled` never
  appears in `--print` output.)

**Objective baseline evidence.** The Pi-vs-pipy comparison gate already proves
the divergence: `automation_pi_comparison.py`'s
`event_order_and_discriminators_match` check is currently **red** because the
real Pi `AgentSession` (driven by `pi_faux_event_driver.mts`) ends its json
sequence with `agent_settled` while pipy ends at `agent_end`:

```
pi  =[... 'turn_end', 'agent_end', 'agent_settled']
pipy=[... 'turn_end', 'agent_end']
```

This slice turns that check green.

## How pipy matches it (ownership boundary)

pipy does **not** emit `agent_settled` from the shared tool-loop
`AutomationEmitter` (`src/pipy_harness/native/automation/events.py`). That
emitter is the sink source for *both* modes, and in `--mode rpc` the `RpcServer`
already **synthesizes** `agent_settled` itself at its own emit-interception layer
with queue-aware suppression (`src/pipy_harness/native/automation/rpc.py:274-310`
— it writes `agent_settled` after `agent_end` only when no follow-up/steer run is
reserved). If the emitter emitted its own `agent_settled`, RPC mode would emit
**two** (one from the emitter, one synthesized), regressing the shipped RPC
behavior. Therefore each mode owns its idle boundary:

- **RPC** synthesizes `agent_settled` in `RpcServer.emit` (already shipped).
- **JSON** synthesizes `agent_settled` in the `run_json_mode` one-shot driver
  (`src/pipy_harness/native/automation/run_modes.py`).

This is the faithful boundary: pipy's `--mode json` is a one-shot, single-prompt,
single-threaded driver (`_SinglePromptStream` feeds exactly one turn then EOF;
there is no steering/follow-up queue). The whole run — including any internal
retries and auto-compaction — drains to idle exactly when `_run_oneshot(...)`
returns. So the driver writes one payload-free `{"type": "agent_settled"}` line
through the same `JsonlWriter` after the run, in a `finally` so it still fires if
the run raises (mirroring Pi's `_runAgentPrompt` `finally`). The `JsonlWriter`
already serializes writes under a lock, and no further events arrive after the
synchronous run returns, so the line is deterministically last.

### Fields / optionality pin

| Field | Pi optionality | pipy value |
| --- | --- | --- |
| `type` | required, literal `"agent_settled"` | `"agent_settled"` |
| (any other) | none — Pi emits no other fields | none |

The emitted object is exactly `{"type": "agent_settled"}`, byte-identical to the
RPC slice's line.

## Deferred (explicit, documented)

- **Extension-surface `agent_settled` hook.** Pi's `_emitAgentSettled` also emits
  an *extension* `agent_settled` (`agent-session.ts:536`;
  `core/extensions/types.ts`). pipy's extension lifecycle does not yet fire
  `agent_settled` to extensions in any mode; that stays a named follow-on,
  unchanged by this slice.
- **`--print` lifecycle rendering.** Unchanged: `--print` renders only final
  assistant text.

## Acceptance criteria / done-when

1. `--mode json "<prompt>"` emits exactly one `{"type":"agent_settled"}` line,
   strictly after the run's `agent_end`, as the final stdout line.
2. `agent_settled` carries no payload fields (`== {"type": "agent_settled"}`).
3. A multiline prompt (one turn) still emits exactly one `agent_settled`.
4. `--print`/`-p` output is unchanged (final assistant text only).
5. `--mode rpc` `agent_settled` behavior is unchanged (still exactly one, still
   queue-aware; no double emit).
6. `automation_pi_comparison.py`'s `event_order_and_discriminators_match` goes
   green; `automation_rpc_conformance.py`'s json-mode check asserts the trailing
   `agent_settled`.
7. `just check` green; different-family (Pi) review CLEAN over the full diff.

## Ordered implementation tasks

1. **Emit in `run_json_mode`.** In
   `src/pipy_harness/native/automation/run_modes.py`, wrap `_run_oneshot(adapter,
   cwd)` in `try/finally`; in the `finally` write `{"type": "agent_settled"}`
   through the existing `writer`. Add a comment citing the Pi `finally` boundary.
   Do not touch `run_print_mode`, the `AutomationEmitter`, or `rpc.py`.
2. **Tests** (`tests/test_native_automation_json_mode.py`): update
   `test_run_json_mode_emits_header_then_event_stream` so the expected sequence
   ends `... "agent_end", "agent_settled"`; add a focused test asserting the last
   record is exactly `{"type": "agent_settled"}`, exactly one occurrence, and it
   follows `agent_end`; extend the multiline test to assert one `agent_settled`.
3. **Conformance gate** (`scripts/parity_checks/automation_rpc_conformance.py`):
   in `_check_json_mode_oneshot`, change the trailing assertion from `agent_end`
   to a trailing `agent_settled` immediately after `agent_end`; update the
   module docstring item (12) accordingly.
4. **Docs**: `docs/automation-rpc.md` — extend the canonical `--mode json`
   sequence to end with `agent_settled`, flip the "`--mode rpc` only" /
   "`--mode json` emission is a follow-on" lines to "shipped on both", and scope
   the remaining follow-on to the extension-surface hook only. Strike the
   json-mode `agent_settled` follow-on from `docs/pi-mono-gap-audit.md`,
   `docs/backlog.md`, `docs/parity-plan.md`, `docs/parity-criterion.md`, and
   `docs/pi-parity.md` where they name it, keeping the extension-surface hook as
   the residual. Add a CHANGELOG entry.
