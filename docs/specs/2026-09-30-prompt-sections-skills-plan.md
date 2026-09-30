# Prompt sections and skills (SYS1c prompt remainder + "Skills and prompt")

Branch `feat/prompt-sections-skills`. Reference: pi-mono `1b347794e`.

## Gap

pipy builds only `preamble`/`addendum`/`project_context`/`skills`/`cwd`
(+ the pipy-only `resume`) system-prompt sections, with a pipy-written
preamble listing tools inline. Pi's `buildSystemPromptSections`
(`packages/coding-agent/src/core/system-prompt.ts`) also builds `tools`,
`rules` and `docs`, and rebuilds the sections before every request from the
active tool names (`agent-session.ts` `_installAgentNextTurnRefresh` ->
`_preparePromptAndToolLoadout`). The "Skills and prompt" backlog follow-on
lists the skill-side gaps: `disable-model-invocation`, the bash-only skill
advertisement, `.md` skill naming, package skill order, and the template /
command store symlink guard. F7b left Pi's skill-invocation block
(`SkillInvocationMessageComponent`).

## What Pi does (pinned)

### Sections (`buildSystemPromptSections`)

Order: `preamble`, then (default prompt only) `tools`, `rules`, `docs`, then
`addendum` (non-empty append), `project_context` (context files),
`skills` (see below), `cwd` (backslashes -> `/`), then custom sections.
Every section but `preamble` is wrapped `<name>\n...\n</name>`.

- `customPrompt` truthy (non-empty) replaces the preamble and omits
  `tools`/`rules`/`docs`. An empty custom prompt is the default prompt.
- Default preamble: `You are an expert coding assistant operating inside pi,
  a coding agent harness. You help users by reading files, executing
  commands, editing code, and writing new files.`
- `tools`: `selectedTools` filtered to names with a snippet, one
  `- name: snippet` line each in `selectedTools` order, or `(none)`; then a
  blank line and `In addition to the tools above, you may have access to
  other custom tools depending on the project.`
- `rules` (`buildRules`): deduped trimmed bullets. If `bash` or `powershell`
  is selected and none of `grep`/`find`/`ls`: `Use bash for file operations
  like ls, rg, find` (PowerShell variants not applicable: pipy has no
  `powershell` tool). Then each selected tool's guidelines in
  `selectedTools` order, then `promptGuidelines`, then `Be concise in your
  responses`, `Show file paths clearly when working with files`.
- `docs`: fixed text naming the README, docs dir, examples dir and a topic
  list (quoted in the driver fixture).
