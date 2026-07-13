# RPC thinking-level propagation parity plan

Gap: `--mode rpc` accepts `set_thinking_level` / `cycle_thinking_level` but only records the level in the transport; the next provider turn is still constructed with the adapter's previous provider state.

Pi reference:

- `packages/coding-agent/src/modes/rpc/rpc-mode.ts` (`set_thinking_level` and `cycle_thinking_level` cases) dispatches `set_thinking_level` to `session.setThinkingLevel(command.level)` and `cycle_thinking_level` to `session.cycleThinkingLevel()`.
- `packages/coding-agent/src/core/agent-session.ts` (`setThinkingLevel` and `cycleThinkingLevel`) mutates `agent.state.thinkingLevel`, appends a thinking-level change, emits `thinking_level_changed`, and returns the cycled level; `cycleThinkingLevel()` returns `undefined` when the current model does not support thinking.
- `packages/coding-agent/src/core/agent-session.ts` (provider request construction in `runAgentLoop`) passes `this.thinkingLevel` into provider request construction.

Pipy scope:

- Keep this a narrow RPC automation slice. Do not implement live model switching or broader provider registry changes.
- When a `PipyNativeToolReplAdapter` has a `NativeReplProviderState`, the RPC thinking commands must update that shared state's `thinking_level` before the next prompt is delivered. `NativeReplProviderState.current_provider()` already passes `thinking_level` into `resolve_construction(...)`, so rebinding through that boundary matches the existing TUI/settings behavior.
- When the adapter has only an injected provider and no provider state (the deterministic fake-provider test path), keep the current recorded/get_state behavior because there is no construction boundary to mutate.
- Response/event shape remains unchanged: no-payload success for `set_thinking_level`, `{level}` for `cycle_thinking_level`, and a `thinking_level_changed` event when accepted.
- Pipy's existing accepted levels include `xhigh`; do not change catalog/model-level clamping in this slice.

Done when:

1. Focused RPC tests prove `set_thinking_level` and `cycle_thinking_level` update a provider-state-backed adapter's `thinking_level` before the next provider construction.
2. Existing RPC fake-provider behavior remains deterministic.
3. `docs/automation-rpc.md`, `docs/backlog.md`, and `docs/pi-mono-gap-audit.md` no longer describe RPC thinking-level propagation as deferred.

Implementation notes:

- `NativeRpcServer._set_thinking_level(...)` updates both the transport-local
  level and `adapter.provider_state.thinking_level` when the adapter exposes a
  `NativeReplProviderState`.
- The focused tests use a catalog-backed provider state and assert that
  `adapter._current_provider()` constructs a provider with the selected
  `reasoning_effort`, proving the next provider construction sees the RPC level.
