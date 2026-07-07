# Parity plan: `ctx.ui.custom` overlay options

Gap: extension platform follow-on — broaden Pi-shaped custom component UI by matching Pi's `ctx.ui.custom(factory, options)` option surface for overlay/non-overlay selection and overlay handle callbacks.

Pi reference:

- `/Users/jochen/src/pi-mono/packages/coding-agent/src/core/extensions/types.ts` defines `ExtensionUIContext.custom(factory, options?)`. The factory receives `(tui, theme, keybindings, done)` and returns a component. Options fields are optional: `overlay?: boolean`, `overlayOptions?: OverlayOptions | (() => OverlayOptions)`, and `onHandle?: (handle: OverlayHandle) => void`.
- `/Users/jochen/src/pi-mono/packages/coding-agent/src/modes/interactive/interactive-mode.ts:2368-2448` defaults `overlay` to `false`. Non-overlay replaces the editor container, restores the saved editor text, restores focus, requests render, resolves the `done` result, and disposes the component. Overlay mode calls `ui.showOverlay(component, resolveOptions())`; `resolveOptions()` uses static/dynamic `overlayOptions`, else falls back to `{width: component.width}` when present. `onHandle(handle)` is invoked after the overlay is shown. Close hides the overlay, resolves the result, and disposes the component. Factory rejection restores the editor in non-overlay mode and rejects.

Pipy scope:

- Pipy already exposes a synchronous Python-owned `ctx.ui.custom(factory)` for live product TUI contexts and deterministic `None` for headless contexts. Its factory is Python-shaped as `factory(done)` and components expose `render(width)` / `handle_input(key)`; this slice preserves that boundary rather than adding TypeScript `tui/theme/keybindings` parameters.
- Add an optional `options` argument to `ExtensionUi.custom`, `_CollectingUi.custom`, `ToolLoopTerminalUi.run_custom_component`, and the live driver wrapper. No method-name alias is needed (`custom` is already the Pi-shaped method name), but options should accept both Pi camelCase field names and Python snake_case field names: `overlay` (bool, default false), `overlayOptions`/`overlay_options` (dict or callable, safely ignored except width), and `onHandle`/`on_handle` callback.
- Because pipy's TUI has one inline overlay renderer, both `overlay=True` and `overlay=False` render through the existing custom overlay paint path in this bounded slice; the accepted `overlay` field is API-compatible but has no visual/stacking behavioral effect yet. The parity behavior for this bounded slice is API compatibility: options are accepted, factory still runs only with live UI, `done(result)` resolves/returns the result, component `dispose()` is called on close if present, bad dispose is ignored, headless mode does not construct the component, and `onHandle` receives a minimal handle with `hide()` and `update()`/`requestRender()` no-op repaint hooks. `overlayOptions` width, when provided as `{width: N}` or by callable, constrains the width passed to `component.render(width)`; otherwise the live terminal width is used.
- Do not implement a full Pi overlay stack, dynamic positioning, or source-compatible component library in this slice.

Done when:

1. Focused tests cover default custom behavior, options acceptance, `overlay=True` being accepted without changing the bounded inline-overlay behavior, width resolution from static and callable overlay options, `onHandle`, dispose-on-close, and headless no-construction.
2. `docs/extension-api.md`, `docs/backlog.md`, and `docs/pi-mono-gap-audit.md` describe the shipped bounded options support and leave broader component-library/overlay-stack parity deferred.
3. `just check` passes and the different-family review gate returns CLEAN for the complete diff before commit.
