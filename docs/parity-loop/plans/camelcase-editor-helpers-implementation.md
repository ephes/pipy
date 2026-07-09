# Implementation plan: Extension UI camelCase editor text helpers

1. Extend the extension UI public surface.
   - Add `getEditorText`, `setEditorText`, and `pasteToEditor` to the `ExtensionUi` protocol.
   - Implement them on `_CollectingUi` as thin delegates to the existing snake_case helpers.
   - Acceptance: no new driver protocol methods; existing live/headless behavior is reused.

2. Add focused tests.
   - Cover live command dispatch using the camelCase names and assert the same driver calls/state changes as the snake_case helpers.
   - Cover headless command dispatch using the camelCase names and assert empty-string read plus no-op writes.
   - Check extension conformance surfaces for any enumerated UI method list and update if present; this slice found no such enumerated list in `scripts/parity_checks/extension_conformance_gate.py` or sibling extension gates.
   - Acceptance: focused extension dispatch/live-session tests pass.

3. Update docs and parity tracking.
   - Document camelCase as the Pi-canonical spelling in `docs/extension-api.md` while retaining snake_case as Python convenience aliases.
   - Update `docs/backlog.md` and `docs/pi-mono-gap-audit.md` to reflect this slice as shipped.
   - Acceptance: docs no longer describe these Pi-shaped names as missing/deferred.

4. Verify and review.
   - Run focused tests, `just check`, and the mandatory different-family review over the complete diff.
   - Acceptance: gates green and review CLEAN before commit.
