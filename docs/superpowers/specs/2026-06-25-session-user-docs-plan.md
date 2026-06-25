# Session User Documentation Parity Plan

## Gap

User documentation parity still lacks a Pi-like session guide for pipy's shipped native product session tree. `docs/user-documentation.md` lists session docs as implementation slice 4, while `docs/backlog.md` ranks user documentation parity as a highest-impact remaining gap. Pi's reference README documents session storage, startup flags, branching, `/tree`, `/fork`, `/clone`, and compaction from an outside-in user perspective; pipy's existing `docs/session-tree.md` is an implementation spec rather than a product guide.

Reference paths:

- `/Users/jochen/src/pi-mono/packages/coding-agent/README.md` (`Sessions` and `Compaction` sections)
- `docs/session-tree.md`
- `docs/user-documentation.md`
- current `uv run pipy repl --help`

## Scope

Add user-facing session documentation for shipped pipy behavior, not new runtime features. The slice will create `docs/sessions.md` and `docs/compaction.md`, wire both pages into the docs index and `zensical.toml`, and update planning/release notes to mark the user-doc session slice shipped. The pages will explicitly explain the product native session tree, startup/session flags, in-app session slash commands, branch/fork/clone workflows, durable compaction behavior, export/share pointers, and the separate `pipy-session` metadata/catalog utility.

Out of scope:

- Runtime changes to session storage, selectors, compaction, or export.
- New conformance gates.
- Copying Pi-only features that pipy does not ship; any differences will be labeled as current pipy behavior or follow-ons.

## Design

1. Use Pi's session docs shape as the user-facing model: management, branching, compaction, and storage locations.
2. Use pipy's shipped session-tree spec and CLI help as source of truth for command names and flags.
3. Keep privacy/storage wording aligned with project direction: native sessions are product transcripts; `pipy-session` is a separate summary-safe learning/catalog tool.
4. Keep docs navigation complete so `just docs-build` sees the new pages.
5. Update `docs/user-documentation.md`, `docs/backlog.md`, and `CHANGELOG.md` in the same diff so planning status and release notes match the added user docs.

## Done when

- `docs/sessions.md` and `docs/compaction.md` exist as outside-in user guides.
- `docs/index.md` and `zensical.toml` link both pages.
- `docs/user-documentation.md` marks session docs shipped; `docs/backlog.md` no longer says sessions docs remain open; `CHANGELOG.md` records the user-doc addition.
- Verification passes: `just docs-build` and `just check`.
