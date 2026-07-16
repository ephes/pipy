# Project-trust extension surfaces — design plan

Status: implemented project-trust slice 3; direct Claude Opus plan review
`CLEAN` on round 2 (2026-07-16).

Gap sources: `docs/pi-mono-gap-audit.md` priority 2 and `docs/backlog.md`
"Next: extension-owned project-trust decision/read APIs". This is exactly
slice 3 from
[`2026-07-15-project-trust-implementation-plan.md`](2026-07-15-project-trust-implementation-plan.md);
the shipped core/settings/resource gate and interactive/management behavior are
not reopened.

Pi reference checkout: `/Users/jochen/src/pi-mono` at `b084d2fb`.

## Scope

Add the missing extension-owned trust seam end to end:

1. activate only global, explicit CLI, and process-inline/test extension inputs
   before a decision is required, then reuse those exact activation instances
   in the post-decision runtime;
2. emit the serial `project_trust` decision event before saved/default/UI
   fallback and honor first-decision, error, and persistence semantics; and
3. expose the resolved run-local boolean through zero-argument snake_case and
   Pi-shaped camelCase read callbacks on every normal extension context.

The slice does not change trust-store ancestry, protected-input detection,
selector choices, package/config behavior, or the model/tool sandbox posture.
It does not add unrelated extension events or package-update realignment.

## Pi behavior to match

### Startup activation and reuse

Pi forces the bootstrap settings manager to untrusted, resolves only global and
temporary CLI extensions (plus inline factories), and passes that result into
`resolveProjectTrusted`. After the decision, `loadFinalExtensionSet` reuses
preloaded extensions by canonical resolved path, activates only remaining
project/project-package paths against the same runtime, and rebuilds final
extension order from the final path list. A pre-trust load failure is not
retried in the same startup.

Pipy has no public inline-extension-factory surface today. Direct tests may
inject a prepared activation batch, but this slice will not invent a new user
API merely to mirror Pi's test factory input.

Pi references:

- `core/resource-loader.ts:330-355,492-556`
- `main.ts:604-678`

### Decision event field and ownership pin

| Surface | Field | Optionality | Exact value / behavior |
| --- | --- | --- | --- |
| event | `type` | required | literal `"project_trust"` |
| event | `cwd` | required | canonical final runtime cwd string |
| result | `trusted` | required | `"yes"`, `"no"`, or `"undecided"` |
| result | `remember` | optional | only the exact boolean `True` persists a yes/no result |
| context | `cwd` | required | same canonical runtime cwd |
| context | `mode` | required | `"tui"`, `"print"`, `"json"`, or `"rpc"` |
| context | `has_ui` / `hasUI` | required | true only when startup dialog UI is callable |
| context | `ui` | required | bounded `select`, `confirm`, `input`, and `notify` |

Handlers run serially in final pre-trust extension load order, including
multiple handlers registered by one extension. `undecided` continues. The first
valid `yes` or `no` stops all later handlers and owns the decision. A handler
exception yields a safe extension-labelled warning and continues. A malformed
return is treated as a handler error and continues rather than becoming truthy.
`remember is True` writes the exact cwd only after a valid yes/no; it has no
effect on `undecided`.

The event is reached only after the existing CLI-override and no-protected-input
rungs. Therefore both rungs suppress pre-trust activation and event dispatch;
saved decisions and global defaults remain later rungs and do not suppress it.

Pi references:

- `core/project-trust.ts:42-99`
- `core/extensions/runner.ts:201-229`
- `core/extensions/types.ts:505-527`

### Startup UI and mode pin

The trust context owns a startup-only UI object, not a normal live-session
context. In headless use, `select(...)` returns `None`, `confirm(...)` returns
`False`, and `input(...)` returns `None` immediately without reading stdin or
writing stdout. `notify(...)` may write a bounded diagnostic to stderr only.
For `mode="tui"`, `has_ui` is true only for an actual interactive startup TTY;
a piped interactive REPL still receives `mode="tui"` with inert UI. JSON/RPC
stdout remains protocol-only.

Pipy's existing synchronous extension callback contract remains synchronous;
an awaitable handler result is driven through the existing bounded awaitable
bridge before validation.

### Read callback pin

Normal post-start command, shortcut, lifecycle, input, tool, provider, and
session-gate contexts receive:

- `ctx.is_project_trusted()` — zero arguments, returns the captured run-local
  boolean; and
- `ctx.isProjectTrusted()` — zero arguments, exact alias.

Both callbacks are read-only. They never inspect the store, re-run resolution,
or persist a decision. Their lifetime is the context invocation; reload builds
new contexts from the still-current `SettingsManager.project_trusted` value.
The settings manager/runtime owns the boolean, not the extension.

Pi references:

