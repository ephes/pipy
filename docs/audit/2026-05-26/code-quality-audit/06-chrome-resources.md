# Audit: Chrome + Workspace + Resources

Scope:
- `src/pipy_harness/native/chrome.py` (448L)
- `src/pipy_harness/native/workspace_context.py` (434L)
- `src/pipy_harness/native/_resource_files.py` (378L)
- `src/pipy_harness/native/skills.py` (158L)
- `src/pipy_harness/native/prompt_templates.py` (144L)
- `src/pipy_harness/native/custom_commands.py` (195L)
- `src/pipy_harness/native/themes.py` (167L)
- `src/pipy_harness/native/image_attachment.py` (196L)

Comparison:
- `packages/coding-agent/src/core/resource-loader.ts` (927L)
- `packages/coding-agent/src/core/skills.ts` (487L)
- `packages/coding-agent/src/core/prompt-templates.ts` (285L)
- `packages/coding-agent/src/core/slash-commands.ts` (40L)
- `packages/coding-agent/src/core/extensions/loader.ts`
- `packages/coding-agent/src/modes/interactive/theme/theme.ts` (1,227L)
- `packages/coding-agent/src/modes/interactive/interactive-mode.ts` (chrome banner)

## Summary

The chrome + resource-discovery layer has the strongest AI-slop signature of the
modules audited so far. Roughly 860 lines of `skills.py`, `prompt_templates.py`,
`custom_commands.py`, `themes.py`, and `image_attachment.py` are dormant feature
scaffolds: they ship full discovery loaders, value objects, frontmatter parsers,
compose helpers, `safe_*_metadata` projectors, ~1,200 lines of pinning tests,
and zero production callers in `src/`. The chrome banner is the only thing
roughly half of this code touches, and the chrome banner uses an entirely
different path map than the resource loaders (`~/.pipy/skills` vs the loader's
`~/.config/pipy/skills`; `.pipy/commands` vs `.pipy/templates`; `.pipy/plugins`
for a plugin loader that does not exist), so the banner enumerates resources
nobody reads and the loaders read resources the banner does not display. Two
copies of the AGENTS.md candidate list disagree on filenames (`pipy.md/PIPY.md`
vs documented `CLAUDE.md/CLAUDE.MD`), the dataclass that carries the bottom
status has a dead `cwd_label` field that every caller passes as `""`, the
banner advertises a `ctrl+o` expansion that has no key binding, and
`_resource_files.discover_resource_files` has three different byte-cap checks
around the same value with one branch that is structurally unreachable. The
workspace_context module itself is the single piece of this layer that does
something real (it composes AGENTS.md into the system prompt and is wired into
`session.py`, `cli.py`, `sdk.py`); even that has stale module docstrings
referring to `CLAUDE.md` precedence the code no longer implements.

## Findings

### F1: Three resource-discovery modules with no production callers

- **Where**: `src/pipy_harness/native/skills.py:76`,
  `src/pipy_harness/native/prompt_templates.py:69`,
  `src/pipy_harness/native/custom_commands.py:80`, plus the corresponding
  `compose_*_block` and `find_*_by_name` helpers.
- **Symptom**: `discover_workspace_skills`, `discover_workspace_prompt_templates`,
  `discover_workspace_custom_commands`, `compose_skills_system_block`,
  `compose_custom_commands_help_block`, `find_template_by_name`, and
  `find_custom_command_by_name` are imported only by `tests/test_native_skills.py`,
  `tests/test_native_prompt_templates.py`, and `tests/test_native_custom_commands.py`.
  `grep -rn 'from pipy_harness.native.\(skills\|prompt_templates\|custom_commands\) ' src/`
  returns zero hits. The slash-command dispatcher in `session.py:290-301` defines
  `/help /clear /status /settings /login /logout /model /read /ask-file
  /propose-file /apply-proposal /verify` — no `/skill`, `/template`, or `/command`.
  ~497 lines of discovery code and ~1,118 lines of pinning tests build a system
  no live REPL path ever exercises.
- **Article principle**: 5 (AI slop aesthetics — registries with one entry, dead
  branches); 6 (Volume = noise).