- `skills`: the first of `read`, `bash` in `selectedTools` picks the loader
  tool; no such tool or no visible skill -> no section. `formatSkillsForPrompt`
  drops `disableModelInvocation` skills; the header's second line is `Use the
  read tool to load ...` or `Use bash to load ...`.

Built-in snippets / guidelines (`core/tools/*.ts`
`*ToolSystemPromptContribution`):

| tool | snippet | guidelines |
| --- | --- | --- |
| read | Read file contents | Use read to examine files instead of cat or sed. |
| bash | Execute bash commands (ls, grep, find, etc.) | PI_* env guideline, only with `exposeSessionEnvironment` |
| edit | Make precise file edits with exact text replacement, including multiple disjoint edits in one call | four `edits[]` bullets |
| write | Create or overwrite files | Use write only for new files or complete rewrites. |
| grep | Search file contents for patterns (respects .gitignore) | none |
| find | Find files by glob pattern (respects .gitignore) | none |
| ls | List directory contents | none |

Snippets are normalized to one line (`_normalizePromptSnippet`), guidelines
trimmed and deduped (`_normalizePromptGuidelines`).

### Skills

- `loadSkillFromFile`: `name = frontmatter.name || basename(dirname(filePath))`
  for every file, `SKILL.md` or a plain `.md` (pipy uses the file stem for a
  plain `.md`). `disableModelInvocation = frontmatter["disable-model-invocation"] === true`
  (a YAML boolean; a quoted `"true"` is a string and does not count).
- Order (`resource-loader.ts` `mergePaths([...cliEnabledSkills,
  ...enabledSkills], additionalSkillPaths)`, `package-manager.ts` `resolve`):
  temporary `-e` package skills, then settings packages (project, then
  global), then settings `skills` path entries, then the auto roots (project
  `.pi/skills`, project `.agents/skills` ancestors, user skills, `~/.agents/skills`),
  then `--skill` paths last. `loadSkills` keeps the first skill of a name.
  Prompt templates use the same merge (`additionalPromptTemplatePaths` last,
  `dedupePrompts` first wins). pipy searches `--skill`/`--prompt-template`
  first and packages last.
- `enableSkillCommands` only hides `/skill:name` commands
  (`interactive-mode.ts` `createBaseAutocompleteProvider`); the skills still
  reach the prompt. pipy's `with_enablement(enable_skill_commands=False)` drops
  them from the prompt too.
- Invocation (`agent-session.ts` `_expandSkillCommand`): `/skill:<name>
  [args]` reads the file fresh, strips the frontmatter, trims, and sends
  `<skill name="N" location="PATH">\nReferences are relative to BASEDIR.\n\nBODY\n</skill>`
  plus `\n\nARGS` when args are non-empty. An unknown name passes through.
  Slash commands `skill:<name>` carry the skill description.
- Drawing: a user message matching `parseSkillBlock`
  (`^<skill name="([^"]+)" location="([^"]+)">\n([\s\S]*?)\n</skill>(?:\n\n([\s\S]+))?$`)
  draws a `SkillInvocationMessageComponent` (Box(1,1) on `customMessageBg`;
  collapsed: bold `[skill]` in `customMessageLabel`, space, name in
  `customMessageText`, dim ` (ctrl+o to expand)`; expanded: the label line,
  then Markdown `**name**\n\n` + content) followed, when the trimmed args are
  non-empty, by a normal user message. Live and on resume (`renderSessionItems`).

### Prompt templates / commands

`loadTemplatesFromDir` follows symlinked files (`statSync`) wherever they
point. pipy's flat stores refuse a symlinked store and a file symlink that
escapes it.

## pipy design

1. **`system_prompt_sections.py` owns Pi's prompt text.** Add
   `DEFAULT_PREAMBLE` (Pi's wording with `pipy` for `pi`),
   `BUILTIN_TOOL_PROMPTS` (the table above; bash without a guideline), the
   `build_rules`, `tools_section` and `docs_section` builders, and a frozen
   `SystemPromptTemplate` holding the tool-independent inputs (custom
   preamble or `None`, addendum, project-context body, skills, cwd, resume,
   snippet and guideline maps, docs paths). `template.sections(selected_tools)`
   is Pi's `buildSystemPromptSections`. `docs_section` takes the product name,
   paths and topic list, so the test can feed Pi's values and compare with
   Pi's output; pipy passes its own:
   `pipy` / `README.md` / `docs` / `docs/examples`, topics mapped to pipy
   docs (extensions -> `docs/extension-api.md, examples/extensions/`;
   themes, skills, prompt templates -> `docs/customization.md`; TUI ->
   `docs/tui-workflow.md`; keybindings, SDK, packages -> same names;
   custom providers -> `docs/providers.md`; adding models ->
   `docs/provider-catalog.md`; no environment-variable or MCP doc, so those
   topics are omitted). Paths resolve by walking up from the module to the
   first directory with `README.md` and `docs/` (as `default_changelog_path`
   does); the wheel does not ship the docs yet (Publishing backlog item).
   `NATIVE_TOOL_LOOP_SYSTEM_PROMPT` leaves `session.py`; its audit ceiling
   drops to the new line count (lowered, never raised).
2. **Per-run rebuild.** `CodingSessionAdapter.prepare_session_context` builds
   the template and renders the initial sections for the provider-visible
   built-ins (today's `read_tool_visible` gate generalized). `CodingSession`
   keeps the one `system_prompt_sections` field, typed
   `SystemPromptSections | SystemPromptTemplate` (no new field, so the
   member audit pins hold). Wiring exposes `system_sections()` on the loop
   scope: the template rendered for the live active tool names
   (`NativeToolCapabilities.definitions()` order), or the fixed sections
   when a caller passed only a prompt string. Phase E renders the run's base
   prompt from it (`before_agent_start` sees that text), phase F2 diffs those
   sections against the transcript, and `_request_values` compares against
   the run's base prompt, not the session-start one. So an extension
   `set_active_tools` or a reload changes `tools`/`rules`/`skills` in the next
   run's system message, as Pi's refresh does. Mid-run refresh (Pi refreshes
   per turn inside a run) is a documented follow-on.
3. **Skills.** `_RawResourceFile`/`SkillFile` gain
   `disable_model_invocation` and `base_dir`; the frontmatter parser reports
   an unquoted `true`/`True`/`TRUE` for `disable-model-invocation`. A plain
   `.md` skill without a `name` is named after its directory. The shared
   source order becomes: packages, workspace, (`.agents` ancestors), global,
   (`~/.agents`), then CLI paths, for skills and prompt templates. The prompt
   advertisement takes all discovered skills after the `skills` pattern
   filter, ignoring `enableSkillCommands`, and drops
   `disable-model-invocation` ones; `skills_section_body(skills, loader)`
   writes the read or bash header.
4. **Skill invocation.** `/skill:<name> [args]` replaces pipy's
   `/skill <name>` (and its bare `/skill` listing, a pipy-only surface removed
   per the no-deprecation policy): the file is read fresh, the provider text
   is Pi's `<skill ...>` block (+ args), and the slash menu lists
   `/skill:<name>` with the description when `enableSkillCommands` is on.
   Off only hides the menu entries: a typed `/skill:<name>` still expands, as
   Pi's `_expandSkillCommand` does (pipy's dispatch gate goes). An
   unknown or unreadable skill keeps pipy's rejection notice (the unhandled
   `/...` deviation stays tracked in F7b). `parse_skill_block` (Pi regex) and
   a `SkillInvocationBox` row (collapsed/expanded like Pi, redrawn on Ctrl+O
   and resize like `SummaryBox`) draw the block in the TUI, live and when a
   session is resumed; the args follow as a normal user message.
5. **Symlink guard.** Remove the flat-store containment guard: template and
   command files follow symlinks like Pi's `loadTemplatesFromDir`.

## Kept / deferred (backlog)

- Pi's bash guideline needs `PI_*` session environment variables that pipy's
  bash tool does not set; pipy is Pi with `exposeSessionEnvironment: false`.
- Extension tool `promptSnippet`/`promptGuidelines` and
  `before_agent_start` `systemPromptOptions`; extension tools therefore do not
  appear in `tools` (as a Pi extension tool without a snippet).
- Discovery screens: skills and prompt templates still skip secret-shaped,
  generated and binary files and stop at the 64 KiB per-file / 256 KiB total
  caps. Pi's loaders have none of these; removing them touches the shared
  `_resource_files` policy of every store and is its own slice (no documented
  boundary requires them; recorded in docs/backlog.md).
- Per-turn (mid-run) section refresh; Pi settings `skills`/`prompts` path
  entries; shipping the docs in the wheel; skill descriptions are still cut
  at pipy's 256-character label limit and `<location>` is the resolved path.

## Done when

- `tests/test_native_system_prompt_sections.py` pins every case of
  `scripts/parity_checks/pi_system_prompt_driver.mts` (fixture
  `tests/fixtures/pi_system_prompt_sections.json`, regenerated from Pi with
  `scripts/parity_checks/pi_system_prompt_fixture.py`), including Pi's own
  preamble and docs text when fed Pi's name/paths/topics, plus a live
  node comparison when pi-mono is available.
- Skill tests: `disable-model-invocation`, plain `.md` naming, package/CLI
  order, bash advertisement, enableSkillCommands vs prompt, `/skill:name`
  block text, `parse_skill_block`, the box rows.
- `automation_pi_comparison.py` also compares the section names with Pi's
  (`preamble, tools, rules, docs, cwd`).
- tmux PTY evidence of `/skill:name args` collapsed and expanded next to Pi.
- One live openai-codex `gpt-6.1-sol` turn with isolated config/sessions.
- `just check`, `scripts/parity_checks/*`, `scripts/parity_score.sh`,
  `just catalog-drift` green; docs, CHANGELOG, backlog updated.

## Implementation order

1. `system_prompt_sections.py`: Pi texts, builders, `SystemPromptTemplate`;
   fixture + pin tests from the Pi driver. Accept: every driver case equal.
2. `skills.py`/`_resource_files.py`: `disable_model_invocation`, directory
   naming, package-first/CLI-last order, flat stores follow symlinks, bash
   header. Accept: focused discovery tests.
3. `resources.py`, `repl_input.py`, `command_menu.py`, the controller notice:
   `/skill:<name> [args]`, Pi block text, menu entries, bare `/skill` gone.
   Accept: dispatch and menu tests.
4. Adapter + `CodingSession` + wiring + `loop_step`: template per run.
   Accept: a `set_active_tools` change patches `tools`/`rules` in the next
   run's system message; audit pins green.
5. TUI: `parse_skill_block`, `SkillInvocationBox`, `render_user_message`.
   Accept: row tests collapsed/expanded, resume replay.
6. `automation_pi_comparison.py` section names; docs, CHANGELOG, backlog.
