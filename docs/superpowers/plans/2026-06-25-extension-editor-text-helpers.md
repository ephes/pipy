# Extension editor text helpers - implementation plan

1. Protocol and collecting UI surface
   - Add `get_editor_text`, `set_editor_text`, and `paste_to_editor` to
     `ExtensionUiDriver` and `ExtensionUi`.
   - Implement `_CollectingUi` headless behavior: `get_editor_text()` returns
     `""`; mutators no-op when no live UI is available; live driver failures are
     fail-soft.
   - Acceptance: focused unit tests instantiate command contexts with and
     without fake live drivers and observe deterministic behavior.

2. Product TUI/live driver wiring
   - Add `get_input_text()` and `paste_input_text(text)` to
     `ToolLoopTerminalUi`.
   - Delegate live extension UI calls through `_LiveExtensionUiDriver`.
   - Acceptance: live-driver unit coverage proves delegation; TUI tests prove
     current/pending text is visible and paste replaces the input buffer for
     this slice.

3. Tests and conformance
   - Add command-dispatch tests for `ctx.ui.get_editor_text`,
     `ctx.ui.set_editor_text`, and `ctx.ui.paste_to_editor`.
   - Update runtime-checkable protocol fake-driver coverage.
   - Acceptance: focused tests pass, followed by
     `uv run python scripts/parity_checks/extension_package_conformance.py --json`
     and `just check`.

4. Docs and closeout
   - Update `docs/extension-api.md`, `docs/backlog.md`,
     `docs/pi-mono-gap-audit.md`, `docs/parity-plan.md`, `docs/pi-parity.md`,
     and `CHANGELOG.md`.
   - Acceptance: docs describe the shipped narrow editor text helper behavior
     without claiming custom editor component or autocomplete-provider parity.
