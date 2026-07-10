# Extension custom overlay handle parity plan

Gap: extend the shipped bounded `ctx.ui.custom(..., options)` overlay path so its Pi-shaped handle exposes the focus/visibility helpers documented by Pi, not only `hide`/`update`/`requestRender`.

Pi reference:

- `/Users/jochen/src/pi-mono/packages/coding-agent/src/core/extensions/types.ts` defines `ctx.ui.custom(factory, options)` with optional `overlay`, `overlayOptions`, and `onHandle`. The `onHandle` callback receives an `OverlayHandle`.
- `/Users/jochen/src/pi-mono/packages/tui/src/tui.ts` defines `OverlayOptions` fields `width`, `minWidth`, `maxHeight`, `anchor`, `offsetX`, `offsetY`, `row`, `col`, `margin`, `visible`, and `nonCapturing`; pipy's bounded overlay path already only honors width hints and keeps the rest as deferred positioning/focus-compositing work.
- The same Pi TUI file defines `OverlayHandle` methods: `hide()`, `setHidden(hidden)`, `isHidden()`, `focus()`, `unfocus(options?)`, and `isFocused()`. `hide` permanently removes the overlay; `setHidden` toggles temporary visibility; `focus` brings the overlay to the visual front and gives it input focus; `unfocus` releases focus to fallback or an explicit target; `isFocused` reports focus ownership.
- Pi coding-agent docs (`packages/coding-agent/docs/extensions.md`, Custom Components / Overlay Mode) document `handle.focus()`, `handle.unfocus({ target })`, `handle.setHidden(true/false)`, and `handle.hide()` as the handle API extensions can use.

Pipy design:

- Keep ownership at the existing pipy TUI boundary: `_CustomOverlayHandle` in `src/pipy_harness/native/tui.py` is the opaque handle passed to `onHandle` from `ToolLoopTerminalUi.run_custom_component`.
- This slice is deliberately bounded because pipy still renders overlay and non-overlay custom components through one inline custom UI path. Add the missing methods with deterministic semantics in that path:
  - `hide()` remains the permanent close path and sets the current custom result to `None`.
  - `setHidden(True)` temporarily hides the custom component region and stops routing keys to the component while the custom loop remains open; `setHidden(False)` shows it again.
  - `isHidden()` returns that temporary hidden flag.
  - `focus()` marks the overlay focused and visible; because pipy has only one active custom component in this bounded path, it also resumes key routing.
  - `unfocus(options=None)` marks the overlay unfocused and stops key routing without closing it. The optional `target` is accepted for Pi-shaped duck typing but ignored until pipy has a real overlay stack/focus graph.
  - `isFocused()` returns the focused flag.
  - Existing `requestRender`/`request_render` stay as pipy convenience aliases to repaint.
- Do not implement Pi's full overlay stack, `nonCapturing`, target focus graph, z-order, or anchor/row/col compositing in this slice; keep those documented as deferred broader component-library parity.

Done when:

1. Focused tests prove the handle passed to `onHandle` has Pi-shaped `setHidden`/`isHidden`/`focus`/`unfocus`/`isFocused` methods, plus existing `hide`/`requestRender` aliases.
2. A hidden custom overlay does not call the component's `render` or `handle_input`, then resumes both after `setHidden(False)`/`focus()`. An unfocused-but-visible overlay still renders, but does not route `handle_input` until `focus()` restores input ownership.
3. `hide()` from the handle closes the custom component and returns `None`, preserving existing behavior.
4. `docs/extension-api.md`, `docs/backlog.md`, and `docs/pi-mono-gap-audit.md` describe this bounded overlay-handle parity as shipped and keep the full overlay stack/focus-compositing work deferred.