- **Pi comparison**: Pi's `resource-loader.ts` exposes one `DefaultResourceLoader`
  that the interactive mode actually instantiates; skills/prompts/extensions are
  all loaded through it and dispatched as `SlashCommandSource = "extension" |
  "prompt" | "skill"` via `slash-commands.ts`. The loader exists because it has
  callers.
- **Suggested fix**: Either wire `/skill`, `/template`, `/command` into
  `session.py` (composing `compose_skills_system_block` into the system prompt,
  routing `/template <name>` and `/<custom-name>` through the existing dispatch
  table), or delete the three modules and their tests. The current state — fully
  tested, fully unused — is the textbook AI-slop scaffold.
- **Severity**: high

### F2: `image_attachment.py` is a dead boundary

- **Where**: `src/pipy_harness/native/image_attachment.py:1-196` and
  `src/pipy_harness/native/models.py:150`.
- **Symptom**: `ImageAttachment`, `load_image_attachment`, and
  `read_image_attachment_bytes` are only imported by
  `tests/test_native_image_attachment.py`. `ProviderRequest.image_attachments`
  is a 5th tuple field on the provider boundary that no provider reads (`grep -rn
  '\.image_attachments' src/` returns only the field declaration). The module
  docstring at line 22 admits: "Live wiring to a real vision provider is
  deferred to a later track; this module ships the value object, the bounded
  loader, and the `ProviderRequest.image_attachments` plumbing so the boundary
  exists before a real provider needs it." This is "make the plumbing first,
  add the feature later" — exactly the pattern Armin flags as slop.
- **Article principle**: 5 (registries with one entry); 6 (Volume = noise).
- **Pi comparison**: Pi's `ImageContent` shape is defined where vision providers
  actually consume it (`packages/ai/src/types.ts`).
- **Suggested fix**: Delete `image_attachment.py`, the test file, and the
  `image_attachments` field on `ProviderRequest`. When a provider that needs
  vision lands, add the type next to that provider. The boundary is not load-bearing.
- **Severity**: medium

### F3: Chrome banner advertises `[Extensions]` for a non-existent plugin loader

- **Where**: `src/pipy_harness/native/chrome.py:35-46, 251`.
- **Symptom**: `_STARTUP_CHROME_RESOURCE_SOURCES["extensions"] = (".pipy/plugins",)`
  and `_STARTUP_CHROME_GLOBAL_RESOURCE_SOURCES["extensions"] = ("~/.pipy/plugins",)`.
  Nothing in `src/pipy_harness/` loads anything from `.pipy/plugins`:
  `grep -rn 'plugin' src/pipy_harness/` returns only these four lines from
  `chrome.py`. The banner will render an `[Extensions]` block populated from a
  directory whose contents are never read.
- **Article principle**: 3 (Plausible-but-wrong); 5 (discovery patterns with
  multiple fallback paths that are never tested).
- **Pi comparison**: Pi has a real `core/extensions/loader.ts` with
  `loadExtensions`, `createExtensionRuntime`, manifest parsing, etc. The banner
  shows the loaded extensions because there is a loader.
- **Suggested fix**: Drop the `extensions` key from both
  `_STARTUP_CHROME_RESOURCE_SOURCES` constants and the loop at line 247-252. Add
  it back when a plugin loader exists.
- **Severity**: high

### F4: Chrome banner enumerates `.pipy/commands` under "Prompts" but the prompt loader uses `.pipy/templates`

- **Where**: `src/pipy_harness/native/chrome.py:38, 44, 250`;
  `src/pipy_harness/native/prompt_templates.py:40-41`;
  `src/pipy_harness/native/custom_commands.py:45-46`.
- **Symptom**: The chrome map says `"prompts": (".pipy/commands",)` and
  `~/.pipy/commands`. But `PROMPT_TEMPLATES_WORKSPACE_SUBDIR = "templates"` and
  `CUSTOM_COMMANDS_WORKSPACE_SUBDIR = "commands"`. So the banner column called
  `[Prompts]` lists the same directory the `custom_commands` loader would use,
  and the actual `prompt_templates` directory `.pipy/templates` never shows up
  in the banner at all. Two of the three resource modules disagree with the
  banner about which directory is which.
