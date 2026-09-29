# CTX1: Pi context-file and skill discovery

Status: implemented (2026-09-29). Reference: pi-mono `4df157433`.

## Problem

pipy loads `AGENTS.md > AGENTS.MD > pipy.md > PIPY.md` per directory and
deliberately skips `CLAUDE.md` (`native/workspace_context.py`). In a repo that
keeps its instructions only in `CLAUDE.md`, pipy therefore runs without them.
Skills come only from `<workspace>/.pipy/skills/*.md` (trusted), the global
`<config>/skills/*.md` and packages. The loader reads flat `*.md` files one
level deep, so the standard `<name>/SKILL.md` layout and `.agents/skills` are
never seen. Privacy is not a reason to differ from Pi.

## Pi reference (pinned)

### Context files: `core/resource-loader.ts:162-247`

- `loadContextFileFromDir(dir)` walks the candidates
  `["AGENTS.override.md", "AGENTS.md", "AGENTS.MD", "CLAUDE.md", "CLAUDE.MD"]`
  in order. It returns the first `join(dir, name)` that exists and is a file
  (`statSync` follows symlinks). A directory candidate continues to the next
  name. A read error warns and continues to the next name. Content is
  `stripBom(readFileSync(...))`.
- `loadProjectContextFiles({cwd, agentDir})`:
  1. The global file comes first, from `loadContextFileFromDir(agentDir)`.
  2. It then walks `resolvePath(cwd)` (absolute, **not** realpath) up to the
     filesystem root and `unshift`s each directory's file, so the result is
     root-most first and cwd last.
  3. It dedups by path string through `seenPaths`, seeded with the global
     file's path.
  4. Worktree shadowing uses `findShadowedContextFile(cwd)` (`:188-207`) with
     `findGitPaths` (`core/footer-data-provider.ts:16-48`). When cwd sits in a
     linked git worktree that is *nested inside* its main worktree, the main
     repo's context file with the same basename as the worktree root's own
     context file is skipped. The comparison is realpath-canonical. The
     function returns `undefined` in four cases: an ordinary repo, a sibling
     worktree, a bare layout (`<main>/.git` must canonicalize to the common git
     dir), and a submodule.
- Context files are **not** trust-gated. `reload()` (`:611-620`) calls
  `loadProjectContextFiles` unconditionally, with only `noContextFiles`
  applied.
- Prompt formatting is in `core/system-prompt.ts:72-79,163-178` and
  `ai/src/utils/text.ts:15-21`. The `project_context` section is:

  ```
  <project_context>
  Project-specific instructions and guidelines:

  <project_instructions path="/abs/AGENTS.md">
  <content>
  </project_instructions>

  <project_instructions path="...">
  ...
  </project_context>
  ```

  Sections are joined with `\n\n`, and the order is `preamble`, `tools`,
  `rules`, `docs`, `addendum` (append), `project_context`, `skills`, `cwd`.
  The `skills` section is `<skills>\n${formatSkillsForPrompt(...).trim()}\n</skills>`.
  Its third header line reads "…resolve it against the skill directory (parent
  of SKILL.md / dirname of the path)…".

### Skill roots and trust

These come from `core/package-manager.ts:454-487,2400-2560` and
`core/trust-manager.ts:179-205`.

- Auto skill roots are added to the resource accumulator in this order:
  1. project `<cwd>/.pi/skills`, in mode `"pi"`, **only when trusted**;
  2. project `.agents/skills` for each directory from cwd up to the git repo
     root (the first ancestor containing `.git`), or up to the filesystem root
     when there is no repo (`collectAncestorAgentsSkillDirs`). These use mode
     `"agents"`, are listed cwd-first, **only when trusted**, and exclude
     `~/.agents/skills`;
  3. user `<agentDir>/skills`, in mode `"pi"`;
  4. user `~/.agents/skills` (`$HOME`-based, not `agentDir`), in mode
     `"agents"`.

  CLI `--skill` paths come first overall. `mergePaths([...cli, ...enabled])`
  and CLI directories load via `loadSkillsFromDirInternal(dir, …, true)`,
  which is `"pi"` layout.
