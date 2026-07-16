# Pi-parity follow-ups: skill advertisement, theme-in-settings, Pi-faithful command set — Design

Status: design approved 2026-06-20. Follow-ups surfaced during the top-level CLI
cleanup ([2026-06-20-top-level-cli-cleanup-design.md](2026-06-20-top-level-cli-cleanup-design.md)),
where `/skill` and `/theme` realignments were deferred because the underlying
Pi-shaped mechanisms (system-prompt skill advertisement; theme selection in
`/settings`) were not yet wired. This spec wires them and, given a new policy
decision, converts the cleanup's deprecated aliases into hard removals.

## Policy decision (approved 2026-06-20): no deprecations

pipy has no users yet and stays private until Pi parity is reached. Therefore
deprecation shims (aliases, deprecation notices) are pure cost. **Remove
pipy-only surfaces outright and match Pi directly.** This is recorded in
`AGENTS.md` so future work follows it. It supersedes the "pragmatic mix /
deprecated alias" approach used in the CLI-cleanup Slice 3 for `/clear` and
`/status`.

## Goal

Reach Pi's actual model for skills and themes, and a Pi-faithful slash-command
set:

- Skills are advertised in the system prompt (name + description + location) and
  loaded by the model on demand via the `read` tool — Pi's model. `/skill` stays
  (Pi keeps a `/skill:name` command too).
