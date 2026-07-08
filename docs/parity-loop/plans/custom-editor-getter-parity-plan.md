# Custom Editor Getter Parity Plan

Gap: Broader custom editor/component-library parity, focused on the Pi-shaped `ctx.ui.getEditorComponent()` / `get_editor_component()` live getter.

Pi reference:

- `/Users/jochen/src/pi-mono/packages/coding-agent/src/modes/interactive/interactive-mode.ts:2090-2091` exposes `setEditorComponent: (factory) => this.setCustomEditorComponent(factory)` and `getEditorComponent: () => this.editorComponentFactory` in live extension UI contexts.
- `/Users/jochen/src/pi-mono/packages/coding-agent/src/modes/interactive/interactive-mode.ts:2288-2335` stores `this.editorComponentFactory = factory` before constructing the custom editor, copies editor text into the new component, wires callbacks/autocomplete/app handlers, and clears back to default on `undefined`.
- `/Users/jochen/src/pi-mono/packages/coding-agent/src/core/extensions/types.ts:255-258` documents `getEditorComponent(): EditorFactory | undefined` as returning the currently configured custom editor factory, or `undefined` when using the default editor.
- `/Users/jochen/src/pi-mono/packages/coding-agent/src/modes/rpc/rpc-mode.ts:276-283` makes RPC/headless `setEditorComponent()` a no-op and `getEditorComponent()` return `undefined`.

Pipy current divergence:

- `ToolLoopTerminalUi.get_editor_component()` currently returns the factory only when `_custom_editor_active` is true. If the factory is stored but construction fails or returns `None`, pipy reports no configured factory even though Pi stores and returns the configured factory immediately.
- `ExtensionUiState.get_editor_component()` already returns `None` headless, matching Pi RPC/no-op behavior, and should continue to fail soft.

Implementation scope:

1. Change the live TUI getter to return the stored `_custom_editor_factory`, not the active component state. Clearing via `set_editor_component(None)` must still return `None`.
2. Add focused tests proving:
   - a live factory that raises during construction is still returned by `get_editor_component()` after fail-soft handling;
   - a live factory that returns `None` is still returned by `get_editor_component()`;
   - clearing restores `None`;
   - existing headless/no-driver extension context behavior remains `None`.
3. Update extension/backlog/gap docs to mark this narrow getter parity increment shipped while leaving broader component-library parity deferred.

Done when:

- Focused tests cover the Pi getter semantics above.
- `just check` passes.
- A different-family review returns CLEAN over the complete diff before commit.
