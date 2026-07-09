# Plan: Extension UI camelCase editor text helpers

## Gap

Pi's extension `ExtensionUIContext` exposes core editor text helpers as camelCase TypeScript methods: `pasteToEditor(text)`, `setEditorText(text)`, and `getEditorText()`. Pipy already implements the matching live behavior through Pythonic snake_case methods (`paste_to_editor`, `set_editor_text`, `get_editor_text`) and live TUI driver plumbing, but does not expose the Pi-shaped camelCase aliases for these three helpers. This leaves translated Pi extensions needing avoidable renaming despite pipy's no-deprecation policy favoring direct Pi shape where practical.

## Pi reference

- `/Users/jochen/src/pi-mono/packages/coding-agent/src/core/extensions/types.ts`: `ExtensionUIContext` declares `pasteToEditor(text: string): void`, `setEditorText(text: string): void`, and `getEditorText(): string`.
- `/Users/jochen/src/pi-mono/packages/coding-agent/src/modes/interactive/interactive-mode.ts`: live TUI context maps those names to bracketed paste into the editor, editor text replacement, and `getExpandedText?.() ?? getText()`.
- `/Users/jochen/src/pi-mono/packages/coding-agent/src/modes/rpc/rpc-mode.ts`: non-TUI contexts no-op writes and return an empty string, matching pipy's existing headless snake_case behavior.

## Pipy design

Add camelCase aliases through the same mechanism pipy already uses for Pi-shaped `setHiddenThinkingLabel`, `addAutocompleteProvider`, `setEditorComponent`/`getEditorComponent`, and `getToolsExpanded`/`setToolsExpanded`: declare them on the `ExtensionUi` protocol and implement thin delegating methods on `_CollectingUi`. The structural-contract blast radius is intentionally small: `_CollectingUi` is pipy's concrete context, and existing test doubles that implement the driver protocol are not affected because no driver method is added.

- `getEditorText()` delegates to `get_editor_text()` and therefore returns live editor text or `""` headless/fail-soft.
- `setEditorText(text)` delegates to `set_editor_text(text)` and therefore replaces the live editor buffer or no-ops headless/fail-soft.
- `pasteToEditor(text)` delegates to `paste_to_editor(text)` and therefore inserts literal text at the current cursor via the existing live bracketed-paste path or no-ops headless/fail-soft.

No driver or TUI behavior changes are needed; this is a public extension API shape fix only. Pipy has no separate hidden expanded-editor payload today: image paste/drop insert explicit `@image:` references, and the live custom-editor adapter exposes its text through `_custom_editor_text()`, so the existing `get_input_text()` path is pipy's Pi-equivalent for `getExpandedText?.() ?? getText()`. Keep the existing snake_case helpers as Python convenience names because they are already documented pipy extension surface, but document the new camelCase aliases as the Pi-canonical spelling for translated/new parity-focused extensions.

## Done when

1. Unit coverage proves each camelCase alias reaches the identical live driver call as its snake_case twin.
2. Unit coverage separately proves headless/no-driver behavior: `getEditorText()` returns `""`, and `setEditorText(...)` / `pasteToEditor(...)` no-op without constructing or mutating live UI state.
3. If any conformance gate enumerates the extension UI surface, update its expected list so the new Pi names are checked instead of silently under-covered.
4. `docs/extension-api.md`, `docs/backlog.md`, and `docs/pi-mono-gap-audit.md` mention that the editor text helper aliases now include Pi's camelCase names, and that camelCase is canonical for Pi parity.
5. Focused tests and `just check` pass.
