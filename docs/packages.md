# Pipy Packages

Pipy packages bundle trusted local resources — Python extensions, skills, prompt
templates, custom slash commands, and themes — so they can be reused across
projects or shared through a git repository. Pipy currently supports local paths
and managed git sources. PyPI and npm package sources are intentionally deferred
until there is an explicit supply-chain policy.

> **Security:** installed packages are trusted content. Python extensions execute
> local code, and skills/prompts can instruct the model to perform actions such as
> running commands. Review a package before installing it, and keep credentials in
> provider auth stores or environment variables rather than package files.

## Install and manage

```bash
pipy install ./path/to/package
pipy install /absolute/path/to/package
pipy install git:github.com/user/repo@v1
pipy install https://github.com/user/repo@main

pipy remove ./path/to/package
pipy uninstall ./path/to/package   # alias for remove
pipy list
pipy config

pipy update                        # update pipy and reconcile installed packages
pipy update --extensions           # reconcile packages only
pipy update --extension git:github.com/user/repo@v1
pipy update self --dry-run
pipy update self --force
```

By default `install` and `remove` write package-source entries to the user
settings file under the pipy configuration root. Those package commands follow
Pi's `-l`/`--local` convention for choosing project settings instead:

```bash
pipy install -l ./tools/pipy-package
```

Project installs are stored in `.pipy/settings.json` and can be committed when a
team intentionally shares that package dependency. User installs stay local to
the current account. Use `--cwd <dir>` on package commands when operating on a
project other than the current working directory.

To try an extension or resource without installing a package, use the per-run
resource flags instead:

```bash
pipy --extension ./extension.py
pipy --skill ./skills/review/SKILL.md
pipy --prompt-template ./prompts/fix.md
pipy --theme ./themes/night.toml
```

## Package sources

### Local paths

Local sources may be absolute or relative paths:

```text
/absolute/path/to/package
./relative/path/to/package
```

A local source points at the existing file or directory; pipy does not copy it
into a cache. Relative paths are resolved from the settings file that contains
them, so a project-local install can use paths relative to the project.

If the source is a single Python extension file, pipy loads it as that extension.
If the source is a directory, pipy discovers resources using the package manifest
and conventional directories described below.

### Managed git sources

Git sources can use a `git:` shorthand or a protocol URL:

```text
git:github.com/user/repo@v1
git:git@github.com:user/repo@v1
https://github.com/user/repo@main
ssh://git@github.com/user/repo@v1
```

- `git:` sources accept shorthand forms such as `github.com/user/repo` and
  `git@github.com:user/repo`.
- Protocol URLs (`https://`, `http://`, `ssh://`, `git://`) can be used without
  the `git:` prefix.
- HTTPS and SSH use your normal git configuration. In non-interactive runs, set
  `GIT_TERMINAL_PROMPT=0` or `GIT_SSH_COMMAND` if you need git to fail fast.
- Optional `@ref` suffixes pin a branch, tag, or commit. Updating reconciles the
  local checkout to the configured ref; it does not silently move a pinned ref to
  a newer version.
- Pipy clones managed git packages into a pipy-owned cache and resets/cleans the
  checkout during reconciliation.

## Package structure

A directory package can declare resources in `package.json` under a `pipy` key or
use conventional directories. Paths are relative to the package root.

```json
{
  "name": "my-pipy-package",
  "pipy": {
    "extensions": ["extensions"],
    "skills": ["skills"],
    "prompts": ["prompts"],
    "themes": ["themes"]
  }
}
```

Conventional directories are used when no manifest is present:

- `extensions/` loads Python extension files and extension directories with a
  `pipy-extension.toml` manifest.
- `skills/` recursively loads `SKILL.md` directories and top-level Markdown
  skills.
- `prompts/` loads Markdown prompt templates and custom slash commands.
- `themes/` loads TOML theme files.

Pipy packages are Python/resource packages, not Pi TypeScript packages. Pipy does
not run `npm install`, does not execute JavaScript/TypeScript extensions, and
ignores npm-only metadata except where it is useful for package resource paths.

## Dependencies and credentials

Python extension dependencies are currently the package author's responsibility:
ship stdlib-only extensions where possible, vendor safe helper code in the
package, or document the Python environment expected by the extension. Do not put
API keys, OAuth tokens, private keys, or other credentials in package resources or
settings. Use provider environment variables, `/login`, or provider-specific auth
storage instead.

## Filtering and enablement

Installed packages contribute resources to the same discovery pipeline as local
project resources. You can enable or disable individual resources with
`pipy config`:

```bash
pipy config list
pipy config --json
pipy config enable skill review
pipy config disable prompt legacy-*
pipy config --scope project disable extension experimental
```

`pipy config` uses its own explicit scope flag: `--scope global` writes resource
filters to the user settings file, and `--scope project` writes them to
`.pipy/settings.json`. Filters are glob-style patterns applied by resource type,
so a package can stay installed while selected skills, prompts, themes, or
extensions are disabled.

## Runtime loading

Installed package resources are loaded on startup and after `/reload` according
to the active settings and per-run resource flags. Skills appear in the startup
skills section and can be opened through `/skill`; prompt templates become their
own slash commands; themes become selectable in `/settings`; extensions can
register commands, tools, UI hooks, model/provider contributions, and other
supported extension surfaces.

## Current limitations

- PyPI and npm package sources are not accepted yet.
- JavaScript/TypeScript Pi extensions are not executed by pipy.
- Pipy's package update reconciles managed git caches and supports self-update
  planning, but the exact installer used for `pipy update self` depends on the
  local checkout/installation method.
- Broader extension APIs continue to land in small slices; see
  [Extension API](extension-api.md) for the maintainer-level target surface.
