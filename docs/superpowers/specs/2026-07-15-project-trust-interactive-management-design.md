# Project trust interactive and management integration — design

Status: implemented 2026-07-15; the design received a direct Claude Opus plan
review before implementation.

Reviewed parent design:
[`2026-07-15-project-trust-design.md`](2026-07-15-project-trust-design.md).
Ordered track plan:
[`2026-07-15-project-trust-implementation-plan.md`](2026-07-15-project-trust-implementation-plan.md).
Pi reference: `/Users/jochen/src/pi-mono` at `b084d2fb`, especially
`packages/coding-agent/src/core/project-trust.ts`, `trust-manager.ts`,
`package-manager-cli.ts`,
`modes/interactive/components/trust-selector.ts`,
`modes/interactive/components/settings-selector.ts`, and
`modes/interactive/interactive-mode.ts`.

## Single-gap scope

Implement only slice 2 of the reviewed project-trust track: the interactive
startup decision, post-start warning, local `/trust` command, manual-reload
auto-persist exception, global `defaultProjectTrust` settings control, and
trust-aware `install`, `remove`/`uninstall`, `list`, and `config` commands.

The extension-owned `project_trust` decision event, pre-trust extension reuse,
and `ctx.isProjectTrusted()` remain slice 3. Package `update` retains its current
behavior until the separate update/config realignment gap; this slice does not
make it prompt, consult the global default, or silently change its target
semantics. Remote package sources, `.agents/skills`, standalone workspace theme
discovery, and a sandbox remain deferred.

## Startup selection

The existing core order remains: last CLI override -> no protected input ->
closest saved decision -> global-only `defaultProjectTrust` -> UI/headless
fallback. This slice fills only the final UI branch. A protected, unresolved
interactive TTY run opens a local selector before project settings, package
roots, extensions, catalog contributions, providers, system prompts, or other
protected resources are built. Print, JSON, RPC, list-models, piped input, and
other non-UI paths keep the current immediate `false` fallback and never read
stdin or contaminate protocol stdout/stderr.

The startup selector shows the canonical cwd and explains that trust enables
project settings/resources, project packages, and executable project
extensions. Its ordered choices and stored updates are:

| Choice | Result | Persisted updates |
| --- | --- | --- |
| `Trust` | trusted | exact cwd `true` |
| `Trust parent folder (<parent>)` | trusted | parent `true`, then exact cwd delete in one `set_many` |
| `Trust (this session only)` | trusted | none |
| `Do not trust` | untrusted | exact cwd `false` |
| `Do not trust (this session only)` | untrusted | none |

The parent row is omitted at a filesystem root. Escape, Ctrl-C, Ctrl-D, EOF, or
no selection resolve untrusted without writing. A trust-store read/write error
fails closed and is shown as a bounded diagnostic. The standalone selector
reuses pipy's inline TUI navigation and resize handling and closes before the
normal product TUI starts, so selector text is never appended to either the
native session tree or the metadata-first archive.

After startup, an untrusted interactive session renders one live warning only
when a protected input exists: project `.pipy` resources/packages were ignored,
and `/trust` can save a decision for the next restart. It is UI/diagnostic state,
not session content. The temporary slice-1 unresolved-trust stderr message is
removed; headless modes remain silent.

## `/trust` and reload

`/trust` is a built-in local command and slash-menu entry. It runs no provider
turn or tool call and appends no user/assistant/session entry. The selector
shows:

- canonical cwd;
- closest saved decision (`none`, exact, or inherited source path); and
- the immutable current run-local state (`trusted` or `untrusted`).

It offers only persistent `Trust`, optional `Trust parent folder (<parent>)`,
and `Do not trust` rows. Saving uses the same update tuples as startup and
reports `Restart pipy for this to take effect.` It never hot-loads or unloads
settings, packages, extensions, providers, prompts, or resources. Cancel makes
no change. Store failures are live diagnostics and leave the runtime unchanged.
In a non-TUI/captured-stream session, `/trust` reports that an interactive TUI
is required rather than reading stdin.

