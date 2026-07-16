# RPC `agent_settled` event — design + plan

Status: shipped 2026-07-14 through the parity loop as a single gap. Gap source:
`docs/pi-mono-gap-audit.md` priority 3 ("`agent_settled` remains the one real
RPC follow-on") and `docs/automation-rpc.md` ("Pi's later `agent_settled` event
remains an explicit follow-on rather than part of the baseline").

Reference checkout: `/Users/jochen/src/pi-mono` at `b084d2fb`.

## Scope (one reviewable slice)

Emit the Pi `agent_settled` session event on the pipy `--mode rpc` async event
stream when an agent run settles into true idle. No new RPC command (the
31-command baseline is unchanged); this is an asynchronous session event added to
the emitted vocabulary. `--mode json` is explicitly **out of scope** for this
slice (see "Deferred" below).

## What Pi does (pinned reference facts)

- **Event shape.** `agent_settled` carries **no payload fields**. The union
  member is `{ type: "agent_settled" }`
  (`packages/coding-agent/src/core/agent-session.ts:134`; extension type
  `packages/coding-agent/src/core/extensions/types.ts:710`).
- **When it fires.** `AgentSession._runAgentPrompt` sets `_isAgentRunActive =
  true`, runs `agent.prompt(...)`, then loops `while (_handlePostAgentRun())
  agent.continue()`, and in its `finally` calls `_emitAgentSettled()`
  (`agent-session.ts:1022-1034`). `_emitAgentSettled` sets `_isAgentRunActive =
  false`, emits an extension `agent_settled` then a session `agent_settled`, and
  resolves the idle-wait (`agent-session.ts:534-541`). `_handlePostAgentRun`
  returns `this.agent.hasQueuedMessages()` (`agent-session.ts:1062-1064`), so
  **all** steer/follow-up messages queued during the run are drained *inside the
  same `_runAgentPrompt`* via `agent.continue()` before the single
  `agent_settled`. Net: **exactly one `agent_settled` per top-level agent run,
  emitted after the final `agent_end`, only when the agent has become idle (no
  queued messages remain).**
- **RPC forwarding + use.** `rpc-mode.ts` subscribes to the session and writes
  every event to stdout; on `agent_settled` it triggers
  `checkShutdownRequested()` (`rpc-mode.ts:354-358`). Clients use it to detect
  idle: `RpcClient.waitForIdle` / `collectEvents` resolve on `agent_settled`
  (`rpc-client.ts:445-486`).
- **Ordering.** `agent_settled` is emitted strictly **after** the run's final
  `agent_end` (it is in the `finally` after the prompt/continue loop that emits
  `agent_end`).

## How pipy matches it (pipy-owned boundary)

pipy's RPC server (`src/pipy_harness/native/automation/rpc.py`,
`NativeRpcServer`) already computes the settle boundary. Pi injects steer/
follow-up **in-turn** (one `_runAgentPrompt`); pipy's documented simplification
delivers **one queued message per run boundary** as a *separate* agent run
(`agent_start`/`agent_end`) promoted through `_reserve_next_message`. So the
faithful mapping of Pi's "one `agent_settled` when idle" is: emit it at the
`agent_end` boundary **only when the boundary settles to true idle** — i.e. when
`_reserve_next_message(settled=True)` returns `None` (nothing queued was
promoted to run next). When a queued message *is* reserved, a new run continues,
so — exactly like Pi — no `agent_settled` is emitted between the two runs.

This reuses the idle definition already used by `get_state`
(`isStreaming == self._turn_active`, `rpc.py:686`) and by `_await_drain`
(`rpc.py:381-383`): after `_reserve_next_message(settled=True)` returns `None`,
`self._turn_active` is `False` and both queues are empty — the true-idle
boundary.

### Emission point

In `NativeRpcServer.emit()`, the `agent_end` boundary settles/reserves and
writes both lifecycle lines under a single `self._lock` hold:

```python
self._abort.clear()
with self._lock:
    reserved = self._reserve_next_message_locked(settled=True)
    self._writer.write_line(event)                    # agent_end hits the wire
    if reserved is None:
        self._writer.write_line({"type": "agent_settled"})
if reserved is not None:
    self._deliver(reserved)                           # queue_update + channel push (new run)
```

Writing `agent_settled` after `write_line(event)` guarantees Pi's ordering
(`agent_settled` strictly after `agent_end`). It is only written when `reserved
is None`, so it is never interleaved with the `_deliver` path (which only runs
when `reserved is not None`); the two branches are mutually exclusive.

