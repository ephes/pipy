# Live extension event UI driver plan

Gap: extension event hooks outside lifecycle (`input`, `before_agent_start`, `tool_call`, `tool_result`, `user_bash`, `before_provider_request`, and `session_before`) currently receive a `ctx.ui` that records notifications but does not carry the live product-TUI driver, so Pi-style chrome/editor UI calls from those hooks do not paint immediately. This closes one bounded extension-platform parity slice by threading the existing live `_LiveExtensionUiDriver` through those dispatchers and their call sites when a product TUI is active.

Pi reference: `/Users/jochen/src/pi-mono/packages/coding-agent/src/modes/interactive/interactive-mode.ts` exposes a single live `createExtensionUIContext()` to extension code; methods such as `setStatus`, `setWidget`, `setHeader`, `setFooter`, `setTitle`, `custom`, `pasteToEditor`, `setEditorText`, `getEditorText`, `addAutocompleteProvider`, `setEditorComponent`, `getEditorComponent`, `getToolsExpanded`, and `setToolsExpanded` directly mutate/read the live interactive mode. The non-interactive extension runner in `/Users/jochen/src/pi-mono/packages/coding-agent/src/core/extensions/runner.ts` keeps TUI-only methods as no-ops/undefined, so pipy's headless behavior should remain deterministic.

Pipy plan: add an optional `ui_driver: ExtensionUiDriver | None = None` parameter to each non-lifecycle dispatch helper that constructs `_CollectingUi`; pass it into `_CollectingUi(has_ui, notify_sink, ui_driver=ui_driver)`. Update `NativeToolLoopSession` call sites to pass the already-built `extension_ui_driver` for live TUI runs and `None` for captured/headless runs. Do not change hook ordering, transformations, fail-open/fail-closed policy, notification privacy, or the behavior of `has_ui=False` contexts. The existing lifecycle path already passes the driver and remains the model.

Implementation scope and done-when:

1. Unit-level dispatcher coverage proves representative non-lifecycle hooks (`input`, `before_agent_start`, `tool_call`, `tool_result`, `user_bash`, `before_provider_request`, and `session_before`) can call live UI-driver methods immediately when `has_ui=True` and `ui_driver` is provided, while preserving their existing return semantics.
2. Headless/no-driver tests prove those same UI calls remain deterministic no-ops where applicable.
3. `docs/extension-api.md`, `docs/pi-mono-gap-audit.md`, and `docs/backlog.md` no longer describe non-lifecycle live UI-driver threading as deferred; they scope any remaining follow-ons separately.
4. Gates: focused tests plus `just check`; complete diff receives a different-family CLEAN review before commit.