- **Article principle**: 3 (Plausible-but-wrong); 5 (AI slop aesthetics).
- **Pi comparison**: Pi's `resource-loader.ts` is the single source of truth: it
  owns both the directory paths and the banner content, so this disagreement
  cannot occur.
- **Suggested fix**: When the dispatch wiring (F1) lands, derive the banner from
  the same constants the loaders use (`PROMPT_TEMPLATES_WORKSPACE_SUBDIR`,
  `CUSTOM_COMMANDS_WORKSPACE_SUBDIR`, `SKILLS_WORKSPACE_SUBDIR`) instead of
  re-listing them in `chrome.py`. Until then, delete the `prompts` row so the
  banner does not lie.
- **Severity**: high

### F5: Chrome global root is `~/.pipy/...`; the actual resource loader's global root is `~/.config/pipy/...`

- **Where**: `src/pipy_harness/native/chrome.py:41-46, 361-364`;
  `src/pipy_harness/native/_resource_files.py:69-91`;
  `src/pipy_harness/native/workspace_context.py:128-150`.
- **Symptom**: `_STARTUP_CHROME_GLOBAL_RESOURCE_SOURCES` hardcodes
  `~/.pipy/skills`, `~/.pipy/commands`, `~/.pipy/plugins`,
  `~/.pipy/AGENTS.md`, `~/AGENTS.md` — directly under `$HOME`. The actual
  resource loaders use `resolve_global_resource_root` which resolves
  `PIPY_CONFIG_HOME` → `${XDG_CONFIG_HOME}/pipy` → `~/.config/pipy`. So the
  banner enumerates a directory tree that is not where pipy actually loads from.
  A user who follows the discovery rules and creates `~/.config/pipy/skills/foo.md`
  gets it loaded but not displayed; a user who follows the banner and creates
  `~/.pipy/skills/foo.md` gets it displayed but not loaded.
- **Article principle**: 1 (Make bad states impossible); 3 (Plausible-but-wrong);
  5 (multiple fallback paths that are never tested in concert).
- **Pi comparison**: Pi has one `getAgentDir()` and one set of canonical
  directory constants; everything routes through them.
- **Suggested fix**: Call `resolve_global_resource_root(...) / "skills"` etc.
  from the banner instead of hardcoded `~/.pipy/...` strings. Pin a
  cross-module test that the banner enumerates whatever the loader loads.
- **Severity**: high

### F6: Chrome listing globs sub-directories; the actual skill loader globs `*.md` files

- **Where**: `src/pipy_harness/native/chrome.py:403-421`;
  `src/pipy_harness/native/_resource_files.py:252-277`.
- **Symptom**: `chrome._names_for_candidate` for `skills`/`prompts`/`extensions`
  iterates `path.iterdir()` and filters `child.is_dir() and not
  child.name.startswith(".")`. The real loader (`_iter_md_files`) globs
  `*.md` regular files directly under the directory. A workspace following the
  loader's convention (`.pipy/skills/foo.md`, `.pipy/skills/bar.md`) has no
  child directories and the banner renders an empty `[Skills]` (so the section
  is hidden by `if not label: continue`). The banner was written assuming Pi's
  one-directory-per-skill layout (`skills/foo/SKILL.md`); the loader was
  written for a flat-file layout. Neither knows about the other.
- **Article principle**: 3 (Plausible-but-wrong implementation); 5 (registries
  whose entries are never reconciled).
- **Pi comparison**: Pi's `skills.ts` is recursive and uses `SKILL.md` naming;
  the banner agrees with the loader because both go through
  `resource-loader.ts`.
- **Suggested fix**: Reuse `discover_resource_files` from the banner so the
  rendered list matches the loaded list. The "skill = directory" vs "skill =
  file" choice is now ambiguous; pick one and pin a test.
- **Severity**: high

### F7: Two divergent context-file candidate lists

- **Where**: `src/pipy_harness/native/workspace_context.py:61-66`;
  `src/pipy_harness/native/chrome.py:36, 367-400`.
