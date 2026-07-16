# Implementation plan: extension chrome requestRender

1. Add the live chrome TUI handle
   - Acceptance: `ToolLoopTerminalUi` can create a small object exposing both Pi-style `requestRender(force=False)` and Pythonic `request_render(force=False)`, both repainting the live frame and not returning a provider-turn sentinel. Requests made during `render()` are coalesced rather than recursively repainting.

2. Update chrome factory invocation
   - Acceptance: widget/header factories can receive `(tui_handle, theme)` and footer factories can receive `(tui_handle, theme, footer_data)`; existing one-arg widget/header and two-arg footer factories continue to work fail-softly. Arity selection uses signature binding so a `TypeError` raised by the body of a Pi-shaped factory is not retried with legacy arguments.

3. Re-render live factory components each frame
   - Acceptance: factory/bare-component regions call their component `render(width)` on each `_render_region_lines` call; width changes still call optional `invalidate()` first; static string/list regions keep snapshot semantics.

4. Add tests and conformance coverage
   - Acceptance: tests prove Pi-shaped factory arguments, same-width live re-render after state changes, and `requestRender()` repainting. The chrome-widget conformance gate includes same-width live re-render coverage.

5. Update docs and parity tracking
   - Acceptance: extension/parity/backlog docs mark live chrome `requestRender` as shipped for chrome components while keeping adjacent deferred items (multi-widget messages, editor integration, tool-render invalidation, non-lifecycle UI driver threading) explicit.
