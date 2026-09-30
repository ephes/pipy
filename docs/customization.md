# Customization

Pipy loads several kinds of user-supplied resources at startup: **skills**,
**prompt templates**, **custom slash commands**, and **chrome themes**. Each is
a plain file you drop into a discovery directory — no build step. Extensions,
packages, and custom providers extend pipy further and are covered in their own
pages.

> **Security:** Skills, templates, custom commands, and extensions can instruct
> the model to perform any action and may reference executable code. Review
> resource content before using resources you did not write.

Project `.pipy` resources are trust-gated before startup. Saved decisions live
in the global config root's `trust.json`; `--approve` and `--no-approve` provide
run-only overrides. When a project is untrusted, its skills, templates, commands,
extensions, system-prompt files, settings, and project package declarations are
not loaded. Global resources/packages and explicit per-run source flags remain
available. Project `.agents/skills` directories (in the project or any parent
directory) also make a project need trust, as in Pi. Your own `~/.agents/skills`
does not. Context files (`AGENTS.md`, `CLAUDE.md` and the other Pi candidates)
are intentionally exempt from this gate (unless `--no-context-files` is used). Interactive startup can save a
current/parent/session-only decision; `/trust` manages the saved next-restart
decision. Trust is not a sandbox.
Before saved/default/UI fallback, global and explicit CLI extensions may handle
the serial `project_trust` event; project extensions remain unavailable until a
trusted decision wins. Normal extension contexts can read the run-local state
through `is_project_trusted()` or `isProjectTrusted()`.

## Where resources live

Skills, prompt templates, and custom commands are discovered from the same two
roots, plus installed packages. Themes are the exception: they come from pipy's
built-ins and installed packages (and explicit `--theme` files), not from a
workspace or global theme directory.

| Source | Skills | Templates | Commands | Themes |
| --- | --- | --- | --- | --- |
| Project (workspace) | `.pipy/skills/` | `.pipy/templates/` | `.pipy/commands/` | (packages only) |
| Project `.agents` (cwd up to the git root) | `.agents/skills/` | — | — | — |
| Global config root | `<root>/skills/` | `<root>/templates/` | `<root>/commands/` | (packages only) |
| User `.agents` | `~/.agents/skills/` | — | — | — |
| Installed packages | `skills/` | `templates/` | `commands/` | `themes/*.toml` |

Skills are searched in that row order, after any `--skill` paths, and the first
skill with a given name wins. The project rows load only for trusted projects.

The global config root resolves in order: `$PIPY_CONFIG_HOME`, then
`${XDG_CONFIG_HOME}/pipy`, then `~/.pipy` when that directory exists, then
`~/.config/pipy`. Settings, context files, trust, keybindings, resources,
extensions and `models.json` all use this one root. Pipy intentionally uses
`.pipy` for project config, not Pi's `.pi`.

Symlinks in skill roots are followed like Pi: a skill directory or file
symlinked into `~/.pipy/skills/`, `~/.agents/skills/` or a `--skill` path loads
from wherever it points, and so does one in a trusted project's `.pipy/skills/`
or `.agents/skills/`. The template and command stores still skip a symlink that
points outside the store.

Workspace resources are discovered first, then global, then package resources at
the lowest precedence.

### Filtering and disabling resources

`settings.json` carries an array per resource kind whose entries are
include/exclude patterns (`+pattern` keeps, `-pattern` drops), managed with
`pipy config`:

```json
{
  "skills": ["+review-*", "-secret-*"],
  "prompts": ["+*"],
  "themes": ["+*"],
  "extensions": ["+*"],
  "enableSkillCommands": true
}
```

Per run, the `pipy` REPL also accepts explicit source flags and matching cutoffs:

| Flag | Effect |
| --- | --- |
| `--skill PATH` | Load an extra skill file/dir (repeatable; loads even with `--no-skills`). |
| `--no-skills` / `-ns` | Disable default skill discovery for this run. |
| `--prompt-template PATH` | Load an extra template file/dir (repeatable). |
| `--no-prompt-templates` / `-np` | Disable default template discovery for this run. |
| `--theme PATH` | Make an extra theme file/dir selectable (does not select it). |
| `--no-themes` | Disable package theme discovery for this run. |
| `--extension PATH` / `--no-extensions` | See [Extensions](extension-api.md). |

After editing settings or resource files by hand, run `/reload` to pick up the
changes mid-session.

## Skills