- **Symptom**: `INSTRUCTION_CANDIDATE_FILENAMES = ("AGENTS.md", "AGENTS.MD",
  "pipy.md", "PIPY.md")` is the workspace_context source of truth. The chrome
  map says `"context": ("AGENTS.md", "pipy.md", ".pipy/AGENTS.md")` — drops
  `AGENTS.MD` (uppercase MD) and `PIPY.md`, adds `.pipy/AGENTS.md` which the
  loader does not look for. The ancestor walker at chrome.py:367-400 then
  reaches back to `INSTRUCTION_CANDIDATE_FILENAMES` again, so the banner uses
  one list for cwd and a different list for ancestors. A workspace with
  `AGENTS.MD` is loaded by the discovery and invisible in the banner; a
  workspace with `.pipy/AGENTS.md` is shown in the banner and never loaded.
- **Article principle**: 1 (Make bad states impossible); 3 (Plausible-but-wrong).
- **Pi comparison**: Pi's `loadContextFileFromDir` (resource-loader.ts:57)
  defines one candidates list `["AGENTS.md", "AGENTS.MD", "CLAUDE.md",
  "CLAUDE.MD"]` and reuses it everywhere.
- **Suggested fix**: Have `chrome.discover_loaded_resource_names("context", ...)`
  call `discover_workspace_instructions(...)` and project the resulting
  `instructions[*].path_label`. One traversal, one truth, deterministic agreement.
- **Severity**: high

### F8: Stale module docstring in `workspace_context.py` describes `CLAUDE.md` precedence the code dropped

- **Where**: `src/pipy_harness/native/workspace_context.py:12-15`.
- **Symptom**: The docstring says "Per-directory candidate precedence (highest
  first): `AGENTS.md > AGENTS.MD > CLAUDE.md > CLAUDE.MD`." The actual constant
  at line 61-66 is `("AGENTS.md", "AGENTS.MD", "pipy.md", "PIPY.md")`. No
  `CLAUDE.md`. The "do not conflate pipy with neighbor tools" comment in
  `chrome.py:30-34` is the right policy; the docstring just was never updated
  when the policy was adopted.
