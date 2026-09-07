# Daily-use harness design: pipy, Pi, and tau

Status: proposed improvement sequence. Initial documentation corrections are
included here; behavioral baseline and runtime implementation have not started.

## Decision

Keep pipy's native runtime and its existing ownership boundaries. Prioritize
context continuity, one reusable product-session control surface, and recovery
over additional provider families or broad feature parity. Most of the necessary
product foundations already exist. The missing piece is consistent, dependable
behavior through those foundations, not another whole-repository decomposition.

The first acceptance target is interactive terminal coding, with a headless
session API that can support automation and extensions without reproducing
product policy. This is a planning assumption, not evidence of successful daily
dogfooding. No new daemon, distributed scheduler, database, or async rewrite is
required by this plan.

## Evidence and limits

This is a source comparison of clean local checkouts on 2026-09-07:

| Repository | Commit | Role in this comparison |
| --- | --- | --- |
| pipy | `6bcc6670578d6f62562a37c8bfc2b1d07dbb4870` | Current implementation, tests, and documented contracts |
| pi-mono | `aa23e784c647d713e775a8adcaf3c219e84f5068` | Mature coding-session composition and daily workflows |
| tau | `f9e21ee60c9903548db0550faaf0a15f16c79a57` | Python harness/session separation and provider-history contracts |

References below are relative to each named repository. This is neither an
exhaustive parity audit nor a live provider benchmark. The July Pi gap audit is
historical; its reload prerequisite and ranking are superseded for this planning
exercise. Its detailed provider gaps remain candidates, not prerequisites.

Pipy's decomposition and transactional reload programs are complete. Current
`scripts/architecture_metrics.py --json` reports 91,749 source Python lines,
142,021 test Python lines, a 336-line coding-session composition root, and a
580-line TUI file (230 class AST lines, five definitions, nine fields). These
are snapshot measurements, not new acceptance limits. Small facades alone do
not establish a reusable API or prove daily usability.

Summary-safe archive searches found earlier architecture and review records,
including a six-round plan review and a separate explicit recommendation to
stop after a clean follow-up. Those are workflow context, not proof that this
design is correct. No raw transcripts were inspected.

## What is already worth keeping

- The provider-neutral `native/agent/` loop, immutable request authorization,
  injected tools/providers, and synchronous canonical events.
- The `native/coding/` state, input, command, and product-session owners; private
  native session trees and the existing terminal resume/fork/compact workflows.
- Extension generations and guarded publication, project trust, tool filtering,
  settings, resource loading, and the existing terminal editor/component owners.
- The separation between full-content product history and the optional,
  metadata-only `pipy_session` workflow archive.
- Strict source typing, import-boundary tests, focused concurrency contracts,
  and PTY coverage. Preserve semantic boundaries; do not add exact file-size or
  method-count gates for the proposed work.

## Findings that change the priority order

| Finding | Current evidence | Consequence |
| --- | --- | --- |
| Compaction loses old task facts from model context | pipy `native/agent/history.py::_agent_history_summary` emits counts and explicitly says details are unavailable; `native/repl/provider_selection.py::apply_compaction` persists that text | Semantic compaction is the first product improvement. The original private transcript remains stored; this is model-context loss, not deletion of the archive. |
| Context pressure is handled mechanically | pipy `native/repl/loop_step.py::_compact_if_needed` uses message/byte thresholds; `native/agent/history.py` cuts user groups | Long sessions and a large single run need model-aware context budgeting and safe request-boundary recovery. |
| Reusable internals lack a public product-session API | pipy `sdk.py::run_native` uses the intentionally separate compatibility runtime; `native/coding/session.py::run` takes streams and composes terminal/REPL input | Embedders cannot obtain the real multi-turn product with a small supported API. |
| RPC is a partial second control authority | pipy `native/automation/rpc.py` owns queues/flags, rejects live model changes, has no compact/session-switch handlers, and only records auto-compaction/auto-retry flags | Accepted command vocabulary must not be counted as implemented behavior. Route controls to one product owner rather than implementing another session engine in RPC. |
| Transient recovery is incomplete | pipy `native/agent/loop_policy.py` enforces zero retries; RPC `abort_retry` is inert | Existing Codex transport recovery does not supply an agent-wide retry lifecycle. |
| Direct RPC bash cannot be aborted | pipy `native/automation/rpc.py::_cmd_abort_bash` refuses a running command; `_cmd_bash` uses `command_sandbox.run_command`, independently of the model's `BashTool` | A responsive input loop is insufficient if the operation cannot stop. Preserve each caller's policy while sharing process lifetime capabilities. |
| Provider-neutral history is narrower than the references | pipy `native/agent/messages.py` retains assistant text and separate tool calls, without an ordered rich-content union or assistant provider provenance | Audit same-provider resume and cross-provider continuation before broadening providers. A schema migration is conditional on demonstrated lost information, not assumed necessary. |

