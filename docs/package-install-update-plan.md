# Package Install/Update User Docs Plan

## Gap

`docs/pi-mono-gap-audit.md` marks user documentation parity as still missing the
install/update deep dives. Pi's user docs cover package installation, removal,
listing, update modes, source syntax, security, package manifests, dependency
expectations, filtering, and resource enablement in
`/Users/jochen/src/pi-mono/packages/coding-agent/docs/packages.md`, with package
command summaries also linked from Pi quickstart/usage.

## Pi reference behavior to mirror in pipy docs

- Command family: `install`, `remove`/`uninstall`, `list`, `config`, and
  `update` with `--extensions`, `--extension <source>`, self/pipy target, force,
  and dry-run semantics. Pi also documents npm sources; pipy intentionally
  supports only local paths and managed git sources today, with PyPI/npm deferred.
- Source classes: local absolute/relative paths are stored without copying and
  resolved relative to the settings scope; git sources support `git:` shorthand
  and protocol URLs, optional pinned refs, SSH/HTTPS transport, clone caches, and
  reconcile/update behavior. Pipy does not run `npm install` or load TypeScript
  extensions.
- Settings scopes: default user settings, project-local settings with `-l`, and
  project settings that are shareable. Pipy uses `.pipy/settings.json` and the
  pipy user config root rather than Pi's `.pi/settings.json` / `~/.pi/agent`.
- Runtime loading: installed packages contribute extensions, skills, prompt
  templates, and themes through package rules; per-run `--extension` remains the
  try-without-install surface.
- Security: packages are trusted local code/content. Users should review sources
  before installing; credentials/secrets remain outside package settings.

## Pipy implementation scope

Add a user-facing `docs/packages.md` page for pipy packages. Keep it honest to
shipped pipy behavior: local path and managed git sources only, no PyPI/npm,
Python extension files, TOML themes, `pipy-extension.toml`, and pipy config/state
paths. Link the page from `docs/index.md`, `docs/quickstart.md`, and the package
section of `docs/usage.md`. Update `docs/user-documentation.md`,
`docs/pi-mono-gap-audit.md`, and `docs/backlog.md` to mark the install/update
user-doc gap shipped.

## Done when

- `docs/packages.md` covers install/remove/list/config/update, local and git
  source syntax, settings scopes, package layout/manifests, dependencies/current
  limitations, filtering/enablement, security, and examples.
- Existing user-doc entry points link to the new page.
- Parity/backlog docs no longer list install/update deep dives as missing.
- `just check` passes and the final different-family review is CLEAN.