- **Article principle**: documentation parity (CLAUDE.md rule "Implementation
  and review work is not complete until documentation matches the change").
- **Pi comparison**: N/A — this is a doc-vs-code drift issue.
- **Suggested fix**: Update the docstring to read `AGENTS.md > AGENTS.MD >
  pipy.md > PIPY.md`.
- **Severity**: low

### F9: `chrome.print_startup_chrome` docstring advertises `~/.claude` and `~/.codex` sources the function explicitly excludes

- **Where**: `src/pipy_harness/native/chrome.py:193-201` vs
  `src/pipy_harness/native/chrome.py:30-34`.
- **Symptom**: The function docstring at lines 197-200 says listings come from
  "global user-home sources (``~/.claude``, ``~/.codex``, ``~/.pipy``)." The
  comment at lines 30-34 explains the deliberate decision NOT to surface
  `~/.claude/CLAUDE.md` or `~/.codex/...`. The docstring is left over from an
  earlier design.
- **Article principle**: doc-vs-code drift.
- **Pi comparison**: N/A.
- **Suggested fix**: Drop `~/.claude` and `~/.codex` from the docstring; mention
  that pipy intentionally does not surface neighbor-tool configs.
- **Severity**: low

### F10: `BottomStatusFields.cwd_label` is dead

- **Where**: `src/pipy_harness/native/chrome.py:122-148` (declaration);
  `src/pipy_harness/native/chrome.py:151-176` (`format_bottom_status_line`);
  `src/pipy_harness/native/session.py:1741`,
  `src/pipy_harness/native/tool_loop_session.py:791` (callers).
- **Symptom**: The `cwd_label` field is declared on the dataclass and is the
  first kwarg in every caller and every test, but `format_bottom_status_line`
  never reads it. Every caller passes `cwd_label=""`. The actual cwd rendering
  is done separately by `print_bottom_status_block(cwd_label=..., status_line=...)`
  (lines 286-294), which takes its own `cwd_label` kwarg.
- **Article principle**: 1 (Make bad states impossible — two fields named
  `cwd_label` carrying different semantics is a trap); 6 (Volume = noise).
- **Pi comparison**: N/A — Pi keeps the cwd row and the status row as two
  distinct strings without a vestigial duplicate field.
- **Suggested fix**: Drop `cwd_label` from `BottomStatusFields`. Callers already
  pass `cwd_label=""` so the change is mechanical.
- **Severity**: medium

### F11: `ctrl+o` hint in startup banner is unwired

- **Where**: `src/pipy_harness/native/chrome.py:223, 232`.
- **Symptom**: The controls strip prints `"ctrl+o more"` and the next line says
  "Press ctrl+o to show full startup help and loaded resources." `grep -rn
  'ctrl+o\|\\x0f\|ControlO\|control.o' src/pipy_harness/native/repl_input.py`
  returns zero hits. The repl input has no `ctrl+o` keybinding; only the
  tool-loop session prints a `(ctrl+o to expand)` hint next to truncated
  observations (`tool_loop_session.py:1293, 1496`), and even that is just a
  hint, not a bound key. So the banner advertises an expandable startup help
  that does not exist.
- **Article principle**: 3 (Plausible-but-wrong UI).
- **Pi comparison**: Pi binds `app.tools.expand` and renders a real
  `ExpandableText` widget in `interactive-mode.ts:642`. The hint is real
  because the feature is real.
- **Suggested fix**: Either implement the expansion (bind ctrl+o to print a
  longer help block + full resource listing) or drop the "ctrl+o more" /
  "Press ctrl+o to show full startup help" lines from the banner.
- **Severity**: medium

### F12: `discover_resource_files` has a dead duplicate byte-cap check

- **Where**: `src/pipy_harness/native/_resource_files.py:185-200`.
- **Symptom**:
  ```python
  if total_loaded + byte_length > total_byte_cap and raw_files:
      cap_reached = True
      break
  if total_loaded + byte_length > total_byte_cap:
      cap_reached = True
      break
  ```
  The first `if` is structurally unreachable: if the second `if` is reached, the
  first did not trigger, which means either the condition was false (so the
  second is also false) or `raw_files` was empty (so the second's break still
  fires unconditionally). Either way, the first block does nothing the second
  doesn't. Then lines 198-200 do a *third* identical check after `_read_capped_bytes`
  in case `byte_length` changed mid-read — but `_read_capped_bytes` shadows
  `byte_length` so the third check is comparing against possibly-stale data
  vs the first/second checks. The pre-read `stat().st_size` (line 182) and
  post-read `_read_capped_bytes` return value (line 192-195) both bind to the
  same name; the intent is unclear.
- **Article principle**: 2 (AI over-engineers edge-case handling); 5 (dead
  branches); 6 (Volume = noise).
- **Pi comparison**: Pi's `resource-loader.ts` does not do byte-cap accounting
  at this layer; loaders read whole files and Pi caps at the prompt level.
- **Suggested fix**: Collapse to one cap check after `_read_capped_bytes`
  returns the actually-streamed `byte_length`. Delete the pre-read stat-based
  optimisation, or keep one (not three) of them.
- **Severity**: medium

### F13: `_load_first_candidate` swallows OSError at five separate sites

- **Where**: `src/pipy_harness/native/workspace_context.py:252-285` (5 distinct
  `except OSError` clauses);
  `src/pipy_harness/native/_resource_files.py:168, 173, 177, 183, 196, 263, 267,
  274, 297` (9 distinct clauses).
- **Symptom**: Every individual filesystem call (`is_dir`, `resolve`, `is_file`,
  `stat`, `iterdir`, `open` via `_read_capped_bytes`) is wrapped in its own
  `except OSError` that silently treats the candidate as "not present." There
  is no logging, no diagnostic, no surfacing of "we tried to read an AGENTS.md
  in `/some/parent/` and you don't have permission." A user with a
  permission-denied AGENTS.md will see no banner entry, get no warning, and
  wonder why their workspace context did not load. Pi-mono explicitly prints
  `chalk.yellow('Warning: Could not read ${filePath}: ${error}')` (resource-loader.ts:48).
- **Article principle**: 4 (Permissive error handling is a smell); 1 (Make bad
  states impossible — instead, this swallows them).
- **Pi comparison**: Pi prints a warning on read failure; pipy is silent.
- **Suggested fix**: For most of these, the user-correctness fix is "log once at
  startup if a candidate file exists but cannot be read." A `ResourceDiagnostic`
  channel (which pi-mono has) is the cleaner architecture.
- **Severity**: medium

### F14: Workspace cap is checked AFTER every per-file `_read_capped_bytes` streams the whole file

- **Where**: `src/pipy_harness/native/workspace_context.py:196-225`;
  `src/pipy_harness/native/workspace_context.py:305-323`.
- **Symptom**: The function walks all ancestor directories and calls
  `_load_first_candidate` for each, which calls `_read_capped_bytes`, which
  streams the WHOLE file (hashing chunks of 1 MiB) regardless of whether the
  total cap will allow it. Then lines 216-226 enforce the total cap by
  discarding files that overflow. A workspace with a 1 GB AGENTS.md in an
  ancestor (a pathological case but real on dotfile checkouts) streams the
  whole 1 GB into the sha256 buffer even though the loader will throw the
  content away. The byte cap is enforced as a filter, not as a stop signal.
- **Article principle**: 2 (over-engineering — caps are advertised but not
  honored at the I/O boundary).
- **Pi comparison**: Pi reads files via `readFileSync(...)` once and does not
  hash them.
- **Suggested fix**: Pass the running total into `_read_capped_bytes` and let
  it return early once `total_loaded + per_file_byte_cap` would already exceed
  the total cap. Or stop hashing past the cap entirely (the hash is only used
  for change detection between runs; truncating it is fine).
- **Severity**: low

### F15: `_resource_files._path_label_for` resolves through symlinks and can lose the workspace prefix

- **Where**: `src/pipy_harness/native/_resource_files.py:280-298`.
- **Symptom**: For workspace-source files the label is computed as
  `candidate.resolve().relative_to(workspace)`. If a workspace skill
  `.pipy/skills/foo.md` is a symlink to `.skills-vendored/foo.md` (still inside
  the workspace), the rendered `path_label` becomes `.skills-vendored/foo.md`
  instead of `.pipy/skills/foo.md`. The audit trail says the file came from a
  directory the user never used as the discovery source. Containment is
  enforced (good) but the user-facing label hides the entry point.
- **Article principle**: 3 (Plausible-but-wrong).
- **Pi comparison**: N/A — Pi keeps the symlink path.
- **Suggested fix**: Label with `candidate.relative_to(workspace).as_posix()`
  (no `.resolve()`) so the user sees the path they actually have in their tree.
  Keep `resolve()` for the seen-set dedup and containment check.
- **Severity**: low

### F16: `_load_first_candidate` returns `None` (whole directory) when ONE candidate is already seen, instead of falling through

- **Where**: `src/pipy_harness/native/workspace_context.py:277-278`.
- **Symptom**: `if resolved_candidate in seen_paths: return None`. If the
  workspace's `AGENTS.md` is a symlink to `~/.config/pipy/AGENTS.md`, the
  global candidate is loaded first and added to `seen_paths`. When the walker
  reaches the workspace, `resolve()` produces the same path, and the function
  returns `None` for the whole directory — even though the workspace might
  also contain a `pipy.md` that is distinct and would load fine. The docstring
  at lines 30-34 explicitly says the symlink-escape case falls through to the
  next candidate name; the symlink-to-same-canonical-path case does NOT, and
  the asymmetry is undocumented. A user who runs `ln -s ~/.config/pipy/AGENTS.md
  AGENTS.md` and also has a `pipy.md` would expect the latter to load and it
  silently won't.
- **Article principle**: 3 (Plausible-but-wrong); 1 (Make bad states impossible
  — symmetric handling would).
- **Pi comparison**: Pi just dedups by absolute path in a `seen_paths` set at
  the top-level loop; same-canonical-path workspaces still get tried for the
  next candidate name.
- **Suggested fix**: Replace `return None` with `continue` so the loop tries
  the next filename in the same directory.
- **Severity**: low

### F17: `print_startup_chrome` ends with `if not rendered_section: return`, which is dead

- **Where**: `src/pipy_harness/native/chrome.py:269-271`.
- **Symptom**: The last statement of `print_startup_chrome` is
  ```python
  if not rendered_section:
      # Keep one blank line after the chrome controls for spacing parity.
      return
  ```
  Falling off the end of a function returns `None` anyway; the `return` is a
  no-op. The comment "Keep one blank line after the chrome controls for
  spacing parity" describes a behavior the code does not perform (no extra
  blank line is printed in the `not rendered_section` case — the explicit
  blank line is at lines 244-245, before the section loop).