Pi's `packages/coding-agent/src/core/sdk.ts::createAgentSession` exposes product
construction with resource, model, session, and tool injection;
`core/agent-session.ts` owns product control; modes consume that session.
`core/compaction/compaction.ts` uses semantic summarization and context budgeting.
These are the useful design lessons. Pi's newer `packages/agent/src/harness/`
durable operations/lanes are a separate scope and not needed for a local coding
assistant. Pi also retains large product owners: copying its file layout would
not automatically simplify pipy.

Tau's `src/tau_agent/harness.py::AgentHarness` separates its portable harness
from `src/tau_coding/session.py` and the TUI. Its typed blocks in
`src/tau_agent/messages.py` and `tests/test_cross_provider_history.py` are
useful reference contracts. It also has very large coding-session and TUI owners; adopt bounded
interfaces and behavioral tests, not its monoliths or an async architecture just
because it uses one.

## Target ownership

| Layer | Owns | Must not become |
| --- | --- | --- |
| Provider adapters | Wire encoding, capabilities, auth/transport integration, provider replay constraints | Product queues, session lifecycle, terminal policy |
| Canonical agent | One accepted run, provider/tool execution, request authority, canonical events | A CLI, persistent-session manager, or extension implementation |
| Coding session | Active model/history/tree, input admission, compaction, retry policy, lifecycle and control snapshots | Separate mutable state for each frontend |
| Composition/resources | Construct providers, tools, settings, storage and extension generations | A second agent loop |
| TUI, print, JSON, RPC, Python API | Input/output adaptation over the same product operations and events | Alternate implementations of compaction, queues, or model selection |
| Workflow archive | Summary-safe optional learning records | Provider-visible conversational memory |

Keep the synchronous core. A session has one authority for accepting and
settling operations. Existing session/publication locks continue to protect
their documented fields; frontend and worker controls enter those owners rather
than writing parallel state. Provider I/O, extension callbacks, and terminal
painting remain outside shared-state locks. Cancellation, EOF, close, and late
worker completion must converge on the same lifecycle owner. Do not introduce
a universal event bus, generic repository hierarchy, or service locator.

## Ordered implementation slices

This planning change corrects the RPC compaction/toggle, session-switching and
bash claims and the stale completed-program pointers. Slice 0 still owns the
behavioral scenario set, complete support table and observed dogfood baseline;
it must include queue-mode simplifications and timeout/cancellation reporting.

Each row is a bounded outcome, not an instruction to implement the entire table
at once. Produce the detailed contract only when selecting its slice; split a
row further if it cannot be independently tested and reviewed. Existing
ownership and privacy contracts remain authoritative until explicitly changed.

