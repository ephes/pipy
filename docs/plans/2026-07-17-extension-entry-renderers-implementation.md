# Durable Extension Entry Renderers — Implementation Plan

Reviewed design: [`../specs/2026-07-17-extension-entry-renderers.md`](../specs/2026-07-17-extension-entry-renderers.md)

1. Add the independent activation contribution.
   - Add public protocol/API registration, immutable registered value, activated
     contribution storage, collector/export, metadata, and entry-specific
     invalid/duplicate reason codes.
   - Thread entry-renderer collision state through ordinary, pending pre-trust,
     and finalized activation paths without coupling it to message renderers.
   - Acceptance: focused activation tests prove valid, invalid, duplicate,
     same-type message+entry, pending-finalization, metadata, and collection
     behavior.

2. Add the Pi-shaped durable-entry renderer dispatch.
   - Project a stored `CustomEntry` to a detached JSON-safe mapping with all Pi
     fields, pass `EntryRenderContext(expanded, width, theme)`, invoke exactly
     `(entry, context)`, and accept only the existing bounded component contract.
   - Treat missing renderer, `None`, bad output, async output, and renderer or
     component failures as omitted live output without leaking data/errors.
   - Acceptance: unit tests prove exact fields, copy isolation, context values,
     styled output, omission, and fail-soft bounds.

3. Rewire live-session ownership to the TUI.
   - Carry separate message/entry registries through activation and reload.
   - Persist `append_entry` in every mode, but render only with a live product
     TUI; remove the headless diagnostic/fallback for durable entries.
   - Dispatch new appends, startup replay, `/resume` redraw, expansion rerender,
     and reload redraw from the stored entry object. Keep displayed custom
     messages on their existing registry and fallback path.
   - Acceptance: session/TUI tests prove no-renderer omission, TUI-only
     invocation, same-type routing, replay/resume/expand/reload behavior, and no
     session mutation or headless output.

4. Close conformance and documentation.
   - Add `extension_entry_renderer_conformance.py`, wire its marker into the
     golden extension gate when appropriate, and update focused test manifests.
   - Update `docs/extension-api.md`, `docs/backlog.md`, `docs/parity-plan.md`,
     `docs/pi-mono-gap-audit.md`, and `CHANGELOG.md` to mark only this slice
     shipped and name the next independent gap.
   - Acceptance: focused tests/conformance pass; `just check` and prek (when
     configured) pass; a fresh direct different-family review is `CLEAN` over
     the exact complete diff.

5. Commit and capture evidence.
   - Commit the reviewed, green implementation on `main`, record verification
     and review evidence, inspect the session for reusable summary-safe lessons,
     and append/commit any lesson ledger change separately.
   - Acceptance: `main` contains the implementation commit, the worktree is
     clean except for any intentionally separate lesson commit, and nothing is
     pushed.
