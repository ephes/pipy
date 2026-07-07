# Implementation Plan: Extension `/reload` session_start(reason="reload")

1. Add product-path coverage.
   - Create a focused captured-stream test in `tests/test_native_tool_loop_session.py` with an extension whose `session_start` hook writes `event.reason` to a workspace proof file and calls `ctx.ui.notify(...)`.
   - The extension command mutates the extension file or a marker so the reloaded generation is distinguishable, then `/reload` runs.
   - Acceptance: proof records the startup event and the reload event in order (`startup`, then `reload`) and the reload hook's notification appears in diagnostics, with no provider turn.

2. Wire `/reload` lifecycle dispatch.
   - In the existing `/reload` branch of `NativeToolReplSession.run`, after extension activation, flag parsing/application, provider/tool/renderer/menu refresh, and `emitter.set_lifecycle_hooks(...)`, call `emitter.fire_lifecycle(EVENT_SESSION_START, reason="reload")`.
   - Acceptance: the call uses the already configured `_ExtensionAwareEmitter`, so it carries `cwd`, `has_ui`, `_extension_notify`, live `extension_ui_driver`, and the reloaded flag values; hook errors remain fail-soft.

3. Update parity docs.
   - Update `docs/extension-api.md` to state that `/reload` clears old chrome, activates the new generation, and re-fires `session_start` with `reason="reload"`, matching Pi. Remove the old limitation that session-start-only chrome stays blank until a later lifecycle event.
   - Update `docs/backlog.md` only if it still repeats the old limitation.
   - Acceptance: docs no longer describe the gap as deferred.

4. Verify and review.
   - Run the focused test and `just check`; run prek only if `.pre-commit-config.yaml` exists.
   - Run the different-family review over the complete diff and fix any ISSUES before committing.
