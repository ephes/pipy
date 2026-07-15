# Project trust — design

Status: reviewed parity-loop design slice (direct Claude Opus plan review CLEAN
2026-07-15). This document is the deliverable for the first, design-only gap in
the project-trust track. Runtime work is split into the implementation slices
under "Delivery sequence" rather than being mixed into this review.

Gap sources: `docs/pi-mono-gap-audit.md` priority 2,
`docs/parity-plan.md` section 5 item 2, and `docs/backlog.md` "Next: project
trust". Reference checkout: `/Users/jochen/src/pi-mono` at `b084d2fb`.

## Scope

Pin the complete trust decision and protected-resource loading order before
runtime code changes. The design covers:

- the canonical trust-store schema and closest-ancestor lookup;
- what project inputs require trust and which inputs remain available;
- the startup decision order in interactive, print, JSON, and RPC modes;
- `defaultProjectTrust`, `--approve` / `--no-approve`, `/trust`, and reload;
- package/config command behavior; and
- extension decision ownership plus the read-only
  `ctx.isProjectTrusted()` surface.

This slice does **not** implement trust yet. It does not change package-update
semantics, add remote package sources, add `.agents/skills` discovery, or add a
sandbox. Those boundaries remain explicit below.

## Security boundary

Project trust is an input-loading guard, not an execution sandbox. A trusted
project may influence settings, prompts, resources, packages, and executable
extensions before the first turn. An untrusted project must not influence those
protected startup inputs. Once a session runs, the model-visible tools and any
loaded extension code still have the permissions of the pipy process.

Pipy retains its existing context-file rule: workspace/ancestor `AGENTS.md` /
`pipy.md` instruction files remain loadable regardless of project trust unless
`--no-context-files` is set. This mirrors Pi's exemption for `AGENTS.md` and
`CLAUDE.md`; it is an explicit prompt-injection risk, not a claim of isolation.

Pi references:

- `packages/coding-agent/docs/security.md:5-57`
- `packages/coding-agent/src/core/trust-manager.ts:20-28,166-198`
- `packages/coding-agent/src/core/resource-loader.ts:463-488`

## Trust store

Pipy will add a `ProjectTrustStore` at `<resolved-config-home>/trust.json`, next
to its global `settings.json`. `<resolved-config-home>` follows the existing
`PIPY_CONFIG_HOME` / XDG / home resolution in
`pipy_harness.native.settings.resolve_config_home`; this is pipy's equivalent
of Pi's `<agentDir>/trust.json`.

### Persisted field list

| Field | Optionality | Value | Rule |
| --- | --- | --- | --- |
| object key | required per entry | canonical absolute project or parent directory | `Path.expanduser().resolve()`; lookup uses the same canonicalization |
| object value | required per persisted entry | JSON boolean | `true` trusts; `false` declines |

The root value is an object. Pi's reader accepts `null` values for compatibility,
but `set(path, null)` deletes the entry and the writer never emits a new null.
Pipy will do the same: accept `null` while reading, ignore it during lookup, and
delete rather than persist it on update. Any other root/value shape is invalid.
Keys are serialized in sorted order with a trailing newline. Pipy additionally
uses its existing lock/atomic-write pattern and owner-private file permissions;
these storage mechanics do not change the Pi decision semantics. A malformed or
unreadable store fails closed with a diagnostic and is never silently
overwritten.

Lookup starts at the canonical current directory and walks one parent at a time
to the filesystem root. The closest boolean entry wins. Therefore an exact child
decision overrides its parent, and deleting the child reveals the inherited
parent decision. There is no trust-by-name, git-root, or settings-file boundary.

Pi references:

- `core/trust-manager.ts:30-52` (canonicalization and nearest ancestor)
- `core/trust-manager.ts:94-123` (validated schema and sorted write)
- `core/trust-manager.ts:200-230` (store path and get/set/delete)
- `test/trust-manager.test.ts:24-37` (child override and delete fallback)

## Inputs that require trust

The detector checks for protected project inputs without parsing or importing
them. A bare `.pipy` directory does not require a decision.

