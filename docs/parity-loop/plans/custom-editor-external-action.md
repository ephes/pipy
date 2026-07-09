# Custom editor external-editor action parity plan

## Gap

Pi custom editor components receive app-level keybinding/action handlers copied from the default editor in `packages/coding-agent/src/modes/interactive/interactive-mode.ts:setCustomEditorComponent`. The reserved editor-global action list in `packages/coding-agent/src/core/extensions/runner.ts` and the keybinding table in `packages/coding-agent/src/core/keybindings.ts` include `app.editor.external` with default key `ctrl+g` (open external editor). Pipy's custom editor adapter currently forwards the shipped model/thinking/tool/follow-up/dequeue actions but does not expose or handle `app.editor.external`, so a custom editor extending Pi's `CustomEditor` cannot delegate Ctrl-G external editing through the app action map.

## Reference behavior

Pi sources:

- `packages/coding-agent/src/core/keybindings.ts`: `app.editor.external` default `ctrl+g`, description `Open external editor`.
- `packages/coding-agent/src/core/extensions/runner.ts`: `app.editor.external` is an editor-global reserved binding for extension shortcut conflicts.
- `packages/coding-agent/src/modes/interactive/interactive-mode.ts:2354-2420`: custom-editor installation copies `defaultEditor.actionHandlers` into a custom editor's `actionHandlers` map and wires default editor callbacks. The default editor owns the concrete external-editor behavior; custom editors delegate the app action instead of implementing process/terminal ownership themselves.
- `packages/coding-agent/src/modes/rpc/rpc-mode.ts`: RPC/headless custom editor components are unsupported, so this is a live TUI-only surface.

## Pipy design

Keep ownership at pipy's existing TUI boundary:

1. Add `app.editor.external` to `_CustomEditorKeybindings` so custom editor factories see the Pi action in `keybindings.action_handlers` / `actionHandlers`. Unlike the current hard-coded helper map, this entry must be resolved through pipy's `KeybindingsManager` when available: `keys_for("app.editor.external")` returns the user override if one exists, else Pi's default `ctrl+g`; `matches` accepts both canonical `ctrl+g` and pipy's decoded `ctrl-g` spelling.
2. Thread the resolved `KeybindingsManager` used by `/hotkeys` into `ToolLoopTerminalUi` / `_CustomEditorKeybindings` so custom editor keybinding specs and matching stay aligned with user `keybindings.json` overrides. Keep this as a small additive step; a larger refactor to derive every action handler from a single default-editor handler map is a follow-on, but note that Pi copies the whole map and pipy should avoid hand-maintained omissions over time.
3. Add `ctrl-g` (the default decoded key for `app.editor.external`) to pipy's extension shortcut reserved set, matching Pi's `RESERVED_KEYBINDINGS_FOR_EXTENSION_CONFLICTS`. If a user rebinds the app action, dynamic shortcut conflict checking can remain a follow-on because pipy's existing shortcut validator is default-key based; in the live editor loop, the editor action wins over an extension shortcut registered on the same user-rebound key, and startup warns about that shadowing. The default Pi conflict must be closed in this slice. Audit `docs/examples/extensions/` and `scripts/` for `register_shortcut("ctrl-g"...)`; they should not ship an example that now disables itself.
4. Extract the existing `ctx.ui.editor(...)` overlay Ctrl-G implementation (`$VISUAL`/`$EDITOR` selection, normal terminal-mode restore, temp markdown file, successful-exit reload, failure/unset-editor preservation) into a shared `ToolLoopTerminalUi` helper and have the overlay, built-in editor action, and custom editor action all call that helper. Do not copy a second implementation. A custom editor invoking `app.editor.external` updates the active component text via `_set_custom_editor_text`, does not submit, does not enqueue a provider turn, and repaints. The built-in editor action performs the same text replacement as an undoable edit.
5. Runtime guard: this action is only reachable while a live custom editor owns the foreground input loop, so there is no provider turn in flight and stdin/stdout are TTYs. If no editor command is configured, if process launch/exit fails, or if the editor exits nonzero, preserve the old draft and repaint. Headless/RPC behavior remains unchanged: `setEditorComponent` is unsupported/no-op without a live UI.

## Done when

- Focused tests prove `keybindings.actionHandlers` includes `app.editor.external`, `keys_for("app.editor.external")` returns the default and an injected user override, Pi's `shift+ctrl+p` spelling remains stable, unrelated default bindings such as paste image keep their existing specs, and `matches` accepts the decoded alias for both `ctrl+g` and an overridden binding.
- Focused tests prove default `ctrl-g` is reserved for extension shortcuts, while no shipped example extension or script still registers it.
- Focused tests exercise deterministic fake external editor commands for success, nonzero exit, and unset editor: success replaces the live custom-editor draft; failure/unset preserves it; none submits a message or provider turn. Reuse of the shared helper is visible in the diff by having the overlay path call it too.
- `docs/backlog.md`, `docs/pi-mono-gap-audit.md`, and release notes reflect the shipped increment.
- `just check` passes and the different-family review gate returns CLEAN over the final diff.
