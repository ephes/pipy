# Extension session-manager helpers design

Gap: Extension/package platform follow-on — read-only extension session-manager helpers.

Pi reference paths:

- `/Users/jochen/src/pi-mono/packages/coding-agent/src/core/extensions/types.ts`
- `/Users/jochen/src/pi-mono/packages/coding-agent/src/core/extensions/runner.ts`
- `/Users/jochen/src/pi-mono/packages/coding-agent/src/core/session-manager.ts`

## Scope

Pi exposes a read-only `ctx.sessionManager` on extension contexts, with accessors such as `getCwd`, `getSessionDir`, `getSessionId`, `getSessionFile`, `getLeafId`, `getLeafEntry`, `getEntry`, `getLabel`, `getBranch`, `getHeader`, `getEntries`, `getTree`, and `getSessionName`. Pipy already owns a native `NativeSessionTree` with the analogous data and a command/shortcut `CommandContext`; this slice adds a bounded, read-only session-manager view to command/shortcut contexts. It is a single reviewable parity slice: no mutation helpers (`setLabel`, `setSessionName`, `sendMessage`), no provider/model registry expansion, and no extra hook contexts.

## Design

- Add frozen/plain value objects in `pipy_harness.native.extension_runtime` for a safe session header, entry, and tree node view. They mirror Pi's readable shape but use Python snake_case plus JSON-like `to_dict()` data for extension authors that need generic inspection.
- Add a `SessionManagerView` protocol and expose `ctx.session_manager` on `CommandContext`; also provide a Pi-shaped alias `ctx.sessionManager` for translated extensions.
- Back the view with an optional `NativeSessionTree`. When no product session is wired (unit/deterministic dispatch), methods return safe empty/`None` values rather than exposing internals or throwing for simple reads.
- Keep the surface read-only: entries are copied into immutable dataclasses, labels are returned from copied maps, and the underlying `NativeSessionTree` is never handed to extension code.
- Wire the active `session_tree` into command and shortcut dispatch in the tool-loop product path. Existing tests that call dispatch directly continue to work with an empty view.

## Acceptance criteria

1. An extension command can read `ctx.session_manager.get_session_id()`, `get_leaf_id()`, `get_entries()`, `get_branch()`, `get_tree()`, `get_session_name()`, and labels from the active native session without a provider turn.
2. The Pi-shaped `ctx.sessionManager` alias returns the same read-only view.
3. The view is immutable / read-only and does not expose `NativeSessionTree` mutation methods.
4. `uv run python scripts/parity_checks/extension_package_conformance.py --json` and `just check` pass.
