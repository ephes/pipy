This file is for coding agents working in this repository. It records local project instructions that should be visible to Codex, Claude, Pi, and similar tools.

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