| Pipy project input | Trust required | Pi analogue / note |
| --- | --- | --- |
| `.pipy/settings.json` | yes | `.pi/settings.json` |
| `.pipy/extensions` | yes | executable project extensions |
| `.pipy/skills` | yes | project skills |
| `.pipy/templates` | yes | Pi `.pi/prompts` |
| `.pipy/commands` | yes | pipy-owned executable Markdown resource; folded into the protected resource set |
| `.pipy/themes` | yes | project themes |
| `.pipy/SYSTEM.md` | yes | project system-prompt replacement |
| `.pipy/APPEND_SYSTEM.md` | yes | project system-prompt append |
| project packages named by `.pipy/settings.json` | yes | unreachable until trusted settings load |
| workspace/ancestor `AGENTS.md` / `pipy.md` | no | context-file exemption; still subject to `--no-context-files` |
| global settings/resources/packages/extensions | no | user-owned inputs |
| explicit per-run CLI extension/skill/template/theme paths | no | operator-selected temporary inputs |

Pi also treats `.agents/skills` in the current directory or any ancestor as a
trust-requiring project input, except the user's `~/.agents/skills`. Pipy does
not currently discover `.agents/skills`; the trust implementation must not add
a dead detector that prompts for a resource pipy cannot load. If `.agents/skills`
discovery is added later, that slice must add Pi's ancestor scan and global-home
exception to this detector at the same time.

Detection failures are conservative: an `OSError` does not make a resource
loadable; the decision pipeline treats the affected protected input as
untrusted and reports a safe diagnostic.

Pi references:

- `core/trust-manager.ts:20-28,166-198`
- `docs/security.md:9-21`

## Decision order

Trust is resolved for the **final runtime cwd**, after startup session selection
or resume determines it and before any project settings or resources are read.
The result is run-local and keyed by canonical cwd when a session switch creates
a runtime for another project.

The ordered decision is:

1. A CLI override wins: `--approve` / `-a` returns trusted and
   `--no-approve` / `-na` returns untrusted for this invocation only. It is not
   persisted and suppresses the extension decision event, saved-store lookup,
   global default, and prompt. If both flags occur, normal argument order makes
   the last one win, matching Pi's sequential parser.
2. If no trust-requiring project input exists, return trusted without prompting
   or writing the store.
3. Load only the **pre-trust extension set**: global extensions, explicit CLI
   extensions, and process-inline/test factories. Project extensions and
   project-package extensions remain unavailable. Emit `project_trust` directly
   to this ordered set; the first handler returning `yes` or `no` owns the
   decision. `undecided` continues to the next handler. Handler failures warn and
   continue. `remember: true` saves only the exact cwd decision.
4. Read the closest saved store decision. A boolean wins over the global
   default.
5. Read `defaultProjectTrust` from **global settings only**. `always` trusts,
   `never` declines, and missing/invalid/`ask` proceeds to the UI rule. A project
   setting may not set its own trust policy because project settings are still
   gated.
6. With an interactive startup UI, show the trust selector. Cancel or no
   selection declines for the session. With no UI (print, JSON, RPC, piped
   startup, help/list-model resolution), `ask` fails closed to untrusted and no
   prompt is written to stdout or stderr.

The pre-trust extension instances are reused in the final extension runtime so
global/CLI module top-level code is not executed twice. After the decision, the
settings manager reloads with the chosen trust state; only then may package
resolution, final extension activation, skill/template/command/theme discovery,
system-prompt discovery, provider registration, model resolution, and the first
turn occur.

Pi references:

- `core/project-trust.ts:42-99` (decision order and non-UI fallback)
- `main.ts:567-678` (final cwd, untrusted bootstrap, resolution, final services)
- `core/resource-loader.ts:330-355,400-404,492-565` (preload, reuse, final load)
- `cli/args.ts:178-184` (sequential override parsing)

### Mode matrix

| Mode | UI prompt when unresolved + `ask` | Result for unresolved + `ask` |
| --- | --- | --- |
| interactive TTY | yes | selected option; cancel = untrusted |
| `--print` / `-p` | no | untrusted |
| `--mode json` | no | untrusted; stdout remains protocol-only |
| `--mode rpc` | no | untrusted; stdin/stdout remain protocol-only |
| help / list-models startup | no | untrusted |

This table is scoped only to the final unresolved + `defaultProjectTrust:
"ask"` branch. Earlier decision steps still apply in every mode: a CLI override,
absence of protected inputs, an extension yes/no decision, a saved decision, or
`defaultProjectTrust: "always"` / `"never"` resolves before the mode-specific UI
fallback.

`defaultProjectTrust: "always"` is a Pi-forced non-interactive opt-in: unlike the
upstream absence of a trust system, the default remains `"ask"`, which means
untrusted without UI. There is no implicit "automation means approve" default.