- **Article principle**: 5 (dead branches); 6 (Volume = noise).
- **Pi comparison**: N/A.
- **Suggested fix**: Delete the dead conditional.
- **Severity**: low

### F18: Two consecutive `print(file=error_stream)` after the onboarding line emit a double blank

- **Where**: `src/pipy_harness/native/chrome.py:244-245`.
- **Symptom**: Two unconditional `print(file=error_stream)` lines emit two
  blank rows between the onboarding text and the resource sections. The
  startup banner therefore has a 2-row gap before `[Context]` regardless of
  terminal height. Pi emits one row.
- **Article principle**: minor visual drift; 3 (plausible-but-wrong).
- **Pi comparison**: Pi's `interactive-mode.ts` joins compact + onboarding with
  a single `\n\n` and the section block follows on the next render pass.
- **Suggested fix**: Drop one of the two prints.
- **Severity**: low

### F19: `_short_token_count` always emits a `.0` digit for round-thousands and never paginates past `1k`

- **Where**: `src/pipy_harness/native/chrome.py:187-190`.
- **Symptom**: `f"{value/1000:.1f}k"` renders 1000 → `1.0k`, 1500 → `1.5k`,
  100000 → `100.0k`. The trailing `.0` is noise on round counts. There's no
  `M` cutoff so 5,000,000 reasoning tokens render as `5000.0k` (8 chars where
  `5M` would do). Pi shows token counts more compactly.