| Order | Slice and scope | Completion evidence |
| --- | --- | --- |
| 0 | Establish a daily-use scenario set and truthful support table. Reuse existing tests and correct documented RPC claims. Record observed setup, coding, resume, compaction, cancellation, and extension failures. | Deterministic tool-backed edit/check/resume scenario plus existing PTY/JSON/RPC tests; a clearly marked live-provider smoke result or explicit untested status. |
| 1a | Semantic compaction at the existing safe user-turn boundary. Extract a bounded summarization service from the existing branch-summary provider path in `native/repl/collaborators.py`; retain goals, decisions, changed files, constraints, and unfinished work, incorporating the previous summary on successive compactions. | Fake-provider contracts prove summary input/output, preservation of task facts across repeated compaction in the next request, failure/cancel leaving active history/tree unchanged, and resume reconstructing the same compacted context. |
| 1b | Model-aware context budgeting and overflow handling through the same service. Budget system text, tools, attachments, history and output reserve; support a safe cut within long runs without orphan tool results. | Small-context and single-long-run scenarios; abort while summarizing; no duplicate user/tool/summary entries; explicit recoverable error when no safe sufficient cut exists. |
| 2a | Define and expose a minimal product-session control facade: construct, submit, observe, cancel, snapshot, close. Reuse current coding/agent owners. Establish ownership and thread-entry rules, not a generic command framework. | A two-turn Python test with tool execution, events, cancellation and disposal, using no `TextIO`, terminal factory, or private extension candidate in the caller-facing API. |
| 2b | Route RPC prompt/queue/abort/state through that owner, then model/thinking and compaction controls in independently reviewable cuts. Preserve wire framing/correlation and explicitly report unsupported commands. | Same scenario across direct API and RPC yields equivalent accepted inputs, model/history state and terminal outcomes; queue modes affect real delivery; no transport-local shadow policy. |
| 2c | Add resume/fork/session lifecycle and an executable embedding example. Adapt existing terminal/print/JSON entry points incrementally; their UI remains mode-specific. Replace the pipy-only compatibility SDK deliberately rather than silently changing `run_native`. | CLI/headless session round-trip; extension lifecycle fires once; EOF/close during work cannot append to retired state; SDK docs and removed surfaces agree. No deprecation aliases. |
| 3a | Bounded, cancellable retries for eligible provider failures before accepted progress, with one attempt policy above transport adapters. Keep existing Codex transport recovery under an explicit total-attempt bound. | Injected clock/failure tests cover cancellation during backoff, permanent errors, exhaustion, event order, queued input, and no replay after text/tool progress or side effects. |
| 3b | Apply the retry owner to summary requests and wire real RPC retry settings/abort. | Failed/cancelled summaries publish nothing, successful retry publishes once, all modes observe the same retry lifecycle, and true-idle waits for retry settlement. |
| 4 | Cancellable process execution for direct RPC bash, followed by correlated incremental output. Reuse low-level lifetime/cancellation support without weakening the direct-command sandbox or model-tool trust policy. | Abort and timeout distinguish outcomes; spawned children terminate; one terminal response; no updates after completion; EOF disposes workers and stdout remains JSONL. |
| 5 | Extension and provider-history conformance. Ship one small documented Python extension example covering a tool, command and event; test load/reload/failure/close. Audit rich-content replay with the primary providers. | Example runs headlessly and in the terminal; rejected reload preserves the prior generation; same-provider resume and provider switch tests demonstrate any history defect before schema work is selected. |

Slice 1b explicitly owns changing today's whole-user-group compaction contract,
including the positive dropped-group invariant, product compaction value and
persistence/reconstruction consumers. This is not an implicit relaxation of 1a.
Its implementation spec must define and test the new safe-cut representation.
Slice 2b deliberately extends the minimal 2a facade with queued-input admission
and settlement semantics before migrating RPC; 2a does not claim full queue
control is already exposed.

For 3a, the coding session owns configured retry limits and enable/abort policy;
the canonical agent/provider-turn execution boundary reissues the unchanged
provider request within the same accepted iteration. It must not restart the
whole accepted run or re-execute earlier tools. Retryable attempts must be
resolved before final failed-turn settlement; event detail and the replacement
of the current zero-retry invariant belong to 3a's reviewed implementation spec.
The no-progress guard applies to the request attempt being retried, while all
prior accepted history and tool effects remain intact.

