# Extension Editor Component Implementation Plan

Reviewed design: `docs/superpowers/specs/extension-editor-component-plan.md` (plan review CLEAN via Pi review loop).

1. Extend extension UI protocols and contexts.
   - Add snake_case and camelCase editor-component methods to the UI protocols and concrete headless/live contexts in `src/pipy_harness/native/extension_runtime.py`.
   - Acceptance: headless command contexts return `None` and do not error when setting a factory-like object or `None`.

2. Add live UI-driver storage.
   - Add `set_editor_component` / `get_editor_component` to the session UI driver boundary in `src/pipy_harness/native/tool_loop_session.py`.
   - Store only an opaque in-memory object, clear on `None`, and never call or persist it.
   - Acceptance: command/shortcut contexts can set an object, read back the identical object, and clear it.

3. Cover with focused tests.
   - Add dispatch tests for live store/round-trip/clear and headless Pi-RPC-style no-op behavior.
   - Add chrome-driver/session-driver unit coverage if needed for the live boundary.

4. Update docs and parity tracking.
   - Update `docs/extension-api.md`, `docs/pi-mono-gap-audit.md`, and `docs/backlog.md` to say the API-compatibility storage slice shipped, while full custom editor rendering remains deferred.
   - Keep the tracked reviewed plan files as run evidence for this slice.

5. Verify and review.
   - Run `uv run python scripts/parity_checks/extension_package_conformance.py --json`.
   - Run `just check` (and `prek run --all-files` only if `.pre-commit-config.yaml` exists).
   - Run the different-family review loop over the complete diff until CLEAN, then commit.