**Atomicity.** The settle transition, the `agent_end` write, and the
`agent_settled` write must happen under the same `self._lock` hold. `emit()` runs
on the worker thread; a `prompt`/`steer`/`follow_up` runs on the reader thread
and takes `self._lock` to accept a new run (flip `_turn_active`) and then writes
its response. Without the shared hold, a prompt accepted in the window after
`_reserve_next_message(settled=True)` exposes `_turn_active == False` but before
`agent_settled` is written would write its response *between* `agent_end` and
`agent_settled`, stranding a stale `agent_settled` after the new run's acceptance
and misleading `waitForIdle` clients. Holding the lock across the pair blocks
that acceptance until `agent_settled` is on the wire — the same atomicity Pi gets
from emitting the pair synchronously in `_runAgentPrompt`'s `finally`. Any
reserved message is delivered (`_deliver`, which re-takes the lock) only after
the hold is released, so the lock is never re-entered. To keep the settle/reserve
logic single-sourced, `_reserve_next_message` is split into a locked wrapper and
a `_reserve_next_message_locked` core the boundary calls while already holding
the lock. Prompt acceptance must likewise classify the current state and either
enqueue the message or mark a new run active in one lock hold; a split
read-then-append would let settlement occur between those operations and strand
the just-accepted prompt after a false idle event.

Shape: `{"type": "agent_settled"}` with no payload fields (matches Pi exactly).

### Why this boundary, not the tool-loop source

The tool loop (`tool_loop_session.py`) has no knowledge of the RPC steer/
follow-up queue — each promoted queued message arrives as just another prompt on
the input channel, so a source-level emit would fire once *per prompt run* and
would emit an extra `agent_settled` between A and its queued follow-up B, which
Pi does not. The RPC server is the only seam that knows the reserve/idle state,
so it is the correct boundary. This also keeps the change surgical and leaves the
`--mode json` sink untouched.

## Deferred (explicit, documented)

- **`--mode json` `agent_settled`.** Pi's `print-mode.ts` forwards every session
  event in `json` mode, so Pi's json stream also carries `agent_settled` after
  each run. pipy's json mode is a one-shot single-prompt driver with no
  steering/queue concept, and its documented canonical sequence ends at
  `agent_end`. Adding `agent_settled` there is a separate, self-contained change
  (new golden sequence + conformance line) with a different emission seam (the
  tool-loop `AutomationEmitter`, once per prompt run). It is kept as a named
  follow-on so this slice stays a single reviewable RPC change. The docs will
  record json-mode `agent_settled` as the remaining follow-on rather than
  claiming full parity.

## Acceptance criteria / done-when

1. In `--mode rpc`, a single prompt that runs to completion emits exactly one
   `{"type":"agent_settled"}` line, strictly after that run's `agent_end`.
2. When a follow-up (or steer) is reserved at an `agent_end` boundary, **no**
   `agent_settled` is emitted between the two runs; exactly one `agent_settled`
   is emitted after the final (idle) `agent_end`. Deterministically exercised via
   the batch prompt+follow_up+EOF drain pattern
   (`test_batch_eof_drains_queued_followup`), where run A's `agent_end` reserves
   B (no settle) and B's `agent_end` settles idle (one settle).
3. `agent_settled` carries no payload fields.
4. Existing RPC tests still pass (those that `collect_until(agent_end)` stop at
   `agent_end`; the trailing `agent_settled` line is read by later collects and
   does not change their assertions).
5. `just check` green; different-family (Pi) review CLEAN over the full diff.

## Ordered implementation tasks

1. **Emit.** In `NativeRpcServer.emit()`, handle the `agent_end` boundary under a
   single `self._lock` hold: settle/reserve via `_reserve_next_message_locked`,
   write `agent_end`, and write `{"type": "agent_settled"}` when `reserved is
   None` — all inside the hold so a concurrent prompt/steer/follow-up cannot slip
   a new run's acceptance between the pair (see "Atomicity" above). Split
   `_reserve_next_message` into a locked wrapper + `_reserve_next_message_locked`
   core. Comment ties it to Pi's `_emitAgentSettled` idle semantics.
2. **Tests** (`tests/test_native_automation_rpc.py`):
   - `test_agent_settled_emitted_after_idle`: run one prompt; assert an
     `agent_settled` event is emitted and that the immediately preceding
     lifecycle event is `agent_end`; assert the event has no extra keys beyond
     `type`.
   - `test_agent_settled_suppressed_between_queued_runs`: extend the batch
     prompt+follow_up+EOF pattern; collect the full stream; assert exactly one
     `agent_settled`, that it follows the **second** `agent_end`, and that no
     `agent_settled` appears before the second `agent_end`.
   - `test_prompt_racing_agent_end_is_reserved_not_stranded`: use a deterministic
     lock barrier to pause prompt acceptance after its first state-lock hold,
     settle the active run, and prove the prompt was already queued/reserved
     rather than stranded behind a false `agent_settled`.
3. **Docs.**
   - `docs/automation-rpc.md`: add an `agent_settled` row to the session-event
     vocabulary; update the RPC section to state it is emitted at the idle
     boundary; adjust the "explicit follow-on" line so it scopes the remaining
     follow-on to `--mode json` only.
   - `docs/pi-mono-gap-audit.md`: mark RPC `agent_settled` shipped for
     `--mode rpc`; keep json-mode `agent_settled` as the residual follow-on.
   - `docs/backlog.md`: strike the RPC `agent_settled` follow-on from the Next
     Slice / RPC notes accordingly.
4. **Gate + review.** `just check`; then Pi different-family review over the full
   diff until CLEAN; commit.
