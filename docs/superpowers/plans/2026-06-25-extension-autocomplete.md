# Implementation Plan: Extension Autocomplete Providers

Reviewed design: `docs/superpowers/specs/2026-06-25-extension-autocomplete-design.md` (Opus plan review CLEAN).

1. Add the provider contract and UI API aliases.
   - Define/co-locate small autocomplete context/result helpers near the TUI/provider boundary.
   - Extend `ExtensionUiDriver`/`ExtensionUi` and `_ExtensionUiState` with `add_autocomplete_provider` and `addAutocompleteProvider`.
   - Acceptance: command-dispatch tests can call both aliases; live driver receives factories in call order; headless dispatch is a no-op.

2. Wrap the built-in TUI completion provider.
   - Create a built-in provider object for current `@` and path-completion logic with Pi-shaped `get_suggestions`, `apply_completion`, and optional `should_trigger_file_completion` behavior.
   - Add ordered factory composition to `ToolLoopTerminalUi.add_extension_autocomplete_provider`.
   - Acceptance: without factories, existing `@` and Tab completion behavior and tests remain unchanged.

3. Route editor refresh/acceptance through the composed provider.
   - Use `get_suggestions(lines, cursor_line, cursor_col, {force, signal})` for `@` refresh and forced Tab completion.
   - Use the active provider's `apply_completion` for Enter/Tab acceptance.
   - Acceptance: tests prove wrappers can append/replace suggestions, custom insertion works, and `should_trigger_file_completion` can veto path completion.

4. Fail soft and bound rows.
   - Coerce `CompletionItem`, dict/object, tuple, and string rows into bounded completion items.
   - On bad factories/providers/methods, fall back to the previous provider or close/no-op rather than crashing.
   - Acceptance: broken factory/provider tests pass.

5. Update docs and parity status.
   - Update `docs/extension-api.md`, `docs/pi-mono-gap-audit.md`, and `docs/backlog.md` to mark autocomplete providers as shipped while leaving custom editor/live re-rendering/multi-widget message components deferred.
   - Acceptance: docs match behavior before final review.

6. Gates and review.
   - Run focused tests, `just check`, `prek run --all-files` if configured, then `opus-review-loop` over the complete diff. Fix issues and repeat until CLEAN.
