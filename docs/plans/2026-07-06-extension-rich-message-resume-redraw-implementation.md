# Extension rich message resume/redraw implementation plan

Reviewed design: `docs/specs/2026-07-06-extension-rich-message-resume-redraw-plan.md` (plan review CLEAN via opus-review-loop).

1. Add a focused redraw primitive.
   - Implement a small helper on the live-session path that clears product-TUI custom-entry rows and replays active-branch `_CustomEntry` / displayable `_CustomMessageEntry` entries from the current `session_tree`.
   - Acceptance: helper reuses `render_extension_custom_entry(...)` for `_CustomEntry`, preserving current-run renderer map, width, expansion state, theme, fail-soft fallback, and live-only output.

2. Wire redraw after successful `/resume` switches.
   - Call the helper after direct `/resume <ref>` and picker-based switches replace `session_tree` and rebuild provider context.
   - Acceptance: stale custom rows from the previous active session are removed and only the newly active branch's visible custom entries are displayed.

3. Add focused tests/gate coverage.
   - Add unit coverage for the redraw helper with a fake terminal UI and fake session tree branches.
   - Extend `scripts/parity_checks/extension_message_renderer_conformance.py --json` to assert resume-redraw semantics.
   - Acceptance: focused tests and conformance gate fail before the implementation and pass after it.

4. Update docs and parity tracking.
   - Update `docs/extension-api.md`, `docs/pi-mono-gap-audit.md`, and `docs/backlog.md` to mark this follow-on shipped and name remaining deferred follow-ons.
   - Acceptance: selected Next Slice is no longer this completed gap.

5. Validate and review.
   - Run `uv run python scripts/parity_checks/extension_message_renderer_conformance.py --json`, `uv run python scripts/parity_checks/extension_conformance_gate.py --json`, `just check`, and prek only if configured.
   - Run the different-family review over the complete diff; fix any ISSUES and repeat gates/review until CLEAN.
