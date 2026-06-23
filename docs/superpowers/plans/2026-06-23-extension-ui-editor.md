# Extension UI Editor Implementation Plan

Status: draft for parity-loop review, 2026-06-23.

1. Public API wiring
   - Add `editor(title, prefill=None) -> str | None` to `ExtensionUiDriver`,
     `ExtensionUi`, and `_CollectingUi`.
   - Acceptance: command handlers can call `ctx.ui.editor(...)`; headless mode
     returns `None`; driver exceptions fail soft.

2. TUI overlay
   - Add `_ExtensionEditorComponent` near the existing extension select/input
     components.
   - Support sanitized rendering, multiline text, left/right/up/down movement,
     backspace, Enter submit, Shift+Enter newline when decoded, Alt+Enter
     newline as pipy's existing fallback, and Esc/Ctrl-C cancel.
   - Add `ToolLoopTerminalUi.run_extension_editor(title, prefill=None)` that
     delegates through `run_custom_component`.
   - Acceptance: component unit tests cover editing and wrapping-safe rendering.

3. Product wiring
   - Wire the live extension UI driver used by command and shortcut dispatch to
     call `ToolLoopTerminalUi.run_extension_editor`.
   - Acceptance: existing command dispatch tests can verify the method reaches
     the driver.

4. Docs and parity notes
   - Update the extension API status, UI surface, suggested-slice list, parity
     plan, gap audit, and backlog closeout language so the shipped editor helper
     is no longer listed as missing.
   - Acceptance: docs clearly leave `setEditorComponent`, autocomplete
     providers, external-editor handoff, and live component re-rendering
     deferred.

5. Verification and review
   - Run focused tests and conformance gates, then `just check`.
   - Run the mandatory Opus review over the complete diff after gates pass.
   - Commit only after the final CLEAN review covers the exact diff.
