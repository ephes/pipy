# Extension chrome requestRender slice plan

Gap: live per-frame `requestRender` support for extension chrome components.

## Pi reference

Pi exposes the live TUI object to extension chrome factories:

- `packages/coding-agent/src/core/extensions/types.ts`: `setWidget(key, content, options)` accepts a factory `(tui, theme) => Component & { dispose?(): void }`; `setFooter(factory)` accepts `(tui, theme, footerData)`; `setHeader(factory)` accepts `(tui, theme)`.
- `packages/coding-agent/src/modes/interactive/interactive-mode.ts`: `setExtensionWidget`, `setExtensionFooter`, and `setExtensionHeader` call those factories with `this.ui`, then add/keep the returned component in the live component tree. Pi calls `this.ui.requestRender()` after set/replace/restore.
- `packages/tui/src/tui.ts`: `requestRender(force = false)` schedules a repaint, with forced repaint clearing previous frame state.

Pinned behavior for this slice:

1. Chrome factories receive a TUI/request-render handle as their first argument, followed by theme, and footer factories receive footer data third.
2. A component object returned from a factory remains live; each repaint may call `component.render(width)`, not only initial set or width-change.
3. A component can call `tui.requestRender()` to request a live repaint without a provider turn.
4. The slice is bounded to product-TUI chrome regions already supported by pipy (`set_widget`, `set_header`, `set_footer`). It does not implement Pi's full async render scheduler, overlay/component input, or multi-widget message components.

## Pipy design

Pipy already stores `_ChromeRegion.source`, `component`, `snapshot`, `width`, and `is_factory`, and already keeps factory components for disposal. It also has `ToolLoopTerminalUi.paint()` as the live repaint boundary.

Implementation plan:

1. Add a small extension chrome TUI handle owned by `ToolLoopTerminalUi` with `requestRender(force=False)` and `request_render(force=False)` methods. Both request a fail-soft repaint without creating a provider turn. Calls made during a component `render()` are coalesced into one follow-up repaint rather than recursing; `force` is accepted for Pi shape, but pipy's renderer repaints the full live region already, so it does not need separate forced state clearing.
2. When `_build_region` invokes a chrome factory, use signature binding to prefer Pi-shaped arguments: widgets/header `(tui_handle, theme)`, footer `(tui_handle, theme, footer_data)`. Keep compatibility with already-shipped pipy one-arg/two-arg factories by selecting the old call shape only when the signature cannot bind the Pi-shaped call, so a `TypeError` raised inside a correctly-shaped factory remains a fail-soft factory failure and is not retried with duplicate side effects.
3. Change `_render_region_lines` for factory/component regions to re-run `render_chrome_component` on every frame, not only after width changes. On width changes, still call optional `invalidate()` before rendering. On render failure, keep the existing fail-soft drop behavior. Static line regions remain snapshot-only.
4. Add focused tests proving factory arity and request-render liveness: a widget factory receives a handle with `requestRender`, the component's second render is visible at the same width after state changes, calling `requestRender()` triggers a repaint, in-render `requestRender()` is coalesced, and body-raised `TypeError` does not double-invoke the factory.
5. Update extension/parity docs and the chrome-widget conformance gate to mark this slice shipped and cover same-width live re-render.

Done when: focused tests and `scripts/parity_checks/extension_chrome_widgets_conformance.py --json` pass, docs remove this requestRender item from the deferred list, `just check` is green, and a different-family review is CLEAN over the final diff.
