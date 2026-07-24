# Architecture quality improvement program

Status: active implementation program. Review outcomes are recorded in the
coordinator ledger and commit history.

Date: 2026-07-24.

## Purpose

The Phase 0–7 architecture migration and quality burn-down created real
headless agent, coding-session, provider, extension, session, automation, and UI
boundaries. This follow-on does not reopen that migration or redesign the
product. It addresses the concentrated debt found by the 2026-07-24 comparative
audit against local Tau and Pi:

- extension reload is assembled through duplicated mutable projections rather
  than one validated generation;
- the built-in command interpreter and outer REPL step still contain
  application logic behind very wide collaborator lists;
- strict Mypy coverage is scoped rather than complete across `src`;
- the one-shot native runtime and canonical interactive agent loop have an
  undocumented semantic overlap;
- the terminal UI remains a large façade over editor, overlay, extension
  chrome, and frame state;
- repeated PTY timing flakes weaken confidence in otherwise strong terminal
  coverage;
- living architecture, backlog, package metadata, and Pi-gap documents lag the
  code; and
- formatting is not yet a repository gate.

The program is complete only when the structural slices below are landed,
living documentation describes the resulting code, and a fresh subagent-based
comparison against the then-current Tau and Pi worktrees has been recorded.

## Measured baseline

At `ba5d030`:

- `src`: 77,982 Python lines; tests: 112,562 Python lines;
- configured `just lint` and `just typecheck`: clean;
- final full gate: 4,585 passed, 2 skipped;
- repository C901: 39 findings in 13 pinned files; `src`: 23 in four files;
- `src` typing suppressions: one;
- diagnostic `mypy --strict src`: 144 errors in 41 files;
- `native/tool_loop_session.py`: 5,085 physical lines;
- `_BuiltinCommandInterpreter.interpret`: complexity 97, 861 lines, 35
  parameters;
- `_ReplLoopStep.step_once`: complexity 43, 559 lines, 37 parameters;
- `native/tui.py`: 7,017 physical lines;
- `ToolLoopTerminalUi`: audit estimate of 129 fields and 195 methods; Slice 1
  adds the reproducible metric that establishes the authoritative field
  baseline `B`;
- `ruff format --check .`: 261 files would be reformatted; and
- the Pi gap audit targets Pi 0.80.6 while the local reference is 0.82.0.

These are comparison baselines, not quotas that justify cosmetic splitting.

## Program invariants

Every slice must preserve:

1. CLI text, JSON/RPC schemas, provider wire requests, session formats, event
   ordering, extension contracts, TUI behavior, and command precedence unless a
   slice explicitly names and tests a product change.
2. The private full-content native product session tree and the metadata-only
   workflow archive as separate trust/privacy domains.
3. Fail-closed project trust, path containment, tool admission, provider auth,
   and extension activation behavior.
4. Catalog-driven provider construction and the existing one-way import
   boundaries.
5. No new runtime dependency, unchecked `Any`, unexplained suppression, C901
   pin, or Mypy exclusion.
6. Direct work on `main`, one independently green slice at a time, with
   superseded code deleted in the same slice.

Large-file or complexity reduction is accepted only when a cohesive state,
effect, or ownership boundary becomes independently testable.

## Slice protocol

For the plan and every implementation slice:

1. Start from a clean tracked worktree.
2. Add or strengthen characterization before moving behavior when the boundary
   is not already pinned.
3. Implement one bounded ownership change.
4. Update the living architecture/backlog and relevant user or developer docs
   in the same slice.
5. Run focused tests, `just check`, and `just docs-build`. PTY-affecting slices
   also run `just test-pty-smoke` and the focused real-PTY module.
6. Run the mandatory Pi review loop with
   `openai-codex/gpt-5.6-sol` at high reasoning until explicit CLEAN, with at
   most three review rounds. Fix repository-wide instances of any reported
   stale pattern in one pass.
7. Commit the clean slice directly to `main` with a distinct repository-style
   subject. Never amend, rebase, reset, or push.

Formatting-only sub-slices 15a/15b are the sole exception to item 4: they
contain only formatter output. Slice 15c is the non-formatting bookkeeping and
gate commit that updates the backlog/contributor docs for the complete
formatting series. The ledger still records all three commits in 15c.

If a slice cannot reach a green gate and CLEAN review after two implementation
attempts or three review rounds, stop and report the blocker rather than
weakening an invariant.

## Ordered slices

### Slice 0 — reviewed program plan

Land this document after Pi reviews the scope, ordering, invariants, acceptance
criteria, and feasibility.

Acceptance:

