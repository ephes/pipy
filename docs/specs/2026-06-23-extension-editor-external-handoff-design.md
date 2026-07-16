# Extension Editor External Handoff Design

Status: draft for parity-loop plan review, 2026-06-23.

## Selected Gap

Close one small slice of the highest-ranked extension/package platform follow-on:
Pi's extension editor overlay supports opening `$VISUAL`/`$EDITOR` from the
extension multi-line editor (`ExtensionEditorComponent`, Ctrl+G via
`app.editor.external`). Pipy's `ctx.ui.editor(title, prefill)` currently ships
the in-frame multi-line editor but still marks that external-editor handoff as
deferred.

Reference paths:

- `/Users/jochen/src/pi-mono/packages/coding-agent/src/modes/interactive/components/extension-editor.ts`
- `/Users/jochen/src/pi-mono/packages/coding-agent/src/modes/interactive/components/custom-editor.ts`
- `/Users/jochen/src/pi-mono/packages/coding-agent/src/core/keybindings.ts`
- `/Users/jochen/src/pi-mono/packages/coding-agent/docs/keybindings.md`

## Pi Behavior To Match

Pi's extension editor:

- Shows the external-editor key hint only when `$VISUAL` or `$EDITOR` is set.
- Handles the `app.editor.external` keybinding (`ctrl+g` by default).
- Writes the current editor text to a temp markdown file.
- Temporarily stops the TUI, launches the configured editor with inherited
  stdio, and resumes/re-renders the TUI when the editor exits.
- Replaces the in-frame editor text only when the editor exits successfully.
- Deletes the temp file best-effort.

## Pipy Implementation Shape

Keep this inside pipy's existing stdlib terminal boundary:

1. Extend `_ExtensionEditorComponent` in `src/pipy_harness/native/tui.py` with
   an optional external-editor callback and a dynamic hint.
2. Have `ToolLoopTerminalUi.run_extension_editor(...)` pass a callback that:
   writes current text to a private temp file under the OS temp dir, restores
   the terminal mode before spawning, runs the configured editor using
   `subprocess.run(..., stdin/stdout/stderr inherited)`, re-enters raw mode
   afterwards when possible, reloads the temp file only on exit code 0, strips
   one trailing newline to match Pi, and repaints the live frame.
3. Recognize decoded `ctrl-g` in the extension editor overlay as the default
   `app.editor.external` key. This is consistent with the existing keybinding
   table and the current decoder.
4. Preserve deterministic headless behavior: `ctx.ui.editor(...)` still returns
   `None` when no live UI driver is present.

No new runtime dependencies, no shell invocation for the editor command unless
the platform requires it, and no session/archive writes of editor content.

## Acceptance Criteria

1. A focused unit or PTY test proves Ctrl+G launches an injected editor command,
   the TUI leaves raw mode while the command runs, and successful edits become
   the submitted editor result.
2. A focused regression test proves a failing editor exit keeps the original
   in-frame text.
3. Existing `ctx.ui.editor` submit/newline/cancel behavior remains covered.
4. `docs/extension-api.md`, `docs/pi-mono-gap-audit.md`, `docs/parity-plan.md`,
   and `docs/backlog.md` no longer list this exact external-editor handoff as
   deferred.
5. `just check` passes before the final different-family review.