## Settings ownership

`SettingsManager` gains a run-local `project_trusted` boolean. Its constructor
and reload path skip opening/parsing `.pipy/settings.json` when false and expose
an empty project scope; effective settings then consist of base defaults, global
settings, and CLI/env overrides only. Switching false clears previously loaded
project values and errors. Switching true loads the project scope. Any project-
scope write while false raises before touching disk.

### `defaultProjectTrust` field

| Field | Optionality | Accepted values | Default / ownership |
| --- | --- | --- | --- |
| `defaultProjectTrust` | optional | `"ask"`, `"always"`, `"never"` | `"ask"`; global settings only |

Invalid values resolve to `"ask"` rather than becoming truthy or failing open.
The `/settings` control may expose this enum only when it writes global scope.

Pi references: `core/settings-manager.ts:94-101,308-378,442-477,899-907`.

## Resource and package loading

When untrusted:

- project settings are empty, so project package declarations and local
  resource filters are absent;
- no workspace `.pipy` extension, skill, template, command, or theme default is
  discovered;
- no project `SYSTEM.md` / `APPEND_SYSTEM.md` is read;
- global packages and their resources remain available; and
- explicit CLI resources remain available, including with the matching
  `--no-*` flag, because the operator named them for that run.

The gate must be carried as source provenance, not approximated by turning off
all default discovery: global defaults must continue to load. Pipy's current
discovery helpers mix workspace and global sources under one
`include_defaults` boolean, so runtime implementation must split or filter the
source set explicitly. Package composition must receive the trust-filtered
settings manager before resolving roots, preventing project package code from
being imported during the pre-trust phase.

Context loading remains after this gate and independent of it. Theme selection,
provider registrations, resource enablement, and model construction must use the
post-decision settings/resources, never values read by an eager pre-decision
caller.

Pipy boundaries affected by later runtime slices:

- `native/settings.py:277-381`
- `native/package_runtime.py:33-58`
- `native/extensions.py:154-246`
- `native/_resource_files.py:120-227`
- `native/resources.py:138-239`
- `native/system_prompt_inputs.py:128-197`
- `native/tool_loop_session.py:1303-1380`
- `cli.py:1701-1723,2321-2431`

## Startup selector and `/trust`

The unresolved interactive startup selector offers, in order:

1. `Trust` — save `{cwd: true}`;
2. `Trust parent folder (<parent>)` — save `{parent: true}` and delete the exact
   cwd entry so inheritance is effective;
3. `Trust (this session only)` — trusted, no store update;
4. `Do not trust` — save `{cwd: false}`;
5. `Do not trust (this session only)` — untrusted, no store update.

The parent option is omitted at a filesystem root. The selector title shows the
canonical cwd and explains that trust enables project settings/resources,
project packages, and executable project extensions.

`/trust` is a local TUI command (no provider turn, tool call, or session-content
append). It shows the saved decision, including an inherited source path, and
the current run-local state. It offers only persistent current/parent/decline
options; after writing it reports that restart is required. It does **not**
retroactively activate or unload resources in the running process.

Reload has one Pi-specific exception. If startup found no protected input, the
run was implicitly trusted without a saved decision. If a protected input is
then added and `/reload` loads it under that already-trusted runtime, reload
saves an exact `cwd: true` decision once. A run that started untrusted does not
become trusted merely because `/trust` wrote the store; it still requires
restart.

That auto-persist is intentional Pi parity, not a general background trust
promotion. It occurs only after the user explicitly invokes `/reload`, only for
the exact startup cwd that had **no** protected input when the run began, only
when the current runtime is already implicitly trusted, only when a protected
input now exists, and only when no exact/inherited saved decision has appeared.
It prevents the next restart from unexpectedly prompting after the current
process has already loaded and potentially executed the newly materialized
resource. File creation alone, automatic rendering, an untrusted startup, or a
reload in another cwd never persists trust.

Pi references:

- `core/trust-manager.ts:54-92` (option/update list)
- `core/project-trust.ts:23-40` (startup prompt)
- `modes/interactive/components/trust-selector.ts:30-77` (saved/current state)
- `modes/interactive/interactive-mode.ts:3435-3452,4378-4427,5363-5371`

## Package and config commands

The later CLI integration slice applies the same trust resolver to `install`,
`remove`, `list`, and `config`; `-l` / `--local` writes are rejected unless the
effective decision is trusted. `--approve` / `--no-approve` are command-local
run overrides and are not persisted. Global package/config writes do not require
project trust, but an untrusted command must not read/list project settings.

