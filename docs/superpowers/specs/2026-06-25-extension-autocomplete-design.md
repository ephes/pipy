# Extension Autocomplete Provider Slice

Gap: extension/package follow-on — richer Pi extension APIs, specifically Pi's `ctx.ui.addAutocompleteProvider(factory)` wrapper hook.

Reference: `/Users/jochen/src/pi-mono/packages/coding-agent/src/core/extensions/types.ts` (`AutocompleteProviderFactory`, `ExtensionUIContext.addAutocompleteProvider`).

## Scope

Pi lets command/shortcut handlers stack an autocomplete provider wrapper on the current editor autocomplete provider. For this slice, pipy will add a Python-shaped but Pi-contract equivalent for the product TUI's existing completion core: `ctx.ui.add_autocomplete_provider(factory)` plus `ctx.ui.addAutocompleteProvider(factory)`. The factory receives the current provider object and returns a provider object with Pi-shaped methods. Pipy applies registered wrappers only to live product-TUI autocomplete refresh/Tab completion; headless contexts accept the call but have no visible effect, matching the deterministic no-UI contract used by other `ctx.ui` helpers.

## Design

- Add a small provider contract in `pipy_harness.native.extension_runtime` / `tui` that mirrors Pi's surface:
  - `get_suggestions(lines, cursor_line, cursor_col, context)` (camelCase `getSuggestions` also accepted) returns an object/dict with `items` and `prefix`.
  - `apply_completion(lines, cursor_line, cursor_col, item, prefix)` (camelCase `applyCompletion` also accepted) returns the replacement editor text/cursor, with a default implementation that replaces the active prefix with the item's value.
  - `should_trigger_file_completion(lines, cursor_line, cursor_col)` (camelCase `shouldTriggerFileCompletion` also accepted) is optional and delegates to the current provider when absent.
- The context includes `force` and a best-effort abort `signal=None`, matching Pi's `getSuggestions(..., {signal, force})` shape without inventing a workspace argument. The built-in provider owns the workspace path internally, like Pi's `CombinedAutocompleteProvider` constructor state.
- Completion items are pipy's existing `CompletionItem` rows or dict/object rows with `value`/`label`, coerced fail-soft to bounded `CompletionItem` instances.
- Extend `ExtensionUi` with snake_case and Pi-shaped camelCase aliases: `add_autocomplete_provider(factory)` and `addAutocompleteProvider(factory)`.
- Store wrappers on `_ExtensionUiState` during command/shortcut dispatch. If a live UI driver is available, immediately hand the factory to the TUI driver. Headless dispatch records nothing visible and never blocks.
- Extend `_LiveExtensionUiDriver` and `ToolLoopTerminalUi` with `add_extension_autocomplete_provider(factory)`. The TUI keeps an ordered list of factories, composes them around its built-in provider, uses `get_suggestions`/`should_trigger_file_completion` for `@` and forced Tab completion, and accepts rows through the provider's `apply_completion` so wrappers can customize insertion.
- Built-in provider behavior remains unchanged when no extension provider is registered. A broken factory/provider/method is ignored for that refresh (fail-soft fallback to the previous provider) and never crashes the editor.
- Preserve privacy: providers receive editor buffer lines/cursor and context only. No provider output is archived by this slice.

## Done When

1. Unit tests prove command handlers can call both alias names and live drivers receive factories in order.
2. Pure TUI tests prove a wrapper can append/replace completion rows for `@` autocomplete and forced Tab path completion, while the no-extension path is unchanged.
3. Tests prove custom `apply_completion` can control insertion and optional `should_trigger_file_completion` can veto file completion.
4. Broken factories/providers fail soft.
5. `docs/extension-api.md`, `docs/pi-mono-gap-audit.md`, and `docs/backlog.md` mark autocomplete providers as shipped and leave custom editor/live re-rendering/multi-widget message components deferred.
