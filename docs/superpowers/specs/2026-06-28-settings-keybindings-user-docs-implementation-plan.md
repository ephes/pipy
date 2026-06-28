# Settings and keybindings user-docs implementation plan

Reviewed design: `docs/superpowers/specs/2026-06-28-settings-keybindings-user-docs-plan.md` (Pi review CLEAN).

1. Draft `docs/settings.md`.
   - Acceptance: names pipy's global/project settings paths, project override behavior, `/settings` and `/reload`, common examples, grouped field reference, and explicit notes for pipy divergences (default-off install telemetry, `.pipy` path, no npm/PyPI package sources yet, some accepted settings are future/no-op where runtime support is absent).
2. Draft `docs/keybindings.md`.
   - Acceptance: names pipy's keybindings path, key syntax, reload/migration behavior, all shipped action ids/defaults from `pipy_harness.native.keybindings`, and customization examples.
3. Wire navigation.
   - Acceptance: `zensical.toml` nav includes both pages near other user docs; `docs/index.md` includes them in the outside-in reading list.
4. Update planning/parity docs and changelog.
   - Acceptance: `docs/user-documentation.md`, `docs/backlog.md`, and `docs/pi-mono-gap-audit.md` mark settings/keybindings user docs as shipped or remove them from remaining-user-doc lists; `CHANGELOG.md` has one Added note.
5. Verify.
   - Acceptance: run `just check`; run `prek run --all-files` only if `.pre-commit-config.yaml` exists; then run the required different-family review over the complete diff before commit.
