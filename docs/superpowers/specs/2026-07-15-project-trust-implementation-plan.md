# Project trust — implementation plan

Status: slices 1 (trust core and settings/resource gate) and 2 (interactive and
management integration) shipped 2026-07-15; slice 3 remains.

Reviewed design:
[`2026-07-15-project-trust-design.md`](2026-07-15-project-trust-design.md).
Pi reference: `/Users/jochen/src/pi-mono` at `b084d2fb`.

The reviewed design intentionally divides project trust into three future
parity-loop gaps. Each slice below must land independently with its own tests,
docs, full `just check`, direct different-family CLEAN review, and commit.

## Slice 1 — trust core and settings/resource gate

This is the next selected runtime gap. It establishes the security boundary
without TUI selectors or extension-owned decisions.

### Task 1: trust store and protected-input detector

Add a pipy-owned module (for example `native/project_trust.py`) containing:

- canonical cwd normalization;
- closest-ancestor `ProjectTrustStore.get_entry()` / `get()`;
- lock-guarded atomic `set_many()` with null-as-delete semantics;
- strict root/value validation and safe diagnostics; and
- `has_trust_requiring_project_resources(cwd)` for the reviewed `.pipy`
  settings/resource/system-prompt list, including `.pipy/commands` and excluding
  unsupported `.agents/skills`.

Acceptance criteria:

1. Exact child decisions override parent decisions; deleting a child reveals
   the parent.
2. Symlink/canonical aliases resolve to one key.
3. The store accepts boolean/null input, rejects every other value/root shape,
   never overwrites a malformed file, writes sorted keys, and uses private
   atomic storage.
4. A bare `.pipy` directory does not trigger trust; each reviewed protected
   entry does; ordinary context files do not.

Focused tests: new `tests/test_native_project_trust.py` with store ancestry,
schema, concurrency/atomicity, canonicalization, permission, and detector table
coverage.

### Task 2: trust-aware settings manager

Extend `SettingsManager` with `project_trusted`, defaulting to the current
trusted behavior for direct callers until startup passes an explicit state.
When false, do not open or parse project settings and expose an empty project
scope. Add a typed global-only `get_default_project_trust()` returning
`ask|always|never` with invalid/missing values mapped to `ask`. Refuse all
project-scope writes while untrusted.

Acceptance criteria:

1. An invalid project settings file produces no parse diagnostic while
   untrusted because it is never read; the same file diagnoses when trusted.
2. Switching false clears project values/errors; switching true loads them.
3. Base/global/CLI override precedence is unchanged.
4. A project `defaultProjectTrust` value cannot influence resolution; only the
   global scope can.
5. Project writes fail before creating or modifying `.pipy/settings.json`.

Focused tests: extend `tests/test_native_settings.py` for load omission,
transition, enum fallback, global-only ownership, and write refusal.

### Task 3: source-specific resource gating

Split the current mixed `include_defaults` discovery switches so callers can
independently include workspace, global, package, and explicit CLI sources.
Thread the resolved trust boolean through package composition, extensions,
skills, templates, custom commands, project-package themes, and system-prompt
discovery. Global
packages/resources and explicit CLI paths remain enabled while workspace
defaults and project package entries are removed when untrusted.

Acceptance criteria:

1. An untrusted fixture containing every protected workspace source contributes
   none of them.
2. In that same run, every global source and explicit CLI source still loads,
   including explicit sources paired with `--no-*`.
3. Project package roots are not resolved/imported; global package roots still
   contribute resources.
4. Project `SYSTEM.md` / `APPEND_SYSTEM.md` are not opened; global/explicit
   prompt inputs retain existing precedence.
5. `AGENTS.md` / `pipy.md` context loading remains unchanged unless
   `--no-context-files` is set.

Focused tests: extend resource/extension/package/system-prompt tests with a
single provenance matrix rather than duplicating one-off cases per loader.

### Task 4: core resolver and final-cwd startup ordering

Add the resolver without extension ownership yet. The order is CLI override →
no protected inputs → saved closest decision → global default → interactive
selection callback or headless false. Wire `--approve/-a` and
`--no-approve/-na` into both the explicit `repl` parser and top-level Pi-shaped
dispatch. Resolve only after session selection yields the final runtime cwd,
then construct settings/resources/catalog/provider from the resolved state.

Acceptance criteria:

1. The last CLI override flag wins, applies only to the run, and does not write
   `trust.json`.
