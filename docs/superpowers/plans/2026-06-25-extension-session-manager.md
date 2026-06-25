# Implementation plan: extension session-manager helpers

1. Add read-only view types.
   - Define `SessionEntryView`, `SessionHeaderView`, `SessionTreeNodeView`, and `SessionManagerView`/empty implementation in `extension_runtime.py`.
   - Acceptance: views copy only safe scalar/JSON-like entry data and are immutable from extension code.

2. Add context plumbing.
   - Extend `_CommandContext`, `dispatch_extension_command`, `dispatch_extension_shortcut`, and `_run_extension_handler` with an optional session manager/tree parameter.
   - Expose both `ctx.session_manager` and Pi-shaped `ctx.sessionManager`.
   - Acceptance: direct dispatch without a session still returns safe empty values.

3. Wire product runtime.
   - Pass the active `NativeSessionTree` from `NativeToolReplSession` command and shortcut dispatch call sites.
   - Acceptance: extension handlers in the product path see the active session id, file, cwd, leaf, entries, labels, branch, tree, and session name.

4. Test and document.
   - Add focused tests for direct dispatch and native session data/alias behavior.
   - Update `docs/extension-api.md`, `docs/pi-mono-gap-audit.md`, and `docs/backlog.md` to mark the session-manager helper slice shipped and keep remaining follow-ons current.
   - Acceptance: extension conformance gate and `just check` pass before review.