- Pi returns explicit CLEAN over the final plan diff;
- the plan distinguishes architectural work from product parity;
- every later slice has a measurable exit condition; and
- no production code changes.

### Slice 1 — restore living documentation truth

Refresh the documents operators and contributors actually enter through:

- rewrite `docs/architecture.md` around the current canonical agent,
  coding-session, provider, extension, product-session, automation, TUI, and
  metadata-archive boundaries;
- mark `docs/architecture-migration.md` as completed historical evidence and
  link to the concise living overview;
- replace stale `IN PROGRESS` and historical “Next Slice” signals in
  `docs/backlog.md` with this program's active queue;
- refresh `docs/pi-mono-gap-audit.md` against the exact current local Pi commit
  and classify new deltas without implementing them;
- update the package description and documentation index to describe the native
  coding-agent product accurately; and
- add a read-only `scripts/architecture_metrics.py --json` helper that reports
  physical lines, C901 totals, and the `ToolLoopTerminalUi` state-field
  inventory. A state field is one unique attribute declared by a class-body
  annotation or assigned/deleted through `self.<name>` in that class's methods;
  `ClassVar` declarations are excluded;
- keep historical measured ledger rows intact rather than rewriting their
  contemporaneous facts.

Acceptance:

- no current page says completed strict modules remain non-strict;
- no historical completed slice is advertised as the current target;
- the Pi comparison commit/version is current and reproducible;
- architecture diagrams match executable import directions; and
- the metrics helper records the exact baseline field set and count `B` in the
  slice ledger;
- `just docs-build` and `just check` are clean.

### Slice 2 — canonical session-extension generation

Remove the mirrored extension contribution fields from `_RunControlState`.
Represent the live session extension state through one typed generation holding
the activated runtime and parsed flag values. Consumers read commands, hooks,
outboxes, renderers, providers, tools, shortcuts, and flags from that generation
instead of separately copied fields.

This is a shape-only prerequisite: initial activation and reload order remain
unchanged.

Acceptance:

- `_RunControlState` no longer duplicates the contribution members already
  owned by `_ExtensionRuntime`;
- outbox list identity and all reload-visible late binding remain unchanged;
- import direction stays cycle-free; and
- extension lifecycle, tool, provider, renderer, reload, RPC, and TUI focused
  suites pass.

### Slice 3 — transactional extension reload commit

Turn `/reload` into a staged live-generation replacement:

1. reload settings and discover package/resources;
2. activate a candidate runtime against a staging host whose registries,
   outboxes, chrome requests, and listeners are not connected to live
   consumers;
3. parse candidate extension flags and build all fallible derived provider,
   tool, renderer, command-discovery, emitter, and chrome projections;
4. reject and dispose the candidate staging host without changing or delivering
   anything to the live generation when validation fails; and
5. publish through one shared `SessionExtensionGenerationRef`. Every command,
   tool, provider, renderer, emitter, and UI bridge snapshots that reference
   once at the start of an operation and reads all extension-owned state from
   that snapshot; no consumer retains a separately refreshed contribution map.
   After all fallible preparation succeeds, commit is one pointer replacement
   under the session's existing synchronization boundary.

Activation diagnostics may still report a disabled individual extension as
today. Arbitrary external side effects performed directly by trusted extension
module code are outside an in-process transaction and must be documented as
such; pipy-owned activation APIs expose only the staging host until commit.
Candidate-wide validation failure must not mix old flags with new
commands/hooks/tools/providers or deliver candidate outboxes/UI requests.
Outbox delivery and terminal chrome reconciliation occur only after the pointer
swap, as idempotent notifications derived from the committed snapshot. They are
not rollback participants and cannot expose candidate state before commit; a
presentation failure reports the existing fail-soft diagnostic while the
semantic generation remains wholly new.

Acceptance:

- a candidate flag error retains the complete prior generation;
- rejected candidate outboxes, listeners, and UI/chrome requests are disposed
  without delivery;
- an injected failure at each candidate-build step leaves the prior generation
  pointer and every consumer unchanged;
- operation-boundary probes across commands, tools, providers, renderers,
  emitters, and UI observe either one old generation ID or one new generation
  ID, never a mixture, on both successful and rejected reloads;
- removed/disabled extension chrome is cleared only inside a successful commit;
- provider fallback, active-tool filters, custom-message delivery, and
  lifecycle hook order remain characterized; and
- tests prove successful replacement and failure rollback as whole-generation
  behavior.

### Slice 4 — session command effect family

