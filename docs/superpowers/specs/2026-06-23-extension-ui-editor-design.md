# Extension UI Editor Design

Status: draft for parity-loop review, 2026-06-23.

## Gap

Pi exposes `ctx.ui.editor(title, prefill)` for extension commands that need a
focused multi-line text editor. Pipy already has Pi-shaped extension command UI
primitives (`select`, `input`, `confirm`, `custom`) and a raw-mode custom
overlay driver, but no first-class editor helper. Extension authors must either
hand-roll a custom component or fall back to single-line `input`, which leaves a
remaining gap in the richer extension UI surface tracked in
`docs/extension-api.md`.

Reference paths:

- `/Users/jochen/src/pi-mono/packages/coding-agent/src/core/extensions/types.ts`
  (`ExtensionUIContext.editor(title, prefill?)`)
- `/Users/jochen/src/pi-mono/packages/coding-agent/src/core/extensions/runner.ts`
  (`noOpUIContext.editor`)
- `/Users/jochen/src/pi-mono/packages/coding-agent/src/modes/interactive/components/extension-editor.ts`

## Design

Add `ctx.ui.editor(title, prefill=None) -> str | None` as a narrow Python
equivalent of Pi's editor helper:

- In a live product TUI, it opens a focused extension overlay driven by
  `ToolLoopTerminalUi.run_custom_component`.
- The overlay supports multi-line text, cursor movement within the buffer,
  Enter to submit, Shift+Enter for newline where the terminal reports it, and
  Alt+Enter as pipy's documented newline fallback for terminals that do not
  expose distinct Shift+Enter bytes. Esc/Ctrl-C cancel.
- In non-interactive/headless contexts, or when no UI driver is wired, it
  returns `None` without blocking, matching Pi's no-op UI context.
- Display text is sanitized before rendering; the returned value is the raw
  edited text, bounded by normal in-memory command-handler execution rather than
  archived by default.
- Driver exceptions fail soft and return `None`, matching the existing
  `select`/`input`/`confirm` behavior.

This is intentionally not the broader custom editor component API
(`setEditorComponent`) or autocomplete provider API. Pi's extension editor also
supports launching `$VISUAL`/`$EDITOR` through the app external-editor binding;
that temp-file handoff is explicitly deferred to a separate editor-polish slice
so this change can close the first multi-line editor helper without adding a
new process-launching surface.

## Done When

- `pipy_harness.extensions.ExtensionUi` exposes `editor`.
- `ToolLoopTerminalUi` has a reusable extension editor component and driver
  method.
- Unit coverage proves delegation, deterministic headless behavior, display
  sanitization, multiline editing, cursor movement, and cancellation.
- Real-PTY coverage proves the live overlay accepts typed text, newlines, and
  Enter submission without entering the alternate screen.
- `docs/extension-api.md`, `docs/pi-mono-gap-audit.md`,
  `docs/parity-plan.md`, and `docs/backlog.md` reflect the shipped slice.
- Gates pass:
  `uv run python scripts/parity_checks/extension_conformance_gate.py --json`,
  `uv run python scripts/parity_checks/extension_package_conformance.py --json`,
  and `just check`.