- **Article principle**: 6 (Volume = noise on the status line where space is
  precious).
- **Pi comparison**: Pi's `footer-data-provider.ts` formats token counts with
  `k`/`M` separation and drops trailing zeros.
- **Suggested fix**: Suppress `.0` and add a `M`/`G` tier above 1M.
- **Severity**: low

### F20: `BottomStatusFields.context_budget_suffix` is overloaded with two distinct facts

- **Where**: `src/pipy_harness/native/session.py:1746`;
  `src/pipy_harness/native/tool_loop_session.py:796`;
  `src/pipy_harness/native/chrome.py:122-148, 168-176`.
- **Symptom**: The no-tool REPL passes
  `context_budget_suffix=f"bytes · turns {state.provider_turn_count}/{state.max_turns}"`
  and the tool-loop REPL passes `context_budget_suffix="auto"`. The chrome
  formatter wraps the suffix as `({suffix})`. So the no-tool footer reads
  `0.0%/4k (bytes · turns 0/10)` — a single parenthesized blob carrying both
  the budget unit ("bytes") and a separate counter ("turns 0/10"). Business
  logic is being encoded into a free-form string field. A future "session
  hash" or "remaining context" indicator would have nowhere to go without
  either inventing a new field or shoving another `·`-delimited token into
  the same suffix.
- **Article principle**: 1 (Make bad states impossible — strong types beat
  free-form suffix strings); 5 (slop aesthetic — string concatenation in
  lieu of data modeling).
- **Pi comparison**: Pi's footer uses distinct fields per concern (budget
  unit, turn counter, branch, attention) rather than one omnibus suffix.
- **Suggested fix**: Split into typed fields: `context_budget_unit: str`
  ("bytes" / "auto"), `extra_tags: tuple[str, ...]` for the `·`-joined extras
  (turns, etc.). The formatter joins them; callers stop concatenating.
- **Severity**: low

### F21: Resource-loader and instruction-loader duplicate ~80 lines of identical helpers