Extract the session-owned built-in effects from
`_BuiltinCommandInterpreter`: status/name/new/tree/resume/fork/clone/compact and
their session-switch gates. Keep built-in/resource/extension precedence in the
headless coding controller. Use a narrow typed collaborator bundle rather than
passing the whole session or run context.

Acceptance:

- session command behavior, diagnostics, persistence, prefill, branch rebuild,
  and extension gate ordering are unchanged;
- the new executor has no provider, settings, package, or unrelated TUI
  dependency; and
- the root interpreter loses the complete session command branch family.

### Slice 5 — provider and configuration command effect family

Extract model selection/cycling, login/logout, settings, scoped models,
hotkeys/changelog/copy, project trust, and provider mutation effects into
cohesive typed executors. Preserve the terminal versus captured-stream
presentation split.

Acceptance:

- no command runs an unintended provider turn or tool call;
- model/auth changes retain context clearing, fallback, footer, persistence,
  and catalog refresh timing;
- trust remains fail-closed; and
- the root interpreter no longer receives provider/settings/keybinding
  collaborators individually.

### Slice 6 — transfer, package, and reload command families

Extract import/export/share, package/update-related local effects, active-tool
changes, and the now-atomic reload operation. Reduce
`_BuiltinCommandInterpreter` to closed action routing over typed effect-family
ports; keep unknown/impossible action handling explicit.

Acceptance:

- `interpret` complexity falls below 20 and its direct parameter count below
  10;
- export/import/share bytes, path policy, session mutations, package trust, and
  reload behavior remain unchanged;
- no generic catch-all callable or untyped context object replaces the current
  explicit contracts; and
- the C901 finding leaves the root if the file is otherwise clean enough for
  that function, without moving the branch chain intact.

### Slice 7 — strict extension and public-facade typing

Extend the enumerated strict-equivalent Mypy gate through the extension value,
UI, runtime, hook, discovery, and public façade modules. Make intended exports
explicit at their owning modules rather than depending on incidental imports.

Acceptance:

- the selected extension surface is strict-clean;
- public names continue to resolve from documented import paths;
- no cast or suppression hides unchecked `Any`; and
- extension fake-host, loader, API, lifecycle, UI, and import-boundary tests
  remain green.

### Slice 8 — strict native support modules

Add the remaining native leaf/support modules to strict coverage in bounded
batches grouped by owner: session-tree/resources/settings/packages, terminal
support/rendering, provider catalog/construction helpers, and product policy
leaves. A batch may cover at most one package or three root modules so failures
stay reviewable.

Acceptance:

- every selected module is added to the ratchet in the same commit that makes
  it clean;
- no global `strict = true`, exclusion, suppression, or relaxed sub-flag; and
- each batch independently passes the full gate and Pi review.

This numbered slice may therefore land as `8a`, `8b`, and so on; the coordinator
records the exact module inventory before each sub-slice.

### Slice 9 — strict source frontier completion

Finish the remaining `pipy_harness` and `pipy_session` source modules, then
replace the long module inventory with package-wide strict-equivalent patterns
that apply to all source packages but not the top-level test modules.

Acceptance:

- diagnostic `mypy --strict src` is clean;
- `just typecheck` still checks both `src` and `tests`;
- tests remain under their existing baseline unless separately tightened;
- only the single documented runtime-selected stdlib subclass suppression
  survives; and
- CI enforces the complete strict source frontier.

### Slice 10 — one-shot runtime convergence decision

Characterize the overlap and intentional differences between
`NativeAgentSession` (one-shot CLI/SDK) and the canonical headless agent loop.
Record an ADR-level decision:

- if overlapping provider/tool/event semantics are equivalent, route the
  one-shot adapter through the canonical loop with a one-turn policy and delete
  the superseded pipeline; or
- if the proposal/apply and metadata-run contract is intentionally distinct,
  name it as a compatibility runtime, define the boundary, and remove semantic
  duplication by reusing canonical provider-turn/tool executors where their
  contracts match.

Acceptance:

- the decision is based on executable equivalence/contract tests;
- two implementations no longer silently claim the same semantics;
- one-shot SDK/CLI output and metadata archive behavior remain unchanged; and
- the living architecture identifies the chosen ownership.

### Slice 11 — editor state extraction

Keep `ToolLoopTerminalUi` as the product façade, but move editable buffer,
cursor, selection, history, undo/redo, paste, completion, and queued-input state
into a typed editor-state owner with pure transitions where practical.

Acceptance:

- keybinding and edit behavior remain byte-for-byte/keystroke equivalent;
- `read_line` delegates editor transitions rather than mutating parallel fields;
- editor state is testable without a real terminal; and
- `scripts/architecture_metrics.py --json` reports at most `B - 24`
  `ToolLoopTerminalUi` state fields after this slice; and
