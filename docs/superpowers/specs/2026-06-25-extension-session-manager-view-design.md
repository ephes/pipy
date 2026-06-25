# Extension session manager view - design

Status: captured plan for a future parity slice, not yet implemented. Owning
spec: [`docs/extension-api.md`](../../extension-api.md). Gap source: Pi's
`ExtensionContext.sessionManager` surface in
`/Users/jochen/src/pi-mono/packages/coding-agent/src/core/extensions/types.ts`
and context construction in
`/Users/jochen/src/pi-mono/packages/coding-agent/src/core/extensions/runner.ts`.

## Gap

Pi exposes `ctx.sessionManager` as a session view to extension contexts. Pipy
command/shortcut contexts currently expose conversation helpers, UI, flags,
dynamic controls, and custom-entry append helpers, but no read-only session
manager helper.

## Design

Add a small pipy-owned, read-only `ctx.session_manager` surface for
command/shortcut contexts. In live product sessions, the view is backed by the
active `NativeSessionTree`. In headless or unit dispatch contexts without a
tree, it exposes deterministic empty values.

Safe metadata only:

- session id
- cwd
- optional session path
- optional session name
- current leaf id
- current branch entries as JSON-safe snapshots

The view must not expose mutable session objects, writer handles, or methods
that change the active session tree. Returned entries should be JSON-safe copies
shaped like session-tree records. Pi's mutating session-manager operations
(`newSession`, `fork`, `switchSession`, and related replacement helpers) remain
deferred.

## Done

1. Extension commands can read `ctx.session_manager.session_id`, `name`,
   `leaf_id`, and `branch_entries()` during a live product session without
   mutating the tree.
2. Unit tests cover the headless empty view and a `NativeSessionTree`-backed
   view.
3. Docs/backlog/audit mention this narrow helper as shipped while keeping
   remaining session replacement helpers deferred.
4. `uv run python scripts/parity_checks/extension_package_conformance.py --json`
   and `just check` pass.
