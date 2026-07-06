# Plan: Extension footer-data reactive branch-change callbacks

## Gap and scope

Implement the narrow extension-platform follow-on for `FooterData.onBranchChange(...)` in pipy's live product TUI. Today pipy passes a Pi-shaped footer-data snapshot to `ctx.ui.set_footer(...)`, but `onBranchChange` is a safe no-op. This slice makes live footer-data callbacks fire when the current git branch changes, and rebuilds/repaints the custom footer with a fresh snapshot. Headless contexts remain deterministic snapshots/no-ops. This is a single reviewable slice; broader custom editor rendering, multi-widget messages, live tool-render invalidation, and non-lifecycle hook UI threading remain deferred.

## Pi reference

Reference path: `/Users/jochen/src/pi-mono/packages/coding-agent/src/core/footer-data-provider.ts`.

Pi behavior:

- `FooterDataProvider` owns a cwd, cached branch, extension statuses, available-provider count, and `branchChangeCallbacks = new Set<() => void>()`.
- `getGitBranch()` returns the cached/current branch: branch name for `ref: refs/heads/...`, `"detached"` for detached HEAD, `null` outside git or on read errors; `.invalid` falls back to `git symbolic-ref` then `"detached"`.
- `getExtensionStatuses()` returns extension status texts, and `getAvailableProviderCount()` returns the available provider count.
- `onBranchChange(callback)` adds the callback to the set and returns an unsubscribe function that deletes it.
- Pi watches the directory containing `HEAD` (plus polling for WSL-mounted repos and reftable paths). On refresh, when the resolved branch differs from a previously-known cached branch, Pi updates the cache and invokes all callbacks. `setCwd(...)` also resets watches/cache and calls callbacks.
- The read-only extension surface is `Pick<FooterDataProvider, "getGitBranch" | "getExtensionStatuses" | "getAvailableProviderCount" | "onBranchChange">`.

## Pipy design

Pipy-owned Python boundaries:

- Keep `FooterData` in `src/pipy_harness/native/extension_runtime.py` as the object passed to extension footer factories.
- Extend `FooterData` with an optional callback registrar. Its current `get_*` fields remain snapshot values. `on_branch_change(callback)` delegates to the registrar when present and returns that disposer; without a registrar it keeps the existing no-op disposer for headless/offline snapshots. Support both snake_case and Pi-shaped camelCase.
- Add a small live branch watcher at the TUI/driver boundary rather than exposing git internals to extensions. `_LiveExtensionUiDriver.set_footer(factory)` will build `FooterData` with a registrar backed by `ToolLoopTerminalUi`.
- `ToolLoopTerminalUi` should remember the active footer factory, cwd-derived branch snapshot, registered extension callbacks, and disposer state. When branch changes are detected, it rebuilds the footer region from the same factory with fresh `FooterData`, repaints, and calls registered callbacks. Callback failures are fail-soft and do not break the TUI.
- Use a bounded polling implementation (sufficient for pipy's stdlib runtime and tests) with a small interval only while a custom footer is installed and at least one callback is registered or a footer factory is active. Poll `_detect_git_branch(cwd)`, mirroring current pipy branch detection (`None` outside git; `"detached"` for detached HEAD via current helper behavior if present). Avoid reading `.git` through the restricted file tools; runtime code may use stdlib/subprocess.
- Disposer semantics: each zero-argument `onBranchChange` callback persists like Pi's callback set until its idempotent disposer removes only that callback. Clearing/removing the footer clears the live TUI's footer callback set and stops polling when no longer needed.

## Tests and docs

Focused tests:

1. `FooterData.onBranchChange` with no registrar returns a callable no-op disposer and does not invoke callbacks.
2. Live driver footer data exposes a working `onBranchChange`: registering a callback, simulating a branch change through the TUI hook/poller boundary, causes zero-argument callback invocation and a footer rebuild with the new branch.
3. The returned disposer prevents future callback invocation and is idempotent.
4. Clearing/replacing the footer removes old callbacks so stale extension callbacks do not fire.

Docs updates:

- Update `docs/extension-api.md`, `docs/pi-mono-gap-audit.md`, and `docs/backlog.md` to move reactive footer branch-change delivery from deferred/follow-on to shipped for this slice.
- Add/update release notes if this repo has a current release note file for parity slices.

## Done when

- Focused tests pass.
- `uv run python scripts/parity_checks/extension_package_conformance.py --json` passes.
- `just check` passes (and `prek run --all-files` if a pre-commit config exists).
- Different-family review returns CLEAN over the final code+docs diff.
