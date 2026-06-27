# Extension `paste_to_editor` implementation plan

1. Update live TUI paste semantics.
   - Change `ToolLoopTerminalUi.paste_input_text` to insert via the existing
     bracketed-paste insertion path at `input_cursor` instead of replacing the
     whole buffer.
   - Acceptance: pasted text preserves prefix/suffix text and cursor advances by
     the inserted length.
2. Add focused coverage.
   - Extend chrome-driver and dispatch tests so `set_editor_text` replacement and
     `paste_to_editor` insertion are both asserted.
   - Acceptance: tests fail on the old replace-only paste implementation.
3. Update docs/release notes.
   - Remove stale wording in the extension API/backlog/spec plan that said
     paste currently replaces the prompt buffer; mark the Pi paste semantic gap
     closed.
   - Acceptance: parity docs describe insert-at-cursor live behavior and
     headless no-op/RPC fallback boundaries.
4. Run gates and review.
   - Run `uv run python scripts/parity_checks/extension_package_conformance.py --json`,
     `just check`, and the required different-family review over the complete
     diff before committing.