2. No protected inputs yields trusted without store access/prompt.
3. Saved closest decision beats global default; `always`/`never` beat the UI
   fallback; invalid/`ask` is false headlessly.
4. Print/JSON/RPC never read trust input or contaminate protocol stdout.
5. Startup resume into another project resolves trust for the session cwd, not
   the shell's original cwd.
6. No project-derived theme/provider/model/extension state is observed before
   resolution.

Focused tests: parser/top-level dispatch tests plus product-path startup tests
for interactive callback, print, JSON, RPC, help/list-models, and cross-project
resume.

### Task 5: conformance gate and documentation

Add `scripts/parity_checks/project_trust_conformance.py --json` covering store,
resource provenance, mode/default/override matrix, final-cwd ordering, and
context exemption. Update settings/customization/security/user docs,
`CHANGELOG.md`, `docs/pi-parity.md`, and the parity audit/backlog/roadmap.

Acceptance criteria:

1. The new gate is deterministic and exercises product boundaries, not source
   substring checks alone.
2. Docs explicitly say trust is not a sandbox and list every protected/exempt
   source.
3. `just check` includes or independently runs the new gate, all tests pass, and
   the exact code+docs diff receives a direct different-family CLEAN verdict.

## Slice 2 — interactive and management integration

### Task 6: startup selector and untrusted warning

Implement the five reviewed startup choices in the product TUI, including the
parent update pair and session-only options. Cancel resolves false. Render the
post-start untrusted warning only when protected inputs exist.

Acceptance criteria:

1. Real-PTY tests select current, parent, session-only, decline, and cancel at
   80x24 and cover resize/cancel frame recovery.
2. Parent selection writes parent true and deletes the child entry atomically.
3. No prompt text enters the native session tree or metadata archive.

### Task 7: `/trust` and reload semantics

Add the local `/trust` selector with saved/inherited/current state and
restart-required result. Add the exact-cwd manual `/reload` auto-persist
exception from the reviewed design.

Acceptance criteria:

1. `/trust` runs no provider turn/tool call and does not hot-activate resources.
2. Saved current/parent/decline decisions report restart required.
3. A manual reload after a no-resource startup persists exact trust only under
   every reviewed guard; file creation alone and untrusted startup do not.

### Task 8: settings/package/config command integration

Expose the global `defaultProjectTrust` control and apply trust to `install`,
`remove`, `list`, and `config`, including `-l` write refusal and command-local
overrides. Do not bundle bare-update/`--all` semantic realignment.

Acceptance criteria:

1. Untrusted list/config output omits project entries but retains global ones.
2. `--approve` enables a local command without persistence;
   `--no-approve` overrides saved trust.
3. Local writes fail closed with a direct diagnostic; global writes remain
   usable.
4. `update` consumes saved/explicit trust only when its separate realignment
   slice lands; this slice neither prompts nor invokes extension decisions for
   update.

## Slice 3 — extension decision and read APIs

### Task 9: pre-trust extension activation and reuse

Inventory and activate only global/CLI/inline extensions before resolution,
then reuse those instances/runtime while adding trusted project and project-
package extensions after the decision.

Acceptance criteria:

1. Project extension module code cannot execute before approval.
2. Global/CLI module top-level code executes exactly once across both phases.
3. Final extension ordering and collision semantics remain Pi-shaped.

### Task 10: `project_trust` decision event

Add the pinned event/result/context fields and serial first-decision ownership.
Provide inert headless select/confirm/input calls and stderr-only notify.

Acceptance criteria:

1. `undecided` continues; first yes/no stops later handlers; handler errors warn
   and continue.
2. Only exact `remember: true` on yes/no writes the exact cwd.
3. CLI override/no-resource paths suppress the event.
4. JSON/RPC stdout stays protocol-only and headless UI calls never block/read
   stdin.

### Task 11: extension read callback

Expose zero-argument `ctx.is_project_trusted()` and
`ctx.isProjectTrusted()` from the runtime context.

Acceptance criteria:

1. Both names return the same run-local boolean in TUI/print/JSON/RPC contexts.
2. Calls are read-only and cannot trigger resolution or persistence.
3. Extension API docs pin arity, lifetime, and ownership.

## Track completion

The project-trust track is complete only after all three committed runtime
slices are shipped, the conformance gate covers their composition, and the
audit/roadmap no longer describes any project input as loading before a trust
decision. The design-only commit that introduced this plan does not itself
claim runtime parity.