The `/reload` exception is deliberately narrow. Startup records an
`auto_trust_on_reload_cwd` candidate only when the resolved final cwd had no
protected input and the run therefore entered trusted state through the
no-resource short circuit (not a CLI override, saved decision, or global
default). Immediately before a manual reload discovers project inputs, it may
persist exact cwd `true` only when all guards still hold:

1. the current canonical cwd equals the candidate;
2. the run-local settings state is still trusted;
3. a protected input now exists; and
4. the trust store still has no exact or inherited boolean decision.

On success the candidate is consumed and the reload status says trust was
saved. If a saved decision appeared, the candidate is consumed without
writing. On a store error it remains fail-closed, emits a warning, and does not
claim persistence. Merely creating a file, an automatic repaint, a reload from
an untrusted run, or a different cwd never writes `trust.json`.

## Global default setting

`SettingsManager` gains a typed `set_default_project_trust(value)` accepting
only `ask`, `always`, or `never` and always writing global scope. The product
`/settings` dialog adds `Default project trust` with Pi's labels:

| Stored value | Label |
| --- | --- |
| `ask` | `Ask` |
| `always` | `Trust` |
| `never` | `Do not trust` |

Activating the row opens a three-choice local selector, persists the selected
global value, and repaints the settings dialog. It affects later process
startups only; it does not re-resolve the current run. The field remains
global-only even when the current project is trusted.

## Package and config commands

`install`, `remove`/`uninstall`, `list`, and `config` accept ordered
`--approve`/`-a` and `--no-approve`/`-na` flags; the last occurrence wins for
that command and is never persisted. `config` also accepts Pi's `-l`/`--local`
spelling for project scope while the broader config-command realignment remains
separate.

Each command constructs settings with `project_trusted=false`, resolves trust
for its canonical `--cwd`, then reloads project scope only when trusted. The
resolver uses the same override/no-resource/saved/global/UI order as product
startup, with the standalone selector only when both stdin and stdout are TTYs.
Extension ownership remains absent until slice 3.

| Command case | Trusted | Untrusted |
| --- | --- | --- |
| global install/remove | may read project settings; writes global | project scope is never opened; global write still succeeds |
| local install/remove (`-l`) | writes project settings | exits nonzero before package resolution/cache mutation/settings write; names `--approve` |
| list | shows global and project packages | shows global packages only |
| config list | discovers global plus trusted project/package resources | omits project settings/packages/resources, retains global resources |
| config global enable/disable | writes global | writes global without opening project settings |
| config local enable/disable (`-l` or project scope) | writes project | exits nonzero before discovery or write; names `--approve` |

Explicit `--no-approve` overrides a saved `true`; explicit `--approve` permits a
local operation without creating `trust.json`. No-resource short-circuit still
permits the first local write, matching Pi; that new protected input may prompt
on a later process startup. Malformed/unreadable trust state fails closed.

`update` is intentionally untouched. The later realignment slice will make its
package half use only explicit or saved `true` trust, without prompting or
consulting `defaultProjectTrust`, while also fixing bare self-only/`--all`
semantics.

## Verification contract

Focused tests cover option/update construction, selector cancellation and
write failure, real-PTY startup choices at 80x24 plus resize/cancel recovery,
warning/session-tree privacy, `/trust` exact/inherited/current display and
non-hot activation, every reload auto-persist guard, the settings enum, ordered
management overrides, local refusal before mutation, and untrusted list/config
provenance. The deterministic project-trust conformance gate expands with
interactive helper, reload, settings, and management checks without network or
model calls.

## Done when

- Focused project-trust, TUI/PTY, settings, package/config, and conformance tests
  pass.
- `just check` and `prek run --all-files` (only when configured) pass.
- User/security/settings/package docs, parity indexes, and `CHANGELOG.md` mark
  only slice 2 shipped and keep extension trust plus update realignment queued.
- The exact code, tests, docs, and plan diff receives a direct fresh Claude
  Opus `CLEAN` review and is committed on `main` without pushing.