Pi's `update` is intentionally different: it never prompts or asks extensions.
It uses only an explicit override or an already saved `true` decision; otherwise
project packages are absent. It does not consult `defaultProjectTrust`. This
rule will land with the separate package-update/config realignment gap so trust
work does not silently change the currently queued update semantics.

Pi references: `package-manager-cli.ts:505-550,553-623,626-766` and
`test/package-command-paths.test.ts:131-317`.

## Extension API pin

### Decision event

| Field | Optionality | Value / behavior |
| --- | --- | --- |
| event `type` | required | literal `"project_trust"` |
| event `cwd` | required | canonical runtime cwd string |
| result `trusted` | required | `"yes"`, `"no"`, or `"undecided"` |
| result `remember` | optional | boolean; only exact `true` persists a yes/no result |
| context `cwd` | required | same runtime cwd |
| context `mode` | required | `"tui"`, `"print"`, `"json"`, or `"rpc"` |
| context `has_ui` | required | whether startup UI is actually callable |
| context `ui` | required | bounded `select` / `confirm` / `input` / `notify` surface; inert prompting when `has_ui` is false |

Callbacks are invoked serially in extension load order. Ownership ends at the
first `yes`/`no`; later handlers are not called. `undecided` never persists even
if it carries `remember: true`. The event runs only on the pre-trust global/CLI
extension set and only when a protected input exists and no CLI override was
given.

The required startup `ui` object is safe in every mode. When `has_ui` is false
or the mode is not interactive, `select` returns `None`, `confirm` returns
`False`, and `input` returns `None` immediately; they do not read stdin, render a
prompt, or write either protocol stream. `notify` may emit a bounded diagnostic
to **stderr** in print/JSON/RPC modes, matching Pi's startup trust context, but
never writes stdout (JSON/RPC stdout remains protocol-only). In interactive mode
the startup selector/input implementations own the live terminal UI. Extensions
must return `undecided` themselves when their UI call yields no selection; an
inert UI call does not manufacture a yes/no result.

### Read API

`ctx.is_project_trusted()` plus the Pi-shaped alias
`ctx.isProjectTrusted()` are zero-argument callbacks returning the runtime's
current boolean. They are available to normal post-start extension contexts;
they do not mutate or re-resolve trust.

Pi references:

- `core/extensions/types.ts:505-527,1165-1171,1589-1594`
- `core/extensions/runner.ts:201-229,666-675`

## Delivery sequence

This design decomposes the remaining track into reviewable gaps:

1. **Trust core + settings/resource gate.** Store, detector, resolver without
   extension ownership, `defaultProjectTrust`, final-cwd startup ordering,
   untrusted settings/resource/package filtering, CLI run overrides, mode
   behavior, and focused conformance tests. Global/CLI inputs and context-file
   exemptions remain usable.
2. **Interactive and management integration.** Startup selector, warning,
   `/trust`, reload/restart semantics, `/settings` enum, and package/config
   command flags. Package `update` realignment remains its own queued gap but
   consumes the saved-decision API.
3. **Extension trust surfaces.** Pre-trust global/CLI activation reuse,
   serial `project_trust` decision ownership, error handling/remember semantics,
   and `ctx.isProjectTrusted()`.

Each slice updates the relevant user/security/settings/extension docs,
`CHANGELOG.md`, parity index/audit/backlog, focused tests and conformance gates,
then passes `just check` and a direct different-family review over its exact
diff.

## Explicitly deferred

- A sandbox, permission prompts for ordinary tools, or restriction of the model
  after startup.
- `.agents/skills` loading (and therefore its ancestor detector) until pipy
  supports that resource source.
- Remote PyPI/npm package sources, lifecycle scripts, and automatic installation
  of missing packages.
- Bare-update / `--all` semantic realignment, except for consuming the saved
  trust decision in that separate gap.
- Unrelated extension hooks (`before_provider_headers`, `agent_settled`, durable
  entry renderers) and dynamic tool loading.

## Done when

The design has a direct different-family CLEAN review; it pins the trust-store
schema and ancestry, every protected/exempt source, final-cwd loading order,
mode/default/override matrix, selector and reload semantics, package/config
rules, extension event fields/ownership, read callback arity, pipy boundary
mapping, and a multi-slice delivery order without implementing runtime behavior.
