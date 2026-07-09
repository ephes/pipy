# Custom editor external-editor action implementation plan

1. Thread resolved keybindings into the TUI custom-editor adapter.
   - Add an optional `keybindings_manager` field to `ToolLoopTerminalUi`, pass the session's resolved manager from `NativeToolReplSession._build_terminal_ui`, and update `_CustomEditorKeybindings` to resolve `keys_for(action)` from that manager before falling back to local defaults.
   - Acceptance: tests can inject `KeybindingsManager({"app.editor.external": "ctrl+x"})` and observe custom-editor key specs/matches using the override plus decoded aliases; default Pi spellings such as `shift+ctrl+p` remain stable and unrelated default bindings such as paste image keep their existing specs.

2. Add the missing Pi action and reserved default shortcut.
   - Add `app.editor.external` to `_CustomEditorKeybindings` action keys/aliases/handler actions and to `extension_runtime.RESERVED_SHORTCUT_KEYS` as `ctrl-g`.
   - Acceptance: action handlers include `app.editor.external`; extension shortcut registration rejects default `ctrl-g`; startup warns if a user-rebound editor key shadows an activated extension shortcut; `docs/examples/extensions/` and `scripts/` contain no example `ctrl-g` shortcut registration.

3. Share the external-editor helper.
   - Extract the `ctx.ui.editor(...)` overlay Ctrl-G `$VISUAL`/`$EDITOR` temp-file implementation into a reusable `ToolLoopTerminalUi` helper that accepts current text and returns edited text or `None`.
   - Update the overlay path to call the helper. Route custom editor and built-in editor `app.editor.external` to the same helper, updating the active draft only when it returns text.
   - Acceptance: success replaces draft; no editor/nonzero failure preserves draft; built-in replacement is undoable; no submission/provider-turn sentinel is produced.

4. Docs and verification.
   - Update parity docs/backlog and release notes for the shipped custom-editor app action increment.
   - Run focused tests, conformance gates as relevant, `just check`, then final different-family review over the complete diff.
