# Extension Terminal Input Plan

Gap: implement the narrow Pi extension UI `onTerminalInput` surface for local interactive product-TUI command/shortcut contexts.

Pi reference:

- `packages/coding-agent/src/core/extensions/types.ts`: `TerminalInputHandler = (data: string) => { consume?: boolean; data?: string } | undefined`; `ExtensionUIContext.onTerminalInput(handler)` returns an unsubscribe function.
- `packages/coding-agent/src/modes/interactive/interactive-mode.ts`: `onTerminalInput` delegates to `addExtensionTerminalInputListener`, which calls `this.ui.addInputListener(handler)`, keeps every returned unsubscribe in `extensionTerminalInputUnsubscribers`, and removes it from that set when the returned disposer is called. `clearExtensionTerminalInputListeners()` calls all stored disposers and clears the set during interactive teardown/reset.
- `packages/coding-agent/src/modes/rpc/rpc-mode.ts`: `onTerminalInput()` is unsupported outside the interactive terminal and returns a safe no-op unsubscribe.

Pipy design:

- Add `ctx.ui.on_terminal_input(handler)` and Pi-shaped `ctx.ui.onTerminalInput(handler)` to the extension UI object. The handler is live-only and receives decoded terminal key strings from the product TUI's existing key reader (for example printable characters and symbolic keys such as `enter`, `tab`, or `esc`). Pipy does not archive these raw inputs.
- Return a disposer callable from both snake_case and camelCase methods. Headless/no-UI contexts return a deterministic no-op disposer and never call the handler, matching Pi RPC's safe no-op.
- Store live listeners on `ToolLoopTerminalUi`; add/remove listeners in insertion order. On each key read by `read_line`, call the listeners before built-in editor handling. If a listener returns a mapping/object with `consume: true`, pipy consumes the key and does not run built-in handling. If the result includes a string `data`, pipy feeds that replacement text back through the existing pending-input path so built-in handling processes it next. Undefined/None or failing handlers are fail-soft and do not block built-in handling.
- Clear all extension terminal input listeners when extension chrome is cleared so `/reload`/session teardown does not accumulate stale registrations. Disposers are idempotent.

Done when:

1. Focused tests prove live snake_case/camelCase registration, disposer idempotence, consume semantics, replacement-data semantics, fail-soft handlers, and headless no-op behavior.
2. Docs mark `onTerminalInput` as shipped for local interactive contexts and preserve the deferred boundary for full custom editor/component input integration.
3. `uv run python scripts/parity_checks/extension_package_conformance.py --json` and `just check` pass.
