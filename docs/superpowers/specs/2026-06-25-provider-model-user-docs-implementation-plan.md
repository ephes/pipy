# Provider/model user documentation implementation plan

1. Create `docs/providers.md`.
   - Acceptance: page explains shipped provider/model workflows from a user point of view: listing models, choosing a provider/model at startup and in the TUI, credentials, `models.json`, ds4, thinking/images metadata, and known follow-ons.
2. Wire the page into navigation.
   - Acceptance: `docs/index.md` links the page in the outside-in reading list and `zensical.toml` includes it in nav.
3. Mark the slice shipped in planning docs.
   - Acceptance: `docs/user-documentation.md`, `docs/pi-mono-gap-audit.md`, and `docs/backlog.md` no longer list provider/model user docs as open and identify the remaining user-doc slices.
4. Verify.
   - Acceptance: run `uv run pipy --list-models`, `uv run python scripts/parity_checks/provider_catalog_conformance.py --json`, `just docs-build`, and `just check`; then run a different-family review over the complete diff.
