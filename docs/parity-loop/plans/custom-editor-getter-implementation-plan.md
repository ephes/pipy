# Custom Editor Getter Implementation Plan

1. Add regression coverage for live getter semantics.
   - Acceptance: tests fail on current behavior when a custom editor factory raises or returns `None`, because `get_editor_component()` must still return the configured factory.

2. Align the live TUI getter with Pi.
   - Acceptance: `ToolLoopTerminalUi.get_editor_component()` returns the stored factory object whenever one is configured, regardless of whether the constructed component is active; after clearing it returns `None`.

3. Refresh parity docs.
   - Acceptance: `docs/extension-api.md`, `docs/backlog.md`, and `docs/pi-mono-gap-audit.md` mention that the getter semantics increment shipped and keep broader component-library parity deferred.

4. Verify and review.
   - Acceptance: focused tests and `just check` pass, and the different-family review gate is CLEAN over code and docs.