- **Where**: `src/pipy_harness/native/workspace_context.py:128-150` vs
  `src/pipy_harness/native/_resource_files.py:69-91` (`resolve_*_root`);
  `src/pipy_harness/native/workspace_context.py:305-323` vs
  `src/pipy_harness/native/_resource_files.py:231-249` (`_read_capped_bytes`);
  `src/pipy_harness/native/workspace_context.py:68-79` vs
  `src/pipy_harness/native/_resource_files.py:34-47` (env/cap constants).
- **Symptom**: `PIPY_CONFIG_HOME_ENV`, `XDG_CONFIG_HOME_ENV`,
  `PIPY_CONFIG_DIR_NAME`, `GLOBAL_PATH_LABEL_PREFIX`,
  `DEFAULT_PER_FILE_BYTE_CAP`, `DEFAULT_TOTAL_BYTE_CAP`, `_HASH_CHUNK_SIZE`,
  `_read_capped_bytes(...)`, and `resolve_global_*_root(...)` exist as two
  near-identical copies. The PER_FILE_TRUNCATION_MARKER_TEMPLATE has a
  workspace-instruction-specific message in one file and a
  resource-file-specific message in the other (the wording diverges but the
  semantics are identical). `_resource_files.py` exists explicitly to share
  helpers, then duplicates them anyway.
- **Article principle**: 5 (slop aesthetic — share-as-copy-paste); 6 (Volume).
- **Pi comparison**: Pi has one `loadProjectContextFiles` + one `loadSkills` +
  one `loadPromptTemplates` driven by the same `DefaultResourceLoader` and
  shared utility helpers in `utils/paths.ts` and `utils/frontmatter.ts`.
- **Suggested fix**: Extract `resolve_global_root(subdir: str)`,
  `read_capped_bytes(...)`, and the env-name constants into a single shared
  helper module both `workspace_context.py` and `_resource_files.py` import.
  The truncation marker strings can stay distinct or merge — the format is
  the same.
- **Severity**: low

### F22: `themes.py` is unused production code (test-only)

- **Where**: `src/pipy_harness/native/themes.py:1-167`.
- **Symptom**: `themes.py` defines `Theme`, `ThemeColors`, `BUILTIN_THEMES`,
  `resolve_theme`, `style`. `grep -rn 'pipy_harness.native.themes\|resolve_theme\|BUILTIN_THEMES' src/`
  returns only the module's own self-references. All actual ANSI styling in
  the REPL comes from `chrome.py`'s `ChromeStyle` (a parallel and entirely
  independent palette: `_PI_TITLE_TRUECOLOR`, `_PI_SECTION_TRUECOLOR`, etc.).
  The "pure-data theme registry" exists, has tests, and is invoked nowhere.
- **Article principle**: 5 (registries with one entry — three themes registered,
  zero consumers); 6 (Volume = noise).
- **Pi comparison**: Pi's `theme.ts` is 1,227 lines because it does JSON
  schema validation, color-space conversion, user theme paths, and is the
  actual styling engine for the interactive mode. Pipy's themes.py was
  presumably an attempt at a simpler equivalent that never landed.
- **Suggested fix**: Either replace the ad-hoc ANSI palette in `chrome.py:48-101`
  with the themes-module `Theme` (so themes have a consumer), or delete
  `themes.py` and its test. The "right thing" (a small pure registry) is fine
  in principle, but it has to be wired up.
- **Severity**: medium

### F23: Module docstring of `workspace_context.py` calls itself a "slopfork"

- **Where**: `src/pipy_harness/native/workspace_context.py:3-8`.
- **Symptom**: The module docstring says "This module is a pure,
  dependency-free pipy-owned slopfork of pi-mono's `loadProjectContextFiles`."
  The word "slopfork" is in production code committed to the project. It's a
  candid label but doubles as documentation that the file is exactly what
  this audit is checking for.
- **Article principle**: meta — the author admits the AI-slop origin in the
  doc.
- **Pi comparison**: N/A.
- **Suggested fix**: Either reduce the slop and rewrite the docstring, or
  promote the file to first-class status with a non-derogatory description.
- **Severity**: low