- Theme selection lives in the `/settings` menu (Pi's model); the pipy-only
  `/theme` command is removed. `--theme`/`--no-themes` load flags are unchanged.
- The pipy-only commands `/clear`, `/status`, `/help` are removed outright (Pi
  has none; equivalents are `/new`, `/session`, `/hotkeys`). End state: pipy's
  slash set == Pi's set + `/skill`.

## Pi reference (verified against `/Users/jochen/src/pi-mono`)

- `packages/coding-agent/src/core/skills.ts` `formatSkillsForPrompt`: emits
  `<available_skills>` with per-skill `<name>`, `<description>`, `<location>`
  (the skill's `filePath`), prefaced by "Use the read tool to load a skill's
  file when the task matches its description" plus a note to resolve a skill's
  relative paths against the skill directory. `system-prompt.ts` appends it
  **only when the `read` tool is available** (`customPromptHasRead`).
- Pi keeps `/skill:name` expansion (`agent-session.ts`), so a skill command is
  parity, not divergence.
- `packages/coding-agent/src/core/slash-commands.ts` built-ins: no `/clear`,
  `/status`, `/theme`, `/help`, or literal `/template`. Theme selection is in the
  settings selector; templates are dynamic `/<name>` commands.

## Slice A — Wire the skill advertisement (#11)

**Files:** `src/pipy_harness/native/skills.py`, `src/pipy_harness/adapters/native.py`,
and the system-prompt build path; tests under `tests/`.

- Rewrite `compose_skills_system_block(skills)` to Pi's shape: a header
  ("The following skills provide specialized instructions…", "Use the read tool
  to load a skill's file when the task matches its description.", and the
  relative-path-resolution note) followed by an `<available_skills>` block with
  per-skill `<name>`, `<description>`, and **`<location>`** (absolute path),
  XML-escaped. Empty `skills` → empty string.
- `SkillFile` must expose the skill's absolute filesystem path for `<location>`
  (today it carries `path_label`, workspace-relative for in-workspace files).
  Add/derive an absolute-path field without leaking it to the archive
  (`safe_skill_metadata` stays path-label/sha/bytes only).
- Inject the block in `adapters/native.py` `run()` after
  `compose_system_prompt(base_prompt, discovery)`, **only when the `read` tool is
  in the active tool set** (mirror Pi's gate). Thread the discovered skills into
  this build path (the adapter must receive the discovered `SkillFile`s).
- Add each discovered skill's **parent directory** to the read-only
  `reference_roots` passed to the tool session, so the model can `read` skills
  outside cwd (`~/.pipy/skills`, XDG, package roots). This reuses the existing
  absolute-path-under-reference-root allowance in `read_only_tool.py` (~621).
  Workspace-local skills already read via relative path.
- **Keep `/skill`** unchanged.

**Testing:**
- A discovered skill's name/description/absolute location appears in the built
  system prompt; the block is absent when the `read` tool is excluded
  (`--no-builtin-tools`/exclude-tools equivalent or an empty/`read`-less set).
- The skill's parent dir is in the session `reference_roots`, and the read tool
  can load a skill body from a global (outside-cwd) skill dir; a non-skill path
  outside cwd is still refused.
- Archive boundary unchanged: `safe_skill_metadata` still emits only
  path_label/sha256/byte_length/truncated — no bodies, descriptions, or absolute
  paths leak to JSONL/Markdown/the metadata archive.
- XML escaping: a skill whose name/description contains `<`/`&`/`"` is escaped.

## Slice B — Theme selection in `/settings`, remove `/theme` (#12)

**Files:** `src/pipy_harness/native/tool_loop_session.py` (settings dialog +
`/theme` handler), `src/pipy_harness/native/repl_input.py` (registries); tests.

- Add a **Theme** row to `_settings_dialog_rows`. Add an `action == "theme"`
  branch in the `_drive_settings_dialog` loop that opens a theme picker using the
  same pattern as the `/model` selector (`run_model_selector` →
  `run_theme_selector` or a reused selector), listing available themes and
  applying the chosen one via `select_theme`. Settings remains the source of
  truth (the chosen theme persists like the current `/theme <name>` path).
- **Remove the `/theme` command** entirely: its handler in
  `tool_loop_session.py`, its entries in the `repl_input.py` completion/
  description registries, and any help/menu text listing it. `--theme` /
  `--no-themes` load flags and `PIPY_THEME` are untouched.

**Testing:**
- The settings dialog exposes a theme row; selecting a theme applies it and
  persists it (mirrors the prior `/theme <name>` apply assertion, now via the
  dialog action).
- `/theme` is gone — dispatching it is an unknown command (no handler, not in
  completions).
- `--theme <name>` startup flag still selects the theme; `NO_COLOR` behavior
  unchanged.

## Slice C — Pi-faithful command set + no-deprecation policy

**Files:** `src/pipy_harness/native/tool_loop_session.py`,
`src/pipy_harness/native/repl_input.py`, `AGENTS.md`, and the parity docs +
`CHANGELOG.md`.

- **Remove `/clear`, `/status`, `/help`** outright: their handlers/alias
  branches in `tool_loop_session.py`, and their entries in the `repl_input.py`
  registries and any reserved-name/menu lists. No deprecation notices. Pi
  equivalents (`/new`, `/session`, `/hotkeys`) are unchanged and remain the
  canonical commands.
- Add a short **`AGENTS.md`** note recording the no-deprecation /
  private-until-parity policy.
- Docs sync: `docs/parity-plan.md` §1 (these rows become "removed", not
  "deprecated alias"; `/theme` removed; `/skill` advertisement now wired) and §3;
  `docs/pi-parity.md`; `docs/settings-config.md` (theme now selectable in
  `/settings`; `/skill` advertisement); `CHANGELOG.md` `[Unreleased]`.

**Testing:**
- `/clear`, `/status`, `/help`, `/theme` are all unknown commands; the completion
  and slash-menu sets equal the Pi-faithful set (+ `/skill`, + dynamic template
  `/<name>`).
- `just docs-build` clean; `just check` green.

## Out of scope

- Realigning `/skill <name>` → Pi's `/skill:name` syntax (cosmetic; `/skill`
  stays as-is — both keep a skill command).
- `disableModelInvocation`-style per-skill suppression (Pi has it; not required
  for the advertisement baseline — note as a possible later refinement).
- Any change to `--read-root`/`--tool-budget`/`--input-runtime`/prompt-history
  (kept internal per the cleanup).

## Testing & verification

- Per slice: unit/CLI tests as listed; `just check` green at each slice end.
- Pi review loop per slice; commit only on CLEAN.
- Slice C also runs `just docs-build`.

## Risks

- **Slice A threading**: the adapter must receive the discovered `SkillFile`s and
  their dirs and fold them into both the system prompt and `reference_roots`.
  Mitigation: follow the existing discovery → `compose_system_prompt` path and
  the existing `reference_roots` plumbing; keep the archive-safe metadata
  boundary intact.
- **Read access widening**: adding skill dirs to `reference_roots` lets the model
  read those dirs (read-only). This is intended (it's how the model loads
  skills, matching Pi) and bounded to discovered skill directories.
- **Slice C edits committed behavior** (the Slice-3 `/clear`/`/status` aliases):
  intentional under the no-deprecation policy; covered by updated tests.
