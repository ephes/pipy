# Custom editor app-hotkey parity plan

Gap: broader custom editor/component-library parity (one focused increment): custom editor components should preserve Pi-style app hotkeys for model/thinking/tool/extension actions instead of forcing every extension editor to reimplement them.

Pi reference:

- `packages/coding-agent/src/core/extensions/types.ts`: `EditorFactory = (tui, theme, keybindings) => EditorComponent`; `setEditorComponent(factory)` stores a factory and `getEditorComponent()` returns it.
- `packages/coding-agent/src/modes/interactive/interactive-mode.ts:2289-2354`: when a factory is set, Pi saves current editor text, calls the factory, wires `onSubmit` and `onChange`, copies text, border/padding/autocomplete, and for `CustomEditor`-shaped objects with an `actionHandlers` `Map`, copies app-level handlers from the default editor: `onEscape`, `onCtrlD`, `onPasteImage`, `onExtensionShortcut`, and all default editor action handlers. This means a custom editor that calls its base/super handler for unhandled keys still gets app actions such as model cycling, thinking cycling/toggle, tool expansion, external editor, follow-up/dequeue, session actions, and extension shortcuts.

Pipy current behavior:

- `src/pipy_harness/native/tui.py:set_editor_component` already calls a Python factory as `(tui, theme, keybindings)`, stores the component, wires submit/change callbacks, copies current text, and forwards autocomplete providers.
- `_wire_custom_editor_component` explicitly defers app-level handlers, so while the custom editor is active `read_line` routes every key to `_handle_custom_editor_key` before the built-in hotkey handling. A custom editor cannot delegate app hotkeys through a Pi-shaped keybinding/action-handler object.

Implementation scope:

1. Add a small pipy-owned keybinding/action adapter passed as the third factory argument. It should expose `keys_for(action)` plus `matches(key, action)`/`matches_action(key, action)` and an `action_handlers`/`actionHandlers` mapping for the app actions that `ToolLoopTerminalUi.read_line` can represent as returns/sentinels today.
2. Wire a custom editor component's optional `on_extension_shortcut`/`onExtensionShortcut` and `action_handlers`/`actionHandlers` dict-like fields to the dispatchable handlers when present, without overwriting extension-provided handlers. Preserve current fail-soft behavior for hostile/immutable objects. Keep submit/change callbacks host-wired like Pi even when component fields are pre-set. Leave escape/exit/paste-image callbacks deferred until pipy has a matching custom-editor interrupt/exit/paste dispatch boundary.
3. Keep the slice bounded to keybinding/action delegation. Do not implement Pi's full component library, border/padding APIs, external-editor overlay, or session-selector component stack in this slice.

Done when:

- Focused tests prove a custom editor receives a keybindings object, can delegate `shift-tab` through `action_handlers["app.thinking.cycle"]`, and `read_line` returns the same hotkey sentinel while preserving typed text for the next prompt.
- Focused tests prove extension shortcut keys reach `on_extension_shortcut`/`onExtensionShortcut` while the custom editor is active.
- Existing custom editor tests continue to pass.
- Docs/backlog/audit mark this increment as landed while keeping broader component-library parity open.
