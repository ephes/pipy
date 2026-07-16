# Project trust core and settings/resource gate — execution plan

Status: implemented runtime slice; direct different-family plan review CLEAN.

Reviewed parent design:
[`2026-07-15-project-trust-design.md`](2026-07-15-project-trust-design.md).
Ordered track plan:
[`2026-07-15-project-trust-implementation-plan.md`](2026-07-15-project-trust-implementation-plan.md).
Pi reference: `/Users/jochen/src/pi-mono` at `b084d2fb`, especially
`packages/coding-agent/src/core/trust-manager.ts`,
`project-trust.ts`, `settings-manager.ts`, `resource-loader.ts`, and
`packages/coding-agent/src/main.ts`.

## Single-slice scope

Implement only slice 1 from the reviewed track plan: the trust store and
protected-input detector, trust-aware settings, provenance-specific resource
gating, and the core startup resolver through the final runtime cwd. This slice
also adds the project-trust conformance gate and updates user/parity/release
documentation.

The resolver order is pinned to: last CLI override (`--approve` / `-a` or
`--no-approve` / `-na`) → no protected project inputs → closest saved decision
→ global-only `defaultProjectTrust` → an injected interactive selection callback
or headless false. Extension-owned `project_trust` decisions are not in this
slice, so the future extension step is intentionally absent between the
no-resource and saved-decision steps.

Interactive selector rendering, `/trust`, reload auto-persist, package/config
command integration, pre-trust extension reuse, the extension decision event,
and `ctx.isProjectTrusted()` remain later slices. The callback seam is included
only so the core resolver and startup ordering are testable; normal product
startup fails closed when no selector has been supplied. Until slice 2 adds the
full selector and warning, interactive-TTY startup with protected inputs and an
unresolved `ask` decision emits one concise stderr-only diagnostic that names
the disabled project inputs and the interim operator choices (`--approve`,
global `defaultProjectTrust: "always"`, or a saved `trust.json` decision).
Non-UI startup — including print, JSON, RPC, help, list-models, and piped input —
remains silent on both stdout and stderr for this decision, as pinned by the
parent design. Trust remains an input loading guard, not a sandbox.

## Pinned data and behavior

- `trust.json` is an object keyed by canonical absolute directories. Values are
  required booleans for persisted decisions; `null` is accepted when reading
  existing data but ignored by lookup, and a `None` update deletes the key.
  Other root/value shapes fail closed. Closest canonical ancestor wins.
- Writes re-read under the existing bounded sidecar lock pattern, update all
  entries atomically, serialize sorted keys with a trailing newline, and keep
  the directory/file owner-private. A malformed or unreadable store is reported
  and never overwritten.
- Protected entries under the final cwd's `.pipy` are `settings.json`,
  `extensions`, `skills`, `templates`, `commands`, `SYSTEM.md`, and
  `APPEND_SYSTEM.md`. A bare `.pipy` directory and `AGENTS.md` / `pipy.md` do not
  require trust. Unsupported `.agents/skills` and standalone `.pipy/themes`
  discovery are not added; project-package themes are gated through project
  settings.
- `defaultProjectTrust` is optional and global-only. Accepted strings are
  `ask`, `always`, and `never`; absent, invalid, or project-only values resolve
  to `ask`. This field has no upstream API default: Pi forces `ask`, and pipy
  follows it.
- An untrusted `SettingsManager` never opens project settings, exposes an empty
  project scope, clears project values/errors on transition, and rejects a
  project write before touching disk. Global/base/override precedence remains
  unchanged.
- Source gating is by provenance. Untrusted startup excludes default workspace
  `.pipy` extensions, skills, templates, commands, system prompts, and
  project-scoped package declarations (including their themes). It retains
  global defaults, global packages, and explicit CLI paths even when the
  matching `--no-*` flag disables
  defaults. Context-file loading remains independent.
- Trust is resolved only after startup session selection determines the final
  cwd and before settings, packages, extensions, resources, themes, catalog
  contributions, model selection, or provider construction can observe project
  state. Print, JSON, RPC, help, and list-model paths never prompt or read stdin;
  unresolved `ask` is false and protocol stdout remains clean.

## Ordered implementation tasks

1. Add `native/project_trust.py` and focused store/detector tests. Acceptance:
   ancestry, deletion fallback, canonical aliases, strict schema, malformed-file
   preservation, atomic/private writes, contention behavior, and the complete
   protected/exempt detector table pass.
2. Extend `SettingsManager` with a `project_trusted` state, transition/reload
   support, project-write refusal, a global-only default-trust accessor, and
   tests proving an invalid project file is not opened while untrusted.
3. Split loader defaults by workspace/global/package/explicit provenance and
   thread trust through package composition, extensions, skills/templates,
   commands, project-package themes, and system-prompt inputs. Add a shared
   fixture matrix that proves all workspace sources are absent while global/package/explicit sources
   remain, including explicit-plus-`--no-*` cases and unchanged context files.
4. Add the core resolver and sequential trust CLI flags to explicit `repl` and
   top-level Pi-shaped dispatch. Reorder startup around the final session cwd,
   then build settings/resources/catalog/provider exactly once from that state.
   Test override precedence/no persistence, no-resource short circuit, saved
   ancestor precedence, global defaults, injected interactive selection,
   headless mode behavior, protocol purity, help/list-models, and cross-project
   resume. Cover the interim unresolved-`ask` diagnostic on interactive-TTY
   stderr, prove it is absent from every non-UI stdout and stderr path, and
   remove or replace it when slice 2 lands the full interactive
   selector/warning.
5. Add `scripts/parity_checks/project_trust_conformance.py --json`, wire the
   deterministic product-level gate into normal validation if appropriate, and
   update settings/security/customization/user docs, `CHANGELOG.md`,
   `docs/pi-parity.md`, `docs/parity-plan.md`, `docs/pi-mono-gap-audit.md`, and
   `docs/backlog.md`. Mark only this runtime slice shipped; keep slices 2–3
   explicitly queued.

## Done when

- Focused project-trust, settings, resource, startup, and conformance tests pass.
- `just check` and `prek run --all-files` (only if configured) pass.
- The complete code, tests, docs, and release-note diff receives a direct fresh
  different-family `CLEAN` verdict (Claude/Opus for this GPT-family
  implementation) with no material review omissions.
- The exact reviewed diff is committed on `main`; nothing is pushed.