A skill is a Markdown file (with optional YAML frontmatter) that packages a
specialized workflow the model loads on demand. Pipy follows Pi's
progressive-disclosure model: at startup, each discovered skill's **name and
description** are advertised in the tool-loop system prompt (along with the
skill's absolute location), and the model loads the full body on demand with the
`read` tool. Only the descriptions stay in context; the bodies load when needed.

Pipy discovers skills in Pi's layout:

- A directory that contains a `SKILL.md` is one skill. The skill is named after
  the directory unless the frontmatter sets `name`. Pipy does not look inside
  that directory for more skills.
- Other directories are searched recursively. Entries whose names start with
  `.` are skipped, and so is `node_modules`.
- Plain `*.md` files count as skills only in some places:
  - at the top level of `.pipy/skills/`, `<root>/skills/`, a package skill
    directory, or a `--skill` directory;
  - below the top level of `.agents/skills/`.
- `.gitignore`, `.ignore` and `.fdignore` files inside a skills directory hide
  the entries they match.
- A skill needs a non-empty `description`. Pipy skips a file without one.

Add a skill as `.pipy/skills/<name>/SKILL.md` or `.pipy/skills/<name>.md` in your
project, as `.agents/skills/<name>/SKILL.md` to share it with other agents, or as
`<root>/skills/<name>/SKILL.md` globally:

```markdown
---
name: lint-fix
description: Run the linter and fix reported issues. Use when asked to clean up lint.
---

# Lint and fix

Run `just lint`, read each reported file, and apply minimal fixes.
```

The frontmatter `description` determines when the model reaches for a skill, so
be specific. The model reads a skill body by its absolute location, and `read`
opens any path, so a skill body can also point at sibling files.

### Skill commands

`/skill` lists the discovered skills (names and descriptions only). `/skill
<name>` loads one immediately without a provider turn:

```text
/skill                # list available skills
/skill lint-fix       # load the lint-fix skill body
```

Skill commands are registered when `enableSkillCommands` is `true` (the
default); set it to `false` in `settings.json` to register no skill commands and
list none.

> Pipy uses `/skill <name>` (a single `/skill` command with an argument), where
> Pi uses per-skill `/skill:name` commands. The capability is the same.

## Prompt templates

A prompt template is a Markdown file whose body is sent as your next message,
with arguments substituted in. Drop it at `.pipy/templates/<name>.md` (or
`<root>/templates/<name>.md`) and invoke it as its **own** slash command:

```markdown
---
name: pr-summary
description: Summarize the staged diff as a PR description.
---

Write a PR description for this change. Focus area: $ARGUMENTS
```

```text
/pr-summary the auth refactor
```

The body expands `$ARGUMENTS` / `${ARGUMENTS}` to the full argument string and
`$1`..`$9` / `${1}`..`${9}` to individual whitespace-separated arguments, then
sends the result as your user message. There is no `/template` wrapper command —
each template is invoked directly by its name (the pipy-only `/template`
dispatcher was removed for Pi parity).

## Custom slash commands

A custom slash command (sometimes called a user command) is like a prompt
template but conceptually a reusable command rather than a one-off message. Place
it at `.pipy/commands/<name>.md` (or `<root>/commands/<name>.md`) with optional
`name`/`description` frontmatter; the body is the message text, with the same
`$ARGUMENTS` / `$1`..`$9` substitution, sent when you type `/<name>` at the
prompt.

## Themes

Pipy ships built-in chrome themes as code-defined palettes. Installed local-path
and managed-git packages can contribute **additional** themes as `.toml` files
in a package `themes/` directory. A theme file sets a `name` plus any subset of
the palette's color fields; unspecified fields inherit the default palette, so a
theme can override just a few colors:

```toml
name = "midnight"
# Each value is a safe SGR parameter string (digits and semicolons only).
primary = "38;5;39"
secondary = "38;5;244"
```

Select the active theme from the `/settings` dialog (the theme row opens a
picker), with the `PIPY_THEME` environment variable, or by setting `theme` in
`settings.json`. `--theme PATH` only makes a theme *available* for selection; it
does not switch to it. (The standalone `/theme` command was removed for Pi
parity — theme selection lives in `/settings`.)

## Extensions, packages, and custom providers

- **Extensions and packages** add commands, tools, hooks, UI, and bundled
  resources through pipy's Python extension platform. See
  [Extension API](extension-api.md). The platform is Pi-shaped but not yet
  Pi-equivalent; richer extension UI and remote package sources are still in
  progress. An extension tool may activate more registered tools with
  `ctx.set_active_tools(...)`; purely additive changes preserve the cached tool
  prefix on supported Anthropic Claude 4.5+ and selected OpenAI/Codex Responses
  models, while unsupported models safely receive the complete current tool
  list.
- **Custom providers and models** are configured through `models.json` and the
  provider catalog. See [Providers and models](providers.md).

## See also

- [Settings](settings.md) — the `skills` / `prompts` / `themes` / `extensions`
  arrays, `enableSkillCommands`, and `theme`.
- [Using pipy](usage.md) — slash commands and the CLI reference.
- [Extension API](extension-api.md) and [Providers and models](providers.md).
