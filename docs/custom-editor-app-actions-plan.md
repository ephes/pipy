# Custom editor app-action delegation plan

Status: shipped bounded slice; broader component-library parity remains a
strategic follow-on, not the selected next slice.

## Gap

Broader custom editor/component-library parity was selected when this plan was
written. This shipped slice narrowed it to Pi's `CustomEditor` app-keybinding
delegation for live `ctx.ui.setEditorComponent(...)` custom editors.

## Pi reference

- `/Users/jochen/src/pi-mono/packages/coding-agent/src/core/extensions/types.ts`: `EditorFactory = (tui, theme, keybindings) => EditorComponent`; `setEditorComponent(factory | undefined)` stores the factory and custom editors are expected to call `super.handleInput(data)` for keys they do not handle.
- `/Users/jochen/src/pi-mono/packages/coding-agent/src/modes/interactive/components/custom-editor.ts`: `CustomEditor.handleInput` first checks extension shortcuts, then `app.clipboard.pasteImage` through the separate `onPasteImage` callback, then app keybindings. It routes `app.interrupt` (Escape) to `onEscape`/`actionHandlers["app.interrupt"]` unless autocomplete is open, routes `app.exit` (Ctrl-D) only when `getText().length === 0`, then loops over every other registered `actionHandlers` entry except `app.interrupt` and `app.exit`. `app.clear` is not inserted into the copied handler map, so Ctrl-C stays on the process-level interrupt path.
- `/Users/jochen/src/pi-mono/packages/coding-agent/src/modes/interactive/interactive-mode.ts`: when a custom editor exposes an `actionHandlers` map, Pi copies default-editor `onEscape`, `onCtrlD`, `onPasteImage`, `onExtensionShortcut`, and default action handlers onto it before installing the component.
- `/Users/jochen/src/pi-mono/packages/coding-agent/src/core/keybindings.ts`: the relevant editor/app default keys are Pi-style specs such as `escape`, `ctrl+d`, `shift+tab`, `ctrl+p`, `shift+ctrl+p`, `ctrl+l`, `ctrl+o`, `ctrl+t`, `alt+enter`, `alt+up`, and `ctrl+v` on non-Windows (`alt+v` on Windows for paste image).

## Pipy plan

Pipy already passes a Pi-shaped keybindings adapter and wires handlers for a smaller action set. Extend that boundary rather than adding a new extension API:

1. Expand `_CustomEditorKeybindings` to include the live product-TUI app actions pipy can dispatch from the editor loop: interrupt, exit, model select, message follow-up/dequeue, clipboard paste image, plus the already-wired thinking/model/tools toggles. Do **not** add `app.clear`/Ctrl-C to the delegated handler map; Pi deliberately leaves Ctrl-C to the process-level interrupt path, and pipy must not let a custom editor swallow the Ctrl-C exit/interrupt path. Keep canonical bindings (used by `keys_for`) in Pi-style display form such as `ctrl+p`, while `matches(...)` also accepts pipy's decoded dash-form live events such as `ctrl-p`.
2. Wire dispatchable app actions into custom editor `action_handlers` / `actionHandlers` maps and into `_handle_custom_editor_key` using the same sentinels or direct effects as the built-in editor path. For non-submitting app hotkeys that dispatch back to the session, preserve draft text in `_pending_initial_text` like the built-in path. Preserve Pi's special callback shape: existing `on_escape`/`onEscape` callbacks win over the default interrupt callback, Ctrl-D exits only when `_custom_editor_text()` is empty, and paste image routes through `on_paste_image`/`onPasteImage` rather than the generic handler map.
3. Keep unsupported or non-editor-context keybindings out of this slice; do not add broader Pi component-library APIs.
4. Add focused unit coverage for a custom editor that delegates unhandled keys through `keybindings.matches(...)` and `action_handlers`, proving at least a session-dispatching app action (for example Ctrl-L/model select or Shift-Tab/thinking cycle) and Alt-Enter/follow-up route out with draft preservation, plus `onEscape` precedence, Ctrl-D empty/non-empty behavior, and Ctrl-C remaining outside the delegated action map where practical.
5. Update the then-selected `docs/backlog.md` broader custom
   editor/component-library paragraph and `docs/pi-mono-gap-audit.md`
   extension follow-ons to mark this app-action delegation increment shipped
   while leaving broader component-library work open.

## Done when

- Custom editor app actions above are available via the keybindings adapter, special callbacks, and handler maps.
- Tests prove delegated custom-editor app hotkeys route through the same product-TUI actions as the default editor without dropping draft text.
- `just check` and a different-family review are clean over docs and code.
