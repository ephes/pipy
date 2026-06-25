# Session User Documentation Implementation Plan

Acceptance criteria apply to this documentation-only parity slice.

1. Add `docs/sessions.md`.
   - Cover native product session storage, session lifecycle, startup flags, interactive slash commands, branching, resume/fork/clone workflows, export/share pointers, and the `pipy-session` catalog split.
   - Acceptance: the page describes shipped pipy behavior and cites current command names from `uv run pipy repl --help`.
2. Add `docs/compaction.md`.
   - Cover manual `/compact`, automatic compaction, durable `compaction` entries, settings knobs, limitations, extension gate behavior, and how compaction relates to sessions/export.
   - Acceptance: the page avoids claiming model-authored summaries and describes the current metadata/count summary behavior.
3. Wire documentation navigation.
   - Update `docs/index.md` and `zensical.toml` to include both pages near the other user-facing guides.
   - Acceptance: `just docs-build` can resolve the new pages and links.
4. Update planning and release notes.
   - Mark the session docs slice shipped in `docs/user-documentation.md`; update `docs/backlog.md` current user-doc gap wording; add a `CHANGELOG.md` entry.
   - Acceptance: planning docs no longer list session docs as the next missing user-doc slice.
5. Verify.
   - Run `uv run python scripts/parity_checks/session_tree_conformance.py --json`, `just docs-build`, and `just check`.
   - Acceptance: all gates pass before final different-family review.