- Layout (`collectSkillEntries`, `:372-447`, and
  `skills.ts:loadSkillsFromDirInternal`):
  - A directory containing a `SKILL.md` file yields only that file and
    recursion stops there.
  - Otherwise the loader recurses into subdirectories. It skips names
    starting with `.` and `node_modules`.
  - Non-`SKILL.md` `*.md` files are included only at the root in `"pi"` mode,
    or only below the root in `"agents"` mode.
  - Symlinked files and directories are followed.
- Skill load (`skills.ts:277-345`):
  - name is `frontmatter.name || basename(dirname(file))`;
  - a skill with an empty or missing `description` is **dropped**, for
    `SKILL.md` and plain `.md` files alike;
  - dedup (`loadSkills`, `:419-450`) is first-wins by name, and a
    realpath-duplicate file is skipped silently.
- Trust detection (`hasTrustRequiringProjectResources`): a project needs
  trust when a protected `.pi/` entry exists **or** `.agents/skills` exists in
  cwd or *any* ancestor up to the filesystem root (not only up to the git
  root). `~/.agents/skills` is exempt, compared canonically.

## pipy change (this slice)

### 1. Context files (`native/workspace_context.py`)

- `INSTRUCTION_CANDIDATE_FILENAMES = ("AGENTS.override.md", "AGENTS.md",
  "AGENTS.MD", "CLAUDE.md", "CLAUDE.MD")`. `pipy.md`/`PIPY.md` are removed
  outright (no-deprecation policy; Pi has no equivalent). The global root
  (`PIPY_CONFIG_HOME` → `$XDG_CONFIG_HOME/pipy` → `~/.pipy` → `~/.config/pipy`)
  is pipy's `agentDir` and is unchanged.
- The walk starts from the absolute cwd (`os.path.abspath` of the expanded
  path, not `resolve()`), which matches `resolvePath`. Ordering stays
  global-first, then root-most ancestor, then cwd last.
- Dedup stays **canonical-path** based. This is a superset of Pi's
  string-path dedup: it also drops the same file reached through a symlinked
  ancestor. It only matters with symlink aliasing, and pipy already pins it.
- New: Pi worktree shadowing. `_find_git_paths` is a direct port of
  `findGitPaths`, and `_find_shadowed_context_file` ports
  `findShadowedContextFile`, including the bare-layout and submodule guards.
  A directory whose chosen candidate canonicalizes to the shadowed path
  contributes nothing. As in Pi, it does not fall through to another name.
- BOM stripping (a leading U+FEFF) is applied to content, like Pi `stripBom`.
- Kept (documented deviations, pre-existing): the symlink-escape guard (a
  candidate whose realpath leaves its directory is skipped and the next name
  is tried), the per-file/total byte caps with markers, and the
  metadata-only session surface (`path_label`, `sha256`, `byte_length`,
  `truncated`).
- `WorkspaceInstructionFile` gains `absolute_path: str`. That is the
  unresolved joined path, matching Pi's `path`, and it is used only in the
  provider prompt. It is never included in `workspace_instruction_safe_metadata`.
- `compose_system_prompt(base, discovery)` renders Pi's `project_context`
  section: `base.rstrip()` + `\n\n` + the section above, with the attribute
  value `path="<absolute_path>"`. The total-cap synthetic entry keeps its
  notice as a final `project_instructions` block with its label as the path.
  Context stays **not** trust-gated (unchanged).
- The startup chrome `[Context]` list is computed by calling the real loader
  (`discover_workspace_instructions`), so shadowing, dedup and guards cannot
  drift. Labels use Pi's `formatContextPath`: the cwd-relative path when the
  file is inside cwd, else `~`-abbreviated absolute. This replaces the
  duplicated candidate walk in `chrome.py`.

