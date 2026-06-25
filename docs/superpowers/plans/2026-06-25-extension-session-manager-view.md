# Extension session manager view - implementation plan

1. Add read-only session view types in
   `src/pipy_harness/native/extension_runtime.py`.
   - Acceptance: `CommandContext` exposes `session_manager`; default/headless
     contexts return empty safe values and never raise for reads.

2. Serialize active `NativeSessionTree` state into safe snapshots.
   - Acceptance: the view exposes session id, cwd, optional path, name, leaf id,
     and `branch_entries()` as JSON-safe dict copies without mutation methods.

3. Wire live command/shortcut dispatch in
   `src/pipy_harness/native/tool_loop_session.py`.
   - Acceptance: extension commands and keyboard shortcuts receive a view over
     the active session tree.

4. Add focused tests.
   - Acceptance: tests cover default empty dispatch and
     `NativeSessionTree`-backed command dispatch, including branch entry
     snapshots and no mutable tree handle exposure.

5. Update parity docs.
   - Acceptance: `docs/extension-api.md`, `docs/pi-mono-gap-audit.md`, and
     `docs/backlog.md` mark this narrow helper shipped and leave Pi session
     replacement helpers deferred.

6. Run gates/review/commit.
   - Acceptance: extension package conformance, `just check`, and
     different-family review are CLEAN over the complete diff.
