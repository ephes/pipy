# Extension footer data provider parity plan

Gap: extension/package platform follow-on — make `ctx.ui.set_footer` hand translated Python extensions a Pi-shaped read-only footer data provider instead of a dataclass-only snapshot.

Pi reference paths:

- `/Users/jochen/src/pi-mono/packages/coding-agent/src/core/extensions/types.ts`
- `/Users/jochen/src/pi-mono/packages/coding-agent/src/core/footer-data-provider.ts`

Pi behavior to match in this slice:

- `ctx.ui.setFooter(factory)` accepts a custom footer factory `(tui, theme, footerData)`.
- The third argument is a `ReadonlyFooterDataProvider` exposing `getGitBranch()`, `getExtensionStatuses()`, `getAvailableProviderCount()`, and `onBranchChange(...)`.
- `getGitBranch()` returns a nullable branch name; `getExtensionStatuses()` returns a read-only status map; `getAvailableProviderCount()` returns the current provider-count integer; `onBranchChange` registers a callback in Pi.

Pipy plan:

- Keep the existing `FooterData` value object and attributes (`git_branch`, `extension_statuses`) for compatibility with the shipped Python API, but add Pi-shaped methods: `get_git_branch`/`getGitBranch`, `get_extension_statuses`/`getExtensionStatuses`, `get_available_provider_count`/`getAvailableProviderCount`, and `on_branch_change`/`onBranchChange`.
- Add an `available_provider_count` field to `FooterData`, defaulting to 0 so existing tests/extension code can construct it without changes.
- Wire `_LiveExtensionUiDriver.set_footer` to populate `available_provider_count` from the live TUI when available. If the current TUI does not expose the count, use 0 rather than reaching into provider internals.
- Treat branch-change reactivity as a deterministic no-op registration for this narrow slice: `onBranchChange(callback)` returns a disposer callable and never calls the callback. This preserves Pi's callable surface without promising file-watcher reactivity yet.
- Update docs and parity audit/backlog to mark this narrow footer-data provider API follow-on as shipped while keeping broader reactive footer/live component work deferred.

Done when:

- Focused unit tests cover the Pi-shaped footer methods, read-only status snapshot, no-op branch-change disposer, and live driver population of available provider count.
- `uv run python scripts/parity_checks/extension_package_conformance.py --json` and `just check` pass.

## Implementation plan

1. Extend the `FooterData` dataclass with an `available_provider_count` field defaulting to `0`.
   - Acceptance: existing call sites that pass only branch/status still work.
2. Add snake_case and Pi-shaped camelCase read methods for branch, extension statuses, and provider count.
   - Acceptance: tests can call `getGitBranch()`, `getExtensionStatuses()`, and `getAvailableProviderCount()` on the footer data handed to an extension.
3. Add no-op `on_branch_change` / `onBranchChange` registration that returns a disposer callable.
   - Acceptance: registering a callback does not call it in this slice; calling the disposer is safe and idempotent.
4. Populate `FooterData.available_provider_count` from the live TUI driver when the TUI exposes `available_provider_count`, falling back to `0`.
   - Acceptance: `_LiveExtensionUiDriver.set_footer` tests observe the count in the footer data object.
5. Update extension/parity docs to mark the footer-data provider surface as shipped and leave reactive branch updates/live footer redraws deferred.
   - Acceptance: docs distinguish the new callable API from still-deferred reactivity.
6. Run focused tests, the extension package conformance gate, `just check`, and a different-family review over the final diff before committing.
   - Acceptance: all gates are green and review returns CLEAN.
