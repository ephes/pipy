# Plan: Extension custom editor component integration

Gap: Extension platform follow-on — full custom editor rendering/input integration beyond the current in-memory `setEditorComponent` store.

Pi reference paths:
- `/Users/jochen/src/pi-mono/packages/coding-agent/src/core/extensions/types.ts`
- `/Users/jochen/src/pi-mono/packages/coding-agent/src/modes/interactive/interactive-mode.ts`
- `/Users/jochen/src/pi-mono/packages/coding-agent/src/modes/rpc/rpc-mode.ts`

Pi behavior to match in this bounded slice:
- `ctx.ui.setEditorComponent(factory)` stores a live custom editor factory; passing `undefined` restores the default editor. RPC/headless contexts no-op and `getEditorComponent()` returns `undefined`.
- In interactive mode, Pi immediately replaces the main editor by calling `factory(tui, getEditorTheme(), keybindings)` and focuses the returned component.
- Pi preserves the current editor text across both directions: default -> custom calls `newEditor.setText(currentText)`, custom -> default calls `defaultEditor.setText(currentText)`.
- Pi wires default editor callbacks onto the custom component: `onSubmit`, `onChange`, and, for `CustomEditor`-like components exposing an `actionHandlers` map, fallback app handlers (`onEscape`, `onCtrlD`, `onPasteImage`, `onExtensionShortcut`) plus all default action handlers.
- Pi forwards autocomplete if the custom component has `setAutocompleteProvider` and an active provider exists.

Pipy implementation scope:
- Keep Python-owned boundaries; do not import or emulate Pi TUI internals. Implement a minimal live custom-editor adapter in `ToolLoopTerminalUi` using duck-typed extension components.
- On `set_editor_component(factory)`, preserve the current input text, call a callable factory with bounded live handles `(tui, theme, keybindings)` when possible, and activate the returned object as the current editor component. Passing `None` clears it while preserving text back into the built-in editor.
- The activated component may expose `render(width)`, `handle_input(key)`, `get_text()`, `set_text(text)`, optional callback attributes (`on_submit`, `on_change`, snake/camel aliases), and optional `set_autocomplete_provider`. Pipy will route decoded keys to it during `read_line`; submission occurs through the wired callback or a returned submitted value, with no provider turn until `read_line` returns.
- Preserve deterministic headless/no-ui behavior already present in `ExtensionUIContext`: no-op set and `None` get when no UI driver exists.
- Keep this slice focused: no full Pi component library, no external editor changes, no RPC extension-UI channel, no message/tool renderer invalidation.

Done-when criteria:
1. Focused tests prove setting a callable factory replaces the editor, renders custom rows, routes input to `handle_input`, wires submit/change callbacks, and preserves text when clearing back to the built-in editor.
2. Existing custom-editor store behavior and headless no-op behavior remain green.
3. Docs/backlog/gap audit mark this bounded custom editor integration as shipped while leaving broader component-library/RPC work deferred.
4. `just check` passes and a different-family review returns CLEAN over the complete diff.
