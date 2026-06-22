This file is for coding agents working in this repository. It records local project instructions that should be visible to Codex, Claude, Pi, and similar tools.

## Start Here
- **Trunk-based development: work directly on `main`.** Do not create
  `feature/*`, topic, or side branches for routine work. If you start on a
  non-`main` branch, switch back to `main` first. See **Source Control** below
  for the full policy.
- Use `docs/backlog.md` as the current task-slice index. It names the next small, reviewable slice and the main deferred boundaries.
- Use `docs/harness-spec.md` for harness architecture, native runtime direction, adapter boundaries, privacy constraints, and broader deferred design.
- Use `docs/session-storage.md` before changing session capture, archive layout, catalog commands, automatic capture, sync behavior, or privacy policy.
- Keep `pipy-native` as the product runtime direction. Codex, Claude, Pi, or other CLI wrapping may be useful for capture/reference work, but must not become the main product execution path unless the project direction explicitly changes.
- Keep capture metadata-first by default; see Session Capture and Workflow Learning Capture below for privacy rules.
- For nontrivial implementation slices, expect focused tests, `just check`, relevant docs updates, and an independent review pass before treating the work as complete.
- Scale repeated review passes to risk: a clean first review can close low-risk planning-only or docs-only slices, while implementation slices usually need a clean follow-up after fixes. Stop after a clean second review unless scope, risk, or implementation changed.

## No-deprecation policy
- pipy has no users yet and stays private until Pi parity is reached. Do not add
  deprecation shims (aliases, deprecation notices) for pipy-only surfaces being
  realigned to Pi — remove them outright and match Pi directly.

## Dotfile Management (chezmoi)
- Dotfiles are managed with **chezmoi** (source: `~/.local/share/chezmoi`, repo: dotfiles).
- Always edit the chezmoi source, not the target file directly. Use `chezmoi edit <target>` or edit the source file in `~/.local/share/chezmoi/` directly.
- After editing, run `chezmoi apply` to deploy changes to the home directory.
- Some files are templates (`.tmpl` suffix). Use chezmoi template syntax when needed.
- To find the source for a managed file: `chezmoi source-path <target>`.

## Documentation Updates
- Implementation and review work is not complete until documentation matches the change when behavior, workflow, or user-facing usage changes.
- Update release notes in the same change when they apply. Treat missing or stale docs or release notes as incomplete work.

## Source Control (trunk-based development)
- **This repo uses trunk-based development.** Do routine work directly on `main`
  in small, reviewable, green increments.
- Do not create `feature/*`, topic, or other side branches for routine work. If
  you start a session and discover you are on a non-`main` branch, switch back
  to `main` before making changes when the worktree is clean. If the branch
  already contains completed work, merge it back to `main` promptly, validate
  `main`, and continue from `main`.
- Run `just check` (and `prek` hooks if a `.pre-commit-config.yaml` is present — `pre-commit` is not installed) and keep each commit's tests/lint/typecheck green so `main` stays releasable.

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
- The dedicated `pipy-session workflow` subcommand has been removed. Use
  `pipy-session append <active-path> --type <event> --summary <summary>` or
  `--event-json` for summary-safe role, subagent, review-outcome, or evaluation
  events when an active record exists.
- Do not put prompts, transcript bodies, tool output, secrets, credentials, or sensitive personal data in appended workflow fields.
- Prefer summary-safe learning events over relying on memory when evaluating patterns such as Codex implementation plus Claude review.
- Prefer descriptive session slugs for intentional records. Avoid generic slugs
  such as `codex-yolo` when the recorder or wrapper allows a better task name.
- Do not add redundant review passes after a clean second review unless risk,
  scope, or implementation changed enough to justify another independent pass.

## Session Learning Checks
- Before backlog grooming, workflow changes, model/provider comparisons, or
  "where are we?" planning, inspect the existing archive through summary-safe
  surfaces such as targeted
  `uv run pipy-session search "<topic>" --json` and
  `uv run pipy-session list --json`. The removed `reflect` command is no longer
  available.
- Use only metadata, event summaries, and Markdown summaries by default. Do not
  inspect or promote raw transcript bodies unless explicitly needed and
  privacy-reviewed.
- Promote repeated lessons into `AGENTS.md`, `docs/backlog.md`, specs, prompts,
  or skills rather than relying on memory.
- Treat one-off session anecdotes as weak signal unless they are supported by
  multiple review outcomes, workflow evaluations, or explicit decisions.

## Parity loop

To drive one pi-mono parity gap end to end (select gap → plan → different-family
plan review → implementation plan → implement → docs → code-review loop until
CLEAN → commit), follow the canonical workflow in
`docs/parity-loop/skill-body.md`. This is the `pipy-parity-loop` skill; the same
body is wrapped per-agent under `.claude/skills/`, `.pipy/skills/`, and
`.pi/skills/`. Drive exactly one gap per invocation (the unattended outer loop is
deferred). Honor the body's hard rules: never self-grade, the review gate is a
mandatory different-family CLEAN, and an operator override is an escalation/stop —
never a pass.

## Parity improve

To consume captured parity-loop lessons and materialize them into gated edits
(skills/docs/tests/harness), follow the canonical workflow in
`docs/parity-loop/improve-body.md`. This is the `parity-improve` skill; the same
body is wrapped per-agent under `.claude/skills/`, `.pipy/skills/`, and
`.pi/skills/`. Application is gated: `just check` + a different-family review
CLEAN + human/judge sign-off for instruction edits. The ledger helper
`scripts/parity_lessons.py` refuses to mark a lesson `applied` without a real
materializing commit, so lessons cannot be closed without being acted on.