- `core/extensions/types.ts:319-321,1589-1594`
- `core/extensions/runner.ts:666-675`

## Pipy design

### 1. Reusable activation batch

Move initial extension activation ahead of provider-catalog construction and
represent it as one internal activation batch containing:

- the ordered descriptor identity (`entry_path`, canonicalized for matching);
- each activation outcome and registered contributions/hooks;
- the shared user/custom-message outboxes; and
- enough per-extension staged state to finalize the batch once without
  re-importing or re-running `activate(api)`.

Pre-trust discovery uses the untrusted bootstrap `SettingsManager`, global
package roots only, explicit CLI paths, `include_workspace_defaults=False`, and
the normal `--no-extensions` plus global extension-pattern rules. Discovery
must not stat, parse, or import project/project-package extension paths.

When trust resolves, perform final discovery with the resolved settings and
package roots. Rebuild the batch in that final descriptor order: reuse matching
preloaded outcomes by canonical entry path; retain failed preloads as failed;
activate only new paths; and finalize command/tool/provider/shortcut/flag/
renderer collision checks exactly once against the complete final reserved and
taken sets. Activation-time messages remain staged until that final validation,
so an extension disabled by a final collision contributes nothing. The provider
catalog and `NativeToolReplSession` consume the same finalized batch. The
session's later explicit `/reload` continues to build a fresh batch once, as it
does today.

This closes the current hidden double-activation path in which catalog
construction and the live session separately import the same extension. It
also prevents a pre-trust instance from being imported a second time merely to
join the final runtime.

### 2. Resolver callback boundary

Extend `resolve_project_trust` with an optional extension-decision callback
between the no-protected-input and saved-store rungs. The generic trust module remains
independent of extension imports: it receives a callback returning a validated
yes/no/undecided result plus diagnostics, applies exact-`True` persistence, and
records a distinct `extension` resolution source. The CLI startup coordinator
owns discovery/activation, constructs the mode-aware trust context, and supplies
that callback only when the event rung is reachable.

### 3. Context construction

Add immutable `ProjectTrustEvent`, `ProjectTrustDecision`, and
`ProjectTrustContext` value objects in the extension runtime, plus a dedicated
dispatcher over the pre-trust batch. Reuse `_CollectingUi`'s inert
select/confirm/input behavior, but provide a startup notify sink that writes
only safe bounded text to the passed error stream. Do not reuse a normal command
context: the startup context exposes only the pinned fields and four UI methods.

Thread `project_trusted` into `_CommandContext` construction through every
dispatcher. Give it a callable captured from the active settings manager so
all contexts in TUI/print/JSON/RPC and after `/reload` report the same run-local
state without store access.

## Tests and objective gate

Add focused tests that prove:

1. project and project-package module sentinels do not execute before approval;
2. global and CLI module sentinels execute exactly once across trust, catalog,
   and live-session construction, including a trusted final merge;
3. final descriptor order and first-wins collision behavior are unchanged, and
   a failed pre-load is not retried;
4. `undecided` continues, first yes/no stops later handlers, async handlers are
   awaited, exceptions/malformed results warn and continue, and only exact
   `remember=True` on yes/no persists the exact cwd;
5. CLI override and no-protected-input resolution do not activate the pre-trust set or
   emit the event, while saved/default decisions remain after it;
6. headless select/confirm/input return immediately without touching stdin or
   stdout, and notify is stderr-only in print/JSON/RPC product paths; and
7. both zero-argument trust callbacks return the same boolean, reject positional
   arguments, and have no persistence side effects across command, shortcut,
   lifecycle, input, tool-call/result, provider-request, and session-gate
   context constructors; product-path assertions cover TUI, print, JSON, and RPC
   modes.

Extend `scripts/parity_checks/project_trust_conformance.py --json` with a
product-path extension section rather than adding a source-substring-only gate.

## Documentation and release surface

Update `docs/extension-api.md` from planned to shipped, mark slice 3 complete in
the reviewed implementation plan, and close the project-trust track in
`docs/pi-mono-gap-audit.md`, `docs/backlog.md`, `docs/parity-plan.md`,
`docs/pi-parity.md`, and `CHANGELOG.md`. User/security/settings docs change only
where they still say extension decisions are deferred.

## Done when

Exactly this slice is complete when pre-trust global/CLI extensions can own a
trust decision without any project code executing, their instances are reused
through catalog and session startup, the first valid decision and exact remember
semantics match Pi, every normal extension context exposes both read aliases,
headless protocol streams stay clean, focused tests and the extended objective
gate pass, `just check` (plus `prek` if configured) is green, the complete
code/docs diff receives a direct fresh-context Claude Opus `CLEAN`, and the gap
is committed on `main` without pushing.
