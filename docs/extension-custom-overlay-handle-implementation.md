# Extension custom overlay handle implementation plan

1. Add focused tests in `tests/test_native_extension_custom_ui.py` for the live `ctx.ui.custom` handle:
   - capture the handle delivered through `onHandle` and assert it exposes `hide`, `setHidden`, `isHidden`, `focus`, `unfocus`, `isFocused`, `requestRender`, and `request_render`;
   - exercise `setHidden(True)`/`setHidden(False)` with synthetic input to prove hidden overlays skip both rendering and key delivery, then resume;
   - exercise `unfocus()`/`focus()` to prove visible unfocused overlays still render but skip key delivery until focused again;
   - exercise `hide()` to prove it closes the custom component with a `None` result.
2. Extend `ToolLoopTerminalUi` custom overlay state with `_custom_overlay_hidden` and `_custom_overlay_focused`, reset them at the start/end of each `run_custom_component` invocation, and paint after state changes.
3. Extend `_CustomOverlayHandle` in `src/pipy_harness/native/tui.py` with Pi-shaped visibility/focus methods. Keep target handling accepted but ignored because pipy's bounded path has no overlay focus graph yet.
4. Gate `_custom_overlay_region_lines` on the hidden flag, and gate the `run_custom_component` key dispatch on focused-and-not-hidden while keeping the loop alive.
5. Update parity docs (`docs/extension-api.md`, `docs/backlog.md`, `docs/pi-mono-gap-audit.md`) to mark bounded overlay-handle methods shipped and keep full overlay stack/focus-compositing deferred.
6. Run focused tests, `just check`, and the final different-family review over the complete diff.