- focused editor, completion, image-paste, history, workflow, and PTY tests pass.

### Slice 12 — overlay and extension-chrome state extraction

Move selector/dialog/overlay stack state and extension header/footer/widget/title
state behind cohesive owners. Preserve the existing extension UI bridge and
terminal façade.

Acceptance:

- model/settings/tree/trust/session overlays retain navigation, resize, cancel,
  and repaint behavior;
- extension chrome replacement and clearing are generation-consistent;
- `scripts/architecture_metrics.py --json` reports at most
  `floor(B * 0.70)` `ToolLoopTerminalUi` state fields after this slice, a
  cumulative reduction of at least 30%; and
- no alternate-screen or framework dependency is introduced.

### Slice 13 — pure frame composition

Extract the live frame's content/layout/style calculation into a pure renderer
over immutable snapshots. Terminal writes, cursor control, locks, and restoration
stay in the terminal driver/UI façade.

Acceptance:

- frame rows, clipping, wrapping, styling, footer pinning, cursor placement, and
  native scrollback behavior remain exact at characterized terminal sizes;
- paint failure bookkeeping and deferred flush/coalescing remain unchanged;
- at least two TUI C901 findings are eliminated by the snapshot/render
  ownership, with no increase in repository or TUI finding counts; and
- captured, PTY, and terminal-screen comparison gates pass.

### Slice 14 — deterministic PTY synchronization

Replace known load-sensitive readiness races with observable state/byte
handshakes. Do not increase sleeps or timeouts as the primary fix and do not
weaken real-PTY coverage.

Acceptance:

- identify each previously documented full-suite PTY flake and its race;
- each formerly flaky focused case passes 20 consecutive isolated runs and 10
  consecutive runs in its containing PTY module or smallest representative
  group;
- `just test-pty-smoke` passes five consecutive times on the local platform;
- one complete `just check` run passes after those stress runs;
- CI retains Linux and macOS PTY jobs; and
- terminal state/theme hygiene is restored after tests.

### Slice 15 — repository formatting baseline and gate

Normalize Ruff formatting in reviewable mechanical batches, separated from
behavioral changes, then add `ruff format --check .` to the local and CI quality
gate.

Acceptance:

- no batch mixes semantic edits with formatting;
- Pi receives the complete batch diff without scoped/truncated review;
- generated/fixture files are excluded only with explicit rationale;
- final `ruff format --check .` is clean; and
- contributor docs name the formatter command.

This slice may land as `15a`–`15c` by directory to stay within review bundle
limits. Slices 15a and 15b contain formatter output only and do not update the
backlog. Slice 15c enables the local/CI gate and records all formatting commits
in the backlog and contributor documentation.

### Slice 16 — final documentation, disposition, and fresh comparison

Refresh the living architecture and backlog with measured after-values. Run
three fresh read-only subagent audits:

1. pipy internal architecture/quality;
2. pipy versus current local Tau; and
3. pipy versus current local Pi.

Cross-check their claims against the code, record the resulting assessment in a
dated document, and update the next queue without reopening completed work.

Acceptance:

- before/after measurements cover complexity, strict typing, suppressions,
  composition/TUI state, tests, PTY reliability, and formatting;
- every intentional residual is individually justified;
- the comparison records exact Tau/Pi commits;
- `docs/architecture.md`, `docs/backlog.md`, package metadata, and the dated
  assessment agree; and
- final `just check`, `just docs-build`, and Pi review are CLEAN.

## Explicit non-goals

This program does not itself implement:

- Pi 0.82 provider/product deltas such as constrained sampling, Kimi or
  OpenRouter OAuth, parallel tool execution, new provider catalog rows, or RPC
  bash streaming;
- a public multi-turn SDK, package split, reusable `pi-tui` API, Textual,
  Pydantic, or another runtime framework;
- remote PyPI/npm package execution;
- alternate-screen TUI behavior;
- changes to privacy/trust policy; or
- aggregate line-count or C901 reductions without an ownership improvement.

The refreshed Pi audit may queue those product gaps after this program, but they
must not expand an architecture slice.

## Coordinator ledger

For each landed slice, append to `docs/backlog.md`:

- commit hash and subject;
- primary ownership change;
- focused and full verification results;
- before/after metrics relevant to the slice;
- Pi review rounds/findings and explicit CLEAN; and
- any intentionally deferred residual with rationale.

The final report lists every commit, aggregate review statistics, final
measurements, documentation changed, and the exact next slice if the program
stops early.
