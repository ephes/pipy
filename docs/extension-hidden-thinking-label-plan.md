# Extension UI hidden-thinking label parity plan

Gap: implement Pi's narrow extension UI `setHiddenThinkingLabel` surface for live interactive product-TUI command/shortcut contexts.

## Pi reference

- `/Users/jochen/src/pi-mono/packages/coding-agent/src/core/extensions/types.ts`: `ExtensionUIContext.setHiddenThinkingLabel(label?: string): void`; omitting the argument restores the default hidden-thinking label.
- `/Users/jochen/src/pi-mono/packages/coding-agent/src/modes/interactive/interactive-mode.ts`: `setHiddenThinkingLabel(label?)` stores `label ?? "Thinking..."`, pushes that label into existing assistant message components and the streaming component, and requests a render.
- `/Users/jochen/src/pi-mono/packages/coding-agent/src/modes/rpc/rpc-mode.ts`: headless/RPC `setHiddenThinkingLabel` is a no-op because it requires TUI message rendering access.
- `/Users/jochen/src/pi-mono/packages/coding-agent/src/modes/interactive/components/assistant-message.ts`: when thinking blocks are hidden, the component renders the configured label as italic thinking text instead of the hidden thinking body.

## Pipy design

- Add snake_case and Pi-shaped camelCase methods to `ExtensionUi` / `_CollectingUi`: `set_hidden_thinking_label(label: str | None = None)` and `setHiddenThinkingLabel(label: str | None = None)`.
- Live contexts delegate to the product-TUI driver. Headless/no-UI contexts no-op, matching Pi RPC.
- Add driver support on `_ExtensionUiDriver` and `ToolLoopTerminalUi`: store a default label of `"Thinking..."`; `None` restores that default; non-`None` labels are stringified and sanitized by the existing frame rendering path.
- When `thinking_hidden` is true and live reasoning text exists, render one reasoning-style row containing the configured label instead of suppressing the block completely. Existing deferred-reasoning behavior remains unchanged: hidden settled reasoning is retained and revealed when thinking is unfolded; this slice does not add archived labels.
- Do not persist labels to the session tree or settings. The label is live-only extension UI state.

## Acceptance criteria

1. Extension dispatch exposes both `ctx.ui.set_hidden_thinking_label(...)` and `ctx.ui.setHiddenThinkingLabel(...)` to live command/shortcut contexts and they call through to the UI driver.
2. Headless dispatch treats both helpers as deterministic no-ops.
3. Product-TUI rendering shows the default `Thinking...` label while thinking is folded and live reasoning exists; a custom label replaces it; calling the helper with no argument restores the default.
4. Docs mark `setHiddenThinkingLabel` as shipped for live command/shortcut contexts and preserve the live-only/no-op headless boundary.
5. `uv run python scripts/parity_checks/extension_package_conformance.py --json` and `just check` pass.
