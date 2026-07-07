# Plan: Extension `/reload` session_start(reason="reload")

## Gap

Pipy reloads extension code, resources, settings, provider contributions, tools, flags, menu metadata, message renderers, and custom chrome state, but currently does **not** fire the reloaded extensions' `session_start` lifecycle hook after `/reload`. That leaves Pi-style extensions that set persistent chrome only from `session_start` blank after reload. Pi's `AgentSession.reload()` emits `session_shutdown(reason="reload")` before rebuilding extensions, then emits `session_start(reason="reload")` after the new extension runner is bound.

## Pi reference

- `/Users/jochen/src/pi-mono/packages/coding-agent/src/core/agent-session.ts:2493-2513`: `reload()` emits `session_shutdown` with `reason: "reload"`, reloads settings/resources, rebuilds runtime/extension runner, then emits `{ type: "session_start", reason: "reload" }` and re-extends resources.
- `/Users/jochen/src/pi-mono/packages/coding-agent/src/core/extensions/types.ts:549`: `SessionStartEvent` has `type: "session_start"` and a `reason` discriminator.

## Pipy target

After successful extension reactivation in the product TUI `/reload` path, pipy should dispatch the new generation's `session_start` hooks once with `LifecycleEvent(name="session_start", reason="reload")`, using the same live UI driver, notify sink, flags, and control setters as other lifecycle contexts. This happens after the reloaded flag values are parsed/applied and after provider/tool/renderer/menu state has been rebound enough for a `session_start` hook to set chrome through the live UI driver. It should remain fail-soft like existing lifecycle dispatch and trigger no provider turn.

## Scope constraints

- Single slice: only product `/reload` lifecycle parity for `session_start(reason="reload")`.
- Do not change startup `session_start` semantics, `session_shutdown`, resource extension, or package source policy.
- Preserve pipy's current fail-soft lifecycle behavior and local trust boundary.
- Keep docs and conformance coverage aligned; remove the current doc note that says session-start-only chrome stays blank after reload.

## Done-when

1. A focused test proves an extension that sets chrome/status in `session_start` is invoked with `reason == "reload"` after the reloaded generation is installed.
2. The live UI driver path is passed so chrome/status set by the hook can repaint immediately after `/reload`.
3. Docs (`docs/extension-api.md`, backlog/audit if needed) reflect that `/reload` re-fires `session_start(reason="reload")` rather than deferring it.
4. `just check` passes and the final full diff receives a different-family CLEAN review.