### 2. Skills (`native/_resource_files.py`, `native/skills.py`)

- Skill discovery gains a Pi layout walk. Templates and commands keep their
  flat `*.md` glob. A `layout` of `"flat" | "pi" | "agents"` per source
  implements `collectSkillEntries` exactly: `SKILL.md` stops recursion, `.`
  names and `node_modules` are skipped, and root-only (`pi`) or
  non-root-only (`agents`) plain `.md` files are included. Entries are sorted
  by name for determinism, since Pi uses `readdirSync` order.
- Source order for skills:
  1. CLI paths (`pi` layout);
  2. `<cwd>/.pipy/skills` (`pi`, trusted), the equivalent of Pi's
     `.pi/skills`;
  3. `.agents/skills` from cwd up to the git root or the filesystem root
     (`agents`, trusted, excluding `$HOME/.agents/skills`, compared by
     absolute path as Pi's `resolve(dir) !== resolve(userAgentsSkillsDir)`);
  4. `<config>/skills` (`pi`);
  5. `$HOME/.agents/skills` (`agents`);
  6. packages (unchanged position, see Deviations).
- The global resource root (`resolve_global_resource_root`:
  `PIPY_CONFIG_HOME` → `$XDG_CONFIG_HOME/pipy` → `~/.config/pipy`) is left
  unchanged. It is shared with `models.json`, prompt templates, commands and
  extension packages, so it differs from the context/settings root, which
  also probes `~/.pipy`. Unifying the two roots is a separate follow-on.
- Pi ignore rules (`addIgnoreRules`/`prefixIgnorePattern`,
  `IGNORE_FILE_NAMES = [".gitignore", ".ignore", ".fdignore"]`) are applied
  in the skill layout walk, with each skills root as the matcher root:
  - At every visited directory, those files' lines are added, prefixed by the
    directory's root-relative POSIX path. Blank lines and `#` comments are
    skipped, and `\#` and `\!` are escapes. A leading `!` negates and a
    leading `/` is stripped before prefixing.
  - A `SKILL.md` or a plain `.md` file is skipped when its root-relative path
    is ignored. A directory is not descended when `<rel>/` is ignored.
  - Matching follows node `ignore` (gitignore) semantics:
    - the last matching rule wins;
    - a trailing `/` matches directories only;
    - a pattern containing a `/` is anchored to the root, and one without a
      `/` matches any path segment;
    - `*`, `?`, `**` and `[...]` behave as in gitignore;
    - a path under an ignored directory is ignored.
  - The rules live in a small stdlib matcher (`native/ignore_rules.py`). pipy
    has no runtime dependency for this.
  - pipy's existing filename screen (secret-looking names, control
    characters, generated suffixes) still applies on top.
- Skill load rules:
  - name is the frontmatter `name`, or else for `SKILL.md` the parent
    directory name, or else the file stem (see Deviations);
  - a skill without a non-empty `description` is dropped, before name dedup
    and before byte-cap accounting, as in Pi;
  - first-wins name dedup plus canonical-path dedup, both unchanged.
- The symlink guard is kept. A source directory that is itself a symlink is
  skipped, and a discovered file whose realpath leaves the concrete source
  root is skipped. Recursion does not follow a symlinked subdirectory whose
  realpath leaves the root, because the containment check rejects its files.
- Path labels (metadata-safe, never absolute):
  - workspace-relative inside cwd (`.agents/skills/x/SKILL.md`);
  - `..`-relative for ancestor `.agents` dirs outside cwd;
  - `<global>/skills/<rel>` for `<config>/skills`;
  - `<user-agents>/skills/<rel>` for `~/.agents/skills`.
- The `compose_skills_system_block` prompt block becomes Pi's `skills`
  section: `\n\n<skills>\n<formatSkillsForPrompt text trimmed>\n</skills>`,
  using Pi's exact third header line. It is still gated on the read tool being
  visible (unchanged).
