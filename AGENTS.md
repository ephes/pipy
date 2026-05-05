This file is for coding agents working in this repository. It records local project instructions that should be visible to Codex, Claude, Pi, and similar tools.

## Start Here
- Use `docs/backlog.md` as the current task-slice index. It names the next small, reviewable slice and the main deferred boundaries.
- Use `docs/harness-spec.md` for harness architecture, native runtime direction, adapter boundaries, privacy constraints, and broader deferred design.
- Use `docs/session-storage.md` before changing session capture, archive layout, catalog commands, automatic capture, sync behavior, or privacy policy.
- Keep `pipy-native` as the product runtime direction. Codex, Claude, Pi, or other CLI wrapping may be useful for capture/reference work, but must not become the main product execution path unless the project direction explicitly changes.
- Keep capture metadata-first by default; see Session Capture and Workflow Learning Capture below for privacy rules.
- For nontrivial implementation slices, expect focused tests, `just check`, relevant docs updates, and an independent review pass before treating the work as complete.

## Dotfile Management (chezmoi)
- Dotfiles are managed with **chezmoi** (source: `~/.local/share/chezmoi`, repo: dotfiles).
- Always edit the chezmoi source, not the target file directly. Use `chezmoi edit <target>` or edit the source file in `~/.local/share/chezmoi/` directly.
- After editing, run `chezmoi apply` to deploy changes to the home directory.
- Some files are templates (`.tmpl` suffix). Use chezmoi template syntax when needed.
- To find the source for a managed file: `chezmoi source-path <target>`.

## Documentation Updates
- Implementation and review work is not complete until documentation matches the change when behavior, workflow, or user-facing usage changes.
- Update release notes in the same change when they apply. Treat missing or stale docs or release notes as incomplete work.

## Session Capture
- Store durable raw coding-agent session records outside git by default, under `~/.local/state/pipy/sessions/<project>/`.
- Project-local raw session data may also live under `.pipy/sessions/`, which is intentionally ignored by git and not synced by the default recipes unless `PIPY_SESSION_DIR` is pointed there.
- Append events while capturing; finalize records when the session ends and treat finalized files as immutable. See `docs/session-storage.md` for the file lifecycle.
- Use JSONL for machine-readable events and a short Markdown summary for human review.
- Do not store secrets, tokens, private keys, credentials, or sensitive personal data. Redact them before writing session records.
- If a platform does not expose a complete raw transcript, store the best available reconstruction and explicitly mark it as partial.
- Session records should explain the goal, important decisions, commands or tools used, files changed, verification performed, and follow-up ideas.
- Commit only durable session policy, schemas, curated lessons, ADRs, prompts, hooks, or skills that are intentionally promoted from raw session data.

## Workflow Learning Capture
- When a session involves implementation, review, subagents, model comparison, or a meaningful workflow decision, record summary-safe workflow events before finalizing the session.
- Use `pipy-session workflow role` for implementer/reviewer/planner roles and model identifiers when known.
- Use `pipy-session workflow subagent` when delegated explorer/worker/subagent work materially affects the result.
- Use `pipy-session workflow review-outcome` after review cycles to capture finding counts by severity and closure counts: accepted, fixed, rejected, and deferred.
- Use `pipy-session workflow evaluation` when there is a useful judgment about whether to keep, switch, or compare a workflow pattern.
- Do not put prompts, transcript bodies, tool output, secrets, credentials, or sensitive personal data in workflow fields.
- Prefer summary-safe learning events over relying on memory when evaluating patterns such as Codex implementation plus Claude review.
