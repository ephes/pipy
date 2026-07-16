# Extension `agent_settled` Design

Status: proposed parity slice

## Scope

Add exactly Pi's payload-free `agent_settled` lifecycle hook to pipy's Python
extension runtime. The hook fires once after an accepted agent run is truly
idle: the provider/tool loop has ended and no automatic retry, compaction
retry, steering message, follow-up message, extension-scheduled prompt, or
pre-seeded prompt will run next. This slice does not add a second automation
event, durable entry renderers, dynamic tool loading, or package/update CLI
changes.

Pi reference paths:

- `/Users/jochen/src/pi-mono/packages/coding-agent/src/core/extensions/types.ts`
  (`AgentSettledEvent`, `ExtensionAPI.on("agent_settled", ...)`): the event has
  only `type: "agent_settled"` and is documented as firing after the run has
  fully settled with no retry, compaction, or queued continuation remaining.
- `/Users/jochen/src/pi-mono/packages/coding-agent/src/core/agent-session.ts`
  (`_runAgentPrompt`, `_handlePostAgentRun`, `_emitAgentSettled`): Pi drains
  post-run retry/continuation work before awaiting extension handlers, then
  emits the public session event. The `finally` placement makes settlement run
  on failure as well as success.
- `/Users/jochen/src/pi-mono/packages/coding-agent/src/core/extensions/runner.ts`
  (`ExtensionRunner.emit`): handlers run serially in extension/registration
  order, are awaited, and one handler failure does not prevent later handlers.

## Pipy ownership and event shape

`pipy_harness.native.extension_runtime` owns the Python event vocabulary and
existing serial, fail-soft lifecycle dispatch. Add `EVENT_AGENT_SETTLED` to the
existing lifecycle set and reuse `LifecycleEvent(name="agent_settled",
reason=None)`. This preserves the established translated-Python contract:
extension handlers read `event.name`; there is no payload, archive write, or
protocol output.

`pipy_harness.native.tool_loop_session.NativeToolReplSession` owns the provider,
tool, compaction, and queued-message drain. Its `_ExtensionAwareEmitter` may
expose an extension-only `agent_settled()` method, but that method must not call
the shared `AutomationEmitter`. JSON mode and RPC mode already synthesize their
single payload-free protocol `agent_settled` at their own mode-specific idle
boundaries; routing this new hook through the shared automation emitter would
double-emit those protocol events.

## True-idle placement

Track whether a started agent run still needs its settled notification. Mark
settlement pending immediately before dispatching `agent_start`, so an
unexpected provider, tool, or lifecycle exception cannot bypass the eventual
notification. A normal or completed-fatal `agent_end` leaves that marker
pending. At the top of the outer session loop, first drain extension outboxes
and apply the existing queue
precedence (pending local command, TUI steering/follow-up drain, initial prompt
seeds, extension steering/follow-up, then ordinary extension prompts). Fire the
pending hook only when that resolution selects neither a local command nor a
provider-visible queued prompt, immediately before reading fresh input.

If a settled handler itself schedules a new prompt, drain that newly populated
extension outbox before blocking so the prompt starts a new agent run. Clear the
pending marker before dispatch so a failing handler cannot duplicate the
event. The enclosing session `finally` fires any still-pending settlement before
`session_shutdown`; this covers a completed fatal return and an unexpected
mid-run exception, matching Pi's `_runAgentPrompt` `finally`. It does nothing
for a session that never started an agent run.

All four product modes execute this same `NativeToolReplSession` outer loop.
TUI and captured interactive sessions reach the boundary before blocking for
fresh input. Print and JSON sessions enter with a pre-seeded prompt and then an
EOF input stream, so they settle after the seed drains and before that EOF is
read. RPC feeds prompts through its input adapter; it reaches the same boundary
when its queue is empty, independently of the RPC server's mode-owned protocol
event synthesis.

This placement gives these required sequences:

- ordinary run: `agent_start ... agent_end, agent_settled`;
- queued continuation: `agent_start ... agent_end, agent_start ... agent_end,
  agent_settled` (no settlement between runs);
- completed fatal run: `agent_start ... agent_end, agent_settled,
  session_shutdown`;
- unexpected mid-run failure: `agent_start ... agent_settled,
  session_shutdown` (an `agent_end` is not fabricated);
- no agent run: no `agent_settled`;
- JSON/RPC protocol output: unchanged single mode-owned `agent_settled`.

## Verification and documentation

Focused tests will prove registration/dispatch, payload-free event metadata,
serial fail-soft handler order, exactly-once true-idle placement across an
extension-enqueued follow-up, settlement on the completed fatal return, and no
event for an input-only/EOF session. The golden extension conformance fixture
and gate will include an `agent_settled` proof marker without leaking prompt,
provider, tool, or UI bodies. Existing JSON/RPC tests remain the regression
guard against protocol duplication.

Update `docs/extension-api.md`, `docs/backlog.md`, `docs/parity-plan.md`,
`docs/pi-mono-gap-audit.md`, `docs/pi-parity.md`, and `CHANGELOG.md` to mark only
the extension hook shipped and leave durable entry rendering and other named
follow-ons open.

## Done when

1. Extensions can register `@api.on("agent_settled")` and receive one
   payload-free, serial, fail-soft lifecycle callback at true idle in TUI,
   captured/print, JSON, and RPC product sessions.
2. Queued continuations prevent an intermediate callback, while a handler may
   schedule a subsequent run without blocking on stdin.
3. Completed fatal runs settle, sessions with no agent run do not, and JSON/RPC
   automation streams still contain exactly one mode-owned event.
4. Focused tests, conformance gates, `just check`, optional `prek`, and the
   different-family review gate are green over the complete code/docs diff.