- Trust detection (`project_trust.has_trust_requiring_project_resources`) also
  returns true when `.agents/skills` exists in cwd or any ancestor (walked to
  the filesystem root), other than canonical `$HOME/.agents/skills`.
- `WorkspaceResources.discover`, the chrome `[Skills]` list, `/skill`, the
  prompt advertisement and the skill reference roots already share
  `discover_workspace_skills`, so they pick up the new roots with no further
  wiring. The call sites that need `home_dir` default to `Path.home()`.

## Deviations kept (documented in `docs/pi-parity.md`)

- The symlink-escape guard for context files and skills. Pi follows symlinks
  anywhere. Consequence: a skill directory symlinked out of `~/.pipy/skills`
  or `~/.agents/skills` is not loaded (follow-on candidate: relax for
  user-owned global roots).
- Byte caps and markers, and the metadata-only archive surface.
- Canonical-path context dedup (a superset of Pi's).
- A plain `.md` skill without a frontmatter `name` keeps its file stem. Pi
  would name it after its parent directory (`skills`), which is a follow-on
  candidate.
- The package skill position (pipy: after auto roots; Pi: before) is left for
  a follow-on.
- The global resource root is unchanged (`~/.config/pipy`, not `~/.pipy`, is
  the fallback for skills, templates, commands and `models.json`). Unifying
  it with the config-home root is a follow-on.
- Not ported in this slice: the Pi skill `disable-model-invocation`
  frontmatter, the "Use bash to load…" variant when only bash is visible, and
  the rest of Pi's sectioned system prompt (`<tools>`, `<rules>`, `<cwd>`
  tags).

## Tests (done-when)

- The candidate order is pinned: `AGENTS.override.md` beats `AGENTS.md`;
  `CLAUDE.md` loads when it is the only file; a `CLAUDE.md` in an ancestor
  loads; `pipy.md` is no longer loaded.
- Global first, then root-most, then cwd last; dedup when cwd equals the
  global root.
- Worktree shadowing:
  - a nested linked worktree skips the main repo's same-named file;
  - a sibling worktree does not;
  - an ordinary repo does not;
  - a different basename (worktree `CLAUDE.md`, main `AGENTS.md`) does not;
  - a bare layout (`proj/.bare` plus a worktree nested under `proj`) does
    not;
  - a submodule (its gitdir under `.git/modules`, no `commondir`) does not.
- The prompt renders exactly Pi's `<project_context>` bytes for two files.
  Absolute paths appear in the prompt but not in the safe metadata.
- BOM is stripped.
- Chrome `[Context]` labels come from the loader (cwd-relative and `~`
  forms, shadowed file absent).
- Skills:
  - `SKILL.md` directory layout;
  - `pi` vs `agents` plain-`.md` rules;
  - `.`/`node_modules` skipped;
  - `.agents/skills` ancestors stop at the git root;
  - untrusted projects load neither `.pipy/skills` nor project
    `.agents/skills`, while `~/.agents/skills` loads regardless;
  - `$HOME/.agents/skills` is not duplicated as a project root when cwd is
    under `$HOME`;
  - source order and first-wins dedup across roots;
  - a skill without a description is dropped and does not shadow a later
    same-named skill;
  - `SKILL.md` name fallback is the directory name;
  - `.gitignore`, `.ignore` and `.fdignore` rules apply: a nested file's
    prefixed rule, negation, a directory-only pattern, and an ignored
    directory that is not descended;
  - the ignore matcher gets unit tests for anchoring, `*`/`**`/`?`, negation
    order and directory-only rules.
- Trust detection: `.agents/skills` in an ancestor requires trust;
  `$HOME/.agents/skills` alone does not.
- The skills prompt block matches Pi's wrapped section text.
- `just check` is green. Docs are updated: `docs/usage.md` (context and
  skills), `docs/pi-parity.md`, `CHANGELOG.md`, `docs/backlog.md` (CTX1
  done), and the other docs that mention `pipy.md`.
