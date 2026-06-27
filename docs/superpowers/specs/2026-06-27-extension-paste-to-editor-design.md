# Extension `paste_to_editor` parity design

Status: proposed

## Gap

Pi exposes `ctx.ui.pasteToEditor(text)` as a distinct extension UI helper. In
interactive mode Pi routes the text through the editor's bracketed-paste path
(`editor.handleInput("\x1b[200~" + text + "\x1b[201~")`), while RPC degrades to
`setEditorText(text)`. Pipy currently exposes the Python/Pi-shaped helper names
but the live product TUI implementation is equivalent to `set_editor_text`: it
replaces the entire prompt buffer and moves the cursor to the end. That drops
Pi's important editor semantics: pasted text is inserted at the current cursor
position and preserves the existing surrounding draft.

Relevant Pi reference paths:

- `/Users/jochen/src/pi-mono/packages/coding-agent/src/modes/interactive/interactive-mode.ts`
  (`createExtensionUIContext().pasteToEditor`)
- `/Users/jochen/src/pi-mono/packages/coding-agent/src/core/extensions/types.ts`
  (`ExtensionUIContext.pasteToEditor` docs)
- `/Users/jochen/src/pi-mono/packages/coding-agent/src/modes/rpc/rpc-mode.ts`
  (RPC fallback to `setEditorText`)

## Pi behavior to match

- Field/method name: `pasteToEditor(text: string): void` (pipy keeps Pythonic
  `paste_to_editor(text)` and camelCase alias exposure through existing context
  wrappers).
- Optionality: `text` is required by the API, coerced by pipy's Python boundary
  with the same `str(text)` behavior as other UI helpers.
- Interactive product TUI behavior: route through paste handling rather than a
  plain full-buffer replace. For pipy's editor this means inserting literal text
  at the current cursor, preserving text before/after the cursor, advancing the
  cursor by the inserted length, closing stale autocomplete, and keeping pasted
  newlines literal.
- Headless/non-live behavior: no-op at the extension context boundary, matching
  pipy's existing deterministic headless UI contract. RPC's degrade-to-set path
  is tracked in `docs/automation-rpc.md` and is not changed by this live-TUI
  slice.
- Derived identifiers: none. No provider/auth/package manifest metadata is
  involved.

## Pipy implementation plan

1. Change `ToolLoopTerminalUi.paste_input_text(text)` to use the existing editor
   paste insertion primitive (`_insert_paste`) instead of delegating to
   `set_input_text`.
2. Keep `_LiveExtensionUiDriver.paste_to_editor` and
   `ExtensionUiContext.paste_to_editor` boundaries unchanged so extension code
   continues to call the same API.
3. Add focused tests proving that live paste inserts at the cursor and preserves
   surrounding text, while `set_editor_text` still replaces the buffer and the
   headless helper remains a no-op.
4. Update extension/parity docs and release notes to mark this Pi semantic gap
   closed and remove wording that says paste currently replaces the buffer.

## Done when

- Focused unit tests cover live insertion-at-cursor behavior and replacement
  remains limited to `set_editor_text`.
- `uv run python scripts/parity_checks/extension_package_conformance.py --json`
  passes.
- `just check` passes.
- A different-family review returns CLEAN on the complete code+docs diff before
  commit.
