# Custom editor app-hotkey implementation plan

1. Add a bounded keybinding/action adapter for `ToolLoopTerminalUi.set_editor_component`.
   - Acceptance: the editor factory receives a non-`None` object with `keys_for`, `matches`, `matches_action`/`matchesAction`, and `action_handlers`/`actionHandlers` for the app actions pipy's read loop can dispatch.
2. Wire Pi-shaped custom-editor callbacks and action maps.
   - Acceptance: `on_extension_shortcut`/`onExtensionShortcut` and existing `action_handlers`/`actionHandlers` are filled only when absent for dispatchable actions; submit/change remain host-wired; immutable/bad objects fail soft.
3. Translate delegated actions back into existing read-loop sentinels.
   - Acceptance: model/thinking/tool actions preserve the current custom-editor text into the next prompt and return the same internal sentinel strings as the built-in editor; extension shortcuts return the extension-shortcut sentinel.
4. Cover with focused tests and docs.
   - Acceptance: custom-editor tests cover factory keybindings, delegated thinking-cycle action, extension shortcut routing, and existing behavior; docs note this increment as shipped while broader component-library parity remains open.