Slice 0 precedes selection. For terminal-first use, 1a/1b take priority; 2a is
the main architectural investment. Retry work can precede 2b/2c if daily-use
evidence identifies provider failures as the dominant problem, but later RPC
controls must use the shared owner. Slice 4 does not need to wait for all SDK
lifecycle controls. This is a dependency-guided queue, not a requirement to
finish all architecture work before delivering a user-visible improvement.

Semantic compaction must prepare from a stable session snapshot and publish
only if that snapshot is still current. A failed, cancelled, or stale summary
cannot discard history. The implementing spec must enumerate guarded readers
and writers, persistence failure behavior, and publication/teardown ordering
using the existing product-session contract. The same guarded-reader/writer inventory is
required before slice 2b migrates RPC state: preserve admission/reservation,
`agent_end`/`agent_settled` atomicity, `isStreaming` boundaries, and abort/close
ordering. The migration must not leave the old and new owners concurrently
writing the same state. Summary content belongs only in
private product history/provider requests, never in the metadata archive.
Reuse the branch-summary capability, but route execution through the canonical
provider-turn cancellation/event boundary rather than copying its direct call.

## Deliberate deferrals

- Parallel tool execution: useful latency work after measurement. Begin with
  explicit concurrency-safe read tools; keep mutating and unknown extension
  tools sequential until authority, cancellation and result ordering are
  specified. Pi's parallel default is not a prerequisite for daily usability.
- Strict schemas/constrained sampling: add when failures on a primary provider
  justify it, with capability-gated `require` semantics. Grammar tools can wait.
- More provider catalogs/OAuth families, image generation, remote session
  backends, durable operation lanes, distributed agents, server mode, and exact
  Pi component/theme parity are outside the minimum target.
- No new file-count reduction program, full test-suite rewrite, or release
  packaging program. Improve a boundary when the selected behavior needs it.

## Validation and review stop conditions

Daily-use acceptance means completing a real repository task, running its
checks, interrupting and steering work, resuming after exit, and continuing
after compaction without reconstructing the task manually. A provider failure
must leave a usable session. An extension should be addable through documented
APIs without patching the loop or terminal. Automated assertions cover lifecycle
and request content; live dogfooding evaluates semantic summary quality and UX.
Passing synthetic tests alone is not a daily-usability claim.

For each implementation slice run focused behavior tests, `just check`, relevant
PTY checks, documentation updates, and release notes when behavior changes.
Run `prek` if a pre-commit configuration is introduced. Isolate provider/session
and theme state in temporary directories; do not change the user's `pi` theme.
Measure startup, cancellation latency, and request size on the same scenario
before setting performance targets.

Use direct read-only Claude Code **Opus 5** through `claude-review-loop` for
plans/specs and code. A clean first docs review is sufficient. Respect the
repository defaults of at most two docs rounds and three code rounds; stop
earlier at CLEAN or when remaining feedback is advisory and further changes
would not materially improve the work. Unresolved correctness findings remain
blocking. Shared-mutable-state correctness defects retain the repository's
special handling; model unavailability is not a clean review and must not cause
a silent model substitution.

This change is planning and correction of current-state documentation only;
no runtime behavior changes and no release note applies.


## Assessment validation and review disposition

The source baseline passed `just check`: lint, formatting, Mypy, and 5,384
passed / 2 skipped tests. Documentation builds and whitespace checks passed.
Live provider dogfooding was not performed; slice 0 remains responsible for it.
Both local theme stores remained `pi`.

Claude Code `claude-opus-5` completed two substantive read-only review rounds.
The first returned one Warning and three Suggestions; the second returned only
four Suggestions, with no Warning or Critical finding. The earlier sandbox-
invalid attempt produced no accepted verdict. The final advisory points are
recorded above as explicit 1b/2b/3a contract ownership and restored RPC target
event names. Those final documentation clarifications were not re-reviewed.
Review stops at the repository's two-round docs cap and diminishing returns;
this is an advisory disposition, not a CLEAN gate for implementation. Future
slice specs and code require their own reviews.
