# Implementation plan: `ctx.ui.custom` overlay options

1. Extend the extension UI protocol and collecting implementation.
   - Acceptance: `ExtensionUi.custom(factory, options=None)` and `_CollectingUi.custom(...)` accept the optional options object without constructing components when `has_ui` is false or no driver is wired.
2. Thread options through the live product driver.
   - Acceptance: `ToolLoopSession` passes options to `ToolLoopTerminalUi.run_custom_component`, and tests can exercise the live driver without changing command dispatch semantics.
3. Add bounded TUI support for Pi option fields.
   - Acceptance: `run_custom_component(factory, options=None)` accepts `overlay`, `overlayOptions`/`overlay_options`, and `onHandle`/`on_handle`; resolves static/callable width; calls `onHandle` with a minimal handle; disposes components on close; and keeps both overlay states on the existing inline overlay renderer.
4. Add focused tests.
   - Acceptance: tests cover default result flow, static/callable width options, `overlay=True` acceptance, handle callbacks, dispose-on-close, and headless no-construction.
5. Update docs and parity notes.
   - Acceptance: extension API/backlog/audit mark bounded custom overlay options shipped and keep full overlay stack/component-library parity deferred.
